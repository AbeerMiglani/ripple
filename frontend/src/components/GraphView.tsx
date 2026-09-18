import React, { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import { InfraNode, InfraEdge, NODE_COLORS } from "../types";
import { useUIStore } from "../stores/uiStore";
import { useSimulationStore } from "../stores/simulationStore";

interface GraphViewProps {
  nodes: InfraNode[];
  edges: InfraEdge[];
}

export default function GraphView({ nodes, edges }: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  const selectedNodeIds = useUIStore((s) => s.selectedNodeIds);
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId);
  const toggleNodeSelection = useUIStore((s) => s.toggleNodeSelection);
  const setHoveredNode = useUIStore((s) => s.setHoveredNode);
  const failedNodeIds = useSimulationStore((s) => s.failedNodeIds);
  const restoredNodeIds = useSimulationStore((s) => s.restoredNodeIds);
  const savedNodeIds = useSimulationStore((s) => s.savedNodeIds);
  const mode = useUIStore((s) => s.mode);
  const redundancyNodes = useUIStore((s) => s.redundancyNodes);

  // Initialize Cytoscape once
  useEffect(() => {
    if (!containerRef.current) return;

    cyRef.current = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "data(color)",
            width: 24,
            height: 24,
            "font-size": 8,
            color: "#e6edf3",
            "text-valign": "bottom",
            "text-margin-y": 4,
          },
        },
        {
          selector: "edge",
          style: {
            width: 2,
            "line-color": "#4a5c70",
            "target-arrow-color": "#4a5c70",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
        {
          selector: ".failed",
          style: {
            "background-color": "#f04438",
            "border-width": 2,
            "border-color": "#7f1d1d",
          },
        },
        {
          // Brought back during a demo's recovery phase.
          selector: ".restored",
          style: {
            "background-color": "#4fae83", // --rp-ok
            "border-width": 2,
            "border-color": "#d6f5e4",
          },
        },
        {
          // Kept online by an applied intervention.
          selector: ".saved",
          style: {
            "border-width": 3,
            "border-color": "#7fd0a8", // --rp-teal-bright
          },
        },
        {
          selector: ".selected",
          style: {
            "border-width": 3,
            "border-color": "#94bce3", // --rp-accent
          },
        },
        {
          selector: ".redundancy",
          style: {
            "border-width": 3,
            "border-color": "#5cb2a6", // --rp-teal
          },
        },
        {
          selector: ".hovered",
          style: {
            "border-width": 2,
            "border-color": "#e3a33f", // --rp-wave-2
          },
        },
      ],
      layout: { name: "concentric" },
    });

    cyRef.current.on("tap", "node", (evt) => {
      const state = useUIStore.getState();
      if (state.mode === "add_redundancy") {
        state.addRedundancyNode(evt.target.id());
      } else {
        state.toggleNodeSelection(evt.target.id());
      }
    });

    cyRef.current.on("mouseover", "node", (evt) => {
      setHoveredNode(evt.target.id());
    });
    cyRef.current.on("mouseout", "node", () => {
      setHoveredNode(null);
    });

    return () => {
      cyRef.current?.destroy();
    };
  }, [toggleNodeSelection, setHoveredNode]);

  // Update elements when topology changes
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const cyNodes = nodes.map((n) => {
      const [r, g, b] = NODE_COLORS[n.node_type] || [148, 163, 184];
      return {
        group: "nodes" as const,
        data: {
          id: n.id,
          label: n.name,
          color: `rgb(${r},${g},${b})`,
        },
      };
    });

    const cyEdges = edges.map((e) => ({
      group: "edges" as const,
      data: {
        id: e.id,
        source: e.source_id,
        target: e.target_id,
      },
    }));

    cy.elements().remove();
    cy.add([...cyNodes, ...cyEdges]);
    cy.layout({ name: "concentric", animate: false }).run();
  }, [nodes, edges]);

  // Update classes for selection, hover, and failure states
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    cy.nodes().forEach((n) => {
      const id = n.id();
      n.toggleClass("failed", failedNodeIds.has(id));
      n.toggleClass("restored", restoredNodeIds.has(id));
      n.toggleClass("saved", savedNodeIds.has(id));
      n.toggleClass("selected", mode === "default" && selectedNodeIds.has(id));
      n.toggleClass("redundancy", mode === "add_redundancy" && redundancyNodes.includes(id));
      n.toggleClass("hovered", id === hoveredNodeId);
    });
  }, [selectedNodeIds, hoveredNodeId, failedNodeIds, restoredNodeIds, savedNodeIds, mode, redundancyNodes]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%", background: "var(--rp-bg)" }} />;
}
