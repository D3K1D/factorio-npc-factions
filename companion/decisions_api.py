"""OpenAI Decisions API provider for NPC decisions (System One).

Each offered NPC decision becomes one ``choice`` question sent to
``POST /v1/decisions``. The model may only answer with an offered candidate ID;
the mod still re-checks legality before anything happens in the game.

The API key is read from ``OPENAI_API_KEY``: first from the process
environment, then (on Windows) from the user environment in the registry, so a
key set after this program started is still found. The key is never logged.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence

from companion import usage as ledger

ENDPOINT = "https://api.openai.com/v1/decisions"
MODEL = "gpt-6-luna"
QUESTION_NAME = "next_action"
INSTRUCTIONS = (
    "You choose the next action for an NPC engineer playing Factorio. "
    "Pick the option that best advances its current goal, given its state, "
    "surroundings and personality."
)

Transport = Callable[[str, Mapping[str, Any], str, float], Mapping[str, Any]]


class DecisionsAPIError(RuntimeError):
    """The Decisions API call failed or returned an unusable answer."""


def api_key_from_environment(name: str = "OPENAI_API_KEY") -> str | None:
    key = os.environ.get(name)
    if key:
        return key
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _ = winreg.QueryValueEx(handle, name)
        except OSError:
            return None
        return value or None
    return None


def build_input(request: Mapping[str, Any]) -> str:
    lines = []
    context = request.get("context")
    if context:
        lines.append(str(context))
    profile = request.get("profile") or {}
    if profile:
        traits = ", ".join(
            f"{key}={value:.2f}" if isinstance(value, float) else f"{key}={value}"
            for key, value in sorted(profile.items())
        )
        lines.append(f"Personality: {traits}")
    features = request.get("features") or {}
    if features:
        lines.append("State: " + json.dumps(features, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines)


def build_body(request: Mapping[str, Any], model: str = MODEL) -> dict[str, Any]:
    return {
        "model": model,
        "input": build_input(request),
        "questions": [{
            "type": "choice",
            "name": QUESTION_NAME,
            "instructions": INSTRUCTIONS,
            "choices": [{"value": c["id"], "description": c["description"]}
                        for c in request["candidates"]],
        }],
    }


def parse_answer(response: Mapping[str, Any],
                 candidate_ids: Sequence[str]) -> tuple[str, dict[str, float], float | None]:
    answers = response.get("answers")
    if not isinstance(answers, list):
        raise DecisionsAPIError("Response has no answers list")
    answer = next((a for a in answers if isinstance(a, Mapping) and a.get("name") == QUESTION_NAME), None)
    if answer is None:
        raise DecisionsAPIError("Response has no answer for the decision question")
    if answer.get("type") == "refusal":
        raise DecisionsAPIError("Model refused the decision")
    selected = answer.get("choice")
    if selected not in candidate_ids:
        raise DecisionsAPIError("Model chose an option that was not offered")
    probabilities = {cid: 0.0 for cid in candidate_ids}
    for entry in answer.get("probabilities") or []:
        if not isinstance(entry, Mapping) or entry.get("value") not in probabilities:
            continue
        value = entry.get("probability")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            probabilities[entry["value"]] = float(value)
    if sum(probabilities.values()) <= 0:
        probabilities[selected] = 1.0
    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        confidence = None
    return selected, probabilities, confidence


class OutOfCredit(DecisionsAPIError):
    """OpenAI says the account has no credit left: nothing more is asked of it this run."""


# While paused, nothing is asked of the AI and the engineers go on by rule: until the runner is started
# again after OpenAI's "no credit" (math.inf), or for a while after Codex's usage limit. Shared by every
# caller (the strategists, System One and the designer).
_paused_until = 0.0
_pause_reason = ""
_pause_lock = threading.Lock()


def out_of_credit() -> bool:
    return time.time() < _paused_until


def pause_reason() -> str:
    return _pause_reason or "the AI is paused"


def paused_until() -> float:
    return _paused_until


def pause_ai(seconds: float, reason: str) -> bool:
    """Pause every AI call for `seconds` (math.inf: until restarted). True when it was not paused before."""
    global _paused_until, _pause_reason
    with _pause_lock:
        fresh = time.time() >= _paused_until
        _paused_until = max(_paused_until, time.time() + seconds)
        if fresh:
            _pause_reason = reason
        return fresh


def resume_ai() -> None:
    global _paused_until, _pause_reason
    with _pause_lock:
        _paused_until, _pause_reason = 0.0, ""


def no_credit_reply(code: int, error: Any) -> bool:
    """A 429 that is the account's empty balance, not a passing rate limit."""
    if code != 429 or not isinstance(error, Mapping):
        return False
    kind = f"{error.get('code') or ''} {error.get('type') or ''}"
    return "insufficient_quota" in kind or "credit" in str(error.get("message") or "").lower()


def http_transport(url: str, body: Mapping[str, Any], api_key: str, timeout_s: float) -> Mapping[str, Any]:
    if out_of_credit():
        raise OutOfCredit(pause_reason())
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2000).decode("utf-8", "replace")
        error = None
        try:
            error = json.loads(detail).get("error", {})
            detail = error.get("message", detail)
        except (ValueError, AttributeError):
            pass
        if no_credit_reply(exc.code, error):
            pause_ai(math.inf, "OpenAI says there is no credit left")
            raise OutOfCredit(f"HTTP {exc.code}: {detail}") from None
        raise DecisionsAPIError(f"HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DecisionsAPIError(f"Request failed: {exc}") from None


class DecisionsProvider:
    backend = "openai-decisions"

    def __init__(self, *, api_key: str | None = None, model: str = MODEL,
                 timeout_s: float = 10.0, transport: Transport | None = None) -> None:
        key = api_key or api_key_from_environment()
        if not key:
            raise ValueError("OPENAI_API_KEY is not set")
        self._api_key = key
        self.model = model
        self.timeout_s = timeout_s
        self._transport = transport or http_transport
        self.last_latency_ms: float | None = None
        self.last_usage: Mapping[str, Any] | None = None
        self.last_request_bytes: int | None = None

    def __repr__(self) -> str:
        return f"DecisionsProvider(model={self.model!r})"

    def decide(self, request: dict[str, Any]) -> Mapping[str, Any]:
        body = build_body(request, self.model)
        self.last_request_bytes = len(json.dumps(body).encode("utf-8"))
        started = time.perf_counter()
        response = self._transport(ENDPOINT, body, self._api_key, self.timeout_s)
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        usage = response.get("usage")
        self.last_usage = usage if isinstance(usage, Mapping) else None
        ledger.record("system one", self.model, api="decisions", input_tokens=(self.last_usage or {}).get("input_tokens"),
                      latency_ms=self.last_latency_ms, purpose=request.get("purpose"))
        ids = [c["id"] for c in request["candidates"]]
        selected, probabilities, confidence = parse_answer(response, ids)
        return {
            "selected_id": selected,
            "probabilities": probabilities,
            "confidence": confidence,
            "backend": self.backend,
            "model_revision": self.model,
            "calibration_revision": "uncalibrated-factorio",
        }
