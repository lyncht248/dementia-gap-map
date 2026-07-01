# Shared Schemas

These JSON Schemas define the stable processed records that tracks exchange.

Early prototype exports should use `.jsonl`, with one JSON object per line conforming to the matching schema.

## Track A

- `paper.schema.json`
- `paper_edge.schema.json`
- `topic_cluster.schema.json`
- `topic_trajectory.schema.json`

## Track B

- `gwas_association.schema.json`
- `gene.schema.json`
- `pathway.schema.json`
- `trial.schema.json`
- `target_evidence.schema.json`
- `functional_link.schema.json`

## Shared Integration

- `topic_evidence_link.schema.json` — one record per (topic, evidence, link_type)
  join between a Track A topic and a Track B evidence item.
- `topic_evidence_rollup.schema.json` — one record per Track A topic: the Track B
  half of the frontend `map_data.json` cluster (dominant `pathway_group`,
  `top_genes`, `top_gwas`, `trials`, aggregated `scores`, `evidence_counts`,
  `disease_groups`, `provenance`). Joined to Track A on `topic_id`.

Both are produced by `translational-evidence/exports/build_topic_bridge.py` and
land in `data/processed/shared/` (`topic_evidence_links.jsonl` and
`topic_evidence_rollup.jsonl`).

## Disease dimension

Track B broadens Alzheimer-only coverage to Alzheimer disease plus related
dementias (ADRD). Records are tagged with a controlled disease dimension so
views can split Alzheimer from the related dementias.

Controlled vocabulary (exact string values):

- `alzheimer` — Alzheimer disease (incl. late/early onset, AD)
- `vascular_dementia` — vascular dementia / vascular cognitive impairment
- `frontotemporal_dementia` — FTD / FTLD / primary progressive aphasia
- `lewy_body_dementia` — dementia with Lewy bodies / Parkinson's disease dementia
- `mixed_dementia` — explicitly mixed (e.g. Alzheimer + vascular)
- `dementia_unspecified` — bare "dementia", all-cause dementia, MCI, cognitive decline
- `other` — neurodegenerative but none of the above / unclear

Classification precedence when text matches several groups:
`mixed_dementia` > specific subtype (`vascular_dementia` /
`frontotemporal_dementia` / `lewy_body_dementia`) > `alzheimer` >
`dementia_unspecified` > `other`.

Which records carry which field (both are OPTIONAL, not `required`):

- `disease_group` (string, nullable) — a single value on per-instance records:
  `gwas_association` (from `trait`), `trial` (from `conditions`), and
  `target_evidence` (from `disease_label`).
- `disease_groups` (array of strings) — dedup+sorted values on `gene`, which
  aggregates across many traits/associations and can span several groups.

The classification is implemented offline (standard library only) in
`translational-evidence/common.py` as `classify_disease_group(text)` and
`classify_disease_groups(texts)`.
