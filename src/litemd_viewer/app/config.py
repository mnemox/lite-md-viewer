"""Runtime paths and tunables.

Mirrors the .NET host's conventions: data lives next to the application (its "content
root"), the server binds loopback-only, and a PORT environment variable set by a dev or
preview harness overrides the default port.
"""

from __future__ import annotations

import os
from pathlib import Path

# The package's content root: <repo>/src/litemd_viewer
CONTENT_ROOT = Path(__file__).resolve().parent.parent

# Everything writable can be relocated in one go (useful for tests and for running
# several instances against different document sets).
DATA_DIR = Path(os.environ.get("LITEMD_DATA_DIR", CONTENT_ROOT)).resolve()

DB_PATH = DATA_DIR / "litemdviewer.db"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
VECTOR_DIR = DATA_DIR / "vectors"

STATIC_DIR = CONTENT_ROOT / "wwwroot"

HOST = "127.0.0.1"
DEFAULT_PORT = 5099
PORT_OVERRIDE = os.environ.get("PORT", "").strip()
PORT = int(PORT_OVERRIDE) if PORT_OVERRIDE else DEFAULT_PORT

# Document file types the app manages and renders (lower-case, incl. dot).
DOCUMENT_EXT = (".md", ".markdown", ".xml")
DEFAULT_EXT = ".md"
UNSUPPORTED_MESSAGE = "Only .md, .markdown, or .xml files are supported."

# ---- file sync ----
SYNC_DEBOUNCE_SECONDS = 0.5     # collapse an editor's burst of save events
SYNC_TICK_SECONDS = 0.25
SYNC_RECONCILE_SECONDS = 10.0   # safety net for coalesced/missed watcher events

# ---- vector indexing ----
# Embedding is far more expensive than the content mirror, so it debounces for longer and
# the .hnsw file is flushed once per quiet period rather than once per save.
INDEX_DEBOUNCE_SECONDS = 2.0
INDEX_COMMIT_IDLE_SECONDS = 2.0

EMBED_MODEL = os.environ.get("LITEMD_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.environ.get("LITEMD_EMBED_DIM", "384"))
VECTOR_COLLECTION = "doc_chunks"
VECTOR_FIELD = "embedding"

# Chunking, measured in characters of *cleaned* text, with overlap so a passage spanning a
# boundary is still retrievable.
#
# The limit exists because the embedding model truncates at EMBED_MAX_TOKENS and silently
# discards the rest. Technical markdown tokenises at roughly 3.2 characters per token -- far
# worse than the ~4 of plain prose -- so the earlier 2000 pushed half of all chunks past the
# limit and lost 13% of the corpus. Measured over this corpus, 1200 keeps the longest chunk
# at ~487 tokens, i.e. nothing is truncated.
CHUNK_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 40

# Context window of EMBED_MODEL. Chunks must stay under it; see CHUNK_CHARS.
EMBED_MAX_TOKENS = 512

# The document title is prepended to every passage before embedding, so it has to share the
# context window with the passage. Measured over this corpus titles run 3-21 tokens and the
# longest chunk then reaches 496 of 512, so this cap exists only to stop a pathologically
# long rename from pushing the passage itself out of the window.
EMBED_TITLE_MAX_CHARS = 80

# Prepended to a search query, never to a passage. bge-v1.5 treats its retrieval instruction
# as optional, so this defaults to off; set it to
#   "Represent this sentence for searching relevant passages: "
# to enable the documented one.
EMBED_QUERY_PREFIX = os.environ.get("LITEMD_EMBED_QUERY_PREFIX", "")

# Set LITEMD_DISABLE_EMBEDDING=1 to run the app without loading the ONNX model.
EMBEDDING_ENABLED = os.environ.get("LITEMD_DISABLE_EMBEDDING", "") not in ("1", "true", "True")

# Set LITEMD_NO_BROWSER=1 when a launcher opens the browser itself, so that the
# openBrowserOnStart setting does not produce a second tab.
NO_BROWSER = os.environ.get("LITEMD_NO_BROWSER", "") in ("1", "true", "True")

# ---- local analysis (Ollama chat over the open document) ----
# Off by default: run.bat sets LITEMD_AI_ENABLED=1 only after the user opts in and Ollama
# has been installed and the model pulled. See run.bat's :setup_ai subroutine.
AI_ENABLED = os.environ.get("LITEMD_AI_ENABLED", "") in ("1", "true", "True")
OLLAMA_URL = os.environ.get("LITEMD_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("LITEMD_OLLAMA_MODEL", "gemma4:e2b")

# Ollama defaults num_ctx to 4096 tokens regardless of what the model supports, which would
# silently truncate a whole-document prompt. This is passed explicitly on every request.
AI_NUM_CTX = 16384

# Measured elsewhere in this file at ~3.2 chars/token for technical markdown (see
# CHUNK_CHARS). AI_NUM_CTX tokens is therefore ~52K chars; this budget leaves room for the
# system prompt, chat history and the answer itself.
AI_CONTEXT_CHARS = 32000

# How many sections to retrieve (via the existing hybrid search) when the document is too
# big to send whole.
AI_RETRIEVE_K = 8

# Prior turns replayed to the model on each question, oldest of the window first.
AI_HISTORY_TURNS = 6

AI_TIMEOUT = 120.0


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)


def is_supported(path: str | os.PathLike[str]) -> bool:
    return os.path.splitext(str(path))[1].lower() in DOCUMENT_EXT
