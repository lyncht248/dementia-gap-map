"""Guaranteed backbone papers.

The corpus is defined by the field query (see ``config.SEARCH_TERM``); these
PMIDs are simply *unioned in* so a handful of well-known dementia-genetics
papers are guaranteed present even if the query phrasing misses one. They do not
drive corpus construction.

If ``data/processed/translational-evidence/gwas_associations.jsonl`` exists (the
Track B handoff), its ``publication.pmid`` values are merged in the same way.
"""

from __future__ import annotations

import json

from .. import config

# (pmid, short description) — kept readable; only the PMID is used downstream.
MANUAL_SEEDS: list[tuple[str, str]] = [
    ("35379992", "Bellenguez 2022 AD/ADRD GWAS (Nat Genet)"),
    ("30820047", "Jansen 2019 AD GWAS meta-analysis (Nat Genet)"),
    ("30617256", "Kunkle 2019 IGAP AD GWAS (Nat Genet)"),
    ("34493870", "Wightman 2021 AD GWAS (Nat Genet)"),
    ("33589840", "Schwartzentruber 2021 AD fine-mapping (Nat Genet)"),
    ("23150908", "Lambert 2013 IGAP AD GWAS (Nat Genet)"),
    ("29777097", "Marioni 2018 GWAS by proxy AD (Transl Psychiatry)"),
    ("28714976", "Sims 2017 rare coding variants TREM2/ABI3/PLCG2 (Nat Genet)"),
    ("33432193", "Novikova 2021 microglial enhancers AD (Nat Commun)"),
    ("31932797", "Nott 2019 brain cell-type enhancers (Science)"),
]


def _seeds_from_track_b() -> list[str]:
    path = (
        config.REPO_ROOT
        / "data"
        / "processed"
        / "translational-evidence"
        / "gwas_associations.jsonl"
    )
    if not path.exists():
        return []
    pmids: set[str] = set()
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pub = rec.get("publication") or {}
            pmid = pub.get("pmid") or rec.get("pmid")
            if pmid:
                pmids.add(str(pmid))
    return sorted(pmids)


def get_seed_pmids() -> list[str]:
    """Return de-duplicated seed PMIDs (manual + Track B if available)."""
    pmids = {pmid for pmid, _ in MANUAL_SEEDS}
    pmids.update(_seeds_from_track_b())
    return sorted(pmids)
