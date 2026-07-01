#!/usr/bin/env python3
"""Validate Track A handoff files against the shared JSON schemas.

    cd topic-dynamics
    python validate.py

Exits non-zero if any record violates its schema, so it can gate a build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = REPO_ROOT / "data" / "processed" / "topic-dynamics"
SCHEMAS = REPO_ROOT / "shared" / "schemas"

FILES = {
    "papers.jsonl": "paper.schema.json",
    "paper_edges.jsonl": "paper_edge.schema.json",
    "topic_clusters.jsonl": "topic_cluster.schema.json",
    "topic_trajectories.jsonl": "topic_trajectory.schema.json",
}


def main() -> int:
    ok = True
    for fn, schema_file in FILES.items():
        path = PROCESSED / fn
        if not path.exists():
            print(f"MISSING {fn} (run the pipeline first)")
            ok = False
            continue
        validator = jsonschema.Draft202012Validator(
            json.loads((SCHEMAS / schema_file).read_text())
        )
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        errors = [e.message for r in rows for e in validator.iter_errors(r)]
        status = "OK" if not errors else f"{len(errors)} ERRORS"
        print(f"{fn}: {len(rows)} rows -> {status}")
        for msg in errors[:5]:
            print(f"    {msg}")
        ok = ok and not errors
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
