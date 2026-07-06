using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Data;
using LiteMdViewer.Models;

namespace LiteMdViewer.Services;

/// <summary>
/// Mirrors every managed file's on-disk content into the database (the <c>FileContents</c>
/// table) and keeps it fresh, so the last-known content survives the file being deleted or
/// moved out from under the app.
///
/// Two mechanisms cooperate:
///  - a <see cref="FileSystemWatcher"/> per managed-file directory reacts to writes in near
///    real time (debounced, since editors fire several events per save), and
///  - a periodic reconcile sweep (<see cref="ReconcileIntervalSeconds"/>) rebuilds the watcher
///    set as the managed list changes, and re-syncs any file whose mtime moved — a safety net
///    for coalesced/missed events and for files that reappear on disk.
///
/// It only ever <em>writes</em> the mirror; a missing file is left alone (its stored copy is
/// the whole point). The mirror row is removed only when the file is unmanaged/deleted through
/// the app (handled by the delete endpoints, with a defensive prune here too).
/// </summary>
public class FileSyncService : BackgroundService
{
    private const int DebounceMs = 500;
    private const int TickMs = 250;
    private const int ReconcileIntervalSeconds = 10;

    private readonly IServiceScopeFactory _scopeFactory;
    private readonly ILogger<FileSyncService> _logger;

    // One watcher per distinct directory that holds a managed file.
    private readonly Dictionary<string, FileSystemWatcher> _watchers =
        new(StringComparer.OrdinalIgnoreCase);

    // Paths touched by watcher events, mapped to the earliest UTC time they should be synced
    // (pushed forward on each new event so a burst of writes collapses into one sync).
    private readonly ConcurrentDictionary<string, DateTime> _dirty =
        new(StringComparer.OrdinalIgnoreCase);

    // Serializes all DB writes from this service so SQLite sees a single writer.
    private readonly SemaphoreSlim _writeLock = new(1, 1);

    public FileSyncService(IServiceScopeFactory scopeFactory, ILogger<FileSyncService> logger)
    {
        _scopeFactory = scopeFactory;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        // Let host startup (schema create/seed) settle before the first sweep.
        try { await Task.Delay(1000, ct); }
        catch (OperationCanceledException) { return; }

        await ReconcileAsync(ct);           // initial: build watchers + full content sync
        var lastReconcile = DateTime.UtcNow;

        using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(TickMs));
        try
        {
            while (await timer.WaitForNextTickAsync(ct))
            {
                await DrainDirtyAsync(ct);  // near-real-time, debounced syncs
                if (DateTime.UtcNow - lastReconcile >= TimeSpan.FromSeconds(ReconcileIntervalSeconds))
                {
                    await ReconcileAsync(ct);
                    lastReconcile = DateTime.UtcNow;
                }
            }
        }
        catch (OperationCanceledException) { /* shutting down */ }
        finally { DisposeWatchers(); }
    }

    // ---- real-time path (watcher events → debounced syncs) ----

    private void MarkDirty(string path)
    {
        string canon;
        try { canon = Path.GetFullPath(path); }
        catch { return; }
        _dirty[canon] = DateTime.UtcNow.AddMilliseconds(DebounceMs);
    }

    private async Task DrainDirtyAsync(CancellationToken ct)
    {
        var now = DateTime.UtcNow;
        var due = _dirty.Where(kv => kv.Value <= now).Select(kv => kv.Key).ToList();
        foreach (var path in due)
        {
            _dirty.TryRemove(path, out _);
            await SyncPathAsync(path, ct);
        }
    }

    private async Task SyncPathAsync(string fullPath, CancellationToken ct)
    {
        await _writeLock.WaitAsync(ct);
        try
        {
            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();

            // Match the canonical stored path case-insensitively, as the rest of the app does.
            var files = await db.Files.AsNoTracking().ToListAsync(ct);
            var f = files.FirstOrDefault(x =>
                string.Equals(x.FullPath, fullPath, StringComparison.OrdinalIgnoreCase));
            if (f is null) return;                       // event for a file we don't manage
            if (!File.Exists(f.FullPath)) return;        // deleted → keep the stored copy

            DateTime mtime;
            try { mtime = File.GetLastWriteTimeUtc(f.FullPath); }
            catch { return; }

            var existing = await db.FileContents.FindAsync(new object[] { f.Id }, ct);
            if (existing is not null && existing.SourceWriteUtc == mtime) return;  // unchanged

            if (await UpsertFromDiskAsync(db, f.Id, f.FullPath, existing, mtime, ct))
                await db.SaveChangesAsync(ct);
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception ex) { _logger.LogWarning(ex, "content sync failed for {Path}", fullPath); }
        finally { _writeLock.Release(); }
    }

    // ---- periodic reconcile (watchers + full sweep) ----

    private async Task ReconcileAsync(CancellationToken ct)
    {
        await _writeLock.WaitAsync(ct);
        try
        {
            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var files = await db.Files.AsNoTracking().ToListAsync(ct);

            RefreshWatchers(files);

            // Drop mirror rows whose managed file is gone (defensive; delete endpoints also clean up).
            var fileIds = files.Select(f => f.Id).ToHashSet();
            var orphans = await db.FileContents.Where(c => !fileIds.Contains(c.FileId)).ToListAsync(ct);
            if (orphans.Count > 0) db.FileContents.RemoveRange(orphans);

            var contents = await db.FileContents.ToDictionaryAsync(c => c.FileId, ct);
            var changed = orphans.Count > 0;
            foreach (var f in files)
            {
                ct.ThrowIfCancellationRequested();
                if (!File.Exists(f.FullPath)) continue;  // missing → preserve the stored copy

                DateTime mtime;
                try { mtime = File.GetLastWriteTimeUtc(f.FullPath); }
                catch { continue; }

                contents.TryGetValue(f.Id, out var existing);
                if (existing is not null && existing.SourceWriteUtc == mtime) continue;  // unchanged

                if (await UpsertFromDiskAsync(db, f.Id, f.FullPath, existing, mtime, ct))
                    changed = true;
            }

            if (changed) await db.SaveChangesAsync(ct);
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception ex) { _logger.LogWarning(ex, "content reconcile failed"); }
        finally { _writeLock.Release(); }
    }

    private void RefreshWatchers(IReadOnlyList<ManagedFile> files)
    {
        var dirs = files
            .Select(f => Path.GetDirectoryName(f.FullPath))
            .Where(d => !string.IsNullOrEmpty(d))
            .Select(d => d!)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        foreach (var dir in _watchers.Keys.ToList())
        {
            if (dirs.Contains(dir)) continue;
            try { _watchers[dir].Dispose(); } catch { /* ignore */ }
            _watchers.Remove(dir);
        }

        foreach (var dir in dirs)
        {
            if (_watchers.ContainsKey(dir) || !Directory.Exists(dir)) continue;
            TryAddWatcher(dir);
        }
    }

    private void TryAddWatcher(string dir)
    {
        try
        {
            var w = new FileSystemWatcher(dir)
            {
                IncludeSubdirectories = false,
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName |
                               NotifyFilters.Size | NotifyFilters.CreationTime,
                InternalBufferSize = 64 * 1024,
            };
            w.Filters.Add("*.md");
            w.Filters.Add("*.markdown");
            w.Changed += (_, e) => MarkDirty(e.FullPath);
            w.Created += (_, e) => MarkDirty(e.FullPath);
            w.Renamed += (_, e) => MarkDirty(e.FullPath);
            w.EnableRaisingEvents = true;
            _watchers[dir] = w;
        }
        catch (Exception ex) { _logger.LogWarning(ex, "could not watch directory {Dir}", dir); }
    }

    // ---- shared read + upsert ----

    // Reads the file (tolerant of another process holding it open) and writes the mirror row.
    // Returns true when the tracked graph changed (something to SaveChanges). A transient read
    // failure (file mid-write) returns false; the next tick / reconcile retries.
    private async Task<bool> UpsertFromDiskAsync(
        AppDbContext db, int fileId, string fullPath, FileContent? existing, DateTime mtime,
        CancellationToken ct)
    {
        string text;
        try
        {
            await using var fs = new FileStream(fullPath, FileMode.Open, FileAccess.Read,
                                                FileShare.ReadWrite | FileShare.Delete);
            using var sr = new StreamReader(fs);
            text = await sr.ReadToEndAsync(ct);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "deferred content read for {Path}", fullPath);
            return false;
        }

        var hash = Sha256Hex(text);
        var now = DateTime.UtcNow;

        if (existing is null)
        {
            db.FileContents.Add(new FileContent
            {
                FileId = fileId,
                Content = text,
                ContentHash = hash,
                SourceWriteUtc = mtime,
                SyncedUtc = now,
            });
            return true;
        }

        existing.SourceWriteUtc = mtime;              // remember we've seen this mtime
        if (existing.ContentHash == hash) return true; // touched but identical → only mtime moved

        existing.Content = text;
        existing.ContentHash = hash;
        existing.SyncedUtc = now;
        return true;
    }

    private static string Sha256Hex(string text) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text)));

    private void DisposeWatchers()
    {
        foreach (var w in _watchers.Values)
        {
            try { w.Dispose(); } catch { /* ignore */ }
        }
        _watchers.Clear();
    }

    public override void Dispose()
    {
        DisposeWatchers();
        _writeLock.Dispose();
        base.Dispose();
        GC.SuppressFinalize(this);
    }
}
