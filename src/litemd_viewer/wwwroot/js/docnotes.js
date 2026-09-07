// Per-document notes: a collapsible top-right panel plus a bottom-right "+" FAB shown while a
// file is open. Notes are markdown, rendered with renderMarkdown(). Add/edit opens a centered
// modal with a live markdown preview on the right; the panel itself only lists notes.

import { api } from './api.js';
import { renderMarkdown } from './render.js';
import { toast, confirmDialog } from './ui.js';
import { popupMenu } from './tree.js';
import { getSelectionInfo, showArrows, hideArrows, highlightNotes } from './noteRefs.js';

const $ = (id) => document.getElementById(id);
const OPEN_KEY = 'docNotesOpen';
const COLLAPSE_KEY = 'docNotesCollapsed';
const WIDTH_KEY = 'docNotesWidth';
const MAX_REF_PREVIEW = 30;
const LINK_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';

let wired = false;
let fileId = null;   // active document id (null when not viewing a file)
let notes = [];      // the active document's notes
let editingNote = null; // note currently being edited in the modal (null when adding)

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
  $('addDocNoteOpt').onclick = () => { closeMenu(); openNoteEditor(); };
  $('docNotesAdd').onclick = () => openNoteEditor();

  // Note editor modal wiring.
  const editorText = $('noteEditorText');
  editorText.addEventListener('input', () => previewNote());
  editorText.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); closeNoteEditor(); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveNote(); }
  });
  $('noteEditorClose').onclick = closeNoteEditor;
  $('noteEditorCancel').onclick = closeNoteEditor;
  $('noteEditorSave').onclick = saveNote;
  $('noteEditorModal').addEventListener('mousedown', (e) => {
    if (e.target === $('noteEditorModal')) closeNoteEditor();
  });

  // Close the popup on an outside click or Escape (mirrors the dashboard FAB).
  document.addEventListener('mousedown', (e) => {
    if (!menu.contains(e.target) && e.target !== fab) closeMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('noteEditorModal').classList.contains('hidden')) { closeNoteEditor(); return; }
      closeMenu();
    }
  });

  // Visibility is toggled from the topbar Notes button (like Ask). The panel is hidden by
  // default and only appears when a file is open. A separate collapse toggle shrinks/expands
  // the list once the panel is visible.
  const panel = $('docNotesPanel');
  const notesBtn = $('notesBtn');
  setOpen(localStorage.getItem(OPEN_KEY) === '1');
  setCollapsed(localStorage.getItem(COLLAPSE_KEY) === '1');
  $('docNotesToggle').onclick = () => {
    const now = !panel.classList.contains('collapsed');
    setCollapsed(now);
    localStorage.setItem(COLLAPSE_KEY, now ? '1' : '0');
  };
  if (notesBtn) notesBtn.onclick = () => setOpen(!panel.classList.contains('open'));

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
      // Panel is anchored to the right edge with the handle on the left, so dragging left
      // (negative dx) grows it.
      const dx = startX - ev.clientX;
      const width = Math.min(max, Math.max(min, startWidth + dx));
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

export function refreshDocHighlights() {
  if (!fileId) return;
  highlightNotes(notes);
}

// Reset the panel when leaving the file view (dashboard / welcome).
export function clearDocNotes() {
  fileId = null;
  notes = [];
  $('docFabMenu')?.classList.add('hidden');
  closeNoteEditor();
  hideArrows();
  render();
}

// ---------- rendering ----------

function render() {
  const list = $('docNotesList');
  if (!list) return;
  hideArrows();
  const count = $('docNotesCount');
  if (count) count.textContent = String(notes.length);
  const btnCount = $('notesBtnCount');
  if (btnCount) {
    btnCount.textContent = String(notes.length);
    btnCount.classList.toggle('hidden', notes.length === 0);
  }
  list.innerHTML = '';
  if (notes.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'doc-notes-empty';
    empty.textContent = 'No notes yet.';
    list.appendChild(empty);
    highlightNotes(notes);
    return;
  }
  for (const note of notes) list.appendChild(buildNote(note));
  highlightNotes(notes);
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
  editBtn.onclick = () => openNoteEditor(note);
  const relBtn = document.createElement('button');
  relBtn.className = 'doc-note-act rel';
  relBtn.title = note.references?.length ? `${note.references.length} linked passage(s)` : 'Link selected text';
  relBtn.innerHTML = LINK_ICON;
  if (note.references?.length) {
    const badge = document.createElement('span');
    badge.className = 'note-ref-count';
    badge.textContent = String(note.references.length);
    relBtn.appendChild(badge);
  }
  relBtn.onclick = (e) => { e.stopPropagation(); onRelClick(note, relBtn); };
  relBtn.onmouseenter = () => { if (note.references?.length) showArrows(note.references, relBtn); };
  const delBtn = document.createElement('button');
  delBtn.className = 'doc-note-act danger';
  delBtn.textContent = 'Delete';
  delBtn.onclick = () => deleteNote(note);
  actions.append(editBtn, relBtn, delBtn);

  el.append(body, actions);
  return el;
}

// ---------- note editor modal (add + edit) ----------

function previewNote() {
  renderMarkdown($('noteEditorText').value, $('noteEditorPreview'));
}

function openNoteEditor(note = null) {
  if (fileId == null) return;
  editingNote = note || null;
  $('noteEditorTitle').textContent = note ? 'Edit note' : 'Add note';
  $('noteEditorText').value = note ? (note.text || '') : '';
  previewNote();
  $('noteEditorModal').classList.remove('hidden');
  $('noteEditorText').focus();
  ensureExpanded();
}

function closeNoteEditor() {
  $('noteEditorModal').classList.add('hidden');
  editingNote = null;
}

async function saveNote() {
  if (fileId == null) return;
  const text = $('noteEditorText').value;
  if (!text.trim()) { toast('Nothing to save', 'error'); return; }
  try {
    if (editingNote) {
      const dto = await api.patchDocNote(fileId, editingNote.id, { text });
      Object.assign(editingNote, dto);
      hideArrows();
      render();
      toast('Note updated', 'ok');
    } else {
      const dto = await api.createDocNote(fileId, text);
      notes.push(dto);
      render();
      toast('Note added', 'ok');
    }
    closeNoteEditor();
  } catch (e) { toast(e.message, 'error'); }
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

async function onRelClick(note, btn) {
  const sel = getSelectionInfo();
  if (sel) {
    try {
      const ref = await api.addNoteRef(fileId, note.id, sel.start, sel.length, sel.text);
      note.references = [...(note.references || []), ref];
      hideArrows();
      render();
      const newBtn = document.querySelector(`.doc-note[data-id="${note.id}"] .doc-note-act.rel`);
      if (newBtn) showArrows(note.references, newBtn);
      toast('Linked', 'ok');
    } catch (e) { toast(e.message, 'error'); }
    return;
  }
  showRefMenu(btn, note);
}

function showRefMenu(btn, note) {
  const refs = note.references || [];
  const items = [{ label: refs.length ? `${refs.length} linked passage(s)` : 'No linked passages', disabled: true }];
  for (const ref of refs) {
    const preview = ref.text.length > MAX_REF_PREVIEW ? ref.text.slice(0, MAX_REF_PREVIEW) + '…' : ref.text;
    items.push({ label: `Unlink “${preview}”`, danger: true, onClick: () => deleteRef(note, ref.id) });
  }
  popupMenu(btn, items);
}

async function deleteRef(note, refId) {
  try {
    await api.deleteNoteRef(fileId, note.id, refId);
    note.references = note.references.filter((r) => r.id !== refId);
    hideArrows();
    render();
    toast('Unlinked', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

function ensureExpanded() {
  if (!fileId) return;
  setOpen(true);
  if ($('docNotesPanel').classList.contains('collapsed')) {
    setCollapsed(false);
    localStorage.setItem(COLLAPSE_KEY, '0');
  }
}

function setOpen(on) {
  const panel = $('docNotesPanel');
  const btn = $('notesBtn');
  const wasOpen = panel.classList.contains('open');
  panel.classList.toggle('open', on);
  if (btn) btn.classList.toggle('active', on);
  if (on) {
    if (!wasOpen) localStorage.setItem(OPEN_KEY, '1');
  } else {
    if (wasOpen) localStorage.setItem(OPEN_KEY, '0');
  }
}
