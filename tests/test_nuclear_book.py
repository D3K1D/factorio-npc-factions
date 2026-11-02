"""The user's nuclear book stamped (companion/nuclear_book.py, nuclear_plant.book_plan), and the fuel chain fed into it
by belt (companion/uranium_plant.py)."""

from __future__ import annotations

import json
import math
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import blueprint_packs, layout_planner, nuclear_book, nuclear_cell, nuclear_plant, rates  # noqa: E402
from companion import uranium_plant  # noqa: E402
from companion.modules import Placed  # noqa: E402
import test_uranium_plant as tu  # noqa: E402

# Kingdud's "Space Age: Tileable Nuclear Setups, Peak and Base Load" (Factorio Prints -OA4gcfXynl7cftYwtxE), the user's
# pick, as saved in their Blueprints\nuclear folder.
PACKS = ROOT / "tests" / "fixtures" / "packs"
BOOK = nuclear_book.load(PACKS)
MAP = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))


PIECES = ("nuclear-reactor", "heat-exchanger", "steam-turbine", "heat-pipe", "pipe", "pipe-to-ground", "substation",
          "big-electric-pole", "storage-tank", "transport-belt", "underground-belt", "inserter", "fast-inserter",
          "iron-chest", "small-electric-pole", "offshore-pump")


def site(without=()):
    recipes = tu.recipes() + [{"name": n, "products": [{"name": n, "amount": 1}]}
                              for n in ("splitter", "sulfur", "iron-plate") + PIECES if n not in without]
    return {"game_facts": tu.facts(), "recipes": recipes, "origin": {"x": 0, "y": 0}, "request_id": "1:1"}


def add(world, facts, name, x, y, d=0, recipe=None):
    f = facts.entities.get(name) or {}
    row = {"n": name, "x": x, "y": y, "d": d, "f": world["force"], "t": f.get("type") or name,
           "w": f.get("width", 1), "h": f.get("height", 1)}
    if recipe:
        row["r"] = recipe
    world["entities"].append(row)


def build(world, facts, plan):
    for e in plan["entities"]:
        add(world, facts, e["prototype"], e["position"]["x"], e["position"]["y"], e["direction"])


def sources(world, facts):
    """A sulfur plant and an iron furnace, each with an inserter putting its product on a belt running east."""
    for item, machine, (x, y) in (("sulfur", "chemical-plant", (110, 30)), ("iron-plate", "stone-furnace", (110, 50))):
        if machine == "chemical-plant":
            add(world, facts, machine, x + 0.5, y + 0.5, 0, item)
        else:
            add(world, facts, machine, x + 1, y + 1, 0, item)
        add(world, facts, "inserter", x + 0.5, y + 2.5, 0)   # (it faces where it picks up: the machine, north)
        for k in range(-4, 12):
            add(world, facts, "transport-belt", x + k + 0.5, y + 3.5, 4)


@unittest.skipUnless(BOOK, "the nuclear book fixture is not here (third-party blueprints: not in every checkout)")
class PieceTests(unittest.TestCase):
    def test_the_book_grows_from_2c_to_8c(self):
        self.assertEqual([n for n, _ in nuclear_book.tiers(BOOK)], [1, 2, 4, 8])
        self.assertTrue(all("end" not in l.lower() for n in (1, 2, 4, 8) for l in nuclear_book.labels(BOOK, n)))

    def test_the_2c_piece_its_ports_and_spent_cells(self):
        s = site()
        facts = rates.of(s)
        p = nuclear_book.piece(s, BOOK["2c, 160MW, Peak Load"])
        names = Counter(e.name for e in p["book"])
        self.assertEqual((names["nuclear-reactor"], names["heat-exchanger"], names["steam-turbine"]), (2, 16, 28))
        self.assertNotIn("decider-combinator", names)      # (a plan carries no wires)
        self.assertEqual(len(p["lines"]), 1)                # (one fuel belt under both reactors)
        self.assertEqual(len(p["water"]), 2)                # (a pipe-to-ground each side, paired outside the piece)
        self.assertEqual(nuclear_book.heat_problems(facts, p["book"]), [])
        added = [e for e in p["entities"] if e not in p["book"]]
        spent = [e for e in added if e.name == "inserter"]
        self.assertEqual(len(spent), 2)                     # (one a reactor, picking from it)
        body = {t for r in p["reactors"] for t in nuclear_book._tiles(facts, r)}
        for e in spent:
            dx, dy = nuclear_book.STEP[e.direction]
            self.assertIn((math.floor(e.x) + dx, math.floor(e.y) + dy), body)
        self.assertEqual(Counter(e.name for e in added if e.name != "inserter"),
                         Counter({"transport-belt": 2, "fast-inserter": 1, "iron-chest": 1}))
        line = p["lines"][0]
        self.assertNotIn(line["feed"], p["tiles"])
        self.assertEqual(nuclear_book.STEP[line["face"]], (-line["side"][0], -line["side"][1]))

    def test_its_tanks_left_out_close_up_and_still_join(self):
        s = site()
        facts = rates.of(s)
        full = nuclear_book.piece(s, BOOK["2c, 160MW, Peak Load"])
        short = nuclear_book.piece(s, BOOK["2c, 160MW, Peak Load"], tanks=False)
        self.assertNotIn("storage-tank", {e.name for e in short["book"]})
        height = lambda p: max(y for _, y in p["tiles"]) - min(y for _, y in p["tiles"])   # noqa: E731
        self.assertLess(height(short), height(full) - 40)
        self.assertEqual(Counter(e.name for e in short["book"])["steam-turbine"], 28)
        self.assertEqual(nuclear_book.heat_problems(facts, short["book"]), [])
        # the steam still runs from the exchangers' pipes to the turbines': one pipe network joins them
        pipes = {(math.floor(e.x), math.floor(e.y)) for e in short["book"] if e.name == "pipe"}
        turbines = [e for e in short["book"] if e.name == "steam-turbine"]
        top = min(t[1] for e in turbines for t in nuclear_book._tiles(facts, e))
        self.assertTrue(any((x, top - 1) in pipes for x, _ in nuclear_book._tiles(facts, turbines[0])))

    def test_a_missing_heat_pipe_is_found(self):
        s = site()
        facts = rates.of(s)
        p = nuclear_book.piece(s, BOOK["2c, 160MW, Peak Load"])
        cut = [e for e in p["book"] if not (e.name == "heat-pipe" and e.y > p["reactors"][0].y + 3)]
        self.assertTrue(nuclear_book.heat_problems(facts, cut))

    def test_which_pieces_hold_which(self):
        s = site()
        facts = rates.of(s)
        two, four = (nuclear_book.piece(s, BOOK[l])["book"] for l in ("2c, 160MW, Peak Load", "4c, 480MW, peak load"))
        self.assertIsNotNone(nuclear_book.align(facts, two, four))
        base = nuclear_book.piece(s, BOOK["8c, 1.3Gw, base load"])["book"]
        self.assertIsNone(nuclear_book.align(facts, four, base))   # (a peak load piece grows into the peak load one)


@unittest.skipUnless(BOOK, "the nuclear book fixture is not here (third-party blueprints: not in every checkout)")
class StampTests(unittest.TestCase):
    def test_2c_by_the_shore_then_4c_in_place_then_no_further(self):
        s, world = site(), json.loads(json.dumps(MAP))
        facts = rates.of(s)
        first = nuclear_plant.plan(s, world, BOOK)
        got = Counter(e["prototype"] for e in first["entities"])
        self.assertEqual((got["nuclear-reactor"], got["steam-turbine"]), (2, 28))
        self.assertEqual(got["offshore-pump"], 2)   # (its own: 16 exchangers boil 1650 water a second, a pump 1200)
        pumps = [(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])) for e in first["entities"]
                 if e["prototype"] == "offshore-pump"]
        self.assertGreater(max(abs(pumps[0][0] - pumps[1][0]), abs(pumps[0][1] - pumps[1][1])),
                           nuclear_plant.PUMP_GAP)   # (two side by side: the second was refused)
        sinks = [e for e in first["entities"] if e.get("filters")]
        self.assertEqual([(e["prototype"], e["filters"][0]["name"]) for e in sinks],
                         [("fast-inserter", "depleted-uranium-fuel-cell")])
        self.assertTrue(blueprint_packs.PLACEABLE >= {"reactor", "heat-pipe", "storage-tank"})
        # nothing on what stands
        before = {}
        for e in world["entities"]:
            if e.get("t") not in ("tree", "simple-entity"):
                for t in nuclear_cell.tiles_of((e["x"], e["y"]), e.get("w", 1), e.get("h", 1), e.get("d") or 0):
                    before[t] = e["n"]
        for e in first["entities"]:
            if e["prototype"] == "offshore-pump":
                continue
            for t in nuclear_book._tiles(facts, Placed(e["prototype"], e["position"]["x"], e["position"]["y"],
                                                       e["direction"])):
                self.assertNotIn(t, before, e["prototype"])
        build(world, facts, first)
        self.assertEqual(len(nuclear_plant.feeds(s, world, BOOK)), 1)
        second = nuclear_plant.plan(s, world, BOOK)
        self.assertIn("4c", second["notes"][0])
        self.assertEqual(Counter(e["prototype"] for e in second["entities"])["nuclear-reactor"], 2)   # (only the new)
        build(world, facts, second)
        self.assertEqual(len(nuclear_plant.feeds(s, world, BOOK)), 2)
        with self.assertRaisesRegex(layout_planner.PlanError, "biggest piece"):
            nuclear_plant.plan(s, world, BOOK)

    def test_a_piece_not_to_be_made_yet_is_no_plant(self):
        s, world = site(without=("substation",)), json.loads(json.dumps(MAP))
        with self.assertRaisesRegex(layout_planner.PlanError, "needs research first: substation"):
            nuclear_plant.plan(s, world, BOOK)

    def test_every_inserter_it_adds_has_power(self):
        from companion.uranium_plant import SUPPLY
        s, world = site(), json.loads(json.dumps(MAP))
        plan = nuclear_plant.plan(s, world, BOOK)
        poles = [(e["position"]["x"], e["position"]["y"], SUPPLY[e["prototype"]]) for e in plan["entities"]
                 if e["prototype"] in SUPPLY]
        for e in plan["entities"]:
            if e["prototype"].endswith("inserter"):
                x, y = math.floor(e["position"]["x"]) + 0.5, math.floor(e["position"]["y"]) + 0.5
                self.assertTrue(any(abs(x - px) < r + 0.5 and abs(y - py) < r + 0.5 for px, py, r in poles), (x, y))

    def test_without_a_book_the_generated_plant(self):
        s, world = site(), json.loads(json.dumps(MAP))
        plan = nuclear_plant.plan(s, world, {})
        self.assertEqual(plan["source"], "nuclear-plant")


@unittest.skipUnless(BOOK, "the nuclear book fixture is not here (third-party blueprints: not in every checkout)")
class FedByBeltTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site, cls.world = site(), json.loads(json.dumps(MAP))
        facts = rates.of(cls.site)
        build(cls.world, facts, nuclear_plant.plan(cls.site, cls.world, BOOK))
        cls.world["resources"]["uranium-ore"] = [[y, 60, 84] for y in range(60, 84)]
        sources(cls.world, facts)
        cls.feeds = nuclear_plant.feeds(cls.site, cls.world, BOOK)
        cls.plan = uranium_plant.plan(cls.site, cls.world, list(tu.BLOCKS.values()), BOOK)

    def test_iron_and_sulfur_come_by_belt_not_by_hand(self):
        self.assertEqual(self.plan["stages"][0]["manual_supplies"], [])
        self.assertEqual(sorted(self.plan["taps"]), ["iron-plate", "iron-plate", "sulfur"])
        self.assertEqual(Counter(e["prototype"] for e in self.plan["entities"])["splitter"], 3)

    def test_the_cells_belt_sideloads_the_fuel_belt(self):
        belts = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])): e["direction"]
                 for e in self.plan["entities"] if e["prototype"] == "transport-belt"}
        f = self.feeds[0]
        self.assertEqual(belts.get(f["feed"]), f["face"])

    def test_the_lines_that_carry_an_item(self):
        found = uranium_plant.made_lines(self.world, self.site, "sulfur")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].tiles[0][:2], (110, 33))   # (where its inserter drops it)
        self.assertEqual(uranium_plant.made_lines(self.world, self.site, "copper-cable"), [])


if __name__ == "__main__":
    unittest.main()
