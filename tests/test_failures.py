"""One memory of goals that could not be planned (companion/failures.py): held until something that could mend them
changes, longer each time for the same reason, the strategist told after repeated failures (SO-4: an urgent power
plant failing "3 buildings outside its plot" was asked again every 20 seconds for 195 game minutes)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import blueprints as bp  # noqa: E402
from companion import catalog_planner as cp  # noqa: E402
from companion import failures  # noqa: E402
from companion import layout_planner  # noqa: E402

PLOT = "3 buildings outside its plot or near players' builds: power-1, power-2, power-3"


def site(tick=100_000, **extra):
    return {"world_id": "w", "npc_id": 1, "tick": tick, "recipes": [], "base": [], "inventory": {},
            "known_patches": [], "team": {"plot": [-576, -576, -192, -192]}, "home": {"x": 0, "y": 0}, **extra}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        failures.forget()

    def test_reasons_alike_but_for_their_counts_are_one(self):
        self.assertEqual(failures.reason_key(PLOT), failures.reason_key("34 buildings outside its plot or near "
                                                                         "players' builds: defense-1"))
        self.assertNotEqual(failures.reason_key(PLOT), failures.reason_key("no shore within reach"))

    def test_a_failed_goal_is_held_and_longer_each_time_for_the_same_reason(self):
        failures.note(site(), "power-plant", PLOT)
        self.assertIsNotNone(failures.held(site(100_000 + failures.FAIL_HOLD - 1), "power-plant"))
        self.assertIsNone(failures.held(site(100_000 + failures.FAIL_HOLD), "power-plant"))
        self.assertEqual(failures.note(site(130_000), "power-plant", PLOT), 2)
        self.assertIsNotNone(failures.held(site(130_000 + failures.FAIL_HOLD), "power-plant"))   # (twice as long)
        self.assertEqual(failures.note(site(200_000), "power-plant", "no shore within reach"), 1)   # (a new reason)
        self.assertIsNone(failures.held(site(), "steam-power"))                                 # (only its own goal)
        self.assertIsNone(failures.held(dict(site(), npc_id=2), "power-plant"))                # (only its own engineer)

    def test_it_comes_back_when_what_could_mend_it_changes(self):
        failures.note(site(), "power-plant", PLOT)                       # (ground: its plot, home, patches known)
        later = 100_600
        self.assertIsNotNone(failures.held(site(later, base=[{"purpose": "coal-loop"}]), "power-plant"))
        self.assertIsNotNone(failures.held(site(later, recipes=[{"name": "boiler"}]), "power-plant"))
        self.assertIsNone(failures.held(site(later, known_patches=[{"resource": "coal"}]), "power-plant"))
        self.assertIsNone(failures.held(site(later, team={"plot": [-576, -576, 0, 0]}), "power-plant"))
        failures.note(site(), "steel-smelting", "steel-plate is not unlocked")   # (research)
        self.assertIsNone(failures.held(site(later, recipes=[{"name": "steel-plate"}]), "steel-smelting"))
        failures.note(site(), "wall-automation", "not enough stone-brick in stock")   # (items)
        self.assertIsNotNone(failures.held(site(later), "wall-automation"))
        self.assertIsNone(failures.held(site(later, inventory={"stone-brick": 40}), "wall-automation"))
        failures.note(site(), "bus-base", "it went wrong")                   # (a reason of none: any of them)
        self.assertIsNone(failures.held(site(later, recipes=[{"name": "x"}]), "bus-base"))

    def test_failing_again_and_again_after_changes_only_its_hold_lets_it_go(self):
        # (W-29: each building put up let the ammo loop go, and it failed every request)
        reason = "the ammo loop cannot go under what stands at -465,-477"
        for k in range(failures.ESCALATE_AFTER):
            failures.note(site(100_000 + k), "ammo-line", reason)
        moved = site(100_600, home={"x": 9, "y": 0})   # (a change its kind of reason would count)
        self.assertIsNotNone(failures.held(moved, "ammo-line"))
        self.assertIsNone(failures.held(site(100_000 + 2 + failures.hold({"count": 3})), "ammo-line"))

    def test_the_defense_brain_reads_the_game_s_attack_count_as_it_sends_it(self):
        # (W-29: {count, first, last} from the game crashed every request once a defense goal had failed)
        from companion import defense_brain
        self.assertEqual(defense_brain._situation(site(threat_board={"ever": {"count": 4, "first": 1}}))["ever"], 4)
        self.assertEqual(defense_brain._situation(site(threat_board={"ever": 3}))["ever"], 3)

    def test_a_plan_made_or_a_save_loaded_from_before_forgets_it(self):
        failures.note(site(), "power-plant", PLOT)
        failures.planned(site(), "power-plant")
        self.assertIsNone(failures.held(site(100_100), "power-plant"))
        failures.note(site(), "power-plant", PLOT)
        self.assertIsNone(failures.held(site(1000), "power-plant"))

    def test_the_strategist_hears_every_few_failures_for_one_reason(self):
        self.assertEqual([n for n in range(1, 8) if failures.escalate(n)], [3, 6])


class PlannerTests(unittest.TestCase):
    def setUp(self):
        failures.forget()
        self.folder = tempfile.TemporaryDirectory()
        self.lines = []
        self.planner = cp.CatalogPlanner(bp.Catalog(self.folder.name), log=self.lines.append)

    def tearDown(self):
        self.folder.cleanup()

    def test_a_held_goal_is_passed_over_by_every_chooser_and_says_why(self):
        s = site(power_short=True, recipes=[{"name": n} for n in cp.PURPOSES["power-plant"]["requires_recipes"]])
        self.assertTrue(cp.available("power-plant", s))
        failures.note(s, "power-plant", PLOT)
        self.assertFalse(cp.available("power-plant", s))
        self.assertIn("outside its plot", cp.why_unavailable("power-plant", s))

    def test_a_failure_is_noted_under_its_goal_and_told_to_the_strategist_when_it_keeps_failing(self):
        for n in range(1, 4):
            failed = layout_planner.PlanError(PLOT)
            failed.purpose = "power-plant"
            self.planner._failed(site(100_000 * n), failed)
            self.assertEqual(getattr(failed, "escalate", None), 3 if n == 3 else None)
        self.assertTrue(any("its strategist is told" in line for line in self.lines))

    def test_the_plot_check_names_the_goal_it_failed(self):
        # (with no goal on it the game noted "choose", and the power plant was never held, SO-4)
        s = site(request_id="1:9", purpose="choose")
        plan = {"purpose": "power-plant", "entities": [], "stages": []}
        with mock.patch.object(self.planner, "_plan", return_value=plan), \
                mock.patch.object(self.planner, "_with_plan", side_effect=lambda x: x), \
                mock.patch.object(self.planner, "_upkeep"), \
                mock.patch.object(cp.team_area, "misplaced", return_value=["power-1", "power-2", "power-3"]):
            with self.assertRaises(layout_planner.PlanError) as caught:
                self.planner(s)
        self.assertEqual(caught.exception.purpose, "power-plant")
        self.assertIsNotNone(failures.held(s, "power-plant"))


class ClassTests(unittest.TestCase):
    """Every planner failure carries the goal it was for, so the one memory applies (SO-4)."""

    def setUp(self):
        failures.forget()
        self.folder = tempfile.TemporaryDirectory()
        self.planner = cp.CatalogPlanner(bp.Catalog(self.folder.name), log=lambda _: None)

    def tearDown(self):
        self.folder.cleanup()

    def ask(self, raised, tick):
        def plan(site_):
            self.planner._purpose_now = "power-plant"   # (chosen by the planner itself: the request says "choose")
            raise raised
        with mock.patch.object(self.planner, "_plan", side_effect=plan), \
                mock.patch.object(self.planner, "_with_plan", side_effect=lambda x: x), \
                mock.patch.object(self.planner, "_upkeep"):
            try:
                self.planner(site(tick, request_id="1:9", purpose="choose"))
            except Exception as exc:
                return exc

    def test_a_planner_failing_again_and_again_with_no_goal_named_is_held_under_its_goal(self):
        for n in range(3):
            exc = self.ask(layout_planner.PlanError("no room for the boilers"), 100_000 + n * 1200)
            self.assertEqual(exc.purpose, "power-plant")
        self.assertEqual(exc.escalate, 3)   # (asked past the hold here, as by name: the third tells its strategist)
        self.assertIsNotNone(failures.held(site(100_000 + 2400), "power-plant"))
        self.assertEqual(failures.MEMORY[("w", 1, "power-plant")]["count"], 3)

    def test_a_bug_in_a_planner_names_its_goal_too_but_is_no_planning_failure(self):
        exc = self.ask(KeyError("x"), 100_000)
        self.assertEqual(exc.purpose, "power-plant")
        self.assertIsNone(failures.held(site(), "power-plant"))

    def test_waiting_moving_or_scouting_are_not_failures(self):
        for raised in (cp.Defer("later"), cp.MoveSite("power-plant", 10, 0), cp.Scout("power-plant", "coal")):
            self.ask(raised, 100_000)
        self.assertIsNone(failures.held(site(), "power-plant"))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        failures.forget()

    def test_a_release_stands_though_the_change_passes(self):
        failures.note(site(), "power-plant", PLOT)
        self.assertIsNone(failures.held(site(100_600, team={"plot": [0, 0, 1, 1]}), "power-plant"))
        self.assertIsNone(failures.held(site(100_700), "power-plant"))       # (the plot back as it was: released)
        self.assertEqual(failures.note(site(101_000), "power-plant", PLOT), 2)   # (its count kept: longer next)

    def test_ground_is_its_patches_and_no_build_zones_not_just_how_many(self):
        failures.note(site(known_patches=[{"resource": "coal", "dx": 1, "dy": 1}]), "power-plant", PLOT)
        moved = site(100_600, known_patches=[{"resource": "coal", "dx": 90, "dy": 1}])   # (as scouting exports them)
        self.assertIsNone(failures.held(moved, "power-plant"))               # (another patch at the same count)
        failures.note(site(), "power-plant", PLOT)
        self.assertIsNone(failures.held(site(100_600, team={"plot": [-576, -576, -192, -192], "no_build": []}),
                                        "power-plant"))                     # (a player's build moved: no-build zones)

    def test_an_item_failure_waits_for_the_items_it_names(self):
        failures.note(site(), "wall-automation", "not enough stone-brick in stock")
        self.assertIsNotNone(failures.held(site(100_600, inventory={"iron-plate": 500}), "wall-automation"))
        self.assertIsNotNone(failures.held(site(100_600, inventory={"stone": 500}), "wall-automation"))   # (whole names)
        self.assertIsNone(failures.held(site(100_600, inventory={"stone-brick": 50}), "wall-automation"))

    def test_the_games_cooldown_gives_way_once_a_change_released_the_goal(self):
        failures.note(site(), "power-plant", PLOT)
        moved = site(100_600, team={"plot": [0, 0, 1, 1]}, failed_purposes=["power-plant"])
        self.assertTrue(failures.released(moved, "power-plant"))
        self.assertFalse(failures.released(site(100_600, failed_purposes=["steam-power"]), "steam-power"))
        failures.note(site(), "steam-power", "no shore within reach")
        self.assertFalse(failures.released(site(100_600), "steam-power"))                   # (held: the game's too)
        self.assertTrue(failures.released(site(100_000 + failures.FAIL_HOLD), "steam-power"))   # (its hold ran out)

    def test_a_goal_asked_for_by_name_is_held_too(self):
        failures.note(site(), "ally-defense", PLOT)
        self.assertIn("held until something changes", cp.why_unavailable("ally-defense", site(100_600)))


class CarryTests(unittest.TestCase):
    def test_the_bridge_and_the_game_carry_the_escalation_to_the_strategist(self):
        bridge = (ROOT / "companion" / "designer_bridge.py").read_text(encoding="utf-8")
        start = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn('failed["escalate"] = exc.escalate', bridge)
        self.assertIn('kind="goal_stuck"', start)
        # (told before the first plan's fallback too, not only with plans behind it)
        self.assertLess(start.index('kind="goal_stuck"'), start.index("if npc.design_history and #npc.design_history>0 then\n    note_failure"))
        self.assertIn("failures.forget()", bridge)


if __name__ == "__main__":
    unittest.main()
