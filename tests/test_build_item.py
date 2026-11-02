"""A building to put down whose item is gone or does not fit by the time it is built (worker.lua's build, designed_start's
DesignedStart.usable), in Factorio's Lua 5.2: a damaged item goes down with its health, and one it counted but could not
use is got again, never the same build tried blind. (Rehearsal 3, 2026-10-27: 16 turret builds failed
missing_healthy_normal_placement_item: the kit picks its turrets up damaged after a fight.)"""
import unittest

import test_clearing
import test_obstacles


class WorkerTests(unittest.TestCase):
    def build(self, stacks):
        lua = test_obstacles.worker_with("""
            defines.direction = {north=0, east=4, south=8, west=12}
            defines.build_check_type = {manual=0}
            prototypes.entity['gun-turret'] = {type='ammo-turret', tile_width=2, tile_height=2}
            prototypes.item['gun-turret'] = {type='item', place_result={name='gun-turret'}}
            character.can_place_entity = function() return true end
            character.force = {name='me'}
            made = nil
            character.surface.can_place_entity = function() return true end
            character.surface.create_entity = function(spec)
                made = {valid=true, max_health=400, health=400, position=spec.position, name=spec.name}
                return made
            end
            script = {raise_script_built=function() end}
            slots = {}
            local inv = setmetatable({}, {__len=function() return #slots end, __index=function(_, i)
                if type(i) == 'number' then return slots[i] end end})
            inv.get_item_count = function() local n = 0 for _, s in ipairs(slots) do n = n + s.count end return n end
            inv.can_insert = function() return true end
            character.get_main_inventory = function() return inv end
            function stack(count, health)
                slots[#slots + 1] = {valid_for_read=true, name='gun-turret', quality={name='normal'}, count=count,
                                     health=health}
            end
        """)
        lua.execute(stacks)
        lua.execute("""
            assert(W.enqueue(npc, {kind='build', entity_name='gun-turret', item='gun-turret', position={x=3, y=0}}))
            W.tick(npc, 1)""")
        return lua

    def test_a_damaged_turret_goes_down_with_its_health(self):
        lua = self.build("stack(1, 0.4)")
        self.assertEqual(lua.eval("made.health"), 160)
        self.assertEqual(lua.eval("slots[1].count"), 0)

    def test_an_undamaged_one_is_used_first(self):
        lua = self.build("stack(1, 0.4); stack(1, 1)")
        self.assertEqual(lua.eval("made.health"), 400)
        self.assertEqual((lua.eval("slots[1].count"), lua.eval("slots[2].count")), (1, 0))


    def test_an_item_at_no_health_is_not_put_down(self):
        lua = self.build("stack(1, 0)")
        self.assertIsNone(lua.eval("made"))
        self.assertEqual(lua.eval("npc.failures[1].reason"), "missing_healthy_normal_placement_item")


class PlanTests(unittest.TestCase):
    def test_an_item_a_build_could_not_use_is_got_again_not_built_with(self):
        lua = test_clearing.game()
        lua.execute("""
            prototypes.entity['gun-turret'] = {name='gun-turret', type='ammo-turret', tile_width=2, tile_height=2,
                collision_mask={layers={object=true}}}
            held['gun-turret'] = 1
            DS.fought_over = function() return false end
            local stage = {id='s', entity_ids={'t'}, supplies={}, witnesses={}}
            npc.design = {status='building', stage_index=1, purpose='turrets', plan={plan_id='p', stages={stage},
                order={'t'}, entities={t={id='t', prototype='gun-turret', item='gun-turret', world={x=5, y=5},
                failures=0}}}}
            first = {DS.next(npc, 100)}
            npc.design_jobs = {j1={build=true, entity_id='t'}}
            DS.on_receipt(npc, {job_id='j1', status='failed', reason='missing_healthy_normal_placement_item',
                                item='gun-turret'}, 101)
            again = {DS.next(npc, 102)}""")
        self.assertEqual(lua.eval("first[1].kind"), "build")
        self.assertEqual(lua.eval("(DS.usable(npc, 'gun-turret', 102))"), 0)
        self.assertNotEqual(lua.eval("again[1] and again[1].kind"), "build")       # not tried blind
        self.assertTrue(lua.eval("again[2]").startswith(("recipe_locked", "waiting_for")))   # it waits for one
        lua.execute("held['gun-turret'] = 2")                                    # what it holds changed
        self.assertEqual(lua.eval("(DS.usable(npc, 'gun-turret', 103))"), 2)
        self.assertEqual(lua.eval("(DS.next(npc, 104)).kind"), "build")


if __name__ == "__main__":
    unittest.main()
