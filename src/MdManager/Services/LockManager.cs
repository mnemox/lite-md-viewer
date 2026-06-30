using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Principal;

namespace MdManager.Services;

/// <summary>
/// Single owner of the on-disk delete-lock. The lock is an explicit NTFS
/// "Deny Delete" ACE for the current user on the file itself. It blocks
/// Explorer delete/rename/move (rename needs delete), persists across app
/// restarts and reboots, and does NOT block content writes (so in-app editing
/// keeps working). Removing the ACE unlocks the file. Windows + NTFS only.
/// </summary>
public sealed class LockManager
{
    private readonly ILogger<LockManager> _log;
    public LockManager(ILogger<LockManager> log) => _log = log;

    public bool PlatformSupported => OperatingSystem.IsWindows();

    public bool IsNtfs(string path)
    {
        try
        {
            var root = Path.GetPathRoot(Path.GetFullPath(path));
            if (string.IsNullOrEmpty(root)) return false;
            return new DriveInfo(root).DriveFormat.Equals("NTFS", StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    /// <summary>True when this volume can hold the delete-lock.</summary>
    public bool CanLock(string path) => PlatformSupported && IsNtfs(path);

    public void Lock(string path)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("The delete-lock requires Windows on an NTFS volume.");
        ApplyDenyDelete(path, add: true);
    }

    public void Unlock(string path)
    {
        if (!OperatingSystem.IsWindows() || !File.Exists(path)) return;
        try { ApplyDenyDelete(path, add: false); }
        catch (Exception ex) { _log.LogWarning(ex, "Unlock failed for {Path}", path); }
    }

    public bool HasLock(string path)
    {
        if (!OperatingSystem.IsWindows() || !File.Exists(path)) return false;
        try
        {
            var sec = new FileInfo(path).GetAccessControl();
            var sid = WindowsIdentity.GetCurrent().User!;
            foreach (FileSystemAccessRule rule in sec.GetAccessRules(true, false, typeof(SecurityIdentifier)))
            {
                if (rule.AccessControlType == AccessControlType.Deny
                    && rule.IdentityReference.Equals(sid)
                    && (rule.FileSystemRights & FileSystemRights.Delete) == FileSystemRights.Delete)
                    return true;
            }
        }
        catch (Exception ex) { _log.LogWarning(ex, "HasLock check failed for {Path}", path); }
        return false;
    }

    [SupportedOSPlatform("windows")]
    private static void ApplyDenyDelete(string path, bool add)
    {
        var fi = new FileInfo(path);
        var sec = fi.GetAccessControl();                       // System.IO.FileSystem.AccessControl
        var sid = WindowsIdentity.GetCurrent().User!;          // current user SID
        // Deny Delete on the FILE itself overrides both the file's Allow-Delete and
        // the parent's DELETE_CHILD for this principal (Deny is evaluated first).
        var rule = new FileSystemAccessRule(sid, FileSystemRights.Delete, AccessControlType.Deny);
        sec.ModifyAccessRule(add ? AccessControlModification.Add : AccessControlModification.Remove, rule, out _);
        fi.SetAccessControl(sec);                              // persists to the NTFS security descriptor
    }
}
