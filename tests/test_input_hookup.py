"""A line's inputs by belt from the machines that make them, never only by hand (F-20)."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import input_hookup as hook  # noqa: E402
from companion.modules import EAST, Placed  # noqa: E402
from companion.ratios import Book  # noqa: E402

BOUNDS = (-40, -40, 40, 40)


def world(*machines):
    return {"force": "f", "entities": [{"n": "assembling-machine-2", "t": "assembling-machine", "r": r, "f": "f",
                                        "x": x, "y": y, "w": 3, "h": 3} for r, x, y in machines]}


class DedicatedTests(unittest.TestCase):
    """D-19: a defense row's plates, with no bus lane of them yet, from the base's own furnaces by belt, not by hand."""

    def test_plates_from_its_furnaces_only_for_a_defense_row_with_no_bus_lane(self):
        book = Book.vanilla()
        w = {"force": "f", "entities": [{"n": "stone-furnace", "t": "furnace", "r": "iron-plate", "f": "f",
                                         "x": 10.0, "y": 0.0, "w": 2, "h": 2}]}
        plain = hook.sources_for("iron-plate", {}, w, [], book, {}, None, {})
        self.assertEqual(plain, [])                                          # (option B: the bus's lane only)
        token = hook.DEDICATED.set(True)
        try:
            mine = hook.sources_for("iron-plate", {}, w, [], book, {}, None, {})
        finally:
            hook.DEDICATED.reset(token)
        self.assertEqual(len(mine), 1)
        token = hook.DEDICATED.set(True)
        try:   # (a bus lane carries it, none spare now: still the lane's, never its furnaces; NPC Factory)
            lane = hook.sources_for("iron-plate", {"belt_lines": [{"kind": "bus", "item": "iron-plate"}]}, w, [], book,
                                    {}, None, {})
        finally:
            hook.DEDICATED.reset(token)
        self.assertEqual(lane, [])
        fed = hook.feed("iron-plate", 0.2, (0, 0), EAST, sources=mine, taken=set(), bounds=BOUNDS)
        self.assertTrue(fed.enough, fed.why)
        self.assertTrue(any(e.name == "inserter" for e in fed.entities))


    def test_one_furnace_is_not_given_to_two_ports(self):
        # (Codex's D-19 review: two 0.2/s ports both on one 0.3125/s furnace)
        from companion.modules import Module, Port, NORTH

        def furnace(x):
            return {"n": "stone-furnace", "t": "furnace", "r": "iron-plate", "f": "f", "x": x, "y": 0.0, "w": 2, "h": 2}

        def two_ports(*xs):
            m = Module("row")
            m.ports += [Port("iron-plate", "in", 0, -6, NORTH, 0.2), Port("iron-plate", "in", 0, 6, NORTH, 0.2)]
            token = hook.DEDICATED.set(True)
            try:
                return hook.hook_rows([(m, [], (0, 0))], {"force": "f", "entities": [furnace(x) for x in xs]},
                                      Book.vanilla(), set(), BOUNDS)
            finally:
                hook.DEDICATED.reset(token)
        alone = two_ports(10.0)
        self.assertEqual(len(alone.ports.get(0) or ()), 1, alone.notes)   # (one furnace: one port, the other by hand)
        both = two_ports(10.0, 30.0)
        self.assertEqual(len(both.ports.get(0) or ()), 2, both.notes)
        self.assertTrue(any(e.name == "inserter" and math.floor(e.x) >= 28 for e in both.entities[0]))   # (the far one)


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.book = Book.vanilla()

    def test_an_inserter_at_the_machine_and_a_belt_into_the_port(self):
        w = world(("engine-unit", 10.5, 0.5))
        fed = hook.feed("engine-unit", 0.05, (0, 0), EAST, sources=hook.sources_of("engine-unit", w, [], self.book),
                        taken=set(), bounds=BOUNDS)
        self.assertTrue(fed.enough, fed.why)
        ins = [e for e in fed.entities if e.name == "inserter"]
        belts = [e for e in fed.entities if e.name != "inserter"]
        self.assertEqual(len(ins), 1)
        ix, iy = math.floor(ins[0].x), math.floor(ins[0].y)
        self.assertTrue(9 - 1 <= ix <= 11 + 1 and -1 - 1 <= iy <= 1 + 1)       # (beside the machine)
        last = belts[-1]
        self.assertEqual(((math.floor(last.x), math.floor(last.y)), last.direction), ((0, 0), EAST))
        self.assertAlmostEqual(fed.rate, 0.075, places=3)                         # (an assembler 2's engines)

    def test_more_machines_join_the_belt_until_they_cover_it(self):
        w = world(("engine-unit", 10.5, 0.5), ("engine-unit", 10.5, 8.5), ("engine-unit", 10.5, 16.5))
        fed = hook.feed("engine-unit", 0.14, (0, 0), EAST, sources=hook.sources_of("engine-unit", w, [], self.book),
                        taken=set(), bounds=BOUNDS)
        self.assertEqual(len(fed.sources), 2, fed.why)
        self.assertTrue(fed.enough)
        tiles = [(math.floor(e.x), math.floor(e.y)) for e in fed.entities]
        self.assertEqual(len(tiles), len(set(tiles)))                             # (nothing on top of another)

    def test_a_planned_row_s_machine_is_a_source_too(self):
        placed = [Placed("assembling-machine-2", 5.5, 5.5, 0, "engine-unit")]
        self.assertEqual(len(hook.sources_of("engine-unit", world(), placed, self.book)), 1)

    def test_it_says_why_when_nothing_feeds_it(self):
        self.assertIn("nothing here makes", hook.feed("sulfur", 0.1, (0, 0), EAST, sources=[], taken=set(),
                                                      bounds=BOUNDS).why)
        w = world(("engine-unit", 10.5, 0.5))
        walled = {(x, y) for x in range(-40, 41) for y in range(-40, 41) if max(abs(x), abs(y)) in range(3, 9)}
        fed = hook.feed("engine-unit", 0.05, (0, 0), EAST, sources=hook.sources_of("engine-unit", w, [], self.book),
                        taken=walled, bounds=BOUNDS)
        self.assertFalse(fed.ok)
        self.assertIn("no belt route", fed.why)


if __name__ == "__main__":
    unittest.main()


class PlanHookupTests(unittest.TestCase):
    def test_a_blue_line_takes_its_intermediates_by_belt_not_by_hand(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_tier_builds import BLUE, TIERS, researched, site, tier_builds, world
        s = site(tiers=researched(TIERS, "military-2", "military-science-pack", "chemical-science-pack"),
                 request_id="1:7", origin={"x": -32, "y": -32}, machines={"assembling-machine-2": {}})
        from companion import layout_planner
        with self.assertRaises(layout_planner.PlanError):   # (a bus builder: no row fed plates by hand, F-21)
            tier_builds.plan(s, world(), BLUE)
        s = {**s, "personality": {"traits": ["chaotic"]}}   # (one who never builds a bus: the hand bridges)
        plan = tier_builds.plan(s, world(), BLUE)
        hand = {x["item"] for st in plan["stages"] for x in st["manual_supplies"]}
        for item in ("engine-unit", "advanced-circuit", "sulfur", "plastic-bar"):
            self.assertNotIn(item, hand)
            self.assertIn(item, plan["tier"]["delivered"])
        self.assertNotIn("copper-plate", plan["tier"]["delivered"])     # (plates by belt only from the bus: F-20 B)
        self.assertTrue(any("no bus lane carries copper-plate yet" in n for n in plan["notes"]))   # (said)
        tiles = [(e["position"]["x"], e["position"]["y"]) for e in plan["entities"]]
        self.assertEqual(len(tiles), len(set(tiles)))                    # (no feed on top of anything)
        ids = [e["id"] for e in plan["entities"]]
        self.assertEqual(len(ids), len(set(ids)))


class LaneEndTests(unittest.TestCase):
    """Plates from the bus's lane ends, within the lane's spare as the lane ledger has it."""

    def book_and_world(self, claims):
        from companion import lane_ledger
        line = {"id": "iron-plate@0,0", "item": "iron-plate", "kind": "bus", "ok": True, "supply": 7.5,
                "tiles": [[0, 0, 8], [0, 20, 8]], "claims": claims}
        w = {"force": "f", "entities": [{"n": "iron-chest", "t": "container", "f": "f", "x": 0.5, "y": 21.5,
                                         "w": 1, "h": 1}]}
        return lane_ledger.LaneBook.from_site({}, {"belt_lines": [line]}), w

    def test_an_inserter_unloads_the_lane_s_end_chest_onto_the_feed(self):
        lanes, w = self.book_and_world({"a@1,3": 2.0})
        ends = hook.lane_ends(w, lanes)
        self.assertEqual(ends["iron-plate"][0][:2], ("iron-plate@0,0", (0, 21)))
        src = hook._ends("iron-plate", ends, lanes, {})
        fed = hook.feed("iron-plate", 0.5, (10, 21), EAST, sources=src, taken={(0, y) for y in range(21)},
                        bounds=BOUNDS)
        self.assertTrue(fed.enough, fed.why)
        self.assertEqual(fed.used[0].grant, "iron-plate@0,0")

    def test_a_lane_with_no_spare_gives_nothing(self):
        lanes, w = self.book_and_world({"a@1,3": 7.5})
        self.assertEqual(hook.lane_ends(w, lanes), {})
        self.assertTrue(hook.from_bus("iron-plate", Book.vanilla(), lanes))      # (a lane carries it: not its furnaces)
        self.assertFalse(hook.from_bus("engine-unit", Book.vanilla(), lanes))


class PowerTests(unittest.TestCase):
    def test_a_pole_beside_an_inserter_no_pole_powers(self):
        lift = [Placed("inserter", 50.5, 50.5, 0)]
        poles = hook.poles_for(lift, [(0.5, 0.5)], set())
        self.assertEqual(len(poles), 1)
        self.assertEqual(hook.unpowered(lift, [(p.x, p.y) for p in poles]), [])
        self.assertEqual(hook.poles_for(lift, [(51.5, 51.5)], set()), [])


class RoundTwoTests(unittest.TestCase):
    def test_a_lab_s_pack_port_at_no_rate_still_gets_a_feed(self):
        w = world(("chemical-science-pack", 10.5, 0.5))
        fed = hook.feed("chemical-science-pack", 0.0, (0, 0), EAST,
                        sources=hook.sources_of("chemical-science-pack", w, [], Book.vanilla()), taken=set(),
                        bounds=BOUNDS)
        self.assertTrue(fed.enough, fed.why)

    def test_one_end_chest_feeds_no_more_than_its_inserter_brings_and_each_is_claimed(self):
        lanes, w = LaneEndTests().book_and_world({})
        ends = hook.lane_ends(w, lanes)
        drawn = {}
        first = hook.feed("iron-plate", 0.6, (10, 21), EAST, sources=hook._ends("iron-plate", ends, lanes, drawn),
                          taken={(0, y) for y in range(21)}, bounds=BOUNDS)
        self.assertIsNone(hook._grant(first, "a", 0.6, lanes, drawn))
        self.assertEqual(first.lifts[0].line_op["op"], "tap")                  # (told to the game)
        self.assertEqual(lanes.lane("iron-plate@0,0").pending, {"a": 0.6})
        second = hook._ends("iron-plate", ends, lanes, drawn)
        self.assertAlmostEqual(second[0].rate, hook.INSERTER_RATE - 0.6)       # (what the chest's filler has left)

    def test_a_medium_pole_s_reach_counts(self):
        lift = [Placed("inserter", 3.5, 0.5, 0)]
        self.assertEqual(hook.unpowered(lift, [(0.5, 0.5, 3.5)]), [])
        self.assertEqual(len(hook.unpowered(lift, [(0.5, 0.5)])), 1)


class RetrofitTests(unittest.TestCase):
    def test_a_standing_line_s_hand_chest_gets_a_belt_and_an_inserter(self):
        from companion import tier_builds
        w = world(("engine-unit", 10.5, 0.5))
        w.update({"area": [-40, -40, 40, 40], "home": {"x": 0, "y": 0},
                  "entities": w["entities"] + [{"n": "small-electric-pole", "t": "electric-pole", "f": "f",
                                                "x": -3.5, "y": 0.5, "w": 1, "h": 1},
                                               {"n": "iron-chest", "t": "container", "f": "f", "x": -5.5, "y": 0.5,
                                                "w": 1, "h": 1}]})
        site = {"origin": {"x": 0, "y": 0}, "request_id": "1:9",
                "base": [{"id": "blue-1", "hand_chests": [{"items": ["engine-unit"], "x": -6, "y": 0}],
                          "capacity": {"takes": {"engine-unit": 0.05}}}]}
        plan = tier_builds.retrofit(site, w)
        self.assertIsNotNone(plan)
        ins = [e for e in plan["entities"] if e["prototype"] == "inserter"]
        self.assertEqual(len(ins), 2)                                         # (one at the machine, one at the chest)
        near = [e for e in ins if abs(e["position"]["x"] + 5.5) + abs(e["position"]["y"] - 0.5) == 1]
        self.assertEqual(len(near), 1)                                        # (beside the chest)
        self.assertIn("engine-unit", plan["tier"]["delivered"])

    def test_nothing_to_retrofit_is_no_plan(self):
        from companion import tier_builds
        self.assertIsNone(tier_builds.retrofit({"base": [{"id": "a"}]}, {"area": [0, 0, 1, 1], "entities": []}))


class OldBusTests(unittest.TestCase):
    def test_a_bus_built_before_its_lanes_were_recorded_has_them_laid_out_again(self):
        import copy
        sys.path.insert(0, str(ROOT / "tests"))
        from test_belt_lines import fixture
        site, w = fixture()
        site = copy.deepcopy(site)
        site.pop("belt_lines", None)
        site["base_plan"].setdefault("done", {}).update({"smelting-copper-plate": 1, "bus-gears": 1})
        lanes = hook.bus_book(site, w).lanes()
        self.assertTrue(any(l.line.kind == "bus" and l.item == "iron-plate" for l in lanes))
        self.assertTrue(all(l.carry_from == "design" for l in lanes if l.line.kind == "bus"))


class LaneTests(unittest.TestCase):
    def test_two_items_on_one_entry_each_side_load_onto_their_own_lane(self):
        from companion.modules import Module, Port, NORTH
        m = Module("block")
        a, b = Port("engine-unit", "in", 0, 0, NORTH, 0.05), Port("sulfur", "in", 0, 0, NORTH, 0.05)
        a.lane, b.lane = "left", "right"
        m.ports += [a, b]
        w = world(("engine-unit", -10.5, 0.5), ("sulfur", 10.5, 0.5))
        w["entities"][1]["n"] = "chemical-plant"
        out = hook.hook_rows([(m, [], (0, 0))], w, Book.vanilla(), set(), BOUNDS)
        self.assertEqual(out.ports.get(0), {0, 1}, out.notes)
        last = {}
        for e in out.entities[0]:
            if e.name == "transport-belt" and abs(math.floor(e.x)) == 1 and math.floor(e.y) == 0:
                last[math.floor(e.x)] = e.direction
        self.assertEqual(last, {-1: EAST, 1: hook.WEST})            # (left from the west, right from the east)

    def test_two_items_with_no_lane_each_stay_by_hand(self):
        from companion.modules import Module, Port, NORTH
        m = Module("block")
        m.ports += [Port("engine-unit", "in", 0, 0, NORTH, 0.05), Port("sulfur", "in", 0, 0, NORTH, 0.05)]
        out = hook.hook_rows([(m, [], (0, 0))], world(), Book.vanilla(), set(), BOUNDS)
        self.assertFalse(out.ports)
        self.assertTrue(any("no lane each" in n for n in out.notes))


class LaneFirstTests(unittest.TestCase):
    def test_a_line_whose_plates_have_no_bus_lane_is_not_taken_until_one_stands(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_tier_builds import site
        from companion import tier_builds
        build = tier_builds.Build("engine-unit", 0.1, "x")
        s = site(tick=1000, npc_id=7, machines={"assembling-machine-2": {}})
        self.assertIn(tier_builds.lane_first(s, build), ("iron-plate", "steel-plate"))
        later = {**s, "tick": 10 ** 7}
        self.assertIsNotNone(tier_builds.lane_first(later, build))         # (no hand bridge, however long: LIVE-7c)
        smelted = {**s, "base_plan": {"done": {"smelting-iron-plate": 1, "smelting-steel-plate": 1}}}
        self.assertIsNotNone(tier_builds.lane_first(smelted, build))        # (its smelting alone lays no lane)
        laned = {**s, "base_plan": {"done": {"smelting-iron-plate": 1, "smelting-steel-plate": 1, "bus-gears": 1}}}
        self.assertIsNone(tier_builds.lane_first(laned, build))             # (its lanes stand)
        self.assertIsNone(tier_builds.lane_first({**s, "failed_purposes": ["bus-base"]}, build))

    def test_a_retrofit_waits_too_when_only_a_lane_could_feed_it(self):
        from companion import tier_builds
        hand = {"base": [{"id": "g", "hand_chests": [{"items": ["copper-plate"], "x": 0, "y": 0}]}]}
        self.assertFalse(tier_builds.retrofit_due(hand))                     # (copper: no lane yet)
        self.assertTrue(tier_builds.retrofit_due({**hand, "base_plan": {"done": {"smelting-copper-plate": 1,
                                                                                  "bus-gears": 1}}}))
        cables = {"base": [{"id": "g", "hand_chests": [{"items": ["copper-cable"], "x": 0, "y": 0}]}]}
        self.assertTrue(tier_builds.retrofit_due(cables))                    # (a machine makes it: belts now)


class FeedCheckTests(unittest.TestCase):
    def test_an_imported_block_fed_at_its_edge_is_not_a_catalog_build(self):
        from types import SimpleNamespace
        from companion import build_choice
        belted = {"id": "x", "size": [5, 5],
                  "entities": [{"prototype": "transport-belt", "position": {"x": 0.5, "y": 2.5}, "direction": 4}],
                  "import": {"ports": [{"way": "in", "kind": "belt", "item": "iron-plate", "edge": "west",
                                        "x": 0.5, "y": 2.5},
                                       {"way": "out", "kind": "belt", "item": "automation-science-pack"}]}}
        found = build_choice.feed(None, SimpleNamespace(build=belted))
        self.assertTrue(found and found[0].blocks)
        self.assertEqual(build_choice.feed(None, SimpleNamespace(build={"id": "y"})), [])   # (its own, chest-fed)
        along = {**belted, "entities": [{"prototype": "transport-belt", "position": {"x": 0.5, "y": 2.5},
                                         "direction": 0}]}   # (north along its west edge: its own drills' belt)
        self.assertEqual(build_choice.feed(None, SimpleNamespace(build=along)), [])
        undergrounds = {"id": "u", "entities": [{"prototype": "assembling-machine-2", "recipe": "iron-gear-wheel",
                                                 "position": {"x": 2.5, "y": 2.5}}],
                        "import": {"ports": [{"way": "out", "kind": "belt", "item": "iron-gear-wheel"}]}}
        job = SimpleNamespace(site={})
        self.assertTrue(build_choice.feed(job, SimpleNamespace(build=undergrounds)))   # (its plates from outside)
        chested = {**undergrounds, "entities": undergrounds["entities"] + [{"prototype": "iron-chest",
                                                                           "position": {"x": 0.5, "y": 0.5}}]}
        self.assertEqual(build_choice.feed(job, SimpleNamespace(build=chested)), [])     # (a chest: hand-filled)
        piped = {"id": "z", "entities": [], "import": {"ports": [{"way": None, "kind": "fluid", "item": None}]}}
        self.assertTrue(build_choice.feed(None, SimpleNamespace(build=piped)))      # (crude or water from outside)


class BusWillMakeTests(unittest.TestCase):
    def test_the_science_path_waits_for_the_bus_s_own_section_of_its_item(self):
        from companion import tier_builds
        build = tier_builds.Build("electronic-circuit", 0.25, "x")
        site = {"base_plan": {"stages": ["power", "feed-copper-plate", "bus-gears", "bus-green-circuits"],
                              "done": {"power": 1, "feed-copper-plate": 1, "bus-gears": 1}}}
        self.assertEqual(tier_builds.bus_will_make(site, build), "bus-green-circuits")
        built = {"base_plan": {**site["base_plan"], "done": {**site["base_plan"]["done"], "bus-green-circuits": 1}}}
        self.assertIsNone(tier_builds.bus_will_make(built, build))
        self.assertIsNone(tier_builds.bus_will_make({**site, "failed_purposes": ["bus-base"]}, build))
        self.assertIn("green-circuits section", tier_builds._waited("bus-green-circuits"))
