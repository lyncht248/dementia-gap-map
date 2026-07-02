#!/usr/bin/env python3
"""Build the interactive "theme atlas" web view from the Qwen3-Embedding-8B run.

Reads the bake-off artifacts produced by ``scripts/embed_map.py`` for the
``qwen3-8b`` model and writes the layout the web app renders
(``web/public/atlas/atlas.json``, drawn by ``web/src/lib/atlasRender.ts``),
a Nomic-Atlas-style theme map:

  * one hex-tiled dot per paper (4,780 dots), no overlaps;
  * a two-tier topic hierarchy — a handful of **major** topics shown when zoomed
    out, and the fine-grained **minor** topics revealed as you zoom in;
  * hover a dot to trace the papers it cites / is cited by.

Key design decision — *how we choose topics*
--------------------------------------------
The 45 clusters Qwen/HDBSCAN found mix several *classes* of thing (diseases,
methodologies, individual genes, biological themes). For the zoom-out view we
want the big labels to all be the **same class**, so we pick a single class that
the embedding geometry actually supports as coherent, contiguous regions:
**disease / neurological condition**.

Empirically the non-Alzheimer conditions (Parkinson's, ALS/FTD, Huntington's,
Lewy body, prion, MS, ophthalmic, vascular, psychiatric) fall out as tight,
spatially-separated islands, while Alzheimer's disease forms one large central
"continent". Each fine cluster is assigned to exactly one disease major; the
fine clusters themselves (Mendelian randomization, polygenic scores, microglia &
TREM2, fluid biomarkers, …) become the minor topics you see on zoom-in.

Run: ``python3 scripts/build_atlas.py``
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/exports/visual/embeddings/qwen3-8b"
# Inside the web app's public/ dir so it is both openable directly (the page is
# fully self-contained) and served live at /atlas/ when the site deploys.
OUT_DIR = ROOT / "web/public/atlas"

# Hex-lattice spacing for the packed display coords (world units). Sized to the
# corpus's dense-core nearest-neighbour distance so the footprint is preserved.
PACK_SPACING = 0.032

# ---------------------------------------------------------------------------
# Minor topics: human-readable names for each of the 45 Qwen/HDBSCAN clusters.
# (Derived from each cluster's top TF-IDF terms in clusters.jsonl.)
# ---------------------------------------------------------------------------
MINOR_LABELS: dict[int, str] = {
    7: "Mendelian Randomization",
    19: "Parkinson's — SNCA / LRRK2",
    12: "ALS & Frontotemporal Dementia",
    14: "Alzheimer's Reviews & Genomics",
    21: "AD Pathology — Tau / Amyloid",
    37: "Neuroimaging Genetics",
    10: "Fluid Biomarkers (CSF / plasma)",
    42: "Polygenic Risk Scores",
    29: "Microglia & Neuroinflammation",
    31: "Multi-omics & Drug Repurposing",
    9: "DNA Methylation & Epigenetics",
    2: "Alzheimer's GWAS Meta-analyses",
    44: "Cognitive Function & Schizophrenia",
    33: "Epistasis & Gene Interactions",
    41: "East Asian AD Genetics",
    1: "Huntington's Disease",
    34: "AD Rare Variants (ABCA7)",
    25: "Cerebral Small-vessel Disease",
    32: "Ancestry-diverse AD Genetics",
    20: "Aging, Longevity & Telomeres",
    24: "Cardiometabolic Shared Risk",
    23: "GWAS Methods & Imputation",
    43: "Mild Cognitive Impairment",
    4: "Glaucoma & Macular Degeneration",
    27: "TREM2 & Microglial Receptors",
    3: "Multiple Sclerosis",
    15: "Neurodegeneration (overview)",
    26: "Cross-disorder eQTL & Pleiotropy",
    40: "Late-onset AD Linkage",
    5: "Sleep & Circadian Traits",
    6: "Gut Microbiome",
    8: "Copy-number & Structural Variation",
    13: "PSP & Atypical Parkinsonism",
    16: "Dementia Meta-analyses (ABCC9)",
    28: "Regulatory & Functional Variants",
    38: "Depression & Bipolar Genetics",
    18: "Lewy Body Dementia",
    11: "Hereditary Ataxia & Canine Models",
    17: "Parkinson's Cognitive Decline",
    0: "Prion Disease (CJD)",
    30: "Transcriptome-wide Association",
    39: "Clusterin (CLU) Association",
    35: "Complement Receptor (CR1)",
    22: "Splicing & Isoforms",
    36: "Machine-learning Classification",
}

# ---------------------------------------------------------------------------
# Major topics: single class = disease / neurological condition.
# Each entry: key -> (label, color). Fine clusters map into these below.
# Colors: one warm hue for the Alzheimer's continent, distinct saturated hues
# for the disease islands.
# ---------------------------------------------------------------------------
MAJORS: dict[str, tuple[str, str]] = {
    "alzheimer": ("Alzheimer's Disease & Dementia", "#E4794B"),
    "parkinson": ("Parkinson's Disease", "#E0A93B"),
    "als_ftd": ("ALS & Frontotemporal Dementia", "#C24C8E"),
    "lewy_atypical": ("Lewy Body & Atypical Parkinsonism", "#9070D0"),
    "huntington": ("Huntington's & Hereditary Ataxias", "#5FA8DE"),
    "vascular": ("Vascular & Small-vessel Dementia", "#3FB0A2"),
    "psychiatric": ("Psychiatric & Cognitive Traits", "#B98A34"),
    "ms": ("Multiple Sclerosis", "#63BE55"),
    "prion": ("Prion Disease", "#D9534F"),
    "ophthalmic": ("Ophthalmic Neurodegeneration", "#7E9A3C"),
}

# fine cluster id -> major key
CLUSTER_TO_MAJOR: dict[int, str] = {
    # Parkinson's
    19: "parkinson", 17: "parkinson",
    # ALS / FTD
    12: "als_ftd",
    # Lewy body & atypical parkinsonism (PSP/MSA/corticobasal)
    18: "lewy_atypical", 13: "lewy_atypical",
    # Huntington's & hereditary ataxias
    1: "huntington", 11: "huntington",
    # Vascular / small-vessel / cardiometabolic
    25: "vascular", 24: "vascular",
    # Psychiatric & cognitive / cross-disorder
    44: "psychiatric", 38: "psychiatric", 26: "psychiatric",
    # Multiple sclerosis
    3: "ms",
    # Prion
    0: "prion",
    # Ophthalmic
    4: "ophthalmic",
}
# everything else -> the Alzheimer's / dementia continent
for _c in MINOR_LABELS:
    CLUSTER_TO_MAJOR.setdefault(_c, "alzheimer")


def pack_force(xy, groups, spacing, iters=240):
    """Tighten the UMAP layout into dense, overlap-free disease regions while
    keeping each region a distinct, cohesive shape (not one fused blob).

    Forces, settled from the UMAP layout:
      * **region gravity** — a modest pull toward the region centroid fills the
        diffuse whitespace so each disease reads as a solid mass. Kept low so the
        region keeps an organic outline rather than collapsing to a circle.
      * **global gravity** — a gentle pull toward the whole-map centroid brings
        the islands in closer without merging them.
      * **anchor** — a pull back to the point's own UMAP position, preserving the
        continent's real shape and the internal ordering of its sub-topics.
      * **collision** — no two dots closer than one diameter (clean tiling).
    Deterministic (fixed initial positions, no RNG).
    """
    import numpy as np
    from scipy.spatial import cKDTree

    pos = np.asarray(xy, dtype=float).copy()
    anchor = pos.copy()
    groups = np.asarray(groups)
    r = spacing / 2.0
    gids = np.unique(groups)
    for it in range(iters):
        t = it / iters
        g_grav = 0.045 * (1 - 0.5 * t)  # fill each region (low = organic shape)
        g_glob = 0.04 * (1 - 0.3 * t)   # bring islands closer (not fused)
        g_anch = 0.016                  # preserve the UMAP continent shape

        gcen = pos.mean(0)
        cen = {g: pos[groups == g].mean(0) for g in gids}
        for g in gids:
            m = groups == g
            pos[m] += g_grav * (cen[g] - pos[m])
        pos += g_glob * (gcen - pos)
        pos += g_anch * (anchor - pos)

        pairs = cKDTree(pos).query_pairs(2 * r, output_type="ndarray")
        if len(pairs):
            a, b = pairs[:, 0], pairs[:, 1]
            d = pos[a] - pos[b]
            dist = np.sqrt((d ** 2).sum(1)) + 1e-9
            push = (2 * r - dist).clip(min=0) / 2.0
            unit = d / dist[:, None]
            disp = np.zeros_like(pos)
            np.add.at(disp, a, unit * push[:, None])
            np.add.at(disp, b, -unit * push[:, None])
            pos += disp
    return pos


def hex_snap(xy, spacing):
    """Snap the (already compact) cloud onto a single hexagonal lattice so every
    paper gets its own cell — perfect tiling, zero overlaps, uniform gaps, like
    the reference atlas. Nearest-free-cell assignment processed densest-first so
    each point claims a cell close to itself and the gaps between disease islands
    are preserved (rather than collapsing into one disk). Deterministic.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    xy = np.asarray(xy, dtype=float)
    n = len(xy)
    d, _ = cKDTree(xy).query(xy, k=min(7, n))
    order = np.argsort(d[:, -1])  # dense (small kth-NN dist) first

    dx = spacing
    dy = spacing * math.sqrt(3) / 2.0

    def cell_of(x, y):
        row = int(round(y / dy))
        col = int(round((x - (row % 2) * dx / 2.0) / dx))
        return row, col

    def cell_center(row, col):
        return (col * dx + (row % 2) * dx / 2.0, row * dy)

    occupied: set[tuple[int, int]] = set()
    out = [None] * n
    for i in order:
        x, y = xy[i]
        r0, c0 = cell_of(x, y)
        best = None
        best_ring = 0
        ring = 0
        while True:
            for rr in range(r0 - ring, r0 + ring + 1):
                for cc in range(c0 - ring, c0 + ring + 1):
                    if max(abs(rr - r0), abs(cc - c0)) != ring:
                        continue
                    if (rr, cc) in occupied:
                        continue
                    cxp, cyp = cell_center(rr, cc)
                    dd = (cxp - x) ** 2 + (cyp - y) ** 2
                    if best is None or dd < best[0]:
                        best = (dd, rr, cc)
                        best_ring = ring
            # once we have a candidate, scan one more ring (a nearer cell can sit
            # in the next ring out) then stop.
            if best is not None and ring >= best_ring + 1:
                break
            ring += 1
            if ring > 600:
                break
        _, rr, cc = best
        occupied.add((rr, cc))
        out[i] = cell_center(rr, cc)
    return np.array(out, dtype=float)


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#%02x%02x%02x" % (round(r), round(g), round(b))


def lighten(hex_color: str, amt: float) -> str:
    """amt in [-1,1]; positive toward white, negative toward black."""
    r, g, b = hex_to_rgb(hex_color)
    if amt >= 0:
        r += (255 - r) * amt
        g += (255 - g) * amt
        b += (255 - b) * amt
    else:
        f = 1 + amt
        r *= f
        g *= f
        b *= f
    return rgb_to_hex(r, g, b)


def main() -> None:
    points = [json.loads(l) for l in (SRC / "points.jsonl").open()]
    clusters = {json.loads(l)["cluster"]: json.loads(l)
                for l in (SRC / "clusters.jsonl").open()}
    manifest = json.loads((SRC / "manifest.json").read_text())

    # Re-pack for a dense, no-overlap "theme atlas" look: squeeze whitespace out
    # centroids). Each point's region = its fine cluster's disease major (noise
    # points ride with their nearest non-noise neighbour's major).
    grp_idx = {g: i for i, g in enumerate(MAJORS)}
    import numpy as _np
    from scipy.spatial import cKDTree as _KD
    XY = _np.array([(p["x"], p["y"]) for p in points])
    real = [i for i, p in enumerate(points) if p["cluster"] != -1]
    rtree = _KD(XY[real])
    groups = []
    for i, p in enumerate(points):
        c = p["cluster"]
        if c == -1:
            c = points[real[int(rtree.query(XY[i])[1])]]["cluster"]
        groups.append(grp_idx[CLUSTER_TO_MAJOR.get(c, "alzheimer")])

    packed = pack_force([(p["x"], p["y"]) for p in points], groups, spacing=PACK_SPACING)
    # then snap onto one shared hex lattice: clean tiling, no overlaps.
    packed = hex_snap(packed, PACK_SPACING)
    for p, (qx, qy) in zip(points, packed):
        p["px"], p["py"] = float(qx), float(qy)

    by_cluster: dict[int, list] = defaultdict(list)
    for p in points:
        by_cluster[p["cluster"]].append(p)

    fine_ids = sorted(c for c in by_cluster if c != -1)

    # Per-fine-cluster colour: shade the major's base colour so that sub-regions
    # inside a major (esp. the big Alzheimer's continent) are gently
    # distinguishable while still reading as one colour family.
    major_members: dict[str, list[int]] = defaultdict(list)
    for c in fine_ids:
        major_members[CLUSTER_TO_MAJOR[c]].append(c)
    # order each major's clusters by centroid x so the shading forms a smooth ramp
    for mk, members in major_members.items():
        members.sort(key=lambda c: sum(p["px"] for p in by_cluster[c]) / len(by_cluster[c]))

    fine_color: dict[int, str] = {}
    for mk, members in major_members.items():
        base = MAJORS[mk][1]
        n = len(members)
        for i, c in enumerate(members):
            # spread lightness across ~[-0.12, +0.24] of the base hue
            t = 0 if n == 1 else i / (n - 1)
            fine_color[c] = lighten(base, -0.12 + 0.36 * t)

    # centroids (packed display coords) for label placement
    def centroid(pts):
        return (sum(p["px"] for p in pts) / len(pts),
                sum(p["py"] for p in pts) / len(pts))

    fine_records = []
    for c in fine_ids:
        cx, cy = centroid(by_cluster[c])
        fine_records.append({
            "id": c,
            "major": CLUSTER_TO_MAJOR[c],
            "label": MINOR_LABELS.get(c, clusters[c]["label"]),
            "color": fine_color[c],
            "x": round(cx, 3),
            "y": round(cy, 3),
            "count": len(by_cluster[c]),
        })

    major_records = []
    for mk, (label, color) in MAJORS.items():
        member_pts = [p for c in major_members[mk] for p in by_cluster[c]]
        if not member_pts:
            continue
        cx, cy = centroid(member_pts)
        major_records.append({
            "id": mk, "label": label, "color": color,
            "x": round(cx, 3), "y": round(cy, 3), "count": len(member_pts),
        })
    major_records.sort(key=lambda m: -m["count"])

    # Assign noise points (-1) to the nearest fine cluster centroid so every dot
    # carries a sub-topic; colour comes from position (gradient) in the browser.
    fine_cx = {r["id"]: (r["x"], r["y"]) for r in fine_records}

    def nearest_fine(px, py):
        best, bd = None, 1e18
        for cid, (fx, fy) in fine_cx.items():
            d = (px - fx) ** 2 + (py - fy) ** 2
            if d < bd:
                bd, best = d, cid
        return best

    # points array: [px, py, fine_id, year]
    pt_rows = []
    titles = []
    ids = []
    idx_of = {}
    for i, p in enumerate(points):
        c = p["cluster"]
        if c == -1:
            c = nearest_fine(p["px"], p["py"])
        pt_rows.append([round(p["px"], 3), round(p["py"], 3), c, p["year"]])
        titles.append(p["title"])
        ids.append(p["paper_id"])
        idx_of[p["paper_id"]] = i

    # Citation graph: in-corpus paper->reference links (a paper cites another
    # paper in the corpus). Undirected here — "cites / is cited by". Emitted as
    # index pairs into `points` for hover-highlighting.
    def _norm_ref(r):
        if isinstance(r, dict):
            r = r.get("paper_id") or r.get("pmid")
        if r is None:
            return None
        r = str(r)
        return r if r.startswith("pmid:") else "pmid:" + r

    corpus_ids = set(idx_of)
    edge_set = set()
    papers_path = ROOT / "data/processed/topic-dynamics/papers.jsonl"
    if papers_path.exists():
        for line in papers_path.open():
            d = json.loads(line)
            src = d.get("paper_id")
            if src not in corpus_ids:
                continue
            for r in (d.get("references") or []):
                t = _norm_ref(r)
                if t in corpus_ids and t != src:
                    a, b = sorted((idx_of[src], idx_of[t]))
                    edge_set.add((a, b))
    edges = sorted(edge_set)

    years = [p["year"] for p in points]
    data = {
        "meta": {
            "model": manifest.get("model_id", "Qwen/Qwen3-Embedding-8B"),
            "spacing": PACK_SPACING,
            "n_papers": len(points),
            "n_major": len(major_records),
            "n_minor": len(fine_records),
            "n_edges": len(edges),
            "year_min": min(years),
            "year_max": max(years),
        },
        "majors": major_records,
        "minors": fine_records,
        "points": pt_rows,
        "titles": titles,
        "ids": ids,
        "edges": edges,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "atlas.json").write_text(json.dumps(data, separators=(",", ":")))

    print(f"wrote {OUT_DIR/'atlas.json'}")
    print(f"  {len(points)} papers | {len(major_records)} major topics | "
          f"{len(fine_records)} minor topics | {len(edges)} citation links")
    for m in major_records:
        print(f"    {m['count']:>4}  {m['label']}")


if __name__ == "__main__":
    main()
