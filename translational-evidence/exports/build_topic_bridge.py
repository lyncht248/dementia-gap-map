"""Build the Track A <-> Track B integration bridge.

Track A (topic-dynamics) publishes a snapshot of topic clusters + member
papers. Track B (translational-evidence) owns curated gene / GWAS / pathway /
trial evidence. This script joins the two on member-paper PMIDs and on
case-sensitive gene-symbol mentions in member-paper text, producing TWO shared
outputs plus a manifest:

  1. data/processed/shared/topic_evidence_links.jsonl
     One record per (topic, evidence, link_type) join, conforming to
     shared/schemas/topic_evidence_link.schema.json. Every score carries a
     `notes` string so the link is explainable.

  2. data/processed/shared/topic_evidence_rollup.jsonl
     One record per topic: the Track B half of the frontend map_data.json
     (dominant pathway_group, top genes, top GWAS, related trials, aggregated
     scores, evidence counts, disease groups, provenance).

  3. data/processed/shared/topic_bridge_manifest.json
     Snapshot-awareness metadata (input counts + provenance note).

The current Track A snapshot is a SUBSET (full run pending). No counts are
hardcoded: they are read from the inputs, so a re-run against the full corpus
just works.

STDLIB-only Python 3.9. Run:
    python3 translational-evidence/exports/build_topic_bridge.py
Then validate:
    python3 translational-evidence/validate.py \
        data/processed/shared/topic_evidence_links.jsonl
"""

import csv
import json
import re
import sys
import pathlib
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402  (import after sys.path bootstrap)


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------

SNAPSHOT_DIR = common.INTERIM_DIR / "track_a_snapshot"
TOPIC_CLUSTERS = SNAPSHOT_DIR / "topic_clusters.jsonl"
PAPERS = SNAPSHOT_DIR / "papers.jsonl"

GENES = common.PROCESSED_DIR / "genes.jsonl"
GWAS = common.PROCESSED_DIR / "gwas_associations.jsonl"
PATHWAYS = common.PROCESSED_DIR / "pathways.jsonl"
TRIALS = common.PROCESSED_DIR / "trials.jsonl"

# AUTHORITATIVE gene_symbol -> pathway_group mapping.
GENE_PATHWAY_CSV = common.TE_DIR / "map" / "gene_pathway.csv"

LINKS_OUT = common.SHARED_PROCESSED_DIR / "topic_evidence_links.jsonl"
ROLLUP_OUT = common.SHARED_PROCESSED_DIR / "topic_evidence_rollup.jsonl"
MANIFEST_OUT = common.SHARED_PROCESSED_DIR / "topic_bridge_manifest.json"


# ---------------------------------------------------------------------------
# Gene-mention matching config
# ---------------------------------------------------------------------------

# Symbols shorter than this are skipped for gene_mention (too collision-prone).
MIN_SYMBOL_LEN = 3

# Curated blocklist of gene symbols that collide with common English words or
# generic tokens when matched case-sensitively against free text. Kept small
# and audited against the ACTUAL snapshot (see the LOG line at runtime): any
# entry here that produces no hits in the current snapshot is noted so the
# blocklist can be trimmed. These are excluded from gene_mention only; they can
# still be linked via paper_overlap (GWAS reported_genes), which is unambiguous.
AMBIGUOUS_SYMBOLS = {
    "SET",   # English word "set"; a real gene symbol elsewhere.
    "MAX",   # English word / abbreviation "max".
    "CAMK",  # generic kinase-family stem.
    "REST",  # English word "rest" (RE1-silencing transcription factor).
    "AR",    # 2 chars anyway, but generic ("AR"/augmented reality).
    "IMPACT",  # English word.
    "MICE",  # English word "mice".
    "CELL",  # English word "cell".
}

# Mechanism-group keyword vocabulary for matching a cluster's label + top_terms
# to a pathway_group. Values are the controlled pathway_group vocabulary.
MECHANISM_KEYWORDS = {
    "amyloid": ["amyloid", "abeta", "a-beta", "plaque", "app", "secretase"],
    "tau": ["tau", "tangle", "neurofibrillary", "tauopathy", "mapt"],
    "microglia_immune": [
        "microglia", "microglial", "immune", "immunity", "inflammation",
        "inflammatory", "neuroinflammation", "innate", "complement",
    ],
    "lipid_metabolism": [
        "lipid", "cholesterol", "apoe", "apolipoprotein", "metabolism",
        "clusterin",
    ],
    "vascular": [
        "vascular", "vasculature", "cerebrovascular", "hyperintensit",
        "white matter", "blood-brain", "angiotensin",
    ],
    "endocytosis_endosomal": [
        "endocytosis", "endosom", "trafficking", "retromer", "clathrin",
        "vesicle",
    ],
    "synaptic_neuronal": [
        "synaptic", "synapse", "neuronal", "neurotransmit", "plasticity",
        "dendritic",
    ],
    "epigenetic_transcription": [
        "epigenetic", "epigenome", "methylation", "transcription",
        "chromatin", "histone", "acetylation", "expression",
    ],
}


# ---------------------------------------------------------------------------
# Loading / indexing
# ---------------------------------------------------------------------------

def load_gene_pathway_map(csv_path):
    """Return {gene_symbol: pathway_group} from the authoritative CSV."""
    mapping = {}
    with pathlib.Path(csv_path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            symbol = (row.get("gene_symbol") or "").strip()
            group = (row.get("pathway_group") or "").strip()
            if symbol and group:
                mapping[symbol] = group
    return mapping


def index_papers(papers):
    """Build paper_id -> pmid and paper_id -> (title + ' ' + abstract) maps."""
    pid_to_pmid = {}
    pid_to_text = {}
    for p in papers:
        pid = p.get("paper_id")
        if not pid:
            continue
        pid_to_pmid[pid] = p.get("pmid")
        title = p.get("title") or ""
        abstract = p.get("abstract") or ""
        pid_to_text[pid] = (title + " " + abstract)
    return pid_to_pmid, pid_to_text


def index_genes(genes):
    """Build symbol -> gene record map (symbols are unique in the source)."""
    sym_to_gene = {}
    for g in genes:
        sym = g.get("symbol")
        if sym:
            sym_to_gene[sym] = g
    return sym_to_gene


def index_gwas(gwas):
    """Build the set of GWAS pmids and pmid -> [associations] map."""
    gwas_pmids = set()
    pmid_to_assocs = defaultdict(list)
    for a in gwas:
        pmid = a.get("pmid")
        if not pmid:
            continue
        gwas_pmids.add(pmid)
        pmid_to_assocs[pmid].append(a)
    return gwas_pmids, pmid_to_assocs


def index_pathways(pathways):
    """Build mechanism_group(pathway_group) -> pathway record map."""
    group_to_pathway = {}
    for p in pathways:
        group = p.get("mechanism_group")
        if group:
            group_to_pathway[group] = p
    return group_to_pathway


def index_trials_by_mechanism(trials):
    """Build trial mechanism_group -> [trial records] map."""
    by_mech = defaultdict(list)
    for t in trials:
        mech = t.get("mechanism_group")
        if mech:
            by_mech[mech].append(t)
    return by_mech


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _genetic_support(gene):
    return (gene.get("evidence_scores") or {}).get("genetic_support")


def _functional_support(gene):
    return (gene.get("evidence_scores") or {}).get("functional_support")


def _mean(values):
    """Mean of non-None numeric values, or None if there are none."""
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 4)


def _first_rsid(assoc):
    return (assoc.get("variant") or {}).get("rsid")


# ---------------------------------------------------------------------------
# Core: build links + gather per-topic evidence for the rollup
# ---------------------------------------------------------------------------

def build_topic(cluster, ctx):
    """Build links + a rollup record for a single Track A cluster.

    Returns (links, rollup, per_gene_support) where per_gene_support maps a
    linked gene symbol to its genetic_support (used for dominant-group logic).
    """
    topic_id = cluster["topic_id"]
    label = cluster.get("label")
    member_paper_ids = list(cluster.get("paper_ids") or [])
    n_members = len(member_paper_ids)

    pid_to_pmid = ctx["pid_to_pmid"]
    pid_to_text = ctx["pid_to_text"]
    sym_to_gene = ctx["sym_to_gene"]
    gwas_pmids = ctx["gwas_pmids"]
    pmid_to_assocs = ctx["pmid_to_assocs"]
    sym_to_group = ctx["sym_to_group"]
    group_to_pathway = ctx["group_to_pathway"]
    trials_by_mech = ctx["trials_by_mech"]

    links = []
    # Dedup key set for (topic, evidence_type, evidence_id, link_type).
    seen = set()

    def add_link(link):
        key = (link["topic_id"], link["evidence_type"],
               link["evidence_id"], link["link_type"])
        if key in seen:
            return False
        seen.add(key)
        links.append(link)
        return True

    # Track which genes are linked to this topic (symbol -> gene record) and by
    # what means, for the rollup + pathway logic.
    linked_genes = {}            # symbol -> gene record
    gene_mention_syms = set()    # symbols linked via gene_mention
    paper_overlap_syms = set()   # symbols linked via paper_overlap

    # --- (a) gene_mention -> gene -----------------------------------------
    # Case-sensitive whole-word match of each eligible symbol against member
    # paper text (title + abstract).
    member_texts = [(pid, pid_to_text.get(pid, "")) for pid in member_paper_ids]
    for symbol, gene in sym_to_gene.items():
        if len(symbol) < MIN_SYMBOL_LEN:
            continue
        if symbol in AMBIGUOUS_SYMBOLS:
            continue
        pattern = ctx["symbol_patterns"].get(symbol)
        if pattern is None:
            continue
        matching_pmids = []
        for pid, text in member_texts:
            if text and pattern.search(text):
                pmid = pid_to_pmid.get(pid)
                if pmid:
                    matching_pmids.append(pmid)
        if not matching_pmids:
            continue
        matching_pmids = sorted(set(matching_pmids))
        n_match = len(matching_pmids)
        score = round(n_match / n_members, 4) if n_members else 0.0
        add_link({
            "topic_id": topic_id,
            "evidence_type": "gene",
            "evidence_id": gene["gene_id"],
            "link_type": "gene_mention",
            "supporting_paper_ids": matching_pmids,
            "score": score,
            "notes": "symbol '%s' in %d/%d member abstracts"
                     % (symbol, n_match, n_members),
        })
        linked_genes[symbol] = gene
        gene_mention_syms.add(symbol)

    # --- (b) paper_overlap -> gwas_association ----------------------------
    # (c) paper_overlap -> gene (reported_genes of those associations)
    # Determine pmids shared by this cluster and our GWAS corpus.
    overlap_pmids = []
    for pid in member_paper_ids:
        pmid = pid_to_pmid.get(pid)
        if pmid and pmid in gwas_pmids:
            overlap_pmids.append(pmid)
    overlap_pmids = sorted(set(overlap_pmids))

    n_gwas_assoc = 0
    for pmid in overlap_pmids:
        for assoc in pmid_to_assocs.get(pmid, []):
            assoc_id = assoc.get("association_id")
            if not assoc_id:
                continue
            emitted = add_link({
                "topic_id": topic_id,
                "evidence_type": "gwas_association",
                "evidence_id": assoc_id,
                "link_type": "paper_overlap",
                "supporting_paper_ids": [pmid],
                "score": 1.0,
                "notes": "GWAS publication in topic corpus",
            })
            if emitted:
                n_gwas_assoc += 1

            # (c) reported_genes of this association -> gene paper_overlap.
            for rg in (assoc.get("reported_genes") or []):
                gene = sym_to_gene.get(rg)
                if gene is None:
                    continue
                add_link({
                    "topic_id": topic_id,
                    "evidence_type": "gene",
                    "evidence_id": gene["gene_id"],
                    "link_type": "paper_overlap",
                    "supporting_paper_ids": [pmid],
                    "score": 1.0,
                    "notes": "gene '%s' reported by GWAS pub %s in topic corpus"
                             % (rg, pmid),
                })
                linked_genes[rg] = gene
                paper_overlap_syms.add(rg)

    # --- pathway representation among linked genes ------------------------
    # Weight each represented pathway_group by summed genetic_support of the
    # topic's linked genes in that group (authoritative CSV mapping).
    group_support = defaultdict(float)
    group_gene_count = defaultdict(int)
    total_grouped_genes = 0
    for symbol, gene in linked_genes.items():
        group = sym_to_group.get(symbol)
        if not group:
            continue
        gs = _genetic_support(gene) or 0.0
        group_support[group] += gs
        group_gene_count[group] += 1
        total_grouped_genes += 1

    # Keyword match of label + top_terms to the mechanism vocabulary.
    haystack = " ".join(
        [str(label or "")] + [str(t) for t in (cluster.get("top_terms") or [])]
    ).lower()
    keyword_groups = set()
    for group, kws in MECHANISM_KEYWORDS.items():
        for kw in kws:
            if kw in haystack:
                keyword_groups.add(group)
                break

    # Dominant group = highest summed genetic_support among linked genes.
    dominant_group = None
    if group_support:
        dominant_group = max(
            group_support,
            key=lambda g: (group_support[g], group_gene_count[g]),
        )

    # --- (d) pathway_mapping -> pathway -----------------------------------
    # Emit a pathway link for every group represented among linked genes, plus
    # any group flagged only by top_terms keyword match (score None = no
    # gene-share evidence, purely lexical).
    represented = set(group_support.keys()) | keyword_groups
    for group in sorted(represented):
        pathway = group_to_pathway.get(group)
        if pathway is None:
            continue
        share = None
        if total_grouped_genes and group in group_gene_count:
            share = round(group_gene_count[group] / total_grouped_genes, 4)
        note_bits = []
        if group in group_support:
            if group == dominant_group:
                note_bits.append("dominant linked-gene pathway group")
            else:
                note_bits.append("among linked-gene pathway groups")
        if group in keyword_groups:
            note_bits.append("top_terms match")
        add_link({
            "topic_id": topic_id,
            "evidence_type": "pathway",
            "evidence_id": pathway["pathway_id"],
            "link_type": "pathway_mapping",
            "score": share,
            "notes": "; ".join(note_bits) if note_bits else "pathway mapping",
        })

    # -----------------------------------------------------------------------
    # Rollup record
    # -----------------------------------------------------------------------
    rollup = build_rollup(
        topic_id=topic_id,
        label=label,
        linked_genes=linked_genes,
        gene_mention_syms=gene_mention_syms,
        paper_overlap_syms=paper_overlap_syms,
        overlap_pmids=overlap_pmids,
        pmid_to_assocs=pmid_to_assocs,
        sym_to_group=sym_to_group,
        dominant_group=dominant_group,
        group_to_pathway=group_to_pathway,
        trials_by_mech=trials_by_mech,
        n_gwas_assoc=n_gwas_assoc,
        n_pathways=len({l["evidence_id"] for l in links
                        if l["evidence_type"] == "pathway"}),
        ctx=ctx,
    )

    return links, rollup


def build_rollup(topic_id, label, linked_genes, gene_mention_syms,
                 paper_overlap_syms, overlap_pmids, pmid_to_assocs,
                 sym_to_group, dominant_group, group_to_pathway,
                 trials_by_mech, n_gwas_assoc, n_pathways, ctx):
    """Assemble the Track B half of the frontend record for one topic."""
    # top_genes: up to 8 linked genes ranked by genetic_support.
    genes_sorted = sorted(
        linked_genes.items(),
        key=lambda kv: (_genetic_support(kv[1]) or -1.0, kv[0]),
        reverse=True,
    )
    top_genes = []
    for symbol, gene in genes_sorted[:8]:
        top_genes.append({
            "symbol": symbol,
            "gene_id": gene["gene_id"],
            "genetic_support": _genetic_support(gene),
            "functional_support": _functional_support(gene),
            "disease_groups": list(gene.get("disease_groups") or []),
        })

    # top_gwas: up to 8 from paper_overlap associations (dedup by association_id).
    top_gwas = []
    seen_assoc = set()
    for pmid in overlap_pmids:
        for assoc in pmid_to_assocs.get(pmid, []):
            aid = assoc.get("association_id")
            if not aid or aid in seen_assoc:
                continue
            seen_assoc.add(aid)
            top_gwas.append({
                "association_id": aid,
                "rsid": _first_rsid(assoc),
                "reported_genes": list(assoc.get("reported_genes") or []),
                "trait": assoc.get("trait"),
            })
            if len(top_gwas) >= 8:
                break
        if len(top_gwas) >= 8:
            break

    # Dominant pathway record + its trial-mechanism crosswalk.
    dominant_pathway = (group_to_pathway.get(dominant_group)
                        if dominant_group else None)
    pathway_scores = (dominant_pathway or {}).get("scores") or {}

    # trials: up to 10 whose mechanism_group == the dominant pathway_group's
    # mapped trial mechanism (via the pathway record's crosswalk).
    trials_out = []
    mapped_trial_mech = pathway_scores.get("mapped_trial_mechanism")
    if mapped_trial_mech:
        for t in trials_by_mech.get(mapped_trial_mech, [])[:10]:
            phases = t.get("phases") or []
            trials_out.append({
                "nct_id": t.get("nct_id"),
                "brief_title": t.get("brief_title"),
                "phase": phases[-1] if phases else None,
                "mechanism_group": t.get("mechanism_group"),
            })

    # Aggregated scores.
    mean_genetic = _mean([_genetic_support(g) for g in linked_genes.values()])
    mean_functional = _mean(
        [_functional_support(g) for g in linked_genes.values()]
    )
    scores = {
        "genetic_support": mean_genetic,
        "functional_support": mean_functional,
        "clinical_translation": pathway_scores.get("clinical_translation"),
        "clinical_saturation": pathway_scores.get("clinical_saturation"),
        "translation_gap": pathway_scores.get("translation_gap"),
        "_method": (
            "genetic_support/functional_support = mean over linked genes; "
            "clinical_* / translation_gap copied from the dominant "
            "pathway_group record (by summed genetic_support of linked genes)"
        ),
    }

    # disease_groups: union across linked genes.
    disease_groups = set()
    for gene in linked_genes.values():
        disease_groups.update(gene.get("disease_groups") or [])

    evidence_counts = {
        "n_linked_genes": len(linked_genes),
        "n_gene_mention": len(gene_mention_syms),
        "n_paper_overlap_genes": len(paper_overlap_syms),
        "n_gwas_assoc": n_gwas_assoc,
        "n_pathways": n_pathways,
    }

    provenance = (
        "Track A cluster + Track B evidence join on member-paper "
        "PMIDs/abstracts (track_a_snapshot: %d papers / %d clusters - "
        "SUBSET, full run pending)"
        % (ctx["track_a_papers"], ctx["track_a_clusters"])
    )

    return {
        "topic_id": topic_id,
        "label": label,
        "pathway_group": dominant_group,
        "top_genes": top_genes,
        "top_gwas": top_gwas,
        "trials": trials_out,
        "scores": scores,
        "evidence_counts": evidence_counts,
        "disease_groups": sorted(disease_groups),
        "provenance": provenance,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # --- load inputs -------------------------------------------------------
    clusters = common.read_jsonl(TOPIC_CLUSTERS)
    papers = common.read_jsonl(PAPERS)
    genes = common.read_jsonl(GENES)
    gwas = common.read_jsonl(GWAS)
    pathways = common.read_jsonl(PATHWAYS)
    trials = common.read_jsonl(TRIALS)

    track_a_papers = len(papers)
    track_a_clusters = len(clusters)
    track_b_genes = len(genes)
    common.log("loaded Track A: %d papers, %d clusters"
               % (track_a_papers, track_a_clusters))
    common.log("loaded Track B: %d genes, %d gwas assoc, %d pathways, "
               "%d trials" % (len(genes), len(gwas), len(pathways),
                              len(trials)))

    # --- indexes -----------------------------------------------------------
    pid_to_pmid, pid_to_text = index_papers(papers)
    sym_to_gene = index_genes(genes)
    gwas_pmids, pmid_to_assocs = index_gwas(gwas)
    group_to_pathway = index_pathways(pathways)
    trials_by_mech = index_trials_by_mechanism(trials)
    sym_to_group = load_gene_pathway_map(GENE_PATHWAY_CSV)
    common.log("authoritative gene->pathway_group entries: %d"
               % len(sym_to_group))

    # Precompile a case-sensitive whole-word regex per eligible symbol.
    symbol_patterns = {}
    for symbol in sym_to_gene:
        if len(symbol) < MIN_SYMBOL_LEN or symbol in AMBIGUOUS_SYMBOLS:
            continue
        symbol_patterns[symbol] = re.compile(r"\b" + re.escape(symbol) + r"\b")

    # LOG blocklist activity against the ACTUAL snapshot so it can be audited:
    # which blocked symbols would otherwise have hit member text?
    corpus_text = "\n".join(pid_to_text.values())
    blocked_would_hit = []
    blocked_no_hit = []
    for symbol in sorted(AMBIGUOUS_SYMBOLS):
        if symbol not in sym_to_gene:
            continue  # not even a Track B gene; nothing to block
        if len(symbol) < MIN_SYMBOL_LEN:
            continue  # already skipped by length rule
        pat = re.compile(r"\b" + re.escape(symbol) + r"\b")
        (blocked_would_hit if pat.search(corpus_text)
         else blocked_no_hit).append(symbol)
    common.log("gene_mention blocklist: %s" % sorted(AMBIGUOUS_SYMBOLS))
    common.log("  blocklist symbols present as Track B genes that WOULD have "
               "matched member text (spurious hits prevented): %s"
               % (blocked_would_hit or "none"))
    common.log("  blocklist symbols with no hit in this snapshot (defensive "
               "only): %s" % (blocked_no_hit or "none"))

    ctx = {
        "pid_to_pmid": pid_to_pmid,
        "pid_to_text": pid_to_text,
        "sym_to_gene": sym_to_gene,
        "gwas_pmids": gwas_pmids,
        "pmid_to_assocs": pmid_to_assocs,
        "sym_to_group": sym_to_group,
        "group_to_pathway": group_to_pathway,
        "trials_by_mech": trials_by_mech,
        "symbol_patterns": symbol_patterns,
        "track_a_papers": track_a_papers,
        "track_a_clusters": track_a_clusters,
    }

    # --- build per topic ---------------------------------------------------
    all_links = []
    all_rollups = []
    per_topic_link_count = {}
    for cluster in clusters:
        links, rollup = build_topic(cluster, ctx)
        all_links.extend(links)
        all_rollups.append(rollup)
        per_topic_link_count[cluster["topic_id"]] = len(links)

    # --- write outputs -----------------------------------------------------
    n_links = common.write_jsonl(LINKS_OUT, all_links)
    n_rollups = common.write_jsonl(ROLLUP_OUT, all_rollups)

    manifest = {
        "track_a_papers": track_a_papers,
        "track_a_clusters": track_a_clusters,
        "track_b_genes": track_b_genes,
        "generated_from": "track_a_snapshot (SUBSET - full run pending)",
    }
    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_OUT.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")

    common.log("wrote %d links -> %s" % (n_links, LINKS_OUT))
    common.log("wrote %d rollups -> %s" % (n_rollups, ROLLUP_OUT))
    common.log("wrote manifest -> %s" % MANIFEST_OUT)

    # --- per-topic summary table ------------------------------------------
    print_summary(all_rollups, per_topic_link_count)
    return 0


def print_summary(rollups, per_topic_link_count):
    """Print topic_id | label | n_links | pathway_group | genetic | func | gap."""
    def fmt(x):
        return "%.4f" % x if isinstance(x, (int, float)) else "null"

    header = ("topic_id", "label", "n_links", "dominant_pathway_group",
              "genetic_support", "functional_support", "translation_gap")
    rows = []
    for r in rollups:
        s = r["scores"]
        rows.append((
            r["topic_id"],
            (r["label"] or "")[:34],
            str(per_topic_link_count.get(r["topic_id"], 0)),
            str(r["pathway_group"]),
            fmt(s["genetic_support"]),
            fmt(s["functional_support"]),
            fmt(s["translation_gap"]),
        ))

    widths = [max(len(header[i]), max((len(row[i]) for row in rows),
                                      default=0)) for i in range(len(header))]
    line = "  ".join(header[i].ljust(widths[i]) for i in range(len(header)))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(header))))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(header))))


if __name__ == "__main__":
    sys.exit(main())
