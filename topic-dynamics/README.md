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

## Corpus definition

The corpus **is** the field: every PubMed paper about dementia (broadly, via the
`Dementia` MeSH tree plus Alzheimer / frontotemporal / Lewy-body / vascular /
cognitive-impairment synonyms) that **also mentions GWAS**. This is a full-field
subset, not a seed-and-expand sample — the query in `config.SEARCH_TERM` defines
membership, and every match is ingested (no size cap by default).

For each paper we pull its **title/metadata, reference list, and cited-by list**
— no abstracts or full text. References drive bibliographic coupling; cited-by
drives the co-citation network.

Hand-curated backbone papers (`topics/ingest/seeds.py`) and any Track B GWAS
PMIDs are unioned in so they are guaranteed present, but they do not drive corpus
construction.

## Pipeline

The `topics/` package implements the end-to-end pipeline (the workspace folder
name `topic-dynamics` has a hyphen and cannot be a Python package, so the
importable code lives in `topics/` and `run.py` is the entry point).

```text
topics/
  config.py            # query, corpus/network/scoring parameters
  ingest/              # PubMed (esearch history/esummary/elink refs) + iCite, cached
  normalize/           # esummary + iCite -> paper.schema.json records
  network/             # coupling + co-citation edges, both derived from references
  cluster/             # greedy-modularity communities + TF-IDF labels
  score/               # yearly trajectories + explainable emergence scores
  exports/             # writers for the four handoff files
run.py                 # entry point
validate.py            # checks outputs against shared/schemas
```

**Bibliographic coupling** links papers that share references; **co-citation**
links papers that are cited together by later papers (from cited-by lists).
Shared references/citers touching more than `MAX_NEIGHBOR_DF_FRACTION` of the
corpus are dropped as uninformative hubs.

### Run

```bash
pip install -r requirements.txt
cd topic-dynamics
python run.py                       # ingest the whole dementia+GWAS field
python run.py --max-papers 500      # cap the corpus for a quick test run
python validate.py                  # schema-check the outputs
```

Every API response is cached under `data/raw/topic-dynamics/cache/`, so re-runs
are fast and offline. A full-field run makes one `elink` call per paper
(throttled to NCBI's rate limit); set `NCBI_API_KEY` to go faster.

### Outputs

Written to `data/processed/topic-dynamics/` (git-ignored; regenerate with the
pipeline): `papers.jsonl`, `paper_edges.jsonl`, `topic_clusters.jsonl`,
`topic_trajectories.jsonl` — all validated against `shared/schemas`.
