import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Cluster, Paper } from "../types";
import {
  fitTransform,
  pointInPolygon,
  toScreen,
  type Point,
  type Transform,
} from "../lib/geometry";

interface Props {
  papers: Paper[];
  edges?: [number, number][];
  clusters: Cluster[];
  viewMode: "clusters" | "all";
  selectMode: boolean;
  isActive: (p: Paper) => boolean;
  selectedIds: Set<string>;
  onSelect: (ids: string[]) => void;
  onReset?: () => void;
}

const NEUTRAL = "#c8c8cc";

export default function MapCanvas({
  papers,
  edges = [],
  clusters,
  viewMode,
  selectMode,
  isActive,
  selectedIds,
  onSelect,
  onReset,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [transform, setTransform] = useState<Transform>({ scale: 1, tx: 0, ty: 0 });
  const [fitted, setFitted] = useState(false);
  const fitScaleRef = useRef(1); // scale at the initial fit; "zoomed in" is measured against this

  const clusterById = useMemo(() => {
    const m = new Map<string, Cluster>();
    for (const c of clusters) m.set(c.topic_id, c);
    return m;
  }, [clusters]);

  const indexById = useMemo(() => {
    const m = new Map<string, number>();
    papers.forEach((p, i) => m.set(p.paper_id, i));
    return m;
  }, [papers]);

  // adjacency (paper index -> neighbour indices) for hover highlighting
  const adjacency = useMemo(() => {
    const adj = new Map<number, number[]>();
    for (const [a, b] of edges) {
      let la = adj.get(a); if (!la) adj.set(a, (la = []));
      let lb = adj.get(b); if (!lb) adj.set(b, (lb = []));
      la.push(b); lb.push(a);
    }
    return adj;
  }, [edges]);

  // interaction refs
  const dragging = useRef(false);
  const lastPtr = useRef<Point>({ x: 0, y: 0 });
  const lasso = useRef<Point[]>([]);
  const [lassoTick, setLassoTick] = useState(0); // force redraw while drawing
  const [hover, setHover] = useState<{ paper: Paper; sx: number; sy: number } | null>(null);

  // --- sizing ---------------------------------------------------------------
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ w: Math.max(320, r.width), h: Math.max(360, r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // --- initial fit ----------------------------------------------------------
  useEffect(() => {
    if (fitted || papers.length === 0) return;
    const t = fitTransform(papers, size.w, size.h);
    fitScaleRef.current = t.scale;
    setTransform(t);
    setFitted(true);
  }, [fitted, papers, size.w, size.h]);

  const radiusFor = useCallback(
    (p: Paper) => {
      const c = p.metrics.citation_count ?? 10;
      return Math.max(2.2, Math.min(8, 2.2 + Math.sqrt(c) * 0.35)) * Math.sqrt(transform.scale);
    },
    [transform.scale]
  );

  // --- render ---------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size.w * dpr;
    canvas.height = size.h * dpr;
    canvas.style.width = `${size.w}px`;
    canvas.style.height = `${size.h}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);

    // subtle grid
    ctx.strokeStyle = "rgba(0,0,0,0.035)";
    ctx.lineWidth = 1;
    const step = 80;
    for (let x = (transform.tx % step); x < size.w; x += step) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, size.h);
      ctx.stroke();
    }
    for (let y = (transform.ty % step); y < size.h; y += step) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(size.w, y);
      ctx.stroke();
    }

    // coupling edges — faint web beneath the points (only between visible papers)
    const hoverIdx = hover ? indexById.get(hover.paper.paper_id) ?? -1 : -1;
    const visible = (p: Paper) => isActive(p) || selectedIds.has(p.paper_id);
    if (edges.length) {
      ctx.strokeStyle = "#9aa4b8";
      ctx.lineWidth = 0.8;
      ctx.globalAlpha = 0.045;
      ctx.beginPath();
      for (const [a, b] of edges) {
        if (a === hoverIdx || b === hoverIdx) continue; // hovered links drawn bright later
        const pa = papers[a], pb = papers[b];
        if (!pa || !pb || !visible(pa) || !visible(pb)) continue;
        const sa = toScreen(pa, transform), sb = toScreen(pb, transform);
        if ((sa.x < 0 && sb.x < 0) || (sa.x > size.w && sb.x > size.w) ||
            (sa.y < 0 && sb.y < 0) || (sa.y > size.h && sb.y > size.h)) continue;
        ctx.moveTo(sa.x, sa.y);
        ctx.lineTo(sb.x, sb.y);
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // points — inactive first (faint), then active, then selected on top
    const drawPoint = (p: Paper, mode: "inactive" | "active" | "selected") => {
      const s = toScreen(p, transform);
      if (s.x < -20 || s.x > size.w + 20 || s.y < -20 || s.y > size.h + 20) return;
      const isOther = p.cluster_id === "other";
      const r = radiusFor(p) * (isOther ? 0.65 : 1);
      const cluster = clusterById.get(p.cluster_id);
      let color = NEUTRAL;
      let alpha = 0.25;
      if (mode === "inactive") {
        color = NEUTRAL;
        alpha = 0.18;
      } else if (viewMode === "clusters") {
        color = cluster?.color ?? NEUTRAL;
        alpha = mode === "selected" ? 1 : isOther ? 0.4 : 0.82;
      } else {
        color = mode === "selected" ? (cluster?.color ?? "#333") : "#7a7a80";
        alpha = mode === "selected" ? 1 : 0.5;
      }
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      if (mode === "selected") {
        ctx.globalAlpha = 1;
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = "#1c1c1e";
        ctx.stroke();
      }
    };

    const active: Paper[] = [];
    const selected: Paper[] = [];
    for (const p of papers) {
      if (selectedIds.has(p.paper_id)) selected.push(p);
      else if (isActive(p)) active.push(p);
      else drawPoint(p, "inactive");
    }
    for (const p of active) drawPoint(p, "active");
    for (const p of selected) drawPoint(p, "selected");
    ctx.globalAlpha = 1;

    // cluster labels — primary label always; sublabel fades in when zoomed in.
    // Labels are decluttered greedily: larger clusters get priority, and a
    // label is skipped if its pill would overlap one already drawn. As zoom
    // spreads the centroids apart, more of the smaller labels reappear — so the
    // zoomed-out view stays clean and detail emerges on zoom in.
    if (viewMode === "clusters") {
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      // zoom ratio relative to the initial fit; sublabels ramp in from 1.4x–2.2x
      const zoom = transform.scale / (fitScaleRef.current || 1);
      const subAlpha = Math.max(0, Math.min(1, (zoom - 1.4) / 0.8));
      const LABEL_FONT = "600 13px ui-sans-serif, system-ui, -apple-system, sans-serif";
      const SUB_FONT = "500 10.5px ui-sans-serif, system-ui, -apple-system, sans-serif";
      const padX = 6;
      const bh = 18;
      const drawn: { x0: number; y0: number; x1: number; y1: number }[] = [];
      const overlaps = (r: { x0: number; y0: number; x1: number; y1: number }) =>
        drawn.some((d) => r.x0 < d.x1 && r.x1 > d.x0 && r.y0 < d.y1 && r.y1 > d.y0);
      // largest clusters first so they win the space; "other" never labels
      const ordered = [...clusters]
        .filter((c) => c.topic_id !== "other")
        .sort((a, b) => (b.paper_count ?? 0) - (a.paper_count ?? 0));
      for (const c of ordered) {
        const s = toScreen(c.centroid, transform);
        if (s.x < 0 || s.x > size.w || s.y < 0 || s.y > size.h) continue;
        const hasSub = subAlpha > 0.01 && !!c.sublabel;

        ctx.font = LABEL_FONT;
        const tw = ctx.measureText(c.label).width;
        ctx.font = SUB_FONT;
        const stw = hasSub ? ctx.measureText(c.sublabel!).width : 0;

        // combined footprint of label (+ sublabel) with a small margin
        const halfW = Math.max(tw / 2 + padX, stw / 2 + 5) + 3;
        const top = s.y - bh / 2 - 3;
        const bot = hasSub ? s.y + bh / 2 + 8 + 8 + 3 : s.y + bh / 2 + 3;
        const rect = { x0: s.x - halfW, y0: top, x1: s.x + halfW, y1: bot };
        if (overlaps(rect)) continue;
        drawn.push(rect);

        // primary label pill
        ctx.font = LABEL_FONT;
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = "rgba(255,255,255,0.82)";
        roundRect(ctx, s.x - tw / 2 - padX, s.y - bh / 2, tw + padX * 2, bh, 5);
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#26262b";
        ctx.fillText(c.label, s.x, s.y + 0.5);

        // sublabel — smaller, beneath, only once zoomed in
        if (hasSub) {
          ctx.font = SUB_FONT;
          const sy = s.y + bh / 2 + 8;
          ctx.globalAlpha = 0.7 * subAlpha;
          ctx.fillStyle = "rgba(255,255,255,0.72)";
          roundRect(ctx, s.x - stw / 2 - 5, sy - 8, stw + 10, 15, 4);
          ctx.fill();
          ctx.globalAlpha = subAlpha;
          ctx.fillStyle = "#55555c";
          ctx.fillText(c.sublabel!, s.x, sy);
        }
      }
    }

    // hovered paper's connections, drawn bright on top
    if (hoverIdx >= 0) {
      const nbrs = adjacency.get(hoverIdx);
      const src = papers[hoverIdx];
      if (nbrs && src) {
        const s0 = toScreen(src, transform);
        const col = clusterById.get(src.cluster_id)?.color ?? "#333";
        ctx.strokeStyle = col;
        ctx.lineWidth = 1.2;
        ctx.globalAlpha = 0.75;
        ctx.beginPath();
        for (const j of nbrs) {
          const pj = papers[j];
          if (!pj) continue;
          const sj = toScreen(pj, transform);
          ctx.moveTo(s0.x, s0.y);
          ctx.lineTo(sj.x, sj.y);
        }
        ctx.stroke();
        // re-dot the neighbours so they read as connected
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = col;
        for (const j of nbrs) {
          const pj = papers[j];
          if (!pj) continue;
          const sj = toScreen(pj, transform);
          ctx.beginPath();
          ctx.arc(sj.x, sj.y, Math.max(1.8, radiusFor(pj)), 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
    }

    // lasso path
    if (lasso.current.length > 1) {
      ctx.beginPath();
      ctx.moveTo(lasso.current[0].x, lasso.current[0].y);
      for (const pt of lasso.current.slice(1)) ctx.lineTo(pt.x, pt.y);
      ctx.closePath();
      ctx.fillStyle = "rgba(30, 90, 70, 0.10)";
      ctx.fill();
      ctx.setLineDash([6, 5]);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#2f6f57";
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [
    papers,
    edges,
    indexById,
    adjacency,
    clusters,
    clusterById,
    transform,
    size,
    viewMode,
    selectedIds,
    isActive,
    radiusFor,
    lassoTick,
    hover,
  ]);

  // --- pointer handlers -----------------------------------------------------
  const relPos = (e: React.PointerEvent): Point => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const findHover = useCallback(
    (pos: Point): Paper | null => {
      let best: Paper | null = null;
      let bestD = 12 * 12;
      for (const p of papers) {
        if (!isActive(p) && !selectedIds.has(p.paper_id)) continue;
        const s = toScreen(p, transform);
        const dx = s.x - pos.x;
        const dy = s.y - pos.y;
        const d = dx * dx + dy * dy;
        if (d < bestD) {
          bestD = d;
          best = p;
        }
      }
      return best;
    },
    [papers, transform, isActive, selectedIds]
  );

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture(e.pointerId);
    const pos = relPos(e);
    if (selectMode) {
      lasso.current = [pos];
      setLassoTick((t) => t + 1);
    } else {
      dragging.current = true;
      lastPtr.current = pos;
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const pos = relPos(e);
    if (selectMode && lasso.current.length > 0) {
      lasso.current.push(pos);
      setLassoTick((t) => t + 1);
      return;
    }
    if (dragging.current) {
      const dx = pos.x - lastPtr.current.x;
      const dy = pos.y - lastPtr.current.y;
      lastPtr.current = pos;
      setTransform((t) => ({ ...t, tx: t.tx + dx, ty: t.ty + dy }));
      return;
    }
    // hover
    const p = findHover(pos);
    setHover(p ? { paper: p, sx: pos.x, sy: pos.y } : null);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (selectMode && lasso.current.length > 2) {
      const poly = lasso.current;
      const ids: string[] = [];
      for (const p of papers) {
        if (!isActive(p)) continue;
        if (pointInPolygon(toScreen(p, transform), poly)) ids.push(p.paper_id);
      }
      onSelect(ids);
    }
    lasso.current = [];
    setLassoTick((t) => t + 1);
    dragging.current = false;
    try {
      (e.target as Element).releasePointerCapture(e.pointerId);
    } catch {
      /* noop */
    }
  };

  // --- wheel zoom -----------------------------------------------------------
  // Registered natively with { passive: false } so preventDefault() actually
  // stops the page from scrolling / zooming. React's onWheel is passive, so it
  // can't do this — on macOS the whole page zooms (pinch fires ctrlKey wheel
  // events) unless we intercept here.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      // macOS trackpad pinch sends larger deltas with ctrlKey; damp it a touch.
      const intensity = e.ctrlKey ? 0.01 : 0.0015;
      const factor = Math.exp(-e.deltaY * intensity);
      setTransform((t) => {
        const scale = Math.max(0.15, Math.min(20, t.scale * factor));
        const k = scale / t.scale;
        return { scale, tx: px - (px - t.tx) * k, ty: py - (py - t.ty) * k };
      });
    };
    canvas.addEventListener("wheel", handleWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", handleWheel);
  }, []);

  const resetView = () => {
    const t = fitTransform(papers, size.w, size.h);
    fitScaleRef.current = t.scale;
    setTransform(t);
    onReset?.();
  };

  return (
    <div ref={wrapRef} className="map-wrap">
      <canvas
        ref={canvasRef}
        className={selectMode ? "map-canvas selecting" : "map-canvas"}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      />
      <button className="reset-view" onClick={resetView} title="Reset view">
        Reset view
      </button>
      {hover && !dragging.current && lasso.current.length === 0 && (
        <div
          className="tooltip"
          style={{
            left: Math.min(hover.sx + 14, size.w - 240),
            top: Math.max(hover.sy - 10, 8),
          }}
        >
          <div className="tooltip-title">{hover.paper.title}</div>
          <div className="tooltip-meta">
            {(clusterById.get(hover.paper.cluster_id)?.label ?? hover.paper.cluster_id)} ·{" "}
            {hover.paper.year} · {hover.paper.metrics.citation_count ?? 0} cites
          </div>
        </div>
      )}
    </div>
  );
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
