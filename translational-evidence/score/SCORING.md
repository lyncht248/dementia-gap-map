# Scoring methodology (Track B: translational evidence)

This document specifies every formula, weight, normalization constant, the
mechanism crosswalk, and the data provenance behind the scores written by
`translational-evidence/score/scores.py`.

Design principle: **every score is fully explainable**. Each derived number is
stored in the JSONL record alongside its raw component inputs and the exact
weights/normalizations used, so nothing is a black box.

All scores are clamped/rounded to `[0, 1]` (4 decimal places) unless noted.

---

## 1. Gene scores (written into `genes.jsonl` -> `evidence_scores`)

### Open Targets join
Open Targets `target_evidence.jsonl` is joined to each gene by **Ensembl
`gene_id` first, then by `symbol == target_label`**. The matched key is stored
as `evidence_scores.open_targets_match` (`"gene_id"`, `"symbol"`, or `null`).

`target_evidence.jsonl` now carries **one row per (gene, disease)** across the
ADRD disease set. For the **headline** `open_targets_*` scores we prefer the
**Alzheimer disease anchor row** (`disease_id == MONDO_0004975`), because AD is
the anchor disease of this track; if a gene has no Alzheimer OT row we fall back
to its first-seen disease row so the gene still receives OT scores.
`evidence_scores.open_targets_headline_disease` is `"alzheimer"` when the
headline scores came from the Alzheimer anchor row, else `null` (fallback row).
`evidence_scores.open_targets_disease_groups` lists the sorted-unique
`disease_group` values the gene is associated with across **all** its OT disease
rows.

The following raw Open Targets association scores are attached (each `null` if
there is no OT match):

- `open_targets_overall`
- `open_targets_genetic_association`
- `open_targets_rna_expression`
- `open_targets_affected_pathway`
- `open_targets_clinical`
- `open_targets_literature`

`pathway_group` is attached from the curated `map/gene_pathway.csv`
(`null` if the gene is not in the map).

### genetic_support (0..1)
```
genetic_support = 0.5*neglog10p_norm + 0.2*study_count_norm + 0.3*ot_genetic
  neglog10p_norm   = min(1, best_neglog10p / 30.0)
  study_count_norm = min(1, gwas_study_count / 5.0)
  ot_genetic       = open_targets_genetic_association (or 0 if no OT match)
```
Provenance / rationale:
- `best_neglog10p` and `gwas_study_count` come from the GWAS Catalog ingest
  (already in `evidence_scores`). Genome-wide significance is ~7.3
  (`p = 5e-8`); APOE reaches far higher (`-log10(p)` in the hundreds), so the
  cap of **30.0** keeps a single dominant locus from swamping the scale
  while still saturating strong loci.
- `gwas_study_count` cap of **5.0** rewards replication across studies.
- `ot_genetic` is Open Targets' own genetic-association datatype score.

Components stored under `evidence_scores.genetic_support_components`:
`neglog10p_norm`, `study_count_norm`, `ot_genetic`, the raw inputs, the
`weights`, and the `normalization` caps.

### functional_support (0..1, or null) -- OT L2G (aggregated, colocalisation-integrating)
```
functional_support = clamp01(base_l2g + coloc_bonus)
  base_l2g    = max OT Locus-to-Gene (L2G) score for the gene across all loci
                in functional_links.jsonl (0 if the gene has L2G rows but no
                positive score)
  coloc_bonus = up to +0.15 cell-type-relevance bonus when the gene has any
                GWAS->QTL colocalisation link in a brain-relevant biosample
                (microglia highest); 0 otherwise
  (null when the gene has NO functional_links at all)
```
This is the **real functional / eQTL layer**, sourced entirely from the Open
Targets fine-mapping pipeline. It is built by
`ingest/open_targets_l2g.py` -> `normalize/open_targets_l2g.py` ->
`functional_links.jsonl`, then aggregated per gene here.

`functional_links.jsonl` is joined to each gene by **Ensembl `gene_id` first,
then by `gene_symbol`** (the matched key is stored as
`functional_support_components.functional_links_match`). L2G is a supervised
model that **integrates colocalisation and QTL evidence across many studies**
into a single locus-to-gene score, so it is the **primary** functional signal
here. Genes with **no** functional_links get `functional_support = null` (not 0)
with the note stored verbatim:
> no OT L2G/QTL link

**Cell-type relevance bonus.** If a gene has any `gwas_qtl_colocalisation` link
in a brain-relevant biosample (biosample name contains one of *microglia,
astrocyte, neuron, oligodendro, brain, cortex, OPC*), the single highest-matching
bonus below is added (bonuses are **not** summed), then the total is clamped to
`[0, 1]`:

| biosample contains | bonus |
| --- | --- |
| `microglia` | +0.15 |
| `astrocyte` | +0.12 |
| `neuron` | +0.1 |
| `oligodendro` | +0.1 |
| `opc` | +0.1 |
| `cortex` | +0.08 |
| `brain` | +0.08 |

**Important empirical finding:** raw Open Targets **GWAS->QTL colocalisation is
sparse / near-empty for Alzheimer disease** (the current build produced **0**
colocalisation links across ~1,865 credible sets), so in practice
`functional_support` is driven by `base_l2g` and the `coloc_bonus` is currently
`0.0` for every gene. The bonus machinery is in place for when brain-QTL
colocalisation is available (and see the eQTL Catalogue note below).

Components stored under `evidence_scores.functional_support_components`:
`method`, `max_l2g`, `n_l2g_loci`, `best_l2g_locus`, `has_brain_qtl_coloc`,
`coloc_cell_types`, `coloc_bonus`, `ot_rna_expression`, `ot_affected_pathway`
(the last two are retained as **secondary recorded components** from the old
proxy, no longer used in the score), and `functional_links_match`.

`evidence_scores._formulas` restates these formulas inside every gene record.

---

## 2. Pathway scores (written into `pathways.jsonl` -> `scores`)

### Mechanism crosswalk (pathway -> trial vocabulary)
The pathway `mechanism_group` vocabulary and the trial `mechanism_group`
vocabulary differ slightly. The crosswalk is applied transparently and also
stored in each record's `scores.crosswalk_note`:

| pathway `mechanism_group` | trial `mechanism_group` |
| --- | --- |
| `amyloid` | `amyloid` |
| `tau` | `tau` |
| `microglia_immune` | `inflammation_microglia` |
| `lipid_metabolism` | `lipid_metabolism` |
| `vascular` | `vascular` |
| `synaptic_neuronal` | `synaptic_neuroprotection` |
| `endocytosis_endosomal` | (none) |
| `epigenetic_transcription` | (none) |
| `other` | `other` |

`(none)` means there is no direct trial mechanism group; those pathways get
`trial_count = 0` and `clinical_translation = 0.0` with note
`"no mapped trials"`.

### Phase scoring (per trial)
Each trial's `phases` tokens are mapped to a `phase_score`; the trial's score is
the **max** across its tokens. An `overall_status` of `APPROVED` /
`APPROVED_FOR_MARKETING` forces `1.0`. Missing / `NA` / observational -> 0.1.

| phase token | phase_score |
| --- | --- |
| `PHASE4` | 1.0 |
| `PHASE3` | 0.9 |
| `PHASE2_PHASE3` | 0.75 |
| `PHASE2/PHASE3` | 0.75 |
| `PHASE2` | 0.6 |
| `PHASE1_PHASE2` | 0.4 |
| `PHASE1/PHASE2` | 0.4 |
| `PHASE1` | 0.3 |
| `EARLY_PHASE1` | 0.2 |
| (anything else / NA / observational) | 0.1 |

### clinical_translation (0..1)
```
clinical_translation = 0.6*max_phase_score
                     + 0.25*min(1, trial_count/20.0)
                     + 0.15*has_results_fraction
```
Where the terms are computed over the trials whose (crosswalked) mechanism
matches the pathway. `has_results_fraction` = fraction of mapped trials with
`has_results == true`. If there are no mapped trials the score is `0.0` with
note `"no mapped trials"`.

### clinical_saturation (0..1)
```
clinical_saturation = min(1, trial_count / 50.0)
```
Raw `trial_count` is kept in the record.

### combined_support (0..1)
Mean over the pathway's **matched** member genes (member symbols joined into the
enriched `genes.jsonl` by symbol) of:
```
0.6*genetic_support + 0.4*(functional_support or genetic_support)
```
`functional_support` falls back to `genetic_support` when it is null (a gene
may have GWAS genetics but no OT L2G functional_link). `member_gene_count`
and `member_genes_matched` are stored so coverage is visible.

### translation_gap (0..1)
```
translation_gap = combined_support * (1 - clinical_translation)
```
**Higher = strong genetics/function but little clinical activity = a
translational opportunity / gap.**

Each pathway record's `scores` object stores: `clinical_translation`,
`clinical_saturation`, `combined_support`, `translation_gap`, `trial_count`,
`mapped_trial_mechanism`, `max_phase_score`, `has_results_fraction`,
`member_gene_count`, `member_genes_matched`, `clinical_translation_note`,
`crosswalk_note`, and `_formulas`.

---

## 3. Provenance summary

| Score | Source(s) | Proxy? |
| --- | --- | --- |
| `genetic_support` | GWAS Catalog (best -log10 p, study count) + Open Targets genetic_association | No |
| `functional_support` | Open Targets L2G (max across loci) + brain-QTL colocalisation bonus, from `functional_links.jsonl` | No (real functional layer) |
| Open Targets `open_targets_*` | Open Targets Platform association scores | No |
| `pathway_group` | curated `map/gene_pathway.csv` | Curated |
| `clinical_translation` / `clinical_saturation` | ClinicalTrials.gov trials (phase, count, has_results) via mechanism crosswalk | No |
| `combined_support` | derived from member-gene `genetic_support`/`functional_support` | Mixed |
| `translation_gap` | `combined_support * (1 - clinical_translation)` | Derived |

### Functional layer status & future work
- **`functional_support` is now a real functional layer** built from the Open
  Targets Locus-to-Gene (L2G) model, aggregated per gene across all fine-mapped
  loci (`functional_links.jsonl`). L2G already integrates colocalisation and QTL
  evidence across many studies into one score, so it is the primary signal.
- **Raw GWAS->QTL colocalisation is sparse for AD.** The current build has
  **0** colocalisation links, so the brain-cell-type `coloc_bonus` is `0.0`
  everywhere today; the bonus machinery (microglia/astrocyte/neuron/…) is ready
  for when brain-QTL colocalisation becomes available.
- **eQTL Catalogue (optional future work).** A dedicated brain-cell-type eQTL
  enrichment (e.g. the eQTL Catalogue, `evidence_type = "eqtl_catalogue"` in the
  functional_link schema) is an optional future addition that would populate the
  cell-type bonus directly; it is **not** integrated yet.
