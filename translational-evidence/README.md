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

## First Build Steps

Start with reproducible snapshots, not live calls inside notebooks. The first goal is to produce small, stable JSONL files that Topic Dynamics and the future visual layer can consume.

### 1. Create Source Snapshots

Pull and cache raw responses under `data/raw/translational-evidence/`.

Priority order:

1. GWAS Catalog Alzheimer disease studies.
2. GWAS Catalog associations for each study accession.
3. ClinicalTrials.gov Alzheimer disease studies.
4. Open Targets Alzheimer disease associated targets.
5. SingleBrain/eQTL supplementary tables once the source files are available.

Suggested raw filenames:

```text
data/raw/translational-evidence/gwas_catalog_alzheimer_studies_YYYY-MM-DD.json
data/raw/translational-evidence/gwas_catalog_alzheimer_associations_YYYY-MM-DD.jsonl
data/raw/translational-evidence/clinicaltrials_alzheimer_studies_YYYY-MM-DD.jsonl
data/raw/translational-evidence/open_targets_alzheimer_targets_YYYY-MM-DD.json
data/raw/translational-evidence/singlebrain_eqtl_sources_YYYY-MM-DD/
```

### 2. Normalize GWAS First

Build `normalize/gwas_catalog.py` or equivalent to produce:

```text
data/processed/translational-evidence/gwas_associations.jsonl
data/processed/translational-evidence/genes.jsonl
```

Minimum useful fields:

- study accession
- trait
- PMID
- publication title/date
- strongest risk allele or rsID
- p-value
- reported genes
- Ensembl/Entrez IDs when present

This gives Topic Dynamics the first seed PMIDs and gives this track the first gene list.

### 3. Normalize Clinical Trials

Build `normalize/clinicaltrials.py` to produce:

```text
data/processed/translational-evidence/trials.jsonl
```

Minimum useful fields:

- NCT ID
- title
- status
- phase
- study type
- conditions
- interventions
- start/completion dates
- trial category
- mechanism group

For the prototype, use a transparent manual mechanism mapping such as amyloid, tau, cholinergic/symptomatic, inflammation/microglia, lipid/metabolism, vascular, synaptic/neuroprotection, and diagnostic biomarker.

### 4. Add Target And Pathway Evidence

Use Open Targets to produce:

```text
data/processed/translational-evidence/target_evidence.jsonl
```

Then create a first pathway table:

```text
data/processed/translational-evidence/pathways.jsonl
```

For the first pass, pathways can be curated mechanism groups. Replace or enrich them later with Reactome/GO/MSigDB mappings if needed.

### 5. Link Evidence To Topics

Once Topic Dynamics has clusters, publish:

```text
data/processed/shared/topic_evidence_links.jsonl
```

Start with simple links:

- topic contains a GWAS PMID
- topic contains papers mentioning a gene symbol
- topic label or top terms match a mechanism group
- manual mapping for high-value prototype topics

### 6. Keep Every Score Explainable

Each score should carry its inputs in the record or in a sidecar notes file. Early useful scores:

- `genetic_support`: GWAS count, best p-value, number of supporting loci/genes
- `functional_support`: eQTL/colocalization support by gene and cell type
- `clinical_translation`: trial count, trial phase/status, intervention match
- `translation_gap`: high genetic/functional support with low clinical translation
