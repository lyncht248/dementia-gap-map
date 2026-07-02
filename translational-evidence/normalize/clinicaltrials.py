"""Normalize raw ClinicalTrials.gov studies into the trial.schema.json shape.

Reads the combined raw JSONL produced by
``ingest/clinicaltrials.py`` (the most recent date-stamped file), flattens each
study's protocolSection, classifies a transparent ``trial_category``, assigns a
``mechanism_group`` from the curated map, and writes
``data/processed/translational-evidence/trials.jsonl``.

Every derived field keeps its explaining inputs alongside it
(``trial_category_reason``, ``mechanism_match``, ``mechanism_source``).

Standard library only (Python 3.9).

Usage:

    python3 translational-evidence/normalize/clinicaltrials.py
"""

import csv
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


MECHANISM_MAP_PATH = common.TE_DIR / "map" / "intervention_mechanism.csv"
OUTPUT_PATH = common.PROCESSED_DIR / "trials.jsonl"

# Intervention-type buckets used by the trial_category rules.
THERAPEUTIC_TYPES = {
    "DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT",
}
IMAGING_KEYWORDS = ("pet", "mri", "scan", "imaging", "tomography")
BIOMARKER_KEYWORDS = (
    "biomarker", "csf", "cerebrospinal", "plasma", "blood test",
    "blood-based", "diagnostic", "assay",
)
LIFESTYLE_KEYWORDS = (
    "exercise", "physical activity", "diet", "dietary", "nutrition",
    "cognitive training", "cognitive stimulation", "care", "caregiver",
    "lifestyle", "music", "yoga", "meditation", "mindfulness",
    "counseling", "rehabilitation", "education",
)


# ---------------------------------------------------------------------------
# Curated mechanism map
# ---------------------------------------------------------------------------

def _skip_leading_comments(fh):
    """Advance past any leading ``#`` comment lines and return the file handle.

    The GENERATED intervention_mechanism.csv (written by
    map/intervention_mechanism_build.py) starts with a ``# GENERATED ...``
    provenance comment. Skip such comment lines so the DictReader sees the real
    header row first. A hand-edited CSV with no leading comment is handled
    identically (nothing to skip). Mirrors map/pathways.py.
    """
    pos = fh.tell()
    line = fh.readline()
    while line and line.lstrip().startswith("#"):
        pos = fh.tell()
        line = fh.readline()
    fh.seek(pos)
    return fh


def load_mechanism_map(path):
    """Load the curated keyword->mechanism map as an ordered list of pairs.

    Order is preserved from the CSV so 'first match wins' is deterministic.
    Keywords are lowercased for case-insensitive substring matching.
    """
    pairs = []  # list of (keyword_lower, mechanism_group)
    with pathlib.Path(path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(_skip_leading_comments(fh))
        for row in reader:
            keyword = (row.get("keyword") or "").strip().lower()
            group = (row.get("mechanism_group") or "").strip()
            if keyword and group:
                pairs.append((keyword, group))
    return pairs


def match_mechanism(text_blobs, mechanism_pairs):
    """Return (mechanism_group, matched_keyword) or (None, None).

    ``text_blobs`` is an ordered list of lowercased strings to search
    (e.g. intervention names first, then the brief title as fallback). The
    first keyword (in CSV order) that is a substring of ANY blob wins.
    """
    for keyword, group in mechanism_pairs:
        for blob in text_blobs:
            if blob and keyword in blob:
                return group, keyword
    return None, None


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _get(d, *keys):
    """Safely walk nested dicts; return None if any level is missing."""
    cur = d
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _clean_str(value):
    """Return a stripped non-empty string, or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def extract_interventions(protocol):
    """Return a list of {name,type,description} dicts (name required)."""
    raw = _get(protocol, "armsInterventionsModule", "interventions") or []
    out = []
    for iv in raw:
        if not isinstance(iv, dict):
            continue
        name = _clean_str(iv.get("name"))
        if not name:
            # Schema requires intervention.name to be a string; skip nameless.
            continue
        out.append({
            "name": name,
            "type": _clean_str(iv.get("type")),
            "description": _clean_str(iv.get("description")),
        })
    return out


# ---------------------------------------------------------------------------
# trial_category classification (transparent rules)
# ---------------------------------------------------------------------------

def classify_trial_category(study_type, interventions, brief_title):
    """Return (trial_category, reason) using transparent, ordered rules."""
    st = (study_type or "").upper()

    if st == "OBSERVATIONAL":
        return "observational", "studyType=OBSERVATIONAL"

    types = {(iv.get("type") or "").upper() for iv in interventions}
    types.discard("")

    # Text used for keyword sub-classification: intervention names +
    # descriptions + brief title.
    text_parts = []
    for iv in interventions:
        if iv.get("name"):
            text_parts.append(iv["name"].lower())
        if iv.get("description"):
            text_parts.append(iv["description"].lower())
    if brief_title:
        text_parts.append(brief_title.lower())
    blob = " | ".join(text_parts)

    def _has(keywords):
        return next((k for k in keywords if k in blob), None)

    # Rule 1: clear therapeutic intervention types.
    therapeutic_hit = types & THERAPEUTIC_TYPES
    if therapeutic_hit:
        return (
            "therapeutic",
            "intervention type(s) %s in therapeutic set"
            % sorted(therapeutic_hit),
        )

    # Rule 2: DEVICE / DIAGNOSTIC_TEST with imaging keywords -> imaging.
    if types & {"DEVICE", "DIAGNOSTIC_TEST"}:
        img_kw = _has(IMAGING_KEYWORDS)
        if img_kw:
            return (
                "imaging",
                "type in {DEVICE,DIAGNOSTIC_TEST} + imaging keyword %r"
                % img_kw,
            )

    # Rule 3: DIAGNOSTIC_TEST or biomarker keywords -> diagnostic_biomarker.
    if "DIAGNOSTIC_TEST" in types:
        return (
            "diagnostic_biomarker",
            "intervention type DIAGNOSTIC_TEST",
        )
    bio_kw = _has(BIOMARKER_KEYWORDS)
    if bio_kw and types & {"DEVICE", "OTHER", "PROCEDURE", "RADIATION"}:
        return (
            "diagnostic_biomarker",
            "biomarker keyword %r with non-therapeutic intervention type"
            % bio_kw,
        )

    # Rule 4: lifestyle / behavioral / dietary care.
    if types & {"BEHAVIORAL", "DIETARY_SUPPLEMENT", "OTHER"}:
        life_kw = _has(LIFESTYLE_KEYWORDS)
        if life_kw:
            return (
                "lifestyle_care",
                "type in {BEHAVIORAL,DIETARY_SUPPLEMENT,OTHER} + lifestyle "
                "keyword %r" % life_kw,
            )
        if types & {"BEHAVIORAL", "DIETARY_SUPPLEMENT"}:
            return (
                "lifestyle_care",
                "intervention type(s) %s"
                % sorted(types & {"BEHAVIORAL", "DIETARY_SUPPLEMENT"}),
            )

    # Rule 5: no confident classification.
    if not interventions:
        return None, "interventional study with no listed interventions"
    return (
        None,
        "no category rule matched (intervention types=%s)"
        % (sorted(types) or "[]"),
    )


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------

def normalize_study(study, mechanism_pairs):
    """Return a normalized record dict, or None if it must be skipped."""
    protocol = study.get("protocolSection")
    if not isinstance(protocol, dict):
        return None

    nct_id = _clean_str(_get(protocol, "identificationModule", "nctId"))
    brief_title = _clean_str(
        _get(protocol, "identificationModule", "briefTitle")
    )
    overall_status = _clean_str(
        _get(protocol, "statusModule", "overallStatus")
    )

    # Required non-null fields: skip if any missing.
    if not (nct_id and brief_title and overall_status):
        return None

    official_title = _clean_str(
        _get(protocol, "identificationModule", "officialTitle")
    )
    study_type = _clean_str(_get(protocol, "designModule", "studyType"))

    phases_raw = _get(protocol, "designModule", "phases")
    phases = [p for p in phases_raw if p] if isinstance(phases_raw, list) \
        else []

    conditions_raw = _get(protocol, "conditionsModule", "conditions")
    conditions = [c for c in conditions_raw if c] \
        if isinstance(conditions_raw, list) else []

    interventions = extract_interventions(protocol)

    start_date = _clean_str(
        _get(protocol, "statusModule", "startDateStruct", "date")
    )
    completion_date = _clean_str(
        _get(protocol, "statusModule", "completionDateStruct", "date")
    )
    primary_completion_date = _clean_str(
        _get(protocol, "statusModule", "primaryCompletionDateStruct", "date")
    )

    trial_category, category_reason = classify_trial_category(
        study_type, interventions, brief_title
    )

    # Mechanism matching: intervention names first, then brief title fallback.
    text_blobs = [iv["name"].lower() for iv in interventions]
    if brief_title:
        text_blobs.append(brief_title.lower())

    if interventions:
        group, matched = match_mechanism(text_blobs, mechanism_pairs)
        if group is None:
            mechanism_group = "other"
            mechanism_match = None
        else:
            mechanism_group = group
            mechanism_match = matched
        mechanism_source = "manual_intervention_map"
    else:
        mechanism_group = None
        mechanism_match = None
        mechanism_source = "manual_intervention_map"

    # disease_group: classify over the study's conditions joined into one
    # string, so co-occurring subtypes (e.g. Alzheimer + vascular) trigger the
    # mixed_dementia precedence rule inside common.classify_disease_group.
    disease_group = common.classify_disease_group(" | ".join(conditions))

    lead_sponsor = _clean_str(
        _get(protocol, "sponsorCollaboratorsModule", "leadSponsor", "name")
    )
    lead_sponsor_class = _clean_str(
        _get(protocol, "sponsorCollaboratorsModule", "leadSponsor", "class")
    )
    enrollment = _get(protocol, "designModule", "enrollmentInfo", "count")
    has_results = study.get("hasResults")

    return {
        "nct_id": nct_id,
        "brief_title": brief_title,
        "official_title": official_title,
        "overall_status": overall_status,
        "study_type": study_type,
        "phases": phases,
        "conditions": conditions,
        "disease_group": disease_group,
        "interventions": interventions,
        "start_date": start_date,
        "completion_date": completion_date,
        "trial_category": trial_category,
        "trial_category_reason": category_reason,
        "mechanism_group": mechanism_group,
        "mechanism_match": mechanism_match,
        "mechanism_source": mechanism_source,
        # Explainability / useful extras.
        "lead_sponsor": lead_sponsor,
        "lead_sponsor_class": lead_sponsor_class,
        "enrollment": enrollment if isinstance(enrollment, int) else None,
        "has_results": bool(has_results) if has_results is not None else None,
        "primary_completion_date": primary_completion_date,
    }


def _latest_combined_raw():
    """Find the most recent combined raw studies JSONL, or None.

    Prefers the ADRD (Alzheimer + related dementias) combined file; falls back
    to the legacy Alzheimer-only file if no ADRD file exists yet.
    """
    for pattern in (
        "clinicaltrials_adrd_studies_*.jsonl",
        "clinicaltrials_alzheimer_studies_*.jsonl",
    ):
        candidates = sorted(common.RAW_DIR.glob(pattern))
        if candidates:
            return candidates[-1]
    return None


def main():
    raw_path = _latest_combined_raw()
    if raw_path is None:
        raise RuntimeError(
            "No combined raw file found in %s (pattern "
            "clinicaltrials_adrd_studies_*.jsonl or the legacy "
            "clinicaltrials_alzheimer_studies_*.jsonl). Run the ingest step "
            "first." % common.RAW_DIR
        )

    common.log("reading raw studies from %s" % raw_path)
    studies = common.read_jsonl(raw_path)
    common.log("loaded %d raw studies" % len(studies))

    mechanism_pairs = load_mechanism_map(MECHANISM_MAP_PATH)
    common.log(
        "loaded %d curated mechanism keywords from %s"
        % (len(mechanism_pairs), MECHANISM_MAP_PATH)
    )

    records = []
    skipped = 0
    for study in studies:
        rec = normalize_study(study, mechanism_pairs)
        if rec is None:
            skipped += 1
            continue
        records.append(rec)

    count = common.write_jsonl(OUTPUT_PATH, records)
    common.log(
        "normalize complete: %d records written, %d skipped "
        "(missing nct_id/brief_title/overall_status) -> %s"
        % (count, skipped, OUTPUT_PATH)
    )

    if count == 0:
        raise RuntimeError(
            "0 normalized records written; refusing to report success."
        )

    # Small distribution summary for eyeballing.
    from collections import Counter
    cat_counts = Counter(r["trial_category"] for r in records)
    mech_counts = Counter(r["mechanism_group"] for r in records)
    dg_counts = Counter(r["disease_group"] for r in records)
    common.log("trial_category distribution: %s" % dict(cat_counts))
    common.log("mechanism_group distribution: %s" % dict(mech_counts))
    common.log("disease_group distribution: %s" % dict(dg_counts))

    return 0


if __name__ == "__main__":
    sys.exit(main())
