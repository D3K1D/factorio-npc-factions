"""Which research to take when nothing its goals wait on is offered: each option weighed by what it is worth
(the way to the rocket, the team's goal; what its factory's priorities want of what it unlocks) against what it
costs (units of packs), rather than a model picking from the options' descriptions (System One picked "lamp" at
100% in run 19, 2026-10-25). The next science tier's research (companion/tier_path.py) comes before the weighing
when it is offered: grey science's military-2 waited until minute 230 for the team's military share (run 51).

Every choice is written down (decisions/research.jsonl) with what it weighed, for a learned model to take over.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from companion import tier_path

ROCKET = 1.0          # on the way to the rocket silo (space: the team's goal)
TIER = 1.0            # what a wanted science tier still waits on (companion/tier_path.py)
# Survival research (weapons, walls, armor, repair, radar: the game marks it `survival`) against the next tier's:
TIER_PULL = 0.2       # the next tier's pull when on time: above the calm line, under the alert line (threat.py)
LATE_SCALE = 60.0     # minutes past its aim that double the next tier's pull
LATE_CAP = 3.0        # (at most this many times: a full emergency still beats a long-late tier)
EVOLUTION = 0.5       # how much fully evolved biters press before any attack (a death world is never calm)
PRIORITY = 1.0        # times the priority weight of the most wanted item it unlocks
BONUS_ONLY = 0.15     # unlocks no recipe (a bonus): worth this much before the way and priorities
COST_SCALE = 300.0    # units times packs that halve an option's worth
LOG_NAME = "research.jsonl"


def cost(option: Mapping[str, Any]) -> float:
    units = float(option.get("units") or 0)
    packs = len(option.get("packs") or ()) or 1
    return units * packs


def weigh(options: Iterable[Mapping[str, Any]], site: Mapping[str, Any] | None = None) -> list[dict]:
    """Every option with its worth and parts, best first."""
    tiers = set(tier_path.assess(site).research()) if site else set()
    prio = None
    if site:
        try:
            from companion import priorities
            prio = priorities.assess(site, hold=False)
        except Exception:
            prio = None
    rows = []
    for o in options:
        if not isinstance(o, Mapping) or not o.get("name"):
            continue
        unlocks = [str(u) for u in o.get("unlocks") or ()]
        wanted = max((prio.of(u).weight for u in unlocks), default=0.0) if prio else 0.0
        tier = o["name"] in tiers
        worth = ((ROCKET if o.get("rocket_path") else 0.0) + (TIER if tier else 0.0) + PRIORITY * wanted
                 + (0.0 if unlocks else BONUS_ONLY))
        score = worth / (1.0 + cost(o) / COST_SCALE)
        rows.append({"name": o["name"], "score": round(score, 4), "worth": round(worth, 3), "wanted": round(wanted, 3),
                     "rocket_path": bool(o.get("rocket_path")), "tier": tier, "cost": cost(o)})
    rows.sort(key=lambda r: (-r["score"], r["cost"], r["name"]))
    return rows


def _fresh(site: Mapping[str, Any], request: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The last plan request's site with the research request's fresher parts over it: research moves on between plan
    requests, and so do traits and threats (what its machines make is the plan request's)."""
    if not (request and isinstance(request.get("tiers"), Mapping)):
        out = dict(site)
    else:
        out = {**site, "tiers": request["tiers"],
               "tick": max(int(request.get("tick") or 0), int(site.get("tick") or 0))}
    for key in ("personality", "threat_board", "social"):
        if request and request.get(key):
            out[key] = request[key]
    return out


def _tier_choice(options: list[Mapping[str, Any]], path: tier_path.Path) -> tuple[str, tier_path.Tier] | None:
    offered = {str(o["name"]) for o in options}
    for tier in path.researching():
        for name in tier.left():
            if name in offered:
                return name, tier
    return None


def tier_pick(options: Iterable[Mapping[str, Any]], site: Mapping[str, Any] | None,
              request: Mapping[str, Any] | None = None) -> tuple[str, str] | None:
    """The first offered technology the next science tiers wait on (prerequisites come first, so the first offered is
    the one to take now), and why; None when none is offered. Written down like every choice."""
    if not site:
        return None   # (no plan request yet: nothing known of what its machines make)
    site = _fresh(site, request)
    options = [o for o in options if isinstance(o, Mapping) and o.get("name")]
    path = tier_path.assess(site)
    picked = _tier_choice(options, path)
    if not picked:
        return None
    name, tier = picked
    why = f"the {tier.pack} tier waits for it (aim {tier.aim:.0f}m, now {path.minute:.0f}m)"
    record(request or {}, weigh(options, site), chose=name, why=why)
    return name, why


def pressure(site: Mapping[str, Any]) -> tuple[float, str]:
    """How hard the biters press on its base now (0 to 1), and why: the threat (companion/threat.py: attacks, gaps in
    its cover, breaches, war) or, before any attack, how far the biters have evolved."""
    from companion import threat
    t = threat.estimate(site)
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    try:
        evolution = min(1.0, max(0.0, float(board.get("evolution") or 0)))
    except (TypeError, ValueError):
        evolution = 0.0
    level = threat.either([t.level, EVOLUTION * evolution])
    why = "; ".join(t.why) or f"biters evolved {evolution:.2f}"
    return level, why


def _weighed(options: list[Mapping[str, Any]], site: Mapping[str, Any]):
    """The tier choice and its pull, the pressure, and the survival options each with its worth, best first."""
    path = tier_path.assess(site)
    tiered = _tier_choice(options, path)
    tier_score = 0.0
    if tiered:
        late = max(0.0, path.minute - tiered[1].aim)
        tier_score = TIER_PULL * min(LATE_CAP, 1.0 + late / LATE_SCALE)
    level, because = pressure(site)
    survival = sorted(((level / (1.0 + cost(o) / COST_SCALE), o) for o in options if o.get("survival")),
                      key=lambda so: (-so[0], so[1]["name"]))
    return path, tiered, tier_score, level, because, survival


def _note(request: Mapping[str, Any] | None, options, site, chose: str, why: str, tier_score: float, level: float,
          survival) -> None:
    record(request or {}, weigh(options, site), chose=chose, why=why, pressure=round(level, 3),
           tier_score=round(tier_score, 3), survival={o["name"]: round(sc, 3) for sc, o in survival})


def survival_pick(options: Iterable[Mapping[str, Any]], site: Mapping[str, Any] | None,
                  request: Mapping[str, Any] | None = None) -> tuple[str, str] | None:
    """Survival research when the biters press harder than an on-time tier pulls (TIER_PULL): taken before the
    team's basics and the factory's needs. None when calm enough or none is offered."""
    if not site:
        return None
    site = _fresh(site, request)
    options = [o for o in options if isinstance(o, Mapping) and o.get("name")]
    path, tiered, tier_score, level, because, survival = _weighed(options, site)
    if not survival or survival[0][0] < max(TIER_PULL, tier_score):
        return None
    score, best = survival[0]
    why = f"survival: pressure {level:.2f} ({because}), worth {score:.2f} over the tier's pull {max(TIER_PULL, tier_score):.2f}"
    _note(request, options, site, best["name"], why, tier_score, level, survival)
    return best["name"], why


def pick(options: Iterable[Mapping[str, Any]], site: Mapping[str, Any] | None,
         request: Mapping[str, Any] | None = None) -> tuple[str, str] | None:
    """Survival research or the next tier's, weighed: a survival option (the game marks it) is worth the pressure
    on the base over what it costs; the next tier's research TIER_PULL, more the later its tier is (up to LATE_CAP
    times). When calm the tier path wins; under threat a cheap survival tech does. None when neither is offered, or
    only survival is and it is calm. Written down like every choice, with the scores weighed."""
    if not site:
        return None   # (no plan request yet)
    site = _fresh(site, request)
    options = [o for o in options if isinstance(o, Mapping) and o.get("name")]
    path, tiered, tier_score, level, because, survival = _weighed(options, site)
    if survival and survival[0][0] > tier_score and (tiered or survival[0][0] >= TIER_PULL):
        score, best = survival[0]
        why = (f"survival: pressure {level:.2f} ({because}), worth {score:.2f} against "
               + (f"the {tiered[1].pack} tier's {tier_score:.2f}" if tiered else f"the line {TIER_PULL:.2f}"))
        _note(request, options, site, best["name"], why, tier_score, level, survival)
        return best["name"], why
    if tiered:
        name, tier = tiered
        why = (f"the {tier.pack} tier waits for it (aim {tier.aim:.0f}m, now {path.minute:.0f}m; "
               f"pressure {level:.2f})")
        _note(request, options, site, name, why, tier_score, level, survival)
        return name, why
    return None


_LOCK = threading.Lock()


def choose(options: Iterable[Mapping[str, Any]], site: Mapping[str, Any] | None = None,
           request: Mapping[str, Any] | None = None) -> tuple[str, list[dict]] | None:
    rows = weigh(options, site)
    if not rows:
        return None
    record(request or {}, rows)
    return rows[0]["name"], rows


def record(request: Mapping[str, Any], rows: list[dict], chose: str | None = None, why: str | None = None,
           **weighed: Any) -> None:
    data = os.environ.get("NPC_FACTIONS_DATA")
    if not data or not rows:
        return
    row = {"time": time.time(), "npc": request.get("npc_id"), "request": request.get("request_id"),
           "researched": request.get("researched"), "chose": chose or rows[0]["name"], "options": rows}
    if why:
        row["why"] = why
    row.update(weighed)   # (what was weighed against what: pressure, the tier's pull, survival worths)
    path = Path(data) / "decisions" / LOG_NAME
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass   # (a log, never in the way)
