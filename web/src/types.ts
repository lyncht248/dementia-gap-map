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
  /** coarse (theme-level) group id, or null for unclustered papers */
  coarse_id?: string | null;
  x: number;
  y: number;
  genes: string[];
  pathway_group: string;
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
  color: string;
  /** coarse (theme-level) group id this fine cluster belongs to */
  coarse_id?: string | null;
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

/** A theme-level grouping of several fine clusters, used for the always-on
 *  coarse label tier. Anchored on its largest member's centroid. */
export interface CoarseCluster {
  coarse_id: string;
  label: string;
  color: string;
  centroid: { x: number; y: number };
  paper_count: number;
  fine_ids: string[];
}

/** One naming lens: the same clusters labelled under a different scheme
 *  (theme / pathway / subtype). Overlaid on the base map at runtime. */
export interface LabelLens {
  id: string;
  name: string;
  coarse_clusters: CoarseCluster[];
  /** fine cluster_id -> label */
  fine: Record<string, string>;
  /** fine cluster_id -> coarse_id */
  coarse_of: Record<string, string>;
}

export interface LensFile {
  default: string;
  lenses: LabelLens[];
}

export interface MapData {
  generated_note?: string;
  disease?: string;
  clusters: Cluster[];
  /** Coarse theme groups (always-on labels); fine `clusters` reveal on zoom. */
  coarse_clusters?: CoarseCluster[];
  papers: Paper[];
  /** Pruned coupling edges as [i, j] index pairs into `papers`. */
  edges?: [number, number][];
}
