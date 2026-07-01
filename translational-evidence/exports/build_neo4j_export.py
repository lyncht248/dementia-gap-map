#!/usr/bin/env python3
"""Produce Neo4j-ready LOAD CSV artifacts from the FULL Track B evidence graph.

This is a *lossless re-projection* of the un-capped evidence graph
(``data/exports/graph/{nodes,edges}.jsonl``) into two flat CSV files plus a
Cypher loader, so the whole graph can be explored in Neo4j with filters instead
of aggressive caps. Nothing is pruned: every node and every edge in the JSONL
export becomes a row here.

Inputs (build products of build_evidence_graph.py; read, never modified):
  data/exports/graph/nodes.jsonl   -> conforms to evidence_node.schema.json
  data/exports/graph/edges.jsonl   -> conforms to evidence_edge.schema.json

Outputs (gitignored build products under data/exports/graph/neo4j/):
  nodes.csv     columns: node_id,node_type,label,score,genetic_support,
                functional_support,translation_gap,disease_groups,
                pathway_group,provenance
                (disease_groups is pipe-joined; provenance is a JSON string)
  edges.csv     columns: edge_id,source_id,target_id,edge_type,score,evidence
  load.cypher   constraint + LOAD CSV passes that MERGE nodes (generic :Entity
                label + per-type :Gene/:Variant/... labels, APOC-free) and
                CREATE typed relationships by edge_type
  README.md     is written by the caller / workflow, not here (kept separate so
                docs stay source-controlled).

Design notes for a faithful, APOC-free Neo4j import:
  * CSV is written with the default excel dialect (comma-delimited, double-quote
    quoting, CRLF-free via newline="") so Neo4j's LOAD CSV WITH HEADERS parses it
    directly. Embedded commas / quotes / newlines in labels or the provenance
    JSON string are handled by csv quoting.
  * The source graph has a handful of ``topic_gene`` edges that share an
    ``edge_id`` (same topic+gene, but different evidence: gene_mention vs
    paper_overlap). To keep the export lossless we make ``edge_id`` unique by
    appending "#2", "#3", ... to later collisions, and we CREATE (not MERGE)
    relationships so no evidence is silently merged away. The original id is
    preserved as the un-suffixed prefix.
  * Per-type labels are applied without APOC by running one small LOAD CSV pass
    per node_type that filters ``row.node_type`` and sets the concrete label via
    a static Cypher clause (e.g. ``SET n:Gene``). This avoids dynamic labels,
    which core Cypher cannot do.

Run:
  python3 translational-evidence/exports/build_neo4j_export.py
"""

import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GRAPH_DIR = common.REPO_ROOT / "data" / "exports" / "graph"
NODES_JSONL = GRAPH_DIR / "nodes.jsonl"
EDGES_JSONL = GRAPH_DIR / "edges.jsonl"

OUT_DIR = GRAPH_DIR / "neo4j"
NODES_CSV = OUT_DIR / "nodes.csv"
EDGES_CSV = OUT_DIR / "edges.csv"
LOAD_CYPHER = OUT_DIR / "load.cypher"
MANIFEST_PATH = OUT_DIR / "neo4j_manifest.json"


# ---------------------------------------------------------------------------
# CSV headers (order is contractual: the loader relies on these names)
# ---------------------------------------------------------------------------

NODE_HEADER = [
    "node_id",
    "node_type",
    "label",
    "score",
    "genetic_support",
    "functional_support",
    "translation_gap",
    "disease_groups",   # pipe-joined
    "pathway_group",
    "provenance",       # JSON string
]

EDGE_HEADER = [
    "edge_id",
    "source_id",
    "target_id",
    "edge_type",
    "score",
    "evidence",
]

# node_type -> concrete Neo4j label (used for the per-type SET passes).
NODE_TYPE_LABELS = {
    "variant": "Variant",
    "gene": "Gene",
    "pathway": "Pathway",
    "drug": "Drug",
    "trial": "Trial",
    "disease": "Disease",
    "topic": "Topic",
}

# edge_type -> Neo4j relationship type (UPPER_SNAKE by convention).
EDGE_TYPE_RELS = {
    "variant_gene": "VARIANT_GENE",
    "gene_pathway": "GENE_PATHWAY",
    "gene_disease": "GENE_DISEASE",
    "trial_drug": "TRIAL_DRUG",
    "trial_pathway": "TRIAL_PATHWAY",
    "drug_pathway": "DRUG_PATHWAY",
    "topic_gene": "TOPIC_GENE",
    "topic_pathway": "TOPIC_PATHWAY",
}


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _fmt_num(x):
    """Render a numeric value for CSV; None -> '' (empty, i.e. Cypher null)."""
    if x is None:
        return ""
    # Keep floats readable and Cypher-parseable (toFloat handles these).
    if isinstance(x, float):
        # Trim trailing zeros without losing precision for scientific values.
        return repr(x)
    return str(x)


def _pathway_group_for(node):
    """Best single pathway/mechanism group string for a node, or ''.

    Preference: explicit node 'group' -> provenance.pathway_group ->
    provenance.mechanism_group -> provenance.mechanism_groups (pipe-joined list).
    Drugs carry only a mechanism_groups list, so we join it.
    """
    grp = node.get("group")
    if grp:
        return str(grp)
    prov = node.get("provenance") or {}
    for key in ("pathway_group", "mechanism_group"):
        val = prov.get(key)
        if val:
            return str(val)
    groups = prov.get("mechanism_groups")
    if isinstance(groups, list) and groups:
        return "|".join(str(g) for g in groups if g)
    return ""


def node_to_row(node):
    """Flatten one node dict into the NODE_HEADER-ordered CSV row list."""
    scores = node.get("scores") or {}
    disease_groups = node.get("disease_groups") or []
    provenance = node.get("provenance") or {}
    return [
        node.get("node_id", ""),
        node.get("node_type", ""),
        node.get("label", ""),
        _fmt_num(node.get("score")),
        _fmt_num(scores.get("genetic_support")),
        _fmt_num(scores.get("functional_support")),
        _fmt_num(scores.get("translation_gap")),
        "|".join(str(g) for g in disease_groups if g),
        _pathway_group_for(node),
        json.dumps(provenance, ensure_ascii=False, sort_keys=True),
    ]


def edge_to_row(edge, edge_id):
    """Flatten one edge dict into the EDGE_HEADER-ordered CSV row list."""
    return [
        edge_id,
        edge.get("source_id", ""),
        edge.get("target_id", ""),
        edge.get("edge_type", ""),
        _fmt_num(edge.get("score")),
        edge.get("evidence", ""),
    ]


# ---------------------------------------------------------------------------
# Cypher loader generation
# ---------------------------------------------------------------------------

def build_load_cypher(node_types, edge_types):
    """Return the load.cypher text.

    ``node_types`` / ``edge_types`` are the *observed* types (so we only emit
    passes that will actually match rows), each rendered as a fully-static
    (APOC-free) LOAD CSV pass.
    """
    lines = []
    lines.append(
        "// Neo4j LOAD CSV loader for the FULL Track B translational-evidence "
        "graph.")
    lines.append(
        "// Generated by translational-evidence/exports/build_neo4j_export.py "
        "-- do not edit by hand.")
    lines.append("// Reads sibling files nodes.csv and edges.csv from the "
                 "Neo4j import/ directory.")
    lines.append("// APOC is NOT required. Run with:")
    lines.append("//   cat load.cypher | cypher-shell -u neo4j -p testpassword")
    lines.append("")

    lines.append("// --- 0. Uniqueness constraint on node_id "
                 "(also builds an index used by the loads below). ---")
    lines.append("CREATE CONSTRAINT entity_node_id IF NOT EXISTS")
    lines.append("FOR (n:Entity) REQUIRE n.node_id IS UNIQUE;")
    lines.append("")

    lines.append("// --- 1. Load every node as a generic :Entity, keyed by "
                 "node_id, carrying all properties. ---")
    lines.append("LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row")
    lines.append("MERGE (n:Entity {node_id: row.node_id})")
    lines.append("SET n.node_type          = row.node_type,")
    lines.append("    n.label              = row.label,")
    lines.append("    n.score              = toFloat(row.score),")
    lines.append("    n.genetic_support    = toFloat(row.genetic_support),")
    lines.append("    n.functional_support = toFloat(row.functional_support),")
    lines.append("    n.translation_gap    = toFloat(row.translation_gap),")
    lines.append("    n.disease_groups     = "
                 "CASE WHEN row.disease_groups IS NULL OR row.disease_groups = '' "
                 "THEN [] ELSE split(row.disease_groups, '|') END,")
    lines.append("    n.pathway_group      = row.pathway_group,")
    lines.append("    n.provenance         = row.provenance;")
    lines.append("")

    lines.append("// --- 2. Per-type label passes (APOC-free: one static SET "
                 "per node_type). ---")
    for nt in sorted(node_types):
        label = NODE_TYPE_LABELS.get(nt)
        if not label:
            # Unknown type: title-case the type as a fallback label.
            label = nt.replace("_", " ").title().replace(" ", "")
        lines.append("LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row")
        lines.append("WITH row WHERE row.node_type = '%s'" % nt)
        lines.append("MATCH (n:Entity {node_id: row.node_id})")
        lines.append("SET n:%s;" % label)
        lines.append("")

    lines.append("// --- 3. Typed relationships (CREATE, so parallel evidence "
                 "is preserved; one static pass per edge_type). ---")
    for et in sorted(edge_types):
        rel = EDGE_TYPE_RELS.get(et)
        if not rel:
            rel = et.upper()
        lines.append("LOAD CSV WITH HEADERS FROM 'file:///edges.csv' AS row")
        lines.append("WITH row WHERE row.edge_type = '%s'" % et)
        lines.append("MATCH (s:Entity {node_id: row.source_id})")
        lines.append("MATCH (t:Entity {node_id: row.target_id})")
        lines.append("CREATE (s)-[r:%s {edge_id: row.edge_id}]->(t)" % rel)
        lines.append("SET r.score    = toFloat(row.score),")
        lines.append("    r.evidence = row.evidence,")
        lines.append("    r.edge_type = row.edge_type;")
        lines.append("")

    lines.append("// --- 4. Sanity counts. ---")
    lines.append("MATCH (n) RETURN labels(n) AS labels, count(*) AS n "
                 "ORDER BY n DESC;")
    lines.append("MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n "
                 "ORDER BY n DESC;")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not NODES_JSONL.exists() or not EDGES_JSONL.exists():
        raise SystemExit(
            "Missing graph export. Run build_evidence_graph.py first "
            "(expected %s and %s)." % (NODES_JSONL, EDGES_JSONL))

    common.log("reading full graph export")
    nodes = common.read_jsonl(NODES_JSONL)
    edges = common.read_jsonl(EDGES_JSONL)
    common.log("loaded %d nodes, %d edges" % (len(nodes), len(edges)))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- nodes.csv -------------------------------------------------------
    node_type_counts = {}
    node_ids = set()
    dup_node_ids = 0
    with NODES_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(NODE_HEADER)
        for node in nodes:
            nid = node.get("node_id")
            if nid in node_ids:
                dup_node_ids += 1
            else:
                node_ids.add(nid)
            nt = node.get("node_type", "")
            node_type_counts[nt] = node_type_counts.get(nt, 0) + 1
            writer.writerow(node_to_row(node))
    common.log("wrote %s (%d rows)" % (NODES_CSV, len(nodes)))

    # --- edges.csv -------------------------------------------------------
    # Make edge_id unique (source graph has topic_gene collisions) so the CSV
    # is a faithful, lossless projection. CREATE in Cypher then keeps them all.
    edge_type_counts = {}
    seen_edge_ids = {}
    collisions = 0
    dangling = 0
    with EDGES_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(EDGE_HEADER)
        for edge in edges:
            src = edge.get("source_id")
            tgt = edge.get("target_id")
            if src not in node_ids or tgt not in node_ids:
                dangling += 1  # should be 0: the source export drops these.
            base_id = edge.get("edge_id", "")
            n_prev = seen_edge_ids.get(base_id, 0)
            if n_prev == 0:
                uniq_id = base_id
            else:
                uniq_id = "%s#%d" % (base_id, n_prev + 1)
                collisions += 1
            seen_edge_ids[base_id] = n_prev + 1
            et = edge.get("edge_type", "")
            edge_type_counts[et] = edge_type_counts.get(et, 0) + 1
            writer.writerow(edge_to_row(edge, uniq_id))
    common.log("wrote %s (%d rows)" % (EDGES_CSV, len(edges)))

    # --- load.cypher -----------------------------------------------------
    cypher = build_load_cypher(node_type_counts.keys(), edge_type_counts.keys())
    with LOAD_CYPHER.open("w", encoding="utf-8") as fh:
        fh.write(cypher)
    common.log("wrote %s" % LOAD_CYPHER)

    # --- manifest --------------------------------------------------------
    manifest = {
        "generated_by":
            "translational-evidence/exports/build_neo4j_export.py",
        "generated_on": common.today_stamp(),
        "source": {
            "nodes_jsonl": str(NODES_JSONL.relative_to(common.REPO_ROOT)),
            "edges_jsonl": str(EDGES_JSONL.relative_to(common.REPO_ROOT)),
        },
        "nodes_csv": {
            "path": str(NODES_CSV.relative_to(common.REPO_ROOT)),
            "rows": len(nodes),
            "by_type": node_type_counts,
            "duplicate_node_ids": dup_node_ids,
            "header": NODE_HEADER,
        },
        "edges_csv": {
            "path": str(EDGES_CSV.relative_to(common.REPO_ROOT)),
            "rows": len(edges),
            "by_type": edge_type_counts,
            "edge_id_collisions_uniquified": collisions,
            "dangling_endpoints": dangling,
            "header": EDGE_HEADER,
        },
        "load_cypher": str(LOAD_CYPHER.relative_to(common.REPO_ROOT)),
    }
    with MANIFEST_PATH.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")

    # --- console summary -------------------------------------------------
    common.log("nodes.csv rows=%d by_type=%s" % (len(nodes), node_type_counts))
    common.log("edges.csv rows=%d by_type=%s" % (len(edges), edge_type_counts))
    if collisions:
        common.log("uniquified %d colliding edge_id(s) with #N suffixes"
                   % collisions)
    if dangling:
        common.log("WARNING: %d edge(s) reference a missing node" % dangling)


if __name__ == "__main__":
    main()
