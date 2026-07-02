#!/usr/bin/env python3
"""Build the SPECTER2 web maps for the Dementia Gap Map app.

Emits two files, both in the MapData schema the app already consumes:

  web/public/data/map_data.specter2.json         (style: default)
      SPECTER2 UMAP layout, dots sized by citations, one colour per cluster —
      the app's native look, for a like-for-like compare with the co-citation
      map.

  web/public/data/map_data.specter2_clean.json   (style: clean)
      The "datamapplot / Atlas" look: the deterministic non-overlapping bubble
      packing (from the embedding run) rendered as uniform dots with a smooth
      positional colour gradient (hue follows angle around the map) and no grid,
      so it reads as one continuous continent of themes.

Paper-level metadata (pmid/doi/journal/authors/genes/pathway_group/trials/
metrics/url) is joined by paper_id from the co-citation map so the feed +
filters keep working in every mode.

Run:
    python scripts/build_webmap_specter2.py
"""
from __future__ import annotations

import colorsys
import json
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
EMB = ROOT / "data/exports/visual/embeddings/specter2"
COCITE = ROOT / "web/public/data/map_data.json"
OUT = ROOT / "web/public/data/map_data.specter2.json"
OUT_CLEAN = ROOT / "web/public/data/map_data.specter2_clean.json"

# Every real HDBSCAN cluster becomes a topic; only the largest TOP_N_LABEL get an
# on-map text label (the rest stay unlabelled so the map isn't a wall of text).
# True HDBSCAN noise (cluster -1) falls into the faint "other" bucket.
TOP_N_LABEL = 18

COORD_X = 500.0   # match the co-citation coordinate half-extents
COORD_Y = 425.0


def load_jsonl(path: Path):
    with open(path) as fh:
        return [json.loads(l) for l in fh if l.strip()]


def title_case_label(label: str, n_terms: int = 2) -> str:
    out = []
    for w in label.split()[:n_terms]:
        out.append(w.upper() if (w.isupper() or any(ch.isdigit() for ch in w))
                   else w.capitalize())
    return " ".join(out)


def hsv_hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def distinct_colors(n: int) -> list[str]:
    """n visually distinct hex colours via golden-ratio hue spacing."""
    golden = 0.61803398875
    out = []
    for i in range(n):
        out.append(hsv_hex((i * golden) % 1.0,
                           0.55 + 0.12 * (i % 3),
                           0.72 + 0.10 * ((i // 3) % 2)))
    return out


def gradient_colors(xy: np.ndarray, k: int = 28, s: float = 0.55,
                    v: float = 0.92, hue_rot: float = 0.33) -> list[str]:
    """Smooth positional colour field: hue follows the angle around the map's
    centre, KNN-averaged on the hue circle so the centre isn't noisy and the
    result is a buttery gradient that respects the UMAP structure."""
    c = xy.mean(0)
    ang = np.arctan2(xy[:, 1] - c[1], xy[:, 0] - c[0])
    hue = (ang / (2 * np.pi)) % 1.0
    vecs = np.stack([np.cos(2 * np.pi * hue), np.sin(2 * np.pi * hue)], 1)
    _, idx = cKDTree(xy).query(xy, k=min(k, len(xy)))
    sm = vecs[idx].mean(1)
    hue_s = (np.arctan2(sm[:, 1], sm[:, 0]) / (2 * np.pi) + hue_rot) % 1.0
    return [hsv_hex(h, s, v) for h in hue_s]


def scale_aniso(xy: np.ndarray) -> np.ndarray:
    """Fill the (wide) box, each axis independent (UMAP axes have no units)."""
    xy = xy - (xy.max(0) + xy.min(0)) / 2.0
    half = np.percentile(np.abs(xy), 98, axis=0)
    out = xy.copy()
    out[:, 0] = np.clip(xy[:, 0] * (COORD_X / half[0]), -COORD_X, COORD_X)
    out[:, 1] = np.clip(xy[:, 1] * (COORD_Y / half[1]), -COORD_Y, COORD_Y)
    return out


def scale_iso(xy: np.ndarray, frac: float = 0.95) -> np.ndarray:
    """Roughly place the layout in the box preserving aspect (keeps organic
    cluster/island shapes). Packing sets the final density; this just anchors."""
    xy = xy - (xy.max(0) + xy.min(0)) / 2.0
    half = np.percentile(np.abs(xy), 95, axis=0)
    f = min(COORD_X / half[0], COORD_Y / half[1]) * frac
    return xy * f


def pack(xy: np.ndarray, n: int, iters: int = 1400, anchor_k: float = 0.02):
    """Confined collision relaxation -> uniform, densely tiled dots (the
    "continent" look). Repulsion pushes overlapping dots apart while a soft
    circular boundary pulls stragglers in, so dense UMAP regions inflate AND
    sparse regions contract to a single uniform density that fills the frame —
    which plain repulsion (push-only) cannot do. A weak anchor to the input
    layout keeps clusters in their region. Returns (packed_positions, draw_radius)."""
    # Confinement radius = fill the box; dot radius follows from hex coverage.
    R = 0.98 * min(COORD_X, COORD_Y)
    hex_cov = 0.60  # sub-critical so relaxation fully clears overlaps (dense but not jammed)
    r = float(R * np.sqrt(hex_cov / n))
    min_d = 2.0 * r

    # Start from the layout scaled so its bulk sits inside the boundary disk.
    xy = xy - xy.mean(0)
    p98 = float(np.percentile(np.linalg.norm(xy, axis=1), 98))
    pos = (xy * (R / p98)).astype(np.float64)
    anchor = pos.copy()

    def sep(p, step=1.0):
        pairs = cKDTree(p).query_pairs(min_d, output_type="ndarray")
        if len(pairs) == 0:
            return p
        disp = np.zeros_like(p)
        a, b = pairs[:, 0], pairs[:, 1]
        delta = p[a] - p[b]
        dist = np.linalg.norm(delta, axis=1)
        tie = dist < 1e-9
        delta[tie] = (a[tie] - b[tie])[:, None] * np.array([1e-6, -1e-6])
        dist[tie] = np.linalg.norm(delta[tie], axis=1)
        push = (step * (min_d - dist) * 0.5)[:, None] * (delta / dist[:, None])
        np.add.at(disp, a, push)
        np.add.at(disp, b, -push)
        return p + disp

    for it in range(iters):
        pos = sep(pos)
        pos += (anchor - pos) * (anchor_k * max(0.0, 1.0 - it / (iters * 0.6)))
        # soft circular confinement: pull anything past R back toward the disk
        rad = np.linalg.norm(pos, axis=1)
        out = rad > R
        if out.any():
            pos[out] -= pos[out] * ((rad[out] - R) / rad[out] * 0.5)[:, None]
    # final pure-separation passes to clear any boundary pile-ups
    for _ in range(1500):
        if len(cKDTree(pos).query_pairs(min_d, output_type="ndarray")) == 0:
            break
        pos = sep(pos)
    return pos.astype(np.float32), r * 0.96


def assign_topics(clusters_raw):
    """Map real HDBSCAN clusters -> topic ids; label only the largest N."""
    real = [c for c in clusters_raw if c["cluster"] != -1]
    topic_of, order = {}, []
    for i, c in enumerate(real):
        tid = f"s{i}"
        topic_of[c["cluster"]] = tid
        order.append((tid, c, i))
    return topic_of, order


def build(points, meta, topic_of, order, coords, *, style, colors=None,
          cluster_colors, point_radius=None, note):
    """Assemble a MapData dict. `colors` is an optional per-paper colour list
    (used by the clean gradient); `cluster_colors[tid]` gives each topic's
    colour (dot colour in default mode, label colour in clean mode)."""
    members = {tid: [] for tid, _, _ in order}
    papers = []
    for i, pt in enumerate(points):
        pid = pt["paper_id"]
        m = meta.get(pid, {})
        tid = topic_of.get(pt["cluster"], "other")
        rec = {
            "paper_id": pid, "pmid": m.get("pmid"), "doi": m.get("doi"),
            "title": pt.get("title") or m.get("title", ""),
            "year": pt.get("year") or m.get("year"),
            "journal": m.get("journal"), "authors": m.get("authors", []),
            "cluster_id": tid,
            "x": round(float(coords[i, 0]), 2), "y": round(float(coords[i, 1]), 2),
            "genes": m.get("genes", []),
            "pathway_group": m.get("pathway_group", "unclassified"),
            "trials": m.get("trials", []),
            "metrics": m.get("metrics", {"citation_count": None,
                "relative_citation_ratio": None, "apt": None, "is_clinical": None}),
            "url": m.get("url") or (f"https://pubmed.ncbi.nlm.nih.gov/{m.get('pmid')}/"
                                    if m.get("pmid") else None),
        }
        if colors is not None:
            rec["color"] = colors[i]
        papers.append(rec)
        if tid != "other":
            members[tid].append(i)

    clusters = []
    for tid, c, i in order:
        idx = members[tid]
        if not idx:
            continue
        pts = coords[idx]
        mp = [papers[j] for j in idx]
        genes = Counter(g for p in mp for g in p["genes"])
        trials = Counter(t for p in mp for t in p["trials"])
        pg = Counter(p["pathway_group"] for p in mp)
        years = [p["year"] for p in mp if p["year"]]
        clusters.append({
            "topic_id": tid,
            "label": title_case_label(c["label"]) if i < TOP_N_LABEL else "",
            "color": cluster_colors[tid],
            "pathway_group": pg.most_common(1)[0][0] if pg else "unclassified",
            "top_genes": [g for g, _ in genes.most_common(8)],
            "trials": [t for t, _ in trials.most_common(10)],
            "paper_count": len(idx),
            "centroid": {"x": round(float(np.median(pts[:, 0])), 2),
                         "y": round(float(np.median(pts[:, 1])), 2)},
            "year_start": min(years) if years else None,
            "year_end": max(years) if years else None,
            "scores": {}, "emergence": None, "terms": c["top_terms"],
        })

    out = {
        "generated_note": note,
        "disease": "Alzheimer disease / dementia (ADRD)",
        "coordinate_space": ("packed bubble layout of SPECTER2 embeddings"
                             if style == "clean"
                             else "UMAP (cosine) of SPECTER2 title+abstract embeddings"),
        "source": "specter2",
        "style": style,
        "clusters": clusters,
        "papers": papers,
        "edges": [],
    }
    if point_radius is not None:
        out["point_radius"] = round(point_radius, 3)
    return out


def main():
    points = load_jsonl(EMB / "points.jsonl")
    clusters_raw = load_jsonl(EMB / "clusters.jsonl")
    meta = {p["paper_id"]: p for p in json.loads(COCITE.read_text())["papers"]}
    umap = np.array([[p["x"], p["y"]] for p in points], dtype=float)
    topic_of, order = assign_topics(clusters_raw)

    # ---- default: native app look ----
    palette = distinct_colors(len(order))
    cluster_colors = {tid: palette[i] for tid, _, i in order}
    default = build(points, meta, topic_of, order, scale_aniso(umap),
                    style="default", cluster_colors=cluster_colors,
                    note=("SPECTER2 semantic map: title+abstract embeddings "
                          "(allenai/specter2 proximity adapter), UMAP 2D + HDBSCAN. "
                          "Dots sized by citations, one colour per cluster."))
    OUT.write_text(json.dumps(default))

    # ---- clean: packed uniform dots + positional gradient ----
    packed, r = pack(scale_iso(umap), len(points))
    grad = gradient_colors(packed)
    # each topic's label colour = the gradient colour of its most central member
    grad_arr = np.array(grad, dtype=object)
    tmembers = {tid: [] for tid, _, _ in order}
    for i, pt in enumerate(points):
        tid = topic_of.get(pt["cluster"])
        if tid:
            tmembers[tid].append(i)
    clean_cluster_colors = {}
    for tid, _, _ in order:
        idx = tmembers[tid]
        cen = packed[idx].mean(0)
        j = idx[int(np.argmin(((packed[idx] - cen) ** 2).sum(1)))]
        clean_cluster_colors[tid] = grad[j]
    clean = build(points, meta, topic_of, order, packed, style="clean",
                  colors=grad, cluster_colors=clean_cluster_colors,
                  point_radius=r,
                  note=("SPECTER2 'cleaned' map: non-overlapping packed bubbles "
                        "(uniform dots) with a smooth positional colour gradient. "
                        "Layout from SPECTER2 title+abstract embeddings."))
    OUT_CLEAN.write_text(json.dumps(clean))

    for label, path, d in [("default", OUT, default), ("clean", OUT_CLEAN, clean)]:
        n_other = sum(1 for p in d["papers"] if p["cluster_id"] == "other")
        print(f"[{label}] {path.name}: {len(d['papers'])} papers, "
              f"{len(d['clusters'])} themes, {n_other} in 'other', "
              f"{path.stat().st_size/1e6:.2f} MB"
              + (f", point_radius={d.get('point_radius')}" if "point_radius" in d else ""))


if __name__ == "__main__":
    main()
