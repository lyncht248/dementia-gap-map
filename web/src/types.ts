export interface PaperMetrics {
  citation_count: number | null;
  relative_citation_ratio: number | null;
  apt: number | null;
  is_clinical: boolean | null;
}

/** A trial linked to a paper because its drug targets one of the paper's genes.
 * `via` = the paper gene(s) the drug hits (the reason for the link). */
export interface TrialLink {
  title: string;
  nct_id: string | null;
  drug: string | null;
  via: string[];
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
  /** coarse level of the hierarchy: the paper's disease area (major) */
  area?: string;
  x: number;
  y: number;
  genes: string[];
  /** per-paper pathway groups (derived from the paper's own genes) */
  pathways?: string[];
  pathway_group: string;
  trials: string[];
  /** clickable trial links with the "why" (present on atlas-feed papers) */
  trialLinks?: TrialLink[];
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

/** A disease area (the coarse level of the topic hierarchy). */
export interface AreaInfo {
  id: string;
  label: string;
  color: string;
  paper_count?: number;
}

export interface MapData {
  generated_note?: string;
  disease?: string;
  areas?: AreaInfo[];
  clusters: Cluster[];
  papers: Paper[];
  /** Pruned coupling edges as [i, j] index pairs into `papers`. */
  edges?: [number, number][];
}
