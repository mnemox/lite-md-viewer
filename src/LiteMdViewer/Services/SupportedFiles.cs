namespace LiteMdViewer.Services;

/// <summary>Single source of truth for the document file types the app manages and renders.</summary>
public static class SupportedFiles
{
    // Editable/renderable document extensions (lower-case, incl. dot).
    public static readonly string[] DocumentExt = { ".md", ".markdown", ".xml" };

    // Applied to a bare "new file" name that has no extension.
    public const string DefaultExt = ".md";

    public const string UnsupportedMessage = "Only .md, .markdown, or .xml files are supported.";

    public static bool IsSupported(string path) =>
        DocumentExt.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase);
}
