"""Inserter throughput (Ledger's L-4, companion/inserters.py): what each inserter moves by hand size, source and target,
one shared check of a plan's machines against their inserters, and the measured inserter-bound signal (ledger.py)."""
import unittest
from pathlib import Path

from companion import inserters, ledger
from companion.modules import NORTH, Placed, SOUTH

ROOT = Path(__file__).resolve().parents[1]
CAN = {"recipes": [{"name": n} for n in ("inserter", "fast-inserter", "bulk-inserter", "iron-gear-wheel")]}


class RateTests(unittest.TestCase):

    def test_a_swing_is_a_full_turn_and_the_hand_grows_with_research(self):
        self.assertAlmostEqual(inserters.rate("inserter"), 0.84, places=2)          # the game's 0.83 chest to chest
        self.assertAlmostEqual(inserters.rate("fast-inserter"), 2.4, places=2)
        researched = {"inserter_bonus": {"stack": 1, "bulk": 2}}
        self.assertAlmostEqual(inserters.hand("inserter", researched), 2)
        self.assertAlmostEqual(inserters.hand("bulk-inserter", researched), 4)       # (its own 1 and the bulk 2)
        self.assertAlmostEqual(inserters.rate("inserter", researched), 1.68, places=2)

    def test_from_a_belt_a_bigger_hand_waits_for_each_item_more_on_one_lane(self):
        site = {"inserter_bonus": {"stack": 2}}
        chest = inserters.rate("fast-inserter", site)
        both = inserters.rate("fast-inserter", site, source="belt", lanes=2)
        one = inserters.rate("fast-inserter", site, source="belt", lanes=1)
        self.assertGreater(chest, both)
        self.assertGreater(both, one)
        self.assertAlmostEqual(inserters.rate("fast-inserter", source="belt", lanes=1),
                               inserters.rate("fast-inserter"))                    # (a hand of 1 waits for nothing)

    def test_a_modded_inserter_from_the_game_s_facts(self):
        site = {"game_facts": {"entities": {"bob-turbo-inserter": {"kind": "inserter", "rotation": 0.06,
                                                                   "stack_bonus": 0}}}}
        self.assertAlmostEqual(inserters.rate("bob-turbo-inserter", site), 3.6, places=2)


class PortTests(unittest.TestCase):

    def test_short_sides_get_more_then_faster_then_a_slower_rate(self):
        self.assertTrue(inserters.port(0.5, "inserter", 1, CAN).ok)
        more = inserters.port(1.5, "inserter", 1, CAN)
        self.assertEqual((more.fix, more.to, more.to_count), ("add", "inserter", 2))
        faster = inserters.port(6.0, "inserter", 1, CAN)
        self.assertEqual((faster.fix, faster.to, faster.to_count), ("upgrade", "fast-inserter", 3))
        slower = inserters.port(30.0, "inserter", 1, CAN)
        self.assertEqual(slower.fix, "slower")
        self.assertLess(slower.factor, 1.0)


class PlanTests(unittest.TestCase):

    def gears(self, speed_recipe="iron-gear-wheel", arm="inserter"):
        # an assembler at (0,0) making gears, one inserter feeding it from a chest to the north, one taking to the south
        return [Placed("assembling-machine-2", 0.5, 0.5, NORTH, speed_recipe),
                Placed("iron-chest", 0.5, -2.5), Placed(arm, 0.5, -1.5, NORTH),
                Placed(arm, 0.5, 2.5, NORTH), Placed("iron-chest", 0.5, 3.5)]

    def test_a_plan_s_machines_are_judged_against_their_inserters(self):
        # gears in an assembler 2: 0.75 / 0.5 s = 1.5 crafts, 3 plates in, 1.5 gears out; one yellow inserter moves 0.84
        short = inserters.plan(self.gears(), CAN)
        sides = {("in" if " in" in c.where else "out"): c for c in short}
        self.assertEqual(sorted(sides), ["in", "out"])
        self.assertEqual(sides["in"].fix, "upgrade")
        self.assertAlmostEqual(sides["in"].need, 3.0)
        self.assertEqual(sides["out"].fix, "add")
        self.assertEqual(inserters.plan(self.gears(arm="fast-inserter"), {**CAN, "inserter_bonus": {"stack": 1}}), [])


class PlanFixTests(unittest.TestCase):
    """Codex, L-4 round 1."""

    def test_fluids_come_by_pipe_not_inserter(self):
        site = {**CAN, "recipes": CAN["recipes"] + [{"name": "acid-thing", "energy": 1, "category": "crafting-with-fluid",
                "ingredients": [{"name": "sulfuric-acid", "amount": 100, "type": "fluid"},
                                {"name": "iron-plate", "amount": 1, "type": "item"}],
                "products": [{"name": "acid-thing", "amount": 1, "type": "item"}]}]}
        plan = [Placed("assembling-machine-2", 0.5, 0.5, NORTH, "acid-thing"), Placed("iron-chest", 0.5, -2.5),
                Placed("inserter", 0.5, -1.5, NORTH)]
        self.assertEqual(inserters.plan(plan, site), [])                     # (0.75 plates a second: one does it)

    def test_an_item_on_one_side_of_a_belt_comes_half_as_often(self):
        site = {**CAN, "inserter_bonus": {"stack": 2}}
        base = [{"prototype": "assembling-machine-2", "position": {"x": 0.5, "y": 0.5}, "recipe": "iron-gear-wheel"},
                {"prototype": "inserter", "position": {"x": 0.5, "y": -1.5}, "direction": NORTH},
                {"prototype": "transport-belt", "position": {"x": 0.5, "y": -2.5}, "direction": 4}]
        both = inserters.plan(base, site)
        mixed = [*base[:2], {**base[2], "lanes": {"left": ["iron-plate"], "right": ["copper-plate"]}}]
        one = inserters.plan(mixed, site)
        have = lambda checks: next((c.have for c in checks if " in" in c.where), None)
        self.assertLess(have(one), have(both))

    def test_an_imported_inserter_s_own_pickup_and_drop_are_used(self):
        # (it stands west of the machine but its settings point it east into it, not by its direction)
        plan = [{"prototype": "assembling-machine-2", "position": {"x": 0.5, "y": 0.5}, "recipe": "iron-gear-wheel"},
                {"prototype": "inserter", "position": {"x": -1.5, "y": 0.5}, "direction": NORTH,
                 "pickup_position": {"x": -1, "y": 0}, "drop_position": {"x": 1, "y": 0}},
                {"prototype": "iron-chest", "position": {"x": -2.5, "y": 0.5}}]
        sides = [("in" if " in" in c.where else "out") for c in inserters.plan(plan, CAN)]
        self.assertEqual(sides, ["in"])

    def test_a_stack_inserter_puts_down_stacks_no_taller_than_the_belt_allows(self):
        flat = {"game_facts": {"entities": {"stack-inserter": {"kind": "inserter", "rotation": 0.04, "stack_bonus": 3}},
                               "bonuses": {"belt_stack": 0}}}
        tall = {"game_facts": {**flat["game_facts"], "bonuses": {"belt_stack": 3}}}
        self.assertLess(inserters.rate("stack-inserter", flat, target="belt"),
                        inserters.rate("stack-inserter", tall, target="belt"))
        # (a hand of 6 onto stacks of 4: rounded down to one stack of 4 a swing, at most 4 x 2.4 a second; Codex)
        six = {"game_facts": {"entities": {"stack-inserter": {"kind": "inserter", "rotation": 0.04, "stack_bonus": 5}},
                              "bonuses": {"belt_stack": 3}}}
        self.assertLessEqual(inserters.rate("stack-inserter", six, target="belt"), 9.6 + 1e-9)

    def test_short_of_a_fluid_is_not_the_inserters(self):
        block = {"id": "plastic", "stage": "bus-plastic", "machines": {"chemical-plant": 4},
                 "measured": {"minutes": 40, "shares": {"starved": 0.5, "starved_fluid": 0.45},
                              "arms": {"in:working": 0.9}}}
        self.assertEqual(ledger.Ledger.from_site({"base": [block]}).inserter_bound(), [])

    def test_each_ingredient_against_the_arms_that_bring_it(self):
        # (Codex, L-4: a needs 1/s and b 0.5/s; each has its own yellow inserter of 0.84/s; together 1.68 covers 1.5)
        site = {**CAN, "recipes": CAN["recipes"] + [{"name": "ab", "energy": 1, "category": "crafting",
                "ingredients": [{"name": "a", "amount": 2}, {"name": "b", "amount": 1}],
                "products": [{"name": "ab", "amount": 1}]}]}
        plan = [{"prototype": "assembling-machine-1", "position": {"x": 0.5, "y": 0.5}, "recipe": "ab"},
                {"prototype": "inserter", "position": {"x": -0.5, "y": -1.5}, "direction": NORTH},
                {"prototype": "transport-belt", "position": {"x": -0.5, "y": -2.5}, "direction": 4,
                 "lanes": {"left": ["a"], "right": ["a"]}},
                {"prototype": "inserter", "position": {"x": 1.5, "y": -1.5}, "direction": NORTH},
                {"prototype": "transport-belt", "position": {"x": 1.5, "y": -2.5}, "direction": 4,
                 "lanes": {"left": ["b"], "right": ["b"]}}]
        short = inserters.plan(plan, site)
        self.assertEqual([c.where.rsplit(" ", 1)[1] for c in short], ["(a)"])
        mixed = [*plan[:2], {**plan[2], "lanes": {"left": ["a"], "right": ["b"]}}, *plan[3:]]   # (a/b, then b alone)
        self.assertEqual([c.where.rsplit(" ", 1)[1] for c in inserters.plan(mixed, site)], ["(a)"])

    def test_an_arm_bringing_two_items_shares_its_time_between_them(self):
        # (Codex, L-4: a and b by one arm from a mixed belt, c by two others; each needs 0.8/s, an arm moves 0.84)
        site = {**CAN, "recipes": CAN["recipes"] + [{"name": "abc", "energy": 1, "category": "crafting",
                "ingredients": [{"name": "a", "amount": 1}, {"name": "b", "amount": 1}, {"name": "c", "amount": 1}],
                "products": [{"name": "abc", "amount": 1}]}]}
        belt = lambda x, items: {"prototype": "transport-belt", "position": {"x": x, "y": -2.5}, "direction": 4,
                                 "lanes": {"left": items[:1], "right": items[-1:]}}
        arm = lambda x: {"prototype": "inserter", "position": {"x": x, "y": -1.5}, "direction": NORTH}
        plan = [{"prototype": "assembling-machine-1", "position": {"x": 0.5, "y": 0.5}, "recipe": "abc"},
                arm(-0.5), belt(-0.5, ["a", "b"]), arm(0.5), belt(0.5, ["c"]), arm(1.5), belt(1.5, ["c"])]
        short = inserters.plan(plan, {**site, "machines": {"assembling-machine-1": {"speed": 0.8}}})
        self.assertEqual(len(short), 1)
        self.assertIn("in (", short[0].where)
        self.assertNotIn("(c)", short[0].where)

    def test_a_mix_of_fast_and_slow_arms_short_of_the_need_is_said(self):
        # (Codex, L-4: 2.4 + 0.84 against 3.5 a second must not pass as enough)
        site = {**CAN, "recipes": CAN["recipes"] + [{"name": "big", "energy": 1, "category": "crafting",
                "ingredients": [{"name": "a", "amount": 7}], "products": [{"name": "big", "amount": 1}]}]}
        plan = [{"prototype": "assembling-machine-1", "position": {"x": 0.5, "y": 0.5}, "recipe": "big"},
                {"prototype": "fast-inserter", "position": {"x": -0.5, "y": -1.5}, "direction": NORTH},
                {"prototype": "iron-chest", "position": {"x": -0.5, "y": -2.5}},
                {"prototype": "inserter", "position": {"x": 0.5, "y": -1.5}, "direction": NORTH},
                {"prototype": "iron-chest", "position": {"x": 0.5, "y": -2.5}}]
        short = inserters.plan(plan, site)
        self.assertEqual(len(short), 1)
        self.assertFalse(short[0].ok)
        self.assertTrue(short[0].fix and short[0].why)

    def test_one_slow_arm_swapped_is_the_fix_not_a_slower_block(self):
        # (Codex, L-4: one fast and two yellow move 4.08 of 4.5 a second; one yellow swapped for a fast gives 5.64)
        site = {**CAN, "recipes": CAN["recipes"] + [{"name": "big", "energy": 1, "category": "crafting",
                "ingredients": [{"name": "a", "amount": 9}], "products": [{"name": "big", "amount": 1}]}]}
        chest = lambda x: {"prototype": "iron-chest", "position": {"x": x, "y": -2.5}}
        arm = lambda name, x: {"prototype": name, "position": {"x": x, "y": -1.5}, "direction": NORTH}
        plan = [{"prototype": "assembling-machine-1", "position": {"x": 0.5, "y": 0.5}, "recipe": "big"},
                arm("fast-inserter", -0.5), chest(-0.5), arm("inserter", 0.5), chest(0.5), arm("inserter", 1.5),
                chest(1.5)]
        short = inserters.plan(plan, site)
        self.assertEqual([(c.fix, c.to, c.to_count) for c in short], [("upgrade", "fast-inserter", 1)])

    def test_a_swap_is_judged_where_each_arm_stands(self):
        # (Codex, L-4: three fast arms already; swapping one for a fast one changes nothing)
        site = {**CAN, "inserter_bonus": {"stack": 2}, "recipes": CAN["recipes"] + [{"name": "big", "energy": 0.5,
                "category": "crafting", "ingredients": [{"name": "a", "amount": 17}], "products": [{"name": "big", "amount": 1}]}]}
        arm = lambda x: {"prototype": "fast-inserter", "position": {"x": x, "y": -1.5}, "direction": NORTH}
        belt = lambda x: {"prototype": "transport-belt", "position": {"x": x, "y": -2.5}, "direction": 4,
                          "lanes": {"left": ["a"], "right": ["z"]}}
        plan = [{"prototype": "assembling-machine-1", "position": {"x": 0.5, "y": 0.5}, "recipe": "big"},
                arm(-0.5), {"prototype": "iron-chest", "position": {"x": -0.5, "y": -2.5}}, arm(0.5), belt(0.5),
                arm(1.5), belt(1.5)]
        short = inserters.plan(plan, site)
        self.assertEqual(len(short), 1)
        self.assertNotEqual((short[0].fix, short[0].to), ("upgrade", "fast-inserter"))

    def test_an_ingredient_nothing_can_bring_is_a_missing_route(self):
        site = {**CAN, "recipes": CAN["recipes"] + [{"name": "ab", "energy": 1, "category": "crafting",
                "ingredients": [{"name": "a", "amount": 1}, {"name": "b", "amount": 1}],
                "products": [{"name": "ab", "amount": 1}]}]}
        plan = [{"prototype": "assembling-machine-1", "position": {"x": 0.5, "y": 0.5}, "recipe": "ab"},
                {"prototype": "inserter", "position": {"x": 0.5, "y": -1.5}, "direction": NORTH},
                {"prototype": "transport-belt", "position": {"x": 0.5, "y": -2.5}, "direction": 4,
                 "lanes": {"left": ["a"], "right": ["a"]}}]
        short = inserters.plan(plan, site)
        self.assertEqual([(c.fix, c.where.rsplit(" ", 1)[1]) for c in short], [("route", "(b)")])

    def test_the_strategist_hears_of_a_block_held_up_by_its_inserters(self):
        block = {"id": "gears", "stage": "bus-gears", "machines": {"assembling-machine-2": 4},
                 "measured": {"minutes": 40, "shares": {"starved": 0.4}, "arms": {"in:working": 0.9}}}
        self.assertIn("inserters feeding it work 90%", ledger.Ledger.from_site({"base": [block]}).summary())


class SignalTests(unittest.TestCase):

    def block(self, starved=0.0, backed=0.0, arms=None, minutes=40):
        return {"id": "gears", "stage": "bus-gears", "machines": {"assembling-machine-2": 4},
                "measured": {"minutes": minutes, "shares": {"working": 0.5, "starved": starved, "backed_up": backed},
                             "arms": arms or {}}}

    def test_short_of_inputs_while_its_feeders_never_wait_is_inserter_bound(self):
        site = {"base": [self.block(starved=0.4, arms={"in:working": 0.9, "out:waiting_source": 0.5})], **CAN}
        out = ledger.Ledger.from_site(site).inserter_bound(site)
        self.assertEqual([(o["side"], o["to"]) for o in out], [("in", "bulk-inserter")])
        self.assertIn("short of inputs 40% of the time", out[0]["why"])

    def test_short_while_its_feeders_wait_for_items_is_supply_not_inserters(self):
        site = {"base": [self.block(starved=0.4, arms={"in:waiting_source": 0.6, "in:working": 0.3})], **CAN}
        self.assertEqual(ledger.Ledger.from_site(site).inserter_bound(site), [])
        some = {"base": [self.block(starved=0.4, arms={"in:waiting_source": 0.24, "in:working": 0.76})], **CAN}
        self.assertEqual(ledger.Ledger.from_site(some).inserter_bound(some), [])   # (time to spare: supply, maybe)
        unfuelled = {"base": [self.block(starved=0.4, arms={"in:no_fuel": 0.2, "in:working": 0.8})], **CAN}
        self.assertEqual(ledger.Ledger.from_site(unfuelled).inserter_bound(unfuelled), [])
        backed = {"base": [self.block(starved=0.4, arms={"in:waiting_target": 0.2, "in:working": 0.8})], **CAN}
        self.assertEqual(ledger.Ledger.from_site(backed).inserter_bound(backed), [])   # (another input backs up)
        full = {"base": [self.block(backed=0.5, arms={"out:working": 0.95})], **CAN}
        self.assertEqual([o["side"] for o in ledger.Ledger.from_site(full).inserter_bound(full)], ["out"])


class GameTests(unittest.TestCase):

    def test_the_sampler_keeps_its_inserters_time_by_side(self):
        from lupa.lua52 import LuaRuntime
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1, item_ingredient_shortage=2, waiting_for_source_items=3}}
            M = assert(load(source))()
            gears = {name='iron-gear-wheel', energy=0.5, products={{name='iron-gear-wheel', amount=1}},
                     ingredients={{name='iron-plate', amount=2}}}
            box = {left_top={x=0, y=0}, right_bottom={x=3, y=3}}
            machine = {valid=true, type='assembling-machine', name='assembling-machine-2', status=2, unit_number=1,
                       products_finished=0, get_recipe=function() return gears end, bounding_box=box}
            feeder = {valid=true, type='inserter', name='inserter', status=1, drop_target=machine, bounding_box=box}
            taker = {valid=true, type='inserter', name='inserter', status=3, pickup_target=machine, bounding_box=box}
            plan = {order={'m', 'a', 'b'}, entities={m={entity=machine}, a={entity=feeder}, b={entity=taker}}}
            for minute = 0, 40 do M.sample(plan, minute * 3600) end
            npc = {design_history={plan}, design={status='complete'}}
            arms = M.blocks(npc, {x=0, y=0})[1].measured.arms
        """)
        arms = dict(lua.eval("arms").items())
        self.assertEqual(arms, {"in:working": 1, "out:waiting_source": 1})



class BothEndsTests(unittest.TestCase):

    def test_an_arm_between_two_machines_counts_for_both(self):
        from lupa.lua52 import LuaRuntime
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().source = (ROOT / "mod" / "scripts" / "base_memory.lua").read_text(encoding="utf-8")
        lua.execute("""
            defines = {entity_status={working=1}}
            M = assert(load(source))()
            r = {name='x', energy=1, products={}, ingredients={}}
            box = {left_top={x=0, y=0}, right_bottom={x=3, y=3}}
            a = {valid=true, type='assembling-machine', name='a', status=1, unit_number=1, products_finished=0,
                 get_recipe=function() return r end, bounding_box=box}
            b = {valid=true, type='assembling-machine', name='a', status=1, unit_number=2, products_finished=0,
                 get_recipe=function() return r end, bounding_box=box}
            arm = {valid=true, type='inserter', name='inserter', status=1, pickup_target=a, drop_target=b, bounding_box=box}
            plan = {order={'a', 'b', 'i'}, entities={a={entity=a}, b={entity=b}, i={entity=arm}}}
            for minute = 0, 3 do M.sample(plan, minute * 3600) end
            arms = M.blocks({design_history={plan}, design={status='complete'}}, {x=0, y=0})[1].measured.arms
        """)
        self.assertEqual(dict(lua.eval("arms").items()), {"in:working": 1, "out:working": 1})

if __name__ == "__main__":
    unittest.main()
