"""Radio-style chat lines: the companion's filter (companion/radio.py) and the game's (radio.lua) agree."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime

from companion import radio

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("Iron's running low — I'll grab some more.", "Iron's running low, I'll grab some more."),
    ("On it–heading north now.", "On it, heading north now."),
    ("Walls up - turrets next.", "Walls up, turrets next."),
    ("“Copy that”, she said…", "\"Copy that\", she said..."),
    ("Got it; moving.", "Got it, moving."),
    ("Biters north. Two big ones. Need help. Hurry.", "Biters north. Two big ones."),
    ("Short and sweet.", "Short and sweet."),
    ("x" * 30 + " " + "word " * 40, None),     # long: cut at a word, under 140
]


def lua_line():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "radio.lua").read_text(encoding="utf-8")
    lua.execute("Radio = assert(load(source))()")
    return lambda text: lua.globals().Radio.line(text)


class RadioTests(unittest.TestCase):
    def test_no_dashes_plain_quotes_two_short_sentences(self):
        game = lua_line()
        for text, want in CASES:
            for say in (radio.line, game):
                out = say(text)
                self.assertNotRegex(out, "[—–―−“”‘’]", text)
                self.assertNotIn(" - ", out)
                self.assertLessEqual(len(out), 140)
                if want is not None:
                    self.assertEqual(out, want, f"{say}: {text!r}")


if __name__ == "__main__":
    unittest.main()
