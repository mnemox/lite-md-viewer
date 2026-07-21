using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;

namespace LiteMdViewer.Endpoints;

// Per-document markdown notes. Each note belongs to a ManagedFile and is shown in the file
// page's top-right notes panel. A document's notes also surface on the dashboard as a single
// titled cluster whose board position lives in DocumentNoteGroup (created with the first note,
// removed with the last). Mirrors the PATCH-partial style of DashboardEndpoints.
public static class DocumentNotesEndpoints
{
    private const double GroupBaseX = 28, GroupBaseY = 24, GroupStep = 26;

    public static void MapDocumentNotes(this WebApplication app)
    {
        // ----- per-file notes (drive the file page panel) -----
        var f = app.MapGroup("/api/files/{fileId:int}/notes");

        f.MapGet("", async (int fileId, AppDbContext db) =>
        {
            var notes = await db.DocumentNotes
                .Where(n => n.FileId == fileId)
                .OrderBy(n => n.SortOrder).ThenBy(n => n.Id)
                .Select(n => ToDto(n))
                .ToListAsync();
            return Results.Ok(notes);
        });

        f.MapPost("", async (int fileId, CreateDocNoteRequest req, AppDbContext db) =>
        {
            if (!await db.Files.AnyAsync(x => x.Id == fileId)) return Results.NotFound();

            // Lazily place the document's cluster card the first time it gets a note, cascading
            // so multiple documents' groups don't land exactly on top of each other.
            if (!await db.DocumentNoteGroups.AnyAsync(x => x.FileId == fileId))
            {
                var groupCount = await db.DocumentNoteGroups.CountAsync();
                var maxZ = await db.DocumentNoteGroups.AnyAsync()
                    ? await db.DocumentNoteGroups.MaxAsync(x => x.Z) : 0;
                var step = (groupCount % 6) * GroupStep;
                db.DocumentNoteGroups.Add(new DocumentNoteGroup
                {
                    FileId = fileId,
                    X = GroupBaseX + step,
                    Y = GroupBaseY + step,
                    Z = maxZ + 1,
                });
            }

            var maxSort = await db.DocumentNotes.Where(n => n.FileId == fileId).AnyAsync()
                ? await db.DocumentNotes.Where(n => n.FileId == fileId).MaxAsync(n => n.SortOrder) : 0;
            var now = DateTime.UtcNow;
            var note = new DocumentNote
            {
                FileId = fileId,
                Text = req.Text ?? "",
                SortOrder = maxSort + 1,
                CreatedUtc = now,
                UpdatedUtc = now,
            };
            db.DocumentNotes.Add(note);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(note));
        });

        // Partial update: only supplied fields change.
        f.MapPatch("/{noteId:int}", async (int fileId, int noteId, PatchDocNoteRequest req, AppDbContext db) =>
        {
            var n = await db.DocumentNotes.FirstOrDefaultAsync(x => x.Id == noteId && x.FileId == fileId);
            if (n is null) return Results.NotFound();

            if (req.Text is not null) n.Text = req.Text;
            if (req.SortOrder.HasValue) n.SortOrder = req.SortOrder.Value;
            n.UpdatedUtc = DateTime.UtcNow;

            await db.SaveChangesAsync();
            return Results.Ok(ToDto(n));
        });

        f.MapDelete("/{noteId:int}", async (int fileId, int noteId, AppDbContext db) =>
        {
            var n = await db.DocumentNotes.FirstOrDefaultAsync(x => x.Id == noteId && x.FileId == fileId);
            if (n is null) return Results.NotFound();
            db.DocumentNotes.Remove(n);

            // Drop the empty cluster card once its last note is gone, so the dashboard doesn't
            // keep showing an empty group.
            if (!await db.DocumentNotes.AnyAsync(x => x.FileId == fileId && x.Id != noteId))
            {
                var group = await db.DocumentNoteGroups.FirstOrDefaultAsync(x => x.FileId == fileId);
                if (group is not null) db.DocumentNoteGroups.Remove(group);
            }

            await db.SaveChangesAsync();
            return Results.NoContent();
        });

        // ----- dashboard aggregation (grouped clusters) -----
        var d = app.MapGroup("/api/dashboard/document-notes");

        d.MapGet("", async (AppDbContext db) =>
        {
            var groups = await db.DocumentNoteGroups.OrderBy(g => g.Z).ThenBy(g => g.Id).ToListAsync();
            if (groups.Count == 0) return Results.Ok(Array.Empty<DocNoteGroupDto>());

            var fileIds = groups.Select(g => g.FileId).ToList();
            var files = await db.Files.Where(x => fileIds.Contains(x.Id))
                .ToDictionaryAsync(x => x.Id, x => x);
            var notes = await db.DocumentNotes.Where(n => fileIds.Contains(n.FileId))
                .OrderBy(n => n.SortOrder).ThenBy(n => n.Id).ToListAsync();

            var result = new List<DocNoteGroupDto>();
            foreach (var g in groups)
            {
                if (!files.TryGetValue(g.FileId, out var file)) continue;   // orphan guard
                var groupNotes = notes.Where(n => n.FileId == g.FileId).Select(ToDto).ToList();
                if (groupNotes.Count == 0) continue;
                result.Add(new DocNoteGroupDto(
                    g.FileId, file.Title, !File.Exists(file.FullPath), g.X, g.Y, g.Z, groupNotes));
            }
            return Results.Ok(result);
        });

        // Persist a cluster card's board position / stacking after a drag.
        d.MapPatch("/{fileId:int}", async (int fileId, PatchDocNoteGroupRequest req, AppDbContext db) =>
        {
            var g = await db.DocumentNoteGroups.FirstOrDefaultAsync(x => x.FileId == fileId);
            if (g is null) return Results.NotFound();

            if (req.X.HasValue) g.X = req.X.Value;
            if (req.Y.HasValue) g.Y = req.Y.Value;
            if (req.Z.HasValue) g.Z = req.Z.Value;

            await db.SaveChangesAsync();
            return Results.NoContent();
        });
    }

    private static DocNoteDto ToDto(DocumentNote n) => new(n.Id, n.FileId, n.Text, n.SortOrder);
}
