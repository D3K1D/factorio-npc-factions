"""Reading a player's answer to "mind if I grab...?" (team_supply.lua TeamSupply.answer): only a plain,
unmixed yes lets an engineer into a player's chest (Codex review, 2026-10-22)."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def answer():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "team_supply.lua").read_text(encoding="utf-8")
    lua.execute("""
        for _, m in ipairs({'scripts.team_world', 'scripts.voice', 'scripts.recipes', 'scripts.radio',
                            'scripts.agent', 'scripts.character'}) do
            package.preload[m] = function() return {active=function() return true end} end
        end
        S = assert(load(source))()""")
    return lua.globals().S.answer


class ConsentTests(unittest.TestCase):
    def test_a_plain_yes_is_a_yes_and_anything_saying_no_is_not(self):
        read = answer()
        for text in ("yes", "Sure!", "go ahead", "ok", "no problem", "not at all", "I don't mind", "all yours"):
            self.assertEqual(read(text, True), "yes", text)
        for text in ("no", "nope", "don't take it", "not okay", "yes but leave the rest", "don’t", "mine"):
            self.assertEqual(read(text, True), "no", text)

    def test_talk_is_not_an_answer(self):
        read = answer()
        self.assertIsNone(read("yes I'm building the bus now near the iron", False))   # chatter with a yes in it
        self.assertIsNone(read("yes build labs", False))                               # (Codex's second case)
        self.assertIsNone(read("ok wait", True))
        self.assertIsNone(read("what?", True))
        self.assertEqual(read("yes take what you need", True), "yes")
        self.assertEqual(read("yeah sure", False), "yes")                              # a short plain reply


if __name__ == "__main__":
    unittest.main()
