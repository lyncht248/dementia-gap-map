#!/usr/bin/env python3
"""Build embedding theme-map artifacts for the dementia/GWAS paper corpus."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PAPERS_PATH = ROOT / "data/processed/topic-dynamics/papers.jsonl"
OUT_ROOT = ROOT / "data/exports/visual/embeddings"
SEED = 42

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "study", "analysis",
    "using", "based", "novel", "human", "disease", "diseases", "risk", "gene",
    "genes", "genetic", "genetics", "genome", "wide", "association", "associations",
    "identifies", "identify", "reveals", "role", "new", "insights", "into",
    "brain", "cell", "cells", "cellular", "single", "data", "large", "meta",
    "analyses", "variants", "variant", "loci", "locus", "common", "rare",
    "sequencing", "expression", "level", "levels", "protein", "proteins",
    "patients", "population", "populations", "cohort", "biomarkers", "biomarker",
    "clinical", "molecular", "functional", "are", "not", "its", "via", "per",
}

MODEL_CONFIG = {
    "qwen3-8b": {
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "dimension": 4096,
        "batch_size": {"mps": 4, "cuda": 32, "cpu": 1},
        "max_seq_length": 1024,
        "title": "Qwen3-Embedding-8B",
    },
    "specter2": {
        "model_id": "allenai/specter2",
        "base_model_id": "allenai/specter2_base",
        "dimension": 768,
        "batch_size": {"mps": 16, "cuda": 128, "cpu": 16},
        "max_seq_length": 512,
        "title": "SPECTER2",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODEL_CONFIG, default="qwen3-8b")
    parser.add_argument("--papers", type=Path, default=PAPERS_PATH)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--limit", type=int, help="Smoke-test on the first N papers.")
    parser.add_argument("--skip-embed", action="store_true", help="Reuse vectors.npy.")
    parser.add_argument("--device", choices=["auto", "mps", "cpu", "cuda"], default="auto")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def paper_text(paper: dict[str, Any]) -> str:
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    return f"{title}\n\n{abstract}".strip()


def tokens(title: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", (title or "").lower())
    return [word for word in words if word not in STOPWORDS]


def top_terms(titles: list[str], global_df: Counter[str], n_docs: int, k: int) -> list[str]:
    tf: Counter[str] = Counter()
    for title in titles:
        tf.update(set(tokens(title)))

    scored: list[tuple[float, str]] = []
    for term, count in tf.items():
        idf = math.log((n_docs + 1) / (global_df.get(term, 0) + 1)) + 1
        scored.append((count * idf, term))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [term for _, term in scored[:k]]


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def choose_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def encode_qwen(
    docs: list[str],
    out_path: Path,
    *,
    batch_size: int,
    device: str,
    max_seq_length: int,
) -> np.ndarray:
    import torch
    from sentence_transformers import SentenceTransformer

    config = MODEL_CONFIG["qwen3-8b"]
    model_kwargs = {}
    if device in {"mps", "cuda"}:
        model_kwargs["torch_dtype"] = torch.float16

    model = SentenceTransformer(
        config["model_id"],
        device=device,
        model_kwargs=model_kwargs,
    )
    model.max_seq_length = max_seq_length

    dim = config["dimension"]
    tmp_path = out_path.with_suffix(".tmp.npy")
    mmap = np.lib.format.open_memmap(tmp_path, mode="w+", dtype=np.float16, shape=(len(docs), dim))

    for start in range(0, len(docs), batch_size):
        end = min(start + batch_size, len(docs))
        batch = model.encode(
            docs[start:end],
            batch_size=batch_size,
            normalize_embeddings=False,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        if batch.shape[1] != dim:
            raise RuntimeError(f"Expected embedding dimension {dim}, got {batch.shape[1]}")
        mmap[start:end] = batch.astype(np.float16)
        mmap.flush()
        print(f"[embed] {end}/{len(docs)}", flush=True)

    del mmap
    tmp_path.replace(out_path)
    return np.load(out_path)


def encode_specter2(
    papers: list[dict[str, Any]],
    out_path: Path,
    *,
    batch_size: int,
    device: str,
    max_seq_length: int,
) -> np.ndarray:
    import torch
    from adapters import AutoAdapterModel
    from transformers import AutoTokenizer

    config = MODEL_CONFIG["specter2"]
    tokenizer = AutoTokenizer.from_pretrained(config["base_model_id"])
    model = AutoAdapterModel.from_pretrained(config["base_model_id"])
    adapter_name = model.load_adapter(config["model_id"], source="hf", load_as="specter2")
    model.set_active_adapters(adapter_name)
    print(f"[adapter] active={model.active_adapters}", flush=True)
    model.to(device)
    model.eval()

    dim = config["dimension"]
    tmp_path = out_path.with_suffix(".tmp.npy")
    mmap = np.lib.format.open_memmap(tmp_path, mode="w+", dtype=np.float16, shape=(len(papers), dim))
    texts = [
        (paper.get("title") or "").strip()
        + tokenizer.sep_token
        + (paper.get("abstract") or "").strip()
        for paper in papers
    ]

    for start in range(0, len(texts), batch_size):
        end = min(start + batch_size, len(texts))
        inputs = tokenizer(
            texts[start:end],
            padding=True,
            truncation=True,
            return_tensors="pt",
            return_token_type_ids=False,
            max_length=max_seq_length,
        )
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            output = model(**inputs)
            batch = output.last_hidden_state[:, 0, :].detach().cpu().numpy()
        if batch.shape[1] != dim:
            raise RuntimeError(f"Expected embedding dimension {dim}, got {batch.shape[1]}")
        mmap[start:end] = batch.astype(np.float16)
        mmap.flush()
        print(f"[embed] {end}/{len(texts)}", flush=True)

    del mmap
    tmp_path.replace(out_path)
    return np.load(out_path)


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    vectors32 = vectors.astype(np.float32, copy=False)
    norms = np.linalg.norm(vectors32, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors32 / norms


def run_umap_and_hdbscan(vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    import hdbscan
    import umap

    projection_params = {
        "n_neighbors": 15,
        "min_dist": 0.1,
        "metric": "cosine",
        "n_components": 2,
        "random_state": SEED,
    }
    cluster_umap_params = {
        "n_neighbors": 15,
        "min_dist": 0.0,
        "metric": "cosine",
        "n_components": 10,
        "random_state": SEED,
    }
    hdbscan_params = {
        "min_cluster_size": 25,
        "min_samples": 5,
        "metric": "euclidean",
    }

    xy = umap.UMAP(**projection_params).fit_transform(vectors)
    cluster_space = umap.UMAP(**cluster_umap_params).fit_transform(vectors)
    labels = hdbscan.HDBSCAN(**hdbscan_params).fit_predict(cluster_space)
    return xy, labels, {
        "projection_umap": projection_params,
        "cluster_umap": cluster_umap_params,
        "hdbscan": hdbscan_params,
    }


def median_nearest_neighbor(coords: np.ndarray) -> float:
    from sklearn.neighbors import NearestNeighbors

    neighbors = NearestNeighbors(n_neighbors=2).fit(coords)
    distances, _ = neighbors.kneighbors(coords)
    return float(np.median(distances[:, 1]))


def pack_points(coords: np.ndarray, radius: float, *, ticks: int = 500) -> np.ndarray:
    """Deterministic anchored collision relaxation in data coordinates."""
    from scipy.spatial import cKDTree

    packed = coords.astype(np.float64).copy()
    anchors = packed.copy()
    min_dist = radius * 2.05
    rng = np.random.default_rng(SEED)
    packed += rng.normal(0, radius * 0.01, packed.shape)

    for tick in range(ticks):
        strength = max(0.015, 0.08 * (1 - tick / ticks))
        packed += (anchors - packed) * strength

        tree = cKDTree(packed)
        pairs = list(tree.query_pairs(min_dist))
        if not pairs:
            break
        rng.shuffle(pairs)

        for i, j in pairs:
            delta = packed[j] - packed[i]
            dist = float(np.hypot(delta[0], delta[1]))
            if dist < 1e-9:
                angle = rng.uniform(0, math.tau)
                direction = np.array([math.cos(angle), math.sin(angle)])
                dist = 1e-9
            else:
                direction = delta / dist
            push = (min_dist - dist) * 0.52
            packed[i] -= direction * push
            packed[j] += direction * push

    # Final unanchored passes make the non-overlap guarantee explicit.
    for _ in range(250):
        tree = cKDTree(packed)
        pairs = list(tree.query_pairs(min_dist))
        if not pairs:
            break
        rng.shuffle(pairs)
        for i, j in pairs:
            delta = packed[j] - packed[i]
            dist = float(np.hypot(delta[0], delta[1]))
            if dist < 1e-9:
                angle = rng.uniform(0, math.tau)
                direction = np.array([math.cos(angle), math.sin(angle)])
                dist = 1e-9
            else:
                direction = delta / dist
            push = (min_dist - dist) * 0.51
            packed[i] -= direction * push
            packed[j] += direction * push

    return packed


def cluster_records(labels: np.ndarray, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global_df: Counter[str] = Counter()
    for paper in papers:
        global_df.update(set(tokens(paper.get("title", ""))))

    rows: list[dict[str, Any]] = []
    for cluster in sorted(set(int(label) for label in labels if label != -1)):
        titles = [paper.get("title", "") for paper, label in zip(papers, labels) if int(label) == cluster]
        terms = top_terms(titles, global_df, len(papers), 8)
        rows.append({
            "cluster": cluster,
            "size": len(titles),
            "label": " / ".join(terms[:4]) if terms else f"cluster {cluster}",
            "top_terms": terms,
        })
    return sorted(rows, key=lambda row: (-row["size"], row["cluster"]))


def build_points(
    papers: list[dict[str, Any]],
    xy: np.ndarray,
    packed: np.ndarray,
    labels: np.ndarray,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for paper, raw, display, label in zip(papers, xy, packed, labels):
        rows.append({
            "paper_id": paper["paper_id"],
            "x": round(float(raw[0]), 6),
            "y": round(float(raw[1]), 6),
            "px": round(float(display[0]), 6),
            "py": round(float(display[1]), 6),
            "cluster": int(label),
            "title": paper.get("title") or "",
            "year": paper.get("year"),
        })
    return rows


def plot_map(
    out_dir: Path,
    points: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    *,
    title: str,
) -> None:
    import matplotlib.pyplot as plt
    import plotly.express as px

    label_by_cluster = {row["cluster"]: row["label"] for row in clusters}
    cluster_values = sorted({row["cluster"] for row in points if row["cluster"] != -1})
    cmap = plt.get_cmap("tab20", max(1, len(cluster_values)))
    color_by_cluster = {cluster: cmap(i % 20) for i, cluster in enumerate(cluster_values)}

    fig, ax = plt.subplots(figsize=(14, 11), dpi=180)
    for cluster in [-1] + cluster_values:
        rows = [row for row in points if row["cluster"] == cluster]
        if not rows:
            continue
        color = "#d4d4d4" if cluster == -1 else color_by_cluster[cluster]
        alpha = 0.35 if cluster == -1 else 0.82
        ax.scatter(
            [row["px"] for row in rows],
            [row["py"] for row in rows],
            s=14,
            c=[color],
            edgecolors="white",
            linewidths=0.22,
            alpha=alpha,
        )

    for cluster in cluster_values:
        rows = [row for row in points if row["cluster"] == cluster]
        cx = float(np.median([row["px"] for row in rows]))
        cy = float(np.median([row["py"] for row in rows]))
        ax.text(cx, cy, label_by_cluster.get(cluster, str(cluster)), fontsize=8, ha="center", va="center")

    ax.set_title(title, fontsize=16)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_dir / "map.png")
    plt.close(fig)

    html_rows = [{
        "paper_id": row["paper_id"],
        "x": row["px"],
        "y": row["py"],
        "cluster": str(row["cluster"]),
        "title": row["title"],
        "year": row["year"],
        "label": label_by_cluster.get(row["cluster"], "noise"),
    } for row in points]
    plotly_fig = px.scatter(
        html_rows,
        x="x",
        y="y",
        color="cluster",
        hover_data=["paper_id", "title", "year", "label"],
        title=title,
        width=1100,
        height=850,
    )
    plotly_fig.update_traces(marker={"size": 7, "opacity": 0.78})
    plotly_fig.update_yaxes(scaleanchor="x", scaleratio=1, visible=False)
    plotly_fig.update_xaxes(visible=False)
    plotly_fig.write_html(out_dir / "map.html", include_plotlyjs="cdn")


def git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def main() -> None:
    args = parse_args()
    config = MODEL_CONFIG[args.model]
    out_dir = args.out_dir or OUT_ROOT / args.model
    out_dir.mkdir(parents=True, exist_ok=True)

    papers = read_jsonl(args.papers)
    if args.limit:
        papers = papers[:args.limit]
    docs = [paper_text(paper) for paper in papers]
    ids = [paper["paper_id"] for paper in papers]
    n_no_abstract = sum(1 for paper in papers if not (paper.get("abstract") or "").strip())
    write_json(out_dir / "ids.json", ids)

    start_time = time.perf_counter()
    device = choose_device(args.device)
    batch_size = args.batch_size or config["batch_size"].get(device, 1)
    vectors_path = out_dir / "vectors.npy"

    if args.skip_embed:
        vectors = np.load(vectors_path)
    elif args.model == "qwen3-8b":
        vectors = encode_qwen(
            docs,
            vectors_path,
            batch_size=batch_size,
            device=device,
            max_seq_length=config["max_seq_length"],
        )
    elif args.model == "specter2":
        vectors = encode_specter2(
            papers,
            vectors_path,
            batch_size=batch_size,
            device=device,
            max_seq_length=config["max_seq_length"],
        )
    else:
        raise AssertionError(args.model)

    normalized = l2_normalize(vectors)
    xy, labels, params = run_umap_and_hdbscan(normalized)
    radius = 0.45 * median_nearest_neighbor(xy)
    packed = pack_points(xy, radius)

    clusters = cluster_records(labels, papers)
    points = build_points(papers, xy, packed, labels)
    write_jsonl(out_dir / "points.jsonl", points)
    write_jsonl(out_dir / "clusters.jsonl", clusters)
    plot_map(
        out_dir,
        points,
        clusters,
        title=f"{config['title']} - dementia/GWAS corpus ({len(papers):,} papers)",
    )

    elapsed = time.perf_counter() - start_time
    manifest = {
        "model": args.model,
        "model_id": config["model_id"],
        "embedding_dimension": int(vectors.shape[1]),
        "n_papers": len(papers),
        "n_no_abstract": n_no_abstract,
        "n_clusters": len(clusters),
        "n_noise": int(np.sum(labels == -1)),
        "params": params,
        "packing": {
            "method": "anchored_collision_relaxation",
            "radius": radius,
            "min_center_distance": radius * 2.05,
            "seed": SEED,
        },
        "package_versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "sentence-transformers": package_version("sentence-transformers"),
            "torch": package_version("torch"),
            "umap-learn": package_version("umap-learn"),
            "hdbscan": package_version("hdbscan"),
            "matplotlib": package_version("matplotlib"),
            "adapters": package_version("adapters"),
            "pandas": package_version("pandas"),
            "plotly": package_version("plotly"),
            "scikit-learn": package_version("scikit-learn"),
            "scipy": package_version("scipy"),
            "transformers": package_version("transformers"),
        },
        "wall_clock_seconds": round(elapsed, 3),
        "device": device,
        "batch_size": batch_size,
        "max_seq_length": config["max_seq_length"],
        "git_commit": git_commit(),
    }
    write_json(out_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
