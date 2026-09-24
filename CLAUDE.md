# CLAUDE.md

Guidance for AI coding assistants (and humans) working in this repository.

## Git and pull requests — no AI attribution

Commits and pull requests must carry **no indication of AI authorship**:

- No `Co-Authored-By: Claude …` (or any other AI co-author) trailers.
- No `Signed-off-by` lines naming Claude or Anthropic.
- No `Claude-Session:` or similar session/tracking lines.
- No "Generated with Claude Code" (or similar) text in PR titles, bodies, or commit messages.

Write commit messages and PR descriptions as plain engineering prose about the change itself.

## What this is

Ripple simulates how an infrastructure failure cascades through a city's power, water, transit and
telecom network (Motter–Lai load redistribution plus domain-aware dependency severing), ranks the
most critical assets, and recommends interventions that are verified by rerunning the cascade.

- `backend/` — FastAPI API, Celery worker, SQLAlchemy/PostGIS models, Alembic migrations.
  - `app/simulation/` — the cascade engine. Deliberately **database-free** (no `app.db`/`app.models`
    imports) so it runs standalone in tests and diagnosis.
  - `app/services/` — graph building, recommendations (+ Redis cache), centrality, ingestion.
  - `native/ripple_graph_rs/` — optional Rust (PyO3 + rayon) acceleration for global efficiency;
    the Python fallback in `app/simulation/cascade.py` must stay numerically identical.
- `frontend/` — React + Vite + TanStack Query + Zustand, MapLibre + deck.gl map, Cytoscape graph.
- `data/seed/` — synthetic seed network; `data/scripts/` — generator, scripted demo and
  `build_basemap.py`, which builds the self-hosted map tiles, fonts and sprite under
  `frontend/public/` (see `data/tiles/README.md`).
- `./demo` — one-command Docker Compose runner (`./demo up|status|test|down|reset`).

## Checks (what CI runs)

```bash
# Backend (from backend/)
pip install -e ".[dev]" flake8
flake8 app tests && ruff check . && mypy app && pytest tests/

# Rust extension (from backend/native/ripple_graph_rs/)
cargo test --release
maturin build --release --out dist && pip install dist/*.whl   # then re-run pytest: parity tests un-skip

# Frontend (from frontend/)
npm ci && npm test && npm run build
```

Tests run without Postgres/Neo4j/Redis: `backend/tests/conftest.py` shims the database and security
modules but uses the **real** `app.config.Settings` (environment `"test"`, no `.env`). Read settings
directly (`settings.x`); do not add defensive wrappers around them.

## Conventions that matter

- **One graph builder.** Build simulation graphs with `app.services.graph_build.build_graph`; never
  hand-roll a NetworkX graph from ORM rows (divergent builders silently disabled edge semantics).
- **Never mutate a baseline graph.** Copy first (`isolated_graph`, `G.copy()`); scenario edits go
  through `app.simulation.scenario.apply_scenario_modifications`, which returns a copy.
- **Waves are marginal.** `failed_node_ids` is the set newly failed in that wave;
  `cumulative_failed_node_ids` is the running total. Do not re-accumulate or mix them.
- **Scenario runs are scenario-aware.** `SimulationResult.scenario_id` records the applied scenario;
  anything derived from a run (recommendations above all) must use that modified topology.
- **Coordinates.** Positional pairs are GeoJSON `[lng, lat]` (see `app/services/geo.py`); node
  payloads carry named `lat`/`lng` scalars.
- **Edge types.** `app/simulation/semantics.py:EDGE_TYPES` is the vocabulary; the ORM enum, the Neo4j
  relationship map (`graph_sync.RELATIONSHIP_TYPES`), the scenario API and the frontend `EdgeType`
  must all cover it (tests enforce the backend side).
- **Migrations.** Exactly one Alembic head (`tests/test_migrations.py`). Add a new revision; never
  edit an applied one.
- **Frontend data access.** Go through `src/api/client.ts` (`apiFetch`/`apiPost`) so server error
  details reach the user; recommendations come only from `useMitigations` (one shared query).
  Only `stores/simulationStore.ts` may touch `localStorage` (a contract test enforces this).
- **Provenance honesty.** The shipped data is synthetic; UI labels must not present estimated or
  projected figures as observed or verified.
