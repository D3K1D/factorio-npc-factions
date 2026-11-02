"""What is made by hand over and over gets its machine between rounds (companion/plan_hold.py repeated,
DesignedStart.hand_made; W-23). normal-7b, NPC 4: under steady attack every goal was reinforce ("holding the plot's
edge"), each plan finished inside its own 6-minute hand budget and the next began at nought, so turrets and piercing
rounds were made by hand for hours while the defense supply never got a turn."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import catalog_planner as cp  # noqa: E402
from companion import defense_production as ds  # noqa: E402
from companion import plan_hold  # noqa: E402
from test_defense_production import pressed  # noqa: E402
from test_plan_hold import game  # noqa: E402

LONG = plan_hold.HOLD + 600


def site_tick():
    from test_defense_production import TICK
    return TICK - 600


def planner():
    p = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
    p.designer, p.lines = None, []
    p.log = p.lines.append
    return p


class PlannerTests(unittest.TestCase):
    def setUp(self):
        ds._PICKED.clear()

    def test_the_maker_goes_before_holding_the_edge_once_it_was_made_by_hand_long_enough(self):
        p = planner()
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]), \
                mock.patch.object(cp, "team_urgent", return_value="reinforce"):
            self.assertEqual(p.choose_purpose(pressed(6, 10, hand_made={"gun-turret": LONG})), ds.GOAL)
            self.assertEqual(p.choose_purpose(pressed(6, 10, hand_made={"gun-turret": 600})), "reinforce")
        self.assertTrue(any("made by hand for" in line and "between waves" in line for line in p.lines))

    def test_never_while_attacked_nor_with_a_breach_open(self):
        p = planner()
        site = pressed(6, 10, hand_made={"gun-turret": LONG}, under_attack=True)
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]), \
                mock.patch.object(cp, "team_urgent", return_value="reinforce"):
            self.assertEqual(p.choose_purpose(site), "reinforce")
        site = pressed(6, 10, hand_made={"gun-turret": LONG})
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]), \
                mock.patch.object(cp.team_area, "team_of", return_value=True), \
                mock.patch.object(cp, "perimeter_available", lambda g, s, **k: g == "breach-repair"), \
                mock.patch.object(cp, "team_urgent", return_value="breach-repair"):
            self.assertEqual(p.choose_purpose(site), "breach-repair")

    def test_hit_while_its_defense_row_goes_up_it_goes_back_to_the_row(self):
        # (death-world-waves-power: a wave while the magazine row was built set it aside for reinforce, and it never
        # stood: the row paused was no maker any more, so the edge was held by hand again)
        p = planner()
        site = pressed(6, 10, reconsider=True, paused=ds.GOAL)
        with mock.patch.object(cp, "team_urgent", return_value="reinforce"):
            with self.assertRaises(cp.Defer):
                p.choose_purpose(site)
            self.assertEqual(p.choose_purpose({**site, "under_attack": True}), "reinforce")   # (the fight on: defend)
            self.assertEqual(p.choose_purpose({**site, "paused": "coal-loop"}), "reinforce")  # (any other build)
        with mock.patch.object(cp.team_area, "team_of", return_value=True), \
                mock.patch.object(cp, "perimeter_available", lambda g, s, **k: g == "breach-repair"), \
                mock.patch.object(cp, "team_urgent", return_value="breach-repair"):
            self.assertEqual(p.choose_purpose(site), "breach-repair")                         # (a breach: mended first)

    def test_set_aside_for_a_defense_asked_for_now_it_goes_back_once_the_wave_is_over(self):
        # (the third run: its strategist's build(now) on home_attacked set the row aside for reinforce, no reconsider)
        p = planner()
        site = pressed(6, 10, paused=ds.GOAL, ask="now")
        with mock.patch.object(cp, "team_urgent", return_value="reinforce"):
            with self.assertRaises(cp.Defer):
                p.choose_purpose(site)
            self.assertEqual(p.choose_purpose({**site, "under_attack": True}), "reinforce")

    def test_also_when_the_defense_is_asked_for_by_name(self):
        # (Codex, W-23: a named ask skips choose_purpose)
        p = planner()
        site = pressed(6, 10, paused=ds.GOAL, ask="now", purpose="reinforce", request_id="1:12")
        with self.assertRaises(cp.Defer):
            p._plan_goal(site)
        from companion import goal_brain   # (hand defenses only: the row itself, asked for, is planned as ever)
        self.assertTrue(goal_brain.defense("reinforce"))
        self.assertFalse(goal_brain.defense(ds.GOAL))
        # (Codex, W-23: an ally's call and a breach are theirs to end; the rows themselves are not hand defense)
        from companion import perimeter
        ally = next(g for g, spec in cp.PURPOSES.items() if spec.get("ally_help"))
        self.assertTrue(plan_hold.own_hand_defense("reinforce"))
        for other in (ally, perimeter.BREACH, ds.GOAL, None):
            self.assertFalse(plan_hold.own_hand_defense(other))
        src = (ROOT / "companion" / "catalog_planner.py").read_text(encoding="utf-8")
        self.assertIn("if plan_hold.own_hand_defense(purpose):", src)

    def test_nor_is_a_second_maker_planned_beside_the_one_set_aside(self):
        # (Codex, W-23: a hand-back's maker, or the bricks walls wait on, chosen again while the first stands paused)
        p = planner()
        y = {"purpose": "reinforce", "items": ["gun-turret"], "tick": site_tick()}
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]), \
                mock.patch.object(cp, "team_urgent", return_value="reinforce"):
            with self.assertRaises(cp.Defer):
                p.choose_purpose(pressed(6, 10, reconsider=True, paused=ds.GOAL, yielded=y))
        with mock.patch.object(plan_hold, "makers", return_value=["brick-smelting"]), \
                mock.patch.object(cp, "team_urgent", return_value="reinforce"):
            with self.assertRaises(cp.Defer):
                p.choose_purpose(pressed(6, 10, reconsider=True, paused="brick-smelting",
                                         hand_made={"stone-wall": LONG}))

    def test_a_hand_defense_while_its_row_is_set_aside_hands_back_at_the_first_quiet_minute(self):
        # (the run after: build(now) set the row aside in a wave, and reinforce's doubled budget held the rest of it)
        plan = {"purpose": "reinforce", "entities": [{"id": "t", "prototype": "gun-turret"}]}
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]):
            self.assertEqual(plan_hold.hand_hold(pressed(6, 10, paused=ds.GOAL, yields={"reinforce": 3}), plan),
                             plan_hold.PAUSED_HOLD)
            self.assertEqual(plan_hold.hand_hold(pressed(6, 10, yields={"reinforce": 1}), plan), 2 * plan_hold.HOLD)
        from companion import perimeter
        ally = next(g for g, spec in cp.PURPOSES.items() if spec.get("ally_help"))
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]):
            for other in (ally, perimeter.BREACH):   # (Codex, W-23: never cut short)
                got = plan_hold.hand_hold(pressed(6, 10, paused=ds.GOAL), {**plan, "purpose": other})
                self.assertEqual(got, plan_hold.HOLD)
        src = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("math.max(600,", src)                                   # (the game's least budget)

    def test_only_defense_items_with_a_maker_count(self):
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]):
            self.assertIsNone(plan_hold.repeated(pressed(6, 10, hand_made={"iron-gear-wheel": LONG})))
        with mock.patch.object(plan_hold, "makers", return_value=[]):
            self.assertIsNone(plan_hold.repeated(pressed(6, 10, hand_made={"gun-turret": LONG})))
        with mock.patch.object(plan_hold, "makers", return_value=[ds.GOAL]):   # (several items add up)
            got = plan_hold.repeated(pressed(6, 10, hand_made={"gun-turret": LONG / 2, "stone-wall": LONG / 2,
                                                               "iron-gear-wheel": 99999, "x": "bad"}))
            self.assertEqual(got[0], ds.GOAL)
            self.assertEqual(set(got[1]), {"gun-turret", "stone-wall"})

    def test_the_supply_s_own_pick_makes_what_was_made_by_hand(self):
        # (Codex, W-23: the row chosen for turrets made by hand was the most short item's, ammunition)
        site = pressed(6, 10, hand_made={"gun-turret": LONG})
        self.assertEqual(plan_hold.focus(site), {"gun-turret"})
        got = ds.pick(site, available=lambda g: False)
        self.assertEqual(got.need.item, "gun-turret")
        self.assertIsNone(plan_hold.focus(pressed(6, 10, hand_made={"gun-turret": 600})))
        self.assertIsNone(plan_hold.focus(pressed(6, 10, hand_made={"gun-turret": LONG}, under_attack=True)))

    def test_a_hand_back_no_row_can_make_leaves_the_hand_made_items_their_turn(self):
        y = {"purpose": "steel-smelting", "items": ["iron-plate"], "tick": 0}
        site = pressed(6, 10, hand_made={"gun-turret": LONG}, yielded={**y, "tick": site_tick()})
        self.assertEqual(plan_hold.focuses(site), [{"iron-plate"}, {"gun-turret"}])
        self.assertEqual(ds.pick(site, available=lambda g: False).need.item, "gun-turret")

    def test_counts_start_over_only_for_what_the_plan_makes_by_machine(self):
        # (Codex, W-23: power, which the row only waits on, cleared them, and reinforce came again before the row)
        from companion import wall_block
        site = pressed(6, 10, hand_made={"gun-turret": LONG})
        row = {"purpose": ds.GOAL, "defense_production": {"item": "gun-turret"}}
        self.assertEqual(plan_hold.made_by(site, row), ["gun-turret"])
        self.assertEqual(plan_hold.made_by(site, {**row, "defense_production": {"item": "firearm-magazine"}}), [])
        self.assertEqual(plan_hold.made_by(site, {"purpose": "steam-power"}), [])
        walls = pressed(6, 10, hand_made={"stone-wall": LONG})
        self.assertEqual(plan_hold.made_by(walls, {"purpose": wall_block.PURPOSE}), ["stone-wall"])
        self.assertEqual(plan_hold.made_by(walls, {"purpose": wall_block.BRICKS}), [])
        src = (ROOT / "companion" / "catalog_planner.py").read_text(encoding="utf-8")
        self.assertIn('plan["hand_clear"] = clear', src)


class GameTests(unittest.TestCase):
    """designed_start.lua: hand work counted across plans."""

    def test_hand_work_carries_over_to_the_next_plan_and_it_hands_back_in_a_quiet_minute(self):
        lua = game()
        lua.execute("""
            game.tick = 100
            d.plan.hand_hold = 1000; d.plan.hand_items = DS.item_set({'gun-turret'})
            DS.hand_add(d, 'gun-turret', 700, npc)
            -- (the plan ends within its budget; the next of the same kind begins at nought)
            npc.design = {status='building', stage_index=1, purpose='reinforce',
                          plan={plan_id='p2', stages={}, order={}, entities={}, hand_hold=1000,
                                hand_items=DS.item_set({'gun-turret'})}}
            d = npc.design
            DS.hand_add(d, 'gun-turret', 400, npc)
            npc.alarm = {last=1000}""")
        self.assertEqual(lua.eval("d.hand_ticks"), 400)
        self.assertIsNone(lua.eval("DS.hand_back(npc, d, 1100)"))          # (attacked: it holds)
        self.assertTrue(lua.eval("DS.hand_back(npc, d, 1000 + DS.ATTACK_CALM + 1)"))   # (between waves: 1100 of it)
        self.assertEqual(lua.eval("npc.yielded.items[1]"), "gun-turret")
        self.assertEqual(lua.eval("npc.yielded.hand_ticks"), 1100)
        self.assertIsNone(lua.eval("npc.hand_made['gun-turret']"))         # (counts start over)

    def test_only_its_plan_s_items_count_toward_its_hand_back(self):
        lua = game()
        lua.execute("""
            game.tick = 100
            d.plan.hand_hold = 1000; d.plan.hand_items = DS.item_set({'gun-turret'})
            DS.hand_add(d, 'iron-gear-wheel', 5000, npc)""")
        self.assertIsNone(lua.eval("DS.hand_back(npc, d, 200)"))
        self.assertEqual(lua.eval("DS.hand_made(npc, 200)['iron-gear-wheel']"), 5000)

    def test_counts_are_forgotten_in_a_while_and_the_list_is_bounded(self):
        lua = game()
        lua.execute("game.tick = 100; DS.hand_add(d, 'gun-turret', 700, npc)")
        self.assertEqual(lua.eval("DS.hand_made(npc, 100 + DS.HAND_MADE_FOR)['gun-turret']"), 700)
        self.assertIsNone(lua.eval("DS.hand_made(npc, 101 + DS.HAND_MADE_FOR)['gun-turret']"))
        self.assertIsNone(lua.eval("npc.hand_made['gun-turret']"))
        lua.execute("for i = 1, 40 do DS.hand_add(d, 'item-' .. i, 10, npc) end; n = 0; "
                    "for _ in pairs(npc.hand_made) do n = n + 1 end")
        self.assertEqual(lua.eval("n"), lua.eval("DS.HAND_MADE_MAX"))

    def test_a_plan_that_makes_them_clears_their_counts_once_it_stands(self):
        lua = game()
        lua.execute("""
            game.tick = 100
            DS.hand_add(d, 'gun-turret', 700, npc); DS.hand_add(d, 'stone-wall', 300, npc)""")
        src = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("hand_clear=DesignedStart.item_set(payload.hand_clear)", src)   # (kept with the plan)
        self.assertIn("DesignedStart.hand_made_clear(npc,d.plan.hand_clear)", src)    # (cleared when it completes)
        self.assertIn("hand_made=try(function() return DesignedStart.hand_made(npc,game.tick) end)", src)
        lua.execute("DS.hand_made_clear(npc, DS.item_set({'gun-turret'}))")
        self.assertIsNone(lua.eval("npc.hand_made['gun-turret']"))
        self.assertEqual(lua.eval("npc.hand_made['stone-wall'].ticks"), 300)


if __name__ == "__main__":
    unittest.main()
