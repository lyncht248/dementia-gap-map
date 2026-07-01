import { useCallback, useEffect, useMemo, useState } from "react";
import type { MapData, Paper } from "./types";
import { loadMapData } from "./lib/data";
import MapCanvas from "./components/MapCanvas";
import NewsFeed from "./components/NewsFeed";

export default function App() {
  const [data, setData] = useState<MapData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [viewMode, setViewMode] = useState<"clusters" | "all">("clusters");
  const [selectMode, setSelectMode] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [activeGroups, setActiveGroups] = useState<Set<string>>(new Set());
  const [yearRange, setYearRange] = useState<[number, number]>([2000, 2100]);

  useEffect(() => {
    loadMapData()
      .then((d) => {
        setData(d);
        setActiveGroups(new Set(d.clusters.map((c) => c.pathway_group)));
        const years = d.papers.map((p) => p.year);
        setYearRange([Math.min(...years), Math.max(...years)]);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const yearBounds = useMemo<[number, number]>(() => {
    if (!data) return [2000, 2026];
    const years = data.papers.map((p) => p.year);
    return [Math.min(...years), Math.max(...years)];
  }, [data]);

  const pathwayGroups = useMemo(() => {
    if (!data) return [];
    const seen = new Map<string, string>(); // group -> color
    for (const c of data.clusters) if (!seen.has(c.pathway_group)) seen.set(c.pathway_group, c.color);
    return [...seen.entries()];
  }, [data]);

  const isActive = useCallback(
    (p: Paper) =>
      activeGroups.has(p.pathway_group) &&
      p.year >= yearRange[0] &&
      p.year <= yearRange[1],
    [activeGroups, yearRange]
  );

  const selected = useMemo(
    () => (data ? data.papers.filter((p) => selectedIds.has(p.paper_id)) : []),
    [data, selectedIds]
  );

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
    <div className="app">
      <header className="hero">
        <h1>Dementia Gap Map</h1>
        <p>
          Explore dementia &amp; Alzheimer research papers, clustered by topic.
          Drag to pan, scroll to zoom, then draw a region to inspect a group of
          papers below.
        </p>
        {data.generated_note && (
          <span className="note-badge" title={data.generated_note}>
            sample data
          </span>
        )}
      </header>

      <section className="map-panel">
        <div className="toolbar toolbar-right">
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

        {filtersOpen && (
          <div className="filters">
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
              <span className="filters-label">
                From year: {yearRange[0]}
              </span>
              <input
                type="range"
                min={yearBounds[0]}
                max={yearBounds[1]}
                value={yearRange[0]}
                onChange={(e) =>
                  setYearRange(([, hi]) => [
                    Math.min(Number(e.target.value), hi),
                    hi,
                  ])
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

        <MapCanvas
          papers={data.papers}
          clusters={data.clusters}
          viewMode={viewMode}
          selectMode={selectMode}
          isActive={isActive}
          selectedIds={selectedIds}
          onSelect={(ids) => {
            setSelectedIds(new Set(ids));
            setSelectMode(false);
          }}
        />

        <div className="toolbar toolbar-bottom">
          <div className="segmented">
            <button
              className={viewMode === "clusters" ? "seg on" : "seg"}
              onClick={() => setViewMode("clusters")}
            >
              Clusters
            </button>
            <button
              className={viewMode === "all" ? "seg on" : "seg"}
              onClick={() => setViewMode("all")}
            >
              All papers
            </button>
          </div>
          <span className="count-note">{data.papers.length} papers</span>
        </div>
      </section>

      <NewsFeed
        selected={selected}
        clusters={data.clusters}
        onClear={() => setSelectedIds(new Set())}
      />

      <footer className="foot">
        Prototype MVP · synthetic placeholder data · see PROTOTYPE_BUILD_SPEC.md
      </footer>
    </div>
  );
}
