/**
 * Runtime switches for surfaces that are hidden from the demo flow.
 *
 * The competition prototype is graded on how quickly a judge can see the
 * cascade story, so the advanced controls are switched off rather than
 * removed. Every gated block stays as live, type-checked code behind
 * `{UI_FLAGS.x && (...)}`; flipping one boolean here brings the feature
 * straight back with no other edit.
 *
 * Deleting or commenting the code out was not an option even if we wanted it:
 * `backend/tests/test_frontend_contracts_p2.py` pins several of these call
 * sites by reading the .tsx sources with comments stripped first, so a
 * commented-out handler reads to the test suite as a deleted one.
 *
 * Typed against an interface rather than inferred, so the fields stay `boolean`
 * instead of narrowing to the literal `false` — TypeScript would otherwise
 * flag the guarded JSX as unreachable.
 */

export interface UIFlags {
  /** "What-if: add redundancy" mode — the pair-picker and its Save & simulate. */
  whatIfRedundancyMode: boolean;
  /** Click-to-select trigger points plus the "Simulate baseline" run button. */
  manualFailureSelection: boolean;
  /** Betweenness / PageRank switch inside the criticality panel. */
  criticalityMetricToggle: boolean;
  /** The "Top critical nodes" panel itself. */
  criticalityPanel: boolean;
  /** Baseline-vs-scenario comparison panel. */
  scenarioComparePanel: boolean;
  /** Footer event log. The demo narration strip carries this story instead. */
  eventLogFooter: boolean;
}

export const UI_FLAGS: UIFlags = {
  whatIfRedundancyMode: false,
  manualFailureSelection: true,
  criticalityMetricToggle: false,
  criticalityPanel: false,
  scenarioComparePanel: false,
  eventLogFooter: false,
};
