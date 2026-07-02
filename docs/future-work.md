# Track B — deferred / future work

Enrichments intentionally **not** built in the current finalise pass. Captured
here so they aren't lost; none are blockers for the current data + linkage.
Prioritised by how much they unlock.

## 1. Druggability + drug→target universe (makes "propose a new/repurposed drug" real)

**Why:** an analysis agent can already answer *which targets are under-developed*
(genetic evidence high, `clinical.n_trials`/`max_phase` low). It **cannot** yet
answer *"suggest a drug for this under-translated target"* honestly — for exactly
the under-translated mechanisms (endocytosis, epigenetic) there are no drug edges,
because we only carry drugs that appear in **AD trials** (`drug_mechanism_api.jsonl`,
~222 drugs / 159 with a resolved target; 477 `drug_gene` graph edges). There is no
"is this target even druggable" signal and no all-indication drug list to repurpose
from.

**What to add (Open Targets, stdlib-only, cached like the rest):**
- `target.tractability` per gene → `druggability.*` metrics (small-molecule /
  antibody / PROTAC modalities, clinical-precedence buckets, druggable-genome
  membership). Kept raw, labelled external, same pattern as `open_targets.*`.
- **all-indication known drugs** per target (OT `knownDrugs` / ChEMBL) → a full
  `drug → target` map (not just AD-trial drugs), so an agent can surface an
  existing drug from another indication that hits an under-translated AD gene =
  repurposing. Add these as `drug_gene` edges tagged with `indication` +
  `phase` so the browser distinguishes AD-trial vs other-indication drugs.

**How to apply:** new ingest (OT GraphQL `target(ensemblId){ tractability … }`
+ known-drugs), feed into `entity_metrics.py` (`druggability.*`, `n_known_drugs`)
and `build_evidence_graph.py` (extra `drug_gene` edges). ~20–30 min of network.
Note the exact OT v4 field names need confirming (a naive `knownDrugs` on `Target`
errored in probing — likely a different accessor/version).

## 2. Non-AD / broader gene coverage

Current gene set is the union of GWAS-derived genes + Open Targets associated
targets for the ADRD disease set. Broaden to fuller neurodegeneration coverage
(e.g. more of the `disease_group` vocab represented as first-class targets, not
only where they co-occur with AD) so cross-disease contrasts are less AD-anchored.

## 3. Further drug coverage

Beyond #1: intervention-name → drug resolution currently covers the AD-trials
corpus. Widen normalisation (synonyms, combination therapies, biologics naming)
so more trials resolve to a target gene (currently 780 / ~6.8k trials resolve).

---
Deferred by the user on 2026-07-02: *"can do in future pass along with optional
enrichments … need to get what we have finalised and linked to the papers now."*
