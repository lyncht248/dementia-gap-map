// Framework-agnostic renderer for the Qwen embedding "theme atlas".
//
// `mountAtlas(root, data, opts)` builds the canvas inside `root`, wires pan /
// zoom / hover-to-trace-citations / lasso region-select, and returns a handle
// the React shell drives (select mode, filters, reset view, clear selection).
// The disease-area legend + year filter live in the app's Filters panel, not on
// the canvas. Data: scripts/build_atlas.py -> web/public/atlas/atlas.json.

export interface AtlasMajor {
  id: string; label: string; color: string; x: number; y: number; count: number;
}
export interface AtlasMinor {
  id: number; major: string; label: string; color: string;
  x: number; y: number; count: number;
}
export interface AtlasData {
  meta: {
    spacing: number; n_papers: number; n_major: number; n_minor: number;
    n_edges: number; year_min: number; year_max: number; model: string;
  };
  majors: AtlasMajor[];
  minors: AtlasMinor[];
  /** [px, py, fineClusterId, year] */
  points: number[][];
  titles: string[];
  /** paper_id per point (e.g. "pmid:12345") */
  ids: string[];
  /** [i, j] index pairs into points */
  edges: number[][];
}

export interface SelectedPaper {
  i: number;
  paper_id: string;
  title: string;
  year: number;
  minor: string;
  major: string;
  degree: number;
}

export interface AtlasReady {
  majors: AtlasMajor[];
  yearMin: number;
  yearMax: number;
  total: number;
}

export interface AtlasOptions {
  onSelect?: (rows: SelectedPaper[]) => void;
  onSelectModeChange?: (on: boolean) => void;
  onReady?: (meta: AtlasReady) => void;
  onCount?: (visible: number) => void;
}

export interface AtlasHandle {
  destroy: () => void;
  setSelectMode: (on: boolean) => void;
  clearSelection: () => void;
  resetView: () => void;
  setFilter: (hiddenMajors: string[], yearRange: [number, number]) => void;
  /** Agent control: select papers by id (updates the feed), spotlight with a
   * ring, or animate the camera to their bounding box. */
  selectByIds: (ids: string[]) => number;
  highlightByIds: (ids: string[]) => number;
  clearHighlight: () => void;
  zoomToIds: (ids: string[], pad?: number) => number;
}

type Pt = { x: number; y: number };

function hx(h: string): [number, number, number] {
  h = h.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function mix(a: number[], b: number[], t: number) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}
function esc(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));
}
function pointInPolygon(p: Pt, poly: Pt[]): boolean {
  if (poly.length < 3) return false;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i].x, yi = poly[i].y, xj = poly[j].x, yj = poly[j].y;
    const hit = yi > p.y !== yj > p.y && p.x < ((xj - xi) * (p.y - yi)) / (yj - yi) + xi;
    if (hit) inside = !inside;
  }
  return inside;
}

const GREY = "rgb(200,200,204)";

export function mountAtlas(root: HTMLElement, DATA: AtlasData, opts: AtlasOptions = {}): AtlasHandle {
  root.classList.add("atlas-root");
  root.innerHTML = "";
  const cv = document.createElement("canvas");
  cv.className = "atlas-canvas";
  const tip = document.createElement("div");
  tip.className = "atlas-tip";
  root.append(cv, tip);

  const ctx = cv.getContext("2d")!;
  let DPR = Math.min(window.devicePixelRatio || 1, 2);

  const P = DATA.points;
  const idIndex = new Map<string, number>();
  DATA.ids.forEach((id, i) => idIndex.set(id, i));
  const idsToIdx = (ids: string[]): number[] => {
    const out: number[] = [];
    for (const id of ids) {
      const i = idIndex.get(id);
      if (i !== undefined) out.push(i);
    }
    return out;
  };
  let minx = 1e9, maxx = -1e9, miny = 1e9, maxy = -1e9;
  for (const p of P) {
    if (p[0] < minx) minx = p[0]; if (p[0] > maxx) maxx = p[0];
    if (p[1] < miny) miny = p[1]; if (p[1] > maxy) maxy = p[1];
  }
  const worldW = maxx - minx, worldH = maxy - miny;
  const cx0 = (minx + maxx) / 2, cy0 = (miny + maxy) / 2;

  const spacing = DATA.meta.spacing || 0.032;
  const dotR = spacing * 0.46;

  const minorMajor: Record<number, string> = {};
  const minorLabel: Record<number, string> = {};
  for (const m of DATA.minors) { minorMajor[m.id] = m.major; minorLabel[m.id] = m.label; }
  const majorLabel: Record<string, string> = {};
  for (const M of DATA.majors) majorLabel[M.id] = M.label;

  // ---- colour: own disease-area flat colour, blending only in a thin border seam
  const majArr = DATA.majors.map((M) => ({ id: M.id, x: M.x, y: M.y, c: hx(M.color) }));
  const majIdx: Record<string, number> = {};
  majArr.forEach((m, i) => (majIdx[m.id] = i));
  const SEAM = spacing * 7;
  const SEAM_MAX = 0.5;
  const PC: string[] = new Array(P.length);
  for (let i = 0; i < P.length; i++) {
    const px = P[i][0], py = P[i][1];
    const own = majIdx[minorMajor[P[i][2]]];
    const oc = majArr[own].c;
    let bd = Infinity, bc: number[] | null = null;
    for (let j = 0; j < majArr.length; j++) {
      if (j === own) continue;
      const dx = px - majArr[j].x, dy = py - majArr[j].y, d2 = dx * dx + dy * dy;
      if (d2 < bd) { bd = d2; bc = majArr[j].c; }
    }
    let c: number[] = oc;
    if (bc) { const dOther = Math.sqrt(bd); if (dOther < SEAM) c = mix(oc, bc, (1 - dOther / SEAM) * SEAM_MAX); }
    PC[i] = "rgb(" + Math.round(c[0]) + "," + Math.round(c[1]) + "," + Math.round(c[2]) + ")";
  }

  // ---- citation adjacency ----
  const ADJ: number[][] = Array.from({ length: P.length }, () => []);
  for (const e of DATA.edges || []) { ADJ[e[0]].push(e[1]); ADJ[e[1]].push(e[0]); }
  let hoverIdx = -1;

  // ---- filter state ----
  const hiddenMajors = new Set<string>();
  let yLo = DATA.meta.year_min, yHi = DATA.meta.year_max;
  const visible = (i: number) => {
    const p = P[i];
    return !hiddenMajors.has(minorMajor[p[2]]) && p[3] >= yLo && p[3] <= yHi;
  };
  function reportCount() {
    if (!opts.onCount) return;
    let n = 0;
    for (let i = 0; i < P.length; i++) if (visible(i)) n++;
    opts.onCount(n);
  }

  // ---- selection ----
  let selecting = false;
  let lasso: Pt[] = [];
  let selSet = new Set<number>();
  let hlSet = new Set<number>(); // agent spotlight (amber ring), distinct from selection
  let selAnchor = -1; // the clicked paper (draws citation lines), -1 for lasso

  const rowFor = (i: number): SelectedPaper => ({
    i, paper_id: DATA.ids[i], title: DATA.titles[i], year: P[i][3],
    minor: minorLabel[P[i][2]], major: majorLabel[minorMajor[P[i][2]]], degree: ADJ[i].length,
  });
  function pickAt(x: number, y: number): number {
    const rr = Math.max(2.5, dotR * view.s) + 2;
    let best = -1, bd = rr * rr;
    for (let i = 0; i < P.length; i++) {
      if (!visible(i)) continue;
      const p = P[i];
      const dx = wx(p[0]) - x, dy = wy(p[1]) - y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }
  // Click a paper -> select it AND everything it cites / is cited by.
  function clickSelect(i: number) {
    selAnchor = i;
    const nbrs = ADJ[i].filter(visible);
    selSet = new Set<number>([i, ...nbrs]);
    const ordered = nbrs.slice().sort((a, b) => ADJ[b].length - ADJ[a].length);
    opts.onSelect?.([rowFor(i), ...ordered.map(rowFor)]);
    draw();
  }

  // ---- view ----
  const view = { s: 1, tx: 0, ty: 0 };
  let baseS = 1;
  const cw = () => cv.clientWidth;
  const ch = () => cv.clientHeight;
  function fit() {
    const w = cw(), h = ch(), pad = 48;
    view.s = Math.min((w - 2 * pad) / worldW, (h - 2 * pad) / worldH);
    view.tx = w / 2 - cx0 * view.s;
    view.ty = h / 2 - cy0 * view.s;
  }
  const wx = (x: number) => x * view.s + view.tx;
  const wy = (y: number) => -y * view.s + (ch() - view.ty);

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = cw() * DPR; cv.height = ch() * DPR;
    draw();
  }

  function draw() {
    const w = cw(), h = ch();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const zoom = view.s / baseS;
    const r = Math.min(Math.max(1.0, dotR * view.s), 9);
    const hoverOn = hoverIdx >= 0 && visible(hoverIdx);
    const nb = hoverOn ? new Set(ADJ[hoverIdx]) : null;
    const selOn = !hoverOn && selSet.size > 0;

    for (let i = 0; i < P.length; i++) {
      const p = P[i];
      const X = wx(p[0]), Y = wy(p[1]);
      if (X < -4 || X > w + 4 || Y < -4 || Y > h + 4) continue;
      if (!visible(i)) {
        ctx.globalAlpha = 0.16;
        ctx.beginPath(); ctx.arc(X, Y, r, 0, 6.2832);
        ctx.fillStyle = GREY; ctx.fill();
        continue;
      }
      let a = 1;
      if (hoverOn && i !== hoverIdx && !nb!.has(i)) a = 0.55;
      else if (selOn && !selSet.has(i)) a = 0.3;
      ctx.globalAlpha = a;
      ctx.beginPath(); ctx.arc(X, Y, r, 0, 6.2832);
      ctx.fillStyle = PC[i]; ctx.fill();
    }
    ctx.globalAlpha = 1;

    // citation links — from the hovered paper, or (when not hovering) the paper
    // clicked to build the current selection.
    const lineAnchor = hoverOn ? hoverIdx : (selAnchor >= 0 && visible(selAnchor) ? selAnchor : -1);
    if (lineAnchor >= 0) {
      const hX = wx(P[lineAnchor][0]), hY = wy(P[lineAnchor][1]);
      ctx.strokeStyle = "rgba(28,28,38,.5)";
      ctx.lineWidth = Math.min(1.5, 0.7 * zoom);
      ctx.beginPath();
      for (const j of ADJ[lineAnchor]) {
        if (!visible(j)) continue;
        ctx.moveTo(hX, hY); ctx.lineTo(wx(P[j][0]), wy(P[j][1]));
      }
      ctx.stroke();
      for (const j of ADJ[lineAnchor]) {
        if (!visible(j)) continue;
        ctx.beginPath(); ctx.arc(wx(P[j][0]), wy(P[j][1]), r, 0, 6.2832);
        ctx.fillStyle = PC[j]; ctx.fill();
      }
      ctx.beginPath(); ctx.arc(hX, hY, r + 2, 0, 6.2832);
      ctx.fillStyle = PC[lineAnchor]; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#1b1b1f"; ctx.stroke();
    }

    // labels
    const showMinor = zoom > 1.7;
    const majorAlpha = showMinor ? Math.max(0.12, 1 - (zoom - 1.7) / 1.4) : 1;
    if (majorAlpha > 0.02) {
      for (const M of DATA.majors) {
        if (hiddenMajors.has(M.id)) continue;
        drawLabel(M.label, wx(M.x), wy(M.y), 15 + Math.min(7, M.count / 500), M.color, majorAlpha, true);
      }
    }
    if (showMinor) {
      const a = Math.min(1, (zoom - 1.7) / 0.6);
      for (const m of DATA.minors) {
        if (hiddenMajors.has(m.major)) continue;
        if (m.count < 40 && zoom < 3) continue;
        drawLabel(m.label, wx(m.x), wy(m.y), 16, "#22222a", a, false);
      }
    }

    // agent spotlight — amber rings on highlighted papers
    if (hlSet.size) {
      ctx.lineWidth = 2.4;
      ctx.strokeStyle = "#f2a900";
      for (const i of hlSet) {
        if (!visible(i)) continue;
        const X = wx(P[i][0]), Y = wy(P[i][1]);
        if (X < -6 || X > w + 6 || Y < -6 || Y > h + 6) continue;
        ctx.globalAlpha = 0.95;
        ctx.beginPath();
        ctx.arc(X, Y, r + 4, 0, 6.2832);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // lasso path
    if (lasso.length > 1) {
      ctx.beginPath();
      ctx.moveTo(lasso[0].x, lasso[0].y);
      for (let k = 1; k < lasso.length; k++) ctx.lineTo(lasso[k].x, lasso[k].y);
      ctx.closePath();
      ctx.fillStyle = "rgba(47,111,87,0.10)";
      ctx.fill();
      ctx.setLineDash([6, 5]);
      ctx.lineWidth = 1.5; ctx.strokeStyle = "#2f6f57";
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function drawLabel(text: string, x: number, y: number, size: number, color: string, alpha: number, bold: boolean) {
    ctx.font = `${bold ? "700" : "600"} ${size}px -apple-system,Segoe UI,Roboto,sans-serif`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.globalAlpha = alpha;
    ctx.lineWidth = bold ? 4.5 : 4; ctx.strokeStyle = "rgba(255,255,255,.95)"; ctx.lineJoin = "round";
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color; ctx.fillText(text, x, y);
    ctx.globalAlpha = 1;
  }

  // ---- interaction ----
  const rel = (e: MouseEvent): Pt => { const b = cv.getBoundingClientRect(); return { x: e.clientX - b.left, y: e.clientY - b.top }; };
  let drag: { x: number; y: number; tx: number; ty: number } | null = null;
  let dragMoved = false;

  const onDown = (e: MouseEvent) => {
    const m = rel(e);
    if (selecting) { lasso = [m]; hideTip(); return; }
    drag = { x: m.x, y: m.y, tx: view.tx, ty: view.ty }; dragMoved = false; cv.classList.add("drag");
  };
  const onUp = () => {
    if (selecting && lasso.length > 2) { finishSelection(); return; }
    if (selecting) { lasso = []; draw(); return; }
    if (drag) {
      cv.classList.remove("drag");
      if (!dragMoved) {
        // a click (not a pan): select the paper under the cursor + its citations,
        // or clear the selection if the click landed on empty space.
        const i = pickAt(drag.x, drag.y);
        if (i >= 0) clickSelect(i);
        else if (selSet.size) { selSet = new Set(); selAnchor = -1; opts.onSelect?.([]); draw(); }
      }
      drag = null;
    }
  };
  const onLeave = () => { if (hoverIdx >= 0) { hoverIdx = -1; draw(); } hideTip(); };
  const onMove = (e: MouseEvent) => {
    if (selecting) {
      if (lasso.length) { lasso.push(rel(e)); draw(); }
      return;
    }
    if (drag) {
      const m = rel(e);
      if (Math.abs(m.x - drag.x) + Math.abs(m.y - drag.y) > 4) dragMoved = true;
      if (hoverIdx >= 0) hoverIdx = -1;
      view.tx = drag.tx + (m.x - drag.x); view.ty = drag.ty - (m.y - drag.y);
      draw(); hideTip(); return;
    }
    if (e.target !== cv) { if (hoverIdx >= 0) { hoverIdx = -1; draw(); } hideTip(); return; }
    hover(e);
  };
  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const m = rel(e);
    const intensity = e.ctrlKey ? 0.01 : 0.0015;
    zoomAt(m.x, m.y, Math.exp(-e.deltaY * intensity));
  };

  function zoomAt(sx: number, sy: number, f: number) {
    const ns = Math.max(baseS * 0.6, Math.min(baseS * 40, view.s * f));
    const wxp = (sx - view.tx) / view.s, wyp = (ch() - sy - view.ty) / view.s;
    view.s = ns;
    view.tx = sx - wxp * view.s;
    view.ty = ch() - sy - wyp * view.s;
    draw();
  }

  function finishSelection() {
    const poly = lasso;
    lasso = [];
    const rows: SelectedPaper[] = [];
    selSet = new Set();
    selAnchor = -1;
    for (let i = 0; i < P.length; i++) {
      if (!visible(i)) continue;
      const p = P[i];
      if (pointInPolygon({ x: wx(p[0]), y: wy(p[1]) }, poly)) {
        selSet.add(i);
        rows.push({
          i, paper_id: DATA.ids[i], title: DATA.titles[i], year: p[3],
          minor: minorLabel[p[2]], major: majorLabel[minorMajor[p[2]]], degree: ADJ[i].length,
        });
      }
    }
    rows.sort((a, b) => b.degree - a.degree || b.year - a.year);
    selecting = false;
    cv.classList.remove("selecting");
    opts.onSelectModeChange?.(false);
    opts.onSelect?.(rows);
    draw();
  }

  function hover(e: MouseEvent) {
    const m = rel(e);
    const r = Math.max(2.5, dotR * view.s) + 2;
    let best = -1, bd = r * r;
    for (let i = 0; i < P.length; i++) {
      if (!visible(i)) continue;
      const p = P[i];
      const dx = wx(p[0]) - m.x, dy = wy(p[1]) - m.y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    if (best !== hoverIdx) { hoverIdx = best; draw(); }
    if (best < 0) { hideTip(); return; }
    const p = P[best], fid = p[2], deg = ADJ[best].length;
    tip.innerHTML =
      `<div class="t">${esc(DATA.titles[best])}</div>` +
      `<div class="m">${p[3]} · ${esc(minorLabel[fid])} · ${esc(majorLabel[minorMajor[fid]])}</div>` +
      `<div class="m">${deg} citation link${deg === 1 ? "" : "s"} in corpus</div>`;
    tip.style.opacity = "1";
    const b = cv.getBoundingClientRect();
    tip.style.left = Math.min(m.x + 14, b.width - 320) + "px";
    tip.style.top = Math.min(m.y + 14, b.height - 90) + "px";
  }
  const hideTip = () => { tip.style.opacity = "0"; };

  cv.addEventListener("mousedown", onDown);
  cv.addEventListener("mouseleave", onLeave);
  cv.addEventListener("wheel", onWheel, { passive: false });
  window.addEventListener("mouseup", onUp);
  window.addEventListener("mousemove", onMove);
  const ro = new ResizeObserver(() => resize());
  ro.observe(root);

  fit(); baseS = view.s; resize();
  opts.onReady?.({ majors: DATA.majors, yearMin: DATA.meta.year_min, yearMax: DATA.meta.year_max, total: P.length });
  reportCount();

  return {
    destroy() {
      ro.disconnect();
      cv.removeEventListener("mousedown", onDown);
      cv.removeEventListener("mouseleave", onLeave);
      cv.removeEventListener("wheel", onWheel);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("mousemove", onMove);
      root.innerHTML = "";
      root.classList.remove("atlas-root");
    },
    setSelectMode(on: boolean) {
      selecting = on;
      lasso = [];
      cv.classList.toggle("selecting", on);
      if (on && hoverIdx >= 0) hoverIdx = -1;
      hideTip();
      draw();
    },
    clearSelection() { selSet = new Set(); selAnchor = -1; draw(); },
    resetView() { fit(); baseS = view.s; draw(); },
    selectByIds(ids: string[]) {
      const idx = idsToIdx(ids);
      selSet = new Set(idx);
      selAnchor = idx.length === 1 ? idx[0] : -1;
      const rows = idx.map(rowFor).sort((a, b) => b.degree - a.degree || b.year - a.year);
      opts.onSelect?.(rows);
      draw();
      return idx.length;
    },
    highlightByIds(ids: string[]) {
      const idx = idsToIdx(ids);
      hlSet = new Set(idx);
      draw();
      return idx.length;
    },
    clearHighlight() { hlSet = new Set(); draw(); },
    zoomToIds(ids: string[], pad = 60) {
      const idx = idsToIdx(ids);
      if (!idx.length) return 0;
      let mnx = 1e9, mxx = -1e9, mny = 1e9, mxy = -1e9;
      for (const i of idx) {
        const p = P[i];
        if (p[0] < mnx) mnx = p[0]; if (p[0] > mxx) mxx = p[0];
        if (p[1] < mny) mny = p[1]; if (p[1] > mxy) mxy = p[1];
      }
      const bw = Math.max(mxx - mnx, 1e-6), bh = Math.max(mxy - mny, 1e-6);
      const w = cw(), h = ch();
      const s = Math.min((w - 2 * pad) / bw, (h - 2 * pad) / bh);
      view.s = Math.max(baseS * 0.6, Math.min(baseS * 40, s));
      view.tx = w / 2 - ((mnx + mxx) / 2) * view.s;
      view.ty = h / 2 - ((mny + mxy) / 2) * view.s;
      draw();
      return idx.length;
    },
    setFilter(hm: string[], yr: [number, number]) {
      hiddenMajors.clear();
      for (const id of hm) hiddenMajors.add(id);
      yLo = yr[0]; yHi = yr[1];
      draw();
      reportCount();
    },
  };
}
