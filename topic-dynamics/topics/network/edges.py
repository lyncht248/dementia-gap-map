"""Bibliographic-coupling and co-citation edges.

- Bibliographic coupling: papers A and B are linked when they *share
  references* (they cite the same earlier work). Input: each corpus paper's
  reference list.

- Co-citation: papers X and Y are linked when later papers *cite both of them*.
  Input: each corpus paper's cited-by list (all citing papers, not just those
  in the corpus), giving the full co-citation signal.

Shared items (a reference, or a citing paper) touching more than
``MAX_NEIGHBOR_DF_FRACTION`` of the corpus are dropped as uninformative hubs
that would otherwise connect everything to everything.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from math import sqrt
from typing import Any

from .. import config

Pair = tuple[str, str]


def _similarity_edges(
    node_neighbours: dict[str, list[str]],
    sizes: dict[str, int],
    min_weight: float,
    max_df: int,
) -> dict[Pair, dict[str, float]]:
    """Cosine-normalized overlap edges from a node -> shared-neighbour mapping.

    ``node_neighbours`` maps each shared item (a reference, or a citing paper)
    to the nodes that touch it; ``sizes`` is each node's total neighbour count
    used for cosine normalization.
    """
    shared: dict[Pair, int] = defaultdict(int)
    for nodes in node_neighbours.values():
        if len(nodes) < 2 or len(nodes) > max_df:
            continue
        for a, b in combinations(sorted(nodes), 2):
            shared[(a, b)] += 1

    edges: dict[Pair, dict[str, float]] = {}
    for (a, b), count in shared.items():
        denom = sqrt(sizes.get(a, 0) * sizes.get(b, 0))
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
    max_df = max(2, int(config.MAX_NEIGHBOR_DF_FRACTION * len(corpus)))

    # Bibliographic coupling: invert paper -> references into reference -> papers.
    ref_sizes = {p: len(set(refs.get(p, []))) for p in corpus}
    ref_to_papers: dict[str, list[str]] = defaultdict(list)
    for paper in corpus:
        for ref in set(refs.get(paper, [])):
            ref_to_papers[ref].append(paper)
    coupling = _similarity_edges(
        ref_to_papers, ref_sizes, config.MIN_COUPLING_WEIGHT, max_df
    )

    # Co-citation: invert paper -> its citers into citing-paper -> corpus papers
    # it cites, so each citing paper contributes co-citation pairs.
    citer_sizes = {p: len(set(citers.get(p, []))) for p in corpus}
    citing_to_papers: dict[str, list[str]] = defaultdict(list)
    for paper in corpus:
        for citer in set(citers.get(paper, [])):
            citing_to_papers[citer].append(paper)
    cocitation = _similarity_edges(
        citing_to_papers, citer_sizes, config.MIN_COCITATION_WEIGHT, max_df
    )

    blended: dict[Pair, float] = defaultdict(float)
    for pair, e in coupling.items():
        blended[pair] += config.COUPLING_BLEND * e["weight"]
    for pair, e in cocitation.items():
        blended[pair] += config.COCITATION_BLEND * e["weight"]

    return {"coupling": coupling, "cocitation": cocitation, "blended": dict(blended)}


def to_export_records(edges: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten coupling + co-citation edges into paper_edge.schema records."""
    records: list[dict[str, Any]] = []
    for edge_type, key in (
        ("bibliographic_coupling", "coupling"),
        ("co_citation", "cocitation"),
    ):
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
