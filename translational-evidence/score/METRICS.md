# Track B per-entity METRICS layer

`score/entity_metrics.py` emits **one flat, machine-readable metrics record per
entity** (gene / variant / pathway) to
`data/processed/translational-evidence/entity_metrics.jsonl`
(schema `shared/schemas/entity_metric.schema.json`). This document is the field
reference: every metric, its exact definition + formula + provenance, grouped by
metric group and split by `entity_type`.

Counts in this doc are from the `2026-07-01` build:
**6,318** records — **523 gene**, **5,786 variant**, **9 pathway**.

---

## Design principle — transparent metrics, verdicts derived by agents

This layer exposes **raw, composable signals only**. Each metric is a
**number, boolean, or null** — never a free-text verdict. Opinionated labels
like `"contradicted"`, `"opportunity"`, `"novel"`, `"validated"`, or
`"de-risked"` are **NOT shipped here**: they are **derived downstream by agents**
that compose them from these metrics (see [worked examples](#worked-examples--composing-a-verdict-from-metrics)).

Why this shape:

- **Explainable.** Every metric carries a short `source` string stating exactly
  which input file/field(s) and formula produced it. Nothing is fabricated;
  every value is either copied verbatim from a processed input or recomputed by
  a documented rule over those inputs.
- **Parseable + extensible.** Metric keys are **dotted `"<group>.<name>"`**
  strings so an agent can select a whole group (`genetic.*`, `clinical.*`).
  `additionalProperties` is `true` at every level of the schema, so an agent may
  **attach its own metric keys** (e.g. `agent.contradiction_score`) to the same
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
  "metrics": {
    "genetic.genetic_support": { "value": 0.9668, "source": "genes.jsonl:evidence_scores.genetic_support" },
    "clinical.stopped_ratio":  { "value": 0.1024, "source": "…; n_stopped/n_trials" }
    // … every value wrapped as {value, source}; null value == metric not computable
  }
}
```

Every metric value is wrapped as `{"value": <number|bool|array|null>, "source":
<string>}`. A `null` value means the metric was not computable for that entity
(e.g. no matching trials), and its `source` says why.

### Which metric groups exist per entity_type

| group          | gene | variant | pathway |
| -------------- | :--: | :-----: | :-----: |
| `genetic.*`    |  ✅  |   ✅    |         |
| `functional.*` |  ✅  |         |         |
| `links.*`      |      |   ✅    |         |
| `support.*`    |      |         |   ✅    |
| `clinical.*`   |  ✅  |         |   ✅    |
| `temporal.*`   |  ✅  |   ✅    |   ✅    |
| `cross_disease.*` | ✅ | ✅    |         |
| `composite.*`  |  ✅  |         |   ✅    |

The top-level `disease_groups` array is present on every record.

---

## Shared conventions

- **Phase score** (`clinical.max_phase_score`): each trial's `phases[]` are
  normalised to canonical tokens and mapped to a monotone score — `PHASE4` /
  `APPROVED` = 1.0, `PHASE3` = 0.9, `PHASE2_PHASE3` = 0.75, `PHASE2` = 0.6,
  `PHASE1_PHASE2` = 0.4, `PHASE1` = 0.3, `EARLY_PHASE1` = 0.2; anything else
  (NA / observational / unknown / empty) = 0.1. A trial's score is the max over
  its phases (combined forms like `PHASE2_PHASE3` are matched too); an entity's
  `max_phase_score` is the max over its trials.
- **Stopped** trial = `overall_status` in `{TERMINATED, WITHDRAWN, SUSPENDED}`.
- **Approval** = any trial with `overall_status == APPROVED_FOR_MARKETING`.
- **Drug** intervention = `interventions[].type` in `{DRUG, BIOLOGICAL}`; drug
  counts are over distinct lowercased names.
- **Effect direction** of a GWAS association is classified as `risk` /
  `protective` / `None` by priority: explicit `effect.direction`
  (`increase`→risk, `decrease`→protective), then `effect.odds_ratio`
  (`>1`→risk, `<1`→protective), then `effect.beta` (`>0`→risk, `<0`→protective).
  Associations with no usable directional signal are `None` and do **not** count
  toward `direction_n`.
- **Direction agreement** over a set of directional calls =
  `max(n_risk, n_protective) / (n_risk + n_protective)` (∈ [0.5, 1.0]); `null`
  when there are no directional calls.
- **Mechanism → trials crosswalk.** Genes and pathways inherit clinical signals
  by mechanism: a pathway's `scores.mapped_trial_mechanism` selects
  `trials.jsonl` rows with that `mechanism_group`; a gene reaches the same trial
  cohort via its `pathway_group` → matching pathway record → that mechanism.

---

## GENE metrics (`entity_type == "gene"`)

`entity_id` = `gene_id`; `label` = `symbol`. 523 records.

### `genetic.*` — genetic support & effect-direction consistency

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `genetic.gwas_study_count` | int·null | distinct GWAS studies for this gene | `genes.jsonl:evidence_scores.gwas_study_count` |
| `genetic.gwas_association_count` | int·null | GWAS associations for this gene | `genes.jsonl:evidence_scores.gwas_association_count` |
| `genetic.best_neglog10p` | number·null | best (max) −log10(p) across its associations | `genes.jsonl:evidence_scores.best_neglog10p` |
| `genetic.genetic_support` | number·null | Track A/B genetic-support score (0–1) | `genes.jsonl:evidence_scores.genetic_support` |
| `genetic.ot_genetic_association` | number·null | Open Targets genetic-association datatype score | `genes.jsonl:evidence_scores.open_targets_genetic_association` |
| `genetic.direction_n` | int | # associations mentioning this symbol in `reported_genes` **with a usable direction** | recomputed from `gwas_associations.jsonl` (direction rule above) |
| `genetic.direction_agreement` | number·null | `max(risk, protective) / direction_n` (1.0 = unanimous) | recomputed; `null` when `direction_n == 0` |
| `genetic.n_conflicting` | int | `min(n_risk, n_protective)` — the minority-direction count | recomputed |

`direction_agreement` + `n_conflicting` are the raw signal for
"do GWAS studies disagree on whether this gene raises or lowers risk?" (an agent
composes a "direction conflict" verdict from them; this layer never labels it).

### `functional.*` — functional / eQTL layer

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `functional.functional_support` | number·null | aggregated functional-support score (0–1) | `genes.jsonl:evidence_scores.functional_support` |
| `functional.max_l2g` | number·null | max Open Targets Locus-to-Gene score over loci | `…functional_support_components.max_l2g` |
| `functional.n_l2g_loci` | int·null | # loci with an L2G link to this gene | `…functional_support_components.n_l2g_loci` |
| `functional.ot_rna_expression` | number·null | OT RNA-expression datatype score | `genes.jsonl:evidence_scores.open_targets_rna_expression` |
| `functional.ot_affected_pathway` | number·null | OT affected-pathway datatype score | `genes.jsonl:evidence_scores.open_targets_affected_pathway` |

### `clinical.*` — mechanism-inherited clinical signals

Via `pathway_group` → matching pathway record → its
`scores.mapped_trial_mechanism` → the `trials.jsonl` cohort for that mechanism.
When a gene has no `pathway_group` / no matching pathway, counts are `0`, ratios
/ phase / translation scores are `null`, `has_approval` is `false`, and every
`source` says "no pathway_group / no matching pathway record for this gene".

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `clinical.mechanism` | string·null | the gene's `pathway_group` | `genes.jsonl:evidence_scores.pathway_group` |
| `clinical.n_trials` | int | # trials in the mechanism cohort | mechanism crosswalk over `trials.jsonl` |
| `clinical.max_phase_score` | number·null | max phase score over cohort trials (see phase map) | crosswalk; phase-map |
| `clinical.n_stopped` | int | # cohort trials with stopped status | crosswalk; status ∈ TERMINATED/WITHDRAWN/SUSPENDED |
| `clinical.stopped_ratio` | number·null | `n_stopped / n_trials` | crosswalk; `null` when `n_trials == 0` |
| `clinical.n_with_results` | int | # cohort trials with `has_results` | crosswalk; `trial.has_results` |
| `clinical.has_approval` | bool | any cohort trial `APPROVED_FOR_MARKETING` | crosswalk |
| `clinical.clinical_translation` | number·null | pathway clinical-translation score (0–1) | `pathways.jsonl:scores.clinical_translation` |
| `clinical.clinical_saturation` | number·null | pathway clinical-saturation score (0–1) | `pathways.jsonl:scores.clinical_saturation` |

### `temporal.*` — GWAS recency

Over the publication years of this gene's matching associations.

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `temporal.first_gwas_year` | int·null | earliest matching-association pub year | `gwas_associations.jsonl:publication.date` |
| `temporal.latest_gwas_year` | int·null | latest matching-association pub year | `gwas_associations.jsonl:publication.date` |
| `temporal.n_recent_gwas` | int | # matching associations with `year >= CURRENT_YEAR - 3` | `…publication.date`; `CURRENT_YEAR` env-overridable |

### `cross_disease.*` — pleiotropy across dementia subtypes

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `cross_disease.n_disease_groups` | int | # controlled disease groups this gene spans | `genes.jsonl:disease_groups` length |
| `cross_disease.direction_flip_across_disease` | bool | `True` iff ≥2 disease groups have **opposing dominant** effect directions | per-`disease_group` dominant direction over `gwas_associations.jsonl` (a tie is not dominant) |

### `composite.*`

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `composite.translation_gap` | number·null | the gene's pathway translation-gap score (high genetic/functional support vs. low clinical translation) | `pathways.jsonl:scores.translation_gap` (via `pathway_group`) |

---

## VARIANT metrics (`entity_type == "variant"`)

One record per distinct `variant.rsid` in `gwas_associations.jsonl`.
`entity_id` = `"variant:" + rsid`; `label` = `rsid`; `pathway_group` = `null`.
5,786 records.

### `genetic.*`

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `genetic.n_associations` | int | # GWAS rows with this rsid | `gwas_associations.jsonl` |
| `genetic.n_studies` | int | # distinct `study_accession` for this rsid | `gwas_associations.jsonl` |
| `genetic.best_neglog10p` | number·null | max −log10(`p_value`) over this rsid | `gwas_associations.jsonl` (recomputed) |
| `genetic.direction_agreement` | number·null | `max(risk, protective) / direction_n` for this rsid | `gwas_associations.jsonl` (direction rule) |

### `links.*` — what this locus points at

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `links.reported_genes` | array[string] | sorted distinct `reported_genes` across this rsid's associations | `gwas_associations.jsonl:reported_genes` |
| `links.l2g_genes` | array[string] | sorted distinct L2G-linked gene symbols for this rsid | `functional_links.jsonl:gene_symbol` |

### `cross_disease.*`

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `cross_disease.disease_groups` | array[string] | sorted distinct `disease_group` values across this rsid's associations | `gwas_associations.jsonl:disease_group` |

### `temporal.*`

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `temporal.first_year` | int·null | earliest association pub year for this rsid | `gwas_associations.jsonl:publication.date` |
| `temporal.latest_year` | int·null | latest association pub year for this rsid | `gwas_associations.jsonl:publication.date` |
| `temporal.n_recent` | int | # associations with `year >= CURRENT_YEAR - 3` | `…publication.date`; `CURRENT_YEAR` env-overridable |

---

## PATHWAY metrics (`entity_type == "pathway"`)

`entity_id` = `pathway_id`; `label` = `pathway.label`; `pathway_group` =
`mechanism_group`. 9 records.

### `support.*` — evidence from member genes

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `support.member_gene_count` | int | # member genes | `pathways.jsonl:scores.member_gene_count` |
| `support.mean_genetic_support` | number·null | mean `genetic_support` over matched member genes | `genes.jsonl:evidence_scores.genetic_support` (mean) |
| `support.mean_functional_support` | number·null | mean `functional_support` over matched member genes | `genes.jsonl:evidence_scores.functional_support` (mean) |
| `support.combined_support` | number·null | pathway combined-support score (0–1) | `pathways.jsonl:scores.combined_support` |

### `clinical.*` — trials via `mapped_trial_mechanism`

Trials selected by `mechanism_group == pathway.scores.mapped_trial_mechanism`.
When `mapped_trial_mechanism` is `null`/unmapped, the cohort is empty (counts
`0`, ratios/phase `null`, `has_approval` `false`).

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `clinical.n_trials` | int | # trials in the mapped-mechanism cohort | crosswalk over `trials.jsonl` |
| `clinical.max_phase_score` | number·null | max phase score over cohort trials | crosswalk; phase-map |
| `clinical.n_stopped` | int | # cohort trials with stopped status | crosswalk |
| `clinical.stopped_ratio` | number·null | `n_stopped / n_trials` | crosswalk; `null` when `n_trials == 0` |
| `clinical.n_with_results` | int | # cohort trials with `has_results` | crosswalk |
| `clinical.has_approval` | bool | any cohort trial `APPROVED_FOR_MARKETING` | crosswalk |
| `clinical.n_drugs` | int | # distinct DRUG/BIOLOGICAL intervention names (lowercased) | crosswalk |
| `clinical.clinical_translation` | number·null | pathway clinical-translation score | `pathways.jsonl:scores.clinical_translation` |
| `clinical.clinical_saturation` | number·null | pathway clinical-saturation score | `pathways.jsonl:scores.clinical_saturation` |

### `temporal.*` — trial recency

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `temporal.first_trial_year` | int·null | earliest cohort `start_date` year | `trials.jsonl:start_date` |
| `temporal.latest_trial_year` | int·null | latest cohort `start_date` year | `trials.jsonl:start_date` |
| `temporal.n_recent_trials` | int | # cohort trials with `year >= CURRENT_YEAR - 3` | `trials.jsonl:start_date`; `CURRENT_YEAR` env-overridable |

### `composite.*`

| metric | value | definition / formula | source |
| --- | --- | --- | --- |
| `composite.translation_gap` | number·null | pathway translation-gap score | `pathways.jsonl:scores.translation_gap` |

---

## Worked examples — composing a verdict from metrics

These verdicts are **NOT** stored in `entity_metrics.jsonl`. They are what a
downstream agent computes **from** the metrics above. Numbers are the real
`2026-07-01` build.

### 1. "Contradicted / clinically stalled despite strong genetics" (gene)

Compose from **high `genetic.genetic_support`** AND **high
`clinical.stopped_ratio`** AND **`clinical.has_approval == false`** (optionally
with `clinical.n_trials` large enough to be meaningful):

```python
def contradiction(g):  # g.metrics -> {dotted_key: {value, source}}
    return (val(g, "genetic.genetic_support") >= 0.7
            and val(g, "clinical.stopped_ratio") >= 0.15
            and val(g, "clinical.has_approval") is False
            and val(g, "clinical.n_trials") >= 20)
```

Real hits: **CR1** (`genetic_support=0.861`, `stopped_ratio=0.170`,
`n_trials=106`, `has_approval=false`, mechanism `microglia_immune`) and
**MS4A6A** (`genetic_support=0.707`, same microglia cohort). The agent labels
these "genetically strong but clinically stalled"; the layer just ships the
numbers.

### 2. "Direction conflict" (gene)

Compose from **low `genetic.direction_agreement`** AND **`genetic.n_conflicting`
≥ 2** (real minority-direction evidence, not a single flipped row):

```python
def direction_conflict(g):
    return (val(g, "genetic.direction_agreement") is not None
            and val(g, "genetic.direction_agreement") < 0.7
            and val(g, "genetic.n_conflicting") >= 2)
```

Real hits: **EPHA1** (`direction_agreement=0.500`, `n_conflicting=5`,
`direction_n=10`), **CLU** (`0.643`, `n_conflicting=5`, `direction_n=14`),
**MS4A6A** (`0.571`, `n_conflicting=3`). Add
`cross_disease.direction_flip_across_disease == true` to specialise to
"effect flips *between dementia subtypes*".

### 3. "Under-translated opportunity" (pathway)

Compose from **high `composite.translation_gap`** AND **`clinical.has_approval
== false`** AND **low `clinical.n_trials`** (strong biology, little clinical
attention):

```python
def under_translated(p):
    return (val(p, "composite.translation_gap") >= 0.3
            and val(p, "clinical.has_approval") is False
            and val(p, "clinical.n_trials") <= 5)
```

Real hits: **Endocytosis / endosomal trafficking**
(`translation_gap=0.699`, `combined_support=0.699`, `n_trials=0`,
`has_approval=false`) and **Epigenetic / transcriptional regulation**
(`translation_gap=0.347`, `n_trials=0`). Both have well-supported member genes
(endosomal `mean_functional_support=0.819`) but no mapped trials — an agent
flags them "opportunity", this layer does not.

### 4. "Recently-emerging locus" (variant)

Compose from **`temporal.latest_year >= CURRENT_YEAR - 2`** AND
**`temporal.n_recent >= 1`** AND a strong association
(`genetic.best_neglog10p` high, `genetic.n_studies` ≥ some threshold):

```python
def emerging_locus(v):
    return (val(v, "temporal.latest_year") >= CURRENT_YEAR - 2
            and val(v, "temporal.n_recent") >= 1
            and val(v, "genetic.best_neglog10p") >= 8)  # ~genome-wide sig
```

Real hits: **rs6733839** (`latest_year=2026`, `n_recent=9`,
`best_neglog10p=138.7`, `n_studies=22`) and **rs4663105**
(`latest_year=2025`, `n_recent=10`, `neglog10p=43.5`). Because recency uses
`CURRENT_YEAR` (not wall-clock), re-running with `TE_CURRENT_YEAR=2024` shifts
the window deterministically.

---

## Regenerate

```bash
python3 translational-evidence/score/entity_metrics.py
python3 translational-evidence/validate.py            # entity_metrics.jsonl -> OK
```

The metrics also travel onto the evidence graph — the full `metrics` object is
attached to each graph node and a compact set of flat, queryable props is hoisted
for Neo4j / HTML filters (see `RUNBOOK.md` §9). No counts are hardcoded; a re-run
against the full corpus just works.
