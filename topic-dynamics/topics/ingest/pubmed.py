"""PubMed E-utilities client: esearch (with history), esummary, and elink refs.

The corpus is the full result set of a broad field query, so retrieval uses the
Entrez history server (WebEnv / query_key) and pages through every hit rather
than capping at a retmax.
"""

from __future__ import annotations

from typing import Any

from .. import config
from .http_cache import get_json


def _common_params() -> dict[str, Any]:
    params: dict[str, Any] = {
        "tool": config.NCBI_TOOL,
        "email": config.NCBI_EMAIL,
        "retmode": "json",
    }
    if config.NCBI_API_KEY:
        params["api_key"] = config.NCBI_API_KEY
    return params


def esearch_history(term: str) -> dict[str, Any]:
    """Run the corpus query and park the results on the history server.

    Returns ``{count, webenv, query_key}``.
    """
    params = _common_params()
    params.update({"db": "pubmed", "term": term, "usehistory": "y", "retmax": 0})
    data = get_json(
        f"{config.EUTILS_BASE}/esearch.fcgi",
        params=params,
        min_interval=config.NCBI_MIN_INTERVAL,
        label="esearch_hist",
    )
    res = data.get("esearchresult", {})
    return {
        "count": int(res.get("count", 0)),
        "webenv": res.get("webenv"),
        "query_key": res.get("querykey"),
    }


def esummary_history(
    webenv: str,
    query_key: str,
    count: int,
    page: int,
    limit: int = 0,
    log=print,
) -> dict[str, dict[str, Any]]:
    """Page through the parked result set, returning esummary records by PMID."""
    out: dict[str, dict[str, Any]] = {}
    target = min(count, limit) if limit else count
    for start in range(0, target, page):
        params = _common_params()
        params.update(
            {
                "db": "pubmed",
                "WebEnv": webenv,
                "query_key": query_key,
                "retstart": start,
                "retmax": min(page, target - start),
            }
        )
        data = get_json(
            f"{config.EUTILS_BASE}/esummary.fcgi",
            params=params,
            min_interval=config.NCBI_MIN_INTERVAL,
            label="esummary",
        )
        result = data.get("result", {})
        for uid in result.get("uids", []):
            out[uid] = result[uid]
        log(f"[pubmed] esummary {min(start + page, target)}/{target}")
    return out


def esummary(pmids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch esummary records for an explicit PMID list (used for extra seeds)."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pmids), 200):
        batch = pmids[i : i + 200]
        params = _common_params()
        params.update({"db": "pubmed", "id": ",".join(batch)})
        data = get_json(
            f"{config.EUTILS_BASE}/esummary.fcgi",
            params=params,
            min_interval=config.NCBI_MIN_INTERVAL,
            label="esummary",
        )
        result = data.get("result", {})
        for uid in result.get("uids", []):
            out[uid] = result[uid]
    return out


def _elink(pmid: str, linkname: str, label: str) -> list[str]:
    params = _common_params()
    params.update(
        {"dbfrom": "pubmed", "db": "pubmed", "id": pmid, "linkname": linkname}
    )
    data = get_json(
        f"{config.EUTILS_BASE}/elink.fcgi",
        params=params,
        min_interval=config.NCBI_MIN_INTERVAL,
        label=label,
    )
    linksets = data.get("linksets", [])
    if not linksets:
        return []
    for db in linksets[0].get("linksetdbs", []):
        if db.get("linkname") == linkname:
            return list(db.get("links", []))
    return []


def get_references(pmid: str) -> list[str]:
    """PMIDs this paper cites (drives bibliographic coupling)."""
    return _elink(pmid, "pubmed_pubmed_refs", "elink_refs")


def get_citations(pmid: str) -> list[str]:
    """PMIDs that cite this paper (drives co-citation).

    Capped at ``MAX_CITERS_PER_PAPER``, keeping the most recent (elink returns
    citing PMIDs in ascending, ~chronological order).
    """
    citers = _elink(pmid, "pubmed_pubmed_citedin", "elink_citedin")
    return citers[-config.MAX_CITERS_PER_PAPER :]
