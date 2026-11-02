"""The lane ledger: every belt line tracked on its own (the user, 2026-10-28: "tracking each bus line independently from
one another so the NPC knows what belts have plenty of output, when it needs balancing, and if it needs to expand
the bus"). Bus lanes now; any line the game records (coal trunks, and outpost and feed lines as they are recorded) the
same way.

Per lane (companion/belt_lines.py records them; the game samples how full each runs, mod/scripts/base_memory.lua):
- carry: items a second going on. Measured where the game has sampled it long enough and it is nowhere backed up (a
  moving belt this full carries that share of what it can); a backed-up one carries at least what its design puts on
  (what is taken is less than what comes); else its design.
- capacity: what the belt can carry (its tier, both lanes).
- claims: each tap, branch or consumer on it at its machines' full rate.
- headroom: carry less claims.

Signals:
- "plenty": headroom above a margin;
- "short": claims beyond carry (supply first);
- "balance": belts of one item side by side uneven, one running dry with taps after it beside a well-filled one (a
  balancer, or a lane swap);
- "expand": claims near the belt's capacity (`what` "belt": the next belt), or short while it carries under its capacity
  (`what` "supply": more smelting or drills first; the belt has room).
Belts of one group (a bus plate's belts, balanced at their head and after heavy tapping) are judged together for
"short", "expand" and "plenty"; "balance" is between them.

A new consumer's claim is granted only if the lane's (or group's) headroom covers it, else refused with the reason;
a grant is held for the rest of the plan, so two in one plan cannot take the same spare.

    book = LaneBook.from_site(site, world)      # (the map's lines when given: they have every tile and stand checked)
    book.lanes(item=None, group=None)          # [Lane]
    book.groups(item=None)                     # [Group]: belts of one item side by side
    book.signals()                             # [dict], most pressing first
    book.grant(rate, key, item=..., line=..., group=...)   # Grant(ok, line, headroom, why)
    book.plan_lane(id, item, supply, ...)      # a lane a plan lays now, counted here too
    remember(site)                             # logged (data/decisions/lanes.jsonl) when its signals change
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from companion import belt_lines
from companion.belt_lines import BACKED, BELT_RATE, DRY, FULL, HUNGRY, Line

PLENTY = 0.5           # items a second of headroom that is plenty at least
PLENTY_SHARE = 0.1     # or this share of the belt's capacity, whichever is more
EXPAND_SHARE = 0.85    # claims this share of the belt's capacity: the next belt
LOG_NAME = "lanes.jsonl"


@dataclass
class Lane:
    line: Line
    carry: float
    carry_from: str                  # "measured", "at least" (backed up: it gives more than is taken) or "design"
    capacity: float
    pending: dict[str, float] = field(default_factory=dict)   # granted in this plan, not built yet

    @property
    def id(self) -> str:
        return self.line.id

    @property
    def item(self) -> str:
        return self.line.item

    @property
    def claims(self) -> dict[str, float]:
        """Built claims and this plan's grants; a grant under a key already built adds to it (Codex, L-2)."""
        out = dict(self.line.claims)
        for k, v in self.pending.items():
            out[k] = out.get(k, 0.0) + v
        return out

    @property
    def claimed(self) -> float:
        return sum(self.claims.values())

    @property
    def headroom(self) -> float:
        return self.carry - self.claimed

    def row(self) -> dict:
        return {"id": self.id, "item": self.item, "kind": self.line.kind, "group": self.line.group,
                "carry": round(self.carry, 3), "carry_from": self.carry_from, "capacity": round(self.capacity, 3),
                "claims": round(self.claimed, 3), "headroom": round(self.headroom, 3),
                **({"unread": self.line.unread} if self.line.unread else {})}


@dataclass
class Group:
    """Belts of one item side by side, balanced together: what they carry and claim as one."""
    id: str
    lanes: list[Lane]
    carry: float
    carry_from: str

    @property
    def item(self) -> str:
        return self.lanes[0].item

    @property
    def capacity(self) -> float:
        return sum(lane.capacity for lane in self.lanes)

    @property
    def claimed(self) -> float:
        return sum(lane.claimed for lane in self.lanes)

    @property
    def headroom(self) -> float:
        return self.carry - self.claimed


@dataclass
class Grant:
    ok: bool
    line: str | None        # the lane (or group) the claim was put on, or the one that came closest
    headroom: float         # what it had before this claim
    why: str


QUEUE = 1.25   # a reading this far above what its design puts on is a slow queue, not flow


def _carry(line: Line) -> tuple[float, str]:
    """What a lane carries, and how that is known. How full a belt runs gives its flow only where it moves freely: a
    full belt reads the same moving or jammed, and a slow queue reads full while little moves (a 1/s coal trunk read
    49% full in a short run, 2026-10-29). So a reading is taken as flow only while the belt is nowhere backed up and
    reads no more than its design puts on (by QUEUE); it can then only show less going on (drills run out, unfuelled
    or broken). Backed up or queued, it gives at least its design (what is taken is less than what comes), or, with
    no design to go by (a lane fed from outside), an unknown amount: nothing is granted from it."""
    if line.supply <= 0:   # (no design: how full it runs cannot tell flow from a queue, Codex, L-2)
        return sum(line.claims.values()), "unknown"
    if line.measured:
        flowing = line.flowing
        if flowing is not None and flowing <= QUEUE * line.supply:
            return min(flowing, line.supply), "measured"
        return line.supply, "at least"
    return line.supply, "design"


SIDES = ("left", "right")


def side(line: Line, which: str) -> Line:
    """One side of a belt that carries an item a side, as a line of its own (the user, 2026-10-29: "2 different items
    on them, one on the right side and one on the left"): its item, what goes on it, its claims (keyed "...#side"),
    its lane's own measured fill, half the belt's rate. Its id is the line's with "#left" or "#right"."""
    claims = {k: v for k, v in line.claims.items() if k.endswith("#" + which)}
    out = Line(f"{line.id}#{which}", (line.sides or {})[which], line.tiles,
               float((line.side_supply or {}).get(which, 0.0)), claims, kind=line.kind, group=None,
               minutes=line.minutes, rate=(line.rate / 2) if line.rate else None)
    i = SIDES.index(which)
    for at, lanes in line.side_fill.items():
        v = lanes[i]
        if v is not None:
            out.fill[at] = (v, None, v, None)   # (one lane alone: its fill is its fuller lane's)
    return out


class LaneBook:
    def __init__(self, found: Iterable[Line], starved: Mapping[str, float] | None = None):
        self._lanes: dict[str, Lane] = {}
        for line in found:
            # A belt with an item a side is two lanes, each judged and granted on its own; a one-item belt is one.
            for one in ([side(line, s) for s in SIDES if s in line.sides] if line.sides else [line]):
                carry, how = _carry(one)
                self._lanes[one.id] = Lane(one, carry, how, float(one.rate or BELT_RATE / (2 if line.sides else 1)))
        self.starved = dict(starved or {})
        self.grants: list[dict] = []

    @classmethod
    def from_site(cls, site: Mapping[str, Any], world: Mapping[str, Any] | None = None) -> "LaneBook":
        """The lanes of the map export when given (every tile, and whether each still stands), else of the site's."""
        source = world if world is not None and world.get("belt_lines") is not None else site
        return cls(belt_lines.lines(source), _starved(site))

    # --- what it holds ------------------------------------------------------
    def lanes(self, item: str | None = None, group: str | None = None) -> list[Lane]:
        return [lane for lane in self._lanes.values()
                if (item is None or lane.item == item) and (group is None or lane.line.group == group)]

    def lane(self, line_id: str) -> Lane | None:
        return self._lanes.get(line_id)

    def groups(self, item: str | None = None) -> list[Group]:
        """Belts of one item side by side (a lane alone is not a group)."""
        by: dict[str, list[Lane]] = {}
        for lane in self.lanes(item):
            if lane.line.group:
                by.setdefault(lane.line.group, []).append(lane)
        return [self._group(gid, lanes) for gid, lanes in sorted(by.items()) if len(lanes) > 1]

    def group(self, group_id: str) -> Group | None:
        lanes = self.lanes(group=group_id)
        return self._group(group_id, lanes) if lanes else None

    @staticmethod
    def _group(gid: str, lanes: list[Lane]) -> Group:
        """A group's carry: measured as the most its belts carry together at one row (each belt's own busiest row
        would count what a balancer moved from one to another twice: Codex, L-1), at least its design when any belt
        backs up, else its design."""
        lines = [lane.line for lane in lanes]
        design = sum(lane.line.supply for lane in lanes)
        if design <= 0:
            return Group(gid, lanes, sum(lane.claimed for lane in lanes), "unknown")
        if all(line.measured for line in lines):
            rows = _common_rows(lines)
            moving = not any(line.backed_up for line in lines)
            carry = max((sum(_at_row(line, r)[0] * float(line.rate) for line in lines) for r in rows), default=None)
            if moving and carry is not None and carry <= QUEUE * design:
                return Group(gid, lanes, min(carry, design), "measured")
            return Group(gid, lanes, design, "at least")
        return Group(gid, lanes, design, "design")

    def plan_lane(self, line_id: str, item: str, supply: float, *, capacity: float = BELT_RATE,
                  group: str | None = None, claims: Mapping[str, float] | None = None,
                  sides: Mapping[str, str] | None = None, side_supply: Mapping[str, float] | None = None) -> Lane:
        """A lane the plan lays now: counted here with the built ones, by its design, until the game records it. With
        `sides` ({"left": item, "right": item}) a belt with an item a side: a lane each ("<id>#left", "<id>#right"),
        each half the belt, what goes on each `side_supply`; the first is returned."""
        line = Line(line_id, item, [], float(supply), dict(claims or {}), kind="planned", group=group,
                    sides=dict(sides) if sides else None, side_supply=dict(side_supply or {}) if sides else None)
        made = []
        for one in ([side(line, s_) for s_ in SIDES if s_ in line.sides] if line.sides else [line]):
            lane = Lane(one, float(one.supply), "design", float(capacity) / (2 if line.sides else 1))
            self._lanes[one.id] = lane
            made.append(lane)
        return made[0]

    # --- claims ---------------------------------------------------------------
    def grant(self, rate: float, key: str, *, item: str | None = None, line: str | None = None,
              group: str | None = None, reach: Iterable[str] | None = None, side: str | None = None,
              kinds: Iterable[str] = ("trunk", "planned")) -> Grant:
        """A claim of `rate` items a second for consumer `key`: on lane `line`, on group `group` (of its belts in
        `reach`, those the consumer can tap, e.g. the outer belt on its side; all when not given, the one with the most
        headroom takes it), or on the lane of `item` (among `kinds`) with the most headroom. Granted only if that belt,
        and its group with it, has the headroom; refused with the reason. A grant is held for the rest of the plan."""
        rate = float(rate)
        if line is not None and side is not None:   # (one side of a belt with an item a side; its key says which)
            if f"{line}#{side}" in self._lanes:
                line, key = f"{line}#{side}", f"{key}#{side}"
        if line is not None:
            lane = self._lanes.get(line)
            if lane is None:
                return Grant(False, None, 0.0, f"no lane {line}")
            return self._take(lane, lane.headroom, rate, key, f"{lane.item} lane {lane.id}")
        if group is not None:
            # The claim goes on one belt: the one a tap takes it from must have the headroom itself (the group's total
            # would overbook the belt tapped: Codex, L-2), and the belt is named.
            g = self.group(group)
            if g is None:
                return Grant(False, None, 0.0, f"no group {group}")
            near = [lane for lane in g.lanes if reach is None or lane.id in set(reach)]
            if not near:
                return Grant(False, None, 0.0, f"refused: none of {g.item} belts {g.id} is within reach")
            best = max(near, key=lambda lane: lane.headroom)
            if best.headroom + 1e-9 < rate <= g.headroom + 1e-9:
                why = (f"refused: {g.item} belts {g.id} have {g.headroom:.2f}/s left together but no belt within "
                       f"reach {rate:.2f}/s alone (the most is {best.headroom:.2f}/s on {best.id}): a balancer first")
                self.grants.append({"key": key, "rate": round(rate, 4), "line": best.id, "ok": False, "why": why})
                return Grant(False, best.id, best.headroom, why)
            return self._take(best, best.headroom, rate, key, f"{best.item} lane {best.id}")
        found = [lane for lane in self.lanes(item)
                 if lane.line.kind in set(kinds) and lane.carry_from != "unknown"]
        if not found:
            return Grant(False, None, 0.0, f"no lane carries {item}")
        best = max(found, key=lambda lane: lane.headroom)
        return self._take(best, best.headroom, rate, key, f"{best.item} lane {best.id}")

    def _take(self, lane: Lane, headroom: float, rate: float, key: str, what: str) -> Grant:
        ok = headroom + 1e-9 >= rate and lane.carry_from != "unknown"
        why = (f"{what} carries {lane.carry:.2f}/s ({lane.carry_from}), claimed {lane.claimed:.2f}/s: "
               f"{headroom:.2f}/s left, {rate:.2f}/s asked")
        if lane.carry_from == "unknown":
            why = f"{what}: how much it carries is not known yet (no design to go by, and how full it runs is no flow)"
        # A belt of a group shares what goes on with the others (a balancer moves it between them): what the group
        # carries together bounds every grant on any of its belts (two belts each reading 6 a second may be one 6 moved
        # across by a balancer: Codex, L-2).
        g = self.group(lane.line.group) if lane.line.group else None
        if ok and g is not None and len(g.lanes) > 1 and g.headroom + 1e-9 < rate:
            ok = False
            why = (f"{what} has {headroom:.2f}/s left on its own, but its belts {g.id} carry {g.carry:.2f}/s together "
                   f"({g.carry_from}) with {g.claimed:.2f}/s claimed: {g.headroom:.2f}/s left, {rate:.2f}/s asked")
        if ok:
            lane.pending[key] = lane.pending.get(key, 0.0) + rate
        self.grants.append({"key": key, "rate": round(rate, 4), "line": lane.id, "ok": ok, "why": why})
        return Grant(ok, lane.id, headroom, ("granted: " if ok else "refused: ") + why)

    # --- signals --------------------------------------------------------------
    def signals(self) -> list[dict]:
        """Every lane's (or group's) signal, most pressing first: balance, short, expand, then plenty."""
        out: list[dict] = []
        grouped = {lane.id for g in self.groups() for lane in g.lanes}
        for g in self.groups():
            found = self._balance(g)
            unread = sum(lane.line.unread for lane in g.lanes)
            if unread:   # (taps with no probe before them: where they run dry is not seen; said, not hidden)
                for s in found or []:
                    s["unread"] = unread
                if not found:
                    out.append({"signal": "balance", "group": g.id, "item": g.item, "unread": unread, "what": "unseen",
                                "why": f"{g.item} belts {g.id}: {unread} taps have no probe before them, so whether "
                                       f"they run dry there is not known"})
            out += found
            out += _judge(g.id, g.item, g.carry, g.carry_from, g.capacity, g.claimed, self._hungry(g.lanes),
                          f"{g.item} belts {g.id} ({len(g.lanes)})", group=True)
        for lane in self._lanes.values():
            if lane.id in grouped:
                continue
            out += _judge(lane.id, lane.item, lane.carry, lane.carry_from, lane.capacity, lane.claimed,
                          self._hungry([lane]), f"{lane.item} lane {lane.id}")
        order = {"balance": 0, "short": 1, "expand": 2, "plenty": 3}
        return sorted(out, key=lambda s: (order[s["signal"]], -abs(s.get("headroom", 0.0))))

    def _hungry(self, lanes: list[Lane]) -> list[str]:
        """Modules tapping these lanes measurably short of inputs (evidence named in a signal's reason)."""
        names = {_tap(k)[0] for lane in lanes for k in lane.line.claims}
        return sorted(m for m in names if self.starved.get(m, 0.0) >= HUNGRY)

    def _balance(self, g: Group) -> list[dict]:
        """A belt of the group runs nearly empty beside a well-filled one with taps after it, before it fills again
        (a balancer already there that refills it before its next tap leaves nothing to do: Codex, L-1). Every dry
        belt is looked at, not only the driest (an untapped empty inner belt must not hide a tapped dry outer one).
        The first such place from the head; `fed_row` is where the dry belt last carried."""
        lines = [lane.line for lane in g.lanes]
        if not all(line.measured for line in lines):
            return []
        rows = _common_rows(lines)
        if not rows:
            return []
        fills = {line.id: {r: _at_row(line, r) for r in rows} for line in lines}
        found = None
        for dry in lines:
            fed_row = dry.tiles[0][1]
            for i, row in enumerate(rows):
                f, _ = fills[dry.id][row]
                if f >= DRY:
                    fed_row = row
                    continue
                full = max((o for o in lines if o is not dry), key=lambda o: fills[o.id][row][1])
                if fills[full.id][row][1] < FULL:
                    continue
                recover = next((r for r in rows[i + 1:] if fills[dry.id][r][0] >= DRY), math.inf)
                hit = [k for k in dry.claims if _tap(k)[1] is None or row < _tap(k)[1] < recover]
                if hit:
                    if found is None or row < found[0]:
                        found = (row, fed_row, dry, full, hit)
                    break
                # (a dry stretch that starves no tap: look on past it, it may run dry again further down)
        if found is None:
            return []
        row, fed_row, dry, full, hit = found
        hungry = [m for m in self._hungry([lane for lane in g.lanes if lane.line is dry])
                  if any(_tap(k)[0] == m for k in hit)]
        why = (f"{dry.item} belt at x={dry.tiles[0][0]} runs nearly empty by row {row} "
               f"({fills[dry.id][row][0]:.0%} full) with {len(hit)} taps after it, while the belt at "
               f"x={full.tiles[0][0]} runs {fills[full.id][row][1]:.0%} full on its fuller lane")
        if hungry:
            why += "; short of it: " + ", ".join(f"{m} ({self.starved[m]:.0%} of the time)" for m in hungry[:3])
        return [{"signal": "balance", "group": g.id, "item": dry.item, "lines": [line.id for line in lines],
                 "dry": dry.id, "dry_at": [dry.tiles[0][0], row], "dry_row": row, "fed_row": fed_row,
                 "fill": {line.id: round(fills[line.id][row][0], 3) for line in lines}, "why": why}]


def _judge(where: str, item: str, carry: float, how: str, capacity: float, claimed: float, hungry: list[str],
           what: str, group: bool = False) -> list[dict]:
    """A lane's (or group's) signals from its numbers."""
    base = {"item": item, ("group" if group else "line"): where, "carry": round(carry, 3), "carry_from": how,
            "capacity": round(capacity, 3), "claims": round(claimed, 3), "headroom": round(carry - claimed, 3)}
    evidence = f"; short of it: {', '.join(hungry[:3])}" if hungry else ""
    out: list[dict] = []
    if how == "unknown":
        return out
    # ("at least" is a floor: claims past it are short only where a consumer measurably goes without, Codex, L-2)
    if claimed > carry + 1e-6 and (how != "at least" or hungry):
        out.append({**base, "signal": "short", "why": f"{what} carries {carry:.2f}/s ({how}) while its claims want "
                                                      f"{claimed:.2f}/s{evidence}: supply first"})
        if carry < EXPAND_SHARE * capacity:
            out.append({**base, "signal": "expand", "what": "supply",
                        "why": f"{what} carries {carry:.2f}/s of the {capacity:.1f}/s the belt holds: more supply "
                               f"(smelting or drills) before another belt"})
    # (the next belt only once supply is not what is short: both at once read as two jobs in conflict, and a floor
    # under its claims is no sign the supply is there; Codex, L-2)
    if claimed >= EXPAND_SHARE * capacity and not out and how == "at least" and carry < claimed:
        out.append({**base, "signal": "expand", "what": "supply",
                    "why": f"{what}: claims {claimed:.2f}/s, {claimed / capacity:.0%} of what the belt holds, but only "
                           f"{carry:.2f}/s is known to go on: more supply (smelting or drills) before another belt"})
    if claimed >= EXPAND_SHARE * capacity and not out:
        out.append({**base, "signal": "expand", "what": "belt",
                    "why": f"{what}: claims {claimed:.2f}/s, {claimed / capacity:.0%} of what the belt holds "
                           f"({capacity:.1f}/s): the next belt"})
    if not out and carry - claimed >= max(PLENTY, PLENTY_SHARE * capacity):
        out.append({**base, "signal": "plenty", "why": f"{what} has {carry - claimed:.2f}/s to spare ({how})"})
    return out


def _common_rows(lines: list[Line]) -> list[int]:
    """Probe rows every belt of a group has measured (a bus runs south: rows line up across its belts)."""
    sets = [{at[1] for at in line.fill} for line in lines]
    return sorted(set.intersection(*sets)) if sets else []


def _at_row(line: Line, row: int) -> tuple[float, float]:
    """(both lanes' 30-minute fill, its fuller lane's) at a probe row."""
    for at, v in line.fill.items():
        if at[1] == row:
            return v[0], v[2]
    return 0.0, 0.0


def _tap(key: str) -> tuple[str, int | None]:
    """A bus tap's module and the row of its splitter, from its claim's key ("module@x,y")."""
    module, _, at = key.rpartition("@")
    try:
        return module, int(at.split(",")[1])
    except (IndexError, ValueError):
        return key, None


def _starved(site: Mapping[str, Any]) -> dict[str, float]:
    """Bus modules by name and the share of time their machines measurably stood short of inputs."""
    from companion import ledger
    out = {}
    for block in site.get("base") or []:
        if isinstance(block, Mapping) and str(block.get("stage") or "").startswith("bus-"):
            share, measured = ledger.busy_share(block, "starved")
            if measured:
                out[str(block["stage"])[4:]] = share
    return out


# The latest signals of each engineer: logged when they change, and a line for its strategist.
_latest: dict[int, list[dict]] = {}
_shape: dict[int, list] = {}
_lock = threading.Lock()


def remember(site: Mapping[str, Any], world: Mapping[str, Any] | None = None) -> LaneBook | None:
    """The engineer's lanes as the site (or map) shows them: their signals logged (data/decisions/lanes.jsonl) when
    they changed since its last request, for weights and thresholds to be learned from how the factory went on."""
    npc = site.get("npc_id")
    if not isinstance(npc, int):
        return None
    book = LaneBook.from_site(site, world)
    signals = book.signals() if book.lanes() else []
    with _lock:
        before = _shape.get(npc)
        _latest[npc] = signals
        _shape[npc] = shape = _shape_of(signals)
        changed = signals and shape != before
        if changed:   # (written under the lock: two requests at once log in the order they were judged)
            _log(site, book, signals)
    return book


def _shape_of(signals: list[dict]) -> list:
    """What a log line is kept for: each signal, its lane and its numbers rounded to a tenth."""
    return [(s["signal"], s.get("line") or s.get("group"), s.get("what"), s.get("dry"), s.get("dry_row"),
             s.get("unread"), s.get("fed_row"), *(round(float(s.get(k) or 0.0), 1) for k in ("carry", "claims")))
            for s in signals]


def note_for(npc: Any) -> str | None:
    """One line for the strategist when a lane is short, uneven or full; None otherwise."""
    with _lock:
        signals = list(_latest.get(npc) or ()) if isinstance(npc, int) else []
    pressing = [s for s in signals if s["signal"] != "plenty" and s.get("what") != "unseen"]   # (the planners')
    return " | ".join(s["why"] for s in pressing[:2]) or None


def forget() -> None:
    with _lock:
        _latest.clear()
        _shape.clear()


def _log(site: Mapping[str, Any], book: LaneBook, signals: list[dict]) -> None:
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    row = {"time": time.time(), "tick": site.get("tick"), "npc": site.get("npc_id"), "request": site.get("request_id"),
           "lanes": [lane.row() for lane in book.lanes()][:64], "signals": signals[:32]}
    path = Path(folder) / "decisions" / LOG_NAME
    try:   # (called under _lock)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
