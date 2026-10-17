#!/usr/bin/env python3
"""Build a README-safe Tetris / LEGO animation of a GitHub contribution graph.

Fetches the last year of contributions (GraphQL when GITHUB_TOKEN is set,
otherwise the public contributions calendar) and writes two self-contained
SVGs: a light theme and a dark theme. Bricks are grouped into tetrominoes,
fall column by column from the bottom of each stack upward, and lock into
the real contribution grid. Animation is pure CSS so it runs inside a GitHub
README <img>.

No third-party packages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

# Geometry matches a GitHub-style calendar (chunky enough for studs to read
# next to the snake graph, which uses 12px cells on a 16px pitch).
CELL = 12
STEP = 16
RX = 2.4
LEFT = 40
RIGHT = 16
BOTTOM = 14
CAPTION_BASELINE = 16
MONTH_BASELINE = 34
GRID_TOP = 42

# Motion. Tuned so a full year locks, holds long enough to read, then resets.
FALL = 0.78
STACK_GAP = 0.06
WAVE_START = 0.35
WAVE_STEP = 0.055
HOLD_AFTER = 4.4
CLEAR = 0.9
TAIL = 1.7

# Same greens as Platane/snk's github / github-dark palettes, so this graph
# sits next to the existing snake with matching empty-cell and level colors.
PALETTES = {
    "light": {
        "name": "github",
        "levels": ("#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"),
        "text": "#656d76",
        "empty_stroke": "#d0d7de",
    },
    "dark": {
        "name": "github-dark",
        "levels": ("#161b22", "#01311f", "#034525", "#0f6d31", "#00c647"),
        "text": "#8b949e",
        "empty_stroke": "#21262d",
    },
}

LEVEL_NAMES = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}

# Classic polyominoes, largest first. Rotations are expanded at import time.
# Offsets are (row, col) with row growing downward.
_RAW_SHAPES: list[tuple[str, tuple[tuple[int, int], ...]]] = [
    ("O", ((0, 0), (0, 1), (1, 0), (1, 1))),
    ("T", ((0, 0), (0, 1), (0, 2), (1, 1))),
    ("L", ((0, 0), (1, 0), (2, 0), (2, 1))),
    ("J", ((0, 1), (1, 1), (2, 0), (2, 1))),
    ("S", ((0, 1), (0, 2), (1, 0), (1, 1))),
    ("Z", ((0, 0), (0, 1), (1, 1), (1, 2))),
    ("I", ((0, 0), (0, 1), (0, 2), (0, 3))),
    ("I3", ((0, 0), (0, 1), (0, 2))),
    ("L3", ((0, 0), (1, 0), (1, 1))),
    ("I2", ((0, 0), (0, 1))),
    ("O1", ((0, 0),)),
]


@dataclass
class Day:
    date: date
    level: int
    count: int


@dataclass
class Calendar:
    username: str
    weeks: list[list[Day | None]]

    @property
    def total(self) -> int:
        return sum(day.count for week in self.weeks for day in week if day)

    @property
    def levels(self) -> list[list[int]]:
        grid = []
        for row in range(7):
            line = []
            for week in self.weeks:
                day = week[row] if row < len(week) else None
                line.append(day.level if day else 0)
            grid.append(line)
        return grid


@dataclass
class Piece:
    name: str
    cells: list[tuple[int, int, int]]  # row, col, level
    start: float = 0.0
    drop: float = 0.0
    group: int = 0


@dataclass
class Timeline:
    duration: float
    hold_end: float
    clear_end: float


def _normalize(cells: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    cells = list(cells)
    min_r = min(r for r, _ in cells)
    min_c = min(c for _, c in cells)
    return tuple(sorted((r - min_r, c - min_c) for r, c in cells))


def _rotate(cells: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Clockwise 90 degrees in screen coordinates (row down, col right)."""
    return [(c, -r) for r, c in cells]


def build_catalog() -> list[tuple[str, tuple[tuple[int, int], ...]]]:
    catalog: list[tuple[str, tuple[tuple[int, int], ...]]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for name, base in _RAW_SHAPES:
        current = list(base)
        for _ in range(4):
            norm = _normalize(current)
            if norm not in seen:
                seen.add(norm)
                catalog.append((name, norm))
            current = _rotate(current)
    return catalog


SHAPES = build_catalog()


def extract_pieces(levels: list[list[int]]) -> list[Piece]:
    """Greedy bottom-up packing into tetrominoes, then smaller polyominoes.

    The anchor is always the current bottom-most filled cell, and every shape
    is attached so that anchor sits on the shape's bottom edge. That keeps the
    packing stacked the way the pieces will later fall.
    """
    rows = len(levels)
    cols = len(levels[0]) if rows else 0
    occupied = {(r, c) for r in range(rows) for c in range(cols) if levels[r][c] > 0}
    pieces: list[Piece] = []
    while occupied:
        anchor_r, anchor_c = max(occupied, key=lambda rc: (rc[0], -rc[1]))
        placed: tuple[str, list[tuple[int, int, int]]] | None = None
        for name, shape in SHAPES:
            bottom = max(r for r, _ in shape)
            for shape_r, shape_c in shape:
                if shape_r != bottom:
                    continue
                origin_r = anchor_r - shape_r
                origin_c = anchor_c - shape_c
                cells: list[tuple[int, int, int]] = []
                ok = True
                for sr, sc in shape:
                    rr = origin_r + sr
                    cc = origin_c + sc
                    if not (0 <= rr < rows and 0 <= cc < cols) or (rr, cc) not in occupied:
                        ok = False
                        break
                    cells.append((rr, cc, levels[rr][cc]))
                if ok:
                    placed = (name, cells)
                    break
            if placed:
                break
        if placed is None:
            placed = ("O1", [(anchor_r, anchor_c, levels[anchor_r][anchor_c])])
        name, cells = placed
        for r, c, _ in cells:
            occupied.remove((r, c))
        pieces.append(Piece(name=name, cells=cells))
    return pieces


def _strong_components(ids: list[int], succ: dict[int, list[int]]) -> list[list[int]]:
    """Tarjan. `succ` edges point from a piece to the pieces that must wait for it."""
    index = 0
    stack: list[int] = []
    on_stack: set[int] = set()
    indices: dict[int, int] = {}
    low: dict[int, int] = {}
    components: list[list[int]] = []

    def visit(node: int) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in succ.get(node, []):
            if nxt not in indices:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], indices[nxt])
        if low[node] == indices[node]:
            comp: list[int] = []
            while True:
                top = stack.pop()
                on_stack.remove(top)
                comp.append(top)
                if top == node:
                    break
            components.append(comp)

    for node in ids:
        if node not in indices:
            visit(node)
    return components


def schedule(pieces: list[Piece], ncols: int) -> list[list[Piece]]:
    """Bottom-up, left-to-right drop times.

    In each column the lower piece lands before anything above it starts, so a
    falling brick never passes through one that is already locked. Pieces that
    hook around each other (a dependency cycle) fall together as one rigid group.
    Returns those simultaneous groups.
    """
    if not pieces:
        return []
    for i, piece in enumerate(pieces):
        piece.group = i

    succ: dict[int, list[int]] = defaultdict(list)
    for col in range(ncols):
        touching: list[tuple[int, int]] = []
        for piece in pieces:
            rows = [r for r, c, _ in piece.cells if c == col]
            if rows:
                touching.append((max(rows), piece.group))
        touching.sort(key=lambda item: -item[0])
        for (_, lower), (_, upper) in zip(touching, touching[1:]):
            if upper not in succ[lower]:
                succ[lower].append(upper)

    comps = _strong_components([p.group for p in pieces], succ)
    members = {p.group: p for p in pieces}
    comp_of: dict[int, int] = {}
    groups: list[list[Piece]] = []
    for comp_i, comp in enumerate(comps):
        group = [members[i] for i in comp]
        groups.append(group)
        for i in comp:
            comp_of[i] = comp_i

    pred: dict[int, set[int]] = defaultdict(set)
    for lower, uppers in succ.items():
        for upper in uppers:
            a, b = comp_of[lower], comp_of[upper]
            if a != b:
                pred[b].add(a)

    group_start: dict[int, float] = {}

    def start_of(idx: int) -> float:
        if idx in group_start:
            return group_start[idx]
        group = groups[idx]
        cols = {c for piece in group for _, c, _ in piece.cells}
        earliest = WAVE_START + min(cols) * WAVE_STEP
        ready = earliest
        for earlier in pred.get(idx, ()):
            ready = max(ready, start_of(earlier) + FALL + STACK_GAP)
        group_start[idx] = ready
        return ready

    for idx, group in enumerate(groups):
        start = start_of(idx)
        for piece in group:
            piece.start = start
    return groups


def assign_drops(groups: list[list[Piece]]) -> None:
    """Shared translation per simultaneous group, so relative spacing holds."""
    for group in groups:
        bottom = max(GRID_TOP + r * STEP for piece in group for r, _, _ in piece.cells)
        drop = -(bottom + CELL + 6)
        for piece in group:
            piece.drop = drop


def build_timeline(pieces: list[Piece]) -> Timeline:
    if not pieces:
        hold_end = 6.0
    else:
        last_land = max(p.start + FALL + 0.18 for p in pieces)
        hold_end = last_land + HOLD_AFTER
    clear_end = hold_end + CLEAR
    duration = clear_end + TAIL
    return Timeline(duration=duration, hold_end=hold_end, clear_end=clear_end)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    channels = [max(0, min(255, int(round(channel)))) for channel in rgb]
    return "#{:02x}{:02x}{:02x}".format(*channels)


def mix(a: str, b: str, t: float) -> str:
    left, right = _hex_to_rgb(a), _hex_to_rgb(b)
    return _rgb_to_hex(tuple(left[i] * (1 - t) + right[i] * t for i in range(3)))


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _stops(events: list[tuple[float, str]], duration: float) -> list[tuple[float, str]]:
    """Convert second-timestamps into strictly increasing percentages."""
    stops: list[tuple[float, str]] = []
    last = -1.0
    for time, decl in events:
        pct = max(0.0, min(100.0, time / duration * 100.0))
        if pct <= last + 0.05:
            pct = min(100.0, last + 0.05)
        stops.append((pct, decl))
        last = pct
    if stops[0][0] != 0.0:
        stops.insert(0, (0.0, stops[0][1]))
    if stops[-1][0] != 100.0:
        stops.append((100.0, stops[-1][1]))
    return stops


def _keyframe_body(drop: float, start: float, timeline: Timeline) -> str:
    land = start + FALL
    hidden = f"translateY({drop:.1f}px)"
    # Cubic ease-in: most of the travel happens in the last third (gravity).
    samples = []
    for u in (0.42, 0.68, 0.86):
        remain = (1 - u) ** 3
        samples.append((start + 0.08 + (FALL - 0.08) * u, f"translateY({drop * remain:.1f}px)", 1))
    events: list[tuple[float, str]] = [
        (0.0, f"transform:{hidden};opacity:0"),
        (start, f"transform:{hidden};opacity:0"),
        (start + 0.07, f"transform:{hidden};opacity:1"),
    ]
    for time, transform, opacity in samples:
        events.append((time, f"transform:{transform};opacity:{opacity}"))
    events.extend(
        [
            (land, "transform:translateY(0);opacity:1"),
            (land + 0.08, "transform:translateY(-2.4px);opacity:1"),
            (land + 0.16, "transform:translateY(0.6px);opacity:1"),
            (land + 0.24, "transform:translateY(0);opacity:1"),
            (timeline.hold_end, "transform:translateY(0);opacity:1"),
            (timeline.clear_end, "transform:translateY(-12px);opacity:0"),
            (min(timeline.duration, timeline.clear_end + 0.12), f"transform:{hidden};opacity:0"),
            (timeline.duration, f"transform:{hidden};opacity:0"),
        ]
    )
    # Keep the authored order, but times must be non-decreasing for _stops.
    events.sort(key=lambda item: item[0])
    parts = []
    for pct, decl in _stops(events, timeline.duration):
        parts.append(f"{pct:.3f}%{{{decl}}}")
    return "".join(parts)


def _month_labels(calendar: Calendar) -> list[tuple[int, str]]:
    labels: list[tuple[int, str]] = []
    last_col = -10
    for col, week in enumerate(calendar.weeks):
        present = [day for day in week if day]
        if not present:
            continue
        name = None
        if col == 0:
            name = MONTHS[present[0].date.month - 1]
        else:
            for day in present:
                if day.date.day == 1:
                    name = MONTHS[day.date.month - 1]
                    break
        if name and col - last_col >= 2:
            labels.append((col, name))
            last_col = col
    return labels


def _gradients(levels: tuple[str, ...]) -> str:
    chunks: list[str] = []
    for level in range(1, 5):
        base = levels[level]
        top = mix(base, "#ffffff", 0.34)
        bottom = mix(base, "#000000", 0.22)
        stud_hi = mix(base, "#ffffff", 0.78)
        stud_mid = mix(base, "#ffffff", 0.12)
        stud_lo = mix(base, "#000000", 0.32)
        chunks.append(
            f'<linearGradient id="f{level}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{top}"/>'
            f'<stop offset="0.38" stop-color="{base}"/>'
            f'<stop offset="1" stop-color="{bottom}"/>'
            f"</linearGradient>"
            f'<radialGradient id="s{level}" cx="32%" cy="30%" r="70%">'
            f'<stop offset="0" stop-color="{stud_hi}"/>'
            f'<stop offset="0.55" stop-color="{stud_mid}"/>'
            f'<stop offset="1" stop-color="{stud_lo}"/>'
            f"</radialGradient>"
        )
    return "".join(chunks)


def _brick(level: int, levels: tuple[str, ...]) -> str:
    base = levels[level]
    stroke = mix(base, "#000000", 0.38)
    return (
        f'<rect width="{CELL}" height="{CELL}" rx="{RX}" fill="url(#f{level})" '
        f'stroke="{stroke}" stroke-width="0.7"/>'
        f'<rect x="1.15" y="1.05" width="{CELL - 2.3:.2f}" height="1.7" rx="0.8" '
        f'fill="#fff" fill-opacity="0.38"/>'
        f'<rect x="1.4" y="{CELL - 2.7:.2f}" width="{CELL - 2.8:.2f}" height="1.35" rx="0.65" '
        f'fill="#000" fill-opacity="0.22"/>'
        f'<circle cx="{CELL / 2:.2f}" cy="{CELL / 2 + 0.35:.2f}" r="2.35" '
        f'fill="url(#s{level})" stroke="{stroke}" stroke-width="0.45"/>'
        f'<circle cx="{CELL / 2 - 0.75:.2f}" cy="{CELL / 2 - 0.55:.2f}" r="0.7" '
        f'fill="#fff" fill-opacity="0.62"/>'
    )


def _styles(pieces: list[Piece], timeline: Timeline) -> str:
    shared: dict[str, str] = {}
    classes: list[str] = []
    for index, piece in enumerate(pieces):
        body = _keyframe_body(piece.drop, piece.start, timeline)
        name = shared.get(body)
        if name is None:
            name = f"k{len(shared)}"
            shared[body] = name
        classes.append(f".p{index}{{animation-name:{name}}}")
    frames = "".join(f"@keyframes {name}{{{body}}}" for body, name in shared.items())
    duration = f"{timeline.duration:.2f}s"
    base = (
        ".lbl{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;"
        "font-size:12px}"
        ".mo{font-size:11px}"
        ".dy{font-size:11px}"
        f".p{{animation-duration:{duration};animation-timing-function:linear;"
        "animation-iteration-count:infinite;animation-fill-mode:both;"
        "transform-box:fill-box}"
        "@media (prefers-reduced-motion:reduce){"
        ".p{animation:none!important;transform:none!important;opacity:1!important}}"
    )
    return base + frames + "".join(classes)


def render_svg(calendar: Calendar, theme: str, pieces: list[Piece] | None = None) -> str:
    palette = PALETTES[theme]
    levels = palette["levels"]
    text = palette["text"]
    grid = calendar.levels
    ncols = len(calendar.weeks)
    if pieces is None:
        pieces = extract_pieces(grid)
        groups = schedule(pieces, ncols)
        assign_drops(groups)
    timeline = build_timeline(pieces)
    width = LEFT + (max(ncols - 1, 0) * STEP + CELL) + RIGHT
    height = GRID_TOP + 6 * STEP + CELL + BOTTOM
    total = calendar.total
    title = f"{calendar.username}'s contributions as Tetris bricks"
    desc = (
        f"{total} contributions in the last year. "
        "Bricks fall week by week and lock into the GitHub contribution graph."
    )

    empty: list[str] = []
    for col, week in enumerate(calendar.weeks):
        for row, day in enumerate(week):
            if day is None:
                continue
            if day.level != 0:
                continue
            x = LEFT + col * STEP
            y = GRID_TOP + row * STEP
            empty.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="{RX}" '
                f'fill="{levels[0]}"/>'
            )

    bricks: list[str] = []
    for index, piece in enumerate(pieces):
        cells = []
        for row, col, level in piece.cells:
            x = LEFT + col * STEP
            y = GRID_TOP + row * STEP
            cells.append(f'<g transform="translate({x},{y})">{_brick(level, levels)}</g>')
        label = f"{piece.name} {' '.join(f'{r},{c}' for r, c, _ in piece.cells)}"
        bricks.append(f'<g class="p p{index}" data-piece="{_xml(label)}">{"".join(cells)}</g>')

    months = []
    for col, name in _month_labels(calendar):
        x = LEFT + col * STEP
        months.append(
            f'<text class="lbl mo" x="{x}" y="{MONTH_BASELINE}" fill="{text}">{name}</text>'
        )
    days = []
    for row, name in DAY_LABELS.items():
        y = GRID_TOP + row * STEP + CELL - 2
        days.append(
            f'<text class="lbl dy" x="{LEFT - 8}" y="{y}" text-anchor="end" fill="{text}">{name}</text>'
        )

    legend = _legend(width, levels, text)
    caption = (
        f'<text class="lbl" x="{LEFT}" y="{CAPTION_BASELINE}" fill="{text}">'
        f"{total:,} contributions in the last year</text>"
    )
    seconds = f"{timeline.duration:.1f}"
    header = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">\n'
        f"<title id=\"title\">{_xml(title)}</title>\n"
        f"<desc id=\"desc\">{_xml(desc)} Loop {seconds}s.</desc>\n"
        f"<defs>{_gradients(levels)}</defs>\n"
        f"<style>{_styles(pieces, timeline)}</style>\n"
    )
    body = (
        caption
        + legend
        + "".join(months)
        + "".join(days)
        + "".join(empty)
        + "".join(bricks)
    )
    return header + body + "\n</svg>\n"


def _legend(width: int, levels: tuple[str, ...], text: str) -> str:
    box = 11
    gap = 3
    more_x = width - RIGHT
    squares = []
    cursor = more_x - 36
    for level in range(4, -1, -1):
        cursor -= box
        squares.append(
            f'<rect x="{cursor}" y="6" width="{box}" height="{box}" rx="2" fill="{levels[level]}"/>'
        )
        cursor -= gap
    less_x = cursor - 4
    return (
        f'<text class="lbl" x="{more_x}" y="{CAPTION_BASELINE}" text-anchor="end" fill="{text}">More</text>'
        + "".join(squares)
        + f'<text class="lbl" x="{less_x}" y="{CAPTION_BASELINE}" text-anchor="end" fill="{text}">Less</text>'
    )


def prepare(calendar: Calendar) -> list[Piece]:
    pieces = extract_pieces(calendar.levels)
    groups = schedule(pieces, len(calendar.weeks))
    assign_drops(groups)
    return pieces


# --- data sources -----------------------------------------------------------


def _request(url: str, *, token: str | None = None, data: bytes | None = None, accept: str = "application/json") -> bytes:
    headers = {
        "User-Agent": "tetris-contrib",
        "Accept": accept,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"GitHub API {exc.code} for {url.split('?')[0]}: {detail}") from exc


def fetch_graphql(username: str, token: str) -> Calendar:
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                weekday
                contributionCount
                contributionLevel
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"login": username}}).encode()
    raw = _request("https://api.github.com/graphql", token=token, data=payload)
    body = json.loads(raw.decode())
    if body.get("errors"):
        message = "; ".join(err.get("message", "unknown error") for err in body["errors"])
        raise RuntimeError(f"GitHub GraphQL error: {message}")
    user = (body.get("data") or {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user {username!r} was not found")
    weeks_json = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    weeks: list[list[Day | None]] = []
    for week in weeks_json:
        days: list[Day | None] = [None] * 7
        for item in week["contributionDays"]:
            weekday = int(item["weekday"])
            parsed = date.fromisoformat(item["date"])
            level = LEVEL_NAMES.get(item["contributionLevel"])
            if level is None:
                raise RuntimeError(f"Unknown contribution level {item['contributionLevel']!r}")
            if not 0 <= weekday <= 6:
                raise RuntimeError(f"Unexpected weekday {weekday}")
            days[weekday] = Day(date=parsed, level=level, count=int(item["contributionCount"]))
        weeks.append(days)
    if not weeks:
        raise RuntimeError("Contribution calendar was empty")
    return Calendar(username=username, weeks=weeks)


def parse_public_html(username: str, html: str) -> Calendar:
    blocks = re.findall(
        r'<td\b(?=[^>]*\bdata-date="(\d{4}-\d{2}-\d{2})")(?=[^>]*\bdata-level="(\d)")[^>]*>'
        r".*?</td>\s*<tool-tip\b[^>]*>(.*?)</tool-tip>",
        html,
        flags=re.S,
    )
    if len(blocks) < 7:
        raise RuntimeError(
            f"Could not read the public contribution calendar for {username} "
            f"({len(blocks)} days found)"
        )
    parsed: list[tuple[date, int, int]] = []
    for date_text, level_text, tip in blocks:
        tip_text = re.sub(r"<[^>]+>", "", tip)
        tip_text = re.sub(r"\s+", " ", tip_text).strip()
        if tip_text.lower().startswith("no contribution"):
            count = 0
        else:
            match = re.search(r"(\d+)\s+contribution", tip_text, flags=re.I)
            if not match:
                raise RuntimeError(f"Could not read contribution count from {tip_text!r}")
            count = int(match.group(1))
        parsed.append((date.fromisoformat(date_text), int(level_text), count))
    parsed.sort()
    weeks: list[list[Day | None]] = []
    current: list[Day | None] = [None] * 7
    started = False
    for day_date, level, count in parsed:
        weekday = (day_date.weekday() + 1) % 7  # Sunday = 0, matching GitHub
        if weekday == 0 and started:
            weeks.append(current)
            current = [None] * 7
        current[weekday] = Day(date=day_date, level=level, count=count)
        started = True
    if started:
        weeks.append(current)
    return Calendar(username=username, weeks=weeks)


def fetch_public(username: str) -> Calendar:
    url = f"https://github.com/users/{username}/contributions"
    html = _request(url, accept="text/html").decode("utf-8", "replace")
    return parse_public_html(username, html)


def load_calendar(username: str, token: str | None, public: bool) -> Calendar:
    if public or not token:
        if not public and not token:
            print("No GITHUB_TOKEN set; reading the public contribution calendar.", file=sys.stderr)
        return fetch_public(username)
    return fetch_graphql(username, token)


def write_outputs(calendar: Calendar, out_dir: str, basename: str) -> list[tuple[str, int]]:
    os.makedirs(out_dir, exist_ok=True)
    pieces = prepare(calendar)
    written: list[tuple[str, int]] = []
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        svg = render_svg(calendar, theme, pieces)
        ET.fromstring(svg)
        path = os.path.join(out_dir, f"{basename}{suffix}.svg")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(svg)
        written.append((path, len(svg.encode())))
    _sanity(calendar, pieces)
    return written


def _sanity(calendar: Calendar, pieces: list[Piece]) -> None:
    filled = [
        (r, c)
        for r, row in enumerate(calendar.levels)
        for c, level in enumerate(row)
        if level > 0
    ]
    covered = [(r, c) for piece in pieces for r, c, _ in piece.cells]
    if sorted(covered) != sorted(filled):
        raise RuntimeError("Piece packing does not match the contribution grid")
    timeline = build_timeline(pieces)
    if timeline.duration <= 0:
        raise RuntimeError("Animation duration must be positive")
    for piece in pieces:
        if piece.start + FALL + 0.3 >= timeline.hold_end:
            raise RuntimeError("A piece lands after the hold begins")
        if piece.drop >= 0:
            raise RuntimeError("Pieces must fall downward from above the board")


# --- self test --------------------------------------------------------------


def _synthetic() -> Calendar:
    # A tiny year: an O, a vertical I, a hole, and a lone brick.
    start = date(2025, 10, 12)  # Sunday
    levels = [
        [0, 0, 1, 1, 0, 0, 4],
        [0, 2, 1, 1, 0, 0, 3],
        [0, 2, 0, 0, 0, 0, 2],
        [0, 2, 0, 1, 1, 0, 1],
        [0, 2, 0, 0, 1, 0, 0],
        [1, 1, 0, 0, 1, 1, 0],
        [1, 1, 0, 0, 0, 0, 0],
    ]
    weeks: list[list[Day | None]] = []
    for col in range(7):
        days: list[Day | None] = []
        for row in range(7):
            days.append(Day(date=start + timedelta(days=col * 7 + row), level=levels[row][col], count=levels[row][col]))
        weeks.append(days)
    return Calendar(username="octocat", weeks=weeks)


def _assert_schedule(levels: list[list[int]], pieces: list[Piece]) -> None:
    by_col: dict[int, list[Piece]] = defaultdict(list)
    for piece in pieces:
        for col in {c for _, c, _ in piece.cells}:
            by_col[col].append(piece)
    for col, group in by_col.items():
        group.sort(key=lambda piece: piece.start)
        for earlier, later in zip(group, group[1:]):
            earlier_rows = [r for r, c, _ in earlier.cells if c == col]
            later_rows = [r for r, c, _ in later.cells if c == col]
            same_formation = abs(earlier.start - later.start) <= 1e-9 and abs(earlier.drop - later.drop) <= 1e-9
            if same_formation:
                continue
            lower_rows, upper_rows = earlier_rows, later_rows
            lower, upper = earlier, later
            if max(later_rows) > max(earlier_rows):
                lower_rows, upper_rows = later_rows, earlier_rows
                lower, upper = later, earlier
            if max(lower_rows) > max(upper_rows) and upper.start + 1e-9 < lower.start + FALL:
                raise AssertionError(
                    f"column {col}: upper piece starts at {upper.start:.3f} "
                    f"before lower piece lands at {lower.start + FALL:.3f}"
                )
    covered = [(r, c) for piece in pieces for r, c, _ in piece.cells]
    filled = [(r, c) for r, row in enumerate(levels) for c, level in enumerate(row) if level]
    if sorted(covered) != sorted(filled) or len(covered) != len(set(covered)):
        raise AssertionError("packing is not an exact cover of filled cells")


def self_test() -> None:
    catalog_keys = [shape for _, shape in SHAPES]
    if len(catalog_keys) != len(set(catalog_keys)):
        raise AssertionError("shape catalog has duplicate rotations")
    names = {name for name, _ in SHAPES}
    for required in ("O", "I", "T", "L", "J", "S", "Z", "O1"):
        if required not in names:
            raise AssertionError(f"missing shape {required}")

    calendar = _synthetic()
    pieces = prepare(calendar)
    _assert_schedule(calendar.levels, pieces)
    if not any(piece.name == "O" for piece in pieces):
        raise AssertionError("expected a square tetromino in the synthetic grid")

    for theme in ("light", "dark"):
        svg = render_svg(calendar, theme, pieces)
        root = ET.fromstring(svg)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise AssertionError("root is not an SVG element")
        style_node = root.find("{http://www.w3.org/2000/svg}style")
        if style_node is None or not style_node.text or "@keyframes" not in style_node.text:
            raise AssertionError("missing CSS keyframes")
        if "prefers-reduced-motion" not in style_node.text:
            raise AssertionError("missing reduced-motion fallback")
        if svg.encode().__len__() > 1_500_000:
            raise AssertionError("synthetic svg is unexpectedly large")
        # 0% and 100% of every keyframe should both be hidden so the loop is seamless.
        for body in re.findall(r"@keyframes k\d+\{((?:\d+\.\d+%\{[^}]*\})+)\}", style_node.text):
            stops = re.findall(r"([\d.]+)%\{([^}]*)\}", body)
            if not stops:
                raise AssertionError("empty keyframes")
            pcts = [float(p) for p, _ in stops]
            if pcts != sorted(pcts) or len(pcts) != len(set(pcts)):
                raise AssertionError(f"keyframe percentages not strictly increasing: {pcts}")
            if abs(pcts[0]) > 1e-6 or abs(pcts[-1] - 100) > 1e-6:
                raise AssertionError("keyframes must start at 0% and end at 100%")
            if "opacity:0" not in stops[0][1] or "opacity:0" not in stops[-1][1]:
                raise AssertionError("loop endpoints must be invisible so the restart does not flash")

    light = render_svg(calendar, "light", pieces)
    dark = render_svg(calendar, "dark", pieces)
    if light == dark or "#9be9a8" not in light or "#00c647" not in dark:
        raise AssertionError("light and dark palettes did not apply")

    sample_html = """
    <td data-date="2026-10-11" data-level="1"></td>
    <tool-tip>1 contribution on Sunday, October 11th.</tool-tip>
    <td data-date="2026-10-12" data-level="0"></td>
    <tool-tip>No contributions on Monday, October 12th.</tool-tip>
    <td data-date="2026-10-13" data-level="2"></td>
    <tool-tip>4 contributions on Tuesday, October 13th.</tool-tip>
    <td data-date="2026-10-14" data-level="0"></td>
    <tool-tip>No contributions on Wednesday, October 14th.</tool-tip>
    <td data-date="2026-10-15" data-level="4"></td>
    <tool-tip>13 contributions on Thursday, October 15th.</tool-tip>
    <td data-date="2026-10-16" data-level="0"></td>
    <tool-tip>No contributions on Friday, October 16th.</tool-tip>
    <td data-date="2026-10-17" data-level="0"></td>
    <tool-tip>No contributions on Saturday, October 17th.</tool-tip>
    """
    parsed = parse_public_html("tester", sample_html)
    assert parsed.total == 18
    assert parsed.levels[0][0] == 1  # Sunday
    assert parsed.levels[4][0] == 4  # Thursday
    empty = Calendar(username="empty", weeks=[[Day(date=date(2026, 1, 4), level=0, count=0)] + [None] * 6])
    empty_svg = render_svg(empty, "light", prepare(empty))
    ET.fromstring(empty_svg)
    if "0 contributions" not in empty_svg:
        raise AssertionError("empty calendar should still render a caption")
    print(f"self-test ok ({len(pieces)} synthetic pieces, {len(SHAPES)} shape variants)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Tetris-style GitHub contribution SVG")
    parser.add_argument("--username", default=os.environ.get("GITHUB_USER", "KenYanL"))
    parser.add_argument("--out", default="dist")
    parser.add_argument("--basename", default="tetris-contrib")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    parser.add_argument(
        "--public",
        action="store_true",
        help="Read the public contributions page instead of the GraphQL API",
    )
    parser.add_argument("--self-test", action="store_true", help="Run offline checks and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    calendar = load_calendar(args.username, args.token, args.public)
    written = write_outputs(calendar, args.out, args.basename)
    pieces = prepare(calendar)
    print(
        f"{calendar.username}: {calendar.total} contributions, "
        f"{sum(level > 0 for row in calendar.levels for level in row)} active days, "
        f"{len(pieces)} pieces, {len(calendar.weeks)} weeks"
    )
    for path, size in written:
        print(f"wrote {path} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
