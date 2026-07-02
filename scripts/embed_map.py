#!/usr/bin/env python3
"""Embed the Track A corpus (title + abstract), project to 2D, cluster into
themes, and pack the points into a clean non-overlapping bubble layout.

Implements the bake-off contract in docs/embedding-benchmark.md. One model per
run, selected with --model. Downstream (UMAP + HDBSCAN + labels + packing) is
identical across models so the maps are directly comparable.

Usage:
    python scripts/embed_map.py --model specter2
    python scripts/embed_map.py --model qwen3-8b
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/processed/topic-dynamics/papers.jsonl"

# Reuse Track A's tokenizer + stopwords for cluster labels so they match the
# rest of the project's style (topic-dynamics/topics/cluster/topics.py).
sys.path.insert(0, str(ROOT / "topic-dynamics"))
try:
    from topics.cluster.topics import _STOPWORDS, _tokens  # type: ignore
except Exception:  # pragma: no cover - fallback if import path shifts
    _STOPWORDS = {
        "the", "and", "for", "with", "from", "that", "this", "study", "using",
        "based", "novel", "disease", "risk", "gene", "genes", "genetic",
    }

    def _tokens(title: str) -> list[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", (title or "").lower())
        return [w for w in words if w not in _STOPWORDS]


SEED = 42

MODELS = {
    "specter2": {"dim": 768, "hf": "allenai/specter2_base"},
    "qwen3-8b": {"dim": 4096, "hf": "Qwen/Qwen3-Embedding-8B"},
}


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #
def load_corpus() -> list[dict]:
    papers = []
    with open(CORPUS) as fh:
        for line in fh:
            line = line.strip()
            if line:
                papers.append(json.loads(line))
    return papers


# --------------------------------------------------------------------------- #
# Embedding backends
# --------------------------------------------------------------------------- #
def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def embed_specter2(papers: list[dict], batch_size: int, log) -> np.ndarray:
    """SPECTER2: base model + proximity adapter, input = title[SEP]abstract."""
    import torch
    from transformers import AutoTokenizer
    from adapters import AutoAdapterModel

    device = pick_device()
    log(f"[specter2] device={device}")
    tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
    model = AutoAdapterModel.from_pretrained("allenai/specter2_base")
    # Proximity adapter — the right one for title+abstract retrieval/clustering.
    model.load_adapter("allenai/specter2", source="hf", load_as="proximity", set_active=True)
    model.to(device).eval()

    texts = [
        (p.get("title") or "").strip() + tok.sep_token + (p.get("abstract") or "").strip()
        for p in papers
    ]
    out = np.empty((len(texts), 768), dtype=np.float32)
    t0 = time.time()
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            enc = tok(
                batch,
                padding=True,
                truncation=True,
                return_tensors="pt",
                max_length=512,
            ).to(device)
            # SPECTER2 embedding = CLS token of last hidden state.
            emb = model(**enc).last_hidden_state[:, 0, :]
            out[i : i + len(batch)] = emb.cpu().numpy()
            if (i // batch_size) % 20 == 0:
                done = i + len(batch)
                rate = done / max(time.time() - t0, 1e-6)
                log(f"[specter2] {done}/{len(texts)}  ({rate:.1f} docs/s)")
    log(f"[specter2] embedded {len(texts)} docs in {time.time()-t0:.0f}s")
    return out


def embed_qwen3(papers: list[dict], batch_size: int, log) -> np.ndarray:
    """Qwen3-Embedding-8B via sentence-transformers, document = title\\n\\nabstract."""
    from sentence_transformers import SentenceTransformer

    device = pick_device()
    log(f"[qwen3-8b] device={device}")
    model = SentenceTransformer("Qwen/Qwen3-Embedding-8B", device=device)
    docs = [
        (f"{(p.get('title') or '').strip()}\n\n{(p.get('abstract') or '').strip()}").strip()
        for p in papers
    ]
    t0 = time.time()
    emb = model.encode(
        docs,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=False,  # we L2-normalize ourselves downstream
        convert_to_numpy=True,
    )
    log(f"[qwen3-8b] embedded {len(docs)} docs in {time.time()-t0:.0f}s")
    return emb.astype(np.float32)


# --------------------------------------------------------------------------- #
# Projection + clustering
# --------------------------------------------------------------------------- #
def l2_normalize(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return x / n


def project_and_cluster(vecs: np.ndarray, log):
    import umap
    import hdbscan

    log("[umap] 2D projection for the plot")
    xy = umap.UMAP(
        n_neighbors=15, min_dist=0.1, metric="cosine",
        n_components=2, random_state=SEED,
    ).fit_transform(vecs)

    log("[umap] 10D projection for clustering")
    hi = umap.UMAP(
        n_neighbors=15, min_dist=0.0, metric="cosine",
        n_components=10, random_state=SEED,
    ).fit_transform(vecs)

    log("[hdbscan] clustering")
    labels = hdbscan.HDBSCAN(
        min_cluster_size=25, min_samples=5, metric="euclidean",
    ).fit_predict(hi)
    return np.asarray(xy, dtype=np.float32), np.asarray(labels, dtype=int)


def label_clusters(papers, labels, k_terms=8):
    import math

    global_df: Counter = Counter()
    for p in papers:
        global_df.update(set(_tokens(p.get("title", ""))))
    n_docs = len(papers)

    clusters = []
    for cid in sorted(set(labels)):
        if cid == -1:
            continue
        members = [papers[i] for i in range(len(papers)) if labels[i] == cid]
        tf: Counter = Counter()
        for p in members:
            tf.update(set(_tokens(p.get("title", ""))))
        scored = []
        for term, c in tf.items():
            idf = math.log((n_docs + 1) / (global_df.get(term, 0) + 1)) + 1
            scored.append((c * idf, term))
        scored.sort(key=lambda x: (-x[0], x[1]))
        top = [t for _, t in scored[:k_terms]]
        clusters.append({
            "cluster": int(cid),
            "size": len(members),
            "label": " ".join(top[:4]),
            "top_terms": top,
        })
    clusters.sort(key=lambda c: -c["size"])
    return clusters


# --------------------------------------------------------------------------- #
# Bubble packing (no overlaps) — §3a
# --------------------------------------------------------------------------- #
def pack_bubbles(xy: np.ndarray, log):
    """Return packed coords + (method, radius) as clean non-overlapping bubbles.

    Uses deterministic collision relaxation anchored to the UMAP layout. We chose
    this over RasterFairy hex-grid snapping because the corpus layout has organic
    cluster outlines and detached islands; RasterFairy fills a single bounding
    grid and would merge those islands, whereas relaxation preserves the gaps
    (matching the target theme-map look) while removing every overlap."""
    from scipy.spatial import cKDTree

    # radius = 0.45 * median nearest-neighbour distance of the raw layout
    tree = cKDTree(xy)
    d, _ = tree.query(xy, k=2)
    nn = d[:, 1]
    r = float(0.45 * np.median(nn))

    log("[pack] collision relaxation (anchored, damped)")
    # Deterministic collision relaxation: iteratively push apart any two points
    # closer than 2r. Anchored to the original UMAP position (strength decays to
    # zero) so global structure is preserved early, then a final anchor-free
    # phase guarantees no overlaps. Equivalent in spirit to d3-force forceCollide.
    pos = xy.astype(np.float64).copy()
    anchor = xy.astype(np.float64).copy()
    # Pack to a slightly padded separation so that when markers are later drawn
    # at radius r, the small margin guarantees no *visible* overlap even if a
    # few many-body pairs settle right at the target distance.
    pad = 1.06
    min_d = 2.0 * r * pad

    def separate(positions, step=0.9):
        tree = cKDTree(positions)
        pairs = tree.query_pairs(min_d, output_type="ndarray")
        if len(pairs) == 0:
            return positions, 0
        disp = np.zeros_like(positions)
        a, b = pairs[:, 0], pairs[:, 1]
        delta = positions[a] - positions[b]
        dist = np.linalg.norm(delta, axis=1)
        # deterministic separation direction for exact-overlap ties (index order)
        tie = dist < 1e-9
        delta[tie] = (a[tie] - b[tie])[:, None] * np.array([1e-6, -1e-6])
        dist[tie] = np.linalg.norm(delta[tie], axis=1)
        push = (step * (min_d - dist) * 0.5)[:, None] * (delta / dist[:, None])
        np.add.at(disp, a, push)
        np.add.at(disp, b, -push)
        return positions + disp, len(pairs)

    N_MAIN = 400
    for it in range(N_MAIN):
        pos, _ = separate(pos)
        strength = 0.05 * (1.0 - it / N_MAIN)  # decays to 0
        pos += (anchor - pos) * strength
    # Final anchor-free phase: separate (with damping) until no pairs within the
    # padded distance, or budget exhausted.
    conv = None
    for it in range(1200):
        pos, n_pairs = separate(pos)
        if n_pairs == 0:
            conv = N_MAIN + it
            break
    # Report residual overlaps measured at the TRUE draw radius (2r), which is
    # what matters visually; the pad above makes this ~always zero.
    hard = len(cKDTree(pos).query_pairs(2.0 * r))
    if conv is not None:
        log(f"[pack] converged after {conv} iters; overlaps at draw-radius={hard}")
    else:
        log(f"[pack] budget reached; overlaps at draw-radius={hard}")
    return pos.astype(np.float32), "collision-relaxation", r


# --------------------------------------------------------------------------- #
# Plot
# --------------------------------------------------------------------------- #
_PALETTE = [
    "#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860",
    "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD", "#E377C2", "#17BECF",
    "#AEC7E8", "#FFBB78", "#98DF8A", "#FF9896", "#C5B0D5", "#9EDAE5",
]


def make_plots(pxy, labels, clusters, papers, model, out_dir, radius, log):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Circle
    import matplotlib.patheffects as pe

    cmap = {c["cluster"]: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(clusters)}
    colors = [cmap.get(int(l), "#E6E6E6") for l in labels]

    # Draw each paper as a real circle of the packing radius so the rendered
    # bubbles exactly match the zero-overlap geometry (radius slightly < packing
    # target => guaranteed clean tiling, never overlapping).
    fig, ax = plt.subplots(figsize=(16, 14), dpi=130)
    circles = [Circle((pxy[i, 0], pxy[i, 1]), radius=radius) for i in range(len(pxy))]
    ax.add_collection(PatchCollection(circles, facecolor=colors, edgecolor="none"))

    # Label only the largest clusters (short label) with de-overlapping so the
    # dense centre stays legible. Greedy: skip a label if it lands too close to
    # one already placed.
    placed = []
    span = max(np.ptp(pxy[:, 0]), np.ptp(pxy[:, 1]))
    min_gap = span * 0.045
    for c in clusters[:22]:  # clusters is sorted by size desc
        cid = c["cluster"]
        pts = pxy[labels == cid]
        if len(pts) == 0:
            continue
        cx, cy = float(np.median(pts[:, 0])), float(np.median(pts[:, 1]))
        if any((cx - px) ** 2 + (cy - py) ** 2 < min_gap ** 2 for px, py in placed):
            continue
        placed.append((cx, cy))
        short = " ".join(c["label"].split()[:3])
        ax.text(cx, cy, short, fontsize=12.5, fontweight="bold", ha="center",
                va="center", color=cmap[cid], zorder=5,
                path_effects=[pe.withStroke(linewidth=3.5, foreground="white")])
    m = radius * 3
    ax.set_xlim(pxy[:, 0].min() - m, pxy[:, 0].max() + m)
    ax.set_ylim(pxy[:, 1].min() - m, pxy[:, 1].max() + m)
    ax.set_title(f"{model} — dementia/GWAS corpus ({len(papers):,} papers)",
                 fontsize=16, fontweight="bold")
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_dir / "map.png", bbox_inches="tight")
    plt.close(fig)
    log(f"[plot] wrote {out_dir/'map.png'}")

    # Interactive HTML (optional but preferred)
    try:
        import plotly.graph_objects as go

        hover = [
            f"{(p.get('title') or '')[:120]}<br>{p.get('year','')} — cluster {int(l)}"
            for p, l in zip(papers, labels)
        ]
        fig = go.Figure(go.Scattergl(
            x=pxy[:, 0], y=pxy[:, 1], mode="markers",
            marker=dict(size=6, color=[c for c in colors], line=dict(width=0)),
            text=hover, hoverinfo="text",
        ))
        anns = [
            dict(x=pxy[labels == c["cluster"]][:, 0].mean(),
                 y=pxy[labels == c["cluster"]][:, 1].mean(),
                 text=f"<b>{c['label']}</b>", showarrow=False,
                 font=dict(size=13))
            for c in clusters if (labels == c["cluster"]).any()
        ]
        fig.update_layout(
            title=f"{model} — dementia/GWAS corpus ({len(papers):,} papers)",
            annotations=anns, plot_bgcolor="white", width=1200, height=1000,
            xaxis=dict(visible=False, scaleanchor="y"), yaxis=dict(visible=False),
        )
        fig.write_html(str(out_dir / "map.html"), include_plotlyjs="cdn")
        log(f"[plot] wrote {out_dir/'map.html'}")
    except Exception as e:  # pragma: no cover
        log(f"[plot] skipped map.html ({e})")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=list(MODELS))
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None, help="debug: cap #papers")
    args = ap.parse_args()

    def log(msg):
        print(msg, flush=True)

    model = args.model
    out_dir = ROOT / f"data/exports/visual/embeddings/{model}"
    out_dir.mkdir(parents=True, exist_ok=True)

    papers = load_corpus()
    if args.limit:
        papers = papers[: args.limit]
    ids = [p["paper_id"] for p in papers]
    n_no_abs = sum(1 for p in papers if not (p.get("abstract") or "").strip())
    log(f"[corpus] {len(papers)} papers, {n_no_abs} without abstract")

    t_start = time.time()
    bs = args.batch_size or (16 if model == "specter2" else 8)
    if model == "specter2":
        vecs = embed_specter2(papers, bs, log)
    else:
        vecs = embed_qwen3(papers, bs, log)

    vecs = l2_normalize(vecs)
    xy, labels = project_and_cluster(vecs, log)
    clusters = label_clusters(papers, labels, k_terms=8)
    pxy, pack_method, radius = pack_bubbles(xy, log)
    wall = time.time() - t_start

    n_noise = int((labels == -1).sum())
    log(f"[done] {len(clusters)} clusters, {n_noise} noise, {wall:.0f}s")

    # ---- write artifacts (§2 contract) ----
    np.save(out_dir / "vectors.npy", vecs.astype(np.float16))
    (out_dir / "ids.json").write_text(json.dumps(ids))
    with open(out_dir / "points.jsonl", "w") as fh:
        for i, p in enumerate(papers):
            fh.write(json.dumps({
                "paper_id": p["paper_id"],
                "x": round(float(xy[i, 0]), 4),
                "y": round(float(xy[i, 1]), 4),
                "px": round(float(pxy[i, 0]), 4),
                "py": round(float(pxy[i, 1]), 4),
                "cluster": int(labels[i]),
                "title": p.get("title", ""),
                "year": p.get("year"),
            }) + "\n")
    with open(out_dir / "clusters.jsonl", "w") as fh:
        for c in clusters:
            fh.write(json.dumps(c) + "\n")

    import importlib.metadata as md

    def ver(pkg):
        try:
            return md.version(pkg)
        except Exception:
            return None

    manifest = {
        "model": MODELS[model]["hf"],
        "model_key": model,
        "dim": int(vecs.shape[1]),
        "n_papers": len(papers),
        "n_no_abstract": n_no_abs,
        "n_clusters": len(clusters),
        "n_noise": n_noise,
        "umap": {"plot": {"n_neighbors": 15, "min_dist": 0.1, "n_components": 2},
                 "cluster": {"n_neighbors": 15, "min_dist": 0.0, "n_components": 10},
                 "metric": "cosine", "random_state": SEED},
        "hdbscan": {"min_cluster_size": 25, "min_samples": 5, "metric": "euclidean"},
        "packing": {"method": pack_method, "radius": round(radius, 5)},
        "device": pick_device(),
        "wall_clock_s": round(wall, 1),
        "versions": {k: ver(k) for k in
                     ["torch", "transformers", "adapters", "sentence-transformers",
                      "umap-learn", "hdbscan", "numpy", "scipy", "rasterfairy-py3"]},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log(f"[manifest] {json.dumps(manifest['packing'])}  dim={manifest['dim']}")

    make_plots(pxy, labels, clusters, papers, model, out_dir, radius, log)
    log(f"[artifacts] written to {out_dir}")


if __name__ == "__main__":
    main()
