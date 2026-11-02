"""An outpost beyond the team's wall holds its own: turrets first, lasers where it can make them, two walls with a
door a tank fits through, the walls built as walls are made (the user, 2026-10-25)."""

import copy
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from companion import bus_planner, construction, growth, outpost
from companion.base_plan import Layout, Stage
from companion.bus import Bus
from companion.modules import Placed
from companion.ratios import Book
from companion.smelting import footprint

GUN = {"name": "gun-turret", "kind": "ammo", "range": 18, "w": 2, "h": 2, "ammo_categories": ["bullet"]}
LASER = {"name": "laser-turret", "kind": "electric", "range": 24, "w": 2, "h": 2}
KIT = {"turrets": [LASER, GUN], "walls": ["stone-wall"], "gates": ["gate"], "ammo": {"bullet": ["firearm-magazine"]}}
BOX = (100, 100, 180, 140)   # what the outpost holds; home lies to the north-west
TYPES = {"stone-wall": "wall", "gate": "gate", "laser-turret": "electric-turret", "gun-turret": "ammo-turret",
         "small-electric-pole": "electric-pole"}


def tiles_of(placed):
    out = set()
    for e in placed:
        out |= footprint([e])
    return out


class RingTests(unittest.TestCase):

    def test_two_walls_round_it_and_a_door_of_gates_facing_home(self):
        guard, wall, _, notes, joined = outpost.ring(BOX, (0, 0), KIT, set(), [(90.5, 90.5)])
        self.assertTrue(joined)
        self.assertEqual(notes, [])
        walls = {(math.floor(e.x), math.floor(e.y)) for e in wall if e.name == "stone-wall"}
        x0, y0 = BOX[0] - outpost.RING_MARGIN, BOX[1] - outpost.RING_MARGIN
        x1, y1 = BOX[2] + outpost.RING_MARGIN, BOX[3] + outpost.RING_MARGIN
        for layer in range(outpost.RING_LAYERS):   # (the east and south sides whole; home is north-west)
            self.assertTrue(all((x1 - 1 + layer, y) in walls for y in range(y0, y1)))
            self.assertTrue(all((x, y1 - 1 + layer) in walls for x in range(x0, x1)))
        gates = sorted((math.floor(e.x), math.floor(e.y)) for e in wall if e.name == "gate")
        self.assertEqual(len(gates), outpost.DOOR * outpost.RING_LAYERS)
        self.assertEqual({x for x, _ in gates}, {x0, x0 - 1})                # the west side: home is farther west
        self.assertEqual(wall[0].name, "gate")                               # the door before the wall closes
        self.assertFalse(tiles_of(guard) & tiles_of(wall))

    def test_laser_turrets_round_it_with_their_poles_and_a_gun_pair_by_the_door(self):
        guard, _, supplies, _, _ = outpost.ring(BOX, (0, 0), KIT, set(), [(90.5, 90.5)])
        lasers = [e for e in guard if e.name == "laser-turret"]
        guns = [e for e in guard if e.name == "gun-turret"]
        self.assertGreaterEqual(len(lasers), 20)
        self.assertEqual(len(guns), 2)
        self.assertEqual(len(supplies), 2)                                   # only the guns need ammo
        self.assertTrue(all(s["slot"] == "ammo" for s in supplies))
        self.assertTrue(any(e.name == "small-electric-pole" for e in guard))
        # every turret between the wall and what the outpost holds
        for e in lasers + guns:
            self.assertFalse(BOX[0] <= e.x < BOX[2] and BOX[1] <= e.y < BOX[3])
        seen = set()
        for e in guard:
            here = footprint([e])
            self.assertFalse(here & seen, e)
            seen |= here

    def test_gun_turrets_only_where_it_makes_no_laser_turrets_and_no_gate_leaves_the_door_open(self):
        kit = {**KIT, "turrets": [GUN], "gates": []}
        guard, wall, supplies, _, _ = outpost.ring(BOX, (0, 0), kit, set(), [(90.5, 90.5)])
        self.assertTrue(guard and all(e.name == "gun-turret" for e in guard))
        self.assertEqual(len(supplies), len(guard))
        x0 = BOX[0] - outpost.RING_MARGIN
        mid = round((BOX[1] + BOX[3]) / 2)
        door = range(mid - outpost.DOOR // 2, mid - outpost.DOOR // 2 + outpost.DOOR)
        walls = {(math.floor(e.x), math.floor(e.y)) for e in wall}
        self.assertFalse(any((x0, y) in walls for y in door))

    def test_what_crosses_it_keeps_its_gap_and_its_own_pieces_standing_are_laid_again_the_same(self):
        belt = {(BOX[0] - outpost.RING_MARGIN - k, 130) for k in range(outpost.RING_LAYERS)}
        guard, wall, _, _, _ = outpost.ring(BOX, (0, 0), KIT, set(belt), [(90.5, 90.5)])
        self.assertFalse(tiles_of(wall) & belt)
        rows = [{"n": e.name, "t": TYPES[e.name], "x": e.x, "y": e.y, "w": 2 if "turret" in e.name else 1,
                 "h": 2 if "turret" in e.name else 1, "f": "player"} for e in guard + wall]
        dump = {"force": "player", "entities": rows}
        standing = tiles_of(guard + wall) | belt
        g2, w2, _, _, _ = outpost.ring(BOX, (0, 0), KIT, standing, [(90.5, 90.5)], outpost.own_at(dump))
        self.assertEqual(sorted((e.name, e.x, e.y) for e in w2), sorted((e.name, e.x, e.y) for e in wall))
        turrets = lambda g: sorted((e.name, e.x, e.y) for e in g if "turret" in e.name)
        self.assertEqual(turrets(g2), turrets(guard))

    def test_inside_the_team_area_the_team_s_wall_holds_it(self):
        team = {"team": {"area": [-576, -576, 576, 576]}}
        self.assertFalse(outpost.needs_ring(team, (100, 100, 180, 140)))
        self.assertTrue(outpost.needs_ring(team, (-600, 100, -500, 140)))
        self.assertTrue(outpost.needs_ring({}, (100, 100, 180, 140)))     # (no team world: on its own)

    def test_a_spot_by_a_nest_is_not_taken(self):
        tiles = {(x, y) for x in range(0, 10) for y in range(0, 10)}
        self.assertTrue(outpost._nest_near({}, tiles, [(9 + outpost.NEST_CLEAR - 1, 5)]))
        self.assertFalse(outpost._nest_near({}, tiles, [(9 + outpost.NEST_CLEAR + 1, 5)]))


class WallsFirstTests(unittest.TestCase):

    def layout(self):
        walls = [Placed("stone-wall", x + 0.5, 0.5) for x in range(100)]
        return Layout(bus=Bus(x=0, y=0, columns=4, length=10), stages=[
            Stage("outpost-smelt-iron-plate-x1", []), Stage("outpost-wall-iron-plate-1-1", walls),
            Stage("outpost-wall-iron-plate-1-2", walls[:40])])

    def test_a_stretch_of_its_wall_comes_next_once_the_walls_in_stock_cover_it(self):
        lay = self.layout()
        stage, _ = bus_planner.walls_first({"inventory": {"stone-wall": 30}}, lay, {})
        self.assertIsNone(stage)
        stage, _ = bus_planner.walls_first({"inventory": {"stone-wall": 50}}, lay, {})
        self.assertEqual(stage.name, "outpost-wall-iron-plate-1-2")        # (a shorter stretch the stock covers)
        stage, why = bus_planner.walls_first({"inventory": {"stone-wall": 60}, "base": [{"stock": {"stone-wall": 50}}]},
                                             lay, {})
        self.assertEqual(stage.name, "outpost-wall-iron-plate-1-1")
        self.assertIn("walls in stock", why)
        stage, _ = bus_planner.walls_first({"inventory": {"stone-wall": 40}}, lay, {"outpost-wall-iron-plate-1-1": 1})
        self.assertEqual(stage.name, "outpost-wall-iron-plate-1-2")

    def test_its_wall_is_not_part_of_the_expansion_so_the_plates_never_wait_on_it(self):
        self.assertTrue(growth.in_section("outpost-guard-iron-plate-x1", "-x1"))
        self.assertFalse(growth.in_section("outpost-wall-iron-plate-1-1", "-x1"))


@patch("companion.base_plan.FIRST_FURNACES", 48)   # (a block with nothing left to finish: no thin first stage, F-21)
class BeyondTheTeamAreaTests(unittest.TestCase):
    """Run 33's bus (2026-10-25), its outpost's patch taken as beyond the team area."""

    def setUp(self):
        bus_planner._searches.clear()
        self.wait = patch.object(bus_planner, "EXPANSION_WAIT_S", 600)
        self.wait.start()
        self.addCleanup(self.wait.stop)

    def test_its_guard_comes_first_its_wall_after_the_furnaces(self):
        cap = json.loads((Path(__file__).parent / "fixtures" / "bw-tight-bus.json").read_text(encoding="utf-8"))
        site, world = cap["site"], copy.deepcopy(cap["world"])
        world["team"]["area"] = [-150, -150, 576, 576]
        with patch.object(construction, "expansion", return_value=1.0):
            plan = bus_planner.plan_stage(site, world, Book.from_site(site))
        self.assertEqual(plan["base_plan"]["extra"], ["outpost-iron-plate-1-15"])
        self.assertEqual(plan["stages"][0]["id"], "outpost-guard-iron-plate-x1")
        self.assertTrue(plan["entities"] and all(e["prototype"] == "gun-turret" for e in plan["entities"]))
        self.assertEqual(len(plan["stages"][0]["manual_supplies"]), len(plan["entities"]))   # their ammo
        layout = bus_planner.replay({**site, "base_plan": plan["base_plan"]}, world, Book.from_site(site))
        names = [s.name for s in layout.stages]
        smelt = names.index("outpost-smelt-iron-plate-x1")
        walls = [n for n in names if n.startswith("outpost-wall-iron-plate-1-")]
        self.assertTrue(walls and all(names.index(n) > smelt for n in walls))
        self.assertLess(names.index("outpost-guard-iron-plate-x1"), names.index("supply-iron-plate-x1"))
        # nothing of its wall where its turrets, belts, drills or furnaces go
        ours = [e for s in layout.stages if s.name.startswith(("outpost-", "supply-iron-plate-x1")) for e in s.entities]
        seen = set()
        for e in ours:
            here = footprint([e])
            self.assertFalse(here & seen, e)
            seen |= here


if __name__ == "__main__":
    unittest.main()
