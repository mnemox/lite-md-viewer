using System.IO.Compression;
using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;
using LiteMdViewer.Services;

namespace LiteMdViewer.Endpoints;

public static class AttachmentsEndpoints
{
    public static void MapAttachments(this WebApplication app)
    {
        var g = app.MapGroup("/api/files");

        // Export the active document's whole graph as a downloadable zip: copy each member
        // .md file + the client-built index.html into a folder, zip it, store it under
        // attachments/, and record a persistent Attachment row owned by the graph.
        g.MapPost("/{id:int}/export", async (int id, ExportRequest req, AppDbContext db, GraphService graph, IWebHostEnvironment env) =>
        {
            var file = await db.Files.FindAsync(id);
            if (file is null) return Results.NotFound();
            if (string.IsNullOrWhiteSpace(req.IndexHtml))
                return Results.BadRequest(new { error = "Missing index.html content." });

            var gid = await graph.GetOrCreateGraphAsync(id);
            var docs = (await graph.GetGraphMemberFilesAsync(gid))
                .Where(f => File.Exists(f.FullPath)).ToList();

            var attachmentsDir = Path.Combine(env.ContentRootPath, "attachments");
            Directory.CreateDirectory(attachmentsDir);
            var workDir = Path.Combine(attachmentsDir, "work", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(workDir);
            var storedName = Guid.NewGuid().ToString("N") + ".zip";
            var storedPath = Path.Combine(attachmentsDir, storedName);

            try
            {
                foreach (var f in docs)
                    File.Copy(f.FullPath, Path.Combine(workDir, ExportFileName(f)), overwrite: true);
                await File.WriteAllTextAsync(Path.Combine(workDir, "index.html"), req.IndexHtml);
                ZipFile.CreateFromDirectory(workDir, storedPath);
            }
            catch (Exception ex)
            {
                try { if (File.Exists(storedPath)) File.Delete(storedPath); } catch { /* best effort */ }
                return Results.Problem("Export failed: " + ex.Message);
            }
            finally
            {
                try { Directory.Delete(workDir, recursive: true); } catch { /* best effort */ }
            }

            var att = new Attachment
            {
                GraphId = gid,
                FileName = DateTime.UtcNow.ToString("dd-MM-yyyy_HH-mm") + "-UTC.zip",
                StoredName = storedName,
                SizeBytes = new FileInfo(storedPath).Length,
                NodeCount = docs.Count,
                CreatedUtc = DateTime.UtcNow,
            };
            db.Attachments.Add(att);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(att));
        });

        // All exports owned by this document's graph (newest first); any member sees them.
        g.MapGet("/{id:int}/attachments", async (int id, AppDbContext db, GraphService graph) =>
        {
            var gid = await graph.GetGraphIdAsync(id);
            if (gid is null) return Results.Ok(Array.Empty<AttachmentDto>());
            return Results.Ok(await db.Attachments.Where(a => a.GraphId == gid.Value)
                .OrderByDescending(a => a.CreatedUtc)
                .Select(a => new AttachmentDto(a.Id, a.FileName, a.SizeBytes, a.NodeCount, a.CreatedUtc))
                .ToListAsync());
        });

        var att = app.MapGroup("/api/attachments");

        // Download a stored zip with an attachment disposition.
        att.MapGet("/{attId:int}/download", async (int attId, AppDbContext db, IWebHostEnvironment env) =>
        {
            var a = await db.Attachments.FindAsync(attId);
            if (a is null) return Results.NotFound();
            var path = Path.Combine(env.ContentRootPath, "attachments", a.StoredName);
            if (!File.Exists(path)) return Results.NotFound(new { error = "Attachment file not found." });
            var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            return Results.File(stream, "application/zip", a.FileName);
        });

        // Delete the stored zip and the DB row.
        att.MapDelete("/{attId:int}", async (int attId, AppDbContext db, IWebHostEnvironment env) =>
        {
            var a = await db.Attachments.FindAsync(attId);
            if (a is null) return Results.NotFound();
            try
            {
                var path = Path.Combine(env.ContentRootPath, "attachments", a.StoredName);
                if (File.Exists(path)) File.Delete(path);
            }
            catch { /* best effort */ }
            db.Attachments.Remove(a);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });
    }

    // Deterministic name for a copied file inside the export — MUST match the JS slug
    // so the index.html links resolve to the copied files.
    private static string ExportFileName(ManagedFile f) => $"{f.Id}-{Slug(f.Title)}.md";

    private static string Slug(string? title)
    {
        var lowered = (title ?? "").ToLowerInvariant();
        var s = new string(lowered.Select(c => (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ? c : '-').ToArray());
        while (s.Contains("--")) s = s.Replace("--", "-");
        s = s.Trim('-');
        return s.Length == 0 ? "doc" : s;
    }

    private static AttachmentDto ToDto(Attachment a) =>
        new(a.Id, a.FileName, a.SizeBytes, a.NodeCount, a.CreatedUtc);
}
