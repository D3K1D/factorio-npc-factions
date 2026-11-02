"""What a plan request's export costs the game (DesignedStart.export runs inside an RCON command, on every peer): run 55's
bus checkpoint froze 30 to 60 s at every plan request, its native layout suggestions searching a built-up home to the
end (OP-3, 2026-10-26)."""
import unittest

import test_retire


def runtime():
    lua = test_retire.runtime()
    lua.execute("""
        made = {}
        for purpose, s in pairs(DS.SUGGESTED) do
            s.make = function(n) made[#made + 1] = purpose; return {entities={{position={x=5, y=5}}}} end
        end
    """)
    return lua


class SuggestionTests(unittest.TestCase):
    def test_a_goal_built_is_not_searched_again_unless_it_repeats_or_its_ore_runs_out(self):
        lua = runtime()
        lua.execute("""
            npc.running_out = {{resource='iron-ore', minutes=10}}
            out = DS.suggestions(npc, {x=1, y=2}, {'research-lab', 'basic-defense', 'electric-iron-mining',
                'electric-copper-mining'})
        """)
        made = set(lua.eval("made").values())
        self.assertNotIn("research-lab", made)
        self.assertNotIn("electric-copper-mining", made)        # (its ore is not running out)
        self.assertTrue({"basic-defense", "electric-iron-mining", "steam-power"} <= made)
        self.assertEqual(dict(lua.eval("out['steam-power'].entities[1].position").items()), {"x": 4, "y": 3})
        self.assertIsNone(lua.eval("npc.checks_left"))          # (only while a suggestion is searched)

    def test_a_search_round_a_built_up_home_stops_at_its_budget(self):
        lua = test_retire.runtime()
        lua.execute("""
            checks = 0
            DS.SUGGESTION_CHECKS = 100   -- (fewer than the spots round a built-up home)
            prototypes.entity['gun-turret'] = {tile_width=2, tile_height=2}
            npc.home = {x=0, y=0}
            npc.design_history = {}
            npc.force = {recipes={['gun-turret']={enabled=true}, ['firearm-magazine']={enabled=true}}}
            npc.character = {valid=true, surface={
                can_place_entity=function() checks = checks + 1; return false end,
                count_tiles_filtered=function() return 0 end,
                find_entities_filtered=function() return {{type='assembling-machine'}} end,
                find_nearest_enemy=function() return nil end}}
            out = DS.suggestions(npc, {x=0, y=0}, {})
        """)
        self.assertIsNone(lua.eval("out['basic-defense']"))
        self.assertEqual(lua.eval("checks"), lua.eval("DS.SUGGESTION_CHECKS"))   # (every spot fails: it stops there)


class RockTests(unittest.TestCase):
    """The rocks round home, for the goal weighing's stone on hand (the user, 2026-10-26: early stone from the rocks
    round the spawn)."""

    def test_what_the_rocks_within_reach_give_by_their_products(self):
        lua = test_retire.runtime()
        lua.execute("""
            asked = nil
            prototypes.entity['huge-rock'] = {mineable_properties={minable=true, products={
                {type='item', name='stone', amount_min=24, amount_max=50},
                {type='item', name='coal', amount_min=24, amount_max=50}}}}
            prototypes.entity['big-rock'] = {mineable_properties={minable=true, products={
                {type='item', name='stone', amount=20}}}}
            prototypes.entity['ruin'] = {mineable_properties={minable=false, products={{type='item', name='stone', amount=9}}}}
            prototypes.entity['sulfur-rock'] = {mineable_properties={minable=true, required_fluid='sulfuric-acid',
                products={{type='item', name='sulfur', amount=5}}}}
            prototypes.entity['lucky-rock'] = {mineable_properties={minable=true, products={
                {type='item', name='iron-ore', amount=10, independent_probability=0.5},
                {type='item', name='copper-ore', amount=3, extra_count_fraction=1, independent_probability=1,
                 shared_probability={min=0.25, max=0.75}},
                {type='fluid', name='water', amount=5}}}}
            local list = {}
            for _, name in ipairs({'huge-rock', 'huge-rock', 'big-rock', 'ruin', 'sulfur-rock', 'lucky-rock'}) do
                list[#list + 1] = {name=name, minable=true}
            end
            list[#list + 1] = {name='big-rock', minable=false}   -- (made unminable: the worker would not take it)
            npc.character = {valid=true, surface={find_entities_filtered=function(f) asked = f; return list end}}
            out = DS.rocks(npc, {x=10, y=20})
        """)
        out = dict(lua.eval("out").items())
        self.assertEqual(out, {"stone": 2 * 37 + 20, "coal": 2 * 37, "iron-ore": 5, "copper-ore": 2, "count": 4,
                               "radius": 96})
        asked = dict(lua.eval("asked").items())
        self.assertEqual((asked["radius"], asked["type"], asked["force"]), (96, "simple-entity", "neutral"))
        self.assertEqual(dict(asked["position"].items()), {"x": 10, "y": 20})


class ChanceTests(unittest.TestCase):
    def test_a_recipe_product_chance_read_from_its_shared_range(self):
        """2.1 gives uranium processing's products probability 1 and the real split in shared ranges (read live)."""
        lua = test_retire.runtime()
        lua.execute("""
            u235 = DS.expected_yield({name='uranium-235', amount=1, probability=1, independent_probability=1,
                                      shared_probability={min=0, max=0.007}})
            u238 = DS.expected_yield({name='uranium-238', amount=1, probability=1, independent_probability=1,
                                      shared_probability={min=0.007, max=1}})
        """)
        self.assertAlmostEqual(lua.eval("u235"), 0.007)
        self.assertAlmostEqual(lua.eval("u238"), 0.993)


if __name__ == "__main__":
    unittest.main()
