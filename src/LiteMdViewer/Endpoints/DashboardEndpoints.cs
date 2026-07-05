using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;

namespace LiteMdViewer.Endpoints;

// Dashboard sticky notes: free-positioned markdown cards on the dashboard board.
// Positions (X/Y) and stacking order (Z) are persisted so a dragged note keeps its
// place across refreshes. Mirrors the FilesEndpoints PATCH-partial style.
public static class DashboardEndpoints
{
    public static void MapDashboard(this WebApplication app)
    {
        var g = app.MapGroup("/api/dashboard");

        g.MapGet("/notes", async (AppDbContext db) =>
        {
            var notes = await db.DashboardNotes
                .OrderBy(n => n.Z).ThenBy(n => n.Id)
                .Select(n => ToDto(n))
                .ToListAsync();
            return Results.Ok(notes);
        });

        g.MapPost("/notes", async (CreateNoteRequest req, AppDbContext db) =>
        {
            var kind = req.Kind == DashboardNoteKind.Flip ? DashboardNoteKind.Flip : DashboardNoteKind.Note;
            var maxZ = await db.DashboardNotes.AnyAsync() ? await db.DashboardNotes.MaxAsync(n => n.Z) : 0;
            var now = DateTime.UtcNow;
            var note = new DashboardNote
            {
                Kind = kind,
                FrontText = req.FrontText ?? "",
                BackText = req.BackText ?? "",
                X = req.X,
                Y = req.Y,
                Z = maxZ + 1,
                CreatedUtc = now,
                UpdatedUtc = now,
            };
            db.DashboardNotes.Add(note);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(note));
        });

        // Partial update: only the supplied fields change (position, text, or stacking).
        g.MapPatch("/notes/{id:int}", async (int id, PatchNoteRequest req, AppDbContext db) =>
        {
            var n = await db.DashboardNotes.FindAsync(id);
            if (n is null) return Results.NotFound();

            if (req.FrontText is not null) n.FrontText = req.FrontText;
            if (req.BackText is not null) n.BackText = req.BackText;
            if (req.X.HasValue) n.X = req.X.Value;
            if (req.Y.HasValue) n.Y = req.Y.Value;
            if (req.Z.HasValue) n.Z = req.Z.Value;
            n.UpdatedUtc = DateTime.UtcNow;

            await db.SaveChangesAsync();
            return Results.Ok(ToDto(n));
        });

        g.MapDelete("/notes/{id:int}", async (int id, AppDbContext db) =>
        {
            var n = await db.DashboardNotes.FindAsync(id);
            if (n is null) return Results.NotFound();
            db.DashboardNotes.Remove(n);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });
    }

    private static NoteDto ToDto(DashboardNote n) =>
        new(n.Id, n.Kind, n.FrontText, n.BackText, n.X, n.Y, n.Z);
}
