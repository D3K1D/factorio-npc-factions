"""Ghosts, the one way the mod puts them down (W-35, in Factorio's Lua 5.2). stage-nuclear aborted the engine at tick
627252: an inner transport-belt ghost under a real belt of its own (ConsistencyScraper, "entity-in-the-way"). Ghosts
were put down with no build check, and its hands built over its own ghosts keeping them. Now a ghost goes down only
where the game lets one go, every own ghost under a building is cleared as it goes down, and no file puts a ghost down
any other way (the lint below)."""
from pathlib import Path
import re
import unittest

from lupa import LuaRuntime

import test_obstacles

ROOT = Path(__file__).resolve().parents[1]

SETUP = r"""
    defines = {build_check_type={manual_ghost=3, manual=1}}
    prototypes = {entity={['transport-belt']={tile_width=1, tile_height=1},
                          ['stone-furnace']={tile_width=2, tile_height=2},
                          ['boiler']={tile_width=3, tile_height=2}}}
    ghosts, created, allowed = {}, {}, true
    surface = {
        can_place_entity=function(spec) asked = spec; return allowed end,
        create_entity=function(spec)
            local g = {valid=true, name=spec.name, ghost_name=spec.inner_name, position=spec.position, force=spec.force}
            g.destroy = function() g.valid = false end
            created[#created + 1] = spec; ghosts[#ghosts + 1] = g
            return g
        end,
        find_entities_filtered=function(f)
            local out = {}
            for _, g in ipairs(ghosts) do
                local p = g.position
                local inside
                if f.area then
                    inside = p.x >= f.area[1][1] and p.x <= f.area[2][1] and p.y >= f.area[1][2] and p.y <= f.area[2][2]
                else
                    inside = math.abs(p.x - f.position.x) <= (f.radius or 0) and math.abs(p.y - f.position.y) <= (f.radius or 0)
                end
                if g.valid and inside and g.force == f.force then out[#out + 1] = g end
            end
            return out
        end}
    Ghosts = assert(load(ghosts_source, '@ghosts'))()
"""


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().ghosts_source = (ROOT / "mod" / "scripts" / "ghosts.lua").read_text(encoding="utf-8")
    lua.execute(SETUP)
    return lua


class GhostTests(unittest.TestCase):
    def test_a_ghost_goes_down_only_where_the_game_lets_a_ghost_go(self):
        lua = runtime()
        lua.execute("allowed = false; g = Ghosts.create(surface, {inner_name='transport-belt', position={x=1.5, y=1.5}, force='f'})")
        self.assertIsNone(lua.eval("g"))
        self.assertEqual(lua.eval("#created"), 0)
        # (asked of the inner building, as a ghost's: asked as "entity-ghost" the game says no on bare ground)
        self.assertEqual((lua.eval("asked.name"), lua.eval("asked.build_check_type")), ("transport-belt", 3))

    def test_one_already_there_is_that_one_not_a_second(self):
        lua = runtime()
        lua.execute("""a = Ghosts.create(surface, {inner_name='transport-belt', position={x=1.5, y=1.5}, force='f'})
                       b = Ghosts.create(surface, {inner_name='transport-belt', position={x=1.5, y=1.5}, force='f'})""")
        self.assertTrue(lua.eval("a ~= nil and a == b"))
        self.assertEqual(lua.eval("#created"), 1)
        self.assertEqual(lua.eval("created[1].name"), "entity-ghost")

    def test_every_own_ghost_on_a_buildings_footprint_is_cleared(self):
        lua = runtime()
        lua.execute("""
            for _, p in ipairs({{x=0.5, y=0.5}, {x=1.5, y=0.5}, {x=3.5, y=0.5}}) do
                Ghosts.create(surface, {inner_name='transport-belt', position=p, force='f'})
            end
            Ghosts.create(surface, {inner_name='transport-belt', position={x=0.5, y=1.5}, force='other'})
            n = Ghosts.clear(surface, 'stone-furnace', {x=1, y=1}, 0, 'f')""")
        self.assertEqual(lua.eval("n"), 2)                                       # (its own on the 2x2; not beyond)
        self.assertTrue(lua.eval("ghosts[3].valid"))
        self.assertTrue(lua.eval("ghosts[4].valid"))                             # (another force's left alone)

    def test_a_long_one_turned_lies_across(self):
        lua = runtime()
        lua.execute("a = Ghosts.area('boiler', {x=0, y=0}, 4)")
        self.assertAlmostEqual(lua.eval("a[2][1] - a[1][1]"), 2 - 0.02)
        self.assertAlmostEqual(lua.eval("a[2][2] - a[1][2]"), 3 - 0.02)


class WorkerBuildTests(unittest.TestCase):
    def test_a_building_put_down_by_hand_clears_its_own_ghost_under_it(self):
        lua = test_obstacles.worker_with("""
            cleared = {}
            stubs['scripts.ghosts'] = {clear=function(s, name, pos) cleared[#cleared + 1] = name end}
            W = assert(load(worker_source, '@worker'))()""")
        src = (ROOT / "mod" / "scripts" / "worker.lua").read_text(encoding="utf-8")
        # (each of the worker's three real builds clears what stands under it, on success)
        self.assertEqual(src.count("Ghosts.clear(entity.surface, entity.name, entity.position, entity.direction, "
                                   "entity.force)"), 3)
        self.assertIsNotNone(lua.eval("W"))


class GhostLint(unittest.TestCase):
    def test_no_ghost_is_put_down_outside_the_helper(self):
        bad = []
        for path in sorted((ROOT / "mod").rglob("*.lua")):
            if path.name == "ghosts.lua":
                continue
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"create_entity\s*[\({,]", text):
                window = text[m.start():m.start() + 200]
                if re.search(r"""name\s*=\s*["']entity-ghost["']""", window):
                    bad.append(f"{path.name}:{text.count(chr(10), 0, m.start()) + 1}")
        self.assertEqual(bad, [], "put a ghost down with Ghosts.create (mod/scripts/ghosts.lua): a ghost under a "
                                  "building of its own aborts the engine (W-35)")


if __name__ == "__main__":
    unittest.main()
