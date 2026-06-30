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

/// <summary>
/// A typed link between two managed files. Kinds:
///  - "reference": directed; <c>FromId</c> = parent/referencing, <c>ToId</c> = child/referenced.
///  - "sibling":   undirected same-level peer (stored canonical FromId &lt;= ToId).
///  - "companion": undirected association, shown as a list (stored canonical FromId &lt;= ToId).
/// </summary>
public class Relation
{
    public int Id { get; set; }
    public int FromId { get; set; }
    public int ToId { get; set; }
    public string Kind { get; set; } = "";
    public DateTime CreatedUtc { get; set; } = DateTime.UtcNow;
}

public static class RelationKind
{
    public const string Reference = "reference";
    public const string Sibling = "sibling";
    public const string Companion = "companion";
}

/// <summary>Key/value application settings (theme, startup flags, last paths).</summary>
public class Setting
{
    public string Key { get; set; } = "";
    public string? Value { get; set; }
}
