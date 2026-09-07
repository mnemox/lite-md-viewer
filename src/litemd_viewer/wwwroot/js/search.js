// Passage search, in two scopes.
//
// ALL: a hit is a document. Picking one opens it at the top -- the reader asked for the
// document, not for one paragraph of it, and being dropped into its middle loses the
// context that makes the document readable.
//
// DOC: a hit is a section of the document already open, so the panel becomes a list of
// places to jump to inside it. Picking one scrolls to the matching passage and flashes it.
// The passage is located in the *rendered* output, which the snippet cannot match
// literally: it comes from the raw markdown (so it still carries syntax the renderer
// strips) with its whitespace collapsed and its tail truncated. Matching therefore happens
// on a run of words, which survives all three differences.

import { api } from './api.js';
import { navigate } from './router.js';
import { toast, viewNoteDialog } from './ui.js';

const $ = (id) => document.getElementById(id);

const DEBOUNCE_MS = 220;
const MIN_CHARS = 2;
const MAX_HITS = 10;

const ALL = 'all';
const DOC = 'doc';

// Word run used to locate a passage: long enough to be unambiguous, short enough that a
// truncated or partly-syntax snippet still matches.
const RUN_MAX = 12;
const RUN_MIN = 3;
const WORD = /[\p{L}\p{N}]+/gu;

let openFile = null;
let activeFile = () => null;   // the document on screen, or null outside the file view
let timer = null;
let seq = 0;              // discards responses that arrive out of order
let hits = [];
let cursor = -1;          // keyboard-highlighted row, -1 for none
let query = '';
let scope = ALL;
let scopedTo = null;      // the file the visible DOC-scope results belong to
let status = null;        // cached /api/search/status, for explaining empty results

export function initSearch(openFileFn, activeFileFn) {
  openFile = openFileFn;
  if (activeFileFn) activeFile = activeFileFn;
  const input = $('searchInput');
  if (!input) return;

  input.addEventListener('input', () => {
    $('searchClear').classList.toggle('hidden', input.value === '');
    schedule(input.value);
  });
  input.addEventListener('keydown', onKeyDown);
  input.addEventListener('focus', showIdle);

  $('searchClear').onclick = () => {
    input.value = '';
    reset();
    input.focus();
  };

  $('searchPanel').addEventListener('click', (e) => {
    const row = e.target.closest('.search-hit');
    if (row) go(hits[Number(row.dataset.i)]);
  });

  $('searchScope').addEventListener('click', (e) => {
    const pill = e.target.closest('.search-scope-pill');
    if (pill) setScope(pill.dataset.scope, { rerun: true });
  });
  // Keep the input focused when the scope is switched with the mouse, so the arrow keys
  // still drive the list afterwards.
  $('searchScope').addEventListener('mousedown', (e) => e.preventDefault());

  syncSearchScope();

  // A click anywhere else dismisses the panel.
  document.addEventListener('mousedown', (e) => {
    if (!$('searchWrap').contains(e.target)) close();
  });

  // Ctrl/Cmd+K opens the app's global search. Ctrl/Cmd+F is left for the browser's own
  // Find so users can search any rendered text that the app does not index.
  document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.key.toLowerCase() === 'k') focusWithScope(e, ALL);
  });
}

function focusWithScope(e, next) {
  e.preventDefault();
  const input = $('searchInput');
  input.focus();
  input.select();
  setScope(next, { rerun: true });
}

// What the panel shows with nothing to list: a line saying what is about to be searched.
function showIdle() {
  if (hits.length) { renderHits(); open(); return; }
  // A query that has already run left its own message in the panel; keep it.
  if (query.length >= MIN_CHARS) { open(); return; }
  const file = activeFile();
  if (!file) return;
  render(note(scope === DOC
    ? `Search inside “${file.title}” — jump to a section.`
    : 'Search every indexed document.'));
  open();
}

// The DOC scope only exists while a document is open, so it lapses on its own when the
// reader leaves the file view.
function scopeFileId() {
  if (scope !== DOC) return null;
  return activeFile()?.id ?? null;
}

function setScope(next, { rerun }) {
  if (next === DOC && !activeFile()) next = ALL;
  const changed = next !== scope;
  scope = next;
  if (changed) { hits = []; cursor = -1; }
  syncSearchScope();
  if (!rerun) return;
  const text = $('searchInput').value.trim();
  if (text.length >= MIN_CHARS) run(text);
  else showIdle();
}

// Repaint the topbar toggle. Exported because the scope depends on which document is open,
// which only app.js knows about; it calls this whenever that changes.
export function syncSearchScope() {
  const bar = $('searchScope');
  if (!bar) return;
  const file = activeFile();
  if (!file && scope === DOC) { scope = ALL; hits = []; cursor = -1; }

  bar.classList.toggle('hidden', !file);
  for (const pill of bar.querySelectorAll('.search-scope-pill')) {
    const on = pill.dataset.scope === scope;
    pill.classList.toggle('active', on);
    pill.setAttribute('aria-selected', String(on));
  }
  if (file) {
    bar.querySelector('[data-scope="doc"]').title = `Search inside “${file.title}”`;
    bar.querySelector('[data-scope="all"]').title = 'Search every indexed document (Ctrl+K)';
  }
  // The placeholder is the other half of the signal: it says what Enter will search.
  $('searchInput').placeholder = scope === DOC && file
    ? 'Search this document…' : 'Search documents…';
}

// ---------- querying ----------

function schedule(value) {
  clearTimeout(timer);
  const text = value.trim();
  if (text.length < MIN_CHARS) { reset(); return; }
  timer = setTimeout(() => run(text), DEBOUNCE_MS);
}

async function run(text) {
  const mine = ++seq;
  query = text;
  const fileId = scopeFileId();
  if (fileId == null && scope === DOC) scope = ALL;   // the document was closed mid-query
  render(note('Searching…'));
  open();

  let result;
  try {
    result = await api.search(text, MAX_HITS, undefined, fileId);
  } catch (e) {
    if (mine !== seq) return;
    hits = [];
    render(note(`Search failed: ${e.message}`));
    return;
  }
  if (mine !== seq) return;   // a newer query has superseded this one

  hits = result.hits || [];
  scopedTo = fileId;
  cursor = -1;

  // A scoped request must come back scoped. An older server silently ignores the unknown
  // fileId parameter and answers with every document, which would otherwise be painted as
  // sections of the open one -- wrong titles, and rows that jump somewhere else entirely.
  if (fileId != null && hits.some((h) => h.fileId !== fileId)) {
    hits = [];
    render(note('This server build cannot search within a document. Restart it to pick up the change.'));
    return;
  }

  if (hits.length) renderHits();
  else render(note(await emptyMessage()));
}

// Zero hits can mean "nothing matches" or "the index is not ready yet"; say which.
async function emptyMessage() {
  if (status === null) {
    try { status = await api.searchStatus(); } catch { status = {}; }
  }
  if (status.enabled === false) return 'Search is disabled: no embedding model is loaded.';
  if (status.lastError) return `Search is unavailable: ${status.lastError}`;
  if (status.ready === false) return 'The search index is still starting up — try again shortly.';
  const pending = (status.pendingFiles || 0) + (status.pendingNotes || 0);
  if (pending) return `Still indexing ${pending} item(s) — try again shortly.`;
  if (scope === DOC) return `No matches for “${query}” in this document.`;
  return `No matches for “${query}”.`;
}

// ---------- panel ----------

function renderHits() {
  render(hits.map((h, i) => (scope === DOC ? sectionRow(h, i) : documentRow(h, i))).join(''));
}

function documentRow(h, i) {
  const isNote = h.noteKind != null;
  const noteBadge = isNote ? `<span class="badge note">${h.noteKind === 'dashboard' ? 'dashboard note' : 'note'}</span>` : '';
  const title = escapeHtml(h.title) || (isNote ? 'Note' : 'Untitled');
  const pathTitle = isNote ? (h.noteKind === 'dashboard' ? 'Dashboard note' : h.fullPath) : h.fullPath;
  return `
    <button class="search-hit${i === cursor ? ' active' : ''}" data-i="${i}"
            role="option" aria-selected="${i === cursor}" title="${escapeHtml(pathTitle)}">
      <span class="search-hit-head">
        <span class="search-hit-title">${title}</span>
        ${h.passageCount > 1 ? `<span class="search-hit-count">${h.passageCount} passages</span>` : ''}
        ${h.missing ? '<span class="badge missing">missing</span>' : ''}
        ${noteBadge}
      </span>
      <span class="search-hit-snippet">${escapeHtml(h.snippet)}</span>
    </button>`;
}

// A section is identified by its heading trail, with the ancestors dimmed so the eye lands
// on the heading itself. A passage that sits above every heading -- front matter, or a
// preamble -- has no trail, so it is named after the document it opens.
function sectionRow(h, i) {
  const path = (h.sectionPath || []);
  const parts = path.length ? path : [h.title];
  const label = parts
    .map((part, n) => `<span class="${n === parts.length - 1 ? 'search-hit-leaf' : 'search-hit-crumb'}">${escapeHtml(part)}</span>`)
    .join('<span class="search-hit-sep">›</span>');
  return `
    <button class="search-hit${i === cursor ? ' active' : ''}" data-i="${i}"
            role="option" aria-selected="${i === cursor}">
      <span class="search-hit-head">
        <span class="search-hit-title search-hit-path">${label}</span>
        ${h.passageCount > 1 ? `<span class="search-hit-count">${h.passageCount} passages</span>` : ''}
      </span>
      <span class="search-hit-snippet">${escapeHtml(h.snippet)}</span>
    </button>`;
}

function note(text) {
  return `<div class="search-note">${escapeHtml(text)}</div>`;
}

function render(html) {
  $('searchPanel').innerHTML = html;
}

function open() {
  $('searchPanel').classList.remove('hidden');
  $('searchInput').setAttribute('aria-expanded', 'true');
}

function close() {
  $('searchPanel').classList.add('hidden');
  $('searchInput').setAttribute('aria-expanded', 'false');
}

function reset() {
  clearTimeout(timer);
  seq++;                    // orphan any in-flight request
  hits = [];
  cursor = -1;
  query = '';
  render('');
  close();
  $('searchClear').classList.add('hidden');
}

function moveCursor(delta) {
  if (!hits.length) return;
  if (cursor < 0) cursor = delta > 0 ? 0 : hits.length - 1;
  else cursor = (cursor + delta + hits.length) % hits.length;
  renderHits();
  $('searchPanel').querySelector('.search-hit.active')
    ?.scrollIntoView({ block: 'nearest' });
}

function onKeyDown(e) {
  if (e.key === 'ArrowDown') { e.preventDefault(); open(); moveCursor(1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); open(); moveCursor(-1); }
  else if (e.key === 'Enter') {
    if (hits.length) { e.preventDefault(); go(hits[cursor < 0 ? 0 : cursor]); }
  } else if (e.key === 'Escape') {
    // Swallow it, so the app's global Escape handler does not also close the drawer.
    e.stopPropagation();
    if ($('searchPanel').classList.contains('hidden')) $('searchInput').blur();
    else close();
  }
}

// ---------- opening a hit ----------

async function go(hit) {
  if (!hit || !openFile) return;
  close();
  $('searchInput').blur();

  // A section hit belongs to the document already on screen: jump within it.
  if (scope === DOC && scopedTo === hit.fileId && activeFile()?.id === hit.fileId) {
    revealPassage(hit.snippet);
    return;
  }

  // Note hits open a read-only modal with the full note text.
  if (hit.noteKind === 'dashboard' || hit.noteKind === 'document') {
    await openNoteModal(hit);
    return;
  }

  await openFile(hit.fileId);
  scrollToTop();
}

async function openNoteModal(hit) {
  try {
    const note = await (hit.noteKind === 'dashboard'
      ? api.dashboardNote(hit.noteId)
      : api.docNote(hit.fileId, hit.noteId));
    viewNoteDialog(note, {
      fileTitle: hit.title,
      onOpenFile: hit.noteKind === 'document' ? () => navigate({ name: 'file', fileId: hit.fileId }) : undefined,
    });
  } catch (e) {
    toast(e.message, 'error');
  }
}

// The content area is reused across documents, so its scroll offset survives the swap and
// has to be cleared explicitly.
function scrollToTop() {
  document.querySelector('.content')?.scrollTo({ top: 0 });
}

// The document renders asynchronously (mermaid in particular), so retry briefly until the
// passage appears rather than assuming it is already in the DOM. Exported so other panels
// that show a snippet of the open document (the Ask chat's sources) can jump to it too.
export async function revealPassage(snippet) {
  const run = words(snippet).slice(0, RUN_MAX);
  if (run.length < RUN_MIN) return;

  for (let attempt = 0; attempt < 24; attempt++) {
    const docEl = currentDocEl();
    if (docEl && docEl.textContent) {
      const range = findPassage(docEl, run);
      if (range) { flash(range); return; }
    }
    await sleep(125);
  }
}

function currentDocEl() {
  const viewer = $('viewer');
  if (viewer && !viewer.classList.contains('hidden')) return viewer;
  const preview = $('editorPreview');
  if (preview && !preview.classList.contains('hidden')) return preview;
  return null;
}

// Tokenise the rendered text, remembering where each word sits in the DOM so a matched run
// can be turned back into a Range.
function tokenIndex(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
  const list = [];
  const spans = [];
  let node;
  while ((node = walker.nextNode())) {
    for (const m of node.textContent.matchAll(WORD)) {
      list.push(m[0].toLowerCase());
      spans.push({ node, start: m.index, end: m.index + m[0].length });
    }
  }
  return { words: list, spans };
}

function findPassage(root, run) {
  const idx = tokenIndex(root);
  if (!idx.words.length) return null;

  // Prefer the longest run; drop trailing words until it matches, since the snippet's tail
  // is the part most likely to be truncated or to contain stripped syntax.
  for (let len = run.length; len >= RUN_MIN; len--) {
    const at = findRun(idx.words, run.slice(0, len));
    if (at === -1) continue;
    const range = document.createRange();
    range.setStart(idx.spans[at].node, idx.spans[at].start);
    range.setEnd(idx.spans[at + len - 1].node, idx.spans[at + len - 1].end);
    return range;
  }
  return null;
}

function findRun(haystack, needle) {
  outer:
  for (let i = 0; i + needle.length <= haystack.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

// Scroll the passage into view and flash its block, which needs no DOM surgery and so
// cannot disturb the note highlights layered over the same text.
function flash(range) {
  let el = range.startContainer;
  if (el.nodeType === Node.TEXT_NODE) el = el.parentElement;
  const block = el?.closest('p, li, td, th, pre, blockquote, h1, h2, h3, h4, h5, h6') || el;
  if (!block) return;

  block.scrollIntoView({ block: 'center', behavior: 'smooth' });
  block.classList.remove('search-flash');
  void block.offsetWidth;              // restart the animation if it is already running
  block.classList.add('search-flash');
  setTimeout(() => block.classList.remove('search-flash'), 2400);
}

// ---------- helpers ----------

function words(text) {
  return [...(text || '').toLowerCase().matchAll(WORD)].map((m) => m[0]);
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
