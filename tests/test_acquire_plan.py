"""The whole recipe tree before fetching anything, and fetch-or-craft by time (DesignedStart.make_plan, fetch_worth,
acquire, in Factorio's Lua 5.2). From the Workers Union review (2026-10-29): fetching went depth-first and mined for a
recipe it could never finish; fixed amounts decided whether to fetch."""
import unittest

import test_retire

SETUP = r"""
    defines.inventory.chest = 1
    held = {['iron-plate']=0}
    npc.home, npc.design, npc.design_history = {x=0, y=0}, {}, {}
    local inv = {get_item_count=function(spec) return held[type(spec) == 'table' and spec.name or spec] or 0 end}
    npc.character = {valid=true, position={x=0, y=0}, reach_distance=10, character_running_speed=0.15,
                     prototype={crafting_categories={crafting=true}},
                     surface={count_entities_filtered=function() return 0 end,
                              find_entities_filtered=function() return {} end},
                     get_main_inventory=function() return inv end}
    local function r(name, ings, energy, cat)
        return {name=name, enabled=true, category=cat or 'crafting', energy=energy or 0.5,
                ingredients=ings, products={{type='item', name=name, amount=1}}}
    end
    npc.force.recipes = {
        ['iron-gear-wheel']=r('iron-gear-wheel', {{type='item', name='iron-plate', amount=2}}),
        ['gadget']=r('gadget', {{type='item', name='iron-gear-wheel', amount=1}, {type='item', name='widget', amount=1}}),
        ['widget']=r('widget', {{type='item', name='iron-plate', amount=1}}),
        ['oily']=r('oily', {{type='fluid', name='water', amount=10}}),
        ['pump-part']=r('pump-part', {{type='item', name='oily', amount=1}})}
    npc.force.recipes.widget.enabled = false        -- (locked: the gadget cannot be finished)
    acquire = require('scripts.agent').designed.acquire
    function want(item, n) local o = {acquire(npc, item, n or 5, 0)} return o[1], o[2] end
"""


def runtime():
    lua = test_retire.runtime()
    lua.execute(SETUP)
    return lua


class TreeTests(unittest.TestCase):
    def test_nothing_is_fetched_for_a_recipe_with_a_locked_ingredient_lower_down(self):
        lua = runtime()
        self.assertIsNone(lua.eval("(want('gadget'))"))                       # (no plate mined or fetched for it)
        self.assertEqual(lua.eval("select(2, want('gadget'))"), "recipe_locked:widget")

    def test_a_fluid_anywhere_in_the_tree_says_so(self):
        lua = runtime()
        self.assertEqual(lua.eval("select(2, want('pump-part'))"), "fluid_ingredient:oily")

    def test_a_tree_it_can_finish_has_its_hands_time_and_what_it_takes(self):
        lua = runtime()
        lua.execute("plan = DS.make_plan(npc, 'iron-gear-wheel', 10)")
        self.assertEqual(lua.eval("plan.base['iron-plate']"), 20)
        self.assertGreater(lua.eval("plan.seconds"), 0)


class FetchTests(unittest.TestCase):
    def test_fetched_when_quicker_by_time_not_by_a_fixed_amount(self):
        lua = runtime()
        lua.execute("""
            plan = {seconds=50, base={}}                     -- (slow by hand: 10 s each)
            near = {valid=true, position={x=5, y=0}}
            far = {valid=true, position={x=400, y=0}}""")
        self.assertTrue(lua.eval("DS.fetch_worth(npc, 'x', near, 2, 5, plan)"))   # (two in reach: worth it)
        lua.execute("npc.fetch_memo = nil; quick = {seconds=1, base={}}")
        self.assertFalse(lua.eval("DS.fetch_worth(npc, 'y', far, 5, 5, quick)"))  # (a long walk for a second's craft)
        lua.execute("npc.fetch_memo = nil")
        self.assertTrue(lua.eval("DS.fetch_worth(npc, 'z', far, 5, 5, nil)"))     # (its hands cannot make it)

    def test_what_its_machines_make_is_taken_where_it_is_whatever_its_hands_could_do(self):
        # (the user, 2026-10-25: grab gears or green circuits off belts rather than craft them from plates)
        lua = runtime()
        lua.execute("""
            DS.machine_made = function(_, item) return item == 'iron-gear-wheel' end
            far = {valid=true, position={x=400, y=0}}""")
        self.assertTrue(lua.eval("DS.fetch_worth(npc, 'iron-gear-wheel', far, 5, 5, {seconds=0.1, base={}})"))

    def test_the_choice_holds_a_while(self):
        lua = runtime()
        lua.execute("""
            game.tick = 100
            near = {valid=true, position={x=5, y=0}}
            first = DS.fetch_worth(npc, 'x', near, 5, 5, {seconds=50, base={}})
            again = DS.fetch_worth(npc, 'x', near, 5, 5, {seconds=0, base={}})""")
        self.assertTrue(lua.eval("first"))
        self.assertTrue(lua.eval("again"))                                     # (held DECISION ticks)


if __name__ == "__main__":
    unittest.main()
