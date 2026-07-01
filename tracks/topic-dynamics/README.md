# Track A: Topic Dynamics Layer

This track builds the literature and topic-dynamics layer.

## Scope

Inputs:

- PubMed paper metadata
- PubMed references and cited-by links
- iCite article metrics
- Optional Europe PMC, Semantic Scholar, or OpenAlex metadata
- Seed PMIDs from Track B GWAS/eQTL sources

Outputs:

- `data/processed/topic-dynamics/papers.jsonl`
- `data/processed/topic-dynamics/paper_edges.jsonl`
- `data/processed/topic-dynamics/topic_clusters.jsonl`
- `data/processed/topic-dynamics/topic_trajectories.jsonl`

## Suggested Internal Layout

```text
ingest/       # API clients and source downloads
normalize/    # source-specific cleaning into common paper records
network/      # citation, co-citation, and bibliographic coupling graph logic
cluster/      # Leiden/Louvain/topic clustering experiments
score/        # emergence and trajectory scoring
exports/      # writers for processed handoff files
notebooks/    # scratch analysis only
```

## Contract

Use these shared schemas when publishing stable outputs:

- `shared/schemas/paper.schema.json`
- `shared/schemas/paper_edge.schema.json`
- `shared/schemas/topic_cluster.schema.json`
- `shared/schemas/topic_trajectory.schema.json`

Do not make the visual layer depend on notebook outputs. Promote useful notebook results into scripts or exports first.
