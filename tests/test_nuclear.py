"""Nuclear power sized from the game's numbers (companion/nuclear.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import json  # noqa: E402
from collections import Counter  # noqa: E402

from companion import nuclear  # noqa: E402
from companion import nuclear_cell  # noqa: E402

# The game's heat and fluid connections for the four pieces (2.1, read live).
PROTOS = json.loads((ROOT / "tests" / "fixtures" / "nuclear-prototypes.json").read_text(encoding="utf-8"))

# Vanilla 2.x, as the game's facts export gives it.
FACTS = {"entities": {
    "nuclear-reactor": {"kind": "reactor", "kw": 40000, "neighbour_bonus": 1, "ready": True},
    "heat-exchanger": {"kind": "boiler", "source": "heat", "kw": 10000, "target_temperature": 500, "ready": True},
    "boiler": {"kind": "boiler", "source": "burner", "kw": 1800, "target_temperature": 165, "ready": True},
    "steam-turbine": {"kind": "generator", "max_kw": 5820, "fluid_per_second": 60, "ready": True},
    "steam-engine": {"kind": "generator", "max_kw": 900, "fluid_per_second": 30, "ready": True},
    "offshore-pump": {"kind": "pump", "per_second": 1200, "ready": True},
    "electric-mining-drill": {"kind": "drill", "speed": 0.5, "ready": True},
    "centrifuge": {"kind": "crafter", "speed": 1, "categories": ["centrifuging"], "ready": True}},
    "resources": {"uranium-ore": {"mining_time": 2, "fluid": "sulfuric-acid", "fluid_amount": 10}},
    "fuels": {"uranium-fuel-cell": {"mj": 8000, "categories": ["nuclear"]}},
    "fluids": {"steam": {"heat_capacity_kj": 0.2}, "water": {"heat_capacity_kj": 0.2, "default_temperature": 15}}}
RECIPES = [
    {"name": "uranium-fuel-cell", "energy": 10, "category": "crafting",
     "ingredients": [{"name": "iron-plate", "amount": 10}, {"name": "uranium-235", "amount": 1},
                     {"name": "uranium-238", "amount": 19}],
     "products": [{"name": "uranium-fuel-cell", "amount": 10}]},
    {"name": "uranium-processing", "energy": 12, "category": "centrifuging",
     "ingredients": [{"name": "uranium-ore", "amount": 10}],
     "products": [{"name": "uranium-235", "amount": 1, "probability": 0.007},
                  {"name": "uranium-238", "amount": 1, "probability": 0.993}]}]


def site():
    return {"game_facts": FACTS, "recipes": RECIPES}


class SizingTests(unittest.TestCase):
    def test_neighbours_in_two_rows(self):
        self.assertEqual(nuclear.neighbours(1), [0])
        self.assertEqual(nuclear.neighbours(2), [1, 1])
        self.assertEqual(nuclear.neighbours(4), [2, 2, 2, 2])
        self.assertEqual(sorted(nuclear.neighbours(6)), [2, 2, 2, 2, 3, 3])

    def test_a_2x2_block_is_the_known_48_exchangers_and_83_turbines(self):
        size = nuclear.sizing(site(), 4)
        self.assertEqual((size["reactor"], size["heat_exchanger"], size["turbine"]),
                         ("nuclear-reactor", "heat-exchanger", "steam-turbine"))
        self.assertEqual(size["heat_mw"], 480)                     # 4 x 40 MW x (1 + 2 neighbours)
        self.assertEqual((size["heat_exchangers"], size["turbines"]), (48, 83))
        self.assertAlmostEqual(size["power_mw"], 480)
        self.assertAlmostEqual(size["fuel_cells_per_minute"], 1.2)  # (a cell lasts 200 s in each reactor)
        self.assertEqual(size["offshore_pumps"], 5)                # 48 x 103 water a second

    def test_two_reactors_and_the_uranium_behind_their_cells(self):
        size = nuclear.sizing(site(), 2)
        self.assertEqual(size["heat_mw"], 160)
        self.assertEqual((size["heat_exchangers"], size["turbines"]), (16, 28))
        self.assertAlmostEqual(size["fuel_cells_per_minute"], 0.6)
        # 0.06 U-235 a minute; a centrifuge makes 0.007 every 12 s (0.035 a minute)
        self.assertAlmostEqual(size["u235_per_minute"], 0.06)
        self.assertEqual(size["centrifuge"], "centrifuge")
        self.assertEqual(size["centrifuges"], 2)
        self.assertGreater(size["uranium_ore_per_second"], 0)

    def test_uranium_ore_by_drills_with_acid(self):
        got = nuclear.mining(site(), 1.0)
        self.assertEqual(got["drill"], "electric-mining-drill")
        self.assertEqual(got["ore_per_drill"], 0.25)          # speed 0.5 over mining time 2
        self.assertEqual(got["drills"], 4)
        self.assertEqual(got["fluid"], "sulfuric-acid")
        self.assertAlmostEqual(got["fluid_per_second"], 1.0)   # 1 acid a cycle (10 a ten), 4 drills at 0.25 cycles a second
        self.assertIsNone(nuclear.mining({"game_facts": {**FACTS, "resources": {}}, "recipes": RECIPES}, 1.0))

    def test_without_a_reactor_or_turbine_there_is_no_plant(self):
        bare = {"game_facts": {**FACTS, "entities": {k: v for k, v in FACTS["entities"].items() if k != "steam-turbine"
                                                     and k != "steam-engine"}}, "recipes": RECIPES}
        self.assertIsNone(nuclear.sizing(bare, 2))


class UnitCellTests(unittest.TestCase):
    def counts(self, cell):
        return dict(Counter(e[0] for e in cell["entities"]))

    def test_the_two_reactor_cell_at_the_sized_ratio_all_meets(self):
        cell = nuclear_cell.unit_cell(PROTOS, 16, 28)
        self.assertEqual(nuclear_cell.check(cell, PROTOS), [])
        # (4 rows a spine, a gap between rows: 4 x 3 + 3 heat pipes each; the water on by a pipe across each gap)
        self.assertEqual(self.counts(cell), {"nuclear-reactor": 2, "heat-pipe": 30, "heat-exchanger": 16,
                                             "steam-turbine": 28, "pipe": 12})
        self.assertEqual(len(cell["water"]), 4)   # (a chain each side of each spine)
        self.assertTrue(set(cell["water"]).isdisjoint(cell["tiles"]))
        self.assertTrue(cell["gaps"])

    def test_rows_packed_without_a_gap_meet_too(self):
        cell = nuclear_cell.unit_cell(PROTOS, 16, 28, gap=0)
        self.assertEqual(nuclear_cell.check(cell, PROTOS), [])
        self.assertEqual(self.counts(cell)["heat-pipe"], 24)
        self.assertNotIn("pipe", self.counts(cell))

    def test_the_check_finds_a_water_pipe_missing_across_a_gap(self):
        cell = nuclear_cell.unit_cell(PROTOS, 8, 8)
        cell["entities"].remove(next(e for e in cell["entities"] if e[0] == "pipe"))
        self.assertTrue(any("water passes to no exchanger" in p for p in nuclear_cell.check(cell, PROTOS)))

    def test_smaller_and_odd_cells_meet_too(self):
        for exchangers, turbines in ((8, 14), (3, 5), (2, 3)):
            cell = nuclear_cell.unit_cell(PROTOS, exchangers, turbines)
            self.assertEqual(nuclear_cell.check(cell, PROTOS), [], (exchangers, turbines))
            got = self.counts(cell)
            self.assertEqual((got["heat-exchanger"], got["steam-turbine"]), (exchangers, turbines))

    def test_the_check_finds_a_turbine_that_is_missing(self):
        cell = nuclear_cell.unit_cell(PROTOS, 4, 4)
        first = next(e for e in cell["entities"] if e[0] == "steam-turbine")
        cell["entities"].remove(first)
        self.assertTrue(any("steam reaches no turbine" in p for p in nuclear_cell.check(cell, PROTOS)))

    def test_the_check_finds_reactors_apart(self):
        cell = nuclear_cell.unit_cell(PROTOS, 2, 2)
        cell["entities"] = [(n, x + 1, y, d) if n == "nuclear-reactor" and x > 1 else (n, x, y, d)
                            for n, x, y, d in cell["entities"]]
        self.assertIn("the reactors do not touch at facing heat connections", nuclear_cell.check(cell, PROTOS))


def plant_site():
    """The oil-practice map's site with the vanilla facts, the pieces' connections as game_facts.lua exports them."""
    facts = json.loads(json.dumps(FACTS))
    for name, p in PROTOS.items():
        e = facts["entities"].setdefault(name, {"kind": "heat", "ready": True})
        e.update(width=p["w"], height=p["h"], heat=p.get("heat_src") or p.get("heat") or [],
                 fluid=[{"filter": b.get("filter"), "c": b["c"]} for b in (p["fluid"] or [])])
    fixture = json.loads((ROOT / "tests" / "fixtures" / "oil-practice-field.json").read_text(encoding="utf-8"))
    return {**fixture["site"], "game_facts": facts, "recipes": RECIPES}, fixture["world"]


class PlantTests(unittest.TestCase):
    def test_the_plant_by_the_shore_with_water_fuel_and_poles(self):
        from companion import nuclear_plant
        site, world = plant_site()
        plan = nuclear_plant.plan(site, world)
        got = Counter(e["prototype"] for e in plan["entities"])
        self.assertEqual((got["nuclear-reactor"], got["heat-exchanger"], got["steam-turbine"]), (2, 16, 28))
        self.assertEqual(got["offshore-pump"], 2)               # (16 exchangers' water: 1650 a second)
        self.assertEqual((got["iron-chest"], got["inserter"]), (4, 4))   # (each reactor's fuel in, spent cells out)
        self.assertEqual(len(plan["fuel_chests"]), 2)
        self.assertGreater(got["small-electric-pole"], 0)
        # nothing overlaps, and every turbine and inserter has a pole over it
        origin = site.get("origin") or {"x": 0, "y": 0}
        seen, poles, electric = {}, [], []
        for e in plan["entities"]:
            x, y = e["position"]["x"] + origin["x"], e["position"]["y"] + origin["y"]
            p = PROTOS.get(e["prototype"]) or {"w": 1, "h": 1}
            if e["prototype"] == "offshore-pump":
                continue
            tiles = nuclear_cell.tiles_of((x, y), p["w"], p["h"], e["direction"])
            for t in tiles:
                self.assertNotIn(t, seen, (e["prototype"], seen.get(t)))
                seen[t] = e["prototype"]
            if e["prototype"] == "small-electric-pole":
                poles.append((x, y))
            elif e["prototype"] in ("steam-turbine", "inserter"):
                electric.append(tiles)
        for tiles in electric:
            self.assertTrue(any(abs(a + 0.5 - px) < 3 and abs(b + 0.5 - py) < 3 for a, b in tiles for px, py in poles))

    def test_the_goal_comes_before_the_steam_plant_while_power_is_short(self):
        import tempfile
        from unittest import mock
        from companion import blueprints as bp, nuclear_plant
        from companion.catalog_planner import PURPOSES, CatalogPlanner, available, purpose_options
        recipes = [{"name": n} for n in set(PURPOSES["nuclear-power"]["requires_recipes"])
                   | set(PURPOSES["power-plant"]["requires_recipes"])]
        site = {"request_id": "1:40", "purpose": "choose", "recipes": recipes, "resources": [],
                "known_patches": [{"resource": "uranium-ore", "tiles": 400}],
                "completed_purposes": ["steam-power", "power-plant"], "origin": {"x": 0, "y": 0}}
        self.assertFalse(available("nuclear-power", site))                 # (power is not short)
        short = {**site, "power_short": True}
        self.assertEqual(purpose_options(short)[0]["id"], "nuclear-power")
        self.assertTrue(available("nuclear-power", {**short, "known_patches": []}))   # (no uranium known: scouted)
        self.assertFalse(available("nuclear-power", {**short, "recipes": [{"name": "steam-turbine"}]}))
        self.assertFalse(available("nuclear-power", {**short, "completed_purposes": ["nuclear-power"]}))
        self.assertEqual(purpose_options({**short, "completed_purposes": ["nuclear-power"]})[0]["id"], "power-plant")
        _, world = plant_site()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(nuclear_plant, "plan", return_value={"purpose": "nuclear-power", "entities": [],
                                                                       "notes": [], "pumps": 1}) as built:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            planner.world = lambda s: world
            self.assertEqual(planner(short)["purpose"], "nuclear-power")
            from companion.catalog_planner import Scout
            with self.assertRaises(Scout) as sent:   # (no uranium among its patches nor on the map: it goes looking)
                planner({**short, "known_patches": []})
            self.assertEqual((sent.exception.purpose, sent.exception.resource), ("nuclear-power", "uranium-ore"))
        built.assert_called_once()

    def test_offered_once_the_draw_ahead_passes_most_of_the_capacity(self):
        from companion import power_outlook
        from companion.catalog_planner import PURPOSES, URGENT, available, purpose_options
        recipes = [{"name": n} for n in PURPOSES["nuclear-power"]["requires_recipes"]] + [
            {"name": "electronic-circuit", "energy": 0.5, "category": "crafting",
             "products": [{"name": "electronic-circuit", "amount": 1}]}]
        facts = {**FACTS, "entities": {**FACTS["entities"],
                                       "boiler": {**FACTS["entities"]["boiler"]},
                                       "assembling-machine-2": {"kind": "crafter", "source": "electric", "kw": 150,
                                                                "speed": 0.75, "categories": ["crafting"],
                                                                "ready": True}}}
        base = [{"purpose": "steam-power", "machines": {"boiler": 2, "steam-engine": 4}},     # 3.6 MW
                {"purpose": "red-circuits", "machines": {"assembling-machine-2": 12}}]          # 1.8 MW
        site = {"request_id": "1:60", "purpose": "choose", "recipes": recipes, "resources": [], "game_facts": facts,
                "known_patches": [{"resource": "uranium-ore", "tiles": 400}], "base": base,
                "completed_purposes": ["steam-power"], "origin": {"x": 0, "y": 0}}
        got = power_outlook.outlook(site)
        self.assertEqual((got["capacity_kw"], got["draw_kw"]), (3600, 1800))
        self.assertFalse(available("nuclear-power", site))                 # (half: room yet)
        # the factory plan's next section: 15 circuits a second, 10 machines of 1.5 a second, 1.5 MW more
        ahead = {**site, "factory_plan": {"version": 1, "sections": [{"id": "circuits", "status": "planned",
                                                         "makes": {"electronic-circuit": 15}}]}}
        self.assertAlmostEqual(power_outlook.outlook(ahead)["planned_kw"], 1500)
        self.assertTrue(power_outlook.due(ahead))                           # (3.3 of 3.6 MW)
        self.assertTrue(available("nuclear-power", ahead))
        self.assertIn("nuclear-power", [o["id"] for o in purpose_options(ahead)])
        self.assertIn("nuclear-power", URGENT)   # (urgent only while power is short: weighed here, not first)
        self.assertIsNone(power_outlook.outlook({**site, "base": []}))     # (no generator: nothing to measure)
        # chosen ahead of the factory plan's next section, though power is not short yet
        import tempfile
        from companion import blueprints as bp
        from companion.catalog_planner import CatalogPlanner
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            self.assertEqual(planner.choose_purpose(ahead), "nuclear-power")
        # solar panels give power too (counted, not sampled: base_memory.lua), a planned mining section draws
        sunny = {**ahead, "game_facts": {**facts, "entities": {**facts["entities"],
                                                                "solar-panel": {"kind": "solar", "max_kw": 60}}},
                 "base": base + [{"purpose": "solar", "machines": {"solar-panel": 30}}]}
        self.assertEqual(power_outlook.outlook(sunny)["capacity_kw"], 3600 + 1800)
        mine = {**site, "game_facts": {**facts, "entities": {**facts["entities"], "electric-mining-drill": {
            **facts["entities"]["electric-mining-drill"], "source": "electric", "kw": 90,
            "categories": ["basic-solid"]}}},
            "resources": [{"resource": "iron-ore"}],
            "factory_plan": {"version": 1, "sections": [{"id": "iron-mine", "status": "planned",
                                                          "makes": {"iron-ore": 5, "utility-science-pack": 1}}]}}
        # (0.5 ore a second a drill; a pack whose recipe is locked yet is no mining)
        self.assertAlmostEqual(power_outlook.outlook(mine)["planned_kw"], 10 * 90)

    def test_without_the_pieces_connections_there_is_no_plant(self):
        from companion import layout_planner, nuclear_plant
        site, world = plant_site()
        del site["game_facts"]["entities"]["heat-exchanger"]["heat"]
        with self.assertRaises(layout_planner.PlanError):
            nuclear_plant.plan(site, world)

    def test_pieces_joined_otherwise_are_no_plant_not_a_crash(self):
        from companion import layout_planner, nuclear_plant
        site, world = plant_site()
        turbine = site["game_facts"]["entities"]["steam-turbine"]
        turbine["fluid"] = [{"filter": "steam", "c": turbine["fluid"][0]["c"][:1]}]   # (a one-ended turbine)
        with self.assertRaises(layout_planner.PlanError):
            nuclear_plant.local_plant(site)
        site, _ = plant_site()   # (steam in one box, water out another: no steam passes on to the next turbine)
        turbine = site["game_facts"]["entities"]["steam-turbine"]
        ends = turbine["fluid"][0]["c"]
        turbine["fluid"] = [{"filter": "steam", "c": ends[:1]}, {"filter": "water", "c": ends[1:]}]
        with self.assertRaises(layout_planner.PlanError):
            nuclear_plant.local_plant(site)
        site, _ = plant_site()
        site["game_facts"]["entities"]["heat-exchanger"]["fluid"] = [{"filter": None, "c": [[0, -0.5, 0]]}]
        with self.assertRaises(layout_planner.PlanError):
            nuclear_plant.local_plant(site)

    def test_too_few_offshore_pumps_is_no_plant(self):
        from unittest import mock
        from companion import layout_planner, nuclear_plant, rocket_plan
        site, world = plant_site()
        real, calls = rocket_plan._pump, []

        def one_shore_spot(*a):
            calls.append(1)
            return real(*a) if len(calls) == 1 else None
        with mock.patch.object(rocket_plan, "_pump", one_shore_spot), \
                self.assertRaisesRegex(layout_planner.PlanError, "only 1 of the 2 offshore pumps"):
            nuclear_plant.plan(site, world)


if __name__ == "__main__":
    unittest.main()
