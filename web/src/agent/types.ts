// Shared types for the agent control surface.
import type { Point, Transform } from "../lib/geometry";

/** Imperative handle exposed by MapCanvas for agent-driven camera control. */
export interface MapHandle {
  zoomToPoints(points: Point[], padding?: number): void;
  zoomToPapers(paperIds: string[], padding?: number): void;
  resetView(): void;
  getTransform(): Transform;
  getSize(): { w: number; h: number };
}

export interface FilterPatch {
  pathway_groups?: string[];
  yearRange?: [number, number];
}

export interface MapState {
  selectedIds: string[];
  highlightedIds: string[];
  visibleCount: number;
  totalPapers: number;
  transform: Transform;
  filters: {
    pathway_groups: string[];
    yearRange: [number, number];
  };
}

/**
 * The declarative command surface the agent drives (brief §5). The agent emits
 * intent; the adapter (built in App.tsx) maps each call onto existing React
 * state setters + the MapCanvas handle. Methods that resolve entities to papers
 * return the resolved paper_ids so the agent can narrate what it acted on.
 */
export interface AgentController {
  selectPapers(paperIds: string[]): { selected: number };
  highlightPapers(paperIds: string[]): { highlighted: number; note?: string };
  clearSelection(): void;
  clearHighlight(): void;
  zoomToPapers(paperIds: string[]): { zoomed: number; note?: string };
  zoomToCommunity(
    topicId: string
  ): { topic_id: string; members: number; note?: string } | { error: string };
  setFilters(patch: FilterPatch): { pathway_groups: string[]; yearRange: [number, number] };
  resetView(): void;
  /** Resolve a gene symbol / rsID / pathway group to papers, then select+zoom. */
  focusEntity(entity: { gene?: string; variant?: string; pathway_group?: string }): {
    resolved: number;
    paperIds: string[];
    by: string;
  };
  getState(): MapState;
}
