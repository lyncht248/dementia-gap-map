# Track B — `map/` : gene→pathway and drug→mechanism (API-derived, multi-valued)

This directory turns the ADRD gene and drug universe into two **AD-mechanism**
views. The mechanism vocabulary (the "buckets") is fixed and hand-authored, but
**every gene's pathway membership and every drug's mechanism-of-action is pulled
live from public APIs** — mygene.info, Reactome, and Open Targets. Nothing is
hand-curated per gene or per drug, and **nothing the APIs return is discarded**.

## Core principle — RECORD EVERYTHING, MULTI-VALUED

For each gene we persist the **full** captured annotation set from **every**
source (all GO terms, all Reactome pathways, all Open Targets target pathways) —
not just the subset that matched an AD mechanism bucket. For each drug we persist
**every** Open Targets mechanism-of-action row with its full target-symbol list.

AD-bucket / mechanism tags are a **LIST of signals**, each
`{bucket, source, matched_term}` (genes) or `{mechanism, source, matched_term,
moa_text}` (drugs). A gene may legitimately carry several buckets from several
sources, and we keep them all. The **only** reduction to a single value is a thin
`primary` projection derived for the two legacy single-value CSVs the map
consumes — it is clearly labelled as a projection and never overwrites the rich
multi-valued record.

## Two layers: rich capture (JSONL) → thin projection (CSV)

| Layer | File | Shape | Consumer |
| --- | --- | --- | --- |
| **Rich capture (genes)** | `data/processed/translational-evidence/gene_pathways_api.jsonl` | one record per gene: full `sources.{go,reactome,open_targets}` + `ad_bucket_signals[]` + ranked `buckets[]` + `primary_bucket` + `primary_support[]` | analysts, the graph explorer, agents |
| **Thin projection (genes)** | `map/gene_pathway.csv` | `gene_symbol, pathway_group (=primary_bucket), notes` | `map/pathways.py`, the legacy single-colour map |
| **Rich capture (drugs)** | `data/processed/translational-evidence/drug_mechanism_api.jsonl` | one record per resolved ChEMBL drug: full `sources.opentargets[]` (every MoA + targets) + `mechanism_signals[]` + `mechanisms[]` + `primary_mechanism` + `primary_support[]` + all trial spellings | analysts, agents |
| **Thin projection (drugs)** | `map/intervention_mechanism.csv` | `keyword, mechanism_group (=primary_mechanism), notes` | `normalize/clinicaltrials.py` (trial tagging) |

> **The two CSVs are GENERATED thin projections. Do not hand-edit them.** Each
> carries a `# GENERATED …` provenance comment on line 1. Regenerate them (and
> the rich JSONL) with the two build scripts below. The full, multi-source,
> multi-valued truth lives in the JSONL sidecars — the CSV is only a
> single-value convenience for legacy consumers.

## How each layer is built

### `map/gene_pathway_build.py` — gene → pathway

Input: `data/processed/translational-evidence/genes.jsonl` (the ADRD gene
universe). For every gene:

1. **mygene.info** `GET /v3/gene/{ensembl}?fields=symbol,uniprot,go` → the Swiss-Prot
   UniProt accession(s) + **all** `go.{BP,MF,CC}[{id,term}]`.
2. **Reactome** `GET /ContentService/data/mapping/UniProt/{acc}/pathways?species=9606`
   → **all** `[{stId, displayName}]`, unioned across the gene's accessions.
3. **Open Targets** GraphQL `target(ensemblId).pathways` → **all**
   `[{pathwayId, pathway, topLevelTerm}]`.

All three source lists are stored verbatim. The keyword ruleset (below) is then
run over the GO term names, Reactome `displayName`s, and OT `pathway` /
`topLevelTerm` strings to emit `ad_bucket_signals[]`. Buckets are ranked by the
number of **distinct supporting sources** (secondary: distinct matched-terms;
final tiebreak: the priority order), and the top one becomes `primary_bucket` —
the single value written to `gene_pathway.csv`. Genes with **no** keyword hit
are `unknown`: they carry an empty `buckets[]` and are **omitted from the CSV**,
but their **full raw GO/Reactome/OT annotations are still stored** in the JSONL.

### `map/intervention_mechanism_build.py` — drug → mechanism

Input: `data/processed/translational-evidence/trials.jsonl`. Distinct
DRUG/BIOLOGICAL intervention names are ranked by trial frequency (pure
placebo/saline/sham controls skipped), and the top N are resolved against
Open Targets:

1. **OT** `search(queryString, entityNames:["drug"])` → `chemblId`.
2. **OT** `drug(chemblId).mechanismsOfAction.rows` → **every**
   `{mechanismOfAction, targets:[{id, approvedSymbol}]}` row.

All MoA rows are stored verbatim. The trial-mechanism ruleset is run over each
MoA text **and** its target symbols to emit `mechanism_signals[]`; the
most-supported mechanism becomes `primary_mechanism` (the value written to
`intervention_mechanism.csv`). A drug OT resolves but whose MoA text does not
match any keyword is recorded as `other` (still a real, fully-captured record).
Several trial spellings that resolve to the same ChEMBL id are merged into ONE
rich record (all spellings + summed trial counts unioned — nothing discarded).

## The ONLY hand element: the keyword ruleset

Everything above is API-derived. The single residual hand element is the
**transparent, in-code keyword ruleset** that maps captured term strings into the
fixed AD-mechanism vocabulary. It lives in the two build scripts
(`PATHWAY_BUCKET_KEYWORDS` / `TRIAL_MECHANISM_KEYWORDS`), not per gene or per
drug, so it is auditable and editable in one place. Membership is decided by the
APIs; the ruleset only *names* the mechanism a captured term belongs to.

**Gene bucket vocabulary:** `amyloid`, `tau`, `microglia_immune`,
`lipid_metabolism`, `vascular`, `endocytosis_endosomal`, `synaptic_neuronal`,
`epigenetic_transcription` (+ `unknown` for no-hit genes with raw annotations
still stored). **Drug mechanism vocabulary:** `amyloid`, `tau`,
`cholinergic_symptomatic`, `inflammation_microglia`, `lipid_metabolism`,
`vascular`, `synaptic_neuroprotection`, `diagnostic_biomarker`, `other`.

### The primary projection is *not* the old hand curation

Because the primary is the **most-source-supported** bucket, it can differ from
the retired hand `gene_pathway.csv`. For example, on genes with broad GO
annotation the flagship label can be outvoted: **APP** projects to
`endocytosis_endosomal` (15 supporting terms across all 3 sources) over `amyloid`
(14 terms); **MAPT** projects to `synaptic_neuronal` (2 sources) over `tau`
(1 source, GO-only). This is a faithful consequence of the "most-source-supported"
rule, not an error — the `amyloid` / `tau` signals are still present in the rich
record's `buckets[]` and `ad_bucket_signals[]`. Consumers that want a specific
mechanism should read the multi-valued `buckets[]`, not only `primary_bucket`.
See RUNBOOK §11 for the full agreement/known-limitation notes (incl. the
`"a-beta"` keyword's `alpha-beta` T-cell collision, and genes outside the current
GWAS universe that the projection cannot cover).

## Other files in `map/`

- `pathways.py` — groups `gene_pathway.csv` by `pathway_group` into
  `pathways.jsonl` (skips the generated `# GENERATED …` comment line).
- `chemical_gene.csv` — curated chemical-UI → gene crosswalk for the topic bridge
  (`chemical_ui_crosswalk`); unrelated to the API capture here.
- `mesh_tree.py` — **API-derived** MeSH Dementia-subtree classifier (MeSH SPARQL);
  the old hand `mesh_disease.csv` was deleted in favour of it.

## Regenerate

```bash
# genes: mygene + Reactome + OT  ->  gene_pathways_api.jsonl + gene_pathway.csv + pathways.jsonl
python3 translational-evidence/map/gene_pathway_build.py

# drugs: OT search + MoA  ->  drug_mechanism_api.jsonl + intervention_mechanism.csv
python3 translational-evidence/map/intervention_mechanism_build.py

# force fresh API calls (bypass the data/raw cache):
TE_REFRESH=1 python3 translational-evidence/map/gene_pathway_build.py
```

Standard library only (Python 3.9); all network via `common.get_json` /
`common.post_json` (cached to `data/raw/translational-evidence/`). Missing data
is recorded as nothing — never fabricated.
