"""Ratio calculator and base pictures."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from companion.ratios import Book, audit, science, solve, unlocked  # noqa: E402

AM2 = {"assembling-machine-2", "stone-furnace", "chemical-plant", "oil-refinery"}


class RatioTests(unittest.TestCase):
    def setUp(self):
        self.book = Book.vanilla()

    def test_thirty_blue_per_minute_matches_the_known_build(self):
        plan = solve({"chemical-science-pack": 0.5}, self.book, allowed=AM2)
        counts = {s.recipe: s.count for s in plan.steps}
        self.assertEqual(counts["chemical-science-pack"], 8)
        self.assertEqual(counts["engine-unit"], 7)
        self.assertEqual(counts["advanced-circuit"], 6)
        self.assertEqual(counts["steel-plate"], 8)
        self.assertAlmostEqual(plan.step("copper-cable").rate, 7.5)
        self.assertAlmostEqual(plan.raw["iron-ore"], 6.0)
        self.assertEqual(plan.drills()["iron-ore"], 12)

    def test_classic_green_circuit_ratio_is_three_cable_to_two_circuit(self):
        plan = solve({"electronic-circuit": 3.0}, self.book, inputs={"iron-plate", "copper-plate"},
                     allowed={"assembling-machine-2"})
        self.assertEqual(plan.step("electronic-circuit").count, 2)
        self.assertEqual(plan.step("copper-cable").count, 3)
        self.assertEqual(set(plan.inputs), {"iron-plate", "copper-plate"})
        self.assertAlmostEqual(plan.belts()["copper-plate"], 4.5 / 15)

    def test_space_age_moves_circuits_to_electronics(self):
        book = Book.vanilla(space_age=True)
        self.assertEqual(book.recipes["electronic-circuit"].category, "electronics")
        plan = solve({"electronic-circuit": 1.0}, book, inputs={"iron-plate", "copper-plate"},
                     allowed={"assembling-machine-2"})
        self.assertEqual(plan.step("electronic-circuit").machine, "assembling-machine-2")

    def test_the_game_export_overrides_offline_recipes(self):
        site = {"recipes": [{"name": "iron-gear-wheel", "category": "crafting", "energy": 1.0,
                             "ingredients": [{"name": "iron-plate", "amount": 2}],
                             "products": [{"name": "iron-gear-wheel", "amount": 1}]}]}
        book = Book.from_site(site)
        self.assertEqual(book.recipes["iron-gear-wheel"].energy, 1.0)
        self.assertIn("engine-unit", book.recipes)

    def test_a_modded_item_is_made_by_whatever_recipe_makes_it(self):
        # Bob's Warfare: a magazine is an empty magazine ("empty-recipe") and 2 plates
        site = {"recipes": [
            {"name": "empty-recipe", "category": "crafting", "energy": 1.0,
             "ingredients": [{"name": "iron-plate", "amount": 2}], "products": [{"name": "empty-magazine", "amount": 1}]},
            {"name": "firearm-magazine", "category": "crafting", "energy": 1.0,
             "ingredients": [{"name": "empty-magazine", "amount": 1}, {"name": "iron-plate", "amount": 2}],
             "products": [{"name": "firearm-magazine", "amount": 1}]}]}
        book = Book.from_site(site)
        self.assertEqual(book.recipe_for("empty-magazine").name, "empty-recipe")
        plan = solve({"firearm-magazine": 1.0}, book, inputs={"iron-plate"}, allowed={"assembling-machine-2"})
        self.assertEqual(plan.step("empty-recipe").item, "empty-magazine")
        self.assertAlmostEqual(plan.inputs["iron-plate"], 4.0)
        self.assertIn("empty-magazine", unlocked(site))
        self.assertIn("empty-recipe", unlocked(site))

    def test_audit_compares_the_build_with_what_science_uses(self):
        dump = {"force": "npc-faction-1", "entities": [
            {"n": "assembling-machine-1", "t": "assembling-machine", "r": "iron-gear-wheel", "f": "npc-faction-1"},
            {"n": "assembling-machine-1", "t": "assembling-machine", "r": "automation-science-pack",
             "f": "npc-faction-1"}]}
        rows, packs = audit(dump, self.book)
        self.assertAlmostEqual(packs["automation-science-pack"] * 60, 6.0)
        gear = next(r for r in rows if r.recipe == "iron-gear-wheel")
        self.assertAlmostEqual(gear.capacity * 60, 60.0)
        self.assertAlmostEqual(gear.utilization, 0.1)
        self.assertEqual(science(30, ["automation-science-pack"]), {"automation-science-pack": 0.5})


class PictureTests(unittest.TestCase):
    def test_renders_a_small_base(self):
        from render_base import render
        dump = {"area": [-10, -10, 10, 10], "home": {"x": 0, "y": 0}, "force": "npc-faction-1", "npc_name": "Ada",
                "tick": 1, "character": {"x": 1, "y": 1}, "water": [[-8, -8, -4]],
                "resources": {"iron-ore": [[3, 2, 6]]}, "trees": [[-5, 5]],
                "entities": [
                    {"n": "assembling-machine-1", "t": "assembling-machine", "x": 0.5, "y": 0.5, "d": 0, "w": 3,
                     "h": 3, "f": "npc-faction-1", "r": "iron-gear-wheel"},
                    {"n": "transport-belt", "t": "transport-belt", "x": 2.5, "y": 0.5, "d": 4, "w": 1, "h": 1,
                     "f": "npc-faction-1"},
                    {"n": "inserter", "t": "inserter", "x": -1.5, "y": 0.5, "d": 12, "w": 1, "h": 1,
                     "f": "npc-faction-1"},
                    {"n": "big-rock", "t": "simple-entity", "x": 5.5, "y": -5.5, "w": 2, "h": 2, "f": "neutral"}]}
        with tempfile.TemporaryDirectory() as folder:
            out = render(dump, Path(folder) / "base.png")
            from PIL import Image
            with Image.open(out) as image:
                self.assertGreater(image.size[0], 100)


if __name__ == "__main__":
    unittest.main()
