"""Contract tests against the wire format the unchanged frontend expects.

The frontend was not touched by the port, so these assert the things that would silently
break it: camelCase keys in both directions, the {"error": ...} envelope, the extra `id` on
a 409, and 204s with no body.
"""

from __future__ import annotations

from pathlib import Path

from conftest import write_doc


def test_tree_is_empty_initially(client):
    body = client.get("/api/tree").json()
    assert body == {"folders": [], "files": []}


def test_add_file_returns_camel_case(client, docs_dir: Path):
    path = write_doc(docs_dir, "notes.md", "# Notes\n")
    response = client.post("/api/files", json={"path": path, "folderId": None})

    assert response.status_code == 200
    body = response.json()
    # These exact keys are what tree.js and app.js read.
    assert set(body) == {"id", "title", "fullPath", "folderId", "sortOrder", "missing"}
    assert body["title"] == "notes"
    assert body["missing"] is False


def test_add_file_rejects_unsupported_extension(client, docs_dir: Path):
    path = write_doc(docs_dir, "notes.txt", "hello")
    response = client.post("/api/files", json={"path": path})

    assert response.status_code == 400
    # api.js reads data.error; FastAPI's default "detail" key would show a bare status.
    assert response.json()["error"] == "Only .md, .markdown, or .xml files are supported."


def test_add_file_requires_a_path(client):
    response = client.post("/api/files", json={"path": "   "})
    assert response.status_code == 400
    assert response.json()["error"] == "A path is required."


def test_duplicate_add_returns_409_with_the_existing_id(client, docs_dir: Path):
    path = write_doc(docs_dir, "dup.md", "# Dup\n")
    first = client.post("/api/files", json={"path": path}).json()

    response = client.post("/api/files", json={"path": path})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "This file is already managed."
    # app.js opens this id instead of surfacing an error.
    assert body["id"] == first["id"]


def test_duplicate_add_is_case_insensitive(client, docs_dir: Path):
    path = write_doc(docs_dir, "Case.md", "# Case\n")
    client.post("/api/files", json={"path": path})

    response = client.post("/api/files", json={"path": path.upper()})
    assert response.status_code == 409


def test_add_folder_files_reports_added_and_skipped(client, docs_dir: Path):
    write_doc(docs_dir, "a.md", "# A\n")
    write_doc(docs_dir, "b.markdown", "# B\n")
    write_doc(docs_dir, "c.txt", "ignored")

    first = client.post("/api/files/folder", json={"path": str(docs_dir)}).json()
    assert first["added"] == 2
    assert first["skipped"] == 0
    assert len(first["files"]) == 2

    again = client.post("/api/files/folder", json={"path": str(docs_dir)}).json()
    assert again["added"] == 0
    assert again["skipped"] == 2


def test_create_new_file_writes_to_disk(client, docs_dir: Path):
    response = client.post(
        "/api/files/new", json={"dir": str(docs_dir), "name": "fresh"}
    )
    assert response.status_code == 200

    created = docs_dir / "fresh.md"
    assert created.is_file()
    assert created.read_text(encoding="utf-8") == "# fresh\n"


def test_patch_file_accepts_camel_case_body(client, docs_dir: Path):
    path = write_doc(docs_dir, "p.md", "# P\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    folder = client.post("/api/folders", json={"name": "Group"}).json()
    body = client.patch(
        f"/api/files/{file_id}",
        json={"title": "Renamed", "folderId": folder["id"], "sortOrder": 7},
    ).json()

    assert body["title"] == "Renamed"
    assert body["folderId"] == folder["id"]
    assert body["sortOrder"] == 7

    # moveToRoot is a camelCase boolean the drawer sends when dragging to the top level.
    back = client.patch(f"/api/files/{file_id}", json={"moveToRoot": True}).json()
    assert back["folderId"] is None


def test_renaming_a_file_queues_it_for_reindexing(client, docs_dir: Path):
    # The title is embedded with every passage, so a rename leaves the vectors stale even
    # though the file on disk has not changed.
    from app.services.indexing import ACTION_REINDEX, get_indexer

    path = write_doc(docs_dir, "r.md", "# R\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    indexer = get_indexer()
    indexer._pending.clear()
    client.patch(f"/api/files/{file_id}", json={"title": "A Different Name"})
    assert indexer._pending.get(file_id, (0, None))[1] == ACTION_REINDEX

    # Reordering is not a rename, so it must not cost an embedding.
    indexer._pending.clear()
    client.patch(f"/api/files/{file_id}", json={"sortOrder": 3})
    assert file_id not in indexer._pending


def test_content_round_trip_and_read_only_fallback(client, docs_dir: Path):
    path = write_doc(docs_dir, "content.md", "# Original\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    body = client.get(f"/api/files/{file_id}/content").json()
    assert body["text"] == "# Original\n"
    assert body["onDisk"] is True
    assert body["readOnly"] is False

    assert client.put(
        f"/api/files/{file_id}/content", json={"text": "# Edited\n"}
    ).json() == {"ok": True}
    assert Path(path).read_text(encoding="utf-8") == "# Edited\n"


def test_content_falls_back_to_the_mirror_when_the_file_is_gone(
    client, docs_dir: Path, session
):
    from app.models import FileContent, utcnow

    path = write_doc(docs_dir, "mirrored.md", "# Mirrored\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    session.add(FileContent(
        file_id=file_id, content="# Stored copy\n", content_hash="abc",
        source_write_utc=utcnow(), synced_utc=utcnow(),
    ))
    session.commit()
    Path(path).unlink()

    body = client.get(f"/api/files/{file_id}/content").json()
    assert body["text"] == "# Stored copy\n"
    assert body["onDisk"] is False
    assert body["readOnly"] is True

    # ...and it can be written back to disk.
    recreated = client.post(f"/api/files/{file_id}/recreate")
    assert recreated.status_code == 200
    assert recreated.json()["missing"] is False
    assert Path(path).read_text(encoding="utf-8") == "# Stored copy\n"


def test_details_timestamps_carry_a_utc_offset(client, docs_dir: Path):
    path = write_doc(docs_dir, "d.md", "# D\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    body = client.get(f"/api/files/{file_id}/details").json()
    assert body["exists"] is True
    # `new Date(...)` in ui.js reads an offset-less timestamp as local time, which would
    # shift the displayed value; the offset must be present.
    assert body["modifiedUtc"].endswith("+00:00") or body["modifiedUtc"].endswith("Z")


def test_move_file_updates_path_and_keeps_the_id(client, docs_dir: Path, tmp_path: Path):
    path = write_doc(docs_dir, "movable.md", "# Movable\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    target = tmp_path / "elsewhere"
    target.mkdir()

    body = client.post(
        f"/api/files/{file_id}/move", json={"dir": str(target), "newName": "moved.md"}
    ).json()

    assert body["id"] == file_id      # relations and vectors stay attached
    assert body["fullPath"] == str(target / "moved.md")
    assert (target / "moved.md").is_file()
    assert not Path(path).exists()


def test_unmanage_returns_204_and_leaves_the_file(client, docs_dir: Path):
    path = write_doc(docs_dir, "keep.md", "# Keep\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    response = client.delete(f"/api/files/{file_id}")
    assert response.status_code == 204
    assert response.content == b""
    assert Path(path).is_file()
    assert client.get("/api/tree").json()["files"] == []


def test_delete_from_disk_removes_the_file(client, docs_dir: Path):
    path = write_doc(docs_dir, "gone.md", "# Gone\n")
    file_id = client.post("/api/files", json={"path": path}).json()["id"]

    assert client.delete(f"/api/files/{file_id}/disk").status_code == 204
    assert not Path(path).exists()


def test_missing_file_returns_404_envelope(client):
    response = client.get("/api/files/9999/content")
    assert response.status_code == 404
    assert "error" in response.json()


# ------------------------------------------------------------------------ folders
def test_folder_crud_and_cycle_guard(client):
    parent = client.post("/api/folders", json={"name": "Parent"}).json()
    child = client.post(
        "/api/folders", json={"name": "Child", "parentId": parent["id"]}
    ).json()

    assert child["parentId"] == parent["id"]

    cycle = client.patch(f"/api/folders/{parent['id']}", json={"parentId": child["id"]})
    assert cycle.status_code == 400
    assert cycle.json()["error"] == "That move would create a cycle."

    self_parent = client.patch(
        f"/api/folders/{parent['id']}", json={"parentId": parent["id"]}
    )
    assert self_parent.json()["error"] == "A folder cannot be its own parent."


def test_deleting_a_folder_reparents_its_children(client, docs_dir: Path):
    parent = client.post("/api/folders", json={"name": "Parent"}).json()
    child = client.post(
        "/api/folders", json={"name": "Child", "parentId": parent["id"]}
    ).json()

    path = write_doc(docs_dir, "in-folder.md", "# X\n")
    file_id = client.post(
        "/api/files", json={"path": path, "folderId": child["id"]}
    ).json()["id"]

    assert client.delete(f"/api/folders/{child['id']}").status_code == 204

    tree = client.get("/api/tree").json()
    moved = next(f for f in tree["files"] if f["id"] == file_id)
    assert moved["folderId"] == parent["id"]


# ----------------------------------------------------------------------- settings
def test_settings_round_trip(client):
    assert client.put("/api/settings/theme", json={"value": "dark"}).json() == {
        "key": "theme", "value": "dark"
    }
    assert client.get("/api/settings").json()["theme"] == "dark"


# ------------------------------------------------------------------------ browse
def test_browse_lists_documents_and_directories(client, docs_dir: Path):
    write_doc(docs_dir, "visible.md", "# V\n")
    write_doc(docs_dir, "hidden.txt", "nope")
    (docs_dir / "sub").mkdir()

    body = client.get("/api/browse", params={"path": str(docs_dir)}).json()
    assert body["isRoot"] is False
    names = [e["name"] for e in body["entries"]]
    assert "visible.md" in names
    assert "hidden.txt" not in names
    # Directories sort before files.
    assert names.index("sub") < names.index("visible.md")
    assert body["entries"][0]["isDir"] is True


def test_browse_without_a_path_lists_drives(client):
    body = client.get("/api/browse").json()
    assert body["isRoot"] is True
    assert body["path"] is None
    assert all(e["isDir"] for e in body["entries"])
