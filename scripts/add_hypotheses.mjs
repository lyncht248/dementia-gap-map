// Patch a "mechanistic hypotheses" layer onto the already-built atlas.json
// WITHOUT touching the layout (points / ids / edges / majors / minors stay
// exactly as they were). This lets the map offer a second framing of the same
// literature: instead of the 10 disease regions, the 8 Alzheimer's "cure"
// mechanistic bets (amyloid, tau, lipid/APOE, microglia, endocytosis, synaptic,
// vascular, epigenetic).
//
// Why a separate patch (mirrors add_emergence.mjs): atlas.json is built from the
// Qwen embedding by scripts/build_atlas.py, which has no translational evidence.
// The per-paper mechanism assignment already lives in atlas_feed.json
// (pathway_group, derived from each paper's own genes via gene_pathway.csv), and
// the pathway-level metrics live in the Track B rollup. This joins the two onto
// atlas.json so atlasRender.ts stays self-contained (one fetch).
//
// Adds:
//   data.hypotheses : the 8 bets, each with its label, a short on-canvas label,
//                     a categorical colour, the mechanistic hypothesis statement,
//                     its paper count on this map, and the Track B rollup metrics
//                     (gene_count, trial_count, combined_support,
//                     clinical_translation, clinical_saturation, translation_gap).
//                     Ordered by translation_gap desc — the gap-map signal
//                     (strong genetics/biology, little clinical translation up top).
//   data.unclassified_count : papers with no mechanism link (grey background).
//   data.pointHyp   : per-paper hypothesis index, parallel to points/ids
//                     (-1 = unclassified). Index into data.hypotheses.
//
// Run after build_atlas.py + build-atlas-feed.mjs, or standalone against the
// committed atlas.json:  node scripts/add_hypotheses.mjs

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const P = (...p) => path.join(ROOT, ...p);

const ATLAS = P("web/public/atlas/atlas.json");
const FEED = P("web/public/atlas/atlas_feed.json");
const PATHWAYS = P("data/processed/translational-evidence/pathways.jsonl");

// ---- the 8 mechanistic hypotheses -----------------------------------------
// Per-mechanism presentation: short on-canvas label, categorical colour, and the
// one-line "cure" hypothesis. Colours are the data-viz reference categorical
// palette (validated: worst adjacent CVD ΔE 16.2 on white; identity is never
// colour-alone — every mechanism is directly labelled on the map and in the
// panel). Keyed by the pathway_group id used throughout the pipeline.
const HYP = {
  amyloid: {
    short: "Amyloid",
    title: "Amyloid processing",
    color: "#e34948", // red
    statement: "Reduce toxic amyloid production, aggregation, or plaque burden.",
  },
  tau: {
    short: "Tau",
    title: "Tau / neurofibrillary pathology",
    color: "#eb6834", // orange
    statement:
      "Prevent tau phosphorylation, aggregation, spreading, or downstream neuronal toxicity.",
  },
  lipid_metabolism: {
    short: "Lipid / APOE",
    title: "Lipid metabolism / APOE",
    color: "#eda100", // yellow
    statement:
      "Modify lipid handling, ApoE biology, cholesterol transport, or ApoE isoform effects.",
  },
  microglia_immune: {
    short: "Microglia",
    title: "Microglia / innate immunity",
    color: "#4a3aa7", // violet
    statement:
      "Reset damaging or insufficient microglial responses, complement, phagocytosis, or inflammatory tone.",
  },
  endocytosis_endosomal: {
    short: "Endocytosis",
    title: "Endocytosis / endosomal trafficking",
    color: "#2a78d6", // blue
    statement:
      "Restore intracellular trafficking, receptor recycling, autophagy-lysosomal handling, or vesicle biology.",
  },
  synaptic_neuronal: {
    short: "Synaptic",
    title: "Synaptic / neuronal resilience",
    color: "#e87ba4", // magenta
    statement:
      "Preserve synapses, neuronal function, plasticity, and resistance to toxic pathology.",
  },
  vascular: {
    short: "Vascular",
    title: "Vascular / cerebrovascular",
    color: "#1baf7a", // aqua
    statement:
      "Treat vascular dysfunction, blood-brain barrier injury, small-vessel disease, or perfusion.",
  },
  epigenetic_transcription: {
    short: "Epigenetic",
    title: "Epigenetic / transcriptional",
    color: "#008300", // green
    statement:
      "Modify disease-state gene expression programs, chromatin regulation, or cell-state transitions.",
  },
};

const round = (v, n = 4) =>
  v == null || Number.isNaN(v) ? null : Number(Number(v).toFixed(n));

// ---- inputs ----------------------------------------------------------------
const atlas = JSON.parse(fs.readFileSync(ATLAS, "utf8"));
const feed = JSON.parse(fs.readFileSync(FEED, "utf8"));
const pathways = fs
  .readFileSync(PATHWAYS, "utf8")
  .split("\n")
  .filter(Boolean)
  .map((l) => JSON.parse(l));

// mechanism_group -> rollup record (label + scores)
const roll = new Map(pathways.map((p) => [p.mechanism_group, p]));
// paper_id -> its dominant pathway_group (already computed by build-atlas-feed)
const pgOf = new Map(feed.papers.map((p) => [p.paper_id, p.pathway_group]));

// minor (fine cluster) id -> disease major, to AD-bias the label anchors.
const majorOfMinor = new Map(atlas.minors.map((m) => [m.id, m.major]));

// paper coords on THIS map, per mechanism (+ unclassified count). points array
// rows are [px, py, fine_id, year], parallel to ids. We keep, per mechanism,
// both its full footprint and just its Alzheimer's-continent papers — the label
// anchor uses the AD subset (these are the "Alzheimer's cure" bets, so the label
// belongs in the AD continent), while the recolouring still paints the whole
// cross-disease footprint.
const coords = new Map(); // mechanism id -> [[x,y], ...] (all classified)
const adCoords = new Map(); // mechanism id -> [[x,y], ...] (AD continent only)
const adClassified = [];
let unclassified = 0;
atlas.ids.forEach((id, i) => {
  const g = pgOf.get(id);
  if (!g || !HYP[g]) {
    unclassified += 1;
    return;
  }
  const pt = atlas.points[i];
  const xy = [pt[0], pt[1]];
  if (!coords.has(g)) coords.set(g, []);
  coords.get(g).push(xy);
  if (majorOfMinor.get(pt[2]) === "alzheimer") {
    if (!adCoords.has(g)) adCoords.set(g, []);
    adCoords.get(g).push(xy);
    adClassified.push(xy);
  }
});
const counts = new Map([...coords].map(([g, c]) => [g, c.length]));

// Robust label anchor per mechanism, in world display units (same space as the
// disease majors). Within the AD continent a mechanism's papers still scatter,
// so a plain mean is pulled off by fringe papers; we take the median centre,
// drop papers > 0.8 world-units from it, and average the rest. A mechanism with
// too few AD papers (e.g. tau, 1 paper) falls back to the centre of all
// AD-continent classified papers — a neutral spot inside the AD cloud.
const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
const median = (a) => {
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const globalCentre = [
  mean(adClassified.map((c) => c[0])),
  mean(adClassified.map((c) => c[1])),
];
function anchor(id) {
  const pts = adCoords.get(id);
  if (!pts || !pts.length) return globalCentre;
  const mx = median(pts.map((c) => c[0])),
    my = median(pts.map((c) => c[1]));
  const core = pts.filter((c) => Math.hypot(c[0] - mx, c[1] - my) <= 0.8);
  if (core.length < 5) return globalCentre;
  return [mean(core.map((c) => c[0])), mean(core.map((c) => c[1]))];
}

// ---- hypotheses records, ranked by translation gap -------------------------
const hypotheses = Object.keys(HYP)
  .map((id) => {
    const r = roll.get(id);
    const s = r?.scores ?? {};
    const meta = HYP[id];
    const [cx, cy] = anchor(id);
    return {
      id,
      label: meta.title,
      short: meta.short,
      color: meta.color,
      statement: meta.statement,
      count: counts.get(id) || 0,
      x: round(cx, 3),
      y: round(cy, 3),
      gene_count: r?.gene_count ?? null,
      trial_count: s.trial_count ?? 0,
      combined_support: round(s.combined_support),
      clinical_translation: round(s.clinical_translation),
      clinical_saturation: round(s.clinical_saturation),
      translation_gap: round(s.translation_gap),
    };
  })
  .sort((a, b) => (b.translation_gap ?? 0) - (a.translation_gap ?? 0));

// hypothesis id -> index into the ranked array
const idx = new Map(hypotheses.map((h, i) => [h.id, i]));

// per-paper hypothesis index, parallel to points / ids (-1 = unclassified)
const pointHyp = atlas.ids.map((id) => {
  const g = pgOf.get(id);
  return g != null && idx.has(g) ? idx.get(g) : -1;
});

// ---- write -----------------------------------------------------------------
atlas.hypotheses = hypotheses;
atlas.unclassified_count = unclassified;
atlas.pointHyp = pointHyp;
fs.writeFileSync(ATLAS, JSON.stringify(atlas));

const classified = atlas.ids.length - unclassified;
console.log(
  `patched ${hypotheses.length} mechanistic hypotheses onto ${path.relative(ROOT, ATLAS)}`
);
console.log(
  `  ${classified}/${atlas.ids.length} papers classified · ${unclassified} unclassified`
);
console.log("  ranked by translation gap:");
for (const h of hypotheses)
  console.log(
    `    ${h.short.padEnd(12)} gap=${String(h.translation_gap).padEnd(6)} ` +
      `support=${String(h.combined_support).padEnd(6)} trials=${String(h.trial_count).padEnd(4)} ` +
      `genes=${String(h.gene_count).padEnd(4)} papers=${h.count}`
  );
