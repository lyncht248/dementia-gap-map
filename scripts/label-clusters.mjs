// Two-tier cluster labelling (post-build enrichment).
//
// Reads the built map (web/public/data/map_data.json) and one or more cached,
// LLM-authored label lenses (scripts/cluster_labels.<id>.json). Each lens names
// the SAME clusters differently (theme / pathway / subtype ...); the map layout
// never changes — only the labels. Emits:
//
//   web/public/data/label_lenses.json  — every lens, resolved (coarse groups
//        with anchor/colour/count + fine label map). The app overlays these at
//        runtime so a viewer can switch lenses without a rebuild.
//   web/public/data/map_data.json      — the DEFAULT lens (first in LENSES)
//        baked in (cluster.label, coarse_id, coarse_clusters) so the map still
//        renders if label_lenses.json is absent.
//
// Deterministic + idempotent: run after scripts/build-map-data.mjs.
//   node scripts/label-clusters.mjs

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const P = (...p) => path.join(ROOT, ...p);
const MAP = P("web/public/data/map_data.json");

// Order matters: the first lens is baked into map_data.json as the default.
const LENSES = ["theme", "pathway", "subtype"];

const data = JSON.parse(fs.readFileSync(MAP, "utf8"));
const clusterById = new Map(data.clusters.map((c) => [c.topic_id, c]));

// Resolve one lens file into { id, name, coarse_clusters, fine, coarse_of }.
function resolveLens(id) {
  const cfg = JSON.parse(fs.readFileSync(P(`scripts/cluster_labels.${id}.json`), "utf8"));
  const coarse_of = {};
  const coarse_clusters = [];
  for (const grp of cfg.coarse || []) {
    const members = (grp.members || []).map((m) => clusterById.get(m)).filter(Boolean);
    if (!members.length) continue;
    for (const m of members) coarse_of[m.topic_id] = grp.coarse_id;
    // anchor the coarse label on the largest member so it sits on solid ground
    const anchor = members.reduce((a, b) => (b.paper_count > a.paper_count ? b : a));
    coarse_clusters.push({
      coarse_id: grp.coarse_id,
      label: grp.label,
      color: anchor.color,
      centroid: { ...anchor.centroid },
      paper_count: members.reduce((n, m) => n + m.paper_count, 0),
      fine_ids: members.map((m) => m.topic_id),
    });
  }
  coarse_clusters.sort((a, b) => b.paper_count - a.paper_count);

  const uncovered = data.clusters
    .filter((c) => c.topic_id !== "other" && !(c.topic_id in coarse_of))
    .map((c) => c.topic_id);
  if (uncovered.length) console.warn(`  [${id}] WARNING: no coarse group for: ${uncovered.join(", ")}`);

  return { id: cfg.id || id, name: cfg.name || id, coarse_clusters, fine: cfg.fine || {}, coarse_of };
}

const lenses = LENSES.map(resolveLens);

// --- write the runtime lens file --------------------------------------------
const LENS_OUT = P("web/public/data/label_lenses.json");
fs.writeFileSync(LENS_OUT, JSON.stringify({ default: lenses[0].id, lenses }));

// --- bake the default lens into map_data.json --------------------------------
const def = lenses[0];
for (const c of data.clusters) {
  if (def.fine[c.topic_id]) c.label = def.fine[c.topic_id];
  c.coarse_id = def.coarse_of[c.topic_id] ?? null;
}
for (const p of data.papers) p.coarse_id = def.coarse_of[p.cluster_id] ?? null;
data.coarse_clusters = def.coarse_clusters;
fs.writeFileSync(MAP, JSON.stringify(data));

// --- report ------------------------------------------------------------------
console.log(`resolved ${lenses.length} lenses -> ${path.relative(ROOT, LENS_OUT)}`);
for (const l of lenses) {
  console.log(`  ${l.id.padEnd(8)} "${l.name}" — ${l.coarse_clusters.length} coarse:`);
  for (const g of l.coarse_clusters) console.log(`      ${g.label.padEnd(26)} [${g.fine_ids.join(" ")}]  n=${g.paper_count}`);
}
console.log(`baked default lens "${def.id}" into ${path.relative(ROOT, MAP)}`);
