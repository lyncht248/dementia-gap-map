#!/usr/bin/env python3
"""Union GWAS-derived genes with Open Targets associated targets.

Track B (translational evidence). Standard-library only (Python 3.9).

The GWAS-only gene list (``normalize/gwas_catalog.py`` -> ``genes.jsonl``) misses
known / Mendelian Alzheimer & related-dementia genes that never reach genome-wide
significance in the GWAS Catalog (e.g. PSEN1, PSEN2, GRN). Open Targets, which
aggregates genetic + literature + clinical evidence against MONDO diseases, does
carry them. This step UNIONs the two by stable Ensembl gene id so those genes are
present and queryable, while staying honest about provenance.

Inputs (both already produced upstream):
  - PROCESSED_DIR/genes.jsonl            (gene.schema.json; GWAS-derived)
  - PROCESSED_DIR/target_evidence.jsonl  (target_evidence.schema.json; OT rows,
    one per (target_id, disease_id); target_id is the Ensembl gene id)

Union rules (dedup by gene_id = Ensembl):
  (a) GWAS genes keep every existing field untouched (gwas_* evidence_scores,
      disease_groups, entrez_ids, ...). We only ADD:
        - "sources": ["gwas_catalog"]  (+ "open_targets" if an OT row matched)
        - merged Open Targets disease_group values into "disease_groups"
        - evidence_scores.open_targets  (headline OT scores, anchor-preferred)
  (b) Genes only in Open Targets (e.g. PSEN1/PSEN2/GRN) are ADDED as new records
      with gene_id=target_id (Ensembl), symbol=target_label, name=approved_name.
      Their GWAS fields are explicitly NULL (honest: not GWAS-supported) so they
      still validate and downstream scoring treats their genetic support as
      absent rather than fabricated.

OT match priority mirrors ``score/scores.py``: Ensembl gene_id first, then
symbol==target_label. Headline OT scores prefer the Alzheimer anchor disease row
(MONDO_0004975), falling back to the first-seen row -- identical to the scoring
step -- so this merge and the later enrich agree.

The result is rewritten to PROCESSED_DIR/genes.jsonl (gene.schema.json:
gene_id + symbol required). ``score/scores.py`` runs later and further enriches
these records (including the flat ``open_targets_*`` headline fields) in place;
this step just guarantees the OT-only genes are PRESENT before that happens.

Usage:
    python3 translational-evidence/normalize/merge_genes.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# Alzheimer disease MONDO id -- the anchor disease whose OT row supplies the
# headline scores when a gene has one (kept in sync with score/scores.py).
OT_ANCHOR_DISEASE_ID = "MONDO_0004975"

SOURCE_GWAS = "gwas_catalog"
SOURCE_OT = "open_targets"

# GWAS-derived evidence_scores keys that we set to null on OT-only genes so it is
# explicit that those genes have NO GWAS support (rather than the keys silently
# being absent). Kept aligned with the record shape emitted by
# normalize/gwas_catalog.py::aggregate_genes.
_GWAS_NULL_SCORE_FIELDS = {
    "gwas_study_count": None,
    "gwas_association_count": None,
    "best_p_value": None,
    "best_neglog10p": None,
    "ensembl_gene_ids": [],
    "example_variants": [],
}

# Headline OT score fields we surface (subset kept explicit; any extras present
# on the OT scores dict are also carried through unchanged).
_OT_CORE_SCORE_FIELDS = (
    "overall",
    "genetic_association",
    "literature",
    "clinical",
    "affected_pathway",
    "rna_expression",
)


def build_ot_index(target_evidence):
    """Index Open Targets rows by Ensembl gene_id and by symbol.

    Returns four dicts:
      - scores_by_gene_id / scores_by_symbol: key -> headline OT scores dict,
        preferring the Alzheimer anchor-disease row over the first-seen row.
      - groups_by_gene_id / groups_by_symbol: key -> sorted-unique list of the OT
        disease_group values across ALL of that gene's disease rows.
      - meta_by_gene_id: gene_id -> {"symbol", "approved_name"} for building
        OT-only records.
    """
    scores_by_gene_id = {}
    scores_by_symbol = {}
    anchor_gene_id = set()   # keys whose stored scores came from the anchor row
    anchor_symbol = set()
    groups_by_gene_id = {}
    groups_by_symbol = {}
    meta_by_gene_id = {}

    def _consider(store, anchor_set, key, scores, is_anchor):
        if not key:
            return
        if key not in store:
            store[key] = scores
            if is_anchor:
                anchor_set.add(key)
            return
        # Only an anchor row may upgrade a previously non-anchor pick; two
        # non-anchor rows never clobber each other (first-seen wins).
        if is_anchor and key not in anchor_set:
            store[key] = scores
            anchor_set.add(key)

    def _record_group(store, key, group):
        if not key or group is None:
            return
        store.setdefault(key, set()).add(group)

    for rec in target_evidence:
        gene_id = rec.get("gene_id") or rec.get("target_id")
        symbol = rec.get("target_label")
        scores = rec.get("scores") or {}
        group = rec.get("disease_group")
        is_anchor = rec.get("disease_id") == OT_ANCHOR_DISEASE_ID

        _consider(scores_by_gene_id, anchor_gene_id, gene_id, scores, is_anchor)
        _consider(scores_by_symbol, anchor_symbol, symbol, scores, is_anchor)

        _record_group(groups_by_gene_id, gene_id, group)
        _record_group(groups_by_symbol, symbol, group)

        if gene_id and gene_id not in meta_by_gene_id:
            meta_by_gene_id[gene_id] = {
                "symbol": symbol,
                "approved_name": rec.get("approved_name"),
            }

    groups_by_gene_id = {k: sorted(v) for k, v in groups_by_gene_id.items()}
    groups_by_symbol = {k: sorted(v) for k, v in groups_by_symbol.items()}
    return (
        scores_by_gene_id,
        scores_by_symbol,
        groups_by_gene_id,
        groups_by_symbol,
        meta_by_gene_id,
    )


def _ot_score_block(scores):
    """Project an OT scores dict into the evidence_scores.open_targets block.

    Keeps the core datatypes explicit (null if absent) and carries any extra
    datatype scores (e.g. genetic_literature, animal_model) through unchanged so
    nothing is silently dropped.
    """
    block = {}
    for field in _OT_CORE_SCORE_FIELDS:
        block[field] = scores.get(field)
    for key, val in scores.items():
        if key not in block:
            block[key] = val
    return block


def _merge_disease_groups(existing, ot_groups):
    """Return sorted-unique union of an existing disease_groups list + OT groups."""
    merged = set()
    if isinstance(existing, list):
        merged.update(g for g in existing if isinstance(g, str))
    elif isinstance(existing, str):
        merged.add(existing)
    merged.update(g for g in (ot_groups or []) if isinstance(g, str))
    return sorted(merged)


def merge(genes, target_evidence):
    """Union GWAS genes with Open Targets targets; return the merged records.

    Returns (records, stats) where stats has the before/after counts and the
    source breakdown the task asks for.
    """
    (
        scores_by_gene_id,
        scores_by_symbol,
        groups_by_gene_id,
        groups_by_symbol,
        meta_by_gene_id,
    ) = build_ot_index(target_evidence)

    merged = []
    consumed_ot_gene_ids = set()  # OT genes folded into an existing GWAS record

    n_both = 0
    for rec in genes:
        gene_id = rec.get("gene_id")
        symbol = rec.get("symbol")

        # Match OT by Ensembl gene_id first, then by symbol (scores.py priority).
        ot_scores = None
        ot_groups = []
        matched_ot_gene_id = None
        if gene_id and gene_id in scores_by_gene_id:
            ot_scores = scores_by_gene_id[gene_id]
            ot_groups = groups_by_gene_id.get(gene_id, [])
            matched_ot_gene_id = gene_id
        elif symbol and symbol in scores_by_symbol:
            ot_scores = scores_by_symbol[symbol]
            ot_groups = groups_by_symbol.get(symbol, [])
            # A symbol match still corresponds to a real OT Ensembl gene; find
            # it so we don't also emit it as an OT-only record.
            for tid, meta in meta_by_gene_id.items():
                if meta.get("symbol") == symbol:
                    matched_ot_gene_id = tid
                    break

        sources = [SOURCE_GWAS]
        if ot_scores is not None:
            sources.append(SOURCE_OT)
            n_both += 1
            if matched_ot_gene_id:
                consumed_ot_gene_ids.add(matched_ot_gene_id)

        # Merge OT disease groups into the existing (GWAS) disease_groups list.
        rec["disease_groups"] = _merge_disease_groups(
            rec.get("disease_groups"), ot_groups
        )

        # Attach the OT headline scores block (null-safe) without disturbing the
        # existing gwas_* evidence_scores fields.
        es = rec.get("evidence_scores")
        if not isinstance(es, dict):
            es = {}
            rec["evidence_scores"] = es
        es["open_targets"] = (
            _ot_score_block(ot_scores) if ot_scores is not None else None
        )

        rec["sources"] = sources
        merged.append(rec)

    # OT-only genes: every OT Ensembl gene not already folded into a GWAS record.
    n_ot_only = 0
    for gene_id, meta in meta_by_gene_id.items():
        if gene_id in consumed_ot_gene_ids:
            continue
        symbol = meta.get("symbol")
        if not gene_id or not symbol:
            # Cannot satisfy the schema's required gene_id + symbol; skip rather
            # than fabricate an identifier.
            continue
        ot_scores = scores_by_gene_id.get(gene_id) or {}
        ot_groups = groups_by_gene_id.get(gene_id, [])

        evidence_scores = dict(_GWAS_NULL_SCORE_FIELDS)
        evidence_scores["open_targets"] = _ot_score_block(ot_scores)

        record = {
            "gene_id": gene_id,
            "symbol": symbol,
            "name": meta.get("approved_name"),
            "entrez_ids": [],
            "aliases": [],
            "disease_groups": _merge_disease_groups(None, ot_groups),
            "evidence_scores": evidence_scores,
            "sources": [SOURCE_OT],
        }
        merged.append(record)
        n_ot_only += 1

    # Stable ordering: strongest GWAS signal first (largest -log10 p), then
    # symbol. OT-only genes (best_neglog10p is None) sort after GWAS-supported
    # ones. Matches normalize/gwas_catalog.py's sort so diffs stay readable.
    def _sort_key(rec):
        nl = (rec.get("evidence_scores") or {}).get("best_neglog10p")
        return (-(nl if nl is not None else -1.0), rec.get("symbol") or "")

    merged.sort(key=_sort_key)

    n_gwas_only = sum(1 for r in merged if r.get("sources") == [SOURCE_GWAS])
    stats = {
        "before": len(genes),
        "after": len(merged),
        "ot_rows": len(target_evidence),
        "ot_distinct_genes": len(meta_by_gene_id),
        "both": n_both,
        "gwas_only": n_gwas_only,
        "ot_only": n_ot_only,
    }
    return merged, stats


def main():
    genes_path = common.PROCESSED_DIR / "genes.jsonl"
    te_path = common.PROCESSED_DIR / "target_evidence.jsonl"

    if not genes_path.exists():
        raise SystemExit(
            "ERROR: %s not found (run normalize/gwas_catalog.py first)."
            % genes_path
        )
    if not te_path.exists():
        raise SystemExit(
            "ERROR: %s not found (run normalize/open_targets.py first)."
            % te_path
        )

    genes = common.read_jsonl(genes_path)
    target_evidence = common.read_jsonl(te_path)
    common.log(
        "loaded %d GWAS-derived genes, %d target_evidence rows"
        % (len(genes), len(target_evidence))
    )

    merged, stats = merge(genes, target_evidence)

    if stats["after"] == 0:
        raise SystemExit("ERROR: merge produced 0 genes; aborting (no overwrite).")
    if stats["after"] < stats["before"]:
        raise SystemExit(
            "ERROR: merge shrank the gene list (%d -> %d); aborting."
            % (stats["before"], stats["after"])
        )

    n_written = common.write_jsonl(genes_path, merged)

    common.log(
        "genes %d -> %d (both=%d gwas_only=%d ot_only=%d) -> %s"
        % (
            stats["before"],
            stats["after"],
            stats["both"],
            stats["gwas_only"],
            stats["ot_only"],
            genes_path,
        )
    )
    print(
        "OK: genes %d -> %d (both=%d, gwas_only=%d, ot_only=%d) -> %s"
        % (
            stats["before"],
            n_written,
            stats["both"],
            stats["gwas_only"],
            stats["ot_only"],
            genes_path,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
