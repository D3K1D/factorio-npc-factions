"""Pipe routing keeps fluids apart and passes under buildings."""
import unittest

from companion.modules import EAST, WEST
from companion.pipes import PipeMap, SPAN


def tiles(placed, name=None):
    return {(int(p.x - 0.5), int(p.y - 0.5)) for p in placed if name is None or p.name == name}


def connected(placed):
    """Follow the route as the game would connect it: pipes to neighbours, pipe-to-ground pairs."""
    by_tile = {(int(p.x - 0.5), int(p.y - 0.5)): p for p in placed}
    steps = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
    first = next(iter(by_tile))
    seen, todo = {first}, [first]
    while todo:
        t = todo.pop()
        p = by_tile[t]
        out = []
        if p.name == "pipe":
            out = [(t[0] + dx, t[1] + dy) for dx, dy in steps.values()]
        else:
            dx, dy = steps[p.direction]
            out = [(t[0] + dx, t[1] + dy)]
            for k in range(1, SPAN + 1):
                u = (t[0] - dx * k, t[1] - dy * k)
                q = by_tile.get(u)
                if q and q.name == "pipe-to-ground":
                    out.append(u)
                    break
        for n in out:
            q = by_tile.get(n)
            if not q or n in seen:
                continue
            if q.name == "pipe-to-ground" and n in [(t[0] + dx, t[1] + dy) for dx, dy in steps.values()]:
                # a neighbour pipe-to-ground connects only if its open side faces us
                ddx, ddy = steps[q.direction]
                if (n[0] + ddx, n[1] + ddy) != t:
                    continue
            seen.add(n)
            todo.append(n)
    return seen == set(by_tile)


class PipeTests(unittest.TestCase):
    def test_a_short_open_route_is_plain_pipe(self):
        m = PipeMap()
        placed = m.route("water", (0, 0), (2, 0))
        self.assertEqual(tiles(placed, "pipe"), {(0, 0), (1, 0), (2, 0)})

    def test_it_goes_under_a_wall_of_buildings(self):
        m = PipeMap(blocked={(5, y) for y in range(-20, 21)})
        placed = m.route("water", (0, 0), (12, 0))
        self.assertIsNotNone(placed)
        self.assertNotIn((5, 0), tiles(placed))
        grounds = [p for p in placed if p.name == "pipe-to-ground"]
        self.assertTrue(grounds)
        self.assertTrue(connected(placed))
        # entrance faces back west, exit faces on east
        self.assertEqual([g.direction for g in grounds[:2]], [WEST, EAST])

    def test_long_routes_mostly_run_under_ground(self):
        m = PipeMap()
        placed = m.route("crude-oil", (0, 0), (60, 0))
        self.assertLess(len(placed), 20)
        self.assertTrue(connected(placed))

    def test_fluids_never_touch(self):
        m = PipeMap()
        m.add_pipes(m.route("water", (0, 0), (0, 10)), "water")
        oil = m.route("crude-oil", (-1, 5), (6, 5))
        self.assertIsNotNone(oil)
        for (x, y) in tiles(oil, "pipe"):
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                self.assertNotEqual(m.pipes.get(n, "crude-oil"), "water")
        self.assertTrue(connected(oil))

    def test_reserved_connections_stay_clear(self):
        m = PipeMap()
        m.reserve((3, 0), "")              # an unused machine connection
        m.reserve((4, 0), "heavy-oil")     # another fluid's inlet
        placed = m.route("water", (0, 0), (8, 0))
        self.assertFalse(tiles(placed, "pipe") & {(3, 0), (4, 0)})

    def test_no_pipe_beside_another_fluids_inlet_or_facing_end(self):
        # (a plain pipe joins every side: beside the heavy cracker's heavy oil inlet a light oil pipe mixed the two)
        m = PipeMap()
        m.reserve((4, 0), "heavy-oil")     # an inlet its pipe comes to later
        placed = m.route("light-oil", (0, 0), (8, 0))
        near = {(3, 0), (5, 0), (4, 1), (4, -1)}
        self.assertFalse(tiles(placed, "pipe") & near)
        m = PipeMap()
        from companion.modules import Placed
        m.add_pipes([Placed("pipe-to-ground", 4.5, 0.5, WEST)], "heavy-oil")   # its pipe joins on the west
        placed = m.route("light-oil", (0, 1), (0, -1))
        self.assertNotIn((3, 0), tiles(placed, "pipe"))
        m = PipeMap()
        m.add_pipes([Placed("pipe-to-ground", 4.5, 0.5, EAST)], "heavy-oil")   # facing away: a pipe may stand beside
        self.assertTrue(m._pipe_ok((3, 0), "light-oil", set()))
        # an end standing on the map (rocket_plan.pipe_map), facing west: no other fluid's pipe on its open side
        from companion.rocket_plan import pipe_map
        dump = {"entities": [{"n": "pipe-to-ground", "t": "pipe-to-ground", "x": 4.5, "y": 0.5, "d": WEST,
                              "fl": "water"}]}
        m = pipe_map(dump, [], {})
        self.assertFalse(m._pipe_ok((3, 0), "crude-oil", set()))
        self.assertTrue(m._pipe_ok((3, 0), "water", set()))

    def test_a_pair_never_straddles_another_pairs_end(self):
        m = PipeMap(blocked={(x, 0) for x in range(2, 9)} | {(x, 1) for x in range(2, 9)}
                    | {(x, -1) for x in range(2, 9)})
        a = m.route("water", (0, 0), (10, 0))
        ends = [t for t in tiles(a, "pipe-to-ground")]
        self.assertTrue(ends)
        b = m.route("crude-oil", (0, 5), (10, 5))
        self.assertTrue(connected(b))

    def test_a_branch_starts_on_the_existing_line(self):
        m = PipeMap()
        trunk = m.route("water", (0, 0), (10, 0))
        branch = m.route("water", (5, 8), (5, 8), joins=tiles(trunk, "pipe"))
        self.assertEqual(tiles(branch), {(5, 8)})
        branch = m.route("water", (0, 0), (20, 6), joins=tiles(trunk, "pipe"))
        self.assertIsNotNone(branch)


if __name__ == "__main__":
    unittest.main()
