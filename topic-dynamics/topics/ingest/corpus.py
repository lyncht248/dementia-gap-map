"""Build the paper corpus by expanding seed PMIDs through PubMed links.

Strategy (bounded to keep API usage and memory in check):

1. Start from seed PMIDs.
2. Fetch each seed's references and cited-by lists.
3. Rank candidate neighbours by how many seed neighbourhoods they appear in
   (a cheap relevance signal), pull metadata for the top candidates, and keep
   the ones whose title is on-topic until the corpus cap is reached.
4. Fetch references + cited-by for every accepted paper so the network stage
   has coupling and co-citation inputs for the whole corpus.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .. import config
from . import pubmed


def _is_on_topic(title: str) -> bool:
    low = (title or "").lower()
    return any(kw in low for kw in config.TOPIC_KEYWORDS)


def build_corpus(
    seed_pmids: list[str],
    max_papers: int,
    log=print,
) -> dict[str, Any]:
    """Return corpus PMIDs plus reference/citation adjacency and summaries."""
    seed_pmids = list(dict.fromkeys(seed_pmids))
    refs: dict[str, list[str]] = {}
    citers: dict[str, list[str]] = {}

    log(f"[corpus] expanding {len(seed_pmids)} seeds")
    candidate_counts: Counter[str] = Counter()
    for pmid in seed_pmids:
        refs[pmid] = pubmed.get_references(pmid)
        citers[pmid] = pubmed.get_citations(pmid)
        for neighbour in set(refs[pmid]) | set(citers[pmid]):
            if neighbour not in seed_pmids:
                candidate_counts[neighbour] += 1

    # Look at more candidates than we need; the keyword filter drops many.
    room = max(0, max_papers - len(seed_pmids))
    ranked = [pmid for pmid, _ in candidate_counts.most_common(room * 3 + 50)]
    log(f"[corpus] scoring {len(ranked)} candidate neighbours for relevance")

    summaries = pubmed.esummary(seed_pmids + ranked)

    accepted: list[str] = []
    for pmid in ranked:
        if len(accepted) >= room:
            break
        rec = summaries.get(pmid)
        if rec and _is_on_topic(rec.get("title", "")):
            accepted.append(pmid)

    corpus = seed_pmids + accepted
    log(f"[corpus] corpus size: {len(corpus)} ({len(accepted)} expanded)")

    # Fill in reference/citation lists for accepted papers.
    for pmid in accepted:
        if pmid not in refs:
            refs[pmid] = pubmed.get_references(pmid)
        if pmid not in citers:
            citers[pmid] = pubmed.get_citations(pmid)

    return {
        "corpus": corpus,
        "seeds": seed_pmids,
        "refs": refs,
        "citers": citers,
        "summaries": {pmid: summaries[pmid] for pmid in corpus if pmid in summaries},
    }
