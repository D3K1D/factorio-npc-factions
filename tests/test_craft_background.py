"""All crafting in the background (worker.lua, designed_start's stage wait, in Factorio's Lua 5.2): a craft goes on while
the engineer walks and builds, a job that needs what is still being crafted waits for it where it is, and the time spent
waiting on crafts is tallied (time_use "wait:craft"). The user's recorded play (2026-10-28): the player crafts 37% of
the time, 42% of that while walking or mining."""
import unittest

import test_clearing
import test_obstacles
import test_opening_pace


class WorkerTests(unittest.TestCase):
    def test_its_march_resupply_crafts_in_the_background_counting_what_its_queue_still_makes(self):
        import test_retire
        lua = test_retire.runtime()
        lua.execute("""
            coming = 0
            require('scripts.worker').craft_coming = function(_, item) return item == 'firearm-magazine' and coming or 0 end
            local inv = {get_item_count=function(spec) return (spec.name or spec) == 'iron-plate' and 200 or 0 end}
            npc.character = {valid=true, get_main_inventory=function() return inv end,
                             prototype={crafting_categories={crafting=true}}}
            npc.force.recipes = {['firearm-magazine']={name='firearm-magazine', enabled=true, category='crafting',
                energy=1, ingredients={{type='item', name='iron-plate', amount=4}},
                products={{type='item', name='firearm-magazine', amount=1}}}}""")
        job = lua.eval("(require('scripts.agent').resupply(npc))")
        self.assertEqual((job.kind, job.recipe, job.background), ("craft", "firearm-magazine", True))
        lua.execute("coming = 20")
        self.assertIsNone(lua.eval("(require('scripts.agent').resupply(npc))"))   # on its way: not again

    def test_a_build_whose_item_is_still_being_crafted_waits_for_it_there(self):
        lua = test_obstacles.worker_with("""
            defines.direction = {north=0, east=4, south=8, west=12}
            defines.build_check_type = {manual=0}
            prototypes.entity['stone-furnace'] = {type='furnace', tile_width=2, tile_height=2}
            prototypes.item['stone-furnace'] = {type='item', place_result={name='stone-furnace'}}
            prototypes.recipe = {['stone-furnace']={products={{type='item', name='stone-furnace', amount=1}}}}
            character.can_place_entity = function() return true end
            character.surface.can_place_entity = function() return true end
            character.force = {name='me'}
            character.crafting_queue = {{index=1, recipe='stone-furnace', count=1}}
            slots = {}
            local inv = setmetatable({}, {__len=function() return #slots end, __index=function(_, i)
                if type(i) == 'number' then return slots[i] end end})
            inv.get_item_count = function() return 0 end
            character.get_main_inventory = function() return inv end
            assert(W.enqueue(npc, {kind='build', entity_name='stone-furnace', item='stone-furnace', position={x=3, y=0}}))
            W.tick(npc, 1); W.tick(npc, 2)""")
        self.assertEqual(lua.eval("npc.current_job.state"), "waiting_craft")
        self.assertIsNone(lua.eval("npc.failures and npc.failures[1]"))
        lua.execute("character.crafting_queue = nil; W.tick(npc, 3)")              # nothing coming: it fails as before
        self.assertEqual(lua.eval("npc.failures[1].reason"), "missing_healthy_normal_placement_item")
        receipt = list(lua.eval("npc.receipts").values())[0]
        self.assertEqual(receipt.craft_wait, 2)                                     # how long it waited, for the tally

    def test_an_intermediate_in_the_queue_is_nothing_to_wait_for(self):
        lua = test_opening_pace.crafting_worker()
        lua.execute("""prototypes.recipe = {['iron-gear-wheel']={products={{name='iron-gear-wheel', amount=1}}}}
                       queue[1] = {index=1, recipe='iron-gear-wheel', count=5, prerequisite=true}""")
        self.assertEqual(lua.eval("W.craft_coming(npc, 'iron-gear-wheel')"), 0)
        lua.execute("queue[1].prerequisite = false")
        self.assertEqual(lua.eval("W.craft_coming(npc, 'iron-gear-wheel')"), 5)


class TallyTests(unittest.TestCase):
    def test_a_jobs_wait_on_a_craft_is_tallied_apart_from_its_own_time(self):
        lua = test_clearing.game()
        lua.execute("""DS.tally(npc, {build=true}, {kind='build', item='stone-furnace', status='complete',
                                                    started_tick=100, tick=400, craft_wait=120})""")
        self.assertEqual(lua.eval("npc.time_use.tags['build:build:stone-furnace']"), 180)
        self.assertEqual(lua.eval("npc.time_use.tags['wait:craft']"), 120)


class StageTests(unittest.TestCase):
    def stage(self, x):
        lua = test_clearing.game()
        lua.execute(f"""
            prototypes.recipe = {{['transport-belt']={{products={{{{type='item', name='transport-belt', amount=2}}}}}}}}
            prototypes.entity['transport-belt'].tile_width, prototypes.entity['transport-belt'].tile_height = 1, 1
            npc.character.crafting_queue_size = 1
            npc.character.crafting_queue = {{{{index=1, recipe='transport-belt', count=10, prerequisite=false}}}}
            npc.character.build_distance = 10
            DS.fought_over = function() return false end
            local stage = {{id='s', entity_ids={{'a'}}, supplies={{}}, witnesses={{}}}}
            npc.design = {{status='building', stage_index=1, plan={{stages={{stage}}, entities={{
                a={{id='a', prototype='transport-belt', item='transport-belt', world={{x={x}, y=0.5}}, direction=0}}}}}}}}""")
        return lua

    def test_with_only_crafting_left_it_walks_to_where_the_next_piece_goes(self):
        lua = self.stage(60.5)
        job = lua.eval("(DS.next(npc, 100))")
        self.assertEqual((job.kind, job.position.x), ("move", 60.5))

    def test_there_already_it_waits_and_the_wait_is_tallied(self):
        lua = self.stage(3.5)
        lua.execute("a = {DS.next(npc, 100)}; b = {DS.next(npc, 160)}")
        self.assertEqual(lua.eval("b[2]"), "crafting:transport-belt")
        self.assertEqual(lua.eval("npc.time_use.tags['wait:craft']"), 60)


if __name__ == "__main__":
    unittest.main()
