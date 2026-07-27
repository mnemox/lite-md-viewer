"""Thin async client for the local Ollama server.

Every failure mode (server unreachable, model not pulled, request timeout) is turned into a
typed `OllamaError` here so the router never has to guess what a raw `httpx` exception means.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from .. import config

log = logging.getLogger("litemdviewer.ollama")


class OllamaError(Exception):
    """A problem talking to Ollama, with a message safe to show the user."""


async def health(model: str | None = None) -> dict:
    """Whether the server answers and, if given a model tag, whether it is pulled.

    Returns {"reachable": bool, "model_present": bool, "error": str | None}.
    """
    model = model or config.OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{config.OLLAMA_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        return {"reachable": False, "model_present": False, "error": str(exc)}
    except (ValueError, json.JSONDecodeError) as exc:
        return {"reachable": False, "model_present": False, "error": str(exc)}

    tags = {m.get("name") for m in data.get("models", [])}
    # Ollama normalises an unqualified tag like "gemma4:e4b" to itself, but a bare
    # "gemma4" pull would show as "gemma4:latest" -- compare the base name too.
    base = model.split(":", 1)[0]
    present = model in tags or any(t == base or (t or "").startswith(base + ":") for t in tags)
    return {"reachable": True, "model_present": present, "error": None}


async def chat_stream(
    messages: list[dict],
    model: str | None = None,
    num_ctx: int | None = None,
) -> AsyncIterator[str]:
    """Stream an assistant reply as a sequence of text deltas.

    `messages` follows Ollama's chat schema: a list of {"role": ..., "content": ...}.
    Raises OllamaError on anything that stops the stream before it completes.
    """
    model = model or config.OLLAMA_MODEL
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        # Ollama defaults num_ctx to 4096 regardless of what the model supports, which would
        # silently truncate a whole-document prompt -- always set it explicitly.
        "options": {"num_ctx": num_ctx or config.AI_NUM_CTX},
    }

    try:
        async with httpx.AsyncClient(timeout=config.AI_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{config.OLLAMA_URL}/api/chat", json=payload
            ) as resp:
                if resp.status_code == 404:
                    raise OllamaError(
                        f"Model '{model}' is not available. Pull it with: ollama pull {model}"
                    )
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise OllamaError(f"Ollama returned {resp.status_code}: {body.decode(errors='replace')[:200]}")

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("error"):
                        raise OllamaError(str(row["error"]))
                    content = (row.get("message") or {}).get("content")
                    if content:
                        yield content
                    if row.get("done"):
                        return
    except httpx.ConnectError as exc:
        raise OllamaError(
            "Could not reach Ollama. Is it running? Start it with 'ollama serve'."
        ) from exc
    except httpx.TimeoutException as exc:
        raise OllamaError("Ollama did not respond in time.") from exc
    except httpx.HTTPError as exc:
        raise OllamaError(str(exc)) from exc
