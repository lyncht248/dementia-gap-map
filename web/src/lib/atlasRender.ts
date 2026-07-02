// Framework-agnostic renderer for the Qwen embedding "theme atlas".
//
// `mountAtlas(root, data)` builds the canvas + overlay UI inside `root`, wires
// pan / zoom / hover-to-trace-citations, and returns a cleanup function. It is
// the single source of truth for how the atlas renders (the React component in
// AtlasMap.tsx just fetches the data and calls this). The data file is produced
// by scripts/build_atlas.py -> web/public/atlas/atlas.json.

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

export interface AtlasOptions {
  /** called when the user finishes drawing a selection region */
  onSelect?: (rows: SelectedPaper[]) => void;
  /** called when the atlas leaves select mode on its own (after a drag) */
  onSelectModeChange?: (on: boolean) => void;
}

export interface AtlasHandle {
  destroy: () => void;
  setSelectMode: (on: boolean) => void;
  clearSelection: () => void;
}

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

export function mountAtlas(root: HTMLElement, DATA: AtlasData, opts: AtlasOptions = {}): AtlasHandle {
  root.classList.add("atlas-root");
  root.innerHTML = "";

  const cv = document.createElement("canvas");
  cv.className = "atlas-canvas";
  const panel = document.createElement("div");
  panel.className = "atlas-panel";
  const h1 = document.createElement("h1");
  h1.textContent = "Dementia Gap Map — Theme Atlas";
  const sub = document.createElement("p");
  const legend = document.createElement("div");
  legend.className = "atlas-legend";
  const hint = document.createElement("div");
  hint.className = "atlas-hint";
  hint.textContent =
    "Scroll to zoom · drag to pan · hover a dot to trace its citations · zoom in for finer topics";
  panel.append(h1, sub, legend, hint);
  const tip = document.createElement("div");
  tip.className = "atlas-tip";
  const zoomBox = document.createElement("div");
  zoomBox.className = "atlas-zoom";
  const zout = document.createElement("button");
  zout.textContent = "–";
  const zin = document.createElement("button");
  zin.textContent = "+";
  zoomBox.append(zout, zin);
  root.append(cv, panel, tip, zoomBox);

  const ctx = cv.getContext("2d")!;
  let DPR = Math.min(window.devicePixelRatio || 1, 2);

  // ---- world bounds ----
  const P = DATA.points;
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
  const hidden = new Set<string>();

  // ---- colour: own disease-area flat colour, blending toward a neighbour only
  // in a thin seam right at the border (gradient only where topics actually meet).
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

  // ---- selection ----
  let selecting = false;                       // "Select region" mode active
  let selRect: { x0: number; y0: number; x1: number; y1: number } | null = null;
  let selSet = new Set<number>();              // currently selected point indices

  // ---- view ----
  const view = { s: 1, tx: 0, ty: 0 };
  let baseS = 1;
  const cw = () => cv.clientWidth;
  const ch = () => cv.clientHeight;
  function fit() {
    const w = cw(), h = ch(), pad = 60;
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
    const hoverOn = hoverIdx >= 0 && !hidden.has(minorMajor[P[hoverIdx][2]]);
    const nb = hoverOn ? new Set(ADJ[hoverIdx]) : null;
    const selOn = !hoverOn && selSet.size > 0;

    for (let i = 0; i < P.length; i++) {
      const p = P[i], fid = p[2];
      if (hidden.has(minorMajor[fid])) continue;
      const X = wx(p[0]), Y = wy(p[1]);
      if (X < -4 || X > w + 4 || Y < -4 || Y > h + 4) continue;
      let a = 1;
      if (hoverOn && i !== hoverIdx && !nb!.has(i)) a = 0.55;
      else if (selOn && !selSet.has(i)) a = 0.3;
      ctx.globalAlpha = a;
      ctx.beginPath();
      ctx.arc(X, Y, r, 0, 6.2832);
      ctx.fillStyle = PC[i];
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    if (hoverOn) {
      const hX = wx(P[hoverIdx][0]), hY = wy(P[hoverIdx][1]);
      ctx.strokeStyle = "rgba(28,28,38,.5)";
      ctx.lineWidth = Math.min(1.5, 0.7 * zoom);
      ctx.beginPath();
      for (const j of ADJ[hoverIdx]) {
        if (hidden.has(minorMajor[P[j][2]])) continue;
        ctx.moveTo(hX, hY); ctx.lineTo(wx(P[j][0]), wy(P[j][1]));
      }
      ctx.stroke();
      for (const j of ADJ[hoverIdx]) {
        if (hidden.has(minorMajor[P[j][2]])) continue;
        ctx.beginPath(); ctx.arc(wx(P[j][0]), wy(P[j][1]), r, 0, 6.2832);
        ctx.fillStyle = PC[j]; ctx.fill();
      }
      ctx.beginPath(); ctx.arc(hX, hY, r + 2, 0, 6.2832);
      ctx.fillStyle = PC[hoverIdx]; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#1b1b1f"; ctx.stroke();
    }

    const showMinor = zoom > 1.7;
    const majorAlpha = showMinor ? Math.max(0.12, 1 - (zoom - 1.7) / 1.4) : 1;
    if (majorAlpha > 0.02) {
      for (const M of DATA.majors) {
        if (hidden.has(M.id)) continue;
        drawLabel(M.label, wx(M.x), wy(M.y), 15 + Math.min(7, M.count / 500), M.color, majorAlpha, true);
      }
    }
    if (showMinor) {
      const a = Math.min(1, (zoom - 1.7) / 0.6);
      for (const m of DATA.minors) {
        if (hidden.has(m.major)) continue;
        if (m.count < 40 && zoom < 3) continue;
        drawLabel(m.label, wx(m.x), wy(m.y), 16, "#22222a", a, false);
      }
    }

    // selection rectangle while dragging
    if (selRect) {
      const x = Math.min(selRect.x0, selRect.x1), y = Math.min(selRect.y0, selRect.y1);
      const rw = Math.abs(selRect.x1 - selRect.x0), rh = Math.abs(selRect.y1 - selRect.y0);
      ctx.fillStyle = "rgba(47,111,87,0.10)";
      ctx.strokeStyle = "rgba(47,111,87,0.9)";
      ctx.lineWidth = 1.5;
      ctx.fillRect(x, y, rw, rh);
      ctx.strokeRect(x, y, rw, rh);
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

  // ---- interaction (coords relative to the canvas, so it works when embedded) ----
  const rel = (e: MouseEvent) => { const b = cv.getBoundingClientRect(); return { x: e.clientX - b.left, y: e.clientY - b.top }; };
  let drag: { x: number; y: number; tx: number; ty: number } | null = null;

  const onDown = (e: MouseEvent) => {
    const m = rel(e);
    if (selecting) { selRect = { x0: m.x, y0: m.y, x1: m.x, y1: m.y }; hideTip(); return; }
    drag = { x: m.x, y: m.y, tx: view.tx, ty: view.ty }; cv.classList.add("drag");
  };
  const onUp = () => {
    if (selecting && selRect) { finishSelection(); return; }
    drag = null; cv.classList.remove("drag");
  };
  const onLeave = () => { if (hoverIdx >= 0) { hoverIdx = -1; draw(); } hideTip(); };
  const onMove = (e: MouseEvent) => {
    if (selecting) {
      if (selRect) { const m = rel(e); selRect.x1 = m.x; selRect.y1 = m.y; draw(); }
      return;
    }
    if (drag) {
      const m = rel(e);
      if (hoverIdx >= 0) hoverIdx = -1;
      view.tx = drag.tx + (m.x - drag.x); view.ty = drag.ty - (m.y - drag.y);
      draw(); hideTip(); return;
    }
    if (e.target !== cv) { if (hoverIdx >= 0) { hoverIdx = -1; draw(); } hideTip(); return; }
    hover(e);
  };

  function finishSelection() {
    const rct = selRect!;
    selRect = null;
    const x = Math.min(rct.x0, rct.x1), y = Math.min(rct.y0, rct.y1);
    const rw = Math.abs(rct.x1 - rct.x0), rh = Math.abs(rct.y1 - rct.y0);
    const rows: SelectedPaper[] = [];
    selSet = new Set();
    if (rw > 3 && rh > 3) {
      for (let i = 0; i < P.length; i++) {
        const p = P[i];
        if (hidden.has(minorMajor[p[2]])) continue;
        const X = wx(p[0]), Y = wy(p[1]);
        if (X >= x && X <= x + rw && Y >= y && Y <= y + rh) {
          selSet.add(i);
          rows.push({
            i, paper_id: DATA.ids[i], title: DATA.titles[i], year: p[3],
            minor: minorLabel[p[2]], major: majorLabel[minorMajor[p[2]]], degree: ADJ[i].length,
          });
        }
      }
    }
    rows.sort((a, b) => b.degree - a.degree || b.year - a.year);
    selecting = false;
    cv.classList.remove("selecting");
    opts.onSelectModeChange?.(false);
    opts.onSelect?.(rows);
    draw();
  }
  const onWheel = (e: WheelEvent) => { e.preventDefault(); const m = rel(e); zoomAt(m.x, m.y, Math.exp(-e.deltaY * 0.0015)); };

  function zoomAt(sx: number, sy: number, f: number) {
    const wxp = (sx - view.tx) / view.s, wyp = (ch() - sy - view.ty) / view.s;
    view.s *= f;
    view.tx = sx - wxp * view.s;
    view.ty = ch() - sy - wyp * view.s;
    draw();
  }
  zin.onclick = () => zoomAt(cw() / 2, ch() / 2, 1.4);
  zout.onclick = () => zoomAt(cw() / 2, ch() / 2, 1 / 1.4);

  function hover(e: MouseEvent) {
    const m = rel(e);
    const r = Math.max(2.5, dotR * view.s) + 2;
    let best = -1, bd = r * r;
    for (let i = 0; i < P.length; i++) {
      const p = P[i]; if (hidden.has(minorMajor[p[2]])) continue;
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

  // ---- legend ----
  for (const M of DATA.majors) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML =
      `<span class="sw" style="background:${M.color}"></span>` +
      `<span>${esc(M.label)}</span><span class="ct">${M.count}</span>`;
    row.onclick = () => {
      if (hidden.has(M.id)) hidden.delete(M.id); else hidden.add(M.id);
      row.classList.toggle("off"); draw();
    };
    legend.appendChild(row);
  }
  sub.textContent =
    `${DATA.meta.n_papers.toLocaleString()} papers · ${DATA.meta.n_major} disease areas · ` +
    `${DATA.meta.n_minor} sub-topics · ${DATA.meta.year_min}–${DATA.meta.year_max} · Qwen3-Embedding-8B`;

  cv.addEventListener("mousedown", onDown);
  cv.addEventListener("mouseleave", onLeave);
  cv.addEventListener("wheel", onWheel, { passive: false });
  window.addEventListener("mouseup", onUp);
  window.addEventListener("mousemove", onMove);
  const ro = new ResizeObserver(() => resize());
  ro.observe(root);

  fit(); baseS = view.s; resize();

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
      selRect = null;
      cv.classList.toggle("selecting", on);
      if (on && hoverIdx >= 0) { hoverIdx = -1; }
      hideTip();
      draw();
    },
    clearSelection() {
      selSet = new Set();
      draw();
    },
  };
}
