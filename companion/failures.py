"""One memory of the goals an engineer could not have planned: its planner failed (a PlanError), or declined a defense
goal it chose (D-16). Each goal is held, passed over by every chooser (catalog_planner.available), while nothing has
changed that could mend what it failed on, and no longer than its hold, which doubles with each failure for the same
reason in a row. Every ESCALATE_AFTER such failures its strategist hears of it (the game's `goal_stuck` event), to think
again: another site, another layout, or giving it up.

An urgent power plant whose plan failed the same way each time ("3 buildings outside its plot") was retried about every
20 seconds for about 195 game minutes, crowding out every other goal (the practice world's grows, 2026-10-29); a defense
goal its planner declined kept winning the choice a minute apart (D-16, run normal-7b).

What could mend a failure, by the words of its reason: ground (new patches known, its plot or home moved), research (new
recipes), items (more of something carried or stocked); a reason of none of these, any of them. A defense goal judges by
its own (companion/defense_brain.py: a new attack, a turret lost, a bigger wave, walls made, something built).
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Mapping

FAIL_HOLD = 18000        # ticks a goal is first held after its plan failed (5 game minutes, as the game's own cooldown)
DECLINE_HOLD = 72000     # ...after its planner declined it (20 minutes: defense_brain.DECLINE_MAX)
MAX_HOLD = 216000        # (doubling with each failure for the same reason, up to an hour)
FORGET = 432000          # ticks after which a failure is forgotten altogether (two hours)
ESCALATE_AFTER = 3       # failures for the same reason in a row before its strategist hears of it (and every so many)
KEPT = 512               # failures remembered at most (every world's, the oldest dropped first)
GROUND = ("outside", "plot", "room", "site", "fit", "cliff", "water", "shore", "patch", "ground", "space", "obstacle",
          "reach", "found", "far")
RESEARCH = ("research", "recipe", "unlock", "locked", "tech")
ITEMS = ("item", "stock", "enough", "lack", "missing", "short", "carry", "made")

MEMORY: dict[tuple, dict] = {}
_LOCK = threading.Lock()


def reason_key(reason: str) -> str:
    """A failure's reason as its kind: numbers and the list after a colon dropped ("34 buildings outside its plot:
    defense-1, ..." and "3 buildings outside its plot: ..." are one reason)."""
    text = str(reason or "").split(":", 1)[0] if ":" in str(reason or "") else str(reason or "")
    return re.sub(r"\d+(\.\d+)?", "#", text.lower()).strip()[:80]


def kinds(reason: str) -> set[str]:
    """What could mend a failure of this reason: ground, research, items; any of them when it names none."""
    words = str(reason or "").lower()
    out = {name for name, keys in (("ground", GROUND), ("research", RESEARCH), ("items", ITEMS))
           if any(k in words for k in keys)}
    return out or {"ground", "research", "items"}


def defensive(purpose: str) -> bool:
    """A goal that places defenses (turrets, the wall ring): judged by what the defense brain sees."""
    from companion import goal_brain
    return goal_brain.defense(purpose)


def situation(site: Mapping[str, Any], purpose: str) -> dict:
    """What a failure was judged against: the defense brain's for a defense goal, else its ground, research and
    items."""
    if defensive(purpose):
        from companion import defense_brain
        return {"defense": defense_brain._situation(site)}
    from companion import ratios
    team = site.get("team") if isinstance(site.get("team"), Mapping) else {}
    try:
        recipes = len(ratios.unlocked(site))
    except Exception:
        recipes = 0
    items: dict[str, float] = {}
    held = site.get("inventory") if isinstance(site.get("inventory"), Mapping) else {}
    for name, count in held.items():
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            items[str(name)] = float(count)
    for b in site.get("base") or []:
        stock = b.get("stock") if isinstance(b, Mapping) else None
        for name, count in (stock.items() if isinstance(stock, Mapping) else ()):
            if isinstance(count, (int, float)) and not isinstance(count, bool):
                items[str(name)] = items.get(str(name), 0.0) + float(count)
    patches = sorted(json.dumps([p.get("resource"), p.get("dx", p.get("x")), p.get("dy", p.get("y"))], default=str)
                     for p in site.get("known_patches") or [] if isinstance(p, Mapping))
    return {"patches": patches,
            "plot": json.dumps([team.get("plot"), team.get("no_build")], sort_keys=True, default=str),
            "home": json.dumps(site.get("home"), sort_keys=True, default=str),
            "recipes": recipes, "items": items}


def changed(then: Mapping[str, Any], now: Mapping[str, Any], reason: str) -> bool:
    """Whether something changed since a failure that could mend it."""
    if "defense" in then or "defense" in now:
        from companion import defense_brain
        return not defense_brain.unchanged(then.get("defense") or {}, now.get("defense") or {}, reason)
    what = kinds(reason)
    if "ground" in what and (bool(set(now["patches"]) - set(then["patches"])) or now["plot"] != then["plot"]
                             or now["home"] != then["home"]):
        return True
    if "research" in what and now["recipes"] > then["recipes"]:
        return True
    if "items" in what:
        # (the items its reason names, where it names any: anything else is made and carried all the time)
        words = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(reason).lower()))
        # (whole names only: more stone is not more stone-brick; and item-like names it does not carry yet)
        named = {n for n in set(now["items"]) | set(then["items"]) if n in words}
        named |= {w for w in words if "-" in w}
        if any(now["items"].get(n, 0.0) > then["items"].get(n, 0.0) for n in named or now["items"]):
            return True
    return False


def hold(entry: Mapping[str, Any]) -> int:
    """How long a failure holds its goal: its first hold, doubled for each failure for the same reason before."""
    first = DECLINE_HOLD if entry.get("kind") == "declined" else FAIL_HOLD
    return min(MAX_HOLD, first * 2 ** max(0, int(entry.get("count") or 1) - 1))


def _key(site: Mapping[str, Any], purpose: str) -> tuple:
    return (site.get("world_id"), site.get("npc_id"), purpose)


def note(site: Mapping[str, Any], purpose: str, reason: str, kind: str = "failed") -> int:
    """A goal's plan failed (or was declined) for `reason`: held from now. The failures for the same reason in a row
    so far, this one counted."""
    tick = int(site.get("tick") or 0)
    entry = {"tick": tick, "reason": str(reason or "")[:200], "kind": kind, "situation": situation(site, purpose)}
    key = _key(site, purpose)
    with _LOCK:
        world = key[0]
        for old in [k for k, v in MEMORY.items() if k[0] == world and not 0 <= tick - v["tick"] < FORGET]:
            del MEMORY[old]   # (its world's old ones; ticks are each world's own)
        before = MEMORY.pop(key, None)
        same = before is not None and reason_key(before["reason"]) == reason_key(entry["reason"]) \
            and before["kind"] == kind
        entry["count"] = before["count"] + 1 if same else 1
        MEMORY[key] = entry
        while len(MEMORY) > KEPT:   # (other worlds' too, the oldest written first)
            del MEMORY[next(iter(MEMORY))]
    return entry["count"]


def escalate(count: int) -> bool:
    """Whether this many failures for the same reason in a row go to its strategist."""
    return count >= ESCALATE_AFTER and count % ESCALATE_AFTER == 0


def held(site: Mapping[str, Any], purpose: str) -> dict | None:
    """The failure holding this goal now (nothing changed that could mend it, within its hold), or None."""
    key = _key(site, purpose)
    with _LOCK:
        entry = MEMORY.get(key)
    if entry is None:
        return None
    ago = int(site.get("tick") or 0) - entry["tick"]
    if ago < 0:   # (a save loaded from before it)
        with _LOCK:
            if MEMORY.get(key) is entry:
                del MEMORY[key]
        return None
    if entry.get("released") or ago >= hold(entry):
        return None
    # (failed again and again for one reason, each time after a change let it go: those changes were not its mend; only
    # its hold, doubling, lets it go now. The ammo loop "cannot go under what stands" was let go by every building put up
    # and tried every request; the silo stage never got a turn, W-29)
    if entry.get("kind") == "failed" and int(entry.get("count") or 1) >= ESCALATE_AFTER:   # (a decline: D-20 as ever)
        return entry
    if changed(entry["situation"], situation(site, purpose), entry["reason"]):
        # (released for good, though the change may pass: its count stays for the next failure's hold)
        with _LOCK:
            entry["released"] = True
        return None
    return entry


def released(site: Mapping[str, Any], purpose: str) -> bool:
    """Whether this memory knows the goal's failure and holds it no longer (a change released it, or its hold for
    that reason ran out): the game's own cooldown for it (its failed_purposes, by time and purpose alone, a new
    reason counted as a repeat) gives way, this memory's hold governing."""
    with _LOCK:
        known = _key(site, purpose) in MEMORY
    return known and held(site, purpose) is None


def planned(site: Mapping[str, Any], purpose: str) -> None:
    """A plan for this goal was made: its failures are behind it."""
    with _LOCK:
        MEMORY.pop(_key(site, purpose), None)


_CHOSEN: dict[tuple, str] = {}
_STALLS: dict[tuple, Any] = {}   # (world, npc): the stall noted last (one stall, one goal held)


def chose(site: Mapping[str, Any], goal: str) -> None:
    """The goal its planner chose for a request of its own choosing (the game knows only "choose")."""
    with _LOCK:
        _CHOSEN[(site.get("world_id"), site.get("npc_id"))] = str(goal)
        while len(_CHOSEN) > KEPT:
            del _CHOSEN[next(iter(_CHOSEN))]


def stalled(site: Mapping[str, Any]) -> str | None:
    """Its watchdog found it getting nowhere on a goal (the game's `stalled`, SO-5): that goal noted as failed, once a
    stall, and held as any failure is. The goal, or None."""
    from companion import catalog_planner
    stall = site.get("stalled") if isinstance(site.get("stalled"), Mapping) else None
    goal = str(stall.get("goal")) if stall else ""
    if goal == "choose":   # (on a goal its planner chose: that one)
        with _LOCK:
            goal = _CHOSEN.get((site.get("world_id"), site.get("npc_id")), goal)
    if goal not in catalog_planner.PURPOSES:
        return None
    who = (site.get("world_id"), site.get("npc_id"))
    with _LOCK:
        if _STALLS.get(who) == stall.get("tick"):
            return None   # (noted already: the same stall at a later request is not a second goal's)
        _STALLS[who] = stall.get("tick")
        while len(_STALLS) > KEPT:
            del _STALLS[next(iter(_STALLS))]
    note(site, goal, f"stalled ({stall.get('phase')}, {stall.get('reason')})")
    return goal


def forget() -> None:
    with _LOCK:
        MEMORY.clear()
        _CHOSEN.clear()
        _STALLS.clear()
