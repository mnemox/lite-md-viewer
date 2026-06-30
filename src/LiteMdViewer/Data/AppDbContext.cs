using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Models;

namespace LiteMdViewer.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<Folder> Folders => Set<Folder>();
    public DbSet<ManagedFile> Files => Set<ManagedFile>();
    public DbSet<Relation> Relations => Set<Relation>();
    public DbSet<Attachment> Attachments => Set<Attachment>();
    public DbSet<Setting> Settings => Set<Setting>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<ManagedFile>().HasIndex(f => f.FullPath).IsUnique();
        b.Entity<Relation>().HasIndex(r => new { r.FromId, r.ToId, r.Kind }).IsUnique();
        b.Entity<Attachment>().HasIndex(a => a.FileId);
        b.Entity<Setting>().HasKey(s => s.Key);
    }
}
