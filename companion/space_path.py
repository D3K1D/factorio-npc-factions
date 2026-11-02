"""The goals that make what the way to space needs next, where the science path's own builder cannot (R-3).

The science path (companion/tier_path.py) says what is due: a supply the tier ahead takes from machines of its own, a
pack line, the rocket's part. The science path's goal (catalog_planner "science-tier", companion/tier_builds.py) builds
what a row of assemblers or furnaces fed from chests can make. What it cannot (oil: crude from pumpjacks, gas from a
refinery, plastic and sulfur from chemical plants, everything fluid) is made by goals of their own, which waited to be
"suggested" and were never weighed: after the plot wall the engineer built the bus for a whole stage (proving
stage-oil, stage-blue, stage-silo, 2026-10-28).

**The class of problem**: once a tier's research is done, the production it needs is not offered as the next step on
the way to space. Its variants: oil gathering and processing for plastic and sulfur; blue science's red circuits and
engines; the silo and its parts after the silo's research; a mod's pack made from a fluid; a goal for the item that
waits on another goal first (processing on gathering); a goal with no layout here yet (said, not dropped).

**One mechanism**: for every due item the science path's builder passes over, the goals that make it: those whose
recipes make it, or that mine it, or that the goal list says make it (`makes`). A goal not available yet because
another must stand first brings that one (its `requires_built`, followed down). They are weighed with the rest as
asked for by the science path; one that cannot be had here is said with why.

    goals(site, designer=False)        [(goal, item, why)] to weigh, the nearest first
    passed(site, designer=False)       what was wanted and could not be offered, and why
"""

from __future__ import annotations

from typing import Any, Mapping

from companion import tier_path

DEPTH = 4   # goals followed down their `requires_built` at most


def _products(site: Mapping[str, Any]) -> dict[str, set[str]]:
    """recipe -> what it makes, as the site's recipe list says (the recipes unlocked)."""
    out: dict[str, set[str]] = {}
    for r in site.get("recipes") if isinstance(site.get("recipes"), list) else ():
        if isinstance(r, Mapping) and r.get("name"):
            out[str(r["name"])] = {str(p.get("name")) for p in r.get("products") or () if isinstance(p, Mapping)}
    return out


def makers(item: str, site: Mapping[str, Any]) -> list[str]:
    """The goals that make `item`: by the goal list's `makes`, the resource it mines, or a recipe of it that makes it
    (an item named for its recipe too)."""
    from companion.catalog_planner import PURPOSES
    products = _products(site)
    out = []
    for goal, spec in PURPOSES.items():
        if spec.get("architect") or spec.get("bus") or spec.get("tier"):
            continue   # (the factory plan, the bus and the science path's own goal answer it otherwise)
        made = set(spec.get("makes") or ())
        for key in ("electric_smelting", "electric_supply"):   # (what a smelting or supply array makes)
            if spec.get(key):
                made.add(str(spec[key]))
        if spec.get("resource"):
            made.add(str(spec["resource"]))
        # (its recipes: those it needs, and those any one of which it makes, plastic or sulfur: DS-2's `requires_any`)
        for recipe in [*(spec.get("requires_recipes") or ()), *(spec.get("requires_any") or ())]:
            made |= products.get(recipe, set()) or ({recipe} if recipe == item else set())
        if item in made:
            out.append(goal)
    return out


OWN = "science-tier"   # the science path's own goal (companion/tier_builds.py)


def _wanted(site: Mapping[str, Any]) -> list[tuple[str, str, bool]]:
    """The due items on the way to space, with why they are due and whether the science path's own goal builds them
    (a researched tier's line on the way to space: blue science, the silo and its part), most pressing first. A pack
    already made growing, a supply its own goal makes (steel), labs: the science path's goal weighs those itself."""
    from companion import tier_builds
    path = tier_path.assess(site)
    out = []
    for d in path.dues():
        if d.item == tier_path.LAB or tier_builds.on_bus(site, d.item):
            continue
        own = tier_builds.buildable(d.item, site) is None
        tier = path.tier(d.item)
        if own and not d.waits:
            if tier and tier.rocket and not tier.machine and path.ahead(tier):
                out.append((d.item, d.why, True))   # (the way to space's next line: its goal is the next step)
            continue
        if not own:
            out.append((d.item, d.why, False))
        for w in d.waits:   # (what a due line waits on: its supplies' goals too)
            out.append((w, f"{d.item} waits on it", False))
    first: dict[str, tuple[str, bool]] = {}
    for item, why, mine in out:
        first.setdefault(item, (why, mine))   # (one line an item: its most pressing reason)
    return [(item, why, mine) for item, (why, mine) in first.items()]


def _resolve(goal: str, site: Mapping[str, Any], designer: bool, depth: int = 0) -> tuple[str | None, str | None]:
    """`goal`, or the goal that must stand before it, when one can be built now; else (None, why not)."""
    from companion.catalog_planner import PURPOSES, available, built_purposes, has_built, why_unavailable
    if available(goal, site, designer=designer):
        return goal, None
    if depth < DEPTH:
        done = built_purposes(site)
        for before in PURPOSES[goal].get("requires_built") or ():
            if not has_built(before, done) and before in PURPOSES:
                got, _ = _resolve(before, site, designer, depth + 1)
                if got:
                    return got, None
    spec = PURPOSES[goal]
    why = why_unavailable(goal, site, designer=designer) or ""
    # (a goal the game lays out, with no layout offered here: said so, rather than the planner's general reason)
    if (spec.get("suggested") and goal not in (site.get("suggestions") or {})
            and not (spec.get("standalone") and designer)
            and not why.startswith(("needs research", "build first", "failed here"))):
        why = "the game offered no layout for it here (no room, or nothing unlocked for it yet)"
    return None, why or "not available here"


def _work(site: Mapping[str, Any], designer: bool) -> tuple[list[tuple[str, str, str]], list[str]]:
    offered: list[tuple[str, str, str]] = []
    passed: list[str] = []
    for item, why, own in _wanted(site):
        goals = [OWN] if own else makers(item, site)
        if not goals:
            passed.append(f"{item}: no goal makes it")
            continue
        reasons = []
        for goal in goals:
            got, why_not = _resolve(goal, site, designer)
            if got:
                if got not in [g for g, _, _ in offered]:
                    offered.append((got, item, why if got == goal else f"{goal} for {item} needs it first ({why})"))
                break
            reasons.append(f"{goal}: {why_not}")
        else:
            passed.append(f"{item}: " + "; ".join(reasons))
    return offered, passed


def goals(site: Mapping[str, Any] | None, *, designer: bool = False) -> list[tuple[str, str, str]]:
    """The goals to weigh for what the way to space needs next: (goal, the item it is for, why), nearest first."""
    if not isinstance(site, Mapping):
        return []
    try:
        return _work(site, designer)[0]
    except Exception:   # (a signal, never in the way of a goal)
        return []


def passed(site: Mapping[str, Any] | None, *, designer: bool = False) -> list[str]:
    """What the way to space wants that no goal can be offered for now, and why (said, not dropped)."""
    if not isinstance(site, Mapping):
        return []
    try:
        return _work(site, designer)[1]
    except Exception as exc:
        return [f"the space path failed: {str(exc)[:80]}"]


_SAID: dict = {}


def news(site: Mapping[str, Any] | None, *, designer: bool = False) -> list[str]:
    """passed(), when it differs from what was last said for this engineer (a log line once, not every request)."""
    now = passed(site, designer=designer)
    key = site.get("npc_id") if isinstance(site, Mapping) else None
    if _SAID.get(key) == now:
        return []
    _SAID[key] = now
    return now
