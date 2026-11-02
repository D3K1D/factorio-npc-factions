"""A plan handed straight to the engineer while it waits for one keeps the request it replaces (DesignedStart.
keep_request, used by mod/scripts/proving.lua's hand_plan; in Factorio's Lua 5.2; W-27). death-world-waves-power: the
steam plan handed at its start took the place of its first request, iron-smelting-start, and its opening never smelted
iron."""
import unittest

import test_retire

ROOT = test_retire.ROOT


class KeepRequestTests(unittest.TestCase):
    def test_the_request_it_replaces_is_set_aside_under_a_new_id(self):
        lua = test_retire.runtime()
        lua.execute("""
            storage.design_sequence = 1
            npc.design = {status='waiting', request_id=npc.id .. ':1', purpose='iron-smelting-start', goal='iron',
                          origin={x=0, y=0}, export_cache={}}
            kept = DS.keep_request(npc, 50)""")
        self.assertEqual(lua.eval("kept.purpose"), "iron-smelting-start")
        self.assertEqual(lua.eval("kept.status"), "waiting")
        self.assertNotEqual(lua.eval("kept.request_id"), lua.eval("npc.design.request_id"))   # (asked afresh)
        self.assertIsNone(lua.eval("kept.export_cache"))
        self.assertEqual(lua.eval("kept.requested_tick"), 50)
        self.assertFalse(lua.eval("kept == npc.design"))

    def test_nothing_when_none_waits_or_one_is_set_aside(self):
        lua = test_retire.runtime()
        lua.execute("npc.design = {status='building'}")
        self.assertIsNone(lua.eval("DS.keep_request(npc, 1)"))
        lua.execute("npc.design = {status='waiting'}; npc.paused_design = {status='building'}")
        self.assertIsNone(lua.eval("DS.keep_request(npc, 1)"))

    def test_resumed_it_is_asked_afresh_its_wait_timed_from_then(self):
        # (Codex, W-27: set aside longer than its planner's wait, it timed out at once)
        lua = test_retire.runtime()
        lua.execute("""
            game = game or {}
            game.tick = 9000
            A = require('scripts.agent')
            A.event = function() end
            npc.paused_design = {status='waiting', requested_tick=50, export_cache={}}
            npc.design = {status='complete'}
            A.plan_finished(npc, 'steam-power', 'complete')""")
        self.assertEqual(lua.eval("npc.design.status"), "waiting")
        self.assertEqual(lua.eval("npc.design.requested_tick"), 9000)
        self.assertIsNone(lua.eval("npc.design.export_cache"))

    def test_the_proving_ground_hands_its_plans_so(self):
        src = (ROOT / "mod" / "scripts" / "proving.lua").read_text(encoding="utf-8")
        self.assertIn("local kept=DesignedStart.keep_request(npc,game.tick)", src)
        self.assertIn("if kept then npc.paused_design=kept end", src)
        self.assertIn("d.rejections,d.last_rejection=before[5],before[6]", src)
        ds = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")   # (a block for a player)
        self.assertIn("if npc.paused_design then npc.design=npc.paused_design; npc.paused_design=nil end", ds)


if __name__ == "__main__":
    unittest.main()
