"""The incremental indexing contract.

A deterministic stub embedder stands in for the ONNX model: it is fast, offline, and lets
these tests count exactly how many passages were embedded, which is what the whole
chunk-hash design exists to minimise.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.models import FileChunk, FileContent, FileIndexState, ManagedFile, utcnow
from app.services.chunker import sha256_hex
from app.services.indexing import (
    Indexer, _fuse, _section_index, _section_matches, _section_path,
)


TOKEN_RE = re.compile(r"[a-z0-9]+")


class StubEmbedder:
    """A deterministic hashing bag-of-words vectorizer.

    Real embeddings would make these tests slow and non-deterministic, but a pure hash of
    the whole string would give meaningless neighbours. Hashing tokens into dimensions and
    normalising yields cosine similarity that tracks lexical overlap, which is enough to
    assert that the right passage ranks first -- and it counts how many texts were embedded.
    """

    def __init__(self) -> None:
        self.model_name = "stub"
        self.dimension = config.EMBED_DIM
        self.calls: list[list[str]] = []
        self.last_error = None
        self.ready = True

    @property
    def embedded_count(self) -> int:
        return sum(len(batch) for batch in self.calls)

    def load(self) -> bool:
        return True

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in TOKEN_RE.findall(text.lower()):
            slot = int.from_bytes(
                hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
            )
            vector[slot % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture()
def vector_dir(tmp_path: Path, monkeypatch):
    """Point the vector store at a scratch directory for each test."""
    directory = tmp_path / "vectors"
    directory.mkdir()
    monkeypatch.setattr(config, "VECTOR_DIR", directory)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def indexer(vector_dir):
    instance = Indexer(embedder=StubEmbedder())
    yield instance
    if instance._store is not None:
        instance._store.close()


def make_document(text: str, name: str = "doc.md") -> int:
    """Insert a managed file plus its content mirror, as the sync service would."""
    with SessionLocal() as session:
        file = ManagedFile(full_path=f"C:\\docs\\{name}", title=name[:-3], added_utc=utcnow())
        session.add(file)
        session.flush()
        session.add(FileContent(
            file_id=file.id, content=text, content_hash=sha256_hex(text),
            source_write_utc=utcnow(), synced_utc=utcnow(),
        ))
        session.commit()
        return file.id


def set_content(file_id: int, text: str) -> None:
    with SessionLocal() as session:
        mirror = session.get(FileContent, file_id)
        mirror.content = text
        mirror.content_hash = sha256_hex(text)
        mirror.synced_utc = utcnow()
        session.commit()


def set_title(file_id: int, title: str) -> None:
    with SessionLocal() as session:
        session.get(ManagedFile, file_id).title = title
        session.commit()


def chunk_rows(file_id: int) -> list[FileChunk]:
    with SessionLocal() as session:
        return list(session.scalars(
            select(FileChunk).where(FileChunk.file_id == file_id)
            .order_by(FileChunk.chunk_index)
        ).all())


def vector_count(indexer: Indexer) -> int:
    row = indexer.store.conn.execute(
        "SELECT count FROM _vector_meta WHERE collection=? AND field=?",
        (config.VECTOR_COLLECTION, config.VECTOR_FIELD),
    ).fetchone()
    return row["count"] if row else 0


# ---------------------------------------------------------------------- indexing
def test_indexing_creates_chunk_rows_and_vectors(indexer):
    file_id = make_document("# Title\n\nA paragraph about badgers.\n")
    indexer._index(file_id)

    rows = chunk_rows(file_id)
    assert len(rows) == 1
    assert rows[0].embedded_utc is not None
    assert vector_count(indexer) == 1

    with SessionLocal() as session:
        state = session.get(FileIndexState, file_id)
    assert state.chunk_count == 1
    assert state.error is None


def test_reindexing_unchanged_content_embeds_nothing(indexer):
    file_id = make_document("# Title\n\nUnchanged content.\n")
    indexer._index(file_id)
    baseline = indexer.embedder.embedded_count

    indexer._index(file_id)
    indexer._index(file_id)

    assert indexer.embedder.embedded_count == baseline


def test_editing_one_paragraph_reembeds_only_the_affected_chunks(indexer):
    paragraph = "A stable sentence that stays put. " * 12
    sections = [f"## Section {i}\n\n{paragraph}" for i in range(30)]

    file_id = make_document("\n\n".join(sections))
    indexer._index(file_id)

    total_chunks = len(chunk_rows(file_id))
    assert total_chunks > 3
    first_pass = indexer.embedder.embedded_count
    assert first_pass == total_chunks

    sections[-1] = sections[-1].replace("A stable sentence", "An edited sentence", 1)
    set_content(file_id, "\n\n".join(sections))
    indexer._index(file_id)

    reembedded = indexer.embedder.embedded_count - first_pass
    # The whole point of per-chunk hashing: a local edit is a local re-embed.
    assert 0 < reembedded <= 2
    assert reembedded < total_chunks


def test_chunk_ids_are_stable_across_edits(indexer):
    file_id = make_document("# Doc\n\nFirst version of the text.\n")
    indexer._index(file_id)
    before = [(r.chunk_index, r.id) for r in chunk_rows(file_id)]

    set_content(file_id, "# Doc\n\nSecond version of the text.\n")
    indexer._index(file_id)
    after = [(r.chunk_index, r.id) for r in chunk_rows(file_id)]

    # Same row, same primary key, therefore the same vector-store doc_id.
    assert before == after


def test_repeated_edits_do_not_grow_the_vector_index(indexer):
    file_id = make_document("# Doc\n\nRevision 0 of this paragraph.\n")
    indexer._index(file_id)

    for revision in range(1, 26):
        set_content(file_id, f"# Doc\n\nRevision {revision} of this paragraph.\n")
        indexer._index(file_id)

    # 26 saves of a one-chunk document must leave exactly one vector behind.
    assert vector_count(indexer) == 1
    assert len(chunk_rows(file_id)) == 1
    assert indexer.store._indexes[
        (config.VECTOR_COLLECTION, config.VECTOR_FIELD)
    ].get_current_count() == 1


def test_a_shortened_document_drops_its_surplus_chunks(indexer):
    long_text = "\n\n".join(
        f"## Section {i}\n\n" + ("padding sentence. " * 25) for i in range(12)
    )
    file_id = make_document(long_text)
    indexer._index(file_id)

    original = len(chunk_rows(file_id))
    assert original > 2

    set_content(file_id, "# Doc\n\nMuch shorter now.\n")
    indexer._index(file_id)

    remaining = chunk_rows(file_id)
    assert len(remaining) == 1
    assert vector_count(indexer) == 1
    assert [r.chunk_index for r in remaining] == [0]


def test_removing_a_document_clears_its_chunks_and_vectors(indexer):
    file_id = make_document("# Doc\n\nSoon to be removed.\n")
    indexer._index(file_id)
    assert vector_count(indexer) == 1

    indexer._remove(file_id)

    assert chunk_rows(file_id) == []
    assert vector_count(indexer) == 0
    with SessionLocal() as session:
        assert session.get(FileIndexState, file_id) is None


def test_indexing_a_deleted_document_is_a_no_op(indexer):
    file_id = make_document("# Doc\n\nContent.\n")
    with SessionLocal() as session:
        session.delete(session.get(ManagedFile, file_id))
        session.commit()

    indexer._index(file_id)      # must not raise
    assert chunk_rows(file_id) == []


def test_an_embedding_failure_is_recorded_and_retried(indexer):
    class FailingEmbedder(StubEmbedder):
        def embed_documents(self, texts):
            self.last_error = "model unavailable"
            return None

    indexer.embedder = FailingEmbedder()
    file_id = make_document("# Doc\n\nCannot be embedded yet.\n")
    indexer._index(file_id)

    with SessionLocal() as session:
        state = session.get(FileIndexState, file_id)
    assert state.error == "model unavailable"
    assert state.content_hash == ""          # forces a retry rather than looking done
    assert all(r.embedded_utc is None for r in chunk_rows(file_id))

    # Once the model works, the same sweep picks it up.
    indexer.embedder = StubEmbedder()
    indexer._index(file_id)
    assert all(r.embedded_utc is not None for r in chunk_rows(file_id))
    assert vector_count(indexer) == 1


# ------------------------------------------------------------------- reconcile
def test_reconcile_queues_only_stale_documents(indexer):
    fresh = make_document("# Fresh\n\nAlready indexed.\n", name="fresh.md")
    stale = make_document("# Stale\n\nNot yet indexed.\n", name="stale.md")

    indexer._index(fresh)
    indexer._pending.clear()

    queued = indexer.reconcile()
    assert queued == 1
    assert stale in indexer._pending
    assert fresh not in indexer._pending


# ------------------------------------------------------------------ title prefix
def test_the_title_is_embedded_with_every_passage(indexer):
    # A passage from the middle of a document rarely restates the subject, so each one is
    # embedded with the document's title for context.
    body = "A paragraph that never names its own subject. " * 12
    file_id = make_document(
        "\n\n".join(f"## Part {i}\n\n{body}" for i in range(8)),
        name="Quarterly Wombat Report.md",
    )
    indexer._index(file_id)

    embedded = [text for batch in indexer.embedder.calls for text in batch]
    assert len(embedded) > 1, "the document must span several passages"
    assert all(t.startswith("Quarterly Wombat Report\n\n") for t in embedded)


def test_the_stored_passage_keeps_no_title_prefix(indexer):
    file_id = make_document(
        "# Heading\n\nThe body of the document.\n", name="Quarterly Wombat Report.md"
    )
    indexer._index(file_id)

    row = chunk_rows(file_id)[0]
    # The snippet is built from this text and the viewer locates the passage by matching it
    # against the rendered document, so a title absent from the prose must not leak in.
    assert not row.text.startswith("Quarterly Wombat Report")
    assert row.text.startswith("Heading")

    hit = indexer.search("body of the document", k=5).hits[0]
    assert "Wombat" not in hit.snippet


def test_renaming_a_document_reembeds_it_in_place(indexer):
    file_id = make_document("# Doc\n\nA paragraph about setts.\n", name="old-name.md")
    indexer._index(file_id)
    first_pass = indexer.embedder.embedded_count
    before = chunk_rows(file_id)[0].content_hash

    set_title(file_id, "Badger Habitat Survey")
    indexer._index(file_id, force=True)

    # The prose is untouched, but what was embedded is not, so the hash must have moved.
    assert chunk_rows(file_id)[0].content_hash != before
    assert indexer.embedder.embedded_count > first_pass
    assert indexer.embedder.calls[-1][0].startswith("Badger Habitat Survey\n\n")
    assert vector_count(indexer) == 1, "a rename updates the vector, it does not add one"


def test_an_overlong_title_cannot_crowd_out_the_passage(indexer):
    file_id = make_document("# Doc\n\nThe passage itself.\n", name="short.md")
    set_title(file_id, "word " * 200)
    indexer._index(file_id, force=True)

    embedded = indexer.embedder.calls[-1][0]
    prefix = embedded.split("\n\n")[0]
    assert len(prefix) <= config.EMBED_TITLE_MAX_CHARS
    assert "The passage itself." in embedded


# ---------------------------------------------------------------------- search
def test_a_document_is_findable_by_a_title_its_text_never_mentions(indexer):
    # The reason the title is embedded at all: the drawer title is editable, need not appear
    # anywhere in the prose, and is the name the reader actually searches by.
    target = make_document(
        "# Overview\n\nThe subject is discussed only obliquely here.\n",
        name="Quarterly Wombat Report.md",
    )
    make_document("# Other\n\nUnrelated prose about badger setts.\n", name="other.md")

    with SessionLocal() as session:
        for fid in session.scalars(select(ManagedFile.id)).all():
            indexer._index(fid)

    result = indexer.search("quarterly wombat report", k=5, mode="vector")

    assert result.hits
    assert result.hits[0].file_id == target
    assert "wombat" not in result.hits[0].snippet.lower(), "matched on title, not on prose"


def test_search_finds_the_matching_passage(indexer):
    badgers = make_document(
        "# Wildlife\n\nBadgers dig extensive setts in woodland.\n", name="wild.md"
    )
    make_document("# Cooking\n\nSourdough needs a mature starter.\n", name="cook.md")

    for file_id in (badgers, _second_id(badgers)):
        indexer._index(file_id)

    result = indexer.search("Badgers dig extensive setts in woodland.", k=5, mode="vector")

    assert result.hits
    assert result.hits[0].file_id == badgers
    assert "Badgers" in result.hits[0].snippet
    assert result.hits[0].chunk_index == 0


def test_text_search_matches_keywords(indexer):
    file_id = make_document("# Notes\n\nThe quick brown fox jumps.\n")
    indexer._index(file_id)

    result = indexer.search("brown fox", k=5, mode="text")
    assert [h.file_id for h in result.hits] == [file_id]


def test_hybrid_search_surfaces_a_rare_exact_phrase(indexer):
    # The vector side cannot know these terms, so its nearest neighbour is noise; only the
    # lexical side has real evidence and it must win. This is the live re-index case: a
    # freshly added passage has to be findable by its own distinctive wording.
    marked = make_document(
        "# Report\n\nXylophone quokka telemetry is the distinctive marker sentence.\n",
        name="marked.md",
    )
    for name in ("other-a.md", "other-b.md", "other-c.md"):
        make_document(f"# {name}\n\nRoutine prose about unrelated matters.\n", name=name)

    with SessionLocal() as session:
        for file_id in session.scalars(select(ManagedFile.id)).all():
            indexer._index(file_id)

    result = indexer.search("xylophone quokka telemetry", k=5, mode="hybrid")

    assert result.hits
    assert result.hits[0].file_id == marked
    assert "Xylophone" in result.hits[0].snippet


def test_search_returns_one_hit_per_document(indexer):
    # Every section mentions badgers, so many passages of this one document match; the
    # result must still be a single entry rather than one per passage.
    section = "Badgers dig extensive setts in the woodland loam. " * 12
    text = "# Wildlife\n\n" + "\n\n".join(f"## Part {i}\n\n{section}" for i in range(8))
    file_id = make_document(text, name="badgers.md")
    indexer._index(file_id)
    assert len(chunk_rows(file_id)) > 1, "the document must span several passages"

    result = indexer.search("badgers setts woodland", k=10)

    assert [h.file_id for h in result.hits] == [file_id]
    assert result.hits[0].passage_count > 1


def test_consolidated_hit_carries_the_best_passage(indexer):
    filler = "Unrelated padding about kitchen plumbing. " * 30
    text = (
        "# Doc\n\n" + filler
        + "\n\n## Airships\n\nThe distinctive zeppelin marker phrase lives here.\n\n"
        + filler
    )
    file_id = make_document(text, name="zeppelin.md")
    indexer._index(file_id)

    hit = indexer.search("distinctive zeppelin marker phrase", k=5).hits[0]

    assert hit.file_id == file_id
    assert "zeppelin" in hit.snippet.lower(), "the representative passage must be the match"
    rows = {r.id: r for r in chunk_rows(file_id)}
    assert hit.start_offset == rows[hit.chunk_id].start_offset


def test_k_limits_documents_not_passages(indexer):
    # Each document is long enough to contribute several matching passages, so a k that
    # counted passages would return fewer than three documents.
    body = "Shared vocabulary about migration planning and upgrades. " * 14
    ids = [
        make_document(f"# Doc {i}\n\n" + "\n\n".join([body] * 4), name=f"doc-{i}.md")
        for i in range(3)
    ]
    for file_id in ids:
        indexer._index(file_id)

    result = indexer.search("migration planning upgrades", k=3)

    assert len(result.hits) == 3
    assert len({h.file_id for h in result.hits}) == 3


def test_single_passage_hit_reports_one(indexer):
    file_id = make_document("# Doc\n\nA solitary paragraph about okapi.\n", name="okapi.md")
    indexer._index(file_id)

    hit = indexer.search("solitary paragraph okapi", k=5).hits[0]
    assert hit.passage_count == 1


def test_fusion_breaks_ties_towards_the_lexical_hit():
    # Rank 1 in either list scores exactly 1/(60+1), so these two tie and the winner must
    # not depend on which list happened to be iterated first.
    vector_rows = [{"doc_id": 69}]
    text_rows = [{"doc_id": 18}]

    ranked = _fuse(vector_rows, text_rows)
    assert [row["doc_id"] for row, _ in ranked] == [18, 69]
    assert ranked[0][1] == pytest.approx(ranked[1][1])


def test_fusion_ranks_a_document_found_by_both_signals_first():
    ranked = _fuse([{"doc_id": 1}, {"doc_id": 2}], [{"doc_id": 2}, {"doc_id": 3}])

    assert [row["doc_id"] for row, _ in ranked][0] == 2, "agreement must outweigh a tie"
    assert ranked[0][1] > ranked[1][1]


def test_fusion_is_deterministic_for_complete_ties():
    ranked = _fuse([{"doc_id": 7}, {"doc_id": 3}], [{"doc_id": 7}, {"doc_id": 3}])
    assert [row["doc_id"] for row, _ in ranked] == [7, 3]


def test_search_returns_the_matching_chunk_and_its_offset(indexer):
    # Long enough to span several chunks, with the distinctive passage in the middle so a
    # wrong answer cannot accidentally be the first or last chunk.
    def part(i: int) -> str:
        return f"## Part {i}\n\n" + ("intro sentence. " * 25)

    marker = "## Airships\n\nThe distinctive zeppelin marker phrase lives here.\n"
    text = "# Doc\n\n" + "\n\n".join(
        [part(i) for i in range(10)] + [marker] + [part(i) for i in range(10, 20)]
    )

    file_id = make_document(text)
    indexer._index(file_id)

    rows = chunk_rows(file_id)
    assert len(rows) > 2
    expected = next(r for r in rows if "zeppelin" in r.text)

    result = indexer.search("distinctive zeppelin marker", k=5, mode="hybrid")
    assert result.hits
    top = result.hits[0]

    assert top.chunk_id == expected.id
    assert top.chunk_index == expected.chunk_index
    # The offset must point back at the passage, so the viewer can scroll to the match.
    assert top.start_offset == expected.start_offset
    assert "zeppelin" in text[expected.start_offset:expected.end_offset]


def test_search_skips_documents_that_were_deleted(indexer):
    file_id = make_document("# Doomed\n\nA unique sentence about zeppelins.\n")
    indexer._index(file_id)

    with SessionLocal() as session:
        session.delete(session.get(ManagedFile, file_id))
        session.commit()

    result = indexer.search("zeppelins", k=5, mode="hybrid")
    assert result.hits == []          # the orphan guard drops it


def test_empty_query_returns_no_hits(indexer):
    assert indexer.search("   ", k=5).hits == []


# ------------------------------------------------------- search within one document
SCOPED_DOC = (
    "# Handbook\n\nAn opening paragraph before any section.\n\n"
    "## Installation\n\nInstall the widget by running the bundled setup program.\n\n"
    "### Windows\n\nOn Windows the widget installer needs administrator rights.\n\n"
    "## Troubleshooting\n\nIf the widget installer fails, clear the cache and retry.\n\n"
    "## Licensing\n\nEntirely unrelated prose about invoices and renewal dates.\n"
)


def test_scoped_search_returns_sections_of_the_named_document(indexer):
    target = make_document(SCOPED_DOC, name="handbook.md")
    other = make_document(
        "# Elsewhere\n\nAnother widget installer lives in this other document.\n",
        name="other.md",
    )
    for file_id in (target, other):
        indexer._index(file_id)

    result = indexer.search("widget installer", k=10, file_id=target)

    assert result.hits, "the scoped search must find the document's own passages"
    assert {h.file_id for h in result.hits} == {target}, "the other document must not leak in"
    assert all(h.section_path for h in result.hits)


def test_scoped_search_labels_a_hit_with_its_heading_trail(indexer):
    file_id = make_document(SCOPED_DOC, name="handbook.md")
    indexer._index(file_id)

    result = indexer.search("administrator rights", k=10, file_id=file_id)

    hit = next(h for h in result.hits if h.section_path[-1] == "Windows")
    assert hit.section_path == ["Handbook", "Installation", "Windows"]


def test_scoped_hits_are_returned_in_document_order(indexer):
    file_id = make_document(SCOPED_DOC, name="handbook.md")
    indexer._index(file_id)

    result = indexer.search("widget installer cache", k=10, file_id=file_id)

    offsets = [h.start_offset for h in result.hits]
    assert offsets == sorted(offsets), "the list must read as the document does"


def test_scoped_search_collapses_a_section_matching_several_times(indexer):
    # One section long enough to be cut into several passages, all of them matching.
    section = "The widget installer writes its log beside the binary. " * 40
    text = "# Doc\n\n## Logging\n\n" + section + "\n\n## Other\n\nUnrelated prose.\n"
    file_id = make_document(text, name="logs.md")
    indexer._index(file_id)
    assert len(chunk_rows(file_id)) > 2, "the section must span several passages"

    result = indexer.search("widget installer log", k=10, file_id=file_id)

    logging_hits = [h for h in result.hits if h.section_path[-1] == "Logging"]
    assert len(logging_hits) == 1, "one section is one hit"
    assert logging_hits[0].passage_count > 1


def test_each_section_hit_carries_its_own_snippet(indexer):
    # The whole handbook is one passage, so a snippet taken from the chunk would be the same
    # text for every section -- and the viewer, which scrolls by matching the snippet's
    # words, would send all of them to the same place.
    file_id = make_document(SCOPED_DOC, name="handbook.md")
    indexer._index(file_id)
    assert len(chunk_rows(file_id)) == 1, "the premise: one passage, several sections"

    hits = indexer.search("widget installer", k=10, file_id=file_id).hits

    assert len(hits) > 1
    assert len({h.snippet for h in hits}) == len(hits)
    windows = next(h for h in hits if h.section_path[-1] == "Windows")
    assert windows.snippet.startswith("On Windows the widget installer")


def test_a_passage_above_the_first_heading_has_no_section(indexer):
    text = "A preamble about marmots, before any heading at all.\n\n# Later\n\nOther prose.\n"
    file_id = make_document(text, name="preamble.md")
    indexer._index(file_id)

    hit = indexer.search("preamble marmots", k=5, file_id=file_id).hits[0]
    assert hit.section_path == []


def test_a_preamble_passage_takes_the_first_heading_it_runs_into():
    # Front matter, then a heading, all inside one passage. Naming the hit after the section
    # it reaches beats leaving it unnamed, which is all its own start offset could say.
    text = "---\ntitle: Notes\n---\n\n## Context\n\nBody prose.\n"
    sections = _section_index(text)
    span = (0, len(text))

    assert _section_matches(text, span, None, sections) == [
        (text.index("## Context"), ("Context",))
    ]


def test_a_preamble_passage_with_no_heading_at_all_stays_unnamed():
    text = "Just a note with no headings anywhere in it.\n"
    sections = _section_index(text)

    assert _section_matches(text, (0, len(text)), None, sections) == [(0, ())]


def test_an_unscoped_hit_carries_no_section_path(indexer):
    file_id = make_document(SCOPED_DOC, name="handbook.md")
    indexer._index(file_id)

    hit = indexer.search("widget installer", k=5).hits[0]
    assert hit.section_path == [], "the trail is only meaningful inside one document"


def test_scoped_search_of_a_missing_document_is_empty(indexer):
    file_id = make_document(SCOPED_DOC, name="handbook.md")
    indexer._index(file_id)
    with SessionLocal() as session:
        session.delete(session.get(ManagedFile, file_id))
        session.commit()

    assert indexer.search("widget installer", k=5, file_id=file_id).hits == []


def test_section_index_ignores_a_hash_inside_a_code_fence():
    text = (
        "# Real\n\n"
        "```bash\n"
        "# not a heading, a shell comment\n"
        "```\n\n"
        "body text\n"
    )
    index = _section_index(text)
    assert _section_path(index, len(text) - 1) == ["Real"]


def test_section_index_keeps_a_trailing_hash_that_is_part_of_a_word():
    index = _section_index("## C#\n\nbody\n")
    assert _section_path(index, 8) == ["C#"]


def test_section_index_drops_a_closing_hash_sequence():
    index = _section_index("## Setup ##\n\nbody\n")
    assert _section_path(index, 14) == ["Setup"]


def test_a_sibling_heading_replaces_rather_than_nests():
    text = "# Top\n\n## One\n\nalpha\n\n## Two\n\nbeta\n"
    index = _section_index(text)
    assert _section_path(index, text.index("beta")) == ["Top", "Two"]


def test_status_reports_index_size(indexer):
    file_id = make_document("# Doc\n\nSomething to count.\n")
    indexer._index(file_id)

    status = indexer.status()
    assert status["indexed_files"] == 1
    assert status["indexed_chunks"] == 1
    assert status["dimension"] == config.EMBED_DIM


def _second_id(first_id: int) -> int:
    """The id of the other document created by the test above."""
    with SessionLocal() as session:
        ids = sorted(session.scalars(select(ManagedFile.id)).all())
    return next(i for i in ids if i != first_id)
