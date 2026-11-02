"""Clearing ground ahead of time, so the large sections a factory plan keeps room for fit when their turn
comes (Phase 3C, "future-proof"): the trees and rocks in a zone are mined before the zone is needed, and its
cliffs are blasted once cliff explosives can be made. The engineer pays with its own work, as a player
does, and keeps the wood and stone.

Where. The factory plan names the zones (site["factory_plan"]["zones"]), each
    {"id": str, "box": [x0, y0, x1, y1] in world tiles, "clear": True, "priority": 0..1, "why": str}.
A zone may also come straight from a request (site["order"]["zone"] or site["zone"], the same shape), so
any caller can ask for ground to be cleared; the game remembers it and it is finished later like the rest.

What is in the way is the game's to say (scripts/clearing.lua): anything of the map's own whose collision
layers meet a building's, however a mod names it. The planner never lists trees or rocks.

How. A zone is cleared in slices of SLICE tiles (a grid laid from the zone's corner), nearest home first.
One clearing plan takes the next few slices not known to be clear, with one budget (things cleared) and
one time bound for all of them, so it holds the engineer from other work for a few minutes at most; the
game ends it at once when anything urgent comes up. The game surveys each slice when its stage ends and
reports it in the site (site["clearing"]["zones"]: each surveyed box with what is left there to mine, cliffs
to blast now or once researched, what nothing clears, and what lies outside its own ground). A zone is done
when every slice is known clear: pending_zones() leaves it out, and progress() says so for the factory plan.

When. Only after the opening, never ahead of anything urgent, defensive or military (catalog_planner.py
decides that), and by zone priority: a zone whose section comes next (priority >= FIRST) is cleared before
new goals; the rest only when there is nothing else to build.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import ratios, team_area

PURPOSE = "clear-area"
SLICE = 32              # tiles a side: one stage's box (about a chunk)
MAX_SIDE = 512          # a zone's longest side; bigger ones are cut to this (a factory plan splits them)
STAGES = 6              # slices one plan may take (empty ones pass in a moment)
BUDGET = 50             # things one plan clears (trees, rocks, cliffs): a few minutes of work
TICKS = 4 * 3600        # and the longest one plan may take, walking there included
FIRST = 0.7             # a zone this important comes before new goals: the section planned there is next
BLAST_RETRY = 10 * 3600 # cliffs it could not get a charge for are tried again after this long
REACH = 480             # how far from home a plan's boxes may lie (the game's reach less its window)
TEAM_REACH = 990        # the same in the team world (its bands of the yard lie far out)

Box = tuple[int, int, int, int]


def _box(raw: Any) -> Box | None:
    """Four finite numbers as whole tiles (outward), or None."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    box = (math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        return None
    return box


def zone_of(raw: Any, source: str = "plan") -> dict | None:
    """A zone to clear, checked and in whole world tiles (a side longer than MAX_SIDE is cut short), or None
    when it is not one (no box, or "clear" set false). `source`: "plan" (the factory plan's) or "asked"."""
    if not isinstance(raw, Mapping) or raw.get("clear") is False:
        return None
    source = "asked" if source == "asked" else "plan"
    box = _box(raw.get("box"))
    if box is None:
        return None
    box = (box[0], box[1], min(box[2], box[0] + MAX_SIDE), min(box[3], box[1] + MAX_SIDE))
    try:
        priority = float(raw.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0.0
    priority = min(1.0, max(0.0, priority)) if math.isfinite(priority) else 0.0
    zone_id = str(raw.get("id") or f"zone-{box[0]}-{box[1]}")[:64]
    return {"id": zone_id, "box": box, "priority": priority, "why": str(raw.get("why") or "")[:160],
            "source": source}


def records(site: Mapping[str, Any]) -> dict[str, dict]:
    """What the game reported per zone: id -> {"box", "boxes": {slice box: survey}, ...} (world tiles)."""
    report = site.get("clearing")
    out: dict[str, dict] = {}
    for z in (report.get("zones") if isinstance(report, Mapping) else None) or []:
        if not isinstance(z, Mapping) or not isinstance(z.get("id"), str):
            continue
        boxes = {}
        for b in z.get("boxes") or []:
            box = _box(b.get("box")) if isinstance(b, Mapping) else None
            if box is not None:
                boxes[box] = b
        out[z["id"]] = {**z, "box": _box(z.get("box")), "boxes": boxes}
    return out


def requested(site: Mapping[str, Any]) -> dict | None:
    """A zone asked for directly with this request (its order, or the site itself), or None."""
    order = site.get("order")
    raw = order.get("zone") if isinstance(order, Mapping) else None
    return zone_of(raw if raw is not None else site.get("zone"), source="asked")


def zones(site: Mapping[str, Any]) -> list[dict]:
    """Every zone to clear: the factory plan's, then zones asked for earlier that the game is still
    working on (the plan's own ids win)."""
    plan = site.get("factory_plan")
    out, seen = [], set()
    for raw in (plan.get("zones") if isinstance(plan, Mapping) else None) or []:
        zone = zone_of(raw)
        if zone and zone["id"] not in seen:
            seen.add(zone["id"])
            out.append(zone)
    for zone_id, rec in records(site).items():
        if zone_id in seen or rec.get("source") != "asked" or rec.get("box") is None:
            continue
        zone = zone_of({"id": zone_id, "box": rec["box"], "priority": rec.get("priority"), "why": rec.get("why")},
                       source="asked")
        if zone:
            seen.add(zone_id)
            out.append(zone)
    return out


def _home(site: Mapping[str, Any]) -> tuple[float, float]:
    origin, home = site.get("origin") or {}, site.get("home") or {}
    return (float(origin.get("x", 0)) + float(home.get("x", 32)), float(origin.get("y", 0)) + float(home.get("y", 32)))


def slices(zone: Mapping[str, Any], home: tuple[float, float] | None = None) -> list[Box]:
    """The zone cut into SLICE-tile boxes from its corner (the last row and column as wide as is left), nearest
    `home` first when given."""
    x0, y0, x1, y1 = zone["box"]
    out = [(x, y, min(x + SLICE, x1), min(y + SLICE, y1)) for y in range(y0, y1, SLICE) for x in range(x0, x1, SLICE)]
    if home is not None:
        hx, hy = home
        out.sort(key=lambda b: ((b[0] + b[2]) / 2 - hx) ** 2 + ((b[1] + b[3]) / 2 - hy) ** 2)
    return out


def _its_ground(site: Mapping[str, Any], box: Box) -> bool:
    """Whether any of the box may be its own ground (the team world: its plot, its bands, or past the team
    area). Outside the team world, all of it is."""
    team = team_area.team_of(site)
    if team is None:
        return True
    origin = site.get("origin") or {}
    ox, oy = float(origin.get("x", 0)), float(origin.get("y", 0))
    x0, y0, x1, y1 = box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy   # (the site's rules are local)
    own = [r for r in ((team.plot,) if team.plot else ()) + team.bands]
    if any(x0 < r[2] and x1 > r[0] and y0 < r[3] and y1 > r[1] for r in own):
        return True
    a = team.area
    return not (x0 >= a[0] and y0 >= a[1] and x1 <= a[2] and y1 <= a[3])


def _in_reach(site: Mapping[str, Any], box: Box) -> bool:
    """Whether the game takes a box this far from the site's window (it holds a plan to a reach)."""
    origin = site.get("origin") or {}
    ox, oy = float(origin.get("x", 0)), float(origin.get("y", 0))
    reach = TEAM_REACH if team_area.team_of(site) else REACH
    return (box[0] - ox >= -reach and box[1] - oy >= -reach and box[2] - ox <= 64 + reach
            and box[3] - oy <= 64 + reach)


def _slice_clear(survey: Mapping[str, Any] | None, site: Mapping[str, Any], unlocked: set[str]) -> bool | None:
    """Whether a surveyed slice is clear of all it can clear (None: never surveyed). Cliffs it had no charge
    for count again after BLAST_RETRY; cliffs whose charge was not researched, once it is."""
    if survey is None:
        return None
    if int(survey.get("mine") or 0) > 0:
        return False
    if int(survey.get("blast") or 0) > 0:
        if not survey.get("short"):
            return False
        try:
            waited = int(site.get("tick") or 0) - int(survey.get("tick") or 0)
        except (TypeError, ValueError):
            waited = BLAST_RETRY
        if waited >= BLAST_RETRY:
            return False
    if int(survey.get("later") or 0) > 0 and set(survey.get("explosives") or ()) & unlocked:
        return False
    return True


def _state(site: Mapping[str, Any], zone: Mapping[str, Any], unlocked: set[str] | None = None) -> list[tuple]:
    """(slice, clear?, survey) for each slice it may work on, nearest home first. A record for another box
    under this zone's id (the zone was redrawn) counts for nothing."""
    unlocked = ratios.unlocked(site) if unlocked is None else unlocked
    rec = records(site).get(zone["id"])
    surveyed = rec["boxes"] if rec and rec.get("box") == tuple(zone["box"]) else {}
    out = []
    for box in slices(zone, _home(site)):
        if not _its_ground(site, box) or not _in_reach(site, box):
            continue
        survey = surveyed.get(box)
        out.append((box, _slice_clear(survey, site, unlocked), survey))
    return out


def progress(site: Mapping[str, Any], zone: Mapping[str, Any]) -> dict:
    """How far the zone's clearing is, for the factory plan to mark: slices in all (on its own ground, in
    reach), how many are known clear, what is left where it surveyed, and whether it is done."""
    rows = _state(site, zone)
    left = {k: 0 for k in ("mine", "blast", "later", "fixed", "outside")}
    for _, _, survey in rows:
        for k in left:
            left[k] += int((survey or {}).get(k) or 0)
    clear = sum(1 for _, ok, _ in rows if ok)
    return {"slices": len(rows), "clear": clear, "surveyed": sum(1 for _, ok, _ in rows if ok is not None),
            "left": left, "fraction": round(clear / len(rows), 3) if rows else 1.0, "done": clear == len(rows)}


def done(site: Mapping[str, Any], zone: Mapping[str, Any]) -> bool:
    return progress(site, zone)["done"]


def pending_zones(site: Mapping[str, Any]) -> list[dict]:
    """The zones with ground still to clear, most important first (priority, then nearest home), each with
    its progress()."""
    hx, hy = _home(site)
    out = []
    for zone in zones(site):
        state = progress(site, zone)
        if not state["done"]:
            x0, y0, x1, y1 = zone["box"]
            dx, dy = max(x0 - hx, 0, hx - x1), max(y0 - hy, 0, hy - y1)
            out.append({**zone, "progress": state, "distance": round(math.hypot(dx, dy), 1)})
    out.sort(key=lambda z: (-z["priority"], z["distance"], z["id"]))
    return out


def zone_for(site: Mapping[str, Any]) -> dict | None:
    """The zone a clearing plan for this request works on: the one asked for with it (unless it is done),
    else the most important pending one."""
    asked = requested(site)
    if asked is not None and not done(site, asked):
        return {**asked, "progress": progress(site, asked)}
    pending = pending_zones(site)
    return pending[0] if pending else None


def due(site: Mapping[str, Any]) -> dict | None:
    """The most important pending zone when it is important enough to come before new goals (its section is
    next), else None."""
    zone = zone_for(site)
    return zone if zone is not None and zone["priority"] >= FIRST else None


def describe(zone: Mapping[str, Any]) -> str:
    state = zone.get("progress") or {}
    text = f"{zone['id']}, priority {zone['priority']:.2f}"
    if state:
        text += f", {state.get('clear', 0)} of {state.get('slices', 0)} slices clear"
    return text + (f": {zone['why']}" if zone.get("why") else "")


def plan_clearing(site: Mapping[str, Any], zone: Mapping[str, Any], *, stages: int = STAGES,
                  budget: int = BUDGET, ticks: int = TICKS) -> dict:
    """The plan for the zone's next stage of clearing: up to `stages` slices not known clear, nearest home
    first, sharing one budget and time bound (boxes in the site's local tiles, as every plan's positions).
    Raises ValueError when nothing in the zone is left to clear."""
    zone = zone_of(zone, source=str(zone.get("source") or "plan"))
    if zone is None:
        raise ValueError("not a zone to clear")
    rows = [box for box, ok, _ in _state(site, zone) if not ok][:max(1, stages)]
    if not rows:
        raise ValueError(f"zone {zone['id']} has nothing left to clear")
    origin = site.get("origin") or {}
    ox, oy = int(origin.get("x", 0)), int(origin.get("y", 0))

    def local(b: Box) -> list[int]:
        return [b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy]
    stage_list, before = [], None
    for n, box in enumerate(rows, start=1):
        stage_list.append({"id": f"clear-{n}", "entity_ids": [], "depends_on": [before] if before else [],
                           "manual_supplies": [], "clear": {"box": local(box)},
                           "witnesses": [{"kind": "area_clear"}]})
        before = stage_list[-1]["id"]
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", f"{zone['id']}-{site.get('request_id', 'x')}")
    return {"plan_id": f"{PURPOSE}-{safe}"[:64], "purpose": PURPOSE, "source": "clearing", "entities": [],
            "clearing": {"zone": {"id": zone["id"], "box": local(tuple(zone["box"])), "priority": zone["priority"],
                                  "why": zone.get("why") or "", "source": zone.get("source") or "plan"},
                         "budget": int(budget), "ticks": int(ticks)},
            "stages": stage_list}
