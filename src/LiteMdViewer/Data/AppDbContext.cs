using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Models;

namespace LiteMdViewer.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<Folder> Folders => Set<Folder>();
    public DbSet<ManagedFile> Files => Set<ManagedFile>();
    public DbSet<Setting> Settings => Set<Setting>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<ManagedFile>().HasIndex(f => f.FullPath).IsUnique();
        b.Entity<Setting>().HasKey(s => s.Key);
    }
}
