"""A proven nuclear book, stamped: the user's pick, Kingdud's "Space Age: Tileable Nuclear Setups, Peak and Base Load"
(OneDrive Blueprints\\nuclear). Its pieces (1c, 2c, 4c, 8c, by reactor count) grow in place: each bigger piece holds the
smaller one, so the plant starts at 2c (the neighbour bonus) and is upgraded on the same spot.

Each piece as the engineer builds it:
- its circuits left out (a plan carries no wires: its inserters then fuel whenever a reactor's fuel slot is empty, as the
  book's base load pieces do); its steam tanks too when room is short (optional parts, the user's rule), the rows they
  took closed up so the exchangers' steam pipes meet the turbines' (tank_band);
- its ports read from its own pieces: each fuel belt's upstream end (the cells come in there), each pipe-to-ground
  whose pair is outside the piece (its water comes in there: the pair tiled pieces share);
- spent cells taken out: the book leaves them in the reactor, which stops once its spent slot fills (read live on 2.1).
  An inserter beside each reactor puts them on the fuel belt's far lane (the cells come in on the near lane), and a
  filtered inserter at the belt's end takes them into a chest.
Sizes come from the game's facts, connections from the pieces themselves.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

from companion import nuclear_cell
from companion.modules import EAST, NORTH, Placed, SOUTH, WEST

STEP = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
FACING = {v: k for k, v in STEP.items()}
CIRCUITS = ("decider-combinator", "arithmetic-combinator", "constant-combinator", "selector-combinator",
            "power-switch", "programmable-speaker", "small-lamp")
BAND = ("storage-tank",)                     # what a closed band may hold (with poles, which move with what they power)
POLES = ("small-electric-pole", "medium-electric-pole", "big-electric-pole", "substation")
SPENT = "depleted-uranium-fuel-cell"
SINK_INSERTER = "fast-inserter"              # (2.x: filters on the fast inserter)
ADDED = ("inserter", "transport-belt", SINK_INSERTER, "iron-chest")   # (what spent() and the feeds add)
PIPE_REACH = 10                              # pipe-to-ground span (vanilla 2.x)
TIER = re.compile(r"\b(\d+)\s*c\b", re.I)


def load(folder: Path | None = None) -> dict[str, dict]:
    """The book's pieces by label, from the packs folder's nuclear\\ books (blueprint_packs.default_folder); empty when
    there is none."""
    from companion import blueprint_packs as bpk
    folder = Path(folder) if folder else bpk.default_folder()
    out: dict[str, dict] = {}
    if folder is None:
        return out
    for file in sorted((folder / "nuclear").glob("*.txt")) if (folder / "nuclear").is_dir() else []:
        for _, s in bpk.strings(file.read_text(encoding="utf-8")):
            try:
                decoded = bpk.decode(s)
            except Exception:
                continue
            for _, bp in bpk.blueprints(decoded):
                if bp.get("label") and TIER.search(str(bp["label"])):
                    out[str(bp["label"])] = bp
    return out


def labels(book: Mapping[str, dict], n: int) -> list[str]:
    """The pieces of `n` reactors that grow in place (not an end piece), base load first."""
    return sorted((l for l in book if "end" not in l.lower() and int(TIER.search(l).group(1)) == n),
                  key=lambda l: ("base" not in l.lower(), l))


def tiers(book: Mapping[str, dict]) -> list[tuple[int, str]]:
    """The pieces that grow in place, smallest first: one per reactor count (base load first, not an end piece)."""
    best: dict[int, str] = {}
    for label in book:
        low = label.lower()
        if "end" in low:
            continue
        n = int(TIER.search(label).group(1))
        if n not in best or ("base" in low and "base" not in best[n].lower()):
            best[n] = label
    return sorted(best.items())


def _tiles(facts, e: Placed) -> set[tuple[int, int]]:
    f = (facts.entities.get(e.name) if facts else None) or {}
    w, h = int(f.get("width") or 1), int(f.get("height") or f.get("width") or 1)
    return nuclear_cell.tiles_of((e.x, e.y), w, h, e.direction)


def piece(site: Mapping[str, Any], bp: Mapping[str, Any], tanks: bool = True, buildable: bool = True) -> dict:
    """A piece as Placed rows (its circuits left out; its tank band closed when `tanks` is False), its ports, and the
    spent cells' inserters and sinks added. Raises ValueError when it is not a nuclear piece this mod set can build,
    or (with `buildable`) one with a piece the engineer cannot make yet (substations before their research)."""
    from companion import blueprint_packs as bpk, rates
    from companion.oil_plant import _at_hand
    facts = rates.of(site)
    plain = {**bp, "entities": [e for e in bp.get("entities") or [] if e.get("name") not in CIRCUITS]}
    protos = bpk.facts_prototypes(site)
    converted, _, missing = bpk.convert(plain, protos)
    rows = converted["entities"]
    if missing:
        raise ValueError(f"the piece needs what this mod set lacks: {', '.join(missing[:3])}")
    if buildable:
        # (every piece it keeps, its tanks too when they stay, and what it adds: the spent cells' inserters, belts,
        # filtered sink and chest)
        have = _at_hand(site)
        names = {e["prototype"] for e in rows if tanks or e["prototype"] not in BAND} | set(ADDED)
        needs = sorted({(protos.get(n) or {}).get("placement_item", n) for n in names} - have)
        if needs:
            raise ValueError(f"needs research first: {', '.join(needs[:3])}")
    placed = [(Placed(e["prototype"], e["position"]["x"], e["position"]["y"], int(e.get("direction") or 0),
                      e.get("recipe"), e.get("belt_to_ground_type")), e) for e in rows]
    if not tanks:
        placed = tank_band(facts, placed)
    ents = [p for p, _ in placed]
    reactors = [e for e in ents if ((facts.entities.get(e.name) or {}).get("kind") if facts else None) == "reactor"]
    if not reactors:
        raise ValueError("no reactor in the piece")
    at: dict[tuple[int, int], Placed] = {}
    for e in ents:
        for t in _tiles(facts, e):
            at[t] = e
    lines = fuel_lines(ents, at)
    if not lines:
        raise ValueError("no fuel belt in the piece")
    water = water_ports(ents, at)
    if not water:
        raise ValueError("no water inlet in the piece")
    extra, sinks = spent(facts, reactors, lines, at)
    # Each line's cells come in on a belt before its first (its head moved out one), fed from the reactor side: a
    # belt ending there sideloads them onto the near lane, away from the spent cells' lane.
    taken = set(at) | {t for e in extra for t in _tiles(facts, e)}
    for line, sink in zip(lines, sinks):
        head, side = line["in"], line["side"]
        feed = (head[0] + side[0], head[1] + side[1])
        if {head, feed} & taken:
            raise ValueError("no room before a fuel belt's head for the cells' belt")
        extra.append(Placed("transport-belt", head[0] + 0.5, head[1] + 0.5, line["direction"]))
        taken |= {head}
        dx, dy = STEP[line["direction"]]
        line.update(feed=feed, face=FACING[(-side[0], -side[1])], on=(sink["tail"][0] + dx, sink["tail"][1] + dy))
    return {"entities": ents + extra, "book": ents, "reactors": reactors, "lines": lines, "water": water,
            "sinks": sinks, "tiles": taken}


def tank_band(facts, placed: list[tuple[Placed, dict]]) -> list[tuple[Placed, dict]]:
    """The piece with its storage tanks left out and the rows they took closed up: a run of rows holding only tanks
    (and poles) is cut, what lies beyond it moved toward the reactors by its height."""
    rows: dict[int, set[str]] = {}
    for p, _ in placed:
        for _, y in _tiles(facts, p):
            rows.setdefault(y, set()).add(p.name)
    tanks = sorted(y for y, names in rows.items() if names & set(BAND) and names <= set(BAND) | set(POLES))
    if not tanks:
        return placed
    reactor_y = [p.y for p, _ in placed if p.name == "nuclear-reactor" or (facts and (facts.entities.get(p.name) or {})
                                                                             .get("kind") == "reactor")]
    mid = sum(reactor_y) / len(reactor_y) if reactor_y else 0
    bands, start = [], tanks[0]
    for a, b in zip(tanks, tanks[1:] + [None]):
        if b != a + 1:
            bands.append((start, a))
            start = b
    out = []
    for p, row in placed:
        ys = {y for _, y in _tiles(facts, p)}
        if any(lo <= min(ys) and max(ys) <= hi for lo, hi in bands) and p.name in BAND + POLES:
            continue   # (in a band: left out)
        shift = 0
        for lo, hi in bands:
            height = hi - lo + 1
            if lo > mid and min(ys) > hi:
                shift -= height   # (beyond a band below the reactors: up)
            elif hi < mid and max(ys) < lo:
                shift += height   # (beyond a band above them: down)
        if p.name in BAND:
            continue   # (a tank outside a closed band: its band was not all tanks; none is kept without its band)
        moved = Placed(p.name, p.x, p.y + shift, p.direction, p.recipe, p.kind)
        out.append((moved, {**row, "position": {"x": moved.x, "y": moved.y}}))
    return out


def fuel_lines(ents: list[Placed], at: Mapping[tuple[int, int], Placed]) -> list[dict]:
    """Each run of belts in the piece, upstream to downstream (undergrounds followed), with the tile before its first
    belt (where the cells come in) and the reactor side of it (where its fuel inserters stand)."""
    belts = {(math.floor(e.x), math.floor(e.y)): e for e in ents if e.name in ("transport-belt", "underground-belt")}
    ins = {(math.floor(e.x), math.floor(e.y)): e for e in ents if e.name.endswith("inserter")}

    def nxt(t):
        e = belts[t]
        dx, dy = STEP[e.direction % 16]
        if e.name == "underground-belt" and e.kind == "input":
            for k in range(1, 12):
                q = (t[0] + dx * k, t[1] + dy * k)
                f = belts.get(q)
                if f and f.name == "underground-belt" and f.kind == "output" and f.direction == e.direction:
                    return q
            return None
        q = (t[0] + dx, t[1] + dy)
        return q if q in belts else None
    fed = {q for q in (nxt(t) for t in belts) if q}
    out = []
    for head in sorted(t for t in belts if t not in fed):
        run, t = [], head
        while t and t not in run:
            run.append(t)
            t = nxt(t)
        e = belts[head]
        dx, dy = STEP[e.direction % 16]
        side = None   # (the side its fuel inserters stand on: they pick from it, drop into a reactor)
        for t in run:
            for s in ((dy, -dx), (-dy, dx)):
                i = ins.get((t[0] + s[0], t[1] + s[1]))
                if i and STEP[i.direction % 16] == (-s[0], -s[1]):
                    side = s
        if side is None:
            continue
        out.append({"tiles": run, "in": (head[0] - dx, head[1] - dy), "direction": e.direction % 16,
                    "side": side, "end": run[-1]})
    return out


def water_ports(ents: list[Placed], at: Mapping[tuple[int, int], Placed]) -> list[dict]:
    """The piece's pipes-to-ground whose pair lies outside it: the tile just outside the piece on that line where its
    pair goes, and the way on from there (its pair's own connection, facing out)."""
    ptg = {(math.floor(e.x), math.floor(e.y)): e for e in ents if e.name == "pipe-to-ground"}
    xs, ys = [t[0] for t in at], [t[1] for t in at]
    box = (min(xs), min(ys), max(xs), max(ys))
    out = []
    for t, e in sorted(ptg.items()):
        nx, ny = STEP[e.direction % 16]
        ux, uy = -nx, -ny   # (its underground runs away from its pipe connection)
        paired = False
        for k in range(1, PIPE_REACH + 2):
            q = (t[0] + ux * k, t[1] + uy * k)
            f = ptg.get(q)
            if f is not None:
                paired = STEP[f.direction % 16] == (ux, uy)
                break
        if paired:
            continue
        k = 1
        while box[0] <= t[0] + ux * k <= box[2] and box[1] <= t[1] + uy * k <= box[3]:
            k += 1
        q = (t[0] + ux * k, t[1] + uy * k)
        if k > PIPE_REACH + 1 or q in at:
            continue
        out.append({"pair": q, "pair_direction": FACING[(ux, uy)], "join": (q[0] + ux, q[1] + uy), "of": t})
    return out


def spent(facts, reactors: list[Placed], lines: list[dict], at: Mapping[tuple[int, int], Placed]
          ) -> tuple[list[Placed], list[dict]]:
    """An inserter beside each reactor taking its spent cells onto a fuel belt (a free tile between the reactor and the
    belt), and at each belt's end a filtered inserter into a chest. Raises ValueError for a reactor with no such
    tile."""
    belt = {t: line for line in lines for t in line["tiles"]}
    taken = set(at)
    extra: list[Placed] = []
    for r in reactors:
        body = _tiles(facts, r)
        spot = None
        for t in sorted({(x + dx, y + dy) for x, y in body for dx, dy in STEP.values()} - body - taken):
            for d, (dx, dy) in STEP.items():
                if (t[0] - dx, t[1] - dy) in body and (t[0] + dx, t[1] + dy) in belt:
                    spot = (t, FACING[(-dx, -dy)])   # (it picks from the reactor side)
                    break
            if spot:
                break
        if spot is None:
            raise ValueError("a reactor with no free tile for its spent cells' inserter")
        (x, y), d = spot
        extra.append(Placed("inserter", x + 0.5, y + 0.5, d))
        taken.add((x, y))
    sinks = []
    for line in lines:
        dx, dy = STEP[line["direction"]]
        end = line["end"]
        tail = (end[0] + dx, end[1] + dy)              # one belt more past its end
        far = (-line["side"][0], -line["side"][1])     # (the spent cells' lane: away from the reactors)
        grab = (tail[0] + far[0], tail[1] + far[1])
        chest = (grab[0] + far[0], grab[1] + far[1])
        if {tail, grab, chest} & taken:
            raise ValueError("no room past a fuel belt's end for its spent cells' chest")
        extra += [Placed("transport-belt", tail[0] + 0.5, tail[1] + 0.5, line["direction"]),
                  Placed(SINK_INSERTER, grab[0] + 0.5, grab[1] + 0.5, FACING[(-far[0], -far[1])]),
                  Placed("iron-chest", chest[0] + 0.5, chest[1] + 0.5)]
        taken |= {tail, grab, chest}
        sinks.append({"inserter": grab, "chest": chest, "tail": tail})
    return extra, sinks


def heat_problems(facts, ents: list[Placed]) -> list[str]:
    """The stamped piece's heat checked from the game's connection points: every heat exchanger's heat input joined
    to a reactor through heat pipes (nuclear_cell.check's heat rule; the book's steam runs through pipes and tanks)."""
    heat = {}
    for e in ents:
        f = (facts.entities.get(e.name) or {}) if facts else {}
        if f.get("heat"):
            for t in _tiles(facts, e):
                heat[t] = e
    pieces = {id(e): e for e in heat.values()}
    conns = {}
    for e in pieces.values():
        f = facts.entities[e.name]
        conns[id(e)] = [nuclear_cell.connection((e.x, e.y), e.direction, c) for c in f["heat"]]
    hot, todo = set(), [e for e in pieces.values() if (facts.entities[e.name].get("kind") == "reactor")]
    seen = {id(e) for e in todo}
    while todo:
        e = todo.pop()
        hot.add(id(e))
        for own, reach, _ in conns[id(e)]:
            o = heat.get(reach)
            if o is not None and id(o) not in seen and any(r == own for _, r, _ in conns[id(o)]):
                seen.add(id(o))
                todo.append(o)
    return [f"{e.name} at ({e.x}, {e.y}) gets no heat" for e in pieces.values()
            if facts.entities[e.name].get("kind") == "boiler" and id(e) not in hot]


def align(facts, smaller: list[Placed], bigger: list[Placed]) -> tuple[float, float] | None:
    """The shift that lays `smaller` onto the part of `bigger` it is (an in-place upgrade): the reactor-to-reactor
    shift under which the most of its pieces stand where the bigger one has the same piece; None when it is not inside."""
    have = {(e.name, e.x, e.y, e.direction) for e in bigger}
    best, score = None, 0
    small_r = [e for e in smaller if (facts.entities.get(e.name) or {}).get("kind") == "reactor"]
    big_r = [e for e in bigger if (facts.entities.get(e.name) or {}).get("kind") == "reactor"]
    if not small_r:
        return None
    for b in big_r:
        dx, dy = b.x - small_r[0].x, b.y - small_r[0].y
        hits = sum((e.name, e.x + dx, e.y + dy, e.direction) in have for e in smaller)
        if hits > score:
            best, score = (dx, dy), hits
    return best if best and score >= 0.9 * len(smaller) else None
