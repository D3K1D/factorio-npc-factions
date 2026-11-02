"""The nuclear plant's fuel chain (companion/uranium_plant.py), and nuclear sizing on a real mod set's facts."""

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

from companion import nuclear, nuclear_plant, rates, uranium_plant  # noqa: E402
from companion.modules import EAST, NORTH, Placed, WEST  # noqa: E402
import test_nuclear  # noqa: E402

# The game's facts on 2.1 with the user's mods (read live: Cerys, Maraxsis, Space Age; uranium's resource category
# added by hand, as game_facts.lua now exports it), and the optimizer's fuel cell and sulfuric acid blocks as the
# library imports them.
FACTS = json.loads((ROOT / "tests" / "fixtures" / "game-facts-2.1.json").read_text(encoding="utf-8"))
BLOCKS = json.loads((ROOT / "tests" / "fixtures" / "uranium-blocks.json").read_text(encoding="utf-8"))
READY = ("nuclear-reactor", "heat-exchanger", "steam-turbine", "heat-pipe", "centrifuge", "electric-mining-drill",
         "offshore-pump", "chemical-plant", "assembling-machine-2")
URANIUM = (-258, -476)   # a patch in the oil-practice map's free ground


def facts(ready=READY):
    out = json.loads(json.dumps(FACTS))
    for name in ready:
        out["entities"][name]["ready"] = True
    return out


def recipes(without=()):
    """The fuel recipes, and one making each building the blocks hold (all researched)."""
    pieces = {e["prototype"] for b in BLOCKS.values() for e in b["entities"]} - set(without)
    return test_nuclear.RECIPES + [{"name": "sulfuric-acid"}] + [{"name": n, "products": [{"name": n, "amount": 1}]}
                                                                 for n in sorted(pieces | {"splitter", "sulfur",
                                                                                           "iron-plate"})]


SOURCES = (("sulfur", "chemical-plant", (-246, -416)), ("iron-plate", "stone-furnace", (-246, -402)))


def sources(world):
    """A sulfur plant and an iron furnace in the map's free ground, each with an inserter putting its product on a
    belt running east (what the fuel chain taps)."""
    f = rates.of({"game_facts": facts()})
    for item, machine, (x, y) in SOURCES:
        w = f.entities[machine]["width"]
        world["entities"].append({"n": machine, "x": x + w / 2, "y": y + w / 2, "d": 0, "f": world["force"],
                                  "t": f.entities[machine].get("type"), "w": w, "h": w, "r": item})
        world["entities"].append({"n": "inserter", "x": x + 0.5, "y": y + w + 0.5, "d": 0, "f": world["force"],
                                  "t": "inserter", "w": 1, "h": 1})   # (it faces where it picks up: the machine)
        for k in range(-4, 12):
            world["entities"].append({"n": "transport-belt", "x": x + k + 0.5, "y": y + w + 1.5, "d": 4,
                                      "f": world["force"], "t": "transport-belt", "w": 1, "h": 1})


def built_world():
    """The oil-practice map with the nuclear plant built (planned, then entered as the map would show it) and a
    uranium patch near it."""
    fixture = json.loads((ROOT / "tests" / "fixtures" / "oil-practice-field.json").read_text(encoding="utf-8"))
    site = {**fixture["site"], "game_facts": facts(), "recipes": recipes()}
    world = json.loads(json.dumps(fixture["world"]))
    plant = nuclear_plant.plan(site, world)
    o = site.get("origin") or {"x": 0, "y": 0}
    for e in plant["entities"]:
        world["entities"].append({"n": e["prototype"], "x": e["position"]["x"] + o["x"],
                                  "y": e["position"]["y"] + o["y"], "d": e["direction"], "f": world["force"],
                                  "t": e["prototype"]})
    world["resources"]["uranium-ore"] = [[y, URANIUM[0], URANIUM[0] + 24] for y in range(URANIUM[1], URANIUM[1] + 24)]
    sources(world)
    return site, world


class SizingOnTheModSetTests(unittest.TestCase):
    def test_the_pieces_that_join_are_chosen_among_a_mod_sets(self):
        for ready in (READY, ()):   # (researched, or nothing ready yet)
            size = nuclear.sizing({"game_facts": facts(ready), "recipes": test_nuclear.RECIPES}, 2)
            self.assertNotIn(size["reactor"], ("chcs-solar-power-tower",))   # (heated by a fluid, not fuel cells)
            self.assertNotEqual(size["turbine"], "maraxsis-oversized-steam-turbine")   # (another steam)
            self.assertEqual(size["heat_exchanger"], "heat-exchanger")
        size = nuclear.sizing({"game_facts": facts(), "recipes": test_nuclear.RECIPES}, 2)
        self.assertEqual((size["reactor"], size["turbine"]), ("nuclear-reactor", "steam-turbine"))

    def test_uranium_is_mined_by_a_drill_not_a_pumpjack(self):
        got = nuclear.mining({"game_facts": facts(), "recipes": []}, 2.857, width=3)
        self.assertEqual(got["drill"], "electric-mining-drill")
        self.assertEqual(got["drills"], 12)
        self.assertAlmostEqual(got["fluid_per_second"], 3.0)   # (one acid an ore at 2.1's rate)
        dry = facts()   # (a faster 3x3 mod drill with no fluid box cannot take the acid)
        dry["entities"]["dry-drill"] = {"kind": "drill", "width": 3, "height": 3, "speed": 2, "ready": True,
                                        "categories": ["basic-solid"]}
        self.assertEqual(nuclear.mining({"game_facts": dry, "recipes": []}, 2.857, width=3)["drill"],
                         "electric-mining-drill")


class BlockTests(unittest.TestCase):
    def test_the_ready_blocks_and_their_ports(self):
        builds = list(BLOCKS.values())
        acid = uranium_plant.ready(builds, uranium_plant.ACID)
        cells = uranium_plant.ready(builds, uranium_plant.CELL)
        self.assertIs(acid, BLOCKS["sulfuric_acid"])
        self.assertIs(cells, BLOCKS["fuel_cells"])
        self.assertIsNone(uranium_plant.ready(builds, uranium_plant.CELL, {"recipes": [{"name": "sulfuric-acid"}]}))
        self.assertIs(uranium_plant.ready(builds, uranium_plant.CELL, {"recipes": recipes()}), cells)
        # (a piece not to be had yet, the assembler say: not this block)
        self.assertIsNone(uranium_plant.ready(builds, uranium_plant.CELL,
                                              {"recipes": recipes(without=("assembling-machine-2",))}))
        # (a block without the ore port the drills' belt ends at: not this chain's)
        other = json.loads(json.dumps(cells))
        other["import"]["ports"] = [p for p in other["import"]["ports"] if p["item"] != "uranium-ore"]
        self.assertIsNone(uranium_plant.ready([other], uranium_plant.CELL))
        water = next(p for p in acid["import"]["ports"] if p["item"] == "water")
        # (the sidecar says east; its pipe-to-ground faces north, so the water joins above it)
        self.assertEqual(uranium_plant.port(acid, water)[1:], ((7, 0), (0, -1)))
        out = next(p for p in acid["import"]["ports"] if p["item"] == "sulfuric-acid")
        self.assertEqual(uranium_plant.port(acid, out)[1], (9, 7))


class FuelChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site, cls.world = built_world()
        cls.plan = uranium_plant.plan(cls.site, cls.world, list(BLOCKS.values()))

    def placed(self):
        o = self.site.get("origin") or {"x": 0, "y": 0}
        return [Placed(e["prototype"], e["position"]["x"] + o["x"], e["position"]["y"] + o["y"], e["direction"])
                for e in self.plan["entities"]]

    def test_drills_blocks_and_their_inputs_by_belt(self):
        got = Counter(e["prototype"] for e in self.plan["entities"])
        self.assertEqual(got["electric-mining-drill"], 12)
        self.assertEqual((got["centrifuge"], got["assembling-machine-2"], got["chemical-plant"]), (4, 1, 1))
        self.assertEqual(self.plan["stages"][0]["manual_supplies"], [])   # (no chest filled by hand)
        self.assertEqual(sorted(self.plan["taps"]), ["iron-plate", "iron-plate", "sulfur"])
        self.assertEqual(got["splitter"], 3)
        self.assertEqual(self.plan["reactor_feeds"], 2)

    def test_no_belt_to_tap_is_no_plan(self):
        from companion import layout_planner
        import copy
        world = copy.deepcopy(self.world)
        world["entities"] = [e for e in world["entities"] if e.get("r") != "sulfur"]
        with self.assertRaisesRegex(layout_planner.PlanError, "no standing belt of sulfur"):
            uranium_plant.plan(self.site, world, list(BLOCKS.values()))

    def test_nothing_overlaps_the_plant_or_itself(self):
        f = rates.of(self.site)
        seen = {}
        for e in self.world["entities"]:
            if e.get("n") in f.entities and e.get("t") not in ("tree", "simple-entity", "cliff"):
                for t in uranium_plant.tiles(f, Placed(e["n"], e["x"], e["y"], int(e.get("d") or 0))):
                    seen[t] = e["n"]
        for e in self.placed():
            if e.name == "offshore-pump":
                continue
            for t in uranium_plant.tiles(f, e):
                if e.name == "splitter" and seen.get(t) == "transport-belt":
                    continue   # (a tap's splitter takes the tapped belt's tile)
                self.assertNotIn(t, seen, (e.name, seen.get(t)))
                seen[t] = e.name

    def test_every_drill_has_acid_and_the_cells_reach_each_fuel_chest(self):
        f = rates.of(self.site)
        placed = self.placed()
        pipes = {(math.floor(e.x), math.floor(e.y)) for e in placed if e.name in ("pipe", "pipe-to-ground")}
        conns = [c for b in f.entities["electric-mining-drill"]["fluid"] for c in b["c"]]
        from companion import nuclear_cell
        for d in [e for e in placed if e.name == "electric-mining-drill"]:
            reach = {nuclear_cell.connection((d.x, d.y), d.direction, c)[1] for c in conns}
            self.assertTrue(reach & pipes, (d.x, d.y))
        belts = {(math.floor(e.x), math.floor(e.y)) for e in placed if e.name == "transport-belt"}
        inserters = {(math.floor(e.x), math.floor(e.y)): e.direction for e in placed if e.name == "inserter"}
        for feed in uranium_plant.feeds(self.world, f):
            self.assertIn(feed["belt"], belts)
            self.assertEqual(inserters.get(feed["inserter"]), EAST if feed["side"] > 0 else WEST)

    def test_the_goal_follows_the_plant(self):
        from companion.catalog_planner import PURPOSES, available, purpose_options
        recipes = [{"name": n} for n in PURPOSES["nuclear-fuel"]["requires_recipes"]]
        site = {"request_id": "1:50", "purpose": "choose", "recipes": recipes, "resources": [],
                "known_patches": [{"resource": "uranium-ore", "tiles": 400}],
                "completed_purposes": ["steam-power", "power-plant", "oil-processing"], "origin": {"x": 0, "y": 0}}
        self.assertFalse(available("nuclear-fuel", site))               # (no plant yet)
        built = {**site, "completed_purposes": site["completed_purposes"] + ["nuclear-power"]}
        self.assertTrue(available("nuclear-fuel", built))
        no_oil = [p for p in built["completed_purposes"] if p != "oil-processing"]
        self.assertFalse(available("nuclear-fuel", {**built, "completed_purposes": no_oil}))   # (its sulfur's source)
        self.assertEqual(purpose_options(built)[0]["id"], "nuclear-fuel")
        self.assertFalse(available("nuclear-fuel", {**built, "completed_purposes": built["completed_purposes"]
                                                    + ["nuclear-fuel"]}))


if __name__ == "__main__":
    unittest.main()
