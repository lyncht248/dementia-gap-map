# Dementia Gap Map

An interactive map of the dementia genetics literature. Every paper matching
**"Dementia AND GWAS"** is placed on a 2-D canvas by co-citation similarity, so
papers that are cited together sit near each other and each visual clump is a
research topic. Draw a region around any clump and a newsfeed below breaks it
down by topic, gene/locus, pathway, and linked clinical trial — and surfaces
which of those topics are currently emerging.

The live app is a single-page React site (`web/`) that loads one precomputed
file, `web/public/data/map_data.json`. That file is built offline by the
pipeline in `scripts/` from the processed datasets in `data/processed/`.

## Using the map

- **Pan / zoom** — drag to pan, scroll to zoom. Points are colored by topic
  cluster; grey points are the catch-all "other" community.
- **Select region** — click *Select region*, then draw a boundary around a
  group of papers. The newsfeed populates with just those papers.
- **Filters** — toggle pathway groups and set a year range to fade papers in or
  out of view.
- **Newsfeed** — for the current selection, switch between:
  - **Papers** — cards ranked by citation count, linking out to each source.
  - **Genes / loci**, **Pathways**, **Trials** — ranked bar lists of the
    facets present in the selection.
  The sidebar lets you filter the selection down by any facet, and lists
  **Emerging topics** ranked by an emergence score (recency burst + growth +
  citation influence).

Each paper carries citation metrics from NIH iCite (citation count, Relative
Citation Ratio, clinical flag), gene/locus links, and linked trials rolled up
from its topic.

## Tech stack

- **React 18 + TypeScript + Vite** — `web/`
- **HTML canvas** rendering for the map (`web/src/components/MapCanvas.tsx`) —
  handles pan, zoom, lasso selection, and edge/hover highlighting.
- **No backend.** The app is fully static and fetches a single JSON file at
  runtime, so it deploys anywhere that serves static files (configured for
  Vercel).

## Data pipeline

The map is built from two upstream data tracks whose stable outputs land in
`data/processed/`:

- **Topic dynamics** (`data/processed/topic-dynamics/`) — papers, the
  co-citation / bibliographic-coupling edges, and topic clusters.
- **Translational evidence** (`data/processed/translational-evidence/` and
  `data/processed/shared/`) — GWAS genetics, genes, pathways, clinical trials,
  and the topic-to-evidence links that join genetics onto each topic.

`scripts/build-map-data.mjs` turns those into the app's `map_data.json`:

1. Builds a graph from the co-citation edges (cosine-weighted, with a coupling
   fallback for not-yet-cited papers).
2. Detects topics with **Louvain community detection** and lays the graph out
   with **ForceAtlas2** — communities and positions come from the *same* graph,
   so a spatial clump on the map really is a topic. The layout is deterministic
   (seeded, no RNG) so re-runs are stable.
3. Joins each topic to its top genes, pathway group, and trials, computes the
   per-topic emergence score, and writes `web/public/data/map_data.json`.

The current build covers ~4,780 papers across 16 topic communities.

## Repository layout

```text
web/                          # The React/Vite single-page app (the map + newsfeed)
  public/data/map_data.json   #   the single precomputed file the app loads
  src/                        #   App, MapCanvas, NewsFeed, data + geometry libs

scripts/                      # Offline pipeline that builds map_data.json
  build-map-data.mjs          #   layout + community detection + evidence join
  emergence.mjs               #   per-topic emergence scoring
  embed_map.py, *.py          #   embedding / co-citation experiments

data/
  raw/                        # Source API downloads and hand-curated inputs
  interim/                    # Track-local working outputs
  processed/                  # Stable per-track outputs consumed by the build
  exports/                    # Larger visual-layer artifacts

shared/                       # Data contracts and helpers shared across tracks
docs/                         # Working agreement, methodological inspiration
```

## Local development

Run the app:

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

Rebuild the map data (only needed when `data/processed/` changes):

```bash
cd web
npm run gen-data   # installs scripts/ deps, runs build-map-data.mjs
```

Production build:

```bash
cd web
npm run build      # type-checks and emits web/dist/
npm run preview    # serve the built output locally
```

## Deployment

The app deploys as a static site to Vercel. `scripts/setup-vercel.sh` is a
one-shot helper (run locally, needs your Vercel login) that links the repo and
connects Vercel's Git integration, after which pushing to `main` auto-deploys
production.

## Further reading

- [PROTOTYPE_BUILD_SPEC.md](PROTOTYPE_BUILD_SPEC.md) — full data-source and
  build-pipeline notes.
- [docs/inspiration/](docs/inspiration/README.md) — the methodological lineage:
  using the citation / co-citation graph to detect research topics trending
  toward breakthroughs and drug approvals years in advance.
</content>
</invoke>
