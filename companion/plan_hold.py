"""A plan starved of its own items yields to what would make them (F-11).

A plan holds the engineer while its items come by hand (ore mined, plates hand-smelted, crafted): the game finds a hand
route for nearly everything, so such a plan never waits, never fails, and the goals that would make its items by
machine never get a turn. One plot-turrets plan took a death world's whole hour, its turrets crafted from
hand-smelted iron, and steam power was never built (the proving ground, 2026-10-27).

So a plan gets a budget of hand work for its own items (`hand_hold`, in ticks), and only when something available
now would make them by machine. The game counts the plan's hand work; over the budget, with no attack on, it hands
the plan back (what stands stays, no failure is noted: designed_start.lua, DesignedStart.hand_back) and says so
(site["yielded"]). The next goal is then what makes those items (`after`): its defense supply row, else what that row
waits on (power first), else the science path's build. Once nothing would make them, the plan is offered again as
before; each hand-back of the same purpose doubles its next budget, so it never goes back and forth for long. Under
attack it never hands back, and nothing here goes before the defense then.

The ladder: hand work within its budget, it carries on; over it, it hands back (logged in the game); the maker is
chosen (logged); nothing makes them: said, back to the plan with twice the budget; attacked: the plan holds.
"""
from __future__ import annotations

from typing import Any, Mapping

HOLD = 6 * 3600          # ticks of hand work for its own items before a plan hands back (when something would make them)
MAX_DOUBLINGS = 4        # (a purpose that hands back again and again: at most 16 times the budget)
YIELD_FOR = 15 * 3600    # ticks a hand-back is acted on: past it, the plan's own goal comes first again
PAUSED_HOLD = 600        # (the game's least: a defense put up by hand while its defense row is set aside, W-23)


def items_of(plan: Mapping[str, Any]) -> list[str]:
    """The items a plan builds with (its buildings' prototypes) and those it keeps supplied by hand (a turret's
    magazines), in order, once each."""
    out: list[str] = []
    for e in plan.get("entities") or []:
        name = e.get("prototype") or e.get("name") if isinstance(e, Mapping) else None
        if isinstance(name, str) and name not in out:
            out.append(name)
    for stage in plan.get("stages") or []:
        for supply in (stage.get("manual_supplies") or []) if isinstance(stage, Mapping) else []:
            item = supply.get("item") if isinstance(supply, Mapping) else None
            if isinstance(item, str) and item not in out:
                out.append(item)
    return out


HAND_SHARE = 0.03   # (a share of its time lately spent making an item by hand that counts: hand_items)


def hand_items(site: Mapping[str, Any]) -> list[str]:
    """What the engineer has been making by hand lately (its time use: crafted or mined), most first: its upkeep's
    crafts count with the plan's own items (a turret plan held while magazines were crafted for the turrets)."""
    shares = (site.get("time_use") or {}).get("shares") if isinstance(site.get("time_use"), Mapping) else None
    got: dict[str, float] = {}
    for tag, share in (shares or {}).items():
        parts = str(tag).split(":")
        if len(parts) >= 3 and parts[1] in ("craft", "mine") and float(share or 0) >= HAND_SHARE:
            got[parts[2]] = got.get(parts[2], 0.0) + float(share)
    return sorted(got, key=lambda i: -got[i])


def _built(goal: str, site: Mapping[str, Any]) -> bool:
    from companion import catalog_planner as cp
    return cp.has_built(goal, cp.built_purposes(site))


def _with_prerequisites(goal: str, site: Mapping[str, Any], designer: bool) -> list[str]:
    """`goal` if it can be built now; else the goals it waits on being built (power for an assembler row) that can be,
    when its recipes are known; else nothing."""
    from companion import catalog_planner as cp, defense_production, ratios
    if goal == defense_production.GOAL:   # (its row for the item asked: ready, whatever else its own pick is now)
        return [goal] if cp.defense_production_ready(site, goal) else \
            [g for g in cp.PURPOSES[goal]["requires_built"] if not _built(g, site)
             and cp.available(g, site, designer=designer)
             and all(r in ratios.unlocked(site) for r in cp.PURPOSES[goal]["requires_recipes"])]
    if cp.available(goal, site, designer=designer):
        return [goal]
    spec = cp.PURPOSES.get(goal) or {}
    unlocked = ratios.unlocked(site)
    if not all(r in unlocked for r in spec.get("requires_recipes") or []):
        return []
    return [g for g in spec.get("requires_built") or [] if not _built(g, site) and cp.available(g, site, designer=designer)]


def makers(site: Mapping[str, Any], items: list[str], purpose: str | None = None, *,
           designer: bool = False) -> list[str]:
    """The goals that would make some of `items` by machine now, best first (never `purpose` itself): the defense
    supply row while one of them is short of what the waves will take, the science path's build when it makes one of
    them; each, if it cannot be built yet, as what it waits on (power first)."""
    out: list[str] = []
    wanted = set(items)

    def add(goals: list[str]) -> None:
        for g in goals:
            if g != purpose and g not in out:
                out.append(g)
    from companion import defense_production
    try:
        short = [n.item for n in defense_production.draw(site).short() if n.item in wanted]
    except Exception:
        short = []
    # (first the pick over all of them, the same the supply makes after a hand-back: plan_hold.focus; then each item's
    # own maker: a row of it, or for walls the wall machinery; each else what it waits on)
    for chosen in ([set(short)] if short else []) + [{item} for item in short]:
        got = defense_production.pick(site, lambda g: bool(_with_prerequisites(g, site, designer)), items=chosen)
        if got:
            add(_with_prerequisites(got.goal, site, designer))
    try:
        from companion import catalog_planner as cp, tier_builds
        build = tier_builds.cached_pick(site) if cp.available("science-tier", site, designer=designer) else None
        if build and build.item in wanted:
            add(["science-tier"])
    except Exception:
        pass
    return out


def countable(site: Mapping[str, Any], plan: Mapping[str, Any], *, designer: bool = False) -> list[str]:
    """The items whose hand work counts against the plan's budget: its own and what it has been making by hand lately
    (hand_items), each only when something would make it by machine (so a hand-back always has a maker to go to)."""
    purpose = plan.get("purpose")
    items = items_of(plan) + [i for i in hand_items(site) if i not in items_of(plan)]
    return [i for i in items if makers(site, [i], purpose, designer=designer)]


def hand_hold(site: Mapping[str, Any], plan: Mapping[str, Any], *, designer: bool = False) -> int | None:
    """The plan's budget of hand work for the countable items, in ticks: none when none is; doubled for each time this
    purpose handed back lately (site["yields"], cleared when it completes)."""
    purpose = plan.get("purpose")
    if not countable(site, plan, designer=designer):
        return None
    if supplies_defense(site.get("paused")) and own_hand_defense(purpose):
        # (its defense row set aside for this, in a wave: back to the row at the first quiet minute, W-23; a doubled
        # budget held the engineer at hand-made turrets for the rest of the run while the row stood half built)
        return PAUSED_HOLD
    n = int((site.get("yields") or {}).get(purpose) or 0) if isinstance(site.get("yields"), Mapping) else 0
    return HOLD * 2 ** min(n, MAX_DOUBLINGS)


def yielded(site: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The plan handed back lately over its hand work (purpose, items, tick), or None: none, past YIELD_FOR, or its
    base attacked now (the defense goes first then, as before)."""
    y = site.get("yielded")
    if not isinstance(y, Mapping) or site.get("under_attack"):
        return None
    if float(site.get("tick") or 0) - float(y.get("tick") or 0) > YIELD_FOR:
        return None
    return y


def focus(site: Mapping[str, Any]) -> set[str] | None:
    """What a plan handed back lately was making, else the defense items made by hand over and over (repeated): its
    defense supply makes those first, or None."""
    got = focuses(site)
    return got[0] if got else None


def focuses(site: Mapping[str, Any]) -> list[set[str]]:
    """The item sets its defense supply tries first, in turn: a plan handed back lately's, then what was made by hand
    over and over (a hand-back whose items no row can make leaves the next one its turn: Codex, W-23)."""
    y = yielded(site)
    out = [{i for i in (y or {}).get("items") or [] if isinstance(i, str)}, set(repeated_items(site))]
    return [items for items in out if items]


def after(site: Mapping[str, Any], *, designer: bool = False) -> tuple[str | None, str]:
    """After a hand-back: (the goal that makes its items, why) or (None, why) when nothing would (back to the plan)."""
    y = yielded(site)
    if not y:
        return None, ""
    items = [i for i in y.get("items") or [] if isinstance(i, str)]
    minutes = float(y.get("hand_ticks") or 0) / 3600
    goals = makers(site, items, y.get("purpose"), designer=designer)
    what = ", ".join(items) or "its items"
    if goals:
        return goals[0], (f"{y.get('purpose')} handed back after {minutes:.0f} min making {what} by hand: "
                          f"what makes them by machine first")
    return None, f"nothing makes {what} by machine yet: back to {y.get('purpose')}"


def hand_made(site: Mapping[str, Any]) -> dict[str, float]:
    """Its hand work per item lately, in ticks, counted across plans by the game (DesignedStart.hand_made)."""
    got = site.get("hand_made")
    out: dict[str, float] = {}
    for item, ticks in (got.items() if isinstance(got, Mapping) else []):
        try:
            if isinstance(item, str) and float(ticks) > 0:
                out[item] = float(ticks)
        except (TypeError, ValueError):
            pass
    return out


def repeated(site: Mapping[str, Any], *, designer: bool = False) -> tuple[str, list[str], float] | None:
    """What is made by hand over and over gets its machine between rounds (W-23): under steady attack each reinforce
    plan finished within its own budget and the next began at nought, so turrets and piercing rounds were made by hand
    for hours while a row for them could be built (normal-7b, NPC 4). Once the defense items something would make have
    taken HOLD of hand work across plans, and no attack is on, (the maker, those items, minutes); else None."""
    made = hand_made(site)
    items = [i for i in repeated_items(site) if makers(site, [i], designer=designer)]
    total = sum(made[i] for i in items)
    if not items or total < HOLD:
        return None
    goals = makers(site, items, designer=designer)
    return (goals[0], items, total / 3600) if goals else None


def repeated_items(site: Mapping[str, Any]) -> list[str]:
    """The defense items made by hand across its plans, most first, once they took HOLD together and no attack is on
    (whatever would make them: the supply's own pick looks at these first, `focus`)."""
    if site.get("under_attack"):
        return []
    from companion import defense_production
    defense = {n.item for n in defense_production.draw(site).needs}   # (its defense items: the opening's own are weighed)
    made = hand_made(site)
    items = sorted((i for i in made if i in defense), key=lambda i: (-made[i], i))
    return items if sum(made[i] for i in items) >= HOLD else []


def made_by(site: Mapping[str, Any], plan: Mapping[str, Any]) -> list[str]:
    """What `plan` makes by machine of the defense items made by hand over and over (its row's item; walls for the wall
    machinery): those counts start over once it stands (plan["hand_clear"]), never for what it only waits on (power)."""
    from companion import defense_production, wall_block
    items = set(repeated_items(site))
    row = plan.get("defense_production") if isinstance(plan.get("defense_production"), Mapping) else {}
    if plan.get("purpose") == defense_production.GOAL and row.get("item") in items:
        return [row["item"]]
    if plan.get("purpose") == wall_block.PURPOSE:
        return sorted(items & set(defense_production.perimeter_walls(site)))
    return []


def supplies_defense(purpose: Any) -> bool:
    """A goal that makes its defense items by machine (the defense supply's row, the wall machinery): a build of it is
    part of the defense, not set aside for more hand-made defense between waves (W-23)."""
    from companion import defense_production, wall_block
    return purpose in (defense_production.GOAL, wall_block.PURPOSE)


def own_hand_defense(purpose: Any) -> bool:
    """Its own defense put up by hand (reinforce, its plot's turrets): what yields to its defense row set aside in a
    wave. Not a breach mended (open until it is), nor an ally's call (theirs to end), nor the rows themselves (Codex,
    W-23)."""
    from companion import catalog_planner as cp, goal_brain, perimeter
    if not isinstance(purpose, str) or purpose == perimeter.BREACH or supplies_defense(purpose):
        return False
    return goal_brain.defense(purpose) and not (cp.PURPOSES.get(purpose) or {}).get("ally_help")
