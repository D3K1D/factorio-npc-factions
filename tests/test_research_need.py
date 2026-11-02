"""How much a research pick is needed (catalog_planner.research_need), sent with the answer for the team's queue to
sort the engineers' picks by (team_research.lua, R-9: logistics came at minute 33 behind green science and steel)."""
import unittest
from unittest import mock

from companion import catalog_planner as cp
from companion import designer_bridge


def options(*rows):
    return {"options": [dict(r) for r in rows]}


class ResearchNeedTests(unittest.TestCase):
    def test_what_the_next_goal_waits_on_is_a_what_a_later_one_waits_on_b_with_its_step(self):
        req = options({"name": "logistics", "unlocks": ["underground-belt", "splitter"]},
                      {"name": "steel-processing", "unlocks": ["steel-plate"]})
        with mock.patch.object(cp, "wanted_ranks", return_value={"splitter": 0, "steel-plate": 2}):
            self.assertEqual(cp.research_need("logistics", req, {}), {"class": "a", "step": 0})
            self.assertEqual(cp.research_need("steel-processing", req, {}), {"class": "b", "step": 2})

    def test_the_tier_and_rocket_path_are_c_the_rest_d(self):
        req = options({"name": "logistic-science-pack", "unlocks": ["logistic-science-pack"]},
                      {"name": "concrete", "unlocks": ["concrete"], "rocket_path": True},
                      {"name": "lamp", "unlocks": ["small-lamp"]})
        with mock.patch.object(cp, "wanted_ranks", return_value={}):
            self.assertEqual(cp.research_need("logistic-science-pack", req, {})["class"], "c")
            self.assertEqual(cp.research_need("concrete", req, {})["class"], "c")
            self.assertEqual(cp.research_need("lamp", req, {})["class"], "d")

    def test_arms_when_attacked_lately_are_a(self):
        req = {**options({"name": "gun-turret", "unlocks": ["gun-turret"]}), "team": True}
        with mock.patch.object(cp, "wanted_ranks", return_value={"gun-turret": 13}), \
                mock.patch.object(cp, "military_due", return_value=(True, "attacked lately")):
            self.assertEqual(cp.research_need("gun-turret", req, {})["class"], "a")   # (not b: a later goal's too)

    def test_under_attack_the_turrets_damage_and_fire_rate_are_arms_too(self):
        # (lesson 20: the user took physical-projectile-damage-2 2.8 minutes after the attacks grew hard)
        req = {**options({"name": "physical-projectile-damage-2", "unlocks": [], "military": True, "lab": 900},
                         {"name": "weapon-shooting-speed-2", "unlocks": [], "military": True, "lab": 1200},
                         {"name": "military-2", "unlocks": ["grenade"], "military": True, "lab": 300}),
               "team": True}
        self.assertEqual(cp.turret_bonuses(req["options"]), ["physical-projectile-damage-2", "weapon-shooting-speed-2"])
        with mock.patch.object(cp, "wanted_ranks", return_value={}), \
                mock.patch.object(cp, "military_due", return_value=(True, "attacked lately")):
            self.assertEqual(cp.research_need("weapon-shooting-speed-2", req, {})["class"], "a")
        with mock.patch.object(cp, "wanted_ranks", return_value={}), \
                mock.patch.object(cp, "military_due", return_value=(False, "calm")):
            self.assertNotEqual(cp.research_need("weapon-shooting-speed-2", req, {})["class"], "a")

    def test_a_failed_send_keeps_the_need_for_the_retry(self):
        bridge = designer_bridge.DesignerBridge.__new__(designer_bridge.DesignerBridge)
        bridge.handled = {}
        def down(line):
            raise OSError("line dropped")
        bridge.call = down
        cp.RESEARCH_NEEDS["7:r2"] = {"class": "a", "step": 0}
        with self.assertRaises(OSError):
            bridge._research("7:r2", "logistics")
        self.assertIn("7:r2", cp.RESEARCH_NEEDS)
        cp.RESEARCH_NEEDS.pop("7:r2")

    def test_the_answer_to_the_game_carries_the_need_once(self):
        bridge = designer_bridge.DesignerBridge.__new__(designer_bridge.DesignerBridge)
        bridge.handled = {}
        sent = []
        bridge.call = lambda line: sent.append(line) or {"accepted": True}
        cp.RESEARCH_NEEDS["7:r1"] = {"class": "a", "step": 0}
        bridge._research("7:r1", "logistics")
        self.assertIn('"need": {"class": "a", "step": 0}', sent[0])
        self.assertNotIn("7:r1", cp.RESEARCH_NEEDS)
        bridge._research("7:r1", "logistics")
        self.assertNotIn('"need"', sent[1])


class ResearchUseTests(unittest.TestCase):
    """R-10: red packs went on techs the engineers never use (normal-7e); the labs wait instead."""

    def setUp(self):
        patch = mock.patch.object(cp, "wanted_ranks", return_value={})
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_tech_serves_by_its_path_its_packs_its_leads_or_a_factory_bonus(self):
        used = lambda **o: cp.research_used({"name": "t", **o}, {})
        goal = sorted(cp.catalog_recipes())[0]
        self.assertTrue(used(rocket_path=True))
        self.assertTrue(used(military=True))
        self.assertTrue(used(unlocks=["logistic-science-pack"]))
        self.assertTrue(used(unlocks=[goal]))
        self.assertTrue(used(unlocks=["some-intermediate"], leads=["some-intermediate", goal]))   # (a prerequisite)
        self.assertTrue(used(bonuses=["mining-drill-productivity-bonus"]))
        self.assertFalse(used(unlocks=["mod-building-1"], leads=["mod-building-1", "mod-building-2"]))
        self.assertTrue(used(leads_armed=True))
        self.assertTrue(used(leads_cut=True))   # (more after it than the game lists: some may serve)   # (a prerequisite of arms: explosives before the tank)
        self.assertTrue(used(lead_bonuses=["laboratory-speed"]))
        self.assertTrue(used(unlocks=["landfill"]))   # (the engineer's own supplies: obstacles.lua fills water)
        self.assertTrue(cp.research_used({"name": cp.VEHICLE_FIRST[0]}, {}))   # (a list the chooser names)
        self.assertFalse(used(bonuses=["nothing"]))
        self.assertFalse(used(bonuses=["character-mining-speed"]))
        self.assertFalse(used())

    def test_nothing_that_serves_leaves_the_labs_idle_with_no_need_kept(self):
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        planner.log = lambda line: None
        planner.chooser = None
        planner._last_site = {}
        request = {"request_id": "7:r9", "options": [
            {"name": "mod-lights", "units": 10, "packs": ["automation-science-pack"], "unlocks": [],
             "bonuses": ["nothing"]},
            {"name": "mod-building", "units": 20, "packs": ["automation-science-pack"], "unlocks": ["mod-building-1"],
             "leads": ["mod-building-1"]}]}
        with mock.patch("companion.research_brain.choose", return_value=None),                 mock.patch("companion.research_brain.pick", return_value=None),                 mock.patch("companion.research_brain.survival_pick", return_value=None):
            self.assertEqual(planner.choose_research(request), cp.IDLE)
            self.assertNotIn("7:r9", cp.RESEARCH_NEEDS)
            self.assertEqual(cp.RESEARCH_UNUSED.pop("7:r9"), ["mod-lights", "mod-building"])
            request["options"].append({"name": "lab-speed", "units": 50, "packs": ["automation-science-pack"],
                                       "unlocks": [], "bonuses": ["laboratory-speed"]})
            self.assertEqual(planner.choose_research(request), "lab-speed")
        cp.RESEARCH_NEEDS.pop("7:r9", None)

    def test_the_idle_answer_goes_to_the_game_as_idle(self):
        bridge = designer_bridge.DesignerBridge.__new__(designer_bridge.DesignerBridge)
        bridge.handled = {}
        sent = []
        bridge.call = lambda line: sent.append(line) or {"accepted": True}
        cp.RESEARCH_UNUSED["7:r3"] = ["lamp"]
        bridge._research("7:r3", cp.IDLE)
        self.assertIn('"idle": true', sent[0])
        self.assertIn('"unused": ["lamp"]', sent[0])
        self.assertNotIn("7:r3", cp.RESEARCH_UNUSED)


if __name__ == "__main__":
    unittest.main()
