# Tracks

This folder holds the two independent data workspaces.

- `topic-dynamics` is Track A: literature, citation/co-citation networks, clusters, and trajectories.
- `translational-evidence` is Track B: GWAS/eQTL, genes, pathways, drugs/interventions, and trials.

Each track should keep its own ingestion, normalization, scoring, and export code inside its folder. Shared contracts live in `shared/schemas`.
