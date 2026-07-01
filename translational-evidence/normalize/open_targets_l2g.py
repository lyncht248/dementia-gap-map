"""Normalize Open Targets credible sets into functional_links.jsonl.

Reads the raw credibleSets pages cached by
``translational-evidence/ingest/open_targets_l2g.py`` (via the run manifest)
and emits functional_link records conforming to
``shared/schemas/functional_link.schema.json``.

Two kinds of records are produced per credible set:

  * L2G predictions (PRIMARY, densely populated) -- one record per
    l2GPredictions row (top 3 kept):
        link_id        = "{studyLocusId}:l2g:{ensembl}"
        gene_id        = target.id (Ensembl)
        gene_symbol    = target.approvedSymbol
        variant_or_locus = studyLocusId
        rsid           = first variant.rsIds (or null)
        cell_type      = null
        evidence_type  = "l2g_prediction"
        score          = l2g score
        source         = "open_targets_l2g"
        source_study   = study.id
        method         = "OT L2G"
        disease_group  = classify_disease_group(study.traitFromSource | condition)
        rank           = 1-based rank within the locus (extra property)

  * GWAS->QTL colocalisation (OPPORTUNISTIC, sparse for AD) -- one record per
    colocalisation row whose otherStudyLocus.studyType != "gwas":
        link_id        = "{studyLocusId}:coloc:{qtlGeneId}:{method}:{qtl_type}"
        gene_id        = otherStudyLocus.qtlGeneId (Ensembl)
        gene_symbol    = null
        variant_or_locus = studyLocusId
        rsid           = first variant.rsIds (or null)
        cell_type      = otherStudyLocus.study.biosample.biosampleName
        evidence_type  = "gwas_qtl_colocalisation"
        score          = h4
        source         = "open_targets_coloc"
        method         = colocalisationMethod
        disease_group  = classify_disease_group(study.traitFromSource | condition)
        clpp           = colocalisation clpp (extra property)
        qtl_study_type = otherStudyLocus.studyType (extra property)

Identical link_ids are de-duplicated (first occurrence wins).

Output: ``data/processed/translational-evidence/functional_links.jsonl``

Run:

    python3 translational-evidence/normalize/open_targets_l2g.py
"""

import sys
import pathlib
import json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


L2G_RAW_DIR = common.RAW_DIR / "open_targets_l2g"
L2G_SOURCE = "open_targets_l2g"
COLOC_SOURCE = "open_targets_coloc"


def _find_manifest():
    """Return the newest ingest manifest path, or None."""
    candidates = sorted(L2G_RAW_DIR.glob("manifest_*.json"))
    return candidates[-1] if candidates else None


def _load_json(path):
    with pathlib.Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _iter_credibleset_rows(manifest):
    """Yield every raw credible-set row from the cached batch/page files.

    Uses the manifest's per-batch page counts to know exactly which cached
    files to read, so normalization never re-hits the network.
    """
    stamp = manifest["stamp"]
    for batch in manifest.get("batches", []):
        batch_index = batch["batch_index"]
        for page_index in range(batch["pages"]):
            page_path = L2G_RAW_DIR / (
                "crediblesets_%s_batch_%03d_page_%03d.json"
                % (stamp, batch_index, page_index)
            )
            if not page_path.exists():
                common.log("WARNING: expected cache missing: %s" % page_path)
                continue
            data = _load_json(page_path)
            cs = (data.get("data") or {}).get("credibleSets") or {}
            for row in (cs.get("rows") or []):
                yield row


def _disease_group_for(study):
    """Classify disease_group from a credible set's study traits."""
    trait = study.get("traitFromSource") or study.get("condition")
    return common.classify_disease_group(trait)


def _first_rsid(variant):
    """Return the first rsId of a variant, or None."""
    rsids = (variant or {}).get("rsIds") or []
    for r in rsids:
        if r:
            return r
    return None


def _l2g_records(row, rsid, disease_group):
    """Emit L2G functional_link records for one credible set (top 3)."""
    out = []
    study = row.get("study") or {}
    study_id = study.get("id")
    study_locus_id = row.get("studyLocusId")
    l2g = (row.get("l2GPredictions") or {}).get("rows") or []
    for rank, pred in enumerate(l2g, start=1):
        target = pred.get("target") or {}
        ensembl = target.get("id")
        if not ensembl or not study_locus_id:
            continue
        out.append({
            "link_id": "%s:l2g:%s" % (study_locus_id, ensembl),
            "gene_id": ensembl,
            "gene_symbol": target.get("approvedSymbol"),
            "variant_or_locus": study_locus_id,
            "rsid": rsid,
            "cell_type": None,
            "evidence_type": "l2g_prediction",
            "score": pred.get("score"),
            "disease_group": disease_group,
            "source": L2G_SOURCE,
            "source_study": study_id,
            "method": "OT L2G",
            "rank": rank,
        })
    return out


def _coloc_records(row, rsid, disease_group):
    """Emit GWAS->QTL colocalisation records (non-gwas other side only)."""
    out = []
    study = row.get("study") or {}
    study_id = study.get("id")
    study_locus_id = row.get("studyLocusId")
    coloc = (row.get("colocalisation") or {}).get("rows") or []
    for c in coloc:
        other = c.get("otherStudyLocus") or {}
        qtl_type = other.get("studyType")
        # Only keep QTL colocalisations (skip GWAS-GWAS coloc).
        if qtl_type is None or qtl_type == "gwas":
            continue
        qtl_gene = other.get("qtlGeneId")
        if not qtl_gene or not study_locus_id:
            continue
        method = c.get("colocalisationMethod")
        other_study = other.get("study") or {}
        biosample = other_study.get("biosample") or {}
        out.append({
            "link_id": "%s:coloc:%s:%s:%s"
                       % (study_locus_id, qtl_gene, method, qtl_type),
            "gene_id": qtl_gene,
            "gene_symbol": None,
            "variant_or_locus": study_locus_id,
            "rsid": rsid,
            "cell_type": biosample.get("biosampleName"),
            "evidence_type": "gwas_qtl_colocalisation",
            "score": c.get("h4"),
            "disease_group": disease_group,
            "source": COLOC_SOURCE,
            "source_study": study_id,
            "method": method,
            "clpp": c.get("clpp"),
            "qtl_study_type": qtl_type,
        })
    return out


def main():
    manifest_path = _find_manifest()
    if manifest_path is None:
        raise RuntimeError(
            "No ingest manifest found under %s. Run "
            "translational-evidence/ingest/open_targets_l2g.py first."
            % L2G_RAW_DIR
        )
    common.log("reading ingest manifest: %s" % manifest_path)
    manifest = _load_json(manifest_path)

    records = []
    seen_link_ids = set()
    dropped_dup = 0
    n_credible_sets = 0
    n_l2g = 0
    n_coloc = 0

    for row in _iter_credibleset_rows(manifest):
        n_credible_sets += 1
        rsid = _first_rsid(row.get("variant"))
        disease_group = _disease_group_for(row.get("study") or {})

        for rec in _l2g_records(row, rsid, disease_group):
            if rec["link_id"] in seen_link_ids:
                dropped_dup += 1
                continue
            seen_link_ids.add(rec["link_id"])
            records.append(rec)
            n_l2g += 1

        for rec in _coloc_records(row, rsid, disease_group):
            if rec["link_id"] in seen_link_ids:
                dropped_dup += 1
                continue
            seen_link_ids.add(rec["link_id"])
            records.append(rec)
            n_coloc += 1

    out_path = common.PROCESSED_DIR / "functional_links.jsonl"
    count = common.write_jsonl(out_path, records)

    distinct_genes = len({r["gene_id"] for r in records})
    common.log("processed %d credible sets" % n_credible_sets)
    common.log("emitted %d L2G links, %d coloc links (%d duplicate link_ids "
               "dropped)" % (n_l2g, n_coloc, dropped_dup))
    common.log("distinct genes: %d" % distinct_genes)
    common.log("wrote %d functional_link records to %s" % (count, out_path))
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
