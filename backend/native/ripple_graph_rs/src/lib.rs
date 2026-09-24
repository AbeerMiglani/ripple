//! Rust acceleration for `calculate_global_efficiency`'s all-pairs
//! shortest-path hotspot. Mirrors the Python fallback in
//! `app.simulation.cascade._calculate_global_efficiency_py` exactly: BFS is
//! run over a *directed* adjacency (outgoing edges only, matching
//! `nx.all_pairs_shortest_path_length` on a `DiGraph`), and every ordered
//! reachable pair contributes `1 / distance`.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::VecDeque;

/// Sum of `1 / distance` over every node reachable from `source`, distance > 0.
fn bfs_sum_inverse_distances(source: usize, adjacency: &[Vec<usize>]) -> f64 {
    let mut visited = vec![false; adjacency.len()];
    let mut queue = VecDeque::new();
    visited[source] = true;
    queue.push_back((source, 0usize));

    let mut sum = 0.0;
    while let Some((node, dist)) = queue.pop_front() {
        if dist > 0 {
            sum += 1.0 / dist as f64;
        }
        for &next in &adjacency[node] {
            if !visited[next] {
                visited[next] = true;
                queue.push_back((next, dist + 1));
            }
        }
    }
    sum
}

/// Global efficiency of a directed graph: `(1 / (N*(N-1))) * sum(1/d(i,j))`
/// over all ordered pairs `i != j`, normalized by `n_baseline` (falling back
/// to `num_nodes`) rather than the number of surviving nodes -- see the
/// Python docstring this mirrors for why.
fn global_efficiency_impl(num_nodes: usize, edges: &[(usize, usize)], n_baseline: Option<usize>) -> f64 {
    let n = n_baseline.unwrap_or(num_nodes);
    if n < 2 {
        return 0.0;
    }

    let mut adjacency: Vec<Vec<usize>> = vec![Vec::new(); num_nodes];
    for &(u, v) in edges {
        adjacency[u].push(v);
    }

    let denom = (n * (n - 1)) as f64;

    // Embarrassingly parallel: each source's BFS only reads the shared
    // adjacency list and owns its own visited/queue state.
    let total: f64 = (0..num_nodes)
        .into_par_iter()
        .map(|source| bfs_sum_inverse_distances(source, &adjacency))
        .sum();

    total / denom
}

/// The first edge with an endpoint outside `0..num_nodes`, if any. Indexing
/// the adjacency list with one would panic, which surfaces in Python as an
/// opaque `PanicException` instead of a catchable `ValueError`.
fn first_out_of_range_edge(num_nodes: usize, edges: &[(usize, usize)]) -> Option<(usize, usize)> {
    edges
        .iter()
        .copied()
        .find(|&(u, v)| u >= num_nodes || v >= num_nodes)
}

#[pyfunction]
#[pyo3(signature = (num_nodes, edges, n_baseline=None))]
fn global_efficiency(
    py: Python<'_>,
    num_nodes: usize,
    edges: Vec<(usize, usize)>,
    n_baseline: Option<usize>,
) -> PyResult<f64> {
    if let Some((u, v)) = first_out_of_range_edge(num_nodes, &edges) {
        return Err(PyValueError::new_err(format!(
            "edge ({u}, {v}) references a node outside 0..{num_nodes}"
        )));
    }
    // The computation touches no Python objects, so the GIL is released for
    // its duration. Holding it stalled every other thread in the process --
    // including the API's event loop, since the recommendation engine calls
    // this twice per candidate from a request handler.
    Ok(py.allow_threads(|| global_efficiency_impl(num_nodes, &edges, n_baseline)))
}

#[pymodule]
fn ripple_graph_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(global_efficiency, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{first_out_of_range_edge, global_efficiency_impl};

    #[test]
    fn in_range_edges_pass_validation() {
        assert_eq!(first_out_of_range_edge(3, &[(0, 1), (1, 2)]), None);
        assert_eq!(first_out_of_range_edge(0, &[]), None);
    }

    #[test]
    fn out_of_range_edges_are_reported_not_panicked_on() {
        assert_eq!(first_out_of_range_edge(2, &[(0, 1), (1, 2)]), Some((1, 2)));
        assert_eq!(first_out_of_range_edge(2, &[(5, 0)]), Some((5, 0)));
    }

    #[test]
    fn empty_graph_has_zero_efficiency() {
        // Mirrors test_calculate_global_efficiency's disconnected 3-node case.
        assert_eq!(global_efficiency_impl(3, &[], None), 0.0);
    }

    #[test]
    fn path_graph_matches_hand_computed_value() {
        // A -> B -> C (indices 0, 1, 2): A->B=1, B->C=1, A->C=2.
        // E = (1/6) * (1/1 + 1/1 + 1/2) = 2.5/6 = 0.41666...
        let edges = vec![(0, 1), (1, 2)];
        let eff = global_efficiency_impl(3, &edges, None);
        assert!((eff - 0.416666).abs() < 0.001);
    }

    #[test]
    fn single_node_returns_zero() {
        assert_eq!(global_efficiency_impl(1, &[], None), 0.0);
    }

    #[test]
    fn directed_edges_only_traverse_forward() {
        // A -> B, no B -> A: A can reach B (distance 1), B cannot reach A.
        // N_baseline=2: denom = 2. Only one ordered pair contributes 1/1.
        let edges = vec![(0, 1)];
        let eff = global_efficiency_impl(2, &edges, None);
        assert!((eff - 0.5).abs() < 1e-9);
    }

    #[test]
    fn n_baseline_normalizes_by_the_original_graph_size() {
        // A -> B on a 2-node baseline, but normalized against N_baseline=4
        // (as when a surviving subgraph is scored against the pre-cascade
        // node count): denom = 4*3 = 12, sum = 1.0, eff = 1/12.
        let edges = vec![(0, 1)];
        let eff = global_efficiency_impl(2, &edges, Some(4));
        assert!((eff - (1.0 / 12.0)).abs() < 1e-9);
    }

    #[test]
    fn unreachable_pairs_contribute_nothing() {
        // Two disconnected components: 0->1, 2->3. Only those two ordered
        // pairs are reachable; the cross-component pairs contribute 0.
        let edges = vec![(0, 1), (2, 3)];
        let eff = global_efficiency_impl(4, &edges, None);
        // denom = 4*3 = 12, sum = 1/1 + 1/1 = 2.0
        assert!((eff - (2.0 / 12.0)).abs() < 1e-9);
    }
}
