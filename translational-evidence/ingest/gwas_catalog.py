#!/usr/bin/env python3
"""Ingest GWAS Catalog studies + associations for ADRD (Alzheimer + related).

Track B (translational evidence). Standard-library only.

This BROADENS the original Alzheimer-only ingest to the wider set of Alzheimer
disease and related dementias (ADRD). Instead of a single EFO trait it iterates
a LIST of EFO trait labels, querying findByEfoTrait (exact-match on the EFO
label) for each.

Pipeline:
  1. For each EFO trait in EFO_TRAITS, page all studies (size=100), caching each
     page as gwas_catalog_studies_{traitSlug}_{stamp}_page_{P:03d}.json.
     Some trait labels legitimately return 0 studies / 404 -> log and continue.
  2. Union all studies across traits and DEDUP by accessionId (recording the
     trait we queried on, so downstream can fall back to it).
  3. For each unique accession, fetch its associations REUSING the existing
     per-accession cache dir gwas_catalog_associations/{acc}.json (Alzheimer
     accessions are already cached; only new accessions are fetched).
  4. Write a combined JSONL gwas_catalog_adrd_associations_{stamp}.jsonl where
     each line is {"accessionId","queryTrait","association"}.

The original Alzheimer-only combined files are ALSO (re)written so the legacy
path keeps working.

Raw responses are cached under RAW_DIR with date-stamped, deterministic names,
and reused on re-run unless TE_REFRESH=1 is set (handled by common.get_json).

Usage:
    python3 translational-evidence/ingest/gwas_catalog.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)

import json  # noqa: E402


STUDIES_URL = "https://www.ebi.ac.uk/gwas/rest/api/studies/search/findByEfoTrait"
ASSOCIATIONS_URL_TMPL = (
    "https://www.ebi.ac.uk/gwas/rest/api/studies/{accession}/associations"
)

# The original Alzheimer-only trait; kept first so its combined legacy files and
# per-page cache names (gwas_catalog_studies_alzheimer_disease_...) are stable.
EFO_TRAIT = "Alzheimer disease"

# Broadened ADRD trait list. Exact-match EFO labels; some may return 0/404.
EFO_TRAITS = [
    "Alzheimer disease",
    "dementia",
    "vascular dementia",
    "frontotemporal dementia",
    "Lewy body dementia",
    "dementia with Lewy bodies",
    "Parkinson's disease dementia",
]

PAGE_SIZE = 100


def fetch_studies_for_trait(efo_trait, stamp):
    """Page through all studies for one EFO trait, caching each page.

    Returns a flat list of study dicts (possibly empty). Each page is cached to
    RAW_DIR/gwas_catalog_studies_{traitSlug}_{stamp}_page_{P:03d}.json.

    A 404 (or any final HTTP failure) is caught and logged, returning [] so the
    caller can continue with the remaining traits: findByEfoTrait is exact-match
    on the EFO label and some labels legitimately have no studies.
    """
    trait_slug = common.slug(efo_trait)
    studies = []
    page = 0
    total_pages = None

    while True:
        cache_path = (
            common.RAW_DIR
            / (
                "gwas_catalog_studies_%s_%s_page_%03d.json"
                % (trait_slug, stamp, page)
            )
        )
        try:
            data = common.get_json(
                STUDIES_URL,
                params={
                    "efoTrait": efo_trait,
                    "page": page,
                    "size": PAGE_SIZE,
                },
                cache_path=cache_path,
            )
        except RuntimeError as err:
            # Exact-match label with no studies -> 404, or a transient failure
            # after retries. Report and continue with remaining traits.
            common.log(
                "trait %r page %d fetch failed (continuing): %s"
                % (efo_trait, page, err)
            )
            break

        page_info = data.get("page", {}) or {}
        if total_pages is None:
            total_pages = page_info.get("totalPages")
            total_elements = page_info.get("totalElements")
            common.log(
                "trait %r: %s total elements across %s pages (size=%d)"
                % (efo_trait, total_elements, total_pages, PAGE_SIZE)
            )

        page_studies = (data.get("_embedded", {}) or {}).get("studies", []) or []
        studies.extend(page_studies)
        common.log(
            "trait %r fetched page %d (%d studies, running total %d)"
            % (efo_trait, page, len(page_studies), len(studies))
        )

        page += 1
        if total_pages is not None:
            if page >= total_pages:
                break
        elif not page_studies:
            break
        if page > 10000:
            common.log("aborting study paging at safety cap (page > 10000)")
            break

    return studies


def fetch_associations_for_study(accession):
    """Fetch and cache the associations for one study accession.

    Reuses the per-accession cache dir gwas_catalog_associations/{acc}.json, so
    accessions cached by the original Alzheimer-only run are NOT refetched.
    Returns the list of association dicts (possibly empty).
    """
    cache_path = (
        common.RAW_DIR
        / "gwas_catalog_associations"
        / ("%s.json" % accession)
    )
    data = common.get_json(
        ASSOCIATIONS_URL_TMPL.format(accession=accession),
        cache_path=cache_path,
    )
    assocs = (data.get("_embedded", {}) or {}).get("associations", []) or []
    return assocs


def _write_combined_studies(path, studies):
    """Pretty-write a list of study dicts to a JSON file."""
    with path.open("w", encoding="utf-8") as fh:
        json.dump(studies, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _write_combined_associations(path, studies, stamp,
                                 query_trait_by_acc, include_query_trait):
    """Fetch (cached) associations for each study and write a combined JSONL.

    Each line is {"accessionId", "association"} and, when
    ``include_query_trait`` is True, additionally {"queryTrait"}.

    Returns (n_written, n_studies_with_zero).
    """
    n_studies = len(studies)
    n_assoc_records = 0
    n_studies_with_zero = 0
    assoc_lines = []

    for idx, study in enumerate(studies, start=1):
        accession = study.get("accessionId")
        if not accession:
            common.log("skipping study with no accessionId at index %d" % idx)
            continue

        assocs = fetch_associations_for_study(accession)
        if not assocs:
            n_studies_with_zero += 1
        for assoc in assocs:
            line = {"accessionId": accession, "association": assoc}
            if include_query_trait:
                line["queryTrait"] = query_trait_by_acc.get(accession)
            assoc_lines.append(line)
            n_assoc_records += 1

        if idx % 25 == 0 or idx == n_studies:
            common.log(
                "associations progress: %d/%d studies, %d association records"
                % (idx, n_studies, n_assoc_records)
            )

    written = common.write_jsonl(path, assoc_lines)
    return written, n_studies_with_zero


def main():
    stamp = common.today_stamp()
    common.RAW_DIR.mkdir(parents=True, exist_ok=True)

    # --- 1 + 2: studies per trait, then union + dedup by accessionId --------
    studies_by_acc = {}          # accession -> study dict (first seen wins)
    query_trait_by_acc = {}      # accession -> first EFO trait that returned it
    per_trait_counts = []        # [(trait, n_studies_returned)]
    alzheimer_studies = []       # for the legacy Alzheimer-only combined file

    for efo_trait in EFO_TRAITS:
        trait_studies = fetch_studies_for_trait(efo_trait, stamp)
        per_trait_counts.append((efo_trait, len(trait_studies)))
        if efo_trait == EFO_TRAIT:
            alzheimer_studies = trait_studies
        for study in trait_studies:
            acc = study.get("accessionId")
            if not acc:
                continue
            if acc not in studies_by_acc:
                studies_by_acc[acc] = study
                query_trait_by_acc[acc] = efo_trait

    common.log("per-trait study counts:")
    for trait, n in per_trait_counts:
        common.log("  %-32s -> %d studies" % (trait, n))

    studies = list(studies_by_acc.values())
    if not studies:
        raise SystemExit(
            "ERROR: fetched 0 studies across all EFO traits %r; aborting."
            % (EFO_TRAITS,)
        )
    common.log(
        "union across %d traits: %d unique studies (by accessionId)"
        % (len(EFO_TRAITS), len(studies))
    )

    # --- Legacy Alzheimer-only combined studies file ------------------------
    alz_studies_path = (
        common.RAW_DIR / ("gwas_catalog_alzheimer_studies_%s.json" % stamp)
    )
    _write_combined_studies(alz_studies_path, alzheimer_studies)
    common.log(
        "wrote Alzheimer-only combined studies: %s (%d studies)"
        % (alz_studies_path, len(alzheimer_studies))
    )

    # --- Broadened ADRD combined studies file -------------------------------
    adrd_studies_path = (
        common.RAW_DIR / ("gwas_catalog_adrd_studies_%s.json" % stamp)
    )
    _write_combined_studies(adrd_studies_path, studies)
    common.log(
        "wrote ADRD combined studies: %s (%d unique studies)"
        % (adrd_studies_path, len(studies))
    )

    # --- 3 + 4: associations ------------------------------------------------
    # Legacy Alzheimer-only combined associations (no queryTrait key).
    alz_assoc_path = (
        common.RAW_DIR
        / ("gwas_catalog_alzheimer_associations_%s.jsonl" % stamp)
    )
    alz_written, alz_zero = _write_combined_associations(
        alz_assoc_path, alzheimer_studies, stamp,
        query_trait_by_acc, include_query_trait=False,
    )
    common.log(
        "wrote Alzheimer-only associations JSONL: %s (%d records; %d studies "
        "had zero)" % (alz_assoc_path, alz_written, alz_zero)
    )

    # Broadened ADRD combined associations (with queryTrait key).
    adrd_assoc_path = (
        common.RAW_DIR
        / ("gwas_catalog_adrd_associations_%s.jsonl" % stamp)
    )
    adrd_written, adrd_zero = _write_combined_associations(
        adrd_assoc_path, studies, stamp,
        query_trait_by_acc, include_query_trait=True,
    )
    common.log(
        "wrote ADRD associations JSONL: %s (%d records; %d studies had zero)"
        % (adrd_assoc_path, adrd_written, adrd_zero)
    )

    if adrd_written == 0:
        raise SystemExit(
            "ERROR: 0 ADRD association records across %d studies; aborting."
            % len(studies)
        )

    # --- Report -------------------------------------------------------------
    traits_with_studies = [(t, n) for t, n in per_trait_counts if n > 0]
    traits_without_studies = [t for t, n in per_trait_counts if n == 0]
    common.log("traits returning studies: %s"
               % ", ".join("%s=%d" % (t, n) for t, n in traits_with_studies))
    if traits_without_studies:
        common.log("traits returning ZERO studies: %s"
                   % ", ".join(traits_without_studies))

    print(
        "OK: %d unique studies (union of %d traits), %d ADRD association "
        "records -> %s"
        % (len(studies), len(EFO_TRAITS), adrd_written, adrd_assoc_path)
    )


if __name__ == "__main__":
    main()
