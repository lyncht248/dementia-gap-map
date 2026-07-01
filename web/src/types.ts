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
}

export interface MapData {
  generated_note?: string;
  disease?: string;
  clusters: Cluster[];
  papers: Paper[];
}
