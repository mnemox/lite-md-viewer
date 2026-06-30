// Fullscreen viewer for rendered Mermaid diagrams: zoom (wheel + buttons) and
// drag-to-pan. enhance() adds an expand button to each diagram; the overlay is a
// single shared singleton that clones the diagram in on open (the live document
// DOM is rebuilt frequently by renderMarkdown, so we never move the original svg).

import { toast } from './ui.js';

const MIN = 0.1;
const MAX = 12;

const ICON_COPY = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const ICON_EXPAND = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg>';

let overlay = null;       // .gv-overlay element (built lazily)
let viewport = null;      // .gv-viewport (handles wheel/pointer)
let stage = null;         // .gv-stage (gets the transform)
const state = { scale: 1, tx: 0, ty: 0 };

const clamp = (s) => Math.max(MIN, Math.min(MAX, s));

// Add idempotent hover tools (copy + expand) to every rendered diagram in `container`.
export function enhance(container) {
  container.querySelectorAll('pre.mermaid').forEach((pre) => {
    if (!pre.querySelector('svg')) return;          // mermaid not done / failed
    if (pre.querySelector('.gv-tools')) return;     // already enhanced
    pre.classList.add('gv-host');                   // position: relative target

    const tools = document.createElement('div');
    tools.className = 'gv-tools';

    const mkBtn = (icon, label, handler) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'gv-tool';
      b.title = label;
      b.setAttribute('aria-label', label);
      b.innerHTML = icon;
      b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); handler(); });
      return b;
    };

    tools.append(
      mkBtn(ICON_COPY, 'Copy to clipboard', () => copyDiagram(pre.querySelector('svg'))),
      mkBtn(ICON_EXPAND, 'Expand', () => openOverlay(pre.querySelector('svg'))),
    );
    pre.appendChild(tools);
  });
}

// Copy the diagram to the clipboard as a PNG image (falls back to SVG text).
async function copyDiagram(svg) {
  if (!svg) return;
  try {
    // Pass the blob promise to ClipboardItem so the user-gesture stays valid while
    // the SVG rasterizes (Chrome keeps the write authorized this way).
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': svgToPngBlob(svg) })]);
    toast('Diagram copied to clipboard', 'ok');
  } catch (e) {
    try {
      await navigator.clipboard.writeText(new XMLSerializer().serializeToString(svg));
      toast('Diagram SVG copied to clipboard', 'ok');
    } catch {
      toast('Copy failed', 'error');
    }
  }
}

// Rasterize an SVG node to a PNG blob at 2× for a crisp paste.
function svgToPngBlob(svg) {
  return new Promise((resolve, reject) => {
    const clone = svg.cloneNode(true);
    const vb = svg.viewBox && svg.viewBox.baseVal;
    const w = Math.ceil((vb && vb.width) || svg.getBoundingClientRect().width || 800);
    const h = Math.ceil((vb && vb.height) || svg.getBoundingClientRect().height || 600);
    clone.setAttribute('width', w);
    clone.setAttribute('height', h);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const xml = new XMLSerializer().serializeToString(clone);
    const src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml);
    const img = new Image();
    img.onload = () => {
      const scale = 2;
      const canvas = document.createElement('canvas');
      canvas.width = w * scale;
      canvas.height = h * scale;
      const ctx = canvas.getContext('2d');
      // paint the page background so the image isn't transparent when pasted
      ctx.fillStyle = getComputedStyle(document.body).backgroundColor || '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, w, h);
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png');
    };
    img.onerror = () => reject(new Error('SVG image load failed'));
    img.src = src;
  });
}

function buildOverlay() {
  overlay = document.createElement('div');
  overlay.className = 'gv-overlay';
  overlay.innerHTML = `
    <div class="gv-toolbar">
      <button type="button" class="icon-btn" data-act="out"   title="Zoom out" aria-label="Zoom out">−</button>
      <button type="button" class="icon-btn" data-act="reset" title="Fit"       aria-label="Fit">⤢</button>
      <button type="button" class="icon-btn" data-act="in"    title="Zoom in"   aria-label="Zoom in">+</button>
      <button type="button" class="icon-btn" data-act="close" title="Close"     aria-label="Close">✕</button>
    </div>
    <div class="gv-viewport"><div class="gv-stage"></div></div>`;
  viewport = overlay.querySelector('.gv-viewport');
  stage = overlay.querySelector('.gv-stage');

  overlay.querySelector('.gv-toolbar').addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'close') closeOverlay();
    else if (act === 'in') zoomAroundCenter(1.2);
    else if (act === 'out') zoomAroundCenter(1 / 1.2);
    else if (act === 'reset') fitToScreen();
  });
  // Backdrop click closes (only when the overlay itself is the target, never the
  // viewport/diagram/toolbar — so a pan-release isn't mistaken for a backdrop click).
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeOverlay(); });

  viewport.addEventListener('wheel', onWheel, { passive: false });
  viewport.addEventListener('pointerdown', onPointerDown);
  document.body.appendChild(overlay);
}

function openOverlay(srcSvg) {
  if (!srcSvg) return;
  if (!overlay) buildOverlay();
  stage.replaceChildren(srcSvg.cloneNode(true));    // fresh clone each open
  const clone = stage.firstElementChild;
  if (clone) clone.style.maxWidth = 'none';         // override .mermaid svg max-width:100%
  overlay.classList.add('open');
  document.documentElement.style.overflow = 'hidden';   // lock page scroll
  document.addEventListener('keydown', onKey, true);    // CAPTURE → beats drawer's Esc
  requestAnimationFrame(fitToScreen);               // wait a frame so layout/bbox is known
}

function closeOverlay() {
  if (!overlay) return;
  overlay.classList.remove('open');
  stage.replaceChildren();                          // drop the clone (free memory)
  document.documentElement.style.overflow = '';
  document.removeEventListener('keydown', onKey, true);
}

function applyTransform() {
  stage.style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
}

function fitToScreen() {
  const clone = stage.firstElementChild;
  if (!clone) return;
  const vp = viewport.getBoundingClientRect();
  let w, h;
  const vb = clone.viewBox && clone.viewBox.baseVal;
  if (vb && vb.width && vb.height) {
    w = vb.width; h = vb.height;
  } else {
    const b = clone.getBBox();
    w = b.width; h = b.height;
  }
  if (!w || !h) return;
  clone.style.width = w + 'px';                     // pin to a pixel box for clean scale math
  clone.style.height = h + 'px';
  const margin = 0.92;
  state.scale = clamp(Math.min(vp.width / w, vp.height / h) * margin);
  state.tx = (vp.width - w * state.scale) / 2;      // center
  state.ty = (vp.height - h * state.scale) / 2;
  applyTransform();
}

// Zoom keeping the content point under (cx,cy) [viewport coords] fixed.
function zoomAt(cx, cy, next) {
  next = clamp(next);
  if (next === state.scale) return;
  const px = (cx - state.tx) / state.scale;         // content point under (cx,cy)
  const py = (cy - state.ty) / state.scale;
  state.scale = next;
  state.tx = cx - px * state.scale;                 // re-anchor that point
  state.ty = cy - py * state.scale;
  applyTransform();
}

function onWheel(e) {
  e.preventDefault();
  const rect = viewport.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  zoomAt(cx, cy, state.scale * Math.exp(-e.deltaY * 0.0015)); // wheel up → zoom in
}

function zoomAroundCenter(factor) {
  const rect = viewport.getBoundingClientRect();
  zoomAt(rect.width / 2, rect.height / 2, state.scale * factor);
}

// ---- pan (Pointer Events) ----
let dragging = false;
let startX = 0, startY = 0, startTx = 0, startTy = 0;

function onPointerDown(e) {
  dragging = true;
  startX = e.clientX; startY = e.clientY;
  startTx = state.tx; startTy = state.ty;
  viewport.setPointerCapture(e.pointerId);
  viewport.classList.add('grabbing');
  viewport.addEventListener('pointermove', onPointerMove);
  viewport.addEventListener('pointerup', onPointerUp);
  viewport.addEventListener('pointercancel', onPointerUp);
}

function onPointerMove(e) {
  if (!dragging) return;
  state.tx = startTx + (e.clientX - startX);
  state.ty = startTy + (e.clientY - startY);
  applyTransform();
}

function onPointerUp(e) {
  dragging = false;
  viewport.releasePointerCapture?.(e.pointerId);
  viewport.classList.remove('grabbing');
  viewport.removeEventListener('pointermove', onPointerMove);
  viewport.removeEventListener('pointerup', onPointerUp);
  viewport.removeEventListener('pointercancel', onPointerUp);
}

function onKey(e) {
  if (e.key === 'Escape') { e.stopPropagation(); closeOverlay(); }
  else if (e.key === '+' || e.key === '=') zoomAroundCenter(1.2);
  else if (e.key === '-' || e.key === '_') zoomAroundCenter(1 / 1.2);
  else if (e.key === '0') fitToScreen();
}
