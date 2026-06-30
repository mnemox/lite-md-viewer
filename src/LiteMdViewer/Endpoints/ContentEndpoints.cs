using LiteMdViewer.Data;
using LiteMdViewer.Models;

namespace LiteMdViewer.Endpoints;

public static class ContentEndpoints
{
    public static void MapContent(this WebApplication app)
    {
        var g = app.MapGroup("/api/files");

        // File metadata for the details modal: on-disk created/modified timestamps
        // and the full path. Dates are null when the file is gone from disk.
        g.MapGet("/{id:int}/details", async (int id, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();

            var exists = File.Exists(f.FullPath);
            DateTime? created = null, modified = null;
            if (exists)
            {
                created = File.GetCreationTimeUtc(f.FullPath);
                modified = File.GetLastWriteTimeUtc(f.FullPath);
            }
            return Results.Ok(new FileDetailsDto(f.Id, f.Title, f.FullPath, created, modified, exists));
        });

        // Raw markdown text for the viewer/editor. Tolerant read so it works even
        // if another process holds the file open.
        g.MapGet("/{id:int}/content", async (int id, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            if (!File.Exists(f.FullPath))
                return Results.NotFound(new { error = "File is missing on disk." });

            string text;
            await using (var fs = new FileStream(f.FullPath, FileMode.Open, FileAccess.Read,
                                                 FileShare.ReadWrite | FileShare.Delete))
            using (var sr = new StreamReader(fs))
                text = await sr.ReadToEndAsync();

            f.LastOpenedUtc = DateTime.UtcNow;
            await db.SaveChangesAsync();
            return Results.Ok(new ContentDto(f.Id, f.Title, f.FullPath, text));
        });

        // Save edited content via an in-place truncate-write (FileMode.Create).
        g.MapPut("/{id:int}/content", async (int id, SaveContentRequest req, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            if (!File.Exists(f.FullPath))
                return Results.Conflict(new { error = "File is missing on disk." });

            try
            {
                await using var fs = new FileStream(f.FullPath, FileMode.Create, FileAccess.Write, FileShare.Read);
                await using var sw = new StreamWriter(fs);
                await sw.WriteAsync(req.Text ?? "");
            }
            catch (Exception ex) { return Results.Problem("Could not save: " + ex.Message); }

            f.LastWriteUtc = File.GetLastWriteTimeUtc(f.FullPath);
            await db.SaveChangesAsync();
            return Results.Ok(new { ok = true });
        });
    }
}
