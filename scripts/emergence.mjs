// Per-cluster "emergence" score, in the spirit of Davis et al. 2025's
// breakthrough signature: a topic is emerging when it shows a burst of new
// papers (%New), fast growth, and influential work (RCR). Computed over each
// cluster's full membership so it's a property of the topic, not of a selection.
//
// Shared by build-map-data.mjs (canonical pipeline) and add_emergence.mjs
// (patches an already-built map_data.json without touching the layout).

export function computeEmergence(papers, clusters) {
  const years = papers.map((p) => p.year).filter((y) => Number.isInteger(y));
  const maxYear = years.length ? Math.max(...years) : new Date().getFullYear();
  const recentFrom = maxYear - 2;        // last 3 years = "new"
  const priorLo = maxYear - 5, priorHi = maxYear - 3; // preceding 3 years

  const byCluster = new Map();
  for (const p of papers) {
    if (!byCluster.has(p.cluster_id)) byCluster.set(p.cluster_id, []);
    byCluster.get(p.cluster_id).push(p);
  }

  // raw per-cluster metrics
  const raw = new Map();
  for (const c of clusters) {
    if (c.topic_id === "other") continue;
    const mem = byCluster.get(c.topic_id) || [];
    if (!mem.length) continue;
    const recent = mem.filter((p) => Number.isInteger(p.year) && p.year >= recentFrom).length;
    const prior = mem.filter((p) => Number.isInteger(p.year) && p.year >= priorLo && p.year <= priorHi).length;
    const rcrs = mem.map((p) => p.metrics && p.metrics.relative_citation_ratio).filter((v) => typeof v === "number");
    const cx = c.centroid ? c.centroid.x : 0, cy = c.centroid ? c.centroid.y : 0;
    const dists = mem.map((p) => Math.hypot((p.x ?? cx) - cx, (p.y ?? cy) - cy)).sort((a, b) => a - b);
    raw.set(c.topic_id, {
      pct_new: recent / mem.length,
      growth: recent / Math.max(1, prior),
      mean_rcr: rcrs.length ? rcrs.reduce((a, b) => a + b, 0) / rcrs.length : 0,
      spread: dists.length ? dists[Math.floor(dists.length / 2)] : 0, // median dist from centroid (diffuseness ~ low cohesion)
    });
  }

  // normalize each signal to [0,1] across clusters
  const keys = ["pct_new", "growth", "mean_rcr", "spread"];
  const norm = {};
  for (const k of keys) {
    const vals = [...raw.values()].map((r) => (k === "growth" ? Math.log1p(r[k]) : r[k]));
    const lo = Math.min(...vals), hi = Math.max(...vals);
    norm[k] = (v) => (hi > lo ? ((k === "growth" ? Math.log1p(v) : v) - lo) / (hi - lo) : 0);
  }

  for (const c of clusters) {
    if (c.topic_id === "other" || !raw.has(c.topic_id)) { c.emergence = null; continue; }
    const r = raw.get(c.topic_id);
    // burst-weighted composite; low cohesion (high spread) contributes a little
    const score = 0.45 * norm.pct_new(r.pct_new) + 0.30 * norm.growth(r.growth) +
                  0.15 * norm.mean_rcr(r.mean_rcr) + 0.10 * norm.spread(r.spread);
    c.emergence = {
      score: Math.round(score * 1000) / 1000,
      pct_new: Math.round(r.pct_new * 1000) / 1000,
      growth: Math.round(r.growth * 100) / 100,
      mean_rcr: Math.round(r.mean_rcr * 100) / 100,
    };
  }
  return clusters;
}
