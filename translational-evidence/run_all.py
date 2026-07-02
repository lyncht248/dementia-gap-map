#!/usr/bin/env python3
"""One-command orchestrator for Track B (translational evidence).

Runs the whole pipeline in dependency order via subprocess, printing a clear
header before each step. Standard-library only (Python 3.9): it shells out to
``python3 <script>`` with ``subprocess.run(..., check=True)`` so any non-zero
exit aborts the run immediately (no silent failures).

Order:
    1. ingest/gwas_catalog.py
    2. ingest/clinicaltrials.py
    3. ingest/open_targets.py
    4. ingest/open_targets_l2g.py
    5. normalize/gwas_catalog.py
    6. normalize/clinicaltrials.py
    7. normalize/open_targets.py
    8. normalize/open_targets_l2g.py
    9. normalize/merge_genes.py             (union GWAS genes + OT targets)
    10. map/gene_pathway_build.py            (API capture -> gene_pathway.csv)
    11. map/intervention_mechanism_build.py (API capture -> intervention_mechanism.csv)
    12. map/pathways.py
    13. score/scores.py
    14. validate.py

Usage:
    python3 translational-evidence/run_all.py            # full pipeline
    python3 translational-evidence/run_all.py --skip-ingest
        # skip the three ingest steps and run normalize + map + score +
        # validate against the already-cached raw API responses.

Notes:
- Script paths are resolved relative to THIS file, so it works from any CWD.
- The raw-response cache is reused automatically; set TE_REFRESH=1 in the
  environment to force fresh API calls (see common.get_json / common.post_json).
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Directory that contains this orchestrator (the translational-evidence/ dir).
TE_DIR = Path(__file__).resolve().parent

# Steps that hit the network / build the raw cache. Skippable with --skip-ingest.
INGEST_STEPS = [
    "ingest/gwas_catalog.py",
    "ingest/clinicaltrials.py",
    "ingest/open_targets.py",
    "ingest/open_targets_l2g.py",
]

# Everything downstream of the raw cache. Always run.
DOWNSTREAM_STEPS = [
    "normalize/gwas_catalog.py",
    "normalize/clinicaltrials.py",
    "normalize/open_targets.py",
    "normalize/open_targets_l2g.py",
    # Union the GWAS-derived gene list with Open Targets associated targets so
    # known/Mendelian AD genes (PSEN1/PSEN2/GRN, ...) that GWAS misses are present
    # and queryable BEFORE scoring enriches them. Must run after both
    # normalize/gwas_catalog.py (builds genes.jsonl) and normalize/open_targets.py
    # (builds target_evidence.jsonl), and before score/scores.py.
    "normalize/merge_genes.py",
    # API-derived, multi-valued capture -> thin projection CSVs. These call the
    # APIs (mygene/Reactome/OT) via the cached data/raw layer, so they respect
    # --skip-ingest / TE_REFRESH exactly like the ingest steps. gene_pathway_build
    # regenerates gene_pathway.csv; intervention_mechanism_build regenerates
    # intervention_mechanism.csv from the now-normalized trials.
    "map/gene_pathway_build.py",
    "map/intervention_mechanism_build.py",
    # Re-normalize trials AFTER the mechanism map is rebuilt, so trials.jsonl /
    # pathways / scores use the current run's mechanism map (not the prior run's).
    # Fixes the one-command ordering: normalize -> build map -> re-normalize.
    "normalize/clinicaltrials.py",
    "map/pathways.py",
    "score/scores.py",
    "validate.py",
]


def _print_header(index, total, rel_path, skip_ingest):
    """Print a visible banner before running a step."""
    mode = "skip-ingest" if skip_ingest else "full"
    bar = "=" * 70
    print(bar, flush=True)
    print(
        "[run_all %s] STEP %d/%d: %s" % (mode, index, total, rel_path),
        flush=True,
    )
    print(bar, flush=True)


def _run_step(rel_path):
    """Run one pipeline script, raising CalledProcessError on failure."""
    script_path = TE_DIR / rel_path
    if not script_path.exists():
        raise FileNotFoundError("pipeline script not found: %s" % script_path)
    # Use the same interpreter that is running this orchestrator.
    subprocess.run([sys.executable, str(script_path)], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the translational-evidence pipeline end to end."
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip the three ingest steps and run normalize+map+score+validate "
             "against the cached raw API responses.",
    )
    args = parser.parse_args(argv)

    if args.skip_ingest:
        steps = list(DOWNSTREAM_STEPS)
    else:
        steps = INGEST_STEPS + DOWNSTREAM_STEPS

    total = len(steps)
    print(
        "[run_all] running %d step(s) (%s); scripts resolved under %s"
        % (total, "skip-ingest" if args.skip_ingest else "full", TE_DIR),
        flush=True,
    )

    for i, rel_path in enumerate(steps, start=1):
        _print_header(i, total, rel_path, args.skip_ingest)
        try:
            _run_step(rel_path)
        except subprocess.CalledProcessError as err:
            print(
                "\n[run_all] ABORT: step %d/%d '%s' failed with exit code %d."
                % (i, total, rel_path, err.returncode),
                file=sys.stderr,
                flush=True,
            )
            return err.returncode
        except FileNotFoundError as err:
            print(
                "\n[run_all] ABORT: %s" % err,
                file=sys.stderr,
                flush=True,
            )
            return 1

    print("=" * 70, flush=True)
    print("[run_all] DONE: all %d step(s) completed successfully." % total,
          flush=True)
    print("=" * 70, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
