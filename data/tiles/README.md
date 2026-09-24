# Ripple basemap

The geographic map draws a self-hosted [Protomaps](https://protomaps.com/)
vector basemap. It uses no tile host and no API key, and needs no network
access at runtime: every file is served statically by the frontend.

| File (under `frontend/public/`) | What it is |
|---|---|
| `tiles/manipal.pmtiles` | Vector tiles for the study area: earth, water, land use, roads, buildings, boundaries, places and POIs |
| `basemap/sprites/dark*` | POI and place icons for the `dark` flavour (1× and 2×) |
| `basemap/fonts/<fontstack>/<range>.pbf` | Noto Sans glyphs for the label layers |

The style that uses them is built in `frontend/src/map/basemapStyle.ts`, and
`frontend/src/map/basemap.test.ts` checks that everything it references
exists. There is only one copy of each file, in `frontend/public/`. That is the
directory Vite serves, and the only one inside the frontend's Docker build
context.

## What the extract covers

- **Area:** 74.75–74.83 °E, 13.31–13.39 °N. That is the seed network plus
  about 2 km on each side. The map's `maxBounds` stops panning at this edge,
  because beyond it there are no street-level tiles.
- **Zooms:** 0–15, which is the planet build's own maximum. MapLibre
  overzooms past 15.
- **Size:** about 1.3 MiB of tiles (131 tiles), plus about 1 MiB of glyphs and
  sprites.
- **Fonts:** Latin, Latin Extended and General Punctuation, plus Kannada for
  local names that have no English form. Labels prefer English (`lang: "en"`).

The seed network is **synthetic**, so its road junctions do not line up with
the real streets drawn underneath it. The map therefore hides healthy road
junctions and road links by default (Layers → Road junctions). Any junction
that fails, is restored or is selected is still drawn; see
`frontend/src/map/roadVisibility.ts`.

`TOPOLOGY_SOURCE=osm` does **not** align the two. It replaces the whole
network with a roads-only graph (no substations, water stations, hospitals or
towers, with zero load and zero population), on which almost nothing cascades.
Aligning properly would mean generating the seed's road layer from OSM and
keeping the synthetic utility layer on top of it.

## Regenerating

```bash
pip install pmtiles requests
python data/scripts/build_basemap.py                  # newest Protomaps daily build
python data/scripts/build_basemap.py --date 20260924  # a specific build
```

The script reads only the tiles it needs, using HTTP byte ranges against the
Protomaps daily planet build. It then downloads the sprite and glyphs from
`protomaps.github.io/basemaps-assets` and overwrites the files above in place.
If you change the area with `--bbox`, also update `BASEMAP_BOUNDS` in
`basemapStyle.ts`; the test fails until the two agree.

## Serving it elsewhere

PMTiles is read with HTTP **byte-range** requests. The Vite dev and preview
servers support them, as do nginx, Caddy and any object store. A server that
ignores `Range` and returns the whole file makes the map fail with "Server
returned no content-length header or content-length exceeding request".

## Licensing

- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors, under the ODbL. The Protomaps build of it is attributed on the
  map.
- Noto Sans glyphs are under the SIL Open Font License.
- The sprite comes from Protomaps
  [basemaps-assets](https://github.com/protomaps/basemaps-assets).
