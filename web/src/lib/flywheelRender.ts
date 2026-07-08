// Framework-agnostic renderer for the "flywheel" (development-pipeline) view.
//
// An 8 x 5 grid — the 8 mechanistic Alzheimer's-cure hypotheses (rows, ranked
// least-gap-first) x the 5 pipeline stages Research -> Genetics -> Models ->
// Trials -> Results (columns). Each cell packs the typed dots that populate that
// hypothesis+stage (papers / genes / model-validated genes / trials / trials
// with results). Hovering a dot lights up its lineage across stages — a paper's
// genes, their model validation and the trials that target them; a trial's genes
// and the research behind them — the way the atlas traces citations.
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

export interface FlywheelOptions {
  onReady?: (info: { hypotheses: FlyHypothesis[] }) => void;
}
export interface FlywheelHandle {
  destroy: () => void;
}

type Placement = { node: FlyNode; row: number; col: number; x: number; y: number; color: string };

const GREY = "#d6d6dc";
const INK = "#22222a";
const MUTED = "#6b6b70";

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

  const nodeById = new Map<string, FlyNode>();
  for (const n of DATA.nodes) nodeById.set(n.id, n);

  // adjacency for lineage tracing
  const adj = new Map<string, string[]>();
  for (const [a, b] of DATA.edges) {
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
    adj.get(a)!.push(b);
    adj.get(b)!.push(a);
  }

  // ---- layout geometry -------------------------------------------------------
  const LEFT = 128, TOP = 96, PADR = 14, PADB = 16;
  const CELL_PAD = 7;
  let placements: Placement[] = [];
  // placements grouped by cell for packing + by node for lineage
  const placementsByNode = new Map<string, Placement[]>();

  function layout() {
    const w = cv.clientWidth, h = cv.clientHeight;
    const gridW = Math.max(10, w - LEFT - PADR);
    const gridH = Math.max(10, h - TOP - PADB);
    const colW = gridW / stages.length;
    const rowH = gridH / hyps.length;

    // group nodes into (row, col) cells (papers are multi-membership -> multiple rows)
    const cells = new Map<string, FlyNode[]>();
    for (const n of DATA.nodes) {
      const c = colOf[n.stage];
      if (c == null) continue;
      for (const hid of n.hyps) {
        const r = rowOf[hid];
        if (r == null) continue;
        const k = `${r},${c}`;
        if (!cells.has(k)) cells.set(k, []);
        cells.get(k)!.push(n);
      }
    }
    // choose one dot pitch that makes the densest cell fit its box
    const innerW = colW - 2 * CELL_PAD, innerH = rowH - 2 * CELL_PAD - 12; // 12: cell count label
    let maxN = 1;
    for (const arr of cells.values()) maxN = Math.max(maxN, arr.length);
    // solve for pitch p so that ceil(N/floor(innerW/p)) * p <= innerH (approx via area)
    let pitch = Math.sqrt((innerW * innerH) / maxN);
    pitch = Math.max(2.2, Math.min(7, pitch));
    const dotR = Math.max(1, pitch * 0.36);

    placements = [];
    placementsByNode.clear();
    for (const [k, arr] of cells) {
      const [r, c] = k.split(",").map(Number);
      const cx0 = LEFT + c * colW + CELL_PAD;
      const cy0 = TOP + r * rowH + CELL_PAD + 12;
      const cols = Math.max(1, Math.floor(innerW / pitch));
      // stable order: by kind then label so the packing is deterministic
      arr.sort((a, b) => a.id.localeCompare(b.id));
      arr.forEach((n, i) => {
        const gx = i % cols, gy = Math.floor(i / cols);
        const p: Placement = {
          node: n, row: r, col: c,
          x: cx0 + gx * pitch + pitch / 2,
          y: cy0 + gy * pitch + pitch / 2,
          color: colorOf[hyps[r].id],
        };
        placements.push(p);
        if (!placementsByNode.has(n.id)) placementsByNode.set(n.id, []);
        placementsByNode.get(n.id)!.push(p);
      });
    }
    return { colW, rowH, dotR, gridW, gridH };
  }

  let geom = { colW: 0, rowH: 0, dotR: 1.5, gridW: 0, gridH: 0 };

  // ---- lineage (hover) -------------------------------------------------------
  let hoverId: string | null = null;
  // connected component of a node, both directions, bounded so a hub gene doesn't
  // light up the whole map.
  function lineage(id: string): Set<string> {
    const seen = new Set<string>([id]);
    let frontier = [id];
    for (let depth = 0; depth < 6 && frontier.length; depth++) {
      const next: string[] = [];
      for (const u of frontier) {
        for (const v of adj.get(u) || []) {
          if (!seen.has(v)) { seen.add(v); next.push(v); }
        }
      }
      frontier = next;
      if (seen.size > 600) break;
    }
    return seen;
  }

  function draw() {
    const w = cv.clientWidth, h = cv.clientHeight;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const { colW, rowH, dotR } = geom;

    // column headers
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "700 12px -apple-system,Segoe UI,Roboto,sans-serif";
    stages.forEach((s, c) => {
      ctx.fillStyle = MUTED;
      ctx.fillText(s.label.toUpperCase(), LEFT + c * colW + colW / 2, TOP - 24);
      if (c > 0) {
        ctx.strokeStyle = "#efefec"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(LEFT + c * colW, TOP - 12); ctx.lineTo(LEFT + c * colW, TOP + hyps.length * rowH); ctx.stroke();
      }
      // arrow between columns (flow toward a cure)
      if (c < stages.length - 1) {
        const ax = LEFT + (c + 1) * colW - 4;
        ctx.fillStyle = "#cfcfca";
        ctx.beginPath(); ctx.moveTo(ax - 4, TOP - 27); ctx.lineTo(ax, TOP - 24); ctx.lineTo(ax - 4, TOP - 21); ctx.fill();
      }
    });

    // row labels + separators
    ctx.textAlign = "left";
    hyps.forEach((hy, r) => {
      const cy = TOP + r * rowH + rowH / 2;
      ctx.fillStyle = hy.color;
      ctx.beginPath(); ctx.arc(14, cy - 7, 5, 0, 6.2832); ctx.fill();
      ctx.fillStyle = INK; ctx.font = "700 12.5px -apple-system,Segoe UI,Roboto,sans-serif";
      ctx.fillText(hy.short, 26, cy - 7);
      ctx.fillStyle = MUTED; ctx.font = "400 10.5px -apple-system,Segoe UI,Roboto,sans-serif";
      ctx.fillText(`gap ${hy.translation_gap?.toFixed(2)}`, 26, cy + 7);
      if (r > 0) {
        ctx.strokeStyle = "#eeeeeb"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(8, TOP + r * rowH); ctx.lineTo(cv.clientWidth - PADR, TOP + r * rowH); ctx.stroke();
      }
    });

    // per-cell count labels (top-left of each cell)
    ctx.font = "600 10px -apple-system,Segoe UI,Roboto,sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    const cellCount = new Map<string, number>();
    for (const p of placements) {
      const k = `${p.row},${p.col}`;
      cellCount.set(k, (cellCount.get(k) || 0) + 1);
    }
    for (const [k, n] of cellCount) {
      const [r, c] = k.split(",").map(Number);
      ctx.fillStyle = "#a6a6a2";
      ctx.fillText(String(n), LEFT + c * colW + CELL_PAD, TOP + r * rowH + CELL_PAD + 8);
    }

    // lineage highlight set
    const lit = hoverId ? lineage(hoverId) : null;

    // dots
    ctx.textBaseline = "middle";
    for (const p of placements) {
      const on = !lit || lit.has(p.node.id);
      ctx.globalAlpha = on ? 1 : 0.12;
      ctx.beginPath(); ctx.arc(p.x, p.y, dotR, 0, 6.2832);
      ctx.fillStyle = lit && on ? p.color : (lit ? GREY : p.color);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // lineage connecting lines (between directly-linked lit nodes, nearest placements)
    if (lit && hoverId) {
      ctx.strokeStyle = "rgba(40,40,54,.45)"; ctx.lineWidth = 1;
      let drawn = 0;
      const litArr = [...lit];
      for (const u of litArr) {
        for (const v of adj.get(u) || []) {
          if (!lit.has(v) || u > v) continue; // each undirected edge once
          const pu = placementsByNode.get(u), pv = placementsByNode.get(v);
          if (!pu || !pv) continue;
          // nearest placement pair
          let best: [Placement, Placement] | null = null, bd = Infinity;
          for (const a of pu) for (const b of pv) {
            const d = (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
            if (d < bd) { bd = d; best = [a, b]; }
          }
          if (best) {
            ctx.beginPath(); ctx.moveTo(best[0].x, best[0].y); ctx.lineTo(best[1].x, best[1].y); ctx.stroke();
            if (++drawn > 400) break;
          }
        }
        if (drawn > 400) break;
      }
      // re-draw the lit dots on top of the lines
      for (const p of placements) {
        if (!lit.has(p.node.id)) continue;
        ctx.beginPath(); ctx.arc(p.x, p.y, dotR + 0.4, 0, 6.2832);
        ctx.fillStyle = p.color; ctx.fill();
      }
    }
  }

  // ---- interaction -----------------------------------------------------------
  function pick(mx: number, my: number): Placement | null {
    const rr = Math.max(3.2, geom.dotR + 2.2);
    let best: Placement | null = null, bd = rr * rr;
    for (const p of placements) {
      const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
      if (d < bd) { bd = d; best = p; }
    }
    return best;
  }
  function rel(e: MouseEvent) {
    const b = cv.getBoundingClientRect();
    return { x: e.clientX - b.left, y: e.clientY - b.top };
  }
  const KIND_LABEL: Record<string, string> = { paper: "Paper", gene: "Gene", trial: "Trial" };
  function stageLabel(id: string) { return stages.find((s) => s.id === id)?.label ?? id; }
  function onMove(e: MouseEvent) {
    const m = rel(e);
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
        ? `<div class="m">${n.stage === "models" ? "genetic + model-validated" : "genetic evidence"}</div>`
        : `<div class="m">${n.year ?? ""}</div>`;
    tip.innerHTML =
      `<div class="t">${esc(n.label)}</div>` +
      `<div class="m">${KIND_LABEL[n.kind]} · ${esc(stageLabel(n.stage))} · ${esc(hyps[p.row].short)}</div>` +
      extra + (n.url ? `<div class="m" style="opacity:.7">click to open ↗</div>` : "");
    tip.style.opacity = "1";
    const b = cv.getBoundingClientRect();
    tip.style.left = Math.min(m.x + 14, b.width - 300) + "px";
    tip.style.top = Math.min(m.y + 14, b.height - 96) + "px";
  }
  function onLeave() { if (hoverId) { hoverId = null; draw(); } tip.style.opacity = "0"; }
  function onClick(e: MouseEvent) {
    const m = rel(e);
    const p = pick(m.x, m.y);
    if (p?.node.url) window.open(p.node.url, "_blank", "noopener");
  }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = cv.clientWidth * DPR;
    cv.height = cv.clientHeight * DPR;
    geom = layout();
    draw();
  }

  cv.addEventListener("mousemove", onMove);
  cv.addEventListener("mouseleave", onLeave);
  cv.addEventListener("click", onClick);
  const ro = new ResizeObserver(() => resize());
  ro.observe(root);
  resize();
  opts.onReady?.({ hypotheses: hyps });

  return {
    destroy() {
      ro.disconnect();
      cv.removeEventListener("mousemove", onMove);
      cv.removeEventListener("mouseleave", onLeave);
      cv.removeEventListener("click", onClick);
      root.innerHTML = "";
      root.classList.remove("fly-root");
    },
  };
}
