import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MapData, Paper } from "./types";
import { loadMapData } from "./lib/data";
import { warmupDuckDb } from "./lib/duckdb";
import MapCanvas from "./components/MapCanvas";
import NewsFeed from "./components/NewsFeed";
import AgentPanel from "./components/AgentPanel";
import { createController } from "./agent/controller";
import type { MapHandle } from "./agent/types";

const EMPTY: MapData = { clusters: [], papers: [], edges: [] };

export default function App() {
  const [data, setData] = useState<MapData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [selectMode, setSelectMode] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [highlightedIds, setHighlightedIds] = useState<Set<string>>(new Set());

  const [activeGroups, setActiveGroups] = useState<Set<string>>(new Set());
  const [yearRange, setYearRange] = useState<[number, number]>([2000, 2100]);

  // Split layout
  const [agentWidth, setAgentWidth] = useState(400);
  const [dragging, setDragging] = useState(false);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapHandle>(null);

  useEffect(() => {
    warmupDuckDb();
    loadMapData()
      .then((d) => {
        setData(d);
        setActiveGroups(new Set(d.clusters.map((c) => c.pathway_group)));
        const years = d.papers.map((p) => p.year);
        setYearRange([Math.min(...years), Math.max(...years)]);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // Refs so the (stable) controller always reads current state.
  const dataRef = useRef<MapData | null>(null);
  dataRef.current = data;
  const selectedRef = useRef(selectedIds);
  selectedRef.current = selectedIds;
  const highlightedRef = useRef(highlightedIds);
  highlightedRef.current = highlightedIds;
  const groupsRef = useRef(activeGroups);
  groupsRef.current = activeGroups;
  const yearRef = useRef(yearRange);
  yearRef.current = yearRange;

  const controller = useMemo(
    () =>
      createController({
        getData: () => dataRef.current ?? EMPTY,
        getSelectedIds: () => selectedRef.current,
        getHighlightedIds: () => highlightedRef.current,
        getActiveGroups: () => groupsRef.current,
        getYearRange: () => yearRef.current,
        setSelectedIds,
        setHighlightedIds,
        setActiveGroups,
        setYearRange,
        map: () => mapRef.current,
      }),
    []
  );

  // Dev-only debug handle: drive the map from the console (window.mapAgent).
  useEffect(() => {
    if (import.meta.env.DEV) {
      (window as unknown as { mapAgent?: typeof controller }).mapAgent = controller;
    }
  }, [controller]);

  // Divider drag
  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      const rect = workspaceRef.current?.getBoundingClientRect();
      const left = rect?.left ?? 0;
      const w = e.clientX - left;
      const max = (rect?.width ?? 1200) - 360;
      setAgentWidth(Math.max(300, Math.min(Math.max(300, max), w)));
    };
    const onUp = () => setDragging(false);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [dragging]);

  const yearBounds = useMemo<[number, number]>(() => {
    if (!data) return [2000, 2026];
    const years = data.papers.map((p) => p.year);
    return [Math.min(...years), Math.max(...years)];
  }, [data]);

  const pathwayGroups = useMemo(() => {
    if (!data) return [];
    const seen = new Map<string, string>();
    for (const c of data.clusters) if (!seen.has(c.pathway_group)) seen.set(c.pathway_group, c.color);
    return [...seen.entries()];
  }, [data]);

  const isActive = useCallback(
    (p: Paper) =>
      activeGroups.has(p.pathway_group) && p.year >= yearRange[0] && p.year <= yearRange[1],
    [activeGroups, yearRange]
  );

  const selected = useMemo(
    () => (data ? data.papers.filter((p) => selectedIds.has(p.paper_id)) : []),
    [data, selectedIds]
  );

  const resetAll = () => {
    setSelectedIds(new Set());
    setHighlightedIds(new Set());
    setActiveGroups(new Set(data?.clusters.map((c) => c.pathway_group)));
    setYearRange(yearBounds);
    setSelectMode(false);
    setFiltersOpen(false);
  };

  const toggleGroup = (g: string) =>
    setActiveGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });

  if (error) {
    return (
      <div className="loading">
        <p>Could not load map data.</p>
        <pre>{error}</pre>
      </div>
    );
  }
  if (!data) return <div className="loading">Loading map…</div>;

  return (
    <div className="workspace" ref={workspaceRef}>
      <div className="agent-col" style={{ width: agentWidth }}>
        <AgentPanel controller={controller} />
      </div>

      <div
        className={`divider ${dragging ? "dragging" : ""}`}
        onPointerDown={() => setDragging(true)}
        role="separator"
        aria-orientation="vertical"
      >
        <span className="divider-grip" />
      </div>

      <div className="map-col">
        <header className="map-topbar">
          <div className="map-topbar-title">
            <strong>Dementia Gap Map</strong>
            <span className="map-topbar-sub">Dementia AND GWAS · {data.papers.length} papers</span>
          </div>
          <div className="map-topbar-actions">
            <button
              className={`btn ${selectMode ? "active" : ""}`}
              onClick={() => setSelectMode((v) => !v)}
            >
              {selectMode ? "Drawing…" : "Select region"}
            </button>
            <button
              className={`btn ${filtersOpen ? "active" : ""}`}
              onClick={() => setFiltersOpen((v) => !v)}
            >
              Filters
            </button>
          </div>
        </header>

        {filtersOpen && (
          <div className="filters filters-bar">
            <div className="filters-row">
              <span className="filters-label">Pathway groups</span>
              <div className="filters-groups">
                {pathwayGroups.map(([g, color]) => (
                  <button
                    key={g}
                    className={`fchip ${activeGroups.has(g) ? "on" : ""}`}
                    onClick={() => toggleGroup(g)}
                  >
                    <span className="dot" style={{ background: color }} />
                    {g}
                  </button>
                ))}
              </div>
            </div>
            <div className="filters-row">
              <span className="filters-label">From year: {yearRange[0]}</span>
              <input
                type="range"
                min={yearBounds[0]}
                max={yearBounds[1]}
                value={yearRange[0]}
                onChange={(e) =>
                  setYearRange(([, hi]) => [Math.min(Number(e.target.value), hi), hi])
                }
              />
              <span className="filters-label">to {yearRange[1]}</span>
              <input
                type="range"
                min={yearBounds[0]}
                max={yearBounds[1]}
                value={yearRange[1]}
                onChange={(e) =>
                  setYearRange(([lo]) => [lo, Math.max(Number(e.target.value), lo)])
                }
              />
            </div>
          </div>
        )}

        <div className="map-stage">
          <MapCanvas
            ref={mapRef}
            papers={data.papers}
            edges={data.edges ?? []}
            clusters={data.clusters}
            viewMode="clusters"
            selectMode={selectMode}
            isActive={isActive}
            selectedIds={selectedIds}
            highlightedIds={highlightedIds}
            onSelect={(ids) => {
              setSelectedIds(new Set(ids));
              setSelectMode(false);
            }}
            onReset={resetAll}
          />
        </div>

        <div className="map-feed">
          <NewsFeed
            selected={selected}
            clusters={data.clusters}
            onClear={() => setSelectedIds(new Set())}
          />
        </div>
      </div>
    </div>
  );
}
