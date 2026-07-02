// Two-tier cluster labelling (post-build enrichment).
//
// Reads the built map (web/public/data/map_data.json) and a cached, LLM-authored
// label file (scripts/cluster_labels.json), then writes back:
//   - improved FINE labels on each cluster (cluster.label)
//   - a coarse `coarse_id` on each cluster and paper
//   - a top-level `coarse_clusters` array (label anchor, colour, paper_count)
//
// The map layout groups papers by co-citation similarity, so the LLM only NAMES
// the clusters — it never moves them. Coarse groups are the cached member lists
// in cluster_labels.json; everything else (anchors, colours, counts) is derived
// from the data so the script stays general across rebuilds.
//
// Deterministic + idempotent: run after scripts/build-map-data.mjs.
//   node scripts/label-clusters.mjs

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const P = (...p) => path.join(ROOT, ...p);
const MAP = P("web/public/data/map_data.json");
const LABELS = P("scripts/cluster_labels.json");

const data = JSON.parse(fs.readFileSync(MAP, "utf8"));
const cfg = JSON.parse(fs.readFileSync(LABELS, "utf8"));

const clusterById = new Map(data.clusters.map((c) => [c.topic_id, c]));

// --- fine tier: overwrite the weak tf-idf single-word labels -----------------
let relabelled = 0;
for (const [id, label] of Object.entries(cfg.fine || {})) {
  const c = clusterById.get(id);
  if (c && label) { c.label = label; relabelled++; }
}

// --- coarse tier: group fine clusters, derive anchor/colour/count ------------
const coarseOf = new Map(); // fine topic_id -> coarse_id
const coarseClusters = [];
for (const grp of cfg.coarse || []) {
  const members = (grp.members || []).map((id) => clusterById.get(id)).filter(Boolean);
  if (!members.length) continue;
  for (const m of members) coarseOf.set(m.topic_id, grp.coarse_id);

  // anchor the coarse label on the largest member so it sits on solid ground
  const anchor = members.reduce((a, b) => (b.paper_count > a.paper_count ? b : a));
  const paper_count = members.reduce((n, m) => n + m.paper_count, 0);
  coarseClusters.push({
    coarse_id: grp.coarse_id,
    label: grp.label,
    color: anchor.color,
    centroid: { ...anchor.centroid },
    paper_count,
    fine_ids: members.map((m) => m.topic_id),
  });
}
coarseClusters.sort((a, b) => b.paper_count - a.paper_count);

// stamp coarse_id onto clusters and papers (null for unclustered / "other")
for (const c of data.clusters) c.coarse_id = coarseOf.get(c.topic_id) ?? null;
for (const p of data.papers) p.coarse_id = coarseOf.get(p.cluster_id) ?? null;

data.coarse_clusters = coarseClusters;

const covered = data.clusters.filter((c) => c.topic_id !== "other" && !c.coarse_id).map((c) => c.topic_id);
if (covered.length) console.warn(`  WARNING: fine clusters with no coarse group: ${covered.join(", ")}`);

fs.writeFileSync(MAP, JSON.stringify(data));
console.log(`labelled ${relabelled} fine clusters; ${coarseClusters.length} coarse groups`);
for (const g of coarseClusters) console.log(`  ${g.label.padEnd(26)} [${g.fine_ids.join(" ")}]  n=${g.paper_count}`);
console.log(`wrote ${path.relative(ROOT, MAP)}`);
