# Dementia Gap Map

A map of papers discussing dementia, Alzheimer disease, GWAS loci, genes, pathways, drugs/interventions, and clinical trials, grouped by citation/co-citation similarity.

The project is split into two independent data tracks so two people can work without touching the same files most of the time:

- **Track A: Topic dynamics layer** - papers, citation/co-citation links, topic clusters, and topic trajectories.
- **Track B: Translational evidence layer** - GWAS/eQTL evidence, genes, pathways, drugs/interventions, and clinical trials.

The visual layer will be added after both tracks have stable processed outputs.

## Repository Layout

```text
tracks/
  topic-dynamics/             # Track A owned workspace
  translational-evidence/     # Track B owned workspace

shared/
  schemas/                    # Shared data contracts between tracks
  lib/                        # Shared helper code, once needed

data/
  raw/                        # Source API downloads and hand-curated inputs
  interim/                    # Track-local working outputs
  processed/                  # Stable outputs for cross-track use
  exports/                    # Final files for the future visual layer

docs/
  working-agreement.md        # Ownership and handoff rules
```

## Working Rule

Track-specific work should stay inside its own `tracks/*` folder and matching `data/*/{track}` folder. Cross-track files should only be added under `shared/` or `data/processed/shared` when both tracks need them.

See [PROTOTYPE_BUILD_SPEC.md](PROTOTYPE_BUILD_SPEC.md) for the full data-source and prototype handoff notes.
