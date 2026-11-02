"""The user's pace targets as a signal (companion/pace.py): milestones reached, what a goal brings nearer, how due."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import pace  # noqa: E402


def site(minute=0.0, done=(), base=(), **extra):
    return {"tick": int(minute * 3600), "completed_purposes": list(done), "base": list(base), **extra}


def block(purpose, **machines):
    return {"purpose": purpose, "machines": machines}


class ReachedTests(unittest.TestCase):
    def test_milestones_read_off_the_base(self):
        s = site(done=["steam-power", "red-science-automation"],
                 base=[block("electric-iron-smelting", **{"electric-mining-drill": 8}),
                       block("coal-loop", **{"burner-mining-drill": 4})])
        self.assertEqual(pace.reached(s), {"steam power", "red science automated", "mostly electric mining"})

    def test_a_science_line_fed_by_hand_is_not_automated_yet(self):
        red = {"purpose": "red-science-automation", "machines": {"assembling-machine-1": 2},
               "recipes": ["automation-science-pack"],
               "hand_fed": {"automation-science-pack": ["iron-gear-wheel"]}}
        self.assertNotIn("red science automated", pace.reached(site(done=["red-science-automation"], base=[red])))
        red.pop("hand_fed")
        self.assertIn("red science automated", pace.reached(site(done=["red-science-automation"], base=[red])))

    def test_a_line_on_bridge_chests_presses_for_what_connects_it(self):
        from unittest import mock
        from companion import tier_path
        s = site(minute=40, done=["red-science-automation"])
        hand = {"automation-science-pack": ("iron-plate",)}
        with mock.patch.object(tier_path, "bridged", lambda site: dict(hand)), \
                mock.patch.object(tier_path, "pack_hand_fed", lambda site, pack: hand.get(pack, ())), \
                mock.patch.dict("companion.catalog_planner.PURPOSES", {"tie-in": {"connects": True}, "plain": {}}):
            state = pace.State.of(s)
            self.assertIn("red science automated", pace.direct("tie-in", s, {}, {}, state))
            self.assertNotIn("red science automated", pace.direct("plain", s, {}, {}, state))
            later = pace.State.of(site(minute=55, done=["red-science-automation"]))
            self.assertGreater(pace.due("tie-in", site(minute=55), state=later)[0],
                               pace.due("tie-in", s, state=state)[0])   # (the longer it stands, the harder)

    def test_burner_drills_keep_mining_from_being_mostly_electric(self):
        s = site(base=[block("electric-iron-smelting", **{"electric-mining-drill": 8}),
                       block("iron-smelting-expansion", **{"burner-mining-drill": 6})])
        self.assertNotIn("mostly electric mining", pace.reached(s))

    def test_a_bus_under_way_is_the_bus_begun_and_red_science_made(self):
        s = site(base_plan={"stages": [{"id": "power"}]})
        self.assertTrue({"main bus begun", "red science automated"} <= pace.reached(s))


class ServesTests(unittest.TestCase):
    def test_a_goal_serves_in_full_the_milestone_it_makes_the_thing_of(self):
        s = site(20, done=["steam-power", "copper-smelting-start", "research-lab"])
        self.assertEqual(pace.serves("red-science-automation", s)["red science automated"], 1.0)

    def test_what_a_milestones_goal_waits_on_serves_it_in_part(self):
        s = site(20, done=["steam-power", "copper-smelting-start"])
        self.assertEqual(pace.serves("research-lab", s).get("red science automated"), pace.PREREQ_SHARE)

    def test_a_milestone_reached_is_served_by_nothing(self):
        s = site(20, done=["steam-power", "copper-smelting-start", "research-lab", "red-science-automation"])
        self.assertNotIn("red science automated", pace.serves("red-science-automation", s))

    def test_electric_mining_is_served_by_the_share_of_missing_drills_a_goal_adds(self):
        s = site(20, done=["iron-smelting-expansion"], base=[block("iron-smelting-expansion", **{"burner-mining-drill": 10})])
        state = pace.State.of(s)
        few = pace.electric_share("electric-coal-supply", s, {"electric-mining-drill": 2}, state)
        many = pace.electric_share("electric-iron-smelting", s, {"electric-mining-drill": 12}, state,
                                   {"iron-plate": 3.75})
        self.assertGreater(many, few)
        # (electric iron smelting retires the burner pairs on iron once it covers them: then mostly electric)
        self.assertEqual(many, 1.0)
        # a hoarder keeps its burners: the same drills close less of the gap
        hoarder = {**s, "personality": {"traits": ["hoarder"]}}
        kept = pace.electric_share("electric-iron-smelting", hoarder, {"electric-mining-drill": 12},
                                   pace.State.of(hoarder), {"iron-plate": 3.75})
        self.assertLess(kept, many)

    def test_what_waits_on_a_goal_it_replaces_is_served_by_it(self):
        # (the bus waits on electric iron mining, which electric iron smelting stands for)
        s = site(80, done=["steam-power", "red-science-automation"])
        self.assertEqual(pace.serves("electric-iron-smelting", s).get("main bus begun"), pace.PREREQ_SHARE)


class DueTests(unittest.TestCase):
    def test_a_milestone_presses_from_lead_minutes_before_its_aim_and_more_past_it(self):
        aim = pace.BY_NAME["red science automated"].aim
        self.assertEqual(pace.pressing("red science automated", aim - pace.LEAD - 1), 0.0)
        self.assertAlmostEqual(pace.pressing("red science automated", aim), 1.0)
        self.assertAlmostEqual(pace.pressing("red science automated", aim + pace.LEAD), 2.0)
        self.assertEqual(pace.pressing("red science automated", aim + 100 * pace.LEAD), pace.DUE_CAP)

    def test_due_is_the_most_pressing_milestone_served_times_its_share(self):
        s = site(35, done=["steam-power", "copper-smelting-start"])
        value, which = pace.due("research-lab", s)
        self.assertEqual(which, "red science automated")
        self.assertAlmostEqual(value, pace.PREREQ_SHARE * pace.pressing("red science automated", 35))

    def test_nothing_is_due_early(self):
        self.assertEqual(pace.due("red-science-automation", site(1, done=["steam-power"]))[0], 0.0)


if __name__ == "__main__":
    unittest.main()
