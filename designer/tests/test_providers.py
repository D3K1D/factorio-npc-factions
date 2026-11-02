"""All provider cases use deterministic replay or injected fake HTTP; no paid calls."""

import copy
import getpass
import json
import os
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from designer_llm.private_config import (
    DEFAULT_RESPONSES_ENDPOINT, EnvironmentCredentialSupplier, MaskedCredentialSupplier,
    PrivateConfigError, ResponsesConfig, assert_no_credentials, load_private_config,
)
from designer_llm.providers import (
    HTTPTransportError, OpenAIHTTPTransport, ReplayProvider, ResponsesProvider,
    RetryablePreflightError, TransportResponse, prepare_request,
)


SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}
REQUEST = {"request": {"goal": "supply iron"}, "context": {"known": False}, "memory": {}}
FAKE_CREDENTIAL = "fixture-credential-not-a-real-provider-key"


def response(text='{"summary":"fixture only"}', **updates):
    body = {"status": "completed", "id": "resp_fixture", "model": "explicit-fixture-model",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}}
    body.update(updates)
    return TransportResponse(200, json.dumps(body))


class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def __repr__(self):
        return "FakeTransport(mock fixture)"


def adapter(answer=None, **config):
    transport = FakeTransport([response() if answer is None else answer])
    provider = ResponsesProvider(ResponsesConfig(model="explicit-fixture-model", **config),
                                 transport, lambda: FAKE_CREDENTIAL)
    return provider, transport


class ConfigTests(unittest.TestCase):
    def test_model_is_explicit_and_unknown_options_fail(self):
        for values in ({}, {"model": ""}, {"model": "<fill-me>"},
                       {"model": "chosen", "reasoning_effort": "invented"}):
            with self.subTest(values=values), self.assertRaises(PrivateConfigError):
                ResponsesConfig.from_mapping(values)

    def test_endpoint_is_https_without_credentials_and_custom_requires_opt_in(self):
        for endpoint in ("http://api.openai.com/v1/responses", "https://user:pass@example.com/v1/responses",
                         "https://api.openai.com/v1/responses?key=bad", "https://api.openai.com/v1/responses#bad"):
            with self.subTest(endpoint=endpoint), self.assertRaises(PrivateConfigError):
                ResponsesConfig("chosen", endpoint=endpoint, allow_custom_endpoint=True)
        with self.assertRaises(PrivateConfigError):
            ResponsesConfig("chosen", endpoint="https://example.invalid/v1/responses")
        custom = ResponsesConfig("chosen", endpoint="https://example.invalid/v1/responses", allow_custom_endpoint=True)
        self.assertEqual(custom.endpoint, "https://example.invalid/v1/responses")

    def test_budget_bounds_reject_excess_and_nonfinite(self):
        for setting in ({"timeout_s": 301}, {"total_deadline_s": float("nan")}, {"max_retries": 3},
                        {"max_output_tokens": 32769}, {"max_response_bytes": 999}, {"max_retries": True}):
            with self.subTest(setting=setting), self.assertRaises(PrivateConfigError):
                ResponsesConfig("chosen", **setting)

    def test_private_config_cannot_hold_secret_or_duplicate_keys(self):
        sandbox_temp = Path(__file__).resolve().parents[1] / "work" / "test-temp"
        sandbox_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=sandbox_temp) as directory:
            path = Path(directory) / "private.json"
            for content in ('{"model":"chosen","api_key":"fixture"}',
                            '{"model":"chosen","model":"other"}', '["chosen"]'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(PrivateConfigError):
                    load_private_config(path)
            path.write_text('{"model":"chosen"}', encoding="utf-8")
            self.assertEqual(load_private_config(path).model, "chosen")

    def test_public_artifact_guard_handles_nested_secrets_and_safe_game_keys(self):
        for data in ({"nested": [{"Authorization": "fixture"}]},
                     {"message": "sk-fixture_only_not_a_real_key"},
                     {"message": "Bearer fixtureonlytoken"}):
            with self.assertRaises(PrivateConfigError) as rejected:
                assert_no_credentials(data)
            self.assertEqual(str(rejected.exception), "credentials_forbidden")
        assert_no_credentials({"entity_key": "iron", "key": "game lookup"})

    def test_credentials_are_lazy_explicit_local_and_masked(self):
        with patch.dict(os.environ, {"DESIGNER_TEST_KEY": FAKE_CREDENTIAL}, clear=True):
            supplier = EnvironmentCredentialSupplier("DESIGNER_TEST_KEY")
            self.assertEqual(supplier(), FAKE_CREDENTIAL)
            self.assertNotIn(FAKE_CREDENTIAL, repr(supplier))
            with self.assertRaises(PrivateConfigError):
                EnvironmentCredentialSupplier("MISSING_SELECTED_VARIABLE")()
        prompts = []
        supplier = MaskedCredentialSupplier(lambda prompt: prompts.append(prompt) or FAKE_CREDENTIAL)
        self.assertEqual(prompts, [])
        self.assertEqual(supplier(), FAKE_CREDENTIAL)
        self.assertIn("masked", prompts[0])
        self.assertNotIn(FAKE_CREDENTIAL, repr(supplier))

    def test_masked_prompt_fails_closed_if_getpass_would_echo(self):
        def echoed_fallback(prompt):
            warnings.warn("Password input may be echoed", getpass.GetPassWarning)
            self.fail("The credential must not be read after a getpass warning")
        supplier = MaskedCredentialSupplier(echoed_fallback)
        with self.assertRaises(PrivateConfigError) as failure:
            supplier()
        self.assertEqual(str(failure.exception), "masked_input_unavailable")


class ReplayTests(unittest.TestCase):
    def test_fixture_is_mock_and_deep_copied_at_both_boundaries(self):
        fixture = {"summary": "original", "nested": {"count": 1}}
        provider = ReplayProvider([fixture, fixture])
        fixture["nested"]["count"] = 99
        first = provider.propose(REQUEST, SCHEMA)
        first.proposal["nested"]["count"] = 55
        second = provider.propose(REQUEST, SCHEMA)
        self.assertTrue(first.mock)
        self.assertEqual(second.proposal["nested"]["count"], 1)
        self.assertEqual(provider.propose(REQUEST, SCHEMA).reason, "replay_exhausted")

    def test_replay_cannot_return_or_consume_public_credentials(self):
        with self.assertRaises(PrivateConfigError):
            ReplayProvider({"api_key": "fixture"})
        provider = ReplayProvider({"summary": "safe"})
        rejected = provider.propose({"api_key": "fixture"}, SCHEMA)
        self.assertEqual(rejected.status, "error")
        self.assertEqual(provider.propose(REQUEST, SCHEMA).status, "proposal")


class ResponsesTests(unittest.TestCase):
    def test_request_matches_responses_strict_output_and_keeps_credential_private(self):
        provider, transport = adapter()
        schema = copy.deepcopy(SCHEMA)
        result = provider.propose(REQUEST, schema)
        call = transport.calls[0]
        self.assertEqual(call["endpoint"], DEFAULT_RESPONSES_ENDPOINT)
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + FAKE_CREDENTIAL)
        payload = call["payload"]
        self.assertEqual(payload["model"], "explicit-fixture-model")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["text"]["format"], {
            "type": "json_schema", "name": "factorio_design_proposal", "strict": True, "schema": schema})
        self.assertNotIn("reasoning", payload)
        self.assertNotIn(FAKE_CREDENTIAL, json.dumps(payload))
        self.assertEqual(result.status, "proposal")
        self.assertTrue(result.mock)
        self.assertEqual(result.proposal, {"summary": "fixture only"})
        self.assertEqual(result.raw_proposal_text, '{"summary":"fixture only"}')
        self.assertEqual(result.metadata["input_tokens"], 12)
        self.assertEqual(result.metadata["response_id"], "resp_fixture")
        self.assertNotIn(FAKE_CREDENTIAL, repr(result))
        self.assertNotIn(FAKE_CREDENTIAL, repr(provider))
        self.assertNotIn(FAKE_CREDENTIAL, repr(response()))

    def test_refusal_and_incomplete_are_never_accepted_as_proposals(self):
        cases = [
            (response(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "private refusal text"}]}]),
             "refusal", "provider_refusal"),
            (response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
             "incomplete", "output_budget_exhausted"),
            (response(status="incomplete", incomplete_details={"reason": "untrusted arbitrary text"}),
             "incomplete", "provider_incomplete"),
            (response(status="failed", error={"message": "untrusted error text"}), "error", "provider_error"),
        ]
        for answer, status, reason in cases:
            provider, _ = adapter(answer)
            result = provider.propose(REQUEST, SCHEMA)
            self.assertEqual((result.status, result.reason), (status, reason))
            self.assertIsNone(result.proposal)
            self.assertIsNone(result.raw_proposal_text)
            self.assertEqual(result.metadata["model"], "explicit-fixture-model")
            self.assertNotIn("private refusal", repr(result))
            self.assertNotIn("untrusted", repr(result))

    def test_json_duplicates_nonfinite_and_invalid_utf8_are_rejected(self):
        cases = [TransportResponse(200, '{"status":"completed","status":"failed"}'),
                 TransportResponse(200, b'\xff'), response('{"summary":"a","summary":"b"}'),
                 response('{"summary":NaN}'), response('{"summary":1e999}'),
                 response('{"summary":-1e999}'), response('[]'), response('{invalid')]
        for answer in cases:
            with self.subTest(answer_type=type(answer.body).__name__):
                provider, _ = adapter(answer)
                result = provider.propose(REQUEST, SCHEMA)
                self.assertEqual(result.status, "error")
                self.assertIsNone(result.proposal)

    def test_credential_echo_is_rejected_even_when_json_escaped(self):
        for text in (json.dumps({"summary": FAKE_CREDENTIAL}),
                     '{"summary":"' + FAKE_CREDENTIAL.replace("fixture", "fixt\\u0075re") + '"}'):
            provider, _ = adapter(response(text))
            result = provider.propose(REQUEST, SCHEMA)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.reason, "sensitive_response")
            self.assertNotIn(FAKE_CREDENTIAL, repr(result))
            self.assertIsNone(result.raw_proposal_text)

    def test_supplied_credential_is_rejected_in_public_request_before_transport(self):
        provider, transport = adapter()
        result = provider.propose({"request": {"goal": FAKE_CREDENTIAL}}, SCHEMA)
        self.assertEqual(result.reason, "credentials_forbidden")
        self.assertEqual(transport.calls, [])

    def test_schema_must_close_every_object_and_require_every_field(self):
        for schema in ({"type": "object", "properties": {}, "required": []},
                       {**SCHEMA, "required": []},
                       {**SCHEMA, "properties": {"summary": {"type": "object", "properties": {}}}},
                       {**SCHEMA, "required": [{}]}):
            provider, transport = adapter()
            result = provider.propose(REQUEST, schema)
            self.assertEqual(result.status, "error")
            self.assertEqual(transport.calls, [])

    def test_ambiguous_transport_failure_and_rate_limit_never_retry(self):
        for answer in (RuntimeError("possibly sent " + FAKE_CREDENTIAL), TransportResponse(429, FAKE_CREDENTIAL)):
            provider, transport = adapter(answer, max_retries=2)
            result = provider.propose(REQUEST, SCHEMA)
            self.assertEqual(result.status, "error")
            self.assertEqual(len(transport.calls), 1)
            self.assertNotIn(FAKE_CREDENTIAL, repr(result))

    def test_only_confirmed_unsent_preflight_failure_can_retry(self):
        transport = FakeTransport([RetryablePreflightError("fixture unsent"), response()])
        provider = ResponsesProvider(ResponsesConfig("chosen", max_retries=1), transport, lambda: FAKE_CREDENTIAL)
        result = provider.propose(REQUEST, SCHEMA)
        self.assertEqual(result.status, "proposal")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(result.metadata["attempt_count"], 2)

    def test_default_retries_are_zero(self):
        provider, transport = adapter(RetryablePreflightError("fixture unsent"))
        self.assertEqual(provider.propose(REQUEST, SCHEMA).reason, "transport_unavailable")
        self.assertEqual(len(transport.calls), 1)

    def test_invalid_unicode_or_nonfinite_request_fails_before_local_key_entry(self):
        for goal in ("\ud800", float("inf")):
            transport = FakeTransport([])
            suppliers = []
            provider = ResponsesProvider(ResponsesConfig("chosen"), transport,
                                         lambda: suppliers.append(True) or FAKE_CREDENTIAL)
            result = provider.propose({"request": {"goal": goal}}, SCHEMA)
            self.assertEqual(result.status, "error")
            self.assertEqual(transport.calls, [])
            self.assertEqual(suppliers, [])

    def test_response_size_and_total_deadline_are_enforced(self):
        provider, _ = adapter(TransportResponse(200, "x" * 1025), max_response_bytes=1024)
        self.assertEqual(provider.propose(REQUEST, SCHEMA).reason, "response_too_large")
        current = [0.0]
        def transport(**kwargs):
            self.assertEqual(kwargs["timeout_s"], 2)
            current[0] = 3
            return response()
        provider = ResponsesProvider(ResponsesConfig("chosen", timeout_s=2, total_deadline_s=2),
                                     transport, lambda: FAKE_CREDENTIAL, clock=lambda: current[0])
        self.assertEqual(provider.propose(REQUEST, SCHEMA).reason, "deadline_exceeded")

    def test_usage_metadata_is_bounded_and_untrusted_strings_not_copied(self):
        provider, _ = adapter(response(usage={"input_tokens": True, "output_tokens": -1,
                                            "total_tokens": 1000000001, "debug": "arbitrary"},
                                       id="untrusted id with spaces", model="unknown model with spaces"))
        result = provider.propose(REQUEST, SCHEMA)
        self.assertEqual(set(result.metadata), {"latency_ms", "attempt_count", "model"})

    def test_exact_payload_preflight_checks_escaping_and_never_calls_supplier(self):
        config = ResponsesConfig("chosen", max_request_bytes=4096)
        small_request = {"request": {"goal": "iron"}}
        expected = prepare_request(config, small_request, SCHEMA)
        transport = FakeTransport([response()])
        calls = []
        provider = ResponsesProvider(config, transport, lambda: calls.append(True) or FAKE_CREDENTIAL)
        result = provider.propose(small_request, SCHEMA)
        self.assertEqual(result.status, "proposal")
        self.assertEqual(transport.calls[0]["payload"], expected)
        self.assertEqual(len(calls), 1)
        # Escaped control characters and the extra serialized input layer make
        # the actual request larger than the apparent goal text.
        oversized = {"request": {"goal": "\n" * 1400}}
        self.assertLess(len(json.dumps(oversized).encode("utf-8")), config.max_request_bytes)
        with self.assertRaises(PrivateConfigError) as failure:
            prepare_request(config, oversized, SCHEMA)
        self.assertEqual(str(failure.exception), "request_too_large")
        calls.clear()
        result = provider.propose(oversized, SCHEMA)
        self.assertEqual(result.reason, "request_too_large")
        self.assertEqual(calls, [])
        self.assertEqual(len(transport.calls), 1)

    def test_configuration_errors_do_not_echo_supplier_or_transport_exceptions(self):
        def unavailable():
            raise RuntimeError(FAKE_CREDENTIAL)
        provider = ResponsesProvider(ResponsesConfig("chosen"), FakeTransport([]), unavailable)
        result = provider.propose(REQUEST, SCHEMA)
        self.assertEqual(result.reason, "credential_unavailable")
        self.assertNotIn(FAKE_CREDENTIAL, repr(result))


class FakeHTTPResponse:
    def __init__(self, body=b'{}', status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class FakeSocket:
    def settimeout(self, timeout):
        self.timeout = timeout


class FakeHTTPConnection:
    def __init__(self, answer, delay=0):
        self.answer = answer
        self.delay = delay
        self.sock = FakeSocket()
        self.calls = []
        self.closed = False

    def connect(self):
        if self.delay:
            time.sleep(self.delay)

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))

    def getresponse(self):
        return self.answer

    def close(self):
        self.closed = True


class LiveTransportWithFakeHTTPTests(unittest.TestCase):
    """The live transport is exercised through fake sockets, never the internet."""

    def invoke(self, answer, **options):
        connection = FakeHTTPConnection(answer)
        with patch("designer_llm.providers.http.client.HTTPSConnection", return_value=connection) as factory:
            result = OpenAIHTTPTransport()(endpoint=DEFAULT_RESPONSES_ENDPOINT,
                                          headers={"Authorization": "Bearer " + FAKE_CREDENTIAL},
                                          payload={"model": "chosen"}, timeout_s=1,
                                          max_response_bytes=options.get("max_response_bytes", 1024))
        factory.assert_called_once_with("api.openai.com", timeout=1)
        return result, connection

    def test_official_https_request_identity_encoding_and_real_marker(self):
        result, connection = self.invoke(FakeHTTPResponse())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(connection.calls[0][:2], ("POST", "/v1/responses"))
        self.assertEqual(connection.calls[0][2]["headers"]["Accept-Encoding"], "identity")
        self.assertTrue(connection.closed)
        provider = ResponsesProvider(ResponsesConfig("chosen"), OpenAIHTTPTransport(), lambda: FAKE_CREDENTIAL)
        self.assertFalse(provider.mock)

    def test_redirect_is_never_followed(self):
        result, connection = self.invoke(FakeHTTPResponse(status=307, headers={"Location": "https://example.invalid"}))
        self.assertEqual(result.status_code, 307)
        self.assertEqual(len(connection.calls), 1)

    def test_custom_endpoint_is_rejected_before_creating_connection(self):
        with patch("designer_llm.providers.http.client.HTTPSConnection") as factory:
            with self.assertRaises(HTTPTransportError) as failure:
                OpenAIHTTPTransport()(endpoint="https://example.invalid/v1/responses", headers={},
                                      payload={}, timeout_s=1, max_response_bytes=1024)
            self.assertEqual(str(failure.exception), "unsupported_live_endpoint")
            factory.assert_not_called()

    def test_compression_and_large_advertised_or_streamed_body_are_rejected(self):
        answers = [FakeHTTPResponse(headers={"Content-Encoding": "gzip"}),
                   FakeHTTPResponse(headers={"Content-Length": "1025"}),
                   FakeHTTPResponse(body=b"x" * 1025)]
        for answer in answers:
            with self.assertRaises(HTTPTransportError) as failure:
                self.invoke(answer)
            self.assertIn(str(failure.exception), {"unsupported_response_encoding", "response_too_large"})

    def test_deadline_cancels_before_sending_after_slow_connect(self):
        connection = FakeHTTPConnection(FakeHTTPResponse(), delay=0.2)
        with patch("designer_llm.providers.http.client.HTTPSConnection", return_value=connection):
            before = time.monotonic()
            with self.assertRaises(HTTPTransportError) as failure:
                OpenAIHTTPTransport()(endpoint=DEFAULT_RESPONSES_ENDPOINT,
                                      headers={"Authorization": "Bearer " + FAKE_CREDENTIAL},
                                      payload={}, timeout_s=0.1, max_response_bytes=1024)
            self.assertEqual(str(failure.exception), "transport_timeout")
            self.assertLess(time.monotonic() - before, 0.18)
            time.sleep(0.12)  # Let the fake connect complete and observe cancellation.
        self.assertEqual(connection.calls, [])
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
