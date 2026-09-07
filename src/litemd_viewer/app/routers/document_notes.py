"""Per-document notes, their highlight references, and the dashboard clusters they form.

Each note belongs to a managed file and appears in the file page's notes panel. A document's
notes also surface on the dashboard as one titled cluster whose board position lives in
`document_note_groups` -- created with the first note, removed with the last.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import bad_request, not_found
from ..models import (
    DocumentNote,
    DocumentNoteGroup,
    DocumentNoteReference,
    ManagedFile,
    utcnow,
)
from ..schemas import (
    CreateDocNoteRequest,
    CreateNoteReferenceRequest,
    DocNoteDto,
    DocNoteGroupDto,
    NoteReferenceDto,
    PatchDocNoteGroupRequest,
    PatchDocNoteRequest,
)
from ..services import platform_fs
from ..services.indexing import get_indexer

router = APIRouter(prefix="/api/files/{file_id}/notes", tags=["document-notes"])
groups = APIRouter(prefix="/api/dashboard/document-notes", tags=["document-notes"])

# Cascade successive clusters so they do not land exactly on top of each other.
GROUP_BASE_X = 28.0
GROUP_BASE_Y = 24.0
GROUP_STEP = 26.0


def to_ref_dto(ref: DocumentNoteReference) -> NoteReferenceDto:
    return NoteReferenceDto(
        id=ref.id, document_note_id=ref.document_note_id,
        start_offset=ref.start_offset, length=ref.length, text=ref.text,
    )


def to_dto(note: DocumentNote) -> DocNoteDto:
    return DocNoteDto(
        id=note.id, file_id=note.file_id, text=note.text, sort_order=note.sort_order,
        references=[to_ref_dto(r) for r in sorted(note.references, key=lambda r: r.id)],
    )


# ------------------------------------------------------------------ per-file notes
@router.get("", response_model=list[DocNoteDto])
def list_notes(file_id: int, session: Session = Depends(get_session)) -> list[DocNoteDto]:
    notes = session.scalars(
        select(DocumentNote)
        .where(DocumentNote.file_id == file_id)
        .order_by(DocumentNote.sort_order, DocumentNote.id)
    ).all()
    return [to_dto(n) for n in notes]


@router.get("/{note_id}", response_model=DocNoteDto)
def get_note(file_id: int, note_id: int, session: Session = Depends(get_session)) -> DocNoteDto:
    note = session.scalar(
        select(DocumentNote).where(
            DocumentNote.id == note_id, DocumentNote.file_id == file_id
        )
    )
    if note is None:
        raise not_found()
    return to_dto(note)


@router.post("", response_model=DocNoteDto)
def create_note(
    file_id: int, req: CreateDocNoteRequest, session: Session = Depends(get_session)
) -> DocNoteDto:
    if session.get(ManagedFile, file_id) is None:
        raise not_found()

    # Lazily place the document's cluster card the first time it gets a note.
    has_group = session.scalar(
        select(DocumentNoteGroup.id).where(DocumentNoteGroup.file_id == file_id)
    )
    if has_group is None:
        all_groups = session.scalars(select(DocumentNoteGroup)).all()
        step = (len(all_groups) % 6) * GROUP_STEP
        session.add(DocumentNoteGroup(
            file_id=file_id,
            x=GROUP_BASE_X + step,
            y=GROUP_BASE_Y + step,
            z=max((g.z for g in all_groups), default=0) + 1,
        ))

    siblings = session.scalars(
        select(DocumentNote).where(DocumentNote.file_id == file_id)
    ).all()
    now = utcnow()
    note = DocumentNote(
        file_id=file_id,
        text=req.text or "",
        sort_order=max((n.sort_order for n in siblings), default=0) + 1,
        created_utc=now,
        updated_utc=now,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    get_indexer().enqueue_note(note.id, "document", delay=0.0)
    return to_dto(note)


@router.patch("/{note_id}", response_model=DocNoteDto)
def patch_note(
    file_id: int, note_id: int, req: PatchDocNoteRequest,
    session: Session = Depends(get_session),
) -> DocNoteDto:
    """Partial update: only the supplied fields change."""
    note = session.scalar(
        select(DocumentNote).where(
            DocumentNote.id == note_id, DocumentNote.file_id == file_id
        )
    )
    if note is None:
        raise not_found()

    if req.text is not None:
        note.text = req.text
    if req.sort_order is not None:
        note.sort_order = req.sort_order
    note.updated_utc = utcnow()

    session.commit()
    get_indexer().enqueue_note(note.id, "document", delay=0.0)
    return to_dto(note)


@router.delete("/{note_id}", status_code=204)
def delete_note(
    file_id: int, note_id: int, session: Session = Depends(get_session)
) -> Response:
    note = session.scalar(
        select(DocumentNote).where(
            DocumentNote.id == note_id, DocumentNote.file_id == file_id
        )
    )
    if note is None:
        raise not_found()
    session.delete(note)

    # Drop the cluster card once its last note is gone, so no empty group is shown.
    remaining = session.scalar(
        select(DocumentNote.id).where(
            DocumentNote.file_id == file_id, DocumentNote.id != note_id
        )
    )
    if remaining is None:
        group = session.scalar(
            select(DocumentNoteGroup).where(DocumentNoteGroup.file_id == file_id)
        )
        if group is not None:
            session.delete(group)

    session.commit()
    get_indexer().remove_note(note_id, "document")
    return Response(status_code=204)


# ------------------------------------------------------- note <-> highlight references
@router.get("/{note_id}/references", response_model=list[NoteReferenceDto])
def list_references(
    file_id: int, note_id: int, session: Session = Depends(get_session)
) -> list[NoteReferenceDto]:
    exists = session.scalar(
        select(DocumentNote.id).where(
            DocumentNote.id == note_id, DocumentNote.file_id == file_id
        )
    )
    if exists is None:
        raise not_found()

    refs = session.scalars(
        select(DocumentNoteReference)
        .where(DocumentNoteReference.document_note_id == note_id)
        .order_by(DocumentNoteReference.id)
    ).all()
    return [to_ref_dto(r) for r in refs]


@router.post("/{note_id}/references", response_model=NoteReferenceDto)
def create_reference(
    file_id: int, note_id: int, req: CreateNoteReferenceRequest,
    session: Session = Depends(get_session),
) -> NoteReferenceDto:
    note = session.scalar(
        select(DocumentNote).where(
            DocumentNote.id == note_id, DocumentNote.file_id == file_id
        )
    )
    if note is None:
        raise not_found()
    if not req.text or not req.text.strip():
        raise bad_request("Highlighted text is required.")
    if req.length <= 0:
        raise bad_request("Length must be positive.")

    ref = DocumentNoteReference(
        document_note_id=note_id,
        start_offset=req.start_offset,
        length=req.length,
        text=req.text[:500],
        created_utc=utcnow(),
    )
    session.add(ref)
    session.commit()
    return to_ref_dto(ref)


@router.delete("/{note_id}/references/{ref_id}", status_code=204)
def delete_reference(
    file_id: int, note_id: int, ref_id: int, session: Session = Depends(get_session)
) -> Response:
    note = session.scalar(
        select(DocumentNote).where(
            DocumentNote.id == note_id, DocumentNote.file_id == file_id
        )
    )
    if note is None:
        raise not_found()

    ref = session.scalar(
        select(DocumentNoteReference).where(
            DocumentNoteReference.id == ref_id,
            DocumentNoteReference.document_note_id == note_id,
        )
    )
    if ref is None:
        raise not_found()

    session.delete(ref)
    session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------- dashboard aggregation
@groups.get("", response_model=list[DocNoteGroupDto])
def list_groups(session: Session = Depends(get_session)) -> list[DocNoteGroupDto]:
    group_rows = session.scalars(
        select(DocumentNoteGroup).order_by(DocumentNoteGroup.z, DocumentNoteGroup.id)
    ).all()
    if not group_rows:
        return []

    file_ids = [g.file_id for g in group_rows]
    files = {
        f.id: f for f in session.scalars(
            select(ManagedFile).where(ManagedFile.id.in_(file_ids))
        ).all()
    }
    notes = session.scalars(
        select(DocumentNote)
        .where(DocumentNote.file_id.in_(file_ids))
        .order_by(DocumentNote.sort_order, DocumentNote.id)
    ).all()

    result: list[DocNoteGroupDto] = []
    for group in group_rows:
        file = files.get(group.file_id)
        if file is None:
            continue  # orphan guard
        group_notes = [to_dto(n) for n in notes if n.file_id == group.file_id]
        if not group_notes:
            continue
        result.append(DocNoteGroupDto(
            file_id=group.file_id,
            title=file.title,
            missing=not platform_fs.exists(file.full_path),
            x=group.x, y=group.y, z=group.z,
            notes=group_notes,
        ))
    return result


@groups.patch("/{file_id}", status_code=204)
def patch_group(
    file_id: int, req: PatchDocNoteGroupRequest, session: Session = Depends(get_session)
) -> Response:
    """Persist a cluster card's board position / stacking after a drag."""
    group = session.scalar(
        select(DocumentNoteGroup).where(DocumentNoteGroup.file_id == file_id)
    )
    if group is None:
        raise not_found()

    if req.x is not None:
        group.x = req.x
    if req.y is not None:
        group.y = req.y
    if req.z is not None:
        group.z = req.z

    session.commit()
    return Response(status_code=204)
