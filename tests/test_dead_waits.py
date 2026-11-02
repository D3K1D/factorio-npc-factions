"""A plan waits on an item no source of its own can hand it (designed_start, in Factorio's Lua 5.2). long-64
(2026-10-29): an iron expansion sat 9 minutes in "supply-wait:drills_make:iron-ore", carrying ore by hand to furnaces
its own burner drills fed, ore those drills never leave loose. Also: from trees and rocks, a trip's worth at the least."""
import unittest

import test_machine_made
import test_retire


class DrillFedTests(unittest.TestCase):
    def test_ore_into_a_furnace_its_own_drill_feeds_is_no_supply(self):
        lua = test_retire.runtime()
        lua.execute("""
            defines.inventory.crafter_input = 2
            drill.mining_target = {prototype={mineable_properties={products={{name='iron-ore'}}}}}
            npc.character = {valid=true, get_main_inventory=function()
                return {get_item_count=function() return 0 end} end}
            furnace.get_inventory = function() return {get_item_count=function() return 0 end} end
            out = {DS.supply_job(npc, {entity=furnace}, {entity_id='f', item='iron-ore', slot='input'})}""")
        self.assertTrue(lua.eval("DS.drill_fed(npc, furnace, 'iron-ore')"))
        self.assertIsNone(lua.eval("out[1]"))
        self.assertIsNone(lua.eval("out[2]"))                                 # (no wait either: nothing to do)
        self.assertFalse(lua.eval("DS.drill_fed(npc, furnace, 'coal')"))


class StageTests(unittest.TestCase):
    def test_one_supply_waiting_holds_neither_the_stage_nor_its_witnesses(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            defines.inventory.lab_input = 3
            local lab = {valid=true, type='lab', get_inventory=function() return {get_item_count=function() return 0 end} end}
            local stage = {id='s', entity_ids={}, supplies={{entity_id='l', item='no-such-pack', slot='input'}},
                           witnesses={{kind='entity_built', entity_id='l'}}, witness_tick=1}
            npc.design = {status='building', stage_index=1, purpose='x', plan={plan_id='p', stages={stage, {id='t',
                entity_ids={}, supplies={}, witnesses={}}}, order={'l'}, entities={l={id='l', entity=lab}}}}
            DS.fought_over = function() return false end
            out = {DS.next(npc, 100)}""")
        # (its pack cannot be had: the supply waits; its witness is met: the stage is done all the same)
        self.assertEqual(lua.eval("npc.design.plan.stages[1].status"), "complete")

    def test_a_once_supply_its_stage_starts_with_still_holds_it(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            defines.inventory.lab_input = 3
            local lab = {valid=true, type='lab', get_inventory=function() return {get_item_count=function() return 0 end} end}
            local stage = {id='s', entity_ids={}, supplies={{entity_id='l', item='no-such-pack', slot='input', once=true}},
                           witnesses={{kind='entity_built', entity_id='l'}}, witness_tick=1}
            npc.design = {status='building', stage_index=1, purpose='x', plan={plan_id='p', stages={stage, {id='t',
                entity_ids={}, supplies={}, witnesses={}}}, order={'l'}, entities={l={id='l', entity=lab}}}}
            DS.fought_over = function() return false end
            out = {DS.next(npc, 100)}""")
        self.assertNotEqual(lua.eval("npc.design.plan.stages[1].status"), "complete")


class DeadWaitTests(unittest.TestCase):
    def test_a_wait_on_drills_none_of_which_leaves_ore_loose_ends_soon(self):
        lua = test_machine_made.runtime()
        lua.execute("""
            a_drill_on('iron-ore')
            drills[1].drop_target = {valid=true, type='furnace'}     -- (into its furnace: none loose)
            npc.drills_seen = nil""")
        self.assertEqual(lua.eval("select(2, want('iron-ore', 10))"), "drills_make:iron-ore")
        lua.execute("for t = 1000, 1000 + DS.DEAD_WAIT, 60 do game.tick = t; want('iron-ore', 10) end")
        lua.execute("game.tick = 1060 + DS.DEAD_WAIT")
        self.assertEqual(lua.eval("(want('iron-ore', 10)).kind"), "mine")      # (its hands, after 30 s, not 5 min)

    def test_from_trees_and_rocks_a_trips_worth_at_the_least(self):
        lua = test_machine_made.runtime()
        lua.execute("""
            prototypes.get_entity_filtered = function()
                return {rock={mineable_properties={products={{name='stone'}}}}}
            end""")
        self.assertEqual(lua.eval("(want('stone', 2)).count"), lua.eval("DS.NATURAL_BATCH"))


if __name__ == "__main__":
    unittest.main()
