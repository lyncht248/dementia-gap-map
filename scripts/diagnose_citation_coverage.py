#!/usr/bin/env python3
"""Diagnostic #1: per-paper citation / co-citation coverage.

Question this answers: does the corpus carry enough co-citation signal for a
co-citation network (CCN) to group topics, or is the median paper too sparse?

For every corpus paper it computes:
  - citing_papers   number of papers that cite it (its iCite cited_by list)
  - cociting_papers number of those citers that also cite >=1 OTHER corpus
                    paper, i.e. citers that actually contribute a co-citation
  - cocit_nnz       nonzero entries in its co-citation vector = number of
                    DISTINCT other corpus papers it is ever co-cited with
  - cocit_degree    degree in the thresholded cosine co-citation graph
                    (the CCN that would drive topic grouping)
  - year

Two co-citation passes are reported so the effect of the broad-citer filter is
visible:
  raw     every citer that cites >=2 corpus papers counts
  capped  only citers citing between 2 and MAX_CITER_SET corpus papers count
          (this is the profile the CCN is actually built from)

Inputs:
  data/processed/topic-dynamics/papers.jsonl          (year, corpus membership)
  data/interim/topic-dynamics/icite_citedby.jsonl     (cited_by per paper)
Outputs:
  data/interim/topic-dynamics/citation_coverage.jsonl (per-paper record)
  summary table printed to stdout
"""
import json, math, os
from collections import defaultdict
from itertools import combinations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPERS = os.path.join(ROOT, "data/processed/topic-dynamics/papers.jsonl")
CITEDBY = os.path.join(ROOT, "data/interim/topic-dynamics/icite_citedby.jsonl")
OUT = os.path.join(ROOT, "data/interim/topic-dynamics/citation_coverage.jsonl")

# Match compute_cocitation_cosine.py so "cocit_degree" reflects the real CCN.
MAX_CITER_SET = 80
MIN_COSINE = 0.05
TOP_PER_NODE = 40


def pctl(sorted_vals, q):
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1) + 0.5))
    return sorted_vals[idx]


def summarize(name, vals):
    s = sorted(vals)
    n = len(s)
    mean = sum(s) / n if n else 0
    return {
        "metric": name,
        "n": n,
        "min": s[0] if n else 0,
        "p10": pctl(s, 0.10),
        "p25": pctl(s, 0.25),
        "median": pctl(s, 0.50),
        "p75": pctl(s, 0.75),
        "p90": pctl(s, 0.90),
        "max": s[-1] if n else 0,
        "mean": round(mean, 2),
        "zero": sum(1 for v in s if v == 0),
        "lt10": sum(1 for v in s if v < 10),
        "lt20": sum(1 for v in s if v < 20),
    }


def main():
    # --- corpus + year ------------------------------------------------------
    year = {}
    corpus_order = []
    for line in open(PAPERS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        pm = str(r.get("pmid"))
        if not pm or pm == "None":
            continue
        year[pm] = r.get("year")
        corpus_order.append(pm)
    corpus = set(corpus_order)
    print(f"corpus papers: {len(corpus)}")

    # --- cited_by (number of citing papers) ---------------------------------
    cited_by = {}
    for line in open(CITEDBY, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        cited_by[str(r["pmid"])] = list({str(c) for c in (r.get("cited_by") or [])})
    have_icite = set(cited_by)
    print(f"papers with an iCite record: {len(have_icite)}")

    citing_papers = {p: len(cited_by.get(p, [])) for p in corpus}

    # --- invert: citer -> set of corpus papers it cites ---------------------
    citer_to_corpus = defaultdict(set)
    for pm, cbs in cited_by.items():
        if pm not in corpus:
            continue
        for c in cbs:
            citer_to_corpus[c].add(pm)
    print(f"distinct citing papers (any corpus paper): {len(citer_to_corpus)}")

    # --- co-citation, raw and capped ----------------------------------------
    # cociting_papers[p]  = citers of p that cite >=2 corpus papers
    # cocit_nnz[p]        = distinct other corpus papers p is co-cited with
    cociting_raw = defaultdict(int)
    cociting_capped = defaultdict(int)
    nnz_raw = defaultdict(set)
    vec = defaultdict(lambda: defaultdict(int))   # capped profile -> cosine
    broad = 0
    for c, s in citer_to_corpus.items():
        k = len(s)
        if k < 2:
            continue
        for p in s:
            cociting_raw[p] += 1
        for a, b in combinations(s, 2):
            nnz_raw[a].add(b)
            nnz_raw[b].add(a)
        if k > MAX_CITER_SET:
            broad += 1
            continue
        for p in s:
            cociting_capped[p] += 1
        for a, b in combinations(s, 2):
            vec[a][b] += 1
            vec[b][a] += 1
    print(f"citers dropped as broad (>{MAX_CITER_SET} corpus papers): {broad}")

    # --- thresholded cosine CCN degree --------------------------------------
    norm = {p: math.sqrt(sum(v * v for v in nb.values())) for p, nb in vec.items()}
    edges = defaultdict(list)
    seen = set()
    for a, nb_a in vec.items():
        na = norm[a]
        for b, cocount in nb_a.items():
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            nb_b = vec[b]
            small, big = (nb_a, nb_b) if len(nb_a) <= len(nb_b) else (nb_b, nb_a)
            dot = 0
            for kk, val in small.items():
                w = big.get(kk)
                if w:
                    dot += val * w
            denom = na * norm[b]
            if denom <= 0:
                continue
            cos = dot / denom
            if cos >= MIN_COSINE:
                edges[a].append((cos, b))
                edges[b].append((cos, a))

    degree = defaultdict(int)
    kept = set()
    for p, lst in edges.items():
        lst.sort(reverse=True)
        for cos, other in lst[:TOP_PER_NODE]:
            key = (p, other) if p < other else (other, p)
            if key in kept:
                continue
            kept.add(key)
            degree[p] += 1
            degree[other] += 1

    # --- per-paper records --------------------------------------------------
    with open(OUT, "w", encoding="utf-8") as fh:
        for p in corpus_order:
            fh.write(json.dumps({
                "pmid": p,
                "year": year.get(p),
                "has_icite": p in have_icite,
                "citing_papers": citing_papers.get(p, 0),
                "cociting_papers_raw": cociting_raw.get(p, 0),
                "cociting_papers_capped": cociting_capped.get(p, 0),
                "cocit_nnz": len(nnz_raw.get(p, ())),
                "cocit_degree": degree.get(p, 0),
            }) + "\n")
    print(f"wrote per-paper records -> {os.path.relpath(OUT, ROOT)}\n")

    # --- summary ------------------------------------------------------------
    metrics = [
        ("citing_papers", [citing_papers.get(p, 0) for p in corpus_order]),
        ("cociting_papers_raw", [cociting_raw.get(p, 0) for p in corpus_order]),
        ("cociting_papers_capped", [cociting_capped.get(p, 0) for p in corpus_order]),
        ("cocit_nnz", [len(nnz_raw.get(p, ())) for p in corpus_order]),
        ("cocit_degree", [degree.get(p, 0) for p in corpus_order]),
    ]
    hdr = f"{'metric':<24}{'min':>5}{'p10':>6}{'p25':>6}{'median':>8}{'p75':>7}{'p90':>7}{'max':>8}{'mean':>8}{'=0':>7}{'<10':>7}{'<20':>7}"
    print(hdr)
    print("-" * len(hdr))
    N = len(corpus_order)
    for name, vals in metrics:
        st = summarize(name, vals)
        print(f"{st['metric']:<24}{st['min']:>5}{st['p10']:>6}{st['p25']:>6}"
              f"{st['median']:>8}{st['p75']:>7}{st['p90']:>7}{st['max']:>8}"
              f"{st['mean']:>8}"
              f"{st['zero']:>7}{st['lt10']:>7}{st['lt20']:>7}")
    print("-" * len(hdr))
    print(f"(=0 / <10 / <20 are paper COUNTS out of N={N}; "
          f"as %: e.g. median co-citing_capped verdict below)\n")

    # --- co-citation coverage is dominated by recency: break out by year ----
    def med(xs):
        s = sorted(xs)
        return s[len(s) // 2] if s else 0

    by_year = defaultdict(list)
    for p in corpus_order:
        by_year[year.get(p)].append(p)
    print("BY PUBLICATION YEAR (co-citation coverage accrues with age):")
    yh = f"  {'year':>6}{'n':>6}{'med_citing':>12}{'med_cocit':>11}{'med_nnz':>9}{'med_deg':>9}{'deg=0%':>8}"
    print(yh)
    for y in sorted(by_year, key=lambda v: (v is None, v)):
        b = by_year[y]
        nb = len(b)
        z = sum(1 for p in b if degree.get(p, 0) == 0) / nb * 100
        print(f"  {str(y):>6}{nb:>6}"
              f"{med([citing_papers.get(p,0) for p in b]):>12}"
              f"{med([cociting_capped.get(p,0) for p in b]):>11}"
              f"{med([len(nnz_raw.get(p,())) for p in b]):>9}"
              f"{med([degree.get(p,0) for p in b]):>9}{z:>7.0f}%")
    print()

    # --- verdict, split into mature core vs recent tail ---------------------
    MATURE_MAX_YEAR = 2022   # >=~4 yrs to accrue citations at run time
    mature = [p for p in corpus_order
              if isinstance(year.get(p), int) and year[p] <= MATURE_MAX_YEAR]
    recent = [p for p in corpus_order
              if isinstance(year.get(p), int) and year[p] > MATURE_MAX_YEAR]

    cap_all = sorted(cociting_capped.get(p, 0) for p in corpus_order)
    med_all = pctl(cap_all, 0.50)
    med_mat = med([cociting_capped.get(p, 0) for p in mature])
    med_rec = med([cociting_capped.get(p, 0) for p in recent])
    connected = sum(1 for p in corpus_order if degree.get(p, 0) >= 1)

    print("VERDICT (co-citing contexts, capped profile = the CCN's real input):")
    print(f"  whole corpus (N={N}): median = {med_all}  "
          f"(<10: {sum(1 for v in cap_all if v<10)/N*100:.0f}%, "
          f"<20: {sum(1 for v in cap_all if v<20)/N*100:.0f}%)")
    print(f"  mature core (year<={MATURE_MAX_YEAR}, n={len(mature)}): median = {med_mat}")
    print(f"  recent tail (year> {MATURE_MAX_YEAR}, n={len(recent)}): median = {med_rec}")
    print(f"  connected in CCN (degree>=1): {connected}/{N} = {connected/N*100:.0f}%")
    print()
    if med_all < 10 <= med_mat:
        print("  => RECENCY-LIMITED, NOT STRUCTURALLY SPARSE.")
        print("     The corpus-wide median is low only because the recent tail")
        print("     has not accrued citations yet. The mature core carries")
        print("     enough co-citation signal for CCN topic grouping; the")
        print(f"     recent ~{len(recent)/N*100:.0f}% of papers need a content-based")
        print("     fallback (embeddings / bibliographic coupling) to be placed.")
    elif med_mat < 10:
        print("  => STRUCTURALLY SPARSE: even the mature core has <10 co-citing")
        print("     contexts. CCN alone will not group topics reliably.")
    else:
        print("  => OK: median paper has enough co-citing contexts for CCN.")


if __name__ == "__main__":
    main()
