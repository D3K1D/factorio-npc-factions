"""Engineers' starts on a fresh map (settlements.lua find_at_start), in Factorio's Lua 5.2 with a stub map."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]

MAP = """
    entities, tiles, made = {}, {}, {}
    function add(name, kind, x, y, extra)
        local e = {name=name, type=kind, position={x=x, y=y}, valid=true, minable=true, amount=1000,
                   force=extra and extra.force or 'neutral'}
        e.destroy = function() e.valid = false end
        entities[#entities + 1] = e
        return e
    end
    local function near(e, f)
        if not e.valid then return false end
        if f.name and e.name ~= f.name then return false end
        if f.type then
            local types = type(f.type) == 'table' and f.type or {f.type}
            local ok = false
            for _, t in ipairs(types) do if e.type == t then ok = true end end
            if not ok then return false end
        end
        if f.force and e.force ~= f.force then return false end
        if f.position then
            local dx, dy = e.position.x - f.position.x, e.position.y - f.position.y
            return dx * dx + dy * dy <= f.radius * f.radius
        end
        if f.area then
            return e.position.x >= f.area[1][1] and e.position.x < f.area[2][1]
                and e.position.y >= f.area[1][2] and e.position.y < f.area[2][2]
        end
        return true
    end
    surface = {index=1, map_gen_settings={starting_points={{x=0, y=0}, {x=1000, y=0}}},
        is_chunk_generated=function() return true end,
        request_to_generate_chunks=function() end, force_generate_chunk_requests=function() end,
        find_entities_filtered=function(f)
            local out = {}
            for _, e in ipairs(entities) do if near(e, f) then out[#out + 1] = e end end
            return out
        end,
        count_entities_filtered=function(f)
            local n = 0
            for _, e in ipairs(entities) do if near(e, f) then n = n + 1 end end
            return n
        end,
        find_non_colliding_position=function(_, p) return {x=p.x or p[1], y=p.y or p[2]} end,
        get_tile=function(x, y) return {name=tiles[x .. ':' .. y] or 'grass-1'} end,
        set_tiles=function(list) for _, t in ipairs(list) do
            tiles[t.position[1] .. ':' .. t.position[2]] = t.name end end,
        find_tiles_filtered=function(f)
            local out = {}
            for key, name in pairs(tiles) do
                local x, y = key:match('(-?%d+):(-?%d+)')
                x, y = tonumber(x), tonumber(y)
                if (x - f.position.x) ^ 2 + (y - f.position.y) ^ 2 <= f.radius ^ 2 then
                    out[#out + 1] = {name=name, position={x=x, y=y}}
                end
            end
            return out
        end,
        create_entity=function(spec)
            local e = add(spec.name, spec.name == 'tree-01' and 'tree' or 'resource', spec.position[1] or spec.position.x,
                          spec.position[2] or spec.position.y)
            made[spec.name] = (made[spec.name] or 0) + 1
            return e
        end}
    player = {surface=surface}
    storage = {npcs={}}
    game = {tick=10}
    S = assert(load(source))()
"""


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "settlements.lua").read_text(encoding="utf-8")
    lua.execute(MAP)
    return lua


class StartTests(unittest.TestCase):
    def test_the_most_compact_spot_is_chosen_and_completed(self):
        lua = runtime()
        lua.execute("""
            -- iron right at the start point but its coal and copper far; iron a little further out with
            -- coal, copper, stone, water and trees all beside it
            add('iron-ore', 'resource', 1005, 0)
            add('coal', 'resource', 1120, 0)
            add('iron-ore', 'resource', 1060, 60)
            add('coal', 'resource', 1066, 60)
            add('copper-ore', 'resource', 1060, 70)
            add('stone', 'resource', 1052, 60)
            add('tree', 'tree', 1060, 50)
            tiles['1070:60'] = 'water'
            trace = {}
            home, why, survey = S.find_at_start(player, 1, 1, 64, trace)
        """)
        self.assertEqual((lua.eval("home.x"), lua.eval("home.y")), (1060, 60))
        self.assertIsNone(lua.eval("trace.prepared"))                # nothing missing there: nothing laid
        self.assertEqual(lua.eval("survey.landmarks['copper-ore'].available"), True)

    def test_what_a_start_lacks_is_laid_down_and_nests_cleared(self):
        lua = runtime()
        lua.execute("""
            add('iron-ore', 'resource', 1005, 0)
            nest = add('biter-spawner', 'unit-spawner', 1040, 0, {force='enemy'})
            far = add('biter-spawner', 'unit-spawner', 1300, 0, {force='enemy'})
            trace = {}
            home, why, survey = S.find_at_start(player, 1, 1, 64, trace)
        """)
        self.assertIsNotNone(lua.eval("home"))
        self.assertEqual(sorted(lua.eval("trace.prepared").split(",")), ["coal", "copper-ore", "stone", "water", "wood"])
        self.assertFalse(lua.eval("nest.valid"))                    # too close: cleared
        self.assertTrue(lua.eval("far.valid"))
        self.assertEqual(lua.eval("made['copper-ore']"), 81)         # a small patch (radius 5)
        for name in ("coal", "copper-ore", "stone"):
            self.assertTrue(lua.eval(f"survey.landmarks['{name}'].available"))
            self.assertLess(lua.eval(f"survey.landmarks['{name}'].distance"), 30)
        self.assertTrue(lua.eval("survey.landmarks.water.available"))

    def test_with_no_iron_near_the_start_it_is_all_laid_down(self):
        lua = runtime()
        lua.execute("trace = {} home, why, survey = S.find_at_start(player, 1, 1, 64, trace)")
        self.assertEqual((lua.eval("home.x"), lua.eval("home.y")), (1000, 0))
        self.assertIn("iron-ore", lua.eval("trace.prepared"))


if __name__ == "__main__":
    unittest.main()
