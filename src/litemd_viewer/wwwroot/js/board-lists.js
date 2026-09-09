// One board opened as Trello-style columns. Lists hold cards; both reorder by dragging, and
// cards can be dragged between lists. Every drag persists through the two reorder endpoints
// (api.reorderLists / api.reorderCards) once the drop settles.

import { api } from './api.js';
import { toast, confirmDialog } from './ui.js';
import { navigate } from './router.js';
import { contrastColor } from './colors.js';

const $ = (id) => document.getElementById(id);

let canvas = null;
let header = null;
let titleEl = null;
let wired = false;

let boardId = null;
// Drag state, shared by the delegated handlers on the canvas.
let draggingEl = null;
let dragKind = null;      // 'card' | 'list'
let srcListId = null;     // the list a card started in, to renumber it after a cross-list move

export function initBoardLists() {
  if (wired) return;
  wired = true;
  canvas = $('blCanvas');
  header = document.querySelector('.bl-header');
  titleEl = $('blTitle');

  $('blBack')?.addEventListener('click', () => navigate({ name: 'boards' }));

  // One set of delegated drag handlers for every list and card on the canvas.
  canvas.addEventListener('dragstart', onDragStart);
  canvas.addEventListener('dragover', onDragOver);
  canvas.addEventListener('drop', (e) => e.preventDefault());
  canvas.addEventListener('dragend', onDragEnd);
}

export async function renderBoardLists(id) {
  boardId = id;
  if (!canvas) canvas = $('blCanvas');

  let board, lists;
  try {
    [board, lists] = await Promise.all([api.board(id), api.boardLists(id)]);
  } catch (e) {
    toast(e.message, 'error');
    navigate({ name: 'boards' });
    return;
  }

  titleEl.textContent = board.name || 'Untitled board';
  tintHeader(board.color);

  canvas.replaceChildren();
  for (const list of lists) canvas.appendChild(buildList(list));
  canvas.appendChild(buildAddList());
}

function tintHeader(color) {
  if (!header) return;
  header.style.backgroundColor = color || '';
  header.style.color = color ? contrastColor(color) : '';
}

// ------------------------------------------------------------------------- lists
function buildList(list) {
  const el = document.createElement('section');
  el.className = 'bl-list';
  el.dataset.id = list.id;
  el.draggable = true;
  el.__list = list;

  const head = document.createElement('div');
  head.className = 'bl-list-head';

  const name = document.createElement('div');
  name.className = 'bl-list-title';
  name.dir = 'auto';
  name.textContent = list.name || 'Untitled list';
  name.tabIndex = 0;
  name.addEventListener('click', () => editListTitle(name, list));
  head.appendChild(name);

  const del = document.createElement('button');
  del.className = 'bl-list-del icon-btn';
  del.title = 'Delete list';
  del.setAttribute('aria-label', 'Delete list');
  del.textContent = '×';
  del.addEventListener('click', () => deleteList(el, list));
  head.appendChild(del);

  el.appendChild(head);

  const cards = document.createElement('div');
  cards.className = 'bl-cards';
  for (const card of list.cards || []) cards.appendChild(buildCard(card));
  el.appendChild(cards);

  el.appendChild(buildAddCard(cards, list.id));
  return el;
}

function editListTitle(name, list) {
  openInlineEditor(name, {
    value: list.name || '',
    multiline: false,
    onCommit: async (val) => {
      try {
        const updated = await api.patchList(boardId, list.id, { name: val });
        list.name = updated.name;
        name.textContent = updated.name || 'Untitled list';
      } catch (e) {
        toast(e.message, 'error');
      }
    },
  });
}

async function deleteList(el, list) {
  const count = (el.querySelectorAll('.bl-card') || []).length;
  const suffix = count ? ` and its ${count} card${count === 1 ? '' : 's'}` : '';
  if (!(await confirmDialog(`Delete list “${list.name}”${suffix}?`, { okLabel: 'Delete', danger: true }))) return;
  try {
    await api.deleteList(boardId, list.id);
    el.remove();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function buildAddList() {
  const wrap = document.createElement('div');
  wrap.className = 'bl-add-list';
  const btn = document.createElement('button');
  btn.className = 'bl-add-btn';
  btn.textContent = '+ Add a list';
  wrap.appendChild(btn);

  btn.addEventListener('click', () => {
    const restore = suspendAncestorsDrag(wrap);
    const input = document.createElement('input');
    input.className = 'bl-input';
    input.placeholder = 'List name…';
    wrap.replaceChildren(input);
    input.focus();

    let done = false;
    const reset = () => { if (done) return; done = true; restore(); wrap.replaceChildren(btn); };
    const submit = async () => {
      const val = input.value.trim();
      if (!val) { reset(); return; }
      try {
        const created = await api.createList(boardId, val);
        canvas.insertBefore(buildList(created), wrap);
        // Keep composing so several lists can be added in a row.
        done = true; restore(); wrap.replaceChildren(btn); btn.click();
      } catch (e) {
        toast(e.message, 'error');
        reset();
      }
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); submit(); }
      else if (e.key === 'Escape') { e.preventDefault(); reset(); }
    });
    input.addEventListener('blur', reset);
  });

  return wrap;
}

// ------------------------------------------------------------------------- cards
function buildCard(card) {
  const el = document.createElement('div');
  el.className = 'bl-card';
  el.dataset.id = card.id;
  el.draggable = true;
  el.__card = card;

  const text = document.createElement('div');
  text.className = 'bl-card-text';
  text.dir = 'auto';
  text.textContent = card.text || '';
  text.addEventListener('click', () => editCard(el, text, card));
  el.appendChild(text);

  const del = document.createElement('button');
  del.className = 'bl-card-del icon-btn';
  del.title = 'Delete card';
  del.setAttribute('aria-label', 'Delete card');
  del.textContent = '×';
  del.addEventListener('click', (e) => { e.stopPropagation(); deleteCard(el, card); });
  el.appendChild(del);

  return el;
}

function editCard(el, text, card) {
  openInlineEditor(text, {
    value: card.text || '',
    multiline: true,
    onCommit: async (val) => {
      const listId = Number(el.closest('.bl-list').dataset.id);
      try {
        const updated = await api.patchCard(boardId, listId, card.id, { text: val });
        card.text = updated.text;
        text.textContent = updated.text || '';
      } catch (e) {
        toast(e.message, 'error');
      }
    },
  });
}

async function deleteCard(el, card) {
  const listId = Number(el.closest('.bl-list').dataset.id);
  try {
    await api.deleteCard(boardId, listId, card.id);
    el.remove();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function buildAddCard(cards, listId) {
  const wrap = document.createElement('div');
  wrap.className = 'bl-add-card';
  const btn = document.createElement('button');
  btn.className = 'bl-add-btn';
  btn.textContent = '+ Add a card';
  wrap.appendChild(btn);

  btn.addEventListener('click', () => {
    const restore = suspendAncestorsDrag(wrap);
    const input = document.createElement('textarea');
    input.className = 'bl-input bl-textarea';
    input.rows = 2;
    input.placeholder = 'Card text…';
    wrap.replaceChildren(input);
    input.focus();

    let done = false;
    const reset = () => { if (done) return; done = true; restore(); wrap.replaceChildren(btn); };
    const submit = async () => {
      const val = input.value.trim();
      if (!val) { reset(); return; }
      try {
        const created = await api.createCard(boardId, listId, val);
        cards.appendChild(buildCard(created));
        input.value = '';            // keep the composer open for rapid entry
        input.focus();
      } catch (e) {
        toast(e.message, 'error');
      }
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
      else if (e.key === 'Escape') { e.preventDefault(); reset(); }
    });
    input.addEventListener('blur', reset);
  });

  return wrap;
}

// ------------------------------------------------------------- drag & drop (native)
function onDragStart(e) {
  const card = e.target.closest?.('.bl-card');
  const list = e.target.closest?.('.bl-list');
  if (card) {
    dragKind = 'card';
    draggingEl = card;
    srcListId = Number(card.closest('.bl-list').dataset.id);
  } else if (list) {
    dragKind = 'list';
    draggingEl = list;
    srcListId = null;
  } else {
    return;
  }
  e.dataTransfer.effectAllowed = 'move';
  try { e.dataTransfer.setData('text/plain', String(draggingEl.dataset.id)); } catch { /* Firefox needs data set */ }
  // Defer the class so the browser's drag image is the un-dimmed element.
  requestAnimationFrame(() => draggingEl && draggingEl.classList.add('dragging'));
}

function onDragOver(e) {
  if (!draggingEl) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';

  if (dragKind === 'card') {
    const list = e.target.closest?.('.bl-list');
    if (!list) return;
    const cards = list.querySelector('.bl-cards');
    const after = dragAfter(cards, e.clientY, '.bl-card', 'y');
    if (after == null) cards.appendChild(draggingEl);
    else cards.insertBefore(draggingEl, after);
  } else if (dragKind === 'list') {
    const addList = canvas.querySelector('.bl-add-list');
    const after = dragAfter(canvas, e.clientX, '.bl-list', 'x');
    if (after == null) canvas.insertBefore(draggingEl, addList);
    else canvas.insertBefore(draggingEl, after);
  }
}

function onDragEnd() {
  if (!draggingEl) return;
  draggingEl.classList.remove('dragging');

  if (dragKind === 'card') {
    const destList = draggingEl.closest('.bl-list');
    const destListId = Number(destList.dataset.id);
    persistCardOrder(destListId, destList);
    if (srcListId != null && srcListId !== destListId) {
      const srcList = canvas.querySelector(`.bl-list[data-id="${srcListId}"]`);
      if (srcList) persistCardOrder(srcListId, srcList);
    }
  } else if (dragKind === 'list') {
    const ids = [...canvas.querySelectorAll('.bl-list')].map((el) => Number(el.dataset.id));
    api.reorderLists(boardId, ids).catch((e) => toast(e.message, 'error'));
  }

  draggingEl = null;
  dragKind = null;
  srcListId = null;
}

function persistCardOrder(listId, listEl) {
  const ids = [...listEl.querySelectorAll('.bl-card')].map((el) => Number(el.dataset.id));
  api.reorderCards(boardId, listId, ids).catch((e) => toast(e.message, 'error'));
}

// The sibling the dragged element should sit before, from the pointer position; null = append.
function dragAfter(container, pos, selector, axis) {
  const els = [...container.querySelectorAll(`${selector}:not(.dragging)`)];
  let closest = { offset: -Infinity, el: null };
  for (const el of els) {
    const box = el.getBoundingClientRect();
    const offset = axis === 'y' ? pos - box.top - box.height / 2 : pos - box.left - box.width / 2;
    if (offset < 0 && offset > closest.offset) closest = { offset, el };
  }
  return closest.el;
}

// -------------------------------------------------------------------------- shared
// Swap a display element for an input in place; commit on Enter/blur, cancel on Escape.
function openInlineEditor(displayEl, { value, multiline, onCommit }) {
  const restore = suspendAncestorsDrag(displayEl);
  const input = document.createElement(multiline ? 'textarea' : 'input');
  input.className = multiline ? 'bl-input bl-textarea' : 'bl-input';
  if (multiline) input.rows = 2;
  input.value = value;
  displayEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (commit) => {
    if (done) return;
    done = true;
    restore();
    const val = input.value.trim();
    input.replaceWith(displayEl);
    if (commit && val && val !== value) await onCommit(val);
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (!multiline || !e.shiftKey)) { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

// Native drag is initiated by the nearest draggable ancestor, which would hijack text
// selection inside an editor. Turn those ancestors' draggable off for the edit's lifetime.
function suspendAncestorsDrag(node) {
  const suspended = [];
  let el = node.parentElement;
  while (el && el !== canvas) {
    if (el.draggable) { el.draggable = false; suspended.push(el); }
    el = el.parentElement;
  }
  return () => suspended.forEach((e) => { e.draggable = true; });
}
