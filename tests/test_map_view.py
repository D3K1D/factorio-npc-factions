"""Engineers shown on the players' map (map_view.lua), in Factorio's Lua 5.2 with the game stubbed."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "map_view.lua").read_text(encoding="utf-8")
    lua.execute("""
        MapView = assert(load(source))()
        settings = {global={['npc-factions-show-on-map']={value=true}}}
        charted, made = {}, {}
        surface = {}
        player_force = {name='player'}
        function player_force.chart(s, area) charted[#charted+1] = area end
        function player_force.add_chart_tag(s, spec)
            local tag = {valid=true, text=spec.text, position=spec.position}
            function tag.destroy() tag.valid = false end
            made[#made+1] = tag
            return tag
        end
        game = {connected_players={{force=player_force}}}
        ada = {id=1, name='Ada', force={name='npc-faction-1'}, home={x=1500, y=0},
               character={valid=true, surface=surface, position={x=1400, y=10}}}
        storage = {npcs={ada}}
    """)
    return lua


class MapViewTests(unittest.TestCase):
    def test_an_engineer_is_marked_where_it_is_and_its_base_charted(self):
        lua = runtime()
        lua.execute("MapView.tick(60)")
        self.assertEqual(lua.eval("made[1].text"), "Ada")
        self.assertEqual(lua.eval("#charted"), 2)                     # around it, and its base
        self.assertEqual(lua.eval("charted[2][2][1]"), 1596)
        lua.execute("ada.character.position = {x=1360, y=20}; MapView.tick(360)")
        self.assertFalse(lua.eval("made[1].valid"))                   # the old marker moves on
        self.assertEqual(lua.eval("made[2].position.x"), 1360)
        self.assertEqual(lua.eval("#charted"), 3)                     # the base only once a minute

    def test_switched_off_or_nobody_watching_leaves_no_markers(self):
        lua = runtime()
        lua.execute("MapView.tick(60); settings.global['npc-factions-show-on-map'].value = false; MapView.tick(360)")
        self.assertFalse(lua.eval("made[1].valid"))
        self.assertEqual(lua.eval("#made"), 1)
        lua.execute("settings.global['npc-factions-show-on-map'].value = true; game.connected_players = {}")
        lua.execute("MapView.tick(660)")
        self.assertEqual(lua.eval("#made"), 1)


if __name__ == "__main__":
    unittest.main()
