// Markdown + Mermaid rendering pipeline (client-side).
//   1. markdown-it -> HTML (mermaid fences become inert <pre class="mermaid">)
//   2. DOMPurify sanitize -> insert into DOM
//   3. mermaid.run over the inert blocks -> SVG
// Order matters: sanitize BEFORE mermaid runs; never DOMPurify the produced SVG.

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
