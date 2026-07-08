// Framework-agnostic renderer for the "flywheel" (development-pipeline) view.
//
// An 8-row x 5-column board — the 8 mechanistic Alzheimer's-cure hypotheses
// (rows, ranked least-gap-first) x the pipeline stages Research -> Genetics ->
// Models -> Trials -> Results (columns). Each cell holds an approximately
// circular cluster of typed dots (cluster size = number of items). Hovering a
// dot draws its lineage across stages (a paper's genes and the trials targeting
// them; a trial's target gene and the research behind it).
//
// Data: scripts/build_flywheel.py -> web/public/atlas/flywheel.json.

export interface FlyNode {
  id: string;
  kind: "paper" | "gene" | "trial";
  stage: string;
  hyps: string[];
  label: string;
  year?: number;
  url?: string;
  phase?: string;
  has_results?: boolean;
  drugs?: string[];
  targets?: string[];
}
export interface FlyHypothesis {
  id: string; label: string; short: string; color: string; statement: string;
  translation_gap: number | null; combined_support: number | null; clinical_translation: number | null;
}
export interface FlyData {
  stages: { id: string; label: string }[];
  hypotheses: FlyHypothesis[];
  nodes: FlyNode[];
  edges: [string, string][];
}

export interface FlywheelOptions { onReady?: () => void }
export interface FlywheelHandle { destroy: () => void }

const INK = "#22222a";
const MUTED = "#6b6b70";
const GOLDEN = Math.PI * (3 - Math.sqrt(5)); // ~2.39996 rad

// what one dot means in each stage (info-on-hover over the column header)
const STAGE_INFO: Record<string, string> = {
  research: "One dot = a paper in the corpus that studies one of this hypothesis's genes.",
  genetics: "One dot = a gene with human genetic (GWAS) evidence for this mechanism — the associated target.",
  models: "One dot = one of those genes that also has functional / animal-model validation.",
  trials: "One dot = a clinical trial pursuing this mechanism (click a dot to open it on ClinicalTrials.gov).",
  results: "One dot = one of those trials that has posted results.",
};

function esc(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));
}

export function mountFlywheel(root: HTMLElement, DATA: FlyData, opts: FlywheelOptions = {}): FlywheelHandle {
  root.classList.add("fly-root");
  root.innerHTML = "";
  const cv = document.createElement("canvas");
  cv.className = "fly-canvas";
  const tip = document.createElement("div");
  tip.className = "atlas-tip";
  root.append(cv, tip);
  const ctx = cv.getContext("2d")!;
  let DPR = Math.min(window.devicePixelRatio || 1, 2);

  const stages = DATA.stages;
  const hyps = DATA.hypotheses;
  const rowOf: Record<string, number> = {};
  hyps.forEach((h, i) => (rowOf[h.id] = i));
  const colOf: Record<string, number> = {};
  stages.forEach((s, i) => (colOf[s.id] = i));
  const colorOf: Record<string, string> = {};
  hyps.forEach((h) => (colorOf[h.id] = h.color));

  // adjacency for lineage tracing
  const adj = new Map<string, string[]>();
  for (const [a, b] of DATA.edges) {
    (adj.get(a) ?? adj.set(a, []).get(a)!).push(b);
    (adj.get(b) ?? adj.set(b, []).get(b)!).push(a);
  }

  // ---- geometry --------------------------------------------------------------
  const LEFT = 196, TOP = 100, PADR = 16, PADB = 18, CELL_PAD = 8;
  type P = { node: FlyNode; row: number; col: number; x: number; y: number; sx: number; sy: number; color: string };
  let placements: P[] = [];
  const byNode = new Map<string, P[]>();
  const cellCount = new Map<string, number>();
  let dotR = 1.5;
  let rowH = 0, colW = 0;

  function layout() {
    const w = cv.clientWidth, h = cv.clientHeight;
    colW = Math.max(20, (w - LEFT - PADR) / stages.length);
    rowH = Math.max(20, (h - TOP - PADB) / hyps.length);
    const Rc = Math.max(6, Math.min(colW, rowH) / 2 - CELL_PAD);

    // group nodes into cells (papers are multi-membership -> multiple rows)
    const cells = new Map<string, FlyNode[]>();
    for (const n of DATA.nodes) {
      const c = colOf[n.stage];
      if (c == null) continue;
      for (const hid of n.hyps) {
        const r = rowOf[hid];
        if (r == null) continue;
        const k = `${r},${c}`;
        (cells.get(k) ?? cells.set(k, []).get(k)!).push(n);
      }
    }
    let maxN = 1;
    for (const arr of cells.values()) maxN = Math.max(maxN, arr.length);
    // phyllotaxis scale so the densest cluster just fills its cell; dot radius
    // sized to that, floored so sparse cells stay visible / hoverable.
    const c0 = Rc / Math.sqrt(maxN);
    dotR = Math.max(1.6, Math.min(4, c0 * 0.62));

    placements = [];
    byNode.clear();
    cellCount.clear();
    for (const [k, arr] of cells) {
      const [r, cCol] = k.split(",").map(Number);
      const cx = LEFT + cCol * colW + colW / 2;
      const cy = TOP + r * rowH + rowH / 2;
      cellCount.set(k, arr.length);
      arr.sort((a, b) => a.id.localeCompare(b.id));
      arr.forEach((n, i) => {
        const rad = c0 * Math.sqrt(i + 0.5);
        const th = (i + 0.5) * GOLDEN;
        const x = cx + rad * Math.cos(th);
        const y = cy + rad * Math.sin(th);
        const p: P = { node: n, row: r, col: cCol, x, y, sx: LEFT - 40, sy: cy, color: colorOf[hyps[r].id] };
        placements.push(p);
        (byNode.get(n.id) ?? byNode.set(n.id, []).get(n.id)!).push(p);
      });
    }
  }

  // ---- lineage (hover) -------------------------------------------------------
  let hoverId: string | null = null;
  function lineage(id: string): Set<string> {
    const seen = new Set<string>([id]);
    let frontier = [id];
    for (let depth = 0; depth < 4 && frontier.length; depth++) {
      const next: string[] = [];
      for (const u of frontier) for (const v of adj.get(u) || []) if (!seen.has(v)) { seen.add(v); next.push(v); }
      frontier = next;
      if (seen.size > 450) break;
    }
    return seen;
  }

  // ---- animation: research dots fly out of the row; the rest fade in ---------
  let animStart = 0;
  const DUR = 950;
  let raf = 0;
  const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

  function draw(now = performance.now()) {
    const w = cv.clientWidth, h = cv.clientHeight;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const t = Math.min(1, (now - animStart) / DUR);
    const fade = easeOut(Math.min(1, Math.max(0, (t - 0.25) / 0.75))); // non-research fade, delayed

    // column headers + flow arrows + separators
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    stages.forEach((s, c) => {
      const hx = LEFT + c * colW + colW / 2;
      ctx.font = "700 12px -apple-system,Segoe UI,Roboto,sans-serif";
      ctx.fillStyle = MUTED;
      ctx.fillText(s.label.toUpperCase() + "  ⓘ", hx, TOP - 30);
      if (c > 0) {
        ctx.strokeStyle = "#f0f0ed"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(LEFT + c * colW, TOP - 14); ctx.lineTo(LEFT + c * colW, TOP + hyps.length * rowH); ctx.stroke();
        const ax = LEFT + c * colW;
        ctx.fillStyle = "#d3d3ce";
        ctx.beginPath(); ctx.moveTo(ax - 5, TOP - 33); ctx.lineTo(ax, TOP - 30); ctx.lineTo(ax - 5, TOP - 27); ctx.fill();
      }
    });

    // row labels (fuller description, wrapped) + gap + separators
    ctx.textAlign = "left";
    hyps.forEach((hy, r) => {
      const cy = TOP + r * rowH + rowH / 2;
      ctx.fillStyle = hy.color;
      ctx.beginPath(); ctx.arc(14, cy - 12, 5, 0, 6.2832); ctx.fill();
      ctx.fillStyle = INK; ctx.font = "700 12.5px -apple-system,Segoe UI,Roboto,sans-serif";
      const lines = wrap(hy.label, LEFT - 30);
      lines.slice(0, 2).forEach((ln, i) => ctx.fillText(ln, 26, cy - 12 + i * 15));
      ctx.fillStyle = MUTED; ctx.font = "400 10.5px -apple-system,Segoe UI,Roboto,sans-serif";
      ctx.fillText(`gap ${hy.translation_gap?.toFixed(2)} · hover for detail`, 26, cy - 12 + Math.min(2, lines.length) * 15 + 3);
      if (r > 0) {
        ctx.strokeStyle = "#efefec"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(8, TOP + r * rowH); ctx.lineTo(cv.clientWidth - PADR, TOP + r * rowH); ctx.stroke();
      }
    });

    // per-cell count (top-left of each cell)
    ctx.font = "600 10px -apple-system,Segoe UI,Roboto,sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    ctx.fillStyle = "#adada8";
    for (const [k, n] of cellCount) {
      const [r, c] = k.split(",").map(Number);
      ctx.fillText(String(n), LEFT + c * colW + CELL_PAD, TOP + r * rowH + CELL_PAD + 8);
    }

    // dots (all visible always; hover only adds connectors)
    ctx.textBaseline = "middle";
    for (const p of placements) {
      const research = p.col === 0;
      let x = p.x, y = p.y, a = 1;
      if (t < 1) {
        if (research) {
          const tt = easeOut(Math.min(1, t * 1.15));
          x = p.sx + (p.x - p.sx) * tt;
          y = p.sy + (p.y - p.sy) * tt;
          a = 0.25 + 0.75 * tt;
        } else {
          a = fade;
        }
      }
      ctx.globalAlpha = a;
      ctx.beginPath(); ctx.arc(x, y, dotR, 0, 6.2832);
      ctx.fillStyle = p.color; ctx.fill();
    }
    ctx.globalAlpha = 1;

    // lineage connectors (no dimming — connections only)
    if (t >= 1 && hoverId) {
      const lit = lineage(hoverId);
      ctx.strokeStyle = "rgba(34,34,42,.5)"; ctx.lineWidth = 1;
      let drawn = 0;
      for (const u of lit) {
        for (const v of adj.get(u) || []) {
          if (!lit.has(v) || u > v) continue;
          const pu = byNode.get(u), pv = byNode.get(v);
          if (!pu || !pv) continue;
          let best: [P, P] | null = null, bd = Infinity;
          for (const a of pu) for (const b of pv) { const d = (a.x - b.x) ** 2 + (a.y - b.y) ** 2; if (d < bd) { bd = d; best = [a, b]; } }
          if (best) { ctx.beginPath(); ctx.moveTo(best[0].x, best[0].y); ctx.lineTo(best[1].x, best[1].y); ctx.stroke(); if (++drawn > 500) break; }
        }
        if (drawn > 500) break;
      }
      // emphasise the lit dots + a ring on the hovered one
      for (const p of placements) {
        if (!lit.has(p.node.id)) continue;
        ctx.beginPath(); ctx.arc(p.x, p.y, dotR + 0.5, 0, 6.2832); ctx.fillStyle = p.color; ctx.fill();
      }
      const hp = byNode.get(hoverId)?.[0];
      if (hp) { ctx.beginPath(); ctx.arc(hp.x, hp.y, dotR + 3, 0, 6.2832); ctx.lineWidth = 2; ctx.strokeStyle = "#1b1b1f"; ctx.stroke(); }
    }

    if (t < 1) raf = requestAnimationFrame(() => draw());
  }

  // wrap text to a max pixel width (measured with the current 12.5px label font)
  function wrap(text: string, maxW: number): string[] {
    ctx.font = "700 12.5px -apple-system,Segoe UI,Roboto,sans-serif";
    const words = text.split(" ");
    const lines: string[] = [];
    let cur = "";
    for (const wd of words) {
      const test = cur ? cur + " " + wd : wd;
      if (ctx.measureText(test).width > maxW && cur) { lines.push(cur); cur = wd; }
      else cur = test;
    }
    if (cur) lines.push(cur);
    return lines;
  }

  // ---- interaction -----------------------------------------------------------
  function pick(mx: number, my: number): P | null {
    const rr = Math.max(3.5, dotR + 2.5);
    let best: P | null = null, bd = rr * rr;
    for (const p of placements) { const d = (p.x - mx) ** 2 + (p.y - my) ** 2; if (d < bd) { bd = d; best = p; } }
    return best;
  }
  const rel = (e: MouseEvent) => { const b = cv.getBoundingClientRect(); return { x: e.clientX - b.left, y: e.clientY - b.top }; };
  const KIND: Record<string, string> = { paper: "Paper", gene: "Gene", trial: "Trial" };
  const stageLabel = (id: string) => stages.find((s) => s.id === id)?.label ?? id;
  function showTip(html: string, x: number, y: number) {
    tip.innerHTML = html; tip.style.opacity = "1";
    const b = cv.getBoundingClientRect();
    tip.style.left = Math.min(x + 14, b.width - 320) + "px";
    tip.style.top = Math.min(y + 14, b.height - 110) + "px";
  }
  function onMove(e: MouseEvent) {
    const m = rel(e);
    // column header info
    if (m.y < TOP - 14 && m.y > TOP - 46 && m.x > LEFT) {
      const c = Math.floor((m.x - LEFT) / colW);
      const s = stages[c];
      if (s) { hoverId = null; draw(); cv.style.cursor = "help"; showTip(`<div class="t">${esc(s.label)}</div><div class="m">${esc(STAGE_INFO[s.id] || "")}</div>`, m.x, m.y); return; }
    }
    // row label info
    if (m.x < LEFT && m.y > TOP) {
      const r = Math.floor((m.y - TOP) / rowH);
      const hy = hyps[r];
      if (hy) { hoverId = null; draw(); cv.style.cursor = "help";
        showTip(`<div class="t">${esc(hy.label)}</div><div class="m">${esc(hy.statement)}</div>` +
          `<div class="m" style="opacity:.75">combined support ${hy.combined_support?.toFixed(2)} · clinical translation ${Math.round((hy.clinical_translation ?? 0) * 100)}% · gap ${hy.translation_gap?.toFixed(2)}</div>`, m.x, m.y);
        return; }
    }
    const p = pick(m.x, m.y);
    const id = p?.node.id ?? null;
    if (id !== hoverId) { hoverId = id; draw(); }
    if (!p) { tip.style.opacity = "0"; cv.style.cursor = "default"; return; }
    const n = p.node;
    cv.style.cursor = n.url ? "pointer" : "default";
    const extra =
      n.kind === "trial"
        ? `<div class="m">${n.phase && n.phase !== "NA" ? n.phase + " · " : ""}${n.has_results ? "has results" : "no results yet"}${n.targets?.length ? " · targets " + esc(n.targets.slice(0, 3).join(", ")) : ""}</div>`
        : n.kind === "gene"
        ? `<div class="m">${n.stage === "models" ? "genetic + model-validated target" : "genetically-supported target"}</div>`
        : `<div class="m">${n.year ?? ""}</div>`;
    showTip(
      `<div class="t">${esc(n.label)}</div>` +
      `<div class="m">${KIND[n.kind]} · ${esc(stageLabel(n.stage))} · ${esc(hyps[p.row].label)}</div>` +
      extra + (n.url ? `<div class="m" style="opacity:.7">click to open ↗</div>` : ""),
      m.x, m.y
    );
  }
  function onLeave() { if (hoverId) { hoverId = null; draw(); } tip.style.opacity = "0"; }
  function onClick(e: MouseEvent) { const p = pick(rel(e).x, rel(e).y); if (p?.node.url) window.open(p.node.url, "_blank", "noopener"); }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = cv.clientWidth * DPR; cv.height = cv.clientHeight * DPR;
    layout();
    if (raf) cancelAnimationFrame(raf);
    draw();
  }

  cv.addEventListener("mousemove", onMove);
  cv.addEventListener("mouseleave", onLeave);
  cv.addEventListener("click", onClick);
  const ro = new ResizeObserver(() => resize());
  ro.observe(root);
  animStart = performance.now();
  resize();
  opts.onReady?.();

  return {
    destroy() {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      cv.removeEventListener("mousemove", onMove);
      cv.removeEventListener("mouseleave", onLeave);
      cv.removeEventListener("click", onClick);
      root.innerHTML = "";
      root.classList.remove("fly-root");
    },
  };
}
