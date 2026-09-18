"""Provenance regression tests for the synthetic seed dataset.

``data_quality`` and ``name_source`` are rendered to the user in the map
tooltip, the criticality panel and the failure-selection chips, so an
overstatement here is an overstatement to whoever is reading the map. The
shipped dataset is entirely synthetic: coordinates are real Manipal positions,
but the assets, capacities, loads and population figures are estimates.

``data/seed/README.md`` fixes the vocabulary and records that ``verified`` was
once the ingestion default and overstated exactly this. The model-level defaults
are covered in test_database_ingestion.py; what is pinned here is the seed
artifact and the generator that writes it, so neither can start claiming
observed or verified data.
"""

import json
import re
from pathlib import Path

import pytest

from tests.test_seed_determinism import load_generator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_NODES_PATH = PROJECT_ROOT / "data" / "seed" / "nodes.geojson"
SEED_EDGES_PATH = PROJECT_ROOT / "data" / "seed" / "edges.json"
SEED_README_PATH = PROJECT_ROOT / "data" / "seed" / "README.md"

#: The permitted vocabulary, per data/seed/README.md.
ALLOWED_DATA_QUALITY = {"observed", "estimated", "derived", "simulated"}

#: Labels this dataset may never claim. "observed" would assert a public source
#: that does not exist; "verified" is not in the vocabulary at all.
FORBIDDEN_FOR_SYNTHETIC = {"observed", "verified"}

#: The synthetic numbering scheme, so a real institution name cannot slip in.
SYNTHETIC_NAME_PATTERN = re.compile(
    r"^(Grid Substation PS|Water Facility WS|Hospital MC|Cell Tower TC|Road Junction RJ)-\d{2,3}$"
)


@pytest.fixture(scope="module")
def seed_nodes():
    with open(SEED_NODES_PATH, encoding="utf-8") as f:
        return json.load(f)["features"]


@pytest.fixture(scope="module")
def seed_edges():
    with open(SEED_EDGES_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def generated():
    generator = load_generator()
    nodes = generator.generate_nodes()
    return nodes, generator.generate_edges(nodes)


# ---------------------------------------------------------------------------
# The shipped fixture
# ---------------------------------------------------------------------------

def test_seed_nodes_never_claim_verified_or_observed(seed_nodes):
    """Synthetic data may not be labelled as coming from a real source."""
    offenders = []
    for feature in seed_nodes:
        props = feature["properties"]
        for field in ("data_quality", "name_source"):
            value = props.get(field)
            if value is not None and value in FORBIDDEN_FOR_SYNTHETIC:
                offenders.append((props["name"], field, value))
    assert not offenders, f"synthetic nodes claiming real provenance: {offenders[:5]}"


def test_seed_data_quality_stays_inside_the_documented_vocabulary(seed_nodes):
    """Any declared data_quality must be one of the README's four values."""
    unknown = {
        feature["properties"]["data_quality"]
        for feature in seed_nodes
        if feature["properties"].get("data_quality") is not None
        and feature["properties"]["data_quality"] not in ALLOWED_DATA_QUALITY
    }
    assert not unknown, f"data_quality values outside the documented vocabulary: {unknown}"


def test_seed_nodes_are_flagged_synthetic(seed_nodes):
    """Every shipped node declares itself synthetic."""
    for feature in seed_nodes:
        props = feature["properties"]
        assert props["is_synthetic"] is True, props["name"]
        assert props["data_source"] == "synthetic", props["name"]


def test_seed_edges_are_flagged_synthetic(seed_edges):
    for edge in seed_edges:
        assert edge["is_synthetic"] is True, edge["id"]
        assert edge["data_source"] == "synthetic", edge["id"]


def test_seed_node_names_are_synthetic_labels(seed_nodes):
    """Names follow the synthetic numbering scheme, not real institutions.

    The dataset sits on real Manipal coordinates, so a real hospital or
    substation name would read as a claim about an actual facility.
    """
    unexpected = [
        feature["properties"]["name"]
        for feature in seed_nodes
        if not SYNTHETIC_NAME_PATTERN.match(feature["properties"]["name"])
    ]
    assert not unexpected, f"names outside the synthetic scheme: {unexpected[:5]}"


def test_readme_documents_the_vocabulary_the_tests_enforce():
    """The vocabulary lives in one place; this keeps the tests honest to it."""
    readme = SEED_README_PATH.read_text(encoding="utf-8")
    for value in ALLOWED_DATA_QUALITY:
        assert f"`{value}`" in readme, f"{value} missing from the README vocabulary table"
    assert "`verified` is not a valid value" in readme


# ---------------------------------------------------------------------------
# The generator that writes it
# ---------------------------------------------------------------------------

def test_generated_nodes_are_marked_synthetic(generated):
    """A regenerated fixture carries the same provenance as the shipped one."""
    nodes, _ = generated
    assert nodes
    for node in nodes:
        assert node["is_synthetic"] is True, node["name"]
        assert node["data_source"] == "synthetic", node["name"]
        assert node.get("data_quality") not in FORBIDDEN_FOR_SYNTHETIC
        assert node.get("name_source") not in FORBIDDEN_FOR_SYNTHETIC


def test_generated_edges_are_marked_synthetic(generated):
    _, edges = generated
    assert edges
    for edge in edges:
        assert edge["is_synthetic"] is True, edge["id"]
        assert edge["data_source"] == "synthetic", edge["id"]


def test_generated_node_names_follow_the_synthetic_scheme(generated):
    nodes, _ = generated
    unexpected = [n["name"] for n in nodes if not SYNTHETIC_NAME_PATTERN.match(n["name"])]
    assert not unexpected, f"generator produced non-synthetic names: {unexpected[:5]}"
