/**
 * ProvenanceTag — labels a metric with how it was produced.
 *
 * This is result-level provenance (Simulated / Estimated / Derived /
 * Verified) — distinct from the per-node data_quality vocabulary
 * (observed/estimated/derived/simulated) documented in data/seed/README.md
 * and already shown in node tooltips and selection chips. The two answer
 * different questions: node data_quality says where an *asset's* numbers
 * came from; this says where a *result* number came from — a direct engine
 * field, a capped/uncapped population estimate, or a client-side
 * computation over other fields.
 */
import React from "react";

export type ProvenanceKind = "simulated" | "estimated" | "derived" | "verified" | "muted";

const CLASS_BY_KIND: Record<ProvenanceKind, string> = {
  simulated: "rp-tag rp-tag-simulated",
  estimated: "rp-tag rp-tag-estimated",
  derived: "rp-tag rp-tag-derived",
  verified: "rp-tag rp-tag-verified",
  muted: "rp-tag rp-tag-muted",
};

const LABEL_BY_KIND: Record<ProvenanceKind, string> = {
  simulated: "Simulated",
  estimated: "Estimated",
  derived: "Derived",
  verified: "Verified",
  muted: "—",
};

export default function ProvenanceTag({
  kind,
  label,
}: {
  kind: ProvenanceKind;
  /** Override the default text (e.g. "Estimated · uncapped"). */
  label?: string;
}) {
  return <span className={CLASS_BY_KIND[kind]}>{label ?? LABEL_BY_KIND[kind]}</span>;
}
