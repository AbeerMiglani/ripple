#!/usr/bin/env python3
"""Build the self-hosted basemap the frontend's geographic map renders.

Cuts a small vector-tile extract around the study area out of the Protomaps
daily planet build, and downloads the sprite and font glyphs the
``protomaps-themes-base`` style needs for icons and labels. Nothing is fetched
at runtime: the frontend serves these files statically, so the map works
offline and needs no API key.

Only HTTP byte ranges of the planet file are read -- roughly 500 small requests
for the default area, never the whole ~100 GB archive.

Requirements (not backend dependencies; this runs once, by hand)::

    pip install pmtiles requests

Usage, from the repository root::

    python data/scripts/build_basemap.py                 # newest daily build
    python data/scripts/build_basemap.py --date 20260923 # a specific build

Outputs (overwritten in place, so re-running is safe):

* ``frontend/public/tiles/manipal.pmtiles``
* ``frontend/public/basemap/sprites/dark{.json,.png,@2x.json,@2x.png}``
* ``frontend/public/basemap/fonts/<fontstack>/<range>.pbf``

See ``data/tiles/README.md`` for what the extract contains and its licensing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import math
import sys
from pathlib import Path
from urllib.parse import quote

try:
    import requests
    from pmtiles.reader import Reader
    from pmtiles.tile import TileType, zxy_to_tileid
    from pmtiles.writer import Writer
except ImportError as exc:  # pragma: no cover - a one-off tool, not app code
    sys.exit(f"missing dependency ({exc.name}); run: pip install pmtiles requests")

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC = REPO_ROOT / "frontend" / "public"
TILES_OUT = PUBLIC / "tiles" / "manipal.pmtiles"
SPRITES_OUT = PUBLIC / "basemap" / "sprites"
FONTS_OUT = PUBLIC / "basemap" / "fonts"

BUILD_URL = "https://build.protomaps.com/{date}.pmtiles"
ASSETS_URL = "https://protomaps.github.io/basemaps-assets"

#: (west, south, east, north). The seed network spans 74.771-74.811 E and
#: 13.331-13.368 N; this adds roughly 2 km on every side so the map has context
#: around it. Keep in sync with BASEMAP_BOUNDS in frontend/src/map/basemapStyle.ts.
DEFAULT_BBOX = (74.75, 13.31, 74.83, 13.39)
#: The planet build's own maximum; MapLibre overzooms past it.
DEFAULT_MAXZOOM = 15

SPRITE_FLAVOR = "dark"
#: Every font stack protomaps-themes-base's layers reference, except the
#: Devanagari one it uses only for Hindi-script labels.
FONT_STACKS = ("Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic")
#: Glyph ranges (256 codepoints each): Basic Latin + Latin-1, Latin
#: Extended-A/B, General Punctuation, and Kannada for local names that have no
#: English form. MapLibre fetches a range only when a label needs it.
GLYPH_RANGES = ("0-255", "256-511", "3072-3327", "8192-8447")


def tile_ranges(z: int, bbox: tuple[float, float, float, float]) -> tuple[range, range]:
    """The x and y tile ranges at zoom ``z`` that intersect ``bbox``."""
    west, south, east, north = bbox
    n = 2**z

    def x(lng: float) -> int:
        return min(n - 1, int((lng + 180.0) / 360.0 * n))

    def y(lat: float) -> int:
        rad = math.radians(lat)
        return min(n - 1, int((1.0 - math.asinh(math.tan(rad)) / math.pi) / 2.0 * n))

    return range(x(west), x(east) + 1), range(y(north), y(south) + 1)


def find_build(session: requests.Session, date: str | None) -> str:
    """URL of the requested daily build, or the newest one published."""
    if date:
        candidates = [date]
    else:
        today = dt.datetime.now(dt.timezone.utc).date()
        candidates = [(today - dt.timedelta(days=d)).strftime("%Y%m%d") for d in range(10)]
    for candidate in candidates:
        url = BUILD_URL.format(date=candidate)
        resp = session.get(url, headers={"Range": "bytes=0-126"}, timeout=30)
        if resp.status_code == 206:
            return url
    sys.exit(f"no Protomaps daily build found for {candidates[0]}..{candidates[-1]}")


def build_extract(session: requests.Session, url: str, bbox, maxzoom: int) -> tuple[int, int]:
    """Copy every tile intersecting ``bbox`` into TILES_OUT. Returns (tiles, bytes)."""

    @functools.lru_cache(maxsize=4096)
    def get_bytes(offset: int, length: int) -> bytes:
        resp = session.get(url, headers={"Range": f"bytes={offset}-{offset + length - 1}"}, timeout=60)
        if resp.status_code != 206:
            raise RuntimeError(f"expected a byte-range response from {url}, got HTTP {resp.status_code}")
        return resp.content

    reader = Reader(get_bytes)
    source_header = reader.header()
    if source_header["tile_type"] != TileType.MVT:
        sys.exit(f"unexpected tile type {source_header['tile_type']}")
    metadata = reader.metadata()

    tiles: list[tuple[int, bytes]] = []
    for z in range(maxzoom + 1):
        xs, ys = tile_ranges(z, bbox)
        for x in xs:
            for y in ys:
                data = reader.get(z, x, y)
                if data:
                    tiles.append((zxy_to_tileid(z, x, y), data))
        print(f"  z{z:<2} {len(xs) * len(ys):>3} tile(s)", flush=True)

    # The writer clusters (and deduplicates) tiles only when fed in tile-id order.
    tiles.sort(key=lambda item: item[0])
    west, south, east, north = bbox
    header = {
        "tile_type": TileType.MVT,
        "tile_compression": source_header["tile_compression"],
        "min_zoom": 0,
        "max_zoom": maxzoom,
        "min_lon_e7": int(west * 1e7),
        "min_lat_e7": int(south * 1e7),
        "max_lon_e7": int(east * 1e7),
        "max_lat_e7": int(north * 1e7),
        "center_zoom": min(14, maxzoom),
        "center_lon_e7": int((west + east) / 2 * 1e7),
        "center_lat_e7": int((south + north) / 2 * 1e7),
    }
    metadata = {
        **metadata,
        "description": f"Protomaps basemap extract for bbox {bbox} from {url}",
    }

    TILES_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(TILES_OUT, "wb") as f:
        writer = Writer(f)
        for tile_id, data in tiles:
            writer.write_tile(tile_id, data)
        writer.finalize(header, metadata)
    return len(tiles), TILES_OUT.stat().st_size


def download(session: requests.Session, url: str, dest: Path) -> int:
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return len(resp.content)


def download_style_assets(session: requests.Session) -> int:
    total = 0
    for suffix in (".json", ".png", "@2x.json", "@2x.png"):
        name = f"{SPRITE_FLAVOR}{suffix}"
        total += download(session, f"{ASSETS_URL}/sprites/v4/{name}", SPRITES_OUT / name)
    for stack in FONT_STACKS:
        for glyph_range in GLYPH_RANGES:
            total += download(
                session,
                f"{ASSETS_URL}/fonts/{quote(stack)}/{glyph_range}.pbf",
                FONTS_OUT / stack / f"{glyph_range}.pbf",
            )
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--bbox",
        type=lambda s: tuple(float(v) for v in s.split(",")),
        default=DEFAULT_BBOX,
        help="west,south,east,north in degrees (default: %(default)s)",
    )
    parser.add_argument("--maxzoom", type=int, default=DEFAULT_MAXZOOM)
    parser.add_argument("--date", help="daily build to use, YYYYMMDD (default: newest)")
    parser.add_argument("--skip-assets", action="store_true", help="rebuild tiles only")
    args = parser.parse_args()

    session = requests.Session()
    url = find_build(session, args.date)
    print(f"source: {url}")
    count, size = build_extract(session, url, args.bbox, args.maxzoom)
    print(f"wrote {TILES_OUT.relative_to(REPO_ROOT)}: {count} tiles, {size / 1024 / 1024:.2f} MiB")
    if not args.skip_assets:
        assets = download_style_assets(session)
        print(f"wrote sprites and fonts under {SPRITES_OUT.parent.relative_to(REPO_ROOT)}: {assets / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
