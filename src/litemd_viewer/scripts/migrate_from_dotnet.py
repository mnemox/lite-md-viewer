"""One-shot migration from the .NET EF Core database into the Python schema.

Copies every row out of the old PascalCase tables into their snake_case counterparts,
preserving primary keys so that all cross-table references (graph edges, note references,
attachments) stay intact. Also copies the stored attachment payloads.

This has already been run against the original database, and the .NET project it read from
has since been removed from the repository. It is kept for re-importing from a retained
copy of the old database, which now has to be pointed at explicitly.

Usage, from the repository root:

    .venv\\Scripts\\python src\\litemd_viewer\\scripts\\migrate_from_dotnet.py \\
        --source path\\to\\old\\litemdviewer.db --dry-run

Drop --dry-run to write. The target database is backed up first, and the migration refuses
to run over existing documents unless --force is given.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Allow running the script directly, without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Attachment,
    Base,
    DashboardNote,
    DocumentNote,
    DocumentNoteGroup,
    DocumentNoteReference,
    FileContent,
    Folder,
    Graph,
    GraphColorMap,
    GraphCompanion,
    GraphEdge,
    GraphMember,
    ManagedFile,
    Setting,
)

# The original location, before the .NET project was removed. Retained so an older
# checkout still works without arguments; otherwise pass --source.
DEFAULT_SOURCE = config.CONTENT_ROOT.parent / "LiteMdViewer" / "litemdviewer.db"
DEFAULT_SOURCE_ATTACHMENTS = config.CONTENT_ROOT.parent / "LiteMdViewer" / "attachments"


def parse_dt(value) -> datetime | None:
    """Parse an EF Core SQLite timestamp.

    .NET writes DateTime with 7 fractional digits ("2026-06-30 11:22:14.9415045"), which
    datetime.fromisoformat rejects, so the fraction is truncated to microseconds. Values
    are naive UTC on both sides, matching DateTime.UtcNow's stored form.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]

    if "." in text:
        head, _, frac = text.partition(".")
        digits = ""
        for ch in frac:
            if ch.isdigit():
                digits += ch
            else:
                break  # stop at a timezone offset such as "+00:00"
        text = f"{head}.{digits[:6].ljust(6, '0')}" if digits else head

    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def read_rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not table_exists(conn, table):
        return []
    return conn.execute(f"SELECT * FROM [{table}]").fetchall()


def col(row: sqlite3.Row, name: str, default=None):
    """Read a column that may be absent in an older revision of the source schema."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def migrate(source: Path, dry_run: bool) -> dict[str, int]:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    counts: dict[str, int] = {}

    try:
        with SessionLocal() as session:
            # ---- folders ----
            rows = read_rows(src, "Folders")
            for r in rows:
                session.add(Folder(
                    id=r["Id"],
                    name=col(r, "Name", ""),
                    parent_id=col(r, "ParentId"),
                    sort_order=col(r, "SortOrder", 0),
                    created_utc=parse_dt(col(r, "CreatedUtc")) or datetime.min,
                ))
            counts["folders"] = len(rows)

            # ---- managed files ----
            rows = read_rows(src, "Files")
            for r in rows:
                session.add(ManagedFile(
                    id=r["Id"],
                    full_path=col(r, "FullPath", ""),
                    title=col(r, "Title", ""),
                    folder_id=col(r, "FolderId"),
                    sort_order=col(r, "SortOrder", 0),
                    last_write_utc=parse_dt(col(r, "LastWriteUtc")),
                    added_utc=parse_dt(col(r, "AddedUtc")) or datetime.min,
                    last_opened_utc=parse_dt(col(r, "LastOpenedUtc")),
                ))
            counts["managed_files"] = len(rows)

            # ---- content mirror ----
            rows = read_rows(src, "FileContents")
            for r in rows:
                session.add(FileContent(
                    file_id=r["FileId"],
                    content=col(r, "Content", ""),
                    content_hash=col(r, "ContentHash", ""),
                    source_write_utc=parse_dt(col(r, "SourceWriteUtc")) or datetime.min,
                    synced_utc=parse_dt(col(r, "SyncedUtc")) or datetime.min,
                ))
            counts["file_contents"] = len(rows)

            # ---- graphs ----
            rows = read_rows(src, "Graphs")
            for r in rows:
                session.add(Graph(
                    id=r["Id"],
                    created_utc=parse_dt(col(r, "CreatedUtc")) or datetime.min,
                ))
            counts["graphs"] = len(rows)

            rows = read_rows(src, "GraphMembers")
            seen_members: set[int] = set()
            kept = 0
            for r in rows:
                if r["FileId"] in seen_members:
                    continue  # a file belongs to at most one graph
                seen_members.add(r["FileId"])
                session.add(GraphMember(
                    id=r["Id"], graph_id=r["GraphId"], file_id=r["FileId"]
                ))
                kept += 1
            counts["graph_members"] = kept

            rows = read_rows(src, "GraphEdges")
            seen_edges: set[tuple] = set()
            kept = 0
            for r in rows:
                key = (r["FromId"], r["ToId"], col(r, "Kind", "reference"))
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                session.add(GraphEdge(
                    id=r["Id"], graph_id=r["GraphId"],
                    from_id=r["FromId"], to_id=r["ToId"],
                    kind=col(r, "Kind", "reference"),
                ))
                kept += 1
            counts["graph_edges"] = kept

            rows = read_rows(src, "GraphCompanions")
            seen_companions: set[tuple] = set()
            kept = 0
            for r in rows:
                key = (r["GraphId"], r["FileId"])
                if key in seen_companions:
                    continue
                seen_companions.add(key)
                session.add(GraphCompanion(
                    id=r["Id"], graph_id=r["GraphId"], file_id=r["FileId"]
                ))
                kept += 1
            counts["graph_companions"] = kept

            rows = read_rows(src, "GraphColorMaps")
            for r in rows:
                session.add(GraphColorMap(
                    id=r["Id"],
                    graph_id=r["GraphId"],
                    file_path=col(r, "FilePath", ""),
                    list_name=col(r, "ListName", ""),
                    json=col(r, "Json", ""),
                    created_utc=parse_dt(col(r, "CreatedUtc")) or datetime.min,
                ))
            counts["graph_color_maps"] = len(rows)

            # ---- attachments ----
            rows = read_rows(src, "Attachments")
            for r in rows:
                session.add(Attachment(
                    id=r["Id"],
                    graph_id=r["GraphId"],
                    kind=col(r, "Kind", "export"),
                    file_name=col(r, "FileName", ""),
                    stored_name=col(r, "StoredName", ""),
                    source_path=col(r, "SourcePath"),
                    size_bytes=col(r, "SizeBytes", 0),
                    node_count=col(r, "NodeCount", 0),
                    created_utc=parse_dt(col(r, "CreatedUtc")) or datetime.min,
                ))
            counts["attachments"] = len(rows)

            # ---- settings ----
            rows = read_rows(src, "Settings")
            for r in rows:
                session.merge(Setting(key=r["Key"], value=col(r, "Value")))
            counts["settings"] = len(rows)

            # ---- dashboard notes ----
            rows = read_rows(src, "DashboardNotes")
            for r in rows:
                session.add(DashboardNote(
                    id=r["Id"],
                    kind=col(r, "Kind", "note"),
                    front_text=col(r, "FrontText", ""),
                    back_text=col(r, "BackText", ""),
                    x=col(r, "X", 0.0),
                    y=col(r, "Y", 0.0),
                    z=col(r, "Z", 0),
                    created_utc=parse_dt(col(r, "CreatedUtc")) or datetime.min,
                    updated_utc=parse_dt(col(r, "UpdatedUtc")) or datetime.min,
                ))
            counts["dashboard_notes"] = len(rows)

            # ---- document notes ----
            rows = read_rows(src, "DocumentNotes")
            note_ids = set()
            for r in rows:
                note_ids.add(r["Id"])
                session.add(DocumentNote(
                    id=r["Id"],
                    file_id=r["FileId"],
                    text=col(r, "Text", ""),
                    sort_order=col(r, "SortOrder", 0),
                    created_utc=parse_dt(col(r, "CreatedUtc")) or datetime.min,
                    updated_utc=parse_dt(col(r, "UpdatedUtc")) or datetime.min,
                ))
            counts["document_notes"] = len(rows)

            rows = read_rows(src, "DocumentNoteReferences")
            seen_refs: set[tuple] = set()
            kept = 0
            for r in rows:
                if r["DocumentNoteId"] not in note_ids:
                    continue  # orphan; the FK would reject it
                key = (
                    r["DocumentNoteId"], col(r, "StartOffset", 0),
                    col(r, "Length", 0), col(r, "Text", ""),
                )
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                session.add(DocumentNoteReference(
                    id=r["Id"],
                    document_note_id=r["DocumentNoteId"],
                    start_offset=col(r, "StartOffset", 0),
                    length=col(r, "Length", 0),
                    text=col(r, "Text", ""),
                    created_utc=parse_dt(col(r, "CreatedUtc")) or datetime.min,
                ))
                kept += 1
            counts["document_note_references"] = kept

            rows = read_rows(src, "DocumentNoteGroups")
            seen_groups: set[int] = set()
            kept = 0
            for r in rows:
                if r["FileId"] in seen_groups:
                    continue
                seen_groups.add(r["FileId"])
                session.add(DocumentNoteGroup(
                    id=r["Id"], file_id=r["FileId"],
                    x=col(r, "X", 0.0), y=col(r, "Y", 0.0), z=col(r, "Z", 0),
                ))
                kept += 1
            counts["document_note_groups"] = kept

            if dry_run:
                session.rollback()
            else:
                session.commit()
    finally:
        src.close()

    return counts


def copy_attachments(source_dir: Path, dry_run: bool) -> int:
    if not source_dir.is_dir():
        return 0
    copied = 0
    for item in source_dir.iterdir():
        if not item.is_file():
            continue
        target = config.ATTACHMENTS_DIR / item.name
        if target.exists():
            continue
        if not dry_run:
            shutil.copy2(item, target)
        copied += 1
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="path to the .NET litemdviewer.db")
    parser.add_argument("--attachments", type=Path, default=DEFAULT_SOURCE_ATTACHMENTS,
                        help="path to the .NET attachments directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be copied, then roll back")
    parser.add_argument("--force", action="store_true",
                        help="migrate even though the target already holds documents")
    args = parser.parse_args()

    source: Path = args.source.resolve()
    if not source.is_file():
        print(f"Source database not found: {source}")
        return 1

    config.ensure_dirs()

    # Never migrate on top of live data by accident.
    target_existed = config.DB_PATH.exists()
    if target_existed and not args.dry_run:
        backup = config.DB_PATH.with_suffix(
            f".backup-{datetime.now():%Y%m%d-%H%M%S}.db"
        )
        shutil.copy2(config.DB_PATH, backup)
        print(f"Backed up existing target to {backup.name}")

    Base.metadata.create_all(engine)

    with SessionLocal() as session:
        existing = session.query(ManagedFile).count()
    if existing and not args.force:
        print(f"Target already has {existing} managed files. Re-run with --force to "
              f"migrate anyway (the target was backed up).")
        return 1

    print(f"Source : {source}")
    print(f"Target : {config.DB_PATH}")
    print()

    counts = migrate(source, args.dry_run)
    attachments = copy_attachments(args.attachments.resolve(), args.dry_run)

    width = max(len(k) for k in counts)
    for table, count in counts.items():
        print(f"  {table.ljust(width)}  {count:>5}")
    print(f"  {'attachment files'.ljust(width)}  {attachments:>5}")
    print()
    print("Dry run - nothing was written." if args.dry_run else "Migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
