using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Models;

namespace LiteMdViewer.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<Folder> Folders => Set<Folder>();
    public DbSet<ManagedFile> Files => Set<ManagedFile>();
    public DbSet<Graph> Graphs => Set<Graph>();
    public DbSet<GraphMember> GraphMembers => Set<GraphMember>();
    public DbSet<GraphEdge> GraphEdges => Set<GraphEdge>();
    public DbSet<GraphCompanion> GraphCompanions => Set<GraphCompanion>();
    public DbSet<GraphColorMap> GraphColorMaps => Set<GraphColorMap>();
    public DbSet<Attachment> Attachments => Set<Attachment>();
    public DbSet<Setting> Settings => Set<Setting>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<ManagedFile>().HasIndex(f => f.FullPath).IsUnique();
        b.Entity<GraphMember>().HasIndex(m => m.FileId).IsUnique();
        b.Entity<GraphMember>().HasIndex(m => m.GraphId);
        b.Entity<GraphEdge>().HasIndex(e => new { e.FromId, e.ToId, e.Kind }).IsUnique();
        b.Entity<GraphEdge>().HasIndex(e => e.GraphId);
        b.Entity<GraphCompanion>().HasIndex(c => new { c.GraphId, c.FileId }).IsUnique();
        b.Entity<GraphCompanion>().HasIndex(c => c.GraphId);
        b.Entity<GraphColorMap>().HasIndex(c => c.GraphId);
        b.Entity<Attachment>().HasIndex(a => a.GraphId);
        b.Entity<Setting>().HasKey(s => s.Key);
    }
}
