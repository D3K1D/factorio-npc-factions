"""Whether a vehicle of its own pays an engineer, and which (M-1, 2026-10-26): cars and tanks once its research
allows, when its long walks cost it enough time.

From what its strategist sees (`situation["travel"]`, drive.lua `Drive.travel`): its long walks of the last game
hour (how many, the minutes an hour it spends on the ones long enough to drive), how fast its own drives go when
it has made some, its vehicle if it has one, and the vehicles its force can make now, each with what it takes
that only machines make (engine units) and how much of that it has.

    saved       the minutes an hour those walks would take less driven: on foot at 0.15 tiles a tick, driven at
                its own speed (the range: about 0.25 as the crow flies, boarding, doors and detours in)
    danger      on foot it cannot outrun small biters (0.24 against 0.15): nests near its home count for a little
    get         the minutes it takes to get one whose parts it has (crafting it, fetching the parts)

It asks for one (arm_up, focus "vehicle") when it has none, the time saved clears MIN_SAVED and pays back the
getting within HORIZON, and a vehicle's machine-made parts are at hand: the sturdiest such. Without the parts it
says what is short (for its strategist and the run's notes), and asks nothing. Every number is a weight to be
learned from how the trips go: each decision is written down (`record`) with what it saw.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

WALK_SPEED = 0.15             # tiles a tick on foot
DRIVE_SPEED = 0.25            # tiles a tick driven, as the crow flies, until its own drives say (drive.lua)
MIN_SAVED = 4.0               # game minutes an hour it must save at the least, or a vehicle is not worth the bother
HORIZON = 2.0                 # game hours the time saved is counted over, against the time to get one
GET_MINUTES = 2.0             # game minutes to get a vehicle whose parts it has (the craft, the fetching)
PART_MINUTES = 0.05           # and a little more for each machine-made part it fetches
DANGER = 0.25                 # minutes an hour a spawner near its home counts for (it cannot outrun their biters)
ASK_EVERY = 108000            # game ticks between asks for one, per engineer (30 game minutes)
LOG_NAME = "vehicle.jsonl"

_asked: dict[Any, int] = {}   # engineer -> the game tick it last asked for one
_LOCK = threading.Lock()


def _travel(situation: Mapping[str, Any]) -> Mapping[str, Any]:
    t = situation.get("travel") if isinstance(situation, Mapping) else None
    return t if isinstance(t, Mapping) else {}


def judge(situation: Mapping[str, Any]) -> dict:
    """What a vehicle would be worth to it now, and which it could get."""
    travel = _travel(situation)
    walk = float(travel.get("walk_minutes_per_hour") or 0)
    speed = max(WALK_SPEED, float(travel.get("drive_speed") or DRIVE_SPEED))
    saved = walk * (1.0 - WALK_SPEED / speed)
    threats = situation.get("threats") if isinstance(situation.get("threats"), Mapping) else {}
    spawners = int(threats.get("unit-spawner") or 0)
    score = saved + DANGER * spawners
    has = bool(travel.get("vehicle") or travel.get("in_pockets"))
    options = []
    for v in travel.get("can_make") or []:
        if not isinstance(v, Mapping) or not v.get("item"):
            continue
        parts = [p for p in v.get("parts") or [] if isinstance(p, Mapping)]
        short = [{"name": p.get("name"), "need": int(p.get("amount") or 0) - int(p.get("have") or 0)}
                 for p in parts if p.get("by") == "machine" and int(p.get("have") or 0) < int(p.get("amount") or 0)]
        get = GET_MINUTES + PART_MINUTES * sum(int(p.get("amount") or 0) for p in parts)
        options.append({"item": v["item"], "name": v.get("name"), "short": short, "get": round(get, 2)})
    ready = [o for o in options if not o["short"]]
    pick = ready[0] if ready else None   # (sturdiest first, as the game lists them)
    pays = (not has and pick is not None and score >= MIN_SAVED and score * HORIZON >= pick["get"])
    blocked = sorted({s["name"] for o in options for s in o["short"] if s.get("name")}) if not ready else []
    return {"walk_minutes_per_hour": walk, "saved_minutes_per_hour": round(saved, 2), "spawners": spawners,
            "score": round(score, 2), "has": has, "pick": pick, "options": options, "blocked_on": blocked,
            "pays": pays, "trips_per_hour": travel.get("per_hour"), "average_tiles": travel.get("average_tiles")}


def intentions(request: Mapping[str, Any]) -> list[dict]:
    """A vehicle of its own, when one pays and it has not asked lately."""
    situation = request.get("situation") if isinstance(request.get("situation"), Mapping) else {}
    j = judge(situation)
    if not j["pays"]:
        return []
    npc, tick = request.get("npc_id"), int(situation.get("tick") or 0)
    with _LOCK:
        last = _asked.get(npc)
        if last is not None and 0 <= tick - last < ASK_EVERY:
            return []
        _asked[npc] = tick
    pick = j["pick"]
    out = [{"tool": "arm_up", "priority": "next",
            "reason": f"a {pick['name']} of its own: its long walks take {j['walk_minutes_per_hour']:.0f} minutes an "
                      f"hour, driven about {j['saved_minutes_per_hour']:.0f} less",
            "args": {"focus": "vehicle", "vehicle": pick["item"], "ammo": 20}}]
    record(request, j, out)
    return out


def describe(situation: Mapping[str, Any]) -> str:
    """One line for its strategist: its long walks, and what a vehicle would save."""
    j = judge(situation)
    if j["has"] or j["saved_minutes_per_hour"] < 1:
        return ""
    line = (f"Your long walks take {j['walk_minutes_per_hour']:.0f} minutes a game hour "
            f"({j['trips_per_hour'] or 0} trips, {j['average_tiles'] or 0} tiles on average); driven they would take "
            f"about {j['saved_minutes_per_hour']:.0f} less.")
    if j["pick"]:
        return line + f" You can get a {j['pick']['name']} now (arm_up, focus vehicle)."
    if j["blocked_on"]:
        return line + f" A vehicle needs {', '.join(j['blocked_on'])}, which only machines make, and you have too few."
    return line


def with_reflex(request: Mapping[str, Any], decided: list[dict]) -> list[dict]:
    """The strategist's decision, with a vehicle of its own asked for when one pays and neither the decision nor
    its agenda asks for one already: the engineer's own judgment, whatever the model said."""
    if request.get("reason") not in ("routine", "initiative"):
        return decided

    def for_vehicle(i: Any) -> bool:
        return (isinstance(i, Mapping) and i.get("tool") == "arm_up" and isinstance(i.get("args"), Mapping)
                and i["args"].get("focus") == "vehicle")
    if any(for_vehicle(i) for i in decided):
        return decided
    situation = request.get("situation") if isinstance(request.get("situation"), Mapping) else {}
    if any(for_vehicle(i) for i in situation.get("agenda") or []):
        return decided
    return list(decided) + intentions(request)


_LOG_LOCK = threading.Lock()


def record(request: Mapping[str, Any], judged: Mapping[str, Any], out: list[dict]) -> None:
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    situation = request.get("situation") if isinstance(request.get("situation"), Mapping) else {}
    row = {"time": time.time(), "npc": request.get("npc_id"), "request": request.get("request_id"),
           "tick": situation.get("tick"), "travel": situation.get("travel"), "judged": dict(judged),
           "intentions": out}
    path = Path(folder) / "decisions" / LOG_NAME
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
