# Data Directory

Generated data is intentionally ignored by git. Keep only small documentation, schemas, and placeholder files under version control.

## Layout

```text
raw/
  shared/                     # Inputs reused by both tracks
  topic-dynamics/             # Track A source downloads
  translational-evidence/     # Track B source downloads

interim/
  topic-dynamics/             # Track A working files
  translational-evidence/     # Track B working files

processed/
  shared/                     # Cross-track handoff files
  topic-dynamics/             # Stable Track A outputs
  translational-evidence/     # Stable Track B outputs

exports/
  visual/                     # Future visual layer inputs
```

## Format Preference

Use newline-delimited JSON (`.jsonl`) for early prototypes because it is easy to diff, stream, and inspect. Parquet or SQLite can be added later when files become large or query-heavy.

## Naming Rules

- Raw API responses should include the source and date, for example `gwas_catalog_alzheimer_studies_2026-07-01.json`.
- Processed files should use stable names, for example `papers.jsonl` or `trials.jsonl`.
- Shared processed files should match a schema in `shared/schemas`.
