import React, { useState } from "react";
import { useCentrality, useNetworkTopology } from "../api/hooks";
import { useUIStore } from "../stores/uiStore";
import Section from "./shared/Section";
import { UI_FLAGS } from "../config/uiFlags";
import { useNodeLookup } from "../hooks/useNodeLookup";

const CriticalityPanel: React.FC = () => {
  const networkId = useUIStore((s) => s.networkId);
  const [metric, setMetric] = useState<"betweenness" | "pagerank">("betweenness");
  // Keyed on (networkId, metric), so switching metrics is a cache lookup
  // after the first fetch rather than a re-fetch every time, and shares the
  // same request-and-error handling every other panel gets from React Query.
  const { data: scores, isLoading } = useCentrality(networkId, metric);
  const { data: topology } = useNetworkTopology(networkId);
  const toggleNodeSelection = useUIStore((s) => s.toggleNodeSelection);
  const selectedNodeIds = useUIStore((s) => s.selectedNodeIds);
  const hoverNode = useUIStore((s) => s.setHoveredNode);

  const nodeLookup = useNodeLookup(topology?.nodes);

  const metricToggle = (
    <div style={{ display: "flex", gap: 4 }}>
      <button className="rp-btn" style={{ fontSize: 10, padding: "2px 7px", background: metric === "betweenness" ? "var(--rp-accent)" : "var(--rp-surface-3)", color: metric === "betweenness" ? "#0b0f14" : "var(--rp-mute)", borderColor: metric === "betweenness" ? "var(--rp-accent)" : "var(--rp-divider)" }} onClick={() => setMetric("betweenness")} title="Betweenness centrality — shortest-path bottlenecks">
        Betweenness
      </button>
      <button className="rp-btn" style={{ fontSize: 10, padding: "2px 7px", background: metric === "pagerank" ? "var(--rp-accent)" : "var(--rp-surface-3)", color: metric === "pagerank" ? "#0b0f14" : "var(--rp-mute)", borderColor: metric === "pagerank" ? "var(--rp-accent)" : "var(--rp-divider)" }} onClick={() => setMetric("pagerank")} title="PageRank — recursive connectivity">
        PageRank
      </button>
    </div>
  );

  if (isLoading || !scores) {
    return (
      <Section title="Top critical nodes" state={isLoading ? "Loading" : "—"}>
        {UI_FLAGS.criticalityMetricToggle && metricToggle}
        {isLoading && <div style={{ marginTop: 8, color: "var(--rp-mute)", fontSize: 12.5 }}>Loading criticality…</div>}
      </Section>
    );
  }

  const top10 = scores.slice(0, 10);
  const maxScore = top10.length > 0 ? (top10[0].score > 0 ? top10[0].score : 1) : 1;

  return (
    <Section title="Top critical nodes">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: "var(--rp-mute)" }}>
          {metric === "betweenness" ? "Shortest-path bottlenecks" : "Recursive connectivity"}
        </span>
        {UI_FLAGS.criticalityMetricToggle && metricToggle}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {top10.map((s, i) => {
          const isSelected = selectedNodeIds.has(s.node_id);
          const percent = (s.score / maxScore) * 100;
          const nodeObj = nodeLookup.get(s.node_id);
          const resolvedName = s.display_name || s.name || nodeObj?.display_name || nodeObj?.name || s.node_id.slice(0, 8);
          const nameSource = s.name_source || nodeObj?.name_source || s.data_source || "synthetic";
          const dataQuality = s.data_quality || nodeObj?.data_quality || "estimated";

          return (
            <div
              key={s.node_id}
              onClick={() => toggleNodeSelection(s.node_id)}
              onMouseEnter={() => hoverNode(s.node_id)}
              onMouseLeave={() => hoverNode(null)}
              title={`${resolvedName} (${s.node_id}) | Source: ${nameSource} | Quality: ${dataQuality}`}
              style={{
                background: isSelected ? "rgba(148,188,227,.14)" : "var(--rp-surface-3)",
                border: isSelected ? "1px solid var(--rp-accent)" : "1px solid transparent",
                padding: "6px 8px",
                cursor: "pointer",
                fontSize: 12,
                position: "relative",
                overflow: "hidden",
              }}
            >
              <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${percent}%`, background: "var(--rp-wave-2)", opacity: 0.15, zIndex: 0 }} />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", position: "relative", zIndex: 1, gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 500 }}>
                    #{i + 1} {resolvedName}
                  </span>
                  <span style={{ fontSize: 9, padding: "1px 4px", background: "var(--rp-surface-2)", color: "var(--rp-mute)", flexShrink: 0 }}>{nameSource}</span>
                  <span style={{ fontSize: 9, padding: "1px 4px", background: "rgba(148,188,227,.15)", color: "var(--rp-accent)", flexShrink: 0 }}>{dataQuality}</span>
                </div>
                <span style={{ color: "var(--rp-wave-2)", fontWeight: "bold", flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{s.score.toFixed(3)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </Section>
  );
};

export default CriticalityPanel;
