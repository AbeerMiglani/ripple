import { useMemo } from "react";
import type { InfraNode } from "../types";

/**
 * Builds an id -> node lookup, memoized on the node list identity.
 *
 * Six components each rebuilt this same Map independently (some memoized,
 * one on every render) from `useNetworkTopology`'s `nodes` array or, for
 * MapView, the equivalent prop. One shared hook instead of six near-identical
 * blocks.
 */
export function useNodeLookup(nodes: InfraNode[] | undefined): Map<string, InfraNode> {
  return useMemo(() => {
    const map = new Map<string, InfraNode>();
    if (nodes) for (const n of nodes) map.set(n.id, n);
    return map;
  }, [nodes]);
}
