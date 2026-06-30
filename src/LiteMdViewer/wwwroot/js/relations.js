// Document-relations modal: an interactive graph of the active document's connected
// network (reference parent/child + same-level edges) plus a companions list. Renders
// a custom layered 2D SVG (same-level nodes share a row) with a 2D/3D toggle; the 3D
// view (graph3d.js) shares the same interactions. Single-click a node to link, double-
// click to open it as the background document, click an edge to remove it.

import { api } from './api.js';
import { toast, confirmDialog } from './ui.js';
import { popupMenu } from './tree.js';
import { createPanZoom } from './panzoom.js';
import { computeLayout } from './graphlayout.js';
import { createGraph3d } from './graph3d.js';

const SVGNS = 'http://www.w3.org/2000/svg';
const NODE_W = 144, NODE_H = 38, PAD = 64;

let overlay = null;     // modal overlay element (singleton while open)
let pz = null;          // 2D pan/zoom controller
let g3d = null;         // 3D controller (lazy)
let mode = '2d';        // '2d' | '3d'
let activeId = null;    // current background/active document
let onNavigate = null;  // callback(id) → host reloads the background document
let current = null;     // last graph response
let layout = null;      // last computeLayout result

// Open the relations modal for a document. `navigateCb(id)` reloads the host viewer.
export async function openRelations(fileId, navigateCb) {
  activeId = fileId;
  onNavigate = navigateCb;
  mode = '2d';
  buildModal();
  await refresh();
}

function buildModal() {
  if (overlay) return;
  overlay = document.createElement('div');
  overlay.className = 'modal';
  overlay.innerHTML = `
    <div class="modal-card wide relations-card">
      <div class="modal-head">
        <strong>Relations</strong>
        <span class="rel-hint">Single-click a node to link · double-click to open · click an edge to remove it</span>
        <div class="seg rel-modes">
          <button class="seg-btn active" data-act="mode-2d">2D</button>
          <button class="seg-btn" data-act="mode-3d">3D</button>
        </div>
        <button class="icon-btn" data-act="close" aria-label="Close">✕</button>
      </div>
      <div class="relations">
        <div class="rel-graph">
          <div class="rel-toolbar">
            <button type="button" class="icon-btn" data-act="out" title="Zoom out">−</button>
            <button type="button" class="icon-btn" data-act="fit" title="Fit">⤢</button>
            <button type="button" class="icon-btn" data-act="in"  title="Zoom in">+</button>
          </div>
          <div class="rel-viewport"><div class="rel-stage"></div></div>
          <div class="rel-3d hidden"></div>
        </div>
        <aside class="rel-companions">
          <div class="rel-comp-head">
            <strong>Companions</strong>
            <button class="btn" data-act="add-companion">+ Add</button>
          </div>
          <ul class="browse-list rel-comp-list"></ul>
        </aside>
      </div>
    </div>`;

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) return closeModal();
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'close') closeModal();
    else if (act === 'in') zoomIn();
    else if (act === 'out') zoomOut();
    else if (act === 'fit') fitView();
    else if (act === 'add-companion') addRelationFlow('companion', activeId);
    else if (act === 'mode-2d') setMode('2d');
    else if (act === 'mode-3d') setMode('3d');
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);

  const viewport = overlay.querySelector('.rel-viewport');
  const stage = overlay.querySelector('.rel-stage');
  pz = createPanZoom(viewport, stage, { skipSelector: '.rel-node, .rel-edge-hit', maxFitScale: 1 });
}

function onKey(e) {
  if (!overlay) return;
  if (document.querySelector('.pick-list') || document.querySelector('.popup-menu')) return; // nested UI owns keys
  if (e.key === 'Escape') { e.stopPropagation(); closeModal(); }
  else if (e.key === '+' || e.key === '=') zoomIn();
  else if (e.key === '-' || e.key === '_') zoomOut();
  else if (e.key === '0') fitView();
}

function closeModal() {
  if (!overlay) return;
  document.removeEventListener('keydown', onKey);
  g3d?.dispose(); g3d = null;
  overlay.remove();
  overlay = null; pz = null; current = null; layout = null;
}

// toolbar/keys dispatch to whichever renderer is active
function zoomIn()  { mode === '3d' ? g3d?.zoomIn()  : pz?.zoomIn(); }
function zoomOut() { mode === '3d' ? g3d?.zoomOut() : pz?.zoomOut(); }
function fitView() { mode === '3d' ? g3d?.fit()     : pz?.fit(); }

function setMode(m) {
  if (m === mode || !overlay) return;
  mode = m;
  overlay.querySelector('[data-act="mode-2d"]').classList.toggle('active', m === '2d');
  overlay.querySelector('[data-act="mode-3d"]').classList.toggle('active', m === '3d');
  overlay.querySelector('.rel-viewport').classList.toggle('hidden', m !== '2d');
  overlay.querySelector('.rel-3d').classList.toggle('hidden', m !== '3d');
  renderActive();
}

async function refresh() {
  let graph;
  try { graph = await api.graph(activeId); }
  catch (e) { toast(e.message, 'error'); return; }
  current = graph;
  layout = computeLayout(graph);
  renderActive();
  renderCompanions(graph.companions);
}

function renderActive() {
  if (!current || !overlay) return;
  if (mode === '3d') {
    if (!g3d) g3d = createGraph3d(overlay.querySelector('.rel-3d'), callbacks);
    g3d.render(current, layout);
    requestAnimationFrame(() => g3d && g3d.resize()); // container just became visible
  } else {
    render2d(current, layout);
  }
}

// ---- 2D SVG renderer ----
const truncate = (s, n = 18) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

function svgEl(name, attrs = {}) {
  const el = document.createElementNS(SVGNS, name);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function render2d(graph, layout) {
  const stage = overlay.querySelector('.rel-stage');
  stage.innerHTML = '';
  const { pos2d, bounds } = layout;
  const minX = bounds.minX - NODE_W / 2 - PAD, maxX = bounds.maxX + NODE_W / 2 + PAD;
  const minY = bounds.minY - NODE_H / 2 - PAD, maxY = bounds.maxY + NODE_H / 2 + PAD;
  const W = Math.max(maxX - minX, 1), H = Math.max(maxY - minY, 1);

  const svg = svgEl('svg', { class: 'rel-svg', viewBox: `${minX} ${minY} ${W} ${H}`, width: W, height: H });
  svg.style.maxWidth = 'none';
  svg.innerHTML = '<defs><marker id="rel-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" style="fill: var(--text-dim)"/></marker></defs>';
  const edgesG = svgEl('g', { class: 'rel-edges' });
  const nodesG = svgEl('g', { class: 'rel-nodes' });
  svg.append(edgesG, nodesG);

  for (const e of graph.edges) {
    const a = pos2d.get(e.fromId), b = pos2d.get(e.toId);
    if (!a || !b) continue;
    let d, cls;
    if (e.kind === 'reference') {
      const dir = Math.sign(b.y - a.y) || 1;            // parent → child
      d = `M ${a.x} ${a.y + dir * NODE_H / 2} L ${b.x} ${b.y - dir * NODE_H / 2}`;
      cls = 'rel-edge rel-edge-ref';
    } else {
      const cx = (a.x + b.x) / 2, cy = a.y + NODE_H / 2 + 38; // same row → dip below
      d = `M ${a.x} ${a.y + NODE_H / 2} Q ${cx} ${cy} ${b.x} ${b.y + NODE_H / 2}`;
      cls = 'rel-edge rel-edge-sib';
    }
    const path = svgEl('path', { class: cls, d, fill: 'none' });
    if (e.kind === 'reference') path.setAttribute('marker-end', 'url(#rel-arrow)');
    edgesG.appendChild(path);

    const hit = svgEl('path', { class: 'rel-edge-hit', d, fill: 'none', stroke: 'transparent', 'stroke-width': '16' });
    hit.style.cursor = 'pointer'; hit.style.pointerEvents = 'stroke';
    hit.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (pz.wasDragged()) return;
      callbacks.edgeMenu(e, ev.clientX, ev.clientY);
    });
    edgesG.appendChild(hit);
  }

  for (const n of graph.nodes) {
    const p = pos2d.get(n.id); if (!p) continue;
    const g = svgEl('g', {
      class: 'rel-node' + (n.id === graph.activeId ? ' rel-active' : '') + (n.missing ? ' rel-missing' : ''),
      'data-id': n.id,
      transform: `translate(${p.x - NODE_W / 2} ${p.y - NODE_H / 2})`,
    });
    g.style.cursor = 'pointer';
    g.append(
      svgEl('rect', { class: 'rel-node-box', width: NODE_W, height: NODE_H, rx: 8, ry: 8 }),
      Object.assign(svgEl('text', {
        class: 'rel-node-label', x: NODE_W / 2, y: NODE_H / 2,
        'text-anchor': 'middle', 'dominant-baseline': 'central',
      }), { textContent: truncate(n.title) || ('#' + n.id) }),
      Object.assign(svgEl('title'), { textContent: n.title || ('#' + n.id) }),
    );
    let clickTimer = null;
    g.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (pz.wasDragged()) return;
      clearTimeout(clickTimer);
      const x = ev.clientX, y = ev.clientY;
      clickTimer = setTimeout(() => { if (overlay) callbacks.nodeMenu(n.id, x, y); }, 220);
    });
    g.addEventListener('dblclick', (ev) => {
      ev.stopPropagation(); clearTimeout(clickTimer);
      if (pz.wasDragged()) return;
      callbacks.navigate(n.id);
    });
    nodesG.appendChild(g);
  }

  stage.appendChild(svg);
  requestAnimationFrame(() => pz.fit());
}

// ---- shared interactions (used by both 2D and 3D) ----
// popupMenu wants an element with getBoundingClientRect; fake one at the click point.
const anchorAt = (x, y) => ({ getBoundingClientRect: () => ({ left: x, right: x, top: y, bottom: y, width: 0, height: 0 }) });

const callbacks = {
  navigate: (id) => navigateTo(id),
  nodeMenu: (fid, x, y) => {
    const items = [
      { label: 'Add parent…',     onClick: () => addRelationFlow('parent', fid) },
      { label: 'Add child…',      onClick: () => addRelationFlow('child', fid) },
      { label: 'Add same-level…', onClick: () => addRelationFlow('sibling', fid) },
    ];
    if (fid !== activeId) items.push({ label: 'Open this document', onClick: () => navigateTo(fid) });
    items.push({ label: 'Remove from graph', danger: true, onClick: () => removeNode(fid) });
    popupMenu(anchorAt(x, y), items);
  },
  edgeMenu: (edge, x, y) => {
    popupMenu(anchorAt(x, y), [{ label: 'Remove connection', danger: true, onClick: () => removeEdge(edge) }]);
  },
};

async function removeEdge(edge) {
  try {
    if (edge.kind === 'reference') await api.removeRelation(edge.fromId, edge.toId, 'child');
    else await api.removeRelation(edge.fromId, edge.toId, 'sibling');
    await refresh();
    toast('Connection removed', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

async function navigateTo(fid) {
  if (fid === activeId) return;
  activeId = fid;
  if (onNavigate) { try { await onNavigate(fid); } catch { /* host reported it */ } }
  await refresh();
}

// ---- companions ----
function renderCompanions(companions) {
  const list = overlay.querySelector('.rel-comp-list');
  list.innerHTML = '';
  if (!companions.length) {
    const li = document.createElement('li');
    li.className = 'disabled';
    li.textContent = 'No companions yet.';
    list.appendChild(li);
    return;
  }
  for (const c of companions) {
    const li = document.createElement('li');
    const icon = document.createElement('span'); icon.textContent = '📄';
    const name = document.createElement('span'); name.className = 'name'; name.textContent = c.title; name.dir = 'auto';
    if (c.missing) name.style.color = 'var(--danger)';
    name.onclick = () => navigateTo(c.id);
    const rm = document.createElement('button');
    rm.className = 'icon-btn rel-rm'; rm.textContent = '✕'; rm.title = 'Remove companion';
    rm.onclick = (e) => { e.stopPropagation(); removeCompanion(c.id); };
    li.append(icon, name, rm);
    list.appendChild(li);
  }
}

async function removeCompanion(otherId) {
  try { await api.removeRelation(activeId, otherId, 'companion'); await refresh(); }
  catch (e) { toast(e.message, 'error'); }
}

// ---- add / remove links ----
async function addRelationFlow(kind, baseId) {
  const picked = await pickDocument(baseId);
  if (picked == null) return;
  try {
    await api.addRelation(baseId, picked, kind);
    await refresh();
    toast('Linked', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

async function removeNode(fid) {
  const ok = await confirmDialog(
    'Remove this document from the graph? Its parent/child/same-level links are deleted (the document itself is kept).',
    { okLabel: 'Remove', danger: true });
  if (!ok) return;
  const edges = current.edges.filter((e) => e.fromId === fid || e.toId === fid);
  try {
    for (const e of edges) {
      if (e.kind === 'reference') {
        if (e.fromId === fid) await api.removeRelation(fid, e.toId, 'child');
        else await api.removeRelation(fid, e.fromId, 'parent');
      } else {
        const other = e.fromId === fid ? e.toId : e.fromId;
        await api.removeRelation(fid, other, 'sibling');
      }
    }
    await refresh();
    toast('Removed from graph', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

// Filterable picker over managed documents (excludes the base document). Resolves to
// a file id, or null if cancelled.
function pickDocument(excludeId) {
  return new Promise((resolve) => {
    const ov = document.createElement('div');
    ov.className = 'modal';
    ov.innerHTML = `
      <div class="modal-card" style="width:min(460px,96vw)">
        <div class="modal-head"><strong>Pick a document</strong>
          <button class="icon-btn" data-act="cancel" aria-label="Close">✕</button></div>
        <input class="input pick-filter" placeholder="Filter documents…" style="margin:10px 16px 0;width:auto" />
        <ul class="browse-list pick-list"></ul>
      </div>`;
    const list = ov.querySelector('.pick-list');
    const filter = ov.querySelector('.pick-filter');
    let files = [];

    const render = () => {
      const q = filter.value.toLowerCase();
      list.innerHTML = '';
      const shown = files.filter((f) => !q || f.title.toLowerCase().includes(q));
      if (!shown.length) {
        const li = document.createElement('li'); li.className = 'disabled';
        li.textContent = files.length ? 'No matches.' : 'No other documents.';
        list.appendChild(li); return;
      }
      for (const f of shown) {
        const li = document.createElement('li');
        const icon = document.createElement('span'); icon.textContent = '📄';
        const name = document.createElement('span'); name.className = 'name'; name.textContent = f.title; name.dir = 'auto';
        if (f.missing) name.style.color = 'var(--danger)';
        li.append(icon, name);
        li.onclick = () => done(f.id);
        list.appendChild(li);
      }
    };
    const done = (val) => { ov.remove(); document.removeEventListener('keydown', onk); resolve(val); };
    const onk = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
    ov.addEventListener('click', (e) => {
      if (e.target === ov) return done(null);
      if (e.target.closest('[data-act="cancel"]')) done(null);
    });
    filter.addEventListener('input', render);
    document.addEventListener('keydown', onk);
    document.body.appendChild(ov);

    api.tree()
      .then((t) => { files = t.files.filter((f) => f.id !== excludeId); render(); filter.focus(); })
      .catch((e) => { toast(e.message, 'error'); done(null); });
  });
}
