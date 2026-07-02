#!/usr/bin/env python3
"""Build the FULL Track B translational evidence graph (nodes + edges).

This is the data layer behind the standalone evidence-graph explorer. It is a
deliberately *un-capped* export: every variant, gene, pathway, real drug, trial,
disease group and Track A topic becomes a node, and every supported relationship
becomes an edge. Legibility is the job of the in-browser filters, not of this
exporter, so nothing is aggressively pruned here.

Inputs (all standard-library-only, read via ``common``):
  data/processed/translational-evidence/genes.jsonl
  data/processed/translational-evidence/gwas_associations.jsonl
  data/processed/translational-evidence/pathways.jsonl
  data/processed/translational-evidence/trials.jsonl
  data/processed/translational-evidence/functional_links.jsonl
  data/processed/translational-evidence/target_evidence.jsonl   (disease scores)
  translational-evidence/map/gene_pathway.csv
  translational-evidence/map/intervention_mechanism.csv
  data/processed/shared/topic_evidence_rollup.jsonl
  data/processed/shared/topic_evidence_links.jsonl

Outputs (gitignored build products under data/exports/graph/):
  nodes.jsonl           -> conforms to shared/schemas/evidence_node.schema.json
  edges.jsonl           -> conforms to shared/schemas/evidence_edge.schema.json
  graph_manifest.json   -> counts by node_type + edge_type and totals

Node ids follow a stable "<type>:<key>" scheme:
  variant:rs429358   gene:ENSG00000130203   pathway:amyloid
  drug:lecanemab     trial:NCT02094729      disease:alzheimer   topic:002

Every node carries deterministic x/y layout coordinates (layered columns along
the evidence chain, spread by score, with index-based jitter and NO randomness)
so the HTML template needs no client-side layout. Every edge carries a score,
an evidence label and provenance, and dangling edges (an endpoint that is not a
node) are dropped.

Run:
  python3 translational-evidence/exports/build_evidence_graph.py
"""

import csv
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MAP_DIR = common.TE_DIR / "map"
GENE_PATHWAY_CSV = MAP_DIR / "gene_pathway.csv"
INTERVENTION_MECHANISM_CSV = MAP_DIR / "intervention_mechanism.csv"

OUT_DIR = common.REPO_ROOT / "data" / "exports" / "graph"
NODES_PATH = OUT_DIR / "nodes.jsonl"
EDGES_PATH = OUT_DIR / "edges.jsonl"
MANIFEST_PATH = OUT_DIR / "graph_manifest.json"

ENTITY_METRICS_PATH = common.PROCESSED_DIR / "entity_metrics.jsonl"
# Rich per-drug Open Targets MoA capture (targets[] per drug) for drug_gene
# edges. Written by map/intervention_mechanism_build.py.
DRUG_MECHANISM_API_PATH = common.PROCESSED_DIR / "drug_mechanism_api.jsonl"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Interventions that are not real drugs (case-insensitive substring match).
_PLACEBO_TERMS = (
    "placebo",
    "saline",
    "standard of care",
    "usual care",
)
_DRUG_TYPES = {"DRUG", "BIOLOGICAL"}

# Human-readable disease-group labels for disease nodes.
_DISEASE_LABELS = {
    "alzheimer": "Alzheimer disease",
    "vascular_dementia": "Vascular dementia",
    "frontotemporal_dementia": "Frontotemporal dementia",
    "lewy_body_dementia": "Lewy body dementia",
    "mixed_dementia": "Mixed dementia",
    "dementia_unspecified": "Dementia (unspecified)",
    "other": "Other neurodegenerative",
}

# Evidence-chain columns for the layered layout. disease sits to the left of the
# chain; topic sits as an offset side column above the genes.
_COLUMN_X = {
    "disease": -1.0,
    "variant": 0.0,
    "gene": 1.0,
    "topic": 1.5,
    "pathway": 2.0,
    "drug": 3.0,
    "trial": 4.0,
}
_COORD_MAX = 1000.0

# Cap for normalising -log10(p) GWAS edge scores into [0, 1] (matches the
# neglog10p_cap used for genetic_support in the gene pipeline).
_NEGLOG10P_CAP = 30.0

# Truncation length for trial labels.
_TRIAL_LABEL_MAX = 90


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _truncate(text, limit):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _is_real_drug_name(name):
    """True if an intervention name looks like a real drug (not placebo/etc)."""
    if not name:
        return False
    low = str(name).lower()
    for term in _PLACEBO_TERMS:
        if term in low:
            return False
    return True


def _first_phase(phases):
    if not phases:
        return None
    return phases[0]


# ---------------------------------------------------------------------------
# Map loaders
# ---------------------------------------------------------------------------

def load_gene_pathway_map():
    """gene_symbol -> sorted list of pathway_group (mechanism) values."""
    out = {}
    with GENE_PATHWAY_CSV.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("gene_symbol") or "").strip()
            pg = (row.get("pathway_group") or "").strip()
            if not sym or not pg:
                continue
            out.setdefault(sym, set()).add(pg)
    return {sym: sorted(groups) for sym, groups in out.items()}


def load_intervention_mechanism_map():
    """Ordered list of (keyword_lower, mechanism_group); first match wins."""
    entries = []
    seen = set()
    with INTERVENTION_MECHANISM_CSV.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            kw = (row.get("keyword") or "").strip().lower()
            mg = (row.get("mechanism_group") or "").strip()
            if not kw or not mg:
                continue
            if kw in seen:  # duplicate-safe: first occurrence wins
                continue
            seen.add(kw)
            entries.append((kw, mg))
    return entries


def match_intervention_mechanism(name, entries):
    """Return the mechanism_group for the first keyword found in name, or None."""
    if not name:
        return None, None
    low = str(name).lower()
    for kw, mg in entries:
        if kw in low:
            return mg, kw
    return None, None


def load_drug_target_map():
    """Return {drug_name_slug: {"drug": display, "targets": [gene_symbols]}}.

    Each Open Targets MoA capture record carries ``sources.opentargets[].targets``
    (target gene SYMBOLS) plus the name spellings the drug appears under (``name``,
    ``ot_name``, ``query_names``, ``trial_names``). We slug EVERY spelling (the
    same slugging drug NODES use, from trial intervention names) -> the drug's
    de-duplicated target symbol list, so a drug node can be joined to its target
    gene node(s). First slug-owner wins on collision.
    """
    if not DRUG_MECHANISM_API_PATH.exists():
        return {}
    out = {}
    for r in common.read_jsonl(DRUG_MECHANISM_API_PATH):
        targets = set()
        for ot in (r.get("sources") or {}).get("opentargets", []) or []:
            for sym in (ot.get("targets") or []):
                if sym:
                    targets.add(sym)
        if not targets:
            continue
        display = r.get("name") or r.get("ot_name")
        entry = {"drug": display, "targets": sorted(targets)}
        spellings = set()
        for key in ("name", "ot_name"):
            if r.get(key):
                spellings.add(r[key])
        for key in ("query_names", "trial_names"):
            for n in (r.get(key) or []):
                spellings.add(n)
        for sp in spellings:
            s = common.slug(sp)
            if s:
                out.setdefault(s, entry)
    return out


# ---------------------------------------------------------------------------
# Node id helpers
# ---------------------------------------------------------------------------

def gene_node_id(gene_id):
    return "gene:" + str(gene_id)


def variant_node_id(rsid):
    return "variant:" + str(rsid)


def pathway_node_id(mechanism_group):
    return "pathway:" + str(mechanism_group)


def drug_node_id(name_slug):
    return "drug:" + name_slug


def trial_node_id(nct_id):
    return "trial:" + str(nct_id)


def disease_node_id(disease_group):
    return "disease:" + str(disease_group)


def topic_node_id(topic_id):
    # topic_id already looks like "topic:000"; keep it as-is for stability.
    tid = str(topic_id)
    return tid if tid.startswith("topic:") else "topic:" + tid


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------

def build_variant_nodes(gwas):
    """One node per distinct rsid; provenance carries best (smallest) p_value."""
    best = {}  # rsid -> dict(p_value, trait, disease_group, assoc_count)
    for r in gwas:
        rsid = (r.get("variant") or {}).get("rsid")
        if not rsid:
            continue
        p = r.get("p_value")
        cur = best.get(rsid)
        if cur is None:
            best[rsid] = {
                "best_p_value": p,
                "trait": r.get("trait"),
                "disease_group": r.get("disease_group"),
                "association_count": 1,
            }
        else:
            cur["association_count"] += 1
            if p is not None and (cur["best_p_value"] is None
                                  or p < cur["best_p_value"]):
                cur["best_p_value"] = p
                cur["trait"] = r.get("trait")
                cur["disease_group"] = r.get("disease_group")

    nodes = []
    for rsid in sorted(best):
        info = best[rsid]
        p = info["best_p_value"]
        nl = common.neglog10(p)
        score = common.clamp01(nl / _NEGLOG10P_CAP) if nl is not None else None
        dg = info["disease_group"]
        nodes.append({
            "node_id": variant_node_id(rsid),
            "node_type": "variant",
            "label": rsid,
            "score": score,
            "scores": {"neglog10p": nl},
            "disease_groups": [dg] if dg else [],
            "provenance": {
                "source": "gwas_associations",
                "best_p_value": p,
                "trait": info["trait"],
                "disease_group": dg,
                "association_count": info["association_count"],
            },
        })
    return nodes


def build_gene_nodes(genes):
    """One node per gene; score = genetic_support."""
    nodes = []
    for r in genes:
        es = r.get("evidence_scores") or {}
        genetic = es.get("genetic_support")
        functional = es.get("functional_support")
        pathway_group = es.get("pathway_group")
        nodes.append({
            "node_id": gene_node_id(r["gene_id"]),
            "node_type": "gene",
            "label": r.get("symbol") or r["gene_id"],
            "score": genetic,
            "scores": {
                "genetic_support": genetic,
                "functional_support": functional,
            },
            "disease_groups": r.get("disease_groups") or [],
            "group": pathway_group,
            "provenance": {
                "source": "genes",
                "gene_id": r["gene_id"],
                "symbol": r.get("symbol"),
                "pathway_group": pathway_group,
                "gwas_association_count": r.get("gwas_association_count"),
            },
        })
    return nodes


def build_pathway_nodes(pathways):
    """One node per curated pathway (9). score = combined_support."""
    nodes = []
    for r in pathways:
        sc = r.get("scores") or {}
        mg = r.get("mechanism_group")
        nodes.append({
            "node_id": pathway_node_id(mg),
            "node_type": "pathway",
            "label": r.get("label") or mg,
            "score": sc.get("combined_support"),
            "scores": {
                "combined_support": sc.get("combined_support"),
                "clinical_translation": sc.get("clinical_translation"),
                "clinical_saturation": sc.get("clinical_saturation"),
                "translation_gap": sc.get("translation_gap"),
            },
            "disease_groups": [],
            "group": mg,
            "provenance": {
                "source": "pathways",
                "pathway_id": r.get("pathway_id"),
                "mechanism_group": mg,
                "mapped_trial_mechanism": sc.get("mapped_trial_mechanism"),
                "gene_count": r.get("gene_count"),
                "trial_count": sc.get("trial_count"),
            },
        })
    return nodes


def build_drug_nodes(trials, intervention_entries):
    """One node per distinct real drug/biological intervention name.

    Excludes placebo / saline / standard-of-care / usual-care. Provenance
    records how many trials it appears in and which trial mechanism_groups.
    """
    agg = {}  # slug -> dict(name, trial_count, mechanism_groups, disease_groups,
              #              matched_mechanism, matched_keyword)
    for t in trials:
        nct = t.get("nct_id")
        mech = t.get("mechanism_group")
        dg = t.get("disease_group")
        for iv in (t.get("interventions") or []):
            if iv.get("type") not in _DRUG_TYPES:
                continue
            name = iv.get("name")
            if not _is_real_drug_name(name):
                continue
            s = common.slug(name)
            if not s:
                continue
            entry = agg.get(s)
            if entry is None:
                mg, kw = match_intervention_mechanism(name, intervention_entries)
                entry = {
                    "name": name,
                    "trials": set(),
                    "mechanism_groups": set(),
                    "disease_groups": set(),
                    "matched_mechanism": mg,
                    "matched_keyword": kw,
                }
                agg[s] = entry
            if nct:
                entry["trials"].add(nct)
            if mech:
                entry["mechanism_groups"].add(mech)
            if dg:
                entry["disease_groups"].add(dg)

    nodes = []
    for s in sorted(agg):
        e = agg[s]
        nodes.append({
            "node_id": drug_node_id(s),
            "node_type": "drug",
            "label": e["name"],
            "score": None,
            "scores": {"trial_count": len(e["trials"])},
            "disease_groups": sorted(e["disease_groups"]),
            "group": e["matched_mechanism"],
            "provenance": {
                "source": "trials.interventions",
                "trial_count": len(e["trials"]),
                "mechanism_groups": sorted(e["mechanism_groups"]),
                "matched_mechanism": e["matched_mechanism"],
                "matched_keyword": e["matched_keyword"],
            },
        })
    return nodes, agg


def build_trial_nodes(trials):
    """One node per trial. label = truncated brief_title."""
    nodes = []
    for t in trials:
        nct = t.get("nct_id")
        if not nct:
            continue
        dg = t.get("disease_group")
        nodes.append({
            "node_id": trial_node_id(nct),
            "node_type": "trial",
            "label": _truncate(t.get("brief_title") or nct, _TRIAL_LABEL_MAX),
            "score": None,
            "scores": {},
            "disease_groups": [dg] if dg else [],
            "group": t.get("mechanism_group"),
            "provenance": {
                "source": "trials",
                "nct_id": nct,
                "phase": _first_phase(t.get("phases")),
                "phases": t.get("phases") or [],
                "overall_status": t.get("overall_status"),
                "mechanism_group": t.get("mechanism_group"),
                "disease_group": dg,
            },
        })
    return nodes


def build_disease_nodes(disease_groups, target_evidence):
    """One node per disease_group present. Carry a mean OT overall score."""
    # Aggregate a headline score per disease_group from target_evidence.
    overall_by_group = {}  # dg -> list of overall scores
    for r in target_evidence:
        dg = r.get("disease_group")
        ov = (r.get("scores") or {}).get("overall")
        if dg and ov is not None:
            overall_by_group.setdefault(dg, []).append(ov)

    nodes = []
    for dg in sorted(disease_groups):
        overalls = overall_by_group.get(dg, [])
        mean_overall = (sum(overalls) / len(overalls)) if overalls else None
        nodes.append({
            "node_id": disease_node_id(dg),
            "node_type": "disease",
            "label": _DISEASE_LABELS.get(dg, dg),
            "score": mean_overall,
            "scores": {"mean_target_overall": mean_overall,
                       "target_evidence_count": len(overalls)},
            "disease_groups": [dg],
            "provenance": {
                "source": "disease_group vocabulary",
                "disease_group": dg,
                "target_evidence_count": len(overalls),
            },
        })
    return nodes


def build_topic_nodes(rollup):
    """One node per Track A topic from the rollup. score = genetic_support."""
    nodes = []
    for r in rollup:
        tid = r.get("topic_id")
        if not tid:
            continue
        sc = r.get("scores") or {}
        nodes.append({
            "node_id": topic_node_id(tid),
            "node_type": "topic",
            "label": r.get("label") or tid,
            "score": sc.get("genetic_support"),
            "scores": {
                "genetic_support": sc.get("genetic_support"),
                "functional_support": sc.get("functional_support"),
                "clinical_translation": sc.get("clinical_translation"),
                "clinical_saturation": sc.get("clinical_saturation"),
                "translation_gap": sc.get("translation_gap"),
            },
            "disease_groups": r.get("disease_groups") or [],
            "group": r.get("pathway_group"),
            "provenance": {
                "source": "topic_evidence_rollup",
                "topic_id": tid,
                "pathway_group": r.get("pathway_group"),
                "evidence_counts": r.get("evidence_counts") or {},
            },
        })
    return nodes


# ---------------------------------------------------------------------------
# Edge builders
# ---------------------------------------------------------------------------

def build_variant_gene_edges(gwas, functional_links, symbol_to_gene_id):
    """variant_gene from GWAS reported_genes (evidence 'gwas') and L2G links.

    Each edge is de-duplicated on (variant, gene, evidence); the strongest score
    for a duplicated pair is kept.
    """
    # (variant_id, gene_id, evidence) -> best edge dict
    best = {}

    def _consider(variant_id, gene_id, evidence, score, prov):
        key = (variant_id, gene_id, evidence)
        cur = best.get(key)
        if cur is None or (score is not None and (cur["score"] is None
                                                  or score > cur["score"])):
            best[key] = {"score": score, "provenance": prov}

    # GWAS reported_genes -> variant_gene (score = normalised -log10 p).
    for r in gwas:
        rsid = (r.get("variant") or {}).get("rsid")
        if not rsid:
            continue
        reported = r.get("reported_genes") or []
        if not reported:
            continue
        nl = common.neglog10(r.get("p_value"))
        score = common.clamp01(nl / _NEGLOG10P_CAP) if nl is not None else None
        for sym in reported:
            gid = symbol_to_gene_id.get(sym)
            if not gid:
                continue
            _consider(
                variant_node_id(rsid), gene_node_id(gid), "gwas", score,
                {
                    "source": "gwas_associations.reported_genes",
                    "association_id": r.get("association_id"),
                    "reported_symbol": sym,
                    "p_value": r.get("p_value"),
                    "neglog10p": nl,
                    "study_accession": r.get("study_accession"),
                    "pmid": r.get("pmid"),
                },
            )

    # Functional (L2G) links -> variant_gene (score = L2G score).
    for fl in functional_links:
        rsid = fl.get("rsid")
        gid = fl.get("gene_id")
        if not rsid or not gid:
            continue
        _consider(
            variant_node_id(rsid), gene_node_id(gid), "l2g", fl.get("score"),
            {
                "source": "functional_links",
                "link_id": fl.get("link_id"),
                "evidence_type": fl.get("evidence_type"),
                "method": fl.get("method"),
                "l2g_score": fl.get("score"),
                "source_study": fl.get("source_study"),
                "gene_symbol": fl.get("gene_symbol"),
            },
        )

    edges = []
    for (variant_id, gene_id, evidence) in sorted(best):
        e = best[(variant_id, gene_id, evidence)]
        edges.append({
            "edge_id": "e:vg:%s:%s:%s" % (evidence, variant_id, gene_id),
            "source_id": variant_id,
            "target_id": gene_id,
            "edge_type": "variant_gene",
            "score": e["score"],
            "evidence": evidence,
            "provenance": e["provenance"],
        })
    return edges


def build_gene_pathway_edges(genes, gene_pathway_map):
    """gene_pathway from map/gene_pathway.csv (keyed by gene symbol)."""
    edges = []
    seen = set()
    for r in genes:
        sym = r.get("symbol")
        gid = r.get("gene_id")
        if not sym or not gid:
            continue
        for pg in gene_pathway_map.get(sym, []):
            src = gene_node_id(gid)
            tgt = pathway_node_id(pg)
            key = (src, tgt)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "edge_id": "e:gp:%s:%s" % (gid, pg),
                "source_id": src,
                "target_id": tgt,
                "edge_type": "gene_pathway",
                "score": None,
                "evidence": "gene_pathway_map",
                "provenance": {
                    "source": "map/gene_pathway.csv",
                    "gene_symbol": sym,
                    "pathway_group": pg,
                },
            })
    return edges


def build_gene_disease_edges(genes):
    """gene_disease from each gene's disease_groups list."""
    edges = []
    for r in genes:
        gid = r.get("gene_id")
        if not gid:
            continue
        for dg in (r.get("disease_groups") or []):
            edges.append({
                "edge_id": "e:gd:%s:%s" % (gid, dg),
                "source_id": gene_node_id(gid),
                "target_id": disease_node_id(dg),
                "edge_type": "gene_disease",
                "score": (r.get("evidence_scores") or {}).get("genetic_support"),
                "evidence": "gene_disease_group",
                "provenance": {
                    "source": "genes.disease_groups",
                    "gene_symbol": r.get("symbol"),
                    "disease_group": dg,
                },
            })
    return edges


def build_trial_drug_edges(trials):
    """trial_drug: each trial -> each of its real drug/biological interventions."""
    edges = []
    seen = set()
    for t in trials:
        nct = t.get("nct_id")
        if not nct:
            continue
        for iv in (t.get("interventions") or []):
            if iv.get("type") not in _DRUG_TYPES:
                continue
            name = iv.get("name")
            if not _is_real_drug_name(name):
                continue
            s = common.slug(name)
            if not s:
                continue
            src = trial_node_id(nct)
            tgt = drug_node_id(s)
            key = (src, tgt)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "edge_id": "e:td:%s:%s" % (nct, s),
                "source_id": src,
                "target_id": tgt,
                "edge_type": "trial_drug",
                "score": None,
                "evidence": "trial_intervention",
                "provenance": {
                    "source": "trials.interventions",
                    "nct_id": nct,
                    "intervention_name": name,
                    "intervention_type": iv.get("type"),
                },
            })
    return edges


def build_trial_pathway_edges(trials, trial_mech_to_pathway):
    """trial_pathway: trial -> pathway via mechanism_group crosswalk.

    trial_mech_to_pathway maps a trial-side mechanism_group (e.g.
    'inflammation_microglia') to a pathway mechanism_group (e.g.
    'microglia_immune') using each pathway's mapped_trial_mechanism.
    """
    edges = []
    seen = set()
    for t in trials:
        nct = t.get("nct_id")
        mech = t.get("mechanism_group")
        if not nct or not mech:
            continue
        pg = trial_mech_to_pathway.get(mech)
        if not pg:
            continue
        src = trial_node_id(nct)
        tgt = pathway_node_id(pg)
        key = (src, tgt)
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "edge_id": "e:tp:%s:%s" % (nct, pg),
            "source_id": src,
            "target_id": tgt,
            "edge_type": "trial_pathway",
            "score": None,
            "evidence": "trial_mechanism_crosswalk",
            "provenance": {
                "source": "trials.mechanism_group",
                "nct_id": nct,
                "trial_mechanism_group": mech,
                "pathway_mechanism_group": pg,
            },
        })
    return edges


def build_drug_pathway_edges(drug_agg, trial_mech_to_pathway):
    """drug_pathway: drug -> pathway via intervention_mechanism keyword match.

    The keyword match yields a trial-side mechanism_group; the crosswalk maps it
    to a pathway mechanism_group node.
    """
    edges = []
    for s in sorted(drug_agg):
        e = drug_agg[s]
        mech = e.get("matched_mechanism")
        if not mech:
            continue
        pg = trial_mech_to_pathway.get(mech)
        if not pg:
            continue
        edges.append({
            "edge_id": "e:dp:%s:%s" % (s, pg),
            "source_id": drug_node_id(s),
            "target_id": pathway_node_id(pg),
            "edge_type": "drug_pathway",
            "score": None,
            "evidence": "intervention_mechanism_map",
            "provenance": {
                "source": "map/intervention_mechanism.csv",
                "drug_name": e.get("name"),
                "matched_keyword": e.get("matched_keyword"),
                "trial_mechanism_group": mech,
                "pathway_mechanism_group": pg,
            },
        })
    return edges


def build_drug_gene_edges(drug_agg, drug_target_map, symbol_to_gene_id):
    """drug_gene: drug -> its Open Targets target gene(s).

    Connects the clinical layer to the SPECIFIC gene (and thence, via the
    existing variant_gene / gene_pathway edges, to the variant and pathway
    layers). Only drugs that are graph nodes (in ``drug_agg``) and target genes
    that are graph nodes (in ``symbol_to_gene_id``) are connected; anything else
    is skipped (never fabricated). De-duplicated on (drug_slug, gene_id).
    """
    edges = []
    seen = set()
    for s in sorted(drug_agg):
        entry = drug_target_map.get(s)
        if not entry:
            continue
        drug_name = drug_agg[s].get("name") or entry.get("drug")
        for sym in entry["targets"]:
            gid = symbol_to_gene_id.get(sym)
            if not gid:
                continue
            key = (s, gid)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "edge_id": "e:dg:%s:%s" % (s, gid),
                "source_id": drug_node_id(s),
                "target_id": gene_node_id(gid),
                "edge_type": "drug_gene",
                "score": None,
                "evidence": "open_targets_moa_target",
                "provenance": {
                    "source": "drug_mechanism_api.sources.opentargets.targets",
                    "drug_name": drug_name,
                    "target_gene": sym,
                    "gene_id": gid,
                },
            })
    return edges


def build_topic_edges(topic_links, node_ids):
    """topic_gene / topic_pathway / topic_disease from topic_evidence_links.

    Each Track A<->B bridge link is surfaced as a graph edge that CARRIES the
    link's structured provenance: the machine-readable ``method`` and
    ``confidence`` are hoisted onto the edge top-level (queryable in Neo4j /
    filterable in the HTML explorer) AND kept inside ``provenance`` alongside
    the exact join key so future agents can trust/extend the link.

      evidence_type=gene     -> topic_gene    (methods: pmid_join,
                                chemical_ui_crosswalk, regex_symbol_match)
      evidence_type=pathway  -> topic_pathway (method: gene_pathway_curated)
      evidence_type=disease  -> topic_disease (method: mesh_ui_join)

    ``gwas_association`` links are intentionally NOT emitted as topic edges here:
    the association is not a standalone graph node, so a topic->gwas edge would
    dangle. The genes reported by those associations already appear as high-
    confidence topic_gene (pmid_join) edges.

    Endpoints must already exist as nodes (dangling links are dropped here;
    they would also be dropped by the global dangling-edge pass).
    """
    edges = []
    for link in topic_links:
        etype = link.get("evidence_type")
        eid = link.get("evidence_id")
        tid = link.get("topic_id")
        if not eid or not tid:
            continue
        topic_id = topic_node_id(tid)
        if topic_id not in node_ids:
            continue

        if etype == "gene":
            target = gene_node_id(eid)
            edge_type = "topic_gene"
            prefix = "e:tg"
        elif etype == "pathway":
            # pathway evidence_id is like "curated:amyloid" -> mechanism_group.
            mech = eid.split(":", 1)[1] if ":" in eid else eid
            target = pathway_node_id(mech)
            edge_type = "topic_pathway"
            prefix = "e:tpw"
        elif etype == "disease":
            # disease evidence_id is already "disease:<group>" == the node_id.
            dg = eid.split(":", 1)[1] if ":" in eid else eid
            target = disease_node_id(dg)
            edge_type = "topic_disease"
            prefix = "e:tds"
        else:
            continue

        if target not in node_ids:
            continue

        method = link.get("method")
        confidence = link.get("confidence")
        # The bridge dedup key is (topic, evidence_type, evidence_id, link_type),
        # so include link_type in the edge_id to keep parallel-method edges (e.g.
        # a topic_gene found by BOTH pmid_join/paper_overlap AND
        # chemical_ui_crosswalk/chemical_annotation) distinct and lossless.
        link_type = link.get("link_type")
        edges.append({
            "edge_id": "%s:%s:%s:%s" % (prefix, topic_id, target,
                                        link_type or "na"),
            "source_id": topic_id,
            "target_id": target,
            "edge_type": edge_type,
            "score": link.get("score"),
            "evidence": link_type,
            # Hoisted, queryable structured-join metadata (mirrors provenance).
            "method": method,
            "confidence": confidence,
            "provenance": {
                "source": "topic_evidence_links",
                "topic_id": tid,
                "evidence_type": etype,
                "evidence_id": eid,
                "link_type": link_type,
                "method": method,
                "confidence": confidence,
                "join_key": (link.get("provenance") or {}).get("join_key"),
                "link_provenance": link.get("provenance") or {},
                "notes": link.get("notes"),
            },
        })
    return edges


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def assign_layout(nodes):
    """Assign deterministic layered x/y coordinates in-place.

    - x is fixed per node_type (evidence-chain columns), then normalised.
    - within each column, nodes are sorted by score descending (high-score
      nodes cluster near the top) with node_id as a stable tiebreak, then
      spread evenly down the column with a tiny index-based jitter (NO random).
    - all coordinates are normalised to roughly 0..1000.
    """
    # Normalise the column x positions into 0..1000.
    xs = list(_COLUMN_X.values())
    xmin, xmax = min(xs), max(xs)
    xspan = (xmax - xmin) or 1.0

    by_type = {}
    for n in nodes:
        by_type.setdefault(n["node_type"], []).append(n)

    for node_type, group in by_type.items():
        col_x = _COLUMN_X.get(node_type, 1.0)
        norm_x = (col_x - xmin) / xspan * _COORD_MAX

        # Sort high-score first; None scores sink to the bottom. Stable tiebreak
        # on node_id keeps the layout fully deterministic.
        group.sort(
            key=lambda n: (-(n.get("score") if n.get("score") is not None
                             else -1.0), n["node_id"])
        )
        count = len(group)
        for idx, n in enumerate(group):
            if count > 1:
                frac = idx / (count - 1)
            else:
                frac = 0.5
            # Index-based jitter: deterministic, small, sign alternates.
            jitter = ((idx % 7) - 3) * 1.5
            y = frac * _COORD_MAX + jitter
            y = max(0.0, min(_COORD_MAX, y))
            n["x"] = round(norm_x, 3)
            n["y"] = round(y, 3)


# ---------------------------------------------------------------------------
# Per-entity metrics join (from data/processed/.../entity_metrics.jsonl)
# ---------------------------------------------------------------------------
#
# entity_metrics carries a transparent, machine-readable METRICS record per
# gene / variant / pathway. Each metric is a dotted "<group>.<name>" key mapping
# to {"value": ..., "source": ...}. We (a) attach the FULL metrics object onto
# the matching graph node as node['metrics'] (nested, for completeness) and
# (b) hoist a compact set of FLAT, queryable properties (e.g. stopped_ratio,
# direction_agreement) onto the node so Neo4j Cypher and the HTML filters can use
# them directly. Nothing is fabricated: every hoisted value is copied verbatim
# from the entity_metrics record.
#
# Join keys (entity_metrics.entity_id -> graph node_id):
#   gene    : node_id == "gene:" + entity_id      (entity_id is the bare gene_id)
#   variant : node_id == entity_id                (both use the "variant:" prefix)
#   pathway : node_id == "pathway:" + pathway_group (mechanism_group crosswalk)

# node_type -> {flat_node_property: dotted_metric_key}. Only these compact
# properties are hoisted onto the node top-level; the full metrics object is
# always attached as node['metrics'] regardless of this map.
FLAT_METRIC_KEYS = {
    "gene": {
        "stopped_ratio": "clinical.stopped_ratio",
        "direction_agreement": "genetic.direction_agreement_ratio",
        "n_conflicting": "genetic.n_conflicting",
        "n_trials": "clinical.n_trials",
        "first_gwas_year": "temporal.first_gwas_year",
        "latest_gwas_year": "temporal.latest_gwas_year",
        "n_recent_gwas": "temporal.n_recent_gwas",
        "has_approval": "clinical.has_approval",
        # translation_gap composite was removed; hoist its raw components so an
        # agent / Cypher query can form its own genetics-vs-clinical gap.
        "best_neglog10p": "genetic.best_neglog10p",
        "n_papers": "literature.n_papers",
        "max_l2g": "functional.max_l2g",
    },
    "pathway": {
        "stopped_ratio": "clinical.stopped_ratio",
        "has_approval": "clinical.has_approval",
        "n_trials": "clinical.n_trials",
        "n_drugs": "clinical.n_drugs",
        "first_trial_year": "temporal.first_trial_year",
        "latest_trial_year": "temporal.latest_trial_year",
        "n_recent_trials": "temporal.n_recent_trials",
        # translation_gap composite removed; hoist raw components instead.
        "mean_best_neglog10p": "support.mean_best_neglog10p",
        "trials_per_gene": "ratios.trials_per_gene",
        "n_papers": "literature.n_papers",
    },
    "variant": {
        "n_associations": "genetic.n_associations",
        "n_studies": "genetic.n_studies",
        "first_year": "temporal.first_year",
        "latest_year": "temporal.latest_year",
        "n_recent": "temporal.n_recent",
        "direction_agreement": "genetic.direction_agreement_ratio",
    },
}


def _metric_value(metrics, dotted_key):
    """Return the scalar 'value' for a dotted metric key, or None if absent."""
    entry = metrics.get(dotted_key)
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def metric_node_id(rec):
    """Map an entity_metrics record to its expected graph node_id, or None."""
    etype = rec.get("entity_type")
    eid = rec.get("entity_id")
    if not etype or not eid:
        return None
    if etype == "gene":
        return gene_node_id(eid)
    if etype == "variant":
        # entity_id already carries the "variant:" prefix (variant:<rsid>).
        return str(eid) if str(eid).startswith("variant:") else variant_node_id(eid)
    if etype == "pathway":
        pg = rec.get("pathway_group")
        return pathway_node_id(pg) if pg else None
    return None


def load_entity_metrics():
    """Load entity_metrics.jsonl into {node_id: record}, or {} if absent."""
    if not ENTITY_METRICS_PATH.exists():
        common.log("entity_metrics.jsonl not found; skipping metrics join")
        return {}
    by_node = {}
    for rec in common.read_jsonl(ENTITY_METRICS_PATH):
        nid = metric_node_id(rec)
        if nid is not None:
            by_node[nid] = rec
    return by_node


def attach_metrics(nodes, metrics_by_node):
    """Attach flat metric props + full metrics object onto matching nodes.

    Returns (matched, unmatched_metric_records) counts by node_type for the
    manifest / report. Mutates nodes in place.
    """
    matched = {}
    matched_ids = set()
    for n in nodes:
        rec = metrics_by_node.get(n["node_id"])
        if rec is None:
            continue
        metrics = rec.get("metrics") or {}
        # Full nested metrics object for completeness.
        n["metrics"] = metrics
        # Compact, flat, queryable properties hoisted to the node top-level.
        flat_map = FLAT_METRIC_KEYS.get(n["node_type"], {})
        for prop, dotted in flat_map.items():
            n[prop] = _metric_value(metrics, dotted)
        matched[n["node_type"]] = matched.get(n["node_type"], 0) + 1
        matched_ids.add(n["node_id"])
    unmatched = sorted(set(metrics_by_node) - matched_ids)
    return matched, unmatched


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def drop_dangling_edges(edges, node_ids):
    """Keep only edges whose both endpoints are known node_ids."""
    kept = []
    dropped = 0
    for e in edges:
        if e["source_id"] in node_ids and e["target_id"] in node_ids:
            kept.append(e)
        else:
            dropped += 1
    return kept, dropped


def counts_by(records, key):
    out = {}
    for r in records:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items()))


def counts_by_optional(records, key):
    """Count records by a key that may be absent/None (skips missing)."""
    out = {}
    for r in records:
        v = r.get(key)
        if v is None:
            continue
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def main():
    common.log("loading Track B processed inputs")
    genes = common.read_jsonl(common.PROCESSED_DIR / "genes.jsonl")
    gwas = common.read_jsonl(common.PROCESSED_DIR / "gwas_associations.jsonl")
    pathways = common.read_jsonl(common.PROCESSED_DIR / "pathways.jsonl")
    trials = common.read_jsonl(common.PROCESSED_DIR / "trials.jsonl")
    functional_links = common.read_jsonl(
        common.PROCESSED_DIR / "functional_links.jsonl")
    target_evidence = common.read_jsonl(
        common.PROCESSED_DIR / "target_evidence.jsonl")

    rollup_path = common.SHARED_PROCESSED_DIR / "topic_evidence_rollup.jsonl"
    links_path = common.SHARED_PROCESSED_DIR / "topic_evidence_links.jsonl"
    rollup = common.read_jsonl(rollup_path) if rollup_path.exists() else []
    topic_links = common.read_jsonl(links_path) if links_path.exists() else []

    gene_pathway_map = load_gene_pathway_map()
    intervention_entries = load_intervention_mechanism_map()
    drug_target_map = load_drug_target_map()

    # symbol -> gene_id (for GWAS reported_genes, which are symbols).
    symbol_to_gene_id = {}
    for r in genes:
        if r.get("symbol") and r.get("gene_id"):
            symbol_to_gene_id.setdefault(r["symbol"], r["gene_id"])

    # Crosswalk: trial-side mechanism_group -> pathway mechanism_group, built
    # from each pathway's mapped_trial_mechanism.
    trial_mech_to_pathway = {}
    for r in pathways:
        mtm = (r.get("scores") or {}).get("mapped_trial_mechanism")
        mg = r.get("mechanism_group")
        if mtm and mg:
            trial_mech_to_pathway[mtm] = mg

    # Disease groups present anywhere in Track B.
    disease_groups = set()
    for r in genes:
        disease_groups.update(r.get("disease_groups") or [])
    for r in gwas:
        if r.get("disease_group"):
            disease_groups.add(r["disease_group"])
    for t in trials:
        if t.get("disease_group"):
            disease_groups.add(t["disease_group"])
    for fl in functional_links:
        if fl.get("disease_group"):
            disease_groups.add(fl["disease_group"])
    for r in target_evidence:
        if r.get("disease_group"):
            disease_groups.add(r["disease_group"])

    common.log("building nodes")
    variant_nodes = build_variant_nodes(gwas)
    gene_nodes = build_gene_nodes(genes)
    pathway_nodes = build_pathway_nodes(pathways)
    drug_nodes, drug_agg = build_drug_nodes(trials, intervention_entries)
    trial_nodes = build_trial_nodes(trials)
    disease_nodes = build_disease_nodes(disease_groups, target_evidence)
    topic_nodes = build_topic_nodes(rollup)

    nodes = (variant_nodes + gene_nodes + pathway_nodes + drug_nodes
             + trial_nodes + disease_nodes + topic_nodes)
    node_ids = {n["node_id"] for n in nodes}

    common.log("building edges")
    edges = []
    edges += build_variant_gene_edges(gwas, functional_links, symbol_to_gene_id)
    edges += build_gene_pathway_edges(genes, gene_pathway_map)
    edges += build_gene_disease_edges(genes)
    edges += build_trial_drug_edges(trials)
    edges += build_trial_pathway_edges(trials, trial_mech_to_pathway)
    edges += build_drug_pathway_edges(drug_agg, trial_mech_to_pathway)
    edges += build_drug_gene_edges(drug_agg, drug_target_map, symbol_to_gene_id)
    edges += build_topic_edges(topic_links, node_ids)

    edges, dropped = drop_dangling_edges(edges, node_ids)
    if dropped:
        common.log("dropped %d dangling edge(s)" % dropped)

    common.log("assigning deterministic layout")
    assign_layout(nodes)

    common.log("joining per-entity metrics onto nodes")
    metrics_by_node = load_entity_metrics()
    metrics_matched, metrics_unmatched = attach_metrics(nodes, metrics_by_node)
    if metrics_by_node:
        common.log("attached metrics to %d node(s) by type: %s"
                   % (sum(metrics_matched.values()), metrics_matched))
        if metrics_unmatched:
            common.log("%d entity_metrics record(s) had no matching node"
                       % len(metrics_unmatched))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n_written = common.write_jsonl(NODES_PATH, nodes)
    e_written = common.write_jsonl(EDGES_PATH, edges)

    node_type_counts = counts_by(nodes, "node_type")
    edge_type_counts = counts_by(edges, "edge_type")

    # Structured-join summary for the Track A<->B bridge edges (topic_*): how
    # each edge was made (method) and how confident it is (confidence). This
    # makes the "how + why" of every bridge link auditable straight from the
    # graph manifest, not just the underlying links file.
    topic_edge_types = {"topic_gene", "topic_pathway", "topic_disease"}
    topic_edges = [e for e in edges if e["edge_type"] in topic_edge_types]
    topic_edge_methods = counts_by_optional(topic_edges, "method")
    topic_edge_confidence = counts_by_optional(topic_edges, "confidence")

    manifest = {
        "generated_by": "translational-evidence/exports/build_evidence_graph.py",
        "generated_on": common.today_stamp(),
        "nodes": {
            "total": n_written,
            "by_type": node_type_counts,
        },
        "edges": {
            "total": e_written,
            "by_type": edge_type_counts,
            "dangling_dropped": dropped,
            "topic_bridge": {
                "note": ("topic_gene/topic_pathway/topic_disease edges carry the "
                         "bridge link's method + confidence (hoisted onto the "
                         "edge and mirrored in provenance with the exact "
                         "join_key). See translational-evidence/exports/"
                         "LINK_METHODS.md."),
                "total": len(topic_edges),
                "by_method": topic_edge_methods,
                "by_confidence": topic_edge_confidence,
            },
        },
        "metrics": {
            "source": str(ENTITY_METRICS_PATH.relative_to(common.REPO_ROOT))
            if ENTITY_METRICS_PATH.exists() else None,
            "records_loaded": len(metrics_by_node),
            "attached_by_type": metrics_matched,
            "unmatched_records": len(metrics_unmatched),
            "flat_keys": FLAT_METRIC_KEYS,
            "note": ("full metrics object attached as node['metrics']; flat "
                     "queryable props hoisted per FLAT_METRIC_KEYS"),
        },
        "layout": {
            "columns_x": _COLUMN_X,
            "coord_max": _COORD_MAX,
            "note": ("deterministic layered layout; x per node_type, y spread by "
                     "score with index-based jitter; no randomness"),
        },
        "inputs": {
            "genes": len(genes),
            "gwas_associations": len(gwas),
            "pathways": len(pathways),
            "trials": len(trials),
            "functional_links": len(functional_links),
            "target_evidence": len(target_evidence),
            "topic_rollup": len(rollup),
            "topic_links": len(topic_links),
        },
    }
    with MANIFEST_PATH.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")

    # ---- report ----
    print("Wrote:")
    print("  %s" % NODES_PATH)
    print("  %s" % EDGES_PATH)
    print("  %s" % MANIFEST_PATH)
    print()
    print("Nodes: %d total" % n_written)
    for k, v in node_type_counts.items():
        print("  %-9s %d" % (k, v))
    print("Edges: %d total (dangling dropped: %d)" % (e_written, dropped))
    for k, v in edge_type_counts.items():
        print("  %-14s %d" % (k, v))
    if topic_edges:
        print("Topic bridge edges: %d (topic_gene/topic_pathway/topic_disease)"
              % len(topic_edges))
        print("  by method:")
        for k, v in topic_edge_methods.items():
            print("    %-24s %d" % (k, v))
        print("  by confidence:")
        for k, v in topic_edge_confidence.items():
            print("    %-24s %d" % (k, v))
    if metrics_by_node:
        print("Metrics attached: %d node(s)" % sum(metrics_matched.values()))
        for k, v in sorted(metrics_matched.items()):
            print("  %-9s %d" % (k, v))
        if metrics_unmatched:
            print("  unmatched entity_metrics records: %d"
                  % len(metrics_unmatched))


if __name__ == "__main__":
    main()
