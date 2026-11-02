"""The build history (companion/build_history.py) and the weighing of builds for a job
(companion/build_choice.py): one library every engineer shares, kept across games, that gets better
with use. No AI is asked: the designer and System One are fakes."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from lupa.lua52 import LuaRuntime  # noqa: E402

from companion import blueprints as bp  # noqa: E402
from companion import build_choice, build_history, layout_planner  # noqa: E402
from companion.catalog_planner import DESIGN_NEW, CatalogPlanner  # noqa: E402
from test_blueprints import POWERED, FakeChooser, site  # noqa: E402
from test_rates import FACTS, PLATES  # noqa: E402

RECIPES = [PLATES, {"name": "burner-mining-drill"}, {"name": "stone-furnace"}]


def facts_site(request="1:1", **extra):
    """A request with the game's facts: what its machines make is known, so the job's need is too."""
    value = site(request=request, recipes=RECIPES, game_facts=FACTS, world_id="npc-world-1", tick=600000,
                 npc_name="Ada")
    value.update(extra)
    return value


def saved(library, pairs=2, **extra):
    """A build saved from the built-in layout at the first site (`pairs` drill and furnace pairs)."""
    source = site(**extra)
    return library.add(build_history.new_build(layout_planner.plan(source, pairs=pairs), source,
                                               source="starter-layout-planner", purpose="iron-smelting-start"))


def block(plan_id, build_id, *, minutes=40, out=None, shares=None, load=None, accepted=100, buildings=4,
          machines=None, capacity=None):
    """A measured block as the game exports it (mod/scripts/base_memory.lua)."""
    return {"id": plan_id, "purpose": "iron-smelting-start", "accepted": accepted, "buildings": buildings,
            "planned": 4, "machines": machines or {"burner-mining-drill": 2, "stone-furnace": 2},
            "capacity": {"makes": capacity or {"iron-plate": 0.625, "iron-ore": 0.5}, "takes": {"iron-ore": 0.625}},
            "measured": {"minutes": minutes, "out": out or {"iron-plate": 0.5, "iron-ore": 0.5},
                         "takes": {"iron-ore": 0.5}, "shares": shares or {"working": 0.8, "starved": 0.2}},
            "ledger": {"kind": "smelting", "build": build_id, **({"load": load} if load is not None else {})}}


class CountingCatalog(bp.Catalog):
    """A library that counts its writes."""

    def __init__(self, path):
        super().__init__(path)
        self.writes = 0

    def save(self, blueprint):
        self.writes = getattr(self, "writes", 0) + 1
        super().save(blueprint)


class RecordTests(unittest.TestCase):
    def setUp(self):
        build_history.forget()
        self.folder = tempfile.TemporaryDirectory()
        self.library = CountingCatalog(self.folder.name)
        self.build = saved(self.library)

    def tearDown(self):
        self.folder.cleanup()

    def test_an_old_library_file_loads_and_scores_as_before(self):
        old = {k: v for k, v in self.build.items() if k not in ("job", "lineage", "uses", "notes")}
        old["id"] = "0123456789abcdef"
        Path(self.folder.name, "0123456789abcdef.json").write_text(json.dumps(old), encoding="utf-8")
        reloaded = bp.Catalog(self.folder.name)
        build = reloaded.blueprints["0123456789abcdef"]
        self.assertEqual((build_history.notes(build), build_history.performance(build)), ([], 0.0))
        self.assertEqual(len(reloaded.candidates(site(ore_x=8, ore_y=12), "iron-smelting-start")), 2)

    def test_a_new_build_keeps_its_job(self):
        made = build_history.new_build(layout_planner.plan(facts_site()), facts_site(), source="x",
                                       purpose="iron-smelting-start")
        job = made["job"]
        # two burner drills (0.25 ore a second each) into stone furnaces (0.3125 plates a second each):
        # the furnaces wait on the drills, so the build gives what its drills mine
        self.assertEqual(job["makes"], {"iron-plate": 0.5})
        self.assertEqual(job["parts"], {"burner-mining-drill": 2, "stone-furnace": 2})
        self.assertEqual((job["resources"], job["kind"]), (["iron-ore"], "smelting"))
        self.assertAlmostEqual(job["burns"]["coal"], 0.12)

    def test_a_block_becomes_readings_of_what_was_expected_against_what_it_did(self):
        r = build_history.readings(block("p", self.build["id"], shares={"working": 0.5, "starved": 0.3,
                                                                        "backed_up": 0.2}), self.build)
        # a smelting column makes what its consumers take: time backed up is capacity it did not need
        self.assertNotIn("out:iron-plate", r)
        self.assertEqual(r["share:starved"], [0.3, 0.0])
        self.assertEqual(r["share:backed_up"], [0.2, 0.0])
        self.assertNotIn("share:working", r)
        self.assertEqual(r["standing"], [4, 4])
        self.assertEqual(r["machines:stone-furnace"], [2, 2])
        # a mall is meant to work a sixth of the time and wait the rest with its chests full
        mall = build_history.readings(block("m", self.build["id"], load=0.15, shares={"working": 0.6, "backed_up": 0.4}),
                                      self.build)
        self.assertEqual(mall["share:backed_up"], [0.4, 0.85])
        self.assertAlmostEqual(mall["out:iron-plate"][1], 0.625 * 0.15, places=4)

    def test_any_measured_gap_becomes_a_note_with_its_evidence_even_a_state_never_named(self):
        for n, shares in enumerate([{"working": 0.6, "waiting_for_train": 0.4}, {"working": 0.95},
                                    {"working": 0.56, "waiting_for_train": 0.44}]):
            self.library.record(self.build["id"], success=True)
            use = build_history.use_key("w", n, f"p{n}", 100)
            self.build.setdefault("uses", []).append({"key": use, "world": "w", "engineer": f"E{n}", "minutes": 40,
                                                      "readings": build_history.readings(
                                                          block(f"p{n}", self.build["id"], shares=shares), self.build)})
        notes = build_history.derive_notes(self.build)
        note = next(n for n in notes if n["key"] == "share:waiting_for_train")
        self.assertEqual((note["seen"], note["of"]), (2, 3))
        self.assertIn("waiting for train 42% of the time", note["text"])
        self.assertEqual([e["engineer"] for e in note["evidence"]], ["E0", "E2"])

    def test_the_users_examples_are_gaps_the_readings_find_by_themselves(self):
        # prone to bottlenecks: its furnaces starved; needs faster inserters: it made less than its machines
        # could while nothing held it up; destroyed or upgraded: machines missing or new ones in their place
        # could be smaller: its output went untaken; for a planned load, it made less than planned
        b = block("p", self.build["id"], shares={"working": 0.42, "starved": 0.38, "backed_up": 0.2},
                  buildings=3, machines={"burner-mining-drill": 1, "stone-furnace": 2, "steel-furnace": 1})
        r = build_history.readings(b, self.build)
        off = {k for k, (m, e) in r.items() if abs(build_history.gap(k, m, e)) > build_history.TOLERANCE}
        self.assertEqual(off, {"share:starved", "share:backed_up", "standing", "machines:burner-mining-drill",
                               "machines:steel-furnace"})
        planned = build_history.readings(block("p", self.build["id"], load=0.95, out={"iron-plate": 0.3}), self.build)
        self.assertLess(build_history.gap("out:iron-plate", *planned["out:iron-plate"]), -build_history.TOLERANCE)
        self.assertEqual(build_history.describe("machines:steel-furnace", 1, 0), "holds 1 steel-furnace it was not built with")

    def test_a_used_block_is_written_once_when_seen_again_when_measured_and_not_on_every_sample(self):
        s = facts_site(base=[block("bp-1", self.build["id"], minutes=3)])
        start = self.library.writes
        build_history.attribute(self.library, s)
        self.assertEqual(self.library.writes - start, 1)                 # its use, first seen
        build_history.attribute(self.library, {**s, "tick": s["tick"] + 3600})
        self.assertEqual(self.library.writes - start, 1)                 # a sample later: nothing to write
        mature = {**s, "tick": s["tick"] + 40 * 3600, "base": [block("bp-1", self.build["id"], minutes=40)]}
        build_history.attribute(self.library, mature)
        build_history.attribute(self.library, {**mature, "tick": mature["tick"] + 3600,
                                               "base": [block("bp-1", self.build["id"], minutes=41)]})
        self.assertEqual(self.library.writes - start, 2)                 # measured once it matured
        build_history.attribute(self.library, {**mature, "base": [block("bp-1", self.build["id"], minutes=80)]})
        self.assertEqual(self.library.writes - start, 3)                 # and again at twice that
        use = build_history.uses(self.library.blueprints[self.build["id"]])[0]
        self.assertEqual((use["world"], use["npc"], use["engineer"], use["minutes"]), ("npc-world-1", 1, "Ada", 80))
        self.assertEqual(use["readings"]["share:starved"], [0.2, 0.0])
        note = build_history.notes(self.library.blueprints[self.build["id"]])[0]
        self.assertEqual(note["key"], "share:starved")
        reloaded = bp.Catalog(self.folder.name).blueprints[self.build["id"]]   # kept across games
        self.assertEqual(build_history.notes(reloaded)[0]["text"], note["text"])

    def test_a_block_gone_before_it_was_measured_is_a_gap_and_a_reload_is_not(self):
        s = facts_site(base=[block("bp-1", self.build["id"], minutes=5, accepted=590000)])
        build_history.attribute(self.library, s)
        build_history.attribute(self.library, {**s, "tick": 500000, "base": []})      # a save loaded: earlier
        self.assertIsNone(build_history.uses(self.library.blueprints[self.build["id"]])[0].get("gone"))
        build_history.attribute(self.library, {**s, "tick": 600000, "base": [block("bp-1", self.build["id"], minutes=5,
                                                                                    accepted=590000)]})
        build_history.attribute(self.library, {**s, "tick": 610000, "base": []})       # retired or destroyed
        use = build_history.uses(self.library.blueprints[self.build["id"]])[0]
        self.assertEqual(use["gone"], 5)
        self.assertIn("gone after 5 minutes", build_history.notes(self.library.blueprints[self.build["id"]])[0]["text"])
        # a base as long as the export can be may simply have dropped its oldest blocks
        other = saved(self.library, pairs=1)
        full = [block(f"x{i}", None) for i in range(63)] + [block("bp-2", other["id"], minutes=5)]
        build_history.attribute(self.library, facts_site(base=full, tick=620000))
        build_history.attribute(self.library, facts_site(base=full[:-1] + [block("x63", None)], tick=630000))
        self.assertIsNone(build_history.uses(self.library.blueprints[other["id"]])[0].get("gone"))

    def test_how_its_uses_ran_moves_its_score_so_a_better_variant_wins(self):
        parent, variant = self.build, saved(self.library, pairs=3)
        for build, starved in ((parent, 0.5), (variant, 0.02)):
            for n in range(3):
                build.setdefault("uses", []).append({"key": f"k{n}", "minutes": 40, "readings": {
                    "share:starved": [starved, 0.0], "out:iron-plate": [0.5 - starved / 2, 0.5]}})
        self.assertLess(build_history.performance(parent), 0)
        self.assertGreater(build_history.performance(variant), 0)
        spot = {"distance": 10, "relocations": 0}
        self.assertGreater(bp.score(variant, spot), bp.score(parent, spot))

    def test_a_build_that_did_not_finish_is_noted_with_the_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=lambda _: None)
            plan = planner(facts_site(request="1:1"))
            planner.record("1:1", {"status": "deferred", "reason": "designer:rejected:overlap", "accepted_tick": None,
                                   "plan_id": None})
            build = planner.catalog.blueprints[plan["blueprint_id"]]
        use = build_history.uses(build)[0]
        self.assertEqual((use["outcome"], use["world"], use["engineer"]), ("blocked", "npc-world-1", "Ada"))
        self.assertEqual(build_history.capability(use), 0.0)
        self.assertIn("its build did not finish (designer:rejected:overlap)", build_history.notes(build)[0]["text"])


class WeighingTests(unittest.TestCase):
    def setUp(self):
        build_history.forget()
        self.folder = tempfile.TemporaryDirectory()
        self.library = bp.Catalog(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def report(self, build, s=None, reference=True):
        s = s or facts_site()
        reports = build_choice.weigh(self.library, s, "iron-smelting-start",
                                     reference=layout_planner.plan(s) if reference else None)
        return next(r for r in reports if r.id == build["id"])

    def test_a_build_making_too_little_needs_expanding_and_one_making_far_too_much_could_be_smaller(self):
        small, big = saved(self.library, pairs=1), saved(self.library, pairs=4, ore_x=36,
                                                         resources=[{"resource": "iron-ore",
                                                                     "rows": [[y, 36, 50] for y in range(26, 40)]}])
        s = facts_site(resources=[{"resource": "iron-ore", "rows": [[y, 36, 50] for y in range(26, 40)]}])
        short = self.report(small, s)
        self.assertIn("makes 0.25 of the 0.5 iron-plate a second needed: it needs expanding",
                      [f.text for f in short.gaps])
        self.assertTrue(short.usable)
        large = self.report(big, s)
        self.assertEqual([f.check for f in large.gaps], ["size"])
        self.assertIn("2.0 times what is needed", large.gaps[0].text)
        # the need follows the ledger when the base is short of the item
        hungry = {"id": "lab", "purpose": "research-lab", "ledger": {"kind": "labs", "takes": {"iron-plate": 2.0}}}
        need, source = build_choice.need_for(dict(s, base=[hungry]), bp.from_plan(layout_planner.plan(s), s, source="x"))
        self.assertEqual(source, "the ledger")
        self.assertAlmostEqual(need["iron-plate"], 0.95 * 2 / 0.85, places=3)
        self.assertEqual(build_choice.need_for(dict(s, need={"iron-plate": 3}))[0], {"iron-plate": 3.0})

    def test_a_build_that_does_not_fit_reports_how_near_it_comes(self):
        build = saved(self.library, pairs=2)
        thin = facts_site(resources=[{"resource": "iron-ore", "rows": [[y, 40, 42] for y in range(29, 32)]}])
        r = self.report(build, thin)
        self.assertFalse(r.usable)
        self.assertTrue(r.close)
        fit = next(f for f in r.findings if f.check == "fit")
        self.assertTrue(fit.blocks)
        self.assertRegex(fit.text, r"at the nearest spot \d+ of the 8 tiles its drills need have no ore")
        self.assertIsNotNone(r.near)
        copper = facts_site(resources=[{"resource": "copper-ore", "rows": [[y, 40, 47] for y in range(29, 35)]}])
        far = self.report(build, copper, reference=False)
        self.assertFalse(far.close)

    def test_parts_that_cannot_be_made_stop_it_and_parts_to_make_first_only_cost_time(self):
        build = saved(self.library, pairs=2)
        # the live game, 2026-10-23: no saved stone or smelting layout "fit" once the engineers had put their
        # carried drills down, though the game makes what a stage lacks; now it costs a little, and serves
        empty = facts_site(inventory={"iron-plate": 40, "stone": 20},
                           recipes=RECIPES + [{"name": "iron-gear-wheel", "ingredients": [{"name": "iron-plate", "amount": 2}],
                                                "products": [{"name": "iron-gear-wheel", "amount": 1}]}])
        r = self.report(build, empty)
        self.assertTrue(r.usable)
        self.assertGreater(r.cost, 0)
        self.assertIn("its first stage needs 1 more burner-mining-drill, 1 more stone-furnace made first", r.summary())
        # a chest is made of wood: the trees there give it (the game says what its trees and rocks give)
        chest = {"name": "wooden-chest", "ingredients": [{"name": "wood", "amount": 2}],
                 "products": [{"name": "wooden-chest", "amount": 1}]}
        bare = facts_site(inventory={}, recipes=[chest])
        self.assertFalse(build_choice.can_make("wooden-chest", bare))
        self.assertTrue(build_choice.can_make("wooden-chest", dict(bare, minable=["wood"])))
        locked = self.report(build, facts_site(recipes=[PLATES], inventory={}))
        self.assertFalse(locked.usable)
        self.assertIn("needs what cannot be made here yet: burner-mining-drill, stone-furnace", locked.summary())

    def test_a_build_that_keeps_failing_is_not_reused_as_it_is(self):
        build = saved(self.library, pairs=2)
        for _ in range(3):
            self.library.record(build["id"], success=False, reason="drill_not_on_resource")
        r = self.report(build)
        self.assertFalse(r.usable)
        self.assertIn("failed 3 of its 3 builds, last: drill_not_on_resource", r.summary())

    def test_notes_are_gaps_and_one_an_adaptation_failed_to_close_is_not_the_layouts(self):
        build = saved(self.library, pairs=2)
        build["notes"] = [{"key": "share:starved", "text": "short of inputs 40% of the time (expected 0%)",
                           "gap": 0.4, "seen": 3, "of": 3}]
        r = self.report(build)
        self.assertEqual([f.key for f in r.gaps], ["share:starved"])
        build["lineage"] = {"parent": "p", "variants": [], "addressed": ["share:starved"]}
        self.assertEqual(self.report(build).gaps, [])

    def test_a_variant_still_short_of_the_need_it_was_adapted_for_is_not_adapted_again_for_it(self):
        small = saved(self.library, pairs=1)
        small["lineage"] = {"parent": "p", "variants": [], "addressed": ["out:iron-plate"], "need": {"iron-plate": 0.5}}
        self.assertEqual(self.report(small).to_adapt, [])                  # the designer could not get it there
        bigger = facts_site(need={"iron-plate": 1.0})                      # a bigger need is a new question
        self.assertEqual([f.key for f in self.report(small, bigger).to_adapt], ["out:iron-plate"])

    def test_a_new_question_plugs_in(self):
        build = saved(self.library, pairs=2)

        def daylight(job, report):
            return [build_choice.Finding("daylight", "too dark here", gap=0.5)]
        reports = build_choice.weigh(self.library, facts_site(), "iron-smelting-start",
                                     checks=build_choice.CHECKS + [daylight])
        self.assertEqual([f.text for f in reports[0].gaps], ["too dark here"])

    def test_every_build_of_the_job_is_weighed_whatever_purpose_it_was_saved_for(self):
        start = saved(self.library, pairs=2)
        s = facts_site(purpose="iron-smelting-expansion")
        reference = layout_planner.plan(s, pairs=4)
        ids = [r.id for r in build_choice.weigh(self.library, s, "iron-smelting-expansion", reference=reference)]
        self.assertEqual(ids, [start["id"]])          # the same ore into the same plates
        copper = layout_planner.plan(facts_site(resources=[{"resource": "copper-ore",
                                                            "rows": [[y, 42, 47] for y in range(29, 35)]}]),
                                     resource="copper-ore")
        self.assertEqual(build_choice.weigh(self.library, s, "copper-smelting-start", reference=copper), [])


class DecisionTests(unittest.TestCase):
    def setUp(self):
        build_history.forget()
        self.folder = tempfile.TemporaryDirectory()
        self.library = bp.Catalog(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def reports(self, s=None):
        s = s or facts_site()
        return build_choice.weigh(self.library, s, "iron-smelting-start", reference=layout_planner.plan(s))

    def test_the_rules_reuse_what_fits_and_works(self):
        good = saved(self.library, pairs=2)
        saved(self.library, pairs=1, ore_x=30)
        choice = build_choice.decide(self.reports(), designer=True, purpose="iron-smelting-start")
        self.assertEqual((choice.kind, choice.report.id), ("reuse", good["id"]))

    def test_with_gaps_the_closest_is_adapted_and_with_nothing_close_one_is_designed(self):
        small = saved(self.library, pairs=1)
        choice = build_choice.decide(self.reports(), designer=True, purpose="iron-smelting-start")
        self.assertEqual((choice.kind, choice.report.id), ("adapt", small["id"]))
        # another engineer adapting it right now: this one does not ask the designer for the same thing
        later = build_choice.decide(self.reports(), designer=True, busy={small["id"]}, purpose="iron-smelting-start")
        self.assertEqual(later.kind, "builtin")
        # before power (no designer): the built-in layout, sized to the job, over a saved one too small
        self.assertEqual(build_choice.decide(self.reports(), designer=False).kind, "builtin")
        self.assertEqual(build_choice.decide([], designer=True).kind, "new")
        self.assertEqual(build_choice.decide([], designer=False).kind, "builtin")

    def test_system_ones_options_carry_each_builds_report_and_notes(self):
        small = saved(self.library, pairs=1)
        small["notes"] = [{"key": "share:no_fuel", "text": "out of fuel 30% of the time (expected 0%)", "gap": 0.3,
                           "seen": 2, "of": 2}]
        options = build_choice.options(self.reports(), designer=True)
        self.assertEqual([o["id"] for o in options], [f"bp-{small['id']}", f"adapt-{small['id']}"])
        self.assertIn("Report: ", options[0]["description"])
        self.assertIn("out of fuel 30% of the time (expected 0%), in 2 of 2 uses", options[0]["description"])
        self.assertIn("it needs expanding", options[1]["description"])
        self.assertEqual([o["id"] for o in build_choice.options(self.reports(), designer=False)], [f"bp-{small['id']}"])


class PlannerTests(unittest.TestCase):
    def setUp(self):
        build_history.forget()

    def test_a_build_with_gaps_is_adapted_from_itself_and_kept_as_its_variant(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            small = saved(library, pairs=1)
            asked, said = [], []

            def designer(s):
                asked.append(s)
                return layout_planner.plan(s, pairs=2)
            planner = CatalogPlanner(library, designer=designer, chooser=FakeChooser("adapt-"), log=said.append)
            plan = planner(facts_site(request="2:1", **POWERED))
            brief = asked[0]["start_from"]
            self.assertEqual(brief["parent"], small["id"])
            self.assertIn(f"Start from saved layout {small['id'][:8]}", brief["text"])
            self.assertIn("it needs expanding", brief["text"])
            self.assertIn("drill-1 burner-mining-drill", brief["text"])
            variant = library.blueprints[plan["blueprint_id"]]
            self.assertEqual(variant["lineage"]["parent"], small["id"])
            self.assertEqual(variant["lineage"]["addressed"], ["out:iron-plate"])
            self.assertEqual(variant["lineage"]["need"], {"iron-plate": 0.5})
            self.assertEqual(library.blueprints[small["id"]]["lineage"]["variants"], [variant["id"]])
            self.assertEqual(plan["ledger"]["build"], variant["id"])
            self.assertTrue(any(f"layout {small['id'][:8]} adapted into" in line for line in said), said)
            # the next engineer finds the variant, which fits and works: no designer (and the rules, or
            # System One, are not offered the parent to adapt again for what its variant was made for)
            planner.chooser = None
            again = planner(facts_site(request="3:1", **POWERED))
            parent = next(r for r in build_choice.weigh(library, facts_site(), "iron-smelting-start",
                                                        reference=layout_planner.plan(facts_site()))
                          if r.id == small["id"])
            self.assertTrue(parent.gaps)
            self.assertEqual(parent.to_adapt, [])
            self.assertEqual((len(asked), again["blueprint_id"]), (1, variant["id"]))

    def test_the_games_status_and_its_measured_block_are_one_use_of_the_build(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            build = saved(library, pairs=2)
            planner = CatalogPlanner(library, log=lambda _: None)
            plan = planner(facts_site(request="2:1"))
            self.assertEqual((plan["blueprint_id"], plan["ledger"]["build"]), (build["id"], build["id"]))
            planner.record("2:1", {"status": "complete", "plan_id": plan["plan_id"], "accepted_tick": 1700,
                                   "stage_completed_ticks": [4700, 9000], "completed_tick": 9000})
            later = facts_site(request="2:2", purpose="choose", tick=200000,
                               base=[block(plan["plan_id"], build["id"], accepted=1700, minutes=45)])
            build_history.attribute(library, later)
            uses = build_history.uses(library.blueprints[build["id"]])
        self.assertEqual(len(uses), 1)
        self.assertEqual((uses[0]["outcome"], uses[0]["first_output_s"], uses[0]["minutes"]), ("complete", 50, 45))

    def test_without_system_one_the_rules_adapt_and_without_a_designer_the_built_in_serves(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            small = saved(library, pairs=1)
            asked = []
            planner = CatalogPlanner(library, designer=lambda s: asked.append(s) or layout_planner.plan(s, pairs=2),
                                     log=lambda _: None)
            planner(facts_site(request="2:1", **POWERED))
            self.assertEqual(asked[0]["start_from"]["parent"], small["id"])
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            small = saved(library, pairs=1)
            plan = CatalogPlanner(library, log=lambda _: None)(facts_site(request="2:1", **POWERED))
            self.assertNotEqual(plan["blueprint_id"], small["id"])
            self.assertEqual(len([e for e in plan["entities"] if e["prototype"] == "stone-furnace"]), 2)

    def test_a_failed_adaptation_falls_back_and_frees_the_build_for_others(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            saved(library, pairs=1)
            said = []
            planner = CatalogPlanner(library, designer=lambda s: 1 / 0, log=said.append)
            plan = planner(facts_site(request="2:1", **POWERED))
            self.assertTrue(plan["entities"])
            self.assertTrue(any("designer failed" in line for line in said), said)
            self.assertEqual(build_choice.busy(library), set())

    def test_a_fitting_build_is_reused_without_the_designer_when_its_stage_must_be_made_first(self):
        # the live game's stone and smelting starts: the engineer carried none of the parts, but can make them
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            build = saved(library, pairs=2)
            asked = []
            planner = CatalogPlanner(library, designer=lambda s: asked.append(s) or layout_planner.plan(s),
                                     log=lambda _: None)
            plan = planner(facts_site(request="2:1", inventory={"iron-plate": 50, "stone": 20}, **POWERED))
            self.assertEqual((asked, plan["blueprint_id"]), ([], build["id"]))

    def test_system_one_sees_reuse_adapt_and_new_and_the_layout_question_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            library = bp.Catalog(folder)
            saved(library, pairs=1)
            chooser = FakeChooser("bp-")
            planner = CatalogPlanner(library, designer=lambda s: layout_planner.plan(s), chooser=chooser,
                                     log=lambda _: None)
            planner(facts_site(request="2:1", **POWERED))
        self.assertEqual([o.split("-")[0] for o in chooser.seen[-1]], ["bp", "adapt", DESIGN_NEW.split("-")[0]])
        from companion.catalog_planner import DecisionsChooser

        class Provider:
            def decide(self, request):
                self.request = request
                return {"selected_id": request["candidates"][0]["id"], "probabilities": {}}
        provider = Provider()
        DecisionsChooser(provider=provider).choose({"npc_name": "Ada"}, [{"id": "bp-x", "description": "x"}])
        self.assertIn("adapting one has the designer change only what its gaps need", provider.request["context"])


class DesignerBriefTests(unittest.TestCase):
    def test_the_designer_is_asked_to_start_from_the_saved_layout_within_its_budget(self):
        from test_codex_backend import FakeCodex
        from test_designer_planner import site as designer_site, wire
        from designer_llm.proposal_schema import to_wire
        from companion.designer_planner import GOAL_CHARS, REPAIR_ROOM, LLMPlanner, build_context
        s = designer_site()
        context = build_context(s)
        good = layout_planner.plan(s)
        brief = {"text": "Start from saved layout abcdef12 and change only what these gaps need:\n- x", "entities": 30,
                 "parent": "abcdef1234567890", "addressed": []}
        codex = FakeCodex(to_wire(wire(context, good)))
        plan = LLMPlanner(model="gpt-6.1-sol", codex=codex, log=lambda _: None)({**s, "start_from": brief})
        self.assertEqual([e["id"] for e in plan["entities"]], [e["id"] for e in good["entities"]])
        prompt = codex.asked[0][0]
        self.assertIn("Start from saved layout abcdef12", prompt)
        self.assertIn('"max_entities":38', prompt.replace(" ", ""))    # room for all its parts and a few more
        long = {**brief, "text": "y" * 5000}
        codex = FakeCodex(to_wire(wire(context, good)))
        LLMPlanner(model="gpt-6.1-sol", codex=codex, log=lambda _: None)({**s, "start_from": long})
        goal = json.loads(codex.asked[0][0].split("The request and the observed world, as JSON:\n", 1)[1])["request"]["goal"]
        self.assertLessEqual(len(goal), GOAL_CHARS - REPAIR_ROOM)


class GameSideTests(unittest.TestCase):
    """The mod keeps a plan's build id with its block and exports it (Lua, run through lupa)."""

    def test_the_game_keeps_the_build_id_from_the_plans_ledger_block_and_drops_a_bad_one(self):
        source = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        start, end = source.index("local LEDGER_ITEMS"), source.index("local function base_plan_copy")
        lua = LuaRuntime(unpack_returned_tuples=True)
        copy_of = lua.execute("local function finite(v) return type(v)=='number' and v==v and math.abs(v)<1e6 end\n"
                              + source[start:end] + "\nreturn ledger_copy")
        origin = lua.eval("{x=0, y=0}")
        self.assertEqual(copy_of(lua.eval("{kind='smelting', build='9dd44aeffdb48e74'}"), origin).build, "9dd44aeffdb48e74")
        for bad in ("'drop table; --'", "string.rep('a', 65)", "12"):
            self.assertIsNone(copy_of(lua.eval("{kind='smelting', build=" + bad + "}"), origin).build)

    def test_a_block_exports_its_build_its_accepted_tick_and_every_state_its_machines_were_in(self):
        from test_base_memory import LedgerTests   # (its fake machines)
        lua = LedgerTests().lua()
        lua.execute("""
            a = machine('assembling-machine', gears)
            p = plan('gears', {a})
            p.accepted_tick = 1234
            p.ledger = {kind='intermediate', build='9dd44aeffdb48e74'}
            M.sample(p, 1000)
            a.status = 99                              -- a state the companion has no word for
            M.sample(p, 1000 + 3600)
            npc = {design_history={p}, design={status='complete'}}
            b = M.blocks(npc, {x=0, y=0})[1]
        """)
        self.assertEqual((lua.eval("b.ledger.build"), lua.eval("b.accepted")), ("9dd44aeffdb48e74", 1234))
        self.assertAlmostEqual(lua.eval("b.measured.shares.other"), 1.0)


if __name__ == "__main__":
    unittest.main()
