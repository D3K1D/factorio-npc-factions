"""One threat estimate (companion/threat.py), the hand work an emergency may take (companion/handcraft.py),
and the emergency builds bounded by them (companion/catalog_planner.py)."""
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

from companion import catalog_planner as cp  # noqa: E402
from companion import defense_brain, handcraft, perimeter, threat  # noqa: E402
from test_defense_brain import CORE, LAB, attack  # noqa: E402
from test_defense_plans_perimeter import site, world  # noqa: E402

RECIPES = [
    {"name": "iron-plate", "category": "smelting", "energy": 3.2,
     "ingredients": [{"name": "iron-ore", "amount": 1}], "products": [{"name": "iron-plate", "amount": 1}]},
    {"name": "copper-plate", "category": "smelting", "energy": 3.2,
     "ingredients": [{"name": "copper-ore", "amount": 1}], "products": [{"name": "copper-plate", "amount": 1}]},
    {"name": "stone-brick", "category": "smelting", "energy": 3.2,
     "ingredients": [{"name": "stone", "amount": 2}], "products": [{"name": "stone-brick", "amount": 1}]},
    {"name": "iron-gear-wheel", "category": "crafting", "energy": 0.5,
     "ingredients": [{"name": "iron-plate", "amount": 2}], "products": [{"name": "iron-gear-wheel", "amount": 1}]},
    {"name": "stone-wall", "category": "crafting", "energy": 0.5,
     "ingredients": [{"name": "stone-brick", "amount": 5}], "products": [{"name": "stone-wall", "amount": 1}]},
    {"name": "gun-turret", "category": "crafting", "energy": 8,
     "ingredients": [{"name": "iron-gear-wheel", "amount": 10}, {"name": "copper-plate", "amount": 10},
                     {"name": "iron-plate", "amount": 20}], "products": [{"name": "gun-turret", "amount": 1}]},
    {"name": "firearm-magazine", "category": "crafting", "energy": 1,
     "ingredients": [{"name": "iron-plate", "amount": 4}], "products": [{"name": "firearm-magazine", "amount": 1}]},
    {"name": "engine-unit", "category": "advanced-crafting", "energy": 10,
     "ingredients": [{"name": "iron-plate", "amount": 1}], "products": [{"name": "engine-unit", "amount": 1}]}]
TICK = 1_000_000


def hit(seconds_ago, units=20, first_ago=None):
    a = attack(-395, -401, TICK - seconds_ago * 60, {"small-biter": units}, came=(-395, -440))
    if first_ago is not None:
        a["first"] = TICK - first_ago * 60
    return a


def attacked(*hits, **extra):
    return site(**{"base": [CORE, LAB], "tick": TICK, "threats": [], "recipes": RECIPES,
                   "threat_board": {"evolution": 0, "attacks": list(hits)}, **extra})


def guard(x, y, ammo=10):
    return {"x": x, "y": y, "name": "gun-turret", "kind": "ammo-turret", "ammo": ammo, "health": 1.0}


class EstimateTests(unittest.TestCase):
    def test_an_attack_its_turrets_do_not_hold_is_a_threat_that_fades(self):
        self.assertEqual(threat.estimate(site(base=[CORE, LAB], tick=TICK)).level, 0.0)   # nothing yet: calm
        now = threat.estimate(attacked(hit(30)))
        self.assertGreaterEqual(now.level, threat.ALERT)
        self.assertTrue(now.urgent)
        self.assertGreater(now.gap, 0)
        later = threat.estimate(attacked(hit(1500)))
        self.assertLess(later.level, now.level)
        self.assertLess(later.level, threat.ALERT)                       # one wave long ago: calm again
        self.assertTrue(cp.threatened(attacked(hit(30))))
        self.assertFalse(cp.threatened(attacked(hit(1500))))

    def test_a_wave_its_turrets_hold_is_no_threat(self):
        guards = [guard(-395 + dx, -404) for dx in (-6, -2, 2, 6)]   # four loaded turrets on the side it came from
        held = threat.estimate(attacked(hit(30), guards=guards))
        self.assertEqual(held.gap, 0.0)
        self.assertLess(held.level, threat.ALERT)
        empty = threat.estimate(attacked(hit(30), guards=[guard(-395 + dx, -404, ammo=0) for dx in (-6, -2, 2, 6)]))
        self.assertGreaterEqual(empty.level, threat.ALERT)               # (no ammunition: they hold nothing)

    def test_waves_that_keep_coming_keep_it_threatened_between_them(self):
        # one wave 15 minutes ago: calm; the same, with waves every five minutes before it: still threatened
        alone = threat.estimate(attacked(hit(900)))
        often = threat.estimate(attacked(hit(900, first_ago=900), hit(1200, first_ago=1200), hit(1500, first_ago=1500),
                                         hit(1800, first_ago=1800)))
        self.assertLess(alone.level, threat.ALERT)
        self.assertGreater(often.confidence, alone.confidence)
        self.assertLess(often.spacing_s, alone.spacing_s)
        self.assertGreater(often.level, alone.level)

    def test_the_spacing_starts_from_the_assumption_and_learns_from_the_waves(self):
        self.assertEqual(threat.spacing([], TICK), (threat.PRIOR_SPACING_S, None, 0, 0.0))
        one, since, n, conf = threat.spacing([hit(120)], TICK)
        self.assertEqual((n, conf, since), (1, 0.5, 120))
        self.assertAlmostEqual(one, (120 + threat.PRIOR_SPACING_S) / 2)   # halfway to the assumption
        three = threat.spacing([hit(s, first_ago=s) for s in (100, 200, 300)], TICK)[0]
        self.assertAlmostEqual(three, (300 + threat.PRIOR_SPACING_S) / 4)

    def test_old_repairs_on_a_stretch_do_not_close_a_new_hole_there(self):
        # (D-23, Codex's review: three repairs from before a hole opened counted it mended)
        def at(accepted):
            return {"purpose": "breach-repair", "box": [-10, -10, 10, 10], "accepted": accepted}
        hole = {"x": 0, "y": 0, "layers": 4, "hole": True, "hole_tick": 500, "tick": 500}
        self.assertEqual(threat.open_breaches({"base": [at(100)] * 3, "breaches": [hole]}), 1)
        self.assertEqual(threat.open_breaches({"base": [at(600)] * 3, "breaches": [hole]}), 0)
        self.assertEqual(threat.open_breaches({"base": [], "breaches": [{**hole, "hole": None}]}), 0)   # (4 standing)

    def test_others_attacking_or_a_breach_threaten_it_without_a_board(self):
        self.assertTrue(cp.threatened(site(social={"recent_attacks": 1})))
        breached = site(breaches=[{"x": -380, "y": -573.5, "tick": 900, "lost": "stone-wall", "layers": 1}])
        t = threat.estimate(breached)
        self.assertGreaterEqual(t.level, threat.BREACH)
        self.assertTrue(t.urgent)

    def test_pollution_alone_is_pressure_for_the_defense_brain_not_a_threat(self):
        smog = attacked(pollution=[{"x": -392, "y": -396, "p": 6000}],
                        threats=[{"x": -380, "y": -700, "kind": "unit-spawner"}])
        self.assertGreater(defense_brain.assess(smog)[0].pressure, 0)
        self.assertEqual(threat.estimate(smog).level, 0.0)
        self.assertEqual(threat.hand_seconds(threat.estimate(smog)), 0.0)

    def test_every_attack_its_plot_took_is_counted_past_the_30_minutes_the_board_shows(self):
        self.assertEqual(threat.attacked(site(base=[CORE, LAB])), 0)
        self.assertEqual(threat.attacked(attacked(hit(30), hit(900))), 2)               # (an older game's board)
        long_ago = attacked(**{"threat_board": {"evolution": 0, "attacks": [], "ever": {"count": 3, "first": 5,
                                                                                         "last": 9}}})
        self.assertEqual(threat.attacked(long_ago), 3)
        self.assertLess(threat.estimate(long_ago).level, threat.ALERT)                 # (long ago: no threat now)

    def test_each_judgment_with_it_is_written_down(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            threat.record(attacked(hit(30)), threat.estimate(attacked(hit(30))), {"chose": "reinforce"})
            row = json.loads((Path(folder) / "decisions" / threat.LOG_NAME).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["chose"], "reinforce")
        self.assertGreater(row["level"], 0)


class HandcraftTests(unittest.TestCase):
    def test_hand_work_is_crafting_smelting_and_mining_less_what_it_has(self):
        s = attacked(hit(30))
        wall = handcraft.cost(s, {"stone-wall": 1})
        craft = 0.5 / handcraft.HAND_SPEED                                    # (its quick hands)
        self.assertAlmostEqual(wall, craft + 5 * 3.2 / handcraft.SMELT_PARALLEL + 10 * handcraft.MINE_S)
        self.assertAlmostEqual(handcraft.cost({**s, "inventory": {"stone-brick": 5}}, {"stone-wall": 1}), craft)
        player = {**s, "hand_speed": 1, "inventory": {"stone-brick": 5}}       # (the setting at a player's speed)
        self.assertAlmostEqual(handcraft.cost(player, {"stone-wall": 1}), 0.5)
        self.assertEqual(handcraft.cost({**s, "inventory": {"stone-wall": 3}}, {"stone-wall": 3}), 0.0)
        self.assertTrue(math.isinf(handcraft.cost(s, {"engine-unit": 1})))   # (only machines make it)
        stocked = {**s, "base": [CORE, LAB, {"purpose": "wall-automation", "box": [-380, -380, -370, -370],
                                            "stock": {"stone-wall": 2}}]}
        self.assertEqual(handcraft.cost(stocked, {"stone-wall": 2}), 0.0)  # (its chests count)

    def test_what_needs_steel_can_be_had_only_once_its_own_furnaces_make_steel(self):
        # (run 20, 2026-10-25: a wall row's loader, a mod's made with steel, held the row 11 minutes and more)
        steel = {"name": "steel-plate", "category": "smelting", "energy": 16,
                 "ingredients": [{"name": "iron-plate", "amount": 5}], "products": [{"name": "steel-plate", "amount": 1}]}
        loader = {"name": "loader", "category": "crafting", "energy": 1,
                  "ingredients": [{"name": "steel-plate", "amount": 5}, {"name": "iron-gear-wheel", "amount": 5}],
                  "products": [{"name": "loader", "amount": 1}]}
        s = attacked(recipes=RECIPES + [steel, loader])
        self.assertFalse(handcraft.obtainable(s, "loader"))
        self.assertTrue(handcraft.obtainable(s, "iron-gear-wheel"))
        smelting = {"purpose": "steel-smelting", "box": [-380, -380, -370, -370], "capacity": {"makes": {"steel-plate": 0.1}}}
        self.assertTrue(handcraft.obtainable({**s, "base": [CORE, LAB, smelting]}, "loader"))
        self.assertTrue(handcraft.obtainable({**s, "inventory": {"steel-plate": 5}}, "loader"))
        self.assertTrue(handcraft.obtainable(s, "some-modded-thing"))     # (nothing known of it: no telling)

    def test_the_most_it_can_make_in_time_grows_with_the_time_and_stays_within_it(self):
        s = attacked(hit(30), inventory={"iron-plate": 400, "copper-plate": 100})
        kit = {"gun-turret": 1, "firearm-magazine": 10}
        counts = [handcraft.most(s, kit, seconds) for seconds in (0, 30, 120, 600)]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts[0], 0)
        for seconds, n in zip((30, 120, 600), counts[1:]):
            self.assertLessEqual(handcraft.cost(s, {k: v * n for k, v in kit.items()}) + n * handcraft.PLACE_S, seconds)


class ReconsiderTests(unittest.TestCase):
    def test_hit_while_building_it_defends_when_urgent_else_goes_back_to_the_build(self):
        # (urgency was looked at only between goals: a coal build ran 60 minutes and more whatever hit the base)
        from companion import blueprints as bp
        from companion.catalog_planner import CatalogPlanner, Defer
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            planner.world = lambda s: world()
            hot = attacked(hit(10), purpose="choose", paused="coal-supply", reconsider=True)
            self.assertIn(planner.choose_purpose(hot), ("reinforce", "plot-turrets"))
            guards = [guard(-395 + dx, -404) for dx in (-6, -2, 2, 6)]
            held = attacked(hit(10), purpose="choose", paused="coal-supply", reconsider=True, guards=guards,
                            request_id="1:11")
            with self.assertRaises(Defer):                                # its turrets hold: back to the coal
                planner.choose_purpose(held)
            rows = [json.loads(line) for line in
                    (Path(folder) / "decisions" / threat.LOG_NAME).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["chose"] for r in rows][-1], "back")
        self.assertEqual(rows[-1]["reconsider"], "coal-supply")


class BoundedEmergencyTests(unittest.TestCase):
    def test_an_emergency_gets_hand_work_by_the_time_to_the_next_wave(self):
        calm = cp.emergency_kit(site(base=[CORE, LAB], tick=TICK, recipes=RECIPES))
        self.assertEqual((calm.seconds, calm.turrets, calm.walls), (0.0, 0, 0))
        hot = attacked(hit(10), hit(300, first_ago=300), hit(600, first_ago=600),
                       inventory={"iron-plate": 400, "copper-plate": 100, "stone": 200})
        kit = cp.emergency_kit(hot)
        t = threat.estimate(hot)
        self.assertAlmostEqual(kit.seconds, round(threat.hand_seconds(t), 1))
        self.assertLessEqual(kit.seconds, t.next_wave_s * threat.HAND_SHARE + 1e-6)
        self.assertGreater(kit.turrets, 0)
        # walls from stone are dear by hand: far fewer than a whole edge
        self.assertLess(kit.walls, 60)

    def test_an_emergency_wall_is_as_long_as_the_walls_it_has_and_can_make_in_time(self):
        hot = attacked(hit(10), hit(300, first_ago=300), hit(600, first_ago=600), inventory={"stone-wall": 30})
        self.assertEqual(cp.hand_walls(hot), 30 + cp.emergency_kit(hot).walls)
        plan = perimeter.plan_wall(hot, world(), limit=cp.hand_walls(hot))
        self.assertLessEqual(sum(1 for e in plan["entities"] if e["prototype"] == "stone-wall"), cp.hand_walls(hot))

    def test_emergency_turrets_are_bounded_but_never_fewer_than_a_pair(self):
        hot = attacked(hit(10))
        many = perimeter.plan_turrets(hot, world(), shield=False)
        few = perimeter.plan_turrets(hot, world(), shield=False, max_turrets=2)
        guns = lambda plan: sum(1 for e in plan["entities"] if e["prototype"] == "gun-turret")  # noqa: E731
        self.assertGreater(guns(many), guns(few))
        self.assertGreaterEqual(guns(few), 2)
        # a group's shield whole or not at all
        bare = perimeter.plan_turrets(hot, world(), shield=True, max_turrets=2, wall_limit=3)
        self.assertNotIn("stone-wall", {e["prototype"] for e in bare["entities"]})
        area = defense_brain.most_needed(hot)
        self.assertGreater(area.need, defense_brain.MIN_GROUP)
        pair = defense_brain.plan_reinforce(hot, world(), area, max_turrets=0)
        self.assertEqual(pair["brain"]["turrets"], defense_brain.MIN_GROUP)

    def test_reinforcements_spread_out_unless_the_biters_keep_hitting_there(self):
        # (the user, 2026-10-26: too many too close together; side by side only where the biters keep attacking)
        def along(plan):
            return sorted(e["position"]["x"] for e in plan["entities"] if e["prototype"] == "gun-turret")
        once = attacked(hit(10))
        spread = along(defense_brain.plan_reinforce(once, world(), defense_brain.most_needed(once)))
        self.assertGreaterEqual(len(spread), defense_brain.MIN_GROUP)
        self.assertTrue(all(b - a >= perimeter.spacing({"range": 18}) - 1 for a, b in zip(spread, spread[1:])), spread)
        again = attacked(hit(10), hit(300, first_ago=300))
        plan = defense_brain.plan_reinforce(again, world(), defense_brain.most_needed(again))
        self.assertTrue(plan["brain"]["hot"])
        side = along(plan)
        self.assertTrue(all(b - a <= 3.5 for a, b in zip(side, side[1:])), side)

    def test_reinforcements_go_only_where_they_reach_the_area(self):
        # (the review, 2026-10-26: spread past the area, a second plan's six covered nothing and it kept asking)
        big = attacked(hit(10, units=80))
        entities, guards = [], []
        for _ in range(4):
            s = attacked(hit(10, units=80), guards=list(guards))
            area = defense_brain.most_needed(s)
            if area is None:
                break
            try:
                plan = defense_brain.plan_reinforce(s, world(entities), area)
            except perimeter.Done:
                break
            for e in plan["entities"]:
                if e["prototype"] == "gun-turret":
                    x, y = e["position"]["x"], e["position"]["y"]
                    self.assertLessEqual(defense_brain._box_distance((x, y), area.box),
                                         defense_brain.turret_range(big, "gun-turret") + defense_brain.REACH_MARGIN)
                    entities.append({"n": "gun-turret", "t": "ammo-turret", "x": x, "y": y, "w": 2, "h": 2,
                                     "f": "npc-faction-1"})
                    guards.append(guard(x, y))
        self.assertTrue(guards)


if __name__ == "__main__":
    unittest.main()
