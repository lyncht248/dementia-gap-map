# Theme Atlas — Qwen3-Embedding-8B

An interactive, pan/zoom "theme map" of the 4,780-paper dementia / GWAS corpus,
built from the **Qwen3-Embedding-8B** run of the embedding bake-off
(`docs/embedding-benchmark.md`). One hex-tiled dot per paper (no overlaps),
coloured by a smooth disease-area gradient; big labels when zoomed out, finer
sub-topics as you zoom in. **Hover any dot to trace its citation links** — lines
fan out to every other paper in the corpus it cites or is cited by.

Open `index.html` directly in a browser (it is fully self-contained), or visit
`/atlas/` on the deployed site.

## How topics are chosen (the two-tier hierarchy)

The clustering (UMAP + HDBSCAN over the Qwen embeddings) found **45 fine
clusters**, but those mix several *classes* of thing — diseases, methodologies,
individual genes, biological themes. Mixing classes in the zoomed-out view is
confusing, so we split topics into two tiers:

- **Major topics (zoomed out) — one class: _disease / neurological condition_.**
  We picked disease because it is the single class the embedding geometry
  actually supports as coherent, spatially-separated regions: the non-Alzheimer
  conditions (Parkinson's, ALS/FTD, Huntington's, Lewy body, prion, MS,
  ophthalmic, vascular, psychiatric) fall out as tight islands, while
  Alzheimer's disease forms one large central continent. There are **10** of
  them, all the same class.

- **Minor topics (zoomed in) — the 45 Qwen/HDBSCAN clusters.** These carry the
  method/gene detail (Mendelian Randomization, Polygenic Risk Scores, Microglia
  & Neuroinflammation, Fluid Biomarkers, TREM2, DNA Methylation, …) and appear
  as you zoom into a region.

Each fine cluster is assigned to exactly one disease major
(`CLUSTER_TO_MAJOR` in `scripts/build_atlas.py`). The full mapping and the
readable label for every cluster live in that script.

## Layout & colour

Raw UMAP scatters have dense pile-ups and lots of whitespace. For the atlas look
the layout is built in two deterministic steps:

1. `pack_force` — a small force simulation: collision (no overlaps) + gravity
   toward each paper's disease-major centroid (squeeze out whitespace, keep
   regions as islands) + a faint anchor to the original UMAP position (preserve
   sub-topic ordering).
2. `hex_snap` — snaps the compact cloud onto one shared hexagonal lattice so
   every paper gets its own cell: perfect tiling, zero overlaps, uniform gaps.

**Colour** is a smooth gradient: each dot's colour is an inverse-distance blend
of the 10 disease-area colours, so region cores keep their hue while borders
melt into their neighbours (computed in the browser from the region centroids).

**Citation links.** On hover, lines connect the paper to every corpus paper it
cites or is cited by (22,763 undirected in-corpus links, derived from each
paper's `references` list intersected with the corpus; both directions merged).

## Regenerate

```bash
python3 scripts/build_atlas.py
```

Reads `data/exports/visual/embeddings/qwen3-8b/{points,clusters}.jsonl` +
`manifest.json` and rewrites `index.html` + `atlas.json` here.
