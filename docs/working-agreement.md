# Working Agreement

This repository is split so Track A and Track B can move independently and integrate through shared data contracts.

## Ownership

Track A owns:

- `topic-dynamics/**`
- `data/raw/topic-dynamics/**`
- `data/interim/topic-dynamics/**`
- `data/processed/topic-dynamics/**`

Track B owns:

- `translational-evidence/**`
- `data/raw/translational-evidence/**`
- `data/interim/translational-evidence/**`
- `data/processed/translational-evidence/**`

Shared ownership:

- `shared/**`
- `data/raw/shared/**`
- `data/processed/shared/**`
- `data/exports/**`

## Integration Rules

1. Keep ingestion scripts track-local unless they are genuinely shared.
2. Keep large downloaded data out of git. Put it under `data/raw`, `data/interim`, `data/processed`, or `data/exports`.
3. Use `shared/schemas` for the fields that both tracks depend on.
4. Treat `data/processed/shared` as the handoff area between tracks.
5. Do not change another track's processed output shape without updating the matching schema.

## Shared Fields

- `publication.pmid` on Track B GWAS records seeds Track A's literature network.
- `disease_group` is a controlled-vocabulary field on Track B processed outputs.
  Track B covers Alzheimer disease and related dementias (ADRD) and tags each
  `gwas_association`, `trial`, and `target_evidence` record with a single
  `disease_group`; `gene` records carry a `disease_groups` array (dedup +
  sorted). Values are drawn from `alzheimer`, `vascular_dementia`,
  `frontotemporal_dementia`, `lewy_body_dementia`, `mixed_dementia`,
  `dementia_unspecified`, `other` (see `shared/schemas/README.md`). Track A can
  use these tags to filter seed PMIDs / topics by dementia subtype — e.g.
  Alzheimer-only via `disease_group == "alzheimer"`. This is a shared
  *understanding* of the field, not a change to the ownership rules above:
  `disease_group` stays Track B's to populate.

## Expected Handoff Files

Track A should eventually publish:

- `data/processed/topic-dynamics/papers.jsonl`
- `data/processed/topic-dynamics/paper_edges.jsonl`
- `data/processed/topic-dynamics/topic_clusters.jsonl`
- `data/processed/topic-dynamics/topic_trajectories.jsonl`

Track B should eventually publish:

- `data/processed/translational-evidence/gwas_associations.jsonl`
- `data/processed/translational-evidence/genes.jsonl`
- `data/processed/translational-evidence/pathways.jsonl`
- `data/processed/translational-evidence/trials.jsonl`
- `data/processed/translational-evidence/target_evidence.jsonl`

## Track B → shared handoff (integration bridge)

Track B now also **publishes the Track A ↔ Track B integration bridge** into the
shared handoff area. It builds two files with
`translational-evidence/exports/build_topic_bridge.py`, which reads Track A's
published `topic_clusters` + `papers` snapshot and joins them against Track B's
curated evidence:

- `data/processed/shared/topic_evidence_links.jsonl`
  (`shared/schemas/topic_evidence_link.schema.json`) — one explainable record per
  (topic, evidence, link_type) join.
- `data/processed/shared/topic_evidence_rollup.jsonl`
  (`shared/schemas/topic_evidence_rollup.schema.json`) — one record per topic, the
  Track B half of the frontend `map_data.json` cluster (dominant `pathway_group`,
  `top_genes`, `top_gwas`, `trials`, aggregated `scores`, `evidence_counts`,
  `disease_groups`, `provenance`).

**Join method.** The bridge joins on **three** link types rather than PMID
overlap alone: `gene_mention` (case-sensitive whole-word gene-symbol matches in
member-paper abstracts), `paper_overlap` (shared PMIDs between a topic's papers
and Track B's GWAS publications, which also pulls in the associations' reported
genes), and `pathway_mapping` (linked-gene → `pathway_group` via the
authoritative `translational-evidence/map/gene_pathway.csv`, plus topic
`top_terms` keyword matches). Bare **PMID overlap alone is thin** — only ~10 of
~410 snapshot papers share a PMID with the GWAS corpus — so `gene_mention` +
`pathway_mapping` carry most of the coverage.

The current bridge is built against a Track A **SUBSET** snapshot (~410 papers /
9 clusters); Track A's full run is pending and coverage will grow substantially
when it lands (see the Track B RUNBOOK "Refresh when Track A full run lands").

The shared integration files are:

- `data/processed/shared/topic_evidence_links.jsonl`
- `data/processed/shared/topic_evidence_rollup.jsonl`
