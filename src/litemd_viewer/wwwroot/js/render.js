// Document rendering pipeline (client-side). renderDoc() dispatches by file extension:
//   - Markdown (default): markdown-it -> HTML (mermaid fences become inert <pre class="mermaid">)
//       1. markdown-it -> HTML   2. DOMPurify sanitize -> DOM   3. mermaid.run over inert blocks -> SVG
//     Order matters: sanitize BEFORE mermaid runs; never DOMPurify the produced SVG.
//   - XML: highlight.js (xml grammar) -> DOMPurify sanitize -> DOM. No markdown-it, no mermaid.

import { enhance } from './graphview.js';

let md = null;
let mermaidTheme = 'default';

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function getMd() {
  if (md) return md;
  md = window.markdownit({
    html: false,
    linkify: true,
    typographer: true,
    highlight: (code, lang) => {
      if (lang === 'mermaid') {
        return `<pre class="mermaid">${escapeHtml(code)}</pre>`;
      }
      if (lang && window.hljs && window.hljs.getLanguage(lang)) {
        try {
          return `<pre class="hljs"><code>${window.hljs.highlight(code, { language: lang }).value}</code></pre>`;
        } catch { /* fall through */ }
      }
      return `<pre class="hljs"><code>${escapeHtml(code)}</code></pre>`;
    },
  });
  return md;
}

export function setMermaidTheme(theme) {
  mermaidTheme = theme === 'dark' ? 'dark' : 'default';
}

// Mermaid diagram-type headers that can open a *bare* (unfenced) diagram document, so a file
// that is nothing but a diagram still renders instead of collapsing into markdown prose.
const MERMAID_HEADER = /^(?:%%\{|(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b|(?:sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|gantt|pie(?:\s|$)|mindmap|timeline|quadrantChart|requirementDiagram|gitGraph|C4Context|sankey-beta|xychart-beta|block-beta|packet-beta|zenuml|architecture-beta)\b)/;

// True when the whole document is a Mermaid diagram written without a ```mermaid fence. The
// test is intentionally tight -- the first non-empty line must be an init directive or a
// diagram header in its expected shape (e.g. "flowchart LR", "sequenceDiagram") -- so ordinary
// prose that merely starts with a word like "graph" is not misrendered. A document that already
// contains a fence is left to the normal markdown path.
function isBareMermaid(text) {
  const trimmed = (text || '').trim();
  if (!trimmed || trimmed.includes('```')) return false;
  const firstLine = trimmed.split('\n', 1)[0].trim();
  return MERMAID_HEADER.test(firstLine);
}

export async function renderMarkdown(text, container) {
  if (isBareMermaid(text)) {
    // Render the file as one diagram. escapeHtml keeps mermaid's own markup safe; the shared
    // mermaid.run() below turns it into SVG, and a parse failure just leaves the source
    // visible (like a fenced block) instead of throwing.
    container.innerHTML = `<pre class="mermaid">${escapeHtml(text.trim())}</pre>`;
  } else {
    const html = getMd().render(text || '');
    container.innerHTML = window.DOMPurify.sanitize(html);
  }

  // External links open in a new tab.
  container.querySelectorAll('a[href]').forEach((a) => {
    const href = a.getAttribute('href') || '';
    if (/^https?:/i.test(href)) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
  });

  const blocks = container.querySelectorAll('pre.mermaid');
  if (blocks.length && window.mermaid) {
    try {
      window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: mermaidTheme });
      await window.mermaid.run({ nodes: Array.from(blocks) });
    } catch (e) {
      console.error('mermaid render failed', e);
    }
  }

  // Add expand-to-fullscreen (zoom/pan) controls to rendered diagrams.
  enhance(container);
}

export function isXmlPath(path) {
  return /\.xml$/i.test(path || '');
}

// Render a whole file as syntax-highlighted XML source (a read-only view of the raw bytes).
// Deliberately bypasses markdown-it (html:false would escape/mangle the tags) and mermaid.
// hljs.highlight escapes the source and emits only <span class="hljs-…">, so tags show as
// visible source and DOMPurify passes the <pre>/<code>/<span class> through unchanged.
export function renderXml(text, container) {
  const src = text || '';
  let inner;
  try {
    inner = window.hljs.highlight(src, { language: 'xml' }).value;
  } catch {
    inner = escapeHtml(src);
  }
  container.innerHTML = window.DOMPurify.sanitize(
    `<pre class="hljs"><code class="language-xml">${inner}</code></pre>`);
}

// Single entry the app calls; picks the pipeline by file extension.
export function renderDoc(path, text, container) {
  return isXmlPath(path) ? renderXml(text, container) : renderMarkdown(text, container);
}
