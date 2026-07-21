// Capture highlighted text passages in the document and draw arrows from a
// document-note's relation icon to the linked passages. Used by docnotes.js.

const SVGNS = 'http://www.w3.org/2000/svg';
const ARROW_DURATION = 3200; // ms the arrows stay visible
const MAX_TEXT = 500;

let host = null;
let arrowTimer = null;
let raf = null;
let activeIcon = null;
let activeRefs = [];
let rangeCache = new Map(); // refId -> { range, docEl }

// ---------- public API ----------

export function getSelectionInfo() {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;

  const docEl = currentDocEl();
  if (!docEl) return null;

  const range = sel.getRangeAt(0);
  if (!docEl.contains(range.commonAncestorContainer)) return null;

  const raw = range.toString();
  if (!raw || !raw.trim()) return null;

  const text = raw.length > MAX_TEXT ? raw.slice(0, MAX_TEXT) : raw;

  const pre = document.createRange();
  pre.setStart(docEl, 0);
  pre.setEnd(range.startContainer, range.startOffset);
  const start = pre.toString().length;

  return { start, length: text.length, text };
}

export function showArrows(refs, iconEl) {
  activeIcon = iconEl;
  activeRefs = refs || [];
  if (!activeRefs.length || !currentDocEl()) {
    hideArrows();
    return;
  }

  ensureHost();
  rangeCache.clear();
  clearTimeout(arrowTimer);
  cancelRaf();
  scheduleFrame();
  arrowTimer = setTimeout(hideArrows, ARROW_DURATION);
}

export function hideArrows() {
  clearTimeout(arrowTimer);
  arrowTimer = null;
  cancelRaf();
  if (host) {
    host.remove();
    host = null;
  }
  rangeCache.clear();
  activeIcon = null;
  activeRefs = [];
}

// ---------- DOM helpers ----------

function currentDocEl() {
  const viewer = document.getElementById('viewer');
  if (viewer && !viewer.classList.contains('hidden')) return viewer;
  const preview = document.getElementById('editorPreview');
  if (preview && !preview.classList.contains('hidden')) return preview;
  return null;
}

function ensureHost() {
  if (host) return;
  host = document.createElement('div');
  host.className = 'note-refs-overlay';
  host.setAttribute('aria-hidden', 'true');
  host.innerHTML = `<svg width="100%" height="100%" xmlns="${SVGNS}"><defs><marker id="note-ref-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="var(--accent)"/></marker></defs><g class="note-ref-lines"></g></svg>`;
  document.body.appendChild(host);
}

function scheduleFrame() {
  if (raf) return;
  const frame = () => {
    if (!host || !activeIcon) return;
    draw();
    raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
}

function cancelRaf() {
  if (raf) {
    cancelAnimationFrame(raf);
    raf = null;
  }
}

function draw() {
  if (!host || !activeIcon || !activeIcon.isConnected) {
    hideArrows();
    return;
  }
  const docEl = currentDocEl();
  if (!docEl) {
    hideArrows();
    return;
  }

  const anchor = getCenter(activeIcon.getBoundingClientRect());
  const g = host.querySelector('.note-ref-lines');
  g.innerHTML = '';

  for (const ref of activeRefs) {
    const range = getRange(ref, docEl);
    if (!range) continue;
    const rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const target = getCenter(rect);
    drawArrow(g, anchor, target);
  }
}

function getRange(ref, docEl) {
  const cached = rangeCache.get(ref.id);
  if (cached && cached.docEl === docEl && cached.range.startContainer?.isConnected) {
    const r = cached.range;
    const rect = r.getBoundingClientRect();
    if (rect.width || rect.height) return r;
  }
  const range = findTextRange(docEl, ref.text, ref.startOffset);
  if (range) rangeCache.set(ref.id, { range, docEl });
  else rangeCache.delete(ref.id);
  return range;
}

function findTextRange(root, text, preferredStart) {
  if (!text || !root) return null;
  const full = root.textContent;
  if (!full) return null;

  const matches = [];
  let pos = 0;
  while ((pos = full.indexOf(text, pos)) !== -1) {
    matches.push(pos);
    pos += text.length;
  }
  if (!matches.length) return null;

  let start = matches[0];
  let best = Infinity;
  for (const m of matches) {
    const d = Math.abs(m - (preferredStart ?? 0));
    if (d < best) { best = d; start = m; }
  }
  const end = start + text.length;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) nodes.push(n);

  const range = document.createRange();
  let textPos = 0;
  let started = false;

  for (const node of nodes) {
    const len = node.textContent.length;

    if (!started) {
      if (start >= textPos && start < textPos + len) {
        range.setStart(node, start - textPos);
        started = true;
      } else if (start === textPos + len && !node.nextSibling) {
        // edge case: selection starts right at the end of the last text node
        range.setStart(node, len);
        started = true;
      }
    }

    if (started) {
      if (end >= textPos && end <= textPos + len) {
        range.setEnd(node, end - textPos);
        return range;
      } else if (end === textPos + len && !node.nextSibling) {
        range.setEnd(node, len);
        return range;
      }
    }

    textPos += len;
  }

  return null;
}

function getCenter(rect) {
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

function drawArrow(g, start, end) {
  const line = document.createElementNS(SVGNS, 'line');
  line.setAttribute('x1', start.x.toFixed(1));
  line.setAttribute('y1', start.y.toFixed(1));
  line.setAttribute('x2', end.x.toFixed(1));
  line.setAttribute('y2', end.y.toFixed(1));
  line.setAttribute('stroke', 'var(--accent)');
  line.setAttribute('stroke-width', '2');
  line.setAttribute('marker-end', 'url(#note-ref-arrowhead)');
  g.appendChild(line);
}
