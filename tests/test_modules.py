"""Generated building blocks: sized by the ratio calculator, tidy, and connectable to a bus."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.modules import green_circuits, red_science, gears  # noqa: E402
from companion.ratios import Book  # noqa: E402
from companion import bus_planner  # noqa: E402
from unittest import mock  # noqa: E402

SIZE = {"assembling-machine-2": 3, "assembling-machine-1": 3}


def mall_goal(saved):
    """A new bus's own mall section (mall.OWN), as its plan names it."""
    from companion import mall
    return mall.extension_goal(mall.OWN, saved["mall"])


def tiles(module):
    taken = {}
    for e in module.entities:
        n = SIZE.get(e.name, 1)
        for dx in range(n):
            for dy in range(n):
                tile = (math.floor(e.x - n / 2 + dx + 0.5), math.floor(e.y - n / 2 + dy + 0.5))
                taken.setdefault(tile, []).append(e.name)
    return taken


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.book = Book.vanilla()

    def check_shape(self, module):
        clashes = {t: names for t, names in tiles(module).items() if len(names) > 1}
        self.assertEqual(clashes, {})
        for e in module.entities:
            self.assertTrue(0 <= e.x <= module.width and 0 <= e.y <= module.height, e)
        for port in module.ports:
            self.assertEqual(port.x, 0, port)  # every input and output is on the bus side
            self.assertIn((port.x, port.y), tiles(module))

    def test_green_circuit_line_is_the_classic_three_to_two(self):
        m = green_circuits(3.0, self.book)
        self.check_shape(m)
        recipes = [e.recipe for e in sorted(m.entities, key=lambda e: e.x) if e.recipe]
        self.assertEqual(recipes, ["copper-cable", "electronic-circuit", "copper-cable", "electronic-circuit",
                                   "copper-cable"])
        self.assertEqual({p.item for p in m.ports if p.kind == "in"}, {"iron-plate", "copper-plate"})
        self.assertEqual(m.makes, {"electronic-circuit": 3.0})
        self.assertEqual(m.counts().get("inserter", 0), 0)  # this line needs fast inserters throughout

    def test_bigger_targets_repeat_the_unit(self):
        m = green_circuits(6.0, self.book)
        self.check_shape(m)
        self.assertEqual(sum(1 for e in m.entities if e.recipe == "electronic-circuit"), 4)

    def test_red_science_is_sized_by_the_calculator(self):
        m = red_science(0.5, self.book)
        self.check_shape(m)
        self.assertEqual(sum(1 for e in m.entities if e.recipe == "automation-science-pack"), 4)
        self.assertEqual(sum(1 for e in m.entities if e.recipe == "iron-gear-wheel"), 1)
        out = [p for p in m.ports if p.kind == "out"]
        self.assertEqual([p.item for p in out], ["automation-science-pack"])

    def test_busy_machines_get_more_or_faster_inserters(self):
        m = gears(3.0, self.book)  # 2 machines, each eating 3 iron/s
        feeders = [e for e in m.entities if "inserter" in e.name and e.y < 3]
        self.assertTrue(all(e.name == "fast-inserter" for e in feeders))
        self.assertGreaterEqual(len(feeders), 4)

    def test_plan_entities_are_ready_for_the_game(self):
        plan = red_science(0.5, self.book).plan(dx=10, dy=5, prefix="red_")
        ids = [e["id"] for e in plan]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(e["id"].startswith("red_") for e in plan))
        self.assertTrue(any(e.get("recipe") == "automation-science-pack" for e in plan))



class BusTests(unittest.TestCase):
    def test_taps_cross_lanes_underground_and_outputs_start_new_lanes(self):
        from companion.bus import Bus, drop_overlaps
        book = Book.vanilla()
        bus = Bus(0, 0, columns=4, length=20)
        bus.add_lane("iron-plate")
        bus.add_lane("copper-plate")
        placed = bus.attach(green_circuits(3.0, book))
        self.assertEqual(bus.lanes[placed.output_lane].item, "electronic-circuit")
        self.assertEqual(bus.lanes[placed.output_lane].source, "green-circuits")
        entities = drop_overlaps(bus.entities())
        names = [e.name for e in entities]
        self.assertEqual(names.count("splitter"), 2)
        undergrounds = [e for e in entities if e.name == "underground-belt"]
        self.assertEqual(len(undergrounds) % 2, 0)
        self.assertTrue(all(e.x >= placed.x for e in entities if e.recipe))
        # nothing shares a tile (splitters cover two)
        occupied = {}
        for e in entities:
            spots = [(math.floor(e.x - 0.5), math.floor(e.y)), (math.floor(e.x + 0.5), math.floor(e.y))] \
                if e.name == "splitter" else [(x, y) for x in range(math.floor(e.x - SIZE.get(e.name, 1) / 2 + 0.5),
                                                                    math.floor(e.x + SIZE.get(e.name, 1) / 2 + 0.5))
                                              for y in range(math.floor(e.y - SIZE.get(e.name, 1) / 2 + 0.5),
                                                             math.floor(e.y + SIZE.get(e.name, 1) / 2 + 0.5))]
            for spot in spots:
                self.assertNotIn(spot, occupied, (e, occupied.get(spot)))
                occupied[spot] = e

    def test_the_bus_ends_in_limited_chests_for_plates_and_intermediates(self):
        from companion.bus import Bus, END_CHEST_SLOTS
        from companion.modules import NORTH, red_science
        book = Book.vanilla()
        bus = Bus(0, 0, columns=5, length=30)
        bus.add_lane("iron-plate")
        bus.add_lane("copper-plate")
        bus.attach(gears(0.5, book))
        bus.attach(red_science(0.25, book))
        last = bus.segments()[-1][1]
        chests = {e.x - 0.5: e for e in last if e.name == "iron-chest"}
        inserters = {e.x - 0.5: e for e in last if e.name == "inserter" and e.y == bus.bottom + 0.5}
        for column, lane in bus.lanes.items():
            x = bus.lane_x(column)
            if lane.item.endswith("science-pack"):
                self.assertNotIn(x, chests)   # science goes to the labs, never into a chest
            else:
                self.assertEqual(chests[x].bar, END_CHEST_SLOTS)
                self.assertEqual(chests[x].y, bus.bottom + 1.5)
                self.assertEqual(inserters[x].direction, NORTH)
        self.assertEqual(len(chests), 3)   # iron, copper, gears
        self.assertEqual(chests[0].to_plan("c")["bar"], END_CHEST_SLOTS)

    def test_a_missing_input_lane_is_refused(self):
        from companion.bus import Bus
        bus = Bus(0, 0, columns=3, length=20)
        bus.add_lane("iron-plate")
        with self.assertRaises(ValueError):
            bus.attach(red_science(0.5, Book.vanilla()))  # needs copper too



    def test_every_pole_is_wired_to_the_bus_line(self):
        from companion.base_plan import modules_for
        from companion.bus import Bus
        book = Book.vanilla()
        built = modules_for(("automation-science-pack", "logistic-science-pack"), 15, book)
        bus = Bus(0, 0, columns=8, length=sum(m.height + 2 for m in built) + 8)
        bus.add_lane("iron-plate")
        bus.add_lane("copper-plate")
        for m in built:
            bus.attach(m)
        poles = [(e.x, e.y) for e in bus.entities() if e.name == "small-electric-pole"]
        reached, frontier = {poles[0]}, [poles[0]]
        while frontier:
            p = frontier.pop()
            for q in poles:
                if q not in reached and math.dist(p, q) <= 7.5:
                    reached.add(q)
                    frontier.append(q)
        self.assertEqual(set(poles) - reached, set())

class BusGroupTests(unittest.TestCase):
    """Plates in groups of belts side by side, balanced at the head and again after heavy tapping."""

    def grouped_bus(self):
        from companion.base_plan import modules_for
        from companion.bus import Bus
        book = Book.vanilla()
        built = modules_for(("automation-science-pack", "logistic-science-pack"), 60, book)
        bus = Bus(0, 0, columns=12, length=sum(m.height + 4 for m in built) + 20)
        bus.add_lane("iron-plate", belts=4, supply=15.0)
        bus.add_lane("copper-plate", belts=2, supply=15.0)
        for i, m in enumerate(built):
            bus.attach(m, side="west" if i % 2 else "east")
        return bus

    def test_group_belts_sit_side_by_side_with_a_gap_after(self):
        bus = self.grouped_bus()
        self.assertEqual([bus.lane_x(c) for c in range(7)], [0, 1, 2, 3, 6, 7, 10])
        self.assertEqual(bus.outer("iron-plate", "east"), 3)
        self.assertEqual(bus.outer("iron-plate", "west"), 0)

    def test_nothing_overlaps_and_undergrounds_reach_across_a_group(self):
        from companion.bus import drop_overlaps
        bus = self.grouped_bus()
        entities = drop_overlaps(bus.entities())
        occupied = {}
        for e in entities:
            spots = [(math.floor(e.x - 0.5), math.floor(e.y)), (math.floor(e.x + 0.5), math.floor(e.y))]                 if e.name == "splitter" else [(x, y) for x in range(math.floor(e.x - SIZE.get(e.name, 1) / 2 + 0.5),
                                                                    math.floor(e.x + SIZE.get(e.name, 1) / 2 + 0.5))
                                              for y in range(math.floor(e.y - SIZE.get(e.name, 1) / 2 + 0.5),
                                                             math.floor(e.y + SIZE.get(e.name, 1) / 2 + 0.5))]
            for spot in spots:
                self.assertNotIn(spot, occupied, (e, occupied.get(spot)))
                occupied[spot] = e
        rows = {}
        for e in entities:
            if e.name == "underground-belt":
                rows.setdefault((e.y, e.direction), []).append(e)
        for (_, direction), found in rows.items():
            found.sort(key=lambda e: e.x, reverse=direction == 12)
            pending = None
            for e in found:
                if e.kind == "input":
                    pending = e
                else:
                    self.assertIsNotNone(pending)
                    self.assertLessEqual(abs(e.x - pending.x), 5)    # a yellow underground spans five tiles
                    pending = None

    def test_taps_take_the_outer_belt_and_a_balancer_refills_it(self):
        bus = self.grouped_bus()
        heads = [b for b in bus.balancers if b[2] == bus.y]
        self.assertEqual(sorted((c, n) for c, n, _ in heads), [(0, 4), (4, 2)])   # spread at the head
        later = [b for b in bus.balancers if b[2] > bus.y]
        self.assertTrue(later)                                                  # taken a lot: rebalanced
        splitters = {(e.x, e.y) for e in bus.entities() if e.name == "splitter"}
        for column, belts, row in bus.balancers:
            self.assertIn((bus.lane_x(column) + 1.0, row + 0.5), splitters)
        taken, supplied = bus.flow()["iron-plate"][1], bus.flow()["iron-plate"][0]
        self.assertGreater(taken, 0)
        self.assertEqual(supplied, 15.0)
        for placed in bus.attached:
            for item, row in placed.taps:
                if item == "iron-plate":
                    column = 3 if placed.side == "east" else 0
                    x = bus.lane_x(column) + (1.0 if placed.side == "east" else 0.0)
                    self.assertIn((x, row - 0.5), splitters)

    def test_poles_stand_in_gaps_power_the_end_and_are_wired(self):
        bus = self.grouped_bus()
        entities = bus.entities()
        poles = [(e.x, e.y) for e in entities if e.name == "small-electric-pole"]
        lanes = {bus.lane_x(c) for c in bus.lanes}
        self.assertFalse([p for p in poles if bus.x <= p[0] - 0.5 < bus.east and p[0] - 0.5 in lanes])
        reached, frontier = {poles[0]}, [poles[0]]
        while frontier:
            p = frontier.pop()
            for q in poles:
                if q not in reached and math.dist(p, q) <= 7.5:
                    reached.add(q)
                    frontier.append(q)
        self.assertEqual(set(poles) - reached, set())
        end = [e for e in entities if e.name == "inserter" and e.y == bus.bottom + 0.5]
        for ins in end:
            self.assertTrue(any(abs(p[0] - ins.x) <= 2.5 and abs(p[1] - ins.y) <= 2.5 for p in poles), ins)


class OutletTests(unittest.TestCase):
    def test_the_tile_an_open_belt_end_feeds_is_kept_from_later_routes(self):
        # (run 28, 2026-10-25: a column's feed tail spilled spare coal onto the copper ore route laid across its
        # end, which carried it round into the head; the ore lane clogged and the column never smelted)
        from companion.modules import EAST, SOUTH, Placed
        from companion.smelting import outlets, route
        feed = [Placed("transport-belt", 0.5, y + 0.5, SOUTH) for y in range(0, 4)]   # (ends at row 3: spills on row 4)
        self.assertEqual(outlets(feed), {(0, 4)})
        hop = [Placed("underground-belt", 0.5, 0.5, SOUTH, kind="input"), Placed("underground-belt", 0.5, 3.5, SOUTH, kind="output")]
        self.assertEqual(outlets(hop), {(0, 4)})                       # (the entrance feeds its exit)
        joined = feed + [Placed("transport-belt", 0.5, 4.5, EAST), Placed("transport-belt", 1.5, 4.5, EAST)]
        self.assertEqual(outlets(joined), {(2, 4)})                    # (a belt it feeds is no outlet)
        # a route from west to east across the spill row goes round the outlet
        path = route((-3, 4), (3, 4), set().union(*[{(0, y)} for y in range(0, 4)]) | outlets(feed), bounds=(-10, -10, 10, 10))
        self.assertNotIn((0, 4), {(int(p.x), int(p.y)) for p in path})


class SmeltingBlockTests(unittest.TestCase):
    """Full-belt smelting blocks: two columns sharing a plate belt, one coal belt split between them."""

    def test_a_bus_that_fits_nowhere_says_so_once_rather_than_trying_without_end(self):
        # (gears made on site, then on the bus, then on site again...: its planner held past four minutes
        # and then hit the recursion limit, one-engineer run 2026-10-25)
        import time
        from companion.base_plan import plan_bus_base
        from companion.style import Style
        iron = [[y, -20, -10] for y in range(-20, -10)]
        dump = {"area": [-24, -24, 24, 24], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": [[y, 10, 20] for y in range(-20, -10)],
                              "coal": [[y, -5, 5] for y in range(10, 20)]}, "water": [], "entities": []}
        began = time.monotonic()
        with self.assertRaises(ValueError):
            plan_bus_base(dump, Book.vanilla(), science_per_minute=15, style=Style(gears_on_site=True),
                          packs=("automation-science-pack", "logistic-science-pack"))
        self.assertLess(time.monotonic() - began, 60)

    def test_a_full_block_shares_its_plate_belt_and_its_coal(self):
        from companion.base_plan import BLOCK_FURNACES, plan_bus_base
        from companion.modules import EAST, SOUTH, WEST
        iron = [[y, -70, -30] for y in range(-80, -40)]
        copper = [[y, 30, 70] for y in range(-80, -40)]
        coal = [[y, -40, 40] for y in range(-118, -96)]
        dump = {"area": [-120, -120, 120, 160], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": copper, "coal": coal}, "water": [], "entities": []}
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"))
        self.assertTrue(layout.ahead)
        everything = layout.entities()
        belts = {(math.floor(e.x), math.floor(e.y)): e.direction for e in everything if e.name == "transport-belt"}
        splitters = {(e.x, e.y) for e in everything if e.name == "splitter"}
        for plate in ("iron-plate", "copper-plate"):
            mine = [e for s in layout.stages if s.name.startswith(f"smelting-{plate}") for e in s.entities]
            furnaces = [e for e in mine if e.name == "stone-furnace"]
            if any(n.startswith(f"the second {plate}") for n in layout.notes):
                continue
            self.assertEqual(len(furnaces), BLOCK_FURNACES)
            xs = sorted({f.x for f in furnaces})
            self.assertEqual(len(xs), 2)                          # two columns...
            self.assertEqual(xs[1] - xs[0], 5)                    # ...either side of one plate belt
            cx, cy = round(xs[0] - 3), round(min(f.y for f in furnaces) - 1)
            self.assertEqual(belts.get((cx + 5, cy)), SOUTH)      # the shared plate belt
            self.assertIn((cx + 6.0, cy - 2.5), splitters)        # one coal belt, split above the middle
            self.assertEqual(belts.get((cx + 1, cy - 1)), WEST)   # onto the west head's inner lane
            self.assertEqual(belts.get((cx + 9, cy - 1)), EAST)   # and the east head's
            self.assertEqual(belts.get((cx + 5, cy - 4)), SOUTH)  # the coal comes down into the splitter
            # one ore belt too, split at the head (the distributor): onto each head's outer lane
            self.assertIn((cx + 0.0, cy - 3.5), splitters)
            self.assertEqual(belts.get((cx - 1, cy - 1)), EAST)
            self.assertEqual(belts.get((cx + 11, cy - 1)), WEST)
            self.assertEqual(belts.get((cx - 1, cy - 5)), SOUTH)

    def test_no_drill_mines_two_ores_where_patches_touch(self):
        from companion.smelting import foreign_tiles, ore_tiles, plan_field
        resources = {"iron-ore": [[y, 0, 30] for y in range(0, 20)],
                     "copper-ore": [[y, 0, 30] for y in range(20, 40)],
                     "crude-oil": [[10, 5, 5]]}
        iron = ore_tiles(resources, "iron-ore")
        copper = ore_tiles(resources, "copper-ore")
        self.assertEqual(foreign_tiles(resources, "iron-ore"), copper)    # oil is no drill's business
        fld = plan_field(iron, set(), 8, "iron-ore", near=(15, 25), foreign=copper)
        self.assertIsNotNone(fld)
        for cx, cy, _ in fld.drills:
            area = {(math.floor(cx) + dx, math.floor(cy) + dy) for dx in range(-2, 3) for dy in range(-2, 3)}
            self.assertFalse(area & copper, (cx, cy))

    def test_a_real_map_bus_puts_one_ore_on_each_belt(self):
        import json
        from companion.base_plan import plan_bus_base
        from companion.smelting import ore_tiles
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        res = world["resources"]
        tiles = {n: ore_tiles(res, n) for n in res if n != "crude-oil"}
        layout = plan_bus_base(world, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=12)
        for e in layout.entities():
            if e.name == "electric-mining-drill":
                area = {(math.floor(e.x) + dx, math.floor(e.y) + dy) for dx in range(-2, 3) for dy in range(-2, 3)}
                self.assertLessEqual(sum(1 for t in tiles.values() if area & t), 1, e)   # (iron and copper touch here)

    def test_a_lane_no_machine_can_feed_is_fed_by_hand_from_a_chest(self):
        import json
        from companion.base_plan import plan_bus_base
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        world["entities"] = [e for e in world["entities"] if e.get("r") != "sulfur"]   # nothing makes sulfur
        layout = plan_bus_base(world, Book.vanilla(), science_per_minute=15, columns=19,
                               packs=("automation-science-pack", "logistic-science-pack"),
                               extra=("chemical-science-pack",))
        lane = next(s for s in layout.stages if s.name == "lane-sulfur")      # never a lane nothing feeds
        chest = next(e for e in lane.entities if e.name == "iron-chest")
        inserter = next(e for e in lane.entities if e.name == "inserter")
        self.assertEqual(lane.supplies, [{"x": chest.x, "y": chest.y, "item": "sulfur", "slot": "input"}])
        self.assertTrue(any(n.startswith("sulfur is carried to the bus by hand") for n in layout.notes))
        # the inserter sits between the chest and the lane head, picking from the chest
        column = layout.bus.lane_of("sulfur")
        lx, head = layout.bus.lane_x(column) + 0.5, layout.bus.lanes[column].start + 0.5
        self.assertEqual(abs(inserter.x - chest.x) + abs(inserter.y - chest.y), 1)
        self.assertEqual(abs(inserter.x - lx) + abs(inserter.y - head), 1)

    def test_a_field_planned_once_is_extended_where_it_stands(self):
        import json
        from companion.base_plan import plan_bus_base
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        packs = ("automation-science-pack", "logistic-science-pack")
        first = plan_bus_base(world, Book.vanilla(), science_per_minute=15, packs=packs, columns=12)
        self.assertIn("field-iron-plate", first.sites)
        # the bus as built so far stands on the map; the plan is made again with what it remembers
        built = [e for s in first.stages if s.name in ("power", "smelting-iron-plate", "smelting-copper-plate")
                 for e in s.entities]
        sizes = {"electric-mining-drill": 3, "stone-furnace": 2, "splitter": 1}
        later = dict(world, entities=world["entities"] + [
            {"n": e.name, "t": e.name, "x": e.x, "y": e.y, "w": sizes.get(e.name, 1), "h": sizes.get(e.name, 1),
             "d": e.direction, "f": world["force"]} for e in built])
        again = plan_bus_base(later, Book.vanilla(), science_per_minute=15, packs=packs, columns=12,
                              origin=(first.bus.x, first.bus.y), ahead=first.ahead, sites=first.sites)
        def drills(layout, name):
            return sorted((e.x, e.y) for s in layout.stages if s.name == name for e in s.entities
                          if e.name == "electric-mining-drill")
        for plate in ("iron-plate", "copper-plate"):
            self.assertEqual(again.sites[f"field-{plate}"], first.sites[f"field-{plate}"])
            self.assertEqual(drills(again, f"smelting-{plate}-more"), drills(first, f"smelting-{plate}-more"))

    def test_a_built_ore_route_and_coal_line_are_kept_not_searched_for_again(self):
        import json
        from companion.base_plan import plan_bus_base
        from companion.smelting import footprint
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        packs = ("automation-science-pack", "logistic-science-pack")
        first = plan_bus_base(world, Book.vanilla(), science_per_minute=15, packs=packs, columns=12)
        built = [e for s in first.stages if s.name in ("power", "smelting-iron-plate", "smelting-copper-plate")
                 for e in s.entities]
        sizes = {"electric-mining-drill": 3, "stone-furnace": 2, "splitter": 1}
        # a crowded map: walls hard round everything built, so its lines could not be found again
        taken = footprint(built)
        ring = {(x + dx, y + dy) for x, y in taken for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - taken
        later = dict(world, entities=world["entities"] + [
            {"n": e.name, "t": e.name, "x": e.x, "y": e.y, "w": sizes.get(e.name, 1), "h": sizes.get(e.name, 1),
             "d": e.direction, "f": world["force"]} for e in built] + [
            {"n": "stone-wall", "t": "wall", "x": x + 0.5, "y": y + 0.5, "f": world["force"]} for x, y in ring])
        again = plan_bus_base(later, Book.vanilla(), science_per_minute=15, packs=packs, columns=12,
                              origin=(first.bus.x, first.bus.y), ahead=first.ahead, sites=first.sites)
        self.assertFalse([n for n in again.notes if "belt route from" in n or "fed by hand: no coal" in n])
        for plate in ("iron-plate", "copper-plate"):     # no coal carried by hand to furnaces a belt feeds
            self.assertEqual(next(s for s in again.stages if s.name == f"smelting-{plate}-more").supplies, [])

    def test_nothing_is_planned_where_a_belt_inserter_or_drill_puts_things_down(self):
        from companion.base_plan import blocked_tiles
        dump = {"water": [], "entities": [
            {"n": "transport-belt", "t": "transport-belt", "x": 0.5, "y": 0.5, "d": 4},       # ends pointing east
            {"n": "inserter", "t": "inserter", "x": 10.5, "y": 0.5, "d": 12},                 # picks west, drops east
            {"n": "electric-mining-drill", "t": "mining-drill", "x": 21.5, "y": 1.5, "w": 3, "h": 3, "d": 8}]}
        tiles = blocked_tiles(dump)
        self.assertIn((1, 0), tiles)      # in front of the belt end
        self.assertIn((11, 0), tiles)     # the inserter's drop tile
        self.assertIn((21, 3), tiles)     # the drill's drop tile, south of it
        self.assertNotIn((9, 0), tiles)   # not where the inserter picks up

    def test_a_route_that_cannot_exist_gives_up_quickly(self):
        import time
        from companion.smelting import route
        wall = {(x, y) for x in range(-12, 13) for y in range(-12, 13) if max(abs(x), abs(y)) in (8, 9, 10, 11, 12)}
        start = time.time()
        self.assertIsNone(route((-200, -200), (0, 0), wall, bounds=(-256, -256, 256, 256)))   # walled in
        self.assertLess(time.time() - start, 10)

    def test_an_entry_only_an_underground_could_reach_sideways_gives_up_at_once(self):
        # (run 33's map: a coal entry whose one free neighbour is reached only out of an underground heading across the
        # entry's way in; four coal lines to it searched 300,000 states each, 2026-10-26)
        import time
        from companion.smelting import route
        # the goal (0, 0) is open to the south only, and (0, 1) only to hops along its row (an exit goes straight on)
        blocked = {(-1, 0), (1, 0), (0, -1)} | {(x, 1) for x in range(-4, 5) if x} | {(0, y) for y in range(2, 7)}
        start = time.time()
        self.assertIsNone(route((-150, -150), (0, 0), blocked, bounds=(-200, -200, 200, 200)))
        self.assertLess(time.time() - start, 1)
        # a way in along the column is enough
        opened = blocked - {(0, 6), (0, 5)}
        self.assertIsNotNone(route((-150, -150), (0, 0), opened, bounds=(-200, -200, 200, 200)))

    def test_the_opening_does_not_rebuild_what_the_bus_replaced(self):
        from companion.catalog_planner import PURPOSES, opening_goal
        recipes = [{"name": n} for p in PURPOSES.values() for n in p.get("requires_recipes", [])]
        site = {"completed_purposes": ["iron-smelting-start", "coal-loop", "stone-supply", "copper-smelting-start",
                                       "steam-power", "research-lab", "red-science-automation", "boiler-coal",
                                       "green-science-automation", "electric-iron-mining", "electric-copper-mining",
                                       "bus-base"],
                "base": [{"purpose": "coal-loop", "machines": {"burner-mining-drill": 4}}], "recipes": recipes,
                "resources": [{"resource": "iron-ore", "rows": []}, {"resource": "coal", "rows": []}],
                "base_plan": {"stages": ["power", "smelting-iron-plate"], "done": {"power": 1}}}
        # the old iron furnaces were retired: no burner pairs, nor electric smelting beside the bus's own; its
        # burner drills on coal go electric
        self.assertEqual(opening_goal(site), "electric-coal-supply")
        electric = {**site, "completed_purposes": site["completed_purposes"] + ["electric-coal-supply",
                                                                               "electric-stone-supply"]}
        self.assertIsNone(opening_goal(electric))
        # before a bus: electric smelting, never burner pairs, once there is power (and its income buys the array)
        with mock.patch("companion.plate_lead.limiting", return_value=None):
            self.assertEqual(opening_goal({**electric, "base_plan": {}}), "electric-iron-smelting")


class PersonalityOpeningTests(unittest.TestCase):
    def test_aggressive_engineers_research_weapons_first_and_turtles_defenses(self):
        from companion import blueprints as bp
        from companion.catalog_planner import CatalogPlanner
        import tempfile
        options = [{"name": "automation", "early": True}, {"name": "military"}, {"name": "gun-turret"},
                   {"name": "logistics"}]
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            def pick(traits):
                return planner.choose_research({"request_id": "1:r1", "options": options,
                                                "personality": {"traits": traits}})
            self.assertEqual(pick(["aggressive", "brave"]), "military")
            self.assertEqual(pick(["fortifier"]), "gun-turret")
            self.assertEqual(pick(["curious"]), "automation")          # everyone else: the opening's order
            at_war = planner.choose_research({"request_id": "1:r2", "options": options, "war": True,
                                              "personality": {"traits": ["curious"]}})
            self.assertEqual(at_war, "military")                         # at war: weapons first, anyone
            walled = planner.choose_research({"request_id": "1:r3", "war": True, "walled": True,
                                              "options": options + [{"name": "automobilism"}],
                                              "personality": {"traits": ["curious"]}})
            self.assertEqual(walled, "automobilism")                     # walled off by biters: a vehicle

    def test_turtles_build_defenses_right_after_their_first_power(self):
        from companion.catalog_planner import opening_order
        plain = [p for p, _ in opening_order({})]
        turtle = [p for p, _ in opening_order({"personality": {"traits": ["cautious"]}})]
        self.assertNotIn("basic-defense", plain)
        self.assertEqual(turtle[turtle.index("steam-power") + 1], "basic-defense")


class BasePlanTests(unittest.TestCase):
    def test_plans_fields_columns_and_a_bus_on_a_small_map(self):
        from companion.base_plan import plan_bus_base
        from companion.smelting import footprint
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        dump = {"area": [-60, -60, 60, 60], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15)
        # no coal on this map: the furnaces are fuelled by hand; no water: no power plant either
        notes = [n for n in layout.notes if not n.startswith(("no shore", "the second")) and "its field feeds" not in n]
        self.assertEqual(len(notes), 2)
        self.assertTrue(all(n.startswith("coal for the ") for n in notes))
        names = [s.name for s in layout.stages]
        # red now; smelting is laid out for green too, its extra furnaces built last
        self.assertEqual(names, ["smelting-iron-plate", "smelting-copper-plate", "bus-red-science", "bus-labs",
                                 "smelting-iron-plate-more", "smelting-copper-plate-more"])
        self.assertTrue(all(len(s.entities) <= 900 for s in layout.stages))
        self.assertEqual({lane.item for lane in layout.bus.lanes.values()},
                         {"iron-plate", "copper-plate", "automation-science-pack"})
        drills = [e for e in layout.entities() if e.name == "electric-mining-drill"]
        self.assertGreaterEqual(len(drills), 4)
        furnaces = [s for st in layout.stages for s in st.supplies]
        self.assertTrue(furnaces and all(s["item"] == "coal" for s in furnaces))
        tiles = [footprint([e]) for e in layout.entities()]
        seen = set()
        for t in tiles:
            self.assertFalse(t & seen)
            seen |= t

    def test_red_and_green_science_share_gear_and_circuit_lanes(self):
        from companion.base_plan import plan_bus_base
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        dump = {"area": [-70, -70, 70, 90], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"))
        lanes = [lane.item for _, lane in sorted(layout.bus.lanes.items())]
        self.assertEqual(lanes[:6], ["iron-plate", "iron-plate", "copper-plate", "copper-plate", "iron-gear-wheel",
                                     "electronic-circuit"])   # plates two belts each, side by side
        self.assertEqual([s.name for s in layout.stages][2:],
                         ["bus-gears", "bus-green-circuits", "bus-red-science", "bus-green-science",
                          "bus-mall-logistics", "bus-mall-machines", "bus-labs",
                          "smelting-iron-plate-more", "smelting-copper-plate-more"])
        self.assertTrue(all(len(s.entities) <= 900 for s in layout.stages))
        # A new bus: science first, its mall after the labs, the same two parts (mall.OWN).
        from companion import mall
        own = mall.extension_goal(mall.OWN, mall.recipes_for(None))
        first = plan_bus_base(dump, Book.vanilla(), science_per_minute=15,
                              packs=("automation-science-pack", "logistic-science-pack"), extra=(own,))
        self.assertEqual([s.name for s in first.stages][2:],
                         ["bus-gears", "bus-green-circuits", "bus-red-science", "bus-green-science", "bus-labs",
                          "bus-mall-logistics", "bus-mall-machines",
                          "smelting-iron-plate-more", "smelting-copper-plate-more"])

        def recipes(lay, name):
            return sorted(e.recipe for a in lay.bus.attached if a.module.name == name for e in a.module.entities
                          if e.recipe)
        for name in ("mall-logistics", "mall-machines"):
            self.assertEqual(recipes(first, name), recipes(layout, name))

    def test_a_bus_starting_with_blue_has_its_mall_after_blues_labs_and_keeps_it_there(self):
        from companion import mall
        from companion.base_plan import plan_bus_base
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        dump = {"area": [-150, -100, 150, 300], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        own = mall.extension_goal(mall.OWN, mall.recipes_for(None))
        spots, origin = [], None
        # (planned with blue from the start; then extended for military science, laid out again where it stands)
        for extra in (("chemical-science-pack", own), ("chemical-science-pack", own, "military-science-pack")):
            lay = plan_bus_base(dump, Book.vanilla(), science_per_minute=15, base_labs=False, columns=24,
                                packs=("automation-science-pack", "logistic-science-pack"), extra=extra, origin=origin)
            origin = (lay.bus.x, lay.bus.y)
            names = [s.name for s in lay.stages]
            self.assertLess(names.index("bus-labs-blue"), names.index("bus-mall-logistics"))
            self.assertLess(names.index("bus-labs-blue"), names.index("bus-mall-machines"))
            spots.append({(a.module.name, a.x, a.y, a.side) for a in lay.bus.attached if a.module.name.startswith("mall")})
        self.assertEqual(len(spots[0]), 2)
        self.assertEqual(spots[0], spots[1])   # (the section added later goes below it)

    def test_every_drill_in_a_field_has_power(self):
        from companion.power import groups
        from companion.smelting import plan_field
        ore = {(x, y) for x in range(0, 30) for y in range(0, 60)}
        for drills in range(1, 9):
            fld = plan_field(ore, set(), drills, "iron-ore")
            entities = fld.entities()
            poles = [(e.x, e.y) for e in entities if e.name == "small-electric-pole"]
            placed = [e for e in entities if e.name == "electric-mining-drill"]
            self.assertEqual(len(placed), drills)
            for d in placed:   # a 3x3 drill touches a pole's 5x5 supply area
                self.assertTrue(any(abs(d.x - px) < 4 and abs(d.y - py) < 4 for px, py in poles), (drills, d))
            self.assertEqual(len(groups(poles)), 1, drills)

    def test_coal_rides_the_ore_belt_when_a_coal_field_fits(self):
        from companion.base_plan import plan_bus_base
        from companion.modules import EAST, SOUTH, WEST
        from companion.smelting import footprint
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        coal = [[y, -8, 8] for y in range(-58, -46)]
        dump = {"area": [-60, -60, 60, 60], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": copper, "coal": coal}, "water": [], "entities": []}
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15)
        self.assertEqual([n for n in layout.notes if not n.startswith(("no shore", "the second"))
                          and "its field feeds" not in n], [])   # no water here (a field's reach: said, F-21)
        smelting = [s for s in layout.stages if s.name.startswith("smelting-") and not s.name.endswith("-more")]
        self.assertEqual(len(smelting), 2)
        coal_tiles = {(x, y) for y, a, b in coal for x in range(a, b + 1)}
        for stage in smelting:
            self.assertEqual(stage.supplies, [])   # no hand fuel
            belts = {(math.floor(e.x), math.floor(e.y)): e.direction
                     for e in stage.entities if e.name == "transport-belt"}
            furnace = min((e for e in stage.entities if e.name == "stone-furnace"), key=lambda e: e.y)
            head = (round(furnace.x - 3), round(furnace.y - 2))   # ore belt column, one row above the top furnace
            self.assertEqual(belts.get(head), SOUTH)
            self.assertEqual(belts.get((head[0] - 1, head[1])), EAST)    # ore from the west
            self.assertEqual(belts.get((head[0] + 1, head[1])), WEST)    # coal from the east
            self.assertNotEqual(belts.get((head[0], head[1] - 1)), SOUTH)   # nothing feeds it from behind: a sideload head
            drills = [e for e in stage.entities if e.name == "electric-mining-drill"]
            self.assertTrue(any(footprint([d]) & coal_tiles for d in drills))
        seen = set()
        for e in layout.entities():
            tiles = footprint([e])
            self.assertFalse(tiles & seen, e)
            seen |= tiles

    def test_smelting_is_laid_out_for_the_next_tier_and_finished_later(self):
        from companion.base_plan import plan_bus_base
        from companion.ratios import solve
        from companion.smelting import furnaces_for
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        dump = {"area": [-90, -90, 90, 120], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        packs = ("automation-science-pack", "logistic-science-pack")
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15, packs=packs)
        self.assertTrue(layout.ahead)
        blue = solve({p: 0.25 for p in (*packs, "chemical-science-pack")}, Book.vanilla(),
                     inputs={"iron-plate", "copper-plate"},
                     allowed={"assembling-machine-2", "chemical-plant", "oil-refinery", "stone-furnace"})
        for plate in ("iron-plate", "copper-plate"):
            now = sum(e.name == "stone-furnace" for s in layout.stages if s.name == f"smelting-{plate}" for e in s.entities)
            more = sum(e.name == "stone-furnace" for s in layout.stages if s.name == f"smelting-{plate}-more"
                       for e in s.entities)
            self.assertGreater(more, 0)
            today = solve({p: 0.25 for p in packs}, Book.vanilla(), inputs={"iron-plate", "copper-plate"},
                          allowed={"assembling-machine-2"})
            self.assertEqual(now, furnaces_for(today.inputs[plate] * 1.1))   # today's furnaces first
            # (a full-belt block: two columns of 24 sharing the plate belt; fewer only where its field feeds fewer,
            # said: furnaces with no ore are not planned, F-21)
            if not any(n.startswith(f"the second {plate}") for n in layout.notes):
                self.assertLessEqual(now + more, 48)
                self.assertGreater(more, 0)
            self.assertGreaterEqual(now + more, furnaces_for(blue.inputs[plate] * 1.1))

    def test_a_crowded_map_plans_smelting_for_today_only(self):
        import json
        from companion.base_plan import plan_bus_base
        dump = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=10)
        self.assertFalse(layout.ahead)
        self.assertFalse(any(s.name.endswith("-more") for s in layout.stages))


class BlueModuleTests(unittest.TestCase):
    def test_blue_chain_modules_are_well_formed(self):
        from companion import blue_modules as bm
        from companion.smelting import footprint
        book = Book.vanilla()
        cases = [(bm.red_circuits, 0.375, {"plastic-bar", "copper-plate", "electronic-circuit"}, "advanced-circuit"),
                 (bm.engines, 0.25, {"steel-plate", "iron-gear-wheel", "iron-plate"}, "engine-unit"),
                 (bm.blue_science, 0.25, {"engine-unit", "advanced-circuit", "sulfur"}, "chemical-science-pack"),
                 (bm.steel, 0.25, {"iron-plate", "coal"}, "steel-plate")]
        for build, rate, inputs, product in cases:
            m = build(rate, book)
            self.assertEqual({p.item for p in m.ports if p.kind == "in"}, inputs, m.name)
            self.assertEqual([p.item for p in m.ports if p.kind == "out"], [product])
            rows = sorted(p.y for p in m.ports)
            self.assertTrue(all(b - a >= 2 for a, b in zip(rows, rows[1:])), m.name)   # bus taps need room
            seen = set()
            for e in m.entities:
                tiles = footprint([e])
                self.assertFalse(tiles & seen, (m.name, e))
                seen |= tiles
                self.assertTrue(all(0 <= x < m.width and 0 <= y < m.height for x, y in tiles), (m.name, e))
            poles = [(e.x, e.y) for e in m.entities if e.name == "small-electric-pole"]
            for e in m.entities:
                if "inserter" in e.name:
                    self.assertTrue(any(abs(e.x - px) <= 2.5 and abs(e.y - py) <= 2.5 for px, py in poles),
                                    (m.name, e))

    def test_red_circuits_are_fed_cable_by_their_neighbours(self):
        from companion import blue_modules as bm
        m = bm.red_circuits(0.375, Book.vanilla())
        machines = sorted((e for e in m.entities if e.recipe), key=lambda e: e.x)
        self.assertEqual([e.recipe for e in machines],
                         ["advanced-circuit", "copper-cable", "advanced-circuit", "advanced-circuit", "copper-cable"])
        gaps = [e for e in m.entities if e.name == "inserter" and e.y == 8.5]
        self.assertEqual(len(gaps), 3)   # one into each red circuit assembler


class BusExtensionTests(unittest.TestCase):
    """Ada's map at radius 256 (tests/fixtures/k-blue-map-256.json): she has researched blue science."""
    SITE = {"request_id": "1:30", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
            "researched": ["logistic-science-pack", "chemical-science-pack"],
            "completed_purposes": ["iron-smelting-start", "research-lab", "red-science-automation", "oil-processing",
                                   "blue-science-automation"]}

    @classmethod
    def setUpClass(cls):
        import json
        cls.world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))

    def test_blue_goes_in_from_the_start_with_one_set_of_labs(self):
        from companion import power_plant as pp
        from companion.base_plan import plan_bus_base
        from companion.bus_planner import plan_stage
        from companion.smelting import footprint
        first = plan_stage(self.SITE, self.world, Book.vanilla())
        saved = first["base_plan"]
        names = saved["stages"]
        self.assertEqual(saved["extra"], ["chemical-science-pack", mall_goal(saved)])
        self.assertNotIn("bus-labs", names)
        # science first: the bus's mall after the labs, which come with blue's section
        self.assertLess(names.index("bus-labs-blue"), names.index("bus-mall-logistics"))
        self.assertLess(names.index("bus-labs-blue"), names.index("bus-mall-machines"))
        for name in ("lane-coal", "lane-plastic-bar", "lane-sulfur", "lane-steel-plate", "bus-red-circuits",
                     "bus-engines", "bus-blue-science", "bus-labs-blue"):
            self.assertIn(name, names)
        self.assertNotIn("bus-steel", names)                     # steel by direct insertion, not from bus iron
        self.assertLess(names.index("lane-plastic-bar"), names.index("bus-red-circuits"))
        self.assertLess(names.index("lane-steel-plate"), names.index("bus-engines"))
        sites = dict(x.split("=", 1) for x in saved["sites"])
        self.assertEqual(sites["steel"], "direct")
        from companion.style import style_for
        layout = plan_bus_base(self.world, Book.vanilla(), science_per_minute=float(saved["spm"]),
                               packs=tuple(saved["packs"]), columns=int(saved["columns"]),
                               origin=(int(saved["x"]), int(saved["y"])), unlocked=saved["mall"], chosen={},
                               ahead=bool(saved["ahead"]), extra=tuple(saved["extra"]), base_labs=False,
                               style=style_for(self.SITE))
        self.assertEqual([s.name for s in layout.stages], names)   # the replay is the same plan
        self.assertEqual([n for n in layout.notes if not n.startswith("the second")], [])
        seen = set()
        for e in layout.entities():
            tiles = pp.tiles(e) if e.name in ("boiler", "steam-engine", "offshore-pump") else footprint([e])
            self.assertFalse(tiles & seen, e)
            seen |= tiles

    def test_a_finished_red_and_green_bus_is_extended_for_blue(self):
        from companion.bus_planner import extendable, plan_stage
        red_green = {**self.SITE, "researched": ["logistic-science-pack"]}
        first = plan_stage(red_green, self.world, Book.vanilla())
        saved = {**first["base_plan"], "done": {s: 1 for s in first["base_plan"]["stages"]}}
        self.assertIn("bus-labs", saved["stages"])
        # nothing new to research for: the finished bus grows instead (red and green science once more)
        self.assertEqual(extendable({**red_green, "base_plan": saved}), ("growth-1-2",))
        site = {**self.SITE, "base_plan": saved}
        self.assertEqual(extendable(site), ("chemical-science-pack",))
        nxt = plan_stage(site, self.world, Book.vanilla())
        self.assertEqual(nxt["base_plan"]["extra"], [mall_goal(saved), "chemical-science-pack"])
        self.assertTrue(nxt["base_plan"]["stage"].startswith(("lane-", "bus-")))
        self.assertNotIn(nxt["base_plan"]["stage"], saved["stages"])
        names = nxt["base_plan"]["stages"]
        # (the first labs make way for the new ones with its last labs section: its science runs then, F-18)
        last = {**saved, "stages": names, "done": {s: 1 for s in names if s != "bus-labs-blue"},
                "extra": nxt["base_plan"]["extra"]}
        final = plan_stage({**self.SITE, "base_plan": last}, self.world, Book.vanilla())
        self.assertEqual(final["base_plan"]["stage"], "bus-labs-blue")
        self.assertIn("stage:bus-labs", final["retire"])


class DirectSteelTests(unittest.TestCase):
    def test_iron_furnaces_hand_their_plates_straight_to_steel_furnaces(self):
        import math
        from companion.smelting import footprint, steel_column, steel_pairs
        self.assertEqual(steel_pairs(0.25), 4)                  # 1/16 steel/s a pair
        m = steel_column(4)
        self.assertEqual(len(footprint(m.entities)), sum(len(footprint([e])) for e in m.entities))   # no overlaps
        at = {}
        for e in m.entities:
            for t in footprint([e]):
                at[t] = e
        reach = {"inserter": 1, "long-handed-inserter": 2}
        step = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
        moves = []
        for e in m.entities:
            if e.name in reach:   # an inserter faces where it picks up
                dx, dy = step[e.direction]
                r, x, y = reach[e.name], math.floor(e.x), math.floor(e.y)
                moves.append((at[(x + dx * r, y + dy * r)], at[(x - dx * r, y - dy * r)]))
        kinds = sorted({(a.name if a.name != "stone-furnace" else f"furnace@{a.x}",
                         b.name if b.name != "stone-furnace" else f"furnace@{b.x}") for a, b in moves})
        self.assertEqual(kinds, [("furnace@3.0", "furnace@6.0"),       # iron plates straight across
                                 ("furnace@6.0", "transport-belt"),   # steel out
                                 ("transport-belt", "furnace@3.0"),   # ore and coal in
                                 ("transport-belt", "furnace@6.0")])  # coal over the steel belt
        coal_from = {math.floor(a.x) for a, b in moves if b.x == 6.0 and a.name == "transport-belt"}
        self.assertEqual(coal_from, {9})                       # from belt C, not the steel belt (x=8)

    def test_a_bus_without_room_for_a_steel_block_makes_steel_in_a_module(self):
        import json
        from unittest import mock
        import companion.base_plan as bp
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        with mock.patch.object(bp, "_steel_line", return_value=None):
            layout = bp.plan_bus_base(world, Book.vanilla(), science_per_minute=15,
                                      packs=("automation-science-pack", "logistic-science-pack"), columns=17,
                                      extra=("chemical-science-pack",), base_labs=False, ahead=True)
        names = [s.name for s in layout.stages]
        self.assertIn("bus-steel", names)
        self.assertNotIn("lane-steel-plate", names)
        self.assertEqual(layout.sites["steel"], "module")       # and it stays a module from now on

    def test_a_bus_planned_with_a_steel_module_keeps_it(self):
        from companion.bus_planner import _sites
        self.assertEqual(_sites({"stages": ["bus-steel"], "sites": []})["steel"], "module")
        self.assertNotIn("steel", _sites({"stages": ["lane-coal"], "sites": []}))

    def test_a_built_steel_block_is_planned_where_it_stands(self):
        import json
        from companion.base_plan import _steel_line
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        entry = (500, 500)   # the lane: a belt of its own stands at the entry
        built = dict(world, entities=world["entities"] + [{"n": "transport-belt", "t": "transport-belt", "x": 500.5,
                                                            "y": 500.5, "d": 8, "f": world["force"]}])
        line = _steel_line(built, set(), set(), entry, 0.25, {"steel-site": "-50,-48"})
        self.assertEqual(sum(e.name == "stone-furnace" for e in line), 8)
        self.assertEqual(min(e.x for e in line), -50 + 0.5)    # where it stands: no field, no routes again
        self.assertFalse(any(e.name == "electric-mining-drill" for e in line))


class StyleTests(unittest.TestCase):
    def test_each_engineer_has_its_own_lasting_style(self):
        from companion.style import style_for
        ada = {"npc_id": 1, "personality": {"seed": 42, "traits": ["methodical", "honest"]}}
        bram = {"npc_id": 2, "personality": {"seed": 7, "traits": ["bold", "curious"]}}
        cora = {"npc_id": 3, "personality": {"seed": 99, "traits": ["frugal", "cautious"]}}
        self.assertEqual(style_for(ada), style_for(dict(ada)))   # fixed for life
        styles = [style_for(s) for s in (ada, bram, cora)]
        self.assertEqual(len({(s.heading, s.spread) for s in styles}), 3)
        self.assertLessEqual(styles[0].spread, 0.5)              # tidy: keeps the base together
        self.assertGreaterEqual(styles[1].spread, 0.5)           # bold and curious: likes room
        self.assertEqual((styles[2].spare, styles[2].cheap_machines, styles[2].reserve), (1.25, True, 2))

    def test_different_styles_lay_different_bases_on_one_map(self):
        import json
        from companion.base_plan import plan_bus_base
        from companion.style import Style
        dump = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        heads = set()
        for style in (Style(), Style(heading=3.14, spread=1.0, first_side="west", spare=1.25)):
            layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15, packs=("automation-science-pack",),
                                   columns=8, style=style, ahead=False)
            heads.add((layout.bus.x, layout.bus.y))
        self.assertEqual(len(heads), 2)

    def test_gears_made_on_site_is_a_lasting_style_choice(self):
        from companion.style import Style
        on = Style(gears_on_site=True)
        self.assertTrue(Style.decode(on.code()).gears_on_site)
        self.assertFalse(Style.decode("0.0000,0.0000,east,1.100,0,0,0").gears_on_site)   # planned before it existed

    def test_with_gears_on_site_no_gears_ride_the_bus_for_science_or_engines(self):
        import json
        from companion.base_plan import plan_bus_base
        from companion.style import Style
        dump = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=17,
                               extra=("chemical-science-pack",), base_labs=False, ahead=True,
                               style=Style(gears_on_site=True))
        self.assertEqual(layout.sites["gears"], "site")
        names = [s.name for s in layout.stages]
        self.assertNotIn("bus-gears-2", names)                    # only engines took more gears
        takes = {a.module.name: {p.item for p in a.module.ports if p.kind == "in"} for a in layout.bus.attached}
        self.assertNotIn("iron-gear-wheel", takes["green-science"])
        self.assertNotIn("iron-gear-wheel", takes["engines"])
        self.assertIn("iron-gear-wheel", takes["mall-machines"])   # the mall still takes the bus's gears

    def test_where_gears_on_site_do_not_fit_they_ride_the_bus(self):
        from companion.bus_planner import plan_stage
        from companion.style import style_for
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        world = {"area": [-70, -70, 70, 90], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                 "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        site = {"request_id": "1:60", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
                "researched": ["logistic-science-pack"]}
        self.assertTrue(style_for(site).gears_on_site)              # this engineer would make them on site
        with mock.patch.object(bus_planner, "FIRST_SHARE", 1.0),                 mock.patch.object(bus_planner, "PLACES", 1):   # (a first bus at the full rate where it first fits: no room)
            saved = plan_stage(site, world, Book.vanilla())["base_plan"]
        self.assertIn("gears=bus", saved["sites"])                  # kept, so its later stages agree


class PowerPlantTests(unittest.TestCase):
    def test_a_pole_chain_never_stands_on_a_power_plants_buildings(self):
        # (run 59, 2026-10-26: footprint took a steam engine for one tile; a coal field's pole chain ended on it, its
        # last pole gave way to the engine in the game, and the field's drills stood 8.5 tiles off the network)
        from companion import power_plant as pp
        from companion.modules import EAST, NORTH, Placed
        from companion.power import connect
        from companion.smelting import footprint
        for e in (Placed("steam-engine", 0.5, 0.0, NORTH), Placed("steam-engine", 0.0, 0.5, EAST),
                  Placed("boiler", 0.5, 1.0, NORTH), Placed("offshore-pump", 1.0, 0.5, EAST)):
            self.assertEqual(footprint([e]), pp.tiles(e), e)
        engine = Placed("steam-engine", 0.5, 0.0, NORTH)
        far = [Placed("small-electric-pole", 0.5, 40.5)]
        extra, problems = connect([engine] + far, [(0.5, -5.5)], footprint([engine] + far))
        self.assertEqual(problems, [])
        self.assertFalse({(math.floor(e.x), math.floor(e.y)) for e in extra} & pp.tiles(engine))

    def test_a_bus_on_a_real_map_gets_a_power_plant_by_the_lake(self):
        import json
        from companion import power_plant as pp
        from companion.base_plan import plan_bus_base
        from companion.smelting import footprint
        dump = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        layout = plan_bus_base(dump, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=10)
        self.assertEqual(layout.stages[0].name, "power")
        power = layout.stages[0]
        boilers = [e for e in power.entities if e.name == "boiler"]
        engines = [e for e in power.entities if e.name == "steam-engine"]
        self.assertEqual(len(engines), 2 * len(boilers))
        self.assertGreaterEqual(len(boilers) * pp.BOILER_KW + pp.supply_kw(dump),
                                pp.draw_kw(e.name for s in layout.stages for e in s.entities))
        self.assertEqual(len(power.supplies), len(boilers))          # hand fuel as a backup
        self.assertTrue(any(e.name == "electric-mining-drill" for e in power.entities))   # coal by belt
        self.assertFalse([n for n in layout.notes if "power" in n])
        seen = set()
        for e in layout.entities():
            tiles = pp.tiles(e) if e.name in ("boiler", "steam-engine", "offshore-pump") else footprint([e])
            self.assertFalse(tiles & seen, e)
            seen |= tiles

    def test_more_power_extends_the_boiler_row_one_pump_feeds(self):
        import json
        from companion import power_plant as pp
        from companion.base_plan import all_ore, blocked_tiles
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        taken = blocked_tiles(world) | all_ore(world)
        home = (float(world["home"]["x"]), float(world["home"]["y"]))
        first = pp.plan_row(world, 1, taken, home, belt=False)
        entities, _, _, row, _ = first
        names = [e.name for e in entities]
        self.assertEqual((names.count("offshore-pump"), names.count("boiler")), (1, 1))
        self.assertNotIn("transport-belt", names)                    # fed by hand: no belt nothing fills
        built = taken | {t for e in entities for t in pp.tiles(e)}
        more, _, _, grown, had_belt = pp.plan_row(world, 2, built, home, rows=[row], belt=False)
        names = [e.name for e in more]
        self.assertEqual((names.count("offshore-pump"), names.count("boiler"), names.count("steam-engine")),
                         (0, 2, 4))                                  # the same pump, two boilers more
        self.assertEqual((grown["boilers"], grown["wx"], grown["wy"]), (3, row["wx"], row["wy"]))
        self.assertIn("pipe", names)                                 # water on from the old last boiler
        self.assertFalse(had_belt)

    def test_a_row_is_extended_only_as_it_stands(self):
        # (run 47: a double row kept without its flag was extended as a single one; its new boilers' water pipe ended
        # four tiles short of the old ones, and the bus's power stood dry for an hour)
        import json
        from companion import power_plant as pp
        from companion.base_plan import all_ore, blocked_tiles
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        taken = blocked_tiles(world) | all_ore(world)
        home = (float(world["home"]["x"]), float(world["home"]["y"]))
        entities, _, _, row, _ = pp.plan_row(world, 2, taken, home, belt=True)
        double, _, _ = pp.place({"wx": row["wx"], "wy": row["wy"], "turns": row["turns"]}, 2,
                                1 if row["mirror"] >= 0 else -1, True, double=True)
        stands = {**world, "entities": list(world.get("entities", [])) + [
            {"n": e.name, "t": e.name, "x": e.x, "y": e.y, "f": world.get("force")} for e in double]}
        spot = {"wx": row["wx"], "wy": row["wy"], "turns": row["turns"]}
        mirror = 1 if row["mirror"] >= 0 else -1
        self.assertTrue(pp._stands(stands, spot, 2, mirror, True, True))
        self.assertFalse(pp._stands(stands, spot, 2, mirror, True, False))          # (kept without its flag)
        built = taken | {t for e in double for t in pp.tiles(e)}
        single = {**row, "boilers": 2, "double": False}
        grown = pp.plan_row(stands, 2, built, home, rows=[single], belt=True)
        self.assertTrue(grown is None or (grown[3]["wx"], grown[3]["wy"]) != (row["wx"], row["wy"]))
        source = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        self.assertIn("out.double=raw.double==true", source)                        # (the game keeps the flag)

    def test_more_power_doubles_what_stands_and_a_fed_row_is_not_stretched(self):
        import json
        from companion import power_plant as pp
        from companion.base_plan import all_ore, blocked_tiles
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        force = world.get("force")
        standing = [{"n": "boiler", "f": force}] * 6 + [{"n": "steam-engine", "f": force}] * 12
        self.assertGreaterEqual(pp.boilers_to_add({**world, "entities": standing}), 6)   # as many again as stand
        self.assertEqual(pp.boilers_to_add({**world, "entities": []}), 1)
        taken = blocked_tiles(world) | all_ore(world)
        home = (float(world["home"]["x"]), float(world["home"]["y"]))
        entities, _, _, row, _ = pp.plan_row(world, 2, taken, home, belt=True)
        self.assertTrue(row["belt"])
        built = taken | {t for e in entities for t in pp.tiles(e)}
        grown = pp.plan_row(world, 2, built, home, rows=[row], belt=True)
        self.assertEqual(grown[3]["boilers"], 4)                 # extended, as before, when asked to
        own = pp.plan_row(world, 2, built, home, rows=[row], belt=True, extend_fed=False)
        self.assertNotEqual((own[3]["wx"], own[3]["wy"]), (row["wx"], row["wy"]))   # a row of its own
        self.assertEqual(own[3]["boilers"], 2)

    def test_two_boiler_rows_share_one_coal_belt_and_one_pump(self):
        import math
        from companion import power_plant as pp
        from companion.modules import EAST, NORTH, SOUTH, WEST
        parts = pp.template(7, belt=True, double=True)
        at = {}
        for n, x, y, d in parts:
            w, h = pp.size_of(n, d)
            x0, y0 = math.floor(x - w / 2 + 0.5), math.floor(y - h / 2 + 0.5)
            for t in {(x0 + i, y0 + j) for i in range(w) for j in range(h)}:
                self.assertNotIn(t, at, (n, x, y))                  # nothing overlaps
                at[t] = (n, x, y, d)
        boilers = [(x, y) for n, x, y, d in parts if n == "boiler"]
        self.assertEqual({y for _, y in boilers}, {2.5, 7.5})      # a row each side of the belt
        self.assertEqual(sum(n == "offshore-pump" for n, *_ in parts), 1)
        for x, y in boilers:                                       # each fed from the one belt
            feed = (x, 4) if y < 5 else (x, 6)
            self.assertEqual(at[feed][0], "burner-inserter")
            self.assertEqual(at[(x, 5)][0], "transport-belt")
        # water: pipes join their neighbours, a pipe-to-ground its open side and its partner, a boiler its two ends
        step = {NORTH: (0, -1), EAST: (1, 0), SOUTH: (0, 1), WEST: (-1, 0)}
        seen, todo = {(0, 2)}, [(0, 2)]
        while todo:
            t = todo.pop()
            n, x, y, d = at[t]
            if n == "pipe":
                nxt = [(t[0] + a, t[1] + b) for a, b in step.values()]
            elif n == "pipe-to-ground":
                a, b = step[d]
                nxt = [(t[0] + a, t[1] + b), (t[0] - 2 * a, t[1] - 2 * b)]
            elif n == "boiler":
                row = 3 if y < 5 else 7
                nxt = [(x - 2, row), (x + 2, row)]
            else:
                continue
            for o in nxt:
                if o in at and at[o][0] in ("pipe", "pipe-to-ground", "boiler") and o not in seen:
                    seen.add(o)
                    todo.append(o)
        reached = {(at[t][1], at[t][2]) for t in seen if at[t][0] == "boiler"}
        self.assertEqual(reached, set(boilers))                    # one pump waters both rows
        self.assertNotIn((0, 5), at)                               # the coal belt comes in over the pipes
        self.assertNotIn((1, 5), at)
        poles = [(x, y) for n, x, y, _ in parts if n == "small-electric-pole"]
        linked, todo = {poles[0]}, [poles[0]]
        while todo:
            a = todo.pop()
            for b in poles:
                if b not in linked and math.dist(a, b) <= 7.5:
                    linked.add(b)
                    todo.append(b)
        self.assertEqual(len(linked), len(poles))                  # one network
        # turned and mirrored, a pipe-to-ground still faces the pipe it joins
        for turns in range(4):
            for mirror in (1, -1):
                placed, _, _ = pp.place({"wx": 10, "wy": 20, "turns": turns}, 4, mirror, double=True)
                pipes = {(math.floor(e.x), math.floor(e.y)) for e in placed if e.name == "pipe"}
                for e in placed:
                    if e.name == "pipe-to-ground":
                        a, b = step[e.direction]
                        self.assertIn((math.floor(e.x) + a, math.floor(e.y) + b), pipes, (turns, mirror))
        # a double row grows into the second row before it grows longer
        grown = pp.template(4, double=True)
        self.assertEqual(sorted(x for n, x, _, _ in grown if n == "boiler"), [4, 4, 8, 8])

    def test_with_coal_far_from_water_the_boilers_go_by_the_coal(self):
        import json
        from companion import power_plant as pp
        from companion.base_plan import all_ore, blocked_tiles, plan_bus_base
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        taken = blocked_tiles(world) | all_ore(world)
        home = (float(world["home"]["x"]), float(world["home"]["y"]))
        entities, _, _, row, _ = pp.plan_row(world, 1, taken, home, belt=False)
        built = taken | {t for e in entities for t in pp.tiles(e)}
        remote = pp.plan_remote_row(world, 4, built, [row], near=home)
        names = [e.name for e in remote[0]]
        self.assertEqual((names.count("offshore-pump"), names.count("boiler")), (0, 4))   # water from the old row
        self.assertGreater(names.count("pipe-to-ground"), 0)
        layout = plan_bus_base(world, Book.vanilla(), science_per_minute=15,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=10, power_rows=[row])
        power = next(s for s in layout.stages if s.name == "power")
        self.assertLess(len(power.entities), 150)            # a coal belt across the map was 200+
        self.assertTrue(layout.power_row.get("remote"))

    def test_a_reported_power_shortage_becomes_a_power_plant_goal(self):
        import json
        from companion.catalog_planner import PURPOSES, CatalogPlanner, purpose_options
        from companion import blueprints as bp
        import tempfile
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        recipes = [{"name": n} for n in PURPOSES["power-plant"]["requires_recipes"]]
        site = {"request_id": "1:40", "purpose": "choose", "recipes": recipes, "resources": [],
                "completed_purposes": ["steam-power", "power-plant"], "origin": {"x": 0, "y": 0}}
        self.assertNotIn("power-plant", [o["id"] for o in purpose_options(site)])   # no shortage reported
        short = {**site, "power_short": True}
        self.assertEqual(purpose_options(short)[0]["id"], "power-plant")
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            planner.world = lambda s: world
            plan = planner(short)
        self.assertEqual(plan["purpose"], "power-plant")
        names = [e["prototype"] for e in plan["entities"]]
        self.assertGreaterEqual(names.count("boiler"), 1)
        self.assertEqual(names.count("steam-engine"), 2 * names.count("boiler"))
        self.assertEqual(len(plan["stages"][0]["manual_supplies"]), names.count("boiler"))


class ModuleOrderTests(unittest.TestCase):
    def test_a_block_for_a_player_is_placed_near_their_spot_with_marked_ends(self):
        import json
        from companion.module_order import plan_module
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        site = {"request_id": "1:77", "origin": {"x": 0, "y": 0}, "machines": {"assembling-machine-2": {}},
                "order": {"item": "electronic-circuit", "rate": 10, "at": {"x": 40, "y": -60}}}
        plan = plan_module(site, world)
        names = [e["prototype"] for e in plan["entities"]]
        self.assertGreater(names.count("assembling-machine-2"), 10)
        self.assertEqual(sorted(m["text"] for m in plan["markers"]),
                         ["copper plate in", "electronic circuit out", "iron plate in"])
        self.assertLess(len(plan["entities"]), 900)                    # within what a plan may hold
        x0 = min(e["position"]["x"] for e in plan["entities"])
        self.assertLess(abs(x0 - 40), 120)                             # by the asked spot


class PoleLineTests(unittest.TestCase):
    def test_a_pole_line_goes_around_a_lake(self):
        import math
        from companion.power import REACH, chain
        lake = {(x, y) for x in range(20, 80) for y in range(-30, 31)}
        line = chain((0.5, 0.5), (100.5, 0.5), set(lake))
        points = [(0.5, 0.5)] + [(e.x, e.y) for e in line]
        self.assertTrue(all(math.dist(a, b) <= REACH for a, b in zip(points, points[1:])))
        self.assertFalse(any((math.floor(e.x), math.floor(e.y)) in lake for e in line))
        self.assertLessEqual(math.dist(points[-1], (100.5, 0.5)), REACH)
        self.assertEqual(len(chain((0.5, 0.5), (900.5, 300.5), set())), 131)   # long lines too


class FirstPowerTests(unittest.TestCase):
    def test_first_power_goes_to_the_nearest_shore_with_a_line_home(self):
        import json, math
        from companion.catalog_planner import available
        from companion.power_planner import plan_power
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        world["entities"] = [e for e in world["entities"] if e.get("f") != world["force"]]   # a fresh start
        site = {"request_id": "1:3", "origin": {"x": 0, "y": 0}, "water_rows": [], "suggestions": {},
                "completed_purposes": ["copper-smelting-start"],   # something to power first
                "recipes": [{"name": n} for n in ("offshore-pump", "boiler", "steam-engine", "pipe",
                                                  "small-electric-pole")]}
        self.assertTrue(available("steam-power", site))       # no water in the site, but a shore on the map
        plan = plan_power(site, world, "steam-power")
        names = [e["prototype"] for e in plan["entities"]]
        self.assertEqual((plan["purpose"], names.count("boiler")), ("steam-power", 1))
        poles = [(e["position"]["x"], e["position"]["y"]) for e in plan["entities"]
                 if e["prototype"] == "small-electric-pole"]
        self.assertTrue(any(math.dist(p, (0, 0)) < 12 for p in poles))   # the line reaches home


class DefenseTests(unittest.TestCase):
    WORLD = {"area": [-200, -200, 200, 200], "home": {"x": 0, "y": 0}, "force": "npc-faction-1", "water": [],
             "resources": {},
             "entities": [{"n": "biter-spawner", "t": "unit-spawner", "x": 160.0, "y": -5.0, "w": 5, "h": 5, "f": "enemy"},
                          {"n": "transport-belt", "t": "transport-belt", "x": 96.5, "y": -4.5, "w": 1, "h": 1,
                           "f": "npc-faction-1"}]}
    SITE = {"request_id": "1:50", "origin": {"x": 0, "y": 0},
            "base": [{"id": "oil-1", "purpose": "oil-processing", "box": [80, -10, 90, 0]},
                     {"id": "smelt", "purpose": "iron-smelting-start", "box": [-5, -5, 5, 5]}]}

    def test_a_threatened_outpost_gets_turrets_and_a_wall_facing_the_nests(self):
        from companion.defense_planner import outposts, plan_defense
        open_ = outposts(self.SITE, self.WORLD)
        self.assertEqual([o["block"]["id"] for o in open_], ["oil-1"])   # the home block is not an outpost
        plan = plan_defense(self.SITE, self.WORLD, open_[0])
        guns = [e for e in plan["entities"] if e["prototype"] == "gun-turret"]
        walls = [e for e in plan["entities"] if e["prototype"] == "stone-wall"]
        self.assertEqual(len(guns), 2)
        self.assertTrue(all(g["position"]["x"] > 90 for g in guns))            # on the side facing the nest
        self.assertTrue(all(w["position"]["x"] > max(g["position"]["x"] for g in guns) for w in walls))
        self.assertNotIn((96.5, -4.5), [(w["position"]["x"], w["position"]["y"]) for w in walls])   # the belt stays
        self.assertEqual({s["item"] for s in plan["stages"][0]["manual_supplies"]}, {"firearm-magazine"})

    def test_with_power_turrets_stand_round_an_ammo_chest_behind_a_wall(self):
        from companion.defense_planner import outposts, plan_defense
        pole = {"n": "small-electric-pole", "t": "electric-pole", "x": 88.5, "y": -5.5, "w": 1, "h": 1,
                "f": "npc-faction-1"}
        world = {**self.WORLD, "entities": [e for e in self.WORLD["entities"] if e["n"] != "transport-belt"] + [pole]}
        plan = plan_defense(self.SITE, world, outposts(self.SITE, world)[0])
        by = {}
        for e in plan["entities"]:
            by.setdefault(e["prototype"], []).append(e)
        self.assertEqual((len(by["gun-turret"]), len(by["inserter"]), len(by["iron-chest"])), (3, 3, 1))
        chest = by["iron-chest"][0]["position"]
        for ins in by["inserter"]:   # each picks from the chest
            dx, dy = chest["x"] - ins["position"]["x"], chest["y"] - ins["position"]["y"]
            self.assertEqual(ins["direction"], {(0, -1): 0, (1, 0): 4, (0, 1): 8, (-1, 0): 12}[(round(dx), round(dy))])
        # one chest to fill, not every turret
        self.assertEqual(plan["stages"][0]["manual_supplies"],
                         [{"entity_id": by["iron-chest"][0]["id"], "item": "firearm-magazine", "slot": "input"}])
        front = max(g["position"]["x"] for g in by["gun-turret"])
        self.assertTrue(all(w["position"]["x"] >= front + 2 for w in by["stone-wall"]))   # a tile between

    def test_walls_follow_research_and_a_radar_watches_beyond(self):
        from companion.defense_planner import outposts, plan_defense
        pole = {"n": "small-electric-pole", "t": "electric-pole", "x": 88.5, "y": -5.5, "w": 1, "h": 1,
                "f": "npc-faction-1"}
        world = {**self.WORLD, "entities": [e for e in self.WORLD["entities"] if e["n"] != "transport-belt"] + [pole]}
        base = ["gun-turret", "firearm-magazine", "radar"]

        def plan(names, w=world):
            site = {**self.SITE, "recipes": [{"name": n} for n in names]}
            out = {}
            for e in plan_defense(site, w, outposts(site, w)[0])["entities"]:
                out.setdefault(e["prototype"], []).append(e["position"])
            return out

        self.assertNotIn("stone-wall", plan(base))                                   # turrets alone at first
        one = {w["x"] for w in plan(base + ["stone-wall"])["stone-wall"]}
        two = {w["x"] for w in plan(base + ["stone-wall", "gate"])["stone-wall"]}
        self.assertEqual((len(one), len(two)), (1, 2))                               # then a row, then a double row
        by = plan(base + ["stone-wall"])
        self.assertEqual(len(by["radar"]), 1)
        r, p = by["radar"][0], by["small-electric-pole"][0]
        self.assertLess(r["x"], min(g["x"] for g in by["gun-turret"]))             # behind the turrets
        self.assertTrue(abs(r["x"] - p["x"]) <= 4 and abs(r["y"] - p["y"]) <= 4)    # in the pole's reach
        watched = {**world, "entities": world["entities"] + [{"n": "radar", "t": "radar", "x": 70.5, "y": 0.5,
                                                               "w": 3, "h": 3, "f": "npc-faction-1"}]}
        self.assertNotIn("radar", plan(base, watched))                              # one already watches here
        self.assertNotIn("radar", plan(base, self.WORLD))                           # no power: no radar

    def test_the_mod_can_place_everything_a_defense_plan_holds(self):
        import re
        from companion.defense_planner import outposts, plan_defense
        source = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        table = re.search(r"local PLACEABLE=\{(.*?)\}", source, re.S).group(1)
        placeable = set(re.findall(r'\[?"?([a-z-]+)"?\]?=true', table))
        kinds = {"gun-turret": "ammo-turret", "stone-wall": "wall", "iron-chest": "container", "inserter": "inserter",
                 "small-electric-pole": "electric-pole", "radar": "radar", "gate": "gate"}
        pole = {"n": "small-electric-pole", "t": "electric-pole", "x": 88.5, "y": -5.5, "w": 1, "h": 1,
                "f": "npc-faction-1"}
        world = {**self.WORLD, "entities": [e for e in self.WORLD["entities"] if e["n"] != "transport-belt"] + [pole]}
        site = {**self.SITE, "recipes": [{"name": n} for n in ("gun-turret", "firearm-magazine", "stone-wall", "gate",
                                                                  "radar")]}
        names = {e["prototype"] for e in plan_defense(site, world, outposts(site, world)[0])["entities"]}
        self.assertIn("radar", names)
        self.assertEqual({n for n in names if kinds.get(n) not in placeable}, set())

    def test_a_defended_outpost_or_one_out_of_reach_is_left(self):
        from companion.defense_planner import outposts
        defended = {**self.SITE, "base": self.SITE["base"] + [{"id": "outpost-defense:oil-1",
                                                                "purpose": "outpost-defense", "box": [93, -9, 97, 0]}]}
        self.assertEqual(outposts(defended, self.WORLD), [])
        far = {**self.WORLD, "entities": [dict(self.WORLD["entities"][0], x=400.0)]}
        self.assertEqual(outposts(self.SITE, far), [])
        # the team world: inside the team area the plot's edge holds it, and a defense is never defended
        # (one-engineer hour 2026-10-24: outposts were put up round the starter turrets)
        self.assertTrue(outposts(self.SITE, self.WORLD))
        teamed = {**self.SITE, "team": {"plot": [-50, -50, 150, 150], "area": [-50, -50, 150, 150], "no_build": []}}
        self.assertEqual(outposts(teamed, self.WORLD), [])
        beyond = {**self.SITE, "team": {"plot": [-300, -300, -200, -200], "area": [-300, -300, -200, -200],
                                        "no_build": []}}
        self.assertTrue(outposts(beyond, self.WORLD))                  # (an outpost past the team area: defended)
        guards = {**beyond, "base": [dict(b, purpose="starter-turrets") for b in self.SITE["base"]]}
        self.assertEqual(outposts(guards, self.WORLD), [])

    def test_defense_is_offered_only_while_a_threatened_block_is_open(self):
        from companion.catalog_planner import unfinished
        site = {**self.SITE, "threats": [{"x": 160.0, "y": -5.0}]}
        self.assertTrue(unfinished("outpost-defense", site))
        covered = {**site, "base": site["base"] + [{"id": "outpost-defense:oil-1", "purpose": "outpost-defense",
                                                    "box": [93, -9, 97, 0]}]}
        self.assertFalse(unfinished("outpost-defense", covered))    # defended: build something else

    def test_an_ally_who_asks_gets_turrets_by_their_base(self):
        from companion.catalog_planner import available, unfinished
        from companion.defense_planner import plan_for_ally
        recipes = [{"name": n} for n in ("gun-turret", "firearm-magazine", "stone-wall")]
        site = {**self.SITE, "recipes": recipes, "help": {"kind": "defend", "player_index": 1, "x": 120.0, "y": 40.0}}
        self.assertTrue(available("ally-defense", site))
        self.assertTrue(unfinished("ally-defense", site))
        self.assertFalse(available("ally-defense", {**site, "help": None}))
        plan = plan_for_ally(site, self.WORLD)                  # the nest is at (160, -5), north-east of them
        guns = [e for e in plan["entities"] if e["prototype"] == "gun-turret"]
        self.assertEqual((plan["purpose"], len(guns)), ("ally-defense", 4))
        self.assertTrue(all(abs(g["position"]["x"] - 120) < 30 and abs(g["position"]["y"] - 40) < 30 for g in guns))
        self.assertTrue(all(g["position"]["y"] < 40 - 12 for g in guns))   # north of them, facing the nest

    def test_the_edge_of_the_main_base_is_defended_too(self):
        from companion.defense_planner import frontier, plan_defense
        nests = [{"n": "biter-spawner", "t": "unit-spawner", "x": x, "y": 70.0, "w": 5, "h": 5, "f": "enemy"}
                 for x in (-10.0, 0.0, 12.0)]
        world = {**self.WORLD, "entities": nests}
        open_ = frontier(self.SITE, world)
        self.assertEqual([o["block"]["id"] for o in open_], ["smelt"])   # the home block faces these nests
        self.assertEqual(open_[0]["nests"], 3)
        plan = plan_defense(self.SITE, world, open_[0])
        guns = [e for e in plan["entities"] if e["prototype"] == "gun-turret"]
        self.assertEqual(len(guns), 4)                                    # a bigger group, more turrets
        self.assertTrue(all(g["position"]["y"] > 5 for g in guns))        # on the south side, facing them
        spots = sorted(g["position"]["x"] for g in guns)
        self.assertTrue(all(b - a >= 2 for a, b in zip(spots, spots[1:])))   # 2x2 turrets never overlap


class PlaceTests(unittest.TestCase):
    """A new bus weighs a few places by what each takes to build before its first science (bus_planner.place_bus)."""
    WORLD = {"home": {"x": 0, "y": 0}}

    @staticmethod
    def layout(x, y, columns, belts):
        from types import SimpleNamespace
        from companion.base_plan import Placed, Stage
        stages = [Stage("bus-red-science", [Placed("transport-belt", x + 0.5, y + 0.5, 0) for _ in range(belts)]),
                  Stage("bus-labs", []), Stage("smelting-iron-plate", [Placed("transport-belt", x, y, 0)] * 5000)]
        return SimpleNamespace(bus=SimpleNamespace(x=x, y=y), stages=stages, notes=[], columns=columns)

    def planner(self, calls):
        # (two other places: east, for 19 lanes or fewer, cheap; south, for 16 or fewer, dear)
        others = [((200, 0), 19, 10), ((0, 200), 16, 50)]

        def fake(world, book, **kw):
            calls.append((kw["columns"], kw.get("avoid"), bool(kw.get("fit_only"))))
            for (x, y), most, belts in others:
                if (x, y) not in kw.get("avoid", ()) and kw["columns"] <= most:
                    return self.layout(x, y, kw["columns"], 0 if kw.get("fit_only") else belts)
            raise ValueError("no room for a bus near home")
        return fake

    def test_the_place_that_takes_least_to_build_before_its_science(self):
        calls = []
        first = self.layout(0, 0, 20, 1000)   # (where it first fit: much to build)
        with mock.patch.object(bus_planner, "plan_bus_base", self.planner(calls)):
            best = bus_planner.place_bus(self.WORLD, Book.vanilla(), first, {"columns": 20}, 10)
        self.assertEqual((best.bus.x, best.bus.y, best.columns), (200, 0, 19))
        self.assertIn("of 3 places for the bus, 200,0 with 19 lanes takes least", best.notes[0])
        self.assertEqual([c for c in calls if not c[2]], [(19, ((0, 0),), False), (16, ((0, 0), (200, 0)), False)])
        self.assertEqual([c[0] for c in calls if c[2]], [20, 19, 20, 19, 18, 17, 16])   # (widest first)
        # where it first fit is cheapest, it stays; no other place, nor time for one: it stays
        cheap = self.layout(0, 0, 20, 1)
        with mock.patch.object(bus_planner, "plan_bus_base", self.planner([])):
            self.assertIs(bus_planner.place_bus(self.WORLD, Book.vanilla(), cheap, {"columns": 20}, 10), cheap)
        with mock.patch.object(bus_planner, "plan_bus_base", side_effect=ValueError("no room")):
            self.assertIs(bus_planner.place_bus(self.WORLD, Book.vanilla(), first, {"columns": 20}, 10), first)
        with mock.patch.object(bus_planner, "plan_bus_base", self.planner([])), \
                mock.patch.object(bus_planner.time, "monotonic", side_effect=[0.0] + [1e6] * 10):
            self.assertIs(bus_planner.place_bus(self.WORLD, Book.vanilla(), first, {"columns": 20}, 10), first)
        # (time up after a probe found a place: no layout starts)
        calls = []
        with mock.patch.object(bus_planner, "plan_bus_base", self.planner(calls)), \
                mock.patch.object(bus_planner.time, "monotonic", side_effect=[0.0, 0.0, 0.0, 0.0, 1e6] + [1e6] * 10):
            self.assertIs(bus_planner.place_bus(self.WORLD, Book.vanilla(), first, {"columns": 20}, 10), first)
        self.assertEqual([c for c in calls if not c[2]], [])

    def test_a_place_that_goes_without_a_route_is_no_cheaper(self):
        planner = self.planner([])

        def short(world, book, **kw):   # (the cheap east place: its ore belt found no way)
            out = planner(world, book, **kw)
            if not kw.get("fit_only") and out.bus.x == 200:
                out.notes.append("no belt route from the iron-ore field to its furnaces")
            return out
        first = self.layout(0, 0, 20, 1000)
        first.notes.append("the bus keeps 20 of 26 lanes: a wider one fit nowhere")   # (not a shortfall)
        with mock.patch.object(bus_planner, "plan_bus_base", short):
            best = bus_planner.place_bus(self.WORLD, Book.vanilla(), first, {"columns": 20}, 10)
        self.assertEqual((best.bus.x, best.bus.y), (0, 200))   # (the dearer south place, with all it needs)
        self.assertEqual(bus_planner.shortfalls(first), 0)

    def test_a_coal_line_whose_fields_make_too_little_falls_short(self):
        from companion.base_plan import Placed, coal_short
        drills = [Placed("electric-mining-drill", 0.5, 0.5, 0)] * 3 + [Placed("transport-belt", 0.5, 4.5, 0)]
        self.assertEqual(coal_short(drills, 2.0), 0.5)               # (three drills, 1.5/s, of 2)
        self.assertEqual(coal_short(drills, 1.2), 0.0)
        self.assertEqual(coal_short([Placed("splitter", 0.5, 0.5, 0)], 5.0), 0.0)   # (a branch: the line has it)
        self.assertEqual(coal_short(None, 5.0), 0.0)                   # (no line: noted as fed by hand)

    def test_another_place_is_not_the_same_place_moved(self):
        from companion.base_plan import PLACE_GAP, find_fit
        shape = {(x, y) for x in range(4) for y in range(4)}
        near = find_fit(set(), shape, (0, 0), (-100, -100, 100, 100), anchor=(0, 0))
        other = find_fit(set(), shape, (0, 0), (-100, -100, 100, 100), anchor=(0, 0), avoid=[near])
        self.assertGreaterEqual(math.hypot(other[0] - near[0], other[1] - near[1]), PLACE_GAP)


class FirstShareTests(unittest.TestCase):
    """The first bus makes half the science it aims at; growth sections below it make the rest (C, 2026-10-26)."""

    def test_the_first_bus_is_built_for_half_its_aim_in_fives(self):
        self.assertEqual([bus_planner.first_rate(a) for a in (5, 10, 20, 30, 45)], [5.0, 5.0, 10.0, 15.0, 20.0])
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        world = {"area": [-70, -70, 70, 90], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                 "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        site = {"request_id": "1:60", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
                "researched": ["logistic-science-pack"]}
        aim = bus_planner.goal(site)[0]
        saved = bus_planner.plan_stage(site, world, Book.vanilla())["base_plan"]
        self.assertEqual((saved["aim"], saved["spm"]), (aim, bus_planner.first_rate(aim)))

    def test_it_grows_by_sections_at_its_aim_as_planned_and_at_least_once(self):
        from companion import growth
        copies = growth.MAX_GROWTH
        self.assertEqual(bus_planner.growth_wanted({}, {"spm": 10.0, "aim": 20.0}), copies)
        self.assertEqual(bus_planner.growth_wanted({}, {"spm": 15.0}), copies)   # (a bus planned before: as before)
        with mock.patch.object(bus_planner.factory_plan, "bus_targets",
                               return_value=mock.Mock(growth=0)):   # (a plan wanting no more than the bus's rate)
            self.assertEqual(bus_planner.growth_wanted({}, {"spm": 10.0, "aim": 20.0}), 1)   # (its aim: one more)
            self.assertEqual(bus_planner.growth_wanted({}, {"spm": 10.0}), 0)

    def test_a_growth_section_makes_what_the_bus_aims_at(self):
        from companion import growth
        from companion.base_plan import plan_bus_base
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        dump = {"area": [-150, -100, 150, 300], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        packs = ("automation-science-pack", "logistic-science-pack")
        goal = growth.goal_name(1, list(packs))

        def grown(**kwargs):
            lay = plan_bus_base(dump, Book.vanilla(), science_per_minute=10, packs=packs, extra=(goal,), **kwargs)
            return sorted((a.module.name, len(a.module.entities)) for a in lay.bus.attached
                          if a.module.name.endswith("-g1"))
        at_aim = sorted((m.name, len(m.entities)) for m in growth.section(1, 20.0, packs, Book.vanilla()))
        self.assertEqual(grown(aim=20.0), at_aim)
        self.assertNotEqual(grown(), at_aim)   # (without an aim: the bus's own rate, as before)


class UpgradeTests(unittest.TestCase):
    def test_a_flat_out_module_feeding_a_starved_one_is_upgraded_in_place(self):
        from companion.bus_planner import plan_stage, replay
        from companion.upgrade_planner import plan_upgrade, upgradable
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        world = {"area": [-70, -70, 70, 90], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                 "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        site = {"request_id": "1:60", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
                "researched": ["logistic-science-pack"]}
        with mock.patch.object(bus_planner, "FIRST_SHARE", 1.0),                 mock.patch.object(bus_planner, "PLACES", 1):   # (a gears module flat out at the full rate)
            first = plan_stage(site, world, Book.vanilla())
        saved = {**first["base_plan"], "done": {s: 1 for s in first["base_plan"]["stages"]}}
        unlocked = [{"name": n} for n in ("assembling-machine-3", "fast-inserter")]
        base = [{"id": "g", "purpose": "bus-base", "stage": "bus-gears", "machines": {"assembling-machine-2": 1},
                 "status": {"working": 1}},
                {"id": "r", "purpose": "bus-base", "stage": "bus-green-science", "machines": {"assembling-machine-2": 3},
                 "status": {"starved": 3}}]
        busy = {**site, "base_plan": saved, "base": base, "recipes": unlocked}
        self.assertTrue(upgradable(busy))
        self.assertFalse(upgradable({**busy, "recipes": []}))      # nothing better unlocked
        plan = plan_upgrade(busy, world, Book.vanilla())
        self.assertEqual(plan["upgrades"], "bus-gears")
        names = [e["prototype"] for e in plan["entities"]]
        self.assertIn("assembling-machine-3", names)
        self.assertNotIn("assembling-machine-2", names)
        gears = next(s for s in replay(busy, world, Book.vanilla()).stages if s.name == "bus-gears")
        old = {(e.x, e.y) for e in gears.entities if e.name == "assembling-machine-2"}
        new = {(e["position"]["x"], e["position"]["y"]) for e in plan["entities"] if e["prototype"] == "assembling-machine-3"}
        self.assertEqual(new, old)                                   # same spots
        self.assertTrue(all(e.get("recipe") == "iron-gear-wheel" for e in plan["entities"]
                            if e["prototype"] == "assembling-machine-3"))

    def test_a_busy_smelting_block_gets_steel_furnaces_where_its_stone_ones_stand(self):
        import json
        from companion.bus_planner import plan_stage, replay
        from companion.upgrade_planner import plan_upgrade, upgradable
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        site = {"request_id": "1:61", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
                "researched": ["logistic-science-pack"]}
        first = plan_stage(site, world, Book.vanilla())
        saved = {**first["base_plan"], "done": {s: 1 for s in first["base_plan"]["stages"]}}
        base = [{"id": "s", "purpose": "bus-base", "stage": "smelting-iron-plate", "machines": {"stone-furnace": 24},
                 "status": {"working": 23, "starved": 1}},
                {"id": "g", "purpose": "bus-base", "stage": "bus-gears", "machines": {"assembling-machine-2": 2},
                 "status": {"starved": 2}}]
        busy = {**site, "base_plan": saved, "base": base, "recipes": [{"name": "steel-furnace"}]}
        self.assertTrue(upgradable(busy))
        plan = plan_upgrade(busy, world, Book.vanilla())
        self.assertEqual(plan["upgrades"], "smelting-iron-plate")
        stages = {s.name: s for s in replay(busy, world, Book.vanilla()).stages}
        old = {(e.x, e.y) for e in stages["smelting-iron-plate"].entities if e.name == "stone-furnace"}
        new = {(e["position"]["x"], e["position"]["y"]) for e in plan["entities"] if e["prototype"] == "steel-furnace"}
        self.assertEqual(new, old)                                   # the same 2x2 spots
        self.assertNotIn("stone-furnace", [e["prototype"] for e in plan["entities"]])
        later = {(e.x, e.y) for e in stages["smelting-iron-plate-more"].entities if e.name == "electric-mining-drill"}
        drills = {(e["position"]["x"], e["position"]["y"]) for e in plan["entities"]
                  if e["prototype"] == "electric-mining-drill"}
        self.assertTrue(later)
        self.assertEqual(drills, later)                                # and the ore to run them on


class MallTests(unittest.TestCase):
    def test_every_machine_gets_its_ingredients_and_fills_a_limited_chest(self):
        from companion.mall import BULK_SLOTS, CHEST_SLOTS, mall
        from companion.smelting import footprint
        book = Book.vanilla()
        m = mall(book)
        machines = {e.recipe: e for e in m.entities if e.recipe}
        self.assertEqual(len(machines), 12)
        at = {}
        for e in m.entities:
            for tile in footprint([e]):
                self.assertNotIn(tile, at, (e, at.get(tile)))
                at[tile] = e
        belts = {k: e for k, e in at.items() if e.name == "transport-belt"}
        for recipe, machine in machines.items():
            x0 = int(machine.x - 1.5)
            ingredients = {n for n, _ in book.recipes[recipe].ingredients}
            feeders = []
            if ingredients - {"electronic-circuit", "copper-plate", "transport-belt", "inserter"}:
                feeders.append(at[(x0, 6)].name)          # plain inserter from belt A (row 5)
                self.assertIn((x0, 5), belts)
            if ingredients & {"electronic-circuit", "copper-plate"}:
                self.assertEqual(at[(x0 + 1, 6)].name, "long-handed-inserter")   # belt B (row 4)
                self.assertIn((x0 + 1, 4), belts)
            for chained in ingredients & {"transport-belt", "inserter"}:
                source = machines[chained]
                gap = x0 - 1 if source.x < machine.x else x0 + 3
                self.assertEqual(at[(gap, 8)].name, "inserter", recipe)
                self.assertEqual(abs(source.x - machine.x), 4)
            self.assertEqual(at[(x0 + 1, 10)].name, "inserter")
            self.assertEqual((at[(x0 + 1, 11)].name, at[(x0 + 1, 11)].bar),
                             ("iron-chest", BULK_SLOTS.get(recipe, CHEST_SLOTS)))
        # every inserter is within a small pole's supply area (2.5 tiles)
        poles = [(e.x, e.y) for e in m.entities if e.name == "small-electric-pole"]
        for e in m.entities:
            if "inserter" in e.name:
                self.assertTrue(any(abs(e.x - px) <= 2.5 and abs(e.y - py) <= 2.5 for px, py in poles), e)
        self.assertEqual({p.item for p in m.ports},
                         {"iron-plate", "iron-gear-wheel", "electronic-circuit", "copper-plate"})

    def test_two_narrow_parts_tap_only_the_lanes_they_use(self):
        from companion.mall import mall
        book = Book.vanilla()
        logistics, machines = mall(book, part="logistics"), mall(book, part="machines")
        self.assertEqual({p.item for p in logistics.ports}, {"iron-plate", "iron-gear-wheel", "electronic-circuit"})
        self.assertEqual({p.item for p in machines.ports},
                         {"iron-plate", "iron-gear-wheel", "electronic-circuit", "copper-plate"})
        self.assertLessEqual(max(logistics.width, machines.width), 30)
        self.assertEqual(len([e for e in logistics.entities if e.recipe]) + len([e for e in machines.entities if e.recipe]),
                         12)

    def test_only_unlocked_recipes_and_chains_with_their_feeder(self):
        from companion.mall import recipes_for
        early = {"pipe", "iron-chest", "firearm-magazine", "transport-belt", "inserter", "long-handed-inserter",
                 "electric-mining-drill", "assembling-machine-1", "underground-belt"}
        self.assertEqual(recipes_for(early), ["transport-belt", "underground-belt", "inserter", "long-handed-inserter",
                                              "electric-mining-drill", "assembling-machine-1", "pipe", "iron-chest",
                                              "firearm-magazine"])
        self.assertNotIn("splitter", recipes_for(early - {"transport-belt"} | {"splitter"}))


class RetireTests(unittest.TestCase):
    DONE = ["iron-smelting-start", "copper-smelting-start", "research-lab", "red-science-automation",
            "plate-belts", "steam-power", "steel-smelting"]

    def test_the_bus_supersedes_old_smelting_and_science_by_personality(self):
        from companion.bus_planner import retired_early, superseded
        early = {"completed_purposes": ["iron-smelting-start", "iron-smelting-expansion", "copper-smelting-start"]}
        self.assertEqual(retired_early(early, "smelting-iron-plate"), ["iron-smelting-start", "iron-smelting-expansion"])
        self.assertEqual(retired_early(early, "smelting-copper-plate"), ["copper-smelting-start"])
        self.assertEqual(retired_early({**early, "personality": {"traits": ["cautious"]}}, "smelting-iron-plate"),
                         ["iron-smelting-expansion"])
        self.assertEqual(retired_early(early, "bus-gears"), [])
        red = ("automation-science-pack",)
        site = {"completed_purposes": self.DONE}
        self.assertEqual(superseded(site, red), ["iron-smelting-start", "copper-smelting-start", "plate-belts",
                                                 "red-science-automation", "research-lab"])
        self.assertEqual(superseded({**site, "personality": {"traits": ["hoarder"]}}, red), [])
        self.assertNotIn("iron-smelting-start", superseded({**site, "personality": {"traits": ["traditionalist"]}}, red))
        # blue science is not on the bus yet: the old lab (and what feeds it) stays
        blue = {"completed_purposes": self.DONE + ["blue-science-automation"]}
        self.assertNotIn("research-lab", superseded(blue, red))

    def test_old_smelting_goes_with_its_bus_stage_the_rest_with_the_last(self):
        from companion.bus_planner import plan_stage
        iron = [[y, -40, -25] for y in range(-40, -25)]
        copper = [[y, 25, 40] for y in range(-40, -25)]
        world = {"area": [-60, -60, 60, 60], "home": {"x": 0, "y": 0}, "force": "npc-faction-1",
                 "resources": {"iron-ore": iron, "copper-ore": copper}, "water": [], "entities": []}
        site = {"request_id": "1:5", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
                "completed_purposes": self.DONE}
        first = plan_stage(site, world, Book.vanilla())
        self.assertEqual(first["base_plan"]["stage"], "smelting-iron-plate")
        self.assertEqual(first["base_plan"]["extra"], [])   # (red science only: no mall on the bus, none to count)
        # the bus's iron smelting takes the hand-fed iron with it; nothing else goes yet
        self.assertEqual(first.get("retire"), [p for p in ("iron-smelting-start", "iron-smelting-expansion",
                                                           "electric-iron-mining") if p in self.DONE])
        saved = dict(first["base_plan"])
        stages = saved["stages"]
        saved["done"] = {s: 1 for s in stages[:-1]}
        last = plan_stage({**site, "base_plan": saved}, world, Book.vanilla())
        self.assertEqual(last["base_plan"]["stage"], stages[-1])
        self.assertIn("iron-smelting-start", last["retire"])


class RocketTests(unittest.TestCase):
    """The rocket section: fluid rows, the oil complex, and the bus extension that brings them together."""
    SITE = {"request_id": "1:40", "difficulty": "normal", "origin": {"x": 0, "y": 0}, "machines": {},
            "researched": ["logistic-science-pack", "chemical-science-pack", "rocket-silo"],
            "completed_purposes": ["iron-smelting-start", "research-lab", "red-science-automation", "oil-processing",
                                   "blue-science-automation", "oil-gathering"]}

    def test_fluid_rows_take_their_fluid_on_the_far_edge(self):
        from companion import rocket_modules as r
        from companion.bus import mirrored
        book = Book.vanilla(True)
        pu = r.processing_units(0.1, book)
        fluid = next(p for p in pu.ports if p.kind == "fluid")
        self.assertEqual((fluid.item, fluid.x), ("sulfuric-acid", pu.width - 1))
        self.assertTrue(all(p.x == 0 for p in pu.ports if p.kind in ("in", "out")))
        flipped = next(p for p in mirrored(pu).ports if p.kind == "fluid")
        self.assertEqual(flipped.x, 0)   # on the west side the far edge is the west one
        # assemblers face south so their fluid inlet is on the pipe row
        self.assertTrue(all(e.direction == 8 for e in pu.entities if e.name == "assembling-machine-2"))
        silo = r.silo(0.05)
        self.assertEqual(sorted(p.item for p in silo.ports),
                         ["low-density-structure", "processing-unit", "rocket-fuel"])

    def test_the_oil_complex_keeps_its_fluids_apart(self):
        from companion.oil_complex import oil_complex
        m = oil_complex()
        fluids = m.pipe_fluids
        plain = {(int(e.x - 0.5), int(e.y - 0.5)) for e in m.entities if e.name == "pipe"}
        for (x, y) in plain:
            for n in ((x + 1, y), (x, y + 1)):
                if n in plain:
                    self.assertEqual(fluids[(x, y)], fluids[n], (x, y))
        self.assertEqual({p.item for p in m.ports if p.kind.startswith("fluid")},
                         {"crude-oil", "water", "lubricant", "sulfuric-acid"})
        self.assertEqual(sum(1 for e in m.entities if e.name == "chemical-plant"), 6)

    def test_the_rocket_goal_comes_with_the_silo_research(self):
        from companion.bus_planner import goal
        self.assertNotIn("rocket-part", goal({"researched": ["logistic-science-pack", "chemical-science-pack"]})[1])
        self.assertIn("rocket-part", goal(self.SITE)[1])

    def test_a_bus_planned_for_the_rocket_keeps_its_oil_complex_in_place(self):
        import json
        from companion import power_plant as pp
        from companion.bus_planner import plan_stage
        from companion.rocket_plan import _tiles
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        world["resources"]["crude-oil"] = world["resources"]["crude-oil"] + [[14, 96, 96], [-2, 100, 100]]
        first = plan_stage(self.SITE, world, Book.vanilla(True))
        saved = first["base_plan"]
        names = saved["stages"]
        self.assertEqual(saved["extra"], ["chemical-science-pack", "rocket-part", mall_goal(saved)])
        for name in ("oil-complex", "lane-rocket-fuel", "lane-stone-brick", "lane-iron-ore", "bus-processing-units",
                     "bus-electric-engines", "bus-concrete", "bus-foundations", "bus-rocket-silo"):
            self.assertIn(name, names)
        self.assertLess(names.index("oil-complex"), names.index("bus-processing-units"))
        self.assertLess(names.index("bus-labs-blue"), names.index("oil-complex"))
        site = [s for s in saved["sites"] if s.startswith("oil-complex=")]
        self.assertEqual(len(site), 1)
        # A later stage replans with the saved spot: the same stages, the complex where it was.
        later = plan_stage({**self.SITE, "base_plan": {**saved, "done": {names[0]: 1}}}, world, Book.vanilla(True))
        self.assertEqual(later["base_plan"]["stages"], names)
        self.assertEqual(later["base_plan"]["sites"], saved["sites"])


class GrowthTests(unittest.TestCase):
    """A finished bus keeps growing: sections with their own smelting, fed from new ore fields."""

    def test_a_plate_module_is_fed_from_its_far_edge(self):
        from companion.growth import plate_module
        m = plate_module("iron-ore", "iron-plate", 2.0)
        ext = {p.item: p for p in m.ports if p.kind == "ext"}
        self.assertEqual(set(ext), {"iron-ore", "coal"})
        self.assertTrue(all(p.x > m.width - 4 for p in ext.values()))      # the far edge
        out = next(p for p in m.ports if p.kind == "out")
        self.assertEqual((out.item, out.x), ("iron-plate", 0))              # plates to the bus
        self.assertEqual(sum(1 for e in m.entities if e.name == "stone-furnace"), 7)

    def test_growth_goals_keep_their_order_and_science(self):
        from companion.base_plan import ordered_goals
        from companion.bus_planner import extendable
        from companion.growth import parse
        self.assertEqual(ordered_goals(["growth-1-2", "chemical-science-pack", "growth-2-3"]),
                         ["growth-1-2", "chemical-science-pack", "growth-2-3"])
        self.assertEqual(parse("growth-1-2"), (1, ("automation-science-pack", "logistic-science-pack")))
        done = {"stages": ["a"], "done": {"a": 1}, "packs": ["automation-science-pack", "logistic-science-pack"],
                "extra": ["chemical-science-pack", "growth-1-3"]}
        site = {"researched": ["logistic-science-pack", "chemical-science-pack"], "base_plan": done}
        self.assertEqual(extendable(site), ("growth-2-3",))
        many = {**done, "extra": ["chemical-science-pack"] + [f"growth-{i}-3" for i in range(1, 5)]}
        self.assertEqual(extendable({**site, "base_plan": many}), ())    # a limit, for now

    def test_a_growth_section_gets_its_own_ore_fields(self):
        import json
        from companion.base_plan import plan_bus_base
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        layout = plan_bus_base(world, Book.vanilla(), science_per_minute=10,
                               packs=("automation-science-pack", "logistic-science-pack"), columns=8,
                               extra=("growth-1-2",), base_labs=True, ahead=False)
        names = [s.name for s in layout.stages]
        for name in ("supply-iron-plate-g1", "supply-copper-plate-g1", "bus-smelt-iron-plate-g1", "bus-labs-g1"):
            self.assertIn(name, names)
        self.assertLess(names.index("supply-iron-plate-g1"), names.index("bus-smelt-iron-plate-g1"))
        self.assertFalse([n for n in layout.notes if n.startswith("growth")], layout.notes)


class EarlyGameTests(unittest.TestCase):
    """The burner phase the way a player runs it: the coal loop grows with the burners, and once electric
    drills are researched the boilers get coal by belt."""

    @staticmethod
    def world_with_hand_fed_plant():
        import json
        from companion import power_plant as pp
        from companion.base_plan import all_ore, blocked_tiles
        world = json.loads((ROOT / "tests" / "fixtures" / "k-blue-map.json").read_text(encoding="utf-8"))
        taken = blocked_tiles(world) | all_ore(world)
        home = (float(world["home"]["x"]), float(world["home"]["y"]))
        entities, _, _, row, _ = pp.plan_row(world, 2, taken, home, belt=False)
        sizes = {"boiler": (3, 2), "steam-engine": (3, 5), "offshore-pump": (1, 2)}
        for e in entities:   # the plant as the map dump lists it (unrotated size, direction)
            w, h = sizes.get(e.name, (1, 1))
            world["entities"].append({"n": e.name, "t": e.name, "x": e.x, "y": e.y, "w": w, "h": h,
                                      "d": e.direction, "f": world["force"]})
        return world, row

    def check_fed(self, plan, world, boilers):
        from companion.base_plan import blocked_tiles
        names = [e["prototype"] for e in plan["entities"]]
        self.assertEqual(plan["purpose"], "boiler-coal")
        self.assertEqual(names.count("burner-inserter"), boilers)        # one behind each boiler
        self.assertGreaterEqual(names.count("electric-mining-drill"), 1)  # on coal, powered
        self.assertGreater(names.count("transport-belt"), 3)
        blocked = blocked_tiles(world)
        for e in plan["entities"]:
            tile = (math.floor(e["position"]["x"]), math.floor(e["position"]["y"]))
            if e["prototype"] in ("transport-belt", "burner-inserter"):
                self.assertNotIn(tile, blocked, e)

    def test_a_saved_hand_fed_row_gets_its_coal_belt_as_the_template_lays_it(self):
        from companion.power_planner import plan_boiler_coal
        world, row = self.world_with_hand_fed_plant()
        site = {"request_id": "1:20", "origin": {"x": 0, "y": 0}, "power_rows": [row]}
        plan = plan_boiler_coal(site, world)
        self.check_fed(plan, world, 2)
        self.assertEqual((plan["power_row"]["belt"], plan["power_row"]["boilers"]), (True, 2))

    def test_a_native_starter_plant_is_fed_from_the_boilers_on_the_map(self):
        from companion.power_planner import plan_boiler_coal
        world, _ = self.world_with_hand_fed_plant()
        plan = plan_boiler_coal({"request_id": "1:21", "origin": {"x": 0, "y": 0}}, world)
        self.check_fed(plan, world, 2)
        self.assertNotIn("power_row", plan)

    def test_ore_running_out_brings_the_opening_back_to_fresh_ground(self):
        from companion.catalog_planner import available, coal_enough, running_out, smelters
        fresh = {"name": "iron-ore", "left": 90000, "rate": 1.0, "minutes": 1500}
        low = {"name": "iron-ore", "left": 300, "rate": 1.0, "minutes": 5}
        iron = {"purpose": "iron-smelting-start", "machines": {"burner-mining-drill": 4, "stone-furnace": 4},
                "recipes": ["iron-plate"]}
        self.assertEqual(smelters({"base": [{**iron, "ore": fresh}]}, "iron-plate"), 4)
        self.assertEqual(smelters({"base": [{**iron, "ore": low}]}, "iron-plate"), 0)   # it will stop: build more
        coal_low = {"name": "coal", "left": 200, "rate": 1.0, "minutes": 3}
        loop = {"purpose": "coal-loop", "machines": {"burner-mining-drill": 4}, "ore": coal_low}
        site = {"completed_purposes": ["iron-smelting-start", "coal-loop"], "base": [loop, {**iron, "ore": fresh}],
                "resources": [{"resource": "coal", "rows": []}]}
        self.assertFalse(coal_enough(site))                       # the loop's coal is nearly gone: a new loop
        self.assertTrue(running_out(site, "coal"))
        self.assertFalse(running_out(site, "iron-ore"))
        # a mining block built once (one at a time) comes back when its ore runs out
        mine = {"purpose": "electric-iron-mining", "machines": {"electric-mining-drill": 2},
                "ore": {"name": "iron-ore", "left": 100, "rate": 1.0, "minutes": 1}}
        base = {"completed_purposes": ["steam-power", "electric-iron-mining"],
                "recipes": [{"name": n} for n in ("electric-mining-drill", "small-electric-pole", "stone-furnace")],
                "resources": [{"resource": "iron-ore", "rows": []}], "suggestions": {"electric-iron-mining": {}}}
        self.assertTrue(available("electric-iron-mining", {**base, "base": [mine]}))
        plenty = {**mine, "ore": {**mine["ore"], "left": 90000, "minutes": 1500}}
        self.assertFalse(available("electric-iron-mining", {**base, "base": [plenty]}))

    def test_the_coal_loop_grows_with_the_burners_up_to_the_budget(self):
        from companion.catalog_planner import LOOP_MAX, coal_draw, coal_enough, opening_goal
        base = [{"purpose": "coal-loop", "machines": {"burner-mining-drill": 4}},
                {"purpose": "iron-smelting-start", "machines": {"burner-mining-drill": 4, "stone-furnace": 4}}]
        site = {"completed_purposes": ["iron-smelting-start", "coal-loop"], "base": base,
                "resources": [{"resource": "coal", "rows": []}]}
        self.assertTrue(coal_enough(site))
        grown = {**site, "base": base + [{"purpose": "iron-smelting-expansion",
                                          "machines": {"burner-mining-drill": 8, "stone-furnace": 8}},
                                         {"purpose": "steam-power", "machines": {"boiler": 1}}]}
        self.assertGreater(coal_draw(grown), 4 * 0.2125)
        self.assertFalse(coal_enough(grown))                               # more burners than the loop spares
        self.assertEqual(opening_goal(grown), "coal-loop")
        belted = {**grown, "completed_purposes": grown["completed_purposes"] + ["boiler-coal"]}
        self.assertLess(coal_draw(belted), coal_draw(grown))               # boilers on a belt burn no loop coal
        capped = {**grown, "base": grown["base"] + [{"purpose": "coal-loop",
                                                     "machines": {"burner-mining-drill": LOOP_MAX - 4}}]}
        self.assertTrue(coal_enough(capped))


if __name__ == "__main__":
    unittest.main()
