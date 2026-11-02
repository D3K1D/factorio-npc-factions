"""Every wait of a plan has a bound and an escalation (designed_start.lua, in Factorio's Lua 5.2; W-31, SO-5's audit):
a producing stage's witness, a stage's start-up input, any acquisition reason, and an engineer's first plan failing or
blocked, each handed back or asked again in a while, never held for ever (a lab stage waited 496,000 ticks for packs,
normal-7b)."""
import unittest

import test_bootstrap_done
import test_clearing


def game():
    lua = test_clearing.game()
    lua.execute(test_bootstrap_done.SETUP)
    return lua


class BoundTests(unittest.TestCase):
    def test_a_witness_not_met_in_a_while_hands_the_plan_back(self):
        lua = game()
        lua.execute("defines.entity_status = defines.entity_status or {}; stage.supplies = {}; DS.next(npc, 100)")
        lua.execute("DS.next(npc, 200)")
        self.assertEqual(lua.eval("npc.design.status"), "building")
        lua.execute("out = {DS.next(npc, 100 + DS.STUCK_FOR + 1)}")
        self.assertEqual(lua.eval("out[2]"), "yielded")
        self.assertEqual(lua.eval("npc.design.status"), "deferred")         # (handed back: no failure noted)
        self.assertTrue(lua.eval("npc.design.reason:sub(1, 6) == 'stuck:'"))
        self.assertIsNotNone(lua.eval("npc.design_failures and npc.design_failures['iron-smelting-expansion']"))   # (held)

    def test_a_start_up_input_waited_on_too_long_hands_the_plan_back(self):
        src = (test_clearing.ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn('block_plan(npc,d,"start_up_waits:"..tostring(supply.item)..":"..why,tick,true)', src)
        self.assertIn("if supply.once then stage.once_wait=nil end", src)       # (reset by its job)

    def test_any_other_acquisition_reason_has_its_bound_too(self):
        src = (test_clearing.ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn('block_plan(npc,d,"waits:"..why,tick,true)', src)
        self.assertIn("d.locked_since,d.waiting_since=nil,nil", src)              # (reset by a build)


class RepairGivenUpTests(unittest.TestCase):
    def test_a_repair_given_up_on_a_piece_of_its_plan_hands_the_plan_back(self):
        # (W-32: "cannot fix unpowered ... needs a redesign", and the stage stayed producing, holding every goal)
        lua = game()
        lua.execute("defines.entity_status = defines.entity_status or {}; stage.supplies = {}; DS.next(npc, 100)")
        lua.execute("npc.design.repair_given_up = {symptom='unpowered', tick=150, x=1, y=2}; out = {DS.next(npc, 200)}")
        self.assertEqual(lua.eval("out[2]"), "yielded")
        self.assertEqual(lua.eval("npc.design.status"), "deferred")
        self.assertTrue(lua.eval("npc.design.reason:sub(1, 16) == 'repair_given_up:'"))
        src = (test_clearing.ROOT / "mod" / "scripts" / "corrector.lua").read_text(encoding="utf-8")
        self.assertIn("d.repair_given_up = {symptom=fix.symptom", src)                # (set by the corrector)
        self.assertIn("stage=fix.record and fix.record.stage}", src)

    def test_one_of_another_stage_leaves_this_one_be(self):
        lua = game()
        lua.execute("defines.entity_status = defines.entity_status or {}; stage.supplies = {}; DS.next(npc, 100)")
        lua.execute("npc.design.repair_given_up = {symptom='unpowered', stage='other'}; out = {DS.next(npc, 200)}")
        self.assertEqual(lua.eval("out[2]"), "producing")
        self.assertIsNone(lua.eval("npc.design.repair_given_up"))


class FirstPlanTests(unittest.TestCase):
    def designed(self, lua):
        lua.execute("""
            storage.test = nil
            settings = settings or {global={}}
            settings.global['npc-factions-start-mode'] = {value='designed'}""")

    def test_a_designed_engineer_s_first_plan_failing_asks_again_in_a_while(self):
        lua = game()
        self.designed(lua)
        lua.execute("""
            game.tick = 500
            npc.design_history = {}
            npc.design = {status='waiting', request_id=npc.id .. ':1', requested_tick=0, purpose='iron-smelting-start'}
            DS.fail({request_id=npc.id .. ':1', reason='nope'})""")
        self.assertEqual(lua.eval("npc.design.status"), "deferred")
        self.assertEqual(lua.eval("npc.design.next_goal_tick"), 500 + 7200)

    def test_its_first_plan_blocked_gets_a_next_goal(self):
        lua = game()
        self.designed(lua)
        lua.execute("""
            npc.design_history = {}
            for k = 1, DS.MAX_STALLED do DS.stall(npc, npc.design, 'x', 700) end""")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        self.assertEqual(lua.eval("npc.design.next_goal_tick"), 700 + 7200)


if __name__ == "__main__":
    unittest.main()
