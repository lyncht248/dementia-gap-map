#!/usr/bin/env python3
"""
Build compact Parquet tables for the in-browser agent (DuckDB-Wasm).

Reads the Track B JSONL datasets + the on-screen map_data.json and writes small,
flattened Parquet files into web/public/data/parquet/. Nested score objects are
flattened into scalar columns; multi-valued fields are kept as native string
lists so the browser agent can use `list_contains(col, 'APOE')`.

Anchored on stable IDs only (PMID, Ensembl gene_id/symbol, NCT id, rsID,
disease_group) per docs/agent-graph-control-brief.md — never on coordinates or
community numbers, which move on re-layout / re-cluster.

Usage:  python3 scripts/build-agent-parquet.py
Requires: pyarrow  (already present in this environment)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TE = os.path.join(ROOT, "data", "processed", "translational-evidence")
MAP_DATA = os.path.join(ROOT, "web", "public", "data", "map_data.json")
OUT_DIR = os.path.join(ROOT, "web", "public", "data", "parquet")


def read_jsonl(path: str) -> Iterable[dict]:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def as_str_list(v: Any) -> list[str]:
    """Coerce a value into a list[str] (drop Nones, stringify scalars/dicts)."""
    if v is None:
        return []
    if not isinstance(v, list):
        v = [v]
    out: list[str] = []
    for x in v:
        if x is None:
            continue
        if isinstance(x, dict):
            # e.g. interventions: {"name": ..., "type": ...}
            name = x.get("name") or x.get("intervention_name") or x.get("label")
            out.append(str(name) if name is not None else json.dumps(x, sort_keys=True))
        else:
            out.append(str(x))
    return out


def num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None if v is None else float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write_table(name: str, rows: list[dict], schema: pa.schema) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    # Build column-oriented arrays according to the declared schema.
    cols: dict[str, list] = {f.name: [] for f in schema}
    for r in rows:
        for f in schema:
            cols[f.name].append(r.get(f.name))
    arrays = [pa.array(cols[f.name], type=f.type) for f in schema]
    table = pa.Table.from_arrays(arrays, schema=schema)
    out = os.path.join(OUT_DIR, f"{name}.parquet")
    pq.write_table(table, out, compression="zstd")
    print(f"  {name:18s} {len(rows):6d} rows  {os.path.getsize(out)/1024:8.1f} KiB")


STR = pa.string()
F64 = pa.float64()
I64 = pa.int64()
BOOL = pa.bool_()
STRLIST = pa.list_(pa.string())


def build_genes() -> None:
    rows = []
    for g in read_jsonl(os.path.join(TE, "genes.jsonl")):
        es = g.get("evidence_scores") or {}
        rows.append(
            {
                "gene_id": g.get("gene_id"),
                "symbol": g.get("symbol"),
                "name": g.get("name"),
                "pathway_group": es.get("pathway_group"),
                "disease_groups": as_str_list(g.get("disease_groups")),
                "genetic_support": num(es.get("genetic_support")),
                "functional_support": num(es.get("functional_support")),
                "open_targets_overall": num(es.get("open_targets_overall")),
                "open_targets_genetic_association": num(es.get("open_targets_genetic_association")),
                "open_targets_literature": num(es.get("open_targets_literature")),
                "open_targets_clinical": num(es.get("open_targets_clinical")),
                "open_targets_headline_disease": es.get("open_targets_headline_disease"),
                "gwas_study_count": num(es.get("gwas_study_count")),
                "gwas_association_count": num(es.get("gwas_association_count")),
                "best_neglog10p": num(es.get("best_neglog10p")),
                "best_p_value": num(es.get("best_p_value")),
                "example_variants": as_str_list(es.get("example_variants")),
            }
        )
    schema = pa.schema(
        [
            ("gene_id", STR), ("symbol", STR), ("name", STR), ("pathway_group", STR),
            ("disease_groups", STRLIST),
            ("genetic_support", F64), ("functional_support", F64),
            ("open_targets_overall", F64), ("open_targets_genetic_association", F64),
            ("open_targets_literature", F64), ("open_targets_clinical", F64),
            ("open_targets_headline_disease", STR),
            ("gwas_study_count", F64), ("gwas_association_count", F64),
            ("best_neglog10p", F64), ("best_p_value", F64),
            ("example_variants", STRLIST),
        ]
    )
    write_table("genes", rows, schema)


def build_pathways() -> None:
    rows = []
    for p in read_jsonl(os.path.join(TE, "pathways.jsonl")):
        sc = p.get("scores") or {}
        rows.append(
            {
                "pathway_id": p.get("pathway_id"),
                "label": p.get("label"),
                "mechanism_group": p.get("mechanism_group"),
                "gene_count": num(p.get("gene_count")),
                "gene_ids": as_str_list(p.get("gene_ids")),
                "clinical_translation": num(sc.get("clinical_translation")),
                "clinical_saturation": num(sc.get("clinical_saturation")),
                "combined_support": num(sc.get("combined_support")),
                "translation_gap": num(sc.get("translation_gap")),
                "trial_count": num(sc.get("trial_count")),
                "has_results_fraction": num(sc.get("has_results_fraction")),
                "max_phase_score": num(sc.get("max_phase_score")),
                "mapped_trial_mechanism": sc.get("mapped_trial_mechanism"),
            }
        )
    schema = pa.schema(
        [
            ("pathway_id", STR), ("label", STR), ("mechanism_group", STR),
            ("gene_count", F64), ("gene_ids", STRLIST),
            ("clinical_translation", F64), ("clinical_saturation", F64),
            ("combined_support", F64), ("translation_gap", F64),
            ("trial_count", F64), ("has_results_fraction", F64),
            ("max_phase_score", F64), ("mapped_trial_mechanism", STR),
        ]
    )
    write_table("pathways", rows, schema)


def build_trials() -> None:
    rows = []
    for t in read_jsonl(os.path.join(TE, "trials.jsonl")):
        rows.append(
            {
                "nct_id": t.get("nct_id"),
                "brief_title": t.get("brief_title"),
                "disease_group": t.get("disease_group"),
                "mechanism_group": t.get("mechanism_group"),
                "overall_status": t.get("overall_status"),
                "study_type": t.get("study_type"),
                "trial_category": t.get("trial_category"),
                "phases": as_str_list(t.get("phases")),
                "interventions": as_str_list(t.get("interventions")),
                "conditions": as_str_list(t.get("conditions")),
                "lead_sponsor": t.get("lead_sponsor"),
                "lead_sponsor_class": t.get("lead_sponsor_class"),
                "enrollment": num(t.get("enrollment")),
                "has_results": bool(t.get("has_results")) if t.get("has_results") is not None else None,
                "start_date": str(t.get("start_date")) if t.get("start_date") is not None else None,
            }
        )
    schema = pa.schema(
        [
            ("nct_id", STR), ("brief_title", STR), ("disease_group", STR),
            ("mechanism_group", STR), ("overall_status", STR), ("study_type", STR),
            ("trial_category", STR), ("phases", STRLIST), ("interventions", STRLIST),
            ("conditions", STRLIST), ("lead_sponsor", STR), ("lead_sponsor_class", STR),
            ("enrollment", F64), ("has_results", BOOL), ("start_date", STR),
        ]
    )
    write_table("trials", rows, schema)


def build_gwas() -> None:
    rows = []
    for a in read_jsonl(os.path.join(TE, "gwas_associations.jsonl")):
        var = a.get("variant")
        rsid = var.get("rsid") if isinstance(var, dict) else var
        rows.append(
            {
                "association_id": a.get("association_id"),
                "pmid": str(a.get("pmid")) if a.get("pmid") is not None else None,
                "rsid": rsid,
                "reported_genes": as_str_list(a.get("reported_genes")),
                "ensembl_gene_ids": as_str_list(a.get("ensembl_gene_ids")),
                "p_value": num(a.get("p_value")),
                "disease_group": a.get("disease_group"),
                "trait": a.get("trait"),
                "study_accession": a.get("study_accession"),
                "risk_frequency": num(a.get("risk_frequency")),
            }
        )
    schema = pa.schema(
        [
            ("association_id", STR), ("pmid", STR), ("rsid", STR),
            ("reported_genes", STRLIST), ("ensembl_gene_ids", STRLIST),
            ("p_value", F64), ("disease_group", STR), ("trait", STR),
            ("study_accession", STR), ("risk_frequency", F64),
        ]
    )
    write_table("gwas", rows, schema)


def build_functional_links() -> None:
    rows = []
    for f in read_jsonl(os.path.join(TE, "functional_links.jsonl")):
        rows.append(
            {
                "link_id": f.get("link_id"),
                "rsid": f.get("rsid"),
                "variant_or_locus": f.get("variant_or_locus"),
                "gene_id": f.get("gene_id"),
                "gene_symbol": f.get("gene_symbol"),
                "disease_group": f.get("disease_group"),
                "evidence_type": f.get("evidence_type"),
                "method": f.get("method"),
                "score": num(f.get("score")),
                "source": f.get("source"),
                "cell_type": f.get("cell_type"),
                "rank": num(f.get("rank")),
            }
        )
    schema = pa.schema(
        [
            ("link_id", STR), ("rsid", STR), ("variant_or_locus", STR),
            ("gene_id", STR), ("gene_symbol", STR), ("disease_group", STR),
            ("evidence_type", STR), ("method", STR), ("score", F64),
            ("source", STR), ("cell_type", STR), ("rank", F64),
        ]
    )
    write_table("functional_links", rows, schema)


def build_papers_and_clusters() -> None:
    with open(MAP_DATA) as f:
        d = json.load(f)
    cluster_label = {c["topic_id"]: c.get("label") for c in d["clusters"]}

    paper_rows = []
    for p in d["papers"]:
        m = p.get("metrics") or {}
        paper_rows.append(
            {
                "paper_id": p.get("paper_id"),
                "pmid": str(p.get("pmid")) if p.get("pmid") is not None else None,
                "title": p.get("title"),
                "year": num(p.get("year")),
                "journal": p.get("journal"),
                "cluster_id": p.get("cluster_id"),
                "cluster_label": cluster_label.get(p.get("cluster_id")),
                "pathway_group": p.get("pathway_group"),
                "x": num(p.get("x")),
                "y": num(p.get("y")),
                "citation_count": num(m.get("citation_count")),
                "relative_citation_ratio": num(m.get("relative_citation_ratio")),
                "is_clinical": bool(m.get("is_clinical")) if m.get("is_clinical") is not None else None,
                "genes": as_str_list(p.get("genes")),
                "trials": as_str_list(p.get("trials")),
                "doi": p.get("doi"),
                "url": p.get("url"),
            }
        )
    paper_schema = pa.schema(
        [
            ("paper_id", STR), ("pmid", STR), ("title", STR), ("year", F64),
            ("journal", STR), ("cluster_id", STR), ("cluster_label", STR),
            ("pathway_group", STR), ("x", F64), ("y", F64),
            ("citation_count", F64), ("relative_citation_ratio", F64),
            ("is_clinical", BOOL), ("genes", STRLIST), ("trials", STRLIST),
            ("doi", STR), ("url", STR),
        ]
    )
    write_table("papers", paper_rows, paper_schema)

    cluster_rows = []
    for c in d["clusters"]:
        sc = c.get("scores") or {}
        centroid = c.get("centroid") or {}
        cluster_rows.append(
            {
                "topic_id": c.get("topic_id"),
                "label": c.get("label"),
                "pathway_group": c.get("pathway_group"),
                "color": c.get("color"),
                "paper_count": num(c.get("paper_count")),
                "top_genes": as_str_list(c.get("top_genes")),
                "trials": as_str_list(c.get("trials")),
                "centroid_x": num(centroid.get("x")),
                "centroid_y": num(centroid.get("y")),
                "year_start": num(c.get("year_start")),
                "year_end": num(c.get("year_end")),
                "score_emergence": num(sc.get("emergence")),
                "score_genetic_support": num(sc.get("genetic_support")),
                "score_functional_support": num(sc.get("functional_support")),
                "score_clinical_translation": num(sc.get("clinical_translation")),
                "score_clinical_saturation": num(sc.get("clinical_saturation")),
            }
        )
    cluster_schema = pa.schema(
        [
            ("topic_id", STR), ("label", STR), ("pathway_group", STR), ("color", STR),
            ("paper_count", F64), ("top_genes", STRLIST), ("trials", STRLIST),
            ("centroid_x", F64), ("centroid_y", F64),
            ("year_start", F64), ("year_end", F64),
            ("score_emergence", F64), ("score_genetic_support", F64),
            ("score_functional_support", F64), ("score_clinical_translation", F64),
            ("score_clinical_saturation", F64),
        ]
    )
    write_table("clusters", cluster_rows, cluster_schema)


def build_manifest(tables: list[str]) -> None:
    manifest = {
        "note": "Parquet tables for the in-browser agent (DuckDB-Wasm). "
        "Generated by scripts/build-agent-parquet.py. Query stable IDs only "
        "(pmid, gene_id/symbol, nct_id, rsid, disease_group).",
        "tables": tables,
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)


def main() -> int:
    if not os.path.isdir(TE):
        print(f"missing data dir: {TE}", file=sys.stderr)
        return 1
    print(f"Writing Parquet -> {os.path.relpath(OUT_DIR, ROOT)}")
    build_genes()
    build_pathways()
    build_trials()
    build_gwas()
    build_functional_links()
    build_papers_and_clusters()
    build_manifest(
        ["genes", "pathways", "trials", "gwas", "functional_links", "papers", "clusters"]
    )
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
