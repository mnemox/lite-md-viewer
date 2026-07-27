"""Filesystem helpers, including the few places where .NET had no stdlib equivalent.

`DriveInfo.GetDrives()` and `FileAttributes.Hidden | System` are Win32 concepts; both are
reproduced here, with POSIX fallbacks so the module still imports and behaves sensibly
elsewhere.
"""

from __future__ import annotations

import os
import stat
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

_HIDDEN_MASK = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_SYSTEM_MASK = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)


def canonical(path: str) -> str:
    """The absolute, normalized form of a path, as Path.GetFullPath produced."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


def same_path(a: str | None, b: str | None) -> bool:
    """Case-insensitive path comparison, matching the app's OrdinalIgnoreCase usage."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def path_key(path: str) -> str:
    """A dictionary/set key that compares paths case-insensitively."""
    return os.path.normcase(os.path.normpath(path))


def list_drives() -> list[tuple[str, str]]:
    """Ready drive roots as (display name, root path).

    Equivalent to DriveInfo.GetDrives().Where(d => d.IsReady). On Windows the logical-drive
    bitmask is read from the kernel; an empty optical drive is filtered out because its root
    does not resolve.
    """
    if not IS_WINDOWS:
        return [("/", "/")]

    roots: list[tuple[str, str]] = []
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        mask = 0

    if mask:
        for i, letter in enumerate(string.ascii_uppercase):
            if not (mask >> i) & 1:
                continue
            root = f"{letter}:\\"
            if os.path.exists(root):
                roots.append((root, root))
    else:
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                roots.append((root, root))
    return roots


def is_hidden_or_system(path: str) -> bool:
    """True for entries the picker should not show. Unreadable entries count as hidden."""
    try:
        if IS_WINDOWS:
            attrs = os.stat(path).st_file_attributes  # type: ignore[attr-defined]
            return bool(attrs & (_HIDDEN_MASK | _SYSTEM_MASK))
        return os.path.basename(path).startswith(".")
    except OSError:
        return True


def is_accessible(directory: str) -> bool:
    """Whether the directory can be listed at all (used to grey out entries)."""
    try:
        with os.scandir(directory) as it:
            next(it, None)
        return True
    except OSError:
        return False


def read_text_tolerant(path: str) -> str:
    """Read a text file even while another process holds it open.

    Python's open() uses the CRT's default share mode on Windows, which permits concurrent
    readers and writers -- the equivalent of FileShare.ReadWrite | FileShare.Delete. A BOM is
    consumed if present and undecodable bytes are replaced rather than raising, so a file
    caught mid-write still yields something.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return handle.read()


def write_text(path: str, text: str) -> None:
    """Truncate-write, matching FileMode.Create and UTF-8 without a BOM.

    newline="" keeps the caller's line endings byte-for-byte instead of translating "\\n".
    """
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def mtime_utc(path: str) -> datetime | None:
    """Naive-UTC last write time, or None when the file cannot be stat'ed."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).replace(
            tzinfo=None
        )
    except OSError:
        return None


def created_utc_aware(path: str) -> datetime | None:
    """Timezone-aware creation time for the details modal.

    The client renders these with `new Date(...)`, which reads an offset-less timestamp as
    local time -- so these must carry an explicit UTC offset.
    """
    try:
        return datetime.fromtimestamp(os.path.getctime(path), tz=timezone.utc)
    except OSError:
        return None


def modified_utc_aware(path: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except OSError:
        return None


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def exists(path: str | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


INVALID_FILENAME_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


def is_valid_filename(name: str) -> bool:
    """Equivalent of checking Path.GetInvalidFileNameChars()."""
    return bool(name) and not (set(name) & INVALID_FILENAME_CHARS)
