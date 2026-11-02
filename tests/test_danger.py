"""Every placement it walks to is judged for danger (Hands.in_danger, the worker's build and build_run, in Factorio's
Lua 5.2; W-30). Offense's late-combat runs: a run's first piece was checked and its later ones not, it went to a site
inside a nest cluster's reach (57 biters, big and behemoth worms), and the engineer died at minute 1."""
import unittest

import test_opening_pace

SETUP = """
    storage = storage or {}
    enemies = {}   -- {kind=..., x=..., y=..., range=...}
    character.surface = {
        count_entities_filtered=function(f)
            local n = 0
            for _, e in ipairs(enemies) do
                if e.kind == f.type and (e.x - f.position.x)^2 + (e.y - f.position.y)^2 <= f.radius^2 then n = n + 1 end
            end
            return n
        end,
        find_entities_filtered=function(f)
            local out = {}
            for _, e in ipairs(enemies) do
                if e.kind == f.type and (e.x - f.position.x)^2 + (e.y - f.position.y)^2 <= f.radius^2 then
                    out[#out + 1] = {position={x=e.x, y=e.y}, prototype={attack_parameters={range=e.range}}}
                end
            end
            return out
        end}
    H = require('scripts.hands')
    character.build_distance, character.reach_distance = 10, 10
    require('scripts.navigation').distance = function(a, b) return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2) end
    prototypes.entity = prototypes.entity or {}
    prototypes.entity['gun-turret'] = {type='ammo-turret'}
    prototypes.entity['stone-furnace'] = {type='furnace'}
"""


def runtime():
    lua = test_opening_pace.crafting_worker()
    lua.execute(SETUP)
    return lua


class DangerTests(unittest.TestCase):
    def test_biters_a_nest_beside_it_or_a_worm_s_reach(self):
        lua = runtime()
        self.assertFalse(lua.eval("H.in_danger(npc, {x=0, y=0})"))
        lua.execute("enemies = {{kind='unit', x=30, y=0}}")
        self.assertTrue(lua.eval("H.in_danger(npc, {x=0, y=0})"))
        lua.execute("enemies = {{kind='unit-spawner', x=20, y=0}}")
        self.assertFalse(lua.eval("H.in_danger(npc, {x=0, y=0})"))            # (a nest only right beside it)
        lua.execute("enemies = {{kind='turret', x=50, y=0, range=48}}")       # (a behemoth worm's reach)
        self.assertTrue(lua.eval("H.in_danger(npc, {x=0, y=0})"))

    def test_a_build_in_danger_is_refused_before_the_walk_and_a_turret_is_not(self):
        lua = runtime()
        lua.execute("""
            enemies = {{kind='unit', x=5, y=0}}
            assert(W.enqueue(npc, {kind='build', entity_name='stone-furnace', item='stone-furnace', position={x=0, y=0}}))
            W.tick(npc, 1)
            r = nil
            for _, x in pairs(npc.receipts) do r = x end""")
        self.assertEqual(lua.eval("r.reason"), "in_danger")
        lua.execute("""
            npc.receipts = {}
            assert(W.enqueue(npc, {kind='build', entity_name='gun-turret', item='gun-turret', position={x=0, y=0}}))
            W.tick(npc, 2)
            r = nil
            for _, x in pairs(npc.receipts) do r = x end""")
        self.assertNotEqual(lua.eval("r and r.reason"), "in_danger")          # (put down in a fight on purpose)

    def test_a_run_stops_short_of_its_pieces_in_danger(self):
        run = """
            job = {kind='build_run', placements={
                {id='a', entity_name='stone-furnace', item='stone-furnace', position={x=0, y=0}},
                {id='b', entity_name='stone-furnace', item='stone-furnace', position={x=90, y=0}}}}
            assert(W.enqueue(npc, job))
            W.tick(npc, 1)
            done = false
            for _, x in pairs(npc.receipts) do if x.status == 'complete' then done = true end end"""
        lua = runtime()
        lua.execute("enemies = {{kind='unit', x=100, y=0}}" + run)
        self.assertTrue(lua.eval("done"))                                      # (the far one in danger: left, no walk)
        lua = runtime()
        lua.execute(run)
        self.assertFalse(lua.eval("done"))                                     # (safe: it walks on to it)
        # (Codex, W-30: a safe piece beyond one in danger is not walked to through it)
        lua = runtime()
        lua.execute("""
            enemies = {{kind='unit', x=50, y=0}}
            job = {kind='build_run', placements={
                {id='a', entity_name='stone-furnace', item='stone-furnace', position={x=0, y=0}},
                {id='b', entity_name='stone-furnace', item='stone-furnace', position={x=45, y=0}},
                {id='c', entity_name='stone-furnace', item='stone-furnace', position={x=120, y=0}}}}
            assert(W.enqueue(npc, job))
            W.tick(npc, 1)
            done = false
            for _, x in pairs(npc.receipts) do if x.status == 'complete' then done = true end end""")
        self.assertTrue(lua.eval("done"))

    def test_a_walk_to_a_build_s_staging_point_never_into_its_danger(self):
        # (Codex, W-30: an oil project's staging move came before its build was judged)
        lua = runtime()
        lua.execute("""
            enemies = {{kind='unit', x=5, y=0}}
            assert(W.enqueue(npc, {kind='move', position={x=0, y=0}, avoid_danger=true}))
            W.tick(npc, 1)
            r = nil
            for _, x in pairs(npc.receipts) do r = x end""")
        self.assertEqual(lua.eval("r.reason"), "in_danger")
        root = test_opening_pace.ROOT / "mod" / "scripts"
        self.assertEqual((root / "oil_projects.lua").read_text(encoding="utf-8").count("avoid_danger=true"), 2)
        corrector = (root / "corrector.lua").read_text(encoding="utf-8")
        self.assertIn('if fix.reason == "in_danger" then', corrector)            # (backs off, no try counted)


if __name__ == "__main__":
    unittest.main()
