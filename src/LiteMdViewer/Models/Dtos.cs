namespace LiteMdViewer.Models;

// ---- requests ----
public record AddFileRequest(string Path, int? FolderId);
public record AddFolderFilesRequest(string Path, int? FolderId);
public record NewFileRequest(string Dir, string Name, int? FolderId);
public record MoveFileRequest(string Dir, string? NewName = null);
public record PatchFileRequest(string? Title, int? FolderId, bool MoveToRoot = false, int? SortOrder = null);
public record CreateFolderRequest(string Name, int? ParentId);
public record PatchFolderRequest(string? Name, int? ParentId, bool MoveToRoot = false, int? SortOrder = null);
public record SaveContentRequest(string Text);
public record SettingRequest(string? Value);
public record CreateNoteRequest(string Kind, string FrontText, string BackText, double X, double Y);
public record PatchNoteRequest(string? FrontText, string? BackText, double? X, double? Y, int? Z);
public record CreateDocNoteRequest(string Text);
public record PatchDocNoteRequest(string? Text, int? SortOrder);
public record CreateNoteReferenceRequest(int StartOffset, int Length, string Text);
public record PatchDocNoteGroupRequest(double? X, double? Y, int? Z);

// ---- responses ----
public record FileDto(
    int Id, string Title, string FullPath, int? FolderId, int SortOrder, bool Missing);

public record FolderDto(int Id, string Name, int? ParentId, int SortOrder);

public record TreeDto(IEnumerable<FolderDto> Folders, IEnumerable<FileDto> Files);

public record AddFolderFilesResult(int Added, int Skipped, IEnumerable<FileDto> Files);

public record BrowseEntry(string Name, string Path, bool IsDir, bool IsMarkdown, bool Accessible);
public record BrowseResult(string? Path, string? Parent, bool IsRoot, IEnumerable<BrowseEntry> Entries);

// OnDisk is false when Text was pulled from the DB mirror because the file is gone; the
// viewer then shows the "from database" warning strip and locks editing (ReadOnly).
public record ContentDto(
    int Id, string Title, string FullPath, string Text, bool OnDisk, bool ReadOnly);

public record NoteDto(
    int Id, string Kind, string FrontText, string BackText, double X, double Y, int Z);

public record NoteReferenceDto(int Id, int DocumentNoteId, int StartOffset, int Length, string Text);

// A markdown note attached to a document (shown in the file page's notes panel).
public record DocNoteDto(int Id, int FileId, string Text, int SortOrder, IReadOnlyList<NoteReferenceDto> References);

// A document's note cluster as placed on the dashboard board: the file's title/missing
// state, the group card's position (X/Y/Z), and the notes it holds.
public record DocNoteGroupDto(
    int FileId, string Title, bool Missing, double X, double Y, int Z,
    IEnumerable<DocNoteDto> Notes);

public record NoteReferenceListDto(IEnumerable<NoteReferenceDto> References);

public record FileDetailsDto(
    int Id, string Title, string FullPath,
    DateTime? CreatedUtc, DateTime? ModifiedUtc, bool Exists);

// ---- relations ----
public record AddRelationRequest(int OtherId, string Kind); // kind: parent|child|sibling|companion
public record RelationNodeDto(int Id, string Title, bool Missing, string? Path = null);
public record RelationEdgeDto(int FromId, int ToId, string Kind);
public record GraphDto(
    int ActiveId,
    IEnumerable<RelationNodeDto> Nodes,
    IEnumerable<RelationEdgeDto> Edges,
    IEnumerable<RelationNodeDto> Companions);

// ---- color maps (graph node border colors) ----
public record AddColorMapRequest(string Path);
public record ColorLegendDto(string Color, string Meaning);
public record ColorFileDto(string FilePath, string Color);
public record ColorMapDto(
    int Id, string ListName, string FilePath,
    IEnumerable<ColorLegendDto> Legend, IEnumerable<ColorFileDto> Files);

// ---- attachments (graph exports, uploads, file references) ----
public record ExportRequest(string IndexHtml);
public record AddAttachmentReferenceRequest(string Path);
public record AttachmentDto(
    int Id, string FileName, long SizeBytes, int NodeCount, DateTime CreatedUtc,
    string Kind, bool Missing);
