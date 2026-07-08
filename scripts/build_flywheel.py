#!/usr/bin/env python3
"""Build the "flywheel" (development-pipeline) view data for the map.

Reframes the 8 mechanistic Alzheimer's-cure hypotheses as a 5-stage pipeline —
Research -> Genetics -> Models -> Trials -> Results — and emits, per hypothesis,
the *typed dots* that populate each stage plus the *lineage edges* that connect a
dot to its neighbours in adjacent stages (so hovering a paper lights up its gene
-> model -> trial chain, and hovering a trial lights up its precursor research).

Node kinds (one dot each):
  research : a corpus paper (multi-membership: appears under every hypothesis
             whose genes it studies)                         id p:<pmid>
  genetics : a gene with human genetic (GWAS) evidence       id g:<SYMBOL>
  models   : the subset of those genes that also has         id m:<SYMBOL>
             functional / animal-model validation
  trials   : a clinical trial tagged to the mechanism        id t:<NCT>
  results  : the subset of those trials with posted results  id r:<NCT>

Edges (undirected pairs of node ids; the stage x-order implies direction):
  p:<pmid> — g:<SYMBOL>   paper studies gene
  g:<SYMBOL> — m:<SYMBOL>  gene advanced to model validation
  (m|g):<SYMBOL> — t:<NCT> a trial's drug targets the gene
  t:<NCT> — r:<NCT>        trial has results
  t:<NCT> — p:<pmid>       trial cites a corpus paper  (from CT.gov, merged in by
                           add_flywheel_refs.py; absent until that runs)

Inputs (all already in the repo):
  web/public/atlas/atlas.json                                  hypotheses + ids
  web/public/atlas/atlas_feed.json                             papers (genes, pathways)
  web/public/data/parquet/genes.parquet                        genetic/functional support
  web/public/data/parquet/target_evidence.parquet              OT animal-model evidence
  web/public/data/parquet/trials.parquet                       mechanism-tagged trials
  web/public/data/parquet/drugs.parquet                        drug -> target genes
  data/processed/shared/atlas_evidence_rollup.jsonl            trial -> target genes
  data/interim/flywheel/trial_refs.json  (optional)            NCT -> [corpus pmids]

Output:
  web/public/atlas/flywheel.json

Run: python3 scripts/build_flywheel.py
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ATLAS = ROOT / "web/public/atlas/atlas.json"
FEED = ROOT / "web/public/atlas/atlas_feed.json"
GENES = ROOT / "web/public/data/parquet/genes.parquet"
TE = ROOT / "web/public/data/parquet/target_evidence.parquet"
TRIALS = ROOT / "web/public/data/parquet/trials.parquet"
DRUGS = ROOT / "web/public/data/parquet/drugs.parquet"
ROLLUP = ROOT / "data/processed/shared/atlas_evidence_rollup.jsonl"
REFS = ROOT / "data/interim/flywheel/trial_refs.json"
OT_TRIALS = ROOT / "data/interim/flywheel/ot_gene_trials.json"  # gene -> AD trials (Open Targets)
OUT = ROOT / "web/public/atlas/flywheel.json"

# pathway mechanism_group -> trial mechanism_group (from pathways.jsonl crosswalk)
TRIAL_MECH = {
    "amyloid": "amyloid",
    "tau": "tau",
    "microglia_immune": "inflammation_microglia",
    "lipid_metabolism": "lipid_metabolism",
    "vascular": "vascular",
    "synaptic_neuronal": "synaptic_neuroprotection",
    "endocytosis_endosomal": None,
    "epigenetic_transcription": None,
}
# Curated mechanism overrides. The automated GO/Reactome IDF assignment
# (gene_pathway.csv) puts a handful of marquee targets in the wrong mechanistic
# home — e.g. APP (the amyloid precursor protein) and the secretases land in
# synaptic/endocytosis rather than amyloid, and MAPT (tau protein) lands in
# synaptic. That makes an amyloid trial appear to "depend on" a synaptic gene.
# These are the unambiguous, textbook homes.
PATHWAY_OVERRIDE = {
    "APP": "amyloid", "PSEN1": "amyloid", "BACE1": "amyloid", "BACE2": "amyloid",
    "MAPT": "tau", "GSK3B": "tau",
}

# a gene "counts" as genetically supported at this bar, and model-validated at this
GEN_MIN = 0.5           # genetic_support, OR any GWAS association (below)
MODEL_FUNC_MIN = 0.5    # functional_support, OR any animal-model evidence


def as_list(v):
    """Coerce a parquet cell (which may be a numpy array, list, None, or scalar)
    to a plain list — numpy arrays have ambiguous truthiness."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if hasattr(v, "tolist"):
        return list(v.tolist())
    return [v]


def phase_of(phases) -> str:
    s = " ".join(str(x) for x in as_list(phases)).upper().replace(" ", "")
    for p in ("PHASE4", "PHASE3", "PHASE2", "PHASE1"):
        if p in s:
            return "P" + p[-1]
    return "NA"


def main() -> None:
    atlas = json.loads(ATLAS.read_text())
    feed = json.loads(FEED.read_text())
    hyps = atlas["hypotheses"]
    hyp_ids = {h["id"] for h in hyps}
    corpus = set(atlas["ids"])  # pmid:NNN

    genes = pd.read_parquet(GENES)
    te = pd.read_parquet(TE)
    trials = pd.read_parquet(TRIALS)
    drugs = pd.read_parquet(DRUGS)

    # ---- per-gene animal-model evidence (max OT animal score over its rows) ----
    animal = te.groupby("gene_id")["ot_animal_model"].max()
    genes = genes.assign(animal=genes["gene_id"].map(animal))

    # full gene -> mechanism map (all genes, override applied) — drives both the
    # gene nodes and each paper's Research membership, so the overrides propagate
    # everywhere (a paper studying APP counts toward amyloid, not synaptic).
    sym_hyp_all = {}
    for _, g in genes.iterrows():
        sym = g["symbol"]
        h = PATHWAY_OVERRIDE.get(sym, g["pathway_group"])
        if h in hyp_ids:
            sym_hyp_all[sym] = h

    # ---- gene nodes: genetics (has genetic evidence) and models (also validated)
    gene_hyp = {}       # SYMBOL -> hyp id
    genetics_genes = defaultdict(list)  # hyp -> [SYMBOL...]
    models_genes = defaultdict(list)
    is_model = {}       # SYMBOL -> bool
    for _, g in genes.iterrows():
        sym = g["symbol"]
        h = sym_hyp_all.get(sym)
        if h is None:
            continue
        gs = float(g["genetic_support"] or 0)
        gw = float(g["gwas_association_count"] or 0)
        if not (gs >= GEN_MIN or gw > 0):
            continue
        gene_hyp[sym] = h
        genetics_genes[h].append(sym)
        fs = float(g["functional_support"] or 0)
        am = float(g["animal"] or 0)
        model = fs >= MODEL_FUNC_MIN or am > 0
        is_model[sym] = model
        if model:
            models_genes[h].append(sym)

    # ---- drug -> target genes (to link trials to genes) ------------------------
    drug_targets = {}   # UPPER drug name -> set(SYMBOL)
    for _, d in drugs.iterrows():
        tg = set(t for t in as_list(d["mechanism_targets"]) if t)
        if not tg:
            continue
        for nm in (d["name"], d["ot_name"]):
            if isinstance(nm, str) and nm:
                drug_targets[nm.upper()] = drug_targets.get(nm.upper(), set()) | tg

    # trial nct -> target genes, from the rollup (drug_target linked)
    rollup_targets = defaultdict(set)
    for line in ROLLUP.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        for t in (r.get("trials") or []):
            nct = t.get("nct_id")
            if nct:
                rollup_targets[nct] |= set(t.get("target_genes") or [])

    # ---- trial + result nodes per hypothesis, with gene links ------------------
    trial_refs = json.loads(REFS.read_text()) if REFS.exists() else {}

    nodes = []   # {id, kind, stage, hyps:[...], label, ...}
    edges = set()
    seen_node = {}

    def add_node(nid, **kw):
        if nid in seen_node:
            # merge hyps (papers are multi-membership)
            seen_node[nid]["hyps"] = sorted(set(seen_node[nid]["hyps"]) | set(kw.get("hyps", [])))
            return
        seen_node[nid] = {"id": nid, **kw}
        nodes.append(seen_node[nid])

    # genetics + models gene nodes
    for h in hyp_ids:
        for sym in genetics_genes[h]:
            add_node(f"g:{sym}", kind="gene", stage="genetics", hyps=[h], label=sym)
            if is_model.get(sym):
                add_node(f"m:{sym}", kind="gene", stage="models", hyps=[h], label=sym)
                edges.add((f"g:{sym}", f"m:{sym}"))

    # research paper nodes (multi-membership) + paper->gene edges. Membership is
    # derived from the paper's own genes via the override-aware map, so a paper is
    # in every mechanism it actually studies (and the overrides apply here too).
    paper_meta = {}
    for p in feed["papers"]:
        pid = p["paper_id"]
        pws = sorted({sym_hyp_all[s] for s in (p.get("genes") or []) if s in sym_hyp_all})
        if not pws:
            continue
        paper_meta[pid] = p
        add_node(f"p:{pid}", kind="paper", stage="research", hyps=pws,
                 label=p["title"], year=p.get("year"), url=p.get("url"))
        for sym in (p.get("genes") or []):
            if sym in gene_hyp:
                edges.add((f"p:{pid}", f"g:{sym}"))

    # trial + result nodes, from mechanism-tagged trials.parquet
    hyp_by_trialmech = {v: k for k, v in TRIAL_MECH.items() if v}
    trial_count = defaultdict(int)
    result_count = defaultdict(int)
    for _, t in trials.iterrows():
        tm = t["mechanism_group"]
        h = hyp_by_trialmech.get(tm)
        if not h:
            continue
        nct = t["nct_id"]
        if not isinstance(nct, str):
            continue
        ph = phase_of(t["phases"])
        hasres = bool(t["has_results"])
        drugs_iv = [str(x) for x in as_list(t["interventions"]) if x]
        # gene targets: rollup + drug-name match, keep those in this hypothesis
        tgts = set(rollup_targets.get(nct, set()))
        for iv in drugs_iv:
            tgts |= drug_targets.get(iv.upper(), set())
        tgts = {g for g in tgts if gene_hyp.get(g) == h}
        add_node(f"t:{nct}", kind="trial", stage="trials", hyps=[h], label=str(t["brief_title"]),
                 phase=ph, has_results=hasres, url=f"https://clinicaltrials.gov/study/{nct}",
                 drugs=drugs_iv[:4], targets=sorted(tgts))
        trial_count[h] += 1
        # gene -> trial edge (from the gene's furthest stage)
        for g in tgts:
            src = f"m:{g}" if is_model.get(g) else f"g:{g}"
            edges.add((src, f"t:{nct}"))
        # trial -> precursor corpus papers (CT.gov refs)
        for pmid in trial_refs.get(nct, []):
            pkey = pmid if pmid.startswith("pmid:") else f"pmid:{pmid}"
            if pkey in corpus and f"p:{pkey}" in seen_node:
                edges.add((f"t:{nct}", f"p:{pkey}"))
        if hasres:
            add_node(f"r:{nct}", kind="trial", stage="results", hyps=[h], label=str(t["brief_title"]),
                     phase=ph, url=f"https://clinicaltrials.gov/study/{nct}")
            edges.add((f"t:{nct}", f"r:{nct}"))
            result_count[h] += 1

    # ---- Open Targets gene -> trial lineage edges ------------------------------
    # OT gives the authoritative target -> drug -> trial link the local data lacks.
    # We only add edges to trials we already show (mechanism-tagged) and genes we
    # already show, so the counts stay Nathan's while the backward lineage lights up.
    ot_links = json.loads(OT_TRIALS.read_text()) if OT_TRIALS.exists() else {}
    ot_linked_trials = set()
    for sym, links in ot_links.items():
        gnode = f"m:{sym}" if is_model.get(sym) else f"g:{sym}"
        if gnode not in seen_node:
            continue
        for l in links:
            tnode = f"t:{l['nct']}"
            if tnode in seen_node:
                edges.add((gnode, tnode))
                seen_node[tnode].setdefault("targets", [])
                if sym not in seen_node[tnode]["targets"]:
                    seen_node[tnode]["targets"] = sorted(set(seen_node[tnode]["targets"]) | {sym})
                ot_linked_trials.add(tnode)

    # ---- hypotheses ranked least-gap first (most reinforced at top) ------------
    ranked = sorted(hyps, key=lambda h: (h["translation_gap"] or 0, -(h["trial_count"] or 0)))
    stages = ["research", "genetics", "models", "trials", "results"]
    stage_labels = {
        "research": "Research", "genetics": "Genetics", "models": "Models",
        "trials": "Trials", "results": "Results",
    }

    out = {
        "note": "Development-pipeline (flywheel) view: 8 hypotheses x 5 stages "
                "(Research/Genetics/Models/Trials/Results) with typed dots and "
                "lineage edges. Built by scripts/build_flywheel.py.",
        "stages": [{"id": s, "label": stage_labels[s]} for s in stages],
        "hypotheses": [{
            "id": h["id"], "label": h["label"], "short": h["short"], "color": h["color"],
            "statement": h["statement"], "translation_gap": h["translation_gap"],
            "combined_support": h["combined_support"], "clinical_translation": h["clinical_translation"],
        } for h in ranked],
        "nodes": nodes,
        "edges": sorted([list(e) for e in edges]),
        "has_refs": bool(trial_refs),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))

    # ---- report ----------------------------------------------------------------
    ref_edges = sum(1 for a, b in edges if a.startswith("t:") and b.startswith("p:"))
    gt_edges = sum(1 for a, b in edges if (a[0] in "gm" and b.startswith("t:")) or (b[0] in "gm" and a.startswith("t:")))
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size/1e6:.2f} MB)")
    print(f"  {len(nodes)} nodes, {len(edges)} edges")
    print(f"  lineage: gene->trial {gt_edges} edges ({len(ot_linked_trials)} trials with backward lineage), "
          f"trial->paper refs {ref_edges}")
    print(f"\n  {'hypothesis':16}{'research':>9}{'genetics':>9}{'models':>8}{'trials':>8}{'results':>8}")
    for h in ranked:
        hid = h["id"]
        r = sum(1 for n in nodes if n["kind"] == "paper" and hid in n["hyps"])
        print(f"  {h['short']:16}{r:>9}{len(genetics_genes[hid]):>9}"
              f"{len(models_genes[hid]):>8}{trial_count[hid]:>8}{result_count[hid]:>8}")


if __name__ == "__main__":
    main()
