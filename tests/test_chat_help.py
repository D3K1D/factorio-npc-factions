"""Asking an engineer for help by chat, in Factorio's Lua 5.2 (other modules stubbed)."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "chat.lua").read_text(encoding="utf-8")
    lua.globals().radio_source = (ROOT / "mod" / "scripts" / "radio.lua").read_text(encoding="utf-8")
    lua.execute("""
        package.preload['scripts.radio'] = function() return assert(load(radio_source))() end
        package.preload['scripts.team_supply'] = function() return {reply=function() return false end} end
        for _, name in ipairs({'scripts.chat_intents', 'scripts.diplomacy', 'scripts.personality',
                               'scripts.player_trade', 'scripts.character', 'scripts.teach', 'scripts.agent'}) do
            package.preload[name] = function()
                return {classify=function() return {intent='unknown', accepted=false} end, version='stub'}
            end
        end
        thinking = false
        heard_by = {}
        package.preload['scripts.agent'] = function()
            return {thinking=function() return thinking end,
                    event=function(npc, e) heard = e; heard_by[#heard_by + 1] = npc.name; return true end}
        end
        Chat = assert(load(source))()
        game = {tick = 1000}
        said = {}
        player = {valid=true, index=1, position={x=40, y=-20}, force='player',
                  print=function(text) said[#said + 1] = text end}
        friends = false
        npc = {id=1, name='Ada', character={valid=true},
               force={get_friend=function(_, other) return friends end}, design={}}
        storage = {npcs={npc}}
    """)
    return lua


class ChatHelpTests(unittest.TestCase):
    def test_an_ally_can_ask_for_turrets_by_their_base(self):
        lua = runtime()
        self.assertTrue(lua.eval("Chat.handle(player, 'Ada: please defend my base')"))
        self.assertIsNone(lua.eval("npc.help_request"))                  # not allies: no help
        self.assertIn("not your ally", lua.eval("said[#said]"))
        lua.execute("friends = true; game.tick = 2000")
        self.assertTrue(lua.eval("Chat.handle(player, 'Ada: can you protect my base?')"))
        self.assertEqual(lua.eval("npc.help_request.kind"), "defend")
        self.assertEqual((lua.eval("npc.help_request.position.x"), lua.eval("npc.help_request.position.y")), (40, -20))


    def test_an_engineer_with_a_strategist_hears_the_message_itself(self):
        lua = runtime()
        lua.execute("thinking = true; friends = true")
        self.assertTrue(lua.eval("Chat.handle(player, 'Ada: Kevin is planning another assault, hit him first')"))
        self.assertEqual(lua.eval("heard.kind"), "chat")
        self.assertEqual(lua.eval("heard.from"), None)   # the stub player has no name
        self.assertIn("kevin is planning", lua.eval("heard.text"))
        self.assertIsNone(lua.eval("npc.help_request"))          # no fixed reaction: it decides

    def test_everyone_hears_a_message_for_everyone(self):
        lua = runtime()
        lua.execute("""thinking = true
            storage.npcs[2] = {id=2, name='Borin', character={valid=true}, force=npc.force, design={}}""")
        self.assertTrue(lua.eval("Chat.handle(player, 'Guys no more requests, I will ask if I need something')"))
        self.assertEqual(list(lua.eval("heard_by").values()), ["Ada", "Borin"])
        self.assertEqual(lua.eval("heard.text"), "no more requests, i will ask if i need something")
        self.assertTrue(lua.eval("heard.to_everyone"))
        lua.execute("thinking = false")
        self.assertFalse(lua.eval("Chat.handle(player, 'all: hello')"))   # nobody listening: plain chat


if __name__ == "__main__":
    unittest.main()
