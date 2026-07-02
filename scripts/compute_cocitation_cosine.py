#!/usr/bin/env python3
"""Compute co-citation edges weighted by cosine similarity of co-citation
profile vectors — the weighting used in the breakthrough paper.

Method (per the paper):
  - Two corpus papers are co-cited each time a later paper cites both.
    Invert cited_by: for each citing paper C, the corpus papers it cites form a
    co-cited set; every pair in that set gets +1 co-citation.
  - Each corpus paper becomes a sparse vector over the papers it is co-cited
    with (component = co-citation count). Edge weight = cosine similarity of
    the two papers' vectors, which normalizes away well-cited vs. less-cited
    distortion.

Input:  data/interim/topic-dynamics/icite_citedby.jsonl
Output: data/interim/topic-dynamics/paper_edges_cocite_cosine.jsonl
        (source_paper_id, target_paper_id, edge_type, weight, evidence)
"""
import json, math, os
from collections import defaultdict
from itertools import combinations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "data/interim/topic-dynamics/icite_citedby.jsonl")
OUT = os.path.join(ROOT, "data/interim/topic-dynamics/paper_edges_cocite_cosine.jsonl")

MAX_CITER_SET = 80   # skip broad citers (a review citing >80 corpus papers gives diffuse, expensive co-citations)
MIN_COSINE = 0.05    # drop negligible edges
TOP_PER_NODE = 40    # cap fan-out per paper


def main():
    # corpus paper -> list of citing PMIDs
    cited_by = {}
    for line in open(IN, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        cited_by[str(r["pmid"])] = r.get("cited_by") or []
    corpus = set(cited_by)
    print(f"corpus papers with iCite record: {len(corpus)}")

    # invert: citing PMID -> set of corpus papers it cites
    citer_to_corpus = defaultdict(set)
    for pm, cbs in cited_by.items():
        for c in cbs:
            citer_to_corpus[c].add(pm)
    print(f"distinct citing papers: {len(citer_to_corpus)}")

    # accumulate co-citation counts (first-order profile vectors)
    vec = defaultdict(lambda: defaultdict(int))  # pmid -> {pmid: cocitations}
    skipped = 0
    for c, s in citer_to_corpus.items():
        if len(s) < 2:
            continue
        if len(s) > MAX_CITER_SET:
            skipped += 1
            continue
        for a, b in combinations(s, 2):
            vec[a][b] += 1
            vec[b][a] += 1
    print(f"papers with >=1 co-citation: {len(vec)} (skipped {skipped} broad citers)")

    norm = {p: math.sqrt(sum(v * v for v in nb.values())) for p, nb in vec.items()}

    # cosine over candidate pairs (those directly co-cited at least once)
    edges = defaultdict(list)  # pmid -> [(cosine, other, cocount)]
    seen = set()
    for a, nb_a in vec.items():
        na = norm[a]
        for b, cocount in nb_a.items():
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            nb_b = vec[b]
            # dot over shared neighbours (iterate smaller dict)
            small, big = (nb_a, nb_b) if len(nb_a) <= len(nb_b) else (nb_b, nb_a)
            dot = 0
            for k, val in small.items():
                w = big.get(k)
                if w:
                    dot += val * w
            denom = na * norm[b]
            if denom <= 0:
                continue
            cos = dot / denom
            if cos >= MIN_COSINE:
                edges[a].append((cos, b, cocount))
                edges[b].append((cos, a, cocount))

    # keep top-K per node, dedup, write
    kept = set()
    n = 0
    with open(OUT, "w", encoding="utf-8") as fh:
        for p, lst in edges.items():
            lst.sort(reverse=True)
            for cos, other, cocount in lst[:TOP_PER_NODE]:
                key = (p, other) if p < other else (other, p)
                if key in kept:
                    continue
                kept.add(key)
                fh.write(json.dumps({
                    "source_paper_id": "pmid:" + key[0],
                    "target_paper_id": "pmid:" + key[1],
                    "edge_type": "co_citation_cosine",
                    "weight": round(cos, 6),
                    "evidence": {"cocitations": cocount},
                }) + "\n")
                n += 1
    print(f"wrote {n} cosine co-citation edges -> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
