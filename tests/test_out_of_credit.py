"""OpenAI's credit used up (the live game, 2026-10-22: from minute 12 every call failed and was tried again
every few seconds): the first "no credit" answer stops every AI caller, and the engineers go on by rule."""
from __future__ import annotations

import io
import json
import math
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import decisions_api  # noqa: E402
from companion.layout_planner import PlanError  # noqa: E402

EMPTY = {"error": {"message": "You have no credits remaining. Add credits to continue using the API at "
                              "https://platform.openai.com/settings/organization/billing/.",
                   "type": "insufficient_quota", "code": "insufficient_quota"}}


def http_error(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", code, "error", {},
                                  io.BytesIO(json.dumps(body).encode()))


class CreditTests(unittest.TestCase):
    def tearDown(self):
        decisions_api.resume_ai()

    def test_only_an_empty_balance_counts_not_a_passing_rate_limit(self):
        self.assertTrue(decisions_api.no_credit_reply(429, EMPTY["error"]))
        self.assertTrue(decisions_api.no_credit_reply(429, {"message": "You have no credits remaining."}))
        self.assertFalse(decisions_api.no_credit_reply(429, {"message": "Rate limit reached for requests",
                                                             "code": "rate_limit_exceeded"}))
        self.assertFalse(decisions_api.no_credit_reply(500, EMPTY["error"]))

    def test_the_first_no_credit_answer_stops_every_later_call(self):
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            raise http_error(429, EMPTY)
        with mock.patch("urllib.request.urlopen", urlopen):
            with self.assertRaises(decisions_api.OutOfCredit):
                decisions_api.http_transport("https://api.openai.com/v1/responses", {}, "key", 5)
            self.assertTrue(decisions_api.out_of_credit())
            with self.assertRaises(decisions_api.OutOfCredit):
                decisions_api.http_transport("https://api.openai.com/v1/decisions", {}, "key", 5)
        self.assertEqual(len(calls), 1)                      # the second was never sent

    def test_a_rate_limit_is_an_ordinary_failure(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(429, {"error": {"message": "slow down"}})):
            with self.assertRaises(decisions_api.DecisionsAPIError) as raised:
                decisions_api.http_transport("https://api.openai.com/v1/responses", {}, "key", 5)
        self.assertNotIsInstance(raised.exception, decisions_api.OutOfCredit)
        self.assertFalse(decisions_api.out_of_credit())

    def test_the_strategists_go_by_rule_and_say_so_once(self):
        from companion.strategist import AgentBridge
        said, asked = [], []

        class Spent:
            source = "llm"

            def decide(self, request):
                asked.append(request["request_id"])
                decisions_api.pause_ai(math.inf, "OpenAI says there is no credit left")
                raise decisions_api.OutOfCredit("HTTP 429: You have no credits remaining.")
        bridge = AgentBridge(lambda command: {}, Spent(), log=said.append)
        request = {"request_id": "1:s1", "npc_id": 1, "reason": "routine", "events": [], "situation": {"name": "Ada"}}
        first = bridge.think(request, [])
        second = bridge.think(dict(request, request_id="1:s2"), [])
        self.assertEqual((first[1], second[1]), ("rules", "rules"))
        self.assertEqual(asked, ["1:s1"])                     # OpenAI asked once, then never again
        self.assertEqual(len([line for line in said if "no credit left" in line]), 1)

    def test_the_designer_is_not_asked_either(self):
        from companion.designer_planner import LLMPlanner
        sent = []
        planner = LLMPlanner(transport=lambda **kw: sent.append(kw), credential=lambda: "unused", log=lambda _: None)
        decisions_api.pause_ai(math.inf, "OpenAI says there is no credit left")
        with self.assertRaises(PlanError):
            planner({"npc_id": 1, "request_id": "1:1", "tick": 600})
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()


class PauseTests(unittest.TestCase):
    def tearDown(self):
        decisions_api.resume_ai()

    def test_a_limit_pauses_for_a_while_and_the_ai_comes_back_by_itself(self):
        import time
        self.assertTrue(decisions_api.pause_ai(0.2, "Codex's usage limit is reached"))
        self.assertFalse(decisions_api.pause_ai(0.1, "again"))          # already paused: not news
        self.assertTrue(decisions_api.out_of_credit())
        self.assertEqual(decisions_api.pause_reason(), "Codex's usage limit is reached")
        time.sleep(0.3)
        self.assertFalse(decisions_api.out_of_credit())
