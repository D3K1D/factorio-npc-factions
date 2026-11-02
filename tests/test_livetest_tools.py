"""The live-test tools: the packaged mod, the RCON endpoint file, the attach runner's reconnecting and
stopping (no game needed)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import attach_runner as ar  # noqa: E402
import package_mod  # noqa: E402

VERSION = json.loads((ROOT / "mod" / "info.json").read_text(encoding="utf-8"))["version"]
TOP = f"npc-factions_{VERSION}"


class PackageTests(unittest.TestCase):
    def test_one_versioned_top_folder_with_the_mod_and_the_same_bytes_every_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = package_mod.build(Path(tmp) / "a")
            second = package_mod.build(Path(tmp) / "b")
            self.assertEqual(first.name, f"{TOP}.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as z:
                names = z.namelist()
                info = json.loads(z.read(f"{TOP}/info.json"))
        self.assertTrue(all(n.startswith(f"{TOP}/") for n in names))
        self.assertEqual((info["name"], info["version"]), ("npc-factions", VERSION))
        for needed in ("control.lua", "data.lua", "settings.lua", "scripts/team_world.lua", "locale/en/npc-factions.cfg"):
            self.assertIn(f"{TOP}/{needed}", names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.endswith((".pyc", ".log", ".zip"))])


class EndpointTests(unittest.TestCase):
    def test_setup_writes_the_endpoint_and_the_ini_lines_and_never_says_the_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rcon.json"
            said: list[str] = []
            ar.setup(path, 27015, say=said.append)
            endpoint = ar.load_endpoint(path)
            lines = (Path(tmp) / "config-ini-lines.txt").read_text(encoding="utf-8")
            self.assertIn("local-rcon-socket=127.0.0.1:27015", lines)
            self.assertIn(f"local-rcon-password={endpoint['password']}", lines)
            self.assertFalse(any(endpoint["password"] in s for s in said))
            self.assertGreaterEqual(len(endpoint["password"]), 24)
            ar.setup(path, 27015, say=said.append)                       # again: the same password
            self.assertEqual(ar.load_endpoint(path)["password"], endpoint["password"])
            ar.setup(path, 27015, force=True, say=said.append)           # unless asked for a new one
            self.assertNotEqual(ar.load_endpoint(path)["password"], endpoint["password"])
            path.write_text(json.dumps({"host": "10.0.0.5", "port": 27015, "password": "x"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                ar.load_endpoint(path)


class FakeClient:
    def __init__(self, replies):
        self.replies, self.closed = replies, False

    def text(self, command):
        reply = self.replies(command)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


class LinkTests(unittest.TestCase):
    def test_it_waits_for_the_game_and_reconnects_after_a_drop(self):
        attempts = []

        def connect(endpoint, timeout):
            attempts.append(timeout)
            if len(attempts) == 1:
                raise ConnectionRefusedError("not hosting yet")
            drops = {"left": 1}

            def replies(command):
                if drops["left"] and len(attempts) == 2:
                    drops["left"] -= 1
                    return ConnectionResetError("closed")
                return "ok:" + command
            return FakeClient(replies)

        link = ar.Link({"host": "127.0.0.1", "port": 1, "password": "p"}, timeout=45, connect=connect)
        with self.assertRaises(ar.Disconnected):
            link.ensure()                         # the game is not up
        link.ensure()
        with self.assertRaises(ar.Disconnected):
            link.text("/b")                       # connected, then the line drops
        self.assertFalse(link.connected)
        with self.assertRaises(ar.Disconnected):
            link.text("/c")                       # (only ensure() connects: the runner's own thread)
        link.ensure()
        self.assertEqual(link.text("/c"), "ok:/c")   # a fresh line
        self.assertEqual((len(attempts), attempts[0], link.connects), (3, 45, 2))


class SpawnTests(unittest.TestCase):
    def test_spawn_now_waits_for_the_game_then_brings_the_engineers_in(self):
        tries = {"n": 0}

        def connect(endpoint, timeout):
            tries["n"] += 1
            if tries["n"] < 3:
                raise ConnectionRefusedError()
            return FakeClient(lambda c: 'NPC_TEAM_RESULT {"ok": true, "spawned": 7}')
        link = ar.Link({"host": "127.0.0.1", "port": 1, "password": "p"}, connect=connect)
        said, slept = [], []
        result = ar.spawn_now(link, said.append, sleep=slept.append)
        self.assertEqual(result["spawned"], 7)
        self.assertEqual(slept, [1, 2])
        self.assertEqual(said, ["spawn: 7 engineers"])


class FakeBridge:
    def __init__(self, rcon, planner, log=print):
        self.rcon, self.handled, self.polls = rcon, {}, 0

    def poll_once(self):
        self.polls += 1
        self.rcon.text("/npc-plan observe")
        self.handled[f"1:{self.polls}"] = "accepted"


class RunnerTests(unittest.TestCase):
    def runner(self, tmp, replies, **kw):
        link = ar.Link({"host": "127.0.0.1", "port": 1, "password": "p"},
                       connect=lambda e, t: FakeClient(replies))
        self.slept = []
        clock = {"t": 0.0}

        def sleep(s):
            self.slept.append(s)
            clock["t"] += s
        return ar.Runner(link, Path(tmp), planner=None, strategist="off", log=lambda line: None,
                         bridge_factory=FakeBridge, agent_factory=lambda *a, **k: None, sleep=sleep,
                         clock=lambda: clock["t"], status_every=2, **kw)

    def test_it_stops_on_stop_now_and_leaves_its_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"n": 0}

            def replies(command):
                calls["n"] += 1
                if calls["n"] == 6:
                    (Path(tmp) / "stop-now").write_text("")
                if command == "/npc-agent status":
                    return 'NPC_AGENT_RESULT {"tick": 600, "npcs": [{"name": "Ada", "phase": "designed"}]}'
                return "NPC_PLAN_RESULT {}"
            runner = self.runner(tmp, replies)
            runner.run()
            status = json.loads((Path(tmp) / "status.json").read_text(encoding="utf-8"))
        self.assertTrue(status["connected"])
        self.assertEqual(status["tick"], 600)
        self.assertEqual(status["roster"][0]["name"], "Ada")
        self.assertGreaterEqual(status["plans"]["accepted"], 2)
        self.assertEqual(status["llm"]["calls"], 0)

    def test_while_the_game_is_away_it_backs_off_and_a_reload_starts_the_answers_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"n": 0, "tick": 5000}

            def replies(command):
                state["n"] += 1
                if state["n"] <= 3:
                    return ConnectionResetError("game closed")
                if command == "/npc-agent status":
                    state["tick"] -= 3000    # (a save from earlier was loaded)
                    return 'NPC_AGENT_RESULT {"tick": %d, "npcs": []}' % state["tick"]
                return "NPC_PLAN_RESULT {}"
            runner = self.runner(tmp, replies)
            runner.run(until=40)
        self.assertEqual(self.slept[:3], [1, 2, 4])          # backing off while the game is away
        self.assertLess(len(runner.bridge.handled), runner.bridge.polls)   # answered ids forgotten on the reload


if __name__ == "__main__":
    unittest.main()
