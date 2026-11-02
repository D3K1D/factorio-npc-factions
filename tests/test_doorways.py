"""The way through each door kept clear (companion/doorways.py, D-7): a car lines up on a door's axis about a dozen tiles
out and drives straight through (NPC Movement's M-2 probe, 2026-10-26), so no planner builds in front of or behind a
door, and a new door goes where that way is clear."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import base_plan, doorways, outpost, perimeter  # noqa: E402
from test_defense_plans_perimeter import site, wall, world  # noqa: E402


def gate(x, y, d=None):
    g = {"n": "gate", "t": "gate", "x": x + 0.5, "y": y + 0.5, "w": 1, "h": 1, "f": "npc-faction-1"}
    if d is not None:
        g["d"] = d
    return g


class LaneTests(unittest.TestCase):
    def test_each_door_is_found_from_its_gates_and_kept_clear_both_ways(self):
        ew = [gate(x, y, 4) for x in range(10, 14) for y in (0, 1)]        # across a line running east-west
        ns = [gate(50, y, 0) for y in range(20, 24)]                       # across one running north-south
        dump = {"entities": ew + ns}
        found = sorted(doorways.doors(dump))
        self.assertEqual(found, [((10, 0, 13, 1), (True,)), ((50, 20, 50, 23), (False,))])
        tiles = doorways.lanes(dump)
        self.assertIn((9, -doorways.DOOR_LANE), tiles)                     # out in front, a tile wider
        self.assertIn((14, 1 + doorways.DOOR_LANE), tiles)                 # and behind
        self.assertNotIn((11, 0), tiles)                                   # (the door itself is its gates')
        self.assertNotIn((8, -1), tiles)                                   # (nor wider than a tile either side)
        self.assertIn((50 + doorways.DOOR_LANE, 24), tiles)
        self.assertIn((50 - doorways.DOOR_LANE, 19), tiles)
        # (a door with no direction given is wider along its wall; square, either way: Codex, D-7-door-lanes)
        self.assertEqual(doorways.doors({"entities": [gate(x, 5) for x in range(4)]}), [((0, 5, 3, 5), (True,))])
        square = {"entities": [gate(x, y) for x in range(4) for y in range(4)]}
        self.assertEqual(doorways.doors(square), [((0, 0, 3, 3), (True, False))])
        self.assertTrue({(1, -3), (-3, 1)} <= doorways.lanes(square))

    def test_no_planner_builds_in_a_doors_way(self):
        dump = {"entities": [gate(x, 0, 4) for x in range(10, 14)], "water": []}
        blocked = base_plan.blocked_tiles(dump)
        self.assertIn((11, -5), blocked)
        self.assertIn((12, 6), blocked)
        self.assertNotIn((20, -5), blocked)

    def test_the_wall_still_grows_through_a_doors_way_and_turrets_keep_out_of_it(self):
        # the north edge one layer deep with its door at x -386..-383: the next layer in crosses the door's way
        out = [gate(x, -574, 4) if -386 <= x <= -383 else wall(x, -574) for x in range(-574, -192)]
        out += [wall(-574, y) for y in range(-574, -192)]
        plan = perimeter.plan_wall(site(), world(out), limit=perimeter.WALL_RESERVE + 400)
        row = {math.floor(e["position"]["x"]): e["prototype"] for e in plan["entities"] if e["position"]["y"] == -572.5}
        self.assertEqual([row.get(x) for x in range(-387, -381)],
                         ["stone-wall", "gate", "gate", "gate", "gate", "stone-wall"])
        lane = doorways.lanes(world(out))
        turrets = perimeter.plan_turrets(site(), world(out))
        for e in turrets["entities"]:
            if e["prototype"] in ("gun-turret", "small-electric-pole"):
                self.assertFalse(perimeter._tiles(perimeter.Placed(e["prototype"], e["position"]["x"],
                                                                   e["position"]["y"]), 2, 2) & lane, e)

    def test_only_a_doors_own_wall_grows_into_its_way_and_no_farther_than_walls_get_thick(self):
        # (Codex, D-7-door-lanes: a wall row across another door's way would shut it)
        dump = {"force": "npc-faction-1", "entities": [gate(x, 0, 4) for x in range(10, 14)], "water": [],
                "avoid": [{"x0": 14, "y0": -1, "x1": 14, "y1": -1}]}
        room = doorways.wall_room(dump, "npc-faction-1")
        self.assertEqual(set(room), doorways.lane((10, 0, 13, 0), True, depth=doorways.MAX_LAYERS - 1) - {(14, -1)})
        self.assertEqual(room[(11, -2)], (True, True))                            # a gate goes there
        self.assertEqual(room[(9, -2)], (True, False))                            # a wall beside it
        self.assertEqual(doorways.wall_room(dump, "npc-faction-2"), {})           # (another's door: none of it)
        ground = perimeter._Ground(dump)
        self.assertTrue(ground.wall_free((11, 1)) and ground.wall_free((9, -3)))   # its next layers in or out
        self.assertFalse(ground.wall_free((11, -5)))                              # a wall farther out: no
        self.assertFalse(ground.free({(11, 1)}))                                  # nor anything but its wall
        self.assertEqual(doorways.wall_room({"entities": [gate(x, y, 4) for x in range(4) for y in range(4)]}), {})

    def test_a_wall_line_in_front_of_its_own_door_goes_through_it_and_leaves_anothers_door_open(self):
        edge = next(e for e in perimeter.edges(site()) if e.name == "north")
        row = edge.row(perimeter.WALL_OFFSET)
        x0 = (edge.lo + edge.hi) // 2 + 40
        # a door of its own two rows outside the line, off the line's own door: the line goes through it with gates
        mine = [gate(x, row - 2, 4) for x in range(x0, x0 + 4)]
        plan = perimeter.plan_wall(site(), world(mine))
        here = {math.floor(e["position"]["x"]): e["prototype"] for e in plan["entities"] if e["position"]["y"] == row + 0.5}
        self.assertEqual([here.get(x) for x in range(x0 - 1, x0 + 5)],
                         ["stone-wall", "gate", "gate", "gate", "gate", "stone-wall"])
        # a teammate's door there instead: its way is left open (no wall across it)
        theirs = [{**g, "f": "npc-faction-2"} for g in mine]
        plan = perimeter.plan_wall(site(), world(theirs))
        here = {math.floor(e["position"]["x"]) for e in plan["entities"] if e["position"]["y"] == row + 0.5}
        self.assertFalse(here & set(range(x0 - 1, x0 + 5)))

    def test_an_outpost_ring_with_its_inner_gates_standing_gets_its_outer_ones(self):
        # (Codex, D-7-door-lanes: a partly built outpost lost its outer gate layer when laid out again)
        box, home = (100, 100, 180, 140), (0, 0)
        kit = {"turrets": [], "walls": ["stone-wall"], "gates": ["gate"], "ammo": {}}
        _, first, _, _, _ = outpost.ring(box, home, kit, set(), [])
        inner_x = box[0] - outpost.RING_MARGIN
        inner = [g for g in first if g.name == "gate" and math.floor(g.x) == inner_x]
        dump = {"force": "npc-faction-1", "water": [],
                "entities": [{**gate(math.floor(g.x), math.floor(g.y), 0)} for g in inner]}
        taken = set(base_plan.blocked_tiles(dump)) - set(doorways.wall_room(dump, "npc-faction-1"))   # (outpost.plan)
        _, again, _, _, _ = outpost.ring(box, home, kit, taken, [], outpost.own_at(dump))
        outer = [g for g in again if g.name == "gate" and math.floor(g.x) == inner_x - 1]
        self.assertEqual(len(outer), outpost.DOOR)

    def test_a_new_door_goes_where_its_way_through_is_clear(self):
        # a furnace row just inside the north edge's middle: the door moves along to where the way in is clear
        edge = next(e for e in perimeter.edges(site()) if e.name == "north")
        middle = (edge.lo + edge.hi) // 2
        clear = perimeter.doors(edge, world())
        self.assertIn(middle, clear)
        row = [{"n": "stone-furnace", "t": "furnace", "x": x + 1.0, "y": -560.0, "w": 2, "h": 2,
                "f": "npc-faction-1"} for x in range(middle - 12, middle + 12, 2)]
        moved = perimeter.doors(edge, world(row))
        self.assertNotIn(middle, moved)                                    # (just past the row's end)
        box = (min(moved), edge.row(perimeter.WALL_OFFSET), max(moved),
               edge.row(perimeter.WALL_OFFSET + perimeter.MAX_LAYERS - 1))
        solid = base_plan.blocked_tiles({"entities": row, "water": []})
        self.assertFalse(doorways.lane(box, True) & solid)


if __name__ == "__main__":
    unittest.main()
