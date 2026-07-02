"""Capture gene->pathway (3 sources) and drug->mechanism (Open Targets) from APIs.

CORE PRINCIPLE - RECORD EVERYTHING, MULTI-VALUED
------------------------------------------------
For each gene we persist the FULL captured annotations from EVERY source (all GO
terms, all Reactome pathways, all Open Targets target pathways), not just the
subset that matched an AD mechanism bucket. The ONTOLOGY ANNOTATIONS are the
truth; the AD buckets are a DERIVED CONVENIENCE. AD-bucket / mechanism tags are a
LIST of signals - each ``{bucket, source, matched_term}`` - so a gene may
legitimately carry several buckets from several sources and we keep them all.

We derive ONE ``primary_bucket`` ONLY for the thin single-colour CSV the legacy
map consumes. Selection is by IDF (SPECIFICITY) WEIGHTING, not "most terms win":

  1. Across ALL genes, compute the document-frequency of every matched term
     (how many genes carry that term in any source), and its inverse-document-
     frequency  idf(term) = log(n_genes / (1 + genes_with_term)).
  2. For each gene, score each candidate bucket = sum of idf() of the DISTINCT
     terms (across GO / Reactome / OT) that mapped to it. primary_bucket =
     argmax; ties broken by ``BUCKET_PRIORITY``.

This makes rare/specific terms (amyloid-beta, tau) outweigh ubiquitous ones
(gene expression, neuron), so the primary reflects the gene's most DISTINCTIVE
mechanism rather than whichever bucket happens to collect the most generic
annotations. The chosen bucket's supporting terms + their idf are recorded in the
provenance (``primary_support`` / ``notes``). The rich record keeps all signals +
all raw annotations for transparency.

Sources (each probed live; membership comes from the API, not by hand):
  1. mygene.info  GET /v3/gene/{ensembl}?fields=symbol,uniprot,go
        -> UniProt accession(s) + ALL go.{BP,MF,CC}[{id,term}].
  2. Reactome     GET /ContentService/data/mapping/UniProt/{acc}/pathways?species=9606
        -> ALL [{stId, displayName}].
  3. Open Targets GraphQL  target(ensemblId).pathways
        -> ALL [{pathwayId, pathway, topLevelTerm}].

The ONLY residual hand element is the transparent keyword ruleset
(``PATHWAY_BUCKET_KEYWORDS`` / ``TRIAL_MECHANISM_KEYWORDS``) that maps captured
term strings into the fixed AD mechanism vocabulary; it lives in code, not
per-gene. Nothing is fabricated: a source that returns nothing contributes
nothing (no signal, no term).

NOTE: the authoritative, trials-corpus-driven drug->mechanism capture (with
Open Targets targets[] for drug_target trial linkage) lives in
map/intervention_mechanism_build.py, which writes the SINGULAR
drug_mechanism_api.jsonl. This module no longer writes a (plural, orphan)
drug_mechanisms_api.jsonl seeded sidecar; the drug-side OT helpers below are
retained for provenance/reuse but are not invoked by the default build.

Outputs (all GENERATED - do not hand-edit):
  - data/processed/translational-evidence/gene_pathways_api.jsonl
        ONE rich record per gene: full sources.* + ad_bucket_signals[] +
        buckets[] + primary_bucket + primary_support[] (each with its idf) +
        bucket_scores{}.
  - translational-evidence/map/gene_pathway.csv
        THIN projection (gene_symbol, pathway_group=primary_bucket, notes).
  - data/processed/translational-evidence/pathways.jsonl
        one record per pathway_group (same shape as map/pathways.py; scores are
        added later by score/scores.py in run_all).

Raw API responses are cached under:
  - data/raw/translational-evidence/mygene/                (+ /query for lookups)
  - data/raw/translational-evidence/reactome/
  - data/raw/translational-evidence/opentargets_targets/
  - data/raw/translational-evidence/opentargets_drugs/     (search + drug docs)

Standard library only (Python 3.9). Network via common.get_json / common.post_json
(cached to data/raw; TE_REFRESH=1 forces fresh calls).

Run:
    python3 translational-evidence/map/gene_pathway_build.py
Skip the drug pass (genes only):
    python3 translational-evidence/map/gene_pathway_build.py --skip-drugs
"""

import argparse
import csv
import math
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GENES_JSONL = common.PROCESSED_DIR / "genes.jsonl"
GENE_PATHWAY_CSV = common.TE_DIR / "map" / "gene_pathway.csv"
GENE_SIDECAR_JSONL = common.PROCESSED_DIR / "gene_pathways_api.jsonl"
PATHWAYS_JSONL = common.PROCESSED_DIR / "pathways.jsonl"

MYGENE_CACHE_DIR = common.RAW_DIR / "mygene"
MYGENE_QUERY_CACHE_DIR = common.RAW_DIR / "mygene" / "query"
REACTOME_CACHE_DIR = common.RAW_DIR / "reactome"
OT_TARGETS_CACHE_DIR = common.RAW_DIR / "opentargets_targets"
OT_DRUGS_CACHE_DIR = common.RAW_DIR / "opentargets_drugs"

MYGENE_GENE_URL = "https://mygene.info/v3/gene/"
MYGENE_QUERY_URL = "https://mygene.info/v3/query"
REACTOME_URL = "https://reactome.org/ContentService/data/mapping/UniProt/"
OT_GRAPHQL_URL = "https://api.platform.opentargets.org/api/v4/graphql"

CSV_HEADER_COMMENT = (
    "# GENERATED convenience view (thin projection) by gene_pathway_build.py: "
    "pathway_group is the IDF-derived primary AD bucket (specificity-weighted "
    "argmax over matched GO/Reactome/OpenTargets terms; ties broken by AD "
    "priority). The ontology annotations in gene_pathways_api.jsonl are the "
    "truth; this single-bucket column is a derived convenience. Do not hand-edit."
)

# Source labels used in the ad_bucket_signals / sources object.
SRC_GO = "go"
SRC_REACTOME = "reactome"
SRC_OT = "open_targets"


# ---------------------------------------------------------------------------
# AD pathway bucket vocabulary + keyword ruleset (the ONLY hand element).
# Keys are the controlled pathway_group values; values are lowercase substrings
# matched against GO term names, Reactome displayNames, and OT pathway /
# topLevelTerm strings.
# ---------------------------------------------------------------------------

PATHWAY_BUCKET_KEYWORDS = {
    "amyloid": [
        "amyloid", "secretase", "presenilin", "beta-amyloid", "a-beta",
        "plaque", "a4 protein",
    ],
    "tau": [
        "tau", "microtubule-associated", "neurofibrillary", "tauopathy",
    ],
    "microglia_immune": [
        "immun", "microglia", "complement", "inflammat", "phagocyt", "innate",
        "cytokine", "interleukin", "chemokine", "myeloid", "antigen",
        "toll-like",
    ],
    "lipid_metabolism": [
        "lipid", "lipoprotein", "cholesterol", "chylomicron", "apolipoprotein",
        "sterol", "fatty acid", "triglyceride", "hdl", "ldl",
    ],
    "vascular": [
        "vascular", "angiogen", "blood vessel", "endothel", "coagul",
        "hemostasis", "blood-brain",
    ],
    "endocytosis_endosomal": [
        "endocyt", "endosom", "clathrin", "vesicle", "retromer", "lysosom",
        "autophag", "trafficking", "protein transport",
    ],
    "synaptic_neuronal": [
        "synap", "neuron", "axon", "dendrit", "neurotransmit", "glutamate",
        "long-term potentiation", "plasticity",
    ],
    "epigenetic_transcription": [
        "chromatin", "histone", "methylation", "transcription", "epigen",
        "acetylation",
    ],
}

# Tie-break priority (highest first) for the primary-projection.
BUCKET_PRIORITY = [
    "amyloid",
    "tau",
    "microglia_immune",
    "lipid_metabolism",
    "endocytosis_endosomal",
    "synaptic_neuronal",
    "vascular",
    "epigenetic_transcription",
]

# Human-readable labels for the pathways.jsonl records.
GROUP_LABELS = {
    "amyloid": "Amyloid processing",
    "tau": "Tau / neurofibrillary pathology",
    "lipid_metabolism": "Lipid metabolism",
    "microglia_immune": "Microglia / innate immunity",
    "endocytosis_endosomal": "Endocytosis / endosomal trafficking",
    "synaptic_neuronal": "Synaptic / neuronal function",
    "vascular": "Vascular",
    "epigenetic_transcription": "Epigenetic / transcriptional regulation",
    "other": "Other / uncertain mechanism",
    "unknown": "Unknown / no supporting API term",
}


# ---------------------------------------------------------------------------
# Trial (drug) mechanism vocabulary + keyword / target ruleset.
# Matched against OT mechanismOfAction text AND the mechanism's target symbols.
# ---------------------------------------------------------------------------

TRIAL_MECHANISM_KEYWORDS = {
    "amyloid": {
        "keywords": ["amyloid", "secretase", "bace", "a4 protein"],
        "targets": {"APP", "BACE1", "PSEN1", "PSEN2"},
    },
    "tau": {
        "keywords": ["tau", "microtubule"],
        "targets": {"MAPT"},
    },
    "cholinergic_symptomatic": {
        "keywords": [
            "cholinesterase", "acetylcholine", "cholinergic", "nmda",
            "glutamate receptor",
        ],
        "targets": {"ACHE", "BCHE", "CHRNA7", "GRIN2B"},
    },
    "inflammation_microglia": {
        "keywords": [
            "immun", "inflamm", "microglia", "tnf", "interleukin", "complement",
        ],
        "targets": {"TREM2", "CD33"},
    },
    "lipid_metabolism": {
        "keywords": [
            "lipid", "cholesterol", "statin", "hmg-coa", "apolipoprotein",
            "ppar", "insulin", "glp-1",
        ],
        "targets": set(),
    },
    "vascular": {
        "keywords": [
            "angiotensin", "adrenergic", "calcium channel", "anticoagul",
            "antiplatelet", "vascular",
        ],
        "targets": set(),
    },
    "synaptic_neuroprotection": {
        "keywords": [
            "neuroprotect", "bdnf", "ngf", "sigma", "serotonin", "dopamine",
            "monoamine",
        ],
        "targets": set(),
    },
    "diagnostic_biomarker": {
        "keywords": [],
        "targets": set(),
    },
}

# Tie-break priority for the drug primary_mechanism projection (highest first).
MECHANISM_PRIORITY = [
    "amyloid",
    "tau",
    "cholinergic_symptomatic",
    "inflammation_microglia",
    "lipid_metabolism",
    "vascular",
    "synaptic_neuroprotection",
    "diagnostic_biomarker",
]

MECHANISM_OTHER = "other"

# Seed set of AD/ADRD-relevant drug INNs to probe against Open Targets. These are
# real drug NAMES (not generic keywords); OT resolves each to a ChEMBL id and the
# mechanism-of-action text we then classify is fully API-derived. The list is a
# transparent, auditable seed - it decides WHICH drugs we look up, never WHAT
# their mechanism is.
DRUG_SEEDS = [
    # anti-amyloid antibodies / small molecules
    "aducanumab", "lecanemab", "donanemab", "gantenerumab", "solanezumab",
    "bapineuzumab", "crenezumab", "ponezumab", "remternetug",
    "verubecestat", "lanabecestat", "atabecestat", "elenbecestat",
    "umibecestat", "semagacestat", "avagacestat", "tramiprosate",
    # anti-tau
    "semorinemab", "gosuranemab", "tilavonemab", "zagotenemab", "bepranemab",
    # cholinergic / symptomatic
    "donepezil", "rivastigmine", "galantamine", "memantine", "tacrine",
    # inflammation / microglia
    "al002", "etanercept", "ibuprofen", "naproxen", "celecoxib",
    "sargramostim", "minocycline",
    # lipid / metabolic
    "semaglutide", "liraglutide", "metformin", "pioglitazone", "rosiglitazone",
    "atorvastatin", "simvastatin",
    # vascular
    "aspirin", "clopidogrel", "warfarin", "cilostazol", "nilvadipine",
    "telmisartan",
    # synaptic / neuroprotection
    "levetiracetam", "riluzole", "cerebrolysin",
]


# ---------------------------------------------------------------------------
# Gene id resolution
# ---------------------------------------------------------------------------

def resolve_lookup_id(gene_rec):
    """Choose the best mygene lookup id for a gene record.

    Preference order:
      1. Ensembl gene id (gene_id starting with 'ENSG')
      2. Entrez number (gene_id 'ENTREZ:<n>' -> <n>, or first entrez_ids entry)
    Returns (lookup_id, id_kind) or (None, None) if only a bare symbol/alias is
    available (caller falls back to the mygene query endpoint).
    """
    gene_id = str(gene_rec.get("gene_id") or "")
    if gene_id.startswith("ENSG"):
        return gene_id, "ensembl"
    if gene_id.startswith("ENTREZ:"):
        num = gene_id.split(":", 1)[1].strip()
        if num:
            return num, "entrez"
    entrez_ids = gene_rec.get("entrez_ids") or []
    for eid in entrez_ids:
        if eid:
            return str(eid).strip(), "entrez"
    return None, None


def _cache_name(text):
    """Filesystem-safe cache stem for an id/symbol."""
    return common.slug(text) or "unnamed"


def query_symbol_to_ensembl(symbol):
    """Resolve a bare symbol/alias to (ensembl_id, entrez_id) via mygene query.

    Returns (ensembl_or_None, entrez_or_None). Never raises on a 'no hits'
    answer; genuine transport errors propagate to the caller, which records
    nothing rather than inventing data. We prefer Ensembl (needed for the OT
    target lookup) but also surface Entrez for the mygene doc fetch.
    """
    if not symbol:
        return None, None
    cache_path = MYGENE_QUERY_CACHE_DIR / (_cache_name(symbol) + ".json")
    data = common.get_json(
        MYGENE_QUERY_URL,
        params={
            "q": symbol,
            "species": "human",
            "fields": "symbol,entrezgene,ensembl",
            "size": 1,
        },
        cache_path=cache_path,
    )
    hits = (data or {}).get("hits") or []
    if not hits:
        return None, None
    hit = hits[0]
    ensembl = hit.get("ensembl")
    ensg = None
    if isinstance(ensembl, dict):
        ensg = ensembl.get("gene")
    elif isinstance(ensembl, list) and ensembl:
        first = ensembl[0]
        if isinstance(first, dict):
            ensg = first.get("gene")
    ensg = ensg if (isinstance(ensg, str) and ensg.startswith("ENSG")) else None
    entrez = hit.get("entrezgene")
    entrez = str(entrez) if entrez else None
    return ensg, entrez


# ---------------------------------------------------------------------------
# mygene.info fetch
# ---------------------------------------------------------------------------

def fetch_mygene(lookup_id):
    """Fetch a mygene gene doc; return the parsed dict or None on 404.

    404 (unknown id) is treated as 'no data' (returns None) so a stray
    alias/clone id does not abort the whole build. Server errors still raise.
    """
    cache_path = MYGENE_CACHE_DIR / (_cache_name(lookup_id) + ".json")
    try:
        return common.get_json(
            MYGENE_GENE_URL + str(lookup_id),
            params={"fields": "symbol,uniprot,go"},
            cache_path=cache_path,
        )
    except RuntimeError as err:
        if "status=404" in str(err):
            return None
        raise


def extract_go(mygene_doc):
    """Collect ALL GO annotations across BP/MF/CC as {id, term, category}.

    Keeps every annotation (order-preserving, de-duplicated by (category,id)) -
    not just those that match a bucket - for full transparency.
    """
    if not isinstance(mygene_doc, dict):
        return []
    go = mygene_doc.get("go") or {}
    if not isinstance(go, dict):
        return []
    out = []
    seen = set()
    for ns in ("BP", "MF", "CC"):
        entries = go.get(ns)
        if entries is None:
            continue
        if isinstance(entries, dict):  # single-term namespaces come as a dict
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            term = entry.get("term")
            gid = entry.get("id")
            if not term:
                continue
            key = (ns, gid, term)
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": gid, "term": term, "category": ns})
    return out


def extract_uniprot_accessions(mygene_doc):
    """Collect Swiss-Prot UniProt accessions from a mygene doc.

    Only Swiss-Prot (reviewed) accessions are used for Reactome mapping; TrEMBL
    entries are ignored to keep pathway evidence high-quality. Returns a list.
    """
    if not isinstance(mygene_doc, dict):
        return []
    uni = mygene_doc.get("uniprot") or {}
    if not isinstance(uni, dict):
        return []
    sp = uni.get("Swiss-Prot")
    accs = []
    if isinstance(sp, str):
        accs.append(sp)
    elif isinstance(sp, list):
        for acc in sp:
            if isinstance(acc, str):
                accs.append(acc)
    out = []
    seen = set()
    for acc in accs:
        if acc and acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


# ---------------------------------------------------------------------------
# Reactome fetch
# ---------------------------------------------------------------------------

def fetch_reactome_pathways(uniprot_acc):
    """Fetch ALL human Reactome pathways for a UniProt accession.

    Returns a list of {stId, name}. A 404 (no mapping) yields []. The Reactome
    ContentService returns 404 for accessions with no pathway mapping; that is
    treated as 'no pathways'.
    """
    cache_path = REACTOME_CACHE_DIR / (_cache_name(uniprot_acc) + ".json")
    try:
        data = common.get_json(
            REACTOME_URL + str(uniprot_acc) + "/pathways",
            params={"species": "9606"},
            cache_path=cache_path,
        )
    except RuntimeError as err:
        if "status=404" in str(err):
            return []
        raise
    out = []
    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get("displayName")
            if name:
                out.append({"stId": entry.get("stId"), "name": name})
    return out


# ---------------------------------------------------------------------------
# Open Targets fetch (target pathways)
# ---------------------------------------------------------------------------

_OT_TARGET_QUERY = (
    "query($id:String!){target(ensemblId:$id){approvedSymbol "
    "pathways{pathwayId pathway topLevelTerm}}}"
)


def fetch_ot_target_pathways(ensembl_id):
    """Fetch ALL Open Targets target pathways for an Ensembl gene id.

    Returns (approved_symbol_or_None, [{pathwayId, pathway, topLevelTerm}...]).
    A target that OT does not know (data.target is null) yields (None, []). We
    never fabricate: on a missing target we record nothing for this source.
    """
    if not ensembl_id:
        return None, []
    cache_path = OT_TARGETS_CACHE_DIR / (_cache_name(ensembl_id) + ".json")
    data = common.post_json(
        OT_GRAPHQL_URL,
        {"query": _OT_TARGET_QUERY, "variables": {"id": ensembl_id}},
        cache_path=cache_path,
    )
    target = (((data or {}).get("data") or {}).get("target")) or None
    if not target:
        return None, []
    symbol = target.get("approvedSymbol")
    out = []
    seen = set()
    for p in (target.get("pathways") or []):
        if not isinstance(p, dict):
            continue
        pid = p.get("pathwayId")
        name = p.get("pathway")
        top = p.get("topLevelTerm")
        key = (pid, name, top)
        if key in seen:
            continue
        seen.add(key)
        out.append({"pathwayId": pid, "pathway": name, "topLevelTerm": top})
    return symbol, out


# ---------------------------------------------------------------------------
# Open Targets fetch (drug search + mechanism of action)
# ---------------------------------------------------------------------------

_OT_DRUG_SEARCH_QUERY = (
    "query($s:String!){search(queryString:$s,entityNames:[\"drug\"],"
    "page:{index:0,size:1}){hits{id name}}}"
)
_OT_DRUG_QUERY = (
    "query($c:String!){drug(chemblId:$c){name mechanismsOfAction{rows{"
    "mechanismOfAction targets{id approvedSymbol}}}}}"
)


def search_drug_chembl(name):
    """Resolve a drug NAME to (chemblId, ot_name) via OT search.

    Returns (None, None) when OT has no drug hit. Membership is API-decided.
    """
    if not name:
        return None, None
    cache_path = OT_DRUGS_CACHE_DIR / ("search_" + _cache_name(name) + ".json")
    data = common.post_json(
        OT_GRAPHQL_URL,
        {"query": _OT_DRUG_SEARCH_QUERY, "variables": {"s": name}},
        cache_path=cache_path,
    )
    hits = ((((data or {}).get("data") or {}).get("search") or {})
            .get("hits") or [])
    if not hits:
        return None, None
    hit = hits[0]
    return hit.get("id"), hit.get("name")


def fetch_drug_moa(chembl_id):
    """Fetch a drug's name + ALL mechanism-of-action rows from Open Targets.

    Returns (ot_name_or_None, [{mechanismOfAction, targets:[{id,symbol}]}...]).
    Records everything OT reports (no collapse). Missing drug -> (None, []).
    """
    if not chembl_id:
        return None, []
    cache_path = OT_DRUGS_CACHE_DIR / ("drug_" + _cache_name(chembl_id) + ".json")
    data = common.post_json(
        OT_GRAPHQL_URL,
        {"query": _OT_DRUG_QUERY, "variables": {"c": chembl_id}},
        cache_path=cache_path,
    )
    drug = (((data or {}).get("data") or {}).get("drug")) or None
    if not drug:
        return None, []
    rows_out = []
    rows = ((drug.get("mechanismsOfAction") or {}).get("rows")) or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        moa = row.get("mechanismOfAction")
        targets = []
        for t in (row.get("targets") or []):
            if isinstance(t, dict):
                targets.append({
                    "id": t.get("id"),
                    "approvedSymbol": t.get("approvedSymbol"),
                })
        rows_out.append({"mechanismOfAction": moa, "targets": targets})
    return drug.get("name"), rows_out


# ---------------------------------------------------------------------------
# Bucketing (genes) - MULTI-VALUED: keep every signal from every source
# ---------------------------------------------------------------------------

def _match_buckets_in_text(text):
    """Return the set of buckets whose keywords appear in the lowercased text."""
    if not text:
        return set()
    text_l = text.lower()
    hits = set()
    for bucket, keywords in PATHWAY_BUCKET_KEYWORDS.items():
        for kw in keywords:
            if kw in text_l:
                hits.add((bucket, kw))
                break  # one keyword per bucket per term is enough
    return hits


def collect_gene_signals(go_terms, reactome_pathways, ot_pathways):
    """Collect ALL AD-bucket signals across the three sources.

    Each signal is ``{bucket, source, matched_term}``. A gene keeps every match
    (one per bucket per source-term), never collapsing to a single winner.
    Returns the list of signals.
    """
    signals = []

    def emit(source, term):
        for bucket, _kw in _match_buckets_in_text(term):
            signals.append(
                {"bucket": bucket, "source": source, "matched_term": term}
            )

    for g in go_terms:
        emit(SRC_GO, g.get("term"))
    for p in reactome_pathways:
        emit(SRC_REACTOME, p.get("name"))
    for p in ot_pathways:
        # OT contributes both the pathway name and its top-level term.
        emit(SRC_OT, p.get("pathway"))
        emit(SRC_OT, p.get("topLevelTerm"))
    return signals


def compute_term_idf(all_signals_by_gene):
    """Compute idf(term) for every matched term across the whole gene set.

    ``all_signals_by_gene`` is a list (one entry per gene) of that gene's signal
    list. Document frequency counts a term ONCE per gene (a gene that carries the
    same matched_term from several sources still counts as one document), so the
    idf reflects how many genes are annotated with the term, not how many
    (source, term) rows exist.

        idf(term) = log(n_genes / (1 + genes_with_term))

    With n_genes in the denominator's numerator and the +1 smoothing, a term that
    every gene carries approaches log(n / (1+n)) ~ 0 (uninformative), while a term
    only one gene carries approaches log(n / 2) (highly specific). Returns
    (idf_by_term, n_genes).
    """
    n_genes = len(all_signals_by_gene)
    df = {}  # matched_term -> number of genes carrying it (at least once)
    for signals in all_signals_by_gene:
        terms_here = {s["matched_term"] for s in signals}
        for term in terms_here:
            df[term] = df.get(term, 0) + 1
    idf_by_term = {}
    for term, freq in df.items():
        # n_genes is >= 1 whenever any signal exists; guard defensively anyway.
        idf_by_term[term] = math.log(n_genes / (1.0 + freq)) if n_genes else 0.0
    return idf_by_term, n_genes


def rank_buckets(signals, idf_by_term):
    """Rank buckets by IDF (specificity) weight, argmax -> primary_bucket.

    For each bucket, score = sum of idf(matched_term) over the DISTINCT terms
    that mapped to it (a term counts once per bucket even if several sources
    surfaced it, so a single ubiquitous term cannot dominate by repetition). The
    bucket with the highest score wins; ties (including all-zero-idf ties) are
    broken by ``BUCKET_PRIORITY`` so rare/specific mechanisms outrank generic
    ones.

    Returns (buckets_sorted, primary_bucket, primary_support, bucket_scores):
      - buckets_sorted: distinct buckets, highest idf-score first (tie-break by
        BUCKET_PRIORITY);
      - primary_bucket: top of that list, or None if there are no signals;
      - primary_support: {source, matched_term, idf} rows supporting the primary
        bucket (all supporting signals, so provenance stays complete);
      - bucket_scores: {bucket: rounded idf-score} for every candidate bucket.
    """
    if not signals:
        return [], None, [], {}

    # bucket -> {term: idf} for the DISTINCT terms mapped to that bucket.
    idf_terms_by_bucket = {}
    for s in signals:
        term = s["matched_term"]
        idf_terms_by_bucket.setdefault(s["bucket"], {})[term] = (
            idf_by_term.get(term, 0.0)
        )

    # Score by PEAK specificity: the MAX idf of any single term mapped to the
    # bucket (summed idf as a tiebreak). Max, not sum, so a gene's single most-
    # distinctive term decides its bucket -- e.g. APP's rare "amyloid" term beats
    # its many moderately-common "synaptic/neuronal" terms (sum lets breadth win
    # and mislabels canonical genes: APP->synaptic, MAPT->synaptic).
    bucket_scores = {
        bucket: max(term_idfs.values())
        for bucket, term_idfs in idf_terms_by_bucket.items()
    }
    bucket_sum = {
        bucket: sum(term_idfs.values())
        for bucket, term_idfs in idf_terms_by_bucket.items()
    }

    def sort_key(bucket):
        priority = (BUCKET_PRIORITY.index(bucket)
                    if bucket in BUCKET_PRIORITY else len(BUCKET_PRIORITY))
        return (-bucket_scores[bucket], -bucket_sum[bucket], priority)

    buckets_sorted = sorted(bucket_scores.keys(), key=sort_key)
    primary = buckets_sorted[0]
    primary_support = [
        {"source": s["source"], "matched_term": s["matched_term"],
         "idf": round(idf_by_term.get(s["matched_term"], 0.0), 4)}
        for s in signals if s["bucket"] == primary
    ]
    rounded_scores = {b: round(v, 4) for b, v in bucket_scores.items()}
    return buckets_sorted, primary, primary_support, rounded_scores


def build_notes(primary_bucket, primary_support, sources, bucket_scores=None,
                max_terms=3):
    """Build the CSV notes column: short, explainable IDF provenance.

    Records that the primary is IDF-derived, its idf-score, and the highest-idf
    supporting terms (most specific first) with their per-term idf. Example:
      "primary=amyloid IDF-derived (score=6.21); go:amyloid-beta formation
       (idf=5.10); reactome:Amyloid fiber formation (idf=4.30) (+2 more) |
       captured 63 GO / 16 Reactome / 16 OT"
    """
    counts = "captured %d GO / %d Reactome / %d OT" % (
        len(sources.get("go") or []),
        len(sources.get("reactome") or []),
        len(sources.get("open_targets") or []),
    )
    if not primary_bucket:
        return "no AD bucket matched; " + counts
    score = (bucket_scores or {}).get(primary_bucket)
    score_str = "" if score is None else " (score=%.2f)" % score

    # Show the most SPECIFIC supporting terms first (highest idf), de-duplicated
    # by (source, term) so a term surfaced by several sources is shown once.
    seen = set()
    uniq = []
    for s in primary_support:
        key = (s["source"], s["matched_term"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    uniq.sort(key=lambda s: -(s.get("idf") or 0.0))
    shown = [
        "%s:%s (idf=%.2f)" % (s["source"], s["matched_term"], s.get("idf") or 0.0)
        for s in uniq[:max_terms]
    ]
    more = len(uniq) - len(shown)
    tail = " (+%d more)" % more if more > 0 else ""
    return "primary=%s IDF-derived%s; %s%s | %s" % (
        primary_bucket,
        score_str,
        "; ".join(shown),
        tail,
        counts,
    )


# ---------------------------------------------------------------------------
# Per-gene processing
# ---------------------------------------------------------------------------

def process_gene(gene_rec):
    """Resolve one gene through the 3 sources and CAPTURE its signals.

    This is the first (per-gene, API-hitting) pass. It records everything the
    ontologies say - full sources.* and the multi-valued ad_bucket_signals - but
    deliberately does NOT choose a primary_bucket: primary selection is IDF
    (specificity) weighted and therefore needs the whole gene set's term
    document-frequencies, which are only known after every gene is captured. See
    ``finalize_gene`` for the second pass.

    Never fabricates: when an id cannot be resolved or an API returns nothing,
    the corresponding source list is empty and no signal is emitted.
    """
    gene_id = gene_rec.get("gene_id")
    symbol = gene_rec.get("symbol") or gene_id

    lookup_id, id_kind = resolve_lookup_id(gene_rec)
    ensembl_id = lookup_id if id_kind == "ensembl" else None
    resolved_via = id_kind

    # Fetch mygene; if the primary id 404s, resolve the symbol via query.
    mygene_doc = None
    if lookup_id is not None:
        mygene_doc = fetch_mygene(lookup_id)
    if mygene_doc is None:
        q_ensg, q_entrez = query_symbol_to_ensembl(symbol)
        q_id = q_ensg or q_entrez
        if q_id is not None:
            lookup_id = q_id
            ensembl_id = ensembl_id or q_ensg
            resolved_via = "query:" + ("ensembl" if q_ensg else "entrez")
            mygene_doc = fetch_mygene(lookup_id)
    elif ensembl_id is None:
        # We had an Entrez id for mygene but need an Ensembl id for OT; try query.
        q_ensg, _ = query_symbol_to_ensembl(symbol)
        if q_ensg:
            ensembl_id = q_ensg

    go_terms = extract_go(mygene_doc)
    uniprot_accs = extract_uniprot_accessions(mygene_doc)

    reactome_pathways = []
    seen_st = set()
    for acc in uniprot_accs:
        for p in fetch_reactome_pathways(acc):
            key = p.get("stId") or p.get("name")
            if key in seen_st:
                continue
            seen_st.add(key)
            reactome_pathways.append(p)

    ot_symbol, ot_pathways = fetch_ot_target_pathways(ensembl_id)

    sources = {
        "go": go_terms,
        "reactome": reactome_pathways,
        "open_targets": ot_pathways,
    }

    signals = collect_gene_signals(go_terms, reactome_pathways, ot_pathways)

    return {
        "gene_id": gene_id,
        "symbol": symbol,
        "uniprot": uniprot_accs,
        "resolved_via": resolved_via,
        "ensembl_id": ensembl_id,
        "ot_approved_symbol": ot_symbol,
        "sources": sources,
        "ad_bucket_signals": signals,
        # Filled in by finalize_gene once the global term IDF is known:
        "buckets": [],
        "primary_bucket": None,
        "primary_support": [],
        "bucket_scores": {},
        "notes": None,
    }


def finalize_gene(rec, idf_by_term):
    """Second pass: choose the IDF-weighted primary bucket for a captured gene.

    Mutates ``rec`` in place, filling buckets / primary_bucket / primary_support
    (each supporting term annotated with its idf) / bucket_scores / notes using
    the whole-gene-set ``idf_by_term`` from ``compute_term_idf``. Returns rec.
    """
    signals = rec["ad_bucket_signals"]
    buckets, primary, primary_support, bucket_scores = rank_buckets(
        signals, idf_by_term
    )
    rec["buckets"] = buckets
    rec["primary_bucket"] = primary
    rec["primary_support"] = primary_support
    rec["bucket_scores"] = bucket_scores
    rec["notes"] = build_notes(primary, primary_support, rec["sources"],
                               bucket_scores)
    return rec


# ---------------------------------------------------------------------------
# Drug mechanism classification - MULTI-VALUED
# ---------------------------------------------------------------------------

def _classify_moa(moa_text, target_symbols):
    """Return the set of (mechanism, matched_term) for one MoA row.

    Matches BOTH the mechanism-of-action free text (keywords) AND the target
    symbols against the trial mechanism vocabulary. Keeps every distinct match.
    """
    hits = set()
    text_l = (moa_text or "").lower()
    symbols_up = {(s or "").upper() for s in target_symbols if s}
    for mech, rule in TRIAL_MECHANISM_KEYWORDS.items():
        for kw in rule["keywords"]:
            if kw in text_l:
                hits.add((mech, kw))
                break
        for sym in rule["targets"]:
            if sym in symbols_up:
                hits.add((mech, "target:" + sym))
    return hits


def collect_drug_signals(moa_rows):
    """Collect ALL mechanism signals across a drug's MoA rows.

    Each signal is ``{mechanism, source, matched_term}`` with source
    'open_targets' (all drug mechanism evidence is OT-derived). Keeps everything.
    """
    signals = []
    for row in moa_rows:
        moa = row.get("mechanismOfAction")
        symbols = [t.get("approvedSymbol") for t in (row.get("targets") or [])]
        for mech, term in _classify_moa(moa, symbols):
            signals.append({
                "mechanism": mech,
                "source": "open_targets",
                "matched_term": term,
                "moa_text": moa,
            })
    return signals


def rank_mechanisms(signals):
    """Rank drug mechanisms by number of distinct supporting MoA-term matches.

    Returns (mechanisms_sorted, primary_mechanism, primary_support). If a drug
    resolved but nothing matched the vocabulary, primary is 'other'.
    """
    if not signals:
        return [], None, []
    terms_by_mech = {}
    for s in signals:
        terms_by_mech.setdefault(s["mechanism"], set()).add(s["matched_term"])

    def sort_key(mech):
        priority = (MECHANISM_PRIORITY.index(mech)
                    if mech in MECHANISM_PRIORITY else len(MECHANISM_PRIORITY))
        return (-len(terms_by_mech[mech]), priority)

    mechanisms_sorted = sorted(terms_by_mech.keys(), key=sort_key)
    primary = mechanisms_sorted[0]
    primary_support = [
        {"source": s["source"], "matched_term": s["matched_term"],
         "moa_text": s["moa_text"]}
        for s in signals if s["mechanism"] == primary
    ]
    return mechanisms_sorted, primary, primary_support


def process_drug(seed_name):
    """Resolve one drug NAME through Open Targets and build the rich record.

    Returns the record, or None if OT has no drug hit for the seed (we record
    nothing rather than fabricate). All captured MoA rows are preserved.
    """
    chembl_id, ot_search_name = search_drug_chembl(seed_name)
    if not chembl_id:
        return None
    ot_name, moa_rows = fetch_drug_moa(chembl_id)
    signals = collect_drug_signals(moa_rows)
    mechanisms, primary, primary_support = rank_mechanisms(signals)
    if not primary and moa_rows:
        # resolved with MoA rows but nothing matched the vocabulary -> 'other'
        primary = MECHANISM_OTHER
        mechanisms = [MECHANISM_OTHER]
    return {
        "seed_name": seed_name,
        "chembl_id": chembl_id,
        "name": ot_name or ot_search_name or seed_name,
        "sources": {"open_targets_moa": moa_rows},
        "mechanisms_of_action": moa_rows,
        "mechanism_signals": signals,
        "mechanisms": mechanisms,
        "primary_mechanism": primary,
        "primary_support": primary_support,
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_gene_pathway_csv(gene_records):
    """Write the THIN single-colour projection CSV.

    Only genes with a real primary_bucket are emitted (pathway_group = the
    primary_bucket projection); genes with no bucket match are omitted (never
    forced). Rows de-duplicated by gene_symbol (first winner wins) and sorted.
    """
    rows = []
    seen = set()
    for rec in gene_records:
        primary = rec.get("primary_bucket")
        if not primary:
            continue
        symbol = rec["symbol"]
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rows.append({
            "gene_symbol": symbol,
            "pathway_group": primary,
            "notes": rec["notes"],
        })
    rows.sort(key=lambda r: (r["pathway_group"], r["gene_symbol"]))

    GENE_PATHWAY_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = GENE_PATHWAY_CSV.with_name(GENE_PATHWAY_CSV.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(CSV_HEADER_COMMENT + "\n")
        writer = csv.DictWriter(
            fh, fieldnames=["gene_symbol", "pathway_group", "notes"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    os.replace(str(tmp), str(GENE_PATHWAY_CSV))
    return rows


def write_gene_sidecar(gene_records):
    """Write the rich per-gene JSONL (ALL genes, recording everything)."""
    records = []
    for rec in gene_records:
        records.append({
            "gene_id": rec["gene_id"],
            "symbol": rec["symbol"],
            "uniprot": rec["uniprot"],
            "resolved_via": rec["resolved_via"],
            "ensembl_id": rec["ensembl_id"],
            "ot_approved_symbol": rec["ot_approved_symbol"],
            "sources": rec["sources"],
            "ad_bucket_signals": rec["ad_bucket_signals"],
            "buckets": rec["buckets"],
            "primary_bucket": rec["primary_bucket"],
            "primary_support": rec["primary_support"],
            "bucket_scores": rec["bucket_scores"],
            "notes": rec["notes"],
        })
    return common.write_jsonl(GENE_SIDECAR_JSONL, records)


def build_pathways_jsonl(csv_rows):
    """Group CSV rows by pathway_group -> pathway records (map/pathways.py shape).

    Deliberately mirrors map/pathways.py so the downstream score/scores.py step
    (which reruns in run_all after this) keeps working unchanged.
    """
    groups = {}
    for row in csv_rows:
        groups.setdefault(row["pathway_group"], []).append(row)

    records = []
    for group in sorted(groups):
        members = groups[group]
        gene_ids = sorted({m["gene_symbol"] for m in members})
        notes_by_gene = {}
        for m in members:
            if m["notes"]:
                notes_by_gene[m["gene_symbol"]] = m["notes"]
        records.append({
            "pathway_id": "api:" + common.slug(group),
            "label": GROUP_LABELS.get(group, group),
            "source": "api:mygene.info+reactome+opentargets (gene_pathway_build.py)",
            "gene_ids": gene_ids,
            "mechanism_group": group,
            "gene_count": len(gene_ids),
            "notes_by_gene": notes_by_gene,
        })
    return common.write_jsonl(PATHWAYS_JSONL, records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    genes = common.read_jsonl(GENES_JSONL)
    common.log("loaded %d genes from %s" % (len(genes), GENES_JSONL))

    # Pass 1: capture ontology annotations + AD-bucket signals for every gene.
    gene_records = []
    for i, gene_rec in enumerate(genes, start=1):
        gene_records.append(process_gene(gene_rec))
        if i % 25 == 0 or i == len(genes):
            common.log("captured %d/%d genes" % (i, len(genes)))

    # Compute term specificity (IDF) over the WHOLE captured gene set, then use it
    # to choose each gene's IDF-weighted primary bucket (pass 2). This is what
    # lets rare terms (amyloid-beta, tau) outrank ubiquitous ones (neuron).
    idf_by_term, n_idf_genes = compute_term_idf(
        [r["ad_bucket_signals"] for r in gene_records]
    )
    common.log("computed IDF over %d genes / %d distinct matched terms"
               % (n_idf_genes, len(idf_by_term)))
    for rec in gene_records:
        finalize_gene(rec, idf_by_term)

    csv_rows = write_gene_pathway_csv(gene_records)
    common.log("wrote %d rows to %s" % (len(csv_rows), GENE_PATHWAY_CSV))

    n_side = write_gene_sidecar(gene_records)
    common.log("wrote %d gene sidecar records to %s" % (n_side, GENE_SIDECAR_JSONL))

    n_path = build_pathways_jsonl(csv_rows)
    common.log("wrote %d pathway records to %s" % (n_path, PATHWAYS_JSONL))

    # Summary to stderr.
    bucketed = sum(1 for r in gene_records if r["primary_bucket"])
    multi = sum(1 for r in gene_records if len(r["buckets"]) > 1)
    common.log(
        "summary: %d genes, %d with a primary bucket, %d carry >1 AD-bucket signal"
        % (len(gene_records), bucketed, multi)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
