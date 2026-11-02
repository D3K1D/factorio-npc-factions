"""The roboport grid: roboports over the base, their logistic areas overlapping so they make one network, powered and
joined to the base's poles, robots put in, a storage chest for what robots take down, and grown as the base grows
(the user, 2026-10-29: "setup roboports and the correct chests and items for them ... for robot use to expand the
factory automatically").

What is covered is what the base is made of: every part of every block (its machines where they stand, the parts
mod/scripts/base_memory.lua exports), not one box spanning far fields. Cells go on a grid anchored on the network's
first roboport (or home), spaced so neighbouring logistic areas overlap; each new one must touch a standing or planned
one, so the network stays one. A plan adds at most MAX_NEW roboports (the base grows in steps; the next plan adds more).
How far a roboport reaches comes from the game's facts (mods change it).

    due(site)                  # (True, why) when roboports can be made and part of the base is not covered
    plan(site, world)          # a plan: roboports, poles, a storage chest; robots supplied into the first roboport
    coverage(site)             # (covered share of the base's parts, uncovered points)
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

from companion.modules import Placed

PURPOSE = "robot-network"
LOGISTIC_RADIUS = 25      # a roboport's reach when the game's facts do not say (base game: 25 logistic, 55 building)
CONSTRUCTION_RADIUS = 55
OVERLAP = 4               # tiles neighbouring logistic areas overlap by (so the network is one)
MAX_NEW = 4               # roboports a plan adds at most
SAMPLE = 4                # the base's parts are checked every this many tiles
SEARCH = 6                # a roboport may stand this far off its grid point
PORT = 4                  # a roboport is 4x4
MARGIN = 2                # a part's edge this far out is covered too


def _radii(site: Mapping[str, Any]) -> tuple[float, float]:
    facts = site.get("game_facts") if isinstance(site.get("game_facts"), Mapping) else {}
    rows = facts.get("buildings") or facts.get("entities") or {}
    port = rows.get("roboport") if isinstance(rows, Mapping) else None
    port = port if isinstance(port, Mapping) else {}
    return (float(port.get("logistic_radius") or LOGISTIC_RADIUS),
            float(port.get("construction_radius") or CONSTRUCTION_RADIUS))


def _origin(site: Mapping[str, Any]) -> tuple[float, float]:
    origin = site.get("origin") or {"x": 0, "y": 0}
    return float(origin["x"]), float(origin["y"])


def boxes(site: Mapping[str, Any]) -> list[tuple[int, int, int, int]]:
    """The base's parts in world tiles: each block's parts where its machines stand (else its machines' box)."""
    ox, oy = _origin(site)
    out = []
    for block in site.get("base") or ():
        if not isinstance(block, Mapping) or block.get("purpose") == PURPOSE:
            continue
        parts = [p.get("box") for p in block.get("parts") or () if isinstance(p, Mapping)]
        if not parts and block.get("machines_box"):
            parts = [block["machines_box"]]
        for b in parts:
            try:
                x0, y0, x1, y1 = (float(v) for v in b[:4])
            except (TypeError, ValueError):
                continue
            out.append((math.floor(x0 + ox), math.floor(y0 + oy), math.ceil(x1 + ox), math.ceil(y1 + oy)))
    return out


def points(site: Mapping[str, Any]) -> list[tuple[int, int]]:
    """Points every SAMPLE tiles over the base's parts (and their edges), world tiles."""
    out = set()
    for x0, y0, x1, y1 in boxes(site):
        for x in range(x0 - MARGIN, x1 + MARGIN + 1, SAMPLE):
            for y in range(y0 - MARGIN, y1 + MARGIN + 1, SAMPLE):
                out.add((x, y))
        out |= {(x0 - MARGIN, y0 - MARGIN), (x1 + MARGIN, y1 + MARGIN), (x0 - MARGIN, y1 + MARGIN),
                (x1 + MARGIN, y0 - MARGIN)}
    return sorted(out)


def cells(site: Mapping[str, Any]) -> list[tuple[float, float, float]]:
    """Roboports standing in its networks: (x, y, logistic radius), world tiles."""
    out = []
    for net in site.get("logistics") or ():
        for c in (net or {}).get("cells") or ():
            try:
                out.append((float(c["x"]), float(c["y"]), float(c.get("logistic") or LOGISTIC_RADIUS)))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _covered(p: tuple[float, float], at: Iterable[tuple[float, float, float]]) -> bool:
    return any(abs(p[0] - x) <= r and abs(p[1] - y) <= r for x, y, r in at)


def coverage(site: Mapping[str, Any]) -> tuple[float, list[tuple[int, int]]]:
    """The share of the base's parts inside a logistic area, and the points that are not."""
    pts, standing = points(site), cells(site)
    if not pts:
        return 1.0, []
    missing = [p for p in pts if not _covered(p, standing)]
    return 1.0 - len(missing) / len(pts), missing


def _makeable(site: Mapping[str, Any], item: str) -> bool:
    rows = site.get("recipes")
    if not isinstance(rows, list) or not rows:
        return False
    return any(isinstance(r, Mapping) and r.get("name") == item for r in rows)


NEEDS = ("roboport", "construction-robot")   # what a cell needs that research unlocks


def due(site: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether the grid should grow: roboports and robots can be made, it did not just fail here, and part of the base
    lies outside every logistic area."""
    missing = [n for n in NEEDS if not _makeable(site, n)]
    if missing:
        return False, "needs research first: " + ", ".join(missing)
    if PURPOSE in {str(p) for p in site.get("failed_purposes") or ()}:
        return False, "failed here recently"
    share, missing = coverage(site)
    if not missing:
        return False, "the base is covered"
    return True, f"{share:.0%} of the base is in robot range; {len(missing)} spots are not"


def _grid(anchor: tuple[float, float], spacing: float, p: tuple[float, float]) -> tuple[int, int]:
    """The grid point nearest p."""
    gx = anchor[0] + round((p[0] - anchor[0]) / spacing) * spacing
    gy = anchor[1] + round((p[1] - anchor[1]) / spacing) * spacing
    return int(round(gx)), int(round(gy))


RING = [(dx, dy) for dx in range(-3, 3) for dy in range(-3, 3) if dx in (-3, 2) or dy in (-3, 2)]   # pole spots


def _pole(at: tuple[int, int], taken: set[tuple[int, int]]) -> tuple[int, int] | None:
    """A tile beside a roboport centred at `at` for its pole (nearest its middle first), or None."""
    return next(((at[0] + dx, at[1] + dy) for dx, dy in sorted(RING, key=lambda d: (abs(d[0] + 0.5) + abs(d[1] + 0.5), d))
                 if (at[0] + dx, at[1] + dy) not in taken), None)


def _spot(at: tuple[int, int], taken: set[tuple[int, int]], ok=lambda spot: True) -> tuple[int, int] | None:
    """A 4x4 spot for a roboport within SEARCH of a grid point (its centre on a tile corner), nearest first, that
    `ok` accepts."""
    for d in range(SEARCH + 1):
        ring = [(at[0] + dx, at[1] + dy) for dx in range(-d, d + 1) for dy in range(-d, d + 1)
                if max(abs(dx), abs(dy)) == d]
        for cx, cy in sorted(ring, key=lambda t: (abs(t[0] - at[0]) + abs(t[1] - at[1]), t)):
            tiles = {(cx - 2 + i, cy - 2 + j) for i in range(PORT) for j in range(PORT)}
            if not tiles & taken and ok((cx, cy)):
                return cx, cy
    return None


def layout(site: Mapping[str, Any], taken: set[tuple[int, int]], home: tuple[float, float]) -> list[tuple[int, int]]:
    """Where new roboports go (world tile corners, their centres): grid points covering the most of what is not
    covered, each touching the network (a standing or already chosen cell within a spacing), at most MAX_NEW."""
    radius, _ = _radii(site)
    spacing = max(2 * radius - OVERLAP, PORT + 1)
    standing = cells(site)
    anchor = (standing[0][0], standing[0][1]) if standing else (float(home[0]), float(home[1]))
    _, missing = coverage(site)
    missing = set(missing)
    chosen: list[tuple[int, int]] = []
    occupied = set(taken)
    while missing and len(chosen) < MAX_NEW:
        net = [(x, y) for x, y, _ in standing] + [(float(x), float(y)) for x, y in chosen]
        candidates: dict[tuple[int, int], int] = {}
        for p in missing:
            g = _grid(anchor, spacing, p)
            candidates[g] = candidates.get(g, 0) + 1
        # (one network: each new cell's area touches a standing or chosen one, judged where it really stands, a spot
        # moved off its grid point included; the first may stand alone)
        # (and room for its pole: a roboport with none would charge nothing, and leaving it out afterwards could split
        # the chain, Codex, L-3)
        def joins(spot: tuple[int, int], net=net) -> bool:
            port = {(spot[0] - 2 + i, spot[1] - 2 + j) for i in range(PORT) for j in range(PORT)}
            if _pole(spot, occupied | port) is None:
                return False
            return not net or any(abs(spot[0] - x) <= 2 * radius and abs(spot[1] - y) <= 2 * radius for x, y in net)
        best = None
        for g, n in sorted(candidates.items(), key=lambda kv: (-kv[1], kv[0])):
            if net and not any(abs(g[0] - x) <= spacing + 1 and abs(g[1] - y) <= spacing + 1 for x, y in net):
                continue
            spot = _spot(g, occupied, joins)
            if spot is not None:
                best = spot
                break
        if best is None and net:
            # (nothing within reach covers an open part: grow the network a cell toward them, trying every grid
            # neighbour of its cells, nearest the open parts first; one step blocked must not stop it)
            near = sorted({(int(round(x + dx * spacing)), int(round(y + dy * spacing))) for x, y in net
                           for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy},
                          key=lambda g: (min(math.dist(g, p) for p in missing), g))
            for g in near[:24]:
                best = _spot(g, occupied, joins)
                if best is not None:
                    break
        if best is None:
            break
        chosen.append(best)
        occupied |= {(best[0] - 2 + i, best[1] - 2 + j) for i in range(PORT) for j in range(PORT)}
        occupied.add(_pole(best, occupied))   # (its pole's tile kept for it)
        missing = {p for p in missing if not (abs(p[0] - best[0]) <= radius and abs(p[1] - best[1]) <= radius)}
    return chosen


def plan(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """The grid's next cells as a plan: roboports, the poles that power them, a storage chest by the first one where its
    networks have none, and construction robots (and logistic ones once researched) put into the first roboport,
    as many as the network ledger says it should have."""
    from companion import layout_planner, network_ledger
    from companion.base_plan import blocked_tiles
    from companion.power import connect
    from companion.smelting import footprint
    blocked = set(blocked_tiles(world))
    home = (float(world["home"]["x"]), float(world["home"]["y"])) if world.get("home") else _origin(site)
    spots = layout(site, blocked, home)
    if not spots:
        raise layout_planner.PlanError("no roboport spot covers the base's open parts")
    placed = [Placed("roboport", float(x), float(y)) for x, y in spots]
    # Only poles on a network a generator feeds count: a pole group nothing feeds charges no roboport (Codex, L-3).
    live = set(world.get("live_nets") or ())
    powered = [(e["x"], e["y"]) for e in world.get("entities", [])
               if e.get("t") == "electric-pole" and e.get("f") == world.get("force") and e.get("net") in live]
    if not powered:
        raise layout_planner.PlanError("robot network: no powered poles to join")
    # (a pole beside each roboport, joined to the base's network: layout() chose only spots with room for one)
    taken = blocked | footprint(placed)
    for x, y in spots:
        pole = _pole((x, y), taken)
        placed.append(Placed("medium-electric-pole" if _makeable(site, "medium-electric-pole")
                             else "small-electric-pole", pole[0] + 0.5, pole[1] + 0.5))
        taken.add(pole)
    # (a storage chest for what robots take down, by the first roboport, where its networks have none)
    storages = sum(int(((n or {}).get("chests") or {}).get("storages") or 0) for n in site.get("logistics") or ())
    if storages == 0 and _makeable(site, "storage-chest"):
        chest = _pole(spots[0], taken)
        if chest:
            placed.append(Placed("storage-chest", chest[0] + 0.5, chest[1] + 0.5))
            taken.add(chest)
    extra, problems = connect(placed, powered, set(taken))
    notes = []
    if problems:
        # Ground kept for later sections (the factory plan's zones, the bus's planned ground) may wall the poles off;
        # poles are small and come down easily, so they cross it when nothing else gets through, as the bus's belts
        # do (the robots stage: 43,743 kept tiles, no pole route to a network 16 tiles away, 2026-10-29).
        from companion import land
        # (the soft zones only: the copy drops them and their cached tiles; the bus's own ground, reserved_hard, stays)
        kept = land.reserved_tiles(world) - blocked_tiles({**world, "reserved": None, "_reserved_tiles": None})
        extra, problems = connect(placed, powered, set(taken) - kept)
        if not problems:
            notes.append("its poles cross ground kept for later sections")
    if problems:
        _capture(site, world, placed, problems)
        raise layout_planner.PlanError(f"robot network: {problems[0]}")
    placed += extra
    ox, oy = _origin(site)
    out = [e.to_plan(f"robo-{n}", -ox, -oy) for n, e in enumerate(placed, start=1)]
    first = next(e["id"] for e in out if e["prototype"] == "roboport")
    book = network_ledger.NetworkBook.from_site(site)
    supplies = []
    for item in network_ledger.ROBOT_ITEMS:
        want = book.item(item)
        # (what the network is short of, not its whole target again: the robots it has stay; a roboport holds 50.
        # Construction robots for the cells this plan adds too: the ledger counts only those standing)
        short = want.short if want is not None else 0
        if item == "construction-robot" and want is not None:
            short = max(short, want.wanted + network_ledger.ROBOTS_PER_CELL * len(spots) - want.stock)
        if short > 0 and _makeable(site, item):
            supplies.append({"entity_id": first, "item": item, "slot": "robot", "count": min(short, 50)})
    share, missing = coverage(site)
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"{PURPOSE}-{safe}", "source": "robo-grid", "purpose": PURPOSE, "entities": out,
            "notes": [f"{len(spots)} roboports: {share:.0%} of the base in robot range before, {len(missing)} spots out",
                      *notes],
            "stages": [{"id": "robot-network", "depends_on": [], "entity_ids": [e["id"] for e in out],
                        "manual_supplies": supplies,
                        "witnesses": [{"kind": "entity_built", "entity_id": first},
                                      {"kind": "power_connected", "entity_id": first}]}]}


def _capture(site: Mapping[str, Any], world: Mapping[str, Any], placed: list, problems: list[str]) -> None:
    """What a failed plan saw, once a run (data/failures/robot-network.json): to find why later, not to guess."""
    import json
    import os
    from pathlib import Path
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    path = Path(folder) / "failures" / "robot-network.json"
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"problems": problems, "placed": [(e.name, e.x, e.y) for e in placed],
                                    "site": site, "world": world}, default=str), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass



# --- the network's own supply: a maker cell for what its robots use up -------------------------------------------

SUPPLY = "robot-supply"
SUPPLY_ITEMS = ("repair-pack",)     # what the robots use up and the network makes for itself
CELL_CHESTS = ("requester-chest", "passive-provider-chest")
REQUEST_CRAFTS = 20                 # a requester asks enough for this many crafts
PROVIDER_SLOTS = 4                  # the provider chest's limit: a few stacks, then the maker waits


def _maker(site: Mapping[str, Any]) -> str | None:
    rows = {str(r.get("name")) for r in site.get("recipes") or () if isinstance(r, Mapping)}
    return next((m for m in ("assembling-machine-2", "assembling-machine-1") if m in rows), None)


def _pack_target(net: Mapping[str, Any], attacked: bool) -> int:
    """Repair packs one network keeps (as network_ledger's: a floor, a share per wall or turret, twice under attack)."""
    from companion import network_ledger as nl
    want = min(nl.REPAIR_CAP, nl.REPAIR_FLOOR + math.ceil(nl.REPAIR_PER_DEFENSE * int(net.get("defenses") or 0)))
    return min(nl.REPAIR_CAP, 2 * want) if attacked else want


def _in(net: Mapping[str, Any], x: float, y: float) -> bool:
    return any(abs(x - float(c["x"])) <= float(c.get("logistic") or LOGISTIC_RADIUS)
               and abs(y - float(c["y"])) <= float(c.get("logistic") or LOGISTIC_RADIUS)
               for c in net.get("cells") or () if isinstance(c, Mapping) and "x" in c)


def _short_networks(site: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Networks short of repair packs, each judged on its own (one well stocked must not hide one bare: Codex, L-5),
    with logistic robots to serve a requester (or robots the cell can bring), and no maker cell of their own yet: none
    in the base memory's plans, none seen live by the game's export ("makers")."""
    ox, oy = _origin(site)
    cells_at = []
    for b in site.get("base") or ():
        if isinstance(b, Mapping) and b.get("purpose") == SUPPLY:
            try:
                x0, y0, x1, y1 = (float(v) for v in b["box"][:4])
            except (KeyError, TypeError, ValueError):
                return []    # (a cell not measured yet: wait for its box rather than build a second one)
            cells_at.append(((x0 + x1) / 2 + ox, (y0 + y1) / 2 + oy))
    attacked = bool(site.get("under_attack"))
    out = []
    for net in site.get("logistics") or ():
        if not isinstance(net, Mapping):
            continue
        if int((net.get("robots") or {}).get("logistic") or 0) < 1 and not (_makeable(site, "logistic-robot")
                                                                            and _makeable(site, "roboport")):
            continue
        if int(net.get("makers") or 0) > 0 or any(_in(net, x, y) for x, y in cells_at):
            continue
        if int(net.get("repair_packs") or 0) < _pack_target(net, attacked):
            out.append(net)
    return out


def supply_due(site: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether a network should make what its robots use up (repair packs): a network of its own is short, has logistic
    robots for the cell's requester, and has no maker cell yet; the cell's chests, machine and item can be made."""
    if not site.get("logistics"):
        return False, "no robot network yet"
    missing = [n for n in (*CELL_CHESTS, *SUPPLY_ITEMS) if not _makeable(site, n)]
    if missing or _maker(site) is None:
        return False, "needs research first: " + ", ".join(missing or ["an assembling machine"])
    if SUPPLY in {str(p) for p in site.get("failed_purposes") or ()}:
        return False, "failed here recently"
    short = _short_networks(site)
    if not short:
        return False, "every network with logistic robots holds or makes the repair packs it needs"
    net = short[0]
    return True, (f"network {net.get('id')} holds {int(net.get('repair_packs') or 0)} repair packs of "
                  f"{_pack_target(net, bool(site.get('under_attack')))}: a maker cell in it")


def supply_item(site: Mapping[str, Any]) -> str | None:
    """The item a maker cell is due for, if any."""
    return SUPPLY_ITEMS[0] if _short_networks(site) else None


def supply_plan(site: Mapping[str, Any], world: Mapping[str, Any]) -> dict:
    """A maker cell inside the network's logistic area: a requester chest asking the network for the recipe's
    ingredients, an inserter, the assembler, an inserter, and a limited passive-provider chest its robots take from
    (the user's "Late Game Robot Builds" cells are the same shape). Powered from the base's live poles."""
    from companion import layout_planner, ratios
    from companion.base_plan import blocked_tiles
    from companion.modules import NORTH
    from companion.power import connect
    from companion.smelting import footprint
    item = supply_item(site)
    if item is None:
        raise layout_planner.PlanError("nothing for the robot network to make")
    machine = _maker(site)
    recipe = ratios.Book.from_site(site).recipes.get(item)
    if recipe is None or machine is None:
        raise layout_planner.PlanError(f"no recipe for {item}")
    blocked = set(blocked_tiles(world))
    live = set(world.get("live_nets") or ())
    powered = [(e["x"], e["y"]) for e in world.get("entities", [])
               if e.get("t") == "electric-pole" and e.get("f") == world.get("force") and e.get("net") in live]
    if not powered:
        raise layout_planner.PlanError("robot supply: no powered poles to join")

    def cell(x: int, y: int) -> list[Placed]:
        """The column at (x, y): chest, inserter, 3x3 machine, inserter, chest, a pole beside, top to bottom."""
        asks = {"sections": [{"index": 1, "filters": [
            {"index": i, "name": name, "quality": "normal", "comparator": "=",
             "count": max(1, int(round(amount * REQUEST_CRAFTS)))}
            for i, (name, amount) in enumerate(recipe.ingredients, start=1)]}]}
        req = Placed("requester-chest", x + 0.5, y + 0.5)
        req.settings = {"request_filters": asks}
        out = [req, Placed("inserter", x + 0.5, y + 1.5, NORTH),
               Placed(machine, x + 0.5, y + 3.5, NORTH, item),
               Placed("inserter", x + 0.5, y + 5.5, NORTH),
               Placed("passive-provider-chest", x + 0.5, y + 6.5, NORTH, bar=PROVIDER_SLOTS),
               Placed("medium-electric-pole" if _makeable(site, "medium-electric-pole") else "small-electric-pole",
                      x + 2.5, y + 1.5)]
        return out

    spot = None
    short = _short_networks(site)
    ports = [(float(c["x"]), float(c["y"]), float(c.get("logistic") or LOGISTIC_RADIUS))
             for c in (short[0].get("cells") or ()) if isinstance(c, Mapping)] if short else []
    for cx, cy, r in ports:   # (in the short network's own area)
        for dx in range(-int(r) + 4, int(r) - 4, 3):
            for dy in (-int(r) + 4, int(r) - 12, 6, -14):
                parts = cell(int(cx) + dx, int(cy) + dy)
                if not footprint(parts) & blocked:
                    spot = parts
                    break
            if spot:
                break
        if spot:
            break
    if spot is None:
        raise layout_planner.PlanError(f"no room for a {item} maker in the robot network")
    supplies = []
    if int((short[0].get("robots") or {}).get("logistic") or 0) < 1:
        # (a network with no logistic robot would leave the requester empty: the cell brings a roboport beside it with
        # robots to serve it, joining the same network)
        x0 = int(min(e.x for e in spot)) + 3
        y0 = int(min(e.y for e in spot))
        port = next((Placed("roboport", x0 + 2 + dx, y0 + 2 + dy) for dx in (0, 1, 2, 3) for dy in (0, -2, 2, -4, 4)
                     if not footprint([Placed("roboport", x0 + 2 + dx, y0 + 2 + dy)]) & (blocked | footprint(spot))),
                    None)
        if port is None:
            raise layout_planner.PlanError("robot supply: no room for a roboport to bring its logistic robots")
        spot = spot + [port]
    extra, problems = connect(spot, powered, blocked | footprint(spot))
    if problems:
        raise layout_planner.PlanError(f"robot supply: {problems[0]}")
    placed = spot + extra
    ox, oy = _origin(site)
    out = []
    for n, e in enumerate(placed, start=1):
        row = e.to_plan(f"supply-{n}", -ox, -oy)
        row.update(getattr(e, "settings", None) or {})
        out.append(row)
    made = next(e["id"] for e in out if e.get("recipe") == item)
    port_id = next((e["id"] for e in out if e["prototype"] == "roboport"), None)
    if port_id is not None:
        from companion import network_ledger
        need = network_ledger.NetworkBook.from_site(site).item("logistic-robot")
        count = max(network_ledger.LOGISTIC_PER_REQUESTER, min(need.short if need is not None else 0, 50))
        supplies.append({"entity_id": port_id, "item": "logistic-robot", "slot": "robot", "count": min(count, 50)})
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"plan_id": f"{SUPPLY}-{safe}", "source": "robo-grid", "purpose": SUPPLY, "entities": out,
            "notes": [f"a {item} maker cell: its inputs asked of the network, its packs into a provider chest"],
            "stages": [{"id": SUPPLY, "depends_on": [], "entity_ids": [e["id"] for e in out], "manual_supplies": supplies,
                        "witnesses": [{"kind": "entity_built", "entity_id": made},
                                      {"kind": "power_connected", "entity_id": made}]}]}
