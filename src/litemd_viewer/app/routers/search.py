"""Semantic and full-text search over the indexed document passages."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..schemas import IndexStatusDto, SearchResultDto
from ..services.indexing import get_indexer

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search", response_model=SearchResultDto)
def search(
    q: str = Query(default="", description="the search text"),
    k: int = Query(default=20, ge=1, le=100),
    mode: str = Query(default="hybrid", pattern="^(vector|text|hybrid)$"),
    # Query parameters are not covered by the schema-wide camelCase alias generator, and
    # api.js sends `?fileId=...`, so the alias has to be spelled out here.
    file_id: int | None = Query(
        default=None, alias="fileId",
        description="search only inside this document, returning its matching sections",
    ),
) -> SearchResultDto:
    """Search indexed passages.

    "vector" is pure nearest-neighbour, "text" is FTS only, and "hybrid" (the default) fuses
    the two so exact keyword matches and paraphrases both surface.

    Without `fileId` a hit is a document; with it a hit is a section of that one document,
    which is what a reader already inside a document wants to jump between.
    """
    return get_indexer().search(q, k=k, mode=mode, file_id=file_id)


@router.get("/search/status", response_model=IndexStatusDto)
def status() -> IndexStatusDto:
    """Whether the index is usable, how much is indexed, and what is still queued."""
    return IndexStatusDto(**get_indexer().status())


@router.post("/search/reindex")
def reindex(
    force: bool = Query(default=False, description="rebuild every document, not just stale ones"),
) -> dict:
    """Queue every document whose indexed content no longer matches its mirror.

    `force` rebuilds all of them, which is what picks up a change to the chunking or
    markdown-cleaning rules -- those leave the source text, and so the mirror hash, untouched.
    """
    return {"queued": get_indexer().reconcile(force=force)}
