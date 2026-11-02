"""Where the blocks of an engineer's base stand: each block's machines grouped by nearness (the game's `parts`,
base_memory.lua), not one box round all of it.

A block can stand in several places: furnaces by the factory and their drills at a far patch, a power plant by a
distant coal field. A box round all of it takes in the empty ground between, and whatever is planned round such boxes
comes out far too big (run 55: a wall ring of 256 by 263 tiles round a factory of 123 by 100, its far fields and power
plant taken in). So what goes round the base (the wall ring, the areas the defense brain holds) groups these parts.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence, TypeVar

Box = tuple[float, float, float, float]
T = TypeVar("T")

# What is at stake in a machine (by name): labs and the silo most, then assemblers, power and smelting; drills least.
STAKES = (("lab", 10), ("silo", 10), ("assembling-machine", 5), ("boiler", 3), ("steam-engine", 3), ("furnace", 2))


def _rect(box: Any) -> bool:
    return isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)


def _counts(machines: Any) -> dict[str, int]:
    return {str(k): int(v or 0) for k, v in machines.items()} if isinstance(machines, Mapping) else {}


def parts(block: Mapping[str, Any]) -> list[tuple[Box, dict[str, int]]]:
    """A block's parts, in its export's (local) tiles: (box, machines) each. From a game that does not send parts:
    its `machines_box` (else its `box`) with all its machines, as one part. None for a block without machines."""
    out = []
    for p in block.get("parts") or []:
        if isinstance(p, Mapping) and _rect(p.get("box")):
            out.append((tuple(float(v) for v in p["box"]), _counts(p.get("machines"))))
    if out:
        return out
    machines = _counts(block.get("machines"))
    if not any(machines.values()):
        return []
    box = block.get("machines_box") if _rect(block.get("machines_box")) else block.get("box")
    return [(tuple(float(v) for v in box), machines)] if _rect(box) else []


def stake(machines: Mapping[str, int]) -> int:
    """What is at stake in these machines (STAKES; at least 1)."""
    return max(1, sum(next((w for word, w in STAKES if word in name), 1) * int(n or 0) for name, n in machines.items()))


def gap(a: Sequence[float], b: Sequence[float]) -> float:
    """Tiles between two boxes, along the farther axis (0 where they touch or overlap)."""
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0.0)


def cluster(items: Sequence[tuple[Sequence[float], T]], reach: float) -> list[tuple[Box, list[T]]]:
    """Boxes grouped by nearness: any two no more than `reach` apart are in one group, and so on through each other.
    Each group: the box round its own, and their items, in the order given (the group of the first item first)."""
    parent = list(range(len(items)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(items)):
        for j in range(i):
            if gap(items[i][0], items[j][0]) <= reach:
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(root(i), []).append(i)
    out = []
    for members in sorted(groups.values(), key=lambda m: m[0]):
        boxes = [items[i][0] for i in members]
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        out.append((tuple(float(v) for v in box), [items[i][1] for i in members]))
    return out
