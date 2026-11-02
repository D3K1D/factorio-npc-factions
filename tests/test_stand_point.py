"""Where to stand to build, chosen before the walk (Worker.stand_point, Rescue.open_at), in Factorio's Lua 5.2. From
the Workers Union review (2026-10-29): a stance picked on the spot after the walk had it step into the next piece's
place, or into a pocket it could not leave."""
import unittest

import test_obstacles
import test_rescue

STUBS = """
    open_ok = function() return true end
    stubs['scripts.rescue'].open_at = function(c, at, extra) return open_ok(at, extra) end
    character.surface.find_non_colliding_position = function(_, p) return {x=p.x, y=p.y} end
    character.position = {x=-20, y=0}
    box = {x1=-1, y1=-1, x2=1, y2=1}                         -- (a 2x2 furnace at the origin)
    function pick(avoid, tick) return W.stand_point(npc, {position={x=0, y=0}, avoid=avoid}, box, tick or 1) end
    function inside(p, b) return p.x > b[1][1] and p.x < b[2][1] and p.y > b[1][2] and p.y < b[2][2] end
"""


class StandPointTests(unittest.TestCase):
    def test_the_nearest_point_in_reach_out_of_its_box_and_out_of_the_pieces_to_come(self):
        lua = test_obstacles.worker_with(STUBS)
        lua.execute("""
            first = pick({})
            next_piece = {{-4, -1}, {-1, 1}}                 -- (the furnace to its west, still to go up)
            second = pick({next_piece})""")
        self.assertLess(lua.eval("first.x"), -1)             # (the side it comes from)
        self.assertFalse(lua.eval("inside(second, next_piece) or inside(second, {{-1,-1},{1,1}})"))
        self.assertLessEqual(lua.eval("math.sqrt(second.x^2 + second.y^2)"), 10 - 0.75)

    def test_a_point_found_shut_in_is_passed_over_and_remembered_a_while(self):
        lua = test_obstacles.worker_with(STUBS)
        lua.execute("""
            shut = pick({})
            open_ok = function(at) return not (math.abs(at.x - shut.x) < 0.01 and math.abs(at.y - shut.y) < 0.01) end
            other = pick({}, 10)
            open_ok = function() return true end
            again = pick({}, 20)                             -- (still remembered: not chosen though open now)
            later = pick({}, 20 + 3600)""")
        self.assertFalse(lua.eval("other.x == shut.x and other.y == shut.y"))
        self.assertFalse(lua.eval("again.x == shut.x and again.y == shut.y"))
        self.assertTrue(lua.eval("later.x == shut.x and later.y == shut.y"))

    def test_nowhere_to_stand_walks_as_before(self):
        lua = test_obstacles.worker_with(STUBS + "open_ok = function() return false end")
        self.assertIsNone(lua.eval("pick({})"))


class BuildRunTests(unittest.TestCase):
    def test_a_run_walks_to_a_stand_point_clear_of_its_own_pieces_still_to_go_up(self):
        lua = test_obstacles.worker_with(STUBS + """
            defines.direction = {north=0, east=4, south=8, west=12}
            prototypes.entity['stone-furnace'] = {type='furnace', collision_box={left_top={x=-0.9, y=-0.9}, right_bottom={x=0.9, y=0.9}}}
            prototypes.item['stone-furnace'] = {type='item', place_result={name='stone-furnace'}}
            goals = {}
            nav_state = {'walking'}
            stubs['scripts.navigation'].step = function(_, _, goal, radius)
                goals[#goals + 1] = {x=goal.x, y=goal.y, r=radius}; return nav_state[1], nav_state[2] end
            character.position = {x=-40, y=0}
            assert(W.enqueue(npc, {kind='build_run', placements={
                {entity_name='stone-furnace', item='stone-furnace', position={x=0, y=0}},
                {entity_name='stone-furnace', item='stone-furnace', position={x=-3, y=0}}}}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("#goals"), 1)
        self.assertEqual(lua.eval("goals[1].r"), 0.5)                              # (to the stand point)
        self.assertFalse(lua.eval("inside(goals[1], {{-3.9, -0.9}, {-2.1, 0.9}})"))   # (not where the next goes)
        self.assertFalse(lua.eval("inside(goals[1], {{-0.9, -0.9}, {0.9, 0.9}})"))
        # (there, and still out of reach: as near as it gets, never the same stand point again and again)
        lua.execute("nav_state = {'arrived'}; W.tick(npc, 2); nav_state = {'walking'}; W.tick(npc, 3)")
        self.assertNotEqual(lua.eval("goals[#goals].r"), 0.5)
        lua.execute("npc.current_job.stand_for = nil")                          # (chosen afresh)
        # (no way there: as near as it gets, as before)
        lua.execute("nav_state = {'failed', 'no_path'}; W.tick(npc, 2); nav_state = {'walking'}; W.tick(npc, 3)")
        self.assertNotEqual(lua.eval("goals[#goals].r"), 0.5)


class OpenAtTests(unittest.TestCase):
    def test_a_spot_ringed_by_pieces_still_to_be_built_is_closed(self):
        lua = test_rescue.runtime()
        self.assertTrue(lua.eval("R.open_at(character, {x=0.5, y=0.5}, {})"))
        lua.execute("ring = {{{-2,-2},{3,-1}}, {{-2,2},{3,3}}, {{-2,-1},{-1,2}}, {{2,-1},{3,2}}}")
        self.assertFalse(lua.eval("R.open_at(character, {x=0.5, y=0.5}, ring)"))
        self.assertTrue(lua.eval("R.open_at(character, {x=6.5, y=0.5}, ring)"))   # (outside the ring)


class PlanTests(unittest.TestCase):
    def test_a_build_job_carries_the_boxes_of_the_pieces_still_to_go_up_near_it(self):
        import test_clearing
        lua = test_clearing.game()
        lua.execute("""
            prototypes.entity['gun-turret'] = {name='gun-turret', type='ammo-turret', tile_width=2, tile_height=2,
                collision_mask={layers={object=true}}}
            held['gun-turret'] = 3
            DS.fought_over = function() return false end
            local function t(id, x) return {id=id, prototype='gun-turret', item='gun-turret', world={x=x, y=5}, failures=0} end
            local stage = {id='s', entity_ids={'a', 'b', 'c', 'far'}, supplies={}, witnesses={}}
            prototypes.entity['stone-furnace'] = {name='stone-furnace', type='furnace', tile_width=2, tile_height=2,
                collision_mask={layers={object=true}}}
            local es = {a=t('a', 5), b=t('b', 7), c=t('c', 3), far=t('far', 40)}
            es.b.prototype, es.b.item = 'stone-furnace', 'stone-furnace'   -- (another kind: not one run with a)
            es.far.prototype, es.far.item = 'stone-furnace', 'stone-furnace'
            es.c.entity = {valid=true}                       -- (already up)
            npc.design = {status='building', stage_index=1, purpose='turrets', plan={plan_id='p', stages={stage},
                order={'a', 'b', 'c', 'far'}, entities=es}}
            job = DS.next(npc, 100)""")
        self.assertEqual(lua.eval("job.kind"), "build")
        self.assertEqual(lua.eval("#job.avoid"), 1)                              # (b only: c is up, far is far)
        self.assertEqual(lua.eval("job.avoid[1][1][1]"), 6)


if __name__ == "__main__":
    unittest.main()
