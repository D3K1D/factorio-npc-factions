"""A main bus: lanes of plates and intermediates with modules beside it.

Lanes run south, one item per belt, each followed by a two-tile gap. Plates
come in groups of belts side by side (two or four, as players lay them), a gap
after the group: a splitter balancer at the head spreads the smelting's belt
over the group, and the bus keeps count of what each tap takes; once the outer
belt has given away about half its share, another balancer refills it from the
inner ones. A module on the east side takes an input with a *tap*: a splitter
on the lane (a group's outer belt on that side) pushes half into the gap beside
it, a short belt turns east, and underground belts hop the remaining lanes and
groups (exit and entrance in each gap) to the module's input port. A module's output belt runs west back to the bus and turns south into a
new lane. Modules go on both sides, as players build them: a west-side module
is the mirror image of its east-side form, with its ports on its east edge, and
its taps and outputs cross the lanes westward. Lane columns are reserved in
advance (planning ahead), so modules sit beyond the bus's final width and later
lanes never collide with them.
The bus ends in a row of limited chests, one per plate and intermediate lane:
the engineer's own supply for hand building, which fills a few stacks and then
lets the lane back up (science lanes end in the labs instead).

Coordinates are world tile positions; entity positions are tile centres.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from companion.modules import EAST, LOADERS, Module, NORTH, Placed, SOUTH, WEST, loader_tiles

SPACING = 3   # lane, gap, gap
POLE_EVERY = 6
BELT_RATE = 15.0      # a yellow belt, both lanes (items/s)
REBALANCE = 0.5       # a balancer once taps took this share of one belt's flow from a group's outer belt
END_CHEST = "iron-chest"
END_CHEST_SLOTS = 4   # the bar: a few stacks for the engineer, then the lane backs up


@dataclass
class Lane:
    item: str
    start: int            # first row of the lane's belt
    source: str = "head"  # "head" (smelting / outside) or the module that makes it


@dataclass
class Attached:
    module: Module
    x: int                # module west edge
    y: int                # module top row
    taps: list[tuple[str, int]] = field(default_factory=list)   # (item, row)
    output_lane: int | None = None
    side: str = "east"


def mirrored(m: Module) -> Module:
    """The module flipped east to west: belts, inserters and ports keep working, mirrored."""
    swap = {EAST: WEST, WEST: EAST}
    out = Module(m.name)
    out.entities = [replace(e, x=m.width - e.x, direction=swap.get(e.direction, e.direction), **_mirrored_settings(e))
                    for e in m.entities]
    sides = {"left": "right", "right": "left"}   # (a flipped belt keeps its way of travel, its sides swap)
    out.ports = [type(p)(p.item, p.kind, m.width - 1 - p.x, p.y, swap.get(p.direction, p.direction), p.rate,
                         sides.get(p.lane, p.lane))
                 for p in m.ports]
    out.width, out.height, out.makes = m.width, m.height, dict(m.makes)
    return out


def _mirrored_settings(e: Placed) -> dict:
    """An imported block's own settings flipped east to west (science_blocks.Kept): a splitter's left and right
    priorities swap, an inserter's pickup and drop offsets turn about the north-south line."""
    settings = getattr(e, "settings", None)
    if not settings:
        return {}
    side = {"left": "right", "right": "left"}
    out = {}
    for k, v in settings.items():
        if k in ("input_priority", "output_priority"):
            v = side.get(v, v)
        elif k in ("pickup_position", "drop_position") and isinstance(v, dict):
            v = {**v, "x": -float(v.get("x") or 0)}
        out[k] = v
    return {"settings": out}


class Bus:
    def __init__(self, x: int, y: int, columns: int, length: int):
        """`columns` lanes are reserved, starting at tile (x, y) and running `length` rows south."""
        self.x, self.y, self.columns, self.length = x, y, columns, length
        self.lanes: dict[int, Lane] = {}
        self.attached: list[Attached] = []
        self.used_rows: set[int] = set()
        self.crossing: dict[str, set[int]] = {"east": set(), "west": set()}   # rows a side's belts cross its pole line
        self.next_module_y = y + 2
        self.next_west_y = y + 2
        self.groups: dict[int, int] = {}          # first column of a group of adjacent belts -> its size
        self.supply: dict[str, float] = {}        # what the smelting (or a module) puts on an item's lanes
        self.taken: dict[str, float] = {}         # what the taps take from them
        self.drawn: dict[tuple[str, str], float] = {}   # (item, side): taken from that outer belt since a balancer
        self.balancers: list[tuple[int, int, int]] = []  # (first column, belts, first row)

    # --- geometry --------------------------------------------------------
    def group_of(self, column: int) -> tuple[int, int]:
        """(first column, size) of the group `column` belongs to; a lone lane is a group of one."""
        for first, size in self.groups.items():
            if first <= column < first + size:
                return first, size
        return column, 1

    def lane_x(self, index: int) -> int:
        """Belts of a group sit side by side; a two-tile gap follows every group (and lone lane)."""
        x = self.x
        for column in range(1, index + 1):
            x += 1 if self.group_of(column)[0] == self.group_of(column - 1)[0] else SPACING
        return x

    @property
    def east(self) -> int:
        """First tile east of the reserved bus."""
        return self.lane_x(self.columns - 1) + SPACING

    @property
    def module_x(self) -> int:
        return self.east + 3

    @property
    def bottom(self) -> int:
        return self.y + self.length

    def lane_of(self, item: str) -> int | None:
        for index, lane in sorted(self.lanes.items()):
            if lane.item == item:
                return index
        return None

    def lanes_of(self, item: str) -> list[int]:
        return sorted(c for c, lane in self.lanes.items() if lane.item == item)

    def outer(self, item: str, side: str = "east") -> int | None:
        """The belt a module on `side` taps (or feeds): the item's outermost belt on that side, beside a gap."""
        columns = self.lanes_of(item)
        if not columns:
            return None
        return columns[0] if side == "west" else columns[-1]

    def free_column(self, belts: int = 1) -> int:
        for index in range(self.columns - belts + 1):
            span = range(index, index + belts)
            if all(c not in self.lanes and self.group_of(c) == (c, 1) for c in span):
                return index
        raise ValueError("no reserved bus column left")

    # --- building ----------------------------------------------------------
    def add_lane(self, item: str, column: int | None = None, start: int | None = None, source: str = "head",
                 belts: int = 1, supply: float | None = None) -> int:
        """A lane, or a group of `belts` lanes side by side for one item (plates), with a balancer at its
        head that spreads what comes in over all of them."""
        column = self.free_column(belts) if column is None else column
        for c in range(column, column + belts):
            self.lanes[c] = Lane(item, self.y if start is None else start, source)
        if belts > 1:
            self.groups[column] = belts
            rows = self.balancer_rows(belts)
            self.balancers.append((column, belts, self.y if start is None else start))
            self.used_rows.update(range(self.y if start is None else start, (self.y if start is None else start) + rows))
        if supply is not None:
            self.supply[item] = self.supply.get(item, 0.0) + supply
        return column

    @staticmethod
    def balancer_rows(belts: int) -> int:
        return 1 if belts <= 2 else 2

    def balancer(self, column: int, belts: int, row: int) -> list[Placed]:
        """Splitters across a group: pairs (1-2, 3-4) on the first row, then the middle pair (2-3) on the
        next, so the inner belts refill the outer ones, as players rebalance a bus."""
        out = []
        for k in range(0, belts - 1, 2):
            out.append(Placed("splitter", self.lane_x(column + k) + 1.0, row + 0.5, SOUTH))
        for k in range(1, belts - 1, 2):
            out.append(Placed("splitter", self.lane_x(column + k) + 1.0, row + 1.5, SOUTH))
        return out

    def flow(self) -> dict[str, tuple[float, float]]:
        """Per item with a known supply: (put on the bus, taken by taps), items/s."""
        return {item: (rate, self.taken.get(item, 0.0)) for item, rate in self.supply.items()}

    def _draw(self, item: str, side: str, rate: float, after: int) -> None:
        """Count a tap on an item's outer belt; when that belt has given away about half its share since
        the last balancer, put another balancer below the tap (the first free rows from `after`)."""
        self.taken[item] = self.taken.get(item, 0.0) + rate
        columns = self.lanes_of(item)
        if len(columns) < 2:
            return
        first, belts = self.group_of(columns[0])
        key = (item, side)
        self.drawn[key] = self.drawn.get(key, 0.0) + rate
        share = min(BELT_RATE, self.supply.get(item, BELT_RATE * belts) / belts)
        if self.drawn[key] < REBALANCE * share:
            return
        rows = self.balancer_rows(belts)
        row = after
        while any(r in self.used_rows for r in range(row - 1, row + rows + 1)):
            row += 1
        self.balancers.append((first, belts, row))
        self.used_rows.update(range(row, row + rows))
        self.drawn[(item, "east")] = self.drawn[(item, "west")] = 0.0

    @property
    def west_x(self) -> int:
        """Column of a west-side module's port tiles (its east edge)."""
        return self.x - 4

    def attach(self, module: Module, *, y: int | None = None, side: str = "east") -> Attached:
        """Place `module` beside the bus with its top at row `y`, or at the first free rows below the
        last module on that side where every tap is below the start of the lane it takes from."""
        west = side == "west"
        if west:
            module = mirrored(module)
        for port in module.ports:
            if port.kind == "in" and self.lane_of(port.item) is None:
                raise ValueError(f"no bus lane carries {port.item}")
        # Fluid ports sit on the far edge and are piped from outside; only belt ports meet the bus.
        belt_ports = [p for p in module.ports if p.kind in ("in", "out")]

        def clash(top: int) -> bool:
            for port in belt_ports:
                row = top + port.y
                if row in self.used_rows or row - 1 in self.used_rows:
                    return True
                if port.kind == "in" and row - 1 <= self.lanes[self.lane_of(port.item)].start:
                    return True   # the tap's splitter must sit on the lane, below its first belt
            return False

        if y is not None:
            top = y
            if clash(top):
                raise ValueError(f"bus rows for a module at {top} are already used")
        else:
            top = self.next_west_y if west else self.next_module_y
            while clash(top):
                top += 1
        placed = Attached(module, self.west_x - module.width + 1 if west else self.module_x, top, side=side)
        for port in belt_ports:
            row = top + port.y
            self.used_rows.update({row - 1, row})
            self.crossing[side].add(row)
            if port.kind == "in":
                placed.taps.append((port.item, row))
            else:
                column = self.outer(port.item, side)
                if column is None:
                    column = self.add_lane(port.item, start=row, source=module.name)
                placed.output_lane = column
        for port in belt_ports:
            if port.kind == "in":
                self._draw(port.item, side, port.rate, top + port.y + 1)
        self.attached.append(placed)
        if west:
            self.next_west_y = top + module.height + 2
        else:
            self.next_module_y = top + module.height + 2
        return placed

    def entities(self) -> list[Placed]:
        return [e for _, part in self.segments() for e in part]

    def segments(self) -> list[tuple[str, list[Placed]]]:
        """The bus in build order: one segment per module, holding the lane belts and poles from the
        previous module down to this one, the module's taps, the module and its output. Players
        extend a bus the same way, one module at a time."""
        taps_at: set[tuple[int, int]] = set()   # (column, row) with a splitter
        for placed in self.attached:
            for item, row in placed.taps:
                taps_at.add((self.outer(item, placed.side), row - 1))
        order = sorted(self.attached, key=lambda a: a.y)
        bounds = []
        for i, placed in enumerate(order):
            # Each segment carries the lanes past its own module (taps and output included), so a
            # module beside another on the far side still gets its inputs when it is built.
            start = self.y if i == 0 else bounds[-1][1]
            own = placed.y + placed.module.height + 1
            end = self.bottom if i + 1 == len(order) else max(own, order[i + 1].y, start)
            bounds.append((start, min(end, self.bottom)))
        poles = self._poles()
        out = []
        for placed, (start, end) in zip(order, bounds):
            part: list[Placed] = []
            west = placed.side == "west"
            for item, row in placed.taps:
                tap = self._tap_west if west else self._tap
                part.extend(tap(self.outer(item, placed.side), row, placed.x))
            for column, belts, row in self.balancers:
                if start <= row < end or (placed is order[0] and row < start):
                    part.extend(self.balancer(column, belts, row))
            if placed.output_lane is not None:
                port = next(p for p in placed.module.ports if p.kind == "out")
                output = self._output_west if west else self._output
                part.extend(output(placed.output_lane, placed.y + port.y, placed.x))
            for e in placed.module.entities:
                part.append(replace(e, x=e.x + placed.x, y=e.y + placed.y))   # (an imported block's settings kept)
            for column, lane in self.lanes.items():
                lx = self.lane_x(column)
                for row in range(max(start, lane.start), end):
                    if (column, row) in taps_at:
                        continue
                    if lane.source not in ("head", "side") and row == lane.start:
                        continue  # the module's output belt turns into the lane here
                    part.append(Placed("transport-belt", lx + 0.5, row + 0.5, SOUTH))
            part.extend(p for p in poles if start <= p.y < end)
            if placed is order[-1]:
                part.extend(self._end_chests())
            out.append((placed.module.name, drop_overlaps(part)))
        return out

    def _end_chests(self) -> list[Placed]:
        """At the bus end, an inserter lifts each plate and intermediate lane into a limited chest; a
        pole in every other gap powers them."""
        capped = sorted(c for c, lane in self.lanes.items() if not lane.item.endswith("science-pack"))
        out = []
        for column in capped:
            lx = self.lane_x(column)
            out.append(Placed("inserter", lx + 0.5, self.bottom + 0.5, NORTH))   # picks the lane end, drops south
            out.append(Placed(END_CHEST, lx + 0.5, self.bottom + 1.5, NORTH, bar=END_CHEST_SLOTS))
        if capped:
            # poles across the end row in the gaps, on to the bus pole line's column, wire the inserters in
            for gx in self._gap_poles([self.lane_x(c) for c in capped], self.east + 1):
                out.append(Placed("small-electric-pole", gx + 0.5, self.bottom + 0.5, NORTH))
        return out

    def gaps(self) -> list[int]:
        """The first gap tile after every group and lone lane (where poles can stand across the bus)."""
        out = [self.x - 1]
        for column in range(self.columns):
            if column + 1 >= self.columns or self.group_of(column + 1)[0] != self.group_of(column)[0]:
                out += [self.lane_x(column) + 1, self.lane_x(column) + 2]
        return out

    def _gap_poles(self, powered: list[int], last: int, first: int | None = None) -> list[int]:
        """Pole columns across the bus, in gaps only: every tile in `powered` within a pole's 5x5 supply
        area, each pole within wire reach of the next, ending at column `last` (and starting at `first`)."""
        gaps = sorted(set(self.gaps() + [last] + ([first] if first is not None else [])))
        poles = [first] if first is not None else []
        need = sorted(powered)
        while need:
            x = need[0]
            reach = [g for g in gaps if g - 2 <= x <= g + 2 and (not poles or g - poles[-1] <= 7)]
            if not reach:
                reach = [g for g in gaps if g > (poles[-1] if poles else x - 3)][:1]
                if not reach:
                    break
            g = max(reach)
            poles.append(g)
            need = [n for n in need if n > g + 2]
        while poles and last - poles[-1] > 7:   # on to the pole line, through the gaps
            nxt = max(g for g in gaps if g - poles[-1] <= 7)
            if nxt <= poles[-1]:
                break
            poles.append(nxt)
        if not poles or poles[-1] != last:
            poles.append(last)
        return [p for i, p in enumerate(poles) if p is not None and p not in poles[:i]]

    def _tap(self, column: int, row: int, module_x: int) -> list[Placed]:
        """Splitter on the lane one row above `row`, then east along `row` to the module."""
        lx = self.lane_x(column)
        parts = [Placed("splitter", lx + 1.0, row - 1 + 0.5, SOUTH),
                 Placed("transport-belt", lx + 1.5, row + 0.5, EAST)]
        x = lx + 2
        for first_x, last_x in self._runs([c for c in self.lanes if c > column and self.lanes[c].start <= row]):
            parts.append(Placed("underground-belt", x + 0.5, row + 0.5, EAST, kind="input"))
            parts.append(Placed("underground-belt", last_x + 1.5, row + 0.5, EAST, kind="output"))
            x = last_x + 2
            # a lane that starts further south is just an empty column: plain belts cross it
        for bx in range(x, module_x):
            parts.append(Placed("transport-belt", bx + 0.5, row + 0.5, EAST))
        return parts

    def _tap_west(self, column: int, row: int, module_x: int) -> list[Placed]:
        """The mirror of `_tap`: the splitter pushes half into the gap west of the lane, and the belt
        runs west along `row` to a west-side module's port."""
        lx = self.lane_x(column)
        parts = [Placed("splitter", lx + 0.0, row - 1 + 0.5, SOUTH),
                 Placed("transport-belt", lx - 0.5, row + 0.5, WEST)]
        x = lx - 2
        for first_x, last_x in reversed(self._runs([c for c in self.lanes
                                                    if c < column and self.lanes[c].start <= row])):
            parts.append(Placed("underground-belt", x + 0.5, row + 0.5, WEST, kind="input"))
            parts.append(Placed("underground-belt", first_x - 0.5, row + 0.5, WEST, kind="output"))
            x = first_x - 2
        for bx in range(self.west_x + 1, x + 1):
            parts.append(Placed("transport-belt", bx + 0.5, row + 0.5, WEST))
        return parts

    def _output_west(self, column: int, row: int, module_x: int) -> list[Placed]:
        """The mirror of `_output`: a west-side module's output runs east and turns south into its lane."""
        lx = self.lane_x(column)
        parts = [Placed("transport-belt", lx + 0.5, row + 0.5, SOUTH)]
        x = self.west_x + 1
        for first_x, last_x in self._runs([c for c in self.lanes if c < column and self.lanes[c].start < row]):
            for bx in range(x, first_x - 1):
                parts.append(Placed("transport-belt", bx + 0.5, row + 0.5, EAST))
            parts.append(Placed("underground-belt", first_x - 0.5, row + 0.5, EAST, kind="input"))
            parts.append(Placed("underground-belt", last_x + 1.5, row + 0.5, EAST, kind="output"))
            x = last_x + 2
        for bx in range(x, lx):
            parts.append(Placed("transport-belt", bx + 0.5, row + 0.5, EAST))
        return parts

    def _output(self, column: int, row: int, module_x: int) -> list[Placed]:
        """The module's output belt runs west along `row` and turns south into its lane."""
        lx = self.lane_x(column)
        parts = [Placed("transport-belt", lx + 0.5, row + 0.5, SOUTH)]
        x = module_x - 1
        for first_x, last_x in reversed(self._runs([c for c in self.lanes
                                                    if c > column and self.lanes[c].start < row])):
            for bx in range(last_x + 2, x + 1):
                parts.append(Placed("transport-belt", bx + 0.5, row + 0.5, WEST))
            parts.append(Placed("underground-belt", last_x + 1.5, row + 0.5, WEST, kind="input"))
            parts.append(Placed("underground-belt", first_x - 0.5, row + 0.5, WEST, kind="output"))
            x = first_x - 2
        for bx in range(lx + 1, x + 1):
            parts.append(Placed("transport-belt", bx + 0.5, row + 0.5, WEST))
        return parts

    def _runs(self, columns: list[int]) -> list[tuple[int, int]]:
        """The given lanes as runs of belts side by side (a group), west to east: (first x, last x). A
        crossing belt hops each run with one underground pair (a group is at most four wide)."""
        out: list[tuple[int, int]] = []
        for x in sorted(self.lane_x(c) for c in columns):
            if out and x == out[-1][1] + 1:
                out[-1] = (out[-1][0], x)
            else:
                out.append((x, x))
        return out

    def _poles(self) -> list[Placed]:
        """A pole line between the bus and the modules. Tap and output belts cross that column, so
        each next pole goes on the farthest free row within wire reach (seven rows)."""
        out = []
        two_sided = any(a.side == "west" for a in self.attached)
        columns = [(self.east + 1, "east")] + ([(self.x - 2, "west")] if two_sided else [])
        if two_sided:
            # Poles across the bus in the lane gaps join the west line to the east one.
            row = self.y
            while row in self.used_rows:
                row += 1
            for gx in self._gap_poles([], self.east + 1, first=self.x - 2):
                if gx not in (self.x - 2, self.east + 1):   # the two pole lines stand there already
                    out.append(Placed("small-electric-pole", gx + 0.5, row + 0.5, NORTH))
        for x, side in columns:
            # Only this side's tap and output belts cross its pole line.
            crossing = self.crossing[side]
            row = self.y
            while row in self.used_rows:
                row += 1
            while row < self.bottom:
                out.append(Placed("small-electric-pole", x + 0.5, row + 0.5, NORTH))
                nxt = next((r for r in range(row + 7, row, -1) if r not in crossing), None)
                if nxt is None:   # seven crossing rows in a row: the line breaks here
                    nxt = row + 8
                    while nxt in crossing:
                        nxt += 1
                row = nxt
        return out


def drop_overlaps(entities: list[Placed]) -> list[Placed]:
    """Bus belts give way to splitters, undergrounds, loaders and module entities on the same tile."""
    import math
    solid = set()
    for e in entities:
        if e.name != "transport-belt":
            if e.name in LOADERS:
                solid |= loader_tiles(e.x, e.y, e.direction)
            elif e.name == "splitter":
                # a south-facing splitter covers the tile west and east of its centre
                solid.update({(math.floor(e.x - 0.5), math.floor(e.y)), (math.floor(e.x + 0.5), math.floor(e.y))})
            else:
                size = 3 if e.name.startswith("assembling") else 1
                for dx in range(size):
                    for dy in range(size):
                        solid.add((math.floor(e.x - size / 2 + dx + 0.5), math.floor(e.y - size / 2 + dy + 0.5)))
    seen, kept = set(), []
    for e in entities:
        tile = (math.floor(e.x), math.floor(e.y))
        if e.name == "transport-belt":
            if tile in solid or tile in seen:
                continue
            seen.add(tile)
        kept.append(e)
    return kept
