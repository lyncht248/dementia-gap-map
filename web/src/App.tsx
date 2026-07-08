import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AtlasMap, { type AtlasMapHandle } from "./components/AtlasMap";
import NewsFeed from "./components/NewsFeed";
import AgentPanel from "./components/AgentPanel";
import FlywheelMap from "./components/FlywheelMap";
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
  const [anchorId, setAnchorId] = useState<string | null>(null);
  const [meta, setMeta] = useState<AtlasReady | null>(null);
  const [view, setView] = useState<"disease" | "flywheel">("disease");
  const [hiddenMajors, setHiddenMajors] = useState<string[]>([]);
  const [yearRange, setYearRange] = useState<[number, number]>([2000, 2100]);
  const [count, setCount] = useState(0);
  const [feed, setFeed] = useState<FeedData | null>(null);
  const atlasRef = useRef<AtlasMapHandle>(null);
  // paper positions captured from the atlas when switching to the flywheel, so
  // its Research dots morph in from where they sat on the Disease-areas map.
  const entryRef = useRef<Map<string, { nx: number; ny: number }> | null>(null);

  // Switch framing; when entering the flywheel, snapshot the atlas dot positions.
  const goFlywheel = () => {
    const pos = atlasRef.current?.paperPositionsNorm() ?? [];
    entryRef.current = new Map(pos.map((p) => [p.id, { nx: p.nx, ny: p.ny }]));
    setView("flywheel");
  };

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
    setAnchorId(null);
    atlasRef.current?.clearSelection();
  };

  // Reset view: recenter the map AND clear the filters + any selection.
  const resetAll = () => {
    setHiddenMajors([]);
    if (meta) setYearRange([meta.yearMin, meta.yearMax]);
    setSelected([]);
    setAnchorId(null);
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
  // `anchor` = the single paper the user clicked (highlighted in the feed).
  const onSelect = (rows: SelectedPaper[], anchor?: string | null) => {
    setAnchorId(anchor ?? null);
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
          setAnchorId(null);
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

  // Panel open/close (and the initial split-layout settle) change the map width;
  // nudge a resize so the atlas re-fits (it auto-fits on resize until the user
  // pans/zooms).
  useEffect(() => {
    const t = setTimeout(() => window.dispatchEvent(new Event("resize")), 60);
    return () => clearTimeout(t);
  }, [agentOpen]);

  const mapPage = (
    <div className="app">
      <header className="hero">
        <h1>Dementia Gap Map</h1>
        <p>
          A map of dementia &amp; GWAS research from Pubmed, grouped semantically.
          Click &lsquo;Select Region&rsquo; and draw a circle.
        </p>
        <p>
          Each paper (node) is linked to its gene target(s), pathway(s), and clinical
          trial(s). Connections on the map are based on citations.
        </p>
      </header>

      <section className={`map-panel ${view === "flywheel" ? "has-flywheel" : ""}`}>
        {meta && meta.hypotheses.length > 0 && (
          <div className="toolbar toolbar-left">
            <div className="segmented" role="group" aria-label="Map framing">
              <button
                className={`seg ${view === "disease" ? "on" : ""}`}
                onClick={() => setView("disease")}
                title="The semantic map, coloured by disease region"
              >
                Disease areas
              </button>
              <button
                className={`seg ${view === "flywheel" ? "on" : ""}`}
                onClick={goFlywheel}
                title="The development pipeline: hypotheses × stages (Research → Genetics → Models → Trials → Results)"
              >
                Flywheel
              </button>
            </div>
          </div>
        )}

        {view !== "flywheel" && (
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
        )}

        {filtersOpen && meta && view !== "flywheel" && (
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
          {/* Atlas stays mounted (holds the agent's map handle); hidden under the
              flywheel when that framing is active. */}
          <div
            style={{ width: "100%", height: "100%", display: view === "flywheel" ? "none" : "block" }}
          >
            <AtlasMap
              ref={atlasRef}
              selectMode={selectMode}
              mode="disease"
              hiddenMajors={hiddenMajors}
              hiddenHyp={[]}
              yearRange={yearRange}
              onSelect={onSelect}
              onSelectModeChange={setSelectMode}
              onReady={onReady}
              onCount={setCount}
            />
          </div>
          {view === "flywheel" && (
            <div className="fly-wrap">
              <FlywheelMap entry={entryRef.current ?? undefined} />
            </div>
          )}
        </div>

        {view !== "flywheel" && (
          <>
            <div className="toolbar toolbar-bottom">
              <span className="count-note">{count.toLocaleString()} papers</span>
            </div>
            <button className="reset-view" onClick={resetAll} title="Reset view">
              Reset view
            </button>
          </>
        )}
      </section>

      {view === "flywheel" && (
        <section className="fly-caption">
          <h2>The development flywheel</h2>
          <p>
            Each hypothesis (row) across the five pipeline stages (columns), ranked
            with the most clinically reinforced at the top. Every dot is one
            item — a <strong>paper</strong>, a genetically-supported <strong>gene</strong>,
            a <strong>model-validated</strong> gene, a <strong>trial</strong>, or a
            trial with <strong>results</strong>. Hover a column header or row for what
            it means, hover a dot to trace its lineage across stages, and click a dot
            to open it.
          </p>
        </section>
      )}

      {view !== "flywheel" && (
        <NewsFeed
          selected={selected}
          clusters={clusters}
          areas={areas}
          anchorId={anchorId}
          onClear={clearSelection}
          onFilteredChange={onFilteredChange}
        />
      )}

      <footer className="foot">
        Dementia Gap Map · research prototype · data from PubMed, GWAS Catalog, Open Targets
        &amp; ClinicalTrials.gov
      </footer>
    </div>
  );

  return (
    <div className="workspace" ref={workspaceRef}>
      {/* Kept mounted (hidden when minimized) so open chats are preserved. */}
      <div
        className="agent-col"
        style={{ width: agentWidth, display: agentOpen ? undefined : "none" }}
      >
        <AgentPanel controller={controller} onMinimize={() => setAgentOpen(false)} />
      </div>
      {agentOpen ? (
        <div
          className={`divider ${dragging ? "dragging" : ""}`}
          onPointerDown={() => setDragging(true)}
          role="separator"
          aria-orientation="vertical"
        >
          <span className="divider-grip" />
        </div>
      ) : (
        <button
          className="agent-reopen"
          onClick={() => setAgentOpen(true)}
          title="Show the research agent  ( [ )"
          aria-label="Show the research agent"
          aria-keyshortcuts="["
        >
          <span className="agent-reopen-label">Agent</span>
          <kbd className="agent-reopen-key" aria-hidden="true">[</kbd>
        </button>
      )}

      <div className="right-scroll">{mapPage}</div>
    </div>
  );
}
