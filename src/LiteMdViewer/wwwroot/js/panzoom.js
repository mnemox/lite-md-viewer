// Reusable pan + zoom for an SVG "stage" inside a "viewport" element. Shared by the
// Mermaid fullscreen overlay (graphview.js) and the relations graph (relations.js).
// Wheel zooms toward the cursor; dragging pans. fit()/zoomIn()/zoomOut() drive toolbar
// buttons and keys. wasDragged() lets click handlers ignore a click that was really a
// pan. Pass { skipSelector } so presses on matching elements (e.g. graph nodes) don't
// start a pan, keeping their click/dblclick handlers intact.

const MIN = 0.1;
const MAX = 12;
const clamp = (s) => Math.max(MIN, Math.min(MAX, s));

export function createPanZoom(viewport, stage, { skipSelector = null, fitMargin = 0.92, maxFitScale = Infinity } = {}) {
  const st = { scale: 1, tx: 0, ty: 0 };
  let moved = false;

  const apply = () => { stage.style.transform = `translate(${st.tx}px, ${st.ty}px) scale(${st.scale})`; };

  function fit() {
    const clone = stage.firstElementChild;
    if (!clone) return;
    const vp = viewport.getBoundingClientRect();
    let w, h;
    const vb = clone.viewBox && clone.viewBox.baseVal;
    if (vb && vb.width && vb.height) { w = vb.width; h = vb.height; }
    else { const b = clone.getBBox ? clone.getBBox() : null; if (!b) return; w = b.width; h = b.height; }
    if (!w || !h) return;
    clone.style.width = w + 'px';                 // pin to a pixel box for clean scale math
    clone.style.height = h + 'px';
    const target = Math.min(vp.width / w, vp.height / h) * fitMargin;
    st.scale = clamp(Math.min(target, maxFitScale));   // never blow a small graph up past maxFitScale

    st.tx = (vp.width - w * st.scale) / 2;        // center
    st.ty = (vp.height - h * st.scale) / 2;
    apply();
  }

  // Zoom keeping the content point under (cx,cy) [viewport coords] fixed.
  function zoomAt(cx, cy, next) {
    next = clamp(next);
    if (next === st.scale) return;
    const px = (cx - st.tx) / st.scale;
    const py = (cy - st.ty) / st.scale;
    st.scale = next;
    st.tx = cx - px * st.scale;
    st.ty = cy - py * st.scale;
    apply();
  }

  function zoomCenter(factor) {
    const r = viewport.getBoundingClientRect();
    zoomAt(r.width / 2, r.height / 2, st.scale * factor);
  }

  function onWheel(e) {
    e.preventDefault();
    const r = viewport.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, st.scale * Math.exp(-e.deltaY * 0.0015));
  }

  // ---- pan (Pointer Events) ----
  let dragging = false, sx = 0, sy = 0, stx = 0, sty = 0;
  function onDown(e) {
    if (e.button != null && e.button !== 0) return;
    moved = false;
    if (skipSelector && e.target.closest && e.target.closest(skipSelector)) return; // let node clicks through
    dragging = true;
    sx = e.clientX; sy = e.clientY; stx = st.tx; sty = st.ty;
    viewport.setPointerCapture?.(e.pointerId);
    viewport.classList.add('grabbing');
    viewport.addEventListener('pointermove', onMove);
    viewport.addEventListener('pointerup', onUp);
    viewport.addEventListener('pointercancel', onUp);
  }
  function onMove(e) {
    if (!dragging) return;
    const dx = e.clientX - sx, dy = e.clientY - sy;
    if (!moved && Math.hypot(dx, dy) > 4) moved = true;
    st.tx = stx + dx; st.ty = sty + dy;
    apply();
  }
  function onUp(e) {
    dragging = false;
    viewport.releasePointerCapture?.(e.pointerId);
    viewport.classList.remove('grabbing');
    viewport.removeEventListener('pointermove', onMove);
    viewport.removeEventListener('pointerup', onUp);
    viewport.removeEventListener('pointercancel', onUp);
  }

  viewport.addEventListener('wheel', onWheel, { passive: false });
  viewport.addEventListener('pointerdown', onDown);

  return {
    fit,
    zoomIn: () => zoomCenter(1.2),
    zoomOut: () => zoomCenter(1 / 1.2),
    wasDragged: () => moved,
  };
}
