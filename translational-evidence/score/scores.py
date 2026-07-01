"""Score + enrich step for Track B (translational evidence).

Computes fully explainable evidence scores and enriches
``genes.jsonl`` and ``pathways.jsonl`` IN PLACE (rewrite), keeping every raw
component input alongside each derived score so nothing is a black box.

Standard-library only (Python 3.9). Reads no live APIs -- it consumes the
already-produced processed JSONL outputs and the curated map CSVs.

Run:

    python3 translational-evidence/score/scores.py

Outputs (rewritten in place):
    data/processed/translational-evidence/genes.jsonl
    data/processed/translational-evidence/pathways.jsonl

Also (re)writes translational-evidence/score/SCORING.md.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)

import csv  # noqa: E402
import json  # noqa: E402


# ---------------------------------------------------------------------------
# Constants: normalization + weights (kept here AND documented in SCORING.md)
# ---------------------------------------------------------------------------

# genetic_support weights + normalization
GS_W_PVAL = 0.5      # weight on normalized best -log10(p)
GS_W_STUDIES = 0.2   # weight on normalized GWAS study count
GS_W_OT = 0.3        # weight on Open Targets genetic_association
GS_NEGLOG10P_CAP = 30.0   # -log10(p) at/above this normalizes to 1.0
GS_STUDY_CAP = 5.0        # GWAS study count at/above this normalizes to 1.0

# functional_support (L2G-based) cell-type colocalisation bonus.
# functional_support = clamp01(base_l2g + coloc_bonus), where base_l2g is the
# max L2G score for the gene across loci and coloc_bonus rewards a GWAS->QTL
# colocalisation link in a brain-relevant biosample (microglia highest). The
# bonus is capped at FS_COLOC_BONUS_MAX; the final score is clamped to [0, 1].
FS_COLOC_BONUS_MAX = 0.15   # max cell-type relevance bonus
# Brain-relevant biosample substrings -> per-cell-type bonus (microglia highest).
# Matched case-insensitively against the functional_link cell_type / biosample
# name; the single highest-matching bonus is added (bonuses are not summed).
FS_CELL_TYPE_BONUS = (
    ("microglia", 0.15),
    ("astrocyte", 0.12),
    ("neuron", 0.10),
    ("oligodendro", 0.10),
    ("opc", 0.10),
    ("cortex", 0.08),
    ("brain", 0.08),
)

# combined_support weights (per member gene)
CS_W_GENETIC = 0.6
CS_W_FUNCTIONAL = 0.4

# clinical_translation weights + normalization
CT_W_PHASE = 0.6
CT_W_COUNT = 0.25
CT_W_RESULTS = 0.15
CT_TRIAL_COUNT_CAP = 20.0   # trial count that saturates the count term

# clinical_saturation normalization
SAT_TRIAL_COUNT_CAP = 50.0

# Anchor disease for the Open Targets headline scores. ``target_evidence.jsonl``
# now carries one row per (gene, disease); Alzheimer disease (MONDO_0004975) is
# the anchor of this track, so its association scores are what land in the
# headline ``open_targets_*`` fields on each gene. The other disease rows are
# summarized into ``open_targets_disease_groups`` for transparency.
OT_ANCHOR_DISEASE_ID = "MONDO_0004975"  # Alzheimer disease
OT_ANCHOR_DISEASE_GROUP = "alzheimer"

# Phase token -> phase_score. Trials list phases as separate tokens
# (e.g. ["PHASE1", "PHASE2"]); we take the MAX phase_score across a trial's
# tokens. The combined tokens (PHASE1_PHASE2 / PHASE2_PHASE3) are included in
# case the source ever emits them merged; when the tokens arrive split we
# naturally pick the higher single-phase score, which matches the intent.
PHASE_SCORE = {
    "PHASE4": 1.0,
    "PHASE3": 0.9,
    "PHASE2_PHASE3": 0.75,
    "PHASE2/PHASE3": 0.75,
    "PHASE2": 0.6,
    "PHASE1_PHASE2": 0.4,
    "PHASE1/PHASE2": 0.4,
    "PHASE1": 0.3,
    "EARLY_PHASE1": 0.2,
    # Anything else (NA / N/A / observational / missing) -> 0.1 (see below).
}
PHASE_SCORE_DEFAULT = 0.1

# Crosswalk: pathway mechanism_group -> trial mechanism_group.
# The pathway vocabulary and the trial vocabulary differ slightly; this makes
# the mapping explicit and transparent. None means "no direct trial group".
PATHWAY_TO_TRIAL_MECHANISM = {
    "amyloid": "amyloid",
    "tau": "tau",
    "microglia_immune": "inflammation_microglia",
    "lipid_metabolism": "lipid_metabolism",
    "vascular": "vascular",
    "synaptic_neuronal": "synaptic_neuroprotection",
    "endocytosis_endosomal": None,   # no direct trial mechanism group
    "epigenetic_transcription": None,  # no direct trial mechanism group
    "other": "other",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _mean(values):
    """Mean of a list of numbers; None if the list is empty."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _phase_score_for_trial(phases):
    """Max phase_score across a trial's phase tokens (default 0.1)."""
    if not phases:
        return PHASE_SCORE_DEFAULT
    best = PHASE_SCORE_DEFAULT
    for tok in phases:
        if tok is None:
            continue
        norm = str(tok).strip().upper().replace(" ", "")
        best = max(best, PHASE_SCORE.get(norm, PHASE_SCORE_DEFAULT))
    return best


def _approved_status(status):
    """Trials whose overall_status marks an approved/marketed drug -> 1.0.

    ClinicalTrials.gov has no APPROVED status, but the spec asks that an
    APPROVED signal map to 1.0. We treat overall_status APPROVED_FOR_MARKETING
    (and the literal 'APPROVED') as the max phase score.
    """
    if not status:
        return False
    s = str(status).strip().upper().replace(" ", "_")
    return s in ("APPROVED", "APPROVED_FOR_MARKETING")


def _cell_type_bonus(cell_type):
    """Brain-relevant biosample -> bonus (single highest match), else 0.0.

    Case-insensitive substring match against FS_CELL_TYPE_BONUS; microglia is
    the strongest. Returns (bonus, matched_substring) with matched=None when the
    biosample is not brain-relevant / is missing.
    """
    if not cell_type:
        return 0.0, None
    text = str(cell_type).lower()
    best = 0.0
    best_kw = None
    for kw, bonus in FS_CELL_TYPE_BONUS:
        if kw in text and bonus > best:
            best = bonus
            best_kw = kw
    return best, best_kw


# ---------------------------------------------------------------------------
# functional_links index (OT L2G + GWAS->QTL colocalisation)
# ---------------------------------------------------------------------------

def build_functional_index(functional_links):
    """Aggregate functional_links per gene by gene_id and by gene_symbol.

    For each gene key we collect, from its L2G predictions, the max L2G score
    and the locus (studyLocusId) it came from and the number of contributing
    loci; and from its GWAS->QTL colocalisation links (if any) the set of
    brain-relevant cell types and the best cell-type bonus.

    Returns (by_gene_id, by_symbol) where each maps a key -> a dict:
        {
          "max_l2g": float|None,
          "n_l2g_loci": int,
          "best_l2g_locus": str|None,
          "coloc_cell_types": [sorted brain-relevant biosample names],
          "coloc_bonus": float,          # best brain-cell-type bonus (<=0.15)
          "has_brain_qtl_coloc": bool,
        }
    Join priority downstream mirrors OT: gene_id (Ensembl) first, then symbol.
    """
    by_gene_id = {}
    by_symbol = {}

    def _blank():
        return {
            "max_l2g": None,
            "n_l2g_loci": 0,
            "best_l2g_locus": None,
            "_l2g_loci": set(),         # internal: distinct loci with L2G
            "coloc_cell_types": set(),  # internal accumulation -> sorted later
            "coloc_bonus": 0.0,
            "has_brain_qtl_coloc": False,
        }

    def _get(store, key):
        if key is None:
            return None
        agg = store.get(key)
        if agg is None:
            agg = _blank()
            store[key] = agg
        return agg

    def _apply(agg, rec):
        if agg is None:
            return
        etype = rec.get("evidence_type")
        score = rec.get("score")
        locus = rec.get("variant_or_locus")
        if etype == "l2g_prediction":
            if score is not None:
                if agg["max_l2g"] is None or score > agg["max_l2g"]:
                    agg["max_l2g"] = float(score)
                    agg["best_l2g_locus"] = locus
                if locus is not None:
                    agg["_l2g_loci"].add(locus)
        elif etype == "gwas_qtl_colocalisation":
            bonus, kw = _cell_type_bonus(rec.get("cell_type"))
            if kw is not None:
                agg["has_brain_qtl_coloc"] = True
                ct = rec.get("cell_type")
                if ct:
                    agg["coloc_cell_types"].add(ct)
                if bonus > agg["coloc_bonus"]:
                    agg["coloc_bonus"] = bonus

    for rec in functional_links:
        gid = rec.get("gene_id")
        sym = rec.get("gene_symbol")
        _apply(_get(by_gene_id, gid), rec)
        _apply(_get(by_symbol, sym), rec)

    def _finalize(store):
        for agg in store.values():
            agg["n_l2g_loci"] = len(agg.pop("_l2g_loci"))
            agg["coloc_cell_types"] = sorted(agg["coloc_cell_types"])

    _finalize(by_gene_id)
    _finalize(by_symbol)
    return by_gene_id, by_symbol


# ---------------------------------------------------------------------------
# Map CSV loaders
# ---------------------------------------------------------------------------

def load_gene_pathway_map():
    """symbol -> pathway_group from map/gene_pathway.csv."""
    path = common.TE_DIR / "map" / "gene_pathway.csv"
    out = {}
    members = {}  # pathway_group -> [symbols]
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sym = (row.get("gene_symbol") or "").strip()
            grp = (row.get("pathway_group") or "").strip() or None
            if not sym:
                continue
            out[sym] = grp
            if grp:
                members.setdefault(grp, []).append(sym)
    return out, members


# ---------------------------------------------------------------------------
# A) Genes enrichment
# ---------------------------------------------------------------------------

GENES_FORMULAS = (
    "genetic_support = 0.5*neglog10p_norm + 0.2*study_count_norm + 0.3*ot_genetic "
    "(neglog10p_norm = min(1, best_neglog10p/30); study_count_norm = "
    "min(1, gwas_study_count/5); ot_genetic = open_targets_genetic_association or 0). "
    "functional_support = clamp01(base_l2g + coloc_bonus) where base_l2g = max OT "
    "L2G score for the gene across loci (functional_links) and coloc_bonus (<=0.15) "
    "rewards a GWAS->QTL colocalisation in a brain-relevant biosample (microglia "
    "highest); null if the gene has no functional_links. ot_rna_expression / "
    "ot_affected_pathway retained as secondary recorded components."
)

FUNCTIONAL_METHOD = (
    "OT L2G (integrates colocalisation/QTL across studies)"
)
FUNCTIONAL_NO_LINK_NOTE = "no OT L2G/QTL link"


def build_ot_index(target_evidence):
    """Index Open Targets target_evidence by gene_id and by target_label.

    ``target_evidence.jsonl`` now has MULTIPLE rows per gene (one per disease).
    For the headline ``open_targets_*`` scores we prefer the ALZHEIMER anchor
    disease row (``disease_id == OT_ANCHOR_DISEASE_ID``); if a gene has no
    Alzheimer row we fall back to the first-seen row so the gene still gets OT
    scores. We also collect, per gene, the sorted-unique set of OT
    ``disease_group`` values so downstream can record which dementias a gene is
    associated with in Open Targets.

    Returns (by_gene_id, by_symbol, dgroups_by_gene_id, dgroups_by_symbol):
      - by_gene_id / by_symbol map a key -> the chosen (anchor-preferred) OT
        ``scores`` dict.
      - dgroups_by_* map a key -> sorted list of distinct OT disease_groups for
        that gene.
    Join priority downstream: gene_id (Ensembl) first, then symbol==target_label.
    """
    by_gene_id = {}
    by_symbol = {}
    # Track whether the currently-stored scores for a key came from the anchor
    # disease, so a later anchor row can upgrade a non-anchor first pick but two
    # non-anchor rows never clobber each other (first-seen wins among non-anchor).
    anchor_gene_id = set()
    anchor_symbol = set()
    dgroups_by_gene_id = {}
    dgroups_by_symbol = {}

    def _record_group(store, key, rec):
        if key is None:
            return
        dg = rec.get("disease_group")
        if dg is None:
            return
        store.setdefault(key, set()).add(dg)

    def _consider(store, anchor_set, key, scores, is_anchor):
        """Store scores for key preferring anchor rows over first-seen rows."""
        if key is None:
            return
        if key not in store:
            store[key] = scores
            if is_anchor:
                anchor_set.add(key)
            return
        # Already have something; only upgrade a non-anchor pick to an anchor.
        if is_anchor and key not in anchor_set:
            store[key] = scores
            anchor_set.add(key)

    for rec in target_evidence:
        scores = rec.get("scores") or {}
        gid = rec.get("gene_id")
        tid = rec.get("target_id")
        label = rec.get("target_label")
        is_anchor = rec.get("disease_id") == OT_ANCHOR_DISEASE_ID

        _consider(by_gene_id, anchor_gene_id, gid, scores, is_anchor)
        # target_id is the Ensembl id too; index it as a fallback gene_id key.
        _consider(by_gene_id, anchor_gene_id, tid, scores, is_anchor)
        _consider(by_symbol, anchor_symbol, label, scores, is_anchor)

        # Collect disease_groups across ALL disease rows for this gene.
        _record_group(dgroups_by_gene_id, gid, rec)
        _record_group(dgroups_by_gene_id, tid, rec)
        _record_group(dgroups_by_symbol, label, rec)

    dgroups_by_gene_id = {k: sorted(v) for k, v in dgroups_by_gene_id.items()}
    dgroups_by_symbol = {k: sorted(v) for k, v in dgroups_by_symbol.items()}
    return by_gene_id, by_symbol, dgroups_by_gene_id, dgroups_by_symbol


def enrich_genes(genes, ot_by_gene_id, ot_by_symbol, gene_to_pathway,
                 ot_dgroups_by_gene_id=None, ot_dgroups_by_symbol=None,
                 fl_by_gene_id=None, fl_by_symbol=None):
    """Enrich each gene record in place; return (records, n_ot, n_functional).

    Records are mutated in place (never rebuilt), so any pre-existing fields --
    notably the ``disease_groups`` list added by normalize/gwas_catalog.py -- are
    preserved untouched. We only defensively normalize/re-attach
    ``disease_groups`` so the key is always present as a list.

    ``fl_by_gene_id`` / ``fl_by_symbol`` are the functional_links aggregates
    (see build_functional_index): functional_support is computed from them
    (Ensembl gene_id first, then symbol). ``n_functional`` counts genes that got
    a non-null functional_support.
    """
    ot_dgroups_by_gene_id = ot_dgroups_by_gene_id or {}
    ot_dgroups_by_symbol = ot_dgroups_by_symbol or {}
    fl_by_gene_id = fl_by_gene_id or {}
    fl_by_symbol = fl_by_symbol or {}
    n_ot = 0
    n_functional = 0
    for rec in genes:
        # Preserve the disease_groups tag added upstream by the GWAS normalize
        # step. We read it from the existing record and write it back as a list
        # (default []), so it is never dropped when the record is rewritten.
        existing_dgroups = rec.get("disease_groups")
        if isinstance(existing_dgroups, list):
            rec["disease_groups"] = existing_dgroups
        elif existing_dgroups is None:
            rec["disease_groups"] = []
        else:
            rec["disease_groups"] = [existing_dgroups]

        es = rec.get("evidence_scores")
        if not isinstance(es, dict):
            es = {}
            rec["evidence_scores"] = es

        gene_id = rec.get("gene_id")
        symbol = rec.get("symbol")

        # --- Join Open Targets: gene_id (Ensembl) first, then symbol. ---
        # The chosen scores dict is the ALZHEIMER anchor-disease row when the
        # gene has one (see build_ot_index), falling back to the first-seen row.
        ot = None
        ot_match_key = None
        ot_dgroups = []
        if gene_id and gene_id in ot_by_gene_id:
            ot = ot_by_gene_id[gene_id]
            ot_match_key = "gene_id"
            ot_dgroups = ot_dgroups_by_gene_id.get(gene_id, [])
        elif symbol and symbol in ot_by_symbol:
            ot = ot_by_symbol[symbol]
            ot_match_key = "symbol"
            ot_dgroups = ot_dgroups_by_symbol.get(symbol, [])

        if ot is not None:
            n_ot += 1

        def _ot(field):
            if ot is None:
                return None
            return ot.get(field)

        ot_overall = _ot("overall")
        ot_genetic = _ot("genetic_association")
        ot_rna = _ot("rna_expression")
        ot_affected = _ot("affected_pathway")
        ot_clinical = _ot("clinical")
        ot_literature = _ot("literature")

        es["open_targets_overall"] = ot_overall
        es["open_targets_genetic_association"] = ot_genetic
        es["open_targets_rna_expression"] = ot_rna
        es["open_targets_affected_pathway"] = ot_affected
        es["open_targets_clinical"] = ot_clinical
        es["open_targets_literature"] = ot_literature
        es["open_targets_match"] = ot_match_key  # null if no OT match
        # Which anchor row supplied the headline scores, and every OT disease
        # group this gene is associated with (across all disease rows).
        es["open_targets_headline_disease"] = (
            OT_ANCHOR_DISEASE_GROUP if ot_match_key is not None
            and OT_ANCHOR_DISEASE_GROUP in ot_dgroups else None
        )
        es["open_targets_disease_groups"] = ot_dgroups

        # --- pathway_group from curated map (null if absent). ---
        es["pathway_group"] = gene_to_pathway.get(symbol) if symbol else None

        # --- genetic_support ---
        neglog10p = es.get("best_neglog10p")
        study_count = es.get("gwas_study_count")

        g_p = min(1.0, (neglog10p / GS_NEGLOG10P_CAP)) if neglog10p is not None else 0.0
        g_studies = (min(1.0, (study_count / GS_STUDY_CAP))
                     if study_count is not None else 0.0)
        g_ot = ot_genetic if ot_genetic is not None else 0.0

        genetic_support = common.clamp01(
            GS_W_PVAL * g_p + GS_W_STUDIES * g_studies + GS_W_OT * g_ot
        )
        es["genetic_support"] = round(genetic_support, 4)
        es["genetic_support_components"] = {
            "neglog10p_norm": round(g_p, 6),
            "study_count_norm": round(g_studies, 6),
            "ot_genetic": g_ot,
            "raw_best_neglog10p": neglog10p,
            "raw_gwas_study_count": study_count,
            "weights": {
                "neglog10p_norm": GS_W_PVAL,
                "study_count_norm": GS_W_STUDIES,
                "ot_genetic": GS_W_OT,
            },
            "normalization": {
                "neglog10p_cap": GS_NEGLOG10P_CAP,
                "study_count_cap": GS_STUDY_CAP,
            },
        }

        # --- functional_support (OT L2G, aggregated; coloc bonus) ---
        # Join functional_links: gene_id (Ensembl) first, then symbol.
        fl = None
        fl_match_key = None
        if gene_id and gene_id in fl_by_gene_id:
            fl = fl_by_gene_id[gene_id]
            fl_match_key = "gene_id"
        elif symbol and symbol in fl_by_symbol:
            fl = fl_by_symbol[symbol]
            fl_match_key = "symbol"

        if fl is None:
            # No functional_links for this gene at all -> null (not 0).
            es["functional_support"] = None
            es["functional_support_components"] = {
                "method": FUNCTIONAL_METHOD,
                "max_l2g": None,
                "n_l2g_loci": 0,
                "best_l2g_locus": None,
                "has_brain_qtl_coloc": False,
                "coloc_cell_types": [],
                "coloc_bonus": 0.0,
                "ot_rna_expression": ot_rna,
                "ot_affected_pathway": ot_affected,
                "functional_links_match": None,
                "note": FUNCTIONAL_NO_LINK_NOTE,
            }
        else:
            base_l2g = fl.get("max_l2g") or 0.0
            coloc_bonus = min(fl.get("coloc_bonus", 0.0), FS_COLOC_BONUS_MAX)
            functional_support = common.clamp01(base_l2g + coloc_bonus)
            es["functional_support"] = round(functional_support, 4)
            n_functional += 1
            es["functional_support_components"] = {
                "method": FUNCTIONAL_METHOD,
                "max_l2g": (round(base_l2g, 6)
                            if fl.get("max_l2g") is not None else None),
                "n_l2g_loci": fl.get("n_l2g_loci", 0),
                "best_l2g_locus": fl.get("best_l2g_locus"),
                "has_brain_qtl_coloc": fl.get("has_brain_qtl_coloc", False),
                "coloc_cell_types": fl.get("coloc_cell_types", []),
                "coloc_bonus": round(coloc_bonus, 6),
                "ot_rna_expression": ot_rna,
                "ot_affected_pathway": ot_affected,
                "functional_links_match": fl_match_key,
            }

        # --- self-documentation ---
        es["_formulas"] = GENES_FORMULAS

    return genes, n_ot, n_functional


# ---------------------------------------------------------------------------
# B) Pathway clinical-translation + gap scoring
# ---------------------------------------------------------------------------

PATHWAY_FORMULAS = (
    "clinical_translation = 0.6*max_phase_score + 0.25*min(1, trial_count/20) "
    "+ 0.15*has_results_fraction (0.0 if no mapped trials). "
    "clinical_saturation = min(1, trial_count/50). "
    "combined_support = mean over matched member genes of "
    "0.6*genetic_support + 0.4*(functional_support or genetic_support). "
    "translation_gap = combined_support * (1 - clinical_translation) "
    "(higher = strong genetics/function but little clinical activity)."
)

CROSSWALK_NOTE = (
    "pathway mechanism_group -> trial mechanism_group crosswalk: "
    + "; ".join(
        "%s->%s" % (k, ("none" if v is None else v))
        for k, v in PATHWAY_TO_TRIAL_MECHANISM.items()
    )
)


def index_trials_by_mechanism(trials):
    """Group trials by their trial mechanism_group.

    Returns mech -> list of {phase_score, has_results}.
    """
    out = {}
    for t in trials:
        mech = t.get("mechanism_group")
        if mech is None:
            continue
        phases = t.get("phases") or []
        if _approved_status(t.get("overall_status")):
            ps = 1.0
        else:
            ps = _phase_score_for_trial(phases)
        has_results = bool(t.get("has_results"))
        out.setdefault(mech, []).append(
            {"phase_score": ps, "has_results": has_results}
        )
    return out


def _gene_support_value(gene_es):
    """0.6*genetic_support + 0.4*(functional_support or genetic_support)."""
    gs = gene_es.get("genetic_support")
    if gs is None:
        return None
    fs = gene_es.get("functional_support")
    functional = fs if fs is not None else gs
    return CS_W_GENETIC * gs + CS_W_FUNCTIONAL * functional


def enrich_pathways(pathways, trials_by_mech, genes_by_symbol, pathway_members):
    """Attach a 'scores' object to each pathway record; return records."""
    for rec in pathways:
        mech_group = rec.get("mechanism_group")
        trial_mech = PATHWAY_TO_TRIAL_MECHANISM.get(mech_group)

        # --- clinical translation from mapped trials ---
        mapped = trials_by_mech.get(trial_mech, []) if trial_mech else []
        trial_count = len(mapped)

        if trial_count > 0:
            max_phase_score = max(m["phase_score"] for m in mapped)
            n_with_results = sum(1 for m in mapped if m["has_results"])
            has_results_fraction = n_with_results / trial_count
            count_norm = min(1.0, trial_count / CT_TRIAL_COUNT_CAP)
            clinical_translation = round(
                CT_W_PHASE * max_phase_score
                + CT_W_COUNT * count_norm
                + CT_W_RESULTS * has_results_fraction,
                4,
            )
            ct_note = None
        else:
            max_phase_score = 0.0
            has_results_fraction = 0.0
            clinical_translation = 0.0
            ct_note = "no mapped trials"

        clinical_saturation = round(
            min(1.0, trial_count / SAT_TRIAL_COUNT_CAP), 4
        )

        # --- combined_support from member genes ---
        member_symbols = pathway_members.get(mech_group, [])
        # Prefer the pathway record's own gene list if present (should match).
        rec_gene_ids = rec.get("gene_ids") or []
        if rec_gene_ids:
            member_symbols = rec_gene_ids
        member_gene_count = len(member_symbols)

        support_vals = []
        matched = 0
        for sym in member_symbols:
            g = genes_by_symbol.get(sym)
            if g is None:
                continue
            val = _gene_support_value(g.get("evidence_scores") or {})
            if val is None:
                continue
            matched += 1
            support_vals.append(val)

        combined = _mean(support_vals)
        combined_support = round(combined, 4) if combined is not None else 0.0

        translation_gap = round(combined_support * (1.0 - clinical_translation), 4)

        rec["scores"] = {
            "clinical_translation": clinical_translation,
            "clinical_saturation": clinical_saturation,
            "combined_support": combined_support,
            "translation_gap": translation_gap,
            "trial_count": trial_count,
            "mapped_trial_mechanism": trial_mech,  # null if no direct group
            "max_phase_score": round(max_phase_score, 4),
            "has_results_fraction": round(has_results_fraction, 4),
            "member_gene_count": member_gene_count,
            "member_genes_matched": matched,
            "clinical_translation_note": ct_note,
            "crosswalk_note": CROSSWALK_NOTE,
            "_formulas": PATHWAY_FORMULAS,
        }
    return pathways


# ---------------------------------------------------------------------------
# SCORING.md
# ---------------------------------------------------------------------------

def write_scoring_md():
    path = common.TE_DIR / "score" / "SCORING.md"
    crosswalk_rows = "\n".join(
        "| `%s` | %s |" % (k, ("(none)" if v is None else "`%s`" % v))
        for k, v in PATHWAY_TO_TRIAL_MECHANISM.items()
    )
    phase_rows = "\n".join(
        "| `%s` | %s |" % (k, v)
        for k, v in sorted(PHASE_SCORE.items(), key=lambda kv: -kv[1])
    )
    content = """# Scoring methodology (Track B: translational evidence)

This document specifies every formula, weight, normalization constant, the
mechanism crosswalk, and the data provenance behind the scores written by
`translational-evidence/score/scores.py`.

Design principle: **every score is fully explainable**. Each derived number is
stored in the JSONL record alongside its raw component inputs and the exact
weights/normalizations used, so nothing is a black box.

All scores are clamped/rounded to `[0, 1]` (4 decimal places) unless noted.

---

## 1. Gene scores (written into `genes.jsonl` -> `evidence_scores`)

### Open Targets join
Open Targets `target_evidence.jsonl` is joined to each gene by **Ensembl
`gene_id` first, then by `symbol == target_label`**. The matched key is stored
as `evidence_scores.open_targets_match` (`"gene_id"`, `"symbol"`, or `null`).

`target_evidence.jsonl` now carries **one row per (gene, disease)** across the
ADRD disease set. For the **headline** `open_targets_*` scores we prefer the
**Alzheimer disease anchor row** (`disease_id == %(anchor_id)s`), because AD is
the anchor disease of this track; if a gene has no Alzheimer OT row we fall back
to its first-seen disease row so the gene still receives OT scores.
`evidence_scores.open_targets_headline_disease` is `"alzheimer"` when the
headline scores came from the Alzheimer anchor row, else `null` (fallback row).
`evidence_scores.open_targets_disease_groups` lists the sorted-unique
`disease_group` values the gene is associated with across **all** its OT disease
rows.

The following raw Open Targets association scores are attached (each `null` if
there is no OT match):

- `open_targets_overall`
- `open_targets_genetic_association`
- `open_targets_rna_expression`
- `open_targets_affected_pathway`
- `open_targets_clinical`
- `open_targets_literature`

`pathway_group` is attached from the curated `map/gene_pathway.csv`
(`null` if the gene is not in the map).

### genetic_support (0..1)
```
genetic_support = %(gsw_p)s*neglog10p_norm + %(gsw_s)s*study_count_norm + %(gsw_ot)s*ot_genetic
  neglog10p_norm   = min(1, best_neglog10p / %(p_cap)s)
  study_count_norm = min(1, gwas_study_count / %(s_cap)s)
  ot_genetic       = open_targets_genetic_association (or 0 if no OT match)
```
Provenance / rationale:
- `best_neglog10p` and `gwas_study_count` come from the GWAS Catalog ingest
  (already in `evidence_scores`). Genome-wide significance is ~7.3
  (`p = 5e-8`); APOE reaches far higher (`-log10(p)` in the hundreds), so the
  cap of **%(p_cap)s** keeps a single dominant locus from swamping the scale
  while still saturating strong loci.
- `gwas_study_count` cap of **%(s_cap)s** rewards replication across studies.
- `ot_genetic` is Open Targets' own genetic-association datatype score.

Components stored under `evidence_scores.genetic_support_components`:
`neglog10p_norm`, `study_count_norm`, `ot_genetic`, the raw inputs, the
`weights`, and the `normalization` caps.

### functional_support (0..1, or null) -- OT L2G (aggregated, colocalisation-integrating)
```
functional_support = clamp01(base_l2g + coloc_bonus)
  base_l2g    = max OT Locus-to-Gene (L2G) score for the gene across all loci
                in functional_links.jsonl (0 if the gene has L2G rows but no
                positive score)
  coloc_bonus = up to +%(fs_bonus_max)s cell-type-relevance bonus when the gene has any
                GWAS->QTL colocalisation link in a brain-relevant biosample
                (microglia highest); 0 otherwise
  (null when the gene has NO functional_links at all)
```
This is the **real functional / eQTL layer**, sourced entirely from the Open
Targets fine-mapping pipeline. It is built by
`ingest/open_targets_l2g.py` -> `normalize/open_targets_l2g.py` ->
`functional_links.jsonl`, then aggregated per gene here.

`functional_links.jsonl` is joined to each gene by **Ensembl `gene_id` first,
then by `gene_symbol`** (the matched key is stored as
`functional_support_components.functional_links_match`). L2G is a supervised
model that **integrates colocalisation and QTL evidence across many studies**
into a single locus-to-gene score, so it is the **primary** functional signal
here. Genes with **no** functional_links get `functional_support = null` (not 0)
with the note stored verbatim:
> %(func_no_link_note)s

**Cell-type relevance bonus.** If a gene has any `gwas_qtl_colocalisation` link
in a brain-relevant biosample (biosample name contains one of *microglia,
astrocyte, neuron, oligodendro, brain, cortex, OPC*), the single highest-matching
bonus below is added (bonuses are **not** summed), then the total is clamped to
`[0, 1]`:

| biosample contains | bonus |
| --- | --- |
%(fs_bonus_rows)s

**Important empirical finding:** raw Open Targets **GWAS->QTL colocalisation is
sparse / near-empty for Alzheimer disease** (the current build produced **0**
colocalisation links across ~1,865 credible sets), so in practice
`functional_support` is driven by `base_l2g` and the `coloc_bonus` is currently
`0.0` for every gene. The bonus machinery is in place for when brain-QTL
colocalisation is available (and see the eQTL Catalogue note below).

Components stored under `evidence_scores.functional_support_components`:
`method`, `max_l2g`, `n_l2g_loci`, `best_l2g_locus`, `has_brain_qtl_coloc`,
`coloc_cell_types`, `coloc_bonus`, `ot_rna_expression`, `ot_affected_pathway`
(the last two are retained as **secondary recorded components** from the old
proxy, no longer used in the score), and `functional_links_match`.

`evidence_scores._formulas` restates these formulas inside every gene record.

---

## 2. Pathway scores (written into `pathways.jsonl` -> `scores`)

### Mechanism crosswalk (pathway -> trial vocabulary)
The pathway `mechanism_group` vocabulary and the trial `mechanism_group`
vocabulary differ slightly. The crosswalk is applied transparently and also
stored in each record's `scores.crosswalk_note`:

| pathway `mechanism_group` | trial `mechanism_group` |
| --- | --- |
%(crosswalk_rows)s

`(none)` means there is no direct trial mechanism group; those pathways get
`trial_count = 0` and `clinical_translation = 0.0` with note
`"no mapped trials"`.

### Phase scoring (per trial)
Each trial's `phases` tokens are mapped to a `phase_score`; the trial's score is
the **max** across its tokens. An `overall_status` of `APPROVED` /
`APPROVED_FOR_MARKETING` forces `1.0`. Missing / `NA` / observational -> %(phase_default)s.

| phase token | phase_score |
| --- | --- |
%(phase_rows)s
| (anything else / NA / observational) | %(phase_default)s |

### clinical_translation (0..1)
```
clinical_translation = %(ctw_p)s*max_phase_score
                     + %(ctw_c)s*min(1, trial_count/%(ct_cap)s)
                     + %(ctw_r)s*has_results_fraction
```
Where the terms are computed over the trials whose (crosswalked) mechanism
matches the pathway. `has_results_fraction` = fraction of mapped trials with
`has_results == true`. If there are no mapped trials the score is `0.0` with
note `"no mapped trials"`.

### clinical_saturation (0..1)
```
clinical_saturation = min(1, trial_count / %(sat_cap)s)
```
Raw `trial_count` is kept in the record.

### combined_support (0..1)
Mean over the pathway's **matched** member genes (member symbols joined into the
enriched `genes.jsonl` by symbol) of:
```
0.6*genetic_support + 0.4*(functional_support or genetic_support)
```
`functional_support` falls back to `genetic_support` when it is null (a gene
may have GWAS genetics but no OT L2G functional_link). `member_gene_count`
and `member_genes_matched` are stored so coverage is visible.

### translation_gap (0..1)
```
translation_gap = combined_support * (1 - clinical_translation)
```
**Higher = strong genetics/function but little clinical activity = a
translational opportunity / gap.**

Each pathway record's `scores` object stores: `clinical_translation`,
`clinical_saturation`, `combined_support`, `translation_gap`, `trial_count`,
`mapped_trial_mechanism`, `max_phase_score`, `has_results_fraction`,
`member_gene_count`, `member_genes_matched`, `clinical_translation_note`,
`crosswalk_note`, and `_formulas`.

---

## 3. Provenance summary

| Score | Source(s) | Proxy? |
| --- | --- | --- |
| `genetic_support` | GWAS Catalog (best -log10 p, study count) + Open Targets genetic_association | No |
| `functional_support` | Open Targets L2G (max across loci) + brain-QTL colocalisation bonus, from `functional_links.jsonl` | No (real functional layer) |
| Open Targets `open_targets_*` | Open Targets Platform association scores | No |
| `pathway_group` | curated `map/gene_pathway.csv` | Curated |
| `clinical_translation` / `clinical_saturation` | ClinicalTrials.gov trials (phase, count, has_results) via mechanism crosswalk | No |
| `combined_support` | derived from member-gene `genetic_support`/`functional_support` | Mixed |
| `translation_gap` | `combined_support * (1 - clinical_translation)` | Derived |

### Functional layer status & future work
- **`functional_support` is now a real functional layer** built from the Open
  Targets Locus-to-Gene (L2G) model, aggregated per gene across all fine-mapped
  loci (`functional_links.jsonl`). L2G already integrates colocalisation and QTL
  evidence across many studies into one score, so it is the primary signal.
- **Raw GWAS->QTL colocalisation is sparse for AD.** The current build has
  **0** colocalisation links, so the brain-cell-type `coloc_bonus` is `0.0`
  everywhere today; the bonus machinery (microglia/astrocyte/neuron/…) is ready
  for when brain-QTL colocalisation becomes available.
- **eQTL Catalogue (optional future work).** A dedicated brain-cell-type eQTL
  enrichment (e.g. the eQTL Catalogue, `evidence_type = "eqtl_catalogue"` in the
  functional_link schema) is an optional future addition that would populate the
  cell-type bonus directly; it is **not** integrated yet.
""" % dict(
        anchor_id=OT_ANCHOR_DISEASE_ID,
        gsw_p=GS_W_PVAL, gsw_s=GS_W_STUDIES, gsw_ot=GS_W_OT,
        p_cap=GS_NEGLOG10P_CAP, s_cap=GS_STUDY_CAP,
        func_no_link_note=FUNCTIONAL_NO_LINK_NOTE,
        fs_bonus_max=FS_COLOC_BONUS_MAX,
        fs_bonus_rows="\n".join(
            "| `%s` | +%s |" % (kw, bonus) for kw, bonus in FS_CELL_TYPE_BONUS
        ),
        crosswalk_rows=crosswalk_rows,
        phase_rows=phase_rows,
        phase_default=PHASE_SCORE_DEFAULT,
        ctw_p=CT_W_PHASE, ctw_c=CT_W_COUNT, ctw_r=CT_W_RESULTS,
        ct_cap=CT_TRIAL_COUNT_CAP, sat_cap=SAT_TRIAL_COUNT_CAP,
    )
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Sanity-check printing
# ---------------------------------------------------------------------------

def print_sanity(genes, pathways):
    print("\n=== TOP 10 pathways by translation_gap ===")
    ranked = sorted(
        pathways,
        key=lambda r: r["scores"]["translation_gap"],
        reverse=True,
    )
    for r in ranked[:10]:
        s = r["scores"]
        print(
            "  %-32s gap=%.4f  combined_support=%.4f  clin_transl=%.4f  "
            "trials=%d (mech=%s)"
            % (
                r.get("label", r.get("pathway_id")),
                s["translation_gap"],
                s["combined_support"],
                s["clinical_translation"],
                s["trial_count"],
                s["mapped_trial_mechanism"],
            )
        )

    # Gene disease_group distribution (each gene may span several groups).
    print("\n=== Gene disease_group distribution ===")
    n_with_dg = 0
    dg_counts = {}
    for r in genes:
        dgs = r.get("disease_groups") or []
        if dgs:
            n_with_dg += 1
        for dg in dgs:
            dg_counts[dg] = dg_counts.get(dg, 0) + 1
    print("  total genes=%d  carrying disease_groups=%d" % (len(genes), n_with_dg))
    for dg, c in sorted(dg_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print("    %-24s %d" % (dg, c))

    print("\n=== TOP 12 genes by genetic_support (symbol, score, disease_groups) ===")
    ranked_g = sorted(
        genes,
        key=lambda r: (r.get("evidence_scores") or {}).get("genetic_support") or 0.0,
        reverse=True,
    )
    for r in ranked_g[:12]:
        es = r.get("evidence_scores") or {}
        print(
            "  %-12s gs=%.4f  disease_groups=%s"
            % (
                r.get("symbol"),
                es.get("genetic_support") or 0.0,
                r.get("disease_groups") or [],
            )
        )

    # functional_support (L2G) ranking + coverage.
    n_fs = sum(
        1 for r in genes
        if (r.get("evidence_scores") or {}).get("functional_support") is not None
    )
    print("\n=== TOP 12 genes by functional_support (L2G) "
          "[%d/%d genes non-null] ===" % (n_fs, len(genes)))
    ranked_fs = sorted(
        genes,
        key=lambda r: (r.get("evidence_scores") or {}).get("functional_support")
        or -1.0,
        reverse=True,
    )
    for r in ranked_fs[:12]:
        es = r.get("evidence_scores") or {}
        fsc = es.get("functional_support_components") or {}
        print(
            "  %-12s fs=%-8s max_l2g=%-8s n_loci=%-3s brain_qtl=%s"
            % (
                r.get("symbol"),
                es.get("functional_support"),
                fsc.get("max_l2g"),
                fsc.get("n_l2g_loci"),
                fsc.get("has_brain_qtl_coloc"),
            )
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    genes_path = common.PROCESSED_DIR / "genes.jsonl"
    pathways_path = common.PROCESSED_DIR / "pathways.jsonl"
    te_path = common.PROCESSED_DIR / "target_evidence.jsonl"
    trials_path = common.PROCESSED_DIR / "trials.jsonl"
    fl_path = common.PROCESSED_DIR / "functional_links.jsonl"

    common.log("reading inputs")
    genes = common.read_jsonl(genes_path)
    pathways = common.read_jsonl(pathways_path)
    target_evidence = common.read_jsonl(te_path)
    trials = common.read_jsonl(trials_path)
    functional_links = (common.read_jsonl(fl_path)
                        if fl_path.exists() else [])
    gene_to_pathway, pathway_members = load_gene_pathway_map()

    common.log("genes=%d pathways=%d target_evidence=%d trials=%d "
               "functional_links=%d"
               % (len(genes), len(pathways), len(target_evidence),
                  len(trials), len(functional_links)))

    # A) enrich genes
    (ot_by_gene_id, ot_by_symbol,
     ot_dgroups_by_gene_id, ot_dgroups_by_symbol) = build_ot_index(
        target_evidence
    )
    fl_by_gene_id, fl_by_symbol = build_functional_index(functional_links)
    genes, n_ot, n_functional = enrich_genes(
        genes, ot_by_gene_id, ot_by_symbol, gene_to_pathway,
        ot_dgroups_by_gene_id, ot_dgroups_by_symbol,
        fl_by_gene_id, fl_by_symbol,
    )
    common.log("Open Targets matched %d/%d genes" % (n_ot, len(genes)))
    common.log("functional_support (L2G) populated for %d/%d genes"
               % (n_functional, len(genes)))

    # index enriched genes by symbol for pathway combined_support
    genes_by_symbol = {}
    for g in genes:
        sym = g.get("symbol")
        if sym and sym not in genes_by_symbol:
            genes_by_symbol[sym] = g

    # B) enrich pathways
    trials_by_mech = index_trials_by_mechanism(trials)
    pathways = enrich_pathways(pathways, trials_by_mech, genes_by_symbol,
                               pathway_members)

    # rewrite in place
    n_g = common.write_jsonl(genes_path, genes)
    n_p = common.write_jsonl(pathways_path, pathways)
    common.log("wrote genes=%d pathways=%d" % (n_g, n_p))

    # C) SCORING.md
    md_path = write_scoring_md()
    common.log("wrote %s" % md_path)

    # sanity print
    print_sanity(genes, pathways)


if __name__ == "__main__":
    main()
