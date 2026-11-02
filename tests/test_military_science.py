"""Military (grey) science: labs for every pack, the piercing and pack rows, the bus section, the team
world's goal and research order (companion/military_modules.py, modules.labs, base_plan, catalog_planner)."""
from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import military_modules as mm  # noqa: E402
from companion import modules as mods  # noqa: E402
from companion.modules import EAST, NORTH, SOUTH, WEST  # noqa: E402
from companion.ratios import Book  # noqa: E402
from companion.smelting import footprint  # noqa: E402

STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
# Bob's Warfare as the game exports it: a piercing magazine takes an empty magazine ("empty-recipe").
BOBS = {"recipes": [
    {"name": "empty-recipe", "category": "crafting", "energy": 1,
     "ingredients": [{"name": "iron-plate", "amount": 2}], "products": [{"name": "empty-magazine", "amount": 1}]},
    {"name": "piercing-rounds-magazine", "category": "crafting", "energy": 3,
     "ingredients": [{"name": "empty-magazine", "amount": 1}, {"name": "steel-plate", "amount": 1},
                     {"name": "copper-plate", "amount": 5}],
     "products": [{"name": "piercing-rounds-magazine", "amount": 1}]}]}


def overlaps(module) -> list:
    seen, out = {}, []
    for e in module.entities:
        for t in footprint([e]):
            if t in seen:
                out.append((e.name, seen[t], t))
            seen[t] = e.name
    return out


class LabsTests(unittest.TestCase):
    def test_labs_take_four_and_five_packs_each_inserter_reaching_a_belt_and_a_lab(self):
        packs = ("automation-science-pack", "logistic-science-pack", "military-science-pack",
                 "chemical-science-pack", "production-science-pack")
        for n in (4, 5):
            m = mods.labs(3, packs[:n])
            self.assertEqual(overlaps(m), [])
            self.assertEqual(sorted(p.item for p in m.ports), sorted(packs[:n]))
            belts = {(math.floor(e.x), math.floor(e.y)): e.direction for e in m.entities if e.name == "transport-belt"}
            labs = set().union(*(footprint([e]) for e in m.entities if e.name == "lab"))
            for e in m.entities:
                if "inserter" not in e.name:
                    continue
                reach = 2 if e.name == "long-handed-inserter" else 1
                dx, dy = STEP[e.direction]
                pick = (math.floor(e.x + dx * reach), math.floor(e.y + dy * reach))
                drop = (math.floor(e.x - dx * (reach + 0.2)), math.floor(e.y - dy * (reach + 0.2)))
                self.assertIn(pick, belts, f"{n} packs: {e.name} at {e.x},{e.y} picks from no belt")
                self.assertIn(drop, labs, f"{n} packs: {e.name} at {e.x},{e.y} drops into no lab")
            # each pack's belt, followed from its port, reaches a belt the labs' inserters pick from
            picked = set()
            for e in m.entities:
                if "inserter" in e.name:
                    reach = 2 if e.name == "long-handed-inserter" else 1
                    dx, dy = STEP[e.direction]
                    picked.add(math.floor(e.y + dy * reach))
            for port in m.ports:
                x, y = port.x, port.y
                for _ in range(40):
                    if y in picked and x >= 3:
                        break
                    dx, dy = STEP[belts[(x, y)]]
                    x, y = x + dx, y + dy
                    self.assertIn((x, y), belts, f"{port.item}'s belt ends at {x},{y}")
                self.assertIn(y, picked, f"{port.item} never reaches the labs")

    def test_two_packs_keep_the_old_layout(self):
        m = mods.labs(2, ("automation-science-pack", "logistic-science-pack"))
        self.assertEqual((m.height, [p.y for p in m.ports]), (9, [2, 4]))


class ModuleTests(unittest.TestCase):
    def test_the_piercing_and_pack_rows_from_vanilla_and_from_bobs_recipes(self):
        vanilla = Book.vanilla()
        bobs = Book.from_site(BOBS)
        for book, maker in ((vanilla, "firearm-magazine"), (bobs, "empty-recipe")):
            piercing = mm.piercing_rounds(0.15, book)
            self.assertEqual(overlaps(piercing), [])
            self.assertIn(maker, {e.recipe for e in piercing.entities})
            self.assertEqual({p.item for p in piercing.ports if p.kind == "in"},
                             {"iron-plate", "copper-plate", "steel-plate"})
            self.assertEqual([p.item for p in piercing.ports if p.kind == "out"], ["piercing-rounds-magazine"])
        packs = mm.military_science(0.15, vanilla)
        self.assertEqual(overlaps(packs), [])
        recipes = [e.recipe for e in packs.entities if e.recipe]
        self.assertEqual(recipes[:3], ["grenade", "military-science-pack", "stone-wall"])   # makers either side
        self.assertEqual({p.item for p in packs.ports if p.kind == "in"},
                         {"piercing-rounds-magazine", "iron-plate", "coal", "stone-brick"})
        # handed over by the neighbours, not carried on a belt: grenades west of the pack, walls east
        gaps = [(e.x, e.direction) for e in packs.entities if e.name == "inserter" and 8 < e.y < 9]
        self.assertEqual(sorted(d for _, d in gaps), [EAST, WEST])
        self.assertAlmostEqual(mm.needs(0.15, vanilla)["piercing-rounds-magazine"], 0.075)


class BusTests(unittest.TestCase):
    def test_a_bus_grows_a_military_section_with_its_own_lanes_and_labs_for_every_pack(self):
        from companion.base_plan import plan_bus_base
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        layout = plan_bus_base(world, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=24,
                               extra=("military-science-pack", "chemical-science-pack"), base_labs=False)
        names = [s.name for s in layout.stages]
        for stage in ("lane-coal-military", "lane-stone-brick-military", "bus-steel-military", "bus-piercing",
                      "bus-military-science", "bus-labs-military", "lane-coal", "bus-labs-blue"):
            self.assertIn(stage, names)
        self.assertEqual(layout.notes, [])
        seen = set()
        for e in layout.entities():
            tiles = footprint([e])
            self.assertFalse(tiles & seen, e)
            seen |= tiles
        labs = {a.module.name: [p.item for p in a.module.ports] for a in layout.bus.attached
                if a.module.name.startswith("labs")}
        self.assertEqual(labs["labs-blue"], ["automation-science-pack", "logistic-science-pack",
                                             "military-science-pack", "chemical-science-pack"])
        coal = [c for c, lane in layout.bus.lanes.items() if lane.item == "coal"]
        self.assertEqual(len(coal), 2)   # each section its own


class TeamGoalTests(unittest.TestCase):
    SITE = {"team": {"plot": [-500, -500, 500, 500], "area": [-1000, -1000, 1000, 1000], "no_build": []},
            "completed_purposes": ["red-science-automation", "green-science-automation"],
            "recipes": [{"name": n} for n in ("military-science-pack", "piercing-rounds-magazine", "grenade",
                                              "stone-wall", "assembling-machine-1", "inserter",
                                              "small-electric-pole")]}

    def test_grey_science_is_a_goal_in_the_team_world_only_and_until_the_bus_makes_it(self):
        from companion.catalog_planner import available
        self.assertTrue(available("military-science-automation", self.SITE))
        self.assertFalse(available("military-science-automation", {**self.SITE, "team": None}))
        self.assertFalse(available("military-science-automation",
                                   {**self.SITE, "completed_purposes": ["red-science-automation"]}))
        on_bus = {**self.SITE, "base_plan": {"stages": ["x"], "extra": ["military-science-pack"]}}
        self.assertFalse(available("military-science-automation", on_bus))

    def test_the_team_researches_automation_then_weapons(self):
        # automation first, for red science made by machines (the user, 2026-10-24), then arms
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        options = [{"name": "automation", "early": True}, {"name": "laser-turret", "military": True},
                   {"name": "stone-wall", "military": True}]
        self.assertEqual(planner.choose_research({"request_id": "1:r1", "team": True, "options": options}),
                         "automation")
        self.assertEqual(planner.choose_research({"request_id": "1:r2", "team": True, "options": options[1:]}),
                         "stone-wall")
        self.assertEqual(planner.choose_research({"request_id": "1:r3", "team": True, "options": options[1:2]}),
                         "laser-turret")
        self.assertEqual(planner.choose_research({"request_id": "1:r4", "options": options}), "automation")

    def test_after_the_teams_list_military_research_keeps_its_share_and_the_rest_goes_to_the_rocket(self):
        # (military first always would never reach space: military upgrades do not run out)
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        options = [{"name": "physical-projectile-damage-2", "military": True},
                   {"name": "oil-processing", "rocket_path": True}, {"name": "toolbelt"}]

        def pick(military, onward, evolution=0.0, **extra):
            return planner.choose_research({"request_id": "1:r9", "team": True, "options": options, "tick": 100000,
                                            "researched": {"military": military, "rocket_path": onward},
                                            "threat_board": {"evolution": evolution, "attacks": []}, **extra})
        self.assertEqual(pick(10, 20), "oil-processing")                 # a third military already: on to the rocket
        self.assertEqual(pick(2, 20), "physical-projectile-damage-2")    # under its share: arms
        self.assertEqual(pick(10, 20, evolution=0.9), "physical-projectile-damage-2")   # evolved biters: more arms
        attacked = {"threat_board": {"evolution": 0.0, "attacks": [{"tick": 99000, "x": 0, "y": 0}]}}
        self.assertEqual(pick(10, 20, **attacked), "physical-projectile-damage-2")      # attacked lately: arms first
        self.assertEqual(pick(19, 11, **attacked), "oil-processing")     # (but not past the evolved share)

    def test_research_goes_first_to_what_its_next_goals_wait_on(self):
        # (one-engineer run 2026-10-25: its electric mining and bus waited on research that never came, and
        # it held off building anything for minutes on end)
        from unittest import mock
        from companion import catalog_planner
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        planner._last_site = {"stand-in": True}
        options = [{"name": "physical-projectile-damage-2", "military": True},
                   {"name": "oil-processing", "rocket_path": True, "unlocks": ["oil-refinery"]},
                   {"name": "logistics", "unlocks": ["splitter", "underground-belt"]}]
        request = {"request_id": "1:r9", "team": True, "options": options, "tick": 100000,
                   "researched": {"military": 2, "rocket_path": 20}, "threat_board": {"evolution": 0, "attacks": []}}
        with mock.patch.object(catalog_planner, "wanted_ranks", lambda site: {"splitter": 0, "electric-mining-drill": 1}):
            self.assertEqual(planner.choose_research(request), "logistics")      # (over its military share)
            attacked = {**request, "threat_board": {"evolution": 0, "attacks": [{"tick": 99000, "x": 0, "y": 0}]}}
            self.assertEqual(planner.choose_research(attacked), "physical-projectile-damage-2")   # survival first
            # what its goals wait on before the team's basics (on a fixed list first, arms and green science held
            # the electric mining drill to minute 22, run 35, 2026-10-25)
            first = {**request, "options": [{"name": "automation", "unlocks": ["assembling-machine-1"]}] + options}
            self.assertEqual(planner.choose_research(first), "logistics")
        with mock.patch.object(catalog_planner, "wanted_ranks", lambda site: {}):
            first = {**request, "options": [{"name": "automation", "unlocks": ["assembling-machine-1"]}] + options}
            self.assertEqual(planner.choose_research(first), "automation")       # nothing waited on: the basics
            self.assertEqual(planner.choose_research(request), "physical-projectile-damage-2")   # (its share)

    def test_research_for_the_soonest_goal_first_and_the_cheaper_of_two_it_waits_on(self):
        from unittest import mock
        from companion import catalog_planner
        from companion.catalog_planner import CatalogPlanner
        planner = CatalogPlanner(catalog=None, log=lambda _: None)
        planner._last_site = {"stand-in": True}
        options = [{"name": "logistics", "units": 75, "packs": ["automation-science-pack"],
                    "unlocks": ["splitter", "underground-belt"]},
                   {"name": "automation", "units": 10, "packs": ["automation-science-pack"],
                    "unlocks": ["assembling-machine-1", "long-handed-inserter"]},
                   {"name": "electric-mining-drill", "units": 15, "packs": ["automation-science-pack"],
                    "unlocks": ["electric-mining-drill"]}]
        request = {"request_id": "1:r9", "team": True, "options": options, "tick": 100000,
                   "researched": {"military": 2, "rocket_path": 20}, "threat_board": {"evolution": 0, "attacks": []}}
        # (the electric drill for the next goal; the next after it waits on both belts and long inserters)
        ranks = {"electric-mining-drill": 0, "underground-belt": 1, "long-handed-inserter": 1, "assembling-machine-1": 2}
        with mock.patch.object(catalog_planner, "wanted_ranks", lambda site: ranks):
            self.assertEqual(planner.choose_research(request), "electric-mining-drill")   # the soonest goal's
            rest = {**request, "options": options[:2]}
            self.assertEqual(planner.choose_research(rest), "automation")   # (both wait on the next: the cheaper)


if __name__ == "__main__":
    unittest.main()
