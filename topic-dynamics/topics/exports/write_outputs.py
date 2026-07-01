"""Write the four Track A handoff files as JSONL into data/processed/topic-dynamics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .. import config

_TOPIC_FIELDS = (
    "topic_id", "label", "summary", "paper_ids", "top_terms",
    "year_start", "year_end", "scores",
)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def write_all(
    papers: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    trajectories: list[dict[str, Any]],
    log=print,
) -> dict[str, int]:
    out = config.PROCESSED_DIR
    topic_rows = []
    for t in topics:
        row = {k: t.get(k) for k in _TOPIC_FIELDS if k in t or k == "summary"}
        row.setdefault("summary", None)
        topic_rows.append(row)

    counts = {
        "papers.jsonl": _write_jsonl(out / "papers.jsonl", papers.values()),
        "paper_edges.jsonl": _write_jsonl(out / "paper_edges.jsonl", edges),
        "topic_clusters.jsonl": _write_jsonl(out / "topic_clusters.jsonl", topic_rows),
        "topic_trajectories.jsonl": _write_jsonl(
            out / "topic_trajectories.jsonl", trajectories
        ),
    }
    for name, n in counts.items():
        log(f"[export] {name}: {n} records")
    return counts
