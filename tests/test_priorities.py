"""Priorities on the factory ledger (companion/priorities.py, mod/scripts/priorities.lua): each item and section
weighed from the engineer's situation, held for a while, set by its strategist, and acted on only when it
matters. The user's examples are tests here, not the design: the model reads signals."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import blueprints as bp  # noqa: E402
from companion import ledger, priorities, strategist  # noqa: E402
from companion.catalog_planner import CatalogPlanner  # noqa: E402
from companion.priorities import MINUTE, Pull  # noqa: E402
from tests.test_blueprints import site  # noqa: E402
from tests.test_ledger import block  # noqa: E402

RED = [{"name": "logistics", "packs": ["automation-science-pack"]}]
BLUE = [{"name": "advanced-material-processing",
         "packs": ["automation-science-pack", "logistic-science-pack", "chemical-science-pack"]}]
TEAM = {"area": [-200, -200, 200, 200], "plot": [0, 0, 64, 64]}
DEFENSE = {"ammo": {"bullet": ["firearm-magazine"]}, "turrets": [{"name": "gun-turret"}],
           "walls": [{"name": "stone-wall"}], "repair": [{"name": "repair-pack"}], "evolution": 0.2}


def steel(research=RED, **extra):
    """The user's example: iron runs low because a steel line takes it, and science takes the rest."""
    return {"npc_id": 7, "npc_name": "Ada", "tick": 100 * MINUTE, "focus": {"research": research}, "base": [
        block("smelting-iron-plate", makes={"iron-plate": 10}, kind="smelting"),
        block("bus-science", takes={"iron-plate": 6}, makes={"automation-science-pack": 1}, kind="science"),
        block("steel-smelting", purpose="steel-smelting", stage=None, takes={"iron-plate": 8},
              makes={"steel-plate": 1.6}, minutes=40, shares={"working": 0.9}, used={"iron-plate": 7.5})],
        **extra}


def fighting(seconds_ago=30, starved_ammo=False, **extra):
    """A death world: science and an ammunition line share iron, and biters hit the plot `seconds_ago`."""
    ammo = block("bus-ammo", takes={"iron-plate": 4}, makes={"firearm-magazine": 1}, kind="ammo",
                 **({"minutes": 40, "shares": {"working": 0.6, "starved": 0.4}, "used": {"iron-plate": 2.4}}
                    if starved_ammo else {}))
    attacks = [] if seconds_ago is None else [{"tick": 100 * MINUTE - seconds_ago * 60, "units": {"small-biter": 12}}]
    return {"npc_id": 9, "npc_name": "Bob", "tick": 100 * MINUTE, "team": TEAM, "defense": DEFENSE,
            "threat_board": {"attacks": attacks, "evolution": 0.2}, "focus": {"research": RED},
            "base": [block("smelting-iron-plate", makes={"iron-plate": 8}, kind="smelting"),
                     block("bus-science", takes={"iron-plate": 8}, makes={"automation-science-pack": 1},
                           kind="science"), ammo], **extra}


def only(found, item="iron-plate"):
    return next(s for s in found if s.budget.item == item)


class Fresh(unittest.TestCase):
    def setUp(self):
        priorities.forget()

    def tearDown(self):
        priorities.forget()


class ExampleTests(Fresh):
    def test_iron_for_steel_waits_while_nothing_plans_steel_and_comes_back_when_research_needs_it(self):
        short = only(priorities.shortfalls(steel()))
        self.assertTrue(short.held)
        self.assertEqual(short.unfed, "steel-smelting")         # science gets its iron first
        self.assertIn(short.priority.level, ("low", "ignore"))
        self.assertEqual(short.times_held, 1)
        # blue science researched next: steel goes into its engines, so the same shortage now counts (once
        # that has held a couple of minutes: things take time)
        self.assertTrue(only(priorities.shortfalls(steel(BLUE, tick=100 * MINUTE + 60))).held)
        again = only(priorities.shortfalls(steel(BLUE, tick=103 * MINUTE)))
        self.assertFalse(again.held)
        self.assertTrue(again.raised)
        self.assertIn("engine-unit", again.priority.why())
        self.assertIn("raised again", again.line())

    def test_while_fighting_science_waits_and_ammunition_comes_first(self):
        hot = only(priorities.shortfalls(fighting(30)))
        self.assertEqual(hot.unfed, "bus-science")             # ammunition is fed first
        self.assertTrue(hot.held)
        self.assertIn("attacked", hot.priority.why())
        # in the clear: science matters again, and what goes short is the ammunition line
        calm = only(priorities.shortfalls(fighting(None, tick=115 * MINUTE)))
        self.assertEqual(calm.unfed, "bus-ammo")
        self.assertFalse(calm.held)                             # (a death world: survival is never low)
        # the ammunition line itself measurably starved in the fight: at once, whatever the order
        priorities.forget()
        starving = only(priorities.shortfalls(fighting(30, starved_ammo=True)))
        self.assertEqual(starving.unfed, "bus-ammo")
        self.assertTrue(starving.priority.urgent)
        self.assertEqual(starving.priority.level, "top")

    def test_a_fight_spares_what_the_fight_needs_all_the_way_down(self):
        prio = priorities.assess(fighting(30), hold=False)
        book = prio.ctx.book
        ammo, science, smelting = (next(s for s in book.segments if s.id == n)
                                   for n in ("bus-ammo", "bus-science", "smelting-iron-plate"))
        self.assertGreater(prio.section(ammo).weight, 0.85)
        self.assertLess(prio.section(science).weight, priorities.ACT)
        self.assertGreater(prio.section(smelting).weight, 0.6)   # its iron feeds the ammunition
        calm = priorities.assess(fighting(None), hold=False)
        self.assertGreater(calm.section("bus-science").weight, 0.6)
        # it cools over minutes, not at once
        cooling = priorities.assess(fighting(6 * 60), hold=False)
        self.assertLess(prio.section("bus-science").weight, cooling.section("bus-science").weight)
        self.assertLess(cooling.section("bus-science").weight, calm.section("bus-science").weight)


class SignalTests(Fresh):
    def test_the_path_to_space_is_always_a_baseline_and_its_next_step_counts_most(self):
        space = {"left": 40, "total": 100, "packs": {"automation-science-pack": 2000, "chemical-science-pack": 6000},
                 "next": {"techs": ["sulfur-processing"], "packs": ["chemical-science-pack"]}}
        prio = priorities.assess({"npc_id": 1, "base": [], "focus": {"space": space}}, hold=False)
        blue = prio.of("chemical-science-pack")
        self.assertGreaterEqual(blue.weight, 0.6)
        self.assertIn("the next step to space (sulfur-processing)", blue.why(3))
        near = priorities.assess({"npc_id": 1, "base": [], "focus": {"space": dict(space, left=5)}}, hold=False)
        self.assertGreater(near.of("rocket-part").weight, prio.of("rocket-part").weight)
        # without the game's tree (an older mod): the packs named after their techs
        old = priorities.assess({"npc_id": 1, "base": [], "researched": ["logistic-science-pack"]}, hold=False)
        self.assertIn("the next step to space", old.of("chemical-science-pack").why())

    def test_on_a_death_world_survival_weighs_heavily_even_in_peace(self):
        team = priorities.assess({"npc_id": 1, "base": [], "team": TEAM, "defense": DEFENSE}, hold=False)
        faction = priorities.assess({"npc_id": 1, "base": [], "focus": {"arms": DEFENSE}}, hold=False)
        for item in ("firearm-magazine", "gun-turret", "stone-wall"):
            self.assertGreaterEqual(team.of(item).weight, priorities.ACT, item)
            self.assertLess(faction.of(item).weight, team.of(item).weight, item)
        self.assertLess(team.of("rocket-part").weight, faction.of("rocket-part").weight)   # the far part counts less
        # evolution climbing: more so
        evolved = priorities.assess({"npc_id": 1, "base": [], "team": TEAM, "defense": dict(DEFENSE, evolution=0.9)},
                                    hold=False)
        self.assertGreater(evolved.of("firearm-magazine").weight, team.of("firearm-magazine").weight)

    def test_research_goals_and_asks_pull_their_items_and_what_they_are_made_from(self):
        prio = priorities.assess({"npc_id": 1, "base": [], "purpose": "engine-assembly", "focus": {
            "research": BLUE,
            "asks": [{"tool": "stockpile", "priority": "now", "items": {"piercing-rounds-magazine": 200},
                      "reason": "Andre asked"}]}}, hold=False)
        self.assertIn("research advanced-material-processing needs it", prio.of("chemical-science-pack").why())
        self.assertIn("building engine-assembly", prio.of("engine-unit").why(3))
        steel_plate = prio.of("steel-plate")
        self.assertEqual(steel_plate.via, "piercing-rounds-magazine")   # (the ask needs it sooner than engines)
        ask = prio.of("piercing-rounds-magazine")
        self.assertTrue(ask.urgent)
        self.assertIn("Andre asked", ask.why())
        self.assertGreater(prio.of("copper-plate").weight, priorities.FLOOR)   # (the magazine's chain)

    def test_its_bus_pulls_its_packs_not_the_names_of_its_sections(self):
        bus = {"packs": ["automation-science-pack", "logistic-science-pack"],
               "extra": ["mall-1-14", "chemical-science-pack", "growth-1-2"], "stages": [], "done": {}}
        prio = priorities.assess({"npc_id": 1, "base": [], "base_plan": bus}, hold=False)
        self.assertIn("its bus is built for it", prio.of("chemical-science-pack").why())
        for name in ("mall-1-14", "growth-1-2"):
            self.assertNotIn("its bus is built for it", prio.of(name).why())

    def test_its_character_leans_its_priorities(self):
        def science(axes):
            book = {"npc_id": 1, "base": [block("bus-labs", takes={"automation-science-pack": 1}, kind="labs")],
                    "personality": {"axes": axes}}
            return priorities.assess(book, hold=False).section("bus-labs").weight
        self.assertGreater(science({"science": 0.95}), science({"science": 0.5}))
        frugal = {"npc_id": 1, "base": [block("bus-mall", takes={"iron-plate": 2}, kind="mall")],
                  "personality": {"axes": {"frugality": 0.95}}}
        spendthrift = dict(frugal, personality={"axes": {"frugality": 0.3}})
        self.assertLess(priorities.assess(frugal, hold=False).section("bus-mall").weight,
                        priorities.assess(spendthrift, hold=False).section("bus-mall").weight)

    def test_power_fuel_ore_robots_and_idle_labs_are_signals_too(self):
        base = [block("power", makes={}, takes={"coal": 1}, kind="power"),
                block("iron-mine", purpose="electric-iron-mining", stage=None, makes={"iron-ore": 2},
                      ore={"name": "iron-ore", "minutes": 4, "left": 1000, "rate": 2})]
        prio = priorities.assess({"npc_id": 1, "base": base, "power_short": True, "focus": {"research": []}},
                                 hold=False)
        self.assertTrue(prio.section("power").urgent)
        self.assertGreaterEqual(prio.of("coal").weight, 0.6)
        self.assertIn("runs out in 4 minutes", prio.of("iron-ore").why(3))
        self.assertIn("nothing is being researched", prio.targets["science"].why(4))
        researching = priorities.assess({"npc_id": 1, "base": base, "focus": {"research": RED}}, hold=False)
        self.assertGreater(researching.targets["science"].weight, prio.targets["science"].weight)
        robots = priorities.assess({"npc_id": 1, "base": [], "researched": ["construction-robotics"]}, hold=False)
        early = priorities.assess({"npc_id": 1, "base": []}, hold=False)
        self.assertGreater(robots.section("mall").weight, early.section("mall").weight)

    def test_a_teammate_short_of_what_it_makes_raises_it(self):
        ada = {"npc_id": 7, "npc_name": "Ada", "team": TEAM, "base": [
            block("bus-circuits", takes={"copper-plate": 6}, makes={"electronic-circuit": 2}, kind="science"),
            block("smelting-copper-plate", makes={"copper-plate": 2})]}
        bob = {"npc_id": 8, "npc_name": "Bob", "team": TEAM, "base": [
            block("smelting-copper-plate", makes={"copper-plate": 20})]}
        priorities.remember(ada)
        priorities.remember(bob)
        self.assertIn("Ada is short of it", priorities.assess(bob, hold=False).of("copper-plate").why(4))
        self.assertNotIn("short of it", priorities.assess(ada, hold=False).of("copper-plate").why(4))

    def test_new_signals_plug_in_and_one_that_fails_stops_nothing(self):
        @priorities.signal("test-quality")
        def quality(ctx):
            yield Pull("speed-module", 0.9, "quality research is near")

        @priorities.signal("test-broken")
        def broken(ctx):
            raise KeyError("half a situation")
        try:
            prio = priorities.assess({"npc_id": 1, "base": []}, hold=False)
        finally:
            del priorities.SIGNALS["test-quality"], priorities.SIGNALS["test-broken"]
        self.assertGreaterEqual(prio.of("speed-module").weight, 0.9)
        self.assertEqual(prio.errors, ["test-broken: 'half a situation'"])
        self.assertGreater(prio.of("automation-science-pack").weight, priorities.FLOOR)


class HoldTests(Fresh):
    def test_levels_hold_for_a_while_rise_sooner_and_patient_engineers_hold_longer(self):
        def level(research, minutes, patience=0.5):
            s = steel(research, tick=100 * MINUTE + int(minutes * MINUTE),
                      personality={"axes": {"patience": patience}})
            return priorities.assess(s).section("steel-smelting")
        self.assertEqual(level(RED, 0).level, "low")
        rising = level(BLUE, 0.5)
        self.assertEqual((rising.level, rising.held), ("low", True))     # not yet
        self.assertEqual(level(BLUE, 3).level, "normal")                 # it stayed so: now
        falling = level(RED, 4)
        self.assertEqual((falling.level, falling.held), ("normal", True))
        self.assertEqual(level(RED, 10).level, "normal")                 # ten minutes of it, not six
        self.assertEqual(level(RED, 14.5).level, "low")
        priorities.forget()
        level(BLUE, 0, patience=0.95)
        level(RED, 1, patience=0.95)
        self.assertEqual(level(RED, 12, patience=0.95).level, "normal")  # patient: 13 minutes
        self.assertEqual(level(RED, 15, patience=0.95).level, "low")

    def test_something_urgent_changes_it_at_once(self):
        calm = priorities.assess(fighting(None))
        self.assertGreater(calm.section("bus-science").weight, 0.6)
        hot = priorities.assess(fighting(10, tick=100 * MINUTE + 60))
        self.assertLess(hot.section("bus-science").weight, priorities.ACT)   # dropped at once, no waiting
        self.assertEqual(hot.of("firearm-magazine").level, "top")
        # the fight over: ammunition stays first for a while after
        after = priorities.assess(fighting(None, tick=100 * MINUTE + 3 * MINUTE))
        self.assertEqual(after.of("firearm-magazine").level, "top")
        self.assertTrue(after.of("firearm-magazine").held)


class SettingTests(Fresh):
    def test_the_strategists_settings_decide_until_they_run_out_or_their_condition_comes(self):
        raised = steel(priorities=[{"name": "steel-plate", "level": "high", "left": 20, "ago": 1,
                                    "reason": "steel for the tank"}])
        short = only(priorities.shortfalls(raised))
        self.assertFalse(short.held)
        self.assertIn("you set it high", short.priority.why(3))
        science_low = fighting(None, priorities=[{"name": "science", "level": "low", "left": 15}])
        lowered = only(priorities.shortfalls(science_low))
        self.assertEqual((lowered.unfed, lowered.held), ("bus-science", True))   # ammunition first, science waits
        self.assertEqual(priorities.assess(science_low, hold=False).section("bus-science").level, "low")
        # run out, or the condition met: the signals decide again
        for done in ({"left": 0}, {"until": "researched steel-processing"}, {"until": "built steel-smelting"},
                     {"until": "supplied"}):
            s = steel(priorities=[{"name": "steel-plate", "level": "high", "left": 20, "ago": 1, **done}],
                      researched=["steel-processing"], completed_purposes=["steel-smelting"])
            if done.get("until") == "supplied":
                s["base"] = s["base"][:2]   # nothing short now
            self.assertNotEqual(priorities.assess(s, hold=False).of("steel-plate").set, "you set it high", done)
        still = steel(priorities=[{"name": "steel-plate", "level": "high", "left": 20, "ago": 1,
                                   "until": "researched steel-processing"}])
        self.assertEqual(priorities.assess(still, hold=False).of("steel-plate").set,
                         "you set it high until researched steel-processing")

    def test_calm_and_attacked_conditions_follow_the_fighting(self):
        keep = [{"name": "ammo", "level": "top", "left": 60, "ago": 2, "until": "calm"}]
        self.assertEqual(priorities.assess(fighting(30, priorities=keep), hold=False).section("bus-ammo").set,
                         "you set it top until calm")
        self.assertIsNone(priorities.assess(fighting(None, priorities=keep), hold=False).section("bus-ammo").set)
        lull = [{"name": "defense", "level": "low", "left": 60, "ago": 5, "until": "attacked"}]
        self.assertIsNotNone(priorities.assess(fighting(None, priorities=lull), hold=False).weigh(kind="defense").set)
        self.assertIsNone(priorities.assess(fighting(60, priorities=lull), hold=False).weigh(kind="defense").set)

    def test_an_urgent_need_overrides_a_lowering(self):
        lowered = fighting(30, starved_ammo=True, priorities=[{"name": "ammo", "level": "ignore", "left": 30}])
        prio = priorities.assess(lowered, hold=False)
        self.assertGreater(prio.section("bus-ammo").weight, 0.85)
        self.assertIn("but this is urgent", prio.targets["ammo"].why(9))

    def test_the_situation_is_the_freshest_copy(self):
        s = steel(priorities=[{"name": "steel-plate", "level": "high", "left": 20}])
        cleared = {"npc_id": 7, "situation": {"name": "Ada", "research": {"current": "logistics"}}}
        self.assertIsNone(priorities.assess(s, cleared, hold=False).of("steel-plate").set)
        newer = {"npc_id": 7, "situation": {"priorities": [{"name": "steel-plate", "level": "ignore", "left": 5}]}}
        self.assertEqual(priorities.assess(s, newer, hold=False).of("steel-plate").weight, 0.05)


class FitsTests(Fresh):
    BASE = {"npc_id": 1, "base": [
        block("smelting-iron-plate", makes={"iron-plate": 10}, kind="smelting"),
        block("bus-science", takes={"iron-plate": 6}, makes={"automation-science-pack": 1}, kind="science"),
        block("bus-mall", takes={"iron-plate": 20}, makes={"transport-belt": 2}, kind="mall")]}

    def test_a_section_that_matters_more_fits_where_what_matters_less_yields(self):
        book = ledger.Ledger.from_site(self.BASE)
        self.assertFalse(book.fits({"iron-plate": 2})[0])                  # 8.7 + 2 of 10
        self.assertEqual(priorities.fits(book, {"iron-plate": 2}), book.fits({"iron-plate": 2}))   # (no ranks)
        ok, notes = priorities.fits(self.BASE, {"iron-plate": 2}, weight=0.95)
        self.assertTrue(ok, notes)
        self.assertRegex(notes[0], "fits only as .*bus-mall.* yield to it")
        self.assertFalse(priorities.fits(self.BASE, {"iron-plate": 2}, weight=0.15)[0])
        # by kind and what it makes: in a fight, an ammunition section is the one that matters
        hot = dict(self.BASE, team=TEAM, defense=DEFENSE, tick=100 * MINUTE,
                   threat_board={"attacks": [{"tick": 100 * MINUTE - 600, "units": {"small-biter": 20}}]})
        self.assertTrue(priorities.fits(hot, {"iron-plate": 2}, kind="ammo", makes=["firearm-magazine"])[0])
        self.assertFalse(priorities.fits(hot, {"iron-plate": 2}, kind="mall", makes=["transport-belt"])[0])
        self.assertEqual(book.fits_ranked({"iron-plate": 2}), book.fits({"iron-plate": 2}))


class ActingTests(Fresh):
    def test_supply_comes_first_only_for_shortages_that_matter(self):
        notes = []
        with tempfile.TemporaryDirectory() as folder:
            planner = CatalogPlanner(bp.Catalog(folder), log=notes.append)
            with mock.patch("companion.plate_lead.goal", return_value=None):   # (the ledger's own answer alone)
                self.assertIsNone(planner.supply_first(site(base=steel()["base"], focus={"research": RED})))
            self.assertTrue(any("held for now: iron-plate (low)" in n for n in notes), notes)
            goal, budget = planner.supply_first(site(base=steel()["base"], focus={"research": BLUE}))
            self.assertEqual((goal, budget.item), ("iron-smelting-expansion", "iron-plate"))
            self.assertTrue(any("raised again: iron-plate" in n for n in notes), notes)

    def test_the_strategist_sees_shortages_with_their_priority_and_the_held_ones_apart(self):
        priorities.remember(steel())
        notes = priorities.notes_for({"npc_id": 7, "situation": {}})
        self.assertNotIn("factory", notes)
        self.assertIn("Short but held for now", notes["priorities"])
        self.assertIn("iron-plate (low: steel-smelting goes short; makes steel-plate", notes["priorities"])
        text = strategist.prompt({"reason": "routine", "situation": {}, **notes})
        self.assertIn("prioritize changes that", text)
        self.assertNotRegex(text, "[—–]")
        priorities.remember(steel(BLUE, tick=103 * MINUTE))
        notes = priorities.notes_for({"npc_id": 7, "situation": {}})
        self.assertIn("priority normal", notes["factory"])
        self.assertNotIn("priorities", notes)
        self.assertIn("Your factory is short of: iron-plate", strategist.prompt({"reason": "routine", "situation": {},
                                                                              **notes}))
        self.assertEqual(priorities.notes_for({"npc_id": 99, "situation": {}}), {})   # nothing known: no line

    def test_the_bridge_adds_the_notes_to_the_thought_it_was_going_to_have(self):
        asked = []

        class Spy:
            source = "llm"

            def decide(self, request):
                asked.append(request)
                return []
        priorities.remember(steel())
        request = {"request_id": "7:s1", "npc_id": 7, "reason": "routine", "events": [], "situation": {"name": "Ada"}}
        bridge = strategist.AgentBridge(lambda c: {"requests": [request], "tick": 100 * MINUTE, "accepted": 0},
                                        Spy(), log=lambda _: None)
        for _ in range(200):
            bridge.poll_once()
            if asked:
                break
            time.sleep(0.01)
        self.assertIn("held for now", asked[0]["priorities"])


class ToolTests(Fresh):
    def test_the_strategist_has_a_compact_prioritize_tool(self):
        tool = next(t for t in strategist.tools(compact=True) if t["name"] == "prioritize")
        props = tool["parameters"]["properties"]
        self.assertEqual(props["level"]["enum"], ["top", "high", "normal", "low", "ignore", "auto"])
        self.assertLess(len(json.dumps(tool)), 900)
        self.assertIn("prioritize", {t["name"] for t in strategist.tools(team=True)})

        class Codex:
            def ask(self, prompt, schema, **kw):
                self.prompt = prompt
                return {"calls": [{"tool": "prioritize", "arguments": {
                    "name": "science", "level": "low", "minutes": 20, "until": None,
                    "reason": "ammo first while the biters keep coming"}}]}
        codex = Codex()
        out = strategist.CodexStrategist(codex, "gpt-6.1-sol", log=lambda _: None).decide(
            {"request_id": "1:s1", "npc_id": 1, "reason": "routine", "events": [], "situation": {"team": True}})
        self.assertEqual(out, [{"tool": "prioritize", "args": {"name": "science", "level": "low", "minutes": 20},
                                "priority": "next", "reason": "ammo first while the biters keep coming"}])
        self.assertIn("- prioritize: Raise or lower", codex.prompt)


def to_python(value):
    """A Lua table as JSON would see it: 1..n keys make a list."""
    if hasattr(value, "items"):
        items = dict(value.items())
        if items and all(isinstance(k, int) for k in items) and sorted(items) == list(range(1, len(items) + 1)):
            return [to_python(items[k]) for k in range(1, len(items) + 1)]
        return {str(k): to_python(v) for k, v in items.items()}
    return value


class GameTests(Fresh):
    """The game's side (priorities.lua through agent.lua), in Factorio's Lua 5.2 with the game stubbed."""

    def runtime(self):
        import test_agent
        lua = test_agent.runtime()
        lua.execute("""
            Priorities = require('scripts.priorities')
            prototypes.fluid = {['sulfuric-acid']={}}
            prototypes.item['steel-plate'] = {}
            game.tick = 3600
            local n = 0
            function submit(intentions)
                n = n + 1
                npc.agent = npc.agent or {events={}, agenda={}, notes={}, results={}, seq=0}
                npc.agent.pending = {request_id='p' .. n, tick=game.tick, events={}}
                return Agent.submit({request_id='p' .. n, intentions=intentions})
            end""")
        return lua

    def test_a_setting_round_trips_from_the_strategist_through_the_save_and_back(self):
        lua = self.runtime()
        # what the companion sends (CodexStrategist above): checked, kept with the engineer
        answer = lua.eval("""submit({{tool='prioritize', priority='next', reason='ammo first while the biters keep coming',
            args={name='science', level='low', minutes=20}}})""")
        self.assertEqual(answer.accepted, 1)
        kept = to_python(lua.eval("npc.priorities"))
        self.assertEqual(kept, [{"name": "science", "level": "low", "tick": 3600, "till": 3600 + 20 * 3600,
                                 "reason": "ammo first while the biters keep coming"}])
        self.assertEqual(lua.eval("npc.agent.results[#npc.agent.results].status"), "done")
        # the situation (and the site: the same list) carries it, ten minutes later
        lua.execute("game.tick = 3600 + 10 * 3600")
        listed = to_python(lua.eval("Agent.situation(npc, game.tick).priorities"))
        self.assertEqual(listed, [{"name": "science", "level": "low", "left": 10, "ago": 10,
                                   "reason": "ammo first while the biters keep coming"}])
        # and the companion weighs it
        prio = priorities.assess(fighting(None), {"npc_id": 9, "situation": {"priorities": listed}}, hold=False)
        self.assertEqual(prio.section("bus-science").level, "low")
        # run out: gone from the save
        lua.execute("game.tick = 3600 + 21 * 3600")
        self.assertIsNone(lua.eval("Priorities.list(npc, game.tick)"))
        self.assertEqual(lua.eval("#npc.priorities"), 0)

    def test_the_game_checks_every_setting(self):
        lua = self.runtime()
        lua.execute("""
            npc.force.technologies = {['steel-processing']={name='steel-processing'}}
            npc.design_history = {{purpose='steel-smelting'}}""")
        bad = lua.eval("""submit({
            {tool='prioritize', args={name='not-a-thing', level='high'}},
            {tool='prioritize', args={name='iron-plate', level='urgent'}},
            {tool='prioritize', args={name='iron-plate', level='high', ['until']='whenever'}},
            {tool='prioritize', args={name='iron-plate', level='high', ['until']='researched made-up-tech'}},
            {tool='prioritize', args={name='iron-plate', level='high', ['until']='built'}}})""")
        self.assertEqual([bad.rejected[i].reason for i in range(1, 6)], ["name", "level", "until", "until", "until"])
        good = lua.eval("""submit({
            {tool='prioritize', args={name='steel-smelting', level='high', ['until']='researched steel-processing'}},
            {tool='prioritize', args={name='sulfuric-acid', level='top', minutes=9999}},
            {tool='prioritize', args={name='ammo', level='top', ['until']='  calm  '}},
            {tool='prioritize', args={name='steel-plate', level='ignore'}}})""")
        self.assertEqual(good.accepted, 4)
        kept = {p["name"]: p for p in to_python(lua.eval("npc.priorities"))}
        self.assertEqual(kept["steel-smelting"]["until"], "researched steel-processing")   # one of its builds
        self.assertEqual(kept["steel-smelting"]["till"] - 3600, 240 * 3600)   # a condition still runs out
        self.assertEqual(kept["sulfuric-acid"]["till"] - 3600, 240 * 3600)    # (at most four hours)
        self.assertEqual(kept["ammo"]["until"], "calm")
        self.assertEqual(kept["steel-plate"]["till"] - 3600, 30 * 3600)       # nothing said: half an hour
        # one per name, and auto takes it off
        lua.eval("submit({{tool='prioritize', args={name='ammo', level='high'}}, "
                 "{tool='prioritize', args={name='steel-plate', level='auto'}}})")
        names = [p["name"] for p in to_python(lua.eval("npc.priorities"))]
        self.assertEqual(names, ["ammo", "sulfuric-acid", "steel-smelting"])
        self.assertEqual(lua.eval("npc.priorities[1].level"), "high")

    def test_the_site_carries_what_the_companion_weighs_the_rest_from(self):
        lua = self.runtime()
        lua.execute("""
            local function tech(name, done, units, packs, prereqs)
                local ingredients = {}
                for i, p in ipairs(packs) do ingredients[i] = {name=p, amount=1} end
                local t = {name=name, researched=done, enabled=true, research_unit_count=units,
                           research_unit_ingredients=ingredients, prerequisites={}}
                for _, p in ipairs(prereqs or {}) do t.prerequisites[p] = npc.force.technologies[p] end
                npc.force.technologies[name] = t
                return t
            end
            npc.force.technologies = {}
            tech('automation', true, 10, {'automation-science-pack'})
            tech('steam-power', false, nil, {})   -- (a trigger tech, Space Age's first steps: done by doing)
            tech('logistics', false, 75, {'automation-science-pack'}, {'automation', 'steam-power'})
            tech('rocket-silo', false, 1000, {'automation-science-pack', 'logistic-science-pack'}, {'logistics'})
            npc.force.current_research = npc.force.technologies['logistics']
            npc.agent = {events={}, notes={}, results={}, seq=0, agenda={
                {tool='stockpile', priority='now', reason='Andre asked', args={item='firearm-magazine', count=200}},
                {tool='deliver', priority='next', args={items={{name='iron-plate', count=50}}, to='Andre'}}}}""")
        focus = to_python(lua.eval("Priorities.focus(npc)"))
        self.assertEqual(focus["research"], [{"name": "logistics", "packs": ["automation-science-pack"]}])
        self.assertEqual(focus["space"], {"target": "rocket-silo", "left": 3, "total": 4,
                                          "packs": {"automation-science-pack": 1075, "logistic-science-pack": 1000},
                                          "next": {"techs": ["logistics"], "packs": ["automation-science-pack"]}})
        self.assertEqual(focus["asks"], [
            {"tool": "stockpile", "priority": "now", "items": {"firearm-magazine": 200}, "reason": "Andre asked"},
            {"tool": "deliver", "priority": "next", "items": {"iron-plate": 50}}])
        # the team world: the team's research, which the players lead
        lua.execute("""
            settings = {global={['npc-factions-scenario']={value='team'}}}
            local master = {name='player', current_research={name='rocket-silo'}, research_progress=0,
                            research_queue={}, technologies={}}
            game.forces = {player=master}""")
        self.assertEqual(to_python(lua.eval("Priorities.focus(npc)"))["research"],
                         [{"name": "rocket-silo", "packs": ["automation-science-pack", "logistic-science-pack"]}])
        # and the companion reads it: the export's next step to space, the asks
        prio = priorities.assess({"npc_id": 1, "base": [], "focus": focus}, hold=False)
        self.assertIn("the next step to space (logistics)", prio.of("automation-science-pack").why(4))
        self.assertTrue(prio.of("firearm-magazine").urgent)
        # the export sends both with every site
        source = (ROOT / "mod" / "scripts" / "designed_start.lua").read_text(encoding="utf-8")
        export = source[source.index("function DesignedStart.export"):source.index("local function plan_npc")]
        self.assertRegex(export, r"priorities=Priorities\.list\(npc,game\.tick\),focus=Priorities\.focus\(npc\)")


if __name__ == "__main__":
    unittest.main()
