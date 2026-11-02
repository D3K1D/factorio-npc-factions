"""What a burner really burns, and its next look when it falls due (DesignedStart.measured_draw, fuel_job's schedule),
in Factorio's Lua 5.2. From the Workers Union review (2026-10-29): burn_rate assumed full draw, so an idle furnace
looked as due as a busy one."""
import unittest

import test_retire


def runtime():
    lua = test_retire.runtime()
    lua.execute("""
        game.tick = 1000
        storage.burn_seen = nil
        e = {unit_number=77}
        FULL = 150000                                   -- (a stone furnace's 150 kW)
    """)
    return lua


class MeasuredTests(unittest.TestCase):
    def test_a_moving_average_of_what_it_burnt_within_a_floor_and_full_draw(self):
        lua = runtime()
        self.assertEqual(lua.eval("DS.measured_draw(e, 10e6, FULL)"), 150000)   # (no look yet: full draw)
        lua.execute("game.tick = 1060; r1 = DS.measured_draw(e, 10e6 - 90000, FULL)")
        self.assertEqual(lua.eval("r1"), 90000)                                 # (90 kJ in a second)
        lua.execute("game.tick = 1120; r2 = DS.measured_draw(e, 10e6 - 90000, FULL)")   # (idle a second)
        self.assertAlmostEqual(lua.eval("r2"), 63000)                           # (0.7 x 90 + 0.3 x 0)
        lua.execute("for t = 1180, 3000, 60 do game.tick = t; low = DS.measured_draw(e, 10e6 - 90000, FULL) end")
        self.assertEqual(lua.eval("low"), 150000 * 0.25)                        # (never below a quarter)

    def test_a_refill_only_starts_the_next_look(self):
        lua = runtime()
        lua.execute("""DS.measured_draw(e, 5e6, FULL)
                       game.tick = 1060; DS.measured_draw(e, 9e6, FULL)""")
        self.assertIsNone(lua.eval("storage.burn_seen.units[77].rate"))         # (more than before: no sample)
        self.assertEqual(lua.eval("storage.burn_seen.units[77].energy"), 9e6)

    def test_near_empty_it_is_judged_at_full_draw_whatever_it_burnt_idle(self):
        # (an idle furnace measured at the floor, then put to work: no running dry on the idle rate)
        lua = runtime()
        lua.execute("""
            prototypes.item.coal.fuel_value = 4e6
            storage.burn_seen = {n=1, units={[77]={tick=1000, energy=4e6, rate=150000 * 0.25}}}
            e.prototype = {get_max_energy_usage=function() return 150000 / 60 end}
            e.burner = {remaining_burning_fuel=0}
            inv = {get_contents=function() return {{name='coal', count=1}} end}
            left = DS.fuel_time(e, inv, 'coal')""")
        self.assertAlmostEqual(lua.eval("left"), 4e6 / 150000)                  # (26 s at full draw, not 106)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.lua = test_retire.runtime()
        self.lua.execute("""
            prototypes.item.coal.fuel_value = 4e6
            defines.inventory.fuel = 1
            game.tick = 1000
            npc.character = {valid=true, position={x=0, y=0}, surface={count_entities_filtered=function() return 0 end,
                find_entities_filtered=function() return {} end},
                get_main_inventory=function() return {get_item_count=function() return 30 end} end}
            npc.design, npc.design_history = {}, {}
            coal = 40
            b = {valid=true, type='furnace', unit_number=5, position={x=3, y=0},
                 prototype={get_max_energy_usage=function() return 150000 / 60 end}, burner={remaining_burning_fuel=0}}
            inv = {get_item_count=function() return coal end, get_contents=function() return {{name='coal', count=coal}} end}
            function look(t) game.tick = t; return DS.fuel_job(npc, b, inv, 'coal') end""")

    def test_not_due_it_is_looked_at_again_only_when_it_falls_due(self):
        lua = self.lua
        self.assertIsNone(lua.eval("(look(1000))"))
        self.assertGreater(lua.eval("npc.fuel_next[5].tick"), 1000 + 299)

    def test_one_emptied_meanwhile_is_looked_at_at_once(self):
        lua = self.lua
        lua.execute("look(1000); coal = 1")
        self.assertIsNotNone(lua.eval("(look(1010))"))                        # (well below: no waiting for the look)


if __name__ == "__main__":
    unittest.main()
