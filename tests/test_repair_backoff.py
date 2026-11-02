"""A repair that failed backs off for its whole place, not one building (Corrector.place_key, backed_off, finish; in
Factorio's Lua 5.2; W-28). The grown bus's iron stage stood complete with its inserters unpowered: a pole bridge was
tried for each inserter in turn, twice, five minutes each, while every other goal waited 200 game minutes."""
import unittest

import test_corrector


class BackoffTests(unittest.TestCase):
    def runtime(self):
        return test_corrector.PoleBridgeTests.runtime(test_corrector.PoleBridgeTests())

    def test_a_failed_bridge_holds_every_other_building_of_its_plan_and_longer_each_time(self):
        lua = self.runtime()
        lua.execute("""
            function acquire() return nil end
            poles = {pole(0.5, 0.5, 1)}
            field[1].electric_network_id = nil
            npc.design = nil""")
        lua.eval("C.step(npc, 2000, acquire)")
        self.assertEqual(lua.eval("npc.corrector.fix.fix"), "pole_bridge")
        lua.eval("C.step(npc, 2000 + 18001, acquire)")                        # (took too long: not fixed)
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        till = lua.eval("(function() for _, b in pairs(npc.corrector.backoff) do return b.till end end)()")
        self.assertEqual(till, 20001 + lua.eval("C.BACKOFF"))
        self.assertTrue(any("leaves unpowered repairs" in str(s) for s in lua.eval("logged").values()))
        # (any other unpowered building of that plan: not tried meanwhile)
        lua.execute("npc.corrector.gave_up = {}; npc.corrector.tries = {}; npc.corrector.next_scan = 0")
        lua.eval("C.step(npc, 20002, acquire)")
        self.assertIsNone(lua.eval("npc.corrector.fix"))
        # (after it, tried again; failed again, held twice as long)
        lua.execute("npc.corrector.next_scan = 0")
        lua.eval("C.step(npc, 20002 + C.BACKOFF, acquire)")
        self.assertEqual(lua.eval("npc.corrector.fix.fix"), "pole_bridge")
        start = 20002 + lua.eval("C.BACKOFF")
        lua.eval(f"C.step(npc, {start} + 18001, acquire)")
        till = lua.eval("(function() for _, b in pairs(npc.corrector.backoff) do return b.till end end)()")
        self.assertEqual(till, start + 18001 + 2 * lua.eval("C.BACKOFF"))

    def test_one_that_cannot_be_laid_out_holds_its_plan_too(self):
        # (Codex, W-28: its failure was kept under its cell while the scan asks by its plan)
        lua = self.runtime()
        lua.execute("""
            C.steps = function() return nil end
            npc.design = nil
            for _, p in ipairs(npc.design_history) do p.plan_id = 'bus-1' end""")
        lua.eval("C.step(npc, 2000, function() return nil end)")
        self.assertTrue(lua.eval("C.backed_off(npc.corrector, 'unpowered', {plan_id='bus-1'}, {x=999, y=999}, 2001)"))

    def test_its_place_is_its_plan_else_its_cell(self):
        lua = self.runtime()
        self.assertEqual(lua.eval("C.place_key('unpowered', {plan_id='bus-1'}, {x=5, y=5})"), "unpowered@bus-1")
        self.assertEqual(lua.eval("C.place_key('unpowered', nil, {x=40, y=-5})"), "unpowered@1,-1")
        self.assertFalse(lua.eval("C.backed_off({}, 'unpowered', nil, {x=0, y=0}, 1)"))


if __name__ == "__main__":
    unittest.main()
