#!/usr/bin/env python3
"""Fetch cited_by + RCR for the corpus PMIDs from NIH iCite.

iCite's /api/pubs returns, per PMID: cited_by (space-separated PMIDs that cite
it), relative_citation_ratio, is_clinical, citation_count, year. That's exactly
what the paper's co-citation weighting needs ("with each citation it receives").

Uses curl (which respects the agent proxy + CA bundle) rather than urllib.
Output: data/interim/topic-dynamics/icite_citedby.jsonl (one record per paper).
"""
import json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPERS = os.path.join(ROOT, "data/processed/topic-dynamics/papers.jsonl")
OUTDIR = os.path.join(ROOT, "data/interim/topic-dynamics")
OUT = os.path.join(OUTDIR, "icite_citedby.jsonl")
BATCH = 200
API = "https://icite.od.nih.gov/api/pubs?pmids="


def curl_json(url, tries=4):
    for i in range(tries):
        p = subprocess.run(["curl", "-sS", "--max-time", "60", url],
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            try:
                return json.loads(p.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2 * (i + 1))
    return None


def main():
    pmids = []
    for line in open(PAPERS, encoding="utf-8"):
        line = line.strip()
        if line:
            pm = json.loads(line).get("pmid")
            if pm:
                pmids.append(str(pm))
    os.makedirs(OUTDIR, exist_ok=True)
    n = len(pmids)
    got = miss = 0
    with open(OUT, "w", encoding="utf-8") as fh:
        for i in range(0, n, BATCH):
            batch = pmids[i:i + BATCH]
            d = curl_json(API + ",".join(batch))
            recs = (d or {}).get("data", []) if isinstance(d, dict) else (d or [])
            by = {str(r.get("pmid")): r for r in recs}
            for pm in batch:
                r = by.get(pm)
                if not r:
                    miss += 1
                    continue
                cb = r.get("cited_by")
                cb = cb.split() if isinstance(cb, str) else (cb or [])
                fh.write(json.dumps({
                    "pmid": pm,
                    "cited_by": cb,
                    "rcr": r.get("relative_citation_ratio"),
                    "is_clinical": r.get("is_clinical"),
                    "citation_count": r.get("citation_count"),
                    "year": r.get("year"),
                }) + "\n")
                got += 1
            print(f"  {min(i+BATCH,n)}/{n}  (got={got} miss={miss})", flush=True)
            time.sleep(0.34)
    print(f"done: {got} fetched, {miss} not in iCite -> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
