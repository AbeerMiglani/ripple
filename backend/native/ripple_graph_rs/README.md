# ripple_graph_rs

A Rust/PyO3 acceleration for `calculate_global_efficiency` — the all-pairs
shortest-path calculation `app/simulation/cascade.py` runs twice per
`run_cascade` call (before and after the cascade). The recommendation engine
(`app/services/recommendations.py`) reruns `run_cascade` once per candidate
plus once for the baseline, so a single 10-candidate recommendation request
makes 20+ calls to this function — it was the measured hotspot.

This crate is entirely optional. `app/simulation/cascade.py` imports it
defensively:

```python
try:
    from ripple_graph_rs import global_efficiency as _rust_global_efficiency
except ImportError:
    _rust_global_efficiency = None
```

If it isn't built, everything works exactly as before via
`_calculate_global_efficiency_py`, just slower on large graphs. Nothing about
`pip install -e .` for the rest of the backend changes.

## Building locally

Requires a Rust toolchain ([rustup.rs](https://rustup.rs)) and `maturin`:

```sh
pip install maturin
cd backend/native/ripple_graph_rs

# Inside an activated virtualenv:
maturin develop --release

# Without a virtualenv (e.g. a bare system/container Python):
maturin build --release --out dist
pip install dist/*.whl
```

Then verify it's active:

```sh
python -c "from app.simulation import cascade; print(cascade._rust_global_efficiency)"
```

Docker builds get this automatically — `backend/Dockerfile` has a dedicated
`rust-builder` stage that compiles the wheel and copies only the compiled
artifact into the runtime image, so the Rust toolchain itself never ships.

## Testing

```sh
cargo test --release                                    # Rust-level unit tests
cd ../../.. && pytest backend/tests/test_global_efficiency_parity.py  # Rust vs Python parity
```

The parity test is skipped automatically when this extension isn't built —
there's nothing to compare against.

## Implementation notes

- The graph is treated as **directed**: BFS only follows outgoing edges,
  matching `nx.all_pairs_shortest_path_length` on a `DiGraph`. Treating it as
  undirected would silently change every efficiency score.
- Each source node's BFS is independent, so sources are parallelized with
  `rayon`.
- Normalization uses `n_baseline` when given (the pre-cascade node count),
  not the number of surviving nodes — same behavior as the Python fallback.
