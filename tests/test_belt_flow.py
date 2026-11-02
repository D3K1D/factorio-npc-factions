"""Lane joins must not let spare fuel block an ore or product lane."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from companion import belt_flow, bus_planner, layout_planner
from companion.base_plan import Layout, Stage
from companion.bus import Bus
from companion.modules import EAST, NORTH, SOUTH, WEST, Placed
from companion.ratios import Book


def belt(x, y, direction=SOUTH, name="transport-belt", kind=None):
    return Placed(name, x + .5, y + .5, direction, kind=kind)


class LaneTests(unittest.TestCase):
    def flow(self, entities, seeds):
        return belt_flow.propagate(belt_flow.lanes(entities), seeds)[0]

    def test_straight_lines_keep_lanes_in_every_direction_and_tier(self):
        for direction, (dx, dy) in belt_flow.STEP.items():
            for name in belt_flow.BELTS:
                with self.subTest(direction=direction, name=name):
                    entities = [belt(dx * i, dy * i, direction, name) for i in range(3)]
                    items = self.flow(entities, {(0, 0, "left"): {"ore"}, (0, 0, "right"): {"coal"}})
                    self.assertEqual(items[2 * dx, 2 * dy, "left"], {"ore"})
                    self.assertEqual(items[2 * dx, 2 * dy, "right"], {"coal"})

    def test_curves_keep_lanes_in_both_turn_directions(self):
        for direction, (dx, dy) in belt_flow.STEP.items():
            for turn in (4, 12):
                out = (direction + turn) % 16
                ex, ey = belt_flow.STEP[out]
                entities = [belt(0, 0, direction), belt(dx, dy, out), belt(dx + ex, dy + ey, out)]
                items = self.flow(entities, {(0, 0, "left"): {"ore"}, (0, 0, "right"): {"coal"}})
                self.assertEqual(items[dx + ex, dy + ey, "left"], {"ore"})
                self.assertEqual(items[dx + ex, dy + ey, "right"], {"coal"})

    def test_a_side_feed_puts_both_lanes_on_the_near_lane(self):
        entities = [belt(0, -1), belt(0, 0), belt(-1, 0, EAST)]
        items = self.flow(entities, {(-1, 0, "left"): {"ore"}, (-1, 0, "right"): {"coal"}})
        self.assertEqual(items[0, 0, "right"], {"ore", "coal"})
        self.assertEqual(items[0, 0, "left"], set())

    def test_two_side_feeds_prevent_a_curve_even_without_a_rear_feed(self):
        entities = [belt(-1, 0, EAST), belt(0, 0), belt(1, 0, WEST)]
        items = self.flow(entities, {(-1, 0, "left"): {"ore"}, (1, 0, "left"): {"coal"}})
        self.assertEqual(items[0, 0, "right"], {"ore"})
        self.assertEqual(items[0, 0, "left"], {"coal"})

    def test_undergrounds_keep_lanes_and_do_not_spill_from_the_entrance(self):
        entities = [belt(0, 0, EAST, "underground-belt", "input"),
                    belt(5, 0, EAST, "underground-belt", "output"), belt(6, 0, EAST), belt(1, 0, SOUTH)]
        items = self.flow(entities, {(0, 0, "left"): {"ore"}, (0, 0, "right"): {"coal"}})
        self.assertEqual(items[6, 0, "left"], {"ore"})
        self.assertEqual(items[6, 0, "right"], {"coal"})
        self.assertEqual(items[1, 0, "left"], set())
        too_far = [entities[0], belt(6, 0, EAST, "underground-belt", "output")]
        self.assertEqual(self.flow(too_far, {(0, 0, "left"): {"ore"}})[6, 0, "left"], set())

    def test_a_splitter_keeps_lanes_on_both_outputs(self):
        for direction, (dx, dy) in belt_flow.STEP.items():
            split = Placed("splitter", 1 if dx == 0 else .5, .5 if dx == 0 else 1, direction)
            halves = belt_flow._tiles(split)
            entities = [split] + [belt(x + dx, y + dy, direction) for x, y in halves]
            items = self.flow(entities, {(*halves[0], "left"): {"ore"}, (*halves[1], "right"): {"coal"}})
            for x, y in halves:
                self.assertEqual(items[x + dx, y + dy, "left"], {"ore"})
                self.assertEqual(items[x + dx, y + dy, "right"], {"coal"})

    def test_world_belts_extend_a_planned_route_and_unknown_undergrounds_are_conservative(self):
        world = {"entities": [{"n": "underground-belt", "t": "underground-belt", "x": x + .5,
                               "y": .5, "d": EAST} for x in (1, 5)]}
        entities = [belt(0, 0, EAST), belt(6, 0, EAST)]
        items, _ = belt_flow.propagate(belt_flow.lanes(entities, world), {(0, 0, "left"): {"ore"}})
        self.assertEqual(items[6, 0, "left"], {"ore"})

    def test_world_undergrounds_pair_by_their_exported_end_and_old_mixing_elsewhere_holds_back_no_plan(self):
        # (the game now exports which end an underground is; a mixed lane already standing is not the plan's)
        world = {"entities": [{"n": "underground-belt", "t": "underground-belt", "x": 1.5, "y": .5, "d": EAST, "ug": "input"},
                              {"n": "underground-belt", "t": "underground-belt", "x": 4.5, "y": .5, "d": EAST, "ug": "output"}]}
        graph = belt_flow.lanes([belt(0, 0, EAST), belt(5, 0, EAST)], world)
        self.assertIn((4, 0, "left"), graph[1, 0, "left"])
        mixed = {"entities": [{"n": "transport-belt", "t": "transport-belt", "x": 50.5, "y": y + .5, "d": SOUTH}
                              for y in range(3)]
                 + [{"n": "transport-belt", "t": "transport-belt", "x": 49.5, "y": 1.5, "d": EAST},
                    {"n": "electric-mining-drill", "t": "mining-drill", "x": 50.5, "y": -2.5, "d": SOUTH},
                    {"n": "electric-mining-drill", "t": "mining-drill", "x": 46.5, "y": 1.5, "d": EAST}],
                 "resources": {"iron-ore": [[y, 44, 52] for y in range(-5, 0)], "coal": [[y, 44, 48] for y in range(0, 4)]}}
        self.assertEqual(belt_flow.check([belt(0, 0, EAST), belt(1, 0, EAST)], mixed), [])

    def test_fixed_point_on_a_loop_keeps_the_original_join_witness(self):
        a, b, c = (0, 0, "left"), (1, 0, "left"), (2, 0, "left")
        items, witnesses = belt_flow.propagate({a: {b}, b: {c}, c: {a}}, {a: {"ore"}, b: {"coal"}})
        self.assertTrue(all(value == {"ore", "coal"} for value in items.values()))
        self.assertEqual(witnesses[c].tile, witnesses[b].tile)
        self.assertEqual(witnesses[b].source, (0, 0))


class SourceTests(unittest.TestCase):
    def test_drills_drop_on_the_side_of_the_drop_point(self):
        world = {"resources": {"iron-ore": [[0, -4, 4]], "copper-ore": [[2, -1, -1]],
                                "crude-oil": [[0, -1, -1]]}}
        drill = Placed("electric-mining-drill", -1.5, .5, EAST)
        entities = [drill, belt(0, 0)]
        self.assertEqual(belt_flow.sources(entities, world), {(0, 0, "right"): {"iron-ore", "copper-ore"}})
        drill.x, drill.direction = 2.5, WEST
        self.assertEqual(belt_flow.sources(entities, world), {(0, 0, "left"): {"iron-ore"}})

    def test_a_centred_drill_drop_seeds_both_lanes(self):
        entities = [Placed("electric-mining-drill", .5, -1.5, SOUTH), belt(0, 0)]
        seeds = belt_flow.sources(entities, {"resources": {"coal": [[-2, 0, 0]]}})
        self.assertEqual(seeds, {(0, 0, side): {"coal"} for side in belt_flow.SIDES})

    def test_burner_drills_mine_only_their_two_by_two_area(self):
        entities = [Placed("burner-mining-drill", 0, 0, EAST), belt(1, 0)]
        seeds = belt_flow.sources(entities, {"resources": {"coal": [[0, 0, 0]], "stone": [[1, 0, 0]]}})
        self.assertEqual(seeds, {(1, 0, "right"): {"coal"}})

    def test_inserters_drop_a_known_machine_product_on_the_far_lane(self):
        for name, size in (("assembling-machine-2", 3), ("stone-furnace", 2)):
            entities = [Placed(name, -2, .5, recipe="iron-gear-wheel"),
                        Placed("inserter", -.5, .5, WEST), belt(0, 0)]
            self.assertEqual(belt_flow.sources(entities), {(0, 0, "left"): {"iron-gear-wheel"}})
            entities[0].recipe = None
            self.assertEqual(belt_flow.sources(entities), {})

    def test_unknown_recipes_and_chests_do_not_invent_sources(self):
        entities = [Placed("iron-chest", -1.5, .5, recipe="coal"),
                    Placed("inserter", -.5, .5, WEST), belt(0, 0)]
        self.assertEqual(belt_flow.sources(entities), {})

    def test_existing_machine_recipes_use_the_world_dump_recipe_key(self):
        world = {"entities": [{"n": "assembling-machine-1", "t": "assembling-machine", "x": -2.5,
                                "y": .5, "r": "copper-cable"},
                               {"n": "inserter", "t": "inserter", "x": -.5, "y": .5, "d": WEST}]}
        self.assertEqual(belt_flow.sources([belt(0, 0)], world), {(0, 0, "left"): {"copper-cable"}})

    def test_an_inserter_parallel_to_a_belt_drops_on_its_right_lane(self):
        entities = [Placed("assembling-machine-2", .5, -2.5, recipe="iron-gear-wheel"),
                    Placed("inserter", .5, -.5, NORTH), belt(0, 0)]
        self.assertEqual(belt_flow.sources(entities), {(0, 0, "right"): {"iron-gear-wheel"}})


def head(spill=False):
    # Both sides feed the head: ore from west, coal from east.
    entities = [belt(0, y) for y in range(4)]
    entities += [belt(-1, 0, EAST), belt(1, 0, WEST), belt(2, 0, WEST),
                 Placed("electric-mining-drill", -2.5, .5, EAST),
                 Placed("electric-mining-drill", 4.5, .5, WEST)]
    if spill:
        # The westward ore route crosses the feed's open tail, then returns to its head.
        entities += [belt(x, 4, WEST) for x in range(-2, 2)] + [belt(-3, 4, NORTH)]
        entities += [belt(-3, y, NORTH) for y in range(1, 4)]
        entities += [belt(-3, 0, EAST), belt(-2, 0, EAST),
                     Placed("electric-mining-drill", 3.5, 4.5, WEST)]
        entities = [e for e in entities if not (e.name == "electric-mining-drill" and e.x == -2.5)]
    world = {"resources": {"copper-ore": [[4, 4, 4]] if spill else [[0, -4, -4]], "coal": [[0, 5, 5]]}}
    return entities, world


class SafetyTests(unittest.TestCase):
    def test_correct_smelting_head_with_a_free_tail_passes(self):
        entities, world = head()
        self.assertEqual(belt_flow.check(entities, world), [])
        items, _ = belt_flow.propagate(belt_flow.lanes(entities), belt_flow.sources(entities, world))
        self.assertEqual(items[0, 3, "right"], {"copper-ore"})
        self.assertEqual(items[0, 3, "left"], {"coal"})

    def test_run_28_tail_spills_coal_back_into_the_ore_lane_with_a_witness(self):
        entities, world = head(spill=True)
        problems = belt_flow.check(entities, world)
        self.assertTrue(problems)
        self.assertTrue(any("coal, copper-ore" in p and "join at" in p and "from belt" in p for p in problems))
        items, witnesses = belt_flow.propagate(belt_flow.lanes(entities), belt_flow.sources(entities, world))
        self.assertEqual(items[0, 0, "right"], {"coal", "copper-ore"})
        self.assertIn(witnesses[0, 0, "right"].tile, ((0, 4), (0, 0)))

    def test_stage_checks_include_previous_stages_before_serializing(self):
        entities, world = head(spill=True)
        layout = Layout(Bus(0, 0, 2, 20), [Stage("old", entities[:-1]), Stage("new", entities[-1:])])
        site = {"base_plan": {"stages": ["old", "new"], "done": {"old": 1},
                              "x": 0, "y": 0, "columns": 2, "spm": 15}}
        with patch.object(bus_planner, "plan_bus_base", return_value=layout):
            with self.assertRaisesRegex(layout_planner.PlanError, "join at"):
                bus_planner.plan_stage(site, world, Book.vanilla())


class AnchorTests(unittest.TestCase):
    """A belt fed from both sides and not from behind turns into a curve when one input is lost (run 32: biters
    took the iron ore piece and coal filled the ore lane); an empty belt behind it keeps both sideloads."""

    def test_a_two_sided_head_is_a_junction_and_gets_an_anchor_behind_it(self):
        entities, world = head()
        self.assertEqual(belt_flow.junctions(entities, world), [(0, 0)])
        added, stuck = belt_flow.anchors(entities, world)
        self.assertEqual(stuck, [])
        self.assertEqual([(a.name, a.x, a.y, a.direction) for a in added], [("transport-belt", .5, -.5, SOUTH)])
        self.assertEqual(belt_flow.junctions(entities + added, world), [])
        self.assertEqual(belt_flow.check(entities + added, world), [])

    def test_with_the_anchor_losing_the_ore_piece_keeps_coal_on_its_own_lane(self):
        entities, world = head()
        added, _ = belt_flow.anchors(entities, world)
        lost = [e for e in entities if not (e.x == -.5 and e.y == .5)]   # the ore piece at (-1, 0) is gone
        seeds = {(1, 0, "right"): {"coal"}}
        bare = belt_flow.propagate(belt_flow.lanes(lost), seeds)[0]
        self.assertEqual(bare[0, 3, "right"], {"coal"})          # a curve: coal on the ore lane
        kept = belt_flow.propagate(belt_flow.lanes(lost + added), seeds)[0]
        self.assertEqual(kept[0, 3, "right"], set())
        self.assertEqual(kept[0, 3, "left"], {"coal"})

    def test_plain_curves_sideloads_and_straight_lines_need_no_anchor(self):
        curve = [belt(-1, 0, EAST), belt(0, 0), belt(0, 1)]
        side = [belt(0, -1), belt(0, 0), belt(0, 1), belt(-1, 0, EAST)]
        line = [belt(0, y) for y in range(4)]
        for entities in (curve, side, line):
            self.assertEqual(belt_flow.junctions(entities), [])

    def test_no_anchor_where_the_tile_behind_is_kept_fed_or_dropped_on(self):
        entities, world = head()
        self.assertEqual(belt_flow.anchors(entities, world, blocked={(0, -1)}), ([], [(0, 0)]))
        fed = entities + [belt(-1, -1, EAST)]   # a belt running into the tile behind
        self.assertEqual(belt_flow.anchors(fed, world)[1], [(0, 0)])
        dropped = entities + [Placed("inserter", .5, -1.5, NORTH)]   # picks at (0, -3), drops at (0, -1)
        self.assertEqual(belt_flow.anchors(dropped, world)[1], [(0, 0)])
        world_built = {**world, "entities": [{"n": "wooden-chest", "t": "container", "x": .5, "y": -.5}]}
        self.assertEqual(belt_flow.anchors(entities, world_built)[1], [(0, 0)])
        trees = {**world, "entities": [{"n": "tree-01", "t": "tree", "x": .5, "y": -.5}]}
        self.assertEqual(len(belt_flow.anchors(entities, trees)[0]), 1)   # (trees are cleared)

    def test_a_built_junction_whose_anchor_stands_needs_none(self):
        entities, world = head()
        added, _ = belt_flow.anchors(entities, world)
        built = {**world, "entities": [{"n": "transport-belt", "t": "transport-belt", "x": a.x, "y": a.y,
                                        "d": a.direction} for a in added]}
        self.assertEqual(belt_flow.anchors(entities, built), ([], []))

    def test_the_bus_stage_carries_anchors_for_its_own_and_earlier_junctions_and_lane_contracts(self):
        entities, world = head()
        layout = Layout(Bus(0, 0, 2, 20), [Stage("old", entities[:3]), Stage("new", entities[3:])])
        site = {"base_plan": {"stages": ["old", "new"], "done": {"old": 1}, "x": 0, "y": 0, "columns": 2,
                              "spm": 15}}
        with patch.object(bus_planner, "plan_bus_base", return_value=layout):
            plan = bus_planner.plan_stage(site, world, Book.vanilla())
        anchors = [e for e in plan["entities"] if e["position"] == {"x": .5, "y": -.5}]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["direction"], SOUTH)
        head_lanes = next(e["lanes"] for e in plan["entities"] if e["position"] == {"x": .5, "y": 3.5})
        self.assertEqual(head_lanes, {"left": ["coal"], "right": ["copper-ore"]})


class ContractTests(unittest.TestCase):
    def test_lanes_with_known_sources_get_contracts_and_unknown_ones_none(self):
        entities, world = head()
        rows = belt_flow.contracts(entities, world)
        self.assertEqual(rows[0, 2], {"left": ["coal"], "right": ["copper-ore"]})
        self.assertEqual(rows[-1, 0], {"left": ["copper-ore"], "right": ["copper-ore"]})
        chest_fed = [Placed("iron-chest", -1.5, .5), Placed("inserter", -.5, .5, WEST), belt(0, 0), belt(0, 1)]
        self.assertEqual(belt_flow.contracts(chest_fed), {})


class LayoutTests(unittest.TestCase):
    def assert_stages(self, layout, world):
        entities = []
        for stage in layout.stages:
            entities.extend(stage.entities)
            with self.subTest(stage=stage.name):
                problems = belt_flow.check(entities, world)
                self.assertFalse(problems, problems[:1])

    def test_every_stage_of_the_existing_red_green_blue_and_growth_fixtures(self):
        from companion.base_plan import plan_bus_base
        from companion.style import style_for
        world = json.loads((Path(__file__).parent / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        packs = ("automation-science-pack", "logistic-science-pack")
        for extra in ((), ("chemical-science-pack",), ("growth-1-2",)):
            with self.subTest(extra=extra):
                layout = plan_bus_base(world, Book.vanilla(), science_per_minute=15, columns=21,
                                       packs=packs, extra=extra, base_labs=not extra, style=style_for({}))
                self.assert_stages(layout, world)

    def test_every_stage_of_a_factory_plan_fixture(self):
        from companion import factory_plan
        from test_factory_plan import BLUE, WORLD
        site = {**BLUE, "factory_plan": factory_plan.default_plan(BLUE)}
        first = bus_planner.plan_stage(site, WORLD, Book.vanilla())
        saved = {**first["base_plan"], "done": {first["base_plan"]["stage"]: 1}}
        self.assert_stages(bus_planner.replay({**site, "base_plan": saved}, WORLD, Book.vanilla()), WORLD)

    def test_a_red_green_bus_extended_for_blue_keeps_old_module_outlets_clear(self):
        from test_modules import BusExtensionTests
        BusExtensionTests.setUpClass()
        world, site = BusExtensionTests.world, BusExtensionTests.SITE
        first = bus_planner.plan_stage({**site, "researched": ["logistic-science-pack"]}, world, Book.vanilla())
        saved = {**first["base_plan"], "done": {s: 1 for s in first["base_plan"]["stages"]}}
        more = bus_planner.plan_stage({**site, "base_plan": saved}, world, Book.vanilla())
        saved.update(stages=more["base_plan"]["stages"], extra=["chemical-science-pack"])
        self.assert_stages(bus_planner.replay({**site, "base_plan": saved}, world, Book.vanilla()), world)

    def test_rocket_supply_keeps_stone_and_coal_apart(self):
        from test_modules import RocketTests
        world = json.loads((Path(__file__).parent / "fixtures" / "k-blue-map-256.json").read_text(encoding="utf-8"))
        world["resources"]["crude-oil"] += [[14, 96, 96], [-2, 100, 100]]
        first = bus_planner.plan_stage(RocketTests.SITE, world, Book.vanilla(True))
        site = {**RocketTests.SITE, "base_plan": first["base_plan"]}
        self.assert_stages(bus_planner.replay(site, world, Book.vanilla(True)), world)

    def test_styles_do_not_collect_ore_from_a_burner_beside_a_replaced_furnace(self):
        from companion.base_plan import plan_bus_base
        from companion.style import Style
        from test_factory_plan import WORLD
        for style in (Style(), Style(heading=3.14, spread=1, first_side="west", spare=1.25)):
            layout = plan_bus_base(WORLD, Book.vanilla(), science_per_minute=15,
                                   packs=("automation-science-pack",), columns=8, style=style, ahead=False)
            self.assert_stages(layout, WORLD)

    def test_a_steel_block_does_not_overlap_its_fields_ore_outlet(self):
        from companion.base_plan import plan_bus_base
        from test_factory_plan import WORLD
        world = {**WORLD, "entities": [e for e in WORLD["entities"] if e.get("r") != "sulfur"]}
        layout = plan_bus_base(world, Book.vanilla(), science_per_minute=15, columns=19,
                               packs=("automation-science-pack", "logistic-science-pack"),
                               extra=("chemical-science-pack",))
        self.assert_stages(layout, world)

    def test_rebuilt_routes_with_unknown_world_underground_subtypes_are_conservative(self):
        from companion.base_plan import plan_bus_base
        from test_factory_plan import WORLD
        kwargs = dict(science_per_minute=15, packs=("automation-science-pack", "logistic-science-pack"), columns=12)
        first = plan_bus_base(WORLD, Book.vanilla(), **kwargs)
        built = [e for s in first.stages if s.name in ("power", "smelting-iron-plate", "smelting-copper-plate")
                 for e in s.entities]
        sizes = {"electric-mining-drill": 3, "stone-furnace": 2}
        rows = [{"n": e.name, "t": e.name, "x": e.x, "y": e.y, "d": e.direction,
                 "w": sizes.get(e.name, 1), "h": sizes.get(e.name, 1), "f": WORLD["force"]} for e in built]
        world = {**WORLD, "entities": WORLD["entities"] + rows}
        again = plan_bus_base(world, Book.vanilla(), **kwargs, origin=(first.bus.x, first.bus.y),
                              ahead=first.ahead, sites=first.sites)
        self.assertTrue(belt_flow.check(again.entities(), world))
        for row, entity in zip(rows, built):
            row["kind"] = entity.kind
        self.assert_stages(again, world)


if __name__ == "__main__":
    unittest.main()
