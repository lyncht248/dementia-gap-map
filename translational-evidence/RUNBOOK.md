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
9. `map/gene_pathway_build.py` — API capture → `gene_pathways_api.jsonl` + `gene_pathway.csv`
10. `map/intervention_mechanism_build.py` — API capture → `drug_mechanism_api.jsonl` + `intervention_mechanism.csv`
11. `map/pathways.py`
12. `score/scores.py`
13. `validate.py`

With `--skip-ingest`, steps 1–4 (the ingest steps) are omitted and only steps
5–13 run. Steps 9–10 hit the APIs (mygene / Reactome / Open Targets) through the
cached `data/raw` layer, so `--skip-ingest` still reuses their cache and
`TE_REFRESH=1` forces fresh calls. **Ordering note:** step 10 regenerates
`intervention_mechanism.csv`, which `normalize/clinicaltrials.py` (step 6)
consumes for trial mechanism tagging — on the first run against a *new* corpus,
run the pipeline twice (or run the two `map/*_build.py` scripts before the
normalize steps) so the trial tags pick up a freshly regenerated CSV.

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

### Map (API-derived capture → thin projection → processed JSONL)

The gene→pathway and drug→mechanism maps are now **API-derived and
multi-valued**. Two build scripts capture the FULL annotation set from every
source into rich JSONL sidecars, then emit a THIN single-value CSV projection for
the legacy consumers. See **[`map/README.md`](map/README.md)** for the full
design; the short version:

- **`map/gene_pathway_build.py`** — for every gene in `genes.jsonl`, captures
  **all** GO terms (mygene.info), **all** Reactome pathways, and **all** Open
  Targets target pathways, and records them verbatim. It then tags AD-mechanism
  **buckets as a LIST of signals** (`{bucket, source, matched_term}`) and derives
  ONE `primary_bucket` (the most-source-supported bucket) purely for the thin
  CSV. Writes:
  - `data/processed/translational-evidence/gene_pathways_api.jsonl` — one rich
    record per gene (full `sources.*` + `ad_bucket_signals[]` + `buckets[]` +
    `primary_bucket` + `primary_support[]`). **Records everything; nothing is
    collapsed away.**
  - `map/gene_pathway.csv` — **GENERATED thin projection**
    (`gene_symbol, pathway_group=primary_bucket, notes`) with a `# GENERATED …
    do not hand-edit` header. Genes with no keyword hit (`unknown`) are omitted
    from the CSV but keep their full raw annotations in the JSONL.
  - `data/processed/translational-evidence/pathways.jsonl` — regenerated from the
    projection (same shape as `map/pathways.py`).
- **`map/intervention_mechanism_build.py`** — ranks distinct DRUG/BIOLOGICAL
  intervention names from `trials.jsonl` by trial frequency and resolves the top
  N against Open Targets, capturing **every** mechanism-of-action row + its full
  target list. Tags **mechanisms as a LIST of signals** and derives ONE
  `primary_mechanism` for the thin CSV. Writes:
  - `data/processed/translational-evidence/drug_mechanism_api.jsonl` — one rich
    record per resolved ChEMBL drug (`sources.opentargets[]` = every MoA+targets,
    `mechanism_signals[]`, `mechanisms[]`, `primary_mechanism`, `primary_support[]`,
    all trial spellings + summed `trial_count`).
  - `map/intervention_mechanism.csv` — **GENERATED thin projection**
    (`keyword, mechanism_group=primary_mechanism, notes`) consumed by
    `normalize/clinicaltrials.py`.
- **`map/pathways.py`** — reads `map/gene_pathway.csv` (now the generated thin
  projection; it skips the leading `# GENERATED …` comment line automatically),
  groups genes by `pathway_group`, and writes one `pathways.jsonl` record per
  group. No network calls.

> **The two map CSVs are GENERATED — do not hand-edit them.** The full,
> multi-source, multi-valued truth is in the two `*_api.jsonl` sidecars; the CSVs
> are only single-value convenience projections for legacy consumers. Both build
> scripts are stdlib-only and go through `common.get_json`/`post_json` (cached to
> `data/raw`; `TE_REFRESH=1` forces fresh calls).

**The ONLY remaining hand elements in Track B** are (1) the transparent
in-code **bucket/mechanism keyword rulesets** (`PATHWAY_BUCKET_KEYWORDS` /
`TRIAL_MECHANISM_KEYWORDS`, which only *name* which fixed-vocabulary mechanism a
captured term belongs to) and (2) the **scoring weights** in `score/SCORING.md`
(deferred by decision). Everything else — gene pathway membership, drug MoA, and
the **`mesh_ui_join` disease classification** (`map/mesh_tree.py` reads the MeSH
Dementia subtree live from MeSH SPARQL; the old hand `mesh_disease.csv` was
deleted) — is API-derived.

### Score (processed JSONL → enriched in place)
- **`score/scores.py`** — computes the four explainable scores and enriches
  `genes.jsonl` and `pathways.jsonl` **in place**, storing every raw component
  input and weight alongside each derived number. `functional_support` is now a
  **real functional layer** aggregated per gene from `functional_links.jsonl`
  (max OT L2G score across loci, plus a brain-cell-type colocalisation bonus);
  it is joined by Ensembl `gene_id` first, then `gene_symbol`, and is `null` when
  a gene has no functional_links. Also (re)writes `score/SCORING.md`. Reads no
  live APIs.
- **`score/entity_metrics.py`** — builds the **per-entity METRICS layer** (the
  **AUTHORITATIVE agent layer**): one flat, machine-readable metrics record per
  **gene / variant / pathway**, written to
  `data/processed/translational-evidence/entity_metrics.jsonl`
  (schema `entity_metric.schema.json`). Reads the already-processed Track B files
  (`genes`, `gwas_associations`, `functional_links`, `trials`, `pathways`,
  `target_evidence`, `gene_pathways_api`) plus `shared/topic_evidence_links.jsonl`
  (literature counts) and the optional Track A `papers.jsonl` snapshot
  (paper→year for recency); reads no live APIs. Every metric is a **PRIMITIVE**
  under a dotted `"<group>.<name>"` key wrapped as `{value, source}`:
  **counts** (`n_*`), **raw** observed/external values (`best_neglog10p`,
  `max_l2g`, Open Targets datatype scores), **booleans** (`has_approval`), small
  **lists**, and **simple `a/b` ratios** (each carrying a `formula` note and
  `null` when the denominator is 0). `source` names the exact input field(s) +
  formula, so the layer is fully **explainable** and nothing is fabricated.

  > **Weighted composites REMOVED (counts/ratios philosophy).** The old
  > opinionated 0-1 composites — `genetic.genetic_support`,
  > `functional.functional_support`, `composite.translation_gap` — were **removed
  > from entity_metrics**. **No weighted 0-1 verdicts are shipped** (no
  > `0.5*x + 0.3*y`, no `"under-researched"` / `"under-translated"` /
  > `"emerging"` labels). Their raw **components** are kept as standalone
  > primitives so an **agent composes** those higher-order answers itself (see the
  > worked recipes in `score/METRICS.md`). `open_targets.*` are Open Targets' OWN
  > external harmonic-sum scores, kept RAW and clearly labelled — not our stats.
  > The 0-1 `evidence_scores` on `genes.jsonl` and the `scores` on `pathways.jsonl`
  > are **LEGACY weighted scores retained only for the current Track A map
  > contract** (to be replaced by these metrics); they are NOT part of the metrics
  > layer. Because `additionalProperties` is allowed everywhere, agents may attach
  > their own metric keys (e.g. `agent.under_researched`).

  See **`score/METRICS.md`** for the full field reference (every metric, formula,
  source, per entity_type) and worked agent-composition recipes. **Recency**
  metrics use `CURRENT_YEAR` (default `2026`, overridable via `TE_CURRENT_YEAR`)
  as "now" — never `datetime.today()` — with a 3-year recent window, so a given
  input always yields the same output:

  ```bash
  python3 translational-evidence/score/entity_metrics.py
  # override "now" for recency (deterministic), e.g.:
  TE_CURRENT_YEAR=2024 python3 translational-evidence/score/entity_metrics.py
  ```

  `2026-07-02` build: **7,278** records — **1,484 gene**, **5,786 variant**,
  **8 pathway**.

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
  - Curated **structured join tables** (see the crosswalk CSVs below).

  **Join method (BridgeV2) — STRUCTURED-FIRST, fully provenanced.** The bridge is
  now v2, built on the enriched **2,507-paper / 17-cluster** `track_a_snapshot`
  (abstracts, MeSH descriptors, chemical descriptors, and references are now
  populated). Prefer **structured, ID-based joins over text/regex**, and record
  on **every** link a machine-readable `method`, a `confidence`
  (`high`/`medium`/`low`), a `provenance` object with the exact join key, and a
  human-readable `notes`. Method priority (highest → lowest):

  1. `pmid_join` (high) — topic member **PMIDs** ∩ GWAS association PMIDs →
     `topic → gwas_association` (`paper_overlap`) + `topic → gene` for each
     reported gene. Join key: `pmid`.
  2. `mesh_ui_join` (high) — member-paper **MeSH UIs** classified via the
     **API-derived** MeSH tree (`map/mesh_tree.py`; MeSH SPARQL, Dementia branch
     `C10.228.140.380` + `F03.615.400`) → `topic → disease` (`mesh_annotation`),
     tallied per `disease_group`. Join key: `mesh_ui`; provenance also records
     the deciding `tree_number` and `classifier`. Zero hand definition — the
     disease buckets are read live from the MeSH ontology.
  3. `chemical_ui_crosswalk` (high) — member-paper **chemical/substance UIs** in
     the curated `map/chemical_gene.csv` (a gene-product descriptor points at its
     gene) → `topic → gene` (`chemical_annotation`). Join key: `chemical_ui`.
  4. `gene_pathway_curated` (medium) — pathway groups of the topic's
     **structurally-linked** genes via `map/gene_pathway.csv`, weighted by summed
     `genetic_support` → `topic → pathway` (`pathway_mapping`). Join key:
     `gene_symbol->pathway_group`.
  5. `regex_symbol_match` (low) — **DEMOTED fallback only**: case-sensitive
     whole-word gene-symbol match in member title+abstract, run **only** for
     genes not already linked structurally (symbols < 3 chars + an audited
     ambiguous-symbol blocklist excluded). Join key:
     `case_sensitive_whole_word_symbol`; provenance flags `fallback: true`.

  The full taxonomy — how each method works, its confidence, the join key it
  records, and the design principle — is documented in
  **`translational-evidence/exports/LINK_METHODS.md`**.

  **Curated crosswalk CSVs** (`translational-evidence/map/`, the auditable
  structured join tables — extend a method by adding rows, no code change):

  | CSV | columns | rows | feeds |
  | --- | --- | --- | --- |
  | `chemical_gene.csv` | `chemical_ui, chemical_term, gene_symbol, notes` | 46 | `chemical_ui_crosswalk` |
  | `gene_pathway.csv` | `gene_symbol, pathway_group, notes` | 304 (**GENERATED**) | `gene_pathway_curated` |

  > `gene_pathway.csv` is no longer a hand-curated table — it is now the
  > **GENERATED thin projection** written by `map/gene_pathway_build.py` from the
  > API capture (`gene_pathways_api.jsonl`), one row per gene that got a
  > `primary_bucket` (304 in the `2026-07-02` build vs the retired 41 hand rows).
  > The `gene_pathway_curated` bridge method feeds off `pathway_group`, so it now
  > runs against the API-derived projection. `chemical_gene.csv` is still a small
  > hand crosswalk for `chemical_ui_crosswalk`.

  `mesh_ui_join` no longer uses a hand CSV: `mesh_disease.csv` was **deleted** and
  replaced by `map/mesh_tree.py`, which reads the Dementia subtree live from the
  MeSH SPARQL endpoint and buckets descriptors by tree position (only five anchor
  sub-branch prefixes are hand-set; every descriptor in each bucket is API-read).

  **Outputs** (all under `data/processed/shared/`, all gitignored):
  - `topic_evidence_links.jsonl` — one explainable record per
    (topic, evidence_type, evidence_id, link_type) join
    (`topic_evidence_link.schema.json`); every record carries `method`,
    `confidence`, `provenance`, and `notes`.
  - `topic_evidence_rollup.jsonl` — one record per topic, the Track B half of the
    frontend `map_data.json` cluster (`topic_evidence_rollup.schema.json`).
  - `topic_bridge_manifest.json` — snapshot-awareness metadata (input counts +
    `link_methods` / `link_types` / `link_confidence` tallies). Not
    schema-validated.

  No counts are hardcoded in the script — they are read from the inputs, so a
  re-run against a refreshed corpus just works.

  **BridgeV2 counts (`track_a_snapshot`: 2,507 papers / 17 clusters):** 6,901
  links total — by method: `pmid_join` 6,607, `regex_symbol_match` 164,
  `chemical_ui_crosswalk` 69, `mesh_ui_join` 32, `gene_pathway_curated` 29; by
  confidence: high 6,708, low 164, medium 29. (`mesh_ui_join` moved 33→32 when it
  switched from the hand CSV to the API-derived MeSH tree: the tree correctly
  excludes three pre-dementia headings — Cognitive Dysfunction/MCI, Cognition
  Disorders, Neurocognitive Disorders — that sit *above* the Dementia branch,
  while newly catching genuine Dementia-branch descriptors like Huntington
  Disease, Multi-Infarct Dementia and Mixed Dementias.)

#### Refresh when Track A publishes a new snapshot

The bridge is built against a **materialized** `track_a_snapshot`. To refresh it
against a newer Track A run on `origin/main` (do not edit `topic-dynamics/**`):

1. `git fetch origin`, then **re-materialize** the snapshot files from
   `origin/main` into `data/interim/translational-evidence/track_a_snapshot/`
   (the `git show origin/main:data/processed/topic-dynamics/<file> > …` commands
   above — at minimum `topic_clusters.jsonl` and `papers.jsonl`).
2. **Re-run** the bridge:
   `python3 translational-evidence/exports/build_topic_bridge.py`.
3. **Regenerate the graph pack** so the new links surface as edges:
   `python3 translational-evidence/exports/build_evidence_graph.py`, then
   `python3 translational-evidence/exports/build_neo4j_export.py`, then
   `python3 translational-evidence/viz/build_graph_viz.py`.
4. **Re-validate**: `python3 translational-evidence/validate.py` (all files,
   including the graph `nodes.jsonl` / `edges.jsonl`, must be `OK` → exit 0).

Bridge coverage grows with a larger corpus (more PMID overlap, more MeSH/chemical
descriptors, more abstracts for the regex fallback). No schema or code change is
needed for the refresh.

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
| `entity_metrics.jsonl` | `entity_metric.schema.json` | 7,278 |

(Counts grew from the earlier Alzheimer-only build because the pipeline now
covers ADRD; `target_evidence` is ~300 targets × 5 disease ids.
`functional_links.jsonl` is the OT L2G functional layer: 3,372 L2G links + 0
colocalisation links over 1,710 distinct genes.)

`entity_metrics.jsonl` is the **per-entity METRICS layer** (one flat record per
gene / variant / pathway = **1,484 + 5,786 + 8 = 7,278**). Each metric is a
PRIMITIVE (count / raw value / boolean / list / simple ratio) under a dotted
`"<group>.<name>"` key wrapped as `{value, source}` (ratios add `formula`) —
transparent primitives only, **no weighted 0-1 composites**, no baked-in
verdicts, extensible via `additionalProperties`. See **`score/METRICS.md`** for
every metric's definition, formula, source, and worked agent-composition recipes.
Built by `score/entity_metrics.py`; recency uses `CURRENT_YEAR` (env-overridable
via `TE_CURRENT_YEAR`, default `2026`).

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

`exports/build_topic_bridge.py` (BridgeV2) writes two schema-validated shared
files (both gitignored). Counts below are from the BridgeV2 build against the
enriched Track A snapshot (**2,507 papers / 17 clusters**):

| Output file | Schema (`shared/schemas/`) | Records |
| --- | --- | --- |
| `topic_evidence_links.jsonl` | `topic_evidence_link.schema.json` | 6,902 |
| `topic_evidence_rollup.jsonl` | `topic_evidence_rollup.schema.json` | 17 |

The 6,901 links break down (by **method** / structured-first) as `pmid_join`
6,607 (`paper_overlap`), `regex_symbol_match` 164 (`gene_mention`, low-confidence
fallback), `chemical_ui_crosswalk` 69 (`chemical_annotation`), `mesh_ui_join` 32
(`mesh_annotation` → topic↔disease, API-derived MeSH tree), and
`gene_pathway_curated` 29
(`pathway_mapping`). By **confidence**: high 6,709, low 164, medium 29. Every
link carries `method` / `confidence` / `provenance` / `notes` — see
`exports/LINK_METHODS.md`.

Validator result (all files, including the two shared outputs and the graph
`nodes.jsonl` / `edges.jsonl`):

```
data/processed/translational-evidence/gwas_associations.jsonl: 7351 records, OK
data/processed/translational-evidence/genes.jsonl: 1484 records, OK
data/processed/translational-evidence/pathways.jsonl: 8 records, OK
data/processed/translational-evidence/trials.jsonl: 6841 records, OK
data/processed/translational-evidence/target_evidence.jsonl: 1499 records, OK
data/processed/translational-evidence/functional_links.jsonl: 3372 records, OK
data/processed/translational-evidence/entity_metrics.jsonl: 7278 records, OK
data/processed/shared/topic_evidence_links.jsonl: 11119 records, OK
data/processed/shared/topic_evidence_rollup.jsonl: 10 records, OK
data/exports/graph/nodes.jsonl: 16246 records, OK
data/exports/graph/edges.jsonl: 9138 records, OK
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

> The topic overlay (`topic` nodes + `topic_gene` / `topic_pathway` /
> `topic_disease` edges) is joined from the shared BridgeV2 (the enriched
> **2,507-paper / 17-cluster** snapshot). Each bridge edge **carries the link's
> `method` + `confidence`** (hoisted onto the edge and mirrored in `provenance`
> with the exact join key), so how+why every Track A↔B link was made is
> queryable in Neo4j and visible in the HTML explorer. It refreshes for free when
> Track A publishes a new snapshot and the bridge + graph pack are rebuilt (see
> §3 → "Refresh when Track A publishes a new snapshot"). Method taxonomy:
> `exports/LINK_METHODS.md`.

### Node & edge types + full counts (BridgeV2 build)

Counts are the real numbers from
`data/exports/graph/graph_manifest.json`.

**Nodes — 15,294 total:**

| node type | count | source |
| --- | --- | --- |
| `trial` | 6,841 | `trials.jsonl` |
| `variant` | 5,786 | `gwas_associations.jsonl` (rsID / locus) |
| `drug` | 2,111 | trial `interventions[]` |
| `gene` | 523 | `genes.jsonl` |
| `pathway` | 9 | `pathways.jsonl` |
| `topic` | 17 | `topic_evidence_rollup.jsonl` (Track A overlay) |
| `disease` | 7 | `disease_group` vocabulary |
| **total** | **15,294** | |

**Edges — 11,018 total:**

| edge type | count | meaning |
| --- | --- | --- |
| `trial_pathway` | 5,271 | trial → mechanism/pathway group |
| `trial_drug` | 3,350 | trial → intervention/drug |
| `variant_gene` | 1,274 | GWAS variant → reported gene |
| `gene_disease` | 535 | gene → dementia disease group |
| `topic_gene` | 316 | topic → gene (bridge; carries method/confidence) |
| `drug_pathway` | 174 | drug → pathway (via mechanism) |
| `gene_pathway` | 36 | gene → pathway group |
| `topic_disease` | 32 | **NEW** topic → disease group (bridge; `mesh_ui_join`, API-derived MeSH tree) |
| `topic_pathway` | 29 | topic → pathway (bridge; carries method/confidence) |
| **total** | **11,018** | |

The **377** topic bridge edges (`topic_gene` + `topic_pathway` +
`topic_disease`) each carry `method` + `confidence`. By method: `regex_symbol_match`
164, `pmid_join` 83, `chemical_ui_crosswalk` 69, `mesh_ui_join` 32,
`gene_pathway_curated` 29. By confidence: high 184, low 164, medium 29. (These are
also in the manifest under `edges.topic_bridge`; regenerated by
`build_evidence_graph.py`.) `paper_overlap` links to GWAS
associations are **not** edges — a GWAS association is not a graph node, so those
topic→association links would dangle; the reported genes already appear as
high-confidence `topic_gene` (`pmid_join`) edges.

(The builder dropped **1,949** dangling edges whose endpoints were not present as
nodes — reported as `edges.dangling_dropped` in the manifest — so every retained
edge resolves to two real nodes.)

Graph inputs (from the manifest): `genes` 523, `gwas_associations` 7,351,
`pathways` 9, `trials` 6,841, `target_evidence` 1,499, `functional_links` 3,372,
`topic_links` 6,902, `topic_rollup` 17.

### How to regenerate

Run after the pipeline (and after the topic bridge, so the topic overlay is
current):

```bash
# 1. Build the full graph (nodes.jsonl + edges.jsonl + graph_manifest.json).
#    Adds topic_gene / topic_pathway / topic_disease edges carrying method+confidence.
python3 translational-evidence/exports/build_evidence_graph.py

# 2a. Build the zero-install browser viz (graph_data.js + evidence_graph.html).
#     Click a bridge edge to see its method / confidence / join provenance.
python3 translational-evidence/viz/build_graph_viz.py

# 2b. Build the Neo4j-ready CSVs + loader (neo4j/{nodes,edges}.csv, load.cypher, README).
#     edges.csv gains method/confidence columns; load.cypher adds a TOPIC_DISEASE
#     relationship and SET r.method / r.confidence on every bridge relationship.
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
`2026-07-02` manifest reports **7,278** metrics records loaded and attached with
**0 unmatched** (`gene` 1,484, `variant` 5,786, `pathway` 8) — see
`graph_manifest.json → metrics.{attached_by_type, flat_keys, unmatched_records}`.

Flat props hoisted per node type (`FLAT_METRIC_KEYS`). The old weighted
`translation_gap` composite was **REMOVED** from the metrics layer; its raw
count/ratio **components** are hoisted instead (`best_neglog10p`, `n_papers`,
`max_l2g` on genes; `mean_best_neglog10p`, `trials_per_gene`, `n_papers` on
pathways) so an agent / Cypher query forms its own genetics-vs-clinical gap:

| node type | flat props (→ dotted metric) |
| --- | --- |
| `gene` | `stopped_ratio`, `direction_agreement`, `n_conflicting`, `n_trials`, `first_gwas_year`, `latest_gwas_year`, `n_recent_gwas`, `has_approval`, `best_neglog10p`, `n_papers`, `max_l2g` |
| `pathway` | `stopped_ratio`, `has_approval`, `n_trials`, `n_drugs`, `first_trial_year`, `latest_trial_year`, `n_recent_trials`, `mean_best_neglog10p`, `trials_per_gene`, `n_papers` |
| `variant` | `n_associations`, `n_studies`, `first_year`, `latest_year`, `n_recent`, `direction_agreement` |

`build_neo4j_export.py` writes these as typed CSV columns and `load.cypher`
loads them with `toInteger()` / `toFloat()` / `toBoolean()` (a blank column ==
Cypher `null`). The `metrics_translation_gap` column was **removed** in step with
the metrics-layer change; the raw count/ratio columns above replace it. (The
node still carries the LEGACY `genetic_support` / `functional_support` /
`translation_gap` **Track A map-contract scores** on its `scores` dict / as their
own CSV columns — those are distinct from the metrics layer and are being
replaced by the metrics.) `load.cypher` §5 ships **commented-out example
queries** (copy one to run) — these are read-only illustrations; **verdicts are
composed from the metrics, never baked in**:

```cypher
// Strong genetics but clinically stalled genes (high raw signal + high stopped
// share; no verdict baked in — the old 0-1 translation_gap composite is gone)
MATCH (g:Gene)
WHERE g.best_neglog10p >= 20 AND g.stopped_ratio >= 0.3 AND g.n_trials >= 5
RETURN g.label, g.best_neglog10p, g.stopped_ratio, g.n_trials, g.n_papers
ORDER BY g.stopped_ratio DESC, g.best_neglog10p DESC LIMIT 25;

// Direction-conflict genes: GWAS effect directions disagree across studies
MATCH (g:Gene)
WHERE g.direction_agreement IS NOT NULL AND g.direction_agreement < 0.7 AND g.n_conflicting >= 2
RETURN g.label, g.direction_agreement, g.n_conflicting, g.best_neglog10p
ORDER BY g.n_conflicting DESC, g.direction_agreement ASC LIMIT 25;

// Recently-emerging loci: latest GWAS in the last ~3 years (CURRENT_YEAR-2)
MATCH (v:Variant)
WHERE v.latest_year >= 2024 AND v.n_recent >= 1
RETURN v.label, v.latest_year, v.n_recent, v.n_associations, v.n_studies
ORDER BY v.latest_year DESC, v.n_associations DESC LIMIT 25;

// Under-translated pathways (agent-composed): strong member-gene genetics +
// broad literature but no trials — no shipped 0-1 gap score
MATCH (p:Pathway)
WHERE p.has_approval = false AND p.n_trials = 0 AND p.mean_best_neglog10p >= 15
RETURN p.label, p.mean_best_neglog10p, p.n_papers, p.trials_per_gene, p.n_trials, p.n_drugs
ORDER BY p.mean_best_neglog10p DESC LIMIT 25;
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
Expect **15,294** nodes and **11,018** relationships loaded (incl. the new
`TOPIC_DISEASE` relationship). `disease_groups` is loaded as a Neo4j list
(`'alzheimer' IN n.disease_groups`); `provenance` is a JSON string (filter with
`CONTAINS`, or parse it in your app). Track A↔B bridge relationships
(`TOPIC_GENE` / `TOPIC_PATHWAY` / `TOPIC_DISEASE`) carry `r.method` +
`r.confidence` (from `edges.csv` `method` / `confidence` columns) so you can
filter to structured, high-confidence links, e.g.
`MATCH (t:Topic)-[r]->(x) WHERE r.confidence='high' AND r.method<>'regex_symbol_match'`.
APOC is **not** required.

### Graph export files (all under `data/exports/graph/`, gitignored)

| file | what it is |
| --- | --- |
| `nodes.jsonl` | 15,294 evidence nodes (`evidence_node.schema.json`) |
| `edges.jsonl` | 11,018 evidence edges (`evidence_edge.schema.json`); bridge edges carry `method` + `confidence` |
| `graph_manifest.json` | node/edge counts by type, inputs, layout, dangling-drop count, `edges.topic_bridge` method/confidence tally |
| `graph_data.js` | the same graph as `window.GRAPH` for the browser viz |
| `evidence_graph.html` | zero-install sigma.js explorer (option A); click a bridge edge for method/confidence/provenance |
| `neo4j/nodes.csv`, `neo4j/edges.csv` | Neo4j `LOAD CSV` inputs (`edges.csv` has `method` / `confidence` cols) |
| `neo4j/load.cypher` | constraint + typed-label loader (no APOC); adds `TOPIC_DISEASE` + `SET r.method` / `r.confidence` |
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

A `topic_disease` bridge edge (`data/exports/graph/edges.jsonl`) — carries the
`method` + `confidence` (hoisted + mirrored in `provenance` with the join key):

```json
{
  "confidence": "high",
  "edge_id": "e:tds:topic:013:disease:alzheimer:mesh_annotation",
  "edge_type": "topic_disease",
  "evidence": "mesh_annotation",
  "method": "mesh_ui_join",
  "provenance": {
    "evidence_type": "disease",
    "evidence_id": "disease:alzheimer",
    "join_key": "mesh_ui",
    "link_provenance": {
      "disease_group": "alzheimer",
      "classifier": "mesh_tree (MeSH SPARQL, branch C10.228.140.380)",
      "mesh_uis": [{"mesh_term": "Alzheimer Disease", "mesh_ui": "D000544", "tree_number": "C10.228.140.380.100", "n_papers": 2}],
      "n_major": 2, "n_papers": 2
    },
    "link_type": "mesh_annotation",
    "method": "mesh_ui_join", "confidence": "high",
    "source": "topic_evidence_links", "topic_id": "topic:013",
    "notes": "disease group 'alzheimer' via API-derived MeSH tree (mesh_tree, branch C10.228.140.380) in 2/3 member papers (2 major)"
  },
  "score": 0.6667,
  "source_id": "topic:013",
  "target_id": "disease:alzheimer"
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

---

## 11. API-derived gene→pathway & drug→mechanism map (multi-valued capture)

The `map/` layer is now **API-derived and multi-valued**: gene pathway
membership comes from mygene.info + Reactome + Open Targets, and drug
mechanism-of-action comes from Open Targets. The **full** captured annotation set
is persisted per gene/drug (nothing is collapsed to a "voted winner" and
discarded); the AD-mechanism buckets are a **list of signals**; and a single
`primary` value is projected **only** for the two thin legacy CSVs. Full design
in **[`map/README.md`](map/README.md)**.

### Files

| File | Role |
| --- | --- |
| `data/processed/translational-evidence/gene_pathways_api.jsonl` | rich per-gene capture (all GO/Reactome/OT + `ad_bucket_signals[]` + `buckets[]` + `primary_bucket`) |
| `data/processed/translational-evidence/drug_mechanism_api.jsonl` | rich per-drug capture (all OT MoA rows + targets + `mechanism_signals[]` + `mechanisms[]` + `primary_mechanism`) |
| `map/gene_pathway.csv` | **GENERATED** thin projection: `gene_symbol, pathway_group=primary_bucket, notes` |
| `map/intervention_mechanism.csv` | **GENERATED** thin projection: `keyword, mechanism_group=primary_mechanism, notes` |

> The trials-corpus-driven `drug_mechanism_api.jsonl` (singular) from
> `intervention_mechanism_build.py` is authoritative and is what the CSV
> projection and the drug_target trial linkage use. (A plural
> `drug_mechanisms_api.jsonl` seeded-INN sidecar was previously emitted by
> `gene_pathway_build.py`; that orphan is no longer produced.)

### Capture richness (`2026-07-02` build, 523 genes)

- **Avg per gene:** 17.8 GO terms (max 227), 2.6 Reactome pathways (max 78),
  2.5 OT pathways (max 78). Source coverage: 436/523 genes have ≥1 GO term,
  290 have ≥1 Reactome pathway, 287 have ≥1 OT pathway, 487 carry an OT
  `approvedSymbol`. 87 genes had all three sources empty (unknown to the APIs);
  their record is still stored (empty source lists, no fabricated data).
- **Multi-valued richness:** **150 genes carry >1 AD-bucket signal** (distinct
  buckets), i.e. genuinely multi-mechanism. Distribution of #buckets/gene:
  0→220, 1→153, 2→70, 3→45, 4→20, 5→6, 6→4, 7→4, 8→1 (APOE).
- **`unknown` genes:** **220 genes** got no keyword hit (empty `buckets[]`) yet
  **retain their full raw GO/Reactome/OT annotations** in the JSONL — they are
  simply omitted from the thin CSV. 303 genes have a `primary_bucket`; the CSV
  has 304 rows (one extra row is a symbol that resolves to two gene records).
- **Drug side:** 222 distinct ChEMBL drugs resolved from the trial corpus;
  primary_mechanism distribution `other` 129, `synaptic_neuroprotection` 36,
  `amyloid` 19, `cholinergic_symptomatic` 17, `vascular` 11, `lipid_metabolism`
  6, `inflammation_microglia` 2, `tau` 2. Trial coverage: **983 / 2,450**
  drug/biological trials (40.1%) are tag-able by the resolved keyword map.

### Adversarial verification (VerifyDocs)

Six genes (APOE, TREM2, MAPT, BIN1, APP + the multi-bucket **CLU**) and four
drugs (donepezil, lecanemab, semaglutide, memantine) were **re-fetched live**
(`TE_REFRESH=1`) and compared to the recorded sidecars:

- **Genes — PASS (6/6).** For every gene the recorded GO / Reactome / OT sets
  are **exactly equal** to the live API response (e.g. APP 227 GO / 20 Reactome /
  20 OT; APOE 157 / 12 / 12), UniProt accessions match, and **every**
  `ad_bucket_signal.matched_term` is a real captured term. Nothing dropped.
- **Drugs — PASS (4/4).** Recorded MoA rows + target symbols exactly equal live
  OT (donepezil→ACHE "Acetylcholinesterase inhibitor"; lecanemab "Amyloid-beta
  A4 protein inhibitor"; memantine "Glutamate [NMDA] receptor…"; semaglutide
  "Glucagon-like peptide 1 receptor agonist"). Every mechanism signal cites a
  real MoA text.

### Known limitations of the *primary projection* (not the capture)

The rich capture is faithful and complete; these caveats apply only to the
single-value projection and the hand keyword ruleset:

1. **Primary ≠ old hand curation (19/41 agree).** Because the primary is the
   *most-source-supported* bucket, it can differ from the retired hand CSV: e.g.
   **APP → `endocytosis_endosomal`** (not amyloid), **MAPT → `synaptic_neuronal`**
   (not tau), **SORL1 → amyloid** (not endocytosis). The amyloid/tau signals are
   still present in `buckets[]`; consumers wanting a specific mechanism should
   read the multi-valued list, not `primary_bucket` alone. 8 old-hand genes have
   no primary: **5 are outside the current GWAS gene universe** (PSEN1, PSEN2,
   TSPOAP1, WWOX, IQCK — never processed) and **3 are present but `unknown`**
   (MS4A4A, MS4A6A, CASS4: sparse GO, no keyword hit; raw GO still stored).
2. **`"a-beta"` keyword collides with `alpha-beta`.** The `amyloid` keyword list
   (from the spec) includes `"a-beta"`, which matches the substring `alpha-beta`
   in GO terms like "…CD8-positive, **alpha-beta** T cell activation",
   mis-tagging **10 signals** across CCR2/HLA-DRB1/PSMA1/DAPL1/etc. as `amyloid`.
   DAPL1's only amyloid signal is this false positive, so it gets a spurious
   `amyloid` primary. **Recommended fix:** replace `"a-beta"` with the
   word-boundary form (e.g. match `"aβ"`/`"a-beta "` with a trailing delimiter,
   or drop it since `"amyloid"`/`"beta-amyloid"` already cover Aβ terms).
3. **GLP-1 drugs project to `other`.** OT reports semaglutide/liraglutide MoA as
   "Glucagon-like peptide 1 receptor agonist", which does not contain the literal
   `"glp-1"` keyword, so they fall through to `other` (faithful, not data loss).
   **Recommended fix:** add `"glucagon-like peptide"` to the `lipid_metabolism`
   keyword list.
4. **`pathways.jsonl` now has 7 groups (was 9).** No gene projects to `tau` or
   `other` as its *primary* (tau signals are always outnumbered; `other` is not a
   gene bucket), so those groups drop out of the projection even though tau
   signals exist in the rich capture. Downstream pathway-level scoring therefore
   has no standalone `tau` pathway today.

**Remaining hand elements across Track B** (everything else is API-derived):
the two **keyword rulesets** (`PATHWAY_BUCKET_KEYWORDS` /
`TRIAL_MECHANISM_KEYWORDS`) and the **scoring weights** (`score/SCORING.md`,
deferred by decision). `mesh_disease` is already API-derived via `map/mesh_tree.py`
(MeSH SPARQL Dementia subtree); the old hand `mesh_disease.csv` was deleted.
