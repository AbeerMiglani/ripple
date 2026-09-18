/**
 * DemoBar — the first thing a judge sees, and ideally the only thing they
 * have to use.
 *
 * Three buttons, one narration line, one replay rail. Pressing a button runs a
 * whole scenario unattended: cascade waves, a held peak, then the derived
 * restoration. The rail stays clickable afterwards so any beat can be revisited
 * without re-running anything.
 */

import React from "react";
import { useDemoStore } from "../demo/demoStore";
import { useSimulationStore } from "../stores/simulationStore";
import { DEMO_PRESETS, presetById, type DemoPreset } from "../demo/presets";
import ProvenanceTag from "./shared/ProvenanceTag";

const BAR: React.CSSProperties = {
  flex: "none",
  background: "var(--rp-bg)",
  borderBottom: "1px solid var(--rp-divider-strong)",
  padding: "10px 18px 11px",
  display: "flex",
  flexDirection: "column",
  gap: 9,
};

const MICRO: React.CSSProperties = {
  fontSize: 10,
  letterSpacing: "0.14em",
  textTransform: "uppercase",
  color: "var(--rp-faint)",
};

function DemoButton({
  preset,
  isActive,
  onRun,
}: {
  preset: DemoPreset;
  isActive: boolean;
  onRun: () => void;
}) {
  return (
    <button
      onClick={onRun}
      className="rp-blueprint"
      style={{
        flex: "1 1 240px",
        minWidth: 200,
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 3,
        padding: "10px 13px",
        cursor: "pointer",
        textAlign: "left",
        fontFamily: "var(--rp-font-body)",
        background: isActive ? "rgba(148,188,227,.14)" : "var(--rp-surface-2)",
        border: `1px solid ${isActive ? "var(--rp-accent)" : "var(--rp-divider-strong)"}`,
        color: "var(--rp-text)",
      }}
    >
      <i className="rp-corner tl" />
      <i className="rp-corner br" />
      <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span
          style={{
            fontFamily: "var(--rp-font-heading)",
            fontWeight: 600,
            fontSize: 15,
            letterSpacing: "0.04em",
            color: isActive ? "var(--rp-accent-soft)" : "var(--rp-text)",
          }}
        >
          {preset.label}
        </span>
        <span style={{ ...MICRO, color: isActive ? "var(--rp-accent)" : "var(--rp-mute)" }}>
          {preset.title}
        </span>
      </span>
      <span style={{ fontSize: 11.5, color: "var(--rp-mute)", lineHeight: 1.45 }}>{preset.headline}</span>
    </button>
  );
}

export default function DemoBar() {
  const activePresetId = useDemoStore((s) => s.activePresetId);
  const phase = useDemoStore((s) => s.phase);
  const beats = useDemoStore((s) => s.beats);
  const beatIndex = useDemoStore((s) => s.beatIndex);
  const error = useDemoStore((s) => s.error);
  const arm = useDemoStore((s) => s.arm);
  const stop = useDemoStore((s) => s.stop);
  const goToBeat = useDemoStore((s) => s.goToBeat);
  const startRecovery = useDemoStore((s) => s.startRecovery);
  const recoveryStartIndex = useDemoStore((s) => s.recoveryStartIndex);
  const hasRunRecovery = useDemoStore((s) => s.hasRunRecovery);
  const totalFailed = useSimulationStore((s) => s.result?.total_failed ?? 0);

  const preset = presetById(activePresetId);
  const currentBeat = beatIndex >= 0 ? beats[beatIndex] : undefined;

  const narration = (() => {
    if (phase === "error") return error ?? "The demo could not be started.";
    if (phase === "arming") return preset ? preset.premise : "Preparing the scenario…";
    if (phase === "awaiting-recovery") {
      return `Collapse complete — ${totalFailed} assets offline. ${preset?.recoveryCaption ?? ""}`.trim();
    }
    if (currentBeat) return currentBeat.caption;
    if (phase === "complete") return preset?.recoveryCaption ?? "Scenario complete.";
    return "Pick a scenario — it runs itself from there. The map and graph stay clickable throughout.";
  })();

  const phaseLabel = (() => {
    switch (phase) {
      case "arming":
        return "Computing cascade";
      case "collapse":
        return "Act 1 · Collapse";
      case "peak":
        return "Act 1 · Peak impact";
      case "awaiting-recovery":
        return "Act 1 complete";
      case "recovery":
        return "Act 2 · Recovery";
      case "complete":
        return "Complete · replay any beat";
      case "error":
        return "Failed";
      default:
        return "Ready";
    }
  })();

  const phaseColor =
    phase === "error"
      ? "var(--rp-wave-0)"
      : phase === "recovery" || phase === "complete"
      ? "var(--rp-ok)"
      : phase === "peak" || phase === "awaiting-recovery"
      ? "var(--rp-wave-2)"
      : phase === "idle"
      ? "var(--rp-mute)"
      : "var(--rp-accent)";

  return (
    <div style={BAR}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "stretch" }}>
        {DEMO_PRESETS.map((p) => (
          <DemoButton key={p.id} preset={p} isActive={p.id === activePresetId} onRun={() => arm(p.id)} />
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", minHeight: 24 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 7,
            padding: "3px 9px",
            border: `1px solid ${phaseColor}`,
            flexShrink: 0,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              display: "block",
              background: phaseColor,
            }}
          />
          <span style={{ ...MICRO, color: phaseColor }}>{phaseLabel}</span>
        </span>

        <span
          style={{
            fontSize: 12.5,
            color: phase === "error" ? "var(--rp-error-soft)" : "var(--rp-text-dim)",
            lineHeight: 1.45,
            flex: 1,
            minWidth: 220,
          }}
        >
          {narration}
        </span>

        {phase === "awaiting-recovery" && recoveryStartIndex >= 0 && (
          <button
            className="rp-btn rp-btn-primary"
            onClick={startRecovery}
            style={{ flexShrink: 0, background: "var(--rp-ok)", borderColor: "var(--rp-ok)", color: "#0b0f14" }}
          >
            ▶ Run restoration
          </button>
        )}

        {(phase === "recovery" || phase === "complete") && (
          <span
            title="The engine models collapse only. Restoration order is derived from the recorded cascade waves — upstream assets first, then whatever depended on them."
            style={{ flexShrink: 0 }}
          >
            <ProvenanceTag kind="derived" label="Recovery · derived from recorded waves" />
          </span>
        )}

        {phase !== "idle" && (
          <button className="rp-btn rp-btn-secondary" style={{ flexShrink: 0 }} onClick={stop}>
            Stop
          </button>
        )}
      </div>

      {beats.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <span style={{ ...MICRO, flexShrink: 0, marginRight: 2 }}>Replay</span>
          {beats.map((beat, i) => {
            const isCurrent = i === beatIndex;
            const isMilestone = beat.kind === "recovery" && beat.milestone !== null;
            // Act 2 is not reachable from the rail until it has been run, so the
            // rail cannot be used to skip the collapse it is meant to follow.
            const locked = beat.kind === "recovery" && !hasRunRecovery;
            const tint =
              beat.kind === "recovery"
                ? "var(--rp-ok)"
                : beat.kind === "peak"
                ? "var(--rp-wave-2)"
                : "var(--rp-wave-0)";
            return (
              <button
                key={i}
                onClick={() => goToBeat(i)}
                disabled={locked}
                title={locked ? "Run the restoration first" : beat.caption}
                style={{
                  padding: "2px 9px",
                  fontSize: 10.5,
                  fontVariantNumeric: "tabular-nums",
                  cursor: locked ? "not-allowed" : "pointer",
                  opacity: locked ? 0.4 : 1,
                  background: isCurrent ? tint : "var(--rp-surface-2)",
                  color: isCurrent ? "#0b0f14" : isMilestone && !locked ? tint : "var(--rp-mute)",
                  border: `1px solid ${isCurrent || (isMilestone && !locked) ? tint : "var(--rp-divider)"}`,
                  fontWeight: isCurrent || isMilestone ? 600 : 400,
                  fontFamily: "var(--rp-font-body)",
                }}
              >
                {beat.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
