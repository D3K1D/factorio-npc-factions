"""Where the blocks of the base stand (companion/footprint.py, base_memory.lua's parts) and what goes round them: the
wall ring and the defense brain's areas, on run 55's real base (tests/fixtures/bw-ring-run55.json: its first ring was
256 by 263 tiles, its south side on the plot's edge, round far fields and a far power plant)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from lupa.lua52 import LuaRuntime, lua_type

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import defense_brain, footprint, perimeter  # noqa: E402
from test_defense_plans_perimeter import site, world  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "bw-ring-run55.json").read_text(encoding="utf-8"))


def _py(value):
    """A Lua table as the game's JSON export gives it: a sequence as a list, else a dict."""
    if lua_type(value) != "table":
        return value
    keys = list(value.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [_py(value[k]) for k in sorted(keys)]
    return {str(k): _py(v) for k, v in value.items()}


def exported(plans, origin) -> list[dict]:
    """The plans' blocks as the game exports them (base_memory.lua BaseMemory.blocks), from their machines' boxes."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
    lua.execute("""
        defines = {entity_status={working=1}}
        M = assert(load(source))()
        surface = {find_entities_filtered=function() return {} end}
        history = {}
        function plan(id, purpose)
            local p = {plan_id=id, purpose=purpose, order={}, entities={}}
            history[#history + 1] = p
            return p
        end
        function add(p, name, kind, x0, y0, x1, y1)
            local k = 'e' .. (#p.order + 1)
            p.order[#p.order + 1] = k
            p.entities[k] = {entity={valid=true, name=name, type=kind, status=1, surface=surface,
                position={x=(x0 + x1) / 2, y=(y0 + y1) / 2}, prototype={mining_drill_radius=2.49},
                bounding_box={left_top={x=x0, y=y0}, right_bottom={x=x1, y=y1}},
                get_recipe=function() return nil end}}
        end
    """)
    g = lua.globals()
    for p in plans:
        handle = g.plan(p["id"], p["purpose"])
        for m in p["machines"]:
            g.add(handle, *m)
    return _py(lua.eval("M.blocks({design_history=history, design={status='complete'}}, {x=%d, y=%d})"
                        % (origin["x"], origin["y"])))


def run55(**extra) -> dict:
    return site(origin=FIXTURE["origin"], team=FIXTURE["team"], base=exported(FIXTURE["plans"], FIXTURE["origin"]),
                **extra)


def _inside(box, ring) -> bool:
    return ring[0] <= box[0] and ring[1] <= box[1] and box[2] <= ring[2] and box[3] <= ring[3]


class Run55Tests(unittest.TestCase):
    def test_a_block_with_drills_at_a_far_patch_stands_in_two_places(self):
        base = run55()["base"]
        brick = next(b for b in base if b["purpose"] == "electric-brick-smelting")
        self.assertEqual(brick["machines_box"], [-1, 60, 142, 208])     # (one box round both: 143 by 148)
        near, far = brick["parts"]
        self.assertEqual(near["machines"], {"electric-mining-drill": 5, "stone-furnace": 4})
        self.assertEqual((far["box"], far["machines"]), ([135, 205, 142, 208], {"electric-mining-drill": 2}))
        power = next(b for b in base if b["purpose"] == "bus-base")
        self.assertEqual(len(power["parts"]), 1)                         # (its boilers and their coal drills: one place)

    def test_the_first_ring_goes_round_the_factory_not_its_far_fields_and_power_plant(self):
        s = run55()
        ox, oy = FIXTURE["origin"]["x"], FIXTURE["origin"]["y"]
        self.assertEqual(perimeter.core(s), (-59 + ox, -15 + oy, 64 + ox, 85 + oy))   # its machines: 123 by 100
        ring, gen = perimeter.ring_due(s)
        self.assertEqual((ring, gen), ((-83 + ox, -39 + oy, 88 + ox, 109 + oy), 1))   # RING_MARGIN round it
        plot = perimeter.plot_box(s)
        self.assertTrue(all(abs(a - b) > perimeter.RING_SNAP for a, b in zip(ring, plot)))   # no side on the plot's edge
        walls = sum(e.hi - e.lo + 1 for e in perimeter.ring_edges(s, ring))
        self.assertLess(walls, sum(e.hi - e.lo + 1 for e in perimeter.plot_edges(s)))   # (run 55's ring: longer)
        power = next(b for b in s["base"] if b["purpose"] == "bus-base")["parts"][0]["box"]
        self.assertFalse(_inside((power[0] + ox, power[1] + oy, power[2] + ox, power[3] + oy), ring))
        plan = perimeter.plan_wall(s, world())
        self.assertEqual(plan["keep"]["ring"], list(ring))
        # the starter turrets' vital buildings: the same main part, its drills alone left out
        vital = perimeter.core_box(s)
        self.assertTrue(_inside(vital, perimeter.core(s)))

    def test_the_defense_brain_holds_a_far_power_plant_as_an_area_of_its_own(self):
        found = defense_brain.areas(run55())
        power = next(a for a in found if "bus-base" in a.purposes)
        lab = next(a for a in found if "research-lab" in a.purposes)
        self.assertIsNot(power, lab)
        self.assertLess(power.box[2] - power.box[0], 24)                  # the plant and its coal drills, not the base
        self.assertIn("electric-brick-smelting", power.purposes)           # (the brick block's far drills stand there)
        self.assertIn("electric-brick-smelting", lab.purposes)             # and its furnaces by the factory
        self.assertGreater(lab.value, power.value)                         # (labs and assemblers most at stake)


class FootprintTests(unittest.TestCase):
    def test_a_block_from_an_older_game_is_one_part(self):
        machines = {"stone-furnace": 2}
        parts = [{"box": [0, 0, 2, 2], "machines": machines}, {"box": [90, 0, 93, 3], "machines": {"lab": 1}}]
        self.assertEqual(len(footprint.parts({"parts": parts, "machines_box": [0, 0, 93, 3]})), 2)
        self.assertEqual(footprint.parts({"machines_box": [0, 0, 9, 9], "box": [0, 0, 40, 40], "machines": machines}),
                         [((0.0, 0.0, 9.0, 9.0), machines)])
        self.assertEqual(footprint.parts({"box": [0, 0, 40, 40], "machines": machines}),
                         [((0.0, 0.0, 40.0, 40.0), machines)])
        self.assertEqual(footprint.parts({"box": [0, 0, 40, 40], "machines": {}}), [])   # (belts and poles alone)

    def test_boxes_near_each_other_group_through_each_other(self):
        items = [((0, 0, 4, 4), "a"), ((100, 0, 104, 4), "far"), ((10, 0, 14, 4), "b"), ((20, 0, 24, 4), "c")]
        groups = footprint.cluster(items, 6)
        self.assertEqual(groups, [((0.0, 0.0, 24.0, 4.0), ["a", "b", "c"]), ((100.0, 0.0, 104.0, 4.0), ["far"])])
        self.assertEqual(len(footprint.cluster(items, 5)), 4)
        self.assertEqual(footprint.stake({"lab": 1, "electric-mining-drill": 3}), 13)
        self.assertEqual(footprint.stake({}), 1)


if __name__ == "__main__":
    unittest.main()


class LivePolesTests(unittest.TestCase):
    def test_only_poles_a_generator_feeds_count_as_power(self):
        from companion.power import live_poles
        dump = {"force": "f", "live_nets": [1], "entities": [
            {"t": "electric-pole", "f": "f", "x": 0.5, "y": 0.5, "net": 1},
            {"t": "electric-pole", "f": "f", "x": 9.5, "y": 0.5, "net": 5},     # (no generator on it: F-21)
            {"t": "electric-pole", "f": "f", "x": 3.5, "y": 3.5}]}             # (net not said: counted)
        self.assertEqual(live_poles(dump), [(0.5, 0.5), (3.5, 3.5)])
        self.assertEqual(len(live_poles({**dump, "live_nets": []})), 3)      # (the map does not say: all)


class CoverTests(unittest.TestCase):
    def test_a_machine_outside_every_pole_s_area_gets_a_pole_beside_it(self):
        from companion.modules import Placed
        from companion.power import cover
        planned = [Placed("inserter", 0.5, 0.5, 0), Placed("small-electric-pole", 20.5, 0.5),
                   Placed("inserter", 21.5, 0.5, 0)]
        poles = cover(planned, {"force": "f", "entities": []}, set())
        self.assertEqual(len(poles), 1)                                     # (only the far one, by the first)
        self.assertLessEqual(abs(poles[0].x - 0.5) + abs(poles[0].y - 0.5), 2.0)
        live = {"force": "f", "live_nets": [1], "entities": [{"t": "electric-pole", "f": "f", "n": "small-electric-pole",
                                                               "x": 1.5, "y": 1.5, "net": 1}]}
        self.assertEqual(cover(planned, live, set()), [])                  # (a live pole covers it)
