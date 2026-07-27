"""Read-only server-side filesystem enumeration for the "Add file" picker.

The browser sandbox cannot hand JavaScript a real absolute path, so the user navigates the
real disk through this endpoint and the chosen absolute path is registered.
"""

from __future__ import annotations

import os

from .. import config
from ..schemas import BrowseEntry, BrowseResult
from . import platform_fs

JSON_EXT = (".json",)


def browse(path: str | None, kind: str | None = None) -> BrowseResult:
    """List a directory's sub-directories and matching files.

    `kind` selects the file filter: None/"md" for document files, "json" for colour schemas,
    "any" for every file. An unreadable or non-existent path falls back to the drive list
    rather than erroring, so the picker can always recover.
    """
    lowered = (kind or "").lower()
    if lowered == "json":
        extensions: tuple[str, ...] | None = JSON_EXT
    elif lowered == "any":
        extensions = None
    else:
        extensions = config.DOCUMENT_EXT

    if not path or not path.strip():
        return _list_drives()

    try:
        full = platform_fs.canonical(path)
    except (OSError, ValueError):
        return _list_drives()

    if not os.path.isdir(full):
        return _list_drives()

    directories: list[BrowseEntry] = []
    files: list[BrowseEntry] = []
    try:
        with os.scandir(full) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        if platform_fs.is_hidden_or_system(entry.path):
                            continue
                        directories.append(BrowseEntry(
                            name=entry.name,
                            path=entry.path,
                            is_dir=True,
                            is_markdown=False,
                            accessible=platform_fs.is_accessible(entry.path),
                        ))
                    else:
                        ext = os.path.splitext(entry.name)[1].lower()
                        if extensions is not None and ext not in extensions:
                            continue
                        files.append(BrowseEntry(
                            name=entry.name,
                            path=entry.path,
                            is_dir=False,
                            is_markdown=True,
                            accessible=True,
                        ))
                except OSError:
                    continue
    except OSError:
        # Permission denied or a disconnected drive: show the folder as empty.
        pass

    directories.sort(key=lambda e: e.name.lower())
    files.sort(key=lambda e: e.name.lower())

    parent = os.path.dirname(full)
    if parent == full:  # a drive root has no parent
        parent = None

    return BrowseResult(path=full, parent=parent, is_root=False,
                        entries=directories + files)


def _list_drives() -> BrowseResult:
    entries = [
        BrowseEntry(name=name, path=root, is_dir=True, is_markdown=False, accessible=True)
        for name, root in platform_fs.list_drives()
    ]
    return BrowseResult(path=None, parent=None, is_root=True, entries=entries)
