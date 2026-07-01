"""Build pathways.jsonl from the curated gene -> pathway_group mapping.

Reads the source-controlled ``translational-evidence/map/gene_pathway.csv``,
groups genes by ``pathway_group``, and writes one pathway record per group to
``data/processed/translational-evidence/pathways.jsonl`` conforming to
``shared/schemas/pathway.schema.json``.

No network calls. Standard library only.

Run:
    python3 translational-evidence/map/pathways.py
"""

import csv
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# Curated CSV lives next to this script and IS source-controlled.
GENE_PATHWAY_CSV = common.TE_DIR / "map" / "gene_pathway.csv"

OUTPUT_PATH = common.PROCESSED_DIR / "pathways.jsonl"

# Human-readable labels for each pathway_group in the controlled vocabulary.
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
}


def load_rows(csv_path):
    """Read the curated CSV into a list of dict rows, skipping blank lines."""
    rows = []
    with pathlib.Path(csv_path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"gene_symbol", "pathway_group", "notes"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(
                "%s missing required columns: %s"
                % (csv_path, sorted(missing))
            )
        for raw in reader:
            symbol = (raw.get("gene_symbol") or "").strip()
            group = (raw.get("pathway_group") or "").strip()
            notes = (raw.get("notes") or "").strip()
            if not symbol:
                continue
            if not group:
                raise RuntimeError(
                    "gene '%s' has no pathway_group in %s"
                    % (symbol, csv_path)
                )
            if group not in GROUP_LABELS:
                raise RuntimeError(
                    "gene '%s' uses unknown pathway_group '%s' "
                    "(not in controlled vocabulary)" % (symbol, group)
                )
            rows.append(
                {"gene_symbol": symbol, "pathway_group": group, "notes": notes}
            )
    return rows


def build_records(rows):
    """Group rows by pathway_group and build one pathway record per group."""
    groups = {}
    for row in rows:
        groups.setdefault(row["pathway_group"], []).append(row)

    records = []
    for group in sorted(groups):
        members = groups[group]
        gene_ids = sorted({m["gene_symbol"] for m in members})
        notes_by_gene = {}
        for m in members:
            if m["notes"]:
                notes_by_gene[m["gene_symbol"]] = m["notes"]
        record = {
            "pathway_id": "curated:" + common.slug(group),
            "label": GROUP_LABELS[group],
            "source": "curated:gene_pathway.csv",
            "gene_ids": gene_ids,
            "mechanism_group": group,
            "gene_count": len(gene_ids),
            "notes_by_gene": notes_by_gene,
        }
        records.append(record)
    return records


def main():
    rows = load_rows(GENE_PATHWAY_CSV)
    common.log("loaded %d curated gene->pathway rows from %s"
               % (len(rows), GENE_PATHWAY_CSV))

    records = build_records(rows)
    count = common.write_jsonl(OUTPUT_PATH, records)
    common.log("wrote %d pathway records to %s" % (count, OUTPUT_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
