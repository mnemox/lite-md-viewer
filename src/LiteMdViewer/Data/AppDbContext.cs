using Microsoft.EntityFrameworkCore;
using LiteMdViewer.Models;

namespace LiteMdViewer.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<Folder> Folders => Set<Folder>();
    public DbSet<ManagedFile> Files => Set<ManagedFile>();
    public DbSet<FileContent> FileContents => Set<FileContent>();
    public DbSet<Graph> Graphs => Set<Graph>();
    public DbSet<GraphMember> GraphMembers => Set<GraphMember>();
    public DbSet<GraphEdge> GraphEdges => Set<GraphEdge>();
    public DbSet<GraphCompanion> GraphCompanions => Set<GraphCompanion>();
    public DbSet<GraphColorMap> GraphColorMaps => Set<GraphColorMap>();
    public DbSet<Attachment> Attachments => Set<Attachment>();
    public DbSet<Setting> Settings => Set<Setting>();
    public DbSet<DashboardNote> DashboardNotes => Set<DashboardNote>();
    public DbSet<DocumentNote> DocumentNotes => Set<DocumentNote>();
    public DbSet<DocumentNoteGroup> DocumentNoteGroups => Set<DocumentNoteGroup>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<ManagedFile>().HasIndex(f => f.FullPath).IsUnique();
        b.Entity<DocumentNote>().HasIndex(n => n.FileId);
        b.Entity<DocumentNoteGroup>().HasIndex(g => g.FileId).IsUnique();
        b.Entity<FileContent>().HasKey(c => c.FileId);
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
