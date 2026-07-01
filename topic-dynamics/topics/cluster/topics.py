"""Community detection over the blended paper network + deterministic labels."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities

from .. import config

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "study", "analysis",
    "using", "based", "novel", "human", "disease", "diseases", "risk", "gene",
    "genes", "genetic", "genetics", "genome", "wide", "association", "associations",
    "identifies", "identify", "reveals", "role", "new", "insights", "into",
    "brain", "cell", "cells", "cellular", "single", "data", "large", "meta",
    "analyses", "variants", "variant", "loci", "locus", "common", "rare",
    "sequencing", "expression", "level", "levels", "protein", "proteins",
    "patients", "population", "populations", "cohort", "biomarkers", "biomarker",
    "clinical", "molecular", "functional", "are", "not", "its", "via", "per",
}


def _tokens(title: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", (title or "").lower())
    return [w for w in words if w not in _STOPWORDS]


def _top_terms(titles: list[str], global_df: Counter, n_docs: int, k: int) -> list[str]:
    tf: Counter[str] = Counter()
    for t in titles:
        tf.update(set(_tokens(t)))  # doc-frequency within the cluster
    scored: list[tuple[float, str]] = []
    for term, c in tf.items():
        idf = math.log((n_docs + 1) / (global_df.get(term, 0) + 1)) + 1
        scored.append((c * idf, term))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [term for _, term in scored[:k]]


def cluster(
    corpus: list[str],
    blended: dict[tuple[str, str], float],
    papers: dict[str, dict[str, Any]],
    log=print,
) -> list[dict[str, Any]]:
    """Return topic-cluster records (unscored) sorted by size descending."""
    graph = nx.Graph()
    graph.add_nodes_from(corpus)
    for (a, b), w in blended.items():
        if w > 0:
            graph.add_edge(a, b, weight=w)

    if graph.number_of_edges() == 0:
        log("[cluster] no edges; every paper is its own singleton")
        communities = [{p} for p in corpus]
    else:
        communities = greedy_modularity_communities(graph, weight="weight")

    # Global doc-frequency for IDF over the whole corpus.
    global_df: Counter[str] = Counter()
    for pmid in corpus:
        global_df.update(set(_tokens(papers.get(pmid, {}).get("title", ""))))
    n_docs = len(corpus)

    topics: list[dict[str, Any]] = []
    tid = 0
    for members in sorted(communities, key=len, reverse=True):
        members = [m for m in members if m in papers]
        if len(members) < config.MIN_CLUSTER_SIZE:
            continue
        titles = [papers[m].get("title", "") for m in members]
        years = [papers[m].get("year") or 0 for m in members]
        years = [y for y in years if y]
        terms = _top_terms(titles, global_df, n_docs, config.TOP_TERMS_PER_TOPIC)

        # Cohesion: mean internal blended weight (0 if isolated).
        internal = [
            blended.get(tuple(sorted((a, b))), 0.0)
            for i, a in enumerate(members)
            for b in members[i + 1 :]
        ]
        cohesion = sum(internal) / len(internal) if internal else 0.0

        topics.append(
            {
                "topic_id": f"topic:{tid:03d}",
                "label": " / ".join(terms[:3]) if terms else f"topic {tid}",
                "paper_ids": [f"pmid:{m}" for m in members],
                "pmids": members,
                "top_terms": terms,
                "year_start": min(years) if years else 0,
                "year_end": max(years) if years else 0,
                "cohesion": cohesion,
            }
        )
        tid += 1

    log(f"[cluster] {len(topics)} topics >= {config.MIN_CLUSTER_SIZE} papers")
    return topics
