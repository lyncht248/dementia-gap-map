import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { mountAtlas, type AtlasData, type AtlasHandle, type SelectedPaper } from "../lib/atlasRender";

export interface AtlasMapHandle {
  clearSelection: () => void;
}
interface Props {
  selectMode: boolean;
  onSelect: (rows: SelectedPaper[]) => void;
  onSelectModeChange: (on: boolean) => void;
}

// The dementia theme atlas (Qwen3-Embedding-8B), embedded in the map panel.
// Fetches the pre-built layout and mounts the canvas renderer; forwards region
// selections up to the parent. Data: web/public/atlas/atlas.json.
const AtlasMap = forwardRef<AtlasMapHandle, Props>(function AtlasMap(
  { selectMode, onSelect, onSelectModeChange },
  ref
) {
  const elRef = useRef<HTMLDivElement>(null);
  const handleRef = useRef<AtlasHandle | null>(null);
  const [data, setData] = useState<AtlasData | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Keep the latest callbacks without re-mounting the canvas.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onModeRef = useRef(onSelectModeChange);
  onModeRef.current = onSelectModeChange;

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}atlas/atlas.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`Failed to load atlas data (${r.status})`);
        return r.json();
      })
      .then((d: AtlasData) => setData(d))
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!data || !elRef.current) return;
    const h = mountAtlas(elRef.current, data, {
      onSelect: (rows) => onSelectRef.current(rows),
      onSelectModeChange: (on) => onModeRef.current(on),
    });
    handleRef.current = h;
    return () => { h.destroy(); handleRef.current = null; };
  }, [data]);

  useEffect(() => { handleRef.current?.setSelectMode(selectMode); }, [selectMode]);

  useImperativeHandle(ref, () => ({
    clearSelection: () => handleRef.current?.clearSelection(),
  }), []);

  if (error) return <div className="atlas-loading"><p>Could not load the map.</p><pre>{error}</pre></div>;
  if (!data) return <div className="atlas-loading">Loading map…</div>;
  return <div ref={elRef} />;
});

export default AtlasMap;
