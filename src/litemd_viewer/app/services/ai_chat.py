"""Builds the context and message list for a question about one open document.

Whole document if it fits the character budget; otherwise the existing scoped hybrid search
(the same one the search panel uses) picks the most relevant sections instead.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import config
from ..models import ManagedFile
from ..schemas import AiSourceDto
from .indexing import Indexer, get_indexer

SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions about a single document the user is "
    "reading. Answer only using the document text supplied below. If the answer is not in "
    "it, say so plainly instead of guessing. When useful, refer to sections by their "
    "heading names."
)


def build_context(
    session: Session, file: ManagedFile, question: str
) -> tuple[str, list[AiSourceDto]]:
    """Return (context_text, sources). `sources` is empty when the whole document fit."""
    content, _ = Indexer._load_content(session, file)
    content = content or ""

    if len(content) <= config.AI_CONTEXT_CHARS:
        return content, []

    result = get_indexer().search(
        question, k=config.AI_RETRIEVE_K, mode="hybrid", file_id=file.id
    )
    parts: list[str] = []
    sources: list[AiSourceDto] = []
    budget = config.AI_CONTEXT_CHARS
    for hit in result.hits:
        heading = " > ".join(hit.section_path) if hit.section_path else file.title
        excerpt = hit.snippet
        block = f"## {heading}\n{excerpt}"
        if budget - len(block) < 0:
            break
        parts.append(block)
        budget -= len(block)
        sources.append(AiSourceDto(
            section_path=hit.section_path,
            start_offset=hit.start_offset,
            snippet=hit.snippet,
        ))

    return "\n\n".join(parts), sources


def build_messages(
    document_title: str,
    context_text: str,
    history: list[tuple[str, str]],
    question: str,
) -> list[dict]:
    """The full message list handed to Ollama: system prompt, document, history, question.

    `history` is a list of (role, text) pairs, oldest first, already capped by the caller
    to config.AI_HISTORY_TURNS.
    """
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": f"Document title: {document_title}\n\n{context_text}",
        },
    ]
    for role, text in history:
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": question})
    return messages
