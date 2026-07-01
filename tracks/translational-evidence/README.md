# Track B: Translational Evidence Layer

This track builds the genetics, functional, pathway, drug/intervention, and clinical-trial evidence layer.

## Scope

Inputs:

- GWAS Catalog studies and associations
- Open Targets target-disease evidence
- SingleBrain or other eQTL/colocalization tables
- ClinicalTrials.gov Alzheimer disease studies
- Optional pathway and drug-target mapping sources

Outputs:

- `data/processed/translational-evidence/gwas_associations.jsonl`
- `data/processed/translational-evidence/genes.jsonl`
- `data/processed/translational-evidence/pathways.jsonl`
- `data/processed/translational-evidence/trials.jsonl`
- `data/processed/translational-evidence/target_evidence.jsonl`

## Suggested Internal Layout

```text
ingest/       # API clients and source downloads
normalize/    # source-specific cleaning into common records
map/          # locus-to-gene, gene-to-pathway, and intervention mapping
score/        # evidence strength and translation-gap scoring
exports/      # writers for processed handoff files
notebooks/    # scratch analysis only
```

## Contract

Use these shared schemas when publishing stable outputs:

- `shared/schemas/gwas_association.schema.json`
- `shared/schemas/gene.schema.json`
- `shared/schemas/pathway.schema.json`
- `shared/schemas/trial.schema.json`
- `shared/schemas/target_evidence.schema.json`

Track A can use `publication.pmid` fields from this track as seed papers for the literature network.
