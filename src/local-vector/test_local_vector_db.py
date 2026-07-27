"""Regression tests for the stable-id / upsert contract that the document indexer relies on.

Run with:  .venv\\Scripts\\python -m pytest test_local_vector_db.py -q
"""

import os
import shutil
import tempfile

import numpy as np
import pytest

from local_vector_db import LocalVectorDB

DIM = 16

SCHEMA = {
    "file_id": "INTEGER",
    "text": "TEXT",
    "embedding": {"type": "VECTOR", "dim": DIM, "metric": "cosine"},
}


def unit(seed: int):
    rng = np.random.default_rng(seed)
    v = rng.random(DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


@pytest.fixture()
def db():
    path = tempfile.mkdtemp(prefix="lvdb_test_")
    inst = LocalVectorDB(path)
    inst.create_collection("chunks", dict(SCHEMA), max_elements=8)
    try:
        yield inst
    finally:
        try:
            inst.close()
        except Exception:
            pass
        shutil.rmtree(path, ignore_errors=True)


def vector_count(db, collection="chunks", field="embedding"):
    row = db.conn.execute(
        "SELECT count FROM _vector_meta WHERE collection=? AND field=?",
        (collection, field),
    ).fetchone()
    return row["count"]


def test_add_honours_caller_supplied_id(db):
    returned = db.add("chunks", {"file_id": 7, "text": "hello", "embedding": unit(1)}, doc_id=500)
    assert returned == 500
    assert db.get("chunks", 500)["file_id"] == 7


def test_upsert_creates_when_absent(db):
    db.upsert("chunks", 42, {"file_id": 1, "text": "first", "embedding": unit(2)})
    assert db.get("chunks", 42)["text"] == "first"
    assert vector_count(db) == 1


def test_upsert_keeps_id_and_does_not_grow_the_index(db):
    db.upsert("chunks", 42, {"file_id": 1, "text": "v1", "embedding": unit(3)})

    for i in range(25):
        db.upsert("chunks", 42, {"file_id": 1, "text": f"v{i}", "embedding": unit(100 + i)})

    # The whole point: 26 writes, still exactly one document and one vector.
    assert vector_count(db) == 1
    assert db._indexes[("chunks", "embedding")].get_current_count() == 1
    assert db.get("chunks", 42)["text"] == "v24"
    # max_elements must not have doubled on replacement writes.
    assert db._schemas["chunks"]["embedding"]["max_elements"] == 8


def test_upsert_replaces_the_vector_not_just_the_row(db):
    old, new = unit(4), unit(5)
    db.upsert("chunks", 1, {"file_id": 1, "text": "x", "embedding": old})
    db.upsert("chunks", 1, {"file_id": 1, "text": "x", "embedding": new})

    hits = db.query("chunks", vector=new, k=1)
    assert hits[0]["doc_id"] == 1
    assert hits[0]["_distance"] == pytest.approx(0.0, abs=1e-5)

    stale = db.query("chunks", vector=old, k=1)
    assert stale[0]["_distance"] > 0.01


def test_upsert_preserves_unsupplied_fields(db):
    db.upsert("chunks", 3, {"file_id": 9, "text": "keep me", "embedding": unit(6)})
    db.upsert("chunks", 3, {"embedding": unit(7)})

    row = db.get("chunks", 3)
    assert row["file_id"] == 9
    assert row["text"] == "keep me"


def test_upsert_refreshes_full_text_index(db):
    db.upsert("chunks", 4, {"file_id": 1, "text": "alpha", "embedding": unit(8)})
    assert [r["doc_id"] for r in db.query("chunks", text="alpha")] == [4]

    db.upsert("chunks", 4, {"file_id": 1, "text": "bravo", "embedding": unit(9)})
    assert db.query("chunks", text="alpha") == []
    assert [r["doc_id"] for r in db.query("chunks", text="bravo")] == [4]


def test_text_search_returns_most_relevant_first(db):
    # Doc 1 is inserted first, so a rowid-ordered scan would return it first even though
    # doc 3 is the only one carrying the rare term.
    db.upsert("chunks", 1, {"file_id": 1, "text": "telemetry " * 3, "embedding": unit(30)})
    db.upsert("chunks", 2, {"file_id": 2, "text": "telemetry notes", "embedding": unit(31)})
    db.upsert("chunks", 3, {"file_id": 3, "text": "xylophone telemetry", "embedding": unit(32)})

    hits = db.query("chunks", text='"xylophone" OR "telemetry"', k=3)
    assert hits[0]["doc_id"] == 3, "the rare-term match must outrank the common ones"
    assert {h["doc_id"] for h in hits} == {1, 2, 3}


def test_text_search_k_keeps_the_best_matches(db):
    db.upsert("chunks", 1, {"file_id": 1, "text": "common word", "embedding": unit(33)})
    db.upsert("chunks", 2, {"file_id": 2, "text": "common word", "embedding": unit(34)})
    db.upsert("chunks", 3, {"file_id": 3, "text": "common word quokka", "embedding": unit(35)})

    # Truncating to k must drop the weakest match, not simply the highest rowid.
    hits = db.query("chunks", text='"quokka" OR "common"', k=1)
    assert [h["doc_id"] for h in hits] == [3]


def test_text_search_still_honours_scalar_filters(db):
    db.upsert("chunks", 1, {"file_id": 1, "text": "shared term", "embedding": unit(36)})
    db.upsert("chunks", 2, {"file_id": 2, "text": "shared term", "embedding": unit(37)})

    hits = db.query("chunks", filters={"file_id": 2}, text='"shared"', k=5)
    assert [h["doc_id"] for h in hits] == [2]


def test_delete_decrements_and_hides(db):
    db.upsert("chunks", 1, {"file_id": 1, "text": "a", "embedding": unit(10)})
    db.upsert("chunks", 2, {"file_id": 1, "text": "b", "embedding": unit(11)})
    db.delete("chunks", 1)

    assert vector_count(db) == 1
    assert db.get("chunks", 1) is None
    assert all(r["doc_id"] != 1 for r in db.query("chunks", vector=unit(10), k=5))


def test_rebuild_index_compacts_tombstones(db):
    for i in range(1, 6):
        db.upsert("chunks", i, {"file_id": 1, "text": f"t{i}", "embedding": unit(20 + i)})
    for i in (2, 4):
        db.delete("chunks", i)

    index = db._indexes[("chunks", "embedding")]
    assert index.get_current_count() == 5  # tombstoned, not reclaimed

    reinserted = db.rebuild_index("chunks", "embedding")
    assert reinserted == 3
    assert db._indexes[("chunks", "embedding")].get_current_count() == 3
    assert vector_count(db) == 3

    survivors = {r["doc_id"] for r in db.query("chunks", vector=unit(21), k=5)}
    assert survivors == {1, 3, 5}


def test_commit_only_writes_dirty_indexes(db):
    db.upsert("chunks", 1, {"file_id": 1, "text": "a", "embedding": unit(12)})
    db.commit()

    path = db._vector_path("chunks", "embedding")
    first = os.path.getmtime(path)
    assert db._dirty == set()

    db.commit()  # nothing changed since
    assert os.path.getmtime(path) == first


def test_growth_still_works_for_genuinely_new_docs(db):
    # capacity starts at 8; adding 20 distinct docs must resize, not fail
    for i in range(1, 21):
        db.upsert("chunks", i, {"file_id": i, "text": f"doc {i}", "embedding": unit(200 + i)})

    assert vector_count(db) == 20
    assert db._schemas["chunks"]["embedding"]["max_elements"] >= 20
    assert len(db.query("chunks", vector=unit(201), k=20)) == 20


def test_survives_reopen(db):
    db.upsert("chunks", 77, {"file_id": 5, "text": "persisted", "embedding": unit(13)})
    db.commit()
    path = db.path
    db.close()

    reopened = LocalVectorDB(path)
    try:
        assert reopened.get("chunks", 77)["text"] == "persisted"
        hit = reopened.query("chunks", vector=unit(13), k=1)[0]
        assert hit["doc_id"] == 77
    finally:
        reopened.close()
