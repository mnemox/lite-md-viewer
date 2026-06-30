using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;

namespace LiteMdViewer.Services;

public static class GraphHelper
{
    /// <summary>
    /// Computes the connected component (over reference + sibling edges, treated as
    /// undirected) that the given file belongs to. Returns the set of file ids in the
    /// component plus the loaded relations and a file lookup, so callers can build the
    /// graph DTO or gather files to export without re-querying.
    /// </summary>
    public static async Task<(HashSet<int> Component, List<Relation> Relations, Dictionary<int, ManagedFile> ById)>
        ComponentAsync(AppDbContext db, int id)
    {
        var rels = await db.Relations.ToListAsync();
        var byId = (await db.Files.ToListAsync()).ToDictionary(f => f.Id);

        var adj = new Dictionary<int, List<int>>();
        void Link(int a, int b) => (adj.TryGetValue(a, out var l) ? l : adj[a] = new()).Add(b);
        foreach (var e in rels)
        {
            if ((e.Kind == RelationKind.Reference || e.Kind == RelationKind.Sibling)
                && byId.ContainsKey(e.FromId) && byId.ContainsKey(e.ToId))
            {
                Link(e.FromId, e.ToId);
                Link(e.ToId, e.FromId);
            }
        }

        var comp = new HashSet<int> { id };
        var queue = new Queue<int>();
        queue.Enqueue(id);
        while (queue.Count > 0)
        {
            if (!adj.TryGetValue(queue.Dequeue(), out var nbrs)) continue;
            foreach (var n in nbrs) if (comp.Add(n)) queue.Enqueue(n);
        }
        return (comp, rels, byId);
    }
}
