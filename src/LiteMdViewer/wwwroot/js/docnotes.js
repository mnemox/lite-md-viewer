// Per-document notes: a collapsible top-right panel plus a bottom-right "+" FAB shown while a
// file is open. Notes are markdown, rendered with renderMarkdown() and edited inline in the
// panel (a textarea swaps in for the rendered body). Mirrors the FAB/popup idiom from
// dashboard.js and the toast/confirm helpers from ui.js.

import { api } from './api.js';
import { renderMarkdown } from './render.js';
import { toast, confirmDialog } from './ui.js';

const $ = (id) => document.getElementById(id);
const COLLAPSE_KEY = 'docNotesCollapsed';
const WIDTH_KEY = 'docNotesWidth';

let wired = false;
let fileId = null;   // active document id (null when not viewing a file)
let notes = [];      // the active document's notes

// ---------- public API ----------

// Wire the FAB, its popup, the panel's add button, and the collapse toggle once, at init().
export function initDocNotes() {
  if (wired) return;
  wired = true;

  const fab = $('docFab');
  const menu = $('docFabMenu');
  const closeMenu = () => {
    if (menu.classList.contains('hidden')) return false;
    menu.classList.add('hidden');
    fab.setAttribute('aria-expanded', 'false');
    return true;
  };
  const toggleMenu = () => {
    const show = menu.classList.contains('hidden');
    menu.classList.toggle('hidden', !show);
    fab.setAttribute('aria-expanded', String(show));
  };

  fab.onclick = (e) => { e.stopPropagation(); toggleMenu(); };
  $('addDocNoteOpt').onclick = () => { closeMenu(); startAdd(); };
  $('docNotesAdd').onclick = () => startAdd();

  // Close the popup on an outside click or Escape (mirrors the dashboard FAB).
  document.addEventListener('mousedown', (e) => {
    if (!menu.contains(e.target) && e.target !== fab) closeMenu();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

  // Collapsible panel; the collapsed/expanded choice persists across reloads.
  const panel = $('docNotesPanel');
  setCollapsed(localStorage.getItem(COLLAPSE_KEY) === '1');
  $('docNotesToggle').onclick = () => {
    const now = !panel.classList.contains('collapsed');
    setCollapsed(now);
    localStorage.setItem(COLLAPSE_KEY, now ? '1' : '0');
  };

  // Resizable width via the drag handle on the panel's left edge.
  const savedWidth = parseInt(localStorage.getItem(WIDTH_KEY), 10);
  if (savedWidth) panel.style.width = savedWidth + 'px';
  const handle = $('docNotesResize');
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = panel.getBoundingClientRect().width;
    const min = parseFloat(getComputedStyle(panel).minWidth) || 220;
    const max = parseFloat(getComputedStyle(panel).maxWidth) || 720;
    handle.classList.add('active');
    handle.setPointerCapture(e.pointerId);
    const onMove = (ev) => {
      // Panel is anchored to the right edge, so dragging left (negative dx) grows it.
      const dx = ev.clientX - startX;
      const width = Math.min(max, Math.max(min, startWidth - dx));
      panel.style.width = width + 'px';
    };
    const onUp = () => {
      handle.classList.remove('active');
      handle.releasePointerCapture(e.pointerId);
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      localStorage.setItem(WIDTH_KEY, String(Math.round(panel.getBoundingClientRect().width)));
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
  });
}

// Fetch and render the notes for a freshly-opened document.
export async function loadDocNotes(id) {
  fileId = id;
  notes = [];
  render();
  try { notes = await api.docNotes(id); }
  catch (e) { toast(e.message, 'error'); notes = []; }
  render();
}

// Reset the panel when leaving the file view (dashboard / welcome).
export function clearDocNotes() {
  fileId = null;
  notes = [];
  $('docFabMenu')?.classList.add('hidden');
  render();
}

// ---------- rendering ----------

function render() {
  const list = $('docNotesList');
  if (!list) return;
  const count = $('docNotesCount');
  if (count) count.textContent = String(notes.length);
  list.innerHTML = '';
  if (notes.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'doc-notes-empty';
    empty.textContent = 'No notes yet.';
    list.appendChild(empty);
    return;
  }
  for (const note of notes) list.appendChild(buildNote(note));
}

function buildNote(note) {
  const el = document.createElement('div');
  el.className = 'doc-note';
  el.dataset.id = note.id;

  const body = document.createElement('article');
  body.className = 'viewer markdown-body doc-note-body';
  body.dir = 'auto';
  renderMarkdown(note.text || '', body);

  const actions = document.createElement('div');
  actions.className = 'doc-note-actions';
  const editBtn = document.createElement('button');
  editBtn.className = 'doc-note-act';
  editBtn.textContent = 'Edit';
  editBtn.onclick = () => editNote(el, note);
  const delBtn = document.createElement('button');
  delBtn.className = 'doc-note-act danger';
  delBtn.textContent = 'Delete';
  delBtn.onclick = () => deleteNote(note);
  actions.append(editBtn, delBtn);

  el.append(body, actions);
  return el;
}

// ---------- inline editor (add + edit) ----------

// Build a textarea editor card. onSave receives the trimmed-or-raw value; onCancel restores.
function buildEditor(value, onSave, onCancel) {
  const el = document.createElement('div');
  el.className = 'doc-note editing';
  el.innerHTML = `
    <textarea class="editor-text doc-note-input" spellcheck="false" dir="auto"
              placeholder="Write markdown…"></textarea>
    <div class="doc-note-actions">
      <button class="doc-note-act" data-act="cancel">Cancel</button>
      <button class="doc-note-act primary" data-act="save">Save</button>
    </div>`;
  const ta = el.querySelector('textarea');
  ta.value = value;
  el.querySelector('[data-act="save"]').onclick = () => onSave(ta.value);
  el.querySelector('[data-act="cancel"]').onclick = () => onCancel();
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); onCancel(); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onSave(ta.value); }
  });
  return { el, ta };
}

function startAdd() {
  if (fileId == null) return;
  ensureExpanded();
  const list = $('docNotesList');
  list.querySelector('.doc-notes-empty')?.remove();
  const { el, ta } = buildEditor('', async (text) => {
    if (!text.trim()) { toast('Nothing to save', 'error'); return; }
    try {
      const dto = await api.createDocNote(fileId, text);
      notes.push(dto);
      render();
      toast('Note added', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  }, () => render());
  list.prepend(el);
  ta.focus();
}

function editNote(cardEl, note) {
  const { el, ta } = buildEditor(note.text || '', async (text) => {
    if (!text.trim()) { toast('Nothing to save', 'error'); return; }
    try {
      await api.patchDocNote(fileId, note.id, { text });
      note.text = text;
      render();
      toast('Note updated', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  }, () => render());
  cardEl.replaceWith(el);
  ta.focus();
}

async function deleteNote(note) {
  if (!(await confirmDialog('Delete this note?', { okLabel: 'Delete', danger: true }))) return;
  try {
    await api.deleteDocNote(fileId, note.id);
    notes = notes.filter((n) => n.id !== note.id);
    render();
    toast('Note deleted', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

// ---------- helpers ----------

function setCollapsed(on) {
  $('docNotesPanel').classList.toggle('collapsed', on);
  $('docNotesToggle').setAttribute('aria-expanded', String(!on));
}

function ensureExpanded() {
  if ($('docNotesPanel').classList.contains('collapsed')) {
    setCollapsed(false);
    localStorage.setItem(COLLAPSE_KEY, '0');
  }
}
