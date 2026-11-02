"""Imported science blocks first (R-5b, companion/science_blocks.py): a library block for a pack line is offered before
the generated rows; its entries worked out from the block itself; rows only for what it takes in and nothing makes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import science_blocks as sb  # noqa: E402
from companion import tier_path  # noqa: E402
from companion.ratios import Book  # noqa: E402
from test_tier_path import BLUE, site  # noqa: E402


def ent(n, proto, x, y, d=0, **kw):
    return {"id": f"e{n}", "prototype": proto, "position": {"x": x, "y": y}, "direction": d, **kw}


def block(label="Blue Block", locked=False):
    """A small blue block (9x9): red circuits and sulfur in from the west on row 1 to a blue assembler; steel, gears and
    pipes in from the east on row 1 to an engine assembler, whose engines an inserter hands to the blue one; the packs
    out by an inserter onto a belt leaving west on row 7."""
    e = [ent(1, "transport-belt", 0.5, 1.5, 4), ent(2, "transport-belt", 1.5, 1.5, 4), ent(3, "transport-belt", 2.5, 1.5, 4),
         ent(4, "inserter", 2.5, 2.5, 0), ent(5, "assembling-machine-2", 2.5, 4.5, recipe=BLUE),
         ent(6, "inserter", 2.5, 6.5, 0), ent(7, "transport-belt", 2.5, 7.5, 12), ent(8, "transport-belt", 1.5, 7.5, 12),
         ent(9, "transport-belt", 0.5, 7.5, 12),
         ent(10, "assembling-machine-2", 6.5, 4.5, recipe="engine-unit"), ent(11, "inserter", 4.5, 4.5, 4),
         ent(12, "transport-belt", 8.5, 1.5, 12), ent(13, "transport-belt", 7.5, 1.5, 12),
         ent(14, "transport-belt", 6.5, 1.5, 12), ent(15, "inserter", 6.5, 2.5, 0)]
    return {"id": "blk" + label[:3], "import": {"label": label}, "outputs": [BLUE], "size": [9, 9], "entities": e,
            "stage_bills": [{"assembling-machine-2": 2}],
            "recipes_needed": [BLUE, "engine-unit"] + (["locked"] if locked else [])}


_ENTRIES = sb.entries


def two_lane_entries(record, book, pack=None):
    """The fixture's entries with no more than two items each (fed from chests, a lane each): the rest on a belt three
    tiles along the same edge (the engine assembler takes three through one)."""
    found, out, uncovered = _ENTRIES(record, book, pack)
    split = []
    for en in found:
        split.append(sb.Entry(en.x, en.y, en.edge, en.items[:2]))
        if en.items[2:]:
            dx, dy = (0, 3) if en.edge in ("west", "east") else (3, 0)
            split.append(sb.Entry(en.x + dx, en.y + dy, en.edge, en.items[2:]))
    return split, out, uncovered


class Catalog:
    def __init__(self, *records):
        import threading
        self._lock = threading.RLock()
        self.blueprints = {r["id"]: r for r in records}


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.book = Book.from_site({})
        patch = mock.patch.object(sb, "entries", two_lane_entries)
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_entry_carries_what_its_machines_take_that_the_block_does_not_make(self):
        found, out, uncovered = _ENTRIES(block(), self.book, BLUE)
        by_edge = {en.edge: set(en.items) for en in found}
        self.assertEqual(by_edge["west"], {"advanced-circuit", "sulfur"})          # (the blue assembler's)
        self.assertIn("steel-plate", by_edge["east"])                               # (the engine assembler's)
        self.assertNotIn("engine-unit", by_edge["west"] | by_edge["east"])          # (made inside)
        self.assertEqual((out, uncovered), ((0, 7, "west"), []))

    def test_a_block_whose_feeds_would_overlap_is_not_offered(self):
        crowded = block()
        crowded["entities"] = crowded["entities"] + [ent(99, "wooden-chest", -0.5, 1.5)]   # (on the west entry's stub)
        crowded["size"] = [9, 9]
        found, out, _ = sb.entries(crowded, self.book, BLUE)
        o = sb.Offer("k", "x", BLUE, 0.1, found, {}, [], [], out, crowded)
        self.assertIsNone(sb.rows(o))

    def test_entries_a_tile_or_two_apart_each_get_room_for_their_feeds(self):
        b = block()
        items = ["advanced-circuit", "sulfur", "iron-plate", "steel-plate"]
        for spacing in (1, 2, 3):   # (adjacent, one between, two between; the one before the last one item, its lane free)
            found = [sb.Entry(0, k * spacing, "west", items[:n]) for k, n in enumerate((1, 1, 2))]
            found.append(sb.Entry(4, 8, "south", items[:2]))
            m = sb.rows(sb.Offer("k", "x", BLUE, 0.1, found, {}, [], [], (0, 7, "west"), b))
            self.assertIsNotNone(m, (spacing, sb.REFUSED))
            tiles = [(int(e.x // 1), int(e.y // 1)) for e in m.entities]
            self.assertEqual(len(tiles), len(set(tiles)), spacing)              # (nothing on anything else)
            stubs = {t for e, t in zip(m.entities, tiles) if e.name == "transport-belt" and t[0] < 0}
            for i in m.feed_index:
                self.assertNotIn(tiles[i], stubs)
            for e in m.entities:   # (its own machines too, Codex R-5b: the fixture has no pole of its own)
                if sb._half(e.name) is not None:
                    self.assertTrue(sb._powered(m, e.x, e.y, sb._half(e.name)), (spacing, e))

    def test_fed_from_chests_no_two_items_share_a_lane(self):
        b = block()
        crowded = [sb.Entry(0, 1, "west", ["sulfur", "iron-plate"]), sb.Entry(0, 2, "west", ["steel-plate"])]
        three = [sb.Entry(0, 1, "west", ["sulfur", "iron-plate", "steel-plate"])]
        for found in (crowded, three):   # (its far lane is the next entry's line; three items on two lanes)
            o = sb.Offer("k", "x", BLUE, 0.1, found, {}, [], [], (0, 7, "west"), b)
            self.assertIsNone(sb.rows(o))
            self.assertIn("lane", sb.REFUSED)
        crowded_o = sb.Offer("k", "x", BLUE, 0.1, crowded, {}, [], [], (0, 7, "west"), b)
        self.assertIsNotNone(sb.rows(crowded_o, chests=False))   # (belts brought to it, a lane each: the hookup's)
        self.assertIsNone(sb.rows(sb.Offer("k", "x", BLUE, 0.1, three, {}, [], [], (0, 7, "west"), b), chests=False))

    def test_each_item_of_an_entry_has_its_own_lane(self):
        found, _, _ = _ENTRIES(block(), self.book, BLUE)
        for en in (en for en in found if len(en.items) <= 2):   # (more than two: no block offered, rows)
            self.assertEqual(sorted(en.lanes), sorted(en.items))
            self.assertEqual(len(set(en.lanes.values())), len(en.items))   # (never two on one lane)

    def test_the_ports_say_which_lane_each_item_comes_in_on(self):
        found = [sb.Entry(0, 1, "west", ["sulfur", "iron-plate"], lanes={"sulfur": "left", "iron-plate": "right"})]
        o = sb.Offer("k", "x", BLUE, 0.1, found, {}, [], [], (0, 7, "west"), block())
        bus = {p.item: p.lane for p in sb.rows(o, chests=False).ports if p.kind == "in"}
        self.assertEqual(bus, {"sulfur": "left", "iron-plate": "right"})
        m = sb.rows(o)
        chest = {p.item: p.lane for p in m.ports if p.kind == "in"}
        self.assertEqual(set(chest.values()), {"left", "right"})
        for port in (p for p in m.ports if p.kind == "in"):   # (on the right of an east-running stub: south, y+)
            self.assertEqual(port.lane, "left" if port.y > 1 else "right")

    def test_an_output_between_two_entries_still_finds_a_pole(self):
        found = [sb.Entry(0, 2, "west", ["sulfur"]), sb.Entry(0, 4, "west", ["iron-plate"])]
        m = sb.rows(sb.Offer("k", "x", BLUE, 0.1, found, {}, [], [], (0, 3, "west"), block()))
        self.assertIsNotNone(m, sb.REFUSED)

    def test_an_input_one_inserter_cannot_carry_gets_more_chests(self):
        found = [sb.Entry(0, 1, "west", ["sulfur", "iron-plate"])]
        o = sb.Offer("k", "x", BLUE, 0.1, found, {"sulfur": 2.0, "iron-plate": 0.5}, [], [], (0, 7, "west"), block(),
                     lift=0.8)
        m = sb.rows(o)
        sulfur = [p for p in m.ports if p.item == "sulfur"]
        self.assertEqual(len(sulfur), 3)
        self.assertAlmostEqual(sum(p.rate for p in sulfur), 2.0)
        self.assertEqual(len([p for p in m.ports if p.item == "iron-plate"]), 1)
        sides = {p.item: {q.direction for q in m.ports if q.item == p.item} for p in m.ports if p.kind == "in"}
        self.assertEqual([len(v) for v in sides.values()], [1, 1], sides)                # (a lane each, never mixed)
        self.assertNotEqual(sides["sulfur"], sides["iron-plate"])

    def test_extra_chests_go_to_the_entry_whose_machines_draw_more(self):
        found = [sb.Entry(0, 1, "west", ["sulfur"], {"sulfur": 3.0}), sb.Entry(0, 5, "west", ["sulfur"], {"sulfur": 0.2})]
        o = sb.Offer("k", "x", BLUE, 0.1, found, {"sulfur": 3.2}, [], [], (0, 7, "west"), block(), lift=0.8)
        m = sb.rows(o)
        rates = sorted(round(p.rate, 3) for p in m.ports if p.item == "sulfur")
        self.assertEqual(rates, [0.2, 0.75, 0.75, 0.75, 0.75])   # (four for the one drawing 3/s, one for the other)

    def test_without_chests_only_the_stubs_and_a_port_at_each_end(self):
        found, out, _ = sb.entries(block(), self.book, BLUE)
        m = sb.rows(sb.Offer("k", "x", BLUE, 0.1, found, {}, [], [], out, block()), chests=False)
        self.assertIsNone(m.feed_index)
        self.assertNotIn(sb.CHEST, [e.name for e in m.entities])
        ins = {(p.item, p.x, p.y) for p in m.ports if p.kind == "in"}
        self.assertIn(("advanced-circuit", -2, 1), ins)
        self.assertEqual([(p.item, p.x, p.y) for p in m.ports if p.kind == "out"], [(BLUE, -1, 7)])

    def test_the_blocks_own_settings_are_kept(self):
        b = block()
        b["entities"][3] = {**b["entities"][3], "filters": [{"index": 1, "name": "sulfur"}]}
        found, out, _ = sb.entries(b, self.book, BLUE)
        m = sb.rows(sb.Offer("k", "x", BLUE, 0.1, found, {}, [], [], out, b))
        self.assertEqual(m.entities[3].to_plan("i")["filters"], [{"index": 1, "name": "sulfur"}])

    def test_the_block_fed_from_chests_pairs_each_chest_with_its_item(self):
        with mock.patch.object(sb, "candidates", lambda s, p: [block()]):
            o = sb.offer(site(), BLUE, 0.05, book=self.book)
        m = sb.rows(o)
        self.assertEqual(len(m.feed_index), len(m.ports))
        for i, port in zip(m.feed_index, m.ports):
            self.assertEqual(m.entities[i].name, sb.CHEST)
        self.assertEqual(m.ports[-1].kind, "out")


class OfferTests(unittest.TestCase):
    def setUp(self):
        self.book = Book.from_site({})
        patch = mock.patch.object(sb, "entries", two_lane_entries)
        patch.start()
        self.addCleanup(patch.stop)

    def test_only_blocks_that_can_be_built_now_are_offered(self):
        sb.use_catalog(Catalog(block(locked=True)))
        try:
            with mock.patch("companion.blueprints.locked_items", lambda r, s: ["recipe:locked"] if "locked" in
                            (r.get("recipes_needed") or ()) else []):
                self.assertEqual(sb.candidates(site(), BLUE), [])
        finally:
            sb.use_catalog(None)

    def test_rows_only_for_what_it_takes_and_nothing_makes(self):
        with mock.patch.object(sb, "candidates", lambda s, p: [block()]):
            o = sb.offer(site(), BLUE, 0.05, book=self.book)
        self.assertIn("advanced-circuit", o.need_rows)
        self.assertNotIn("engine-unit", o.need_rows + o.elsewhere)     # (it makes them)
        self.assertNotIn("sulfur", o.need_rows)                        # (oil's: elsewhere)

    def test_the_block_runs_no_faster_than_its_output_chest_takes(self):
        with mock.patch.object(sb, "candidates", lambda s, p: [block()]),                 mock.patch.object(sb.mods, "inserter_rate", lambda *a: 0.01):
            o = sb.offer(site(), BLUE, 0.005, book=self.book)
        self.assertAlmostEqual(o.rate, 0.01)

    def test_the_picked_block_is_planned_at_the_rate_it_was_picked_for(self):
        with mock.patch.object(sb, "candidates", lambda s, p: [block()]):
            picked = sb.offer(site(), BLUE, 0.05, book=self.book)
            with mock.patch.object(sb, "cost", lambda *a: 10 ** 6):   # (no second judgment of cost)
                again = sb.chosen(site(), BLUE, picked.key, 0.05, book=self.book)
        self.assertEqual((again.key, again.need_rows, again.elsewhere), (picked.key, picked.need_rows, picked.elsewhere))

    def test_the_sites_own_fluids_are_read_before_inputs_are_judged(self):
        from companion import tier_builds
        with mock.patch.object(sb, "candidates", lambda s, p: []),                 mock.patch.object(tier_builds, "_note_fluids") as noted:
            sb.offer(site(), BLUE, 0.05, book=self.book)
        noted.assert_called_once()

    def test_a_block_costing_much_more_than_the_rows_gives_way_to_them(self):
        with mock.patch.object(sb, "candidates", lambda s, p: [block()]), \
                mock.patch.object(sb, "generated_cost", lambda *a: 10), \
                mock.patch.object(sb, "cost", lambda *a: 100):
            self.assertIsNone(sb.offer(site(), BLUE, 0.05, book=self.book))
        with mock.patch.object(sb, "candidates", lambda s, p: [block()]), \
                mock.patch.object(sb, "generated_cost", lambda *a: 100), \
                mock.patch.object(sb, "cost", lambda *a: 110):
            self.assertIsNotNone(sb.offer(site(), BLUE, 0.05, book=self.book))   # (the proven one, a little preferred)


RED, GREEN = "automation-science-pack", "logistic-science-pack"


def book_block_3():
    """The user's "3. Automation + Logistic Science (60/min each)" (1.0 Science Book - Tileable v3.7), as imported."""
    import json
    return json.loads((ROOT / "tests" / "fixtures" / "packs" / "science-book-block-3.json").read_text(encoding="utf-8"))


@unittest.skipUnless((ROOT / "tests" / "fixtures" / "packs" / "science-book-block-3.json").exists(),
                     "the Science Book block 3 fixture is not here (third-party blueprints: not in every checkout)")
class BusBlockTests(unittest.TestCase):
    """U-2: the user's science block on the bus in place of the generated red and green rows, fed by lane taps."""

    def setUp(self):
        self.book = Book.from_site({})
        self.record = book_block_3()
        sb.use_catalog(Catalog(self.record))
        self.addCleanup(sb._LIBRARY.pop, "catalog", None)

    def test_its_entries_are_iron_and_copper_on_the_west_edge_and_a_turn_inside_is_none(self):
        found, out, uncovered = sb.entries(self.record, self.book, RED)
        self.assertEqual([(en.edge, en.y, en.items) for en in found],
                         [("west", 1, ["iron-plate"]), ("west", 13, ["copper-plate"])])   # (no south "entry")
        self.assertEqual((out, uncovered), ((0, 8, "west"), []))

    def test_the_bus_takes_the_block_for_red_and_green_and_its_labs_take_both_from_one_lane(self):
        from companion import base_plan
        from companion.bus import Bus
        designs = {}
        built = base_plan.modules_for((RED, GREEN), 15, self.book, designs=designs)
        names = [m.name for m in built]
        self.assertEqual(names[:2], [sb.BUS_BLOCK, "labs"])
        self.assertNotIn("red-science", names)
        self.assertNotIn("green-science", names)
        block = built[0]
        self.assertEqual([(p.item, p.kind, p.x, p.y) for p in block.ports],
                         [("iron-plate", "in", 0, 1), ("copper-plate", "in", 0, 13), (RED, "out", 0, 8)])
        self.assertEqual(set(block.makes), {RED, GREEN})
        self.assertEqual([(p.item, p.kind) for p in built[1].ports], [(RED, "in")])   # (the block's one pack lane)
        self.assertEqual(designs, {sb.BUS_BLOCK: "science-book-3"})
        bus = Bus(0, 0, 10, 100_000)
        bus.add_lane("iron-plate", belts=2, supply=7.5)
        bus.add_lane("copper-plate", belts=2, supply=7.5)
        for m in built:
            bus.attach(m)
        self.assertEqual(bus.attached[0].taps, [("iron-plate", bus.attached[0].y + 1),
                                                ("copper-plate", bus.attached[0].y + 13)])

    def test_a_bus_planned_without_the_block_keeps_its_rows(self):
        from companion import base_plan
        names = [m.name for m in base_plan.modules_for((RED, GREEN), 15, self.book, chosen={})]
        self.assertIn("red-science", names)
        self.assertNotIn(sb.BUS_BLOCK, names)
        kept = base_plan.modules_for((RED, GREEN), 15, self.book, chosen={sb.BUS_BLOCK: "science-book-3"})
        self.assertEqual(kept[0].name, sb.BUS_BLOCK)

    def test_no_block_while_what_it_is_built_of_is_locked_or_for_red_alone(self):
        from companion import base_plan
        locked = [m.name for m in base_plan.modules_for((RED, GREEN), 15, self.book, unlocked=["transport-belt"])]
        self.assertNotIn(sb.BUS_BLOCK, locked)
        self.assertNotIn(sb.BUS_BLOCK, [m.name for m in base_plan.modules_for((RED,), 15, self.book)])
        coming = ["automation-science-pack", "copper-cable", "electronic-circuit", "inserter", "iron-gear-wheel",
                  "transport-belt"]   # (green's research under way: the bus is planned with it all the same)
        self.assertEqual(base_plan.modules_for((RED, GREEN), 15, self.book, unlocked=coming)[0].name, sb.BUS_BLOCK)

    def test_the_taps_draw_what_the_bus_asks_not_the_block_flat_out(self):
        _, m = sb.bus_block((RED, GREEN), self.book, 0.25)
        _, full = sb.bus_block((RED, GREEN), self.book, 10.0)
        iron = next(p.rate for p in m.ports if p.item == "iron-plate")
        self.assertAlmostEqual(iron * 4, next(p.rate for p in full.ports if p.item == "iron-plate"))

    def test_its_splitter_settings_reach_the_bus_and_turn_with_it_on_the_west(self):
        from companion.bus import Bus
        _, m = sb.bus_block((RED, GREEN), self.book, 0.25)
        own = sorted(str(e.settings) for e in m.entities if getattr(e, "settings", None))
        for side, flip in (("east", False), ("west", True)):
            bus = Bus(0, 0, 10, 100_000)
            bus.add_lane("iron-plate", belts=2, supply=7.5)
            bus.add_lane("copper-plate", belts=2, supply=7.5)
            bus.attach(m, side=side)
            laid = [e.settings for e in bus.entities() if getattr(e, "settings", None)]
            self.assertEqual(len(laid), len(own))
            if flip:
                swap = {"left": "right", "right": "left"}
                self.assertEqual(sorted(str({k: swap.get(v, v) for k, v in d.items()}) for d in laid), own)
            else:
                self.assertEqual(sorted(str(d) for d in laid), own)

    def test_a_mirrored_port_comes_in_on_the_other_lane(self):
        from companion.bus import mirrored
        from companion.modules import Module, Port, EAST
        m = Module("m", ports=[Port("copper-plate", "in", 0, 3, EAST, 1.0, "left"), Port("iron-plate", "in", 0, 5, EAST, 1.0)],
                   width=6, height=8)
        self.assertEqual([p.lane for p in mirrored(m).ports], ["right", None])

    def test_a_block_making_the_rate_is_taken_over_a_smaller_slower_one(self):
        import copy
        slow = copy.deepcopy(self.record)
        slow["id"] = "a-slow"
        drop = [e for e in slow["entities"] if e.get("recipe") == GREEN][1:]
        slow["entities"] = [e for e in slow["entities"] if e not in drop]   # (one green assembler: smaller, slower)
        sb.use_catalog(Catalog(self.record, slow))
        self.assertEqual(sb.bus_block((RED, GREEN), self.book, 0.5)[0], "science-book-3")
        self.assertEqual(sb.bus_block((RED, GREEN), self.book, 0.01)[0], "a-slow")

    def test_gears_and_circuits_come_after_the_block_for_a_later_mall(self):
        from companion import base_plan
        names = [m.name for m in base_plan.modules_for((RED, GREEN), 15, self.book, with_mall=False)]
        self.assertEqual(names, [sb.BUS_BLOCK, "labs", "gears", "green-circuits"])

    def test_a_bus_is_planned_with_green_from_the_start_when_the_block_is_there(self):
        from companion import bus_planner
        start = {"difficulty": "normal", "researched": [], "recipes": []}
        self.assertEqual(bus_planner.goal(start)[1], (RED, GREEN))
        sb._LIBRARY.pop("catalog")
        self.assertEqual(bus_planner.goal(start)[1], (RED,))

    def test_a_stage_whose_recipes_are_not_researched_waits_and_the_next_ready_one_goes_first(self):
        from types import SimpleNamespace as NS
        from companion import bus_planner, layout_planner
        from companion.modules import Placed
        block = NS(name="bus-science-block", entities=[Placed("assembling-machine-1", 0, 0, recipe=GREEN)])
        labs = NS(name="bus-labs", entities=[Placed("lab", 0, 0)])
        layout = NS(stages=[block, labs], notes=[])
        site = {"recipes": [{"name": RED}]}
        self.assertIs(bus_planner._ready_stage(site, layout, {}, block), labs)
        self.assertIn("bus-science-block waits for " + GREEN, layout.notes[0])
        with self.assertRaises(layout_planner.PlanError):
            bus_planner._ready_stage(site, layout, {"bus-labs": 1}, block)
        self.assertIs(bus_planner._ready_stage({"recipes": [{"name": RED}, {"name": GREEN}]}, layout, {}, block), block)

    def test_the_block_stage_makes_both_packs(self):
        from companion import tier_builds
        self.assertIn(tier_builds.BUS_BLOCK_STAGE, tier_builds.bus_stages(GREEN))
        self.assertIn("bus-red-science", tier_builds.bus_stages(RED))
        self.assertEqual(tier_builds.bus_stages("iron-gear-wheel"), ("bus-gears",))
        saved = {"base_plan": {"stages": ["bus-science-block"], "done": {"bus-science-block": 1},
                               "packs": [RED, GREEN]}}
        self.assertTrue(tier_builds.on_bus(saved, GREEN))


if __name__ == "__main__":
    unittest.main()
