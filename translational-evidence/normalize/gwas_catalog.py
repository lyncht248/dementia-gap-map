#!/usr/bin/env python3
"""Normalize cached GWAS Catalog ADRD data into schema-conformant JSONL.

Track B (translational evidence). Standard-library only.

Broadened from Alzheimer-only to Alzheimer + related dementias (ADRD): every
association record is tagged with a controlled ``disease_group`` and every gene
record with the sorted-unique ``disease_groups`` it spans.

Reads the combined ingest outputs (preferring the broadened ADRD files, falling
back to the legacy Alzheimer-only files if the ADRD ones are absent):
  - RAW_DIR/gwas_catalog_adrd_studies_{stamp}.json         (list of study dicts)
  - RAW_DIR/gwas_catalog_adrd_associations_{stamp}.jsonl
      (each line {"accessionId":..., "queryTrait":..., "association": <assoc>})

Produces:
  a) PROCESSED_DIR/gwas_associations.jsonl  (gwas_association.schema.json)
       + "disease_group": classify_disease_group(study diseaseTrait.trait),
         falling back to the queryTrait when the granular trait is empty.
  b) PROCESSED_DIR/genes.jsonl              (gene.schema.json)
       + "disease_groups": sorted-unique disease_group across all associations
         reporting that gene.

Score-phase normalized numbers are NOT computed here: only the raw component
inputs are stored so every downstream score stays fully explainable.

Usage:
    python3 translational-evidence/normalize/gwas_catalog.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)

import json  # noqa: E402


# Gene "names" that mean "not reported" and must be excluded.
_EXCLUDED_GENE_NAMES = {"nr", "intergenic", ""}


def _latest_stamped_file(prefix, suffix):
    """Return the newest RAW_DIR file matching prefix*suffix, or None."""
    candidates = sorted(common.RAW_DIR.glob(prefix + "*" + suffix))
    return candidates[-1] if candidates else None


def _extract_id_list(raw_list, inner_key):
    """Flatten [{inner_key: value}, ...] (or [value, ...]) into [str, ...].

    Tolerant of the two shapes the API can return: a list of dicts keyed by
    ``inner_key`` (the observed shape) or a bare list of scalars. Empty / None
    entries are dropped.
    """
    out = []
    for item in raw_list or []:
        value = None
        if isinstance(item, dict):
            value = item.get(inner_key)
        else:
            value = item
        if value is None:
            continue
        value = str(value).strip()
        if value:
            out.append(value)
    return out


def parse_risk_allele_name(name):
    """Parse a riskAlleleName like 'rs698842-A' into (rsid, risk_allele).

    - Splits on the LAST '-' so hyphenated allele descriptors are handled.
    - rsid is the part before; risk_allele the part after.
    - '?' (unknown allele) becomes None.
    - Missing/blank input yields (None, None); a name with no '-' is treated as
      an rsid with an unknown allele.
    """
    if not name:
        return None, None
    name = str(name).strip()
    if not name:
        return None, None
    if "-" in name:
        rsid, allele = name.rsplit("-", 1)
        rsid = rsid.strip() or None
        allele = allele.strip()
        if allele in ("", "?"):
            allele = None
        return rsid, allele
    # No delimiter: whole token is the variant id, allele unknown.
    return (name if name != "?" else None), None


def _primary_locus(association):
    """Return the first locus dict of an association, or {}."""
    loci = association.get("loci") or []
    return loci[0] if loci else {}


def _reported_genes_from_locus(locus):
    """Return list of (symbol, ensembl_ids, entrez_ids) for a locus.

    Excludes not-reported gene names (NR / intergenic / blank).
    """
    genes = []
    for g in locus.get("authorReportedGenes") or []:
        symbol = (g.get("geneName") or "").strip()
        if symbol.lower() in _EXCLUDED_GENE_NAMES:
            continue
        ensembl_ids = _extract_id_list(g.get("ensemblGeneIds"), "ensemblGeneId")
        entrez_ids = _extract_id_list(g.get("entrezGeneIds"), "entrezGeneId")
        genes.append((symbol, ensembl_ids, entrez_ids))
    return genes


def _compute_p_value(association):
    """Return the association p-value as a float, or None.

    Prefers the explicit ``pvalue`` field; otherwise reconstructs it from
    mantissa * 10**exponent when both are present.
    """
    pv = association.get("pvalue")
    if pv is not None:
        try:
            return float(pv)
        except (TypeError, ValueError):
            pass
    mant = association.get("pvalueMantissa")
    exp = association.get("pvalueExponent")
    if mant is not None and exp is not None:
        try:
            return float(mant) * (10 ** int(exp))
        except (TypeError, ValueError):
            return None
    return None


def _num_or_none(value):
    """Coerce to float, or None if absent/non-numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _study_publication(study):
    """Return (pmid, publication_dict) from a study dict."""
    info = study.get("publicationInfo") or {}
    pmid = info.get("pubmedId")
    pmid = str(pmid) if pmid is not None else None
    publication = {
        "title": (info.get("title") or None),
        "journal": (info.get("publication") or None),
        "date": (info.get("publicationDate") or None),
    }
    # Trim whitespace on the title (API sometimes has trailing spaces).
    if publication["title"]:
        publication["title"] = publication["title"].strip() or None
    return pmid, publication


def normalize_associations(assoc_lines, studies_by_acc):
    """Build the list of gwas_association records with unique association_ids."""
    records = []
    seen_ids = {}  # base_id -> count, to disambiguate duplicates

    for line in assoc_lines:
        accession = line.get("accessionId")
        association = line.get("association") or {}
        query_trait = line.get("queryTrait")
        study = studies_by_acc.get(accession, {})

        granular_trait = ((study.get("diseaseTrait") or {}).get("trait")) or None
        trait = granular_trait
        pmid, publication = _study_publication(study)

        # disease_group is derived from the granular study trait; when that is
        # empty we fall back to the EFO trait we queried on for this study.
        disease_group = common.classify_disease_group(
            granular_trait or query_trait
        )

        locus = _primary_locus(association)

        # Variant from the first strongest risk allele of the primary locus.
        rsid = None
        risk_allele = None
        strongest = locus.get("strongestRiskAlleles") or []
        if strongest:
            rsid, risk_allele = parse_risk_allele_name(
                strongest[0].get("riskAlleleName")
            )

        gene_tuples = _reported_genes_from_locus(locus)
        reported_symbols = [sym for sym, _, _ in gene_tuples if sym]
        ensembl_ids = []
        entrez_ids = []
        for _, ens, ent in gene_tuples:
            ensembl_ids.extend(ens)
            entrez_ids.extend(ent)
        # Preserve order while de-duplicating.
        ensembl_ids = list(dict.fromkeys(ensembl_ids))
        entrez_ids = list(dict.fromkeys(entrez_ids))

        p_value = _compute_p_value(association)

        effect = {
            "odds_ratio": _num_or_none(association.get("orPerCopyNum")),
            "beta": _num_or_none(association.get("betaNum")),
            "direction": (association.get("betaDirection") or None),
        }

        # trait is a required, non-null field per schema. If a study is missing
        # its granular trait, fall back to the EFO trait we queried on (and only
        # then to a generic label so the record still validates).
        if not trait:
            trait = query_trait or "dementia"

        gene_part = reported_symbols[0] if reported_symbols else "NA"
        base_id = "%s:%s:%s" % (accession, (rsid or "NA"), gene_part)
        n_seen = seen_ids.get(base_id, 0)
        seen_ids[base_id] = n_seen + 1
        association_id = base_id if n_seen == 0 else "%s#%d" % (base_id, n_seen)

        record = {
            "association_id": association_id,
            "study_accession": accession,
            "trait": trait,
            "disease_group": disease_group,
            "pmid": pmid,
            "publication": publication,
            "variant": {
                "rsid": rsid,
                "risk_allele": risk_allele,
                "chromosome": None,
                "position": None,
            },
            "reported_genes": reported_symbols,
            "p_value": p_value,
            "effect": effect,
            # Extras retained for downstream use (schema allows additional props).
            "risk_frequency": (association.get("riskFrequency") or None),
            "snp_type": (association.get("snpType") or None),
            "ensembl_gene_ids": ensembl_ids,
            "entrez_gene_ids": entrez_ids,
        }
        records.append(record)

    return records


def aggregate_genes(assoc_records, assoc_lines, studies_by_acc):
    """Aggregate one gene record per unique gene_id across all associations.

    Iterates the raw association lines so we can associate each gene with its
    own ensembl/entrez ids, study accession, p-value and rsid regardless of how
    many genes share a locus.
    """
    # gene_key -> aggregation dict. We key by pick_gene_id output.
    genes = {}

    for line in assoc_lines:
        accession = line.get("accessionId")
        association = line.get("association") or {}
        query_trait = line.get("queryTrait")
        study = studies_by_acc.get(accession, {})
        locus = _primary_locus(association)
        p_value = _compute_p_value(association)

        # disease_group for this association (same derivation as the assoc
        # records): granular study trait, falling back to the queried EFO trait.
        granular_trait = ((study.get("diseaseTrait") or {}).get("trait")) or None
        disease_group = common.classify_disease_group(
            granular_trait or query_trait
        )

        # rsid for example_variants
        rsid = None
        strongest = locus.get("strongestRiskAlleles") or []
        if strongest:
            rsid, _ = parse_risk_allele_name(strongest[0].get("riskAlleleName"))

        for symbol, ensembl_ids, entrez_ids in _reported_genes_from_locus(locus):
            if not symbol:
                continue
            gene_id = common.pick_gene_id(ensembl_ids, entrez_ids, symbol)
            if not gene_id:
                continue

            g = genes.get(gene_id)
            if g is None:
                g = {
                    "gene_id": gene_id,
                    "symbol": symbol,
                    "ensembl_ids": [],   # ordered-unique
                    "entrez_ids": [],    # ordered-unique
                    "accessions": set(),
                    "association_count": 0,
                    "best_p_value": None,
                    "example_variants": [],  # ordered-unique, capped later
                    "disease_groups": set(),
                }
                genes[gene_id] = g

            for eid in ensembl_ids:
                if eid not in g["ensembl_ids"]:
                    g["ensembl_ids"].append(eid)
            for eid in entrez_ids:
                if eid not in g["entrez_ids"]:
                    g["entrez_ids"].append(eid)

            g["accessions"].add(accession)
            g["association_count"] += 1
            # disease_group may be None when no MeSH Dementia label matched the
            # trait; the gene.disease_groups array is string-only (schema), so
            # skip nulls here.
            if disease_group is not None:
                g["disease_groups"].add(disease_group)

            if p_value is not None:
                if g["best_p_value"] is None or p_value < g["best_p_value"]:
                    g["best_p_value"] = p_value

            if rsid and rsid not in g["example_variants"]:
                g["example_variants"].append(rsid)

    # Materialize into schema-conformant records.
    records = []
    for gene_id, g in genes.items():
        best_p = g["best_p_value"]
        record = {
            "gene_id": gene_id,
            "symbol": g["symbol"],
            "name": None,
            "entrez_ids": list(g["entrez_ids"]),
            "aliases": [],
            "disease_groups": sorted(g["disease_groups"]),
            "evidence_scores": {
                "gwas_study_count": len(g["accessions"]),
                "gwas_association_count": g["association_count"],
                "best_p_value": best_p,
                "best_neglog10p": common.neglog10(best_p),
                "ensembl_gene_ids": list(g["ensembl_ids"]),
                "example_variants": g["example_variants"][:5],
            },
        }
        records.append(record)

    # Stable ordering: strongest signal first (largest -log10 p), then symbol.
    def _sort_key(rec):
        nl = rec["evidence_scores"]["best_neglog10p"]
        return (-(nl if nl is not None else -1.0), rec["symbol"])

    records.sort(key=_sort_key)
    return records


def main():
    # Prefer the broadened ADRD ingest outputs; fall back to the legacy
    # Alzheimer-only combined files if the ADRD ones are absent.
    studies_path = _latest_stamped_file(
        "gwas_catalog_adrd_studies_", ".json"
    )
    assoc_path = _latest_stamped_file(
        "gwas_catalog_adrd_associations_", ".jsonl"
    )
    if studies_path is None or assoc_path is None:
        common.log(
            "ADRD ingest outputs not found; falling back to Alzheimer-only files"
        )
        studies_path = _latest_stamped_file(
            "gwas_catalog_alzheimer_studies_", ".json"
        )
        assoc_path = _latest_stamped_file(
            "gwas_catalog_alzheimer_associations_", ".jsonl"
        )
    if studies_path is None or assoc_path is None:
        raise SystemExit(
            "ERROR: missing ingest outputs in %s "
            "(run ingest/gwas_catalog.py first). studies=%s associations=%s"
            % (common.RAW_DIR, studies_path, assoc_path)
        )
    common.log("reading studies from %s" % studies_path)
    common.log("reading associations from %s" % assoc_path)

    with studies_path.open("r", encoding="utf-8") as fh:
        studies = json.load(fh)
    studies_by_acc = {
        s.get("accessionId"): s for s in studies if s.get("accessionId")
    }

    assoc_lines = common.read_jsonl(assoc_path)
    common.log(
        "loaded %d studies, %d association lines"
        % (len(studies), len(assoc_lines))
    )

    assoc_records = normalize_associations(assoc_lines, studies_by_acc)
    gene_records = aggregate_genes(assoc_records, assoc_lines, studies_by_acc)

    assoc_out = common.PROCESSED_DIR / "gwas_associations.jsonl"
    genes_out = common.PROCESSED_DIR / "genes.jsonl"

    n_assoc = common.write_jsonl(assoc_out, assoc_records)
    n_genes = common.write_jsonl(genes_out, gene_records)

    common.log("wrote %d associations -> %s" % (n_assoc, assoc_out))
    common.log("wrote %d genes -> %s" % (n_genes, genes_out))

    if n_assoc == 0 or n_genes == 0:
        raise SystemExit(
            "ERROR: normalization produced 0 records "
            "(associations=%d genes=%d); aborting." % (n_assoc, n_genes)
        )

    print(
        "OK: %d associations, %d genes -> %s , %s"
        % (n_assoc, n_genes, assoc_out, genes_out)
    )


if __name__ == "__main__":
    main()
