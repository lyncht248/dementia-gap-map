#!/usr/bin/env node
/**
 * gen-mock-data.mjs
 *
 * Generates a synthetic `map_data.json` for the Dementia Gap Map frontend MVP.
 *
 * This is placeholder data so the UI is demonstrable before the real
 * Track A / Track B processed outputs exist. It follows the shapes in
 * shared/schemas (paper.schema.json, topic_cluster.schema.json) closely
 * enough that real exports can drop in later. Everything is deterministic
 * (seeded PRNG) so the map is stable across builds.
 *
 * Output: web/public/data/map_data.json
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, "../web/public/data/map_data.json");

// ---- deterministic PRNG (mulberry32) --------------------------------------
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rng = mulberry32(20260701);
const rand = () => rng();
const randn = () => {
  // Box-Muller
  const u = 1 - rand();
  const v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
};
const pick = (arr) => arr[Math.floor(rand() * arr.length)];
const pickN = (arr, n) => {
  const copy = [...arr];
  const out = [];
  while (out.length < n && copy.length) out.push(copy.splice(Math.floor(rand() * copy.length), 1)[0]);
  return out;
};
const clamp01 = (x) => Math.max(0, Math.min(1, x));

// ---- cluster definitions (dementia / AD genetics topics) ------------------
// centroid coords live in an arbitrary 0..1000 space; the app auto-fits.
const CLUSTERS = [
  {
    topic_id: "t-amyloid",
    label: "Amyloid-β & Secretase Biology",
    color: "#7c5cbf",
    pathway_group: "amyloid",
    top_genes: ["APP", "PSEN1", "PSEN2", "BACE1", "ADAM10"],
    trials: ["lecanemab", "donanemab", "aducanumab", "gantenerumab"],
    centroid: { x: 300, y: 300 }, spread: 55, weight: 1.4, base_year: 2016,
    scores: { emergence: 0.35, genetic_support: 0.9, functional_support: 0.7, clinical_translation: 0.95, clinical_saturation: 0.92 },
  },
  {
    topic_id: "t-tau",
    label: "Tau Pathology & Neurofibrillary Tangles",
    color: "#4e79a7",
    pathway_group: "tau",
    top_genes: ["MAPT", "STX6", "EIF2AK3", "MOBP"],
    trials: ["anti-tau antibody", "tau vaccine", "OGA inhibitor"],
    centroid: { x: 520, y: 360 }, spread: 48, weight: 1.1, base_year: 2017,
    scores: { emergence: 0.5, genetic_support: 0.75, functional_support: 0.6, clinical_translation: 0.6, clinical_saturation: 0.55 },
  },
  {
    topic_id: "t-apoe-lipid",
    label: "APOE & Lipid Metabolism",
    color: "#8c6d31",
    pathway_group: "lipid_metabolism",
    top_genes: ["APOE", "ABCA7", "CLU", "SORL1", "ABCA1"],
    trials: ["APOE-targeted therapy", "statin repurposing"],
    centroid: { x: 250, y: 470 }, spread: 40, weight: 0.8, base_year: 2015,
    scores: { emergence: 0.4, genetic_support: 0.98, functional_support: 0.7, clinical_translation: 0.3, clinical_saturation: 0.35 },
  },
  {
    topic_id: "t-microglia",
    label: "Microglia & Innate Immunity",
    color: "#e15759",
    pathway_group: "microglia_immune",
    top_genes: ["TREM2", "PLCG2", "ABI3", "INPP5D", "CD33", "MS4A6A", "SPI1"],
    trials: ["anti-TREM2 agonist", "microglial modulator"],
    centroid: { x: 640, y: 250 }, spread: 52, weight: 1.2, base_year: 2019,
    scores: { emergence: 0.85, genetic_support: 0.9, functional_support: 0.8, clinical_translation: 0.25, clinical_saturation: 0.2 },
  },
  {
    topic_id: "t-endosomal",
    label: "Endosomal Trafficking",
    color: "#59a14f",
    pathway_group: "endocytosis",
    top_genes: ["BIN1", "PICALM", "SORL1", "CD2AP", "RIN3"],
    trials: [],
    centroid: { x: 470, y: 560 }, spread: 42, weight: 0.7, base_year: 2018,
    scores: { emergence: 0.6, genetic_support: 0.8, functional_support: 0.5, clinical_translation: 0.1, clinical_saturation: 0.1 },
  },
  {
    topic_id: "t-vascular",
    label: "Vascular Contributions to Dementia",
    color: "#af7aa1",
    pathway_group: "vascular",
    top_genes: ["NOTCH3", "HTRA1", "COL4A1"],
    trials: ["antihypertensive prevention", "anticoagulation"],
    centroid: { x: 180, y: 660 }, spread: 46, weight: 0.7, base_year: 2016,
    scores: { emergence: 0.45, genetic_support: 0.5, functional_support: 0.4, clinical_translation: 0.55, clinical_saturation: 0.6 },
  },
  {
    topic_id: "t-biomarker",
    label: "Fluid & Imaging Biomarkers",
    color: "#f28e2b",
    pathway_group: "diagnostic",
    top_genes: ["MAPT", "APP", "GFAP", "NEFL"],
    trials: ["plasma p-tau217 assay", "amyloid PET", "CSF biomarker panel"],
    centroid: { x: 700, y: 470 }, spread: 50, weight: 1.0, base_year: 2020,
    scores: { emergence: 0.8, genetic_support: 0.3, functional_support: 0.4, clinical_translation: 0.7, clinical_saturation: 0.5 },
  },
  {
    topic_id: "t-neuroinflam",
    label: "Neuroinflammation & Complement",
    color: "#76b7b2",
    pathway_group: "inflammation",
    top_genes: ["CR1", "CLU", "C1QA", "TNF", "IL1B"],
    trials: ["anti-inflammatory", "complement inhibitor"],
    centroid: { x: 560, y: 150 }, spread: 44, weight: 0.8, base_year: 2018,
    scores: { emergence: 0.65, genetic_support: 0.6, functional_support: 0.55, clinical_translation: 0.3, clinical_saturation: 0.3 },
  },
  {
    topic_id: "t-synaptic",
    label: "Synaptic Dysfunction & Plasticity",
    color: "#ff9da7",
    pathway_group: "synaptic",
    top_genes: ["FYN", "PTK2B", "GRIN2B", "DLG4"],
    trials: ["synaptic protectant", "NMDA modulator"],
    centroid: { x: 400, y: 180 }, spread: 40, weight: 0.6, base_year: 2017,
    scores: { emergence: 0.5, genetic_support: 0.45, functional_support: 0.5, clinical_translation: 0.4, clinical_saturation: 0.35 },
  },
  {
    topic_id: "t-metabolic",
    label: "Metabolic & Mitochondrial Dysfunction",
    color: "#9c755f",
    pathway_group: "metabolism",
    top_genes: ["GRN", "TOMM40", "ABCA1"],
    trials: ["semaglutide", "insulin sensitizer", "metabolic intervention"],
    centroid: { x: 330, y: 720 }, spread: 44, weight: 0.7, base_year: 2019,
    scores: { emergence: 0.7, genetic_support: 0.4, functional_support: 0.45, clinical_translation: 0.5, clinical_saturation: 0.4 },
  },
  {
    topic_id: "t-eqtl",
    label: "Single-cell & Brain eQTL Mapping",
    color: "#bab0ac",
    pathway_group: "functional",
    top_genes: ["BIN1", "TREM2", "PICALM", "MS4A6A", "APOE"],
    trials: [],
    centroid: { x: 760, y: 300 }, spread: 48, weight: 0.9, base_year: 2021,
    scores: { emergence: 0.9, genetic_support: 0.7, functional_support: 0.95, clinical_translation: 0.05, clinical_saturation: 0.05 },
  },
  {
    topic_id: "t-clinical",
    label: "Therapeutics & Clinical Trials",
    color: "#499894",
    pathway_group: "clinical",
    top_genes: ["APP", "MAPT", "APOE"],
    trials: ["lecanemab", "donanemab", "blarcamesine", "masitinib"],
    centroid: { x: 620, y: 640 }, spread: 50, weight: 1.0, base_year: 2020,
    scores: { emergence: 0.6, genetic_support: 0.4, functional_support: 0.35, clinical_translation: 0.9, clinical_saturation: 0.85 },
  },
];

// ---- text corpora for synthetic titles/metadata ---------------------------
const JOURNALS = [
  "Nature Genetics", "Nature Neuroscience", "Neuron", "Nature Medicine",
  "Alzheimer's & Dementia", "Molecular Neurodegeneration", "Acta Neuropathologica",
  "Brain", "The Lancet Neurology", "JAMA Neurology", "Cell", "Science Translational Medicine",
];
const SURNAMES = [
  "Bellenguez", "Lambert", "Kunkle", "Jansen", "Wightman", "Schwartzentruber",
  "Sims", "Karch", "Naj", "Marioni", "Novikova", "Andrews", "de Rojas",
  "Holstege", "Bertram", "Escott-Price", "Deming", "Yang", "Zhou", "Chen",
  "Nguyen", "Patel", "Garcia", "Muller", "Rossi", "Tanaka", "Kim",
];
const PROCESSES = {
  amyloid: ["amyloid-β aggregation", "Aβ clearance", "γ-secretase processing", "plaque deposition"],
  tau: ["tau phosphorylation", "neurofibrillary tangle formation", "tau propagation", "MAPT splicing"],
  lipid_metabolism: ["lipid transport", "cholesterol homeostasis", "lipoprotein biology", "APOE isoform effects"],
  microglia_immune: ["microglial activation", "phagocytosis", "innate immune signaling", "TREM2 signaling"],
  endocytosis: ["endosomal trafficking", "clathrin-mediated endocytosis", "endolysosomal function"],
  vascular: ["cerebral small vessel disease", "blood-brain barrier integrity", "cerebrovascular pathology"],
  diagnostic: ["plasma biomarker performance", "amyloid PET quantification", "CSF p-tau dynamics"],
  inflammation: ["complement activation", "neuroinflammatory response", "cytokine signaling"],
  synaptic: ["synaptic loss", "long-term potentiation", "dendritic spine density"],
  metabolism: ["brain glucose metabolism", "mitochondrial dysfunction", "insulin resistance"],
  functional: ["cell-type-specific expression", "eQTL colocalization", "single-nucleus profiling"],
  clinical: ["disease-modifying treatment", "cognitive decline", "trial efficacy and safety"],
};
const TITLE_TEMPLATES = [
  (g, p) => `Genome-wide association study implicates ${g} in ${p}`,
  (g, p) => `${g} regulates ${p} in Alzheimer's disease`,
  (g, p) => `Role of ${g} in ${p} and dementia risk`,
  (g, p) => `Single-nucleus analysis links ${g} to ${p}`,
  (g, p) => `${p}: a mechanistic study of ${g} in neurodegeneration`,
  (g, p) => `Rare variants in ${g} modulate ${p}`,
  (g, p) => `Longitudinal evidence for ${p} driven by ${g}`,
  (g, p) => `Therapeutic targeting of ${g} to modify ${p}`,
];

function makeTitle(cluster) {
  const g = pick(cluster.top_genes.length ? cluster.top_genes : ["APOE"]);
  const p = pick(PROCESSES[cluster.pathway_group] || ["Alzheimer's disease pathology"]);
  return pick(TITLE_TEMPLATES)(g, p);
}
function makeAuthors() {
  const n = 2 + Math.floor(rand() * 4);
  const names = pickN(SURNAMES, n).map((s) => `${s} ${String.fromCharCode(65 + Math.floor(rand() * 26))}`);
  return names;
}

// ---- generate papers ------------------------------------------------------
const TOTAL = 1200;
const totalWeight = CLUSTERS.reduce((s, c) => s + c.weight, 0);
const papers = [];
let idn = 1;
const thisYear = 2026;

for (const cluster of CLUSTERS) {
  const count = Math.max(20, Math.round((cluster.weight / totalWeight) * TOTAL));
  for (let i = 0; i < count; i++) {
    const x = cluster.centroid.x + randn() * cluster.spread;
    const y = cluster.centroid.y + randn() * cluster.spread;
    // year: skew toward base_year..2026, emergent clusters skew more recent
    const skew = cluster.scores.emergence;
    const yr = Math.min(
      thisYear,
      Math.max(2010, Math.round(cluster.base_year + rand() * (thisYear - cluster.base_year) * (0.6 + skew * 0.6)))
    );
    const citation_count = Math.round(Math.abs(randn()) * 60 + rand() * 40);
    const rcr = +(clamp01(Math.abs(randn()) * 0.6 + 0.2) * 3).toFixed(2);
    const apt = +clamp01(cluster.scores.clinical_translation * 0.6 + rand() * 0.4).toFixed(2);
    const is_clinical = rand() < cluster.scores.clinical_translation * 0.5;
    const genes = pickN(cluster.top_genes.length ? cluster.top_genes : ["APOE"], Math.min(2, cluster.top_genes.length || 1));
    const trials = rand() < 0.25 && cluster.trials.length ? pickN(cluster.trials, 1) : [];
    const pmid = 30000000 + Math.floor(rand() * 8000000);
    papers.push({
      paper_id: `pmid:${pmid}`,
      pmid: String(pmid),
      doi: `10.1038/s41588-0${20 + Math.floor(rand() * 6)}-${1000 + Math.floor(rand() * 8999)}-${Math.floor(rand() * 9)}`,
      title: makeTitle(cluster),
      year: yr,
      journal: pick(JOURNALS),
      authors: makeAuthors(),
      cluster_id: cluster.topic_id,
      x: +x.toFixed(2),
      y: +y.toFixed(2),
      genes,
      pathway_group: cluster.pathway_group,
      trials,
      metrics: {
        citation_count,
        relative_citation_ratio: rcr,
        apt,
        is_clinical,
      },
      url: `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`,
    });
    idn++;
  }
}

// recompute cluster centroids from actual points (for label placement)
const clustersOut = CLUSTERS.map((c) => {
  const pts = papers.filter((p) => p.cluster_id === c.topic_id);
  const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  const years = pts.map((p) => p.year);
  return {
    topic_id: c.topic_id,
    label: c.label,
    color: c.color,
    pathway_group: c.pathway_group,
    top_genes: c.top_genes,
    trials: c.trials,
    paper_count: pts.length,
    centroid: { x: +cx.toFixed(2), y: +cy.toFixed(2) },
    year_start: Math.min(...years),
    year_end: Math.max(...years),
    scores: c.scores,
  };
});

const out = {
  generated_note:
    "SYNTHETIC PLACEHOLDER DATA generated by scripts/gen-mock-data.mjs. " +
    "Replace with real Track A/B exports (data/app/map_data.json) when available.",
  generated_seed: 20260701,
  disease: "Alzheimer disease / dementia",
  coordinate_space: "arbitrary 2D projection (auto-fit by the app)",
  clusters: clustersOut,
  papers,
};

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify(out));
console.log(`Wrote ${papers.length} papers across ${clustersOut.length} clusters -> ${OUT}`);
