"""A belt route that cannot exist is proven impossible at once (companion/smelting._apart), in every shape of being
cut off, and the proof never turns down a route the search would find (OP-4, 2026-10-27: a goal inside a 5-thick
walled ring took 6 s to give up, every time a belt was planned to it)."""
from __future__ import annotations

import random
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import smelting  # noqa: E402
from companion.smelting import route, undergrounds  # noqa: E402

BIG = (-256, -256, 256, 256)


def ring(thick: int, inner: int = 8, gap: tuple[int, int] | None = None) -> set[tuple[int, int]]:
    out = inner + thick - 1
    tiles = {(x, y) for x in range(-out, out + 1) for y in range(-out, out + 1) if inner <= max(abs(x), abs(y)) <= out}
    return tiles - ({(gx, gap[1]) for gx in range(gap[0], gap[0] + 1)} if gap else set())


def quick(test, call, seconds=2.0):   # (generous: the suite runs 16 files at once)
    start = time.perf_counter()
    result = call()
    test.assertLess(time.perf_counter() - start, seconds)
    return result


class CutOffTests(unittest.TestCase):
    def test_a_goal_inside_a_ring_thicker_than_a_hop_is_given_up_at_once(self):
        self.assertIsNone(quick(self, lambda: route((-200, -200), (0, 0), ring(5), bounds=BIG)))

    def test_a_start_inside_such_a_ring_too(self):
        self.assertIsNone(quick(self, lambda: route((0, 0), (-200, -200), ring(5), bounds=BIG)))

    def test_a_ring_a_hop_crosses_still_has_its_route(self):
        found = quick(self, lambda: route((-60, -60), (0, 0), ring(4), bounds=BIG), 2)
        self.assertIsNotNone(found)
        self.assertTrue(any(b.name == "underground-belt" for b in found))
        with undergrounds(False):   # (without undergrounds researched the same ring shuts it in)
            self.assertIsNone(quick(self, lambda: route((-60, -60), (0, 0), ring(4), bounds=BIG)))

    def test_a_ring_with_a_gap_has_its_route(self):
        self.assertIsNotNone(quick(self, lambda: route((-60, -60), (0, 0), ring(5, gap=(0, -10)) - {(0, y) for y in range(-12, -7)},
                                                       bounds=BIG), 2))

    def test_a_river_across_the_window_is_given_up_at_once(self):
        river = {(x, y) for x in range(-90, 91) for y in range(-3, 3)}
        self.assertIsNone(quick(self, lambda: route((-30, -60), (20, 40), river, bounds=(-80, -80, 80, 80))))
        narrow = {(x, y) for x in range(-90, 91) for y in range(-2, 2)}   # (four wide: a hop crosses)
        self.assertIsNotNone(route((-30, -60), (20, 40), narrow, bounds=(-80, -80, 80, 80)))

    def test_a_winding_river_across_the_whole_window_is_settled_by_one_flood_of_the_window(self):
        # (each side past POCKET: the search floods the window once on the way, rather than search for 7 s)
        river = {(x, y + (x // 40) % 3) for x in range(-256, 257) for y in range(-3, 3)}
        self.assertIsNone(quick(self, lambda: route((-30, -60), (20, 40), river, bounds=BIG), 5))

    def test_a_corner_the_window_s_edge_closes_is_given_up_at_once(self):
        # the goal in the window's corner, walled off by a thick L whose other sides are the window's edge
        wall = {(x, y) for x in range(60, 81) for y in range(60, 66)} | {(x, y) for x in range(60, 66) for y in range(60, 81)}
        self.assertIsNone(quick(self, lambda: route((-40, -40), (75, 75), wall, bounds=(-80, -80, 80, 80))))

    def test_a_goal_walled_on_all_sides_but_reachable_only_by_a_hop_onto_it_is_given_up(self):
        # (a route ends on an ordinary belt: never by hopping onto the goal)
        wall = {(x, y) for x in range(-1, 2) for y in range(-1, 2)} - {(0, 0)}
        wall |= {(x, y) for x in range(-6, 7) for y in range(-6, 7) if max(abs(x), abs(y)) in (2,)}
        self.assertIsNone(quick(self, lambda: route((-50, -50), (0, 0), wall, bounds=BIG)))


class SoundnessTests(unittest.TestCase):
    def test_the_proof_never_turns_down_a_route_the_search_finds(self):
        rng = random.Random(7)
        bounds = (-14, -14, 14, 14)
        proven = 0
        for _ in range(250):
            density = rng.choice((0.25, 0.35, 0.45, 0.55))
            blocked = {(x, y) for x in range(-14, 15) for y in range(-14, 15) if rng.random() < density}
            start, goal = (rng.randint(-14, 14), rng.randint(-14, 14)), (rng.randint(-14, 14), rng.randint(-14, 14))
            blocked -= {start, goal}
            if start == goal:
                continue
            for hops in (True, False):
                with undergrounds(hops):
                    if not smelting._apart(start, goal, lambda t: (t == goal or t not in blocked)
                                           and -14 <= t[0] <= 14 and -14 <= t[1] <= 14, hops):
                        continue
                    proven += 1
                    with mock.patch.object(smelting, "_apart", lambda *a: False), \
                            mock.patch.object(smelting, "_dead_end", lambda *a: False):
                        found = route(start, goal, blocked, bounds=bounds, expansions=200_000)
                    self.assertIsNone(found, (start, goal, hops, sorted(blocked)))
        self.assertGreater(proven, 50)   # (the maps cut many routes off: the check means something)

    def test_nor_across_barriers_a_hop_may_or_may_not_cross(self):
        # walls 3 to 6 thick across the window, now and then holed, so the hops' entrance rule is what decides
        rng = random.Random(11)
        bounds = (-20, -20, 20, 20)
        apart_with_hops = crossed = 0
        for _ in range(120):
            thick, at = rng.randint(3, 6), rng.randint(-6, 2)
            blocked = {(x, y) for x in range(-20, 21) for y in range(at, at + thick)}
            for _hole in range(rng.choice((0, 0, 1, 2))):
                hx = rng.randint(-20, 20)
                blocked -= {(hx, y) for y in range(at, at + rng.randint(1, thick))}
            blocked |= {(rng.randint(-20, 20), rng.randint(-20, 20)) for _ in range(rng.randint(0, 60))}
            start, goal = (rng.randint(-20, 20), -20), (rng.randint(-20, 20), 20)
            blocked -= {start, goal}
            free = lambda t, b=blocked, g=goal: (t == g or t not in b) and -20 <= t[0] <= 20 and -20 <= t[1] <= 20
            if smelting._apart(start, goal, free, True):
                apart_with_hops += 1
                with mock.patch.object(smelting, "_apart", lambda *a, **k: False),                         mock.patch.object(smelting, "_dead_end", lambda *a: False):
                    self.assertIsNone(route(start, goal, blocked, bounds=bounds, expansions=300_000),
                                      (thick, at, start, goal))
            elif not smelting._apart(start, goal, lambda t, b=blocked, g=goal: free(t), False):
                pass
            else:
                crossed += 1   # cut off on foot, not with hops: only a hop gets across
        self.assertGreater(apart_with_hops, 20)
        self.assertGreater(crossed, 5)


if __name__ == "__main__":
    unittest.main()
