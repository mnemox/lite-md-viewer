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

export async function renderMarkdown(text, container) {
  const html = getMd().render(text || '');
  container.innerHTML = window.DOMPurify.sanitize(html);

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
