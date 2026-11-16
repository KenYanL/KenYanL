#!/usr/bin/env python3
"""Pick the committed travel-map asset for the homepage workflow.

The map holiday follows the same UTC days as contrib-tetris so the travel
map and the brickfall graph change together:

* lunarnewyear: contrib-tetris's three-day UTC window (tabulated Lunar New
  Year plus the next two days, 2026-2035). Uses the Lucky Envelope assets,
  including the New Year firecracker bursts on the SVG (the PNG is static).
* midautumn: the same Mid-Autumn three-day UTC windows as contrib-tetris.
* valentine: 14 February (UTC).
* newyear: 1 January (UTC). Lucky Envelope assets, same ink and firecracker
  SVG as lunarnewyear.
* halloween: 31 October (UTC).
* christmas: 24-26 December (UTC).

Chinese festivals are checked first. On a dual-holiday day such as
2029-02-14 (Lunar New Year overlapping Valentine), the static map uses
lunarnewyear, matching contrib-tetris's Chinese-first rule.

A concrete workflow_dispatch palette uses ``assets/travel-map-<name>``
when those files exist, otherwise default.

Prints shell assignments for the staging step:
  PALETTE, DATE_UTC, SVG, PNG
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "assets"


def _holiday_window(anchors: tuple[date, ...], days: int = 3) -> frozenset[date]:
    out: list[date] = []
    for anchor in anchors:
        for offset in range(days):
            out.append(anchor + timedelta(days=offset))
    return frozenset(out)


# Same anchors as contrib-tetris (tabulated festival day plus the next two UTC days).
LUNAR_NEW_YEAR_DATES = _holiday_window(
    (
        date(2026, 2, 17),
        date(2027, 2, 6),
        date(2028, 1, 26),
        date(2029, 2, 13),
        date(2030, 2, 3),
        date(2031, 1, 23),
        date(2032, 2, 11),
        date(2033, 1, 31),
        date(2034, 2, 19),
        date(2035, 2, 8),
    )
)
MIDAUTUMN_DATES = _holiday_window(
    (
        date(2026, 9, 25),
        date(2027, 9, 15),
        date(2028, 10, 3),
        date(2029, 9, 22),
        date(2030, 9, 12),
        date(2031, 10, 1),
        date(2032, 9, 19),
        date(2033, 9, 8),
        date(2034, 9, 27),
        date(2035, 9, 16),
    )
)

# Recurring Western / fixed-day holidays. Chinese windows above take priority.
AUTO_DAYS: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("valentine", ((2, 14),)),
    ("newyear", ((1, 1),)),
    ("halloween", ((10, 31),)),
    ("christmas", ((12, 24), (12, 25), (12, 26))),
)


def parse_day(raw: str | None) -> date:
    if raw is None or raw.strip() == "":
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(raw.strip())


def auto_palette(day: date) -> str:
    # Chinese festivals first so a dual-holiday UTC day uses that map.
    if day in LUNAR_NEW_YEAR_DATES:
        return "lunarnewyear"
    if day in MIDAUTUMN_DATES:
        return "midautumn"
    key = (day.month, day.day)
    for name, days in AUTO_DAYS:
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
    if args and args[0] in {"--self-test", "--test"}:
        return self_test()
    requested = args[0] if args else os.environ.get("REQUESTED", "auto")
    day = parse_day(os.environ.get("TRAVEL_DATE") or (args[1] if len(args) > 1 else None))
    palette = resolve(requested, day)
    svg, png = asset_paths(palette)
    if not svg.is_file() or not png.is_file():
        print(f"error: missing travel map assets for {palette}", file=sys.stderr)
        return 1
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


def self_test() -> int:
    cases: list[tuple[str, date, str]] = [
        ("auto", date(2026, 2, 14), "valentine"),
        ("auto", date(2026, 2, 13), "default"),
        ("auto", date(2026, 2, 15), "default"),
        ("auto", date(2027, 2, 6), "lunarnewyear"),
        ("auto", date(2027, 2, 7), "lunarnewyear"),
        ("auto", date(2027, 2, 8), "lunarnewyear"),
        ("auto", date(2027, 2, 9), "default"),
        ("auto", date(2027, 9, 15), "midautumn"),
        ("auto", date(2027, 9, 16), "midautumn"),
        ("auto", date(2027, 9, 17), "midautumn"),
        ("auto", date(2027, 9, 14), "default"),
        ("auto", date(2027, 9, 18), "default"),
        ("auto", date(2026, 9, 25), "midautumn"),
        ("auto", date(2026, 10, 31), "halloween"),
        ("auto", date(2026, 12, 24), "christmas"),
        ("auto", date(2026, 12, 25), "christmas"),
        ("auto", date(2026, 12, 26), "christmas"),
        ("auto", date(2026, 7, 1), "default"),
        ("auto", date(2027, 1, 1), "newyear"),
        ("auto", date(2026, 12, 31), "default"),
        # Dual-holiday: Lunar New Year 2029 overlaps Valentine. Chinese first.
        ("auto", date(2029, 2, 13), "lunarnewyear"),
        ("auto", date(2029, 2, 14), "lunarnewyear"),
        ("auto", date(2029, 2, 15), "lunarnewyear"),
        ("halloween", date(2026, 7, 1), "halloween"),
        ("christmas", date(2026, 7, 1), "christmas"),
        ("valentine", date(2026, 7, 1), "valentine"),
        ("lunarnewyear", date(2026, 7, 1), "lunarnewyear"),
        ("newyear", date(2026, 7, 1), "newyear"),
        ("midautumn", date(2026, 7, 1), "midautumn"),
        ("not-a-palette", date(2026, 7, 1), "default"),
    ]
    failed = 0
    for req, day, exp in cases:
        got = resolve(req, day)
        if got != exp:
            print(f"FAIL {req} {day} got {got} expected {exp}", file=sys.stderr)
            failed += 1
    # Assets for every concrete holiday exist.
    holiday_names = ("valentine", "lunarnewyear", "newyear", "midautumn", "halloween", "christmas")
    for name in holiday_names:
        svg, png = asset_paths(name)
        if not svg.is_file() or not png.is_file():
            print(f"FAIL missing assets for {name}", file=sys.stderr)
            failed += 1
    # New Year / Lunar New Year SVGs carry the firecracker bursts; others do not.
    firecracker_names = ("newyear", "lunarnewyear")
    for name in holiday_names + ("default",):
        svg, _png = asset_paths(name)
        raw = svg.read_text(encoding="utf-8") if svg.is_file() else ""
        has = 'id="firecrackers"' in raw and "prefers-reduced-motion" in raw
        expect = name in firecracker_names
        if has != expect:
            print(
                f"FAIL {name} firecracker markers {'present' if has else 'missing'}, "
                f"expected {'present' if expect else 'absent'}",
                file=sys.stderr,
            )
            failed += 1
    total = len(cases) + 6 + len(holiday_names) + 1
    passed = total - failed
    print(f"{passed}/{total} resolver cases passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
