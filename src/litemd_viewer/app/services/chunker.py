"""Split a markdown document into overlapping passages for embedding.

Chunking is block-aligned rather than character-aligned: the text is first cut into
paragraphs and fenced code blocks, then those blocks are packed greedily into chunks. This
keeps a code fence intact and stops a sentence from being sliced mid-word, and it makes the
per-chunk hash stable -- editing one paragraph leaves the other chunks byte-identical, so
they are not re-embedded.

Each chunk's text is then stripped of markdown syntax. Markup is pure cost to a sentence
embedding: it burns context, dilutes the vector, pollutes the full-text index and makes an
unreadable snippet. Offsets keep pointing into the raw document, so the viewer can still
scroll to the passage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .. import config

FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")

# ---- markdown cleanup ----
_FRONT_MATTER_RE = re.compile(r"\A---[^\n]*\n.*?\n---[^\n]*\n", re.S)
_FENCE_LINE_RE = re.compile(r"^\s{0,3}(?:```|~~~)\s*([^\n]*)$")
# A line of only dashes/stars/underscores or table rule pipes carries no words at all.
_RULE_RE = re.compile(r"^\s*(?:[-*_=]\s*){2,}$")
_TABLE_RULE_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*")
_QUOTE_RE = re.compile(r"^\s*>+\s?")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_REF_LINK_RE = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
_AUTOLINK_RE = re.compile(r"<(?:https?|mailto):[^>\s]*>")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]{0,120}>")
# Renders as nothing, so it is not content. May span lines, hence handled before the loop.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.S)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
# Bounded by non-word characters, so an identifier such as file_id keeps its underscores.
_ITALIC_UNDER_RE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")
_BACKTICK_RE = re.compile(r"`+")
# Mermaid node labels are prose; its ids and arrows are structure.
_MERMAID_LABEL_RE = re.compile(
    r'"([^"\n]{2,})"|\[([^\]|\n]{2,})\]|\(([^)|\n]{2,})\)|\{([^}|\n]{2,})\}'
)
_SPACES_RE = re.compile(r"[ \t]{2,}")
_BLANKS_RE = re.compile(r"\n{3,}")


def _mermaid_labels(line: str) -> str:
    parts = [g for m in _MERMAID_LABEL_RE.finditer(line) for g in m.groups() if g]
    return " ".join(p.strip() for p in parts if p.strip())


def _clean_inline(line: str) -> str:
    line = _IMAGE_RE.sub(r"\1", line)
    line = _LINK_RE.sub(r"\1", line)
    line = _REF_LINK_RE.sub(r"\1", line)
    line = _AUTOLINK_RE.sub(" ", line)
    line = _HTML_TAG_RE.sub(" ", line)
    line = _STRIKE_RE.sub(r"\1", line)
    line = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", line)
    line = _ITALIC_STAR_RE.sub(r"\1", line)
    line = _ITALIC_UNDER_RE.sub(r"\1", line)
    return _BACKTICK_RE.sub("", line)


def clean_markdown(text: str) -> str:
    """Reduce markdown to the words that carry meaning.

    Headings, emphasis, quote and list markers, table pipes, link targets, HTML tags and
    fence markers are dropped. Code inside a fence is *kept*, because a technical corpus is
    searched by its identifiers; only a mermaid block is reduced, to its node labels, since
    the rest of it is diagram wiring.
    """
    if not text:
        return ""

    text = _HTML_COMMENT_RE.sub(" ", _FRONT_MATTER_RE.sub("", text))

    out: list[str] = []
    in_fence = False
    info = ""

    for line in text.splitlines():
        fence = _FENCE_LINE_RE.match(line)
        if fence:
            if in_fence:
                in_fence, info = False, ""
            else:
                in_fence, info = True, fence.group(1).strip().lower()
            continue                       # the marker line is never content

        if in_fence:
            if info.startswith("mermaid"):
                labels = _mermaid_labels(line)
                if labels:
                    out.append(labels)
            else:
                out.append(line.strip())
            continue

        if _TABLE_RULE_RE.match(line) or _RULE_RE.match(line):
            continue

        line = _QUOTE_RE.sub("", line)
        line = _HEADING_RE.sub("", line)
        line = _BULLET_RE.sub("", line)
        line = _clean_inline(line)
        out.append(line.replace("|", " ").rstrip())

    cleaned = _BLANKS_RE.sub("\n\n", _SPACES_RE.sub(" ", "\n".join(out)))
    return cleaned.strip()


@dataclass(frozen=True)
class Chunk:
    index: int
    start_offset: int
    end_offset: int
    text: str
    content_hash: str


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


@dataclass(frozen=True)
class _Block:
    start: int
    end: int


def _blocks(text: str) -> list[_Block]:
    """Cut the document into paragraphs, treating a fenced code block as one unit."""
    lines = text.splitlines(keepends=True)
    blocks: list[_Block] = []

    offset = 0
    block_start: int | None = None
    in_fence = False
    fence_marker = ""

    for line in lines:
        stripped = line.strip()
        match = FENCE_RE.match(line)

        if in_fence:
            if match and stripped.startswith(fence_marker):
                in_fence = False
                offset += len(line)
                blocks.append(_Block(block_start or 0, offset))
                block_start = None
                continue
            offset += len(line)
            continue

        if match:
            if block_start is not None:
                blocks.append(_Block(block_start, offset))
            in_fence = True
            fence_marker = match.group(1)
            block_start = offset
            offset += len(line)
            continue

        if not stripped:
            if block_start is not None:
                blocks.append(_Block(block_start, offset))
                block_start = None
            offset += len(line)
            continue

        if block_start is None:
            block_start = offset
        offset += len(line)

    if block_start is not None:
        blocks.append(_Block(block_start, offset))

    return blocks


def _split_oversized(block: _Block, text: str, limit: int, overlap: int) -> list[_Block]:
    """Hard-split a single block that is longer than one chunk, preferring line breaks."""
    pieces: list[_Block] = []
    start = block.start
    while start < block.end:
        end = min(start + limit, block.end)
        if end < block.end:
            newline = text.rfind("\n", start + limit // 2, end)
            if newline > start:
                end = newline + 1
        pieces.append(_Block(start, end))
        if end >= block.end:
            break
        start = max(end - overlap, start + 1)
    return pieces


def chunk_document(text: str) -> list[Chunk]:
    """Produce the ordered chunks of a document. Empty input yields no chunks."""
    if not text or not text.strip():
        return []

    limit = config.CHUNK_CHARS
    overlap = config.CHUNK_OVERLAP_CHARS

    blocks: list[_Block] = []
    for block in _blocks(text):
        if block.end - block.start > limit:
            blocks.extend(_split_oversized(block, text, limit, overlap))
        else:
            blocks.append(block)

    if not blocks:
        return []

    # Greedily pack blocks, then step back far enough to overlap the previous chunk.
    chunks: list[Chunk] = []
    current: list[_Block] = []
    index = 0

    def flush(pending: list[_Block]) -> None:
        nonlocal index
        if not pending:
            return
        start = pending[0].start
        end = pending[-1].end
        # Offsets stay raw so the viewer can locate the passage; only the text is cleaned.
        body = clean_markdown(text[start:end])
        if len(body) < config.MIN_CHUNK_CHARS and chunks:
            return  # a trailing scrap is already covered by the previous chunk's overlap
        if not body:
            return  # the block was pure markup, e.g. a table rule or a bare fence
        chunks.append(Chunk(
            index=index,
            start_offset=start,
            end_offset=end,
            text=body,
            content_hash=sha256_hex(body),
        ))
        index += 1

    for block in blocks:
        projected = (block.end - current[0].start) if current else (block.end - block.start)
        if current and projected > limit:
            flush(current)
            # Carry trailing blocks forward as overlap so a passage that straddles a
            # boundary is still retrievable from at least one chunk.
            carried: list[_Block] = []
            carried_len = 0
            for previous in reversed(current):
                length = previous.end - previous.start
                if carried_len + length > overlap:
                    break
                carried.insert(0, previous)
                carried_len += length
            current = carried
        current.append(block)

    flush(current)
    return chunks
