import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  mountAtlas,
  type AtlasData,
  type AtlasHandle,
  type AtlasReady,
  type SelectedPaper,
} from "../lib/atlasRender";

export interface AtlasMapHandle {
  clearSelection: () => void;
  resetView: () => void;
  selectByIds: (ids: string[]) => number;
  highlightByIds: (ids: string[]) => number;
  clearHighlight: () => void;
  zoomToPapers: (ids: string[]) => number;
  setHighlight: (paperIds: string[] | null) => void;
}
interface Props {
  selectMode: boolean;
  hiddenMajors: string[];
  yearRange: [number, number];
  onSelect: (rows: SelectedPaper[], anchorId?: string | null) => void;
  onSelectModeChange: (on: boolean) => void;
  onReady: (meta: AtlasReady) => void;
  onCount: (n: number) => void;
}

// The dementia theme atlas (Qwen3-Embedding-8B), embedded in the map panel.
const AtlasMap = forwardRef<AtlasMapHandle, Props>(function AtlasMap(
  { selectMode, hiddenMajors, yearRange, onSelect, onSelectModeChange, onReady, onCount },
  ref
) {
  const elRef = useRef<HTMLDivElement>(null);
  const handleRef = useRef<AtlasHandle | null>(null);
  const [data, setData] = useState<AtlasData | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Keep latest callbacks without re-mounting the canvas.
  const cbs = useRef({ onSelect, onSelectModeChange, onReady, onCount });
  cbs.current = { onSelect, onSelectModeChange, onReady, onCount };

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
      onSelect: (rows, anchorId) => cbs.current.onSelect(rows, anchorId),
      onSelectModeChange: (on) => cbs.current.onSelectModeChange(on),
      onReady: (m) => cbs.current.onReady(m),
      onCount: (n) => cbs.current.onCount(n),
    });
    handleRef.current = h;
    return () => { h.destroy(); handleRef.current = null; };
  }, [data]);

  useEffect(() => { handleRef.current?.setSelectMode(selectMode); }, [selectMode]);
  useEffect(() => { handleRef.current?.setFilter(hiddenMajors, yearRange); }, [hiddenMajors, yearRange]);

  useImperativeHandle(ref, () => ({
    clearSelection: () => handleRef.current?.clearSelection(),
    resetView: () => handleRef.current?.resetView(),
    selectByIds: (ids) => handleRef.current?.selectByIds(ids) ?? 0,
    highlightByIds: (ids) => handleRef.current?.highlightByIds(ids) ?? 0,
    clearHighlight: () => handleRef.current?.clearHighlight(),
    zoomToPapers: (ids) => handleRef.current?.zoomToIds(ids) ?? 0,
    setHighlight: (ids: string[] | null) => handleRef.current?.setHighlight(ids),
  }), []);

  if (error) return <div className="atlas-loading"><p>Could not load the map.</p><pre>{error}</pre></div>;
  if (!data) return <div className="atlas-loading">Loading map…</div>;
  return <div ref={elRef} />;
});

export default AtlasMap;
