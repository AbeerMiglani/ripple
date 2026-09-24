import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { EdgeType, InfraEdge, InfraNode, NodeType } from "../types";
import { mapEdgesToDraw, mapNodesToDraw } from "./roadVisibility";

function node(id: string, node_type: NodeType): InfraNode {
  return {
    id,
    name: id,
    node_type,
    lat: 13.35,
    lng: 74.79,
    capacity: 100,
    current_load: 0,
    failure_threshold: 1,
    population_served: 0,
    status: "operational",
  };
}

function edge(source_id: string, target_id: string, edge_type: EdgeType): InfraEdge {
  return { id: `${source_id}-${target_id}`, source_id, target_id, edge_type, weight: 1, capacity: 100, is_bidirectional: false };
}

const SUB = node("SUB", "power_substation");
const HOSP = node("HOSP", "hospital");
const J1 = node("J1", "road_junction");
const J2 = node("J2", "road_junction");
const NODES = [SUB, HOSP, J1, J2];
const EDGES = [
  edge("SUB", "HOSP", "power_supply"),
  edge("J1", "J2", "road_link"),
  edge("HOSP", "J1", "road_link"),
  edge("SUB", "HOSP", "road_link"), // a road straight between two facilities
];
const ids = (xs: { id: string }[]) => xs.map((x) => x.id).sort();

describe("mapNodesToDraw", () => {
  it("hides healthy road junctions when roads are off", () => {
    expect(ids(mapNodesToDraw(NODES, false, new Set()))).toEqual(["HOSP", "SUB"]);
  });

  it("always draws a pinned junction, e.g. one that failed or was selected", () => {
    expect(ids(mapNodesToDraw(NODES, false, new Set(["J1"])))).toEqual(["HOSP", "J1", "SUB"]);
  });

  it("draws everything when roads are on", () => {
    expect(ids(mapNodesToDraw(NODES, true, new Set()))).toEqual(["HOSP", "J1", "J2", "SUB"]);
  });
});

describe("mapEdgesToDraw", () => {
  it("keeps utility links and drops every road link when no junction is drawn", () => {
    const drawn = mapNodesToDraw(NODES, false, new Set());
    expect(ids(mapEdgesToDraw(EDGES, drawn, false))).toEqual(["SUB-HOSP"]);
    expect(mapEdgesToDraw(EDGES, drawn, false).map((e) => e.edge_type)).toEqual(["power_supply"]);
  });

  it("keeps a pinned junction's access link, but not links to hidden junctions", () => {
    const drawn = mapNodesToDraw(NODES, false, new Set(["J1"]));
    const drawnEdges = mapEdgesToDraw(EDGES, drawn, false);
    expect(drawnEdges.map((e) => `${e.id}:${e.edge_type}`).sort()).toEqual([
      "HOSP-J1:road_link",
      "SUB-HOSP:power_supply",
    ]);
  });

  it("draws every link when roads are on", () => {
    expect(mapEdgesToDraw(EDGES, NODES, true)).toHaveLength(EDGES.length);
  });
});

describe("on the shipped seed network", () => {
  const seed = (file: string) =>
    JSON.parse(readFileSync(fileURLToPath(new URL(`../../../data/seed/${file}`, import.meta.url)), "utf-8"));
  const nodes: InfraNode[] = seed("nodes.geojson").features.map(
    (f: { properties: InfraNode; geometry: { coordinates: [number, number] } }) => ({
      ...f.properties,
      lng: f.geometry.coordinates[0],
      lat: f.geometry.coordinates[1],
    })
  );
  const edges: InfraEdge[] = seed("edges.json");

  it("shows only the utility layer by default", () => {
    const drawn = mapNodesToDraw(nodes, false, new Set());
    const drawnEdges = mapEdgesToDraw(edges, drawn, false);
    expect(drawn.some((n) => n.node_type === "road_junction")).toBe(false);
    expect(drawn).toHaveLength(nodes.filter((n) => n.node_type !== "road_junction").length);
    expect(drawnEdges.some((e) => e.edge_type === "road_link")).toBe(false);
    expect(drawnEdges).toHaveLength(edges.filter((e) => e.edge_type !== "road_link").length);
  });
});
