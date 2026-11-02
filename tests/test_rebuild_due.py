"""A loss overdue goes back (W-34, in Factorio's Lua 5.2): normal-7d's 28 rebuilds took 138 s at the median and 556 s at
most, and 96 losses never came back. A stage short of its building share kept them from even being looked at (NPC 6:
belts, poles and a whole steam block, up to 1154 s), and the defense hold counted from the first look, not the loss.
Now: overdue REBUILD_DUE past its calm, a loss goes back past a starved stage; the hold counts from the loss; what feeds
its power goes back before the rest, after what defends."""
import unittest

import test_factory_rebuild
import test_rebuild_wait


def runtime():
    lua = test_rebuild_wait.runtime()
    lua.execute("logged = {}; log = function(s) logged[#logged + 1] = s end")
    return lua


class OverdueTests(unittest.TestCase):
    def test_a_loss_is_overdue_past_its_calm_and_told_once(self):
        lua = runtime()
        lua.execute("""
            lose(furnace, 1000)
            early = DS.rebuild_overdue(npc, 1000 + 1800 + DS.REBUILD_DUE - 1)
            late = DS.rebuild_overdue(npc, 1000 + 1800 + DS.REBUILD_DUE)
            again = DS.rebuild_overdue(npc, 1000 + 1800 + DS.REBUILD_DUE + 60)
            n = 0
            for _, s in ipairs(logged) do if s:find('overdue') then n = n + 1 end end""")
        self.assertFalse(lua.eval("early"))
        self.assertTrue(lua.eval("late"))
        self.assertTrue(lua.eval("again"))
        self.assertEqual(lua.eval("n"), 1)
        self.assertEqual(lua.eval("npc.rebuild_overdue_count"), 1)

    def test_the_defense_hold_counts_from_the_loss_not_the_first_look(self):
        # (NPC 6: not looked at for 182 s, then held a whole 120 s more; normal-7d)
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            defending = true
            a = job(1000 + DS.REBUILD_DEFENSE_HOLD - 60)        -- (first look: within the hold from the loss)
            b = job(1000 + DS.REBUILD_DEFENSE_HOLD)             -- (the hold is over, a minute after the first look)""")
        self.assertIsNone(lua.eval("a"))
        self.assertEqual(lua.eval("b.entity_name"), "stone-furnace")

    def test_what_feeds_its_power_goes_back_first_after_what_defends(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1; held['boiler'] = 1
            b = thing('boiler', {force='npc-force'})
            prototypes.entity['boiler'] = {type='boiler', tile_width=3, tile_height=2}
            iron_start.order[#iron_start.order + 1] = 'boil'
            iron_start.entities.boil = {id='boil', entity=b, prototype='boiler', item='boiler', world={x=30, y=5}}
            npc.unit_index = nil
            lose(furnace, 1000); lose(b, 1100)                  -- (the furnace lost first: the oldest)""")
        out = lua.eval("(job(3200))")
        self.assertEqual(out.entity_name if out.kind == "build" else out.placements[1].entity_name, "boiler")


class FactoryTickTests(unittest.TestCase):
    """Factory.tick: a stage short of its share builds first, but never past an overdue loss."""
    SETUP = """rebuild = true
               DS.active = function() return true end
               DS.build_starved = function() return true end"""

    def test_a_starved_stage_goes_first_while_no_loss_is_overdue(self):
        lua = test_factory_rebuild.runtime()
        lua.execute(self.SETUP + "; DS.rebuild_overdue = function() return false end; Factory.tick(npc, 100)")
        self.assertEqual(lua.eval("enqueued[1].entity_name"), "plan-piece")
        self.assertEqual(lua.eval("#asked"), 0)

    def test_an_overdue_loss_goes_back_past_a_starved_stage(self):
        lua = test_factory_rebuild.runtime()
        lua.execute(self.SETUP + "; DS.rebuild_overdue = function() return true end; Factory.tick(npc, 100)")
        self.assertEqual(lua.eval("enqueued[1].entity_name"), "burner-mining-drill")
        self.assertEqual(lua.eval("bound[1].upkeep"), "rebuild")

    def test_an_overdue_loss_and_a_starved_stage_take_turns(self):
        lua = test_factory_rebuild.runtime()
        lua.execute(self.SETUP + """; DS.rebuild_overdue = function() return true end
                    DS.OVERDUE_EVERY = 1800; DS.REBUILD_RUN_STARVED = 8
                    for t = 100, 400, 100 do npc.pending_job = nil; Factory.tick(npc, t) end
                    for t = 2000, 2200, 100 do npc.pending_job = nil; Factory.tick(npc, t) end""")
        names = [lua.eval(f"enqueued[{i}].entity_name") for i in range(1, 8)]
        # (a turn, then the stage's until OVERDUE_EVERY has passed, then a turn again)
        self.assertEqual(names, ["burner-mining-drill", "plan-piece", "plan-piece", "plan-piece",
                                 "burner-mining-drill", "plan-piece", "plan-piece"])
        self.assertEqual(lua.eval("asked[1].run"), 8)               # (a short walk past a starved stage)

    def test_with_nothing_it_can_put_back_the_stage_builds_and_it_looks_again_in_a_while(self):
        lua = test_factory_rebuild.runtime()
        lua.execute(self.SETUP + """; rebuild = false; DS.rebuild_overdue = function() return true end
                    Factory.tick(npc, 100); npc.pending_job = nil; Factory.tick(npc, 200)""")
        self.assertEqual((lua.eval("#asked"), lua.eval("enqueued[2].entity_name")), (1, "plan-piece"))
        self.assertEqual(lua.eval("npc.overdue_idle_until"), 400)


class AgendaTests(unittest.TestCase):
    """W-36 (normal-7e, NPC 4): under steady attack its build(now) orders on its own ground held every loss, power and
    belts up to 630 s. An overdue loss gets its short turn past them too."""
    SETUP = """rebuild = true; urgent = true; away = false
               agenda_job = {kind='build', entity_name='reinforce-turret'}"""

    def test_its_order_on_its_own_ground_goes_first_while_no_loss_is_overdue(self):
        lua = test_factory_rebuild.runtime()
        lua.execute(self.SETUP + "; DS.rebuild_overdue = function() return false end; Factory.tick(npc, 100)")
        self.assertEqual(lua.eval("enqueued[1].entity_name"), "reinforce-turret")

    def test_an_overdue_loss_takes_its_turn_past_the_order_and_they_alternate(self):
        lua = test_factory_rebuild.runtime()
        lua.execute(self.SETUP + """; DS.rebuild_overdue = function() return true end
                    for t = 100, 300, 100 do npc.pending_job = nil; Factory.tick(npc, t) end""")
        self.assertEqual([lua.eval(f"enqueued[{i}].entity_name") for i in range(1, 4)],
                         ["burner-mining-drill", "reinforce-turret", "reinforce-turret"])
        self.assertEqual(lua.eval("asked[1].run"), 8)

    def test_off_its_own_ground_only_what_it_reaches_goes_back(self):
        lua = test_factory_rebuild.runtime()
        lua.execute("rebuild = false; urgent = true; away = true; Factory.tick(npc, 100)")
        self.assertTrue(lua.eval("asked[1].near"))


class OwnGroundTests(unittest.TestCase):
    def test_away_is_off_its_own_ground_not_far_from_its_home_point(self):
        lua = runtime()
        lua.execute("""
            mine = 1
            npc.character.surface.count_entities_filtered = function(f)
                if f.type and f.force == npc.force then return mine end
                return 0
            end
            furnace_at = iron_start.entities['iron-smelting-start2'].world
            npc.home = {x=-500, y=0}; npc.character.position = {x=300, y=0}   -- (its plot's far edge: buildings by it)
            at_edge = DS.away_from_home(npc)
            mine = 0; npc.own_ground = nil
            off = DS.away_from_home(npc)""")
        self.assertFalse(lua.eval("at_edge"))
        self.assertTrue(lua.eval("off"))
        lua.execute("""npc.own_ground = nil; lose(furnace, 1000)       -- (what it lost by it: still its ground)
                       npc.character.position = {x=furnace_at.x + 3, y=furnace_at.y}
                       by_loss = DS.away_from_home(npc)
                       npc.own_ground = nil; npc.character.position = {x=2, y=0}; home_here = DS.away_from_home(npc)
                       npc.helping = {}; trip = DS.away_from_home(npc)""")
        self.assertFalse(lua.eval("by_loss"))
        self.assertFalse(lua.eval("home_here"))                                   # (by its home: its ground)
        self.assertTrue(lua.eval("trip"))                                         # (helping: a trip, wherever)
        lua.execute("""npc.helping = nil
                       npc.agent = {agenda={{priority='now', tool='stockpile'}, {priority='now', tool='visit'}}}
                       own_first = DS.on_trip(npc)
                       npc.agent.agenda = {{priority='now', tool='deliver'}}; deliver = DS.on_trip(npc)
                       npc.agent.agenda = {}; npc.recall = {}; recalled = DS.on_trip(npc)""")
        self.assertFalse(lua.eval("own_first"))                                  # (its own work first: no trip yet)
        self.assertTrue(lua.eval("deliver"))
        self.assertTrue(lua.eval("recalled"))

    def test_a_loss_past_every_hold_says_queued_not_its_last_wait(self):
        lua = runtime()
        lua.execute("""
            held['stone-furnace'] = 1
            lose(furnace, 1000)
            defending = true; job(1000 + 1800)              -- (calm over, held for the defense)
            before = npc.rebuild[1].wait
            defending = false; npc.rebuild[1].retry_at = 0
            job(1000 + 1800 + 60)
            after = npc.rebuild[1] and npc.rebuild[1].wait""")
        self.assertEqual(lua.eval("before"), "defense_going_up")
        self.assertEqual(lua.eval("after"), "queued")                       # (its turn given: queued, built next)


if __name__ == "__main__":
    unittest.main()
