"""Hand logistics as sweeps (worker.lua's sweep job, DesignedStart.sweep_of and its rounds, in Factorio's Lua 5.2): take
from or give to every entity in reach in one job, a few ticks apiece, as a player's ctrl-drag with the even-distribution
mod (the user, 2026-10-28: "collect all the coal from the loop, then ... distribute it among the furnaces and drills
within seconds")."""
import unittest

import test_obstacles
import test_retire


def sweeping_worker():
    return test_obstacles.worker_with("""
        defines.inventory.fuel, defines.inventory.chest = 1, 2
        prototypes.item.coal = {type='item', name='coal'}
        reach_ok = {}
        character.can_reach_entity = function(e) return reach_ok[e] ~= false end
        held = 30
        local stack = {valid_for_read=true, name='coal', quality={name='normal'}, health=1}
        setmetatable(stack, {__index=function(_, k) if k == 'count' then return held end end,
                             __newindex=function(_, k, v) if k == 'count' then held = v end end})
        local inv = setmetatable({}, {__len=function() return 1 end, __index=function(_, i)
            if i == 1 then return stack end end})
        inv.get_item_count = function() return held end
        inv.insert = function(s) held = held + s.count return s.count end
        inv.valid = true
        character.get_main_inventory = function() return inv end
        n = 0
        function burner()
            n = n + 1
            local b = {valid=true, type='furnace', unit_number=n, position={x=n, y=0}, surface=character.surface,
                       force=character.force, coal=0}
            b.get_fuel_inventory = function()
                return {valid=true, get_insertable_count=function() return 50 end,
                        insert=function(s) b.coal = b.coal + s.count return s.count end}
            end
            return b
        end
    """)


class WorkerSweepTests(unittest.TestCase):
    def test_a_distribute_sweep_gives_each_its_share_a_few_ticks_apiece(self):
        lua = sweeping_worker()
        lua.execute("""
            a, b, c = burner(), burner(), burner()
            assert(W.enqueue(npc, {kind='sweep', item='coal', mode='deposit',
                                   entries={{target=a, count=10}, {target=b, count=12}, {target=c, count=8}}}))
            for t = 1, 20 do W.tick(npc, t) end""")
        self.assertEqual((lua.eval("a.coal"), lua.eval("b.coal"), lua.eval("c.coal")), (10, 12, 8))
        receipt = list(lua.eval("npc.receipts").values())[0]
        self.assertEqual((receipt.kind, receipt.status, receipt.count), ("sweep", "complete", 30))
        self.assertEqual(len(receipt.moved), 3)
        self.assertLessEqual(receipt.tick - receipt.started_tick, 3 * 3 + 2)    # (seconds, not a walk each)

    def test_one_it_cannot_get_to_is_left_and_the_rest_go_on(self):
        lua = sweeping_worker()
        lua.execute("""
            a, b = burner(), burner()
            reach_ok[a] = false
            stubs['scripts.navigation'].step = function() return 'failed', 'no_path' end
            assert(W.enqueue(npc, {kind='sweep', item='coal', mode='deposit',
                                   entries={{target=a, count=10}, {target=b, count=12}}}))
            for t = 1, 20 do W.tick(npc, t) end""")
        self.assertEqual((lua.eval("a.coal"), lua.eval("b.coal")), (0, 12))
        receipt = list(lua.eval("npc.receipts").values())[0]
        self.assertEqual((receipt.status, receipt.count), ("complete", 12))

    def test_a_sweep_must_name_real_things(self):
        lua = sweeping_worker()
        self.assertFalse(lua.eval("(W.enqueue(npc, {kind='sweep', item='coal', mode='deposit', entries={}}))"))
        self.assertFalse(lua.eval("(W.enqueue(npc, {kind='sweep', item='coal', mode='give', "
                                  "entries={{target=burner(), count=1}}}))"))


class PlannerSweepTests(unittest.TestCase):
    def test_others_near_join_up_to_what_it_holds_and_far_ones_do_not(self):
        lua = test_retire.runtime()
        lua.execute("""
            held = {coal=25}
            npc.character = {valid=true, reach_distance=10,
                get_main_inventory=function() return {get_item_count=function(s) return held[s.name] or 0 end} end}
            local function at(x) return {valid=true, unit_number=x, position={x=x, y=0}} end
            first = at(0)
            out = DS.sweep_of(npc, {kind='transfer', target=first, item='coal', count=10, mode='deposit'},
                {{target=at(5), count=10}, {target=at(50), count=10}, {target=at(8), count=10}})""")
        self.assertEqual(lua.eval("out.kind"), "sweep")
        counts = [lua.eval(f"out.entries[{i}].count") for i in range(1, lua.eval("#out.entries") + 1)]
        self.assertEqual(counts, [10, 10, 5])                                 # (the far one left; 25 held in all)
        lua.execute("""alone = DS.sweep_of(npc, {kind='transfer', target=first, item='coal', count=10, mode='deposit'},
                                             {{target={valid=true, unit_number=99, position={x=90, y=0}}, count=5}})""")
        self.assertEqual(lua.eval("alone.kind"), "transfer")                 # nothing near: the transfer as it was

    def test_a_sweeps_shares_are_counted_off_its_round(self):
        lua = test_retire.runtime()
        lua.execute("""
            npc.character = {valid=true, get_main_inventory=function()
                return {get_item_count=function() return 0 end} end}
            npc.design = {}
            npc.fuel_rounds = {coal={fuel='coal', split={[1]=10, [2]=12}}}
            npc.design_jobs = {j={upkeep='supply', item='coal', entity_id='f1', slot='fuel'}}
            DS.on_receipt(npc, {job_id='j', kind='sweep', mode='deposit', item='coal', status='complete', count=22,
                                moved={{unit_number=1, type='furnace', count=10}, {unit_number=2, type='furnace', count=7}}},
                          100)""")
        self.assertEqual((lua.eval("npc.fuel_rounds.coal.split[1]"), lua.eval("npc.fuel_rounds.coal.split[2]")), (0, 5))


if __name__ == "__main__":
    unittest.main()
