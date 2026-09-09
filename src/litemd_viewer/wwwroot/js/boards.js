// Boards screen: a free-positioned, draggable card for each board.
// First stage: each board is just a name with a 3-dot Edit/Delete menu.

import { api } from './api.js';
import { toast, confirmDialog } from './ui.js';
import { popupMenu } from './tree.js';
import { createPanZoom } from './panzoom.js';
import { renderColorSwatches, contrastColor } from './colors.js';

const $ = (id) => document.getElementById(id);
const DRAG_THRESHOLD = 4;
const CARD_W = 180;
const CARD_H = 100;
const NEW_OFFSET = 28;

let board = null;
let pz = null;
let wired = false;
let topZ = 0;

export function initBoards() {
  if (wired) return;
  wired = true;
  board = $('boardsBoard');
  pz = createPanZoom($('boardsViewport'), board, { skipSelector: '.board-card' });

  const toolbar = $('boardsToolbar');
  if (toolbar) {
    toolbar.addEventListener('click', (e) => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'in') pz.zoomIn();
      else if (act === 'out') pz.zoomOut();
      else if (act === 'fit') pz.reset();
    });
  }

  $('boardsAddBtn')?.addEventListener('click', (e) => {
    e.stopPropagation();
    openBoardEditor();
  });
}

export async function renderBoards() {
  if (!board) board = $('boardsBoard');
  let boards = [];
  try { boards = await api.boards(); }
  catch (e) { toast(e.message, 'error'); return; }

  board.querySelectorAll('.board-card').forEach((n) => n.remove());
  topZ = boards.reduce((m, b) => Math.max(m, b.z || 0), 0);
  for (const b of boards) board.appendChild(buildBoard(b));
}

function buildBoard(b) {
  const el = document.createElement('div');
  el.className = 'board-card';
  el.dataset.id = b.id;
  el.style.insetInlineStart = (b.x || 0) + 'px';
  el.style.insetBlockStart = (b.y || 0) + 'px';
  el.style.zIndex = String(b.z || 0);
  el.__board = b;
  applyBoardColor(el, b.color);

  const title = document.createElement('span');
  title.className = 'board-card-title';
  title.dir = 'auto';
  title.textContent = b.name || 'Untitled board';
  el.appendChild(title);

  const menuBtn = document.createElement('button');
  menuBtn.className = 'board-card-menu icon-btn';
  menuBtn.title = 'Board actions';
  menuBtn.setAttribute('aria-label', 'Board actions');
  menuBtn.textContent = '⋯';
  el.appendChild(menuBtn);

  makeDraggable(el, {
    skipSelector: '.board-card-menu',
    onDrop: () => persistPosition(el),
  });

  menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    openBoardMenu(el, menuBtn);
  });

  return el;
}

function openBoardMenu(el, anchor) {
  const b = el.__board;
  popupMenu(anchor, [
    { label: 'Edit', onClick: () => openBoardEditor(b) },
    { label: 'Delete', danger: true, onClick: () => deleteBoard(el) },
  ]);
}

async function openBoardEditor(b) {
  const result = await boardEditor(b);
  if (!result?.name?.trim()) return;

  if (b) {
    try {
      const updated = await api.patchBoard(b.id, { name: result.name, color: result.color });
      b.name = updated.name;
      b.color = updated.color;
      const el = elForBoard(b.id);
      if (el) {
        el.querySelector('.board-card-title').textContent = updated.name;
        applyBoardColor(el, updated.color);
      }
      toast('Board updated', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  } else {
    const existing = board.querySelectorAll('.board-card');
    const x = existing.length * NEW_OFFSET;
    const y = existing.length * NEW_OFFSET;
    try {
      const created = await api.createBoard({ name: result.name, color: result.color, x, y });
      board.appendChild(buildBoard(created));
      bringToFront(board.lastElementChild);
      toast('Board created', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  }
}

function boardEditor(board) {
  return new Promise((resolve) => {
    let selectedColor = board?.color || null;
    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.innerHTML = `
      <div class="modal-card" style="width:min(360px,96vw)">
        <div class="modal-head">
          <strong>${board ? 'Edit board' : 'New board'}</strong>
          <button class="icon-btn" data-act="close" aria-label="Close">✕</button>
        </div>
        <div class="board-editor-body">
          <label class="board-editor-row">
            <span>Name</span>
            <input class="input board-editor-name" type="text" placeholder="Board name…" />
          </label>
          <label class="board-editor-row">
            <span>Background color</span>
            <div class="color-swatches"></div>
          </label>
        </div>
        <div class="modal-foot" style="justify-content:flex-end">
          <button class="btn" data-act="cancel">Cancel</button>
          <button class="btn primary" data-act="save">${board ? 'Save' : 'Create'}</button>
        </div>
      </div>`;
    const nameInput = overlay.querySelector('.board-editor-name');
    const swatches = overlay.querySelector('.color-swatches');
    nameInput.value = board?.name || '';
    renderColorSwatches(swatches, selectedColor, (c) => { selectedColor = c; });

    const close = (val) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(val); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    const submit = () => close({ name: nameInput.value.trim(), color: selectedColor });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) return close(null);
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'close' || act === 'cancel') close(null);
      if (act === 'save') submit();
    });
    nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    nameInput.focus();
    nameInput.select();
  });
}

function applyBoardColor(el, color) {
  el.style.backgroundColor = color || '';
  el.style.color = color ? contrastColor(color) : '';
}

async function deleteBoard(el) {
  const b = el.__board;
  if (!(await confirmDialog(`Delete board “${b.name}”?`, { okLabel: 'Delete', danger: true }))) return;
  try { await api.deleteBoard(b.id); el.remove(); toast('Board deleted', 'ok'); }
  catch (e) { toast(e.message, 'error'); }
}

function elForBoard(id) {
  return board?.querySelector(`.board-card[data-id="${id}"]`);
}

function bringToFront(el) {
  const z = ++topZ;
  el.style.zIndex = String(z);
  if (el.__board) el.__board.z = z;
}

function makeDraggable(el, { skipSelector = null, onDrop } = {}) {
  let sx = 0, sy = 0, ox = 0, oy = 0, dragging = false, moved = false, pid = 0;

  el.addEventListener('pointerdown', (e) => {
    if (e.button != null && e.button !== 0) return;
    if (skipSelector && e.target.closest(skipSelector)) return;
    dragging = true; moved = false;
    sx = e.clientX; sy = e.clientY;
    ox = el.offsetLeft; oy = el.offsetTop;
    pid = e.pointerId;
    bringToFront(el);
    el.setPointerCapture?.(pid);
    el.classList.add('dragging');
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
  });

  function onMove(e) {
    if (!dragging) return;
    const { scale, tx, ty } = pz ? pz.getTransform() : { scale: 1, tx: 0, ty: 0 };
    const dx = (e.clientX - sx) / scale, dy = (e.clientY - sy) / scale;
    if (!moved && Math.hypot(e.clientX - sx, e.clientY - sy) > DRAG_THRESHOLD) moved = true;
    if (!moved) return;
    const minX = -tx / scale, minY = -ty / scale;
    const maxX = (board.clientWidth - tx) / scale - el.offsetWidth;
    const maxY = (board.clientHeight - ty) / scale - el.offsetHeight;
    el.style.insetInlineStart = clamp(ox + dx, minX, maxX) + 'px';
    el.style.insetBlockStart = clamp(oy + dy, minY, maxY) + 'px';
  }

  function onUp() {
    dragging = false;
    el.releasePointerCapture?.(pid);
    el.classList.remove('dragging');
    el.removeEventListener('pointermove', onMove);
    el.removeEventListener('pointerup', onUp);
    el.removeEventListener('pointercancel', onUp);
    if (moved) onDrop?.();
  }
}

async function persistPosition(el) {
  const b = el.__board;
  const x = el.offsetLeft, y = el.offsetTop, z = b.z;
  b.x = x; b.y = y;
  try { await api.patchBoard(b.id, { x, y, z }); }
  catch (e) { toast(e.message, 'error'); }
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
