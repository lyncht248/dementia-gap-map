#!/usr/bin/env python3
"""Compute a transparent, machine-readable per-entity METRICS layer (Track B).

This is the "score" step for the translational-evidence track. It reads the
already-processed, schema-valid Track B inputs and emits ONE flat metrics record
per entity (gene / variant / pathway). Every metric is:

  * numeric / boolean / null (never a free-text verdict), and
  * carries a short ``source`` provenance string so it is EXPLAINABLE.

Design philosophy (from the project owner): expose the raw, composable
signals; do NOT bake in opinionated labels like "contradicted" or
"opportunity". Downstream agents compose verdicts from these metrics. The record
shape uses dotted metric keys (``<group>.<name>``) so agents can parse groups,
and ``additionalProperties`` is allowed everywhere so agents can attach their own
metric keys later.

Inputs (data/processed/translational-evidence/):
    genes.jsonl, gwas_associations.jsonl, functional_links.jsonl,
    trials.jsonl, pathways.jsonl
    translational-evidence/map/gene_pathway.csv

Output:
    data/processed/translational-evidence/entity_metrics.jsonl

Reproducibility: "now" for recency metrics is CURRENT_YEAR, read from the
TE_CURRENT_YEAR environment variable (default 2026). We never call
datetime.today() so a given input always yields the same output.

Run:
    python3 translational-evidence/score/entity_metrics.py
"""

import csv
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

GENE_PATHWAY_CSV = common.TE_DIR / "map" / "gene_pathway.csv"
OUT_PATH = common.PROCESSED_DIR / "entity_metrics.jsonl"


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(\d{4})")


def _year_of(date_str):
    """Extract a 4-digit year from a date-ish string, else None.

    Handles 'YYYY-MM-DD', 'YYYY-MM', 'YYYY', and prose that leads with a year.
    """
    if not date_str:
        return None
    m = _YEAR_RE.search(str(date_str))
    if not m:
        return None
    return int(m.group(1))


def _metric(value, source):
    """Wrap a metric value with its provenance note."""
    return {"value": value, "source": source}


def _norm_phase_token(token):
    """Normalise a phase string to the canonical CT.gov underscore form.

    Accepts both 'PHASE3' and 'Phase 3' shapes; collapses whitespace/spacing to
    underscores and upper-cases. 'Phase 1/Phase 2' style also normalises.
    """
    if token is None:
        return ""
    s = str(token).strip().upper()
    s = s.replace("/", "_").replace("-", "_")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


# Canonical phase-token -> phase score. Higher = later clinical stage.
_PHASE_SCORE = {
    "PHASE4": 1.0,
    "APPROVED": 1.0,
    "PHASE3": 0.9,
    "PHASE2_PHASE3": 0.75,
    "PHASE2": 0.6,
    "PHASE1_PHASE2": 0.4,
    "PHASE1": 0.3,
    "EARLY_PHASE1": 0.2,
}
_PHASE_DEFAULT = 0.1  # NA / observational / unknown / empty


def _phase_score_for_token(token):
    """Score a single normalised phase token (default 0.1)."""
    return _PHASE_SCORE.get(_norm_phase_token(token), _PHASE_DEFAULT)


def max_phase_score(phases):
    """Max phase score over a trial's phases[] list.

    An empty/absent phases list scores the default (0.1). We also try the joined
    token (e.g. ['PHASE2','PHASE3'] -> 'PHASE2_PHASE3') so combined-phase trials
    map to their dedicated score rather than the max of the parts.
    """
    if not phases:
        return _PHASE_DEFAULT
    best = _PHASE_DEFAULT
    for p in phases:
        best = max(best, _phase_score_for_token(p))
    # Combined form: sort tokens for stable joins like PHASE1_PHASE2.
    norm_tokens = sorted(_norm_phase_token(p) for p in phases if p is not None)
    joined = "_".join(t for t in norm_tokens if t)
    if joined in _PHASE_SCORE:
        best = max(best, _PHASE_SCORE[joined])
    return best


# Overall-status buckets.
_STOPPED_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
_APPROVAL_STATUS = "APPROVED_FOR_MARKETING"
_DRUG_TYPES = {"DRUG", "BIOLOGICAL"}


# ---------------------------------------------------------------------------
# Effect-direction classification for a single GWAS association.
# ---------------------------------------------------------------------------

def _direction_of(assoc):
    """Classify one association as 'risk' / 'protective' / None.

    risk       = effect.direction 'increase' OR odds_ratio > 1 OR beta > 0
    protective = effect.direction 'decrease' OR odds_ratio < 1 OR beta < 0

    Priority: explicit direction, then odds_ratio, then beta. Returns None when
    no usable directional signal exists (so it does not count toward direction_n).
    """
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


def _agreement_stats(directions):
    """Given a list of 'risk'/'protective' labels, return dict of stats.

    Returns n (usable directional count), n_risk, n_protective, agreement
    (= max/n, None if n==0), and n_conflicting (= min).
    """
    n_risk = sum(1 for x in directions if x == "risk")
    n_prot = sum(1 for x in directions if x == "protective")
    n = n_risk + n_prot
    agreement = (max(n_risk, n_prot) / n) if n else None
    return {
        "n": n,
        "n_risk": n_risk,
        "n_protective": n_prot,
        "agreement": agreement,
        "n_conflicting": min(n_risk, n_prot),
    }


def _dominant_direction(directions):
    """The dominant label in a list, or None on empty / exact tie."""
    n_risk = sum(1 for x in directions if x == "risk")
    n_prot = sum(1 for x in directions if x == "protective")
    if n_risk == 0 and n_prot == 0:
        return None
    if n_risk == n_prot:
        return None  # a tie is not a dominant direction
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
    return genes, gwas, flinks, trials, pathways


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
    """rsid -> list of functional_link records."""
    by_rsid = {}
    for fl in flinks:
        rsid = fl.get("rsid")
        if rsid:
            by_rsid.setdefault(rsid, []).append(fl)
    return by_rsid


def index_trials_by_mechanism(trials):
    """trial mechanism_group -> list of trial records."""
    by_mech = {}
    for t in trials:
        mech = t.get("mechanism_group")
        by_mech.setdefault(mech, []).append(t)
    return by_mech


def index_pathways(pathways):
    """Return (by_mechanism_group, by_pathway_id)."""
    by_mech = {}
    by_id = {}
    for p in pathways:
        by_mech[p.get("mechanism_group")] = p
        by_id[p.get("pathway_id")] = p
    return by_mech, by_id


# ---------------------------------------------------------------------------
# Trial-cohort summariser (shared by gene "inherited" and pathway metrics).
# ---------------------------------------------------------------------------

def summarise_trials(trial_list):
    """Aggregate a list of trial records into clinical count/phase/status stats.

    Returns a dict of plain numbers/bools (no provenance) that callers wrap.
    """
    n = len(trial_list)
    n_stopped = 0
    n_with_results = 0
    has_approval = False
    best_phase = None
    drug_names = set()
    years = []

    for t in trial_list:
        status = t.get("overall_status")
        if status in _STOPPED_STATUSES:
            n_stopped += 1
        if status == _APPROVAL_STATUS:
            has_approval = True
        if t.get("has_results") or t.get("hasResults"):
            n_with_results += 1

        ps = max_phase_score(t.get("phases") or [])
        best_phase = ps if best_phase is None else max(best_phase, ps)

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
        "max_phase_score": best_phase,  # None only when n == 0
        "n_stopped": n_stopped,
        "stopped_ratio": (n_stopped / n) if n else None,
        "n_with_results": n_with_results,
        "has_approval": has_approval,
        "n_drugs": len(drug_names),
        "first_year": min(years) if years else None,
        "latest_year": max(years) if years else None,
        "n_recent": sum(1 for y in years if y >= CURRENT_YEAR - RECENT_WINDOW),
    }


# ---------------------------------------------------------------------------
# GENE metrics
# ---------------------------------------------------------------------------

def build_gene_record(gene, gwas_by_symbol, pathways_by_mech, trials_by_mech):
    es = gene.get("evidence_scores") or {}
    fc = es.get("functional_support_components") or {}
    symbol = gene.get("symbol")
    gene_id = gene.get("gene_id")
    pathway_group = es.get("pathway_group")

    metrics = {}

    # --- genetic (from the gene's own aggregated evidence_scores) ---
    metrics["genetic.gwas_study_count"] = _metric(
        es.get("gwas_study_count"), "genes.jsonl:evidence_scores.gwas_study_count")
    metrics["genetic.gwas_association_count"] = _metric(
        es.get("gwas_association_count"),
        "genes.jsonl:evidence_scores.gwas_association_count")
    metrics["genetic.best_neglog10p"] = _metric(
        es.get("best_neglog10p"), "genes.jsonl:evidence_scores.best_neglog10p")
    metrics["genetic.genetic_support"] = _metric(
        es.get("genetic_support"), "genes.jsonl:evidence_scores.genetic_support")
    metrics["genetic.ot_genetic_association"] = _metric(
        es.get("open_targets_genetic_association"),
        "genes.jsonl:evidence_scores.open_targets_genetic_association")

    # --- genetic direction (recomputed from matching GWAS associations) ---
    assocs = gwas_by_symbol.get((symbol or "").upper(), [])
    directions = [d for d in (_direction_of(a) for a in assocs) if d is not None]
    stats = _agreement_stats(directions)
    dir_src = ("gwas_associations.jsonl: reported_genes contains symbol; "
               "risk=direction increase|OR>1|beta>0, "
               "protective=direction decrease|OR<1|beta<0")
    metrics["genetic.direction_n"] = _metric(stats["n"], dir_src)
    metrics["genetic.direction_agreement"] = _metric(
        stats["agreement"], dir_src + "; agreement=max(risk,protective)/direction_n")
    metrics["genetic.n_conflicting"] = _metric(
        stats["n_conflicting"], dir_src + "; n_conflicting=min(risk,protective)")

    # --- functional ---
    metrics["functional.functional_support"] = _metric(
        es.get("functional_support"),
        "genes.jsonl:evidence_scores.functional_support")
    metrics["functional.max_l2g"] = _metric(
        fc.get("max_l2g"),
        "genes.jsonl:evidence_scores.functional_support_components.max_l2g")
    metrics["functional.n_l2g_loci"] = _metric(
        fc.get("n_l2g_loci"),
        "genes.jsonl:evidence_scores.functional_support_components.n_l2g_loci")
    metrics["functional.ot_rna_expression"] = _metric(
        es.get("open_targets_rna_expression"),
        "genes.jsonl:evidence_scores.open_targets_rna_expression")
    metrics["functional.ot_affected_pathway"] = _metric(
        es.get("open_targets_affected_pathway"),
        "genes.jsonl:evidence_scores.open_targets_affected_pathway")

    # --- clinical (mechanism-inherited via pathway_group) ---
    pathway = pathways_by_mech.get(pathway_group) if pathway_group else None
    metrics["clinical.mechanism"] = _metric(
        pathway_group, "genes.jsonl:evidence_scores.pathway_group")
    if pathway is not None:
        pscores = pathway.get("scores") or {}
        mapped_mech = pscores.get("mapped_trial_mechanism")
        mech_trials = trials_by_mech.get(mapped_mech, []) if mapped_mech else []
        tsum = summarise_trials(mech_trials)
        csrc = ("pathways.jsonl mechanism_group==gene.pathway_group; trials.jsonl "
                "filtered by mechanism_group==pathway.scores.mapped_trial_mechanism "
                "(%r)" % mapped_mech)
        metrics["clinical.n_trials"] = _metric(tsum["n_trials"], csrc)
        metrics["clinical.max_phase_score"] = _metric(
            tsum["max_phase_score"], csrc + "; max over trial phases[] via phase-map")
        metrics["clinical.n_stopped"] = _metric(
            tsum["n_stopped"], csrc + "; status in TERMINATED/WITHDRAWN/SUSPENDED")
        metrics["clinical.stopped_ratio"] = _metric(
            tsum["stopped_ratio"], csrc + "; n_stopped/n_trials")
        metrics["clinical.n_with_results"] = _metric(
            tsum["n_with_results"], csrc + "; trial.has_results")
        metrics["clinical.has_approval"] = _metric(
            tsum["has_approval"], csrc + "; any overall_status==APPROVED_FOR_MARKETING")
        metrics["clinical.clinical_translation"] = _metric(
            pscores.get("clinical_translation"),
            "pathways.jsonl:scores.clinical_translation")
        metrics["clinical.clinical_saturation"] = _metric(
            pscores.get("clinical_saturation"),
            "pathways.jsonl:scores.clinical_saturation")
    else:
        nomech = "no pathway_group / no matching pathway record for this gene"
        for key in ("n_trials", "n_stopped", "n_with_results"):
            metrics["clinical." + key] = _metric(0, nomech)
        for key in ("max_phase_score", "stopped_ratio", "clinical_translation",
                    "clinical_saturation"):
            metrics["clinical." + key] = _metric(None, nomech)
        metrics["clinical.has_approval"] = _metric(False, nomech)

    # --- temporal (from matching GWAS publication years) ---
    gwas_years = [y for y in (_year_of((a.get("publication") or {}).get("date"))
                              for a in assocs) if y is not None]
    tsrc = "gwas_associations.jsonl:publication.date year for matching associations"
    metrics["temporal.first_gwas_year"] = _metric(
        min(gwas_years) if gwas_years else None, tsrc)
    metrics["temporal.latest_gwas_year"] = _metric(
        max(gwas_years) if gwas_years else None, tsrc)
    metrics["temporal.n_recent_gwas"] = _metric(
        sum(1 for y in gwas_years if y >= CURRENT_YEAR - RECENT_WINDOW),
        tsrc + "; year >= CURRENT_YEAR-%d (CURRENT_YEAR=%d)"
        % (RECENT_WINDOW, CURRENT_YEAR))

    # --- cross-disease ---
    disease_groups = gene.get("disease_groups") or []
    metrics["cross_disease.n_disease_groups"] = _metric(
        len(disease_groups), "genes.jsonl:disease_groups length")
    # direction flip: dominant effect direction differs between >=2 disease groups.
    per_group_dirs = {}
    for a in assocs:
        d = _direction_of(a)
        if d is None:
            continue
        per_group_dirs.setdefault(a.get("disease_group"), []).append(d)
    dominants = set()
    for grp, dirs in per_group_dirs.items():
        dom = _dominant_direction(dirs)
        if dom is not None:
            dominants.add(dom)
    metrics["cross_disease.direction_flip_across_disease"] = _metric(
        len(dominants) >= 2,
        "gwas_associations.jsonl: dominant effect direction per disease_group; "
        "True if >=2 groups have opposing dominant directions")

    # --- composite (from the gene's pathway record) ---
    if pathway is not None:
        metrics["composite.translation_gap"] = _metric(
            (pathway.get("scores") or {}).get("translation_gap"),
            "pathways.jsonl:scores.translation_gap (via gene pathway_group)")
    else:
        metrics["composite.translation_gap"] = _metric(
            None, "no pathway_group / no matching pathway record for this gene")

    return {
        "entity_type": "gene",
        "entity_id": gene_id,
        "label": symbol,
        "pathway_group": pathway_group,
        "disease_groups": disease_groups,
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
            "gwas_associations.jsonl: max -log10(p_value) over this rsid")

        directions = [d for d in (_direction_of(a) for a in assocs)
                      if d is not None]
        stats = _agreement_stats(directions)
        metrics["genetic.direction_agreement"] = _metric(
            stats["agreement"],
            "gwas_associations.jsonl: max(risk,protective)/direction_n for this rsid")

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
            sum(1 for y in years if y >= CURRENT_YEAR - RECENT_WINDOW),
            tsrc + "; year >= CURRENT_YEAR-%d (CURRENT_YEAR=%d)"
            % (RECENT_WINDOW, CURRENT_YEAR))

        records.append({
            "entity_type": "variant",
            "entity_id": "variant:" + rsid,
            "label": rsid,
            "pathway_group": None,
            "disease_groups": disease_groups,
            "metrics": metrics,
        })
    return records


# ---------------------------------------------------------------------------
# PATHWAY metrics
# ---------------------------------------------------------------------------

def build_pathway_record(pathway, genes_by_symbol, trials_by_mech):
    scores = pathway.get("scores") or {}
    mechanism_group = pathway.get("mechanism_group")
    mapped_mech = scores.get("mapped_trial_mechanism")
    metrics = {}

    # --- support (member genes) ---
    member_symbols = pathway.get("gene_ids") or []
    gsups, fsups = [], []
    for sym in member_symbols:
        g = genes_by_symbol.get(str(sym).upper())
        if g is None:
            continue
        es = g.get("evidence_scores") or {}
        gs = es.get("genetic_support")
        fs = es.get("functional_support")
        if isinstance(gs, (int, float)) and not isinstance(gs, bool):
            gsups.append(gs)
        if isinstance(fs, (int, float)) and not isinstance(fs, bool):
            fsups.append(fs)

    metrics["support.member_gene_count"] = _metric(
        scores.get("member_gene_count", len(member_symbols)),
        "pathways.jsonl:scores.member_gene_count")
    metrics["support.mean_genetic_support"] = _metric(
        (sum(gsups) / len(gsups)) if gsups else None,
        "genes.jsonl:evidence_scores.genetic_support mean over matched member genes")
    metrics["support.mean_functional_support"] = _metric(
        (sum(fsups) / len(fsups)) if fsups else None,
        "genes.jsonl:evidence_scores.functional_support mean over matched member genes")
    metrics["support.combined_support"] = _metric(
        scores.get("combined_support"), "pathways.jsonl:scores.combined_support")

    # --- clinical (trials via mapped_trial_mechanism crosswalk) ---
    mech_trials = trials_by_mech.get(mapped_mech, []) if mapped_mech else []
    tsum = summarise_trials(mech_trials)
    csrc = ("trials.jsonl filtered by mechanism_group=="
            "pathway.scores.mapped_trial_mechanism (%r)" % mapped_mech)
    metrics["clinical.n_trials"] = _metric(tsum["n_trials"], csrc)
    metrics["clinical.max_phase_score"] = _metric(
        tsum["max_phase_score"], csrc + "; max over trial phases[] via phase-map")
    metrics["clinical.n_stopped"] = _metric(
        tsum["n_stopped"], csrc + "; status in TERMINATED/WITHDRAWN/SUSPENDED")
    metrics["clinical.stopped_ratio"] = _metric(
        tsum["stopped_ratio"], csrc + "; n_stopped/n_trials")
    metrics["clinical.n_with_results"] = _metric(
        tsum["n_with_results"], csrc + "; trial.has_results")
    metrics["clinical.has_approval"] = _metric(
        tsum["has_approval"], csrc + "; any overall_status==APPROVED_FOR_MARKETING")
    metrics["clinical.n_drugs"] = _metric(
        tsum["n_drugs"],
        csrc + "; distinct DRUG/BIOLOGICAL intervention names (lowercased)")
    metrics["clinical.clinical_translation"] = _metric(
        scores.get("clinical_translation"),
        "pathways.jsonl:scores.clinical_translation")
    metrics["clinical.clinical_saturation"] = _metric(
        scores.get("clinical_saturation"),
        "pathways.jsonl:scores.clinical_saturation")

    # --- temporal (trial start years) ---
    tsrc = "trials.jsonl:start_date year for mapped-mechanism trials"
    metrics["temporal.first_trial_year"] = _metric(tsum["first_year"], tsrc)
    metrics["temporal.latest_trial_year"] = _metric(tsum["latest_year"], tsrc)
    metrics["temporal.n_recent_trials"] = _metric(
        tsum["n_recent"],
        tsrc + "; year >= CURRENT_YEAR-%d (CURRENT_YEAR=%d)"
        % (RECENT_WINDOW, CURRENT_YEAR))

    # --- composite ---
    metrics["composite.translation_gap"] = _metric(
        scores.get("translation_gap"), "pathways.jsonl:scores.translation_gap")

    # disease groups: union across matched member genes.
    dgroups = set()
    for sym in member_symbols:
        g = genes_by_symbol.get(str(sym).upper())
        if g:
            for dg in (g.get("disease_groups") or []):
                dgroups.add(dg)

    return {
        "entity_type": "pathway",
        "entity_id": pathway.get("pathway_id"),
        "label": pathway.get("label"),
        "pathway_group": mechanism_group,
        "disease_groups": sorted(dgroups),
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    genes, gwas, flinks, trials, pathways = load_inputs()
    common.log("loaded %d genes, %d gwas, %d functional_links, %d trials, "
               "%d pathways" % (len(genes), len(gwas), len(flinks),
                                len(trials), len(pathways)))
    common.log("CURRENT_YEAR=%d (recent window = last %d years)"
               % (CURRENT_YEAR, RECENT_WINDOW))

    gwas_by_symbol = index_gwas_by_symbol(gwas)
    flinks_by_rsid = index_flinks_by_rsid(flinks)
    trials_by_mech = index_trials_by_mechanism(trials)
    pathways_by_mech, _ = index_pathways(pathways)
    genes_by_symbol = {}
    for g in genes:
        sym = g.get("symbol")
        if sym:
            genes_by_symbol.setdefault(str(sym).upper(), g)

    # gene_pathway.csv is read to confirm the curated symbol->group crosswalk is
    # available; the authoritative per-gene group is genes.jsonl pathway_group,
    # so this is a provenance/consistency touchpoint rather than a second source.
    if GENE_PATHWAY_CSV.exists():
        with GENE_PATHWAY_CSV.open("r", encoding="utf-8", newline="") as fh:
            n_map = sum(1 for _ in csv.DictReader(fh))
        common.log("gene_pathway.csv rows: %d" % n_map)

    records = []
    for g in genes:
        records.append(build_gene_record(
            g, gwas_by_symbol, pathways_by_mech, trials_by_mech))
    n_gene = len(records)

    variant_records = build_variant_records(gwas, flinks_by_rsid)
    records.extend(variant_records)
    n_variant = len(variant_records)

    n_pathway = 0
    for p in pathways:
        records.append(build_pathway_record(p, genes_by_symbol, trials_by_mech))
        n_pathway += 1

    written = common.write_jsonl(OUT_PATH, records)
    common.log("wrote %d entity_metrics records (gene=%d, variant=%d, pathway=%d) "
               "-> %s" % (written, n_gene, n_variant, n_pathway, OUT_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
