using System.Data;
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
builder.Services.AddScoped<GraphService>();

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
    EnsureGraphTables(db);                       // add graph tables to a pre-existing DB (no-op on fresh)
    MigrateLegacyToGraph(db, app.Environment);   // one-time: legacy Relation/Attachment(FileId) → graph model
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

// Adds the explicit-graph tables to an already-created DB (EnsureCreated is a no-op on an
// existing DB). On a fresh DB EnsureCreated already built them from the model, so these are
// no-ops. Table/index names match EF's conventions so fresh and migrated DBs are identical.
static void EnsureGraphTables(AppDbContext db)
{
    db.Database.ExecuteSqlRaw(@"CREATE TABLE IF NOT EXISTS ""Graphs"" (
        ""Id"" INTEGER NOT NULL CONSTRAINT ""PK_Graphs"" PRIMARY KEY AUTOINCREMENT,
        ""CreatedUtc"" TEXT NOT NULL);");

    db.Database.ExecuteSqlRaw(@"CREATE TABLE IF NOT EXISTS ""GraphMembers"" (
        ""Id"" INTEGER NOT NULL CONSTRAINT ""PK_GraphMembers"" PRIMARY KEY AUTOINCREMENT,
        ""GraphId"" INTEGER NOT NULL,
        ""FileId"" INTEGER NOT NULL);");
    db.Database.ExecuteSqlRaw(@"CREATE UNIQUE INDEX IF NOT EXISTS ""IX_GraphMembers_FileId"" ON ""GraphMembers"" (""FileId"");");
    db.Database.ExecuteSqlRaw(@"CREATE INDEX IF NOT EXISTS ""IX_GraphMembers_GraphId"" ON ""GraphMembers"" (""GraphId"");");

    db.Database.ExecuteSqlRaw(@"CREATE TABLE IF NOT EXISTS ""GraphEdges"" (
        ""Id"" INTEGER NOT NULL CONSTRAINT ""PK_GraphEdges"" PRIMARY KEY AUTOINCREMENT,
        ""GraphId"" INTEGER NOT NULL,
        ""FromId"" INTEGER NOT NULL,
        ""ToId"" INTEGER NOT NULL,
        ""Kind"" TEXT NOT NULL);");
    db.Database.ExecuteSqlRaw(@"CREATE UNIQUE INDEX IF NOT EXISTS ""IX_GraphEdges_FromId_ToId_Kind"" ON ""GraphEdges"" (""FromId"", ""ToId"", ""Kind"");");
    db.Database.ExecuteSqlRaw(@"CREATE INDEX IF NOT EXISTS ""IX_GraphEdges_GraphId"" ON ""GraphEdges"" (""GraphId"");");

    db.Database.ExecuteSqlRaw(@"CREATE TABLE IF NOT EXISTS ""GraphCompanions"" (
        ""Id"" INTEGER NOT NULL CONSTRAINT ""PK_GraphCompanions"" PRIMARY KEY AUTOINCREMENT,
        ""GraphId"" INTEGER NOT NULL,
        ""FileId"" INTEGER NOT NULL);");
    db.Database.ExecuteSqlRaw(@"CREATE UNIQUE INDEX IF NOT EXISTS ""IX_GraphCompanions_GraphId_FileId"" ON ""GraphCompanions"" (""GraphId"", ""FileId"");");
    db.Database.ExecuteSqlRaw(@"CREATE INDEX IF NOT EXISTS ""IX_GraphCompanions_GraphId"" ON ""GraphCompanions"" (""GraphId"");");
}

// One-time, idempotent migration from the legacy Relation/Attachment(FileId) model to the
// explicit-graph model. Detected by the presence of the Relations table or an
// Attachments.FileId column; rebuilds inside one transaction (rolls back cleanly on failure
// and retries next boot). Afterwards both markers are gone, so re-runs early-return.
static void MigrateLegacyToGraph(AppDbContext db, IWebHostEnvironment env)
{
    var relationsExists = TableExists(db, "Relations");
    var attHasFileId = ColumnExists(db, "Attachments", "FileId");
    if (!relationsExists && !attHasFileId) return;

    var legacyRels = relationsExists ? ReadRelations(db) : new List<(int From, int To, string Kind)>();
    var legacyAtts = attHasFileId ? ReadAttachments(db) : new List<(int Id, int FileId, string StoredName)>();
    var fileIds = db.Files.Select(f => f.Id).ToHashSet();

    using var tx = db.Database.BeginTransaction();

    // 1. Connected components over reference+sibling edges (both ends present) → graphs.
    var adj = new Dictionary<int, List<int>>();
    void Link(int a, int b) => (adj.TryGetValue(a, out var l) ? l : adj[a] = new()).Add(b);
    foreach (var r in legacyRels)
        if ((r.Kind == "reference" || r.Kind == "sibling") && fileIds.Contains(r.From) && fileIds.Contains(r.To))
        { Link(r.From, r.To); Link(r.To, r.From); }

    var fileToGraph = new Dictionary<int, int>();
    var visited = new HashSet<int>();
    foreach (var startNode in adj.Keys)
    {
        if (!visited.Add(startNode)) continue;
        var comp = new List<int> { startNode };
        var q = new Queue<int>(); q.Enqueue(startNode);
        while (q.Count > 0)
            foreach (var n in adj[q.Dequeue()]) if (visited.Add(n)) { comp.Add(n); q.Enqueue(n); }
        var g = new Graph { CreatedUtc = DateTime.UtcNow }; db.Graphs.Add(g); db.SaveChanges();
        foreach (var fid in comp) { db.GraphMembers.Add(new GraphMember { GraphId = g.Id, FileId = fid }); fileToGraph[fid] = g.Id; }
        db.SaveChanges();
    }

    var edgeSeen = new HashSet<(int, int, string)>();
    foreach (var r in legacyRels)
    {
        if (r.Kind != "reference" && r.Kind != "sibling") continue;
        if (!fileToGraph.TryGetValue(r.From, out var g1) || !fileToGraph.TryGetValue(r.To, out var g2) || g1 != g2) continue;
        if (!edgeSeen.Add((r.From, r.To, r.Kind))) continue;
        db.GraphEdges.Add(new GraphEdge { GraphId = g1, FromId = r.From, ToId = r.To, Kind = r.Kind });
    }
    db.SaveChanges();

    int Singleton(int fid)
    {
        if (fileToGraph.TryGetValue(fid, out var g)) return g;
        var ng = new Graph { CreatedUtc = DateTime.UtcNow }; db.Graphs.Add(ng); db.SaveChanges();
        db.GraphMembers.Add(new GraphMember { GraphId = ng.Id, FileId = fid }); db.SaveChanges();
        fileToGraph[fid] = ng.Id; return ng.Id;
    }

    // 2. Companions: skip pairs that are co-members; otherwise attach to each side's graph.
    var compSeen = new HashSet<(int, int)>();
    foreach (var r in legacyRels)
    {
        if (r.Kind != "companion" || r.From == r.To || !fileIds.Contains(r.From) || !fileIds.Contains(r.To)) continue;
        var key = r.From < r.To ? (r.From, r.To) : (r.To, r.From);
        if (!compSeen.Add(key)) continue;
        fileToGraph.TryGetValue(r.From, out var ga);
        fileToGraph.TryGetValue(r.To, out var gb);
        if (ga != 0 && ga == gb) continue; // co-members → redundant companion
        var gA = Singleton(r.From);
        if (!db.GraphMembers.Any(m => m.GraphId == gA && m.FileId == r.To) && !db.GraphCompanions.Any(c => c.GraphId == gA && c.FileId == r.To))
            db.GraphCompanions.Add(new GraphCompanion { GraphId = gA, FileId = r.To });
        var gB = Singleton(r.To);
        if (!db.GraphMembers.Any(m => m.GraphId == gB && m.FileId == r.From) && !db.GraphCompanions.Any(c => c.GraphId == gB && c.FileId == r.From))
            db.GraphCompanions.Add(new GraphCompanion { GraphId = gB, FileId = r.From });
    }
    db.SaveChanges();

    // 3. Resolve each legacy attachment to a graph (orphans — file gone — are dropped).
    var attToGraph = new Dictionary<int, int>();
    var orphanIds = new List<int>();
    var orphanZips = new List<string>();
    foreach (var a in legacyAtts)
    {
        if (!fileIds.Contains(a.FileId)) { orphanIds.Add(a.Id); orphanZips.Add(a.StoredName); continue; }
        attToGraph[a.Id] = Singleton(a.FileId);
    }

    // 4. Rebuild the Attachments table (FileId → GraphId) via rename+recreate+copy.
    if (attHasFileId)
    {
        db.Database.ExecuteSqlRaw(@"ALTER TABLE ""Attachments"" RENAME TO ""Attachments_old"";");
        db.Database.ExecuteSqlRaw(@"CREATE TABLE ""Attachments"" (
            ""Id"" INTEGER NOT NULL CONSTRAINT ""PK_Attachments"" PRIMARY KEY AUTOINCREMENT,
            ""GraphId"" INTEGER NOT NULL,
            ""FileName"" TEXT NOT NULL,
            ""StoredName"" TEXT NOT NULL,
            ""SizeBytes"" INTEGER NOT NULL,
            ""NodeCount"" INTEGER NOT NULL,
            ""CreatedUtc"" TEXT NOT NULL);");
        // `exclude` is composed only from integer ids read from the DB — no user input.
        var exclude = orphanIds.Count > 0 ? $@" WHERE ""Id"" NOT IN ({string.Join(",", orphanIds)})" : "";
#pragma warning disable EF1002
        db.Database.ExecuteSqlRaw($@"INSERT INTO ""Attachments"" (""Id"", ""GraphId"", ""FileName"", ""StoredName"", ""SizeBytes"", ""NodeCount"", ""CreatedUtc"")
            SELECT ""Id"", 0, ""FileName"", ""StoredName"", ""SizeBytes"", ""NodeCount"", ""CreatedUtc"" FROM ""Attachments_old""{exclude};");
#pragma warning restore EF1002
        db.Database.ExecuteSqlRaw(@"DROP TABLE ""Attachments_old"";");
        db.Database.ExecuteSqlRaw(@"CREATE INDEX ""IX_Attachments_GraphId"" ON ""Attachments"" (""GraphId"");");
        foreach (var kv in attToGraph)
            db.Database.ExecuteSqlRaw(@"UPDATE ""Attachments"" SET ""GraphId"" = {0} WHERE ""Id"" = {1};", kv.Value, kv.Key);
    }

    // 5. Drop the legacy Relations table, then commit.
    if (relationsExists) db.Database.ExecuteSqlRaw(@"DROP TABLE IF EXISTS ""Relations"";");
    tx.Commit();

    // Best-effort: delete orphan attachment zips (filesystem, non-transactional).
    foreach (var stored in orphanZips)
    {
        try { var p = Path.Combine(env.ContentRootPath, "attachments", stored); if (File.Exists(p)) File.Delete(p); }
        catch { /* best effort */ }
    }
}

static bool TableExists(AppDbContext db, string name)
{
    var conn = db.Database.GetDbConnection();
    var close = conn.State != ConnectionState.Open;
    if (close) conn.Open();
    try
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT count(*) FROM sqlite_master WHERE type='table' AND name = $n;";
        var p = cmd.CreateParameter(); p.ParameterName = "$n"; p.Value = name; cmd.Parameters.Add(p);
        return Convert.ToInt64(cmd.ExecuteScalar()) > 0;
    }
    finally { if (close) conn.Close(); }
}

static bool ColumnExists(AppDbContext db, string table, string column)
{
    var conn = db.Database.GetDbConnection();
    var close = conn.State != ConnectionState.Open;
    if (close) conn.Open();
    try
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = $@"PRAGMA table_info(""{table}"");";
        using var r = cmd.ExecuteReader();
        while (r.Read())
            if (string.Equals(r.GetString(1), column, StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }
    finally { if (close) conn.Close(); }
}

static List<(int From, int To, string Kind)> ReadRelations(AppDbContext db)
{
    var list = new List<(int, int, string)>();
    var conn = db.Database.GetDbConnection();
    var close = conn.State != ConnectionState.Open;
    if (close) conn.Open();
    try
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = @"SELECT ""FromId"", ""ToId"", ""Kind"" FROM ""Relations"";";
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add((r.GetInt32(0), r.GetInt32(1), r.GetString(2)));
    }
    finally { if (close) conn.Close(); }
    return list;
}

static List<(int Id, int FileId, string StoredName)> ReadAttachments(AppDbContext db)
{
    var list = new List<(int, int, string)>();
    var conn = db.Database.GetDbConnection();
    var close = conn.State != ConnectionState.Open;
    if (close) conn.Open();
    try
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = @"SELECT ""Id"", ""FileId"", ""StoredName"" FROM ""Attachments"";";
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add((r.GetInt32(0), r.GetInt32(1), r.GetString(2)));
    }
    finally { if (close) conn.Close(); }
    return list;
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
