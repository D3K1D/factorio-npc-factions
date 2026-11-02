"""Actual-shaped Responses tool calls with fake transport; no API or exports."""

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from designer_llm._reference_contract import canonical_bytes
from designer_llm.context import validate_context
from designer_llm.editor import DraftGridController, replay_tool_trace
from designer_llm.private_config import PrivateConfigError, ResponsesConfig
from designer_llm.proposal_schema import proposal_schema, to_wire
from designer_llm.providers import TransportResponse
from designer_llm.request import make_request
from designer_llm.tool_provider import ToolEditingProvider, prepare_tool_request


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FAKE_CREDENTIAL = "fixture-credential-not-a-real-provider-key"


def tool_call(name, arguments, call_id, *, raw_arguments=None):
    return {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
            "name": name, "arguments": json.dumps(arguments) if raw_arguments is None else raw_arguments,
            "status": "completed"}


def response(items, *, output_tokens=200, **updates):
    body = {"id": "resp_fixture", "model": "explicit-fixture-model", "status": "completed",
            "output": items, "usage": {"input_tokens": 300, "output_tokens": output_tokens,
                                         "total_tokens": 300 + output_tokens}}
    body.update(updates)
    return TransportResponse(200, json.dumps(body))


class FakeTransport:
    mock = True

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class ToolProviderTests(unittest.TestCase):
    def setUp(self):
        self.context = validate_context(json.loads((FIXTURES / "context.json").read_text(encoding="utf-8")))
        self.request = make_request(self.context, "Edit a synthetic two-entity iron pair.",
                                    max_entities=4, max_stages=2)
        self.envelope = {"request": self.request, "context": self.context, "memory": {}}
        self.wire = to_wire(json.loads((FIXTURES / "proposal.json").read_text(encoding="utf-8")))
        self.wire["entities"] = self.wire["entities"][:2]
        self.wire["stages"] = self.wire["stages"][:1]
        self.changes = []

    def provider(self, answers, *, config=None, **kwargs):
        transport = FakeTransport(answers)
        provider = ToolEditingProvider(config or ResponsesConfig("explicit-fixture-model", max_output_tokens=8192),
                                       transport, lambda: FAKE_CREDENTIAL,
                                       on_change=lambda state: self.changes.append(copy.deepcopy(state)), **kwargs)
        return provider, transport

    def place(self, index, call_id, **updates):
        entity = self.wire["entities"][index]
        arguments = {"id": entity["id"], "prototype": entity["prototype"],
                     "x": entity["position"]["x"], "y": entity["position"]["y"],
                     "direction": entity["direction"], "recipe": entity["recipe"]}
        arguments.update(updates)
        return tool_call("place_entity", arguments, call_id)

    def submit(self, call_id="submit", *, stages=None):
        return tool_call("submit_plan", {"plan_id": self.wire["plan_id"],
                                         "stages": self.wire["stages"] if stages is None else stages}, call_id)

    def run_provider(self, provider):
        return provider.propose(copy.deepcopy(self.envelope), proposal_schema())

    def test_preflight_is_credential_free_strict_tools_without_dispatch(self):
        payload = prepare_tool_request(ResponsesConfig("explicit-fixture-model"), self.envelope)
        self.assertNotIn("text", payload)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["include"], ["reasoning.encrypted_content"])
        self.assertIn("manual_supplies", payload["input"][0]["content"])
        self.assertIn("crafting inputs and recipe categories", payload["input"][0]["content"])
        self.assertNotIn(FAKE_CREDENTIAL, json.dumps(payload))
        self.assertNotIn("normal quality and cardinal directions", payload["input"][0]["content"])
        self.assertIn("qualities/configurations declared by the supplied capability catalog", payload["input"][0]["content"])
        for tool in payload["tools"]:
            self.assertTrue(tool["strict"])
            self.assertIs(tool["parameters"]["additionalProperties"], False)
            self.assertEqual(set(tool["parameters"]["properties"]), set(tool["parameters"]["required"]))
        with self.assertRaises(PrivateConfigError):
            prepare_tool_request(ResponsesConfig("explicit-fixture-model"), self.envelope, output_tokens=8193)

    def test_actual_shaped_place_pair_then_submit_preserves_geometry_and_reasoning(self):
        reasoning = {"type": "reasoning", "id": "rs_fixture", "summary": [],
                     "encrypted_content": "fixture-encrypted-continuity"}
        first_calls = [self.place(0, "place_drill"), self.place(1, "place_furnace")]
        provider, transport = self.provider([response([reasoning, *first_calls]),
                                             response([self.submit()], output_tokens=400)])
        result = self.run_provider(provider)
        self.assertEqual((result.status, result.reason), ("proposal", "tool_plan_submitted"))
        self.assertTrue(result.mock)
        self.assertEqual(result.proposal, self.wire)
        self.assertIsNone(result.raw_proposal_text)
        self.assertEqual(result.metadata["attempt_count"], 2)
        self.assertEqual(result.metadata["output_tokens"], 600)
        self.assertEqual([c["payload"]["max_output_tokens"] for c in transport.calls], [8192, 7992])
        second_input = transport.calls[1]["payload"]["input"]
        self.assertIn(reasoning, second_input)
        self.assertIn(first_calls[0], second_input)
        feedback = [json.loads(item["output"]) for item in second_input if item.get("type") == "function_call_output"]
        self.assertEqual(feedback[-1]["state"]["entities"], self.wire["entities"])
        self.assertEqual(replay_tool_trace(self.request, self.context, provider.trace), self.wire)
        self.assertEqual(result.metadata["tool_trace_sha256"], hashlib.sha256(canonical_bytes(provider.trace)).hexdigest())
        self.assertNotIn("fixture-encrypted-continuity", json.dumps(provider.trace))
        self.assertFalse(self.changes[-1]["native_flow_qualified"])
        self.assertEqual(self.changes[-1]["construction_status"], "proposed_unbuilt")

    def test_model_receives_collision_error_and_can_place_corrected_entity(self):
        provider, transport = self.provider([
            response([self.place(0, "drill"), self.place(1, "bad_furnace", x=10)]),
            response([self.place(1, "corrected_furnace"), self.submit()]),
        ])
        result = self.run_provider(provider)
        self.assertEqual(result.proposal, self.wire)
        failed = provider.trace["calls"][1]["result"]
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"], "declared_collision")
        self.assertEqual(len(failed["state"]["entities"]), 1)
        self.assertIn("declared_collision", json.dumps(transport.calls[1]["payload"]["input"]))
        self.assertEqual(replay_tool_trace(self.request, self.context, provider.trace), self.wire)

    def test_rejected_submit_returns_flow_feedback_and_can_be_corrected(self):
        bad_stages = copy.deepcopy(self.wire["stages"])
        bad_stages[0]["witnesses"] = bad_stages[0]["witnesses"][:2]
        provider, transport = self.provider([
            response([self.place(0, "drill"), self.place(1, "furnace"), self.submit("bad_submit", stages=bad_stages)]),
            response([self.submit("corrected_submit")]),
        ])
        result = self.run_provider(provider)
        self.assertEqual(result.status, "proposal")
        self.assertEqual(provider.trace["calls"][2]["result"]["error"], "stage_needs_flow_witness")
        self.assertIn("stage_needs_flow_witness", json.dumps(transport.calls[1]["payload"]["input"]))
        self.assertEqual(replay_tool_trace(self.request, self.context, provider.trace), self.wire)

    def test_bad_batch_duplicate_ids_prevents_all_edits(self):
        provider, transport = self.provider([response([self.place(0, "same"), self.place(1, "same")])])
        result = self.run_provider(provider)
        self.assertEqual(result.reason, "invalid_tool_call")
        self.assertEqual(provider.trace["calls"], [])
        self.assertEqual(self.changes, [])
        self.assertEqual(len(transport.calls), 1)

    def test_duplicate_call_id_in_later_response_is_rejected(self):
        call = tool_call("read_grid", {}, "same")
        provider, transport = self.provider([response([call]), response([call])])
        result = self.run_provider(provider)
        self.assertEqual(result.reason, "invalid_tool_call")
        self.assertEqual(len(provider.trace["calls"]), 1)
        self.assertEqual(len(transport.calls), 2)

    def test_submit_must_be_last_and_single_in_whole_batch(self):
        for suffix in ([tool_call("read_grid", {}, "read_after")], [self.submit("second_submit")]):
            self.changes.clear()
            provider, _ = self.provider([response([self.place(0, "place"), self.submit("first_submit"), *suffix])])
            result = self.run_provider(provider)
            self.assertEqual(result.reason, "invalid_tool_call")
            self.assertEqual(provider.trace["calls"], [])
            self.assertEqual(self.changes, [])

    def test_unknown_tool_malformed_json_and_quantity_fail_before_edits(self):
        for call in (tool_call("invented_tool", {}, "unknown"),
                     tool_call("read_grid", {}, "duplicate_json", raw_arguments='{"id":"a","id":"b"}'),
                     tool_call("read_grid", {}, "bad_json", raw_arguments='{broken'),
                     self.place(0, "bad_direction", direction=1),
                     tool_call("read_grid", {}, "nonfinite", raw_arguments='{"x":NaN}')):
            with self.subTest(call_id=call["call_id"]):
                provider, _ = self.provider([response([self.place(0, "valid_first"), call])])
                self.assertEqual(self.run_provider(provider).reason, "invalid_tool_call")
                self.assertEqual(provider.trace["calls"], [])
                self.assertEqual(self.changes, [])

    def test_escaped_exact_credential_in_inner_arguments_never_enters_trace(self):
        call = self.place(0, "sensitive", id=FAKE_CREDENTIAL)
        call["arguments"] = call["arguments"].replace("fixture", r"fixt\u0075re")
        answer = response([call])
        self.assertNotIn(FAKE_CREDENTIAL, answer.body)
        provider, transport = self.provider([answer])
        result = self.run_provider(provider)
        self.assertEqual(result.reason, "sensitive_response")
        self.assertEqual(provider.trace["calls"], [])
        self.assertNotIn(FAKE_CREDENTIAL, json.dumps(provider.trace))
        self.assertNotIn(FAKE_CREDENTIAL, repr(result))
        self.assertEqual(len(transport.calls), 1)

    def test_exact_credential_in_tool_feedback_stops_before_trace(self):
        provider, _ = self.provider([response([tool_call("read_grid", {}, "read")])])
        with patch.object(DraftGridController, "call", return_value={"ok": True, "text": FAKE_CREDENTIAL}):
            result = self.run_provider(provider)
        self.assertEqual(result.reason, "sensitive_response")
        self.assertEqual(provider.trace["calls"], [])
        self.assertNotIn(FAKE_CREDENTIAL, repr(result))

    def test_transport_failure_does_not_retry_or_continue(self):
        provider, transport = self.provider([RuntimeError(FAKE_CREDENTIAL), response([self.submit()])])
        result = self.run_provider(provider)
        self.assertEqual(result.reason, "transport_error")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(FAKE_CREDENTIAL, repr(result))

    def test_http_errors_and_oversized_or_malformed_response_stop(self):
        cases = [(TransportResponse(401, FAKE_CREDENTIAL), "credentials_rejected"),
                 (TransportResponse(429, "fixture"), "provider_rate_limited"),
                 (TransportResponse(200, b"\xff"), "invalid_provider_json"),
                 (TransportResponse(200, "x" * 524289), "response_too_large"),
                 (TransportResponse(200, '{"status":"completed","status":"incomplete"}'), "invalid_provider_json")]
        for answer, reason in cases:
            with self.subTest(reason=reason):
                provider, transport = self.provider([answer])
                result = self.run_provider(provider)
                self.assertEqual(result.reason, reason)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(provider.trace["calls"], [])

    def test_missing_malformed_or_excess_usage_cannot_refund_budget(self):
        for usage in (None, {"input_tokens": 1, "output_tokens": True, "total_tokens": 2},
                      {"input_tokens": 1, "output_tokens": 8193, "total_tokens": 8194}):
            with self.subTest(usage=usage):
                provider, transport = self.provider([response([self.place(0, "unused")], usage=usage),
                                                      response([self.submit()])])
                self.assertEqual(self.run_provider(provider).reason, "invalid_provider_usage")
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(provider.trace["calls"], [])

    def test_output_allowance_decreases_across_at_most_three_responses(self):
        answers = [response([tool_call("read_grid", {}, "read_" + str(i))], output_tokens=count)
                   for i, count in enumerate((1000, 5000, 1000))]
        provider, transport = self.provider(answers)
        result = self.run_provider(provider)
        self.assertEqual(result.reason, "tool_session_budget_exhausted")
        self.assertEqual([c["payload"]["max_output_tokens"] for c in transport.calls], [8192, 7192, 2192])
        self.assertEqual(result.metadata["output_tokens"], 7000)
        self.assertEqual(len(transport.calls), 3)

    def test_exhausted_output_and_incomplete_response_stop(self):
        for first in (response([tool_call("read_grid", {}, "read")], output_tokens=8192),
                      response([self.place(0, "unused")], status="incomplete")):
            provider, transport = self.provider([first, response([self.submit()])])
            self.assertEqual(self.run_provider(provider).reason, "output_budget_exhausted")
            self.assertEqual(len(transport.calls), 1)

    def test_tool_call_and_argument_size_limits_precede_edits(self):
        excessive = [tool_call("read_grid", {}, "read_" + str(i)) for i in range(13)]
        oversized = [tool_call("read_grid", {}, "oversized", raw_arguments=" " * 64_001)]
        for calls, reason in ((excessive, "tool_call_budget_exceeded"), (oversized, "invalid_tool_call")):
            provider, _ = self.provider([response(calls)])
            self.assertEqual(self.run_provider(provider).reason, reason)
            self.assertEqual(provider.trace["calls"], [])
            self.assertEqual(self.changes, [])

    def test_trace_size_limit_retains_only_bounded_safe_diagnostics(self):
        answers = [response([tool_call("read_grid", {}, "read_" + str(i))]) for i in range(3)]
        provider, transport = self.provider(answers)
        with patch.object(DraftGridController, "call", return_value={"ok": True, "text": "R" * 90_000}):
            result = self.run_provider(provider)
        self.assertEqual(result.reason, "tool_trace_too_large")
        self.assertLessEqual(len(canonical_bytes(provider.trace)), 200_000)
        self.assertEqual(len(transport.calls), 3)

    def test_overall_deadline_stops_after_sent_response_without_retry(self):
        current = [0]
        provider, transport = self.provider([response([self.place(0, "unused")])], clock=lambda: current[0])
        original_transport = provider._transport
        def delayed(**kwargs):
            answer = original_transport(**kwargs)
            current[0] = 91
            return answer
        provider._transport = delayed
        self.assertEqual(self.run_provider(provider).reason, "deadline_exceeded")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(provider.trace["calls"], [])

    def test_refusal_message_only_and_unexpected_output_never_submit(self):
        cases = [([{"type": "message", "content": [{"type": "refusal", "refusal": "fixture refusal"}]}],
                  "provider_refusal"),
                 ([{"type": "message", "content": [{"type": "output_text", "text": "Claimed built"}]}],
                  "missing_tool_submission"),
                 ([{"type": "computer_call", "action": {"type": "click"}}], "invalid_tool_call")]
        for output, reason in cases:
            provider, transport = self.provider([response(output)])
            result = self.run_provider(provider)
            self.assertEqual(result.reason, reason)
            self.assertIsNone(result.proposal)
            self.assertEqual(provider.trace["calls"], [])
            self.assertEqual(len(transport.calls), 1)

    def test_constructor_rejects_overspending_retries_and_excess_session_limits(self):
        for config in (ResponsesConfig("explicit-fixture-model", max_output_tokens=8193),
                       ResponsesConfig("explicit-fixture-model", max_output_tokens=255),
                       ResponsesConfig("explicit-fixture-model", max_retries=1)):
            with self.assertRaises(ValueError):
                self.provider([], config=config)
        for setting in ({"max_requests": 4}, {"max_tool_calls": 13}, {"total_deadline_s": 91}):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.provider([], **setting)


if __name__ == "__main__":
    unittest.main()
