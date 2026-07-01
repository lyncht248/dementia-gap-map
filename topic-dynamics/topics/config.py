"""Track A configuration: paths, corpus caps, and network/scoring parameters.

Everything the pipeline needs to be reproducible lives here. API credentials
are read from the environment so nothing secret ends up in git.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Repository paths -------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK = "topic-dynamics"

RAW_DIR = REPO_ROOT / "data" / "raw" / TRACK
INTERIM_DIR = REPO_ROOT / "data" / "interim" / TRACK
PROCESSED_DIR = REPO_ROOT / "data" / "processed" / TRACK
CACHE_DIR = RAW_DIR / "cache"

# --- NCBI E-utilities etiquette --------------------------------------------
# Supplying a tool name + email lets NCBI contact us before blocking, and an
# API key raises the anonymous 3 req/s limit to 10 req/s.
NCBI_TOOL = "dementia-gap-map"
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "lyncht248@gmail.com")
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")  # optional
# Seconds between NCBI requests. 0.34s ~= 3/s (safe without a key).
NCBI_MIN_INTERVAL = 0.12 if NCBI_API_KEY else 0.34

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ICITE_BASE = "https://icite.od.nih.gov/api"

# --- Corpus construction caps ----------------------------------------------
# Broad literature seed query. Kept close to the PROTOTYPE_BUILD_SPEC example.
SEARCH_TERM = (
    "(Alzheimer Disease[MeSH Terms] OR Alzheimer*[Title/Abstract] "
    "OR dementia[Title/Abstract]) AND (GWAS OR genome-wide association "
    "OR eQTL OR genetics[Title/Abstract])"
)
SEARCH_RETMAX = 120          # PMIDs pulled from the broad esearch seed
MAX_PAPERS = 400             # hard cap on total corpus size
MAX_CITERS_PER_PAPER = 200   # keep the co-citation fan-in bounded
MAX_REFS_PER_PAPER = 300     # keep the coupling fan-out bounded

# Keywords an expansion paper's title must touch to stay in-scope.
TOPIC_KEYWORDS = (
    "alzheimer", "dementia", "gwas", "genome-wide", "genome wide",
    "genetic", "eqtl", "variant", "locus", "loci", "microglia", "tau",
    "amyloid", "apoe", "trem2", "single-cell", "single cell", "snrna",
    "neurodegenerat", "frontotemporal", "lewy", "polygenic", "heritability",
)

# --- Network parameters -----------------------------------------------------
MIN_COUPLING_WEIGHT = 0.05    # drop weak bibliographic-coupling edges
MIN_COCITATION_WEIGHT = 0.05  # drop weak co-citation edges
# Blend used when both edge types exist between a pair (see network/edges.py).
COUPLING_BLEND = 0.7
COCITATION_BLEND = 0.3

# --- Clustering / scoring ---------------------------------------------------
MIN_CLUSTER_SIZE = 3          # ignore tiny communities in exports
TOP_TERMS_PER_TOPIC = 6
EMERGENCE_RECENT_YEARS = 2    # "recent" window for pct-new / growth
