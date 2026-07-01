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

# --- Corpus definition ------------------------------------------------------
# The corpus IS the field: every PubMed paper about dementia (broadly) that
# also mentions GWAS. Dementia is covered by the MeSH tree (which subsumes
# Alzheimer, Lewy body, frontotemporal, vascular, etc.) plus title/abstract
# synonyms; the GWAS clause requires the paper to actually mention it.
SEARCH_TERM = (
    "("
    "Dementia[MeSH Terms] OR dementia[Title/Abstract] "
    "OR Alzheimer*[Title/Abstract] OR \"Alzheimer Disease\"[MeSH Terms] "
    "OR frontotemporal[Title/Abstract] OR \"Lewy body\"[Title/Abstract] "
    "OR \"cognitive impairment\"[Title/Abstract] OR neurodegenerat*[Title/Abstract]"
    ") AND ("
    "GWAS[Title/Abstract] OR \"genome-wide association\"[Title/Abstract] "
    "OR \"genome wide association\"[Title/Abstract] "
    "OR \"Genome-Wide Association Study\"[MeSH Terms]"
    ")"
)
# 0 = no cap (ingest the whole field). Set a positive value for quick test runs.
MAX_PAPERS = 0
ESEARCH_PAGE = 500           # UIDs pulled per esummary history page
# Cited-by lists for hub papers (e.g. APOE) can run to tens of thousands; cap
# the fan-in so co-citation stays tractable. Cosine normalization handles the
# rest.
MAX_CITERS_PER_PAPER = 2000

# --- Network parameters -----------------------------------------------------
# References shared by more than this fraction of the corpus are field-defining
# "hub" papers (e.g. APOE); they link everything to everything and only add
# noise + cost, so they are ignored when building edges.
MAX_NEIGHBOR_DF_FRACTION = 0.25
MIN_COUPLING_WEIGHT = 0.05    # drop weak bibliographic-coupling edges
MIN_COCITATION_WEIGHT = 0.05  # drop weak co-citation edges
# Blend used when both edge types exist between a pair (see network/edges.py).
COUPLING_BLEND = 0.7
COCITATION_BLEND = 0.3

# --- Clustering / scoring ---------------------------------------------------
MIN_CLUSTER_SIZE = 3          # ignore tiny communities in exports
TOP_TERMS_PER_TOPIC = 6
EMERGENCE_RECENT_YEARS = 2    # "recent" window for pct-new / growth
