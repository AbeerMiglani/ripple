/**
 * MapView — MapLibre GL JS basemap + deck.gl overlay layers.
 *
 * Renders infrastructure nodes as colored circles and edges as lines,
 * colored by edge_type. Failed nodes pulse red during cascade animation.
 * Overlays the mockup's empty/loading/error states and a legend + road
 * layer toggle, all on top of the real OpenStreetMap basemap (kept as-is —
 * this network is georeferenced to real Manipal coordinates, unlike the
 * design mockup's synthetic "no basemap" network view).
 */

import { useEffect, useRef, useCallback, useState, useMemo } from "react";
import maplibregl from "maplibre-gl";
import { Deck } from "@deck.gl/core";
import { ScatterplotLayer, LineLayer } from "@deck.gl/layers";
import type { InfraNode, InfraEdge, EdgeType } from "../types";
import { NODE_COLORS, NODE_LABELS, FAILED_COLOR, SELECTED_COLOR } from "../types";
import { useUIStore } from "../stores/uiStore";
import { useSimulationStore } from "../stores/simulationStore";
import { EmptyMapState, LoadingMapState, ErrorMapState } from "./shared/MapStateOverlay";
import Banner from "./shared/Banner";
import { useNodeLookup } from "../hooks/useNodeLookup";

interface MapViewProps {
  nodes: InfraNode[];
  edges: InfraEdge[];
}

type EdgeRow = InfraEdge & {
  sourcePos: [number, number];
  targetPos: [number, number];
  src: InfraNode;
  tgt: InfraNode;
};

// Manipal center coordinates
const INITIAL_VIEW = {
  longitude: 74.789,
  latitude: 13.35,
  zoom: 14.5,
  pitch: 0,
  bearing: 0,
};

const EDGE_COLORS: Record<EdgeType, [number, number, number, number]> = {
  power_supply: [239, 68, 68, 140],
  water_supply: [59, 130, 246, 150],
  road_link: [100, 116, 139, 110],
  depends_on: [197, 143, 196, 150],
};

/**
 * The tooltip is built as an HTML string, so anything interpolated into it has
 * to be escaped. Asset names arrive from the network topology and, once the
 * OSM importer is enabled, ultimately from OpenStreetMap — data this app does
 * not control.
 */
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function titleCase(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "Unknown";
}

const EDGE_LABELS: Record<EdgeType, string> = {
  power_supply: "Power supply",
  water_supply: "Water supply",
  road_link: "Road link",
  depends_on: "Dependency",
};

export default function MapView({ nodes, edges }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const deckRef = useRef<Deck | null>(null);

  const selectedNodeIds = useUIStore((s) => s.selectedNodeIds);
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId);
  const toggleNodeSelection = useUIStore((s) => s.toggleNodeSelection);
  const setHoveredNode = useUIStore((s) => s.setHoveredNode);
  const failedNodeIds = useSimulationStore((s) => s.failedNodeIds);
  // Assets a demo's recovery phase has brought back. Painted green rather than
  // simply reverting to their type colour, so "restored" reads as an event.
  const restoredNodeIds = useSimulationStore((s) => s.restoredNodeIds);
  // Assets an applied intervention kept online. Ringed rather than refilled:
  // they never failed in this run, so their own type colour is still correct —
  // what changed is that they are no longer in the failure set.
  const savedNodeIds = useSimulationStore((s) => s.savedNodeIds);
  const mode = useUIStore((s) => s.mode);
  const redundancyNodes = useUIStore((s) => s.redundancyNodes);
  const result = useSimulationStore((s) => s.result);
  const isRunning = useSimulationStore((s) => s.isRunning);
  const runError = useSimulationStore((s) => s.runError);

  const [showRoads, setShowRoads] = useState(true);
  const [layersOpen, setLayersOpen] = useState(false);

  /**
   * Live cascade state for the tooltip.
   *
   * `getTooltip` is registered once, inside the mount-only effect below, so it
   * closes over whatever `failedNodeIds` was at mount — an empty set. Reading
   * through a ref is what lets a callback with a frozen closure see current
   * state. Without it the tooltip reported the persisted `status` column,
   * which the engine never writes back, so a node rendered red and pulsing
   * mid-cascade still read "Operational" in green.
   */
  const failedNodeIdsRef = useRef<Set<string>>(failedNodeIds);
  useEffect(() => {
    failedNodeIdsRef.current = failedNodeIds;
  }, [failedNodeIds]);

  // Pulsing animation for failed nodes.
  //
  // Runs only while something is actually failed. It used to run unconditionally
  // for the component's entire lifetime, ticking setState at 60fps regardless of
  // whether any node was failed — and because pulseRadius is a dependency of
  // updateLayers below, every one of those frames rebuilt the node lookup,
  // re-filtered the full edge list, and reconstructed all three deck.gl layers,
  // for as long as the map was open.
  const [pulseRadius, setPulseRadius] = useState(1);
  const hasFailedNodes = failedNodeIds.size > 0;
  useEffect(() => {
    if (!hasFailedNodes) {
      setPulseRadius(1);
      return;
    }
    let frame: number;
    let start = performance.now();
    const animate = (now: number) => {
      const t = ((now - start) % 2000) / 2000; // 0-1 over 2 seconds
      setPulseRadius(1 + Math.sin(t * Math.PI) * 0.5);
      frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [hasFailedNodes]);

  // Node lookup for edge rendering and the blast-radius layer.
  const nodeById = useNodeLookup(nodes);

  const visibleEdgeTypes = useMemo(() => new Set(edges.map((e) => e.edge_type)), [edges]);

  // Update deck.gl layers when state changes
  const updateLayers = useCallback(() => {
    if (!deckRef.current) return;
    const lookup = nodeById;

    const visibleNodes = showRoads ? nodes : nodes.filter((n) => n.node_type !== "road_junction");

    const nodeLayer = new ScatterplotLayer<InfraNode>({
      id: "nodes",
      data: visibleNodes,
      getPosition: (d) => [d.lng, d.lat],
      getRadius: (d) => {
        const base = d.node_type === "road_junction" ? 30 : 50;
        if (failedNodeIds.has(d.id)) return base * pulseRadius;
        if (restoredNodeIds.has(d.id)) return base * 1.25;
        return base;
      },
      getFillColor: (d: InfraNode) => {
        if (failedNodeIds.has(d.id)) return [239, 68, 68, 255];
        if (restoredNodeIds.has(d.id)) return [79, 174, 131, 255];
        if (mode === "add_redundancy") {
          if (redundancyNodes.includes(d.id)) return [16, 185, 129, 255]; // Emerald
        } else {
          if (selectedNodeIds.has(d.id)) return [56, 189, 248, 255];
        }
        if (d.id === hoveredNodeId) return [250, 204, 21, 255];
        return NODE_COLORS[d.node_type] || [148, 163, 184, 255];
      },
      getLineColor: (d: InfraNode) => {
        if (mode === "add_redundancy" && redundancyNodes.includes(d.id)) return [255, 255, 255, 255];
        if (mode === "default" && selectedNodeIds.has(d.id)) return [255, 255, 255, 255];
        if (failedNodeIds.has(d.id)) return [127, 29, 29, 255];
        if (savedNodeIds.has(d.id)) return [127, 208, 168, 255];
        if (restoredNodeIds.has(d.id)) return [214, 245, 228, 255];
        return [0, 0, 0, 100];
      },
      getLineWidth: (d: InfraNode) => {
        if (mode === "add_redundancy" && redundancyNodes.includes(d.id)) return 3;
        if (mode === "default" && selectedNodeIds.has(d.id)) return 3;
        if (savedNodeIds.has(d.id)) return 3;
        if (restoredNodeIds.has(d.id)) return 2;
        return 1;
      },
      pickable: true,
      onClick: (info) => {
        if (info.object) {
          if (mode === "add_redundancy") {
            useUIStore.getState().addRedundancyNode(info.object.id);
          } else {
            toggleNodeSelection(info.object.id);
          }
        }
      },
      onHover: (info) => {
        setHoveredNode(info.object ? info.object.id : null);
      },
      radiusUnits: "meters" as const,
      updateTriggers: {
        getRadius: [failedNodeIds, restoredNodeIds, pulseRadius],
        getFillColor: [selectedNodeIds, hoveredNodeId, failedNodeIds, restoredNodeIds, mode, redundancyNodes],
        getLineColor: [selectedNodeIds, failedNodeIds, restoredNodeIds, savedNodeIds, mode, redundancyNodes],
        getLineWidth: [selectedNodeIds, restoredNodeIds, savedNodeIds, mode, redundancyNodes],
      },
    });

    // Build edge line data, colored by edge_type (dimmed further when either
    // endpoint is a hidden road junction and roads are off).
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    const edgeData = edges
      .filter((e) => showRoads || e.edge_type !== "road_link")
      .map((e) => {
        const src = lookup.get(e.source_id);
        const tgt = lookup.get(e.target_id);
        if (!src || !tgt) return null;
        if (!visibleIds.has(e.source_id) || !visibleIds.has(e.target_id)) return null;
        return { ...e, sourcePos: [src.lng, src.lat] as [number, number], targetPos: [tgt.lng, tgt.lat] as [number, number], src, tgt };
      })
      .filter(Boolean) as EdgeRow[];

    const edgeLayer = new LineLayer<EdgeRow>({
      id: "edges",
      data: edgeData,
      pickable: false,
      getSourcePosition: (d) => d.sourcePos,
      getTargetPosition: (d) => d.targetPos,
      getColor: (d) => {
        const srcFailed = failedNodeIds.has(d.src.id);
        const tgtFailed = failedNodeIds.has(d.tgt.id);
        if (srcFailed || tgtFailed) return [220, 38, 38, srcFailed && tgtFailed ? 200 : 90];
        return EDGE_COLORS[d.edge_type] ?? [100, 116, 139, 120];
      },
      getWidth: (d) => (d.edge_type === "road_link" ? 1.5 : 2.5),
      updateTriggers: {
        getColor: [failedNodeIds],
      },
    });

    // Blast radius ring for newly failed nodes
    const currentWave = useSimulationStore.getState().currentWave;
    const activeResult = useSimulationStore.getState().result;
    const waves = activeResult ? activeResult.waves : [];
    const blastNodes =
      currentWave >= 0 && currentWave < waves.length
        ? (waves[currentWave].failed_node_ids
            .map((id: string) => lookup.get(id))
            .filter(Boolean) as InfraNode[])
        : [];

    const blastLayer = new ScatterplotLayer<InfraNode>({
      id: "blast-radius",
      data: blastNodes,
      pickable: false,
      getPosition: (d) => [d.lng, d.lat],
      getRadius: 120 * pulseRadius,
      getFillColor: [220, 38, 38, 40],
      radiusUnits: "meters" as const,
      updateTriggers: {
        getRadius: [pulseRadius, currentWave],
      },
    });

    deckRef.current.setProps({ layers: [edgeLayer, blastLayer, nodeLayer] });
  }, [nodes, edges, failedNodeIds, restoredNodeIds, savedNodeIds, selectedNodeIds, hoveredNodeId, pulseRadius, nodeById, toggleNodeSelection, setHoveredNode, mode, redundancyNodes, showRoads]);

  // Initialize MapLibre + deck.gl
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          {
            id: "osm-tiles",
            type: "raster",
            source: "osm",
            minzoom: 0,
            maxzoom: 19,
          },
        ],
      },
      center: [INITIAL_VIEW.longitude, INITIAL_VIEW.latitude],
      zoom: INITIAL_VIEW.zoom,
      antialias: true,
    });

    map.addControl(new maplibregl.NavigationControl(), "top-right");

    const deck = new Deck({
      parent: containerRef.current,
      viewState: INITIAL_VIEW,
      controller: false, // MapLibre handles interaction
      layers: [],
      style: { position: "absolute", top: "0", left: "0", zIndex: 1, pointerEvents: "auto" } as any,
      getCursor: ({ isHovering }) => (isHovering ? "pointer" : "grab"),
      getTooltip: ({ object }: { object?: InfraNode }) => {
        if (!object) return null;
        const typeLabel = NODE_LABELS[object.node_type] || object.node_type;
        const color = NODE_COLORS[object.node_type] || [148, 163, 184];

        // The simulation is the authority on status while a run is loaded;
        // `object.status` is a persisted column the engine never updates.
        const hasFailed = failedNodeIdsRef.current.has(object.id);
        const status = hasFailed ? "Failed" : titleCase(object.status);
        const statusColor = hasFailed ? "var(--rp-wave-0)" : "var(--rp-ok)";

        const name = escapeHtml(object.display_name || object.name || "Unnamed Asset");
        const source = escapeHtml(object.name_source || object.data_source || "synthetic");
        const quality = escapeHtml(object.data_quality || "estimated");

        return {
          html: `
            <div style="font-family: var(--rp-font-body); font-size: var(--rp-text-md); color: var(--rp-text); min-width: 168px; line-height: 1.4;">
              <div style="font-family: var(--rp-font-heading); font-weight: 600; font-size: var(--rp-text-lg); color: var(--rp-text); margin-bottom: 3px;">${name}</div>
              <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
                <span style="display: inline-block; width: 8px; height: 8px; background-color: rgb(${color.join(",")});"></span>
                <span style="color: var(--rp-mute); font-size: var(--rp-text-sm); text-transform: uppercase; font-weight: 600; letter-spacing: 0.05em;">${escapeHtml(typeLabel)}</span>
                <span style="color: ${statusColor}; font-size: var(--rp-text-sm); font-weight: 600; margin-left: auto;">${status}</span>
              </div>
              ${
                object.population_served
                  ? `<div style="font-size: var(--rp-text-sm); color: var(--rp-text-dim);">Serves ${object.population_served.toLocaleString()} people</div>`
                  : ""
              }
              <div style="font-size: var(--rp-text-xs); color: var(--rp-faint); border-top: 1px solid var(--rp-divider); padding-top: 4px; margin-top: 5px;">
                ${source} \u00b7 ${quality}
              </div>
            </div>
          `,
          style: {
            backgroundColor: "var(--rp-surface)",
            border: "1px solid var(--rp-divider-strong)",
            borderRadius: "var(--rp-radius)",
            padding: "8px 12px",
            boxShadow: "0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 4px 6px -4px rgba(0, 0, 0, 0.5)",
            pointerEvents: "none",
            zIndex: "1000",
          },
        };
      },
    });

    // Sync deck.gl viewState with MapLibre camera
    map.on("move", () => {
      const center = map.getCenter();
      deck.setProps({
        viewState: {
          longitude: center.lng,
          latitude: center.lat,
          zoom: map.getZoom(),
          pitch: map.getPitch(),
          bearing: map.getBearing(),
        },
      });
    });

    mapRef.current = map;
    deckRef.current = deck;

    return () => {
      deck.finalize();
      map.remove();
      mapRef.current = null;
      deckRef.current = null;
    };
  }, []);

  // Re-render layers on state change
  useEffect(() => {
    updateLayers();
  }, [updateLayers]);

  const legendNodeTypes = useMemo(() => {
    const seen = new Set(nodes.map((n) => n.node_type));
    return (Object.keys(NODE_LABELS) as (keyof typeof NODE_LABELS)[]).filter((t) => seen.has(t));
  }, [nodes]);

  const showEmpty = !isRunning && !runError && (!result || result.status !== "completed");
  const showLoading = isRunning;
  const showError = !!runError && !isRunning;

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%", position: "relative" }} />

      {/* ---------- layers control (top-right, under nav controls) ---------- */}
      <div
        style={{
          position: "absolute",
          top: 10,
          right: 52,
          zIndex: 4,
          width: 190,
          display: "flex",
          flexDirection: "column",
          gap: 1,
          padding: "7px 8px 8px",
          background: "rgba(11,15,20,.92)",
          border: "1px solid var(--rp-divider-strong)",
        }}
      >
        <button
          onClick={() => setLayersOpen((v) => !v)}
          style={{ display: "flex", alignItems: "center", gap: 7, padding: "2px 2px 5px", background: "transparent", border: "none", cursor: "pointer", textAlign: "left", width: "100%" }}
        >
          <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="var(--rp-accent)" strokeWidth={1.5} style={{ flexShrink: 0 }}>
            <path d="M12 2l9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5" />
          </svg>
          <span style={{ fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--rp-dim)" }}>Layers</span>
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="var(--rp-mute)" strokeWidth={1.6} style={{ flexShrink: 0, marginLeft: "auto", transform: layersOpen ? "rotate(0deg)" : "rotate(-90deg)" }}>
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        {layersOpen && (
          <label
            style={{ display: "flex", alignItems: "center", gap: 7, padding: "5px 6px", cursor: "pointer", borderTop: "1px solid var(--rp-divider)" }}
          >
            <input type="checkbox" checked={showRoads} onChange={(e) => setShowRoads(e.target.checked)} style={{ accentColor: "var(--rp-accent)" }} />
            <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
              <span style={{ fontSize: 11.5, color: "var(--rp-text)" }}>Road junctions</span>
              <span style={{ fontSize: 10, color: "var(--rp-dim)" }}>{nodes.filter((n) => n.node_type === "road_junction").length} nodes</span>
            </span>
          </label>
        )}
      </div>

      {/* ---------- guardrail notice ---------- */}
      {result?.status === "completed" && result.cascade_stabilized === false && (
        <div style={{ position: "absolute", top: 10, left: 10, right: 250, zIndex: 4 }}>
          <Banner tone="warning">
            <strong>Cascade not stabilized.</strong> The run reached the wave guardrail while still
            spreading — this is a bounded, valid snapshot, not a settled end state.
          </Banner>
        </div>
      )}

      {/* ---------- legend (bottom-left) ---------- */}
      <div
        style={{
          position: "absolute",
          left: 10,
          bottom: 10,
          zIndex: 4,
          display: "flex",
          flexDirection: "column",
          gap: 5,
          padding: "7px 10px",
          background: "rgba(11,15,20,.9)",
          border: "1px solid var(--rp-divider)",
        }}
      >
        <span style={{ fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--rp-dim)" }}>Legend</span>
        <div style={{ display: "grid", gridTemplateColumns: "auto auto", gap: "5px 16px" }}>
          {legendNodeTypes.map((t) => {
            const [r, g, b] = NODE_COLORS[t];
            return (
              <div key={t} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <span style={{ width: 9, height: 9, display: "block", background: `rgb(${r},${g},${b})`, border: "1px solid rgba(255,255,255,.3)" }} />
                <span style={{ fontSize: 11, color: "var(--rp-accent-soft)", whiteSpace: "nowrap" }}>{NODE_LABELS[t]}</span>
              </div>
            );
          })}
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ width: 9, height: 9, display: "block", background: "rgb(239,68,68)", border: "1px solid rgba(255,255,255,.3)" }} />
            <span style={{ fontSize: 11, color: "var(--rp-accent-soft)", whiteSpace: "nowrap" }}>Failed</span>
          </div>
        </div>
        {edges.length > 0 && (
          <>
            <div style={{ height: 1, background: "var(--rp-divider)", margin: "2px 0" }} />
            <div style={{ display: "grid", gridTemplateColumns: "auto auto", gap: "5px 16px" }}>
              {(Object.keys(EDGE_LABELS) as EdgeType[])
                .filter((t) => visibleEdgeTypes.has(t))
                .map((t) => {
                  const [r, g, b] = EDGE_COLORS[t];
                  return (
                    <div key={t} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                      <span style={{ width: 12, height: 2, display: "block", background: `rgb(${r},${g},${b})` }} />
                      <span style={{ fontSize: 11, color: "var(--rp-accent-soft)", whiteSpace: "nowrap" }}>{EDGE_LABELS[t]}</span>
                    </div>
                  );
                })}
            </div>
          </>
        )}
      </div>

      {/* ---------- state overlays ---------- */}
      {showEmpty && <EmptyMapState />}
      {showLoading && <LoadingMapState />}
      {showError && <ErrorMapState message={runError ?? undefined} />}
    </div>
  );
}
