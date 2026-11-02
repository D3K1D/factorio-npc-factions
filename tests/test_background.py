"""Plans that take longer than a request may wait, worked out in the background (companion/background.py): run 55's
bus plans took 7 to 8 real minutes, past what the game waits, and each one was thrown away when it came
(2026-10-26)."""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from companion import background, bus_planner
from companion import blueprints as bp
from companion import catalog_planner as cp
from companion.modules import SOUTH, Placed
from companion.smelting import _UNDERGROUNDS, undergrounds

FIXTURES = Path(__file__).parent / "fixtures"


class SearchTests(unittest.TestCase):
    def setUp(self):
        background._jobs.clear()

    def test_a_quick_search_is_answered_at_once_and_taken(self):
        job = background.search("npc", ("bus-base", "a"), lambda: 42, tick=100)
        self.assertEqual(job.outcome(), 42)
        self.assertEqual(background.running("npc"), [])   # (taken: a result is used once)

    def test_a_slow_one_is_left_running_and_a_later_request_takes_it(self):
        go = threading.Event()
        job = background.search("npc", ("bus-base", "a"), lambda: go.wait(5) and "plan", tick=100, wait_s=0.01)
        self.assertIsNone(job)
        self.assertEqual(background.pending_goals("npc"), {"bus-base"})
        go.set()
        job = background.search("npc", ("bus-base", "a"), lambda: "again", tick=200, wait_s=5)
        self.assertEqual((job.outcome(), job.tick), ("plan", 100))   # (the first search's, begun at tick 100)
        self.assertEqual(background.pending_goals("npc"), set())

    def test_one_search_an_engineer_at_a_time_and_others_go_on(self):
        go = threading.Event()
        background.search("npc", ("bus-base", "a"), lambda: go.wait(5), wait_s=0)
        self.assertIsNone(background.search("npc", ("bus-base", "b"), lambda: 1, wait_s=0.5))   # (waits its turn)
        self.assertEqual(background.search("other", ("bus-base", "b"), lambda: 2).outcome(), 2)
        go.set()

    def test_a_search_from_before_a_save_was_loaded_is_dropped(self):
        go = threading.Event()
        background.search("npc", ("bus-base", "a"), lambda: go.wait(5) and "old", tick=9000, wait_s=0)
        # (still under way: no second search for the engineer beside it; a thread cannot be stopped)
        self.assertIsNone(background.search("npc", ("bus-base", "a"), lambda: "new", tick=100, wait_s=0))
        self.assertEqual(len(background.running("npc")), 1)
        go.set()
        time.sleep(0.05)
        job = background.search("npc", ("bus-base", "a"), lambda: "new", tick=100)
        self.assertEqual(job.outcome(), "new")

    def test_the_planner_s_settings_go_with_it_and_its_error_comes_back(self):
        with undergrounds(False):
            job = background.search("npc", ("bus-base", "a"), lambda: _UNDERGROUNDS.get())
        self.assertIs(job.outcome(), False)
        job = background.search("npc", ("bus-base", "b"), lambda: 1 / 0)
        with self.assertRaises(ZeroDivisionError):
            job.outcome()


def belt(x, y):
    return Placed("transport-belt", x + 0.5, y + 0.5, SOUTH)


class BusInBackgroundTests(unittest.TestCase):
    SITE = {"npc_id": 1, "request_id": "1:30", "tick": 1000, "origin": {"x": 0, "y": 0}}
    WORLD = {"entities": [], "water": [], "home": {"x": 0, "y": 0}}

    def setUp(self):
        background._jobs.clear()

    def stage(self, go, calls):
        def made(site, world, book=None, library=None):
            calls.append(site["request_id"])
            go.wait(5)
            return ({"plan_id": f"bus-base-power-{site['request_id']}", "base_plan": {"stage": "power"},
                     "notes": ["kept"]}, [belt(3, 4)])
        return made

    def test_a_late_plan_goes_with_the_request_that_takes_it(self):
        go, calls = threading.Event(), []
        with patch.object(bus_planner, "_stage", self.stage(go, calls)):
            with self.assertRaises(background.Pending):
                bus_planner.plan_in_background(self.SITE, self.WORLD, wait_s=0.01)
            go.set()
            later = {**self.SITE, "request_id": "1:34", "tick": 9000}
            plan = bus_planner.plan_in_background(later, self.WORLD, wait_s=5)
        self.assertEqual(calls, ["1:30"])                       # (searched once, not again from scratch)
        self.assertEqual(plan["plan_id"], "bus-base-power-1-34")
        self.assertEqual(plan["notes"][0], "kept")
        self.assertTrue(plan["notes"][-1].startswith("planned in the background in"))

    def test_one_whose_ground_was_built_on_meanwhile_is_searched_again(self):
        go, calls = threading.Event(), []
        with patch.object(bus_planner, "_stage", self.stage(go, calls)):
            with self.assertRaises(background.Pending):
                bus_planner.plan_in_background(self.SITE, self.WORLD, wait_s=0.01)
            go.set()
            built = {**self.WORLD, "entities": [{"n": "stone-furnace", "t": "furnace", "x": 4, "y": 5, "w": 2, "h": 2}]}
            later = {**self.SITE, "request_id": "1:34", "tick": 9000}
            plan = bus_planner.plan_in_background(later, built, wait_s=5)
        self.assertEqual(calls, ["1:30", "1:34"])
        self.assertEqual(plan["plan_id"], "bus-base-power-1:34")   # (this request's own search)

    def test_another_bus_state_is_another_search(self):
        site = {**self.SITE, "base_plan": {"stages": ["power"], "done": {}}}
        self.assertNotEqual(bus_planner.signature(site),
                            bus_planner.signature({**site, "base_plan": {"stages": ["power"], "done": {"power": 1}}}))
        self.assertEqual(bus_planner.signature(site), bus_planner.signature({**site, "tick": 99999}))
        # (a plan is in the site's local tiles: another site is another search)
        self.assertNotEqual(bus_planner.signature(site), bus_planner.signature({**site, "origin": {"x": 64, "y": 0}}))

    def test_what_it_retires_is_worked_out_again_by_the_request_that_takes_it(self):
        go = threading.Event()

        def made(site, world, book=None, library=None):
            go.wait(5)
            return ({"plan_id": "x", "retire": ["stale"],
                     "base_plan": {"stage": "smelting-iron-plate", "stages": ["power", "smelting-iron-plate", "bus-a"],
                                   "packs": ["automation-science-pack"], "extra": []}}, [belt(3, 4)])
        with patch.object(bus_planner, "_stage", made):
            with self.assertRaises(background.Pending):
                bus_planner.plan_in_background(self.SITE, self.WORLD, wait_s=0.01)
            go.set()
            later = {**self.SITE, "request_id": "1:34", "tick": 9000, "completed_purposes": ["iron-smelting-start"]}
            plan = bus_planner.plan_in_background(later, self.WORLD, wait_s=5)
        self.assertEqual(plan["retire"], ["iron-smelting-start"])   # (built while the stage was searched)

    def test_research_the_new_bus_does_not_use_is_not_another_search(self):
        from companion.mall import GROUPS
        mall = sorted({r for group in GROUPS for r in group})
        site = {**self.SITE, "recipes": [{"name": "iron-gear-wheel"}]}
        self.assertEqual(bus_planner.signature(site),
                         bus_planner.signature({**site, "recipes": [*site["recipes"], {"name": "not-in-any-mall"}]}))
        more = next(r for r in mall if r != "iron-gear-wheel")
        self.assertNotEqual(bus_planner.signature(site),
                            bus_planner.signature({**site, "recipes": [*site["recipes"], {"name": more}]}))

    def test_another_factory_plan_target_is_another_search(self):
        from companion import factory_plan
        kept = factory_plan.BusTargets(20.0, ("automation-science-pack",), 0, reserve=6, area=(0, 0, 90, 200))
        with patch.object(factory_plan, "bus_targets", return_value=kept):
            first = bus_planner.signature(self.SITE)
        with patch.object(factory_plan, "bus_targets", return_value=factory_plan.BusTargets(
                20.0, ("automation-science-pack",), 0, reserve=12, area=(0, 0, 90, 200))):
            wider = bus_planner.signature(self.SITE)
        self.assertNotEqual(first, wider)


class TapSearchTests(unittest.TestCase):
    """A coal line's spots are searched until the searches have cost LINE_SEARCH states: spots walled in close by cost
    next to nothing and the next is tried; real searches that find no way give the line up."""
    DUMP = {"area": [-50, -50, 50, 50], "entities": [],
            "belt_lines": [{"id": "coal@0,-20", "item": "coal", "tiles": [(0, y, SOUTH) for y in range(-20, 20)],
                            "supply": 3.0, "claims": {}, "ok": True}]}

    def tapped(self, searched):
        from companion import belt_lines, smelting
        calls = []

        def route(start, goal, blocked, *, bounds, expansions=None, stats=None, **_):
            calls.append(expansions)
            cost = searched(len(calls))
            if cost is None:
                return [belt(*start), belt(*goal)]
            stats["searched"] = cost
            return None
        with patch.object(smelting, "route", route), belt_lines.taps(True):
            found = belt_lines.tap(self.DUMP, "coal", (10, 0), 0.5, set())
        return found, calls

    def test_spots_walled_in_close_by_do_not_give_the_line_up(self):
        found, calls = self.tapped(lambda n: 0 if n < 9 else None)
        self.assertIsNotNone(found)
        self.assertEqual(len(calls), 9)

    def test_searches_that_find_no_way_give_the_line_up_at_its_budget(self):
        from companion import belt_lines
        found, calls = self.tapped(lambda n: belt_lines.TAP_SEARCH)
        self.assertIsNone(found)
        self.assertEqual(len(calls), belt_lines.LINE_SEARCH // belt_lines.TAP_SEARCH)
        self.assertTrue(all(c <= belt_lines.TAP_SEARCH for c in calls))


class MeanwhileTests(unittest.TestCase):
    def setUp(self):
        background._jobs.clear()
        self.site = {**json.loads((FIXTURES / "bw-opening-2min.json").read_text(encoding="utf-8")),
                     "purpose": "choose"}
        self.planner = cp.CatalogPlanner(bp.Catalog(tempfile.mkdtemp()), log=lambda _: None)

    def test_a_goal_being_worked_out_is_not_available_meanwhile(self):
        self.assertFalse(cp.available("bus-base", {**self.site, "worked_out": ["bus-base"]}))

    def test_the_engineer_builds_its_next_goal_while_the_bus_plan_is_worked_out(self):
        chosen = []

        def choose(site):
            chosen.append(list(site.get("worked_out") or ()))
            return "wall-automation" if site.get("worked_out") else "bus-base"
        with patch.object(self.planner, "choose_purpose", side_effect=choose), \
                patch.object(self.planner, "_bus", side_effect=background.Pending("bus-base", 21)), \
                patch.object(self.planner, "_wall_block", return_value={"purpose": "wall-automation"}):
            plan = self.planner._plan(self.site)
        self.assertEqual(plan, {"purpose": "wall-automation"})
        self.assertEqual(chosen, [[], ["bus-base"]])

    def test_with_nothing_else_to_build_it_asks_again_soon(self):
        def choose(site):
            return cp.CONTINUE_PROJECTS if site.get("worked_out") else "bus-base"
        with patch.object(self.planner, "choose_purpose", side_effect=choose), \
                patch.object(self.planner, "_bus", side_effect=background.Pending("bus-base", 21)):
            with self.assertRaises(cp.Defer) as deferred:
                self.planner._plan(self.site)
        self.assertEqual(deferred.exception.ticks, cp.PENDING_TICKS)


if __name__ == "__main__":
    unittest.main()


class AdoptTests(unittest.TestCase):
    """F-21 #2: a plan found for the site as it was is taken when it still holds, and a ready one is chosen first."""

    def test_a_finished_plan_under_an_older_key_is_adopted_when_it_holds(self):
        background._jobs.clear()
        job = background.Search("npc-a", ("bus-base", "old"), 10, lambda: ("plan", 1))
        job.thread.start()
        job.thread.join(5)
        background._jobs[("npc-a", ("bus-base", "old"))] = job
        self.assertEqual(background.ready_goals("npc-a"), {"bus-base"})
        refused = background.search("npc-a", ("bus-base", "new"), lambda: ("other", 2), tick=20, wait_s=5,
                                    adopt=lambda j: False)
        self.assertEqual(refused.result, ("other", 2))       # (it does not hold: searched afresh)
        background._jobs[("npc-a", ("bus-base", "old"))] = job
        took = background.search("npc-a", ("bus-base", "newer"), lambda: ("third", 3), tick=20, wait_s=5,
                                 adopt=lambda j: True)
        self.assertIs(took, job)
        self.assertEqual(background.ready_goals("npc-a"), set())
        background._jobs.clear()


class StillHoldsTests(unittest.TestCase):
    def test_a_stage_planned_before_holds_until_built_or_the_bus_changes(self):
        from types import SimpleNamespace
        plan = {"base_plan": {"x": 1, "y": 2, "stage": "bus-gears", "stages": ["power", "bus-gears"], "extra": []}}
        job = SimpleNamespace(result=(plan, [], {"_origin": {"x": 0, "y": 0}}))
        site = {"origin": {"x": 0, "y": 0},
                "base_plan": {"x": 1, "y": 2, "stages": ["power", "bus-gears"], "done": {"power": 1}}}
        self.assertTrue(bus_planner._still_holds(site, job))
        self.assertFalse(bus_planner._still_holds({"base_plan": {**site["base_plan"], "done": {"bus-gears": 1}}}, job))
        self.assertFalse(bus_planner._still_holds({**site, "base_plan": {**site["base_plan"], "x": 9}}, job))
        self.assertFalse(bus_planner._still_holds({**site, "origin": {"x": 64, "y": 0}}, job))   # (moved)
        with patch.object(bus_planner, "extendable", return_value=("chemical-science-pack",)):
            self.assertFalse(bus_planner._still_holds(site, job))   # (extended for something new since)
