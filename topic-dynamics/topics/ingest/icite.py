"""NIH iCite client for article-level influence metrics (RCR, APT, etc.)."""

from __future__ import annotations

from typing import Any

from .. import config
from .http_cache import get_json

_FIELDS = "pmid,year,title,relative_citation_ratio,citation_count,apt,is_clinical"


def get_metrics(pmids: list[str]) -> dict[str, dict[str, Any]]:
    """Return iCite metric records keyed by PMID, batching in groups of 100."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pmids), 100):
        batch = pmids[i : i + 100]
        data = get_json(
            f"{config.ICITE_BASE}/pubs",
            params={"pmids": ",".join(batch), "fl": _FIELDS},
            min_interval=0.2,
            label="icite",
        )
        for rec in data.get("data", []):
            out[str(rec["pmid"])] = rec
    return out
