#!/usr/bin/env python3
"""Fetch gene -> drug -> trial(NCT) links from Open Targets — the authoritative
target-to-clinic bridge the local data was missing.

For every gene node in the flywheel's Genetics stage (the mechanism genes with
human genetic evidence), query Open Targets `drugAndClinicalCandidates`, pull the
trial NCT ids from each drug's clinical reports, and keep the ones that are AD/
dementia trials (intersection with trials.parquet). This gives real, per-target
gene -> trial lineage edges (hover a gene/paper and see the trials it led to;
hover a trial and walk back to its target gene and the research behind it).

Output:  data/interim/flywheel/ot_gene_trials.json
         { "<SYMBOL>": [ {"nct","phase","status","drug"}, ... ], ... }

Then re-run scripts/build_flywheel.py to merge these edges + trial nodes.
Run: python3 scripts/fetch_ot_trials.py
"""
from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GENES = ROOT / "web/public/data/parquet/genes.parquet"
TRIALS = ROOT / "web/public/data/parquet/trials.parquet"
OUT = ROOT / "data/interim/flywheel/ot_gene_trials.json"
OT = "https://api.platform.opentargets.org/api/v4/graphql"
HYP8 = {"amyloid", "tau", "lipid_metabolism", "microglia_immune",
        "endocytosis_endosomal", "synaptic_neuronal", "vascular", "epigenetic_transcription"}

QUERY = ('{ target(ensemblId:"%s"){ drugAndClinicalCandidates{ rows{ '
         'drug{name} clinicalReports{ id trialPhase trialOverallStatus } } } } }')


def ot(ensembl: str) -> list[dict]:
    body = json.dumps({"query": QUERY % ensembl})
    for attempt in range(4):
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "40", "-X", "POST", OT,
             "-H", "Content-Type: application/json", "-d", body],
            capture_output=True, text=True)
        try:
            d = json.loads(r.stdout)
            t = (d.get("data") or {}).get("target")
            return (t or {}).get("drugAndClinicalCandidates", {}).get("rows", []) or []
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    print(f"  ! query failed: {ensembl}")
    return []


def main() -> None:
    genes = pd.read_parquet(GENES)
    sel = genes[genes.pathway_group.isin(HYP8)
                & ((genes.genetic_support.fillna(0) >= 0.5) | (genes.gwas_association_count.fillna(0) > 0))]
    sel = sel[sel.gene_id.astype(str).str.startswith("ENSG")]
    ad_ncts = set(pd.read_parquet(TRIALS)["nct_id"].dropna().astype(str))

    out: dict[str, list[dict]] = {}
    n_edges = 0
    rows = list(sel.itertuples())
    for i, g in enumerate(rows):
        links = {}
        for row in ot(g.gene_id):
            drug = (row.get("drug") or {}).get("name")
            for cr in (row.get("clinicalReports") or []):
                cid = str(cr.get("id") or "")
                if cid.upper().startswith("NCT"):
                    nct = cid.upper()
                    if nct in ad_ncts and nct not in links:
                        links[nct] = {"nct": nct, "phase": cr.get("trialPhase"),
                                      "status": cr.get("trialOverallStatus"), "drug": drug}
        if links:
            out[g.symbol] = list(links.values())
            n_edges += len(links)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(rows)} genes · {len(out)} with AD trials · {n_edges} gene->trial links")
        time.sleep(0.2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=0))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    print(f"  {len(rows)} genes queried · {len(out)} link to >=1 AD trial · {n_edges} gene->trial links")


if __name__ == "__main__":
    main()
