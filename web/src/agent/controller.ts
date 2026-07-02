// Adapter: turns declarative agent intents (brief §5) into the app's state.
// Re-targeted for main's Theme-Atlas frontend: AtlasMap exposes only
// clearSelection/resetView, so the agent drives the SELECTION FEED (setSelected)
// + reset today; on-canvas highlight/zoom are noted as not-yet-supported until
// AtlasMap grows an imperative API.
import type { Cluster, Paper } from "../types";
import type { AtlasMapHandle } from "../components/AtlasMap";
import type { AgentController, FilterPatch, MapState } from "./types";

export interface ControllerDeps {
  getPapers: () => Paper[];
  getClusters: () => Cluster[];
  getSelectedIds: () => string[];
  getYearRange: () => [number, number];
  /** Fallback (atlas not ready): resolve paper_ids -> records into the feed. */
  setSelectedByIds: (ids: string[]) => void;
  clearSelection: () => void;
  resetView: () => void;
  setYearRange: (r: [number, number]) => void;
  /** The atlas canvas handle for on-screen select / highlight / zoom by id. */
  atlas: () => AtlasMapHandle | null;
}

export function createController(deps: ControllerDeps): AgentController {
  const knownIds = () => new Set(deps.getPapers().map((p) => p.paper_id));
  const validIds = (ids: string[]) => {
    const known = knownIds();
    return ids.filter((id) => known.has(id));
  };

  return {
    selectPapers(paperIds) {
      const valid = validIds(paperIds);
      const a = deps.atlas();
      if (a) a.selectByIds(valid);
      else deps.setSelectedByIds(valid);
      return { selected: valid.length };
    },

    highlightPapers(paperIds) {
      const valid = validIds(paperIds);
      deps.atlas()?.highlightByIds(valid);
      return { highlighted: valid.length };
    },

    clearSelection() {
      deps.clearSelection();
    },

    clearHighlight() {
      deps.atlas()?.clearHighlight();
    },

    zoomToPapers(paperIds) {
      const valid = validIds(paperIds);
      deps.atlas()?.zoomToPapers(valid);
      return { zoomed: valid.length };
    },

    zoomToCommunity(topicId) {
      const cluster = deps.getClusters().find((c) => c.topic_id === topicId);
      if (!cluster) return { error: `no theme with topic_id '${topicId}'` };
      const members = deps
        .getPapers()
        .filter((p) => p.cluster_id === topicId)
        .map((p) => p.paper_id);
      const a = deps.atlas();
      if (a) {
        a.selectByIds(members);
        a.zoomToPapers(members);
      } else deps.setSelectedByIds(members);
      return { topic_id: topicId, members: members.length };
    },

    setFilters(patch: FilterPatch) {
      if (patch.yearRange) {
        const years = deps.getPapers().map((p) => p.year);
        const min = Math.min(...years);
        const max = Math.max(...years);
        const lo = Math.max(min, Math.min(patch.yearRange[0], max));
        const hi = Math.min(max, Math.max(patch.yearRange[1], min));
        deps.setYearRange([Math.min(lo, hi), Math.max(lo, hi)]);
      }
      return {
        pathway_groups: patch.pathway_groups ?? [],
        yearRange: deps.getYearRange(),
      };
    },

    resetView() {
      deps.resetView();
    },

    focusEntity(entity) {
      const papers = deps.getPapers();
      let by = "none";
      let ids: string[] = [];
      if (entity.gene) {
        const g = entity.gene.toUpperCase();
        ids = papers
          .filter((p) => p.genes.some((x) => x.toUpperCase() === g))
          .map((p) => p.paper_id);
        by = "paper.genes";
      } else if (entity.pathway_group) {
        ids = papers
          .filter((p) => p.pathway_group === entity.pathway_group)
          .map((p) => p.paper_id);
        by = "paper.pathway_group";
      } else if (entity.variant) {
        by = "unsupported_use_query_data";
      }
      if (ids.length) {
        const a = deps.atlas();
        if (a) {
          a.selectByIds(ids);
          a.zoomToPapers(ids);
        } else deps.setSelectedByIds(ids);
      }
      return { resolved: ids.length, paperIds: ids.slice(0, 200), by };
    },

    getState(): MapState {
      const selectedIds = deps.getSelectedIds();
      return {
        selectedIds,
        highlightedIds: [],
        visibleCount: deps.getPapers().length,
        totalPapers: deps.getPapers().length,
        transform: { scale: 1, tx: 0, ty: 0 },
        filters: { pathway_groups: [], yearRange: deps.getYearRange() },
      };
    },
  };
}
