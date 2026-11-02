"""The goal weighing (companion/goal_brain.py): what a goal adds, can be fed, costs in hand work, and the prior."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import goal_brain as gb  # noqa: E402
from companion import ledger  # noqa: E402

RECIPES = [{"name": "iron-gear-wheel", "category": "crafting", "energy": 0.5,
            "ingredients": [{"name": "iron-plate", "amount": 2}], "products": [{"name": "iron-gear-wheel", "amount": 1}]},
           {"name": "automation-science-pack", "category": "crafting", "energy": 5,
            "ingredients": [{"name": "copper-plate", "amount": 1}, {"name": "iron-gear-wheel", "amount": 1}],
            "products": [{"name": "automation-science-pack", "amount": 1}]},
           {"name": "iron-plate", "category": "smelting", "energy": 3.2,
            "ingredients": [{"name": "iron-ore", "amount": 1}], "products": [{"name": "iron-plate", "amount": 1}]},
           {"name": "stone-brick", "category": "smelting", "energy": 3.2,
            "ingredients": [{"name": "stone", "amount": 2}], "products": [{"name": "stone-brick", "amount": 1}]}]
RED = {"entities": [{"id": "g", "prototype": "assembling-machine-1", "recipe": "iron-gear-wheel"},
                    {"id": "s", "prototype": "assembling-machine-1", "recipe": "automation-science-pack"},
                    {"id": "l", "prototype": "lab"}], "stages": []}


def site(**extra):
    out = {"npc_id": 3, "tick": 1000, "recipes": RECIPES, "machines": {"assembling-machine-1": {"speed": 0.5}},
           "suggestions": {"red-science-automation": RED}, "base": [], "completed_purposes": []}
    out.update(extra)
    return out


class FlowTests(unittest.TestCase):
    def test_a_lines_own_intermediates_are_not_its_output_and_its_inputs_are_what_it_needs(self):
        from companion import ratios
        book = ratios.Book.from_site(site())
        makes, need, machines = gb.flows("red-science-automation", site(), book)
        self.assertIn("automation-science-pack", makes)
        self.assertNotIn("iron-gear-wheel", makes)                       # (its gears go into its packs)
        self.assertIn("iron-plate", need)
        self.assertIn("copper-plate", need)
        self.assertEqual(machines["assembling-machine-1"], 2)

    def test_drill_pairs_make_what_the_recipe_gives_for_the_ore(self):
        from companion import ratios
        book = ratios.Book.from_site(site())
        makes, _, machines = gb.flows("brick-smelting", site(), book)
        pairs = 8
        self.assertAlmostEqual(makes["stone-brick"], gb.DRILL_RATE["burner-mining-drill"] * pairs / 2)   # 2 stone a brick
        self.assertEqual((machines["burner-mining-drill"], machines["stone-furnace"]), (pairs, pairs))

    def test_a_consumer_is_worth_what_can_feed_it(self):
        iron = {"id": "i", "purpose": "iron-smelting-start", "capacity": {"makes": {"iron-plate": 0.1}},
                "ledger": {"kind": "smelting", "load": 1.0}}
        book = ledger.Ledger.from_site({"base": [iron]})
        self.assertAlmostEqual(gb.feed_share({"iron-plate": 0.4}, book), gb.HAND_FLOOR + (1 - gb.HAND_FLOOR) * 0.25)
        self.assertEqual(gb.feed_share({"iron-plate": 0.05}, book), 1.0)
        self.assertEqual(gb.feed_share({}, book), 1.0)


class HandWorkTests(unittest.TestCase):
    def test_burners_add_hand_work_costlier_when_its_time_already_goes_there(self):
        work = gb.hand_work({"burner-mining-drill": 4, "stone-furnace": 4})
        fuel = 4 * ledger.FUEL_DRAW["burner-mining-drill"] + 4 * ledger.FUEL_DRAW["stone-furnace"]
        self.assertAlmostEqual(work, fuel * 60 * gb.HAND_SECONDS_PER_FUEL)
        self.assertEqual(gb.hand_work({"assembling-machine-1": 3}), 0.0)
        busy = site(time_use={"shares": {"supply:mine:coal": 0.5, "collect:transfer:iron-plate": 0.2, "build:build:x": 0.3}})
        self.assertAlmostEqual(gb.busy_share(busy), 0.7)
        calm = gb.weigh(site(), ["brick-smelting"])[0]
        pressed = gb.weigh(busy, ["brick-smelting"])[0]
        self.assertGreater(pressed["hand_added"], calm["hand_added"])

    def test_with_its_burners_short_of_fuel_a_fuel_producer_saves_hand_mining(self):
        burners = {"id": "b", "purpose": "iron-smelting-expansion", "machines": {"burner-mining-drill": 8, "stone-furnace": 8}}
        spare = gb.fuel_spare(site(base=[burners]), None)
        self.assertLess(spare, 0)                                          # (no loop: their coal is mined by hand)
        self.assertGreater(gb.fuel_saved("coal-supply", {"coal": 0.5}, -spare), 0)
        self.assertEqual(gb.fuel_saved("coal-supply", {"iron-plate": 0.5}, -spare), 0)
        self.assertEqual(gb.fuel_saved("coal-supply", {"coal": 0.5}, 0.0), 0)   # (fuel enough: nothing saved)


class CoalTests(unittest.TestCase):
    """Coal grows ahead of the burners it feeds (the user, 2026-10-26); an iron block of 11 burners went up on one
    4-drill loop and about 100 coal was mined by hand (run 56)."""

    def setUp(self):
        gb.forget()

    def loop(self, drills=4):
        return {"id": "l", "purpose": "coal-loop", "machines": {"burner-mining-drill": drills}}

    def iron(self, pairs):
        return {"id": f"i{pairs}", "purpose": "iron-smelting-expansion",
                "machines": {"burner-mining-drill": pairs, "stone-furnace": pairs}}

    def test_the_loops_spare_counts_the_burners_standing_whatever_the_ledger_says(self):
        from companion import catalog_planner as cp
        s = site(completed_purposes=["coal-loop"], base=[self.loop(), self.iron(6)])
        expect = 4 * cp.LOOP_NET / cp.COAL_MARGIN - cp.coal_draw(s)
        self.assertAlmostEqual(gb.fuel_spare(s, ledger.Ledger.from_site(s)), expect)
        self.assertLess(gb.fuel_spare(site(completed_purposes=["coal-loop"], base=[self.loop(), self.iron(12)]), None), 0)

    def test_a_burner_goal_the_loops_cannot_feed_prices_its_coal_as_mined_by_hand(self):
        fed = site(completed_purposes=["coal-loop"], base=[self.loop(8)])
        short = site(completed_purposes=["coal-loop"], base=[self.loop(4), self.iron(8)])
        easy = gb.weigh(fed, ["iron-smelting-expansion"])[0]
        hard = gb.weigh(short, ["iron-smelting-expansion"])[0]
        self.assertEqual(easy["coal_mined"], 0.0)
        self.assertGreater(hard["coal_mined"], 0.0)
        self.assertGreater(hard["hand_added"], easy["hand_added"])

    def test_the_loop_is_grown_before_a_burner_block_it_cannot_feed(self):
        short = site(completed_purposes=["coal-loop"], base=[self.loop(4), self.iron(8)])
        everything = lambda p: True   # noqa: E731
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            chosen, rows = gb.choose(short, ["iron-smelting-expansion"], fuel=everything)
            row = json.loads((Path(folder) / "decisions" / gb.LOG_NAME).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(chosen, "coal-loop")
        iron = next(r for r in row["candidates"] if r["goal"] == "iron-smelting-expansion")
        self.assertGreater(iron["coal_mined"], 0)                         # (its coal priced as mined by hand)
        self.assertIn("coal_spare", row)
        # fed by the loops, the burner block goes ahead
        fed = site(completed_purposes=["coal-loop"], base=[self.loop(8)])
        self.assertEqual(gb.choose(fed, ["iron-smelting-expansion"], fuel=everything)[0], "iron-smelting-expansion")
        # and with no fuel maker to build, the weighing alone
        self.assertEqual(gb.choose(short, ["iron-smelting-expansion"], fuel=lambda p: False)[0],
                         "iron-smelting-expansion")

    def test_a_burner_goal_short_of_coal_waits_for_a_fuel_maker_whatever_their_scores(self):
        s = site(completed_purposes=["coal-loop"], base=[self.loop(4)])
        iron = {"goal": "iron-smelting-expansion", "score": 6.0, "coal_mined": 9.0, "makes": {"iron-plate": 1.0}}
        loop = {"goal": "coal-loop", "score": 2.0, "coal_mined": 0.0, "coal_net": 0.57, "makes": {"coal": 1.0}}
        # (drills into chests burn some of their coal, mined by hand while short: still more coal than they burn)
        chests = {"goal": "coal-supply", "score": 3.0, "coal_mined": 9.0, "coal_net": 0.26, "makes": {"coal": 0.5}}
        self.assertIs(gb.fuel_first(s, iron, [iron, chests, loop]), loop)          # (a self-fed loop first)
        self.assertIs(gb.fuel_first(s, iron, [iron, chests]), chests)
        self.assertIsNone(gb.fuel_first(s, dict(iron, coal_mined=0.0), [iron, loop]))   # (fed: no need)
        self.assertIsNone(gb.fuel_first(s, iron, [iron]))                          # (nothing to build: the weighing)
        self.assertIsNone(gb.fuel_first(s, iron, [iron, dict(chests, coal_net=-0.1)]))   # (burns more than it adds)

    def test_the_coal_check_is_on_the_goal_kept_in_hand(self):
        # (a coal-hungry goal held a while by the margin went ahead unchecked: the review, 2026-10-26)
        short = site(completed_purposes=["coal-loop"], base=[self.loop(4), self.iron(8)], tick=1000)
        rows = [{"goal": "research-lab", "score": 2.0, "coal_mined": 0.0, "makes": {}},
                {"goal": "iron-smelting-expansion", "score": 1.95, "coal_mined": 9.0, "makes": {"iron-plate": 1.0}}]
        loop = {"goal": "coal-loop", "score": 1.0, "coal_mined": 0.0, "coal_net": 0.57, "makes": {"coal": 1.0}}
        gb._held[short["npc_id"]] = ("iron-smelting-expansion", 900)
        with mock.patch.object(gb, "weigh", side_effect=[rows, [*rows, loop]]):
            self.assertEqual(gb.choose(short, ["research-lab", "iron-smelting-expansion"], fuel=lambda p: True)[0],
                             "coal-loop")

    def test_the_balance_is_what_can_be_collected_against_the_hand_fed_burners(self):
        from companion import catalog_planner as cp
        # boilers fed by belt, and the ledger's view of them, take nothing from what the engineer collects
        belted = site(completed_purposes=["coal-loop", "steam-power", "boiler-coal"],
                      base=[self.loop(4), {"id": "s", "purpose": "steam-power", "machines": {"boiler": 2},
                                           "ledger": {"kind": "power", "load": 1.0}}])
        self.assertAlmostEqual(gb.fuel_spare(belted, ledger.Ledger.from_site(belted)), 4 * cp.LOOP_NET / cp.COAL_MARGIN)
        # drills into chests add what they fill
        chests = {"id": "c", "purpose": "coal-supply", "machines": {"burner-mining-drill": 2}}
        self.assertAlmostEqual(gb.chest_coal(site(base=[chests])), 2 * gb.DRILL_RATE["burner-mining-drill"])
        measured = dict(chests, capacity={"makes": {"coal": 0.3}})
        self.assertAlmostEqual(gb.chest_coal(site(base=[measured])), 0.3)
        mined_out = dict(chests, capacity={"makes": {"coal": 0}})
        self.assertEqual(gb.chest_coal(site(base=[mined_out])), 0.0)

    def test_a_self_fed_loop_comes_before_drills_into_chests(self):
        s = site(completed_purposes=["coal-loop"], base=[self.loop(4)])
        self.assertEqual(gb.fuel_goals(s, [], available=lambda p: p in ("coal-loop", "coal-supply")), ["coal-loop"])

    def test_a_loop_at_its_cap_is_no_answer(self):
        from companion import catalog_planner as cp
        capped = site(completed_purposes=["coal-loop"], base=[self.loop(cp.LOOP_MAX), self.iron(30)])
        self.assertFalse(gb.fuel_growable("coal-loop", capped))
        self.assertNotIn("coal-loop", gb.fuel_goals(capped, ["iron-smelting-expansion"], available=lambda p: True))


class RockTests(unittest.TestCase):
    def test_stone_from_rocks_within_reach_is_on_hand_for_the_opening(self):
        # (a stone drill block counted 155 stone as 50 minutes of hand mining and came before iron and steam, run 56)
        plain = gb.weigh(site(), ["stone-supply"], opening=["stone-supply", "steam-power"])[0]
        rocky = gb.weigh(site(rocks={"stone": 2000, "coal": 300, "count": 60, "radius": 150}), ["stone-supply"],
                         opening=["stone-supply", "steam-power"])[0]
        self.assertLess(rocky["speedup"], plain["speedup"])
        self.assertEqual(gb.on_hand(site(inventory={"stone": 50}, rocks={"stone": 400, "count": 9}))["stone"], 450)
        self.assertNotIn("count", gb.on_hand(site(rocks={"stone": 1, "count": 9})))
        self.assertEqual(gb.on_hand(site(rocks={"stone": float("nan"), "coal": -5})), {})   # (only sound amounts)
        self.assertEqual(gb.on_hand(site(rocks={"stone": 10 ** 400})), {})
        self.assertEqual(gb.on_hand(site(inventory={"stone": 1e308}, rocks={"stone": 1e308})), {"stone": 1e308})


class AskMemoryTests(unittest.TestCase):
    def setUp(self):
        gb.forget()

    def test_an_ask_passed_over_is_weighed_again_until_built_or_old(self):
        steel = {"id": "s", "purpose": "steel-smelting", "machines": {"stone-furnace": 2}}
        gb.remember_ask(site(tick=1000), "bus-base")
        gb.remember_ask(site(tick=2000), "steel-smelting")
        self.assertEqual(gb.asks(site(tick=3000)), ["steel-smelting", "bus-base"])
        gb.choose(site(tick=3000), ["steel-smelting"])                    # (chosen, but not built yet: still asked)
        self.assertEqual(gb.asks(site(tick=3100)), ["steel-smelting", "bus-base"])
        self.assertEqual(gb.asks(site(tick=3200, base=[steel], completed_purposes=["steel-smelting"])), ["bus-base"])
        self.assertEqual(gb.asks(site(tick=1000 + gb.ASK_HOLD + 1)), [])

    def test_a_loop_asked_to_grow_again_stays_asked_until_it_has(self):
        loop = {"id": "l", "purpose": "coal-loop", "machines": {"burner-mining-drill": 4}}
        before = site(tick=1000, base=[loop], completed_purposes=["coal-loop"])
        gb.remember_ask(before, "coal-loop")
        self.assertEqual(gb.asks(dict(before, tick=1100)), ["coal-loop"])
        grown = dict(before, tick=1200, base=[loop, dict(loop, id="l2")])
        self.assertEqual(gb.asks(grown), [])

    def test_a_save_loaded_from_before_forgets_what_came_after(self):
        gb.remember_ask(site(tick=120_000), "bus-base")
        self.assertEqual(gb.asks(site(tick=1000)), [])


class HandPriceTests(unittest.TestCase):
    def test_hand_work_gets_dearer_steeply_as_its_burners_fill_its_time(self):
        # (run 20, 2026-10-25: priced flat, burner rows kept coming until 55 burners took most of its time)
        row = {"id": "r", "purpose": "iron-smelting-expansion", "machines": {"burner-mining-drill": 5, "stone-furnace": 6}}
        prices = [gb.hand_price(site(base=[dict(row, id=f"r{i}") for i in range(n)])) for n in (0, 2, 4, 8)]
        self.assertEqual(prices, sorted(prices))
        self.assertAlmostEqual(prices[0], gb.HAND_WEIGHT)
        self.assertGreater(prices[3] / prices[2], prices[1] / prices[0])        # (steeper the fuller)
        self.assertLessEqual(prices[3], gb.HAND_WEIGHT / (1 - gb.LOAD_CAP) + 1e-9)
        # what feeds itself or is fed by belt is not hand work: a coal loop, boilers once coal comes by belt
        loop = {"id": "l", "purpose": "coal-loop", "machines": {"burner-mining-drill": 4}}
        self.assertEqual(gb.hand_load(site(base=[loop])), 0.0)
        boilers = {"id": "b", "purpose": "steam-power", "machines": {"boiler": 2, "steam-engine": 4}}
        self.assertGreater(gb.hand_load(site(base=[boilers])), 0.0)
        self.assertEqual(gb.hand_load(site(base=[boilers], completed_purposes=["boiler-coal"])), 0.0)

    def test_drills_into_furnaces_with_no_recipe_set_make_the_plates_of_their_ore(self):
        # (the game's electric mining layout sets no recipe: it counted for nothing, run 20)
        layout = {"entities": [{"id": f"d{i}", "prototype": "electric-mining-drill"} for i in range(2)]
                  + [{"id": f"f{i}", "prototype": "stone-furnace"} for i in range(2)], "stages": []}
        from companion import ratios
        s = site(suggestions={"electric-iron-mining": layout})
        makes, need, _ = gb.flows("electric-iron-mining", s, ratios.Book.from_site(s))
        self.assertAlmostEqual(makes["iron-plate"], 2 / 3.2)          # (two stone furnaces: 0.3125 a second each)
        self.assertNotIn("iron-ore", need)


class UnlockTests(unittest.TestCase):
    def test_a_goal_that_opens_the_way_to_one_retiring_burners_is_worth_part_of_that(self):
        # (the bus waits on electric iron mining and retires the burner rows: run 19 built it at minute 160)
        from companion import bus_planner
        burners = [{"id": f"r{i}", "purpose": p, "machines": {"burner-mining-drill": 5, "stone-furnace": 6}}
                   for i, p in enumerate(bus_planner.SUPERSEDED[:2])]
        lean = gb.weigh(site(completed_purposes=["steam-power"]), ["electric-iron-mining"])[0]["unlocks"]
        heavy = gb.weigh(site(base=burners, completed_purposes=["steam-power"]), ["electric-iron-mining"])[0]["unlocks"]
        self.assertGreater(heavy, lean)


class PlannedOutputTests(unittest.TestCase):
    def test_a_bus_under_way_makes_its_science_goal_and_a_wall_row_its_walls(self):
        # (run 29, 2026-10-25: counted for nothing, a half-built bus lost to the opening's next step)
        bus = site(base_plan={"stages": ["power", "smelting-iron-plate"], "spm": 30,
                              "packs": ["automation-science-pack", "logistic-science-pack"]})
        makes, _, _ = gb.flows("bus-base", bus)
        self.assertEqual(makes, {"automation-science-pack": 0.5, "logistic-science-pack": 0.5})
        self.assertEqual(gb.flows("bus-base", site())[0], {})                # (no bus yet: no claim)
        from companion import wall_block
        makes, need, _ = gb.flows("wall-automation", site())
        self.assertEqual(makes, {"stone-wall": wall_block.WALL_RATE})
        self.assertIn("stone-brick", need)


class OddsTests(unittest.TestCase):
    def test_a_goal_that_keeps_failing_loses_its_odds_and_a_project_under_way_is_finished(self):
        # (runs 24 to 27, 2026-10-25: a boiler's coal belt with no room for it was picked first again and again,
        # while the bus stood half built)
        fresh = gb.weigh(site(), ["brick-smelting"])[0]
        failed = gb.weigh(site(failures={"brick-smelting": {"count": 2, "ago": 60}}), ["brick-smelting"])[0]
        self.assertEqual(failed["odds"], gb.FAIL_ODDS ** 2)
        self.assertAlmostEqual(failed["score"], fresh["score"] * gb.FAIL_ODDS ** 2, places=2)
        with mock.patch("companion.catalog_planner.unfinished", lambda p, s: p == "bus-base"):
            under = gb.weigh(site(completed_purposes=["bus-base"]), ["bus-base"])[0]
            new = gb.weigh(site(), ["bus-base"])[0]
        self.assertTrue(under["under_way"])
        self.assertAlmostEqual(under["score"] - new["score"], gb.CONTINUE_PRIOR, places=2)


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        gb.forget()

    def test_the_openings_order_is_a_prior_a_much_better_goal_can_overtake(self):
        rows = gb.weigh(site(), ["coal-loop", "brick-smelting"], opening=["brick-smelting", "coal-loop"])
        prior = {r["goal"]: r["prior"] for r in rows}
        self.assertEqual(prior, {"brick-smelting": 1.0, "coal-loop": 0.5})
        with mock.patch.object(gb, "PRIOR", 0.0):                         # (turned down: value alone)
            self.assertTrue(gb.weigh(site(), ["coal-loop", "brick-smelting"])[0]["prior"] == 0)

    def test_the_goal_in_hand_is_kept_unless_another_clears_it(self):
        first = gb.choose(site(tick=1000), ["coal-loop", "brick-smelting"], opening=["coal-loop", "brick-smelting"])[0]
        self.assertEqual(first, "coal-loop")
        # the other a shade better now: kept (no flitting)
        with mock.patch.object(gb, "weigh", lambda *a, **k: [{"goal": "brick-smelting", "score": 1.05},
                                                             {"goal": "coal-loop", "score": 1.0}]):
            self.assertEqual(gb.choose(site(tick=2000), ["coal-loop", "brick-smelting"])[0], "coal-loop")
            self.assertEqual(gb.choose(site(tick=2000 + gb.HOLD_TICKS), ["coal-loop", "brick-smelting"])[0],
                             "brick-smelting")                            # (held long enough: the better one)

    def test_defense_goals_are_left_to_the_defense_judgment_and_each_choice_is_written_down(self):
        self.assertTrue(gb.defense("reinforce"))
        self.assertTrue(gb.defense("plot-turrets"))
        self.assertFalse(gb.defense("brick-smelting"))
        self.assertEqual([r["goal"] for r in gb.weigh(site(), ["reinforce", "coal-loop"])], ["coal-loop"])
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"NPC_FACTIONS_DATA": folder}):
            gb.choose(site(), ["coal-loop", "brick-smelting"])
            row = json.loads((Path(folder) / "decisions" / gb.LOG_NAME).read_text(encoding="utf-8").splitlines()[0])
        self.assertIn(row["chose"], ("coal-loop", "brick-smelting"))
        self.assertEqual(len(row["candidates"]), 2)
        self.assertEqual(row["weights"], "default")
        self.assertIn("guard", row)
        self.assertTrue({"due", "milestone", "asked", "guard"} <= set(row["candidates"][0]))


class WeightTests(unittest.TestCase):
    def test_the_score_is_the_one_formula_of_the_parts_and_weights(self):
        r = gb.weigh(site(), ["brick-smelting"], opening=["brick-smelting"])[0]
        w = gb.default_weights()
        expect = ((r["value"] + r["unlocks"] + w["speed"] * r["speedup"] + r["hand_saved"] - r["hand_added"])
                  / (1 + r["cost"] / w["cost_scale"]) + w["prior"] * r["prior"]) * r["odds"]
        self.assertAlmostEqual(r["score"], expect, places=2)

    def test_fitted_weights_come_only_from_the_file_named_and_only_sound_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "w.json"
            path.write_text(json.dumps({"schema": gb.WEIGHTS_SCHEMA, "weights": {
                "due": 0.8, "speed": -1, "cost_scale": 0, "prior": "x", "nonsense": 3}}))
            with mock.patch.dict(os.environ, {gb.WEIGHTS_ENV: str(path)}):
                self.assertEqual(gb.fitted_weights(), {"due": 0.8})
                self.assertEqual(gb.weights()["speed"], gb.SPEED_WEIGHT)
            path.write_text('{"schema": "%s", "weights": {"due": true, "prior": %s, "speed": 0.2}}'
                            % (gb.WEIGHTS_SCHEMA, "1" + "0" * 400))
            os.utime(path, (5, 5))
            with mock.patch.dict(os.environ, {gb.WEIGHTS_ENV: str(path)}):
                self.assertEqual(gb.fitted_weights(), {"speed": 0.2})   # (no flag for a number, nothing past a float)
            path.write_text(json.dumps({"schema": "other", "weights": {"due": 0.8}}))
            os.utime(path, (1, 1))
            with mock.patch.dict(os.environ, {gb.WEIGHTS_ENV: str(path)}):
                self.assertEqual(gb.fitted_weights(), {})
        with mock.patch.dict(os.environ, {gb.WEIGHTS_ENV: ""}):
            self.assertEqual(gb.weights(), gb.default_weights())

    def test_the_pace_term_is_logged_and_weighs_only_with_its_weight(self):
        late = site(tick=40 * 3600, completed_purposes=["steam-power", "copper-smelting-start", "research-lab"])
        r = gb.weigh(late, ["red-science-automation"])[0]
        self.assertGreater(r["due"], 1.0)
        self.assertEqual(r["milestone"], "red science automated")
        with mock.patch.object(gb, "DUE_WEIGHT", 1.0):
            weighed = gb.weigh(late, ["red-science-automation"])[0]
        self.assertAlmostEqual(weighed["score"] - r["score"], r["due"], places=2)

    def test_a_goal_asked_for_gets_the_ask_prior(self):
        plain = gb.weigh(site(), ["brick-smelting"])[0]
        asked = gb.weigh(site(), ["brick-smelting"], asked="brick-smelting")[0]
        self.assertTrue(asked["asked"])
        self.assertAlmostEqual(asked["score"] - plain["score"], gb.ASK_PRIOR, places=2)


class UnlockFixTests(unittest.TestCase):
    def test_standing_for_what_is_built_opens_nothing(self):
        # (coal by belt to the boilers stands for the steam power already built: every goal on power counted as
        # opened by it, and it won by 7.9 to 2.1 over red science at minute 21, run 46, 2026-10-26)
        with mock.patch.object(gb, "hand_saved", lambda p, s, f=None: 1.0 if p == "research-lab" else 0.0):
            built = gb.weigh(site(completed_purposes=["steam-power"]), ["boiler-coal"])[0]
            waiting = gb.weigh(site(), ["steam-power"])[0]
        self.assertEqual(built["unlocks"], 0.0)
        self.assertGreater(waiting["unlocks"], 0.0)


class GuardTests(unittest.TestCase):
    def board(self, attacks=(), evolution=0.0, **extra):
        core = {"id": "c", "purpose": "iron-smelting-start", "box": [-10, -10, 10, 10], "buildings": 10}
        return site(base=[core, *extra.pop("base", [])], tick=600_000,
                    threat_board={"evolution": evolution, "attacks": list(attacks)}, **extra)

    def test_an_attack_with_no_wall_standing_makes_defenses_urgent_at_once(self):
        hit = {"x": 0, "y": -20, "tick": 590_000, "units": {"small-biter": 5}, "from": {"x": 0, "y": -60}}
        g = gb.guard_of(self.board([hit]))
        self.assertTrue(g.hit_unwalled and g.urgent)
        self.assertEqual(g.value, 1.0)
        wall = {"id": "w", "purpose": "plot-wall", "box": [-30, -30, 30, -28]}
        self.assertFalse(gb.guard_of(self.board([hit], base=[wall])).hit_unwalled)
        # (an hour ago, the 30 minutes the board shows long past: the game keeps every attack for good)
        long_ago = self.board()
        long_ago["threat_board"]["ever"] = {"count": 1, "first": 380_000, "last": 380_400}
        self.assertTrue(gb.guard_of(long_ago).urgent)
        from companion import threat
        self.assertTrue(threat.unwalled_hit(long_ago))
        self.assertFalse(threat.unwalled_hit(self.board(base=[wall])))

    def test_pollution_and_evolution_raise_it_slowly_without_an_attack(self):
        calm = gb.guard_of(self.board())
        smoky = gb.guard_of(self.board(evolution=0.3, pollution=[{"x": 0, "y": 0, "p": 900}]))
        self.assertFalse(smoky.urgent)
        self.assertGreater(smoky.value, calm.value)
        self.assertLess(smoky.value, 1.0)

    def test_defenses_and_what_they_wait_on_carry_it(self):
        s = site(completed_purposes=["steam-power"])
        self.assertEqual(gb.guard_share("wall-automation", s), 1.0)
        self.assertEqual(gb.guard_share("plot-wall", s), 1.0)
        self.assertEqual(gb.guard_share("brick-smelting", s), 0.5)          # (the wall row waits on its bricks)
        self.assertEqual(gb.guard_share("electric-brick-smelting", s), 0.5)  # (and on what stands for them)
        self.assertEqual(gb.guard_share("iron-smelting-expansion", s), 0.0)


if __name__ == "__main__":
    unittest.main()


class RobotNetworkTests(unittest.TestCase):
    """The robot network, weighed by what it saves: nothing weighed it, and a real game never chose it (SO-6)."""

    def base(self, n, rebuilt=0, **extra):
        blocks = [{"id": f"b{i}", "purpose": "iron-smelting-expansion", "buildings": 40,
                   "machines_box": [i * 20, 0, i * 20 + 8, 8]} for i in range(n)]
        tick = extra.pop("tick", 600_000)
        return site(base=blocks, rebuilt=[{"rebuilt": tick - 600}] * rebuilt + [{"rebuilt": 0}] * 50, tick=tick,
                    completed_purposes=["steam-power"], **extra)   # (and old ones, long past: not counted)

    def test_it_saves_more_the_bigger_the_base_and_the_more_it_rebuilds(self):
        from companion import ratios
        saved = [gb.robots_saved(s, ratios.Book.from_site(s), []) for s in (self.base(1), self.base(5), self.base(5, 30))]
        self.assertEqual(saved, sorted(saved))
        self.assertGreater(saved[2], saved[1])
        with mock.patch("companion.robo_grid.coverage", return_value=(1.0, [])):
            self.assertEqual(gb.robots_saved(self.base(5), None, []), 0.0)   # (covered: nothing left to save)

    def test_its_worth_is_capped_below_a_starved_plate_or_an_emergency(self):
        row = gb.weigh(self.base(80, 50), ["robot-network"])[0]
        self.assertLessEqual(row["hand_saved"], gb.ROBOT_CAP + 1e-9)

    def test_it_is_weighed_in_the_opening_and_taken_after_it_once_it_pays(self):
        from companion import blueprints as bp
        from companion import catalog_planner as cp
        with tempfile.TemporaryDirectory() as folder:
            planner = cp.CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            with mock.patch.object(cp, "available", side_effect=lambda p, s, designer=False: p == "robot-network"), \
                    mock.patch.object(gb, "weigh", return_value=[{"goal": "robot-network", "score": 2.0,
                                                                  "hand_saved": 4.0}]):
                self.assertEqual(planner._worth_weighing(self.base(5)), "robot-network")
            with mock.patch.object(cp, "available", side_effect=lambda p, s, designer=False: p == "robot-network"), \
                    mock.patch.object(gb, "weigh", return_value=[{"goal": "robot-network", "score": 0.4,
                                                                  "hand_saved": 0.5}]):
                self.assertIsNone(planner._worth_weighing(self.base(5)))   # (not worth it yet)
        self.assertIn("robot-network", gb.ALWAYS_WEIGHED)
