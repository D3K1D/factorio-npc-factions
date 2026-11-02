"""The oil plant built to its ratios (companion/oil_plant.py): sizing from the game's recipes, a block whose pipes all
route, and its plan on a real map (the oil practice world's field, under ground kept for the bus)."""

from __future__ import annotations

import collections
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import oil_plant as op  # noqa: E402
from companion.layout_planner import PlanError  # noqa: E402
from companion.rocket_plan import _tiles  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "oil-practice-field.json").read_text(encoding="utf-8"))


def recipe(name, energy, ingredients, products):
    return {"name": name, "energy": energy, "ingredients": [{"name": n, "amount": a} for n, a in ingredients],
            "products": [{"name": n, "amount": a} for n, a in products]}


VANILLA = [
    recipe("basic-oil-processing", 5, [("crude-oil", 100)], [("petroleum-gas", 45)]),
    recipe("advanced-oil-processing", 5, [("water", 50), ("crude-oil", 100)],
           [("heavy-oil", 25), ("light-oil", 45), ("petroleum-gas", 55)]),
    recipe("heavy-oil-cracking", 2, [("water", 30), ("heavy-oil", 40)], [("light-oil", 30)]),
    recipe("light-oil-cracking", 2, [("water", 30), ("light-oil", 30)], [("petroleum-gas", 20)]),
    recipe("plastic-bar", 1, [("coal", 1), ("petroleum-gas", 20)], [("plastic-bar", 2)]),
    recipe("sulfur", 1, [("water", 30), ("petroleum-gas", 30)], [("sulfur", 2)]),
]


def mixed(block):
    """Where two fluids join in a block: a plain pipe beside a plain pipe of another fluid, or at the open end of
    another fluid's pipe-to-ground (the side it faces)."""
    from companion.pipes import STEPS
    fluid = block["pipes"]
    plain = {(math.floor(e.x), math.floor(e.y)) for e in block["entities"] if e.name == "pipe"}
    faces = {(math.floor(e.x), math.floor(e.y)): e.direction for e in block["entities"] if e.name == "pipe-to-ground"}
    out = []
    for t in sorted(plain):
        for dx, dy in STEPS.values():
            n = (t[0] + dx, t[1] + dy)
            if n in plain and n > t and fluid.get(n) != fluid.get(t):
                out.append((t, n))
            if n in faces and STEPS[faces[n]] == (-dx, -dy) and fluid.get(n) != fluid.get(t):
                out.append((t, n))
    return out


def site(names=None):
    rows = [r for r in VANILLA if names is None or r["name"] in names]
    return {"recipes": rows, "game_facts": {"entities": {"oil-refinery": {"kind": "crafter", "speed": 1},
                                                          "chemical-plant": {"kind": "crafter", "speed": 1}}}}


class SizingTests(unittest.TestCase):
    def test_advanced_processing_with_cracking_is_sized_to_what_the_plants_draw(self):
        size = op.sizing(site(), {"plastic-bar": 2, "sulfur": 1})
        self.assertEqual(size["process"], "advanced-oil-processing")
        self.assertEqual(size["plants"], {"plastic-bar": 1, "sulfur": 1})
        # 50 gas/s drawn; a refinery cracked all the way gives 11 + (9 + 5 * 30/40) * 20/30 = 19.5 gas/s
        self.assertEqual(size["refineries"], 3)
        self.assertAlmostEqual(size["gas_made"], 58.5)
        self.assertGreaterEqual(size["gas_made"], size["gas_drawn"])
        # all heavy oil cracked (3 * 5 = 15/s, 20 a plant), all light (3 * 12.75/s, 15 a plant)
        self.assertEqual(size["cracking"], {"heavy-oil-cracking": 1, "light-oil-cracking": 3})
        self.assertAlmostEqual(size["crude"], 60.0)

    def test_without_both_crackings_the_refinery_runs_basic_processing(self):
        for names in ({"basic-oil-processing", "advanced-oil-processing", "plastic-bar"},
                      {"basic-oil-processing", "advanced-oil-processing", "heavy-oil-cracking", "plastic-bar"}):
            size = op.sizing(site(names), {"plastic-bar": 2})
            self.assertEqual(size["process"], "basic-oil-processing")   # (heavy or light oil would back up)
            self.assertEqual(size["cracking"], {})
        self.assertIsNone(op.sizing(site({"plastic-bar", "sulfur"})))      # (no process at all)

    def test_only_unlocked_products_get_plants(self):
        size = op.sizing(site({"basic-oil-processing", "plastic-bar"}))
        self.assertEqual(set(size["plants"]), {"plastic-bar"})

    def test_a_big_want_is_held_to_the_refineries_one_plant_may_have(self):
        size = op.sizing(site(), {"plastic-bar": 40, "sulfur": 40})
        self.assertLessEqual(size["refineries"], op.MAX_REFINERIES)


class BlockTests(unittest.TestCase):
    def test_every_size_routes_with_nothing_on_top_of_anything(self):
        for want in ({"plastic-bar": 2, "sulfur": 1}, {"plastic-bar": 1}, {"sulfur": 3}, {"plastic-bar": 6, "sulfur": 6}):
            size = op.sizing(site(), want)
            block = op.block(site(), size)
            seen = collections.Counter(t for e in block["entities"] for t in _tiles(e.name, e.x, e.y, e.direction))
            self.assertEqual([t for t, n in seen.items() if n > 1], [], want)
            plants = [e for e in block["entities"] if e.name == "chemical-plant"]
            self.assertEqual(len(plants), sum(size["plants"].values()) + sum(size["cracking"].values()))
            self.assertEqual(mixed(block), [], want)

    def test_the_layout_that_mixed_heavy_and_light_oil_is_found(self):
        # (oil-practice-bus 2026-10-31: a light oil pipe beside the heavy cracker's heavy oil inlet)
        block = {"pipes": {(33, 2): "heavy-oil", (34, 2): "light-oil"},
                 "entities": [op.Placed("pipe", 33.5, 2.5), op.Placed("pipe", 34.5, 2.5)]}
        self.assertEqual(mixed(block), [((33, 2), (34, 2))])

    def test_each_solid_product_leaves_by_inserter_onto_a_belt_into_a_chest(self):
        block = op.block(site(), op.sizing(site(), {"plastic-bar": 4, "sulfur": 2}))
        by_tile = {(math.floor(e.x), math.floor(e.y)): e for e in block["entities"]}
        for e in block["entities"]:
            if e.name == "chemical-plant" and e.recipe in ("plastic-bar", "sulfur"):
                above = by_tile.get((math.floor(e.x), math.floor(e.y) - 2))
                self.assertEqual((above.name, above.direction), ("inserter", op.SOUTH))   # (picks from the plant)
                belt = by_tile.get((math.floor(e.x), math.floor(e.y) - 3))
                self.assertEqual(belt.name, "transport-belt")
        self.assertEqual(sorted(k for k in block["ports"] if k in ("plastic-bar", "sulfur")), ["plastic-bar", "sulfur"])
        for item in ("plastic-bar", "sulfur"):
            self.assertEqual(by_tile[block["ports"][item]].name, "iron-chest")


class ReadyTests(unittest.TestCase):
    def build(self, mirror=False):
        ports = [{"way": "in", "kind": "fluid", "item": "crude-oil", "rate": 18, "edge": "north", "outside": [5, 0]},
                 {"way": "in", "kind": "fluid", "item": "water", "rate": 31, "edge": "west", "outside": [0, 5]},
                 {"way": "out", "kind": "belt", "item": "plastic-bar", "rate": 1.0, "edge": "south", "outside": [5, 9]}]
        return {"import": {"ports_from": "sidecar", "ports": ports}, "recipes_needed": ["plastic-bar"], "size": [10, 10],
                "entities": [{"prototype": "oil-refinery", "position": {"x": 5.5, "y": 4.5}, "direction": 0,
                              "recipe": "advanced-oil-processing", **({"mirror": True} if mirror else {})}]}

    def test_a_ready_block_is_used_only_unmirrored_for_now(self):
        here = {**site(), "inventory": {"oil-refinery": 2}}
        plastic = {"plastic-bar": 2}
        self.assertTrue(op.ready_fits(here, self.build(), plastic))
        self.assertTrue(op.ready_fits(here, self.build(mirror=True), plastic))    # (the mod builds it mirrored: NPC Worker)
        self.assertFalse(op.ready_fits(here, self.build(), op.WANT))            # (no sulfur: no plant for both)
        set_up = self.build()
        set_up["entities"][0]["pickup_position"] = {"x": 0, "y": -2}
        self.assertFalse(op.ready_fits(here, set_up, plastic))                  # (a setting its copies would lose)   # (inlets swap: not built mirrored yet)
        size = op.ready_size(self.build(), {"plastic-bar": 2})
        self.assertEqual((size["copies"], size["crude"]), (2, 36))

    def test_a_mirrored_piece_goes_into_the_plan_marked(self):
        from companion.modules import Placed
        self.assertEqual(Placed("oil-refinery", 1.5, 1.5, 0, "advanced-oil-processing", mirror=True).to_plan("r")["mirror"], True)
        self.assertNotIn("mirror", Placed("pipe", 0.5, 0.5).to_plan("p"))


class MapTests(unittest.TestCase):
    def plan(self):
        return op.plan(copy.deepcopy(FIXTURE["site"]), copy.deepcopy(FIXTURE["world"]))

    def test_on_the_practice_field_under_the_bus_zone(self):
        plan = self.plan()
        counts = collections.Counter(e["prototype"] for e in plan["entities"])
        self.assertEqual(plan["sizing"]["process"], "advanced-oil-processing")
        self.assertEqual(counts["oil-refinery"], plan["sizing"]["refineries"])
        self.assertGreaterEqual(plan["pumpjacks"], 3)       # (60 crude/s at the 10/s it counts a well for: as many as free)
        self.assertEqual(counts["offshore-pump"], 1)
        self.assertGreater(counts["small-electric-pole"], 0)
        # nothing on a cliff (with the tile it keeps clear), nothing on anything else
        origin = FIXTURE["site"]["origin"]
        cliffs = op.cliff_tiles(FIXTURE["world"])
        tiles = collections.Counter()
        for e in plan["entities"]:
            x, y = e["position"]["x"] + origin["x"], e["position"]["y"] + origin["y"]
            for t in _tiles(e["prototype"], x, y, e["direction"]):
                tiles[t] += 1
        self.assertEqual([t for t, n in tiles.items() if n > 1], [])
        self.assertFalse(set(tiles) & cliffs)

    def test_each_pumpjack_has_a_pipe_on_its_outlet(self):
        # (routes ran from the outlet with the plant's pipes as joins: no pipe reached a pumpjack, oil-practice
        # 2026-10-29)
        plan = self.plan()
        pipes = {(math.floor(e["position"]["x"]), math.floor(e["position"]["y"])) for e in plan["entities"]
                 if e["prototype"] in ("pipe", "pipe-to-ground")}
        for e in plan["entities"]:
            if e["prototype"] == "pumpjack":
                d = e["direction"]
                ox, oy = {0: (1, -2), 4: (2, 1), 8: (-1, 2), 12: (-2, -1)}[d]
                self.assertIn((math.floor(e["position"]["x"] + ox), math.floor(e["position"]["y"] + oy)), pipes)

    def test_a_field_short_of_crude_gets_as_many_refineries_as_it_runs(self):
        # (Codex review, DS-3: four pumpjacks for a plant drawing 60 crude/s were accepted silently)
        plan = self.plan()
        given = plan["pumpjacks"] * op.well_rate(op.well_amount(FIXTURE["site"]))
        self.assertGreaterEqual(given, plan["sizing"]["crude"] * 0.98)
        self.assertLess(plan["sizing"]["refineries"], 3)
        self.assertTrue(any("crude/s" in n for n in plan["notes"]), plan["notes"])
        # its field as it knows it (patches kept relative to home): twice a normal well's crude
        here = {"resource": "crude-oil", "tiles": 5, "amount": 3000000, "dx": 44, "dy": -46}
        rich = {**copy.deepcopy(FIXTURE["site"]), "known_patches": [here]}
        self.assertEqual(op.well_amount(rich), 600000)
        self.assertEqual(op.plan(rich, copy.deepcopy(FIXTURE["world"]))["sizing"]["refineries"], 3)
        # a rich field far off says nothing about this one's wells (Codex review, DS-3)
        far = {**copy.deepcopy(FIXTURE["site"]), "known_patches": [{**here, "dx": 300, "dy": 300}]}
        self.assertLess(op.plan(far, copy.deepcopy(FIXTURE["world"]))["sizing"]["refineries"], 3)

    def test_a_second_plant_makes_only_what_the_first_does_not(self):
        built = {**copy.deepcopy(FIXTURE["site"]),
                 "base": [{"purpose": "oil-processing", "recipes": ["advanced-oil-processing", "plastic-bar"]}]}
        missing = op.missing_products(built)
        self.assertEqual(missing, ["sulfur"])
        plan = op.plan(built, copy.deepcopy(FIXTURE["world"]), {m: op.WANT[m] for m in missing})
        self.assertEqual(set(plan["sizing"]["plants"]), {"sulfur"})
        recipes = {e.get("recipe") for e in plan["entities"] if e["prototype"] == "chemical-plant"}
        self.assertNotIn("plastic-bar", recipes)

    def test_sulfur_unlocked_after_a_plastic_plant_gets_a_plant_of_its_own(self):
        from companion.catalog_planner import available
        base = {"purpose": "oil-processing", "recipes": ["advanced-oil-processing", "plastic-bar"]}
        both = site()
        self.assertEqual(op.missing_products({**both, "base": [base]}), ["sulfur"])
        self.assertEqual(op.missing_products(both), [])                    # (nothing built yet: the first plant)
        self.assertEqual(op.missing_products({**both, "base": [{**base, "recipes": ["plastic-bar", "sulfur"]}]}), [])
        names = ["pumpjack", "small-electric-pole", "oil-refinery", "chemical-plant", "basic-oil-processing",
                 "advanced-oil-processing", "heavy-oil-cracking", "light-oil-cracking", "plastic-bar", "sulfur"]
        done = {**both, "recipes": both["recipes"] + [{"name": n} for n in names if n not in {r["name"] for r in VANILLA}],
                "completed_purposes": ["iron-smelting-start", "steam-power", "oil-gathering", "oil-processing"]}
        self.assertTrue(available("oil-processing", {**done, "base": [base]}))
        self.assertFalse(available("oil-processing", {**done, "base": [{**base, "recipes": ["plastic-bar", "sulfur"]}]}))

    def test_without_a_field_or_a_process_it_says_why(self):
        world = copy.deepcopy(FIXTURE["world"])
        world["resources"]["crude-oil"] = []
        with self.assertRaises(PlanError):
            op.plan(copy.deepcopy(FIXTURE["site"]), world)
        bare = {**copy.deepcopy(FIXTURE["site"]), "recipes": [r for r in FIXTURE["site"]["recipes"]
                                                             if "oil-processing" not in r["name"]]}
        with self.assertRaises(PlanError):
            op.plan(bare, copy.deepcopy(FIXTURE["world"]))


if __name__ == "__main__":
    unittest.main()
