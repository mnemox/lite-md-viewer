"""Dashboard sticky notes: free-positioned markdown cards on the board."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import not_found
from ..models import DashboardNote, DashboardNoteKind, utcnow
from ..schemas import CreateNoteRequest, NoteDto, PatchNoteRequest
from ..services.indexing import get_indexer

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def to_dto(note: DashboardNote) -> NoteDto:
    return NoteDto(
        id=note.id, kind=note.kind,
        front_text=note.front_text, back_text=note.back_text,
        x=note.x, y=note.y, z=note.z,
        width=note.width, height=note.height,
    )


@router.get("/notes", response_model=list[NoteDto])
def list_notes(session: Session = Depends(get_session)) -> list[NoteDto]:
    notes = session.scalars(
        select(DashboardNote).order_by(DashboardNote.z, DashboardNote.id)
    ).all()
    return [to_dto(n) for n in notes]


@router.get("/notes/{note_id}", response_model=NoteDto)
def get_note(note_id: int, session: Session = Depends(get_session)) -> NoteDto:
    note = session.get(DashboardNote, note_id)
    if note is None:
        raise not_found()
    return to_dto(note)


@router.post("/notes", response_model=NoteDto)
def create_note(
    req: CreateNoteRequest, session: Session = Depends(get_session)
) -> NoteDto:
    kind = (DashboardNoteKind.FLIP if req.kind == DashboardNoteKind.FLIP
            else DashboardNoteKind.NOTE)
    existing = session.scalars(select(DashboardNote)).all()
    now = utcnow()

    note = DashboardNote(
        kind=kind,
        front_text=req.front_text or "",
        back_text=req.back_text or "",
        x=req.x,
        y=req.y,
        z=max((n.z for n in existing), default=0) + 1,
        width=req.width,
        height=req.height,
        created_utc=now,
        updated_utc=now,
    )
    session.add(note)
    session.commit()
    session.refresh(note)
    get_indexer().enqueue_note(note.id, "dashboard", delay=0.0)
    return to_dto(note)


@router.patch("/notes/{note_id}", response_model=NoteDto)
def patch_note(
    note_id: int, req: PatchNoteRequest, session: Session = Depends(get_session)
) -> NoteDto:
    """Partial update: only the supplied fields change (text, position or stacking)."""
    note = session.get(DashboardNote, note_id)
    if note is None:
        raise not_found()

    if req.front_text is not None:
        note.front_text = req.front_text
    if req.back_text is not None:
        note.back_text = req.back_text
    if req.x is not None:
        note.x = req.x
    if req.y is not None:
        note.y = req.y
    if req.z is not None:
        note.z = req.z
    if req.width is not None:
        note.width = req.width
    if req.height is not None:
        note.height = req.height
    note.updated_utc = utcnow()

    session.commit()
    get_indexer().enqueue_note(note.id, "dashboard", delay=0.0)
    return to_dto(note)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, session: Session = Depends(get_session)) -> Response:
    note = session.get(DashboardNote, note_id)
    if note is None:
        raise not_found()
    session.delete(note)
    session.commit()
    get_indexer().remove_note(note_id, "dashboard")
    return Response(status_code=204)
