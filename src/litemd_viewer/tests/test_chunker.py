"""Chunking rules the incremental indexer depends on."""

from __future__ import annotations

from app import config
from app.services.chunker import chunk_document, clean_markdown, sha256_hex


def test_empty_input_produces_no_chunks():
    assert chunk_document("") == []
    assert chunk_document("   \n\n  ") == []


def test_a_short_document_is_a_single_chunk():
    chunks = chunk_document("# Title\n\nA short paragraph.\n")
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert "A short paragraph." in chunks[0].text


def test_offsets_point_back_into_the_source_text():
    # The text is cleaned, but the offsets must still address the raw document so the viewer
    # can scroll to the passage.
    text = "# Title\n\nFirst paragraph.\n\nSecond paragraph.\n"
    for chunk in chunk_document(text):
        raw = text[chunk.start_offset:chunk.end_offset]
        assert clean_markdown(raw) == chunk.text
        assert "First paragraph." in raw or "Second paragraph." in raw


def test_a_long_document_is_split_with_stable_hashes():
    paragraph = "This is a sentence that carries some weight. " * 12
    text = "\n\n".join(f"## Section {i}\n\n{paragraph}" for i in range(12))

    chunks = chunk_document(text)
    assert len(chunks) > 1
    assert [c.index for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.content_hash == sha256_hex(chunk.text)


def test_a_fenced_code_block_is_never_split():
    code = "\n".join(f"    line_{i} = {i}" for i in range(40))
    text = f"# Doc\n\nIntro paragraph.\n\n```python\n{code}\n```\n\nOutro paragraph.\n"

    chunks = chunk_document(text)
    holding = [c for c in chunks if "line_0 = 0" in c.text]
    assert len(holding) == 1
    assert "line_39 = 39" in holding[0].text   # the block travelled as one unit
    assert "```" not in holding[0].text        # but the fence markers are not content


def test_editing_one_paragraph_leaves_the_other_chunks_untouched():
    """The property that makes incremental re-embedding worthwhile."""
    paragraph = "Stable prose that should not move at all. " * 12
    sections = [f"## Section {i}\n\n{paragraph}" for i in range(12)]

    before = chunk_document("\n\n".join(sections))

    sections[-1] = sections[-1].replace("Stable prose", "Edited prose", 1)
    after = chunk_document("\n\n".join(sections))

    unchanged_before = {c.content_hash for c in before}
    unchanged_after = {c.content_hash for c in after}
    shared = unchanged_before & unchanged_after

    # Most chunks are byte-identical, so only a small tail needs re-embedding.
    assert len(shared) >= len(before) - 2
    assert unchanged_before != unchanged_after


def test_an_oversized_single_paragraph_is_hard_split():
    giant = "word " * (config.CHUNK_CHARS)      # far longer than one chunk
    chunks = chunk_document(giant)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= config.CHUNK_CHARS + config.CHUNK_OVERLAP_CHARS


# ---------------------------------------------------------------- markdown cleaning
def test_cleaning_strips_headings_emphasis_and_code_ticks():
    out = clean_markdown("## A **bold** heading\n\nSome *italic* and `inline_code` text.\n")
    assert out == "A bold heading\n\nSome italic and inline_code text."


def test_cleaning_keeps_underscores_inside_identifiers():
    # The italic rule must not eat the underscores in a symbol name.
    out = clean_markdown("The `file_id` column joins to `start_offset` and my_var_name.")
    assert "file_id" in out
    assert "start_offset" in out
    assert "my_var_name" in out


def test_cleaning_keeps_link_text_and_drops_the_target():
    out = clean_markdown("See [the design doc](https://example.com/a/b) for detail.")
    assert out == "See the design doc for detail."
    assert "example.com" not in out


def test_cleaning_flattens_tables_and_drops_rule_rows():
    table = (
        "| Project | TFM |\n"
        "|---------|-----|\n"
        "| Adapters | net472 |\n"
    )
    out = clean_markdown(table)
    assert "Adapters" in out and "net472" in out
    assert "|" not in out
    assert "---" not in out


def test_cleaning_drops_quote_and_list_markers():
    out = clean_markdown("> **Purpose.** Reads config.\n\n- first item\n- second item\n3. third\n")
    assert out.startswith("Purpose. Reads config.")
    assert "first item" in out and "second item" in out and "third" in out
    assert ">" not in out
    for line in out.splitlines():
        assert not line.startswith(("-", "*", "3."))


def test_cleaning_reduces_mermaid_to_its_labels():
    text = (
        "```mermaid\n"
        "graph TD\n"
        '  subgraph Dest["Consolidated Shared Instance"]\n'
        '  A["Aggregated Tenant A"] --> B\n'
        "  end\n"
        "```\n"
    )
    out = clean_markdown(text)
    assert "Consolidated Shared Instance" in out
    assert "Aggregated Tenant A" in out
    assert "graph TD" not in out
    assert "-->" not in out


def test_cleaning_keeps_code_but_not_its_fence():
    out = clean_markdown("```python\nresult = compute_total(rows)\n```\n")
    assert out == "result = compute_total(rows)"


def test_cleaning_drops_front_matter_and_html():
    out = clean_markdown("---\ntfsId: 1753181\n---\n<div class=\"x\">Real prose.</div>\n")
    assert "tfsId" not in out
    assert "div" not in out
    assert "Real prose." in out


def test_cleaning_drops_html_comments():
    # Comments render as nothing, so they must not be searchable either.
    out = clean_markdown("Before.\n<!-- hardcoded in SqlCommandExecutor -->\nAfter.\n")
    assert "SqlCommandExecutor" not in out
    assert "Before." in out and "After." in out

    spanning = clean_markdown("A.\n<!-- line one\nline two -->\nB.\n")
    assert "line one" not in spanning and "line two" not in spanning
    assert "A." in spanning and "B." in spanning


def test_cleaning_is_idempotent():
    once = clean_markdown("## Title\n\n**Bold** and `code` and | a | b |\n")
    assert clean_markdown(once) == once


def test_cleaned_chunks_stay_within_the_embedding_context():
    # CHUNK_CHARS exists to keep a chunk under EMBED_MAX_TOKENS, and the document title is
    # prepended to every chunk before embedding, so the two share that budget. Guard the
    # relationship so a future bump cannot silently reintroduce truncation.
    budget = config.CHUNK_CHARS + config.EMBED_TITLE_MAX_CHARS
    assert budget / 3.2 < config.EMBED_MAX_TOKENS


def test_chunks_overlap_so_a_straddling_passage_is_still_retrievable():
    sections = [f"## S{i}\n\n" + ("filler sentence here. " * 20) for i in range(10)]
    chunks = chunk_document("\n\n".join(sections))

    assert len(chunks) > 1
    # Consecutive chunks share source range, which is what the overlap buys.
    overlaps = [
        chunks[i].end_offset > chunks[i + 1].start_offset
        for i in range(len(chunks) - 1)
    ]
    assert any(overlaps)
