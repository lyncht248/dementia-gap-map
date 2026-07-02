# Embedding Bake-off: Qwen3-Embedding-8B vs. SPECTER2

**Goal.** Embed every paper in the Track A corpus (title + abstract), project the
vectors to 2D, and cluster them into themes — producing a semantic "theme map" of
the dementia / GWAS literature. We run **two models on two machines in parallel**,
open **one PR per model**, and pick our favourite by looking at the resulting maps
side by side.

This is a **bake-off**: the *only* variable we want to change between the two runs
is the embedding model. Everything downstream (text fed in, projection, clustering,
labelling, plot) is fixed by this document so the comparison is apples-to-apples.

| Run | Model | Machine | Branch | Owner |
|-----|-------|---------|--------|-------|
| **A** | `Qwen/Qwen3-Embedding-8B` | separate MacBook | `embed/qwen3-8b` | — |
| **B** | `allenai/specter2` (base + proximity adapter) | this MacBook (M3, 16 GB) | `embed/specter2` | — |

---

## 1. Input corpus

- **File:** `data/processed/topic-dynamics/papers.jsonl` (JSON Lines, one paper per line).
- **Count:** 4,780 papers.
- **Key:** use `paper_id` (e.g. `"pmid:42381015"`) as the stable identifier in every output.

### Text to embed — title + abstract

For each paper build a single document from **`title` + `abstract`**:

- 4,694 papers (98.2%) have an abstract; **86 papers (1.8%) have no abstract** →
  embed the **title alone** for those. Do **not** skip them; they still get a vector,
  a 2D point, and a cluster.
- Strip leading/trailing whitespace. Do not otherwise clean, lowercase, or truncate
  the text yourself — let each model's tokenizer handle truncation at its own limit.

**Model-specific formatting** (use each model's recommended input; the underlying
text content is identical):

- **SPECTER2** — concatenate with the tokenizer's separator token:
  `title + tokenizer.sep_token + (abstract or "")`. This is the format SPECTER2 was
  trained on. Max length 512 tokens (let the tokenizer truncate).
- **Qwen3-Embedding-8B** — pass the document as-is: `f"{title}\n\n{abstract}".strip()`.
  Qwen3-Embedding supports an optional *instruction* prefix for the query side; here
  we are embedding **documents**, so **no instruction prefix** — embed the raw
  title+abstract. Qwen3 has a long context window, so no meaningful truncation.

---

## 2. Shared output contract

Both runs write to `data/exports/visual/embeddings/<model>/` where `<model>` is
`qwen3-8b` or `specter2`. Produce these files:

| File | Format | Contents |
|------|--------|----------|
| `vectors.npy` | float16 `.npy`, shape `(4780, D)` | raw embeddings, **row order = `ids.json` order**. `D`=4096 (Qwen) / 768 (SPECTER2). |
| `ids.json` | JSON array of strings | `paper_id` for each row, defines row order for `vectors.npy`. |
| `points.jsonl` | JSON Lines | one record per paper: `{"paper_id", "x", "y", "px", "py", "cluster", "title", "year"}` — `x,y` = raw UMAP coords; `px,py` = **packed, non-overlapping** display coords (see §3a). |
| `clusters.jsonl` | JSON Lines | one record per cluster: `{"cluster", "size", "label", "top_terms": [...]}` |
| `map.png` | PNG | the 2D scatter, points coloured by cluster (see §4). |
| `map.html` | standalone HTML (optional but preferred) | interactive Plotly scatter, hover shows title/year/cluster. |
| `manifest.json` | JSON | run metadata: model id, D, n_papers, n_no_abstract, n_clusters, n_noise, UMAP/HDBSCAN params, package versions, wall-clock seconds, device. |

**Git note.** `data/processed/**` and `data/exports/**` are gitignored (see
`.gitignore`). For the PR, **force-add** the small reviewable artifacts so we can
diff them: `points.jsonl`, `clusters.jsonl`, `map.png`, `map.html`, `manifest.json`.
- **Commit `vectors.npy` only if ≤ 50 MB.** SPECTER2 float16 ≈ 7 MB → commit it.
  Qwen3-8B float16 ≈ 39 MB → commit it too (under the cap). If it ever exceeds
  50 MB, gitignore it and attach to the PR instead; the script must regenerate it.
- Use `git add -f <path>` for the whitelisted artifacts since the dirs are ignored.

---

## 3. Fixed downstream: projection + clustering

Run these **identically** for both models. Pin the versions and seeds below.

1. **L2-normalize** every embedding (so distances are cosine).
2. **Projection for the plot** — UMAP → 2D:
   - `n_neighbors=15`, `min_dist=0.1`, `metric="cosine"`, `n_components=2`,
     `random_state=42`.
3. **Clustering** — HDBSCAN on a separate 10-D UMAP embedding (better cluster
   structure than clustering raw 2D):
   - 10-D UMAP: `n_neighbors=15`, `min_dist=0.0`, `metric="cosine"`,
     `n_components=10`, `random_state=42`.
   - HDBSCAN: `min_cluster_size=25`, `min_samples=5`, `metric="euclidean"`.
   - HDBSCAN noise points get `cluster = -1`; keep them (plot in grey).
4. **Cluster labels** — for each cluster, take the top TF-IDF terms across member
   **titles** and join the top 3–4 into a short human-readable `label`. Reuse the
   tokenizer + stopword list already in
   `topic-dynamics/topics/cluster/topics.py` (`_tokens` / `_top_terms`) so labels
   match the rest of the project's style. `top_terms` = top ~8 terms.

> Rationale for 10-D-then-cluster + 2-D-for-plot: it's the standard BERTopic-style
> recipe — clustering in a slightly higher-dim UMAP space is more stable than
> clustering the 2-D display coords, while the 2-D map stays readable.

---

## 3a. Bubble packing — no overlapping dots

**Requirement:** in the final map every paper is an **equal-size circle and no two
circles overlap** — they should tile together cleanly with small uniform gaps,
following the cluster shapes, like the reference map we're matching (dense,
hex-packed, one dot per paper, no pile-ups).

A raw UMAP scatter has dense cores where hundreds of points land on top of each
other — exactly what we do **not** want. So after §3 we compute a second set of
**packed display coordinates** `px,py` from the raw UMAP `x,y`, and the plot in §4
renders `px,py` with a fixed marker radius. Keep both: `x,y` (true embedding, for
analysis) and `px,py` (clean render). Run the **same packing with the same radius
for both models** so the two maps are visually comparable.

Two acceptable methods — try the first for the closest match to the reference, fall
back to the second if it's fragile on your setup:

1. **Hex-grid snapping (RasterFairy)** — closest to the reference's uniform hex
   look. `pip install rasterfairy-py3`; assign the 4,780 UMAP points to a regular
   **hexagonal** (or circular) grid so each dot gets its own cell while preserving
   relative position. Zero overlap by construction. Deterministic.
2. **Collision relaxation (d3-force / anchored)** — robust and slightly more
   organic. Anchor each node to its UMAP coord (`forceX`/`forceY`, strength ≈ 0.1)
   plus `forceCollide(r)` at a fixed radius `r`; run ~300 ticks and export the
   settled positions as `px,py` so `map.png` and `map.html` agree. Seed any RNG at 42.

Pick the radius `r` from the layout itself: `r ≈ 0.45 ×` the **median
nearest-neighbour distance** of the raw UMAP points, so the packed map keeps roughly
the same overall footprint. Record the packing method + `r` in `manifest.json`.

---

## 4. The plot

- Scatter of all 4,780 points at their **packed coords `px,py`** (§3a), every marker
  the **same fixed radius**, so bubbles tile cleanly and **never overlap**.
- Colour by `cluster`; noise (`-1`) in light grey, small/faint.
- Annotate each non-noise cluster with its `label` near the cluster centroid.
- Title: `"<Model> — dementia/GWAS corpus (4,780 papers)"`.
- Save both `map.png` (for the PR diff) and `map.html` (for exploring hover text).

---

## 5. Environment setup (macOS / Apple Silicon)

Use a fresh venv per machine. Both use the MPS backend where available.

```bash
python3 -m venv .venv-embed && source .venv-embed/bin/activate
pip install --upgrade pip
pip install numpy umap-learn hdbscan matplotlib plotly
```

### Run B — SPECTER2 (this MacBook, M3 16 GB)

```bash
pip install "transformers>=4.40" "adapters>=0.2" torch
```

- Load base `allenai/specter2_base`, then load and activate the **proximity**
  adapter `allenai/specter2` (the document/proximity adapter — the right one for
  title+abstract retrieval/clustering).
- `device="mps"` (fall back to `"cpu"` if MPS errors), fp32 is fine (model is tiny),
  batch size 16–32.
- Expected wall-clock: **a few minutes**.

### Run A — Qwen3-Embedding-8B (separate MacBook)

```bash
pip install "sentence-transformers>=3.0" torch
```

- `SentenceTransformer("Qwen/Qwen3-Embedding-8B", device="mps")`, then
  `model.encode(docs, batch_size=8, normalize_embeddings=False, ...)`
  (we L2-normalize ourselves in §3 step 1).
- **Memory:** 8B in fp16 ≈ 16 GB. If the machine has ≥ 24 GB unified memory this
  fits; on a 16 GB machine load 4-bit / use a smaller batch, or it will swap. Prefer
  running Qwen-8B on the machine with the most RAM.
- Keep the sequence length reasonable (e.g. cap at 1024 tokens) — abstracts are
  short, and this keeps memory/time in check.
- Expected wall-clock: **tens of minutes** on Apple Silicon (the 8B forward pass is
  the bottleneck). Let it run; write vectors incrementally so a crash doesn't lose
  progress.

---

## 6. Deliverable per PR

Each machine opens **one PR into `main`** from its branch (`embed/qwen3-8b` /
`embed/specter2`) containing:

1. The run script (put shared logic in `scripts/embed_map.py` with a `--model`
   flag so both PRs share one script; whichever lands first adds it, the second
   rebases on it). The script must be re-runnable and deterministic.
2. The artifacts from §2 for that model, under
   `data/exports/visual/embeddings/<model>/`.
3. A short note in the PR body: wall-clock, n_clusters, n_noise, and one sentence
   on how coherent the themes look.

---

## 7. How we pick the winner

Open both `map.png` (and `map.html`) side by side and judge:

- **Theme separation** — are clusters visually distinct, or one big blob?
- **Cluster coherence** — do the `top_terms` / member titles actually share a theme?
- **Sensible granularity** — not 3 mega-clusters, not 200 slivers; and a low
  noise fraction (`cluster = -1`).
- **Interpretability** — can we name each cluster at a glance?

Whichever model gives the map we'd actually want to ship as the site's theme layer
wins; we merge that PR and keep the other branch for reference.

---

## 8. Conventions

- Corpus lives under Track A ownership (`data/processed/topic-dynamics/**`); this
  experiment only **reads** it and writes new outputs under `data/exports/visual/**`
  (shared area) — no changes to Track A's processed shape.
- Deterministic: all `random_state=42`; record exact package versions in
  `manifest.json`.
- One model per branch/PR. Do not mix both models into one PR.
