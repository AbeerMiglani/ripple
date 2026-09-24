/**
 * The geographic map's basemap style: a self-hosted Protomaps vector extract of
 * the study area, with the sprite and font glyphs its labels and icons need.
 *
 * Every asset is a static file under `frontend/public`, built by
 * `data/scripts/build_basemap.py` (see `data/tiles/README.md`), so the map needs
 * no tile host, API key or network access at runtime.
 *
 * Kept free of runtime maplibre imports so it can be exercised in plain Node
 * tests (`basemap.test.ts`).
 */
import type { LngLatBoundsLike, StyleSpecification } from "maplibre-gl";
import { layers, namedTheme } from "protomaps-themes-base";

export const BASEMAP_SOURCE_ID = "protomaps";

/** Served by the pmtiles protocol handler MapView registers; path is site-relative. */
export const BASEMAP_PMTILES_URL = "pmtiles:///tiles/manipal.pmtiles";

/**
 * The area the extract covers: (west, south), (east, north). Beyond it there
 * are no tiles at street zooms, so the map is kept inside it. Must match
 * DEFAULT_BBOX in data/scripts/build_basemap.py.
 */
export const BASEMAP_BOUNDS: [[number, number], [number, number]] = [
  [74.75, 13.31],
  [74.83, 13.39],
];

/** The Protomaps flavour; the sprite is built for the same one. */
export const BASEMAP_FLAVOR = "dark";

const ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors, © <a href="https://protomaps.com" target="_blank">Protomaps</a>';

/**
 * Build the style. `origin` is the page origin (`window.location.origin`):
 * MapLibre resolves `glyphs` and `sprite` as absolute URLs, not page-relative.
 */
export function basemapStyle(origin: string): StyleSpecification {
  return {
    version: 8,
    glyphs: `${origin}/basemap/fonts/{fontstack}/{range}.pbf`,
    sprite: `${origin}/basemap/sprites/${BASEMAP_FLAVOR}`,
    sources: {
      [BASEMAP_SOURCE_ID]: {
        type: "vector",
        url: BASEMAP_PMTILES_URL,
        attribution: ATTRIBUTION,
      },
    },
    // Earth, water, land use, roads, buildings, boundaries and labels, styled
    // against this source. English names where OSM has them, the local name
    // otherwise.
    layers: layers(BASEMAP_SOURCE_ID, namedTheme(BASEMAP_FLAVOR), { lang: "en" }),
  };
}

/** Typed for `maxBounds` without importing maplibre at runtime. */
export const BASEMAP_MAX_BOUNDS: LngLatBoundsLike = BASEMAP_BOUNDS;
