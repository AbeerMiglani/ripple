# Ripple seed data

The seed files describe a synthetic infrastructure network
positioned at real Manipal, India coordinates. The coordinates make the demo
meaningful on the map; the assets, capacities, loads, population values, and
traffic-like relationships are synthetic estimates.

## Contents

- `nodes.geojson` — 120 infrastructure nodes:
  - 100 road junctions
  - 8 power substations
  - 6 water stations
  - 4 hospitals
  - 2 telecom towers
- `edges.json` — directed dependency links and bidirectional road links.

## Naming and data limits

Names use synthetic numbering such as `Junction 1`, `Hospital 1`, and
`Substation 1`. They are synthetic labels,
not real institution or road names. The dataset does not provide exact
population figures, observed traffic demand, or engineering failure data.

## Provenance vocabulary

Every node carries a `data_quality` label that is shown to the user in the map
tooltip, the criticality panel, and the failure-selection chips. Permitted
values:

| Value       | Meaning |
|-------------|---------|
| `observed`  | Taken directly from a public source and not altered. |
| `estimated` | Derived or assumed because no authoritative value was available. |
| `derived`   | Computed from other fields in this dataset. |
| `simulated` | Produced by the simulation engine rather than by any data source. |

**Everything in this seed dataset is `estimated`.** The assets, capacities,
loads, and population figures are synthetic, so nothing here may claim
`observed`. `verified` is not a valid value — it was previously the ingestion
default and overstated the provenance of fully synthetic data.

## Regeneration

From the repository root:

```bash
python data/scripts/generate_synthetic.py
```

The generator uses a fixed seed for scenario structure and validates graph
connectivity and hospital reachability before writing the files.

IDs are deterministic. Each node ID is derived from the node's `name` and each
edge ID from its `(edge_type, source_id, target_id)` triple — the same key the
database enforces as unique — so regeneration reproduces exactly the IDs in the
checked-in fixtures rather than minting new ones. They are RFC 4122 version-4
UUIDs, which the recommendation API requires, produced by hashing the identity
key rather than by drawing randomness.

Note that the checked-in fixtures carry hand-tuned water-station and telecom
loads that the generator does not currently reproduce, so regenerating replaces
those values and changes how the demo network cascades.

## Optional OSM road data

The backend includes an opt-in OSMnx exporter for road geometry. It writes a
separate dataset and never replaces these checked-in fixtures automatically:

```bash
pip install -e 'backend[osm]'
python -m app.services.osm_ingestion --place "Manipal, Karnataka, India" --output /data/osm
```

The OSM output contains road junctions and road links only. Operational
capacities and population exposure are estimates and must not be presented as
official infrastructure data.
