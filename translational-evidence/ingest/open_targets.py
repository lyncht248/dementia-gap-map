"""Ingest Open Targets associated targets for Alzheimer + related dementias.

Broadened from Alzheimer-only to Alzheimer disease and related dementias
(ADRD). For a fixed set of disease *seeds* the disease id is resolved via the
Open Targets ``search`` query (Alzheimer disease keeps its known id
``MONDO_0004975`` without a lookup), and for EACH resolved disease id the top
300 associated targets are fetched (pages index 0,1,2 of size 100) from the
Open Targets Platform GraphQL API using ``common.post_json`` (stdlib only).

Caching (all under ``data/raw/translational-evidence/``, date-stamped):
  - search responses:  open_targets_search_{slug}_{stamp}.json
  - per-disease pages: open_targets_{diseaseId}_{stamp}_page_{i}.json
  - Alzheimer combined (kept for provenance):
        open_targets_alzheimer_targets_{stamp}.json
  - ADRD combined (list, one entry per disease):
        open_targets_adrd_targets_{stamp}.json

Existing Alzheimer cache is reused; only the NEW diseases are fetched.

Run:

    python3 translational-evidence/ingest/open_targets.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


API_URL = "https://api.platform.opentargets.org/api/v4/graphql"
ALZHEIMER_ID = "MONDO_0004975"  # Alzheimer disease (known; no lookup needed)
PAGE_SIZE = 100
PAGE_INDICES = (0, 1, 2)  # top 300 targets

# Seeds to resolve/ingest. Each is (search_query, known_id_or_None). Alzheimer
# uses its known id directly; the related dementias are resolved by search.
DISEASE_SEEDS = (
    ("Alzheimer disease", ALZHEIMER_ID),
    ("dementia", None),
    ("vascular dementia", None),
    ("frontotemporal dementia", None),
    ("Lewy body dementia", None),
)

SEARCH_QUERY = (
    "query($q:String!){"
    " search(queryString:$q, entityNames:[\"disease\"],"
    " page:{index:0,size:5}){ hits{ id name entity } } }"
)

TARGETS_QUERY = (
    "query($efoId:String!,$index:Int!){"
    " disease(efoId:$efoId){"
    " id name"
    " associatedTargets(page:{index:$index,size:100}){"
    " count"
    " rows{ target{ id approvedSymbol approvedName } score"
    " datatypeScores{ id score } } } } }"
)


def _search_disease(query, stamp):
    """Resolve a disease query to its best-matching disease hit id/name.

    Picks the first disease-entity hit (Open Targets ranks hits by relevance).
    Caches the raw search response for provenance and returns (id, name).
    """
    cache_path = common.RAW_DIR / (
        "open_targets_search_%s_%s.json" % (common.slug(query), stamp)
    )
    payload = {"query": SEARCH_QUERY, "variables": {"q": query}}
    common.log("resolving disease id for query=%r" % query)
    data = common.post_json(API_URL, payload, cache_path=cache_path)

    hits = ((data.get("data") or {}).get("search") or {}).get("hits") or []
    disease_hits = [h for h in hits if h.get("entity") == "disease" and h.get("id")]
    if not disease_hits:
        raise RuntimeError(
            "Open Targets search returned no disease hit for query=%r. "
            "Full response: %r" % (query, data)
        )
    best = disease_hits[0]
    chosen_id = best.get("id")
    chosen_name = best.get("name")
    common.log("  -> chose %s (%s) for query=%r"
               % (chosen_id, chosen_name, query))
    return chosen_id, chosen_name


def _resolve_disease_ids(stamp):
    """Resolve all seeds to disease records, de-duplicating by id.

    Returns an ordered list of {"id", "name", "query"} dicts. Alzheimer uses
    its known id (no search). Later duplicate ids (e.g. a subtype query that
    resolves to an already-seen id) are skipped, keeping the first occurrence.
    """
    resolved = []
    seen_ids = set()
    for query, known_id in DISEASE_SEEDS:
        if known_id is not None:
            disease_id, disease_name = known_id, None
            # Still record a stable human name for provenance; the targets
            # fetch below fills disease_name from the disease() response.
        else:
            disease_id, disease_name = _search_disease(query, stamp)
        if disease_id in seen_ids:
            common.log("skipping duplicate disease id %s (query=%r)"
                       % (disease_id, query))
            continue
        seen_ids.add(disease_id)
        resolved.append({"id": disease_id, "name": disease_name, "query": query})
    return resolved


def _fetch_page(disease_id, index, stamp):
    """POST one page of associated targets; return the parsed disease dict."""
    cache_path = common.RAW_DIR / (
        "open_targets_%s_%s_page_%d.json" % (disease_id, stamp, index)
    )
    payload = {
        "query": TARGETS_QUERY,
        "variables": {"efoId": disease_id, "index": index},
    }
    common.log("fetching Open Targets %s page index=%d (size=%d)"
               % (disease_id, index, PAGE_SIZE))
    data = common.post_json(API_URL, payload, cache_path=cache_path)

    disease = (data.get("data") or {}).get("disease")
    if disease is None:
        raise RuntimeError(
            "Open Targets returned no disease for efoId=%s (page %d). "
            "Full response: %r" % (disease_id, index, data)
        )
    return disease


def _fetch_disease_targets(disease_id, stamp):
    """Fetch the top 300 associated targets for one disease id.

    Returns (resolved_id, resolved_name, total_count, rows).
    """
    resolved_id = None
    resolved_name = None
    total_count = None
    rows = []

    for index in PAGE_INDICES:
        disease = _fetch_page(disease_id, index, stamp)
        if resolved_id is None:
            resolved_id = disease.get("id") or disease_id
            resolved_name = disease.get("name")

        assoc = disease.get("associatedTargets") or {}
        if total_count is None:
            total_count = assoc.get("count")

        page_rows = assoc.get("rows") or []
        common.log("  %s page index=%d -> %d rows (total available=%s)"
                   % (disease_id, index, len(page_rows), total_count))
        rows.extend(page_rows)

    return resolved_id, resolved_name, total_count, rows


def main():
    stamp = common.today_stamp()
    common.RAW_DIR.mkdir(parents=True, exist_ok=True)

    diseases = _resolve_disease_ids(stamp)
    common.log("resolved %d disease id(s):" % len(diseases))
    for d in diseases:
        common.log("  %s  (query=%r, search_name=%s)"
                   % (d["id"], d["query"], d["name"]))

    combined_list = []
    alzheimer_combined = None

    for d in diseases:
        disease_id = d["id"]
        resolved_id, resolved_name, total_count, rows = _fetch_disease_targets(
            disease_id, stamp
        )
        entry = {
            "disease_id": resolved_id,
            "disease_name": resolved_name,
            "query": d["query"],
            "efoId": resolved_id,
            "total_count": total_count,
            "page_size": PAGE_SIZE,
            "page_indices": list(PAGE_INDICES),
            "row_count": len(rows),
            "rows": rows,
        }
        combined_list.append(entry)
        common.log("disease %s (%s): %d rows"
                   % (resolved_id, resolved_name, len(rows)))

        if resolved_id == ALZHEIMER_ID:
            # Preserve the original single-disease combined shape for AD.
            alzheimer_combined = {
                "disease": {"id": resolved_id, "name": resolved_name},
                "efoId": ALZHEIMER_ID,
                "total_count": total_count,
                "page_size": PAGE_SIZE,
                "page_indices": list(PAGE_INDICES),
                "row_count": len(rows),
                "rows": rows,
            }

    # Write / refresh the Alzheimer-only combined cache (backwards compatible).
    if alzheimer_combined is not None:
        alz_path = common.RAW_DIR / (
            "open_targets_alzheimer_targets_%s.json" % stamp
        )
        common._write_cache(alz_path, alzheimer_combined)
        common.log("wrote Alzheimer combined cache: %s (%d rows)"
                   % (alz_path, alzheimer_combined["row_count"]))

    # Write the ADRD combined cache (list, one entry per disease).
    adrd_path = common.RAW_DIR / (
        "open_targets_adrd_targets_%s.json" % stamp
    )
    common._write_cache(adrd_path, combined_list)
    total_rows = sum(e["row_count"] for e in combined_list)
    common.log("wrote ADRD combined cache: %s (%d diseases, %d total rows)"
               % (adrd_path, len(combined_list), total_rows))
    print(str(adrd_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
