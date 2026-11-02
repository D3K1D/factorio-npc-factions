"""How threatened the engineer's base is: one estimate that every judgment asking "are we under threat?" reads.

It was a 10-minute on/off flag: an attack anywhere made every goal defensive and an emergency meant walls made by
hand along a whole edge, however well the turrets held (Codex's and a subagent's review, 2026-10-25). Now:

- **The gap:** what presses on each part of the base against what covers it (companion/defense_brain.py). A wave
  its turrets hold leaves no gap: no threat, though the fight is still on (ammunition matters then).
- **How soon:** the last hit fades, and the waves' spacing says when the next is due. Biters that keep coming keep
  the level up between waves; one wave long ago lets it fall.
- **Breaches, fights and foes** add to it, each on its own.

The level runs 0 (calm) to 1. Its thresholds are parameters, and each judgment made with it is written down
(decisions/threat.jsonl) for a learned model to take over.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

RECENCY_S = 600.0          # the last hit's weight fades over this (seconds)
HORIZON_S = 600.0          # the next wave due this soon counts in full; later, less
PRIOR_SPACING_S = 600.0    # waves' spacing assumed before any has been seen twice
PRIOR_WEIGHT = 1.0         # (in attacks: how many seen waves outweigh the assumption)
PERIODIC = 0.5             # how much the time since the last wave brings the next nearer (0: not at all)
MIN_SPACING_S = 60.0
MIN_BUDGET_S = 45.0        # the next wave is never taken to be sooner than this
GAP_SCALE = 10.0           # small biters of gap (pressure past cover) that make the gap nearly count in full
GAP_UNKNOWN = 0.7          # the gap's weight when its base has nothing to weigh yet (no buildings exported)
SOCIAL_AGO_S = 300.0       # attacks by others are counted for the last 10 minutes, with no time: about this
BREACH, BREACH_EACH = 0.6, 0.2   # an open breach in its wall, and each more
EVENT = 0.9                # a fight going on now (an event its strategist hears)
FOE = 0.45                 # at war with another faction
URGENT_S = 180.0           # hit this lately, or a breach open: urgent

CALM = 0.1                 # under this: in the clear
ALERT = 0.35               # from this: threatened (defense first, no clearing, more military research)
EMERGENCY_FULL = 0.7       # from this: an emergency in full (the most hand work given to defense)

HAND_SHARE = 0.6           # of the time to the next wave, what hand work for defense may take in a full emergency
MAX_BUDGET_S = 900.0       # (at most this much hand work for one emergency build)
LOG_NAME = "threat.jsonl"


@dataclass(frozen=True)
class Threat:
    level: float = 0.0
    gap: float = 0.0                 # the largest gap of any part of the base (small biters)
    known_gap: bool = False          # whether there was a base to weigh
    since_s: float | None = None     # since the last hit
    spacing_s: float = PRIOR_SPACING_S
    next_wave_s: float = PRIOR_SPACING_S
    confidence: float = 0.0          # how much the spacing is seen rather than assumed
    attacks: int = 0
    breaches: int = 0
    urgent: bool = False
    why: tuple = field(default_factory=tuple)

    @property
    def threatened(self) -> bool:
        return self.level >= ALERT

    def features(self) -> dict:
        return {"level": round(self.level, 3), "gap": round(self.gap, 2), "known_gap": self.known_gap,
                "since_s": None if self.since_s is None else round(self.since_s, 1),
                "spacing_s": round(self.spacing_s, 1), "next_wave_s": round(self.next_wave_s, 1),
                "confidence": round(self.confidence, 2), "attacks": self.attacks, "breaches": self.breaches,
                "urgent": self.urgent}


def _num(v: Any, default: float = 0.0) -> float:
    """A number the game sent, or `default` (never a table taken for one: D-20's `ever`, late-combat)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def either(parts: list[float]) -> float:
    """Any one of them (a noisy or): two half-threats make more than one, never more than 1."""
    calm = 1.0
    for p in parts:
        calm *= 1.0 - max(0.0, min(1.0, p))
    return 1.0 - calm


def spacing(attacks: list[Mapping[str, Any]], tick: float | None) -> tuple[float, float | None, int, float]:
    """(waves' spacing, seconds since the last hit, waves seen, how much the spacing is seen) from the game's
    threat board: the waves seen over the time since the first of them, with an assumed spacing that counts
    as PRIOR_WEIGHT waves (one wave: halfway between the time since it and the assumption)."""
    if tick is None:
        return PRIOR_SPACING_S, None, 0, 0.0
    lasts = [_num(a.get("tick")) for a in attacks if isinstance(a.get("tick"), (int, float))]
    if not lasts:
        return PRIOR_SPACING_S, None, 0, 0.0
    firsts = [_num(a.get("first"), _num(a.get("tick"))) for a in attacks if isinstance(a.get("tick"), (int, float))]
    n = len(lasts)
    since = max(0.0, (tick - max(lasts)) / 60.0)
    window = max(0.0, (tick - min(firsts)) / 60.0)
    space = max(MIN_SPACING_S, (window + PRIOR_WEIGHT * PRIOR_SPACING_S) / (n + PRIOR_WEIGHT))
    return space, since, n, n / (n + PRIOR_WEIGHT)


def attacked(site: Mapping[str, Any]) -> int:
    """How many attacks its plot has taken: all of them (the threat board keeps that for good), else those of the
    last 30 minutes it shows (an older game's board)."""
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    ever = board.get("ever") if isinstance(board.get("ever"), Mapping) else {}
    lately = sum(1 for a in board.get("attacks") or [] if isinstance(a, Mapping))
    return max(int(_num(ever.get("count"))), lately)


def unwalled_hit(site: Mapping[str, Any]) -> bool:
    """Attacked (ever: the game keeps it) with no wall of its ring standing yet: its walls are due at once, by hand if
    need be, and its turrets before anything else (the user, 2026-10-26: "at once if the engineer was already
    attacked before walls")."""
    from companion import catalog_planner, perimeter
    return attacked(site) > 0 and not catalog_planner.perimeter_blocks(site, perimeter.WALL)


def open_breaches(site: Mapping[str, Any]) -> int:
    """Breaches with fewer than four layers standing that have not had all their repairs yet."""
    from companion import perimeter
    repairs = [(b["box"], b.get("accepted")) for b in site.get("base") or []
               if isinstance(b, Mapping) and b.get("purpose") == perimeter.BREACH and b.get("box")]
    n = 0
    for b in site.get("breaches") or []:
        if not isinstance(b, Mapping) or "x" not in b:
            continue
        # (a hole is open whatever the layers about it: they are counted round it, D-23, Codex's review)
        if int(b.get("layers") or 0) >= perimeter.MAX_LAYERS and not b.get("hole"):
            continue
        x, y = float(b["x"]), float(b["y"])
        # (a hole's repairs: those taken since it opened, never old ones on the same stretch; Codex's D-23 review)
        since = float(b.get("hole_tick") or b.get("tick") or 0) if b.get("hole") else None
        done = sum(1 for r, at in repairs if r[0] - 16 <= x <= r[2] + 16 and r[1] - 16 <= y <= r[3] + 16
                   and (since is None or at is None or at >= since))
        if done < perimeter.MAX_LAYERS - 1:
            n += 1
    return n


_CACHE: dict[int, tuple[Mapping[str, Any], tuple, Threat]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 16


def _key(site: Mapping[str, Any]) -> tuple:
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    social = site.get("social") if isinstance(site.get("social"), Mapping) else {}
    return (site.get("tick"), len(board.get("attacks") or []), len(site.get("guards") or []),
            len(site.get("base") or []), len(site.get("breaches") or []), social.get("recent_attacks"))


def estimate(site: Mapping[str, Any]) -> Threat:
    """The threat to its base now (worked out once for a site: every goal's availability asks)."""
    key = _key(site)
    with _CACHE_LOCK:
        hit = _CACHE.get(id(site))
        if hit and hit[0] is site and hit[1] == key:
            return hit[2]
    t = _estimate(site)
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_SIZE:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[id(site)] = (site, key, t)
    return t


def _estimate(site: Mapping[str, Any]) -> Threat:
    from companion import defense_brain
    tick = site.get("tick") if isinstance(site.get("tick"), (int, float)) else None
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    attacks = [a for a in board.get("attacks") or [] if isinstance(a, Mapping)]
    space, since, n, confidence = spacing(attacks, tick)
    why: list[str] = []
    social = site.get("social") if isinstance(site.get("social"), Mapping) else {}
    others = _num(social.get("recent_attacks"))
    if others > 0:   # attacked by others (raiders, a faction at war): no times given, within the last 10 minutes
        since = SOCIAL_AGO_S if since is None else min(since, SOCIAL_AGO_S)
        n = max(n, int(others))
        confidence = max(confidence, n / (n + PRIOR_WEIGHT))
        why.append(f"attacked by others {int(others)} times lately")
    next_wave = max(MIN_BUDGET_S, space - PERIODIC * min(since if since is not None else 0.0, space))
    soon = 0.0
    if since is not None:
        soon = max(math.exp(-since / RECENCY_S), confidence * math.exp(-next_wave / HORIZON_S))
        why.append("hit just now" if since < 60 else f"hit {since / 60:.0f} minutes ago")
        if n > 1:
            why.append(f"{n} waves about {space / 60:.0f} minutes apart")
    gap, known = 0.0, False
    try:
        areas = defense_brain.assess(site)
    except Exception:   # (a judgment, never in the way: an unknown gap)
        areas = []
    if areas:
        known = True
        # (what presses now: not the base's next wave drawn on every part ahead of it, which is cover to build, D-13)
        gap = max(0.0, max(a.pressure_now - a.covered for a in areas))
    gap_term = (1.0 - math.exp(-gap / GAP_SCALE)) if known else GAP_UNKNOWN
    if known and since is not None:
        why.append(f"short {gap:.0f} small biters of cover" if gap > 0 else "its turrets hold")
    parts = [gap_term * soon]
    breaches = open_breaches(site)
    if breaches:
        parts.append(min(1.0, BREACH + BREACH_EACH * (breaches - 1)))
        why.append(f"{breaches} breach{'es' if breaches > 1 else ''} open")
    if site.get("foe"):
        parts.append(FOE)
        why.append(f"at war with {site.get('foe')}")
    level = either(parts)
    urgent = breaches > 0 or (since is not None and since < URGENT_S and gap_term > 0)
    return Threat(level=level, gap=gap, known_gap=known, since_s=since, spacing_s=space, next_wave_s=next_wave,
                  confidence=confidence, attacks=n, breaches=breaches, urgent=urgent, why=tuple(why))


def urgency(t: Threat) -> float:
    """0 at the alert line, 1 at a full emergency: how much of an emergency it is."""
    return max(0.0, min(1.0, (t.level - ALERT) / (EMERGENCY_FULL - ALERT)))


def hand_seconds(t: Threat) -> float:
    """The hand work (seconds) an emergency build may take: a share of the time to the next wave, as much as the
    emergency is one. What would take longer by hand waits for machines."""
    return min(MAX_BUDGET_S, max(MIN_BUDGET_S, t.next_wave_s)) * HAND_SHARE * urgency(t)


_LOG_LOCK = threading.Lock()


def record(site: Mapping[str, Any], t: Threat, decision: Mapping[str, Any]) -> None:
    """One judgment made with the threat, with what it saw, for learning (decisions/threat.jsonl)."""
    data = os.environ.get("NPC_FACTIONS_DATA")
    if not data:
        return
    row = {"time": time.time(), "npc": site.get("npc_id"), "tick": site.get("tick"), "request": site.get("request_id"),
           **t.features(), "why": list(t.why), **dict(decision)}
    path = Path(data) / "decisions" / LOG_NAME
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass   # (a log, never in the way of a plan)
