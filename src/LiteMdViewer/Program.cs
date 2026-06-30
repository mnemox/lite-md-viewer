using System.Diagnostics;
using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Endpoints;
using LiteMdViewer.Models;
using LiteMdViewer.Services;

var builder = WebApplication.CreateBuilder(args);

// SQLite next to the app's content root.
var dbPath = Path.Combine(builder.Environment.ContentRootPath, "litemdviewer.db");
builder.Services.AddDbContext<AppDbContext>(opt => opt.UseSqlite($"Data Source={dbPath}"));

builder.Services.AddSingleton<FsBrowser>();

// HTTP only, loopback only — the disk-touching endpoints must never be off-box.
const string url = "http://127.0.0.1:5099";
builder.WebHost.UseUrls(url);

var app = builder.Build();

// Create schema (if needed) and seed defaults before hosted services start.
bool openBrowser;
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
    db.Database.EnsureCreated();
    EnsureRelationsTable(db);   // EnsureCreated won't add tables to a pre-existing DB; do it idempotently
    EnsureAttachmentsTable(db);
    SeedSettings(db);
    openBrowser = db.Settings.Find("openBrowserOnStart")?.Value == "true";
}

app.UseDefaultFiles();
app.UseStaticFiles();

app.MapFiles();
app.MapFolders();
app.MapContent();
app.MapBrowse();
app.MapSettings();
app.MapRelations();
app.MapAttachments();

app.MapFallbackToFile("index.html");

if (openBrowser)
{
    try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
    catch { /* ignore */ }
}

app.Run();

// Adds the Relations table to an already-created DB without a migration or reset.
// On a fresh DB EnsureCreated already made it from the model, so this is a no-op;
// on an existing DB (created before Relations existed) this adds it, preserving data.
static void EnsureRelationsTable(AppDbContext db)
{
    db.Database.ExecuteSqlRaw(
        @"CREATE TABLE IF NOT EXISTS ""Relations"" (
            ""Id"" INTEGER NOT NULL CONSTRAINT ""PK_Relations"" PRIMARY KEY AUTOINCREMENT,
            ""FromId"" INTEGER NOT NULL,
            ""ToId"" INTEGER NOT NULL,
            ""Kind"" TEXT NOT NULL,
            ""CreatedUtc"" TEXT NOT NULL);");
    db.Database.ExecuteSqlRaw(
        @"CREATE UNIQUE INDEX IF NOT EXISTS ""IX_Relations_FromId_ToId_Kind""
          ON ""Relations"" (""FromId"", ""ToId"", ""Kind"");");
}

// Adds the Attachments table to an already-created DB without a migration or reset.
static void EnsureAttachmentsTable(AppDbContext db)
{
    db.Database.ExecuteSqlRaw(
        @"CREATE TABLE IF NOT EXISTS ""Attachments"" (
            ""Id"" INTEGER NOT NULL CONSTRAINT ""PK_Attachments"" PRIMARY KEY AUTOINCREMENT,
            ""FileId"" INTEGER NOT NULL,
            ""FileName"" TEXT NOT NULL,
            ""StoredName"" TEXT NOT NULL,
            ""SizeBytes"" INTEGER NOT NULL,
            ""NodeCount"" INTEGER NOT NULL,
            ""CreatedUtc"" TEXT NOT NULL);");
    db.Database.ExecuteSqlRaw(
        @"CREATE INDEX IF NOT EXISTS ""IX_Attachments_FileId"" ON ""Attachments"" (""FileId"");");
}

static void SeedSettings(AppDbContext db)
{
    void Ensure(string key, string value)
    {
        if (db.Settings.Find(key) is null) db.Settings.Add(new Setting { Key = key, Value = value });
    }
    Ensure("theme", "light");
    Ensure("openBrowserOnStart", "false");
    db.SaveChanges();
}
