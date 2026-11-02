"""Plates kept for building while the base grows (companion/construction.py), and the bus building the rest of a
smelting block next when what stands cannot cover its machines and the building still to do (run 32, 2026-10-25:
the whole bus went up on nine iron furnaces sized for science alone)."""

import math
import unittest
from unittest.mock import patch

from companion import bus_planner, construction, growth
from companion.base_plan import Layout, Stage
from companion.bus import Bus
from companion.modules import EAST, SOUTH, Placed
from companion.ratios import Book


class AllowanceTests(unittest.TestCase):
    def test_building_costs_come_down_to_plates_through_recipes(self):
        book = Book.vanilla()
        self.assertEqual(construction.cost(["transport-belt", "transport-belt"], book), {"iron-plate": 3.0})
        drill = construction.cost(["electric-mining-drill"], book)
        self.assertEqual((drill["iron-plate"], drill["copper-plate"]), (23.0, 4.5))
        self.assertEqual(construction.cost(["no-such-building"], book), {})

    def test_the_allowance_is_the_floor_a_share_of_the_machines_or_the_stages_to_build_capped(self):
        with patch.object(construction, "expansion", return_value=1.0):
            self.assertEqual(construction.allowance({}, {}, {}), {"iron-plate": 1.0, "copper-plate": 0.5})
            self.assertEqual(construction.allowance({}, {"iron-plate": 4.0}, {})["iron-plate"], 2.0)   # half again
            upcoming = {"iron-plate": 0.9 * construction.HORIZON_S * 4, "steel-plate": 360}
            kept = construction.allowance({}, {}, upcoming)
            self.assertEqual(kept["iron-plate"], 3.6)
            self.assertEqual(kept["steel-plate"], 0.2)                                   # any plate it builds with
            self.assertEqual(construction.allowance({}, {}, {"iron-plate": 10 ** 6})["iron-plate"], construction.CAP)
        with patch.object(construction, "expansion", return_value=0.0):   # a full emergency: defense first
            self.assertEqual(construction.allowance({}, {"iron-plate": 4.0}, {"iron-plate": 9000}), {})

    def test_a_supply_is_short_when_its_machines_and_building_pass_its_headroom(self):
        self.assertTrue(construction.short(1.0, 0.5, 0.5))
        self.assertFalse(construction.short(2.0, 0.5, 0.5))

    def test_options_for_one_build_are_weighed_by_plates_and_the_walk_not_pieces(self):
        # (run 57's map, 2026-10-26: a boiler row's water piped 390 tiles under ground beat a belt on pieces)
        from companion.base_plan import UNKNOWN_PLATES, WALK_PLATES, build_cost
        book = Book.from_site({"recipes": [{"name": "pipe-to-ground", "category": "crafting", "energy": 0.5,
                                            "ingredients": [{"name": "pipe", "amount": 10},
                                                            {"name": "iron-plate", "amount": 5}],
                                            "products": [{"name": "pipe-to-ground", "amount": 2}]}]})
        # (both lines end the same distance out: only what they take to make differs)
        pipes = [Placed("pipe-to-ground", 70.5 - 10 * i, 0.5, EAST) for i in range(8)]
        belts = [Placed("transport-belt", 70.5 - i, 0.5, EAST) for i in range(60)]
        self.assertAlmostEqual(build_cost(pipes, book, (0, 0)) - build_cost(belts, book, (0, 0)), 8 * 7.5 - 60 * 1.5)
        self.assertAlmostEqual(build_cost(belts[:1], book, (0, 0)), 1.5 + WALK_PLATES * math.hypot(70.5, 0.5))
        # (a building the book has no recipe for still costs something)
        self.assertAlmostEqual(build_cost(pipes[:1], Book.vanilla(), (0, 0)) - WALK_PLATES * math.hypot(70.5, 0.5),
                               sum(construction.cost(["pipe-to-ground"], Book.vanilla()).values()) or UNKNOWN_PLATES)


def furnaces(n, x=0):
    return [Placed("stone-furnace", x + 1, 2 * i + 1) for i in range(n)]


def belts(n):
    return [Placed("transport-belt", 40.5, y + .5, SOUTH) for y in range(n)]


def layout(first=3, mall_belts=400):
    return Layout(Bus(0, 0, 2, 20), [Stage("power", []), Stage("smelting-iron-plate", furnaces(first)),
                                     Stage("bus-gears", belts(mall_belts)),
                                     Stage("smelting-iron-plate-more", furnaces(20, x=10))])


class SmeltingFirstTests(unittest.TestCase):
    def choose(self, plan, done, expansion=1.0, spm=5.0):
        with patch.object(construction, "expansion", return_value=expansion):
            return bus_planner.smelting_first({}, plan, done, Book.vanilla(), spm,
                                              ("automation-science-pack",), "assembling-machine-2")

    def test_the_rest_of_a_block_comes_next_while_growing_on_too_little_smelting(self):
        # (only once the bus's first segment stands: its first lane goes in thin before, F-21)
        self.assertEqual(self.choose(layout(), {"power": 1, "smelting-iron-plate": 1}), (None, ""))
        stage, why = self.choose(layout(), {"power": 1, "smelting-iron-plate": 1, "bus-gears": 1, "bus-labs": 1})
        self.assertEqual(stage.name, "smelting-iron-plate-more")
        self.assertIn("iron-plate runs 56 a minute", why)
        self.assertTrue(why.startswith("more smelting first"))   # (a note naming a stage first is its failure)

    def test_not_before_its_first_stage_nor_in_an_emergency_that_science_alone_does_not_need(self):
        self.assertEqual(self.choose(layout(), {"power": 1}), (None, ""))
        self.assertEqual(self.choose(layout(), {"power": 1, "smelting-iron-plate": 1}, expansion=0.0), (None, ""))

    def test_not_when_what_stands_covers_its_machines_and_building(self):
        self.assertEqual(self.choose(layout(first=24, mall_belts=10), {"power": 1, "smelting-iron-plate": 1}),
                         (None, ""))

    def test_the_bus_plans_that_stage_next_and_says_why(self):
        plan = layout()
        world = {"entities": [], "resources": {}}
        site = {"base_plan": {"stages": [s.name for s in plan.stages],
                              "done": {"power": 1, "smelting-iron-plate": 1, "bus-gears": 1, "bus-labs": 1},
                              "x": 0, "y": 0, "columns": 2, "spm": 5}}
        with patch.object(bus_planner, "plan_bus_base", return_value=plan), \
                patch.object(construction, "expansion", return_value=1.0):
            out = bus_planner.plan_stage(site, world, Book.vanilla())
        self.assertEqual(out["stages"][0]["id"], "smelting-iron-plate-more")
        self.assertTrue(any(n.startswith("more smelting first") for n in out["notes"]))


@patch("companion.base_plan.FIRST_FURNACES", 48)   # (a block with nothing left to finish: no thin first stage, F-21)
class ExpansionTests(unittest.TestCase):
    """A plate still short once its block is built gets a resource expansion: a full belt more (plate modules beside
    the bus, each with its own ore field and coal belt; where the bus's column has no room, an outpost by an ore
    patch), half of one where a full belt's supply does not fit, searched in the background, and built to the end
    before anything else on the bus."""

    def setUp(self):
        bus_planner._searches.clear()
        self.wait = patch.object(bus_planner, "EXPANSION_WAIT_S", 600)   # (the search, as if it answered at once)
        self.wait.start()
        self.addCleanup(self.wait.stop)

    def bw(self):
        import json
        from pathlib import Path
        cap = json.loads((Path(__file__).parent / "fixtures" / "bw-tight-bus.json").read_text(encoding="utf-8"))
        return cap["site"], cap["world"]

    def test_an_expansion_goal_names_its_plate_index_and_rate_and_its_modules_fill_that(self):
        from companion import outpost
        goal = growth.expansion_name("iron-plate", 2)
        self.assertEqual((goal, growth.parse_expansion(goal), growth.expansion_tag(goal)),
                         ("expand-iron-plate-2-15", ("iron-plate", 2, 15.0), "-x2"))
        full, half = growth.expansion(goal), growth.expansion(growth.expansion_name("copper-plate", 3, 7.5))
        self.assertEqual([m.name for m in full], ["smelt-iron-plate-x2", "smelt-iron-plate-x2-2"])
        self.assertEqual([m.name for m in half], ["smelt-copper-plate-x3"])
        self.assertTrue(growth.in_section("supply-iron-plate-x2-2", "-x2"))
        self.assertFalse(growth.in_section("bus-smelt-iron-plate-x20", "-x2"))
        far = outpost.name("iron-plate", 3, 7.5)
        self.assertEqual((far, outpost.parse(far), outpost.tag(far), len(outpost.modules(far))),
                         ("outpost-iron-plate-3-7.5", ("iron-plate", 3, 7.5), "-x3", 1))

    def test_a_short_plate_with_no_block_left_to_finish_gets_a_belt_more_built_first(self):
        # run 33's bus (2026-10-25): a tight bus on a cramped plot, its iron block nine furnaces with nothing left
        site, world = self.bw()
        # (its column runs into cliffs below the bus: the ground is taken as clear here, the cliffs in the next test)
        with patch.object(construction, "expansion", return_value=1.0), \
                patch.object(bus_planner, "_obstructed", return_value=None):
            plan = bus_planner.plan_stage(site, world, Book.from_site(site))
        self.assertEqual(plan["base_plan"]["extra"], ["expand-iron-plate-1-15"])   # a full belt more
        self.assertEqual(plan["stages"][0]["id"], "supply-iron-plate-x1")          # its ore field and coal first
        self.assertTrue(any(n.startswith("expanding iron-plate first: 15 a second more on the bus")
                            for n in plan["notes"]))
        # the next stage is the expansion's next one, whatever else is waiting
        saved = {**plan["base_plan"], "done": {**site["base_plan"]["done"], "supply-iron-plate-x1": 1}}
        with patch.object(construction, "expansion", return_value=1.0), \
                patch.object(bus_planner, "_obstructed", return_value=None):
            more = bus_planner.plan_stage({**site, "base_plan": saved}, world, Book.from_site(site))
        self.assertTrue(growth.in_section(more["stages"][0]["id"], "-x1"))
        self.assertTrue(any(n.startswith("finishing the iron-plate expansion 1") for n in more["notes"]))

    def test_with_cliffs_on_the_bus_s_column_the_expansion_is_an_outpost_by_an_ore_patch(self):
        # (run 33's plot: the bus's column runs into cliffs below its labs; a review of the first version found the
        # bus would have stalled on the blocked modules, 2026-10-25)
        site, world = self.bw()
        with patch.object(construction, "expansion", return_value=1.0):
            plan = bus_planner.plan_stage(site, world, Book.from_site(site))
        self.assertEqual(plan["base_plan"]["extra"], ["outpost-iron-plate-1-15"])
        self.assertEqual(plan["stages"][0]["id"], "supply-iron-plate-x1")
        self.assertTrue(any("15 a second more by an ore patch" in n for n in plan["notes"]))
        sites = dict(x.split("=", 1) for x in plan["base_plan"]["sites"])
        self.assertIn("outpost-iron-plate-x1", sites)                          # its spot kept for every replay
        layout = bus_planner.replay({**site, "base_plan": plan["base_plan"]}, world, Book.from_site(site))
        smelt = next(s for s in layout.stages if s.name == "outpost-smelt-iron-plate-x1")
        self.assertEqual(sum(1 for e in smelt.entities if e.name == "stone-furnace"), 48)   # a full belt's furnaces
        self.assertEqual(sum(1 for e in smelt.entities if e.name == "iron-chest"), 2)        # its stores
        self.assertIsNone(bus_planner._obstructed(layout, "-x1", world))

    def test_a_built_outpost_stays_where_it_is_on_every_replay(self):
        # (its own buildings stand on its spot once built: the spot kept is laid out again, not searched for)
        site, world = self.bw()
        with patch.object(construction, "expansion", return_value=1.0):
            plan = bus_planner.plan_stage(site, world, Book.from_site(site))
        saved = plan["base_plan"]
        layout = bus_planner.replay({**site, "base_plan": saved}, world, Book.from_site(site))
        ours = [e for s in layout.stages if growth.in_section(s.name, "-x1") for e in s.entities]
        sizes = {"electric-mining-drill": 3, "stone-furnace": 2}
        rows = [{"n": e.name, "t": e.name, "x": e.x, "y": e.y, "d": e.direction, "f": world.get("force"),
                 "w": sizes.get(e.name, 1), "h": sizes.get(e.name, 1)} for e in ours]
        built = {**world, "entities": world["entities"] + rows}
        done = {**saved.get("done", {}), **{s.name: 1 for s in layout.stages if growth.in_section(s.name, "-x1")}}
        again = bus_planner.replay({**site, "base_plan": {**saved, "done": done}}, built, Book.from_site(site))
        smelt = next(s for s in again.stages if s.name == "outpost-smelt-iron-plate-x1")
        self.assertEqual(sum(1 for e in smelt.entities if e.name == "stone-furnace"), 48)
        self.assertFalse(any("expansion 1" in n for n in again.notes))

    def test_the_search_runs_in_the_background_and_its_find_is_laid_out_at_a_later_request(self):
        site, world = self.bw()
        with patch.object(construction, "expansion", return_value=1.0), \
                patch.object(bus_planner, "EXPANSION_WAIT_S", 0.01):
            plan = bus_planner.plan_stage(site, world, Book.from_site(site))
            self.assertTrue(any("being worked out in the background" in n for n in plan["notes"]))
            self.assertEqual(plan["base_plan"]["extra"], [])                   # the bus goes on meanwhile
            job = next(iter(bus_planner._searches.values()))
            job["thread"].join(600)
            later = bus_planner.plan_stage({**site, "tick": site["tick"] + 600}, world, Book.from_site(site))
        self.assertEqual(later["base_plan"]["extra"], ["outpost-iron-plate-1-15"])
        self.assertEqual(bus_planner._searches, {})

    def test_the_head_feeds_the_machines_and_what_is_made_below_only_building(self):
        # nine furnaces at the head (2.8/s): machines taking 2.5/s leave nothing for building; plates made below the
        # machines count for building only
        plan = layout(first=9)
        plan.stages.insert(3, Stage("bus-smelt-iron-plate-x1", furnaces(24, x=30)))
        done = {"power": 1, "smelting-iron-plate": 1}
        with patch.object(construction, "expansion", return_value=1.0), \
                patch.object(construction, "allowance", return_value={"iron-plate": 2.0}):
            short = bus_planner.pressed({}, plan, done, Book.vanilla(), 20.0,
                                        ("automation-science-pack", "logistic-science-pack"), "assembling-machine-2")
            self.assertEqual([(r[0], r[5], r[6]) for r in short], [("iron-plate", True, True)])
            built = bus_planner.pressed({}, plan, {**done, "bus-smelt-iron-plate-x1": 1}, Book.vanilla(), 20.0,
                                        ("automation-science-pack", "logistic-science-pack"), "assembling-machine-2")
            self.assertEqual([(r[0], r[5], r[6]) for r in built], [("iron-plate", True, False)])   # head still short

    def test_an_expansion_that_does_not_fit_is_noted_and_tried_again_later(self):
        from test_factory_plan import WORLD   # (its coal is far, past the base: no expansion's coal belt gets in)

        def column(plate, index):   # (the bus's column only)
            return [(growth.expansion_name(plate, index, r), growth.expansion_tag) for r in (growth.BELT, growth.BELT / 2)]
        site = {"request_id": "1:30", "tick": 500000, "origin": {"x": 0, "y": 0}}
        with patch.object(construction, "expansion", return_value=1.0), \
                patch.object(bus_planner, "_expansion_tries", side_effect=column):
            first = bus_planner.plan_stage(site, WORLD, Book.vanilla())
            saved = {**first["base_plan"], "ahead": 0, "done": {"power": 1, "smelting-iron-plate": 1}}
            site = {**site, "base_plan": saved}
            plan = bus_planner.plan_stage(site, WORLD, Book.vanilla())
            self.assertFalse(any(str(g).startswith("expand-") for g in plan["base_plan"]["extra"]))
            self.assertEqual(plan["base_plan"]["expand_failed"], {"iron-plate": 500000})
            self.assertTrue(any(n.startswith("no room for a iron-plate expansion now") for n in plan["notes"]))
            soon = {**site, "tick": 500000 + 3600, "base_plan": {**plan["base_plan"], "done": saved["done"]}}
            with patch.object(bus_planner, "plan_bus_base", wraps=bus_planner.plan_bus_base) as layouts:
                bus_planner.plan_stage(soon, WORLD, Book.vanilla())
            self.assertEqual(layouts.call_count, 1)   # remembered: the expansion is not searched again yet


if __name__ == "__main__":
    unittest.main()
