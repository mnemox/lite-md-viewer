// Document-relations modal: an interactive graph of the active document's connected
// network (reference parent/child + same-level edges) plus a companions list. Single-
// click a node to add a parent/child/same-level link; double-click to open it as the
// background document. Graph layout is Mermaid; pan/zoom is the shared createPanZoom.

import { api } from './api.js';
import { toast, confirmDialog } from './ui.js';
import { popupMenu } from './tree.js';
import { createPanZoom } from './panzoom.js';

let overlay = null;     // modal overlay element (singleton while open)
let pz = null;          // pan/zoom controller for the graph
let activeId = null;    // current background/active document
let onNavigate = null;  // callback(id) → host reloads the background document
let current = null;     // last graph response { activeId, nodes, edges, companions }
let seq = 0;            // unique id seed for mermaid.render

const themeFromDom = () =>
  document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'default';

// Open the relations modal for a document. `navigateCb(id)` reloads the host viewer.
export async function openRelations(fileId, navigateCb) {
  activeId = fileId;
  onNavigate = navigateCb;
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
        <span class="rel-hint">Single-click a node to link · double-click to open · click an arrow to remove it</span>
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
    else if (act === 'in') pz?.zoomIn();
    else if (act === 'out') pz?.zoomOut();
    else if (act === 'fit') pz?.fit();
    else if (act === 'add-companion') addRelationFlow('companion', activeId);
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);

  const viewport = overlay.querySelector('.rel-viewport');
  const stage = overlay.querySelector('.rel-stage');
  // maxFitScale: 1 → never blow a small graph up past 100% on load.
  pz = createPanZoom(viewport, stage, { skipSelector: '.node, .rel-edge-hit', maxFitScale: 1 });
}

function onKey(e) {
  if (!overlay) return;
  if (document.querySelector('.pick-list') || document.querySelector('.popup-menu')) return; // nested UI owns keys
  if (e.key === 'Escape') { e.stopPropagation(); closeModal(); }
  else if (e.key === '+' || e.key === '=') pz?.zoomIn();
  else if (e.key === '-' || e.key === '_') pz?.zoomOut();
  else if (e.key === '0') pz?.fit();
}

function closeModal() {
  if (!overlay) return;
  document.removeEventListener('keydown', onKey);
  overlay.remove();
  overlay = null; pz = null; current = null;
}

async function refresh() {
  let graph;
  try { graph = await api.graph(activeId); }
  catch (e) { toast(e.message, 'error'); return; }
  current = graph;
  await renderGraph(graph);
  renderCompanions(graph.companions);
}

// ---- graph ----
function sanitize(s) {
  return String(s || '').replace(/"/g, "'").replace(/[\r\n]+/g, ' ').trim();
}

function buildMermaid(graph) {
  const lines = ['flowchart TD'];
  for (const n of graph.nodes) lines.push(`  n${n.id}["${sanitize(n.title) || '#' + n.id}"]`);
  for (const e of graph.edges) {
    if (e.kind === 'reference') lines.push(`  n${e.fromId} --> n${e.toId}`);
    else lines.push(`  n${e.fromId} --- n${e.toId}`); // sibling (same-level)
  }
  return lines.join('\n');
}

async function renderGraph(graph) {
  const stage = overlay.querySelector('.rel-stage');
  try {
    window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: themeFromDom() });
    const { svg } = await window.mermaid.render('relgraph' + (++seq), buildMermaid(graph));
    stage.innerHTML = svg;
  } catch (e) {
    console.error('relations graph render failed', e);
    stage.innerHTML = '<div class="rel-error">Could not render the graph.</div>';
    return;
  }
  const svgEl = stage.querySelector('svg');
  if (svgEl) svgEl.style.maxWidth = 'none';
  wireNodes(graph);
  wireEdges(graph);
  requestAnimationFrame(() => pz.fit());
}

function nodeIdFromEl(el) {
  const m = (el.id || '').match(/n(\d+)/);
  return m ? Number(m[1]) : null;
}

function wireNodes(graph) {
  const stage = overlay.querySelector('.rel-stage');
  stage.querySelectorAll('g.node').forEach((el) => {
    const fid = nodeIdFromEl(el);
    if (fid == null) return;
    if (fid === graph.activeId) el.classList.add('rel-active');
    el.style.cursor = 'pointer';
    let clickTimer = null;
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      if (pz.wasDragged()) return;
      clearTimeout(clickTimer);
      clickTimer = setTimeout(() => { if (overlay) openNodeMenu(el, fid); }, 220); // wait out a dblclick
    });
    el.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      clearTimeout(clickTimer);
      if (pz.wasDragged()) return;
      navigateTo(fid);
    });
  });
}

// Match a rendered edge <path> to its graph edge — by the node ids in its id, with an
// order-based fallback (mermaid renders edges in declaration order).
function edgeForPath(p, graph, index) {
  const ids = (p.id || '').match(/n(\d+)/g);
  if (ids && ids.length >= 2) {
    const a = Number(ids[0].slice(1)), b = Number(ids[1].slice(1));
    const found = graph.edges.find((e) => (e.fromId === a && e.toId === b) || (e.fromId === b && e.toId === a));
    if (found) return found;
  }
  return graph.edges[index] || null;
}

function wireEdges(graph) {
  const stage = overlay.querySelector('.rel-stage');
  stage.querySelectorAll('g.edgePaths > path').forEach((p, i) => {
    const edge = edgeForPath(p, graph, i);
    if (!edge) return;
    // A fat transparent overlay makes the thin arrow easy to click.
    const hit = p.cloneNode(false);
    hit.removeAttribute('id');
    hit.removeAttribute('marker-end');
    hit.removeAttribute('marker-start');
    hit.removeAttribute('class');
    hit.setAttribute('fill', 'none');
    hit.setAttribute('stroke', 'transparent');
    hit.setAttribute('stroke-width', '14');
    hit.classList.add('rel-edge-hit');
    hit.style.cursor = 'pointer';
    hit.style.pointerEvents = 'stroke';
    hit.addEventListener('click', (e) => {
      e.stopPropagation();
      if (pz.wasDragged()) return;
      popupMenu(hit, [{ label: 'Remove connection', danger: true, onClick: () => removeEdge(edge) }]);
    });
    p.parentNode.appendChild(hit);
  });
}

async function removeEdge(edge) {
  try {
    if (edge.kind === 'reference') await api.removeRelation(edge.fromId, edge.toId, 'child');
    else await api.removeRelation(edge.fromId, edge.toId, 'sibling');
    await refresh();
    toast('Connection removed', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

function openNodeMenu(el, fid) {
  const items = [
    { label: 'Add parent…',     onClick: () => addRelationFlow('parent', fid) },
    { label: 'Add child…',      onClick: () => addRelationFlow('child', fid) },
    { label: 'Add same-level…', onClick: () => addRelationFlow('sibling', fid) },
  ];
  if (fid !== activeId) items.push({ label: 'Open this document', onClick: () => navigateTo(fid) });
  items.push({ label: 'Remove from graph', danger: true, onClick: () => removeNode(fid) });
  popupMenu(el, items);
}

// ---- navigation ----
async function navigateTo(fid) {
  if (fid === activeId) return;
  activeId = fid;
  if (onNavigate) { try { await onNavigate(fid); } catch { /* host reported it */ } }
  await refresh(); // same component → graph unchanged; updates active highlight + companions
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
