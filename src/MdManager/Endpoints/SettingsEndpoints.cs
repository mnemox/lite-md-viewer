using Microsoft.EntityFrameworkCore;
using MdManager.Data;
using MdManager.Models;

namespace MdManager.Endpoints;

public static class SettingsEndpoints
{
    public static void MapSettings(this WebApplication app)
    {
        app.MapGet("/api/settings", async (AppDbContext db) =>
            Results.Ok(await db.Settings.ToDictionaryAsync(s => s.Key, s => s.Value)));

        app.MapPut("/api/settings/{key}", async (string key, SettingRequest req, AppDbContext db) =>
        {
            var s = await db.Settings.FindAsync(key);
            if (s is null) db.Settings.Add(new Setting { Key = key, Value = req.Value });
            else s.Value = req.Value;
            await db.SaveChangesAsync();
            return Results.Ok(new { key, value = req.Value });
        });
    }
}
