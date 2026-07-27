"""Graph relations, companions and imported colour maps."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import bad_request, not_found
from ..models import ManagedFile
from ..schemas import (
    AddColorMapRequest,
    AddRelationRequest,
    ColorMapDto,
    GraphDto,
)
from ..services.graph_service import COMPANION, GraphService, canonical_relation

router = APIRouter(prefix="/api/files", tags=["relations"])


@router.get("/{file_id}/graph", response_model=GraphDto)
def get_graph(file_id: int, session: Session = Depends(get_session)) -> GraphDto:
    """The graph this document belongs to, plus that graph's companions.

    Edges and companions are graph-level, so every member sees the same set. A document
    with no graph yet returns a singleton view of itself.
    """
    if session.get(ManagedFile, file_id) is None:
        raise not_found()
    return GraphService(session).build_graph_dto(file_id)


@router.post("/{file_id}/relations", status_code=204)
def add_relation(
    file_id: int, req: AddRelationRequest, session: Session = Depends(get_session)
) -> Response:
    """Link this document to another (parent|child|reference|sibling|companion)."""
    if req.other_id == file_id:
        raise bad_request("A document cannot link to itself.")
    if session.get(ManagedFile, file_id) is None or \
            session.get(ManagedFile, req.other_id) is None:
        raise not_found("Document not found.")

    from_id, to_id, kind = canonical_relation(file_id, req.other_id, req.kind)
    if kind is None:
        raise bad_request("Unknown relation kind.")

    graph = GraphService(session)
    if kind == COMPANION:
        # A companion attaches the other document to *this* document's graph.
        ok, error = graph.add_companion(file_id, req.other_id)
        if not ok:
            raise bad_request(error or "Could not add companion.")
        return Response(status_code=204)

    graph.add_edge(from_id, to_id, kind)
    return Response(status_code=204)


@router.delete("/{file_id}/relations", status_code=204)
def remove_relation(
    file_id: int,
    # Query parameters are not covered by the schema-wide camelCase alias generator, and
    # api.js sends `?otherId=...`, so the alias has to be spelled out here.
    other_id: int = Query(..., alias="otherId"),
    kind: str = Query(...),
    session: Session = Depends(get_session),
) -> Response:
    from_id, to_id, resolved = canonical_relation(file_id, other_id, kind)
    if resolved is None:
        raise bad_request("Unknown relation kind.")

    graph = GraphService(session)
    if resolved == COMPANION:
        graph.remove_companion(file_id, other_id)
    else:
        graph.remove_edge(from_id, to_id, resolved)
    return Response(status_code=204)


@router.delete("/{file_id}/graph", status_code=204)
def remove_from_graph(file_id: int, session: Session = Depends(get_session)) -> Response:
    """Detach this document from its graph entirely; the document itself is kept."""
    GraphService(session).remove_member(file_id)
    return Response(status_code=204)


@router.get("/{file_id}/colormaps", response_model=list[ColorMapDto])
def get_color_maps(
    file_id: int, session: Session = Depends(get_session)
) -> list[ColorMapDto]:
    if session.get(ManagedFile, file_id) is None:
        raise not_found()
    return GraphService(session).get_color_maps(file_id)


@router.post("/{file_id}/colormaps", response_model=ColorMapDto)
def add_color_map(
    file_id: int, req: AddColorMapRequest, session: Session = Depends(get_session)
) -> ColorMapDto:
    """Import a colours-schema JSON file by path and attach it to this document's graph."""
    if session.get(ManagedFile, file_id) is None:
        raise not_found()

    ok, error, color_map = GraphService(session).add_color_map(file_id, req.path)
    if not ok or color_map is None:
        raise bad_request(error or "Could not import the colors file.")
    return color_map


@router.delete("/{file_id}/colormaps/{map_id}", status_code=204)
def remove_color_map(
    file_id: int, map_id: int, session: Session = Depends(get_session)
) -> Response:
    GraphService(session).remove_color_map(file_id, map_id)
    return Response(status_code=204)
