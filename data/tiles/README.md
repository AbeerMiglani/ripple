# Ripple basemap tiles

`manipal.pmtiles` is the self-hosted vector basemap the frontend renders via
[Protomaps](https://protomaps.com/)' PMTiles format — see
`frontend/src/components/MapView.tsx`. It's served as a static file (no
external tile host, no API key), which is what actually stops tile requests
from depending on network access this project's own sandbox has shown can be
unreliable (`tile.openstreetmap.org` and `tiles.openfreemap.org` are both
blocked by some egress policies).

## This is a placeholder, not real map data

The checked-in `manipal.pmtiles` is a **tiny synthetic file** (a few hundred
bytes) generated to prove the integration path end-to-end — the pmtiles
protocol registration, the vector style, and rendering with zero network
requests. It is not a real map of Manipal: it contains a handful of
fabricated polygons and one line repeated across a small zoom pyramid, not
actual roads, buildings, or water bodies.

## Replacing it with the real extract

1. Go to [protomaps.com/extracts](https://app.protomaps.com/) (or the current
   build-tool URL on protomaps.com) and build an extract for Manipal,
   Karnataka, India — a small bounding box around 13.35°N, 74.789°E is
   plenty; this doesn't need to be a huge area.
2. Download the resulting `.pmtiles` file.
3. Replace **both**:
   - `data/tiles/manipal.pmtiles` (this directory — the canonical copy, same
     convention as `data/seed/`)
   - `frontend/public/tiles/manipal.pmtiles` (the copy Vite actually serves
     at `/tiles/manipal.pmtiles` in dev and bundles into `dist/` on build)

If the real extract grows large enough that duplicating it into the
frontend's own build output stops being reasonable, switch
`frontend/public/tiles/manipal.pmtiles` for a backend static-file route
instead (a `StaticFiles` mount in `backend/app/main.py` is a two-line
addition) and point `MapView.tsx`'s `pmtiles://` source URL at that route.

## Regenerating the synthetic placeholder

The placeholder was built from hand-authored MVT (Mapbox Vector Tile)
buffers using `vt-pbf` for encoding and the `pmtiles` Python package's
`Writer` for archive assembly — there's no checked-in generator script for it
since it exists only to be replaced.
