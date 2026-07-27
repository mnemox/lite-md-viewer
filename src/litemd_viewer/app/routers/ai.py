"""Local analysis: a chat over one open document, answered by a local Ollama model.

Entirely opt-in (config.AI_ENABLED, set by run.bat's setup wizard) and entirely local: the
document text only ever goes to http://127.0.0.1:11434, never off-box.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db import SessionLocal, get_session
from ..errors import bad_request, not_found
from ..models import AiChatMessage, AiChatRole, ManagedFile
from ..schemas import AiMessageDto, AiSourceDto, AiStatusDto, AskRequest
from ..services import ollama_client
from ..services.ai_chat import build_context, build_messages

log = logging.getLogger("litemdviewer.ai")

router = APIRouter(prefix="/api", tags=["ai"])
chat_router = APIRouter(prefix="/api/files/{file_id}/chat", tags=["ai"])


def _to_dto(message: AiChatMessage) -> AiMessageDto:
    sources = []
    if message.sources:
        try:
            sources = [AiSourceDto(**s) for s in json.loads(message.sources)]
        except (ValueError, TypeError):
            sources = []
    return AiMessageDto(
        id=message.id, role=message.role, text=message.text,
        sources=sources, created_utc=message.created_utc,
    )


@router.get("/ai/status", response_model=AiStatusDto)
async def ai_status() -> AiStatusDto:
    """Whether local analysis is enabled and, if so, whether Ollama is reachable."""
    if not config.AI_ENABLED:
        return AiStatusDto(enabled=False, reachable=False, model=None, model_present=False)

    result = await ollama_client.health(config.OLLAMA_MODEL)
    return AiStatusDto(
        enabled=True,
        reachable=result["reachable"],
        model=config.OLLAMA_MODEL,
        model_present=result["model_present"],
        error=result["error"],
    )


@chat_router.get("", response_model=list[AiMessageDto])
def get_chat(file_id: int, session: Session = Depends(get_session)) -> list[AiMessageDto]:
    if session.get(ManagedFile, file_id) is None:
        raise not_found()
    rows = session.scalars(
        select(AiChatMessage)
        .where(AiChatMessage.file_id == file_id)
        .order_by(AiChatMessage.id)
    ).all()
    return [_to_dto(m) for m in rows]


@chat_router.delete("", status_code=204)
def clear_chat(file_id: int, session: Session = Depends(get_session)) -> Response:
    if session.get(ManagedFile, file_id) is None:
        raise not_found()
    session.query(AiChatMessage).filter(AiChatMessage.file_id == file_id).delete()
    session.commit()
    return Response(status_code=204)


@chat_router.post("")
def ask(
    file_id: int, req: AskRequest, session: Session = Depends(get_session)
) -> StreamingResponse:
    """Ask a question about the open document; the answer streams back as NDJSON.

    Line shapes: {"type":"sources","sources":[...]}, {"type":"delta","text":...},
    {"type":"done","messageId":...}, {"type":"error","error":...}.
    """
    if not config.AI_ENABLED:
        raise bad_request("Local analysis is not enabled.")

    file = session.get(ManagedFile, file_id)
    if file is None:
        raise not_found()

    question = (req.question or "").strip()
    if not question:
        raise bad_request("A question is required.")

    context_text, sources = build_context(session, file, question)

    history_rows = session.scalars(
        select(AiChatMessage)
        .where(AiChatMessage.file_id == file_id)
        .order_by(AiChatMessage.id.desc())
        .limit(config.AI_HISTORY_TURNS)
    ).all()
    history = [(m.role, m.text) for m in reversed(history_rows)]

    session.add(AiChatMessage(file_id=file_id, role=AiChatRole.USER, text=question))
    session.commit()

    messages = build_messages(file.title, context_text, history, question)
    sources_payload = [s.model_dump(by_alias=True) for s in sources]

    async def body():
        yield json.dumps({"type": "sources", "sources": sources_payload}) + "\n"
        answer_parts: list[str] = []
        error: str | None = None
        try:
            async for delta in ollama_client.chat_stream(
                messages, model=config.OLLAMA_MODEL, num_ctx=config.AI_NUM_CTX
            ):
                answer_parts.append(delta)
                yield json.dumps({"type": "delta", "text": delta}) + "\n"
        except ollama_client.OllamaError as exc:
            error = str(exc)
            log.warning("chat_stream failed: %s", error)
            yield json.dumps({"type": "error", "error": error}) + "\n"

        answer = "".join(answer_parts)
        # A separate session: the Depends(get_session) one may already be torn down by the
        # time this generator finishes draining, since it is not tied to the streamed body.
        if answer:
            with SessionLocal() as write_session:
                assistant = AiChatMessage(
                    file_id=file_id, role=AiChatRole.ASSISTANT, text=answer,
                    sources=json.dumps(sources_payload) if sources_payload else None,
                )
                write_session.add(assistant)
                write_session.commit()
                yield json.dumps({"type": "done", "messageId": assistant.id}) + "\n"
        elif error is None:
            yield json.dumps({"type": "done", "messageId": None}) + "\n"

    return StreamingResponse(body(), media_type="application/x-ndjson")
