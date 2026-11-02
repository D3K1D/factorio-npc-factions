"""The map dump (base_map.lua) is built a few chunks per tick, in Factorio's Lua 5.2."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class BaseMapTests(unittest.TestCase):
    def test_a_dump_is_built_over_ticks_and_counts_each_building_once(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_map.lua").read_text(encoding="utf-8")
        lua.globals().team_source = (ROOT / "mod" / "scripts" / "team_world.lua").read_text(encoding="utf-8")
        lua.execute("""
            package.preload['scripts.team_world'] = function() return assert(load(team_source))() end
            M = assert(load(source))()
            storage = {}
            game = {tick = 100}
            defines = {build_check_type = {manual = 0}}
            prototypes = {tile = {water = {}}, entity = {}}
            local function inside(p, area)
                return p.x >= area[1][1] and p.x < area[2][1] and p.y >= area[1][2] and p.y < area[2][2]
            end
            local function box_hits(e, area)   -- like the game: anything whose box touches the area
                return e.position.x + 1.5 >= area[1][1] and e.position.x - 1.5 < area[2][1]
                   and e.position.y + 1.5 >= area[1][2] and e.position.y - 1.5 < area[2][2]
            end
            things = {
                {valid=true, name='assembling-machine-2', type='assembling-machine', position={x=31.5, y=10.5},
                 direction=0, prototype={tile_width=3, tile_height=3}, force={name='f'},
                 get_recipe=function() return nil end, products_finished=0},
                {valid=true, name='iron-ore', type='resource', position={x=40.5, y=-3.5},
                 force={name='neutral'}, prototype={tile_width=1, tile_height=1}},
            }
            scans = 0
            surface = {
                is_chunk_generated = function() return true end,
                find_entities_filtered = function(filter)
                    scans = scans + 1
                    local out = {}
                    if filter.area then
                        for _, e in ipairs(things) do if box_hits(e, filter.area) then out[#out + 1] = e end end
                    end
                    return out
                end,
                find_tiles_filtered = function(filter)
                    local p = {x=50, y=20}
                    return inside(p, filter.area) and {{position=p}} or {}
                end,
                can_place_entity = function() return false end,
            }
            npc = {id=1, name='Ada', force={name='f'}, home={x=0, y=0},
                   character={valid=true, surface=surface, position={x=0, y=0}}}
            storage.npcs = {npc}
        """)
        first = lua.eval("M.dump(npc, 64)")
        self.assertEqual((first.ok, first.reason), (False, "building"))
        self.assertEqual(lua.eval("scans"), 0)                   # nothing read in the asking tick
        for tick in range(101, 140):
            lua.execute(f"M.tick({tick})")
            self.assertLessEqual(lua.eval("scans"), 4 * (tick - 100) + 1)   # a few chunks per tick
        done = lua.eval("M.dump(npc, 64)")
        self.assertTrue(done.ok)
        names = [e.n for e in done.entities.values()]
        self.assertEqual(names, ["assembling-machine-2"])        # on a chunk edge, counted once
        self.assertIn("iron-ore", list(done.resources.keys()))
        self.assertEqual(len(done.water), 1)
        self.assertIsNone(lua.eval("storage.map_jobs[1]"))        # collected


if __name__ == "__main__":
    unittest.main()
