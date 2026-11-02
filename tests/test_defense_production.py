"""Defense items made by machines at the rate the waves ask for (companion/defense_production.py, F-12): turrets their
areas lack, ammunition for the waves coming, walls for what they break (through the wall machinery); short of what
the base makes, built in a chest-fed row; urgent while the waves grow; a guarding goal the weighing sees."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import catalog_planner as cp  # noqa: E402
from companion import defense_production as ds  # noqa: E402
from companion import goal_brain, tier_builds  # noqa: E402
from companion.ratios import Book  # noqa: E402
from test_defense_brain import CORE, LAB, attack  # noqa: E402
from test_defense_plans_perimeter import site, world  # noqa: E402

TICK = 20 * 3600
RECIPES = {"assembling-machine-1", "inserter", "small-electric-pole", "gun-turret", "firearm-magazine", "stone-wall"}


def waves(*sizes, gap=4 * 3600, end=TICK):
    """Waves on the core, `gap` ticks apart, the last at `end`."""
    n = len(sizes)
    return [attack(-395, -397, end - (n - 1 - k) * gap, {"small-biter": s}, came=(-395, -600))
            for k, s in enumerate(sizes)]


# (its plates' income: a row of turrets or magazines with no iron made stands empty, D-18; no box, so no area of its own)
SMELTING = {"purpose": "iron-smelting-start", "recipes": ["iron-plate", "copper-plate"],
            "capacity": {"makes": {"iron-plate": 1.0, "copper-plate": 1.0}}}


def pressed(*sizes, base=None, guards=(), smelting=True, **extra):
    return site(base=(base or [CORE, LAB]) + ([SMELTING] if smelting else []), tick=TICK, guards=list(guards),
                threat_board={"evolution": 0.1, "attacks": waves(*sizes)}, **extra)


def ammo(d):
    """The ammunition the draw counts (the arms' own: the best the engineer can make)."""
    return next(n for n in d.needs if n.item.endswith("magazine") or "ammo" in n.item)


def block(item, rate):
    return {"purpose": ds.GOAL, "box": [-380, -380, -370, -370], "machines": {"assembling-machine-1": 1},
            "recipes": [item], "measured": {"minutes": 10, "out5": {item: rate}}}


class DrawTests(unittest.TestCase):
    def test_turrets_ammunition_and_walls_by_the_waves(self):
        d = ds.draw(pressed(6, 10))
        items = {n.item: n for n in d.needs}
        self.assertIn("gun-turret", items)                          # (the core lacks turrets against the waves)
        self.assertTrue(ammo(d))
        self.assertIn("stone-wall", items)
        self.assertTrue(d.growing)
        self.assertAlmostEqual(d.gap, 4 * 60)                       # (the gap measured between the waves)
        self.assertGreater(ammo(ds.draw(pressed(20, 40))).rate, ammo(d).rate)   # (bigger waves: more ammunition)

    def test_better_ammunition_takes_fewer_magazines(self):
        with mock.patch("companion.perimeter._ammo_for", return_value="firearm-magazine"):
            plain = ammo(ds.draw(pressed(6, 10)))
        with mock.patch("companion.perimeter._ammo_for", return_value="piercing-rounds-magazine"):
            better = ammo(ds.draw(pressed(6, 10)))
        self.assertLess(better.rate, plain.rate)

    def test_no_turret_it_can_make_no_draw_and_steady_waves_are_not_growing(self):
        with mock.patch("companion.perimeter.arms", return_value={"turrets": [], "walls": [], "ammo": {}}),                 mock.patch("companion.ratios.unlocked", return_value=set()):
            self.assertEqual(ds.draw(pressed(6, 10)).needs, [])
        self.assertFalse(ds.draw(pressed(8, 8)).growing)

    def test_what_its_blocks_make_counts_against_the_draw(self):
        need = ammo(ds.draw(pressed(6, 10)))
        fed = pressed(6, 10, base=[CORE, LAB, block(need.item, need.rate * 2)])
        self.assertNotIn(need.item, [n.item for n in ds.draw(fed).short()])


class RoundOneTests(unittest.TestCase):
    """Codex's F-12 review: standing gun turrets still fed, bricks not asked for again, a starved row fed not doubled,
    repair packs, and power first when nothing can make defense items by machine."""

    def test_standing_gun_turrets_keep_their_ammunition_once_electric_turrets_come_first(self):
        laser = {"name": "laser-turret", "kind": "electric", "range": 24, "min_range": 0}
        gun = {"name": "gun-turret", "kind": "ammo", "range": 18, "min_range": 0, "ammo_categories": ["bullet"]}
        arms = {"turrets": [laser, gun], "walls": [], "ammo": {"bullet": ["firearm-magazine"]}}
        guns = [{"x": -395, "y": -410, "kind": "ammo-turret", "name": "gun-turret", "ammo": 5}]
        with mock.patch("companion.perimeter.arms", return_value=arms):
            items = [n.item for n in ds.draw(pressed(6, 10, guards=guns)).needs]
            self.assertIn("firearm-magazine", items)
            self.assertNotIn("firearm-magazine", [n.item for n in ds.draw(pressed(6, 10)).needs])   # (none to feed)

    def test_bricks_are_asked_for_while_none_are_made_then_the_wall_row(self):
        from companion import wall_block
        bricks = {"purpose": "brick-smelting", "box": [-380, -360, -376, -356], "machines": {"stone-furnace": 2},
                  "recipes": ["stone-brick"], "measured": {"minutes": 10, "out5": {"stone-brick": 0.3}}}
        s = pressed(6, 10, base=[CORE, LAB, bricks])
        with mock.patch.object(ds.Draw, "short", lambda self: [n for n in self.needs if n.item == "stone-wall"]):
            got = ds.pick(s, available=lambda g: g == wall_block.BRICKS)   # (bricks made: not asked for again)
            self.assertEqual(got.goal, ds.GOAL)                             # (a row of walls from them)
            self.assertEqual(ds.pick(s, available=lambda g: True).goal, wall_block.PURPOSE)

    def test_its_own_row_counts_what_it_is_built_to_make_when_starved(self):
        need = ammo(ds.draw(pressed(6, 10)))
        starved = {"purpose": ds.GOAL, "box": [-380, -380, -370, -370], "machines": {"assembling-machine-1": 2},
                   "recipes": [need.item], "capacity": {"makes": {need.item: need.rate * 2}},
                   "measured": {"minutes": 10, "out5": {}}}
        self.assertNotIn(need.item, [n.item for n in ds.draw(pressed(6, 10, base=[CORE, LAB, starved])).short()])

    def test_its_starved_row_adds_to_what_others_make(self):
        need = ammo(ds.draw(pressed(6, 10)))
        starved = {"purpose": ds.GOAL, "box": [-380, -380, -370, -370], "machines": {"assembling-machine-1": 1},
                   "recipes": [need.item], "capacity": {"makes": {need.item: 0.06}}, "measured": {"minutes": 10, "out5": {}}}
        other = block(need.item, 0.06)
        other["purpose"] = "mall"
        self.assertAlmostEqual(ds.have(pressed(6, 10, base=[CORE, LAB, starved, other]), need.item), 0.12)

    def test_repairs_and_walls_with_no_turret_it_can_make(self):
        with mock.patch("companion.perimeter.arms", return_value={"turrets": [], "walls": ["stone-wall"], "ammo": {}}),                 mock.patch("companion.ratios.unlocked", return_value={"repair-pack"}):
            items = [n.item for n in ds.draw(pressed(6, 10)).needs]
        self.assertEqual(sorted(items), ["repair-pack", "stone-wall"])

    def test_repair_packs_for_what_the_waves_damage(self):
        with mock.patch("companion.ratios.unlocked", return_value={"repair-pack", "gun-turret", "firearm-magazine"}):
            self.assertIn("repair-pack", [n.item for n in ds.draw(pressed(6, 10)).needs])

    def test_power_first_when_the_supply_is_urgent_and_nothing_makes_it_by_machine(self):
        planner = cp.CatalogPlanner.__new__(cp.CatalogPlanner)
        planner.designer, logs = None, []
        planner.log = logs.append
        s = pressed(6, 10)
        with mock.patch.object(cp, "available", lambda g, site, designer=False: g == "steam-power"):
            self.assertEqual(planner._defense_production_urgent(s), "steam-power")
        self.assertTrue(any("steam-power first" in line for line in logs))


class PickTests(unittest.TestCase):
    def test_the_most_short_item_gets_a_row_at_its_shortfall(self):
        got = ds.pick(pressed(6, 10), available=lambda g: False)
        self.assertEqual(got.goal, ds.GOAL)
        self.assertIn(got.need.item, [n.item for n in ds.draw(pressed(6, 10)).needs])
        self.assertLessEqual(got.build.rate, min(tier_builds.MAX_BLOCK_RATE, got.need.short) + 1e-9)

    def test_walls_go_to_the_wall_machinery(self):
        from companion import wall_block
        s = pressed(6, 10)
        with mock.patch.object(ds.Draw, "short", lambda self: [n for n in self.needs if n.item == "stone-wall"]):
            self.assertEqual(ds.pick(s, available=lambda g: g == wall_block.BRICKS).goal, wall_block.BRICKS)
            self.assertIsNone(ds.pick(s, available=lambda g: False))   # (walls wait: said, not built)

    def test_a_row_with_no_plates_made_for_it_waits_on_what_grows_them(self):
        # (D-18, death-world-waves-power: the turret row stood empty with no iron smelting, and no turret stood)
        from companion import plate_lead
        s = pressed(6, 10, smelting=False)
        self.assertEqual(ds.starved(s, "gun-turret"), "iron-plate")      # (most of a turret: 40 of its 50 plates)
        with mock.patch.object(plate_lead, "income_goal", return_value=("iron-smelting-expansion", "pairs")):
            got = ds.pick(s, available=lambda g: True)
            self.assertEqual(got.goal, "iron-smelting-expansion")
            self.assertIn("no iron-plate made yet", got.skipped[-1])
            iron = {"iron-smelting-expansion", "electric-iron-smelting"}
            other = ds.pick(s, available=lambda g: g not in iron)   # (nothing to grow it: the next item)
            self.assertNotEqual(other.goal, ds.GOAL)
            self.assertIn("gun-turret: no iron-plate made, and nothing that makes it can be built yet", other.skipped)
        fed = pressed(6, 10)
        self.assertIsNone(ds.starved(fed, "gun-turret"))
        # (sized to the row: 1 iron a second is short of a turret row at 0.1 a second, 4 a second of iron; D-19)
        self.assertIsNone(ds.starved(fed, "gun-turret", rate=0.01))
        self.assertEqual(ds.starved(fed, "gun-turret", rate=0.1), "iron-plate")
        self.assertEqual(ds.pick(fed, available=lambda g: False).goal, ds.GOAL)

    def test_steel_starved_waits_on_its_smelting_and_its_iron_is_counted(self):
        # (Codex's D-18 review: piercing rounds take steel, and steel takes iron)
        from companion import plate_lead
        s = pressed(6, 10)
        self.assertEqual(ds.plates(s, "steel-plate", 1.0), {"iron-plate": 5.0})
        with mock.patch.object(plate_lead, "income_goal", return_value=None):
            self.assertEqual(ds.feeder(s, "steel-plate", lambda g: g == "steel-smelting"), "steel-smelting")
            self.assertIsNone(ds.feeder(s, "steel-plate", lambda g: False))
        direct = ds.plates(s, "piercing-rounds-magazine", 1.0)
        with mock.patch.object(ds.Draw, "short", lambda self: [ds.Need("piercing-rounds-magazine", 1.0, 0.0, "")]):
            iron = ds.plate_draw(s)["iron-plate"]
        self.assertAlmostEqual(iron, direct["iron-plate"] + 5.0 * direct["steel-plate"])

    def test_what_its_defense_is_short_of_counts_in_the_plates_wanted(self):
        from companion import plate_lead
        s = pressed(6, 10, smelting=False)
        iron = ds.plate_draw(s)["iron-plate"]
        self.assertGreater(iron, 0)
        lead = plate_lead.lead_of(s, "iron-plate")
        self.assertGreaterEqual(lead.draw, iron - 1e-9)
        self.assertIn("its defense", lead.why)
        # (its shortfall only: once rows make it all, nothing more is wanted for it)
        with mock.patch.object(ds.Draw, "short", lambda self: []):
            self.assertEqual(ds.plate_draw(s), {})

    def test_urgent_while_the_waves_grow_and_the_supply_is_far_short(self):
        self.assertTrue(ds.urgent(pressed(6, 10)))
        self.assertFalse(ds.urgent(pressed(10, 6)))   # (shrinking waves: weighed, not urgent)


class GoalTests(unittest.TestCase):
    def ready(self, **extra):
        return pressed(6, 10, completed_purposes=["steam-power", "research-lab"], **extra)

    def test_offered_in_the_death_world_and_weighed_as_a_guard(self):
        ds._PICKED.clear()
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES):
            self.assertTrue(cp.available(ds.GOAL, self.ready()))
        self.assertTrue(goal_brain.guards(ds.GOAL))
        ds._PICKED.clear()

    def test_not_offered_before_power_or_its_research(self):
        ds._PICKED.clear()
        with mock.patch.object(cp.ratios, "unlocked", return_value=RECIPES):
            self.assertFalse(cp.available(ds.GOAL, pressed(6, 10)))   # (no steam power built)
        self.assertFalse(cp.available(ds.GOAL, self.ready()))        # (no assembler researched)

    def test_the_weighing_values_its_row_with_its_plates(self):
        ds._PICKED.clear()
        makes, takes, _ = goal_brain.full_flows(ds.GOAL, self.ready(), Book.vanilla())
        self.assertTrue(set(makes) & {n.item for n in ds.draw(self.ready()).needs})
        self.assertGreater(takes.get("iron-plate", 0), 0)
        ds._PICKED.clear()

    def test_the_row_is_planned_chest_fed_near_home(self):
        w = world()
        w["entities"].append({"t": "electric-pole", "n": "small-electric-pole", "f": w.get("force"),
                              "x": w["home"]["x"] + 0.5, "y": w["home"]["y"] + 0.5})
        # (what its rows are built of, unlocked too: tier_builds.locked_parts refuses a row of parts it cannot make)
        basics = RECIPES | {"transport-belt", "iron-chest", "wooden-chest", "long-handed-inserter"}
        with mock.patch.object(cp.ratios, "unlocked", return_value=basics):
            plan = ds.plan(self.ready(request_id="1:5", origin={"x": -432, "y": -416}), w)
        self.assertEqual(plan["purpose"], ds.GOAL)
        fed = {x["item"] for st in plan["stages"] for x in st["manual_supplies"]}
        self.assertIn("iron-plate", fed)
        self.assertNotIn("iron-gear-wheel", fed)   # (a turret's gears made beside it)
        # (its output chest kept: a turret line's belt starts there, D-19)
        from companion.bus import END_CHEST
        outs = plan["keep"]["outputs"]
        self.assertEqual(plan["keep"]["item"], plan["defense_production"]["item"])
        self.assertTrue(outs)
        supplied = {x["entity_id"] for st in plan["stages"] for x in st["manual_supplies"]}
        chests = {(e["position"]["x"], e["position"]["y"]) for e in plan["entities"]
                  if e["prototype"] == END_CHEST and e["id"] not in supplied}
        self.assertEqual({tuple(p) for p in outs}, chests)


class BaseWaveTests(unittest.TestCase):
    """D-13: every exposed part carries the full next wave, so the supply is sized by one wave on the base."""

    def test_one_wave_however_many_parts_carry_it(self):
        from types import SimpleNamespace
        part = SimpleNamespace(pressure=10.0, need=0)
        with mock.patch("companion.defense_brain.base_wave", return_value=10.0):
            with mock.patch("companion.defense_brain.assess", return_value=[part]):
                one = ammo(ds.draw(pressed(6, 10))).rate
            with mock.patch("companion.defense_brain.assess", return_value=[part, part, part]):
                three = ammo(ds.draw(pressed(6, 10))).rate
        self.assertAlmostEqual(one, three)

    def test_before_any_attack_the_biggest_wave_a_part_draws(self):
        from types import SimpleNamespace
        parts = [SimpleNamespace(pressure=4.0, need=0), SimpleNamespace(pressure=9.0, need=0)]
        with mock.patch("companion.defense_brain.base_wave", return_value=0.0),                 mock.patch("companion.defense_brain.assess", return_value=parts):
            d = ds.draw(pressed(6, 10))
        self.assertIn("waves of 9", ammo(d).why)


    def test_a_part_drawing_a_bigger_wave_than_those_seen_sizes_it(self):
        from types import SimpleNamespace
        with mock.patch("companion.defense_brain.base_wave", return_value=2.0),                 mock.patch("companion.defense_brain.assess", return_value=[SimpleNamespace(pressure=10.0, need=0)]):
            self.assertIn("waves of 10", ammo(ds.draw(pressed(6, 10))).why)


if __name__ == "__main__":
    unittest.main()
