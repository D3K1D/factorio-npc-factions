"""Combat movement by rule: routes round known dangers (combat_routes.lua), what an engineer fights of
its own accord and how it moves in a fight (combat.lua), in Factorio's Lua 5.2 without Factorio."""
from pathlib import Path
import math
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def routes():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "combat_routes.lua").read_text(encoding="utf-8")
    lua.execute("R = assert(load(source))()")
    return lua


def points(lua, expr):
    return [(p.x, p.y) for p in lua.eval("(" + expr + ")").values()]   # (the points; not whether safe)


def clear_of(path, start, threats, room):
    """Every leg of the walk keeps `room` beyond each threat's reach."""
    legs = [start] + path
    for (ax, ay), (bx, by) in zip(legs, legs[1:]):
        for (tx, ty, r) in threats:
            dx, dy = bx - ax, by - ay
            u = max(0, min(1, ((tx - ax) * dx + (ty - ay) * dy) / (dx * dx + dy * dy or 1)))
            if math.hypot(ax + u * dx - tx, ay + u * dy - ty) < r + room:
                return False
    return True


class RouteTests(unittest.TestCase):
    def test_straight_there_when_nothing_is_in_the_way(self):
        lua = routes()
        self.assertEqual(points(lua, "R.plan({x=0, y=0}, {x=500, y=0}, {{x=250, y=200, r=40}})"), [(500, 0)])

    def test_round_a_nest_on_the_way_and_out_of_its_reach(self):
        lua = routes()
        path = points(lua, "R.plan({x=0, y=0}, {x=600, y=0}, {{x=300, y=0, r=40}, {x=300, y=60, r=36}})")
        self.assertEqual(path[-1], (600, 0))
        self.assertGreater(len(path), 1)                                  # a corner or two round it
        self.assertTrue(clear_of(path, (0, 0), [(300, 0, 40), (300, 60, 36)], 7))
        self.assertLess(len(path), 6)                                     # straight legs between them

    def test_woods_are_gone_round_when_it_is_not_much_longer_and_are_no_danger(self):
        lua = routes()
        lua.execute("woods = {{x=200, y=0, r=24, soft=true}}")
        path = points(lua, "R.plan({x=0, y=0}, {x=400, y=0}, woods)")
        self.assertTrue(clear_of(path, (0, 0), [(200, 0, 24)], 0))              # round them
        self.assertTrue(lua.eval("(select(2, R.plan({x=0, y=0}, {x=400, y=0}, woods)))"))
        lua.execute("forest = {} for y = -400, 400, 30 do forest[#forest + 1] = {x=200, y=y, r=24, soft=true} end")
        self.assertTrue(lua.eval("(select(2, R.plan({x=0, y=0}, {x=400, y=0}, forest)))"))   # through: still clear

    def test_a_long_trip_past_walls_and_woods_gets_a_route_not_a_straight_line(self):
        lua = routes()
        lua.execute("""
            -- spots it could not reach (lake shores, no way on) all over the map: every way dear
            many = {}
            for i = -10, 50 do for j = -10, 50 do
                if (i * 7 + j * 3) % 4 == 0 then many[#many + 1] = {x=-40 * i, y=-35 * j, r=24, blocked=true} end
            end end""")
        path = points(lua, "R.plan({x=0, y=0}, {x=-1600, y=-1400}, many)")
        self.assertGreater(len(path), 1)                                         # found, not given up

    def test_a_route_asked_for_a_slice_at_a_time_is_the_same_as_planned_at_once(self):
        lua = routes()
        lua.execute("""
            game = {forces={}, tick=0}
            surface = {find_entities_filtered=function() return {} end}
            npc = {force={name='npc-faction-1'}, character={valid=true, position={x=0, y=0}, surface=surface}}
            for i = -10, 50 do for j = -10, 50 do
                if (i * 7 + j * 3) % 4 == 0 then R.block(npc, {x=-40 * i, y=-35 * j}, 0, 24) end
            end end
            whole = R.for_npc(npc, {x=-1600, y=-1400})
            calls, sliced = 0, nil
            repeat calls = calls + 1; game.tick = calls; sliced = R.request(npc, {x=-1600, y=-1400}) until sliced or calls > 500""")
        self.assertGreater(lua.eval("calls"), 2)                                  # a slice at a time
        self.assertEqual(points(lua, "sliced.points"), points(lua, "whole.points"))
        self.assertIsNone(lua.eval("npc.route_job"))                              # done with

    def test_under_geared_it_keeps_a_wide_berth_and_says_when_there_is_no_way_round(self):
        lua = routes()
        lua.execute("nest = {{x=300, y=0, r=60, nest=true}}")
        path = points(lua, "R.plan({x=0, y=0}, {x=600, y=0}, nest, nil, nil, 48)")
        self.assertTrue(clear_of(path, (0, 0), [(300, 0, 60 + 48)], 9))      # well out of its biters' reach
        self.assertTrue(lua.eval("(select(2, R.plan({x=0, y=0}, {x=600, y=0}, nest, nil, nil, 48)))"))
        # a wall of nests from edge to edge of what it may search: no way round
        lua.execute("wall = {} for y = -400, 400, 40 do wall[#wall + 1] = {x=300, y=y, r=60, nest=true} end")
        self.assertFalse(lua.eval("(select(2, R.plan({x=0, y=0}, {x=600, y=0}, wall, nil, nil, 48)))"))
        self.assertTrue(lua.eval("(select(2, R.plan({x=0, y=0}, {x=600, y=0}, nest)))"))   # geared: as before
        self.assertGreater(lua.eval("select(3, R.plan({x=0, y=0}, {x=600, y=0}, wall, nil, nil, 48))"), 0)
        self.assertEqual(lua.eval("select(3, R.plan({x=0, y=0}, {x=600, y=0}, nest))"), 0)   # how many it crosses

    def test_starting_inside_a_nests_reach_it_gets_out_away_from_the_nest(self):
        lua = routes()
        # a nest just north-east of where it stands, the goal far east past it
        path = points(lua, "R.plan({x=0, y=0}, {x=800, y=0}, {{x=40, y=-30, r=60, nest=true}}, nil, nil, 48)")
        legs = [(0, 0)] + path
        for (ax, ay), (bx, by) in zip(legs, legs[1:]):
            for k in range(21):
                x, y = ax + (bx - ax) * k / 20, ay + (by - ay) * k / 20
                self.assertGreater(((x - 40) ** 2 + (y + 30) ** 2) ** 0.5, 40, (x, y))   # not by the nest

    def test_the_land_along_a_route_is_made_before_it_is_trusted(self):
        lua = routes()
        lua.execute("""
            made, asked = {}, 0
            npc = {character={surface={
                is_chunk_generated=function(c) return made[c.x .. ',' .. c.y] or c.x < 10 end,
                request_to_generate_chunks=function() asked = asked + 1 end}}}""")
        self.assertTrue(lua.eval("R.route_ready(npc, {x=0, y=0}, {{x=100, y=0}})"))
        self.assertFalse(lua.eval("R.route_ready(npc, {x=0, y=0}, {{x=600, y=0}})"))     # a detour off the map
        self.assertGreater(lua.eval("asked"), 0)

    def test_a_short_walk_past_a_known_nest_is_not_straight(self):
        lua = routes()
        lua.execute("npc = {seen_dangers={list={[1]={x=50, y=10, r=48, nest=true}}}}")
        self.assertFalse(lua.eval("R.straight_clear(npc, {x=0, y=0}, {x=100, y=0})"))   # it passes the nest
        self.assertTrue(lua.eval("R.straight_clear(npc, {x=0, y=200}, {x=100, y=200})"))  # far from it

    def test_what_it_comes_to_fight_is_not_walked_round(self):
        lua = routes()
        # the target's own turrets sit at the goal: spared, so it walks straight there
        self.assertEqual(points(lua, "R.plan({x=0, y=0}, {x=400, y=0}, {{x=390, y=0, r=24}}, 80)"), [(400, 0)])

    def test_nests_by_the_target_are_still_walked_round_but_its_turrets_are_not(self):
        lua = routes()
        # a nest beside the base it goes to fight, on the way in; the base's own turret at the goal
        path = points(lua, "R.plan({x=0, y=0}, {x=400, y=0}, {{x=340, y=0, r=40, nest=true},"
                           " {x=395, y=0, r=24, nest=false}}, 100)")
        self.assertGreater(len(path), 1)
        self.assertTrue(clear_of(path[:-1], (0, 0), [(340, 0, 40)], 0))
        # coming for the nests themselves: straight there
        self.assertEqual(points(lua, "R.plan({x=0, y=0}, {x=400, y=0}, {{x=340, y=0, r=40, nest=true}}, 100, true)"),
                         [(400, 0)])

    def test_between_a_nest_and_a_lake_it_takes_the_dry_side_clear_of_the_nest(self):
        lua = routes()
        # a nest on the straight line, a lake (blocked spots) along one side of it
        lake = [(x, y, 20) for x in range(150, 451, 30) for y in range(60, 260, 30)]
        lua.globals().threats = lua.table_from(
            [lua.table_from({"x": 300, "y": 0, "r": 60, "nest": True})] +
            [lua.table_from({"x": x, "y": y, "r": r, "nest": True, "blocked": True}) for x, y, r in lake])
        path = points(lua, "R.plan({x=0, y=0}, {x=600, y=0}, threats)")
        self.assertTrue(clear_of(path, (0, 0), [(300, 0, 60)], 7))       # clear of the nest
        self.assertTrue(clear_of(path, (0, 0), lake, 0))                 # and dry
        self.assertTrue(all(y <= 0 for _, y in path))                    # the dry side

    def test_a_long_walk_across_many_nests_stays_quick_and_clear(self):
        lua = routes()
        nests = [(x, y, 40) for x in range(200, 2200, 300) for y in range(-400, 401, 200) if (x // 300 + y // 200) % 2]
        lua.globals().nests = lua.table_from([lua.table_from({"x": x, "y": y, "r": r}) for x, y, r in nests])
        path = points(lua, "R.plan({x=0, y=0}, {x=2400, y=0}, nests)")
        self.assertEqual(path[-1], (2400, 0))
        self.assertTrue(clear_of(path, (0, 0), nests, 0))


class MemoryTests(unittest.TestCase):
    def test_it_remembers_the_nests_it_has_seen_and_forgets_the_ones_gone(self):
        lua = routes()
        lua.execute("""
            in_sight = {}
            game = {forces={}}
            surface = {find_entities_filtered=function(f) if f.force == 'enemy' then return in_sight end return {} end}
            npc = {force={name='npc-faction-1'}, character={valid=true, position={x=0, y=0}, surface=surface}}
            nest = {valid=true, unit_number=7, type='unit-spawner', name='biter-spawner', position={x=60, y=10},
                    force={name='enemy'}}
            in_sight = {nest}
            first = R.look(npc)
            again = R.look(npc)
            route = R.for_npc(npc, {x=400, y=0})""")
        self.assertTrue(lua.eval("first"))
        self.assertFalse(lua.eval("again"))                   # nothing new the second time
        # a route planned before it saw the nest is stale once it has seen it, whoever looked
        lua.execute("""
            fresh = {list={}, count=0}
            other = {force={name='npc-faction-1'}, character={valid=true, position={x=0, y=0}, surface=surface}}
            in_sight = {}
            old_route = R.for_npc(other, {x=400, y=0})
            in_sight = {nest}
            R.look(other)                                     -- its regular look round saw it first
            stale = R.new_sighting(other, old_route)""")
        self.assertTrue(lua.eval("stale"))
        self.assertGreater(len(points(lua, "route.points")), 1)  # remembered, so walked round
        # driving, only what is in the way ahead plans it again; on foot, anything new it sees
        lua.execute("""
            side = {valid=true, unit_number=8, type='unit-spawner', name='biter-spawner', position={x=200, y=150},
                    force={name='enemy'}}
            ahead = {valid=true, unit_number=9, type='unit-spawner', name='biter-spawner', position={x=150, y=4},
                     force={name='enemy'}}
            driver = {force={name='npc-faction-1'}, character={valid=true, position={x=0, y=0}, surface=surface}}
            in_sight = {}
            car_route = R.for_npc(driver, {x=400, y=0}, nil, nil, 0, 'car')
            walk_route = R.for_npc(driver, {x=400, y=0})
            in_sight = {side}""")
        self.assertFalse(lua.eval("R.new_sighting(driver, car_route, 'car')"))   # off to the side: on it goes
        self.assertTrue(lua.eval("R.new_sighting(driver, walk_route)"))
        lua.execute("in_sight = {side, ahead}")
        self.assertTrue(lua.eval("R.new_sighting(driver, car_route, 'car')"))    # in the way: round it
        # walked on: out of sight, still remembered; the nest cleared and seen gone: forgotten
        lua.execute("in_sight = {}; npc.character.position = {x=500, y=0}; R.look(npc)")
        self.assertEqual(lua.eval("npc.seen_dangers.count"), 1)
        lua.execute("nest.valid = false; npc.character.position = {x=50, y=0}; R.look(npc)")
        self.assertEqual(lua.eval("npc.seen_dangers.count"), 0)
        # a spot it could not reach is kept clear of for a while, even in sight
        lua.execute("game.tick = 100; R.block(npc, {x=60, y=0}, 100); R.look(npc)")
        self.assertEqual(lua.eval("npc.seen_dangers.count"), 1)
        self.assertGreater(len(points(lua, "R.for_npc(npc, {x=400, y=0}).points")), 1)
        lua.execute("game.tick = 200000; R.look(npc)")
        self.assertEqual(lua.eval("npc.seen_dangers.count"), 0)


class WaterTests(unittest.TestCase):
    def test_a_route_over_a_lake_is_planned_again_round_it(self):
        lua = routes()
        lua.execute("""
            game = {forces={}, tick=0}
            function lake(p) return p.x > 150 and p.x < 250 and math.abs(p.y) < 70 end
            surface = {find_entities_filtered=function() return {} end,
                       count_tiles_filtered=function(f) return lake(f.position) and 1 or 0 end}
            npc = {force={name='npc-faction-1'}, character={valid=true, position={x=0, y=0}, surface=surface}}
            route = R.for_npc(npc, {x=400, y=0})
            dry = true
            local a = {x=0, y=0}
            for _, b in ipairs(route.points) do
                for i = 1, 40 do
                    local p = {x=a.x + (b.x - a.x) * i / 40, y=a.y + (b.y - a.y) * i / 40}
                    if lake(p) then dry = false end
                end
                a = b
            end""")
        self.assertGreater(len(points(lua, "route.points")), 1)
        self.assertTrue(lua.eval("dry"))                       # round the lake, not through it


class AutopilotTests(unittest.TestCase):
    def test_it_waits_for_the_land_then_plans_round_every_nest_and_lake_on_the_way(self):
        lua = routes()
        lua.execute("""
            storage = {}
            game = {forces={}, tick=0}
            generated = {}
            requested = 0
            far_nest = {valid=true, unit_number=9, type='unit-spawner', name='biter-spawner', position={x=600, y=10},
                        force={name='enemy'}}
            surface = {index=1,
                request_to_generate_chunks=function() requested = requested + 1 end,
                is_chunk_generated=function(c) return generated.all or false end,
                find_entities_filtered=function(f)
                    if f.force == 'enemy' and f.area then return {far_nest} end
                    return {}
                end,
                -- a lake 4 chunks wide across the way at x 256..384, y -64..64
                count_tiles_filtered=function(f)
                    local a = f.area or {{f.position.x, f.position.y}, {f.position.x, f.position.y}}
                    local x, y = a[1][1] or a[1].x, a[1][2] or a[1].y
                    if x >= 256 and x < 384 and y >= -64 and y < 64 then return 1024 end
                    return 0
                end}
            npc = {force={name='npc-faction-1'}, character={valid=true, position={x=0, y=0}, surface=surface}}
            first = R.prepare(npc, {x=1200, y=0}, 10)""")
        self.assertFalse(lua.eval("first"))                    # the land is not there yet: wait
        self.assertGreater(lua.eval("requested"), 10)          # asked for along the whole way
        lua.execute("generated.all = true; ready = R.prepare(npc, {x=1200, y=0}, 20)")
        self.assertTrue(lua.eval("ready"))
        lua.execute("route = R.for_npc(npc, {x=1200, y=0})")
        path = points(lua, "route.points")
        self.assertGreater(len(path), 1)
        # round the nest it never saw (600, 10), and round the lake
        self.assertTrue(clear_of(path, (0, 0), [(600, 10, 48)], 7))
        legs = [(0, 0)] + path
        for (ax, ay), (bx, by) in zip(legs, legs[1:]):
            for k in range(41):
                x, y = ax + (bx - ax) * k / 40, ay + (by - ay) * k / 40
                self.assertFalse(256 <= x < 384 and -64 <= y < 64, (x, y))


def combat():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "combat.lua").read_text(encoding="utf-8")
    for name in ("team_world", "team_war", "combat_odds", "armory"):
        lua.globals()[name + "_source"] = (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
    lua.execute("""
        for _, name in ipairs({'worker', 'movement_policy', 'combat_policy', 'combat_squads', 'combat_equipment',
                               'combat_turrets', 'personality', 'recipes'}) do
            package.preload['scripts.' .. name] = function() return {} end
        end
        package.preload['scripts.team_waves'] = function() return {nest_of=function() return nil end} end
        for _, name in ipairs({'team_world', 'team_war', 'combat_odds', 'armory'}) do
            package.preload['scripts.' .. name] = function() return assert(load(_G[name .. '_source']))() end
        end
        C = assert(load(source))()
        npc = {combat={}}
        function thing(kind, x, y, range)
            return {type=kind, position={x=x, y=y}, prototype={attack_parameters={range=range or 20}}}
        end
    """)
    return lua


class FightRuleTests(unittest.TestCase):
    def test_nests_and_turrets_only_where_it_chose_to_attack_or_when_they_hurt_it(self):
        lua = combat()
        self.assertFalse(lua.eval("C.engage(npc, thing('unit-spawner', 10, 0), 1000, 10)"))
        self.assertFalse(lua.eval("C.engage(npc, thing('turret', 25, 0, 30), 1000, 25)"))
        lua.execute("npc.combat.hurt_tick = 950")                        # a worm hitting it now
        self.assertTrue(lua.eval("C.engage(npc, thing('turret', 25, 0, 30), 1000, 25)"))
        self.assertFalse(lua.eval("C.engage(npc, thing('turret', 40, 0, 30), 1000, 40)"))   # not that one
        lua.execute("npc.combat.hurt_tick = nil; npc.combat_objective = {x=200, y=0}; npc.combat_mission_until = 1600")
        self.assertTrue(lua.eval("C.engage(npc, thing('unit-spawner', 230, 0), 1000, 10)"))  # the target
        self.assertFalse(lua.eval("C.engage(npc, thing('unit-spawner', 230, 0), 1700, 10)"))  # mission over
        self.assertTrue(lua.eval("C.engage(npc, thing('character', 30, 0), 1000, 30)"))      # people, always

    def test_on_a_march_biters_that_keep_away_are_left_alone(self):
        lua = combat()
        lua.execute("npc.marching = {until_tick=2000}")
        self.assertFalse(lua.eval("C.engage(npc, thing('unit', 20, 0), 1000, 20)"))
        self.assertTrue(lua.eval("C.engage(npc, thing('unit', 10, 0), 1000, 10)"))           # coming for it
        lua.execute("npc.combat.hurt_tick = 990")                        # a spitter hitting it from 16
        self.assertTrue(lua.eval("C.engage(npc, thing('unit', 16, 0), 1000, 16)"))

    def test_an_even_gunfight_closes_in_instead_of_circling_out_of_range(self):
        lua = combat()
        lua.execute("""
            defines = {inventory={character_guns=2}}
            function gunner(range)
                local gun = {valid_for_read=true, prototype={attack_parameters={range=range}}}
                return {type='character', selected_gun_index=1, get_inventory=function() return {gun} end}
            end""")
        self.assertEqual(lua.eval("C.kept_reach(gunner(15), 15)"), 0)        # same pistol: it must go in
        self.assertEqual(lua.eval("C.kept_reach(gunner(15), 18)"), 15)       # outranges them: stays out
        self.assertEqual(lua.eval("C.kept_reach(thing('turret', 0, 0, 30), 15)"), 30)   # a worm: keeps out

    def test_cornered_by_faster_biters_far_from_its_turrets_it_stands_and_fights(self):
        lua = combat()
        lua.execute("""
            defines = {inventory={turret_ammo=1}}
            own = {}
            function turret(x, y, empty) own[#own + 1] = {valid=true, type='ammo-turret', position={x=x, y=y},
                get_inventory=function() return {is_empty=function() return empty == true end} end} end
            npc.character = {position={x=0, y=0}, prototype={running_speed=0.15}, force={character_running_speed_modifier=0},
                surface={find_entities_filtered=function(f)
                    local out = {}
                    for _, t in ipairs(own) do
                        if (t.position.x - f.position.x)^2 + (t.position.y - f.position.y)^2 <= f.radius^2 then out[#out + 1] = t end
                    end
                    return out end}}
            function biter(speed) return {valid=true, type='unit', speed=speed, prototype={speed=speed},
                position={x=-10, y=0}} end""")
        self.assertTrue(lua.eval("C.cornered(npc, biter(0.2))"))         # small biters outrun it
        self.assertFalse(lua.eval("C.cornered(npc, biter(0.1))"))        # slow ones it can leave behind
        lua.execute("turret(20, 0)")
        self.assertFalse(lua.eval("C.cornered(npc, biter(0.2))"))        # its loaded turret a short run off: back to it
        lua.execute("own = {}; turret(20, 0, true)")
        self.assertTrue(lua.eval("C.cornered(npc, biter(0.2))"))         # (an empty one is no cover)
        lua.execute("own = {}; turret(-30, 0)")
        self.assertTrue(lua.eval("C.cornered(npc, biter(0.2))"))         # past the biters: no run through them
        lua.execute("own = {}; turret(60, 0)")
        self.assertTrue(lua.eval("C.cornered(npc, biter(0.2))"))         # too far to get to before they catch it
        # the team's loaded turrets a short run away: back to them, shooting (the user, 2026-10-26); far off: it fights
        lua.execute("""
            own = {}
            settings = {global={['npc-factions-scenario']={value='team'}}}
            cover_at = {x=40, y=0}
            package.loaded['scripts.team_war'].cover = function() return cover_at and {position=cover_at} end
            function biter(speed) return {valid=true, type='unit', speed=speed, prototype={speed=speed},
                position={x=-10, y=0}} end""")
        self.assertFalse(lua.eval("C.cornered(npc, biter(0.2))"))
        lua.execute("cover_at = {x=90, y=0}")
        self.assertTrue(lua.eval("C.cornered(npc, biter(0.2))"))
        lua.execute("cover_at = nil")
        self.assertTrue(lua.eval("C.cornered(npc, biter(0.2))"))

    def test_out_of_their_sight_it_turns_hard_to_one_side(self):
        lua = combat()
        lua.execute("""
            npc.home = {x=0, y=-500}
            npc.character = {position={x=-100, y=0},
                surface={find_non_colliding_position=function(_, p) return p end}}
            state = {breaking={people=true, from={x=0, y=0}}}
            math.random = function() return 0.9 end""")
        self.assertTrue(lua.eval("C.break_turn(npc, state, 100)"))
        self.assertEqual((lua.eval("state.goal.x"), lua.eval("state.goal.y")), (-100, -60))   # toward home
        self.assertFalse(lua.eval("C.break_turn(npc, state, 200)"))                      # once only
        lua.execute("state = {breaking={people=true, from={x=0, y=0}}}; math.random = function() return 0.1 end")
        lua.eval("C.break_turn(npc, state, 100)")
        self.assertEqual(lua.eval("state.goal.y"), 60)                                   # now and then the other
        lua.execute("""state = {breaking={people=true, from={x=0, y=0}}}
            npc.seen_dangers = {list={{x=-100, y=60, r=30}}}""")
        lua.eval("C.break_turn(npc, state, 100)")
        self.assertEqual(lua.eval("state.goal.y"), -60)                                  # not into a nest
        lua.execute("state = {breaking={people=false, from={x=0, y=0}}}")
        self.assertFalse(lua.eval("C.break_turn(npc, state, 100)"))                      # biters: no need

    def test_moves_in_a_fight_by_rule(self):
        lua = combat()
        lua.execute("""
            options = {{id='retreat'}, {id='hold'}, {id='advance'}, {id='kite'}, {id='strafe_left'}}
            function pick(d, mobile) return options[C.rule_pick(options, d, 6, 16, mobile)].id end
        """)
        self.assertEqual(lua.eval("pick(4, true)"), "kite")           # a biter too close: back off
        self.assertEqual(lua.eval("pick(10, true)"), "hold")          # in range: stand and shoot
        self.assertEqual(lua.eval("pick(22, true)"), "advance")       # too far: close in
        self.assertEqual(lua.eval("pick(4, false)"), "hold")          # a building does not chase

    def test_losing_a_fight_it_breaks_contact_out_of_their_sight_toward_home_and_round_nests(self):
        lua = combat()
        lua.execute("""
            npc.home = {x=-500, y=0}
            function away(tx, ty) local p, why = C.away(npc, {x=0, y=0}, {x=tx, y=ty}, 0); return {p.x, p.y, why} end""")
        x, y, why = lua.eval("away(10, 0)").values()
        self.assertEqual((round(x), round(y), why), (-20, 0, "local_away_from_threat"))   # a step back only
        lua.execute("npc.combat.breaking = {people=false}")
        x, y, why = lua.eval("away(10, 0)").values()
        self.assertEqual((round(x), round(y), why), (-48, 0, "break_contact"))            # biters lose it
        lua.execute("npc.combat.breaking = {people=true}")
        x, y, _ = lua.eval("away(10, 0)").values()
        self.assertEqual((round(x), round(y)), (-100, 0))                                 # off their screen
        lua.execute("npc.home = {x=500, y=0}")                                            # home lies past them
        x, y, _ = lua.eval("away(10, 0)").values()
        self.assertEqual((round(x), round(y)), (-100, 0))
        lua.execute("npc.home = {x=0, y=-500}")                                           # home to one side
        x, y, _ = lua.eval("away(10, 0)").values()
        self.assertLess(x, -50)
        self.assertLess(y, -50)                                                           # bent toward it
        lua.execute("npc.home = nil; npc.seen_dangers = {list={{x=-100, y=0, r=40}}}")     # a nest that way
        x, y, _ = lua.eval("away(10, 0)").values()
        self.assertGreater((x + 100) ** 2 + y ** 2, 48 ** 2)                              # round it


class ScoutTests(unittest.TestCase):
    def test_scouting_never_walks_into_a_nests_range(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "scouting.lua").read_text(encoding="utf-8")
        lua.execute("""
            package.preload['scripts.team_world'] = function() return {active=function() return false end} end
            S = assert(load(source))()
            nest = {x=100, y=0}
            surface = {
                count_entities_filtered=function(f)
                    return ((f.position.x - nest.x)^2 + (f.position.y - nest.y)^2 <= f.radius^2) and 1 or 0
                end,
                find_non_colliding_position=function(_, p) return p end}
            npc = {home={x=0, y=0}, character={position={x=0, y=0}, surface=surface},
                   scout={points={{x=200, y=0}, {x=0, y=150}}, index=1}}
            S.patches = function() return {} end""")
        job = lua.eval("(S.next(npc, 1))")
        self.assertEqual((job.position.x, job.position.y), (0, 150))     # not past the nest at (100, 0)


def equipment():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "combat_equipment.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.combat_squads'] = function() return {} end
        E = assert(load(source))()
        function plan(kind, d, count)
            local out = {}
            for _, t in ipairs(E.throws({type=kind}, d, count)) do
                if d >= t[5] and d <= t[2] then out[#out + 1] = t[1][1] end
            end
            return table.concat(out, ' ')
        end
        function cover(kind)
            local out = {}
            for _, t in ipairs(E.cover_throws({type=kind})) do out[#out + 1] = t[1][1] end
            return table.concat(out, ' ')
        end""")
    return lua


class ThrowTests(unittest.TestCase):
    def test_what_it_throws_at_what(self):
        lua = equipment()
        self.assertEqual(lua.eval("plan('turret', 20, 1)"), "poison-capsule destroyer-capsule".split()[0])  # a worm
        self.assertEqual(lua.eval("plan('unit', 12, 4)"), "cluster-grenade destroyer-capsule")   # a group
        self.assertEqual(lua.eval("plan('unit', 5, 4)"), "destroyer-capsule")                  # too close to blow up
        self.assertEqual(lua.eval("plan('unit', 12, 1)"), "destroyer-capsule")                 # one biter: guns
        self.assertEqual(lua.eval("plan('unit-spawner', 14, 1)"), "cluster-grenade destroyer-capsule")   # a nest
        self.assertEqual(lua.eval("plan('character', 20, 1)"), "slowdown-capsule")             # a person
        self.assertEqual(lua.eval("plan('character', 12, 2)"),
                         "cluster-grenade slowdown-capsule cluster-grenade destroyer-capsule")   # people together

    def test_a_grenade_never_goes_where_it_would_hit_its_own_turrets(self):
        # (O-9, the user's clear: grenades onto the nests, never onto its own creep turrets beside them)
        lua = equipment()
        lua.execute("""
            mine = {name='player', get_friend=function() return false end}
            other = {name='enemy', get_friend=function() return false end}
            built = {}
            character = {force=mine, surface={find_entities_filtered=function(f)
              local out = {}
              for _, e in ipairs(built) do
                if (e.position.x - f.position.x)^2 + (e.position.y - f.position.y)^2 <= f.radius^2 then out[#out+1] = e end
              end
              return out end}}""")
        self.assertFalse(lua.eval("E.own_in_blast(character, {x=0, y=0}, 8)"))
        lua.execute("built[1] = {valid=true, type='unit-spawner', force=other, position={x=2, y=0}}")   # theirs: fine
        self.assertFalse(lua.eval("E.own_in_blast(character, {x=0, y=0}, 8)"))
        lua.execute("built[3] = {valid=true, type='character', force=mine, position={x=1, y=0}}")  # itself: not a building
        self.assertFalse(lua.eval("E.own_in_blast(character, {x=0, y=0}, 8)"))
        lua.execute("built[2] = {valid=true, type='assembling-machine', force=mine, position={x=6, y=0}}")   # its own
        self.assertTrue(lua.eval("E.own_in_blast(character, {x=0, y=0}, 8)"))
        self.assertFalse(lua.eval("E.own_in_blast(character, {x=-10, y=0}, 8)"))      # thrown the far side: fine

    def test_getting_away_it_slows_people_down_or_leaves_them_something_else_to_shoot(self):
        lua = equipment()
        self.assertEqual(lua.eval("cover('character')"), "slowdown-capsule distractor-capsule")
        self.assertEqual(lua.eval("cover('unit')"), "distractor-capsule")


if __name__ == "__main__":
    unittest.main()
