"""What making something by hand costs the engineer, in seconds: crafting it, smelting the plates it has none of
and mining the ore and stone it has none of, less what it carries and keeps in its chests; what its machines make
is fetched (from them, their chests or their belts: the game's acquire does so), not made again by hand.

An emergency build is then bounded by time (companion/threat.py: a share of the time to the next wave), and what
would take too long by hand waits for machines. Walls by hand only in an emergency (the user, 2026-10-24) follows
from the cost, not a rule: a wall from stone is five bricks, about half a minute by hand.
"""
from __future__ import annotations

import math
import threading
from typing import Any, Mapping

MINE_S = 2.0              # one ore, stone, coal or wood mined by hand
SMELT_PARALLEL = 2.0      # hand smelting runs in a furnace or two beside the crafting
SMELT_SPEED = 1.0         # (a stone furnace)
PLACE_S = 1.0             # putting one building down
DEPTH = 8                 # recipe steps followed at most
MINABLE = ("ore", "stone", "coal", "wood")   # what the engineer mines with its own hands (by name)
# What the engineer's own hands craft (the character's categories, Space Age's included): the rest needs machines.
HAND = frozenset({"crafting", "basic-crafting", "electronics", "pressing", "recycling-or-hand-crafting",
                  "organic-or-hand-crafting", "organic-or-assembling", "electronics-or-assembling",
                  "metallurgy-or-assembling"})
SMELTING = frozenset({"smelting"})
# What it smelts by hand (a furnace it feeds itself: mod/scripts/designed_start.lua SMELT_FROM); any other smelted
# item (steel) comes only from furnaces of its own set to it.
HAND_SMELTS = frozenset({"iron-plate", "copper-plate", "stone-brick"})
COLLECT_S = 0.2           # one item its own machines make, fetched
# How much quicker the engineer's hands are than a player's (the game's setting, exported as `hand_speed`: 10 by
# default, the user, 2026-10-26); it crafts what its machines make at a player's speed, and fetches it instead.
HAND_SPEED = 10.0

_BOOKS: dict[int, tuple[Mapping[str, Any], Any]] = {}
_LOCK = threading.Lock()


def book_for(site: Mapping[str, Any]) -> Any:
    from companion import ratios
    with _LOCK:
        hit = _BOOKS.get(id(site))
        if hit and hit[0] is site:
            return hit[1]
    book = ratios.Book.from_site(site)
    with _LOCK:
        if len(_BOOKS) >= 8:
            _BOOKS.pop(next(iter(_BOOKS)))
        _BOOKS[id(site)] = (site, book)
    return book


def held(site: Mapping[str, Any]) -> dict[str, float]:
    """What it has to hand: carried, and in its builds' chests."""
    out: dict[str, float] = {}
    carried = site.get("inventory")
    for name, n in (carried.items() if isinstance(carried, Mapping) else ()):
        if isinstance(n, (int, float)):
            out[str(name)] = out.get(str(name), 0.0) + float(n)
    for b in site.get("base") or []:
        stock = b.get("stock") if isinstance(b, Mapping) else None
        for name, n in (stock.items() if isinstance(stock, Mapping) else ()):
            if isinstance(n, (int, float)):
                out[str(name)] = out.get(str(name), 0.0) + float(n)
    return out


def made_by_base(site: Mapping[str, Any]) -> set[str]:
    """What its own machines make (each block's capacity, as the game measures and plans them)."""
    out: set[str] = set()
    for b in site.get("base") or []:
        cap = b.get("capacity") if isinstance(b, Mapping) else None
        makes = cap.get("makes") if isinstance(cap, Mapping) else None
        if isinstance(makes, Mapping):
            out.update(str(k) for k, v in makes.items() if isinstance(v, (int, float)) and v > 0)
    return out


def obtainable(site: Mapping[str, Any], item: str, book: Any = None) -> bool:
    """Whether it can have `item` now: held, made by hand from what it can mine, or made by its own machines (a
    recipe the game lists with nothing known of it counts: there is no telling)."""
    book = book if book is not None else book_for(site)
    if book.recipe_for(item) is None and not any(m in item for m in MINABLE):
        return True
    return not math.isinf(cost(site, {item: 1}, book))


def cost(site: Mapping[str, Any], wants: Mapping[str, float], book: Any = None,
         have: Mapping[str, float] | None = None) -> float:
    """Seconds of hand work to have `wants` (item: count), from what it holds; inf when one of them only machines
    make (or nobody can) and it has too few."""
    book = book if book is not None else book_for(site)
    left = dict(held(site) if have is None else have)
    machines = made_by_base(site)
    speed = max(1.0, float(site.get("hand_speed") or HAND_SPEED))

    def need(item: str, n: float, depth: int) -> float:
        take = min(left.get(item, 0.0), n)
        if take > 0:
            left[item] = left.get(item, 0.0) - take
            n -= take
        if n <= 1e-9:
            return 0.0
        if item in machines:   # (made by its machines: fetched, from them or their belts, not made again by hand)
            return n * COLLECT_S
        recipe = book.recipe_for(item) if depth <= DEPTH else None
        if recipe is None or not recipe.ingredients:
            return n * MINE_S if any(m in item for m in MINABLE) else math.inf
        if recipe.category in SMELTING and item in HAND_SMELTS:
            per = recipe.energy / SMELT_SPEED / SMELT_PARALLEL
        elif recipe.category in HAND and recipe.category not in SMELTING:
            per = recipe.energy / speed
        else:   # (only machines make it, and none of its own do)
            return math.inf
        crafts = n / (recipe.makes(item) or 1.0)
        seconds = crafts * per
        for name, amount in recipe.ingredients:
            seconds += need(name, crafts * amount, depth + 1)
            if math.isinf(seconds):
                return seconds
        return seconds

    total = 0.0
    for item, n in wants.items():
        total += need(item, float(n), 0)
        if math.isinf(total):
            break
    return total


def most(site: Mapping[str, Any], kit: Mapping[str, float], seconds: float, *, per_unit_s: float = PLACE_S,
         cap: int = 64, book: Any = None, have: Mapping[str, float] | None = None) -> int:
    """The most of `kit` (item: count each, e.g. a turret and its magazines) it can have and put down within
    `seconds` of hand work, from what it holds."""
    if not kit or seconds <= 0:
        return 0
    book = book if book is not None else book_for(site)

    def fits(n: int) -> bool:
        return cost(site, {k: v * n for k, v in kit.items()}, book, have) + per_unit_s * n <= seconds + 1e-9
    lo, hi = 0, cap
    if fits(hi):
        return hi
    while lo < hi:   # (the cost only grows with the count)
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo
