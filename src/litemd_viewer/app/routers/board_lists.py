"""Board lists and their cards: the Trello-style columns that live inside one board.

A list belongs to a board; a card belongs to a list. Ordering within each level is a dense
`sort_order`. All drags persist through the two `reorder` endpoints: a card move (even across
lists) is a single call that declares the cards a list should now hold, in order.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import not_found
from ..models import Board, BoardCard, BoardList, utcnow
from ..schemas import (
    BoardCardDto,
    BoardListDto,
    CreateBoardCardRequest,
    CreateBoardListRequest,
    PatchBoardCardRequest,
    PatchBoardListRequest,
    ReorderRequest,
)

router = APIRouter(prefix="/api/boards/{board_id}/lists", tags=["board-lists"])


def to_card_dto(card: BoardCard) -> BoardCardDto:
    return BoardCardDto(
        id=card.id,
        list_id=card.list_id,
        text=card.text,
        sort_order=card.sort_order,
        created_utc=card.created_utc,
        updated_utc=card.updated_utc,
    )


def to_list_dto(lst: BoardList) -> BoardListDto:
    return BoardListDto(
        id=lst.id,
        board_id=lst.board_id,
        name=lst.name,
        sort_order=lst.sort_order,
        created_utc=lst.created_utc,
        updated_utc=lst.updated_utc,
        cards=[to_card_dto(c) for c in sorted(lst.cards, key=lambda c: (c.sort_order, c.id))],
    )


def _get_board_or_404(session: Session, board_id: int) -> Board:
    board = session.get(Board, board_id)
    if board is None:
        raise not_found()
    return board


def _get_list_or_404(session: Session, board_id: int, list_id: int) -> BoardList:
    lst = session.scalar(
        select(BoardList).where(
            BoardList.id == list_id, BoardList.board_id == board_id
        )
    )
    if lst is None:
        raise not_found()
    return lst


def _get_card_or_404(
    session: Session, board_id: int, list_id: int, card_id: int
) -> BoardCard:
    card = session.scalar(
        select(BoardCard)
        .join(BoardList, BoardCard.list_id == BoardList.id)
        .where(
            BoardCard.id == card_id,
            BoardCard.list_id == list_id,
            BoardList.board_id == board_id,
        )
    )
    if card is None:
        raise not_found()
    return card


# ------------------------------------------------------------------------- lists
@router.get("", response_model=list[BoardListDto])
def list_lists(board_id: int, session: Session = Depends(get_session)) -> list[BoardListDto]:
    _get_board_or_404(session, board_id)
    lists = session.scalars(
        select(BoardList)
        .where(BoardList.board_id == board_id)
        .order_by(BoardList.sort_order, BoardList.id)
    ).all()
    return [to_list_dto(l) for l in lists]


@router.post("", response_model=BoardListDto)
def create_list(
    board_id: int, req: CreateBoardListRequest, session: Session = Depends(get_session)
) -> BoardListDto:
    _get_board_or_404(session, board_id)
    siblings = session.scalars(
        select(BoardList).where(BoardList.board_id == board_id)
    ).all()
    now = utcnow()
    lst = BoardList(
        board_id=board_id,
        name=req.name or "New list",
        sort_order=max((l.sort_order for l in siblings), default=0) + 1,
        created_utc=now,
        updated_utc=now,
    )
    session.add(lst)
    session.commit()
    session.refresh(lst)
    return to_list_dto(lst)


# `/reorder` is declared before the `/{list_id}` param routes; they use different methods
# so there is no real clash, but literal-before-param keeps the intent obvious.
@router.put("/reorder", status_code=204)
def reorder_lists(
    board_id: int, req: ReorderRequest, session: Session = Depends(get_session)
) -> Response:
    _get_board_or_404(session, board_id)
    now = utcnow()
    for index, list_id in enumerate(req.ordered_ids):
        lst = _get_list_or_404(session, board_id, list_id)
        lst.sort_order = index
        lst.updated_utc = now
    session.commit()
    return Response(status_code=204)


@router.patch("/{list_id}", response_model=BoardListDto)
def patch_list(
    board_id: int,
    list_id: int,
    req: PatchBoardListRequest,
    session: Session = Depends(get_session),
) -> BoardListDto:
    lst = _get_list_or_404(session, board_id, list_id)
    if req.name is not None:
        lst.name = req.name
    if req.sort_order is not None:
        lst.sort_order = req.sort_order
    lst.updated_utc = utcnow()
    session.commit()
    return to_list_dto(lst)


@router.delete("/{list_id}", status_code=204)
def delete_list(
    board_id: int, list_id: int, session: Session = Depends(get_session)
) -> Response:
    lst = _get_list_or_404(session, board_id, list_id)
    session.delete(lst)
    session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------------- cards
@router.post("/{list_id}/cards", response_model=BoardCardDto)
def create_card(
    board_id: int,
    list_id: int,
    req: CreateBoardCardRequest,
    session: Session = Depends(get_session),
) -> BoardCardDto:
    _get_list_or_404(session, board_id, list_id)
    siblings = session.scalars(
        select(BoardCard).where(BoardCard.list_id == list_id)
    ).all()
    now = utcnow()
    card = BoardCard(
        list_id=list_id,
        text=req.text or "",
        sort_order=max((c.sort_order for c in siblings), default=0) + 1,
        created_utc=now,
        updated_utc=now,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    return to_card_dto(card)


# The single persistence call for every card drag. `ordered_ids` is exactly the cards this
# list should hold now, in order -- so a card dragged in from another list of the same board
# is reassigned and positioned in one request.
@router.put("/{list_id}/cards/reorder", status_code=204)
def reorder_cards(
    board_id: int,
    list_id: int,
    req: ReorderRequest,
    session: Session = Depends(get_session),
) -> Response:
    _get_list_or_404(session, board_id, list_id)
    now = utcnow()
    for index, card_id in enumerate(req.ordered_ids):
        card = session.scalar(
            select(BoardCard)
            .join(BoardList, BoardCard.list_id == BoardList.id)
            .where(BoardCard.id == card_id, BoardList.board_id == board_id)
        )
        if card is None:
            raise not_found()
        card.list_id = list_id
        card.sort_order = index
        card.updated_utc = now
    session.commit()
    return Response(status_code=204)


@router.patch("/{list_id}/cards/{card_id}", response_model=BoardCardDto)
def patch_card(
    board_id: int,
    list_id: int,
    card_id: int,
    req: PatchBoardCardRequest,
    session: Session = Depends(get_session),
) -> BoardCardDto:
    card = _get_card_or_404(session, board_id, list_id, card_id)
    if req.text is not None:
        card.text = req.text
    card.updated_utc = utcnow()
    session.commit()
    return to_card_dto(card)


@router.delete("/{list_id}/cards/{card_id}", status_code=204)
def delete_card(
    board_id: int,
    list_id: int,
    card_id: int,
    session: Session = Depends(get_session),
) -> Response:
    card = _get_card_or_404(session, board_id, list_id, card_id)
    session.delete(card)
    session.commit()
    return Response(status_code=204)
