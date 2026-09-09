"""SQLAlchemy 2.0 models.

A snake_case port of the .NET EF Core entity model, plus `file_chunks`, which is new: it
gives every embedded passage a stable id that doubles as the vector store's `doc_id`, so a
document can be re-embedded in place without its vectors ever changing identity.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime


def utcnow() -> datetime:
    """Naive UTC, matching how .NET's DateTime.UtcNow was persisted.

    SQLite has no timezone type and silently drops tzinfo, so keeping every stored value
    naive-and-UTC avoids a column that is sometimes aware and sometimes not.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Folder(Base):
    """A grouping folder for managed-file links. Folders form an arbitrary-depth tree."""

    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None => root
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ManagedFile(Base):
    """A real on-disk document placed under management."""

    __tablename__ = "managed_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Canonical absolute path. Unique, and compared case-insensitively everywhere.
    full_path: Mapped[str] = mapped_column(String, unique=True)
    # Editable display name; renaming this never renames the file on disk.
    title: Mapped[str] = mapped_column(String, default="")
    folder_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    last_write_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    added_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_opened_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FileContent(Base):
    """A database mirror of a managed file's on-disk content.

    Deliberately outlives deletion of the file on disk, so the last-known content can still
    be viewed read-only and the file recreated from it. One row per managed file.
    """

    __tablename__ = "file_contents"

    file_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String, default="")   # sha-256 hex
    source_write_utc: Mapped[datetime] = mapped_column(DateTime)    # file mtime at sync time
    synced_utc: Mapped[datetime] = mapped_column(DateTime)


class FileChunk(Base):
    """One embedded passage of a document.

    `id` is also the `doc_id` in the vector store, which is what lets the indexer update a
    passage in place. `content_hash` is compared chunk-by-chunk on re-index so that editing
    one paragraph re-embeds one chunk instead of the whole file. It hashes what was actually
    embedded, which includes the document title, so renaming the document also re-embeds.

    `text` is the cleaned passage without that title prefix: it is what the reader is shown
    as a snippet, so it must stay bare.
    """

    __tablename__ = "file_chunks"
    __table_args__ = (
        UniqueConstraint("file_id", "chunk_index", name="uq_file_chunks_file_index"),
        Index("ix_file_chunks_file_id", "file_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer, default=0)
    end_offset: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String, default="")
    # Set when the passage's vector has been written to the vector store; cleared if the
    # embedding failed so a later sweep can retry.
    embedded_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FileIndexState(Base):
    """What the vector index currently holds for a document.

    `content_hash` is the mirror hash that was last indexed. Comparing it against the
    mirror's current hash on startup identifies documents that changed while the app was
    closed, without having to re-chunk every file to find out.
    """

    __tablename__ = "file_index_state"

    file_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class NoteChunk(Base):
    """One embedded passage of a dashboard or document note.

    `id` is the `doc_id` in the notes vector collection, just as `FileChunk.id` is for the
    document collection. Notes live in their own collection so their ids cannot collide with
    file chunk ids.
    """

    __tablename__ = "note_chunks"
    __table_args__ = (
        Index("ix_note_chunks_note", "note_kind", "note_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_id: Mapped[int] = mapped_column(Integer)
    note_kind: Mapped[str] = mapped_column(String, default="")
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String, default="")
    embedded_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NoteIndexState(Base):
    """What the vector index currently holds for a note.

    `content_hash` is a hash of the note text that was last indexed. Dashboard notes and
    document notes share the same table and are distinguished by `note_kind`.
    """

    __tablename__ = "note_index_state"

    note_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_kind: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class Graph(Base):
    """A connected group of documents owning their edges, companions and attachments."""

    __tablename__ = "graphs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GraphMember(Base):
    """Links a document to its graph. A document is a member of at most one graph."""

    __tablename__ = "graph_members"
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_graph_members_file_id"),
        Index("ix_graph_members_graph_id", "graph_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[int] = mapped_column(Integer)


class GraphEdge(Base):
    """A typed edge between two member documents.

    - "reference": directed; from_id = referencing, to_id = referenced.
    - "sibling":   undirected peer, stored canonically with from_id <= to_id.
    """

    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("from_id", "to_id", "kind", name="uq_graph_edges_from_to_kind"),
        Index("ix_graph_edges_graph_id", "graph_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(Integer)
    from_id: Mapped[int] = mapped_column(Integer)
    to_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)


class GraphEdgeKind:
    REFERENCE = "reference"
    SIBLING = "sibling"


class GraphCompanion(Base):
    """A companion document associated with a graph (a "see also", not a graph node)."""

    __tablename__ = "graph_companions"
    __table_args__ = (
        UniqueConstraint("graph_id", "file_id", name="uq_graph_companions_graph_file"),
        Index("ix_graph_companions_graph_id", "graph_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[int] = mapped_column(Integer)


class GraphColorMap(Base):
    """An imported JSON schema mapping document paths to node border colours."""

    __tablename__ = "graph_color_maps"
    __table_args__ = (Index("ix_graph_color_maps_graph_id", "graph_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(String, default="")   # source JSON, as imported
    list_name: Mapped[str] = mapped_column(String, default="")   # display label
    json: Mapped[str] = mapped_column(Text, default="")          # normalized { legend, files }
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AttachmentKind:
    EXPORT = "export"
    UPLOAD = "upload"
    REFERENCE = "reference"


class Attachment(Base):
    """A downloadable file owned by a graph.

    - "export":    a generated zip of the graph, stored under attachments/.
    - "upload":    a user-uploaded copy under attachments/ (deleting removes the file).
    - "reference": a pointer to a file elsewhere on disk (deleting removes only the record).
    """

    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_graph_id", "graph_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String, default=AttachmentKind.EXPORT)
    file_name: Mapped[str] = mapped_column(String, default="")    # display/download name
    stored_name: Mapped[str] = mapped_column(String, default="")  # "" for references
    source_path: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    node_count: Mapped[int] = mapped_column(Integer, default=0)   # export kind only
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    """Key/value application settings (theme, startup flags)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String, nullable=True)


class DashboardNoteKind:
    NOTE = "note"
    FLIP = "flip"


class DashboardNote(Base):
    """A sticky note placed on the dashboard board ("note" or two-sided "flip")."""

    __tablename__ = "dashboard_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, default=DashboardNoteKind.NOTE)
    front_text: Mapped[str] = mapped_column(Text, default="")
    back_text: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    z: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DocumentNote(Base):
    """A markdown note attached to a specific managed document."""

    __tablename__ = "document_notes"
    __table_args__ = (Index("ix_document_notes_file_id", "file_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    references: Mapped[list["DocumentNoteReference"]] = relationship(
        back_populates="note",
        cascade="all, delete-orphan",
        order_by="DocumentNoteReference.id",
        lazy="selectin",
    )


class DocumentNoteReference(Base):
    """A link between a note and a highlighted passage of the rendered document."""

    __tablename__ = "document_note_references"
    __table_args__ = (
        Index("ix_document_note_references_note_id", "document_note_id"),
        UniqueConstraint(
            "document_note_id", "start_offset", "length", "text",
            name="uq_document_note_references_note_start_length_text",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_note_id: Mapped[int] = mapped_column(
        ForeignKey("document_notes.id", ondelete="CASCADE")
    )
    start_offset: Mapped[int] = mapped_column(Integer, default=0)
    length: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(String(500), default="")
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    note: Mapped[DocumentNote] = relationship(back_populates="references")


class DocumentNoteGroup(Base):
    """The dashboard placement of a document's note cluster. One row per document."""

    __tablename__ = "document_note_groups"
    __table_args__ = (UniqueConstraint("file_id", name="uq_document_note_groups_file_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(Integer)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    z: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Board(Base):
    """A board that can hold tasks. Positioned on the boards screen like a note card."""

    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    z: Mapped[int] = mapped_column(Integer, default=0)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AiChatRole:
    USER = "user"
    ASSISTANT = "assistant"


class AiChatMessage(Base):
    """One turn of the local-analysis chat over a document.

    `sources` is a JSON-encoded list of the sections used to answer (empty when the whole
    document fit in context, or for a user message). Kept as a string rather than a related
    table since a source is a display-only snapshot, not a live reference.
    """

    __tablename__ = "ai_chat_messages"
    __table_args__ = (Index("ix_ai_chat_messages_file_id", "file_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String, default=AiChatRole.USER)
    text: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
