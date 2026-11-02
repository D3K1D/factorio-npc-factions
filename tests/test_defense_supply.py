"""Supplies to defense while it matters (DesignedStart.defense_supply, in Factorio's Lua 5.2): every turret of its own
kept loaded, in a plan or not, the ones biters come at first and filled up, never by walking into them. (The proving
ground, 2026-10-27: 200 magazines carried, four turrets empty for 165 s and lost.) Also what a dropped plan leaves
standing, and pieces a build run could not put down."""
import unittest

import test_retire

SETUP = r"""
    defines.inventory.turret_ammo, defines.inventory.artillery_turret_ammo = 9, 10
    held = {['firearm-magazine']=200}
    npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
    local inv = {get_item_count=function(spec) return held[type(spec) == 'table' and spec.name or spec] or 0 end}
    npc.character = {valid=true, position={x=0, y=0}, surface={}, prototype={crafting_categories={crafting=true}},
                     get_main_inventory=function() return inv end}
    -- (what fits a turret: the game's ammo categories, through the armory)
    fits = {['gun-turret']={'firearm-magazine'}, ['modded-cannon-turret']={'cannon-shell'}}
    require('scripts.armory').ammo_for = function(name) return fits[name] or {} end
    turrets, foes = {}, {}
    local function d(a, b) return math.sqrt((a.x-b.x)^2 + (a.y-b.y)^2) end
    local surface = npc.character.surface
    surface.find_entities_filtered = function(f) return turrets end
    surface.count_entities_filtered = function(f)
        local n = 0
        for _, u in ipairs(foes) do if d(u, f.position) <= f.radius then n = n + 1 end end
        return n
    end
    n = 0
    function turret(name, x, y, ammo, range, kind, room)
        n = n + 1
        local t = {valid=true, name=name, type=kind or 'ammo-turret', unit_number=n, position={x=x, y=y}, loaded=ammo or 0,
                   prototype={attack_parameters={range=range or 18}}}
        t.get_inventory = function(index)
            t.asked = index
            return {get_item_count=function() return t.loaded end, get_insertable_count=function() return room or 200 end}
        end
        turrets[#turrets + 1] = t
        return t
    end
    function due(tick, urgent) local r = {DS.defense_supply(npc, tick or 1000, urgent)} return flat(r[1]), r[2] end
"""


def runtime():
    lua = test_retire.runtime()
    lua.execute(SETUP)
    return lua


class DefenseSupplyTests(unittest.TestCase):
    def test_a_turret_in_no_plan_with_biters_coming_is_filled_up_first(self):
        lua = runtime()
        lua.execute("""
            calm = turret('gun-turret', 10, 0, 2)          -- low, nothing near it
            front = turret('gun-turret', 80, 0, 12)        -- not low by the usual rule, but biters come at it
            foes = {{x=120, y=0}}                          -- 40 tiles off: past its range, within range + 32
            job, meta = due()""")
        self.assertEqual((lua.eval("job.kind"), lua.eval("job.target.unit_number")), ("transfer", 2))
        self.assertEqual(lua.eval("job.count"), 88)                            # filled: 200 carried over 2 turrets
        self.assertTrue(lua.eval("meta.urgent"))
        lua.execute("job, meta = due(1000, true); front.loaded = 100; job2 = due(1000, true)")
        self.assertEqual(lua.eval("job.target.unit_number"), 2)
        self.assertIsNone(lua.eval("job2"))                                    # urgent: only where biters come
        self.assertEqual(lua.eval("(due(1000)).target.unit_number"), 1)        # then the low one, as upkeep

    def test_an_empty_turret_comes_first_and_one_biters_are_at_is_not_walked_to(self):
        lua = runtime()
        lua.execute("""
            turret('gun-turret', 50, 0, 3)
            besieged = turret('gun-turret', 100, 0, 0)
            empty = turret('gun-turret', 0, 60, 0)
            foes = {{x=105, y=0}, {x=0, y=100}}           -- at the second; coming to the third""")
        self.assertEqual(lua.eval("(due()).target.unit_number"), 3)
        lua.execute("foes = {{x=105, y=0}}")
        self.assertEqual(lua.eval("(due()).target.unit_number"), 3)            # empty first, not the one besieged

    def test_with_a_row_of_its_own_making_them_magazines_come_from_the_row_never_crafted_from_plates(self):
        # (D-19, the user's U-1: the turret upkeep crafted magazines from plates a quarter to near half of its time)
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 0
            low = turret('gun-turret', 10, 0, 5)   -- (some left: it may wait on its row, D-22)
            made = {n=0}
            row = {valid=true, type='assembling-machine', unit_number=900, position={x=5, y=5},
                   get_output_inventory=function() return {get_item_count=function() return made.n end} end}
            npc.design_history = {plan('defense-production', {row})}
            npc.drills_seen = {tick=1000, items={}, smelts={}, loose={}, assembles={['firearm-magazine']={row}}}
            npc.fed_stores = {tick=0, items={}, stores={}, drilled={}, inserted={}}
            DS.FED_CHECK = 10 ^ 9
            DS.DRILLS_LOOK = 10 ^ 9
            DS.home_chests = function() return {} end   -- (the fixture's surface finds turrets for anything)
            made.n = 30""")
        job = lua.eval("(due())")
        self.assertEqual((job.kind, job.mode, job.target.unit_number), ("transfer", "withdraw", 900))
        # (a chest that feeds a row, holding magazines, is not taken from: only what a machine made)
        lua.execute("""
            made.n = 0
            feeds = {valid=true, type='container', unit_number=901, position={x=6, y=6},
                     get_inventory=function() return {get_item_count=function() return 40 end} end}
            npc.design_history = {plan('defense-production', {row, feeds})}
            job, meta = due()""")
        self.assertIsNone(lua.eval("job"))                                   # (it waits on its row: no craft)
        # the row's end chest, filled by an arm from its output belt and taken from by none: collected from
        lua.execute("""
            belt = {valid=true, type='transport-belt'}
            -- (an ammo line's arm takes from it onto its belt: still the row's end, not an input)
            line_arm = {valid=true, drop_target={valid=true, type='transport-belt'}}
            out = {valid=true, type='container', unit_number=902, position={x=7, y=7}, force='npc',
                   surface={find_entities_filtered=function() return {line_arm} end},
                   get_inventory=function() return {get_item_count=function() return 60 end} end}
            npc.design_history = {plan('defense-production', {row, feeds, out})}
            npc.fed_stores.inserted[902] = {{valid=true, pickup_target=belt}}
            job = due()""")
        self.assertEqual((lua.eval("job.mode"), lua.eval("job.target.unit_number")), ("withdraw", 902))
        lua.execute("line_arm.pickup_target = out; job = due()")
        self.assertEqual(lua.eval("job.target.unit_number"), 902)
        lua.execute("line_arm.drop_target = row; job = due()")              # (an arm into a machine: an input chest)
        self.assertIsNone(lua.eval("job"))
        self.assertNotEqual(lua.eval("npc.plate_wait and npc.plate_wait.items['firearm-magazine']"), None)

    def test_an_empty_turret_never_waits_on_its_row_and_a_stalled_row_is_not_waited_on(self):
        # (D-22, stage-silo from cd55ed0: its waves broke the wall while the turrets waited on their row)
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 0
            row = {valid=true, type='assembling-machine', unit_number=900, position={x=5, y=5}, status='working',
                   get_output_inventory=function() return {get_item_count=function() return 0 end} end}
            defines.entity_status = {working='working', no_ingredients='no_ingredients', low_power='low_power'}
            npc.design_history = {plan('defense-production', {row})}
            npc.drills_seen = {tick=1000, items={}, smelts={}, loose={}, assembles={['firearm-magazine']={row}}}
            npc.fed_stores = {tick=0, items={}, stores={}, drilled={}, inserted={}}
            DS.FED_CHECK, DS.DRILLS_LOOK = 10 ^ 9, 10 ^ 9
            DS.home_chests = function() return {} end
            function why_for(loaded)
                local t = turret('gun-turret', 10, 0, loaded)
                local _, why = DS.ammo_job(npc, t, t.get_inventory(9), nil, 20, 20)
                return why
            end""")
        self.assertEqual(lua.eval("why_for(10)"), "assembling:firearm-magazine")   # (some left, its row at work: waits)
        self.assertNotEqual(lua.eval("why_for(0)"), "assembling:firearm-magazine")  # (empty: the bridge by hand)
        lua.execute("row.status = 'no_ingredients'")
        self.assertNotEqual(lua.eval("why_for(10)"), "assembling:firearm-magazine")  # (its row starved: by hand)

    def test_a_chest_fed_by_hand_gets_what_its_stores_hold_first(self):
        # (D-19: the proving ring's loop chest stood empty beside 116 magazines in its home store)
        lua = runtime()
        lua.execute("""
            held['firearm-magazine'] = 0
            store = {valid=true, type='container', unit_number=700, position={x=1, y=1},
                     get_inventory=function() return {get_item_count=function() return 100 end} end}
            DS.home_chests = function() return {store} end
            loopchest = {valid=true, type='container', unit_number=701, position={x=0, y=-36},
                         get_inventory=function() return {get_item_count=function() return 0 end,
                                                          get_insertable_count=function() return 4800 end} end}
            job = DS.supply_job(npc, {id='c', entity=loopchest}, {entity_id='c', item='firearm-magazine', slot='input'},
                                false)""")
        self.assertEqual((lua.eval("job.mode"), lua.eval("job.target.unit_number")), ("withdraw", 700))

    def test_a_modded_turret_gets_what_its_ammo_category_takes(self):
        lua = runtime()
        lua.execute("""
            held['cannon-shell'] = 15
            turret('modded-cannon-turret', 5, 0, 0)""")
        self.assertEqual(lua.eval("(due()).item"), "cannon-shell")

    def test_a_turret_it_failed_to_load_waits_while_others_are_seen_to(self):
        lua = runtime()
        lua.execute("""
            a = turret('gun-turret', 5, 0, 0)
            b = turret('gun-turret', 9, 0, 1)
            npc.design_jobs = {j1={upkeep='turret_ammo', target=1}}
            DS.on_receipt(npc, {job_id='j1', status='failed', reason='inventory_out_of_reach'}, 1000)""")
        self.assertEqual(lua.eval("(due(1001)).target.unit_number"), 2)
        self.assertEqual(lua.eval("(due(1000 + DS.TURRET_BACKOFF)).target.unit_number"), 1)   # tried again later

    def test_upkeep_loads_turrets_before_its_other_rounds(self):
        lua = runtime()
        lua.execute("turret('gun-turret', 5, 0, 0)")
        job = lua.eval("(DS.upkeep(npc, 1000))")
        self.assertEqual((job.kind, job.item), ("transfer", "firearm-magazine"))


    def test_artillery_gets_shells_into_its_own_inventory_and_no_load_is_bigger_than_the_room(self):
        lua = runtime()
        lua.execute("""
            fits['artillery-turret'] = {'artillery-shell'}
            held['artillery-shell'] = 40
            gun = turret('artillery-turret', 5, 0, 0, 200, 'artillery-turret', 15)""")
        job = lua.eval("(due())")
        self.assertEqual((job.item, job.inventory, job.count), ("artillery-shell", 10, 15))

    def test_a_load_broken_off_on_the_way_waits_a_while_too(self):
        lua = runtime()
        lua.execute("""
            a = turret('gun-turret', 5, 0, 0)
            b = turret('gun-turret', 9, 0, 1)
            npc.design_jobs = {j1={upkeep='turret_ammo', target=1}}
            DS.on_receipt(npc, {job_id='j1', status='canceled', reason='combat_interrupt'}, 1000)""")
        self.assertEqual(lua.eval("(due(1001)).target.unit_number"), 2)
        self.assertEqual(lua.eval("(due(1000 + DS.TURRET_BACKOFF / 2)).target.unit_number"), 1)


class DroppedPlanTests(unittest.TestCase):
    def test_a_dropped_plan_leaves_its_turrets_and_walls_standing_and_picks_up_the_rest(self):
        lua = runtime()
        lua.execute("""
            local function e(kind) return {valid=true, type=kind, minable=true} end
            gun, wall, belt = e('ammo-turret'), e('wall'), e('transport-belt')
            npc.design_history = {{purpose='old', order={}, entities={}, stages={}}}
            npc.design = {status='building', purpose='defense', stage_index=1,
                plan={plan_id='d', purpose='defense', order={'g', 'w', 'b'}, stages={{id='s', entity_ids={'g', 'w', 'b'}}},
                      entities={g={id='g', entity=gun}, w={id='w', entity=wall}, b={id='b', entity=belt}}}}
            npc.design.plan.entities.g.failures = 0
            npc.design_jobs = {}
            for k = 1, 6 do      -- a piece it cannot get up: the plan is dropped
                npc.design_jobs['j' .. k] = {build=true, entity_id='g'}
                DS.on_receipt(npc, {job_id='j' .. k, status='failed', reason='missing_healthy_normal_placement_item'}, 10 + k)
            end""")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        salvage = [lua.eval(f"npc.design_salvage[{i}].type") for i in range(1, lua.eval("#npc.design_salvage") + 1)]
        self.assertEqual(salvage, ["transport-belt"])
        self.assertTrue(lua.eval("npc.design_history[#npc.design_history].partial"))   # kept: rebuilt if lost


class BuildRunTests(unittest.TestCase):
    def test_what_a_finished_run_did_not_put_down_is_built_on_its_own_and_so_tried(self):
        lua = runtime()
        lua.execute("""
            npc.design = {status='building', stage_index=1, plan={plan_id='p', order={'a', 'b', 'c'},
                stages={{id='s', entity_ids={'a', 'b', 'c'}}},
                entities={a={id='a'}, b={id='b'}, c={id='c'}}}}
            placed = {valid=true}
            npc.design_jobs = {r1={build_run=true, ids={'a', 'b', 'c'}}}
            DS.on_receipt(npc, {job_id='r1', status='complete', placed={{id='a', entity=placed}}}, 100)""")
        self.assertIsNone(lua.eval("npc.design.plan.entities.a.solo"))
        self.assertTrue(lua.eval("npc.design.plan.entities.b.solo"))
        self.assertTrue(lua.eval("npc.design.plan.entities.c.solo"))


if __name__ == "__main__":
    unittest.main()
