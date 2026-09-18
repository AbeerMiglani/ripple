/**
 * Banner — inline info/warning/success strip. Used for the "cascade did not
 * stabilize" guardrail notice, the map layer-unavailable notice, and the
 * verified-rerun success confirmation.
 */
import React from "react";

export type BannerTone = "info" | "warning" | "success" | "error";

const TONE_STYLE: Record<BannerTone, { bg: string; border: string; fg: string; icon: string }> = {
  info: { bg: "rgba(148,188,227,.08)", border: "rgba(148,188,227,.3)", fg: "#c2dcf3", icon: "var(--rp-accent)" },
  warning: { bg: "rgba(227,163,63,.12)", border: "rgba(227,163,63,.42)", fg: "#f0c98a", icon: "var(--rp-wave-2)" },
  success: { bg: "rgba(79,174,131,.09)", border: "rgba(79,174,131,.4)", fg: "#a9d8c2", icon: "var(--rp-ok)" },
  error: { bg: "rgba(240,68,56,.1)", border: "rgba(240,68,56,.4)", fg: "#ffb4ad", icon: "var(--rp-wave-0)" },
};

export default function Banner({
  tone,
  children,
  action,
}: {
  tone: BannerTone;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  const s = TONE_STYLE[tone];
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 12px",
        background: s.bg,
        border: `1px solid ${s.border}`,
      }}
    >
      <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke={s.icon} strokeWidth={1.8} style={{ flexShrink: 0 }}>
        {tone === "warning" && <path d="M12 3v18M3 12h18" />}
        {tone === "success" && <path d="M20 6L9 17l-5-5" />}
        {tone === "error" && (
          <>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16h.01" />
          </>
        )}
        {tone === "info" && (
          <>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 16v-5M12 8h.01" />
          </>
        )}
      </svg>
      <span style={{ fontSize: 12, color: s.fg, lineHeight: 1.45, flex: "1 1 200px" }}>{children}</span>
      {action}
    </div>
  );
}
