# Ripple

> Simulates how a single infrastructure failure cascades through a city's
> power, water, transit and communications network, identifies the assets
> whose loss does the most damage, and recommends interventions verified by
> resimulation rather than heuristic estimates.

---

## Quick Start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with Docker Compose v2)
- [Node.js 20+](https://nodejs.org/) (for the frontend dev server)

### 1. Clone and configure

```bash
git clone https://github.com/AbeerMiglani/ripple.git
cd ripple
cp .env.example .env
```

### 2. Start the backend services

```bash
docker compose up --build
```

This starts:
- **PostgreSQL 16 + PostGIS 3.4** on port 5432
- **Neo4j 5.21.0 + GDS** on ports 7474 (browser) / 7687 (bolt)
- **Redis 7.4** on port 6379
- **FastAPI backend** on port 8000
- **Celery worker** for async simulation jobs

Wait for all health checks to pass (~30s for Neo4j's first startup).

> **Upgrading an existing database?** Revision `c4f2a7d9e1b1` was an abandoned
> migration branch and has been removed — it left the history with two heads,
> which made `alembic upgrade head` fail and blocked the `migrator` and `seeder`
> services. A fresh database needs no action. A database that was stamped at that
> revision directly must be repaired once, because the surviving branch re-adds
> two of its columns:
>
> ```sql
> ALTER TABLE nodes DROP COLUMN IF EXISTS name_source;
> ALTER TABLE nodes DROP COLUMN IF EXISTS data_quality;
> ```
> ```bash
> alembic stamp bbb3dbb1490c && alembic upgrade head
> ```
>
> Its remaining columns are nullable or carry server defaults, so leaving them in
> place is harmless. Check with `alembic current` if you are unsure.

### 3. Verify the backend

```bash
curl http://localhost:8000/health
# → {"status":"ok","services":{"postgres":"ok","neo4j":"ok","redis":"ok"}}
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

### 5. Access service UIs

| Service | URL |
|---------|-----|
| Frontend | [localhost:5173](http://localhost:5173) |
| Backend API docs | [localhost:8000/docs](http://localhost:8000/docs) |
| Neo4j Browser | [localhost:7474](http://localhost:7474) |

---

## Quick Demo (one command)

**Prerequisites:** Git and [Docker Desktop](https://www.docker.com/products/docker-desktop/)
with Docker Compose v2. Nothing else — no Node, Python, or `curl` needed on this path.

```bash
./demo up
```

Then open **[http://localhost:5173](http://localhost:5173)**.

`./demo up` builds the images, starts Postgres, Neo4j, Redis, the API and the
Celery worker, applies the Alembic migrations, ingests the checked-in seed
network, and waits for each of those to actually report ready before printing
the URL. It waits on real health signals rather than fixed delays, and it is
safe to re-run — seed ingestion is idempotent and never rewrites `data/seed`.
Neo4j's first start is the slow part (heap allocation plus the
graph-data-science plugin), so allow a minute on a cold run.

| Command | What it does |
|---------|--------------|
| `./demo up` | Start everything and verify it is ready. |
| `./demo status` | Re-check readiness and print the URLs. Exits non-zero if the demo is not usable. Changes nothing. |
| `./demo logs [service...]` | Follow logs, e.g. `./demo logs backend`. |
| `./demo test` | Run the deterministic cascade + intervention walkthrough (`data/scripts/demo_scenario.py`) inside the backend container. |
| `./demo down` | Stop the containers. **Data is preserved** — the next `./demo up` is fast. |
| `./demo reset [-y]` | **Destructive.** Delete the local Postgres, Neo4j and Redis Docker volumes, then bring everything back up clean. Prompts for confirmation; `-y` for non-interactive use. |

Run `./demo help` for the full usage text and the `DEMO_*_TIMEOUT` environment
variables that adjust the readiness budgets on a slow machine.

**Credentials.** The first run copies `.env.example` to `.env` if you do not
already have one; an existing `.env` is never modified. Those values —
`ripple` / `ripple_dev` for Postgres and `neo4j` / `ripple_dev_neo4j` for the
[Neo4j Browser](http://localhost:7474) — are **development and demo credentials
only, intended for a local machine and never for a deployment**. `.env` is
git-ignored and stays on your machine.

**Windows.** Run `./demo` from Git Bash or WSL.

The manual workflow in [Quick Start](#quick-start) above still works exactly as
documented: the containerised frontend sits behind the Compose `demo` profile,
so a plain `docker compose up --build` starts the same six services as before
and leaves port 5173 free for `npm run dev`. For broader host-side integration
assertions, `python e2e_test.py` remains available to anyone with Python
installed.

> **Note on verification:** the `./demo` runner's own logic is covered by
> offline tests (`backend/tests/test_demo_runner.py`) and its Compose wiring is
> validated with `docker compose config`, but a full live-stack run of
> `./demo up` has **not** been verified in the restricted environment this was
> developed in, where Docker image-layer downloads are blocked. Please report
> anything that behaves differently on a machine with normal registry access.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                     React Frontend                       │
│  MapLibre GL + deck.gl  │  Cytoscape.js  │  Controls    │
└────────────┬────────────┴───────┬────────┴──────────────┘
             │ REST               │ WebSocket
             ▼                    ▼
┌─────────────────────────────────────────────────────────┐
│                  FastAPI Backend                         │
│  CRUD API  │  Simulation Trigger  │  WS Wave Stream     │
└──────┬─────┴──────────┬───────────┴──────────┬──────────┘
       │                │                      │
       ▼                ▼                      ▼
┌──────────┐   ┌────────────────┐      ┌────────────┐
│ Postgres │   │  Celery Worker │      │   Redis    │
│ + PostGIS│   │  (in-memory    │      │  (broker + │
│          │   │   NetworkX     │      │   pubsub)  │
└──────────┘   │   cascade)     │      └────────────┘
               └───────┬────────┘
                       │ read-only
                       ▼
               ┌────────────────┐
               │     Neo4j      │
               │  + GDS (graph  │
               │   centrality)  │
               └────────────────┘
```

> **Important:** Simulations never mutate Neo4j. The worker reads the graph into an in-memory NetworkX model, runs the cascade, and writes results to PostgreSQL only.

---

## Project Structure

```
ripple/
├── docker-compose.yml          # All backend services
├── .env.example                # Environment template
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app
│   │   ├── config.py           # Settings
│   │   ├── celery_app.py       # Celery instance
│   │   ├── db/                 # Database connectors
│   │   ├── models/             # SQLAlchemy ORM
│   │   ├── schemas/            # Pydantic schemas
│   │   ├── api/                # REST + WebSocket routes
│   │   ├── services/           # Business logic
│   │   └── simulation/         # Cascade engine
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/         # React components (incl. deck.gl layer defs)
│       ├── stores/             # Zustand state
│       ├── api/                # TanStack Query hooks
│       └── types/              # Shared TypeScript types
└── data/
    ├── seed/                   # GeoJSON seed dataset
    └── scripts/                # Data generation
```

---

## Recommendation Engine

Ripple features an automated, deterministic resilience recommendation engine (`backend/app/services/recommendations.py`) designed to help operators prevent or arrest cascading infrastructure collapses.

### In-Memory Motter-Lai Resimulation
Rather than relying on heuristic estimates or unverified approximations, every recommendation candidate is evaluated through an **in-memory Motter-Lai cascade resimulation**:
1. The baseline network topology graph is cloned in memory.
2. The prospective intervention (node capacity hardening or redundancy connection) is applied to the cloned graph.
3. The exact baseline initial failure condition is replayed through the Motter-Lai overload engine.
4. The resulting cascade waves, failed node count, population affected, and global topological efficiency are measured against baseline outcomes to compute genuine, verified deltas (`verified: True`).

### Candidate Intervention Types
The engine generates two distinct intervention types based on topological vulnerability:
- **`upgrade_node` (Hardening):** Raises the operational capacity of critical downstream transit bottlenecks (such as wave-1 casualties absorbing initial failure shock). Candidates are evaluated at double their baseline capacity; the failure threshold is left unchanged.
- **`add_edge` (Redundancy):** Generates structural bypass connections linking surviving operational assets to disconnected downstream service areas that lost upstream connectivity due to wave-1 casualties.

### Deterministic Multi-Factor Ranking
Candidates are ranked by the sort key in `get_recommendations`, applied in this order:
1. **`failures_prevented`:** Net reduction in total failed nodes compared to the baseline cascade outcome.
2. **`protects_critical_services` (Hospital Preservation):** Among candidates preventing an equal number of failures, those that keep a hospital online rank higher.
3. **`raw_population_saved`:** Number of citizens spared from power, water, or telecommunications outages.
4. **`efficiency_gain`:** Post-cascade global network transmission efficiency delta ($\Delta E$).
5. **Canonical ID tie-breaking:** Deterministic alphabetical tie-breaking on UUID ensures 100% reproducible ordering.

Critical-service protection is therefore a tie-breaker *within* an impact tier, not
an override of it: an intervention that prevents more failures outranks one that
prevents fewer but happens to protect a hospital.

### 1-Click Execution & Seamless Comparison
Every candidate returned by `GET /api/simulations/{id}/recommendations` carries a pre-synthesized `scenario_payload`. When an operator applies a recommendation in the UI or via API:
- The scenario is created with one click without manual UUID copy-pasting.
- The mitigation scenario is rerun and registered directly in state.
- The `ScenarioCompare` dashboard allows side-by-side verification confirming strictly-better cascade outcomes.

---

## Key Metrics & Disclaimers

- **Betweenness Centrality by Default:** Structural bottleneck criticality is calculated using Betweenness Centrality over the directed dependency topology, identifying nodes that lie on the greatest fraction of shortest paths across municipal infrastructure sectors.
- **Population Impact & Municipal Cap:** Raw population impact sums the `population_served` across all affected assets. To prevent unrealistic double-counting across overlapping municipal service zones, the headline estimate is capped at the total study-area municipal population (**65,000** citizens for the Manipal study area).
- **Capped vs. Uncapped Figures:** Simulations record both the capped `population_affected_estimate` and the uncapped `raw_population_affected`. Because a baseline and an intervention can *both* exceed the cap — reporting an identical headline figure and hiding a real improvement — before/after comparisons in the UI, the demo script and the E2E test are computed on the uncapped total while the cap remains disclosed. `raw_population_affected` is null for simulations recorded before that field existed.
- **Unresolved Overlap Flag:** The system computes `is_population_capped` and `has_unresolved_overlap`. Both are returned by `GET /api/simulations/{id}` and rendered in the UI, so a capped or overlap-affected estimate is always flagged rather than presented as a precise count.
- **Cascade Stability:** `cascade_stabilized` is false when a cascade was still spreading at the configured wave guardrail (`MAX_CASCADE_WAVES`, default 50). Such a run returns a valid bounded result and is labelled as truncated in the UI rather than being discarded.
- **Data Provenance:** Every node carries a `data_quality` label (`observed` / `estimated` / `derived` / `simulated`). The shipped seed dataset is entirely synthetic and is labelled `estimated` throughout — see `data/seed/README.md` for the vocabulary.

---

## License (TBD)
