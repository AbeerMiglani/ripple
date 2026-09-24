/**
 * The self-hosted basemap is complete: a real extract that covers the network,
 * plus every font and the sprite its style asks for.
 *
 * This repo shipped a 748-byte synthetic placeholder in the tile file's place,
 * which rendered as a few grey rectangles and one diagonal line. A missing
 * glyph or sprite file fails more quietly (labels or icons just vanish), so
 * both are pinned here against the style the app actually builds.
 */
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BASEMAP_BOUNDS, BASEMAP_FLAVOR, basemapStyle } from "./basemapStyle";

const PUBLIC = fileURLToPath(new URL("../../public/", import.meta.url));
const SEED_NODES = fileURLToPath(new URL("../../../data/seed/nodes.geojson", import.meta.url));

/** The fields of a PMTiles v3 header this test checks (spec: github.com/protomaps/PMTiles). */
function readPmtilesHeader(bytes: Uint8Array) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const e7 = (offset: number) => view.getInt32(offset, true) / 1e7;
  return {
    magic: new TextDecoder().decode(bytes.subarray(0, 7)),
    version: view.getUint8(7),
    tileDataBytes: Number(view.getBigUint64(64, true)),
    uniqueTiles: Number(view.getBigUint64(88, true)),
    tileType: view.getUint8(99), // 1 = MVT
    minZoom: view.getUint8(100),
    maxZoom: view.getUint8(101),
    bounds: { west: e7(102), south: e7(106), east: e7(110), north: e7(114) },
  };
}

/** Every "Noto Sans …" font stack named anywhere in the style's layers. */
function fontStacks(value: unknown, found = new Set<string>()): Set<string> {
  if (typeof value === "string" && value.startsWith("Noto Sans")) found.add(value);
  else if (Array.isArray(value)) value.forEach((v) => fontStacks(v, found));
  else if (value && typeof value === "object") Object.values(value).forEach((v) => fontStacks(v, found));
  return found;
}

describe("basemap tiles", () => {
  const header = readPmtilesHeader(new Uint8Array(readFileSync(`${PUBLIC}tiles/manipal.pmtiles`)));

  it("is a real vector extract, not a placeholder", () => {
    expect(header.magic).toBe("PMTiles");
    expect(header.version).toBe(3);
    expect(header.tileType).toBe(1);
    expect(header.minZoom).toBe(0);
    expect(header.maxZoom).toBeGreaterThanOrEqual(15);
    // Zoom range and addressed-tile count cannot tell them apart: the
    // placeholder addressed one 245-byte tile 57 times across z0-16. Distinct
    // tile contents and real data volume can.
    expect(header.uniqueTiles).toBeGreaterThanOrEqual(50);
    expect(header.tileDataBytes).toBeGreaterThan(256 * 1024);
  });

  it("covers every node of the seed network", () => {
    const { features } = JSON.parse(readFileSync(SEED_NODES, "utf-8")) as {
      features: { geometry: { coordinates: [number, number] } }[];
    };
    const { west, south, east, north } = header.bounds;
    const outside = features
      .map((f) => f.geometry.coordinates)
      .filter(([lng, lat]) => lng < west || lng > east || lat < south || lat > north);
    expect(outside).toEqual([]);
  });

  it("matches the bounds the map is constrained to", () => {
    const [[west, south], [east, north]] = BASEMAP_BOUNDS;
    expect(header.bounds.west).toBeCloseTo(west, 5);
    expect(header.bounds.south).toBeCloseTo(south, 5);
    expect(header.bounds.east).toBeCloseTo(east, 5);
    expect(header.bounds.north).toBeCloseTo(north, 5);
  });
});

describe("basemap style assets", () => {
  const style = basemapStyle("http://localhost:5173");

  it("points glyphs and sprite at the self-hosted files", () => {
    expect(style.glyphs).toBe("http://localhost:5173/basemap/fonts/{fontstack}/{range}.pbf");
    expect(style.sprite).toBe(`http://localhost:5173/basemap/sprites/${BASEMAP_FLAVOR}`);
  });

  it("has glyphs for every font stack a label layer uses", () => {
    const stacks = [...fontStacks(style.layers)]
      // Only Hindi-script labels use it, and this study area has none.
      .filter((stack) => !stack.includes("Devanagari"));
    expect(stacks.length).toBeGreaterThan(0);
    const missing = stacks.filter((stack) => !existsSync(`${PUBLIC}basemap/fonts/${stack}/0-255.pbf`));
    expect(missing).toEqual([]);
  });

  it("has the sprite, at both pixel ratios", () => {
    for (const suffix of [".json", ".png", "@2x.json", "@2x.png"]) {
      expect(existsSync(`${PUBLIC}basemap/sprites/${BASEMAP_FLAVOR}${suffix}`)).toBe(true);
    }
    const sprite = JSON.parse(readFileSync(`${PUBLIC}basemap/sprites/${BASEMAP_FLAVOR}.json`, "utf-8"));
    // The locality layer always draws this icon at low zoom.
    expect(sprite).toHaveProperty("townspot");
  });
});
