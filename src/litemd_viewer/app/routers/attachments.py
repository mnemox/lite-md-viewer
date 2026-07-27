"""Graph attachments: zip exports, uploads and on-disk file references."""

from __future__ import annotations

import mimetypes
import os
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db import get_session
from ..errors import bad_request, not_found, problem
from ..models import Attachment, AttachmentKind, ManagedFile, utcnow
from ..schemas import AddAttachmentReferenceRequest, AttachmentDto, ExportRequest
from ..services import platform_fs
from ..services.graph_service import GraphService

router = APIRouter(prefix="/api/files", tags=["attachments"])
downloads = APIRouter(prefix="/api/attachments", tags=["attachments"])


def to_dto(attachment: Attachment) -> AttachmentDto:
    return AttachmentDto(
        id=attachment.id,
        file_name=attachment.file_name,
        size_bytes=attachment.size_bytes,
        node_count=attachment.node_count,
        created_utc=attachment.created_utc,
        kind=attachment.kind,
        # "Missing" only means something for a reference whose target has gone away.
        missing=(attachment.kind == AttachmentKind.REFERENCE
                 and not platform_fs.exists(attachment.source_path)),
    )


def _slug(title: str | None) -> str:
    """Deterministic slug that MUST match the JS one, or index.html links break."""
    lowered = (title or "").lower()
    out = "".join(c if ("a" <= c <= "z" or "0" <= c <= "9") else "-" for c in lowered)
    while "--" in out:
        out = out.replace("--", "-")
    out = out.strip("-")
    return out or "doc"


def _export_file_name(file: ManagedFile) -> str:
    return f"{file.id}-{_slug(file.title)}.md"


@router.post("/{file_id}/export", response_model=AttachmentDto)
def export_graph(
    file_id: int, req: ExportRequest, session: Session = Depends(get_session)
) -> AttachmentDto:
    """Zip the active document's whole graph plus the client-built index.html."""
    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()
    if not req.index_html or not req.index_html.strip():
        raise bad_request("Missing index.html content.")

    graph = GraphService(session)
    graph_id = graph.get_or_create_graph(file_id)
    docs = [f for f in graph.get_graph_member_files(graph_id)
            if platform_fs.exists(f.full_path)]

    config.ensure_dirs()
    work_dir = config.ATTACHMENTS_DIR / "work" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.zip"
    stored_path = config.ATTACHMENTS_DIR / stored_name

    try:
        for doc in docs:
            shutil.copyfile(doc.full_path, work_dir / _export_file_name(doc))
        platform_fs.write_text(str(work_dir / "index.html"), req.index_html)
        # make_archive appends its own ".zip", so hand it the stem.
        shutil.make_archive(str(stored_path.with_suffix("")), "zip", root_dir=str(work_dir))
    except (OSError, shutil.Error) as exc:
        try:
            if stored_path.exists():
                stored_path.unlink()
        except OSError:
            pass  # best effort
        raise problem(f"Export failed: {exc}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    attachment = Attachment(
        graph_id=graph_id,
        kind=AttachmentKind.EXPORT,
        file_name=datetime.now(timezone.utc).strftime("%d-%m-%Y_%H-%M") + "-UTC.zip",
        stored_name=stored_name,
        size_bytes=platform_fs.file_size(str(stored_path)),
        node_count=len(docs),
        created_utc=utcnow(),
    )
    session.add(attachment)
    session.commit()
    return to_dto(attachment)


@router.get("/{file_id}/attachments", response_model=list[AttachmentDto])
def list_attachments(
    file_id: int, session: Session = Depends(get_session)
) -> list[AttachmentDto]:
    """Everything owned by this document's graph, newest first."""
    graph_id = GraphService(session).get_graph_id(file_id)
    if graph_id is None:
        return []
    rows = session.scalars(
        select(Attachment)
        .where(Attachment.graph_id == graph_id)
        .order_by(Attachment.created_utc.desc())
    ).all()
    return [to_dto(a) for a in rows]


@router.post("/{file_id}/attachments/reference", response_model=AttachmentDto)
def add_reference(
    file_id: int,
    req: AddAttachmentReferenceRequest,
    session: Session = Depends(get_session),
) -> AttachmentDto:
    """Attach an existing on-disk file by reference; only a record is stored."""
    if session.get(ManagedFile, file_id) is None:
        raise not_found()
    if not req.path or not req.path.strip():
        raise bad_request("Enter a path to a file.")

    try:
        full = platform_fs.canonical(req.path.strip())
    except (OSError, ValueError):
        raise bad_request("That path is not valid.")
    if not platform_fs.exists(full):
        raise bad_request("No file was found at that path.")

    graph_id = GraphService(session).get_or_create_graph(file_id)
    duplicate = session.scalar(
        select(Attachment.id).where(
            Attachment.graph_id == graph_id,
            Attachment.kind == AttachmentKind.REFERENCE,
            Attachment.source_path == full,
        )
    )
    if duplicate is not None:
        raise bad_request("That file is already attached.")

    attachment = Attachment(
        graph_id=graph_id,
        kind=AttachmentKind.REFERENCE,
        file_name=os.path.basename(full),
        stored_name="",
        source_path=full,
        size_bytes=platform_fs.file_size(full),
        created_utc=utcnow(),
    )
    session.add(attachment)
    session.commit()
    return to_dto(attachment)


@router.post("/{file_id}/attachments/upload", response_model=AttachmentDto)
async def upload_attachment(
    file_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> AttachmentDto:
    """Copy an uploaded file into the attachments folder and record it."""
    if session.get(ManagedFile, file_id) is None:
        raise not_found()

    display_name = os.path.basename(file.filename or "") or "file"
    graph_id = GraphService(session).get_or_create_graph(file_id)

    config.ensure_dirs()
    stored_name = uuid.uuid4().hex + os.path.splitext(display_name)[1]
    stored_path = config.ATTACHMENTS_DIR / stored_name

    size = 0
    try:
        with open(stored_path, "xb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                target.write(chunk)
    except OSError as exc:
        try:
            if stored_path.exists():
                stored_path.unlink()
        except OSError:
            pass  # best effort
        raise problem(f"Upload failed: {exc}")
    finally:
        await file.close()

    if size == 0:
        try:
            stored_path.unlink()
        except OSError:
            pass
        raise bad_request("No file was uploaded.")

    attachment = Attachment(
        graph_id=graph_id,
        kind=AttachmentKind.UPLOAD,
        file_name=display_name,
        stored_name=stored_name,
        size_bytes=size,
        created_utc=utcnow(),
    )
    session.add(attachment)
    session.commit()
    return to_dto(attachment)


@downloads.get("/{attachment_id}/download")
def download_attachment(
    attachment_id: int, session: Session = Depends(get_session)
) -> FileResponse:
    """References stream from their source path; exports and uploads from the stored copy."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise not_found()

    if attachment.kind == AttachmentKind.REFERENCE:
        path = attachment.source_path or ""
    else:
        path = str(config.ATTACHMENTS_DIR / attachment.stored_name)

    if not path or not platform_fs.exists(path):
        raise not_found("Attachment file not found.")

    media_type = mimetypes.guess_type(attachment.file_name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=attachment.file_name)


@downloads.delete("/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: int, session: Session = Depends(get_session)
) -> Response:
    """References drop only the record; exports and uploads also delete the stored file."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise not_found()

    if attachment.kind != AttachmentKind.REFERENCE and attachment.stored_name:
        try:
            stored = config.ATTACHMENTS_DIR / attachment.stored_name
            if stored.is_file():
                stored.unlink()
        except OSError:
            pass  # best effort

    session.delete(attachment)
    session.commit()
    return Response(status_code=204)
