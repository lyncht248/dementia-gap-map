import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AtlasMap, { type AtlasMapHandle } from "./components/AtlasMap";
import NewsFeed from "./components/NewsFeed";
import AgentPanel from "./components/AgentPanel";
import { createController } from "./agent/controller";
import { warmupDuckDb } from "./lib/duckdb";
import type { AtlasReady, SelectedPaper } from "./lib/atlasRender";
import type { AreaInfo, Cluster, Paper } from "./types";

interface FeedData {
  clusters: Cluster[];
  areas: AreaInfo[];
  byId: Map<string, Paper>;
}

// The dementia gap map: the Qwen-embedding theme atlas sits in the map panel;
// selecting papers on it drives the rich NewsFeed. An agent panel on the left
// queries the Track B evidence (DuckDB) and drives the selection feed.
export default function App() {
  const [selectMode, setSelectMode] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selected, setSelected] = useState<Paper[]>([]);
  const [meta, setMeta] = useState<AtlasReady | null>(null);
  const [hiddenMajors, setHiddenMajors] = useState<string[]>([]);
  const [yearRange, setYearRange] = useState<[number, number]>([2000, 2100]);
  const [count, setCount] = useState(0);
  const [feed, setFeed] = useState<FeedData | null>(null);
  const atlasRef = useRef<AtlasMapHandle>(null);

  // Split layout
  const [agentOpen, setAgentOpen] = useState(true);
  const [agentWidth, setAgentWidth] = useState(400);
  const [dragging, setDragging] = useState(false);
  const workspaceRef = useRef<HTMLDivElement>(null);

  // NewsFeed data: papers regrouped by the atlas themes + Track B evidence.
  useEffect(() => {
    warmupDuckDb();
    fetch(`${import.meta.env.BASE_URL}atlas/atlas_feed.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { clusters: Cluster[]; areas?: AreaInfo[]; papers: Paper[] }) => {
        setFeed({
          clusters: d.clusters,
          areas: d.areas ?? [],
          byId: new Map(d.papers.map((p) => [p.paper_id, p])),
        });
      })
      .catch(() => setFeed({ clusters: [], areas: [], byId: new Map() }));
  }, []);

  const clearSelection = () => {
    setSelected([]);
    atlasRef.current?.clearSelection();
  };

  // Reset view: recenter the map AND clear the filters + any selection.
  const resetAll = () => {
    setHiddenMajors([]);
    if (meta) setYearRange([meta.yearMin, meta.yearMax]);
    setSelected([]);
    setSelectMode(false);
    setFiltersOpen(false);
    atlasRef.current?.clearSelection();
    atlasRef.current?.resetView();
  };

  const onReady = (m: AtlasReady) => {
    setMeta(m);
    setYearRange([m.yearMin, m.yearMax]);
    setCount(m.total);
  };

  // Map the atlas' selected paper ids -> full paper records for the feed.
  const onSelect = (rows: SelectedPaper[]) => {
    const byId = feed?.byId;
    if (!byId) return;
    setSelected(rows.map((r) => byId.get(r.paper_id)).filter((p): p is Paper => !!p));
  };

  const toggleMajor = (id: string) =>
    setHiddenMajors((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  // The NewsFeed's active facet filter (e.g. gene = APOE) drives a map highlight.
  const onFilteredChange = useCallback((ids: string[] | null) => {
    atlasRef.current?.setHighlight(ids);
  }, []);

  const clusters = useMemo(() => feed?.clusters ?? [], [feed]);
  const areas = useMemo(() => feed?.areas ?? [], [feed]);

  // --- agent controller (reads latest state via refs; stable identity) ------
  const feedRef = useRef(feed);
  feedRef.current = feed;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const yearRef = useRef(yearRange);
  yearRef.current = yearRange;

  const controller = useMemo(
    () =>
      createController({
        getPapers: () => (feedRef.current ? Array.from(feedRef.current.byId.values()) : []),
        getClusters: () => feedRef.current?.clusters ?? [],
        getSelectedIds: () => selectedRef.current.map((p) => p.paper_id),
        getYearRange: () => yearRef.current,
        setSelectedByIds: (ids) => {
          const byId = feedRef.current?.byId;
          if (!byId) return;
          setSelected(ids.map((id) => byId.get(id)).filter((p): p is Paper => !!p));
        },
        clearSelection: () => {
          setSelected([]);
          atlasRef.current?.clearSelection();
        },
        resetView: () => atlasRef.current?.resetView(),
        setYearRange: (r) => setYearRange(r),
        atlas: () => atlasRef.current,
      }),
    []
  );

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
      const max = (rect?.width ?? 1200) - 360;
      setAgentWidth(Math.max(300, Math.min(Math.max(300, max), e.clientX - left)));
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

  // "[" toggles the agent panel (ignored while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "[" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable)
        return;
      e.preventDefault();
      setAgentOpen((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The atlas fits its view on mount; inside the split layout that can happen
  // before the flex row settles, leaving it bunched. Nudge a resize once the
  // layout is stable and whenever the panel opens/closes (big width change).
  useEffect(() => {
    const nudge = () => window.dispatchEvent(new Event("resize"));
    const raf = requestAnimationFrame(nudge);
    const t = setTimeout(nudge, 250);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(t);
    };
  }, [agentOpen]);

  const mapPage = (
    <div className="app">
      <header className="hero">
        <h1>Dementia Gap Map</h1>
        <p>
          Explore research papers matching &ldquo;Dementia AND GWAS&rdquo; from PubMed, coloured
          by disease area and semantically placed using Qwen embeddings, with topic labels
          added. Each label shows a growth trend (↑ rising, ↓ falling) — papers in the last 3
          years ÷ the preceding 3 years. Drag to pan, scroll to zoom, then draw a region to
          inspect a group of papers below.
        </p>
      </header>

      <section className="map-panel">
        <div className="toolbar toolbar-right">
          <button
            className={`btn ${selectMode ? "active" : ""}`}
            onClick={() => setSelectMode((v) => !v)}
          >
            {selectMode ? "Click and drag…" : "Select region"}
          </button>
          <button
            className={`btn ${filtersOpen ? "active" : ""}`}
            onClick={() => setFiltersOpen((v) => !v)}
          >
            Filters
          </button>
        </div>

        {filtersOpen && meta && (
          <div className="filters">
            <div className="filters-row">
              <span className="filters-label">Disease areas</span>
              <div className="filters-groups">
                {meta.majors.map((m) => (
                  <button
                    key={m.id}
                    className={`fchip ${hiddenMajors.includes(m.id) ? "" : "on"}`}
                    onClick={() => toggleMajor(m.id)}
                  >
                    <span className="dot" style={{ background: m.color }} />
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="filters-row">
              <span className="filters-label">From year: {yearRange[0]}</span>
              <input
                type="range"
                min={meta.yearMin}
                max={meta.yearMax}
                value={yearRange[0]}
                onChange={(e) =>
                  setYearRange(([, hi]) => [Math.min(Number(e.target.value), hi), hi])
                }
              />
              <span className="filters-label">to {yearRange[1]}</span>
              <input
                type="range"
                min={meta.yearMin}
                max={meta.yearMax}
                value={yearRange[1]}
                onChange={(e) =>
                  setYearRange(([lo]) => [lo, Math.max(Number(e.target.value), lo)])
                }
              />
            </div>
          </div>
        )}

        <div className="map-wrap">
          <AtlasMap
            ref={atlasRef}
            selectMode={selectMode}
            hiddenMajors={hiddenMajors}
            yearRange={yearRange}
            onSelect={onSelect}
            onSelectModeChange={setSelectMode}
            onReady={onReady}
            onCount={setCount}
          />
        </div>

        <div className="toolbar toolbar-bottom">
          <span className="count-note">{count.toLocaleString()} papers</span>
        </div>
        <button className="reset-view" onClick={resetAll} title="Reset view">
          Reset view
        </button>
      </section>

      <NewsFeed
        selected={selected}
        clusters={clusters}
        areas={areas}
        onClear={clearSelection}
        onFilteredChange={onFilteredChange}
      />

      <footer className="foot">
        Theme atlas of dementia &amp; GWAS literature · Qwen3-Embedding-8B · citation links
        from PubMed / NIH iCite, GWAS Catalog, Open Targets &amp; ClinicalTrials.gov
      </footer>
    </div>
  );

  return (
    <div className="workspace" ref={workspaceRef}>
      {agentOpen ? (
        <>
          <div className="agent-col" style={{ width: agentWidth }}>
            <AgentPanel controller={controller} onMinimize={() => setAgentOpen(false)} />
          </div>
          <div
            className={`divider ${dragging ? "dragging" : ""}`}
            onPointerDown={() => setDragging(true)}
            role="separator"
            aria-orientation="vertical"
          >
            <span className="divider-grip" />
          </div>
        </>
      ) : (
        <button
          className="agent-reopen"
          onClick={() => setAgentOpen(true)}
          title="Show the research agent  ( [ )"
          aria-label="Show the research agent"
          aria-keyshortcuts="["
        >
          <span className="agent-reopen-icon" aria-hidden="true">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2l1.7 6.1c.2.6.6 1 1.2 1.2L21 11l-6.1 1.7c-.6.2-1 .6-1.2 1.2L12 20l-1.7-6.1c-.2-.6-.6-1-1.2-1.2L3 11l6.1-1.7c.6-.2 1-.6 1.2-1.2z" />
            </svg>
          </span>
          <span className="agent-reopen-label">Agent</span>
          <kbd className="agent-reopen-key" aria-hidden="true">[</kbd>
        </button>
      )}

      <div className="right-scroll">{mapPage}</div>
    </div>
  );
}
