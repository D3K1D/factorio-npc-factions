"""The build history: one library of builds that every engineer shares, in every world and game, and that
gets better with use (companion/blueprints.py keeps the layouts themselves, one JSON file each).

Besides its layout, each build keeps (all of it optional, so older library files load as they are):
- lineage: the build it was adapted from, its own variants, and which gaps the adaptation was for;
- job: what it is for, what it makes and takes at full speed, what it burns and draws, its size and parts;
- uses: where it was built (world, engineer), how that ended, and how it measured in the factory ledger;
- notes: every measurable gap between what it was meant to do and what it did, with the evidence.

Notes are not a list of known problems. A use that has run long enough to be measured becomes readings:
pairs of what was expected and what was measured, one for each number the game reports about its block
(each state its machines spent time in, each item they made where the plan set how busy it would be,
the buildings and machines still standing, the power it gave). A reading off by more than TOLERANCE is a
gap, and the gaps of all its uses are its notes. A state or a number the game's export adds later gives
readings, and so notes, by itself.

    build_history.attribute(catalog, site)   # the site's blocks built from library builds: their uses
    build_history.notes(build)               # what its uses showed, worst first
    build_history.performance(build)         # how its uses ran, against an untried build (for the score)
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any, Iterable, Mapping

from companion import ledger, rates

TOLERANCE = 0.15       # a reading further off than this (a share of the expected rate, or of the time) is a gap
MAX_USES = 40          # uses kept per build (the newest)
EVIDENCE = 3           # uses quoted as a note's evidence
EXPORT_LIMIT = 64      # blocks the game exports at most: a base that long may have dropped its oldest ones
IDLE = "backed_up"     # where a build spends the time it is not meant to work: its output not taken
WORKING = "working"
PRIOR, PRIOR_USES = 0.8, 2   # an untried build is taken to run 80% of the time, as if seen twice
MATURE = ledger.MATURE_MINUTES

# Plain words for the states the game reports (any other state still gives notes, in its own name).
STATES = {"starved": "short of inputs", "backed_up": "backed up, its output not taken",
          "no_power": "without power", "low_power": "on low power", "no_fuel": "out of fuel",
          "depleted": "out of ore", "idle": "idle with nothing to make", "other": "stopped for other reasons"}


def _list(value: Any) -> list:
    return [] if isinstance(value, dict) and not value else list(value or [])


def _round(value: float) -> float:
    return round(float(value), 4)


def use_key(world: Any, npc: Any, plan: Any, tick: Any) -> str:
    """One use: a plan an engineer built in a world (saves of one map share a world id, so the tick the
    plan was accepted at tells two games apart)."""
    return f"{world}|{npc}|{plan}|{tick if isinstance(tick, int) else ''}"


def uses(build: Mapping[str, Any]) -> list[dict]:
    return [u for u in _list(build.get("uses")) if isinstance(u, Mapping)]


def notes(build: Mapping[str, Any]) -> list[dict]:
    """Its notes, worst first: one for each reading that was off in any of its measured uses."""
    return [n for n in _list(build.get("notes")) if isinstance(n, Mapping)]


def _use(build: dict, key: str, **fields: Any) -> tuple[dict, bool]:
    found = next((u for u in uses(build) if u.get("key") == key), None)
    if found is not None:
        return found, False
    use = {"key": key, **{k: v for k, v in fields.items() if v is not None}}
    build["uses"] = (uses(build) + [use])[-MAX_USES:]
    return use, True


# --- readings: what a use was expected to do against what it did ---------------------------------------

def readings(block: Mapping[str, Any], build: Mapping[str, Any]) -> dict[str, list[float]]:
    """A measured block as [measured, expected] pairs. A build with a planned load (a mall works a sixth
    of the time) is expected to make its full speed times that, and to spend the rest of the time backed
    up; one that follows its consumers (a smelting column) to make what they take, so its time backed up
    is capacity it did not need. Its machines are expected to spend no time in any other state, and every
    building it was built with to stand."""
    seg = ledger.Segment.from_block(block)
    load = seg.expected
    out: dict[str, list[float]] = {}
    if load is not None:
        for item, rate in sorted(seg.makes.items()):
            out[f"out:{item}"] = [_round(seg.made.get(item, 0.0)), _round(rate * load)]
    for state, share in sorted(seg.shares.items()):
        if state != WORKING:
            out[f"share:{state}"] = [_round(share), _round(1.0 - load if state == IDLE and load is not None else 0.0)]
    if isinstance(block.get("buildings"), (int, float)) and block.get("planned"):
        out["standing"] = [int(block["buildings"]), int(block["planned"])]
    planned = Counter(e.get("prototype") for e in _list(build.get("entities")) if isinstance(e, Mapping))
    for name, count in sorted((block.get("machines") or {}).items()):
        if isinstance(count, (int, float)):
            out[f"machines:{name}"] = [int(count), int(planned.get(name, 0))]
    measured = block.get("measured") if isinstance(block.get("measured"), Mapping) else {}
    power = (build.get("job") or {}).get("power_kw")
    if isinstance(measured.get("power_mw"), (int, float)) and power:
        out["power"] = [_round(measured["power_mw"]), _round(float(power) / 1000 * (load if load is not None else 1.0))]
    return out


def gap(key: str, measured: float, expected: float) -> float:
    """How far a reading is off: shares of time as they are (fractions already), everything else as a
    share of what was expected; something there that was not expected at all counts as it is."""
    if key.startswith("share:"):
        return measured - expected
    if expected > 0:
        return (measured - expected) / expected
    return float(measured)


def describe(key: str, measured: float, expected: float) -> str:
    """A reading in plain words."""
    space, _, name = key.partition(":")
    if space == "out":
        if expected > 0:
            return f"made {measured:.3g} {name} a second against {expected:.3g} expected ({measured / expected:.0%})"
        return f"made {measured:.3g} {name} a second"
    if space == "share":
        return f"{STATES.get(name, name.replace('_', ' '))} {measured:.0%} of the time (expected {expected:.0%})"
    if space == "machines":
        if expected:
            return f"{measured:g} of the {expected:g} {name} it was built with standing"
        return f"holds {measured:g} {name} it was not built with"
    if key == "standing":
        return f"{measured:g} of its {expected:g} buildings standing"
    if key == "built":
        return "its build did not finish"
    if key == "stood":
        return f"gone after {measured:.0f} minutes, before it had run long enough to be measured"
    if key == "power":
        return f"gave {measured:.3g} MW against {expected:.3g} MW expected"
    return f"{key.replace(':', ' ').replace('_', ' ')}: {measured:.3g} against {expected:.3g} expected"


def derive_notes(build: Mapping[str, Any]) -> list[dict]:
    """Its notes from all its measured uses: for each reading, how often and how far it was off, with the
    latest uses that showed it as evidence."""
    measured = [u for u in uses(build) if isinstance(u.get("readings"), Mapping) and u["readings"]]
    keys = sorted({k for u in measured for k in u["readings"]})
    out = []
    for key in keys:
        rows = []
        for u in measured:
            r = u["readings"].get(key)
            if r is None and key.startswith("share:") and u.get("minutes"):
                # measured, and never in that state: no time at all
                state, load = key[6:], u.get("load")
                r = [0.0, 1.0 - float(load) if state == IDLE and load is not None else 0.0]
            if isinstance(r, (list, tuple)) and len(r) == 2:
                rows.append((u, float(r[0]), float(r[1])))
        off = [(u, m, e) for u, m, e in rows if abs(gap(key, m, e)) > TOLERANCE]
        if not off:
            continue
        m = sum(row[1] for row in off) / len(off)
        e = sum(row[2] for row in off) / len(off)
        text = describe(key, m, e)
        reason = next((u.get("reason") for u, _, _ in reversed(off) if u.get("reason")), None)
        if reason:
            text += f" ({str(reason)[:80]})"
        out.append({"key": key, "text": text, "gap": round(sum(gap(key, mm, ee) for _, mm, ee in off) / len(off), 3),
                    "seen": len(off), "of": len(rows),
                    "evidence": [{k: v for k, v in (("world", u.get("world")), ("engineer", u.get("engineer")),
                                                    ("minutes", u.get("minutes") or u.get("gone")),
                                                    ("measured", mm), ("expected", ee)) if v is not None}
                                 for u, mm, ee in off[-EVIDENCE:]]})
    out.sort(key=lambda n: (-abs(n["gap"]) * n["seen"] / max(1, n["of"]), n["key"]))
    return out


# --- what its uses say of it ----------------------------------------------------------------------------

def capability(use: Mapping[str, Any]) -> float | None:
    """How much of the time a use's machines could run: all of it but what they stood for want of inputs,
    power, fuel, ore or anything else (time backed up is spare: its output was not wanted), with only the
    part of it still standing. A build that did not finish ran none of it. None: not measured yet."""
    r = use.get("readings") if isinstance(use.get("readings"), Mapping) else {}
    built = r.get("built")
    if built and built[0] < built[1]:
        return 0.0
    if not use.get("minutes"):
        return None
    lost = sum(float(v[0]) for k, v in r.items() if k.startswith("share:") and k != f"share:{IDLE}")
    standing = r.get("standing")
    stand = min(1.0, float(standing[0]) / float(standing[1])) if standing and standing[1] else 1.0
    return max(0.0, 1.0 - lost) * stand


def ability(build: Mapping[str, Any]) -> float | None:
    """The mean capability of its measured uses (None before any): how much of its full speed it gives."""
    values = [c for c in (capability(u) for u in uses(build)) if c is not None]
    return sum(values) / len(values) if values else None


def performance(build: Mapping[str, Any]) -> float:
    """Its uses against an untried build's: above 0 it ran better than one is assumed to, below worse
    (a few uses count for little, many for much)."""
    values = [c for c in (capability(u) for u in uses(build)) if c is not None]
    if not values:
        return 0.0
    return (sum(values) + PRIOR * PRIOR_USES) / (len(values) + PRIOR_USES) - PRIOR


# --- its job ----------------------------------------------------------------------------------------------

def _add(table: dict, key: Any, value: float) -> None:
    if key:
        table[key] = table.get(key, 0.0) + value


def _smelting(recipes: Iterable[Mapping[str, Any]], mined: set[str], categories: Iterable[str]) -> Mapping | None:
    """The recipe a furnace without one set runs: the one of its categories that takes what the build's
    drills mine (furnaces choose their recipe from what they are fed)."""
    wanted = set(categories)
    for r in recipes:
        made_in = set(_list(r.get("categories"))) or {r.get("category") or "crafting"}
        if made_in & wanted and any(i.get("name") in mined for i in _list(r.get("ingredients"))):
            return r
    return None


def _rates(build: Mapping[str, Any], facts: rates.Facts, site: Mapping[str, Any]) -> dict:
    """What its machines do at full speed in this game: what they make and take (net, with what it makes
    and uses itself cancelled, and its outputs held to what its own supply of an ingredient allows), what
    they burn, draw and generate."""
    recipes = [r for r in _list(site.get("recipes")) if isinstance(r, Mapping)]
    by_name = {r.get("name"): r for r in recipes}
    on = {r["entity_id"]: r["resource"] for r in _list(build.get("resource_requirements"))
          if isinstance(r, Mapping) and r.get("resource") != "water"}
    mined = set(on.values())
    makes: dict[str, float] = {}
    takes: dict[str, float] = {}
    burns = draws = power = 0.0
    for e in _list(build.get("entities")):
        f = facts.entities.get(e.get("prototype"))
        if not f:
            continue
        kind, speed = f.get("kind"), float(f.get("speed") or 0)
        if kind == "drill" and e.get("id") in on:   # (with the team's mining productivity research)
            _add(makes, on[e["id"]], speed * (1 + float(f.get("productivity") or 0)
                                              + facts.bonuses.get("mining_productivity", 0)))
        elif kind == "crafter":
            recipe = by_name.get(e.get("recipe")) if e.get("recipe") else _smelting(recipes, mined, _list(f.get("categories")))
            if recipe and float(recipe.get("energy") or 0) > 0:
                crafts = speed / float(recipe["energy"])
                for p in _list(recipe.get("products")):
                    _add(makes, p.get("name"), crafts * float(p.get("amount") or 1) * (1 + float(f.get("productivity") or 0)))
                for i in _list(recipe.get("ingredients")):
                    _add(takes, i.get("name"), crafts * float(i.get("amount") or 1))
        elif kind == "generator":
            power += float(f.get("max_kw") or 0)
        burns += facts.fuel_per_second(e.get("prototype")) or 0
        draws += facts.kw(e.get("prototype")) or 0
    limit = min((min(1.0, makes[i] / takes[i]) for i in takes if i in makes and takes[i] > 0), default=1.0)
    net_makes, net_takes = ledger._net(makes, takes)
    out = {"makes": {k: _round(v * limit) for k, v in sorted(net_makes.items())},
           "takes": {k: _round(v) for k, v in sorted(net_takes.items())}}
    if burns:
        out["burns"] = {rates.FUEL: _round(burns)}
    if draws:
        out["draws_kw"] = round(draws, 1)
    if power:
        out["power_kw"] = round(power, 1)
    return out


def job_of(build: Mapping[str, Any], site: Mapping[str, Any] | None = None) -> dict:
    """What a build is for and what it does: its purpose and kind, its size and parts, and its rates from
    this game's facts when the request has them (its machines, mods and research), else as the library
    knew them (the facts of the game it was saved in, or what its last measured use's machines could make)."""
    parts: dict[str, int] = {}
    for bill in _list(build.get("stage_bills")):
        for item, n in (bill or {}).items():
            parts[item] = parts.get(item, 0) + int(n)
    job = {"purpose": build.get("purpose"), "kind": ledger.kind_of(build.get("purpose")),
           "size": list(build.get("size") or []), "tiles": len(_list(build.get("footprint"))),
           "parts": dict(sorted(parts.items())), "outputs": list(build.get("outputs") or []),
           "resources": sorted({r.get("resource") for r in _list(build.get("resource_requirements"))
                                if isinstance(r, Mapping) and r.get("resource")})}
    facts = rates.of(site) if site else None
    if facts:
        job.update(_rates(build, facts, site))
        return job
    known = build.get("job") if isinstance(build.get("job"), Mapping) else {}
    for key in ("makes", "takes", "burns", "draws_kw", "power_kw"):
        if known.get(key):
            job[key] = known[key]
    if not job.get("makes"):
        latest = next((u.get("capacity") for u in reversed(uses(build)) if u.get("capacity")), None)
        if latest:
            job["makes"] = dict(latest)
    return job


# --- the library's records --------------------------------------------------------------------------------

def new_build(plan: Mapping[str, Any], site: Mapping[str, Any], *, source: str, purpose: str,
              parent: str | None = None, addressed: Iterable[str] = (), need: Mapping[str, float] | None = None,
              changes: str = "") -> dict:
    """A plan as a library build, with its job; an adapted one names its parent, the gaps it was for and
    the need it was adapted to meet."""
    from companion import blueprints as bp
    build = bp.from_plan(plan, site, source=source, purpose=purpose)
    build["job"] = job_of(build, site)
    if parent:
        build["lineage"] = {"parent": parent, "variants": [], "addressed": sorted(set(addressed)),
                            "need": {k: _round(v) for k, v in (need or {}).items()}, "changes": str(changes)[:300]}
    return build


def link_variant(catalog: Any, parent_id: str, child_id: str) -> None:
    """The parent lists its new variant (the variant names its parent already)."""
    with catalog._lock:
        parent = catalog.blueprints.get(parent_id)
        if not parent or parent_id == child_id:
            return
        lineage = parent.setdefault("lineage", {"parent": None, "variants": []})
        variants = list(lineage.get("variants") or [])
        if child_id not in variants:
            lineage["variants"] = variants + [child_id]
            catalog.save(parent)


def note_outcome(build: dict, use: Mapping[str, Any]) -> None:
    """How a use's build ended (the game's status row): into its use, with a reading when it did not
    finish, and its notes again. The caller holds the library's lock and saves."""
    key = use_key(use.get("world"), use.get("npc"), use.get("plan"), use.get("tick"))
    entry, _ = _use(build, key, world=use.get("world"), npc=use.get("npc"), engineer=use.get("engineer"),
                    plan=use.get("plan"), tick=use.get("tick"))
    for field in ("outcome", "reason", "first_output_s", "complete_s"):
        if use.get(field) is not None:
            entry[field] = use[field]
    if use.get("outcome") and use["outcome"] != "complete":
        entry["readings"] = {**(entry.get("readings") or {}), "built": [0, 1]}
    build["notes"] = derive_notes(build)


# Uses seen standing during this run, by world and engineer: only those can be missed when they are gone
# (a library outlives the game; another game of the same save has a base of its own).
_seen: dict[tuple, dict[str, int]] = {}
_ticks: dict[tuple, int] = {}
_seen_lock = threading.Lock()


def attribute(catalog: Any, site: Mapping[str, Any]) -> list[str]:
    """The site's blocks built from library builds (the plan's ledger block names its build), as uses of
    those builds. A use is written when it is first seen, when it has run long enough to be measured
    (and again each time it has run twice as long), and when it is gone; never on every sample.
    Returns the builds written."""
    world, npc, tick = site.get("world_id"), site.get("npc_id"), site.get("tick")
    blocks = [b for b in _list(site.get("base")) if isinstance(b, Mapping)]
    if world is None or npc is None or not isinstance(tick, int):
        return []
    here = (world, npc)
    with _seen_lock:
        if tick < _ticks.get(here, tick):
            _seen.pop(here, None)   # the game went back (a save was loaded): what stood then is not known now
        _ticks[here] = tick
        seen = _seen.setdefault(here, {})
    present: set[str] = set()
    changed: set[str] = set()
    with catalog._lock:
        for block in blocks:
            plan = block.get("ledger") if isinstance(block.get("ledger"), Mapping) else {}
            build = catalog.blueprints.get(plan.get("build")) if isinstance(plan.get("build"), str) else None
            if not build:
                continue
            accepted = block.get("accepted") if isinstance(block.get("accepted"), int) else None
            key = use_key(world, npc, block.get("id"), accepted)
            present.add(key)
            use, created = _use(build, key, world=world, npc=npc, engineer=site.get("npc_name"),
                                plan=str(block.get("id")), tick=accepted if accepted is not None else tick)
            with _seen_lock:
                seen[key] = tick
            if created or use.get("seen") is None:
                use["seen"] = tick
                changed.add(build["id"])
            measured = block.get("measured") if isinstance(block.get("measured"), Mapping) else {}
            minutes = int(measured.get("minutes") or 0)
            if minutes >= MATURE and (not use.get("minutes") or minutes >= 2 * int(use["minutes"])):
                seg = ledger.Segment.from_block(block)
                use.update(minutes=minutes, readings={**{k: v for k, v in (use.get("readings") or {}).items()
                                                         if k == "built"}, **readings(block, build)},
                           capacity={k: _round(v) for k, v in seg.makes.items()})
                if seg.expected is not None:
                    use["load"] = seg.expected
                else:
                    use.pop("load", None)
                changed.add(build["id"])
        if len(blocks) < EXPORT_LIMIT:
            with _seen_lock:
                missing = [k for k in seen if k not in present]
            for key in missing:
                build = next((b for b in catalog.blueprints.values() if any(u.get("key") == key for u in uses(b))), None)
                use = next((u for u in uses(build) if u.get("key") == key), None) if build else None
                with _seen_lock:
                    seen.pop(key, None)
                if use is None or use.get("gone") is not None:
                    continue
                start = use.get("tick") if isinstance(use.get("tick"), int) else use.get("seen", tick)
                use["gone"] = max(0, (tick - int(start)) // 3600)
                if not use.get("minutes") and use["gone"] < MATURE:
                    use["readings"] = {**(use.get("readings") or {}), "stood": [use["gone"], MATURE]}
                changed.add(build["id"])
        for build_id in sorted(changed):
            build = catalog.blueprints[build_id]
            build["notes"] = derive_notes(build)
            catalog.save(build)
    return sorted(changed)


def forget() -> None:
    """Nothing seen standing so far is known any more (tests; a new game)."""
    with _seen_lock:
        _seen.clear()
        _ticks.clear()
