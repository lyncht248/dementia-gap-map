# Dementia Gap Map

An interactive map of dementia & GWAS research from PubMed, grouped semantically, that links the literature to the genetic, functional, pathway, drug, and clinical-trial evidence behind it.

**Live demo:** https://dementia-gap-map.vercel.app/

Current scope is the ~4,780 PubMed papers matching `dementia AND GWAS` (easily expandable). Every paper is embedded with **Qwen3-Embedding-8B** and positioned by semantic similarity, so related work sits together. Each paper (node) is linked to its relevant gene target(s), pathway(s), and clinical trial(s), and the connections drawn on the map are citation links between papers. An in-app agent can reason over the underlying data.

## What it's for

The map is built to surface **gaps where effective new interventions may arise** — areas with strong genetic evidence but weak clinical translation. For example, you can ask the agent:

> Where is there strong genetic evidence but weak clinical translation?

The goal is not to claim we predict breakthroughs, but to detect emerging, genetically supported areas and compare them against where clinical translation has (or hasn't) happened.

## Using the map

- **Pan and zoom.** Zoom out for broad disease regions; zoom in for fine themes (Microglia & Neuroinflammation, TREM2, Fluid Biomarkers, Polygenic Risk Scores, …).
- **Hover a paper** to trace its citation links to the rest of the corpus.
- **Click "Select Region"** and draw a circle to list the papers inside it in the feed below the map.
- **Ask the agent** (left panel) to query the evidence and drive the map — it can select papers, highlight genes/pathways, zoom, and filter.

## How it works

Two linked layers:

- **Literature / topic layer** — papers, citation and co-citation links, and semantic theme clusters. Papers are embedded with Qwen3-Embedding-8B; the ~45 fine themes and broader disease regions come from UMAP + HDBSCAN over those embeddings.
- **Translational-evidence layer** — GWAS genetics, a functional / eQTL layer (aggregated Open Targets Locus-to-Gene predictions feeding a `functional_support` score), genes, pathways, drugs/interventions, and clinical trials, covering Alzheimer disease and related dementias (ADRD). Every record is tagged with a controlled `disease_group`, so the map supports a dementia-vs-Alzheimer filter (Alzheimer is the subset `disease_group == "alzheimer"`).

Data sources: PubMed / NCBI E-utilities, NIH iCite, the GWAS Catalog, Open Targets, and ClinicalTrials.gov. See [PROTOTYPE_BUILD_SPEC.md](PROTOTYPE_BUILD_SPEC.md) for the full data-source and pipeline notes.

The web app (`web/`) is a static Vite + React front end. The agent runs a client-orchestrated tool loop: it queries the data in-browser via DuckDB-Wasm over Parquet and calls an OpenAI-compatible model through a serverless proxy, so the API key stays server-side.

## Repository Layout

```text
web/                          # Vite + React app (the map + agent panel)
scripts/                      # Ingestion, embedding, atlas build, and data export

topic-dynamics/               # Literature / topic layer workspace
translational-evidence/       # Translational-evidence layer workspace

shared/
  schemas/                    # Shared data contracts
  lib/                        # Shared helper code

data/
  raw/                        # Source API downloads and hand-curated inputs
  interim/                    # Working outputs
  processed/                  # Stable, cross-cutting outputs
  exports/                    # Files consumed by the web app (embeddings, graph)

docs/                         # Notes, embedding benchmark, inspiration
```

## Inspiration

The core idea — that the structure of the biomedical citation/co-citation graph, cut into a time series of research topics, carries an early machine-detectable signal of which areas will produce real-world outcomes — comes from two preprints out of the NIH Office of Portfolio Analysis (B. Ian Hutchins and colleagues):

- **Prediction of transformative breakthroughs in biomedical research** (Davis et al., 2025). Finds a signature in co-citation networks that flags topics likely to yield future transformative breakthroughs up to ~12 years ahead (>5 years on average). The signal combines a burst of papers around a novel concept, an unusually high number of very influential papers in specialty journals, and low topical cohesion — and the kinetics hold across two periods 20 years apart.
- **Forecasting novel therapeutic development in biomedical research** (Arabi & Hutchins, 2026). Extends the same topic-dynamics approach to a translational endpoint: a model over citation activity, publication content, and the "flocking" of scientists into new topics identifies research areas that later yield FDA-approved drugs years before approval (F1 = 0.84) — ~80% of target drugs predicted in advance, 65% predicted 8+ years out, usually before phase-2 trials begin, using only public contemporaneous data.

This map is a dementia-scoped, transparent-heuristic take on that lineage: turn papers → citation links → theme clusters → trajectories over time, then surface the topics whose evidence is trending toward translational payoff (GWAS loci, genes, pathways, drugs, trials). Full notes and the PDFs are in [docs/inspiration/](docs/inspiration/README.md).

## License

Released under the [MIT License](LICENSE).
