"""Where an engineer's base is under pressure from the biters, how much defense stands there, and where to add
turrets next (the user, 2026-10-25: not "turrets here", but an understanding that defends the areas that pollute
and are attacked, and keeps building up as the biters hit different parts of the base).

The base is taken as areas: its machines grouped by nearness where they stand (the factory core, an outpost of
drills, a power plant by a far coal field; a block standing in two places is in two areas). For each area:

    pressure    the biggest wave that hit it lately (in small-biter equivalents: a medium biter counts for
                several, by SIZE), fading with age; and the wave its pollution will draw (pollution in its
                chunks, POLLUTION_PER_UNIT to a small biter); both scaled up as the biters evolve
    from        where that pressure comes from: where attacks on it first hit, else the nests soaking up
                its pollution, else the nearest nests
    covered     the loaded turrets in reach of it, each worth what it holds off (TURRET_HOLDS small biters,
                more with better ammunition), those on its pressure side counting most
    need        turrets to add: the pressure not covered, in turrets

The area with the biggest need gets turrets on its pressure side (at most MAX_GROUP a plan), loaded by hand: spread
along that side, side by side only where the biters keep attacking (the user, 2026-10-26: too many too close
together); the next plan looks again, so defense builds up where the biters keep coming. Every number here is a
weight to be learned from what happens next: each assessment that leads to building is written down
(`record`) with what it saw, for that.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from companion import footprint
from companion.threat import _num

PURPOSE = "reinforce"
GROUP_GAP = 24            # machines this near each other are one area
REACH_MARGIN = 6          # a turret this far past its range from an area still covers its edge
HALF_LIFE = 72000         # ticks: an attack's weight halves in 20 minutes (they come back where they came)
SIZE = {"small": 1.0, "medium": 4.0, "big": 10.0, "behemoth": 30.0}
POLLUTION_PER_UNIT = 60.0  # pollution in an area's chunks to one small biter of the wave it draws
POLLUTION_DRAWN_MAX = 20.0  # (a guess at waves to come: the waves that come are what build up beyond it)
NO_NESTS_KNOWN = 0.5      # pollution's pull with nothing known of the nests (they are out there all the same)
NEST_FAR = 300.0          # tiles from a part of its base: a nest this far off pulls on its pollution about a third
EVOLUTION_SCALE = 2.0     # pressure grows by this much at full evolution (bigger biters, bigger waves)
TURRET_HOLDS = 5.0        # small biters a loaded gun turret holds off with the plainest ammunition
AMMO = (("uranium", 3.0), ("piercing", 1.6))   # better ammunition holds off more (by name)
MIN_PRESSURE = 2.0        # less than this is no reason to build
GROWTH_MAX = 2.0          # the next wave taken as at most this many times the last on the same part of the base
DRAW_FALLOFF = 64.0       # tiles: a part of its base this much deeper behind the front than the nearest draws a wave a
                          # third as big (waves go for what pollutes nearest where they come from, D-13)
MAX_GROUP = 6             # turrets one plan adds to an area
MIN_GROUP = 2             # (turrets alone die: at least two cover each other)
HIT_NOW = 3600            # ticks: an attack this recent is under way (the part it hit is answered first)
HOT_HITS = 2              # attacks on an area lately (the game keeps 30 minutes of them) make it hot: turrets side by
                          # side there, else spread along its side
DEFENSES = ("reinforce", "starter-turrets", "plot-turrets", "plot-wall", "breach-repair", "basic-defense",
            "outpost-defense")
LOG_NAME = "defense.jsonl"
SIDE_BAND = 16            # tiles out past an area's pressure side (its rows stand 3 to 13 out): a turret there holds a
SIDE_INSIDE = 4           # stretch of it; or this far inside its edge (not one deep inside, behind it: Codex's review)
DECLINE_BAR = 1.25        # a declined defense goal comes back once the next wave grows past this much of what it was
DECLINED_KEPT = 256       # declines remembered at most (every world's, the oldest dropped first)
DECLINE_MAX = 72000       # ticks: ... or after 20 minutes in any case (what it judged by may have changed unseen)


@dataclass
class Area:
    box: tuple[float, float, float, float]   # local tiles
    purposes: list[str]
    value: int
    attack: float = 0.0       # the biggest recent wave on it (small-biter equivalents, faded)
    hits: int = 0             # attacks on it lately
    last_hit: float = -1e18   # the tick of its newest attack (within its reach)
    hit_distance: float = 0.0  # how far that attack's hit lay from it (0: in it)
    pollution: float = 0.0
    drawn: float = 0.0        # the wave its pollution draws
    pressure: float = 0.0
    toward: tuple[float, float] = (0.0, -1.0)   # unit vector: where the pressure comes from
    covered: float = 0.0
    polluting: bool = True    # what waves go for (machines that burn or draw power to work: not labs, poles, chests)
    pressure_now: float = 0.0  # its pressure without the base's next wave drawn on it (the threat level reads this)
    coming: float = 0.0       # the next wave it expects there: waves keep coming and growing (small biters)
    front: float = 0.0        # of the base's next wave, what it draws (small biters): the nearer the front, the more
    need: int = 0
    why: list[str] = field(default_factory=list)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2


def _box_distance(p: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    dx = max(box[0] - p[0], 0.0, p[0] - box[2])
    dy = max(box[1] - p[1], 0.0, p[1] - box[3])
    return math.hypot(dx, dy)


def _unit(dx: float, dy: float) -> tuple[float, float] | None:
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-6 else None


def size_of(name: str) -> float:
    for word, weight in SIZE.items():
        if word in name:
            return weight
    return SIZE["small"]


def wave(attack: Mapping[str, Any]) -> float:
    """An attack's size in small-biter equivalents."""
    units = attack.get("units") if isinstance(attack.get("units"), Mapping) else {}
    total = sum(size_of(str(n)) * float(c or 0) for n, c in units.items())
    return total or float(attack.get("count") or 0)


def turret_range(site: Mapping[str, Any], name: str) -> float:
    for t in ((site.get("defense") or {}).get("turrets") or []):
        if isinstance(t, Mapping) and t.get("name") == name and t.get("range"):
            return float(t["range"])
    return 18.0   # (a gun turret's)


def ammo_factor(name: str | None) -> float:
    for word, factor in AMMO:
        if name and word in name:
            return factor
    return 1.0


def best_ammo(site: Mapping[str, Any]) -> str | None:
    ammo = ((site.get("defense") or {}).get("ammo") or {}).get("bullet")
    return ammo[0] if isinstance(ammo, list) and ammo else None


def areas(site: Mapping[str, Any]) -> list[Area]:
    """Its machines where they stand grouped by nearness (each block's parts, not one box round a block whose drills
    stand at a far patch, nor the belts out to its fields: companion/footprint.py); defenses apart: they are what
    covers, not what is covered. An area's value is what is at stake in its machines."""
    items = []
    for b in site.get("base") or []:
        if not isinstance(b, Mapping) or b.get("purpose") in DEFENSES:
            continue
        for box, machines in footprint.parts(b):
            items.append((box, (str(b.get("purpose")), footprint.stake(machines), _pollutes(machines))))
    out = []
    for box, members in footprint.cluster(items, GROUP_GAP):
        purposes = list(dict.fromkeys(p for p, _, _ in members))
        out.append(Area(box, purposes, sum(v for _, v, _ in members), polluting=any(s for _, _, s in members)))
    return out


QUIET = ("lab", "pole", "chest", "inserter", "belt", "turret", "wall", "gate", "radar", "roboport", "beacon")


def _pollutes(machines: Mapping[str, Any]) -> bool:
    """Whether a part's machines pollute (drills, furnaces, boilers, assemblers and the like) rather than only stand."""
    return any(not any(q in str(n) for q in QUIET) for n in machines)


def nests(site: Mapping[str, Any], worms: bool = False) -> list[tuple[float, float, float]]:
    """The nests it knows (local tiles, with the pollution where each stands): those round home and its radars see,
    and those its pollution reaches farther out (the threat board's: they soak it up and send the attacks); with
    `worms`, the worms it knows too."""
    kinds = ("unit-spawner", "turret") if worms else ("unit-spawner",)
    out = {(float(t["x"]), float(t["y"])): float(t.get("pollution") or 0) for t in site.get("threats") or []
           if isinstance(t, Mapping) and t.get("kind") in kinds and "x" in t and "y" in t}
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    known = board.get("nests") if isinstance(board.get("nests"), Mapping) else {}
    for t in known.get("reached") or []:
        if isinstance(t, Mapping) and "x" in t and "y" in t:
            at = (float(t["x"]), float(t["y"]))
            out[at] = max(out.get(at, 0.0), float(t.get("pollution") or 0))
    return [(x, y, p) for (x, y), p in out.items()]


def nest_pull(site: Mapping[str, Any], box: tuple[float, float, float, float]) -> float:
    """How much the pollution where `box` stands draws attacks, by the nests (no fixed minute: the user, 2026-10-26):
    1 once it reaches a nest (a nest soaks it up and sends it back as attacks), else e^(-distance / NEST_FAR) to the
    nearest nest known, else to the nearest one could stand (none within what it looked over round home), else
    NO_NESTS_KNOWN."""
    known = nests(site)
    if any(p > 0 for _, _, p in known):
        return 1.0
    if known:
        return math.exp(-min(_box_distance((x, y), box) for x, y, _ in known) / NEST_FAR)
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    free = (board.get("nests") or {}).get("free") if isinstance(board.get("nests"), Mapping) else None
    home = site.get("home") if isinstance(site.get("home"), Mapping) else None
    if not isinstance(free, (int, float)) or home is None:
        return NO_NESTS_KNOWN
    hx, hy = float(home["x"]), float(home["y"])
    out_to = max(math.hypot(x - hx, y - hy) for x in (box[0], box[2]) for y in (box[1], box[3]))
    return math.exp(-max(0.0, float(free) - out_to) / NEST_FAR)   # (a nest just past what it looked over)


def assess(site: Mapping[str, Any]) -> list[Area]:
    """Every area of its base with its pressure, where it comes from, what covers it, and turrets wanted."""
    out = areas(site)
    targets = [a.box for a in out if a.polluting]
    wave_next, front = base_wave(site), _front(site, targets or [a.box for a in out])
    for a in out:
        share, source = front(a.box)
        # (a quiet part, a lab, draws no wave while something pollutes for it to go for: Codex's review)
        judge(site, a, wave_next * share if a.polluting or not targets else 0.0, source)
        if a.need and a.hits < HOT_HITS:
            # (as many as the spread rule lets go on its side, the rule the plan places by: no more side by side
            # where no attack keeps landing; D-16, run normal-7b: chosen 53 times, declined 48, the economy waiting)
            gun = next((t for t in arms_of(site) if t.get("kind") == "ammo"), None)
            if gun is not None:
                a.need = min(a.need, len(stretches(site, a, gun)))
    return out


def arms_of(site: Mapping[str, Any]) -> list:
    from companion import perimeter
    return perimeter.arms(site)["turrets"]


def stretches(site: Mapping[str, Any], area: Area, turret: Mapping[str, Any]) -> list[float]:
    """Where along the area's pressure side (local tiles, nearest where the pressure points first) turrets may go by
    the user's rule: spread out, as far apart as keeps each stretch in reach of two, and only where they reach the area;
    side by side only where the biters keep attacking (HOT_HITS: every slot then). The slots a turret standing on that
    side already holds are left out. One rule for what the brain asks for and what the plan places (D-16)."""
    from companion import perimeter
    w, h = int(turret.get("w") or 2), int(turret.get("h") or 2)
    x0, y0, x1, y1 = area.box
    tx, ty = area.toward
    horizontal = abs(ty) >= abs(tx)
    unit = w if horizontal else h
    hot = area.hits >= HOT_HITS
    step = unit + 1 if hot else perimeter.spacing(turret)
    over = max(0.0, turret_range(site, str(turret.get("name"))) - 6)
    lo, hi = (x0, x1) if horizontal else (y0, y1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    aim = min(max(cx + tx * 8, x0), x1) if horizontal else min(max(cy + ty * 8, y0), y1)
    edge = ((y0 if ty < 0 else y1) if horizontal else (x0 if tx < 0 else x1))
    outward = -1.0 if (ty < 0 if horizontal else tx < 0) else 1.0
    held = [(float(g["x"]), float(g["y"])) for g in site.get("guards") or []
            if isinstance(g, Mapping) and "x" in g and "y" in g]
    reach = max(2 * max(1, area.need) + 2, math.ceil((hi - lo + 2 * over) / step) + 1)
    out = []
    for k in sorted(range(-reach, reach + 1), key=lambda k: (abs(k), k)):
        target = aim + k * step
        if hot:
            out.append(target)
            continue
        if not lo - over <= target <= hi + over:
            continue
        if any(abs((sx if horizontal else sy) - target) <= step / 2
               and -SIDE_INSIDE <= ((sy if horizontal else sx) - edge) * outward <= SIDE_BAND for sx, sy in held):
            continue   # (one stands on this stretch already)
        out.append(target)
    return out


# --- a defense goal declined: not chosen again until something changes -------------------------------------------

from companion import failures as _failures   # (one memory of goals that could not be planned, every family's: SO-4)

_declined = _failures.MEMORY


def _situation(site: Mapping[str, Any]) -> dict:
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    attacks = [a for a in board.get("attacks") or [] if isinstance(a, Mapping)]
    from companion import perimeter
    kit = perimeter.arms(site)
    arms = {t.get("name") for t in kit["turrets"]} | set(kit["walls"])   # (not ammunition: made all the time)
    ever = board.get("ever")   # (the game's: {count, first, last}, every attack on its plot kept for good)
    ever = ever.get("count") if isinstance(ever, Mapping) else ever
    return {"tick": float(site.get("tick") or 0),
            "ever": int(ever) if isinstance(ever, (int, float)) and not isinstance(ever, bool) else 0,
            "last": max((float(a.get("tick") or 0) for a in attacks), default=0.0),
            "guards": sum(1 for g in site.get("guards") or [] if isinstance(g, Mapping)),
            "base": len(site.get("base") or []), "wave": base_wave(site),
            # (what it has to build them with: a wall plan declined for too few walls comes back once they are made)
            "stock": {str(n): perimeter.stocked(site, str(n)) for n in arms if n},
            # (its breaches as the game reads them: where, their rows standing, and their last loss: a wall lost or
            # put up there, D-20; Codex's review: a wall lost again where the rows stay as many)
            "breaches": sorted([round(_num(b["x"])), round(_num(b["y"])), int(_num(b.get("layers"))),
                                _num(b.get("tick"))]
                               for b in site.get("breaches") or [] if isinstance(b, Mapping) and "x" in b and "y" in b)}


# A reason of the ground (no room, the layers all standing, turrets spread, what stands in the way): a new attack or a
# bigger wave cannot mend it, only something on the ground changing (D-20: breach-repair declined 12 times and the ammo
# loop failed 10 times in 40 minutes, each released by the next attack, while oil waited; the oil practice, 418ea85)
GROUND_REASONS = ("room", "layer", "spread", "stand", "under", "reach", "no way", "fit", "outside", "in the way")


def unchanged(then: Mapping[str, Any], now: Mapping[str, Any], reason: str = "") -> bool:
    """Nothing changed since a defense goal was declined (or failed) that could bring it back: no turret lost, nothing
    new built, no more walls or turrets made, no breach's layers changed; and for a reason not of the ground, no new
    attack and the next wave not grown past DECLINE_BAR either."""
    if not then:
        return False
    ground = then.get("breaches", []) == now.get("breaches", []) and now["guards"] >= then["guards"]         and now["base"] == then["base"] and all(n <= then["stock"].get(item, 0) for item, n in now["stock"].items())
    if any(w in str(reason).lower() for w in GROUND_REASONS):
        return ground
    return ground and now["ever"] <= then["ever"] and now["last"] <= then["last"]         and now["wave"] <= then["wave"] * DECLINE_BAR


def decline(site: Mapping[str, Any], purpose: str, reason: str = "declined") -> None:
    """Its planner declined this defense goal: what it saw then, so the chooser passes it over until that changes
    (D-16: a goal the planner would decline kept winning the choice, a minute apart, for half an hour). Kept in the
    one memory of goals that could not be planned (companion/failures.py)."""
    _failures.note(site, purpose, reason or "declined", kind="declined")


def declined(site: Mapping[str, Any], purpose: str) -> bool:
    """Whether this defense goal was declined (or failed) and nothing has changed since (`unchanged`), within its
    hold (DECLINE_MAX at first)."""
    return _failures.held(site, purpose) is not None


def lacking(site: Mapping[str, Any], box: tuple[float, float, float, float]) -> int:
    """The turrets a stretch of ground (local tiles) lacks against what presses on it, judged as any area of its base
    is (0 when what covers it holds): a breach's repair sized to the wave that broke through (D-11)."""
    box = tuple(float(v) for v in box)
    targets = [a.box for a in areas(site) if a.polluting]
    share, source = _front(site, targets + [box])(box)
    return judge(site, Area(box, ["stretch"], 0), base_wave(site) * share, source).need


def base_wave(site: Mapping[str, Any]) -> float:
    """The next wave expected on its base as a whole (small biters), wherever it lands: the trend of every wave that hit
    it lately (coming), else the biggest of them or of its teammates' waves lately faded; none with no attack lately. Waves go for what pollutes nearest
    where they come from, not only where the last one hit (D-13: rehearsal 7b lost 17 burner drills and 5 furnaces in
    fields no turret covered, the turrets by the factory's middle and where it had been hit)."""
    tick = float(site.get("tick") or 0)
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    hits = [(float(a.get("tick") or tick), wave(a)) for a in board.get("attacks") or [] if isinstance(a, Mapping)]
    # (its teammates' waves come to its plot in turn, D-15: rehearsal 7g's waves came at minute 9 on one plot, 15 on the
    # next, and each plot's drill fields were covered only once its own was hit; theirs no trend, being other plots)
    team = [(float(a.get("tick") or tick), wave(a)) for a in board.get("team") or [] if isinstance(a, Mapping)]
    faded = max((w * 0.5 ** (max(0.0, tick - t) / HALF_LIFE) for t, w in hits + team), default=0.0)
    return max(coming(hits, tick), faded)


def _front(site: Mapping[str, Any], boxes: list) -> Any:
    """How much of the base's next wave a part of it (a box) draws, and from where: 1 for what pollutes nearest where
    the waves come from (`boxes`: the parts waves go for), e^(-depth / DRAW_FALLOFF) for what lies deeper; where they
    come from: the nests it knows, else where its attacks came from, else its plot's outer edges (point None)."""
    sources = [(x, y) for x, y, _ in nests(site)]
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    if not sources:   # (a teammate's wave came from its own nests: its own plot's attacks, else its outer edges)
        for attack in board.get("attacks") or []:
            came = attack.get("from") if isinstance(attack, Mapping) and isinstance(attack.get("from"), Mapping) else None
            if came and "x" in came and "y" in came:
                sources.append((float(came["x"]), float(came["y"])))
    team = site.get("team") if isinstance(site.get("team"), Mapping) else {}
    plot, outer = team.get("plot"), [o for o in team.get("outer") or [] if o in ("north", "south", "west", "east")]

    def depth(box: tuple) -> tuple[float, Any]:
        if sources:
            p = min(sources, key=lambda s: _box_distance(s, box))
            return _box_distance(p, box), p
        if isinstance(plot, (list, tuple)) and len(plot) == 4 and outer:
            x0, y0, x1, y1 = (float(v) for v in plot)
            edge = {"north": box[1] - y0, "south": y1 - box[3], "west": box[0] - x0, "east": x1 - box[2]}
            return max(0.0, min(edge[o] for o in outer)), None
        return 0.0, None
    nearest = min((depth(b)[0] for b in boxes), default=0.0)

    def share(box: tuple) -> tuple[float, Any]:
        d, p = depth(box)
        return math.exp(-max(0.0, d - nearest) / DRAW_FALLOFF), p
    return share


def judge(site: Mapping[str, Any], a: Area, drawn_wave: float = 0.0, source: Any = None) -> Area:
    """One area's pressure (the waves that hit it, faded, or the next one coming, and the wave its pollution draws,
    grown as the biters evolve), where it comes from, what covers it, and the turrets it lacks."""
    tick = float(site.get("tick") or 0)
    board = site.get("threat_board") if isinstance(site.get("threat_board"), Mapping) else {}
    evolution = min(1.0, max(0.0, float(board.get("evolution") or 0)))
    scale = 1.0 + EVOLUTION_SCALE * evolution
    known = nests(site, worms=True)
    cx, cy = a.centre
    pull: list[tuple[float, float, float]] = []   # (dx, dy, weight) toward what presses on it
    waves: list[tuple[float, float]] = []          # (tick, size) of the waves that hit it
    for attack in board.get("attacks") or []:
        if not isinstance(attack, Mapping) or "x" not in attack:
            continue
        hit = (float(attack["x"]), float(attack["y"]))
        if _box_distance(hit, a.box) > GROUP_GAP * 2:
            continue
        faded = wave(attack) * 0.5 ** (max(0.0, tick - float(attack.get("tick") or tick)) / HALF_LIFE)
        a.attack = max(a.attack, faded)
        a.hits += 1
        when = float(attack.get("tick") or tick)
        if when > a.last_hit:
            a.last_hit, a.hit_distance = when, _box_distance(hit, a.box)
        waves.append((float(attack.get("tick") or tick), wave(attack)))
        came = attack.get("from") if isinstance(attack.get("from"), Mapping) else attack
        pull.append((float(came["x"]) - cx, float(came["y"]) - cy, faded))
    # (its wall line's stretches worn hard that hold: hit again and again there, so turrets side by side; D-23, the user:
    # "what really needed to happen was to add more turrets to that section")
    for s in board.get("worn") or []:
        if isinstance(s, Mapping) and "x" in s and "y" in s                 and _box_distance((float(s["x"]), float(s["y"])), a.box) <= GROUP_GAP * 2:
            a.hits += int(s.get("hits") or 1)
    for p in site.get("pollution") or []:   # (the chunks its machines stand in: each given at its centre)
        if isinstance(p, Mapping):
            x, y = float(p["x"]), float(p["y"])
            if x - 16 < a.box[2] and a.box[0] < x + 16 and y - 16 < a.box[3] and a.box[1] < y + 16:
                a.pollution += float(p.get("p") or 0)
    a.coming = coming(waves, tick)
    own = max(a.attack, a.coming)
    a.attack = max(own, drawn_wave)   # (and the base's next wave, as much as it draws it: D-13)
    a.front = drawn_wave
    if not pull and drawn_wave > 0 and source is not None:   # (unhit, pressed by the wave to come: faces where it comes)
        pull.append((source[0] - cx, source[1] - cy, drawn_wave))
    a.drawn = min(POLLUTION_DRAWN_MAX, a.pollution / POLLUTION_PER_UNIT * nest_pull(site, a.box))
    a.pressure = max(a.attack, a.drawn) * scale
    a.pressure_now = max(own, a.drawn) * scale
    if not pull and known:
        soaking = [n for n in known if n[2] > 0] or known
        nx, ny, _ = min(soaking, key=lambda n: math.hypot(n[0] - cx, n[1] - cy))
        pull.append((nx - cx, ny - cy, 1.0))
    if pull:
        sx = sum(dx / max(1.0, math.hypot(dx, dy)) * w for dx, dy, w in pull)
        sy = sum(dy / max(1.0, math.hypot(dx, dy)) * w for dx, dy, w in pull)
        a.toward = _unit(sx, sy) or a.toward
    else:
        a.toward = _edge_toward(site, a) or a.toward
    for g in site.get("guards") or []:
        if not isinstance(g, Mapping) or "x" not in g:
            continue
        loaded = g.get("kind") != "ammo-turret" or float(g.get("ammo") or 0) > 0
        reach = turret_range(site, str(g.get("name"))) + REACH_MARGIN
        if not loaded or _box_distance((float(g["x"]), float(g["y"])), a.box) > reach:
            continue
        side = _unit(float(g["x"]) - cx, float(g["y"]) - cy)
        facing = 1.0 if side is None else 0.5 + 0.5 * max(0.0, side[0] * a.toward[0] + side[1] * a.toward[1])
        health = float(g.get("health") or 1.0)
        a.covered += TURRET_HOLDS * ammo_factor(best_ammo(site)) * facing * health
    per_turret = TURRET_HOLDS * ammo_factor(best_ammo(site))
    if a.pressure >= MIN_PRESSURE and a.pressure > a.covered:
        a.need = min(MAX_GROUP, max(MIN_GROUP, math.ceil((a.pressure - a.covered) / per_turret - 1e-9)))
    a.why = [f"drawing the next wave {a.front:.0f}" if a.front > own else f"next wave {a.coming:.0f}" if a.coming
             else f"wave {own:.0f}" if own else "",
             f"pollution {a.pollution:.0f}" if a.pollution else "",
             f"evolution {evolution:.2f}" if evolution else ""]
    a.why = [w for w in a.why if w]
    return a


def coming(waves: list[tuple[float, float]], tick: float | None = None) -> float:
    """The next wave expected on a part of the base, from the waves that hit it (tick, size): once they keep coming
    (two or more), the last one as big again as it grew from the one before (at most GROWTH_MAX times, never less
    than the last): defense sized to what comes next, not to the last wave faded (D-11: waves of 9, then 23 and 34,
    met by two turrets). One wave alone is no trend: none; nor two waves a half-life or more apart, nor once the last is
    a half-life old (then it fades as any wave: Codex's review, D-11-early-defense)."""
    if len(waves) < 2:
        return 0.0
    ordered = sorted(waves)
    (t_last, last), (t_before, before) = ordered[-1], ordered[-2]
    if t_last - t_before >= HALF_LIFE or (tick is not None and tick - t_last >= HALF_LIFE):
        return 0.0
    return last * min(GROWTH_MAX, max(1.0, last / before if before > 0 else 1.0))


def most_needed(site: Mapping[str, Any]) -> Area | None:
    """The area to reinforce next: the biggest need, the most at stake first among equals; None when every
    area holds."""
    wanting = [a for a in assess(site) if a.need > 0]
    if not wanting:
        return None
    # (hit now: the part the biters are at first, the nearest its newest hit; LIVE-7c-c, normal-7c: Dax's drill field was
    # hit and its turrets went to the core beside it, more at stake, while the biters stood 3 minutes in the field)
    tick = float(site.get("tick") or 0)
    now = [a for a in wanting if tick - a.last_hit <= HIT_NOW]
    if now:
        return min(now, key=lambda a: (a.hit_distance, -(a.pressure - a.covered), -a.need, -a.value))
    return max(wanting, key=lambda a: (a.pressure - a.covered, a.value))


def _edge_toward(site: Mapping[str, Any], a: Area) -> tuple[float, float] | None:
    """With nothing known to press on it: toward the plot's outer edge (the team world), where they come from."""
    outer = ((site.get("team") or {}).get("outer") or []) if isinstance(site.get("team"), Mapping) else []
    vectors = {"north": (0.0, -1.0), "south": (0.0, 1.0), "west": (-1.0, 0.0), "east": (1.0, 0.0)}
    for name in outer:
        if name in vectors:
            return vectors[name]
    return None


# --- what it decided, for learning ---------------------------------------------------------------------------

_LOG_LOCK = threading.Lock()


def record(site: Mapping[str, Any], area: Area, decision: Mapping[str, Any]) -> None:
    """One line per decision: what it saw (every area's features) and what it did, for a model to learn the
    weights above from what happened next (the attacks and losses that follow, in the game's records)."""
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    row = {"time": time.time(), "tick": site.get("tick"), "npc": site.get("npc_id"),
           "evolution": (site.get("threat_board") or {}).get("evolution"),
           "areas": [{"box": list(x.box), "value": x.value, "attack": round(x.attack, 2),
                      "pollution": round(x.pollution, 1), "pressure": round(x.pressure, 2),
                      "covered": round(x.covered, 2), "need": x.need, "toward": [round(v, 2) for v in x.toward]}
                     for x in assess(site)],
           "chose": {"box": list(area.box), "need": area.need}, "decision": dict(decision)}
    path = Path(folder) / "decisions" / LOG_NAME
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(row) + "\n")
    except OSError:
        pass   # (a log, never in the way of a plan)


# --- the plan ----------------------------------------------------------------------------------------------

def plan_reinforce(site: Mapping[str, Any], world: Mapping[str, Any], area: Area | None = None,
                   shield: bool = False, max_turrets: int | None = None, wall_limit: int | None = None) -> dict:
    """Turrets on the area's pressure side, as many as its need (facing where the pressure comes from, a few tiles
    out from its buildings, on free ground of its own): spread along that side, far enough apart that each stretch is
    in reach of two (perimeter.spacing), and side by side only where the biters keep attacking (HOT_HITS); loaded by
    hand with the best ammunition it makes; with `shield` (an emergency), a short wall in front. An emergency's hand
    work is bounded (companion/threat.py): at most `max_turrets` (never fewer than a pair), the wall while
    `wall_limit` lasts."""
    from companion import layout_planner, perimeter, team_area
    from companion.modules import Placed
    area = area or most_needed(site)
    if area is None:
        raise perimeter.Done("every part of the base holds")
    kit = perimeter.arms(site)
    guns = [t for t in kit["turrets"] if t.get("kind") == "ammo"]   # (no power line to run out to it)
    if not guns:
        raise layout_planner.PlanError("no turret it can make yet that needs no power")
    turret = guns[0]
    w, h = int(turret.get("w") or 2), int(turret.get("h") or 2)
    ox, oy = perimeter._local(site)
    team = team_area.team_of(site)
    ground = perimeter._Ground(world)
    x0, y0, x1, y1 = (area.box[0] + ox, area.box[1] + oy, area.box[2] + ox, area.box[3] + oy)
    tx, ty = area.toward
    horizontal = abs(ty) >= abs(tx)   # (pressure from north or south: a row running east-west)
    n = max(1, area.need)
    if max_turrets is not None:
        n = max(min(n, MIN_GROUP), min(n, max_turrets))
    unit = w if horizontal else h
    hot = area.hits >= HOT_HITS
    # spread along the side, as far apart as keeps each stretch in reach of two, and only where they reach the area
    # (spread past it, they covered nothing and it kept asking, the review, 2026-10-26): as many as fit so; more side
    # by side once the biters keep hitting there
    limit = turret_range(site, turret["name"]) + REACH_MARGIN - 1   # (what assess counts as covering the area)
    step = unit + 1 if hot else perimeter.spacing(turret)
    # (the slots the user's rule leaves on that side, the rule assess asks by: stretches, D-16)
    slots = [t + (ox if horizontal else oy) for t in stretches(site, area, turret)]

    def ok(tiles: set) -> bool:
        if not ground.free(tiles):
            return False
        return team is None or all(team.tile_ok(x - int(ox), y - int(oy)) for x, y in tiles)
    row: list = []
    for gap in range(3, 14):
        if horizontal:
            across = (y0 - gap - h / 2) if ty < 0 else (y1 + gap + h / 2)
        else:
            across = (x0 - gap - w / 2) if tx < 0 else (x1 + gap + w / 2)
        out, mine = [], set()
        for target in slots:
            if len(out) >= n:
                break
            for shift in [0] + [s for i in range(1, (1 if hot else step // 2) + 1) for s in (i, -i)]:
                a = math.floor(target - unit / 2) + shift + unit / 2
                e = Placed(turret["name"], a, across) if horizontal else Placed(turret["name"], across, a)
                if _box_distance((e.x, e.y), (x0, y0, x1, y1)) > limit:
                    continue   # (out of reach of the area: it would cover nothing there, the review, 2026-10-26)
                tiles = perimeter._tiles(e, w, h)
                if ok(tiles) and not tiles & mine:
                    out.append((e, tiles))
                    mine |= tiles
                    break
        if len(out) > len(row):
            row = out
        if len(row) >= n:
            break
    if not row:
        if not slots:
            raise perimeter.Done("turrets stand spread along the side the pressure comes from; more go side by side "
                                 "where the biters keep hitting")
        raise layout_planner.PlanError("no room for turrets on the side the pressure comes from")
    placed = [e for e, _ in row]
    for _, tiles in row:
        ground.take(tiles)
    wall = []
    if shield and kit["walls"]:
        for e, tiles in row:
            for tx_, ty_ in tiles:
                front = (tx_, ty_ - 2) if horizontal and ty < 0 else (tx_, ty_ + 2) if horizontal else \
                    (tx_ - 2, ty_) if tx < 0 else (tx_ + 2, ty_)
                if wall_limit is not None and len(wall) >= wall_limit:
                    break
                if ok({front}) and front not in {(math.floor(p.x), math.floor(p.y)) for p in wall}:
                    wall.append(Placed(kit["walls"][0], front[0] + 0.5, front[1] + 0.5))
                    ground.take({front})
    ammo = perimeter._ammo_for(turret, kit["ammo"], site)
    members = placed + wall
    side = ("north" if ty < 0 else "south") if horizontal else ("west" if tx < 0 else "east")
    stages = [{"id": f"reinforce-{side}", "depends_on": [], "members": [id(e) for e in members],
               "manual_supplies": [{"member": id(e), "item": ammo, "slot": "ammo"} for e in placed],
               "witnesses": [{"kind": "entity_built", "member": id(placed[0])},
                             {"kind": "ammo_loaded", "member": id(placed[0])}]}]
    key = re.sub(r"[^A-Za-z0-9_-]", "-", f"{side}-{int(area.box[0])}-{int(area.box[1])}")
    plan = perimeter._plan(site, PURPOSE, key, members, stages, source="defense-brain")
    plan["brain"] = {"pressure": round(area.pressure, 2), "covered": round(area.covered, 2), "turrets": len(placed),
                     "why": area.why, "side": side, "hot": hot}
    return plan
