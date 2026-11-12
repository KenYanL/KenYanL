#!/usr/bin/env python3
"""Pick the committed travel-map asset for the homepage workflow.

Mirrors KenYanL/travel-footprint ``resolve_palette(..., auto)`` on *main*
as of 2026-11-11: halloween on 31 October (UTC), christmas on 24, 25, and
26 December (UTC), and default every other day.

This is intentionally *not* contrib-tetris's catalog. Lunar New Year,
Valentine, Mid-Autumn, and New Year change the brickfall graph when
contrib-tetris@v1 supports them; travel-footprint main has no those
palettes yet, so the map stays default on those days. When a travel
palette is later released, add ``assets/travel-map-<name>.svg`` (and
``.png``) and, if ``auto`` should select it, extend AUTO_RULES below.

A concrete workflow_dispatch palette uses ``assets/travel-map-<name>``
when those files exist, otherwise default.

Prints shell assignments for the staging step:
  PALETTE, DATE_UTC, SVG, PNG
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "assets"

# (palette, month-day or inclusive (month, day) tuples). First match wins.
AUTO_RULES: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("halloween", ((10, 31),)),
    ("christmas", ((12, 24), (12, 25), (12, 26))),
)


def parse_day(raw: str | None) -> date:
    if raw is None or raw.strip() == "":
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(raw.strip())


def auto_palette(day: date) -> str:
    key = (day.month, day.day)
    for name, days in AUTO_RULES:
        if key in days:
            return name
    return "default"


def asset_paths(palette: str) -> tuple[Path, Path]:
    if palette == "default":
        return ASSETS / "travel-map.svg", ASSETS / "travel-map.png"
    return ASSETS / f"travel-map-{palette}.svg", ASSETS / f"travel-map-{palette}.png"


def resolve(requested: str, day: date) -> str:
    name = (requested or "auto").strip().lower() or "auto"
    if name == "auto":
        name = auto_palette(day)
    svg, png = asset_paths(name)
    if not svg.is_file() or not png.is_file():
        name = "default"
    return name


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    requested = args[0] if args else os.environ.get("REQUESTED", "auto")
    day = parse_day(os.environ.get("TRAVEL_DATE") or (args[1] if len(args) > 1 else None))
    palette = resolve(requested, day)
    svg, png = asset_paths(palette)
    if not svg.is_file() or not png.is_file():
        print(f"error: missing travel map assets for {palette}", file=sys.stderr)
        return 1
    # Relativize to the repo root so the workflow can test -f and cp.
    try:
        svg_s = str(svg.relative_to(Path.cwd()))
        png_s = str(png.relative_to(Path.cwd()))
    except ValueError:
        svg_s, png_s = str(svg), str(png)
    print(f"PALETTE={palette}")
    print(f"DATE_UTC={day.isoformat()}")
    print(f"SVG={svg_s}")
    print(f"PNG={png_s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
