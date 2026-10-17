"""Unit tests for the brick calendar packer and SVG timeline."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brickfall import (  # noqa: E402
    ORIENTATIONS,
    SMALL_PIECES,
    TETROMINOES,
    Calendar,
    Day,
    calendar_from_json,
    coverage_errors,
    decompose,
    lands_before,
    make_timeline,
    match_shape,
    pieces_for_calendar,
    profile_window,
    render_svg,
    rotations,
    stack_order,
)


ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "sample" / "contributions.json"


def _connected(cells) -> bool:
    remaining = set(cells)
    start = remaining.pop()
    seen = {start}
    stack = [start]
    while stack:
        row, col = stack.pop()
        for neighbor in ((row + 1, col), (row - 1, col), (row, col + 1), (row, col - 1)):
            if neighbor in remaining and neighbor not in seen:
                remaining.remove(neighbor)
                seen.add(neighbor)
                stack.append(neighbor)
    return not remaining


class ShapeTests(unittest.TestCase):
    def test_catalog_rotations_are_connected_and_unique(self) -> None:
        expected = {"I": 2, "O": 1, "T": 4, "S": 2, "Z": 2, "J": 4, "L": 4, "I3": 2, "L3": 4, "I2": 2, "I1": 1}
        for name, shape in {**TETROMINOES, **SMALL_PIECES}.items():
            turns = rotations(shape)
            self.assertEqual(len(turns), expected[name], name)
            for turn in turns:
                self.assertEqual(len(turn), len(shape))
                self.assertTrue(_connected(turn), (name, turn))
                self.assertEqual(match_shape(turn), name)

    def test_orientations_cover_every_catalog_name(self) -> None:
        names = {name for name, _shape in ORIENTATIONS}
        self.assertEqual(names, set(expected_names()))


def expected_names():
    return set(TETROMINOES) | set(SMALL_PIECES)


class PackTests(unittest.TestCase):
    def test_rectangle_is_covered_by_real_pieces(self) -> None:
        pieces = decompose(7, 53)
        errors = coverage_errors(pieces, 7, 53)
        self.assertEqual(errors, [])
        leftover = sum(piece.size for piece in pieces if piece.size < 4)
        # 53*7 = 371 = 4*92 + 3, so a perfect pack leaves exactly 3 cells.
        self.assertEqual(leftover, 3)
        names = {piece.name for piece in pieces if piece.size == 4}
        self.assertGreaterEqual(len(names), 4)

    def test_pack_is_deterministic(self) -> None:
        first = decompose(7, 53)
        second = decompose(7, 53)
        self.assertEqual(first, second)

    def test_small_shapes_pack_as_themselves(self) -> None:
        self.assertEqual([piece.name for piece in decompose(1, 1)], ["I1"])
        self.assertEqual([piece.name for piece in decompose(2, 2)], ["O"])
        self.assertEqual([piece.name for piece in decompose(1, 4)], ["I"])

    def test_stack_order_lands_lower_cells_first(self) -> None:
        pieces = stack_order(decompose(7, 53))
        index = {piece: position for position, piece in enumerate(pieces)}
        for earlier in pieces:
            for later in pieces:
                if earlier is later:
                    continue
                if lands_before(earlier, later) and not lands_before(later, earlier):
                    self.assertLess(index[earlier], index[later])


class TimelineTests(unittest.TestCase):
    def test_loop_hides_every_piece_before_restart(self) -> None:
        timeline = make_timeline(90, drop=120)
        self.assertEqual(timeline.delays[0], 0)
        self.assertGreater(timeline.total, 0)
        last = timeline.delays[-1]
        last_land = last + timeline.fall
        # The full board is visible from the last landing until the first fade.
        self.assertGreaterEqual(timeline.fade_at, last_land + 3)
        last_fade_end = last + timeline.fade_at + timeline.fade
        self.assertLess(last_fade_end, timeline.total)
        self.assertGreater(timeline.drop, 0)

    def test_svg_degrades_to_the_finished_graph(self) -> None:
        calendar = _tiny_calendar()
        pieces = pieces_for_calendar(calendar)
        svg = render_svg(calendar, pieces, "dark")
        self.assertIn("@keyframes fall", svg)
        self.assertIn("prefers-reduced-motion", svg)
        self.assertIn("animation-fill-mode:backwards", svg)
        self.assertNotIn("var(", svg)
        self.assertIn(">1 contribution in the last year<", svg)
        self.assertLess(len(svg.encode()), 1_500_000)
        # 0% and 100% of the fall are the same hidden pose, so the loop does not jump.
        block = svg.split("@keyframes fall{")[1].split("@keyframes lock")[0]
        self.assertTrue(block.startswith("0%{"))
        self.assertIn("100%{transform:translateY(", block)
        head = block[: block.index("}") + 1]
        tail = block[block.rindex("100%") : block.rindex("}") + 1]
        self.assertIn("opacity:0", head)
        self.assertIn("opacity:0", tail)
        self.assertIn("translateY(", head)
        self.assertIn("translateY(", tail)
        self.assertNotIn("<script", svg)


class WindowTests(unittest.TestCase):
    def test_profile_window_is_sunday_through_saturday(self) -> None:
        start, end = profile_window(date(2026, 10, 17))
        self.assertEqual(start, date(2025, 10, 12))
        self.assertEqual(end, date(2026, 10, 17))
        self.assertEqual(start.weekday(), 6)
        self.assertEqual(end.weekday(), 5)
        self.assertEqual((end - start).days % 7, 6)

    def test_leap_day_does_not_crash(self) -> None:
        start, end = profile_window(date(2028, 2, 29))
        self.assertEqual(start.weekday(), 6)
        self.assertEqual(end.weekday(), 5)


class SampleTests(unittest.TestCase):
    def test_saved_calendar_matches_kenyanl_window(self) -> None:
        if not SAMPLE.exists():
            self.skipTest("sample calendar not generated yet")
        import json

        calendar = calendar_from_json(json.loads(SAMPLE.read_text(encoding="utf-8")))
        self.assertEqual(calendar.username, "KenYanL")
        self.assertEqual(calendar.columns, 53)
        self.assertEqual(calendar.total, 168)
        pieces = pieces_for_calendar(calendar)
        self.assertEqual(coverage_errors(pieces, 7, 53), [])
        for theme in ("light", "dark"):
            svg = render_svg(calendar, pieces, theme)
            self.assertIn("#aceebb" if theme == "light" else "#56d364", svg)
            self.assertIn("KenYanL", svg)
            self.assertLess(len(svg.encode()), 1_500_000)


def _tiny_calendar() -> Calendar:
    start = date(2026, 10, 11)
    weeks = []
    total_days = 0
    for col in range(2):
        column = []
        for row in range(7):
            count = 1 if (col, row) == (0, 0) else 0
            level = 1 if count else 0
            column.append(Day(start.fromordinal(start.toordinal() + total_days), count, level))
            total_days += 1
        weeks.append(tuple(column))
    return Calendar("tester", start, weeks[-1][-1].date, tuple(weeks))


if __name__ == "__main__":
    unittest.main()
