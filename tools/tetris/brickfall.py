"""Build a looping Tetris/LEGO SVG from a GitHub contribution calendar.

The calendar is the same 7-by-weeks grid shown on a GitHub profile. Every cell
is packed into a standard tetromino (I, O, T, S, Z, L, J), using rotations.
Cells that cannot join one become trominoes, dominoes, or single bricks. Each
piece falls from above the grid and locks on the day it belongs to, so the
last frame is the real contribution graph.

This module is original. It does not wrap or copy the snake action or the
other contribution-graph animations listed in README.md.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator, Sequence


# Live github.com Primer tokens (contribution.default), captured 2026-10-17.
PALETTES: dict[str, dict[str, object]] = {
    "light": {
        "levels": ("#eff2f5", "#aceebb", "#4ac26b", "#2da44e", "#116329"),
        "text": "#59636e",
        "border": "rgba(31,35,40,0.06)",
    },
    "dark": {
        "levels": ("#151b23", "#033a16", "#196c2e", "#2ea043", "#56d364"),
        "text": "#9198a1",
        "border": "rgba(240,246,252,0.05)",
    },
}

LEVEL_NAMES = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}

CELL = 11
GAP = 3
STEP = CELL + GAP
ROWS = 7

# Canonical orientations before rotation. (row, col), row grows downward.
TETROMINOES: dict[str, tuple[tuple[int, int], ...]] = {
    "I": ((0, 0), (0, 1), (0, 2), (0, 3)),
    "O": ((0, 0), (0, 1), (1, 0), (1, 1)),
    "T": ((0, 0), (0, 1), (0, 2), (1, 1)),
    "S": ((0, 1), (0, 2), (1, 0), (1, 1)),
    "Z": ((0, 0), (0, 1), (1, 1), (1, 2)),
    "J": ((0, 0), (1, 0), (2, 0), (2, 1)),
    "L": ((0, 1), (1, 1), (2, 0), (2, 1)),
}
SMALL_PIECES: dict[str, tuple[tuple[int, int], ...]] = {
    "I3": ((0, 0), (0, 1), (0, 2)),
    "L3": ((0, 0), (1, 0), (1, 1)),
    "I2": ((0, 0), (0, 1)),
    "I1": ((0, 0),),
}

WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
LABELED_ROWS = {1: "Mon", 3: "Wed", 5: "Fri"}

QUERY = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from,to:$to){
      contributionCalendar{
        totalContributions
        weeks{
          contributionDays{ date contributionCount contributionLevel }
        }
      }
    }
  }
}
"""

QUERY_DEFAULT = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        totalContributions
        weeks{
          contributionDays{ date contributionCount contributionLevel }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Day:
    date: date
    count: int
    level: int


@dataclass(frozen=True)
class Calendar:
    username: str
    start: date
    end: date
    weeks: tuple[tuple[Day, ...], ...]

    @property
    def columns(self) -> int:
        return len(self.weeks)

    @property
    def total(self) -> int:
        return sum(day.count for week in self.weeks for day in week)

    def level_at(self, row: int, col: int) -> int:
        return self.weeks[col][row].level


@dataclass(frozen=True)
class Piece:
    name: str
    cells: tuple[tuple[int, int], ...]

    @property
    def size(self) -> int:
        return len(self.cells)


@dataclass(frozen=True)
class Timeline:
    delays: tuple[float, ...]
    total: float
    fall: float
    bounce: float
    fade_at: float
    fade: float
    drop: float


def years_ago(day: date, years: int = 1) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def profile_window(today: date) -> tuple[date, date]:
    """Sunday-start weeks covering the profile graph for ``today``.

    GitHub draws the week that contains the calendar date one year ago,
    through the Saturday of the current week.
    """
    ago = years_ago(today)
    start = ago - timedelta(days=(ago.weekday() + 1) % 7)
    end = today + timedelta(days=(5 - today.weekday()) % 7)
    return start, end


def _parse_day(raw: dict) -> Day:
    level = raw.get("contributionLevel", "NONE")
    if isinstance(level, int):
        level_n = level
    else:
        level_n = LEVEL_NAMES[str(level)]
    return Day(
        date=date.fromisoformat(str(raw["date"])[:10]),
        count=int(raw.get("contributionCount", raw.get("count", 0))),
        level=int(level_n),
    )


def calendar_from_days(username: str, start: date, end: date, days: Iterable[Day]) -> Calendar:
    span = (end - start).days + 1
    if span % 7 != 0 or span < 7:
        raise ValueError(f"window {start}..{end} is not a whole number of weeks")
    columns = span // 7
    grid: list[list[Day | None]] = [[None] * ROWS for _ in range(columns)]
    for day in days:
        if day.date < start or day.date > end:
            continue
        index = (day.date - start).days
        col, row = divmod(index, 7)
        grid[col][row] = day
    weeks: list[tuple[Day, ...]] = []
    for col in range(columns):
        column: list[Day] = []
        for row in range(ROWS):
            existing = grid[col][row]
            if existing is None:
                existing = Day(start + timedelta(days=col * 7 + row), 0, 0)
            column.append(existing)
        weeks.append(tuple(column))
    return Calendar(username=username, start=start, end=end, weeks=tuple(weeks))


def calendar_from_json(payload: dict) -> Calendar:
    start = date.fromisoformat(payload["start"])
    end = date.fromisoformat(payload["end"])
    days: list[Day] = []
    for week in payload["weeks"]:
        for raw in week:
            days.append(_parse_day(raw))
    return calendar_from_days(str(payload.get("username", "")), start, end, days)


def calendar_to_json(cal: Calendar) -> dict:
    return {
        "username": cal.username,
        "total": cal.total,
        "start": cal.start.isoformat(),
        "end": cal.end.isoformat(),
        "weeks": [
            [
                {"date": day.date.isoformat(), "count": day.count, "level": day.level}
                for day in week
            ]
            for week in cal.weeks
        ],
    }


class GraphQLError(RuntimeError):
    pass


def _graphql(token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "tetris-contrib",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise GraphQLError(f"GitHub GraphQL HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GraphQLError(f"GitHub GraphQL request failed: {exc.reason}") from exc
    if payload.get("errors"):
        message = "; ".join(str(item.get("message", item)) for item in payload["errors"])
        raise GraphQLError(message)
    return payload["data"]


def fetch_calendar(username: str, token: str, today: date | None = None) -> Calendar:
    """Fetch the profile contribution window for ``username``."""
    today = today or datetime.now(timezone.utc).date()
    start, end = profile_window(today)
    variables = {
        "login": username,
        "from": f"{start.isoformat()}T00:00:00Z",
        "to": f"{end.isoformat()}T23:59:59Z",
    }
    try:
        data = _graphql(token, QUERY, variables)
    except GraphQLError as exc:
        if "year" not in str(exc).lower():
            raise
        # The profile window can run a few days past one year. The default
        # collection is the same graph the profile draws.
        data = _graphql(token, QUERY_DEFAULT, {"login": username})
    user = data.get("user")
    if not user:
        raise GraphQLError(f"GitHub user {username!r} was not found")
    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [_parse_day(raw) for week in weeks for raw in week["contributionDays"]]
    if not days:
        raise GraphQLError(f"no contribution days returned for {username}")
    # Trust the dates GitHub returned when they already form the profile grid.
    got_start = min(day.date for day in days)
    got_end = max(day.date for day in days)
    if (got_end - got_start).days % 7 == 6:
        start, end = got_start, got_end
    return calendar_from_days(username, start, end, days)


def normalize(cells: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    items = list(cells)
    min_row = min(row for row, _col in items)
    min_col = min(col for _row, col in items)
    return tuple(sorted((row - min_row, col - min_col) for row, col in items))


def rotations(shape: Sequence[tuple[int, int]]) -> tuple[tuple[tuple[int, int], ...], ...]:
    found: list[tuple[tuple[int, int], ...]] = []
    current = tuple(shape)
    for _ in range(4):
        norm = normalize(current)
        if norm not in found:
            found.append(norm)
        current = tuple((col, -row) for row, col in current)
    return tuple(found)


def _orientations() -> list[tuple[str, tuple[tuple[int, int], ...]]]:
    ordered: list[tuple[str, tuple[tuple[int, int], ...]]] = []
    for table in (TETROMINOES, SMALL_PIECES):
        for name, shape in table.items():
            for orientation in rotations(shape):
                ordered.append((name, orientation))
    return ordered


ORIENTATIONS = _orientations()
SHAPE_NAME = {orientation: name for name, orientation in ORIENTATIONS}


def match_shape(cells: Iterable[tuple[int, int]]) -> str | None:
    return SHAPE_NAME.get(normalize(cells))


def _badness(cells: set[tuple[int, int]]) -> tuple[int, int, int]:
    """How awkward the uncovered cells are for a later tetromino tiling."""
    if not cells:
        return (0, 0, 0)
    remaining = set(cells)
    odd = isolates = tiny = 0
    while remaining:
        start = remaining.pop()
        stack = [start]
        size = 1
        while stack:
            row, col = stack.pop()
            for neighbor in ((row + 1, col), (row - 1, col), (row, col + 1), (row, col - 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
                    size += 1
        if size % 4:
            odd += 1
        if size == 1:
            isolates += 1
        if size < 4:
            tiny += 1
    return (odd, isolates, tiny)


def _placements(anchor: tuple[int, int], free: set[tuple[int, int]]) -> list[Piece]:
    row, col = anchor
    found: dict[tuple[tuple[int, int], ...], str] = {}
    for name, orientation in ORIENTATIONS:
        for origin_row, origin_col in orientation:
            delta_row = row - origin_row
            delta_col = col - origin_col
            placed = tuple(
                sorted((cell_row + delta_row, cell_col + delta_col) for cell_row, cell_col in orientation)
            )
            if all(cell in free for cell in placed):
                found.setdefault(placed, name)
    return [Piece(name, cells) for cells, name in found.items()]


def _variety(piece: Piece, anchor: tuple[int, int]) -> int:
    """Spread shapes and rotations across the board without using randomness."""
    mix = 0
    for row, col in piece.cells:
        mix = (mix * 5 + row * 2 + col) % 97
    order = ("O", "T", "L", "J", "S", "Z", "I", "L3", "I3", "I2", "I1")
    base = order.index(piece.name) if piece.name in order else len(order)
    return (base * 3 + mix + anchor[0] * 7 + anchor[1] * 11) % 19


def _greedy(rows: int, cols: int) -> list[Piece]:
    """Largest-piece frontier pack, used for small or odd regions."""
    free = {(row, col) for row in range(rows) for col in range(cols)}
    pieces: list[Piece] = []
    while free:
        anchor = min(free)
        candidates = _placements(anchor, free)
        if not candidates:
            raise RuntimeError(f"no piece fits at {anchor}")

        def rank(piece: Piece) -> tuple:
            rest = free.difference(piece.cells)
            return (-piece.size, _badness(rest), _variety(piece, anchor), piece.cells)

        chosen = min(candidates, key=rank)
        pieces.append(chosen)
        free.difference_update(chosen.cells)
    return _weld(pieces)


_TETROMINO_ORIENTATIONS = tuple(
    (name, orientation) for name, orientation in ORIENTATIONS if len(orientation) == 4
)


def _exact_tetrominoes(rows: int, cols: int, bias: int) -> list[Piece]:
    """Tile a small rectangle with tetrominoes only. Backtracking stays shallow."""
    free = {(row, col) for row in range(rows) for col in range(cols)}

    def candidates(anchor: tuple[int, int], open_cells: set[tuple[int, int]]) -> list[Piece]:
        found: dict[tuple[tuple[int, int], ...], Piece] = {}
        row, col = anchor
        for name, orientation in _TETROMINO_ORIENTATIONS:
            for origin_row, origin_col in orientation:
                delta_row = row - origin_row
                delta_col = col - origin_col
                placed = tuple(
                    sorted(
                        (cell_row + delta_row, cell_col + delta_col) for cell_row, cell_col in orientation
                    )
                )
                if all(cell in open_cells for cell in placed):
                    found.setdefault(placed, Piece(name, placed))
        ordered = list(found.values())
        ordered.sort(key=lambda piece: ((_variety(piece, anchor) + bias) % 19, piece.cells))
        return ordered

    def search(open_cells: set[tuple[int, int]]) -> list[Piece] | None:
        if not open_cells:
            return []
        anchor = min(open_cells)
        for piece in candidates(anchor, open_cells):
            found = search(open_cells.difference(piece.cells))
            if found is not None:
                return [piece, *found]
        return None

    found = search(free)
    if found is None:
        raise RuntimeError(f"no tetromino tiling for {rows}x{cols}")
    return found


def _shift(pieces: Sequence[Piece], row_offset: int, col_offset: int) -> list[Piece]:
    moved: list[Piece] = []
    for piece in pieces:
        cells = tuple(sorted((row + row_offset, col + col_offset) for row, col in piece.cells))
        moved.append(Piece(piece.name, cells))
    return moved


def _tile_band(row_offset: int, band_rows: int, cols: int, bias_salt: int) -> list[Piece]:
    """Tile a horizontal band. Widths of 4 are solved exactly; the tail may be smaller."""
    pieces: list[Piece] = []
    block = 0
    col = 0
    while col + 4 <= cols:
        local = _exact_tetrominoes(band_rows, 4, bias=bias_salt + block * 5)
        pieces.extend(_shift(local, row_offset, col))
        col += 4
        block += 1
    if col < cols:
        width = cols - col
        if band_rows * width % 4 == 0:
            local = _exact_tetrominoes(band_rows, width, bias=bias_salt + 3)
            pieces.extend(_shift(local, row_offset, col))
        else:
            pieces.extend(_shift(_greedy(band_rows, width), row_offset, col))
    return pieces


def decompose(rows: int, cols: int) -> list[Piece]:
    """Pack a solid grid into catalog pieces.

    A profile calendar is 7 rows. The top 4 and bottom 3 are tiled separately,
    because each 4-column block of those bands divides evenly into tetrominoes.
    The only leftovers are the few cells required by ``7 * columns`` modulo 4.
    Other sizes use a largest-first frontier pack.
    """
    if rows == 7 and cols >= 1:
        pieces = _tile_band(0, 4, cols, bias_salt=1)
        pieces.extend(_tile_band(4, 3, cols, bias_salt=8))
        return _weld(pieces)
    return _greedy(rows, cols)


def _weld(pieces: list[Piece]) -> list[Piece]:
    """Join adjacent sub-tetromino leftovers when they form a real piece."""
    current = list(pieces)
    changed = True
    while changed:
        changed = False
        small = [index for index, piece in enumerate(current) if piece.size < 4]
        for left in range(len(small)):
            for right in range(left + 1, len(small)):
                first = current[small[left]]
                second = current[small[right]]
                union = tuple(sorted(first.cells + second.cells))
                if len(union) > 4:
                    continue
                name = match_shape(union)
                if name is None or len(union) <= max(first.size, second.size):
                    continue
                current[small[left]] = Piece(name, union)
                current.pop(small[right])
                changed = True
                break
            if changed:
                break
    return current


def lands_before(earlier: Piece, later: Piece) -> bool:
    """True when ``earlier`` has a cell under ``later`` in the same column.

    A brick falls straight down, so anything already locked above its path
    would be flown through. Bottom cells in a shared column land first.
    """
    lowest: dict[int, int] = {}
    for row, col in earlier.cells:
        lowest[col] = max(row, lowest.get(col, -1))
    return any(col in lowest and lowest[col] > row for row, col in later.cells)


def stack_order(pieces: Sequence[Piece]) -> list[Piece]:
    """Land supporting bricks first, sweeping the year left to right."""
    count = len(pieces)
    incoming = [0] * count
    followers: list[list[int]] = [[] for _ in range(count)]
    for earlier in range(count):
        for later in range(count):
            if earlier != later and lands_before(pieces[earlier], pieces[later]):
                incoming[later] += 1
                followers[earlier].append(later)

    def sort_key(index: int) -> tuple:
        rows = [row for row, _col in pieces[index].cells]
        cols = [col for _row, col in pieces[index].cells]
        return (min(cols), -max(rows), min(rows), pieces[index].name, pieces[index].cells)

    ready = sorted((index for index in range(count) if incoming[index] == 0), key=sort_key)
    ordered: list[int] = []
    while ready:
        index = ready.pop(0)
        ordered.append(index)
        unlocked: list[int] = []
        for follower in followers[index]:
            incoming[follower] -= 1
            if incoming[follower] == 0:
                unlocked.append(follower)
        if unlocked:
            ready.extend(unlocked)
            ready.sort(key=sort_key)
    if len(ordered) != count:
        return sorted(pieces, key=sort_key_piece)
    return [pieces[index] for index in ordered]


def sort_key_piece(piece: Piece) -> tuple:
    rows = [row for row, _col in piece.cells]
    cols = [col for _row, col in piece.cells]
    return (min(cols), -max(rows), min(rows), piece.name, piece.cells)


def make_timeline(count: int, drop: float) -> Timeline:
    """One shared clock. Piece ``i`` is delayed so the board stacks in order.

    The finished graph stays up for a few seconds. Pieces then leave in the
    same order and the 0% frame matches the 100% frame, so the loop is seamless.
    """
    fall = 0.58
    bounce = 0.16
    fade = 0.42
    hold = 3.5
    tail = 0.35
    if count <= 1:
        stagger = 0.0
    else:
        stagger = min(0.052, 5.6 / (count - 1))
    delays = tuple(index * stagger for index in range(count))
    last_delay = delays[-1] if delays else 0.0
    last_land = last_delay + (fall if count else 0.0)
    fade_at = last_land + hold
    total = last_delay + fade_at + fade + tail
    return Timeline(
        delays=delays,
        total=total,
        fall=fall,
        bounce=bounce,
        fade_at=fade_at,
        fade=fade,
        drop=drop,
    )


def _mix(color: str, other: tuple[int, int, int], amount: float) -> str:
    value = color.lstrip("#")
    base = tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    mixed = tuple(round(base[index] * (1 - amount) + other[index] * amount) for index in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _fmt(number: float) -> str:
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _percent(moment: float, total: float) -> str:
    return _fmt(100.0 * moment / total)


def month_labels(cal: Calendar) -> list[tuple[int, str]]:
    labels: list[tuple[int, str]] = []
    last_col = -3
    for col, week in enumerate(cal.weeks):
        first_of_month = next((day for day in week if day.date.day == 1), None)
        if col == 0 and first_of_month is None:
            labels.append((0, week[0].date.strftime("%b")))
            last_col = 0
            continue
        if first_of_month is not None and col - last_col >= 2:
            labels.append((col, first_of_month.date.strftime("%b")))
            last_col = col
    return labels


def _brick(level: int, palette: dict[str, object]) -> str:
    colors: tuple[str, ...] = palette["levels"]  # type: ignore[assignment]
    base = colors[level]
    border = str(palette["border"])
    if level == 0:
        return (
            f'<rect width="{CELL}" height="{CELL}" rx="2" fill="{base}" '
            f'stroke="{border}" stroke-width="1"/>'
        )
    highlight = _mix(base, (255, 255, 255), 0.42)
    shade = _mix(base, (0, 0, 0), 0.38)
    stud = _mix(base, (255, 255, 255), 0.20)
    stud_top = _mix(base, (255, 255, 255), 0.55)
    return (
        f'<rect width="{CELL}" height="{CELL}" rx="2" fill="{base}"/>'
        f'<rect x="1" y="0.8" width="9" height="1.15" rx="0.5" fill="{highlight}" opacity="0.55"/>'
        f'<rect x="1" y="9" width="9" height="1.05" rx="0.45" fill="{shade}" opacity="0.4"/>'
        f'<rect x="9.35" y="1.5" width="0.9" height="7.6" rx="0.4" fill="{shade}" opacity="0.28"/>'
        f'<circle cx="5.5" cy="5.65" r="1.9" fill="{shade}"/>'
        f'<circle cx="5.5" cy="5.35" r="1.55" fill="{stud}"/>'
        f'<circle cx="4.9" cy="4.75" r="0.48" fill="{stud_top}"/>'
    )


def _joints(piece: Piece, levels: list[list[int]], origin_x: float, origin_y: float, palette: dict) -> str:
    colors: tuple[str, ...] = palette["levels"]  # type: ignore[assignment]
    occupied = set(piece.cells)
    parts: list[str] = []
    for row, col in piece.cells:
        for neighbor, horizontal in (
            ((row, col + 1), True),
            ((row + 1, col), False),
        ):
            if neighbor not in occupied:
                continue
            if levels[row][col] != levels[neighbor[0]][neighbor[1]]:
                continue
            color = colors[levels[row][col]]
            if horizontal:
                x = origin_x + col * STEP + CELL - 0.45
                y = origin_y + row * STEP + 1.7
                width, height = GAP + 0.9, CELL - 3.4
            else:
                x = origin_x + col * STEP + 1.7
                y = origin_y + row * STEP + CELL - 0.45
                width, height = CELL - 3.4, GAP + 0.9
            parts.append(
                f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(width)}" height="{_fmt(height)}" '
                f'rx="0.6" fill="{color}"/>'
            )
    return "".join(parts)


def _fall_css(timeline: Timeline) -> str:
    drop = timeline.drop
    fall = timeline.fall
    bounce = timeline.bounce
    fade_at = timeline.fade_at
    fade_end = fade_at + timeline.fade
    total = timeline.total
    # Ease-out is baked into keyframe spacing so the whole loop can stay linear.
    stops = [
        (0.0, -drop, 0),
        (0.05, -drop, 1),
        (fall * 0.32, -drop * 0.20, 1),
        (fall * 0.68, -drop * 0.035, 1),
        (fall, 0.0, 1),
        (fall + bounce * 0.42, -3.4, 1),
        (fall + bounce, 0.0, 1),
        (fade_at, 0.0, 1),
        (fade_end, 0.0, 0),
        (total, -drop, 0),
    ]
    body = []
    for moment, y_pos, opacity in stops:
        body.append(
            f"{_percent(moment, total)}%{{transform:translateY({_fmt(y_pos)}px);opacity:{opacity}}}"
        )
    lock_end = fall + bounce + 0.18
    lock = (
        f"0%{{opacity:1}}"
        f"{_percent(fall + bounce, total)}%{{opacity:1}}"
        f"{_percent(lock_end, total)}%{{opacity:0}}"
        f"100%{{opacity:0}}"
    )
    duration = _fmt(total)
    return (
        ".piece{animation:fall "
        + duration
        + "s linear infinite;animation-fill-mode:backwards}"
        ".joints{animation:lock "
        + duration
        + "s linear infinite;animation-fill-mode:backwards}"
        "@keyframes fall{" + "".join(body) + "}"
        "@keyframes lock{" + lock + "}"
        "@media (prefers-reduced-motion:reduce){"
        ".piece,.joints{animation:none}"
        ".piece{transform:none;opacity:1}"
        ".joints{opacity:0}}"
    )


def render_svg(cal: Calendar, pieces: Sequence[Piece], theme: str) -> str:
    if theme not in PALETTES:
        raise ValueError(f"unknown theme {theme!r}")
    palette = PALETTES[theme]
    text = str(palette["text"])
    columns = cal.columns
    origin_x = 34
    origin_y = 22
    width = max(origin_x + columns * STEP + 16, origin_x + 420)
    height = origin_y + ROWS * STEP + 28
    drop = float(origin_y + (ROWS - 1) * STEP + CELL + 8)
    ordered = stack_order(pieces)
    timeline = make_timeline(len(ordered), drop)
    levels = [[cal.level_at(row, col) for col in range(columns)] for row in range(ROWS)]

    defs = []
    for level in range(5):
        defs.append(f'<g id="b{theme[0]}{level}">{_brick(level, palette)}</g>')

    piece_markup: list[str] = []
    counts: dict[str, int] = {}
    for index, piece in enumerate(ordered):
        counts[piece.name] = counts.get(piece.name, 0) + 1
        delay = _fmt(timeline.delays[index])
        joints = _joints(piece, levels, origin_x, origin_y, palette)
        cells = []
        for row, col in piece.cells:
            level = levels[row][col]
            x = origin_x + col * STEP
            y = origin_y + row * STEP
            cells.append(f'<use href="#b{theme[0]}{level}" x="{x}" y="{y}"/>')
        joint_group = ""
        if joints:
            joint_group = f'<g class="joints" opacity="0" style="animation-delay:{delay}s">{joints}</g>'
        piece_markup.append(
            f'<g class="piece" style="animation-delay:{delay}s">{joint_group}{"".join(cells)}</g>'
        )

    labels = []
    for col, name in month_labels(cal):
        x = origin_x + col * STEP
        labels.append(f'<text x="{x}" y="14">{name}</text>')
    for row, name in LABELED_ROWS.items():
        y = origin_y + row * STEP + 8
        labels.append(f'<text x="26" y="{y}" text-anchor="end" font-size="9">{name}</text>')

    sentence = f"{cal.total} contribution" + ("s" if cal.total != 1 else "") + " in the last year"
    caption_y = origin_y + ROWS * STEP + 18
    legend_x = width - 158
    legend = [f'<text x="{legend_x}" y="{caption_y}">Less</text>']
    for level in range(5):
        x = legend_x + 34 + level * STEP
        y = caption_y - 9
        legend.append(f'<use href="#b{theme[0]}{level}" x="{x}" y="{y}"/>')
    legend.append(f'<text x="{legend_x + 34 + 5 * STEP + 2}" y="{caption_y}">More</text>')

    summary = " ".join(f"{name}:{counts[name]}" for name in sorted(counts))
    title = f"{cal.username} contributions, built from falling bricks"
    desc = (
        f"{sentence}. Pieces fall into the contribution calendar "
        f"from {cal.start.isoformat()} to {cal.end.isoformat()}."
    )
    css = _fall_css(timeline)
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" overflow="hidden">\n'
        f"<title>{_xml(title)}</title>\n"
        f"<desc>{_xml(desc)}</desc>\n"
        f"<!-- tetris bricks { _xml(cal.username) } {cal.start.isoformat()} {cal.end.isoformat()} {summary} -->\n"
        f"<style>{css}</style>\n"
        f"<defs>{''.join(defs)}</defs>\n"
        f'<g fill="{text}" font-family="{font}" font-size="12">'
        f"{''.join(labels)}"
        f'<text x="{origin_x}" y="{caption_y}">{_xml(sentence)}</text>'
        f"{''.join(legend)}</g>\n"
        f"{''.join(piece_markup)}\n"
        "</svg>\n"
    )


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def pieces_for_calendar(cal: Calendar) -> list[Piece]:
    return decompose(ROWS, cal.columns)


def coverage_errors(pieces: Sequence[Piece], rows: int, cols: int) -> list[str]:
    seen: dict[tuple[int, int], int] = {}
    errors: list[str] = []
    for piece in pieces:
        if match_shape(piece.cells) != piece.name:
            errors.append(f"{piece.name} is not a catalog placement of {piece.cells}")
        for cell in piece.cells:
            if cell in seen:
                errors.append(f"overlap at {cell}")
            if not (0 <= cell[0] < rows and 0 <= cell[1] < cols):
                errors.append(f"out of range {cell}")
            seen[cell] = 1
    expected = {(row, col) for row in range(rows) for col in range(cols)}
    missing = expected.difference(seen)
    if missing:
        errors.append(f"missing {len(missing)} cells")
    return errors


def iter_piece_names() -> Iterator[str]:
    yield from list(TETROMINOES) + list(SMALL_PIECES)
