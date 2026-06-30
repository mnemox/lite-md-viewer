using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;

namespace LiteMdViewer.Services;

/// <summary>
/// Owns the explicit-graph model: each document belongs to at most one graph (via
/// GraphMember), and a graph owns its reference/sibling edges, companion documents, and
/// export attachments. Linking two documents from different graphs merges the graphs;
/// removing a single edge never splits a graph; removing a member can garbage-collect an
/// emptied graph (rows + zip files).
/// </summary>
public sealed class GraphService
{
    private readonly AppDbContext _db;
    private readonly IWebHostEnvironment _env;

    public GraphService(AppDbContext db, IWebHostEnvironment env) { _db = db; _env = env; }

    public Task<int?> GetGraphIdAsync(int fileId) =>
        _db.GraphMembers.Where(m => m.FileId == fileId).Select(m => (int?)m.GraphId).FirstOrDefaultAsync();

    public async Task<int> GetOrCreateGraphAsync(int fileId)
    {
        var gid = await GetGraphIdAsync(fileId);
        if (gid is not null) return gid.Value;
        var g = new Graph { CreatedUtc = DateTime.UtcNow };
        _db.Graphs.Add(g);
        await _db.SaveChangesAsync();                 // assigns g.Id
        _db.GraphMembers.Add(new GraphMember { GraphId = g.Id, FileId = fileId });
        await _db.SaveChangesAsync();
        return g.Id;
    }

    // Add a reference/sibling edge (already canonicalized). Both documents become members
    // of one graph, merging their graphs if they differ. Returns the resulting graph id.
    public async Task<int> AddEdgeAsync(int fromId, int toId, string kind)
    {
        var gF = await GetOrCreateGraphAsync(fromId);
        var gT = await GetOrCreateGraphAsync(toId);
        int gid = gF;
        if (gF != gT)
        {
            var (target, source) = await BiggerFirstAsync(gF, gT);
            gid = await MergeAsync(target, source);
        }
        if (!await _db.GraphEdges.AnyAsync(e => e.FromId == fromId && e.ToId == toId && e.Kind == kind))
        {
            _db.GraphEdges.Add(new GraphEdge { GraphId = gid, FromId = fromId, ToId = toId, Kind = kind });
            await _db.SaveChangesAsync();
        }
        await ReconcileCompanionsVsMembersAsync(gid);
        return gid;
    }

    private async Task<(int target, int source)> BiggerFirstAsync(int a, int b)
    {
        var ca = await _db.GraphMembers.CountAsync(m => m.GraphId == a);
        var cb = await _db.GraphMembers.CountAsync(m => m.GraphId == b);
        return ca >= cb ? (a, b) : (b, a);
    }

    // Move source's members/edges/attachments/companions into target, then delete source.
    public async Task<int> MergeAsync(int target, int source)
    {
        if (target == source) return target;
        await _db.GraphMembers.Where(m => m.GraphId == source).ExecuteUpdateAsync(s => s.SetProperty(m => m.GraphId, target));
        await _db.GraphEdges.Where(e => e.GraphId == source).ExecuteUpdateAsync(s => s.SetProperty(e => e.GraphId, target));
        await _db.Attachments.Where(a => a.GraphId == source).ExecuteUpdateAsync(s => s.SetProperty(a => a.GraphId, target));

        // Companions: reassign source rows, dropping any that already exist on the target.
        var existing = (await _db.GraphCompanions.Where(c => c.GraphId == target).Select(c => c.FileId).ToListAsync()).ToHashSet();
        foreach (var c in await _db.GraphCompanions.Where(c => c.GraphId == source).ToListAsync())
        {
            if (existing.Contains(c.FileId)) _db.GraphCompanions.Remove(c);
            else { c.GraphId = target; existing.Add(c.FileId); }
        }
        if (await _db.Graphs.FindAsync(source) is { } src) _db.Graphs.Remove(src);
        await _db.SaveChangesAsync();

        await ReconcileCompanionsVsMembersAsync(target);
        return target;
    }

    // A companion that is now also a member of the same graph is redundant — drop it.
    public async Task ReconcileCompanionsVsMembersAsync(int graphId)
    {
        var members = await _db.GraphMembers.Where(m => m.GraphId == graphId).Select(m => m.FileId).ToListAsync();
        await _db.GraphCompanions.Where(c => c.GraphId == graphId && members.Contains(c.FileId)).ExecuteDeleteAsync();
    }

    // Remove a single edge. Members stay; the graph is never split.
    public Task RemoveEdgeAsync(int fromId, int toId, string kind) =>
        _db.GraphEdges.Where(e => e.FromId == fromId && e.ToId == toId && e.Kind == kind).ExecuteDeleteAsync();

    public async Task<(bool ok, string? error)> AddCompanionAsync(int ownerFileId, int companionFileId)
    {
        if (ownerFileId == companionFileId) return (false, "A document cannot be its own companion.");
        var gid = await GetOrCreateGraphAsync(ownerFileId);
        if (await _db.GraphMembers.AnyAsync(m => m.GraphId == gid && m.FileId == companionFileId))
            return (false, "That document is already in the graph.");
        if (await _db.GraphCompanions.AnyAsync(c => c.GraphId == gid && c.FileId == companionFileId))
            return (true, null); // idempotent
        _db.GraphCompanions.Add(new GraphCompanion { GraphId = gid, FileId = companionFileId });
        await _db.SaveChangesAsync();
        return (true, null);
    }

    public async Task RemoveCompanionAsync(int ownerFileId, int companionFileId)
    {
        var gid = await GetGraphIdAsync(ownerFileId);
        if (gid is null) return;
        await _db.GraphCompanions.Where(c => c.GraphId == gid.Value && c.FileId == companionFileId).ExecuteDeleteAsync();
    }

    // "Remove from graph": detach a member and its edges; GC the graph if it is now empty.
    public async Task RemoveMemberAsync(int fileId)
    {
        var gid = await GetGraphIdAsync(fileId);
        if (gid is null) return;
        await _db.GraphEdges.Where(e => e.GraphId == gid.Value && (e.FromId == fileId || e.ToId == fileId)).ExecuteDeleteAsync();
        await _db.GraphMembers.Where(m => m.FileId == fileId).ExecuteDeleteAsync();
        if (!await _db.GraphMembers.AnyAsync(m => m.GraphId == gid.Value))
            await GCGraphAsync(gid.Value);
    }

    // Delete an emptied graph: its attachment zips + all of its rows.
    public async Task GCGraphAsync(int graphId)
    {
        foreach (var a in await _db.Attachments.Where(a => a.GraphId == graphId).ToListAsync())
        {
            try
            {
                var p = Path.Combine(_env.ContentRootPath, "attachments", a.StoredName);
                if (File.Exists(p)) File.Delete(p);
            }
            catch { /* best effort */ }
        }
        await _db.Attachments.Where(a => a.GraphId == graphId).ExecuteDeleteAsync();
        await _db.GraphCompanions.Where(c => c.GraphId == graphId).ExecuteDeleteAsync();
        await _db.Graphs.Where(g => g.Id == graphId).ExecuteDeleteAsync();
    }

    // For file deletion: detach the member (GC if last) and drop its companion role elsewhere.
    public async Task RemoveFileEverywhereAsync(int fileId)
    {
        await RemoveMemberAsync(fileId);
        await _db.GraphCompanions.Where(c => c.FileId == fileId).ExecuteDeleteAsync();
    }

    public async Task<List<ManagedFile>> GetGraphMemberFilesAsync(int graphId)
    {
        var ids = await _db.GraphMembers.Where(m => m.GraphId == graphId).Select(m => m.FileId).ToListAsync();
        return await _db.Files.Where(f => ids.Contains(f.Id)).ToListAsync();
    }

    // The graph the document belongs to (nodes/edges/companions), or a singleton view if none.
    public async Task<GraphDto> BuildGraphDtoAsync(int activeId)
    {
        var gid = await GetGraphIdAsync(activeId);
        if (gid is null)
        {
            var solo = await _db.Files.FindAsync(activeId);
            var nodes = solo is null ? new List<RelationNodeDto>() : new List<RelationNodeDto> { Node(solo) };
            return new GraphDto(activeId, nodes, Array.Empty<RelationEdgeDto>(), Array.Empty<RelationNodeDto>());
        }

        var byId = (await _db.Files.ToListAsync()).ToDictionary(f => f.Id);
        var memberIds = await _db.GraphMembers.Where(m => m.GraphId == gid.Value).Select(m => m.FileId).ToListAsync();
        var companionIds = await _db.GraphCompanions.Where(c => c.GraphId == gid.Value).Select(c => c.FileId).ToListAsync();
        var edges = await _db.GraphEdges.Where(e => e.GraphId == gid.Value)
            .Select(e => new RelationEdgeDto(e.FromId, e.ToId, e.Kind)).ToListAsync();

        return new GraphDto(
            activeId,
            memberIds.Where(byId.ContainsKey).Select(i => Node(byId[i])).ToList(),
            edges,
            companionIds.Where(byId.ContainsKey).Select(i => Node(byId[i])).ToList());
    }

    private static RelationNodeDto Node(ManagedFile f) => new(f.Id, f.Title, !File.Exists(f.FullPath));
}
