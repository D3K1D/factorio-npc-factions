"""Belts and pipes crossing the wall line go under it (companion/perimeter.py, D-8): the wall kept a hole where each
crossed, and the biters walked through it."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import perimeter  # noqa: E402
from companion.modules import NORTH, SOUTH  # noqa: E402
from test_defense_plans_perimeter import site, wall, world  # noqa: E402

RECIPES = [{"name": n} for n in ("gun-turret", "stone-wall", "gate", "firearm-magazine", "underground-belt",
                                 "pipe-to-ground")]
ROW = -576   # the plot's north edge: the wall's first layer two rows in


def belt(x, y, d, name="transport-belt"):
    return {"n": name, "t": "transport-belt", "x": x + 0.5, "y": y + 0.5, "d": d, "w": 1, "h": 1, "f": "npc-faction-1"}


def pipe(x, y):
    return {"n": "pipe", "t": "pipe", "x": x + 0.5, "y": y + 0.5, "w": 1, "h": 1, "f": "npc-faction-1"}


def at(plan, x):
    """What the plan puts in column x, by row: (name, direction, kind)."""
    return {math.floor(e["position"]["y"]): (e["prototype"], e.get("direction", 0), e.get("belt_to_ground_type"))
            for e in plan["entities"] if math.floor(e["position"]["x"]) == x}


class CrossingTests(unittest.TestCase):
    def test_a_belt_across_the_wall_line_goes_under_it_the_way_it_runs(self):
        line = [belt(-300, y, SOUTH) for y in range(ROW - 4, ROW + 12)]           # in from outside the plot
        plan = perimeter.plan_wall(site(recipes=RECIPES), world(line))
        col = at(plan, -300)
        self.assertEqual(col[ROW + perimeter.CROSS_OUT][:2], ("underground-belt", SOUTH))
        self.assertEqual(col[ROW + perimeter.CROSS_IN][:2], ("underground-belt", SOUTH))
        self.assertEqual({col[ROW + o][0] for o in range(perimeter.WALL_OFFSET, perimeter.CROSS_IN)}, {"stone-wall"})
        ends = [e for e in plan["entities"] if e["prototype"] == "underground-belt"]
        self.assertEqual(sorted(e["position"]["y"] for e in ends if e.get("belt_to_ground_type") == "input"),
                         [ROW + perimeter.CROSS_OUT + 0.5])                     # (its way in: the outer end)
        under = next(s for s in plan["stages"] if "-under-" in s["id"])
        door = next(s for s in plan["stages"] if "-door-" in s["id"])
        self.assertEqual(under["depends_on"], [door["id"]])                     # (the door before anything closes)
        self.assertEqual(plan["keep"]["extra"], 5)                              # (its ends, its walls deeper in)
        # out of the plot instead: in at the inner end
        out = perimeter.plan_wall(site(recipes=RECIPES), world([belt(-300, y, NORTH) for y in range(ROW - 4, ROW + 12)]))
        ins = [e for e in out["entities"] if e["prototype"] == "underground-belt"
               and e.get("belt_to_ground_type") == "input"]
        self.assertEqual([e["position"]["y"] for e in ins], [ROW + perimeter.CROSS_IN + 0.5])

    def test_a_pipe_across_goes_under_it_too_and_a_turning_belt_or_no_underground_yet_leaves_it(self):
        pipes = perimeter.plan_wall(site(recipes=RECIPES), world([pipe(-300, y) for y in range(ROW - 4, ROW + 12)]))
        col = at(pipes, -300)
        self.assertEqual(col[ROW + perimeter.CROSS_OUT][:2], ("pipe-to-ground", NORTH))   # (open to the outside)
        self.assertEqual(col[ROW + perimeter.CROSS_IN][:2], ("pipe-to-ground", SOUTH))    # (and to the inside)
        turning = [belt(-300, y, SOUTH) for y in range(ROW - 4, ROW + 4)] + \
            [belt(x, ROW + 4, 4) for x in range(-300, -290)]                      # (it turns east under the line)
        plan = perimeter.plan_wall(site(recipes=RECIPES), world(turning))
        self.assertNotIn("underground-belt", {e["prototype"] for e in plan["entities"]})
        plain = [r for r in RECIPES if r["name"] != "underground-belt"]
        plan = perimeter.plan_wall(site(recipes=plain), world([belt(-300, y, SOUTH) for y in range(ROW - 4, ROW + 12)]))
        self.assertNotIn(ROW + perimeter.WALL_OFFSET, at(plan, -300))          # (the hole stays till it can)


class ReviewTests(unittest.TestCase):
    """Codex's review, D-8-crossings."""

    def walled_but_for(self, x):
        """The north and west edges walled, the north's door at -386..-383, save column x (a belt crosses there)."""
        out = [wall(a, ROW + 2, "gate", "gate") if -386 <= a <= -383 else wall(a, ROW + 2)
               for a in range(-574, -192) if a != x]
        return out + [wall(-574, y) for y in range(-574, -192)]

    def test_a_hole_only_a_belt_leaves_is_still_closed(self):
        line = [belt(-300, y, SOUTH) for y in range(ROW - 4, ROW + 12)]
        plan = perimeter.plan_wall(site(recipes=RECIPES), world(self.walled_but_for(-300) + line), limit=300)
        self.assertEqual({e["prototype"] for e in plan["entities"]}, {"underground-belt", "stone-wall"})
        with self.assertRaises(perimeter.Done):                       # (a wall short of a plug: it waits for walls)
            perimeter.plan_wall(site(recipes=RECIPES), world(self.walled_but_for(-300) + line), limit=1)

    def test_walls_short_or_no_underground_yet_leave_a_hole_it_comes_back_for(self):
        line = [belt(-300, y, SOUTH) for y in range(ROW - 4, ROW + 12)]
        plan = perimeter.plan_wall(site(recipes=RECIPES), world(line), limit=4)
        self.assertEqual(sorted(e["prototype"] for e in plan["entities"] if e["prototype"] != "gate"),
                         ["stone-wall"] * 4 + ["underground-belt"] * 2)   # (four walls: the plug, nothing more)
        # the game counts what still crosses the wall line of the ring it holds (walls up before it could go under)
        plain = [r for r in RECIPES if r["name"] != "underground-belt"]
        board = {"evolution": 0, "attacks": [], "crossings": {"north": [{"along": -300, "under": "underground-belt"}]}}
        self.assertFalse(perimeter.holes_due(site(recipes=plain, threat_board=board)))
        self.assertTrue(perimeter.holes_due(site(recipes=RECIPES, threat_board=board)))   # (it can make one now)
        east = {**board, "crossings": {"east": board["crossings"]["north"]}}               # (a side it does not hold)
        self.assertFalse(perimeter.holes_due(site(recipes=RECIPES, threat_board=east)))
        past = {**board, "crossings": {"north": [{"along": -150, "under": "underground-belt"}]}}   # (past its end)
        self.assertFalse(perimeter.holes_due(site(recipes=RECIPES, threat_board=past)))

    def test_a_plug_counts_one_tile_of_its_line_and_part_built_never_ahead(self):
        # (a plug is one tile of the line and five more pieces: counted, it made the ring walled too soon)
        keep = {"ring": [-576, -576, -192, -192], "ring_gen": 1, "ring_side": "north", "ring_layer": 1, "extra": 50,
                "ends": 20}
        block = {"id": "w", "purpose": "plot-wall", "box": [-574, -574, -193, -573], "keep": keep,
                 "buildings": 160, "planned": 160}                           # (ten plugs: ten tiles and 50 more)
        self.assertEqual(perimeter._layer_pieces(block), 110)
        self.assertEqual(perimeter._layer_pieces({**block, "buildings": 64}), 14)   # (its door and plugs up first)
        self.assertEqual(perimeter._layer_pieces({**block, "buildings": 30}), 0)
        self.assertEqual(perimeter._layer_pieces({**block, "keep": {**keep, "extra": None}}), 160)   # (an older plan)
        self.assertEqual(perimeter._layer_pieces(block, layers=True), 140)   # (on any layer: its walls deeper in too)
        self.assertEqual(perimeter.wall_progress(site(base=[block]))[0], 110)

    def test_a_branch_beside_the_crossing_leaves_it_as_it_is(self):
        edge = next(e for e in perimeter.edges(site()) if e.name == "north")
        pipes = [pipe(-300, y) for y in range(ROW - 4, ROW + 12)]
        branch = {"n": "pipe-to-ground", "t": "pipe-to-ground", "x": -298.5, "y": ROW + 3.5, "d": 12, "w": 1, "h": 1,
                  "f": "npc-faction-1"}
        self.assertEqual(len(perimeter.crossings(edge, world(pipes))), 1)
        self.assertEqual(perimeter.crossings(edge, world(pipes + [branch])), [])
        belts = [belt(-300, y, SOUTH) for y in range(ROW - 4, ROW + 12)]
        self.assertEqual(perimeter.crossings(edge, world(belts + [belt(-299, ROW + 3, 12)])), [])   # (loading onto it)
        self.assertEqual(len(perimeter.crossings(edge, world(belts + [belt(-299, ROW + 3, 4)]))), 1)  # (running away)


if __name__ == "__main__":
    unittest.main()
