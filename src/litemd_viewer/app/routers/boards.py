"""Boards API: a free-positioned card per board on the boards screen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import not_found
from ..models import Board, utcnow
from ..schemas import BoardDto, CreateBoardRequest, PatchBoardRequest

router = APIRouter(prefix="/api/boards", tags=["boards"])


def to_dto(board: Board) -> BoardDto:
    return BoardDto(
        id=board.id,
        name=board.name,
        color=board.color,
        x=board.x,
        y=board.y,
        z=board.z,
        created_utc=board.created_utc,
        updated_utc=board.updated_utc,
    )


@router.get("", response_model=list[BoardDto])
def list_boards(session: Session = Depends(get_session)) -> list[BoardDto]:
    boards = session.scalars(select(Board).order_by(Board.z, Board.id)).all()
    return [to_dto(b) for b in boards]


@router.post("", response_model=BoardDto)
def create_board(
    req: CreateBoardRequest, session: Session = Depends(get_session)
) -> BoardDto:
    existing = session.scalars(select(Board)).all()
    now = utcnow()
    board = Board(
        name=req.name or "New board",
        color=req.color,
        x=req.x,
        y=req.y,
        z=max((b.z for b in existing), default=0) + 1,
        created_utc=now,
        updated_utc=now,
    )
    session.add(board)
    session.commit()
    session.refresh(board)
    return to_dto(board)


@router.get("/{board_id}", response_model=BoardDto)
def get_board(board_id: int, session: Session = Depends(get_session)) -> BoardDto:
    board = session.get(Board, board_id)
    if board is None:
        raise not_found()
    return to_dto(board)


@router.patch("/{board_id}", response_model=BoardDto)
def patch_board(
    board_id: int, req: PatchBoardRequest, session: Session = Depends(get_session)
) -> BoardDto:
    board = session.get(Board, board_id)
    if board is None:
        raise not_found()
    if req.name is not None:
        board.name = req.name
    if req.color is not None:
        board.color = req.color
    if req.x is not None:
        board.x = req.x
    if req.y is not None:
        board.y = req.y
    if req.z is not None:
        board.z = req.z
    board.updated_utc = utcnow()
    session.commit()
    return to_dto(board)


@router.delete("/{board_id}", status_code=204)
def delete_board(
    board_id: int, session: Session = Depends(get_session)
) -> Response:
    board = session.get(Board, board_id)
    if board is None:
        raise not_found()
    session.delete(board)
    session.commit()
    return Response(status_code=204)
