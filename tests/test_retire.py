"""Retiring superseded builds (DesignedStart.retire) in Factorio's Lua 5.2, without Factorio."""
from pathlib import Path
import unittest

import test_character_layer

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = test_character_layer.runtime()
    for name in ("base_map", "teach", "progression", "corrector", "base_memory", "platform_launch", "platform_build", "space_feed", "orbit",
                 "agent",
                 "recipes", "armory", "game_facts", "team_world", "team_research", "radio", "team_supply", "team_waves",
                 "team_war",
                 "priorities", "clearing", "obstacles", "factory_plan", "combat_odds", "holograms",
                 "designed_start"):
        lua.globals().module_sources["scripts." + name] = \
            (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    # what the agent needs from the factions' society and combat, which these tests do not exercise
    lua.globals().module_sources["scripts.society"] = "return {declare_war=function() return true end}"
    lua.globals().module_sources["scripts.combat"] = "return {equip_now=function() return true end}"
    lua.globals().module_sources["scripts.diplomacy"] = "return {state=function() return 'peace' end}"
    lua.globals().module_sources["scripts.aggro"] = (ROOT / "mod" / "scripts" / "aggro.lua").read_text(encoding="utf-8")
    lua.globals().module_sources["scripts.combat_routes"] =         (ROOT / "mod" / "scripts" / "combat_routes.lua").read_text(encoding="utf-8")
    lua.globals().module_sources["scripts.war"] = (ROOT / "mod" / "scripts" / "war.lua").read_text(encoding="utf-8")
    lua.globals().module_sources["scripts.intel"] = (ROOT / "mod" / "scripts" / "intel.lua").read_text(encoding="utf-8")
    lua.globals().module_sources["scripts.drive"] = (ROOT / "mod" / "scripts" / "drive.lua").read_text(encoding="utf-8")
    lua.globals().module_sources["scripts.worker"] = "return {cancel=function() end}"
    lua.execute("""
        defines = defines or {inventory={}, direction={north=0, east=4, south=8, west=12}, build_check_type={manual=0}}
        DS = require('scripts.designed_start')
        -- (a sweep read as one transfer: its first stop, all it moves; W-15)
        function flat(j)
            if j and j.kind == 'sweep' then
                local n = 0
                for _, e in ipairs(j.entries) do n = n + e.count end
                return {kind='transfer', mode=j.mode, item=j.item, target=j.entries[1].target, count=n,
                        inventory=j.entries[1].inventory, sweep=j}
            end
            return j
        end
        local n = 0
        function thing(kind, extra)
            n = n + 1
            local e = {valid=true, unit_number=n, type=kind}
            for k, v in pairs(extra or {}) do e[k] = v end
            return e
        end
        function plan(purpose, list)
            local p = {purpose=purpose, plan_id=purpose, order={}, entities={}, stages={}}
            for i, e in ipairs(list) do
                local id = purpose .. i
                p.order[#p.order + 1] = id
                p.entities[id] = {id=id, entity=e}
            end
            return p
        end
        -- The old base: the first furnace feeds red science (which feeds the lab) and a steel furnace.
        furnace = thing('furnace')
        drill = thing('mining-drill', {drop_target=furnace})
        assembler = thing('assembling-machine')
        lab = thing('lab')
        steel = thing('furnace')
        pole = thing('electric-pole')
        copper = thing('furnace')
        iron_start = plan('iron-smelting-start', {drill, furnace})
        red = plan('red-science-automation', {thing('inserter', {pickup_target=furnace, drop_target=assembler}),
            assembler, thing('inserter', {pickup_target=assembler, drop_target=lab})})
        labs = plan('research-lab', {lab})
        steel_line = plan('steel-smelting', {thing('inserter', {pickup_target=furnace, drop_target=steel}), steel, pole})
        copper_start = plan('copper-smelting-start', {thing('mining-drill', {drop_target=copper}), copper, pole})
        npc = storage.npcs[1]
        npc.design_history = {iron_start, red, labs, steel_line, copper_start}
    """)
    return lua


class RetireTests(unittest.TestCase):
    def test_what_still_feeds_a_kept_build_stays_and_the_rest_comes_down(self):
        lua = runtime()
        lua.execute("DS.retire(npc, {'iron-smelting-start', 'red-science-automation', 'research-lab', "
                    "'copper-smelting-start'}, 100)")
        stays = [p.purpose for p in lua.eval("npc.design_history").values()]
        # the first furnace still feeds the steel furnace: needed, so it stays
        self.assertEqual(stays, ["iron-smelting-start", "steel-smelting"])
        self.assertEqual(sorted(lua.eval("npc.retired").keys()),
                         ["copper-smelting-start", "red-science-automation", "research-lab"])
        salvage = {e.unit_number for e in lua.eval("npc.design_salvage").values()}
        self.assertIn(lua.eval("lab.unit_number"), salvage)
        self.assertIn(lua.eval("copper.unit_number"), salvage)
        self.assertNotIn(lua.eval("pole.unit_number"), salvage)      # the steel line uses it too
        self.assertNotIn(lua.eval("furnace.unit_number"), salvage)

    def test_one_bus_stage_retires_without_its_purpose(self):
        lua = runtime()
        lua.execute("""
            old_labs = plan('bus-base', {thing('lab'), thing('inserter', {pickup_target=thing('transport-belt')})})
            old_labs.base_plan = {stage='bus-labs'}
            gears = plan('bus-base', {thing('assembling-machine')})
            gears.base_plan = {stage='bus-gears'}
            npc.design_history = {old_labs, gears}
            DS.retire(npc, {'stage:bus-labs'}, 100)
        """)
        stays = [p.base_plan.stage for p in lua.eval("npc.design_history").values()]
        self.assertEqual(stays, ["bus-gears"])
        self.assertIsNone(lua.eval("npc.retired and npc.retired['bus-base']"))   # the bus itself is still built
        self.assertEqual(len(lua.eval("npc.design_salvage")), 2)

    def test_the_sides_of_an_old_wall_ring_that_moved_come_down_and_the_rest_stays(self):
        # (the user, 2026-10-26: a wall ring round the factory, moved out as it grows)
        lua = runtime()
        lua.execute("""
            function side(gen, name)
                local p = plan('plot-wall', {thing('wall')})
                p.keep = DS.keep_copy({ring={-464, -444, -356, -356}, ring_gen=gen, ring_side=name})
                return p
            end
            old_north, old_west, new_north = side(1, 'north'), side(1, 'west'), side(2, 'north')
            npc.design_history = {old_north, old_west, new_north}
            DS.retire(npc, {'ring:1:north'}, 100)
        """)
        stays = [(p.keep.ring_gen, p.keep.ring_side) for p in lua.eval("npc.design_history").values()]
        self.assertEqual(stays, [(1, "west"), (2, "north")])
        self.assertIsNone(lua.eval("npc.retired and npc.retired['plot-wall']"))   # the wall ring is still built
        self.assertEqual(len(lua.eval("npc.design_salvage")), 1)
        # an old corner the new ring runs through stays standing (the side that stayed was built round it)
        lua.execute("""
            function at(kind, x, y) return thing(kind, {position={x=x, y=y}}) end
            corner, middle = at('wall', -461.5, -441.5), at('wall', -420.5, -441.5)
            old = plan('plot-wall', {corner, middle})
            old.keep = DS.keep_copy({ring={-464, -444, -356, -356}, ring_gen=1, ring_side='north'})
            west = plan('plot-wall', {at('wall', -461.5, -420.5)})
            west.keep = DS.keep_copy({ring={-464, -492, -340, -340}, ring_gen=2, ring_side='west'})
            npc.design_history, npc.design_salvage = {old, west}, nil
            DS.retire(npc, {'ring:1:north'}, 200)
        """)
        salvage = {e.unit_number for e in lua.eval("npc.design_salvage").values()}
        self.assertEqual(salvage, {lua.eval("middle.unit_number")})
        # and held by the ring now: rebuilt by it when lost
        owned = [x.entity.unit_number for x in lua.eval("west.entities").values()]
        self.assertIn(lua.eval("corner.unit_number"), owned)
        self.assertEqual(len(lua.eval("west.order")), 2)
        self.assertIsNone(lua.eval("npc.unit_index"))                  # (owners changed: looked up afresh)
        # two partial plans of one side share their ids: each moved piece gets an id of its own
        lua.execute("""
            a, b = at('wall', -461.5, -443.5), at('wall', -460.5, -443.5)   -- (both on the west side's band)
            p1, p2 = plan('plot-wall', {a}), plan('plot-wall', {b})
            for _, p in ipairs({p1, p2}) do
                p.keep = DS.keep_copy({ring={-464, -444, -356, -356}, ring_gen=3, ring_side='north'})
            end
            west.keep.ring_gen = 4
            npc.design_history = {p1, p2, west}
            DS.retire(npc, {'ring:3:north'}, 300)
        """)
        moved = {x.entity.unit_number for x in lua.eval("west.entities").values()}
        self.assertTrue({lua.eval("a.unit_number"), lua.eval("b.unit_number")} <= moved)
        self.assertEqual(len(lua.eval("west.order")), len(moved))
        # the whole plot as the ring: only its outer edges are the team's ring; an old inner side comes down
        whole = lua.table_from({1: -576, 2: -576, 3: -192, 4: -192})
        box = lua.table_from({"box": lua.table_from({1: -576, 2: -576, 3: -192, 4: -192}),
                              "outer": lua.table_from({1: "north", 2: "west"})})
        on = lua.eval("DS.on_ring")
        self.assertTrue(on(whole, lua.table_from({"x": -400.5, "y": -573.5}), box))     # north: the team's ring
        self.assertFalse(on(whole, lua.table_from({"x": -194.5, "y": -400.5}), box))    # east: a teammate's side
        self.assertTrue(on(whole, lua.table_from({"x": -194.5, "y": -400.5})))          # (a ring of its own: all four)
        kept = lua.eval("DS.keep_copy({ring={-464.5, -444, -356, -356}, ring_gen=2.0, ring_side='up'})")
        self.assertEqual((list(kept.ring.values()), kept.ring_gen, kept.ring_side), ([-465, -444, -356, -356], 2, None))
        self.assertIsNone(lua.eval("DS.keep_copy({ring={0, 0, 0, 5}, ring_gen=1})"))   # (no ring there)

    def test_an_old_side_comes_down_but_the_belts_and_pipes_it_took_under_it_keep_running(self):
        # (D-8: a belt or pipe across the wall line goes under it; Codex's review, D-8-crossings: taken down with the
        # old side, a pipe's ends were salvaged and the pipes either side no longer met)
        lua = runtime()
        lua.execute("""
            ends = {thing('underground-belt'), thing('underground-belt'), thing('pipe-to-ground'),
                    thing('pipe-to-ground')}
            wall = thing('wall')
            old = plan('plot-wall', {ends[1], ends[2], ends[3], ends[4], wall})
            old.keep = DS.keep_copy({ring={-464, -444, -356, -356}, ring_gen=1, ring_side='north', extra=10})
            npc.design_history, npc.design_salvage = {old}, nil
            DS.retire(npc, {'ring:1:north'}, 100)
        """)
        self.assertEqual({e.unit_number for e in lua.eval("npc.design_salvage").values()}, {lua.eval("wall.unit_number")})
        self.assertEqual(lua.eval("old.keep.extra"), 10)                       # (kept with the plan for the planner)
        self.assertIsNone(lua.eval("DS.keep_copy({ring={-464, -444, -356, -356}, ring_gen=1, extra=-3}).extra"))
        # with a ring held now, its ends go to it: rebuilt by it when lost
        lua.execute("""
            old = plan('plot-wall', {ends[1], ends[3], wall})
            old.keep = DS.keep_copy({ring={-464, -444, -356, -356}, ring_gen=1, ring_side='north', extra=5, ends=2})
            new = plan('plot-wall', {thing('wall', {position={x=-480.5, y=-470.5}})})
            new.keep = DS.keep_copy({ring={-484, -472, -340, -340}, ring_gen=2, ring_side='north'})
            npc.design_history, npc.design_salvage = {old, new}, nil
            DS.retire(npc, {'ring:1:north'}, 200)
        """)
        owned = {x.entity.unit_number for x in lua.eval("new.entities").values()}
        self.assertTrue({lua.eval("ends[1].unit_number"), lua.eval("ends[3].unit_number")} <= owned)
        self.assertEqual(lua.eval("old.keep.ends"), 2)
        self.assertEqual((lua.eval("new.keep.extra"), lua.eval("new.keep.ends")), (2, 2))   # (no walls of its line)

    def test_retiring_nothing_wanted_changes_nothing(self):
        lua = runtime()
        lua.execute("DS.retire(npc, {'oil-processing'}, 100)")
        self.assertEqual(len(lua.eval("npc.design_history")), 5)
        self.assertIsNone(lua.eval("npc.retired"))


class RebuildTests(unittest.TestCase):
    def test_a_destroyed_building_is_put_back_once_it_is_calm(self):
        lua = runtime()
        lua.execute("""
            local f = iron_start.entities['iron-smelting-start2']
            f.prototype, f.item, f.world, f.direction = 'stone-furnace', 'stone-furnace', {x=5, y=5}, 0
            enemies = 0
            npc.character = {valid=true, surface={count_entities_filtered=function() return enemies end},
                             get_main_inventory=function() return {get_item_count=function() return 1 end} end}
            npc.force = 'npc-force'
            furnace.force = 'npc-force'
            DS.on_entity_died({entity=furnace, tick=1000})
            furnace.valid = false
            have = {}
        """)
        self.assertEqual(lua.eval("#npc.rebuild"), 1)
        # straight after the attack: wait
        self.assertIsNone(lua.eval("DS.rebuild_job(npc, 1200)"))
        # calm, but biters still close: wait longer
        lua.execute("enemies = 1")
        self.assertIsNone(lua.eval("DS.rebuild_job(npc, 3000)"))
        lua.execute("enemies = 0")
        out = lua.eval("{DS.rebuild_job(npc, 3000 + 1800)}")
        job, meta = out[1], out[2]
        self.assertEqual((job.kind, job.entity_name, meta.upkeep), ("build", "stone-furnace", "rebuild"))
        lua.execute("""
            rebuilt = thing('furnace')
            npc.design = npc.design or {}
            npc.design_jobs = {j1 = {upkeep='rebuild', plan=iron_start, entity_id='iron-smelting-start2'}}
            DS.on_receipt(npc, {job_id='j1', status='complete', entity=rebuilt}, 5000)
        """)
        self.assertEqual(lua.eval("iron_start.entities['iron-smelting-start2'].entity == rebuilt"), True)
        # how long it stood broken is kept (the gap is what spoils a belt's lanes), though the wait restarted
        self.assertEqual(lua.eval("npc.rebuild_log[1].lost"), 1000)
        self.assertEqual(lua.eval("npc.rebuild_log[1].rebuilt"), 5000)
        self.assertEqual(lua.eval("npc.rebuild_log[1].name"), "stone-furnace")
        self.assertIsNone(lua.eval("DS.rebuild_job(npc, 6000)"))
        self.assertEqual(lua.eval("#npc.rebuild"), 0)


    def test_a_loss_to_biters_calls_an_allied_engineer(self):
        lua = runtime()
        lua.execute("""
            local friends = function() return true end
            npc.force = {name='npc-faction-1', get_friend=friends}
            furnace.force = npc.force
            furnace.position = {x=12, y=-7}
            ally = {id=2, name='Bram', design={}, character={valid=true},
                    force={name='npc-faction-2', get_friend=friends}}
            storage.npcs[2] = ally
            biter = {valid=true, force={name='enemy'}}
            DS.on_entity_died({entity=furnace, tick=1000, cause=biter})
        """)
        self.assertEqual(lua.eval("ally.help_request.kind"), "defend")
        self.assertEqual(lua.eval("ally.help_request.npc_id"), 1)
        self.assertEqual(lua.eval("ally.help_request.position.x"), 12)
        # one call per while
        lua.execute("ally.help_request = nil; drill.force = npc.force; drill.position = {x=1, y=1}")
        lua.execute("DS.on_entity_died({entity=drill, tick=2000, cause=biter})")
        self.assertIsNone(lua.eval("ally.help_request"))


class HandSmeltingTests(unittest.TestCase):
    def test_a_furnace_holding_less_than_a_craft_is_one_to_feed_not_one_at_work(self):
        # (the user's live game replayed 2026-10-26: one stone in its brick furnace, two a brick, and it waited for good)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel, defines.inventory.crafter_input = 1, 2
            prototypes.item.coal.fuel_value = 4e6
            prototypes.recipe = {['stone-brick']={energy=3.2, ingredients={{type='item', name='stone', amount=2}}}}
            held = {stone=20}
            npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
            function slots(t)
                return {get_item_count=function(spec) return t[spec.name] or 0 end,
                        is_empty=function() for _, n in pairs(t) do if n > 0 then return false end end return true end,
                        get_contents=function() local o = {} for k, n in pairs(t) do
                            if n > 0 then o[#o + 1] = {name=k, count=n} end end return o end}
            end
            src, fuel, out = {stone=1}, {coal=5}, {}
            furnace = {valid=true, type='furnace', unit_number=7, position={x=3, y=0},
                       get_inventory=function(i) if i == defines.inventory.crafter_input then return slots(src) end end,
                       get_fuel_inventory=function() return slots(fuel) end,
                       get_output_inventory=function() return slots(out) end}
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function(f) if f.type == 'furnace' then return {furnace} end return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            acquire = require('scripts.agent').designed.acquire""")
        job = lua.eval("(acquire(npc, 'stone-brick', 5, 0))")
        self.assertEqual((job.kind, job.mode, job.item, job.count), ("transfer", "deposit", "stone", 10))
        # with two in it, it is at work: two stone make one brick of the five, so it feeds no more yet but waits
        lua.execute("src.stone = 10")
        self.assertEqual(lua.eval("select(2, acquire(npc, 'stone-brick', 5, 0))"), "smelting:stone-brick")
        # out at far work: a furnace at home with five bricks of the twenty is no trip while it still smelts, one once done
        lua.execute("npc.hand_smelters = {furnace}; npc.character.position = {x=300, y=0}; out['stone-brick'] = 5; src.stone = 10")
        self.assertIsNone(lua.eval("(acquire(npc, 'stone-brick', 20, 0))"))
        lua.execute("src.stone = 0")
        job = lua.eval("(acquire(npc, 'stone-brick', 20, 0))")
        self.assertEqual((job.mode, job.item, job.count), ("withdraw", "stone-brick", 5))
        # four furnaces it smelts in by hand already, none to feed: no fifth put down
        lua.execute("""out['stone-brick'] = 0; src.stone = 0; npc.character.position = {x=0, y=0}
            local function stub() return {valid=true, type='furnace', position={x=1, y=1},
                get_output_inventory=function() return slots({}) end} end
            npc.hand_smelters = {stub(), stub(), stub(), stub()}
            furnace.valid, npc.hand_smelter = false, nil""")
        self.assertEqual(lua.eval("select(2, acquire(npc, 'stone-brick', 20, 0))"), "smelting:stone-brick")

    def test_a_furnace_in_a_line_is_never_one_to_smelt_in_by_hand(self):
        # (run 30, 2026-10-25: stone put into an idle furnace of the bus's iron column came out as bricks on its
        # iron lane, and the bus's modules starved)
        lua = runtime()
        lua.execute("""
            game.tick = 5000
            storage.npc_line_furnaces = nil
            near = {}
            surface = {find_entities_filtered=function(f) return f.type == 'inserter' and near or {} end}
            column = {valid=true, unit_number=71, type='furnace', position={x=0, y=0}, surface=surface}
            lone = {valid=true, unit_number=72, type='furnace', position={x=50, y=0},
                    surface={find_entities_filtered=function() return {} end}}
            near[1] = {drop_target=column}""")
        self.assertTrue(lua.eval("DS.in_line(column)"))
        self.assertFalse(lua.eval("DS.in_line(lone)"))
        lua.execute("near[1] = {pickup_target=column}; storage.npc_line_furnaces = nil")
        self.assertTrue(lua.eval("DS.in_line(column)"))                   # (emptied by an inserter: a line too)

    def test_with_nothing_smelted_it_feeds_an_idle_furnace_by_hand_and_takes_the_plates(self):
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel, defines.inventory.crafter_input = 1, 2
            prototypes.item.coal.fuel_value = 4e6
            held = {}
            npc.home, npc.design = {x=0, y=0}, {}
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end,
                                      find_non_colliding_position=function(_, p) return {x=p.x, y=p.y} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            function slots(t)
                return {get_item_count=function(spec) return t[spec.name] or 0 end,
                        is_empty=function() for _, n in pairs(t) do if n > 0 then return false end end return true end,
                        get_contents=function() local o = {} for k, n in pairs(t) do
                            if n > 0 then o[#o + 1] = {name=k, count=n} end end return o end}
            end
            -- every machine of the old base is empty; the raid left this furnace standing near the engineer
            local far = 100
            for _, p in ipairs(npc.design_history) do
                for _, id in ipairs(p.order) do
                    local e = p.entities[id].entity
                    e.position = e.position or {x=far, y=0}; far = far + 10
                    local mine = {}
                    e.get_output_inventory = function() return slots(mine) end
                    e.get_fuel_inventory = function() return slots({}) end
                    e.get_inventory = function() return slots({}) end
                end
            end
            src, fuel, out = {}, {}, {}
            furnace.position = {x=3, y=0}
            furnace.get_inventory = function(i) if i == defines.inventory.crafter_input then return slots(src) end end
            furnace.get_fuel_inventory = function() return slots(fuel) end
            furnace.get_output_inventory = function() return slots(out) end
            acquire = require('scripts.agent').designed.acquire
            function want() local r = {acquire(npc, 'iron-plate', 20, 0)} return r[1], r[2] end
            function first() return (want()) end
        """)
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.item), ("mine", "coal"))                   # fuel first
        lua.execute("held.coal = 10")
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.mode, job.item, job.inventory), ("transfer", "deposit", "coal", 1))
        self.assertTrue(lua.eval("select(1, want()).target == furnace"))           # the furnace near it
        lua.execute("fuel.coal = 10; held.coal = 0")
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.item), ("mine", "iron-ore"))               # then ore, mined by hand
        lua.execute("held['iron-ore'] = 20")
        job = lua.eval("first()")
        self.assertEqual((job.mode, job.item, job.count, job.inventory), ("deposit", "iron-ore", 20, 2))
        self.assertTrue(lua.eval("select(1, want()).target == furnace"))
        lua.execute("src['iron-ore'] = 20; held['iron-ore'] = 0")
        self.assertEqual(lua.eval("select(2, want())"), "smelting:iron-plate")      # it waits while it smelts
        lua.execute("src['iron-ore'] = 0; out['iron-plate'] = 20")
        job = lua.eval("first()")
        self.assertEqual((job.mode, job.item, job.count), ("withdraw", "iron-plate", 20))   # and takes the plates
        # a raid cut the coal belt: a furnace still holding ore only needs coal, even if it is farther
        lua.execute("""
            out['iron-plate'] = 0; fuel.coal = 0; held.coal = 10; npc.hand_smelter = nil
            local ore, none = {['iron-ore']=8}, {}
            steel.get_inventory = function(i) return slots(ore) end
            steel.get_fuel_inventory = function() return slots(none) end
        """)
        self.assertTrue(lua.eval("select(1, want()).target == steel"))
        self.assertEqual(lua.eval("select(1, want()).item"), "coal")
        # every furnace of its own lost: it makes one from stone and puts it down beside itself
        lua.execute("""
            for _, p in ipairs(npc.design_history) do
                for _, id in ipairs(p.order) do local e = p.entities[id].entity if e.type == 'furnace' then e.valid = false end end
            end
            npc.hand_smelter = nil
            npc.force.recipes = {['stone-furnace']={name='stone-furnace', enabled=true, category='crafting', energy=0.5,
                products={{name='stone-furnace', amount=1}}, ingredients={{type='item', name='stone', amount=5}}}}
            npc.character.prototype = {crafting_categories={crafting=true}}
        """)
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.item), ("mine", "stone"))
        lua.execute("held.stone = 5")
        self.assertEqual(lua.eval("first().kind"), "craft")
        lua.execute("held['stone-furnace'] = 1")
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.entity_name, job.position.x), ("build", "stone-furnace", 3))
        # out at a far build site (a turret group on its plot's edge): it goes home to put it down
        lua.execute("npc.character.position = {x=255, y=0}")
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.position.x, lua.eval("select(3, acquire(npc, 'iron-plate', 20, 0))")),
                         ("move", 0, "return_home"))


class FuelTests(unittest.TestCase):
    def test_a_cold_burner_gets_a_handful_now_and_a_running_one_a_full_trip(self):
        # (live game 2026-10-22: each new drill and furnace waited for 49 hand-mined coal, one after another)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel = 1
            prototypes.item.coal.fuel_value = 4e6
            held, fuel = {}, {}
            npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            drill = {valid=true, type='mining-drill', position={x=5, y=0},
                     get_fuel_inventory=function() return {get_item_count=function(spec) return fuel[spec.name] or 0 end} end}
            supply = {entity_id='d2', item='coal', slot='fuel'}
            function job() game.tick = game.tick + 1; return DS.supply_job(npc, {entity=drill}, supply) end""")
        self.assertEqual((lua.eval("job().kind"), lua.eval("job().item"), lua.eval("job().count")), ("mine", "coal", 10))
        lua.execute("held.coal = 10")
        self.assertEqual((lua.eval("job().mode"), lua.eval("job().count")), ("deposit", 10))     # in it goes, at once
        lua.execute("held.coal = 0; fuel.coal = 3")
        self.assertEqual(lua.eval("job().count"), 47)                 # running low: topped up in one trip
        lua.execute("fuel.coal = 20")
        self.assertIsNone(lua.eval("job()"))

    def test_burners_are_fuelled_by_the_time_it_lasts_and_fuel_in_hand_is_handed_round(self):
        # (one-engineer runs, 2026-10-25: one burner a trip made the trips grow with every burner; about 40
        # kept the engineer fetching coal for an hour)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel = 1
            prototypes.item.coal.fuel_value = 4e6
            held, fuel = {}, {}
            npc.home, npc.design, npc.design_history, npc.fuel_rounds = {x=0, y=0}, {}, {}, nil
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            local function burner(kw)
                return {valid=true, type='mining-drill', position={x=5, y=0},
                        prototype={get_max_energy_usage=function() return kw * 1000 / 60 end},
                        burner={remaining_burning_fuel=0},
                        get_fuel_inventory=function() return {
                            get_item_count=function(spec) return fuel[spec.name] or 0 end,
                            get_contents=function() return {{name='coal', count=fuel.coal or 0}} end} end}
            end
            drill, boiler = burner(150), burner(1800)
            supply = {entity_id='d2', item='coal', slot='fuel'}
            function job(e, urgent, batch) game.tick = game.tick + 1; return DS.supply_job(npc, {entity=e}, supply, urgent, batch) end""")
        # a burner drill: 10 coal last 267 s, past the next trip's 180; 6 last 160 s, so it is due
        lua.execute("fuel.coal = 10")
        self.assertIsNone(lua.eval("job(drill)"))
        lua.execute("fuel.coal = 6")
        # mined by hand, only what keeps it going five minutes (6 more), not its fill (trace 2026-10-26)
        self.assertEqual((lua.eval("job(drill).kind"), lua.eval("job(drill).count")), ("mine", 6))
        # the others due soon come in the same trip, their shares too (as much as one mining job takes)
        self.assertEqual(lua.eval("job(drill, false, function() return 120, 30 end).count"), 30)
        self.assertEqual(lua.eval("job(drill, false, function() return 120, 90 end).count"), 50)
        self.assertEqual((lua.eval("npc.fuel_rounds.coal.fuel"), lua.eval("npc.fuel_rounds.coal.collect")), ("coal", False))
        # while building only what is about to stop: 3 coal (80 s) waits, 1 (27 s) does not
        lua.execute("fuel.coal = 3")
        self.assertIsNone(lua.eval("job(drill, true)"))
        lua.execute("fuel.coal = 1")
        self.assertEqual((lua.eval("job(drill, true).kind"), lua.eval("job(drill, true).count")), ("mine", 11))
        # fuel mined by hand goes round: each burner due its share, one with five minutes in it none
        lua.execute("held.coal = 20; fuel.coal = 6")
        self.assertEqual((lua.eval("job(drill).mode"), lua.eval("job(drill).count")), ("deposit", 6))
        lua.execute("fuel.coal = 15")
        self.assertIsNone(lua.eval("job(drill)"))
        # a round of fuel gathered by hand outlasts ROUND (the gathering itself may take that long), and a round of wood
        # leaves the coal's be: still a share each (Codex's review, thread W-1-fuel)
        lua.execute("game.tick = 9000; fuel.coal = 6; prototypes.item.wood = {fuel_value=2e6}; held.coal = 0")
        self.assertEqual(lua.eval("DS.supply_job(npc, {entity=drill}, {entity_id='d2', item='wood', slot='fuel'}).kind"),
                         "mine")
        lua.execute("held.coal = 20")
        self.assertEqual((lua.eval("job(drill).mode"), lua.eval("job(drill).count")), ("deposit", 6))
        lua.execute("game.tick = 0; npc.fuel_rounds = nil; fuel.coal = 15")
        self.assertIsNone(lua.eval("job(drill)"))
        # a boiler's fill lasts 111 s at full draw: due under a share of it (10 coal: 22 s), not at 20 (44 s)
        lua.execute("held.coal = 0; fuel.coal = 20")
        self.assertIsNone(lua.eval("job(boiler)"))
        lua.execute("fuel.coal = 10")
        self.assertEqual(lua.eval("job(boiler).kind"), "mine")

    def test_with_its_drills_making_the_fuel_it_waits_for_them_rather_than_mine_by_hand(self):
        # (trace 2026-10-26: right after its coal loop stood it mined 47 coal by hand for 94 s, the loop making about 80
        # meanwhile; and it carried one or two coal at a time between a coal chest and the drills filling it)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel, defines.inventory.chest = 1, 2
            prototypes.item.coal.fuel_value = 4e6
            held = {}
            npc.home, npc.design, npc.fuel_rounds = {x=0, y=0}, {}, nil
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            coal_tile = {valid=true, prototype={mineable_properties={products={{type='item', name='coal'}}}}}
            function slots(t) return {
                get_item_count=function(spec) return t[spec.name] or 0 end,
                get_contents=function() return {{name='coal', count=t.coal or 0}} end} end
            local n = 100
            function burner(kind, extra)
                n = n + 1
                local e = {valid=true, type=kind, unit_number=n, position={x=5, y=0}, fuel={},
                           prototype={get_max_energy_usage=function() return 150 * 1000 / 60 end},
                           burner={remaining_burning_fuel=0}}
                e.get_fuel_inventory = function() return slots(e.fuel) end
                for k, v in pairs(extra or {}) do e[k] = v end
                return e
            end
            furnace = burner('furnace')
            supply = {entity_id='f1', item='coal', slot='fuel'}
            function job(e, urgent) game.tick = game.tick + 1; return flat(DS.supply_job(npc, {entity=e}, supply, urgent)) end
            npc.design_history = {}""")
        # no drills of its own on coal: mined by hand (its share)
        lua.execute("furnace.fuel.coal = 6")
        self.assertEqual((lua.eval("job(furnace).kind"), lua.eval("job(furnace).count")), ("mine", 6))
        # a coal loop stands, short of spare this moment: the furnace (160 s left) waits for it
        lua.execute("""
            a, b = burner('mining-drill', {mining_target=coal_tile}), burner('mining-drill', {mining_target=coal_tile})
            a.drop_target, b.drop_target = b, a
            a.fuel.coal, b.fuel.coal = 9, 12
            npc.design_history = {plan('coal-loop', {a, b})}
            npc.fed_stores = nil   -- (looked over every 10 s)""")
        self.assertIsNone(lua.eval("(job(furnace))"))
        # about to stop (1 coal, 27 s): never coal mined by hand once a loop stands, whatever is short (the user,
        # 2026-10-26), and no trip for the loop's five: it waits for a trip's worth
        lua.execute("furnace.fuel.coal = 1; npc.fed_stores = nil")
        self.assertIsNone(lua.eval("(job(furnace))"))
        lua.execute("b.fuel.coal = 18")
        # (the fullest drill's ten and the other's one spare in one sweep: W-15)
        self.assertEqual((lua.eval("job(furnace).mode"), lua.eval("job(furnace).count")), ("withdraw", 11))
        lua.execute("b.fuel.coal = 12; npc.fuel_rounds = nil")
        # its stores are looked over every 10 s: a loop gone (mined out, taken down) is seen then, and it mines by hand
        lua.execute("furnace.fuel.coal = 6; saved = npc.design_history; npc.design_history = {}")
        self.assertIsNone(lua.eval("(job(furnace))"))
        lua.execute("game.tick = 2000; storage.burn_seen = nil")   # (fuel set by hand here, not burnt: full draw)
        self.assertEqual(lua.eval("job(furnace).kind"), "mine")
        lua.execute("npc.design_history = saved; npc.fed_stores = nil")
        # with spare in the loop, it comes from there
        lua.execute("furnace.fuel.coal = 6; b.fuel.coal = 40")
        self.assertEqual((lua.eval("job(furnace).mode"), lua.eval("job(furnace).target.unit_number")),
                         ("withdraw", lua.eval("b.unit_number")))
        # a cold drill of the loop (the loop gone cold) gets only a start: its partner keeps it going
        lua.execute("a.fuel.coal = 0; b.fuel.coal = 0")
        self.assertEqual((lua.eval("job(a).kind"), lua.eval("job(a).count")), ("mine", 1))
        # a chest its drills fill with two coal: no trip yet for a furnace that still runs, one when it is about to stop
        lua.execute("""
            chest = {valid=true, type='container', unit_number=200, position={x=8, y=0}, items={coal=2}}
            chest.get_inventory = function() return slots(chest.items) end
            c = burner('mining-drill', {mining_target=coal_tile, drop_target=chest})
            c.fuel.coal = 30
            npc.design_history = {plan('coal-supply', {c, chest})}
            npc.fed_stores = nil
            furnace.fuel.coal = 6""")
        self.assertIsNone(lua.eval("(job(furnace))"))
        lua.execute("furnace.fuel.coal = 1")
        self.assertIsNone(lua.eval("(job(furnace))"))
        # with a trip's worth, the trip
        lua.execute("furnace.fuel.coal = 6; chest.items.coal = 25")
        self.assertEqual((lua.eval("job(furnace).mode"), lua.eval("job(furnace).count")), ("withdraw", 25))
        # the chest and the loop both: the one holding more
        lua.execute("""
            a.fuel.coal, b.fuel.coal = 9, 48
            npc.design_history = {plan('coal-supply', {c, chest}), plan('coal-loop', {a, b})}
            npc.fed_stores = nil""")
        self.assertEqual(lua.eval("job(furnace).target.unit_number"), lua.eval("b.unit_number"))

    def test_once_its_drills_make_coal_none_is_mined_by_hand_whoever_wants_it(self):
        # (the user, 2026-10-26: never coal mined by hand; Codex's review, thread W-1-coal-rule)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel, defines.inventory.chest, defines.inventory.crafter_input = 1, 2, 3
            prototypes.item.coal.fuel_value = 4e6
            prototypes.item.coal.fuel_categories = {'chemical'}
            prototypes.item.wood = {fuel_value=2e6, fuel_categories={'chemical'}}
            prototypes.item.pellet = {fuel_value=1e6, fuel_categories={'nuclear'}}
            held = {}
            npc.home, npc.design, npc.fuel_rounds = {x=0, y=0}, {}, nil
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            coal_tile = {valid=true, prototype={mineable_properties={products={{type='item', name='coal'}}}}}
            function slots(t) return {
                get_item_count=function(spec) return t[spec.name] or 0 end,
                get_contents=function() local o = {} for k, n in pairs(t) do if n > 0 then o[#o+1] = {name=k, count=n} end end return o end,
                can_insert=function(spec) for k, n in pairs(t) do if n > 0 and k ~= spec.name then return false end end return true end} end
            local n = 100
            function burner(kind, extra)
                n = n + 1
                local e = {valid=true, type=kind, unit_number=n, position={x=5, y=0}, fuel={},
                           prototype={get_max_energy_usage=function() return 150 * 1000 / 60 end},
                           burner={remaining_burning_fuel=0, fuel_categories={chemical=true}}}
                e.get_fuel_inventory = function() return slots(e.fuel) end
                e.get_output_inventory = function() return slots({}) end
                for k, v in pairs(extra or {}) do e[k] = v end
                return e
            end
            a, b = burner('mining-drill', {mining_target=coal_tile}), burner('mining-drill', {mining_target=coal_tile})
            a.drop_target, b.drop_target = b, a
            a.fuel.coal, b.fuel.coal = 8, 6
            furnace = burner('furnace')
            npc.design_history = {plan('coal-loop', {a, b}), plan('smelting', {furnace})}
            supply = {entity_id='x', item='coal', slot='fuel'}
            function job(e) game.tick = game.tick + 1; return DS.supply_job(npc, {entity=e}, supply) end""")
        # a loop drill still running low (6 coal) and nothing spare: no hand-mining, its partner feeds it
        self.assertIsNone(lua.eval("(job(b))"))
        # coal another machine takes (a plastic plant's input): from the loop or not at all
        lua.execute("""
            input = {}
            plant = {valid=true, type='assembling-machine', unit_number=300, position={x=5, y=0},
                     get_recipe=function() return {ingredients={{type='item', name='coal', amount=1}}, products={}} end,
                     get_output_inventory=function() return {get_item_count=function() return 0 end} end,
                     get_inventory=function() return slots(input) end}""")
        got = lua.eval("{DS.supply_job(npc, {entity=plant}, {entity_id='p', item='coal', slot='input'})}")
        self.assertIsNone(got[1])
        self.assertEqual(got[2], "store_filling:coal")
        # a cold furnace takes a short store (five, above GIVE): production stopped may leave it short for good
        lua.execute("a.fuel.coal = 13")
        self.assertEqual((lua.eval("job(furnace).mode"), lua.eval("job(furnace).count")), ("withdraw", 5))
        # wood it carries (for poles) goes into no burner once a loop stands; a fuel the burner cannot burn never does
        lua.execute("held.wood, held.pellet = 20, 5")
        self.assertIsNone(lua.eval("(DS.fuel_in_hand(npc, furnace, furnace.get_fuel_inventory(), 'coal'))"))
        self.assertFalse(lua.eval("DS.takes_fuel(furnace, furnace.get_fuel_inventory(), 'pellet')"))
        # a round's share is counted off by what went in: a deposit that failed leaves it owed
        lua.execute("""
            npc.fuel_rounds = {coal={fuel='coal', split={[furnace.unit_number]=7}}}
            npc.design_jobs = {j1={upkeep='supply', entity_id='x', item='coal'}, j2={upkeep='supply', entity_id='x', item='coal'}}
            DS.on_receipt(npc, {job_id='j1', kind='transfer', mode='deposit', status='failed', count=0,
                                target_unit_number=furnace.unit_number}, 10)""")
        self.assertEqual(lua.eval("npc.fuel_rounds.coal.split[furnace.unit_number]"), 7)
        lua.execute("DS.on_receipt(npc, {job_id='j2', kind='transfer', mode='deposit', status='complete', count=4, "
                    "target_unit_number=furnace.unit_number}, 11)")
        self.assertEqual(lua.eval("npc.fuel_rounds.coal.split[furnace.unit_number]"), 3)
        # its loop all run dry: nothing is coming, so coal is gathered by hand again (no store waited on for good)
        lua.execute("a.fuel.coal, b.fuel.coal = 0, 0; npc.fed_stores = nil")
        self.assertEqual(lua.eval("DS.supply_job(npc, {entity=plant}, {entity_id='p', item='coal', slot='input'}).kind"),
                         "mine")

    def test_what_its_inserters_feed_from_a_belt_is_fed_by_hand_only_once_it_runs_out(self):
        # (run 57, NPC Factory's analysis 2026-10-26: a belt-fed boiler row 210 tiles out, its coal drills backed up, was
        # read as low with the dozen coal an inserter keeps in each boiler, and topped up by hand)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel, defines.inventory.crafter_input = 1, 3
            prototypes.item.coal.fuel_value = 4e6
            held = {}
            npc.home, npc.design, npc.fuel_rounds = {x=0, y=0}, {}, nil
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            function slots(t) return {
                get_item_count=function(spec) return t[spec.name] or 0 end,
                get_contents=function() local o = {} for k, n in pairs(t) do if n > 0 then o[#o+1] = {name=k, count=n} end end return o end} end
            on_belt = {coal=30, ['iron-gear-wheel']=0}
            belt = {valid=true, type='transport-belt', get_max_transport_line_index=function() return 2 end,
                    get_transport_line=function() return {get_item_count=function(item) return (on_belt[item] or 0) / 2 end} end}
            fuel = {coal=12}
            boiler = {valid=true, type='boiler', unit_number=50, position={x=200, y=0},
                      prototype={get_max_energy_usage=function() return 1800 * 1000 / 60 end},
                      burner={remaining_burning_fuel=0}, get_fuel_inventory=function() return slots(fuel) end}
            arm = {valid=true, type='inserter', drop_target=boiler, pickup_target=belt}
            npc.design_history = {plan('power-row', {belt, arm, boiler})}
            supply = {entity_id='b', item='coal', slot='fuel'}
            function job() game.tick = game.tick + 1; return DS.supply_job(npc, {entity=boiler}, supply) end""")
        # twelve coal (27 s at full draw) and coal on the belt it takes from: no trip
        self.assertIsNone(lua.eval("(job())"))
        # but not from an arm that cannot bring it: its filter keeps coal out, it has no power, it was turned elsewhere
        # (Codex's review, thread W-5-auto-fed)
        lua.execute("""
            defines.entity_status = {no_power=7, working=1}
            arm.use_filters, arm.inserter_filter_mode, arm.filter_slot_count = true, 'whitelist', 1
            arm.get_filter = function() return {name='stone'} end""")
        self.assertIsNotNone(lua.eval("(job())"))
        lua.execute("arm.use_filters = false; arm.status = 7")
        self.assertIsNotNone(lua.eval("(job())"))
        lua.execute("arm.status = 1; arm.drop_target = {valid=true}")
        self.assertIsNotNone(lua.eval("(job())"))
        lua.execute("arm.drop_target = boiler; npc.fuel_rounds = nil")
        self.assertIsNone(lua.eval("(job())"))
        # the belt empty: the boiler is fed as any other
        lua.execute("on_belt.coal = 0")
        self.assertIsNotNone(lua.eval("(job())"))
        # cold, coal on the belt or not (a blackout stops the inserter): fed by hand
        lua.execute("on_belt.coal = 30; fuel.coal = 0")
        self.assertIsNotNone(lua.eval("(job())"))
        # an assembler its inserter feeds gears from that belt: by hand only once it has none
        lua.execute("""
            input = {['iron-gear-wheel']=3}
            on_belt['iron-gear-wheel'] = 20
            asm = {valid=true, type='assembling-machine', unit_number=60, position={x=200, y=4},
                   get_recipe=function() return {ingredients={{type='item', name='iron-gear-wheel', amount=1}}, products={}} end,
                   get_output_inventory=function() return slots({}) end,
                   get_inventory=function() return slots(input) end}
            npc.design_history = {plan('power-row', {belt, arm, boiler}), plan('science', {{valid=true, type='inserter', drop_target=asm, pickup_target=belt}, asm})}
            npc.fed_stores = nil
            gears = {entity_id='a', item='iron-gear-wheel', slot='input'}""")
        lua.execute("function asks() local j, why = DS.supply_job(npc, {entity=asm}, gears) return j ~= nil or why ~= nil end")
        self.assertFalse(lua.eval("asks()"))
        lua.execute("input['iron-gear-wheel'] = 0")
        self.assertTrue(lua.eval("asks()"))     # (it sets about getting some: a job, or what it waits on)

    def test_a_boiler_waiting_for_its_own_coal_line_is_not_fed_by_hand_while_power_is_not_short(self):
        # (run 60, 2026-10-26: four new bus boilers fed by hand for an hour while their coal line was built and then broken,
        # the base never short of power)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel = 1
            prototypes.item.coal.fuel_value = 4e6
            held = {}
            npc.home, npc.design, npc.fuel_rounds = {x=0, y=0}, {}, nil
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            function slots(t) return {
                get_item_count=function(spec) return t[spec.name] or 0 end,
                get_contents=function() local o = {} for k, n in pairs(t) do if n > 0 then o[#o+1] = {name=k, count=n} end end return o end} end
            belt = {valid=true, type='transport-belt', get_max_transport_line_index=function() return 2 end,
                    get_transport_line=function() return {get_item_count=function() return 0 end} end}
            local function boiler(n, x, burning)
                local fuel = {coal=10}
                return {valid=true, type='boiler', unit_number=n, position={x=x, y=0},
                        prototype={get_max_energy_usage=function() return 1800 * 1000 / 60 end},
                        burner={remaining_burning_fuel=burning}, get_fuel_inventory=function() return slots(fuel) end}
            end
            new, old = boiler(70, 200, 1e6), boiler(71, 5, 2e6)
            function engines(net) return {find_entities_filtered=function() return {{electric_network_id=net}} end} end
            new.surface, old.surface = engines(1), engines(1)   -- (their steam engines on one network)
            arm = {valid=true, type='inserter', drop_target=new, pickup_target=belt}
            npc.design_history = {plan('bus-power', {belt, arm, new}), plan('steam-power', {old})}
            supply = {entity_id='n', item='coal', slot='fuel'}
            function job() game.tick = game.tick + 1; return DS.supply_job(npc, {entity=new}, supply) end""")
        # its belt empty, another boiler of its own burning, power not short: it waits for its coal line
        self.assertIsNone(lua.eval("(job())"))
        # not if its inserter could not bring the coal (a burner inserter out of fuel), nor if the boiler burning is on
        # another power network (Codex's review, thread W-7-boilers-forest)
        lua.execute("defines.entity_status = {no_fuel=5}; arm.status = 5")
        self.assertIsNotNone(lua.eval("(job())"))
        lua.execute("arm.status = nil; old.surface = engines(2)")
        self.assertIsNotNone(lua.eval("(job())"))
        # nor where it cannot tell its network (no steam engine of its own found by it)
        lua.execute("old.surface = engines(1); new.surface = {find_entities_filtered=function() return {} end}")
        self.assertIsNotNone(lua.eval("(job())"))
        lua.execute("new.surface = engines(1)")
        self.assertIsNone(lua.eval("(job())"))
        # short of power: fed by hand
        lua.execute("npc.power_short = game.tick")
        self.assertIsNotNone(lua.eval("(job())"))
        # no other boiler burning (a blackout): fed by hand
        lua.execute("npc.power_short = nil; old.burner.remaining_burning_fuel = 0; old.get_fuel_inventory = function() return slots({}) end")
        self.assertIsNotNone(lua.eval("(job())"))
        # a boiler with no inserter of its own (fed by hand by design): fed as ever
        lua.execute("old.burner.remaining_burning_fuel = 2e6; arm.drop_target = {valid=true}; npc.fed_stores = nil")
        self.assertIsNotNone(lua.eval("(job())"))

    def test_a_round_collects_what_the_loop_has_and_splits_it_lowest_first(self):
        # (the user, 2026-10-26: every now and then what the loop has is taken and split evenly over all furnaces, drills
        # and boilers; it takes a while to reach a full stack and does not need to; wood in the first furnace and drill)
        lua = runtime()
        lua.execute("""
            defines.inventory.fuel, defines.inventory.chest = 1, 2
            prototypes.item.coal.fuel_value = 4e6
            prototypes.item.wood = {fuel_value=2e6}
            held, trees = {}, 1
            npc.home, npc.design, npc.fuel_rounds, npc.design_history = {x=0, y=0}, {}, nil, {}
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function(f) if f.type == 'tree' then return trees end return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            coal_tile = {valid=true, prototype={mineable_properties={products={{type='item', name='coal'}}}}}
            function slots(t) return {
                get_item_count=function(spec) return t[spec.name] or 0 end,
                get_contents=function() local o = {} for k, n in pairs(t) do if n > 0 then o[#o+1] = {name=k, count=n} end end return o end,
                can_insert=function(spec) for k, n in pairs(t) do if n > 0 and k ~= spec.name then return false end end return true end} end
            local n = 100
            function burner(kind, extra)
                n = n + 1
                local e = {valid=true, type=kind, unit_number=n, position={x=5, y=0}, fuel={},
                           prototype={get_max_energy_usage=function() return 150 * 1000 / 60 end},
                           burner={remaining_burning_fuel=0}}
                e.get_fuel_inventory = function() return slots(e.fuel) end
                for k, v in pairs(extra or {}) do e[k] = v end
                return e
            end
            f1, f2, f3 = burner('furnace'), burner('furnace'), burner('furnace')
            supply = {entity_id='f1', item='coal', slot='fuel'}
            function rows()
                local out = {}
                for _, f in ipairs({f1, f2, f3}) do
                    local left, full = DS.fuel_time(f, f.get_fuel_inventory(), 'coal')
                    out[#out+1] = {key=f.unit_number, left=left, rate=150000, cap=full}
                end
                return out
            end
            function batch() return 120, 20, rows() end
            function job(e) game.tick = game.tick + 1; return DS.supply_job(npc, {entity=e}, supply, false, batch) end""")
        # before any loop stands: wood, gathered for now (a cold furnace's start, ten coal's worth)
        self.assertEqual((lua.eval("job(f1).kind"), lua.eval("job(f1).item"), lua.eval("job(f1).count")), ("mine", "wood", 20))
        lua.execute("trees = 0")   # (no trees near: coal)
        self.assertEqual(lua.eval("job(f1).item"), "coal")
        # wood in hand goes in; the slot then takes no coal until it is burnt
        lua.execute("trees = 1; held.wood = 20; npc.fuel_rounds = nil")
        self.assertEqual((lua.eval("job(f1).item"), lua.eval("job(f1).mode")), ("wood", "deposit"))
        lua.execute("held.wood = 0; f1.fuel.wood = 3; npc.fuel_rounds = nil")
        self.assertIsNone(lua.eval("(job(f1))"))
        # a loop stands, with 20 and 15 spare: the round collects from one drill after the other, then splits
        lua.execute("""
            f1.fuel = {coal=1}; f2.fuel = {coal=4}; f3.fuel = {coal=6}
            a, b = burner('mining-drill', {mining_target=coal_tile}), burner('mining-drill', {mining_target=coal_tile})
            a.drop_target, b.drop_target = b, a
            a.fuel.coal, b.fuel.coal = 23, 28
            npc.design_history = {plan('coal-loop', {a, b})}
            npc.fed_stores = nil""")
        # (W-15: from both drills in one sweep, as a player's ctrl-drag over the loop: the fullest first)
        first = lua.eval("(job(f1))")
        self.assertEqual((first.kind, first.mode), ("sweep", "withdraw"))
        stops = [(lua.eval(f"(job(f1)).entries[{i}].target.unit_number"), lua.eval(f"(job(f1)).entries[{i}].count"))
                 for i in (1, 2)]
        self.assertEqual(stops, [(lua.eval("b.unit_number"), 20), (lua.eval("a.unit_number"), 15)])
        # nothing left to collect: split, the lowest first, all toward the same seconds of running
        lua.execute("held.coal = 35; a.fuel.coal = 8; b.fuel.coal = 8")   # (what the sweep took)
        give = lua.eval("(job(f1))")
        self.assertEqual(give.mode, "deposit")              # (f1 had one coal: its share goes in first)
        split = lua.eval("npc.fuel_rounds.coal.split")
        keys = {f: lua.eval(f + ".unit_number") for f in ("f1", "f2", "f3")}
        shares = {f: split[keys[f]] or 0 for f in keys}
        self.assertEqual(sum(shares.values()), 35)
        self.assertEqual(give.count, shares["f1"])
        after = [shares[f] + lua.eval(f + ".fuel.coal") for f in keys]
        self.assertLessEqual(max(after) - min(after), 2)    # (each brought to about the same, 15 or so)

    def test_what_it_makes_for_a_machine_goes_in_a_handful_at_a_time(self):
        # (one-engineer hour, 2026-10-24: a lab waited 5 minutes of hand crafting for its first 60 packs; the
        # user: "put them in as it crafts it, or inbetween tasks", not wait until it has 60)
        lua = runtime()
        lua.execute("""
            defines.inventory.crafter_input = 2
            held, input = {}, {}
            npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            furnace = {valid=true, type='furnace', position={x=5, y=0},
                       get_inventory=function() return {get_item_count=function(spec) return input[spec.name] or 0 end} end}
            supply = {entity_id='f1', item='iron-ore', slot='input'}
            function job() return DS.supply_job(npc, {entity=furnace}, supply) end""")
        # what is mined or smelted is fetched ready, in one trip from a store; but mined by hand, a handful (a bootstrap
        # furnace's 60 hand-mined ore for the five plates its stage waited on took two minutes, trace 2026-10-26)
        self.assertEqual((lua.eval("job().kind"), lua.eval("job().item"), lua.eval("job().count")), ("mine", "iron-ore", 10))
        lua.execute("held['iron-ore'] = 37")
        self.assertEqual((lua.eval("job().mode"), lua.eval("job().count")), ("deposit", 37))   # what it carries: all at once
        # what it crafts for a machine (science packs for a lab): a handful at a time
        lua.execute("""
            held['iron-ore'] = 0
            defines.inventory.lab_input = 3
            local R = require('scripts.recipes')
            R.hand = function() return {} end
            R.for_item = function() return {name='automation-science-pack', ingredients={}, energy=5} end
            R.made_in = function() return true end
            R.yield = function() return 1 end
            lab = {valid=true, type='lab', position={x=5, y=0},
                   get_inventory=function() return {get_item_count=function(spec) return input[spec.name] or 0 end} end}
            supply = {entity_id='l1', item='automation-science-pack', slot='input'}
            function job() return DS.supply_job(npc, {entity=lab}, supply) end""")
        self.assertEqual((lua.eval("job().kind"), lua.eval("job().count")), ("craft", 10))
        lua.execute("input['automation-science-pack'] = 4")
        self.assertEqual(lua.eval("job().count"), 10)                 # low again: the next handful, not the whole fill
        # never below what one craft takes: a recipe of 50 stone fed ten at a time would never start (Codex's review)
        lua.execute("""
            input = {stone=10}
            assembler = {valid=true, type='assembling-machine', position={x=5, y=0},
                         get_recipe=function() return {ingredients={{type='item', name='stone', amount=50}}, products={}} end,
                         get_output_inventory=function() return {get_item_count=function() return 0 end} end,
                         get_inventory=function() return {get_item_count=function(spec) return input[spec.name] or 0 end} end}
            supply = {entity_id='a1', item='stone', slot='input'}
            function job() return DS.supply_job(npc, {entity=assembler}, supply) end""")
        self.assertEqual((lua.eval("job().kind"), lua.eval("job().item"), lua.eval("job().count")), ("mine", "stone", 50))
        lua.execute("input.stone = 50")
        self.assertIsNone(lua.eval("(job())"))

    def test_a_machine_fed_by_hand_is_not_fed_while_plenty_of_what_it_makes_is_held(self):
        # (one-engineer hour, 2026-10-24: a reused layout's bootstrap furnace was hand fed mined ore and chopped
        # wood for 9 plates while the engineer carried 228)
        lua = runtime()
        lua.execute("""
            defines.inventory.crafter_input = 2
            held, input = {}, {}
            npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            smelts = {name='iron-plate', products={{type='item', name='iron-plate', amount=1}}}
            furnace = {valid=true, type='furnace', position={x=5, y=0}, get_recipe=function() return smelts end,
                       get_inventory=function() return {get_item_count=function(spec) return input[spec.name] or 0 end} end}
            supply = {entity_id='bootstrap', item='iron-ore', slot='input'}
            function job() return DS.supply_job(npc, {entity=furnace}, supply) end""")
        self.assertEqual(lua.eval("job().kind"), "mine")              # short of plates: fed, ore mined for it
        lua.execute("held['iron-plate'] = 228")
        self.assertIsNone(lua.eval("job()"))                         # plenty held: its work is done
        self.assertTrue(lua.eval("DS.held_plenty(npc, furnace)"))
        lua.execute("held['iron-plate'] = 20")
        self.assertEqual(lua.eval("job().kind"), "mine")              # running short again: fed again

    def test_what_its_own_assemblers_make_is_known_and_collected_ready(self):
        # (a stockpile of walls is collected from the wall row, not crafted by hand, 2026-10-24)
        lua = runtime()
        lua.execute("""
            defines.inventory.chest = 1
            game.tick = 1000
            walls = {name='stone-wall', products={{type='item', name='stone-wall', amount=1}}}
            row = {valid=true, type='assembling-machine', get_recipe=function() return walls end,
                   get_output_inventory=function() return {get_item_count=function() return 0 end} end}
            box = {valid=true, type='container',
                   get_inventory=function() return {get_item_count=function(spec) return spec.name == 'stone-wall' and 37 or 0 end} end}
            npc.design = {status='complete'}
            npc.design_history = {{purpose='wall-automation', order={'a', 'c'}, entities={a={entity=row}, c={entity=box}}, stages={}}}
            npc.machine_made = nil""")
        self.assertTrue(lua.eval("DS.machine_made(npc, 'stone-wall')"))
        self.assertFalse(lua.eval("DS.machine_made(npc, 'firearm-magazine')"))
        self.assertEqual((lua.eval("DS.collect(npc, 'stone-wall', 200).count"),
                          lua.eval("DS.collect(npc, 'stone-wall', 200).mode")), (37, "withdraw"))
        self.assertIsNone(lua.eval("DS.collect(npc, 'firearm-magazine', 10)"))

    def test_a_machine_fed_by_hand_gets_the_spare_of_what_its_machines_make_not_what_it_crafts(self):
        # (one-engineer run 19, 2026-10-25: the first lab was fed hand-crafted red packs long after red
        # science ran by machine, half the engineer's time)
        lua = runtime()
        lua.execute("""
            defines.inventory.lab_input = 3
            game.tick = 1000
            held, input, spare = {}, {}, 0
            packs = {name='automation-science-pack', products={{type='item', name='automation-science-pack', amount=1}}}
            line = {valid=true, type='assembling-machine', get_recipe=function() return packs end,
                    get_output_inventory=function() return {get_item_count=function() return spare end} end}
            npc.home, npc.design = {x=0, y=0}, {status='complete'}
            npc.design_history = {{purpose='red-science-automation', order={'a'}, entities={a={entity=line}}, stages={}}}
            npc.machine_made = nil
            npc.character = {valid=true, position={x=0, y=0},
                             surface={count_entities_filtered=function() return 1 end,
                                      find_entities_filtered=function() return {} end},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            lab = {valid=true, type='lab', position={x=5, y=0},
                   get_inventory=function() return {get_item_count=function(spec) return input[spec.name] or 0 end} end}
            supply = {entity_id='l1', item='automation-science-pack', slot='input'}
            function job() return DS.supply_job(npc, {entity=lab}, supply) end""")
        self.assertIsNone(lua.eval("job()"))                           # none spare: it waits, it does not craft
        lua.execute("spare = 7")
        self.assertEqual((lua.eval("job().mode"), lua.eval("job().count"), lua.eval("job().target == line")),
                         ("withdraw", 7, True))
        lua.execute("held['automation-science-pack'] = 7")
        self.assertEqual(lua.eval("job().mode"), "deposit")

    def test_a_new_attack_sets_the_build_aside_once_and_asks_what_now(self):
        # (urgency was looked at only between goals: a coal build ran 60 minutes and more whatever hit the base)
        lua = runtime()
        lua.execute("""
            game.tick = 100000
            require('scripts.team_world').active = function() return true end
            newest = nil
            require('scripts.team_war').newest = function() return newest end
            npc.design = {status='building', purpose='coal-supply', requested_tick=50000, plan={}}
            npc.paused_design, npc.reconsidered_for, npc.reconsidered_tick = nil, nil, nil
            npc.home = {x=0, y=0}""")
        self.assertFalse(lua.eval("DS.reconsider(npc, 100000)"))            # no attack
        lua.execute("newest = 40000")
        self.assertFalse(lua.eval("DS.reconsider(npc, 100000)"))            # (before this build: weighed already)
        lua.execute("newest = 99000")
        self.assertTrue(lua.eval("DS.reconsider(npc, 100000)"))
        self.assertEqual((lua.eval("npc.paused_design.purpose"), lua.eval("npc.design.purpose"),
                          lua.eval("npc.design.reconsider")), ("coal-supply", "choose", True))
        # back to the build (the planner deferred): the same wave does not ask again, a new one soon after waits
        lua.execute("npc.design = npc.paused_design; npc.paused_design = nil")
        self.assertFalse(lua.eval("DS.reconsider(npc, 100100)"))
        lua.execute("newest = 100050")
        self.assertFalse(lua.eval("DS.reconsider(npc, 100100)"))
        self.assertTrue(lua.eval("DS.reconsider(npc, 100000 + DS.RECONSIDER_EVERY)"))
        lua.execute("npc.design = {status='building', purpose='reinforce', requested_tick=0, plan={}}; npc.paused_design = nil; newest = 900000")
        self.assertFalse(lua.eval("DS.reconsider(npc, 900000)"))           # (a defense going up: no)

    def test_a_plan_being_built_is_starved_below_its_share_of_the_engineers_time(self):
        # (run 20, 2026-10-25: turrets half built for 11 minutes and more while walls were crafted and coal carried)
        lua = runtime()
        lua.execute("""
            npc.design = {status='building'}
            npc.time_use = {since=0, tags={['supply:transfer:coal']=700, ['agent:craft:stone-wall']=200,
                                          ['plan:craft:gun-turret']=100}}""")
        self.assertTrue(lua.eval("DS.build_starved(npc)"))
        lua.execute("npc.time_use.tags['build:build:gun-turret'] = 400")   # (half of it now building)
        self.assertFalse(lua.eval("DS.build_starved(npc)"))
        lua.execute("npc.time_use.tags['build:build:gun-turret'] = 0; npc.design.status = 'complete'")
        self.assertFalse(lua.eval("DS.build_starved(npc)"))                # (nothing being built)

    def test_keeping_a_standing_building_supplied_never_blocks_its_plan_but_one_it_cannot_put_up_does(self):
        # (one-engineer run 2026-10-25: a turret's ammo, which the engineer no longer held, blocked the plan)
        lua = runtime()
        lua.execute("""
            game.tick = 1000
            turret = {valid=true, type='ammo-turret', name='gun-turret'}
            npc.design = {status='building', purpose='plot-turrets', stage_index=1,
                          plan={plan_id='t', purpose='plot-turrets', order={'g1', 'g2'},
                                stages={{id='s', entity_ids={'g1', 'g2'}}},
                                entities={g1={id='g1', entity=turret, failures=0}, g2={id='g2', failures=0}}}}
            npc.design_history = {{purpose='coal-loop', order={}, entities={}, stages={}}}
            npc.design_jobs = {}
            n = 0
            function failed(meta)
                n = n + 1
                npc.design_jobs['j' .. n] = meta
                DS.on_receipt(npc, {job_id='j' .. n, status='failed', reason='source_empty'}, 1000 + n)
            end
            for i = 1, 8 do failed({entity_id='g1', supply=true}) end""")
        self.assertEqual(lua.eval("npc.design.status"), "building")         # its ammo: no block
        lua.execute("for i = 1, 8 do failed({entity_id='g2', build=true}) end")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")          # one it cannot put up: blocked

    def test_a_plan_whose_own_jobs_keep_failing_without_progress_gives_up(self):
        # (run 29, 2026-10-25: a brick block it could not walk to failed 93 build runs in a row, none charged to a
        # building, and stood unbuilt for 50 minutes)
        lua = runtime()
        lua.execute("""
            game.tick = 1000
            npc.design = {status='building', purpose='brick-smelting', stage_index=1,
                          plan={plan_id='b', purpose='brick-smelting', order={'d1'}, stages={{id='s', entity_ids={'d1'}}},
                                entities={d1={id='d1', failures=0}}}}
            npc.design_history, npc.design_jobs = {}, {}
            n = 0
            function run(status, reason, placed, meta)
                n = n + 1
                npc.design_jobs['j' .. n] = meta or {build_run=true}
                DS.on_receipt(npc, {job_id='j' .. n, status=status, reason=reason, placed=placed}, 1000 + n)
            end""")
        lua.execute("for i = 1, DS.MAX_STALLED - 1 do run('failed', 'no_path') end")
        self.assertEqual(lua.eval("npc.design.status"), "building")
        lua.execute("run('complete', nil, {{id='x'}})")                    # (one building put down: progress)
        self.assertIsNone(lua.eval("npc.design.stalled"))
        lua.execute("for i = 1, 5 do run('failed', 'source_empty', nil, {upkeep='supply'}) end")   # (upkeep: not its own)
        self.assertIsNone(lua.eval("npc.design.stalled"))
        lua.execute("for i = 1, DS.MAX_STALLED do run('failed', 'no_path') end")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")

    def test_a_building_refused_for_good_blocks_its_plan_at_once_and_the_ground_is_remembered(self):
        # (run 20, 2026-10-25: a coal belt planned across a cliff was tried six times)
        lua = runtime()
        lua.execute("""
            game.tick = 1000
            npc.design = {status='building', purpose='boiler-coal', stage_index=1,
                          plan={plan_id='c', purpose='boiler-coal', order={'b1'}, stages={{id='s', entity_ids={'b1'}}},
                                entities={b1={id='b1', failures=0}}}}
            npc.design_history, npc.design_jobs = {}, {j1={entity_id='b1', build=true}}
            DS.on_receipt(npc, {job_id='j1', status='failed', reason='placement_blocked_or_out_of_reach',
                                blocked_by='cliff'}, 1001)""")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        import sys
        sys.path.insert(0, str(ROOT))
        from test_clearing import worker
        lua = worker()
        lua.execute("""
            game = {tick=1000}
            me = {name='npc-faction-1'}
            cliff = {type='cliff', force={name='neutral'},
                     bounding_box={left_top={x=-435.2, y=-376}, right_bottom={x=-431, y=-373.5}}}
            rock = {type='simple-entity', force={name='neutral'}}
            W.unbuildable(npc, cliff); W.unbuildable(npc, cliff)""")
        self.assertTrue(lua.eval("W.for_good({force=me}, cliff)"))
        self.assertFalse(lua.eval("W.for_good({force=me}, rock)"))
        self.assertFalse(lua.eval("W.for_good({force=me}, {type='unit-spawner', force={name='enemy'}})"))
        self.assertEqual(lua.eval("#npc.unbuildable"), 1)                 # (once)
        self.assertEqual([lua.eval("npc.unbuildable[1]." + k) for k in ("x0", "y0", "x1", "y1")], [-436, -376, -432, -374])
        # a diagonal cliff's box is turned (run 22's, as the game gave it): the upright box round it, which holds
        # the tile the game refused (-364, -307)
        lua.execute("""diagonal = {type='cliff', force={name='neutral'}, bounding_box={left_top={x=-365.37109375,
            y=-305.80859375}, right_bottom={x=-361.12890625, y=-303.69140625}, orientation=0.125}}
            W.unbuildable(npc, diagonal)""")
        box = [lua.eval("npc.unbuildable[2]." + k) for k in ("x0", "y0", "x1", "y1")]
        self.assertTrue(box[0] <= -364 <= box[2] and box[1] <= -307 <= box[3], box)
        from companion.base_plan import blocked_tiles
        tiles = blocked_tiles({"avoid": [{"x0": -436, "y0": -376, "x1": -432, "y1": -374, "kind": "cliff"}]})
        self.assertIn((-434, -375), tiles)
        self.assertEqual(len(tiles), 15)

    def test_back_after_dying_it_fetches_what_it_carried_from_its_corpse_once_it_is_calm(self):
        # (what it carried lies in its corpse: a death world's walls, bricks and magazines)
        lua = runtime()
        lua.execute("""
            game.tick = 2000
            defines.inventory.character_corpse = 9
            prototypes = prototypes or {}
            prototypes.item = {['stone-wall']={type='item'}, ['firearm-magazine']={type='ammo'}, pistol={type='gun'}}
            contents = {{name='pistol', count=1, quality='normal'}, {name='stone-wall', count=140, quality='normal'}}
            enemies = 0
            corpse = {valid=true, position={x=50, y=60},
                      get_inventory=function() return {is_empty=function() return #contents == 0 end,
                                                       get_contents=function() return contents end} end}
            surface = {find_entities_filtered=function(spec) return spec.type == 'character-corpse' and {corpse} or {} end,
                       count_entities_filtered=function() return enemies end}
            npc.character = {valid=true, surface=surface, position={x=0, y=0}}
            npc.corpse_at = {x=50, y=60, tick=1000}""")
        self.assertEqual((lua.eval("DS.corpse_job(npc, 2000).item"), lua.eval("DS.corpse_job(npc, 2000).count")),
                         ("stone-wall", 140))                               # (not the gun: the worker moves items)
        lua.execute("enemies = 3")
        self.assertIsNone(lua.eval("DS.corpse_job(npc, 2000)"))             # biters by it: not now
        self.assertIsNotNone(lua.eval("npc.corpse_at"))                     # (but later)
        lua.execute("enemies = 0; contents = {{name='pistol', count=1, quality='normal'}}")
        self.assertIsNone(lua.eval("DS.corpse_job(npc, 2000)"))             # nothing left it can carry
        self.assertIsNone(lua.eval("npc.corpse_at"))
        lua.execute("npc.corpse_at = {x=50, y=60, tick=0}; contents = {{name='stone-wall', count=5, quality='normal'}}")
        self.assertIsNone(lua.eval("DS.corpse_job(npc, 200000)"))           # too long ago: given up

    def test_where_its_time_goes_is_tallied_recent_first(self):
        # (a run spent most of its time hand-mining coal for its burners, and nothing knew)
        lua = runtime()
        lua.execute("""
            npc.time_use = nil
            DS.tally(npc, {upkeep='supply'}, {kind='mine', item='coal', started_tick=0, tick=3000})
            DS.tally(npc, {build=true}, {kind='build', item='stone-furnace', started_tick=3000, tick=4000})
            DS.tally(npc, {upkeep='supply'}, {kind='mine', item='coal', started_tick=4000, tick=5000})
            used = DS.time_use(npc)""")
        self.assertEqual(lua.eval("used.shares['supply:mine:coal']"), 0.8)
        self.assertEqual(lua.eval("used.shares['build:build:stone-furnace']"), 0.2)
        self.assertEqual(lua.eval("used.seconds"), 83)
        # ten minutes on, the old counts weigh half: the recent counts most
        lua.execute("DS.tally(npc, {upkeep='collect'}, {kind='transfer', item='iron-plate', started_tick=41000, "
                    "tick=43000}); used = DS.time_use(npc)")
        self.assertAlmostEqual(lua.eval("used.shares['collect:transfer:iron-plate']"), 2000 / 4500, places=2)
        lua.execute("DS.tally(npc, nil, {kind='mine', started_tick=10, tick=5})")   # (nonsense: ignored)

    def test_a_failure_is_noted_under_the_goal_the_planner_names(self):
        # (a request for a goal of its own choosing says "choose": the goal that failed was never noted and was
        # chosen again and again, one-engineer run 2026-10-24)
        lua = runtime()
        lua.execute("""
            game.tick = 5000
            npc.design = {status='waiting', purpose='choose', request_id='1:9'}
            npc.design_history = {{purpose='coal-loop', order={}, entities={}, stages={}}}
            npc.design_failures = nil
            storage.npcs = {npc}
            DS.fail({request_id='1:9', reason='PlanError: no native layout', purpose='power-expansion'})""")
        self.assertEqual(lua.eval("npc.design_failures['power-expansion'].count"), 1)
        self.assertIsNone(lua.eval("npc.design_failures['choose']"))


class FirstScoutTests(unittest.TestCase):
    def test_an_engineer_with_nothing_built_walks_its_scouting_trip_and_then_asks_again(self):
        # (Factorio 2.1 map, 2026-10-23: its first plan sent it looking for iron; it never walked)
        lua = runtime()
        lua.execute("""
            local ground = {count_entities_filtered=function() return 0 end,
                            find_entities_filtered=function() return {} end,
                            find_non_colliding_position=function(_, p) return {x=p.x, y=p.y} end}
            npc.home, npc.design_history, npc.known_patches = {x=0, y=0}, {}, {}
            npc.design = {status='deferred', purpose='iron-smelting-start', goal='Start iron plates.',
                          request_id='1:1'}
            npc.character = {valid=true, position={x=0, y=0}, surface=ground,
                             get_main_inventory=function() return {get_item_count=function() return 0 end,
                                                                   get_contents=function() return {} end} end}
            npc.scout = {resource='iron-ore', purpose='iron-smelting-start', window=32,
                         points={{x=56, y=0}, {x=0, y=56}}, index=1, started=0}""")
        job = lua.eval("(DS.upkeep(npc, 100))")
        self.assertEqual((job.kind, job.position.x), ("move", 56))
        lua.execute("""npc.known_patches['iron-ore:3:0'] = {resource='iron-ore', x=60, y=4, tiles=40, amount=90000,
                                                           tick=100, surface_index=1}""")
        lua.eval("DS.upkeep(npc, 200)")
        self.assertEqual((lua.eval("npc.design.status"), lua.eval("npc.design.purpose")), ("waiting", "iron-smelting-start"))
        self.assertIsNone(lua.eval("npc.scout"))


class ResearchWakeTests(unittest.TestCase):
    def test_in_the_team_world_a_finished_tech_wakes_only_the_engineer_it_unblocks(self):
        # (rehearsal 2026-10-23: every tech woke all seven strategists, nearly all to say "research next")
        lua = runtime()
        lua.execute("""
            Agent = require('scripts.agent')
            storage.agent_bridge_tick = game.tick
            for id = 1, 3 do storage.npcs[id].design = {} end
            storage.npcs[1].phase = 'designed-start:need:recipe_locked:gun-turret'
            storage.npcs[2].phase = 'designed-start:building:s1'
            prototypes.recipe = {['gun-turret']={products={{type='item', name='gun-turret'}}}}
            settings.global['npc-factions-scenario'] = {value='team'}
            function finish(force)
                for id = 1, 3 do storage.npcs[id].agent = nil end
                Agent.on_research_finished({research={valid=true, name='gun-turret', force=force,
                    prototype={effects={{type='unlock-recipe', recipe='gun-turret'}}}}})
                local woke = {}
                for id = 1, 3 do
                    local a = storage.npcs[id].agent
                    if a and #a.events > 0 then woke[#woke + 1] = id end
                end
                return table.concat(woke, ',')
            end""")
        self.assertEqual(lua.eval("finish(storage.founding_force)"), "1")   # only the one waiting on turrets
        lua.execute("settings.global['npc-factions-scenario'] = {value='factions'}")
        self.assertEqual(lua.eval("finish(storage.npcs[2].force)"), "2")    # other games: its own force's tech


class ModdedRecipeTests(unittest.TestCase):
    def test_an_item_made_by_a_recipe_of_another_name_is_crafted_by_that_recipe(self):
        # Bob's Warfare: a magazine takes an empty magazine, made by "empty-recipe"; Quality's
        # recycling also lists empty magazines among its products, and is no way to make one.
        lua = runtime()
        lua.execute("""
            held = {['iron-plate']=100}
            npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
            npc.character = {valid=true, position={x=0, y=0}, prototype={crafting_categories={crafting=true}},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            local function recipe(name, ingredients, products, extra)
                local r = {name=name, enabled=true, category='crafting', energy=1, ingredients=ingredients,
                           products=products}
                for k, v in pairs(extra or {}) do r[k] = v end
                npc.force.recipes[name] = r
            end
            npc.force.recipes = {}
            recipe('empty-recipe', {{type='item', name='iron-plate', amount=2}},
                   {{type='item', name='empty-magazine', amount=1}})
            recipe('firearm-magazine', {{type='item', name='empty-magazine', amount=1},
                                        {type='item', name='iron-plate', amount=2}},
                   {{type='item', name='firearm-magazine', amount=1}})
            recipe('firearm-magazine-recycling', {{type='item', name='firearm-magazine', amount=1}},
                   {{type='item', name='empty-magazine', amount=0.25}}, {category='recycling', hidden=true})
            acquire = require('scripts.agent').designed.acquire
            function first() return (acquire(npc, 'firearm-magazine', 10, 0)) end
        """)
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.recipe, job.count), ("craft", "empty-recipe", 10))
        lua.execute("held['empty-magazine'] = 10")
        job = lua.eval("first()")
        self.assertEqual((job.kind, job.recipe, job.count), ("craft", "firearm-magazine", 10))
        lua.execute("npc.force.recipes['empty-recipe'].enabled = false; held['empty-magazine'] = 0")
        self.assertEqual(lua.eval("select(2, acquire(npc, 'firearm-magazine', 10, 0))"),
                         "recipe_locked:empty-magazine")


class TurretAmmoTests(unittest.TestCase):
    def test_a_turret_gets_the_best_ammo_held_else_the_best_it_can_make_that_fits(self):
        lua = runtime()
        lua.execute("""
            defines.inventory.turret_ammo = 9
            held = {['iron-plate']=100, ['firearm-magazine']=30}
            npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
            npc.character = {valid=true, position={x=0, y=0}, prototype={crafting_categories={crafting=true}},
                             get_main_inventory=function()
                                 return {get_item_count=function(spec) return held[spec.name] or 0 end} end}
            npc.force.recipes = {['firearm-magazine']={name='firearm-magazine', enabled=true, category='crafting',
                energy=1, ingredients={{type='item', name='iron-plate', amount=4}},
                products={{type='item', name='firearm-magazine', amount=1}}}}
            -- the armory: piercing rounds are better (the force can make them, say, but not this engineer yet)
            require('scripts.armory').ammo_for = function() return {'piercing-rounds-magazine', 'firearm-magazine'} end
            loaded, room = {}, {}
            ammo = {get_item_count=function(spec) return loaded[spec.name] or 0 end,
                    get_insertable_count=function(spec) return room[spec.name] or 200 end}
            turret = {valid=true, name='gun-turret', type='ammo-turret'}
            function job() local r = {DS.ammo_job(npc, turret, ammo, 'firearm-magazine', 5, 20)} return r[1], r[2] end
        """)
        first = lua.eval("(job())")
        self.assertEqual((first.kind, first.item, first.count), ("transfer", "firearm-magazine", 20))
        lua.execute("held['piercing-rounds-magazine'] = 8")
        self.assertEqual(lua.eval("(job())").item, "piercing-rounds-magazine")       # the best it holds
        # yellow still in the slot: piercing will not go in beside it; nothing held, so it makes yellow
        lua.execute("""held['piercing-rounds-magazine'] = 0; held['firearm-magazine'] = 0
                       loaded['firearm-magazine'] = 3; room['piercing-rounds-magazine'] = 0""")
        made = lua.eval("(job())")
        self.assertEqual((made.kind, made.recipe), ("craft", "firearm-magazine"))
        lua.execute("loaded['firearm-magazine'] = 6")
        self.assertIsNone(lua.eval("(job())"))                                         # enough in it


class LatePlanTests(unittest.TestCase):
    def test_a_plan_that_arrives_after_the_wait_is_still_taken(self):
        lua = runtime()
        lua.execute("""npc.character = {valid=true}
                       npc.design = {status='deferred', reason='designer_timeout', request_id='1:5'}""")
        late = lua.eval("DS.submit({request_id='1:5', plan={}})")
        self.assertFalse(str(late.reason).startswith("not_waiting"))   # checked like any plan, not refused
        lua.execute("npc.design = {status='deferred', reason='continue_projects', request_id='1:6'}")
        other = lua.eval("DS.submit({request_id='1:6', plan={}})")
        self.assertEqual(other.reason, "not_waiting:deferred")


class RebuildRunTests(unittest.TestCase):
    """What biters broke goes back in one walk once it is calm there, before the routine rounds, and a loss still
    fought over holds back none of the others (run 33, 2026-10-25: one trip a building after an attack, behind the
    supply rounds, left copper unmined for twenty minutes)."""

    def test_calm_losses_go_back_in_one_walk(self):
        lua = runtime()
        lua.execute("""
            local losses = {{copper_start, 'copper-smelting-start1', 'burner-mining-drill', {x=10, y=10}},
                            {copper_start, 'copper-smelting-start2', 'stone-furnace', {x=12, y=10}},
                            {iron_start, 'iron-smelting-start1', 'burner-mining-drill', {x=200, y=10}}}
            near = {}   -- biters by x
            standing = {}   -- what stands again by x
            npc.character = {valid=true, surface={count_entities_filtered=function(f) return near[f.position.x] or 0 end,
                                                  find_entities_filtered=function(f) return {standing[f.position.x]} end},
                             get_main_inventory=function() return {get_item_count=function() return 5 end} end}
            npc.force = 'npc-force'
            for _, row in ipairs(losses) do
                local e = row[1].entities[row[2]]
                e.prototype, e.item, e.world, e.direction = row[3], row[3], row[4], 0
                e.entity.force = 'npc-force'
                DS.on_entity_died({entity=e.entity, tick=1000})
                e.entity.valid = false
            end
            near[200] = 1   -- the iron drill's spot is still fought over
        """)
        self.assertEqual(lua.eval("#npc.rebuild"), 3)
        self.assertIsNone(lua.eval("DS.rebuild_job(npc, 1200)"))          # straight after the attack: wait
        job, meta = lua.eval("{DS.rebuild_job(npc, 1000 + 1800)}").values()
        self.assertEqual((job.kind, meta.upkeep), ("build_run", "rebuild_run"))
        self.assertEqual(len(job.placements), 2)                           # the two calm ones, in one walk
        lua.execute("""
            local job, meta = ...
            npc.design_jobs = {j = meta}
            local placed = {}
            for _, pl in ipairs(job.placements) do placed[#placed + 1] = {id=pl.id, entity=thing('x')} end
            DS.on_receipt(npc, {job_id='j', status='complete', placed=placed}, 3000)
        """, job, meta)
        self.assertEqual(lua.eval("#npc.rebuild"), 1)                      # the iron drill waits for calm
        self.assertEqual(lua.eval("#npc.rebuild_log"), 2)
        self.assertEqual(lua.eval("npc.rebuild_log[1].lost"), 1000)
        lua.execute("near[200] = 0")
        job, meta = lua.eval("{DS.rebuild_job(npc, 4000 + 1800)}").values()
        self.assertEqual((job.kind, meta.upkeep), ("build", "rebuild"))    # one left: a single build
        lua.execute("standing[200] = thing('mining-drill')")               # a walk put it down, unreported
        self.assertIsNone(lua.eval("DS.rebuild_job(npc, 6000)"))
        self.assertEqual(lua.eval("#npc.rebuild"), 0)                      # it stands: taken back into its plan
        self.assertEqual(lua.eval("iron_start.entities['iron-smelting-start1'].entity == standing[200]"), True)

    def test_the_game_keeps_the_expansions_that_found_no_room(self):
        lua = runtime()
        lua.execute("""copy = DS.base_plan_copy({x=1, y=2, columns=3, length=4, spm=5, stage='s',
                                                 expand_failed={['iron-plate']=100, bad='x'}})""")
        self.assertEqual(lua.eval("copy.expand_failed['iron-plate']"), 100)
        self.assertIsNone(lua.eval("copy.expand_failed.bad"))

    def test_the_game_keeps_what_its_bus_aims_at(self):
        lua = runtime()
        lua.execute("copy = DS.base_plan_copy({x=1, y=2, columns=3, length=4, spm=10, aim=20, stage='s'})")
        self.assertEqual(lua.eval("copy.aim"), 20)


if __name__ == "__main__":
    unittest.main()
