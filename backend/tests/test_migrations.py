"""Guards on the Alembic migration graph.

A divergent branch previously left two heads in the history, which made
``alembic upgrade head`` fail outright. Because docker-compose's ``migrator``
service runs exactly that command and the ``seeder`` gates on it, the whole
cold-start path was broken. These tests fail fast if a second head is ever
reintroduced.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _script_directory() -> ScriptDirectory:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config)


def test_migration_graph_has_exactly_one_head():
    """``alembic upgrade head`` is only unambiguous with a single head."""
    heads = _script_directory().get_heads()
    assert len(heads) == 1, (
        f"Expected exactly 1 Alembic head, found {len(heads)}: {sorted(heads)}. "
        "Multiple heads make 'alembic upgrade head' fail and break the "
        "docker-compose migrator/seeder cold start. Resolve the branch with a "
        "merge revision or by re-parenting the divergent migration."
    )


def test_migration_chain_is_linear_and_reachable():
    """Every revision must be walkable from base to the single head."""
    script = _script_directory()
    head = script.get_heads()[0]

    walked = [rev.revision for rev in script.walk_revisions("base", head)]
    on_disk = [rev.revision for rev in script.walk_revisions()]

    assert sorted(walked) == sorted(on_disk), (
        "Some migration files are not reachable from base -> head: "
        f"{sorted(set(on_disk) - set(walked))}"
    )

    # A linear chain means each revision has at most one parent.
    for rev in script.walk_revisions("base", head):
        assert len(rev.down_revision or ()) <= 1 or isinstance(rev.down_revision, str), (
            f"Revision {rev.revision} has multiple parents: {rev.down_revision}"
        )


def test_shared_enum_types_disable_automatic_create_and_are_pg_native():
    """The 0001 migration explicitly creates/drops its Postgres enum types.

    ``node_type`` and ``node_status`` are both used on the "nodes" table, so
    creating that table also fires SQLAlchemy's automatic enum-creation DDL
    event for each of them, in addition to the explicit ``.create(bind,
    checkfirst=True)`` loop already in ``upgrade()``. Without
    ``create_type=False`` that is a second ``CREATE TYPE`` for a type the
    explicit loop already created, which Postgres rejects with
    ``DuplicateObject`` on every fresh database — this is not a flake, it
    reproduces every time (confirmed against a real Postgres+PostGIS 16).

    ``create_type`` is a constructor parameter of ``postgresql.ENUM`` only.
    Passing it to the cross-dialect ``sqlalchemy.Enum`` is *silently
    ignored* — no attribute is even set, so nothing errors and nothing
    works — which is how this shipped unnoticed: it fails only against a
    real Postgres, and the tests here run without one.
    """
    module_path = BACKEND_DIR / "alembic" / "versions" / "0001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("initial_schema_migration", module_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    for attr in ("node_type", "node_status", "edge_type", "simulation_status"):
        enum_type = getattr(migration, attr)
        assert isinstance(enum_type, postgresql.ENUM), (
            f"{attr} must be sqlalchemy.dialects.postgresql.ENUM, not the "
            "cross-dialect sqlalchemy.Enum — create_type has no effect on "
            "the latter and the duplicate-CREATE-TYPE bug comes back."
        )
        assert enum_type.create_type is False, (
            f"{attr}.create_type must be False: upgrade()/downgrade() manage "
            "this type's CREATE/DROP explicitly, and leaving the default "
            "True makes op.create_table() attempt a second CREATE TYPE for "
            "any table that uses it."
        )
