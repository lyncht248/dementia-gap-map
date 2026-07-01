"""Ingest ClinicalTrials.gov studies for Alzheimer + related dementias (ADRD).

Runs SEPARATE ``query.cond`` pulls for each ADRD condition against the
ClinicalTrials.gov v2 REST API, paging through each by following
``nextPageToken`` until it is absent. Each raw page is cached deterministically
under

    data/raw/translational-evidence/clinicaltrials_{condSlug}_page_{P:03d}.json

Studies are DEDUPED by ``nctId`` across all conditions (a trial listed under
both "Alzheimer Disease" and "Dementia" appears once), and the combined,
deduped set is written line-per-study to

    data/raw/translational-evidence/clinicaltrials_adrd_studies_{stamp}.jsonl

Standard library only (Python 3.9). Re-runs reuse the on-disk page cache unless
the ``TE_REFRESH=1`` environment variable is set (handled by
``common.get_json``). The Alzheimer pages fetched by the earlier Alzheimer-only
version are reused via the ``alzheimer_disease`` cond-slug cache.

Usage:

    python3 translational-evidence/ingest/clinicaltrials.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


API_URL = "https://clinicaltrials.gov/api/v2/studies"

# ADRD conditions to pull separately (order preserved for reporting).
CONDITIONS = [
    "Alzheimer Disease",
    "Vascular Dementia",
    "Frontotemporal Dementia",
    "Lewy Body Dementia",
    "Dementia",
]

PAGE_SIZE = 100

# Safety cap on the total number of UNIQUE studies to ingest across conditions.
STUDY_CAP = 15000
# Progress cadence (studies, per condition).
PROGRESS_EVERY = 500


def _page_cache_path(cond_slug, page_num):
    """Deterministic cache path for a single API page (1-indexed) of a cond."""
    return common.RAW_DIR / (
        "clinicaltrials_%s_page_%03d.json" % (cond_slug, page_num)
    )


def _nct_id(study):
    """Extract nctId from a raw study, or None."""
    protocol = study.get("protocolSection")
    if not isinstance(protocol, dict):
        return None
    ident = protocol.get("identificationModule")
    if not isinstance(ident, dict):
        return None
    nct = ident.get("nctId")
    if nct is None:
        return None
    nct = str(nct).strip()
    return nct or None


def fetch_condition_studies(condition, seen_ids, remaining_cap):
    """Page through one condition, returning (unique_studies, capped).

    ``seen_ids`` is a set of nctIds already collected from previous conditions;
    studies whose nctId is already in it are skipped (cross-condition dedup) but
    still counted toward that condition's raw page reads. Newly kept nctIds are
    added to ``seen_ids`` in place.

    ``remaining_cap`` is how many more unique studies may still be added before
    the global STUDY_CAP is reached; when it drops to 0 the pull stops early and
    ``capped`` is True.

    Each page is cached deterministically so re-runs are offline-reproducible.
    """
    cond_slug = common.slug(condition)
    unique_studies = []
    page_token = None
    page_num = 0
    capped = False
    next_progress = PROGRESS_EVERY

    while True:
        page_num += 1
        params = {
            "query.cond": condition,
            "pageSize": PAGE_SIZE,
            "format": "json",
        }
        if page_token:
            params["pageToken"] = page_token

        cache_path = _page_cache_path(cond_slug, page_num)
        data = common.get_json(API_URL, params=params, cache_path=cache_path)

        page_studies = data.get("studies") or []
        for study in page_studies:
            nct = _nct_id(study)
            if nct is None:
                # No stable id: keep it but do not participate in dedup.
                unique_studies.append(study)
                remaining_cap -= 1
            elif nct not in seen_ids:
                seen_ids.add(nct)
                unique_studies.append(study)
                remaining_cap -= 1
            else:
                continue  # duplicate across conditions -> skip

            if remaining_cap <= 0:
                capped = True
                common.log(
                    "CAPPED: reached global safety cap of %d unique studies "
                    "while pulling %r (page %d); stopping pagination even "
                    "though more may exist"
                    % (STUDY_CAP, condition, page_num)
                )
                break

        if len(unique_studies) >= next_progress:
            common.log(
                "  [%s] %d new-unique studies across %d pages so far"
                % (condition, len(unique_studies), page_num)
            )
            next_progress = (
                (len(unique_studies) // PROGRESS_EVERY) + 1
            ) * PROGRESS_EVERY

        if capped:
            break

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return unique_studies, capped


def fetch_all_studies():
    """Pull each ADRD condition, dedup by nctId, return (studies, per_cond, capped).

    ``per_cond`` maps each condition string to the number of NEW unique studies
    it contributed (studies already seen from an earlier condition are not
    counted). ``studies`` is the combined deduped list; ``capped`` is True if
    the global STUDY_CAP was hit.
    """
    seen_ids = set()
    all_studies = []
    per_cond = {}
    capped = False

    for condition in CONDITIONS:
        remaining_cap = STUDY_CAP - len(all_studies)
        if remaining_cap <= 0:
            capped = True
            per_cond[condition] = 0
            common.log(
                "CAPPED before pulling %r: global cap %d already reached"
                % (condition, STUDY_CAP)
            )
            continue

        common.log("pulling condition %r ..." % condition)
        cond_studies, cond_capped = fetch_condition_studies(
            condition, seen_ids, remaining_cap
        )
        per_cond[condition] = len(cond_studies)
        all_studies.extend(cond_studies)
        common.log(
            "condition %r contributed %d new-unique studies (running total %d)"
            % (condition, len(cond_studies), len(all_studies))
        )
        if cond_capped:
            capped = True
            break

    return all_studies, per_cond, capped


def main():
    common.RAW_DIR.mkdir(parents=True, exist_ok=True)

    studies, per_cond, capped = fetch_all_studies()

    if not studies:
        raise RuntimeError(
            "No studies returned from %s for conditions %r; refusing to write "
            "an empty combined file." % (API_URL, CONDITIONS)
        )

    combined_path = (
        common.RAW_DIR
        / ("clinicaltrials_adrd_studies_%s.jsonl" % common.today_stamp())
    )
    count = common.write_jsonl(combined_path, studies)

    common.log("ingest complete (capped=%s):" % capped)
    for condition in CONDITIONS:
        common.log(
            "  %-28s %d new-unique studies"
            % (condition, per_cond.get(condition, 0))
        )
    common.log("  %-28s %d unique studies (after cross-condition dedup)"
               % ("TOTAL", count))
    common.log("combined file -> %s" % combined_path)
    common.log("per-page cache dir: %s" % common.RAW_DIR)

    return 0


if __name__ == "__main__":
    sys.exit(main())
