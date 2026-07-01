"""Build the Track A <-> Track B integration bridge (BridgeV2).

Track A (topic-dynamics) publishes a snapshot of topic clusters + member papers
(now enriched with populated abstracts + MeSH descriptors + chemical descriptors
+ references). Track B (translational-evidence) owns curated gene / GWAS /
pathway / trial evidence. This script joins the two into a STRUCTURED-FIRST,
fully-provenanced bridge and produces three shared outputs:

  1. data/processed/shared/topic_evidence_links.jsonl
     One record per (topic, evidence_type, evidence_id, link_type) join,
     conforming to shared/schemas/topic_evidence_link.schema.json. EVERY link
     records HOW and WHY it was made:
       - method       machine-readable join method (see priority below)
       - confidence   high | medium | low
       - provenance   object carrying the exact join key(s) + counts
       - notes        human-readable explanation

  2. data/processed/shared/topic_evidence_rollup.jsonl
     One record per topic: the Track B half of the frontend map_data.json
     (dominant pathway_group, top genes, top GWAS, related trials, aggregated
     scores, evidence counts, disease_groups with per-group provenance).

  3. data/processed/shared/topic_bridge_manifest.json
     Snapshot-awareness metadata (input counts + provenance note).

LINK METHODS, in PRIORITY order (structured joins first, regex demoted last):

  (1) pmid_join            link_type=paper_overlap        confidence=high
      Topic member PMIDs INTERSECT GWAS PMIDs. Emits, per matched association,
      a topic->gwas_association link, and per reported gene a topic->gene link.
      Structured ID join on PMID; unambiguous.

  (2) mesh_ui_join         link_type=mesh_annotation      confidence=high
      Member papers' MeSH UIs classified via the API-DERIVED MeSH tree
      (mesh_tree.classify_mesh_ui; MeSH SPARQL, Dementia branch C10.228.140.380)
      -> topic->disease links (evidence_type="disease"), tallied per
      disease_group. Structured controlled-vocabulary UI join with ZERO hand
      definition: the disease buckets are read live from the MeSH tree.

  (3) chemical_ui_crosswalk link_type=chemical_annotation confidence=high
      Member papers' chemical UIs looked up in the curated chemical_gene
      crosswalk -> topic->gene links. Structured controlled-vocabulary UI join
      (a gene-product descriptor is an unambiguous pointer to its gene).

  (4) gene_pathway_curated link_type=pathway_mapping      confidence=medium
      Pathway groups of the topic's STRUCTURALLY-linked genes (via the curated
      gene_pathway.csv), weighted by summed genetic_support -> topic->pathway.

  (5) regex_symbol_match   link_type=gene_mention         confidence=low
      FALLBACK ONLY, for genes NOT already linked via (1) or (3). Case-sensitive
      whole-word symbol match in title+abstract, with MIN_SYMBOL_LEN /
      AMBIGUOUS_SYMBOLS safeguards. Clearly the lowest-confidence signal.

Dedup key is (topic, evidence_type, evidence_id, link_type). When the same
(topic, gene) is found by several methods, the HIGHEST-confidence link is kept
and the others' methods are recorded in provenance.also_found_by.

No counts are hardcoded: they are read from the inputs, so a re-run against the
full corpus just works.

STDLIB-only Python 3.9. Run:
    python3 translational-evidence/exports/build_topic_bridge.py
Then validate:
    python3 translational-evidence/validate.py \
        data/processed/shared/topic_evidence_links.jsonl
"""

import csv
import json
import re
import sys
import pathlib
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)

# API-derived MeSH -> disease_group classifier (replaces the hand-curated
# map/mesh_disease.csv). Import from the map/ package one level down.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "map"))
import mesh_tree  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------

SNAPSHOT_DIR = common.INTERIM_DIR / "track_a_snapshot"
TOPIC_CLUSTERS = SNAPSHOT_DIR / "topic_clusters.jsonl"
PAPERS = SNAPSHOT_DIR / "papers.jsonl"

GENES = common.PROCESSED_DIR / "genes.jsonl"
GWAS = common.PROCESSED_DIR / "gwas_associations.jsonl"
PATHWAYS = common.PROCESSED_DIR / "pathways.jsonl"
TRIALS = common.PROCESSED_DIR / "trials.jsonl"

# Curated crosswalks (authoritative, structured join tables).
# NOTE: mesh_ui -> disease_group is NO LONGER a hand CSV. It is derived live from
# the MeSH tree via translational-evidence/map/mesh_tree.py (imported above).
MAP_DIR = common.TE_DIR / "map"
GENE_PATHWAY_CSV = MAP_DIR / "gene_pathway.csv"      # gene_symbol -> pathway_group
CHEMICAL_GENE_CSV = MAP_DIR / "chemical_gene.csv"    # chemical_ui -> gene_symbol

LINKS_OUT = common.SHARED_PROCESSED_DIR / "topic_evidence_links.jsonl"
ROLLUP_OUT = common.SHARED_PROCESSED_DIR / "topic_evidence_rollup.jsonl"
MANIFEST_OUT = common.SHARED_PROCESSED_DIR / "topic_bridge_manifest.json"


# ---------------------------------------------------------------------------
# regex_symbol_match config (FALLBACK ONLY)
# ---------------------------------------------------------------------------

# Symbols shorter than this are skipped for regex gene_mention (collision-prone).
MIN_SYMBOL_LEN = 3

# Curated blocklist of gene symbols that collide with common English words or
# generic tokens when matched case-sensitively against free text. Excluded from
# regex_symbol_match only; a gene can still be linked structurally via
# pmid_join or chemical_ui_crosswalk, which are unambiguous.
AMBIGUOUS_SYMBOLS = {
    "SET",     # English word "set"; a real gene symbol elsewhere.
    "MAX",     # English word / abbreviation "max".
    "CAMK",    # generic kinase-family stem.
    "REST",    # English word "rest" (RE1-silencing transcription factor).
    "AR",      # 2 chars anyway, but generic ("AR"/augmented reality).
    "IMPACT",  # English word.
    "MICE",    # English word "mice".
    "CELL",    # English word "cell".
}


# ---------------------------------------------------------------------------
# Loading / indexing
# ---------------------------------------------------------------------------

def _read_csv_rows(csv_path):
    """Yield DictReader rows for a CSV file (utf-8)."""
    with pathlib.Path(csv_path).open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            yield row


def load_gene_pathway_map(csv_path):
    """Return {gene_symbol: pathway_group} from the authoritative CSV."""
    mapping = {}
    for row in _read_csv_rows(csv_path):
        symbol = (row.get("gene_symbol") or "").strip()
        group = (row.get("pathway_group") or "").strip()
        if symbol and group:
            mapping[symbol] = group
    return mapping


def load_chemical_gene_map(csv_path):
    """Return {chemical_ui: {"gene_symbol":..., "chemical_term":...}}."""
    mapping = {}
    for row in _read_csv_rows(csv_path):
        ui = (row.get("chemical_ui") or "").strip()
        symbol = (row.get("gene_symbol") or "").strip()
        term = (row.get("chemical_term") or "").strip()
        if ui and symbol:
            mapping[ui] = {"gene_symbol": symbol, "chemical_term": term}
    return mapping


def index_papers(papers):
    """Build paper_id -> pmid and paper_id -> detail (mesh/chem/text) maps."""
    pid_to_pmid = {}
    pid_to_detail = {}
    for p in papers:
        pid = p.get("paper_id")
        if not pid:
            continue
        pid_to_pmid[pid] = p.get("pmid")
        title = p.get("title") or ""
        abstract = p.get("abstract") or ""
        pid_to_detail[pid] = {
            "mesh": p.get("mesh") or [],
            "chemicals": p.get("chemicals") or [],
            "references": p.get("references") or [],
            "title": title,
            "abstract": abstract,
        }
    return pid_to_pmid, pid_to_detail


def index_genes(genes):
    """Build symbol -> gene record map (symbols are unique in the source)."""
    sym_to_gene = {}
    for g in genes:
        sym = g.get("symbol")
        if sym:
            sym_to_gene[sym] = g
    return sym_to_gene


def index_gwas(gwas):
    """Build the set of GWAS pmids and pmid -> [associations] map."""
    gwas_pmids = set()
    pmid_to_assocs = defaultdict(list)
    for a in gwas:
        pmid = a.get("pmid")
        if not pmid:
            continue
        gwas_pmids.add(pmid)
        pmid_to_assocs[pmid].append(a)
    return gwas_pmids, pmid_to_assocs


def index_pathways(pathways):
    """Build mechanism_group(pathway_group) -> pathway record map."""
    group_to_pathway = {}
    for p in pathways:
        group = p.get("mechanism_group")
        if group:
            group_to_pathway[group] = p
    return group_to_pathway


def index_trials_by_mechanism(trials):
    """Build trial mechanism_group -> [trial records] map."""
    by_mech = defaultdict(list)
    for t in trials:
        mech = t.get("mechanism_group")
        if mech:
            by_mech[mech].append(t)
    return by_mech


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _genetic_support(gene):
    return (gene.get("evidence_scores") or {}).get("genetic_support")


def _functional_support(gene):
    return (gene.get("evidence_scores") or {}).get("functional_support")


def _mean(values):
    """Mean of non-None numeric values, or None if there are none."""
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def _first_rsid(assoc):
    return (assoc.get("variant") or {}).get("rsid")


# ---------------------------------------------------------------------------
# Core: build links + gather per-topic evidence for the rollup
# ---------------------------------------------------------------------------

def build_topic(cluster, ctx):
    """Build links + a rollup record for a single Track A cluster.

    Emits links strictly in method priority order so the highest-confidence
    method "wins" the dedup key, and lower-confidence rediscoveries of the same
    (topic, gene) get recorded under provenance.also_found_by.
    """
    topic_id = cluster["topic_id"]
    label = cluster.get("label")
    member_paper_ids = list(cluster.get("paper_ids") or [])
    n_members = len(member_paper_ids)

    pid_to_pmid = ctx["pid_to_pmid"]
    pid_to_detail = ctx["pid_to_detail"]
    sym_to_gene = ctx["sym_to_gene"]
    gwas_pmids = ctx["gwas_pmids"]
    pmid_to_assocs = ctx["pmid_to_assocs"]
    sym_to_group = ctx["sym_to_group"]
    classify_mesh_ui = ctx["classify_mesh_ui"]
    chemical_gene = ctx["chemical_gene"]
    group_to_pathway = ctx["group_to_pathway"]

    links = []
    # Dedup key set for (topic, evidence_type, evidence_id, link_type).
    by_key = {}

    def add_link(link):
        """Add a link, or fold a duplicate into the incumbent's also_found_by.

        Because links are added in priority order, the FIRST (highest-conf)
        link for a key wins. A later, distinct-method duplicate for the same
        key is recorded as an also_found_by entry on the incumbent, so no
        provenance is lost.
        """
        key = (link["topic_id"], link["evidence_type"],
               link["evidence_id"], link["link_type"])
        incumbent = by_key.get(key)
        if incumbent is None:
            by_key[key] = link
            links.append(link)
            return True
        # Same key already emitted: record the alternate method (if new).
        alt = {"method": link["method"], "confidence": link["confidence"]}
        also = incumbent["provenance"].setdefault("also_found_by", [])
        if not any(a.get("method") == alt["method"] for a in also):
            also.append(alt)
        return False

    def note_cross_method(evidence_type, evidence_id, method, confidence):
        """Record that a gene (any link_type) was ALSO found by `method`.

        The task requires: when the same (topic, gene) is found by multiple
        methods, keep the highest-confidence link and record the others'
        methods in provenance.also_found_by. Different structured methods use
        different link_types (paper_overlap vs chemical_annotation), so the
        dedup key differs; we instead annotate the incumbent gene link of the
        HIGHER-confidence method with an also_found_by entry.
        """
        best = None
        best_rank = -1
        for lk in links:
            if (lk["evidence_type"] == evidence_type
                    and lk["evidence_id"] == evidence_id):
                rank = _CONFIDENCE_RANK.get(lk["confidence"], 0)
                if rank > best_rank:
                    best, best_rank = lk, rank
        if best is None:
            return
        alt = {"method": method, "confidence": confidence}
        if alt["method"] == best["method"]:
            return
        also = best["provenance"].setdefault("also_found_by", [])
        if not any(a.get("method") == method for a in also):
            also.append(alt)

    # Genes linked to this topic (symbol -> gene record) + how.
    linked_genes = {}                 # symbol -> gene record (any structured/regex)
    structural_gene_syms = set()      # via pmid_join or chemical_ui_crosswalk
    paper_overlap_syms = set()        # via pmid_join
    chemical_gene_syms = set()        # via chemical_ui_crosswalk
    regex_gene_syms = set()           # via regex_symbol_match

    # =====================================================================
    # (1) pmid_join  ->  gwas_association (paper_overlap, high)
    #                ->  gene            (paper_overlap, high)
    # =====================================================================
    # PMIDs shared by this cluster and the GWAS corpus (structured ID join).
    overlap_pmids = []
    for pid in member_paper_ids:
        pmid = pid_to_pmid.get(pid)
        if pmid and pmid in gwas_pmids:
            overlap_pmids.append(pmid)
    overlap_pmids = sorted(set(overlap_pmids))

    n_gwas_assoc = 0
    gene_pmid_support = defaultdict(set)   # symbol -> {pmids} (for gene links)
    gene_accessions = defaultdict(set)     # symbol -> {study_accessions}
    for pmid in overlap_pmids:
        for assoc in pmid_to_assocs.get(pmid, []):
            assoc_id = assoc.get("association_id")
            if not assoc_id:
                continue
            study_accession = assoc.get("study_accession")
            emitted = add_link({
                "topic_id": topic_id,
                "evidence_type": "gwas_association",
                "evidence_id": assoc_id,
                "link_type": "paper_overlap",
                "supporting_paper_ids": [pmid],
                "score": 1.0,
                "method": "pmid_join",
                "confidence": "high",
                "provenance": {
                    "join_key": "pmid",
                    "pmid": pmid,
                    "study_accession": study_accession,
                    "association_id": assoc_id,
                },
                "notes": ("GWAS association %s (study %s) published in topic "
                          "corpus (PMID %s)"
                          % (assoc_id, study_accession, pmid)),
            })
            if emitted:
                n_gwas_assoc += 1
            # reported_genes of this association -> collect for gene links.
            for rg in (assoc.get("reported_genes") or []):
                if rg in sym_to_gene:
                    gene_pmid_support[rg].add(pmid)
                    if study_accession:
                        gene_accessions[rg].add(study_accession)

    # Emit one gene link per gene reported across the overlap pmids.
    for symbol in sorted(gene_pmid_support):
        gene = sym_to_gene[symbol]
        pmids = sorted(gene_pmid_support[symbol])
        add_link({
            "topic_id": topic_id,
            "evidence_type": "gene",
            "evidence_id": gene["gene_id"],
            "link_type": "paper_overlap",
            "supporting_paper_ids": pmids,
            "score": round(len(pmids) / n_members, 4) if n_members else 1.0,
            "method": "pmid_join",
            "confidence": "high",
            "provenance": {
                "join_key": "pmid",
                "reported_symbol": symbol,
                "pmids": pmids,
                "study_accessions": sorted(gene_accessions[symbol]),
                "n_pmids": len(pmids),
            },
            "notes": ("gene %s reported by %d GWAS publication(s) in topic "
                      "corpus" % (symbol, len(pmids))),
        })
        linked_genes[symbol] = gene
        structural_gene_syms.add(symbol)
        paper_overlap_syms.add(symbol)

    # =====================================================================
    # (2) mesh_ui_join  ->  disease (mesh_annotation, high)
    # =====================================================================
    # Tally member papers per disease_group by classifying each MeSH UI against
    # the API-DERIVED MeSH tree (mesh_tree.classify_mesh_ui; MeSH SPARQL,
    # Dementia branch C10.228.140.380). No hand crosswalk is consulted.
    disease_papers = defaultdict(set)      # disease_group -> {pmid}
    disease_major = defaultdict(int)       # disease_group -> n major mentions
    disease_uis = defaultdict(lambda: defaultdict(int))  # dg -> {ui: n_papers}
    disease_ui_meta = {}                    # ui -> {term, tree_number}
    for pid in member_paper_ids:
        detail = pid_to_detail.get(pid) or {}
        pmid = pid_to_pmid.get(pid)
        seen_ui_this_paper = set()
        for m in detail.get("mesh", []):
            ui = m.get("ui")
            if not ui:
                continue
            hit = classify_mesh_ui(ui)
            if hit is None:
                continue   # UI is not under a Dementia branch of the MeSH tree
            dg = hit["disease_group"]
            disease_ui_meta[ui] = {
                "mesh_term": hit.get("label") or m.get("term"),
                "tree_number": hit.get("tree_number"),
            }
            if pmid:
                disease_papers[dg].add(pmid)
            if ui not in seen_ui_this_paper:
                disease_uis[dg][ui] += 1
                seen_ui_this_paper.add(ui)
            if m.get("major"):
                disease_major[dg] += 1

    disease_rollup = []   # for the rollup's disease_groups (with counts)
    for dg in sorted(disease_papers, key=lambda d: (-len(disease_papers[d]), d)):
        pmids = sorted(disease_papers[dg])
        n_papers = len(pmids)
        # Per-UI breakdown (which MeSH descriptors contributed, tree number, and
        # how often) -- records HOW+WHY each UI classified into this group.
        ui_breakdown = [
            {
                "mesh_ui": ui,
                "mesh_term": disease_ui_meta.get(ui, {}).get("mesh_term"),
                "tree_number": disease_ui_meta.get(ui, {}).get("tree_number"),
                "n_papers": cnt,
            }
            for ui, cnt in sorted(disease_uis[dg].items(),
                                  key=lambda kv: (-kv[1], kv[0]))
        ]
        add_link({
            "topic_id": topic_id,
            "evidence_type": "disease",
            "evidence_id": "disease:%s" % dg,
            "link_type": "mesh_annotation",
            "supporting_paper_ids": pmids,
            "score": round(n_papers / n_members, 4) if n_members else 0.0,
            "method": "mesh_ui_join",
            "confidence": "high",
            "provenance": {
                "join_key": "mesh_ui",
                "disease_group": dg,
                "n_papers": n_papers,
                "n_major": disease_major[dg],
                "mesh_uis": ui_breakdown,
                "classifier": ("mesh_tree (MeSH SPARQL, branch "
                               "C10.228.140.380)"),
            },
            "notes": ("disease group '%s' via API-derived MeSH tree "
                      "(mesh_tree, branch C10.228.140.380) in %d/%d member "
                      "papers (%d major)"
                      % (dg, n_papers, n_members, disease_major[dg])),
        })
        disease_rollup.append({
            "disease_group": dg,
            "n_papers": n_papers,
            "n_major": disease_major[dg],
            "mesh_uis": [b["mesh_ui"] for b in ui_breakdown],
        })

    # =====================================================================
    # (3) chemical_ui_crosswalk  ->  gene (chemical_annotation, high)
    # =====================================================================
    # A gene-product descriptor (e.g. "tau Proteins" D016875) unambiguously
    # points at its gene (MAPT) via the curated chemical_gene crosswalk.
    chem_gene_support = defaultdict(set)          # symbol -> {pmid}
    chem_gene_uis = defaultdict(lambda: defaultdict(int))  # sym -> {ui: n}
    chem_ui_terms = {}
    for pid in member_paper_ids:
        detail = pid_to_detail.get(pid) or {}
        pmid = pid_to_pmid.get(pid)
        seen_ui_this_paper = set()
        for c in detail.get("chemicals", []):
            ui = c.get("ui")
            if not ui or ui not in chemical_gene:
                continue
            symbol = chemical_gene[ui]["gene_symbol"]
            if symbol not in sym_to_gene:
                continue   # crosswalk points at a gene not in Track B evidence
            chem_ui_terms[ui] = chemical_gene[ui]["chemical_term"] or c.get("term")
            if pmid:
                chem_gene_support[symbol].add(pmid)
            if ui not in seen_ui_this_paper:
                chem_gene_uis[symbol][ui] += 1
                seen_ui_this_paper.add(ui)

    for symbol in sorted(chem_gene_support):
        gene = sym_to_gene[symbol]
        pmids = sorted(chem_gene_support[symbol])
        n_papers = len(pmids)
        ui_breakdown = [
            {
                "chemical_ui": ui,
                "chemical_term": chem_ui_terms.get(ui),
                "n_papers": cnt,
            }
            for ui, cnt in sorted(chem_gene_uis[symbol].items(),
                                  key=lambda kv: (-kv[1], kv[0]))
        ]
        add_link({
            "topic_id": topic_id,
            "evidence_type": "gene",
            "evidence_id": gene["gene_id"],
            "link_type": "chemical_annotation",
            "supporting_paper_ids": pmids,
            "score": round(n_papers / n_members, 4) if n_members else 0.0,
            "method": "chemical_ui_crosswalk",
            "confidence": "high",
            "provenance": {
                "join_key": "chemical_ui",
                "gene_symbol": symbol,
                "n_papers": n_papers,
                "chemical_uis": ui_breakdown,
            },
            "notes": ("gene %s via curated chemical-UI crosswalk "
                      "(gene-product descriptor) in %d/%d member papers"
                      % (symbol, n_papers, n_members)),
        })
        linked_genes.setdefault(symbol, gene)
        structural_gene_syms.add(symbol)
        chemical_gene_syms.add(symbol)
        # If this gene was ALSO found structurally via pmid_join, annotate that
        # (higher- or equal-confidence) link's also_found_by.
        note_cross_method("gene", gene["gene_id"],
                          "chemical_ui_crosswalk", "high")
        if symbol in paper_overlap_syms:
            # pmid_join link exists too; record chemical crosswalk on it.
            note_cross_method("gene", gene["gene_id"], "pmid_join", "high")

    # =====================================================================
    # (4) gene_pathway_curated  ->  pathway (pathway_mapping, medium)
    # =====================================================================
    # Weight each represented pathway_group by summed genetic_support of the
    # topic's STRUCTURALLY-linked genes in that group (curated CSV mapping).
    group_support = defaultdict(float)
    group_genes = defaultdict(list)
    total_grouped_support = 0.0
    for symbol in structural_gene_syms:
        group = sym_to_group.get(symbol)
        if not group:
            continue
        gs = _genetic_support(sym_to_gene[symbol]) or 0.0
        group_support[group] += gs
        group_genes[group].append(symbol)
        total_grouped_support += gs

    dominant_group = None
    if group_support:
        dominant_group = max(
            group_support,
            key=lambda g: (group_support[g], len(group_genes[g])),
        )

    for group in sorted(group_support, key=lambda g: (-group_support[g], g)):
        pathway = group_to_pathway.get(group)
        if pathway is None:
            continue
        via_genes = sorted(group_genes[group])
        summed = round(group_support[group], 4)
        share = (round(group_support[group] / total_grouped_support, 4)
                 if total_grouped_support else None)
        add_link({
            "topic_id": topic_id,
            "evidence_type": "pathway",
            "evidence_id": pathway["pathway_id"],
            "link_type": "pathway_mapping",
            "supporting_paper_ids": [],
            "score": share,
            "method": "gene_pathway_curated",
            "confidence": "medium",
            "provenance": {
                "join_key": "gene_symbol->pathway_group",
                "pathway_group": group,
                "via_genes": via_genes,
                "summed_genetic_support": summed,
                "share": share,
                "is_dominant": group == dominant_group,
            },
            "notes": ("pathway '%s' via %d structurally-linked gene(s) %s "
                      "(summed genetic_support %.4f%s)"
                      % (group, len(via_genes), via_genes, summed,
                         "; dominant" if group == dominant_group else "")),
        })

    # =====================================================================
    # (5) regex_symbol_match  ->  gene (gene_mention, low)  [FALLBACK ONLY]
    # =====================================================================
    # ONLY for genes NOT already linked structurally (pmid_join / chemical_ui).
    member_texts = []
    for pid in member_paper_ids:
        detail = pid_to_detail.get(pid) or {}
        member_texts.append(
            (pid, detail.get("title", ""), detail.get("abstract", ""))
        )
    for symbol, gene in sym_to_gene.items():
        if symbol in structural_gene_syms:
            continue  # already linked by a structured method; skip the fallback
        if len(symbol) < MIN_SYMBOL_LEN or symbol in AMBIGUOUS_SYMBOLS:
            continue
        pattern = ctx["symbol_patterns"].get(symbol)
        if pattern is None:
            continue
        matching_pmids = []
        matched_in_abstract = 0
        matched_in_title = 0
        for pid, title, abstract in member_texts:
            hit_abs = bool(abstract) and bool(pattern.search(abstract))
            hit_title = bool(title) and bool(pattern.search(title))
            if hit_abs or hit_title:
                pmid = pid_to_pmid.get(pid)
                if pmid:
                    matching_pmids.append(pmid)
                if hit_abs:
                    matched_in_abstract += 1
                if hit_title:
                    matched_in_title += 1
        if not matching_pmids:
            continue
        matching_pmids = sorted(set(matching_pmids))
        n_match = len(matching_pmids)
        if matched_in_abstract and matched_in_title:
            matched_in = "abstract+title"
        elif matched_in_abstract:
            matched_in = "abstract"
        else:
            matched_in = "title"
        add_link({
            "topic_id": topic_id,
            "evidence_type": "gene",
            "evidence_id": gene["gene_id"],
            "link_type": "gene_mention",
            "supporting_paper_ids": matching_pmids,
            "score": round(n_match / n_members, 4) if n_members else 0.0,
            "method": "regex_symbol_match",
            "confidence": "low",
            "provenance": {
                "join_key": "case_sensitive_whole_word_symbol",
                "gene_symbol": symbol,
                "n_match": n_match,
                "n_members": n_members,
                "matched_in": matched_in,
                "fallback": True,
                "note": ("low-confidence text match; used only because the "
                         "gene was not linked by any structured method"),
            },
            "notes": ("symbol '%s' whole-word match in %d/%d member papers "
                      "(%s) - low-confidence regex fallback"
                      % (symbol, n_match, n_members, matched_in)),
        })
        linked_genes.setdefault(symbol, gene)
        regex_gene_syms.add(symbol)

    # -----------------------------------------------------------------------
    # Rollup record
    # -----------------------------------------------------------------------
    n_pathways = len({lk["evidence_id"] for lk in links
                      if lk["evidence_type"] == "pathway"})
    rollup = build_rollup(
        topic_id=topic_id,
        label=label,
        linked_genes=linked_genes,
        paper_overlap_syms=paper_overlap_syms,
        chemical_gene_syms=chemical_gene_syms,
        regex_gene_syms=regex_gene_syms,
        overlap_pmids=overlap_pmids,
        pmid_to_assocs=pmid_to_assocs,
        dominant_group=dominant_group,
        group_to_pathway=group_to_pathway,
        n_gwas_assoc=n_gwas_assoc,
        n_pathways=n_pathways,
        disease_rollup=disease_rollup,
        ctx=ctx,
    )

    return links, rollup


def build_rollup(topic_id, label, linked_genes, paper_overlap_syms,
                 chemical_gene_syms, regex_gene_syms, overlap_pmids,
                 pmid_to_assocs, dominant_group, group_to_pathway,
                 n_gwas_assoc, n_pathways, disease_rollup, ctx):
    """Assemble the Track B half of the frontend record for one topic."""
    trials_by_mech = ctx["trials_by_mech"]

    # top_genes: up to 8 linked genes ranked by genetic_support.
    genes_sorted = sorted(
        linked_genes.items(),
        key=lambda kv: (_genetic_support(kv[1]) or -1.0, kv[0]),
        reverse=True,
    )
    top_genes = []
    for symbol, gene in genes_sorted[:8]:
        top_genes.append({
            "symbol": symbol,
            "gene_id": gene["gene_id"],
            "genetic_support": _genetic_support(gene),
            "functional_support": _functional_support(gene),
            "disease_groups": list(gene.get("disease_groups") or []),
        })

    # top_gwas: up to 8 from paper_overlap associations (dedup by association_id).
    top_gwas = []
    seen_assoc = set()
    for pmid in overlap_pmids:
        for assoc in pmid_to_assocs.get(pmid, []):
            aid = assoc.get("association_id")
            if not aid or aid in seen_assoc:
                continue
            seen_assoc.add(aid)
            top_gwas.append({
                "association_id": aid,
                "rsid": _first_rsid(assoc),
                "reported_genes": list(assoc.get("reported_genes") or []),
                "trait": assoc.get("trait"),
            })
            if len(top_gwas) >= 8:
                break
        if len(top_gwas) >= 8:
            break

    # Dominant pathway record + its trial-mechanism crosswalk.
    dominant_pathway = (group_to_pathway.get(dominant_group)
                        if dominant_group else None)
    pathway_scores = (dominant_pathway or {}).get("scores") or {}

    # trials: up to 10 whose mechanism_group == the dominant pathway's mapped
    # trial mechanism (via the pathway record's crosswalk).
    trials_out = []
    mapped_trial_mech = pathway_scores.get("mapped_trial_mechanism")
    if mapped_trial_mech:
        for t in trials_by_mech.get(mapped_trial_mech, [])[:10]:
            phases = t.get("phases") or []
            trials_out.append({
                "nct_id": t.get("nct_id"),
                "brief_title": t.get("brief_title"),
                "phase": phases[-1] if phases else None,
                "mechanism_group": t.get("mechanism_group"),
            })

    # Aggregated scores.
    mean_genetic = _mean([_genetic_support(g) for g in linked_genes.values()])
    mean_functional = _mean(
        [_functional_support(g) for g in linked_genes.values()]
    )
    scores = {
        "genetic_support": mean_genetic,
        "functional_support": mean_functional,
        "clinical_translation": pathway_scores.get("clinical_translation"),
        "clinical_saturation": pathway_scores.get("clinical_saturation"),
        "translation_gap": pathway_scores.get("translation_gap"),
        "_method": (
            "genetic_support/functional_support = mean over linked genes; "
            "clinical_* / translation_gap copied from the dominant "
            "pathway_group record (by summed genetic_support of "
            "structurally-linked genes)"
        ),
    }

    # disease_groups: from mesh_ui_join (structured), sorted by paper count,
    # with per-group counts. Also expose the bare sorted list for back-compat.
    disease_groups_sorted = sorted(
        {d["disease_group"] for d in disease_rollup}
    )

    evidence_counts = {
        "n_linked_genes": len(linked_genes),
        "n_gene_mention": len(regex_gene_syms),
        "n_paper_overlap_genes": len(paper_overlap_syms),
        "n_chemical_genes": len(chemical_gene_syms),
        "n_gwas_assoc": n_gwas_assoc,
        "n_pathways": n_pathways,
        "n_diseases": len(disease_rollup),
    }

    provenance = (
        "Track A cluster + Track B evidence structured-first join "
        "(pmid_join > mesh_ui_join > chemical_ui_crosswalk > "
        "gene_pathway_curated > regex_symbol_match) on the track_a_snapshot "
        "(%d papers / %d clusters)."
        % (ctx["track_a_papers"], ctx["track_a_clusters"])
    )

    return {
        "topic_id": topic_id,
        "label": label,
        "pathway_group": dominant_group,
        "top_genes": top_genes,
        "top_gwas": top_gwas,
        "trials": trials_out,
        "scores": scores,
        "evidence_counts": evidence_counts,
        "disease_groups": disease_groups_sorted,
        "disease_group_details": disease_rollup,
        "provenance": provenance,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # --- load inputs -------------------------------------------------------
    clusters = common.read_jsonl(TOPIC_CLUSTERS)
    papers = common.read_jsonl(PAPERS)
    genes = common.read_jsonl(GENES)
    gwas = common.read_jsonl(GWAS)
    pathways = common.read_jsonl(PATHWAYS)
    trials = common.read_jsonl(TRIALS)

    track_a_papers = len(papers)
    track_a_clusters = len(clusters)
    track_b_genes = len(genes)
    common.log("loaded Track A: %d papers, %d clusters"
               % (track_a_papers, track_a_clusters))
    common.log("loaded Track B: %d genes, %d gwas assoc, %d pathways, "
               "%d trials" % (len(genes), len(gwas), len(pathways),
                              len(trials)))

    # --- indexes -----------------------------------------------------------
    pid_to_pmid, pid_to_detail = index_papers(papers)
    sym_to_gene = index_genes(genes)
    gwas_pmids, pmid_to_assocs = index_gwas(gwas)
    group_to_pathway = index_pathways(pathways)
    trials_by_mech = index_trials_by_mechanism(trials)
    sym_to_group = load_gene_pathway_map(GENE_PATHWAY_CSV)
    chemical_gene = load_chemical_gene_map(CHEMICAL_GENE_CSV)
    # API-derived MeSH -> disease_group map (built on import of mesh_tree).
    mesh_disease = mesh_tree.DERIVED_MESH_DISEASE
    common.log("crosswalks: gene->pathway=%d, chemical->gene=%d; "
               "mesh->disease=%d (API-derived via mesh_tree, path=%s)"
               % (len(sym_to_group), len(chemical_gene), len(mesh_disease),
                  mesh_tree.FETCH_PATH))

    # Precompile a case-sensitive whole-word regex per eligible symbol
    # (regex_symbol_match fallback only).
    symbol_patterns = {}
    for symbol in sym_to_gene:
        if len(symbol) < MIN_SYMBOL_LEN or symbol in AMBIGUOUS_SYMBOLS:
            continue
        symbol_patterns[symbol] = re.compile(r"\b" + re.escape(symbol) + r"\b")

    # Audit the blocklist against the ACTUAL snapshot text.
    corpus_text = "\n".join(
        (d.get("title", "") + " " + d.get("abstract", ""))
        for d in pid_to_detail.values()
    )
    blocked_would_hit = []
    blocked_no_hit = []
    for symbol in sorted(AMBIGUOUS_SYMBOLS):
        if symbol not in sym_to_gene or len(symbol) < MIN_SYMBOL_LEN:
            continue
        pat = re.compile(r"\b" + re.escape(symbol) + r"\b")
        (blocked_would_hit if pat.search(corpus_text)
         else blocked_no_hit).append(symbol)
    common.log("regex fallback blocklist: %s" % sorted(AMBIGUOUS_SYMBOLS))
    common.log("  present as Track B genes AND would have matched text: %s"
               % (blocked_would_hit or "none"))

    ctx = {
        "pid_to_pmid": pid_to_pmid,
        "pid_to_detail": pid_to_detail,
        "sym_to_gene": sym_to_gene,
        "gwas_pmids": gwas_pmids,
        "pmid_to_assocs": pmid_to_assocs,
        "sym_to_group": sym_to_group,
        "classify_mesh_ui": mesh_tree.classify_mesh_ui,
        "chemical_gene": chemical_gene,
        "group_to_pathway": group_to_pathway,
        "trials_by_mech": trials_by_mech,
        "symbol_patterns": symbol_patterns,
        "track_a_papers": track_a_papers,
        "track_a_clusters": track_a_clusters,
    }

    # --- build per topic ---------------------------------------------------
    all_links = []
    all_rollups = []
    per_topic_links = {}
    for cluster in clusters:
        links, rollup = build_topic(cluster, ctx)
        all_links.extend(links)
        all_rollups.append(rollup)
        per_topic_links[cluster["topic_id"]] = links

    # --- write outputs -----------------------------------------------------
    n_links = common.write_jsonl(LINKS_OUT, all_links)
    n_rollups = common.write_jsonl(ROLLUP_OUT, all_rollups)

    manifest = {
        "track_a_papers": track_a_papers,
        "track_a_clusters": track_a_clusters,
        "track_b_genes": track_b_genes,
        "n_links": n_links,
        "n_rollups": n_rollups,
        "crosswalks": {
            "gene_pathway": len(sym_to_group),
            "chemical_gene": len(chemical_gene),
            "mesh_disease": {
                "n_uis": len(mesh_disease),
                "source": "mesh_tree (API-derived, MeSH SPARQL "
                          "branch C10.228.140.380 + F03.615.400)",
                "path": mesh_tree.FETCH_PATH,
            },
        },
        "link_methods": _count_by(all_links, "method"),
        "link_types": _count_by(all_links, "link_type"),
        "link_confidence": _count_by(all_links, "confidence"),
        "generated_from": "track_a_snapshot",
    }
    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_OUT.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")

    common.log("wrote %d links -> %s" % (n_links, LINKS_OUT))
    common.log("wrote %d rollups -> %s" % (n_rollups, ROLLUP_OUT))
    common.log("wrote manifest -> %s" % MANIFEST_OUT)

    # --- report ------------------------------------------------------------
    print_report(all_links, all_rollups, per_topic_links)
    return 0


def _count_by(links, field):
    counts = defaultdict(int)
    for lk in links:
        counts[lk.get(field)] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))))


def print_report(all_links, rollups, per_topic_links):
    """Print counts by method / link_type / confidence + per-topic summary."""
    def _print_counts(title, counts):
        print("\n== %s ==" % title)
        for k, v in counts.items():
            print("  %-24s %d" % (k, v))

    print("\n=== BridgeV2 link counts (total %d) ===" % len(all_links))
    _print_counts("by method", _count_by(all_links, "method"))
    _print_counts("by link_type", _count_by(all_links, "link_type"))
    _print_counts("by confidence", _count_by(all_links, "confidence"))

    # Per-topic summary.
    print("\n=== per-topic link summary ===")
    header = ("topic_id", "label", "links", "gwas", "genes",
              "disease", "pathway", "pathway_group")
    rows = []
    rollup_by_id = {r["topic_id"]: r for r in rollups}
    for topic_id in sorted(per_topic_links):
        links = per_topic_links[topic_id]
        r = rollup_by_id.get(topic_id, {})
        rows.append((
            topic_id,
            (r.get("label") or "")[:30],
            str(len(links)),
            str(sum(1 for lk in links
                    if lk["evidence_type"] == "gwas_association")),
            str(sum(1 for lk in links if lk["evidence_type"] == "gene")),
            str(sum(1 for lk in links if lk["evidence_type"] == "disease")),
            str(sum(1 for lk in links if lk["evidence_type"] == "pathway")),
            str(r.get("pathway_group")),
        ))
    widths = [max(len(header[i]), max((len(row[i]) for row in rows),
                                      default=0)) for i in range(len(header))]
    print("  ".join(header[i].ljust(widths[i]) for i in range(len(header))))
    print("  ".join("-" * widths[i] for i in range(len(header))))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(header))))

    # One sample of each NEW link type (method/confidence/provenance).
    print("\n=== sample of each link type ===")
    seen_types = set()
    order = ["paper_overlap", "mesh_annotation", "chemical_annotation",
             "pathway_mapping", "gene_mention"]
    samples = {}
    for lk in all_links:
        lt = lk["link_type"]
        if lt not in samples:
            samples[lt] = lk
    for lt in order:
        if lt in samples:
            print("\n--- %s ---" % lt)
            print(json.dumps(samples[lt], indent=2, ensure_ascii=False,
                             sort_keys=True))
            seen_types.add(lt)


if __name__ == "__main__":
    sys.exit(main())
