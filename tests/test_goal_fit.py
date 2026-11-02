"""The goal weighing's weights fitted from runs (companion/goal_fit.py): the hindsight judgment, the fit, the check on
runs left out, and the weights file the weighing reads only when told to."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import goal_brain as gb  # noqa: E402
from companion import goal_fit  # noqa: E402

RED = "red science automated"


def row(goal, *, value=1.0, prior=0.0, due=0.0, cost=1.0, speedup=0.0):
    return {"goal": goal, "value": value, "unlocks": 0.0, "speedup": speedup, "hand_saved": 0.0, "hand_added": 0.0,
            "cost": cost, "prior": prior, "planned": False, "under_way": False, "odds": 1.0, "due": due,
            "asked": False, "guard": 0.0, "makes": {}}


def decision(run, minute, rows, served, request="1:2", tick=None):
    return goal_fit.Decision(run=run, request=request, minute=minute, logged=rows[0]["goal"], rows=rows,
                             served=served, tick=int(minute * 3600) + sum(map(ord, run)) if tick is None else tick)


def late_red(at=60.0, end=90.0):
    return goal_fit.Outcome(start=0.0, end=end, first={RED: at} if at is not None else {})


class JudgeTests(unittest.TestCase):
    def test_the_goal_serving_a_late_milestone_that_is_due_is_the_best_in_hindsight(self):
        d = decision("r", 22, [row("burner", prior=1.0), row("red-science-automation")],
                     {"red-science-automation": {RED: 1.0}})
        goal_fit.judge(d, late_red())
        self.assertEqual(d.label, {"red-science-automation"})
        self.assertEqual(d.today, "burner")
        self.assertEqual(d.target, {"red-science-automation": 1.0})

    def test_a_milestone_not_due_yet_or_on_time_says_nothing(self):
        early = decision("r", 2, [row("a"), row("red-science-automation")], {"red-science-automation": {RED: 1.0}})
        goal_fit.judge(early, late_red())
        self.assertEqual(early.target, {})
        on_time = decision("r", 22, [row("a"), row("red-science-automation")], {"red-science-automation": {RED: 1.0}})
        goal_fit.judge(on_time, late_red(at=24))
        self.assertEqual(on_time.target, {})

    def test_a_run_that_ended_before_a_due_milestones_aim_judges_nothing(self):
        d = decision("r", 22, [row("a"), row("red-science-automation")], {"red-science-automation": {RED: 1.0}})
        goal_fit.judge(d, late_red(at=None, end=24))
        self.assertEqual(d.target, {})

    def test_a_choice_with_a_defense_among_its_candidates_is_not_judged(self):
        d = decision("r", 22, [row("wall-automation"), row("red-science-automation")],
                     {"red-science-automation": {RED: 1.0}})
        goal_fit.judge(d, late_red())
        self.assertEqual(d.target, {})

    def test_a_quick_goal_for_the_same_lateness_is_worth_more(self):
        d = decision("r", 22, [row("slow", cost=10.0), row("quick", cost=2.0)], {"slow": {RED: 1.0}, "quick": {RED: 1.0}})
        goal_fit.judge(d, late_red())
        self.assertEqual(d.label, {"quick"})
        self.assertGreater(d.target["quick"], d.target["slow"])


class FitTests(unittest.TestCase):
    def runs(self):
        out = []
        for i in range(4):
            # today's prior picks the burner goal; the late red science (due) is what hindsight wants
            d = decision(f"run{i}", 22, [row("burner", prior=1.0), row("red-science-automation", due=1.2)],
                         {"red-science-automation": {RED: 1.0}})
            goal_fit.judge(d, late_red())
            out.append(d)
            # and a choice the outcome says nothing about, kept as today makes it
            calm = decision(f"run{i}", 5, [row("coal-loop", value=2.0), row("lab")], {}, request="1:1")
            goal_fit.judge(calm, late_red())
            out.append(calm)
        return out

    def test_the_pace_weight_is_fitted_to_what_hindsight_wants(self):
        w = goal_fit.fit(self.runs())
        self.assertGreater(w["due"], 0.5)
        self.assertEqual({k: w[k] for k in gb.default_weights() if k != "due"},
                         {k: v for k, v in gb.default_weights().items() if k != "due"})   # (the rest as today)
        for d in self.runs():
            self.assertEqual(goal_fit.top(d, w), "red-science-automation" if d.target else "coal-loop")

    def test_every_weight_may_be_fitted_and_each_keeps_its_sign(self):
        w = goal_fit.fit(self.runs(), names=goal_fit.MODELS["all"])
        self.assertTrue(all(v >= 0 for v in w.values()))
        self.assertGreater(w["cost_scale"], 0)

    def test_runs_left_out_in_turn_are_checked_on_weights_fitted_without_them(self):
        tally, per_run = goal_fit.held_out(self.runs())
        self.assertEqual(sorted(per_run), [f"run{i}" for i in range(4)])
        self.assertEqual((tally.judged, tally.right_today, tally.right_fitted), (4, 0, 4))
        self.assertEqual((tally.unjudged, tally.kept), (4, 4))
        self.assertEqual(len(tally.flips), 4)


class CheckTests(unittest.TestCase):
    def test_runs_resumed_from_one_checkpoint_are_one_family_and_their_shared_choices_count_once(self):
        shared = [decision(run, 60, [row("a"), row("b")], {}, request="1:28", tick=216000) for run in ("r52", "r53")]
        own = decision("r53", 70, [row("a"), row("b")], {}, request="1:30", tick=252000)
        other = decision("r50", 60, [row("a"), row("b")], {}, request="1:28", tick=216100)
        family = goal_fit.lineages([*shared, own, other])
        self.assertEqual(family["r52"], family["r53"])
        self.assertNotEqual(family["r50"], family["r52"])
        self.assertEqual(len(goal_fit.distinct([*shared, own, other])), 3)

    def test_the_simplex_finds_the_least_between_equal_ends(self):
        # (the first simplex's two ends straddled the least with equal values and it stopped at once)
        x = goal_fit.nelder_mead(lambda p: (p[0] - 0.25) ** 2, [0.0])
        self.assertAlmostEqual(x[0], 0.25, places=3)
        y = goal_fit.nelder_mead(lambda p: (p[0] - 1) ** 2 + 3 * (p[1] + 2) ** 2, [0.0, 0.0])
        self.assertAlmostEqual(y[0], 1.0, places=2)
        self.assertAlmostEqual(y[1], -2.0, places=2)


class RunTests(unittest.TestCase):
    def make_run(self, folder: Path) -> Path:
        run = folder / "long-9"
        (run / "sites").mkdir(parents=True)
        (run / "data" / "decisions").mkdir(parents=True)
        for request, minute, done in (("1:2", 22.0, []), ("1:3", 30.0, []), ("1:4", 61.0, ["red-science-automation"])):
            site = {"tick": int(minute * 3600), "request_id": request, "completed_purposes": done, "base": []}
            (run / "sites" / (request.replace(":", "-") + ".json")).write_text(json.dumps(site))
        line = {"tick": 22 * 3600, "request": "1:2", "chose": "burner", "candidates": [{"goal": "burner"}]}
        (run / "data" / "decisions" / gb.LOG_NAME).write_text(json.dumps(line) + "\n")
        return run

    def test_a_run_folder_is_read_weighed_again_and_judged_and_the_weights_written(self):
        rows = [row("burner", prior=1.0), row("red-science-automation", due=1.2)]
        served = {"burner": {}, "red-science-automation": {RED: 1.0}}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(goal_fit, "weigh_again", return_value=rows), \
                mock.patch.object(goal_fit.pace, "serves", side_effect=lambda g, *a, **k: served[g]):
            run = self.make_run(Path(folder))
            decisions = goal_fit.load_run(run, log=lambda _: None)
            self.assertEqual(len(decisions), 1)
            d = decisions[0]
            self.assertEqual((d.label, d.today), ({"red-science-automation"}, "burner"))
            self.assertAlmostEqual(d.measured, 8.0)   # (minutes to its next plan request)
            out = Path(folder) / "weights.json"
            result = goal_fit.report([run], out, log=lambda _: None)
            self.assertEqual(result["decisions"], 1)
            data = json.loads(out.read_text())
            self.assertEqual(data["schema"], gb.WEIGHTS_SCHEMA)
            # the weighing uses the file only when told to
            with mock.patch.dict(os.environ, {gb.WEIGHTS_ENV: ""}):
                self.assertEqual(gb.weights(), gb.default_weights())
            with mock.patch.dict(os.environ, {gb.WEIGHTS_ENV: str(out)}):
                self.assertEqual(gb.weights()["due"], data["weights"]["due"])


if __name__ == "__main__":
    unittest.main()
