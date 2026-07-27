"""The explicit-graph model.

Each document belongs to at most one graph (via `graph_members`), and a graph owns its
reference/sibling edges, companion documents, colour maps and export attachments. Linking
two documents from different graphs merges the graphs; removing a single edge never splits a
graph; removing the last member garbage-collects the graph, its rows and its stored files.
"""

from __future__ import annotations

import json
import os

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import config
from ..models import (
    Attachment,
    AttachmentKind,
    Graph,
    GraphColorMap,
    GraphCompanion,
    GraphEdge,
    GraphEdgeKind,
    GraphMember,
    ManagedFile,
    utcnow,
)
from ..schemas import (
    ColorFileDto,
    ColorLegendDto,
    ColorMapDto,
    GraphDto,
    RelationEdgeDto,
    RelationNodeDto,
)
from . import platform_fs

COMPANION = "companion"

# .NET's JsonSerializer emits compact JSON; matching it keeps the stored snapshot for a
# migrated colour map byte-identical, so the freshness check below does not rewrite it.
_JSON_SEPARATORS = (",", ":")


class GraphService:
    def __init__(self, session: Session) -> None:
        self.db = session

    # ------------------------------------------------------------------ membership
    def get_graph_id(self, file_id: int) -> int | None:
        return self.db.scalar(
            select(GraphMember.graph_id).where(GraphMember.file_id == file_id)
        )

    def get_or_create_graph(self, file_id: int) -> int:
        existing = self.get_graph_id(file_id)
        if existing is not None:
            return existing
        graph = Graph(created_utc=utcnow())
        self.db.add(graph)
        self.db.flush()  # assigns graph.id
        self.db.add(GraphMember(graph_id=graph.id, file_id=file_id))
        self.db.commit()
        return graph.id

    def add_edge(self, from_id: int, to_id: int, kind: str) -> int:
        """Add an already-canonicalized edge, merging the two documents' graphs if needed."""
        graph_from = self.get_or_create_graph(from_id)
        graph_to = self.get_or_create_graph(to_id)

        graph_id = graph_from
        if graph_from != graph_to:
            target, source = self._bigger_first(graph_from, graph_to)
            graph_id = self.merge(target, source)

        duplicate = self.db.scalar(
            select(GraphEdge.id).where(
                GraphEdge.from_id == from_id,
                GraphEdge.to_id == to_id,
                GraphEdge.kind == kind,
            )
        )
        if duplicate is None:
            self.db.add(GraphEdge(graph_id=graph_id, from_id=from_id, to_id=to_id, kind=kind))
            self.db.commit()

        self.reconcile_companions_vs_members(graph_id)
        return graph_id

    def _bigger_first(self, a: int, b: int) -> tuple[int, int]:
        """Merge the smaller graph into the larger one, so fewer rows move."""
        count_a = self.db.scalar(
            select(func.count()).select_from(GraphMember).where(GraphMember.graph_id == a)
        ) or 0
        count_b = self.db.scalar(
            select(func.count()).select_from(GraphMember).where(GraphMember.graph_id == b)
        ) or 0
        return (a, b) if count_a >= count_b else (b, a)

    def merge(self, target: int, source: int) -> int:
        """Move source's rows onto target, then delete source."""
        if target == source:
            return target

        for model in (GraphMember, GraphEdge, Attachment, GraphColorMap):
            self.db.execute(
                update(model).where(model.graph_id == source).values(graph_id=target)
            )

        # Companions: reassign, dropping any the target already has.
        existing = set(self.db.scalars(
            select(GraphCompanion.file_id).where(GraphCompanion.graph_id == target)
        ).all())
        for companion in self.db.scalars(
            select(GraphCompanion).where(GraphCompanion.graph_id == source)
        ).all():
            if companion.file_id in existing:
                self.db.delete(companion)
            else:
                companion.graph_id = target
                existing.add(companion.file_id)

        source_graph = self.db.get(Graph, source)
        if source_graph is not None:
            self.db.delete(source_graph)
        self.db.commit()

        self.reconcile_companions_vs_members(target)
        return target

    def reconcile_companions_vs_members(self, graph_id: int) -> None:
        """A companion that is now also a member of the same graph is redundant."""
        members = select(GraphMember.file_id).where(GraphMember.graph_id == graph_id)
        self.db.execute(
            delete(GraphCompanion).where(
                GraphCompanion.graph_id == graph_id,
                GraphCompanion.file_id.in_(members),
            )
        )
        self.db.commit()

    def remove_edge(self, from_id: int, to_id: int, kind: str) -> None:
        """Remove a single edge. Members stay; the graph is never split."""
        self.db.execute(
            delete(GraphEdge).where(
                GraphEdge.from_id == from_id,
                GraphEdge.to_id == to_id,
                GraphEdge.kind == kind,
            )
        )
        self.db.commit()

    # ------------------------------------------------------------------ companions
    def add_companion(self, owner_file_id: int, companion_file_id: int) -> tuple[bool, str | None]:
        if owner_file_id == companion_file_id:
            return False, "A document cannot be its own companion."

        graph_id = self.get_or_create_graph(owner_file_id)

        is_member = self.db.scalar(
            select(GraphMember.id).where(
                GraphMember.graph_id == graph_id,
                GraphMember.file_id == companion_file_id,
            )
        )
        if is_member is not None:
            return False, "That document is already in the graph."

        already = self.db.scalar(
            select(GraphCompanion.id).where(
                GraphCompanion.graph_id == graph_id,
                GraphCompanion.file_id == companion_file_id,
            )
        )
        if already is not None:
            return True, None  # idempotent

        self.db.add(GraphCompanion(graph_id=graph_id, file_id=companion_file_id))
        self.db.commit()
        return True, None

    def remove_companion(self, owner_file_id: int, companion_file_id: int) -> None:
        graph_id = self.get_graph_id(owner_file_id)
        if graph_id is None:
            return
        self.db.execute(
            delete(GraphCompanion).where(
                GraphCompanion.graph_id == graph_id,
                GraphCompanion.file_id == companion_file_id,
            )
        )
        self.db.commit()

    # ------------------------------------------------------------------ removal
    def remove_member(self, file_id: int) -> None:
        """"Remove from graph": detach a member and its edges, GC the graph if now empty."""
        graph_id = self.get_graph_id(file_id)
        if graph_id is None:
            return

        self.db.execute(
            delete(GraphEdge).where(
                GraphEdge.graph_id == graph_id,
                (GraphEdge.from_id == file_id) | (GraphEdge.to_id == file_id),
            )
        )
        self.db.execute(delete(GraphMember).where(GraphMember.file_id == file_id))
        self.db.commit()

        remaining = self.db.scalar(
            select(GraphMember.id).where(GraphMember.graph_id == graph_id)
        )
        if remaining is None:
            self.gc_graph(graph_id)

    def gc_graph(self, graph_id: int) -> None:
        """Delete an emptied graph: its stored attachment files plus all of its rows.

        Reference attachments only point at files elsewhere on disk, so those are never
        deleted -- only the record is.
        """
        for attachment in self.db.scalars(
            select(Attachment).where(Attachment.graph_id == graph_id)
        ).all():
            if attachment.kind == AttachmentKind.REFERENCE or not attachment.stored_name:
                continue
            try:
                stored = config.ATTACHMENTS_DIR / attachment.stored_name
                if stored.is_file():
                    stored.unlink()
            except OSError:
                pass  # best effort

        for model in (Attachment, GraphCompanion, GraphColorMap):
            self.db.execute(delete(model).where(model.graph_id == graph_id))
        self.db.execute(delete(Graph).where(Graph.id == graph_id))
        self.db.commit()

    def remove_file_everywhere(self, file_id: int) -> None:
        """For file deletion: detach the member (GC if last) and drop its companion roles."""
        self.remove_member(file_id)
        self.db.execute(delete(GraphCompanion).where(GraphCompanion.file_id == file_id))
        self.db.commit()

    # ------------------------------------------------------------------ projection
    def get_graph_member_files(self, graph_id: int) -> list[ManagedFile]:
        ids = select(GraphMember.file_id).where(GraphMember.graph_id == graph_id)
        return list(self.db.scalars(select(ManagedFile).where(ManagedFile.id.in_(ids))).all())

    def build_graph_dto(self, active_id: int) -> GraphDto:
        """The graph the document belongs to, or a singleton view when it has none."""
        graph_id = self.get_graph_id(active_id)
        if graph_id is None:
            solo = self.db.get(ManagedFile, active_id)
            nodes = [_node(solo)] if solo is not None else []
            return GraphDto(active_id=active_id, nodes=nodes, edges=[], companions=[])

        by_id = {f.id: f for f in self.db.scalars(select(ManagedFile)).all()}
        member_ids = self.db.scalars(
            select(GraphMember.file_id).where(GraphMember.graph_id == graph_id)
        ).all()
        companion_ids = self.db.scalars(
            select(GraphCompanion.file_id).where(GraphCompanion.graph_id == graph_id)
        ).all()
        edges = [
            RelationEdgeDto(from_id=e.from_id, to_id=e.to_id, kind=e.kind)
            for e in self.db.scalars(
                select(GraphEdge).where(GraphEdge.graph_id == graph_id)
            ).all()
        ]

        return GraphDto(
            active_id=active_id,
            nodes=[_node(by_id[i]) for i in member_ids if i in by_id],
            edges=edges,
            companions=[_node(by_id[i]) for i in companion_ids if i in by_id],
        )

    # ------------------------------------------------------------------ colour maps
    def get_color_maps(self, active_id: int) -> list[ColorMapDto]:
        graph_id = self.get_graph_id(active_id)
        if graph_id is None:
            return []

        rows = self.db.scalars(
            select(GraphColorMap)
            .where(GraphColorMap.graph_id == graph_id)
            .order_by(GraphColorMap.id)
        ).all()

        result: list[ColorMapDto] = []
        dirty = False
        for row in rows:
            # Re-read the referenced file live so edits to it show up, refreshing the stored
            # snapshot when it changes. Fall back to the snapshot if the file is unreadable.
            if row.file_path and os.path.isfile(row.file_path):
                try:
                    name, legend, files = _parse_color_schema_text(
                        platform_fs.read_text_tolerant(row.file_path),
                        os.path.basename(row.file_path),
                    )
                    if files:
                        fresh = json.dumps(
                            {
                                "legend": [
                                    {"color": item.color, "meaning": item.meaning}
                                    for item in legend
                                ],
                                "files": [
                                    {"filePath": item.file_path, "color": item.color}
                                    for item in files
                                ],
                            },
                            separators=_JSON_SEPARATORS,
                        )
                        if row.json != fresh or row.list_name != name:
                            row.json = fresh
                            row.list_name = name
                            dirty = True
                        result.append(ColorMapDto(
                            id=row.id, list_name=name, file_path=row.file_path,
                            legend=legend, files=files,
                        ))
                        continue
                except (OSError, ValueError):
                    pass  # fall through to the stored snapshot

            legend, files = _parse_color_schema(row.json)
            result.append(ColorMapDto(
                id=row.id, list_name=row.list_name, file_path=row.file_path,
                legend=legend, files=files,
            ))

        if dirty:
            self.db.commit()
        return result

    def add_color_map(
        self, active_id: int, path: str | None
    ) -> tuple[bool, str | None, ColorMapDto | None]:
        if not path or not path.strip():
            return False, "Enter a path to a colors JSON file.", None

        try:
            full = platform_fs.canonical(path.strip())
        except (OSError, ValueError):
            return False, "That path is not valid.", None

        if not os.path.isfile(full):
            return False, "No file was found at that path.", None

        try:
            raw = platform_fs.read_text_tolerant(full)
        except OSError as exc:
            return False, f"Could not read the colors file: {exc}", None

        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            return False, f"Could not read the colors file: {exc}", None
        if not isinstance(parsed, dict):
            return False, "The colors file is empty or invalid.", None

        name, legend, files = _parse_color_schema_obj(parsed, os.path.basename(full))
        if not files:
            return False, "The colors file has no file/color entries to map.", None

        graph_id = self.get_or_create_graph(active_id)
        stored = json.dumps(
            {
                "legend": [{"color": i.color, "meaning": i.meaning} for i in legend],
                "files": [{"filePath": i.file_path, "color": i.color} for i in files],
            },
            separators=_JSON_SEPARATORS,
        )
        row = GraphColorMap(
            graph_id=graph_id, file_path=full, list_name=name,
            json=stored, created_utc=utcnow(),
        )
        self.db.add(row)
        self.db.commit()

        return True, None, ColorMapDto(
            id=row.id, list_name=name, file_path=full, legend=legend, files=files
        )

    def remove_color_map(self, active_id: int, map_id: int) -> None:
        graph_id = self.get_graph_id(active_id)
        if graph_id is None:
            return
        self.db.execute(
            delete(GraphColorMap).where(
                GraphColorMap.id == map_id, GraphColorMap.graph_id == graph_id
            )
        )
        self.db.commit()


# ---------------------------------------------------------------------- helpers
def _node(file: ManagedFile) -> RelationNodeDto:
    return RelationNodeDto(
        id=file.id,
        title=file.title,
        missing=not platform_fs.exists(file.full_path),
        path=file.full_path,
    )


def _ci(obj: dict, key: str):
    """Look a key up case-insensitively, as PropertyNameCaseInsensitive did."""
    if key in obj:
        return obj[key]
    lowered = key.lower()
    for k, v in obj.items():
        if isinstance(k, str) and k.lower() == lowered:
            return v
    return None


def _parse_color_schema_obj(
    doc: dict, fallback_name: str
) -> tuple[str, list[ColorLegendDto], list[ColorFileDto]]:
    raw_legend = _ci(doc, "legend") or []
    raw_files = _ci(doc, "files") or []

    legend = [
        ColorLegendDto(color=str(_ci(i, "color") or ""), meaning=str(_ci(i, "meaning") or ""))
        for i in raw_legend
        if isinstance(i, dict)
    ]
    files = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        file_path = _ci(item, "filePath")
        color = _ci(item, "color")
        if not file_path or not str(file_path).strip():
            continue
        if not color or not str(color).strip():
            continue
        files.append(ColorFileDto(file_path=str(file_path), color=str(color)))

    list_name = _ci(doc, "listName")
    name = str(list_name).strip() if list_name and str(list_name).strip() else fallback_name
    return name, legend, files


def _parse_color_schema_text(
    text: str, fallback_name: str
) -> tuple[str, list[ColorLegendDto], list[ColorFileDto]]:
    doc = json.loads(text)
    if not isinstance(doc, dict):
        return fallback_name, [], []
    return _parse_color_schema_obj(doc, fallback_name)


def _parse_color_schema(raw: str) -> tuple[list[ColorLegendDto], list[ColorFileDto]]:
    """Parse a stored snapshot, tolerating anything malformed."""
    try:
        doc = json.loads(raw or "{}")
        if not isinstance(doc, dict):
            return [], []
        _, legend, files = _parse_color_schema_obj(doc, "")
        return legend, files
    except ValueError:
        return [], []


def canonical_relation(
    file_id: int, other_id: int, ui_kind: str
) -> tuple[int, int, str | None]:
    """Map a UI relation kind (relative to `file_id`) onto a stored edge.

    "companion" is not a graph edge; it is signalled with the COMPANION sentinel and handled
    separately by the caller. An unknown kind yields None.
    """
    if ui_kind == "parent":
        return other_id, file_id, GraphEdgeKind.REFERENCE     # other -> this
    if ui_kind == "child":
        return file_id, other_id, GraphEdgeKind.REFERENCE     # this -> other
    if ui_kind == "reference":
        return file_id, other_id, GraphEdgeKind.REFERENCE
    if ui_kind == "sibling":
        low, high = (file_id, other_id) if file_id <= other_id else (other_id, file_id)
        return low, high, GraphEdgeKind.SIBLING
    if ui_kind == "companion":
        return file_id, other_id, COMPANION
    return 0, 0, None


__all__ = ["GraphService", "canonical_relation", "COMPANION"]
