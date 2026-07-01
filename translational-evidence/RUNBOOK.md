# Track B Runbook — Translational Evidence

How to (re)run the translational-evidence pipeline for the `dementia-gap-map`
prototype, what each step produces, and how the outputs map to the shared
schemas. The track covers **Alzheimer disease plus related dementias (ADRD)**,
and every output record carries a controlled `disease_group` tag so Alzheimer
stays recoverable as the subset `disease_group == "alzheimer"`. Everything here
is standard-library-only Python 3.9 — no `pip`, no virtualenv, no third-party
packages — so it reproduces on a clean machine.

---

## 1. One-command rerun

From the repo root:

```bash
# Full pipeline: ingest (live APIs, cached) -> normalize -> map -> score -> validate
python3 translational-evidence/run_all.py

# Skip the ingest steps and rebuild everything downstream from the
# already-cached raw API responses (no network calls):
python3 translational-evidence/run_all.py --skip-ingest
```

`run_all.py` resolves every step relative to its own location, so it works from
any working directory. It shells out to `python3 <script>` with
`subprocess.run(..., check=True)`, printing a numbered banner before each step;
the first non-zero exit aborts the whole run (no silent failures).

Step order:

1. `ingest/gwas_catalog.py`
2. `ingest/clinicaltrials.py`
3. `ingest/open_targets.py`
4. `ingest/open_targets_l2g.py`
5. `normalize/gwas_catalog.py`
6. `normalize/clinicaltrials.py`
7. `normalize/open_targets.py`
8. `normalize/open_targets_l2g.py`
9. `map/pathways.py`
10. `score/scores.py`
11. `validate.py`

With `--skip-ingest`, steps 1–4 (the ingest steps) are omitted and only steps
5–11 run.

You can also run any single stage directly, e.g.
`python3 translational-evidence/normalize/gwas_catalog.py`.

---

## 2. Environment

- **Python 3.9**, **standard library only**. Modules used: `urllib.request`,
  `json`, `csv`, `pathlib`, `argparse`, `subprocess`, `time`, `math`,
  `datetime`. No `pip install`, no `requests`, no venv required.
- **Caching**: every raw API response is cached under
  `data/raw/translational-evidence/` with a deterministic, date-stamped
  filename. Re-runs reuse the cache and make **no** network calls.
- **`TE_REFRESH=1`**: set this env var to bypass the cache and force fresh API
  calls, e.g. `TE_REFRESH=1 python3 translational-evidence/run_all.py`.
  (Handled centrally by `common.get_json` / `common.post_json`.)
- Generated data lives under `data/**`, which is **gitignored**. The scripts and
  the curated map CSVs under `translational-evidence/**` are source-controlled.

---

## 3. What each script does

This track covers **Alzheimer disease plus related dementias (ADRD)**. Each
ingest step now spans a *list* of traits / conditions / diseases rather than
Alzheimer alone; the Alzheimer-only combined files are still (re)written for
provenance, and Alzheimer stays recoverable downstream as the subset
`disease_group == "alzheimer"`.

### Ingest (live API → raw cache)
- **`ingest/gwas_catalog.py`** — pages GWAS Catalog studies for a **list of EFO
  traits** — *Alzheimer disease*, *dementia*, *vascular dementia*,
  *frontotemporal dementia*, *Lewy body dementia*, *dementia with Lewy bodies*,
  *Parkinson's disease dementia* — unions + dedups them by `accessionId`, then
  fetches each study's associations (reusing the per-accession cache, so
  Alzheimer accessions are not refetched). Writes a combined ADRD associations
  JSONL (with a `queryTrait` per line) plus the legacy Alzheimer-only combined
  files. Traits that legitimately return 0 studies / 404 are logged and skipped.
- **`ingest/clinicaltrials.py`** — runs **separate `query.cond` pulls** for each
  ADRD condition — *Alzheimer Disease*, *Vascular Dementia*, *Frontotemporal
  Dementia*, *Lewy Body Dementia*, *Dementia* — paging each by following
  `nextPageToken`, caching each page, and deduping by `nctId` across conditions.
  Writes one combined `clinicaltrials_adrd_studies` JSONL. Global safety cap of
  15,000 unique studies.
- **`ingest/open_targets.py`** — POSTs GraphQL queries to the Open Targets
  Platform for **multiple disease ids**: Alzheimer disease (`MONDO_0004975`,
  known id, no lookup), plus *dementia*, *vascular dementia*, *frontotemporal
  dementia*, and *Lewy body dementia* resolved via the OT `search` query. For
  each disease it fetches the top 300 associated targets (3 pages of 100).
  Caches each search + page response, a combined ADRD JSON (list, one entry per
  disease), and the legacy Alzheimer-only combined JSON.
- **`ingest/open_targets_l2g.py`** — the **functional / eQTL evidence layer**.
  POSTs GraphQL to Open Targets to (1) resolve the same ADRD disease ids, (2)
  page `studies(diseaseIds, enableIndirect)` and keep every `studyType=="gwas"`
  study, then (3) page `credibleSets(studyIds, studyTypes:[gwas])` over those
  GWAS studies in batches, requesting the **top-3 Locus-to-Gene (L2G)
  predictions** and up to 50 GWAS→QTL colocalisation rows per fine-mapped
  credible set. Caches the studies pages, every credibleSets page, and a run
  manifest under `data/raw/translational-evidence/open_targets_l2g/`. The
  `2026-07-01` build covered **351** GWAS studies → **1,865** credible sets
  across **24** cached credibleSets pages (9 batches).

### Normalize (raw cache → schema-conformant processed JSONL)
- **`normalize/gwas_catalog.py`** — flattens associations into
  `gwas_associations.jsonl` and aggregates one record per gene into
  `genes.jsonl` (carrying best p-value, `-log10(p)`, study/association counts,
  example variants). Tags each association with a single `disease_group` (from
  its `trait`) and each gene with `disease_groups` (dedup + sorted across the
  traits it appears under).
- **`normalize/clinicaltrials.py`** — flattens each trial's `protocolSection`,
  assigns a transparent `trial_category` and a `mechanism_group` (from the
  curated `map/intervention_mechanism.csv`), tags a `disease_group` from the
  trial's `conditions`, and writes `trials.jsonl`. Each derived field keeps its
  explaining input alongside it.
- **`normalize/open_targets.py`** — emits one `target_evidence.jsonl` record per
  target per disease, surfacing the Open Targets datatype association scores and
  tagging each with a `disease_group` from the OT `disease_label`.
- **`normalize/open_targets_l2g.py`** — reads the cached credibleSets pages (via
  the run manifest, no network) and emits `functional_links.jsonl` conforming to
  `shared/schemas/functional_link.schema.json`. It produces two record kinds:
  **L2G predictions** (`evidence_type = "l2g_prediction"`, the primary + densely
  populated signal; one record per top-3 L2G row, `score` = L2G score,
  `gene_id`/`gene_symbol` from the target, `variant_or_locus` = studyLocusId)
  and **GWAS→QTL colocalisation** (`evidence_type = "gwas_qtl_colocalisation"`,
  opportunistic and **sparse for AD**; `score` = h4, `cell_type` = QTL biosample
  name, `gene_id` = the QTL `qtlGeneId`). Each record carries `disease_group`,
  `source`, `source_study`, `method`, and `rsid`; identical `link_id`s are
  de-duplicated. The `2026-07-01` build emitted **3,372** L2G links and **0**
  colocalisation links (AD GWAS→QTL colocalisation is near-empty), over **1,710**
  distinct genes.

### Map (curated source → processed JSONL)
- **`map/pathways.py`** — reads the source-controlled
  `map/gene_pathway.csv`, groups genes by `pathway_group`, and writes one
  `pathways.jsonl` record per group. No network calls.

### Score (processed JSONL → enriched in place)
- **`score/scores.py`** — computes the four explainable scores and enriches
  `genes.jsonl` and `pathways.jsonl` **in place**, storing every raw component
  input and weight alongside each derived number. `functional_support` is now a
  **real functional layer** aggregated per gene from `functional_links.jsonl`
  (max OT L2G score across loci, plus a brain-cell-type colocalisation bonus);
  it is joined by Ensembl `gene_id` first, then `gene_symbol`, and is `null` when
  a gene has no functional_links. Also (re)writes `score/SCORING.md`. Reads no
  live APIs.
- **`score/entity_metrics.py`** — builds the **per-entity METRICS layer**: one
  flat, machine-readable metrics record per **gene / variant / pathway**, written
  to `data/processed/translational-evidence/entity_metrics.jsonl`
  (schema `entity_metric.schema.json`). Reads the already-processed Track B files
  (`genes`, `gwas_associations`, `functional_links`, `trials`, `pathways`) plus
  `map/gene_pathway.csv`; reads no live APIs. Every metric is a **number /
  boolean / null** under a dotted `"<group>.<name>"` key wrapped as
  `{value, source}`, where `source` names the exact input field(s) + formula, so
  the layer is fully **explainable** and nothing is fabricated. **By design it
  ships transparent signals only — no baked-in verdicts** ("contradicted",
  "opportunity", "novel"): downstream agents compose those from the metrics, and
  because `additionalProperties` is allowed everywhere they can attach their own
  metric keys. See **`score/METRICS.md`** for the full field reference (every
  metric, formula, source, per entity_type) and worked verdict-composition
  examples. **Recency** metrics use `CURRENT_YEAR` (default `2026`, overridable
  via `TE_CURRENT_YEAR`) as "now" — never `datetime.today()` — with a 3-year
  recent window, so a given input always yields the same output:

  ```bash
  python3 translational-evidence/score/entity_metrics.py
  # override "now" for recency (deterministic), e.g.:
  TE_CURRENT_YEAR=2024 python3 translational-evidence/score/entity_metrics.py
  ```

  `2026-07-01` build: **6,318** records — **523 gene**, **5,786 variant**,
  **9 pathway**.

  > Not yet wired into `run_all.py` (its `score/` step runs only `scores.py`);
  > run it after `scores.py` — the graph exporters in §9 pick it up automatically.

### Validate
- **`validate.py`** — a stdlib JSONL schema-sanity checker (no `jsonschema`
  dependency). Checks required/non-null keys, declared types, and enums against
  the schemas in `shared/schemas/`, reporting per-file record counts and
  `OK`/errors (with line numbers). Missing outputs are reported as `SKIP`. It
  validates the per-entity **`entity_metrics.jsonl`** (schema
  `entity_metric.schema.json`), the two **shared** bridge outputs
  (`data/processed/shared/topic_evidence_links.jsonl` and
  `.../topic_evidence_rollup.jsonl`), and the graph exports
  (`data/exports/graph/{nodes,edges}.jsonl`) — each when it exists.

### Export (integration bridge: Track A ↔ Track B)
- **`exports/build_topic_bridge.py`** — builds the Track A ↔ Track B integration
  bridge. It reads Track A's published snapshot and joins it against Track B's
  curated evidence, emitting two shared handoff files plus a manifest. Run it
  after the pipeline (it consumes the processed Track B files) and after the
  Track A snapshot is materialized (see below):

  ```bash
  python3 translational-evidence/exports/build_topic_bridge.py
  ```

  **Inputs**

  - Track A snapshot (read-only), under
    `data/interim/translational-evidence/track_a_snapshot/`:
    - `topic_clusters.jsonl` — `topic_id`, `label`, `paper_ids[]`, `top_terms[]`, …
    - `papers.jsonl` — `paper_id`, `pmid`, `title`, `abstract`, …

    These are **materialized from Track A's published processed files on
    `origin/main`** (do not edit `topic-dynamics/**`), e.g.:

    ```bash
    git show origin/main:data/processed/topic-dynamics/topic_clusters.jsonl \
        > data/interim/translational-evidence/track_a_snapshot/topic_clusters.jsonl
    git show origin/main:data/processed/topic-dynamics/papers.jsonl \
        > data/interim/translational-evidence/track_a_snapshot/papers.jsonl
    git show origin/main:data/processed/topic-dynamics/topic_trajectories.jsonl \
        > data/interim/translational-evidence/track_a_snapshot/topic_trajectories.jsonl
    ```

  - Track B processed evidence (this track's own outputs):
    `genes.jsonl`, `gwas_associations.jsonl`, `pathways.jsonl`, `trials.jsonl`.
  - The authoritative `translational-evidence/map/gene_pathway.csv`
    (`gene_symbol` → `pathway_group`).

  **Join method** — three link types, because bare PMID overlap alone is thin
  (only ~10 of ~410 snapshot papers share a PMID with the GWAS corpus):
  - `gene_mention` — case-sensitive whole-word gene-symbol match in member-paper
    title+abstract text (symbols < 3 chars and an audited ambiguous-symbol
    blocklist are excluded);
  - `paper_overlap` — shared PMIDs between the topic's papers and Track B's GWAS
    publications (also pulls in each association's `reported_genes`);
  - `pathway_mapping` — linked-gene → `pathway_group` via `gene_pathway.csv`,
    plus topic `top_terms`/`label` keyword matches to the mechanism vocabulary.

  **Outputs** (both under `data/processed/shared/`, both gitignored):
  - `topic_evidence_links.jsonl` — one explainable record per (topic, evidence,
    link_type) join (`topic_evidence_link.schema.json`); every record carries a
    `notes` string.
  - `topic_evidence_rollup.jsonl` — one record per topic, the Track B half of the
    frontend `map_data.json` cluster (`topic_evidence_rollup.schema.json`).
  - `topic_bridge_manifest.json` — snapshot-awareness metadata (input counts +
    the SUBSET / full-run note). Not schema-validated.

  No counts are hardcoded in the script — they are read from the inputs, so a
  re-run against the full corpus just works.

#### Refresh when Track A full run lands

The current bridge is built against a Track A **SUBSET** snapshot
(the `2026-07-01` build: **410** papers / **9** clusters — see
`topic_bridge_manifest.json`). Track A's **FULL** run is still **pending**. When
it lands on `origin/main`, refresh the bridge:

1. **Re-materialize** the three `track_a_snapshot` files from `origin/main` (the
   three `git show origin/main:data/processed/topic-dynamics/<file> > …` commands
   above). Fetch first: `git fetch origin`.
2. **Re-run** the bridge:
   `python3 translational-evidence/exports/build_topic_bridge.py`.
3. **Re-validate**: `python3 translational-evidence/validate.py` (both shared
   outputs must be `OK`).

Bridge coverage — especially `gene_mention` (more abstracts to match against) and
`paper_overlap` (more topic PMIDs overlapping the GWAS corpus) — will grow
**substantially** with the full corpus; several topics that currently have zero
links (e.g. topics with no gene mentions and no GWAS-PMID overlap) will gain
evidence. No schema or code change is needed for the refresh.

---

## 4. Output files, schemas, and record counts

All processed outputs live under `data/processed/translational-evidence/` and
each conforms to a schema in `shared/schemas/`. Counts below are from the
`2026-07-01` run:

| Output file | Schema (`shared/schemas/`) | Records |
| --- | --- | --- |
| `gwas_associations.jsonl` | `gwas_association.schema.json` | 7,351 |
| `genes.jsonl` | `gene.schema.json` | 523 |
| `pathways.jsonl` | `pathway.schema.json` | 9 |
| `trials.jsonl` | `trial.schema.json` | 6,841 |
| `target_evidence.jsonl` | `target_evidence.schema.json` | 1,499 |
| `functional_links.jsonl` | `functional_link.schema.json` | 3,372 |
| `entity_metrics.jsonl` | `entity_metric.schema.json` | 6,318 |

(Counts grew from the earlier Alzheimer-only build because the pipeline now
covers ADRD; `target_evidence` is ~300 targets × 5 disease ids.
`functional_links.jsonl` is the OT L2G functional layer: 3,372 L2G links + 0
colocalisation links over 1,710 distinct genes.)

`entity_metrics.jsonl` is the **per-entity METRICS layer** (one flat record per
gene / variant / pathway = **523 + 5,786 + 9**). Each metric is a
number/boolean/null under a dotted `"<group>.<name>"` key wrapped as
`{value, source}` — transparent signals only, no baked-in verdicts, extensible
via `additionalProperties`. See **`score/METRICS.md`** for every metric's
definition, formula, source, and worked verdict-composition examples. Built by
`score/entity_metrics.py`; recency uses `CURRENT_YEAR` (env-overridable via
`TE_CURRENT_YEAR`, default `2026`).

`genes.jsonl` and `pathways.jsonl` are produced by the normalize/map steps and
then **rewritten in place** by `score/scores.py` with the scores attached
(`genes.jsonl` gains the L2G-derived `functional_support`; 147/523 genes got a
non-null value in the `2026-07-01` build).

### `functional_links.jsonl` schema (`functional_link.schema.json`)

One record per gene↔locus functional link. Required: `link_id`, `gene_id`,
`evidence_type`, `source`. Key fields:

| field | meaning |
| --- | --- |
| `link_id` | stable id, `"{studyLocusId}:l2g:{ensembl}"` or `"{studyLocusId}:coloc:{qtlGeneId}:{method}:{qtlType}"` |
| `gene_id` | Ensembl gene id |
| `gene_symbol` | approved symbol (L2G) or `null` (coloc) |
| `variant_or_locus` | Open Targets `studyLocusId` |
| `rsid` | lead variant rsID (or `null`) |
| `cell_type` | QTL biosample name for colocalisation links (`null` for L2G) |
| `evidence_type` | `l2g_prediction`, `gwas_qtl_colocalisation`, `eqtl_catalogue`, or `other` |
| `score` | L2G score or colocalisation `h4` |
| `disease_group` | controlled disease tag from the GWAS study trait |
| `source` | `open_targets_l2g` or `open_targets_coloc` |
| `source_study` | Open Targets GWAS study id |
| `method` | `"OT L2G"` or the colocalisation method |

### Disease-group distribution (`2026-07-01` build)

Every record is tagged with a `disease_group` (single value) — genes use
`disease_groups` (array). **Alzheimer is a subset**, filterable via
`disease_group == "alzheimer"` (or, for genes, `"alzheimer" in disease_groups`).

| group | gwas_associations | trials | target_evidence | genes (`disease_groups`) |
| --- | --- | --- | --- | --- |
| `alzheimer` | 6,531 | 3,304 | 300 | 484 |
| `dementia_unspecified` | 526 | 1,486 | 300 | — |
| `vascular_dementia` | 114 | 46 | 299 | 1 |
| `lewy_body_dementia` | 106 | 131 | 300 | 18 |
| `frontotemporal_dementia` | 39 | 198 | 300 | 28 |
| `other` | 27 | 1,454 | — | 2 |
| `mixed_dementia` | 8 | 222 | — | 2 |
| **total records** | **7,351** | **6,841** | **1,499** | **523** |

(Gene columns sum to more than 523 because a gene's `disease_groups` array can
span several groups; the association/trial/target columns are one value per
record and sum to the record total.)

### Shared bridge outputs (`data/processed/shared/`)

`exports/build_topic_bridge.py` writes two schema-validated shared files (both
gitignored). Counts below are from the `2026-07-01` build against the Track A
**SUBSET** snapshot (410 papers / 9 clusters):

| Output file | Schema (`shared/schemas/`) | Records |
| --- | --- | --- |
| `topic_evidence_links.jsonl` | `topic_evidence_link.schema.json` | 1,099 |
| `topic_evidence_rollup.jsonl` | `topic_evidence_rollup.schema.json` | 9 |

The 1,099 links break down as **1,072** `paper_overlap` (1,007 GWAS
associations + reported genes; heavily concentrated in `topic:002`,
`alzheimer / genome-wide / meta-analysis`), **13** `gene_mention`, and **14**
`pathway_mapping`. Six of the nine topics carry links; three (`topic:001`,
`topic:003`, `topic:006`) currently have none (no gene mentions and no
GWAS-PMID overlap) and are expected to gain evidence under the full corpus.

Validator result for the `2026-07-01` build (all files, including the two shared
outputs):

```
data/processed/translational-evidence/gwas_associations.jsonl: 7351 records, OK
data/processed/translational-evidence/genes.jsonl: 523 records, OK
data/processed/translational-evidence/pathways.jsonl: 9 records, OK
data/processed/translational-evidence/trials.jsonl: 6841 records, OK
data/processed/translational-evidence/target_evidence.jsonl: 1499 records, OK
data/processed/translational-evidence/functional_links.jsonl: 3372 records, OK
data/processed/shared/topic_evidence_links.jsonl: 1099 records, OK
data/processed/shared/topic_evidence_rollup.jsonl: 9 records, OK
```

---

## 5. Raw cache location and naming

All raw API responses are cached (pretty-printed JSON) under:

```
data/raw/translational-evidence/
```

Naming scheme (date stamp = the run date, `YYYY-MM-DD`):

| Source | Cache file(s) |
| --- | --- |
| GWAS Catalog studies pages (per trait) | `gwas_catalog_studies_{traitSlug}_{stamp}_page_{PPP}.json` |
| GWAS Catalog per-study associations | `gwas_catalog_associations/{ACCESSION}.json` |
| GWAS Catalog combined studies (ADRD / legacy) | `gwas_catalog_adrd_studies_{stamp}.json` / `gwas_catalog_alzheimer_studies_{stamp}.json` |
| GWAS Catalog combined associations (ADRD / legacy) | `gwas_catalog_adrd_associations_{stamp}.jsonl` / `gwas_catalog_alzheimer_associations_{stamp}.jsonl` |
| ClinicalTrials.gov pages (per condition) | `clinicaltrials_{condSlug}_page_{PPP}.json` |
| ClinicalTrials.gov combined studies | `clinicaltrials_adrd_studies_{stamp}.jsonl` |
| Open Targets disease search | `open_targets_search_{slug}_{stamp}.json` |
| Open Targets pages (per disease id) | `open_targets_{diseaseId}_{stamp}_page_{N}.json` |
| Open Targets combined (ADRD / legacy) | `open_targets_adrd_targets_{stamp}.json` / `open_targets_alzheimer_targets_{stamp}.json` |
| Open Targets L2G studies pages | `open_targets_l2g/studies_{stamp}_page_{i}.json` |
| Open Targets L2G credibleSets pages | `open_targets_l2g/crediblesets_{stamp}_batch_{b}_page_{p}.json` |
| Open Targets L2G run manifest | `open_targets_l2g/manifest_{stamp}.json` |

The `2026-07-01` ADRD build has **207** top-level cached files plus **321**
per-accession GWAS association files under `gwas_catalog_associations/` (528
total). This includes the older Alzheimer-only `clinicaltrials_page_{PPP}.json`
and `gwas_catalog_studies_{stamp}_page_{PPP}.json` files from the first build
(retained, still valid cache); the broadened run adds per-condition
(`clinicaltrials_{condSlug}_page_*`) and per-trait
(`gwas_catalog_studies_{traitSlug}_*`) pages. The normalize steps pick up the
**newest** date-stamped combined file automatically.

Actual EFO traits / trial conditions / OT disease ids queried by this build:

- **GWAS EFO traits:** Alzheimer disease, dementia, vascular dementia,
  frontotemporal dementia, Lewy body dementia, dementia with Lewy bodies,
  Parkinson's disease dementia. (The last two returned 0 studies and are logged
  and skipped.)
- **ClinicalTrials.gov conditions:** Alzheimer Disease, Vascular Dementia,
  Frontotemporal Dementia, Lewy Body Dementia, Dementia.
- **Open Targets disease ids:** `MONDO_0004975` (Alzheimer disease),
  `MONDO_0001627` (dementia), `MONDO_0004648` (vascular dementia),
  `MONDO_0017276` (frontotemporal dementia), `MONDO_0007488` (Lewy body
  dementia) — non-Alzheimer ids resolved via the OT `search` query.

Re-runs reuse this cache; `TE_REFRESH=1` forces fresh fetches and overwrites it.

---

## 6. Scores

The full methodology — every formula, weight, normalization constant, phase
table, and the pathway→trial mechanism crosswalk — is in
[`score/SCORING.md`](score/SCORING.md). Design principle: **every score is
fully explainable**; each derived number is stored in the record alongside its
raw component inputs and the exact weights used, so nothing is a black box.

Four scores:

- **`genetic_support`** (per gene, 0–1) — `0.5*neglog10p_norm +
  0.2*study_count_norm + 0.3*ot_genetic`. Combines GWAS best `-log10(p)` and
  study count with the Open Targets genetic-association datatype score.
- **`functional_support`** (per gene, 0–1 or null) — **real functional layer**:
  `clamp01(base_l2g + coloc_bonus)`, where `base_l2g` is the max Open Targets
  Locus-to-Gene (L2G) score for the gene across all fine-mapped loci in
  `functional_links.jsonl`, and `coloc_bonus` (≤ +0.15) rewards a GWAS→QTL
  colocalisation in a brain-relevant biosample (microglia highest). L2G already
  integrates colocalisation/QTL evidence across studies. `null` when a gene has
  no functional_links. Old OT `rna_expression`/`affected_pathway` are kept only
  as **secondary recorded components**. See `score/SCORING.md`.
- **`clinical_translation`** (per pathway, 0–1) — `0.6*max_phase_score +
  0.25*min(1, trial_count/20) + 0.15*has_results_fraction`, over trials whose
  (crosswalked) mechanism matches the pathway. `0.0` if no mapped trials.
- **`translation_gap`** (per pathway, 0–1) — `combined_support * (1 -
  clinical_translation)`, where `combined_support` is the mean over member genes
  of `0.6*genetic_support + 0.4*(functional_support or genetic_support)`.
  **Higher = strong genetics/function but little clinical activity** — i.e. a
  translational opportunity/gap.

Every score carries its component object (e.g.
`genetic_support_components`, `functional_support_components`, and the pathway
`scores` object) plus a `_formulas` string, so each number is reproducible from
the record alone.

---

## 7. Functional layer status & known limitation

**`functional_support` is now a real functional / eQTL layer** built from the
Open Targets Locus-to-Gene (L2G) model (aggregated per gene from
`functional_links.jsonl`), replacing the old
`rna_expression`/`affected_pathway` proxy. L2G integrates colocalisation and QTL
evidence across many studies into a single score, so it is the primary signal.

**Known limitation — raw GWAS→QTL colocalisation is sparse for AD.** The
current build produced **0** `gwas_qtl_colocalisation` links across ~1,865
credible sets, so the brain-cell-type `coloc_bonus` is `0.0` everywhere today
and `functional_support` is driven entirely by `base_l2g`. The cell-type bonus
machinery (microglia/astrocyte/neuron/oligodendrocyte/OPC/cortex/brain) is in
place for when brain-QTL colocalisation is available. A dedicated brain-cell-type
**eQTL Catalogue** enrichment (`evidence_type = "eqtl_catalogue"` in the
functional_link schema) is noted as **optional future work** and is not yet
integrated. Genes with no functional_links carry
`functional_support = null` with
`functional_support_components.note = "no OT L2G/QTL link"`.

---

## 8. `map_data.json` merge recipe (frontend)

The frontend (`web/`, types in `web/src/types.ts`) loads a single
`data/app/map_data.json`. Each `Cluster` in that file needs a merged shape:

```jsonc
{
  // Track A (topic-dynamics)
  "topic_id":  "...",
  "clusters":  ..., "coords": ..., "color": ...,   // layout / rendering
  "scores": {
    "emergence": ...,          // Track A
    // Track B (topic_evidence_rollup.jsonl)
    "genetic_support": ...,
    "functional_support": ...,
    "clinical_translation": ...,
    "clinical_saturation": ...
  },
  // Track B (topic_evidence_rollup.jsonl)
  "pathway_group": "...",
  "top_genes": [ ... ],
  "trials":    [ ... ]
}
```

**Where each field comes from** (joined on `topic_id`):

| `map_data.json` field | Source track | Source field |
| --- | --- | --- |
| `topic_id` | Track A | `topic_clusters.topic_id` (same key on both sides) |
| `clusters` / `coords` / `color` | Track A | topic layout / rendering fields |
| `scores.emergence` | Track A | `topic_clusters.scores.emergence` |
| `scores.genetic_support` | Track B | `rollup.scores.genetic_support` |
| `scores.functional_support` | Track B | `rollup.scores.functional_support` |
| `scores.clinical_translation` | Track B | `rollup.scores.clinical_translation` |
| `scores.clinical_saturation` | Track B | `rollup.scores.clinical_saturation` |
| `pathway_group` | Track B | `rollup.pathway_group` |
| `top_genes` | Track B | `rollup.top_genes` |
| `trials` | Track B | `rollup.trials` |

So: **emergence + layout/coords/color come from Track A**;
**genetic_support / functional_support / clinical_translation /
clinical_saturation + pathway_group / top_genes / trials come from Track B's
`data/processed/shared/topic_evidence_rollup.jsonl`**, joined on `topic_id`.

**Assembling the final `data/app/map_data.json` is the remaining joint step
(owner TBD).** It is a straight left-join of Track A's topic clusters against
the Track B rollup on `topic_id`. Topics with no rollup match (or a rollup whose
Track B fields are null — e.g. the currently link-less `topic:001` / `topic:003`
/ `topic:006`) should render with their Track A fields and null/empty Track B
fields; those gaps shrink once Track A's full run lands and the bridge is
refreshed.

---

## 9. Evidence graph explorer (standalone Track B)

A **standalone** Track B evidence-graph explorer that lets you check/explore
*everything* with filters. It is **separate from Track A's `web/` app** (it does
not touch `web/**` or `topic-dynamics/**`); it is built entirely from Track B's
own processed evidence plus the shared topic bridge. The graph carries the
**full** un-capped set of nodes and edges — no aggressive caps — so **filtering
does the legibility work**. Every node carries `provenance` + scores and every
edge carries a `score` + `evidence` label, so anything you filter to stays
explainable. Nothing is fabricated.

> The topic overlay (`topic` nodes + `topic_gene` / `topic_pathway` edges) is
> joined from the shared bridge, which currently reflects the Track A **SUBSET**
> snapshot (the `2026-07-01` build: 410 papers / 9 clusters). It refreshes for
> free when Track A's full run lands and the bridge is rebuilt (see §3 →
> "Refresh when Track A full run lands").

### Node & edge types + full counts (`2026-07-01` build)

Counts are the real numbers from
`data/exports/graph/graph_manifest.json`.

**Nodes — 15,286 total:**

| node type | count | source |
| --- | --- | --- |
| `trial` | 6,841 | `trials.jsonl` |
| `variant` | 5,786 | `gwas_associations.jsonl` (rsID / locus) |
| `drug` | 2,111 | trial `interventions[]` |
| `gene` | 523 | `genes.jsonl` |
| `pathway` | 9 | `pathways.jsonl` |
| `topic` | 9 | `topic_evidence_rollup.jsonl` (Track A subset overlay) |
| `disease` | 7 | `disease_group` vocabulary |
| **total** | **15,286** | |

**Edges — 10,732 total:**

| edge type | count | meaning |
| --- | --- | --- |
| `trial_pathway` | 5,271 | trial → mechanism/pathway group |
| `trial_drug` | 3,350 | trial → intervention/drug |
| `variant_gene` | 1,274 | GWAS variant → reported gene |
| `gene_disease` | 535 | gene → dementia disease group |
| `drug_pathway` | 174 | drug → pathway (via mechanism) |
| `topic_gene` | 78 | topic → gene (bridge) |
| `gene_pathway` | 36 | gene → pathway group |
| `topic_pathway` | 14 | topic → pathway (bridge) |
| **total** | **10,732** | |

(The builder dropped **1,949** dangling edges whose endpoints were not present as
nodes — reported as `edges.dangling_dropped` in the manifest — so every retained
edge resolves to two real nodes.)

Graph inputs (from the manifest): `genes` 523, `gwas_associations` 7,351,
`pathways` 9, `trials` 6,841, `target_evidence` 1,499, `functional_links` 3,372,
`topic_links` 1,099, `topic_rollup` 9.

### How to regenerate

Run after the pipeline (and after the topic bridge, so the topic overlay is
current):

```bash
# 1. Build the full graph (nodes.jsonl + edges.jsonl + graph_manifest.json)
python3 translational-evidence/exports/build_evidence_graph.py

# 2a. Build the zero-install browser viz (graph_data.js + evidence_graph.html)
python3 translational-evidence/viz/build_graph_viz.py

# 2b. Build the Neo4j-ready CSVs + loader (neo4j/{nodes,edges}.csv, load.cypher, README)
python3 translational-evidence/exports/build_neo4j_export.py
```

The builder **scripts** live under `translational-evidence/exports/` and
`translational-evidence/viz/` (source-controlled); all **generated** artifacts
land under `data/exports/graph/` (gitignored). No counts are hardcoded — the
scripts read the inputs, so a re-run against the full corpus just works.

### Per-entity metrics on graph nodes (Neo4j / HTML)

`build_evidence_graph.py` **joins `entity_metrics.jsonl` onto the graph nodes**
(so build it first — see the Score step in §3). For every matching gene / variant
/ pathway node it:

- attaches the **full metrics object** as `node['metrics']` (the complete
  `{"<group>.<name>": {value, source}}` map, for completeness); and
- **hoists a compact set of flat, queryable props** onto the node top-level so
  Cypher and the HTML filters can use them directly. Every hoisted value is
  copied verbatim from the metrics record (nothing fabricated).

Join keys: `gene` → `gene:<gene_id>`, `variant` → `variant:<rsid>` (the id
already carries the prefix), `pathway` → `pathway:<mechanism_group>`. The
`2026-07-01` manifest reports **6,318** metrics records loaded and attached with
**0 unmatched** (`gene` 523, `variant` 5,786, `pathway` 9) — see
`graph_manifest.json → metrics.{attached_by_type, flat_keys, unmatched_records}`.

Flat props hoisted per node type (`FLAT_METRIC_KEYS`):

| node type | flat props (→ dotted metric) |
| --- | --- |
| `gene` | `stopped_ratio`, `direction_agreement`, `n_conflicting`, `n_trials`, `first_gwas_year`, `latest_gwas_year`, `n_recent_gwas`, `has_approval`, `translation_gap` |
| `pathway` | `stopped_ratio`, `has_approval`, `n_trials`, `n_drugs`, `first_trial_year`, `latest_trial_year`, `n_recent_trials`, `translation_gap` |
| `variant` | `n_associations`, `n_studies`, `first_year`, `latest_year`, `n_recent`, `direction_agreement` |

`build_neo4j_export.py` writes these as typed CSV columns and `load.cypher`
loads them with `toInteger()` / `toFloat()` / `toBoolean()` (a blank column ==
Cypher `null`). Note: the pathway/gene `translation_gap` metric is loaded as
`metrics_translation_gap` in Neo4j to avoid clashing with the existing
`translation_gap` score column. `load.cypher` §5 ships **commented-out example
queries** (copy one to run) — these are read-only illustrations; **verdicts are
composed from the metrics, never baked in**:

```cypher
// Genetically supported but clinically stalled genes
// (strong genetics + high stopped share; no verdict baked in — just the metrics)
MATCH (g:Gene)
WHERE g.genetic_support >= 0.7 AND g.stopped_ratio >= 0.3 AND g.n_trials >= 5
RETURN g.label, g.genetic_support, g.stopped_ratio, g.n_trials, g.metrics_translation_gap
ORDER BY g.stopped_ratio DESC, g.genetic_support DESC LIMIT 25;

// Direction-conflict genes: GWAS effect directions disagree across studies
MATCH (g:Gene)
WHERE g.direction_agreement IS NOT NULL AND g.direction_agreement < 0.7 AND g.n_conflicting >= 2
RETURN g.label, g.direction_agreement, g.n_conflicting, g.genetic_support
ORDER BY g.n_conflicting DESC, g.direction_agreement ASC LIMIT 25;

// Recently-emerging loci: latest GWAS in the last ~3 years (CURRENT_YEAR-2)
MATCH (v:Variant)
WHERE v.latest_year >= 2024 AND v.n_recent >= 1
RETURN v.label, v.latest_year, v.n_recent, v.n_associations, v.n_studies
ORDER BY v.latest_year DESC, v.n_associations DESC LIMIT 25;

// Under-translated pathways: high translation_gap yet never reached approval
MATCH (p:Pathway)
WHERE p.has_approval = false
RETURN p.label, p.translation_gap, p.stopped_ratio, p.n_trials, p.n_drugs, p.n_recent_trials
ORDER BY p.translation_gap DESC LIMIT 25;
```

The recency threshold in these examples (`latest_year >= 2024`, i.e.
`CURRENT_YEAR - 2`) is rendered from `CURRENT_YEAR` in `build_neo4j_export.py`
(env-overridable via `TE_CURRENT_YEAR`), matching how `entity_metrics.jsonl` was
computed. Full field reference + more verdict-composition examples:
`score/METRICS.md`.

### Two ways to explore EVERYTHING with filters

**(A) Zero-install browser (no Docker, no DB).** Open the self-contained HTML
page — it loads `graph_data.js` via a plain `<script>` tag so it works under
`file://`:

```bash
open data/exports/graph/evidence_graph.html
```

- **Internet caveat:** the page pulls sigma.js + graphology from a **CDN**
  (jsDelivr), so it needs internet for those two libraries to render; the graph
  *data* is fully local in `graph_data.js`. If offline, use option (B).
- **Trials are toggled OFF by default** (`default_on_types` =
  `variant, gene, pathway, drug, disease, topic`) so the 6,841 trial nodes do
  not swamp the first paint — tick the **Trial** type to bring them in. Filter
  by node type, score threshold, trial phase, and search to focus on any slice
  of the full graph.

**(B) Neo4j — full Cypher filtering (the user asked about this).** Load the
entire un-capped graph into Neo4j and filter with Cypher. This is the most
powerful way to slice everything, but it **needs Docker (or a running Neo4j DB)**
— it is not zero-install. Full instructions, ready-to-run `load.cypher`, and
eight worked filter queries are in:

```
data/exports/graph/neo4j/README.md
```

That README covers: starting `neo4j:5` with the export dir mounted, loading via
`cypher-shell` or the Neo4j Browser, and Cypher recipes (under-translated
pathways, microglia genes with strong genetic support, the full
variant→gene→pathway→drug chain, pleiotropic genes across disease groups, trials
by phase for a mechanism, highest-confidence Alzheimer variant→gene links, …).
Expect **15,286** nodes and **10,732** relationships loaded. `disease_groups` is
loaded as a Neo4j list (`'alzheimer' IN n.disease_groups`); `provenance` is a
JSON string (filter with `CONTAINS`, or parse it in your app). APOC is **not**
required.

### Graph export files (all under `data/exports/graph/`, gitignored)

| file | what it is |
| --- | --- |
| `nodes.jsonl` | 15,286 evidence nodes (`evidence_node.schema.json`) |
| `edges.jsonl` | 10,732 evidence edges (`evidence_edge.schema.json`) |
| `graph_manifest.json` | node/edge counts by type, inputs, layout, dangling-drop count |
| `graph_data.js` | the same graph as `window.GRAPH` for the browser viz |
| `evidence_graph.html` | zero-install sigma.js explorer (option A) |
| `neo4j/nodes.csv`, `neo4j/edges.csv` | Neo4j `LOAD CSV` inputs |
| `neo4j/load.cypher` | constraint + typed-label loader (no APOC) |
| `neo4j/README.md`, `neo4j/neo4j_manifest.json` | load instructions + export manifest |

`nodes.jsonl` and `edges.jsonl` are schema-validated by
`translational-evidence/validate.py` (see §4) whenever they are present.

### Sample node & edge (`2026-07-01` build)

A `gene` node (`data/exports/graph/nodes.jsonl`):

```json
{
  "disease_groups": ["alzheimer", "lewy_body_dementia", "mixed_dementia"],
  "group": "lipid_metabolism",
  "label": "APOE",
  "node_id": "gene:ENSG00000130203",
  "node_type": "gene",
  "provenance": {
    "gene_id": "ENSG00000130203",
    "gwas_association_count": null,
    "pathway_group": "lipid_metabolism",
    "source": "genes",
    "symbol": "APOE"
  },
  "score": 0.9668,
  "scores": {"functional_support": 0.9516, "genetic_support": 0.9668},
  "x": 400.0,
  "y": 0.0
}
```

A `variant_gene` edge (`data/exports/graph/edges.jsonl`):

```json
{
  "edge_id": "e:vg:gwas:variant:APOE:gene:ENSG00000130203",
  "edge_type": "variant_gene",
  "evidence": "gwas",
  "provenance": {
    "association_id": "GCST001372:APOE:APOE#1",
    "neglog10p": 15.154901959985743,
    "p_value": 6.999999999999999e-16,
    "pmid": "22245343",
    "reported_symbol": "APOE",
    "source": "gwas_associations.reported_genes",
    "study_accession": "GCST001372"
  },
  "score": 0.5051633986661914,
  "source_id": "variant:APOE",
  "target_id": "gene:ENSG00000130203"
}
```

---

## 10. Data sources

| Source | Endpoint | Used for |
| --- | --- | --- |
| **GWAS Catalog** (EMBL-EBI) | `https://www.ebi.ac.uk/gwas/rest/api/studies/search/findByEfoTrait` and `.../studies/{accession}/associations` | Genetic associations + gene aggregation across the **ADRD EFO trait list** (Alzheimer disease, dementia, vascular / frontotemporal / Lewy body dementia, …) |
| **ClinicalTrials.gov** | `https://clinicaltrials.gov/api/v2/studies` (v2 REST) | Trials for the **ADRD conditions** (Alzheimer Disease, Vascular / Frontotemporal / Lewy Body Dementia, Dementia) → clinical-translation scoring |
| **Open Targets Platform** | `https://api.platform.opentargets.org/api/v4/graphql` | Target–disease association scores for the **ADRD disease ids** (`MONDO_0004975` Alzheimer, `MONDO_0001627` dementia, `MONDO_0004648` vascular, `MONDO_0017276` frontotemporal, `MONDO_0007488` Lewy body) |
| **Open Targets Platform (L2G / credible sets)** | `https://api.platform.opentargets.org/api/v4/graphql` (`studies` + `credibleSets`) | Fine-mapped **Locus-to-Gene (L2G)** predictions + GWAS→QTL colocalisation for the ADRD GWAS studies → the functional / eQTL layer (`functional_links.jsonl`, `functional_support`) |

All three are public research APIs; requests carry a polite `User-Agent`
(`common.USER_AGENT`) and back off/retry on transient failures. If a live call
fails after retries, the pipeline reports the exact error and stops — it never
fabricates records.
