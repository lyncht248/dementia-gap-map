import { useCallback, useEffect, useMemo, useState } from "react";
import type { MapData, Paper } from "./types";
import { loadMapData } from "./lib/data";
import MapCanvas from "./components/MapCanvas";
import NewsFeed from "./components/NewsFeed";

export default function App() {
  const [data, setData] = useState<MapData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [selectMode, setSelectMode] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [activeGroups, setActiveGroups] = useState<Set<string>>(new Set());
  const [yearRange, setYearRange] = useState<[number, number]>([2000, 2100]);

  const allGroups = useCallback(
    (d: MapData) => new Set([...(d.hypotheses ?? []).map((h) => h.id), "unclassified"]),
    []
  );

  useEffect(() => {
    loadMapData()
      .then((d) => {
        setData(d);
        setActiveGroups(allGroups(d));
        const years = d.papers.map((p) => p.year);
        setYearRange([Math.min(...years), Math.max(...years)]);
      })
      .catch((e) => setError(String(e)));
  }, [allGroups]);

  const yearBounds = useMemo<[number, number]>(() => {
    if (!data) return [2000, 2026];
    const years = data.papers.map((p) => p.year);
    return [Math.min(...years), Math.max(...years)];
  }, [data]);

  const hypoGroups = useMemo<[string, string, string][]>(() => {
    if (!data) return [];
    const g: [string, string, string][] = (data.hypotheses ?? []).map((h) => [h.id, h.label, h.color]);
    g.push(["unclassified", "Unclassified", "#cdcdd6"]);
    return g;
  }, [data]);

  const isActive = useCallback(
    (p: Paper) =>
      activeGroups.has(p.hypothesis ?? "unclassified") &&
      p.year >= yearRange[0] &&
      p.year <= yearRange[1],
    [activeGroups, yearRange]
  );

  const selected = useMemo(
    () => (data ? data.papers.filter((p) => selectedIds.has(p.paper_id)) : []),
    [data, selectedIds]
  );

  const resetAll = () => {
    setSelectedIds(new Set());
    if (data) setActiveGroups(allGroups(data));
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
    <div className="app">
      <header className="hero">
        <h1>Dementia Gap Map</h1>
        <p>
          Explore research papers matching &ldquo;Dementia AND GWAS&rdquo;,
          coloured by the disease hypothesis each supports. Drag to pan, scroll
          to zoom, then draw a region to inspect a group of papers below.
        </p>
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
              <span className="filters-label">Hypotheses</span>
              <div className="filters-groups">
                {hypoGroups.map(([g, label, color]) => (
                  <button
                    key={g}
                    className={`fchip ${activeGroups.has(g) ? "on" : ""}`}
                    onClick={() => toggleGroup(g)}
                  >
                    <span className="dot" style={{ background: color }} />
                    {label}
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
          edges={data.edges ?? []}
          clusters={data.clusters}
          hypotheses={data.hypotheses ?? []}
          viewMode="clusters"
          selectMode={selectMode}
          isActive={isActive}
          selectedIds={selectedIds}
          onSelect={(ids) => {
            setSelectedIds(new Set(ids));
            setSelectMode(false);
          }}
          onReset={resetAll}
        />

        <div className="toolbar toolbar-bottom">
          <span className="count-note">{data.papers.length} papers</span>
        </div>
      </section>

      <NewsFeed
        selected={selected}
        clusters={data.clusters}
        onClear={() => setSelectedIds(new Set())}
      />

      <footer className="foot">
        Co-citation map of dementia &amp; GWAS literature · data from PubMed / NIH
        iCite, GWAS Catalog, Open Targets &amp; ClinicalTrials.gov
      </footer>
    </div>
  );
}
