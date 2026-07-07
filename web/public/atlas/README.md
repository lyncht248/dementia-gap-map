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

## Mechanistic-hypothesis framing (the "Hypotheses" toggle)

The map offers a **second framing of the same literature**: a toggle in the map
toolbar switches from the 10 disease regions to the **8 mechanistic "Alzheimer's
cure" hypotheses** (amyloid, tau, lipid / APOE, microglia, endocytosis,
synaptic, vascular, epigenetic — the pathway groups). In this mode the dots are
recoloured by each paper's dominant `pathway_group`, unclassified papers recede
to grey, the 8 hypotheses are labelled on the map (with their translation-gap),
and a panel under the map ranks them by translation gap with the Track B pathway
metrics (support, gap, trials, genes).

Because the atlas is laid out by **literature similarity** (which clusters by
disease and GWAS methodology, not by mechanism), the mechanisms do **not** occupy
separate regions — their anchors cluster inside the Alzheimer's continent, and
the real signal (e.g. tau's wide gap with almost no papers here; amyloid's 96
trials) lives in the pathway metrics, not in paper positions. The framing is
therefore a colour + metric overlay, not a re-layout.

This layer is patched onto `atlas.json` by `scripts/add_hypotheses.mjs` (it does
not touch the layout). It adds `hypotheses` (the 8 bets — `label`, short
on-canvas `short`, categorical `color`, the `statement`, an AD-continent label
anchor `x`/`y`, the paper `count`, and the Track B rollup metrics `gene_count` /
`trial_count` / `combined_support` / `clinical_translation` /
`clinical_saturation` / `translation_gap`, ranked by gap), `pointHyp` (per-paper
hypothesis index into `hypotheses`, `-1` = unclassified, parallel to
`points`/`ids`), and `unclassified_count`. The per-paper mechanism assignment is
reused from `atlas_feed.json` (`pathway_group`, derived from each paper's genes
via `translational-evidence/map/gene_pathway.csv`); the metrics come from the
Track B rollup `data/processed/translational-evidence/pathways.jsonl`.

## Regenerate

```bash
python3 scripts/build_atlas.py     # writes atlas.json (disease layout)
node scripts/build-atlas-feed.mjs  # writes atlas_feed.json (per-paper pathway_group)
node scripts/add_hypotheses.mjs    # patches the mechanistic-hypothesis layer onto atlas.json
cd web && npm run build            # or `npm run dev` to view the app
```

`build_atlas.py` reads `data/exports/visual/embeddings/qwen3-8b/{points,clusters}.jsonl`
+ `manifest.json` (and `data/processed/topic-dynamics/papers.jsonl` for the
citation links) and writes `atlas.json` here. `add_hypotheses.mjs` is a
non-destructive patch — safe to re-run against the committed `atlas.json`.
