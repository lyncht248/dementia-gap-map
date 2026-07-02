import { useRef, useState } from "react";
import AtlasMap, { type AtlasMapHandle } from "./components/AtlasMap";
import AtlasFeed from "./components/AtlasFeed";
import type { SelectedPaper } from "./lib/atlasRender";

// The dementia gap map: the Qwen-embedding theme atlas sits in the map panel;
// the surrounding shell keeps the region-selection tool + the paper feed.
export default function App() {
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<SelectedPaper[]>([]);
  const atlasRef = useRef<AtlasMapHandle>(null);

  const clearSelection = () => {
    setSelected([]);
    atlasRef.current?.clearSelection();
  };

  return (
    <div className="app">
      <header className="hero">
        <h1>Dementia Gap Map</h1>
        <p>
          A theme map of ~4,780 &ldquo;Dementia AND GWAS&rdquo; papers, grouped by disease
          area from Qwen3 embeddings. Drag to pan, scroll to zoom, hover a dot to trace its
          citations, then draw a region to inspect a group of papers below.
        </p>
      </header>

      <section className="map-panel">
        <div className="toolbar toolbar-right">
          <button
            className={`btn ${selectMode ? "active" : ""}`}
            onClick={() => setSelectMode((v) => !v)}
          >
            {selectMode ? "Draw a region…" : "Select region"}
          </button>
          {selected.length > 0 && (
            <button className="btn" onClick={clearSelection}>Clear</button>
          )}
        </div>

        <div className="map-wrap">
          <AtlasMap
            ref={atlasRef}
            selectMode={selectMode}
            onSelect={setSelected}
            onSelectModeChange={setSelectMode}
          />
        </div>
      </section>

      <AtlasFeed selected={selected} onClear={clearSelection} />

      <footer className="foot">
        Theme atlas of dementia &amp; GWAS literature · Qwen3-Embedding-8B · citation links
        from PubMed / NIH iCite
      </footer>
    </div>
  );
}
