# Working Agreement

This repository is split so Track A and Track B can move independently and integrate through shared data contracts.

## Ownership

Track A owns:

- `tracks/topic-dynamics/**`
- `data/raw/topic-dynamics/**`
- `data/interim/topic-dynamics/**`
- `data/processed/topic-dynamics/**`

Track B owns:

- `tracks/translational-evidence/**`
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

The first shared integration file should be:

- `data/processed/shared/topic_evidence_links.jsonl`
