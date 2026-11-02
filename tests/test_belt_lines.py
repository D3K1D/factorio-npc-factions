"""The base's belt lines (companion/belt_lines.py): coal from a few shared lines, each tracked for what goes on,
what its connections take and what is spare (the user, 2026-10-25: not a drill field and a belt per consumer)."""
import json
import math
import unittest
from pathlib import Path

from companion import belt_lines, electric_start
from companion.modules import EAST, NORTH, Placed, SOUTH
from companion.smelting import footprint

FIXTURE = Path(__file__).parent / "fixtures" / "bw-tight-bus.json"


def fixture():
    cap = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return cap["site"], cap["world"]


def standing(world, op, claims=None):
    """The map once a line laid by `op` stands: its belts, and the game's record of it."""
    ents = list(world["entities"]) + [{"n": "transport-belt", "t": "transport-belt", "x": x + 0.5, "y": y + 0.5, "d": d,
                                       "f": world["force"], "w": 1, "h": 1} for x, y, d in op["tiles"]]
    return {**world, "entities": ents,
            "belt_lines": [{"id": op["id"], "item": op["item"], "tiles": op["tiles"], "supply": op["supply"],
                            "claims": claims if claims is not None else {op["key"]: op["rate"]}, "ok": True}]}


class LineTests(unittest.TestCase):

    def test_what_is_spare_is_what_goes_on_less_what_its_connections_take(self):
        line = belt_lines.Line("coal@0,0", "coal", [(0, y, SOUTH) for y in range(5)], 3.0, {"a": 0.27, "b": 1.8})
        self.assertAlmostEqual(line.spare, 0.93)
        full = belt_lines.Line("coal@0,0", "coal", [(0, 0, SOUTH)], 40.0, {})
        self.assertEqual(full.spare, 2 * belt_lines.LANE)           # (a yellow belt carries 15 at most)

    def test_a_splitter_goes_only_on_a_straight_stretch(self):
        tiles = [(0, 0, SOUTH), (0, 1, SOUTH), (0, 2, SOUTH), (0, 3, EAST), (1, 3, EAST), (2, 3, EAST)]
        line = belt_lines.Line("coal@0,0", "coal", tiles, 3.0)
        self.assertEqual(belt_lines.straight(line), [(0, 1, SOUTH), (1, 3, EAST)])

    def test_a_tap_is_a_splitter_on_the_line_and_its_other_output_belted_to_the_consumer(self):
        tiles = [(0, y, SOUTH) for y in range(10)]
        dump = {"area": [-20, -20, 20, 30], "entities": [],
                "belt_lines": [{"id": "coal@0,0", "item": "coal", "tiles": tiles, "supply": 3.0,
                                "claims": {"x": 0.3}, "ok": True}]}
        blocked = {(0, y) for y in range(10)}
        with belt_lines.taps(True):
            branch = belt_lines.tap(dump, "coal", (8, 5), 0.5, blocked, last=EAST)
        self.assertIsNotNone(branch)
        splitter = branch[0]
        self.assertEqual((splitter.name, splitter.direction), ("splitter", SOUTH))
        self.assertEqual(splitter.line_op["op"], "tap")
        self.assertEqual(splitter.line_op["rate"], 0.5)
        here = footprint([splitter])
        self.assertEqual(len(here & blocked), 1)                    # it takes one tile of the line, and one beside
        self.assertEqual(branch[-1].direction, EAST)
        self.assertEqual((int(branch[-1].x), int(branch[-1].y)), (8, 5))
        # not without splitters, nor past what the line can spare
        self.assertIsNone(belt_lines.tap(dump, "coal", (8, 5), 0.5, blocked, last=EAST))
        with belt_lines.taps(True):
            self.assertIsNone(belt_lines.tap(dump, "coal", (8, 5), 2.8, blocked, last=EAST))
            self.assertIsNone(belt_lines.tap(dump, "iron-plate", (8, 5), 0.5, blocked, last=EAST))

    def test_no_tap_on_or_beside_a_splitter_already_there(self):
        tiles = [(0, y, SOUTH) for y in range(4)]
        dump = {"area": [-20, -20, 20, 30], "entities": [{"n": "splitter", "x": 0.0, "y": 1.5, "d": SOUTH}],
                "belt_lines": [{"id": "coal@0,0", "item": "coal", "tiles": tiles, "supply": 3.0, "claims": {},
                                "ok": True}]}
        with belt_lines.taps(True):
            self.assertIsNone(belt_lines.tap(dump, "coal", (8, 2), 0.5, {(0, y) for y in range(4)}))

    def test_a_broken_line_is_not_tapped(self):
        dump = {"area": [-20, -20, 20, 30], "entities": [],
                "belt_lines": [{"id": "coal@0,0", "item": "coal", "tiles": [(0, y, SOUTH) for y in range(10)],
                                "supply": 3.0, "claims": {}, "ok": False}]}
        self.assertEqual(belt_lines.lines(dump), [])

    def test_the_strategist_reads_each_line_s_use_and_spare(self):
        dump = {"belt_lines": [{"id": "coal@0,0", "item": "coal", "tiles": [(0, 0, SOUTH)], "supply": 3.0,
                                "claims": {"a": 1.0}, "ok": True}]}
        self.assertEqual(belt_lines.summary(dump),
                         ["coal line coal@0,0: 3.00/s on, 1.00/s taken by 1 connections, 2.00/s spare"])


def gears(name):
    from companion.modules import Module, Port, WEST
    return Module(name, entities=[Placed("assembling-machine-2", 2.5, 2.5)],
                  ports=[Port("iron-plate", "in", 0, 2, EAST, 2.0), Port("iron-gear-wheel", "out", 0, 4, WEST, 1.0)],
                  width=5, height=6, makes={"iron-gear-wheel": 1.0})


def two_belt_bus():
    """Iron on two belts side by side (a head balancer at row 0), gears tapping the east belt at rows 3 and 11 and
    the west one at row 7 (a re-balancer follows at row 16), and the gears' own lane."""
    from companion.bus import Bus
    bus = Bus(0, 0, 4, 40)
    bus.add_lane("iron-plate", belts=2, supply=12.0)
    bus.attach(gears("gears"))
    bus.attach(gears("gears-2"), side="west")
    bus.attach(gears("gears-3"))
    return bus


def measured(op, fills, minutes=30, rate=15.0, fills5=None, one_lane=False):
    """A line as the game exports it once sampled: `fills` by probe row (a trunk: by probe index), both lanes
    alike (or all on one lane: its fuller lane twice as full)."""
    probes = []
    for i, (x, y, *_) in enumerate(op["probes"]):
        key = y if op.get("kind") == "bus" else i
        if key in fills:
            f, f5 = fills[key], (fills5 or fills)[key]
            lane = 2 if one_lane else 1
            probes.append({"x": x, "y": y, "f": f, "f5": f5, "m": min(1, f * lane), "m5": min(1, f5 * lane)})
    return {**op, "ok": True, "measured": {"minutes": minutes, "rate": rate, "probes": probes}}


class BusLaneTests(unittest.TestCase):

    def test_each_bus_lane_is_a_line_with_its_group_its_share_and_its_taps(self):
        ops = {op["id"]: op for op in belt_lines.bus_lanes(two_belt_bus(), two_belt_bus().entities())}
        self.assertEqual(sorted(ops), ["iron-gear-wheel@4,6", "iron-plate@0,0", "iron-plate@1,0"])
        west, east = ops["iron-plate@0,0"], ops["iron-plate@1,0"]
        self.assertEqual({west["group"], east["group"]}, {"iron-plate@0,0"})
        self.assertEqual((west["kind"], west["tiles"]), ("bus", [[0, 1, SOUTH], [0, 39, SOUTH]]))   # its two ends
        self.assertEqual((west["supply"], east["supply"]), (6.0, 6.0))      # the head balancer halves the smelting's
        self.assertEqual(west["claims"], {"gears-2@0,7": 2.0})             # keyed by where its splitter stands
        self.assertEqual(east["claims"], {"gears@1,3": 2.0, "gears-3@1,11": 2.0})
        # probes on the same rows of both belts: the head, above every tap and the re-balancer, the tail
        self.assertEqual([p[1] for p in west["probes"]], [1, 2, 6, 10, 15, 39])
        self.assertEqual([p[1] for p in west["probes"]], [p[1] for p in east["probes"]])
        self.assertEqual(ops["iron-gear-wheel@4,6"]["supply"], 3.0)         # what the modules put on it

    def test_only_what_is_built_is_reported(self):
        bus = two_belt_bus()
        first = bus.segments()[0][1]                                        # the first module's stretch alone
        ops = belt_lines.bus_lanes(bus, first)
        east = next(op for op in ops if op["id"] == "iron-plate@1,0")
        self.assertLess(east["tiles"][1][1], 11)
        self.assertEqual(list(east["claims"]), ["gears@1,3"])
        self.assertEqual(belt_lines.bus_lanes(bus, []), [])

    def test_taps_and_outputs_count_once_built_whatever_the_order(self):
        # (Codex, L-1: the lane's rows alone counted the taps of modules not built yet)
        bus = two_belt_bus()
        parts = bus.segments()
        two = belt_lines.bus_lanes(bus, [e for _, part in parts[:2] for e in part])
        east = next(op for op in two if op["id"] == "iron-plate@1,0")
        self.assertEqual(list(east["claims"]), ["gears@1,3"])               # gears-3's splitter is not built
        both = belt_lines.bus_lanes(bus, parts[0][1] + parts[2][1])         # the first and third stretches
        east = next(op for op in both if op["id"] == "iron-plate@1,0")
        self.assertEqual(list(east["claims"]), ["gears@1,3"])               # (the third's tap is not joined up yet)

    def test_a_stretch_built_ahead_of_its_head_waits_until_joined_and_the_joined_part_keeps_its_supply(self):
        # (Codex, L-2: a stretch cut off from the head claimed its 6 a second; then a cut further down erased it all)
        bus = two_belt_bus()
        parts = bus.segments()
        third = belt_lines.bus_lanes(bus, parts[2][1])
        self.assertEqual([op for op in third if op["item"] == "iron-plate"], [])
        both = belt_lines.bus_lanes(bus, parts[0][1] + parts[2][1])
        iron = [op for op in both if op["item"] == "iron-plate"]
        self.assertEqual([op["supply"] for op in iron], [6.0, 6.0])
        self.assertTrue(all(op["tiles"][1][1] < parts[2][1][0].y for op in iron))   # (its joined part only)

    def test_every_tap_keeps_its_probe_on_a_long_bus(self):
        # (Codex, L-2: past 12 probe rows, the row before the last tap was dropped)
        from companion.bus import Bus
        bus = Bus(0, 0, 4, 250)
        bus.add_lane("iron-plate", belts=2, supply=15.0)
        for k in range(15):
            bus.attach(gears(f"g{k}"), side="east" if k % 2 == 0 else "west")
        for op in belt_lines.bus_lanes(bus, bus.entities()):
            rows = {p[1] for p in op["probes"]}
            self.assertLessEqual(len(rows), belt_lines.MAX_PROBES)
            if op["item"] == "iron-plate":
                for k in op["claims"]:
                    y = int(k.rsplit("@", 1)[1].split(",")[1])
                    self.assertTrue(rows & {y - 1, y - 2, y - 3}, (op["id"], k))

    def test_the_bus_s_lanes_are_never_tapped_from_outside(self):
        op = belt_lines.bus_lanes(two_belt_bus(), two_belt_bus().entities())[0]
        dump = {"area": [-20, -20, 40, 60], "entities": [], "belt_lines": [{**op, "item": "coal", "ok": True}]}
        with belt_lines.taps(True):
            self.assertIsNone(belt_lines.tap(dump, "coal", (10, 20), 0.1, set()))

    def test_the_tight_bus_of_run_33_reports_its_lanes_once_built(self):
        import copy
        from companion import bus_planner
        site, world = fixture()
        site = copy.deepcopy(site)
        for s in ("smelting-copper-plate", "bus-gears", "bus-green-circuits", "bus-mall-logistics",
                  "bus-mall-machines", "bus-red-science"):
            site["base_plan"].setdefault("done", {})[s] = 1
        plan = bus_planner.plan_stage(site, world)
        lanes = [op for st in plan["stages"] for op in st.get("line_ops", []) if op.get("kind") == "bus"]
        iron = next(op for op in lanes if op["item"] == "iron-plate")
        self.assertIn("gears@-571,-312", iron["claims"])
        self.assertTrue(all(len(op["probes"]) <= belt_lines.MAX_PROBES for op in lanes))
        self.assertEqual(iron["probes"][-1][1], iron["tiles"][1][1])        # the tail is a probe


class MeasureTests(unittest.TestCase):

    def trunk(self, supply=1.0, claims=None):
        tiles = [(0, y, SOUTH) for y in range(30)]
        built = [Placed("transport-belt", x + 0.5, y + 0.5, d) for x, y, d in tiles]
        belt_lines.record_line(built, "coal", supply, 0.5, (0, 30))
        op = dict(built[0].line_op)
        op.pop("key"), op.pop("rate")
        return {**op, "claims": claims if claims is not None else {"a": 0.5, "b": 0.7}}

    def test_what_a_trunk_spares_is_what_measurably_goes_on_less_its_claims(self):
        dump = {"belt_lines": [measured(self.trunk(supply=3.0, claims={"a": 0.5}), {0: 0.1, 5: 0.05})]}
        line = belt_lines.lines(dump)[0]
        self.assertAlmostEqual(line.supply_now, 1.5)                        # drills run out: 1.5 a second, not 3
        self.assertAlmostEqual(line.spare, 1.0)
        self.assertIn("all taken, its end 5% full", belt_lines.summary(dump)[0])
        young = measured(self.trunk(supply=3.0, claims={"a": 0.5}), {0: 0.1, 5: 0.05})
        for p in young["measured"]["probes"]:
            p["min"] = 4                                                    # (probes read 4 minutes: too few)
        self.assertFalse(belt_lines.lines({"belt_lines": [young]})[0].measured)

    def test_a_tap_marks_the_tile_before_it_to_be_sampled(self):
        tiles = [(0, y, SOUTH) for y in range(10)]
        dump = {"area": [-20, -20, 20, 30], "entities": [],
                "belt_lines": [{"id": "coal@0,0", "item": "coal", "tiles": tiles, "supply": 3.0, "claims": {},
                                "ok": True}]}
        with belt_lines.taps(True):
            branch = belt_lines.tap(dump, "coal", (8, 5), 0.5, {(0, y) for y in range(10)}, last=EAST)
        x, y = branch[0].line_op["at"]
        self.assertEqual(branch[0].line_op["probe"], [x, y - 1, SOUTH])   # (its way: read a few tiles back)


class SharedCoalTests(unittest.TestCase):
    """Run 33's map (2026-10-25): the first electric smelting lays a coal line with room to spare, the next taps it."""

    @classmethod
    def setUpClass(cls):
        cls.site, cls.world = fixture()

    def test_the_first_lays_a_line_with_room_and_the_next_taps_it_with_no_coal_drills_of_its_own(self):
        with belt_lines.taps(True):
            first = electric_start.plan_smelting(self.site, self.world, "electric-iron-smelting")
        ops = [op for st in first["stages"] for op in st.get("line_ops", [])]
        self.assertEqual([op["op"] for op in ops], ["line"])
        line = ops[0]
        self.assertGreaterEqual(line["supply"], belt_lines.TRUNK_RATE)   # room for the next
        self.assertAlmostEqual(line["rate"], 24 * 0.0225, places=3)       # its own array's 24 furnaces' coal
        with belt_lines.taps(True):
            second = electric_start.plan_smelting(self.site, standing(self.world, line), "electric-copper-smelting")
        ops = [op for st in second["stages"] for op in st.get("line_ops", [])]
        self.assertEqual([(op["op"], op["line"]) for op in ops], [("tap", line["id"])])
        protos = [e["prototype"] for e in second["entities"]]
        self.assertEqual(protos.count("splitter"), 1)
        ore = math.ceil(protos.count("stone-furnace") * 0.3125 / 0.5)
        self.assertEqual(protos.count("electric-mining-drill"), ore)      # its first part's copper ore only

    def test_a_line_with_no_spare_left_is_not_tapped(self):
        with belt_lines.taps(True):
            first = electric_start.plan_smelting(self.site, self.world, "electric-iron-smelting")
        line = next(op for st in first["stages"] for op in st.get("line_ops", []))
        full = standing(self.world, line, claims={"others": line["supply"]})
        with belt_lines.taps(True):
            second = electric_start.plan_smelting(self.site, full, "electric-copper-smelting")
        ops = [op["op"] for st in second["stages"] for op in st.get("line_ops", [])]
        self.assertEqual(ops, ["line"])                                   # a line of its own


class GameTests(unittest.TestCase):

    def runtime(self):
        import test_clearing
        return test_clearing.game()

    def test_the_game_records_a_line_once_its_stage_stands_and_a_tap_adds_its_claim(self):
        lua = self.runtime()
        lua.execute("""
            line = DS.line_op({op='line', id='coal@1,2', item='coal', key='a', rate=0.27, supply=3,
                               tiles={{1, 2, 8}, {1, 3, 8}, {1, 4, 8}}})
            tap = DS.line_op({op='tap', line='coal@1,2', key='b', rate=0.18})
            bad = DS.line_op({op='line', id='coal@1,2', item='coal', key='a', rate=0.27, tiles={{1, 'x', 8}}})
            DS.lines_apply(npc, {line}, 100)
            DS.lines_apply(npc, {tap}, 200)""")
        self.assertIsNone(lua.eval("bad"))
        claims = dict(lua.eval("npc.belt_lines['coal@1,2'].claims").items())
        self.assertEqual(claims, {"a": 0.27, "b": 0.18})
        self.assertEqual(lua.eval("#npc.belt_lines['coal@1,2'].tiles"), 3)

    def test_a_bus_lane_brings_all_its_taps_at_once_and_keeps_what_was_measured(self):
        lua = self.runtime()
        lua.execute("""
            lane = DS.line_op({op='line', id='iron-plate@1,0', item='iron-plate', kind='bus', group='iron-plate@0,0',
                               supply=6, claims={['gears@1,3']=2}, tiles={{1, 1, 8}, {1, 12, 8}},
                               probes={{1, 1}, {1, 2}, {1, 12}}})
            DS.lines_apply(npc, {lane}, 100)
            npc.belt_lines['iron-plate@1,0'].fill = {first=100}
            npc.belt_lines['iron-plate@1,0'].measured = {minutes=3}
            further = DS.line_op({op='line', id='iron-plate@1,0', item='iron-plate', kind='bus', group='iron-plate@0,0',
                                  supply=6, claims={['gears@1,3']=2, ['gears-3@1,11']=2}, tiles={{1, 1, 8}, {1, 39, 8}},
                                  probes={{1, 1}, {1, 2}, {1, 10}, {1, 39}}})
            DS.lines_apply(npc, {further}, 200)
            bad = DS.line_op({op='line', id='iron-plate@1,0', item='iron-plate', claims={x='lots'}, tiles={{1, 1, 8}}})
            on_bus = {valid=true, position={x=1.5, y=1.5}}""")
        line = lua.eval("npc.belt_lines['iron-plate@1,0']")
        self.assertEqual(dict(line.claims.items()), {"gears@1,3": 2, "gears-3@1,11": 2})
        self.assertEqual((line.kind, line.group, len(line.probes)), ("bus", "iron-plate@0,0", 4))
        self.assertEqual((line.fill.first, line.measured.minutes), (100, 3))
        self.assertIsNone(lua.eval("bad"))
        self.assertFalse(lua.eval("DS.on_line(npc, on_bus)"))              # (the bus planner retires its lanes)

    def test_a_belt_with_an_item_a_side_keeps_both(self):
        lua = self.runtime()
        lua.execute("""
            op = DS.line_op({op='line', id='mix@0,0', item='iron-plate', key='a', rate=0.5, supply=4,
                             tiles={{0, 0, 8}, {0, 9, 8}}, sides={left='iron-plate', right='iron-gear-wheel', up='x'},
                             side_supply={left=3, right='lots'}})
            DS.lines_apply(npc, {op}, 100)
            line = npc.belt_lines['mix@0,0']""")
        self.assertEqual(dict(lua.eval("line.sides").items()), {"left": "iron-plate", "right": "iron-gear-wheel"})
        self.assertEqual(dict(lua.eval("line.side_supply").items()), {"left": 3})

    def test_a_tap_adds_a_probe_before_it(self):
        lua = self.runtime()
        lua.execute("""
            DS.lines_apply(npc, {DS.line_op({op='line', id='coal@1,2', item='coal', key='a', rate=0.27, supply=3,
                                             tiles={{1, 2, 8}, {1, 3, 8}, {1, 4, 8}, {1, 5, 8}},
                                             probes={{1, 2}, {1, 5}}})}, 100)
            tap = DS.line_op({op='tap', line='coal@1,2', key='b', rate=0.18, at={1, 4}, probe={1, 3}})
            DS.lines_apply(npc, {tap, tap}, 200)""")
        probes = [tuple(p.values()) for p in lua.eval("npc.belt_lines['coal@1,2'].probes").values()]
        self.assertEqual(probes, [(1, 2), (1, 5), (1, 3)])                  # once

    def test_a_retired_build_leaves_the_line_it_laid_for_what_else_it_feeds(self):
        lua = self.runtime()
        lua.execute("""
            DS.lines_apply(npc, {DS.line_op({op='line', id='coal@1,2', item='coal', key='a', rate=0.27, supply=3,
                                             tiles={{1, 2, 8}, {1, 3, 8}}})}, 100)
            on = {valid=true, position={x=1.5, y=3.5}}
            off = {valid=true, position={x=5.5, y=3.5}}""")
        self.assertTrue(lua.eval("DS.on_line(npc, on)"))
        self.assertFalse(lua.eval("DS.on_line(npc, off)"))


if __name__ == "__main__":
    unittest.main()
