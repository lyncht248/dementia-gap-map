# Theme Atlas — Qwen3-Embedding-8B

An interactive, pan/zoom "theme map" of the 4,780-paper dementia / GWAS corpus,
built from the **Qwen3-Embedding-8B** run of the embedding bake-off
(`docs/embedding-benchmark.md`). One hex-tiled dot per paper (no overlaps),
coloured by disease area (flat regions, blending only at true borders); big
labels when zoomed out, finer sub-topics as you zoom in. **Hover any dot to
trace its citation links** — lines fan out to every other paper in the corpus it
cites or is cited by. **Draw a region** (Select region) to list those papers in
the feed below the map.

This directory holds only the data (`atlas.json`). The map is rendered by the
web app: the canvas renderer is `web/src/lib/atlasRender.ts`, embedded in the
map panel via `web/src/components/AtlasMap.tsx`. It needs only Track A data
(embeddings + citations) — no Track B (genes / pathways / trials) required;
Track B would only enrich the per-paper selection feed.

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
the layout is built in two deterministic steps (`scripts/build_atlas.py`):

1. `pack_force` — a small force simulation that keeps each disease region a
   distinct, cohesive *shape* (not one fused blob): a modest region-centroid
   pull fills whitespace, a gentle global pull brings the islands closer, a
   strong anchor preserves the UMAP continent outline, and collision keeps dots
   from overlapping.
2. `hex_snap` — snaps the compact cloud onto one shared hexagonal lattice so
   every paper gets its own cell: clean tiling, zero overlaps, uniform gaps.

**Colour** (computed in the browser, `atlasRender.ts`): each dot shows its own
disease-area's flat colour, blending toward a neighbour only in a thin seam
right at the border between two regions — so a gradient appears only where the
embedding actually sits between topics.

**Citation links.** On hover, lines connect the paper to every corpus paper it
cites or is cited by (22,763 undirected in-corpus links, derived from each
paper's `references` list intersected with the corpus; both directions merged).

## Regenerate

```bash
python3 scripts/build_atlas.py   # writes atlas.json
cd web && npm run build          # or `npm run dev` to view the app
```

`build_atlas.py` reads `data/exports/visual/embeddings/qwen3-8b/{points,clusters}.jsonl`
+ `manifest.json` (and `data/processed/topic-dynamics/papers.jsonl` for the
citation links) and writes `atlas.json` here.
