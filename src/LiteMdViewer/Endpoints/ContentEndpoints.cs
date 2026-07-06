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
        // if another process holds the file open. When the file is gone from disk we fall
        // back to the DB mirror kept by FileSyncService (read-only) so the content is not lost.
        g.MapGet("/{id:int}/content", async (int id, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();

            if (!File.Exists(f.FullPath))
            {
                var mirror = await db.FileContents.FindAsync(id);
                if (mirror is null)
                    return Results.NotFound(new { error = "File is missing on disk." });
                // Serve the last-synced copy; the viewer shows the warning strip and locks editing.
                return Results.Ok(new ContentDto(f.Id, f.Title, f.FullPath, mirror.Content,
                                                 OnDisk: false, ReadOnly: true));
            }

            string text;
            await using (var fs = new FileStream(f.FullPath, FileMode.Open, FileAccess.Read,
                                                 FileShare.ReadWrite | FileShare.Delete))
            using (var sr = new StreamReader(fs))
                text = await sr.ReadToEndAsync();

            f.LastOpenedUtc = DateTime.UtcNow;
            await db.SaveChangesAsync();
            return Results.Ok(new ContentDto(f.Id, f.Title, f.FullPath, text,
                                             OnDisk: true, ReadOnly: false));
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

        // Recreate a file that was deleted from disk, writing the DB mirror back to its
        // original path (creating the parent folder if it too is gone). After this the file
        // exists again and is editable, so the viewer drops the warning strip.
        g.MapPost("/{id:int}/recreate", async (int id, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            if (File.Exists(f.FullPath))
                return Results.Conflict(new { error = "File already exists on disk." });

            var mirror = await db.FileContents.FindAsync(id);
            if (mirror is null)
                return Results.Conflict(new { error = "No stored content to recreate from." });

            try
            {
                var dir = Path.GetDirectoryName(f.FullPath);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

                await using var fs = new FileStream(f.FullPath, FileMode.Create, FileAccess.Write, FileShare.Read);
                await using var sw = new StreamWriter(fs);
                await sw.WriteAsync(mirror.Content);
            }
            catch (Exception ex) { return Results.Problem("Could not recreate file: " + ex.Message); }

            var mtime = File.GetLastWriteTimeUtc(f.FullPath);
            f.LastWriteUtc = mtime;
            // Keep the mirror's mtime marker in step so the sync sweep treats this as already-synced.
            mirror.SourceWriteUtc = mtime;
            mirror.SyncedUtc = DateTime.UtcNow;
            await db.SaveChangesAsync();

            return Results.Ok(new FileDto(f.Id, f.Title, f.FullPath, f.FolderId, f.SortOrder, false));
        });
    }
}
