namespace LiteMdViewer.Models;

// ---- requests ----
public record AddFileRequest(string Path, int? FolderId);
public record AddFolderFilesRequest(string Path, int? FolderId);
public record NewFileRequest(string Dir, string Name, int? FolderId);
public record PatchFileRequest(string? Title, int? FolderId, bool MoveToRoot = false, int? SortOrder = null);
public record CreateFolderRequest(string Name, int? ParentId);
public record PatchFolderRequest(string? Name, int? ParentId, bool MoveToRoot = false, int? SortOrder = null);
public record SaveContentRequest(string Text);
public record SettingRequest(string? Value);

// ---- responses ----
public record FileDto(
    int Id, string Title, string FullPath, int? FolderId, int SortOrder, bool Missing);

public record FolderDto(int Id, string Name, int? ParentId, int SortOrder);

public record TreeDto(IEnumerable<FolderDto> Folders, IEnumerable<FileDto> Files);

public record AddFolderFilesResult(int Added, int Skipped, IEnumerable<FileDto> Files);

public record BrowseEntry(string Name, string Path, bool IsDir, bool IsMarkdown, bool Accessible);
public record BrowseResult(string? Path, string? Parent, bool IsRoot, IEnumerable<BrowseEntry> Entries);

public record ContentDto(int Id, string Title, string FullPath, string Text);

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

// ---- attachments (graph exports) ----
public record ExportRequest(string IndexHtml);
public record AttachmentDto(
    int Id, string FileName, long SizeBytes, int NodeCount, DateTime CreatedUtc);
