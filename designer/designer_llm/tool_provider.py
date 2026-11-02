"""Bounded Responses function-call loop controlling the companion draft grid."""

from __future__ import annotations

import copy
import hashlib
import re
import time

from ._reference_contract import ContractError, canonical_bytes
from .editor import DraftGridController, tool_schemas
from .private_config import PrivateConfigError, ResponsesConfig, assert_no_credentials, validate_credential
from .proposal_schema import proposal_schema, validate_wire
from .providers import (ProviderResult, TransportResponse, _InvalidJSON, _contains_secret,
                        _json_text, _parse_json, _check_strict_schema, prepare_request)


def prepare_tool_request(config, envelope, *, input_items=None, output_tokens=None):
    """Credential-free first payload and bounded replay of tool feedback.

    Opening/preflighting a tool session never calls the API. Function results
    are replayed with original response items, including encrypted reasoning.
    """
    payload = prepare_request(config, envelope,
                              proposal_schema(envelope["context"]["geometry_snapshot"]["game_version"]))
    payload.pop("text")
    instructions = (
        "Control the external Factorio draft-layout editor using only its supplied function tools. "
        "Your function calls perform real edits in that editor; the editor returns updated state and errors. "
        "The editor is a proposed, unbuilt layout, not the running game. Never claim native construction. "
        "Choose coordinates, orientations and recipes yourself from the supplied pinned geometry. "
        "When declared entity capabilities exist, choose machine upgrades, entity/recipe/module qualities, "
        "module counts and typed device settings with configure_entity; connect_entities uses declared "
        "connector IDs and wire reach. Unknown capabilities do not permit guessing these settings. "
        "Use place_entity to add the entities requested by the user; use move_entity/set_recipe/remove_entity to correct errors. "
        "Initial observed context is already supplied, so do not spend a turn reading an empty grid. "
        "You may batch independent edits. Calls execute in listed order and submit_plan must be last. "
        "Finish by calling submit_plan with a short plan ID and your construction stages; the host supplies "
        "snapshot identity and exactly the entities you actually placed through tools. "
        "Each stage must own nonempty, unique entities, a placement bill, and at least one same-stage planned "
        "product/resource-extraction/energy-flow or research_progress_delta witness. A power-only stage may "
        "use power_connected on a steam engine or pole (null item/min_delta). research_progress_delta targets "
        "a lab, has null item, and min_delta >= 1 counts research progress ticks/units. Do not split construction and its flow witnesses "
        "into a separate verification-only stage. Declare manual_supplies (fuel/input) for NPC hand-carrying "
        "or null when none. Submission checks known prototype facts, fuel, powered pole networks, "
        "mining patches/drop ports, crafting inputs and recipe categories. Correct returned validation "
        "errors if budget remains. "
        "Rotate fluid connection positions and outward directions with entities; normal ports must each "
        "target the other port one tile away. Underground pipes use nearest opposite-facing same-prototype "
        "ports within range. Separate fluid boxes stay separate; do not mix water and steam. Boilers need "
        "water from an offshore pump on a known shore and engines need steam from a water-fed boiler. "
        "Labs need power and an input manual supply of automation-science-pack or a feeding inserter. "
        "At most 3 API responses and 12 tool calls are available; finish promptly. "
    )
    structural_rules = payload["input"][0]["content"].replace(
        "normal quality and cardinal directions",
        "cardinal directions and only qualities/configurations declared by the supplied capability catalog "
        "(normal quality when that catalog is absent)")
    structural_rules = structural_rules.replace(
        "Propose a Factorio construction plan using the supplied schema.",
        "Construct a Factorio plan through the supplied function tools.")
    structural_rules = structural_rules.replace(
        "Use only the snapshot's prototypes, unlocked recipes and items, ",
        "Use only the snapshot's entity prototypes, unlocked recipes and stage-witness/bill items. "
        "Modules and circuit signals must come from the supplied richer catalog. Use ")
    payload["input"][0]["content"] = instructions + structural_rules
    tools = tool_schemas()
    for tool in tools:
        _check_strict_schema(tool["parameters"])
    payload["tools"] = tools
    payload["tool_choice"] = "required"
    payload["parallel_tool_calls"] = True
    # Legacy include remains supported and makes stateless reasoning continuity
    # explicit. No response is stored, and reasoning items stay in memory only.
    payload["include"] = ["reasoning.encrypted_content"]
    if input_items is not None:
        assert_no_credentials(input_items)
        payload["input"] = copy.deepcopy(input_items)
    if output_tokens is not None:
        if type(output_tokens) is not int or not 1 <= output_tokens <= config.max_output_tokens:
            raise PrivateConfigError("invalid_tool_output_budget")
        payload["max_output_tokens"] = output_tokens
    try:
        assert_no_credentials(payload)
        _json_text(payload, config.max_request_bytes)
    except _InvalidJSON:
        raise PrivateConfigError("request_too_large") from None
    return payload


class ToolEditingProvider:
    """A user-started bounded tool session; continuation is feedback, not retry.

    API responses are charged independently. The UI explicitly reviews this
    allowance before reading the key. Transport failures stop the session.
    The resulting proposal is assembled solely from executed model tool calls.
    """

    provider_id = "openai-responses-tools"

    def __init__(self, config, transport, credential_supplier, *, on_change=None,
                 max_requests=3, max_tool_calls=12, total_deadline_s=90,
                 clock=time.monotonic, mock=None):
        if not isinstance(config, ResponsesConfig) or not callable(transport) or not callable(credential_supplier):
            raise ValueError("invalid_tool_provider_configuration")
        if config.max_retries != 0 or not 256 <= config.max_output_tokens <= 8192 \
                or type(max_requests) is not int or not 1 <= max_requests <= 3 \
                or type(max_tool_calls) is not int or not 1 <= max_tool_calls <= 12 \
                or type(total_deadline_s) not in (int, float) or not 1 <= total_deadline_s <= 90:
            raise ValueError("invalid_tool_session_limits")
        self.config, self._transport, self._supplier = config, transport, credential_supplier
        self.max_requests, self.max_tool_calls, self.total_deadline_s = max_requests, max_tool_calls, total_deadline_s
        self.mock = getattr(transport, "mock", True) if mock is None else mock
        if type(self.mock) is not bool:
            raise ValueError("invalid_mock_flag")
        self._on_change, self._clock = on_change, clock
        self.trace = {"schema": "designer-model-tool-trace-v1", "calls": []}

    def __repr__(self):
        return "ToolEditingProvider(mock=" + str(self.mock) + ", max_requests=" + str(self.max_requests) + ")"

    def propose(self, envelope, schema):
        started, attempts = self._clock(), 0
        usage_total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        response_metadata = {}
        self.trace = {"schema": "designer-model-tool-trace-v1", "calls": []}

        def result(status, reason, proposal=None):
            metadata = {"model": self.config.model, "generation_mode": "tool_calls",
                        "attempt_count": attempts, "maximum_requests": self.max_requests,
                        "tool_call_count": len(self.trace["calls"]),
                        "latency_ms": max(0, min(90000, int((self._clock() - started) * 1000))),
                        **usage_total}
            for source, target in (("id", "response_id"), ("model", "response_model")):
                value = response_metadata.get(source)
                if type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value):
                    metadata[target] = value
            metadata["tool_trace_sha256"] = hashlib.sha256(canonical_bytes(self.trace)).hexdigest()
            return ProviderResult(status, proposal, self.provider_id, self.mock, reason, metadata)

        try:
            payload = prepare_tool_request(self.config, envelope)
            controller = DraftGridController(envelope["request"], envelope["context"], on_change=self._on_change)
        except Exception:
            return result("error", "invalid_public_request")
        try:
            credential = validate_credential(self._supplier())
        except Exception:
            return result("error", "credential_unavailable")
        if _contains_secret(payload, credential):
            return result("error", "credentials_forbidden")
        headers = {"Authorization": "Bearer " + credential, "Content-Type": "application/json"}
        input_items = copy.deepcopy(payload["input"])
        allowed = {tool["name"]: tool["parameters"] for tool in payload["tools"]}
        seen_ids = set()
        remaining_output = self.config.max_output_tokens

        for turn in range(self.max_requests):
            remaining_time = self.total_deadline_s - (self._clock() - started)
            if remaining_time <= 0:
                return result("error", "deadline_exceeded")
            if remaining_output < 256:
                return result("incomplete", "output_budget_exhausted")
            allocation = remaining_output
            try:
                payload = prepare_tool_request(self.config, envelope, input_items=input_items, output_tokens=allocation)
            except Exception:
                return result("error", "request_too_large")
            if _contains_secret(payload, credential):
                return result("error", "credentials_forbidden")
            # Reserve the whole allowance before dispatch. Refund unused tokens
            # only after a completed response with trustworthy bounded usage.
            remaining_output -= allocation
            attempts += 1
            try:
                response = self._transport(endpoint=self.config.endpoint, headers=headers,
                                           payload=copy.deepcopy(payload),
                                           timeout_s=min(self.config.timeout_s, remaining_time),
                                           max_response_bytes=self.config.max_response_bytes)
            except Exception:
                return result("error", "transport_error")
            if self._clock() - started > self.total_deadline_s:
                return result("error", "deadline_exceeded")
            if not isinstance(response, TransportResponse) or type(response.status_code) is not int:
                return result("error", "invalid_transport_response")
            if not 200 <= response.status_code < 300:
                return result("error", "credentials_rejected" if response.status_code in (401, 403)
                              else "provider_rate_limited" if response.status_code == 429 else "provider_http_error")
            try:
                raw = response.body.encode("utf-8") if type(response.body) is str else response.body
                if type(raw) is not bytes or len(raw) > self.config.max_response_bytes:
                    return result("error", "response_too_large")
                if credential.encode("ascii") in raw:
                    return result("error", "sensitive_response")
                parsed = _parse_json(raw.decode("utf-8"))
                if _contains_secret(parsed, credential):
                    return result("error", "sensitive_response")
                assert_no_credentials(parsed)
            except Exception:
                return result("error", "invalid_provider_json")
            if type(parsed) is not dict:
                return result("error", "invalid_provider_json")
            response_metadata = parsed
            usage = parsed.get("usage")
            if type(usage) is not dict or any(type(usage.get(k)) is not int or not 0 <= usage[k] <= 1_000_000_000
                                            for k in usage_total) or usage["output_tokens"] > allocation:
                return result("error", "invalid_provider_usage")
            for key in usage_total:
                usage_total[key] += usage[key]
            if parsed.get("status") == "incomplete":
                return result("incomplete", "output_budget_exhausted")
            if parsed.get("status") != "completed" or parsed.get("error") is not None:
                return result("error", "unexpected_provider_status")
            remaining_output += allocation - usage["output_tokens"]
            output = parsed.get("output")
            if type(output) is not list or len(output) > 128:
                return result("error", "invalid_provider_output")
            calls = []
            try:
                for item in output:
                    if type(item) is not dict:
                        raise ValueError()
                    kind = item.get("type")
                    if kind == "reasoning":
                        continue
                    if kind == "message":
                        content = item.get("content")
                        if type(content) is not list:
                            raise ValueError()
                        if any(type(part) is dict and part.get("type") == "refusal" for part in content):
                            return result("refusal", "provider_refusal")
                        continue
                    if kind != "function_call":
                        raise ValueError()
                    call_id, name, text = item.get("call_id"), item.get("name"), item.get("arguments")
                    if type(call_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call_id) \
                            or call_id in seen_ids or type(name) is not str or name not in allowed \
                            or type(text) is not str or len(text.encode("utf-8")) > 64_000:
                        raise ValueError()
                    arguments = _parse_json(text)
                    if _contains_secret(arguments, credential):
                        return result("error", "sensitive_response")
                    validate_wire(arguments, allowed[name])
                    assert_no_credentials(arguments)
                    seen_ids.add(call_id)
                    calls.append((item, arguments))
                if not calls:
                    return result("error", "missing_tool_submission")
                if len(self.trace["calls"]) + len(calls) > self.max_tool_calls:
                    return result("error", "tool_call_budget_exceeded")
                submissions = [i for i, (item, _) in enumerate(calls) if item["name"] == "submit_plan"]
                if submissions and submissions != [len(calls) - 1]:
                    raise ValueError()
            except Exception:
                return result("error", "invalid_tool_call")
            # Preserve exact response items in memory for function-call feedback.
            input_items.extend(copy.deepcopy(output))
            for item, arguments in calls:
                try:
                    answer = controller.call(item["name"], arguments)
                    if _contains_secret(answer, credential):
                        return result("error", "sensitive_response")
                    assert_no_credentials(answer)
                    trace_call = {"call_id": item["call_id"], "name": item["name"],
                                  "arguments": copy.deepcopy(arguments), "raw_arguments": item["arguments"],
                                  "result": copy.deepcopy(answer)}
                    candidate_trace = {"schema": self.trace["schema"], "calls": self.trace["calls"] + [trace_call]}
                    if len(canonical_bytes(candidate_trace)) > 200_000:
                        return result("error", "tool_trace_too_large")
                    self.trace = candidate_trace
                    input_items.append({"type": "function_call_output", "call_id": item["call_id"],
                                        "output": _json_text(answer, 96_000)})
                except Exception:
                    return result("error", "local_tool_failed")
                if item["name"] == "submit_plan" and answer.get("ok") is True:
                    return result("proposal", "tool_plan_submitted", controller.submitted_wire)
        return result("incomplete", "tool_session_budget_exhausted")
