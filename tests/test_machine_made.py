"""Once its own machines make it, a shortage is waited out and told, never met by hand (DesignedStart.drills_make,
note_wait, note_hand_fuel, in Factorio's Lua 5.2). The user's recorded play (2026-10-28): the player never mined ore and
stopped fuelling by hand at minute 49; the NPC mined iron ore 21% of its time at minute 39 and carried coal 25% at 162."""
import unittest

import test_retire

SETUP = r"""
    defines.inventory.fuel, defines.inventory.crafter_input = 1, 2
    prototypes.item.coal.fuel_value = 4e6
    held = {coal=0}
    game.tick = 1000
    npc.home, npc.design = {x=0, y=0}, {}
    drills = {}
    npc.character = {valid=true, position={x=0, y=0},
                     surface={count_entities_filtered=function() return 1 end,
                              find_entities_filtered=function(f)
                                  if f.type == 'mining-drill' then return drills end
                                  return {}
                              end,
                              find_non_colliding_position=function(_, p) return {x=p.x, y=p.y} end},
                     get_main_inventory=function()
                         return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
    function slots(t)
        return {get_item_count=function(spec) return t[spec.name] or 0 end,
                is_empty=function() for _, n in pairs(t) do if n > 0 then return false end end return true end,
                get_contents=function() local o = {} for k, n in pairs(t) do
                    if n > 0 then o[#o + 1] = {name=k, count=n} end end return o end}
    end
    local far = 100
    for _, p in ipairs(npc.design_history) do
        for _, id in ipairs(p.order) do
            local e = p.entities[id].entity
            e.position = e.position or {x=far, y=0}; far = far + 10
            e.get_output_inventory = function() return slots({}) end
            e.get_fuel_inventory = function() return slots({}) end
            e.get_inventory = function() return slots({}) end
        end
    end
    src, fuel, out = {}, {coal=10}, {}
    furnace.position = {x=3, y=0}
    furnace.get_inventory = function(i) if i == defines.inventory.crafter_input then return slots(src) end end
    furnace.get_fuel_inventory = function() return slots(fuel) end
    furnace.get_output_inventory = function() return slots(out) end
    acquire = require('scripts.agent').designed.acquire
    function want(item, n) local r = {acquire(npc, item or 'iron-plate', n or 20, 0)} return r[1], r[2] end
    function a_drill_on(ore)
        drills[#drills + 1] = {valid=true, mining_target={prototype={mineable_properties={products={{name=ore}}}}}}
        npc.drills_seen = nil
    end
"""


def runtime():
    lua = test_retire.runtime()
    lua.execute(SETUP)
    return lua


class MachineMadeTests(unittest.TestCase):
    def test_in_the_opening_it_still_mines_ore_to_smelt_by_hand(self):
        lua = runtime()
        job = lua.eval("(want())")
        self.assertEqual((job.kind, job.item), ("mine", "iron-ore"))

    def test_once_its_drills_mine_the_ore_a_plate_shortage_waits_on_the_furnaces_and_is_told(self):
        lua = runtime()
        lua.execute("a_drill_on('iron-ore'); r = {want()}")
        self.assertIsNone(lua.eval("r[1]"))
        self.assertEqual(lua.eval("r[2]"), "smelting:iron-plate")
        lua.execute("for t = 1001, 1600, 30 do game.tick = t; want() end")
        w = lua.eval("DS.export_wait(npc)['iron-plate']")
        self.assertGreaterEqual(w.ticks, 570)
        self.assertEqual((w.want, w.since), (20, 1000))
        self.assertTrue(w.none)                                       # no furnace of its own smelts it
        lua.execute("held['iron-ore'] = 20; game.tick = 1700")
        job = lua.eval("(want())")
        self.assertEqual((job.mode, job.item), ("deposit", "iron-ore"))   # ore in hand: smelted

    def test_ore_itself_is_left_to_its_drills_but_what_rocks_give_is_gathered(self):
        lua = runtime()
        lua.execute("""
            prototypes.get_entity_filtered = function()
                return {rock={mineable_properties={products={{name='stone'}, {name='coal'}}}}}
            end
            local find = npc.character.surface.find_entities_filtered
            npc.character.surface.find_entities_filtered = function(f)     -- (a rock standing near)
                if f.name then return {{valid=true, minable=true}} end
                return find(f)
            end
            a_drill_on('iron-ore'); a_drill_on('stone')""")
        self.assertEqual(lua.eval("select(2, want('iron-ore', 10))"), "drills_make:iron-ore")
        self.assertEqual(lua.eval("(want('stone', 10)).kind"), "mine")   # rocks give stone: as ever

    def test_a_wait_halves_with_time_and_a_new_one_starts_afresh(self):
        lua = runtime()
        lua.execute("""
            DS.note_wait(npc, 'copper-plate', 5, 100)
            DS.note_wait(npc, 'copper-plate', 5, 200)
            DS.note_wait(npc, 'copper-plate', 5, 1000)      -- (a gap: a new wait)
            DS.note_wait(npc, 'copper-plate', 5, 100 + DS.TALLY_HALF)""")
        w = lua.eval("npc.plate_wait.items['copper-plate']")
        self.assertEqual(w.ticks, 50)                                 # 100 waited, halved once
        self.assertEqual(w.since, 100 + lua.eval("DS.TALLY_HALF"))

    def test_after_a_long_wait_its_hands_help_and_a_stopped_drill_is_no_source(self):
        lua = runtime()
        lua.execute("""
            a_drill_on('iron-ore')
            for t = 1000, 1000 + DS.MACHINE_WAIT, 60 do game.tick = t; want() end
            game.tick = 1060 + DS.MACHINE_WAIT""")
        self.assertEqual((lua.eval("(want())").kind, lua.eval("(want())").item), ("mine", "iron-ore"))   # rescue
        lua = runtime()
        lua.execute("""
            defines.entity_status = {working=1, no_minable_resources=21}
            a_drill_on('iron-ore'); drills[1].status = 21""")
        self.assertEqual(lua.eval("(want())").kind, "mine")                    # its patch run out: no source

    def test_a_furnace_of_its_own_making_it_is_no_missing_source(self):
        lua = runtime()
        lua.execute("""
            prototypes.recipe = prototypes.recipe or {}
            prototypes.recipe['iron-plate'] = {name='iron-plate', products={{name='iron-plate'}}}
            local line = {valid=true, get_recipe=function() return {name='iron-plate'} end}
            local find = npc.character.surface.find_entities_filtered
            npc.character.surface.find_entities_filtered = function(f)
                if f.type == 'furnace' then return {line} end
                return find(f)
            end
            a_drill_on('iron-ore'); want(); game.tick = 1030; want()""")
        self.assertIsNone(lua.eval("DS.export_wait(npc)['iron-plate'].none"))



class NaturalTests(unittest.TestCase):
    def test_rocks_that_give_its_ore_are_gathered_if_near_never_its_patch(self):
        # (Space Age's volcanic rocks give iron ore: counted as a rock's item anywhere, the ore was mined from its patch,
        # long-61 2026-10-28)
        lua = runtime()
        lua.execute("""
            prototypes.get_entity_filtered = function()
                return {['huge-volcanic-rock']={mineable_properties={products={{name='stone'}, {name='iron-ore'}}}}}
            end
            rocks_near = 0
            local find = npc.character.surface.find_entities_filtered
            npc.character.surface.find_entities_filtered = function(f)
                if f.name then return rocks_near > 0 and {{valid=true, minable=true}} or {} end
                return find(f)
            end
            a_drill_on('iron-ore')""")
        self.assertEqual(lua.eval("select(2, want('iron-ore', 10))"), "drills_make:iron-ore")   # none near: waits
        lua.execute("rocks_near = 1")
        job = lua.eval("(want('iron-ore', 10))")
        self.assertEqual((job.kind, job.item, job.natural_only), ("mine", "iron-ore", True))
        lua.execute("""npc.design_jobs = {j={acquire='iron-ore'}}
                       DS.on_receipt(npc, {job_id='j', kind='mine', item='iron-ore', status='failed',
                                           reason='no_local_resource'}, game.tick)""")
        self.assertEqual(lua.eval("select(2, want('iron-ore', 10))"), "drills_make:iron-ore")   # none it could reach

    def test_a_burner_drill_out_of_fuel_is_still_its_machine_for_it(self):
        lua = runtime()
        lua.execute("""
            defines.entity_status = {working=1, no_fuel=59}
            a_drill_on('iron-ore'); drills[1].status = 59""")
        self.assertEqual(lua.eval("select(2, want('iron-ore', 10))"), "drills_make:iron-ore")


    def test_a_natural_only_mine_job_takes_the_rock_never_the_patch(self):
        import test_clearing
        lua = test_clearing.worker()
        lua.execute("""
            character.prototype = {mining_speed=0.5}
            function at(x, kind, products, time)
                local e = thing('neutral', products)
                e.position, e.type, e.name = {x=x, y=0}, kind, kind .. x
                e.prototype.mineable_properties.mining_time = time
                return e
            end
            ore = at(1.5, 'resource', {{type='item', name='iron-ore', amount=1}}, 1)
            rock = at(30, 'simple-entity', {{type='item', name='iron-ore', amount=10}}, 2)
            character.surface.find_entities_filtered = function(f)
                local out = {}
                for _, e in ipairs({ore, rock}) do
                    for _, t in ipairs(f.type) do if e.type == t then out[#out + 1] = e end end
                end
                return out
            end
            assert(W.enqueue(npc, {kind='mine', item='iron-ore', count=5, natural_only=true}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("npc.current_job.target.name"), "simple-entity30")

class HandFuelTests(unittest.TestCase):
    def test_fuel_carried_by_hand_is_counted_against_its_plan(self):
        lua = runtime()
        lua.execute("""
            npc.design_jobs = {
                j1={upkeep='supply', slot='fuel', item='coal', entity_id='b', purpose='steam-power'},
                j2={upkeep='supply', slot='fuel', item='coal', entity_id='b', purpose='steam-power'},
                j3={upkeep='supply', slot='input', item='iron-plate', entity_id='a', purpose='gears'}}
            DS.on_receipt(npc, {job_id='j1', status='complete', kind='transfer', mode='deposit', count=20,
                                started_tick=100, tick=400}, 400)
            DS.on_receipt(npc, {job_id='j2', status='complete', kind='transfer', mode='withdraw', count=30,
                                started_tick=500, tick=700}, 700)
            DS.on_receipt(npc, {job_id='j3', status='complete', kind='transfer', mode='deposit', count=20,
                                started_tick=800, tick=900}, 900)""")
        out = lua.eval("DS.export_hand_fuel(npc)")
        self.assertEqual(list(out.keys()), ["steam-power"])
        self.assertEqual((out["steam-power"].ticks, out["steam-power"]["items"]), (500, 20))


if __name__ == "__main__":
    unittest.main()
