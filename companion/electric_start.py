"""Electric mining and belt-fed fuel as soon as there is power: no coal carried by hand past the first minutes.

The burner phase (burner drills dropping into stone furnaces, each fuelled by hand) is a bootstrap only. Kept on, it
took most of an engineer's time: coal carried by hand was 19% of it at game minute 80 and 39% at 192 (one-engineer
run 34, 2026-10-25), and the user's direction is that furnaces and boilers are fed by belt early, mining is electric
as soon as possible, and burner drills go. So once steam power runs:

    smelting   a plate module (companion/growth.plate_module) by its ore patch: electric drills on the ore and a
               coal belt from electric drills on coal (companion/outpost.py lays it out, as for an outpost),
               the plates or bricks into a store; the burner pairs making the same product are retired once
               it covers what they made. It is laid out whole but built in parts, grown in place: its feeds
               and a few furnaces first, more furnaces and drills on the same belts later, each part as big as
               the engineer can make and put down in about BUILD_MINUTES (the user, 2026-10-26: early on
               everything is made by hand, so small builds that do the job, larger ones once machines make
               the parts). Its spot and fields are kept with the block (`keep`) for the next part.
    supply     coal or stone for the engineer's own use: electric drills on the patch, their belt into a store;
               the burner drills that made it are retired

    plan = plan_smelting(site, world, "electric-iron-smelting")
    plan = plan_supply(site, world, "electric-coal-supply")
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from companion import layout_planner, outpost, team_area
from companion.modules import NORTH, Placed
from companion.smelting import footprint, foreign_tiles, ore_tiles, outlets, plan_field

FURNACE_STEP = 4      # furnaces a part adds at least (the first part: its feeds and these)
BUILD_MINUTES = 8.0   # a part is as big as the engineer can make and put down in about this long
# (a block's belts, poles and store whatever its furnaces: 4 furnaces took 117 belts, 12 173, 24 268 on run 33's map,
# 2026-10-26; three blocks of four cost half again the iron of one of twelve, so a block grows in place)
BELTS_FIXED, BELTS_PER_FURNACE = 90, 7.5
SMELTING_FAMILY = "electric_smelting"   # its spec key: the product it smelts
SUPPLY_FAMILY = "electric_supply"       # its spec key: the resource it mines
STORE_SLOTS = 8                         # a supply's store: 400 coal or stone, then its drills wait


def _spec(purpose: str) -> Mapping[str, Any]:
    from companion import catalog_planner
    return catalog_planner.PURPOSES[purpose]


def _ground(world: Mapping[str, Any]) -> set:
    from companion.base_plan import blocked_tiles
    return set(blocked_tiles(world)) | set(team_area.forbidden(world))


def home_ground(world: Mapping[str, Any]) -> Mapping[str, Any]:
    """The map an electric block is laid out on: in the team world the engineer's own plot, inside the team's wall
    (beyond it a block needs a guard of its own: companion/outpost.ring), else the whole map."""
    team = team_area.team_of(world)
    area = world.get("area")
    if team is None or team.plot is None or not area:
        return world
    px0, py0, px1, py1 = team.plot
    box = [max(area[0], px0), max(area[1], py0), min(area[2], px1 - 1), min(area[3], py1 - 1)]
    return {**world, "area": box} if box[0] <= box[2] and box[1] <= box[3] else world


def blocks_of(site: Mapping[str, Any], purpose: str) -> list[Mapping[str, Any]]:
    return [b for b in site.get("base") or [] if isinstance(b, Mapping) and b.get("purpose") == purpose]


# Smelting comes in standard arrays, each one module of the same design (the user's 24 stone furnaces, ore and coal on
# one belt, copied as the base grows: the play recording, 2026-10-28), each array grown in place in parts with a
# keep of its own (`array`: its index); a new array is laid out once the last is whole and more plates are wanted
# (companion/plate_lead.py).
def array_of(block: Mapping[str, Any]) -> int:
    k = block.get("keep")
    return int((k.get("array") if isinstance(k, Mapping) else 0) or 0)


def current(site: Mapping[str, Any], purpose: str) -> int:
    """The index of its latest array (0 with none)."""
    return max((array_of(b) for b in blocks_of(site, purpose)), default=0)


def kept(site: Mapping[str, Any], purpose: str, index: int | None = None) -> Mapping[str, Any] | None:
    """What an array keeps to grow in place (its spot and fields, the size it grows to), its latest part's; the
    latest array's by default."""
    index = current(site, purpose) if index is None else index
    for b in reversed(blocks_of(site, purpose)):
        k = b.get("keep")
        if isinstance(k, Mapping) and k.get("sites") and array_of(b) == index:
            return k
    return None


def built_rate(site: Mapping[str, Any], purpose: str, index: int | None = None) -> float:
    """What its built parts smelt, items a second at full speed: every array's, or array `index`'s."""
    from companion import growth
    furnaces = sum(int((b.get("machines") or {}).get("stone-furnace", 0) or 0) for b in blocks_of(site, purpose)
                   if index is None or array_of(b) == index)
    return furnaces * growth.FURNACE_RATE["stone-furnace"]


def target(site: Mapping[str, Any], purpose: str, index: int | None = None) -> float:
    """The size an array grows to: as it was laid out, else its spec's (the latest array's by default)."""
    keep = kept(site, purpose, index) or {}
    return float(keep.get("target") or _spec(purpose).get("rate") or 3.75)


def next_array(site: Mapping[str, Any], purpose: str) -> int:
    """The array its next part goes to: the latest while it is not whole, else a new one."""
    if not blocks_of(site, purpose):
        return 0
    return current(site, purpose) + (1 if grown(site, purpose) else 0)


def covers(site: Mapping[str, Any], purpose: str) -> bool:
    """Its built parts make what the burner builds it replaces made (they are then retired)."""
    return built_rate(site, purpose) >= _burner_rate(site, purpose) - 1e-6


def grown(site: Mapping[str, Any], purpose: str) -> bool:
    """Its latest array is built whole (one that keeps no layout to grow by was built whole)."""
    index = current(site, purpose)
    return kept(site, purpose, index) is None or built_rate(site, purpose, index) >= target(site, purpose, index) - 1e-6


def _burner_rate(site: Mapping[str, Any], purpose: str) -> float:
    """What the builds it replaces make, items a second: a burner pair's drill feeds its furnace; a supply's drills
    fill its chest, a coal loop's what they spare after feeding each other."""
    from companion import catalog_planner, growth
    spec = _spec(purpose)
    replaces = set(spec.get("replaces") or ())
    ratio = outpost.RATIO.get(str(spec.get(SMELTING_FAMILY)), 1.0)
    total = 0.0
    for b in site.get("base") or []:
        if not isinstance(b, Mapping) or b.get("purpose") not in replaces:
            continue
        m = b.get("machines") or {}
        burners = int(m.get("burner-mining-drill", 0) or 0)
        mined = (catalog_planner.LOOP_NET if b.get("purpose") == "coal-loop" else 0.25) * burners \
            + growth.DRILL_RATE * int(m.get("electric-mining-drill", 0) or 0)
        if spec.get(SMELTING_FAMILY):
            total += min(mined / ratio, int(m.get("stone-furnace", 0) or 0) * growth.FURNACE_RATE["stone-furnace"])
        else:
            total += mined
    return total


def retiring(site: Mapping[str, Any], purpose: str, adds: float = 0.0) -> list[str]:
    """The builds a finished electric goal takes down: those it replaces that stand, once its parts with the one
    being built (`adds`, items a second) make what they made (a hoarder keeps everything; a cautious or traditional
    engineer keeps its first build)."""
    from companion import bus_planner, catalog_planner
    traits = {str(t) for t in (site.get("personality") or {}).get("traits") or []}
    if "hoarder" in traits:
        return []
    if built_rate(site, purpose) + adds < _burner_rate(site, purpose) - 1e-6:
        # (the burner pairs smelt on until the electric parts cover them; a grown coal loop, sparing more than two
        # electric drills mine, fed on the burners still standing: retired, the engineer was short of coal, run 45)
        return []
    done = catalog_planner.built_purposes(site)
    out = [p for p in _spec(purpose).get("replaces") or () if p in done]
    if traits & {"traditionalist", "cautious"}:
        out = [p for p in out if p != bus_planner.FIRST_BUILD]
    return out


def _minutes(site: Mapping[str, Any], kit: Mapping[str, int]) -> float:
    """Game minutes to make and put down `kit` (name: count): by hand what its machines do not make, and the plates
    for it at what the base spares for building now."""
    from companion import construction, goal_brain, handcraft, ledger
    book = handcraft.book_for(site)
    hand = handcraft.cost(site, kit, book) + handcraft.PLACE_S * sum(kit.values())
    try:
        books = ledger.Ledger.from_site(site)
    except Exception:
        books = None
    names = [n for n, k in kit.items() for _ in range(int(k))]
    return hand / 60.0 + goal_brain.material_minutes(construction.cost(names, book), books, site)


def _kit(product: str, furnaces: int, total: int, first: bool) -> dict[str, int]:
    """The buildings of a part adding `furnaces` to a block of `total` (the first part: its feeds too)."""
    from companion import growth
    drills = math.ceil(furnaces * growth.FURNACE_RATE["stone-furnace"] * outpost.RATIO.get(product, 1.0)
                       / growth.DRILL_RATE)
    kit = {"stone-furnace": furnaces, "inserter": 2 * furnaces, "electric-mining-drill": drills,
           "small-electric-pole": math.ceil(furnaces / 2)}
    if first:
        kit["transport-belt"] = int(BELTS_FIXED + BELTS_PER_FURNACE * total)
        kit["electric-mining-drill"] += 2   # (its coal field)
        kit["small-electric-pole"] += 8
        kit[outpost.STORE] = 1
    return kit


def part_size(site: Mapping[str, Any], purpose: str) -> int:
    """Furnaces the next part of its array adds (next_array): the most, in steps of FURNACE_STEP, the engineer makes
    and puts down within BUILD_MINUTES; FURNACE_STEP at least, and no more than the array lacks."""
    from companion import growth
    product = str(_spec(purpose)[SMELTING_FAMILY])
    per = growth.FURNACE_RATE["stone-furnace"]
    index = next_array(site, purpose)
    total = max(1, math.ceil(target(site, purpose, index) / per - 1e-9))
    have = round(built_rate(site, purpose, index) / per)
    left = max(1, total - have)
    first = kept(site, purpose, index) is None
    best = min(FURNACE_STEP, left)
    for k in sorted({*range(FURNACE_STEP, left + 1, FURNACE_STEP), left}):
        if k <= best:
            continue
        try:
            if _minutes(site, _kit(product, k, total, first)) > BUILD_MINUTES:
                break
        except Exception:
            break
        best = k
    return best


def _part(stages: list, world: Mapping[str, Any], product: str, ore: str, furnaces: int) -> list:
    """The stages with only what this part builds: none of what stands; of the furnaces not built, the first
    `furnaces` (nearest where its feeds come in, the way its ore belt runs) with their inserters; the ore field's
    drills those feed need; all the rest (belts, poles, store, the coal field) with the first part."""
    from companion import growth
    standing = {(e.get("n"), math.floor(float(e["x"])), math.floor(float(e["y"]))) for e in world.get("entities", [])
                if e.get("f") == world.get("force")}

    def stands(e: Placed) -> bool:
        return (e.name, math.floor(e.x), math.floor(e.y)) in standing
    every = [e for st in stages for e in st.entities]
    furnace_list = sorted((e for e in every if e.name == "stone-furnace"), key=lambda e: (e.y, e.x))
    # (the first fed first: those nearest where its feed belts come in, whichever way the block runs)
    feeds = [(b.x, b.y) for st in stages if not st.name.startswith("outpost-smelt-") for b in st.entities
             if b.name in ("transport-belt", "underground-belt")]
    if feeds and furnace_list:
        ex, ey = min(feeds, key=lambda p: min((p[0] - f.x) ** 2 + (p[1] - f.y) ** 2 for f in furnace_list))
        furnace_list.sort(key=lambda f: ((f.x - ex) ** 2 + (f.y - ey) ** 2, f.y, f.x))
    later = {id(f) for f in [f for f in furnace_list if not stands(f)][furnaces:]}
    kept_f = [f for f in furnace_list if id(f) not in later]

    def beside(i: Placed, f: Placed) -> bool:
        return abs(i.x - f.x) <= 1.6 and abs(i.y - f.y) <= 1.6
    inserters_later = {id(i) for i in every if i.name.endswith("inserter")
                       and any(beside(i, f) for f in furnace_list if id(f) in later)
                       and not any(beside(i, f) for f in kept_f)}
    patch = ore_tiles(world.get("resources") or {}, ore)
    on_ore = [e for e in every if e.name == "electric-mining-drill" and (math.floor(e.x), math.floor(e.y)) in patch]
    need = math.ceil(len(kept_f) * growth.FURNACE_RATE["stone-furnace"] * outpost.RATIO.get(product, 1.0)
                     / growth.DRILL_RATE - 1e-9)
    have = sum(1 for d in on_ore if stands(d))
    drills_later = {id(d) for d in sorted((d for d in on_ore if not stands(d)), key=lambda d: (d.y, d.x))
                    [max(0, need - have):]}
    out = []
    for st in stages:
        mine = [e for e in st.entities if not stands(e) and id(e) not in later and id(e) not in inserters_later
                and id(e) not in drills_later]
        if mine:
            out.append(type(st)(st.name, mine, st.supplies))
    return out


def _to_plan(site: Mapping[str, Any], purpose: str, stages: list[tuple[str, list[Placed], list[dict]]],
             source: str, adds: float = 0.0, keep: Mapping[str, Any] | None = None) -> dict:
    """A catalog plan from (stage name, buildings, witnesses as (kind, index of the building, extra)), in order."""
    origin = site.get("origin") or {"x": 0, "y": 0}
    ox, oy = float(origin["x"]), float(origin["y"])
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    entities, out = [], []
    for name, placed, witnesses in stages:
        key = re.sub(r"[^A-Za-z0-9_-]", "-", name)
        mine = [e.to_plan(f"{key}-{n}", -ox, -oy) for n, e in enumerate(placed, start=1)]
        entities += mine
        seen = [{"kind": kind, "entity_id": mine[i]["id"], **extra} for kind, i, extra in witnesses]
        from companion import belt_lines
        line_ops = belt_lines.ops(placed)
        out.append({"id": name, "depends_on": [out[-1]["id"]] if out else [], "entity_ids": [e["id"] for e in mine],
                    "manual_supplies": [], "witnesses": seen, **({"line_ops": line_ops} if line_ops else {})})
    plan = {"plan_id": f"{purpose}-{safe}", "source": source, "purpose": purpose, "entities": entities,
            "stages": out}
    gone = retiring(site, purpose, adds)
    if gone:
        plan["retire"] = gone
    if keep:
        plan["keep"] = dict(keep)
    return plan


def plan_smelting(site: Mapping[str, Any], world: Mapping[str, Any], purpose: str) -> dict:
    """The electric smelting `purpose` stands for: its product at its spec's rate, on the first spot by a patch
    of its ore where its fields, coal belt and power line all fit (companion/outpost.plan)."""
    from companion.base_plan import SOUTH, Stage, _coal_line, all_ore
    spec = _spec(purpose)
    world = home_ground(world)
    product, rate = str(spec[SMELTING_FAMILY]), float(spec.get("rate") or 3.75)
    if product not in outpost.ORES:
        raise layout_planner.PlanError(f"{product} is not smelted")
    blocked = _ground(world)
    ore = all_ore(world)

    def coal_line(entry, coal_rate, resource, used, last=SOUTH):
        return _coal_line(world, blocked, used - blocked, entry, rate=coal_rate, last=last, resource=resource)
    from companion import growth, ratios
    from companion.smelting import undergrounds
    index = next_array(site, purpose)   # (the latest array while it is not whole, else a new copy of the module)
    keep = kept(site, purpose, index)
    found = dict(str(s).split("=", 1) for s in (keep or {}).get("sites") or () if "=" in str(s))
    full = target(site, purpose, index)
    # (a new array where the whole one has no room: half of it, then a quarter, said; one being grown keeps its size)
    sizes = [full] if keep else [full / 2 ** k for k in range(3) if full / 2 ** k >= FURNACE_STEP * growth.FURNACE_RATE[
        "stone-furnace"] - 1e-9]
    stages, why = None, None
    with undergrounds("underground-belt" in ratios.unlocked(site)):
        for size in sizes:
            stages, why = outpost.plan(world, outpost.name(product, index, size), blocked | ore, blocked, found,
                                       coal_line, Stage, built=blocked)
            if stages:
                full = size
                break
    if not stages:
        raise layout_planner.PlanError((why or [f"no room for electric {product} smelting"])[0][:160])
    stages = _part(stages, world, product, str(spec.get("resource") or ""), part_size(site, purpose))
    adds = sum(1 for st in stages for e in st.entities if e.name == "stone-furnace") * growth.FURNACE_RATE["stone-furnace"]
    if adds <= 0:
        raise layout_planner.PlanError(f"electric {product} smelting is built whole")
    rows = []
    for s in stages:
        if s.name.startswith("outpost-smelt-"):
            furnace = next(i for i, e in enumerate(s.entities) if e.name == "stone-furnace")
            witnesses = [("entity_built", furnace, {}),
                         ("product_counter_delta", furnace, {"item": product, "min_delta": 5})]
            rows.append((f"{purpose}-furnaces", s.entities, witnesses))
        else:   # (its feeds: built first, their power with the furnaces' poles)
            rows.append((f"{purpose}-{s.name}", s.entities, [("entity_built", 0, {})]))
    keep = {"sites": [f"{k}={v}" for k, v in sorted(found.items())], "target": full, "array": index}
    return _to_plan(site, purpose, rows, "electric-start", adds, keep)


def plan_supply(site: Mapping[str, Any], world: Mapping[str, Any], purpose: str) -> dict:
    """The electric supply `purpose` stands for: its spec's drills on the nearest patch of its resource, their
    belt into a store the engineer takes from, poles to the power network."""
    from companion.power import connect
    spec = _spec(purpose)
    resource, drills = str(spec[SUPPLY_FAMILY]), int(spec.get("drills") or 2)
    world = home_ground(world)
    x0, y0, x1, y1 = world.get("area") or (-10 ** 6, -10 ** 6, 10 ** 6, 10 ** 6)
    tiles = {(x, y) for x, y in ore_tiles(world.get("resources") or {}, resource)
             if x0 + 3 <= x <= x1 - 3 and y0 + 3 <= y <= y1 - 3}
    if not tiles:
        raise layout_planner.PlanError(f"no {resource} on the map")
    blocked = _ground(world)
    home = world.get("home") or {"x": 0, "y": 0}
    near = (float(home.get("x") or 0), float(home.get("y") or 0))
    other = foreign_tiles(world["resources"], resource)
    from companion.power import live_poles
    powered = live_poles(world)
    taken = set(blocked)
    for _ in range(4):   # (a field whose store or power has no room: the next one)
        fld = next((f for min_ore in (15, 6) for n in range(drills, 0, -1)
                    for f in [plan_field(tiles, taken, n, resource, min_ore=min_ore, near=near, foreign=other)] if f),
                   None)
        if fld is None:
            break
        field_entities = fld.entities()
        ex, ey = fld.exit
        store = [Placed("inserter", ex + 0.5, ey + 0.5, NORTH),            # picks the collector's end, north of it
                 Placed(outpost.STORE, ex + 0.5, ey + 1.5, NORTH, bar=STORE_SLOTS),
                 Placed("small-electric-pole", ex + 1.5, ey + 0.5)]
        here = footprint(field_entities)
        if footprint(store) & (taken | here):
            taken |= here
            continue
        from companion.power import cover   # (every drill and inserter in some pole's area, F-21)
        store = store + cover(field_entities + store, world, taken | here | footprint(store) | outlets(store))
        poles, problems = connect(field_entities + store, powered, taken | here | footprint(store) | outlets(store))
        if problems:
            taken |= here
            continue
        placed = field_entities + store + poles
        drill = next(i for i, e in enumerate(placed) if e.name == "electric-mining-drill")
        witnesses = [("entity_built", drill, {}),
                     ("resource_extraction_delta", drill, {"item": resource, "min_delta": 5})]
        from companion import growth
        adds = sum(1 for e in placed if e.name == "electric-mining-drill") * growth.DRILL_RATE
        return _to_plan(site, purpose, [(f"{purpose}-field", placed, witnesses)], "electric-start", adds)
    raise layout_planner.PlanError(f"no room for electric drills on {resource} with a store and power")


def makes(purpose: str, site: Mapping[str, Any] | None = None) -> tuple[dict[str, float], dict[str, int]]:
    """(what it makes a second, the machines it places): its next part's (`site`), else its spec's size; for the
    goal weighing."""
    from companion import growth
    spec = _spec(purpose)
    if spec.get(SMELTING_FAMILY):
        product, rate = str(spec[SMELTING_FAMILY]), float(spec.get("rate") or 3.75)
        if site is not None:
            rate = part_size(site, purpose) * growth.FURNACE_RATE["stone-furnace"]
        furnaces = max(1, math.ceil(rate / growth.FURNACE_RATE["stone-furnace"] - 1e-9))
        ore_rate = rate * outpost.RATIO.get(product, 1.0)
        return {product: rate}, {"electric-mining-drill": math.ceil(ore_rate / growth.DRILL_RATE) + 1,
                                 "stone-furnace": furnaces}
    if spec.get(SUPPLY_FAMILY):
        drills = int(spec.get("drills") or 2)
        return {str(spec[SUPPLY_FAMILY]): drills * growth.DRILL_RATE}, {"electric-mining-drill": drills}
    return {}, {}
