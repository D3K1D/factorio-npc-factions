"""Bus modules for blue science: red circuits, engines, blue packs and steel.

They share one row layout (as the mall does): two input belts run east above a
row of assemblers, belt A (rows 5) for plain inserters and belt B (row 4) for
long-handed ones, each carrying up to two items, one per lane, from the bus.
A recipe whose ingredient is not on a belt takes it straight from the
neighbouring assembler that makes it (a cable assembler between two red
circuit assemblers, a pipe assembler between two engine assemblers). Products
leave on an output belt along row 11 that turns down to the output port.

    rows 0-3   B's inputs merge (second item loops round to the head's east side)
    row 4      belt B (long-handed inserters)        row 5   belt A (inserters)
    row 6      input inserters, poles in the gaps    rows 7-9  assemblers
    row 10     output inserters, poles               row 11  output belt, west
    rows 8-10  A's inputs merge at the west end      row 13  output port

Steel uses the same edge with furnaces: iron and coal share belt A, long-handed
inserters lift steel from the furnaces over row 10 onto the output belt.
"""

from __future__ import annotations

import math

from companion.modules import (ASSEMBLER, EAST, INSERTER_RATE, Module, NORTH, Placed, Port, SOUTH, WEST,
                               _belt, _drop_overlaps)
from companion.ratios import Book

FIRST_X = 6


def _inputs(m: Module, end: int, a: tuple[str, ...], b: tuple[str, ...], rates: dict[str, float]) -> None:
    """Belts A (row 5) and B (row 4) from the west edge, each item on its own lane."""
    if b:
        _belt(m, 0, 2, EAST); _belt(m, 1, 2, EAST)
        m.ports.append(Port(b[0], "in", 0, 2, EAST, rates.get(b[0], 0.0)))
        if len(b) > 1:
            for x in range(0, 4):
                _belt(m, x, 0, EAST)
            _belt(m, 4, 0, SOUTH); _belt(m, 4, 1, SOUTH); _belt(m, 4, 2, WEST); _belt(m, 3, 2, WEST)
            m.ports.append(Port(b[1], "in", 0, 0, EAST, rates.get(b[1], 0.0)))
        _belt(m, 2, 2, SOUTH); _belt(m, 2, 3, SOUTH)
        for x in range(2, end):
            _belt(m, x, 4, EAST)
    _belt(m, 0, 8, EAST); _belt(m, 1, 8, EAST)
    m.ports.append(Port(a[0], "in", 0, 8, EAST, rates.get(a[0], 0.0)))
    if len(a) > 1:
        for x in range(0, 4):
            _belt(m, x, 10, EAST)
        _belt(m, 4, 10, NORTH); _belt(m, 4, 9, NORTH); _belt(m, 4, 8, WEST); _belt(m, 3, 8, WEST)
        m.ports.append(Port(a[1], "in", 0, 10, EAST, rates.get(a[1], 0.0)))
    _belt(m, 2, 8, NORTH); _belt(m, 2, 7, NORTH); _belt(m, 2, 6, NORTH)
    for x in range(2, end):
        _belt(m, x, 5, EAST)


def _output(m: Module, end: int, item: str, rate: float) -> None:
    """The output belt west along row 11, down to the port at row 13."""
    for x in range(2, end):
        _belt(m, x, 11, WEST)
    _belt(m, 1, 11, SOUTH); _belt(m, 1, 12, SOUTH); _belt(m, 1, 13, WEST); _belt(m, 0, 13, WEST)
    m.ports.append(Port(item, "out", 0, 13, WEST, rate))


def _kind(rate: float) -> str:
    return next((k for k in ("inserter", "fast-inserter") if INSERTER_RATE[k] >= rate), "fast-inserter")


def row_module(name: str, book: Book, order: list[str], a: tuple[str, ...], b: tuple[str, ...], product: str,
               rate: float, machine: str = ASSEMBLER) -> Module:
    """Assemblers for `order` in a row along belts A and B; `product` leaves for the bus."""
    m = Module(name)
    end = FIRST_X + 4 * len(order)
    speed = book.machines[machine].speed
    _inputs(m, end, a, b, {})
    position = {}
    # (its machines run at the row's rate, not flat out: inserters sized for that; a defense row of 0.06 magazines a
    # second wanted a fast inserter it could not make, and failed in the game at once: W-23)
    full = sum(speed / book.recipes[rc].energy * book.recipes[rc].makes(product) for rc in order
               if book.recipes[rc].makes(product) > 0)
    share = min(1.0, rate / full) if full > 0 and rate > 0 else 1.0
    for k, recipe in enumerate(order):
        x0 = FIRST_X + 4 * k
        position.setdefault(recipe, []).append(x0)
        r = book.recipes[recipe]
        crafts = speed / r.energy * share
        m.entities.append(Placed(machine, x0 + 1.5, 8.5, NORTH, recipe))
        on_a = [n for n, _ in r.ingredients if n in a]
        on_b = [n for n, _ in r.ingredients if n in b]
        if on_a:
            m.entities.append(Placed(_kind(crafts * sum(q for n, q in r.ingredients if n in a)), x0 + 0.5, 6.5, NORTH))
        if on_b:
            m.entities.append(Placed("long-handed-inserter", x0 + 1.5, 6.5, NORTH))
        if r.makes(product) > 0:   # (by what it makes: mods name recipes freely)
            m.entities.append(Placed(_kind(crafts * r.makes(product)), x0 + 1.5, 10.5, NORTH))
        m.entities.append(Placed("small-electric-pole", x0 - 0.5, 6.5))
        m.entities.append(Placed("small-electric-pole", x0 - 0.5, 10.5))
    # Gap inserters: an ingredient on no belt comes from the neighbour that makes it.
    for k, recipe in enumerate(order):
        x0 = FIRST_X + 4 * k
        for n, _ in book.recipes[recipe].ingredients:
            if n in a or n in b:
                continue
            if k + 1 < len(order) and book.recipes[order[k + 1]].makes(n):
                m.entities.append(Placed("inserter", x0 + 3.5, 8.5, EAST))    # picks east, drops west
            elif k > 0 and book.recipes[order[k - 1]].makes(n):
                m.entities.append(Placed("inserter", x0 - 0.5, 8.5, WEST))    # picks west, drops east
            else:
                raise ValueError(f"{recipe} at {k} has no source for {n}")
    m.entities.append(Placed("small-electric-pole", end - 0.5, 6.5))
    m.entities.append(Placed("small-electric-pole", end - 0.5, 10.5))
    m.entities.append(Placed("small-electric-pole", 1.5, 5.5))   # anchor by the bus edge
    _output(m, end, product, rate)
    m.width, m.height = end, 14
    m.makes = {product: rate}
    return _drop_overlaps(m)


def _count(rate: float, book: Book, recipe: str, item: str, machine: str) -> int:
    r = book.recipes[recipe]
    return max(1, math.ceil(rate / (book.machines[machine].speed / r.energy * r.makes(item)) - 1e-9))


def _groups(n: int, outer: str, inner: str) -> list[str]:
    """[outer, inner, outer] groups (the inner one feeds both), then [outer, inner] for an odd one."""
    order = []
    while n >= 2:
        order += [outer, inner, outer]
        n -= 2
    if n:
        order += [outer, inner]
    return order


def red_circuits(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    n = _count(rate, book, "advanced-circuit", "advanced-circuit", machine)
    return row_module("red-circuits", book, _groups(n, "advanced-circuit", "copper-cable"),
                      a=("copper-plate", "electronic-circuit"), b=("plastic-bar",), product="advanced-circuit",
                      rate=rate, machine=machine)


def engines(rate: float, book: Book, machine: str = ASSEMBLER, gears_on_site: bool = False) -> Module:
    """Engines, a pipe assembler between each pair. `gears_on_site` (as the guides advise): a gear
    assembler at each end of a pair hands its gears straight in, made from the iron that comes for the
    pipes, so no gears ride the bus for engines."""
    n = _count(rate, book, "engine-unit", "engine-unit", machine)
    order = _groups(n, "engine-unit", "pipe")
    if gears_on_site:   # gear, engine, pipe, engine, gear, engine, pipe, ...: a gear assembler serves both sides
        out = ["iron-gear-wheel"]
        for group in [order[i:i + 3] for i in range(0, len(order), 3)]:
            out += [*group] + (["iron-gear-wheel"] if len(group) == 3 else [])
        order = out
    return row_module("engines", book, order,
                      a=("steel-plate",) if gears_on_site else ("steel-plate", "iron-gear-wheel"),
                      b=("iron-plate",), product="engine-unit", rate=rate, machine=machine)


def engines_on_site(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    return engines(rate, book, machine, gears_on_site=True)


def green_science_on_site(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    """Green science with its gears made on site: per pack assembler, the inserters and belts it takes
    come straight from the assemblers beside it, and their gears from a gear assembler beside each of
    those (gear, inserter, science, belt, gear, inserter, ...: a gear assembler serves both sides); iron
    and circuits come on one belt."""
    n = _count(rate, book, "logistic-science-pack", "logistic-science-pack", machine)
    order = ["iron-gear-wheel"] + ["inserter", "logistic-science-pack", "transport-belt", "iron-gear-wheel"] * n
    m = row_module("green-science", book, order, a=("iron-plate", "electronic-circuit"), b=(),
                   product="logistic-science-pack", rate=rate, machine=machine)
    return m


def blue_science(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    n = _count(rate, book, "chemical-science-pack", "chemical-science-pack", machine)
    return row_module("blue-science", book, ["chemical-science-pack"] * n,
                      a=("engine-unit", "advanced-circuit"), b=("sulfur",), product="chemical-science-pack",
                      rate=rate, machine=machine)


def steel(rate: float, book: Book, furnace: str = "stone-furnace") -> Module:
    """Furnaces in a row (pitch 3) on belt A (iron and coal); long-handed inserters lift steel over row 10
    onto the output belt."""
    r = book.recipes["steel-plate"]
    speed = 2.0 if furnace == "steel-furnace" else 1.0
    n = max(1, math.ceil(rate / (speed / r.energy) - 1e-9))
    m = Module("steel")
    end = FIRST_X + 3 * n
    _inputs(m, end, ("iron-plate", "coal"), (), {})
    for k in range(n):
        x0 = FIRST_X + 3 * k
        m.entities.append(Placed(furnace, x0 + 1.0, 8.0, NORTH))                 # rows 7-8
        m.entities.append(Placed("inserter", x0 + 0.5, 6.5, NORTH))              # belt A into the furnace
        m.entities.append(Placed("long-handed-inserter", x0 + 0.5, 9.5, NORTH))  # furnace (row 7) to row 11
        m.entities.append(Placed("small-electric-pole", x0 + 2.5, 6.5))
        m.entities.append(Placed("small-electric-pole", x0 + 2.5, 10.5))   # reaches the row-9 inserters
    m.entities.append(Placed("small-electric-pole", 5.5, 6.5))
    m.entities.append(Placed("small-electric-pole", 1.5, 5.5))
    _output(m, end, "steel-plate", rate)
    m.width, m.height = end, 14
    m.makes = {"steel-plate": rate}
    return _drop_overlaps(m)
