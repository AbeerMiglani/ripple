"""Guards on the settings object the suite runs against.

conftest.py installs real ``app.config`` settings when pydantic_settings is
importable and a plain namespace of defaults when it is not. Production code
reads ``settings.x`` directly, so both must hold real values of the right type,
and the lean-environment copy of the defaults must not drift from the class.
"""

from __future__ import annotations

import pytest

from app.config import settings
from tests.conftest import LEAN_SETTINGS, LEAN_SETTINGS_DEFAULTS


def test_settings_are_real_values_not_mocks():
    assert isinstance(settings.max_cascade_waves, int)
    assert isinstance(settings.enforce_edge_semantics, bool)
    assert isinstance(settings.max_initial_failures, int)
    assert settings.topology_source in {"synthetic", "osm"}
    assert settings.environment == "test"


@pytest.mark.skipif(LEAN_SETTINGS, reason="pydantic_settings is not installed")
def test_lean_defaults_match_the_settings_class():
    from app.config import Settings

    real = Settings(_env_file=None, environment="test").model_dump()
    assert set(LEAN_SETTINGS_DEFAULTS) == set(real)
    mismatched = {
        name: (value, real[name])
        for name, value in LEAN_SETTINGS_DEFAULTS.items()
        if real[name] != value
    }
    assert not mismatched, f"lean defaults drifted from app.config.Settings: {mismatched}"
