/**
 * AdvancedDetails — a collapsible drawer for secondary and diagnostic values.
 *
 * The panels used to render engineering telemetry at the same visual weight as
 * the numbers a first-time reader actually needs: raw efficiency decimals,
 * internal field names like `raw_population_affected`, provenance dumps. All of
 * it is worth keeping — an operator checking the engine's working needs it —
 * but none of it should compete with "how many assets are down".
 *
 * Collapsed by default, and state is local: nothing here is worth persisting,
 * and `simulationStore` is deliberately the only writer to localStorage.
 */
import React, { useId, useState } from "react";

interface AdvancedDetailsProps {
  /** Defaults to "Advanced diagnostics". */
  label?: string;
  /** Short hint of what is inside, shown next to the label while collapsed. */
  hint?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

export default function AdvancedDetails({
  label = "Advanced diagnostics",
  hint,
  defaultOpen = false,
  children,
}: AdvancedDetailsProps) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();

  return (
    <div style={{ marginTop: 10, borderTop: "1px solid var(--rp-divider)" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={panelId}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          width: "100%",
          padding: "8px 0 7px",
          background: "none",
          border: "none",
          cursor: "pointer",
          textAlign: "left",
          color: "var(--rp-mute)",
          fontFamily: "var(--rp-font-body)",
          fontSize: "var(--rp-text-sm)",
        }}
      >
        <svg
          width="9"
          height="9"
          viewBox="0 0 12 12"
          aria-hidden="true"
          style={{
            transform: open ? "rotate(0deg)" : "rotate(-90deg)",
            transition: "transform 120ms ease",
            flexShrink: 0,
          }}
        >
          <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.6" fill="none" />
        </svg>
        <span style={{ textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
          {label}
        </span>
        {hint && !open && (
          <span style={{ color: "var(--rp-faint)", fontSize: "var(--rp-text-xs)", marginLeft: "auto" }}>
            {hint}
          </span>
        )}
      </button>
      {open && (
        <div id={panelId} style={{ paddingBottom: 10 }}>
          {children}
        </div>
      )}
    </div>
  );
}
