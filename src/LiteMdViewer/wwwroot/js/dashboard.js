// Dashboard sticky-notes board.
//   - A "+" FAB (bottom-right) opens a popup: "Add note" | "Add flipping note".
//   - Notes are markdown cards, freely draggable; their position (x/y) and stacking
//     (z) persist server-side, so a dragged note stays put across refreshes.
//   - Overflowing text fades at the bottom; clicking a plain note opens it full-size.
//     A flip note flips on click (front <-> back); if the newly shown side overflows
//     it auto-opens full-size to reveal all the text.
//   - A hover "⋯" menu gives Open / Flip / Edit / Delete.
//
// Reuses renderMarkdown() for bodies, the .modal card pattern for editor + full-size,
// popupMenu() for the per-note menu, and the pointer-drag idiom from panzoom.js.

import { api } from './api.js';
import { renderMarkdown } from './render.js';
import { toast, confirmDialog } from './ui.js';
import { popupMenu } from './tree.js';
import { createPanZoom } from './panzoom.js';

const $ = (id) => document.getElementById(id);
const DRAG_THRESHOLD = 4;   // px moved before a press counts as a drag (vs a click)
const NEW_OFFSET = 26;      // cascade step for stacking freshly-created notes

let board = null;
let pz = null;              // board pan/zoom controller (wheel-zoom + drag-pan)
let wired = false;
let topZ = 0;               // highest z-index in play (for bring-to-front)

// ---------- public API ----------

// Wire the FAB and its popup once, at app init().
export function initDashboard() {
  if (wired) return;
  wired = true;
  board = $('dashboardBoard');

  // Wheel-zoom + drag-pan the whole board. skipSelector keeps a press on a note out of
  // the pan gesture so the note's own drag/click/flip handlers still fire.
  pz = createPanZoom($('dashboardViewport'), board, { skipSelector: '.dash-note' });
  document.querySelector('.dash-toolbar').addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'in') pz.zoomIn();
    else if (act === 'out') pz.zoomOut();
    else if (act === 'fit') pz.reset();
  });

  const fab = $('dashFab');
  const menu = $('dashFabMenu');
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
  $('addNoteOpt').onclick = () => { closeMenu(); openEditor({ kind: 'note' }); };
  $('addFlipNoteOpt').onclick = () => { closeMenu(); openEditor({ kind: 'flip' }); };

  // Close the popup on an outside click or Escape (mirrors the drawer's Add menu).
  document.addEventListener('mousedown', (e) => {
    if (!menu.contains(e.target) && e.target !== fab) closeMenu();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
}

// Fetch and (re)render the whole board. Called whenever the dashboard is shown.
export async function renderDashboardNotes() {
  if (!board) board = $('dashboardBoard');
  let notes;
  try { notes = await api.dashboardNotes(); }
  catch (e) { toast(e.message, 'error'); return; }

  board.querySelectorAll('.dash-note').forEach((n) => n.remove());
  topZ = notes.reduce((m, n) => Math.max(m, n.z || 0), 0);
  for (const note of notes) board.appendChild(buildNote(note));
}

// ---------- note rendering ----------

function buildNote(note) {
  const isFlip = note.kind === 'flip';
  const el = document.createElement('div');
  el.className = 'dash-note' + (isFlip ? ' flip' : '');
  el.dataset.id = note.id;
  el.style.insetInlineStart = (note.x || 0) + 'px';
  el.style.insetBlockStart = (note.y || 0) + 'px';
  el.style.zIndex = String(note.z || 0);
  el.__note = note;

  const menuBtn = document.createElement('button');
  menuBtn.className = 'dash-note-menu icon-btn';
  menuBtn.title = 'Note actions';
  menuBtn.setAttribute('aria-label', 'Note actions');
  menuBtn.textContent = '⋯';
  el.appendChild(menuBtn);

  if (isFlip) {
    const inner = document.createElement('div');
    inner.className = 'dash-note-inner';
    const front = faceEl('front');
    const back = faceEl('back');
    inner.appendChild(front);
    inner.appendChild(back);
    el.appendChild(inner);
    renderFace(front, note.frontText);
    renderFace(back, note.backText);
  } else {
    const front = faceEl('front');
    el.appendChild(front);
    renderFace(front, note.frontText);
  }

  wireNote(el, menuBtn);
  return el;
}

function faceEl(side) {
  const f = document.createElement('div');
  f.className = `dash-note-face dash-note-${side} dash-note-content markdown-body`;
  f.dir = 'auto';
  return f;
}

// Render markdown into a face, then flag overflow so the fade shows only when needed.
async function renderFace(faceEl, text) {
  await renderMarkdown(text || '', faceEl);
  requestAnimationFrame(() => {
    const overflowing = faceEl.scrollHeight > faceEl.clientHeight + 1;
    faceEl.classList.toggle('overflowing', overflowing);
  });
}

// ---------- interactions (drag vs click, flip, menu) ----------

function wireNote(el, menuBtn) {
  let sx = 0, sy = 0, ox = 0, oy = 0, dragging = false, moved = false;

  el.addEventListener('pointerdown', (e) => {
    if (e.button != null && e.button !== 0) return;
    if (e.target.closest('.dash-note-menu')) return;   // menu button is not a drag handle
    if (e.target.closest('a[href]')) return;           // let links behave normally
    dragging = true; moved = false;
    sx = e.clientX; sy = e.clientY;
    ox = el.offsetLeft; oy = el.offsetTop;
    bringToFront(el);
    el.setPointerCapture?.(e.pointerId);
    el.classList.add('dragging');
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
  });

  function onMove(e) {
    if (!dragging) return;
    // Screen movement is divided by the board's zoom so the note tracks the cursor
    // 1:1 on screen while its stored x/y stay in unscaled board coordinates.
    const { scale, tx, ty } = pz ? pz.getTransform() : { scale: 1, tx: 0, ty: 0 };
    const dx = (e.clientX - sx) / scale, dy = (e.clientY - sy) / scale;
    if (!moved && Math.hypot(e.clientX - sx, e.clientY - sy) > DRAG_THRESHOLD) moved = true;
    if (!moved) return;
    // Clamp to the board region the viewport currently shows (in board coords), inverting
    // the pan/zoom transform. This lets a note be dropped anywhere on screen — including
    // the extra space revealed by zooming out — not just the frame that filled the
    // viewport at 1:1. board.clientWidth/Height are the viewport size (the board is inset:0).
    const minX = -tx / scale, minY = -ty / scale;
    const maxX = (board.clientWidth - tx) / scale - el.offsetWidth;
    const maxY = (board.clientHeight - ty) / scale - el.offsetHeight;
    el.style.insetInlineStart = clamp(ox + dx, minX, maxX) + 'px';
    el.style.insetBlockStart = clamp(oy + dy, minY, maxY) + 'px';
  }

  function onUp(e) {
    dragging = false;
    el.releasePointerCapture?.(e.pointerId);
    el.classList.remove('dragging');
    el.removeEventListener('pointermove', onMove);
    el.removeEventListener('pointerup', onUp);
    el.removeEventListener('pointercancel', onUp);
    if (moved) persistPosition(el);
    else onNoteClick(el, e);
  }

  menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    openNoteMenu(el, menuBtn);
  });
}

function onNoteClick(el, e) {
  if (e.target.closest('a[href]')) return;   // a link click already did its thing
  const note = el.__note;
  if (note.kind === 'flip') {
    const nowFlipped = !el.classList.contains('flipped');
    el.classList.toggle('flipped', nowFlipped);
    // After the flip settles, if the visible side overflows, open it full-size.
    const faceSel = nowFlipped ? '.dash-note-back' : '.dash-note-front';
    const face = el.querySelector(faceSel);
    setTimeout(() => {
      if (face.classList.contains('overflowing')) {
        openFullSize(nowFlipped ? 'Back' : 'Front', nowFlipped ? note.backText : note.frontText);
      }
    }, 260);
  } else {
    openFullSize('Note', note.frontText);
  }
}

function openNoteMenu(el, anchor) {
  const note = el.__note;
  const items = [
    { label: 'Open full size', onClick: () => {
        const flipped = el.classList.contains('flipped');
        openFullSize(note.kind === 'flip' ? (flipped ? 'Back' : 'Front') : 'Note',
          note.kind === 'flip' && flipped ? note.backText : note.frontText);
      } },
  ];
  if (note.kind === 'flip') {
    items.push({ label: 'Flip', onClick: () => el.classList.toggle('flipped') });
  }
  items.push({ label: 'Edit', onClick: () => openEditor({ kind: note.kind, el }) });
  items.push({ label: 'Delete', danger: true, onClick: () => deleteNote(el) });
  popupMenu(anchor, items);
}

function bringToFront(el) {
  const z = ++topZ;
  el.style.zIndex = String(z);
  if (el.__note) el.__note.z = z;
}

async function persistPosition(el) {
  const note = el.__note;
  const x = el.offsetLeft, y = el.offsetTop, z = note.z;
  note.x = x; note.y = y;
  try { await api.patchNote(note.id, { x, y, z }); }
  catch (e) { toast(e.message, 'error'); }
}

async function deleteNote(el) {
  const note = el.__note;
  if (!(await confirmDialog('Delete this note?', { okLabel: 'Delete', danger: true }))) return;
  try { await api.deleteNote(note.id); el.remove(); toast('Note deleted', 'ok'); }
  catch (e) { toast(e.message, 'error'); }
}

// ---------- editor modal (create + edit) ----------

// opts: { kind: 'note'|'flip', el?: existing note element (edit mode) }
function openEditor({ kind, el = null }) {
  const editing = !!el;
  const note = editing ? el.__note : null;
  const flip = kind === 'flip';
  const overlay = document.createElement('div');
  overlay.className = 'modal';
  const title = editing ? 'Edit note' : (flip ? 'New flipping note' : 'New note');

  const row = (label, id, value) => `
    <div class="note-edit-row">
      ${label ? `<span class="note-edit-label">${label}</span>` : ''}
      <div class="note-edit-grid">
        <textarea class="editor-text note-edit-src" id="${id}" spellcheck="false" dir="auto"
                  placeholder="Write markdown…"></textarea>
        <article class="viewer markdown-body note-edit-preview" id="${id}Prev" dir="auto"></article>
      </div>
    </div>`;

  overlay.innerHTML = `
    <div class="modal-card note-edit-card" style="width:min(860px,96vw)">
      <div class="modal-head">
        <strong>${title}</strong>
        <button class="icon-btn" data-act="close" aria-label="Close">✕</button>
      </div>
      <div class="note-edit-body">
        ${row(flip ? 'Front' : '', 'noteFront', '')}
        ${flip ? row('Back', 'noteBack', '') : ''}
      </div>
      <div class="modal-foot" style="justify-content:flex-end">
        <button class="btn" data-act="cancel">Cancel</button>
        <button class="btn primary" data-act="save">Save</button>
      </div>
    </div>`;

  const frontSrc = overlay.querySelector('#noteFront');
  const backSrc = flip ? overlay.querySelector('#noteBack') : null;
  if (editing) {
    frontSrc.value = note.frontText || '';
    if (backSrc) backSrc.value = note.backText || '';
  }

  // Live preview (debounced), mirroring the main editor.
  const bindPreview = (src, prevId) => {
    const prev = overlay.querySelector('#' + prevId);
    let t = null;
    const run = () => renderMarkdown(src.value, prev);
    src.addEventListener('input', () => { clearTimeout(t); t = setTimeout(run, 300); });
    run();
  };
  bindPreview(frontSrc, 'noteFrontPrev');
  if (backSrc) bindPreview(backSrc, 'noteBackPrev');

  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };

  const save = async () => {
    const frontText = frontSrc.value;
    const backText = backSrc ? backSrc.value : '';
    if (!frontText.trim() && !backText.trim()) { toast('Nothing to save', 'error'); return; }
    try {
      if (editing) {
        await api.patchNote(note.id, { frontText, backText });
        note.frontText = frontText; note.backText = backText;
        refreshFaces(el);
        toast('Note updated', 'ok');
      } else {
        const pos = nextPosition();
        const dto = await api.createNote({ kind, frontText, backText, x: pos.x, y: pos.y });
        topZ = Math.max(topZ, dto.z || 0);
        board.appendChild(buildNote(dto));
        toast('Note added', 'ok');
      }
      close();
    } catch (e) { toast(e.message, 'error'); }
  };

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) return close();
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'close' || act === 'cancel') close();
    if (act === 'save') save();
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  frontSrc.focus();
}

// Re-render an edited note's faces (and refresh overflow flags) in place.
function refreshFaces(el) {
  const note = el.__note;
  const front = el.querySelector('.dash-note-front');
  if (front) renderFace(front, note.frontText);
  const back = el.querySelector('.dash-note-back');
  if (back) renderFace(back, note.backText);
}

// ---------- full-size view ----------

function openFullSize(label, text) {
  const overlay = document.createElement('div');
  overlay.className = 'modal';
  overlay.innerHTML = `
    <div class="modal-card" style="width:min(900px,96vw)">
      <div class="modal-head">
        <strong>${label}</strong>
        <button class="icon-btn" data-act="close" aria-label="Close">✕</button>
      </div>
      <article class="viewer markdown-body note-full" dir="auto"></article>
    </div>`;
  renderMarkdown(text || '', overlay.querySelector('.note-full'));
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) return close();
    if (e.target.closest('[data-act="close"]')) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
}

// ---------- helpers ----------

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// Cascade newly-created notes so they don't stack exactly on top of each other.
function nextPosition() {
  const n = board.querySelectorAll('.dash-note').length;
  const step = (n % 6) * NEW_OFFSET;
  return { x: 28 + step, y: 24 + step };
}
