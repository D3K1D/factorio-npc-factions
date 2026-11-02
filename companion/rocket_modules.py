"""Bus modules for the rocket: processing units, electric engines, concrete, rocket fuel, LDS and the silo.

Fluid rows are blue_modules.row_module turned for a fluid ingredient. Belt A
(row 5) brings up to two items from the bus as before; the assemblers face
south so their fluid inlet is on row 10, where one pipe runs the length of the
row to a fluid port on the module's far edge (the side away from the bus). The
product leaves on belt B (row 4), which runs west to an output port at the bus
edge: long-handed inserters on row 6 reach from the assembler over belt A.

    row 4      belt B, west: products to the bus     row 5   belt A (inputs)
    row 6      inserters in, long-handed out, poles   rows 7-9  assemblers (facing south)
    row 10     the fluid pipe, port at its east end

The silo module is a 9x9 rocket silo below belts A and B: processing units and
LDS on A, rocket fuel on B.
"""

from __future__ import annotations

import math

from companion.blue_modules import FIRST_X, _inputs, _kind, row_module, _count
from companion.modules import ASSEMBLER, EAST, Module, NORTH, Placed, Port, SOUTH, WEST, _belt, _drop_overlaps
from companion.ratios import Book


def fluid_row(name: str, book: Book, recipe: str, a: tuple[str, ...], fluid: str, rate: float,
              machine: str = ASSEMBLER) -> Module:
    """Assemblers for `recipe` along belt A with `fluid` piped in from the far edge."""
    r = book.recipes[recipe]
    product = r.products[0][0]
    n = _count(rate, book, recipe, product, machine)
    crafts = book.machines[machine].speed / r.energy
    m = Module(name)
    end = FIRST_X + 4 * n
    _inputs(m, end, a, (), {})
    for x in range(0, end):
        _belt(m, x, 4, WEST)
    m.ports.append(Port(product, "out", 0, 4, WEST, rate))
    in_rate = crafts * sum(q for i, q in r.ingredients if i in a)
    for k in range(n):
        x0 = FIRST_X + 4 * k
        m.entities.append(Placed(machine, x0 + 1.5, 8.5, SOUTH, recipe))          # fluid inlet on row 10
        m.entities.append(Placed(_kind(in_rate), x0 + 0.5, 6.5, NORTH))           # belt A into it
        m.entities.append(Placed("long-handed-inserter", x0 + 1.5, 6.5, SOUTH))   # out over A onto B
        m.entities.append(Placed("small-electric-pole", x0 - 0.5, 6.5))
    m.entities.append(Placed("small-electric-pole", end - 0.5, 6.5))
    m.entities.append(Placed("small-electric-pole", 1.5, 5.5))   # anchor by the bus edge
    for x in range(FIRST_X, end):
        m.entities.append(Placed("pipe", x + 0.5, 10.5))
    m.ports.append(Port(fluid, "fluid", end - 1, 10, EAST, crafts * n * dict(r.ingredients)[fluid]))
    m.width, m.height = end, 11
    m.makes = {product: rate}
    return _drop_overlaps(m)


def processing_units(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    return fluid_row("processing-units", book, "processing-unit", ("electronic-circuit", "advanced-circuit"),
                     "sulfuric-acid", rate, machine)


def electric_engines(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    return fluid_row("electric-engines", book, "electric-engine-unit", ("electronic-circuit", "engine-unit"),
                     "lubricant", rate, machine)


def concrete(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    return fluid_row("concrete", book, "concrete", ("stone-brick", "iron-ore"), "water", rate, machine)


def rocket_fuel(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    return fluid_row("rocket-fuel", book, "rocket-fuel", ("solid-fuel",), "light-oil", rate, machine)


def low_density_structures(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    n = _count(rate, book, "low-density-structure", "low-density-structure", machine)
    return row_module("low-density-structures", book, ["low-density-structure"] * n,
                      a=("copper-plate", "steel-plate"), b=("plastic-bar",), product="low-density-structure",
                      rate=rate, machine=machine)


def foundations(rate: float, book: Book, machine: str = ASSEMBLER) -> Module:
    """Space platform foundations, each pair of assemblers sharing a copper cable assembler between them."""
    n = _count(rate, book, "space-platform-foundation", "space-platform-foundation", machine)
    order = []
    while n > 0:
        order += ["space-platform-foundation", "copper-cable"] + (["space-platform-foundation"] if n > 1 else [])
        n -= 2
    return row_module("foundations", book, order, a=("steel-plate", "copper-plate"), b=(),
                      product="space-platform-foundation", rate=rate, machine=machine)


def silo(rate: float = 0.0, book: Book | None = None) -> Module:
    """A rocket silo fed from the bus: processing units and LDS on belt A, rocket fuel on belt B."""
    m = Module("rocket-silo")
    end = FIRST_X + 10
    _inputs(m, end, ("processing-unit", "low-density-structure"), ("rocket-fuel",), {})
    x0 = FIRST_X
    m.entities.append(Placed("rocket-silo", x0 + 4.5, 11.5, NORTH))           # rows 7-15
    m.entities.append(Placed("inserter", x0 + 0.5, 6.5, NORTH))               # belt A into the silo
    m.entities.append(Placed("inserter", x0 + 2.5, 6.5, NORTH))
    m.entities.append(Placed("long-handed-inserter", x0 + 1.5, 6.5, NORTH))  # belt B over A into the silo
    for x in (x0 - 0.5, x0 + 3.5, x0 + 7.5):
        m.entities.append(Placed("small-electric-pole", x, 6.5))
    m.entities.append(Placed("small-electric-pole", 1.5, 5.5))
    m.width, m.height = end, 16
    m.makes = {"rocket-part": rate}
    return _drop_overlaps(m)
