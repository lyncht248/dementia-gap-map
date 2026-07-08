# Inspiration Papers

These preprints are the methodological lineage the Dementia Gap Map draws on. Both
come out of the same research program (B. Ian Hutchins and the NIH Office of
Portfolio Analysis) and demonstrate the core idea behind this project: that the
structure of the biomedical citation / co-citation graph, split into a time series
of research topics, carries an early, machine-detectable signal of which research
areas will go on to produce real-world outcomes — breakthroughs and FDA-approved
drugs — years before they arrive.

That is exactly what we are trying to build for dementia and Alzheimer disease:
turn papers → citation/co-citation links → topic clusters → topic trajectories
over time, then surface the topics whose evidence is trending toward
translational payoff (GWAS loci, genes, pathways, drugs/interventions, trials).

## Papers

### 1. Prediction of transformative breakthroughs in biomedical research (2025)

- **File:** [`davis-et-al-2025-prediction-of-transformative-breakthroughs.pdf`](./davis-et-al-2025-prediction-of-transformative-breakthroughs.pdf)
- **Authors:** Matthew T. Davis, Brad L. Busse, Salsabil Arabi, Payam Meyer,
  Travis A. Hoppe, Rebecca A. Meseroll, B. Ian Hutchins, Kristine A. Willis,
  George M. Santangelo (NIH Office of Portfolio Analysis; UW-Madison; NCI)
- **Source:** bioRxiv preprint, posted December 17, 2025 — CC-BY 4.0
- **DOI:** https://doi.org/10.64898/2025.12.16.694385

An AI/ML-detected signature in **co-citation networks** that recognizes topics
likely to produce future transformative breakthroughs up to ~12 years before the
breakthrough publication (on average >5 years in advance). The diagnostic signal
combines: a burst of papers exploring a novel concept, an unusually high number of
very influential papers in specialty journals, and low topical cohesion of the
associated content. They show the kinetics of breakthrough formation are conserved
across two periods 20 years apart.

**Why it inspires this project:** this is the "topic dynamics" blueprint — build a
co-citation network, cut it into topics, track each topic's yearly trajectory, and
learn the shape of a topic that is about to matter. Our literature/topic layer is a
dementia-scoped, transparent-heuristic version of this idea.

### 2. Forecasting novel therapeutic development in biomedical research (2026)

- **File:** [`arabi-hutchins-2026-forecasting-novel-therapeutic-development.pdf`](./arabi-hutchins-2026-forecasting-novel-therapeutic-development.pdf)
- **Authors:** Salsabil Arabi & B. Ian Hutchins (Information School, University of
  Wisconsin-Madison)
- **Source:** bioRxiv preprint, posted June 1, 2026 — Public Domain (CC0)
- **DOI:** https://doi.org/10.64898/2026.05.29.728775

Divides the global citation graph of biomedical literature into a time series of
research topics and extracts topic features from citation activity, publication
content, and the "flocking" of scientists into novel topics. A machine-learning
model identifies research topics that will later yield **FDA-approved drugs** years
before approval (F1 = 0.84): 80% of target drugs predicted in advance, 65%
predicted 8+ years out, usually before phase-2 trials begin — using only public,
contemporaneous publication/citation data.

**Why it inspires this project:** this closes the loop from topic dynamics to a
translational endpoint (drug approval). It is the direct motivation for connecting
our topic trajectories to the translational evidence layer — GWAS, genes, pathways,
drugs, and clinical trials — so the map can highlight dementia research topics
trending toward therapeutic payoff.

## How they relate to the build

See [`../../PROTOTYPE_BUILD_SPEC.md`](../../PROTOTYPE_BUILD_SPEC.md), which already
references the NIH predictive-breakthroughs work as a reference design. The high
compute footprint of the full all-PubMed approach is why this project scopes to an
AD/ADRD corpus and uses transparent heuristic scores rather than reproducing the
full ML pipeline.
