"""Something in the way of a planned piece (scripts/obstacles.lua with designed_start and the worker), in Factorio's
Lua 5.2: one ladder for every plan's pieces. Clear it, work round it, leave the piece out only where that is safe (never
a line's), else hand the plan back; every rung recorded. (Run 60, 2026-10-27: a supply belt's piece on a tree it could
not reach was left out, and the line carried nothing.)"""
import unittest

import test_clearing

LADDER = r"""
    local function one(name, kind, extra)
        local p = prototypes.entity[name] or {name=name}
        p.type, p.tile_width, p.tile_height = kind, 1, 1
        p.collision_mask = p.collision_mask or {layers={item=true, object=true, player=true, water_tile=true}}
        p.items_to_place_this, p.mineable_properties = {{name=name}}, {minable=true}
        for k, v in pairs(extra or {}) do p[k] = v end
        prototypes.entity[name] = p
        return p
    end
    one('underground-belt', 'underground-belt', {max_underground_distance=5})
    one('transport-belt', 'transport-belt', {related_underground_belt=prototypes.entity['underground-belt']})
    one('pipe', 'pipe')
    one('pipe-to-ground', 'pipe-to-ground', {fluidbox_prototypes={{pipe_connections={{direction=0},
        {connection_type='underground', direction=8, max_underground_distance=10}}}}})
    one('stone-wall', 'wall')
    one('small-electric-pole', 'electric-pole', {get_max_wire_distance=function() return 7.5 end,
        get_supply_area_distance=function() return 2.5 end})
    one('gun-turret', 'ammo-turret')
    one('inserter', 'inserter')
    one('stone-furnace', 'furnace')
    one('assembling-machine-1', 'assembling-machine', {electric_energy_source_prototype={}})
    for _, name in ipairs({'underground-belt', 'transport-belt', 'pipe', 'pipe-to-ground', 'stone-wall',
                           'small-electric-pole', 'gun-turret', 'inserter', 'stone-furnace'}) do held[name] = 10 end
    water = {}
    surface.find_tiles_filtered = function(f)
        local out, a = {}, f.area
        for _, t in ipairs(water) do
            if t.x + 1 > a[1][1] and t.x < a[2][1] and t.y + 1 > a[1][2] and t.y < a[2][2] then
                out[#out + 1] = {position={x=t.x, y=t.y},
                                 collides_with=function(layer) return layer == 'water_tile' or (t.deep and layer == 'player') end}
            end
        end
        return out
    end
    taken = {}
    surface.can_place_entity = function(spec)
        return not taken[spec.position.x .. ',' .. spec.position.y]
    end
    -- someone else's building: a teammate's
    function theirs(name, x, y)
        local e = put(name, x, y)
        e.force = {name='teammate'}
        return e
    end
    -- a plan of one stage: pieces {id, prototype, x, y, direction}; witnesses as given
    function plan_of(pieces, witnesses)
        local stage = {id='s', entity_ids={}, supplies={}, witnesses=witnesses or {{kind='power_connected'}}}
        local p = {plan_id='p', purpose='coal-line', stages={stage}, order={}, entities={}}
        for _, q in ipairs(pieces) do
            p.order[#p.order + 1] = q[1]
            stage.entity_ids[#stage.entity_ids + 1] = q[1]
            p.entities[q[1]] = {id=q[1], prototype=q[2], item=q[2], world={x=q[3], y=q[4]}, direction=q[5], failures=0}
        end
        npc.design = {status='building', stage_index=1, purpose=p.purpose, plan=p}
        npc.design_history = npc.design_history or {}
        return p
    end
    function fail(id, reason, blocked_by)
        for k = 1, 6 do
            npc.design_jobs['j' .. id .. k .. tostring(game.tick)] = {build=true, entity_id=id}
            DS.on_receipt(npc, {job_id='j' .. id .. k .. tostring(game.tick), status='failed', reason=reason,
                                blocked_by=blocked_by}, 1000 + k)
            if blocked_by then break end
        end
        game.tick = game.tick + 1
    end
    function last() return npc.obstacle_log[#npc.obstacle_log] end
    function belt_line(n, y)
        local pieces = {}
        for i = 1, n do pieces[#pieces + 1] = {'b' .. i, 'transport-belt', i - 0.5, y or 0.5, 4} end
        return plan_of(pieces)
    end
"""


def worker_with(extra_stubs):
    """test_clearing.worker(), with more of the modules the build path needs stubbed."""
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().worker_source = (test_clearing.ROOT / "mod" / "scripts" / "worker.lua").read_text(encoding="utf-8")
    lua.globals().base_map_source = (test_clearing.ROOT / "mod" / "scripts" / "base_map.lua").read_text(encoding="utf-8")
    lua.globals().hands_source = (test_clearing.ROOT / "mod" / "scripts" / "hands.lua").read_text(encoding="utf-8")
    lua.globals().ghosts_source = (test_clearing.ROOT / "mod" / "scripts" / "ghosts.lua").read_text(encoding="utf-8")
    lua.execute("""
        defines = {direction={north=0}, inventory={}}
        function log() end
        storage = {}
        nav_state = {'failed', 'path_ended_outside_reach'}
        stubs = {['scripts.team_world']={active=function() return false end},
            ['scripts.navigation']={distance=function(a, b) return math.sqrt((a.x-b.x)^2+(a.y-b.y)^2) end,
                                    step=function() return nav_state[1], nav_state[2] end, stop=function() end,
                                    clear=function() end},
            ['scripts.behavior']={ensure=function() end}, ['scripts.train_jobs']={finish=function() end},
            ['scripts.rescue']={note=function() end}, ['scripts.rail_support']={supports=function() return false end}}
        function require(name) return stubs[name] or {} end
        stubs['scripts.base_map'] = assert(load(base_map_source, '@base_map'))()
        stubs['scripts.hands'] = assert(load(hands_source, '@hands'))()
        stubs['scripts.ghosts'] = assert(load(ghosts_source, '@ghosts'))()
        prototypes = {item={}, entity={}}
        inv = {get_item_count=function() return 0 end, can_insert=function() return true end}
        character = {valid=true, surface={}, position={x=0, y=0}, resource_reach_distance=2.7, reach_distance=10,
                     build_distance=10, get_main_inventory=function() return inv end}
        npc = {id=1, character=character}
        W = assert(load(worker_source, '@worker'))()
    """ + extra_stubs)
    return lua


def ladder():
    lua = test_clearing.game()
    lua.execute("game = game or {}; game.tick = game.tick or 1")
    lua.execute(LADDER)
    return lua


class LineTests(unittest.TestCase):
    def test_a_belt_piece_it_cannot_reach_goes_under_and_the_line_stays_whole(self):
        lua = ladder()
        lua.execute("p = belt_line(7); fail('b4', 'path_ended_outside_reach')")
        self.assertEqual(lua.eval("npc.design.status"), "building")
        e = lambda i, k: lua.eval(f"p.entities.b{i}.{k}")
        self.assertEqual((e(3, "prototype"), e(3, "belt_to_ground_type")), ("underground-belt", "input"))
        self.assertEqual((e(5, "prototype"), e(5, "belt_to_ground_type")), ("underground-belt", "output"))
        self.assertEqual(e(4, "skipped.rung"), "bridged")
        self.assertIsNone(e(4, "left_out"))                                   # never left out: it is bridged
        self.assertEqual(lua.eval("last().rung"), "bridged")
        self.assertEqual(lua.eval("last().what"), "unreachable")

    def test_a_teammates_building_on_a_pipe_tile_is_gone_round_with_pipe_to_ground_facing_its_pipes(self):
        lua = ladder()
        lua.execute("""
            p = plan_of({{'p1', 'pipe', 0.5, 0.5}, {'p2', 'pipe', 1.5, 0.5}, {'p3', 'pipe', 2.5, 0.5},
                         {'p4', 'pipe', 3.5, 0.5}, {'p5', 'pipe', 4.5, 0.5}})
            theirs('assembling-machine-1', 2.5, 0.5)
            fail('p3', 'placement_blocked_or_out_of_reach')""")
        e = lambda i, k: lua.eval(f"p.entities.p{i}.{k}")
        self.assertEqual((e(2, "prototype"), e(2, "direction")), ("pipe-to-ground", 12))   # its pipe to the west
        self.assertEqual((e(4, "prototype"), e(4, "direction")), ("pipe-to-ground", 4))    # and on to the east
        self.assertEqual(e(3, "skipped.rung"), "bridged")
        # a branch off the pipe where it would go under: a pipe-to-ground joins nothing at its side, so not bridged
        lua = ladder()
        lua.execute("""
            p = plan_of({{'p1', 'pipe', 0.5, 0.5}, {'p2', 'pipe', 1.5, 0.5}, {'p3', 'pipe', 2.5, 0.5},
                         {'p4', 'pipe', 3.5, 0.5}, {'p5', 'pipe', 4.5, 0.5}, {'branch', 'stone-furnace', 1.5, 1.5}})
            theirs('assembling-machine-1', 2.5, 0.5)
            fail('p3', 'placement_blocked_or_out_of_reach')""")
        self.assertEqual(lua.eval("p.entities.p2.prototype"), "pipe")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        self.assertIn("kept:assembling-machine", lua.eval("last().what"))

    def test_a_span_too_long_to_go_under_or_joined_from_the_side_is_handed_back_never_left_out(self):
        lua = ladder()
        lua.execute("""
            p = belt_line(9)
            for x = 2, 7 do theirs('assembling-machine-1', x + 0.5, 0.5) end   -- six tiles: more than one span
            fail('b4', 'placement_blocked_or_out_of_reach')""")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        self.assertTrue(lua.eval("npc.design.reason").startswith("in_the_way:b4:"))
        self.assertIsNone(lua.eval("p.entities.b4.left_out"))
        self.assertEqual(lua.eval("last().rung"), "hand_back")
        self.assertEqual(lua.eval("npc.unbuildable[#npc.unbuildable].kind"), "assembling-machine")
        # a belt joining the line where it would go under: not bridged either
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            p.order[#p.order + 1] = 'side'
            p.entities.side = {id='side', prototype='inserter', item='inserter', world={x=3.5, y=1.5}, direction=0}
            fail('b4', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        self.assertEqual(lua.eval("p.entities.b3.prototype"), "transport-belt")

    def test_a_line_with_lanes_beside_it_goes_under_but_not_where_a_belt_joins_an_end_or_a_stage_is_done(self):
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            for i = 1, 7 do      -- a bus: another lane alongside, the same way
                p.order[#p.order + 1] = 'n' .. i
                p.entities['n' .. i] = {id='n' .. i, prototype='transport-belt', item='transport-belt',
                    world={x=i - 0.5, y=1.5}, direction=4}
            end
            fail('b4', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("p.entities.b4.skipped.rung"), "bridged")
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            p.order[#p.order + 1] = 'feed'      -- a belt coming in from the side onto b3
            p.entities.feed = {id='feed', prototype='transport-belt', item='transport-belt', world={x=2.5, y=1.5},
                direction=0}
            fail('b4', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("p.entities.b3.prototype"), "transport-belt")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            table.insert(p.stages, 1, {id='done', entity_ids={'b3'}, supplies={}, witnesses={}})
            for i, id in ipairs(p.stages[2].entity_ids) do if id == 'b3' then table.remove(p.stages[2].entity_ids, i) end end
            npc.design.stage_index = 2
            fail('b4', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("p.entities.b3.prototype"), "transport-belt")   # a stage done is not built again
        # an arm standing beside an end, no piece of this plan (a teammate's): not cut off either
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            prototypes.entity['inserter'].mineable_properties = {minable=true}
            local arm = put('inserter', 4.5, 1.5)
            arm.force = {name='teammate'}
            fail('b4', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("p.entities.b5.prototype"), "transport-belt")

    def test_an_underground_is_only_planned_where_a_witness_does_not_watch_the_piece_it_skips(self):
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            p.stages[1].witnesses = {{kind='entity_built', entity_id='b4'}}
            fail('b4', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("npc.design.status"), "blocked")


class GroundTests(unittest.TestCase):
    def test_a_wall_piece_on_a_cliff_or_water_is_not_needed_and_the_wall_goes_on(self):
        lua = ladder()
        lua.execute("""
            p = plan_of({{'w1', 'stone-wall', 0.5, 0.5}, {'w2', 'stone-wall', 1.5, 0.5}, {'w3', 'stone-wall', 2.5, 0.5}},
                        {{kind='entity_built', entity_id='w1'}})
            put('ridge', 1.5, 0.5)
            water[#water + 1] = {x=2, y=0, deep=true}
            fail('w2', 'placement_blocked_or_out_of_reach', 'cliff')
            fail('w3', 'placement_blocked_or_out_of_reach')""")
        self.assertEqual(lua.eval("npc.design.status"), "building")
        self.assertEqual(lua.eval("p.entities.w2.skipped.rung"), "covered")
        self.assertEqual(lua.eval("p.entities.w3.skipped.rung"), "covered")
        # no charge thrown for a wall's cliff, no landfill for its water: the ground does the wall's work
        self.assertIsNone(lua.eval("(DS.ground_job(npc, npc.design, p.entities.w2, 2000))"))   # (no job)

    def test_a_cliff_under_a_building_is_blasted_with_a_charge_it_holds(self):
        lua = ladder()
        lua.execute("""
            p = plan_of({{'f', 'stone-furnace', 0.5, 0.5}})
            cliff = put('ridge', 0.5, 0.5)
            held['boom-charge'] = 1
            first = {DS.ground_job(npc, npc.design, p.entities.f, 2000)}""")
        self.assertEqual(lua.eval("first[2]"), "blasting")
        self.assertEqual(lua.eval("held['boom-charge']"), 0)
        self.assertEqual(lua.eval("last().rung"), "clear")
        lua.execute("later = {DS.ground_job(npc, npc.design, p.entities.f, 2000 + 200)}")
        self.assertFalse(lua.eval("cliff.valid"))                              # (landed beside it: done all the same)
        self.assertEqual(lua.eval("last().rung"), "blasted")

    def test_water_under_a_piece_is_filled_with_landfill_when_it_can_and_gone_under_when_it_cannot(self):
        lua = ladder()
        lua.execute("""
            prototypes.item['landfill'] = {name='landfill',
                place_as_tile_result={result={name='landfill'}, condition={layers={ground_tile=true}}}}
            p = belt_line(7)
            water[#water + 1] = {x=3, y=0}
            held['landfill'] = 3
            job = DS.ground_job(npc, npc.design, p.entities.b4, 2000)""")
        self.assertEqual((lua.eval("job.kind"), lua.eval("job.item"), lua.eval("#job.positions")), ("landfill", "landfill", 1))
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            water[#water + 1] = {x=3, y=0}
            fail('b4', 'placement_blocked_or_out_of_reach')""")
        self.assertEqual(lua.eval("p.entities.b4.skipped.rung"), "bridged")
        self.assertIn("water:1", lua.eval("last().what"))

    def test_what_it_can_clear_is_tried_again_once_before_it_works_round_it(self):
        lua = ladder()
        lua.execute("""
            p = belt_line(7)
            put('oak-of-legend', 3.5, 0.5)
            fail('b4', 'path_ended_outside_reach')""")
        self.assertEqual(lua.eval("last().rung"), "clear")
        self.assertEqual(lua.eval("p.entities.b4.failures"), 0)
        lua.execute("fail('b4', 'path_ended_outside_reach')")
        self.assertEqual(lua.eval("last().rung"), "bridged")                  # (the second time: round it)


    def test_shallow_water_under_a_wall_is_no_wall_and_is_filled(self):
        # (biters wade through shallow water: only deep water does a wall's work)
        lua = ladder()
        lua.execute("""
            prototypes.item['landfill'] = {name='landfill',
                place_as_tile_result={result={name='landfill'}, condition={layers={ground_tile=true}}}}
            p = plan_of({{'w1', 'stone-wall', 0.5, 0.5}})
            water[#water + 1] = {x=0, y=0}
            held['landfill'] = 1
            fail('w1', 'placement_blocked_or_out_of_reach')
            job = DS.ground_job(npc, npc.design, p.entities.w1, 2000)""")
        self.assertIsNone(lua.eval("p.entities.w1.skipped"))
        self.assertEqual(lua.eval("last().rung"), "clear")
        self.assertEqual(lua.eval("job.kind"), "landfill")

    def test_a_piece_with_an_empty_list_of_tile_rules_goes_on_land_and_one_with_rules_on_water(self):
        # (the game gives a pipe an empty list: read as rules, a pipe's pond pieces were never filled)
        lua = ladder()
        lua.execute("""
            prototypes.entity['pipe'].tile_buildability_rules = {}
            prototypes.entity['offshore'] = {name='offshore', type='offshore-pump', tile_buildability_rules={{}},
                collision_mask={layers={object=true, water_tile=true}}}""")
        self.assertTrue(lua.eval("DS.Obstacles.on_land('pipe')"))
        self.assertFalse(lua.eval("DS.Obstacles.on_land('offshore')"))

    def test_a_wall_goes_on_past_a_teammates_building_in_its_line(self):
        lua = ladder()
        lua.execute("""
            prototypes.entity['wooden-chest'] = {name='wooden-chest', type='container',
                collision_mask={layers={item=true, object=true, player=true, water_tile=true}},
                mineable_properties={minable=true}}
            p = plan_of({{'w1', 'stone-wall', 0.5, 0.5}, {'w2', 'stone-wall', 1.5, 0.5}})
            theirs('wooden-chest', 1.5, 0.5)
            fail('w2', 'placement_blocked_or_out_of_reach')""")
        self.assertEqual(lua.eval("npc.design.status"), "building")
        self.assertEqual(lua.eval("p.entities.w2.skipped.rung"), "covered")

    def test_a_wall_piece_on_deep_water_is_known_covered_before_any_try(self):
        lua = ladder()
        lua.execute("""
            p = plan_of({{'w1', 'stone-wall', 0.5, 0.5}})
            water[#water + 1] = {x=0, y=0, deep=true}
            got = {DS.ground_job(npc, npc.design, p.entities.w1, 2000)}""")
        self.assertEqual(lua.eval("got[2]"), "covered")
        self.assertEqual(lua.eval("p.entities.w1.skipped.rung"), "covered")
        self.assertIsNone(lua.eval("next(npc.design_jobs or {})"))            # no build tried

    def test_landfill_is_the_item_chosen_over_a_soil_kept_to_some_tiles(self):
        lua = ladder()
        lua.execute("""
            prototypes.item['a-soil'] = {name='a-soil', place_as_tile_result={result={name='soil'},
                condition={layers={ground_tile=true}}, tile_condition={'shallow-water'}}}
            prototypes.item['landfill'] = {name='landfill',
                place_as_tile_result={result={name='landfill'}, condition={layers={ground_tile=true}}}}""")
        self.assertEqual(lua.eval("DS.Obstacles.landfill({'water'}).item"), "landfill")

    def test_a_landfill_that_lists_the_waters_it_goes_on_is_used_on_those(self):
        # (Space Age's landfill names its waters: refused as "goes on none", a pipe's pond pieces were never tried)
        lua = ladder()
        lua.execute("""
            prototypes.item['landfill'] = {name='landfill', place_as_tile_result={result={name='landfill'},
                condition={layers={ground_tile=true}}, tile_condition={{name='water'}, {name='deepwater'}}}}
            prototypes.item['a-soil'] = {name='a-soil', place_as_tile_result={result={name='soil'},
                condition={layers={ground_tile=true}}, tile_condition={'wetland'}}}""")
        self.assertEqual(lua.eval("DS.Obstacles.landfill({'water', 'deepwater'}).item"), "landfill")
        self.assertEqual(lua.eval("DS.Obstacles.landfill({'wetland'}).item"), "a-soil")
        self.assertIsNone(lua.eval("DS.Obstacles.landfill({'oil-ocean'})"))


class ShiftTests(unittest.TestCase):
    def test_a_pole_in_the_way_stands_aside_keeping_its_wires_and_what_it_powers(self):
        lua = ladder()
        lua.execute("""
            p = plan_of({{'pa', 'small-electric-pole', 0.5, 0.5}, {'pb', 'small-electric-pole', 7.5, 0.5},
                         {'m', 'assembling-machine-1', 9.5, 0.5}})
            theirs('assembling-machine-1', 7.5, 0.5)
            taken['7.5,0.5'] = true
            fail('pb', 'placement_blocked_or_out_of_reach')""")
        self.assertEqual(lua.eval("last().rung"), "shifted")
        x, y = lua.eval("p.entities.pb.world.x"), lua.eval("p.entities.pb.world.y")
        self.assertLessEqual(((x - 0.5) ** 2 + (y - 0.5) ** 2) ** 0.5, 7.5)   # still wired to pa
        self.assertGreater(x + 2.5, 9.0)                                       # still powers the machine
        self.assertEqual(lua.eval("p.entities.pb.moved_from.x"), 7.5)

    def test_a_turret_fed_by_an_inserter_keeps_its_place(self):
        lua = ladder()
        lua.execute("""
            p = plan_of({{'t', 'gun-turret', 0.5, 0.5}, {'arm', 'inserter', 2.5, 0.5, 0}})
            theirs('assembling-machine-1', 0.5, 0.5)
            fail('t', 'placement_blocked_or_out_of_reach')""")
        self.assertEqual(lua.eval("p.entities.t.world.x"), 0.5)
        self.assertIn(lua.eval("last().rung"), ("left_out", "hand_back"))


class WorkerTests(unittest.TestCase):
    def test_landfill_is_put_down_from_reach_one_item_a_tile_only_on_water(self):
        lua = test_clearing.worker()
        lua.execute("""
            defines.direction = {north=0}
            prototypes.item['landfill'] = {name='landfill', place_as_tile_result={result={name='landfill'}}}
            character.build_distance = 10
            have = 2
            inv.get_item_count = function() return have end
            inv.remove = function(s) have = have - s.count return s.count end
            wet = {['3,0']=true, ['4,0']=true}
            set = {}
            character.surface.get_tile = function(x, y)
                return {collides_with=function() return wet[x .. ',' .. y] == true end}
            end
            character.surface.set_tiles = function(tiles)
                for _, t in ipairs(tiles) do set[#set + 1] = t; wet[t.position.x .. ',' .. t.position.y] = nil end
            end
            assert(W.enqueue(npc, {kind='landfill', item='landfill', positions={{x=3, y=0}, {x=4, y=0}, {x=5, y=0}}}))
            W.tick(npc, 1)
            W.tick(npc, 2)""")
        self.assertEqual(lua.eval("#set"), 2)
        self.assertEqual(lua.eval("have"), 0)
        receipt = list(lua.eval("npc.receipts").values())[0]
        self.assertEqual(receipt.status, "complete")
        self.assertFalse(lua.eval("(W.enqueue(npc, {kind='landfill', item='landfill', positions={}}))"))

    def test_a_spot_to_build_it_cannot_walk_to_has_the_tree_in_the_way_cut_first(self):
        lua = worker_with("""
            prototypes.entity['stone-furnace'] = {type='furnace', tile_width=2, tile_height=2}
            prototypes.item['stone-furnace'] = {type='item', place_result={name='stone-furnace'}}
            character.can_place_entity = function() return false end
            tree = {valid=true, type='tree', name='tree-01', minable=true, force={name='neutral'},
                    position={x=3, y=0}, bounding_box={left_top={x=2.6, y=-0.4}, right_bottom={x=3.4, y=0.4}}}
            character.surface.find_entities_filtered = function() return {tree} end
            assert(W.enqueue(npc, {kind='build', entity_name='stone-furnace', item='stone-furnace',
                                   position={x=20, y=0}}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("npc.current_job.detour.name"), "tree-01")
        self.assertEqual(lua.eval("npc.obstacle_log[1].rung"), "cut_through")
        # no tree in the way: the build fails as before, for the ladder to take up
        lua.execute("""
            tree.valid = false
            nav_state = {'failed', 'path_ended_outside_reach'}
            for t = 2, 1000 do if npc.current_job then W.tick(npc, t) end end""")
        self.assertEqual(lua.eval("npc.failures[#npc.failures].reason"), "path_ended_outside_reach")

    def test_a_spot_cleared_by_another_hand_is_a_clearing_done_not_a_failure(self):
        # (LIVE-OP-a, stage-nuclear: its construction robots took the trees in a wall's way, each clearing failed
        # "target_exhausted", and the ring's 170-piece north wall was given up with 3 standing)
        def run(clears):
            lua = worker_with("""
                prototypes.entity['tree-01'] = {name='tree-01', type='tree', mineable_properties={minable=true,
                    mining_time=0.5, products={{type='item', name='wood', amount=4}}}}
                tree = {valid=true, type='tree', name='tree-01', minable=true, force={name='neutral'},
                        position={x=1, y=0}, prototype=prototypes.entity['tree-01'], surface=character.surface,
                        bounding_box={left_top={x=0.6, y=-0.4}, right_bottom={x=1.4, y=0.4}}}
                character.surface.find_entities_filtered = function() return {tree} end""")
            lua.execute(f"""
                assert(W.enqueue(npc, {{kind='mine', target=tree, count=1, clears={'true' if clears else 'false'}}}))
                W.tick(npc, 1)
                tree.valid = false   -- (a robot took it)
                for t = 2, 200 do if npc.current_job then W.tick(npc, t) end end""")
            return list(lua.eval("npc.receipts").values())[-1].status
        self.assertEqual(run(True), "complete")
        self.assertNotEqual(run(False), "complete")                         # (a mine for its item: still a failure)
        # (gone before its first tick: done too; Codex's review)
        lua = worker_with("""
            tree = {valid=false, type='tree', name='tree-01', position={x=1, y=0}}""")
        lua.execute("assert(W.enqueue(npc, {kind='mine', target=tree, count=1, clears=true})); W.tick(npc, 1)")
        self.assertEqual(list(lua.eval("npc.receipts").values())[-1].status, "complete")

if __name__ == "__main__":
    unittest.main()
