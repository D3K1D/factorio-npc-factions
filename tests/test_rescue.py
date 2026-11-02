"""An engineer boxed in by its own buildings frees itself (rescue.lua), in Factorio's Lua 5.2."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "rescue.lua").read_text(encoding="utf-8")
    lua.execute("""
        table.unpack = table.unpack or unpack
        logged = {}
        function log(s) logged[#logged+1] = s end
        R = assert(load(source))()
        force = {name='f'}
        entities = {}
        function solid(kind, x, y, mine)
            local e = {valid=true, type=kind, name=kind, force=mine and force or {name='other'}, minable=true,
                       position={x=x+0.5, y=y+0.5},
                       bounding_box={left_top={x=x+0.1, y=y+0.1}, right_bottom={x=x+0.9, y=y+0.9}}}
            entities[#entities+1] = e
            return e
        end
        surface = {find_entities_filtered=function() return entities end,
                   find_tiles_filtered=function() return {} end}
        character = {valid=true, surface=surface, force=force, position={x=0.5, y=0.5}}
        npc = {id=1, character=character}
        function fail_three(tick)
            for i = 1, 3 do R.note(npc, 'path_ended_outside_reach', tick + i) end
        end
    """)
    return lua


class RescueTests(unittest.TestCase):
    def test_shut_in_by_its_own_inserters_it_picks_one_up(self):
        lua = runtime()
        lua.execute("""
            for x = -2, 2 do for y = -2, 2 do
                if math.abs(x) == 2 or math.abs(y) == 2 then solid('inserter', x, y, true) end
            end end
            near = solid('inserter', 1, 0, true)   -- the closest wall piece
            entities[#entities] = nil
            for i, e in ipairs(entities) do if e.position.x == 2.5 and e.position.y == 0.5 then near = e end end
            fail_three(100)
        """)
        piece = lua.eval("R.blocker(npc, 110)")
        self.assertIsNotNone(piece)
        self.assertEqual(piece.type, "inserter")
        self.assertIn("boxed in", lua.eval("logged[1]"))

    def test_open_ground_or_too_few_failures_change_nothing(self):
        lua = runtime()
        lua.execute("solid('assembling-machine', 1, 0, true)")
        lua.execute("R.note(npc, 'path_ended_outside_reach', 100)")
        self.assertIsNone(lua.eval("R.blocker(npc, 110)"))      # one failure is not a pattern
        lua.execute("fail_three(200)")
        self.assertIsNone(lua.eval("R.blocker(npc, 210)"))      # a wall on one side: not shut in
        lua.execute("R.note(npc, 'item_missing', 300)")
        self.assertEqual(len(lua.eval("npc.path_failures")), 0)  # only path failures count

    def test_walls_that_are_not_its_own_are_left_alone(self):
        lua = runtime()
        lua.execute("""
            for x = -2, 2 do for y = -2, 2 do
                if math.abs(x) == 2 or math.abs(y) == 2 then solid('inserter', x, y, false) end
            end end
            fail_three(100)
        """)
        self.assertIsNone(lua.eval("R.blocker(npc, 110)"))


if __name__ == "__main__":
    unittest.main()
