"""Plate capacity a step ahead of what the science tiers will take (companion/plate_lead.py, F-13): the tiers' draw at
1/s each, whole arrays of 24 furnaces covering it, one more while the engineer waits on its furnaces, a missing source
first; arrays copied as the lead asks; science lines held while an array for it can be built."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import catalog_planner as cp  # noqa: E402
from companion import electric_start, plate_lead  # noqa: E402
import test_tier_path as tt  # noqa: E402

IRON, COPPER = "electric-iron-smelting", "electric-copper-smelting"


def site(**extra):
    plate_lead._CACHE.clear()
    return tt.site(minute=40, **extra)


def array(purpose, furnaces, index=0, target=7.5):
    return {"purpose": purpose, "machines": {"stone-furnace": furnaces},
            "keep": {"sites": ["field=1,1"], "target": target, "array": index}}


def lead(s, item):
    return next(d for d in plate_lead.leads(s) if d.item == item)


class DrawTests(unittest.TestCase):
    def test_the_tiers_draw_at_one_a_second_in_whole_arrays(self):
        s = site()
        draw = plate_lead.tier_draw(s)
        self.assertGreater(draw["iron-plate"], draw["copper-plate"] > 0)
        iron = lead(s, "iron-plate")
        self.assertEqual(iron.target % plate_lead.ARRAY_RATE, 0)                   # (whole arrays)
        self.assertGreaterEqual(iron.target, iron.draw)
        self.assertEqual(iron.purpose, IRON)

    def test_a_military_tier_counts_at_the_weapons_share(self):
        s = site()
        with mock.patch.object(plate_lead, "SCIENCE_RATE", 1.0):
            full = plate_lead.tier_draw(s)
        with mock.patch("companion.tier_path.Path.military_share", lambda self: 0.0):
            plate_lead._CACHE.clear()
            none = plate_lead.tier_draw(s)
        self.assertLess(none["iron-plate"], full["iron-plate"])

    def test_built_arrays_count_and_met_wants_no_more(self):
        s = site()
        need = lead(s, "iron-plate").target
        arrays = [array(IRON, 24, k) for k in range(int(need / plate_lead.ARRAY_RATE))]
        fed = site(base=arrays)
        self.assertFalse(plate_lead.wants(fed, "iron-plate"))
        self.assertTrue(plate_lead.wants(site(), "iron-plate"))

    def test_waits_ask_one_more_array_and_a_missing_source_comes_first(self):
        need = lead(site(), "iron-plate").target
        arrays = [array(IRON, 24, k) for k in range(int(need / plate_lead.ARRAY_RATE))]
        waited = site(base=arrays, plate_wait={"iron-plate": {"ticks": 4000, "want": 20, "since": 1}})
        self.assertTrue(plate_lead.wants(waited, "iron-plate"))
        self.assertIn("waited on", lead(waited, "iron-plate").why)
        brief = site(base=arrays, plate_wait={"iron-plate": {"ticks": 100, "want": 20, "since": 1}})
        self.assertFalse(plate_lead.wants(brief, "iron-plate"))                    # (a moment's wait: not more)
        none = site(base=arrays, plate_wait={"stone-brick": {"ticks": 50, "want": 10, "since": 1, "none": True}})
        self.assertEqual(plate_lead.leads(none)[0].item, "stone-brick")


class GoalTests(unittest.TestCase):
    def test_the_shortest_buildable_array_and_science_lines_held_meanwhile(self):
        s = site()
        with mock.patch.object(cp, "available", lambda g, st, designer=False: g in (IRON, COPPER)):
            got = plate_lead.goal(s)
            self.assertIn(got[0], (IRON, COPPER))
            self.assertTrue(plate_lead.holds(s))
        with mock.patch.object(plate_lead, "holds", return_value=True):
            self.assertTrue(cp.held_for_plates("green-science-automation", s))
            self.assertFalse(cp.held_for_plates("red-science-automation", s))     # (the first small line runs)
            self.assertFalse(cp.held_for_plates("steam-power", s))
            pack = mock.Mock(item="logistic-science-pack", labs=0, rate=0.1)
            with mock.patch("companion.tier_builds.cached_pick", return_value=pack):
                self.assertTrue(cp.held_for_plates("science-tier", s))
            lab = mock.Mock(item="lab", labs=0, rate=1)
            with mock.patch("companion.tier_builds.cached_pick", return_value=lab):
                few = {**s, "base": [{"purpose": "research-lab", "machines": {"lab": 1}}]}
                self.assertFalse(cp.held_for_plates("science-tier", few))           # (its first labs)
                many = {**s, "base": [{"purpose": "science-tier", "machines": {"lab": plate_lead.MIN_LABS}}]}
                self.assertTrue(cp.held_for_plates("science-tier", many))           # (more: science scale, held)

    def test_science_waits_on_the_draw_not_on_whole_arrays(self):
        # (red and green at 1/s draw 2.5 copper plates a second: eight furnaces let green go on; the array's rest is
        # growth, Codex's F-13 review)
        s = site()
        copper = lead(s, "copper-plate")
        covered = site(base=[array(COPPER, int(copper.draw / 0.3125) + 1, 0)])
        d = lead(covered, "copper-plate")
        self.assertGreater(d.short, 0)
        self.assertEqual(d.under, 0)
        with mock.patch.object(cp, "available", lambda g, st, designer=False: g == COPPER):
            self.assertFalse(plate_lead.holds(covered))
            self.assertEqual(plate_lead.goal(covered)[0], COPPER)          # (finished as growth)

    def test_steel_and_what_a_furnace_makes_from_plates_count(self):
        self.assertEqual(plate_lead.products()["steel-plate"], "steel-smelting")
        none = site(plate_wait={"steel-plate": {"ticks": 10, "want": 5, "since": 1, "none": True}})
        steel = lead(none, "steel-plate")
        self.assertGreater(steel.under, 0)
        self.assertEqual(plate_lead.leads(none)[0].item, "steel-plate")

    def test_the_bus_is_fed_from_the_first_array_s_store_alone(self):
        from companion import plate_feed
        first = {**array(IRON, 24, 0), "capacity": {"makes": {"iron-plate": 7.5}}}
        first["keep"]["sites"] = ["outpost-iron-plate-0=10,20"]
        second = {**array(IRON, 24, 1), "capacity": {"makes": {"iron-plate": 7.5}}}
        second["keep"]["sites"] = ["outpost-iron-plate-1=50,60"]
        got = plate_feed.sources(site(base=[first, second]))
        self.assertAlmostEqual(got["iron-plate"][1], 7.5)

    def test_a_real_shortage_comes_before_finishing_an_array(self):
        s = site()
        copper, iron = lead(s, "copper-plate"), lead(s, "iron-plate")
        # (copper's draw met with its array unfinished; iron still under its draw)
        base = [array(COPPER, max(0, int((copper.draw - copper.have) / 0.3125)) + 1, 0),
                array(IRON, int((iron.draw - iron.have) / 0.3125) - 2, 0)]
        mixed = site(base=list(site()["base"]) + base)   # (the science blocks stay: the same tiers drawing)
        with mock.patch.object(cp, "available", lambda g, st, designer=False: g in (IRON, COPPER)):
            self.assertEqual(plate_lead.goal(mixed)[0], IRON)

    def test_steel_s_iron_counts_in_the_iron_draw(self):
        from companion import ratios
        with mock.patch.object(plate_lead, "products", return_value={"iron-plate": IRON, "steel-plate": "steel-smelting"}),                 mock.patch("companion.tier_path.assess") as assess:
            tier = mock.Mock(pack="steel-chest", automated=True, unavailable=(), military=False)
            assess.return_value = mock.Mock(tiers=[tier], ahead=lambda t: False)
            draw = plate_lead.tier_draw(site())
        self.assertAlmostEqual(draw["steel-plate"], 8.0 * 1.0 / ratios.Book.vanilla().recipe_for("steel-chest").makes("steel-chest"))
        self.assertAlmostEqual(draw["iron-plate"], 5 * draw["steel-plate"])

    def test_nothing_buildable_is_said_and_science_goes_on(self):
        s = site()
        with mock.patch.object(cp, "available", lambda g, st, designer=False: False):
            self.assertIsNone(plate_lead.goal(s))
            self.assertFalse(plate_lead.holds(s))
            self.assertTrue(any("no array of it can be built now" in n for n in plate_lead.news(s)))

    def test_the_ledger_s_lead_comes_after_what_is_short_now(self):
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        planner.designer, planner.log = None, lambda line: None
        s = site()
        found = plate_lead.Lead("iron-plate", IRON, 8.0, 15.0, 7.5, "the tiers take 480/min")
        with mock.patch.object(cp.priorities, "pressing", return_value=[]), \
                mock.patch.object(plate_lead, "goal", return_value=(IRON, found)):
            goal, why = planner.supply_first(s)
        self.assertEqual(goal, IRON)
        self.assertIn("480/min", why.line())


class ArrayTests(unittest.TestCase):
    """Arrays: each grown in place with its own keep; a new copy once the last is whole."""

    def test_the_next_part_goes_to_the_latest_array_until_whole_then_a_new_one(self):
        part = site(base=[array(IRON, 24, 0), array(IRON, 8, 1)])
        self.assertEqual(electric_start.next_array(part, IRON), 1)
        self.assertFalse(electric_start.grown(part, IRON))
        self.assertAlmostEqual(electric_start.built_rate(part, IRON, 0), 7.5)
        self.assertAlmostEqual(electric_start.built_rate(part, IRON), 10.0)
        whole = site(base=[array(IRON, 24, 0)])
        self.assertTrue(electric_start.grown(whole, IRON))
        self.assertEqual(electric_start.next_array(whole, IRON), 1)
        self.assertIsNone(electric_start.kept(whole, IRON, 1))                     # (a fresh spot for the copy)
        self.assertEqual(electric_start.next_array(site(base=[]), IRON), 0)

    def test_an_array_whole_is_offered_again_only_while_the_plates_want_more(self):
        whole = site(base=[array(IRON, 24, 0)], completed_purposes=[IRON, "steam-power"])
        with mock.patch.object(plate_lead, "wants", return_value=True):
            more = cp.available(IRON, whole)
        with mock.patch.object(plate_lead, "wants", return_value=False):
            done = cp.available(IRON, whole)
        self.assertFalse(done)
        self.assertEqual(more, IRON in cp.PURPOSES and all(
            n in cp.ratios.unlocked(whole) for n in cp.PURPOSES[IRON]["requires_recipes"])
            and cp.PURPOSES[IRON]["resource"] in cp.known_resources(whole))


class IncomeTests(unittest.TestCase):
    """F-14, fresh run 61: an array's first part (512 buildings) on 30 iron a minute; the income first."""

    def test_an_array_its_income_cannot_buy_soon_gives_way_to_pairs_then_comes(self):
        s = site()
        only = lambda g, st, designer=False: g in (IRON, COPPER, "iron-smelting-expansion", "copper-smelting-start")
        with mock.patch.object(cp, "available", only), mock.patch.object(cp, "coal_enough", return_value=True):
            with mock.patch.object(plate_lead, "affordable", return_value=False):
                goal, why = plate_lead.goal(s)
                self.assertIn(goal, ("iron-smelting-expansion", "copper-smelting-start"))
                self.assertIn("income first", why.why)
                self.assertIn("a plate spent", why.why)
            with mock.patch.object(plate_lead, "affordable", return_value=True):
                self.assertIn(plate_lead.goal(s)[0], (IRON, COPPER))

    def test_the_coal_its_burners_burn_comes_before_more_burners(self):
        s = site()
        only = lambda g, st, designer=False: g in ("coal-loop", "iron-smelting-expansion", "copper-smelting-start")
        with mock.patch.object(cp, "available", only), mock.patch.object(cp, "coal_enough", return_value=False):
            self.assertEqual(plate_lead.goal(s)[0], "coal-loop")
            self.assertTrue(plate_lead.holds(s))                          # (science waits meanwhile)

    def test_affordable_is_the_part_s_plates_against_the_income(self):
        s = site()
        with mock.patch.object(plate_lead, "part_cost", return_value={"iron-plate": 400.0}):
            with mock.patch.object(plate_lead, "income", return_value=0.5):   # (30 a minute: 13 minutes)
                self.assertFalse(plate_lead.affordable(s, IRON))
            with mock.patch.object(plate_lead, "income", return_value=1.0):   # (60 a minute: under 7)
                self.assertTrue(plate_lead.affordable(s, IRON))
        self.assertGreater(plate_lead.part_cost(site(base=[]), IRON).get("iron-plate", 0), 0)

    def test_a_copper_array_waits_on_the_iron_it_costs(self):
        s = site()
        rates = {"copper-plate": 5.0, "iron-plate": 0.5}
        with mock.patch.object(plate_lead, "part_cost", return_value={"copper-plate": 40.0, "iron-plate": 560.0}),                 mock.patch.object(plate_lead, "income", lambda st, item: rates.get(item, 0.0)):
            self.assertFalse(plate_lead.affordable(s, COPPER))
            self.assertEqual(plate_lead.limiting(s, COPPER), "iron-plate")
            only = lambda g, st, designer=False: g in (COPPER, "iron-smelting-expansion", "copper-smelting-start")
            with mock.patch.object(cp, "available", only), mock.patch.object(cp, "coal_enough", return_value=True):
                d = plate_lead.lead_of(s, "copper-plate")
                with mock.patch.object(plate_lead, "leads", return_value=[plate_lead.Lead(
                        "copper-plate", COPPER, 2.0, 7.5, 1.0, "the tiers take 120/min")]):
                    self.assertEqual(plate_lead.goal(s)[0], "iron-smelting-expansion")   # (iron, not more copper)
                self.assertEqual(d.item, "copper-plate")

    def test_iron_s_own_affordable_array_grows_it_when_its_pairs_are_closed(self):
        s = site()
        only = lambda g, st, designer=False: g in (COPPER, IRON)
        with mock.patch.object(cp, "available", only), mock.patch.object(cp, "coal_enough", return_value=True),                 mock.patch.object(plate_lead, "limiting", lambda st, p: "iron-plate" if p == COPPER else None),                 mock.patch.object(plate_lead, "leads", return_value=[plate_lead.Lead(
                    "copper-plate", COPPER, 2.0, 7.5, 1.0, "the tiers take 120/min")]):
            self.assertEqual(plate_lead.goal(s)[0], IRON)

    def test_an_unaffordable_array_is_not_offered_while_its_income_can_grow(self):
        powered = site(base=[], completed_purposes=["steam-power", "iron-smelting-start", "copper-smelting-start",
                                                    "coal-loop"])
        with mock.patch.object(plate_lead, "limiting", lambda st, p: "iron-plate" if p == COPPER else None),                 mock.patch.object(plate_lead, "income_goal", return_value=("iron-smelting-expansion", "pairs")):
            self.assertFalse(cp.available(COPPER, powered))
        with mock.patch.object(plate_lead, "limiting", lambda st, p: "iron-plate"),                 mock.patch.object(plate_lead, "income_goal", return_value=None):
            self.assertFalse(plate_lead.gated(powered, COPPER))                 # (nothing grows it: the array)

    def test_with_real_availability_a_copper_array_short_of_iron_grows_the_iron_array(self):
        import test_electric_start as te
        powered = te.site(te.BURNERS + ["steam-power"])
        plate_lead._CACHE.clear()
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        planner.designer, planner.log = None, lambda line: None
        copper = plate_lead.Lead("copper-plate", COPPER, 2.0, 7.5, 1.0, "the tiers take 120/min")
        with mock.patch.object(plate_lead, "limiting", lambda st, p: "iron-plate" if p == COPPER else None),                 mock.patch.object(plate_lead, "leads", return_value=[copper]),                 mock.patch.object(cp, "coal_enough", return_value=True):
            self.assertFalse(cp.available(COPPER, powered))                   # (gated: iron's income first)
            self.assertEqual(plate_lead.goal(powered)[0], IRON)
            with mock.patch.object(cp.priorities, "pressing", return_value=[mock.Mock(item="copper-plate")]):
                self.assertEqual(planner.supply_first(powered)[0], IRON)

    def test_the_gate_s_guard_is_per_thread(self):
        import threading
        seen = []
        with plate_lead.ungated(COPPER):
            t = threading.Thread(target=lambda: seen.append(COPPER in plate_lead._open()))
            t.start()
            t.join()
            self.assertIn(COPPER, plate_lead._open())
        self.assertEqual(seen, [False])
        self.assertNotIn(COPPER, plate_lead._open())

    def test_a_shortage_now_grows_the_income_before_an_array_it_cannot_afford(self):
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        planner.designer, planner.log = None, lambda line: None
        budget = mock.Mock(item="iron-plate")
        only = lambda g, st, designer=False: g in (IRON, "iron-smelting-expansion")
        with mock.patch.object(cp.priorities, "pressing", return_value=[budget]),                 mock.patch.object(cp, "available", only), mock.patch.object(cp, "coal_enough", return_value=True),                 mock.patch.object(plate_lead, "limiting", return_value="iron-plate"):
            self.assertEqual(planner.supply_first(site())[0], "iron-smelting-expansion")

    def test_pairs_have_a_payback_from_their_flows_and_cost(self):
        s = site(base=[])
        pairs = plate_lead._payback(s, "iron-smelting-expansion", "iron-plate")
        self.assertGreater(pairs, 0)


class RunSixtyTwoTests(unittest.TestCase):
    """F-15, fresh run 62: burner pairs closed for an array still researching, 9 labs at minute 22, steam at 18.5."""

    def test_an_array_waiting_on_research_leaves_the_pairs_open_while_its_plates_are_under(self):
        import test_electric_start as te
        missing = [r for r in te.ALL_RECIPES if r["name"] != "electric-mining-drill"]
        waiting = te.site(te.BURNERS + ["steam-power"], recipes=missing, research_near=["electric-mining-drill"])
        plate_lead._CACHE.clear()
        with mock.patch.object(plate_lead, "under", return_value=True):
            self.assertFalse(cp.burner_closed("iron-smelting-expansion", waiting))
        with mock.patch.object(plate_lead, "under", return_value=False),                 mock.patch.object(plate_lead, "limiting", return_value=None):
            self.assertTrue(cp.burner_closed("iron-smelting-expansion", waiting))   # (plates met: as before)

    def test_labs_past_the_first_are_held_only_while_the_plates_can_grow(self):
        s = site()
        labs = mock.Mock(item="lab", labs=0, rate=9)
        with mock.patch("companion.tier_builds.cached_pick", return_value=labs):
            with mock.patch.object(plate_lead, "holds", return_value=True):
                self.assertTrue(cp.held_for_plates("science-tier", s))
            with mock.patch.object(plate_lead, "holds", return_value=False):   # (nothing grows them: never held)
                self.assertFalse(cp.held_for_plates("science-tier", s))

    def test_a_pack_row_with_labs_is_never_stalled_when_nothing_grows_the_plates(self):
        s = site()
        red = mock.Mock(item="automation-science-pack", labs=9, rate=0.26)
        with mock.patch("companion.tier_builds.cached_pick", return_value=red),                 mock.patch.object(plate_lead, "under_any", return_value=True):
            with mock.patch.object(plate_lead, "holds", return_value=False):
                self.assertFalse(cp.held_for_plates("science-tier", s))      # (research goes on)
            with mock.patch.object(plate_lead, "holds", return_value=True):
                self.assertTrue(cp.held_for_plates("science-tier", s))       # (plates can grow: they first)

    def test_a_winning_lead_goal_is_said_even_when_also_asked_for(self):
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        logs = []
        planner.designer, planner.log = None, logs.append
        found = plate_lead.Lead("iron-plate", IRON, 2.0, 7.5, 1.0, "the tiers take 120/min; income first: pairs")
        s = site(base=[], completed_purposes=["steam-power"], asked="iron-smelting-expansion")
        with mock.patch.object(cp, "opening_steps", return_value=["iron-smelting-expansion"]),                 mock.patch.object(planner, "supply_first", return_value=("iron-smelting-expansion", found)),                 mock.patch.object(planner, "_defense_production_goals", return_value=[]),                 mock.patch.object(cp, "available", lambda g, st, designer=False: g == "iron-smelting-expansion"),                 mock.patch("companion.goal_brain.choose", return_value=(
                    "iron-smelting-expansion", [{"goal": "iron-smelting-expansion", "score": 2.0}])):
            self.assertEqual(planner._weighed_goal(s), "iron-smelting-expansion")
        self.assertTrue(any("income first" in line for line in logs), logs)

    def test_the_lead_is_weighed_first_only_once_power_stands_and_is_said(self):
        self.assertFalse(plate_lead.ready_for_arrays(site(base=[], completed_purposes=["coal-loop"])))
        self.assertTrue(plate_lead.ready_for_arrays(site(base=[], completed_purposes=["steam-power"])))


class BusTests(unittest.TestCase):
    """F-19, Research's stage-blue run: on a bus base the iron array is "not useful here", the lead's growth never
    offered; a hold there waited for good. Its holds go with its offers."""

    def test_no_hold_for_plates_where_its_growth_is_never_offered(self):
        s = site(base_plan={"stages": ["power", "smelting-iron-plate"]})
        with mock.patch.object(cp, "available", lambda g, st, designer=False: True),                 mock.patch.object(plate_lead, "income_goal", return_value=("iron-smelting-expansion", "pairs")):
            self.assertFalse(plate_lead.holds(s))
            pack = mock.Mock(item="chemical-science-pack", labs=0, rate=0.1)
            with mock.patch("companion.tier_builds.cached_pick", return_value=pack):
                self.assertFalse(cp.held_for_plates("science-tier", s))
        with mock.patch.object(cp, "available", lambda g, st, designer=False: True),                 mock.patch.object(plate_lead, "income_goal", return_value=("iron-smelting-expansion", "pairs")):
            self.assertTrue(plate_lead.holds(site()))                    # (before a bus: held while it grows)


if __name__ == "__main__":
    unittest.main()


class DrawOrderTests(unittest.TestCase):
    """F-21 #6: the plate furthest under its draw leads, by the amount: early iron before copper."""

    def test_iron_far_under_its_draw_leads_copper_with_none_yet(self):
        from companion import defense_production
        have = {"iron-plate": 0.5, "copper-plate": 0.0}
        with mock.patch.object(plate_lead, "tier_draw", return_value={"iron-plate": 2.0, "copper-plate": 0.7}),                 mock.patch.object(plate_lead, "products", return_value={"iron-plate": "electric-iron-smelting",
                                                                         "copper-plate": "electric-copper-smelting"}),                 mock.patch.object(plate_lead, "_have", side_effect=lambda site, item, p: have[item]),                 mock.patch.object(plate_lead, "_claimed", return_value=0.0),                 mock.patch.object(plate_lead, "_waits", return_value={}),                 mock.patch.object(defense_production, "plate_draw", return_value={}):
            order = [d.item for d in plate_lead._leads({})]
        self.assertEqual(order, ["iron-plate", "copper-plate"])   # (1.5/s under against copper's 0.7/s)
