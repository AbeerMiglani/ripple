"""Application configuration with explicit production safety checks."""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from the process environment.

    Development defaults intentionally keep the hackathon demo easy to run.  A
    production process fails at startup unless it has unique credentials, a
    non-wildcard CORS policy, and API-key authentication enabled.
    """

    environment: Literal["development", "test", "production"] = "development"

    # --- PostgreSQL ---
    database_url: str = "postgresql+psycopg2://ripple:ripple_dev@localhost:5432/ripple"
    # Connections each process keeps open. The defaults suit a long-running
    # server; a serverless host runs many small processes against a pooler and
    # wants each to hold only one or two.
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)

    # --- Neo4j ---
    # Neo4j only mirrors the network for GDS centrality, and NetworkX computes
    # the same rankings in-process. False leaves it out entirely: no driver,
    # no sync on ingestion, and /health reports it "disabled", not down.
    neo4j_enabled: bool = True
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ripple_dev_neo4j"

    # --- Redis ---
    # KV_URL is the name hosted Redis integrations (Vercel's Upstash) set.
    redis_url: str = Field(
        default="redis://:ripple_dev@localhost:6379/0",
        validation_alias=AliasChoices("redis_url", "kv_url"),
    )

    # --- Simulation execution ---
    # "celery" queues each run for a worker process (the Docker stack).
    # "inline" runs it inside the request that created it, for a host with no
    # worker, such as serverless functions: a run on the shipped network takes
    # milliseconds, so the POST simply answers with the finished result.
    simulation_execution: Literal["celery", "inline"] = "celery"

    # --- Celery ---
    celery_broker_url: str = "redis://:ripple_dev@localhost:6379/0"
    celery_result_backend: str = "redis://:ripple_dev@localhost:6379/1"

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Authentication is intentionally opt-in for a local, single-user demo but
    # mandatory in production. Keys map to a least-privilege role.
    api_key_auth_enabled: bool = False
    api_keys: dict[str, Literal["viewer", "operator", "admin"]] = Field(default_factory=dict)
    # The role a request that presents no key gets while auth is enabled.
    # "none" requires a key. A public demo sets "operator", so anyone can run
    # simulations while rate limits still apply; admin is never anonymous.
    public_access_role: Literal["none", "viewer", "operator"] = "none"
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    # The header a trusted reverse proxy puts the real client address in
    # ("x-real-ip" on Vercel), so rate limits count per visitor rather than
    # per proxy. Unset uses the connection's peer address. Only set it behind a
    # proxy that overwrites the header: a client can send any value it likes.
    client_ip_header: str | None = None
    max_initial_failures: int = Field(default=25, ge=1, le=1_000)
    max_scenario_modifications: int = Field(default=20, ge=1, le=1_000)
    centrality_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    # A completed run's recommendations never change, and each request reruns
    # the cascade once per candidate. 0 disables the cache.
    recommendation_cache_ttl_seconds: int = Field(default=3600, ge=0, le=86_400)
    # Bounds cascade propagation work. Reaching it truncates the cascade and
    # marks the result non-stabilized; it never fails the simulation.
    max_cascade_waves: int = Field(default=50, ge=1, le=10_000)

    # --- Cascade semantics ---
    # When true the cascade respects what each link actually delivers, so a
    # blocked road no longer de-energises an electrical tower while a hospital
    # still requires both power and water. Disable to fall back to pure
    # load-overload Motter-Lai propagation.
    enforce_edge_semantics: bool = True

    # --- Baseline topology source ---
    # "synthetic" reads the committed seed fixture; "osm" pulls the real road
    # network for `osm_place` through the OSMnx pipeline and ingests that
    # instead. Everything downstream is unchanged either way: the graph is
    # still built from the database, so the runner, the API and the map need
    # no knowledge of where the topology came from.
    topology_source: Literal["synthetic", "osm"] = "synthetic"
    osm_place: str = "Manipal, Karnataka, India"
    osm_network_type: str = "drive"
    #: Where OSMnx output is cached, so a re-seed does not re-download.
    osm_cache_dir: str = "/data/osm"

    # --- Retention ---
    # Bearer token a scheduler (Vercel Cron) presents to
    # GET /api/internal/cleanup. Unset turns the endpoint off.
    cron_secret: SecretStr | None = None
    # The cleanup endpoint deletes runs and scenarios older than this.
    retention_days: int = Field(default=7, ge=1, le=3650)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def use_psycopg2_driver(cls, value: str) -> str:
        """Accept the bare postgres:// URLs hosted databases hand out.

        SQLAlchemy has no "postgres" dialect and would pick its default driver
        for "postgresql", so both are pinned to the psycopg2 driver installed.
        """
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg2://" + value[len(prefix):]
        return value

    @field_validator("client_ip_header")
    @classmethod
    def blank_header_is_unset(cls, value: str | None) -> str | None:
        return value.strip().lower() or None if value else None

    @field_validator("cron_secret", mode="before")
    @classmethod
    def blank_secret_is_unset(cls, value: object) -> object:
        # `CRON_SECRET=` in an env file means "not configured". As an empty
        # secret it would accept the header "Bearer " from anyone.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        if self.environment != "production":
            return self

        insecure_values = ("ripple_dev", "ripple_dev_neo4j", "localhost:6379/0")
        # Only the services this process will actually connect to.
        in_use = [self.database_url, self.redis_url]
        if self.neo4j_enabled:
            in_use += [self.neo4j_uri, self.neo4j_password]
        if self.simulation_execution == "celery":
            in_use += [self.celery_broker_url, self.celery_result_backend]
        if any(value in " ".join(in_use) for value in insecure_values):
            raise ValueError("production requires unique database, Neo4j, and Redis credentials")
        if not self.api_key_auth_enabled:
            raise ValueError("production requires API_KEY_AUTH_ENABLED=true")
        # A public deployment may have no keys at all: anonymous requests get
        # PUBLIC_ACCESS_ROLE, and nothing gets admin.
        if not self.api_keys and self.public_access_role == "none":
            raise ValueError("production requires API_KEYS, or PUBLIC_ACCESS_ROLE for a public deployment")
        if not self.cors_origins or "*" in self.cors_origins:
            raise ValueError("production requires explicit CORS_ORIGINS")
        if self.cron_secret is not None and len(self.cron_secret.get_secret_value()) < 16:
            raise ValueError("CRON_SECRET must be at least 16 characters")
        return self


settings = Settings()
