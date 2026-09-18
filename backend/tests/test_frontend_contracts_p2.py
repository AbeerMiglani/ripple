"""Regression coverage for the merged P2 Phase 1 frontend fixes.

There is no JavaScript test runner in this repository, and adding one is out of
scope. The established precedent (see the frontend contract tests in
test_recommendations_stress.py) is to assert these contracts by reading the
sources from Python, which is what this module does.

Every assertion runs against source with comments stripped, and render sites are
counted as actual JSX element openings rather than bare name occurrences, so an
import statement, a docstring or a ``{/* ... */}`` note can never be mistaken for
a mounted component.

The four behaviors pinned here were fixed in "P2 hygiene -- duplicate renders,
demo crash, dead hook, stray writer":

1. RecommendationPanel was mounted twice in one sidebar. Each copy held its own
   applied/applying state, so one could still be clicked after the other had
   applied -- creating a duplicate scenario and a duplicate simulation run.
2. CascadeTimeline was likewise mounted twice, giving two scrubbers for one
   animation.
3. useRecommendations was dead and mistyped against the endpoint.
4. ControlPanel wrote localStorage directly, behind the store's back, which
   desynced the persisted cap and left ScenarioCompare stale.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = PROJECT_ROOT / "frontend" / "src"

APP = FRONTEND_SRC / "App.tsx"
CONTROL_PANEL = FRONTEND_SRC / "components" / "ControlPanel.tsx"
SIMULATION_STORE = FRONTEND_SRC / "stores" / "simulationStore.ts"

SHARED_PANELS = ("RecommendationPanel", "CascadeTimeline")

#: Storage keys that existing browsers already hold. Renaming either orphans
#: every saved run and scenario, so the names are part of the contract.
PERSISTED_STORAGE_KEYS = ("ripple_simulations", "ripple_scenarios")

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"(?<![:/])//[^\n]*")


def strip_comments(source: str) -> str:
    """Remove block and line comments.

    Block comments cover JSX ``{/* ... */}`` notes as well. The line-comment
    pattern skips ``://`` so a URL inside a string survives intact -- these
    files contain no other ``//`` that is not a comment.
    """
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", source))


def jsx_render_count(source: str, component: str) -> int:
    """Count actual JSX openings of ``component`` in comment-stripped source.

    Matches ``<Name>``, ``<Name />`` and ``<Name prop=...>`` but not
    ``<NameSomethingElse``, and not the name appearing in an import, a type
    position, or prose.
    """
    pattern = re.compile(rf"<{re.escape(component)}(?=[\s/>])")
    return len(pattern.findall(strip_comments(source)))


def tsx_sources():
    """Every frontend source file, as (path, text) pairs."""
    files = sorted(FRONTEND_SRC.rglob("*.ts")) + sorted(FRONTEND_SRC.rglob("*.tsx"))
    assert files, f"no frontend sources found under {FRONTEND_SRC}"
    return [(path, path.read_text(encoding="utf-8")) for path in files]


@pytest.fixture(scope="module")
def sources():
    return tsx_sources()


# ---------------------------------------------------------------------------
# The comment/import-safety of the matcher itself
# ---------------------------------------------------------------------------

def test_render_counter_ignores_comments_imports_and_similar_names():
    """The matcher must not fire on anything that is not a mounted element."""
    decoys = """
    import CascadeTimeline from "./CascadeTimeline";
    // <CascadeTimeline /> in a line comment
    /* <CascadeTimeline /> in a block comment */
    {/* <CascadeTimeline /> in a JSX comment */}
    const x = <CascadeTimelineLegend />;
    """
    assert jsx_render_count(decoys, "CascadeTimeline") == 0

    real = decoys + "\n return <CascadeTimeline />;"
    assert jsx_render_count(real, "CascadeTimeline") == 1

    with_props = "<CascadeTimeline waves={3} />" + "<CascadeTimeline>child</CascadeTimeline>"
    assert jsx_render_count(with_props, "CascadeTimeline") == 2


# ---------------------------------------------------------------------------
# 1 & 2 -- each shared panel is mounted exactly once
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("component", SHARED_PANELS)
def test_shared_panel_is_mounted_exactly_once_in_the_whole_app(sources, component):
    """A second mount gives the component a second, independent copy of state."""
    sites = {
        path.relative_to(PROJECT_ROOT).as_posix(): jsx_render_count(text, component)
        for path, text in sources
        if jsx_render_count(text, component)
    }
    assert sum(sites.values()) == 1, f"{component} is mounted at: {sites}"


@pytest.mark.parametrize("component", SHARED_PANELS)
def test_app_is_the_sole_render_site_for_shared_panels(component):
    """App.tsx owns the layout; the panels are mounted there and nowhere else."""
    assert jsx_render_count(APP.read_text(encoding="utf-8"), component) == 1


@pytest.mark.parametrize("component", SHARED_PANELS)
def test_control_panel_does_not_re_render_shared_panels(component):
    """ControlPanel is itself mounted inside App's sidebar.

    Rendering these there nested a second copy directly beneath the first.
    """
    source = CONTROL_PANEL.read_text(encoding="utf-8")
    assert jsx_render_count(source, component) == 0
    # The import went with the render; a lingering one signals a partial revert.
    assert not re.search(rf"^\s*import\s+{component}\b", strip_comments(source), re.MULTILINE)


# ---------------------------------------------------------------------------
# 3 -- the dead hook stays gone
# ---------------------------------------------------------------------------

def test_dead_use_recommendations_hook_stays_removed(sources):
    """It had no call sites and its return type contradicted the endpoint.

    ``GET /api/simulations/{id}/recommendations`` returns a bare list; the hook
    declared ``{simulation_id, recommendations}``.
    """
    offenders = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path, text in sources
        if "useRecommendations" in strip_comments(text)
    ]
    assert not offenders, f"useRecommendations reappeared in: {offenders}"


# ---------------------------------------------------------------------------
# 4 -- the store is the only writer, and the keys are unchanged
# ---------------------------------------------------------------------------

def test_localstorage_is_accessed_only_by_the_simulation_store(sources):
    """Two writers desynced the persisted cap and left the store stale.

    ControlPanel wrote `ripple_scenarios` without calling registerScenario, so a
    new What-If scenario did not reach ScenarioCompare until a page reload.
    """
    accessor = re.compile(r"localStorage\.(setItem|getItem|removeItem|clear)\b")
    offenders = sorted(
        {
            path.relative_to(PROJECT_ROOT).as_posix()
            for path, text in sources
            if accessor.search(strip_comments(text))
        }
        - {SIMULATION_STORE.relative_to(PROJECT_ROOT).as_posix()}
    )
    assert not offenders, f"localStorage accessed outside the store in: {offenders}"


def test_persisted_storage_keys_are_unchanged():
    """Renaming either key silently orphans existing browser state."""
    source = strip_comments(SIMULATION_STORE.read_text(encoding="utf-8"))
    for key in PERSISTED_STORAGE_KEYS:
        assert f'localStorage.getItem("{key}")' in source, f"{key} is no longer read"
        assert f'localStorage.setItem("{key}"' in source, f"{key} is no longer written"


def test_control_panel_persists_through_store_actions():
    """The replacement path, not just the absence of the old one.

    ControlPanel must still record runs and scenarios -- via the store, so the
    in-memory state and localStorage cannot diverge.
    """
    source = strip_comments(CONTROL_PANEL.read_text(encoding="utf-8"))
    assert "registerScenario(" in source, "scenarios are no longer persisted at all"
    assert "addSimulation(" in source, "dismissed runs are no longer recorded"
