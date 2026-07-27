"""The document drawer: the tree, and adding/patching/moving/removing managed files."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import config
from ..db import get_session
from ..errors import bad_request, conflict, not_found, problem
from ..models import (
    Attachment,
    AttachmentKind,
    DocumentNote,
    DocumentNoteGroup,
    DocumentNoteReference,
    FileContent,
    Folder,
    ManagedFile,
    utcnow,
)
from ..schemas import (
    AddFileRequest,
    AddFolderFilesRequest,
    AddFolderFilesResult,
    FileDto,
    FolderDto,
    MoveFileRequest,
    NewFileRequest,
    PatchFileRequest,
    TreeDto,
)
from ..services import platform_fs
from ..services.graph_service import GraphService
from ..services.indexing import get_indexer

router = APIRouter(prefix="/api", tags=["files"])


def to_dto(file: ManagedFile) -> FileDto:
    return FileDto(
        id=file.id,
        title=file.title,
        full_path=file.full_path,
        folder_id=file.folder_id,
        sort_order=file.sort_order,
        missing=not platform_fs.exists(file.full_path),
    )


def _max_sort_order(session: Session) -> int:
    return max(
        (f.sort_order for f in session.scalars(select(ManagedFile)).all()),
        default=0,
    )


@router.get("/tree", response_model=TreeDto)
def get_tree(session: Session = Depends(get_session)) -> TreeDto:
    """The whole drawer. Missing-on-disk is computed on read, never stored."""
    folders = session.scalars(
        select(Folder).order_by(Folder.sort_order, Folder.name)
    ).all()
    files = session.scalars(
        select(ManagedFile).order_by(ManagedFile.sort_order, ManagedFile.title)
    ).all()
    return TreeDto(
        folders=[
            FolderDto(id=f.id, name=f.name, parent_id=f.parent_id, sort_order=f.sort_order)
            for f in folders
        ],
        files=[to_dto(f) for f in files],
    )


@router.post("/files", response_model=FileDto)
def add_file(
    req: AddFileRequest, session: Session = Depends(get_session)
) -> FileDto:
    """Register a real on-disk path under management."""
    if not req.path or not req.path.strip():
        raise bad_request("A path is required.")

    try:
        full = platform_fs.canonical(req.path)
    except (OSError, ValueError):
        raise bad_request("Invalid path.")

    if not config.is_supported(full):
        raise bad_request(config.UNSUPPORTED_MESSAGE)
    if not platform_fs.exists(full):
        raise bad_request("File does not exist.")

    existing = session.scalars(select(ManagedFile)).all()
    for candidate in existing:
        if platform_fs.same_path(candidate.full_path, full):
            # The client opens the existing document instead of showing an error.
            raise conflict("This file is already managed.", id=candidate.id)

    file = ManagedFile(
        full_path=full,
        title=os.path.splitext(os.path.basename(full))[0],
        folder_id=req.folder_id,
        sort_order=max((f.sort_order for f in existing), default=0) + 1,
        last_write_utc=platform_fs.mtime_utc(full),
        added_utc=utcnow(),
    )
    session.add(file)
    session.commit()

    get_indexer().enqueue(file.id)
    return to_dto(file)


@router.post("/files/folder", response_model=AddFolderFilesResult)
def add_folder_files(
    req: AddFolderFilesRequest, session: Session = Depends(get_session)
) -> AddFolderFilesResult:
    """Add every top-level document file from a disk folder, skipping managed ones."""
    if not req.path or not req.path.strip():
        raise bad_request("A folder path is required.")

    try:
        full = platform_fs.canonical(req.path)
    except (OSError, ValueError):
        raise bad_request("Invalid path.")

    if not os.path.isdir(full):
        raise bad_request("Folder does not exist.")

    try:
        candidates = sorted(
            (entry.path for entry in os.scandir(full)
             if entry.is_file() and config.is_supported(entry.name)),
            key=str.lower,
        )
    except OSError as exc:
        raise problem(f"Could not read folder: {exc}")

    existing = session.scalars(select(ManagedFile)).all()
    known = {platform_fs.path_key(f.full_path) for f in existing}
    sort_order = max((f.sort_order for f in existing), default=0)

    added: list[ManagedFile] = []
    skipped = 0
    for path in candidates:
        canon = platform_fs.canonical(path)
        key = platform_fs.path_key(canon)
        if key in known:
            skipped += 1
            continue
        known.add(key)
        sort_order += 1
        file = ManagedFile(
            full_path=canon,
            title=os.path.splitext(os.path.basename(canon))[0],
            folder_id=req.folder_id,
            sort_order=sort_order,
            last_write_utc=platform_fs.mtime_utc(canon),
            added_utc=utcnow(),
        )
        session.add(file)
        added.append(file)

    if added:
        session.commit()
        indexer = get_indexer()
        for file in added:
            indexer.enqueue(file.id)

    return AddFolderFilesResult(
        added=len(added), skipped=skipped, files=[to_dto(f) for f in added]
    )


@router.post("/files/new", response_model=FileDto)
def create_file(
    req: NewFileRequest, session: Session = Depends(get_session)
) -> FileDto:
    """Create a brand-new document on disk in a chosen folder, then manage it."""
    if not req.dir or not req.dir.strip() or not req.name or not req.name.strip():
        raise bad_request("A folder and a file name are required.")
    if not os.path.isdir(req.dir):
        raise bad_request("Target folder does not exist.")

    name = req.name.strip()
    if not platform_fs.is_valid_filename(name):
        raise bad_request("Invalid file name.")

    if not os.path.splitext(name)[1]:
        name += config.DEFAULT_EXT
    if not config.is_supported(name):
        raise bad_request(config.UNSUPPORTED_MESSAGE)

    try:
        full = platform_fs.canonical(os.path.join(req.dir, name))
    except (OSError, ValueError):
        raise bad_request("Invalid path.")

    existing = session.scalars(select(ManagedFile)).all()
    for candidate in existing:
        if platform_fs.same_path(candidate.full_path, full):
            raise conflict("This file is already managed.", id=candidate.id)
    if platform_fs.exists(full):
        raise conflict("A file with that name already exists in this folder.")

    title = os.path.splitext(os.path.basename(full))[0]
    try:
        platform_fs.write_text(full, f"# {title}\n")
    except OSError as exc:
        raise problem(f"Could not create file: {exc}")

    file = ManagedFile(
        full_path=full,
        title=title,
        folder_id=req.folder_id,
        sort_order=max((f.sort_order for f in existing), default=0) + 1,
        last_write_utc=platform_fs.mtime_utc(full),
        added_utc=utcnow(),
    )
    session.add(file)
    session.commit()

    get_indexer().enqueue(file.id)
    return to_dto(file)


@router.patch("/files/{file_id}", response_model=FileDto)
def patch_file(
    file_id: int, req: PatchFileRequest, session: Session = Depends(get_session)
) -> FileDto:
    """Edit display title / move between drawer folders / reorder. Never touches the disk."""
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()

    retitled = req.title is not None and req.title.strip() != file.title
    if req.title is not None:
        file.title = req.title.strip()
    if req.move_to_root:
        file.folder_id = None
    elif req.folder_id is not None:
        file.folder_id = req.folder_id
    if req.sort_order is not None:
        file.sort_order = req.sort_order

    session.commit()

    if retitled:
        # The title is embedded into every one of this document's passages, so a rename makes
        # them stale. Forced, because the file on disk -- and so the mirror hash the indexer
        # normally watches -- has not changed.
        get_indexer().enqueue(file.id, force=True)

    return to_dto(file)


@router.post("/files/{file_id}/move", response_model=FileDto)
def move_file(
    file_id: int, req: MoveFileRequest, session: Session = Depends(get_session)
) -> FileDto:
    """Move the real file to another physical folder and update its stored path.

    The database id is preserved, so graph links, notes and vectors all stay attached.
    """
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()

    if not req.dir or not req.dir.strip():
        raise bad_request("A target folder is required.")
    if not os.path.isdir(req.dir):
        raise bad_request("Target folder does not exist.")

    name = req.new_name.strip() if req.new_name and req.new_name.strip() \
        else os.path.basename(file.full_path)
    if not config.is_supported(name):
        raise bad_request(config.UNSUPPORTED_MESSAGE)

    try:
        target = platform_fs.canonical(os.path.join(req.dir, name))
    except (OSError, ValueError):
        raise bad_request("Invalid path.")

    old_path = file.full_path
    if platform_fs.same_path(target, old_path):
        return to_dto(file)  # already there

    if not platform_fs.exists(old_path):
        raise bad_request("The file no longer exists on disk.")

    for candidate in session.scalars(select(ManagedFile)).all():
        if candidate.id != file_id and platform_fs.same_path(candidate.full_path, target):
            raise conflict("Another managed file already lives at that path.", id=candidate.id)
    if platform_fs.exists(target):
        raise conflict("A file with that name already exists in the target folder.")

    try:
        os.replace(old_path, target)
    except OSError as exc:
        raise problem(f"Could not move file: {exc}")

    file.full_path = target
    file.last_write_utc = platform_fs.mtime_utc(target)

    # Repoint any reference-attachments that named the old absolute path.
    for attachment in session.scalars(
        select(Attachment).where(
            Attachment.kind == AttachmentKind.REFERENCE,
            Attachment.source_path.is_not(None),
        )
    ).all():
        if platform_fs.same_path(attachment.source_path, old_path):
            attachment.source_path = target

    session.commit()
    return to_dto(file)


@router.delete("/files/{file_id}", status_code=204)
def unmanage_file(file_id: int, session: Session = Depends(get_session)) -> Response:
    """Remove from management only. The file on disk is left untouched."""
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()

    _purge_file_rows(session, file_id)
    session.delete(file)
    session.commit()

    get_indexer().remove_file(file_id)
    return Response(status_code=204)


@router.delete("/files/{file_id}/disk", status_code=204)
def delete_file_from_disk(
    file_id: int, session: Session = Depends(get_session)
) -> Response:
    """Delete the real file from disk and drop it from management."""
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()

    try:
        if platform_fs.exists(file.full_path):
            os.remove(file.full_path)
    except OSError as exc:
        raise problem(f"Could not delete file: {exc}")

    _purge_file_rows(session, file_id)
    session.delete(file)
    session.commit()

    get_indexer().remove_file(file_id)
    return Response(status_code=204)


def _purge_file_rows(session: Session, file_id: int) -> None:
    """Drop everything owned by a document that is being unmanaged or deleted.

    The content mirror goes too: it exists to survive an *external* deletion, so removing a
    document deliberately through the app should not leave a copy behind. Notes and their
    dashboard cluster are removed so nothing orphaned lingers on the board.
    """
    GraphService(session).remove_file_everywhere(file_id)

    mirror = session.get(FileContent, file_id)
    if mirror is not None:
        session.delete(mirror)

    note_ids = list(session.scalars(
        select(DocumentNote.id).where(DocumentNote.file_id == file_id)
    ).all())
    if note_ids:
        session.execute(
            delete(DocumentNoteReference).where(
                DocumentNoteReference.document_note_id.in_(note_ids)
            )
        )
        session.execute(delete(DocumentNote).where(DocumentNote.file_id == file_id))

    session.execute(
        delete(DocumentNoteGroup).where(DocumentNoteGroup.file_id == file_id)
    )
