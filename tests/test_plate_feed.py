"""A new bus's first plates from the base's own electric smelting (companion/plate_feed.py), its own smelting later:
the user's smaller first bus, producing sooner (2026-10-26). Run 57's first bus spent 841 of its 2417 buildings on
smelting blocks with long ore and coal lines before any science."""

import unittest
from unittest import mock

from companion import base_plan, belt_lines, bus_planner, plate_feed
from companion.modules import EAST, NORTH, SOUTH, WEST
from companion.ratios import Book
from companion.smelting import footprint
from test_factory_plan import BLUE, WORLD


def belt(x, y, d, force="player"):
    return {"n": "transport-belt", "t": "transport-belt", "x": x + 0.5, "y": y + 0.5, "d": d, "f": force}


def store_world():
    """A store's output belt: west along y=0 from x=20 to x=1, then down at x=0 to the port at (0, 3)."""
    entities = [belt(x, 0, WEST) for x in range(1, 21)] + [belt(0, y, SOUTH) for y in range(0, 3)] + [belt(0, 3, WEST)]
    entities.append(belt(30, 0, WEST, force="enemy"))   # (not the engineer's own)
    return {"force": "player", "area": [-40, -40, 60, 60], "entities": entities, "resources": {}}


class FeedTests(unittest.TestCase):
    def test_the_store_belt_is_traced_back_from_its_port(self):
        line = plate_feed.store_line(store_world(), (0, 3), "iron-plate", 3.75)
        self.assertEqual(line.tiles[-1], (0, 3, WEST))
        self.assertEqual(line.tiles[0], (20, 0, WEST))
        self.assertEqual(len(line.tiles), 24)
        self.assertIsNone(plate_feed.store_line(store_world(), (5, 5), "iron-plate", 3.75))

    def test_a_splitter_on_the_store_belt_and_belts_into_the_path_from_its_side(self):
        world = store_world()
        start = (10, 20)   # the plate path's first tile, heading south
        with belt_lines.taps(True):
            branch = plate_feed.plan(world, "iron-plate", (0, 3), 3.75, start, {start, (10, 19)})
        self.assertEqual(branch[0].name, "splitter")
        self.assertIsNone(getattr(branch[0], "line_op", None))       # (no claim recorded on a traced belt)
        end = branch[-1]
        self.assertIn(((int(end.x), int(end.y)), end.direction), (((9, 20), EAST), ((11, 20), WEST)))
        world["entities"].append(belt(int(end.x), int(end.y), end.direction))
        self.assertTrue(plate_feed.built(world, start))
        with belt_lines.taps(False):   # (no splitters researched: no feed)
            self.assertIsNone(plate_feed.plan(store_world(), "iron-plate", (0, 3), 3.75, start, set()))

    def test_a_feed_is_one_long_search_shared_by_both_sides_of_the_path(self):
        seen = []

        def fake(dump, item, entry, rate, blocked, *, last, key, among, budget, stats):
            seen.append(budget)
            stats["searched"] = 1_000_000
            return None
        with mock.patch.object(belt_lines, "tap", fake):
            self.assertIsNone(plate_feed.plan(store_world(), "iron-plate", (0, 3), 3.75, (10, 20), set()))
            left = plate_feed.FEED_SEARCH - 1_000_000
            self.assertEqual(seen, [(plate_feed.FEED_SEARCH, plate_feed.FEED_SEARCH), (left, left)])
            # One bus plan (its layouts tried in turn): one budget for a plate's feed searches, another plate its own.
            seen.clear()
            with plate_feed.searching():
                plate_feed.plan(store_world(), "iron-plate", (0, 3), 3.75, (10, 20), set())
                plate_feed.plan(store_world(), "iron-plate", (0, 3), 3.75, (10, 20), set())
                plate_feed.plan(store_world(), "copper-plate", (0, 3), 2.5, (10, 20), set())
            self.assertEqual(seen, [(plate_feed.FEED_SEARCH, plate_feed.FEED_SEARCH), (left, left),
                                    (plate_feed.FEED_SEARCH, plate_feed.FEED_SEARCH), (left, left)])
        self.assertGreater(plate_feed.FEED_SEARCH, belt_lines.LINE_SEARCH)   # (past a tap's: a way round)

    def test_a_way_found_is_taken_again_by_the_next_layout_where_its_belts_are_clear(self):
        world, start = store_world(), (10, 20)
        with belt_lines.taps(True), plate_feed.searching():
            branch = plate_feed.plan(world, "iron-plate", (0, 3), 3.75, start, {start})
            with mock.patch.object(belt_lines, "tap", side_effect=AssertionError("searched again")):
                again = plate_feed.plan(world, "iron-plate", (0, 3), 3.75, start, {start})
            self.assertEqual([(e.name, e.x, e.y, e.direction) for e in again],
                             [(e.name, e.x, e.y, e.direction) for e in branch])
            self.assertIsNot(again[1], branch[1])
            # (something of the next layout on one of its belts, or on its splitter's free tile: searched again)
            belt = (int(branch[2].x), int(branch[2].y))
            other = plate_feed.plan(world, "iron-plate", (0, 3), 3.75, start, {start, belt})
            self.assertNotIn(belt, [(int(e.x), int(e.y)) for e in other[1:]])
            line = plate_feed.store_line(world, (0, 3), "iron-plate", 3.75)
            side = footprint(branch[:1]) - {(x, y) for x, y, _ in line.tiles}
            self.assertEqual(len(side), 1)
            moved = plate_feed.plan(world, "iron-plate", (0, 3), 3.75, start, {start} | side)
            self.assertFalse(footprint(moved[:1]) & side)

    def test_a_taps_budget_bounds_its_searches_and_says_what_they_took(self):
        world = store_world()
        line = plate_feed.store_line(world, (0, 3), "iron-plate", 3.75)
        # (a way 80 tiles off, round a wall: more than the budget lets it search)
        blocked = {(x, 15) for x in range(-40, 58)}
        stats: dict = {}
        with belt_lines.taps(True):
            self.assertIsNone(belt_lines.tap(world, "iron-plate", (10, 50), 0.0, blocked, among=[line],
                                             budget=(50, 120), stats=stats))
            self.assertLessEqual(stats["searched"], 120 + 1)   # (a search stops on the state past its cap)
            self.assertGreater(stats["searched"], 0)
            self.assertIsNotNone(belt_lines.tap(world, "iron-plate", (10, 50), 0.0, blocked, among=[line],
                                                budget=(100_000, 100_000)))

    def test_sources_are_the_electric_smelting_blocks_and_what_they_make(self):
        site = {"base": [
            {"purpose": "electric-iron-smelting", "capacity": {"makes": {"iron-plate": 1.25}},
             "keep": {"sites": ["field-iron-plate-x0=1,2,8,1", "outpost-iron-plate-x0=-97,-13"]}},
            {"purpose": "electric-iron-smelting", "capacity": {"makes": {"iron-plate": 2.5}}},
            {"purpose": "electric-copper-smelting", "capacity": {"makes": {"copper-plate": 2.5}}}]}
        self.assertEqual(plate_feed.sources(site), {"iron-plate": ((-97, 0), 3.75)})   # (copper keeps no spot)


class BusFeedTests(unittest.TestCase):
    """The bus with a fed plate: its feed where its smelting stage was, its own smelting after the modules."""

    def stages(self, **kwargs):
        def fake(dump, plate, port, rate, start, blocked):
            return [base_plan.Placed("splitter", start[0] - 4.0, start[1] + 0.5, NORTH),
                    base_plan.Placed("transport-belt", start[0] - 0.5, start[1] + 0.5, EAST)]
        with mock.patch.object(plate_feed, "plan", fake), mock.patch.object(plate_feed, "built", return_value=False):
            layout = base_plan.plan_bus_base(WORLD, Book.vanilla(), columns=10,
                                             packs=("automation-science-pack", "logistic-science-pack"), **kwargs)
        return [s.name for s in layout.stages], layout

    def test_a_fed_plate_comes_first_from_the_base_and_its_own_smelting_after_the_science(self):
        plain, _ = self.stages()
        names, layout = self.stages(feeds={"copper-plate": ((0, 0), 100.0)}, science_per_minute=10.0)
        self.assertIn("smelting-copper-plate", plain)
        self.assertLess(names.index("feed-copper-plate"), names.index("bus-red-science"))
        self.assertGreater(names.index("smelting-copper-plate"), names.index("bus-labs"))
        self.assertLess(names.index("smelting-iron-plate"), names.index("bus-red-science"))   # (not fed: as before)
        feed = next(s for s in layout.stages if s.name == "feed-copper-plate")
        own = next(s for s in layout.stages if s.name == "smelting-copper-plate")
        self.assertEqual(feed.entities[0].direction, SOUTH)   # (the plate path into its lanes leads the feed stage)
        self.assertFalse({id(e) for e in feed.entities} & {id(e) for e in own.entities})

    def test_a_source_making_too_little_feeds_nothing(self):
        names, _ = self.stages(feeds={"copper-plate": ((0, 0), 0.1)})
        self.assertNotIn("feed-copper-plate", names)

    def test_a_bus_being_built_keeps_its_feeds_and_one_whose_source_is_gone_smelts_its_own_at_once(self):
        names, layout = self.stages(feeds={"copper-plate": ((0, 0), 100.0)}, fed={"copper-plate"})
        feed = next(s for s in layout.stages if s.name == "feed-copper-plate")
        self.assertEqual(feed.proof, "")
        self.assertGreater(names.index("smelting-copper-plate"), names.index("bus-labs"))
        names, layout = self.stages(feeds={}, fed={"copper-plate"})
        feed = next(s for s in layout.stages if s.name == "feed-copper-plate")
        self.assertEqual(feed.proof, "built")   # (no plates to wait on: the stage stands once built)
        self.assertEqual(names.index("smelting-copper-plate"), names.index("feed-copper-plate") + 1)
        # (done without its belt, its source back since: still lost, never a branch the done stage would not build)
        names, layout = self.stages(feeds={"copper-plate": ((0, 0), 100.0)}, fed={"copper-plate": True})
        self.assertEqual(next(s for s in layout.stages if s.name == "feed-copper-plate").proof, "built")
        self.assertEqual(names.index("smelting-copper-plate"), names.index("feed-copper-plate") + 1)
        plan = {"id": "x", "prototype": "transport-belt"}
        self.assertEqual([w["kind"] for w in bus_planner._witnesses(feed, [plan])], ["entity_built"])

    def test_a_fed_plate_short_for_building_gets_its_own_smelting_first(self):
        _, layout = self.stages(feeds={"copper-plate": ((0, 0), 100.0)}, science_per_minute=10.0)
        done = {"feed-copper-plate": 1, "smelting-iron-plate": 1}
        own = bus_planner._rest_of_block(layout, done, "copper-plate")
        self.assertEqual(own.name, "smelting-copper-plate")
        site = {"base": [{"purpose": "electric-copper-smelting", "capacity": {"makes": {"copper-plate": 0.4}},
                          "keep": {"sites": ["outpost-copper-plate-x0=0,0"]}}]}
        short = bus_planner.pressed(site, layout, done, Book.vanilla(), 10.0,
                                    ("automation-science-pack", "logistic-science-pack"), "assembling-machine-2")
        self.assertIn("copper-plate", [r[0] for r in short])   # (judged by what the feed brings: 0.2 a second)


if __name__ == "__main__":
    unittest.main()


class RealOutputTests(unittest.TestCase):
    """F-21: a feed taps the belt the furnaces put their plates on, not whatever belt is on the expected port."""

    def test_the_furnaces_output_belt_is_found_when_the_port_s_belt_is_another(self):
        from companion import plate_feed
        from companion.modules import EAST, NORTH
        f = "me"
        ents = [{"t": "furnace", "n": "stone-furnace", "f": f, "x": 2.0, "y": 0.0, "w": 2, "h": 2},
                {"t": "inserter", "n": "inserter", "f": f, "x": 2.5, "y": 1.5, "d": NORTH}]   # (picks from it)
        ents += [{"t": "transport-belt", "f": f, "x": x + 0.5, "y": 2.5, "d": EAST} for x in range(0, 8)]
        ents += [{"t": "transport-belt", "f": f, "x": 20.5, "y": y + 0.5, "d": NORTH} for y in range(0, 6)]   # (other)
        dump = {"force": f, "entities": ents}
        line = plate_feed.store_line(dump, (20, 0), "iron-plate", 1.0)        # (the expected port: the other belt)
        self.assertIsNotNone(line)
        self.assertEqual(line.tiles[-1][:2], (7, 2))                          # (its end, by the store)
        self.assertIn((2, 2), {(x, y) for x, y, _ in line.tiles})             # (where the furnace drops)
        right = plate_feed.store_line(dump, (7, 2), "iron-plate", 1.0)
        self.assertEqual(right.tiles[-1][:2], (7, 2))                         # (the right port: as before)
        self.assertEqual(right.tiles[0][:2], (2, 2))                          # (only past the last furnace drop)
