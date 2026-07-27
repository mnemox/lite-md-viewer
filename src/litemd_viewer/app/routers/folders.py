"""Drawer folders: virtual grouping that never touches the filesystem."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import bad_request, not_found
from ..models import Folder, ManagedFile
from ..schemas import CreateFolderRequest, FolderDto, PatchFolderRequest

router = APIRouter(prefix="/api/folders", tags=["folders"])


def to_dto(folder: Folder) -> FolderDto:
    return FolderDto(
        id=folder.id, name=folder.name,
        parent_id=folder.parent_id, sort_order=folder.sort_order,
    )


@router.get("", response_model=list[FolderDto])
def list_folders(session: Session = Depends(get_session)) -> list[FolderDto]:
    folders = session.scalars(
        select(Folder).order_by(Folder.sort_order, Folder.name)
    ).all()
    return [to_dto(f) for f in folders]


@router.post("", response_model=FolderDto)
def create_folder(
    req: CreateFolderRequest, session: Session = Depends(get_session)
) -> FolderDto:
    if not req.name or not req.name.strip():
        raise bad_request("A folder name is required.")

    existing = session.scalars(select(Folder)).all()
    folder = Folder(
        name=req.name.strip(),
        parent_id=req.parent_id,
        sort_order=max((f.sort_order for f in existing), default=0) + 1,
    )
    session.add(folder)
    session.commit()
    return to_dto(folder)


@router.patch("/{folder_id}", response_model=FolderDto)
def patch_folder(
    folder_id: int, req: PatchFolderRequest, session: Session = Depends(get_session)
) -> FolderDto:
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise not_found()

    if req.name is not None:
        folder.name = req.name.strip()

    if req.move_to_root:
        folder.parent_id = None
    elif req.parent_id is not None:
        if req.parent_id == folder_id:
            raise bad_request("A folder cannot be its own parent.")
        if _creates_cycle(session, folder_id, req.parent_id):
            raise bad_request("That move would create a cycle.")
        folder.parent_id = req.parent_id

    if req.sort_order is not None:
        folder.sort_order = req.sort_order

    session.commit()
    return to_dto(folder)


@router.delete("/{folder_id}", status_code=204)
def delete_folder(folder_id: int, session: Session = Depends(get_session)) -> Response:
    """Delete a folder, reparenting its children up to its own parent (or the root)."""
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise not_found()

    for child in session.scalars(
        select(Folder).where(Folder.parent_id == folder_id)
    ).all():
        child.parent_id = folder.parent_id
    for file in session.scalars(
        select(ManagedFile).where(ManagedFile.folder_id == folder_id)
    ).all():
        file.folder_id = folder.parent_id

    session.delete(folder)
    session.commit()
    return Response(status_code=204)


def _creates_cycle(session: Session, folder_id: int, new_parent_id: int) -> bool:
    """Walk up from the proposed parent; meeting the folder itself means a cycle."""
    current = session.get(Folder, new_parent_id)
    guard = 0
    while current is not None and guard < 1000:
        guard += 1
        if current.id == folder_id:
            return True
        if current.parent_id is None:
            break
        current = session.get(Folder, current.parent_id)
    return False
