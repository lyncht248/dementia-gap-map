export interface PaperMetrics {
  citation_count: number | null;
  relative_citation_ratio: number | null;
  apt: number | null;
  is_clinical: boolean | null;
}

export interface Paper {
  paper_id: string;
  pmid: string | null;
  doi: string | null;
  title: string;
  year: number;
  journal: string | null;
  authors: string[];
  cluster_id: string;
  x: number;
  y: number;
  genes: string[];
  pathway_group: string;
  /** Assigned dementia hypothesis id (null if unclassified). */
  hypothesis?: string | null;
  /** Resolved point colour for the hypothesis (or neutral grey if unclassified). */
  hypothesis_color?: string;
  trials: string[];
  metrics: PaperMetrics;
  url?: string;
}

export interface ClusterScores {
  emergence?: number;
  genetic_support?: number;
  functional_support?: number;
  clinical_translation?: number;
  clinical_saturation?: number;
  [k: string]: number | undefined;
}

export interface ClusterEmergence {
  /** composite 0–1 emergence score (burst + growth + influence) */
  score: number;
  /** fraction of the topic's papers from the last 3 years */
  pct_new: number;
  /** recent vs. preceding 3-year publication ratio */
  growth: number;
  /** mean Relative Citation Ratio of the topic's papers */
  mean_rcr: number;
}

export interface Cluster {
  topic_id: string;
  label: string;
  /** Distinguishing gene / method / mechanism specifics; shown when zoomed in. */
  sublabel?: string;
  /** Deterministic TF-IDF term signature the curated label attaches to. */
  signature?: string;
  term_hints?: string[];
  color: string;
  pathway_group: string;
  top_genes: string[];
  trials: string[];
  paper_count: number;
  centroid: { x: number; y: number };
  year_start: number;
  year_end: number;
  scores: ClusterScores;
  emergence?: ClusterEmergence | null;
}

/** Etiological-hypothesis overlay label, placed at the median position of the
 * papers matching that hypothesis — independent of the co-citation clusters. */
export interface Hypothesis {
  id: string;
  label: string;
  color: string;
  paper_count: number;
  /** papers that matched this hypothesis directly (before cluster fill) */
  match_count?: number;
  centroid: { x: number; y: number };
}

export interface MapData {
  generated_note?: string;
  disease?: string;
  clusters: Cluster[];
  hypotheses?: Hypothesis[];
  papers: Paper[];
  /** Pruned coupling edges as [i, j] index pairs into `papers`. */
  edges?: [number, number][];
}
