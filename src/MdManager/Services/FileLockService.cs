using Microsoft.EntityFrameworkCore;
using MdManager.Data;
using MdManager.Models;

namespace MdManager.Services;

/// <summary>
/// Long-running hosted service. On startup it reconciles on-disk ACLs with the
/// DB's stored intent: every file marked <c>IsLockRequested</c> gets its
/// Deny-Delete ACE (re)applied, every unlocked file has it removed, and missing
/// paths are flagged. This is what makes "deletable only after unlocking in-app"
/// hold even across restarts.
/// </summary>
public sealed class FileLockService : IHostedService
{
    private readonly IServiceScopeFactory _scopeFactory;
    private readonly LockManager _locks;
    private readonly ILogger<FileLockService> _log;

    public FileLockService(IServiceScopeFactory scopeFactory, LockManager locks, ILogger<FileLockService> log)
    {
        _scopeFactory = scopeFactory;
        _locks = locks;
        _log = log;
    }

    public async Task StartAsync(CancellationToken ct)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var files = await db.Files.ToListAsync(ct);

        foreach (var f in files)
        {
            try
            {
                if (!File.Exists(f.FullPath)) { f.Status = FileStatus.Missing; continue; }
                f.LastWriteUtc = File.GetLastWriteTimeUtc(f.FullPath);

                if (f.IsLockRequested && _locks.CanLock(f.FullPath))
                {
                    _locks.Lock(f.FullPath);
                    f.Status = FileStatus.Locked;
                }
                else
                {
                    _locks.Unlock(f.FullPath);
                    f.Status = FileStatus.Unlocked;
                    if (f.IsLockRequested && !_locks.CanLock(f.FullPath))
                        f.IsLockRequested = false; // can't lock on this volume
                }
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Lock reconcile failed for {Path}", f.FullPath);
                f.Status = FileStatus.Unlocked;
            }
        }

        await db.SaveChangesAsync(ct);
        _log.LogInformation("Lock reconcile complete for {Count} managed file(s).", files.Count);
    }

    public Task StopAsync(CancellationToken ct) => Task.CompletedTask;
}
