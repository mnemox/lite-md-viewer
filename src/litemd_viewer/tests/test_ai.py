"""Local analysis: the Ollama-backed chat over one open document.

No test touches the network. `ollama_client.chat_stream` / `.health` are monkeypatched with
fixed async stand-ins, and `ai_chat.get_indexer` is swapped for a stub whose `.search()`
returns canned hits -- the same shape the real indexer produces.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import SessionLocal
from app.models import AiChatMessage, AiChatRole, FileContent, ManagedFile, utcnow
from app.routers import ai as ai_router
from app.schemas import SearchHitDto, SearchResultDto
from app.services import ai_chat, ollama_client


@pytest.fixture()
def client():
    from app.main import app
    return TestClient(app, client=("127.0.0.1", 54321))


@pytest.fixture(autouse=True)
def ai_enabled(monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", True)


def make_document(text: str, name: str = "doc.md") -> ManagedFile:
    with SessionLocal() as session:
        file = ManagedFile(full_path=f"C:\\docs\\{name}", title=name[:-3], added_utc=utcnow())
        session.add(file)
        session.flush()
        session.add(FileContent(
            file_id=file.id, content=text, content_hash="x",
            source_write_utc=utcnow(), synced_utc=utcnow(),
        ))
        session.commit()
        session.refresh(file)
        return file


async def _fake_health_ok(model=None):
    return {"reachable": True, "model_present": True, "error": None}


async def _fake_health_down(model=None):
    return {"reachable": False, "model_present": False, "error": "connection refused"}


# ---------------------------------------------------------------------- /api/ai/status
def test_status_disabled_by_default(monkeypatch, client):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    resp = client.get("/api/ai/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["reachable"] is False


def test_status_enabled_and_reachable(monkeypatch, client):
    monkeypatch.setattr(ollama_client, "health", _fake_health_ok)
    resp = client.get("/api/ai/status")
    body = resp.json()
    assert body["enabled"] is True
    assert body["reachable"] is True
    assert body["modelPresent"] is True
    assert body["model"] == config.OLLAMA_MODEL


def test_status_reports_unreachable(monkeypatch, client):
    monkeypatch.setattr(ollama_client, "health", _fake_health_down)
    resp = client.get("/api/ai/status")
    body = resp.json()
    assert body["enabled"] is True
    assert body["reachable"] is False
    assert body["error"] == "connection refused"


# ---------------------------------------------------------------------- build_context
def test_build_context_returns_whole_document_when_it_fits():
    file = make_document("short document text")
    with SessionLocal() as session:
        managed = session.get(ManagedFile, file.id)
        context, sources = ai_chat.build_context(session, managed, "what is this about?")
    assert context == "short document text"
    assert sources == []


def test_build_context_retrieves_sections_when_too_large(monkeypatch):
    # Small enough to force retrieval, but big enough to fit the first (short) excerpt.
    monkeypatch.setattr(config, "AI_CONTEXT_CHARS", 40)
    file = make_document("x" * 500)

    class StubIndexer:
        def search(self, query, k=8, mode="hybrid", file_id=None):
            return SearchResultDto(query=query, mode=mode, hits=[
                SearchHitDto(
                    file_id=file_id, title="doc", full_path="C:\\docs\\doc.md",
                    missing=False, chunk_id=1, chunk_index=0, start_offset=0,
                    snippet="first relevant excerpt", score=1.0,
                    section_path=["Intro"],
                ),
                SearchHitDto(
                    file_id=file_id, title="doc", full_path="C:\\docs\\doc.md",
                    missing=False, chunk_id=2, chunk_index=1, start_offset=50,
                    snippet="second relevant excerpt, quite a bit longer than the budget allows",
                    score=0.9,
                    section_path=["Details"],
                ),
            ])

    monkeypatch.setattr(ai_chat, "get_indexer", lambda: StubIndexer())

    with SessionLocal() as session:
        managed = session.get(ManagedFile, file.id)
        context, sources = ai_chat.build_context(session, managed, "question")

    assert "Intro" in context
    assert "first relevant excerpt" in context
    assert "Details" not in context
    assert len(sources) == 1
    assert sources[0].section_path == ["Intro"]


# ---------------------------------------------------------------------- POST chat (ask)
def _patch_ask(monkeypatch, deltas=("Hello", " world"), context="doc text", sources=None):
    monkeypatch.setattr(
        ai_router, "build_context", lambda session, file, question: (context, sources or [])
    )

    async def fake_stream(messages, model=None, num_ctx=None):
        for d in deltas:
            yield d

    monkeypatch.setattr(ollama_client, "chat_stream", fake_stream)


def test_ask_persists_user_then_assistant_message(monkeypatch, client):
    file = make_document("hello world document")
    _patch_ask(monkeypatch)

    with client.stream(
        "POST", f"/api/files/{file.id}/chat", json={"question": "hi?"}
    ) as resp:
        lines = [json.loads(line) for line in resp.iter_lines() if line]

    assert lines[0]["type"] == "sources"
    deltas = [line["text"] for line in lines if line["type"] == "delta"]
    assert "".join(deltas) == "Hello world"
    assert lines[-1]["type"] == "done"

    with SessionLocal() as session:
        rows = session.query(AiChatMessage).filter(
            AiChatMessage.file_id == file.id
        ).order_by(AiChatMessage.id).all()
    assert [r.role for r in rows] == [AiChatRole.USER, AiChatRole.ASSISTANT]
    assert rows[0].text == "hi?"
    assert rows[1].text == "Hello world"


def test_ask_reports_error_without_crashing(monkeypatch, client):
    file = make_document("doc")
    monkeypatch.setattr(
        ai_router, "build_context", lambda session, file, question: ("doc", [])
    )

    async def failing_stream(messages, model=None, num_ctx=None):
        raise ollama_client.OllamaError("Could not reach Ollama.")
        yield  # pragma: no cover - unreachable, keeps this an async generator

    monkeypatch.setattr(ollama_client, "chat_stream", failing_stream)

    with client.stream(
        "POST", f"/api/files/{file.id}/chat", json={"question": "hi?"}
    ) as resp:
        assert resp.status_code == 200
        lines = [json.loads(line) for line in resp.iter_lines() if line]

    assert any(line["type"] == "error" for line in lines)

    with SessionLocal() as session:
        rows = session.query(AiChatMessage).filter(AiChatMessage.file_id == file.id).all()
    # the question is saved even though the answer failed; no assistant row without an answer
    assert len(rows) == 1
    assert rows[0].role == AiChatRole.USER


def test_ask_requires_a_question(client):
    file = make_document("doc")
    resp = client.post(f"/api/files/{file.id}/chat", json={"question": "   "})
    assert resp.status_code == 400


def test_ask_disabled_returns_400(monkeypatch, client):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    file = make_document("doc")
    resp = client.post(f"/api/files/{file.id}/chat", json={"question": "hi"})
    assert resp.status_code == 400


def test_ask_unknown_file_is_404(client):
    resp = client.post("/api/files/999999/chat", json={"question": "hi"})
    assert resp.status_code == 404


def test_history_is_capped_at_configured_turns(monkeypatch, client):
    file = make_document("doc")
    monkeypatch.setattr(config, "AI_HISTORY_TURNS", 2)
    with SessionLocal() as session:
        for i in range(5):
            session.add(AiChatMessage(
                file_id=file.id, role=AiChatRole.USER, text=f"old question {i}",
            ))
        session.commit()

    monkeypatch.setattr(
        ai_router, "build_context", lambda session, file, question: ("doc", [])
    )
    captured: dict = {}

    async def capturing_stream(messages, model=None, num_ctx=None):
        captured["messages"] = messages
        yield "ok"

    monkeypatch.setattr(ollama_client, "chat_stream", capturing_stream)

    with client.stream(
        "POST", f"/api/files/{file.id}/chat", json={"question": "new question"}
    ) as resp:
        list(resp.iter_lines())

    messages = captured["messages"]
    # 2 system messages + AI_HISTORY_TURNS history + 1 new question
    assert len(messages) == 2 + 2 + 1
    history_texts = [m["content"] for m in messages[2:-1]]
    assert history_texts == ["old question 3", "old question 4"]


# ---------------------------------------------------------------------- GET/DELETE chat
def test_get_chat_returns_persisted_messages(client):
    file = make_document("doc")
    with SessionLocal() as session:
        session.add(AiChatMessage(file_id=file.id, role=AiChatRole.USER, text="q"))
        session.add(AiChatMessage(
            file_id=file.id, role=AiChatRole.ASSISTANT, text="a",
            sources=json.dumps([{"sectionPath": ["Intro"], "startOffset": 0, "snippet": "s"}]),
        ))
        session.commit()

    resp = client.get(f"/api/files/{file.id}/chat")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert body[1]["sources"][0]["sectionPath"] == ["Intro"]


def test_delete_chat_clears_the_conversation(client):
    file = make_document("doc")
    with SessionLocal() as session:
        session.add(AiChatMessage(file_id=file.id, role=AiChatRole.USER, text="q"))
        session.commit()

    resp = client.delete(f"/api/files/{file.id}/chat")
    assert resp.status_code == 204
    assert client.get(f"/api/files/{file.id}/chat").json() == []
