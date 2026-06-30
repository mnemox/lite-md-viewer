using Microsoft.EntityFrameworkCore;
using MdManager.Data;
using MdManager.Models;

namespace MdManager.Endpoints;

public static class FoldersEndpoints
{
    public static void MapFolders(this WebApplication app)
    {
        var g = app.MapGroup("/api/folders");

        g.MapGet("", async (AppDbContext db) =>
            Results.Ok(await db.Folders
                .OrderBy(f => f.SortOrder).ThenBy(f => f.Name)
                .Select(f => new FolderDto(f.Id, f.Name, f.ParentId, f.SortOrder))
                .ToListAsync()));

        g.MapPost("", async (CreateFolderRequest req, AppDbContext db) =>
        {
            if (string.IsNullOrWhiteSpace(req.Name))
                return Results.BadRequest(new { error = "A folder name is required." });
            var max = await db.Folders.AnyAsync() ? await db.Folders.MaxAsync(f => f.SortOrder) : 0;
            var folder = new Folder { Name = req.Name.Trim(), ParentId = req.ParentId, SortOrder = max + 1 };
            db.Folders.Add(folder);
            await db.SaveChangesAsync();
            return Results.Ok(new FolderDto(folder.Id, folder.Name, folder.ParentId, folder.SortOrder));
        });

        g.MapPatch("/{id:int}", async (int id, PatchFolderRequest req, AppDbContext db) =>
        {
            var folder = await db.Folders.FindAsync(id);
            if (folder is null) return Results.NotFound();

            if (req.Name is not null) folder.Name = req.Name.Trim();
            if (req.MoveToRoot) folder.ParentId = null;
            else if (req.ParentId.HasValue)
            {
                if (req.ParentId.Value == id)
                    return Results.BadRequest(new { error = "A folder cannot be its own parent." });
                if (await CreatesCycle(db, id, req.ParentId.Value))
                    return Results.BadRequest(new { error = "That move would create a cycle." });
                folder.ParentId = req.ParentId;
            }
            if (req.SortOrder.HasValue) folder.SortOrder = req.SortOrder.Value;

            await db.SaveChangesAsync();
            return Results.Ok(new FolderDto(folder.Id, folder.Name, folder.ParentId, folder.SortOrder));
        });

        // Delete folder; reparent its children up to its own parent (or root).
        g.MapDelete("/{id:int}", async (int id, AppDbContext db) =>
        {
            var folder = await db.Folders.FindAsync(id);
            if (folder is null) return Results.NotFound();

            foreach (var c in await db.Folders.Where(f => f.ParentId == id).ToListAsync())
                c.ParentId = folder.ParentId;
            foreach (var c in await db.Files.Where(f => f.FolderId == id).ToListAsync())
                c.FolderId = folder.ParentId;

            db.Folders.Remove(folder);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });
    }

    private static async Task<bool> CreatesCycle(AppDbContext db, int folderId, int newParentId)
    {
        var current = await db.Folders.FindAsync(newParentId);
        var guard = 0;
        while (current is not null && guard++ < 1000)
        {
            if (current.Id == folderId) return true;
            if (current.ParentId is null) break;
            current = await db.Folders.FindAsync(current.ParentId.Value);
        }
        return false;
    }
}
