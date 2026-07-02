// Regenerate curated, single-axis cluster labels with one LLM call.
//
//   Input : web/public/data/map_data.json  (must be built first)
//   Output: scripts/cluster-labels.json     (signature -> {label, sublabel})
//
// Every cluster is named on ONE axis — a research area within dementia genetics,
// at a consistent peer level (the way "ML Theory" and "Robotics" are peers on a
// map of ML papers). The distinguishing gene / method / mechanism specifics go
// into `sublabel`, shown when the map is zoomed in. Labelling all clusters in a
// single request lets the model keep them mutually exclusive and at one level.
//
// Deterministic build stays offline: build-map-data.mjs only READS the cache
// this writes. Run this when clusters change, then rebuild the map.
//
//   ANTHROPIC_API_KEY=... node scripts/label-clusters.mjs
//
// Env: LABEL_MODEL (default claude-sonnet-5), ANTHROPIC_BASE_URL.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const P = (...p) => path.join(ROOT, ...p);

const MODEL = process.env.LABEL_MODEL || "claude-sonnet-5";
const BASE = (process.env.ANTHROPIC_BASE_URL || "https://api.anthropic.com").replace(/\/$/, "");
const KEY = process.env.ANTHROPIC_API_KEY;
if (!KEY) {
  console.error("ANTHROPIC_API_KEY is not set. Export it and re-run.");
  process.exit(1);
}

const map = JSON.parse(fs.readFileSync(P("web/public/data/map_data.json"), "utf8"));

// representative titles per cluster: highest-cited first, deduped
const byCluster = new Map();
for (const p of map.papers) {
  if (!byCluster.has(p.cluster_id)) byCluster.set(p.cluster_id, []);
  byCluster.get(p.cluster_id).push(p);
}
const sampleTitles = (cid, k = 8) =>
  (byCluster.get(cid) || [])
    .slice()
    .sort((a, b) => (b.metrics?.citation_count || 0) - (a.metrics?.citation_count || 0))
    .map((p) => p.title)
    .slice(0, k);

// one payload entry per real cluster (skip the "other" bucket)
const clusters = map.clusters
  .filter((c) => c.topic_id !== "other" && c.signature)
  .map((c) => ({
    signature: c.signature,
    paper_count: c.paper_count,
    top_terms: c.term_hints || [],
    top_genes: c.top_genes || [],
    pathway_group: c.pathway_group,
    sample_titles: sampleTitles(c.topic_id),
  }));

const system = [
  "You label clusters on a 2-D map of research papers matching the query \"dementia AND GWAS\".",
  "The map's clusters are groups of papers found by bibliographic coupling; your job is to name each one.",
  "",
  "Follow these rules exactly:",
  "1. ONE AXIS. Every `label` must be the same KIND of thing: a research area WITHIN dementia genetics, at a consistent, peer level of abstraction — the way \"ML Theory\", \"Robotics\", and \"Alignment\" are peer subfields on a map of machine-learning papers. Do NOT mix kinds (a gene name for one cluster, a disease for another, a method for a third). Pick the research-area framing that reads consistently across ALL clusters.",
  "2. MUTUALLY EXCLUSIVE. No two labels should mean the same thing. You see every cluster at once — differentiate them.",
  "3. `label`: 2-4 words, Title Case, no trailing punctuation. This shows when the map is zoomed out.",
  "4. `sublabel`: the distinguishing specifics for that cluster — the genes, method, or mechanism that set it apart (e.g. \"BIN1 · PICALM · autophagy\"). Use \" · \" as a separator. This shows when the map is zoomed in, so it can carry a second facet a cluster also belongs to. Keep it under ~6 words.",
  "5. Ground labels in the provided titles, top terms, genes, and pathway_group. Do not invent topics not supported by the evidence.",
  "",
  "Return ONLY a JSON object mapping each cluster's exact `signature` string to {\"label\": ..., \"sublabel\": ...}. No prose, no markdown fences.",
].join("\n");

const user = "Clusters:\n" + JSON.stringify(clusters, null, 2);

const res = await fetch(`${BASE}/v1/messages`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    "x-api-key": KEY,
    "anthropic-version": "2023-06-01",
  },
  body: JSON.stringify({
    model: MODEL,
    max_tokens: 2000,
    system,
    messages: [{ role: "user", content: user }],
  }),
});

if (!res.ok) {
  console.error(`Anthropic API ${res.status}: ${await res.text()}`);
  process.exit(1);
}

const body = await res.json();
const text = (body.content || []).map((b) => b.text || "").join("").trim();
const json = text.replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();

let labels;
try {
  labels = JSON.parse(json);
} catch (e) {
  console.error("Model did not return valid JSON:\n" + text);
  process.exit(1);
}

// keep the existing signatures that the model didn't return, and preserve _note
const out = { _note: "Single-axis cluster labels for the dementia-GWAS map. Keyed by the deterministic TF-IDF term signature emitted by build-map-data.mjs. `label` = a research area within dementia genetics at one consistent, peer level (shown zoomed out); `sublabel` = the distinguishing gene / method / mechanism specifics (shown zoomed in). Regenerate with `node scripts/label-clusters.mjs`." };
let n = 0;
for (const c of clusters) {
  const got = labels[c.signature];
  if (got && got.label) {
    out[c.signature] = { label: String(got.label), sublabel: String(got.sublabel || "") };
    n++;
  } else {
    console.warn(`  no label returned for: ${c.signature}`);
  }
}

fs.writeFileSync(P("scripts/cluster-labels.json"), JSON.stringify(out, null, 2) + "\n");
console.log(`wrote scripts/cluster-labels.json  (${n}/${clusters.length} clusters labelled via ${MODEL})`);
console.log("Now rebuild the map:  npm --prefix scripts run build-map");
