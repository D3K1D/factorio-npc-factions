"""No Lua assignment of several names from one `and`/`or` expression around a call.

Inside `a and f()` Lua keeps only f's first result, so `local job,meta=ok and f()` always leaves meta nil. Rehearsal
7e (2026-10-29) crashed the game on exactly that (factory.lua, a rebuild's meta). This scans every mod script.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ASSIGN = re.compile(r"^\s*(?:local\s+)?([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)+)\s*=(?!=)\s*(.+)$")


def top_level(expr):
    """The expression's top-level comma-separated parts (outside brackets and strings), comments cut."""
    parts, depth, quote, cur, i = [], 0, None, "", 0
    while i < len(expr):
        ch = expr[i]
        if quote:
            cur += ch
            if ch == "\\":
                cur += expr[i + 1:i + 2]
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote, cur = ch, cur + ch
        elif expr.startswith("--", i) or (ch == ";" and depth == 0):
            break   # (a comment, or the statement ends and the next begins on the same line)
        elif ch in "([{":
            depth, cur = depth + 1, cur + ch
        elif ch in ")]}":
            depth, cur = depth - 1, cur + ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


def flattened(expr):
    """The expression with every bracketed part removed, to see its own top-level operators."""
    out, depth = "", 0
    for ch in expr:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            out += ch
    return out


def findings():
    found = []
    for path in sorted((ROOT / "mod").rglob("*.lua")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = ASSIGN.match(line)
            if not m or line.lstrip().startswith("--"):
                continue
            names = [s for s in m.group(1).split(",") if s.strip()]
            parts = top_level(m.group(2))
            last = parts[-1].strip()
            if len(parts) >= len(names) or "(" not in last:
                continue
            if re.search(r"\b(and|or|not)\b", flattened(last)):
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    return found


class MultiValueTests(unittest.TestCase):
    def test_no_call_results_lost_inside_and_or(self):
        self.assertEqual(findings(), [], "a call inside and/or keeps one result; call it on its own line")

    def test_the_check_catches_the_crash_shape(self):
        line = "local again,again_meta=not hold and DesignedStart.rebuild_now and DesignedStart.rebuild_now(npc,tick)"
        m = ASSIGN.match(line)
        self.assertTrue(re.search(r"\b(and|or|not)\b", flattened(top_level(m.group(2))[-1])))
        safe = "local ok_travel,travel=pcall(function() return Drive.travel and Drive.travel(npc,tick) end)"
        self.assertFalse(re.search(r"\b(and|or|not)\b", flattened(top_level(ASSIGN.match(safe).group(2))[-1])))


if __name__ == "__main__":
    unittest.main()
