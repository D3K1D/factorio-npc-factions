"""A plan's supplies come from any chest of its own near home, and a supply deposit is what the machine takes
(DesignedStart.home_chests, owned_output, supply_job, in Factorio's Lua 5.2). W-22, stage-silo: the rocket parts lay
in a chest by home in no plan and the silo's input chests stayed empty; 60 inserters into an assembler that took 50
failed the supply as partial_transfer."""
import unittest

import test_acquire_plan

CHESTS = """
    defines.inventory.chest = 1
    local function chest(x, items)
        local inv = {get_item_count=function(spec) return items[type(spec) == 'table' and spec.name or spec] or 0 end,
                     get_insertable_count=function() return 1000 end}
        return {valid=true, type='container', name='steel-chest', unit_number=x, position={x=x, y=0},
                get_inventory=function() return inv end}
    end
    store = chest(20, {['processing-unit']=50})
    input = chest(30, {})
    chests = {store, input}
    npc.character.surface.find_entities_filtered = function(f)
        if f.type == 'container' then return chests end
        return {}
    end
    npc.design = {status='building', plan={order={'in'}, entities={['in']={id='in', entity=input}},
        stages={{supplies={{entity_id='in', item='processing-unit', slot='input'}}}}}}
"""


class HomeChestTests(unittest.TestCase):
    def test_a_chest_by_home_in_no_plan_is_a_source_and_a_plans_input_chest_is_not(self):
        lua = test_acquire_plan.runtime()
        lua.execute(CHESTS)
        self.assertTrue(lua.eval("(DS.owned_output(npc, 'processing-unit')) == store"))
        lua.execute("input.get_inventory().get_item_count = function() return 99 end; npc.home_chests = nil")
        self.assertTrue(lua.eval("(DS.owned_output(npc, 'processing-unit')) == store"))   # (never its own input)

    def test_the_list_is_looked_over_again_in_a_while(self):
        lua = test_acquire_plan.runtime()
        lua.execute(CHESTS + "game.tick = 100; first = #DS.home_chests(npc); chests = {}")
        self.assertEqual(lua.eval("#DS.home_chests(npc)"), lua.eval("first"))
        lua.execute("game.tick = 100 + DS.HOME_CHESTS_LOOK")
        self.assertEqual(lua.eval("#DS.home_chests(npc)"), 0)

    def test_a_paused_plans_input_chest_is_no_source_and_a_new_plan_is_seen_at_once(self):
        lua = test_acquire_plan.runtime()
        lua.execute(CHESTS + """
            game.tick = 100
            input.get_inventory().get_item_count = function() return 99 end
            npc.paused_design, npc.design = npc.design, nil""")
        self.assertTrue(lua.eval("(DS.owned_output(npc, 'processing-unit')) == store"))
        lua.execute("npc.paused_design = nil")                                   # (a change of plans: looked again)
        self.assertTrue(lua.eval("(DS.owned_output(npc, 'processing-unit')) == input"))
        lua.execute("npc.home = {x=500, y=0}; chests = {}")                      # (home moved: looked again)
        self.assertEqual(lua.eval("#DS.home_chests(npc)"), 0)

    def test_a_chest_a_plan_takes_over_after_the_look_is_no_source_from_then_on(self):
        lua = test_acquire_plan.runtime()
        lua.execute(CHESTS + """
            game.tick = 100
            DS.home_chests(npc)                                   -- (looked over: store is a home chest)
            npc.design.plan.order[2] = 'adopted'
            npc.design.plan.entities.adopted = {id='adopted', entity=store}
            npc.design.plan.stages[1].supplies[2] = {entity_id='adopted', item='processing-unit', slot='input'}""")
        self.assertFalse(lua.eval("(DS.owned_output(npc, 'processing-unit')) == store"))


class DepositTests(unittest.TestCase):
    def test_a_supply_deposit_is_what_the_machine_takes_and_done_with_what_went_in(self):
        lua = test_acquire_plan.runtime()
        lua.execute("""
            defines.inventory.crafter_input = 2
            held.inserter = 60
            local inv = {get_item_count=function() return 0 end, get_insertable_count=function() return 50 end}
            machine = {valid=true, type='assembling-machine', get_inventory=function() return inv end,
                       get_recipe=function() return nil end}
            out = DS.supply_job(npc, {entity=machine}, {entity_id='m', item='inserter', slot='input'})""")
        self.assertEqual(lua.eval("out.mode"), "deposit")
        self.assertLessEqual(lua.eval("out.count"), 50)
        self.assertTrue(lua.eval("out.up_to"))

    def test_a_machine_with_no_room_gets_nothing(self):
        lua = test_acquire_plan.runtime()
        lua.execute("""
            defines.inventory.crafter_input = 2
            held.inserter = 60
            local inv = {get_item_count=function() return 0 end, get_insertable_count=function() return 0 end}
            machine = {valid=true, type='assembling-machine', get_inventory=function() return inv end,
                       get_recipe=function() return nil end}
            out = DS.supply_job(npc, {entity=machine}, {entity_id='m', item='inserter', slot='input'})""")
        self.assertIsNone(lua.eval("out"))


if __name__ == "__main__":
    unittest.main()
