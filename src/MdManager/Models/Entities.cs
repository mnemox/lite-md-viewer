namespace MdManager.Models;

/// <summary>A grouping folder for managed-file links. Folders form an arbitrary-depth tree.</summary>
public class Folder
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public int? ParentId { get; set; }   // null => root-level folder
    public int SortOrder { get; set; }
    public DateTime CreatedUtc { get; set; } = DateTime.UtcNow;
}

/// <summary>A real on-disk markdown file placed under management.</summary>
public class ManagedFile
{
    public int Id { get; set; }
    public string FullPath { get; set; } = "";          // canonical absolute path; unique
    public string Title { get; set; } = "";             // editable display name (never renames the file)
    public int? FolderId { get; set; }                  // null => top level
    public bool IsLockRequested { get; set; } = true;   // user intent: should this be locked
    public string Status { get; set; } = FileStatus.Unlocked;
    public int SortOrder { get; set; }
    public DateTime? LastWriteUtc { get; set; }
    public DateTime AddedUtc { get; set; } = DateTime.UtcNow;
    public DateTime? LastOpenedUtc { get; set; }
}

public static class FileStatus
{
    public const string Locked = "locked";
    public const string Unlocked = "unlocked";
    public const string Missing = "missing";
}

/// <summary>Key/value application settings (theme, startup flags, last paths).</summary>
public class Setting
{
    public string Key { get; set; } = "";
    public string? Value { get; set; }
}
