"""Recipes found by what they make (recipes.lua), in Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "recipes.lua").read_text(encoding="utf-8")
    lua.execute("""
        Recipes = assert(load(source))()
        protos, asked = {}, 0
        -- The game's recipe filter: every prototype listing the item among its products.
        prototypes = {entity={}, get_recipe_filtered=function(filters)
            asked = asked + 1
            local want = filters[1].elem_filters[1].name
            local out = {}
            for name, r in pairs(protos) do
                for _, p in ipairs(r.products) do if p.name == want then out[name] = r end end
            end
            return out
        end}
        force = {recipes={}}
        function recipe(name, category, ingredients, products, extra)
            local r = {name=name, category=category, ingredients=ingredients, products=products}
            for k, v in pairs(extra or {}) do r[k] = v end
            protos[name] = r
            force.recipes[name] = {name=name, enabled=true, category=category, ingredients=ingredients,
                                   products=products}
        end
        local function item(name, amount) return {type='item', name=name, amount=amount or 1} end
        recipe('empty-recipe', 'crafting', {item('iron-plate', 2)}, {item('empty-magazine')})
        recipe('firearm-magazine-recycling', 'recycling', {item('firearm-magazine')},
               {item('empty-magazine', 0.25)}, {hidden=true})
        recipe('grenade', 'crafting', {item('iron-plate', 5), item('coal', 10)}, {item('grenade')})
        recipe('maraxsis-pipe-bomb', 'maraxsis-hydro-plant', {item('explosives', 5), item('pipe', 5)},
               {item('grenade')})
        recipe('wall-press', 'pressing', {item('stone-brick', 5)}, {item('stone-wall', 2)})
        recipe('stone-wall', 'crafting', {item('stone-brick', 5)}, {item('stone-wall')})
        recipe('kovarex', 'centrifuging', {item('u235', 40), item('u238', 5)},
               {item('u235', 41), item('u238', 2)})
        hand = {crafting=true}
    """)
    return lua


class RecipeTests(unittest.TestCase):
    def test_an_item_is_found_by_the_recipe_that_makes_it_whatever_its_name(self):
        lua = runtime()
        self.assertEqual(lua.eval("Recipes.for_item(force, 'empty-magazine', hand).name"), "empty-recipe")
        # (recycling is skipped: it is no way to make a thing)
        self.assertEqual(list(lua.eval("Recipes.candidates(force, 'empty-magazine')").values()), ["empty-recipe"])

    def test_the_recipe_named_after_the_item_and_one_made_by_hand_come_first(self):
        lua = runtime()
        self.assertEqual(lua.eval("Recipes.candidates(force, 'stone-wall')[1]"), "stone-wall")
        self.assertEqual(lua.eval("Recipes.for_item(force, 'grenade', hand).name"), "grenade")
        lua.execute("force.recipes['grenade'].enabled = false")
        # only a machine makes one now: still unlocked, so the force can make grenades
        self.assertEqual(lua.eval("Recipes.for_item(force, 'grenade', hand).name"), "maraxsis-pipe-bomb")
        self.assertTrue(lua.eval("Recipes.ready(force, 'grenade')"))
        lua.execute("force.recipes['maraxsis-pipe-bomb'].enabled = false")
        self.assertFalse(lua.eval("Recipes.ready(force, 'grenade')"))
        # a budget of future work may look at one not researched yet
        self.assertEqual(lua.eval("Recipes.for_item(force, 'grenade', hand, true).name"), "grenade")

    def test_a_loop_counts_only_what_it_adds_and_lookups_are_kept(self):
        lua = runtime()
        self.assertEqual(lua.eval("Recipes.yield(protos['kovarex'], 'u235')"), 1)
        self.assertEqual(lua.eval("Recipes.candidates(force, 'u238')[1]"), None)   # it uses more than it makes
        lua.eval("Recipes.candidates(force, 'grenade')")
        lua.eval("Recipes.candidates(force, 'grenade')")
        self.assertEqual(lua.eval("asked"), 2)   # u238 once, grenade once

    def test_hand_steps_start_with_the_deepest_craft_and_need_what_is_held(self):
        lua = runtime()
        lua.execute("""
            recipe('firearm-magazine', 'crafting', {{type='item', name='empty-magazine', amount=1},
                {type='item', name='iron-plate', amount=2}}, {{type='item', name='firearm-magazine', amount=1}})
            held = {['iron-plate']=40}
            function have(i) return held[i] or 0 end""")
        steps = lua.eval("Recipes.hand_steps(force, hand, have, 'firearm-magazine', 10)")
        self.assertEqual([(s.recipe, s.count) for s in steps.values()],
                         [("empty-recipe", 10), ("firearm-magazine", 10)])
        self.assertIsNone(lua.eval("Recipes.hand_steps(force, hand, have, 'firearm-magazine', 11)"))   # 44 plates

    def test_an_entity_is_ready_when_an_item_that_places_it_can_be_made(self):
        lua = runtime()
        lua.execute("prototypes.entity['stone-wall'] = {items_to_place_this={{name='stone-wall', count=1}}}")
        self.assertTrue(lua.eval("Recipes.ready_entity(force, 'stone-wall')"))
        self.assertFalse(lua.eval("Recipes.ready_entity(force, 'gun-turret')"))


if __name__ == "__main__":
    unittest.main()
