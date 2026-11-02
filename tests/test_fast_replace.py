"""Upgrades as a player makes them (worker.lua's Worker.fast_replace, in Factorio's Lua 5.2): one of its own of the same
group at the spot is fast-replaced, contents and recipe kept by the game, the old one back in its hands. From the
Workers Union review (2026-10-29; their construct.lua:942-972)."""
import unittest

import test_build_item


class FastReplaceTests(unittest.TestCase):
    def build(self, setup):
        t = test_build_item.WorkerTests()
        return t.build("""
            stack(2, 1)
            free = 3
            local inv = character.get_main_inventory()
            inv.count_empty_stacks = function() return free end
            inv.insert = function() return 1 end
            old = {valid=true, type='furnace', name='stone-furnace', force=character.force, position={x=3, y=0}}
            character.surface.find_entities_filtered = function(f) return (f.force and not f.name and not f.type) and {old} or {} end
            character.surface.can_fast_replace = function() return replaceable end
            replaceable = true
            args = nil
            local create = character.surface.create_entity
            character.surface.create_entity = function(spec) args = spec; return create(spec) end
        """ + setup)

    def test_its_own_of_the_same_group_is_replaced_in_one_go(self):
        lua = self.build("")
        self.assertTrue(lua.eval("args.fast_replace"))
        self.assertTrue(lua.eval("args.character == character"))
        receipt = list(lua.eval("npc.receipts").values())[0]
        self.assertTrue(receipt.fast_replaced)
        self.assertEqual(lua.eval("slots[1].count"), 1)                       # (one new one spent)

    def test_with_no_room_for_the_old_one_it_says_so(self):
        lua = self.build("free = 0")
        self.assertIsNone(lua.eval("args"))
        self.assertEqual(lua.eval("npc.failures[1].reason"), "inventory_full")

    def test_where_the_game_allows_none_it_builds_as_ever(self):
        lua = self.build("replaceable = false")
        self.assertFalse(lua.eval("args.fast_replace or false"))

    def test_a_full_inventory_with_the_new_ones_last_slot_freed_by_it_is_room_enough(self):
        lua = self.build("free = 0; slots[1].count = 1")
        self.assertTrue(lua.eval("args.fast_replace"))

    def test_a_replace_the_game_refuses_puts_the_item_back_where_it_was(self):
        lua = self.build("character.surface.create_entity = function(spec) args = spec; return nil end")
        self.assertEqual(lua.eval("slots[1].count"), 2)
        self.assertEqual(lua.eval("npc.failures[1].reason"), "fast_replace_failed")

    def test_out_of_build_reach_it_walks_first(self):
        lua = self.build("old.position = {x=30, y=0}")
        self.assertFalse(lua.eval("args and args.fast_replace or false"))


class PlanTests(unittest.TestCase):
    def test_the_planner_leaves_the_old_one_standing_for_the_replace(self):
        # (Codex, 2026-10-29: the spot check broke it first, and the build never came)
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.entity['steel-furnace'] = {name='steel-furnace', type='furnace', tile_width=2, tile_height=2,
                collision_mask={layers={object=true}}}
            held['steel-furnace'] = 1
            DS.fought_over = function() return false end
            old = {valid=true, type='furnace', name='stone-furnace', force=npc.force, minable=true,
                   position={x=5, y=5}}
            surface.find_entities_filtered = function(f) return (f.force and not f.name and not f.type) and {old} or {} end
            surface.can_fast_replace = function() return replaceable end
            DS.Obstacles.verdict = function() return 'clearable' end
            DS.Obstacles.breakable = function() return old end
            local stage = {id='s', entity_ids={'f'}, supplies={}, witnesses={}}
            function plan() npc.design = {status='building', stage_index=1, purpose='turrets', plan={plan_id='p',
                stages={stage}, order={'f'}, entities={f={id='f', prototype='steel-furnace', item='steel-furnace',
                world={x=5, y=5}, failures=0}}}} end
            replaceable = true; plan(); first, why1 = DS.next(npc, 100)
            replaceable = false; plan(); second, why2 = DS.next(npc, 200)""")
        self.assertEqual(lua.eval("first and first.kind or why1"), "build")
        self.assertEqual(lua.eval("second and second.kind or why2"), "mine")                       # (no replace: cleared as ever)


if __name__ == "__main__":
    unittest.main()
