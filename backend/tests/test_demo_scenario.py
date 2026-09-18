"""Regression tests for the demo script's formatting and failure selection.

``data/scripts/demo_scenario.py`` is stdlib-only and lives outside the backend
package, so it is loaded by path rather than imported. Nothing here touches the
API: the formatting helpers are pure, and the selection tests substitute the
module's two network functions (``post`` and ``poll_simulation``) with recorded
fakes.
"""

import importlib.util
from pathlib import Path

import pytest

DEMO_SCRIPT = (
    Path(__file__).resolve().parents[2] / "data" / "scripts" / "demo_scenario.py"
)


def _load_demo_module():
    spec = importlib.util.spec_from_file_location("ripple_demo_scenario", DEMO_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    assert DEMO_SCRIPT.is_file(), f"demo script not found at {DEMO_SCRIPT}"
    return _load_demo_module()


def test_add_edge_recommendation_does_not_crash_on_null_capacity(demo):
    """An add_edge recommendation carries proposed_capacity=None.

    Formatting it with ``:.1f`` raised TypeError and aborted the demo at step 5
    whenever a redundancy link ranked first.
    """
    rec = {
        "intervention_type": "add_edge",
        "proposed_capacity": None,
        "node_id": "11111111-2222-3333-4444-555555555555",
        "display_name": "Manipal Substation",
        "target_display_name": "KMC Hospital Feeder",
    }

    described = demo.describe_intervention(rec)

    assert described
    assert "Manipal Substation" in described
    assert "KMC Hospital Feeder" in described
    assert "upgrade" not in described
    assert "None" not in described


def test_add_edge_falls_back_through_target_name_ladder(demo):
    """Target resolves display_name -> node_name -> truncated id."""
    base = {
        "intervention_type": "add_edge",
        "proposed_capacity": None,
        "node_id": "aaaaaaaa-0000-0000-0000-000000000000",
        "display_name": "Source Asset",
    }

    by_node_name = demo.describe_intervention({**base, "target_node_name": "Raw Target"})
    assert "Raw Target" in by_node_name

    by_id = demo.describe_intervention(
        {**base, "target_node_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"}
    )
    assert "bbbbbbbb" in by_id

    unresolved = demo.describe_intervention(base)
    assert unresolved
    assert "None" not in unresolved


def test_upgrade_node_recommendation_formats_capacity(demo):
    rec = {
        "intervention_type": "upgrade_node",
        "proposed_capacity": 137.5,
        "node_id": "99999999-8888-7777-6666-555555555555",
        "display_name": "Udupi Feeder",
    }

    described = demo.describe_intervention(rec)

    assert "upgrade" in described
    assert "Udupi Feeder" in described
    assert "137.5" in described


def test_upgrade_node_without_capacity_omits_the_parenthetical(demo):
    """Defensive: a missing capacity must not render as 'None' or raise."""
    described = demo.describe_intervention(
        {
            "intervention_type": "upgrade_node",
            "node_id": "77777777-6666-5555-4444-333333333333",
            "node_name": "Fallback Name",
        }
    )

    assert "Fallback Name" in described
    assert "None" not in described


def test_recommendation_label_falls_back_to_truncated_node_id(demo):
    label = demo.recommendation_label(
        {"node_id": "abcdef12-3456-7890-abcd-ef1234567890"}
    )

    assert label == "abcdef12"


# ---------------------------------------------------------------------------
# Verified-consequence failure selection
#
# select_cascading_failure replaced a hardcoded UUID with a PS-02 fallback. Its
# contract is that the demo's initial failure is chosen by *running* the engine,
# not by trusting betweenness centrality as a proxy for consequence, and that it
# refuses rather than inventing a default when nothing cascades.
#
# The function reaches the network only through the module globals `post` and
# `poll_simulation`, so substituting those two is enough to drive it offline.
# ---------------------------------------------------------------------------

def _candidate(node_id, name, rank, node_type="power_substation"):
    return {"node_id": node_id, "display_name": name, "node_type": node_type, "rank": rank}


def _sim(sim_id, waves, total_failed):
    return {
        "id": sim_id,
        "status": "completed",
        "waves": [{"wave": i, "failed_node_ids": [f"n{i}"]} for i in range(waves)],
        "total_failed": total_failed,
    }


class _RecordingApi:
    """Stands in for post()/poll_simulation(), recording what was asked for."""

    def __init__(self, outcomes):
        #: node_id -> (waves, total_failed)
        self.outcomes = outcomes
        self.posted_failures = []
        self.polled = []
        self._by_sim = {}

    def post(self, path, body):
        assert path == "/api/simulations"
        node_id = body["initial_failures"][0]
        self.posted_failures.append(node_id)
        sim_id = f"sim-for-{node_id}"
        waves, total_failed = self.outcomes[node_id]
        self._by_sim[sim_id] = _sim(sim_id, waves, total_failed)
        return {"id": sim_id, "status": "pending"}

    def poll_simulation(self, sim_id):
        self.polled.append(sim_id)
        return self._by_sim[sim_id]

    def install(self, demo, monkeypatch):
        monkeypatch.setattr(demo, "post", self.post)
        monkeypatch.setattr(demo, "poll_simulation", self.poll_simulation)
        # Silence the script's console output during tests.
        monkeypatch.setattr(demo, "info", lambda msg: None)
        monkeypatch.setattr(demo, "ok", lambda msg: None)


def test_selection_skips_candidates_that_do_not_cascade(demo, monkeypatch):
    """The top-ranked candidate is not automatically the demo failure.

    Centrality is a structural proxy: a highly central asset with headroom fails
    alone. Selection must walk past it to one whose failure actually spreads.
    """
    api = _RecordingApi({
        "quiet-node": (1, 1),      # fails alone -- rejected
        "cascading-node": (3, 5),  # genuinely cascades -- selected
    })
    api.install(demo, monkeypatch)

    node_id, label, sim, sim_id = demo.select_cascading_failure(
        "net-1",
        [_candidate("quiet-node", "Quiet PS", 1), _candidate("cascading-node", "Busy PS", 2)],
    )

    assert node_id == "cascading-node"
    assert label == "Busy PS"
    assert api.posted_failures == ["quiet-node", "cascading-node"]
    assert sim["total_failed"] == 5
    assert sim_id == "sim-for-cascading-node"


def test_selected_simulation_is_reused_as_the_baseline(demo, monkeypatch):
    """The run that verified the cascade is the run handed back.

    Nothing is simulated twice: the returned sim and sim_id are the probe's own
    result, so the caller does not re-run the baseline it already has.
    """
    api = _RecordingApi({"cascading-node": (4, 7)})
    api.install(demo, monkeypatch)

    _, _, sim, sim_id = demo.select_cascading_failure(
        "net-1", [_candidate("cascading-node", "Busy PS", 1)]
    )

    assert api.posted_failures == ["cascading-node"]
    assert api.polled == ["sim-for-cascading-node"]
    assert sim["id"] == sim_id


def test_selection_requires_both_multiple_waves_and_multiple_failures(demo, monkeypatch):
    """A second wave that topples nothing new is not a cascade.

    Pins `waves > 1 and total_failed > 1`: a 2-wave run with a single failed
    asset must be rejected, not presented as a cascade.
    """
    api = _RecordingApi({
        "two-waves-one-failure": (2, 1),
        "real-cascade": (2, 3),
    })
    api.install(demo, monkeypatch)

    node_id, _, _, _ = demo.select_cascading_failure(
        "net-1",
        [
            _candidate("two-waves-one-failure", "Deceptive PS", 1),
            _candidate("real-cascade", "Genuine PS", 2),
        ],
    )

    assert node_id == "real-cascade"


def test_selection_walks_candidates_in_the_given_rank_order(demo, monkeypatch):
    """Probing follows the caller's ranking rather than re-sorting."""
    api = _RecordingApi({f"n{i}": (1, 1) for i in range(4)} | {"n4": (3, 3)})
    api.install(demo, monkeypatch)

    node_id, _, _, _ = demo.select_cascading_failure(
        "net-1", [_candidate(f"n{i}", f"Node {i}", i + 1) for i in range(5)]
    )

    assert node_id == "n4"
    assert api.posted_failures == ["n0", "n1", "n2", "n3", "n4"]


def test_selection_raises_rather_than_falling_back_to_a_default(demo, monkeypatch):
    """No cascade found means an explicit failure, never an invented node.

    This is the guard against reintroducing the hardcoded UUID / PS-02 fallback:
    when nothing cascades the function must raise, and the error must report
    what it probed so the seed data can be diagnosed.
    """
    candidates = [_candidate(f"n{i}", f"Node {i}", i + 1) for i in range(5)]
    api = _RecordingApi({f"n{i}": (1, 1) for i in range(5)})
    api.install(demo, monkeypatch)

    with pytest.raises(RuntimeError) as excinfo:
        demo.select_cascading_failure("net-1", candidates)

    message = str(excinfo.value)
    assert "No cascading failure found" in message
    # Every probe is accounted for in the diagnosis.
    for i in range(5):
        assert f"Node {i}" in message
    assert api.posted_failures == [f"n{i}" for i in range(5)]


def test_selection_is_bounded_by_max_probes(demo, monkeypatch):
    """The demo cannot spin through a large quiet network."""
    candidates = [_candidate(f"n{i}", f"Node {i}", i + 1) for i in range(20)]
    api = _RecordingApi({f"n{i}": (1, 1) for i in range(20)})
    api.install(demo, monkeypatch)

    with pytest.raises(RuntimeError):
        demo.select_cascading_failure("net-1", candidates, max_probes=3)

    assert api.posted_failures == ["n0", "n1", "n2"]


def test_selection_labels_candidates_without_a_display_name(demo, monkeypatch):
    """Falls back through display_name -> name -> truncated id."""
    api = _RecordingApi({"abcdef1234567890": (2, 2)})
    api.install(demo, monkeypatch)

    _, label, _, _ = demo.select_cascading_failure(
        "net-1", [{"node_id": "abcdef1234567890", "node_type": "hospital"}]
    )

    assert label == "abcdef12"
