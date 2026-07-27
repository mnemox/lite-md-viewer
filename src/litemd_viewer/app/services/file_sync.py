"""Mirrors every managed file's on-disk content into the database and keeps it fresh.

Two mechanisms cooperate, as in the .NET original:
  - a watchdog observer per managed-file directory reacts to writes in near real time
    (debounced, since editors fire several events per save), and
  - a periodic reconcile sweep rebuilds the watch set as the managed list changes and
    re-syncs any file whose mtime moved -- a safety net for coalesced or missed events and
    for files that reappear on disk.

It only ever *writes* the mirror; a missing file is left alone, since its stored copy is the
whole point. A mirror row is dropped only when the document is unmanaged or deleted through
the app (the delete endpoints do that, with a defensive prune here too).

Whenever a document's content hash actually changes, its id is handed to the indexer -- that
single line is what keeps the vector index in step with the files.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime

from sqlalchemy import delete, select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .. import config
from ..db import SessionLocal
from ..models import FileContent, ManagedFile, utcnow
from . import platform_fs
from .chunker import sha256_hex
from .indexing import Indexer

log = logging.getLogger(__name__)


class _DirtyHandler(FileSystemEventHandler):
    """Feeds document-file events back to the service, ignoring everything else."""

    def __init__(self, mark_dirty) -> None:
        self._mark_dirty = mark_dirty

    def _handle(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        for attr in ("dest_path", "src_path"):
            path = getattr(event, attr, None)
            if path and config.is_supported(str(path)):
                self._mark_dirty(str(path))

    on_created = _handle
    on_modified = _handle
    on_moved = _handle


class FileSyncService:
    def __init__(self, indexer: Indexer) -> None:
        self.indexer = indexer

        self._observer: Observer | None = None
        self._watches: dict[str, object] = {}   # normalized dir -> watch handle

        # Paths seen by the watcher, mapped to the earliest time they should be synced.
        # Re-setting the value on each event collapses a burst of writes into one sync.
        self._dirty: dict[str, float] = {}
        self._lock = threading.Lock()

        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._observer is None:
            self._observer = Observer()
            self._observer.daemon = True
            self._observer.start()
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="file-sync")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await asyncio.to_thread(self._shutdown_observer)

    def _shutdown_observer(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.unschedule_all()
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception:  # noqa: BLE001
            log.debug("observer shutdown failed", exc_info=True)
        finally:
            self._observer = None
            self._watches.clear()

    async def _run(self) -> None:
        # Let startup settle before the first sweep.
        try:
            await asyncio.sleep(1.0)
            await asyncio.to_thread(self.reconcile)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("initial content reconcile failed")

        last_reconcile = time.monotonic()
        while not self._stopping.is_set():
            try:
                await asyncio.sleep(config.SYNC_TICK_SECONDS)
                await asyncio.to_thread(self._drain_dirty)

                if time.monotonic() - last_reconcile >= config.SYNC_RECONCILE_SECONDS:
                    await asyncio.to_thread(self.reconcile)
                    last_reconcile = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("file sync tick failed")

    # ------------------------------------------------------------------ realtime
    def mark_dirty(self, path: str) -> None:
        try:
            key = platform_fs.path_key(path)
        except (OSError, ValueError):
            return
        with self._lock:
            self._dirty[key] = time.monotonic() + config.SYNC_DEBOUNCE_SECONDS

    def _drain_dirty(self) -> None:
        now = time.monotonic()
        with self._lock:
            due = [p for p, at in self._dirty.items() if at <= now]
            for path in due:
                self._dirty.pop(path, None)
        for path in due:
            self._sync_path(path)

    def _sync_path(self, path_key: str) -> None:
        with SessionLocal() as session:
            file = next(
                (f for f in session.scalars(select(ManagedFile)).all()
                 if platform_fs.path_key(f.full_path) == path_key),
                None,
            )
            if file is None:
                return                                   # not a document we manage
            if not platform_fs.exists(file.full_path):
                return                                   # deleted: keep the stored copy

            mtime = platform_fs.mtime_utc(file.full_path)
            if mtime is None:
                return

            mirror = session.get(FileContent, file.id)
            if mirror is not None and mirror.source_write_utc == mtime:
                return                                   # unchanged

            if self._upsert_from_disk(session, file, mirror, mtime):
                session.commit()

    # ------------------------------------------------------------------ reconcile
    def reconcile(self) -> None:
        with SessionLocal() as session:
            files = list(session.scalars(select(ManagedFile)).all())
            self._refresh_watches(files)

            # Defensive: drop mirror rows whose document is gone.
            file_ids = {f.id for f in files}
            orphans = [
                c.file_id for c in session.scalars(select(FileContent)).all()
                if c.file_id not in file_ids
            ]
            changed = bool(orphans)
            if orphans:
                session.execute(
                    delete(FileContent).where(FileContent.file_id.in_(orphans))
                )

            mirrors = {
                c.file_id: c for c in session.scalars(select(FileContent)).all()
            }
            for file in files:
                if not platform_fs.exists(file.full_path):
                    continue                             # preserve the stored copy

                mtime = platform_fs.mtime_utc(file.full_path)
                if mtime is None:
                    continue

                mirror = mirrors.get(file.id)
                if mirror is not None and mirror.source_write_utc == mtime:
                    continue                             # unchanged

                if self._upsert_from_disk(session, file, mirror, mtime):
                    changed = True

            if changed:
                session.commit()

    def _refresh_watches(self, files: list[ManagedFile]) -> None:
        if self._observer is None:
            return

        wanted: dict[str, str] = {}
        for file in files:
            directory = os.path.dirname(file.full_path)
            if directory and os.path.isdir(directory):
                wanted[platform_fs.path_key(directory)] = directory

        for key in list(self._watches):
            if key in wanted:
                continue
            try:
                self._observer.unschedule(self._watches[key])
            except Exception:  # noqa: BLE001
                log.debug("could not unwatch %s", key, exc_info=True)
            self._watches.pop(key, None)

        handler = _DirtyHandler(self.mark_dirty)
        for key, directory in wanted.items():
            if key in self._watches:
                continue
            try:
                self._watches[key] = self._observer.schedule(
                    handler, directory, recursive=False
                )
            except Exception:  # noqa: BLE001
                log.warning("could not watch directory %s", directory)

    # ------------------------------------------------------------------ shared
    def _upsert_from_disk(
        self,
        session,
        file: ManagedFile,
        mirror: FileContent | None,
        mtime: datetime,
    ) -> bool:
        """Read the file and write the mirror row.

        Returns whether anything needs committing. A transient read failure (a file caught
        mid-write) returns False and is retried by the next tick or sweep.
        """
        try:
            text = platform_fs.read_text_tolerant(file.full_path)
        except OSError:
            log.debug("deferred content read for %s", file.full_path, exc_info=True)
            return False

        content_hash = sha256_hex(text)
        now = utcnow()

        if mirror is None:
            session.add(FileContent(
                file_id=file.id,
                content=text,
                content_hash=content_hash,
                source_write_utc=mtime,
                synced_utc=now,
            ))
            self.indexer.enqueue(file.id)
            return True

        mirror.source_write_utc = mtime          # remember we have seen this mtime
        if mirror.content_hash == content_hash:
            return True                          # touched but identical: only mtime moved

        mirror.content = text
        mirror.content_hash = content_hash
        mirror.synced_utc = now

        # The content genuinely changed, so the document's vectors are now stale.
        self.indexer.enqueue(file.id)
        return True
