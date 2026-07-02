# Track B per-entity METRICS layer

`score/entity_metrics.py` emits **one flat, machine-readable metrics record per
entity** (gene / variant / pathway) to
`data/processed/translational-evidence/entity_metrics.jsonl`
(schema `shared/schemas/entity_metric.schema.json`). This document is the field
reference: every metric, its exact definition + formula + provenance, grouped by
metric group and split by `entity_type`.

Counts in this doc are from the `2026-07-02` build:
**7,278** records — **1,484 gene**, **5,786 variant**, **8 pathway**.

---

## Design principle — primitives only; verdicts derived by agents

This layer ships **primitives only**: **counts** (`n_*`), **raw observed /
external values** (`best_neglog10p`, `max_l2g`, Open Targets datatype scores),
**booleans** (`has_approval`), small **lists** (cell types, disease groups,
buckets), and **simple `a/b` ratios**. There are **no weighted 0-1 composites**
(no `0.5*x + 0.3*y`) and **no verdict labels**. Opinionated judgements like
`"under-researched"`, `"under-translated"`, `"emerging"`, `"contradicted"`, or
`"opportunity"` are **NOT shipped here** — a downstream **agent composes them**
from these primitives (see [worked examples](#worked-examples--composing-a-verdict-from-primitives)).

This is deliberate. The previous build shipped weighted composites
(`genetic.genetic_support`, `functional.functional_support`,
`composite.translation_gap`); those bake an author's opinion into a single
number. They have been **REMOVED**. Their raw **components** are retained as
standalone primitives so an agent can weight them however it likes:

| removed weighted composite | retained raw primitives instead |
| --- | --- |
| `genetic.genetic_support` | `genetic.best_neglog10p`, `genetic.best_p_value`, `genetic.n_gwas_studies`, `genetic.n_gwas_associations`, `genetic.ot_genetic_association` |
| `functional.functional_support` | `functional.max_l2g`, `functional.n_l2g_loci`, `functional.n_qtl_coloc`, `functional.cell_types` |
| `composite.translation_gap` | genetics primitives above **vs.** `clinical.*` counts + `ratios.*` (agent forms its own gap) |

Why this shape:

- **Explainable.** Every metric carries a short `source` string stating exactly
  which input file/field(s) produced it; ratios also carry a `formula` note.
  Nothing is fabricated; every value is copied verbatim from a processed input
  or recomputed by a documented rule over those inputs.
- **Parseable + extensible.** Metric keys are **dotted `"<group>.<name>"`**
  strings so an agent can select a whole group (`genetic.*`, `clinical.*`).
  `additionalProperties` is `true` at every level of the schema, so an agent may
  **attach its own metric keys** (e.g. `agent.under_researched`) to the same
  record without breaking validation.
- **Reproducible.** Recency metrics use `CURRENT_YEAR` (default `2026`,
  overridable via the `TE_CURRENT_YEAR` env var) as "now" — never
  `datetime.today()` — so a given input always yields the same output. A year is
  "recent" when `year >= CURRENT_YEAR - RECENT_WINDOW` with `RECENT_WINDOW = 3`.

### Record shape

```json
{
  "entity_type": "gene",          // "gene" | "variant" | "pathway"
  "entity_id": "ENSG00000130203", // gene_id | "variant:"+rsid | pathway_id
  "label": "APOE",                // gene symbol | rsid | pathway label
  "pathway_group": "lipid_metabolism",
  "disease_groups": ["alzheimer", "lewy_body_dementia", "mixed_dementia"],
  "note": "Metrics are PRIMITIVES … higher-order judgements are COMPOSED BY AGENTS …",
  "metrics": {
    "genetic.best_neglog10p": { "value": 302.7, "source": "genes.jsonl:…best_neglog10p (raw -log10 p)" },
    "clinical.stopped_ratio": { "value": 0.0476, "source": "…", "formula": "n_stopped/n_trials" }
    // counts/raw/bools wrapped as {value, source}; ratios add {formula}; null value = not computable
  }
}
```

Each metric value is wrapped as `{"value": …, "source": …}`; **ratios add a
`"formula"` key**. A `null` value means the metric was not computable for that
entity (e.g. no matching trials, or a ratio whose denominator is `0`), and its
`source`/`formula` says why. Every record also carries a top-level **`note`**
restating that these are primitives and that verdicts are agent-composed.

### Which metric groups exist per entity_type

| group             | gene | variant | pathway |
| ----------------- | :--: | :-----: | :-----: |
| `genetic.*`       |  ✅  |   ✅    |         |
| `functional.*`    |  ✅  |         |         |
| `clinical.*`      |  ✅  |         |   ✅    |
| `literature.*`    |  ✅  |         |   ✅    |
| `temporal.*`      |  ✅  |   ✅    |   ✅    |
| `cross_disease.*` |  ✅  |   ✅    |         |
| `mechanism.*`     |  ✅  |         |         |
| `open_targets.*`  |  ✅  |         |         |
| `support.*`       |      |         |   ✅    |
| `links.*`         |      |   ✅    |         |
| `ratios.*`        |  ✅  |         |   ✅    |

---

## Shared conventions

- **`max_phase`** is a **string LABEL** (`PHASE4`, `PHASE3`, …, `NA`), not a
  numeric score. Each trial's `phases[]` are normalised to canonical tokens
  (empty/observational → `NA`); an entity's `max_phase` is the highest-ranked
  label over its trials using the ordinal rank `EARLY_PHASE1 < PHASE1 <
  PHASE1_PHASE2 < PHASE2 < PHASE2_PHASE3 < PHASE3 < PHASE4 < APPROVED`
  (`NA`/unknown ranks lowest). `clinical.n_by_phase` is a **dict of counts**
  (`{label: n_trials}`) so an agent can weight phases itself.
- **Stopped** trial = `overall_status` in `{TERMINATED, WITHDRAWN, SUSPENDED}`.
- **Completed** trial = `overall_status == COMPLETED`.
- **Approval** = any trial with `overall_status == APPROVED_FOR_MARKETING`.
- **Drug** intervention = `interventions[].type` in `{DRUG, BIOLOGICAL}`; drug
  counts are over distinct lowercased names.
- **Effect direction** of a GWAS association → `risk` / `protective` / `None` by
  priority: `effect.direction` (`increase`→risk, `decrease`→protective), then
  `effect.odds_ratio` (`>1`→risk, `<1`→protective), then `effect.beta`
  (`>0`→risk, `<0`→protective). No usable signal → `None` (does not count).
- **`direction_agreement_ratio`** = `max(n_risk, n_protective) / (n_risk +
  n_protective)` (∈ [0.5, 1.0]); `null` when there are no directional calls.
- **Ratios (`a/b`)** are `null` when `b == 0` (or either side is `null`); each
  carries a `formula` note stating the exact `a/b`.
- **Mechanism → trials crosswalk.** A gene inherits clinical signals via its
  `gene_pathways_api.jsonl` **`primary_bucket`** → the matching `pathways.jsonl`
  record → its `scores.mapped_trial_mechanism` → `trials.jsonl` rows with that
  `mechanism_group`. A pathway uses its own `scores.mapped_trial_mechanism`
  directly. When there is no mechanism / no mapped trials, counts are `0`,
  ratios/`max_phase` are `null`, `has_approval` is `false`.

---

## GENE metrics (`entity_type == "gene"`)

`entity_id` = `gene_id`; `label` = `symbol`; `pathway_group` = `primary_bucket`.
1,484 records.

### `genetic.*` — genetic counts, raw significance, effect direction

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `genetic.n_gwas_studies` | int·null | distinct GWAS studies for this gene | `genes.jsonl:evidence_scores.gwas_study_count` |
| `genetic.n_gwas_associations` | int·null | GWAS associations for this gene | `genes.jsonl:evidence_scores.gwas_association_count` |
| `genetic.n_variants` | int | distinct `variant.rsid` where `reported_genes` contains the symbol | `gwas_associations.jsonl` (recomputed) |
| `genetic.best_neglog10p` | number·null | best (max) −log10(p) — **raw** | `genes.jsonl:evidence_scores.best_neglog10p` |
| `genetic.best_p_value` | number·null | smallest p — **raw** | `genes.jsonl:evidence_scores.best_p_value` |
| `genetic.n_risk` | int | # directional associations calling risk | `gwas_associations.jsonl` (direction rule) |
| `genetic.n_protective` | int | # directional associations calling protective | `gwas_associations.jsonl` |
| `genetic.n_conflicting` | int | `min(n_risk, n_protective)` — minority-direction count | recomputed |
| `genetic.direction_agreement_ratio` | number·null | `max(n_risk, n_protective)/(n_risk+n_protective)` | recomputed; `null` when no directional calls |
| `genetic.ot_genetic_association` | number·null | **raw** OT genetic-association datatype score (on gene row) | `genes.jsonl:…open_targets_genetic_association` |
| `genetic.ot_overall` | number·null | **raw** OT overall score (on gene row) | `genes.jsonl:…open_targets_overall` |

### `functional.*` — functional / L2G / eQTL components (no composite)

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `functional.n_l2g_loci` | int·null | # loci with an L2G link to this gene | `…functional_support_components.n_l2g_loci` |
| `functional.max_l2g` | number·null | max Open Targets Locus-to-Gene score — **raw** | `…functional_support_components.max_l2g` |
| `functional.n_qtl_coloc` | int | # QTL-colocalisation functional_links rows (else 1 if `has_brain_qtl_coloc`) | `functional_links.jsonl` / `…functional_support_components.has_brain_qtl_coloc` |
| `functional.cell_types` | array[string] | cell types from coloc links (else `coloc_cell_types`) | `functional_links.jsonl:cell_type` |
| `functional.ot_rna_expression` | number·null | **raw** OT RNA-expression datatype score | `genes.jsonl:…open_targets_rna_expression` |
| `functional.ot_affected_pathway` | number·null | **raw** OT affected-pathway datatype score | `genes.jsonl:…open_targets_affected_pathway` |

### `clinical.*` — mechanism-inherited clinical counts

Via `primary_bucket` → pathway → `mapped_trial_mechanism` → the `trials.jsonl`
cohort. No numeric phase score is emitted — only the `max_phase` **label** and
the `n_by_phase` **count dict**.

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `clinical.mechanism` | string·null | the gene's `primary_bucket` (pathway_group used) | `gene_pathways_api.jsonl:primary_bucket` |
| `clinical.n_trials` | int | # trials in the mechanism cohort | mechanism crosswalk over `trials.jsonl` |
| `clinical.n_by_phase` | object | `{phase_label: n_trials}` over the cohort | crosswalk; per-trial max phase label |
| `clinical.max_phase` | string·null | highest phase **label** over cohort trials | crosswalk |
| `clinical.n_stopped` | int | # cohort trials with stopped status | crosswalk; TERMINATED/WITHDRAWN/SUSPENDED |
| `clinical.stopped_ratio` | number·null | `n_stopped/n_trials` | crosswalk; `null` when `n_trials == 0` |
| `clinical.n_completed` | int | # cohort trials `COMPLETED` | crosswalk |
| `clinical.n_with_results` | int | # cohort trials with `has_results` | crosswalk |
| `clinical.has_approval` | bool | any cohort trial `APPROVED_FOR_MARKETING` | crosswalk |
| `clinical.n_drugs` | int | # distinct DRUG/BIOLOGICAL intervention names | crosswalk |

### `literature.*` — paper counts for this gene

Distinct `supporting_paper_ids` across `evidence_type == "gene"` rows in
`shared/topic_evidence_links.jsonl`. Recency/first/latest years come from Track
A `papers.jsonl` (`pmid → year`); when that snapshot is absent, `n_recent_papers`
/ `first_pub_year` / `latest_pub_year` are `null`.

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `literature.n_papers` | int | # distinct supporting paper ids for this gene | `shared/topic_evidence_links.jsonl` |
| `literature.n_recent_papers` | int·null | # of those with `year >= CURRENT_YEAR - 3` | `track_a_snapshot/papers.jsonl` |
| `literature.first_pub_year` | int·null | earliest paper year | `track_a_snapshot/papers.jsonl` |
| `literature.latest_pub_year` | int·null | latest paper year | `track_a_snapshot/papers.jsonl` |

### `temporal.*` — GWAS recency

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `temporal.first_gwas_year` | int·null | earliest matching-association pub year | `gwas_associations.jsonl:publication.date` |
| `temporal.latest_gwas_year` | int·null | latest matching-association pub year | `gwas_associations.jsonl:publication.date` |
| `temporal.n_recent_gwas` | int | # associations with `year >= CURRENT_YEAR - 3` | `…publication.date`; `CURRENT_YEAR` env-overridable |

### `cross_disease.*` — pleiotropy across dementia subtypes

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `cross_disease.disease_groups` | array[string] | controlled disease groups this gene spans | `genes.jsonl:disease_groups` |
| `cross_disease.n_disease_groups` | int | count of the above | `genes.jsonl:disease_groups` length |
| `cross_disease.direction_flip_across_disease` | bool | `True` iff ≥2 disease groups have **opposing dominant** effect directions | per-`disease_group` dominant direction over `gwas_associations.jsonl` |

### `mechanism.*` — multi-valued AD mechanism membership

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `mechanism.buckets` | array[string] | all mechanism buckets this gene signals into | `gene_pathways_api.jsonl:buckets` |
| `mechanism.n_buckets` | int | count of the above | `gene_pathways_api.jsonl:buckets` length |

### `open_targets.*` — Open Targets' OWN external reference scores (RAW)

These are **Open Targets' externally-maintained harmonic-sum datatype scores**,
kept **raw** alongside our stats. They are **NOT our statistics** — every
`source` reads `open_targets (external harmonic-sum score)`. Taken from
`target_evidence.jsonl` (the **Alzheimer disease** row preferred; else the
highest-`overall` disease row); every key is `null` when the gene has no OT row.

`open_targets.overall`, `open_targets.genetic_association`,
`open_targets.clinical`, `open_targets.literature`,
`open_targets.affected_pathway`, `open_targets.rna_expression`,
`open_targets.animal_model`, `open_targets.genetic_literature`
— each `number·null`.

### `ratios.*` — simple `a/b` primitives (null when denominator 0)

| metric | formula | source |
| --- | --- | --- |
| `ratios.studies_per_trial` | `n_gwas_studies / n_trials` | gene study count ÷ `clinical.n_trials` |
| `ratios.papers_per_study` | `n_papers / n_gwas_studies` | `literature.n_papers` ÷ gene study count |
| `ratios.trials_per_paper` | `n_trials / n_papers` | `clinical.n_trials` ÷ `literature.n_papers` |
| `ratios.recent_gwas_fraction` | `n_recent_gwas / n_gwas_studies` | `temporal.n_recent_gwas` ÷ gene study count |

---

## VARIANT metrics (`entity_type == "variant"`)

One record per distinct `variant.rsid` in `gwas_associations.jsonl`.
`entity_id` = `"variant:" + rsid`; `label` = `rsid`; `pathway_group` = `null`.
5,786 records.

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `genetic.n_associations` | int | # GWAS rows with this rsid | `gwas_associations.jsonl` |
| `genetic.n_studies` | int | # distinct `study_accession` for this rsid | `gwas_associations.jsonl` |
| `genetic.best_neglog10p` | number·null | max −log10(`p_value`) over this rsid — **raw** | `gwas_associations.jsonl` (recomputed) |
| `genetic.direction_agreement_ratio` | number·null | `max(n_risk,n_protective)/(n_risk+n_protective)` for this rsid | `gwas_associations.jsonl` (direction rule) |
| `links.reported_genes` | array[string] | sorted distinct `reported_genes` across this rsid | `gwas_associations.jsonl:reported_genes` |
| `links.l2g_genes` | array[string] | sorted distinct L2G-linked gene symbols for this rsid | `functional_links.jsonl:gene_symbol` |
| `cross_disease.disease_groups` | array[string] | sorted distinct `disease_group` across this rsid | `gwas_associations.jsonl:disease_group` |
| `temporal.first_year` | int·null | earliest association pub year for this rsid | `gwas_associations.jsonl:publication.date` |
| `temporal.latest_year` | int·null | latest association pub year for this rsid | `gwas_associations.jsonl:publication.date` |
| `temporal.n_recent` | int | # associations with `year >= CURRENT_YEAR - 3` | `…publication.date`; env-overridable |

---

## PATHWAY metrics (`entity_type == "pathway"`)

`entity_id` = `pathway_id`; `label` = `pathway.label`; `pathway_group` =
`mechanism_group`. 8 records.

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `support.n_genes` | int | # member genes | `pathways.jsonl:scores.member_gene_count` |
| `support.mean_best_neglog10p` | number·null | mean `best_neglog10p` over matched member genes | `genes.jsonl:evidence_scores.best_neglog10p` (mean) |
| `clinical.n_trials` | int | # trials in the mapped-mechanism cohort | crosswalk over `trials.jsonl` |
| `clinical.n_by_phase` | object | `{phase_label: n_trials}` over the cohort | crosswalk |
| `clinical.max_phase` | string·null | highest phase **label** over cohort trials | crosswalk |
| `clinical.n_stopped` | int | # cohort trials stopped | crosswalk |
| `clinical.stopped_ratio` | number·null | `n_stopped/n_trials` | crosswalk; `null` when `n_trials == 0` |
| `clinical.n_with_results` | int | # cohort trials with `has_results` | crosswalk |
| `clinical.has_approval` | bool | any cohort trial `APPROVED_FOR_MARKETING` | crosswalk |
| `clinical.n_drugs` | int | # distinct DRUG/BIOLOGICAL intervention names | crosswalk |
| `literature.n_papers` | int | union of member-gene paper ids | `shared/topic_evidence_links.jsonl` (union) |
| `temporal.first_trial_year` | int·null | earliest cohort `start_date` year | `trials.jsonl:start_date` |
| `temporal.latest_trial_year` | int·null | latest cohort `start_date` year | `trials.jsonl:start_date` |
| `temporal.n_recent_trials` | int | # cohort trials with `year >= CURRENT_YEAR - 3` | `trials.jsonl:start_date`; env-overridable |
| `ratios.trials_per_gene` | number·null | `n_trials / n_genes` | `clinical.n_trials` ÷ `support.n_genes` |
| `ratios.studies_per_trial` | number·null | `sum(member-gene gwas_study_count) / n_trials` | member study sum ÷ `clinical.n_trials` |

---

## Worked recipes — AGENT-COMPOSED from primitives (NOT shipped)

Every verdict below is **AGENT-COMPOSED** from the primitives above — **none is
stored in `entity_metrics.jsonl`**. There is **no predefined 0-1 score** for any
of these questions; the layer ships only the transparent counts / raw values /
ratios, and an agent picks its own thresholds and combines them. `val(x, k)` reads
`x["metrics"][k]["value"]`. The thresholds shown are illustrative (an agent may
choose others). Numbers are from the real `2026-07-02` build.

### 1. "Under-researched" (gene) — strong genetics, thin literature/clinical

Compose from **high `genetic.best_neglog10p`** (real signal) AND **low
`literature.n_papers`** AND **`clinical.n_trials == 0`** (little attention has
followed the genetics):

```python
def under_researched(g):
    return (val(g, "genetic.best_neglog10p") is not None
            and val(g, "genetic.best_neglog10p") >= 20      # well past genome-wide sig
            and val(g, "literature.n_papers") <= 5          # barely studied
            and val(g, "clinical.n_trials") == 0)           # nothing in the clinic
```

- **EXOC3L2** → `best_neglog10p = 115.0`, `n_gwas_studies = 3`,
  `n_variants = 6`, `literature.n_papers = 3` (`n_recent_papers = 0`),
  `clinical.n_trials = 0`, `ratios.papers_per_study = 1.0`,
  `ratios.trials_per_paper = 0.0`, `mechanism.n_buckets = 0`.
  A genome-wide-significant locus (115 ≫ 8) with only **3** papers and **no**
  trials → the agent labels it **"under-researched"**. This layer only ships the
  counts; `ratios.papers_per_study = 1.0` (vs. **10.2** for APOE) quantifies the
  thin literature-per-study follow-through.

Contrast: **APOE** → `best_neglog10p = 302.7` but `n_papers = 418`,
`n_recent_papers = 75`, `n_trials = 21` → **not** under-researched (agent
skips it because literature/clinical follow-through is high).

### 2. "Under-translated" (gene) — strong biology, little/no clinical follow-through

Compose from **high `genetic.best_neglog10p`** AND **healthy
`literature.n_papers`** AND **low `clinical.n_trials` / low `clinical.max_phase`**
(the biology is established but little/nothing has reached the clinic). The
`ratios.studies_per_trial` ratio quantifies the gap: it is **`null`** when there
are zero trials (the strongest case), and **high** when many GWAS studies point
at a mechanism with only a few trials.

```python
def under_translated(g):
    genetics = val(g, "genetic.best_neglog10p")
    n_trials = val(g, "clinical.n_trials")
    studies_per_trial = val(g, "ratios.studies_per_trial")  # null when n_trials==0
    return (genetics is not None and genetics >= 30
            and val(g, "literature.n_papers") >= 20         # well studied
            and val(g, "clinical.has_approval") is False
            # either zero trials (studies_per_trial is null), or many
            # studies-per-trial (a few trials for a lot of genetics):
            and (n_trials == 0 or (studies_per_trial or 0) >= 2))
```

- **TOMM40** → `best_neglog10p = 219.1`, `n_gwas_studies = 21`,
  `n_variants = 27`, `functional.max_l2g = 0.649`, `functional.n_l2g_loci = 39`,
  `open_targets.genetic_association = 0.577`, `literature.n_papers = 49`
  (`n_recent_papers = 3`), yet `clinical.n_trials = 0`, `clinical.max_phase =
  None`, `has_approval = false`, `ratios.studies_per_trial = null` (0 trials),
  `ratios.trials_per_paper = 0.0`. Strong, well-studied genetics with **zero**
  clinical activity → the agent labels it **"under-translated"**. (Its
  `primary_bucket` is `endocytosis_endosomal`, whose mapped trial mechanism has
  **no** trials in this corpus, so `clinical.n_trials == 0` — a fact the agent
  reads straight off `clinical.mechanism`/`clinical.n_trials`.)
- Contrast the *few-trials* case: **APOE** → `n_gwas_studies = 41`,
  `n_trials = 21`, `ratios.studies_per_trial = 1.95` — plenty of genetics **and**
  real clinical activity, so the agent does **not** call it under-translated.

### 3. "Contradicted / direction-conflicted" (gene)

Compose from **low `genetic.direction_agreement_ratio`** AND **`n_conflicting`
≥ 2** (real minority-direction evidence). Optionally require
`cross_disease.direction_flip_across_disease == true` to specialise to
"effect flips *between dementia subtypes*".

```python
def direction_conflict(g):
    da = val(g, "genetic.direction_agreement_ratio")
    return da is not None and da < 0.7 and val(g, "genetic.n_conflicting") >= 2
```

Real hits: **EPHA1** (`ratio = 0.500`, `n_risk = 5`, `n_protective = 5`),
**MS4A6A** (`0.571`, `n_conflicting = 3`), **CLU** (`0.643`, `n_conflicting = 5`).

### 4. "Under-translated opportunity" (pathway)

Compose from strong member-gene genetics + broad literature but **no / few
trials**: high `support.mean_best_neglog10p`, high `literature.n_papers`,
`clinical.n_trials == 0` (so `ratios.trials_per_gene == 0`):

```python
def pathway_under_translated(p):
    return (val(p, "clinical.n_trials") == 0
            and val(p, "support.mean_best_neglog10p") >= 15
            and val(p, "literature.n_papers") >= 100)
```

Real hits: **Endocytosis / endosomal trafficking**
(`n_genes = 184`, `mean_best_neglog10p = 17.9`, `n_papers = 455`,
`n_trials = 0`, `trials_per_gene = 0.0`) and **Epigenetic / transcriptional
regulation** (`188` genes, `n_papers = 198`, `n_trials = 0`). Well-supported,
widely-published pathways with **no mapped trials** — the agent flags them; the
layer ships only the counts/ratios.

### 5. "Emerging" (gene) — recent GWAS activity dominates

The directive's emerging signal is **high `temporal.n_recent_gwas`** and/or
**high `ratios.recent_gwas_fraction`** (`n_recent_gwas / n_gwas_studies`, with
"recent" = `year >= CURRENT_YEAR - 3`):

```python
def emerging_gene(g):
    frac = val(g, "ratios.recent_gwas_fraction")   # null when n_gwas_studies==0
    return (val(g, "temporal.n_recent_gwas") >= 2
            and frac is not None and frac >= 0.5
            and val(g, "genetic.best_neglog10p") >= 8)   # ~genome-wide sig
```

Honest note for the `2026-07-02` build: **no gene passes** this — every gene's
`temporal.n_recent_gwas` is `0` (the latest *gene-attributed* GWAS publication
year is 2021, below `CURRENT_YEAR - 3 = 2023`), so `ratios.recent_gwas_fraction`
is `0.0` for all genes. That is an honest signal, not a bug: the layer ships the
counts and lets the agent conclude "nothing is emerging by GWAS recency at the
gene level right now." Re-running with a different window (e.g.
`TE_CURRENT_YEAR=2022`) shifts it deterministically.

### 6. "Recently-emerging locus" (variant) — where emergence actually shows

At the **variant** level (which carries per-`publication.date` recency across
2025/2026 GWAS), emergence has real hits. Compose from a recent, strong signal:
`temporal.latest_year >= CURRENT_YEAR - 2` AND `temporal.n_recent >= 1` AND
`genetic.best_neglog10p` high:

```python
def emerging_locus(v):
    return (val(v, "temporal.latest_year") is not None
            and val(v, "temporal.latest_year") >= CURRENT_YEAR - 2
            and val(v, "temporal.n_recent") >= 1
            and val(v, "genetic.best_neglog10p") >= 8)  # ~genome-wide sig
```

Real hits: **rs6733839** (`latest_year = 2026`, `n_recent = 9`,
`best_neglog10p = 138.7`, `n_studies = 22`) and **rs4663105**
(`latest_year = 2025`, `n_recent = 10`, `best_neglog10p = 43.5`). Because
recency uses `CURRENT_YEAR` (not wall-clock), re-running with
`TE_CURRENT_YEAR=2024` shifts the window deterministically.

### 7. "Clinically contested" (gene) — lots of trials, high failure, no approval

Compose from **high `clinical.n_trials`** AND **high `clinical.stopped_ratio`**
AND **`clinical.has_approval == false`** (a mechanism the clinic has attacked
hard, with a high share of stopped/terminated trials and still no approval):

```python
def clinically_contested(g):
    sr = val(g, "clinical.stopped_ratio")            # null when n_trials==0
    return (val(g, "clinical.n_trials") >= 5
            and sr is not None and sr >= 0.3
            and val(g, "clinical.has_approval") is False)
```

Real hits (all inherit the **amyloid** mechanism cohort — 96 trials,
`n_stopped = 36`, `stopped_ratio = 0.375`, `has_approval = false`): **RTN2**
(`best_neglog10p = 97.0`), **MMP13** (`43.7`), **ADAM10** (`10.5`). The agent
reads a genome-wide-significant gene mapped to a heavily-tried mechanism where a
third of trials stopped and nothing is approved → **"clinically contested"**. The
layer ships only `n_trials` / `n_stopped` / `stopped_ratio` / `has_approval`; the
"contested" call is the agent's.

---

## Regenerate

```bash
python3 translational-evidence/score/entity_metrics.py
python3 translational-evidence/validate.py \
    data/processed/translational-evidence/entity_metrics.jsonl   # -> OK
```

The metrics also travel onto the evidence graph — the full `metrics` object is
attached to each graph node and a compact set of flat, queryable props is hoisted
for Neo4j / HTML filters (see `RUNBOOK.md` §9). No counts are hardcoded; a re-run
against the full corpus just works.
