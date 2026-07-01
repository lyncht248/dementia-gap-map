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
  clusters: Cluster[];
  viewMode: "clusters" | "all";
  selectMode: boolean;
  isActive: (p: Paper) => boolean;
  selectedIds: Set<string>;
  onSelect: (ids: string[]) => void;
}

const NEUTRAL = "#c8c8cc";

export default function MapCanvas({
  papers,
  clusters,
  viewMode,
  selectMode,
  isActive,
  selectedIds,
  onSelect,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 800, h: 560 });
  const [transform, setTransform] = useState<Transform>({ scale: 1, tx: 0, ty: 0 });
  const [fitted, setFitted] = useState(false);

  const clusterById = useMemo(() => {
    const m = new Map<string, Cluster>();
    for (const c of clusters) m.set(c.topic_id, c);
    return m;
  }, [clusters]);

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
    setTransform(fitTransform(papers, size.w, size.h));
    setFitted(true);
  }, [fitted, papers, size.w, size.h]);

  const radiusFor = useCallback(
    (p: Paper) => {
      const c = p.metrics.citation_count ?? 10;
      return Math.max(1.6, Math.min(7, 1.6 + Math.sqrt(c) * 0.35)) * Math.sqrt(transform.scale);
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

    // points — inactive first (faint), then active, then selected on top
    const drawPoint = (p: Paper, mode: "inactive" | "active" | "selected") => {
      const s = toScreen(p, transform);
      if (s.x < -20 || s.x > size.w + 20 || s.y < -20 || s.y > size.h + 20) return;
      const r = radiusFor(p);
      const cluster = clusterById.get(p.cluster_id);
      let color = NEUTRAL;
      let alpha = 0.25;
      if (mode === "inactive") {
        color = NEUTRAL;
        alpha = 0.18;
      } else if (viewMode === "clusters") {
        color = cluster?.color ?? NEUTRAL;
        alpha = mode === "selected" ? 1 : 0.82;
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

    // cluster labels
    if (viewMode === "clusters") {
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = "600 13px ui-sans-serif, system-ui, -apple-system, sans-serif";
      for (const c of clusters) {
        const s = toScreen(c.centroid, transform);
        if (s.x < 0 || s.x > size.w || s.y < 0 || s.y > size.h) continue;
        const text = c.label;
        const tw = ctx.measureText(text).width;
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = "rgba(255,255,255,0.82)";
        const padX = 6;
        const bh = 18;
        roundRect(ctx, s.x - tw / 2 - padX, s.y - bh / 2, tw + padX * 2, bh, 5);
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#26262b";
        ctx.fillText(text, s.x, s.y + 0.5);
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
    clusters,
    clusterById,
    transform,
    size,
    viewMode,
    selectedIds,
    isActive,
    radiusFor,
    lassoTick,
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

  const onWheel = (e: React.WheelEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const factor = Math.exp(-e.deltaY * 0.0015);
    setTransform((t) => {
      const scale = Math.max(0.15, Math.min(20, t.scale * factor));
      const k = scale / t.scale;
      return { scale, tx: px - (px - t.tx) * k, ty: py - (py - t.ty) * k };
    });
  };

  const resetView = () =>
    setTransform(fitTransform(papers, size.w, size.h));

  return (
    <div ref={wrapRef} className="map-wrap">
      <canvas
        ref={canvasRef}
        className={selectMode ? "map-canvas selecting" : "map-canvas"}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onWheel={onWheel}
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
