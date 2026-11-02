"""Mending (scripts/mend.lua, in Factorio's Lua 5.2; W-33): what biters hurt and left standing gets its health back
from the engineer's repair packs, its turrets first, a 32-tile patch at a time, never where biters are or where its
own robots mend; and the worker's mend job, which gives way to anything urgent."""
import unittest

import test_character_layer
import test_obstacles

SETUP = """
    Mend = require('scripts.mend')
    Robots = {networks=function(npc, pos) return nets_at(pos) end}
    nets_at = function() return {} end
    -- (NPC Ledger's L-5 answers whether its robots mend there: here from the networks each test sets)
    require('scripts.logistics').can_repair = function(npc, pos)
        local n = 0
        for _, net in ipairs(nets_at(pos)) do
            if net.all_construction_robots > 0 then n = math.max(n, net.get_item_count('repair-pack')) end
        end
        return n
    end
    biters_at = {}   -- (cells with biters, by their middle's x)
    surface = {count_entities_filtered=function(spec)
        if spec.type == 'unit' and biters_at[spec.position.x] then return 1 end
        return 0
    end, find_entities_filtered=function() return {} end}
    function building(id, kind, x, y, health, max)
        return {valid=true, unit_number=id, type=kind, force={name='npc-faction-1'}, position={x=x, y=y},
                health=health, max_health=max or 100, prototype={repair_speed_modifier=1}, surface=surface}
    end
    function hit(e) Mend.on_entity_damaged({entity=e, final_health=e.health}) end
    npc = {id=1, force={name='npc-faction-1'}, home={x=0, y=0},
           character={valid=true, surface=surface, position={x=0, y=0}, force={name='npc-faction-1'}}}
"""


def runtime():
    lua = test_character_layer.runtime()
    lua.execute(SETUP)
    return lua


class HurtTests(unittest.TestCase):
    def test_only_an_engineers_buildings_left_standing_are_remembered(self):
        lua = runtime()
        lua.execute("""hit(building(1, 'wall', 1, 1, 50))
                       local p = building(2, 'wall', 1, 1, 50); p.force = {name='player'}; hit(p)
                       local dead = building(3, 'wall', 1, 1, 0); hit(dead)
                       local body = building(4, 'character', 1, 1, 50); hit(body)
                       local car = building(5, 'car', 1, 1, 50); hit(car)""")
        self.assertEqual(lua.eval("storage.mend.n['npc-faction-1']"), 1)
        self.assertIsNotNone(lua.eval("storage.mend.hurt['npc-faction-1'][1]"))
        self.assertIsNone(lua.eval("storage.mend.hurt['player']"))


class PatchTests(unittest.TestCase):
    def test_the_turrets_patch_first_then_in_it_turrets_then_the_worst(self):
        lua = runtime()
        lua.execute("""
            hit(building(1, 'wall', 40, 8, 10))            -- (a wall's cell, badly hurt)
            hit(building(2, 'ammo-turret', 8, 8, 90))      -- (a turret's cell: first however little it misses)
            hit(building(3, 'wall', 9, 9, 20))
            hit(building(4, 'assembling-machine', 10, 10, 5))
            hit(building(5, 'wall', 11, 9, 60))
            targets, lost = Mend.patch(npc, 512, Robots)""")
        self.assertEqual([lua.eval(f"targets[{i}].unit_number") for i in range(1, 5)], [2, 3, 5, 4])
        self.assertIsNone(lua.eval("targets[5]"))
        self.assertEqual(lua.eval("lost"), 10 + 80 + 40 + 95)

    def test_a_patch_with_biters_or_its_robots_mending_is_passed_over_and_the_whole_forgotten(self):
        lua = runtime()
        lua.execute("""
            hit(building(1, 'ammo-turret', 8, 8, 50))      -- (cell 0:0, middle 16)
            hit(building(2, 'wall', 40, 8, 50))            -- (cell 1:0, middle 48)
            hit(building(3, 'wall', 72, 8, 50))            -- (cell 2:0, middle 80)
            biters_at[16] = true
            nets_at = function(pos)
                if pos.x == 40 then return {{all_construction_robots=4, get_item_count=function() return 9 end}} end
                return {}
            end
            targets = Mend.patch(npc, 512, Robots)""")
        self.assertEqual(lua.eval("targets[1].unit_number"), 3)
        lua.execute("""storage.mend.hurt['npc-faction-1'][3].health = 100
                       biters_at[16] = nil
                       targets = Mend.patch(npc, 512, Robots)""")
        self.assertEqual(lua.eval("targets[1].unit_number"), 1)
        self.assertIsNone(lua.eval("storage.mend.hurt['npc-faction-1'][3]"))   # (whole again: forgotten)

    def test_its_robots_mend_what_their_network_covers_and_it_the_rest_of_the_patch(self):
        lua = runtime()
        lua.execute("""
            hit(building(1, 'wall', 2, 8, 50)); hit(building(2, 'wall', 30, 8, 60))
            nets_at = function(pos)   -- (the patch's middle covered, and its first wall; not the far one)
                if pos.x < 20 then return {{all_construction_robots=4, get_item_count=function() return 9 end}} end
                return {}
            end
            targets = Mend.patch(npc, 512, Robots)""")
        self.assertEqual(lua.eval("targets[1].unit_number"), 2)
        self.assertIsNone(lua.eval("targets[2]"))

    def test_a_turret_its_robots_mend_does_not_rank_its_patch_first(self):
        lua = runtime()
        lua.execute("""
            hit(building(1, 'ammo-turret', 2, 8, 50)); hit(building(2, 'assembling-machine', 4, 8, 50))
            hit(building(3, 'wall', 40, 8, 90))
            nets_at = function(pos)
                if pos.x == 2 then return {{all_construction_robots=4, get_item_count=function() return 9 end}} end
                return {}
            end
            targets = Mend.patch(npc, 512, Robots)""")
        self.assertEqual(lua.eval("targets[1].unit_number"), 3)

    def test_another_surface_is_not_its_to_mend(self):
        lua = runtime()
        lua.execute("""local e = building(1, 'wall', 2, 2, 50); e.surface = {}; hit(e)
                       targets = Mend.patch(npc, 512, Robots)""")
        self.assertIsNone(lua.eval("targets"))

    def test_far_from_home_is_not_its_to_mend(self):
        lua = runtime()
        lua.execute("hit(building(1, 'wall', 900, 0, 50)); targets = Mend.patch(npc, 512, Robots)")
        self.assertIsNone(lua.eval("targets"))

    def test_packs_for_a_patch(self):
        lua = runtime()
        self.assertEqual(lua.eval("Mend.packs_for(10)"), 1)
        self.assertEqual(lua.eval("Mend.packs_for(650)"), 3)
        self.assertEqual(lua.eval("Mend.packs_for(99999)"), 20)


WORKER = """
    stubs['scripts.mend'] = assert(load(mend_source, '@mend'))()
    W = assert(load(worker_source, '@worker'))()
    game = {tick=0}
    force = {name='npc-faction-1'}
    character.force = force
    character.surface = {count_entities_filtered=function() return 0 end, find_entities_filtered=function() return {} end}
    pack = {valid_for_read=true, drained=0, drain_durability=function(n) pack.drained = pack.drained + n end}
    inv.find_item_stack = function(name) if name == 'repair-pack' and pack then return pack end end
    reach = true
    character.can_reach_entity = function() return reach end
    walks = 0
    stubs['scripts.navigation'].step = function() walks = walks + 1; return 'walking' end
    function building(id, kind, health)
        return {valid=true, unit_number=id, type=kind, force=force, position={x=3, y=3}, health=health,
                max_health=100, prototype={repair_speed_modifier=1}, surface=character.surface}
    end
    function run(n) for t = 1, n do game.tick = game.tick + 1; W.tick(npc, game.tick) end end
"""


def worker():
    lua = test_obstacles.worker_with("")
    lua.globals().mend_source = (test_obstacles.test_clearing.ROOT / "mod" / "scripts" / "mend.lua").read_text(
        encoding="utf-8")
    lua.execute(WORKER)
    return lua


def last(lua, field):
    receipts = list(lua.eval("npc.receipts").values())
    return receipts[-1][field] if receipts else None


class MendJobTests(unittest.TestCase):
    def test_it_mends_each_in_turn_with_its_packs_and_is_done(self):
        lua = worker()
        lua.execute("""a, b = building(1, 'ammo-turret', 90), building(2, 'wall', 97)
                       ok, id = W.enqueue(npc, {kind='mend', targets={a, b}})
                       run(12)""")
        self.assertTrue(lua.eval("ok"))
        self.assertEqual((lua.eval("a.health"), lua.eval("b.health")), (100, 100))
        self.assertEqual(lua.eval("pack.drained"), 13)
        self.assertEqual(last(lua, "status"), "complete")
        self.assertEqual(last(lua, "mended"), 13)
        self.assertEqual(last(lua, "first_kind"), "turret")
        self.assertEqual((last(lua, "mended_by_kind")["turret"], last(lua, "mended_by_kind")["wall"]), (10, 3))

    def test_out_of_reach_it_walks_first(self):
        lua = worker()
        lua.execute("""reach = false; a = building(1, 'wall', 50)
                       W.enqueue(npc, {kind='mend', targets={a}}); run(3)""")
        self.assertGreater(lua.eval("walks"), 0)
        self.assertEqual(lua.eval("a.health"), 50)

    def test_it_gives_way_to_a_stage_going_up(self):
        lua = worker()
        lua.execute("""a = building(1, 'wall', 10)
                       W.enqueue(npc, {kind='mend', targets={a}}); run(2)
                       npc.design = {status='building'}; run(2)""")
        self.assertEqual((last(lua, "status"), last(lua, "reason")), ("canceled", "yielded"))
        self.assertLess(lua.eval("a.health"), 100)

    def test_there_and_still_out_of_reach_it_passes_that_one_over(self):
        lua = worker()
        lua.execute("""reach = false; a, b = building(1, 'wall', 50), building(2, 'wall', 50)
                       stubs['scripts.navigation'].step = function() walks = walks + 1; return 'arrived' end
                       W.enqueue(npc, {kind='mend', targets={a, b}}); run(3)""")
        self.assertEqual((last(lua, "status"), last(lua, "mended")), ("complete", 0))   # (both passed over)
        self.assertEqual(lua.eval("walks"), 2)

    def test_an_attack_on_its_base_ends_the_mending(self):
        lua = worker()
        lua.execute("""a = building(1, 'wall', 10)
                       W.enqueue(npc, {kind='mend', targets={a}}); run(2)
                       npc.alarm = {last=game.tick}; run(2)""")
        self.assertEqual((last(lua, "status"), last(lua, "reason")), ("canceled", "yielded"))

    def test_no_packs_and_nothing_mended_is_a_failure(self):
        lua = worker()
        lua.execute("""pack = nil; a = building(1, 'wall', 10)
                       W.enqueue(npc, {kind='mend', targets={a}}); run(2)""")
        self.assertEqual((last(lua, "status"), last(lua, "reason")), ("failed", "no_packs"))

    def test_only_its_own_buildings_are_taken(self):
        lua = worker()
        lua.execute("""a = building(1, 'wall', 10); a.force = {name='player'}
                       ok, why = W.enqueue(npc, {kind='mend', targets={a}})""")
        self.assertEqual((lua.eval("ok"), lua.eval("why")), (False, "invalid_targets"))


if __name__ == "__main__":
    unittest.main()
