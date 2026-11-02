"""The perimeter ammo loop (companion/ammo_line.py, D-19; the user's U-1: hand feeding is not automation): one closed
belt round the wall ring just inside its turrets, an inserter from it into each turret on the ring, poles along it, fed
from the magazine row (else one chest filled by hand); turrets added later get their arms; turrets inside the base none.
And in the game, no turret loaded by hand while its own inserter brings it ammunition."""
from __future__ import annotations

import collections
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import ammo_line  # noqa: E402
from companion import catalog_planner as cp  # noqa: E402
from companion import layout_planner  # noqa: E402
from companion.modules import EAST, NORTH, SOUTH, WEST  # noqa: E402
from test_defense_plans_perimeter import site, world  # noqa: E402

RING = [-500, -500, -300, -300]   # (world tiles: local = world in these tests)
WALLS = [{"purpose": "plot-wall", "box": [-500, -500, -300, -497],
          "keep": {"ring": RING, "ring_gen": 1, "ring_side": s}} for s in ("north", "east", "south", "west")]
CORE = {"purpose": "iron-smelting-start", "box": [-420, -420, -400, -400], "buildings": 4,
        "machines": {"stone-furnace": 2}, "recipes": ["iron-plate"], "capacity": {"makes": {"iron-plate": 1.0}}}
RECIPES = [{"name": n} for n in ("gun-turret", "firearm-magazine", "inserter", "transport-belt", "underground-belt",
                                 "small-electric-pole")]
ARMS = {"turrets": [{"name": "gun-turret", "kind": "ammo", "range": 18, "w": 2, "h": 2, "ammo_categories": ["bullet"]}],
        "ammo": {"bullet": ["firearm-magazine"]}}
POLE = {"n": "small-electric-pole", "t": "electric-pole", "x": -410.5, "y": -410.5, "w": 1, "h": 1, "f": "npc-faction-1"}
ROW = {"purpose": "defense-production", "box": [-440, -440, -430, -430],
       "keep": {"item": "firearm-magazine", "outputs": [[-435.5, -435.5]]}}
ROW_CHEST = {"n": "iron-chest", "t": "container", "x": -435.5, "y": -435.5, "w": 1, "h": 1, "f": "npc-faction-1"}
LOOP_ROWS = (-490, -311)   # (its line on each side: 10 tiles in from the ring's edge)


def gun(x, y, ammo=5):
    return {"x": x, "y": y, "name": "gun-turret", "kind": "ammo-turret", "ammo": ammo, "health": 1}


ON_RING = [gun(x, -492) for x in (-460, -440, -420)]   # (north side: tiles 7 and 8 in from the ring's edge)


def ready(base=(), guards=ON_RING, **extra):
    return site(**{"base": [CORE, *WALLS, *base], "guards": list(guards), "recipes": RECIPES,
                   "completed_purposes": ["steam-power"], "defense": ARMS, **extra})


def tile(e):
    return math.floor(e["position"]["x"]), math.floor(e["position"]["y"])


STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}


def closed(plan):
    """Whether the loop's belts close: each plain belt's and each underground exit's next tile along its way holds a
    belt or an underground of the loop (an underground entrance passes under to its exit)."""
    lay = {}
    for e in plan["entities"]:
        if e["prototype"] in ("transport-belt", "underground-belt"):
            x, y = tile(e)
            if y in LOOP_ROWS or x in LOOP_ROWS:
                lay[(x, y)] = e
    for (x, y), e in lay.items():
        if e["prototype"] == "underground-belt" and e.get("belt_to_ground_type") == "input":
            continue
        dx, dy = STEP[e.get("direction", 0)]
        if (x + dx, y + dy) not in lay:
            return False
    return True


def whole_loop(base=(), entities=(), **extra):
    """Its loop laid a side a plan, until none is left: (the plans, the keeps they leave)."""
    plans, keeps = [], []
    for _ in range(4):
        s = ready(base=[*base, *keeps], **extra)
        if ammo_line.what_is_due(s) != "loop":
            break
        plan = ammo_line.plan(s, world(entities=[POLE, *entities]))
        plans.append(plan)
        keeps.append({"purpose": ammo_line.PURPOSE, "keep": plan["keep"]})
    return plans, keeps


def lanes_of(door):
    """doorways.lanes as the game's: a dump's own cached lanes first (none, where a caller sets them so)."""
    def lanes(dump):
        cached = dump.get("_lane_tiles") if isinstance(dump, dict) else None
        return cached if cached is not None else door
    return lanes


def joined(plans):
    return {"entities": [e for p in plans for e in p["entities"]], "stages": [s for p in plans for s in p["stages"]]}


class WhoTests(unittest.TestCase):
    def test_only_turrets_on_the_ring_get_the_loop_those_inside_the_base_none(self):
        inside = gun(-410, -430)
        found = ammo_line.ring_turrets(ready(guards=ON_RING + [inside]), tuple(RING))
        self.assertEqual(sorted((x, y) for _, x, y in found), [(-460.0, -492.0), (-440.0, -492.0), (-420.0, -492.0)])
        self.assertIsNone(ammo_line.what_is_due(ready(guards=[inside])))

    def test_due_with_a_ring_its_turrets_power_and_what_a_loop_takes(self):
        self.assertEqual(ammo_line.what_is_due(ready()), "loop")
        full = [gun(x, -492, ammo=20) for x in (-460, -440, -420)]
        self.assertIsNone(ammo_line.what_is_due(ready(guards=full)))         # (not drawn on yet: no loop; D-22)
        self.assertTrue(cp.available(ammo_line.PURPOSE, ready()))
        self.assertIsNone(ammo_line.what_is_due(ready(completed_purposes=[])))          # (no power)
        self.assertIsNone(ammo_line.what_is_due(ready(recipes=RECIPES[:3])))           # (no undergrounds, poles)
        self.assertIsNone(ammo_line.what_is_due(site(**{**ready(), "base": [CORE]})))  # (no ring held)
        starved = {**CORE, "capacity": {"makes": {"iron-plate": 0.1}}}
        self.assertIsNone(ammo_line.what_is_due(site(**{**ready(), "base": [starved, *WALLS]})))   # (no iron for it)
        self.assertEqual(ammo_line.what_is_due(site(**{**ready(), "base": [starved, *WALLS],
                                                       "inventory": {"transport-belt": 400}})), "loop")


class ReviewTests(unittest.TestCase):
    """Codex's D-19 loop review."""

    def test_a_side_left_part_built_is_laid_again(self):
        plans, keeps = whole_loop()
        part = {**keeps[0], "buildings": 5, "planned": 300}
        self.assertEqual(ammo_line.what_is_due(ready(base=[part, *keeps[1:]])), "loop")
        self.assertIsNone(ammo_line.what_is_due(ready(base=[{**part, "buildings": 300}, *keeps[1:]])))

    def test_a_side_left_part_built_is_laid_again_over_its_own_pieces(self):
        plans, keeps = whole_loop()
        first = plans[0]
        standing = [{"n": e["prototype"], "t": "transport-belt", "x": e["position"]["x"], "y": e["position"]["y"],
                     "w": 1, "h": 1, "d": e["direction"], "f": "npc-faction-1"}
                    for e in first["entities"] if e["prototype"] == "transport-belt"][:20]
        part = {**keeps[0], "buildings": 20, "planned": 300}
        again = ammo_line.plan(ready(base=[part, *keeps[1:]]), world(entities=[POLE, *standing]))
        self.assertEqual(again["keep"]["side"], keeps[0]["keep"]["side"])
        self.assertTrue({tile(e) for e in again["entities"]} >= {(math.floor(s["x"]), math.floor(s["y"]))
                                                                  for s in standing})
        # (a factory belt across its line since, running another way: not taken over, gone under; Codex's review)
        factory = {**standing[0], "x": standing[0]["x"] + 60, "d": SOUTH}
        try:
            mixed = ammo_line.plan(ready(base=[part, *keeps[1:]]), world(entities=[POLE, *standing, factory]))
            self.assertNotIn((math.floor(factory["x"]), math.floor(factory["y"])),
                             {tile(e) for e in mixed["entities"]})
        except layout_planner.PlanError:
            pass
        # (no part-built loop there: a factory belt on its line is not taken over; Codex's review)
        try:
            fresh = ammo_line.plan(ready(), world(entities=[POLE, *standing[:5]]))
        except layout_planner.PlanError:
            return
        self.assertFalse({tile(e) for e in fresh["entities"]
                          if e["prototype"] == "transport-belt"} & {(math.floor(s["x"]), math.floor(s["y"]))
                                                                    for s in standing[:5]})

    def test_a_building_inside_a_doors_way_is_passed_by_chaining_round_it(self):
        door = {(x, -490) for x in range(-450, -442)}
        pole = {"n": "small-electric-pole", "t": "electric-pole", "x": -444.5, "y": -489.5, "w": 1, "h": 1,
                "f": "npc-faction-1"}
        with mock.patch("companion.doorways.lanes", side_effect=lanes_of(door)):
            plans, _ = whole_loop(entities=[pole])
        self.assertTrue(closed(joined(plans)))
        self.assertNotIn((-445, -490), {tile(e) for p in plans for e in p["entities"]})

    def test_a_side_laid_runs_on_into_the_next_whose_first_tile_its_last_belt_points_at(self):
        # (the proving ring: the map blocks the tile ahead of a belt, and the east side's first was the north's last's)
        north = whole_loop()[0][0]
        standing = [{"n": e["prototype"], "t": "transport-belt" if e["prototype"] == "transport-belt" else
                     ("inserter" if e["prototype"] == "inserter" else "x"), "x": e["position"]["x"],
                     "y": e["position"]["y"], "w": 1, "h": 1, "d": e["direction"], "f": "npc-faction-1"}
                    for e in north["entities"] if e["prototype"] in ("transport-belt", "inserter")]
        east = ammo_line.plan(ready(base=[{"purpose": ammo_line.PURPOSE, "keep": north["keep"]}]),
                              world(entities=[POLE, *standing]))
        self.assertIn(east["keep"]["side"], ammo_line.SIDES)
        self.assertTrue(any(e["prototype"] == "transport-belt" and tile(e) == (-311, -490) for e in east["entities"])
                        or east["keep"]["side"] != "east")

    def test_a_turret_off_the_ring_s_turret_line_gets_no_arm(self):
        off = gun(-380, -494)                                                  # (centre 6 in: an arm at 9 misses it)
        self.assertEqual(len(ammo_line.ring_turrets(ready(guards=ON_RING + [off]), tuple(RING))), 3)

    def test_something_standing_in_a_door_s_way_is_gone_under_as_a_building(self):
        door = {(x, -490) for x in range(-408, -400)}                         # (8 tiles of a door's way on the line)
        pole = {"n": "small-electric-pole", "t": "electric-pole", "x": -402.5, "y": -489.5, "w": 1, "h": 1,
                "f": "npc-faction-1"}
        with mock.patch("companion.doorways.lanes", side_effect=lanes_of(door)):
            try:
                plans, _ = whole_loop(entities=[pole])
            except layout_planner.PlanError:
                return
        self.assertNotIn((-403, -490), {tile(e) for p in plans for e in p["entities"]})


class LoopTests(unittest.TestCase):
    def test_one_closed_belt_round_the_ring_an_arm_into_each_turret_and_poles_reaching_them(self):
        plans, keeps = whole_loop()
        self.assertEqual([p["keep"]["side"] for p in plans][0], "north")      # (where its turrets stand first)
        self.assertEqual(sorted(p["keep"]["side"] for p in plans), sorted(ammo_line.SIDES))
        plan = joined(plans)
        self.assertTrue(closed(plan))
        kinds = collections.Counter(e["prototype"] for e in plan["entities"])
        self.assertEqual(kinds["transport-belt"], 4 * 179)                    # (a 200 square ring, 10 tiles in)
        arms = [e for e in plan["entities"] if e["prototype"] == "inserter" and tile(e)[1] == -491]
        self.assertEqual(len(arms), 3)
        self.assertTrue(all(e["direction"] == SOUTH for e in arms))           # (facing the loop it picks from)
        poles = [e["position"] for e in plan["entities"] if e["prototype"] == "small-electric-pole"]
        for arm in (e["position"] for e in plan["entities"] if e["prototype"] == "inserter"):
            self.assertTrue(any(abs(p["x"] - arm["x"]) <= 2.5 and abs(p["y"] - arm["y"]) <= 2.5 for p in poles))
        first = plans[0]
        self.assertEqual((first["keep"]["loop"], len(first["keep"]["turrets"]), first["keep"]["source"]),
                         (RING, 3, "chest"))                                  # (no magazine row yet: one chest)
        chest = next(e for e in first["entities"] if e["prototype"] == ammo_line.CHEST)
        self.assertEqual(first["stages"][-1]["manual_supplies"],
                         [{"entity_id": chest["id"], "item": "firearm-magazine", "slot": "input"}])
        tiles = [tile(e) for e in plan["entities"]]
        self.assertEqual(len(tiles), len(set(tiles)))
        self.assertIsNone(ammo_line.what_is_due(ready(base=keeps)))

    def test_it_goes_under_a_doors_way_and_what_stands_on_its_line(self):
        door = {(x, y) for x in range(-405, -397) for y in range(-500, -470)}   # (a door's way in, 8 wide)
        crossing = [{"n": "transport-belt", "t": "transport-belt", "x": x + 0.5, "y": -489.5, "w": 1, "h": 1,
                     "f": "npc-faction-1"} for x in (-450, -449)]           # (a belt crossing the loop's line)
        with mock.patch("companion.doorways.lanes", side_effect=lanes_of(door)):
            plans, _ = whole_loop(entities=crossing)
        plan = joined(plans)
        under = [tile(e) for e in plan["entities"] if e["prototype"] == "underground-belt"]
        self.assertIn((-451, -490), under)                                    # (in before the crossing)
        self.assertIn((-448, -490), under)                                    # (out after it)
        self.assertFalse(set(under) & {(-450, -490), (-449, -490)})
        self.assertFalse({tile(e) for e in plan["entities"] if e["prototype"] == "transport-belt"} & door)
        self.assertTrue(closed(plan))

    def test_a_building_too_wide_to_go_under_is_gone_round(self):
        # (D-20, late-combat: the ring's line crossed a building wider than a pair passes, and no loop was laid)
        block = [{"n": "stone-wall", "t": "wall", "x": x + 0.5, "y": -489.5, "w": 1, "h": 1, "f": "npc-faction-1"}
                 for x in range(-450, -444)]                                  # (6 tiles: more than a pair passes)
        plans, _ = whole_loop(entities=block)
        north = next(p for p in plans if p["keep"]["side"] == "north")
        tiles = {tile(e) for e in north["entities"] if e["prototype"] in ("transport-belt", "underground-belt")}
        self.assertFalse(tiles & {(x, -490) for x in range(-450, -444)})
        self.assertIn((-443, -490), tiles)                                    # (back on its line after it)
        self.assertGreater(len([t for t in tiles if t[1] != -490]), 0)        # (round it, off the line)
        # (a turret behind it: no arm into a wall where its belt went round; on its hand loads; Codex's review)
        behind = [*ON_RING, gun(-447, -492)]
        plans, _ = whole_loop(entities=block, guards=behind)
        north = next(p for p in plans if p["keep"]["side"] == "north")
        self.assertNotIn([-447, -492], north["keep"]["turrets"])
        laid = {tile(e) for e in north["entities"] if e["prototype"] in ("transport-belt", "underground-belt")}
        for arm in (e for e in north["entities"] if e["prototype"] == "inserter" and tile(e)[1] == -491):
            self.assertIn((tile(arm)[0], -490), laid)                         # (every arm over a belt)

    def test_with_the_magazine_row_standing_the_loop_is_fed_from_it(self):
        plan = ammo_line.plan(ready(base=[ROW]), world(entities=[POLE, ROW_CHEST]))
        self.assertEqual(plan["keep"]["source"], "row")
        self.assertLessEqual(len(plan["entities"]), 900)
        self.assertFalse(any(st.get("manual_supplies") for st in plan["stages"]))
        out_arm = [e for e in plan["entities"] if e["prototype"] == "inserter"
                   and abs(e["position"]["x"] - ROW_CHEST["x"]) + abs(e["position"]["y"] - ROW_CHEST["y"]) == 1]
        self.assertEqual(len(out_arm), 1)

    def test_no_power_network_no_loop(self):
        with self.assertRaises(layout_planner.PlanError):
            ammo_line.plan(ready(), world())
        # (a pole on a network no generator feeds is none: D-21, the grown bus's 200 unpowered minutes)
        dead = world(entities=[{**POLE, "net": 7}])
        dead["live_nets"] = [1]
        with self.assertRaises(layout_planner.PlanError):
            ammo_line.plan(ready(), dead)


class OuterEdgeTests(unittest.TestCase):
    """A ring that is the plot's outer edges (npc-1's: north and west, the neighbours holding the rest): an open line
    along them, laid from its upstream end, its feed there."""
    PLOT = [-576, -576, -192, -192]

    def edge_site(self, base=()):
        walls = [{"purpose": "plot-wall", "box": [-576, -576, -192, -572],
                  "keep": {"ring": self.PLOT, "ring_gen": 1, "ring_side": s}} for s in ("north", "west")]
        return site(**{"base": [CORE, *walls, *base], "guards": [gun(x, -568) for x in (-500, -480)],
                       "recipes": RECIPES, "completed_purposes": ["steam-power"], "defense": ARMS})

    def test_an_open_line_from_its_upstream_end_its_feed_at_its_start_on_into_the_next_side(self):
        s = self.edge_site()
        ring = ammo_line._ring(s)
        self.assertEqual(ammo_line.sides_of(s, ring), ["west", "north"])     # (west runs north into the north side)
        self.assertEqual(ammo_line.what_is_due(s), "loop")
        pole = {**POLE, "x": -500.5, "y": -540.5}
        west = ammo_line.plan(s, world(entities=[pole]))
        self.assertEqual(west["keep"]["side"], "west")
        belts = [e for e in west["entities"] if e["prototype"] == "transport-belt"]
        start = max(belts, key=lambda e: e["position"]["y"])                 # (its upstream end: the south end)
        chest = next(e for e in west["entities"] if e["prototype"] == ammo_line.CHEST)
        self.assertLessEqual(abs(chest["position"]["y"] - start["position"]["y"]), 6)
        self.assertTrue(all(e["direction"] == NORTH for e in belts))
        north = ammo_line.plan(self.edge_site(base=[{"purpose": ammo_line.PURPOSE, "keep": west["keep"]}]),
                               world(entities=[pole]))
        self.assertEqual((north["keep"]["side"], len(north["keep"]["turrets"])), ("north", 2))
        first = min((e for e in north["entities"] if e["prototype"] == "transport-belt"),
                    key=lambda e: e["position"]["x"])
        top = min(belts, key=lambda e: e["position"]["y"])                   # (west's last belt runs into north's first)
        self.assertEqual((tile(top)[0], tile(top)[1] - 1), tile(first))
        self.assertFalse(any(e["prototype"] == ammo_line.CHEST for e in north["entities"]))


    def test_a_plot_edge_and_its_yard_band_on_one_line_are_one_side(self):
        # (Codex's review: NPC-7's south edge and the band beyond it; one replaced the other)
        from companion import perimeter
        s = self.edge_site()
        edges = perimeter.ring_edges(s, ammo_line._ring(s))
        north = next(e for e in edges if e.name == "north")
        band = perimeter.Edge("north", (north.plot[2], north.plot[1], north.plot[2] + 200, north.plot[3]),
                              north.hi + 1, north.hi + 150)
        with mock.patch.object(perimeter, "ring_edges", return_value=[*edges, band]):
            merged = ammo_line._edges_by_side(s, ammo_line._ring(s))["north"]
        self.assertEqual((merged.lo, merged.hi), (north.lo, north.hi + 150))


class ChooserTests(unittest.TestCase):
    def test_the_loop_comes_before_the_economy_once_the_waves_draw_on_the_ring_and_not_before(self):
        # (D-24: behind the opening's weighed goals the loop never came; death-world-waves-ring was overrun at wave 4
        # with none of its 21 turrets fed. Full turrets want none yet: stage-silo's 12 minutes, D-22)
        import tempfile
        from companion import blueprints as bp
        with tempfile.TemporaryDirectory() as folder:
            planner = cp.CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            self.assertEqual(planner.choose_purpose(ready()), ammo_line.PURPOSE)
            full = [gun(x, -492, ammo=20) for x in (-460, -440, -420)]
            self.assertNotEqual(planner.choose_purpose(ready(guards=full)), ammo_line.PURPOSE)
        # (on a normal map its turrets are kept full by hand: with iron to spare, the loop all the same, after the
        # economy's goals, never before them; D-24)
        rich = {**CORE, "capacity": {"makes": {"iron-plate": 3.0}}}
        s = site(**{**ready(guards=full), "base": [rich, *WALLS]})
        self.assertEqual(ammo_line.what_is_due(s), "loop")
        self.assertIsNone(ammo_line.what_is_due(s, pressed_only=True))
        # (and chosen so, before the weighed goals and reinforce, no attack on now: behind them it never came; Codex)
        with tempfile.TemporaryDirectory() as folder:
            planner = cp.CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            self.assertEqual(planner.choose_purpose(s), ammo_line.PURPOSE)
            self.assertNotEqual(cp.team_urgent({**s, "under_attack": True}), ammo_line.PURPOSE)

    def test_a_loop_begun_is_carried_on_before_reinforce_unless_attacked_now(self):
        # (D-24, late-combat: its north side laid by minute 8, then reinforce every turn, the other sides never laid)
        plans, keeps = whole_loop()
        full = [gun(x, -492, ammo=20) for x in (-460, -440, -420)]
        begun = ready(base=keeps[:1], guards=full)        # (full turrets, iron for no loop of its own: begun all the same)
        self.assertTrue(ammo_line.part_laid(begun))
        self.assertEqual(ammo_line.what_is_due(begun, pressed_only=True), "loop")
        self.assertEqual(cp.team_urgent(begun), ammo_line.PURPOSE)
        self.assertNotEqual(cp.team_urgent({**begun, "under_attack": True}), ammo_line.PURPOSE)
        self.assertFalse(ammo_line.part_laid(ready(guards=full)))                     # (none begun)
        self.assertFalse(ammo_line.part_laid(ready(base=keeps, guards=full)))         # (round its ring)


class AmmoTests(unittest.TestCase):
    def test_the_best_ammunition_it_can_come_by_not_the_best_researched(self):
        # (D-24, late-combat: its loop chest was to be filled with uranium rounds, no uranium; it stood empty)
        from companion import perimeter
        turret = {"name": "gun-turret", "kind": "ammo", "ammo_categories": ["bullet"]}
        takes = {"bullet": ["uranium-rounds-magazine", "piercing-rounds-magazine", "firearm-magazine"]}
        recipes = [{"name": "firearm-magazine", "ingredients": [{"name": "iron-plate", "amount": 4}]},
                   {"name": "piercing-rounds-magazine", "ingredients": [
                       {"name": "firearm-magazine", "amount": 1}, {"name": "steel-plate", "amount": 1},
                       {"name": "copper-plate", "amount": 5}]},
                   {"name": "uranium-rounds-magazine", "ingredients": [
                       {"name": "piercing-rounds-magazine", "amount": 1}, {"name": "uranium-238", "amount": 1}]}]
        plates = {"purpose": "smelting", "capacity": {"makes": {"iron-plate": 2, "copper-plate": 1, "steel-plate": 0.2}}}
        s = {"recipes": recipes, "base": [plates], "inventory": {}}
        self.assertEqual(perimeter._ammo_for(turret, takes, s), "piercing-rounds-magazine")
        self.assertEqual(perimeter._ammo_for(turret, takes, {**s, "inventory": {"uranium-rounds-magazine": 10}}),
                         "piercing-rounds-magazine")                                 # (carried only: what it makes first)
        self.assertEqual(perimeter._ammo_for(turret, takes, {**s, "base": [], "inventory": {"uranium-rounds-magazine": 10}}),
                         "uranium-rounds-magazine")                                  # (nothing made: what it carries)
        self.assertEqual(perimeter._ammo_for(turret, takes, {**s, "base": []}), "uranium-rounds-magazine")  # (none: best)
        self.assertEqual(perimeter._ammo_for(turret, takes), "uranium-rounds-magazine")
        # (uranium-238 from known ore is the centrifuge's, not made by hand: no uranium rounds of it)
        centrifuge = {"name": "uranium-processing", "handcraft": False, "ingredients": [{"name": "uranium-ore", "amount": 10}],
                      "products": [{"name": "uranium-238", "amount": 1}]}
        ore = {**s, "recipes": recipes + [centrifuge], "minable": ["uranium-ore"]}
        self.assertEqual(perimeter._ammo_for(turret, takes, ore), "piercing-rounds-magazine")


class KeptGroundTests(unittest.TestCase):
    def test_the_next_side_starts_ahead_of_the_last_ones_belt_in_ground_the_factory_keeps(self):
        # (D-24, late-combat: the east side's first tile, ahead of the north side's last belt, lay in the factory plan's
        # kept ground; it stayed taken and the side failed "by a corner" request after request)
        first = ammo_line.plan(ready(), world(entities=[POLE]))
        standing = [POLE] + [{"n": e["prototype"], "t": {"transport-belt": "transport-belt", "inserter": "inserter",
                                                         "small-electric-pole": "electric-pole"}.get(e["prototype"], "container"),
                              "x": e["position"]["x"], "y": e["position"]["y"], "w": 1, "h": 1,
                              "d": e.get("direction", 0), "f": "npc-faction-1"} for e in first["entities"]]
        kept = {**world(entities=standing), "reserved": [[-320, -495, -305, -485]]}   # (round the north-east corner)
        later = ready(base=[{"purpose": ammo_line.PURPOSE, "keep": first["keep"]}])
        nxt = ammo_line.plan(later, kept)
        self.assertEqual(nxt["ammo_line"]["kind"], "loop")
        self.assertNotEqual(nxt["keep"]["side"], first["keep"]["side"])


class LaterTests(unittest.TestCase):
    def _looped(self, **extra):
        plans, keeps = whole_loop(**extra)
        return plans[0], keeps

    def test_a_turret_added_to_the_ring_gets_its_arm_only(self):
        first, loop = self._looped()
        later = ready(base=[*loop], guards=ON_RING + [gun(-380, -492)])
        self.assertEqual(ammo_line.what_is_due(later), "arms")
        self.assertEqual(cp.team_urgent(later), ammo_line.PURPOSE)            # (a few inserters: before reinforce)
        standing = [{"n": e["prototype"], "t": "transport-belt", "x": e["position"]["x"], "y": e["position"]["y"],
                     "w": 1, "h": 1, "d": e["direction"], "f": "npc-faction-1"}
                    for e in first["entities"] if e["prototype"] == "transport-belt"]   # (its loop standing)
        arms = ammo_line.plan(later, world(entities=[POLE, *standing]))
        self.assertEqual((arms["ammo_line"]["kind"], arms["ammo_line"]["turrets"]), ("arms", 1))
        self.assertEqual(collections.Counter(e["prototype"] for e in arms["entities"])["transport-belt"], 0)
        done = ready(base=[*loop, {"purpose": ammo_line.PURPOSE, "keep": arms["keep"]}],
                     guards=ON_RING + [gun(-380, -492)])
        self.assertIsNone(ammo_line.what_is_due(done))

    def test_its_chest_gets_a_belt_from_the_row_once_that_stands_and_then_nothing_more(self):
        first, loop = self._looped()
        later = ready(base=[*loop, ROW])
        self.assertEqual(ammo_line.what_is_due(later), "link")
        link = ammo_line.plan(later, world(entities=[POLE, ROW_CHEST]))
        self.assertEqual((link["ammo_line"]["kind"], link["keep"]["source"]), ("link", "row"))
        head = first["keep"]["head"][0]
        into = [e for e in link["entities"] if e["prototype"] == "inserter"
                and abs(e["position"]["x"] - head[0]) + abs(e["position"]["y"] - head[1]) == 1]
        self.assertEqual(len(into), 1)
        done = ready(base=[*loop, ROW, {"purpose": ammo_line.PURPOSE, "keep": link["keep"]}])
        self.assertIsNone(ammo_line.what_is_due(done))

    def test_the_planner_plans_it_and_says_so(self):
        import tempfile
        from companion import blueprints as bp
        logs = []
        with tempfile.TemporaryDirectory() as folder:
            planner = cp.CatalogPlanner(bp.Catalog(folder), log=logs.append)
            planner.world = lambda _: world(entities=[POLE])
            plan = planner._ammo_line(ready(), "1:9")
        self.assertEqual(plan["purpose"], ammo_line.PURPOSE)
        self.assertTrue(any("ammo loop: 3 turrets" in line for line in logs), logs)


class GameTests(unittest.TestCase):
    """In the game: a turret its line's inserter feeds is not loaded by hand; one whose line is dry is."""

    def runtime(self):
        import test_defense_supply
        return test_defense_supply.runtime()

    def test_a_turret_its_line_feeds_is_not_loaded_by_hand_and_a_dry_line_s_is(self):
        lua = self.runtime()
        lua.execute("""
            fedturret = turret('gun-turret', 10, 0, 0)
            line = {}
            belt = {valid=true, type='transport-belt', get_max_transport_line_index=function() return 2 end,
                    get_transport_line=function() return {get_item_count=function() return line.n or 0 end} end}
            arm = {valid=true, type='inserter', drop_target=fedturret, pickup_target=belt, active=true}
            npc.fed_stores = {tick=game and game.tick or 0, items={}, stores={}, drilled={},
                              inserted={[fedturret.unit_number]={arm}}}
            DS.FED_CHECK = 10 ^ 9
            line.n = 5""")
        self.assertIsNone(lua.eval("(due())"))                            # (its belt carries magazines)
        lua.execute("line.n = 0")
        self.assertEqual(lua.eval("(due()).target.unit_number"), lua.eval("fedturret.unit_number"))   # (dry: by hand)

    def test_its_keep_carries_the_line_and_the_row_through_the_game(self):
        lua = self.runtime()
        k = lua.eval("DS.keep_copy({turrets={{-410, -430}, {-396, -430}}, ammo='firearm-magazine', source='chest', "
                     "head={{-412.5, -427.5}}})")
        self.assertEqual([list(p.values()) for p in k.turrets.values()], [[-410, -430], [-396, -430]])
        self.assertEqual((k.ammo, k.source, list(k.head[1].values())), ("firearm-magazine", "chest", [-412.5, -427.5]))
        r = lua.eval("DS.keep_copy({item='firearm-magazine', outputs={{-375.5, -410.5}}})")
        self.assertEqual((r.item, list(r.outputs[1].values())), ("firearm-magazine", [-375.5, -410.5]))
        self.assertIsNone(lua.eval("DS.keep_copy({item='x y'})"))          # (nothing of note: none)
        many = lua.eval("DS.keep_copy({loop={-500, -500, -300, -300}, turrets=(function() local t = {} "
                        "for i = 1, 60 do t[i] = {i, -492} end return t end)()})")
        self.assertEqual(list(many.loop.values()), [-500, -500, -300, -300])
        self.assertEqual(len(many.turrets), 60)                               # (a ring's turrets: more than 32)


if __name__ == "__main__":
    unittest.main()
