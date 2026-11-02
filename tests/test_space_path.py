"""The way to space's next step (R-3): once a tier's research is done its production is due, and the goals that make what
the science path's own builder cannot (oil, a goal's prerequisite) are weighed; the launch, the silo and its part, last
(companion/space_path.py, tier_path's launch milestone, tier_builds' silo)."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import catalog_planner as cp  # noqa: E402
from companion import space_path, tier_builds, tier_path  # noqa: E402
from test_tier_path import BLUE, GREY, TIERS, researched, site  # noqa: E402

OIL_RECIPES = [{"name": n, "products": [{"name": p} for p in ps]} for n, ps in (
    ("pumpjack", ["pumpjack"]), ("small-electric-pole", ["small-electric-pole"]), ("oil-refinery", ["oil-refinery"]),
    ("chemical-plant", ["chemical-plant"]), ("basic-oil-processing", ["petroleum-gas"]),
    ("plastic-bar", ["plastic-bar"]), ("sulfur", ["sulfur"]))]


def plastics_researched(tiers=TIERS):
    """Blue's plastic and the oil under it researched, blue science's own not yet (stage-oil)."""
    out = researched(tiers, "plastics", "oil-processing")
    for t in out["tiers"]:
        for c in t["chain"]:
            if c["name"] in ("plastic-bar", "petroleum-gas"):
                c["enabled"] = True
                c.pop("tech", None)
    return out


def oil_site(**extra):
    return site(tiers=plastics_researched(), recipes=OIL_RECIPES, **extra)


class DueTests(unittest.TestCase):
    def test_a_supply_researched_ahead_of_its_tier_is_due_once_the_rocket_tiers_before_it_run(self):
        d = tier_path.due(oil_site(), "plastic-bar")
        self.assertTrue(d.due, d.why)                     # (blue science's own research not done yet)
        self.assertEqual(d.tier, BLUE)
        self.assertTrue(tier_path.due(oil_site(), "petroleum-gas").due)

    def test_a_tier_whose_research_is_done_is_due_even_if_another_comes_first(self):
        tiers = researched(plastics_researched(), "engine", "advanced-circuit", BLUE)
        for t in tiers["tiers"]:
            for c in t["chain"]:
                c["enabled"] = True
                c.pop("tech", None)
        path = tier_path.assess(site(tiers=tiers))
        self.assertEqual(path.next.pack, GREY)            # (grey is next by order)
        self.assertTrue(path.due(BLUE).due)               # (blue's research done: its line is due too)


class GoalTests(unittest.TestCase):
    def test_makers_by_recipe_resource_and_the_goal_lists_makes(self):
        s = oil_site()
        self.assertIn("oil-processing", space_path.makers("plastic-bar", s))
        self.assertIn("oil-processing", space_path.makers("petroleum-gas", s))
        self.assertIn("oil-gathering", space_path.makers("crude-oil", s))

    def test_a_goal_that_makes_any_one_of_its_recipes_counts_for_each(self):
        # (DS-2: oil processing needs plastic OR sulfur unlocked, `requires_any`, and makes whichever is)
        spec = cp.PURPOSES["oil-processing"]
        self.assertIn("plastic-bar", spec.get("requires_any") or spec.get("requires_recipes"))
        s = oil_site()
        self.assertIn("oil-processing", space_path.makers("sulfur", s))

    def test_a_goal_the_game_offers_no_layout_for_says_so(self):
        s = oil_site(completed_purposes=["oil-gathering"])
        with mock.patch.object(cp, "available", lambda p, s, designer=False: False), \
                mock.patch.object(cp, "why_unavailable", lambda p, s, designer=False: "not useful here now"):
            said = space_path.passed(s)
        self.assertTrue(any("offered no layout" in line for line in said), said)

    def test_a_goal_that_waits_on_another_brings_that_one_and_says_why(self):
        avail = {"oil-gathering"}
        with mock.patch.object(cp, "available", lambda p, s, designer=False: p in avail):
            got = space_path.goals(oil_site())
        self.assertEqual(got[0][0], "oil-gathering")
        self.assertIn("needs it first", got[0][2])

    def test_one_with_no_layout_here_is_said_not_dropped(self):
        with mock.patch.object(cp, "available", lambda p, s, designer=False: False):
            self.assertEqual(space_path.goals(oil_site()), [])
            said = space_path.passed(oil_site())
        self.assertTrue(any(line.startswith("plastic-bar:") and "oil-processing" in line for line in said), said)

    def test_the_weighing_gets_them_as_asked_for_by_the_science_path(self):
        planner = cp.CatalogPlanner(catalog=None, log=lambda _: None)
        seen = {}

        def choose(site, candidates, opening=(), asked=None, **_):
            seen["candidates"], seen["asked"] = list(candidates), list(asked or ())
            return None
        with mock.patch.object(cp, "opening_steps", lambda s, designer=False: ["bus-base"]), \
                mock.patch.object(space_path, "goals", lambda s, designer=False: [("oil-gathering", "plastic-bar", "x")]), \
                mock.patch("companion.goal_brain.choose", choose), \
                mock.patch("companion.goal_brain.defense", lambda g: False):
            planner._weighed_goal(oil_site())
        self.assertIn("oil-gathering", seen["candidates"])
        self.assertIn("oil-gathering", seen["asked"])


class OpenedValueTests(unittest.TestCase):
    """R-4: a goal whose value comes through what it opens the way to (oil gathering, oil processing, plastic, blue
    science) is valued from its successor's known products when the successor has no layout yet, down a chain."""

    def setUp(self):
        from companion import goal_brain as gb
        from companion.ratios import Book
        self.gb, self.book = gb, Book.from_site({})

    def test_a_successor_with_no_layout_is_known_by_what_it_makes_that_is_due(self):
        made = self.gb.known_products("oil-processing", oil_site(), self.book)
        self.assertGreater(made.get("plastic-bar", 0), 0)
        self.assertNotIn("oil-refinery", made)                   # (what it is built from is not what it makes)
        self.assertEqual(self.gb.known_products("oil-processing", site(), self.book), {})   # (before its research)

    def test_opening_the_way_is_worth_what_the_successor_makes(self):
        gb = self.gb
        with mock.patch.object(gb, "outputs", lambda p, s, b=None: ({}, {})), \
                mock.patch.object(gb, "item_value", lambda i, prio, book: 1.0), \
                mock.patch.object(gb, "hand_saved", lambda p, s, f: 0.0):
            opened = gb.opened_value("oil-gathering", oil_site(), self.book, prio=object(), facts=None, price=1.0)
        self.assertGreater(opened, 0)

    def weighed(self, chain, made, layouts=()):
        gb = self.gb
        with mock.patch.object(gb, "waiting_on", lambda p, s: chain.get(p, [])),                 mock.patch.object(gb, "outputs", lambda p, s, b=None: (made.get(p, {}) if p in layouts else {}, {})),                 mock.patch.object(gb, "known_products", lambda p, s, b: {} if p in layouts else made.get(p, {})),                 mock.patch.object(gb, "item_value", lambda i, prio, book: 1.0),                 mock.patch.object(gb, "hand_saved", lambda p, s, f: 0.0):
            return gb.opened_value("a", {}, None, prio=object(), facts=None, price=1.0)

    def test_down_a_chain_of_goals_with_no_layout_each_step_counts_a_share_of_the_next(self):
        share = self.gb.UNLOCK_SHARE
        chain, made = {"a": ["b"], "b": ["c"]}, {"b": {"x": 1.0}, "c": {"y": 1.0}}
        self.assertAlmostEqual(self.weighed(chain, made), share * (1.0 + share * 1.0))
        # (a successor with a layout counts as before, its own products only: the tuned opening is unchanged)
        self.assertAlmostEqual(self.weighed(chain, made, layouts={"b"}), share * 1.0)

    def test_a_goal_reached_two_ways_counts_once_and_a_loop_is_not_followed(self):
        share = self.gb.UNLOCK_SHARE
        diamond = {"a": ["b", "c"], "b": ["d"], "c": ["d"]}
        made = {"b": {"x": 1.0}, "c": {"y": 1.0}, "d": {"z": 1.0}}
        self.assertAlmostEqual(self.weighed(diamond, made), share * (1.0 + share * 1.0) + share * 1.0)
        # (reached by a long way and a short one: counted at the nearer)
        both = {"a": ["b", "d"], "b": ["c"], "c": ["d"]}
        self.assertAlmostEqual(self.weighed(both, {"b": {}, "c": {}, "d": {"z": 1.0}}), share * 1.0)
        loop = {"a": ["b"], "b": ["a"]}
        self.assertAlmostEqual(self.weighed(loop, {"b": {"x": 1.0}}), share * 1.0)

    def test_what_another_of_its_recipes_uses_up_is_no_output(self):
        made = self.gb.known_products("oil-processing", oil_site(), self.book)
        self.assertNotIn("petroleum-gas", made)                  # (its plastic is made from it)
        self.assertIn("plastic-bar", made)


class FeederTests(unittest.TestCase):
    """R-5: a line's intermediate chain comes with it: what its rows take from chests that nothing makes gets a row of
    its own, sized to what the line takes, down the chain until something already makes it."""

    def setUp(self):
        from companion.ratios import Book
        self.book = Book.from_site({})

    def rows(self, s, item, rate):
        return dict(tier_builds.feeders(item, rate, s, self.book, tier_path.assess(s)))

    def test_blue_science_brings_its_red_circuits_and_engines_and_what_they_take(self):
        got = self.rows(site(), BLUE, 0.27)
        for part in ("advanced-circuit", "engine-unit", "electronic-circuit"):
            self.assertIn(part, got)
        self.assertNotIn("sulfur", got)            # (a fluid's product: the oil block's, not a row's)
        self.assertNotIn("iron-plate", got)        # (the base's plates)
        order = list(got)
        self.assertLess(order.index("electronic-circuit"), order.index("advanced-circuit"))   # (its own first)

    def test_what_is_made_or_at_hand_needs_no_row(self):
        from test_tier_path import block
        made = block("engines", {"assembling-machine-2": 4}, ["engine-unit"], {"engine-unit": 1.0})
        self.assertNotIn("engine-unit", self.rows(site(base=[made]), BLUE, 0.27))
        held = {**TIERS, "held": {"advanced-circuit": 1000}}
        self.assertNotIn("advanced-circuit", self.rows(site(tiers=held), BLUE, 0.27))

    def test_a_row_standing_but_starved_is_not_asked_for_again(self):
        from test_tier_path import block
        starved = block("science-tier", {"assembling-machine-2": 6}, ["advanced-circuit"])
        starved["measured"] = {"minutes": 20, "shares": {"item_ingredient_shortage": 1.0}}   # (made nothing lately)
        starved["capacity"] = {"makes": {"advanced-circuit": 0.5}}
        self.assertNotIn("advanced-circuit", self.rows(site(base=[starved]), BLUE, 0.27))

    def test_an_ingredient_two_rows_take_is_sized_to_both(self):
        from companion.modules import Module, Port
        a, b = Module("a"), Module("b")
        a.ports = [Port("iron-gear-wheel", "in", 0, 0, 0, 0.2)]
        b.ports = [Port("iron-gear-wheel", "in", 0, 2, 0, 0.2)]
        with mock.patch.object(tier_builds, "fed_modules",
                               side_effect=lambda it, r, *x: ([a, b], r) if it == "x" else ([], r)), \
                mock.patch.object(tier_builds, "buildable", lambda *x: None):
            got = dict(tier_builds.feeders("x", 0.1, site(base=[]), self.book, tier_path.assess(site(base=[]))))
        self.assertAlmostEqual(got["iron-gear-wheel"], tier_builds.FEED_BLOCK_RATE)   # (0.4 asked, a block's worth)

    def test_a_supply_made_slower_than_the_line_wants_does_not_stop_it(self):
        from test_tier_path import block
        tiers = researched(plastics_researched(), "engine", "advanced-circuit", BLUE)
        for t in tiers["tiers"]:
            for c in t["chain"]:
                c["enabled"] = True
                c.pop("tech", None)
        some = block("oil-processing", {"chemical-plant": 1}, ["plastic-bar"], {"plastic-bar": 0.05})
        path = tier_path.assess(site(tiers=tiers, base=[some]))
        self.assertNotIn("plastic-bar", path.due(BLUE).waits)       # (some made: the line runs slower)
        self.assertIn("plastic-bar", tier_path.assess(site(tiers=tiers)).due(BLUE).waits)   # (none made: it waits)

    def test_a_partial_supply_no_row_can_make_sizes_the_line(self):
        from test_tier_path import block
        some = block("oil-processing", {"chemical-plant": 1}, ["plastic-bar"], {"plastic-bar": 0.05})
        s = site(base=[some])
        got = tier_builds.partial("advanced-circuit", 0.25, s, self.book, [])
        self.assertEqual(got, [("plastic-bar", 0.05)])
        full = tier_builds.fed_modules("advanced-circuit", 0.25, self.book, "assembling-machine-2", s)[1]
        self.assertLess(tier_builds.balanced("advanced-circuit", full, got, self.book, "assembling-machine-2", s), full)

    def test_a_feeder_row_that_found_no_place_is_passed_over_a_while(self):
        s = site(npc_id=7, tick=100 * 3600)
        tier_builds._UNPLACED[(7, "engine-unit")] = 100 * 3600 - 60
        try:
            self.assertTrue(tier_builds.unplaceable(s, "engine-unit"))
            self.assertFalse(tier_builds.unplaceable({**s, "tick": 100 * 3600 + tier_builds.FEED_RETRY}, "engine-unit"))
        finally:
            tier_builds._UNPLACED.clear()

    def test_a_builds_own_rows_feed_each_other(self):
        self.assertNotIn("piercing-rounds-magazine", self.rows(site(), GREY, 0.13))   # (its piercing row's)


def launch_tiers(enabled=True):
    """The tiers with the launch: the rocket's part made in the silo, from processing units, LDS and rocket fuel."""
    out = copy.deepcopy(TIERS)
    chain = [{"name": n, "type": "item", "per": 1, "recipe": n, "category": "crafting", "enabled": True}
             for n in ("processing-unit", "low-density-structure", "rocket-fuel")]
    out["tiers"].append({"pack": "rocket-part", "recipe": "rocket-part", "category": "rocket-building", "launch": True,
                         "rocket": True, "machine_ready": enabled, "enabled": enabled or None, "cost": 9e9,
                         "research": [] if enabled else [{"name": "rocket-silo", "units": 1000, "time": 60,
                                                          "packs": ["automation-science-pack"]}],
                         "chain": chain})
    out["made_in"]["rocket-building"] = ["rocket-silo"]
    return out


class LaunchTests(unittest.TestCase):
    def test_the_launch_is_due_once_the_silo_is_researched_and_waits_on_parts_not_held(self):
        path = tier_path.assess(site(tiers=launch_tiers()))
        d = path.due("rocket-part")
        self.assertTrue(d.due, d.why)
        self.assertIn("launch", d.why)
        self.assertEqual(set(d.waits), {"processing-unit", "low-density-structure", "rocket-fuel"})
        held = {"processing-unit": 50, "low-density-structure": 50, "rocket-fuel": 50}
        self.assertEqual(tier_path.assess(site(tiers=launch_tiers(), inventory=held)).due("rocket-part").waits, ())
        in_a_chest = {**launch_tiers(), "held": held}           # (the game's count: pockets and chests near it)
        self.assertEqual(tier_path.assess(site(tiers=in_a_chest)).due("rocket-part").waits, ())
        self.assertFalse(tier_path.assess(site(tiers=launch_tiers(False))).due("rocket-part").due)

    def test_a_silo_standing_idle_says_what_its_parts_wait_on_and_asks_no_second_silo(self):
        from test_tier_path import block
        silo = block("science-tier", {"rocket-silo": 1}, ["rocket-part"])
        silo["measured"] = {"minutes": 10, "shares": {"item_ingredient_shortage": 1.0}}
        d = tier_path.assess(site(tiers=launch_tiers(), base=[silo])).due("rocket-part")
        self.assertFalse(d.due)
        self.assertIn("silo stands", d.why)
        self.assertEqual(set(d.waits), {"processing-unit", "low-density-structure", "rocket-fuel"})

    def test_what_a_held_part_is_made_of_blocks_nothing_even_if_it_cannot_be_had_here(self):
        tiers = launch_tiers()
        chain = tiers["tiers"][-1]["chain"]
        for row in chain:
            row["by"] = ["rocket-part"]
        chain.append({"name": "other-planet-ice", "type": "item", "per": 10, "unavailable": True,
                      "by": ["processing-unit"]})
        held = {"processing-unit": 50, "low-density-structure": 50, "rocket-fuel": 50}
        path = tier_path.assess(site(tiers={**tiers, "held": held}))
        self.assertTrue(path.tier("rocket-part").unlocked)
        self.assertTrue(path.due("rocket-part").due)
        self.assertFalse(tier_path.assess(site(tiers=tiers)).tier("rocket-part").unlocked)   # (not held: it is needed)

    def test_a_consumer_made_far_below_its_need_still_needs_its_supply(self):
        from test_tier_path import block
        tiers = launch_tiers()
        chain = tiers["tiers"][-1]["chain"]
        for row in chain:
            row["by"] = ["rocket-part"]
        chain.append({"name": "plastic-bar", "type": "item", "per": 2, "recipe": "plastic-bar", "category": "chemistry",
                      "enabled": True, "by": ["low-density-structure"]})
        tiers["made_in"]["chemistry"] = ["chemical-plant"]
        trickle = block("lds", {"assembling-machine-2": 1}, ["low-density-structure"], {"low-density-structure": 0.0001})
        path = tier_path.assess(site(tiers=tiers, base=[trickle]))
        self.assertTrue(path.needed(path.tier("rocket-part"), "plastic-bar", 1.0))

    def test_the_launch_is_no_lab_pack(self):
        from test_tier_path import RED_BLOCK, GREEN_BLOCK, block
        silo = block("science-tier", {"rocket-silo": 1}, ["rocket-part"], {"rocket-part": 0.02})
        path = tier_path.assess(site(tiers=launch_tiers(), base=[RED_BLOCK, GREEN_BLOCK, silo]))
        self.assertNotIn("rocket-part", tier_builds._lab_packs(site(tiers=launch_tiers()), path, "rocket-part"))
        path = tier_path.assess(site(tiers=launch_tiers()))
        self.assertTrue(all("rocket-part" not in s for s in path._pack_sets()))

    def test_the_silo_is_planned_with_its_inputs_from_chests_and_proved_by_its_parts(self):
        from test_tier_builds import world
        # (ten minutes of parts at hand, by the recipe data's 10 of each a part: no rows for them)
        held = {"processing-unit": 1000, "low-density-structure": 1000, "rocket-fuel": 1000}
        s = site(tiers={**launch_tiers(), "held": held}, request_id="1:9", origin={"x": -32, "y": -32})
        plan = tier_builds.plan(s, world(), "rocket-part")
        silo = [e for e in plan["entities"] if e["prototype"] == "rocket-silo"]
        self.assertEqual(len(silo), 1)
        stage = plan["stages"][-1]
        self.assertEqual({x["item"] for x in stage["manual_supplies"]},
                         {"processing-unit", "low-density-structure", "rocket-fuel"})
        self.assertEqual(stage["witnesses"][0]["entity_id"], silo[0]["id"])
        self.assertEqual(stage["witnesses"][0]["item"], "rocket-part")

    def test_the_science_paths_goal_builds_a_silo_fed_from_chests(self):
        from companion.ratios import Book
        modules, rate = tier_builds.fed_modules("rocket-part", tier_path.MIN_RATE, Book.from_site({}), "assembling-machine-2", {})
        self.assertEqual(modules[0].name, "rocket-silo")
        self.assertEqual({p.item for p in modules[0].ports if p.kind == "in"},
                         {"processing-unit", "low-density-structure", "rocket-fuel"})


if __name__ == "__main__":
    unittest.main()
