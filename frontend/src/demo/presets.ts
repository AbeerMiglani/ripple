/**
 * The three one-click demo narratives.
 *
 * Each preset is a seeded initial failure plus the words to say while the
 * engine's own result plays back. Nothing here fabricates an outcome: the
 * captions describe what the cascade engine actually produces, and the numbers
 * on screen always come from the simulation the runner just executed.
 *
 * The seed sets were chosen by running the real engine
 * (`backend/app/simulation/cascade.py`, `enforce_edge_semantics=True`) against
 * the bundled seed network and reading the waves it returned. That matters:
 * `data/scripts/demo_scenario.py:120` has to probe up to eight centrality-ranked
 * candidates before it finds one that cascades at all, because most assets in
 * this network carry enough headroom to fail quietly. A demo cannot afford to
 * discover that live, so the `expected` figures below record what each preset
 * measured, and `npm run demo:probe`-style re-verification lives in the repo's
 * verification notes.
 *
 * Why these targets work, from the dependency topology in `data/seed/edges.json`:
 * hospitals need power *and* water, water stations and cell towers need power.
 * Every hospital and water station has two power feeds, so a single substation
 * failure is absorbed; each cell tower has exactly one. Pairing the two
 * substations that share a downstream asset is therefore what actually severs it.
 */

import type { NodeType } from "../types";

export type DemoId = "grid" | "comms" | "hospital";

export interface DemoSeed {
  /** Seed-network UUID. Preserved verbatim by `backend/app/services/ingestion.py`. */
  nodeId: string;
  /** Resolved against the loaded topology when the UUID is not present. */
  nodeName: string;
  /** Last-resort fallback: highest-population asset of this type. */
  nodeType: NodeType;
}

export interface DemoPreset {
  id: DemoId;
  /** Button face, e.g. "Demo 1". */
  label: string;
  /** Subject of the story, e.g. "Grid Substation". */
  title: string;
  /** One line under the button: what this demo is about. */
  headline: string;
  /** Shown while the simulation is being computed. */
  premise: string;
  seeds: DemoSeed[];
  /** Narration per collapse wave; waves beyond the list fall back to a generic line. */
  collapseCaptions: string[];
  /** Narration for the held peak, before recovery begins. */
  peakCaption: string;
  /** Framing line for the recovery phase. */
  recoveryCaption: string;
  /**
   * What the engine produced for this seed set on the bundled network, measured
   * rather than assumed. Used only to flag a drifted dataset in the console —
   * never rendered as a result.
   */
  expected: { waves: number; totalFailed: number };
}

const PS_02 = "7a80d130-da1c-458f-9e5f-fd87f6a61e78";
const PS_04 = "154d5ce3-8990-4eb0-baf3-f5e4539c470a";
const PS_05 = "f8bee0a3-4f08-47cb-9f22-3c98a12a5d07";
const PS_06 = "4a2591cd-1700-4323-9a19-55e1c88dcc19";
const PS_07 = "85387a99-3aa8-42cb-93a2-8cf35214de1d";
const PS_08 = "98f080b0-6524-4d18-9edb-a739ec6d871c";

export const DEMO_PRESETS: DemoPreset[] = [
  {
    id: "grid",
    label: "Demo 1",
    title: "Grid Substation",
    headline: "One grid event, four waves, half the city's services",
    premise:
      "Two substations share every downstream feed in the city core. Losing both at once removes the redundancy that hides single failures.",
    seeds: [
      { nodeId: PS_02, nodeName: "Grid Substation PS-02", nodeType: "power_substation" },
      { nodeId: PS_05, nodeName: "Grid Substation PS-05", nodeType: "power_substation" },
    ],
    collapseCaptions: [
      "Initial shock — both core substations trip. Nothing downstream has noticed yet.",
      "Power severed. Water stations and hospitals lose the last of their feeds together.",
      "The failure crosses services: hospitals on the remaining water mains go dark, and shed load reaches the comms layer.",
      "Comms follow the services that carried them. The cascade has left the grid entirely.",
    ],
    peakCaption: "Peak impact — the cascade has settled. This is the whole blast radius.",
    recoveryCaption:
      "Restoration follows the dependency chain back up: re-energise the substations first, then everything they carried.",
    expected: { waves: 4, totalFailed: 11 },
  },
  {
    id: "comms",
    label: "Demo 2",
    title: "Cell Tower",
    headline: "Two feeders stand between the city and total comms blackout",
    premise:
      "Every cell tower in this network has exactly one power feed. Two substations therefore carry one hundred percent of the comms layer.",
    seeds: [
      { nodeId: PS_07, nodeName: "Grid Substation PS-07", nodeType: "power_substation" },
      { nodeId: PS_04, nodeName: "Grid Substation PS-04", nodeType: "power_substation" },
    ],
    collapseCaptions: [
      "Initial shock — the only two substations feeding the comms layer trip together.",
      "Both cell towers go dark in a single wave. There is no second feed to fall back on.",
      "With comms gone, the load they carried lands on a hospital that was already at its limit.",
    ],
    peakCaption:
      "Peak impact — the entire communications layer is offline from two power failures. This is the bottleneck.",
    recoveryCaption:
      "The towers cannot come back before their feeders do. Restoration is strictly bottom-up.",
    expected: { waves: 3, totalFailed: 7 },
  },
  {
    id: "hospital",
    label: "Demo 3",
    title: "Hospital",
    headline: "A critical node with no third feed",
    premise:
      "Hospital MC-03 draws power from exactly two substations. The rest of the city is untouched by what happens to them.",
    seeds: [
      { nodeId: PS_08, nodeName: "Grid Substation PS-08", nodeType: "power_substation" },
      { nodeId: PS_06, nodeName: "Grid Substation PS-06", nodeType: "power_substation" },
    ],
    collapseCaptions: [
      "Initial shock — both substations feeding the hospital trip.",
      "The hospital loses power outright. Its water main is fine; that is not what took it offline.",
      "Shed load pushes the nearest cell tower past its threshold. The strain stays local.",
    ],
    peakCaption:
      "Peak impact — tightly contained, and a critical service is in it. Small cascade, worst possible members.",
    recoveryCaption:
      "A short chain restores quickly: two feeders back, and the critical node returns with them.",
    expected: { waves: 3, totalFailed: 4 },
  },
];

export function presetById(id: DemoId | null): DemoPreset | null {
  if (!id) return null;
  return DEMO_PRESETS.find((p) => p.id === id) ?? null;
}

/**
 * Turns a preset's seeds into real node ids against the loaded topology.
 *
 * UUID first (the bundled seed network keeps the ids in
 * `data/seed/nodes.geojson`), then exact name, then the highest-population
 * asset of the right type that has not already been picked. Anything
 * unresolvable is dropped rather than sent to the API, which rejects unknown
 * ids for the whole run.
 */
export function resolveSeedNodeIds(
  preset: DemoPreset,
  nodes: { id: string; name: string; display_name?: string; node_type: NodeType; population_served: number }[]
): string[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const byName = new Map<string, string>();
  for (const n of nodes) {
    byName.set(n.name, n.id);
    if (n.display_name) byName.set(n.display_name, n.id);
  }

  const picked: string[] = [];
  const taken = new Set<string>();

  for (const seed of preset.seeds) {
    let id: string | undefined;

    if (byId.has(seed.nodeId)) {
      id = seed.nodeId;
    } else {
      id = byName.get(seed.nodeName);
    }

    if (!id || taken.has(id)) {
      const candidate = nodes
        .filter((n) => n.node_type === seed.nodeType && !taken.has(n.id))
        .sort((a, b) => b.population_served - a.population_served)[0];
      id = candidate?.id;
    }

    if (id && !taken.has(id)) {
      taken.add(id);
      picked.push(id);
    }
  }

  return picked;
}
