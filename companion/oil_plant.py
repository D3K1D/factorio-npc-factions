"""The oil plant: crude oil and water in, plastic and sulfur out, built to its ratios and running on its own.

What a player builds once oil is researched, instead of one refinery on one pumpjack:
- the best refining process this force has (advanced oil processing with heavy and light cracking once those
  are researched, else basic oil processing), chosen from the game's own recipes;
- as many chemical plants as the plastic and sulfur wanted need, and refineries and cracking plants sized to
  what those plants draw (all heavy oil cracked to light, all light to gas, so no refinery output backs up);
- pumpjacks on the field's wells for the crude, an offshore pump for the water;
- every solid product taken out by an inserter onto its own belt, which ends in a chest (its port: a belt to
  its consumer can start there); the plastic plants' coal is handed in.
Every rate comes from the game's recipes and machine speeds (the site export and the game's facts), mods
included.

Local layout (y down; chemical plants face south, refineries north):

    row 1       product belts (plastic west, sulfur east) into their end chests
    row 2       chemical plant outlets; item plants' output inserters between them
    rows 3..5   chemical plants: plastic, sulfur, then heavy and light cracking
    row 6       chemical plant inlets
    rows 7..9   pipes
    row 10      refinery outlets (heavy, light, gas)
    rows 11..15 refineries
    row 16      refinery inlets (water, crude); the crude and water ports just below
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import layout_planner, rates, team_area
from companion.modules import EAST, NORTH, Placed, SOUTH, WEST
from companion.oil_complex import _tiles
from companion.pipes import PipeMap

PLASTIC, SULFUR = "plastic-bar", "sulfur"
CRACKING = ("heavy-oil-cracking", "light-oil-cracking")
PROCESSES = ("advanced-oil-processing", "basic-oil-processing")
FLUIDS = ("crude-oil", "water", "heavy-oil", "light-oil", "petroleum-gas")
PLANT_Y, ROWS_UP = 3, 6              # chemical plants' top row, their inlet row
# Layouts tried in turn until every pipe routes: rows of pipes between the plants and the refineries, the
# plants' and the refineries' spacing, and the order the fluids are routed in (the first routed get the
# shortest ways).
GAS_FIRST = ("petroleum-gas", "light-oil", "heavy-oil", "water", "crude-oil")
# Cracking plants may face north, their oil and water in on the far side and their light oil or gas out
# straight into the pipe rows.
LAYOUTS = [{"rows": r, "refinery_step": rs, "plant_step": ps, "order": o, "crack": c}
           for r in (5, 3, 7) for c in (NORTH, SOUTH) for o in (GAS_FIRST, FLUIDS) for rs in (7, 8, 9) for ps in (6, 5, 7)]
WANT = {PLASTIC: 2.0, SULFUR: 1.0}    # items a second when nothing says more (blue science at about 1 a second)
MAX_PLANTS, MAX_REFINERIES, MAX_PUMPJACKS = 8, 6, 12
POLE_SUPPLY = 2.5
ROUTE_LIMIT = 20000   # search states for a pipe inside the block (small: a layout whose pipes do not fit fails fast)
SOFT_LIMIT = 30000    # search states for a pipe kept off another section's ground, before it may cross it


# --- the ratios ---------------------------------------------------------------------------------------

def _recipes(site: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out = {}
    for r in site.get("recipes") or []:
        if isinstance(r, Mapping) and r.get("name"):
            out[str(r["name"])] = r
    return out


def _amounts(rows: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows or []:
        if isinstance(row, Mapping) and row.get("name"):
            out[str(row["name"])] = out.get(str(row["name"]), 0.0) + float(row.get("amount") or 0)
    return out


def _speed(facts: rates.Facts | None, machine: str) -> float:
    entity = (facts.entities.get(machine) if facts else None) or {}
    return float(entity.get("speed") or 1.0)


def _per_second(recipe: Mapping[str, Any], speed: float) -> tuple[dict[str, float], dict[str, float]]:
    """(takes, makes) a second, of one machine of `speed` running `recipe`."""
    seconds = float(recipe.get("energy") or 1.0) / max(speed, 1e-9)
    return ({k: v / seconds for k, v in _amounts(recipe.get("ingredients")).items()},
            {k: v / seconds for k, v in _amounts(recipe.get("products")).items()})


def sizing(site: Mapping[str, Any], want: Mapping[str, float] | None = None, cap: int | None = None) -> dict | None:
    """The machine counts for `want` (items a second of plastic and sulfur; what is not unlocked is left out),
    with the refining process that needs the fewest refineries. `cap`: at most this many refineries (what its field's
    crude runs: the chemical plants then run below full). None when no process is unlocked."""
    recipes, facts = _recipes(site), rates.of(site)
    want = dict(WANT if want is None else want)
    plants = {item: n for item, n in want.items() if item in recipes and n > 0}
    if not plants:
        return None
    chem, refinery = _speed(facts, "chemical-plant"), _speed(facts, "oil-refinery")
    counts, draw = {}, {}
    for item, rate in plants.items():
        takes, makes = _per_second(recipes[item], chem)
        n = max(1, min(MAX_PLANTS, math.ceil(rate / max(makes.get(item, 0.0), 1e-9) - 1e-6)))
        counts[item] = n
        for k, v in takes.items():
            draw[k] = draw.get(k, 0.0) + v * n
    gas = draw.get("petroleum-gas", 0.0)
    best = None
    for process in PROCESSES:
        if process not in recipes:
            continue
        takes, makes = _per_second(recipes[process], refinery)
        heavy, light, g = makes.get("heavy-oil", 0.0), makes.get("light-oil", 0.0), makes.get("petroleum-gas", 0.0)
        cracks = {}
        if heavy or light:
            # Heavy and light oil must go somewhere, or the refinery stops: cracked to gas when both crackings
            # are unlocked; else this process is no use here.
            if not all(c in recipes for c in CRACKING):
                continue
            h_takes, h_makes = _per_second(recipes[CRACKING[0]], chem)
            l_takes, l_makes = _per_second(recipes[CRACKING[1]], chem)
            h_in, h_out = h_takes.get("heavy-oil", 0.0), h_makes.get("light-oil", 0.0)
            l_in, l_out = l_takes.get("light-oil", 0.0), l_makes.get("petroleum-gas", 0.0)
            if not (h_in and h_out and l_in and l_out):
                continue
            light_all = light + heavy / h_in * h_out          # a second, per refinery
            g = g + light_all / l_in * l_out
            cracks = {"heavy": heavy / h_in, "light": light_all / l_in}   # plants per refinery
        if g <= 0:
            continue
        refineries = max(1, math.ceil(gas / g - 1e-6))
        if best is None or refineries < best["refineries"]:
            best = {"process": process, "refineries": refineries, "gas_per_refinery": g, "takes": takes,
                    "cracks": cracks}
    if best is None:
        return None
    if cap is not None:
        best["refineries"] = max(1, min(best["refineries"], cap))
    elif best["refineries"] > MAX_REFINERIES:
        # More than one plant should hold: scale the products down to what MAX_REFINERIES give.
        scale = MAX_REFINERIES * best["gas_per_refinery"] / gas
        return sizing(site, {k: v * scale * 0.999 for k, v in want.items()})
    n = best["refineries"]
    out = {"process": best["process"], "refineries": n, "plants": counts,
           "cracking": {CRACKING[0]: math.ceil(best["cracks"].get("heavy", 0) * n - 1e-6),
                        CRACKING[1]: math.ceil(best["cracks"].get("light", 0) * n - 1e-6)} if best["cracks"] else {},
           "crude": best["takes"].get("crude-oil", 0.0) * n,
           "gas_made": best["gas_per_refinery"] * n, "gas_drawn": gas}
    water = best["takes"].get("water", 0.0) * n + draw.get("water", 0.0)
    for c, k in out["cracking"].items():
        water += _per_second(recipes[c], chem)[0].get("water", 0.0) * k
    out["water"] = water
    return out


def missing_products(site: Mapping[str, Any]) -> list[str]:
    """Products unlocked since the engineer's oil plants were built that none of them makes (sulfur after a
    plastic-only plant): a plant for those is offered again. Empty before any oil processing stands."""
    blocks = [b for b in site.get("base") or [] if isinstance(b, Mapping) and b.get("purpose") == "oil-processing"]
    if not blocks:
        return []
    made = {str(r) for b in blocks for r in (b.get("recipes") if isinstance(b.get("recipes"), list) else [])}
    recipes = _recipes(site)
    return [item for item in (PLASTIC, SULFUR) if item in recipes and item not in made]


def well_rate(amount: float, normal: float = 300000.0, base: float = 10.0) -> float:
    """Crude a second a pumpjack draws from a well of `amount` (100% is `base` a second, per `normal`)."""
    return max(0.1, base * float(amount or 0) / normal)


# --- the block ---------------------------------------------------------------------------------------

def _fluid_ingredients(recipe: Mapping[str, Any]) -> list[str]:
    return [r["name"] for r in recipe.get("ingredients") or [] if isinstance(r, Mapping) and r.get("name") in FLUIDS]


def _fluid_products(recipe: Mapping[str, Any]) -> list[str]:
    return [r["name"] for r in recipe.get("products") or [] if isinstance(r, Mapping) and r.get("name") in FLUIDS]


def block(site: Mapping[str, Any], size: Mapping[str, Any]) -> dict:
    """The plant in local tiles: {"entities": [Placed], "pipes": {tile: fluid}, "ports": {name: tile},
    "supplies": [...], "electric": [Placed], "shape": tiles}, in the first of LAYOUTS whose pipes all route.
    Raises PlanError when none does."""
    failed = None
    for layout in LAYOUTS:
        try:
            return _block(site, size, layout)
        except layout_planner.PlanError as exc:
            failed = exc
    raise failed or layout_planner.PlanError("oil plant: no layout")


def _block(site: Mapping[str, Any], size: Mapping[str, Any], layout: Mapping[str, Any]) -> dict:
    recipes = _recipes(site)
    PLANT_STEP, REFINERY_STEP = layout["plant_step"], layout["refinery_step"]
    REFINERY_Y = ROWS_UP + layout["rows"] + 2
    ROWS_DOWN = REFINERY_Y + 5
    ents: list[Placed] = []
    used: set[tuple[int, int]] = set()
    pipes = PipeMap()
    members: dict[str, list[tuple[int, int]]] = {f: [] for f in FLUIDS}

    def put(name, x, y, direction=NORTH, recipe=None, w=1, h=1):
        e = Placed(name, x + w / 2, y + h / 2, direction, recipe)
        ents.append(e)
        used.update(_tiles(x, y, w, h))
        return e

    # Chemical plants, west to east: plastic, sulfur (each item plant's inserter and belt above), cracking.
    x, row = 3, []
    for item in (PLASTIC, SULFUR):
        for _ in range(size["plants"].get(item, 0)):
            row.append((item, item, x))
            x += PLANT_STEP
        if item == PLASTIC and size["plants"].get(PLASTIC):
            x += 1
    sulfur_end = x
    if size["plants"].get(SULFUR):
        x += 4                       # (the sulfur belt's end, its inserter and chest)
    for recipe in CRACKING:
        for _ in range(size["cracking"].get(recipe, 0)):
            row.append((recipe, recipe, x))
            x += PLANT_STEP
    width_plants = x
    for name, recipe, px in row:
        facing = layout["crack"] if recipe in CRACKING else SOUTH
        put("chemical-plant", px, PLANT_Y, facing, recipe, 3, 3)
        fluids_in = _fluid_ingredients(recipes[recipe])
        # Facing south, the inlets are on the south side (row 6), the first fluid ingredient on the east one;
        # facing north, on the north side, the first on the west one; the outlets on the other side.
        inlet_row, outlet_row = (ROWS_UP, PLANT_Y - 1) if facing == SOUTH else (PLANT_Y - 1, ROWS_UP)
        inlets = [(px + 2, inlet_row), (px, inlet_row)] if facing == SOUTH else [(px, inlet_row), (px + 2, inlet_row)]
        if len(fluids_in) == 1:
            for t in inlets:
                members[fluids_in[0]].append(t)
        else:
            for t, f in zip(inlets, fluids_in):
                members[f].append(t)
            for t in inlets[len(fluids_in):]:
                pipes.reserve(t, "")
        outs = _fluid_products(recipes[recipe])
        for t in ((px, outlet_row), (px + 2, outlet_row)):
            if outs:
                members[outs[0]].append(t)
            else:
                pipes.reserve(t, "")
        if not outs:                 # an item plant: its product leaves by inserter onto the belt above
            put("inserter", px + 1, PLANT_Y - 1, SOUTH)
    # Product belts and their end chests.
    plastic = [px for item, _, px in row if item == PLASTIC]
    sulfur = [px for item, _, px in row if item == SULFUR]
    ports: dict[str, tuple[int, int]] = {}
    if plastic:
        for bx in range(max(plastic) + 1, min(plastic) - 2, -1):
            put("transport-belt", bx, 1, WEST)
        put("inserter", min(plastic) - 2, 1, EAST)
        put("iron-chest", min(plastic) - 3, 1)
        ports[PLASTIC] = (min(plastic) - 3, 1)
    if sulfur:
        for bx in range(min(sulfur) + 1, max(sulfur) + 4):
            put("transport-belt", bx, 1, EAST)
        put("inserter", max(sulfur) + 4, 1, WEST)
        put("iron-chest", max(sulfur) + 5, 1)
        ports[SULFUR] = (max(sulfur) + 5, 1)
    # Refineries, facing north: outlets heavy, light, gas on row 10; inlets water, crude on row 16.
    process = recipes[size["process"]]
    outs = set(_fluid_products(process))
    for k in range(size["refineries"]):
        rx = 3 + k * REFINERY_STEP
        put("oil-refinery", rx, REFINERY_Y, NORTH, size["process"], 5, 5)
        for t, f in (((rx, REFINERY_Y - 1), "heavy-oil"), ((rx + 2, REFINERY_Y - 1), "light-oil"),
                     ((rx + 4, REFINERY_Y - 1), "petroleum-gas")):
            if f in outs:
                members[f].append(t)
            else:
                pipes.reserve(t, "")
        ins = set(_fluid_ingredients(process))
        for t, f in (((rx + 1, ROWS_DOWN), "water"), ((rx + 3, ROWS_DOWN), "crude-oil")):
            if f in ins:
                members[f].append(t)
            else:
                pipes.reserve(t, "")
    width = max(width_plants, 3 + size["refineries"] * REFINERY_STEP) + 3
    # The ports for crude and water: below the first refinery's inlets.
    ports["crude-oil"] = (3 + 3, ROWS_DOWN + 1)
    if members["water"]:
        ports["water"] = (3 + 1, ROWS_DOWN + 1)
    pipes.blocked |= used
    # Keep the routes inside the block's own box (plus a margin round it).
    for bx in range(-2, width + 3):
        for by in (-1, ROWS_DOWN + 3):
            pipes.blocked.add((bx, by))
    for by in range(-1, ROWS_DOWN + 4):
        for bx in (-2, width + 3):
            pipes.blocked.add((bx, by))
    laid: list[Placed] = []
    for fluid in FLUIDS:
        ends = members[fluid] + ([ports[fluid]] if fluid in ports else [])
        for t in ends:
            pipes.reserve(t, fluid)
    for fluid in layout["order"]:
        ends = members[fluid] + ([ports[fluid]] if fluid in ports else [])
        if not ends:
            continue
        first, joined = ends[0], {ends[0]}
        start = [Placed("pipe", first[0] + 0.5, first[1] + 0.5)]
        pipes.add_pipes(start, fluid)
        laid.extend(start)
        for t in ends[1:]:
            if t in joined:
                continue
            route = pipes.route(fluid, first, t, joins=joined, limit=ROUTE_LIMIT)
            if route is None:
                raise layout_planner.PlanError(f"oil plant: no pipe for {fluid} to {t}")
            laid.extend(route)
            # (branches start only on plain pipes: one off a pipe-to-ground end would put a pipe on it)
            joined |= {(math.floor(p.x), math.floor(p.y)) for p in route if p.name == "pipe"} | {t}
    ents.extend(laid)
    fluids = {**pipes.pipes, **{t: f for t, (f, _) in pipes.ends.items()}}
    supplies = [{"x": e.x, "y": e.y, "item": "coal", "slot": "input"}
                for e in ents if e.name == "chemical-plant" and e.recipe == PLASTIC]
    electric = [e for e in ents if e.name in ("chemical-plant", "oil-refinery", "inserter")]
    shape = set(used) | {(math.floor(p.x), math.floor(p.y)) for p in laid}
    return {"entities": ents, "pipes": fluids, "ports": ports, "supplies": supplies, "electric": electric,
            "shape": shape, "width": width, "height": ROWS_DOWN + 2}


def poles_for(electric: list[Placed], taken: set[tuple[int, int]], box: tuple[int, int, int, int]) -> list[Placed]:
    """Small poles covering every electric machine: greedily the free tile covering the most machines not yet
    covered (within `box`), then the poles wired together (each group joined later by power.connect)."""
    from companion.rocket_plan import SIZES

    def footprint(e: Placed) -> tuple[float, float, float, float]:
        s = SIZES.get(e.name, 1) / 2
        return (e.x - s, e.y - s, e.x + s, e.y + s)
    boxes = [footprint(e) for e in electric]
    left = set(range(len(boxes)))
    poles: list[Placed] = []
    blocked = set(taken)
    candidates = [(x, y) for x in range(box[0], box[2]) for y in range(box[1], box[3]) if (x, y) not in blocked]

    def covers(t, b):
        cx, cy = t[0] + 0.5, t[1] + 0.5
        return cx - POLE_SUPPLY < b[2] and b[0] < cx + POLE_SUPPLY and cy - POLE_SUPPLY < b[3] and b[1] < cy + POLE_SUPPLY
    while left:
        best = max(candidates, key=lambda t: (sum(1 for i in left if covers(t, boxes[i])), -t[1], -t[0]), default=None)
        if best is None:
            break
        got = {i for i in left if covers(best, boxes[i])}
        if not got:
            break
        left -= got
        poles.append(Placed("small-electric-pole", best[0] + 0.5, best[1] + 0.5))
        candidates.remove(best)
    return poles


# --- a ready-made block -------------------------------------------------------------------------------

STEP = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}
FACING = {(0, -1): NORTH, (1, 0): EAST, (0, 1): SOUTH, (-1, 0): WEST}
MAX_COPIES = 4
UNCARRIED = ("pickup_position", "drop_position", "filter", "filters", "use_filters", "input_priority", "output_priority")


def _at_hand(site: Mapping[str, Any]) -> set[str]:
    have = {k for k, v in (site.get("inventory") or {}).items() if v}
    for r in site.get("recipes") or []:
        if isinstance(r, Mapping):
            have.update(str(p.get("name")) for p in r.get("products") or [] if isinstance(p, Mapping))
    return have


def ready_fits(site: Mapping[str, Any], build: Mapping[str, Any], want: Mapping[str, float]) -> bool:
    """A library block the plant may use: its ports from a sidecar, crude oil (and water) its only fluids in and
    none out, some of `want` among its products, every recipe unlocked and every part to be had."""
    imp = build.get("import") or {}
    ports = [p for p in imp.get("ports") or [] if isinstance(p, Mapping)]
    if imp.get("ports_from") != "sidecar" or not ports:
        return False
    fluids_in = {p.get("item") for p in ports if p.get("way") == "in" and p.get("kind") == "fluid"}
    rates_out = {p.get("item"): float(p.get("rate") or 0) for p in ports if p.get("way") == "out"}
    if "crude-oil" not in fluids_in or not fluids_in <= {"crude-oil", "water"}             or any(rates_out.get(item, 0) <= 0 for item, n in want.items() if n > 0):
        return False   # (every product wanted, made: a plastic-only block is no plant for plastic and sulfur)
    if any(p.get("kind") == "fluid" for p in ports if p.get("way") == "out"):
        return False   # (a fluid out, lubricant say: not a plastic and sulfur plant)
    if any(e.get(k) not in (None, "", [], {}) for e in build.get("entities") or [] for k in UNCARRIED):
        return False   # (settings the plant's copies do not carry yet: such a block might not run)
    recipes = _recipes(site)
    if any(r not in recipes for r in build.get("recipes_needed") or []):
        return False
    have, protos = _at_hand(site), site.get("prototypes") or {}
    return all((protos.get(e["prototype"]) or {}).get("placement_item", e["prototype"]) in have
               for e in build.get("entities") or [])


def ready_size(build: Mapping[str, Any], want: Mapping[str, float], copies: int | None = None) -> dict:
    """What `copies` of a library block make and take (by its sidecar), as sizing() says it; as many copies as
    `want` needs (at most MAX_COPIES) when not given."""
    ports = [p for p in build["import"]["ports"] if isinstance(p, Mapping)]
    makes = {p["item"]: float(p.get("rate") or 0) for p in ports if p.get("way") == "out"}
    takes = {p["item"]: float(p.get("rate") or 0) for p in ports if p.get("way") == "in"}
    if copies is None:
        copies = max([1] + [math.ceil(r / makes[i] - 1e-6) for i, r in want.items() if makes.get(i)])
    copies = max(1, min(MAX_COPIES, copies))

    def count(recipes):
        out: dict[str, int] = {}
        for e in build["entities"]:
            if e["prototype"] == "chemical-plant" and e.get("recipe") in recipes:
                out[e["recipe"]] = out.get(e["recipe"], 0) + copies
        return out
    process = next((e.get("recipe") for e in build["entities"] if e["prototype"] == "oil-refinery"), None)
    return {"process": process, "refineries": sum(1 for e in build["entities"] if e["prototype"] == "oil-refinery") * copies,
            "plants": count((PLASTIC, SULFUR)), "cracking": count(CRACKING),
            "crude": takes.get("crude-oil", 0.0) * copies, "water": takes.get("water", 0.0) * copies,
            "gas_made": None, "gas_drawn": None, "copies": copies, "ready": build,
            "makes": {k: v * copies for k, v in makes.items()}}


def ready_block(size: Mapping[str, Any]) -> dict:
    """`size["copies"]` of a library block side by side, as the plant's local block: their crude and water ports
    piped together (the first copy's are the plant's), an inserter and a chest at each product's belt end, the
    plastic plants' coal handed in, a small pole beside one of its own poles (to wire it to the grid)."""
    from companion.rocket_plan import _tiles as tiles_of
    build = size["ready"]
    w, h = (build.get("size") or [0, 0])[:2]
    gap, margin = 4, 3
    ents: list[Placed] = []
    used: set[tuple[int, int]] = set()
    fluids: dict[tuple[int, int], str] = {}
    ends: dict[str, list[tuple[int, int]]] = {"crude-oil": [], "water": []}
    chests: dict[str, tuple[int, int]] = {}

    def put(e: Placed) -> None:
        ents.append(e)
        used.update(tiles_of(e.name, e.x, e.y, e.direction))
    for k in range(size["copies"]):
        ox, oy = margin + k * (w + gap), margin
        for e in build["entities"]:
            put(Placed(e["prototype"], e["position"]["x"] + ox, e["position"]["y"] + oy, int(e.get("direction") or 0),
                       e.get("recipe"), e.get("belt_to_ground_type"), mirror=bool(e.get("mirror"))))
        for p in build["import"]["ports"]:
            step = STEP.get(str(p.get("edge")))
            if not step or not p.get("outside"):
                continue
            px, py = p["outside"][0] + ox, p["outside"][1] + oy    # (the block's own pipe or belt at its edge)
            beyond = (px + step[0], py + step[1])
            if p.get("kind") == "fluid" and p["item"] in ends:
                fluids[(px, py)] = p["item"]
                ends[p["item"]].append(beyond)
            elif p.get("way") == "out" and p.get("kind") == "belt":
                # the belt's end: an inserter picking from it, a chest beyond it (the product's port)
                put(Placed("inserter", beyond[0] + 0.5, beyond[1] + 0.5, FACING[(-step[0], -step[1])]))
                chest = (beyond[0] + step[0], beyond[1] + step[1])
                put(Placed("iron-chest", chest[0] + 0.5, chest[1] + 0.5))
                chests.setdefault(p["item"], chest)
    width, height = margin * 2 + size["copies"] * (w + gap), margin * 2 + h
    # The copies' crude and water joined, inside the plant's box.
    pipes = PipeMap()
    pipes.blocked |= used
    for bx in range(-1, width + 1):
        pipes.blocked |= {(bx, -1), (bx, height)}
    for by in range(-1, height + 1):
        pipes.blocked |= {(-1, by), (width, by)}
    for t, f in fluids.items():
        pipes.pipes[t] = f
    laid: list[Placed] = []
    for fluid, members in ends.items():
        for t in members:
            pipes.reserve(t, fluid)
        if not members:
            continue
        first = members[0]
        start = [Placed("pipe", first[0] + 0.5, first[1] + 0.5)]
        pipes.add_pipes(start, fluid)
        laid += start
        joined = {first}
        for t in members[1:]:
            path = pipes.route(fluid, first, t, joins=joined, limit=ROUTE_LIMIT)
            if path is None:
                raise layout_planner.PlanError(f"ready block: no pipe joining its copies' {fluid}")
            laid += path
            joined |= {(math.floor(q.x), math.floor(q.y)) for q in path if q.name == "pipe"} | {t}
    ents += laid
    fluids.update({t: f for t, f in pipes.pipes.items() if f in ("crude-oil", "water")})
    # A small pole outside the first copy, within wire reach of one of its own poles.
    own = [e for e in ents if e.name.endswith("electric-pole")]
    ring = [(x, y) for x in range(margin - 2, margin + w + 2) for y in range(margin - 2, margin + h + 2)
            if (x, y) not in used and (x, y) not in pipes.pipes and (x, y) not in pipes.reserved
            and not (margin <= x < margin + w and margin <= y < margin + h)]
    best = min(((math.dist((x + 0.5, y + 0.5), (p.x, p.y)), x, y) for x, y in ring for p in own), default=None)
    if best is None or best[0] > 7.5:
        raise layout_planner.PlanError("ready block: no spot for a pole beside its own")
    put(Placed("small-electric-pole", best[1] + 0.5, best[2] + 0.5))
    supplies = [{"x": e.x, "y": e.y, "item": "coal", "slot": "input"}
                for e in ents if e.name == "chemical-plant" and e.recipe == PLASTIC]
    shape = set(used) | {(math.floor(q.x), math.floor(q.y)) for q in laid}
    ports = {**{f: m[0] for f, m in ends.items() if m}, **chests}
    return {"entities": ents, "pipes": fluids, "ports": ports, "supplies": supplies, "electric": [],
            "covered": True, "shape": shape, "width": width, "height": height}


# --- on the map ---------------------------------------------------------------------------------------

FIELD_SPAN = 40   # wells this near each other are one field


def cliff_tiles(world: Mapping[str, Any]) -> set[tuple[int, int]]:
    """The map's cliffs with a tile to spare all round: its cliff boxes are smaller than what the game refuses
    (a pole and a pipe of the oil plant on cliffs, oil-practice 2026-10-29)."""
    out: set[tuple[int, int]] = set()
    for e in world.get("entities", []):
        if e.get("t") == "cliff":
            w, h = int(e.get("w", 1)), int(e.get("h", 1))
            x0, y0 = math.floor(e["x"] - w / 2 + 0.5) - 1, math.floor(e["y"] - h / 2 + 0.5) - 1
            out |= {(x0 + i, y0 + j) for i in range(w + 2) for j in range(h + 2)}
    return out


def field_of(world: Mapping[str, Any], oil: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """The wells of the field the engineer works: round its own pumpjacks if it has any, else the field
    nearest home (a wide map holds fields far beyond the base)."""
    own = [(math.floor(e["x"]), math.floor(e["y"])) for e in world.get("entities", [])
           if e.get("n") == "pumpjack" and e.get("f") == world.get("force")]
    home = (float(world["home"]["x"]), float(world["home"]["y"]))
    seed = own[0] if own else min(oil, key=lambda t: (t[0] - home[0]) ** 2 + (t[1] - home[1]) ** 2)
    field, frontier = {seed} & oil or {min(oil, key=lambda t: (t[0] - seed[0]) ** 2 + (t[1] - seed[1]) ** 2)}, None
    frontier = list(field)
    while frontier:
        p = frontier.pop()
        for q in oil:
            if q not in field and abs(q[0] - p[0]) <= FIELD_SPAN / 2 and abs(q[1] - p[1]) <= FIELD_SPAN / 2:
                field.add(q)
                frontier.append(q)
    return sorted(field)


def plan(site: Mapping[str, Any], world: Mapping[str, Any], want: Mapping[str, float] | None = None,
         ready: list[Mapping[str, Any]] | None = None) -> dict:
    """The oil plant as a plan for /npc-plan submit: the block near the oil field, pumpjacks on its wells, an
    offshore pump, the pipes between, poles to the grid. A field that gives less crude than the plant draws gets a
    smaller plant, sized to what it gives. `ready`: library blocks (the optimizer's, the user's books) tried first,
    as many copies as the want needs, where one fits (ready_fits); else the plant's own block. Raises PlanError
    when it cannot be had."""
    want = dict(WANT if want is None else want)
    failed = []
    for build in ready or []:
        if not ready_fits(site, build, want):
            continue
        size = ready_size(build, want)
        try:
            out = _place(site, world, size)
            if out["crude"] < size["crude"] * 0.98 and size["copies"] > 1:
                fewer = ready_size(build, want, max(1, math.floor(size["copies"] * out["crude"] / size["crude"] + 1e-6)))
                if fewer["copies"] < size["copies"]:
                    out = _place(site, world, fewer)
            if out["crude"] < out["plan"]["sizing"]["crude"] * 0.98:
                out["plan"]["notes"].append(f"its field gives {out['crude']:.0f} crude/s of the "
                                            f"{out['plan']['sizing']['crude']:.0f} it draws")
            out["plan"]["notes"].append(f"a ready block: {(build.get('import') or {}).get('label') or build.get('label')}")
            return out["plan"]
        except layout_planner.PlanError as exc:
            failed.append(str(exc))
    own = _own(site, world, want)
    if failed:
        own["notes"].append("no ready block fitted: " + failed[0][:100])
    return own


def _own(site: Mapping[str, Any], world: Mapping[str, Any], want: Mapping[str, float]) -> dict:
    """The plant with its own block (block())."""
    size = sizing(site, want)
    if size is None:
        raise layout_planner.PlanError("no oil process unlocked that a plastic or sulfur plant could run on")
    out = _place(site, world, size)
    if out["crude"] < size["crude"] * 0.98:
        # The field gives less: as many refineries (and their cracking) as its crude runs.
        per = size["crude"] / size["refineries"]
        smaller = sizing(site, want, cap=max(1, math.floor(out["crude"] / per + 1e-6)))
        if smaller and smaller["refineries"] < size["refineries"]:
            out = _place(site, world, smaller)
            out["plan"]["notes"].append(f"its field gives {out['crude']:.0f} crude/s: {smaller['refineries']} refineries "
                                        f"make {smaller['gas_made']:.0f} gas/s of the {smaller['gas_drawn']:.0f} its plants could use")
    if out["crude"] < out["plan"]["sizing"]["crude"] * 0.98:
        out["plan"]["notes"].append(f"its field gives {out['crude']:.0f} crude/s of the {out['plan']['sizing']['crude']:.0f} it draws")
    return out["plan"]


def well_amount(site: Mapping[str, Any], field: tuple[float, float] | None = None,
                home: tuple[float, float] | None = None) -> float:
    """Crude in a well of its field, from the patches it knows (amount per well) round `field` (world tiles; the
    patches are kept relative to `home`), else a normal well's."""
    def near(p):
        if field is None or home is None:
            return True
        x, y = home[0] + float(p.get("dx") or 0), home[1] + float(p.get("dy") or 0)
        return abs(x - field[0]) <= FIELD_SPAN and abs(y - field[1]) <= FIELD_SPAN
    cells = [p for p in site.get("known_patches") or [] if isinstance(p, Mapping) and p.get("resource") == "crude-oil"
             and (p.get("tiles") or 0) > 0 and p.get("amount") and near(p)]
    if not cells:
        return 300000.0
    return sum(float(p["amount"]) for p in cells) / sum(float(p["tiles"]) for p in cells)


def _place(site: Mapping[str, Any], world: Mapping[str, Any], size: Mapping[str, Any]) -> dict:
    from companion.base_plan import blocked_tiles, find_fit
    from companion.power import connect, live_poles
    from companion.rocket_plan import _new_pumpjack, _pump, _tiles as world_tiles, pipe_map
    from companion.smelting import ore_tiles
    local = ready_block(size) if size.get("ready") else block(site, size)
    oil = ore_tiles(world.get("resources", {}), "crude-oil")
    if not oil:
        raise layout_planner.PlanError("no known oil well for the oil plant")
    cliffs = cliff_tiles(world)
    blocked = set(blocked_tiles(world)) | team_area.forbidden(world) | cliffs
    taken = blocked | oil
    wells = field_of(world, oil)
    field = (sum(x for x, _ in wells) / len(wells), sum(y for _, y in wells) / len(wells))
    margin = {(x, y) for x in range(-2, local["width"] + 3) for y in range(-1, local["height"] + 3)}
    home = (int(world["home"]["x"]), int(world["home"]["y"]))
    fit = find_fit(taken, margin, home, tuple(world["area"]), anchor=field)
    if fit is None:
        raise layout_planner.PlanError("no room for the oil plant near its field")
    ox, oy = fit
    placed = [Placed(e.name, e.x + ox, e.y + oy, e.direction, e.recipe, e.kind, mirror=e.mirror) for e in local["entities"]]
    fluids = {(t[0] + ox, t[1] + oy): f for t, f in local["pipes"].items()}
    used = taken | {(x + ox, y + oy) for x, y in local["shape"]}
    pipes = pipe_map(world, placed, fluids)
    pipes.blocked |= cliffs
    from companion import land
    bare = {k: v for k, v in world.items() if k not in ("reserved", "reserved_hard", "_reserved_tiles")}
    kept = land.reserved_tiles(world) - set(blocked_tiles(bare))   # (kept ground with nothing on it: no cliff either)

    def route(fluid, start, goal, joins):
        """A pipe kept off ground the factory plan keeps for other sections where it can; across it where it must
        (a field or a shore inside such ground)."""
        if start in kept or goal in kept:
            return pipes.route(fluid, start, goal, joins=joins)   # (an end in such ground: it must cross it)
        off = kept - pipes.blocked
        pipes.blocked |= off
        try:
            path = pipes.route(fluid, start, goal, joins=joins, limit=SOFT_LIMIT)
        finally:
            pipes.blocked -= off
        return path or pipes.route(fluid, start, goal, joins=joins)
    port = {k: (t[0] + ox, t[1] + oy) for k, t in local["ports"].items()}
    plain = {(math.floor(e.x), math.floor(e.y)) for e in placed if e.name == "pipe"}
    network = {f: {t for t, g in fluids.items() if g == f and t in plain} for f in FLUIDS}
    out = list(placed)
    notes: list[str] = []
    # Pumpjacks for the crude: as many as the plant draws, on free wells nearest it, each piped in.
    jacks, crude = 0, 0.0
    need = size["crude"]
    amount = well_amount(site, field, (float(world["home"]["x"]), float(world["home"]["y"])))
    per_well = {}
    for e in world.get("entities", []):
        if e.get("n") == "crude-oil":
            per_well[(math.floor(e["x"]), math.floor(e["y"]))] = float(e.get("a") or 0)
    # (pumpjacks on this field's wells only; one may stand on ground the factory plan keeps for another section,
    # as a drill stands on its ore there: a well cannot move)
    on_field = {**world, "resources": {**world["resources"], "crude-oil": [[y, x, x] for x, y in wells]}}
    used -= {(x + i, y + j) for x, y in wells for i in range(-2, 3) for j in range(-2, 3)} & kept   # (body, outlet, pole)
    while crude < need and jacks < MAX_PUMPJACKS:
        well = _new_pumpjack(on_field, used, port["crude-oil"])
        if well is None:
            break
        jack, pole, outlet = well
        pipes.blocked |= world_tiles(jack.name, jack.x, jack.y) | {(math.floor(pole.x), math.floor(pole.y))}
        pipes.reserved[outlet] = "crude-oil"
        used |= world_tiles(jack.name, jack.x, jack.y) | {(math.floor(pole.x), math.floor(pole.y)), outlet}
        # (the new end is the goal: a route may start anywhere on the network it joins)
        path = route("crude-oil", port["crude-oil"], outlet, network["crude-oil"])
        if path is None:
            notes.append("a pumpjack with no pipe to the plant")
            continue
        out += [jack, pole] + path
        network["crude-oil"] |= {outlet} | {(math.floor(p.x), math.floor(p.y)) for p in path if p.name == "pipe"}
        used |= {(math.floor(p.x), math.floor(p.y)) for p in path}
        jacks += 1
        crude += well_rate(per_well.get((math.floor(jack.x), math.floor(jack.y)), amount))
    if crude < need * 0.98:
        # Short of free wells (a second plant, on a field the first works): it joins the crude its pumpjacks
        # already pipe, and says so.
        from companion.rocket_plan import crude_tiles
        theirs = crude_tiles(world, pipes) - network["crude-oil"]
        if theirs:
            start = min(theirs, key=lambda t: abs(t[0] - port["crude-oil"][0]) + abs(t[1] - port["crude-oil"][1]))
            # (what those pumpjacks give beyond what the refineries already there draw)
            mine = world.get("force")
            jacks_there = sum(1 for e in world.get("entities", []) if e.get("n") == "pumpjack" and e.get("f") == mine)
            refineries_there = sum(1 for e in world.get("entities", []) if e.get("n") == "oil-refinery" and e.get("f") == mine)
            spare = jacks_there * well_rate(amount) - refineries_there * size["crude"] / size["refineries"]
            path = route("crude-oil", port["crude-oil"], start, network["crude-oil"]) if spare > 0 else None
            if path is not None:
                out += path
                notes.append(f"it shares the crude its field's pumpjacks already pipe ({spare:.0f}/s spare)")
                crude = min(need, crude + spare)
    if not jacks and crude < need * 0.98:
        raise layout_planner.PlanError("no free oil well with a pipe to the oil plant")
    if "water" in port:
        pump = _pump(world, used - kept, port["water"])   # (a shore cannot move either: kept ground is no bar)
        path = None
        if pump is not None:
            entity, outlet = pump
            pipes.blocked |= world_tiles(entity.name, entity.x, entity.y, entity.direction)
            pipes.reserved[outlet] = "water"
            path = route("water", port["water"], outlet, network["water"])
            if path is not None:
                out += [entity] + path
        if path is None:
            # No shore of its own within reach (a second plant beside the first): the water already piped there.
            theirs = {t for t, f in pipes.pipes.items() if f == "water"} - network["water"]
            start = min(theirs, key=lambda t: abs(t[0] - port["water"][0]) + abs(t[1] - port["water"][1]), default=None)
            path = route("water", port["water"], start, network["water"]) if start else None
            if path is None:
                raise layout_planner.PlanError("no shore or water pipe for the oil plant's water")
            out += path
            notes.append("it shares the water already piped beside it")
        used |= {(math.floor(p.x), math.floor(p.y)) for p in path}
    # Power: poles over the block's machines, then lines to the grid.
    box = (ox - 2, oy - 1, ox + local["width"] + 2, oy + local["height"] + 2)
    taken_now = used | {(math.floor(e.x), math.floor(e.y)) for e in out} | set(pipes.pipes) | set(pipes.ends)
    for e in out:
        if e.name in ("oil-refinery", "chemical-plant", "pumpjack", "offshore-pump"):
            taken_now |= world_tiles(e.name, e.x, e.y, e.direction)
    electric = [e for e in out if e.name in ("chemical-plant", "oil-refinery", "inserter")]
    poles = [] if local.get("covered") else poles_for(electric, taken_now, box)
    out += poles
    powered = live_poles(world)   # (poles a generator feeds: F-21)
    # (a power line keeps off ground kept for another section where it can, crosses it where it must; cliffs never)
    hard = (taken_now - kept) | cliffs | {(math.floor(p.x), math.floor(p.y)) for p in poles}         | {(math.floor(e.x), math.floor(e.y)) for e in out}
    inside = any((math.floor(e.x), math.floor(e.y)) in kept for e in out if e.name == "small-electric-pole")
    extra, problems = connect(out, powered, hard if inside else hard | kept)   # (its own poles there: it must cross)
    if problems and not inside:
        extra, problems = connect(out, powered, hard)
    if problems:
        raise layout_planner.PlanError(f"oil plant: {problems[0]}")
    out += extra
    origin = site.get("origin") or {"x": 0, "y": 0}
    dx, dy = -float(origin["x"]), -float(origin["y"])
    entities = [e.to_plan(f"oil-{n}", dx, dy) for n, e in enumerate(out, start=1)]
    by_pos = {(e.name, e.x, e.y): entities[i]["id"] for i, e in enumerate(out)}
    first = {name: next((entities[i]["id"] for i, e in enumerate(out) if e.name == name and (e.recipe == recipe or not recipe)), None)
             for name, recipe in (("oil-refinery", None), ("chemical-plant", PLASTIC), ("chemical-plant", SULFUR))}
    supplies = [{"entity_id": by_pos[("chemical-plant", s["x"] + ox, s["y"] + oy)], "item": s["item"], "slot": s["slot"]}
                for s in local["supplies"] if ("chemical-plant", s["x"] + ox, s["y"] + oy) in by_pos]
    witnesses = [{"kind": "entity_built", "entity_id": first["oil-refinery"]},
                 {"kind": "product_counter_delta", "entity_id": first["oil-refinery"], "item": "petroleum-gas",
                  "min_delta": 1}]
    for item in (PLASTIC, SULFUR):
        pid = next((entities[i]["id"] for i, e in enumerate(out) if e.name == "chemical-plant" and e.recipe == item), None)
        if pid:
            witnesses.append({"kind": "product_counter_delta", "entity_id": pid, "item": item, "min_delta": 2})
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    return {"crude": crude, "plan": {"plan_id": f"oil-plant-{safe}", "source": "oil-plant", "purpose": "oil-processing",
            "entities": entities, "sizing": {k: v for k, v in size.items() if k not in ("takes", "ready")}, "pumpjacks": jacks,
            "notes": notes,
            "stages": [{"id": "oil-plant", "depends_on": [], "entity_ids": [e["id"] for e in entities],
                        "manual_supplies": supplies, "witnesses": witnesses}]}}

