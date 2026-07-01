"""Collect the full field corpus: every dementia+GWAS paper, with references.

No seed-and-expand and no per-paper keyword gate. The PubMed query *defines*
the field; we take the whole result set, then fetch each paper's reference list
(references + titles are all the co-citation network needs).

Known backbone papers (``seeds.py``) and any Track B GWAS PMIDs are unioned in
so the corpus is guaranteed to contain them even if the query phrasing misses
one, but they are not the mechanism that builds the corpus.
"""

from __future__ import annotations

from typing import Any

from .. import config
from . import pubmed, seeds


def collect_field(term: str, max_papers: int, log=print) -> dict[str, Any]:
    """Return corpus PMIDs, their reference lists, and their esummary records."""
    hist = pubmed.esearch_history(term)
    log(f"[corpus] query matched {hist['count']} papers in PubMed")
    if not hist["webenv"]:
        raise RuntimeError("esearch returned no history handle")

    summaries = pubmed.esummary_history(
        hist["webenv"], hist["query_key"], hist["count"],
        page=config.ESEARCH_PAGE, limit=max_papers, log=log,
    )
    corpus = list(summaries.keys())

    # Union in guaranteed backbone / Track B papers not already present.
    extra = [p for p in seeds.get_seed_pmids() if p not in summaries]
    if extra:
        summaries.update(pubmed.esummary(extra))
        corpus.extend(p for p in extra if p in summaries)
        log(f"[corpus] added {len(extra)} guaranteed backbone/Track-B papers")

    log(f"[corpus] fetching references + cited-by for {len(corpus)} papers")
    refs: dict[str, list[str]] = {}
    citers: dict[str, list[str]] = {}
    for i, pmid in enumerate(corpus, 1):
        refs[pmid] = pubmed.get_references(pmid)      # coupling input
        citers[pmid] = pubmed.get_citations(pmid)     # co-citation input
        if i % 250 == 0:
            log(f"[corpus] links {i}/{len(corpus)}")

    return {"corpus": corpus, "refs": refs, "citers": citers, "summaries": summaries}
