/**
 * Zustand store for UI state.
 *
 * Manages node selection (shared between MapView and GraphView)
 * and panel visibility.
 */

import { create } from "zustand";

interface UIState {
  mode: "default" | "add_redundancy";
  networkId: string | null;
  hoveredNodeId: string | null;
  hoveredEdgeId: string | null;
  selectedNodeIds: Set<string>; // Supports multi-select for initial failures
  redundancyNodes: string[]; // Up to 2 nodes for new edge
  
  setMode: (mode: "default" | "add_redundancy") => void;
  setNetworkId: (id: string | null) => void;
  setHoveredNode: (id: string | null) => void;
  setHoveredEdge: (id: string | null) => void;
  toggleNodeSelection: (id: string) => void;
  clearSelection: () => void;
  addRedundancyNode: (id: string) => void;
  clearRedundancyNodes: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  mode: "default",
  networkId: null,
  hoveredNodeId: null,
  hoveredEdgeId: null,
  selectedNodeIds: new Set(),
  redundancyNodes: [],

  setMode: (mode) => set({ mode, redundancyNodes: [] }),
  setNetworkId: (id) => set({ networkId: id, selectedNodeIds: new Set(), redundancyNodes: [] }),
  setHoveredNode: (id) => set({ hoveredNodeId: id }),
  setHoveredEdge: (id) => set({ hoveredEdgeId: id }),
  toggleNodeSelection: (id) => set((state) => {
    if (state.mode === "add_redundancy") {
      // In redundancy mode, we capture them differently
      return state;
    }
    const next = new Set(state.selectedNodeIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return { selectedNodeIds: next };
  }),
  clearSelection: () => set({ selectedNodeIds: new Set() }),
  addRedundancyNode: (id) => set((state) => {
    if (state.redundancyNodes.includes(id)) return state;
    if (state.redundancyNodes.length >= 2) return state; // Only 2 allowed
    return { redundancyNodes: [...state.redundancyNodes, id] };
  }),
  clearRedundancyNodes: () => set({ redundancyNodes: [] })
}));
