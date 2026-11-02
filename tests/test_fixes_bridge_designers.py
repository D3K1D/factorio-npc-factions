"""A finished design is kept until the game has it, and a loaded save forgets earlier requests
(companion/designer_bridge.py, companion/strategist.py AgentBridge)."""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.designer_bridge import DesignerBridge  # noqa: E402
from companion.strategist import AgentBridge  # noqa: E402

MARK = "NPC_PLAN_RESULT "


class Game:
    """The game's side of /npc-plan, with a tick, a status row per engineer, and a line that can drop."""

    def __init__(self, requests=()):
        self.requests, self.sent, self.status = list(requests), [], []
        self.tick, self.drops = 1000, 0
        self.lock = threading.Lock()

    def text(self, command: str) -> str:
        with self.lock:
            verb = command.split()[1]
            if verb == "observe":
                answer = {"ok": True, "tick": self.tick, "requests": list(self.requests), "status": list(self.status),
                          "research_requests": [], "taught": []}
                return MARK + json.dumps(answer)
            if verb == "facts":   # (a read, like observe: what the machines do; nothing these tests need)
                return MARK + json.dumps({"ok": True, "entities": {}})
            if self.drops:
                self.drops -= 1
                raise ConnectionError("the line dropped")
            payload = json.loads(command.split(" ", 2)[2])
            self.sent.append((verb, payload))
            rid = payload.get("request_id")
            self.requests = [r for r in self.requests if r["request_id"] != rid]
            return MARK + json.dumps({"ok": True, "accepted": True, "request_id": rid})

    def plans(self) -> list[str]:
        with self.lock:
            return [p["plan"]["plan_id"] for v, p in self.sent if v == "submit"]


class Planner:
    """An engineer's planner: `gate` holds it (a slow design); its plans are named by call."""

    def __init__(self, gate: threading.Event | None = None):
        self.gate, self.calls = gate, 0
        self.world = None

    def __call__(self, site):
        self.calls += 1
        n = self.calls
        if self.gate and n == 1:
            self.gate.wait(10)
        return {"plan_id": f"call-{n}", "entities": [], "stages": []}


def until(condition, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def drop_while_sending(b) -> bool:
    """Poll until the design is done and the line drops while it is being sent."""
    for _ in range(300):
        try:
            b.poll_once()
        except ConnectionError:
            return True
        time.sleep(0.01)
    return False


def bridge(game, planner):
    return DesignerBridge(game, log=lambda m: None, planners=lambda npc: planner)


class KeptUntilSentTests(unittest.TestCase):
    def test_a_design_the_line_dropped_on_is_sent_again(self):
        game, planner = Game([{"request_id": "1:1", "npc_id": 1}]), Planner()
        b = bridge(game, planner)
        game.drops = 1
        self.assertTrue(drop_while_sending(b))                                    # the design is done, the line drops sending it
        self.assertEqual(b.handled["1:1"], "designing")
        b.poll_once()                                        # still asked for: sent again
        self.assertEqual(game.plans(), ["call-1"])
        self.assertEqual(b.handled["1:1"], "accepted")
        self.assertEqual(planner.calls, 1)                   # (not designed twice)
        b.close()

    def test_one_the_game_got_after_all_is_not_sent_twice(self):
        game, planner = Game([{"request_id": "1:1", "npc_id": 1}]), Planner()
        b = bridge(game, planner)
        game.drops = 1
        self.assertTrue(drop_while_sending(b))
        # the plan got there before the line dropped: the engineer is building it now
        game.requests, game.status = [], [{"npc_id": 1, "request_id": "1:1", "status": "building"}]
        b.poll_once()
        self.assertEqual(game.plans(), [])
        self.assertEqual(b.handled["1:1"], "sent")
        self.assertFalse(b._jobs)
        b.close()

    def test_a_late_design_is_still_sent_after_a_drop(self):
        game, planner = Game([{"request_id": "1:1", "npc_id": 1}]), Planner()
        b = bridge(game, planner)
        game.drops = 1
        self.assertTrue(drop_while_sending(b))
        # it stopped waiting meanwhile (the designer took too long): the game still takes it
        game.status = [{"npc_id": 1, "request_id": "1:1", "status": "deferred", "reason": "designer_timeout"}]
        game.requests = []
        b.poll_once()
        self.assertEqual(game.plans(), ["call-1"])
        b.close()


class MapTests(unittest.TestCase):
    def test_a_map_can_be_asked_for_round_a_place_far_from_home(self):
        game = Game()
        b = DesignerBridge(game, Planner(), log=lambda m: None)
        b.world({"npc_id": 5}, center={"x": 384.0, "y": 192.0}, radius=400)
        b.world({"npc_id": 5})
        maps = [p for v, p in game.sent if v == "map"]
        self.assertEqual(maps, [{"npc_id": 5, "radius": 400, "x": 384.0, "y": 192.0},
                                {"npc_id": 5, "radius": DesignerBridge.MAP_RADIUS}])


class ReloadTests(unittest.TestCase):
    def test_after_a_save_is_loaded_no_earlier_design_answers_a_new_request(self):
        gate = threading.Event()
        game, planner = Game([{"request_id": "1:1", "npc_id": 1}]), Planner(gate)
        b = bridge(game, planner)
        b.poll_once()                                        # its designer starts on 1:1
        game.tick = 400                                      # a save from earlier: request ids start over
        b.poll_once()
        self.assertEqual(b.generation, 1)
        self.assertNotIn("1:1", b.handled)
        self.assertEqual(planner.calls, 1)                   # its designer is still busy with the old one
        gate.set()
        self.assertTrue(until(lambda: b._orphans.get(1) is None or b._orphans[1].done()))
        b.poll_once()                                        # now the new 1:1 is designed
        self.assertTrue(until(lambda: planner.calls == 2 and not b.busy()))
        b.poll_once()
        self.assertEqual(game.plans(), ["call-2"])           # the old design was never sent
        b.close()

    def test_the_strategist_forgets_thoughts_from_before_a_load(self):
        gate, sent = threading.Event(), []
        state = {"tick": 1000, "requests": [{"request_id": "1:s1", "npc_id": 1, "reason": "events", "situation": {}}]}

        def call(command):
            if command.startswith("/npc-agent observe"):
                return {"ok": True, "tick": state["tick"], "requests": state["requests"], "results": []}
            sent.append(json.loads(command.split(" ", 2)[2]))
            return {"ok": True, "accepted": 1}

        class Brain:
            source = "rules"

            def decide(self, request):
                gate.wait(10)
                return [{"tool": "say", "args": {"text": "old"}}]
        agent = AgentBridge(call, Brain(), log=lambda m: None)
        agent.poll_once()
        self.assertIn("1:s1", agent.running)
        state["tick"], state["requests"] = 300, []
        agent.poll_once()
        self.assertEqual(agent.running, {})
        gate.set()
        time.sleep(0.05)
        agent.poll_once()
        self.assertEqual(sent, [])                           # the thought from before the load is thrown away


if __name__ == "__main__":
    unittest.main()
