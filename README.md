# Dementia Gap Map

A map of papers discussing dementia, Alzheimer disease, GWAS loci, genes, pathways, drugs/interventions, and clinical trials, grouped by citation/co-citation similarity.

The project is split into two independent work areas so two people can work without touching the same files most of the time:

- **Track A: Topic dynamics layer** - papers, citation/co-citation links, topic clusters, and topic trajectories.
- **Track B: Translational evidence layer** - GWAS genetics, a functional / eQTL layer (aggregated Open Targets Locus-to-Gene predictions in `functional_links.jsonl`, feeding a real `functional_support` score), genes, pathways, drugs/interventions, and clinical trials, covering Alzheimer disease and related dementias (ADRD). Every record is tagged with a controlled `disease_group`, so the map supports a dementia-vs-Alzheimer filter (Alzheimer is the subset `disease_group == "alzheimer"`).

The visual layer will be added after both tracks have stable processed outputs.

## Repository Layout

```text
topic-dynamics/               # Track A owned workspace
translational-evidence/       # Track B owned workspace

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

Track-specific work should stay inside either `topic-dynamics/` or `translational-evidence/` and the matching `data/*/{topic-dynamics,translational-evidence}` folder. Cross-track files should only be added under `shared/` or `data/processed/shared` when both tracks need them.

See [PROTOTYPE_BUILD_SPEC.md](PROTOTYPE_BUILD_SPEC.md) for the full data-source and prototype handoff notes.
