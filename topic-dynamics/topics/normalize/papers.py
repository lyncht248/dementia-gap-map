"""Normalize PubMed esummary + iCite records into ``paper.schema.json`` records."""

from __future__ import annotations

import re
from typing import Any


def _year(rec: dict[str, Any]) -> int | None:
    # esummary gives e.g. "2022 Apr 4" in pubdate / epubdate.
    for key in ("pubdate", "epubdate", "sortpubdate"):
        val = rec.get(key)
        if val:
            m = re.search(r"\d{4}", str(val))
            if m:
                return int(m.group())
    return None


def _authors(rec: dict[str, Any]) -> list[str]:
    return [a.get("name", "") for a in rec.get("authors", []) if a.get("name")]


def _doi(rec: dict[str, Any]) -> str | None:
    for aid in rec.get("articleids", []):
        if aid.get("idtype") == "doi":
            return aid.get("value")
    return None


def build_paper_record(
    pmid: str,
    summary: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = summary or {}
    metrics = metrics or {}
    year = _year(summary) or (int(metrics["year"]) if metrics.get("year") else None)
    return {
        "paper_id": f"pmid:{pmid}",
        "pmid": pmid,
        "doi": _doi(summary),
        "title": summary.get("title") or metrics.get("title") or "",
        "abstract": None,  # esummary has no abstract; efetch left for later
        "year": year if year is not None else 0,
        "journal": summary.get("fulljournalname") or summary.get("source"),
        "authors": _authors(summary),
        "metrics": {
            "citation_count": metrics.get("citation_count"),
            "relative_citation_ratio": metrics.get("relative_citation_ratio"),
            "apt": metrics.get("apt"),
            "is_clinical": metrics.get("is_clinical"),
        },
        "sources": ["pubmed"],
    }
