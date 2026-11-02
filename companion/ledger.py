"""The factory ledger: an engineer's base as segments, with their inputs, outputs and real rates.

The game reports each block it built (mod/scripts/base_memory.lua): what its machines could make at
full speed (`capacity`, from their recipes, speeds, modules and productivity), what they really made
lately (`measured`: sampled about once a minute over the last 5 and 30 minutes, with how the machines
spent their time) and the plan's own `ledger` block, which `intent` adds to every plan here: what kind
of segment it is, how busy it is expected to be and, where the planner knows them, its ports.

From those, each item's supply is weighed against what the segments using it claim, on average (a
segment's full-speed use times how busy it is, or what it measurably used) and at peak (everything
flat out). Players plan the same way: a mall that can take 20 iron a second but stops when its
chests are full uses little on average, science lines nearly all the time.

    book = Ledger.from_site(site)
    book.budget("iron-plate")                 # supply, average and peak claims, headroom
    book.fits({"iron-plate": 20}, load=0.15)  # (ok, why not)
    book.fits_ranked(...)                     # the same by priority (companion/priorities.py: fits)
    book.summary()                            # what is short, for the strategist (None if nothing)
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

AVERAGE_LIMIT = 0.85     # a new segment fits when all claims together stay under this share of supply
MATURE_MINUTES = 30      # measured that long, a block's real numbers replace the expected ones
BUSY = 0.9               # measured share of time working that makes a producer flat out
STARVED = 0.3            # measured share of time short of inputs that makes a consumer starved
ARM_BUSY = 0.75          # its inserters on a side working this share of the time: they are what holds it up
ARM_IDLE = 0.1           # and waiting for items (or room) no more than this: they have no time to spare
BUFFER_SECONDS = 300     # belts and chests cover a peak above supply for about this long
# What a burner burns at full work when the game's facts do not say (coal holds 4 MJ): the fuel its burners
# claim is demand like any ingredient, and what nothing makes is carried by hand (a run spent most of its
# time mining coal by hand for forty burners the ledger never counted, 2026-10-25).
FUEL_DRAW = {"burner-mining-drill": 0.15 / 4, "stone-furnace": 0.09 / 4, "steel-furnace": 0.09 / 4,
             "boiler": 1.8 / 4, "burner-inserter": 0.1 / 4}

# How busy a kind of segment is expected to be until it is measured; kinds not listed follow their
# consumers (smelting and intermediates make what the rest of the base draws).
LOADS = {"science": 0.95, "labs": 0.95, "rocket": 0.9, "mall": 0.15, "ammo": 0.3, "defense": 0.1}
# Left out of the budgets: a block built for someone else (a production block at a player's base takes
# from their bus), and an upgrade (once done, the block it upgraded holds the new machines).
OUTSIDE = {"order", "upgrade"}

# A bus stage's name says what it is: its smelting, the plate supply of a growth section, a lane.
_STAGES = (("smelting-", "smelting"), ("supply-", "smelting"), ("lane-", "bus"), ("power", "power"))
_KINDS = (  # first match wins: (kind, words in the plan's stage or purpose)
    ("mall", ("mall",)),
    ("rocket", ("rocket",)),
    ("labs", ("labs", "research-lab")),
    ("science", ("science",)),
    ("ammo", ("ammo", "magazine", "grenade", "armory")),
    ("defense", ("wall", "turret", "defense", "breach")),
    ("power", ("power", "steam", "boiler")),
    ("smelting", ("smelting", "smelt-", "steel")),
    ("mining", ("mining", "coal-loop", "coal-supply", "stone-supply", "oil-gathering")),
    ("upgrade", ("upgrade",)),
    ("bus", ("plate-belts",)),
)


def kind_of(purpose: Any, stage: Any = None) -> str:
    """What kind of segment a plan builds, from its purpose and (a bus stage) its stage name."""
    if purpose == "module":
        return "order"
    if purpose == "upgrade":
        return "upgrade"
    for prefix, kind in _STAGES:
        if str(stage or "").startswith(prefix):
            return kind
    words = f"{stage or ''} {purpose or ''}".lower()
    for kind, marks in _KINDS:
        if any(m in words for m in marks):
            return kind
    return "intermediate"


def intent(plan: Mapping[str, Any], site: Mapping[str, Any] | None = None) -> dict:
    """The ledger block a plan carries into the game: its kind and expected load (a bus module's
    planner adds its ports and rates)."""
    stage = (plan.get("base_plan") or {}).get("stage") if isinstance(plan.get("base_plan"), Mapping) else None
    kind = kind_of(plan.get("purpose") or (site or {}).get("purpose"), stage)
    out: dict[str, Any] = {"kind": kind}
    if kind in LOADS:
        out["load"] = LOADS[kind]
    return out


def _rates(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            rate = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(rate) and rate > 0:
            out[str(k)] = rate
    return out


@dataclass
class Segment:
    id: str
    kind: str
    makes: dict[str, float]           # net items/s at full speed (what it makes and uses itself cancels)
    takes: dict[str, float]
    expected: float | None            # how busy it is expected to be (None: as busy as its consumers make it)
    minutes: int = 0                  # how long it has been measured
    shares: dict[str, float] = field(default_factory=dict)   # measured: working, starved, backed_up...
    made: dict[str, float] = field(default_factory=dict)     # measured items/s out (last 30 minutes)
    used: dict[str, float] = field(default_factory=dict)     # measured items/s in
    peak: dict[str, float] = field(default_factory=dict)     # measured: the busiest 5 minutes lately
    stage: str | None = None
    purpose: str | None = None
    ports: list = field(default_factory=list)
    load: float | None = None         # worked out by the ledger: how busy it is (expected or measured)
    arms: dict[str, float] = field(default_factory=dict)     # measured: its inserters' time by side ("in:working")

    @property
    def mature(self) -> bool:
        return self.minutes >= MATURE_MINUTES and bool(self.shares)

    @property
    def name(self) -> str:
        return self.stage or self.purpose or self.id

    def share(self, key: str) -> float:
        return float(self.shares.get(key, 0.0))

    def supply(self, item: str) -> float:
        """What it can give of `item`: full speed, less the time it measurably could not run for want of
        inputs, power or fuel (time backed up is spare: its output was not wanted)."""
        rate = self.makes.get(item, 0.0)
        if self.mature:
            rate *= max(0.0, 1 - self.share("starved") - self.share("no_power") - self.share("no_fuel"))
        return rate

    @classmethod
    def from_block(cls, block: Mapping[str, Any], facts: Any = None) -> "Segment":
        capacity = block.get("capacity") if isinstance(block.get("capacity"), Mapping) else {}
        plan = block.get("ledger") if isinstance(block.get("ledger"), Mapping) else {}
        measured = block.get("measured") if isinstance(block.get("measured"), Mapping) else {}
        gross_makes, gross_takes = _rates(capacity.get("makes")), _rates(capacity.get("takes"))
        # the plan's own rates where the machines say nothing yet (a furnace that has not smelted)
        for item, rate in _rates(plan.get("makes")).items():
            gross_makes.setdefault(item, rate)
        for item, rate in _rates(plan.get("takes")).items():
            gross_takes.setdefault(item, rate)
        for item, rate in fuel_takes(block, facts).items():   # (what its burners burn: a claim too)
            gross_takes[item] = gross_takes.get(item, 0.0) + rate
        makes, takes = _net(gross_makes, gross_takes)
        made, used = _net(_rates(measured.get("out")), _rates(measured.get("takes")))
        stage, purpose = block.get("stage"), block.get("purpose")
        kind = str(plan.get("kind") or kind_of(purpose, stage))
        load = plan.get("load")
        expected = float(load) if isinstance(load, (int, float)) else LOADS.get(kind)
        return cls(id=str(block.get("id")), kind=kind, makes=makes, takes=takes, expected=expected,
                   minutes=int(measured.get("minutes") or 0), shares=_rates(measured.get("shares")),
                   made=made, used=used, peak=_rates(measured.get("peak")), stage=stage, purpose=purpose,
                   ports=list(plan.get("ports") or []), arms=_rates(measured.get("arms")))


def fuel_takes(block: Mapping[str, Any], facts: Any = None) -> dict[str, float]:
    """Fuel a block's burner machines burn a second at full work (the game's facts, else FUEL_DRAW)."""
    from companion import rates
    out: dict[str, float] = {}
    machines = block.get("machines") if isinstance(block.get("machines"), Mapping) else {}
    for name, n in machines.items():
        rate = None
        if facts is not None:
            try:
                rate = facts.fuel_per_second(str(name))
            except Exception:
                rate = None
        if rate is None:
            rate = FUEL_DRAW.get(str(name))
        if rate and isinstance(n, (int, float)) and n > 0:
            out[rates.FUEL] = out.get(rates.FUEL, 0.0) + float(rate) * float(n)
    return out


def _net(makes: Mapping[str, float], takes: Mapping[str, float]) -> tuple[dict, dict]:
    """A segment's flows with what it makes and uses itself cancelled. An item it makes but uses more of
    (furnaces that could smelt more ore than their own drills mine) is not a claim on the rest of the
    factory: the segment runs on its own supply, and that is its limit (live check 2026-10-23: burner
    smelting blocks read as short of iron ore nobody else makes)."""
    out, into = {}, {}
    for item in set(makes) | set(takes):
        net = makes.get(item, 0.0) - takes.get(item, 0.0)
        if net > 1e-6:
            out[item] = net
        elif net < -1e-6 and item not in makes:
            into[item] = -net
    return out, into


@dataclass
class Budget:
    item: str
    supply: float      # items/s the producers can give
    average: float     # items/s the consumers claim on average
    peak: float        # items/s they claim all flat out
    made: float = 0.0  # measured items/s out of the producers
    used: float = 0.0  # measured items/s into the consumers

    @property
    def share(self) -> float:
        return self.average / self.supply if self.supply > 0 else (math.inf if self.average > 0 else 0.0)

    @property
    def headroom(self) -> float:
        return self.supply - self.average

    def line(self) -> str:
        if self.supply <= 0:
            return f"{self.item}: {self.average:.3g} a second used on average, and none of its blocks make it"
        return (f"{self.item}: {self.average:.3g} of {self.supply:.3g} a second used on average "
                f"({self.share:.0%}), {self.peak:.3g} at peak")


class Ledger:
    def __init__(self, segments: Iterable[Segment]):
        self.segments = [s for s in segments if s.kind not in OUTSIDE]
        self._settle()

    @classmethod
    def from_site(cls, site: Mapping[str, Any]) -> "Ledger":
        blocks = site.get("base") if isinstance(site.get("base"), list) else []
        from companion import rates
        facts = rates.of(site)
        return cls(Segment.from_block(b, facts) for b in blocks if isinstance(b, Mapping))

    def producers(self, item: str) -> list[Segment]:
        return [s for s in self.segments if item in s.makes]

    def consumers(self, item: str) -> list[Segment]:
        return [s for s in self.segments if item in s.takes]

    def items(self) -> list[str]:
        return sorted({i for s in self.segments for i in (*s.makes, *s.takes)})

    def _claim(self, s: Segment, item: str) -> float:
        """A consumer's average draw of `item`: what it measurably used, else full speed times its load."""
        if s.mature and item in s.used:
            return s.used[item]
        return s.takes[item] * (s.load if s.load is not None else 0.0)

    def _settle(self) -> None:
        """How busy each segment is: measured, else expected; one that follows its consumers (smelting, an
        intermediate) runs as hard as they draw on what it makes, worked out from the far end inwards."""
        for s in self.segments:
            s.load = s.share("working") if s.mature else s.expected
        followers = [s for s in self.segments if s.load is None]
        for _ in range(6):   # chains are short: science <- circuits <- cable <- plates
            for s in followers:
                wanted = 0.0
                for item in s.makes:
                    supply = sum(p.makes[item] for p in self.producers(item))
                    claims = sum(self._claim(c, item) for c in self.consumers(item))
                    wanted = max(wanted, min(1.0, claims / supply) if supply > 0 else 0.0)
                s.load = wanted

    def budget(self, item: str) -> Budget:
        return Budget(item, supply=sum(p.supply(item) for p in self.producers(item)),
                      average=sum(self._claim(c, item) for c in self.consumers(item)),
                      peak=sum(c.takes[item] for c in self.consumers(item)),
                      made=sum(p.made.get(item, 0.0) for p in self.producers(item)),
                      used=sum(c.used.get(item, 0.0) for c in self.consumers(item)))

    def fits(self, takes: Mapping[str, float], load: float = 1.0,
             buffers: Mapping[str, float] | None = None) -> tuple[bool, list[str]]:
        """Whether a new segment taking `takes` (items/s at full speed), busy `load` of the time, fits what
        the base makes: on average all claims stay under AVERAGE_LIMIT of supply. Peaks above supply are
        noted, not refused: belts and chests (`buffers`: items held) cover them for a while."""
        ok, notes = True, []
        for item, rate in takes.items():
            b = self.budget(item)
            if b.supply <= 0:
                ok = False
                notes.append(f"nothing makes {item}")
                continue
            average = b.average + rate * load
            if average > AVERAGE_LIMIT * b.supply:
                ok = False
                notes.append(f"{item}: {average:.3g} of {b.supply:.3g} a second on average would be "
                             f"{average / b.supply:.0%} (limit {AVERAGE_LIMIT:.0%})")
            over = b.peak + rate - b.supply - (buffers or {}).get(item, 0.0) / BUFFER_SECONDS
            if over > 0:
                notes.append(f"{item}: {over:.3g} a second over supply when everything runs flat out")
        return ok, notes

    def fits_ranked(self, takes: Mapping[str, float], load: float = 1.0, *, weight: float = 1.0,
                    rank: Callable[[Segment], float] | None = None,
                    buffers: Mapping[str, float] | None = None) -> tuple[bool, list[str]]:
        """fits() for a new segment of priority `weight` (0 to 1), `rank(segment)` giving each built one's
        (companion/priorities.py; priorities.fits works both out). A segment that matters less than the
        new one yields to it: its claim counts only in proportion (rank / weight), as a player lets a mall
        run dry while ammunition is made, and the notes say who would yield. With no rank, or nothing less
        important, the same as fits()."""
        if rank is None:
            return self.fits(takes, load, buffers)
        ok, notes = True, []
        for item, rate in takes.items():
            b = self.budget(item)
            if b.supply <= 0:
                ok = False
                notes.append(f"nothing makes {item}")
                continue
            average, yielding = rate * load, []
            for c in self.consumers(item):
                r, claim = rank(c), self._claim(c, item)
                if weight > 0 and r < weight and claim > 0:
                    average += claim * max(0.0, r) / weight
                    yielding.append(c.name)
                else:
                    average += claim
            if average > AVERAGE_LIMIT * b.supply:
                ok = False
                notes.append(f"{item}: {average:.3g} of {b.supply:.3g} a second on average"
                             + (", counting what matters less in proportion," if yielding else "")
                             + f" would be {average / b.supply:.0%} (limit {AVERAGE_LIMIT:.0%})")
            elif yielding and b.average + rate * load > AVERAGE_LIMIT * b.supply:
                notes.append(f"{item}: fits only as {', '.join(yielding[:3])} yield to it")
            over = b.peak + rate - b.supply - (buffers or {}).get(item, 0.0) / BUFFER_SECONDS
            if over > 0:
                notes.append(f"{item}: {over:.3g} a second over supply when everything runs flat out")
        return ok, notes

    def short(self) -> list[Budget]:
        """Items the base is short of: claimed more than made on average, or measurably so (a consumer
        starved while every producer runs flat out). Worst first."""
        out = []
        for item in self.items():
            b = self.budget(item)
            if b.average <= 0:
                continue
            starved = [c for c in self.consumers(item) if c.mature and c.share("starved") >= STARVED]
            busy = self.producers(item) and all(p.mature and p.share("working") >= BUSY for p in self.producers(item))
            if b.share > 1.0 or (starved and busy):
                out.append(b)
        return sorted(out, key=lambda b: -b.share)

    def inserter_bound(self, site: Mapping[str, Any] | None = None) -> list[dict]:
        """Blocks held up by their inserters, measured (the user, 2026-10-29: "inserters matter"): short of inputs
        while the inserters feeding them work nearly all the time (they never wait for items, so items are there and
        the arms are too slow), or full of output while the ones taking from them work nearly all the time (they never
        wait for room). Each with the fix: another inserter on that side, or a faster one it can make (fast-replace).
        Judged again at every request, so research raising the hand size changes the fix offered."""
        from companion import inserters
        best = (inserters.makeable(site) or [None])[-1] if site is not None else None
        out = []
        for seg in self.segments:
            if not seg.mature or not seg.arms:
                continue
            # (short of a fluid is the pipes', not the inserters': left out of "in")
            starved = max(0.0, seg.share("starved") - seg.share("starved_fluid"))
            for side, state, share in (("in", "starved", starved), ("out", "backed_up", seg.share("backed_up"))):
                busy = seg.arms.get(f"{side}:working", 0.0)
                # (waiting at either end is time to spare: for items, or for room where another input backs up; and
                # time without fuel or power is not slowness either)
                waiting = sum(seg.arms.get(f"{side}:{k}", 0.0)
                              for k in ("waiting_source", "waiting_target", "no_fuel", "no_power", "low_power"))
                if share >= STARVED and busy >= ARM_BUSY and waiting <= ARM_IDLE:
                    fix = f"another inserter on that side, or {best} in their place" if best else "another inserter"
                    if best and site is not None:
                        fix += f" (a hand of {inserters.hand(best, site):g} now)"
                    what = "short of inputs" if side == "in" else "full of output"
                    out.append({"signal": "inserter-bound", "block": seg.id, "name": seg.name, "side": side,
                                "share": round(share, 3), "arms_busy": round(busy, 3), "to": best,
                                "why": f"{seg.name} is {what} {share:.0%} of the time while its inserters "
                                       f"{'feeding' if side == 'in' else 'emptying'} it work {busy:.0%}: {fix}"})
        return sorted(out, key=lambda o: -o["share"])

    def summary(self, limit: int = 3) -> str | None:
        """A few plain lines on what is short, and blocks held up by their inserters, for the strategist; None when
        nothing is."""
        lines = [b["why"] for b in self.inserter_bound()[:2]]
        for b in self.short()[:limit]:
            hungry = [c.name for c in self.consumers(b.item) if c.mature and c.share("starved") >= STARVED]
            lines.append(b.line() + (f"; short of it: {', '.join(hungry[:3])}" if hungry else ""))
        return " | ".join(lines) or None


def busy_share(block: Mapping[str, Any], key: str) -> tuple[float, bool]:
    """A block's share of machines in state `key`: measured over the last 30 minutes when it has been
    measured that long (True), else what its machines show right now (False)."""
    measured = block.get("measured") if isinstance(block.get("measured"), Mapping) else {}
    shares = measured.get("shares")
    if int(measured.get("minutes") or 0) >= MATURE_MINUTES and isinstance(shares, Mapping):
        return float(shares.get(key, 0.0)), True
    status = block.get("status") if isinstance(block.get("status"), Mapping) else {}
    total = sum(int(v) for v in status.values())
    return (int(status.get(key, 0)) / total if total else 0.0), False


# The latest ledger of each engineer, from the last site its planner saw (the strategists read it).
_latest: dict[int, Ledger] = {}
_sites: dict[int, Mapping[str, Any]] = {}
_lock = threading.Lock()


def remember(site: Mapping[str, Any]) -> Ledger | None:
    npc = site.get("npc_id")
    if not isinstance(npc, int):
        head = str(site.get("request_id", "")).split(":", 1)[0]
        npc = int(head) if head.isdigit() else None
    if npc is None or not isinstance(site.get("base"), list):
        return None
    book = Ledger.from_site(site)
    with _lock:
        _latest[npc] = book
        _sites[npc] = site   # (what its notes judge fixes by: the inserters it can make, its hand size now)
    try:   # its belt lanes too, one by one (companion/lane_ledger.py): their signals logged as they change
        from companion import lane_ledger
        lane_ledger.remember({**site, "npc_id": npc})
    except Exception:   # (never in the way of a plan)
        pass
    try:   # and what its robot network holds against what its growth will place (companion/network_ledger.py)
        from companion import network_ledger
        network_ledger.remember({**site, "npc_id": npc})
    except Exception:
        pass
    return book


def inserter_note(npc: Any) -> str | None:
    """The blocks held up by their inserters, for the strategist's normal path; None when none is."""
    with _lock:
        book = _latest.get(npc) if isinstance(npc, int) else None
        site = _sites.get(npc) if isinstance(npc, int) else None
    found = book.inserter_bound(site) if book else []
    return " | ".join(b["why"] for b in found[:2]) or None


def note_for(npc: Any) -> str | None:
    with _lock:
        book = _latest.get(npc) if isinstance(npc, int) else None
    return book.summary() if book else None


def forget() -> None:
    with _lock:
        _latest.clear()
        _sites.clear()
    from companion import lane_ledger, network_ledger
    lane_ledger.forget()
    network_ledger.forget()


def stage_ports(stage: str, attached: Iterable[Any], dx: float = 0, dy: float = 0) -> dict | None:
    """A bus stage's ledger block from its module (bus.Attached): ports where the module's belts meet the
    bus, with their rates, and what it makes and takes (export-local tiles: world plus dx, dy)."""
    for placed in attached:
        if f"bus-{placed.module.name}" != stage:
            continue
        m = placed.module
        ports = [{"item": p.item, "dir": p.kind, "rate": round(float(p.rate), 4),
                  "x": placed.x + p.x + dx, "y": placed.y + p.y + dy} for p in m.ports if p.kind in ("in", "out")]
        takes: dict[str, float] = {}
        for p in m.ports:
            if p.kind == "in":
                takes[p.item] = takes.get(p.item, 0.0) + float(p.rate)
        return {"ports": ports[:16], "makes": {k: round(float(v), 4) for k, v in m.makes.items()},
                "takes": {k: round(v, 4) for k, v in takes.items()}}
    return None

