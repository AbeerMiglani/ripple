/**
 * Which assets and links the geographic map draws.
 *
 * The seed network's road grid is synthetic: its junctions and road links do
 * not follow the real streets the basemap draws underneath, so drawing all of
 * them makes the map look wrong. They also rarely take part in a cascade. No
 * road junction fails in any of the three demos, and only 3 of the 26
 * single-asset failures that spread reach one. So healthy road junctions and
 * their road links are hidden by default, and the Layers control brings them
 * back.
 *
 * A junction that is part of what is on screen is always drawn, whatever the
 * toggle: one that has failed, been restored, been kept online by an
 * intervention, or been picked by the user. Hiding it would hide part of the
 * result.
 */
import type { InfraEdge, InfraNode } from "../types";

/** The nodes to draw. `pinned` ids are drawn even when road junctions are hidden. */
export function mapNodesToDraw(
  nodes: readonly InfraNode[],
  showRoads: boolean,
  pinned: ReadonlySet<string>
): InfraNode[] {
  if (showRoads) return [...nodes];
  return nodes.filter((n) => n.node_type !== "road_junction" || pinned.has(n.id));
}

/**
 * The edges to draw: only those whose endpoints are both drawn. With road
 * junctions hidden, a road link stays only as the access link of a junction
 * that is itself on screen; a road link between two facilities is still a
 * road, so it is hidden with the rest.
 */
export function mapEdgesToDraw(
  edges: readonly InfraEdge[],
  drawnNodes: readonly InfraNode[],
  showRoads: boolean
): InfraEdge[] {
  const drawn = new Map(drawnNodes.map((n) => [n.id, n]));
  return edges.filter((e) => {
    const src = drawn.get(e.source_id);
    const tgt = drawn.get(e.target_id);
    if (!src || !tgt) return false;
    if (showRoads || e.edge_type !== "road_link") return true;
    return src.node_type === "road_junction" || tgt.node_type === "road_junction";
  });
}
