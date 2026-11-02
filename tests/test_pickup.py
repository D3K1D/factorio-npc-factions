"""Picking up its own buildings (lesson 17, the user's play 2026-10-30, in Factorio's Lua 5.2): 130 pickups brought a
creep's turrets and their ammo back. A pickup job takes each where it stands from its reach, walks only when out of it,
passes over a spot in danger unless what stands there fights, and DesignedStart.pickup_job (for Offense's creep) leaves
what a plan uses."""
import unittest

import test_acquire_plan
import test_obstacles

WORKER = """
    game = {tick=0}
    force = {name='npc-faction-1'}
    character.force = force
    character.surface = {count_entities_filtered=function() return biters end, find_entities_filtered=function() return {} end}
    biters = 0
    prototypes.entity['gun-turret'] = {type='ammo-turret'}
    prototypes.entity['wooden-chest'] = {type='container'}
    reach = true
    character.can_reach_entity = function() return reach end
    walks = 0
    stubs['scripts.navigation'].step = function() walks = walks + 1; return 'walking' end
    room = true
    function thing(id, name)
        local e = {valid=true, unit_number=id, name=name, force=force, position={x=3, y=3}, surface=character.surface,
                   minable=true}
        e.mine = function() if not room then return false end; e.valid = false; return true end
        return e
    end
    function run(n) for t = 1, n do game.tick = game.tick + 1; W.tick(npc, game.tick) end end
"""


def worker():
    lua = test_obstacles.worker_with(WORKER)
    return lua


def last(lua, field):
    receipts = list(lua.eval("npc.receipts").values())
    return receipts[-1][field] if receipts else None


class PickupJobTests(unittest.TestCase):
    def test_each_is_taken_from_its_reach_and_the_job_is_done(self):
        lua = worker()
        lua.execute("""a, b = thing(1, 'gun-turret'), thing(2, 'gun-turret')
                       ok = W.enqueue(npc, {kind='pickup', targets={a, b}}); run(5)""")
        self.assertTrue(lua.eval("ok"))
        self.assertEqual((lua.eval("a.valid"), lua.eval("b.valid")), (False, False))
        self.assertEqual((last(lua, "status"), last(lua, "picked")), ("complete", 2))
        self.assertEqual(lua.eval("walks"), 0)                                  # (from where it stands)

    def test_out_of_reach_it_walks(self):
        lua = worker()
        lua.execute("reach = false; a = thing(1, 'gun-turret'); W.enqueue(npc, {kind='pickup', targets={a}}); run(3)")
        self.assertGreater(lua.eval("walks"), 0)
        self.assertTrue(lua.eval("a.valid"))

    def test_by_biters_a_turret_is_still_taken_and_a_chest_left_standing(self):
        lua = worker()
        lua.execute("""biters = 1; a, b = thing(1, 'gun-turret'), thing(2, 'wooden-chest')
                       W.enqueue(npc, {kind='pickup', targets={a, b}}); run(5)""")
        self.assertFalse(lua.eval("a.valid"))
        self.assertTrue(lua.eval("b.valid"))
        self.assertEqual((last(lua, "picked"), last(lua, "passed")), (1, 1))

    def test_full_pockets_end_it_saying_so(self):
        lua = worker()
        lua.execute("room = false; a = thing(1, 'gun-turret'); W.enqueue(npc, {kind='pickup', targets={a}}); run(3)")
        self.assertEqual((last(lua, "status"), last(lua, "reason")), ("failed", "inventory_full"))

    def test_only_its_own(self):
        lua = worker()
        lua.execute("a = thing(1, 'gun-turret'); a.force = {name='player'}; ok, why = W.enqueue(npc, {kind='pickup', targets={a}})")
        self.assertEqual((lua.eval("ok"), lua.eval("why")), (False, "invalid_targets"))


class PickupApiTests(unittest.TestCase):
    def test_a_plans_building_stays_and_the_rest_go_in_walking_order(self):
        lua = test_acquire_plan.runtime()
        lua.execute("""
            local f = npc.character.force or npc.force
            npc.character.force = f
            local function t(x) return {valid=true, force=f, minable=true, position={x=x, y=0}} end
            far, near, planned, other = t(30), t(5), t(10), t(8)
            other.force = {}
            npc.design = {status='building', plan={order={'p'}, entities={p={id='p', entity=planned}}}}
            job, meta = DS.pickup_job(npc, {far, planned, near, other})""")
        self.assertEqual(lua.eval("job.kind"), "pickup")
        self.assertEqual(lua.eval("#job.targets"), 2)
        self.assertTrue(lua.eval("job.targets[1] == near and job.targets[2] == far"))
        self.assertEqual(lua.eval("meta.upkeep"), "pickup")
        self.assertIsNone(lua.eval("(DS.pickup_job(npc, {planned}))"))
        lua.execute("""paused = {valid=true, force=npc.character.force, minable=true, position={x=1, y=0}}
                       npc.paused_design = {plan={order={'q'}, entities={q={id='q', entity=paused}}}}""")
        self.assertIsNone(lua.eval("(DS.pickup_job(npc, {paused}))"))       # (a plan set aside keeps its own)


if __name__ == "__main__":
    unittest.main()
