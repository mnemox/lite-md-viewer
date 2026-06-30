using Microsoft.EntityFrameworkCore;
using MdManager.Data;
using MdManager.Models;
using MdManager.Services;

namespace MdManager.Endpoints;

public static class FilesEndpoints
{
    public static void MapFiles(this WebApplication app)
    {
        var g = app.MapGroup("/api");

        // Whole drawer: folders + files with current status.
        g.MapGet("/tree", async (AppDbContext db) =>
        {
            var folders = await db.Folders
                .OrderBy(f => f.SortOrder).ThenBy(f => f.Name)
                .Select(f => new FolderDto(f.Id, f.Name, f.ParentId, f.SortOrder))
                .ToListAsync();
            var files = await db.Files
                .OrderBy(f => f.SortOrder).ThenBy(f => f.Title)
                .Select(f => new FileDto(f.Id, f.Title, f.FullPath, f.FolderId, f.Status,
                                         f.IsLockRequested, f.SortOrder, f.Status == FileStatus.Missing))
                .ToListAsync();
            return Results.Ok(new TreeDto(folders, files));
        });

        // Register a real on-disk path under management (and lock it by default).
        g.MapPost("/files", async (AddFileRequest req, AppDbContext db, LockManager locks) =>
        {
            if (string.IsNullOrWhiteSpace(req.Path))
                return Results.BadRequest(new { error = "A path is required." });

            string full;
            try { full = Path.GetFullPath(req.Path); }
            catch { return Results.BadRequest(new { error = "Invalid path." }); }

            var ext = Path.GetExtension(full);
            if (!ext.Equals(".md", StringComparison.OrdinalIgnoreCase) &&
                !ext.Equals(".markdown", StringComparison.OrdinalIgnoreCase))
                return Results.BadRequest(new { error = "Only .md or .markdown files are supported." });

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
                IsLockRequested = true,
                SortOrder = maxSort + 1,
                LastWriteUtc = File.GetLastWriteTimeUtc(full),
                AddedUtc = DateTime.UtcNow,
                Status = FileStatus.Unlocked,
            };

            try
            {
                if (locks.CanLock(full)) { locks.Lock(full); file.Status = FileStatus.Locked; }
                else { file.IsLockRequested = false; } // not an NTFS/Windows volume
            }
            catch { file.Status = FileStatus.Unlocked; }

            db.Files.Add(file);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(file));
        });

        // Create a brand-new .md file on disk in a chosen folder, then manage + lock it.
        g.MapPost("/files/new", async (NewFileRequest req, AppDbContext db, LockManager locks) =>
        {
            if (string.IsNullOrWhiteSpace(req.Dir) || string.IsNullOrWhiteSpace(req.Name))
                return Results.BadRequest(new { error = "A folder and a file name are required." });
            if (!Directory.Exists(req.Dir))
                return Results.BadRequest(new { error = "Target folder does not exist." });

            var name = req.Name.Trim();
            if (name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
                return Results.BadRequest(new { error = "Invalid file name." });

            var ext = Path.GetExtension(name);
            if (ext.Length == 0) { name += ".md"; ext = ".md"; }
            if (!ext.Equals(".md", StringComparison.OrdinalIgnoreCase) &&
                !ext.Equals(".markdown", StringComparison.OrdinalIgnoreCase))
                return Results.BadRequest(new { error = "Only .md or .markdown files are supported." });

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
                IsLockRequested = true,
                SortOrder = maxSort + 1,
                LastWriteUtc = File.GetLastWriteTimeUtc(full),
                AddedUtc = DateTime.UtcNow,
                Status = FileStatus.Unlocked,
            };

            try
            {
                if (locks.CanLock(full)) { locks.Lock(full); file.Status = FileStatus.Locked; }
                else { file.IsLockRequested = false; } // not an NTFS/Windows volume
            }
            catch { file.Status = FileStatus.Unlocked; }

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

        g.MapPost("/files/{id:int}/lock", async (int id, AppDbContext db, LockManager locks) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            if (!File.Exists(f.FullPath))
            {
                f.Status = FileStatus.Missing;
                await db.SaveChangesAsync();
                return Results.Conflict(new { error = "File is missing on disk." });
            }
            if (!locks.CanLock(f.FullPath))
                return Results.BadRequest(new { error = "Locking requires an NTFS volume on Windows." });
            try
            {
                locks.Lock(f.FullPath);
                f.IsLockRequested = true;
                f.Status = FileStatus.Locked;
                await db.SaveChangesAsync();
                return Results.Ok(ToDto(f));
            }
            catch (Exception ex) { return Results.Problem("Could not lock: " + ex.Message); }
        });

        g.MapPost("/files/{id:int}/unlock", async (int id, AppDbContext db, LockManager locks) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            locks.Unlock(f.FullPath);
            f.IsLockRequested = false;
            f.Status = File.Exists(f.FullPath) ? FileStatus.Unlocked : FileStatus.Missing;
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(f));
        });

        // Remove from management only (does NOT touch the file on disk).
        g.MapDelete("/files/{id:int}", async (int id, bool? force, AppDbContext db, LockManager locks) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            if (f.IsLockRequested && !(force ?? false))
                return Results.Conflict(new { error = "File is locked. Unlock first, or pass force=true." });
            if (f.IsLockRequested) locks.Unlock(f.FullPath);
            db.Files.Remove(f);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });

        // Delete the real file from disk. Refused while locked.
        g.MapDelete("/files/{id:int}/disk", async (int id, AppDbContext db) =>
        {
            var f = await db.Files.FindAsync(id);
            if (f is null) return Results.NotFound();
            if (f.IsLockRequested)
                return Results.Conflict(new { error = "File is locked. Unlock before deleting from disk." });
            try
            {
                if (File.Exists(f.FullPath)) File.Delete(f.FullPath);
            }
            catch (Exception ex) { return Results.Problem("Could not delete file: " + ex.Message); }
            db.Files.Remove(f);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });
    }

    private static FileDto ToDto(ManagedFile f) =>
        new(f.Id, f.Title, f.FullPath, f.FolderId, f.Status, f.IsLockRequested, f.SortOrder, f.Status == FileStatus.Missing);
}
