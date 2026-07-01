"""Bibliographic-coupling and co-citation edges over the corpus.

Bibliographic coupling: two papers are linked when they cite the same earlier
papers (known at publication time -> leakage-safe).
Co-citation: two papers are linked when later papers cite both of them
(current-state view of how the field relates them).
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from math import sqrt
from typing import Any

from .. import config

Pair = tuple[str, str]


def _similarity_edges(
    adjacency: dict[str, list[str]],
    corpus: set[str],
    min_weight: float,
) -> dict[Pair, dict[str, float]]:
    """Cosine-normalized overlap edges from a paper -> neighbour-set mapping."""
    sizes = {p: len(set(adjacency.get(p, []))) for p in corpus}

    # Invert: neighbour -> corpus papers that touch it.
    neighbour_to_papers: dict[str, set[str]] = defaultdict(set)
    for paper in corpus:
        for neighbour in set(adjacency.get(paper, [])):
            neighbour_to_papers[neighbour].add(paper)

    shared: dict[Pair, int] = defaultdict(int)
    for papers in neighbour_to_papers.values():
        if len(papers) < 2:
            continue
        for a, b in combinations(sorted(papers), 2):
            shared[(a, b)] += 1

    edges: dict[Pair, dict[str, float]] = {}
    for (a, b), count in shared.items():
        denom = sqrt(sizes[a] * sizes[b])
        if not denom:
            continue
        weight = count / denom
        if weight >= min_weight:
            edges[(a, b)] = {"weight": weight, "shared": count}
    return edges


def build_edges(
    corpus: list[str],
    refs: dict[str, list[str]],
    citers: dict[str, list[str]],
) -> dict[str, Any]:
    """Return coupling edges, co-citation edges, and blended graph weights."""
    corpus_set = set(corpus)
    coupling = _similarity_edges(refs, corpus_set, config.MIN_COUPLING_WEIGHT)
    cocitation = _similarity_edges(citers, corpus_set, config.MIN_COCITATION_WEIGHT)

    # Blend the two edge types into a single weight for clustering.
    blended: dict[Pair, float] = defaultdict(float)
    for pair, e in coupling.items():
        blended[pair] += config.COUPLING_BLEND * e["weight"]
    for pair, e in cocitation.items():
        blended[pair] += config.COCITATION_BLEND * e["weight"]

    return {"coupling": coupling, "cocitation": cocitation, "blended": dict(blended)}


def to_export_records(edges: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten coupling + co-citation edges into paper_edge.schema records."""
    records: list[dict[str, Any]] = []
    for edge_type, key in (("bibliographic_coupling", "coupling"), ("co_citation", "cocitation")):
        for (a, b), e in edges[key].items():
            records.append(
                {
                    "source_paper_id": f"pmid:{a}",
                    "target_paper_id": f"pmid:{b}",
                    "edge_type": edge_type,
                    "weight": round(e["weight"], 6),
                    "evidence": {"shared_count": int(e["shared"])},
                }
            )
    return records
