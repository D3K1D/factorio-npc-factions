"""Inserter throughput, as part of the flow accounting (the user, 2026-10-29: "an assembler might need a lot of gears per
second that a normal yellow inserter wouldnt be able to grab quick enough; things like stack size matter too").

How fast an inserter moves items, from the game's facts for its prototype (base, Space Age and mods alike):
- one swing there and back is a full turn of its arm (rotation speed, turns a tick);
- its hand holds 1 + its own bonus + the force's researched bonus (bulk inserters the bulk bonus);
- from a chest or machine it takes a full hand at once; from a belt it waits for each item after the first to come
  past, on one lane (an item that rides one side) or two, by the belt's speed; onto a belt it drops one at a time;
- what it reaches changes nothing here: a long-handed arm turns at its own speed (its facts carry it).

One shared check for every planner (generated rows, bus modules, imported and optimizer blocks): each machine's inputs
and outputs, at its recipe's rate in that machine, against what the inserters at it move. Where they fall short: more
of them on that side (up to the slots there), a faster type it can make, or the block runs slower and by how much.

    rate("fast-inserter", site, source="belt", lanes=1)       # items/s
    port(need, "inserter", 1, site, source="belt")           # Check: ok, have, need, fix, factor, why
    plan(entities, site)                                     # [Check] for every machine side that falls short
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# Without the game's facts: the base game's inserters (turns a tick, own hand bonus, bulk).
BASE = {"burner-inserter": (0.01, 0, False), "inserter": (0.014, 0, False),
        "long-handed-inserter": (0.02, 0, False), "fast-inserter": (0.04, 0, False),
        "bulk-inserter": (0.04, 1, True), "stack-inserter": (0.04, 3, True)}
ITEM_GAP = 0.25          # tiles between items on a belt lane
BELT_SPEED = 0.03125     # tiles a tick of a yellow belt, when the belt is not known
SLOTS = 3                # inserters that fit on a side of a 3x3 machine
VEC = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def _facts(site: Mapping[str, Any] | None):
    from companion import rates
    try:
        return rates.of(site or {})
    except Exception:
        return None


def _proto(name: str, site: Mapping[str, Any] | None) -> tuple[float, float, bool, bool] | None:
    """(turns a tick, its own hand bonus, bulk, takes the force's bonus) from the facts, the site's prototypes, or
    BASE."""
    facts = _facts(site)
    e = facts.entities.get(name) if facts else None
    if e and e.get("kind") == "inserter" and e.get("rotation"):
        return (float(e["rotation"]), float(e.get("stack_bonus") or 0), bool(e.get("bulk")),
                e.get("force_bonus", True) is not False)
    p = ((site or {}).get("prototypes") or {}).get(name) or {}
    if p.get("rotation_speed"):
        return (float(p["rotation_speed"]), float(p.get("hand_bonus") or 0), bool(p.get("bulk")),
                p.get("stack_bonus", True) is not False)
    if name in BASE:
        turn, own, bulk = BASE[name]
        return turn, own, bulk, True
    return None


def hand(name: str, site: Mapping[str, Any] | None = None) -> float:
    """Items it carries a swing: 1, its own bonus, and the force's researched bonus."""
    p = _proto(name, site)
    if p is None:
        return 1.0
    _, own, bulk, force = p
    facts = _facts(site)
    bonus = (site or {}).get("inserter_bonus") or {}
    if bulk:
        extra = (facts.bonuses.get("bulk_inserter_capacity") if facts else None) or bonus.get("bulk") or 0
    elif force:
        extra = (facts.bonuses.get("inserter_stack") if facts else None) or bonus.get("stack") or 0
    else:
        extra = 0
    return 1.0 + own + float(extra or 0)


def _belt_stack(site: Mapping[str, Any] | None) -> float:
    """Items a stack on a belt may hold (1 plus the researched belt stack bonus)."""
    facts = _facts(site)
    return 1.0 + float((facts.bonuses.get("belt_stack") if facts else 0) or 0)


def rate(name: str, site: Mapping[str, Any] | None = None, *, source: str = "chest", target: str = "machine",
         lanes: int = 2, belt_speed: float | None = None) -> float:
    """Items a second one `name` moves from `source` to `target` ("belt", "chest" or "machine"): its hand a swing,
    a swing a full turn, plus on a belt the wait for each item after the first (from `lanes` lanes: an item that rides
    one side comes half as often) or the drop of each after the first."""
    p = _proto(name, site)
    if p is None:
        return 0.0
    turn = p[0]
    if turn <= 0:
        return 0.0
    h = hand(name, site)
    tall = _belt_stack(site) if ("stack-inserter" in name and target == "belt") else 1.0
    if tall > 1 and h >= tall:   # (onto a belt its hand is rounded down to whole stacks of the belt's height)
        h = math.floor(h / tall) * tall
    swing = 1.0 / turn                                    # ticks
    per_item = ITEM_GAP / (belt_speed or BELT_SPEED)      # ticks between items on one lane
    if source == "belt":
        swing += (h - 1) * per_item / max(1, min(2, lanes))
    if target == "belt":
        # (one item a placement; a stack inserter puts down stacks as tall as the belt's researched stack height)
        swing += (math.ceil(h / tall) - 1) * per_item
    return h * 60.0 / swing


@dataclass
class Check:
    ok: bool
    need: float                 # items/s the side must move
    have: float                 # what its inserters move
    inserter: str
    count: int
    fix: str                    # "", "add", "upgrade" or "slower"
    to: str | None = None       # the inserter the fix uses
    to_count: int = 0           # how many of it
    factor: float = 1.0         # the share of its rate the block runs at with what it has (fix "slower")
    where: str = ""             # the machine and side, for the reason
    why: str = ""


def makeable(site: Mapping[str, Any] | None) -> list[str]:
    """The inserters the engineer can make, fastest last (the game's facts, else the base game's it has recipes for)."""
    have = {str(r.get("name")) for r in (site or {}).get("recipes") or () if isinstance(r, Mapping)}
    facts = _facts(site)
    names = [n for n in (facts.of_kind("inserter") if facts else ()) if not have or n in have]
    names += [n for n in BASE if n in have and n not in names]
    return sorted(set(names), key=lambda n: rate(n, site))


def port(need: float, inserter: str, count: int, site: Mapping[str, Any] | None = None, *, source: str = "chest",
         target: str = "machine", lanes: int = 2, slots: int = SLOTS, where: str = "",
         belt_speed: float | None = None) -> Check:
    """Whether `count` `inserter`s move `need` items/s; else the fix: more of them (up to `slots`), the slowest type it
    can make that does it within the slots, or the share of its rate the block runs at."""
    def at(kind: str) -> float:
        return rate(kind, site, source=source, target=target, lanes=lanes, belt_speed=belt_speed)
    one = at(inserter)
    have = one * count
    base = dict(need=need, have=have, inserter=inserter, count=count, where=where)
    if have + 1e-9 >= need:
        return Check(True, fix="", **base)
    n = math.ceil(need / one - 1e-9) if one > 0 else slots + 1
    if n <= slots:
        return Check(False, fix="add", to=inserter, to_count=n,
                     why=f"{where}: {count} {inserter} move {have:.2f}/s of {need:.2f}/s: {n} of them", **base)
    for kind in makeable(site):
        r = at(kind)
        k = math.ceil(need / r - 1e-9) if r > 0 else slots + 1
        if k <= slots:
            return Check(False, fix="upgrade", to=kind, to_count=k,
                         why=f"{where}: {count} {inserter} move {have:.2f}/s of {need:.2f}/s: {k} {kind}", **base)
    best = max(makeable(site) or [inserter], key=at)
    most = at(best) * slots
    return Check(False, fix="slower", to=best, to_count=slots, factor=round(min(1.0, most / need), 3),
                 why=f"{where}: even {slots} {best} move {most:.2f}/s of {need:.2f}/s: it runs at "
                     f"{min(1.0, most / need):.0%}", **base)


# --- a plan's machines and the inserters at them ----------------------------------------------------------------

FLUIDS = {"water", "steam", "crude-oil", "heavy-oil", "light-oil", "petroleum-gas", "lubricant", "sulfuric-acid"}


def _fluids(site: Mapping[str, Any] | None) -> set[str]:
    """Fluids: what reaches a machine by pipe, never by inserter (the game's facts, else the base game's)."""
    facts = _facts(site)
    return FLUIDS | set((facts.fluids if facts else {}) or ())


def _row(e: Any) -> dict:
    if isinstance(e, Mapping):
        p = e.get("position") or {}
        return {"x": float(p.get("x", e.get("x", 0))), "y": float(p.get("y", e.get("y", 0))),
                "name": str(e.get("prototype") or e.get("name")), "d": int(e.get("direction") or 0),
                "recipe": e.get("recipe"), "pickup": e.get("pickup_position"), "drop": e.get("drop_position"),
                "lanes": e.get("lanes")}
    return {"x": float(e.x), "y": float(e.y), "name": str(e.name), "d": int(e.direction or 0),
            "recipe": getattr(e, "recipe", None), "pickup": None, "drop": None, "lanes": None}


def _size(name: str, site: Mapping[str, Any] | None) -> tuple[int, int]:
    facts = _facts(site)
    e = facts.entities.get(name) if facts else None
    if e and e.get("width"):
        return int(e["width"]), int(e.get("height") or e["width"])
    from companion.smelting import footprint
    from companion.modules import Placed
    n = max(1, int(math.sqrt(len(footprint([Placed(name, 0.5, 0.5)])))))
    return n, n


def _ends(r: dict, site: Mapping[str, Any] | None) -> tuple[tuple[int, int], tuple[int, int]]:
    """An inserter's pickup and drop tiles: its own settings where the plan has them (an imported blueprint), else
    facing its pickup at its reach (the prototype's, a long-handed one's 2), as companion/science_blocks._ends."""
    x, y = r["x"], r["y"]
    pu, dr = r["pickup"], r["drop"]
    if isinstance(pu, Mapping) and isinstance(dr, Mapping):
        return ((math.floor(x + float(pu["x"])), math.floor(y + float(pu["y"]))),
                (math.floor(x + float(dr["x"])), math.floor(y + float(dr["y"]))))
    facts = _facts(site)
    e = facts.entities.get(r["name"]) if facts else None
    reach = int(round(float((e or {}).get("reach") or (2 if "long" in r["name"] else 1))))
    vx, vy = VEC.get(r["d"], (0, -1))
    return ((math.floor(x + vx * reach), math.floor(y + vy * reach)),
            (math.floor(x - vx * reach), math.floor(y - vy * reach)))


def _belt_speed(name: str, site: Mapping[str, Any] | None) -> float | None:
    """A belt's speed, tiles a tick, from the facts (its items a second unstacked, both lanes, over 480: how fast it
    moves, apart from how tall its stacks may be)."""
    facts = _facts(site)
    rate_ = facts.belt_rate(name) if facts else None
    return rate_ / _belt_stack(site) / 480 if rate_ else None


def plan(entities: Iterable[Any], site: Mapping[str, Any] | None = None) -> list[Check]:
    """Every crafting machine of a plan with a recipe, each side (in and out), against the inserters dropping into it
    and taking from it: the sides that fall short, with their fixes. Only items count (fluids come by pipe). What an
    inserter picks from sets its rate: a belt by its tier, and from one lane where the belt carries an item a side; a
    chest or a machine at once."""
    from companion import ratios
    rows = [_row(e) for e in entities]
    book = ratios.Book.from_site(site or {})
    facts = _facts(site)
    fluids = _fluids(site)
    machines, at = [], {}
    for r in rows:
        tile = (math.floor(r["x"]), math.floor(r["y"]))
        if "belt" in r["name"] or "splitter" in r["name"]:
            lanes = r["lanes"] if isinstance(r["lanes"], Mapping) else {}
            sides = {str(v) for side in ("left", "right") for v in (lanes.get(side) or ())}
            at[tile] = ("belt", r["name"], 1 if len(sides) > 1 else 2, frozenset(sides))
        elif r["name"].endswith("chest"):
            at.setdefault(tile, ("chest", r["name"], 2, frozenset()))
        if r["recipe"] and r["recipe"] in book.recipes:
            w, h = _size(r["name"], site)
            x0, y0 = math.floor(r["x"] - w / 2 + 0.5), math.floor(r["y"] - h / 2 + 0.5)
            tiles = {(x0 + i, y0 + j) for i in range(w) for j in range(h)}
            speed = (book.machines[r["name"]].speed if r["name"] in book.machines
                     else float(((facts.entities.get(r["name"]) if facts else None) or {}).get("speed") or 1))
            machines.append((r, tiles, speed))
    for r, tiles, _ in machines:   # (a machine as a source holds what its recipe makes)
        made = frozenset(n for n, _ in book.recipes[r["recipe"]].products)
        for t in tiles:
            at[t] = ("machine", None, 2, made)
    arms = [(r, *_ends(r, site)) for r in rows if r["name"].endswith("inserter")]
    out = []
    for r, tiles, speed in machines:
        rec = book.recipes[r["recipe"]]
        crafts = speed / max(rec.energy, 1e-9)
        need = {"in": crafts * sum(a for n, a in rec.ingredients if n not in fluids),
                "out": crafts * sum(a for n, a in rec.products if n not in fluids)}
        where = f"{r['name']} ({r['recipe']}) at {r['x']:.0f},{r['y']:.0f}"
        for side in ("in", "out"):
            moving = []
            for arm, pick, drop in arms:
                if side == "in" and drop in tiles and pick not in tiles:
                    kind, name, lanes, items = at.get(pick, ("belt", None, 2, frozenset()))   # (not in the plan: a
                    moving.append((arm["name"], kind, "machine", lanes, name, items))           # belt, the slower)
                elif side == "out" and pick in tiles and drop not in tiles:
                    kind, name, lanes, items = at.get(drop, ("belt", None, 2, frozenset()))
                    moving.append((arm["name"], "machine", kind, lanes, name, items))
            if not moving or need[side] <= 0:
                continue
            # Each ingredient against the arms that can bring it (their source belt carries it, by its lane contract):
            # two arms may cover the total while one ingredient's comes too slowly (Codex, L-4); then all of them
            # against the total, where what each brings is not known.
            # Inputs: each ingredient comes only by the arms whose source can hold it (a belt's lane contract, a
            # machine's products; a chest or anything not known, any of them), and an arm's time is shared by all it
            # brings: a flow from arms to ingredients says whether they can all come, and which comes short (two arms
            # may cover the total while one ingredient waits on one arm, or one arm serves two; Codex, L-4).
            if side == "in":
                wants = {item: crafts * amount for item, amount in rec.ingredients if item not in fluids}
                short_item = _feasible(moving, wants, site)
                if short_item is not None:
                    item, wanted, able = short_item
                    check = _short(able, wanted, f"{where} in ({item})", site, force=True)
                    if check is not None:
                        out.append(check)
                    continue
            check = _short(moving, need[side], f"{where} {side}", site)
            if check is not None:
                out.append(check)
    return out


def _one(m, site) -> float:
    belt = m[4] if (m[1] == "belt" or m[2] == "belt") else None
    return rate(m[0], site, source=m[1], target=m[2], lanes=m[3], belt_speed=_belt_speed(belt, site) if belt else None)


def _feasible(moving: list, wants: Mapping[str, float], site) -> tuple[str, float, list] | None:
    """Whether the arms can bring every ingredient at once, each arm's time shared by what it brings: for every set of
    ingredients, what they need together is no more than what the arms able to bring any of them move (the condition
    for such a flow to exist; recipes have few ingredients). None when they can, else (the set's most needed item,
    what the set needs, the arms that can bring it)."""
    from itertools import combinations
    arms = [(_one(m, site), m) for m in moving]
    items = sorted(wants, key=lambda i: -wants[i])[:10]
    worst = None
    for k in range(1, len(items) + 1):
        for group in combinations(items, k):
            able = [(cap, m) for cap, m in arms if not m[5] or any(i in m[5] for i in group)]
            need = sum(wants[i] for i in group)
            gap = need - sum(cap for cap, _ in able)
            if gap > 1e-6 and (worst is None or gap > worst[0]):
                worst = (gap, group[0], need, [m for _, m in able])
    return None if worst is None else worst[1:]


def _short(moving: list, wanted: float, label: str, site: Mapping[str, Any] | None, *,
           force: bool = False) -> Check | None:
    """Whether these arms move `wanted` items/s; else the Check with its fix, sized for their source and target.
    `force`: they are known short (their time is shared with other items), whatever their own total."""
    def one(m) -> float:
        return _one(m, site)
    if not moving:   # (no arm reaches anything that holds it: a way in first, not a faster arm)
        return Check(False, need=wanted, have=0.0, inserter="", count=0, fix="route", factor=0.0, where=label,
                     why=f"{label}: no inserter takes it from anything that holds it")
    have = sum(one(m) for m in moving)
    if have + 1e-9 >= wanted and not force:
        return None
    best = max(moving, key=one)
    belt = best[4] if "belt" in (best[1], best[2]) else None
    speed = _belt_speed(belt, site) if belt else None
    base = dict(need=wanted, have=have, inserter=best[0], count=len(moving), where=label)
    slots = max(SLOTS, len(moving))
    # The fix from the arms as they are, against the real need (Codex, L-4): more of the best one there, within the
    # slots; else the slowest swapped for the fastest it can make (and more of those within the slots); else the share
    # of its rate it runs at with the slots full of the fastest.
    r_best = one(best)
    extra = math.ceil((wanted - have) / r_best - 1e-9) if r_best > 0 else slots + 1
    if len(moving) + extra <= slots:
        check = Check(False, fix="add", to=best[0], to_count=len(moving) + extra,
                      why=f"{label}: {len(moving)} arms move {have:.2f}/s of {wanted:.2f}/s: {extra} more {best[0]}",
                      **base)
    else:
        def swap(kind: str) -> tuple[float, int, int]:
            """(what moves, swapped, added) with the slowest arms swapped for `kind`, then more of it in the slots."""
            # (each arm swapped where it stands: its own source, lanes and belt; Codex, L-4)
            def as_kind(m) -> float:
                b = m[4] if "belt" in (m[1], m[2]) else None
                return rate(kind, site, source=m[1], target=m[2], lanes=m[3],
                            belt_speed=_belt_speed(b, site) if b else None)
            r_kind = rate(kind, site, source=best[1], target=best[2], lanes=best[3], belt_speed=speed)
            gains = sorted((as_kind(m) - one(m) for m in moving), reverse=True)
            total, swapped, added = have, 0, 0
            while total + 1e-9 < wanted and swapped < len(gains) and gains[swapped] > 1e-9:
                total += gains[swapped]
                swapped += 1
            while total + 1e-9 < wanted and len(moving) + added < slots:
                total += r_kind
                added += 1
            return total, swapped, added
        kinds = makeable(site) or [best[0]]   # (slowest first: the cheapest that does it)
        top = next((k for k in kinds if swap(k)[0] + 1e-9 >= wanted), kinds[-1])
        total, swapped, added = swap(top)
        if total + 1e-9 >= wanted:
            check = Check(False, fix="upgrade", to=top, to_count=swapped + added,
                          why=f"{label}: {len(moving)} arms move {have:.2f}/s of {wanted:.2f}/s: {swapped} swapped "
                              f"for {top}" + (f" and {added} more" if added else ""), **base)
        else:
            check = Check(False, fix="slower", to=top, to_count=slots, factor=round(min(1.0, total / wanted), 3),
                          why=f"{label}: even {slots} {top} move {total:.2f}/s of {wanted:.2f}/s: it runs at "
                              f"{min(1.0, total / wanted):.0%}", **base)
    check.factor = round(have / wanted, 3)   # (the share of its rate it runs at as planned)
    return check
