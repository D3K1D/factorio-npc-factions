"""The rates library (companion/rates.py): this game's machines from its own data, and the briefs and
options that carry their numbers."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import rates


def entity(kind, ready=True, **values):
    return {"kind": kind, "width": 1, "height": 1, **({"ready": True} if ready else {}), **values}


# The vanilla numbers as the game exports them (/npc-plan facts), a few modded tiers above.
FACTS = {
    "ok": True, "schema": "npc-game-facts-v1", "game_version": "2.1.23", "mods": {"base": "2.1.23"},
    "entities": {
        "stone-furnace": entity("crafter", speed=1, categories=["smelting"], kw=90, source="burner",
                                fuel=["chemical"], effectivity=1, modules=0),
        "burner-mining-drill": entity("drill", speed=0.25, categories=["basic-solid"], kw=150, source="burner",
                                      fuel=["chemical"], effectivity=1),
        "electric-mining-drill": entity("drill", ready=False, speed=0.5, categories=["basic-solid"], kw=90,
                                        source="electric", drain_kw=0, modules=3),
        "assembling-machine-1": entity("crafter", speed=0.5, categories=["crafting"], kw=75, source="electric",
                                       drain_kw=2.5),
        "assembling-machine-2": entity("crafter", ready=False, speed=0.75, categories=["crafting"], kw=150,
                                       source="electric", drain_kw=5, modules=2),
        "inserter": entity("inserter", rotation=0.014, stack_bonus=0, force_bonus=True, kw=13.2, source="electric",
                           drain_kw=0.4),
        "fast-inserter": entity("inserter", ready=False, rotation=0.04, stack_bonus=0, force_bonus=True),
        "long-handed-inserter": entity("inserter", ready=False, rotation=0.02, stack_bonus=0, force_bonus=True),
        "bulk-inserter": entity("inserter", ready=False, rotation=0.04, stack_bonus=0, bulk=True),
        "transport-belt": entity("belt", items_per_second=15),
        "boiler": entity("boiler", ready=False, kw=1800, source="burner", effectivity=1, target_temperature=165),
        "heat-exchanger": entity("boiler", ready=False, kw=10000, target_temperature=500),
        "steam-engine": entity("generator", ready=False, max_kw=900, fluid_per_second=30),
        "steam-turbine": entity("generator", ready=False, max_kw=5820, fluid_per_second=60),
        "offshore-pump": entity("pump", ready=False, per_second=1200),
    },
    "fuels": {"coal": {"mj": 4, "categories": ["chemical"]}, "wood": {"mj": 2, "categories": ["chemical"]}},
    "fluids": {"water": {"heat_capacity_kj": 2, "default_temperature": 15},
               "steam": {"heat_capacity_kj": 0.2, "default_temperature": 15}},
    "bonuses": {"inserter_stack": 0, "bulk_inserter_capacity": 0, "belt_stack": 0},
}
GEARS = {"name": "iron-gear-wheel", "category": "crafting", "energy": 0.5,
         "ingredients": [{"name": "iron-plate", "amount": 2}], "products": [{"name": "iron-gear-wheel", "amount": 1}]}
PLATES = {"name": "iron-plate", "category": "smelting", "energy": 3.2,
          "ingredients": [{"name": "iron-ore", "amount": 1}], "products": [{"name": "iron-plate", "amount": 1}]}


class RatesTests(unittest.TestCase):
    def setUp(self):
        self.facts = rates.Facts(copy.deepcopy(FACTS))

    def test_inserters_and_belts_move_what_the_game_says_with_research(self):
        f = self.facts
        self.assertEqual([f.inserter_rate(n) for n in ("inserter", "fast-inserter", "long-handed-inserter")],
                         [0.84, 2.4, 1.2])
        researched = rates.Facts({**FACTS, "bonuses": {"inserter_stack": 1, "bulk_inserter_capacity": 3}})
        self.assertEqual(researched.inserter_rate("inserter"), 1.68)    # two in the hand
        self.assertEqual(researched.inserter_rate("bulk-inserter"), 9.6)  # the bulk bonus, not the plain one
        self.assertEqual(f.belt_rate("transport-belt"), 15)
        self.assertIsNone(f.inserter_rate("transport-belt"))

    def test_burner_fuel_and_electric_draw(self):
        f = self.facts
        self.assertEqual(f.fuel_per_second("stone-furnace"), 0.0225)     # 90 kW of 4 MJ coal
        self.assertEqual(f.fuel_per_second("boiler"), 0.45)
        self.assertEqual(f.fuel_per_second("stone-furnace", "wood"), 0.045)
        self.assertIsNone(f.fuel_per_second("assembling-machine-1"))
        self.assertEqual(f.kw("assembling-machine-1"), 77.5)             # with its drain

    def test_a_steam_plant_fits_together_from_the_games_numbers(self):
        plant = self.facts.steam()
        # the first tier when nothing is buildable yet: a boiler, not a heat exchanger; an engine, not a turbine
        self.assertEqual((plant["boiler"], plant["engine"], plant["pump"]), ("boiler", "steam-engine", "offshore-pump"))
        self.assertEqual((plant["steam_per_boiler"], plant["engines_per_boiler"], plant["boilers_per_pump"]),
                         (60.0, 2.0, 20.0))

    def test_who_makes_a_recipe_and_how_fast_buildable_ones_first(self):
        self.assertEqual(self.facts.per_machine(GEARS), [("assembling-machine-1", 1.0), ("assembling-machine-2", 1.5)])
        self.assertEqual(self.facts.per_machine(PLATES), [("stone-furnace", 0.3125)])
        self.assertEqual(self.facts.per_machine({"name": "x", "category": "chemistry", "energy": 1,
                                                 "products": [{"name": "x", "amount": 1}]}), [])

    def test_the_brief_says_what_the_buildings_do_and_how_power_fits(self):
        text = self.facts.brief(["stone-furnace", "burner-mining-drill", "boiler", "steam-engine"])
        self.assertIn("stone-furnace speed 1, burns 0.0225 coal a second", text)
        self.assertIn("inserter 0.84 a second", text)
        self.assertNotIn("fast-inserter", text)                  # not buildable, not asked for
        self.assertIn("transport-belt 15 a second (both lanes)", text)
        self.assertIn("a boiler makes 60 steam a second, enough for 2 steam-engine", text)
        self.assertIn("one offshore-pump feeds 20 boilers", text)
        self.assertLessEqual(len(self.facts.brief(list(FACTS["entities"]), limit=200)), 200)

    def test_a_layout_note_for_system_one(self):
        note = rates.layout_note({"burner-mining-drill": 4, "stone-furnace": 4}, self.facts, [PLATES], "iron-ore")
        self.assertEqual(note, "At full speed it smelts up to 1.25 iron-plate a second, mines 1.00 iron-ore a second, "
                               "burns 0.240 coal a second.")
        self.assertEqual(rates.layout_note({"stone-wall": 40}, self.facts), "")

    def test_the_facts_of_a_request(self):
        site = {"game_facts": FACTS}
        self.assertIs(rates.of(site), rates.of(site))
        self.assertIsNone(rates.of({}))
        self.assertIsNone(rates.of({"game_facts": {"ok": True, "entities": {}}}))
        self.assertNotEqual(rates.Facts(FACTS).key, rates.Facts({**FACTS, "bonuses": {"inserter_stack": 1}}).key)


class BriefTests(unittest.TestCase):
    def test_the_designer_sees_recipe_rates_measured_metrics_and_the_numbers(self):
        from test_codex_backend import FakeCodex
        from test_designer_planner import site, wire
        from designer_llm.proposal_schema import to_wire
        from companion import layout_planner
        from companion.designer_planner import LLMPlanner, build_context
        measured = {"id": "smelt", "purpose": "iron-smelting-expansion", "capacity": {"makes": {"iron-plate": 1.25}},
                    "measured": {"minutes": 40, "out": {"iron-plate": 0.82}, "shares": {"working": 0.9}}}
        s = {**site(), "game_facts": FACTS, "base": [measured],
             "recipes": [dict(PLATES, handcraft=False), dict(GEARS, handcraft=True)]}
        context = build_context(s)
        rows = {r["name"]: r for r in context["facts"]["recipes"]["value"]}
        self.assertEqual(rows["iron-gear-wheel"]["made_in"][0], {"machine": "assembling-machine-1", "per_second": 1.0})
        self.assertEqual(rows["iron-plate"]["seconds"], 3.2)
        metrics = context["facts"]["item_metrics"]
        self.assertEqual(metrics["knowledge"], "known")
        plate = metrics["value"]["iron-plate"]
        self.assertEqual((plate["rate_items_per_second"]["value"], plate["measured_window_ticks"]["value"]), (0.82, 108000))
        self.assertEqual(plate["stock"]["knowledge"], "unknown")
        good = layout_planner.plan(s)
        codex = FakeCodex(to_wire(wire(context, good)))
        LLMPlanner(model="gpt-6.1-sol", codex=codex, log=lambda _: None)(s)
        prompt = codex.asked[0][0]
        self.assertIn("This game's numbers", prompt)
        self.assertIn("burns 0.0225 coal a second", prompt)
        # without the facts, as before
        plain = build_context(site())
        self.assertEqual(plain["facts"]["item_metrics"]["knowledge"], "unknown")
        self.assertNotIn("made_in", json.dumps(plain["facts"]["recipes"]))

    def test_system_one_weighs_saved_layouts_on_what_they_make(self):
        from companion import blueprints as bp
        from companion import layout_planner
        from companion.catalog_planner import CatalogPlanner
        from test_blueprints import POWERED, site

        class Chooser:   # takes the saved layout, keeping what it was shown
            seen = []

            def choose(self, s, options, question="layout"):
                self.seen.append(options)
                target = next(o["id"] for o in options if o["id"].startswith("bp-"))
                return target, {o["id"]: float(o["id"] == target) for o in options}
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            source = site(**POWERED)
            library.add(bp.from_plan(layout_planner.plan(source), source, source="starter-layout-planner"))
            chooser = Chooser()
            planner = CatalogPlanner(library, designer=lambda s: layout_planner.plan(s), chooser=chooser,
                                     log=lambda _: None)
            planner(site(request="2:1", recipes=[PLATES, {"name": "burner-mining-drill"}, {"name": "stone-furnace"}],
                         game_facts=FACTS, **POWERED))
        saved = next(o for o in chooser.seen[-1] if o["id"].startswith("bp-"))
        self.assertIn("At full speed it smelts up to", saved["description"])

    def test_the_module_designer_is_told_this_games_inserters(self):
        from companion.module_designer import INSERTERS, inserter_rates
        self.assertEqual(inserter_rates(rates.Facts(FACTS)),
                         "inserter 0.84 items/s, fast-inserter 2.4 items/s, long-handed-inserter 1.2 items/s")
        self.assertEqual(inserter_rates(None), INSERTERS)


class BridgeTests(unittest.TestCase):
    def test_the_facts_are_read_once_per_research_level(self):
        from companion.designer_bridge import DesignerBridge, MARKER
        asked = []

        class Game:
            def text(self, command):
                asked.append(command)
                return MARKER + " " + json.dumps(FACTS)

        bridge = DesignerBridge(Game(), planner=lambda s: {}, log=lambda _: None)
        first = bridge.with_facts({"request_id": "1:1", "force": "player", "researched": ["a"]}, 1)
        again = bridge.with_facts({"request_id": "2:1", "force": "player", "researched": ["a"]}, 2)
        later = bridge.with_facts({"request_id": "1:2", "force": "player", "researched": ["a", "b"]}, 1)
        self.assertEqual(len(asked), 2)                       # once more after a research
        self.assertTrue(all(r["game_facts"]["entities"] for r in (first, again, later)))
        self.assertIn('/npc-plan facts {"npc_id": 1}', asked[0])
        self.assertEqual(list(bridge._facts), ["player"])     # only the latest per force is kept

        class OldGame:
            def text(self, command):
                return "Unknown command"
        old = DesignerBridge(OldGame(), planner=lambda s: {}, log=lambda _: None)
        self.assertNotIn("game_facts", old.with_facts({"request_id": "1:1", "force": "player"}, 1))


if __name__ == "__main__":
    unittest.main()
