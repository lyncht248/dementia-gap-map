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

## Pipeline

The `topics/` package implements the end-to-end pipeline (the workspace folder
name `topic-dynamics` has a hyphen and cannot be a Python package, so the
importable code lives in `topics/` and `run.py` is the entry point).

```text
topics/
  config.py            # paths, corpus caps, network + scoring parameters
  ingest/              # PubMed (esearch/esummary/elink) + iCite, disk-cached
  normalize/           # esummary + iCite -> paper.schema.json records
  network/             # bibliographic-coupling + co-citation edges
  cluster/             # greedy-modularity communities + TF-IDF labels
  score/               # yearly trajectories + explainable emergence scores
  exports/             # writers for the four handoff files
run.py                 # entry point
validate.py            # checks outputs against shared/schemas
```

### Run

```bash
pip install -r requirements.txt
cd topic-dynamics
python run.py --max-papers 300      # manual + Track B seeds, PubMed esearch expansion
python run.py --no-search           # manual/Track-B seeds only (offline-friendly, fast)
python validate.py                  # schema-check the outputs
```

Seeds come from `topics/ingest/seeds.py` (hand-curated backbone papers) and, when
present, `data/processed/translational-evidence/gwas_associations.jsonl` from
Track B. Every API response is cached under `data/raw/topic-dynamics/cache/`, so
re-runs are fast and offline.

### Outputs

Written to `data/processed/topic-dynamics/` (git-ignored; regenerate with the
pipeline): `papers.jsonl`, `paper_edges.jsonl`, `topic_clusters.jsonl`,
`topic_trajectories.jsonl` — all validated against `shared/schemas`.
