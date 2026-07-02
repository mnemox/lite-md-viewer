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
/// An explicit graph: a connected group of documents that owns the references/siblings
/// between them, the companion documents associated with it, and the export attachments.
/// A document belongs to at most one graph (via <see cref="GraphMember"/>).
/// </summary>
public class Graph
{
    public int Id { get; set; }
    public DateTime CreatedUtc { get; set; } = DateTime.UtcNow;
}

/// <summary>Links a document to its graph (the document → graph table).</summary>
public class GraphMember
{
    public int Id { get; set; }
    public int GraphId { get; set; }
    public int FileId { get; set; }   // unique: a document is a member of at most one graph
}

/// <summary>
/// A typed edge between two member documents of a graph.
///  - "reference": directed; <c>FromId</c> = parent/referencing, <c>ToId</c> = child/referenced.
///  - "sibling":   undirected same-level peer (stored canonical FromId &lt;= ToId).
/// </summary>
public class GraphEdge
{
    public int Id { get; set; }
    public int GraphId { get; set; }
    public int FromId { get; set; }
    public int ToId { get; set; }
    public string Kind { get; set; } = "";
}

public static class GraphEdgeKind
{
    public const string Reference = "reference";
    public const string Sibling = "sibling";
}

/// <summary>A companion document associated with a graph (a "see also", not a graph node).</summary>
public class GraphCompanion
{
    public int Id { get; set; }
    public int GraphId { get; set; }
    public int FileId { get; set; }
}

/// <summary>
/// An imported colors-map schema associated with a graph: a JSON file mapping document
/// file paths to node border colors (plus a legend). Applied on demand to recolor the
/// graph's node borders. The parsed schema is stored as JSON so the source file need not
/// stay reachable.
/// </summary>
public class GraphColorMap
{
    public int Id { get; set; }
    public int GraphId { get; set; }
    public string FilePath { get; set; } = "";   // source JSON file path (as imported)
    public string ListName { get; set; } = "";   // schema listName (display label)
    public string Json { get; set; } = "";        // normalized schema { legend, files } as JSON
    public DateTime CreatedUtc { get; set; } = DateTime.UtcNow;
}

/// <summary>
/// A downloadable file owned by a graph. Three kinds:
///  - "export":    a generated zip of the graph (copied .md files + index.html), stored under attachments/.
///  - "upload":    a user-uploaded file copied into attachments/ (deleting removes the physical file).
///  - "reference": a pointer to an existing file elsewhere on disk (deleting removes only the record).
/// </summary>
public class Attachment
{
    public int Id { get; set; }
    public int GraphId { get; set; }                // the graph this attachment belongs to
    public string Kind { get; set; } = AttachmentKind.Export;
    public string FileName { get; set; } = "";      // display/download name, e.g. "30-06-2026_18-04-UTC.zip"
    public string StoredName { get; set; } = "";    // on-disk name under attachments/ (export/upload); "" for references
    public string? SourcePath { get; set; }         // absolute path of the referenced file (reference kind only)
    public long SizeBytes { get; set; }
    public int NodeCount { get; set; }              // documents in the exported graph (export kind only)
    public DateTime CreatedUtc { get; set; } = DateTime.UtcNow;
}

public static class AttachmentKind
{
    public const string Export = "export";
    public const string Upload = "upload";
    public const string Reference = "reference";
}

/// <summary>Key/value application settings (theme, startup flags, last paths).</summary>
public class Setting
{
    public string Key { get; set; } = "";
    public string? Value { get; set; }
}
