"""A hand-fed start-up machine's stage is done once other machines of its own make its product with no hands
(DesignedStart.made_without_hands, the stage's witnesses looked at before its supplies), in Factorio's Lua 5.2. W-21:
opening-pace's bootstrap furnace wanted nine plates of its own while drill-fed furnaces made them, and its iron ore was
mined by hand eight times, every drill's ore going into a furnace."""
import unittest

import test_clearing

SETUP = """
    prototypes.entity['stone-furnace'] = {name='stone-furnace', type='furnace', tile_width=2, tile_height=2,
        collision_mask={layers={object=true}}}
    prototypes.recipe = prototypes.recipe or {}
    prototypes.recipe['iron-plate'] = {name='iron-plate', products={{type='item', name='iron-plate', amount=1}},
        ingredients={{type='item', name='iron-ore', amount=1}}}
    DS.fought_over = function() return false end
    boot = {valid=true, type='furnace', name='stone-furnace', unit_number=11, products_finished=0,
            position={x=5, y=5}, get_recipe=function() return {name='iron-plate'} end,
            get_inventory=function() return {get_item_count=function() return 0 end} end,
            get_output_inventory=function() return {get_item_count=function() return 0 end} end}
    other = {valid=true, type='furnace', name='stone-furnace', unit_number=12, products_finished=40,
             position={x=30, y=5}, get_recipe=function() return {name='iron-plate'} end,
             get_inventory=function() return {get_item_count=function() return 0 end} end,
             get_output_inventory=function() return {get_item_count=function() return 0 end} end}
    fed = {}
    DS.fed_stores = function() return {drilled=fed, inserted={}, items={}, stores={}} end
    local boot_stage = {id='bootstrap-plates', entity_ids={'boot'},
        supplies={{entity_id='boot', item='iron-ore', slot='input', once=true}},
        witnesses={{kind='product_counter_delta', entity_id='boot', item='iron-plate', min_delta=9}}}
    npc.design = {status='building', stage_index=1, purpose='iron-smelting-expansion', plan={plan_id='p',
        stages={boot_stage}, order={'boot'}, entities={boot={id='boot', entity=boot, prototype='stone-furnace',
        item='stone-furnace', world={x=5, y=5}, failures=0}}}}
    npc.design_history = {{purpose='iron-smelting-start', stages={}, order={'f'},
        entities={f={id='f', entity=other, prototype='stone-furnace', item='stone-furnace', world={x=30, y=5}}}}}
    stage = boot_stage
"""


class BootstrapTests(unittest.TestCase):
    def test_its_stage_is_done_once_a_drill_fed_furnace_makes_the_plates(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            fed[12] = {['iron-ore']=true}
            DS.next(npc, 100)                                  -- (the witnesses start)
            DS.next(npc, 200)""")
        self.assertEqual(lua.eval("stage.status"), "complete")

    def test_with_no_other_maker_it_is_fed_as_ever(self):
        lua = test_clearing.game()
        lua.execute(SETUP + """
            DS.next(npc, 100)
            out = {DS.next(npc, 200)}""")
        self.assertNotEqual(lua.eval("stage.status"), "complete")
        self.assertFalse(lua.eval("DS.made_without_hands(npc, 'iron-plate', boot)"))

    def test_the_machine_itself_never_counts(self):
        lua = test_clearing.game()
        lua.execute(SETUP + "fed[11] = {['iron-ore']=true}")
        self.assertFalse(lua.eval("DS.made_without_hands(npc, 'iron-plate', boot)"))
        lua.execute("fed[12] = {['iron-ore']=true}")
        self.assertTrue(lua.eval("DS.made_without_hands(npc, 'iron-plate', boot)"))

    def test_a_furnace_whose_feeders_bring_other_things_does_not_count(self):
        # (Codex, W-21: a drill bringing copper ore to a furnace last set to iron plate makes no iron plate)
        lua = test_clearing.game()
        lua.execute(SETUP + "fed[12] = {['copper-ore']=true}")
        self.assertFalse(lua.eval("DS.made_without_hands(npc, 'iron-plate', boot)"))

    def test_a_start_up_input_with_its_product_not_had_otherwise_still_holds_its_stage(self):
        lua = test_clearing.game()
        lua.execute(SETUP + "DS.next(npc, 100)")
        self.assertFalse(lua.eval("DS.start_up_unneeded(npc, npc.design, stage, stage.supplies[1])"))
        lua.execute("fed[12] = {['iron-ore']=true}")
        self.assertTrue(lua.eval("DS.start_up_unneeded(npc, npc.design, stage, stage.supplies[1])"))

    def test_once_it_made_its_own_plates_its_ore_is_no_longer_fed(self):
        lua = test_clearing.game()
        lua.execute(SETUP + "DS.next(npc, 100); boot.products_finished = 9")
        self.assertTrue(lua.eval("DS.start_up_unneeded(npc, npc.design, stage, stage.supplies[1])"))
        lua.execute("DS.next(npc, 200)")
        self.assertEqual(lua.eval("stage.status"), "complete")


if __name__ == "__main__":
    unittest.main()
