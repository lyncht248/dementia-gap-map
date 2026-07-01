# Shared

Shared code and data contracts live here.

Use this area only for items that both tracks need. If a helper or data shape is only needed by one track, keep it inside that track's folder.

## Contents

- `schemas/` contains versioned JSON Schema contracts for processed outputs.
- `lib/` is reserved for shared helper code once repeated logic appears in both tracks.

The first integration target is `data/processed/shared/topic_evidence_links.jsonl`, which links Track A topic clusters to Track B genes, pathways, targets, and trials.
