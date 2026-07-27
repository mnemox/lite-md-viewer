"""Reading, saving and recreating a document's text."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import conflict, not_found, problem
from ..models import FileContent, ManagedFile, utcnow
from ..schemas import ContentDto, FileDetailsDto, FileDto, SaveContentRequest
from ..services import platform_fs
from ..services.indexing import get_indexer

router = APIRouter(prefix="/api/files", tags=["content"])


@router.get("/{file_id}/details", response_model=FileDetailsDto)
def get_details(file_id: int, session: Session = Depends(get_session)) -> FileDetailsDto:
    """Metadata for the details modal. Timestamps are null when the file is gone."""
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()

    exists = platform_fs.exists(file.full_path)
    return FileDetailsDto(
        id=file.id,
        title=file.title,
        full_path=file.full_path,
        # Offset-aware on purpose: the client formats these with `new Date(...)`, which
        # would otherwise read a bare timestamp as local time and shift it.
        created_utc=platform_fs.created_utc_aware(file.full_path) if exists else None,
        modified_utc=platform_fs.modified_utc_aware(file.full_path) if exists else None,
        exists=exists,
    )


@router.get("/{file_id}/content", response_model=ContentDto)
def get_content(file_id: int, session: Session = Depends(get_session)) -> ContentDto:
    """Raw text for the viewer/editor, falling back to the mirror when the file is gone."""
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()

    if not platform_fs.exists(file.full_path):
        mirror = session.get(FileContent, file_id)
        if mirror is None:
            raise not_found("File is missing on disk.")
        # The viewer shows its "from database" strip and locks editing for this response.
        return ContentDto(
            id=file.id, title=file.title, full_path=file.full_path,
            text=mirror.content, on_disk=False, read_only=True,
        )

    text = platform_fs.read_text_tolerant(file.full_path)
    file.last_opened_utc = utcnow()
    session.commit()

    return ContentDto(
        id=file.id, title=file.title, full_path=file.full_path,
        text=text, on_disk=True, read_only=False,
    )


@router.put("/{file_id}/content")
def save_content(
    file_id: int, req: SaveContentRequest, session: Session = Depends(get_session)
) -> dict:
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()
    if not platform_fs.exists(file.full_path):
        raise conflict("File is missing on disk.")

    try:
        platform_fs.write_text(file.full_path, req.text or "")
    except OSError as exc:
        raise problem(f"Could not save: {exc}")

    file.last_write_utc = platform_fs.mtime_utc(file.full_path)
    session.commit()

    # The watcher would also catch this, but enqueueing directly makes an in-app edit
    # searchable without waiting on filesystem event delivery.
    get_indexer().enqueue(file_id)
    return {"ok": True}


@router.post("/{file_id}/recreate", response_model=FileDto)
def recreate_file(file_id: int, session: Session = Depends(get_session)) -> FileDto:
    """Write the stored mirror back to disk after the file was deleted externally."""
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()
    if platform_fs.exists(file.full_path):
        raise conflict("File already exists on disk.")

    mirror = session.get(FileContent, file_id)
    if mirror is None:
        raise conflict("No stored content to recreate from.")

    try:
        parent = os.path.dirname(file.full_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        platform_fs.write_text(file.full_path, mirror.content)
    except OSError as exc:
        raise problem(f"Could not recreate file: {exc}")

    mtime = platform_fs.mtime_utc(file.full_path)
    file.last_write_utc = mtime
    # Keep the mirror's mtime marker in step so the sync sweep sees this as already synced.
    if mtime is not None:
        mirror.source_write_utc = mtime
    mirror.synced_utc = utcnow()
    session.commit()

    return FileDto(
        id=file.id, title=file.title, full_path=file.full_path,
        folder_id=file.folder_id, sort_order=file.sort_order, missing=False,
    )
