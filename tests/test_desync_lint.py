"""Multiplayer safety of the mod's Lua: nothing that lets a joining player's game drift from the host's (a desync).

A player who joins a running game loads the host's save. What is in `storage` comes with it; anything else the
mod's Lua keeps between ticks starts afresh on the joiner's machine while the host's copy has run on. Each test
reads every Lua file in `mod/` and refuses one way that can happen. A new exception goes in its list with the
reason it is the same on every machine.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "mod" / "control.lua"] + sorted((ROOT / "mod" / "scripts").glob("*.lua"))

# File-level locals written from inside functions, with why they are the same on every machine.
MODULE_STATE = {
    ("control.lua", "timers"): "profilers: only logged",
    ("armory.lua", "catalog"): "built from prototypes",
    ("chronicle.lua", "listeners"): "subscribed by modules as they load",
    ("clearing.lua", "building_layers"): "built from prototypes",
    ("combat_odds.lua", "cache"): "built from prototypes",
    ("clearing.lua", "kinds"): "built from prototypes",
    ("obstacles.lua", "fills"): "built from prototypes",
    ("obstacles.lua", "naturals"): "built from prototypes",
    ("obstacles.lua", "resources"): "built from prototypes",
    ("orbit.lua", "science"): "built from prototypes",
    ("corrector.lua", "smeltable"): "built from prototypes",
    ("drive.lua", "kinds"): "built from prototypes",
    ("drive.lua", "fuel_lists"): "built from prototypes",
    ("drive_lab.lua", "ACCEL"): "built from defines",
    ("drive_lab.lua", "TURN"): "built from defines",
    ("movement_policy.lua", "OCTANTS"): "built from defines",
    ("personality.lua", "ORDER"): "filled as the file loads",
    ("progression.lua", "proto"): "built from prototypes",
    ("personality.lua", "TRAITS"): "filled as the file loads",
    ("personality.lua", "CONFLICTS"): "filled as the file loads",
    ("rail_trade_projects.lua", "SocialJobs"): "a module required late",
    ("recipes.lua", "cache"): "built from prototypes",
    ("scouting.lua", "fluid_cache"): "built from prototypes",
    ("team_research.lua", "copying"): "a re-entrancy flag, reset within the same call",
    ("worker.lua", "SOURCES"): "built from prototypes",
    ("proving.lua", "STATUS_NAMES"): "built from defines",
}
# Metatables are not saved: a table that gets one must never reach storage.
METATABLES = {"designed_start.lua": "a lookup table local to one function"}
# Files written on every machine (no for_player): only where the code never runs in multiplayer.
WRITES_EVERYWHERE = {"camera.lua": "single player only (Camera.tick returns in multiplayer)"}
# Event handlers, commands and remote interfaces are registered as the files load, so a joiner has the same ones.
REGISTRATIONS = {("script", "on_event"), ("script", "on_nth_tick"), ("script", "on_init"), ("script", "on_load"),
                 ("script", "on_configuration_changed"), ("commands", "add_command"), ("remote", "add_interface")}

KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if", "in",
            "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}
TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>--\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|--[^\n]*)
  | (?P<lstr>\[(?P<seq>=*)\[.*?\](?P=seq)\])
  | (?P<str>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
  | (?P<num>0[xX][0-9a-fA-F.]+(?:[pP][+-]?\d+)?|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_]\w*)
  | (?P<op>\.\.\.|\.\.|==|~=|<=|>=|::|[-+*/%^#<>=(){}\[\];:,.])
""", re.S | re.X)


def tokens(text):
    """(kind, value, line) for each token: name, kw, str, num or op (comments and spaces dropped)."""
    pos, line, out = 0, 1, []
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise SyntaxError(f"line {line}: cannot read {text[pos:pos + 20]!r}")
        value = m.group(0)
        if m.group("ws") is None and m.group("comment") is None:
            if m.group("name"):
                kind = "kw" if value in KEYWORDS else "name"
            elif m.group("str") or m.group("lstr"):
                kind = "str"
            else:
                kind = "num" if m.group("num") else "op"
            out.append((kind, value, line))
        line += value.count("\n")
        pos = m.end()
    return out


def writes(text):
    """What the scopes make suspect: ("global", line, name) for a name never declared local; ("state", line, name)
    for a file-level local assigned, or its fields set, from inside a function; ("register", line, "call in
    function") for a handler, command or interface registered from inside a function."""
    toks = tokens(text)
    scopes = [(set(), False)]       # (names declared, is a function body)
    loops = 0                       # `for` and `while` open their own scope: their `do` opens none
    brackets, saved = [], []        # open ( [ { in this function; the outer function's while in an inner one
    functions = []                  # names of the functions we are in ("" for an anonymous one)
    found = []

    def declared_in_function(name):
        return any(name in names for names, _ in scopes[1:])

    def in_function():
        return any(is_function for _, is_function in scopes)

    def target(i):
        """Index past `name(.field|[key])*`, and whether a field was named."""
        j, field = i + 1, False
        while j < len(toks):
            if toks[j][1] == "." and j + 1 < len(toks) and toks[j + 1][0] == "name":
                j, field = j + 2, True
            elif toks[j][1] == "[":
                depth = 0
                while True:
                    depth += {"[": 1, "]": -1}.get(toks[j][1], 0)
                    if toks[j][1] == "]" and depth == 0:
                        break
                    j += 1
                j, field = j + 1, True
            else:
                break
        return j, field

    i = 0
    while i < len(toks):
        kind, value, line = toks[i]
        if kind == "kw":
            if value == "local":
                if toks[i + 1][1] == "function":
                    scopes[-1][0].add(toks[i + 2][1])
                else:
                    j = i + 1
                    while toks[j][0] == "name":
                        scopes[-1][0].add(toks[j][1])
                        if toks[j + 1][1] != ",":
                            break
                        j += 2
            elif value == "function":
                named = toks[i + 1][0] == "name" and toks[i + 2][1] == "(" and toks[i - 1][1] != "local"
                if named and not any(toks[i + 1][1] in names for names, _ in scopes):
                    found.append(("global", line, toks[i + 1][1]))
                j = i + 1
                while toks[j][1] != "(":
                    j += 1
                functions.append("".join(v for _, v, _ in toks[i + 1:j]))
                params = {"self"} if toks[j - 2][1] == ":" else set()
                while toks[j][1] != ")":
                    if toks[j][0] == "name":
                        params.add(toks[j][1])
                    j += 1
                scopes.append((params, True))
                saved.append(brackets)
                brackets = []
                i = j + 1
                continue
            elif value == "for":
                names = set()
                j = i + 1
                while toks[j][1] not in ("=", "in"):
                    if toks[j][0] == "name":
                        names.add(toks[j][1])
                    j += 1
                scopes.append((names, False))
                loops += 1
            elif value == "while":
                scopes.append((set(), False))
                loops += 1
            elif value == "do":
                if loops:
                    loops -= 1
                else:
                    scopes.append((set(), False))
            elif value in ("then", "repeat"):
                scopes.append((set(), False))
            elif value == "elseif":
                scopes.pop()
            elif value == "else":
                scopes.pop()
                scopes.append((set(), False))
            elif value in ("end", "until"):
                _, is_function = scopes.pop()
                if is_function:
                    brackets = saved.pop()
                    functions.pop()
        elif kind == "op":
            if value in "([{":
                brackets.append(value)
            elif value in ")]}" and brackets:
                brackets.pop()
        elif kind == "name" and functions and toks[i - 1][1] != "." and toks[i + 1][1] == "." \
                and (value, toks[i + 2][1]) in REGISTRATIONS:
            found.append(("register", line, f"{value}.{toks[i + 2][1]} in {functions[-1] or 'a function'}"))
        elif kind == "name" and toks[i - 1][1] not in (".", ":") and not brackets:
            j, field = target(i)
            is_write = j < len(toks) and toks[j][1] == "=" and toks[j + 1][1] != "function"
            insert = toks[i - 1][1] == "(" and toks[i - 2][1] in ("insert", "remove") and toks[i - 4][1] == "table"
            if is_write or insert:
                file_level = value in scopes[0][0] and not declared_in_function(value)
                if not file_level and not declared_in_function(value) and not field and toks[i - 1][1] != "local":
                    found.append(("global", line, value))
                elif file_level and in_function():
                    found.append(("state", line, value))
        elif kind == "name" and brackets and brackets[-1] == "(" and toks[i - 1][1] == "(" \
                and toks[i - 2][1] in ("insert", "remove") and toks[i - 3][1] == "." and toks[i - 4][1] == "table":
            if value in scopes[0][0] and not declared_in_function(value) and in_function():
                found.append(("state", line, value))
        i += 1
    return found


def calls(text, name):
    """The argument count of each call to `name(` (e.g. helpers.write_file), with its line."""
    toks = tokens(text)
    parts = name.split(".")
    out = []
    for i in range(len(toks) - len(parts) * 2):
        if [toks[i + 2 * k][1] for k in range(len(parts))] != parts:
            continue
        if any(toks[i + 2 * k + 1][1] != "." for k in range(len(parts) - 1)) or toks[i + 2 * len(parts) - 1][1] != "(":
            continue
        j, depth, args = i + 2 * len(parts), 1, 1
        while depth:
            v = toks[j][1]
            depth += 1 if v in "([{" else -1 if v in ")]}" else 0
            if v == "," and depth == 1:
                args += 1
            j += 1
        out.append((toks[i][2], args))
    return out


class LintSelfCheck(unittest.TestCase):
    def test_it_finds_what_it_looks_for(self):
        found = writes("local M={}\nlocal cache={}\nlocal n=0\n"
                       "function M.f(a) cache[a]=1; n=n+1; M.last=a; y=2; local cache={} cache[1]=2 end\n"
                       "function M.g() table.insert(cache, 3) end\nfunction loose() end\n"
                       "function M.h() script.on_nth_tick(60, function() end) end\nreturn M\n")
        self.assertEqual(sorted(found), sorted([("state", 4, "cache"), ("state", 4, "n"), ("state", 4, "M"),
                                                ("global", 4, "y"), ("state", 5, "cache"), ("global", 6, "loose"),
                                                ("register", 7, "script.on_nth_tick in M.h")]))
        self.assertEqual(calls("helpers.write_file('a', f(x, y), false)\nhelpers.write_file('b','',true,0)",
                               "helpers.write_file"), [(1, 3), (2, 4)])


class DesyncLint(unittest.TestCase):
    def test_no_lua_globals_written(self):
        bad = [f"{path.name}:{line} {name}" for path in FILES
               for what, line, name in writes(path.read_text(encoding="utf-8")) if what == "global"]
        self.assertEqual(bad, [], "a global is not saved: keep it in storage or make it local")

    def test_module_state_written_at_runtime_is_the_same_everywhere(self):
        bad = sorted({f"{path.name}:{line} {name}" for path in FILES
                      for what, line, name in writes(path.read_text(encoding="utf-8"))
                      if what == "state" and (path.name, name) not in MODULE_STATE})
        self.assertEqual(bad, [], "state outside storage starts empty on a joiner's machine: keep it in storage, "
                                  "or list it in MODULE_STATE with why it is the same on every machine")

    def test_no_weak_tables(self):
        bad = [path.name for path in FILES if "__mode" in path.read_text(encoding="utf-8")]
        self.assertEqual(bad, [], "a weak table's contents depend on each machine's garbage collector")

    def test_metatables_only_where_listed(self):
        bad = [path.name for path in FILES
               if "setmetatable" in path.read_text(encoding="utf-8") and path.name not in METATABLES]
        self.assertEqual(bad, [], "metatables are lost on load: never on a table that reaches storage")

    def test_files_written_on_the_host_only(self):
        bad = [f"{path.name}:{line}" for path in FILES if path.name not in WRITES_EVERYWHERE
               for line, args in calls(path.read_text(encoding="utf-8"), "helpers.write_file") if args < 4]
        self.assertEqual(bad, [], "helpers.write_file needs for_player (0: the host only), or every player writes it")

    def test_registrations_happen_as_files_load(self):
        """In the main chunk, or (commands) in a Module.register_commands that control.lua's main chunk calls."""
        control = (ROOT / "mod" / "control.lua").read_text(encoding="utf-8")
        bad = []
        for path in FILES:
            for what, line, call in writes(path.read_text(encoding="utf-8")):
                if what != "register":
                    continue
                m = re.fullmatch(r"commands\.add_command in (\w+)\.register_commands", call)
                if not m:
                    bad.append(f"{path.name}:{line} {call}")
                elif not re.search(rf"(?m)^{m.group(1)}\.register_commands\(\)", control):
                    bad.append(f"{path.name}:{line} {call} (not called as control.lua loads)")
        self.assertEqual(bad, [], "handlers, commands and interfaces are registered the same on every machine")

    def test_no_os_or_io(self):
        bad = [path.name for path in FILES if re.search(r"\b(os|io)\.\w+\(", path.read_text(encoding="utf-8"))]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
