import networkx as nx

from app.simulation.cascade import calculate_global_efficiency, run_cascade


def test_calculate_global_efficiency():
    """Test global efficiency calculation for a simple 3-node graph."""
    G = nx.DiGraph()
    G.add_nodes_from(["A", "B", "C"])
    # Disconnected graph
    assert calculate_global_efficiency(G) == 0.0
    
    # Path graph A -> B -> C
    G.add_edge("A", "B")
    G.add_edge("B", "C")
    # A->B = 1, B->C = 1, A->C = 2
    # Paths: 3 out of 6 possible pairs
    # E = (1/6) * (1/1 + 1/1 + 1/2) = (2.5) / 6 = 0.41666...
    eff = calculate_global_efficiency(G)
    assert abs(eff - 0.416666) < 0.001


def test_motter_lai_uniform_cascade():
    """
    Test uniform load redistribution with a hand-computed graph.
    A -> B -> C
    A load 10, cap 15
    B load 10, cap 12
    C load 10, cap 15
    
    Wave 0: A fails.
    Wave 1: A redistributes 10 load to B. 
            B's new load = 20. B's capacity = 12. B fails.
    Wave 2: B redistributes 20 load to C.
            C's new load = 30. C's capacity = 15. C fails.
    """
    G = nx.DiGraph()
    G.add_node("A", current_load=10.0, capacity=15.0, failure_threshold=1.0, population_served=100)
    G.add_node("B", current_load=10.0, capacity=12.0, failure_threshold=1.0, population_served=100)
    G.add_node("C", current_load=10.0, capacity=15.0, failure_threshold=1.0, population_served=100)
    
    G.add_edge("A", "B")
    G.add_edge("B", "C")
    
    waves, _eff_b, eff_a, pop, _ = run_cascade(G, ["A"])
    
    assert len(waves) == 3
    assert set(waves[0]["failed_node_ids"]) == {"A"}
    assert set(waves[1]["failed_node_ids"]) == {"B"}
    assert set(waves[2]["failed_node_ids"]) == {"C"}
    assert pop == 300
    assert eff_a == 0.0  # Fully disconnected


def test_zero_neighbor_noop():
    """
    If a node fails and has no surviving neighbors, the load is just lost,
    no zero-division error occurs.
    """
    G = nx.DiGraph()
    G.add_node("A", current_load=100.0, capacity=150.0)
    G.add_node("B", current_load=100.0, capacity=150.0) # B is isolated
    
    waves, _, _, _, _ = run_cascade(G, ["A"])
    assert len(waves) == 1
    assert set(waves[0]["failed_node_ids"]) == {"A"}
    

def test_directed_redistribution():
    """
    Test that load only distributes along OUT-edges.
    A <- B -> C
    If B fails, load goes to A and C.
    If A fails, load does NOT go to B.
    """
    G = nx.DiGraph()
    G.add_node("A", current_load=0.0, capacity=10.0)
    G.add_node("B", current_load=10.0, capacity=20.0)
    G.add_node("C", current_load=0.0, capacity=10.0)
    
    G.add_edge("B", "A")
    G.add_edge("B", "C")
    
    # Failing A shouldn't affect B
    waves_a, _, _, _, _ = run_cascade(G, ["A"])
    assert len(waves_a) == 1
    
    # Failing B distributes 10/2 = 5 to A and C. 
    # Current capacities are 10, so they don't fail.
    waves_b, _, _, _, _ = run_cascade(G, ["B"])
    assert len(waves_b) == 1  # Only B fails


def test_far_node_survives_cascade():
    """
    Verify that the redistribution step only touches immediate successors of the failed node,
    and a 'far' node survives an isolated local failure if the cascade stops before reaching it.
    
    Graph (10 nodes):
    0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9
    
    Node 0 fails.
    Node 1 has tiny capacity, fails.
    Node 2 has huge capacity, survives!
    Nodes 3-9 should never receive load and survive.
    """
    G = nx.DiGraph()
    for i in range(10):
        # Default capacity 100, load 10
        G.add_node(str(i), current_load=10.0, capacity=100.0, failure_threshold=1.0)
        if i > 0:
            G.add_edge(str(i-1), str(i))
            
    # Make Node 1 brittle
    G.nodes["1"]["capacity"] = 15.0
    # Make Node 2 a sink (huge capacity)
    G.nodes["2"]["capacity"] = 9999.0
    
    # Fail Node 0
    waves, _, _, _, _ = run_cascade(G, ["0"])
    
    # Wave 0: "0" fails, sends 10 load to "1"
    # "1" new load = 10 + 10 = 20 > capacity (15) => "1" fails in Wave 1
    # Wave 1: "1" fails, sends 20 load to "2"
    # "2" new load = 10 + 20 = 30 < capacity (9999) => "2" survives!
    
    assert len(waves) == 2
    assert set(waves[0]["failed_node_ids"]) == {"0"}
    assert set(waves[1]["failed_node_ids"]) == {"1"}
    
    all_failed = set(waves[0]["failed_node_ids"]) | set(waves[1]["failed_node_ids"])
    
    # The 'far' nodes strictly survive
    for i in range(2, 10):
        assert str(i) not in all_failed


# ---------------------------------------------------------------------------
# Capacity-weighted redistribution
#
# run_cascade has two redistribution branches: capacity-weighted when any
# surviving out-link declares a `capacity`, and the legacy uniform split
# otherwise. The tests above all use bare add_edge() calls and therefore only
# ever exercise the uniform branch; the recommendation suites do set capacities
# but always uniformly (100.0 everywhere), which is numerically identical to a
# uniform split. These characterize the weighted branch: the proportional split,
# the per-link clamp, and the zero-capacity guard.
# ---------------------------------------------------------------------------

def _fan_out(cap_to_b, cap_to_c, b_capacity, c_capacity, load=100.0):
    """A fails immediately and sheds `load` across two out-links."""
    G = nx.DiGraph()
    G.add_node("A", current_load=load, capacity=1.0, failure_threshold=1.0)
    G.add_node("B", current_load=0.0, capacity=b_capacity, failure_threshold=1.0)
    G.add_node("C", current_load=0.0, capacity=c_capacity, failure_threshold=1.0)
    if cap_to_b is None:
        G.add_edge("A", "B")
    else:
        G.add_edge("A", "B", capacity=cap_to_b)
    if cap_to_c is None:
        G.add_edge("A", "C")
    else:
        G.add_edge("A", "C", capacity=cap_to_c)
    return G


def test_capacity_weighted_split_is_proportional_to_link_capacity():
    """Load follows link capacity, not head count.

    A sheds 100 across links of capacity 75 and 25, so B receives 75 and C
    receives 25. The thresholds bracket those shares exactly: B (capacity 74.9)
    must fail and C (capacity 25.1) must survive. A uniform 50/50 split would
    invert both outcomes, so this discriminates the branch rather than merely
    executing it.
    """
    G = _fan_out(cap_to_b=75.0, cap_to_c=25.0, b_capacity=74.9, c_capacity=25.1)

    waves, _, _, _, _ = run_cascade(G, ["A"])

    assert len(waves) == 2
    assert set(waves[0]["failed_node_ids"]) == {"A"}
    assert set(waves[1]["failed_node_ids"]) == {"B"}


def test_uniform_split_when_no_link_declares_capacity():
    """The legacy branch stays uniform: 100 across two links is 50 each.

    Mirror image of the test above -- here B (capacity 49.9) fails and C
    (capacity 50.1) survives, which a capacity-weighted split could not produce
    from these inputs.
    """
    G = _fan_out(cap_to_b=None, cap_to_c=None, b_capacity=49.9, c_capacity=50.1)

    waves, _, _, _, _ = run_cascade(G, ["A"])

    assert len(waves) == 2
    assert set(waves[1]["failed_node_ids"]) == {"B"}


def test_one_declared_capacity_switches_the_whole_group_to_weighted():
    """`any(capacity is not None ...)` is group-wide, not per-link.

    A single declared capacity puts every sibling link on the weighted path,
    where an undeclared link contributes zero capacity and therefore receives
    nothing. B takes the entire load; C is starved despite being a live
    successor.
    """
    G = _fan_out(cap_to_b=50.0, cap_to_c=None, b_capacity=99.9, c_capacity=0.1)

    waves, _, _, _, _ = run_cascade(G, ["A"])

    # B received min(50, 100 * 50/50) = 50, under its capacity of 99.9.
    # C received nothing, so its capacity of 0.1 is never exceeded.
    assert len(waves) == 1
    assert set(waves[0]["failed_node_ids"]) == {"A"}


def test_link_capacity_clamps_the_transferred_load():
    """A link cannot carry more than its own capacity; the rest is shed.

    A sheds 100 through a single link of capacity 10, so B receives 10, not 100.
    Without the min() clamp B (capacity 50) would be overwhelmed and fail.
    """
    G = nx.DiGraph()
    G.add_node("A", current_load=100.0, capacity=1.0, failure_threshold=1.0)
    G.add_node("B", current_load=0.0, capacity=50.0, failure_threshold=1.0)
    G.add_edge("A", "B", capacity=10.0)

    waves, _, _, _, _ = run_cascade(G, ["A"])

    assert len(waves) == 1
    assert set(waves[0]["failed_node_ids"]) == {"A"}


def test_zero_total_link_capacity_sheds_load_instead_of_redistributing():
    """With no capacity anywhere to carry it, the load is dropped.

    The `total_capacity > 0` guard means a zero-capacity link transfers nothing
    rather than dividing by zero. B survives on a capacity of 1.0 that an
    unguarded transfer of 100 would have destroyed.
    """
    G = nx.DiGraph()
    G.add_node("A", current_load=100.0, capacity=1.0, failure_threshold=1.0)
    G.add_node("B", current_load=0.0, capacity=1.0, failure_threshold=1.0)
    G.add_edge("A", "B", capacity=0.0)

    waves, _, _, _, _ = run_cascade(G, ["A"])

    assert len(waves) == 1
    assert set(waves[0]["failed_node_ids"]) == {"A"}


def test_weighted_redistribution_skips_already_failed_successors():
    """Capacity toward a dead neighbour is not counted in the denominator.

    A and B fail together in wave 0. A's link to B must not absorb any share,
    so C receives the full transfer its own link can carry (min(60, 100) = 60)
    and fails at capacity 59.9 -- whereas splitting 100 across both links would
    have sent C only 50 and left it standing.
    """
    G = nx.DiGraph()
    G.add_node("A", current_load=100.0, capacity=1.0, failure_threshold=1.0)
    G.add_node("B", current_load=0.0, capacity=1.0, failure_threshold=1.0)
    G.add_node("C", current_load=0.0, capacity=59.9, failure_threshold=1.0)
    G.add_edge("A", "B", capacity=40.0)
    G.add_edge("A", "C", capacity=60.0)

    waves, _, _, _, _ = run_cascade(G, ["A", "B"])

    assert set(waves[0]["failed_node_ids"]) == {"A", "B"}
    assert len(waves) == 2
    assert set(waves[1]["failed_node_ids"]) == {"C"}


def test_cascade_stabilizes_flag_true_when_settled():
    """A cascade that reaches a fixed point reports stabilized=True."""
    G = nx.DiGraph()
    G.add_node("A", current_load=10.0, capacity=15.0, failure_threshold=1.0)
    G.add_node("B", current_load=0.0, capacity=9999.0, failure_threshold=1.0)
    G.add_edge("A", "B")

    waves, _, _, _, stabilized = run_cascade(G, ["A"])

    assert stabilized is True
    assert len(waves) == 1


def test_cascade_truncates_instead_of_raising_at_max_waves():
    """
    Hitting the wave guardrail must return a valid bounded result, not destroy
    the run. Previously this raised RuntimeError, which the Celery task turned
    into a 'failed' simulation and an opaque error for the user.

    Chain of brittle nodes: every wave topples exactly one more node, so a
    max_waves below the chain length is guaranteed to truncate mid-cascade.
    """
    G = nx.DiGraph()
    for i in range(10):
        G.add_node(str(i), current_load=10.0, capacity=15.0, failure_threshold=1.0)
        if i > 0:
            G.add_edge(str(i - 1), str(i))

    waves, eff_before, eff_after, pop, stabilized = run_cascade(G, ["0"], max_waves=3)

    # Bounded, valid, and honestly labelled as unsettled.
    assert stabilized is False
    assert len(waves) == 3
    assert all("failed_node_ids" in w for w in waves)
    assert isinstance(eff_before, float) and isinstance(eff_after, float)
    assert isinstance(pop, int)

    # The same scenario with room to run settles and reports it.
    _, _, _, _, stabilized_full = run_cascade(G, ["0"], max_waves=50)
    assert stabilized_full is True
