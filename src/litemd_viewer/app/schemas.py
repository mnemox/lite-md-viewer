"""Request and response models.

The existing frontend is unchanged, so the wire format must stay exactly what ASP.NET Core
produced: camelCase in both directions. Every model therefore uses a camelCase alias
generator, and `populate_by_name` keeps the snake_case names usable from Python.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Camel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# ---------------------------------------------------------------- requests
class AddFileRequest(Camel):
    path: str | None = None
    folder_id: int | None = None


class AddFolderFilesRequest(Camel):
    path: str | None = None
    folder_id: int | None = None


class NewFileRequest(Camel):
    dir: str | None = None
    name: str | None = None
    folder_id: int | None = None


class MoveFileRequest(Camel):
    dir: str | None = None
    new_name: str | None = None


class PatchFileRequest(Camel):
    title: str | None = None
    folder_id: int | None = None
    move_to_root: bool = False
    sort_order: int | None = None


class CreateFolderRequest(Camel):
    name: str | None = None
    parent_id: int | None = None


class PatchFolderRequest(Camel):
    name: str | None = None
    parent_id: int | None = None
    move_to_root: bool = False
    sort_order: int | None = None


class SaveContentRequest(Camel):
    text: str | None = None


class SettingRequest(Camel):
    value: str | None = None


class CreateNoteRequest(Camel):
    kind: str = "note"
    front_text: str = ""
    back_text: str = ""
    x: float = 0.0
    y: float = 0.0
    width: int | None = None
    height: int | None = None


class PatchNoteRequest(Camel):
    front_text: str | None = None
    back_text: str | None = None
    x: float | None = None
    y: float | None = None
    z: int | None = None
    width: int | None = None
    height: int | None = None


class CreateDocNoteRequest(Camel):
    text: str | None = None


class PatchDocNoteRequest(Camel):
    text: str | None = None
    sort_order: int | None = None


class CreateNoteReferenceRequest(Camel):
    start_offset: int = 0
    length: int = 0
    text: str = ""


class PatchDocNoteGroupRequest(Camel):
    x: float | None = None
    y: float | None = None
    z: int | None = None
    width: int | None = None
    height: int | None = None


class AddRelationRequest(Camel):
    other_id: int
    kind: str


class AddColorMapRequest(Camel):
    path: str | None = None


class ExportRequest(Camel):
    index_html: str | None = None


class AddAttachmentReferenceRequest(Camel):
    path: str | None = None


# --------------------------------------------------------------- responses
class FileDto(Camel):
    id: int
    title: str
    full_path: str
    folder_id: int | None
    sort_order: int
    missing: bool


class FolderDto(Camel):
    id: int
    name: str
    parent_id: int | None
    sort_order: int


class TreeDto(Camel):
    folders: list[FolderDto]
    files: list[FileDto]


class AddFolderFilesResult(Camel):
    added: int
    skipped: int
    files: list[FileDto]


class BrowseEntry(Camel):
    name: str
    path: str
    is_dir: bool
    is_markdown: bool
    accessible: bool


class BrowseResult(Camel):
    path: str | None
    parent: str | None
    is_root: bool
    entries: list[BrowseEntry]


class ContentDto(Camel):
    id: int
    title: str
    full_path: str
    text: str
    # False when the text came from the DB mirror because the file is gone; the viewer then
    # shows its "from database" strip and locks editing.
    on_disk: bool
    read_only: bool


class FileDetailsDto(Camel):
    id: int
    title: str
    full_path: str
    created_utc: datetime | None
    modified_utc: datetime | None
    exists: bool


class NoteDto(Camel):
    id: int
    kind: str
    front_text: str
    back_text: str
    x: float
    y: float
    z: int
    width: int | None = None
    height: int | None = None


class NoteReferenceDto(Camel):
    id: int
    document_note_id: int
    start_offset: int
    length: int
    text: str


class DocNoteDto(Camel):
    id: int
    file_id: int
    text: str
    sort_order: int
    references: list[NoteReferenceDto]


class DocNoteGroupDto(Camel):
    file_id: int
    title: str
    missing: bool
    x: float
    y: float
    z: int
    width: int | None = None
    height: int | None = None
    notes: list[DocNoteDto]


# ---- relations ----
class RelationNodeDto(Camel):
    id: int
    title: str
    missing: bool
    path: str | None = None


class RelationEdgeDto(Camel):
    from_id: int
    to_id: int
    kind: str


class GraphDto(Camel):
    active_id: int
    nodes: list[RelationNodeDto]
    edges: list[RelationEdgeDto]
    companions: list[RelationNodeDto]


# ---- color maps ----
class ColorLegendDto(Camel):
    color: str
    meaning: str


class ColorFileDto(Camel):
    file_path: str
    color: str


class ColorMapDto(Camel):
    id: int
    list_name: str
    file_path: str
    legend: list[ColorLegendDto]
    files: list[ColorFileDto]


# ---- attachments ----
class AttachmentDto(Camel):
    id: int
    file_name: str
    size_bytes: int
    node_count: int
    created_utc: datetime
    kind: str
    missing: bool


# ---- search (new in the Python port) ----
class SearchHitDto(Camel):
    """One match. The chunk fields describe its best-scoring passage.

    A hit is a whole document for an unscoped search, and one section of a document for a
    search scoped to a single file. When `note_kind` is set the hit is a dashboard or
    document note rather than a file.
    """

    file_id: int | None = None
    title: str
    full_path: str
    missing: bool
    chunk_id: int
    chunk_index: int
    start_offset: int
    snippet: str
    score: float
    # How many passages of the document (or of the section) matched, this one included.
    passage_count: int = 1
    # The heading trail enclosing the passage, outermost first; empty above the first
    # heading. Only filled in by a search scoped to one document.
    section_path: list[str] = []
    # Set only for note hits: the note id and its kind ('dashboard' or 'document').
    note_id: int | None = None
    note_kind: str | None = None


class SearchResultDto(Camel):
    query: str
    mode: str
    hits: list[SearchHitDto]


class IndexStatusDto(Camel):
    enabled: bool
    model: str | None
    dimension: int
    indexed_files: int
    indexed_notes: int
    indexed_chunks: int
    indexed_note_chunks: int
    pending_files: int
    pending_notes: int
    ready: bool
    last_error: str | None = None


# ---- local analysis (Ollama chat over the open document) ----
class AiStatusDto(Camel):
    enabled: bool
    reachable: bool
    model: str | None
    model_present: bool
    error: str | None = None


class AiSourceDto(Camel):
    """One section used to answer, mirroring what the search panel already shows."""

    section_path: list[str] = []
    start_offset: int
    snippet: str


class AiMessageDto(Camel):
    id: int
    role: str
    text: str
    sources: list[AiSourceDto] = []
    created_utc: datetime


class AskRequest(Camel):
    question: str
