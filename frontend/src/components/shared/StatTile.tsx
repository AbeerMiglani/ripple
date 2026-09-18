/**
 * StatTile — one metric in the Impact Summary grid.
 *
 * Mirrors the mockup's blueprint-cornered stat card: label, big value,
 * a one-line sub-caption, and a provenance tag. `loading` renders the
 * value as a shimmering placeholder instead of a number.
 */
import React from "react";
import ProvenanceTag, { ProvenanceKind } from "./ProvenanceTag";

export default function StatTile({
  label,
  value,
  sub,
  provenance,
  color,
  loading = false,
}: {
  label: string;
  value: string;
  sub?: string;
  provenance: ProvenanceKind;
  /** Value color override (e.g. red for a bad number, teal for a good one). */
  color?: string;
  loading?: boolean;
}) {
  return (
    <div
      className="rp-blueprint"
      style={{
        padding: "10px 11px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        background: "var(--rp-surface-3)",
      }}
    >
      <i className="rp-corner tl" />
      <i className="rp-corner br" />
      <span
        style={{
          fontSize: 10,
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: "var(--rp-mute)",
          lineHeight: 1.3,
        }}
      >
        {label}
      </span>
      <span
        className={loading ? "rp-skel" : undefined}
        style={{
          fontFamily: "var(--rp-font-heading)",
          fontWeight: 600,
          fontSize: 27,
          lineHeight: 1,
          fontVariantNumeric: "tabular-nums",
          color: color ?? "var(--rp-text)",
        }}
      >
        {loading ? "····" : value}
      </span>
      {sub && (
        <span style={{ fontSize: 10.5, color: "var(--rp-mute)", lineHeight: 1.35 }}>{sub}</span>
      )}
      <div style={{ marginTop: 1 }}>
        <ProvenanceTag kind={loading ? "muted" : provenance} label={loading ? "Computing" : undefined} />
      </div>
    </div>
  );
}
