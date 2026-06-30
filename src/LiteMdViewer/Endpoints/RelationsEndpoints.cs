using LiteMdViewer.Data;
using LiteMdViewer.Models;
using LiteMdViewer.Services;

namespace LiteMdViewer.Endpoints;

public static class RelationsEndpoints
{
    public static void MapRelations(this WebApplication app)
    {
        var g = app.MapGroup("/api/files");

        // The graph this document belongs to (its members + reference/sibling edges) plus
        // the graph's companions. Companions and edges are graph-level, so any member sees
        // the same set. A document with no graph yet returns a singleton view of itself.
        g.MapGet("/{id:int}/graph", async (int id, AppDbContext db, GraphService graph) =>
        {
            if (await db.Files.FindAsync(id) is null) return Results.NotFound();
            return Results.Ok(await graph.BuildGraphDtoAsync(id));
        });

        // Add a link from this file to another (parent|child|sibling|companion).
        g.MapPost("/{id:int}/relations", async (int id, AddRelationRequest req, AppDbContext db, GraphService graph) =>
        {
            if (req.OtherId == id) return Results.BadRequest(new { error = "A document cannot link to itself." });
            if (await db.Files.FindAsync(id) is null || await db.Files.FindAsync(req.OtherId) is null)
                return Results.NotFound(new { error = "Document not found." });

            var (fromId, toId, kind) = Normalize(id, req.OtherId, req.Kind);
            if (kind is null) return Results.BadRequest(new { error = "Unknown relation kind." });

            if (kind == Companion)
            {
                // Companion attaches the other document to *this* document's graph (one-directional).
                var (ok, error) = await graph.AddCompanionAsync(id, req.OtherId);
                return ok ? Results.NoContent() : Results.BadRequest(new { error });
            }

            await graph.AddEdgeAsync(fromId, toId, kind);
            return Results.NoContent();
        });

        // Remove a specific link between this file and another.
        g.MapDelete("/{id:int}/relations", async (int id, int otherId, string kind, GraphService graph) =>
        {
            var (fromId, toId, k) = Normalize(id, otherId, kind);
            if (k is null) return Results.BadRequest(new { error = "Unknown relation kind." });

            if (k == Companion) await graph.RemoveCompanionAsync(id, otherId);
            else await graph.RemoveEdgeAsync(fromId, toId, k);
            return Results.NoContent();
        });

        // Detach this document from its graph entirely (deletes its edges; keeps the doc).
        g.MapDelete("/{id:int}/graph", async (int id, GraphService graph) =>
        {
            await graph.RemoveMemberAsync(id);
            return Results.NoContent();
        });
    }

    private const string Companion = "companion";

    // Map a UI relation kind (relative to `id`) to a stored edge (fromId, toId, kind).
    // "companion" is not a graph edge — it is signalled with the Companion sentinel and
    // handled separately. Returns kind=null for an unknown UI kind.
    private static (int fromId, int toId, string? kind) Normalize(int id, int otherId, string uiKind)
    {
        switch (uiKind)
        {
            case "parent":    return (otherId, id, GraphEdgeKind.Reference);  // other -> this
            case "child":     return (id, otherId, GraphEdgeKind.Reference);  // this -> other
            case "reference": return (id, otherId, GraphEdgeKind.Reference);
            case "sibling":   { var (a, b) = Canon(id, otherId); return (a, b, GraphEdgeKind.Sibling); }
            case "companion": return (id, otherId, Companion);
            default:          return (0, 0, null);
        }
    }

    private static (int, int) Canon(int a, int b) => a <= b ? (a, b) : (b, a);
}
