/**
 * CollapsiblePane — a side column that starts as a labelled spine and opens to
 * a drag-resizable panel.
 *
 * The topology graph is the clearest example of something a judge may never
 * need but an engineer will not give up: collapsed it costs 34px, expanded it
 * behaves exactly as before. Children unmount while collapsed — cytoscape
 * renders badly into a zero-size container, and it rebuilds from props and the
 * simulation store on remount, so nothing is actually lost by doing so.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";

const SPINE_WIDTH = 34;

export default function CollapsiblePane({
  title,
  defaultOpen = false,
  defaultWidth = 360,
  minWidth = 220,
  maxWidth = 720,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [width, setWidth] = useState(defaultWidth);
  const dragState = useRef<{ startX: number; startWidth: number } | null>(null);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      dragState.current = { startX: e.clientX, startWidth: width };
    },
    [width]
  );

  // Keyboard equivalent of the drag gesture, per the ARIA window-splitter
  // pattern: the handle is a focusable separator, arrow keys nudge it, and
  // Home/End jump to the bounds — so resizing is not pointer-only.
  const RESIZE_STEP = 24;
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        setWidth((w) => Math.max(minWidth, w - RESIZE_STEP));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setWidth((w) => Math.min(maxWidth, w + RESIZE_STEP));
      } else if (e.key === "Home") {
        e.preventDefault();
        setWidth(minWidth);
      } else if (e.key === "End") {
        e.preventDefault();
        setWidth(maxWidth);
      }
    },
    [minWidth, maxWidth]
  );

  // Tracked on the window rather than the handle so a fast drag that outruns
  // the 5px grip does not silently drop the gesture.
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      const next = drag.startWidth + (e.clientX - drag.startX);
      setWidth(Math.min(maxWidth, Math.max(minWidth, next)));
    };
    const onUp = () => {
      dragState.current = null;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [minWidth, maxWidth]);

  if (!open) {
    return (
      <div
        style={{
          width: SPINE_WIDTH,
          flex: "none",
          borderRight: "1px solid var(--rp-divider)",
          background: "var(--rp-surface-2)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <button
          onClick={() => setOpen(true)}
          title={`Show ${title.toLowerCase()}`}
          aria-expanded={false}
          style={{
            flex: 1,
            width: "100%",
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--rp-dim)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "10px 0",
            gap: 10,
          }}
        >
          <span
            style={{
              writingMode: "vertical-rl",
              fontFamily: "var(--rp-font-heading)",
              fontWeight: 600,
              fontSize: 11,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
            }}
          >
            {title} ›
          </span>
        </button>
      </div>
    );
  }

  return (
    <div
      style={{
        width,
        flex: "none",
        borderRight: "1px solid var(--rp-divider)",
        display: "flex",
        flexDirection: "column",
        position: "relative",
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px 8px 16px",
          background: "var(--rp-surface-2)",
          borderBottom: "1px solid var(--rp-divider)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--rp-font-heading)",
            fontWeight: 600,
            fontSize: 12,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--rp-dim)",
          }}
        >
          {title}
        </span>
        <button
          onClick={() => setOpen(false)}
          title={`Hide ${title.toLowerCase()}`}
          aria-expanded
          className="rp-btn rp-btn-ghost"
          style={{ marginLeft: "auto", fontSize: 11, padding: "0 4px" }}
        >
          ‹ Hide
        </button>
      </div>

      <div style={{ flex: 1, position: "relative", minHeight: 0 }}>{children}</div>

      <div
        onPointerDown={onPointerDown}
        onKeyDown={onKeyDown}
        role="separator"
        aria-orientation="vertical"
        aria-label={`Resize ${title.toLowerCase()}`}
        aria-valuenow={width}
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        tabIndex={0}
        title="Drag to resize, or focus and use the arrow keys"
        style={{
          position: "absolute",
          top: 0,
          right: -3,
          bottom: 0,
          width: 6,
          cursor: "col-resize",
          zIndex: 5,
        }}
      />
    </div>
  );
}
