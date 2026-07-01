"""Ingest Open Targets fine-mapped credible sets (L2G + QTL colocalisation).

This is the functional / eQTL evidence layer for Track B. It adds a
gene-to-variant "functional link" signal on top of the existing
GWAS/trials/pathways/scoring pipeline, sourced entirely from the Open Targets
Platform GraphQL API (stdlib only, via ``common.post_json`` which paces +
caches).

Pipeline (this script only does the INGEST + raw caching; normalization into
``functional_links.jsonl`` lives in
``translational-evidence/normalize/open_targets_l2g.py``):

  1. Resolve disease ids for Alzheimer + related dementias:
       - Alzheimer disease uses its known id MONDO_0004975 (no lookup).
       - "dementia", "vascular dementia", "frontotemporal dementia" and
         "Lewy body dementia" are resolved via the Open Targets ``search``
         query (first disease-entity hit). Chosen ids are logged.
  2. ``studies(diseaseIds:[...], enableIndirect:true)`` paged -> collect every
     study whose studyType == "gwas" (deduped). Raw pages are cached.
  3. ``credibleSets(studyIds:[batch of ~40], studyTypes:[gwas], size 100)``
     paged over ALL those gwas study ids, requesting the top-3 L2G predictions
     and up to 50 colocalisation rows per credible set. Every raw page is
     cached under data/raw/translational-evidence/open_targets_l2g/.

Caching (all under ``data/raw/translational-evidence/``):
  - search responses (shared with open_targets.py):
        open_targets_search_{slug}_{stamp}.json
  - studies pages:
        open_targets_l2g/studies_{stamp}_page_{i}.json
  - credibleSets pages:
        open_targets_l2g/crediblesets_{stamp}_batch_{b}_page_{p}.json
  - a manifest describing the run (chosen ids, gwas ids, page counts):
        open_targets_l2g/manifest_{stamp}.json

Re-running reuses cached responses unless TE_REFRESH=1 is set.

Run:

    python3 translational-evidence/ingest/open_targets_l2g.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


API_URL = "https://api.platform.opentargets.org/api/v4/graphql"
ALZHEIMER_ID = "MONDO_0004975"  # Alzheimer disease (known; no lookup needed)

# studies() pagination.
STUDIES_PAGE_SIZE = 500

# credibleSets() pagination: batch the gwas study ids, page each batch.
STUDY_BATCH_SIZE = 40
CREDIBLESETS_PAGE_SIZE = 100
L2G_TOP_N = 3
COLOC_SIZE = 50

# Sub-directory for this layer's raw caches.
L2G_RAW_DIR = common.RAW_DIR / "open_targets_l2g"

# Seeds to resolve/ingest. Each is (search_query, known_id_or_None). Alzheimer
# uses its known id directly; the related dementias are resolved by search.
# Mirrors translational-evidence/ingest/open_targets.py so the two OT layers
# cover the same disease scope.
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

STUDIES_QUERY = (
    "query($ids:[String!]!,$index:Int!,$size:Int!){"
    " studies(diseaseIds:$ids, enableIndirect:true,"
    " page:{index:$index,size:$size}){"
    " count"
    " rows{ id studyType traitFromSource nSamples } } }"
)

# Note: l2GPredictions/colocalisation page sizes are baked into the query text
# (GraphQL page inputs), which keeps the request identical across all calls so
# the cache is stable.
CREDIBLESETS_QUERY = (
    "query($ids:[String!]!,$index:Int!,$size:Int!){"
    " credibleSets(studyIds:$ids, studyTypes:[gwas],"
    " page:{index:$index,size:$size}){"
    " count"
    " rows{"
    " studyLocusId"
    " variant{ id rsIds }"
    " study{ id traitFromSource condition }"
    " l2GPredictions(page:{index:0,size:%d}){ count"
    " rows{ target{ id approvedSymbol } score } }"
    " colocalisation(page:{index:0,size:%d}){ count"
    " rows{ h4 clpp colocalisationMethod"
    " otherStudyLocus{ studyType qtlGeneId"
    " study{ studyType condition"
    " biosample{ biosampleId biosampleName } } } } }"
    " } } }"
) % (L2G_TOP_N, COLOC_SIZE)


# ---------------------------------------------------------------------------
# Disease id resolution (mirrors open_targets.py)
# ---------------------------------------------------------------------------

def _search_disease(query, stamp):
    """Resolve a disease query to its best-matching disease hit (id, name).

    Picks the first disease-entity hit (Open Targets ranks by relevance) and
    caches the raw search response for provenance.
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
    common.log("  -> chose %s (%s) for query=%r"
               % (best.get("id"), best.get("name"), query))
    return best.get("id"), best.get("name")


def _resolve_disease_ids(stamp):
    """Resolve all seeds to {id, name, query} dicts, de-duplicating by id."""
    resolved = []
    seen_ids = set()
    for query, known_id in DISEASE_SEEDS:
        if known_id is not None:
            disease_id, disease_name = known_id, None
        else:
            disease_id, disease_name = _search_disease(query, stamp)
        if disease_id in seen_ids:
            common.log("skipping duplicate disease id %s (query=%r)"
                       % (disease_id, query))
            continue
        seen_ids.add(disease_id)
        resolved.append({"id": disease_id, "name": disease_name, "query": query})
    return resolved


# ---------------------------------------------------------------------------
# studies(): collect all gwas study ids
# ---------------------------------------------------------------------------

def _fetch_studies_page(disease_ids, index, stamp):
    """POST one page of studies; return (count, rows)."""
    cache_path = L2G_RAW_DIR / ("studies_%s_page_%d.json" % (stamp, index))
    payload = {
        "query": STUDIES_QUERY,
        "variables": {
            "ids": disease_ids,
            "index": index,
            "size": STUDIES_PAGE_SIZE,
        },
    }
    common.log("fetching studies page index=%d (size=%d)"
               % (index, STUDIES_PAGE_SIZE))
    data = common.post_json(API_URL, payload, cache_path=cache_path)
    studies = (data.get("data") or {}).get("studies")
    if studies is None:
        raise RuntimeError(
            "Open Targets returned no studies for diseaseIds=%r (page %d). "
            "Full response: %r" % (disease_ids, index, data)
        )
    return studies.get("count"), (studies.get("rows") or [])


def _collect_gwas_study_ids(disease_ids, stamp):
    """Page studies() over the disease ids; return a sorted list of gwas ids.

    Every study row is inspected; only studyType == "gwas" ids are kept. Ids are
    de-duplicated (a study can be returned for several diseases via indirect
    associations) and returned in stable sorted order for reproducible batching.
    """
    gwas_ids = set()
    non_gwas = 0
    total_rows = 0

    index = 0
    total_count = None
    while True:
        count, rows = _fetch_studies_page(disease_ids, index, stamp)
        if total_count is None:
            total_count = count
        total_rows += len(rows)
        for row in rows:
            if row.get("studyType") == "gwas" and row.get("id"):
                gwas_ids.add(row["id"])
            else:
                non_gwas += 1
        common.log("  studies page %d -> %d rows (cumulative %d / count=%s); "
                   "gwas so far=%d"
                   % (index, len(rows), total_rows, total_count, len(gwas_ids)))
        # Stop when this page is short or we've reached the reported count.
        if not rows or len(rows) < STUDIES_PAGE_SIZE:
            break
        if total_count is not None and total_rows >= total_count:
            break
        index += 1

    common.log("collected %d distinct gwas study ids (%d non-gwas rows skipped, "
               "%d total study rows across %d page(s))"
               % (len(gwas_ids), non_gwas, total_rows, index + 1))
    return sorted(gwas_ids), {
        "reported_count": total_count,
        "total_rows_seen": total_rows,
        "non_gwas_rows": non_gwas,
        "pages": index + 1,
    }


# ---------------------------------------------------------------------------
# credibleSets(): fetch fine-mapped signals + L2G + colocalisation
# ---------------------------------------------------------------------------

def _batches(items, size):
    """Yield successive fixed-size slices of a list."""
    for start in range(0, len(items), size):
        yield start // size, items[start:start + size]


def _fetch_crediblesets_page(study_ids, batch_index, page_index, stamp):
    """POST one credibleSets page for a batch of study ids; return (count, rows)."""
    cache_path = L2G_RAW_DIR / (
        "crediblesets_%s_batch_%03d_page_%03d.json"
        % (stamp, batch_index, page_index)
    )
    payload = {
        "query": CREDIBLESETS_QUERY,
        "variables": {
            "ids": study_ids,
            "index": page_index,
            "size": CREDIBLESETS_PAGE_SIZE,
        },
    }
    data = common.post_json(API_URL, payload, cache_path=cache_path)
    cs = (data.get("data") or {}).get("credibleSets")
    if cs is None:
        raise RuntimeError(
            "Open Targets returned no credibleSets for batch %d page %d "
            "(study_ids=%r). Full response: %r"
            % (batch_index, page_index, study_ids, data)
        )
    return cs.get("count"), (cs.get("rows") or [])


def _fetch_crediblesets_for_batch(study_ids, batch_index, stamp):
    """Page credibleSets for one batch of study ids; return list of raw rows."""
    rows_all = []
    page_index = 0
    reported = None
    while True:
        count, rows = _fetch_crediblesets_page(
            study_ids, batch_index, page_index, stamp
        )
        if reported is None:
            reported = count
        rows_all.extend(rows)
        common.log("  credibleSets batch %d page %d -> %d rows "
                   "(cumulative %d / count=%s)"
                   % (batch_index, page_index, len(rows),
                      len(rows_all), reported))
        if not rows or len(rows) < CREDIBLESETS_PAGE_SIZE:
            break
        if reported is not None and len(rows_all) >= reported:
            break
        page_index += 1
    return rows_all, (page_index + 1)


def main():
    stamp = common.today_stamp()
    common.RAW_DIR.mkdir(parents=True, exist_ok=True)
    L2G_RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Resolve disease ids.
    diseases = _resolve_disease_ids(stamp)
    disease_ids = [d["id"] for d in diseases]
    common.log("resolved %d disease id(s): %s"
               % (len(diseases), ", ".join(disease_ids)))

    # 2) Collect gwas study ids.
    gwas_ids, studies_stats = _collect_gwas_study_ids(disease_ids, stamp)
    common.log("TOTAL gwas study ids: %d" % len(gwas_ids))

    # 3) credibleSets over batches of gwas ids.
    batch_page_counts = []
    total_credible_sets = 0
    n_batches = 0
    for batch_index, batch_ids in _batches(gwas_ids, STUDY_BATCH_SIZE):
        n_batches += 1
        common.log("credibleSets batch %d: %d study ids"
                   % (batch_index, len(batch_ids)))
        rows, pages = _fetch_crediblesets_for_batch(batch_ids, batch_index, stamp)
        total_credible_sets += len(rows)
        batch_page_counts.append({
            "batch_index": batch_index,
            "n_study_ids": len(batch_ids),
            "n_credible_sets": len(rows),
            "pages": pages,
        })

    # Write a manifest for provenance + downstream normalization.
    manifest = {
        "stamp": stamp,
        "api_url": API_URL,
        "diseases": diseases,
        "disease_ids": disease_ids,
        "studies_stats": studies_stats,
        "n_gwas_studies": len(gwas_ids),
        "gwas_study_ids": gwas_ids,
        "study_batch_size": STUDY_BATCH_SIZE,
        "n_batches": n_batches,
        "credible_sets_total": total_credible_sets,
        "batches": batch_page_counts,
        "credibleSets_page_size": CREDIBLESETS_PAGE_SIZE,
        "l2g_top_n": L2G_TOP_N,
        "coloc_size": COLOC_SIZE,
    }
    manifest_path = L2G_RAW_DIR / ("manifest_%s.json" % stamp)
    common._write_cache(manifest_path, manifest)

    common.log("INGEST DONE: %d gwas studies, %d batches, %d credible sets"
               % (len(gwas_ids), n_batches, total_credible_sets))
    common.log("manifest: %s" % manifest_path)
    print(str(manifest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
