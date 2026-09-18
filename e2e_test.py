#!/usr/bin/env python3
"""
Ripple E2E Test Suite — Single Comprehensive Verification Loop (Requirement R2)

Exercises the complete resilience workflow:
1. Discovers and reuses an existing network via GET /api/networks.
2. Asserts Betweenness Centrality is returned by default for network centrality queries.
3. Selects a seed node whose cascade consequence is verified by simulation (no hardcoded IDs).
   Polls until status is completed and asserts multi-wave cascade occurred.
4. Calls the recommendations endpoint and asserts at least one candidate with verified: True.
5. Creates a scenario using the verified candidate's scenario_payload and runs its simulation.
6. Asserts the new simulation produces strictly better outcomes (fewer failures, lower population affected).
7. Queries the scenario compare endpoint to verify end-to-end reporting.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "http://localhost:8000/api"
TIMEOUT_SECS = 30
#: Bounded number of candidate seed nodes to simulate while looking for one
#: whose failure genuinely cascades.
MAX_SEED_PROBES = 8


def request(url: str, method: str = "GET", data: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(url, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def comparable_population(sim: dict[str, Any]) -> int:
    """Population figure that remains comparable across runs.

    ``population_affected_estimate`` is capped at the study-area limit. When a
    baseline and an intervention both exceed that cap they report the same
    number, so asserting a strict decrease on it fails even when the
    intervention genuinely protected tens of thousands of people. The uncapped
    total is used instead where the API provides it.
    """
    raw = sim.get("raw_population_affected")
    return sim.get("population_affected_estimate", 0) if raw is None else raw


def select_cascading_seed_node(
    network_id: str,
    nodes: list[dict[str, Any]],
    centrality: list[dict[str, Any]],
    max_probes: int = MAX_SEED_PROBES,
) -> tuple[str, str, dict[str, Any]]:
    """Find a seed node whose failure produces a real multi-wave cascade.

    This test previously hardcoded a node name plus a literal UUID fallback,
    which silently targeted a stale ID whenever the seed data was regenerated
    (node IDs are not stable across regeneration). Instead, walk candidates in
    centrality order and simulate each until one actually cascades. The winning
    simulation is returned so it doubles as the baseline.
    """
    names = {n["id"]: (n.get("display_name") or n.get("name") or n["id"][:8]) for n in nodes}
    ranked = [c["node_id"] for c in sorted(centrality, key=lambda x: x.get("rank", 9999))]
    # Fall back to plain node order if centrality gave us nothing usable.
    candidates = [nid for nid in ranked if nid in names] or [n["id"] for n in nodes]

    attempts: list[str] = []
    for node_id in candidates[:max_probes]:
        label = names.get(node_id, node_id[:8])
        sim = poll_simulation(
            request(
                f"{BASE_URL}/simulations",
                method="POST",
                data={"network_id": network_id, "initial_failures": [node_id]},
            )["id"]
        )
        waves = len(sim.get("waves", []))
        failed = sim.get("total_failed", 0)
        attempts.append(f"{label}: {failed} failed / {waves} wave(s)")

        if sim.get("status") == "completed" and waves > 1 and failed > 1:
            print(f"  ✓ Verified cascade on {label}: {failed} failed across {waves} waves")
            return node_id, label, sim

        print(f"    {label}: no cascade ({failed} failed, {waves} wave) — next candidate")

    raise AssertionError(
        "No cascading seed node found in the top "
        f"{max_probes} candidates. Probed:\n      " + "\n      ".join(attempts) + "\n"
        "  The E2E loop requires a failure that actually displaces load."
    )


def poll_simulation(sim_id: str, timeout_secs: int = TIMEOUT_SECS) -> dict[str, Any]:
    start = time.time()
    while time.time() - start < timeout_secs:
        res = request(f"{BASE_URL}/simulations/{sim_id}")
        status = res.get("status")
        if status in ("completed", "failed"):
            return res
        time.sleep(1)
    raise TimeoutError(f"Simulation {sim_id} timed out after {timeout_secs}s")


def main() -> int:
    print("=" * 80)
    print("  RIPPLE COMPREHENSIVE E2E VERIFICATION LOOP (R2)")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # 1. Discover or reuse a network
    # -------------------------------------------------------------------------
    print("\n[Step 1/6] Discovering and reusing active infrastructure network...")
    networks = request(f"{BASE_URL}/networks")
    assert isinstance(networks, list) and len(networks) > 0, (
        "No networks found. Ensure seed data is ingested."
    )
    network_id = networks[0]["id"]
    network_name = networks[0].get("name", "Unknown Network")
    print(f"  ✓ Reusing Network: '{network_name}' ({network_id})")

    # -------------------------------------------------------------------------
    # 2. Centrality assertion (Betweenness Centrality by default)
    # -------------------------------------------------------------------------
    print("\n[Step 2/6] Querying network centrality & verifying default metric...")
    default_cent = request(f"{BASE_URL}/networks/{network_id}/centrality")
    assert isinstance(default_cent, list) and len(default_cent) > 0, (
        "Centrality endpoint returned empty results."
    )
    assert "node_id" in default_cent[0] and "score" in default_cent[0] and "rank" in default_cent[0], (
        "Centrality result missing expected fields (node_id, score, rank)"
    )
    assert default_cent[0]["rank"] == 1, (
        f"Expected top node rank to be 1, got {default_cent[0]['rank']}"
    )

    # Verify default query matches betweenness query
    bc_cent = request(f"{BASE_URL}/networks/{network_id}/centrality?metric=betweenness")
    assert len(bc_cent) == len(default_cent), (
        "Centrality result lengths mismatch between default and ?metric=betweenness"
    )
    assert default_cent[0]["node_id"] == bc_cent[0]["node_id"], (
        "Default centrality does not match Betweenness Centrality top node"
    )
    top_cent_name = default_cent[0].get("name") or default_cent[0].get("display_name", "N/A")
    top_cent_score = default_cent[0]["score"]
    print(f"  ✓ Centrality verified: Betweenness is default (Top node: {top_cent_name}, score={top_cent_score:.4f})")

    # -------------------------------------------------------------------------
    # 3. Select a seed node by verified cascade consequence
    # -------------------------------------------------------------------------
    print("\n[Step 3/6] Selecting a cascading seed node by verified consequence...")
    nodes = request(f"{BASE_URL}/networks/{network_id}/nodes")
    assert nodes, "Network returned no nodes."

    seed_node_id, seed_node_name, base_sim = select_cascading_seed_node(
        network_id, nodes, default_cent
    )
    base_sim_id = base_sim["id"]

    print(f"  Target seed node: {seed_node_name} ({seed_node_id})")
    assert base_sim["status"] == "completed", (
        f"Baseline simulation failed: {base_sim.get('error_message')}"
    )
    base_failed = base_sim["total_failed"]
    base_pop = comparable_population(base_sim)
    base_waves = len(base_sim.get("waves", []))

    assert base_waves > 1, f"Expected multi-wave cascade, got {base_waves} wave(s)"
    assert base_failed > 1, f"Expected >1 failed nodes, got {base_failed}"
    assert base_pop > 0, f"Expected population affected > 0, got {base_pop}"
    print(f"  ✓ Baseline cascade completed: {base_failed} failed nodes across {base_waves} waves (Pop affected: {base_pop:,})")

    # -------------------------------------------------------------------------
    # 4. Fetch recommendations and assert verified: True
    # -------------------------------------------------------------------------
    print("\n[Step 4/6] Calling recommendations endpoint on baseline simulation...")
    recs = request(f"{BASE_URL}/simulations/{base_sim_id}/recommendations")
    assert isinstance(recs, list) and len(recs) > 0, (
        "Recommendations endpoint returned 0 candidates"
    )

    verified_candidates = [
        r for r in recs
        if r.get("verified") is True and r.get("failures_prevented", 0) > 0
    ]
    assert len(verified_candidates) > 0, (
        f"No candidates with verified: True and failures_prevented > 0 returned in {recs}"
    )

    top_candidate = verified_candidates[0]
    print(f"  ✓ Found {len(recs)} recommendation(s) ({len(verified_candidates)} verified). Top verified candidate:")
    print(f"    - Target: {top_candidate.get('display_name') or top_candidate.get('node_name')}")
    print(f"    - Type: {top_candidate.get('intervention_type')}")
    print(f"    - Projected prevented failures: {top_candidate.get('failures_prevented')}")
    print(f"    - Projected population saved: {top_candidate.get('raw_population_saved'):,}")
    print(f"    - Protects critical services: {top_candidate.get('protects_critical_services', False)}")
    print(f"    - Verified: {top_candidate.get('verified')}")

    assert "scenario_payload" in top_candidate, "Top candidate missing scenario_payload"
    scenario_payload = top_candidate["scenario_payload"]
    assert "network_id" in scenario_payload and "modifications" in scenario_payload
    assert len(scenario_payload["modifications"]) > 0

    # -------------------------------------------------------------------------
    # 5. Create scenario from candidate payload & re-simulate
    # -------------------------------------------------------------------------
    print("\n[Step 5/6] Creating scenario from verified recommendation payload & re-simulating...")
    scenario = request(f"{BASE_URL}/scenarios", method="POST", data=scenario_payload)
    scenario_id = scenario["id"]
    print(f"  ✓ Scenario created: '{scenario['name']}' ({scenario_id})")

    scen_sim = request(f"{BASE_URL}/simulations", method="POST", data={
        "network_id": network_id,
        "initial_failures": scenario_payload.get("initial_failures", [seed_node_id]),
        "scenario_id": scenario_id,
    })
    scen_sim_id = scen_sim["id"]
    scen_sim = poll_simulation(scen_sim_id)

    assert scen_sim["status"] == "completed", (
        f"Scenario simulation failed: {scen_sim.get('error_message')}"
    )
    scen_failed = scen_sim["total_failed"]
    scen_pop = comparable_population(scen_sim)
    scen_waves = len(scen_sim.get("waves", []))
    print(f"  ✓ Scenario cascade completed: {scen_failed} failed nodes across {scen_waves} waves (Pop affected: {scen_pop:,})")

    # -------------------------------------------------------------------------
    # 6. Assert strictly-better outcomes
    # -------------------------------------------------------------------------
    print("\n[Step 6/6] Asserting strictly-better resilience outcomes...")
    assert scen_failed < base_failed, (
        f"Assertion Failed: Total failed nodes was not strictly better. "
        f"Baseline: {base_failed}, Scenario: {scen_failed}"
    )
    # Measured on the uncapped total (see comparable_population): the capped
    # headline figure saturates, so both runs would report the study-area limit
    # and a genuine improvement would read as "no change".
    assert scen_pop < base_pop, (
        f"Assertion Failed: Population affected was not strictly better. "
        f"Baseline: {base_pop}, Scenario: {scen_pop}"
    )

    failures_saved = base_failed - scen_failed
    pop_saved = base_pop - scen_pop
    print("  ✓ Verified strictly better:")
    print(f"    - Failures prevented: {failures_saved} ({base_failed} → {scen_failed})")
    print(f"    - Population protected: {pop_saved:,} ({base_pop:,} → {scen_pop:,})")

    # Verify scenario compare endpoint
    print("  Verifying scenario compare endpoint...")
    compare = request(f"{BASE_URL}/scenarios/compare/{base_sim_id}/{scenario_id}")
    assert "baseline_result" in compare and "scenario_result" in compare, (
        "Compare endpoint response missing baseline_result or scenario_result"
    )
    assert compare["baseline_result"]["id"] == base_sim_id
    assert compare["scenario_result"]["id"] == scen_sim_id
    print("  ✓ Scenario comparison endpoint verified")

    print("\n" + "=" * 80)
    print("  ALL END-TO-END ASSERTIONS PASSED ✅")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"\n❌ Connection Error: Could not reach Ripple backend at {BASE_URL}. Is Docker running? ({e})")
        sys.exit(1)
    except AssertionError as e:
        print(f"\n❌ Verification Assertion Failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        sys.exit(1)
