"""Bus modules for military (grey) science: piercing rounds, and the packs from piercing rounds,
grenades and stone walls.

They use blue science's row layout (blue_modules.row_module): two input belts above a row of
assemblers, and an ingredient no belt carries handed over by the assembler beside it. Every recipe is
read from the book the game exports, never written out here: with Bob's Warfare a piercing magazine
takes an empty magazine (recipe "empty-recipe") instead of firearm magazines, and the module follows.

    piercing rounds   [piercing, maker, piercing] groups: the maker in the middle (firearm magazines,
                      or Bob's empty magazines) hands its product to both sides; copper and steel on
                      belt A, the maker's iron on belt B.
    military science  [grenade, science, wall] groups: piercing rounds (from the bus) on belt A, the
                      grenade and wall assemblers' own ingredients (iron, coal, stone bricks) on A and B.
"""

from __future__ import annotations

import math

from companion.blue_modules import _count, _groups, row_module
from companion.bus import END_CHEST
from companion.modules import ASSEMBLER, EAST, LOADER, Module, Placed, WEST
from companion.ratios import Book

PACK = "military-science-pack"
PIERCING = "piercing-rounds-magazine"
# What comes off the bus to these modules (plates, steel, and the side lanes of the military section).
BUS_ITEMS = ("iron-plate", "copper-plate", "steel-plate", "coal", "stone-brick", PIERCING)


class MilitaryError(ValueError):
    """The game's recipes cannot be laid out in these rows (a mod changed them too much)."""


def _recipe(book: Book, item: str):
    r = book.recipe_for(item)
    if r is None:
        raise MilitaryError(f"no recipe makes {item}")
    return r


def _belts(items: list[str], first: tuple[str, ...] = ()) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Up to four items on belt A (inserters) and belt B (long-handed inserters), `first` on A."""
    order = list(dict.fromkeys([*first, *items]))
    if len(order) > 4:
        raise MilitaryError(f"too many inputs for one row: {', '.join(order)}")
    return tuple(order[:2]), tuple(order[2:])


def _per_machine(book: Book, recipe, item: str, machine: str) -> float:
    return book.machines[machine].speed / recipe.energy * recipe.makes(item)


def piercing_rounds(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    """Piercing rounds magazines for the bus, `rate` a second."""
    p = _recipe(book, PIERCING)
    made_here = [n for n, _ in p.ingredients if n not in BUS_ITEMS]
    if len(made_here) != 1:
        raise MilitaryError(f"piercing rounds need {made_here or 'nothing'} made beside them")
    part = made_here[0]
    maker = _recipe(book, part)
    if any(n not in BUS_ITEMS for n, _ in maker.ingredients):
        raise MilitaryError(f"{part} needs more than plates")
    n = _count(rate, book, p.name, PIERCING, machine)
    # A maker between two piercing assemblers when it keeps both going, else one each.
    need = _per_machine(book, p, PIERCING, machine) / p.makes(PIERCING) * dict(p.ingredients)[part]
    if 2 * need <= _per_machine(book, maker, part, machine):
        order = _groups(n, p.name, maker.name)
    else:
        order = [r for _ in range(n) for r in (maker.name, p.name)]
    a, b = _belts([i for i, _ in p.ingredients if i != part] + [i for i, _ in maker.ingredients],
                  first=tuple(i for i, _ in p.ingredients if i != part))
    m = row_module("piercing", book, order, a=a, b=b, product=PIERCING, rate=rate, machine=machine)
    return m


def military_science(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    """Military science packs, `rate` a second: each pack assembler between the two that make what it
    needs besides piercing rounds (grenades and stone walls), the piercing rounds from the bus."""
    pack = _recipe(book, PACK)
    beside = [n for n, _ in pack.ingredients if n not in BUS_ITEMS]
    if len(beside) > 2:
        raise MilitaryError(f"military science needs {', '.join(beside)} made beside it: two at most")
    makers = [_recipe(book, item) for item in beside]
    for maker in makers:
        if any(n not in BUS_ITEMS for n, _ in maker.ingredients):
            raise MilitaryError(f"{maker.name} needs more than the bus brings")
    # Enough pack assemblers that one of each maker beside each keeps up.
    crafts = rate / pack.makes(PACK)                         # pack crafts a second
    n = _count(rate, book, pack.name, PACK, machine)
    for item, maker in zip(beside, makers):
        wanted = crafts * dict(pack.ingredients)[item]
        n = max(n, math.ceil(wanted / _per_machine(book, maker, item, machine) - 1e-9))
    left = [makers[0].name] if makers else []
    right = [makers[1].name] if len(makers) > 1 else []
    order = [r for _ in range(n) for r in (*left, pack.name, *right)]
    own = [i for i, _ in pack.ingredients if i in BUS_ITEMS]
    a, b = _belts(own + [i for maker in makers for i, _ in maker.ingredients], first=tuple(own))
    return row_module("military-science", book, order, a=a, b=b, product=PACK, rate=rate, machine=machine)


BLOCK_RATE = 0.1   # packs a second (6 a minute): the first grey science, before a bus makes more
PURPOSE = "military-science-automation"


def _with_chests(module: Module, limit: float | None = None) -> list[Placed]:
    """The module with a chest at each port: inputs the engineer fills by hand (an inserter lifts each
    onto its belt), the product into a chest it empties; a pole beside each pair of port inserters.
    A port whose rate is more than one inserter moves (`limit`, items/s: loader_limit) gets a loader
    instead, as a player would: an output loader facing the module unloads its chest onto the belt, an
    input loader facing away loads the product belt into its chest, a tile further out."""
    parts = list(module.entities)
    rows = {p.y for p in module.ports}
    lifted = []
    for port in module.ports:
        if limit is not None and port.rate > limit:
            parts.append(Placed(END_CHEST, port.x - 2.5, port.y + 0.5))
            parts.append(Placed(LOADER, port.x - 1.0, port.y + 0.5, EAST if port.kind == "in" else WEST,
                                kind="output" if port.kind == "in" else "input"))
            continue
        lifted.append(port)
        parts.append(Placed(END_CHEST, port.x - 1.5, port.y + 0.5))
        parts.append(Placed("inserter", port.x - 0.5, port.y + 0.5, WEST if port.kind == "in" else EAST))
    poles = set()
    for port in lifted:
        for dy in (1, -1, 2):
            if port.y + dy not in rows and not any(abs(port.y - y) <= 2.5 for y in poles):
                poles.add(port.y + dy)
                break
    parts += [Placed("small-electric-pole", -0.5, y + 0.5) for y in sorted(poles)]
    return parts


def needs(rate: float, book: Book) -> dict[str, float]:
    """What `rate` packs a second take from the bus (piercing rounds and the makers' plates, coal and bricks)."""
    pack = _recipe(book, PACK)
    crafts = rate / pack.makes(PACK)
    out: dict[str, float] = {}
    for item, q in pack.ingredients:
        if item in BUS_ITEMS:
            out[item] = out.get(item, 0.0) + crafts * q
        else:
            maker = _recipe(book, item)
            for n, mq in maker.ingredients:
                out[n] = out.get(n, 0.0) + crafts * q / maker.makes(item) * mq
    return out
