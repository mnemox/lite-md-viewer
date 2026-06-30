using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;

namespace LiteMdViewer.Endpoints;

public static class RelationsEndpoints
{
    public static void MapRelations(this WebApplication app)
    {
        var g = app.MapGroup("/api/files");

        // The whole connected component (reference + sibling edges) the file belongs to,
        // plus this file's companions. One call powers both the initial open and
        // re-navigation to another document in the graph.
        g.MapGet("/{id:int}/graph", async (int id, AppDbContext db) =>
        {
            if (await db.Files.FindAsync(id) is null) return Results.NotFound();

            var rels = await db.Relations.ToListAsync();
            var byId = (await db.Files.ToListAsync()).ToDictionary(f => f.Id);

            // Graph edges are reference + sibling; treat both as undirected for connectivity.
            var graphEdges = rels
                .Where(r => (r.Kind == RelationKind.Reference || r.Kind == RelationKind.Sibling)
                            && byId.ContainsKey(r.FromId) && byId.ContainsKey(r.ToId))
                .ToList();

            var adj = new Dictionary<int, List<int>>();
            void Link(int a, int b) => (adj.TryGetValue(a, out var l) ? l : adj[a] = new()).Add(b);
            foreach (var e in graphEdges) { Link(e.FromId, e.ToId); Link(e.ToId, e.FromId); }

            // BFS the connected component starting from the active file.
            var comp = new HashSet<int> { id };
            var queue = new Queue<int>();
            queue.Enqueue(id);
            while (queue.Count > 0)
            {
                if (!adj.TryGetValue(queue.Dequeue(), out var nbrs)) continue;
                foreach (var n in nbrs) if (comp.Add(n)) queue.Enqueue(n);
            }

            var nodes = comp.Where(byId.ContainsKey).Select(i => Node(byId[i])).ToList();
            var edges = graphEdges
                .Where(e => comp.Contains(e.FromId) && comp.Contains(e.ToId))
                .Select(e => new RelationEdgeDto(e.FromId, e.ToId, e.Kind))
                .ToList();

            // Companions are undirected; list the other end of each companion row touching id.
            var companions = rels
                .Where(r => r.Kind == RelationKind.Companion && (r.FromId == id || r.ToId == id))
                .Select(r => r.FromId == id ? r.ToId : r.FromId)
                .Where(byId.ContainsKey)
                .Distinct()
                .Select(i => Node(byId[i]))
                .ToList();

            return Results.Ok(new GraphDto(id, nodes, edges, companions));
        });

        // Add a link from this file to another (parent|child|sibling|companion).
        g.MapPost("/{id:int}/relations", async (int id, AddRelationRequest req, AppDbContext db) =>
        {
            if (req.OtherId == id) return Results.BadRequest(new { error = "A document cannot link to itself." });
            if (await db.Files.FindAsync(id) is null || await db.Files.FindAsync(req.OtherId) is null)
                return Results.NotFound(new { error = "Document not found." });

            var (fromId, toId, kind) = Normalize(id, req.OtherId, req.Kind);
            if (kind is null) return Results.BadRequest(new { error = "Unknown relation kind." });

            var exists = await db.Relations.AnyAsync(r => r.FromId == fromId && r.ToId == toId && r.Kind == kind);
            if (!exists)
            {
                db.Relations.Add(new Relation { FromId = fromId, ToId = toId, Kind = kind });
                await db.SaveChangesAsync();
            }
            return Results.NoContent();
        });

        // Remove a specific link between this file and another.
        g.MapDelete("/{id:int}/relations", async (int id, int otherId, string kind, AppDbContext db) =>
        {
            var (fromId, toId, k) = Normalize(id, otherId, kind);
            if (k is null) return Results.BadRequest(new { error = "Unknown relation kind." });

            var rows = await db.Relations
                .Where(r => r.FromId == fromId && r.ToId == toId && r.Kind == k)
                .ToListAsync();
            if (rows.Count > 0) { db.Relations.RemoveRange(rows); await db.SaveChangesAsync(); }
            return Results.NoContent();
        });
    }

    // Map a UI relation kind (relative to `id`) to a stored row (fromId, toId, storedKind).
    // Returns kind=null for an unknown UI kind.
    private static (int fromId, int toId, string? kind) Normalize(int id, int otherId, string uiKind)
    {
        switch (uiKind)
        {
            case "parent":    return (otherId, id, RelationKind.Reference);  // other -> this
            case "child":     return (id, otherId, RelationKind.Reference);  // this -> other
            case "reference": return (id, otherId, RelationKind.Reference);
            case "sibling":   { var (a, b) = Canon(id, otherId); return (a, b, RelationKind.Sibling); }
            case "companion": { var (a, b) = Canon(id, otherId); return (a, b, RelationKind.Companion); }
            default:          return (0, 0, null);
        }
    }

    private static (int, int) Canon(int a, int b) => a <= b ? (a, b) : (b, a);

    private static RelationNodeDto Node(ManagedFile f) => new(f.Id, f.Title, !File.Exists(f.FullPath));
}
