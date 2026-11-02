"""The factory ledger (companion/ledger.py): segments, supply against claims, what is short."""
from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import blueprints as bp
from companion import ledger, strategist, upgrade_planner
from companion.catalog_planner import CatalogPlanner
from companion.ledger import Ledger, kind_of
from tests.test_blueprints import site


def block(id, makes=None, takes=None, kind=None, load=None, minutes=0, shares=None, used=None, made=None, **extra):
    out = {"id": id, "purpose": extra.pop("purpose", "bus-base"), "stage": extra.pop("stage", id),
           "capacity": {"makes": makes or {}, "takes": takes or {}}, **extra}
    plan = {k: v for k, v in (("kind", kind), ("load", load)) if v is not None}
    if plan:
        out["ledger"] = plan
    if minutes:
        out["measured"] = {"minutes": minutes, "shares": shares or {}, "takes": used or {}, "out": made or {}}
    return out


def iron_base(science=90.0):
    """The user's example: 120 iron a second, science lines that run nearly all the time, a mall."""
    return {"npc_id": 3, "base": [
        block("smelting-iron-plate", makes={"iron-plate": 120}, kind="smelting"),
        block("bus-science", takes={"iron-plate": science}, makes={"automation-science-pack": 2}, kind="science"),
        block("bus-mall", takes={"iron-plate": 20}, makes={"transport-belt": 2}, kind="mall"),
    ]}


class BudgetTests(unittest.TestCase):
    def test_the_users_example_supply_against_average_and_peak(self):
        book = Ledger.from_site(iron_base())
        b = book.budget("iron-plate")
        self.assertEqual(b.supply, 120)
        self.assertAlmostEqual(b.average, 90 * 0.95 + 20 * 0.15)       # 88.5 a second
        self.assertAlmostEqual(b.share, 88.5 / 120)                       # 74%
        self.assertEqual(b.peak, 110)                                     # even flat out, it fits
        ok, notes = book.fits({"iron-plate": 10}, load=0.15)
        self.assertTrue(ok, notes)
        self.assertEqual(book.short(), [])

    def test_a_segment_that_would_push_the_average_over_the_line_does_not_fit(self):
        book = Ledger.from_site({"base": iron_base(science=105)["base"][:2]})
        ok, notes = book.fits({"iron-plate": 20}, load=0.15)    # 99.75 + 3 of 120: 86%
        self.assertFalse(ok)
        self.assertIn("86%", notes[0])
        self.assertTrue(any("over supply" in n for n in notes))   # its peak is over too, noted only
        ok, notes = book.fits({"iron-plate": 10}, load=0.15)
        self.assertTrue(ok, notes)
        self.assertFalse(book.fits({"steel-plate": 1})[0])         # nothing makes steel

    def test_what_its_burners_burn_is_claimed_like_any_ingredient(self):
        # (a run spent most of its time mining coal by hand for forty burners the ledger never counted)
        smelting = block("iron", purpose="iron-smelting-expansion", kind="smelting", load=1.0,
                         machines={"burner-mining-drill": 4, "stone-furnace": 4})
        loop = block("loop", purpose="coal-loop", kind="mining", load=1.0, makes={"coal": 0.5},
                     machines={"burner-mining-drill": 2})
        book = Ledger([ledger.Segment.from_block(smelting), ledger.Segment.from_block(loop)])
        coal = book.budget("coal")
        self.assertAlmostEqual(coal.average, 4 * ledger.FUEL_DRAW["burner-mining-drill"]
                               + 4 * ledger.FUEL_DRAW["stone-furnace"])
        self.assertAlmostEqual(coal.supply, 0.5 - 2 * ledger.FUEL_DRAW["burner-mining-drill"])   # (its own netted)

        class Facts:   # the game's own numbers win (mods change burners)
            def fuel_per_second(self, name):
                return 0.1 if name == "stone-furnace" else None
        self.assertAlmostEqual(ledger.fuel_takes(smelting, Facts())["coal"],
                               4 * ledger.FUEL_DRAW["burner-mining-drill"] + 4 * 0.1)

    def test_what_a_segment_makes_and_uses_itself_cancels(self):
        cables = block("bus-green-circuits", makes={"electronic-circuit": 2, "copper-cable": 6},
                       takes={"copper-cable": 6, "iron-plate": 2, "copper-plate": 3})
        s = ledger.Segment.from_block(cables)
        self.assertEqual(s.makes, {"electronic-circuit": 2})
        self.assertEqual(s.takes, {"iron-plate": 2, "copper-plate": 3})

    def test_a_block_on_its_own_ore_is_not_short_of_it(self):
        # live check 2026-10-23: burner drills feeding their own furnaces read as short of iron ore that
        # nothing else makes; the block runs on its own drills, and that is its limit
        burner = block("iron-smelting-expansion", makes={"iron-ore": 1.0, "iron-plate": 1.25},
                       takes={"iron-ore": 1.25}, minutes=35, shares={"working": 0.9},
                       made={"iron-ore": 0.8, "iron-plate": 0.85}, used={"iron-ore": 0.85})
        s = ledger.Segment.from_block(burner)
        self.assertEqual((s.makes, s.takes), ({"iron-plate": 1.25}, {}))
        self.assertEqual((s.made, s.used), ({"iron-plate": 0.85}, {}))
        book = Ledger.from_site({"base": [burner]})
        self.assertEqual(book.short(), [])
        self.assertIsNone(book.summary())
        # what nothing makes at all is said plainly
        hand_fed = Ledger.from_site({"base": [block("bus-red-science", takes={"iron-gear-wheel": 1}, kind="science")]})
        self.assertIn("none of its blocks make it", hand_fed.summary())

    def test_an_intermediate_runs_as_hard_as_its_consumers_draw(self):
        book = Ledger.from_site({"base": [
            block("smelting-iron-plate", makes={"iron-plate": 15}),
            block("bus-gears", makes={"iron-gear-wheel": 2}, takes={"iron-plate": 4}),
            block("bus-red-science", makes={"automation-science-pack": 1}, takes={"iron-gear-wheel": 1}),
        ]})
        gears = next(s for s in book.segments if s.id == "bus-gears")
        self.assertAlmostEqual(gears.load, 0.95 / 2)                     # science draws 0.95 of its 2 a second
        self.assertAlmostEqual(book.budget("iron-plate").average, 4 * 0.95 / 2)

    def test_measured_numbers_replace_the_expected_ones_after_half_an_hour(self):
        young = block("bus-mall", takes={"iron-plate": 20}, kind="mall", minutes=10,
                      shares={"working": 0.9}, used={"iron-plate": 18})
        grown = dict(young, measured={"minutes": 45, "shares": {"working": 0.05}, "takes": {"iron-plate": 1}})
        smelting = block("smelting-iron-plate", makes={"iron-plate": 30})
        self.assertAlmostEqual(Ledger.from_site({"base": [smelting, young]}).budget("iron-plate").average, 3.0)
        self.assertAlmostEqual(Ledger.from_site({"base": [smelting, grown]}).budget("iron-plate").average, 1.0)
        # a producer that measurably stood without inputs or power gives that much less
        starved = block("smelting-iron-plate", makes={"iron-plate": 30}, minutes=40,
                        shares={"working": 0.5, "starved": 0.25, "no_power": 0.25})
        self.assertAlmostEqual(Ledger.from_site({"base": [starved]}).budget("iron-plate").supply, 15.0)

    def test_short_when_claimed_beyond_supply_or_starved_beside_flat_out_producers(self):
        over = Ledger.from_site({"base": [block("smelting-iron-plate", makes={"iron-plate": 10}),
                                          block("bus-science", takes={"iron-plate": 12}, kind="science")]})
        self.assertEqual([b.item for b in over.short()], ["iron-plate"])
        self.assertIn("iron-plate: 11.4 of 10 a second", over.summary())
        measured = Ledger.from_site({"base": [
            block("smelting-copper-plate", makes={"copper-plate": 10}, minutes=40, shares={"working": 0.95}),
            block("bus-green-circuits", makes={"electronic-circuit": 3}, takes={"copper-plate": 9}, minutes=40,
                  shares={"working": 0.6, "starved": 0.4}, used={"copper-plate": 5.4}),
            block("bus-red-science", takes={"electronic-circuit": 1}, kind="science")]})
        self.assertEqual([b.item for b in measured.short()], ["copper-plate"])
        self.assertIn("short of it: bus-green-circuits", measured.summary())
        self.assertIsNone(Ledger.from_site(iron_base()).summary())

    def test_blocks_built_for_someone_else_or_upgrades_stay_out(self):
        book = Ledger.from_site({"base": [
            block("smelting-iron-plate", makes={"iron-plate": 10}),
            block("order-gears", takes={"iron-plate": 50}, purpose="module"),
            block("upgrade-smelting", makes={"iron-plate": 10}, purpose="upgrade")]})
        self.assertEqual((book.budget("iron-plate").supply, book.budget("iron-plate").average), (10, 0))


class KindTests(unittest.TestCase):
    def test_kinds_from_purposes_and_bus_stages(self):
        cases = {("bus-base", "smelting-iron-plate-more"): "smelting", ("bus-base", "supply-copper-plate"): "smelting",
                 ("bus-base", "bus-mall-logistics"): "mall", ("bus-base", "bus-red-science"): "science",
                 ("bus-base", "bus-labs"): "labs", ("bus-base", "bus-green-circuits"): "intermediate",
                 ("bus-base", "lane-coal"): "bus", ("bus-base", "power"): "power",
                 ("blue-supply-automation", None): "intermediate", ("stone-supply", None): "mining",
                 ("research-lab", None): "labs", ("military-science-automation", None): "science",
                 ("plot-wall", None): "defense", ("module", None): "order", ("upgrade", None): "upgrade",
                 ("coal-loop", None): "mining", ("iron-smelting-expansion", None): "smelting"}
        for (purpose, stage), kind in cases.items():
            self.assertEqual(kind_of(purpose, stage), kind, (purpose, stage))

    def test_a_plan_carries_its_kind_and_expected_load(self):
        self.assertEqual(ledger.intent({"purpose": "red-science-automation"}), {"kind": "science", "load": 0.95})
        self.assertEqual(ledger.intent({"purpose": "bus-base", "base_plan": {"stage": "bus-gears"}}),
                         {"kind": "intermediate"})


class StagePortsTests(unittest.TestCase):
    def test_a_bus_stage_keeps_its_modules_ports_in_export_local_tiles(self):
        from companion.bus import Attached
        from companion.modules import EAST, WEST, Module, Port
        m = Module("gears")
        m.ports = [Port("iron-plate", "in", 0, 2, EAST, 4.0), Port("iron-gear-wheel", "out", 0, 5, WEST, 2.0)]
        m.makes = {"iron-gear-wheel": 2.0}
        got = ledger.stage_ports("bus-gears", [Attached(m, x=110, y=40)], dx=-100, dy=-30)
        self.assertEqual(got["ports"][0], {"item": "iron-plate", "dir": "in", "rate": 4.0, "x": 10, "y": 12})
        self.assertEqual((got["makes"], got["takes"]), ({"iron-gear-wheel": 2.0}, {"iron-plate": 4.0}))
        self.assertIsNone(ledger.stage_ports("bus-labs", [Attached(m, x=0, y=0)]))


class ReadersTests(unittest.TestCase):
    def tearDown(self):
        ledger.forget()

    def test_every_plan_carries_its_ledger_block(self):
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            plan = planner(site())
        # (and the library build it was made from, so its measurements go back to that build)
        self.assertEqual(plan["ledger"], {"kind": "smelting", "build": plan["blueprint_id"]})

    def test_the_strategist_hears_what_its_factory_is_short_of(self):
        short = {"npc_id": 3, "base": [block("smelting-iron-plate", makes={"iron-plate": 10}),
                                       block("bus-science", takes={"iron-plate": 12}, kind="science")]}
        ledger.remember(short)
        note = ledger.note_for(3)
        self.assertIn("iron-plate", note)
        self.assertIsNone(ledger.note_for(4))
        text = strategist.prompt({"reason": "routine", "situation": {}, "factory": note})
        self.assertIn("Your factory is short of: iron-plate", text)
        self.assertNotIn("factory", strategist.prompt({"reason": "routine", "situation": {}}))
        ledger.remember(iron_base())                                  # (npc 3 again: now nothing is short)
        self.assertIsNone(ledger.note_for(3))

    def test_supply_comes_first_once_an_item_is_short(self):
        short = [block("smelting-iron-plate", makes={"iron-plate": 10}),
                 block("bus-science", takes={"iron-plate": 12}, kind="science")]
        notes = []
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=notes.append)
            # (the ledger's own answer: the plates' lead, which comes after it, is left out here)
            lead = mock.patch("companion.plate_lead.goal", return_value=None)
            lead.start()
            self.addCleanup(lead.stop)
            goal, budget = planner.supply_first(site(base=short))
            self.assertEqual((goal, budget.item), ("iron-smelting-expansion", "iron-plate"))
            self.assertIsNone(planner.supply_first(site(base=iron_base()["base"])))
            # beside a bus, burner pairs are no answer: only an upgrade in place (none ready here)
            on_bus = site(base=short, base_plan={"stages": ["smelting-iron-plate", "bus-science"]})
            self.assertIsNone(planner.supply_first(on_bus))

    def test_an_upgrade_judges_measured_load_over_a_snapshot(self):
        snapshot_busy = {"stage": "bus-gears", "machines": {"assembling-machine-1": 4},
                         "status": {"working": 4}}
        quiet = dict(snapshot_busy, measured={"minutes": 40, "shares": {"working": 0.5}})
        flat_out = dict(snapshot_busy, status={"working": 1, "starved": 3},
                        measured={"minutes": 40, "shares": {"working": 0.95}})
        self.assertTrue(upgrade_planner._busy(snapshot_busy))
        self.assertFalse(upgrade_planner._busy(quiet))           # busy this moment, half idle over 30 minutes
        self.assertTrue(upgrade_planner._busy(flat_out))
        hungry = {"status": {"working": 4}, "measured": {"minutes": 40, "shares": {"starved": 0.35}}}
        self.assertAlmostEqual(upgrade_planner._starved(hungry), 0.35)
        self.assertIsNone(upgrade_planner._starved({"status": {"starved": 1, "working": 3}}))

    def test_an_upgrade_is_offered_only_where_a_flat_out_block_starves_one_that_takes_its_item(self):
        # (run 34, 2026-10-25: iron short of what building takes, no machine starving: the upgrade found no
        # bottleneck and the engineer waited on it)
        from unittest import mock
        from companion import catalog_planner
        busy = block("smelting-iron-plate", makes={"iron-plate": 10}, kind="smelting", minutes=40,
                     shares={"working": 0.95})
        fed = block("bus-science", takes={"iron-plate": 12}, kind="science", minutes=40, shares={"working": 0.9})
        hungry = block("bus-science", takes={"iron-plate": 12}, kind="science", minutes=40,
                       shares={"working": 0.5, "starved": 0.4})
        self.assertFalse(catalog_planner.upgrade_helps({"base": [busy, fed]}, "iron-plate"))
        self.assertTrue(catalog_planner.upgrade_helps({"base": [busy, hungry]}, "iron-plate"))
        on_bus = {"base_plan": {"stages": ["smelting-iron-plate", "bus-science"]}}
        with tempfile.TemporaryDirectory() as folder,                 mock.patch.object(catalog_planner, "available", side_effect=lambda p, s, **k: p == "upgrade"):
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            self.assertIsNone(planner.supply_first(site(base=[busy, fed], **on_bus)))
            goal, budget = planner.supply_first(site(base=[busy, hungry], **on_bus))
            self.assertEqual((goal, budget.item), ("upgrade", "iron-plate"))
            tried = site(base=[busy, hungry], failures={"upgrade": {"count": 1}}, **on_bus)
            self.assertIsNone(planner.supply_first(tried))                 # (it failed lately: not again yet)


if __name__ == "__main__":
    unittest.main()
