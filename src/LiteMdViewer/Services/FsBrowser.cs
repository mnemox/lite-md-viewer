using LiteMdViewer.Models;

namespace LiteMdViewer.Services;

/// <summary>
/// Read-only server-side filesystem enumeration for the "Add file" picker.
/// The browser sandbox can't hand JS a real absolute path, so the user navigates
/// the real disk through this endpoint and we register the chosen absolute path.
/// </summary>
public sealed class FsBrowser
{
    private static readonly string[] MarkdownExt = { ".md", ".markdown" };
    private static readonly string[] JsonExt = { ".json" };

    public BrowseResult Browse(string? path, string? kind = null)
    {
        // kind: null/'md' → markdown files; 'json' → .json files; 'any' → every file
        var exts = string.Equals(kind, "json", StringComparison.OrdinalIgnoreCase) ? JsonExt
            : string.Equals(kind, "any", StringComparison.OrdinalIgnoreCase) ? null
            : MarkdownExt;

        if (string.IsNullOrWhiteSpace(path))
            return ListDrives();

        string full;
        try { full = Path.GetFullPath(path); }
        catch { return ListDrives(); }

        if (!Directory.Exists(full))
            return ListDrives();

        var entries = new List<BrowseEntry>();
        try
        {
            foreach (var dir in Directory.EnumerateDirectories(full))
            {
                if (IsHiddenOrSystem(dir)) continue;
                entries.Add(new BrowseEntry(Path.GetFileName(dir), dir, true, false, IsAccessible(dir)));
            }
            foreach (var file in Directory.EnumerateFiles(full))
            {
                var ext = Path.GetExtension(file);
                if (exts is not null && !exts.Contains(ext, StringComparer.OrdinalIgnoreCase)) continue;
                entries.Add(new BrowseEntry(Path.GetFileName(file), file, false, true, true));
            }
        }
        catch (UnauthorizedAccessException) { }
        catch (IOException) { }

        var parent = Directory.GetParent(full)?.FullName;
        var ordered = entries
            .OrderByDescending(e => e.IsDir)
            .ThenBy(e => e.Name, StringComparer.OrdinalIgnoreCase)
            .ToList();
        return new BrowseResult(full, parent, false, ordered);
    }

    private static BrowseResult ListDrives()
    {
        var entries = DriveInfo.GetDrives()
            .Where(d => d.IsReady)
            .Select(d => new BrowseEntry(d.Name, d.RootDirectory.FullName, true, false, true))
            .ToList();
        return new BrowseResult(null, null, true, entries);
    }

    private static bool IsHiddenOrSystem(string dir)
    {
        try
        {
            var attr = File.GetAttributes(dir);
            return (attr & (FileAttributes.Hidden | FileAttributes.System)) != 0;
        }
        catch { return true; }
    }

    private static bool IsAccessible(string dir)
    {
        try { _ = Directory.EnumerateFileSystemEntries(dir).Any(); return true; }
        catch { return false; }
    }
}
