#!/usr/bin/env python3
"""Build the "paper flywheel" view: individual papers mapped onto the
drug-discovery loop.

Where the existing flywheel (build_flywheel.py) shows every paper/gene/trial as
an anonymous dot on a hypothesis x stage board, this view answers a different
question — the one Nathan Skene posed: *what is the role of each individual
paper in the process of getting to a drug?* For a curated set of high-influence
papers it records, per paper:

  role        : its job in the loop
  inputs      : what it consumes
  outputs     : what it produces for the next turn
  method      : the technique it leverages ...
  fro         : ... and whether that technique is a candidate to be "FRO'd"
                (spun out to a Focused Research Organization to industrialise it)
  assumption  : the bet it makes about how dementia gets cured
  challenge   : how that bet is questioned (by data, by trials, or by other papers)

The loop has five steps (a flywheel, so Results feeds back to the Human anchor):

  Human anchor -> Cell state & mechanism -> Perturbation -> Clinical trial
               -> Results & feedback -> (back to Human anchor)

Only the *annotations* are curated here. Every hard number (relative citation
ratio, gene links, trial count, drugs, URL, title, journal, year) is pulled live
from the corpus so the card can never drift from the data behind the map.

Inputs (already in the repo):
  web/public/atlas/atlas_feed.json                  per-paper genes / trials / metrics
  data/processed/topic-dynamics/papers.jsonl        RCR fallback + abstracts

Output:
  web/public/atlas/paperflow.json

Run: python3 scripts/build_paperflow.py
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEED = ROOT / "web/public/atlas/atlas_feed.json"
PAPERS = ROOT / "data/processed/topic-dynamics/papers.jsonl"
OUT = ROOT / "web/public/atlas/paperflow.json"

# ---- the loop -------------------------------------------------------------
# Five steps arranged as a wheel; Results feeds back into the Human anchor.
STEPS = [
    {
        "id": "anchor",
        "label": "Human anchor",
        "question": "Which genes and loci carry causal human evidence?",
        "inputs": "cohorts, GWAS, sequencing",
        "outputs": "risk loci, prioritised genes",
    },
    {
        "id": "mechanism",
        "label": "Cell state & mechanism",
        "question": "Which cell state does the gene act through — and can we read it?",
        "inputs": "loci, single-cell / tissue data",
        "outputs": "cell-state maps, directional targets",
    },
    {
        "id": "perturbation",
        "label": "Perturbation",
        "question": "Does moving the target change pathology and function?",
        "inputs": "target, disease model",
        "outputs": "go / no-go signal",
    },
    {
        "id": "trials",
        "label": "Clinical trial",
        "question": "Does it work in people?",
        "inputs": "candidate drug, patients",
        "outputs": "efficacy, safety readouts",
    },
    {
        "id": "results",
        "label": "Results & feedback",
        "question": "What did the clinic teach us — which assumptions survive?",
        "inputs": "trial outcomes, biomarkers",
        "outputs": "re-ranked targets and theories",
    },
]

# ---- theory palette (colours match the 8-hypothesis map) ------------------
THEORY = {
    "synaptic_neuronal": {"label": "Synaptic / neuronal", "color": "#e87ba4"},
    "epigenetic_transcription": {"label": "Epigenetic / transcriptional", "color": "#008300"},
    "microglia_immune": {"label": "Microglia / immune", "color": "#4a3aa7"},
    "lipid_metabolism": {"label": "Lipid / APOE", "color": "#eda100"},
    "amyloid": {"label": "Amyloid", "color": "#e34948"},
}

# ---- the curated papers ---------------------------------------------------
# Keyed by pmid. `theory` is the mechanism the paper best represents (which can
# differ from its dominant-gene bin in the pipeline). Everything else is the
# annotation layer; hard numbers are merged in from the corpus below.
CURATED = {
    "35379992": dict(
        step="anchor", theory="synaptic_neuronal", short="Bellenguez 2022",
        role="The field's primary human anchor — the loci everything downstream is prioritised against.",
        inputs="111,326 clinical / proxy AD cases and 677,663 controls.",
        outputs="75 risk loci (42 new), 31 prioritised genes; amyloid, tau and microglia pathways.",
        method="Two-stage GWAS meta-analysis with gene prioritisation (L2G).",
        fro=True,
        fro_note="A standardised, biobank-scale locus → causal-gene → cell-type pipeline, run once and well for the whole field.",
        assumption="Common-variant loci point to causal, druggable biology, and more loci means better targets.",
        challenge="Links to 0 trials on this map — the anchor is enormous but inert until the downstream steps turn it into a target.",
    ),
    "30617256": dict(
        step="anchor", theory="lipid_metabolism", short="Kunkle 2019",
        role="The prior-generation genetic anchor (IGAP) — set the target list before Bellenguez extended it.",
        inputs="Stage-1 GWAS meta-analysis of clinically diagnosed AD plus replication.",
        outputs="New risk loci and functional pathways: APP metabolism, tau, immunity and lipid transport.",
        method="Genome-wide meta-analysis with pathway enrichment.",
        fro=True,
        fro_note="The same locus → gene pipeline — the recurring, industrialisable step.",
        assumption="Aggregating more cohorts steadily resolves the causal architecture.",
        challenge="Its top pathways (amyloid, lipid) feed straight into trials that have largely failed — see the Results node.",
    ),
    "28714976": dict(
        step="anchor", theory="microglia_immune", short="Sims 2017",
        role="Rare-variant anchor — pins causality more tightly than common variants can.",
        inputs="Rare coding-variant analysis across AD case-control cohorts.",
        outputs="Rare variants in PLCG2, ABI3 and TREM2 implicate microglial innate immunity causally.",
        method="Rare-variant burden testing.",
        fro=True,
        fro_note="Large-scale sequencing plus burden testing — standardisable infrastructure.",
        assumption="Rare high-impact variants reveal the causal cell type (microglia).",
        challenge="Names microglia as causal, but which microglial state to move is only answered at the mechanism node.",
    ),
    "31932797": dict(
        step="mechanism", theory="microglia_immune", short="Zhou 2020",
        role="Cell-state map — turns a risk gene (TREM2) into a disease-associated cell state.",
        inputs="Anchored genes (TREM2); 5xFAD mice and human AD brain.",
        outputs="TREM2-dependent DAM confirmed; a new Serpina3n+C4b+ reactive oligodendrocyte state; TREM2-independent human responses.",
        method="Single-nucleus RNA-seq (mouse and human).",
        fro=True,
        fro_note="Atlas-scale, standardised human snRNA / spatial plus a validated disease-model panel, so states are comparable across labs.",
        assumption="Disease-associated cell states are causal and actionable, and mouse states transfer to human.",
        challenge="Its own TREM2-independent human finding — plus work arguing DAM is partly reactive/downstream rather than driving.",
    ),
    "25728668": dict(
        step="mechanism", theory="microglia_immune", short="Wang 2015",
        role="Mechanistic model — shows how a risk gene changes microglial function.",
        inputs="TREM2 variants; an AD mouse model.",
        outputs="TREM2 lipid sensing sustains the microglial response around plaques.",
        method="Mouse genetics plus microglial functional assays.",
        fro=True,
        fro_note="Humanised microglia models with standardised functional readouts.",
        assumption="Sustaining the microglial response is protective.",
        challenge="Whether more microglial response preserves cognition is untested here — the phenotype gap.",
    ),
    "28628103": dict(
        step="mechanism", theory="epigenetic_transcription", short="Huang 2017 · PU.1",
        star=True,
        role="The shared node made concrete — a regulatory variant → transcription-factor dose → cell state → disease timing.",
        inputs="Survival GWAS (14,406 cases / 25,849 controls); LDSC over 220 cell types; myeloid eQTL.",
        outputs="rs1057233(G) lowers SPI1 / PU.1 in monocytes and macrophages and delays AD onset — a dose-tunable, directional hypothesis.",
        method="Survival GWAS + cell-type LDSC + eQTL + myeloid follow-up.",
        fro=True,
        fro_note="Systematic enhancer / TF-dosage mapping in human microglia (MPRA + CRISPRi tuning) — a clean FRO candidate.",
        assumption="A transcription factor's dose is causal and tunable, and nudging cell state changes disease trajectory.",
        challenge="The synaptic camp: regulating microglia need not preserve synapses or cognition; and the causal direction is unproven.",
    ),
    "33106633": dict(
        step="mechanism", theory="epigenetic_transcription", short="Corces 2020",
        role="Regulatory fine-map — assigns GWAS risk to specific variants, cell types and putative target genes.",
        inputs="Single-cell ATAC-seq of human brain; AD and PD GWAS loci.",
        outputs="Candidate causal variants at inherited risk loci mapped to cell-type-specific regulatory elements.",
        method="Single-cell epigenomics (scATAC) with variant-to-function.",
        fro=True,
        fro_note="Brain-wide, cell-type-resolved regulatory atlases — highly FRO-able.",
        assumption="Chromatin accessibility pinpoints the causal variant and cell type.",
        challenge="Accessibility is not causation; the perturbation node still has to prove the variant does something.",
    ),
    "28003153": dict(
        step="perturbation", theory="microglia_immune", short="MCC950 · NLRP3",
        role="Perturbation — an actual intervention experiment, not an association.",
        inputs="APP/PS1 mice; the NLRP3 inflammasome inhibitor MCC950.",
        outputs="Non-phlogistic amyloid-β clearance and improved cognition in the model.",
        method="Small-molecule inhibition in a disease model with a behavioural readout.",
        fro=False,
        fro_note="Target-specific medicinal chemistry — a discovery bet more than an infrastructure gap.",
        assumption="Dampening one innate-immune node clears pathology and rescues function.",
        challenge="Mouse cognitive rescue is a weak predictor of human benefit — the trial node is where this breaks.",
    ),
    "36835161": dict(
        step="trials", theory="amyloid", short="AD genetics overview 2023",
        role="The node that actually reaches the clinic — familial genetics to amyloid-targeting trials.",
        inputs="Familial (APP / PSEN1 / PSEN2) and sporadic AD genetics.",
        outputs="Links to real trials — γ-secretase modulators/inhibitors (tarenflurbil, semagacestat, avagacestat, begacestat).",
        method="Review / synthesis.",
        fro=False,
        fro_note="A synthesis, not a method to accelerate.",
        assumption="Familial APP/PSEN biology generalises to sporadic AD, and amyloid-lowering modifies disease.",
        challenge="Every drug it links to failed or was halted — the Results node closes the loop against this assumption.",
    ),
    "39295642": dict(
        step="results", theory="amyloid", short="Amyloid: still a hypothesis (2024)",
        role="Feedback — a paper whose job is to interrogate the field's central assumption.",
        inputs="The full arc of amyloid trials and biomarker data.",
        outputs="Verdict: the amyloid cascade remains a working hypothesis, 'no less but certainly no more.'",
        method="Critical review.",
        fro=False,
        fro_note="",
        assumption="The evidence base does not yet justify amyloid certainty.",
        challenge="This is the challenge — it down-weights the amyloid-lowering targets that dominate the Trials node.",
    ),
    "30982098": dict(
        step="results", theory="synaptic_neuronal", short="Amyloid → synaptic (2019)",
        role="Feedback that re-ranks the theories — argues genetics pushes the field from amyloid cascade toward synaptic failure.",
        inputs="The new AD genetic landscape.",
        outputs="A reframing: genetically driven synaptic failure as the organising hypothesis.",
        method="Review / synthesis.",
        fro=False,
        fro_note="",
        assumption="The locus set points at synaptic biology more than amyloid production.",
        challenge="Directly questions the amyloid node and reinforces the synaptic anchor — the loop updating itself.",
    ),
}

# ---- "do other papers question those assumptions?" edges ------------------
# Directed: a paper that challenges the assumption embodied by another node.
EDGES = [
    {"from": "39295642", "to": "36835161", "note": "Questions whether amyloid-lowering modifies disease."},
    {"from": "30982098", "to": "36835161", "note": "Argues genetics favours synaptic failure over the amyloid cascade."},
]


def load_feed() -> dict:
    feed = json.loads(FEED.read_text())
    return {p["paper_id"]: p for p in feed["papers"]}


def load_rcr_fallback() -> dict:
    out = {}
    for line in PAPERS.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out[d["paper_id"]] = ((d.get("metrics") or {}).get("relative_citation_ratio"))
    return out


def main() -> None:
    feed = load_feed()
    rcr_fallback = load_rcr_fallback()

    papers = []
    for pmid, ann in CURATED.items():
        pid = f"pmid:{pmid}"
        p = feed.get(pid)
        if p is None:
            raise SystemExit(f"paper {pid} not found in atlas_feed.json — corpus changed?")
        m = p.get("metrics") or {}
        rcr = m.get("relative_citation_ratio")
        if rcr is None:
            rcr = rcr_fallback.get(pid)
        trial_links = p.get("trialLinks") or []
        drugs = sorted({d.get("drug") for d in trial_links if d.get("drug")})
        rec = {
            "id": pid,
            "pmid": pmid,
            "step": ann["step"],
            "theory": ann["theory"],
            "star": ann.get("star", False),
            "short": ann["short"],
            "title": p.get("title"),
            "journal": p.get("journal"),
            "year": p.get("year"),
            "rcr": round(rcr, 1) if rcr is not None else None,
            "url": p.get("url") or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "genes": p.get("genes") or [],
            "n_trials": len(p.get("trials") or []),
            "drugs": drugs,
            "role": ann["role"],
            "inputs": ann["inputs"],
            "outputs": ann["outputs"],
            "method": ann["method"],
            "fro": ann["fro"],
            "fro_note": ann.get("fro_note", ""),
            "assumption": ann["assumption"],
            "challenge": ann["challenge"],
        }
        papers.append(rec)

    # order within-step by influence so the biggest anchors sit outermost
    papers.sort(key=lambda r: (-(r["rcr"] or 0)))

    ids = {p["id"] for p in papers}
    edges = []
    for e in EDGES:
        frm, to = f"pmid:{e['from']}", f"pmid:{e['to']}"
        if frm not in ids or to not in ids:
            raise SystemExit(f"edge {frm} -> {to} references a paper not in the curated set")
        edges.append({"from": frm, "to": to, "note": e["note"]})

    out = {
        "note": (
            "Individual papers mapped onto the drug-discovery flywheel: role, "
            "inputs/outputs, method (and whether it is FRO-able), the assumption "
            "each paper makes about how dementia gets cured, and how that "
            "assumption is challenged. Built by scripts/build_paperflow.py."
        ),
        "steps": STEPS,
        "theories": THEORY,
        "papers": papers,
        "edges": edges,
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(papers)} papers, {len(EDGES)} challenge edges)")


if __name__ == "__main__":
    main()
