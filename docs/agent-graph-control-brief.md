# Briefing: agent-driven control of the dementia-gap-map graph

Audience: an agent (and its developer) that will let a user drive the map by
intent — "zoom to the microglia area", "highlight the TREM2 papers", "what's
under this cluster". This describes the data, the on-screen graph, what already
exists, what to build, and what may change.

## 1. Architecture
Two tracks + a web app:
- **Track A (topic-dynamics):** clusters the ~4,780-paper literature into a map
  (paper coordinates + communities) → `web/` React/Vite app, deploys on Vercel.
- **Track B (translational-evidence):** genes / GWAS / trials / Open Targets /
  pathways / functional evidence + scores, and the topic↔evidence bridge.
- The **map** is Track A's `web/` app; Track B supplies the biology joined onto it.

## 2. The data the agent reasons over
Start at the machine-readable catalog: **`data/processed/translational-evidence/CATALOG.json`**
— it lists every dataset (path, schema, primary key, record counts, fields), a
**join graph**, id conventions, and example questions. Read it first.

Key datasets (JSONL, one object per line; also Parquet — see §8):
- `genes.jsonl` — gene_id (Ensembl), symbol, disease_groups[], evidence_scores{genetic_support, functional_support, translation_gap, open_targets_*}
- `entity_metrics.jsonl` — per gene/variant/pathway metrics (dotted keys, each {value, source}); verdicts are for the agent to compose
- `gwas_associations.jsonl` — association_id, pmid, reported_genes[], variant.rsid, p_value, disease_group
- `trials.jsonl` — nct_id, interventions[], phases[], overall_status, mechanism_group, disease_group
- `pathways.jsonl` — pathway_id, mechanism_group, gene_ids[], scores{clinical_translation, clinical_saturation, translation_gap}
- `functional_links.jsonl` — variant/locus → gene (Open Targets L2G)
- `gene_pathways_api.jsonl` / `drug_mechanism_api.jsonl` — full multi-source API capture (GO/Reactome/OT), multi-valued
- shared: `topic_evidence_links.jsonl` (topic→evidence, with method/confidence/provenance + supporting_paper_ids), `topic_evidence_rollup.jsonl` (per-topic Track-B summary)
- graph: `data/exports/graph/{nodes,edges}.jsonl` — the **pre-joined** heterogeneous graph (variant/gene/pathway/drug/trial/disease/topic + typed edges)

ID conventions: gene = Ensembl `gene_id` (+ `symbol`); variant = rsID; trial = NCT
id; paper = **PMID**; disease = controlled `disease_group`; graph node = `<type>:<id>`.

## 3. The graph ON SCREEN (what the agent controls)
The rendered map is driven by **`web/public/data/map_data.json`** (contract in
`web/src/types.ts`):
```ts
MapData { clusters: Cluster[]; papers: Paper[]; edges: Edge[]; disease?; ... }
Paper   { paper_id; pmid; title; year; cluster_id; x; y; genes[]; pathway_group; trials[]; metrics{...} }
Cluster { topic_id; label; color; pathway_group; top_genes[]; trials[]; paper_count; centroid{x,y}; scores{emergence,genetic_support,functional_support,clinical_translation,clinical_saturation} }
Edge    { source_paper_id/target ... }  // coupling edges drawn as a faint web
```
So on screen: **papers are points at baked (x,y)**, grouped into **communities**
(`cluster_id` → a `Cluster` with a `centroid`), connected by coupling `edges`.
"Communities" (the visual islands, ~16) are Track A's Louvain grouping; they are
**not** the same object as the 10 `topic_clusters.jsonl` — see §7.

**"The area under a selection"** = take the selected papers → their `cluster_id`(s)
→ the `Cluster` (pathway_group, top_genes, trials, scores) and/or the Track B
evidence attached to those papers' PMIDs via `topic_evidence_links.jsonl`
(supporting_paper_ids) → genes / pathways / trials / translation_gap.

## 4. What ALREADY exists in `web/` (user-driven)
`App.tsx` + `MapCanvas.tsx` already implement, as internal React state:
- `selectedIds: Set<paper_id>` + region **select mode** (draw a region → `onSelect(ids)`)
- **filters**: `activeGroups` (pathway groups), `yearRange`
- **pan/zoom** (a `transform`), initial **fit** (`fitTransform`), and **hover
  highlight** of a paper's coupling neighbours (adjacency map)
- geometry helpers in `web/src/lib/geometry.ts` (`toScreen/toWorld/fitTransform/pointInPolygon`)

So selection, filtering, zoom, and highlight already exist — but only via **user
gestures**. The missing piece is a way for an **agent** to invoke them.

## 5. What to BUILD: an agent control API
Expose the existing capabilities as an **imperative command surface** the agent
can call (e.g. a React ref/imperative handle, a small Zustand/redux store, or a
`window.mapAgent` object / postMessage channel). Proposed commands (JSON in →
effect on map):
```ts
selectPapers(paper_ids: string[])              // set selectedIds
highlightPapers(paper_ids: string[], style?)   // transient emphasis (distinct from select)
clearSelection()
zoomToPapers(paper_ids: string[])              // fit transform to their bbox
zoomToCommunity(topic_id: string)              // fit to a cluster centroid/members
setFilters({ pathway_groups?: string[]; yearRange?: [number,number]; disease_group?: string })
focusEntity({ gene?: symbol; variant?: rsid; pathway?: string })  // resolve → papers → select+zoom
getState()  -> { selectedIds, visiblePapers, transform, filters }  // so the agent can observe
```
Return a small result (counts, the resolved community/evidence) so the agent can
narrate. Keep it declarative: the agent emits intent; the adapter maps to the
existing state setters. `pointInPolygon` + paper (x,y) already lets you resolve a
drawn/received region → paper_ids.

The agent's **decision layer** (what to select/zoom/highlight) uses §2 data +
§6 joins; the **action layer** is this command API.

## 6. The joins the agent needs (entity ↔ screen)
- gene/symbol → papers: `topic_evidence_links.jsonl` gene links carry
  `provenance.gene_symbol` + `supporting_paper_ids` (PMIDs); OR `Paper.genes[]`
  in map_data.json. PMID ↔ `Paper.pmid`.
- gene → mechanism: **multi-valued** — a gene belongs to ALL buckets it has
  signals for (APP → amyloid AND synaptic AND lipid, all true). **Filter mechanism
  via `gene_pathways_api.jsonl.ad_bucket_signals`** (each `{bucket, source,
  matched_term}`), NOT the single `gene_pathway.csv` primary (a non-authoritative
  one-value convenience; a general ontology can't canonically pick one AD
  mechanism per gene).
- community ↔ evidence/scores: `topic_evidence_rollup.jsonl` (per topic) +
  `Cluster.scores` in map_data.json.
- "everything connected to X" (multi-hop): traverse `edges.jsonl`/`nodes.jsonl`
  or Neo4j (see §8).
- disease filter: `disease_group` on most records + `Paper`/`Cluster` (dementia
  vs Alzheimer etc.).

## 7. What MAY CHANGE (build against these, not hard assumptions)
Track A (map): **communities** count/membership can change on re-cluster
(currently ~16 Louvain; also a separate 10-cluster `topic_clusters.jsonl`);
**paper coordinates** come from ForceAtlas2 — deterministic seed so stable per
build, but a re-layout can move things; the **corpus grows** (4,780 now, heading
higher); `map_data.json` schema is still evolving. Track B: **pathway_group
colouring is moving to API-derived** (Reactome/GO/Open Targets) so a gene's
mechanism bucket may shift; scores are explainable but their weights may be
revised. Stable anchors to rely on: **PMID**, **gene Ensembl id / symbol**,
**NCT id**, **rsID**, `disease_group` vocabulary, and `CATALOG.json` (regenerated,
always current). Prefer resolving by these IDs, not by coordinates or community
numbers.

## 8. Tools the agent needs
Read/decide:
- `CATALOG.json` (orient), then the JSONL directly, or
- **DuckDB SQL** over the data: locally `translational-evidence/exports/query_te.py "<SQL>"`;
  in the browser (Vercel) **DuckDB-Wasm over Parquet** — see
  `translational-evidence/exports/WEB_QUERY.md` (Parquet in `data/exports/parquet/`, ~3 MB total)
- **Neo4j** for multi-hop traversal (`data/exports/graph/neo4j/` + its README; Cypher)
- `map_data.json` for exactly what's on screen (entities + coordinates)
Act/control:
- the **map command API** from §5 (to be built in `web/`)

## 9. Gotchas
- Two groupings exist (10 analytical topics vs ~16 visual communities); the map
  uses the visual communities. Don't assume they line up.
- Track B evidence attaches to papers **by PMID**, not by community — robust to
  re-clustering.
- Big tables (`nodes` 15k, `gwas` 7k): in-browser, query Parquet columns you need.
- Not every paper is in a community (singletons dropped); handle "other".
- The command API in §5 does **not exist yet** — it's the main thing to build;
  everything it needs (state setters, geometry, data) is already present.
