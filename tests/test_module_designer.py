"""Module designs from the LLM designer: request building, checks and the module library (no paid calls)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import designer_planner as dp  # noqa: E402
from companion import module_designer as md  # noqa: E402
from companion.module_library import ModuleLibrary  # noqa: E402
from companion.modules import EAST, Placed, gears  # noqa: E402
from companion.ratios import Book  # noqa: E402
from companion import bus_planner  # noqa: E402
from unittest import mock  # noqa: E402

FACTS = {
    "transport-belt": {"type": "transport-belt", "width": 1, "height": 1, "placement_item": "transport-belt",
                       "energy_source": "none"},
    "underground-belt": {"type": "underground-belt", "width": 1, "height": 1, "placement_item": "underground-belt",
                         "energy_source": "none"},
    "inserter": {"type": "inserter", "width": 1, "height": 1, "placement_item": "inserter", "energy_source": "electric",
                 "pickup_vector": [0, -1], "drop_vector": [0, 1.2]},
    "assembling-machine-2": {"type": "assembling-machine", "width": 3, "height": 3,
                             "placement_item": "assembling-machine-2", "energy_source": "electric",
                             "crafting_categories": ["basic-crafting", "crafting", "advanced-crafting"]},
    "small-electric-pole": {"type": "electric-pole", "width": 1, "height": 1, "placement_item": "small-electric-pole",
                            "energy_source": "none", "supply_area_distance": 2.5, "max_wire_distance": 7.5},
}


def recipes(book: Book) -> list[dict]:
    out = []
    for name in ("iron-gear-wheel",):
        r = book.recipes[name]
        out.append({"name": name, "category": r.category, "handcraft": True,
                    "ingredients": [{"name": n, "amount": a} for n, a in r.ingredients],
                    "products": [{"name": n, "amount": a} for n, a in r.products]})
    return out


class ModuleDesignerTests(unittest.TestCase):
    def test_the_brief_names_the_ratio_ports_and_box(self):
        book = Book.vanilla()
        ref = md.reference("green_circuits", 3.0, book)
        text = md.brief("green_circuits", 3.0, book, ref, "assembling-machine-2")
        self.assertIn("3 x copper-cable", text)
        self.assertIn("2 x electronic-circuit", text)
        for port in ref.ports:
            self.assertIn(f"y={port.y}", text)
        self.assertLessEqual(len(text), 4000)

    def test_the_module_site_passes_the_designer_contracts(self):
        book = Book.vanilla()
        ref = md.reference("gears", 0.5, book)
        goal = md.brief("gears", 0.5, book, ref, "assembling-machine-2")
        site = md.module_site("gears", ref, FACTS, recipes(book), goal)
        context = dp.build_context(site)
        demand = [{"item": "iron-gear-wheel", "metric": "rate_items_per_second", "amount": 0.5, "window_ticks": 3600}]
        request = dp.make_request(context, goal, request_id="module-gears", demand=demand, max_entities=128,
                                  max_stages=1)
        self.assertEqual(request["constraints"]["max_entities"], 128)
        built = context["facts"]["built_machines"]["value"]
        self.assertEqual(len(built), len(ref.ports) + 1)   # the port belts and the network pole

    def test_undergrounds_pair_like_the_game_pairs_them(self):
        tunnels = [Placed("underground-belt", 2.5, 0.5, EAST), Placed("underground-belt", 5.5, 0.5, EAST),
                   Placed("underground-belt", 7.5, 0.5, EAST), Placed("underground-belt", 9.5, 0.5, EAST)]
        paired = md.pair_undergrounds(tunnels)
        kinds = {(e.x, e.kind) for e in paired}
        self.assertEqual(kinds, {(2.5, "input"), (5.5, "output"), (7.5, "input"), (9.5, "output")})
        with self.assertRaises(md.DesignError):
            md.pair_undergrounds([Placed("underground-belt", 2.5, 0.5, EAST)])

    def test_checks_catch_missing_machines_overlaps_and_open_ports(self):
        book = Book.vanilla()
        ref = md.reference("gears", 0.5, book)
        empty = {"entities": [{"prototype": "transport-belt", "position": {"x": 3.5, "y": 3.5}, "direction": 4},
                              {"prototype": "transport-belt", "position": {"x": 3.5, "y": 3.5}, "direction": 4}]}
        module = md.to_module("gears", empty, ref)
        problems = md.check(module, "gears", 0.5, book, "assembling-machine-2")
        self.assertTrue(any("overlap" in p for p in problems))
        self.assertTrue(any("machines on iron-gear-wheel" in p for p in problems))
        self.assertTrue(any("next to the" in p for p in problems))
        # the built-in module itself passes
        builtin = md.to_module("gears", {"entities": [e.to_plan(f"e{n}") for n, e in enumerate(ref.entities)
                                                      if not (e.x < 1 and any(p.y + 0.5 == e.y for p in ref.ports))]},
                               ref)
        self.assertEqual(md.check(builtin, "gears", 0.5, book, "assembling-machine-2"), [])

    def test_the_library_keeps_the_best_fitting_proven_module(self):
        book = Book.vanilla()
        with tempfile.TemporaryDirectory() as folder:
            library = ModuleLibrary(folder)
            small = library.save("gears", gears(0.5, book), 0.6, {"source": "test"})
            big = library.save("gears", gears(2.0, book), 2.2, {"source": "test"})
            self.assertNotEqual(small, big)
            again = ModuleLibrary(folder)
            self.assertEqual(again.best("gears", 0.5)[0], small)
            self.assertEqual(again.best("gears", 1.0)[0], big)
            self.assertIsNone(again.best("gears", 0.1))      # 0.6 is over three times 0.1: wasteful
            self.assertIsNone(again.best("red_science", 0.2))
            module = again.get(small)
            self.assertEqual([p.item for p in module.ports], [p.item for p in gears(0.5, book).ports])


class DesignChoiceTests(unittest.TestCase):
    def library_with_a_lean_gears_design(self, folder):
        book = Book.vanilla()
        lean = gears(0.5, book)
        lean.entities = [e for e in lean.entities if e.name != "small-electric-pole"] +             [Placed("small-electric-pole", 2.5, 2.5)]
        library = ModuleLibrary(folder)
        key = library.save("gears", lean, 1.0, {"source": "test"})
        return library, key, lean

    def test_personality_and_cost_decide_between_builtin_and_design(self):
        from companion.base_plan import pick_module
        book = Book.vanilla()
        with tempfile.TemporaryDirectory() as folder:
            library, key, lean = self.library_with_a_lean_gears_design(folder)
            builtin = gears(0.5, book)
            self.assertLess(len(lean.entities), len(builtin.entities))
            designs = {}
            picked = pick_module("gears", 0.5, book, "assembling-machine-2", library, (), None, designs)
            self.assertEqual(len(picked.entities), len(lean.entities))   # cheaper to build
            self.assertEqual(designs, {"gears": key})
            designs = {}
            picked = pick_module("gears", 0.5, book, "assembling-machine-2", library, ("traditionalist",), None, designs)
            self.assertEqual(len(picked.entities), len(builtin.entities))
            self.assertEqual(designs, {})
            # a bus already being built keeps its choice, whatever the library holds now
            picked = pick_module("gears", 0.5, book, "assembling-machine-2", library, ("traditionalist",),
                                 {"gears": key}, designs)
            self.assertEqual(len(picked.entities), len(lean.entities))
            picked = pick_module("gears", 0.5, book, "assembling-machine-2", library, (), {}, {})
            self.assertEqual(len(picked.entities), len(builtin.entities))

    def test_the_bus_plan_remembers_its_designs(self):
        from companion.bus_planner import plan_stage
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        world = {"area": [-70, -70, 70, 90], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                 "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        site = {"request_id": "1:5", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
                "researched": ["logistic-science-pack"], "personality": {"traits": ["curious"]}}
        with tempfile.TemporaryDirectory() as folder:
            library, key, _ = self.library_with_a_lean_gears_design(folder)
            with mock.patch.object(bus_planner, "FIRST_SHARE", 1.0):   # (the design is for a full-rate first bus)
                first = plan_stage(site, world, Book.vanilla(), library)
            self.assertIn(f"gears={key}", first["base_plan"]["designs"])
            saved = {**first["base_plan"], "done": {first["base_plan"]["stage"]: 1}}
            second = plan_stage({**site, "base_plan": saved}, world, Book.vanilla(), ModuleLibrary(folder))
            self.assertEqual(second["base_plan"]["designs"], first["base_plan"]["designs"])
            self.assertEqual((second["base_plan"]["x"], second["base_plan"]["y"]),
                             (first["base_plan"]["x"], first["base_plan"]["y"]))


if __name__ == "__main__":
    unittest.main()
