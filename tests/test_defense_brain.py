"""The defense brain (companion/defense_brain.py): where the base is pressed, what covers it, where turrets go."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import defense_brain as brain  # noqa: E402
from companion import team_area  # noqa: E402
from test_defense_plans_perimeter import site, world  # noqa: E402

# The factory core and, well apart, an outpost of drills (npc-1's plot: the north-west corner of the team area).
CORE = {"purpose": "iron-smelting-start", "box": [-400, -400, -390, -394], "buildings": 4,
        "machines": {"burner-mining-drill": 2, "stone-furnace": 2}}
LAB = {"purpose": "research-lab", "box": [-388, -398, -384, -394], "buildings": 2, "machines": {"lab": 1}}
OUTPOST = {"purpose": "coal-loop", "box": [-300, -300, -296, -296], "buildings": 4,
           "machines": {"burner-mining-drill": 4}}


def attack(x, y, tick, units, came=None):
    a = {"x": x, "y": y, "tick": tick, "units": units, "kind": "biters"}
    if came:
        a["from"] = {"x": came[0], "y": came[1]}
    return a


class AreaTests(unittest.TestCase):
    def test_buildings_near_each_other_are_one_area_and_defenses_are_not_areas(self):
        s = site(base=[CORE, LAB, OUTPOST, {"purpose": "plot-turrets", "box": [-420, -572, -340, -566]}])
        found = brain.areas(s)
        self.assertEqual(len(found), 2)
        core = next(a for a in found if "research-lab" in a.purposes)
        self.assertEqual((core.box, core.value), ((-400, -400, -384, -394), 16))   # (at stake: a lab most)

    def test_an_areas_pollution_is_in_the_chunks_its_machines_stand_in(self):
        # (chunks given at their centres; the area (-400, -400, -384, -394) ends on a chunk's edge at x -384)
        smog = [{"x": -400, "y": -400, "p": 300}, {"x": -368, "y": -400, "p": 900}]
        a = brain.assess(site(base=[CORE, LAB], tick=0, pollution=smog, threat_board={"evolution": 0, "attacks": []}))
        self.assertEqual(a[0].pollution, 300)   # its own chunk, not the next one it only meets at its edge

    def test_an_area_is_where_machines_stand_not_a_box_round_a_block_with_far_drills(self):
        # (run 52: one area spanned the coal loop, the bricks and the coal supply, by their boxes with belts out)
        bricks = {"purpose": "electric-brick-smelting", "box": [-402, -398, -250, -250], "buildings": 90,
                  "machines": {"stone-furnace": 4, "electric-mining-drill": 2},
                  "parts": [{"box": [-402, -390, -398, -388], "machines": {"stone-furnace": 4}},
                            {"box": [-256, -256, -250, -250], "machines": {"electric-mining-drill": 2}}]}
        belts = {"purpose": "coal-trunk", "box": [-390, -390, -300, -300], "buildings": 90, "machines": {}}
        found = brain.areas(site(base=[CORE, LAB, bricks, belts]))
        self.assertEqual(sorted(a.box for a in found), [(-402, -400, -384, -388), (-256, -256, -250, -250)])
        self.assertTrue(all("electric-brick-smelting" in a.purposes for a in found))


class PressureTests(unittest.TestCase):
    def test_a_wave_that_hit_it_presses_on_it_fading_with_age_from_the_side_it_came(self):
        hit = attack(-395, -401, 100000, {"small-biter": 6, "medium-biter": 2}, came=(-395, -440))
        s = site(base=[CORE, LAB], tick=100000, threats=[], threat_board={"evolution": 0, "attacks": [hit]})
        core = brain.assess(s)[0]
        self.assertEqual(core.attack, 6 + 2 * brain.SIZE["medium"])
        self.assertLess(core.toward[1], -0.9)                            # from the north, where it first hit
        later = brain.assess({**s, "tick": 100000 + brain.HALF_LIFE})[0]
        self.assertAlmostEqual(later.attack, core.attack / 2)
        self.assertGreater(core.need, 0)

    def test_its_pollution_draws_a_wave_and_evolved_biters_press_harder(self):
        smog = [{"x": -392, "y": -396, "p": 600}]
        soaking = {"x": -380, "y": -700, "kind": "unit-spawner", "pollution": 4}   # (its pollution reaches the nest)
        s = site(base=[CORE, LAB], tick=0, pollution=smog, threats=[soaking],
                 threat_board={"evolution": 0, "attacks": []})
        core = brain.assess(s)[0]
        self.assertEqual(core.drawn, 600 / brain.POLLUTION_PER_UNIT)
        self.assertLess(core.toward[1], -0.9)                             # toward the nest, north (y -700)
        evolved = brain.assess({**s, "threat_board": {"evolution": 0.5, "attacks": []}})[0]
        self.assertAlmostEqual(evolved.pressure, core.pressure * (1 + brain.EVOLUTION_SCALE * 0.5))
        calm = brain.assess({**s, "pollution": []})[0]
        self.assertEqual((calm.pressure, calm.need), (0, 0))              # nothing presses: nothing to build

    def test_its_pollution_draws_by_how_near_the_nests_are_and_most_once_it_reaches_one(self):
        # (the user, 2026-10-26: walls sooner the higher the pollution, by where the nests are; run 57: 272-395
        # pollution in its chunks, none reaching the nearest nests 334 tiles off, none known within 250)
        smog = [{"x": -392, "y": -396, "p": 600}]
        board = {"evolution": 0, "attacks": [], "nests": {"reached": [], "free": 250}}
        s = site(base=[CORE, LAB], tick=0, pollution=smog, home={"x": -392, "y": -397}, threat_board=board)
        box = brain.areas(s)[0].box
        out_to = max(math.hypot(x + 392, y + 397) for x in (box[0], box[2]) for y in (box[1], box[3]))
        self.assertAlmostEqual(brain.nest_pull(s, box), math.exp(-(250 - out_to) / brain.NEST_FAR))
        far = {"x": -392, "y": -1000, "kind": "unit-spawner"}                       # seen, but none reaches it
        self.assertAlmostEqual(brain.nest_pull({**s, "threats": [far]}, box), math.exp(-600 / brain.NEST_FAR))
        reached = {**board, "nests": {"reached": [{"x": -800, "y": -396, "pollution": 3}], "free": 250}}
        drawn = brain.assess({**s, "threat_board": reached})[0]
        self.assertEqual(drawn.drawn, 600 / brain.POLLUTION_PER_UNIT)                 # reaching a nest: in full
        self.assertLess(drawn.toward[0], -0.9)                                       # (and from there: the west)
        self.assertLess(brain.assess(s)[0].drawn, drawn.drawn)
        self.assertEqual(brain.nest_pull(site(base=[CORE, LAB]), box), brain.NO_NESTS_KNOWN)   # (nothing known)

    def test_loaded_turrets_in_reach_on_its_pressure_side_cover_it(self):
        hit = attack(-395, -401, 0, {"small-biter": 12}, came=(-395, -440))
        base = site(base=[CORE, LAB], tick=0, threat_board={"evolution": 0, "attacks": [hit]})
        bare = brain.assess(base)[0]
        north = [{"x": -394, "y": -405, "name": "gun-turret", "kind": "ammo-turret", "ammo": 20, "health": 1},
                 {"x": -390, "y": -405, "name": "gun-turret", "kind": "ammo-turret", "ammo": 20, "health": 1}]
        guarded = brain.assess({**base, "guards": north})[0]
        self.assertGreater(guarded.covered, 0)
        self.assertLess(guarded.need, bare.need)
        empty = [dict(g, ammo=0) for g in north]
        self.assertEqual(brain.assess({**base, "guards": empty})[0].covered, 0)    # no ammo: no cover
        south = [dict(g, y=-390) for g in north]
        self.assertLess(brain.assess({**base, "guards": south})[0].covered, guarded.covered)   # wrong side: less

    def test_the_hardest_pressed_part_comes_first_and_none_when_all_hold(self):
        hits = [attack(-298, -301, 0, {"small-biter": 20}, came=(-298, -330)),
                attack(-395, -401, 0, {"small-biter": 4}, came=(-395, -440))]
        s = site(base=[CORE, LAB, OUTPOST], tick=0, threat_board={"evolution": 0, "attacks": hits})
        self.assertIn("coal-loop", brain.most_needed(s).purposes)
        self.assertIsNone(brain.most_needed(site(base=[CORE, LAB], tick=0, threat_board={"attacks": []})))


class ComingTests(unittest.TestCase):
    """D-11: waves of 9, then 23 and 34, met by two turrets: defense sized to what comes next."""

    def test_the_next_wave_is_judged_from_how_the_waves_on_it_grew(self):
        self.assertEqual(brain.coming([]), 0.0)
        self.assertEqual(brain.coming([(100, 9)]), 0.0)                       # (one wave is no trend)
        self.assertEqual(brain.coming([(100, 9), (200, 12)]), 16.0)           # as it grew again
        self.assertEqual(brain.coming([(200, 12), (100, 4)]), 12 * brain.GROWTH_MAX)   # (at most GROWTH_MAX times)
        self.assertEqual(brain.coming([(100, 20), (200, 10)]), 10.0)          # smaller: no less than the last
        self.assertEqual(brain.coming([(0, 4), (brain.HALF_LIFE, 20)]), 0.0)    # (too far apart: no trend)
        self.assertEqual(brain.coming([(100, 9), (200, 12)], 200 + brain.HALF_LIFE), 0.0)   # (long over)

    def test_waves_that_keep_coming_press_harder_than_the_last_one_faded(self):
        late = 100000 + brain.HALF_LIFE // 2
        waves = [attack(-395, -401, 100000 - 36000, {"small-biter": 6}, came=(-395, -440)),
                 attack(-395, -401, 100000, {"small-biter": 12}, came=(-395, -440))]
        s = site(base=[CORE, LAB], tick=late, threat_board={"evolution": 0, "attacks": waves})
        core = brain.assess(s)[0]
        self.assertEqual(core.coming, 24.0)
        self.assertEqual(core.attack, 24.0)                                    # (not 12 faded)
        per = brain.TURRET_HOLDS * brain.ammo_factor(brain.best_ammo(s))
        self.assertEqual(core.need, min(brain.MAX_GROUP, math.ceil(24 / per)))
        lone = brain.assess({**s, "threat_board": {"evolution": 0, "attacks": waves[1:]}})[0]
        self.assertAlmostEqual(lone.attack, 12 * 0.5 ** 0.5)                   # (one wave: faded as before)
        self.assertIn("next wave 24", core.why)

    def test_a_stretch_of_ground_is_judged_as_any_area(self):
        hit = attack(-395, -401, 0, {"small-biter": 18}, came=(-395, -440))
        s = site(base=[CORE, LAB], tick=0, threat_board={"evolution": 0, "attacks": [hit]})
        per = brain.TURRET_HOLDS * brain.ammo_factor(brain.best_ammo(s))
        self.assertEqual(brain.lacking(s, (-398, -404, -392, -398)), math.ceil(18 / per))   # (what 18 small biters want)
        self.assertEqual(brain.lacking(s, (-200, -200, -190, -190)), 0)        # (nothing presses there)


class FrontTests(unittest.TestCase):
    """D-13: rehearsal 7b lost 17 burner drills and 5 furnaces in fields no turret covered: waves go for what pollutes
    nearest where they come from, not only where the last one hit."""

    def test_a_part_never_hit_but_nearest_the_waves_draws_the_next_wave_and_deeper_ones_less(self):
        field = {"purpose": "coal-loop", "box": [-400, -460, -392, -452], "buildings": 4,
                 "machines": {"burner-mining-drill": 4}}                      # 60 tiles north of the core
        deep = {"purpose": "research-lab", "box": [-400, -300, -394, -296], "buildings": 2, "machines": {"lab": 2}}
        hit = attack(-395, -401, 0, {"small-biter": 12}, came=(-395, -560))   # the core hit, from the north
        s = site(base=[CORE, LAB, field, deep], tick=0, threat_board={"evolution": 0, "attacks": [hit]})
        found = {a.purposes[0]: a for a in brain.assess(s)}
        self.assertEqual(brain.base_wave(s), 12.0)
        self.assertEqual(found["coal-loop"].front, 12.0)                      # nearest where they come from: in full
        self.assertGreater(found["coal-loop"].need, 0)                        # turrets, though never hit
        self.assertLess(found["research-lab"].front, found["iron-smelting-start"].front)   # deeper: less
        self.assertIn("drawing the next wave 12", found["coal-loop"].why)
        calm = brain.assess(site(base=[CORE, LAB, field, deep], tick=0, threat_board={"evolution": 0, "attacks": []}))
        self.assertTrue(all(a.front == 0 for a in calm))                       # (no wave lately: none drawn)

    def test_with_nothing_known_of_where_they_come_from_the_plots_outer_edge_is_the_front(self):
        field = {"purpose": "coal-loop", "box": [-500, -560, -492, -552], "buildings": 4,
                 "machines": {"burner-mining-drill": 4}}                      # by the plot's north (outer) edge
        hit = attack(-395, -401, 0, {"small-biter": 10})                      # (no "from")
        s = site(base=[CORE, LAB, field], tick=0, threats=[], threat_board={"evolution": 0, "attacks": [hit]})
        found = {a.purposes[0]: a for a in brain.assess(s)}
        self.assertEqual(found["coal-loop"].front, 10.0)
        self.assertLess(found["iron-smelting-start"].front, 10.0)


    def test_codex_cases_a_quiet_part_in_front_the_side_it_comes_from_and_no_threat_from_the_forecast(self):
        from companion import threat
        lab = {"purpose": "research-lab", "box": [-300, -560, -294, -556], "buildings": 2, "machines": {"lab": 2}}
        drills = {"purpose": "coal-loop", "box": [-300, -480, -292, -472], "buildings": 4,
                  "machines": {"burner-mining-drill": 4}}
        hit = attack(-395, -401, 0, {"small-biter": 12}, came=(-395, -600))
        s = site(base=[CORE, LAB, lab, drills], tick=0, threats=[], threat_board={"evolution": 0, "attacks": [hit]})
        found = {a.purposes[0]: a for a in brain.assess(s)}
        self.assertEqual(found["coal-loop"].front, 12.0)              # (what pollutes nearest them: in full)
        self.assertEqual(found["research-lab"].front, 0.0)            # (a lab ahead of it draws no wave)
        self.assertTrue(found["coal-loop"].toward[1] < 0)               # faces north, where they came from
        hit_cov = [{"x": -395 + dx, "y": -404, "name": "gun-turret", "kind": "ammo-turret", "ammo": 20, "health": 1}
                   for dx in (-6, -2, 2, 6)]
        covered = {**s, "guards": hit_cov}
        self.assertEqual(threat.estimate(covered).gap, 0.0)               # (the hit part holds: no threat now)
        self.assertIn("coal-loop", brain.most_needed(covered).purposes)   # then the drills get turrets, not the lab


    def test_a_teammates_waves_say_what_comes_before_its_own_plot_is_hit(self):
        # (D-15: waves hit the plots in turn, minute 9 on one, 15 on the next; the next one's drill field was covered
        # only once its own was hit)
        field = {"purpose": "coal-loop", "box": [-500, -560, -492, -552], "buildings": 4,
                 "machines": {"burner-mining-drill": 4}}
        mate = [{"tick": -1000, "units": {"small-biter": 9}, "count": 9}]
        s = site(base=[CORE, LAB, field], tick=0, threats=[], threat_board={"evolution": 0, "attacks": [], "team": mate})
        self.assertAlmostEqual(brain.base_wave(s), 9 * 0.5 ** (1000 / brain.HALF_LIFE))
        found = {a.purposes[0]: a for a in brain.assess(s)}
        self.assertGreater(found["coal-loop"].need, 0)                      # covered before its own first wave
        own = {**s, "threat_board": {"evolution": 0, "attacks": [attack(-395, -401, 0, {"small-biter": 4})],
                                     "team": mate}}
        self.assertAlmostEqual(brain.base_wave(own), 9 * 0.5 ** (1000 / brain.HALF_LIFE))   # (a bigger one there still)
        mates = {**s, "threat_board": {"evolution": 0, "attacks": [], "team": [
            {"tick": -100, "units": {"small-biter": 16}, "count": 16}, {"tick": -2000, "units": {"small-biter": 4}, "count": 4}]}}
        self.assertLess(brain.base_wave(mates), 16)                         # (waves on two plots: no growth read in them)


class HitNowTests(unittest.TestCase):
    """LIVE-7c-c, normal-7c: Dax's drill field was hit; its turrets went to the core beside it (more at stake), and the
    biters stood 3 minutes in the field, its rebuilds 170-205 s after the losses."""

    def test_the_part_hit_now_is_answered_first_however_much_more_the_rest_is_worth(self):
        field = {"purpose": "electric-iron-mining", "box": [-400, -360, -392, -352], "buildings": 6,
                 "machines": {"electric-mining-drill": 4}}
        hit = attack(-396, -356, 9900, {"small-biter": 5})                  # (in the field, a few seconds ago)
        s = site(base=[CORE, LAB, field], tick=10000, threat_board={"evolution": 0, "attacks": [hit]})
        chosen = brain.most_needed(s)
        self.assertIn("electric-iron-mining", chosen.purposes)
        plan = brain.plan_reinforce(s, world(), chosen)
        guns = [e for e in plan["entities"] if e["prototype"] == "gun-turret"]
        self.assertTrue(guns and all(abs(g["position"]["y"] - (-356)) < 30 for g in guns))   # (at the field)
        # (an hour on, nothing under way: the most needed again, the most at stake among equals)
        later = {**s, "tick": 10000 + 2 * brain.HIT_NOW}
        self.assertIsNotNone(brain.most_needed(later))


class PlanTests(unittest.TestCase):
    def test_turrets_go_on_the_pressure_side_as_many_as_it_lacks_within_its_plot(self):
        hit = attack(-395, -401, 0, {"small-biter": 18}, came=(-395, -440))
        s = site(base=[CORE, LAB], tick=0, threat_board={"evolution": 0, "attacks": [hit]})
        area = brain.most_needed(s)
        plan = brain.plan_reinforce(s, world(), area)
        guns = [e for e in plan["entities"] if e["prototype"] == "gun-turret"]
        self.assertEqual(len(guns), area.need)
        self.assertTrue(all(g["position"]["y"] < -400 for g in guns))     # north of the core, where they came
        self.assertEqual(team_area.misplaced(plan, s), [])
        self.assertEqual({m["slot"] for m in plan["stages"][0]["manual_supplies"]}, {"ammo"})
        self.assertNotIn("stone-wall", {e["prototype"] for e in plan["entities"]})
        shielded = brain.plan_reinforce(s, world(), area, shield=True)  # (an emergency: a short wall too)
        self.assertIn("stone-wall", {e["prototype"] for e in shielded["entities"]})

    def test_each_decision_is_written_down_with_what_it_saw(self):
        hit = attack(-395, -401, 0, {"small-biter": 18}, came=(-395, -440))
        s = site(base=[CORE, LAB], tick=0, threat_board={"evolution": 0.1, "attacks": [hit]})
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            brain.record(s, brain.most_needed(s), {"turrets": 4})
            row = json.loads((Path(folder) / "decisions" / brain.LOG_NAME).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["decision"]["turrets"], 4)
        self.assertEqual(len(row["areas"]), 1)
        self.assertIn("pressure", row["areas"][0])


class AgreeTests(unittest.TestCase):
    """D-16 (run normal-7b): reinforce chosen 53 times and declined 48, the planner finding every stretch of the side
    held while the brain still asked for more. One rule for both, and a declined goal passed over until things change."""

    def _drawn(self, **extra):
        # (an unhit core drawing a big wave of its teammates': pressure far past what spread turrets hold)
        mate = [{"tick": -100, "units": {"small-biter": 40}, "count": 40}]
        return site(**{"base": [CORE, LAB], "tick": 0, "npc_id": 1, "world_id": "w",
                       "threat_board": {"evolution": 0, "attacks": [], "team": mate}, **extra})

    @staticmethod
    def _stood(plan, guards):
        return guards + [{"x": e["position"]["x"], "y": e["position"]["y"], "name": "gun-turret",
                          "kind": "ammo-turret", "ammo": 20, "health": 1}
                         for e in plan["entities"] if e["prototype"] == "gun-turret"]

    def test_what_the_brain_asks_for_the_plan_places_until_the_spread_rule_is_met(self):
        guards, rounds = [], 0
        while True:
            s = self._drawn(guards=guards)
            area = brain.most_needed(s)
            if area is None:
                break
            plan = brain.plan_reinforce(s, world(), area)               # (never declined while it asks: no Done)
            self.assertEqual(sum(1 for e in plan["entities"] if e["prototype"] == "gun-turret"), area.need)
            guards = self._stood(plan, guards)
            rounds += 1
            self.assertLess(rounds, 5)
        self.assertGreaterEqual(rounds, 1)
        held = brain.assess(self._drawn(guards=guards))[0]
        self.assertGreater(held.pressure, held.covered)                   # (pressed past them, but not hit: spread)
        hits = [attack(-395, -401, -t, {"small-biter": 30}, came=(-395, -440)) for t in (100, 2000)]
        hot = self._drawn(guards=guards, threat_board={"evolution": 0, "attacks": hits})
        self.assertIsNotNone(brain.most_needed(hot))                      # (hit again and again: side by side now)
        brain.plan_reinforce(hot, world(), brain.most_needed(hot))

    def test_a_turret_deep_inside_behind_the_side_holds_no_stretch_of_it(self):
        # (Codex's review: one 15 tiles inside the core, away from the pressure, left the side's one slot taken)
        s = self._drawn()
        area = brain.assess(s)[0]
        gun = next(t for t in brain.arms_of(s) if t.get("kind") == "ammo")
        free = brain.stretches(s, area, gun)
        behind = {"x": -392, "y": -394 + 15, "name": "gun-turret", "kind": "ammo-turret", "ammo": 20, "health": 1}
        self.assertEqual(brain.stretches(self._drawn(guards=[behind]), area, gun), free)
        before = {**behind, "y": -400 - 6}                                  # (one out in front holds its stretch)
        self.assertLess(len(brain.stretches(self._drawn(guards=[before]), area, gun)), len(free))

    def test_a_declined_defense_goal_waits_until_something_changes(self):
        from companion.catalog_planner import brain_available
        brain._declined.clear()
        s = self._drawn()
        self.assertTrue(brain_available(s))
        brain.decline(s, "reinforce")
        self.assertFalse(brain_available(s))
        self.assertFalse(brain_available({**s, "tick": 30000}))           # (nothing changed: still passed over)
        hit = attack(-395, -401, 50, {"small-biter": 3})
        changes = [{**s, "threat_board": {**s["threat_board"], "attacks": [hit]}},           # a new attack
                   {**s, "tick": brain.DECLINE_MAX},                                         # long enough
                   {**s, "base": s["base"] + [OUTPOST]},                                     # something new built
                   {**s, "threat_board": {**s["threat_board"], "team": [
                       {"tick": -50, "units": {"small-biter": 60}, "count": 60}]}}]          # the wave grows
        for changed in changes:
            brain._declined.clear()   # (each a first decline: a second in a row holds twice as long, SO-4)
            brain.decline(s, "reinforce")
            self.assertFalse(brain.declined(changed, "reinforce"), changed)
        brain.decline(s, "reinforce")
        self.assertTrue(brain.declined({**s, "tick": brain.DECLINE_MAX}, "reinforce"))   # (declined again: longer)
        brain.decline(s, "reinforce")
        self.assertFalse(brain.declined({**s, "inventory": {"stone-wall": 50}}, "reinforce"))   # (walls made since)
        lost = self._drawn(guards=[{"x": -395, "y": -410, "name": "gun-turret", "kind": "ammo-turret", "ammo": 9}])
        brain.decline(lost, "reinforce")
        self.assertFalse(brain.declined(self._drawn(), "reinforce"))       # a turret lost
        self.assertFalse(brain.declined({**s, "npc_id": 2}, "reinforce"))  # (its own, not a teammate's)

    def test_a_goal_declined_for_its_ground_waits_through_new_attacks(self):
        # (D-20, the oil practice: breach-repair declined 12 times and the ammo loop failed 10 in 40 minutes, each
        # released by the next attack while oil waited)
        from companion import failures
        failures.MEMORY.clear()
        s = self._drawn()
        hit = attack(-395, -401, 50, {"small-biter": 3})
        attacked = {**s, "tick": 100, "threat_board": {**s["threat_board"], "attacks": [hit]}}
        brain.decline(s, "breach-repair", "the breach has its four layers and no room for more turrets")
        self.assertTrue(brain.declined(attacked, "breach-repair"))           # (an attack makes no room)
        self.assertFalse(brain.declined({**s, "base": s["base"] + [OUTPOST]}, "breach-repair"))   # (something built)
        brain.decline(s, "breach-repair", "the breach has its four layers and no room for more turrets")
        self.assertFalse(brain.declined({**s, "breaches": [{"x": 1, "y": 2, "layers": 2}]}, "breach-repair"))
        broke = {**s, "breaches": [{"x": 1, "y": 2, "layers": 2, "tick": 10}]}
        brain.decline(broke, "breach-repair", "the breach has its four layers and no room for more turrets")
        self.assertFalse(brain.declined({**s, "breaches": [{"x": 1, "y": 2, "layers": 2, "tick": 90}]},
                                        "breach-repair"))                  # (a wall lost there again, rows as many)
        # (the game's `ever` is {count, first, last}: read, never taken for a number; late-combat crashed on it)
        ever = {**s, "threat_board": {**s["threat_board"], "ever": {"count": 4, "first": 1, "last": 9}}}
        brain.decline(ever, "breach-repair", "no room")
        self.assertTrue(brain.declined(ever, "breach-repair"))
        brain.decline(s, "reinforce", "declined")                           # (no ground in it: an attack lifts it)
        self.assertFalse(brain.declined(attacked, "reinforce"))

    def test_the_planner_takes_the_next_goal_at_once_when_it_declines_a_defense_goal(self):
        from companion import blueprints as bp
        from companion import catalog_planner
        brain._declined.clear()
        s = self._drawn(recipes=[{"name": n} for n in ("gun-turret", "stone-wall", "firearm-magazine")])
        with tempfile.TemporaryDirectory() as folder:
            planner = catalog_planner.CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            planner.world = lambda _: world()
            calls = []

            def choose(site_):
                calls.append(1)
                return "reinforce" if len(calls) == 1 else catalog_planner.CONTINUE_PROJECTS
            with mock.patch.object(planner, "choose_purpose", side_effect=choose),                     mock.patch.object(brain, "plan_reinforce", side_effect=__import__("companion.perimeter").perimeter.Done("held")):
                with self.assertRaises(catalog_planner.Defer) as caught:
                    planner._plan(s)
        self.assertEqual(len(calls), 2)                                   # (chosen again at once, not a minute on)
        self.assertNotIn("held", caught.exception.reason)
        self.assertTrue(brain.declined(s, "reinforce"))


class PlannerTests(unittest.TestCase):
    def test_in_the_team_world_an_attack_brings_defense_where_it_is_pressed_and_the_opening_has_it(self):
        from companion.catalog_planner import available, team_opening, team_urgent
        recipes = [{"name": n} for n in ("gun-turret", "stone-wall", "firearm-magazine")]
        hit = attack(-395, -401, 99000, {"small-biter": 18}, came=(-395, -440))
        attacked = site(base=[CORE, LAB], tick=100000, recipes=recipes,
                        threat_board={"evolution": 0, "attacks": [hit]})
        self.assertTrue(available("reinforce", attacked))
        self.assertEqual(team_urgent(attacked), "reinforce")
        order = [p for p, _ in team_opening(attacked)]
        self.assertLess(order.index("iron-smelting-expansion"), order.index("reinforce"))
        self.assertNotIn("starter-turrets", order)                       # (its place is the brain's now)


if __name__ == "__main__":
    unittest.main()
