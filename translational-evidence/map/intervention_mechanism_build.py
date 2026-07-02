"""Capture ALL drug->mechanism annotations from Open Targets for the drug /
biological interventions that actually appear in the ADRD trial corpus, then
project a single mechanism for the legacy trial-tagging CSV.

CORE PRINCIPLE - RECORD EVERYTHING, MULTI-VALUED
------------------------------------------------
For each resolved intervention we persist the FULL set of mechanism-of-action
rows Open Targets reports (every MoA text + every target symbol on every row),
never collapsing to a single "voted" winner and discarding the rest. The
mechanism tags are a LIST of signals - each ``{mechanism, source, matched_term}``
- so a drug may legitimately carry several mechanisms from several MoA rows and
we keep them all. We derive ONE ``primary_mechanism`` ONLY for the thin
single-value CSV the trial normalizer consumes; it is the most-supported
mechanism (tie-break by ``MECHANISM_PRIORITY``), clearly labelled as a
projection. Nothing is fabricated: a drug OT cannot resolve, or a resolved drug
with no MoA rows, records exactly what OT returned (possibly nothing) - never an
invented mechanism.

Input
-----
data/processed/translational-evidence/trials.jsonl
    Distinct DRUG / BIOLOGICAL intervention names are extracted and ranked by
    the number of DISTINCT trials each appears in (case-insensitive). Pure
    placebo / saline / standard-of-care / usual-care controls are skipped. The
    TOP ``TOP_N`` (default 400) names are resolved against Open Targets.

Sources (probed live; membership + mechanism text are API-derived)
------------------------------------------------------------------
  1. OT GraphQL  search(queryString, entityNames:["drug"]) -> chemblId
  2. OT GraphQL  drug(chemblId).mechanismsOfAction.rows
        -> [{mechanismOfAction, targets:[{id, approvedSymbol}]} ...ALL rows...]

The ONLY residual hand element is the transparent keyword / target ruleset
(``TRIAL_MECHANISM_KEYWORDS``) that maps captured MoA strings + target symbols
into the fixed trial-mechanism vocabulary; it lives in code, not per-drug.

Outputs (all GENERATED - do not hand-edit)
------------------------------------------
  - data/processed/translational-evidence/drug_mechanism_api.jsonl
        ONE rich record per resolved drug RECORDING EVERYTHING:
          { name, chembl_id,
            sources:{ opentargets:[ {moa, targets:[symbols]} ...all rows... ] },
            mechanism_signals:[ {mechanism, source, matched_term} ...all... ],
            mechanisms:[distinct], primary_mechanism, primary_support }
        plus explainability extras (query_name, ot_name, trial_count, notes).
  - translational-evidence/map/intervention_mechanism.csv
        THIN projection consumed by normalize/clinicaltrials.py:
          keyword          = resolved drug name (lowercased, substring-matchable)
          mechanism_group  = primary_mechanism
          notes            = provenance (moa / targets)

Raw API responses are cached under
  data/raw/translational-evidence/opentargets_drugs/   (search_* + drug_* docs)

Standard library only (Python 3.9). Network via common.get_json /
common.post_json (cached to data/raw; TE_REFRESH=1 forces fresh calls).

Run:
    python3 translational-evidence/map/intervention_mechanism_build.py
    python3 translational-evidence/map/intervention_mechanism_build.py --top-n 200
"""

import argparse
import csv
import os
import re
import sys
import pathlib
from collections import Counter, OrderedDict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Paths / endpoints
# ---------------------------------------------------------------------------

TRIALS_JSONL = common.PROCESSED_DIR / "trials.jsonl"
INTERVENTION_CSV = common.TE_DIR / "map" / "intervention_mechanism.csv"
DRUG_API_JSONL = common.PROCESSED_DIR / "drug_mechanism_api.jsonl"

OT_DRUGS_CACHE_DIR = common.RAW_DIR / "opentargets_drugs"
OT_GRAPHQL_URL = "https://api.platform.opentargets.org/api/v4/graphql"

TOP_N_DEFAULT = 400

CSV_HEADER_COMMENT = (
    "# GENERATED from Open Targets MoA+targets; full data in "
    "drug_mechanism_api.jsonl"
)

# The intervention types we treat as drug / biological therapies.
DRUG_TYPES = {"DRUG", "BIOLOGICAL"}

SRC_OT = "open_targets"


# ---------------------------------------------------------------------------
# Trial-mechanism vocabulary + keyword / target ruleset (the ONLY hand element).
# Matched against each OT mechanismOfAction text AND that row's target symbols.
# ---------------------------------------------------------------------------

TRIAL_MECHANISM_KEYWORDS = OrderedDict([
    ("amyloid", {
        "keywords": ["amyloid", "secretase", "bace", "a4 protein"],
        "targets": {"APP", "BACE1", "PSEN1", "PSEN2"},
    }),
    ("tau", {
        "keywords": ["tau", "microtubule"],
        "targets": {"MAPT"},
    }),
    ("cholinergic_symptomatic", {
        "keywords": [
            "cholinesterase", "acetylcholine", "cholinergic", "nmda",
            "glutamate receptor",
        ],
        "targets": {"ACHE", "BCHE", "CHRNA7", "GRIN2B"},
    }),
    ("inflammation_microglia", {
        "keywords": [
            "immun", "inflamm", "microglia", "tnf", "interleukin", "complement",
        ],
        "targets": {"TREM2", "CD33"},
    }),
    ("lipid_metabolism", {
        "keywords": [
            "lipid", "cholesterol", "statin", "hmg-coa", "apolipoprotein",
            "ppar", "insulin", "glp-1",
        ],
        "targets": set(),
    }),
    ("vascular", {
        "keywords": [
            "angiotensin", "adrenergic", "calcium channel", "anticoagul",
            "antiplatelet", "vascular",
        ],
        "targets": set(),
    }),
    ("synaptic_neuroprotection", {
        "keywords": [
            "neuroprotect", "bdnf", "ngf", "sigma", "serotonin", "dopamine",
            "monoamine",
        ],
        "targets": set(),
    }),
    ("diagnostic_biomarker", {
        "keywords": [],
        "targets": set(),
    }),
])

# Tie-break priority for the primary_mechanism projection (highest first).
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


# ---------------------------------------------------------------------------
# Intervention-name selection from trials.jsonl
# ---------------------------------------------------------------------------

# Names that are pure controls / vehicles - never resolved against OT. Matched
# as substrings of the lowercased intervention name (transparent, auditable).
CONTROL_SUBSTRINGS = (
    "placebo",
    "saline",
    "standard of care",
    "standard-of-care",
    "usual care",
    "usual-care",
    "sham",
    "vehicle",
    "no intervention",
    "no treatment",
    "matching comparator",
)

# Dose / formulation noise stripped before the OT search query (NOT from the
# frequency key, and NOT from the CSV keyword, which is the OT-resolved name).
_DOSE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ug|g|ml|iu|units?|%|mg/kg|mg/ml|kg)\b.*$",
    re.IGNORECASE,
)
_FORMULATION_RE = re.compile(
    r"\b(oral|tablet|capsule|injection|injectable|solution|infusion|"
    r"transdermal|patch|film|coated|extended[- ]release|sustained[- ]release|"
    r"immediate[- ]release|hydrochloride|hydrobromide|sulfate|sulphate|"
    r"maleate|mesylate|fumarate|tartrate|citrate|besylate|hcl|xr|er|sr|"
    r"once daily|twice daily|group|arm|comparator|matching)\b.*$",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\s*\([^)]*\)")


def is_control(name_lower):
    """True if the (lowercased) intervention name is a pure control / vehicle."""
    return any(sub in name_lower for sub in CONTROL_SUBSTRINGS)


def clean_query_name(raw_name):
    """Produce a clean-ish drug name for the OT search query.

    Strips parenthetical asides, trailing dose specifications and common
    formulation / salt words so ``search("lecanemab 10 mg/kg")`` becomes
    ``search("lecanemab")``. Returns a stripped string (possibly unchanged);
    the ORIGINAL name still drives frequency counting and stays in the record.
    """
    s = _PAREN_RE.sub(" ", raw_name)
    s = _DOSE_RE.sub(" ", s)
    s = _FORMULATION_RE.sub(" ", s)
    s = re.sub(r"[,;:/]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s or raw_name.strip()


def rank_drug_names(trials):
    """Rank distinct DRUG / BIOLOGICAL intervention names by trial frequency.

    Counting is case-insensitive (keyed on the lowercased name) and per-trial
    de-duplicated (a name appearing twice in one trial counts once). Returns a
    list of ``(display_name, trial_count)`` sorted by trial_count desc then
    name, plus the number of trials that carry >=1 non-control DRUG/BIOLOGICAL
    intervention (the denominator for coverage).

    ``display_name`` is the most frequently seen original-case spelling for that
    lowercased key, so provenance stays readable.
    """
    trial_count = Counter()          # name_lower -> #distinct trials
    casing_count = {}                # name_lower -> Counter(original spellings)
    trials_with_drug = 0

    for trial in trials:
        names_lower_here = set()
        for iv in trial.get("interventions") or []:
            if (iv.get("type") or "").upper() not in DRUG_TYPES:
                continue
            raw = (iv.get("name") or "").strip()
            if not raw:
                continue
            low = raw.lower()
            if is_control(low):
                continue
            names_lower_here.add(low)
            casing_count.setdefault(low, Counter())[raw] += 1
        if names_lower_here:
            trials_with_drug += 1
            for low in names_lower_here:
                trial_count[low] += 1

    ranked = []
    for low, cnt in trial_count.items():
        display = casing_count[low].most_common(1)[0][0]
        ranked.append((display, cnt))
    ranked.sort(key=lambda t: (-t[1], t[0].lower()))
    return ranked, trials_with_drug


# ---------------------------------------------------------------------------
# Open Targets fetch (drug search + mechanism of action) - captures EVERYTHING
# ---------------------------------------------------------------------------

_OT_DRUG_SEARCH_QUERY = (
    "query($s:String!){search(queryString:$s,entityNames:[\"drug\"],"
    "page:{index:0,size:1}){hits{id name}}}"
)
_OT_DRUG_QUERY = (
    "query($c:String!){drug(chemblId:$c){name mechanismsOfAction{rows{"
    "mechanismOfAction targets{id approvedSymbol}}}}}"
)


def _cache_name(text):
    """Filesystem-safe cache stem for a name / id."""
    return common.slug(text) or "unnamed"


def search_drug_chembl(query_name):
    """Resolve a drug NAME to (chemblId, ot_name) via OT search.

    Returns (None, None) when OT reports no drug hit - membership is
    API-decided, and we record nothing rather than fabricate a match.
    """
    if not query_name:
        return None, None
    cache_path = OT_DRUGS_CACHE_DIR / (
        "search_" + _cache_name(query_name) + ".json"
    )
    data = common.post_json(
        OT_GRAPHQL_URL,
        {"query": _OT_DRUG_SEARCH_QUERY, "variables": {"s": query_name}},
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

    Returns (ot_name_or_None, [{moa, targets:[symbols]} ...all rows...]).
    Records everything OT reports (no collapse). Missing drug -> (None, []).
    Each row keeps its full target list as approved symbols; unnamed targets are
    dropped (they carry no mechanism signal) but never invented.
    """
    if not chembl_id:
        return None, []
    cache_path = OT_DRUGS_CACHE_DIR / (
        "drug_" + _cache_name(chembl_id) + ".json"
    )
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
                sym = t.get("approvedSymbol")
                if sym:
                    targets.append(sym)
        rows_out.append({"moa": moa, "targets": targets})
    return drug.get("name"), rows_out


# ---------------------------------------------------------------------------
# Mechanism classification - MULTI-VALUED (keep every signal)
# ---------------------------------------------------------------------------

def _classify_row(moa_text, target_symbols):
    """Return the set of (mechanism, matched_term) for one MoA row.

    Matches BOTH the mechanism-of-action free text (keywords) AND the row's
    target symbols against the trial mechanism vocabulary. Keeps every distinct
    match; a single MoA row can therefore emit several mechanisms.
    """
    hits = set()
    text_l = (moa_text or "").lower()
    symbols_up = {(s or "").upper() for s in target_symbols if s}
    for mech, rule in TRIAL_MECHANISM_KEYWORDS.items():
        for kw in rule["keywords"]:
            if kw and kw in text_l:
                hits.add((mech, kw))
                break  # one keyword per mechanism per row is enough
        for sym in rule["targets"]:
            if sym in symbols_up:
                hits.add((mech, "target:" + sym))
    return hits


def collect_drug_signals(moa_rows):
    """Collect ALL mechanism signals across a drug's MoA rows.

    Each signal is ``{mechanism, source, matched_term, moa_text}`` with source
    'open_targets' (all drug mechanism evidence is OT-derived). Keeps everything;
    duplicate (mechanism, term, moa_text) triples are de-duplicated.
    """
    signals = []
    seen = set()
    for row in moa_rows:
        moa = row.get("moa")
        symbols = row.get("targets") or []
        for mech, term in _classify_row(moa, symbols):
            key = (mech, term, moa)
            if key in seen:
                continue
            seen.add(key)
            signals.append({
                "mechanism": mech,
                "source": SRC_OT,
                "matched_term": term,
                "moa_text": moa,
            })
    return signals


def rank_mechanisms(signals):
    """Rank mechanisms by number of distinct supporting matched-terms.

    Returns (mechanisms_sorted, primary_mechanism, primary_support):
      - mechanisms_sorted: distinct mechanisms, most-supported first
        (tie-break by MECHANISM_PRIORITY);
      - primary_mechanism: top of that list, or None when there are no signals;
      - primary_support: the {source, matched_term, moa_text} signals that back
        the primary mechanism.
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


# ---------------------------------------------------------------------------
# Per-drug processing
# ---------------------------------------------------------------------------

def build_notes(primary, primary_support, moa_rows, max_terms=3):
    """Build the CSV notes column: short, explainable OT provenance.

    Example:
      "primary=cholinergic_symptomatic; moa:Acetylcholinesterase inhibitor;
       target:ACHE | 1 MoA row(s)"
    """
    counts = "%d MoA row(s)" % len(moa_rows)
    if not primary or primary == MECHANISM_OTHER:
        # Show the raw MoA text(s) so 'other' is still explainable.
        moas = [r.get("moa") for r in moa_rows if r.get("moa")]
        shown = "; ".join(moas[:max_terms]) if moas else "no MoA reported"
        label = MECHANISM_OTHER if primary == MECHANISM_OTHER else "unresolved"
        return "primary=%s; %s | %s" % (label, shown, counts)
    seen = set()
    shown = []
    for s in primary_support:
        term = s["matched_term"]
        if term.startswith("target:"):
            label = term
        else:
            label = "moa:" + (s.get("moa_text") or term)
        if label in seen:
            continue
        seen.add(label)
        shown.append(label)
        if len(shown) >= max_terms:
            break
    more = len({(s["matched_term"], s.get("moa_text")) for s in primary_support}) \
        - len(shown)
    tail = " (+%d more)" % more if more > 0 else ""
    return "primary=%s; %s%s | %s" % (primary, "; ".join(shown), tail, counts)


def process_drug(display_name, trial_count):
    """Resolve one intervention name through Open Targets -> rich record.

    Returns the record dict, or None if OT has no drug hit for the name (we
    record nothing rather than fabricate). ALL captured MoA rows are preserved.
    """
    query_name = clean_query_name(display_name)
    chembl_id, ot_search_name = search_drug_chembl(query_name)
    if not chembl_id:
        return None
    ot_name, moa_rows = fetch_drug_moa(chembl_id)

    signals = collect_drug_signals(moa_rows)
    mechanisms, primary, primary_support = rank_mechanisms(signals)
    if not primary:
        # Resolved to a real drug but nothing matched the vocabulary (or no MoA
        # rows at all) -> transparent 'other'. Still a real, captured record.
        primary = MECHANISM_OTHER
        mechanisms = [MECHANISM_OTHER]

    resolved_name = ot_name or ot_search_name or display_name
    notes = build_notes(primary, primary_support, moa_rows)

    return {
        "name": resolved_name,
        "chembl_id": chembl_id,
        # RECORD EVERYTHING: every MoA row with its full target-symbol list.
        "sources": {"opentargets": moa_rows},
        "mechanism_signals": signals,
        "mechanisms": mechanisms,
        "primary_mechanism": primary,
        "primary_support": primary_support,
        # Explainability extras (not part of the required contract but useful).
        "query_names": [query_name],
        "ot_name": ot_name,
        # ALL distinct trial spellings that resolved to this ChEMBL drug, and the
        # summed number of trials they appear in (aggregated across spellings).
        "trial_names": [display_name],
        "trial_count": trial_count,
        "notes": notes,
    }


def merge_by_chembl(records):
    """Collapse per-spelling records to ONE rich record per ChEMBL drug.

    Several trial spellings / development codes (e.g. 'Donepezil', 'Donepezil
    HCL', 'E2020') resolve to the same ChEMBL id with identical MoA-derived
    mechanism content. We keep ONE rich record per drug (the task's contract) but
    RECORD EVERYTHING by unioning the trial spellings + query names and summing
    their trial counts. The mechanism content (identical across spellings) is
    taken from the first, highest-frequency occurrence. Input order (frequency
    desc) is preserved for the surviving records.
    """
    merged = OrderedDict()  # chembl_id -> record
    for rec in records:
        cid = rec.get("chembl_id")
        if cid is None:
            # No id to merge on: keep as-is under a synthetic unique key.
            merged["__noid__%d" % len(merged)] = rec
            continue
        if cid not in merged:
            merged[cid] = dict(rec)
            continue
        base = merged[cid]
        for tn in rec.get("trial_names") or []:
            if tn not in base["trial_names"]:
                base["trial_names"].append(tn)
        for qn in rec.get("query_names") or []:
            if qn not in base["query_names"]:
                base["query_names"].append(qn)
        base["trial_count"] = (base.get("trial_count") or 0) + \
            (rec.get("trial_count") or 0)
    return list(merged.values())


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_drug_api_jsonl(records):
    """Write the rich per-drug JSONL (only OT-resolved drugs)."""
    return common.write_jsonl(DRUG_API_JSONL, records)


def write_intervention_csv(records):
    """Regenerate the THIN projection CSV consumed by the trial normalizer.

    Columns: keyword, mechanism_group, notes.
      - keyword         = resolved drug name (lowercased) - substring-matchable
                          against intervention names by the consumer.
      - mechanism_group = primary_mechanism.
      - notes           = provenance (moa / targets).

    De-duplicated by keyword (a drug resolved from several trial spellings maps
    to one canonical name). Rows are ordered longest-keyword-first, then
    alphabetically, so the consumer's first-match-wins substring scan never lets
    a short generic keyword shadow a longer, more specific one.
    """
    by_keyword = OrderedDict()
    for rec in records:
        keyword = (rec.get("name") or "").strip().lower()
        group = rec.get("primary_mechanism")
        if not keyword or not group:
            continue
        # First writer wins per keyword (records already frequency-ordered).
        if keyword in by_keyword:
            continue
        by_keyword[keyword] = {
            "keyword": keyword,
            "mechanism_group": group,
            "notes": rec.get("notes") or "",
        }

    rows = sorted(
        by_keyword.values(),
        key=lambda r: (-len(r["keyword"]), r["keyword"]),
    )

    INTERVENTION_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = INTERVENTION_CSV.with_name(INTERVENTION_CSV.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        # Header FIRST (no leading '#' comment) so csv.DictReader consumers
        # (normalize/clinicaltrials.py, the graph export) read all rows.
        writer = csv.DictWriter(
            fh, fieldnames=["keyword", "mechanism_group", "notes"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    os.replace(str(tmp), str(INTERVENTION_CSV))
    return rows


# ---------------------------------------------------------------------------
# Coverage reporting
# ---------------------------------------------------------------------------

def compute_trial_coverage(trials, resolved_keywords):
    """Return (n_covered, n_drug_trials, pct).

    A trial is 'covered' when at least one of its non-control DRUG/BIOLOGICAL
    intervention names contains one of the resolved (lowercased) keywords as a
    substring - i.e. the trial normalizer will now be able to tag it from the
    API-derived map. Denominator is trials with >=1 non-control DRUG/BIOLOGICAL
    intervention.
    """
    kws = [k for k in resolved_keywords if k]
    covered = 0
    denom = 0
    for trial in trials:
        names = []
        for iv in trial.get("interventions") or []:
            if (iv.get("type") or "").upper() not in DRUG_TYPES:
                continue
            raw = (iv.get("name") or "").strip().lower()
            if not raw or is_control(raw):
                continue
            names.append(raw)
        if not names:
            continue
        denom += 1
        if any(kw in nm for nm in names for kw in kws):
            covered += 1
    pct = (100.0 * covered / denom) if denom else 0.0
    return covered, denom, pct


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--top-n", type=int, default=TOP_N_DEFAULT,
        help="number of most-frequent drug/biological names to resolve "
             "(default %d)" % TOP_N_DEFAULT,
    )
    args = parser.parse_args(argv)

    trials = common.read_jsonl(TRIALS_JSONL)
    common.log("loaded %d trials from %s" % (len(trials), TRIALS_JSONL))

    ranked, trials_with_drug = rank_drug_names(trials)
    common.log(
        "distinct non-control DRUG/BIOLOGICAL names: %d; trials with >=1 such "
        "intervention: %d" % (len(ranked), trials_with_drug)
    )

    top = ranked[: args.top_n]
    common.log("resolving TOP %d names against Open Targets" % len(top))

    per_name = []
    unresolved = []
    for i, (name, cnt) in enumerate(top, start=1):
        rec = process_drug(name, cnt)
        if rec is not None:
            per_name.append(rec)
        else:
            unresolved.append((name, cnt))
        if i % 25 == 0 or i == len(top):
            common.log("processed %d/%d names (%d resolved)"
                       % (i, len(top), len(per_name)))

    # ONE rich record per ChEMBL drug: several trial spellings collapse together
    # (their spellings + counts are unioned, nothing discarded).
    records = merge_by_chembl(per_name)
    common.log(
        "resolved %d trial name(s) -> %d distinct ChEMBL drug record(s)"
        % (len(per_name), len(records))
    )

    n_api = write_drug_api_jsonl(records)
    common.log("wrote %d rich drug records to %s" % (n_api, DRUG_API_JSONL))

    csv_rows = write_intervention_csv(records)
    common.log("wrote %d CSV rows to %s" % (len(csv_rows), INTERVENTION_CSV))

    # Coverage: fraction of drug/bio trials the resolved keywords can tag.
    resolved_keywords = [r["keyword"] for r in csv_rows]
    covered, denom, pct = compute_trial_coverage(trials, resolved_keywords)
    common.log(
        "trial coverage: %d / %d drug/biological trials tag-able by the "
        "resolved map (%.1f%%)" % (covered, denom, pct)
    )

    # Mechanism distribution of the primary projection (per distinct drug).
    dist = Counter(r["primary_mechanism"] for r in records)
    common.log("primary_mechanism distribution: %s" % dict(dist))

    # Resolution stats.
    common.log(
        "resolution: %d/%d TOP names resolved (%d unresolved); collapsed to %d "
        "distinct drugs" % (len(per_name), len(top), len(unresolved),
                            len(records))
    )
    if unresolved[:10]:
        common.log("first unresolved names: %s"
                   % [n for n, _ in unresolved[:10]])
    common.log("multi-mechanism drugs (>1 mechanism): %d"
               % sum(1 for r in records if len(r["mechanisms"]) > 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
