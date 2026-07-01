"""Normalize Open Targets associated targets into target_evidence.jsonl.

Broadened from Alzheimer-only to Alzheimer + related dementias (ADRD). Reads
the combined ADRD raw cache produced by
``translational-evidence/ingest/open_targets.py`` (a list with one entry per
disease) and emits ONE record per (target_id, disease_id) pair -- a gene can
appear under several diseases and each such pairing survives.

Each record conforms to ``shared/schemas/target_evidence.schema.json`` and adds
a ``disease_group`` classified from the disease name via
``common.classify_disease_group``. Alzheimer rows are preserved (AD is a subset
of ADRD).

If no ADRD combined cache exists, this falls back to the legacy Alzheimer-only
combined cache so the step still works against an older cache.

Output: ``data/processed/translational-evidence/target_evidence.jsonl``

Run:

    python3 translational-evidence/normalize/open_targets.py
"""

import sys
import pathlib
import json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


SOURCE = "open_targets"

# datatypeScores ids we always surface in the scores object (schema-declared).
# Missing datatypes are set to null; any additional ids present in the response
# (e.g. genetic_literature, animal_model) are added as extra keys.
CORE_DATATYPES = (
    "genetic_association",
    "literature",
    "clinical",
    "affected_pathway",
    "rna_expression",
)


def _find_adrd_raw():
    """Return the newest ADRD combined Open Targets raw cache path, or None."""
    candidates = sorted(
        common.RAW_DIR.glob("open_targets_adrd_targets_*.json")
    )
    return candidates[-1] if candidates else None


def _find_alzheimer_raw():
    """Return the newest Alzheimer-only combined raw cache path, or None."""
    candidates = sorted(
        common.RAW_DIR.glob("open_targets_alzheimer_targets_*.json")
    )
    return candidates[-1] if candidates else None


def _load_disease_entries():
    """Load a list of per-disease entries {disease_id, disease_name, rows}.

    Prefers the ADRD combined list cache; falls back to the legacy
    Alzheimer-only combined dict (wrapped into a single-element list).
    """
    adrd_path = _find_adrd_raw()
    if adrd_path is not None:
        common.log("reading ADRD combined raw cache: %s" % adrd_path)
        with adrd_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise RuntimeError(
                "ADRD combined cache %s is not a list (got %s)"
                % (adrd_path, type(data).__name__)
            )
        return data, adrd_path

    alz_path = _find_alzheimer_raw()
    if alz_path is not None:
        common.log("no ADRD cache; falling back to Alzheimer-only cache: %s"
                   % alz_path)
        with alz_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        disease = data.get("disease") or {}
        entry = {
            "disease_id": disease.get("id"),
            "disease_name": disease.get("name"),
            "rows": data.get("rows") or [],
        }
        return [entry], alz_path

    raise RuntimeError(
        "No Open Targets combined raw cache found under %s. "
        "Run translational-evidence/ingest/open_targets.py first."
        % common.RAW_DIR
    )


def _build_scores(row):
    """Build the scores object from a target row's datatypeScores."""
    by_id = {}
    for entry in (row.get("datatypeScores") or []):
        dt_id = entry.get("id")
        if dt_id is None:
            continue
        by_id[dt_id] = entry.get("score")

    scores = {"overall": row.get("score")}

    # Always include the core datatypes; missing -> null.
    for dt_id in CORE_DATATYPES:
        scores[dt_id] = by_id.get(dt_id)

    # Include any additional datatype ids present as extra keys.
    for dt_id, val in by_id.items():
        if dt_id not in scores:
            scores[dt_id] = val

    return scores


def _to_record(row, disease_id, disease_label, disease_group):
    """Convert one associated-target row into a target_evidence record."""
    target = row.get("target") or {}
    target_id = target.get("id")
    symbol = target.get("approvedSymbol")
    approved_name = target.get("approvedName")

    return {
        "target_id": target_id,
        "target_label": symbol,
        "disease_id": disease_id,
        "disease_label": disease_label,
        "gene_id": target_id,
        "source": SOURCE,
        "scores": _build_scores(row),
        "approved_name": approved_name,
        "disease_group": disease_group,
    }


def main():
    entries, src_path = _load_disease_entries()

    records = []
    # Dedup key includes disease_id so AD and dementia rows for the same gene
    # both survive; only true (target_id, disease_id) duplicates are dropped.
    seen = set()
    dropped = 0

    for entry in entries:
        disease_id = entry.get("disease_id")
        disease_label = entry.get("disease_name")
        disease_group = common.classify_disease_group(disease_label)
        rows = entry.get("rows") or []
        common.log("normalizing disease %s (%s) group=%s: %d rows"
                   % (disease_id, disease_label, disease_group, len(rows)))

        for row in rows:
            target = row.get("target") or {}
            target_id = target.get("id")
            if target_id is None or disease_id is None:
                continue
            key = (target_id, disease_id)
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            records.append(
                _to_record(row, disease_id, disease_label, disease_group)
            )

    out_path = common.PROCESSED_DIR / "target_evidence.jsonl"
    count = common.write_jsonl(out_path, records)

    if dropped:
        common.log("dropped %d duplicate (target_id, disease_id) rows" % dropped)
    common.log("wrote %d target_evidence records to %s (from %s)"
               % (count, out_path, src_path))
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
