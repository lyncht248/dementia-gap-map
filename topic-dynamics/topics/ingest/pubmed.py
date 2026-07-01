"""PubMed E-utilities client: esearch, esummary, and elink (refs / cited-by)."""

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


def esearch(term: str, retmax: int) -> list[str]:
    """Return PMIDs matching ``term``."""
    params = _common_params()
    params.update({"db": "pubmed", "term": term, "retmax": retmax})
    data = get_json(
        f"{config.EUTILS_BASE}/esearch.fcgi",
        params=params,
        min_interval=config.NCBI_MIN_INTERVAL,
        label="esearch",
    )
    return data.get("esearchresult", {}).get("idlist", [])


def esummary(pmids: list[str]) -> dict[str, dict[str, Any]]:
    """Return raw esummary records keyed by PMID, batching in groups of 200."""
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
    """PMIDs this paper cites (bibliographic coupling input)."""
    refs = _elink(pmid, "pubmed_pubmed_refs", "elink_refs")
    return refs[: config.MAX_REFS_PER_PAPER]


def get_citations(pmid: str) -> list[str]:
    """PMIDs that cite this paper (co-citation input)."""
    citers = _elink(pmid, "pubmed_pubmed_citedin", "elink_citedin")
    # Keep the most recent (elink returns ascending PMID ~ chronological).
    return citers[-config.MAX_CITERS_PER_PAPER :]
