"""Track A end-to-end pipeline.

Run from the track folder:

    python run.py                 # ingest the whole dementia+GWAS field
    python run.py --max-papers 500   # cap the corpus for a quick test run

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
from .ingest import icite
from .network import edges as network_edges
from .normalize import papers as normalize_papers
from .score import trajectories as score_trajectories


def run(max_papers: int, log=print) -> None:
    bundle = corpus_ingest.collect_field(config.SEARCH_TERM, max_papers, log=log)
    corpus = bundle["corpus"]
    log(f"[corpus] final corpus: {len(corpus)} papers")

    log("[metrics] fetching iCite metrics")
    metrics = icite.get_metrics(corpus)

    papers = {
        pmid: normalize_papers.build_paper_record(
            pmid, bundle["summaries"].get(pmid), metrics.get(pmid)
        )
        for pmid in corpus
    }

    log("[network] building coupling (refs) + co-citation (cited-by) edges")
    edge_bundle = network_edges.build_edges(corpus, bundle["refs"], bundle["citers"])
    edge_records = network_edges.to_export_records(edge_bundle)
    log(
        f"[network] {len(edge_bundle['coupling'])} coupling + "
        f"{len(edge_bundle['cocitation'])} co-citation edges"
    )

    topics = cluster_topics.cluster(corpus, edge_bundle["blended"], papers, log=log)
    topics, trajectories = score_trajectories.score_topics(topics, papers, log=log)

    write_outputs.write_all(papers, edge_records, topics, trajectories, log=log)
    log("[done] Track A pipeline complete")


def main() -> None:
    ap = argparse.ArgumentParser(description="Track A topic-dynamics pipeline")
    ap.add_argument(
        "--max-papers",
        type=int,
        default=config.MAX_PAPERS,
        help="cap corpus size for testing (0 = whole field, the default)",
    )
    args = ap.parse_args()
    run(max_papers=args.max_papers)


if __name__ == "__main__":
    main()
