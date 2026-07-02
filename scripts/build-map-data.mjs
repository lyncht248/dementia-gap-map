// Offline map builder: force-directed layout + community detection over the
// real bibliographic-coupling graph, joined with Track B evidence.
//
//   Track A: data/processed/topic-dynamics/{papers,paper_edges,topic_clusters}.jsonl
//   Track B: data/processed/shared/topic_evidence_links.jsonl
//            data/processed/translational-evidence/{genes,trials}.jsonl
//            translational-evidence/map/gene_pathway.csv
//   Output : web/public/data/map_data.json
//
// Communities and positions are derived from the SAME coupling graph, so a
// spatial clump on the map IS a topic. Deterministic: node positions are
// seeded from a golden-angle spiral (no RNG), so re-runs are stable.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Graph from "graphology";
import louvain from "graphology-communities-louvain";
import forceAtlas2 from "graphology-layout-forceatlas2";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const P = (...p) => path.join(ROOT, ...p);

// --- tunables ---------------------------------------------------------------
const RESOLUTION = Number(process.env.RESOLUTION ?? 1.2); // higher => more communities
const EDGE_MIN_WEIGHT = Number(process.env.EDGE_MIN_WEIGHT ?? 0.12); // drop weak coupling edges (hairball) before clustering/layout
const MIN_COMMUNITY = 30; // communities smaller than this are merged into neighbours / "other"
const FA2_ITERATIONS = 600;
const EDGES_PER_NODE = 3; // strongest neighbours kept per node for drawing
const TOP_GENES = 8;
const TOP_TRIALS = 10;

const PALETTE = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948",
  "#b07aa1", "#ff9da7", "#9c755f", "#7c5cbf", "#8cd17d", "#d37295",
  "#86bcb6", "#e69f00", "#56b4e9", "#009e73", "#cc79a7", "#d55e00",
  "#0072b2", "#bcbd22", "#17becf", "#a6761d", "#666666", "#1b9e77",
];
const OTHER_COLOR = "#b8b8be";

// --- io helpers -------------------------------------------------------------
function readJsonl(rel) {
  const out = [];
  const txt = fs.readFileSync(P(rel), "utf8");
  for (const line of txt.split("\n")) {
    const s = line.trim();
    if (s) out.push(JSON.parse(s));
  }
  return out;
}
const pmidOf = (id) => (id && id.startsWith("pmid:") ? id.slice(5) : id);

// --- label tokenisation -----------------------------------------------------
const STOP = new Set((
  "the a an and or of to in for with on by from as at is are was were be been being " +
  "this that these those we our study studies analysis using based between among within " +
  "results result method methods conclusion conclusions background objective aim aims " +
  "disease diseases patient patients gene genes genetic genetics associated association " +
  "associations risk role effect effects human cell cells clinical use used novel new via " +
  "case cases control controls cohort data model models level levels high low increased " +
  "expression protein proteins function functional related evidence potential findings " +
  "identify identified identification investigate investigated assessment analyses"
).split(" "));

function tokenize(title) {
  return (title || "")
    .toLowerCase()
    .split(/[^a-z0-9\-]+/)
    .filter((t) => t.length >= 4 && !STOP.has(t) && !/^\d+$/.test(t));
}

// --- load Track A -----------------------------------------------------------
console.log("reading Track A…");
const papers = readJsonl("data/processed/topic-dynamics/papers.jsonl");
const edgesRaw = readJsonl("data/processed/topic-dynamics/paper_edges.jsonl");
console.log(`  ${papers.length} papers, ${edgesRaw.length} edges`);

// --- load Track B -----------------------------------------------------------
console.log("reading Track B…");
const genePathway = new Map(); // symbol -> pathway_group
const pathwayVocab = new Set();
for (const line of fs.readFileSync(P("translational-evidence/map/gene_pathway.csv"), "utf8").split("\n").slice(1)) {
  const [sym, pg] = line.split(",");
  if (sym && pg) { genePathway.set(sym.trim(), pg.trim()); pathwayVocab.add(pg.trim()); }
}
const geneScore = new Map(); // symbol -> {genetic_support, functional_support}
for (const g of readJsonl("data/processed/translational-evidence/genes.jsonl")) {
  const es = g.evidence_scores || {};
  if (g.symbol) geneScore.set(g.symbol, {
    genetic_support: es.genetic_support ?? null,
    functional_support: es.functional_support ?? null,
  });
}
// trial mechanism_group -> pathway_group aliases
const MECH_ALIAS = { inflammation_microglia: "microglia_immune" };
const trialsByPathway = new Map(); // pathway_group -> [brief_title]
for (const t of readJsonl("data/processed/translational-evidence/trials.jsonl")) {
  let mg = t.mechanism_group;
  if (!mg || mg === "other") continue;
  mg = MECH_ALIAS[mg] || mg;
  const title = (t.brief_title || "").trim();
  if (!title) continue;
  if (!trialsByPathway.has(mg)) trialsByPathway.set(mg, []);
  trialsByPathway.get(mg).push(title.length > 72 ? title.slice(0, 69).trimEnd() + "…" : title);
}
// per-paper gene attribution (PMID -> set of symbols)
const genesByPmid = new Map();
for (const lk of readJsonl("data/processed/shared/topic_evidence_links.jsonl")) {
  if (lk.evidence_type !== "gene") continue;
  const sym = lk.provenance && lk.provenance.gene_symbol;
  if (!sym) continue;
  for (const s of lk.supporting_paper_ids || []) {
    const pm = pmidOf(String(s));
    if (!genesByPmid.has(pm)) genesByPmid.set(pm, new Set());
    genesByPmid.get(pm).add(sym);
  }
}

// --- build graph ------------------------------------------------------------
console.log("building graph…");
const graph = new Graph({ type: "undirected", multi: false });
papers.forEach((p, i) => {
  // deterministic golden-angle spiral seed so ForceAtlas2 starts from a stable, spread state
  const a = i * 2.399963229728653;
  const r = 10 * Math.sqrt(i + 1);
  graph.addNode(p.paper_id, { x: r * Math.cos(a), y: r * Math.sin(a) });
});
let added = 0;
const bestEdge = new Map(); // node -> [weight, other] strongest incident edge across ALL edges
const noteBest = (a, b, w) => { const cur = bestEdge.get(a); if (!cur || w > cur[0]) bestEdge.set(a, [w, b]); };
for (const e of edgesRaw) {
  const s = e.source_paper_id, t = e.target_paper_id, w = e.weight || 0;
  if (s === t || !graph.hasNode(s) || !graph.hasNode(t)) continue;
  noteBest(s, t, w); noteBest(t, s, w);
  if (w < EDGE_MIN_WEIGHT || graph.hasEdge(s, t)) continue;
  graph.addEdge(s, t, { weight: w, w0: w }); // w0 = true weight (kept for pruning); weight is tuned for layout
  added++;
}
// anchor edgeless papers to their single strongest link so nothing is orphaned
let anchored = 0;
graph.forEachNode((n) => {
  if (graph.degree(n) > 0) return;
  const b = bestEdge.get(n);
  if (b && !graph.hasEdge(n, b[1])) { graph.addEdge(n, b[1], { weight: b[0], w0: b[0] }); anchored++; }
});
let isolated = 0;
graph.forEachNode((n) => { if (graph.degree(n) === 0) isolated++; });
console.log(`  graph: ${graph.order} nodes, ${added} strong + ${anchored} anchor edges, ${isolated} still isolated`);

// --- communities (Louvain on the coupling graph) ----------------------------
console.log(`detecting communities (resolution=${RESOLUTION})…`);
louvain.assign(graph, { resolution: RESOLUTION, getEdgeWeight: "weight" });

// merge sub-threshold communities into neighbour-majority large community; else "other"
function communitySizes() {
  const m = new Map();
  graph.forEachNode((_, a) => m.set(a.community, (m.get(a.community) || 0) + 1));
  return m;
}
let sizes = communitySizes();
const large = new Set([...sizes].filter(([, n]) => n >= MIN_COMMUNITY).map(([c]) => c));
graph.forEachNode((node, attr) => {
  if (large.has(attr.community)) return;
  const votes = new Map();
  graph.forEachNeighbor(node, (_, nattr) => {
    if (large.has(nattr.community)) votes.set(nattr.community, (votes.get(nattr.community) || 0) + 1);
  });
  let best = null, bestN = 0;
  for (const [c, n] of votes) if (n > bestN) { best = c; bestN = n; }
  attr.community = best ?? "other";
});
sizes = communitySizes();
console.log(`  ${[...sizes].filter(([c]) => c !== "other").length} communities (+ ${sizes.get("other") || 0} 'other' papers)`);

// --- layout (ForceAtlas2) ---------------------------------------------------
// Attenuate edges that cross communities so clusters separate into islands
// instead of collapsing into one ball (coupling has many cross-topic links).
const INTER_ATTEN = 0.05;
graph.forEachEdge((edge, attr, s, t) => {
  const same = graph.getNodeAttribute(s, "community") === graph.getNodeAttribute(t, "community");
  attr.weight = same ? attr.w0 : attr.w0 * INTER_ATTEN;
});
console.log(`running ForceAtlas2 (${FA2_ITERATIONS} iterations)…`);
const settings = forceAtlas2.inferSettings(graph);
settings.barnesHutOptimize = true;
settings.linLogMode = true;                    // clusters read as separated clumps
settings.outboundAttractionDistribution = true; // push hubs out, spread the map
settings.gravity = 0.35;                        // weak central pull so communities separate
settings.scalingRatio = 28;                     // strong repulsion => spread, not a ball
settings.strongGravityMode = false;
settings.edgeWeightInfluence = 1;
forceAtlas2.assign(graph, { iterations: FA2_ITERATIONS, settings });

// --- community-aware separation --------------------------------------------
// FA2 gives good LOCAL structure but leaves everything in one ball. Keep each
// community's internal shape, but translate whole communities apart so they
// read as separate islands. Satellite "other" papers follow the community of
// their strongest neighbour so they ride along with their nearest island.
console.log("separating communities into islands…");
const mean = (a) => a.reduce((x, y) => x + y, 0) / (a.length || 1);
const pct = (a, p) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : 0; };
const effComm = new Map();
graph.forEachNode((n, a) => {
  if (a.community !== "other") return effComm.set(n, a.community);
  const b = bestEdge.get(n);
  const bc = b ? graph.getNodeAttribute(b[1], "community") : null;
  effComm.set(n, bc && bc !== "other" ? bc : "other");
});
const grp = new Map(); // effComm -> {nodes,xs,ys}
graph.forEachNode((n, a) => {
  const c = effComm.get(n);
  if (!grp.has(c)) grp.set(c, { nodes: [], xs: [], ys: [] });
  const g = grp.get(c); g.nodes.push(n); g.xs.push(a.x); g.ys.push(a.y);
});
const disks = [];
for (const [c, g] of grp) {
  if (c === "other") continue;
  const cx = mean(g.xs), cy = mean(g.ys);
  const d = g.xs.map((x, i) => Math.hypot(x - cx, g.ys[i] - cy));
  disks.push({ c, cx, cy, tx: cx, ty: cy, r: Math.max(30, pct(d, 0.82)) });
}
const PAD = 55;
for (let it = 0; it < 500; it++) {
  let moved = 0;
  for (let i = 0; i < disks.length; i++) for (let j = i + 1; j < disks.length; j++) {
    const A = disks[i], B = disks[j];
    let dx = B.tx - A.tx, dy = B.ty - A.ty, dist = Math.hypot(dx, dy) || 0.01;
    const need = A.r + B.r + PAD - dist;
    if (need > 0) {
      const ux = dx / dist, uy = dy / dist, sh = need / 2;
      A.tx -= ux * sh; A.ty -= uy * sh; B.tx += ux * sh; B.ty += uy * sh; moved++;
    }
  }
  if (!moved) break;
}
const delta = new Map(disks.map((d) => [d.c, [d.tx - d.cx, d.ty - d.cy]]));
graph.forEachNode((n, a) => {
  const d = delta.get(effComm.get(n));
  if (d) { a.x += d[0]; a.y += d[1]; }
});

// normalize to a stable ~1000-unit span so the app's fit + node sizes are predictable
{
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  graph.forEachNode((n, a) => { minX = Math.min(minX, a.x); maxX = Math.max(maxX, a.x); minY = Math.min(minY, a.y); maxY = Math.max(maxY, a.y); });
  const k = 1000 / (Math.max(maxX - minX, maxY - minY) || 1);
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  graph.forEachNode((n, a) => { a.x = (a.x - cx) * k; a.y = (a.y - cy) * k; });
}

// --- community metadata (label, colour, Track B evidence) -------------------
console.log("labelling communities + joining Track B…");
const members = new Map(); // community -> [paperIndex]
papers.forEach((p, i) => {
  const c = graph.getNodeAttribute(p.paper_id, "community");
  if (!members.has(c)) members.set(c, []);
  members.get(c).push(i);
});

// global document frequency for tf-idf labelling
const df = new Map();
const paperTokens = papers.map((p) => {
  const uniq = new Set(tokenize(p.title));
  for (const tok of uniq) df.set(tok, (df.get(tok) || 0) + 1);
  return uniq;
});
const N = papers.length;

// order communities by size (largest first) for stable colour assignment; "other" last
const ordered = [...members.keys()].filter((c) => c !== "other").sort((a, b) => members.get(b).length - members.get(a).length);
if (members.has("other")) ordered.push("other");

// gene "document frequency" across communities, so a gene present in every
// community (e.g. APOE) contributes little to a community's identity.
const geneCommDf = new Map();
for (const c of members.keys()) {
  if (c === "other") continue;
  const seen = new Set();
  for (const i of members.get(c)) {
    const set = genesByPmid.get(pmidOf(papers[i].paper_id));
    if (set) for (const s of set) seen.add(s);
  }
  for (const s of seen) geneCommDf.set(s, (geneCommDf.get(s) || 0) + 1);
}
const nComm = [...members.keys()].filter((c) => c !== "other").length || 1;
const geneIdf = (s) => Math.log((nComm + 1) / ((geneCommDf.get(s) || 0) + 1)) + 1;

const communityMeta = new Map(); // community -> {cluster_id, label, color, pathway_group, ...}
ordered.forEach((c, idx) => {
  const idxs = members.get(c);
  const isOther = c === "other";

  // tf-idf label from member titles
  const tf = new Map();
  for (const i of idxs) for (const tok of paperTokens[i]) tf.set(tok, (tf.get(tok) || 0) + 1);
  const label = isOther ? "other / unclustered" : ([...tf.entries()]
    .map(([tok, f]) => [tok, f * Math.log(N / (df.get(tok) || 1))])
    .sort((a, b) => b[1] - a[1]).slice(0, 3).map(([tok]) => tok).join(" / ") || `topic ${idx}`);

  // genes: aggregate per-paper gene attribution across members
  const geneCount = new Map();
  for (const i of idxs) {
    const set = genesByPmid.get(pmidOf(papers[i].paper_id));
    if (set) for (const s of set) geneCount.set(s, (geneCount.get(s) || 0) + 1);
  }
  const topGenes = [...geneCount.entries()].sort((a, b) => b[1] - a[1]).slice(0, TOP_GENES).map(([s]) => s);

  // pathway_group: dominant pathway among the community's genes, weighted by
  // gene idf so genes shared across all communities (APOE) don't win everywhere.
  const pgCount = new Map();
  for (const [s, n] of geneCount) { const pg = genePathway.get(s); if (pg) pgCount.set(pg, (pgCount.get(pg) || 0) + n * geneIdf(s)); }
  let pathway_group = "unclassified", bestN = 0;
  for (const [pg, n] of pgCount) if (n > bestN) { pathway_group = pg; bestN = n; }

  // scores: mean gene support over the community's genes
  const gs = [], fs_ = [];
  for (const s of topGenes) { const sc = geneScore.get(s); if (sc) { if (sc.genetic_support != null) gs.push(sc.genetic_support); if (sc.functional_support != null) fs_.push(sc.functional_support); } }
  const mean = (a) => a.length ? Math.round((a.reduce((x, y) => x + y, 0) / a.length) * 1e4) / 1e4 : undefined;
  const scores = {};
  if (mean(gs) !== undefined) scores.genetic_support = mean(gs);
  if (mean(fs_) !== undefined) scores.functional_support = mean(fs_);

  // trials matched to the community's pathway_group via mechanism_group
  const trials = pathway_group !== "unclassified" ? (trialsByPathway.get(pathway_group) || []).slice(0, TOP_TRIALS) : [];

  communityMeta.set(c, {
    cluster_id: isOther ? "other" : `c${idx}`,
    label, color: isOther ? OTHER_COLOR : PALETTE[idx % PALETTE.length],
    pathway_group: isOther ? "unclassified" : pathway_group,
    top_genes: topGenes,
    trials, scores,
  });
});

// --- assemble output papers + clusters --------------------------------------
console.log("assembling output…");
const idToIndex = new Map();
const outPapers = papers.map((p, i) => {
  const c = graph.getNodeAttribute(p.paper_id, "community");
  const meta = communityMeta.get(c);
  idToIndex.set(p.paper_id, i);
  const m = p.metrics || {};
  return {
    paper_id: p.paper_id,
    pmid: p.pmid ?? null,
    doi: p.doi ?? null,
    title: p.title || "(untitled)",
    year: p.year,
    journal: p.journal ?? null,
    authors: (p.authors || []).map((a) => (typeof a === "string" ? a : (a.name || String(a)))),
    cluster_id: meta.cluster_id,
    x: Math.round(graph.getNodeAttribute(p.paper_id, "x") * 100) / 100,
    y: Math.round(graph.getNodeAttribute(p.paper_id, "y") * 100) / 100,
    genes: [...(genesByPmid.get(pmidOf(p.paper_id)) || [])].sort(),
    pathway_group: meta.pathway_group,
    trials: [],
    metrics: {
      citation_count: m.citation_count ?? null,
      relative_citation_ratio: m.relative_citation_ratio ?? null,
      apt: m.apt ?? null,
      is_clinical: m.is_clinical ?? null,
    },
    url: p.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/` : null,
  };
});

const outClusters = ordered.map((c) => {
  const meta = communityMeta.get(c);
  const idxs = members.get(c);
  const xs = idxs.map((i) => outPapers[i].x), ys = idxs.map((i) => outPapers[i].y);
  const years = idxs.map((i) => outPapers[i].year).filter((y) => Number.isInteger(y));
  return {
    topic_id: meta.cluster_id,
    label: meta.label,
    color: meta.color,
    pathway_group: meta.pathway_group,
    top_genes: meta.top_genes,
    trials: meta.trials,
    paper_count: idxs.length,
    centroid: { x: Math.round((xs.reduce((a, b) => a + b, 0) / xs.length) * 100) / 100, y: Math.round((ys.reduce((a, b) => a + b, 0) / ys.length) * 100) / 100 },
    year_start: years.length ? Math.min(...years) : null,
    year_end: years.length ? Math.max(...years) : null,
    scores: meta.scores,
  };
});

// --- pruned edge list for drawing (top-K strongest per node) ----------------
console.log("pruning edges for drawing…");
const keep = new Set();
graph.forEachNode((node) => {
  const nbrs = [];
  graph.forEachEdge(node, (edge, attr, s, t) => nbrs.push([attr.w0, s === node ? t : s]));
  nbrs.sort((a, b) => b[0] - a[0]);
  for (const [, other] of nbrs.slice(0, EDGES_PER_NODE)) {
    const a = idToIndex.get(node), b = idToIndex.get(other);
    keep.add(a < b ? `${a}|${b}` : `${b}|${a}`);
  }
});
const outEdges = [...keep].map((k) => k.split("|").map(Number));
console.log(`  ${outEdges.length} edges kept for drawing`);

const nGeneP = outPapers.filter((p) => p.genes.length).length;
const data = {
  generated_note: `Real coupling-graph map: ForceAtlas2 layout + Louvain communities over ${added} edges. ` +
    `${outPapers.length} papers, ${outClusters.filter((c) => c.topic_id !== "other").length} communities; ` +
    `${nGeneP} papers with gene links. Built by scripts/build-map-data.mjs from Track A + Track B.`,
  disease: "Alzheimer disease / dementia (ADRD)",
  coordinate_space: "ForceAtlas2 layout of the bibliographic-coupling graph (auto-fit by the app)",
  clusters: outClusters,
  papers: outPapers,
  edges: outEdges,
};
const OUT = P("web/public/data/map_data.json");
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(data));
console.log(`wrote ${path.relative(ROOT, OUT)}  (${(fs.statSync(OUT).size / 1e6).toFixed(2)} MB)`);
console.log(`  papers=${outPapers.length} clusters=${outClusters.length} edges=${outEdges.length} papers_with_genes=${nGeneP}`);
