"""What the science path says is due, built at its rate (companion/tier_builds.py, F-10): a supply, a pack line with
the supplies it waits on, more of a pack, labs; any pack and rate, and what cannot be built here said, not dropped."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import catalog_planner as cp  # noqa: E402
from companion import tier_builds, tier_path  # noqa: E402
from companion.ratios import Book  # noqa: E402
from test_tier_path import (BLUE, BRICKS, COPPER, GREEN, GREEN_BLOCK, GREY, IRON, LABS, RED, RED_BLOCK, TIERS,  # noqa: E402
                            block, researched, site)

GREY_READY = researched(TIERS, "military-2", "military-science-pack")


def world():
    w = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
    return w


class PickTests(unittest.TestCase):
    def test_a_supply_due_is_built_at_what_is_due_less_what_is_made(self):
        s = site()
        d = tier_path.due(s, "steel-plate")
        b = tier_builds.pick(s)
        self.assertEqual(b.item, "steel-plate")
        self.assertAlmostEqual(b.rate, min(tier_builds.MAX_BLOCK_RATE, d.rate - d.have))
        # some made already: only the rest
        some = block("steel-smelting", {"stone-furnace": 1}, ["steel-plate"], {"steel-plate": d.rate / 2})
        half = site(base=[RED_BLOCK, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS, some])
        d2 = tier_path.due(half, "steel-plate")
        if d2.due:
            self.assertAlmostEqual(tier_builds.pick(half).rate, max(d2.rate - d2.have, tier_builds.MIN_RATE))

    def test_a_pack_line_brings_what_it_waits_on_first_and_labs_with_it(self):
        s = site(tiers=GREY_READY)
        b = tier_builds.build_for(s, GREY)
        self.assertEqual(b.item, GREY)
        self.assertIn("steel-plate", [w for w, _ in b.waits])        # (nothing makes steel yet: first)
        self.assertTrue(all(r > 0 for _, r in b.waits))
        # (the line draws no more steel than its new steel row makes)
        book = Book.vanilla()
        parts, _ = tier_builds.fed_modules(GREY, b.rate, book, "assembling-machine-1", s)
        draw = sum(p.rate for m in parts for p in m.ports if p.kind == "in" and p.item == "steel-plate")
        self.assertLessEqual(draw, dict(b.waits)["steel-plate"] + 1e-9)
        made = block("steel-smelting", {"stone-furnace": 4}, ["steel-plate"], {"steel-plate": 1.0})
        fed = site(tiers=GREY_READY, base=[RED_BLOCK, GREEN_BLOCK, LABS, IRON, COPPER, BRICKS, made])
        self.assertNotIn("steel-plate", [w for w, _ in tier_builds.build_for(fed, GREY).waits])

    def test_a_pack_the_bus_grows_is_the_buss_and_one_it_cannot_grow_gets_a_block(self):
        saved = {"stages": ["bus-red-science", "bus-green-science"], "packs": [RED, GREEN], "extra": [GREY]}
        red = {"purpose": "bus-base", "stage": "bus-red-science", "buildings": 30, "recipes": [RED, "iron-gear-wheel"]}
        s = site(tiers=GREY_READY, base_plan=saved, base=[red])
        self.assertTrue(tier_builds.on_bus(s, RED))       # (its growth sections make red)
        self.assertFalse(tier_builds.on_bus(s, GREY))     # (none makes grey: a block beside the bus does)
        # (green planned on the bus but not built: a plan, not a supply, F-21)
        self.assertFalse(tier_builds.on_bus(s, GREEN))
        built = site(tiers=GREY_READY, base_plan={**saved, "done": {"bus-green-science": 1}}, base=[])
        self.assertTrue(tier_builds.on_bus(built, GREEN))   # (its stage done: past the export's block history too)

    def test_nothing_due_nothing_built_and_what_cannot_be_built_is_said(self):
        self.assertIsNone(tier_builds.pick({"tick": 3600}))
        # labs only: red science is the next tier, but nothing makes the plates its line takes and no row here can:
        # a line that would stand idle is passed over, and why is said
        skipped: list = []
        self.assertIsNone(tier_builds.pick(site(base=[LABS]), skipped=skipped))
        self.assertTrue(any(x.startswith(f"{RED}: waits on") and "iron-plate" in x for x in skipped), skipped)
        # with the plates made, its line is built
        self.assertEqual(tier_builds.pick(site(base=[LABS, IRON, COPPER])).item, RED)


class BuilderTests(unittest.TestCase):
    """Any item: the bus's own modules where there is one, else a row of its recipe; what cannot be, said."""

    def test_the_known_modules_and_a_row_of_any_other_recipe(self):
        book = Book.vanilla()
        self.assertEqual([m.name for m in tier_builds.modules_for("steel-plate", 0.1, book)], ["steel"])
        self.assertEqual([m.name for m in tier_builds.modules_for(GREY, 0.1, book)], ["piercing", "military-science"])
        self.assertEqual([m.name for m in tier_builds.modules_for(BLUE, 0.1, book)], ["blue-science"])
        wall = tier_builds.modules_for("stone-wall", 0.2, book)      # (no module of its own: a row of its recipe)
        self.assertEqual(wall[0].makes, {"stone-wall": 0.2})
        self.assertEqual({e.recipe for e in wall[0].entities if e.recipe}, {"stone-wall"})
        more = tier_builds.modules_for("stone-wall", 3.0, book)
        self.assertGreater(sum(1 for e in more[0].entities if e.recipe), sum(1 for e in wall[0].entities if e.recipe))

    def test_a_rows_inputs_carry_what_they_take(self):
        # (an inserter or a loader at each chest, by the rate: _with_chests)
        wall = tier_builds.modules_for("stone-wall", 0.5, Book.vanilla())[0]
        port = next(p for p in wall.ports if p.kind == "in")
        self.assertEqual((port.item, port.rate), ("stone-brick", 2.5))

    def test_what_no_assembler_makes_is_not_built_and_why_is_said(self):
        book = Book.vanilla()
        with self.assertRaises(tier_builds.NoBuilder) as caught:
            tier_builds.modules_for("plastic-bar", 0.1, book)        # (a chemical plant's)
        self.assertIn("fluid", str(caught.exception))   # (petroleum gas: an oil build's, no chest feeds it)
        with self.assertRaises(tier_builds.NoBuilder):
            tier_builds.modules_for("unobtainium", 0.1, book)
        self.assertIsNotNone(tier_builds.buildable("plastic-bar", {}, book))
        self.assertIsNone(tier_builds.buildable("stone-wall", {}, book))


class EdgeTests(unittest.TestCase):
    def test_labs_wait_for_a_pack_made_by_machine(self):
        path = tier_path.assess(site(base=[LABS]))
        with mock.patch.object(tier_path.Path, "dues",
                               lambda self: [tier_path.Due(tier_path.LAB, True, "labs short", 7.0, 1.0)]):
            skipped: list = []
            self.assertIsNone(tier_builds.pick(site(base=[LABS]), path=path, skipped=skipped))
            self.assertIn("lab: no pack made by machine for them yet", skipped)

    def test_a_mods_fluid_named_by_the_games_recipe_list_is_no_chest_feeds(self):
        s = {"recipes": [{"name": "frozen-thing", "category": "crafting", "energy": 1,
                          "ingredients": [{"type": "fluid", "name": "cryonite-slush", "amount": 10},
                                          {"type": "item", "name": "iron-plate", "amount": 1}],
                          "products": [{"type": "item", "name": "frozen-thing", "amount": 1}]}]}
        why = tier_builds.buildable("frozen-thing", s, Book.from_site(s))
        self.assertIn("cryonite-slush", why or "")

    def test_without_loaders_a_row_is_no_faster_than_an_inserter_feeds_its_chests(self):
        made, rate = tier_builds.fed_modules("stone-wall", 0.5, Book.vanilla(), "assembling-machine-1", {})
        port = next(p for p in made[0].ports if p.kind == "in")
        self.assertLessEqual(port.rate, 0.8)
        self.assertLess(rate, 0.5)
        made, rate = tier_builds.fed_modules("stone-wall", 0.1, Book.vanilla(), "assembling-machine-1", {})
        self.assertEqual(rate, 0.1)   # (fed as asked)

    def test_every_rows_ports_carry_their_draw_and_loaders_lift_the_cap(self):
        book = Book.vanilla()
        made, rate = tier_builds.fed_modules("steel-plate", 0.1, book, "assembling-machine-1", {})
        iron = next(p for p in made[0].ports if p.item == "iron-plate")
        self.assertAlmostEqual(iron.rate, 0.5)                       # (five plates a steel: a known module's port)
        made, rate = tier_builds.fed_modules("military-science-pack", 0.1, book, "assembling-machine-1", {})
        coal = next(p for m in made for p in m.ports if p.item == "coal")
        self.assertGreater(coal.rate, 0)                             # (the grenades' coal, raw as it is)
        capped = tier_builds.fed_modules("steel-plate", 0.5, book, "assembling-machine-1", {})[1]
        with mock.patch("companion.modules.loader_limit", return_value=0.8):   # (loaders researched)
            lifted = tier_builds.fed_modules("steel-plate", 0.5, book, "assembling-machine-1", {})[1]
        self.assertLess(capped, 0.5)
        self.assertEqual(lifted, 0.5)

    def test_an_item_no_chest_can_feed_even_slowly_has_no_builder(self):
        s = {"recipes": [{"name": "heavy-thing", "category": "crafting", "energy": 1,
                          "ingredients": [{"type": "item", "name": "iron-plate", "amount": 100}],
                          "products": [{"type": "item", "name": "heavy-thing", "amount": 1}]}]}
        self.assertIn("than a chest feeds", tier_builds.buildable("heavy-thing", s, Book.from_site(s)) or "")

    def test_bus_growth_counts_only_the_packs_its_sections_make(self):
        from companion import bus_planner
        saved = {"stages": ["bus-red-science"], "packs": [RED, GREEN], "extra": [GREY, BLUE], "spm": 10.0, "aim": 20.0}

        def dues(wanted):
            return lambda self, item: (tier_path.Due(item, True, "more", 70 / 60, 0) if item == wanted
                                       else tier_path.Due(item, False, "x"))
        with mock.patch.object(tier_path.Path, "due", dues(BLUE)):   # (blue on the bus: its sections grow it)
            self.assertEqual(bus_planner.tier_sections(site(base_plan=saved), saved, 10.0, 20.0), 3)
        with mock.patch.object(tier_path.Path, "due", dues(GREY)):   # (grey: no section makes it)
            self.assertEqual(bus_planner.tier_sections(site(base_plan=saved), saved, 10.0, 20.0), 0)

    def test_the_bus_grows_toward_what_the_path_wants_of_its_packs(self):
        from companion import bus_planner, growth
        saved = {"stages": ["bus-red-science"], "packs": [RED, GREEN], "spm": 10.0, "aim": 20.0}
        s = site(base_plan=saved)
        due = tier_path.Due(RED, True, "more red", 70 / 60, 10 / 60)
        with mock.patch.object(tier_path.Path, "due", lambda self, item: due if item == RED else
                               tier_path.Due(item, False, "x")):
            self.assertEqual(bus_planner.tier_sections(s, saved, 10.0, 20.0), 3)   # (10 + 3 x 20 >= 70)
            with mock.patch.object(bus_planner.factory_plan, "bus_targets", return_value=mock.Mock(growth=0)):
                self.assertEqual(bus_planner.growth_wanted(s, saved), 3)
        big = tier_path.Due(RED, True, "much more", 1000 / 60, 0)
        with mock.patch.object(tier_path.Path, "due", lambda self, item: big if item == RED else
                               tier_path.Due(item, False, "x")),                 mock.patch.object(bus_planner.factory_plan, "bus_targets", return_value=mock.Mock(growth=0)):
            self.assertEqual(bus_planner.growth_wanted(s, saved), growth.MAX_GROWTH)   # (within the saved limits)


class PlanTests(unittest.TestCase):
    def test_a_pack_line_is_planned_with_its_supply_first_and_labs_in_chest_fed_rows(self):
        w = world()
        s = site(tiers=GREY_READY, request_id="1:7", origin={"x": -32, "y": -32},
                 machines={"assembling-machine-2": {}}, personality={"traits": ["chaotic"]})   # (no bus: F-21)
        with mock.patch.object(tier_builds, "build_for", wraps=tier_builds.build_for):
            plan = tier_builds.plan(s, w, GREY)
        ids = [st["id"] for st in plan["stages"]]
        self.assertEqual(ids[:3], ["tier-steel-plate", f"tier-{GREY}", f"tier-{GREY}-2"])
        self.assertEqual(plan["tier"]["item"], GREY)
        fed = {x["item"] for st in plan["stages"] for x in st["manual_supplies"]}
        self.assertIn("iron-plate", fed)          # (no bus lane carries iron here: the hand's chest bridges, F-20 B)
        self.assertTrue(any("no bus lane carries iron-plate" in n for n in plan["notes"]))
        self.assertTrue(all(st["witnesses"] for st in plan["stages"]))
        ids_all = [e["id"] for e in plan["entities"]]
        self.assertEqual(len(ids_all), len(set(ids_all)))

    def test_labs_when_labs_are_due(self):
        w = world()
        s = site(request_id="1:8", origin={"x": -32, "y": -32})
        with mock.patch.object(tier_builds, "pick", return_value=tier_builds.Build(tier_path.LAB, 3.0, "labs short")):
            plan = tier_builds.plan(s, w)
        labs = [e for e in plan["entities"] if e["prototype"] == "lab"]
        self.assertEqual(len(labs), 3)
        self.assertEqual(plan["stages"][0]["witnesses"][0]["kind"], "entity_built")


class GoalTests(unittest.TestCase):
    """A goal the weighing and System One see, not an override; the grey line's own goal sized by the signal."""

    def test_the_goal_is_offered_while_something_due_can_be_built(self):
        s = site(completed_purposes=["research-lab"], recipes=[])
        with mock.patch.object(tier_builds, "pick", return_value=tier_builds.Build("steel-plate", 0.05, "x")), \
                mock.patch.object(tier_builds, "lane_first", return_value=None):   # (its plates' lane: F-20 B)
            tier_builds._PICKED.clear()
            self.assertTrue(cp.available("science-tier", s))
            self.assertIn("science-tier", [o["id"] for o in cp.purpose_options(s)])
        with mock.patch.object(tier_builds, "pick", return_value=None):
            tier_builds._PICKED.clear()
            self.assertFalse(cp.available("science-tier", {**s, "tick": s["tick"] + 1}))
        tier_builds._PICKED.clear()

    def test_steel_researched_and_due_is_built_by_the_science_path_at_its_rate(self):
        s = site(completed_purposes=["research-lab", "steel-smelting-not"], recipes=[{"name": "steel-plate"}])
        with mock.patch.object(cp.ratios, "unlocked", return_value={"steel-plate", "stone-furnace", "burner-inserter"}), \
                mock.patch.object(tier_builds, "pick", return_value=tier_builds.Build("steel-plate", 0.05, "x")), \
                mock.patch.object(tier_builds, "lane_first", return_value=None):
            tier_builds._PICKED.clear()
            self.assertEqual(cp.first_source_due(s), "science-tier")
            self.assertEqual(cp.first_source_item("science-tier"), "steel-plate")
        tier_builds._PICKED.clear()

    def test_the_weighing_values_it_by_the_item_due_at_its_rate(self):
        from companion import goal_brain
        s = site()
        with mock.patch.object(tier_builds, "pick", return_value=tier_builds.Build("steel-plate", 0.05, "x")):
            tier_builds._PICKED.clear()
            makes, takes, machines = goal_brain.full_flows("science-tier", s, Book.vanilla())
            self.assertGreater(makes.get("steel-plate", 0), 0.05 - 1e-9)   # (as laid out: its furnaces, its iron)
            self.assertGreater(takes.get("iron-plate", 0), 0)
            self.assertIn("stone-furnace", machines)
        tier_builds._PICKED.clear()

    def test_the_grey_goal_is_weighed_by_its_line_at_the_paths_rate(self):
        from companion import goal_brain
        makes, takes, _ = goal_brain.full_flows("military-science-automation", site(tiers=GREY_READY), Book.vanilla())
        self.assertGreater(makes.get(GREY, 0), 0)
        self.assertGreater(takes.get("iron-plate", 0), 0)

    def test_the_grey_goal_is_not_offered_where_a_block_makes_grey_packs(self):
        grey = block("science-tier", {"assembling-machine-2": 2}, [GREY], {GREY: 0.1})
        s = site(base=[RED_BLOCK, GREEN_BLOCK, LABS, grey])
        self.assertFalse(cp.available("military-science-automation", s))


class LabTests(unittest.TestCase):
    """F-17: labs follow the packs made (long-65 built 10 labs beside a 0.26/s red row at minute 17; the player ran 2
    until minute 81)."""

    class Path:
        def __init__(self, made, research=None, sets=(), speeds=None, unit=30.0):
            from types import SimpleNamespace
            self.tiers = [SimpleNamespace(pack=p, made=r, automated=r > 0, research=research or [],
                                          military=False) for p, r in made.items()]
            self.lab_speeds, self.lab_bonus, self.unit_time = speeds or {"lab": 1.0}, 0.0, unit
            self._sets, self._research = [frozenset(x) for x in sets], research

        def researching(self):
            return self.tiers[:1] if self._research else []

        def _pack_sets(self):
            return self._sets

    @staticmethod
    def site(labs=0, fast=0):
        machines = {**({"lab": labs} if labs else {}), **({"fast-lab": fast} if fast else {})}
        return {"base": [{"purpose": "research-lab", "machines": machines}] if machines else []}

    def test_a_red_row_brings_what_its_packs_keep_busy_and_one_spare(self):
        path = self.Path({RED: 0.0}, research=[{"name": "automation", "time": 5, "packs": [RED]}])
        # (0.26/s at 5 s a unit: 1.3 labs' work, 2 labs and one spare; one lab stands)
        self.assertEqual(tier_builds.labs_busy(self.site(1), path, RED, 0.26), 2)
        self.assertEqual(tier_builds.labs_busy(self.site(1), path, RED, 0.0), 0)       # (no packs made: none)

    def test_labs_keep_growing_with_the_research_s_own_time_a_unit(self):
        path = self.Path({RED: 0.2}, research=[{"name": "slow", "time": 60, "packs": [RED]}])
        self.assertEqual(tier_builds.labs_busy(self.site(3), path), 10)                # (12 labs' work, 3 stand)

    def test_an_unrelated_slow_pack_does_not_hold_the_labs(self):
        path = self.Path({RED: 0.2, GREEN: 0.2, GREY: 0.01}, sets=[{RED, GREEN}, {RED, GREEN, GREY}])
        self.assertEqual(tier_builds.labs_busy(self.site(2), path), 5)                 # (red and green: 6 + 1 - 2)

    def test_faster_labs_standing_count_at_their_speed(self):
        path = self.Path({RED: 0.8}, research=[{"name": "r", "time": 30, "packs": [RED]}],
                         speeds={"lab": 1.0, "fast-lab": 4.0})
        self.assertEqual(tier_builds.labs_busy(self.site(fast=5), path), 5)            # (24 work, 20 stand: 4 + 1)

    def test_a_research_row_with_no_time_keeps_its_packs(self):
        path = self.Path({RED: 0.1, GREEN: 0.5}, research=[{"name": "a", "packs": [RED]},
                                                           {"name": "b", "time": 10, "packs": [GREEN]}], unit=20.0)
        self.assertEqual(tier_builds._unit_time(path), (20.0, [RED]))


if __name__ == "__main__":
    unittest.main()
