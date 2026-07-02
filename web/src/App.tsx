import { useRef, useState } from "react";
import AtlasMap, { type AtlasMapHandle } from "./components/AtlasMap";
import AtlasFeed from "./components/AtlasFeed";
import type { AtlasReady, SelectedPaper } from "./lib/atlasRender";

// The dementia gap map: the Qwen-embedding theme atlas sits in the map panel,
// wrapped in the familiar shell — Select region (lasso), a Filters dropdown
// (disease areas + year range), a paper count, Reset view, and the paper feed.
export default function App() {
  const [selectMode, setSelectMode] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selected, setSelected] = useState<SelectedPaper[]>([]);
  const [meta, setMeta] = useState<AtlasReady | null>(null);
  const [hiddenMajors, setHiddenMajors] = useState<string[]>([]);
  const [yearRange, setYearRange] = useState<[number, number]>([2000, 2100]);
  const [count, setCount] = useState(0);
  const atlasRef = useRef<AtlasMapHandle>(null);

  const clearSelection = () => {
    setSelected([]);
    atlasRef.current?.clearSelection();
  };

  const onReady = (m: AtlasReady) => {
    setMeta(m);
    setYearRange([m.yearMin, m.yearMax]);
    setCount(m.total);
  };

  const toggleMajor = (id: string) =>
    setHiddenMajors((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <div className="app">
      <header className="hero">
        <h1>Dementia Gap Map</h1>
        <p>
          Explore research papers matching &ldquo;Dementia AND GWAS&rdquo; from PubMed, coloured
          by the disease hypothesis each supports. Drag to pan, scroll to zoom, then draw a
          region to inspect a group of papers below.
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
            onSelect={setSelected}
            onSelectModeChange={setSelectMode}
            onReady={onReady}
            onCount={setCount}
          />
        </div>

        <div className="toolbar toolbar-bottom">
          <span className="count-note">{count.toLocaleString()} papers</span>
        </div>
        <button className="reset-view" onClick={() => atlasRef.current?.resetView()} title="Reset view">
          Reset view
        </button>
      </section>

      <AtlasFeed selected={selected} onClear={clearSelection} />

      <footer className="foot">
        Theme atlas of dementia &amp; GWAS literature · Qwen3-Embedding-8B · citation links
        from PubMed / NIH iCite
      </footer>
    </div>
  );
}
