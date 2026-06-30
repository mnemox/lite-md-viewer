namespace LiteMdViewer.Models;

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
    public int SortOrder { get; set; }
    public DateTime? LastWriteUtc { get; set; }
    public DateTime AddedUtc { get; set; } = DateTime.UtcNow;
    public DateTime? LastOpenedUtc { get; set; }
}

/// <summary>Key/value application settings (theme, startup flags, last paths).</summary>
public class Setting
{
    public string Key { get; set; } = "";
    public string? Value { get; set; }
}
