# Track B: Translational Evidence Layer

This track builds the genetics, functional / eQTL, pathway, drug/intervention, and clinical-trial evidence layer. The functional / eQTL layer is present: aggregated Open Targets Locus-to-Gene (L2G) predictions (with GWAS→QTL colocalisation where available) are ingested into `functional_links.jsonl` and drive a real per-gene `functional_support` score.

## Scope

This track covers **Alzheimer disease plus related dementias (ADRD)** — not
Alzheimer alone. Every record is tagged with a controlled `disease_group` (see
[Disease dimension](#disease-dimension)) so Alzheimer can always be recovered as
a subset with `disease_group == "alzheimer"`.

Inputs:

- GWAS Catalog studies and associations, queried across a **list of EFO traits**
  (Alzheimer disease, dementia, vascular dementia, frontotemporal dementia,
  Lewy body dementia, dementia with Lewy bodies, Parkinson's disease dementia)
- Open Targets target-disease evidence for **multiple disease ids** (Alzheimer +
  dementia + vascular / frontotemporal / Lewy body dementia)
- Open Targets fine-mapped **credible sets**: Locus-to-Gene (L2G) predictions and
  GWAS→QTL colocalisation across the ADRD GWAS studies (the functional / eQTL
  layer → `functional_links.jsonl`). A dedicated brain-cell-type eQTL Catalogue
  enrichment is noted as optional future work.
- ClinicalTrials.gov studies across **multiple ADRD conditions** (Alzheimer
  Disease, Vascular Dementia, Frontotemporal Dementia, Lewy Body Dementia,
  Dementia)
- Optional pathway and drug-target mapping sources

Outputs:

- `data/processed/translational-evidence/gwas_associations.jsonl`
- `data/processed/translational-evidence/genes.jsonl`
- `data/processed/translational-evidence/pathways.jsonl`
- `data/processed/translational-evidence/trials.jsonl`
- `data/processed/translational-evidence/target_evidence.jsonl`
- `data/processed/translational-evidence/functional_links.jsonl`

Evidence-graph explorer exports (standalone Track B, separate from Track A's
`web/` app; generated under the gitignored `data/exports/graph/` — see the
["Evidence graph explorer"](RUNBOOK.md#9-evidence-graph-explorer-standalone-track-b)
section of the RUNBOOK):

- `data/exports/graph/nodes.jsonl` — 15,286 evidence nodes (`evidence_node.schema.json`)
- `data/exports/graph/edges.jsonl` — 10,732 evidence edges (`evidence_edge.schema.json`)
- `data/exports/graph/evidence_graph.html` — zero-install sigma.js graph explorer (`open` it; loads CDN libs, so needs internet; trials toggled off by default)
- `data/exports/graph/neo4j/` — Neo4j-ready `nodes.csv` / `edges.csv` + `load.cypher` + `README.md` for full Cypher filtering (needs Docker / a running DB)

## Disease dimension

Because the track now spans ADRD, every processed record carries a controlled
disease tag drawn from this vocabulary (exact string values):

| value | meaning |
| --- | --- |
| `alzheimer` | Alzheimer disease (incl. "Alzheimer's disease", late/early onset, AD) |
| `vascular_dementia` | vascular dementia / vascular cognitive impairment |
| `frontotemporal_dementia` | frontotemporal dementia / FTD / FTLD / primary progressive aphasia |
| `lewy_body_dementia` | dementia with Lewy bodies / Lewy body dementia / Parkinson's disease dementia |
| `mixed_dementia` | explicitly mixed / Alzheimer + vascular etc. |
| `dementia_unspecified` | "dementia", all-cause dementia, MCI, cognitive decline not otherwise specified |
| `other` | neurodegenerative but none of the above / unclear |

Classification precedence when text matches several groups:
`mixed_dementia` > specific subtype (`vascular_dementia` /
`frontotemporal_dementia` / `lewy_body_dementia`) > `alzheimer` >
`dementia_unspecified` > `other`. So "Alzheimer's disease and vascular dementia"
→ `mixed_dementia`, "Alzheimer's disease" → `alzheimer`, bare "Dementia" →
`dementia_unspecified`. The rules are pure, offline, case-insensitive keyword
matches implemented in `common.classify_disease_group(text)` /
`common.classify_disease_groups(texts)`.

Which records carry which field:

- **`gwas_associations`**, **`trials`**, and **`target_evidence`** each carry a
  single `disease_group` string — classified from the GWAS `trait`, the trial
  `conditions`, and the Open Targets `disease_label` respectively.
- **`genes`** carry `disease_groups` (an array, dedup + sorted): a gene is
  aggregated across many associations and can legitimately span several groups.

**Filtering dementia-vs-AD.** To get the Alzheimer-only subset (the original
behaviour), keep records where `disease_group == "alzheimer"` (or, for genes,
where `"alzheimer" in disease_groups`). To get everything *except* pure
Alzheimer, keep `disease_group != "alzheimer"`. Any single subtype (e.g.
vascular dementia) is `disease_group == "vascular_dementia"`.

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
- `shared/schemas/functional_link.schema.json`

Track A can use `publication.pmid` fields from this track as seed papers for the literature network.

## First Build Steps

Start with reproducible snapshots, not live calls inside notebooks. The first goal is to produce small, stable JSONL files that Topic Dynamics and the future visual layer can consume.

### 1. Create Source Snapshots

Pull and cache raw responses under `data/raw/translational-evidence/`.

Priority order (each source now spans the full ADRD set, not Alzheimer alone):

1. GWAS Catalog studies for the **list of EFO traits** (Alzheimer disease,
   dementia, vascular dementia, frontotemporal dementia, Lewy body dementia,
   dementia with Lewy bodies, Parkinson's disease dementia), unioned + deduped
   by accession.
2. GWAS Catalog associations for each unique study accession (Alzheimer
   accessions are reused from the existing per-accession cache; only new
   accessions are fetched).
3. ClinicalTrials.gov studies across the **multiple ADRD conditions** (Alzheimer
   Disease, Vascular Dementia, Frontotemporal Dementia, Lewy Body Dementia,
   Dementia), deduped by NCT id.
4. Open Targets associated targets for **multiple disease ids** (Alzheimer +
   dementia + vascular / frontotemporal / Lewy body dementia; non-Alzheimer ids
   resolved via the Open Targets `search` query).
5. SingleBrain/eQTL supplementary tables once the source files are available.

Suggested raw filenames (Alzheimer-only combined files are still written for
provenance; the ADRD combined files are the new broadened set):

```text
# per-trait / per-condition / per-disease pages
data/raw/translational-evidence/gwas_catalog_studies_{traitSlug}_YYYY-MM-DD_page_PPP.json
data/raw/translational-evidence/clinicaltrials_{condSlug}_page_PPP.json
data/raw/translational-evidence/open_targets_{diseaseId}_YYYY-MM-DD_page_N.json
data/raw/translational-evidence/open_targets_search_{slug}_YYYY-MM-DD.json
# combined (broadened ADRD + legacy Alzheimer-only)
data/raw/translational-evidence/gwas_catalog_adrd_associations_YYYY-MM-DD.jsonl
data/raw/translational-evidence/gwas_catalog_alzheimer_associations_YYYY-MM-DD.jsonl
data/raw/translational-evidence/clinicaltrials_adrd_studies_YYYY-MM-DD.jsonl
data/raw/translational-evidence/open_targets_adrd_targets_YYYY-MM-DD.json
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
- `disease_group` per association (from the trait) and `disease_groups` per gene

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
- `disease_group` (from the trial's conditions)

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
- `functional_support`: real functional / eQTL layer — max Open Targets L2G
  score per gene across fine-mapped loci, plus a brain-cell-type colocalisation
  bonus (from `functional_links.jsonl`)
- `clinical_translation`: trial count, trial phase/status, intervention match
- `translation_gap`: high genetic/functional support with low clinical translation
