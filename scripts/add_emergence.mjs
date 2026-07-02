// Patch per-cluster emergence into an already-built map_data.json WITHOUT
// touching the layout (papers, positions, edges, labels, colours untouched) —
// only each cluster gains an `emergence` field. Run after build-map-data.mjs,
// or standalone against the committed map_data.json.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { computeEmergence } from "./emergence.mjs";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const FILE = path.join(ROOT, "web/public/data/map_data.json");

const data = JSON.parse(fs.readFileSync(FILE, "utf8"));
computeEmergence(data.papers, data.clusters);
fs.writeFileSync(FILE, JSON.stringify(data));

const ranked = data.clusters
  .filter((c) => c.emergence)
  .sort((a, b) => b.emergence.score - a.emergence.score);
console.log(`patched emergence onto ${ranked.length} clusters`);
for (const c of ranked.slice(0, 5))
  console.log(`  ${c.label.padEnd(16)} score=${c.emergence.score} pct_new=${c.emergence.pct_new} growth=${c.emergence.growth}x rcr=${c.emergence.mean_rcr}`);
