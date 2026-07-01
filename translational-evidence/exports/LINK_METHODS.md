# Link Methods — the Track A ↔ Track B bridge taxonomy

This document is the reference for **every way** a Track A literature topic gets
linked to a piece of Track B translational evidence (gene / pathway / disease /
GWAS association), how confident each method is, and the exact join key it records
in provenance.

## Design principle

> **Structured, ID-based joins are preferred over text / regex matching.**
> **Every link records HOW and WHY it was made** — a machine-readable `method`,
> a `confidence` (`high` | `medium` | `low`), a `provenance` object carrying the
> exact join key + counts, and a human-readable `notes` string — so a future
> agent can *trust* a link (see how it was derived) and *extend* the taxonomy
> (add a new curated crosswalk, promote/demote a method) without reverse-
> engineering anything.

Regex symbol matching is **demoted**: it runs only as a labelled, low-confidence
*fallback* for genes that were not already linked by any structured method.

## Method priority (highest → lowest)

```
pmid_join  >  mesh_ui_join  >  chemical_ui_crosswalk  >  gene_pathway_curated  >  regex_symbol_match
```

When the same (topic, gene) is discovered by several methods, the
**highest-confidence** link wins the dedup key
`(topic_id, evidence_type, evidence_id, link_type)`; the other methods are
recorded on the incumbent link under `provenance.also_found_by` so nothing is
lost.

## Where these links live

| Artifact | What it carries |
| --- | --- |
| `data/processed/shared/topic_evidence_links.jsonl` | one record per link, with `method` / `confidence` / `provenance` / `notes` (schema: `topic_evidence_link.schema.json`) |
| `data/exports/graph/edges.jsonl` | bridge links surfaced as graph edges `topic_gene` / `topic_pathway` / `topic_disease`, with `method` + `confidence` **hoisted onto the edge** and mirrored inside `provenance` (with the exact `join_key`) |
| `data/exports/graph/neo4j/edges.csv` + `load.cypher` | `edges.csv` has `method` / `confidence` columns; `load.cypher` MERGEs a `:TOPIC_DISEASE` (and `:TOPIC_GENE` / `:TOPIC_PATHWAY`) relationship and `SET r.method`, `SET r.confidence` |
| `data/exports/graph/evidence_graph.html` | click a bridge edge → side panel renders `method`, `confidence`, and the full join provenance |

---

## The five methods

### 1. `pmid_join` — structured PMID join (confidence: **high**)

- **Link types:** `paper_overlap` (topic→gwas_association, and topic→gene).
- **How it works:** a topic's member-paper **PMIDs** are intersected with the
  **PMIDs of GWAS associations** in Track B (`gwas_associations.jsonl`). Each
  matched association becomes a `topic → gwas_association` link, and every gene
  it *reports* (that exists in Track B) becomes a `topic → gene` link.
- **Why high:** PMID is a globally unique publication identifier — the join is
  unambiguous. The paper that established the GWAS hit literally sits inside the
  topic cluster.
- **Provenance join key:** `pmid` (per association: `pmid`, `study_accession`,
  `association_id`; per gene: `reported_symbol`, `pmids[]`, `study_accessions[]`,
  `n_pmids`).
- **Graph note:** only the `topic → gene` half surfaces as an edge
  (`topic_gene`); `topic → gwas_association` links are *not* emitted as edges
  because a GWAS association is not a standalone graph node (the reported genes
  already carry the signal).

### 2. `mesh_ui_join` — API-derived MeSH-tree disease join (confidence: **high**)

- **Link type:** `mesh_annotation` (topic→disease).
- **How it works:** each member paper's **MeSH descriptor UIs** are classified
  by `translational-evidence/map/mesh_tree.py`, which reads the entire Dementia
  subtree live from the **NLM MeSH SPARQL endpoint** (branches
  `C10.228.140.380` + `F03.615.400`) and buckets each descriptor into a
  `disease_group` purely from its **tree number** (its sub-branch position).
  Papers are tallied per `disease_group`; one `topic → disease` link is emitted
  per group, scored by the fraction of member papers carrying a matching
  descriptor.
- **Zero hand definition:** the *only* hand input is five anchor sub-branch
  prefixes (`…100`=alzheimer, `…230`=vascular, `…266`/`…132`=frontotemporal,
  `…422`=lewy_body, `…711`=mixed); every descriptor in each bucket is read from
  the API, so new/retired MeSH descriptors are picked up on a re-run with no CSV
  edit. The hand-curated `mesh_disease.csv` has been **deleted**.
- **Why high:** MeSH is a controlled vocabulary; a descriptor UID (e.g.
  `D000544` = *Alzheimer Disease*, tree `C10.228.140.380.100`) is an exact
  pointer whose disease group is determined by the authoritative ontology — no
  free-text guessing and no hand list to drift.
- **Provenance join key:** `mesh_ui` (plus `disease_group`, `n_papers`,
  `n_major` major-topic mentions, `classifier`, and a per-UI `mesh_uis[]`
  breakdown with `mesh_term` + `tree_number` + `n_papers`).
- **Fallback:** if SPARQL is unavailable, `mesh_tree.py` fetches per-UI
  descriptor JSON (`https://id.nlm.nih.gov/mesh/{UI}.json`) for the corpus's
  distinct MeSH UIs and classifies by tree-number prefix (cached; path logged).
- **Graph:** surfaces as the **`topic_disease`** edge — the new edge type this
  bridge adds.

### 3. `chemical_ui_crosswalk` — curated chemical/gene-product UI join (confidence: **high**)

- **Link type:** `chemical_annotation` (topic→gene).
- **How it works:** each member paper's **chemical (substance) descriptor UIs**
  are looked up in the curated crosswalk
  `translational-evidence/map/chemical_gene.csv`
  (`chemical_ui → gene_symbol`). A gene-product descriptor (e.g. `D016875` =
  *tau Proteins* → `MAPT`, or `D001057` = *Apolipoproteins E* → `APOE`) is an
  unambiguous pointer to its gene. The gene must exist in Track B evidence.
- **Why high:** like MeSH, this is a controlled-vocabulary UI join — the
  descriptor *is* the entity, so there is no lexical ambiguity.
- **Provenance join key:** `chemical_ui` (plus `gene_symbol`, `n_papers`, and a
  per-UI `chemical_uis[]` breakdown with `chemical_term` + `n_papers`).
- **Graph:** surfaces as a `topic_gene` edge with `method=chemical_ui_crosswalk`.

### 4. `gene_pathway_curated` — curated gene→pathway rollup (confidence: **medium**)

- **Link type:** `pathway_mapping` (topic→pathway).
- **How it works:** the pathway (mechanism) groups of the topic's
  **structurally-linked genes** (those found by `pmid_join` or
  `chemical_ui_crosswalk`) are read from the curated
  `translational-evidence/map/gene_pathway.csv` (`gene_symbol → pathway_group`),
  then weighted by summed `genetic_support`. Each represented pathway group
  becomes a `topic → pathway` link; the top-weighted group is flagged
  `is_dominant`.
- **Why medium:** the gene→pathway assignment is curated (structured), but the
  topic→pathway link is *derived one hop* from the structural gene links rather
  than joined directly on a topic-level identifier.
- **Provenance join key:** `gene_symbol->pathway_group` (plus `pathway_group`,
  `via_genes[]`, `summed_genetic_support`, `share`, `is_dominant`).
- **Guardrail:** pathway groups are rolled up **only from structurally-linked
  genes** — never from regex-matched ones — so a low-confidence text hit can
  never inflate a pathway link.
- **Graph:** surfaces as the `topic_pathway` edge.

### 5. `regex_symbol_match` — case-sensitive symbol text match (confidence: **low**) — *DEMOTED FALLBACK*

- **Link type:** `gene_mention` (topic→gene).
- **How it works:** a case-sensitive, whole-word regex for each Track B gene
  **symbol** is run over member papers' **title + abstract**. Runs **only** for
  genes *not already linked* by `pmid_join` or `chemical_ui_crosswalk`.
- **Safeguards (why it stays low-confidence & narrow):**
  - symbols shorter than `MIN_SYMBOL_LEN` (3) are skipped (collision-prone);
  - a curated `AMBIGUOUS_SYMBOLS` blocklist drops symbols that collide with
    English words (`SET`, `MAX`, `REST`, `CELL`, `MICE`, …);
  - the blocklist is audited against the actual snapshot text at build time.
- **Why low:** it is a lexical guess over free text, not an ID join. It exists
  purely so a genuinely relevant gene with no structured annotation is not
  invisible — never to compete with a structured link.
- **Provenance join key:** `case_sensitive_whole_word_symbol` (plus
  `gene_symbol`, `n_match`, `n_members`, `matched_in` = abstract/title/both, and
  `fallback: true` + a `note` stating why the fallback fired).
- **Graph:** surfaces as a `topic_gene` edge with `method=regex_symbol_match`,
  `confidence=low` — trivially filterable out (e.g. Cypher
  `WHERE r.method <> 'regex_symbol_match'`).

---

## Method / link_type / confidence map

| method | link_type | evidence_type | graph edge | confidence | join key |
| --- | --- | --- | --- | --- | --- |
| `pmid_join` | `paper_overlap` | gwas_association, gene | `topic_gene` (gene half only) | high | `pmid` |
| `mesh_ui_join` | `mesh_annotation` | disease | `topic_disease` | high | `mesh_ui` (+ `tree_number`, API-derived) |
| `chemical_ui_crosswalk` | `chemical_annotation` | gene | `topic_gene` | high | `chemical_ui` |
| `gene_pathway_curated` | `pathway_mapping` | pathway | `topic_pathway` | medium | `gene_symbol->pathway_group` |
| `regex_symbol_match` | `gene_mention` | gene | `topic_gene` | low | `case_sensitive_whole_word_symbol` |

## Curated crosswalks (the structured join tables)

These CSVs are the auditable, hand-curated join tables that make the structured
methods possible. Extend a method by adding rows here — no code change needed.

| CSV | columns | rows | feeds |
| --- | --- | --- | --- |
| `translational-evidence/map/chemical_gene.csv` | `chemical_ui, chemical_term, gene_symbol, notes` | 46 | `chemical_ui_crosswalk` |
| `translational-evidence/map/gene_pathway.csv` | `gene_symbol, pathway_group, notes` | 41 | `gene_pathway_curated` |

`mesh_ui_join` is **not** table-driven any more: `mesh_disease.csv` was deleted
and replaced by `translational-evidence/map/mesh_tree.py`, which derives the
`mesh_ui → disease_group` mapping live from the MeSH tree (SPARQL). Extend it by
editing the five anchor sub-branch prefixes in `mesh_tree.py`, not a CSV.

## Current link + edge counts (`track_a_snapshot`: 2,507 papers / 17 clusters)

Links (`topic_evidence_links.jsonl`, total **6,901**):

| method | links | | link_type | links | | confidence | links |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `pmid_join` | 6,607 | | `paper_overlap` | 6,607 | | high | 6,708 |
| `regex_symbol_match` | 164 | | `gene_mention` | 164 | | medium | 29 |
| `chemical_ui_crosswalk` | 69 | | `chemical_annotation` | 69 | | low | 164 |
| `gene_pathway_curated` | 29 | | `pathway_mapping` | 29 | | | |
| `mesh_ui_join` | 32 | | `mesh_annotation` | 32 | | | |

Graph bridge edges (`edges.jsonl`, total **377** — `paper_overlap` gwas_association
links are not edges; the gene half is):

| edge_type | edges | dominant methods |
| --- | --- | --- |
| `topic_gene` | 316 | `pmid_join` (83), `chemical_ui_crosswalk` (69), `regex_symbol_match` (164) |
| `topic_disease` | 32 | `mesh_ui_join` (32) |
| `topic_pathway` | 29 | `gene_pathway_curated` (29) |

Bridge edges by confidence: **high** 184, **low** 164, **medium** 29.

> Counts are read from the inputs at build time (nothing hardcoded), so a re-run
> against a refreshed snapshot just re-derives them. See
> `data/processed/shared/topic_bridge_manifest.json` and
> `data/exports/graph/graph_manifest.json` (`edges.topic_bridge`) for the live
> numbers.

## How to regenerate

```bash
# 1. Rebuild the bridge links + rollup (structured-first joins).
python3 translational-evidence/exports/build_topic_bridge.py

# 2. Rebuild the evidence graph (adds topic_gene / topic_pathway / topic_disease
#    edges carrying method + confidence).
python3 translational-evidence/exports/build_evidence_graph.py

# 3. Re-project to Neo4j (edges.csv method/confidence cols + TOPIC_DISEASE rel).
python3 translational-evidence/exports/build_neo4j_export.py

# 4. Rebuild the HTML explorer (clickable bridge edges show method/confidence).
python3 translational-evidence/viz/build_graph_viz.py

# 5. Validate everything (graph nodes/edges included) — must exit 0.
python3 translational-evidence/validate.py
```
