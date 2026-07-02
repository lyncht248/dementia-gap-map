#!/usr/bin/env python3
"""Compute a transparent, machine-readable per-entity METRICS layer (Track B).

This is the "score" step for the translational-evidence track. It reads the
already-processed, schema-valid Track B inputs and emits ONE flat metrics record
per entity (gene / variant / pathway). Every metric is a PRIMITIVE:

  * a COUNT (``n_*``), a RAW external/observed value (``best_neglog10p``,
    ``max_l2g``, Open Targets datatype scores), a BOOLEAN (``has_approval``), a
    small LIST (cell types, disease groups), or a SIMPLE a/b RATIO, and
  * carries a short ``source`` provenance string so it is EXPLAINABLE. Ratios
    additionally carry a ``formula`` note and are ``null`` when the denominator
    is 0.

Design philosophy (from the project owner): expose raw, composable COUNTS +
RATIOS + RAW values only. Do NOT ship opinionated, weighted 0-1 composites
(no ``0.5*x + 0.3*y``) and do NOT bake in verdict labels ("under-researched",
"under-translated", "emerging", "contradicted"). Downstream AGENTS compose those
higher-order judgements from these primitives (see METRICS.md worked examples).

This is a deliberate rewrite that REMOVES the old weighted composites
(``genetic.genetic_support``, ``functional.functional_support``,
``composite.translation_gap``) while KEEPING their raw COMPONENTS as standalone
primitives. Open Targets' OWN externally-maintained harmonic-sum scores are kept
RAW alongside our stats under ``open_targets.*`` and clearly labelled as external
reference scores, NOT our statistics.

Inputs (data/processed/translational-evidence/):
    genes.jsonl, gwas_associations.jsonl, functional_links.jsonl, trials.jsonl,
    pathways.jsonl, target_evidence.jsonl, gene_pathways_api.jsonl
Inputs (data/processed/shared/):
    topic_evidence_links.jsonl        (gene literature paper ids)
Inputs (data/interim/translational-evidence/, optional):
    track_a_snapshot/papers.jsonl     (pmid -> year, for recent-paper counts)

Output:
    data/processed/translational-evidence/entity_metrics.jsonl

Reproducibility: "now" for recency metrics is CURRENT_YEAR, read from the
TE_CURRENT_YEAR environment variable (default 2026). We never call
datetime.today() so a given input always yields the same output.

Run:
    python3 translational-evidence/score/entity_metrics.py
"""

import json
import os
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Reproducible "now" for recency metrics (documented, overridable).
# ---------------------------------------------------------------------------
CURRENT_YEAR = int(os.environ.get("TE_CURRENT_YEAR", "2026"))
RECENT_WINDOW = 3  # a year counts as "recent" if year >= CURRENT_YEAR - RECENT_WINDOW

OUT_PATH = common.PROCESSED_DIR / "entity_metrics.jsonl"
TRACK_A_PAPERS = (common.INTERIM_DIR / "track_a_snapshot" / "papers.jsonl")

# Open Targets' own external harmonic-sum score provenance label. These are
# reference scores maintained by Open Targets, NOT our statistics.
OT_SRC = "open_targets (external harmonic-sum score)"

# Note attached to every emitted record.
RECORD_NOTE = (
    "Metrics are PRIMITIVES: counts (n_*), raw observed/external values "
    "(best_neglog10p, max_l2g, Open Targets datatype scores), booleans "
    "(has_approval), small lists, and SIMPLE a/b ratios (null when b==0, each "
    "with a 'formula' note). No weighted 0-1 composites are shipped. "
    "Higher-order judgements (under-researched, under-translated, emerging, "
    "contradicted) are COMPOSED BY AGENTS from these primitives; see "
    "translational-evidence/score/METRICS.md for worked examples. "
    "open_targets.* are Open Targets' OWN external reference scores, not our stats."
)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(\d{4})")


def _year_of(date_str):
    """Extract a 4-digit year from a date-ish string, else None."""
    if not date_str:
        return None
    m = _YEAR_RE.search(str(date_str))
    if not m:
        return None
    return int(m.group(1))


def _metric(value, source):
    """Wrap a primitive metric value with its provenance note."""
    return {"value": value, "source": source}


def _ratio(a, b, source, formula):
    """Simple a/b ratio metric; value is null when b == 0 (or a/b is None).

    Carries both a ``source`` and a ``formula`` note. The numerator/denominator
    are treated as counts/raw values; when either is None we return None too.
    """
    if a is None or b is None or b == 0:
        value = None
    else:
        value = a / b
    return {"value": value, "source": source, "formula": formula}


def _is_recent(year):
    return year is not None and year >= CURRENT_YEAR - RECENT_WINDOW


_RECENT_NOTE = "; recent = year >= CURRENT_YEAR-%d (CURRENT_YEAR=%d)" % (
    RECENT_WINDOW, CURRENT_YEAR)


# ---------------------------------------------------------------------------
# Phase handling: we report the RAW max phase as a STRING label and a count-by-
# phase dict. No numeric phase score is emitted (that would be an opinionated
# ordering baked into a number). We keep only an explicit ordering for choosing
# the "max" label transparently.
# ---------------------------------------------------------------------------

def _norm_phase_token(token):
    """Normalise a phase string to a canonical uppercase underscore token."""
    if token is None:
        return ""
    s = str(token).strip().upper()
    s = s.replace("/", "_").replace("-", "_")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


# Transparent late-stage ordering used ONLY to pick the max-phase LABEL. This is
# an ordinal rank over CT.gov phase tokens, not a score baked into a metric.
_PHASE_RANK = {
    "EARLY_PHASE1": 1,
    "PHASE1": 2,
    "PHASE1_PHASE2": 3,
    "PHASE2": 4,
    "PHASE2_PHASE3": 5,
    "PHASE3": 6,
    "PHASE4": 7,
    "APPROVED": 8,
}
_NA_PHASE = "NA"  # observational / unknown / empty


def _phase_labels(phases):
    """Normalised, non-empty phase tokens for one trial (empty -> ['NA'])."""
    toks = [_norm_phase_token(p) for p in (phases or []) if p is not None]
    toks = [t for t in toks if t]
    return toks or [_NA_PHASE]


def _max_phase_label(phase_tokens):
    """Pick the highest-ranked phase LABEL from a bag of tokens (string)."""
    if not phase_tokens:
        return None
    best = None
    best_rank = -1
    for t in phase_tokens:
        r = _PHASE_RANK.get(t, 0)  # NA / unknown rank 0
        if r > best_rank:
            best_rank = r
            best = t
    return best


# Overall-status buckets.
_STOPPED_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
_COMPLETED_STATUS = "COMPLETED"
_APPROVAL_STATUS = "APPROVED_FOR_MARKETING"
_DRUG_TYPES = {"DRUG", "BIOLOGICAL"}


# ---------------------------------------------------------------------------
# Effect-direction classification for a single GWAS association.
# ---------------------------------------------------------------------------

def _direction_of(assoc):
    """Classify one association as 'risk' / 'protective' / None."""
    effect = assoc.get("effect") or {}
    d = effect.get("direction")
    if d == "increase":
        return "risk"
    if d == "decrease":
        return "protective"
    orr = effect.get("odds_ratio")
    if isinstance(orr, (int, float)) and not isinstance(orr, bool):
        if orr > 1:
            return "risk"
        if orr < 1:
            return "protective"
    beta = effect.get("beta")
    if isinstance(beta, (int, float)) and not isinstance(beta, bool):
        if beta > 0:
            return "risk"
        if beta < 0:
            return "protective"
    return None


def _dominant_direction(directions):
    """Dominant label in a list, or None on empty / exact tie."""
    n_risk = sum(1 for x in directions if x == "risk")
    n_prot = sum(1 for x in directions if x == "protective")
    if n_risk == 0 and n_prot == 0:
        return None
    if n_risk == n_prot:
        return None
    return "risk" if n_risk > n_prot else "protective"


# ---------------------------------------------------------------------------
# Load + index inputs
# ---------------------------------------------------------------------------

def load_inputs():
    genes = common.read_jsonl(common.PROCESSED_DIR / "genes.jsonl")
    gwas = common.read_jsonl(common.PROCESSED_DIR / "gwas_associations.jsonl")
    flinks = common.read_jsonl(common.PROCESSED_DIR / "functional_links.jsonl")
    trials = common.read_jsonl(common.PROCESSED_DIR / "trials.jsonl")
    pathways = common.read_jsonl(common.PROCESSED_DIR / "pathways.jsonl")
    target_evidence = common.read_jsonl(
        common.PROCESSED_DIR / "target_evidence.jsonl")
    gene_pathways_api = common.read_jsonl(
        common.PROCESSED_DIR / "gene_pathways_api.jsonl")
    return (genes, gwas, flinks, trials, pathways, target_evidence,
            gene_pathways_api)


def index_gwas_by_symbol(gwas):
    """symbol (upper) -> list of associations mentioning it in reported_genes."""
    by_symbol = {}
    for a in gwas:
        for sym in (a.get("reported_genes") or []):
            if not sym:
                continue
            by_symbol.setdefault(str(sym).upper(), []).append(a)
    return by_symbol


def index_flinks_by_rsid(flinks):
    by_rsid = {}
    for fl in flinks:
        rsid = fl.get("rsid")
        if rsid:
            by_rsid.setdefault(rsid, []).append(fl)
    return by_rsid


def index_flinks_by_gene(flinks):
    """gene_id -> list, gene_symbol(upper) -> list (functional_link records)."""
    by_id, by_sym = {}, {}
    for fl in flinks:
        gid = fl.get("gene_id")
        if gid:
            by_id.setdefault(gid, []).append(fl)
        sym = fl.get("gene_symbol")
        if sym:
            by_sym.setdefault(str(sym).upper(), []).append(fl)
    return by_id, by_sym


def index_trials_by_mechanism(trials):
    by_mech = {}
    for t in trials:
        mech = t.get("mechanism_group")
        by_mech.setdefault(mech, []).append(t)
    return by_mech


def index_pathways(pathways):
    """Return (by_mechanism_group, by_pathway_id)."""
    by_mech, by_id = {}, {}
    for p in pathways:
        by_mech[p.get("mechanism_group")] = p
        by_id[p.get("pathway_id")] = p
    return by_mech, by_id


def index_target_evidence(target_evidence):
    """gene_id -> chosen OT scores dict (Alzheimer disease row preferred).

    Each gene may have one row per disease; we prefer the 'Alzheimer disease'
    row, else the row with the highest 'overall' score, else the first row.
    """
    by_gene = {}
    for r in target_evidence:
        gid = r.get("gene_id")
        if gid:
            by_gene.setdefault(gid, []).append(r)

    chosen = {}
    for gid, rows in by_gene.items():
        pref = [r for r in rows if r.get("disease_label") == "Alzheimer disease"]
        if pref:
            chosen[gid] = pref[0]
            continue
        best = None
        best_overall = None
        for r in rows:
            ov = (r.get("scores") or {}).get("overall")
            if ov is not None and (best_overall is None or ov > best_overall):
                best_overall = ov
                best = r
        chosen[gid] = best if best is not None else rows[0]
    return chosen


def index_gene_pathways_api(gene_pathways_api):
    """gene_id -> record (mechanism buckets / primary_bucket)."""
    by_id = {}
    for r in gene_pathways_api:
        gid = r.get("gene_id")
        if gid:
            by_id[gid] = r
    return by_id


# ---------------------------------------------------------------------------
# Literature: gene paper ids from shared topic_evidence_links; recency from
# track_a papers pmid->year. Both optional (metrics are null / 0 if absent).
# ---------------------------------------------------------------------------

def load_paper_years():
    """pmid (str) -> publication year (int) from Track A snapshot; {} if absent."""
    if not TRACK_A_PAPERS.exists():
        return None  # signals "unavailable" so recent counts are null
    years = {}
    for p in common.read_jsonl(TRACK_A_PAPERS):
        pmid = p.get("pmid")
        yr = p.get("year")
        if pmid is None:
            continue
        if isinstance(yr, str):
            yr = _year_of(yr)
        if isinstance(yr, int) and not isinstance(yr, bool):
            years[str(pmid)] = yr
    return years


def index_gene_paper_ids(links_path):
    """gene_id -> set(supporting_paper_ids) across gene-typed topic links.

    Uses shared/topic_evidence_links.jsonl rows with evidence_type=='gene'
    (evidence_id is the gene_id). Union of supporting_paper_ids per gene. Returns
    {} if the file is absent.
    """
    by_gene = {}
    if not links_path.exists():
        return by_gene
    with links_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("evidence_type") != "gene":
                continue
            gid = r.get("evidence_id")
            if not gid:
                continue
            pids = r.get("supporting_paper_ids") or []
            if pids:
                by_gene.setdefault(gid, set()).update(str(p) for p in pids)
    return by_gene


# ---------------------------------------------------------------------------
# Trial-cohort summariser (shared by gene "inherited" and pathway metrics).
# ---------------------------------------------------------------------------

def summarise_trials(trial_list):
    """Aggregate a list of trial records into clinical count/phase/status stats.

    Returns a dict of plain primitives (no provenance) that callers wrap. All
    values are counts / booleans / string labels / dicts of counts.
    """
    n = len(trial_list)
    n_stopped = 0
    n_completed = 0
    n_with_results = 0
    has_approval = False
    drug_names = set()
    years = []
    phase_counts = {}       # phase label -> n trials whose max phase is that label
    all_max_tokens = []     # per-trial max phase token, to pick the cohort max

    for t in trial_list:
        status = t.get("overall_status")
        if status in _STOPPED_STATUSES:
            n_stopped += 1
        if status == _COMPLETED_STATUS:
            n_completed += 1
        if status == _APPROVAL_STATUS:
            has_approval = True
        if t.get("has_results") or t.get("hasResults"):
            n_with_results += 1

        max_tok = _max_phase_label(_phase_labels(t.get("phases")))
        if max_tok is not None:
            all_max_tokens.append(max_tok)
            phase_counts[max_tok] = phase_counts.get(max_tok, 0) + 1

        for iv in (t.get("interventions") or []):
            if (iv.get("type") or "").upper() in _DRUG_TYPES:
                name = iv.get("name")
                if name:
                    drug_names.add(str(name).strip().lower())

        yr = _year_of(t.get("start_date"))
        if yr is not None:
            years.append(yr)

    return {
        "n_trials": n,
        "n_by_phase": phase_counts,
        "max_phase": _max_phase_label(all_max_tokens),  # None only when n == 0
        "n_stopped": n_stopped,
        "n_completed": n_completed,
        "n_with_results": n_with_results,
        "has_approval": has_approval,
        "n_drugs": len(drug_names),
        "first_year": min(years) if years else None,
        "latest_year": max(years) if years else None,
        "n_recent": sum(1 for y in years if _is_recent(y)),
    }


# ---------------------------------------------------------------------------
# GENE metrics
# ---------------------------------------------------------------------------

def build_gene_record(gene, ctx):
    es = gene.get("evidence_scores") or {}
    fc = es.get("functional_support_components") or {}
    symbol = gene.get("symbol")
    gene_id = gene.get("gene_id")

    metrics = {}

    # --- mechanism (multi-valued buckets from gene_pathways_api) ---
    gpa = ctx["gpa_by_id"].get(gene_id) or {}
    buckets = list(gpa.get("buckets") or [])
    primary_bucket = gpa.get("primary_bucket")
    metrics["mechanism.buckets"] = _metric(
        buckets, "gene_pathways_api.jsonl:buckets (ad_bucket_signals membership)")
    metrics["mechanism.n_buckets"] = _metric(
        len(buckets), "gene_pathways_api.jsonl:buckets length")

    # --- genetic counts / raw values (gene aggregated evidence_scores) ---
    metrics["genetic.n_gwas_studies"] = _metric(
        es.get("gwas_study_count"), "genes.jsonl:evidence_scores.gwas_study_count")
    metrics["genetic.n_gwas_associations"] = _metric(
        es.get("gwas_association_count"),
        "genes.jsonl:evidence_scores.gwas_association_count")
    metrics["genetic.best_neglog10p"] = _metric(
        es.get("best_neglog10p"),
        "genes.jsonl:evidence_scores.best_neglog10p (raw -log10 p)")
    metrics["genetic.best_p_value"] = _metric(
        es.get("best_p_value"), "genes.jsonl:evidence_scores.best_p_value (raw p)")

    # --- genetic direction (recomputed from matching GWAS associations) ---
    assocs = ctx["gwas_by_symbol"].get((symbol or "").upper(), [])
    directions = [d for d in (_direction_of(a) for a in assocs) if d is not None]
    n_risk = sum(1 for x in directions if x == "risk")
    n_prot = sum(1 for x in directions if x == "protective")
    n_dir = n_risk + n_prot
    dir_src = ("gwas_associations.jsonl: reported_genes contains symbol; "
               "risk=direction increase|OR>1|beta>0, "
               "protective=direction decrease|OR<1|beta<0")
    metrics["genetic.n_risk"] = _metric(n_risk, dir_src)
    metrics["genetic.n_protective"] = _metric(n_prot, dir_src)
    metrics["genetic.n_conflicting"] = _metric(
        min(n_risk, n_prot), dir_src + "; n_conflicting=min(n_risk,n_protective)")
    metrics["genetic.direction_agreement_ratio"] = _ratio(
        max(n_risk, n_prot), n_dir, dir_src,
        "max(n_risk,n_protective)/(n_risk+n_protective)")

    # distinct variants (rsids) attributed to this gene via reported_genes.
    variant_rsids = {(a.get("variant") or {}).get("rsid")
                     for a in assocs if (a.get("variant") or {}).get("rsid")}
    metrics["genetic.n_variants"] = _metric(
        len(variant_rsids),
        "gwas_associations.jsonl: distinct variant.rsid where reported_genes "
        "contains symbol")

    # --- Open Targets raw datatype scores that live on the gene row (our
    #     "genetic.ot_*" reference values used elsewhere in the track). ---
    metrics["genetic.ot_genetic_association"] = _metric(
        es.get("open_targets_genetic_association"),
        "genes.jsonl:evidence_scores.open_targets_genetic_association (raw OT)")
    metrics["genetic.ot_overall"] = _metric(
        es.get("open_targets_overall"),
        "genes.jsonl:evidence_scores.open_targets_overall (raw OT)")

    # --- functional (raw L2G / QTL components; NO functional_support composite) ---
    metrics["functional.n_l2g_loci"] = _metric(
        fc.get("n_l2g_loci"),
        "genes.jsonl:evidence_scores.functional_support_components.n_l2g_loci")
    metrics["functional.max_l2g"] = _metric(
        fc.get("max_l2g"),
        "genes.jsonl:evidence_scores.functional_support_components.max_l2g (raw)")
    # QTL colocalisations for this gene: functional_links rows that are QTL
    # colocalisations (not L2G predictions). Falls back to the gene's own
    # has_brain_qtl_coloc component when no coloc rows are present.
    fl_gene = (ctx["flinks_by_gene_id"].get(gene_id)
               or ctx["flinks_by_gene_sym"].get((symbol or "").upper()) or [])
    coloc_rows = [fl for fl in fl_gene
                  if "coloc" in str(fl.get("evidence_type") or "").lower()]
    n_qtl_coloc = len(coloc_rows)
    if n_qtl_coloc == 0 and fc.get("has_brain_qtl_coloc"):
        n_qtl_coloc = 1
    metrics["functional.n_qtl_coloc"] = _metric(
        n_qtl_coloc,
        "functional_links.jsonl: coloc-type rows for this gene; else 1 if "
        "genes.jsonl functional_support_components.has_brain_qtl_coloc")
    cell_types = sorted({fl.get("cell_type") for fl in fl_gene
                         if fl.get("cell_type")})
    if not cell_types:
        cell_types = list(fc.get("coloc_cell_types") or [])
    metrics["functional.cell_types"] = _metric(
        cell_types,
        "functional_links.jsonl:cell_type for this gene (else "
        "functional_support_components.coloc_cell_types)")
    metrics["functional.ot_rna_expression"] = _metric(
        es.get("open_targets_rna_expression"),
        "genes.jsonl:evidence_scores.open_targets_rna_expression (raw OT)")
    metrics["functional.ot_affected_pathway"] = _metric(
        es.get("open_targets_affected_pathway"),
        "genes.jsonl:evidence_scores.open_targets_affected_pathway (raw OT)")

    # --- clinical (mechanism-inherited via gene primary_bucket -> pathway ->
    #     mapped_trial_mechanism -> trials). ---
    pathway = ctx["pathways_by_mech"].get(primary_bucket) if primary_bucket else None
    metrics["clinical.mechanism"] = _metric(
        primary_bucket, "gene_pathways_api.jsonl:primary_bucket (pathway_group used)")
    if pathway is not None:
        mapped_mech = (pathway.get("scores") or {}).get("mapped_trial_mechanism")
        mech_trials = ctx["trials_by_mech"].get(mapped_mech, []) if mapped_mech else []
    else:
        mapped_mech = None
        mech_trials = []
    tsum = summarise_trials(mech_trials)
    csrc = ("gene primary_bucket -> pathways.jsonl mechanism_group -> "
            "scores.mapped_trial_mechanism (%r); trials.jsonl filtered by "
            "mechanism_group==that value" % mapped_mech)
    n_trials = tsum["n_trials"]
    metrics["clinical.n_trials"] = _metric(n_trials, csrc)
    metrics["clinical.n_by_phase"] = _metric(
        tsum["n_by_phase"], csrc + "; count of trials by their max phase label")
    metrics["clinical.max_phase"] = _metric(
        tsum["max_phase"], csrc + "; highest phase LABEL over trials (string)")
    metrics["clinical.n_stopped"] = _metric(
        tsum["n_stopped"], csrc + "; status in TERMINATED/WITHDRAWN/SUSPENDED")
    metrics["clinical.stopped_ratio"] = _ratio(
        tsum["n_stopped"], n_trials, csrc, "n_stopped/n_trials")
    metrics["clinical.n_completed"] = _metric(
        tsum["n_completed"], csrc + "; status==COMPLETED")
    metrics["clinical.n_with_results"] = _metric(
        tsum["n_with_results"], csrc + "; trial.has_results")
    metrics["clinical.has_approval"] = _metric(
        tsum["has_approval"], csrc + "; any overall_status==APPROVED_FOR_MARKETING")
    metrics["clinical.n_drugs"] = _metric(
        tsum["n_drugs"],
        csrc + "; distinct DRUG/BIOLOGICAL intervention names (lowercased)")

    # --- literature (gene paper ids from shared topic_evidence_links) ---
    paper_ids = ctx["gene_paper_ids"].get(gene_id) or set()
    n_papers = len(paper_ids)
    metrics["literature.n_papers"] = _metric(
        n_papers,
        "shared/topic_evidence_links.jsonl: distinct supporting_paper_ids across "
        "evidence_type=='gene' rows for this gene_id")
    paper_years = ctx["paper_years"]
    if paper_years is None:
        lit_years = []
        n_recent_papers = None
        lit_src_suffix = " (track_a papers.jsonl absent -> null)"
    else:
        lit_years = [paper_years[p] for p in paper_ids if p in paper_years]
        n_recent_papers = sum(1 for y in lit_years if _is_recent(y))
        lit_src_suffix = ""
    metrics["literature.n_recent_papers"] = _metric(
        n_recent_papers,
        "track_a_snapshot/papers.jsonl pmid->year for this gene's paper ids"
        + _RECENT_NOTE + lit_src_suffix)
    metrics["literature.first_pub_year"] = _metric(
        min(lit_years) if lit_years else None,
        "track_a_snapshot/papers.jsonl min year over this gene's paper ids"
        + lit_src_suffix)
    metrics["literature.latest_pub_year"] = _metric(
        max(lit_years) if lit_years else None,
        "track_a_snapshot/papers.jsonl max year over this gene's paper ids"
        + lit_src_suffix)

    # --- temporal (GWAS publication years) ---
    gwas_years = [y for y in (_year_of((a.get("publication") or {}).get("date"))
                              for a in assocs) if y is not None]
    n_recent_gwas = sum(1 for y in gwas_years if _is_recent(y))
    tsrc = "gwas_associations.jsonl:publication.date year for matching associations"
    metrics["temporal.first_gwas_year"] = _metric(
        min(gwas_years) if gwas_years else None, tsrc)
    metrics["temporal.latest_gwas_year"] = _metric(
        max(gwas_years) if gwas_years else None, tsrc)
    metrics["temporal.n_recent_gwas"] = _metric(n_recent_gwas, tsrc + _RECENT_NOTE)

    # --- cross-disease ---
    disease_groups = gene.get("disease_groups") or []
    metrics["cross_disease.disease_groups"] = _metric(
        list(disease_groups), "genes.jsonl:disease_groups")
    metrics["cross_disease.n_disease_groups"] = _metric(
        len(disease_groups), "genes.jsonl:disease_groups length")
    per_group_dirs = {}
    for a in assocs:
        d = _direction_of(a)
        if d is None:
            continue
        per_group_dirs.setdefault(a.get("disease_group"), []).append(d)
    dominants = set()
    for _grp, dirs in per_group_dirs.items():
        dom = _dominant_direction(dirs)
        if dom is not None:
            dominants.add(dom)
    metrics["cross_disease.direction_flip_across_disease"] = _metric(
        len(dominants) >= 2,
        "gwas_associations.jsonl: dominant effect direction per disease_group; "
        "True if >=2 groups have opposing dominant directions")

    # --- open_targets.* : Open Targets' OWN external harmonic-sum datatype
    #     scores, kept RAW (from target_evidence.jsonl, Alzheimer disease row
    #     preferred). null if the gene has no OT row. These are EXTERNAL reference
    #     scores, NOT our statistics. ---
    ot_row = ctx["ot_by_gene"].get(gene_id)
    ot_scores = (ot_row.get("scores") if ot_row else None) or {}
    for key in ("overall", "genetic_association", "clinical", "literature",
                "affected_pathway", "rna_expression", "animal_model",
                "genetic_literature"):
        metrics["open_targets." + key] = _metric(
            ot_scores.get(key) if ot_row else None, OT_SRC)

    # --- ratios (simple a/b primitives; null when denominator 0) ---
    n_studies = es.get("gwas_study_count") or 0
    metrics["ratios.studies_per_trial"] = _ratio(
        es.get("gwas_study_count"), n_trials,
        "genes.jsonl gwas_study_count / clinical.n_trials",
        "n_gwas_studies/n_trials")
    metrics["ratios.papers_per_study"] = _ratio(
        n_papers, n_studies,
        "literature.n_papers / genes.jsonl gwas_study_count",
        "n_papers/n_gwas_studies")
    metrics["ratios.trials_per_paper"] = _ratio(
        n_trials, n_papers,
        "clinical.n_trials / literature.n_papers", "n_trials/n_papers")
    metrics["ratios.recent_gwas_fraction"] = _ratio(
        n_recent_gwas, n_studies,
        "temporal.n_recent_gwas / genes.jsonl gwas_study_count",
        "n_recent_gwas/n_gwas_studies")

    return {
        "entity_type": "gene",
        "entity_id": gene_id,
        "label": symbol,
        "pathway_group": primary_bucket,
        "disease_groups": list(disease_groups),
        "note": RECORD_NOTE,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# VARIANT metrics
# ---------------------------------------------------------------------------

def build_variant_records(gwas, flinks_by_rsid):
    """One record per distinct rsid appearing in gwas_associations."""
    by_rsid = {}
    for a in gwas:
        rsid = (a.get("variant") or {}).get("rsid")
        if not rsid:
            continue
        by_rsid.setdefault(rsid, []).append(a)

    records = []
    for rsid in sorted(by_rsid):
        assocs = by_rsid[rsid]
        metrics = {}

        study_accessions = {a.get("study_accession") for a in assocs
                            if a.get("study_accession")}
        metrics["genetic.n_associations"] = _metric(
            len(assocs), "gwas_associations.jsonl: rows with this rsid")
        metrics["genetic.n_studies"] = _metric(
            len(study_accessions),
            "gwas_associations.jsonl: distinct study_accession for this rsid")

        neglogs = [nl for nl in (common.neglog10(a.get("p_value")) for a in assocs)
                   if nl is not None]
        metrics["genetic.best_neglog10p"] = _metric(
            max(neglogs) if neglogs else None,
            "gwas_associations.jsonl: max -log10(p_value) over this rsid (raw)")

        directions = [d for d in (_direction_of(a) for a in assocs)
                      if d is not None]
        n_risk = sum(1 for x in directions if x == "risk")
        n_prot = sum(1 for x in directions if x == "protective")
        n_dir = n_risk + n_prot
        metrics["genetic.direction_agreement_ratio"] = _ratio(
            max(n_risk, n_prot), n_dir,
            "gwas_associations.jsonl: risk/protective effect direction for this rsid",
            "max(n_risk,n_protective)/(n_risk+n_protective)")

        reported = set()
        for a in assocs:
            for sym in (a.get("reported_genes") or []):
                if sym:
                    reported.add(str(sym))
        metrics["links.reported_genes"] = _metric(
            sorted(reported), "gwas_associations.jsonl:reported_genes for this rsid")

        l2g_genes = sorted({fl.get("gene_symbol")
                            for fl in flinks_by_rsid.get(rsid, [])
                            if fl.get("gene_symbol")})
        metrics["links.l2g_genes"] = _metric(
            l2g_genes, "functional_links.jsonl:gene_symbol for this rsid")

        disease_groups = sorted({a.get("disease_group") for a in assocs
                                 if a.get("disease_group")})
        metrics["cross_disease.disease_groups"] = _metric(
            disease_groups, "gwas_associations.jsonl:disease_group for this rsid")

        years = [y for y in (_year_of((a.get("publication") or {}).get("date"))
                             for a in assocs) if y is not None]
        tsrc = "gwas_associations.jsonl:publication.date year for this rsid"
        metrics["temporal.first_year"] = _metric(
            min(years) if years else None, tsrc)
        metrics["temporal.latest_year"] = _metric(
            max(years) if years else None, tsrc)
        metrics["temporal.n_recent"] = _metric(
            sum(1 for y in years if _is_recent(y)), tsrc + _RECENT_NOTE)

        records.append({
            "entity_type": "variant",
            "entity_id": "variant:" + rsid,
            "label": rsid,
            "pathway_group": None,
            "disease_groups": disease_groups,
            "note": RECORD_NOTE,
            "metrics": metrics,
        })
    return records


# ---------------------------------------------------------------------------
# PATHWAY metrics
# ---------------------------------------------------------------------------

def build_pathway_record(pathway, ctx):
    scores = pathway.get("scores") or {}
    mechanism_group = pathway.get("mechanism_group")
    mapped_mech = scores.get("mapped_trial_mechanism")
    metrics = {}

    # --- support (member genes) ---
    member_symbols = pathway.get("gene_ids") or []
    genes_by_symbol = ctx["genes_by_symbol"]
    matched_genes = []
    neglogs = []
    for sym in member_symbols:
        g = genes_by_symbol.get(str(sym).upper())
        if g is None:
            continue
        matched_genes.append(g)
        nl = (g.get("evidence_scores") or {}).get("best_neglog10p")
        if isinstance(nl, (int, float)) and not isinstance(nl, bool):
            neglogs.append(nl)

    metrics["support.n_genes"] = _metric(
        scores.get("member_gene_count", len(member_symbols)),
        "pathways.jsonl:scores.member_gene_count (member gene_ids)")
    metrics["support.mean_best_neglog10p"] = _metric(
        (sum(neglogs) / len(neglogs)) if neglogs else None,
        "genes.jsonl:evidence_scores.best_neglog10p mean over matched member genes")

    # --- clinical (trials via mapped_trial_mechanism crosswalk) ---
    mech_trials = ctx["trials_by_mech"].get(mapped_mech, []) if mapped_mech else []
    tsum = summarise_trials(mech_trials)
    n_trials = tsum["n_trials"]
    csrc = ("trials.jsonl filtered by mechanism_group=="
            "pathway.scores.mapped_trial_mechanism (%r)" % mapped_mech)
    metrics["clinical.n_trials"] = _metric(n_trials, csrc)
    metrics["clinical.n_by_phase"] = _metric(
        tsum["n_by_phase"], csrc + "; count of trials by their max phase label")
    metrics["clinical.max_phase"] = _metric(
        tsum["max_phase"], csrc + "; highest phase LABEL over trials (string)")
    metrics["clinical.n_stopped"] = _metric(
        tsum["n_stopped"], csrc + "; status in TERMINATED/WITHDRAWN/SUSPENDED")
    metrics["clinical.stopped_ratio"] = _ratio(
        tsum["n_stopped"], n_trials, csrc, "n_stopped/n_trials")
    metrics["clinical.n_with_results"] = _metric(
        tsum["n_with_results"], csrc + "; trial.has_results")
    metrics["clinical.has_approval"] = _metric(
        tsum["has_approval"], csrc + "; any overall_status==APPROVED_FOR_MARKETING")
    metrics["clinical.n_drugs"] = _metric(
        tsum["n_drugs"],
        csrc + "; distinct DRUG/BIOLOGICAL intervention names (lowercased)")

    # --- literature (union of member-gene paper ids) ---
    lit_paper_ids = set()
    for g in matched_genes:
        gid = g.get("gene_id")
        if gid and gid in ctx["gene_paper_ids"]:
            lit_paper_ids.update(ctx["gene_paper_ids"][gid])
    metrics["literature.n_papers"] = _metric(
        len(lit_paper_ids),
        "shared/topic_evidence_links.jsonl: union of gene paper ids over matched "
        "member genes")

    # --- temporal (trial start years) ---
    tsrc = "trials.jsonl:start_date year for mapped-mechanism trials"
    metrics["temporal.first_trial_year"] = _metric(tsum["first_year"], tsrc)
    metrics["temporal.latest_trial_year"] = _metric(tsum["latest_year"], tsrc)
    metrics["temporal.n_recent_trials"] = _metric(
        tsum["n_recent"], tsrc + _RECENT_NOTE)

    # --- ratios (aggregate simple a/b; null when denominator 0) ---
    n_genes = scores.get("member_gene_count", len(member_symbols)) or 0
    n_studies_total = 0
    for g in matched_genes:
        sc = (g.get("evidence_scores") or {}).get("gwas_study_count") or 0
        n_studies_total += sc
    metrics["ratios.trials_per_gene"] = _ratio(
        n_trials, n_genes,
        "clinical.n_trials / support.n_genes", "n_trials/n_genes")
    metrics["ratios.studies_per_trial"] = _ratio(
        n_studies_total, n_trials,
        "sum of member-gene gwas_study_count / clinical.n_trials",
        "sum(n_gwas_studies over member genes)/n_trials")

    # disease groups: union across matched member genes.
    dgroups = set()
    for g in matched_genes:
        for dg in (g.get("disease_groups") or []):
            dgroups.add(dg)

    return {
        "entity_type": "pathway",
        "entity_id": pathway.get("pathway_id"),
        "label": pathway.get("label"),
        "pathway_group": mechanism_group,
        "disease_groups": sorted(dgroups),
        "note": RECORD_NOTE,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    (genes, gwas, flinks, trials, pathways, target_evidence,
     gene_pathways_api) = load_inputs()
    common.log("loaded %d genes, %d gwas, %d functional_links, %d trials, "
               "%d pathways, %d target_evidence, %d gene_pathways_api"
               % (len(genes), len(gwas), len(flinks), len(trials),
                  len(pathways), len(target_evidence), len(gene_pathways_api)))
    common.log("CURRENT_YEAR=%d (recent window = last %d years)"
               % (CURRENT_YEAR, RECENT_WINDOW))

    flinks_by_gene_id, flinks_by_gene_sym = index_flinks_by_gene(flinks)
    genes_by_symbol = {}
    for g in genes:
        sym = g.get("symbol")
        if sym:
            genes_by_symbol.setdefault(str(sym).upper(), g)

    paper_years = load_paper_years()
    if paper_years is None:
        common.log("track_a papers.jsonl absent -> literature recency metrics null")
    else:
        common.log("track_a papers.jsonl: %d pmid->year entries" % len(paper_years))
    gene_paper_ids = index_gene_paper_ids(
        common.SHARED_PROCESSED_DIR / "topic_evidence_links.jsonl")
    common.log("gene paper-id sets from topic_evidence_links: %d genes"
               % len(gene_paper_ids))

    ctx = {
        "gwas_by_symbol": index_gwas_by_symbol(gwas),
        "trials_by_mech": index_trials_by_mechanism(trials),
        "pathways_by_mech": index_pathways(pathways)[0],
        "ot_by_gene": index_target_evidence(target_evidence),
        "gpa_by_id": index_gene_pathways_api(gene_pathways_api),
        "flinks_by_gene_id": flinks_by_gene_id,
        "flinks_by_gene_sym": flinks_by_gene_sym,
        "genes_by_symbol": genes_by_symbol,
        "gene_paper_ids": gene_paper_ids,
        "paper_years": paper_years,
    }

    records = []
    for g in genes:
        records.append(build_gene_record(g, ctx))
    n_gene = len(records)

    flinks_by_rsid = index_flinks_by_rsid(flinks)
    variant_records = build_variant_records(gwas, flinks_by_rsid)
    records.extend(variant_records)
    n_variant = len(variant_records)

    n_pathway = 0
    for p in pathways:
        records.append(build_pathway_record(p, ctx))
        n_pathway += 1

    written = common.write_jsonl(OUT_PATH, records)
    common.log("wrote %d entity_metrics records (gene=%d, variant=%d, pathway=%d) "
               "-> %s" % (written, n_gene, n_variant, n_pathway, OUT_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
