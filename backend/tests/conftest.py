"""
Lean-environment shims, installed once, before any test module is collected.

Several backend dependencies (sqlalchemy, pydantic, fastapi, celery, redis,
neo4j, ...) may not be installed in every environment this suite runs in —
notably a bare sandbox without the full driver set. Rather than each test
module installing its own fake versions of these modules into sys.modules,
this conftest does it once, here, before pytest collects any test file.

conftest.py is always imported by pytest before the test modules in its
directory, so this runs first regardless of collection order — five test
files used to each carry a near-duplicate copy of this logic, which meant
whichever file pytest happened to import first "won": its particular shim
implementation (some more complete than others — one had no field validators
at all) silently became the one every other file got too, since each copy
checked "is this already in sys.modules?" before installing its own. That is
real, demonstrated cross-file pollution, not a hypothetical risk — a
regression in one file's shim could only show up in a *different* file,
depending on alphabetical order. Centralizing removes the duplication and
the order-dependence in one move: there is now exactly one implementation of
each shim, so there is nothing left to disagree.

Every shim below is skipped when the real package is importable (see
_need_mock), so this changes nothing about how the suite runs with the full
driver set installed (e.g. in CI) — it only matters in a lean environment.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import uuid
from types import ModuleType
from unittest.mock import MagicMock


def _need_mock(name: str) -> bool:
    if name in sys.modules:
        mod = sys.modules[name]
        if getattr(mod, "__spec__", None) is None:
            mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
        return False
    try:
        spec = importlib.util.find_spec(name)
        return spec is None
    except (ValueError, ModuleNotFoundError):
        return True


def _make_mock_module(name: str) -> ModuleType:
    if name in sys.modules:
        mod = sys.modules[name]
        if getattr(mod, "__spec__", None) is None:
            mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
        return mod
    mod = ModuleType(name)
    mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    sys.modules[name] = mod
    return mod


# ---------------------------------------------------------------------------
# pydantic / pydantic_settings
# ---------------------------------------------------------------------------
if _need_mock("pydantic"):
    pyd = _make_mock_module("pydantic")

    class BaseModel:
        model_fields: dict = {}

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)
            # Accumulate annotated field names across the MRO, base to
            # derived, so a subclass that adds no fields of its own (e.g.
            # `class Foo(Bar): pass`) still inherits Bar's model_fields —
            # real pydantic does the same. Values are unused by this shim;
            # only the field *names* matter, for model_validate below.
            fields: dict = {}
            for klass in reversed(cls.__mro__):
                fields.update(getattr(klass, "__annotations__", {}) or {})
            cls.model_fields = {name: None for name in fields if not name.startswith("_")}

        @classmethod
        def model_validate(cls, obj):
            """Minimal from_attributes-style construction: every schema in
            this codebase sets ConfigDict(from_attributes=True), so real
            pydantic reads fields off object attributes, not just dicts."""
            if isinstance(obj, dict):
                return cls(**obj)
            data = {name: getattr(obj, name) for name in cls.model_fields if hasattr(obj, name)}
            return cls(**data)

        def model_dump(self, mode=None, exclude_none=False):
            res = {}
            for k, v in self.__dict__.items():
                if exclude_none and v is None:
                    continue
                res[k] = v
            return res

    pyd.BaseModel = BaseModel
    pyd.Field = lambda *args, default=None, default_factory=None, **kwargs: default
    pyd.field_validator = lambda *fields, **kwargs: (lambda fn: fn)
    pyd.model_validator = lambda *args, **kwargs: (lambda fn: fn)
    pyd.ConfigDict = lambda **kw: kw
    pyd.UUID4 = uuid.UUID

#: Captured before the shim below installs a stand-in, after which the module
#: would look importable.
LEAN_SETTINGS = _need_mock("pydantic_settings")
if LEAN_SETTINGS:
    _make_mock_module("pydantic_settings")

#: The documented defaults of app.config.Settings, for a lean environment where
#: pydantic_settings (and so the real Settings class) cannot be imported.
#: test_conftest_settings.py pins these against the real class whenever it is
#: importable, so the two cannot drift apart.
LEAN_SETTINGS_DEFAULTS: dict = {
    "environment": "test",
    "database_url": "postgresql+psycopg2://ripple:ripple_dev@localhost:5432/ripple",
    "neo4j_uri": "bolt://localhost:7687",
    "neo4j_user": "neo4j",
    "neo4j_password": "ripple_dev_neo4j",
    "redis_url": "redis://:ripple_dev@localhost:6379/0",
    "celery_broker_url": "redis://:ripple_dev@localhost:6379/0",
    "celery_result_backend": "redis://:ripple_dev@localhost:6379/1",
    "cors_origins": ["http://localhost:5173", "http://localhost:3000"],
    "api_key_auth_enabled": False,
    "api_keys": {},
    "rate_limit_per_minute": 60,
    "max_initial_failures": 25,
    "max_scenario_modifications": 20,
    "centrality_cache_ttl_seconds": 300,
    "max_cascade_waves": 50,
    "enforce_edge_semantics": True,
    "topology_source": "synthetic",
    "osm_place": "Manipal, Karnataka, India",
    "osm_network_type": "drive",
    "osm_cache_dir": "/data/osm",
}

# Real settings, not a MagicMock. A Mock answers every attribute with a truthy
# Mock, which forced production modules to wrap each settings read in a
# type-checking helper just so the suite could run. The real class has safe
# development defaults; it is built with the environment pinned to "test" and
# no .env file, so a developer's local configuration cannot leak into results.
if "app.config" not in sys.modules:
    if LEAN_SETTINGS:
        from types import SimpleNamespace

        lean_cfg = _make_mock_module("app.config")
        lean_cfg.settings = SimpleNamespace(**LEAN_SETTINGS_DEFAULTS)
    else:
        import app.config as _real_config

        _real_config.settings = _real_config.Settings(_env_file=None, environment="test")

# ---------------------------------------------------------------------------
# fastapi
# ---------------------------------------------------------------------------
if _need_mock("fastapi"):
    fa = _make_mock_module("fastapi")

    class APIRouter:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, *args, **kwargs):
            return lambda fn: fn

        def get(self, *args, **kwargs):
            return lambda fn: fn

    fa.APIRouter = APIRouter
    fa.Depends = lambda x: x
    fa.Query = lambda default=None, **kw: default

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            self.status_code = status_code
            self.detail = detail

    fa.HTTPException = HTTPException

# ---------------------------------------------------------------------------
# celery
# ---------------------------------------------------------------------------
if _need_mock("celery"):
    cel = _make_mock_module("celery")
    cel.shared_task = lambda *args, **kwargs: (lambda fn: fn)

# ---------------------------------------------------------------------------
# sqlalchemy (+ .orm, + .dialects.postgresql)
# ---------------------------------------------------------------------------
if _need_mock("sqlalchemy"):

    class MockColumn:
        """Stands in for both Column(...) and mapped_column(...): a
        descriptor that stores/returns a per-instance value, falling back to
        the declared default. Real SQLAlchemy resolves defaults at flush
        time against a DB row; this resolves them at attribute-read time,
        which is enough for the pure in-memory model tests that use it."""

        def __init__(self, *args, **kwargs):
            self.default = kwargs.get("default")

        def __set_name__(self, owner, name):
            self.name = name

        def __get__(self, instance, owner):
            if instance is None:
                return self
            return instance.__dict__.get(self.name, self.default() if callable(self.default) else self.default)

        def __set__(self, instance, value):
            instance.__dict__[self.name] = value

        def __eq__(self, other):
            return MagicMock()

    mock_sa = _make_mock_module("sqlalchemy")
    mock_sa.Column = MockColumn
    mock_sa.String = MagicMock()
    mock_sa.Integer = MagicMock()
    mock_sa.Float = MagicMock()
    mock_sa.Boolean = MagicMock()
    mock_sa.DateTime = MagicMock()
    mock_sa.JSON = MagicMock()
    mock_sa.Enum = MagicMock()
    mock_sa.ForeignKey = MagicMock()
    mock_sa.CheckConstraint = MagicMock()
    mock_sa.UniqueConstraint = MagicMock()
    mock_sa.create_engine = MagicMock()
    mock_sa.text = MagicMock()

    def mock_validates(*names):
        def decorator(fn):
            fn._sa_validates = names
            return fn

        return decorator

    mock_sa_orm = _make_mock_module("sqlalchemy.orm")
    mock_sa_orm.relationship = MagicMock()
    mock_sa_orm.validates = mock_validates
    mock_sa_orm.sessionmaker = MagicMock()
    mock_sa_orm.Session = MagicMock()
    # Mapped[] is a type-annotation-only construct in real SQLAlchemy 2.0;
    # app/models/network.py uses `from __future__ import annotations`, so
    # `Mapped[str]` etc. are never actually evaluated here — this only needs
    # to exist so `from sqlalchemy.orm import Mapped` succeeds.
    mock_sa_orm.Mapped = MagicMock()
    mock_sa_orm.mapped_column = MockColumn

    class MockBase:
        """Supports @validates the same way real declarative models do:
        validators run both on __init__ kwargs and on later attribute sets,
        which app.models.network.Node relies on (display_name<->name
        cross-field defaulting)."""

        def __init__(self, **kwargs):
            validators = {}
            for attr in dir(self.__class__):
                fn = getattr(self.__class__, attr)
                if hasattr(fn, "_sa_validates"):
                    for name in fn._sa_validates:
                        validators[name] = getattr(self, attr)

            for k, v in kwargs.items():
                if k in validators:
                    v = validators[k](k, v)
                setattr(self, k, v)
            self._sa_initialized = True

        def __setattr__(self, name, value):
            if getattr(self, "_sa_initialized", False):
                for attr in dir(self.__class__):
                    fn = getattr(self.__class__, attr)
                    if hasattr(fn, "_sa_validates") and name in fn._sa_validates:
                        value = getattr(self, attr)(name, value)
            super().__setattr__(name, value)

    mock_sa_orm.DeclarativeBase = MockBase
    mock_sa.orm = mock_sa_orm

    mock_sa_dialects = _make_mock_module("sqlalchemy.dialects")
    mock_sa_pg = _make_mock_module("sqlalchemy.dialects.postgresql")
    mock_sa_pg.UUID = MagicMock()
    mock_sa_dialects.postgresql = mock_sa_pg
    mock_sa.dialects = mock_sa_dialects

# ---------------------------------------------------------------------------
# geoalchemy2
# ---------------------------------------------------------------------------
if _need_mock("geoalchemy2"):
    mock_geo = _make_mock_module("geoalchemy2")
    mock_geo.Geometry = MagicMock()

# ---------------------------------------------------------------------------
# app.db.postgres
# ---------------------------------------------------------------------------
if "app.db.postgres" not in sys.modules:
    if "MockBase" in globals():
        _base = MockBase
    else:
        # sqlalchemy itself was real (not shimmed above) but app.config
        # (imported by the real app.db.postgres) is not — build a minimal
        # real DeclarativeBase instead of the hand-written MockBase.
        from sqlalchemy.orm import DeclarativeBase

        class _RealBase(DeclarativeBase):
            pass

        _base = _RealBase

    mock_app_db_pg = _make_mock_module("app.db.postgres")
    mock_app_db_pg.Base = _base
    mock_app_db_pg.engine = MagicMock()
    mock_app_db_pg.SessionLocal = MagicMock()
    mock_app_db_pg.get_db = MagicMock()

# ---------------------------------------------------------------------------
# redis / app.db.redis
# ---------------------------------------------------------------------------
if "app.db.redis" not in sys.modules:
    mock_redis = _make_mock_module("redis")
    mock_redis_exceptions = _make_mock_module("redis.exceptions")
    mock_redis_exceptions.ConnectionError = type("ConnectionError", (Exception,), {})
    mock_redis_exceptions.TimeoutError = type("TimeoutError", (Exception,), {})
    mock_redis.exceptions = mock_redis_exceptions
    mock_redis_mod = _make_mock_module("app.db.redis")
    mock_redis_mod.get_redis_client = MagicMock()
    mock_redis_mod.get_async_redis_client = MagicMock()
    mock_redis_mod.verify_redis_connection = MagicMock(return_value=True)

# ---------------------------------------------------------------------------
# neo4j / app.db.neo4j
# ---------------------------------------------------------------------------
if _need_mock("neo4j"):
    _make_mock_module("neo4j")

if "app.db.neo4j" not in sys.modules:
    mock_app_db_neo = _make_mock_module("app.db.neo4j")
    mock_app_db_neo.neo4j_session = MagicMock()
    mock_app_db_neo.get_neo4j_driver = MagicMock()
    mock_app_db_neo.close_neo4j_driver = MagicMock()
    mock_app_db_neo.verify_neo4j_connection = MagicMock(return_value=True)

# ---------------------------------------------------------------------------
# app.security
# ---------------------------------------------------------------------------
if "app.security" not in sys.modules:
    mock_sec = _make_mock_module("app.security")
    mock_sec.enforce_rate_limit = MagicMock()
    mock_sec.require_operator = MagicMock()
    mock_sec.require_viewer = MagicMock()
    mock_sec.require_admin = MagicMock()
    mock_sec.websocket_principal = MagicMock()
