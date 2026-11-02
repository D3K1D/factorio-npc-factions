"""A job whose goal is reached already, by robots or another hand, is done, not a failure (Worker.goal_met, in
Factorio's Lua 5.2). LIVE-OP-a: the engineer's own construction robots took the trees and rocks in a wall's way, each
clearing job failed "target_exhausted", and six such failures gave up a 170-piece wall plan; since W-26 its robots
and its network's race its hands all the time."""
import unittest

import test_opening_pace

SETUP = """
    storage = storage or {}
    standing = {}
    character.force = character.force or {name='player'}
    character.surface = {
        count_entities_filtered=function() return 0 end,
        find_entities_filtered=function(f)
            local out = {}
            for _, e in ipairs(standing) do
                if (not f.name or f.name == e.name) then out[#out + 1] = e end
            end
            return out
        end}
    function receipt() local r = nil for _, x in pairs(npc.receipts) do r = x end return r end
"""


def runtime():
    lua = test_opening_pace.crafting_worker()
    lua.execute(SETUP)
    return lua


class GoalMetTests(unittest.TestCase):
    def test_a_building_already_standing_where_it_goes_is_a_build_done(self):
        lua = runtime()
        lua.execute("""
            existing = {valid=true, name='stone-furnace', position={x=3, y=4}, direction=0}
            standing = {existing}
            assert(W.enqueue(npc, {kind='build', entity_name='stone-furnace', item='stone-furnace', position={x=3, y=4}}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("receipt().status"), "complete")
        self.assertTrue(lua.eval("receipt().entity == existing"))             # (its plan takes it as built)
        self.assertTrue(lua.eval("receipt().already"))
        self.assertEqual(lua.eval("receipt().consumed['stone-furnace']"), 0)   # (nothing of its own spent)

    def test_one_standing_there_not_as_planned_is_no_build_done(self):
        # (Codex: an underground's other end, or one mirrored the other way)
        lua = runtime()
        lua.execute("""
            standing = {{valid=true, name='underground-belt', position={x=3, y=4}, direction=0,
                         belt_to_ground_type='input'}}
            ok = W.goal_met(npc, {kind='build', entity_name='underground-belt', position={x=3, y=4},
                                  belt_to_ground_type='output'})
            standing = {{valid=true, name='oil-refinery', position={x=3, y=4}, direction=0, mirroring=false}}
            mir = W.goal_met(npc, {kind='build', entity_name='oil-refinery', position={x=3, y=4}, mirror=true})""")
        self.assertIsNone(lua.eval("ok"))
        self.assertIsNone(lua.eval("mir"))
        src = (test_opening_pace.ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("not (receipt.already and DesignedStart.owned_elsewhere(npc,d.plan,receipt.entity))", src)

    def test_a_clearing_s_flag_reaches_the_worker(self):
        src = (test_opening_pace.ROOT / "mod" / "scripts" / "worker.lua").read_text(encoding="utf-8")
        self.assertIn('"clears"}', src)                                       # (Defense's fae5a50, kept by enqueue)

    def test_nothing_there_it_fails_as_before(self):
        lua = runtime()
        lua.execute("""
            assert(W.enqueue(npc, {kind='build', entity_name='stone-furnace', item='stone-furnace', position={x=3, y=4}}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("receipt().status"), "failed")

    def test_what_it_was_to_take_down_gone_is_done_and_a_gathering_is_not(self):
        lua = runtime()
        lua.execute("""
            tree = {valid=false}
            assert(W.enqueue(npc, {kind='mine', target=tree, count=1, mode='deconstruct'}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("receipt().status"), "complete")
        lua = runtime()
        lua.execute("""
            ore = {valid=false}
            assert(W.enqueue(npc, {kind='mine', target=ore, count=5, item='iron-ore'}))
            W.tick(npc, 1)""")
        self.assertEqual(lua.eval("receipt().status"), "failed")            # (the ore was for its pockets)

    def test_a_recipe_or_direction_set_already(self):
        lua = runtime()
        lua.execute("""
            m = {valid=true, get_recipe=function() return {name='iron-gear-wheel'} end}
            ok = W.goal_met(npc, {kind='configure_recipe', target=m, recipe='iron-gear-wheel'})
            no = W.goal_met(npc, {kind='configure_recipe', target=m, recipe='pipe'})
            d = W.goal_met(npc, {kind='configure_direction', target={valid=true, direction=4}, direction=4})""")
        self.assertTrue(lua.eval("ok.already"))
        self.assertIsNone(lua.eval("no"))
        self.assertTrue(lua.eval("d.already"))


if __name__ == "__main__":
    unittest.main()
