using LiteMdViewer.Services;

namespace LiteMdViewer.Endpoints;

public static class BrowseEndpoints
{
    public static void MapBrowse(this WebApplication app)
    {
        app.MapGet("/api/browse", (string? path, FsBrowser browser) =>
            Results.Ok(browser.Browse(path)));
    }
}
