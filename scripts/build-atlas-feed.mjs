// Build the NewsFeed data for the Theme Atlas.
//
// Regroups the per-paper Track B metadata (already assembled in
// web/public/data/map_data.json — authors, journal, iCite metrics, genes,
// trials, pathway_group) by the **45 Qwen embedding themes** the atlas map is
// drawn from, and attaches each theme's Track B evidence rollup (top genes,
// trials, translational scores) + a per-theme emergence score.
//
// Inputs:
//   web/public/data/map_data.json                                   (per-paper metadata)
//   data/exports/visual/embeddings/qwen3-8b/points.jsonl            (paper -> theme id)
//   web/public/atlas/atlas.json                                     (theme labels)
//   data/processed/shared/atlas_evidence_rollup.jsonl               (Track B evidence per theme, PR #19)
// Output:
//   web/public/atlas/atlas_feed.json   { clusters, papers }  (MapData shape)
//
// Run: node scripts/build-atlas-feed.mjs

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { computeEmergence } from "./emergence.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const P = (...p) => path.join(ROOT, ...p);
const readJsonl = (rel) =>
  fs.readFileSync(P(rel), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));

// ---- inputs ----------------------------------------------------------------
const mapData = JSON.parse(fs.readFileSync(P("web/public/data/map_data.json"), "utf8"));
const points = readJsonl("data/exports/visual/embeddings/qwen3-8b/points.jsonl");
const atlas = JSON.parse(fs.readFileSync(P("web/public/atlas/atlas.json"), "utf8"));
const rollups = readJsonl("data/processed/shared/atlas_evidence_rollup.jsonl");

// paper_id -> true HDBSCAN theme id (-1 = noise/"other")
const themeOf = new Map(points.map((p) => [p.paper_id, p.cluster]));
// theme id -> readable label (the atlas map's own labels, kept in sync)
const themeLabel = new Map(atlas.minors.map((m) => [m.id, m.label]));
// theme id -> its disease area (major) id  — the coarse level of the hierarchy
const themeArea = new Map(atlas.minors.map((m) => [m.id, m.major]));
// disease area id -> { label, color }
const areaInfo = new Map(atlas.majors.map((m) => [m.id, { label: m.label, color: m.color }]));
// "atlas:N" -> evidence rollup
const rollupOf = new Map(rollups.map((r) => [r.topic_id, r]));

// ---- distinct categorical colours for the theme list -----------------------
// Golden-angle hue rotation so 45 themes stay visually separable in the feed.
function themeColor(i) {
  const hue = (i * 137.508) % 360;
  const light = 45 + ((i % 3) * 7); // 45 / 52 / 59
  const [r, g, b] = hslToRgb(hue / 360, 0.58, light / 100);
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}
function hslToRgb(h, s, l) {
  let r, g, b;
  if (s === 0) { r = g = b = l; }
  else {
    const hue2rgb = (p, q, t) => {
      if (t < 0) t += 1; if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3); g = hue2rgb(p, q, h); b = hue2rgb(p, q, h - 1 / 3);
  }
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

// ---- papers: reuse metadata, regroup by atlas theme ------------------------
// Each paper carries BOTH levels of the hierarchy: its fine theme (cluster_id,
// e.g. "East Asian AD Genetics") and its disease area (area, e.g. "Alzheimer's
// Disease & Dementia"). The feed can group / filter by either.
const papers = mapData.papers.map((p) => {
  const t = themeOf.get(p.paper_id);
  const cluster_id = t == null || t < 0 ? "other" : `t${t}`;
  const area = t == null || t < 0 ? "other" : themeArea.get(t) ?? "other";
  return { ...p, cluster_id, area };
});

// ---- clusters: one per atlas theme present + "other" -----------------------
const present = [...new Set(papers.map((p) => p.cluster_id))];
const themeIds = present
  .filter((id) => id !== "other")
  .map((id) => Number(id.slice(1)))
  .sort((a, b) => a - b);

const byCluster = new Map();
for (const p of papers) {
  if (!byCluster.has(p.cluster_id)) byCluster.set(p.cluster_id, []);
  byCluster.get(p.cluster_id).push(p);
}
const centroidOf = (id) => {
  const mem = byCluster.get(id) || [];
  const n = mem.length || 1;
  return { x: mem.reduce((a, p) => a + (p.x ?? 0), 0) / n, y: mem.reduce((a, p) => a + (p.y ?? 0), 0) / n };
};

const dedupe = (arr, n) => [...new Set(arr)].slice(0, n);

const clusters = themeIds.map((t, i) => {
  const id = `t${t}`;
  const roll = rollupOf.get(`atlas:${t}`);
  const top_genes = roll ? dedupe(roll.top_genes.map((g) => g.symbol), 8) : [];
  const trials = roll ? dedupe((roll.trials || []).map((tr) => tr.brief_title), 12) : [];
  return {
    topic_id: id,
    label: themeLabel.get(t) || (roll ? roll.label : id),
    color: themeColor(i),
    pathway_group: roll ? roll.pathway_group : "unclassified",
    top_genes,
    trials,
    paper_count: (byCluster.get(id) || []).length,
    centroid: centroidOf(id),
    scores: roll ? roll.scores : {},
  };
});

// keep "other" as a grey, non-emerging bucket (HDBSCAN noise)
clusters.push({
  topic_id: "other",
  label: "Other / unclustered",
  color: "#b4b7bd",
  pathway_group: "unclassified",
  top_genes: [],
  trials: [],
  paper_count: (byCluster.get("other") || []).length,
  centroid: centroidOf("other"),
  scores: {},
});

// per-theme emergence (burst + growth + influence) over its members
computeEmergence(papers, clusters);

// ---- disease areas (the coarse level of the hierarchy) ---------------------
const areaCount = new Map();
for (const p of papers) areaCount.set(p.area, (areaCount.get(p.area) || 0) + 1);
const areas = [...areaCount.keys()]
  .map((id) => ({
    id,
    label: id === "other" ? "Other / unclustered" : areaInfo.get(id)?.label ?? id,
    color: id === "other" ? "#b4b7bd" : areaInfo.get(id)?.color ?? "#999",
    paper_count: areaCount.get(id),
  }))
  .sort((a, b) => b.paper_count - a.paper_count);

// ---- write -----------------------------------------------------------------
// Slim the papers down to what the NewsFeed reads (keeps the file small).
const slimPapers = papers.map((p) => ({
  paper_id: p.paper_id, title: p.title, year: p.year, journal: p.journal,
  authors: p.authors, cluster_id: p.cluster_id, area: p.area, x: p.x, y: p.y,
  genes: p.genes, pathway_group: p.pathway_group, trials: p.trials,
  metrics: p.metrics, url: p.url,
}));

const out = {
  generated_note:
    `Theme-atlas NewsFeed: ${slimPapers.length} papers regrouped by ${themeIds.length} Qwen embedding ` +
    `themes (+ "other") within ${areas.length} disease areas, with Track B evidence rollups and ` +
    `per-theme emergence. Built by scripts/build-atlas-feed.mjs.`,
  disease: mapData.disease,
  areas,
  clusters,
  papers: slimPapers,
};
const OUT = P("web/public/atlas/atlas_feed.json");
fs.writeFileSync(OUT, JSON.stringify(out));
const emerging = clusters.filter((c) => c.emergence).sort((a, b) => b.emergence.score - a.emergence.score);
console.log(`wrote ${path.relative(ROOT, OUT)} (${(fs.statSync(OUT).size / 1e6).toFixed(2)} MB)`);
console.log(`  ${slimPapers.length} papers · ${themeIds.length} themes + other · ${byCluster.get("other")?.length ?? 0} unclustered`);
console.log(`  top emerging: ${emerging.slice(0, 5).map((c) => `${c.label} (${Math.round(c.emergence.score * 100)})`).join(", ")}`);
