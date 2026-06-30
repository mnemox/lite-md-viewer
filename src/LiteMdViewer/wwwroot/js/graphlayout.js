// Pure layout for the relations graph (no DOM, no deps). Assigns every node an integer
// "level" so reference parents sit above their children and same-level (sibling) nodes
// share a level — i.e. one horizontal row in 2D and one ring in 3D. Also emits 2D
// coordinates. References may be cyclic, so the level passes are bounded.

export function computeLayout(graph, opts = {}) {
  const colGap = opts.colGap ?? 170;
  const rowGap = opts.rowGap ?? 110;

  const ids = graph.nodes.map((n) => n.id);
  const idSet = new Set(ids);
  const inGraph = (e) => idSet.has(e.fromId) && idSet.has(e.toId);
  const refEdges = graph.edges.filter((e) => e.kind === 'reference' && inGraph(e));
  const sibEdges = graph.edges.filter((e) => e.kind === 'sibling' && inGraph(e));

  const parents = new Map(ids.map((id) => [id, []]));
  const children = new Map(ids.map((id) => [id, []]));
  for (const e of refEdges) { children.get(e.fromId).push(e.toId); parents.get(e.toId).push(e.fromId); }

  // Level = longest reference path from a root. Roots (no incoming reference) start at 0.
  const level = new Map(ids.map((id) => [id, 0]));
  const cap = ids.length + 2;
  const relaxRefs = () => {
    let changed = false;
    for (const e of refEdges) {
      const want = level.get(e.fromId) + 1;
      if (level.get(e.toId) < want) { level.set(e.toId, want); changed = true; }
    }
    return changed;
  };
  for (let i = 0; i < cap && relaxRefs(); i++) { /* iterate to fixpoint (bounded) */ }

  // Force sibling-connected nodes to a shared level (the max), then push children back
  // below their parents. Iterate to a bounded fixpoint.
  for (let i = 0; i < cap; i++) {
    let changed = false;
    for (const e of sibEdges) {
      const m = Math.max(level.get(e.fromId), level.get(e.toId));
      if (level.get(e.fromId) !== m) { level.set(e.fromId, m); changed = true; }
      if (level.get(e.toId) !== m) { level.set(e.toId, m); changed = true; }
    }
    if (relaxRefs()) changed = true;
    if (!changed) break;
  }

  // Group into rows by level.
  const rows = new Map();
  for (const id of ids) {
    const L = level.get(id);
    (rows.get(L) || rows.set(L, []).get(L)).push(id);
  }
  const levels = [...rows.keys()].sort((a, b) => a - b);

  // Order within each row: start stable by id, then a few barycenter passes (mean column
  // of reference neighbours) to reduce edge crossings.
  const order = new Map();
  for (const L of levels) {
    rows.get(L).sort((a, b) => a - b).forEach((id, i) => order.set(id, i));
  }
  const bary = (id) => {
    const ns = [...parents.get(id), ...children.get(id)];
    if (!ns.length) return order.get(id);
    return ns.reduce((s, x) => s + order.get(x), 0) / ns.length;
  };
  for (let pass = 0; pass < 4; pass++) {
    for (const L of levels) {
      rows.get(L).sort((a, b) => bary(a) - bary(b) || a - b).forEach((id, i) => order.set(id, i));
    }
  }

  // 2D coordinates: each row centered horizontally around x=0, stacked by level.
  const pos2d = new Map();
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const L of levels) {
    const row = rows.get(L);
    const span = (row.length - 1) * colGap;
    row.forEach((id, i) => {
      const x = i * colGap - span / 2;
      const y = L * rowGap;
      pos2d.set(id, { x, y });
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    });
  }
  if (!ids.length) { minX = maxX = minY = maxY = 0; }

  return { level, rows, levels, pos2d, bounds: { minX, maxX, minY, maxY } };
}
