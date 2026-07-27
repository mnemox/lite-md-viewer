"""Server-side disk browsing for the file picker."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..schemas import BrowseResult
from ..services import fs_browser

router = APIRouter(prefix="/api", tags=["browse"])


@router.get("/browse", response_model=BrowseResult)
def browse(
    path: str | None = Query(default=None),
    kind: str | None = Query(default=None),
) -> BrowseResult:
    return fs_browser.browse(path, kind)
