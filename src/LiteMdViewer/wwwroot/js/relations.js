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
import { openBrowse } from './browse.js';

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
let attachments = [];   // export bundles for the active document
let exporting = false;  // guard against concurrent exports
let colorMaps = [];     // imported color-map schemas for the active graph
let activeMap = null;   // currently-applied color map ({ ..., _byName: Map }) or null

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
        <button class="btn rel-export-btn" data-act="export" title="Export this graph as a downloadable bundle">Export</button>
        <a class="icon-btn rel-download-btn hidden" download title="Download the latest export" aria-label="Download the latest export"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg></a>
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
            <span class="rel-head-title"><strong>Companions</strong><button class="icon-btn rel-help-btn" data-act="help-companions" title="What is a companion document?" aria-label="What is a companion document?">?</button></span>
            <button class="btn" data-act="add-companion">+ Add</button>
          </div>
          <ul class="browse-list rel-comp-list"></ul>
          <div class="rel-colormaps">
            <div class="rel-cmap-head">
              <span class="rel-head-title"><strong>Colors map</strong><button class="icon-btn rel-help-btn" data-act="help-colormaps" title="What is a colors map?" aria-label="What is a colors map?">?</button></span>
              <button class="btn" data-act="add-colormap">+ Add</button>
            </div>
            <ul class="browse-list rel-cmap-list"></ul>
          </div>
          <div class="rel-attachments hidden">
            <div class="rel-attach-head"><strong>Attachments</strong></div>
            <ul class="browse-list rel-attach-list"></ul>
          </div>
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
    else if (act === 'add-colormap') addColorMapFlow();
    else if (act === 'help-companions') helpDialog('About companions', COMPANIONS_HELP);
    else if (act === 'help-colormaps') helpDialog('About colors maps', COLORMAPS_HELP);
    else if (act === 'export') startExport();
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
  const browse = document.getElementById('browseModal');
  if (document.querySelector('.pick-list') || document.querySelector('.popup-menu')
    || document.querySelector('.rel-help-modal')
    || (browse && !browse.classList.contains('hidden'))) return; // nested UI owns keys
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
  colorMaps = []; activeMap = null;
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
  try { colorMaps = await api.colorMaps(activeId); } catch { colorMaps = []; }
  reconcileActiveMap();
  renderActive();
  renderCompanions(graph.companions);
  renderColorMaps(colorMaps);
  try { attachments = await api.attachments(activeId); } catch { attachments = []; }
  renderAttachments(attachments);
}

function renderActive() {
  if (!current || !overlay) return;
  if (mode === '3d') {
    if (!g3d) g3d = createGraph3d(overlay.querySelector('.rel-3d'), callbacks);
    g3d.render(current, layout, colorForNode);
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
    const mapColor = colorForNode(n);
    if (mapColor) {
      const box = g.querySelector('.rel-node-box');
      box.style.stroke = mapColor;
      box.style.strokeWidth = '3';
    }
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

// ---- export / attachments ----
// Deterministic copied-file name — MUST match the server's C# Slug/ExportFileName.
const slug = (t) => (String(t || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'doc');
const exportFileName = (n) => `${n.id}-${slug(n.title)}.md`;
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function formatSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1024 / 1024).toFixed(1) + ' MB';
}

async function startExport() {
  if (exporting || !current) return;
  exporting = true;
  const btn = overlay.querySelector('.rel-export-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Exporting…'; }
  try {
    await api.export(activeId, buildExportHtml(current, layout));
    await refresh();
    toast('Exported', 'ok');
  } catch (e) { toast(e.message, 'error'); }
  finally { exporting = false; if (btn) { btn.disabled = false; btn.textContent = 'Export'; } }
}

// A standalone index.html: a references list + the same 2D graph (literal colors,
// nodes link to the copied .md files sitting beside it).
function buildExportHtml(graph, layout) {
  const { pos2d, bounds } = layout;
  const minX = bounds.minX - NODE_W / 2 - PAD, maxX = bounds.maxX + NODE_W / 2 + PAD;
  const minY = bounds.minY - NODE_H / 2 - PAD, maxY = bounds.maxY + NODE_H / 2 + PAD;
  const W = Math.max(maxX - minX, 1), H = Math.max(maxY - minY, 1);
  const C = { node: '#ffffff', stroke: '#d0d3d9', active: '#2f6fed', text: '#1f2328', missing: '#d64541', edge: '#6b7280' };

  let edgeSvg = '';
  for (const e of graph.edges) {
    const a = pos2d.get(e.fromId), b = pos2d.get(e.toId);
    if (!a || !b) continue;
    if (e.kind === 'reference') {
      const dir = Math.sign(b.y - a.y) || 1;
      edgeSvg += `<path d="M ${a.x} ${a.y + dir * NODE_H / 2} L ${b.x} ${b.y - dir * NODE_H / 2}" fill="none" stroke="${C.edge}" stroke-width="1.6" marker-end="url(#arr)"/>`;
    } else {
      const cx = (a.x + b.x) / 2, cy = a.y + NODE_H / 2 + 38;
      edgeSvg += `<path d="M ${a.x} ${a.y + NODE_H / 2} Q ${cx} ${cy} ${b.x} ${b.y + NODE_H / 2}" fill="none" stroke="${C.edge}" stroke-width="1.6" stroke-dasharray="5 4"/>`;
    }
  }
  let nodeSvg = '';
  for (const n of graph.nodes) {
    const p = pos2d.get(n.id); if (!p) continue;
    const x = p.x - NODE_W / 2, y = p.y - NODE_H / 2;
    const stroke = n.id === graph.activeId ? C.active : C.stroke;
    const sw = n.id === graph.activeId ? 2.5 : 1.5;
    const fill = n.missing ? C.missing : C.text;
    const inner = `<g transform="translate(${x} ${y})"><rect width="${NODE_W}" height="${NODE_H}" rx="8" ry="8" fill="${C.node}" stroke="${stroke}" stroke-width="${sw}"/>`
      + `<text x="${NODE_W / 2}" y="${NODE_H / 2}" text-anchor="middle" dominant-baseline="central" font-family="system-ui, sans-serif" font-size="13" fill="${fill}">${esc(truncate(n.title) || ('#' + n.id))}</text></g>`;
    nodeSvg += n.missing ? inner : `<a href="${exportFileName(n)}" target="_top">${inner}</a>`;
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${minX} ${minY} ${W} ${H}" width="${W}" height="${H}" style="max-width:100%;height:auto">`
    + `<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="${C.edge}"/></marker></defs>`
    + `<g>${edgeSvg}</g><g>${nodeSvg}</g></svg>`;

  const active = graph.nodes.find((n) => n.id === graph.activeId);
  const title = esc(active ? active.title : 'Graph');
  const refs = graph.nodes.map((n) => n.missing
    ? `<li><span class="missing">${esc(n.title)} (missing)</span></li>`
    : `<li><a href="${exportFileName(n)}">${esc(n.title || ('#' + n.id))}</a></li>`).join('');

  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>${title} — graph export</title>
<style>
  html{background:#ffffff}
  body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1f2328;background:#ffffff;max-width:1000px;margin:24px auto;padding:0 16px}
  a{color:#2f6fed}
  h1{font-size:22px} h2{font-size:16px;margin-top:28px;border-bottom:1px solid #e3e5e8;padding-bottom:4px}
  ul.refs{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:8px}
  ul.refs li a{display:inline-block;padding:4px 10px;border:1px solid #d0d3d9;border-radius:8px;text-decoration:none;color:#1f2328;background:#f7f7f8}
  ul.refs li a:hover{border-color:#2f6fed}
  .missing{color:#d64541}
  .graph{margin-top:8px;border:1px solid #e3e5e8;border-radius:10px;padding:8px;overflow:auto;background:#fbfbfc}
  .graph a{cursor:pointer}
</style></head>
<body>
  <h1>${title} — relations graph</h1>
  <h2>Documents</h2>
  <ul class="refs">${refs}</ul>
  <h2>Graph</h2>
  <div class="graph">${svg}</div>
</body></html>`;
}

function renderAttachments(list) {
  const section = overlay.querySelector('.rel-attachments');
  const ul = overlay.querySelector('.rel-attach-list');
  ul.innerHTML = '';
  section.classList.toggle('hidden', !list.length);

  // Head "Download" button → the most recent export (list is newest-first).
  const dl = overlay.querySelector('.rel-download-btn');
  if (list.length) {
    dl.href = api.attachmentUrl(list[0].id);
    dl.setAttribute('download', list[0].fileName);
    dl.classList.remove('hidden');
  } else {
    dl.removeAttribute('href');
    dl.classList.add('hidden');
  }

  for (const a of list) {
    const li = document.createElement('li');
    const icon = document.createElement('span'); icon.textContent = '📦';
    const link = document.createElement('a');
    link.className = 'name'; link.href = api.attachmentUrl(a.id); link.textContent = a.fileName;
    link.setAttribute('download', a.fileName);
    link.title = `${a.nodeCount} document(s) · ${formatSize(a.sizeBytes)}`;
    const rm = document.createElement('button');
    rm.className = 'icon-btn rel-rm'; rm.textContent = '✕'; rm.title = 'Delete export';
    rm.onclick = (e) => { e.stopPropagation(); deleteAttachment(a.id, a.fileName); };
    li.append(icon, link, rm);
    ul.appendChild(li);
  }
}

async function deleteAttachment(attId, name) {
  if (!(await confirmDialog(`Delete export “${name}”? This removes the downloadable file.`, { okLabel: 'Delete', danger: true }))) return;
  try { await api.deleteAttachment(attId); await refresh(); toast('Deleted', 'ok'); }
  catch (e) { toast(e.message, 'error'); }
}

// ---- section help (the "?" icons in the Companions / Colors map headers) ----
const COMPANIONS_HELP = `
  <p>A <strong>companion</strong> is a document that relates to this graph without being part of it.
  It is not drawn as a node and has no connections — it is kept alongside the graph as
  related material (background notes, meeting minutes, a checklist, …).</p>
  <ul>
    <li><strong>+ Add</strong> links an existing managed document as a companion.</li>
    <li>Click a companion's name to open it as the active document.</li>
    <li>✕ removes the companion link only — the document itself is kept.</li>
  </ul>`;

const COLORMAPS_HELP = `
  <p>A <strong>colors map</strong> is a JSON file that recolors the borders of graph nodes,
  e.g. to show status or ownership. Click a map in the list to apply it (click again to turn
  it off). Documents are matched to entries by file name, case-insensitively.</p>
  <p>The referenced <code>.json</code> file must look like this:</p>
  <pre><code>{
  "listName": "Review status",
  "legend": [
    { "color": "#2f9e44", "meaning": "Approved" },
    { "color": "#e8590c", "meaning": "Needs review" }
  ],
  "files": [
    { "filePath": "readme.md",    "color": "#2f9e44" },
    { "filePath": "docs/spec.md", "color": "#e8590c" }
  ]
}</code></pre>
  <ul>
    <li><code>files</code> is required — at least one entry with both <code>filePath</code> and <code>color</code>.</li>
    <li><code>color</code> accepts any CSS color (hex, named, rgb…).</li>
    <li><code>listName</code> is optional — the name shown in the list (defaults to the file name).</li>
    <li><code>legend</code> is optional — shown as a tooltip explaining what each color means.</li>
  </ul>`;

function helpDialog(title, bodyHtml) {
  const ov = document.createElement('div');
  ov.className = 'modal rel-help-modal';
  ov.innerHTML = `
    <div class="modal-card" style="width:min(560px,96vw)">
      <div class="modal-head"><strong></strong>
        <button class="icon-btn" data-act="close" aria-label="Close">✕</button></div>
      <div class="rel-help-body"></div>
    </div>`;
  ov.querySelector('.modal-head strong').textContent = title;
  ov.querySelector('.rel-help-body').innerHTML = bodyHtml;
  const close = () => { ov.remove(); document.removeEventListener('keydown', onk); };
  const onk = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  ov.addEventListener('click', (e) => {
    if (e.target === ov || e.target.closest('[data-act="close"]')) close();
  });
  document.addEventListener('keydown', onk);
  document.body.appendChild(ov);
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

// ---- color maps ----
// The border color for a node under the active map: match by file name (basename),
// case-insensitively, so imported paths need not match the local disk exactly.
const baseName = (s) => String(s || '').split(/[\\/]/).pop().toLowerCase();
function colorForNode(n) {
  if (!activeMap) return null;
  return activeMap._byName.get(baseName(n.path || n.title)) || null;
}

// Rebuild the applied map after a refresh: keep it applied if it still exists.
function reconcileActiveMap() {
  if (!activeMap) return;
  const still = colorMaps.find((m) => m.id === activeMap.id);
  activeMap = still ? buildActiveMap(still) : null;
}

function buildActiveMap(m) {
  const byName = new Map();
  for (const f of (m.files || [])) byName.set(baseName(f.filePath), f.color);
  return { ...m, _byName: byName };
}

function selectColorMap(m) {
  activeMap = (activeMap && activeMap.id === m.id) ? null : buildActiveMap(m); // toggle
  renderColorMaps(colorMaps);
  renderActive();
}

function renderColorMaps(maps) {
  const list = overlay.querySelector('.rel-cmap-list');
  list.innerHTML = '';
  if (!maps.length) {
    const li = document.createElement('li');
    li.className = 'disabled';
    li.textContent = 'No colors maps yet.';
    list.appendChild(li);
    return;
  }
  for (const m of maps) {
    const li = document.createElement('li');
    if (activeMap && activeMap.id === m.id) li.className = 'rel-cmap-active';
    const icon = document.createElement('span'); icon.textContent = '🎨';
    const name = document.createElement('span');
    name.className = 'name'; name.textContent = m.listName; name.dir = 'auto';
    name.title = (m.legend || []).map((l) => `${l.color} — ${l.meaning}`).join('\n') || m.filePath;
    name.onclick = () => selectColorMap(m);
    const rm = document.createElement('button');
    rm.className = 'icon-btn rel-rm'; rm.textContent = '✕'; rm.title = 'Remove colors map';
    rm.onclick = (e) => { e.stopPropagation(); removeColorMap(m.id); };
    li.append(icon, name, rm);
    list.appendChild(li);
  }
}

async function addColorMapFlow() {
  openBrowse(async (path) => {
    try {
      await api.addColorMap(activeId, path);
      await refresh();
      toast('Colors map referenced', 'ok');
    } catch (e) { toast(e.message, 'error'); }
  }, {
    kind: 'json',
    title: 'Reference a colors JSON file',
    addLabel: 'Reference',
    pathPlaceholder: '…or paste a full path to a .json file',
  });
}

async function removeColorMap(mapId) {
  try {
    await api.removeColorMap(activeId, mapId);
    if (activeMap && activeMap.id === mapId) activeMap = null;
    await refresh();
    toast('Colors map removed', 'ok');
  } catch (e) { toast(e.message, 'error'); }
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
    'Remove this document from the graph? Its connections are deleted (the document itself is kept).',
    { okLabel: 'Remove', danger: true });
  if (!ok) return;
  try {
    await api.removeFromGraph(fid);
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
