"""Smooth walking (movement_policy.lua steer/follow/halt) in Factorio's Lua 5.2."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "movement_policy.lua").read_text(encoding="utf-8")
    lua.execute("""
        defines = {direction={north=0, northeast=2, east=4, southeast=6, south=8, southwest=10, west=12,
                              northwest=14}}
        M = assert(load(source))()
    """)
    return lua


class SteeringTests(unittest.TestCase):
    def test_a_slanted_path_keeps_one_direction_instead_of_flickering(self):
        lua = runtime()
        # a path running 30 degrees east of north: the plain choice flips between north-east (45) and
        # north (0) as the walker drifts; kept within 32 degrees, it stays on one
        lua.execute("""
            nav = {}
            dirs = {}
            for k, bearing in ipairs({30, 20, 25, 18, 28, 21}) do
                local r = math.rad(bearing)
                dirs[k] = M.steer(nav, {x=0, y=0}, {x=math.sin(r), y=-math.cos(r)})
            end
        """)
        dirs = list(lua.eval("dirs").values())
        self.assertEqual(len(set(dirs)), 1)
        self.assertEqual(lua.eval("M.steer({}, {x=0, y=0}, {x=1, y=0})"), 4)        # east, from scratch
        self.assertEqual(lua.eval("M.steer({octant=1}, {x=0, y=0}, {x=1, y=0})"), 4)   # 45 degrees off: turns

    def test_waypoints_in_reach_are_passed_without_stopping_and_corners_looked_past(self):
        lua = runtime()
        lua.execute("""
            nav = {index=1, path={{position={x=0.5, y=0}}, {position={x=0.7, y=0}}, {position={x=1.6, y=0}},
                                  {position={x=3, y=0}}}}
            passed, p, aim = M.follow(nav, {x=0, y=0})
        """)
        self.assertTrue(lua.eval("passed"))
        self.assertEqual(lua.eval("nav.index"), 3)          # the first two were within reach: passed
        self.assertEqual(lua.eval("aim.x"), 1.6)            # the next is still a way off: steer at it
        lua.execute("passed, p, aim = M.follow(nav, {x=0.6, y=0})")
        self.assertEqual(lua.eval("aim.x"), 3)              # close now: steer at the one after it
        lua.execute("nav.index = 4; passed, p, aim = M.follow(nav, {x=2.5, y=0})")
        self.assertEqual(lua.eval("nav.index"), 4)          # the last waypoint is reached closer than the rest
        lua.execute("""
            character = {valid=true, walking_state={walking=true, direction=4}}
            M.halt(character)
        """)
        self.assertEqual((lua.eval("character.walking_state.walking"), lua.eval("character.walking_state.direction")),
                         (False, 4))                         # stops facing the way it walked

    def test_once_stuck_it_keeps_to_the_path_exactly(self):
        lua = runtime()
        # among trees: every waypoint reached, no corner cut, always the nearest direction
        lua.execute("""
            nav = {strict=true, octant=1, index=1, path={{position={x=0.5, y=0}}, {position={x=1, y=1}}}}
            passed, p, aim = M.follow(nav, {x=0, y=0})
        """)
        self.assertFalse(lua.eval("passed"))                 # 0.5 away is not reached yet
        self.assertEqual((lua.eval("aim.x"), lua.eval("aim.y")), (0.5, 0))
        r = "math.rad(20)"
        self.assertEqual(lua.eval("M.steer(nav, {x=0, y=0}, {x=math.sin(%s), y=-math.cos(%s)})" % (r, r)), 0)


def navigation():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "navigation.lua").read_text(encoding="utf-8")
    lua.globals().policy = (ROOT / "mod" / "scripts" / "movement_policy.lua").read_text(encoding="utf-8")
    lua.globals().routes = (ROOT / "mod" / "scripts" / "combat_routes.lua").read_text(encoding="utf-8")
    lua.globals().drive = (ROOT / "mod" / "scripts" / "drive.lua").read_text(encoding="utf-8")
    lua.execute("""
        defines = {direction={north=0, northeast=2, east=4, southeast=6, south=8, southwest=10, west=12,
                              northwest=14}}
        package.preload['scripts.movement_policy'] = function() return assert(load(policy))() end
        package.preload['scripts.combat_routes'] = function() return assert(load(routes))() end
        package.preload['scripts.drive'] = function() return assert(load(drive))() end
        N = assert(load(source))()
        D = require('scripts.drive')
        game = {tick=0}
        storage = {}
        behind = {valid=true, minable=true, force={name='neutral'}, position={x=-1, y=0}}
        ahead = {valid=true, minable=true, force={name='neutral'}, position={x=0.9, y=0.3}}
        surface = {find_entities_filtered=function() return {behind, ahead} end}
        held = {}
        pockets = {get_item_count=function(n) return held[n] or 0 end}
        character = {valid=true, position={x=0, y=0}, surface=surface, walking_state={walking=true, direction=4},
                     get_main_inventory=function() return pockets end}
        npc = {id=1, character=character}
    """)
    return lua


class WedgedTests(unittest.TestCase):
    def test_wedged_among_trees_it_chops_the_one_in_its_way_then_walks_on(self):
        lua = navigation()
        self.assertTrue(lua.eval("N.chop_begin(npc, (function() nav = {goal={x=10, y=0}, radius=0.3, requests=0, revision=0}; return nav end)(),"
                                 " {x=10, y=0}, 100)"))
        self.assertTrue(lua.eval("nav.chop.target == ahead"))          # the tree on its way, not the one behind
        lua.execute("job = {navigation=nav}; state = N.step(npc, job, {x=10, y=0}, 0.3, 110)")
        self.assertEqual(lua.eval("state"), "active")
        self.assertTrue(lua.eval("character.selected == ahead and character.mining_state.mining"))
        self.assertFalse(lua.eval("character.walking_state.walking"))   # it stands to chop
        lua.execute("ahead.valid = false; storage.path_requests = {}")   # chopped down
        lua.execute("surface.request_path = function() return 7 end; surface.is_chunk_generated = nil;"
                    "character.prototype = {}; character.force = {}; N.step(npc, job, {x=10, y=0}, 0.3, 120)")
        self.assertIsNone(lua.eval("nav.chop"))
        self.assertFalse(lua.eval("character.mining_state.mining"))


    def test_wedged_against_a_rock_it_mines_the_rock(self):
        lua = navigation()
        lua.execute("""
            rock = {valid=true, minable=true, type='simple-entity', force={name='neutral'}, position={x=2.3, y=0.5}}
            far_tree = {valid=true, minable=true, type='tree', force={name='neutral'}, position={x=2.2, y=-1}}
            surface.find_entities_filtered = function() return {far_tree, rock} end""")
        self.assertTrue(lua.eval("N.chop_begin(npc, (function() nav = {goal={x=10, y=0}}; return nav end)(),"
                                 " {x=10, y=0}, 100)"))
        self.assertTrue(lua.eval("nav.chop.target == rock"))            # the rock; that tree is not touching it


class StretchTests(unittest.TestCase):
    def test_a_new_path_after_a_stall_keeps_to_the_same_stretch(self):
        lua = navigation()
        lua.execute("""
            requested = {}
            surface.find_entities_filtered = function() return {} end
            surface.find_non_colliding_position = function(_, p) return {x=p.x, y=p.y} end
            surface.request_path = function(spec) requested[#requested+1] = spec.goal; return #requested end
            character.prototype = {collision_box={}, collision_mask={}}
            character.force = {name='npc-faction-1', is_chunk_charted=function() return false end}
            game.forces = {}
            npc.force = character.force
            job = {id='j1'}
            for t = 10, 15 do                               -- a long walk (its route planned first, a tick):
                if not requested[1] then game.tick = t; N.step(npc, job, {x=1000, y=0}, 0.3, t) end
            end                                            -- the first stretch, 128 east
            nav = job.navigation
            first = requested[1]
            nav.request_id, nav.retry_at = nil, 0          -- stalled in the trees: a new path is needed
            N.step(npc, job, {x=1000, y=0}, 0.3, 20)""")
        self.assertEqual((lua.eval("first.x"), lua.eval("first.y")), (128, 0))
        self.assertEqual((lua.eval("requested[2].x"), lua.eval("requested[2].y")), (128, 0))   # same stretch
        lua.execute("""
            nav.request_id = nil
            nav.stretch.failed = true                      -- no way there (a lake): aim another
            N.step(npc, job, {x=1000, y=0}, 0.3, 30)""")
        self.assertNotEqual(lua.eval("requested[3].y"), 0)

class ShoreTests(unittest.TestCase):
    def test_a_stretch_that_ends_at_its_own_feet_is_turned_for_another(self):
        lua = navigation()
        lua.execute("""
            requested = {}
            surface.find_entities_filtered = function() return {} end
            -- straight ahead is a lake whose nearest shore is where it stands; turned, there is land
            surface.find_non_colliding_position = function(_, p, r)
                if math.abs(p.y) < 1 then return r >= 64 and {x=0.5, y=0} or nil end
                return {x=p.x, y=p.y}
            end
            surface.request_path = function(spec) requested[#requested+1] = spec.goal; return #requested end
            character.prototype = {collision_box={}, collision_mask={}}
            character.force = {name='npc-faction-1'}
            game.forces = {}
            npc.force = character.force
            job = {id='j1'}
            for t = 10, 15 do
                if not requested[1] then game.tick = t; N.step(npc, job, {x=1000, y=0}, 0.3, t) end
            end""")
        self.assertNotEqual(lua.eval("requested[1].y"), 0)    # turned, not to its own feet
        self.assertGreater(lua.eval("math.abs(requested[1].x) + math.abs(requested[1].y)"), 32)

def cliffside():
    """Run 57's map: the engineer flush against a diagonal cliff piece (its box turned 45 degrees), as the
    engine probe placed it (no path from there; 2/256 tile off the face, every path)."""
    lua = navigation()
    lua.execute("""
        log = function() end
        CHAR = {collision_box={left_top={x=-0.1953125, y=-0.1953125}, right_bottom={x=0.1953125, y=0.1953125}},
                collision_mask={layers={player=true, train=true, is_object=true}}}
        CLIFF = {collision_mask={layers={player=true, object=true, item=true}}}
        cliff = {valid=true, prototype=CLIFF, bounding_box={left_top={x=-363.45703125, y=-372.515625},
                 right_bottom={x=-362.0390625, y=-368.984375}, orientation=0.125}}
        straight = {valid=true, prototype=CLIFF, bounding_box={left_top={x=-367, y=-367.5},
                    right_bottom={x=-365, y=-363.5}}}
        found = {cliff}
        surface.find_entities_filtered = function() return found end
        water = 0
        surface.count_tiles_filtered = function() return water end
        character.prototype = CHAR
        character.force = {name='npc-faction-1'}
        npc.force = character.force
        game.forces = {}
        requested = {}
        surface.request_path = function(spec) requested[#requested+1] = spec; return #requested end
        surface.find_non_colliding_position = function(_, p) return {x=p.x, y=p.y} end
        surface.is_chunk_generated = nil
        function gap(p)
            local b, h = cliff.bounding_box, 0.1953125
            local cx, cy = (b.left_top.x + b.right_bottom.x) / 2, (b.left_top.y + b.right_bottom.y) / 2
            local r = 1 / math.sqrt(2)
            return math.abs((p.x - cx) * r + (p.y - cy) * r) - 2 * h * r - (b.right_bottom.x - b.left_top.x) / 2
        end
    """)
    return lua


class CliffsideTests(unittest.TestCase):
    def test_flush_against_a_turned_box_the_path_is_asked_from_a_hair_off_it(self):
        lua = cliffside()
        lua.execute("character.position = {x=-362.30078125, y=-369.80078125}; p = N.start_from(character)")
        self.assertLess(lua.eval("gap(character.position)"), 2 / 256)   # where it stands: no path from there
        self.assertGreaterEqual(lua.eval("gap(p)"), 1 / 32 - 1e-9)       # well past the 2/256 that works
        self.assertLess(lua.eval("gap(p)"), 0.05)                        # and no farther than that
        self.assertGreater(lua.eval("p.x"), lua.eval("character.position.x"))   # out from the face, south-east
        self.assertGreater(lua.eval("p.y"), lua.eval("character.position.y"))
        # the walk asks from there, and the waypoints are walked from its feet
        lua.execute("job = {id='j'}; for t = 1, 3 do N.step(npc, job, {x=-352, y=-368.2}, 0.5, t) end")
        self.assertEqual(lua.eval("#requested"), 1)
        self.assertAlmostEqual(lua.eval("requested[1].start.x"), lua.eval("p.x"))
        self.assertAlmostEqual(lua.eval("requested[1].start.y"), lua.eval("p.y"))

    def test_a_straight_box_or_a_turned_one_further_off_leaves_the_start_alone(self):
        lua = cliffside()
        lua.execute("""
            found = {straight}
            character.position = {x=-364.8046875, y=-365.5}       -- flush against a straight cliff piece
            a = N.start_from(character)
            found = {cliff}
            character.position = {x=-361.6, y=-369.1}             -- a tile off the diagonal one
            b = N.start_from(character)
            cliff.prototype = {collision_mask={layers={rail=true}}}  -- a turned box it walks over
            character.position = {x=-362.30078125, y=-369.80078125}
            c = N.start_from(character)""")
        self.assertEqual((lua.eval("a.x"), lua.eval("a.y")), (-364.8046875, -365.5))
        self.assertEqual((lua.eval("b.x"), lua.eval("b.y")), (-361.6, -369.1))
        self.assertEqual((lua.eval("c.x"), lua.eval("c.y")), (-362.30078125, -369.80078125))

    def test_a_wall_a_hair_off_on_the_other_side_keeps_the_start_at_its_feet(self):
        lua = cliffside()
        lua.execute("""
            wall = {valid=true, prototype=CLIFF, bounding_box={left_top={x=-362.0955, y=-370.5},
                    right_bottom={x=-361, y=-369}}}
            found = {cliff, wall}
            character.position = {x=-362.30078125, y=-369.80078125}
            p = N.start_from(character)
            wall.bounding_box.left_top.x = -361.9                 -- a gap it can be pushed into
            q = N.start_from(character)
            water = 1                                             -- a shore a hair off instead
            r = N.start_from(character)""")
        self.assertEqual((lua.eval("p.x"), lua.eval("p.y")), (-362.30078125, -369.80078125))
        self.assertGreaterEqual(lua.eval("gap(q)"), 1 / 32 - 1e-9)
        self.assertEqual((lua.eval("r.x"), lua.eval("r.y")), (-362.30078125, -369.80078125))

    def test_in_a_corner_of_two_turned_faces_it_clears_both(self):
        lua = cliffside()
        # a second diagonal piece the other way, its face flush on the engineer's south-west: a V opening east
        lua.execute("""
            function gapu(b, p)
                local a = b.orientation * 2 * math.pi
                local ux, uy = math.cos(a), math.sin(a)
                local cx, cy = (b.left_top.x + b.right_bottom.x) / 2, (b.left_top.y + b.right_bottom.y) / 2
                return math.abs((p.x - cx) * ux + (p.y - cy) * uy) - 0.1953125 * (math.abs(ux) + math.abs(uy))
                    - (b.right_bottom.x - b.left_top.x) / 2
            end
            character.position = {x=-362.30078125, y=-369.80078125}
            local cx, cy = character.position.x - 0.698, character.position.y + 0.698
            other = {valid=true, prototype=CLIFF, bounding_box={left_top={x=cx - 0.70898, y=cy - 1.76563},
                     right_bottom={x=cx + 0.70898, y=cy + 1.76563}, orientation=0.375}}
            found = {cliff, other}
            p = N.start_from(character)""")
        self.assertLess(lua.eval("gapu(other.bounding_box, character.position)"), 2 / 256)
        self.assertGreaterEqual(lua.eval("gap(p)"), 1 / 32 - 1e-9)
        self.assertGreaterEqual(lua.eval("gapu(other.bounding_box, p)"), 1 / 32 - 1e-9)
        self.assertGreater(lua.eval("p.x"), lua.eval("character.position.x"))   # out of the V's mouth, east


class StrandedTests(unittest.TestCase):
    def walk_in(self, lua):
        # it walked in from the east along y=0, crumbs dropped on the way, and stands at x=0
        lua.execute("""
            found = {}
            game.players = {}
            character.name, character.unit_number = 'character', 1
            defines.build_check_type = {manual=1}
            surface.can_place_entity = function() return true end
            job = {id='j1'}
            for t = 1, 12 do
                character.position = {x=12 - t, y=0}
                N.step(npc, job, {x=-10, y=0}, 0.5, t)
            end
            character.position = {x=0, y=0}
            N.clear(npc, job)""")

    def no_path(self, lua, goal, tick):
        lua.execute(f"""
            job = {{id='g{goal[0]}_{tick}'}}
            npc.current_job = job
            storage.npcs = {{[1]=npc}}
            n0 = #requested
            for t = {tick}, {tick} + 5 do
                N.step(npc, job, {{x={goal[0]}, y={goal[1]}}}, 0.5, t)
                if #requested > n0 then break end
            end
            N.on_path_finished({{id=#requested, tick={tick}, path=nil}})""")

    def test_paths_failing_to_different_goals_from_one_spot_send_it_back_the_way_it_came(self):
        lua = cliffside()
        self.walk_in(lua)
        self.no_path(lua, (-20, 5), 100)
        self.no_path(lua, (-20, 5), 200)      # one goal it cannot reach: the goal's fault, it stays
        self.assertIsNone(lua.eval("npc.backtrack"))
        self.no_path(lua, (5, -20), 300)      # a second goal fails from the same spot: stranded
        self.assertIsNotNone(lua.eval("npc.backtrack"))
        self.assertIsNone(lua.eval("job.navigation.failed"))     # the job gets a fresh try afterwards
        # it walks back east along its crumbs, without asking for a path
        lua.execute("""
            n0 = #requested
            for t = 301, 340 do
                N.step(npc, job, {x=5, y=-20}, 0.5, t)
                if npc.backtrack then character.position = {x=character.position.x + 0.15, y=0} end
            end""")
        self.assertIsNone(lua.eval("npc.backtrack"))
        self.assertGreaterEqual(lua.eval("character.position.x"), 4)
        self.assertEqual(lua.eval("#requested"), lua.eval("n0 + 1"))    # then asks again, from there
        self.assertGreaterEqual(lua.eval("requested[#requested].start.x"), 4)
        # the crumbs it walked back over are spent: a second back-off would not lead to the spot again
        self.assertLess(lua.eval("npc.crumbs[#npc.crumbs].x"), 12)
        self.assertGreaterEqual(lua.eval("npc.crumbs[#npc.crumbs].x"), 4)

    def test_a_path_found_in_between_clears_the_tally(self):
        lua = cliffside()
        self.walk_in(lua)
        self.no_path(lua, (-20, 5), 100)
        self.no_path(lua, (-20, 5), 200)
        lua.execute("""
            job = {id='ok'}; npc.current_job = job
            for t = 250, 255 do N.step(npc, job, {x=9, y=9}, 0.5, t) end
            N.on_path_finished({id=#requested, tick=255, path={{position={x=1, y=1}}}})""")
        self.assertIsNone(lua.eval("npc.no_paths"))
        self.no_path(lua, (5, -20), 300)
        self.assertIsNone(lua.eval("npc.backtrack"))


def walking(lua):
    lua.execute("""
        requested = {}
        surface.find_entities_filtered = function() return {} end
        surface.find_non_colliding_position = function(_, p) return {x=p.x, y=p.y} end
        surface.request_path = function(spec) requested[#requested+1] = spec.goal; return #requested end
        character.prototype = {collision_box={}, collision_mask={}}
        character.force = {name='npc-faction-1'}
        game.forces = {}
        npc.force = character.force
        job = {id='j1'}""")


class DrivenWalkTests(unittest.TestCase):
    def test_a_long_walk_of_its_work_is_driven_with_its_car_beside_it_and_walked_on_from_where_it_parks(self):
        lua = navigation()
        walking(lua)
        lua.execute("""
            held = {coal=20}
            car = {valid=true, type='car', name='car', force=npc.force, surface=surface, position={x=2, y=0},
                   burner={remaining_burning_fuel=1e6, inventory={is_empty=function() return false end}},
                   get_driver=function() return nil end, get_passenger=function() return nil end}
            npc.parked = car
            state = N.step(npc, job, {x=1000, y=0}, 0.3, 10)""")
        self.assertEqual(lua.eval("state"), "active")
        self.assertEqual(lua.eval("npc.drive.job"), "j1")                 # the autopilot has the walk
        self.assertEqual(lua.eval("#requested"), 0)                        # no walking path asked
        self.assertTrue(lua.eval("job.navigation.trip.drove"))
        lua.execute("npc.drive.done = true; character.position = {x=990, y=0}; N.step(npc, job, {x=1000, y=0}, 0.3, 500)")
        self.assertIsNone(lua.eval("npc.drive"))                           # parked: the rest on foot
        self.assertEqual(lua.eval("#requested"), 1)
        lua.execute("character.position = {x=1000, y=0}; N.step(npc, job, {x=1000, y=0}, 0.3, 900)")
        self.assertEqual(lua.eval("npc.travel.trips[1].d"), 1000)          # its long walks kept
        self.assertTrue(lua.eval("npc.travel.trips[1].drove"))

    def test_a_drive_that_failed_is_walked_and_a_walk_cancelled_under_a_drive_stops_it(self):
        lua = navigation()
        walking(lua)
        lua.execute("""
            car = {valid=true, type='car', name='car', force=npc.force, surface=surface, position={x=2, y=0},
                   burner={remaining_burning_fuel=1e6, inventory={is_empty=function() return false end}},
                   get_driver=function() return nil end, get_passenger=function() return nil end}
            npc.parked = car
            N.step(npc, job, {x=1000, y=0}, 0.3, 10)
            npc.drive.failed = true
            for t = 20, 30 do if not requested[1] then game.tick = t; N.step(npc, job, {x=1000, y=0}, 0.3, t) end end""")
        self.assertIsNone(lua.eval("npc.drive"))                           # walked instead, not driven again
        self.assertGreater(lua.eval("#requested"), 0)
        lua.execute("""
            job2 = {id='j2'}
            N.step(npc, job2, {x=-1000, y=0}, 0.3, 30000)""")
        self.assertEqual(lua.eval("npc.drive.job"), "j2")
        lua.execute("N.clear(npc, job2)")
        self.assertIsNone(lua.eval("npc.drive"))                           # stopped with the walk

    def test_its_parked_car_off_a_way_is_walked_to_first(self):
        lua = navigation()
        walking(lua)
        lua.execute("""
            car = {valid=true, type='car', name='car', force=npc.force, surface=surface, position={x=-60, y=0},
                   burner={remaining_burning_fuel=1e6, inventory={is_empty=function() return false end}},
                   get_driver=function() return nil end, get_passenger=function() return nil end}
            npc.parked = car
            N.step(npc, job, {x=-1200, y=0}, 0.3, 10)""")
        self.assertIsNone(lua.eval("npc.drive"))
        self.assertEqual(lua.eval("requested[1].x"), -60)                  # to the car first
        lua.execute("character.position = {x=-58, y=0}; N.step(npc, job, {x=-1200, y=0}, 0.3, 300)")
        self.assertEqual(lua.eval("npc.drive.job"), "j1")                  # there: driven


if __name__ == "__main__":
    unittest.main()
