"""Driving on autopilot (drive.lua): steering, the route a vehicle may take, and a drive from boarding to
parking, in Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "drive.lua").read_text(encoding="utf-8")
    lua.globals().routes = (ROOT / "mod" / "scripts" / "combat_routes.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.combat_routes'] = function() return assert(load(routes))() end
        Routes = require('scripts.combat_routes')
        -- (the drive asks for its route a slice a tick; here at once, whatever planner a test sets)
        Routes.request = function(...) return Routes.for_npc(...) end
        D = assert(load(source))()
        defines = {riding={acceleration={nothing=0, accelerating=1, braking=2, reversing=3},
                           direction={left=0, straight=1, right=2}},
                   direction={north=0, northeast=2, east=4, southeast=6, south=8, southwest=10, west=12, northwest=14}}
        game = {tick=0, forces={}}
        storage = {npcs={}}
        held = {car=1, coal=50}
        main = {get_item_count=function(n) return held[n] or 0 end,
                remove=function(s) local n = math.min(s.count, held[s.name] or 0); held[s.name] = (held[s.name] or 0) - n; return n end,
                insert=function(s) held[s.name] = (held[s.name] or 0) + s.count; return s.count end}
        burner_inv = {}
        burner = {remaining_burning_fuel=0, inventory={
            get_item_count=function(n) return burner_inv[n] or 0 end,
            is_empty=function() for _, n in pairs(burner_inv) do if n > 0 then return false end end return true end,
            insert=function(s) burner_inv[s.name] = (burner_inv[s.name] or 0) + s.count; return s.count end}}
        requests = {}
        trees, rocks = 0, 0
        surface = {
            find_entities_filtered=function() return {} end,
            count_entities_filtered=function(f)
                local kinds = type(f.type) == 'table' and f.type or {f.type}
                local n = 0
                for _, k in ipairs(kinds) do
                    if k == 'tree' then n = n + trees elseif k == 'simple-entity' then n = n + rocks end
                end
                return n
            end,
            find_non_colliding_position=function(_, p) return {x=p.x + 5, y=p.y} end,
            request_path=function(spec) requests[#requests+1] = spec; return #requests end}
        function surface.create_entity(spec)
            car = {valid=true, type='car', name=spec.name, force=spec.force, position={x=spec.position.x, y=spec.position.y},
                   surface=surface, orientation=0.25, speed=0, health=600, max_health=600, burner=burner,
                   prototype={collision_mask={}}}
            function car.get_driver() return car.driver end
            function car.get_passenger() return nil end
            function car.set_driver(c) car.driver = c; character.vehicle = c and car or nil end
            return car
        end
        force = {name='npc-faction-1'}
        character = {valid=true, position={x=0, y=0}, surface=surface, get_main_inventory=function() return main end}
        npc = {id=1, force=force, character=character, home={x=-500, y=0}}
        storage.npcs[1] = npc
    """)
    return lua


class SteerTests(unittest.TestCase):
    def test_it_turns_toward_the_point_and_slows_for_sharp_turns(self):
        lua = runtime()
        steer = lambda o, v, x, y, slow="false": tuple(lua.eval(f"{{D.steer({o}, {v}, {{x=0, y=0}}, {{x={x}, y={y}}}, {slow})}}").values())
        self.assertEqual(steer(0.25, 0.3, 50, 0), ("accelerating", "straight"))   # facing east, it is east
        self.assertEqual(steer(0.25, 0.3, 50, 20)[1], "right")                   # a bit south: right
        self.assertEqual(steer(0.25, 0.3, 50, -20)[1], "left")
        self.assertEqual(steer(0.25, 0.5, -5, 0), ("braking", "left"))           # just behind it: slow, turn
        self.assertEqual(steer(0.25, 0.5, -60, 0)[0], "accelerating")          # far behind: room to turn
        tank = lua.eval("D.TURN_RATE.tank")
        self.assertNotEqual(tuple(lua.eval(f"{{D.steer(0.25, 0.12, {{x=0, y=0}}, {{x=2, y=4}}, false, {tank})}}").values())[0],
                            "accelerating")                                      # a tank turns wide: slower
        self.assertEqual(steer(0.25, 0.33, 50, 0, "0.3")[0], "nothing")         # a little too fast: off the pedal
        self.assertEqual(steer(0.25, 0.5, 50, 0, "true")[0], "braking")          # near the end: slow


class VehicleRouteTests(unittest.TestCase):
    def test_a_car_drives_past_nests_but_not_worms_and_leaves_biters_behind(self):
        lua = runtime()
        lua.execute("""
            threats = {{x=0, y=0, r=48, nest=true, kind='unit-spawner'},
                       {x=100, y=0, r=40, nest=true, kind='turret'},
                       {x=200, y=0, r=40, nest=true, moving=true},
                       {x=300, y=0, r=24, nest=true, blocked=true}}
            car = Routes.for_vehicle(threats, 'car')
            tank = Routes.for_vehicle(threats, 'tank')""")
        self.assertEqual(lua.eval("#car"), 3)                         # the moving group is left behind
        self.assertEqual(lua.eval("car[1].r"), 14)                    # a nest: only not to hit it
        self.assertEqual(lua.eval("car[2].r"), 40)                    # a worm: out of its reach
        self.assertEqual(lua.eval("car[3].r"), 24)                    # water stays water
        self.assertEqual(lua.eval("tank[1].r"), 6)
        self.assertEqual(lua.eval("tank[2].r"), 24)                   # a tank goes nearer worms
        self.assertEqual(lua.eval("#Routes.for_vehicle(threats, nil)"), 4)   # on foot: all of them


class WoodsTests(unittest.TestCase):
    def test_a_car_goes_round_thick_woods_on_foot_they_are_no_bother(self):
        lua = runtime()
        lua.execute("""
            function forest(p) return p.x > 150 and p.x < 250 and math.abs(p.y) < 80 end
            surface.count_entities_filtered = function(f)
                if f.type == 'tree' then return forest(f.position) and 20 or 0 end
                return 0
            end
            npc.home = nil
            function through(points)
                local a, hit = {x=0, y=0}, false
                for _, b in ipairs(points) do
                    for i = 1, 40 do
                        local p = {x=a.x + (b.x - a.x) * i / 40, y=a.y + (b.y - a.y) * i / 40}
                        if forest(p) then hit = true end
                    end
                    a = b
                end
                return hit
            end""")
        self.assertTrue(lua.eval("through(Routes.for_npc(npc, {x=400, y=0}).points)"))         # on foot: through
        self.assertFalse(lua.eval("through(Routes.for_npc(npc, {x=400, y=0}, nil, nil, 0, 'car').points)"))


class DriveTests(unittest.TestCase):
    def test_from_its_pockets_into_the_car_along_the_path_and_out_where_it_stops(self):
        lua = runtime()
        self.assertTrue(lua.eval("D.can(npc, {x=1000, y=0})"))       # a car and coal in its pockets
        self.assertFalse(lua.eval("D.can(npc, {x=100, y=0})"))       # too short a trip to drive
        lua.execute("D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)")
        self.assertEqual(lua.eval("held.car"), 0)                     # put down beside it
        self.assertEqual(lua.eval("character.walking_state.walking"), True)   # walking to it
        lua.execute("character.position = {x=4, y=0}; D.tick(npc, 2)")
        self.assertTrue(lua.eval("car.driver == character"))          # in
        self.assertEqual(lua.eval("burner_inv.coal"), 20)             # fuelled from its pockets
        lua.execute("D.tick(npc, 3)")                                 # the route, then a path asked for the leg
        self.assertEqual(lua.eval("npc.drive.phase"), "wait")
        self.assertEqual(lua.eval("requests[1].bounding_box[2][1]"), 4)    # a wide berth first
        lua.execute("""
            D.on_path_finished({id=1, path={{position={x=20, y=0}}, {position={x=60, y=0}}, {position={x=98, y=0}}}})
            D.tick(npc, 4)""")
        self.assertEqual(lua.eval("npc.drive.phase"), "drive")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 1)   # off it goes
        # stuck: no progress for a while, it backs out turned the other way
        for t in range(5, 100):
            lua.execute("D.tick(npc, %d)" % t)
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 3)
        self.assertEqual(lua.eval("npc.drive.stuck"), 1)
        self.assertGreater(lua.eval("npc.drive.careful_until"), 600)       # then carefully for a while
        for t in range(100, 160):
            lua.execute("D.tick(npc, %d)" % t)
        self.assertEqual(lua.eval("npc.drive.phase"), "wait")              # backed out: a fresh path asked
        self.assertEqual(lua.eval("#requests"), 2)
        # far along: the end near, it stops, gets out and leaves the car parked
        lua.execute("car.position = {x=940, y=0}; car.speed = 0.4; D.tick(npc, 500)")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 2)   # braking
        lua.execute("car.speed = 0; D.tick(npc, 501)")
        self.assertEqual(lua.eval("D.state(npc)"), "arrived")
        self.assertIsNone(lua.eval("car.driver"))
        self.assertTrue(lua.eval("npc.parked == car"))

    def test_among_trees_it_slows_and_without_a_path_tries_a_shorter_leg(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)""")
        first = lua.eval("requests[1].goal.x")
        lua.execute("D.on_path_finished({id=1, path=nil}); D.tick(npc, 4)")      # no path for the leg
        self.assertEqual(lua.eval("requests[2].bounding_box[2][1]"), 2.5)       # for a narrower box
        self.assertEqual(lua.eval("requests[2].goal.x"), first)
        lua.execute("D.on_path_finished({id=2, path=nil}); D.tick(npc, 5)")      # nor that: shorter
        self.assertLess(lua.eval("requests[3].goal.x"), first)
        lua.execute("""
            D.on_path_finished({id=3, path={{position={x=30, y=0}}}})
            trees = 20; car.speed = 0.3; D.tick(npc, 30)""")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 1)       # among trees: on at speed
        lua.execute("rocks = 3; car.speed = 0.4; npc.drive.ahead_at = 0; D.tick(npc, 31)")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 2)       # rocks by the way: slow

    def test_it_walks_out_of_its_base_before_driving_and_parks_outside_it_coming_back(self):
        lua = runtime()
        lua.execute("npc.home = {x=10, y=0}")
        self.assertFalse(lua.eval("D.can(npc, {x=1000, y=0})"))      # in its base: out on foot first
        lua.execute("character.position = {x=80, y=0}")
        self.assertTrue(lua.eval("D.can(npc, {x=1000, y=0})"))
        lua.execute("""
            D.start(npc, {x=10, y=0}, 30, 0); D.tick(npc, 1)
            character.position = {x=84, y=0}; D.tick(npc, 2)""")         # in, and heading home
        lua.execute("car.position = {x=70, y=0}; car.speed = 0; D.tick(npc, 3)")
        self.assertEqual(lua.eval("D.state(npc)"), "arrived")           # parked outside its base
        self.assertEqual(lua.eval("D.vehicle_at(npc).x"), 70)            # where to walk to next time

    def test_a_leg_with_no_way_at_all_is_blocked_and_the_route_planned_again(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            for i = 1, 4 do D.on_path_finished({id=#requests, path=nil}); D.tick(npc, 3 + i) end""")
        self.assertGreater(lua.eval("npc.seen_dangers.count"), 0)        # that spot: blocked
        self.assertEqual(lua.eval("npc.drive.phase"), "wait")             # a fresh way asked for

    def test_a_busy_pathfinder_is_asked_again_without_giving_up(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            for i = 1, 20 do D.on_path_finished({id=#requests, try_again_later=true}); game.tick = 100 * i; D.tick(npc, 100 * i) end""")
        self.assertEqual(lua.eval("D.state(npc)"), "driving")              # not given up
        self.assertIsNone(lua.eval("npc.drive.no_path"))
        self.assertGreater(lua.eval("#requests"), 10)                     # asked again and again

    def test_trouble_is_counted_afresh_once_past_it_and_going_nowhere_ends_it(self):
        lua = runtime()
        lua.execute("""
            character.mine_entity = function(e) held.car = held.car + 1; e.valid = false; return true end
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            for i = 1, 6 do                         -- no way at all (a spot blocked), then a way: six times
                for j = 1, 3 do D.on_path_finished({id=#requests, path=nil}); D.tick(npc, 10 * i + j) end
                D.on_path_finished({id=#requests, path={{position={x=60, y=0}}}})
                car.position = {x=58, y=0}; D.tick(npc, 10 * i + 5)      -- that leg driven, the next asked
            end""")
        self.assertEqual(lua.eval("D.state(npc)"), "driving")              # each got past: driving on
        self.assertEqual(lua.eval("#requests"), 25)
        lua.execute("""
            npc.drive.stuck = 5
            D.on_path_finished({id=#requests, path={{position={x=60, y=0}}}}); D.tick(npc, 70)""")
        self.assertEqual(lua.eval("npc.drive.stuck"), 0)                  # the leg driven: stuck afresh
        lua.execute("D.tick(npc, 80 + 10800)")                            # no nearer for three minutes
        self.assertEqual(lua.eval("npc.drive.why"), "no_progress")
        self.assertEqual(lua.eval("held.car"), 1)                         # the car picked up, not left
        self.assertIsNone(lua.eval("npc.parked"))

    def test_a_leg_starts_where_its_box_fits_not_in_a_tree(self):
        lua = runtime()
        lua.execute("""
            surface.count_tiles_filtered = function() return 0 end
            surface.count_entities_filtered = function(f)               -- a tree just north of 0,0
                local a = f.area
                if a and a[1][1] <= 0 and a[2][1] >= 0 and a[1][2] <= -1 and a[2][2] >= -1 then return 1 end
                return 0
            end
            car = {surface=surface}""")
        self.assertEqual(lua.eval("D.path_start(car, {x=0, y=0}).y"), 3)       # 3 tiles behind, clear of it
        self.assertAlmostEqual(lua.eval("D.path_start(car, {x=0, y=0}).x"), 0)
        self.assertEqual(lua.eval("D.path_start(car, {x=0, y=-10}).y"), -10)   # in the open: where it is
        lua.execute("""
            surface.count_tiles_filtered = nil
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)""")
        self.assertIn("start for 1000,0", lua.eval("npc.drive_trace[1]"))       # its drives traced

    def test_it_looks_about_it_as_it_drives_and_plans_round_what_is_new(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=1, path={{position={x=60, y=0}}}}); D.tick(npc, 4)
            Routes.new_sighting = function(_, _, vehicle) driving = vehicle; return true end
            D.tick(npc, 200)""")
        self.assertEqual(lua.eval("#requests"), 2)                        # a worm seen: planned again
        self.assertTrue(lua.eval("driving"))                              # (biters on the move: left behind)
        self.assertIn("new danger seen", lua.eval("table.concat(npc.drive_trace, ';')"))

    def test_a_new_route_waits_for_the_land_along_it_to_be_made(self):
        lua = runtime()
        lua.execute("""
            made = true
            surface.is_chunk_generated = function() return made end
            surface.request_to_generate_chunks = function() end
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            made = false; npc.drive.route = nil; npc.drive.phase = 'plan'; car.speed = 0.3
            D.tick(npc, 2000); D.tick(npc, 2050)""")
        self.assertEqual(lua.eval("#requests"), 1)                        # not into land not yet made
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 2)
        lua.execute("made = true; D.tick(npc, 2200)")
        self.assertEqual(lua.eval("#requests"), 2)

    def test_under_fire_from_a_worm_it_backs_off_and_plans_round_it(self):
        lua = runtime()
        lua.execute("""
            force.get_friend = function() return false end
            force.get_cease_fire = function() return false end
            worm = {valid=true, name='medium-worm-turret', force={name='enemy'}, position={x=40, y=0}}
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=1, path={{position={x=60, y=0}}}}); D.tick(npc, 4)
            surface.find_entities_filtered = function(f)
                if f.type and f.type[2] == 'ammo-turret' then return {worm} end
                return {}
            end
            car.health = 400; D.tick(npc, 5)""")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 3)   # the worm ahead: straight back
        self.assertIn("under fire from medium-worm-turret", lua.eval("npc.drive_trace[#npc.drive_trace]"))
        lua.execute("car.health = 100; D.tick(npc, 6)")
        self.assertEqual(lua.eval("D.state(npc)"), "driving")              # not out beside it
        lua.execute("for t = 7, 40 do D.tick(npc, t) end")                # backing blocked: the other way
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 1)
        lua.execute("car.position = {x=-30, y=0}; D.tick(npc, 41)")      # out of its reach
        self.assertIsNone(lua.eval("npc.drive.flee"))
        self.assertEqual(lua.eval("#requests"), 2)                        # a route round it

    def test_under_fire_it_backs_out_the_way_it_came_steering_as_it_backs(self):
        lua = runtime()
        lua.execute("""
            force.get_friend = function() return false end
            force.get_cease_fire = function() return false end
            worm = {valid=true, name='medium-worm-turret', force={name='enemy'}, position={x=20, y=-40}, unit_number=7}
            D.start(npc, {x=20, y=-1000}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=1, path={{position={x=20, y=-200}}}})
            t = 4
            for x = 0, 20, 2 do car.position = {x=x, y=0}; D.tick(npc, t); t = t + 1 end
            car.orientation = 0                                   -- turned north
            for y = -2, -10, -2 do car.position = {x=20, y=y}; D.tick(npc, t); t = t + 1 end
            surface.find_entities_filtered = function(f)
                if f.type and f.type[2] == 'ammo-turret' then return {worm} end
                return {}
            end
            car.health = 400; D.tick(npc, 30)""")
        self.assertIn("medium-worm-turret 30 off, not known", lua.eval("table.concat(npc.drive_trace, ';')"))
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 3)   # the worm ahead: back
        self.assertEqual(lua.eval("car.riding_state.direction"), 1)      # straight back, the way it came
        lua.execute("for i, y in ipairs({-8, -6, -4, -2, -1}) do car.position = {x=20, y=y}; D.tick(npc, 30 + i) end")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 3)
        self.assertEqual(lua.eval("car.riding_state.direction"), 0)      # its way turned: reversing, wheel left

    def test_stuck_twice_in_thick_woods_it_backs_out_the_way_it_came(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=1, path={{position={x=60, y=0}}}})
            t = 4
            for x = 0, 30, 2 do car.position = {x=x, y=x > 20 and x - 20 or 0}; D.tick(npc, t); t = t + 1 end
            trees = 20
            for i = 1, 600 do
                D.tick(npc, t); t = t + 1
                if npc.drive.backing and npc.drive.backing.along then break end
            end""")
        self.assertIn("thick woods, out the way it came", lua.eval("npc.drive_trace[#npc.drive_trace]"))
        lua.execute("D.tick(npc, t)")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 3)
        self.assertEqual(lua.eval("car.riding_state.direction"), 0)      # back along its way in: wheel left

    def test_the_next_leg_is_asked_for_ahead_and_driven_on_without_stopping(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=1, path={{position={x=50, y=0}}, {position={x=101, y=0}}}}); D.tick(npc, 4)
            car.position = {x=55, y=0}; D.tick(npc, 5)""")
        self.assertEqual(lua.eval("#requests"), 2)                        # the next leg, asked ahead
        self.assertEqual(lua.eval("requests[2].start.x"), 101)            # from where this one ends
        lua.execute("""
            D.on_path_finished({id=2, path={{position={x=150, y=0}}, {position={x=197, y=0}}}})
            car.position = {x=99, y=0}; car.speed = 0.4; D.tick(npc, 6)""")
        self.assertEqual(lua.eval("npc.drive.phase"), "drive")            # on along it
        self.assertEqual(lua.eval("npc.drive.path[2].x"), 197)
        self.assertEqual(lua.eval("#requests"), 2)
        lua.execute("D.tick(npc, 7)")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 1)   # not stopping

    def test_a_pathfinder_busy_for_long_and_it_drives_straight_for_the_leg(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            for i = 1, 21 do D.on_path_finished({id=#requests, try_again_later=true}); game.tick = 100 * i; D.tick(npc, 100 * i) end""")
        self.assertEqual(lua.eval("npc.drive.phase"), "drive")
        self.assertEqual(lua.eval("#npc.drive.path"), 1)                  # straight for the leg's end
        self.assertIn("pathfinder busy", lua.eval("table.concat(npc.drive_trace, ';')"))

    def test_a_long_way_round_is_progress_and_a_trip_slower_than_walking_is_given_up(self):
        lua = runtime()
        lua.execute("""
            character.mine_entity = function(e) e.valid = false; return true end
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            for i = 1, 10 do car.position = {x=5, y=100 * i}; D.tick(npc, 3 + 1000 * i) end""")
        self.assertEqual(lua.eval("D.state(npc)"), "driving")              # no nearer, but on its way
        lua.execute("D.tick(npc, 3 + 2 * 995 / 0.15 + 3600 + 10)")
        self.assertEqual(lua.eval("npc.drive.why"), "too_slow")

    def test_wedged_on_a_rock_it_gets_out_mines_it_and_drives_on(self):
        lua = runtime()
        lua.execute("""
            rock = {valid=true, type='simple-entity', name='big-rock', minable=true, force={name='neutral'},
                    position={x=7, y=1}}
            surface.find_entities_filtered = function(f)
                if f.type and f.type[2] == 'simple-entity' and not f.force then return {rock} end
                return {}
            end
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=1, path={{position={x=60, y=0}}}}); D.tick(npc, 4)
            for t = 5, 200 do D.tick(npc, t) end                      -- stuck once: it backs out
            for round = 1, 2 do                                       -- and again, at the same spot
                npc.drive.backing = nil; npc.drive.phase = 'drive'; npc.drive.path = {{x=60, y=0}}
                npc.drive.held = nil
                for t = 200 * round + 1, 200 * round + 120 do D.tick(npc, t) end
            end""")                                                    # the third time, the rock on it
        self.assertEqual(lua.eval("npc.drive.phase"), "clear")
        self.assertIsNone(lua.eval("car.driver"))                         # out of the car
        self.assertTrue(lua.eval("character.walking_state.walking"))      # up to it first
        lua.execute("character.position = {x=6, y=1}; D.tick(npc, 700)")
        self.assertTrue(lua.eval("character.selected == rock"))
        self.assertTrue(lua.eval("character.mining_state.mining"))         # mining it
        lua.execute("rock.valid = false; D.tick(npc, 701)")              # mined
        lua.execute("character.position = {x=4, y=0}; D.tick(npc, 702)")
        self.assertTrue(lua.eval("car.driver == character"))              # back in
        self.assertFalse(lua.eval("character.mining_state.mining"))

    def test_wedged_on_a_rock_among_biters_it_stays_in_and_backs_out_the_way_it_came(self):
        lua = runtime()
        lua.execute("""
            rock = {valid=true, type='simple-entity', name='big-rock', minable=true, force={name='neutral'},
                    position={x=7, y=1}}
            surface.find_entities_filtered = function(f)
                if f.type and f.type[2] == 'simple-entity' and not f.force then return {rock} end
                return {}
            end
            local count = surface.count_entities_filtered
            surface.count_entities_filtered = function(f) if f.type == 'unit' then return 1 end return count(f) end
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=1, path={{position={x=60, y=0}}}}); D.tick(npc, 4)
            for t = 5, 200 do D.tick(npc, t) end
            for round = 1, 2 do
                npc.drive.backing = nil; npc.drive.phase = 'drive'; npc.drive.path = {{x=60, y=0}}
                npc.drive.held = nil
                for t = 200 * round + 1, 200 * round + 120 do D.tick(npc, t) end
            end""")
        self.assertTrue(lua.eval("car.driver == character"))              # not out among them
        self.assertTrue(lua.eval("npc.drive.backing.along"))              # backing out the way it came

    def test_a_raid_is_driven_only_by_a_clear_way_and_their_defenses_are_spared(self):
        lua = runtime()
        lua.execute("""
            clear = false
            character.mine_entity = function(e) held.car = held.car + 1; e.valid = false; return true end
            Routes.for_npc = function(_, to, spare, nests, wide, vehicle)
                asked = {spare=spare, nests=nests, vehicle=vehicle}
                return {points={{x=to.x, y=to.y}}, k=1, safe=clear, known={}, goal=to}
            end
            D.start(npc, {x=1000, y=0}, 70, 0, {spare=110, strict=true}); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)""")
        self.assertEqual(lua.eval("asked.spare"), 110)
        self.assertEqual(lua.eval("asked.vehicle"), "car")
        self.assertEqual(lua.eval("npc.drive.why"), "unsafe")              # a raid: not by the least bad way
        self.assertEqual(lua.eval("held.car"), 1)                          # the car taken along
        lua.execute("""
            npc.drive = nil; npc.parked = nil; car = nil; character.vehicle = nil
            character.position = {x=0, y=0}
            D.start(npc, {x=1000, y=0}, 30, 10); D.tick(npc, 11)
            character.position = {x=4, y=0}; D.tick(npc, 12); D.tick(npc, 13)""")
        self.assertEqual(lua.eval("D.state(npc)"), "driving")              # home: it goes all the same

    def test_the_last_leg_driven_is_arriving_though_its_path_ends_short_of_the_goal(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=300, y=0}, 8, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            D.on_path_finished({id=#requests, path={{position={x=150, y=0}}, {position={x=288, y=0}}}}); D.tick(npc, 4)
            car.position = {x=150, y=0}; D.tick(npc, 5)
            car.position = {x=287, y=0}; car.speed = 0.3; D.tick(npc, 6)""")      # 13 short of the goal
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 2)            # stopping there
        lua.execute("car.speed = 0; D.tick(npc, 7)")
        self.assertEqual(lua.eval("D.state(npc)"), "arrived")

    def test_the_same_spot_twice_without_a_way_and_it_asks_for_one_well_on(self):
        lua = runtime()
        lua.execute("""
            Routes.for_npc = function(_, to)
                return {points={{x=60, y=0}, {x=300, y=0}, {x=600, y=0}, {x=to.x, y=to.y}}, k=1, safe=true, known={}, goal=to}
            end
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            for i = 1, 8 do D.on_path_finished({id=#requests, path=nil}); D.tick(npc, 3 + i) end""")
        self.assertTrue(lua.eval("npc.drive.far"))                          # the same spot twice: far
        self.assertGreater(lua.eval("requests[#requests].goal.x"), 200)    # well on along the route
        self.assertEqual(lua.eval("requests[#requests].bounding_box[2][1]"), 2.5)

    def test_back_and_forth_between_two_places_and_that_spot_is_blocked(self):
        lua = runtime()
        lua.execute("""
            D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            ends = {{x=60, y=0}, {x=60, y=50}, {x=61, y=1}, {x=60, y=50}, {x=60, y=1}}
            for i, e in ipairs(ends) do
                npc.drive.phase = 'plan'
                local from = car.position
                Routes.for_npc = function(_, to) return {points={{x=e.x, y=e.y}, {x=to.x, y=to.y}}, k=1, safe=true, known={}, goal=to} end
                npc.drive.route = nil
                D.tick(npc, 10 * i)
                car.position = {x=e.x, y=e.y}
            end""")
        self.assertIn("back and forth", lua.eval("table.concat(npc.drive_trace, ';')"))
        self.assertGreater(lua.eval("#npc.drive_legs"), 2)

    def test_no_fuel_or_a_wreck_and_it_goes_on_foot(self):
        lua = runtime()
        lua.execute("held.coal = 0")
        self.assertFalse(lua.eval("D.can(npc, {x=1000, y=0})"))       # no fuel: walked
        lua.execute("""
            held.coal = 50; D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
            character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
            car.health = 100; car.speed = 0.4; D.tick(npc, 4); D.tick(npc, 5)""")
        self.assertEqual(lua.eval("D.state(npc)"), "driving")              # moving: it drives on
        lua.execute("car.speed = 0; car.health = 90; D.tick(npc, 6); D.tick(npc, 7)")
        self.assertEqual(lua.eval("D.state(npc)"), "failed")              # stopped and still hit: out
        self.assertEqual(lua.eval("npc.drive.why"), "vehicle_wrecked")
        self.assertIsNone(lua.eval("car.driver"))


def hurt_on_a_trip(lua):
    """Into the car, on the way east, with ten repair packs and biters about it."""
    lua.execute("""
        packs = {valid_for_read=true, count=10, durability=300}
        function packs.drain_durability(n)
            packs.durability = packs.durability - n
            while packs.durability <= 0 and packs.count > 0 do packs.count = packs.count - 1; packs.durability = packs.durability + 300 end
            packs.valid_for_read = packs.count > 0
        end
        main.find_item_stack = function(n) if n == 'repair-pack' and packs.valid_for_read then return packs end end
        character.health = 250
        defines.shooting = {not_shooting=0, shooting_enemies=1, shooting_selected=2}
        foes = {}
        surface.find_entities_filtered = function(f)
            local kinds = type(f.type) == 'table' and f.type or {f.type}
            for _, k in ipairs(kinds) do if k == 'unit' then
                local out = {}
                for _, e in ipairs(foes) do
                    local dx, dy = e.position.x - f.position.x, e.position.y - f.position.y
                    if dx * dx + dy * dy <= f.radius * f.radius then out[#out+1] = e end
                end
                return out
            end end
            return {}
        end
        function biter(x, y) return {valid=true, name='small-biter', type='unit', force={name='enemy'}, position={x=x, y=y}} end
        D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)
        character.position = {x=4, y=0}; D.tick(npc, 2); D.tick(npc, 3)
        D.on_path_finished({id=1, path={{position={x=60, y=0}}}}); D.tick(npc, 4)
        car.position = {x=10, y=0}""")


class MendTests(unittest.TestCase):
    def test_badly_hurt_among_biters_it_drives_off_stops_when_clear_and_mends_it(self):
        lua = runtime()
        hurt_on_a_trip(lua)
        lua.execute("foes = {biter(40, 2), biter(42, -2)}; car.health = 250; car.speed = 0.4; D.tick(npc, 5)")
        self.assertEqual(lua.eval("npc.drive.goal.x"), -86)                  # away from them, not on at them
        self.assertEqual(lua.eval("D.state(npc)"), "driving")
        lua.execute("foes = {}; car.position = {x=-40, y=0}; for t = 6, 199 do D.tick(npc, t) end")
        self.assertIsNotNone(lua.eval("car.driver"))                         # hit lately: on a while yet
        lua.execute("D.tick(npc, 200)")
        self.assertEqual(lua.eval("car.riding_state.acceleration"), 2)       # clear of them: stopping
        lua.execute("car.speed = 0; for t = 201, 260 do D.tick(npc, t) end")
        self.assertIsNotNone(lua.eval("car.driver"))                         # a while in the seat first
        lua.execute("for t = 261, 300 do D.tick(npc, t) end")
        self.assertIsNone(lua.eval("car.driver"))                            # out
        self.assertEqual(lua.eval("npc.drive.phase"), "mend")
        lua.execute("character.position = {x=-38, y=1}; for t = 301, 500 do D.tick(npc, t) end")
        self.assertGreaterEqual(lua.eval("car.health"), 570)                 # mended (at the pack's speed)
        self.assertEqual(lua.eval("packs.count"), 9)                         # a pack's worth used
        self.assertTrue(lua.eval("car.driver == character"))                 # back in
        self.assertEqual(lua.eval("npc.drive.goal.x"), 1000)                 # on with the trip
        self.assertEqual(lua.eval("npc.drive.mends"), 1)

    def test_something_coming_while_it_mends_and_it_is_back_in_and_away(self):
        lua = runtime()
        hurt_on_a_trip(lua)
        # a rock, nothing about: no going off anywhere, it stops on the way and mends it there
        lua.execute("car.health = 250; car.speed = 0.4; D.tick(npc, 5)")
        self.assertEqual(lua.eval("npc.drive.goal.x"), 1000)
        lua.execute("for t = 6, 186 do D.tick(npc, t) end; car.speed = 0; for t = 187, 300 do D.tick(npc, t) end")
        self.assertEqual(lua.eval("npc.drive.phase"), "mend")
        lua.execute("character.position = {x=11, y=1}; D.tick(npc, 301); foes = {biter(30, 0)}; D.tick(npc, 311)")
        self.assertTrue(lua.eval("car.driver == character"))                 # back in
        lua.execute("D.tick(npc, 312)")
        self.assertLess(lua.eval("npc.drive.goal.x"), 0)                     # and away from it
        self.assertIn("back in, away", lua.eval("table.concat(npc.drive_trace, ';')"))


class GameDataTests(unittest.TestCase):
    def test_what_it_drives_and_burns_comes_from_the_game_data_a_modded_game_included(self):
        lua = runtime()
        lua.execute("""
            local function car_like(hp, weight, rot, item)
                return {get_max_health=function() return hp end, weight=weight, rotation_speed=rot,
                        items_to_place_this={{name=item, count=1}},
                        burner_prototype={fuel_categories={chemical=true}}}
            end
            entities = {car=car_like(450, 700, 0.015, 'car'), tank=car_like(2000, 20000, 0.0035, 'tank'),
                        ['armoured-car']=car_like(900, 3000, 0.01, 'armoured-car-item'),
                        ['no-item-car']={get_max_health=function() return 5000 end, items_to_place_this={}}}
            local function fuel(cats, top, acc, value)
                return {fuel_categories=cats, fuel_top_speed_multiplier=top, fuel_acceleration_multiplier=acc, fuel_value=value}
            end
            prototypes = {entity=entities,
                get_entity_filtered=function() return entities end,
                get_item_filtered=function() return {
                    coal=fuel({'chemical'}, 1, 1, 4e6), ['solid-fuel']=fuel({'chemical'}, 1.05, 1.2, 12e6),
                    ['rocket-fuel']=fuel({'chemical'}, 1.15, 1.8, 1e8), spoilage=fuel({'chemical'}, 0.5, 0.5, 2.5e5),
                    ['uranium-fuel-cell']=fuel({'nuclear'}, 1, 1, 8e9), wood=fuel({'chemical'}, 1, 1, 2e6)} end}""")
        names = [lua.eval("D.vehicles()[%d].name" % i) for i in range(1, lua.eval("#D.vehicles()") + 1)]
        self.assertEqual(names, ["tank", "armoured-car", "car"])        # sturdiest first; one no item places: none
        self.assertTrue(lua.eval("D.kind_of('tank').heavy"))
        self.assertFalse(lua.eval("D.kind_of('armoured-car-item').heavy"))   # by its item too: light, it steers round trees
        self.assertAlmostEqual(lua.eval("D.kind_of('armoured-car').turn"), 0.01 * 2 * 3.141592653589793, places=6)
        fuels = [lua.eval("D.fuels('car')[%d]" % i) for i in range(1, lua.eval("#D.fuels('car')") + 1)]
        self.assertEqual(fuels, ["rocket-fuel", "solid-fuel", "coal", "wood"])   # fastest first; no spoilage, no cells
        lua.execute("held = {['armoured-car-item']=1, wood=5, coal=5}")
        self.assertEqual(lua.eval("D.kind(npc)"), "car")                 # what it would drive: light
        self.assertTrue(lua.eval("D.can(npc, {x=1000, y=0})"))
        lua.execute("D.start(npc, {x=1000, y=0}, 70, 0); D.tick(npc, 1)")
        self.assertEqual(lua.eval("car.name"), "armoured-car")            # put down by its entity's name
        self.assertEqual(lua.eval("held['armoured-car-item']"), 0)
        lua.execute("character.position = {x=4, y=0}; D.tick(npc, 2)")
        self.assertEqual(lua.eval("burner_inv.coal"), 5)                  # the better fuel of the two it has


def ring(lua, door_layers=1):
    """A wall ring round (-100, -100, 100, 100) with a 4-gate door in the middle of its north side."""
    lua.execute("""
        npc.design_history = {{keep={ring={-100, -100, 100, 100}, ring_gen=1}}}
        walls, gates = {}, {}
        for layer = 0, %d - 1 do
            local y = -99.5 + layer
            for x = -100, 99 do
                local p = {x=x + 0.5, y=y}
                if x >= -2 and x < 2 then gates[#gates+1] = {valid=true, type='gate', force=force, position=p, direction=4}
                else walls[#walls+1] = {valid=true, type='wall', force=force, position=p} end
            end
        end
        belts = {}
        local function within(e, a) return e.position.x >= a[1][1] and e.position.x <= a[2][1]
            and e.position.y >= a[1][2] and e.position.y <= a[2][2] end
        surface.find_entities_filtered = function(f)
            local out = {}
            local kinds = type(f.type) == 'table' and f.type or {f.type}
            for _, k in ipairs(kinds) do
                local list = k == 'gate' and gates or k == 'wall' and walls or {}
                for _, e in ipairs(list) do if f.area and within(e, f.area) then out[#out+1] = e end end
            end
            return out
        end
        local count = surface.count_entities_filtered
        surface.count_entities_filtered = function(f)
            if f.type and f.type[1] == 'transport-belt' then
                local n = 0
                for _, b in ipairs(belts) do if within(b, f.area) then n = n + 1 end end
                return n
            end
            return count(f)
        end""" % door_layers)


class DoorTests(unittest.TestCase):
    def test_gates_side_by_side_and_layer_behind_layer_are_one_door(self):
        lua = runtime()
        ring(lua, door_layers=3)
        lua.execute("doors = D.doors(npc, surface, {{-200, -200}, {200, 200}})")
        self.assertEqual(lua.eval("#doors"), 1)
        self.assertEqual((lua.eval("doors[1].x"), lua.eval("doors[1].y"), lua.eval("doors[1].across")), (0, -98.5, "y"))
        self.assertEqual(lua.eval("doors[1].width"), 4)

    def test_coming_home_it_lines_up_before_the_door_goes_straight_through_and_parks_inside_by_it(self):
        lua = runtime()
        ring(lua)
        lua.execute("""
            character.position = {x=0, y=-130}
            D.start(npc, {x=0, y=0}, 10, 0); D.tick(npc, 1)
            character.position = {x=4, y=-130}; D.tick(npc, 2); D.tick(npc, 3)""")
        park = (lua.eval("npc.drive.park.x"), lua.eval("npc.drive.park.y"))
        self.assertGreater(park[1], -100)                                  # inside its ring
        self.assertLess(abs(park[0]), 20)                                  # by the door, off its way in
        self.assertGreater(abs(park[0]), 4)
        self.assertEqual((lua.eval("requests[1].goal.x"), lua.eval("requests[1].goal.y")), (0, -111.5))   # square in front
        lua.execute("""
            D.on_path_finished({id=1, path={{position={x=0, y=-111}}}})
            car.position = {x=0.5, y=-112}; D.tick(npc, 4); D.tick(npc, 5)""")
        self.assertTrue(lua.eval("npc.drive.door.through"))
        self.assertEqual(lua.eval("#requests"), 1)                         # through without the pathfinder
        self.assertEqual((lua.eval("npc.drive.path[2].x"), lua.eval("npc.drive.path[2].y")), (0, -99.5))
        self.assertLess(lua.eval("car.riding_state.acceleration"), 3)       # forward, slowly
        lua.execute("""
            car.position = {x=0, y=-106}; D.tick(npc, 20)
            car.position = {x=0, y=-99}; D.tick(npc, 40)
            car.position = {x=0, y=-88}; D.tick(npc, 60)""")
        self.assertIsNone(lua.eval("npc.drive.door"))                     # through: on from this side
        lua.execute("car.speed = 0; D.tick(npc, 61)")
        self.assertEqual(lua.eval("D.state(npc)"), "arrived")
        self.assertTrue(lua.eval("npc.parked == car"))

    def test_with_the_run_in_clear_it_lines_up_farther_out_to_square_up_first(self):
        # (the engine check: come at from the side 12 out, a car could not square up and hit the wall)
        for blocked, first in ((0, -119.5), (1, -111.5)):
            lua = runtime()
            ring(lua)
            lua.execute("""
                local make = surface.create_entity
                surface.create_entity = function(spec)
                    local c = make(spec); c.prototype = {collision_mask={layers={object=true}}}; return c end
                surface.count_tiles_filtered = function() return 0 end
                surface.index = 1
                blocked = %d
                local count = surface.count_entities_filtered
                surface.count_entities_filtered = function(f) if f.collision_mask then return blocked end return count(f) end
                character.position = {x=0, y=-170}
                D.start(npc, {x=0, y=0}, 10, 0); D.tick(npc, 1)
                character.position = {x=4, y=-170}
                for t = 2, 60 do if not (npc.drive.leg or requests[1]) then D.tick(npc, t) end end
                leg = requests[1] and requests[1].goal or npc.drive.leg""" % blocked)
            self.assertEqual((lua.eval("leg.x"), lua.eval("leg.y")), (0, first))   # the leg's end
            if blocked:
                continue                                                   # something in the way: 12 out, as before
            lua.execute("""
                car.position = {x=0.5, y=-120}; D.tick(npc, 61); D.tick(npc, 62)""")
            self.assertTrue(lua.eval("npc.drive.door.through"))
            self.assertEqual([lua.eval("npc.drive.path[%d].y" % i) for i in (1, 2, 3)], [-111.5, -106.5, -99.5])

    def test_come_at_the_door_from_the_side_it_swings_onto_its_line_and_drives_in_square(self):
        # (the engine check: at the lining-up point heading across the door's line, a car steering for that
        # point circled it for minutes; a car moves here by its own wheel and pedals, turning as the engine's did there)
        lua = runtime()
        ring(lua)
        lua.execute("""
            character.position = {x=0, y=-170}
            D.start(npc, {x=0, y=0}, 10, 0); D.tick(npc, 1)
            car.position, car.orientation, car.speed = {x=2.7, y=-116.4}, 0.84, 0.45   -- heading away, fast
            car.set_driver(character)
            local d = npc.drive
            d.door = {door={x=0, y=-99.5, across='y', width=4}, a={x=0, y=-111.5}, b={x=0, y=-87.5},
                      line={x=0, y=-106.5}, far={x=0, y=-119.5}, through=true}
            d.path, d.pk, d.leg, d.phase, d.careful_until = {d.door.a, d.door.line, {x=0, y=-99.5}, d.door.b}, 1,
                d.door.b, 'drive', 1e9
            for t = 100, 1000 do
                D.tick(npc, t)
                if not npc.drive.door then break end
                local rs = car.riding_state or {}
                if rs.acceleration == 1 then car.speed = math.min(car.speed + 0.01, 0.5)
                elseif rs.acceleration == 2 then car.speed = math.max(car.speed - 0.02, 0)
                else car.speed = math.max(car.speed - 0.002, 0) end
                if rs.direction == 0 then car.orientation = (car.orientation - 0.008) % 1
                elseif rs.direction == 2 then car.orientation = (car.orientation + 0.008) % 1 end
                local a, old = car.orientation * 2 * math.pi, car.position
                car.position = {x=old.x + math.sin(a) * car.speed, y=old.y - math.cos(a) * car.speed}
                if not crossed and old.y < -99.5 and car.position.y >= -99.5 then crossed = car.position.x end
                last = t
            end""")
        self.assertIsNotNone(lua.eval("crossed"))
        self.assertLess(abs(lua.eval("crossed")), 1.0)                    # square through a 4-wide door
        self.assertIsNone(lua.eval("npc.drive.door"))                     # and on from the far side
        self.assertLess(lua.eval("last"), 400)

    def test_from_outside_to_outside_it_goes_round_its_ring_not_through_its_factory(self):
        lua = runtime()
        ring(lua)
        lua.execute("""
            npc.drive = {goal={x=300, y=0}, near=10}
            car = {surface=surface, name='car', position={x=-200, y=0}}
            corner = D.cross(npc, npc.drive, car, {x=-200, y=0}, {x=0, y=0})""")
        self.assertEqual(lua.eval("corner.x"), -124)                      # a corner on its own side, with room
        self.assertEqual(abs(lua.eval("corner.y")), 124)
        self.assertIsNone(lua.eval("npc.drive.door"))

    def test_no_way_to_a_corner_of_its_ring_and_it_goes_round_by_another(self):
        # (the range: the corner's spot in the wild past the cleared ground, no way there, asked again and again)
        lua = runtime()
        ring(lua)
        lua.execute("""
            npc.drive = {goal={x=-300, y=0}, near=10}
            car = {surface=surface, name='car', position={x=200, y=0}}
            first = D.cross(npc, npc.drive, car, {x=200, y=0}, {x=0, y=0})
            local d = npc.drive
            d.leg, d.narrow, d.request_id, d.asked_from = first, true, 7, {x=200, y=0, fitted=true}
            storage.drive_paths = {[7]=npc.id}; storage.npcs[npc.id] = npc
            D.on_path_finished({id=7, path=nil})
            second = D.cross(npc, npc.drive, car, {x=200, y=0}, {x=0, y=0})""")
        self.assertEqual(lua.eval("first.x"), 124)
        self.assertEqual(lua.eval("#npc.drive.bad_corners"), 1)
        self.assertEqual(lua.eval("npc.drive.no_path"), None)                 # not counted against the trip
        self.assertEqual(lua.eval("second.x"), 124)
        self.assertEqual(lua.eval("second.y"), -lua.eval("first.y"))          # the other one on its side

    def test_a_point_of_its_route_in_its_ring_is_passed_over_when_both_ends_are_out(self):
        # (the range: the route, which knows no walls, had a point on the ring's wall; round one corner then the
        # other for it)
        lua = runtime()
        self.walled_all_round(lua)
        lua.execute("""
            npc.drive = {goal={x=-300, y=-300}, near=10,
                         route={points={{x=-20, y=-99}, {x=-150, y=-200}, {x=-300, y=-300}}, k=1}}
            car = {surface=surface, name='car', position={x=-30, y=-112}}
            to = D.cross(npc, npc.drive, car, {x=-30, y=-112}, {x=-20, y=-99})""")
        self.assertEqual(lua.eval("npc.drive.route.k"), 2)
        self.assertLess(lua.eval("to.y"), -112)                               # on, away from the ring
        self.assertLess(lua.eval("to.x"), -30)

    def test_near_its_ring_wall_it_drives_carefully(self):
        # (the range: along the wall at full speed, it could not turn for the door in the room there)
        lua = runtime()
        self.walled_all_round(lua)
        lua.execute("""
            character.position = {x=-200, y=0}
            D.start(npc, {x=-106, y=-300}, 10, 0); D.tick(npc, 1)
            car.set_driver(character)
            local d = npc.drive
            d.path, d.pk, d.leg, d.phase = {{x=-106, y=-60}, {x=-106, y=-120}}, 1, {x=-106, y=-120}, 'drive'
            d.route = {points={{x=-106, y=-300}}, k=1}
            car.position, car.orientation, car.speed = {x=-106, y=0}, 0, 0.45
            D.tick(npc, 50); near = car.riding_state.acceleration
            car.position = {x=-140, y=0}
            d.path, d.pk, d.leg = {{x=-140, y=-60}, {x=-140, y=-120}}, 1, {x=-140, y=-120}
            D.tick(npc, 51); away = car.riding_state.acceleration""")
        self.assertEqual(lua.eval("near"), 2)                                 # braking to a careful speed
        self.assertNotEqual(lua.eval("away"), 2)                              # out in the open: on at speed

    def test_out_of_its_door_along_its_wall_it_goes_round_not_along_it(self):
        # (the range: just out of its north door, the goal east, a leg 2 tiles off the wall: it ran into it)
        lua = runtime()
        self.walled_all_round(lua)
        lua.execute("""
            npc.drive = {goal={x=400, y=0}, near=10}
            car = {surface=surface, name='car', position={x=0, y=-107}}
            to = D.cross(npc, npc.drive, car, {x=0, y=-107}, {x=95, y=-104})""")
        self.assertEqual((lua.eval("to.x"), lua.eval("to.y")), (124, -124))   # round by its corner
        lua.execute("to = D.cross(npc, npc.drive, car, {x=0, y=-107}, {x=0, y=-200})")
        self.assertIsNone(lua.eval("to"))                                       # straight out: no need
        lua.execute("""                                                       -- (Codex) a goal by the wall
            npc.drive = {goal={x=80, y=-104}, near=10}
            to = D.cross(npc, npc.drive, car, {x=0, y=-120}, {x=80, y=-104})""")
        self.assertIsNone(lua.eval("to"))                                       # driven to, not round the ring

    def test_with_no_corner_left_out_there_it_never_takes_a_door_through_its_factory(self):
        lua = runtime()
        self.walled_all_round(lua)
        lua.execute("""
            npc.drive = {goal={x=300, y=0}, near=10, bad_corners={{x=-124, y=-124}, {x=124, y=-124},
                         {x=124, y=124}, {x=-124, y=124}}}
            car = {surface=surface, name='car', position={x=-200, y=0}}
            to = D.cross(npc, npc.drive, car, {x=-200, y=0}, {x=0, y=0})""")
        self.assertIsNone(lua.eval("npc.drive.door"))

    def test_a_wall_it_drives_up_to_for_work_is_no_wall_to_cross(self):
        lua = runtime()
        ring(lua)
        lua.execute("""
            npc.drive = {goal={x=40, y=-99.5}, near=10}
            car = {surface=surface, name='car', position={x=40, y=-60}}
            crossed = D.cross(npc, npc.drive, car, {x=40, y=-60}, {x=40, y=-99.5})""")
        self.assertIsNone(lua.eval("crossed"))
        self.assertIsNone(lua.eval("npc.drive.door"))

    def walled_all_round(self, lua):
        ring(lua)
        lua.execute("""
            for i = -100, 99 do
                walls[#walls+1] = {valid=true, type='wall', force=force, position={x=i + 0.5, y=99.5}}
                walls[#walls+1] = {valid=true, type='wall', force=force, position={x=-99.5, y=i + 0.5}}
                walls[#walls+1] = {valid=true, type='wall', force=force, position={x=99.5, y=i + 0.5}}
            end""")

    def test_out_of_its_ring_by_its_one_door_though_the_wall_it_meets_is_another(self):
        # (the engine check: out east with its one door north, no door "lay across" the way: the tightest
        # box, back and forth at the east wall)
        lua = runtime()
        self.walled_all_round(lua)
        lua.execute("""
            npc.drive = {goal={x=300, y=0}, near=8}
            car = {surface=surface, name='car', position={x=0, y=0}}
            to = D.cross(npc, npc.drive, car, {x=0, y=0}, {x=300, y=0})""")
        self.assertIsNotNone(lua.eval("npc.drive.door"))
        self.assertIsNone(lua.eval("npc.drive.tight"))
        self.assertEqual((lua.eval("to.x"), lua.eval("to.y")), (0, -87.5))     # inside, square in front of it
        self.assertEqual(lua.eval("npc.drive.door.b.y"), -111.5)                # and out the far side

    def test_into_its_ring_from_the_far_side_it_comes_round_to_the_door_from_outside(self):
        lua = runtime()
        self.walled_all_round(lua)
        lua.execute("""
            npc.drive = {goal={x=0, y=0}, near=8}
            car = {surface=surface, name='car', position={x=0, y=160}}
            to = D.cross(npc, npc.drive, car, {x=0, y=160}, {x=0, y=0})""")
        self.assertEqual((lua.eval("to.x"), lua.eval("to.y")), (0, -111.5))    # outside the north door, not inside
        self.assertEqual(lua.eval("npc.drive.door.b.y"), -87.5)

    def test_a_leg_ending_just_short_of_its_ring_wall_goes_for_the_door_now(self):
        # (the engine check: at speed at such a leg's end, a car ran into the wall waiting for the next leg)
        lua = runtime()
        self.walled_all_round(lua)
        lua.execute("""
            npc.drive = {goal={x=0, y=0}, park={x=7, y=-83}, near=8}
            car = {surface=surface, name='car', position={x=-125, y=50}}
            to = D.cross(npc, npc.drive, car, {x=-125, y=50}, {x=-106, y=50})""")
        self.assertIsNotNone(lua.eval("npc.drive.door"))
        self.assertEqual((lua.eval("to.x"), lua.eval("to.y")), (0, -111.5))
        lua.execute("""
            npc.drive = {goal={x=-300, y=50}, near=8}
            to = D.cross(npc, npc.drive, car, {x=-125, y=50}, {x=-106, y=50})""")
        self.assertIsNone(lua.eval("to"))                                  # out there both: no door wanted
        lua.execute("""
            npc.drive = {goal={x=0, y=-103}, near=70}                      -- (Codex: may stop inside, short of it)
            to = D.cross(npc, npc.drive, car, {x=0, y=0}, {x=0, y=-96})""")
        self.assertIsNone(lua.eval("to"))
        self.assertIsNone(lua.eval("npc.drive.door"))

    def test_its_garage_is_by_a_door_in_its_ring_not_an_inner_gate(self):
        lua = runtime()
        ring(lua)
        lua.execute("""
            for x = -2, 1 do gates[#gates+1] = {valid=true, type='gate', force=force, position={x=x + 0.5, y=20.5}, direction=4} end
            spot = D.garage(npc, surface, {x=0, y=30}, 'car')""")
        self.assertLess(lua.eval("spot.y"), -70)                             # by the north door, in the ring's wall

    def test_its_parking_place_keeps_off_belts(self):
        lua = runtime()
        ring(lua)
        lua.execute("spot = D.garage(npc, surface, {x=0, y=0}, 'car')")
        first = (lua.eval("spot.x"), lua.eval("spot.y"))
        lua.execute("belts = {{position={x=%s, y=%s}}}; spot = D.garage(npc, surface, {x=0, y=0}, 'car')" % first)
        self.assertGreater(abs(lua.eval("spot.x") - first[0]) + abs(lua.eval("spot.y") - first[1]), 2)


class WorthTests(unittest.TestCase):
    def test_a_walk_of_its_work_is_driven_when_that_saves_time_walking_to_the_car_first(self):
        lua = runtime()
        ring(lua)
        how, spot = lua.eval("D.worth(npc, {x=0, y=-600})")             # at home, a car in its pockets:
        self.assertEqual(how, "fetch")                                    # put down by the door, not among its
        self.assertGreater(spot.y, -100)                                  # buildings, and driven out
        self.assertLess(spot.y, -70)
        lua.execute("held = {coal=10}")
        self.assertIsNone(lua.eval("D.worth(npc, {x=0, y=-600})"))       # no vehicle: walked
        lua.execute("""
            character.position = {x=0, y=-130}; held = {car=1, coal=50}
            D.start(npc, {x=0, y=0}, 10, 0); D.tick(npc, 1); character.position = {x=4, y=-130}; D.tick(npc, 2)
            npc.drive = nil; car.set_driver(nil); car.position = {x=12, y=-84}
            character.position = {x=0, y=0}""")                            # parked inside by the door, it at home
        how, spot = lua.eval("D.worth(npc, {x=0, y=-600})")
        self.assertEqual((how, spot.x), ("fetch", 12))                    # walked to, then driven
        self.assertIsNone(lua.eval("D.worth(npc, {x=60, y=60})"))        # inside to inside: on foot
        self.assertIsNone(lua.eval("D.worth(npc, {x=0, y=-150})"))       # too short to bother
        lua.execute("car.position = {x=0, y=-140}; character.position = {x=3, y=-140}")
        self.assertEqual(lua.eval("D.worth(npc, {x=0, y=-20})"), "drive")  # left outside: driven home, short as it is


if __name__ == "__main__":
    unittest.main()
