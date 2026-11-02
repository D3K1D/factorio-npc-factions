"""What the LLMs cost: every paid call (strategist, designers, System One) is noted in one ledger
with its tokens and priced from the table below. Test runners read it to report a run's spend.

Prices are USD per 1M tokens, OpenAI's short-context rates (developers.openai.com/api/docs/pricing,
checked 2026-10-17). The Decisions API (System One) charges input only. A model missing here is
still counted, with its cost left unknown.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable

RESPONSES = {   # model: (input, cached input, output)
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
}
DECISIONS = {   # model: input
    "gpt-6-luna": 0.10,
}
LONG_CONTEXT = 272_000   # above this many input tokens the whole request costs more: not priced here

LEDGER: list[dict[str, Any]] = []
_lock = threading.Lock()


def price(model: str, input_tokens: int, output_tokens: int = 0, cached_tokens: int = 0,
          api: str = "responses") -> float | None:
    """USD for one call, or None when the model's price is not known."""
    if api == "codex":
        return 0.0   # on the user's ChatGPT plan: nothing per token
    if api == "decisions":
        rate = DECISIONS.get(model)
        return None if rate is None else input_tokens * rate / 1e6
    rates = RESPONSES.get(model)
    if rates is None or input_tokens > LONG_CONTEXT:
        return None
    fresh = max(0, input_tokens - cached_tokens)
    return (fresh * rates[0] + cached_tokens * rates[1] + output_tokens * rates[2]) / 1e6


def record(system: str, model: str, *, input_tokens: Any = 0, output_tokens: Any = 0, cached_tokens: Any = 0,
           api: str = "responses", latency_ms: Any = None, ok: bool = True, purpose: Any = None,
           npc: Any = None) -> dict[str, Any]:
    """Note one call. Bad numbers count as 0: a usage report must never break a game."""
    def count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0
    tokens_in, tokens_out, cached = count(input_tokens), count(output_tokens), count(cached_tokens)
    entry = {"at": time.time(), "system": system, "model": model, "api": api, "input": tokens_in,
             "output": tokens_out, "cached": cached, "cost": price(model, tokens_in, tokens_out, cached, api),
             "latency_ms": round(float(latency_ms)) if isinstance(latency_ms, (int, float)) else None,
             "ok": bool(ok), "purpose": str(purpose)[:120] if purpose else None,
             "npc": npc if isinstance(npc, (int, str)) else None}
    with _lock:
        LEDGER.append(entry)
    return entry


def mark() -> int:
    """Where the ledger is now; entries(mark) is everything after it."""
    with _lock:
        return len(LEDGER)


def entries(since: int = 0) -> list[dict[str, Any]]:
    with _lock:
        return [dict(e) for e in LEDGER[since:]]


def summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Calls, tokens and cost per system, and in all."""
    systems: dict[str, dict[str, Any]] = {}
    total = {"calls": 0, "failed": 0, "input": 0, "output": 0, "cached": 0, "cost": 0.0, "unpriced": 0}
    for row in rows:
        s = systems.setdefault(row["system"], {"calls": 0, "failed": 0, "input": 0, "output": 0, "cached": 0,
                                               "cost": 0.0, "unpriced": 0, "models": [], "latency_ms": []})
        for bucket in (s, total):
            bucket["calls"] += 1
            bucket["failed"] += 0 if row.get("ok", True) else 1
            for key in ("input", "output", "cached"):
                bucket[key] += row.get(key) or 0
            if row.get("cost") is None:
                bucket["unpriced"] += 1
            else:
                bucket["cost"] += row["cost"]
        if row.get("model") and row["model"] not in s["models"]:
            s["models"].append(row["model"])
        if row.get("latency_ms") is not None:
            s["latency_ms"].append(row["latency_ms"])
    for s in systems.values():
        lat = sorted(s.pop("latency_ms"))
        s["median_latency_ms"] = lat[len(lat) // 2] if lat else None
        s["cost"] = round(s["cost"], 6)
    total["cost"] = round(total["cost"], 6)
    return {"systems": systems, "total": total}
