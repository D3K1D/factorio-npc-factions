"""Offline tests for the OpenAI Decisions API provider (no network calls)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import decisions_api
from companion.decisions_api import DecisionsAPIError, DecisionsProvider, build_body


def request() -> dict:
    return {
        "request_id": "1:7", "npc_id": 1, "observation_tick": 600, "state_revision": 3,
        "expires_tick": 1800, "question_id": "economic_task_v1",
        "context": "Ada has a burner drill and a furnace but no fuel.",
        "profile": {"cooperation": 0.4, "risk_tolerance": 0.7},
        "features": {"feature_version": 2, "coal": 0, "iron_plate": 8},
        "candidates": [
            {"id": "gather_coal", "description": "Walk to coal and mine 30 coal."},
            {"id": "idle", "description": "Wait."},
        ],
    }


def answer(choice="gather_coal", probabilities=None, **extra):
    probabilities = probabilities or [{"value": "gather_coal", "probability": 0.9},
                                      {"value": "idle", "probability": 0.1}]
    return {"answers": [{"type": "choice", "name": "next_action", "choice": choice,
                         "probabilities": probabilities, "confidence": 0.8, **extra}],
            "usage": {"input_tokens": 120}}


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, body, key, timeout):
        self.calls.append((url, body, key, timeout))
        return self.response


class DecisionsProviderTests(unittest.TestCase):
    def test_body_offers_exactly_the_candidates_as_one_choice_question(self):
        body = build_body(request())
        self.assertEqual(body["model"], "gpt-6-luna")
        (question,) = body["questions"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual([c["value"] for c in question["choices"]], ["gather_coal", "idle"])
        self.assertIn("Ada has a burner drill", body["input"])
        self.assertIn("cooperation=0.40", body["input"])

    def test_decide_returns_a_submission_the_bridge_accepts(self):
        transport = FakeTransport(answer())
        provider = DecisionsProvider(api_key="test-key", transport=transport)
        req = request()
        result = provider.decide(req)
        self.assertEqual(result["selected_id"], "gather_coal")
        self.assertEqual(provider.last_usage, {"input_tokens": 120})
        self.assertEqual(transport.calls[0][0], decisions_api.ENDPOINT)
        self.assertAlmostEqual(sum(result["probabilities"].values()), 1.0)

    def test_unlisted_options_get_zero_probability(self):
        provider = DecisionsProvider(api_key="k", transport=FakeTransport(
            answer(probabilities=[{"value": "gather_coal", "probability": 1.0},
                                  {"value": "invented", "probability": 0.5}])))
        result = provider.decide(request())
        self.assertEqual(result["probabilities"], {"gather_coal": 1.0, "idle": 0.0})

    def test_rejects_choice_that_was_not_offered(self):
        provider = DecisionsProvider(api_key="k", transport=FakeTransport(answer(choice="build_rocket")))
        with self.assertRaises(DecisionsAPIError):
            provider.decide(request())

    def test_rejects_refusal(self):
        response = {"answers": [{"type": "refusal", "name": "next_action"}]}
        provider = DecisionsProvider(api_key="k", transport=FakeTransport(response))
        with self.assertRaises(DecisionsAPIError):
            provider.decide(request())

    def test_key_is_required_and_never_shown(self):
        with mock.patch.object(decisions_api, "api_key_from_environment", return_value=None):
            with self.assertRaises(ValueError):
                DecisionsProvider()
        provider = DecisionsProvider(api_key="secret-value", transport=FakeTransport(answer()))
        self.assertNotIn("secret-value", repr(provider))


if __name__ == "__main__":
    unittest.main()
