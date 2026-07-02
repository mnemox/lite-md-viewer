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
                Kind = AttachmentKind.Export,
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

        // All attachments owned by this document's graph (newest first); any member sees them.
        g.MapGet("/{id:int}/attachments", async (int id, AppDbContext db, GraphService graph) =>
        {
            var gid = await graph.GetGraphIdAsync(id);
            if (gid is null) return Results.Ok(Array.Empty<AttachmentDto>());
            var rows = await db.Attachments.Where(a => a.GraphId == gid.Value)
                .OrderByDescending(a => a.CreatedUtc)
                .ToListAsync();
            return Results.Ok(rows.Select(ToDto));
        });

        // Attach an existing on-disk file by reference: only a record is stored; the file
        // stays where it is. Removing the attachment later deletes just the record.
        g.MapPost("/{id:int}/attachments/reference", async (int id, AddAttachmentReferenceRequest req, AppDbContext db, GraphService graph) =>
        {
            var file = await db.Files.FindAsync(id);
            if (file is null) return Results.NotFound();
            if (string.IsNullOrWhiteSpace(req.Path))
                return Results.BadRequest(new { error = "Enter a path to a file." });
            string full;
            try { full = Path.GetFullPath(req.Path.Trim()); }
            catch { return Results.BadRequest(new { error = "That path is not valid." }); }
            if (!File.Exists(full)) return Results.BadRequest(new { error = "No file was found at that path." });

            var gid = await graph.GetOrCreateGraphAsync(id);
            if (await db.Attachments.AnyAsync(a => a.GraphId == gid && a.Kind == AttachmentKind.Reference && a.SourcePath == full))
                return Results.BadRequest(new { error = "That file is already attached." });

            var att = new Attachment
            {
                GraphId = gid,
                Kind = AttachmentKind.Reference,
                FileName = Path.GetFileName(full),
                SourcePath = full,
                SizeBytes = new FileInfo(full).Length,
                CreatedUtc = DateTime.UtcNow,
            };
            db.Attachments.Add(att);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(att));
        });

        // Upload a file into the application's attachments folder. Removing the attachment
        // later deletes the stored copy as well.
        g.MapPost("/{id:int}/attachments/upload", async (int id, HttpRequest request, AppDbContext db, GraphService graph, IWebHostEnvironment env) =>
        {
            var file = await db.Files.FindAsync(id);
            if (file is null) return Results.NotFound();
            if (!request.HasFormContentType) return Results.BadRequest(new { error = "Expected a file upload." });
            var form = await request.ReadFormAsync();
            var upload = form.Files.GetFile("file");
            if (upload is null || upload.Length == 0)
                return Results.BadRequest(new { error = "No file was uploaded." });

            var gid = await graph.GetOrCreateGraphAsync(id);
            var attachmentsDir = Path.Combine(env.ContentRootPath, "attachments");
            Directory.CreateDirectory(attachmentsDir);
            var displayName = Path.GetFileName(upload.FileName);
            if (string.IsNullOrWhiteSpace(displayName)) displayName = "file";
            var storedName = Guid.NewGuid().ToString("N") + Path.GetExtension(displayName);
            var storedPath = Path.Combine(attachmentsDir, storedName);

            try
            {
                await using var target = new FileStream(storedPath, FileMode.CreateNew, FileAccess.Write);
                await upload.CopyToAsync(target);
            }
            catch (Exception ex)
            {
                try { if (File.Exists(storedPath)) File.Delete(storedPath); } catch { /* best effort */ }
                return Results.Problem("Upload failed: " + ex.Message);
            }

            var att = new Attachment
            {
                GraphId = gid,
                Kind = AttachmentKind.Upload,
                FileName = displayName,
                StoredName = storedName,
                SizeBytes = upload.Length,
                CreatedUtc = DateTime.UtcNow,
            };
            db.Attachments.Add(att);
            await db.SaveChangesAsync();
            return Results.Ok(ToDto(att));
        });

        var att = app.MapGroup("/api/attachments");

        // Download an attachment: references stream from their source path, exports and
        // uploads from the stored copy under attachments/.
        att.MapGet("/{attId:int}/download", async (int attId, AppDbContext db, IWebHostEnvironment env) =>
        {
            var a = await db.Attachments.FindAsync(attId);
            if (a is null) return Results.NotFound();
            var path = a.Kind == AttachmentKind.Reference
                ? (a.SourcePath ?? "")
                : Path.Combine(env.ContentRootPath, "attachments", a.StoredName);
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                return Results.NotFound(new { error = "Attachment file not found." });
            var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            return Results.File(stream, ContentType(a.FileName), a.FileName);
        });

        // Delete an attachment. References remove only the DB record (the source file is
        // kept); exports and uploads also delete the stored file.
        att.MapDelete("/{attId:int}", async (int attId, AppDbContext db, IWebHostEnvironment env) =>
        {
            var a = await db.Attachments.FindAsync(attId);
            if (a is null) return Results.NotFound();
            if (a.Kind != AttachmentKind.Reference && !string.IsNullOrEmpty(a.StoredName))
            {
                try
                {
                    var path = Path.Combine(env.ContentRootPath, "attachments", a.StoredName);
                    if (File.Exists(path)) File.Delete(path);
                }
                catch { /* best effort */ }
            }
            db.Attachments.Remove(a);
            await db.SaveChangesAsync();
            return Results.NoContent();
        });
    }

    private static readonly Microsoft.AspNetCore.StaticFiles.FileExtensionContentTypeProvider ContentTypes = new();

    private static string ContentType(string fileName) =>
        ContentTypes.TryGetContentType(fileName, out var ct) ? ct : "application/octet-stream";

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

    // Missing: a referenced source file that is no longer on disk.
    private static AttachmentDto ToDto(Attachment a) =>
        new(a.Id, a.FileName, a.SizeBytes, a.NodeCount, a.CreatedUtc, a.Kind,
            a.Kind == AttachmentKind.Reference && !File.Exists(a.SourcePath ?? ""));
}
