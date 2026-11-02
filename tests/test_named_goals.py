"""Goals its strategist asks for by name (companion/catalog_planner.py): "now" is built; "next" or "later" go through
the engineer's own judgment, survival first, and join the weighing (a bus asked for took the wall row's turn and no
wall stood by minute 110, run 52, 2026-10-26)."""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import blueprints as bp  # noqa: E402
from companion import catalog_planner as cp  # noqa: E402
from companion import goal_brain as gb  # noqa: E402


class AskTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.lines = []
        self.planner = cp.CatalogPlanner(bp.Catalog(self.folder.name), log=self.lines.append)
        gb.forget()

    def tearDown(self):
        self.folder.cleanup()

    def site(self, **extra):
        return {"request_id": "1:30", "npc_id": 1, "tick": 78 * 3600, "purpose": "bus-base", **extra}

    def calm(self, stack, **available):
        """Nothing urgent: no breach or attack, nothing on the urgent list (`available`: what may be built)."""
        stack.enter_context(mock.patch.object(cp, "team_urgent", return_value=None))
        stack.enter_context(mock.patch.object(cp, "threatened", return_value=False))
        stack.enter_context(mock.patch.object(cp, "available", side_effect=lambda p, s, designer=False:
                                              available.get(p, False)))

    def test_a_next_ask_goes_to_the_engineers_own_choice_with_what_was_asked(self):
        seen = {}

        def choose(site):
            seen.update(site)
            return cp.CONTINUE_PROJECTS
        with mock.patch.object(cp, "why_unavailable", return_value=None), \
                mock.patch.object(self.planner, "choose_purpose", side_effect=choose), \
                mock.patch.object(cp, "purpose_options", return_value=[]):
            with self.assertRaises(cp.Defer):
                self.planner._plan(self.site(ask="next"))
        self.assertEqual((seen["purpose"], seen["asked"]), ("choose", "bus-base"))
        self.assertTrue(any("asked for (next)" in line for line in self.lines))

    def test_a_now_ask_and_an_asked_defense_are_built_as_named(self):
        with mock.patch.object(cp, "why_unavailable", return_value=None), \
                mock.patch.object(self.planner, "choose_purpose") as choose, \
                mock.patch.object(self.planner, "_bus", return_value={"plan": "bus"}), \
                mock.patch.object(self.planner, "_perimeter", return_value={"plan": "wall"}):
            self.assertEqual(self.planner._plan(self.site(ask="now")), {"plan": "bus"})
            self.assertEqual(self.planner._plan(self.site(ask=None)), {"plan": "bus"})   # (the game's own names)
            self.assertEqual(self.planner._plan(self.site(purpose="plot-wall", ask="next")), {"plan": "wall"})
        choose.assert_not_called()

    def test_attacked_with_no_wall_standing_its_defenses_come_before_the_ask(self):
        steps = ["electric-stone-supply", "wall-automation", "bus-base"]
        with contextlib.ExitStack() as stack:
            self.calm(stack, **{"bus-base": True})
            stack.enter_context(mock.patch.object(cp, "opening_steps", return_value=steps))
            stack.enter_context(mock.patch.object(gb, "guard_of",
                                                  return_value=gb.Guard(value=1.0, hit_unwalled=True)))
            self.assertEqual(self.planner.choose_purpose(self.site(purpose="choose", asked="bus-base")),
                             "wall-automation")

    def test_calm_the_ask_is_weighed_among_the_openings_steps(self):
        steps = ["wall-automation", "electric-stone-supply"]
        got = {}

        def choose(site, candidates, opening=(), planned=None, asked=None, fuel=None):
            got.update(candidates=list(candidates), asked=asked)
            return "wall-automation", [{"goal": "wall-automation", "score": 2.5}]
        with contextlib.ExitStack() as stack:
            self.calm(stack, **{"bus-base": True})
            stack.enter_context(mock.patch.object(cp, "opening_steps", return_value=steps))
            stack.enter_context(mock.patch.object(self.planner, "supply_first", return_value=None))
            stack.enter_context(mock.patch.object(gb, "guard_of", return_value=gb.Guard(value=0.4)))
            stack.enter_context(mock.patch.object(gb, "choose", side_effect=choose))
            self.assertEqual(self.planner.choose_purpose(self.site(purpose="choose", asked="bus-base")),
                             "wall-automation")
        self.assertEqual(got, {"candidates": ["wall-automation", "electric-stone-supply", "bus-base"],
                               "asked": ["bus-base"]})

    def test_an_ask_passed_over_is_weighed_again_at_the_next_request(self):
        gb.remember_ask(self.site(), "bus-base")
        got = []

        def choose(site, candidates, opening=(), planned=None, asked=None, fuel=None):
            got.append(list(asked))
            return "electric-stone-supply", []
        with contextlib.ExitStack() as stack:
            self.calm(stack, **{"bus-base": True})
            stack.enter_context(mock.patch.object(cp, "opening_steps", return_value=["electric-stone-supply"]))
            stack.enter_context(mock.patch.object(self.planner, "supply_first", return_value=None))
            stack.enter_context(mock.patch.object(gb, "guard_of", return_value=gb.Guard()))
            stack.enter_context(mock.patch.object(gb, "choose", side_effect=choose))
            self.planner.choose_purpose(self.site(purpose="choose", tick=80 * 3600))
        self.assertEqual(got, [["bus-base"]])

    def test_with_the_weighing_failed_mid_opening_the_openings_order_not_the_ask(self):
        with contextlib.ExitStack() as stack:
            self.calm(stack, **{"bus-base": True, "electric-stone-supply": True})
            stack.enter_context(mock.patch.object(cp, "opening_steps", return_value=["electric-stone-supply"]))
            stack.enter_context(mock.patch.object(cp, "opening_goal", return_value="electric-stone-supply"))
            stack.enter_context(mock.patch.object(self.planner, "supply_first", return_value=None))
            stack.enter_context(mock.patch.object(gb, "guard_of", return_value=gb.Guard()))
            stack.enter_context(mock.patch.object(gb, "choose", side_effect=RuntimeError("no weighing")))
            self.assertEqual(self.planner.choose_purpose(self.site(purpose="choose", asked="bus-base")),
                             "electric-stone-supply")

    def test_calm_an_opening_defense_step_in_its_turn_still_comes_before_the_ask(self):
        # (the opening's own order is a survival gate for asks; urgency only pulls later defense steps forward)
        with contextlib.ExitStack() as stack:
            self.calm(stack, **{"bus-base": True})
            stack.enter_context(mock.patch.object(cp, "opening_steps", return_value=["plot-wall", "bus-base"]))
            stack.enter_context(mock.patch.object(gb, "guard_of", return_value=gb.Guard(value=0.2)))
            self.assertEqual(self.planner.choose_purpose(self.site(purpose="choose", asked="bus-base")), "plot-wall")

    def test_the_opening_over_the_ask_stands(self):
        with contextlib.ExitStack() as stack:
            self.calm(stack, **{"steel-smelting": True})
            stack.enter_context(mock.patch.object(cp, "opening_steps", return_value=[]))
            stack.enter_context(mock.patch.object(gb, "guard_of", return_value=gb.Guard()))
            self.assertEqual(self.planner.choose_purpose(self.site(purpose="choose", asked="steel-smelting")),
                             "steel-smelting")


class LuaCarryTests(unittest.TestCase):
    def test_the_game_tells_the_planner_how_soon_its_strategist_asked(self):
        agent = (ROOT / "mod" / "scripts" / "agent.lua").read_text(encoding="utf-8")
        start = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("npc.design.ask=it.priority", agent)
        self.assertIn('npc.design.ask="now"', agent)
        self.assertIn("ask=d.ask,", start)
        # (moved to another site, or off scouting for its ore: the planner's own pick then, not the ask)
        self.assertEqual(start.count("d.purpose=payload.purpose:sub(1,64); d.ask=nil"), 2)


if __name__ == "__main__":
    unittest.main()
