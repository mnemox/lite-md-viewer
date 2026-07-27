"""Graph relations, companions, colour maps, attachments and document notes."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import write_doc


def add(client, docs_dir: Path, name: str) -> int:
    path = write_doc(docs_dir, name, f"# {name}\n")
    return client.post("/api/files", json={"path": path}).json()["id"]


# ------------------------------------------------------------------------ graph
def test_a_lone_document_is_its_own_singleton_graph(client, docs_dir: Path):
    file_id = add(client, docs_dir, "solo.md")
    body = client.get(f"/api/files/{file_id}/graph").json()

    assert body["activeId"] == file_id
    assert [n["id"] for n in body["nodes"]] == [file_id]
    assert body["edges"] == []
    assert body["companions"] == []


def test_child_relation_is_directed_and_shared_by_both_documents(client, docs_dir: Path):
    parent = add(client, docs_dir, "parent.md")
    child = add(client, docs_dir, "child.md")

    assert client.post(
        f"/api/files/{parent}/relations", json={"otherId": child, "kind": "child"}
    ).status_code == 204

    body = client.get(f"/api/files/{parent}/graph").json()
    assert {n["id"] for n in body["nodes"]} == {parent, child}
    assert body["edges"] == [{"fromId": parent, "toId": child, "kind": "reference"}]

    # Edges are graph-level, so the other member sees exactly the same set.
    from_child = client.get(f"/api/files/{child}/graph").json()
    assert from_child["edges"] == body["edges"]


def test_parent_relation_is_stored_reversed(client, docs_dir: Path):
    a = add(client, docs_dir, "a.md")
    b = add(client, docs_dir, "b.md")

    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "parent"})

    edges = client.get(f"/api/files/{a}/graph").json()["edges"]
    assert edges == [{"fromId": b, "toId": a, "kind": "reference"}]


def test_sibling_edges_are_stored_canonically(client, docs_dir: Path):
    a = add(client, docs_dir, "s1.md")
    b = add(client, docs_dir, "s2.md")

    # Link in the "wrong" direction; it must still canonicalize to (low, high).
    client.post(f"/api/files/{b}/relations", json={"otherId": a, "kind": "sibling"})

    edges = client.get(f"/api/files/{a}/graph").json()["edges"]
    low, high = sorted((a, b))
    assert edges == [{"fromId": low, "toId": high, "kind": "sibling"}]

    # Adding the mirror image must not create a second edge.
    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "sibling"})
    assert len(client.get(f"/api/files/{a}/graph").json()["edges"]) == 1


def test_linking_two_graphs_merges_them(client, docs_dir: Path):
    a, b = add(client, docs_dir, "m1.md"), add(client, docs_dir, "m2.md")
    c, d = add(client, docs_dir, "m3.md"), add(client, docs_dir, "m4.md")

    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "child"})
    client.post(f"/api/files/{c}/relations", json={"otherId": d, "kind": "child"})
    client.post(f"/api/files/{b}/relations", json={"otherId": c, "kind": "child"})

    nodes = {n["id"] for n in client.get(f"/api/files/{a}/graph").json()["nodes"]}
    assert nodes == {a, b, c, d}


def test_removing_an_edge_never_splits_the_graph(client, docs_dir: Path):
    a, b = add(client, docs_dir, "r1.md"), add(client, docs_dir, "r2.md")
    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "child"})

    removed = client.delete(
        f"/api/files/{a}/relations", params={"otherId": b, "kind": "child"}
    )
    assert removed.status_code == 204

    body = client.get(f"/api/files/{a}/graph").json()
    assert body["edges"] == []
    assert {n["id"] for n in body["nodes"]} == {a, b}   # both remain members


def test_remove_from_graph_detaches_only_that_document(client, docs_dir: Path):
    a, b = add(client, docs_dir, "d1.md"), add(client, docs_dir, "d2.md")
    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "child"})

    assert client.delete(f"/api/files/{a}/graph").status_code == 204

    assert [n["id"] for n in client.get(f"/api/files/{a}/graph").json()["nodes"]] == [a]
    assert [n["id"] for n in client.get(f"/api/files/{b}/graph").json()["nodes"]] == [b]


def test_self_link_is_rejected(client, docs_dir: Path):
    a = add(client, docs_dir, "self.md")
    response = client.post(
        f"/api/files/{a}/relations", json={"otherId": a, "kind": "child"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "A document cannot link to itself."


def test_unknown_relation_kind_is_rejected(client, docs_dir: Path):
    a, b = add(client, docs_dir, "u1.md"), add(client, docs_dir, "u2.md")
    response = client.post(
        f"/api/files/{a}/relations", json={"otherId": b, "kind": "nonsense"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Unknown relation kind."


def test_companion_is_one_directional_and_not_a_member(client, docs_dir: Path):
    owner = add(client, docs_dir, "owner.md")
    mate = add(client, docs_dir, "mate.md")

    client.post(f"/api/files/{owner}/relations", json={"otherId": mate, "kind": "companion"})

    body = client.get(f"/api/files/{owner}/graph").json()
    assert [n["id"] for n in body["nodes"]] == [owner]
    assert [n["id"] for n in body["companions"]] == [mate]


def test_a_companion_promoted_to_a_member_stops_being_a_companion(client, docs_dir: Path):
    owner = add(client, docs_dir, "o.md")
    mate = add(client, docs_dir, "c.md")

    client.post(f"/api/files/{owner}/relations", json={"otherId": mate, "kind": "companion"})
    client.post(f"/api/files/{owner}/relations", json={"otherId": mate, "kind": "child"})

    body = client.get(f"/api/files/{owner}/graph").json()
    assert {n["id"] for n in body["nodes"]} == {owner, mate}
    assert body["companions"] == []      # reconciled away as redundant


def test_a_node_reports_missing_when_the_file_is_gone(client, docs_dir: Path):
    a = add(client, docs_dir, "vanishing.md")
    (docs_dir / "vanishing.md").unlink()

    node = client.get(f"/api/files/{a}/graph").json()["nodes"][0]
    assert node["missing"] is True


# -------------------------------------------------------------------- colour maps
def test_color_map_import_and_removal(client, docs_dir: Path, tmp_path: Path):
    file_id = add(client, docs_dir, "colored.md")

    schema = tmp_path / "colors.json"
    schema.write_text(json.dumps({
        "listName": "Statuses",
        "legend": [{"color": "#f00", "meaning": "blocked"}],
        "files": [{"filePath": "C:/docs/x.md", "color": "#f00"}],
    }), encoding="utf-8")

    body = client.post(
        f"/api/files/{file_id}/colormaps", json={"path": str(schema)}
    ).json()

    assert body["listName"] == "Statuses"
    assert body["legend"] == [{"color": "#f00", "meaning": "blocked"}]
    assert body["files"] == [{"filePath": "C:/docs/x.md", "color": "#f00"}]

    assert len(client.get(f"/api/files/{file_id}/colormaps").json()) == 1
    assert client.delete(
        f"/api/files/{file_id}/colormaps/{body['id']}"
    ).status_code == 204
    assert client.get(f"/api/files/{file_id}/colormaps").json() == []


def test_color_map_without_entries_is_rejected(client, docs_dir: Path, tmp_path: Path):
    file_id = add(client, docs_dir, "nocolors.md")
    schema = tmp_path / "empty.json"
    schema.write_text(json.dumps({"legend": [], "files": []}), encoding="utf-8")

    response = client.post(
        f"/api/files/{file_id}/colormaps", json={"path": str(schema)}
    )
    assert response.status_code == 400
    assert "no file/color entries" in response.json()["error"]


def test_color_map_reflects_later_edits_to_the_source_file(
    client, docs_dir: Path, tmp_path: Path
):
    file_id = add(client, docs_dir, "live.md")
    schema = tmp_path / "live.json"
    schema.write_text(json.dumps({
        "listName": "One",
        "legend": [], "files": [{"filePath": "a.md", "color": "#111"}],
    }), encoding="utf-8")

    client.post(f"/api/files/{file_id}/colormaps", json={"path": str(schema)})

    schema.write_text(json.dumps({
        "listName": "Two",
        "legend": [], "files": [{"filePath": "b.md", "color": "#222"}],
    }), encoding="utf-8")

    refreshed = client.get(f"/api/files/{file_id}/colormaps").json()[0]
    assert refreshed["listName"] == "Two"
    assert refreshed["files"] == [{"filePath": "b.md", "color": "#222"}]


# -------------------------------------------------------------------- attachments
def test_reference_attachment_keeps_the_source_file(client, docs_dir: Path, tmp_path: Path):
    file_id = add(client, docs_dir, "host.md")
    target = tmp_path / "spec.pdf"
    target.write_bytes(b"%PDF-1.4 fake")

    body = client.post(
        f"/api/files/{file_id}/attachments/reference", json={"path": str(target)}
    ).json()

    assert body["fileName"] == "spec.pdf"
    assert body["kind"] == "reference"
    assert body["missing"] is False
    assert body["sizeBytes"] == target.stat().st_size

    duplicate = client.post(
        f"/api/files/{file_id}/attachments/reference", json={"path": str(target)}
    )
    assert duplicate.json()["error"] == "That file is already attached."

    assert client.delete(f"/api/attachments/{body['id']}").status_code == 204
    assert target.is_file()          # only the record went away


def test_reference_reports_missing_once_the_target_is_deleted(
    client, docs_dir: Path, tmp_path: Path
):
    file_id = add(client, docs_dir, "host2.md")
    target = tmp_path / "temp.bin"
    target.write_bytes(b"data")

    client.post(
        f"/api/files/{file_id}/attachments/reference", json={"path": str(target)}
    )
    target.unlink()

    assert client.get(f"/api/files/{file_id}/attachments").json()[0]["missing"] is True


def test_upload_stores_a_copy_and_deletes_it(client, docs_dir: Path):
    from app import config

    file_id = add(client, docs_dir, "uploads.md")
    response = client.post(
        f"/api/files/{file_id}/attachments/upload",
        files={"file": ("diagram.png", b"\x89PNG fake bytes", "image/png")},
    )

    body = response.json()
    assert body["kind"] == "upload"
    assert body["fileName"] == "diagram.png"
    assert body["sizeBytes"] == len(b"\x89PNG fake bytes")

    stored = list(config.ATTACHMENTS_DIR.glob("*.png"))
    assert len(stored) == 1

    download = client.get(f"/api/attachments/{body['id']}/download")
    assert download.status_code == 200
    assert download.content == b"\x89PNG fake bytes"

    client.delete(f"/api/attachments/{body['id']}")
    assert not stored[0].exists()


def test_export_zips_the_graph_members(client, docs_dir: Path):
    import zipfile

    from app import config

    a, b = add(client, docs_dir, "e1.md"), add(client, docs_dir, "e2.md")
    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "child"})

    body = client.post(
        f"/api/files/{a}/export", json={"indexHtml": "<html>graph</html>"}
    ).json()

    assert body["kind"] == "export"
    assert body["nodeCount"] == 2

    archives = list(config.ATTACHMENTS_DIR.glob("*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        names = set(archive.namelist())
        assert "index.html" in names
        assert f"{a}-e1.md" in names       # slug must match the JS one
        assert f"{b}-e2.md" in names


def test_attachments_are_shared_across_the_graph(client, docs_dir: Path, tmp_path: Path):
    a, b = add(client, docs_dir, "sh1.md"), add(client, docs_dir, "sh2.md")
    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "child"})

    target = tmp_path / "shared.txt"
    target.write_text("x", encoding="utf-8")
    client.post(f"/api/files/{a}/attachments/reference", json={"path": str(target)})

    assert len(client.get(f"/api/files/{b}/attachments").json()) == 1


# ------------------------------------------------------------------------- notes
def test_document_note_lifecycle_and_dashboard_cluster(client, docs_dir: Path):
    file_id = add(client, docs_dir, "noted.md")

    assert client.get("/api/dashboard/document-notes").json() == []

    note = client.post(f"/api/files/{file_id}/notes", json={"text": "first"}).json()
    assert note["fileId"] == file_id
    assert note["sortOrder"] == 1
    assert note["references"] == []

    groups = client.get("/api/dashboard/document-notes").json()
    assert len(groups) == 1
    assert groups[0]["fileId"] == file_id
    assert groups[0]["title"] == "noted"
    assert len(groups[0]["notes"]) == 1

    updated = client.patch(
        f"/api/files/{file_id}/notes/{note['id']}", json={"text": "edited"}
    ).json()
    assert updated["text"] == "edited"

    assert client.delete(
        f"/api/files/{file_id}/notes/{note['id']}"
    ).status_code == 204
    # The cluster card disappears with the last note.
    assert client.get("/api/dashboard/document-notes").json() == []


def test_note_references_round_trip(client, docs_dir: Path):
    file_id = add(client, docs_dir, "refs.md")
    note = client.post(f"/api/files/{file_id}/notes", json={"text": "n"}).json()

    ref = client.post(
        f"/api/files/{file_id}/notes/{note['id']}/references",
        json={"startOffset": 12, "length": 5, "text": "hello"},
    ).json()

    assert ref["documentNoteId"] == note["id"]
    assert ref["startOffset"] == 12
    assert ref["length"] == 5

    listed = client.get(f"/api/files/{file_id}/notes").json()
    assert listed[0]["references"] == [ref]

    assert client.delete(
        f"/api/files/{file_id}/notes/{note['id']}/references/{ref['id']}"
    ).status_code == 204
    assert client.get(
        f"/api/files/{file_id}/notes/{note['id']}/references"
    ).json() == []


def test_note_reference_validation(client, docs_dir: Path):
    file_id = add(client, docs_dir, "val.md")
    note = client.post(f"/api/files/{file_id}/notes", json={"text": "n"}).json()

    blank = client.post(
        f"/api/files/{file_id}/notes/{note['id']}/references",
        json={"startOffset": 0, "length": 3, "text": "   "},
    )
    assert blank.json()["error"] == "Highlighted text is required."

    zero = client.post(
        f"/api/files/{file_id}/notes/{note['id']}/references",
        json={"startOffset": 0, "length": 0, "text": "x"},
    )
    assert zero.json()["error"] == "Length must be positive."


def test_dashboard_sticky_notes(client):
    note = client.post("/api/dashboard/notes", json={
        "kind": "flip", "frontText": "Q", "backText": "A", "x": 10.5, "y": 20.5,
    }).json()

    assert note["kind"] == "flip"
    assert note["frontText"] == "Q"
    assert note["z"] == 1

    moved = client.patch(
        f"/api/dashboard/notes/{note['id']}", json={"x": 99.0, "z": 4}
    ).json()
    assert moved["x"] == 99.0
    assert moved["z"] == 4
    assert moved["frontText"] == "Q"      # untouched fields survive a partial patch

    assert client.delete(f"/api/dashboard/notes/{note['id']}").status_code == 204
    assert client.get("/api/dashboard/notes").json() == []


def test_deleting_a_document_removes_its_notes_and_graph_links(client, docs_dir: Path):
    a, b = add(client, docs_dir, "x1.md"), add(client, docs_dir, "x2.md")
    client.post(f"/api/files/{a}/relations", json={"otherId": b, "kind": "child"})
    client.post(f"/api/files/{a}/notes", json={"text": "note"})

    client.delete(f"/api/files/{a}")

    assert client.get("/api/dashboard/document-notes").json() == []
    assert [n["id"] for n in client.get(f"/api/files/{b}/graph").json()["nodes"]] == [b]
