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
let lineMap = new Map();    // refId -> { visible, hit }
let notesById = new Map();  // noteId -> note
let highlightRoot = null;
let highlightHandler = null;

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
  lineMap.clear();
  activeIcon = null;
  activeRefs = [];
}

export function highlightNotes(notes) {
  clearHighlights();
  const docEl = currentDocEl();
  if (!docEl || !notes || !notes.length) return;

  highlightRoot = docEl;
  for (const note of notes) notesById.set(String(note.id), note);

  for (const note of notes) {
    for (const ref of note.references || []) {
      const range = findTextRange(docEl, ref.text, ref.startOffset);
      if (!range) continue;
      wrapRange(range, note.id, ref.id);
    }
  }

  highlightHandler = (e) => {
    const span = e.target.closest('.note-ref-highlight');
    if (!span) return;
    onHighlightOver(span);
  };
  highlightRoot.addEventListener('mouseover', highlightHandler, true);
}

export function clearHighlights() {
  if (highlightRoot) {
    highlightRoot.removeEventListener('mouseover', highlightHandler, true);
    highlightRoot = null;
    highlightHandler = null;
  }
  notesById.clear();
  for (const id of ['viewer', 'editorPreview']) {
    const el = document.getElementById(id);
    if (el) {
      el.querySelectorAll('.note-ref-highlight').forEach((span) => {
        span.replaceWith(...Array.from(span.childNodes));
      });
    }
  }
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

  const anchorRect = activeIcon.getBoundingClientRect();
  if (!anchorRect.width || !anchorRect.height) { hideArrows(); return; }
  const anchor = getCenter(anchorRect);
  const g = host.querySelector('.note-ref-lines');
  const used = new Set();

  for (const ref of activeRefs) {
    const range = getRange(ref, docEl);
    if (!range) continue;
    const rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const target = getCenter(rect);

    let pair = lineMap.get(ref.id);
    if (!pair) {
      pair = createArrow(g, ref);
      lineMap.set(ref.id, pair);
    }
    updateArrow(pair, anchor, target);
    used.add(ref.id);
  }

  for (const [id, pair] of lineMap) {
    if (!used.has(id)) {
      pair.visible.remove();
      pair.hit.remove();
      lineMap.delete(id);
    }
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

function onHitClick(e) {
  const refId = e.currentTarget.getAttribute('data-ref-id');
  const ref = activeRefs.find((r) => String(r.id) === refId);
  if (ref) {
    e.stopPropagation();
    scrollToRef(ref);
  }
}

function scrollContainer(el) {
  let p = el;
  while (p && p !== document.body && p !== document.documentElement) {
    const style = window.getComputedStyle(p);
    if (style.overflowY === 'auto' || style.overflowY === 'scroll') return p;
    p = p.parentElement;
  }
  return null;
}

function scrollToRef(ref) {
  const docEl = currentDocEl();
  if (!docEl) return;
  const range = getRange(ref, docEl);
  if (!range) return;
  const rect = range.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return;
  const scroller = scrollContainer(docEl);
  if (!scroller) return;

  const scrollerRect = scroller.getBoundingClientRect();
  let topOffset = 20;
  if (scroller.classList.contains('content')) {
    const warning = document.getElementById('dbWarning');
    if (warning && !warning.classList.contains('hidden')) {
      topOffset += warning.getBoundingClientRect().height;
    }
  }
  const targetTop = scroller.scrollTop + rect.top - scrollerRect.top - topOffset;
  scroller.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' });
}

function createArrow(g, ref) {
  const visible = document.createElementNS(SVGNS, 'line');
  visible.setAttribute('class', 'note-ref-line');

  const hit = document.createElementNS(SVGNS, 'line');
  hit.setAttribute('class', 'note-ref-hit');
  hit.setAttribute('data-ref-id', String(ref.id));
  hit.setAttribute('stroke-linecap', 'round');
  hit.addEventListener('click', onHitClick);
  hit.addEventListener('mouseover', onHitOver);

  g.appendChild(visible);
  g.appendChild(hit);
  return { visible, hit };
}

function updateArrow(pair, start, end) {
  const x1 = start.x.toFixed(1);
  const y1 = start.y.toFixed(1);
  const x2 = end.x.toFixed(1);
  const y2 = end.y.toFixed(1);
  pair.visible.setAttribute('x1', x1);
  pair.visible.setAttribute('y1', y1);
  pair.visible.setAttribute('x2', x2);
  pair.visible.setAttribute('y2', y2);
  pair.hit.setAttribute('x1', x1);
  pair.hit.setAttribute('y1', y1);
  pair.hit.setAttribute('x2', x2);
  pair.hit.setAttribute('y2', y2);
}

function makeHighlightSpan(noteId, refId) {
  const span = document.createElement('span');
  span.className = 'note-ref-highlight';
  span.dataset.noteId = String(noteId);
  span.dataset.refId = String(refId);
  return span;
}

function wrapRange(range, noteId, refId) {
  try {
    const span = makeHighlightSpan(noteId, refId);
    range.surroundContents(span);
  } catch {
    wrapRangeSegments(range, noteId, refId);
  }
}

function wrapRangeSegments(range, noteId, refId) {
  const segs = [];
  const walker = document.createTreeWalker(range.commonAncestorContainer, NodeFilter.SHOW_TEXT, null, false);
  let node;
  while ((node = walker.nextNode())) {
    if (!range.intersectsNode(node)) continue;
    let start = 0;
    let end = node.length;
    if (node === range.startContainer) start = range.startOffset;
    if (node === range.endContainer) end = range.endOffset;
    if (start >= end) continue;
    segs.push({ node, start, end });
  }
  for (let i = segs.length - 1; i >= 0; i--) {
    const { node, start, end } = segs[i];
    const text = node.textContent;
    const parent = node.parentNode;
    if (start === 0 && end === text.length) {
      const span = makeHighlightSpan(noteId, refId);
      parent.replaceChild(span, node);
      span.appendChild(node);
      continue;
    }
    if (start > 0) parent.insertBefore(document.createTextNode(text.slice(0, start)), node);
    const selected = document.createTextNode(text.slice(start, end));
    parent.insertBefore(selected, node);
    if (end < text.length) parent.insertBefore(document.createTextNode(text.slice(end)), node);
    parent.removeChild(node);
    const span = makeHighlightSpan(noteId, refId);
    parent.replaceChild(span, selected);
    span.appendChild(selected);
  }
}

function onHighlightOver(span) {
  const noteId = span.dataset.noteId;
  const note = notesById.get(noteId);
  if (!note) return;
  const refs = note.references || [];
  const relBtn = document.querySelector(`.doc-note[data-id="${noteId}"] .doc-note-act.rel`);
  if (!relBtn) return;
  const rect = relBtn.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  if (activeIcon === relBtn && refsEqual(activeRefs, refs)) {
    clearTimeout(arrowTimer);
    arrowTimer = setTimeout(hideArrows, ARROW_DURATION);
    return;
  }
  showArrows(refs, relBtn);
}

function onHitOver(e) {
  const refId = e.currentTarget.getAttribute('data-ref-id');
  const note = findNoteForRef(refId);
  if (!note) return;
  const refs = note.references || [];
  const relBtn = document.querySelector(`.doc-note[data-id="${note.id}"] .doc-note-act.rel`);
  if (!relBtn) return;
  const rect = relBtn.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  if (activeIcon === relBtn && refsEqual(activeRefs, refs)) {
    clearTimeout(arrowTimer);
    arrowTimer = setTimeout(hideArrows, ARROW_DURATION);
    return;
  }
  showArrows(refs, relBtn);
}

function findNoteForRef(refId) {
  for (const note of notesById.values()) {
    if ((note.references || []).some((r) => String(r.id) === refId)) return note;
  }
  return null;
}

function refsEqual(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length !== b.length) return false;
  return a.every((r, i) => r.id === b[i].id);
}
