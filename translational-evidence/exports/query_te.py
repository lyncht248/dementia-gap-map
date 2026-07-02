#!/usr/bin/env python3
"""Query the Track B datasets with SQL, directly over the JSONL — no import, no server.

Uses DuckDB, which reads JSONL natively. Every dataset in CATALOG.json is
registered as a view named after its file stem (e.g. genes, gwas_associations,
entity_metrics, topic_evidence_links, nodes, edges). Nested fields (evidence_scores,
metrics, interventions, sources) are DuckDB STRUCT/LIST — access with dot / unnest.

Requires:  pip install duckdb   (the only optional, dev-only dependency; the
pipeline itself stays stdlib-only. Neo4j covers graph traversal; this covers SQL.)

Usage:
  python3 translational-evidence/exports/query_te.py --list
  python3 translational-evidence/exports/query_te.py "SELECT symbol, evidence_scores.genetic_support g \
       FROM genes ORDER BY g DESC NULLS LAST LIMIT 10"
  python3 translational-evidence/exports/query_te.py "\
     SELECT g.symbol, g.evidence_scores.genetic_support gen, m.metrics \
     FROM genes g JOIN entity_metrics m ON m.entity_id = g.gene_id LIMIT 5"
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402

# view_name -> jsonl path (globbed at runtime so missing files are just skipped)
VIEWS = {
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
    "nodes": "data/exports/graph/nodes.jsonl",
    "edges": "data/exports/graph/edges.jsonl",
}


def connect():
    try:
        import duckdb
    except ImportError:
        sys.stderr.write(
            "duckdb not installed. Install the (optional, dev-only) dependency:\n"
            "  pip install duckdb\n"
            "The pipeline stays stdlib-only; this SQL helper is the only thing that "
            "needs it. You can also read the JSONL directly or use Neo4j for graph queries.\n")
        sys.exit(2)
    con = duckdb.connect()
    registered = []
    for name, rel in VIEWS.items():
        p = common.REPO_ROOT / rel
        if p.exists():
            # DuckDB DDL can't take ? params; inline the (controlled) path, escaped.
            path = str(p).replace("'", "''")
            con.execute(
                "CREATE VIEW %s AS SELECT * FROM read_json_auto('%s', "
                "format='newline_delimited', union_by_name=true, "
                "maximum_object_size=100000000)" % (name, path))
            registered.append(name)
    return con, registered


def main(argv):
    con, registered = connect()
    if not argv or argv[0] in ("--list", "-l"):
        print("registered views (%d):" % len(registered))
        for name in registered:
            cnt = con.execute("SELECT count(*) FROM %s" % name).fetchone()[0]
            cols = [r[0] for r in con.execute("DESCRIBE %s" % name).fetchall()]
            print("  %-24s %7d rows   cols: %s" % (name, cnt, ", ".join(cols)))
        print("\nExample:\n  python3 translational-evidence/exports/query_te.py "
              "\"SELECT symbol, evidence_scores.genetic_support g FROM genes "
              "ORDER BY g DESC NULLS LAST LIMIT 10\"")
        return 0
    sql = " ".join(argv)
    try:
        con.execute(sql)
        cols = [d[0] for d in con.description]
        rows = con.fetchall()
    except Exception as e:
        sys.stderr.write("SQL error: %s\n" % e)
        return 1
    print("\t".join(cols))
    for r in rows:
        print("\t".join("" if v is None else str(v) for v in r))
    sys.stderr.write("(%d rows)\n" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
