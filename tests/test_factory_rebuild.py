"""Where lost buildings come in the engineer's turn (mod/scripts/factory.lua's Factory.tick, in Factorio's Lua 5.2): the
whole path, not only DesignedStart.rebuild_now (W-16's own early hook lost its meta inside an `and`, and the 7-NPC
rehearsal 7e crashed on the first rebuild; nothing had run this path). Urgent work at home holds none of the rebuilds;
urgent work away lets only those within reach go back (rehearsal 7f: a defense ordered now on its home held 50)."""
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
        mods = {}
        for _, name in ipairs(needed) do   -- (what factory.lua leans on: nothing unless a test says so)
            mods[name] = setmetatable({}, {__index=function() return function() return nil end end})
            package.preload[name] = function() return mods[name] end
        end
        function log() end
        settings = {global=setmetatable({}, {__index=function() return {value=0} end})}
        storage = {test=true}
        game = {tick=0}
        Factory = assert(load(source))()
        DS, Agent, Worker = mods['scripts.designed_start'], mods['scripts.agent'], mods['scripts.worker']
        enqueued, bound = {}, {}
        Worker.enqueue = function(npc, job) enqueued[#enqueued + 1] = job; return true, 'job-' .. #enqueued end
        DS.bind = function(npc, id, meta) bound[#bound + 1] = meta end
        asked = {}
        DS.rebuild_now = function(npc, tick, opts)
            asked[#asked + 1] = opts or false
            if rebuild then return {kind='build', entity_name='burner-mining-drill'}, {upkeep='rebuild'} end
        end
        DS.rebuild_opts = function()
            if not urgent then return nil end
            if away then return {near=true} end
            return {agenda_first=true}
        end
        Agent.designed = false
        agenda_job = nil
        Agent.next = function()   -- (its agenda: a job of its own, or waiting)
            if agenda_job then return agenda_job, {intention={tool='build'}} end
            return false, 'waiting_for_its_defense'
        end
        DS.next = function() return {kind='build', entity_name='plan-piece'}, {build=true} end
        mods['scripts.behavior'].cadence = function() return 45 end
        npc = {id=1, force='f', home={x=0, y=0}, character={valid=true, position={x=0, y=0}},
               travelled=0, last_position={x=0, y=0}, plan_revision=0, step=99, receipts={}, phase='working',
               decisions=0}
    """)
    return lua


class FactoryRebuildTests(unittest.TestCase):
    def test_a_loss_goes_back_in_its_turn_with_its_meta(self):
        lua = runtime()
        lua.execute("rebuild = true; Factory.tick(npc, 100)")
        self.assertEqual(lua.eval("enqueued[1].entity_name"), "burner-mining-drill")
        self.assertEqual(lua.eval("bound[1].upkeep"), "rebuild")               # (the meta 7e lost)
        self.assertEqual(lua.eval("npc.phase"), "designed:upkeep:rebuild")

    def test_urgent_work_at_home_has_its_turn_first_and_the_losses_go_back_while_it_waits(self):
        lua = runtime()
        lua.execute("rebuild = true; urgent = true; away = false; Factory.tick(npc, 100)")
        self.assertEqual(lua.eval("enqueued[1].entity_name"), "burner-mining-drill")   # (its order waits: rebuilt)
        self.assertTrue(lua.eval("asked[1].agenda_first"))
        lua = runtime()
        lua.execute("""rebuild = true; urgent = true; away = false
                       agenda_job = {kind='move', position={x=5, y=5}}
                       Factory.tick(npc, 100)""")
        self.assertEqual(lua.eval("enqueued[1].kind"), "move")                 # (its order first)
        self.assertEqual(lua.eval("#asked"), 0)

    def test_urgent_work_away_lets_only_those_in_reach_go_back_and_the_rest_wait(self):
        lua = runtime()
        lua.execute("rebuild = false; urgent = true; away = true; Factory.tick(npc, 100)")
        self.assertTrue(lua.eval("asked[1].near"))

    def test_a_plan_short_of_its_building_share_goes_first(self):
        lua = runtime()
        lua.execute("""rebuild = true
                       DS.active = function() return true end
                       DS.build_starved = function() return true end
                       Factory.tick(npc, 100)""")
        self.assertEqual(lua.eval("enqueued[1].entity_name"), "plan-piece")
        self.assertEqual(lua.eval("#asked"), 0)



class MendTests(unittest.TestCase):
    """W-33: its hurt buildings mended while no plan of its own goes up."""
    MEND = """DS.mend_now = function(npc, tick)
                  mend_asked = (mend_asked or 0) + 1
                  return {kind='mend', targets={}}, {upkeep='mend'}
              end
              Factory.designed_engineer = function() return true end
              DS.next = function() return nil end
              Agent.next = function() return nil end"""

    def test_between_goals_it_mends(self):
        lua = runtime()
        lua.execute(self.MEND + "; Factory.tick(npc, 100)")
        self.assertEqual(lua.eval("enqueued[1].kind"), "mend")
        self.assertEqual(lua.eval("bound[1].upkeep"), "mend")
        self.assertEqual(lua.eval("npc.phase"), "designed:upkeep:mend")

    def test_waiting_on_its_next_plan_it_mends_but_never_while_a_stage_goes_up(self):
        lua = runtime()
        lua.execute(self.MEND + """; DS.active = function() return true end
                       npc.design = {status='waiting'}; Factory.tick(npc, 100)""")
        self.assertEqual(lua.eval("enqueued[1].kind"), "mend")
        lua = runtime()
        lua.execute(self.MEND + """; DS.active = function() return true end
                       npc.design = {status='building'}; Factory.tick(npc, 100)""")
        self.assertIsNone(lua.eval("mend_asked"))
        self.assertIsNone(lua.eval("enqueued[1]"))

    def test_nothing_to_mend_it_waits_between_goals(self):
        lua = runtime()
        lua.execute(self.MEND + """; DS.mend_now = function() return nil end; Factory.tick(npc, 100)""")
        self.assertEqual(lua.eval("npc.phase"), "designed:between-goals")
        self.assertIsNone(lua.eval("enqueued[1]"))


if __name__ == "__main__":
    unittest.main()
