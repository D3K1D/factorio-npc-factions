"""A plan starved of its own items yields to what would make them (companion/plan_hold.py, DesignedStart.hand_back;
F-11): a budget of hand work only when something would make its items by machine, counted in the game, handed back
over it with no attack on (what stands stays, no failure noted), the maker chosen next, back to the plan once nothing
would make them, the budget doubled each time; never while attacked."""
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
from test_defense_production import RECIPES, TICK, pressed  # noqa: E402

TURRETS = {"purpose": "plot-turrets", "entities": [{"id": "t1", "prototype": "gun-turret"},
                                                   {"id": "t2", "prototype": "gun-turret"}]}


def only(*goals):
    """cp.available true for `goals` alone."""
    return lambda g, site, designer=False: g in goals


class MakerTests(unittest.TestCase):
    def setUp(self):
        ds._PICKED.clear()

    def test_power_first_for_its_defense_row_when_the_row_waits_on_it(self):
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES), \
                mock.patch.object(cp, "available", only("steam-power")):
            self.assertEqual(plan_hold.makers(pressed(6, 10), ["gun-turret"], "plot-turrets"), ["steam-power"])

    def test_the_defense_row_itself_once_it_can_be_built(self):
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES), \
                mock.patch.object(cp, "available", only(ds.GOAL)), mock.patch.object(cp, "has_built", return_value=True):
            self.assertEqual(plan_hold.makers(pressed(6, 10), ["gun-turret"], "plot-turrets"), [ds.GOAL])

    def test_nothing_when_no_machine_could_make_them(self):
        # (no assembler researched: power would not make turrets; and an item no draw asks for)
        with mock.patch.object(cp.ratios, "unlocked", return_value={"gun-turret"}), \
                mock.patch.object(cp, "available", only("steam-power")):
            self.assertEqual(plan_hold.makers(pressed(6, 10), ["gun-turret"], "plot-turrets"), [])
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES), \
                mock.patch.object(cp, "available", only("steam-power")):
            self.assertEqual(plan_hold.makers(pressed(6, 10), ["boiler"], "steam-power"), [])

    def test_never_itself(self):
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES), \
                mock.patch.object(cp, "available", only("steam-power")):
            self.assertEqual(plan_hold.makers(pressed(6, 10), ["gun-turret"], "steam-power"), [])


class BudgetTests(unittest.TestCase):
    def setUp(self):
        ds._PICKED.clear()

    def test_a_budget_only_when_something_would_make_its_items_doubled_per_hand_back(self):
        with mock.patch.object(plan_hold, "makers", return_value=["steam-power"]):
            self.assertEqual(plan_hold.hand_hold(pressed(6, 10), TURRETS), plan_hold.HOLD)
            again = pressed(6, 10, yields={"plot-turrets": 2})
            self.assertEqual(plan_hold.hand_hold(again, TURRETS), 4 * plan_hold.HOLD)
            many = pressed(6, 10, yields={"plot-turrets": 40})
            self.assertEqual(plan_hold.hand_hold(many, TURRETS), 2 ** plan_hold.MAX_DOUBLINGS * plan_hold.HOLD)
        with mock.patch.object(plan_hold, "makers", return_value=[]):
            self.assertIsNone(plan_hold.hand_hold(pressed(6, 10), TURRETS))


class AfterTests(unittest.TestCase):
    def y(self, ago=600, **extra):
        return pressed(6, 10, yielded={"purpose": "plot-turrets", "items": ["gun-turret"], "tick": TICK - ago,
                                       "hand_ticks": 6 * 3600}, **extra)

    def test_the_maker_next_then_back_to_the_plan_once_none(self):
        with mock.patch.object(plan_hold, "makers", return_value=["steam-power"]):
            goal, why = plan_hold.after(self.y())
            self.assertEqual(goal, "steam-power")
            self.assertIn("handed back after 6 min making gun-turret by hand", why)
        with mock.patch.object(plan_hold, "makers", return_value=[]):
            goal, why = plan_hold.after(self.y())
            self.assertIsNone(goal)
            self.assertIn("back to plot-turrets", why)

    def test_not_while_attacked_nor_long_after(self):
        with mock.patch.object(plan_hold, "makers", return_value=["steam-power"]):
            self.assertEqual(plan_hold.after(self.y(under_attack=True)), (None, ""))
            self.assertEqual(plan_hold.after(self.y(ago=plan_hold.YIELD_FOR + 60)), (None, ""))
            self.assertEqual(plan_hold.after(pressed(6, 10)), (None, ""))

    def test_the_maker_goes_before_holding_the_plot_s_edge_between_waves(self):
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        planner.designer, logs = None, []
        planner.log = logs.append
        with mock.patch.object(plan_hold, "makers", return_value=["steam-power"]), \
                mock.patch.object(cp, "team_urgent", return_value="reinforce"):
            self.assertEqual(planner.choose_purpose(self.y()), "steam-power")
            self.assertEqual(planner.choose_purpose(self.y(under_attack=True)), "reinforce")
        self.assertTrue(any("next goal steam-power (plot-turrets handed back" in line for line in logs))

    def test_the_plan_carries_its_budget_to_the_game(self):
        src = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("hand_hold=tonumber(payload.hand_hold)", src)


def game():
    import test_clearing
    lua = test_clearing.game()
    lua.execute("""
        npc.design = {status='building', stage_index=1, purpose='plot-turrets',
                      plan={plan_id='p', stages={}, order={}, entities={}, hand_hold=600}}
        npc.design_history = {{plan_id='old', stages={}, entities={}}}
        d = npc.design""")
    return lua


class GameTests(unittest.TestCase):
    """designed_start.lua: the hand-work clock and the hand-back."""

    def test_over_its_budget_it_hands_back_softly(self):
        lua = game()
        # (a mining trip for its turrets: its whole time, from the receipt; a craft queued in the background: none)
        lua.execute("""
            npc.design_jobs = npc.design_jobs or {}
            DS.hand_receipt(npc, {acquire='gun-turret'}, {kind='mine', status='complete', started_tick=1000, tick=1400})""")
        self.assertEqual(lua.eval("d.hand_ticks"), 400)
        self.assertIsNone(lua.eval("DS.hand_back(npc, d, 1400)"))           # (within its budget)
        # (a wait on its own hand furnace counts; time spent building does not)
        lua.execute("DS.hand_clock(npc, d, 1500, 'iron-plate'); DS.hand_clock(npc, d, 1600, nil); "
                    "DS.hand_clock(npc, d, 9000, nil)")
        self.assertEqual(lua.eval("d.hand_ticks"), 500)
        lua.execute("DS.hand_receipt(npc, {supply=true, item='firearm-magazine'}, {kind='craft', status='complete', started_tick=9000, tick=9200}); "
                    "DS.hand_receipt(npc, {supply=true, item='firearm-magazine'}, {kind='transfer', status='complete', started_tick=9200, tick=9900})")
        self.assertEqual(lua.eval("d.hand_ticks"), 700)                        # (its supply crafted, not carried)
        # (its upkeep's crafts meanwhile count too: magazines queued for its turrets, timed by their recipe)
        lua.execute("prototypes.recipe = prototypes.recipe or {}; prototypes.recipe['firearm-magazine'] = {energy=1}; "
                    "DS.hand_receipt(npc, {upkeep='turret_ammo'}, {kind='craft', status='complete', recipe='firearm-magazine', "
                    "queued=10, started_tick=9200, tick=9200})")
        self.assertEqual(lua.eval("d.hand_items['firearm-magazine']"), 200 + 600)
        self.assertTrue(lua.eval("DS.hand_back(npc, d, 9200)"))
        self.assertEqual(lua.eval("d.status"), "deferred")
        self.assertEqual(lua.eval("npc.yielded.purpose"), "plot-turrets")
        self.assertEqual(lua.eval("npc.yielded.items[1]"), "firearm-magazine")   # (most of its hand time)
        self.assertEqual(lua.eval("npc.yielded.items[2]"), "gun-turret")
        self.assertEqual(lua.eval("npc.yields['plot-turrets']"), 1)
        self.assertEqual(lua.eval("d.next_goal_tick"), 9200 + 120)            # (the next goal at once)
        self.assertIsNone(lua.eval("npc.design_failures and npc.design_failures['plot-turrets']"))   # (no failure)

    def test_never_while_attacked_and_never_without_a_budget(self):
        lua = game()
        lua.execute("DS.hand_add(d, 'gun-turret', 700); npc.alarm = {last=650}")
        self.assertIsNone(lua.eval("DS.hand_back(npc, d, 700)"))
        self.assertEqual(lua.eval("d.status"), "building")
        lua.execute("npc.alarm = {last=0}")
        self.assertTrue(lua.eval("DS.hand_back(npc, d, 9000)"))              # (the attack long over)
        lua = game()
        lua.execute("d.plan.hand_hold = nil; DS.hand_add(d, 'gun-turret', 3000)")
        self.assertIsNone(lua.eval("DS.hand_back(npc, d, 3000)"))

    def test_a_build_set_aside_stays_aside_so_the_maker_is_asked_for_first(self):
        lua = game()
        lua.execute("npc.paused_design = {status='building', purpose='bus-base', plan={}}; DS.hand_add(d, 'gun-turret', 700)")
        self.assertTrue(lua.eval("DS.hand_back(npc, d, 700)"))
        self.assertEqual(lua.eval("npc.design.purpose"), "plot-turrets")      # (not the build set aside)
        self.assertEqual(lua.eval("npc.paused_design.purpose"), "bus-base")   # (it resumes after the maker)

    def test_what_it_put_down_stays_when_it_hands_back(self):
        lua = game()
        lua.execute("""
            prototypes.entity['stone-furnace'] = {name='stone-furnace', type='furnace'}
            local furnace = {valid=true, type='furnace', name='stone-furnace'}
            d.plan.stages = {{id='s', status='building', entity_ids={'f'}}}
            d.plan.entities = {f={id='f', prototype='stone-furnace', item='stone-furnace', entity=furnace}}
            DS.hand_add(d, 'stone-furnace', 700)
            npc.design_salvage = {}""")
        self.assertTrue(lua.eval("DS.hand_back(npc, d, 700)"))
        self.assertEqual(lua.eval("#npc.design_salvage"), 0)
        self.assertTrue(lua.eval("npc.design_history[#npc.design_history].partial"))


class RoundOneTests(unittest.TestCase):
    """Codex's F-11 review: walls to the wall machinery, a plan's supplies counted, a breach mended first."""

    def setUp(self):
        ds._PICKED.clear()

    def test_walls_yield_to_the_wall_machinery_not_an_ammunition_row(self):
        from companion import wall_block
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES), \
                mock.patch.object(cp, "available", only(wall_block.BRICKS, ds.GOAL)):
            self.assertEqual(plan_hold.makers(pressed(6, 10), ["stone-wall"], "plot-wall"), [wall_block.BRICKS])

    def test_a_plan_s_hand_supplies_count_as_its_items(self):
        plan = {**TURRETS, "stages": [{"manual_supplies": [{"entity_id": "t1", "item": "firearm-magazine"}]}]}
        self.assertEqual(plan_hold.items_of(plan), ["gun-turret", "firearm-magazine"])

    def test_an_open_breach_is_mended_before_the_maker(self):
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        planner.designer, planner.log = None, lambda line: None
        site = pressed(6, 10, yielded={"purpose": "plot-turrets", "items": ["gun-turret"], "tick": TICK - 600})
        with mock.patch.object(plan_hold, "makers", return_value=["steam-power"]), \
                mock.patch.object(cp.team_area, "team_of", return_value=True), \
                mock.patch.object(cp, "perimeter_available", lambda p, s, **k: p == "breach-repair"), \
                mock.patch.object(cp, "team_urgent", return_value="breach-repair"):
            self.assertEqual(planner.choose_purpose(site), "breach-repair")


class RoundTwoTests(unittest.TestCase):
    """Codex's second F-11 review: waits on its crafting queue count, failed jobs do not, and the item a hand-back
    was making is carried through to the row (whatever the supply's own pick is)."""

    def setUp(self):
        ds._PICKED.clear()

    def test_a_wait_on_its_crafts_counts_and_a_failed_job_does_not(self):
        lua = game()
        lua.execute("""
            DS.hand_clock(npc, d, 0, 'iron-plate'); DS.hand_clock(npc, d, 300, nil)
            DS.hand_receipt(npc, {acquire='gun-turret'}, {kind='mine', status='failed', started_tick=300, tick=36300})""")
        self.assertEqual(lua.eval("d.hand_ticks"), 300)
        self.assertTrue(lua.eval("DS.hand_wait('crafting:firearm-magazine')"))
        self.assertFalse(lua.eval("DS.hand_wait('waiting_for:steel-plate')"))

    def test_a_turret_plan_gets_a_budget_though_the_supply_s_own_pick_is_walls(self):
        from companion import wall_block
        walls = ds.Pick(wall_block.BRICKS, ds.Need("stone-wall", 1.0, 0.0, "x"))
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES),                 mock.patch.object(ds, "cached_pick", return_value=walls),                 mock.patch.object(cp, "has_built", return_value=True):
            site = pressed(6, 10)
            self.assertTrue(cp.defense_production_ready(site))
            self.assertIn(ds.GOAL, plan_hold.makers(site, ["gun-turret"], "plot-turrets"))

    def test_the_row_after_a_hand_back_makes_what_it_was_making(self):
        y = {"purpose": "plot-turrets", "items": ["gun-turret"], "tick": TICK - 600}
        got = ds.pick(pressed(6, 10, yielded=y), available=lambda g: False)
        self.assertEqual(got.need.item, "gun-turret")
        other = ds.pick(pressed(6, 10, yielded={**y, "items": ["no-such-item"]}), available=lambda g: False)
        self.assertIsNotNone(other)                                           # (nothing of it short: the usual pick)


class RoundThreeTests(unittest.TestCase):
    def setUp(self):
        ds._PICKED.clear()

    def test_walls_and_ammunition_handed_back_the_maker_is_the_supply_s_own_pick(self):
        from companion import wall_block
        y = {"purpose": "outpost-defense", "items": ["stone-wall", "firearm-magazine"], "tick": TICK - 600}
        site = pressed(6, 10, yielded=y)
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES),                 mock.patch.object(cp, "available", only(wall_block.BRICKS)),                 mock.patch.object(cp, "has_built", return_value=True):
            goal, _ = plan_hold.after(site)
            focused = ds.pick(site, lambda g: bool(plan_hold._with_prerequisites(g, site, False)))
            self.assertEqual(goal, focused.goal)


class UpkeepTests(unittest.TestCase):
    """Run 61 (the proving ground, 2026-10-27): its upkeep's crafts, not the plan's own, took its hour."""

    def test_what_it_makes_by_hand_lately_counts_with_the_plan_s_items(self):
        site = pressed(6, 10, time_use={"shares": {"upkeep:craft:firearm-magazine": 0.4, "plan:mine:iron-ore": 0.1,
                                                   "supply:transfer:coal": 0.3, "upkeep:craft:pipe": 0.01}})
        self.assertEqual(plan_hold.hand_items(site), ["firearm-magazine", "iron-ore"])
        seen = []
        with mock.patch.object(plan_hold, "makers", lambda s, items, p, designer=False: seen.extend(items) or ["x"]):
            plan_hold.hand_hold(site, TURRETS)
        self.assertEqual(seen, ["gun-turret", "firearm-magazine", "iron-ore"])


class RoundFiveTests(unittest.TestCase):
    """Codex's fifth F-11 review: only what a machine could make is charged, and a built stage finishes."""

    def test_only_the_items_its_planner_named_are_charged(self):
        lua = game()
        lua.execute("d.plan.hand_items = DS.item_set({'firearm-magazine'}); DS.hand_add(d, 'stone-furnace', 5000); "
                    "DS.hand_add(d, 'firearm-magazine', 300)")
        self.assertEqual(lua.eval("d.hand_ticks"), 300)

    def test_ore_mined_for_its_magazines_is_charged_to_them(self):
        lua = game()
        lua.execute("d.plan.hand_items = DS.item_set({'firearm-magazine'}); "
                    "DS.hand_receipt(npc, {upkeep='turret_ammo', item='firearm-magazine'}, "
                    "{kind='mine', status='complete', item='iron-ore', started_tick=0, tick=900})")
        self.assertEqual(lua.eval("d.hand_items['firearm-magazine']"), 900)
        src = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("target=f.turret.unit_number,item=item}", src)
        self.assertIn('{upkeep="rebuild_acquire",item=item}', src)   # (ore mined to rebuild a turret: the turret's)
        self.assertIn("not (current and current.witness_tick) and DesignedStart.hand_back", src)

    def test_the_budget_counts_only_items_with_a_maker(self):
        site = pressed(6, 10, time_use={"shares": {"upkeep:craft:firearm-magazine": 0.4}})
        plan = {"purpose": "iron-smelting-start", "entities": [{"id": "f", "prototype": "stone-furnace"}]}
        with mock.patch.object(plan_hold, "makers",
                               lambda s, items, p, designer=False: ["x"] if items == ["firearm-magazine"] else []):
            self.assertEqual(plan_hold.countable(site, plan), ["firearm-magazine"])
            self.assertEqual(plan_hold.hand_hold(site, plan), plan_hold.HOLD)
        with mock.patch.object(plan_hold, "makers", lambda s, items, p, designer=False: []):
            self.assertIsNone(plan_hold.hand_hold(site, plan))


if __name__ == "__main__":
    unittest.main()
