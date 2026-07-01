"""Yearly topic trajectories and explainable emergence scores.

Scores are deliberately transparent (each component is stored) so the future
visual layer can show *why* a topic ranks highly, per the build spec.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .. import config


def _minmax(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi <= lo:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _yearly_counts(pmids: list[str], papers: dict[str, dict[str, Any]]) -> list[dict[str, int]]:
    counts: Counter[int] = Counter()
    for pmid in pmids:
        year = papers.get(pmid, {}).get("year") or 0
        if year:
            counts[year] += 1
    return [{"year": y, "paper_count": counts[y]} for y in sorted(counts)]


def score_topics(
    topics: list[dict[str, Any]],
    papers: dict[str, dict[str, Any]],
    log=print,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach scores to topics and return (scored_topics, trajectories)."""
    max_year = max((p.get("year") or 0 for p in papers.values()), default=0)
    recent = config.EMERGENCE_RECENT_YEARS

    raw_growth: dict[str, float] = {}
    raw_influence: dict[str, float] = {}
    raw_cohesion: dict[str, float] = {}
    pct_new: dict[str, float] = {}

    for t in topics:
        pmids = t["pmids"]
        years = [papers.get(p, {}).get("year") or 0 for p in pmids]
        years = [y for y in years if y]
        total = len(years) or 1

        n_recent = sum(1 for y in years if y > max_year - recent)
        n_prev = sum(1 for y in years if max_year - 2 * recent < y <= max_year - recent)
        pct_new[t["topic_id"]] = n_recent / total
        raw_growth[t["topic_id"]] = n_recent / (n_prev + 1)

        rcrs = [
            papers[p]["metrics"].get("relative_citation_ratio")
            for p in pmids
            if papers.get(p, {}).get("metrics", {}).get("relative_citation_ratio")
        ]
        raw_influence[t["topic_id"]] = sum(rcrs) / len(rcrs) if rcrs else 0.0
        raw_cohesion[t["topic_id"]] = t.get("cohesion", 0.0)

    growth_n = _minmax(raw_growth)
    influence_n = _minmax(raw_influence)
    cohesion_n = _minmax(raw_cohesion)

    trajectories: list[dict[str, Any]] = []
    for i, t in enumerate(topics):
        tid = t["topic_id"]
        # Low cohesion == high topical mixedness == bridging behaviour.
        mixedness = 1.0 - cohesion_n.get(tid, 0.0)
        emergence = (
            0.40 * pct_new[tid]
            + 0.30 * growth_n.get(tid, 0.0)
            + 0.20 * influence_n.get(tid, 0.0)
            + 0.10 * mixedness
        )
        scores = {
            "emergence": round(emergence, 4),
            "growth": round(growth_n.get(tid, 0.0), 4),
            "influence": round(influence_n.get(tid, 0.0), 4),
            "cohesion": round(cohesion_n.get(tid, 0.0), 4),
            "pct_new": round(pct_new[tid], 4),
            "mean_rcr": round(raw_influence[tid], 4),
        }
        t["scores"] = scores
        t.pop("cohesion", None)

        yearly = _yearly_counts(t["pmids"], papers)
        trajectories.append(
            {
                "trajectory_id": f"traj:{i:03d}",
                "topic_ids": [tid],
                "parent_trajectory_ids": [],
                "child_trajectory_ids": [],
                "yearly_counts": yearly,
                "scores": scores,
            }
        )

    topics.sort(key=lambda t: t["scores"]["emergence"], reverse=True)
    log("[score] emergence scores computed")
    return topics, trajectories
