#!/usr/bin/env python3
"""Generate CATALOG.json: a machine-readable map of every Track B dataset.

Purpose: let an agent read ONE file and know (a) what datasets exist, (b) each
one's schema / primary key / record count / fields, and (c) how to JOIN them
(the join graph) — so cross-dataset questions don't require reverse-engineering
the schema. JSONL stays the source of truth; this just describes it.

Stdlib only. Re-runnable:  python3 translational-evidence/exports/build_catalog.py
Output: data/processed/translational-evidence/CATALOG.json
"""

import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402


# Known datasets: (relative path, schema file or None, primary_key, description, id_keys)
DATASETS = [
    ("data/processed/translational-evidence/genes.jsonl", "gene.schema.json",
     "gene_id",
     "One record per gene/target. Carries evidence_scores (genetic_support, "
     "functional_support, translation_gap, Open Targets scores) + disease_groups.",
     {"gene_id": "Ensembl gene id (stable)", "symbol": "HGNC symbol"}),
    ("data/processed/translational-evidence/gwas_associations.jsonl",
     "gwas_association.schema.json", "association_id",
     "One record per GWAS Catalog association (variant->trait). Has pmid, "
     "reported_genes[], ensembl_gene_ids[], variant.rsid, p_value, effect, disease_group.",
     {"association_id": "stable", "pmid": "PubMed id (joins to Track A papers)"}),
    ("data/processed/translational-evidence/trials.jsonl", "trial.schema.json",
     "nct_id",
     "One record per ClinicalTrials.gov study. interventions[], phases[], "
     "overall_status, mechanism_group, disease_group.",
     {"nct_id": "ClinicalTrials.gov id"}),
    ("data/processed/translational-evidence/target_evidence.jsonl",
     "target_evidence.schema.json", "target_id",
     "One record per (target, disease) from Open Targets. scores{} datatype "
     "association scores. One gene may appear under several diseases.",
     {"target_id": "Ensembl id", "gene_id": "Ensembl id", "disease_id": "MONDO/EFO id"}),
    ("data/processed/translational-evidence/pathways.jsonl", "pathway.schema.json",
     "pathway_id",
     "One record per mechanism/pathway group (curated vocabulary). gene_ids[] "
     "(symbols), scores{clinical_translation, clinical_saturation, translation_gap}.",
     {"pathway_id": "curated:<mechanism_group>", "mechanism_group": "bucket name"}),
    ("data/processed/translational-evidence/functional_links.jsonl",
     "functional_link.schema.json", "link_id",
     "Variant/locus -> gene functional evidence (Open Targets L2G + any QTL "
     "colocalisation). score, evidence_type, disease_group.",
     {"gene_id": "Ensembl id", "rsid": "variant"}),
    ("data/processed/translational-evidence/entity_metrics.jsonl",
     "entity_metric.schema.json", "entity_id",
     "Per-entity agent-composable metrics (gene/variant/pathway). metrics{} = "
     "dotted keys (genetic.*, functional.*, clinical.*, temporal.*, ...) each "
     "with {value, source}. Verdicts are for agents to compose, not shipped.",
     {"entity_id": "gene_id | 'variant:'+rsid | pathway_id", "entity_type": "gene|variant|pathway"}),
    ("data/processed/translational-evidence/gene_pathways_api.jsonl", None, "gene_id",
     "FULL multi-source pathway capture per gene (GO + Reactome + Open Targets). "
     "sources{} keeps ALL annotations; ad_bucket_signals[] are multi-valued "
     "{bucket,source,matched_term}; primary_bucket is a thin projection.",
     {"gene_id": "Ensembl id"}),
    ("data/processed/translational-evidence/drug_mechanism_api.jsonl", None, "name",
     "FULL Open Targets capture per trial drug: sources.opentargets[] (MoA + "
     "targets), mechanism_signals[] multi-valued, primary_mechanism projection.",
     {"name": "intervention name (lowercased)", "chembl_id": "ChEMBL id"}),
    ("data/processed/shared/topic_evidence_links.jsonl", "topic_evidence_link.schema.json",
     None,
     "Track A topic -> Track B evidence links. evidence_type in "
     "[gene|gwas_association|pathway|disease|trial|target]; method/confidence/"
     "provenance record HOW each link was made; supporting_paper_ids[] are PMIDs.",
     {"topic_id": "Track A topic", "evidence_id": "id in the referenced dataset",
      "supporting_paper_ids": "PMIDs (join to Track A papers / gwas pmid)"}),
    ("data/processed/shared/topic_evidence_rollup.jsonl", "topic_evidence_rollup.schema.json",
     "topic_id",
     "One record per Track A topic: Track B half of the map (pathway_group, "
     "top_genes, trials, aggregated scores).",
     {"topic_id": "Track A topic"}),
    ("data/exports/graph/nodes.jsonl", "evidence_node.schema.json", "node_id",
     "PRE-JOINED graph nodes (variant/gene/pathway/drug/trial/disease/topic). "
     "The single best artifact for relationship questions.",
     {"node_id": "'<type>:'+id, e.g. gene:ENSG..., variant:rs..., pathway:..."}),
    ("data/exports/graph/edges.jsonl", "evidence_edge.schema.json", "edge_id",
     "PRE-JOINED typed edges (variant_gene, gene_pathway, gene_disease, "
     "trial_drug, trial_pathway, drug_pathway, topic_gene, topic_pathway, "
     "topic_disease). source_id/target_id are node_ids; carry score/method/provenance.",
     {"source_id": "node_id", "target_id": "node_id"}),
]

# The join graph: how to connect datasets. Each = (from, from_field, to, to_field, note).
JOINS = [
    ("genes.jsonl", "symbol", "gwas_associations.jsonl", "reported_genes[]",
     "gene appears in a GWAS association's reported_genes"),
    ("genes.jsonl", "gene_id", "gwas_associations.jsonl", "ensembl_gene_ids[]",
     "Ensembl-id join (more precise than symbol)"),
    ("genes.jsonl", "gene_id", "target_evidence.jsonl", "gene_id",
     "Open Targets scores per gene (filter disease_id for a specific disease)"),
    ("genes.jsonl", "gene_id", "functional_links.jsonl", "gene_id", "L2G/QTL evidence per gene"),
    ("genes.jsonl", "gene_id", "entity_metrics.jsonl", "entity_id",
     "per-gene metrics (entity_type='gene')"),
    ("genes.jsonl", "gene_id", "gene_pathways_api.jsonl", "gene_id", "full pathway capture per gene"),
    ("genes.jsonl", "symbol", "pathways.jsonl", "gene_ids[]", "gene's mechanism/pathway group"),
    ("pathways.jsonl", "mechanism_group", "trials.jsonl", "mechanism_group",
     "clinical activity per mechanism (via crosswalk)"),
    ("trials.jsonl", "interventions[].name", "drug_mechanism_api.jsonl", "name",
     "drug MoA/target/mechanism per intervention"),
    ("gwas_associations.jsonl", "pmid", "topic_evidence_links.jsonl", "supporting_paper_ids[]",
     "a GWAS pub inside a topic (paper_overlap)"),
    ("topic_evidence_links.jsonl", "evidence_id", "genes.jsonl", "gene_id",
     "when evidence_type='gene'"),
    ("topic_evidence_links.jsonl", "evidence_id", "gwas_associations.jsonl", "association_id",
     "when evidence_type='gwas_association'"),
    ("topic_evidence_links.jsonl", "evidence_id", "pathways.jsonl", "pathway_id",
     "when evidence_type='pathway'"),
    ("topic_evidence_rollup.jsonl", "topic_id", "topic_evidence_links.jsonl", "topic_id",
     "rollup summarises the per-topic links"),
    ("edges.jsonl", "source_id", "nodes.jsonl", "node_id", "edge endpoint"),
    ("edges.jsonl", "target_id", "nodes.jsonl", "node_id", "edge endpoint"),
    ("gwas_associations.jsonl", "pmid", "(Track A) data/processed/topic-dynamics/papers.jsonl",
     "pmid", "cross-track join to the literature corpus"),
]

EXAMPLES = [
    {"question": "Genes genetically supported but clinically stalled",
     "approach": "genes.jsonl (evidence_scores.genetic_support) + entity_metrics.jsonl "
                 "(clinical.stopped_ratio, clinical.has_approval)"},
    {"question": "Under-translated mechanisms (opportunity)",
     "approach": "pathways.jsonl -> scores.translation_gap desc"},
    {"question": "Everything connected to a gene (variant->gene->pathway->drug, topics, disease)",
     "approach": "traverse edges.jsonl from nodes.jsonl node 'gene:<ENSG>' (or Cypher in Neo4j)"},
    {"question": "Dementia-vs-Alzheimer filter",
     "approach": "any dataset: filter disease_group / disease_groups (alzheimer, "
                 "vascular_dementia, frontotemporal_dementia, lewy_body_dementia, "
                 "mixed_dementia, dementia_unspecified)"},
    {"question": "How was a topic->gene link made?",
     "approach": "topic_evidence_links.jsonl -> method + confidence + provenance"},
]


def scan(path):
    p = common.REPO_ROOT / path
    if not p.exists():
        return {"present": False, "records": 0, "fields": []}
    try:
        rows = common.read_jsonl(p)
    except Exception as e:  # tolerate a mid-write / malformed line
        return {"present": True, "records": None, "fields": [], "note": "unreadable: %s" % e}
    fields = sorted(rows[0].keys()) if rows else []
    return {"present": True, "records": len(rows), "fields": fields}


def main():
    files = []
    for path, schema, pk, desc, id_keys in DATASETS:
        info = scan(path)
        files.append({
            "path": path,
            "format": "jsonl",
            "schema": ("shared/schemas/%s" % schema) if schema else None,
            "primary_key": pk,
            "records": info.get("records"),
            "present": info.get("present"),
            "fields": info.get("fields"),
            "id_keys": id_keys,
            "description": desc,
            **({"note": info["note"]} if info.get("note") else {}),
        })

    catalog = {
        "dataset": "dementia-gap-map / translational-evidence (Track B)",
        "generated_by": "translational-evidence/exports/build_catalog.py",
        "source_of_truth": "JSONL (one JSON object per line); every file validates "
                           "against its schema via translational-evidence/validate.py",
        "how_to_query": {
            "read_directly": "line-delimited JSON; stream with any language",
            "pre_joined_graph": "data/exports/graph/{nodes,edges}.jsonl already "
                                "materialise all cross-dataset relationships as typed edges",
            "sql": "translational-evidence/exports/query_te.py runs DuckDB SQL "
                   "directly over the JSONL (no import/server); requires `pip install duckdb`",
            "graph_traversal": "load data/exports/graph/neo4j/ into Neo4j (see that dir's README) for Cypher",
        },
        "id_conventions": {
            "gene": "Ensembl gene id (gene_id); symbol = HGNC",
            "variant": "rsID",
            "trial": "NCT id",
            "paper": "PMID",
            "disease": "controlled disease_group vocabulary",
            "graph_node": "'<node_type>:'+id",
        },
        "files": files,
        "join_graph": [
            {"from": a, "from_field": b, "to": c, "to_field": d, "note": e}
            for (a, b, c, d, e) in JOINS
        ],
        "example_questions": EXAMPLES,
        "notes": "disease_group / disease_groups is a controlled vocabulary present "
                 "on most records for dementia-vs-AD filtering. Scores carry their "
                 "component inputs (no black-box); verdicts (contradiction/opportunity/"
                 "novelty) are for agents to compose from entity_metrics.jsonl.",
    }

    out = common.PROCESSED_DIR / "CATALOG.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    present = sum(1 for f in files if f["present"])
    common.log("wrote %s (%d/%d datasets present)" % (out, present, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
