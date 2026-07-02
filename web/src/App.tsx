import { useCallback, useEffect, useMemo, useState } from "react";
import type { Cluster, LensFile, MapData, Paper } from "./types";
import { loadLensFile, loadMapData } from "./lib/data";
import MapCanvas from "./components/MapCanvas";
import NewsFeed from "./components/NewsFeed";

const lensFromHash = () => window.location.hash.replace(/^#\/?/, "").trim();

export default function App() {
  const [data, setData] = useState<MapData | null>(null);
  const [lensFile, setLensFile] = useState<LensFile | null>(null);
  const [lensId, setLensId] = useState<string>(lensFromHash());
  const [error, setError] = useState<string | null>(null);

  const [selectMode, setSelectMode] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [activeGroups, setActiveGroups] = useState<Set<string>>(new Set());
  const [yearRange, setYearRange] = useState<[number, number]>([2000, 2100]);

  useEffect(() => {
    Promise.all([loadMapData(), loadLensFile()])
      .then(([d, lf]) => {
        setData(d);
        setLensFile(lf);
        setActiveGroups(new Set(d.clusters.map((c) => c.pathway_group)));
        const years = d.papers.map((p) => p.year);
        setYearRange([Math.min(...years), Math.max(...years)]);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // keep the selected lens in sync with the URL hash so each option is a
  // shareable "page" (e.g. .../#pathway) on the Vercel preview.
  useEffect(() => {
    const onHash = () => setLensId(lensFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const lens = useMemo(() => {
    if (!lensFile) return null;
    return lensFile.lenses.find((l) => l.id === lensId) ?? lensFile.lenses.find((l) => l.id === lensFile.default) ?? lensFile.lenses[0] ?? null;
  }, [lensFile, lensId]);

  const selectLens = (id: string) => {
    window.location.hash = id;
    setLensId(id);
  };

  // apply the active lens's labels/coarse groups over the base clusters
  const clusters = useMemo<Cluster[]>(() => {
    if (!data) return [];
    if (!lens) return data.clusters;
    return data.clusters.map((c) => ({
      ...c,
      label: lens.fine[c.topic_id] ?? c.label,
      coarse_id: lens.coarse_of[c.topic_id] ?? null,
    }));
  }, [data, lens]);

  const coarseClusters = useMemo(
    () => (lens ? lens.coarse_clusters : data?.coarse_clusters ?? []),
    [lens, data]
  );

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

  const resetAll = () => {
    setSelectedIds(new Set());
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
    <div className="app">
      <header className="hero">
        <h1>Dementia Gap Map</h1>
        <p>
          Explore research papers matching &ldquo;Dementia AND GWAS&rdquo;,
          clustered by topic. Drag to pan, scroll to zoom, then draw a region to
          inspect a group of papers below.
        </p>
      </header>

      <section className="map-panel">
        <div className="toolbar toolbar-right">
          {lensFile && lensFile.lenses.length > 1 && (
            <div className="segmented" role="group" aria-label="Label scheme">
              {lensFile.lenses.map((l) => (
                <button
                  key={l.id}
                  className={`seg ${(lens?.id ?? lensFile.default) === l.id ? "on" : ""}`}
                  onClick={() => selectLens(l.id)}
                  title={`Label scheme: ${l.name}`}
                >
                  {l.name}
                </button>
              ))}
            </div>
          )}
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
          edges={data.edges ?? []}
          clusters={clusters}
          coarseClusters={coarseClusters}
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
        clusters={clusters}
        onClear={() => setSelectedIds(new Set())}
      />

      <footer className="foot">
        Co-citation map of dementia &amp; GWAS literature · data from PubMed / NIH
        iCite, GWAS Catalog, Open Targets &amp; ClinicalTrials.gov
      </footer>
    </div>
  );
}
