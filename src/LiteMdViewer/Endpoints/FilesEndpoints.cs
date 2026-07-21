using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;
using LiteMdViewer.Services;

namespace LiteMdViewer.Endpoints;

public static class FilesEndpoints
{
    public static void MapFiles(this WebApplication app)
    {
        var g = app.MapGroup("/api");

        // Whole drawer: folders + files (missing-on-disk computed on read).
        g.MapGet("/tree", async (AppDbContext db) =>
        {
            var folders = await db.Folders
                .OrderBy(f => f.SortOrder).ThenBy(f => f.Name)
                .Select(f => new FolderDto(f.Id, f.Name, f.ParentId, f.SortOrder))
                .ToListAsync();
            var files = (await db.Files
                .OrderBy(f => f.SortOrder).ThenBy(f => f.Title)
                .ToListAsync())
                .Select(ToDto)
                .ToList();
            return Results.Ok(new TreeDto(folders, files));
        });

        // Register a real on-disk path under management.
        g.MapPost("/files", async (AddFileRequest req, AppDbContext db) =>
        {
            if (string.IsNullOrWhiteSpace(req.Path))
                return Results.BadRequest(new { error = "A path is required." });

            string full;
            try { full = Path.GetFullPath(req.Path); }
            catch { return Results.BadRequest(new { error = "Invalid path." }); }

            if (!SupportedFiles.IsSupported(full))
                return Results.BadRequest(new { error = SupportedFiles.UnsupportedMessage });

            if (!File.Exists(full))
                return Results.BadRequest(new { error = "File does not exist." });

            // Case-insensitive dedupe on the canonical path.
            var all = await db.Files.ToListAsync();
            var dup = all.FirstOrDefault(f => string.Equals(f.FullPath, full, StringComparison.OrdinalIgnoreCase));
            if (dup != null)
                return Results.Conflict(new { error = "This file is already managed.", id = dup.Id });

            var maxSort = all.Count > 0 ? all.Max(f => f.SortOrder) : 0;
            var file = new ManagedFile
            {
                FullPath = full,
                Title = Path.GetFileNameWithoutExtension(full),
                FolderId = req.FolderId,
                SortOrder = maxSort + 1,
                LastWriteUtc = File.GetLastWriteTimeUtc(full),
                AddedUtc = DateTime.UtcNow,
            };

            db.Files.Add(file);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(file));
        });

        // Add every top-level .md/.markdown file from a disk folder (no recursion),
        // silently skipping paths that are already managed.
        g.MapPost("/files/folder", async (AddFolderFilesRequest req, AppDbContext db) =>
        {
            if (string.IsNullOrWhiteSpace(req.Path))
                return Results.BadRequest(new { error = "A folder path is required." });

            string full;
            try { full = Path.GetFullPath(req.Path); }
            catch { return Results.BadRequest(new { error = "Invalid path." }); }

            if (!Directory.Exists(full))
                return Results.BadRequest(new { error = "Folder does not exist." });

            List<string> mdFiles;
            try
            {
                mdFiles = Directory.EnumerateFiles(full)
                    .Where(SupportedFiles.IsSupported)
                    .OrderBy(f => f, StringComparer.OrdinalIgnoreCase)
                    .ToList();
            }
            catch (Exception ex) { return Results.Problem("Could not read folder: " + ex.Message); }

            var all = await db.Files.ToListAsync();
            var known = new HashSet<string>(all.Select(f => f.FullPath), StringComparer.OrdinalIgnoreCase);
            var maxSort = all.Count > 0 ? all.Max(f => f.SortOrder) : 0;

            var added = new List<ManagedFile>();
            var skipped = 0;
            foreach (var path in mdFiles)
            {
                var canon = Path.GetFullPath(path);
                if (!known.Add(canon)) { skipped++; continue; }
                var file = new ManagedFile
                {
                    FullPath = canon,
                    Title = Path.GetFileNameWithoutExtension(canon),
                    FolderId = req.FolderId,
                    SortOrder = ++maxSort,
                    LastWriteUtc = File.GetLastWriteTimeUtc(canon),
                    AddedUtc = DateTime.UtcNow,
                };
                db.Files.Add(file);
                added.Add(file);
            }
            if (added.Count > 0) await db.SaveChangesAsync();
            return Results.Ok(new AddFolderFilesResult(added.Count, skipped, added.Select(ToDto).ToList()));
        });

        // Create a brand-new .md file on disk in a chosen folder, then manage it.
        g.MapPost("/files/new", async (NewFileRequest req, AppDbContext db) =>
        {
            if (string.IsNullOrWhiteSpace(req.Dir) || string.IsNullOrWhiteSpace(req.Name))
                return Results.BadRequest(new { error = "A folder and a file name are required." });
            if (!Directory.Exists(req.Dir))
                return Results.BadRequest(new { error = "Target folder does not exist." });

            var name = req.Name.Trim();
            if (name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
                return Results.BadRequest(new { error = "Invalid file name." });

            if (Path.GetExtension(name).Length == 0) name += SupportedFiles.DefaultExt;
            if (!SupportedFiles.IsSupported(name))
                return Results.BadRequest(new { error = SupportedFiles.UnsupportedMessage });

            string full;
            try { full = Path.GetFullPath(Path.Combine(req.Dir, name)); }
            catch { return Results.BadRequest(new { error = "Invalid path." }); }

            var all = await db.Files.ToListAsync();
            var dup = all.FirstOrDefault(f => string.Equals(f.FullPath, full, StringComparison.OrdinalIgnoreCase));
            if (dup != null)
                return Results.Conflict(new { error = "This file is already managed.", id = dup.Id });
            if (File.Exists(full))
                return Results.Conflict(new { error = "A file with that name already exists in this folder." });

            var title = Path.GetFileNameWithoutExtension(full);
            try { await File.WriteAllTextAsync(full, $"# {title}\n"); }
            catch (Exception ex) { return Results.Problem("Could not create file: " + ex.Message); }

            var maxSort = all.Count > 0 ? all.Max(f => f.SortOrder) : 0;
            var file = new ManagedFile
            {
                FullPath = full,
                Title = title,
                FolderId = req.FolderId,
                SortOrder = maxSort + 1,
                LastWriteUtc = File.GetLastWriteTimeUtc(full),
                AddedUtc = DateTime.UtcNow,
            };

            db.Files.Add(file);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(file));
        });

        // Edit display title / move to folder / reorder. Never touches the disk filename.
        g.MapPatch("/files/{id:int}", async (int id, PatchFileRequest req, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();

            if (req.Title is not null) f.Title = req.Title.Trim();
            if (req.MoveToRoot) f.FolderId = null;
            else if (req.FolderId.HasValue) f.FolderId = req.FolderId;
            if (req.SortOrder.HasValue) f.SortOrder = req.SortOrder.Value;

            await db.SaveChangesAsync();
            return Results.Ok(ToDto(f));
        });

        // Move the real file to another physical folder and update its path in the DB.
        // Keeps the same DB Id (so relations/graph links stay intact) and DB folder assignment.
        g.MapPost("/files/{id:int}/move", async (int id, MoveFileRequest req, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();

            if (string.IsNullOrWhiteSpace(req.Dir))
                return Results.BadRequest(new { error = "A target folder is required." });
            if (!Directory.Exists(req.Dir))
                return Results.BadRequest(new { error = "Target folder does not exist." });

            var name = string.IsNullOrWhiteSpace(req.NewName)
                ? Path.GetFileName(f.FullPath)
                : req.NewName.Trim();

            if (!SupportedFiles.IsSupported(name))
                return Results.BadRequest(new { error = SupportedFiles.UnsupportedMessage });

            string target;
            try { target = Path.GetFullPath(Path.Combine(req.Dir, name)); }
            catch { return Results.BadRequest(new { error = "Invalid path." }); }

            var oldPath = f.FullPath;

            // No-op if the file is already at the target location.
            if (string.Equals(target, oldPath, StringComparison.OrdinalIgnoreCase))
                return Results.Ok(ToDto(f));

            if (!File.Exists(oldPath))
                return Results.BadRequest(new { error = "The file no longer exists on disk." });

            var all = await db.Files.ToListAsync();
            var dup = all.FirstOrDefault(x => x.Id != id &&
                string.Equals(x.FullPath, target, StringComparison.OrdinalIgnoreCase));
            if (dup != null)
                return Results.Conflict(new { error = "Another managed file already lives at that path.", id = dup.Id });
            if (File.Exists(target))
                return Results.Conflict(new { error = "A file with that name already exists in the target folder." });

            try { File.Move(oldPath, target); }
            catch (Exception ex) { return Results.Problem("Could not move file: " + ex.Message); }

            f.FullPath = target;
            f.LastWriteUtc = File.GetLastWriteTimeUtc(target);

            // Fix up any reference-attachments elsewhere that point at the old absolute path.
            var refs = await db.Attachments
                .Where(a => a.Kind == AttachmentKind.Reference && a.SourcePath != null)
                .ToListAsync();
            foreach (var a in refs)
            {
                if (string.Equals(a.SourcePath, oldPath, StringComparison.OrdinalIgnoreCase))
                    a.SourcePath = target;
            }

            await db.SaveChangesAsync();
            return Results.Ok(ToDto(f));
        });

        // Remove from management only (does NOT touch the file on disk).
        g.MapDelete("/files/{id:int}", async (int id, AppDbContext db, GraphService graph) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            await graph.RemoveFileEverywhereAsync(id);
            await RemoveMirrorAsync(db, id);
            await RemoveDocumentNotesAsync(db, id);
            db.Files.Remove(f);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });

        // Delete the real file from disk and drop it from management.
        g.MapDelete("/files/{id:int}/disk", async (int id, AppDbContext db, GraphService graph) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            try
            {
                if (File.Exists(f.FullPath)) File.Delete(f.FullPath);
            }
            catch (Exception ex) { return Results.Problem("Could not delete file: " + ex.Message); }
            await graph.RemoveFileEverywhereAsync(id);
            await RemoveMirrorAsync(db, id);
            await RemoveDocumentNotesAsync(db, id);
            db.Files.Remove(f);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });
    }

    // Drop the DB content mirror when a file is intentionally unmanaged/deleted through the
    // app. (An external deletion instead keeps both the record and the mirror — that's the
    // whole point of the mirror.)
    private static async Task RemoveMirrorAsync(AppDbContext db, int id)
    {
        var mirror = await db.FileContents.FindAsync(id);
        if (mirror is not null) db.FileContents.Remove(mirror);
    }

    // Drop a file's document notes and its dashboard cluster card when the file is unmanaged/
    // deleted, so no orphaned notes or empty group linger on the dashboard.
    private static async Task RemoveDocumentNotesAsync(AppDbContext db, int id)
    {
        var notes = await db.DocumentNotes.Where(n => n.FileId == id).ToListAsync();
        if (notes.Count > 0) db.DocumentNotes.RemoveRange(notes);
        var group = await db.DocumentNoteGroups.FirstOrDefaultAsync(g => g.FileId == id);
        if (group is not null) db.DocumentNoteGroups.Remove(group);
    }

    private static FileDto ToDto(ManagedFile f) =>
        new(f.Id, f.Title, f.FullPath, f.FolderId, f.SortOrder, !File.Exists(f.FullPath));
}
