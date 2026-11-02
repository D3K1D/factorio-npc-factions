"""The offense judgment (companion/offense_brain.py): when it is strong enough to take out nests, and which."""
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

from companion import offense_brain as offense  # noqa: E402

ARMED = {"armor": "heavy-armor", "guns": ["submachine-gun"], "magazines": 100, "piercing": 100, "turrets": 4,
         "health": 250, "max_health": 250}
NEAR = {"x": 100, "y": -300, "distance": 320, "spawners": 2, "worms": {"small": 2}, "pollution": 40}
FAR_CLEAN = {"x": -400, "y": 100, "distance": 410, "spawners": 1, "worms": {}, "pollution": 0}


def request(armed=ARMED, nests=(NEAR, FAR_CLEAN), evolution=0.1, last=None, reason="routine", agenda=()):
    threat = {"evolution": evolution}
    if last is not None:
        threat["last_attack"] = {"seconds_ago": last}
    return {"request_id": "1:s1", "npc_id": 1, "reason": reason,
            "situation": {"armed": armed, "nests": list(nests), "threat": threat, "agenda": list(agenda)}}


class OffenseTests(unittest.TestCase):
    def test_strong_enough_it_goes_for_the_nests_soaking_up_its_pollution_first(self):
        out = offense.intentions(request())
        self.assertEqual([(i["tool"], i["args"].get("target"), i["args"].get("x")) for i in out],
                         [("attack", "nests", 100)])                     # the polluting one, though farther in rank
        self.assertIn("soaking up", out[0]["reason"])

    def test_not_yet_strong_enough_it_arms_up_toward_the_one_that_matters_and_never_a_lesser_one_instead(self):
        # theirs 20.8 (two spawners, two small worms at evolution 0.1); 15 to 31 is "nearly": it arms up
        nearly = dict(ARMED, magazines=0, piercing=0, armor="heavy-armor", turrets=4)       # 12 + 5 = 17
        self.assertEqual(offense.choose(request(armed=nearly)["situation"])[0], "arm_up")
        hopeless = dict(ARMED, magazines=5, piercing=0, turrets=0, armor=None)              # 0.5
        self.assertIsNone(offense.choose(request(armed=hopeless)["situation"]))   # (not the clean far one either)

    def test_evolved_biters_and_worms_without_turrets_to_creep_hold_it_back(self):
        self.assertEqual(offense.choose(request(evolution=0.1)["situation"])[0], "attack")
        self.assertEqual(offense.choose(request(evolution=1.0)["situation"])[0], "arm_up")   # stronger now: not yet
        no_turrets = dict(ARMED, turrets=0)
        picked = offense.choose(request(armed=no_turrets, nests=(NEAR,))["situation"])
        self.assertNotEqual(picked[0] if picked else None, "attack")                   # worms: creep or wait

    def test_with_its_home_just_hit_or_nothing_to_shoot_with_it_stays(self):
        self.assertEqual(offense.intentions(request(last=60)), [])
        self.assertEqual(offense.intentions(request(armed=dict(ARMED, guns=[]))), [])

    def test_the_reflex_adds_it_when_the_decision_and_agenda_have_none(self):
        decided = [{"tool": "remember", "args": {"note": "x"}}]
        self.assertEqual([i["tool"] for i in offense.with_reflex(request(), decided)], ["remember", "attack"])
        armed_up = [{"tool": "arm_up", "args": {"focus": "all"}}]
        self.assertEqual(offense.with_reflex(request(), armed_up), armed_up)          # its own call stands
        on_it = request(agenda=[{"tool": "attack", "priority": "next"}])
        self.assertEqual(offense.with_reflex(on_it, decided), decided)                 # already going
        self.assertEqual(offense.with_reflex(request(reason="events"), decided), decided)   # (not mid-fight)

    def test_a_clean_cluster_far_off_is_not_worth_the_trip_but_one_that_may_spread_onto_its_ground_is(self):
        # (run 19, 2026-10-25: a nest 240 tiles off soaking up nothing was attacked four times running)
        clean_near = dict(FAR_CLEAN, x=-150, y=50, distance=150)
        self.assertIsNone(offense.choose(request(nests=(FAR_CLEAN,))["situation"]))
        self.assertEqual(offense.choose(request(nests=(clean_near,))["situation"])[0], "attack")

    def test_a_cluster_it_attacked_and_did_not_clear_waits_until_it_is_clearly_stronger(self):
        went = {"x": 100, "y": -300, "tick": 50000, "outcome": "left_standing", "before": 4, "after": 3,
                "armed": dict(ARMED)}
        again = request()
        again["situation"].update(attacks_made=[went], tick=60000)
        self.assertEqual(offense.choose(again["situation"])[0], "arm_up")          # as strong as then: not again yet
        stronger = request(armed=dict(ARMED, piercing=300, turrets=8))
        stronger["situation"].update(attacks_made=[went], tick=60000)
        self.assertEqual(offense.choose(stronger["situation"])[0], "attack")       # clearly stronger: again
        later = request()
        later["situation"].update(attacks_made=[went], tick=50000 + offense.RETRY_TICKS)
        self.assertEqual(offense.choose(later["situation"])[0], "attack")          # (a long while since)
        cleared = request()
        cleared["situation"].update(attacks_made=[dict(went, outcome="cleared")], tick=60000)
        self.assertEqual(offense.choose(cleared["situation"])[0], "attack")
        # whoever orders it: the strategist's own order waits too, arming up instead
        ordered = [{"tool": "attack", "priority": "next", "args": {"target": "nests", "x": 104, "y": -296}}]
        self.assertEqual([i["tool"] for i in offense.with_reflex(again, ordered)], ["arm_up"])
        self.assertEqual(offense.with_reflex(stronger, ordered), ordered)

    def test_the_games_odds_of_the_fight_there_come_before_the_weights(self):
        # (O-2: biters outrun it, so it goes only where it wins the fight standing; plain magazines barely hurt a
        # medium biter, which the weights cannot see: "100 magazines" scored the same against any nest)
        losing = dict(NEAR, odds={"ratio": 0.3, "defenders": 9, "reinforcements": 4})
        picked = offense.choose(request(nests=(losing,))["situation"])
        self.assertEqual(picked[0] if picked else None, None)                  # far off: not even arming toward it
        nearly = dict(NEAR, odds={"ratio": 0.8, "defenders": 9, "reinforcements": 4})
        self.assertEqual(offense.choose(request(nests=(nearly,))["situation"])[0], "arm_up")
        winning = dict(NEAR, odds={"ratio": 1.6, "defenders": 3, "reinforcements": 4})
        weak = dict(ARMED, magazines=20, piercing=0, turrets=2, armor=None)  # the weights alone: not enough
        self.assertEqual(offense.choose(request(armed=weak, nests=(winning,))["situation"])[0], "attack")
        out = offense.intentions(request(armed=weak, nests=(winning,)))
        self.assertIn("odds 1.6", out[0]["reason"])
        self.assertIn("3 biters there", offense.describe(request(nests=(winning,))["situation"]))

    def test_an_attack_is_weighed_on_what_it_can_afford_to_lose(self):
        # (O-5, the user's own play: "if I had more turrets and more ammo to spare I would have went for it. I would
        # lose turrets doing so, but it wouldnt be a big deal since my iron output was so high")
        odds = {"ratio": 1.4, "defenders": 9, "reinforcements": 4, "turrets": 6, "lost": 0.35, "rounds_needed": 600}
        nest = dict(NEAR, odds=odds)
        rich = {"turrets": 12, "spare_turrets": 6, "spare_magazines": 200, "iron_per_min": 300}
        poor = {"spare_turrets": 0, "spare_magazines": 0, "iron_per_min": 5}

        def decide(chest, n=nest):
            situation = request(nests=(n,))["situation"]
            situation["war_chest"] = chest
            return offense.choose(situation)

        self.assertEqual(decide(rich)[0], "attack")                            # turrets lost: no big deal
        self.assertEqual(decide(poor)[0], "arm_up")                            # same odds, can't replace: stock up
        cheap = dict(NEAR, odds=dict(odds, ratio=1.05, lost=0.6))
        self.assertEqual(decide(rich, cheap)[0], "attack")                     # rich: a closer fight it can pay for
        self.assertEqual(decide(poor, cheap)[0], "arm_up")
        situation = request(nests=(nest,))["situation"]
        situation["war_chest"] = rich
        out = offense.intentions({"request_id": "1:s1", "npc_id": 1, "reason": "routine", "situation": situation})
        self.assertEqual(out[0]["args"]["turrets"], 12)                        # its ring: 3 for each of its 4 (O-9)
        self.assertGreaterEqual(out[0]["args"]["magazines"], 120)                # and enough to fill it
        self.assertIn("plates", out[0]["reason"])
        # a nest with none left to fight back: a sure win costs only its rounds, even to a poor engineer (the review)
        bare = dict(NEAR, odds={"ratio": 99, "defenders": 0, "turrets": 8, "lost": 0, "rounds_needed": 50})
        self.assertEqual(offense.cost(offense.assess(request(nests=(bare,))["situation"])[0]), 20.0)
        self.assertEqual(decide(dict(poor, iron_per_min=10), bare)[0], "attack")

    def test_late_game_artillery_shells_a_nest_it_has_shells_for(self):
        # (O-7: shells from far out of their reach; the same odds hold for what they send at it)
        odds = {"ratio": 1.3, "defenders": 20, "reinforcements": 8, "turrets": 6, "lost": 0.4, "rounds_needed": 900}
        nest = dict(NEAR, odds=odds)                                           # 2 spawners, 2 worms: 8 shells
        chest = {"spare_turrets": 4, "spare_magazines": 100, "iron_per_min": 200,
                 "artillery": {"name": "artillery-turret", "range": 224, "shells": 20}}
        situation = request(nests=(nest,))["situation"]
        situation["war_chest"] = chest
        what, c = offense.choose(situation)
        self.assertEqual((what, c["method"], c["shells"]), ("attack", "artillery", 8))
        out = offense.intentions({"request_id": "1:s1", "npc_id": 1, "reason": "routine", "situation": situation})
        self.assertEqual(out[0]["args"]["method"], "artillery")
        poor_chest = dict(chest, spare_turrets=0, spare_magazines=0, iron_per_min=10)   # shells over its means
        situation["war_chest"] = poor_chest
        picked = offense.choose(situation)
        self.assertNotEqual(picked and picked[1].get("method"), "artillery")
        situation["war_chest"] = dict(chest, turrets=1)                         # no guard for it: no shelling
        picked = offense.choose(situation)
        self.assertNotEqual(picked and picked[1].get("method"), "artillery")
        situation["war_chest"] = chest
        chest["artillery"]["shells"] = 3                                        # too few shells: it creeps instead
        chest["turrets"] = 12                                                   # (with a full ring, O-9)
        what, c = offense.choose(situation)
        self.assertEqual(what, "attack")
        self.assertNotIn("method", c)

    def test_late_game_a_quiet_cluster_is_cleared_when_it_is_cheap_and_a_besieged_base_strikes_the_source(self):
        # (O-7, the late-combat stage: no attack in 40 minutes; clusters 243 tiles and more out were never "worth" it,
        # and a base under waves was never calm)
        odds = {"ratio": 2.0, "defenders": 6, "reinforcements": 4, "turrets": 6, "lost": 0.3, "rounds_needed": 300}
        quiet = dict(FAR_CLEAN, x=-300, y=100, distance=320, odds=odds)
        rich = {"turrets": 12, "spare_turrets": 6, "spare_magazines": 300, "iron_per_min": 400, "home_turrets": 6}
        situation = request(nests=(quiet,))["situation"]
        self.assertIsNone(offense.choose(situation))                          # no war chest: not worth the trip
        situation["war_chest"] = rich
        self.assertEqual(offense.choose(situation)[0], "attack")              # cheap for it: cleared
        situation["war_chest"] = dict(rich, spare_turrets=0, spare_magazines=0, iron_per_min=20)
        self.assertIsNone(offense.choose(situation))                          # not cheap for a poor one
        # under waves: only the nests they come from, and only with home holding without it
        besieged = request(nests=(quiet, NEAR), last=30)["situation"]
        besieged["war_chest"] = rich
        besieged["attack_sources"] = [{"x": 100, "y": -300, "count": 4}]
        near = dict(NEAR, odds=odds)
        besieged["nests"] = [quiet, near]
        what, c = offense.choose(besieged)
        self.assertEqual((what, c["x"]), ("attack", 100))                    # the source, not the quiet one
        besieged["war_chest"] = dict(rich, home_turrets=1)
        self.assertIsNone(offense.choose(besieged))                           # home would not hold: it stays

    def test_worms_that_lose_a_creep_do_not_stop_a_shelling_from_out_of_their_reach(self):
        # (O-7, the late-combat stage: big and behemoth worms made every creep a loss, 0.24-0.97, so the shells never
        # went either; from far out only what the nests send reaches it)
        odds = {"ratio": 0.4, "ratio_far": 2.5, "lost_far": 0.2, "defenders": 20, "turrets": 8, "lost": 1.0,
                "rounds_needed": 500}
        nest = dict(NEAR, odds=odds)
        chest = {"turrets": 8, "spare_turrets": 6, "spare_magazines": 300, "iron_per_min": 300, "home_turrets": 6,
                 "artillery": {"name": "artillery-turret", "range": 291, "shells": 30}}
        situation = request(nests=(nest,))["situation"]
        situation["war_chest"] = chest
        what, c = offense.choose(situation)
        self.assertEqual((what, c["method"]), ("attack", "artillery"))
        out = offense.intentions({"request_id": "1:s1", "npc_id": 1, "reason": "routine", "situation": situation})
        self.assertEqual(out[0]["args"]["shells"], 8)                         # it fetches the shells it needs
        nest["odds"] = dict(odds, ratio_far=0.5)                              # what comes out would beat it: no
        self.assertNotEqual((offense.choose(situation) or ("", {}))[1].get("method"), "artillery")

    def test_its_losses_are_priced_from_the_recipes_late_units_included(self):
        # (O-5 late: a uranium magazine and a modded turret cost more than the vanilla weights; the game prices them)
        odds = {"ratio": 1.4, "defenders": 9, "turrets": 6, "lost": 0.35, "rounds_needed": 600}
        nest = dict(NEAR, odds=odds)
        situation = request(nests=(nest,))["situation"]
        situation["war_chest"] = {"turrets": 8, "spare_turrets": 6, "spare_magazines": 200, "iron_per_min": 300}
        plain = offense.assess(situation)[0]["cost"]
        situation["war_chest"]["prices"] = {"turret": 120, "magazine": 30}
        priced = offense.assess(situation)[0]["cost"]
        self.assertAlmostEqual(priced, 2.6 * 120 + 60 * 30)
        self.assertGreater(priced, plain * 3)

    def test_the_shells_it_holds_are_part_of_what_it_can_spend(self):
        # (O-8: shells priced from their recipe made the shelling cost more than its means, which left them out)
        odds = {"ratio": 0.41, "ratio_far": 3.5, "lost_far": 0.1, "defenders": 30, "turrets": 6, "lost": 1.0,
                "rounds_needed": 500}
        nest = dict(NEAR, odds=odds)
        chest = {"turrets": 6, "spare_turrets": 2, "spare_magazines": 50, "iron_per_min": 69, "home_turrets": 6,
                 "prices": {"turret": 60, "magazine": 6, "shell": 80},
                 "artillery": {"name": "artillery-turret", "range": 291, "shells": 48}}
        situation = request(nests=(nest,))["situation"]
        situation["war_chest"] = chest
        what, c = offense.choose(situation)
        self.assertEqual((what, c["method"]), ("attack", "artillery"))

    def test_the_clear_targets_what_the_cloud_reaches_or_attacks_come_from_and_is_done_when_none_is_left(self):
        # (O-9, the user's clear: 12 groups in or near the cloud; done when none was left; 51 spawners, +0.086)
        odds = {"ratio": 2.0, "defenders": 4, "turrets": 6, "lost": 0.2, "rounds_needed": 200}
        in_cloud = dict(NEAR, cloud=30, odds=odds)
        by_cloud = dict(FAR_CLEAN, x=-300, y=0, distance=300, near_cloud=True, cloud=0, odds=odds)
        quiet = dict(FAR_CLEAN, x=-150, y=50, distance=150, cloud=0, odds=odds)             # near, but out of the cloud
        situation = request(nests=(in_cloud, by_cloud, quiet), evolution=0.2)["situation"]
        situation["war_chest"] = {"turrets": 12, "spare_turrets": 8, "spare_magazines": 300, "iron_per_min": 400,
                                  "home_turrets": 6}
        t = offense.targets(situation)
        self.assertEqual(sorted(c["x"] for c in t["targets"]), [-300, 100])
        self.assertFalse(t["done"])
        self.assertAlmostEqual(t["evolution_cost"], 3 * 0.002 * 0.8)                       # 2 + 1 spawners
        self.assertFalse(offense.worth(next(c for c in offense.assess(situation) if c["x"] == -150)))   # not the quiet one
        situation["nests"] = [quiet]
        self.assertTrue(offense.targets(situation)["done"])                                # none left in the cloud

    def test_one_campaign_goes_round_its_base_from_group_to_group_the_shorter_way(self):
        # (O-9, the user's clear: one loop round the base, home only to restock)
        odds = {"ratio": 2.0, "defenders": 4, "turrets": 4, "lost": 0.2, "rounds_needed": 200}
        first = dict(NEAR, x=100, y=0, distance=100, cloud=30, odds=odds)
        a = dict(NEAR, x=70, y=70, distance=99, cloud=10, odds=odds)
        b = dict(NEAR, x=-100, y=0, distance=100, near_cloud=True, cloud=0, odds=odds)
        quiet = dict(NEAR, x=0, y=-100, distance=100, cloud=0, odds=odds)                  # out of the cloud
        weak = dict(NEAR, x=0, y=100, distance=100, cloud=5, odds=dict(odds, ratio=0.5))    # a fight it would lose
        situation = request(nests=(first, a, b, quiet, weak))["situation"]
        situation["home"] = {"x": 0, "y": 0}
        situation["war_chest"] = {"turrets": 12, "spare_turrets": 8, "spare_magazines": 300, "iron_per_min": 400,
                                  "home_turrets": 6}
        assessed = {(c["x"], c["y"]): c for c in offense.assess(situation)}
        stops = offense.tour(situation, assessed[(100, 0)])
        self.assertEqual([(s["x"], s["y"]) for s in stops], [(70, 70), (-100, 0)])
        self.assertTrue(all(s["turrets"] >= 2 and s["magazines"] >= 20 for s in stops))
        situation["threat"]["last_attack"] = {"seconds_ago": 60}
        self.assertEqual(offense.tour(situation, assessed[(100, 0)]), [])                 # under waves: only sources
        del situation["threat"]["last_attack"]
        del situation["home"]
        self.assertEqual(offense.tour(situation, assessed[(100, 0)]), [])                 # no home known: no tour

    def test_a_thin_ring_waits_a_smaller_group_goes_first_or_it_makes_turrets(self):
        # (O-9, the user's clear: rings of 34-37 turrets on groups of 10-12 at no lower than 191 HP; thin rings of 2-3
        # were where it fell to 148)
        odds = {"ratio": 2.0, "defenders": 6, "turrets": 4, "lost": 0.3, "rounds_needed": 300}
        big = dict(NEAR, x=100, y=0, distance=100, spawners=8, worms={"medium": 4}, cloud=40, odds=odds)   # ring of 36
        small = dict(NEAR, x=-150, y=0, distance=150, spawners=2, worms={}, cloud=5, odds=odds)            # ring of 6
        situation = request(nests=(big, small))["situation"]
        situation["war_chest"] = {"turrets": 10, "spare_turrets": 6, "spare_magazines": 400, "iron_per_min": 400,
                                  "home_turrets": 6}
        self.assertEqual(offense.ring_for(offense.assess(situation)[0]), 36)
        what, c = offense.choose(situation)
        self.assertEqual((what, c["x"]), ("attack", -150))                  # the smaller group its ring covers
        situation["nests"] = [big]
        out = offense.intentions({"request_id": "1:s1", "npc_id": 1, "reason": "routine", "situation": situation})
        self.assertEqual((out[0]["tool"], out[0]["args"].get("turrets")), ("arm_up", 36))   # its turret maker first
        losing = dict(small, odds=dict(odds, ratio=0.2))
        situation["nests"] = [big, losing]
        self.assertEqual(offense.choose(situation)[0], "arm_up")         # a fight it would lose hides nothing (review)
        self.assertEqual(offense.choose(situation)[1]["x"], 100)
        situation["nests"] = [big, dict(small, odds=dict(odds, ratio=0.95))]  # nearly there: still the ring first
        self.assertEqual(offense.choose(situation)[1]["x"], 100)
        situation["nests"] = [big]
        situation["war_chest"]["turrets"] = 40
        out = offense.intentions({"request_id": "1:s1", "npc_id": 1, "reason": "routine", "situation": situation})
        self.assertEqual((out[0]["tool"], out[0]["args"]["turrets"]), ("attack", 36))
        self.assertGreaterEqual(out[0]["args"]["magazines"], 360)

    def test_a_cluster_its_attacks_came_from_matters_like_one_soaking_up_its_pollution(self):
        quiet_far = dict(FAR_CLEAN, odds={"ratio": 2.0, "defenders": 2, "turrets": 2, "lost": 0.2, "rounds_needed": 50})
        situation = request(nests=(quiet_far,))["situation"]
        self.assertIsNone(offense.choose(situation))                           # far, quiet: not worth it
        situation["attack_sources"] = [{"x": -390, "y": 110, "count": 3}]     # (the game's export)
        self.assertEqual(offense.choose(situation)[0], "attack")               # the waves come from there
        del situation["attack_sources"]
        situation["threat"]["attacks"] = [{"from": {"x": -390, "y": 110}, "count": 3}]
        self.assertEqual(offense.choose(situation)[0], "attack")               # (or the threat's own list)

    def test_each_decision_is_written_down_and_its_strategist_hears_the_comparison(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            offense.intentions(request())
            row = json.loads((Path(folder) / "decisions" / offense.LOG_NAME).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["chose"], "attack")
        self.assertEqual(len(row["clusters"]), 2)
        line = offense.describe(request()["situation"])
        self.assertIn("soaking up your pollution", line)
        from companion.strategist import prompt
        self.assertIn("Nest clusters near you", prompt(request()))


if __name__ == "__main__":
    unittest.main()
