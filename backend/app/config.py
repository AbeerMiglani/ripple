"""Application configuration with explicit production safety checks."""

from typing import Literal

from pydantic import Field, model_validator
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

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ripple_dev_neo4j"

    # --- Redis ---
    redis_url: str = "redis://:ripple_dev@localhost:6379/0"

    # --- Celery ---
    celery_broker_url: str = "redis://:ripple_dev@localhost:6379/0"
    celery_result_backend: str = "redis://:ripple_dev@localhost:6379/1"

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Authentication is intentionally opt-in for a local, single-user demo but
    # mandatory in production. Keys map to a least-privilege role.
    api_key_auth_enabled: bool = False
    api_keys: dict[str, Literal["viewer", "operator", "admin"]] = Field(default_factory=dict)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    max_initial_failures: int = Field(default=25, ge=1, le=1_000)
    max_scenario_modifications: int = Field(default=20, ge=1, le=1_000)
    centrality_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        if self.environment != "production":
            return self

        insecure_values = ("ripple_dev", "ripple_dev_neo4j", "localhost:6379/0")
        combined = " ".join(
            [
                self.database_url,
                self.neo4j_uri,
                self.neo4j_password,
                self.redis_url,
                self.celery_broker_url,
                self.celery_result_backend,
            ]
        )
        if any(value in combined for value in insecure_values):
            raise ValueError("production requires unique database, Neo4j, and Redis credentials")
        if not self.api_key_auth_enabled or not self.api_keys:
            raise ValueError("production requires API_KEY_AUTH_ENABLED=true and API_KEYS")
        if not self.cors_origins or "*" in self.cors_origins:
            raise ValueError("production requires explicit CORS_ORIGINS")
        return self


settings = Settings()
