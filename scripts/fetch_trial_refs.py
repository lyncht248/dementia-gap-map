#!/usr/bin/env python3
"""Fetch each flywheel trial's cited publications from ClinicalTrials.gov and keep
the ones that are in our corpus — the "trial -> precursor research" lineage edges.

Reads the trial NCT ids from web/public/atlas/flywheel.json, queries the
ClinicalTrials.gov API v2 `referencesModule` in batches, extracts each study's
referenced PMIDs, intersects them with the corpus (atlas.ids), and writes:

  data/interim/flywheel/trial_refs.json   { "<NCT>": ["pmid:123", ...], ... }

Then re-run scripts/build_flywheel.py to merge these into the edge set.

Uses curl (already works through the agent proxy). Run: python3 scripts/fetch_trial_refs.py
"""
from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLY = ROOT / "web/public/atlas/flywheel.json"
ATLAS = ROOT / "web/public/atlas/atlas.json"
OUT = ROOT / "data/interim/flywheel/trial_refs.json"
API = "https://clinicaltrials.gov/api/v2/studies"
BATCH = 40


def fetch_batch(ncts: list[str]) -> list[dict]:
    url = (f"{API}?filter.ids={','.join(ncts)}"
           "&fields=protocolSection.identificationModule.nctId,"
           "protocolSection.referencesModule.references.pmid"
           f"&pageSize={len(ncts)}")
    for attempt in range(4):
        r = subprocess.run(["curl", "-sS", "--max-time", "40", url],
                           capture_output=True, text=True)
        try:
            return json.loads(r.stdout).get("studies", [])
        except Exception:
            time.sleep(2 * (attempt + 1))
    print(f"  ! batch failed after retries ({ncts[0]}…)")
    return []


def main() -> None:
    fly = json.loads(FLY.read_text())
    ncts = sorted({n["id"][2:] for n in fly["nodes"]
                   if n["kind"] == "trial" and n["stage"] == "trials"})
    corpus = set(json.loads(ATLAS.read_text())["ids"])  # pmid:NNN

    refs: dict[str, list[str]] = {}
    total_pmids = 0
    in_corpus = 0
    for i in range(0, len(ncts), BATCH):
        batch = ncts[i:i + BATCH]
        for s in fetch_batch(batch):
            ps = s.get("protocolSection", {})
            nct = ps.get("identificationModule", {}).get("nctId")
            pmids = [r.get("pmid") for r in ps.get("referencesModule", {}).get("references", [])
                     if r.get("pmid")]
            total_pmids += len(pmids)
            keep = [f"pmid:{p}" for p in pmids if f"pmid:{p}" in corpus]
            in_corpus += len(keep)
            if nct and keep:
                refs[nct] = sorted(set(keep))
        print(f"  fetched {min(i + BATCH, len(ncts))}/{len(ncts)} trials · "
              f"{in_corpus} corpus links so far")
        time.sleep(0.3)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(refs, indent=0))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    print(f"  {len(ncts)} trials queried · {total_pmids} total cited PMIDs · "
          f"{in_corpus} intersect the corpus · {len(refs)} trials with >=1 corpus link")


if __name__ == "__main__":
    main()
