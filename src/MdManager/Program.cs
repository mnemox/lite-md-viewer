using System.Diagnostics;
using Microsoft.EntityFrameworkCore;
using MdManager.Data;
using MdManager.Endpoints;
using MdManager.Models;
using MdManager.Services;

var builder = WebApplication.CreateBuilder(args);

// SQLite next to the app's content root.
var dbPath = Path.Combine(builder.Environment.ContentRootPath, "mdmanager.db");
builder.Services.AddDbContext<AppDbContext>(opt => opt.UseSqlite($"Data Source={dbPath}"));

builder.Services.AddSingleton<LockManager>();
builder.Services.AddSingleton<FsBrowser>();
builder.Services.AddHostedService<FileLockService>();
builder.Services.AddHostedService<FileWatcherService>();

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

app.MapFallbackToFile("index.html");

if (openBrowser)
{
    try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
    catch { /* ignore */ }
}

app.Run();

static void SeedSettings(AppDbContext db)
{
    void Ensure(string key, string value)
    {
        if (db.Settings.Find(key) is null) db.Settings.Add(new Setting { Key = key, Value = value });
    }
    Ensure("theme", "light");
    Ensure("lockOnStartup", "true");
    Ensure("openBrowserOnStart", "false");
    db.SaveChanges();
}
