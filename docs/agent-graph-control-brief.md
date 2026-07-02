# Briefing: agent-driven control of the dementia-gap-map graph

> Source brief from a prior agent. See `docs/agent-panel.md` for the
> implementation that realizes §5 of this document.

## 1. Architecture
Two tracks + a web app:

* **Track A (topic-dynamics)**: clusters the ~4,780-paper literature into a map
  (paper coordinates + communities) → `web/` React/Vite app, deploys on Vercel.
* **Track B (translational-evidence)**: genes / GWAS / trials / Open Targets /
  pathways / functional evidence + scores, and the topic↔evidence bridge.
* The map is Track A's `web/` app; Track B supplies the biology joined onto it.

## 2. The data the agent reasons over
Start at `data/processed/translational-evidence/CATALOG.json` — every dataset
(path, schema, primary key, counts, fields), a join graph, id conventions,
example questions. Read it first.

Key datasets (JSONL; also Parquet):

* `genes.jsonl` — `gene_id` (Ensembl), `symbol`, `disease_groups[]`,
  `evidence_scores{genetic_support, functional_support, translation_gap, open_targets_*}`
* `entity_metrics.jsonl` — per gene/variant/pathway metrics (dotted keys, each
  `{value, source}`); verdicts are for the agent to compose
* `gwas_associations.jsonl` — `association_id, pmid, reported_genes[], variant.rsid, p_value, disease_group`
* `trials.jsonl` — `nct_id, interventions[], phases[], overall_status, mechanism_group, disease_group`
* `pathways.jsonl` — `pathway_id, mechanism_group, gene_ids[], scores{clinical_translation, clinical_saturation, translation_gap}`
* `functional_links.jsonl` — variant/locus → gene (Open Targets L2G)
* `gene_pathways_api.jsonl` / `drug_mechanism_api.jsonl` — full multi-source API capture (GO/Reactome/OT)
* shared: `topic_evidence_links.jsonl`, `topic_evidence_rollup.jsonl`
* graph: `data/exports/graph/{nodes,edges}.jsonl`

ID conventions: gene = Ensembl `gene_id` (+ `symbol`); variant = rsID; trial =
NCT id; paper = PMID; disease = controlled `disease_group`; graph node =
`<type>:<id>`.

## 3. The graph ON SCREEN (what the agent controls)
Driven by `web/public/data/map_data.json` (contract in `web/src/types.ts`):
papers are points at baked (x,y), grouped into ~16 Louvain communities
(`cluster_id` → a `Cluster` with a `centroid`), connected by coupling `edges`.
The visual communities are not the 10 `topic_clusters.jsonl`.

"The area under a selection" = selected papers → their `cluster_id`(s) → the
`Cluster` (pathway_group, top_genes, trials, scores) and/or Track B evidence
attached to those papers' PMIDs.

## 4. What ALREADY exists in `web/`
`App.tsx` + `MapCanvas.tsx` implement, as internal React state: `selectedIds` +
region select, filters (`activeGroups`, `yearRange`), pan/zoom, hover-highlight,
geometry helpers (`toScreen/toWorld/fitTransform/pointInPolygon`). Selection,
filtering, zoom, highlight already exist — but only via user gestures.

## 5. What to BUILD: an agent control API
Expose the existing capabilities as an imperative command surface:

```ts
selectPapers(paper_ids)
highlightPapers(paper_ids, style?)      // transient, distinct from select
clearSelection()
zoomToPapers(paper_ids)                 // fit transform to bbox
zoomToCommunity(topic_id)               // fit to cluster centroid/members
setFilters({ pathway_groups?; yearRange?; disease_group? })
focusEntity({ gene?; variant?; pathway? })  // resolve → papers → select+zoom
getState()                              // agent observes
```

Keep it declarative: agent emits intent; the adapter maps to existing state
setters. Return small results so the agent can narrate.

## 6. The joins the agent needs (entity ↔ screen)
* gene/symbol → papers; gene → mechanism/pathway_group; community ↔
  evidence/scores; multi-hop via `edges/nodes.jsonl`; disease filter via
  `disease_group`.

## 7. What MAY CHANGE (build against IDs, not assumptions)
Communities/coordinates/corpus/schema and pathway_group colouring all move.
Stable anchors: PMID, gene Ensembl id/symbol, NCT id, rsID, `disease_group`,
`CATALOG.json`. Resolve by IDs, not coordinates or community numbers.

## 8. Tools the agent needs
Read/decide: `CATALOG.json` → JSONL, DuckDB SQL, Neo4j traversal, or
`map_data.json`. Act/control: the map command API from §5.

## 9. Gotchas
* Two groupings (10 analytical topics vs ~16 visual communities); the map uses
  the visual ones.
* Track B evidence attaches to papers by PMID, not by community.
* Big tables — query only needed columns.
* Not every paper is in a community (singletons dropped) — handle "other".
* The §5 command API is the main thing to build.
