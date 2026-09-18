/**
 * Full-panel overlays for the map area: no simulation yet, a run in
 * progress, and an unreachable engine. Absolutely positioned over whatever
 * the map is currently showing (matches the mockup's three states), so the
 * last good render stays visible underneath a translucent scrim.
 */
import React from "react";

function Frame({
  children,
  borderColor,
  bg,
  width = 400,
}: {
  children: React.ReactNode;
  borderColor?: string;
  bg?: string;
  width?: number;
}) {
  return (
    <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", background: "rgba(11,15,20,.65)", zIndex: 5 }}>
      <div
        className="rp-blueprint"
        style={{
          width: `min(${width}px, 88%)`,
          padding: 22,
          background: bg ?? "rgba(15,20,25,.97)",
          borderColor,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <i className="rp-corner tl" />
        <i className="rp-corner tr" />
        <i className="rp-corner bl" />
        <i className="rp-corner br" />
        {children}
      </div>
    </div>
  );
}

export function EmptyMapState({ onRunDemo }: { onRunDemo?: () => void }) {
  return (
    <Frame>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, alignItems: "center", textAlign: "center" }}>
        <svg width={30} height={30} viewBox="0 0 24 24" fill="none" stroke="var(--rp-accent)" strokeWidth={1.5}>
          <circle cx="12" cy="12" r="1.6" fill="var(--rp-accent)" stroke="none" />
          <circle cx="12" cy="12" r="5.2" opacity={0.6} />
          <circle cx="12" cy="12" r="9.4" opacity={0.3} />
        </svg>
        <h4 style={{ fontSize: 21 }}>Start with a demo</h4>
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--rp-text-dim)", lineHeight: 1.55 }}>
          Press Demo 1, 2 or 3 at the top — each one runs a full cascade and its recovery on its
          own. Or click assets here to pick your own failure points and simulate them.
        </p>
        {onRunDemo && (
          <button className="rp-btn rp-btn-primary" onClick={onRunDemo}>
            Run demo scenario
          </button>
        )}
      </div>
    </Frame>
  );
}

export function LoadingMapState({ waveLabel, jobId }: { waveLabel?: string; jobId?: string }) {
  return (
    <Frame width={380}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <svg width={17} height={17} viewBox="0 0 24 24" fill="none" stroke="var(--rp-accent)" strokeWidth={2} className="rp-spin">
          <path d="M12 3a9 9 0 019 9" strokeLinecap="round" />
          <circle cx="12" cy="12" r="9" opacity={0.22} />
        </svg>
        <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 16 }}>
          Running cascade simulation
        </span>
      </div>
      <div style={{ height: 3, background: "rgba(148,188,227,.15)" }}>
        <div style={{ width: "62%", height: 3, background: "var(--rp-accent)" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--rp-mute)", fontVariantNumeric: "tabular-nums" }}>
        <span>Motter–Lai overload engine{waveLabel ? ` · ${waveLabel}` : ""}</span>
        {jobId && <span>job {jobId}</span>}
      </div>
      <p style={{ margin: 0, fontSize: 11.5, color: "var(--rp-faint)", lineHeight: 1.5 }}>
        Deterministic run — the same seed and initial failure always reproduce this result.
      </p>
    </Frame>
  );
}

export function ErrorMapState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <Frame width={430} bg="rgba(21,14,14,.97)" borderColor="rgba(240,68,56,.45)">
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <svg width={19} height={19} viewBox="0 0 24 24" fill="none" stroke="var(--rp-wave-0)" strokeWidth={1.6}>
          <path d="M10.3 3.9L2.4 17.1A2 2 0 004.1 20h15.8a2 2 0 001.7-2.9L13.7 3.9a2 2 0 00-3.4 0z" />
          <path d="M12 9v4M12 17h.01" />
        </svg>
        <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 17, color: "var(--rp-error-soft)" }}>
          Simulation engine unreachable
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 12.5, color: "#d6b6b3", lineHeight: 1.55 }}>
        {message ?? "The request to run this simulation failed. No partial cascade has been written — the last completed result is retained below."}
      </p>
      {onRetry && (
        <div style={{ display: "flex", gap: 8 }}>
          <button className="rp-btn rp-btn-danger" onClick={onRetry}>
            Retry simulation
          </button>
        </div>
      )}
    </Frame>
  );
}
