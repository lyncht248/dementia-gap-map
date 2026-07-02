#!/usr/bin/env python3
"""Embedding theme-map builder (SPECTER2 bake-off Run B).

Embeds every Track A paper (title + abstract) with SPECTER2, projects to 2D with
UMAP, clusters with HDBSCAN, labels each cluster by TF-IDF over titles, and packs
the points into non-overlapping bubbles. Deterministic (all seeds = 42).

Follows docs/embedding-benchmark.md. Outputs to
    data/exports/visual/embeddings/<model>/
        vectors.npy ids.json points.jsonl clusters.jsonl map.png map.html manifest.json

Usage:  python scripts/embed_map.py --model specter2
vectors.npy is cached: delete it to re-embed.
"""
import argparse, json, os, time
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "data/processed/topic-dynamics/papers.jsonl")

SEED = 42


def read_papers():
    rows = []
    with open(IN, encoding="utf8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def embed_specter2(papers, sep_token_holder):
    import torch
    from transformers import AutoTokenizer
    from adapters import AutoAdapterModel

    tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
    model = AutoAdapterModel.from_pretrained("allenai/specter2_base")
    adapter_name = model.load_adapter("allenai/specter2", source="hf", set_active=True)
    model.set_active_adapters(adapter_name)  # the proximity ([PRX]) adapter runs in forward
    assert model.active_adapters is not None, "SPECTER2 proximity adapter not active"
    print("active adapter:", model.active_adapters)
    model.eval()
    sep_token_holder["sep"] = tok.sep_token

    docs = []
    n_no_abs = 0
    for p in papers:
        title = (p.get("title") or "").strip()
        abstract = (p.get("abstract") or "").strip()
        if not abstract:
            n_no_abs += 1
            docs.append(title)
        else:
            docs.append(title + tok.sep_token + abstract)

    vecs = np.zeros((len(docs), 768), dtype=np.float32)
    B = 32
    t0 = time.time()
    for i in range(0, len(docs), B):
        batch = docs[i:i + B]
        inp = tok(batch, padding=True, truncation=True, return_tensors="pt", max_length=512)
        with torch.no_grad():
            out = model(**inp)
        vecs[i:i + B] = out.last_hidden_state[:, 0, :].cpu().numpy()  # CLS token
        if (i // B) % 20 == 0:
            print(f"  embedded {i + len(batch)}/{len(docs)}  ({time.time() - t0:.0f}s)")
    return vecs, n_no_abs


# --- TF-IDF cluster labels (title terms), matching the project's simple style ---
STOP = set((
    "the a an and or of to in for with on by from as at is are was were be been being "
    "this that these those we our study studies analysis analyses using based between among within "
    "results result method methods conclusion conclusions background objective aim aims "
    "disease diseases patient patients gene genes genetic genetics associated association "
    "associations risk role effect effects human cell cells clinical use used novel new via "
    "case cases control controls cohort data model models level levels high low increased "
    "expression protein proteins function functional related evidence potential findings "
    "identify identified identification investigate investigated assessment "
    "genome-wide wide polygenic variant variants loci locus trait traits phenotype phenotypes "
    "score scores sample samples multivariate summary statistics statistical prediction regression "
    "meta meta-analysis large-scale significant significance approach approaches dataset datasets "
    "population populations sequencing heritability estimation alzheimer dementia brain cognitive "
    "cognition biomarker biomarkers late-onset onset age-related"
).split(" "))


def tokenize(title):
    out = []
    for t in (title or "").lower().replace("/", " ").split():
        t = "".join(ch for ch in t if ch.isalnum() or ch == "-").strip("-")
        if len(t) >= 4 and t not in STOP and not t.isdigit():
            out.append(t)
    return out


def label_clusters(papers, labels):
    from collections import defaultdict, Counter
    import math
    members = defaultdict(list)
    for p, c in zip(papers, labels):
        members[int(c)].append(p)
    # global doc frequency across clusters for a light idf
    cluster_terms = {}
    df = Counter()
    for c, ps in members.items():
        if c == -1:
            continue
        tf = Counter()
        for p in ps:
            for t in set(tokenize(p.get("title"))):
                tf[t] += 1
        cluster_terms[c] = tf
        for t in tf:
            df[t] += 1
    ncl = len(cluster_terms) or 1
    out = {}
    for c, tf in cluster_terms.items():
        n = len(members[c]) or 1
        ranked = sorted(
            ((t, (f / n) * math.log((ncl + 1) / (df[t] + 1))) for t, f in tf.items() if f >= 2),
            key=lambda kv: kv[1], reverse=True,
        )
        top = [t for t, _ in ranked[:8]]
        label = " ".join(w.capitalize() for w in top[:4]) or f"cluster {c}"
        out[c] = {"label": label, "top_terms": top, "size": len(members[c])}
    return out, members


def pack_bubbles(xy, radius):
    """Anchored collision relaxation -> non-overlapping display coords. KDTree for
    speed; deterministic (no RNG)."""
    from sklearn.neighbors import KDTree
    pos = xy.astype(np.float64).copy()
    anchor = xy.astype(np.float64).copy()
    n = len(pos)
    for _ in range(120):
        tree = KDTree(pos)
        pairs = tree.query_radius(pos, r=2 * radius)
        disp = np.zeros_like(pos)
        for i, nbrs in enumerate(pairs):
            for j in nbrs:
                if j <= i:
                    continue
                d = pos[j] - pos[i]
                dist = float(np.hypot(d[0], d[1])) or 1e-6
                overlap = 2 * radius - dist
                if overlap > 0:
                    u = d / dist
                    disp[i] -= u * overlap * 0.5
                    disp[j] += u * overlap * 0.5
        pos += disp
        pos += (anchor - pos) * 0.06  # gentle pull back toward true position
    return pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="specter2", choices=["specter2", "qwen3-8b"])
    args = ap.parse_args()
    outdir = os.path.join(ROOT, "data/exports/visual/embeddings", args.model)
    os.makedirs(outdir, exist_ok=True)

    papers = read_papers()
    ids = [p["paper_id"] for p in papers]
    print(f"{len(papers)} papers")

    sep_holder = {}
    vec_path = os.path.join(outdir, "vectors.npy")
    manifest = {"model": args.model, "n_papers": len(papers), "seed": SEED}
    if os.path.exists(vec_path):
        print("loading cached vectors.npy")
        vecs = np.load(vec_path).astype(np.float32)
        n_no_abs = sum(1 for p in papers if not (p.get("abstract") or "").strip())
    else:
        t0 = time.time()
        if args.model == "specter2":
            vecs, n_no_abs = embed_specter2(papers, sep_holder)
        else:
            raise SystemExit("only specter2 implemented in this environment")
        manifest["embed_seconds"] = round(time.time() - t0, 1)
        np.save(vec_path, vecs.astype(np.float16))
    manifest["D"] = int(vecs.shape[1])
    manifest["n_no_abstract"] = int(n_no_abs)
    with open(os.path.join(outdir, "ids.json"), "w") as fh:
        json.dump(ids, fh)

    # L2 normalize (cosine geometry)
    vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)

    import umap, hdbscan
    print("UMAP 2D…")
    xy = umap.UMAP(n_neighbors=15, min_dist=0.1, metric="cosine", n_components=2,
                   random_state=SEED).fit_transform(vecs)
    print("UMAP 10D + HDBSCAN…")
    x10 = umap.UMAP(n_neighbors=15, min_dist=0.0, metric="cosine", n_components=10,
                    random_state=SEED).fit_transform(vecs)
    labels = hdbscan.HDBSCAN(min_cluster_size=25, min_samples=5,
                             metric="euclidean").fit_predict(x10)
    n_clusters = len(set(int(c) for c in labels) - {-1})
    n_noise = int((labels == -1).sum())
    print(f"  {n_clusters} clusters, {n_noise} noise")

    clabels, members = label_clusters(papers, labels)

    # packing radius from median nearest-neighbour distance of raw UMAP points
    from sklearn.neighbors import KDTree
    tree = KDTree(xy)
    d, _ = tree.query(xy, k=2)
    nn = float(np.median(d[:, 1]))
    radius = 0.45 * nn
    print(f"packing bubbles (r={radius:.4f})…")
    pxy = pack_bubbles(np.asarray(xy), radius)

    # write points.jsonl
    with open(os.path.join(outdir, "points.jsonl"), "w") as fh:
        for i, p in enumerate(papers):
            fh.write(json.dumps({
                "paper_id": p["paper_id"],
                "x": round(float(xy[i, 0]), 4), "y": round(float(xy[i, 1]), 4),
                "px": round(float(pxy[i, 0]), 4), "py": round(float(pxy[i, 1]), 4),
                "cluster": int(labels[i]),
                "title": p.get("title"), "year": p.get("year"),
            }) + "\n")

    with open(os.path.join(outdir, "clusters.jsonl"), "w") as fh:
        for c in sorted(clabels):
            fh.write(json.dumps({"cluster": c, **clabels[c]}) + "\n")

    manifest.update({
        "n_clusters": n_clusters, "n_noise": n_noise,
        "umap": {"nn2d": 15, "min_dist2d": 0.1, "nn10d": 15, "components10d": 10},
        "hdbscan": {"min_cluster_size": 25, "min_samples": 5},
        "packing": {"method": "anchored-collide", "radius": round(radius, 5)},
    })
    with open(os.path.join(outdir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)

    # map.png
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 9), dpi=120)
        cl = labels.astype(int)
        noise = cl == -1
        ax.scatter(pxy[noise, 0], pxy[noise, 1], s=6, c="#d8d8dc", linewidths=0)
        uniq = sorted(set(int(c) for c in cl) - {-1})
        cmap = plt.get_cmap("tab20")
        for k, c in enumerate(uniq):
            m = cl == c
            ax.scatter(pxy[m, 0], pxy[m, 1], s=9, color=cmap(k % 20), linewidths=0)
            cx, cy = pxy[m, 0].mean(), pxy[m, 1].mean()
            ax.text(cx, cy, clabels[c]["label"], fontsize=7, weight="bold",
                    ha="center", va="center")
        ax.set_title(f"SPECTER2 — dementia/GWAS corpus ({len(papers)} papers)")
        ax.axis("off")
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "map.png"))
        print("wrote map.png")
    except Exception as e:
        print("map.png skipped:", e)

    print("done ->", os.path.relpath(outdir, ROOT))


if __name__ == "__main__":
    main()
