#!/usr/bin/env python3
"""Export the web map dataset (web/public/data/map_data.json) from the real
Track A (topic-dynamics) + Track B (translational-evidence) processed outputs.

This replaces the synthetic scripts/gen-mock-data.mjs. It is dependency-free
(Python 3 stdlib only), deterministic (no RNG — all jitter is hashed from
paper_id), and re-runnable whenever either track republishes.

Inputs (all JSONL / JSON under data/processed/):
  topic-dynamics/papers.jsonl          Track A: one record per paper
  topic-dynamics/topic_clusters.jsonl  Track A: topics + member paper_ids
  shared/topic_evidence_rollup.jsonl   Track B: per-topic gene/pathway/trial/score evidence
  shared/topic_evidence_links.jsonl    Track B: per-link evidence w/ supporting paper ids

Output:
  web/public/data/map_data.json        shape consumed by web/src/types.ts
"""
import hashlib
import json
import math
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "data", "processed")
OUT = os.path.join(ROOT, "web", "public", "data", "map_data.json")

PAPERS = os.path.join(PROC, "topic-dynamics", "papers.jsonl")
CLUSTERS = os.path.join(PROC, "topic-dynamics", "topic_clusters.jsonl")
ROLLUP = os.path.join(PROC, "shared", "topic_evidence_rollup.jsonl")
LINKS = os.path.join(PROC, "shared", "topic_evidence_links.jsonl")

# Layout lives in a large arbitrary space; the web app auto-fits the view.
SPACE = 2000.0
CENTER = SPACE / 2.0
RING_R = 860.0

# 18 distinct hues (17 real topics + 1 "unassigned"). Unassigned is neutral grey.
PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948",
    "#b07aa1", "#ff9da7", "#9c755f", "#7c5cbf", "#8cd17d", "#d37295",
    "#86bcb6", "#e69f00", "#56b4e9", "#009e73", "#cc79a7",
]
UNASSIGNED_COLOR = "#b8b8be"
UNCLASSIFIED = "unclassified"


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def unit_floats(key):
    """Two deterministic uniforms in [0,1) derived from a string key."""
    h = hashlib.md5(key.encode("utf-8")).digest()
    a = int.from_bytes(h[0:4], "big") / 2 ** 32
    b = int.from_bytes(h[4:8], "big") / 2 ** 32
    return a, b


def pmid_of(paper_id):
    return paper_id.split("pmid:", 1)[1] if paper_id.startswith("pmid:") else paper_id


def author_name(a):
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        return a.get("name") or a.get("full_name") or " ".join(
            x for x in (a.get("last"), a.get("initials")) if x
        )
    return str(a)


def main():
    papers = list(read_jsonl(PAPERS))
    clusters = list(read_jsonl(CLUSTERS))
    rollup = {r["topic_id"]: r for r in read_jsonl(ROLLUP)}

    # paper_id -> topic_id (Track A cluster membership)
    topic_of = {}
    for c in clusters:
        for pid in c.get("paper_ids", []):
            topic_of[pid] = c["topic_id"]

    # pmid -> {gene symbols} from Track B per-paper gene links (real attribution)
    genes_by_pmid = defaultdict(set)
    for lk in read_jsonl(LINKS):
        if lk.get("evidence_type") != "gene":
            continue
        sym = (lk.get("provenance") or {}).get("gene_symbol")
        if not sym:
            continue
        for spid in lk.get("supporting_paper_ids", []):
            genes_by_pmid[pmid_of(str(spid))].add(sym)

    # Order the topics on the ring: group by pathway so related topics sit near
    # each other, then append the synthetic "unassigned" node last.
    def pathway_of(topic_id):
        pg = (rollup.get(topic_id) or {}).get("pathway_group")
        return pg or UNCLASSIFIED

    ordered = sorted(clusters, key=lambda c: (pathway_of(c["topic_id"]), c["topic_id"]))
    layout_ids = [c["topic_id"] for c in ordered] + ["unassigned"]
    n_nodes = len(layout_ids)

    max_pc = max((len(c.get("paper_ids", [])) for c in clusters), default=1) or 1

    # Per-topic ring centroid + spread.
    node_geom = {}
    for i, tid in enumerate(layout_ids):
        ang = 2 * math.pi * i / n_nodes
        cx = CENTER + RING_R * math.cos(ang)
        cy = CENTER + RING_R * math.sin(ang)
        if tid == "unassigned":
            pc = sum(1 for p in papers if topic_of.get(p["paper_id"]) is None)
            cohesion = 0.0
        else:
            c = next(cc for cc in clusters if cc["topic_id"] == tid)
            pc = len(c.get("paper_ids", []))
            cohesion = float((c.get("scores") or {}).get("cohesion") or 0.0)
        spread = (42.0 + 68.0 * math.sqrt(pc / max_pc)) * (1.15 - 0.3 * cohesion)
        node_geom[tid] = {"cx": cx, "cy": cy, "spread": spread}

    # Place papers; accumulate positions per topic for centroid + year span.
    out_papers = []
    acc = defaultdict(lambda: {"xs": [], "ys": [], "years": []})
    for p in papers:
        pid = p["paper_id"]
        tid = topic_of.get(pid)
        node = tid if tid is not None else "unassigned"
        g = node_geom[node]
        u1, u2 = unit_floats(pid)
        r = g["spread"] * math.sqrt(u2)
        ang = 2 * math.pi * u1
        x = round(g["cx"] + r * math.cos(ang), 2)
        y = round(g["cy"] + r * math.sin(ang), 2)

        pmid = p.get("pmid")
        pg = pathway_of(tid) if tid is not None else UNCLASSIFIED
        genes = sorted(genes_by_pmid.get(pmid_of(pid), ()))
        m = p.get("metrics") or {}
        out_papers.append({
            "paper_id": pid,
            "pmid": pmid,
            "doi": p.get("doi"),
            "title": p.get("title") or "(untitled)",
            "year": p.get("year"),
            "journal": p.get("journal"),
            "authors": [author_name(a) for a in (p.get("authors") or [])],
            "cluster_id": node,
            "x": x,
            "y": y,
            "genes": genes,
            "pathway_group": pg,
            "trials": [],  # trials are topic-level evidence (see clusters[].trials)
            "metrics": {
                "citation_count": m.get("citation_count"),
                "relative_citation_ratio": m.get("relative_citation_ratio"),
                "apt": m.get("apt"),
                "is_clinical": m.get("is_clinical"),
            },
            "url": "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid if pmid else None,
        })
        acc[node]["xs"].append(x)
        acc[node]["ys"].append(y)
        if isinstance(p.get("year"), int):
            acc[node]["years"].append(p["year"])

    # Build cluster records (17 real topics + unassigned pseudo-cluster).
    out_clusters = []
    for idx, c in enumerate(ordered):
        tid = c["topic_id"]
        r = rollup.get(tid, {})
        a = acc[tid]
        n = len(a["xs"]) or 1
        pg = r.get("pathway_group") or UNCLASSIFIED
        ta_scores = c.get("scores") or {}
        tb_scores = r.get("scores") or {}
        scores = {}
        if ta_scores.get("emergence") is not None:
            scores["emergence"] = round(float(ta_scores["emergence"]), 4)
        for k in ("genetic_support", "functional_support",
                  "clinical_translation", "clinical_saturation"):
            v = tb_scores.get(k)
            if v is not None:
                scores[k] = round(float(v), 4)

        trials = []
        seen_nct = set()
        for t in r.get("trials", []):
            nct = t.get("nct_id")
            if nct in seen_nct:
                continue
            seen_nct.add(nct)
            label = t.get("brief_title") or t.get("mechanism_group") or nct
            if label:
                label = label.strip()
                if len(label) > 72:
                    label = label[:69].rstrip() + "…"
                trials.append(label)

        out_clusters.append({
            "topic_id": tid,
            "label": c.get("label") or tid,
            "color": PALETTE[idx % len(PALETTE)],
            "pathway_group": pg,
            "top_genes": [g.get("symbol") for g in r.get("top_genes", []) if g.get("symbol")],
            "trials": trials,
            "paper_count": len(c.get("paper_ids", [])),
            "centroid": {"x": round(sum(a["xs"]) / n, 2), "y": round(sum(a["ys"]) / n, 2)},
            "year_start": c.get("year_start") or (min(a["years"]) if a["years"] else None),
            "year_end": c.get("year_end") or (max(a["years"]) if a["years"] else None),
            "scores": scores,
        })

    # Unassigned pseudo-cluster (only if there are unassigned papers).
    ua = acc["unassigned"]
    if ua["xs"]:
        n = len(ua["xs"])
        out_clusters.append({
            "topic_id": "unassigned",
            "label": "Unassigned / other",
            "color": UNASSIGNED_COLOR,
            "pathway_group": UNCLASSIFIED,
            "top_genes": [],
            "trials": [],
            "paper_count": n,
            "centroid": {"x": round(sum(ua["xs"]) / n, 2), "y": round(sum(ua["ys"]) / n, 2)},
            "year_start": min(ua["years"]) if ua["years"] else None,
            "year_end": max(ua["years"]) if ua["years"] else None,
            "scores": {},
        })

    n_genes_papers = sum(1 for p in out_papers if p["genes"])
    n_topics_pg = sum(1 for c in out_clusters if c["pathway_group"] != UNCLASSIFIED)
    note = (
        "Real Track A (topic-dynamics) + Track B (translational-evidence) export "
        "via scripts/export_map_data.py. %d papers, %d topics; %d papers with "
        "gene links, %d topics with a pathway group." % (
            len(out_papers), len(clusters), n_genes_papers, n_topics_pg)
    )
    data = {
        "generated_note": note,
        "disease": "Alzheimer disease / dementia (ADRD)",
        "coordinate_space": "arbitrary 2D projection (auto-fit by the app)",
        "clusters": out_clusters,
        "papers": out_papers,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print("wrote %s" % os.path.relpath(OUT, ROOT))
    print("  papers=%d clusters=%d (incl. unassigned) papers_with_genes=%d topics_with_pathway=%d"
          % (len(out_papers), len(out_clusters), n_genes_papers, n_topics_pg))


if __name__ == "__main__":
    main()
