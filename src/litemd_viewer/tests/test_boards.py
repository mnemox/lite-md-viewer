"""Contract + behaviour tests for board lists and their cards.

The frontend reads camelCase keys straight off these responses (see wwwroot/js/board-lists.js),
and every card drag persists through the two `reorder` endpoints, so the move-across-lists
path is exercised here the same way the UI drives it.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import BoardCard, BoardList


def _board(client, name: str = "Project") -> int:
    return client.post("/api/boards", json={"name": name}).json()["id"]


def _list(client, board_id: int, name: str = "To do") -> dict:
    return client.post(f"/api/boards/{board_id}/lists", json={"name": name}).json()


def _card(client, board_id: int, list_id: int, text: str = "A task") -> dict:
    return client.post(
        f"/api/boards/{board_id}/lists/{list_id}/cards", json={"text": text}
    ).json()


# ------------------------------------------------------------------------- lists
def test_new_board_has_no_lists(client):
    board_id = _board(client)
    assert client.get(f"/api/boards/{board_id}/lists").json() == []


def test_create_list_returns_camel_case_dto(client):
    board_id = _board(client)
    body = client.post(f"/api/boards/{board_id}/lists", json={"name": "To do"})
    assert body.status_code == 200
    dto = body.json()
    assert set(dto) == {
        "id", "boardId", "name", "sortOrder", "cards", "createdUtc", "updatedUtc"
    }
    assert dto["boardId"] == board_id
    assert dto["name"] == "To do"
    assert dto["cards"] == []


def test_lists_come_back_in_sort_order(client):
    board_id = _board(client)
    first = _list(client, board_id, "First")
    second = _list(client, board_id, "Second")

    names = [l["name"] for l in client.get(f"/api/boards/{board_id}/lists").json()]
    assert names == ["First", "Second"]
    assert first["sortOrder"] < second["sortOrder"]


def test_reordering_lists_flips_their_order(client):
    board_id = _board(client)
    a = _list(client, board_id, "A")
    b = _list(client, board_id, "B")

    resp = client.put(
        f"/api/boards/{board_id}/lists/reorder", json={"orderedIds": [b["id"], a["id"]]}
    )
    assert resp.status_code == 204
    assert resp.content == b""

    names = [l["name"] for l in client.get(f"/api/boards/{board_id}/lists").json()]
    assert names == ["B", "A"]


def test_rename_list(client):
    board_id = _board(client)
    lst = _list(client, board_id, "Old")
    renamed = client.patch(
        f"/api/boards/{board_id}/lists/{lst['id']}", json={"name": "New"}
    ).json()
    assert renamed["name"] == "New"


def test_delete_list_returns_204(client):
    board_id = _board(client)
    lst = _list(client, board_id)
    assert client.delete(f"/api/boards/{board_id}/lists/{lst['id']}").status_code == 204
    assert client.get(f"/api/boards/{board_id}/lists").json() == []


# ------------------------------------------------------------------------- cards
def test_create_card_returns_camel_case_dto(client):
    board_id = _board(client)
    lst = _list(client, board_id)
    resp = client.post(
        f"/api/boards/{board_id}/lists/{lst['id']}/cards", json={"text": "Write docs"}
    )
    assert resp.status_code == 200
    dto = resp.json()
    assert set(dto) == {
        "id", "listId", "text", "sortOrder", "createdUtc", "updatedUtc"
    }
    assert dto["listId"] == lst["id"]
    assert dto["text"] == "Write docs"


def test_cards_are_nested_in_the_list(client):
    board_id = _board(client)
    lst = _list(client, board_id)
    _card(client, board_id, lst["id"], "one")
    _card(client, board_id, lst["id"], "two")

    lists = client.get(f"/api/boards/{board_id}/lists").json()
    texts = [c["text"] for c in lists[0]["cards"]]
    assert texts == ["one", "two"]


def test_edit_card_text(client):
    board_id = _board(client)
    lst = _list(client, board_id)
    card = _card(client, board_id, lst["id"], "draft")
    edited = client.patch(
        f"/api/boards/{board_id}/lists/{lst['id']}/cards/{card['id']}",
        json={"text": "final"},
    ).json()
    assert edited["text"] == "final"


def test_moving_a_card_between_lists_via_reorder(client):
    board_id = _board(client)
    list_a = _list(client, board_id, "A")
    list_b = _list(client, board_id, "B")
    card = _card(client, board_id, list_a["id"], "movable")

    # The UI sends list B's full new order (the moved card included) to B's reorder endpoint.
    resp = client.put(
        f"/api/boards/{board_id}/lists/{list_b['id']}/cards/reorder",
        json={"orderedIds": [card["id"]]},
    )
    assert resp.status_code == 204

    lists = {l["name"]: l for l in client.get(f"/api/boards/{board_id}/lists").json()}
    assert [c["id"] for c in lists["A"]["cards"]] == []
    assert [c["id"] for c in lists["B"]["cards"]] == [card["id"]]
    assert lists["B"]["cards"][0]["listId"] == list_b["id"]


def test_delete_card_returns_204(client):
    board_id = _board(client)
    lst = _list(client, board_id)
    card = _card(client, board_id, lst["id"])
    assert client.delete(
        f"/api/boards/{board_id}/lists/{lst['id']}/cards/{card['id']}"
    ).status_code == 204
    assert client.get(f"/api/boards/{board_id}/lists").json()[0]["cards"] == []


# ------------------------------------------------------------------------- cascade
def test_deleting_a_board_removes_its_lists_and_cards(client, session):
    board_id = _board(client)
    lst = _list(client, board_id)
    _card(client, board_id, lst["id"])
    _card(client, board_id, lst["id"])

    assert client.delete(f"/api/boards/{board_id}").status_code == 204

    assert session.scalars(select(BoardList)).all() == []
    assert session.scalars(select(BoardCard)).all() == []


# --------------------------------------------------------------------------- 404s
def test_list_under_a_missing_board_is_404(client):
    assert client.get("/api/boards/999999/lists").status_code == 404
    assert client.post("/api/boards/999999/lists", json={"name": "x"}).status_code == 404


def test_card_under_a_list_not_in_the_board_is_404(client):
    board_a = _board(client, "A")
    board_b = _board(client, "B")
    list_a = _list(client, board_a, "list-a")
    card = _card(client, board_a, list_a["id"])

    # The list belongs to board A, so addressing it under board B must not resolve.
    wrong = client.delete(
        f"/api/boards/{board_b}/lists/{list_a['id']}/cards/{card['id']}"
    )
    assert wrong.status_code == 404


def test_card_reorder_rejects_a_card_from_another_board(client):
    board_a = _board(client, "A")
    board_b = _board(client, "B")
    list_a = _list(client, board_a, "list-a")
    list_b = _list(client, board_b, "list-b")
    card = _card(client, board_a, list_a["id"])

    # Trying to pull board A's card into board B's list must be refused.
    resp = client.put(
        f"/api/boards/{board_b}/lists/{list_b['id']}/cards/reorder",
        json={"orderedIds": [card["id"]]},
    )
    assert resp.status_code == 404
