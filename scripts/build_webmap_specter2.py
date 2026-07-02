#!/usr/bin/env python3
"""Build web/public/data/map_data.specter2.json for the Dementia Gap Map app.

Turns the SPECTER2 embedding run (data/exports/visual/embeddings/specter2/) into
the same MapData schema the web app already consumes for the co-citation map, so
the app can toggle between the two. Paper-level metadata (pmid/doi/journal/
authors/genes/pathway_group/trials/metrics/url) is joined by paper_id from the
existing co-citation map_data.json; the semantic layout (x,y) and cluster
assignment come from SPECTER2.

Run:
    python scripts/build_webmap_specter2.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EMB = ROOT / "data/exports/visual/embeddings/specter2"
COCITE = ROOT / "web/public/data/map_data.json"
OUT = ROOT / "web/public/data/map_data.specter2.json"

# Every real HDBSCAN cluster becomes a coloured topic; only the largest
# TOP_N_LABEL get an on-map text label (the rest stay coloured but unlabelled so
# the map isn't a wall of text). True HDBSCAN noise (cluster -1) falls into the
# faint "other" bucket, matching how the co-citation map handles its tail.
TOP_N_LABEL = 18

COORD_X = 500.0   # match the co-citation coordinate half-extents
COORD_Y = 425.0


def distinct_colors(n: int) -> list[str]:
    """n visually distinct hex colours via golden-ratio hue spacing."""
    import colorsys

    golden = 0.61803398875
    out = []
    for i in range(n):
        h = (i * golden) % 1.0
        s = 0.55 + 0.12 * (i % 3)          # vary saturation a little
        v = 0.72 + 0.10 * ((i // 3) % 2)   # ...and value, for extra separation
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return out


def load_jsonl(path: Path):
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def title_case_label(label: str, n_terms: int = 2) -> str:
    words = label.split()[:n_terms]
    out = []
    for w in words:
        # keep gene-symbol-like tokens upper, else Title-case
        out.append(w.upper() if (w.isupper() or any(ch.isdigit() for ch in w))
                   else w.capitalize())
    return " ".join(out)


def main():
    points = load_jsonl(EMB / "points.jsonl")
    clusters_raw = load_jsonl(EMB / "clusters.jsonl")
    cocite = json.loads(COCITE.read_text())
    meta = {p["paper_id"]: p for p in cocite["papers"]}

    ids = [p["paper_id"] for p in points]
    xy = np.array([[p["x"], p["y"]] for p in points], dtype=float)

    # Scale the SPECTER2 UMAP layout into the app's coordinate box, centered.
    # UMAP axes carry no inherent units, so we scale each axis independently to
    # fill the (wide) canvas — matching how the co-citation map fills the box —
    # using the 98th percentile so a few stragglers don't shrink the bulk, then
    # clamp the outliers to the edge.
    center = (xy.max(0) + xy.min(0)) / 2.0
    xy = xy - center
    half = np.percentile(np.abs(xy), 98, axis=0)
    xy[:, 0] = np.clip(xy[:, 0] * (COORD_X / half[0]), -COORD_X, COORD_X)
    xy[:, 1] = np.clip(xy[:, 1] * (COORD_Y / half[1]), -COORD_Y, COORD_Y)

    # Promote every real cluster to a coloured topic; label only the largest
    # TOP_N_LABEL. clusters_raw is already sorted by size descending, so real
    # noise (cluster -1) is the only thing that becomes "other".
    real = [c for c in clusters_raw if c["cluster"] != -1]
    colors = distinct_colors(len(real))
    topic_of: dict[int, str] = {}
    cluster_meta: dict[str, dict] = {}
    for i, c in enumerate(real):
        tid = f"s{i}"
        topic_of[c["cluster"]] = tid
        cluster_meta[tid] = {
            "topic_id": tid,
            "label": title_case_label(c["label"]) if i < TOP_N_LABEL else "",
            "color": colors[i],
            "raw_terms": c["top_terms"],
            "members": [],
        }

    # ---- papers ----
    papers = []
    for i, pt in enumerate(points):
        pid = pt["paper_id"]
        m = meta.get(pid, {})
        tid = topic_of.get(pt["cluster"], "other")
        rec = {
            "paper_id": pid,
            "pmid": m.get("pmid"),
            "doi": m.get("doi"),
            "title": pt.get("title") or m.get("title", ""),
            "year": pt.get("year") or m.get("year"),
            "journal": m.get("journal"),
            "authors": m.get("authors", []),
            "cluster_id": tid,
            "x": round(float(xy[i, 0]), 2),
            "y": round(float(xy[i, 1]), 2),
            "genes": m.get("genes", []),
            "pathway_group": m.get("pathway_group", "unclassified"),
            "trials": m.get("trials", []),
            "metrics": m.get("metrics", {
                "citation_count": None, "relative_citation_ratio": None,
                "apt": None, "is_clinical": None,
            }),
            "url": m.get("url") or (f"https://pubmed.ncbi.nlm.nih.gov/{m.get('pmid')}/"
                                    if m.get("pmid") else None),
        }
        papers.append(rec)
        if tid != "other":
            cluster_meta[tid]["members"].append(i)

    # ---- clusters ----
    clusters = []
    for tid, cm in cluster_meta.items():
        idx = cm["members"]
        pts = xy[idx]
        member_papers = [papers[i] for i in idx]
        genes = Counter(g for p in member_papers for g in p["genes"])
        trials = Counter(t for p in member_papers for t in p["trials"])
        pgroups = Counter(p["pathway_group"] for p in member_papers)
        years = [p["year"] for p in member_papers if p["year"]]
        dominant_pg = pgroups.most_common(1)[0][0] if pgroups else "unclassified"
        clusters.append({
            "topic_id": tid,
            "label": cm["label"],
            "color": cm["color"],
            "pathway_group": dominant_pg,
            "top_genes": [g for g, _ in genes.most_common(8)],
            "trials": [t for t, _ in trials.most_common(10)],
            "paper_count": len(idx),
            "centroid": {"x": round(float(np.median(pts[:, 0])), 2),
                         "y": round(float(np.median(pts[:, 1])), 2)},
            "year_start": min(years) if years else None,
            "year_end": max(years) if years else None,
            "scores": {},
            "emergence": None,
            "terms": cm["raw_terms"],
        })

    out = {
        "generated_note": (
            "SPECTER2 semantic map: title+abstract embeddings "
            "(allenai/specter2 proximity adapter), UMAP 2D layout + HDBSCAN "
            f"clustering. {len(papers)} papers, {len(clusters)} labelled themes "
            "(smaller clusters + noise shown as 'other'). Paper metadata joined "
            "from the co-citation map by paper_id. Built by "
            "scripts/build_webmap_specter2.py."
        ),
        "disease": cocite.get("disease", "Alzheimer disease / dementia (ADRD)"),
        "coordinate_space": "UMAP (cosine) of SPECTER2 title+abstract embeddings (auto-fit by the app)",
        "source": "specter2",
        "clusters": clusters,
        "papers": papers,
        "edges": [],  # semantic map has no co-citation coupling web
    }
    OUT.write_text(json.dumps(out))
    n_other = sum(1 for p in papers if p["cluster_id"] == "other")
    print(f"[webmap] wrote {OUT.relative_to(ROOT)}")
    print(f"[webmap] {len(papers)} papers, {len(clusters)} labelled themes, "
          f"{n_other} in 'other'")
    print(f"[webmap] size: {OUT.stat().st_size/1e6:.2f} MB")


if __name__ == "__main__":
    main()
