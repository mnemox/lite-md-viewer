using Microsoft.EntityFrameworkCore;
using MdManager.Data;
using MdManager.Models;

namespace MdManager.Services;

/// <summary>
/// Long-running background service that periodically reconciles each managed
/// file's status: flags files that disappeared from disk as <c>missing</c>, and
/// re-applies the Deny-Delete ACE to locked files if it was stripped externally.
/// The UI reflects status changes by polling <c>/api/tree</c>.
/// </summary>
public sealed class FileWatcherService : BackgroundService
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly LockManager _locks;
    private readonly ILogger<FileWatcherService> _log;
    private static readonly TimeSpan Interval = TimeSpan.FromSeconds(15);

    public FileWatcherService(IServiceScopeFactory scopeFactory, LockManager locks, ILogger<FileWatcherService> log)
    {
        _scopeFactory = scopeFactory;
        _locks = locks;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        // Let the startup lock reconcile (FileLockService) run first.
        try { await Task.Delay(TimeSpan.FromSeconds(5), ct); } catch { return; }

        using var timer = new PeriodicTimer(Interval);
        do
        {
            try { await Scan(ct); }
            catch (OperationCanceledException) { break; }
            catch (Exception ex) { _log.LogWarning(ex, "Status scan failed."); }
        }
        while (await timer.WaitForNextTickAsync(ct));
    }

    private async Task Scan(CancellationToken ct)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var files = await db.Files.ToListAsync(ct);
        var changed = false;

        foreach (var f in files)
        {
            string status;
            if (!File.Exists(f.FullPath))
            {
                status = FileStatus.Missing;
            }
            else if (f.IsLockRequested && _locks.CanLock(f.FullPath))
            {
                if (!_locks.HasLock(f.FullPath))
                {
                    try { _locks.Lock(f.FullPath); }
                    catch (Exception ex) { _log.LogWarning(ex, "Re-lock failed for {Path}", f.FullPath); }
                }
                status = FileStatus.Locked;
            }
            else
            {
                status = FileStatus.Unlocked;
            }

            if (status != f.Status) { f.Status = status; changed = true; }
        }

        if (changed) await db.SaveChangesAsync(ct);
    }
}
