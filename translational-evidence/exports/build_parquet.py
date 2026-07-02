#!/usr/bin/env python3
"""Export the Track B JSONL datasets to Parquet for in-browser DuckDB-Wasm querying.

Why Parquet (not the raw JSONL) for the web: it's columnar + compressed + supports
HTTP range requests, so DuckDB-Wasm in the browser downloads only the columns/rows a
query needs instead of the whole file (nodes.jsonl is 15 MB; its Parquet is a few MB).
Same DuckDB engine locally (query_te.py) and in the browser -> identical schema/types.

Optional dev/build dependency (the pipeline stays stdlib-only):  pip install duckdb
Re-runnable:  python3 translational-evidence/exports/build_parquet.py
Outputs: data/exports/parquet/<name>.parquet  + parquet_manifest.json
"""

import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402

# name -> source JSONL (same view names as query_te.py so SQL is identical local+browser)
DATASETS = {
    "genes": "data/processed/translational-evidence/genes.jsonl",
    "gwas_associations": "data/processed/translational-evidence/gwas_associations.jsonl",
    "trials": "data/processed/translational-evidence/trials.jsonl",
    "target_evidence": "data/processed/translational-evidence/target_evidence.jsonl",
    "pathways": "data/processed/translational-evidence/pathways.jsonl",
    "functional_links": "data/processed/translational-evidence/functional_links.jsonl",
    "entity_metrics": "data/processed/translational-evidence/entity_metrics.jsonl",
    "gene_pathways_api": "data/processed/translational-evidence/gene_pathways_api.jsonl",
    "drug_mechanism_api": "data/processed/translational-evidence/drug_mechanism_api.jsonl",
    "topic_evidence_links": "data/processed/shared/topic_evidence_links.jsonl",
    "topic_evidence_rollup": "data/processed/shared/topic_evidence_rollup.jsonl",
    "atlas_evidence_links": "data/processed/shared/atlas_evidence_links.jsonl",
    "atlas_evidence_rollup": "data/processed/shared/atlas_evidence_rollup.jsonl",
    "nodes": "data/exports/graph/nodes.jsonl",
    "edges": "data/exports/graph/edges.jsonl",
}

OUT_DIR = common.REPO_ROOT / "data" / "exports" / "parquet"


def main():
    try:
        import duckdb
    except ImportError:
        sys.stderr.write(
            "duckdb not installed (optional build dep). Install with:\n  pip install duckdb\n")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    manifest = {"generated_by": "build_parquet.py", "compression": "zstd", "files": []}
    for name, rel in DATASETS.items():
        src = common.REPO_ROOT / rel
        if not src.exists():
            common.log("skip (not built): %s" % rel)
            continue
        dst = OUT_DIR / ("%s.parquet" % name)
        # DuckDB COPY can't take ? params; inline the (controlled) paths, escaped.
        s = str(src).replace("'", "''")
        d = str(dst).replace("'", "''")
        con.execute(
            "COPY (SELECT * FROM read_json_auto('%s', format='newline_delimited', "
            "union_by_name=true, maximum_object_size=100000000)) TO '%s' "
            "(FORMAT parquet, COMPRESSION zstd)" % (s, d))
        rows = con.execute("SELECT count(*) FROM read_parquet('%s')" % d).fetchone()[0]
        j = src.stat().st_size
        p = dst.stat().st_size
        manifest["files"].append({
            "name": name, "parquet": "data/exports/parquet/%s.parquet" % name,
            "rows": rows, "jsonl_bytes": j, "parquet_bytes": p,
            "shrink": round(j / p, 1) if p else None,
        })
        common.log("%-22s %7d rows  %6.1f MB jsonl -> %5.1f MB parquet (%.1fx)"
                   % (name, rows, j / 1e6, p / 1e6, (j / p) if p else 0))

    (OUT_DIR / "parquet_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    tot_j = sum(f["jsonl_bytes"] for f in manifest["files"])
    tot_p = sum(f["parquet_bytes"] for f in manifest["files"])
    common.log("TOTAL %.1f MB jsonl -> %.1f MB parquet (%d files)"
               % (tot_j / 1e6, tot_p / 1e6, len(manifest["files"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
