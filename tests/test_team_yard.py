"""The team yard's outer edges belong to the wall ring: the engineers whose own outer edge runs on into
them hold them (team_world.lua bands, companion team_area and perimeter)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import perimeter, team_area  # noqa: E402


def lua_team():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "team_world.lua").read_text(encoding="utf-8")
    lua.execute("""
        settings = {global={['npc-factions-scenario']={value='team'}}}
        storage = {}
        surface = {index=1, find_entities_filtered=function() return {} end}
        game = {tick=0, surfaces={surface}, forces={player={get_spawn_position=function() return {x=0, y=0} end}}}
        T = assert(load(source))()
        function band(name, i) local b = T.plot(name).bands[i] return b end
    """)
    return lua


class GameTests(unittest.TestCase):
    def test_the_yards_east_edge_goes_to_npc_5_and_its_south_edge_to_npc_7(self):
        lua = lua_team()
        east = lua.eval("band('npc-5', 1)")
        self.assertEqual((east.name, tuple(east.box[i] for i in range(1, 5)), east.corner_lo, east.corner_hi),
                         ("east", (552, 192, 576, 576), False, True))
        south = lua.eval("band('npc-7', 1)")
        self.assertEqual((south.name, tuple(south.box[i] for i in range(1, 5)), south.corner_lo, south.corner_hi),
                         ("south", (192, 552, 576, 576), False, True))
        self.assertIsNone(lua.eval("T.plot('npc-1').bands"))
        self.assertEqual(lua.eval("#T.wall_edges(T.plot('npc-5'))"), 2)   # its own east edge and the yard's

    def test_it_may_build_its_wall_in_the_band_but_nowhere_else_in_the_yard(self):
        lua = lua_team()
        lua.execute("""five = {id=5, character={valid=true, surface=surface}}
            function may(x0, y0, x1, y1) local r = {T.may_build(five, {{x0, y0}, {x1, y1}})} return r[1], r[2] end""")
        self.assertTrue(lua.eval("may(570, 300, 572, 302)"))                  # on the yard's east edge
        self.assertEqual(lua.eval("select(2, may(400, 400, 402, 402))"), "other_plot")   # the yard's middle
        self.assertTrue(lua.eval("T.holds(T.plot('npc-5'), {x=571, y=300})"))
        self.assertFalse(lua.eval("T.holds(T.plot('npc-5'), {x=400, y=400})"))

    def test_an_older_team_area_gets_its_bands_when_next_read(self):
        lua = lua_team()
        lua.execute("""storage.team = T.layout({x=0, y=0})
            for _, p in ipairs(storage.team.plots) do p.bands = nil end
            storage.team.bands_assigned = nil""")
        self.assertEqual(lua.eval("T.state().plots[1].name"), "npc-1")
        self.assertEqual(lua.eval("band('npc-5', 1).name"), "east")


class PlannerTests(unittest.TestCase):
    SITE = {"origin": {"x": 0, "y": 0},
            "team": {"plot": [192, -192, 576, 192], "area": [-576, -576, 576, 576], "no_build": [],
                     "outer": ["east"],
                     "bands": [{"name": "east", "box": [552, 192, 576, 576], "corner_lo": False, "corner_hi": True}]}}

    def test_the_band_is_one_more_stretch_of_wall_line(self):
        found = perimeter.edges(self.SITE)
        self.assertEqual([(e.name, e.lo, e.hi) for e in found], [("east", -192, 191), ("east", 192, 573)])
        self.assertEqual(found[1].row(perimeter.WALL_OFFSET), 573)   # the same line as its own east wall

    def test_the_band_is_its_ground_and_the_rest_of_the_yard_is_not(self):
        team = team_area.team_of(self.SITE)
        self.assertTrue(team.box_ok(570, 300, 572, 302))
        self.assertFalse(team.box_ok(400, 400, 402, 402))
        tiles = team.forbidden_tiles((540, 280, 576, 320))
        self.assertNotIn((570, 300), tiles)
        self.assertIn((545, 300), tiles)                                      # past the band: the yard


if __name__ == "__main__":
    unittest.main()
