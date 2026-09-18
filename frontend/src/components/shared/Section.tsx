/**
 * Section — a collapsible sidebar panel: header (title + state chip +
 * chevron) and a body that toggles open/closed. Used for Impact Summary,
 * Cascade Waves, Recommendation, and Explain This Result, mirroring the
 * mockup's four collapsible sidebar sections.
 */
import React, { useState } from "react";

export default function Section({
  title,
  state,
  defaultOpen = true,
  children,
}: {
  title: string;
  /** Short right-aligned status chip, e.g. "Verified", "No result". */
  state?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ borderBottom: "1px solid var(--rp-divider)" }}>
      <button className="rp-section-header" onClick={() => setOpen((o) => !o)}>
        <span>{title}</span>
        {state && <span className="rp-section-state">{state}</span>}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--rp-faint)"
          strokeWidth={1.6}
          style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)", flexShrink: 0 }}
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && <div style={{ padding: "0 14px 14px" }}>{children}</div>}
    </div>
  );
}
