"""Track A end-to-end pipeline.

Run from the track folder:

    python run.py --max-papers 300

Produces the four handoff files in data/processed/topic-dynamics/:
    papers.jsonl, paper_edges.jsonl, topic_clusters.jsonl, topic_trajectories.jsonl

Every API response is cached under data/raw/topic-dynamics/cache, so re-runs
are fast and offline.
"""

from __future__ import annotations

import argparse

from . import config
from .cluster import topics as cluster_topics
from .exports import write_outputs
from .ingest import corpus as corpus_ingest
from .ingest import icite, pubmed, seeds
from .network import edges as network_edges
from .normalize import papers as normalize_papers
from .score import trajectories as score_trajectories


def run(max_papers: int, extra_search: bool, log=print) -> None:
    seed_pmids = seeds.get_seed_pmids()
    if extra_search:
        found = pubmed.esearch(config.SEARCH_TERM, config.SEARCH_RETMAX)
        seed_pmids = list(dict.fromkeys(seed_pmids + found))
        log(f"[seeds] {len(seed_pmids)} seeds (incl. {len(found)} from esearch)")
    else:
        log(f"[seeds] {len(seed_pmids)} manual seeds")

    bundle = corpus_ingest.build_corpus(seed_pmids, max_papers, log=log)
    corpus = bundle["corpus"]

    log("[metrics] fetching iCite metrics")
    metrics = icite.get_metrics(corpus)

    papers = {
        pmid: normalize_papers.build_paper_record(
            pmid, bundle["summaries"].get(pmid), metrics.get(pmid)
        )
        for pmid in corpus
    }

    log("[network] building coupling + co-citation edges")
    edge_bundle = network_edges.build_edges(corpus, bundle["refs"], bundle["citers"])
    edge_records = network_edges.to_export_records(edge_bundle)
    log(f"[network] {len(edge_records)} edges")

    topics = cluster_topics.cluster(corpus, edge_bundle["blended"], papers, log=log)
    topics, trajectories = score_trajectories.score_topics(topics, papers, log=log)

    write_outputs.write_all(papers, edge_records, topics, trajectories, log=log)
    log("[done] Track A pipeline complete")


def main() -> None:
    ap = argparse.ArgumentParser(description="Track A topic-dynamics pipeline")
    ap.add_argument("--max-papers", type=int, default=config.MAX_PAPERS)
    ap.add_argument(
        "--no-search",
        action="store_true",
        help="use only manual/Track-B seeds, skip the broad PubMed esearch",
    )
    args = ap.parse_args()
    run(max_papers=args.max_papers, extra_search=not args.no_search)


if __name__ == "__main__":
    main()
