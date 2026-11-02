"""Every engineer its own designer, all at work at once (companion/designer_bridge.py, planners=...)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import blueprints as bp  # noqa: E402
from companion.catalog_planner import Defer, MoveSite  # noqa: E402
from companion.designer_bridge import DesignerBridge, make_planners  # noqa: E402
from companion.layout_planner import PlanError  # noqa: E402

MARK = "NPC_PLAN_RESULT "


class Game:
    """The game's side of /npc-plan: requests waiting, and everything the bridge sent."""

    def __init__(self, requests=(), research=()):
        self.requests, self.research, self.sent = list(requests), list(research), []
        self.lock = threading.Lock()

    def text(self, command: str) -> str:
        with self.lock:
            verb = command.split()[1]
            if verb == "observe":
                answer = {"ok": True, "requests": list(self.requests), "status": [],
                          "research_requests": list(self.research), "taught": []}
            else:
                payload = json.loads(command.split(" ", 2)[2])
                self.sent.append((verb, payload))
                rid = payload.get("request_id")
                self.requests = [r for r in self.requests if r["request_id"] != rid]
                self.research = [r for r in self.research if r["request_id"] != rid]
                answer = {"ok": True, "accepted": True, "request_id": rid}
            return MARK + json.dumps(answer)

    def verbs(self, verb: str) -> list[str]:
        with self.lock:
            return [p.get("request_id") for v, p in self.sent if v == verb]


def request(npc: int, seq: int) -> dict:
    return {"request_id": f"{npc}:{seq}", "npc_id": npc, "purpose": "iron-smelting-start"}


class Planner:
    """An engineer's planner: waits on `gate` (a slow LLM design) and counts its calls."""

    def __init__(self, gate: threading.Event | None = None, error: BaseException | None = None):
        self.gate, self.error, self.calls = gate, error, []
        self.world = None

    def __call__(self, site):
        self.calls.append(site["request_id"])
        if self.gate:
            self.gate.wait(10)
        if self.error:
            raise self.error
        return {"plan_id": f"plan-{site['request_id']}", "entities": [], "stages": []}

    def choose_research(self, request):
        return "automation"


def until(condition, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


class ParallelDesignerTests(unittest.TestCase):
    def setUp(self):
        self.gate = threading.Event()
        self.planners = {1: Planner(self.gate), 2: Planner()}
        self.made = []

    def factory(self, npc):
        self.made.append(npc)
        return self.planners.setdefault(npc, Planner())

    def bridge(self, game):
        b = DesignerBridge(game, log=lambda _: None, planners=self.factory)
        self.addCleanup(self.gate.set)
        self.addCleanup(b.close)
        return b

    def test_a_slow_design_for_one_engineer_holds_up_nobody_else(self):
        game = Game([request(1, 1), request(2, 1)])
        bridge = self.bridge(game)
        started = time.monotonic()
        bridge.poll_once()
        self.assertLess(time.monotonic() - started, 1.0)              # the loop never waits for a design
        self.assertTrue(until(lambda: bridge.poll_once() is not None and "2:1" in game.verbs("submit")))
        self.assertNotIn("1:1", game.verbs("submit"))                 # still designing
        self.assertTrue(bridge.busy())
        self.gate.set()
        bridge.drain(5)
        self.assertEqual(sorted(game.verbs("submit")), ["1:1", "2:1"])
        self.assertEqual(sorted(self.made), [1, 2])                   # each its own planner
        self.assertIsNot(bridge.planner_for(1), bridge.planner_for(2))
        self.assertIs(self.planners[1].world.__self__, bridge)        # its map comes through the bridge

    def test_a_hung_design_is_given_up_and_its_engineer_goes_on(self):
        # (SO-5: a design that never came back held its engineer's later requests for good)
        game = Game([request(1, 1)])
        bridge = self.bridge(game)
        bridge.JOB_LIFETIME_S = 0.2
        logs = []
        bridge.log = logs.append
        bridge.poll_once()
        self.assertTrue(bridge.busy())
        game.requests.append(request(1, 2))
        self.assertTrue(until(lambda: bridge.poll_once() is not None and "1:1" in game.verbs("fail")))
        self.assertEqual([p.get("reason") for v, p in game.sent if v == "fail"], ["designer_expired"])
        self.assertTrue(any("did not come in time" in line for line in logs), logs)
        self.assertTrue(until(lambda: self.planners[1].calls == ["1:1", "1:2"]))   # its next request let through
        self.assertEqual(bridge.handled["1:1"], "expired")
        self.assertEqual(self.made.count(1), 2)                        # a fresh planner for the next request
        self.gate.set()                                                 # the late design: dropped, not sent
        bridge.drain(5)
        self.assertNotIn("1:1", game.verbs("submit"))
        self.assertIn("1:2", game.verbs("submit"))

    def test_half_the_workers_stuck_and_a_fresh_pool_takes_over(self):
        bridge = DesignerBridge(Game(), log=lambda _: None, planners=self.factory, workers=2)
        self.addCleanup(self.gate.set)
        self.addCleanup(bridge.close)
        first = bridge._pool
        bridge._stuck = 1
        bridge._expire()
        self.assertIsNot(bridge._pool, first)
        self.assertEqual(bridge._stuck, 0)

    def test_an_answer_by_sets_the_lifetime(self):
        bridge = self.bridge(Game())
        self.assertAlmostEqual(bridge._lifetime({"answer_by": 100.0}), 100.0 + bridge.JOB_MARGIN_S)
        self.assertGreater(bridge._lifetime({}), time.monotonic() + bridge.JOB_LIFETIME_S - 1)

    def test_each_engineer_works_on_one_request_at_a_time(self):
        game = Game([request(1, 1), request(1, 2)])
        bridge = self.bridge(game)
        bridge.poll_once()
        bridge.poll_once()
        self.assertEqual(self.planners[1].calls, ["1:1"])            # the second waits its turn
        self.gate.set()
        self.assertTrue(until(lambda: bridge.poll_once() is not None and "1:2" in game.verbs("submit")))
        self.assertEqual(self.planners[1].calls, ["1:1", "1:2"])

    def test_research_is_answered_while_its_engineer_designs(self):
        game = Game([request(1, 1)], research=[{"request_id": "1:r1", "npc_id": 1, "options": []}])
        bridge = self.bridge(game)
        bridge.poll_once()
        self.assertTrue(until(lambda: bridge.poll_once() is not None and "1:r1" in game.verbs("research")))
        self.assertNotIn("1:1", game.verbs("submit"))

    def test_what_a_designer_cannot_do_reaches_the_game_as_before(self):
        self.planners = {1: Planner(error=RuntimeError("boom")), 2: Planner(error=PlanError("no room")),
                         3: Planner(error=MoveSite("iron-smelting-start", 40, 0)),
                         4: Planner(error=Defer("continue_projects"))}
        game = Game([request(n, 1) for n in (1, 2, 3, 4)])
        bridge = self.bridge(game)
        bridge.poll_once()
        bridge.drain(5)
        self.assertEqual(sorted(game.verbs("fail")), ["1:1", "2:1"])
        self.assertEqual(game.verbs("site"), ["3:1"])
        self.assertEqual(game.verbs("defer"), ["4:1"])
        reason = next(p["reason"] for v, p in game.sent if v == "fail" and p["request_id"] == "1:1")
        self.assertEqual(reason, "RuntimeError: boom")
        self.assertEqual(bridge.handled["1:1"], "failed")

    def test_one_planner_for_everyone_still_answers_within_the_poll(self):
        game = Game([request(1, 1), request(2, 1)])
        shared = Planner()
        bridge = DesignerBridge(game, shared, log=lambda _: None)
        bridge.poll_once()
        self.assertEqual(sorted(game.verbs("submit")), ["1:1", "2:1"])
        self.assertFalse(bridge.parallel)
        self.assertIs(shared.world.__self__, bridge)


class SharedLibraryTests(unittest.TestCase):
    def test_every_engineer_has_its_own_planner_over_one_library(self):
        with tempfile.TemporaryDirectory() as folder:
            factory = make_planners("catalog", catalog=folder, designer=False, chooser=False, log=lambda _: None)
            ada, bob = factory(1), factory(2)
            self.assertIsNot(ada, bob)
            self.assertIs(ada.catalog, bob.catalog)
            self.assertIsNot(ada.tried_sites, bob.tried_sites)

    def test_the_library_takes_writes_from_several_designers_at_once(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            stats = {"uses": 0, "successes": 0, "failures": 0, "first_output_ticks": [], "complete_ticks": []}
            library.add({"id": "shared", "schema": bp.SCHEMA, "purpose": "x", "stats": dict(stats)})

            def work(n):
                for i in range(20):
                    library.add({"id": f"b{n}-{i}", "schema": bp.SCHEMA, "purpose": "x", "stats": dict(stats)})
                    library.record("shared", success=True)
            threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(library), 1 + 6 * 20)
            self.assertEqual(library.blueprints["shared"]["stats"]["uses"], 120)
            again = bp.Catalog(folder)                                      # and every file is whole
            self.assertEqual(again.blueprints["shared"]["stats"]["successes"], 120)


if __name__ == "__main__":
    unittest.main()
