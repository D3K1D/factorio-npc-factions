"""An engineer that dies comes back as a player does (mod/scripts/factory.lua), in Factorio's Lua 5.2."""
import re
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    source = (ROOT / "mod" / "scripts" / "factory.lua").read_text(encoding="utf-8")
    lua.globals().source = source
    needed = sorted(set(re.findall(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", source)))
    lua.globals().needed = lua.table_from(needed)
    lua.execute("""
        for _, name in ipairs(needed) do
            package.preload[name] = function()   -- (what factory.lua leans on, not exercised here)
                return setmetatable({}, {__index=function() return function() return {} end end})
            end
        end
        logged = {}
        function log(line) logged[#logged + 1] = line end
        settings = {global=setmetatable({}, {__index=function() return {value=0} end})}
        made = {}
        biters_at = {}   -- x of a respawn point: biters near it
        surface = {index=1,
                   find_non_colliding_position=function(_, at) return {x=at.x + 0.5, y=at.y} end,
                   count_entities_filtered=function(spec) return biters_at[math.floor(spec.position.x)] and 1 or 0 end,
                   create_entity=function(spec) local c = {valid=true, name=spec.name, position=spec.position,
                                                            force=spec.force}
                                              made[#made + 1] = c; return c end}
        game = {surfaces={[1]=surface}, tick=0}
        Factory = assert(load(source))()
        npc = {id=1, force='npc-faction-1', home={x=10, y=20}, spawn={x=0, y=0}, surface_index=1,
               character={valid=false}, jobs={{kind='mine'}}, receipts={}, phase='working'}
    """)
    return lua


class DesignedEngineerTests(unittest.TestCase):
    def test_an_engineer_of_plans_is_known_as_one_whatever_became_of_its_first_drill(self):
        # (run 24, 2026-10-25: its bus retired the first burner pair, and the scripted start's check called its
        # factory destroyed and set it aside for good)
        lua = runtime()
        self.assertFalse(lua.eval("Factory.designed_engineer(npc)"))       # (the scripted start's own engineer)
        lua.execute("npc.design_history = {{purpose='iron-smelting-start'}}")
        self.assertTrue(lua.eval("Factory.designed_engineer(npc)"))
        lua.execute("npc.design_history = {}; npc.design = {status='deferred'}")
        self.assertTrue(lua.eval("Factory.designed_engineer(npc)"))


class RespawnTests(unittest.TestCase):
    def test_off_for_now_a_lost_body_stays_lost(self):
        # (a body brought back hung the game, 2026-10-25: off until it is designed as a whole; tests reload)
        lua = runtime()
        self.assertFalse(lua.eval("Factory.RESPAWN"))
        lua.execute("Factory.tick(npc, 1000); Factory.tick(npc, 1000 + 100000)")
        self.assertEqual((lua.eval("npc.phase"), lua.eval("#made")), ("body-lost", 0))

    def test_a_dead_engineer_comes_back_at_home_after_a_players_respawn_time(self):
        # (one-engineer run 2026-10-24: its body was lost for good to a biter attack)
        lua = runtime()
        lua.execute("Factory.RESPAWN = true")
        lua.execute("Factory.tick(npc, 1000)")
        self.assertEqual(lua.eval("npc.phase"), "body-lost")
        self.assertEqual(lua.eval("#made"), 0)
        wait = lua.eval("Factory.RESPAWN_TICKS")
        lua.execute(f"Factory.tick(npc, {1000 + wait - 1})")
        self.assertEqual(lua.eval("#made"), 0)                       # not before a player's respawn time
        lua.execute(f"Factory.tick(npc, {1000 + wait})")
        self.assertEqual(lua.eval("#made"), 1)
        self.assertTrue(lua.eval("npc.character == made[1]"))
        self.assertEqual((lua.eval("made[1].position.x"), lua.eval("made[1].position.y")), (10.5, 20))   # at home
        self.assertEqual((lua.eval("npc.phase"), lua.eval("#npc.jobs"), lua.eval("npc.respawns")), ("respawned", 0, 1))
        self.assertIsNone(lua.eval("npc.loss_recorded"))
        self.assertIn("respawned", lua.eval("logged[1]"))
        self.assertIsNone(lua.eval("npc.corpse_at"))                     # (it had not moved: nowhere known)
        lua.execute("npc.character = {valid=false}; npc.loss_recorded = 5000; npc.last_position = {x=3, y=4}")
        lua.execute(f"Factory.tick(npc, {5000 + 2 * wait})")             # (a second death soon after: twice the wait)
        self.assertEqual((lua.eval("npc.corpse_at.x"), lua.eval("npc.corpse_at.y")), (3, 4))   # where it fell

    def test_never_back_into_the_fight_and_each_death_soon_after_the_last_waits_longer(self):
        # (one-engineer run 2026-10-25: it came back at home while biters held it, and died 48 times running)
        lua = runtime()
        lua.execute("biters_at[10] = true; npc.loss_recorded = 0")          # biters at home
        self.assertTrue(lua.eval("Factory.respawn(npc, 600)"))
        self.assertEqual(lua.eval("made[1].position.x"), 0.5)               # at its spawn, clear of them
        lua.execute("biters_at[0] = true; npc.character = {valid=false}; npc.loss_recorded = 1000")
        self.assertFalse(lua.eval("Factory.respawn(npc, 2000)"))            # biters at both: it waits
        self.assertEqual(lua.eval("npc.respawn_retry"), 2300)
        self.assertFalse(lua.eval("Factory.respawn(npc, 2100)"))            # (not each tick)
        wait = lua.eval("Factory.RESPAWN_TICKS")
        self.assertEqual(lua.eval("Factory.respawn_delay(npc, 2000)"), 2 * wait)   # one death lately: twice
        lua.execute("npc.respawn_ticks = {1, 2, 3, 4, 5, 6, 7, 8}")
        self.assertEqual(lua.eval("Factory.respawn_delay(npc, 2000)"), 32 * wait)  # (at most 32 times)
        self.assertEqual(lua.eval("Factory.respawn_delay(npc, 100000)"), wait)     # long ago: as at first

    def test_with_no_room_at_home_it_comes_back_at_its_spawn_and_waits_while_there_is_none(self):
        lua = runtime()
        lua.execute("""
            room = {[0]=true}
            surface.find_non_colliding_position = function(_, at) if room[at.x] then return {x=at.x, y=at.y} end end
            npc.loss_recorded = 0""")
        self.assertTrue(lua.eval("Factory.respawn(npc, 700)"))
        self.assertEqual(lua.eval("made[1].position.x"), 0)             # its spawn
        lua.execute("npc.character = {valid=false}; npc.loss_recorded = 800; room = {}")
        self.assertFalse(lua.eval("Factory.respawn(npc, 1500)"))        # nowhere free: tried again later
        self.assertEqual(lua.eval("npc.loss_recorded"), 800)


if __name__ == "__main__":
    unittest.main()
