import { useEffect, useRef, useState } from "react";
import { mountFlywheel, type FlyData } from "../lib/flywheelRender";

// The development-pipeline ("flywheel") view: the 8 hypotheses as rows, the 5
// stages (Research -> Genetics -> Models -> Trials -> Results) as columns, with
// typed dots per cell and hover-lineage across stages. Loads its own dataset
// (flywheel.json) built by scripts/build_flywheel.py.
export default function FlywheelMap() {
  const elRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<FlyData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}atlas/flywheel.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Failed to load flywheel data (${r.status})`))))
      .then((d: FlyData) => setData(d))
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!data || !elRef.current) return;
    const h = mountFlywheel(elRef.current, data);
    return () => h.destroy();
  }, [data]);

  if (error) return <div className="atlas-loading"><p>Could not load the flywheel.</p><pre>{error}</pre></div>;
  if (!data) return <div className="atlas-loading">Loading pipeline…</div>;
  return <div ref={elRef} style={{ width: "100%", height: "100%" }} />;
}
