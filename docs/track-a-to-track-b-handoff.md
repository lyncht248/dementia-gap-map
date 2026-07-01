# Track A → Track B data handoff

What Track A publishes for Track B to link GWAS / genes / pathways / trials to
literature topics. All files are JSONL in `data/processed/topic-dynamics/` and
validate against `shared/schemas/`.

## Files

| File | Schema | Contents |
|---|---|---|
| `papers.jsonl` | `paper.schema.json` (+ extra fields) | one record per corpus paper |
| `topic_clusters.jsonl` | `topic_cluster.schema.json` | topics with member `paper_ids` |
| `topic_trajectories.jsonl` | `topic_trajectory.schema.json` | per-topic yearly counts + scores |
| `paper_edges.jsonl` | `paper_edge.schema.json` | coupling + co-citation edges |

## `papers.jsonl` — per-paper fields

| Field | Status | Notes |
|---|---|---|
| `paper_id` | ✅ | stable id, `pmid:{PMID}` |
| `pmid` | ✅ | **primary join key to GWAS**; every paper has one (corpus is PubMed-derived) |
| `doi` | ✅ | fallback join, where PubMed has it |
| `title` | ✅ | |
| `abstract` | ✅ **now populated** | via efetch — was `null` in the 400-paper snapshot; this is the gene-mention text |
| `year` | ✅ | |
| `journal` | ✅ | |
| `authors` | ✅ | |
| `mesh` | ✅ **new** | `[{term, ui, major}]` — MeSH descriptors (disease/anatomy/concept). `ui` (e.g. `D000544` = Alzheimer Disease) is a stable join key; `major` flags the paper's main topics |
| `chemicals` | ✅ **new** | `[{term, ui}]` — MeSH substances: drugs + gene products (e.g. `tau Proteins`, `Amyloid beta-Peptides`, `Lipids`) |
| `keywords` | ✅ **new** | author keywords (sparse on older papers) |
| `references` | ✅ **new** | cited PMIDs (raw) — for your own bibliographic coupling + temporal (`new` / `contradicted`) signals; join to `year` per PMID |
| `metrics` | ✅ | `{citation_count, relative_citation_ratio, apt, is_clinical}` from iCite |
| `sources` | ✅ | provenance |

**Not inline:** `cited_by` (who cites each paper). It's fetched and drives the
co-citation edges in `paper_edges.jsonl`; the raw per-paper lists (capped at
2000/paper) can be emitted as a separate `paper_links.jsonl` on request.

## `topic_clusters.jsonl` — topic layer

`topic_id`, `label`, `top_terms`, `paper_ids` (membership), `year_start`,
`year_end`, and `scores` = `{emergence, growth, influence, cohesion, pct_new,
mean_rcr}`.

- **Membership is hard** — each paper belongs to ≤1 topic; no soft membership
  weights are computed (all effectively 1.0).
- **Some papers are unassigned** — singletons / sub-3-paper communities are
  dropped (`MIN_CLUSTER_SIZE=3`), so not every `pmid` in `papers.jsonl` appears
  in a topic. Coverage is much higher on the full run than the 400 snapshot but
  is not 100%.
- `summary` is currently `null` (not generated).

## `topic_trajectories.jsonl`

`trajectory_id`, `topic_ids`, `yearly_counts` (`[{year, paper_count}]`),
`scores`. MVP = one trajectory per topic; split/merge linking not yet done.

## Recommended joins for Track B

- **GWAS ↔ literature:** `papers.pmid` ↔ `gwas_associations.publication.pmid`.
- **Topic → disease/gene/chemical:** `topic_clusters.paper_ids` → `papers` →
  `mesh` / `chemicals` (use `ui` for stable joins) and `abstract`/`title` for
  text mining.
- **Your own coupling / temporal signals:** use `papers.references` + `year`.

## Logistics

- **Corpus size:** query matches **4,776** PubMed papers (+ ~4 backbone) ≈
  **4,780**. Exact count confirmed on completion.
- **Query:** dementia (the `Dementia` MeSH tree + Alzheimer/FTD/Lewy/vascular/
  cognitive-impairment synonyms) **AND** mentions GWAS. Full definition in
  `topics/config.py::SEARCH_TERM`.
- **What's live now:** the **full-field dataset of 4,780 papers** is committed
  to `data/processed/topic-dynamics/` — abstracts 98%, MeSH 79%, references 70%,
  86% of papers assigned to a topic, ~287k coupling+co-citation edges. **The
  schema/contract is final — build the bridge against this data.**
- **Known follow-ups (don't block Track B):** clustering is currently coarse
  (10 broad topics; the two largest are near-duplicate AD clusters) — a
  resolution-tuning issue to be refined (e.g. Leiden). `paper_edges.jsonl` is
  large (~45 MB); Track B does not need it (use `papers.references`).
