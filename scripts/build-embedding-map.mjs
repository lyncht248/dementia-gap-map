// Build the web app's map_data.json from a SPECTER2 embedding run.
//
// Input : data/exports/visual/embeddings/<model>/{points,clusters}.jsonl
//         data/processed/topic-dynamics/papers.jsonl   (paper metadata)
// Output: web/public/data/map_data.json                (same shape as the
//         co-citation builder, so the existing React app + two-tier labelling
//         renders it unchanged)
//
// Coordinates are the packed, non-overlapping display coords (px,py) from the
// embedding run, normalised to a ~1000-unit span. Clusters are HDBSCAN clusters;
// HDBSCAN noise (-1) becomes the "other" bucket. Run after scripts/embed_map.py,
// then scripts/label-clusters.mjs to apply the LLM label lenses.
//
//   node scripts/build-embedding-map.mjs --model specter2

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const P = (...p) => path.join(ROOT, ...p);
const MODEL = (process.argv.find((a) => a.startsWith("--model="))?.split("=")[1])
  || (process.argv[process.argv.indexOf("--model") + 1]) || "specter2";
const DIR = `data/exports/visual/embeddings/${MODEL}`;

const PALETTE = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948",
  "#b07aa1", "#ff9da7", "#9c755f", "#7c5cbf", "#8cd17d", "#d37295",
  "#86bcb6", "#e69f00", "#56b4e9", "#009e73", "#cc79a7", "#d55e00",
  "#0072b2", "#bcbd22", "#17becf", "#a6761d", "#1b9e77", "#8856a7",
  "#66a61e", "#e6ab02", "#a6cee3", "#fb9a99", "#b2df8a", "#cab2d6",
];
const OTHER_COLOR = "#b8b8be";

function readJsonl(rel) {
  const out = [];
  for (const line of fs.readFileSync(P(rel), "utf8").split("\n")) {
    const s = line.trim();
    if (s) out.push(JSON.parse(s));
  }
  return out;
}

console.log(`reading ${DIR}…`);
const points = readJsonl(`${DIR}/points.jsonl`);
const clustersRaw = readJsonl(`${DIR}/clusters.jsonl`);
const manifest = JSON.parse(fs.readFileSync(P(`${DIR}/manifest.json`), "utf8"));

// paper metadata (pmid/doi/authors/journal/metrics) keyed by paper_id
const meta = new Map();
for (const p of readJsonl("data/processed/topic-dynamics/papers.jsonl")) meta.set(p.paper_id, p);

// normalise packed coords to a ~1000-unit centred span (app auto-fits from this)
let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
for (const pt of points) { minX = Math.min(minX, pt.px); maxX = Math.max(maxX, pt.px); minY = Math.min(minY, pt.py); maxY = Math.max(maxY, pt.py); }
const k = 1000 / (Math.max(maxX - minX, maxY - minY) || 1);
const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
const nx = (x) => Math.round((x - cx) * k * 100) / 100;
const ny = (y) => Math.round((y - cy) * k * 100) / 100;

const clusterIdOf = (c) => (c === -1 ? "other" : `s${c}`);

// order real clusters by size for stable colour assignment; "other" last
const realClusters = clustersRaw.filter((c) => c.cluster !== -1).sort((a, b) => b.size - a.size);
const colorOf = new Map();
realClusters.forEach((c, i) => colorOf.set(clusterIdOf(c.cluster), PALETTE[i % PALETTE.length]));
colorOf.set("other", OTHER_COLOR);
const tfidfLabel = new Map(clustersRaw.map((c) => [clusterIdOf(c.cluster), c.label]));

const outPapers = points.map((pt) => {
  const m = meta.get(pt.paper_id) || {};
  const cid = clusterIdOf(pt.cluster);
  const mm = m.metrics || {};
  return {
    paper_id: pt.paper_id,
    pmid: m.pmid ?? null,
    doi: m.doi ?? null,
    title: pt.title || m.title || "(untitled)",
    year: pt.year ?? m.year,
    journal: m.journal ?? null,
    authors: (m.authors || []).map((a) => (typeof a === "string" ? a : a.name || String(a))),
    cluster_id: cid,
    x: nx(pt.px), y: ny(pt.py),
    genes: [],
    // filter chips operate on this; use the fine cluster so the existing UI works
    pathway_group: cid,
    trials: [],
    metrics: {
      citation_count: mm.citation_count ?? null,
      relative_citation_ratio: mm.relative_citation_ratio ?? null,
      apt: mm.apt ?? null,
      is_clinical: mm.is_clinical ?? null,
    },
    url: m.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${m.pmid}/` : null,
  };
});

// cluster metadata (centroid over normalised coords, palette colour, tf-idf label)
const byCluster = new Map();
outPapers.forEach((p) => {
  if (!byCluster.has(p.cluster_id)) byCluster.set(p.cluster_id, []);
  byCluster.get(p.cluster_id).push(p);
});
const clusterOrder = [...realClusters.map((c) => clusterIdOf(c.cluster))];
if (byCluster.has("other")) clusterOrder.push("other");
const outClusters = clusterOrder.map((cid) => {
  const ps = byCluster.get(cid) || [];
  const xs = ps.map((p) => p.x), ys = ps.map((p) => p.y);
  const years = ps.map((p) => p.year).filter((y) => Number.isInteger(y));
  return {
    topic_id: cid,
    label: tfidfLabel.get(cid) || cid,
    color: colorOf.get(cid) || OTHER_COLOR,
    pathway_group: cid,
    top_genes: [],
    trials: [],
    paper_count: ps.length,
    centroid: {
      x: Math.round((xs.reduce((a, b) => a + b, 0) / (xs.length || 1)) * 100) / 100,
      y: Math.round((ys.reduce((a, b) => a + b, 0) / (ys.length || 1)) * 100) / 100,
    },
    year_start: years.length ? Math.min(...years) : null,
    year_end: years.length ? Math.max(...years) : null,
    scores: {},
  };
});

const data = {
  generated_note: `SPECTER2 theme map: title+abstract embeddings (allenai/specter2 proximity adapter) → UMAP 2D + HDBSCAN, hex-packed bubbles. ` +
    `${outPapers.length} papers, ${outClusters.filter((c) => c.topic_id !== "other").length} clusters, ` +
    `${(byCluster.get("other") || []).length} noise. Built by scripts/build-embedding-map.mjs from ${DIR}.`,
  disease: "Alzheimer disease / dementia (ADRD)",
  coordinate_space: "SPECTER2 UMAP embedding, packed display coords (auto-fit by the app)",
  layout: "embedding",
  clusters: outClusters,
  papers: outPapers,
  edges: [],
};
const OUT = P("web/public/data/map_data.json");
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(data));
console.log(`wrote ${path.relative(ROOT, OUT)}  (${(fs.statSync(OUT).size / 1e6).toFixed(2)} MB)`);
console.log(`  papers=${outPapers.length} clusters=${outClusters.length} noise=${(byCluster.get("other") || []).length}`);
console.log(`  embed: model=${manifest.model} D=${manifest.D} n_clusters=${manifest.n_clusters} n_noise=${manifest.n_noise}`);
