// 3D relations graph (Three.js, vendored as window.THREE). Nodes are billboard label
// sprites; reference edges are solid lines with cone arrowheads, sibling edges dashed.
// Layout mirrors the 2D levels: the focused document sits at the center, its same-level
// peers ring around it, and other levels are rings stacked along the Y axis. Orbit with
// drag (rotate), wheel (zoom), shift/right-drag (pan). Clicking a node opens its menu,
// double-click navigates, clicking an edge removes it — same callbacks as 2D.

export function createGraph3d(container, cb) {
  const THREE = window.THREE;
  if (!THREE) {
    container.innerHTML = '<div class="rel-error">3D view needs the three.js library (failed to load).</div>';
    return { render() {}, zoomIn() {}, zoomOut() {}, fit() {}, resize() {}, dispose() {} };
  }

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.domElement.className = 'rel-3d-canvas';
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 20000);
  const target = new THREE.Vector3();
  const homeTarget = new THREE.Vector3();
  let radius = 300, homeRadius = 300, theta = Math.PI * 0.25, phi = Math.PI * 0.32;

  const raycaster = new THREE.Raycaster();
  raycaster.params.Line.threshold = 6;
  const ndc = new THREE.Vector2();

  let nodeObjs = [];   // { sprite, id }
  let edgeObjs = [];   // { line, edge, cone? }
  let disposables = [];
  let rafPending = false;
  let alive = true;

  function colors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n, fb) => cs.getPropertyValue(n).trim() || fb;
    return {
      bg: v('--bg-elev', '#ffffff'), border: v('--border', '#cccccc'),
      text: v('--text', '#111111'), dim: v('--text-dim', '#888888'),
      accent: v('--accent', '#2f6fed'), danger: v('--danger', '#d64541'),
    };
  }

  function makeNode(node, active, col) {
    const W = 256, H = 96, pad = 8, r = 16;
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
    const ctx = cv.getContext('2d');
    roundRect(ctx, pad, pad, W - 2 * pad, H - 2 * pad, r);
    ctx.fillStyle = col.bg; ctx.fill();
    ctx.lineWidth = active ? 8 : 4;
    ctx.strokeStyle = active ? col.accent : col.border; ctx.stroke();
    ctx.fillStyle = node.missing ? col.danger : col.text;
    ctx.font = '600 30px system-ui, -apple-system, sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(trunc(node.title || ('#' + node.id), 16), W / 2, H / 2);
    const tex = new THREE.CanvasTexture(cv);
    tex.anisotropy = 4;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true });
    const sp = new THREE.Sprite(mat);
    const s = 46;
    sp.scale.set(s, s * H / W, 1);
    disposables.push(tex, mat);
    return sp;
  }

  function makeArrow(a, b, color) {
    const dir = new THREE.Vector3().subVectors(b, a);
    if (dir.length() < 1) return null;
    dir.normalize();
    const geo = new THREE.ConeGeometry(5, 12, 12);
    const mat = new THREE.MeshBasicMaterial({ color: new THREE.Color(color) });
    const cone = new THREE.Mesh(geo, mat);
    cone.position.copy(b).addScaledVector(dir, -22);
    cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
    disposables.push(geo, mat);
    return cone;
  }

  function clearScene() {
    nodeObjs.forEach((n) => scene.remove(n.sprite));
    edgeObjs.forEach((e) => { scene.remove(e.line); if (e.cone) scene.remove(e.cone); });
    disposables.forEach((d) => d.dispose && d.dispose());
    disposables = []; nodeObjs = []; edgeObjs = [];
  }

  function render(graph, layout) {
    clearScene();
    const col = colors();
    const focusLevel = layout.level.get(graph.activeId) ?? 0;
    const LAYER = 95;
    const pos = new Map();
    for (const L of layout.levels) {
      const row = layout.rows.get(L);
      const y = (focusLevel - L) * LAYER;
      let ring = row;
      if (L === focusLevel) {
        pos.set(graph.activeId, new THREE.Vector3(0, y, 0));
        ring = row.filter((id) => id !== graph.activeId);
      }
      const R = ringRadius(ring.length);
      ring.forEach((id, i) => {
        const a = (i / Math.max(ring.length, 1)) * Math.PI * 2;
        pos.set(id, new THREE.Vector3(Math.cos(a) * R, y, Math.sin(a) * R));
      });
    }

    for (const n of graph.nodes) {
      const p = pos.get(n.id); if (!p) continue;
      const sp = makeNode(n, n.id === graph.activeId, col);
      sp.position.copy(p);
      scene.add(sp);
      nodeObjs.push({ sprite: sp, id: n.id });
    }

    for (const e of graph.edges) {
      const a = pos.get(e.fromId), b = pos.get(e.toId);
      if (!a || !b) continue;
      const isRef = e.kind === 'reference';
      const geo = new THREE.BufferGeometry().setFromPoints([a, b]);
      const mat = isRef
        ? new THREE.LineBasicMaterial({ color: new THREE.Color(col.dim) })
        : new THREE.LineDashedMaterial({ color: new THREE.Color(col.dim), dashSize: 9, gapSize: 6 });
      const line = new THREE.Line(geo, mat);
      if (!isRef) line.computeLineDistances();
      scene.add(line);
      disposables.push(geo, mat);
      const rec = { line, edge: e };
      if (isRef) { const cone = makeArrow(a, b, col.dim); if (cone) { scene.add(cone); rec.cone = cone; } }
      edgeObjs.push(rec);
    }

    frameToContent(pos);
    resize();
    requestRender();
  }

  function frameToContent(pos) {
    const c = new THREE.Vector3(); let n = 0, maxR = 1;
    pos.forEach((p) => { c.add(p); n++; });
    if (n) c.multiplyScalar(1 / n);
    pos.forEach((p) => { maxR = Math.max(maxR, p.distanceTo(c)); });
    homeTarget.copy(c); target.copy(c);
    homeRadius = radius = Math.max(140, (maxR + 70) / Math.sin((camera.fov * Math.PI / 180) / 2) * 0.6);
  }

  function updateCamera() {
    phi = Math.max(0.05, Math.min(Math.PI - 0.05, phi));
    const sinP = Math.sin(phi);
    camera.position.set(
      target.x + radius * sinP * Math.sin(theta),
      target.y + radius * Math.cos(phi),
      target.z + radius * sinP * Math.cos(theta),
    );
    camera.lookAt(target);
  }

  function resize() {
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    requestRender();
  }

  function requestRender() {
    if (rafPending || !alive) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false; if (!alive) return;
      updateCamera();
      renderer.render(scene, camera);
    });
  }

  // ---- orbit + picking ----
  let dragging = false, dragBtn = 0, moved = false, sx = 0, sy = 0, st = 0, sp0 = 0;
  const panStart = new THREE.Vector3();
  function onDown(e) {
    dragging = true; dragBtn = e.button; moved = false;
    sx = e.clientX; sy = e.clientY; st = theta; sp0 = phi; panStart.copy(target);
    try { renderer.domElement.setPointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
  }
  function onMove(e) {
    if (!dragging) return;
    const dx = e.clientX - sx, dy = e.clientY - sy;
    if (!moved && Math.hypot(dx, dy) > 4) moved = true;
    if (dragBtn === 2 || e.shiftKey) {
      const k = radius * 0.0016;
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
      const up = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
      target.copy(panStart).addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
    } else {
      theta = st - dx * 0.01; phi = sp0 - dy * 0.01;
    }
    requestRender();
  }
  function onUp(e) {
    try { renderer.domElement.releasePointerCapture(e.pointerId); } catch { /* capture is best-effort */ }
    const wasMoved = moved; dragging = false;
    if (!wasMoved) handleClick(e);
  }
  function onWheel(e) {
    e.preventDefault();
    radius = Math.max(20, Math.min(8000, radius * Math.exp(e.deltaY * 0.0012)));
    requestRender();
  }

  let clickTimer = null;
  function handleClick(e) {
    const hit = pick(e);
    if (!hit) return;
    if (hit.type === 'node') {
      clearTimeout(clickTimer);
      const x = e.clientX, y = e.clientY;
      clickTimer = setTimeout(() => cb.nodeMenu(hit.id, x, y), 240);
    } else {
      cb.edgeMenu(hit.edge, e.clientX, e.clientY);
    }
  }
  function onDbl(e) {
    clearTimeout(clickTimer);
    const hit = pick(e);
    if (hit && hit.type === 'node') cb.navigate(hit.id);
  }

  function pick(e) {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    raycaster.setFromCamera(ndc, camera);
    const ns = raycaster.intersectObjects(nodeObjs.map((n) => n.sprite), false);
    if (ns.length) { const o = nodeObjs.find((n) => n.sprite === ns[0].object); if (o) return { type: 'node', id: o.id }; }
    const es = raycaster.intersectObjects(edgeObjs.map((o) => o.line), false);
    if (es.length) { const o = edgeObjs.find((x) => x.line === es[0].object); if (o) return { type: 'edge', edge: o.edge }; }
    return null;
  }

  const el = renderer.domElement;
  el.addEventListener('pointerdown', onDown);
  el.addEventListener('pointermove', onMove);
  el.addEventListener('pointerup', onUp);
  el.addEventListener('dblclick', onDbl);
  el.addEventListener('wheel', onWheel, { passive: false });
  el.addEventListener('contextmenu', (e) => e.preventDefault());
  const ro = new ResizeObserver(() => resize());
  ro.observe(container);

  return {
    render,
    zoomIn: () => { radius = Math.max(20, radius / 1.2); requestRender(); },
    zoomOut: () => { radius = Math.min(8000, radius * 1.2); requestRender(); },
    fit: () => { target.copy(homeTarget); radius = homeRadius; requestRender(); },
    resize,
    dispose() {
      alive = false;
      ro.disconnect();
      clearScene();
      renderer.dispose();
      el.remove();
    },
  };
}

function ringRadius(n) {
  if (n <= 1) return n === 1 ? 80 : 0;
  return Math.max(90, (n * 62) / (2 * Math.PI));
}
function trunc(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
