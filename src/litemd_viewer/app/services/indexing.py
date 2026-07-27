"""Keeps the vector index in step with the documents.

The pipeline hangs off the content mirror's existing change detection. When a file's hash
moves, its id is queued; a single worker then re-chunks the document, compares each passage
against the hash stored on its `file_chunks` row, and only re-embeds the ones that actually
changed. Because a chunk row's primary key is also the vector store's `doc_id`, a revised
passage is updated in place instead of being deleted and re-added under a new id.

Writes to the `.hnsw` file are batched: it is saved once the queue has been quiet for a
moment, not once per save, since saving rewrites the whole file.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from bisect import bisect_left, bisect_right
from typing import Any

from local_vector_db import LocalVectorDB
from sqlalchemy import delete, select

from .. import config
from ..db import SessionLocal
from ..models import FileChunk, FileContent, FileIndexState, ManagedFile, utcnow
from ..schemas import SearchHitDto, SearchResultDto
from . import platform_fs
from .chunker import chunk_document, clean_markdown, sha256_hex
from .embedder import Embedder

log = logging.getLogger(__name__)

ACTION_INDEX = "index"
# Like ACTION_INDEX, but re-chunks even when the source text is unchanged -- the way to pick
# up a change to the chunking or cleaning rules.
ACTION_REINDEX = "reindex"
ACTION_REMOVE = "remove"

# Tombstones from deleted chunks are only reclaimed by a rebuild, so compact after enough
# of them have accumulated.
REBUILD_AFTER_DELETES = 500

# Search returns documents, but retrieves passages, and a long document can easily supply
# many of the best ones. These decide how many passages to consider so that `k` distinct
# documents can still be filled, without letting a broad query scan the whole index.
PASSAGE_OVERFETCH = 8
MAX_PASSAGE_CANDIDATES = 200

# ATX headings, which is what a section boundary is here; setext underlines are rare in the
# corpus and would need the previous line, so they are not treated as sections.
_ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*)$")
# A closing run of hashes is decoration, but only when spaced off -- "C#" is a word.
_CLOSING_HASHES_RE = re.compile(r"\s+#+\s*$")

COLLECTION_SCHEMA: dict[str, Any] = {
    "file_id": "INTEGER",
    "chunk_index": "INTEGER",
    "text": "TEXT",
    "embedding": {
        "type": "VECTOR",
        "dim": config.EMBED_DIM,
        "metric": "cosine",
    },
}


class Indexer:
    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or Embedder()
        self._store: LocalVectorDB | None = None
        self._store_error: str | None = None

        self._pending: dict[int, tuple[float, str]] = {}   # file_id -> (due, action)
        self._lock = threading.Lock()

        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

        self._uncommitted = False
        self._last_write = 0.0
        self._deletes_since_rebuild = 0

    # ------------------------------------------------------------------ store
    @property
    def store(self) -> LocalVectorDB | None:
        """Open the vector store on first use; a failure disables search, not the app."""
        if self._store is not None or self._store_error is not None:
            return self._store
        try:
            config.ensure_dirs()
            store = LocalVectorDB(str(config.VECTOR_DIR))
            store.create_collection_if_missing(
                config.VECTOR_COLLECTION, dict(COLLECTION_SCHEMA), max_elements=4096
            )
            self._store = store
        except Exception as exc:  # noqa: BLE001
            self._store_error = f"{type(exc).__name__}: {exc}"
            log.warning("vector store unavailable: %s", self._store_error)
        return self._store

    # ------------------------------------------------------------------ queueing
    def enqueue(self, file_id: int, delay: float | None = None, force: bool = False) -> None:
        due = time.monotonic() + (config.INDEX_DEBOUNCE_SECONDS if delay is None else delay)
        with self._lock:
            self._pending[file_id] = (due, ACTION_REINDEX if force else ACTION_INDEX)

    def remove_file(self, file_id: int) -> None:
        with self._lock:
            self._pending[file_id] = (time.monotonic(), ACTION_REMOVE)

    def _take_due(self) -> list[tuple[int, str]]:
        now = time.monotonic()
        with self._lock:
            due = [(fid, action) for fid, (at, action) in self._pending.items() if at <= now]
            for file_id, _ in due:
                self._pending.pop(file_id, None)
        return due

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="vector-indexer")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._store is not None:
            await asyncio.to_thread(self._safe_commit)

    async def _run(self) -> None:
        # Let startup settle, then pick up anything that changed while the app was closed.
        try:
            await asyncio.sleep(1.0)
            await asyncio.to_thread(self.reconcile)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("index reconcile failed")

        while not self._stopping.is_set():
            try:
                await asyncio.sleep(0.25)

                due = self._take_due()
                for file_id, action in due:
                    await asyncio.to_thread(self._process, file_id, action)

                # Flush the index file only after the writes have stopped for a moment.
                if self._uncommitted and (
                    time.monotonic() - self._last_write >= config.INDEX_COMMIT_IDLE_SECONDS
                ):
                    await asyncio.to_thread(self._safe_commit)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("indexer tick failed")

    def _safe_commit(self) -> None:
        store = self.store
        if store is None:
            return
        try:
            if self._deletes_since_rebuild >= REBUILD_AFTER_DELETES:
                store.rebuild_index(config.VECTOR_COLLECTION, config.VECTOR_FIELD)
                self._deletes_since_rebuild = 0
            store.commit()
            self._uncommitted = False
        except Exception:  # noqa: BLE001
            log.exception("vector commit failed")

    # ------------------------------------------------------------------ reconcile
    def reconcile(self, force: bool = False) -> int:
        """Queue every document whose indexed hash no longer matches its mirror.

        `force` queues all of them. The mirror hash only tracks the *source* text, so it
        cannot detect a change to the chunking or cleaning rules -- after those change, this
        is the only way to rebuild. Re-embedding still happens per chunk, so unchanged
        passages are skipped.
        """
        queued = 0
        with SessionLocal() as session:
            mirrors = {
                c.file_id: c.content_hash
                for c in session.scalars(select(FileContent)).all()
            }
            states = {
                s.file_id: s for s in session.scalars(select(FileIndexState)).all()
            }
            file_ids = set(session.scalars(select(ManagedFile.id)).all())

            for file_id in file_ids:
                state = states.get(file_id)
                mirror_hash = mirrors.get(file_id)
                if force or state is None or state.content_hash != (mirror_hash or ""):
                    self.enqueue(file_id, delay=0.0, force=force)
                    queued += 1

            # Drop index state for documents that no longer exist.
            for file_id in set(states) - file_ids:
                self.remove_file(file_id)

        if queued:
            log.info("queued %d document(s) for indexing", queued)
        return queued

    # ------------------------------------------------------------------ processing
    def _process(self, file_id: int, action: str) -> None:
        try:
            if action == ACTION_REMOVE:
                self._remove(file_id)
            else:
                self._index(file_id, force=action == ACTION_REINDEX)
        except Exception:  # noqa: BLE001
            log.exception("indexing failed for file %s", file_id)

    def _remove(self, file_id: int) -> None:
        store = self.store
        with SessionLocal() as session:
            chunk_ids = list(session.scalars(
                select(FileChunk.id).where(FileChunk.file_id == file_id)
            ).all())
            if chunk_ids and store is not None:
                for chunk_id in chunk_ids:
                    store.delete(config.VECTOR_COLLECTION, chunk_id)
                self._deletes_since_rebuild += len(chunk_ids)
                self._uncommitted = True
                self._last_write = time.monotonic()

            session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
            session.execute(
                delete(FileIndexState).where(FileIndexState.file_id == file_id)
            )
            session.commit()

    def _index(self, file_id: int, force: bool = False) -> None:
        store = self.store
        if store is None:
            return

        with SessionLocal() as session:
            file = session.get(ManagedFile, file_id)
            if file is None:
                self._remove(file_id)
                return

            content, content_hash = self._load_content(session, file)
            if content is None:
                return  # nothing readable yet; a later sweep retries

            state = session.get(FileIndexState, file_id)
            if (
                not force
                and state is not None
                and state.content_hash == content_hash
                and not state.error
            ):
                return  # already up to date

            chunks = chunk_document(content)
            existing = {
                row.chunk_index: row
                for row in session.scalars(
                    select(FileChunk).where(FileChunk.file_id == file_id)
                ).all()
            }

            # Every passage is embedded together with the title, so the title is part of what
            # the chunk hash has to cover -- otherwise a rename would leave stale vectors
            # behind. The hash therefore describes what was embedded, not merely the passage.
            inputs = [_embed_input(file.title or "", c.text) for c in chunks]

            # Reconcile the chunk rows first so every passage has its stable id before any
            # vector is written.
            rows: list[tuple[FileChunk, bool]] = []
            for chunk, embed_input in zip(chunks, inputs):
                embed_hash = sha256_hex(embed_input)
                row = existing.pop(chunk.index, None)
                if row is None:
                    row = FileChunk(file_id=file_id, chunk_index=chunk.index)
                    session.add(row)
                    changed = True
                else:
                    changed = row.content_hash != embed_hash or row.embedded_utc is None
                row.start_offset = chunk.start_offset
                row.end_offset = chunk.end_offset
                row.text = chunk.text
                row.content_hash = embed_hash
                rows.append((row, changed))

            surplus = list(existing.values())  # the document got shorter
            session.flush()  # assigns ids to the new rows

            stale_ids = [row.id for row in surplus]
            if stale_ids:
                for chunk_id in stale_ids:
                    store.delete(config.VECTOR_COLLECTION, chunk_id)
                self._deletes_since_rebuild += len(stale_ids)
                session.execute(delete(FileChunk).where(FileChunk.id.in_(stale_ids)))

            pending = [
                (row, chunk, text)
                for (row, changed), chunk, text in zip(rows, chunks, inputs) if changed
            ]
            error: str | None = None

            if pending:
                vectors = self.embedder.embed_documents([text for _, _, text in pending])
                if vectors is None:
                    error = self.embedder.last_error or "embedding unavailable"
                    for row, _, _ in pending:
                        row.embedded_utc = None
                else:
                    now = utcnow()
                    for (row, chunk, _), vector in zip(pending, vectors):
                        store.upsert(config.VECTOR_COLLECTION, row.id, {
                            "file_id": file_id,
                            "chunk_index": chunk.index,
                            "text": chunk.text,
                            "embedding": vector,
                        })
                        row.embedded_utc = now

            if pending or stale_ids:
                self._uncommitted = True
                self._last_write = time.monotonic()

            if state is None:
                state = FileIndexState(file_id=file_id)
                session.add(state)
            state.content_hash = content_hash if error is None else ""
            state.chunk_count = len(chunks)
            state.indexed_utc = utcnow()
            state.error = error

            session.commit()

    @staticmethod
    def _load_content(session, file: ManagedFile) -> tuple[str | None, str]:
        """Prefer the mirror, falling back to disk for a file not yet synced."""
        mirror = session.get(FileContent, file.id)
        if mirror is not None:
            return mirror.content, mirror.content_hash or sha256_hex(mirror.content)

        if not platform_fs.exists(file.full_path):
            return None, ""
        try:
            text = platform_fs.read_text_tolerant(file.full_path)
        except OSError:
            return None, ""
        return text, sha256_hex(text)

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str,
        k: int = 20,
        mode: str = "hybrid",
        file_id: int | None = None,
    ) -> SearchResultDto:
        """Search indexed passages.

        Unscoped, this returns at most `k` *documents*. Given a `file_id` it searches only
        that document and returns at most `k` of its *sections*, which is what the reader
        wants once they are already inside a document: a list of the places to jump to.

        Either way retrieval works on passages and the passages are then collapsed, so more
        of them than `k` are fetched -- otherwise one long document, or one long section,
        could crowd out every other result.
        """
        store = self.store
        cleaned = (query or "").strip()
        if store is None or not cleaned:
            return SearchResultDto(query=cleaned, mode=mode, hits=[])

        depth = min(k * PASSAGE_OVERFETCH, MAX_PASSAGE_CANDIDATES)
        filters = None if file_id is None else {"file_id": int(file_id)}
        ranked, mode = self._retrieve(cleaned, depth, mode, filters)
        if not ranked:
            return SearchResultDto(query=cleaned, mode=mode, hits=[])

        hits = (
            self._document_hits(ranked, k) if file_id is None
            else self._section_hits(int(file_id), cleaned, ranked, k)
        )
        return SearchResultDto(query=cleaned, mode=mode, hits=hits)

    def _retrieve(
        self, query: str, depth: int, mode: str, filters: dict | None
    ) -> tuple[list[tuple[dict, float]], str]:
        """Fetch and fuse the candidate passages. Returns them with the mode actually used."""
        store = self.store
        vector_rows: list[dict] = []
        text_rows: list[dict] = []

        if mode in ("vector", "hybrid"):
            vector = self.embedder.embed_query(query)
            if vector is not None:
                try:
                    vector_rows = store.query(
                        config.VECTOR_COLLECTION, vector=vector,
                        vector_field=config.VECTOR_FIELD, k=depth, filters=filters,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("vector search failed")
            elif mode == "vector":
                mode = "text"  # no model: degrade rather than return nothing

        if mode in ("text", "hybrid"):
            try:
                text_rows = store.query(
                    config.VECTOR_COLLECTION, text=_fts_query(query), k=depth,
                    filters=filters,
                )
            except Exception:  # noqa: BLE001
                log.debug("text search failed", exc_info=True)

        return _fuse(vector_rows, text_rows), mode

    def _document_hits(self, ranked: list[tuple[dict, float]], k: int) -> list[SearchHitDto]:
        # `ranked` is already best-first, so the first passage seen for a document is its
        # best one. Counting continues past the `k`th document so the tally is complete.
        best: dict[int, tuple[dict, float]] = {}
        matched: dict[int, int] = {}
        order: list[int] = []
        for row, score in ranked:
            if row.get("file_id") is None:
                continue
            file_id = int(row["file_id"])
            matched[file_id] = matched.get(file_id, 0) + 1
            if file_id not in best:
                best[file_id] = (row, score)
                order.append(file_id)

        top = order[:k]

        with SessionLocal() as session:
            files = {
                f.id: f for f in session.scalars(
                    select(ManagedFile).where(ManagedFile.id.in_(set(top)))
                ).all()
            }
            # The passage's offset into the document lives on the relational row, not in
            # the vector store, so the viewer can scroll straight to the match.
            offsets = {
                c.id: c.start_offset
                for c in session.scalars(
                    select(FileChunk).where(
                        FileChunk.id.in_([int(best[f][0]["doc_id"]) for f in top])
                    )
                ).all()
            }

            hits: list[SearchHitDto] = []
            for file_id in top:
                file = files.get(file_id)
                if file is None:
                    continue  # an entry for a document that has since been removed
                row, score = best[file_id]
                chunk_id = int(row["doc_id"])
                hits.append(SearchHitDto(
                    file_id=file.id,
                    title=file.title,
                    full_path=file.full_path,
                    missing=not platform_fs.exists(file.full_path),
                    chunk_id=chunk_id,
                    chunk_index=int(row.get("chunk_index") or 0),
                    start_offset=offsets.get(chunk_id, 0),
                    snippet=_snippet(row.get("text") or ""),
                    score=round(score, 6),
                    passage_count=matched[file_id],
                ))

        return hits

    def _section_hits(
        self, file_id: int, query: str, ranked: list[tuple[dict, float]], k: int
    ) -> list[SearchHitDto]:
        """Collapse one document's matching passages into a hit per enclosing section.

        Sections come from the raw markdown, because a chunk carries no heading of its own
        and is not cut on heading boundaries either -- a short document is a single passage
        spanning every section it has. So the section is taken from where the query's terms
        actually land inside the passage, which splits one passage across as many sections
        as it really matched in; a passage matched only semantically falls back to the
        section it starts in.

        Relevance picks which `k` sections to return, but they are then handed back in
        document order, so the list reads as the document does and does not reshuffle itself
        with every keystroke.
        """
        with SessionLocal() as session:
            file = session.get(ManagedFile, file_id)
            if file is None:
                return []
            content, _ = self._load_content(session, file)
            spans = {
                c.id: (c.start_offset, c.end_offset)
                for c in session.scalars(
                    select(FileChunk).where(FileChunk.file_id == file_id)
                ).all()
            }
            title = file.title
            full_path = file.full_path

        content = content or ""
        sections = _section_index(content)
        terms = _terms_pattern(query)
        missing = not platform_fs.exists(full_path)

        best: dict[tuple[str, ...], tuple[float, int, dict]] = {}
        matched: dict[tuple[str, ...], int] = {}
        order: list[tuple[str, ...]] = []
        for row, score in ranked:
            span = spans.get(int(row["doc_id"]))
            if span is None:
                continue  # a vector row whose chunk has since been re-cut away
            for offset, path in _section_matches(content, span, terms, sections):
                matched[path] = matched.get(path, 0) + 1
                if path not in best:
                    best[path] = (score, offset, row)
                    order.append(path)

        top = sorted(order[:k], key=lambda p: best[p][1])

        return [
            SearchHitDto(
                file_id=file_id,
                title=title,
                full_path=full_path,
                missing=missing,
                chunk_id=int(best[path][2]["doc_id"]),
                chunk_index=int(best[path][2].get("chunk_index") or 0),
                start_offset=best[path][1],
                snippet=_passage_snippet(content, best[path][1]),
                score=round(best[path][0], 6),
                passage_count=matched[path],
                section_path=list(path),
            )
            for path in top
        ]

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        with SessionLocal() as session:
            indexed_files = len(list(session.scalars(select(FileIndexState.file_id)).all()))
            indexed_chunks = len(list(session.scalars(select(FileChunk.id)).all()))
        return {
            "enabled": config.EMBEDDING_ENABLED and self.store is not None,
            "model": self.embedder.model_name if config.EMBEDDING_ENABLED else None,
            "dimension": self.embedder.dimension,
            "indexed_files": indexed_files,
            "indexed_chunks": indexed_chunks,
            "pending_files": self.pending_count(),
            "ready": self.embedder.ready,
            "last_error": self._store_error or self.embedder.last_error,
        }


# ---------------------------------------------------------------------- helpers
def _fts_query(text: str) -> str:
    """Quote each term so punctuation cannot be read as FTS5 syntax."""
    terms = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if t]
    return " OR ".join(f'"{t}"' for t in terms)


def _fuse(
    vector_rows: list[dict], text_rows: list[dict]
) -> list[tuple[dict, float]]:
    """Reciprocal-rank fusion, which needs no calibration between the two scales."""
    scores: dict[int, float] = {}
    rows: dict[int, dict] = {}
    lexical: set[int] = set()
    k = 60.0

    for rank, row in enumerate(vector_rows):
        doc_id = int(row["doc_id"])
        rows[doc_id] = row
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)

    for rank, row in enumerate(text_rows):
        doc_id = int(row["doc_id"])
        rows.setdefault(doc_id, row)
        lexical.add(doc_id)
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)

    # Both lists contribute identical 1/(k+rank) values, so the top of one ties with the top
    # of the other. Break such ties towards the lexical hit: the text index only returns
    # passages that actually contain the query terms, whereas the vector index always returns
    # k nearest neighbours however distant -- for a rare or out-of-vocabulary phrase its best
    # match is close to noise. The doc id is a final tie-break so that the order never
    # depends on dictionary iteration order.
    ordered = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], kv[0] not in lexical, kv[0]),
    )
    return [(rows[doc_id], score) for doc_id, score in ordered]


def _snippet(text: str, limit: int = 280) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + "\u2026"


def _section_index(text: str) -> tuple[list[int], list[list[str]]]:
    """Where every ATX heading starts, and the heading trail it opens.

    A `###` under a `#` yields the trail ["Top", "Sub"], so a hit can be shown in context
    rather than as a bare leaf heading. A `#` inside a fenced code block is a comment or a
    shell prompt, not structure, so fences are tracked and skipped.
    """
    offsets: list[int] = []
    paths: list[list[str]] = []
    stack: list[tuple[int, str]] = []   # (level, title) of the open ancestors
    at = 0
    fence: str | None = None

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
        else:
            match = _ATX_HEADING_RE.match(line)
            if match:
                level = len(match.group(1))
                title = clean_markdown(_CLOSING_HASHES_RE.sub("", match.group(2))).strip()
                if title:
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, title))
                    offsets.append(at)
                    paths.append([t for _, t in stack])
        at += len(line)

    return offsets, paths


def _section_path(index: tuple[list[int], list[list[str]]], offset: int) -> list[str]:
    """The heading trail enclosing a passage: the last heading at or before its offset."""
    offsets, paths = index
    i = bisect_right(offsets, offset)
    return list(paths[i - 1]) if i else []


def _terms_pattern(query: str) -> re.Pattern | None:
    """One alternation of the query's words, or None if it has none to look for.

    Matching is bounded by non-word characters rather than by `\\b`, so a term next to
    punctuation still matches while a term inside a longer identifier does not.
    """
    terms = sorted(
        {t for t in "".join(c if c.isalnum() else " " for c in query).split() if t},
        key=len, reverse=True,
    )
    if not terms:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.I)


def _section_matches(
    content: str,
    span: tuple[int, int],
    terms: re.Pattern | None,
    sections: tuple[list[int], list[list[str]]],
) -> list[tuple[int, tuple[str, ...]]]:
    """Where a passage matched, as one offset per distinct section it matched in.

    A passage can straddle several headings, and only the parts that actually contain the
    query are worth offering as places to jump to. With nothing to look for -- an empty
    query pattern, or a purely semantic match -- the passage stands for the one section it
    begins in.
    """
    start, end = span
    found: list[tuple[int, tuple[str, ...]]] = []
    if terms is not None:
        seen: set[tuple[str, ...]] = set()
        for match in terms.finditer(content, start, min(end, len(content))):
            path = tuple(_section_path(sections, match.start()))
            if path in seen:
                continue
            seen.add(path)
            found.append((match.start(), path))
    if found:
        return found

    path = tuple(_section_path(sections, start))
    if not path:
        # The passage opens above the document's first heading -- in front matter, or in a
        # preamble. If it runs into a heading it is named by that, which reads far better in
        # a list of sections than an unnamed lead-in would.
        first = _first_heading_in(sections, start, end)
        if first is not None:
            return [first]
    return [(start, path)]


def _first_heading_in(
    sections: tuple[list[int], list[list[str]]], start: int, end: int
) -> tuple[int, tuple[str, ...]] | None:
    """The earliest heading opening inside [start, end), if the span contains one."""
    offsets, paths = sections
    i = bisect_left(offsets, start)
    if i < len(offsets) and offsets[i] < end:
        return offsets[i], tuple(paths[i])
    return None


def _passage_snippet(content: str, offset: int, window: int = 320) -> str:
    """A snippet of the raw document from the line the match sits on.

    Taken from the source rather than from the chunk's stored text because one chunk can
    supply several sections, and each of them needs a snippet of its own -- both to read
    and for the viewer, which locates a hit by matching the snippet's words against the
    rendered document.
    """
    line_start = content.rfind("\n", 0, offset) + 1
    return _snippet(clean_markdown(content[line_start:line_start + window]))


def _embed_input(title: str, text: str) -> str:
    """The text actually handed to the embedder for one passage.

    The document's title is prepended to every passage, because a passage taken from the
    middle of a document rarely restates what the document is about -- and the title is the
    name the reader searches by, yet it need not appear in the prose at all.

    Only the embedding sees this. The stored text stays bare, so the snippet does not repeat
    the title back to the reader and the viewer can still locate the passage by matching the
    snippet's words against the rendered document.
    """
    title = " ".join(title.split())[:config.EMBED_TITLE_MAX_CHARS].strip()
    return f"{title}\n\n{text}" if title else text


# ---------------------------------------------------------------------- singleton
_indexer: Indexer | None = None


def get_indexer() -> Indexer:
    global _indexer
    if _indexer is None:
        _indexer = Indexer()
    return _indexer
