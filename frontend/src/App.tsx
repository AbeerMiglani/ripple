import React, { useEffect, useMemo, useRef, useState } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
import MapView from "./components/MapView";
import GraphView from "./components/GraphView";
import ControlPanel from "./components/ControlPanel";
import CriticalityPanel from "./components/CriticalityPanel";
import ScenarioCompare from "./components/ScenarioCompare";
import RecommendationPanel from "./components/RecommendationPanel";
import CascadeTimeline from "./components/CascadeTimeline";
import ImpactSummary from "./components/ImpactSummary";
import WaveBreakdown from "./components/WaveBreakdown";
import ExplainPanel from "./components/ExplainPanel";
import DemoBar from "./components/DemoBar";
import DemoRunner from "./demo/DemoRunner";
import CollapsiblePane from "./components/shared/CollapsiblePane";
import { UI_FLAGS } from "./config/uiFlags";
import { useDemoStore } from "./demo/demoStore";
import { useNetworks, useNetworkTopology } from "./api/hooks";
import { useUIStore } from "./stores/uiStore";
import { useSimulationStore } from "./stores/simulationStore";

interface LogEntry {
  t: string;
  msg: string;
  tone: "ok" | "warn" | "err" | "info";
  tag: string;
}

const TONE_COLOR: Record<LogEntry["tone"], string> = {
  ok: "var(--rp-ok)",
  warn: "var(--rp-wave-2)",
  err: "var(--rp-wave-0)",
  info: "var(--rp-accent)",
};
const TONE_ICON: Record<LogEntry["tone"], string> = { ok: "✓", warn: "!", err: "✕", info: "·" };

/**
 * Accent frame around the two panels the demo is narrating. Only while a demo
 * is actually driving the board — a static border would just be chrome.
 */
const STORY_HIGHLIGHT = (active: boolean): React.CSSProperties => ({
  borderLeft: `2px solid ${active ? "var(--rp-accent)" : "transparent"}`,
  transition: "border-color 200ms ease",
});

function timeNow() {
  return new Date().toLocaleTimeString([], { hour12: false });
}

/** Client-side event log: real transitions this session observed, not fabricated data. */
function useEventLog(): LogEntry[] {
  const [log, setLog] = useState<LogEntry[]>([]);
  const result = useSimulationStore((s) => s.result);
  const isRunning = useSimulationStore((s) => s.isRunning);
  const runError = useSimulationStore((s) => s.runError);
  const networkId = useUIStore((s) => s.networkId);
  const { data: topology } = useNetworkTopology(networkId);

  const push = (msg: string, tone: LogEntry["tone"], tag: string) =>
    setLog((prev) => [{ t: timeNow(), msg, tone, tag }, ...prev].slice(0, 20));

  useEffect(() => {
    if (topology) push(`Network loaded — ${topology.nodes.length} assets, ${topology.edges.length} links`, "info", "Seed");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!topology]);

  useEffect(() => {
    if (isRunning) push("Simulation running", "info", "Running");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRunning]);

  useEffect(() => {
    if (runError) push(runError, "err", "Failure");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runError]);

  useEffect(() => {
    if (result?.status === "completed") {
      push(
        `Simulation completed — ${result.total_failed} assets failed across ${result.waves.length} waves`,
        "ok",
        "Simulated"
      );
      if (result.is_population_capped) push("Population estimate capped at the study-area limit", "warn", "Estimated");
      if (result.cascade_stabilized === false) push("Cascade still spreading at the wave guardrail — truncated", "warn", "Truncated");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result?.id, result?.status]);

  return log;
}

const App: React.FC = () => {
  const { data: networks, isLoading: isLoadingNetworks } = useNetworks();
  const networkId = useUIStore((s) => s.networkId);
  const setNetworkId = useUIStore((s) => s.setNetworkId);

  useEffect(() => {
    if (networks && networks.length > 0 && !networkId) {
      setNetworkId(networks[0].id);
    }
  }, [networks, networkId, setNetworkId]);

  const { data: topology, isLoading: isLoadingTopology } = useNetworkTopology(networkId);
  const currentNetwork = useMemo(() => networks?.find((n) => n.id === networkId), [networks, networkId]);

  const result = useSimulationStore((s) => s.result);
  const isRunning = useSimulationStore((s) => s.isRunning);
  const runError = useSimulationStore((s) => s.runError);

  const badge = useMemo(() => {
    if (isRunning) return { text: "Running", color: "var(--rp-accent)" };
    if (runError) return { text: "Run failed", color: "var(--rp-wave-0)" };
    if (!result) return { text: "No scenario", color: "var(--rp-mute)" };
    if (result.status === "failed") return { text: "Run failed", color: "var(--rp-wave-0)" };
    if (result.cascade_stabilized === false) return { text: "Truncated · guardrail", color: "var(--rp-wave-2)" };
    return { text: "Cascade recorded", color: "var(--rp-ok)" };
  }, [isRunning, runError, result]);

  const log = useEventLog();

  const demoPhase = useDemoStore((s) => s.phase);
  const isDemoRunning = demoPhase !== "idle" && demoPhase !== "error" && demoPhase !== "complete";
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Bring the impact panels back into view on every phase change, so the
  // numbers the narration is describing are the ones on screen. They are the
  // first thing in the sidebar, so this is a scroll to the top.
  useEffect(() => {
    if (!isDemoRunning) return;
    sidebarRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [demoPhase, isDemoRunning]);

  if (isLoadingNetworks || isLoadingTopology || !topology) {
    return (
      <div className="rp-root" style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center" }}>
        <p style={{ fontSize: 16, color: "var(--rp-mute)" }}>Loading infrastructure data…</p>
      </div>
    );
  }

  return (
    <div className="rp-root" style={{ width: "100%", height: "100vh", display: "flex", flexDirection: "column" }}>
      {/* Headless: owns the demo run and paces its beats. Renders nothing. */}
      <DemoRunner />

      {/* ---------- one-click demos ---------- */}
      <DemoBar />

      {/* ---------- header ---------- */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "14px 18px",
          flexWrap: "wrap",
          padding: "8px 18px",
          minHeight: 56,
          flex: "none",
          background: "var(--rp-surface)",
          borderBottom: "1px solid var(--rp-divider)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke="var(--rp-accent)" strokeWidth={1.5}>
            <circle cx="13" cy="13" r="2.2" fill="var(--rp-accent)" stroke="none" />
            <circle cx="13" cy="13" r="6" opacity={0.7} />
            <circle cx="13" cy="13" r="10.2" opacity={0.38} />
          </svg>
          <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 19, letterSpacing: "0.06em", lineHeight: 1 }}>
              RIPPLE
            </span>
            <span style={{ fontSize: 10, letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-faint)", lineHeight: 1 }}>
              Cascade simulation · resilience planning
            </span>
          </div>
        </div>

        <div style={{ width: 1, height: 28, background: "var(--rp-divider)" }} />

        <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "5px 10px", background: "var(--rp-surface-2)", border: "1px solid var(--rp-divider)" }}>
          <span style={{ width: 5, height: 5, background: "var(--rp-teal)", display: "block", flexShrink: 0 }} />
          <select
            value={networkId || ""}
            onChange={(e) => setNetworkId(e.target.value)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--rp-text)",
              fontFamily: "var(--rp-font-heading)",
              fontWeight: 600,
              fontSize: 14,
              cursor: "pointer",
            }}
          >
            {networks?.map((n) => (
              <option key={n.id} value={n.id} style={{ background: "var(--rp-surface-2)" }}>
                {n.name}
              </option>
            ))}
          </select>
          <span style={{ fontSize: 10.5, color: "var(--rp-faint)", fontVariantNumeric: "tabular-nums" }}>
            {topology.nodes.length} assets · {topology.edges.length} links
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "4px 10px", border: "1px solid var(--rp-divider)" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", display: "block", flexShrink: 0, background: badge.color }} />
          <span style={{ fontSize: 10, letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-faint)" }}>Scenario</span>
          <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 13.5, color: badge.color }}>{badge.text}</span>
        </div>

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
          <div
            title="All figures in this demo come from a synthetic seed dataset"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              padding: "4px 10px",
              background: "rgba(227,163,63,.1)",
              border: "1px solid rgba(227,163,63,.38)",
            }}
          >
            <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="var(--rp-wave-2)" strokeWidth={1.5}>
              <circle cx="12" cy="12" r="9" />
              <path d="M12 16v-5M12 8h.01" />
            </svg>
            <span style={{ fontSize: 10, letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-wave-2)", fontWeight: 500 }}>
              Demo data · synthetic / estimated
            </span>
          </div>
        </div>
      </header>

      {/* ---------- main ---------- */}
      <main style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Collapsed on load: the graph is for tinkering, the map carries the
            story. It is one click and a drag away for anyone who wants it. */}
        <CollapsiblePane title="Topology" defaultOpen={false} defaultWidth={380}>
          <GraphView nodes={topology.nodes} edges={topology.edges} />
        </CollapsiblePane>

        <div style={{ flex: 1, minWidth: 0, position: "relative", borderRight: "1px solid var(--rp-divider)", display: "flex", flexDirection: "column" }}>
          <div
            style={{
              padding: "8px 16px",
              background: "var(--rp-surface-2)",
              borderBottom: "1px solid var(--rp-divider)",
              fontFamily: "var(--rp-font-heading)",
              fontWeight: 600,
              fontSize: 12,
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              color: "var(--rp-dim)",
            }}
          >
            Geographic Map
          </div>
          <div style={{ flex: 1, overflow: "hidden" }}>
            <MapView nodes={topology.nodes} edges={topology.edges} />
          </div>
          <CascadeTimeline />
        </div>

        {/* Impact and waves lead: they are what the demo is narrating, and they
            should be readable without scrolling. The manual controls follow. */}
        <div
          ref={sidebarRef}
          style={{ width: 372, maxWidth: "100%", flex: "none", display: "flex", flexDirection: "column", background: "var(--rp-surface)", overflowY: "auto" }}
        >
          <div style={STORY_HIGHLIGHT(isDemoRunning)}>
            <ImpactSummary />
            <WaveBreakdown />
          </div>
          <ControlPanel />
          <RecommendationPanel />
          <ExplainPanel />
          {UI_FLAGS.criticalityPanel && <CriticalityPanel />}
          {UI_FLAGS.scenarioComparePanel && <ScenarioCompare />}
        </div>
      </main>

      {/* ---------- footer: event log ---------- */}
      {UI_FLAGS.eventLogFooter && (
      <footer style={{ flex: "none", background: "var(--rp-bg)", borderTop: "1px solid var(--rp-divider)", padding: "9px 16px 11px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 7 }}>
          <span style={{ fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--rp-dim)" }}>Event log</span>
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 7, fontSize: 10.5, color: badge.color }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", display: "block", background: badge.color }} />
            {currentNetwork?.name ?? "—"}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "rgba(148,188,227,.1)", maxHeight: 108, overflow: "auto" }}>
          {log.length === 0 ? (
            <div style={{ padding: "6px 10px", background: "var(--rp-surface)", fontSize: 11.5, color: "var(--rp-mute)" }}>Nothing logged yet.</div>
          ) : (
            log.map((e, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 11, padding: "6px 10px", background: "var(--rp-surface)" }}>
                <span style={{ fontSize: 10.5, color: "var(--rp-faint)", fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{e.t}</span>
                <span
                  style={{
                    width: 16,
                    height: 16,
                    display: "grid",
                    placeItems: "center",
                    flexShrink: 0,
                    color: TONE_COLOR[e.tone],
                    border: `1px solid ${TONE_COLOR[e.tone]}`,
                    fontSize: 10,
                    lineHeight: 1,
                  }}
                >
                  {TONE_ICON[e.tone]}
                </span>
                <span style={{ fontSize: 12, color: "var(--rp-text-dim)", lineHeight: 1.4 }}>{e.msg}</span>
                <span style={{ marginLeft: "auto", flexShrink: 0, fontSize: 10, letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-faint)" }}>
                  {e.tag}
                </span>
              </div>
            ))
          )}
        </div>
      </footer>
      )}
    </div>
  );
};

export default App;
