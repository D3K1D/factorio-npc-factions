"""The proving ground's harness (tools/proving.py): checks, overrides, the base save's key, and the scenarios
listed on both sides (the Python list and mod/scripts/proving.lua) being the same."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import proving  # noqa: E402


class CheckTests(unittest.TestCase):
    def test_every_check_must_hold(self):
        ok, checks = proving.check({"a": 3, "b": True}, [("a", "<=", 5), ("b", "==", True)])
        self.assertTrue(ok)
        self.assertEqual([c["ok"] for c in checks], [True, True])
        ok, checks = proving.check({"a": 6, "b": True}, [("a", "<=", 5), ("b", "==", True)])
        self.assertFalse(ok)
        self.assertEqual(checks[0]["got"], 6)

    def test_a_metric_never_reached_fails_its_check(self):
        ok, checks = proving.check({"red_minute": None}, [("red_minute", "<=", 50), ("lab_minute", "<=", 25)])
        self.assertFalse(ok)
        self.assertEqual([c["ok"] for c in checks], [False, False])

    def test_a_metric_of_the_wrong_kind_fails_rather_than_raising(self):
        ok, _ = proving.check({"a": "many"}, [("a", "<=", 5)])
        self.assertFalse(ok)


class OverrideTests(unittest.TestCase):
    def test_values_are_json_else_text(self):
        got = proving.parse_set(["size=40", 'kinds={"small-biter":1}', "edge=west", "evolution=0.5"])
        self.assertEqual(got, {"size": 40, "kinds": {"small-biter": 1}, "edge": "west", "evolution": 0.5})


class ScenarioListTests(unittest.TestCase):
    def test_python_and_lua_name_the_same_scenarios(self):
        lua = (ROOT / "mod" / "scripts" / "proving.lua").read_text(encoding="utf-8")
        in_lua = set(re.findall(r'^S\["([a-z-]+)"\]', lua, re.M))
        # (variants of one scenario name it: "scenario")
        self.assertEqual(in_lua, {spec.get("scenario", name) for name, spec in proving.SCENARIOS.items()})

    def test_each_real_scenario_has_a_limit_params_and_checks(self):
        for name, spec in proving.SCENARIOS.items():
            self.assertIn("owners", spec, name)
            if spec.get("stub"):
                continue
            self.assertGreater(spec["limit"], 0, name)
            self.assertIsInstance(spec["params"], dict, name)
            for metric, op, _ in spec["checks"]:
                self.assertIn(op, proving.OPS, name)
                self.assertIsInstance(metric, str)


class BaseKeyTests(unittest.TestCase):
    def test_the_key_follows_the_mod_set_not_npc_factions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            (mods / "mod-list.json").write_text(json.dumps({"mods": []}), encoding="utf-8")
            (mods / "a_1.0.0.zip").write_bytes(b"")
            first = proving.base_key(mods)
            (mods / "npc-factions_9.9.9.zip").write_bytes(b"")
            self.assertEqual(proving.base_key(mods), first)   # (our own zip is rebuilt every run)
            (mods / "b_1.0.0.zip").write_bytes(b"")
            self.assertNotEqual(proving.base_key(mods), first)


class FreshRingTests(unittest.TestCase):
    """A stage's start with its wall ring taken away (Proving.clear_ring, params.fresh_ring): a start grown before a
    change of the ring's geometry measured on a ring built again (D-25)."""

    def test_the_rings_plans_and_what_they_placed_go_the_rest_stays(self):
        from lupa.lua52 import LuaRuntime   # (Factorio's Lua)
        src = (ROOT / "mod" / "scripts" / "proving.lua").read_text(encoding="utf-8")
        body = src[src.index("Proving.RING_PLANS="):src.index("-- Each turret lost and how soon")]
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute("Proving = {}\n" + body)
        lua.execute("""
            DesignedStart = {begin=function(npc, tick, goal, purpose) npc.design = {purpose=purpose, status='waiting'} end}
            gone = 0
            function thing() local e = {valid=true}; e.destroy = function() e.valid = false; gone = gone + 1 end
                return e end
            wall, turret, drill = thing(), thing(), thing()
            npc = {design_history={
                {purpose='plot-wall', order={'w'}, entities={w={entity=wall}}},
                {purpose='plot-turrets', order={'t'}, entities={t={entity=turret}}},
                {purpose='iron-mining', order={'d'}, entities={d={entity=drill}}}},
                design={purpose='ammo-line'}, paused_design={purpose='bus-base'}}
            out = Proving.clear_ring(npc, 100)""")
        self.assertEqual(lua.eval("out.plans"), 2)
        self.assertEqual(lua.eval("out.entities"), 2)
        self.assertFalse(lua.eval("wall.valid") or lua.eval("turret.valid"))
        self.assertTrue(lua.eval("drill.valid"))
        self.assertEqual(lua.eval("#npc.design_history"), 1)
        self.assertEqual(lua.eval("npc.design.purpose"), "plot-wall")            # (its ring asked for at once;
        self.assertEqual(lua.eval("npc.design.ask"), "now")                      #  the ring's under way dropped)
        self.assertEqual(lua.eval("npc.paused_design.purpose"), "bus-base")
        lua.execute("""npc.design = {purpose='bus-base'}; npc.paused_design = nil; npc.design_history = {}
            Proving.clear_ring(npc, 200)""")
        self.assertEqual(lua.eval("npc.paused_design.purpose"), "bus-base")     # (a build under way: set aside)
        self.assertEqual(lua.eval("npc.design.purpose"), "plot-wall")
        import proving
        self.assertTrue(proving.SCENARIOS["late-combat-bus"]["params"]["fresh_ring"])


class LossAnswerTests(unittest.TestCase):
    """Each turret lost and how soon a new one stood at that spot (Proving.loss_answers, loss_summary; the user's play,
    2026-10-30, lesson 11: more turrets there within about a minute of every loss)."""

    def runtime(self):
        from lupa.lua52 import LuaRuntime   # (Factorio's Lua)
        src = (ROOT / "mod" / "scripts" / "proving.lua").read_text(encoding="utf-8")
        body = src[src.index("Proving.LOSS_REACH="):src.rindex("\nreturn Proving")]
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute("Proving = {}\n" + body)
        lua.execute("""
            turrets = {}
            function put(id, x, y) turrets[id] = {unit_number=id, position={x=x, y=y}} end
            npc = {force='npc', character={surface={find_entities_filtered=function()
                local out = {} for _, t in pairs(turrets) do out[#out + 1] = t end return out end}}}
            s = {}""")
        return lua

    def test_a_turret_lost_and_one_put_back_there_a_minute_on_is_answered_in_60_s(self):
        lua = self.runtime()
        lua.execute("""
            put(1, 0, 0); put(2, 40, 0)
            Proving.loss_answers(npc, s, 60)
            turrets[1] = nil; Proving.loss_answers(npc, s, 120)          -- (lost)
            put(3, 50, 0); Proving.loss_answers(npc, s, 1800)            -- (one elsewhere: no answer)
            put(4, 2, 1); Proving.loss_answers(npc, s, 3720)             -- (one at the spot, 60 s on)
            turrets[2] = nil; Proving.loss_answers(npc, s, 3780)         -- (another lost, not answered)
            out = Proving.loss_summary(s, 3780 + 600)""")
        self.assertEqual(lua.eval("out.losses"), 2)
        self.assertEqual(lua.eval("out.answered"), 1)
        self.assertEqual(lua.eval("out.median_s"), 60)
        self.assertEqual(lua.eval("out.open"), 1)
        self.assertEqual(lua.eval("out.open_oldest_s"), 10)

    def test_two_lost_side_by_side_each_answered_by_the_one_at_its_spot(self):
        lua = self.runtime()
        lua.execute("""
            put(1, 0, 0); put(2, 5, 0)
            Proving.loss_answers(npc, s, 60)
            turrets[1] = nil; Proving.loss_answers(npc, s, 120)
            turrets[2] = nil; Proving.loss_answers(npc, s, 180)
            put(3, 0, 0); Proving.loss_answers(npc, s, 1320)            -- (at the first's spot: 20 s after it)
            out = Proving.loss_summary(s, 1320)""")
        self.assertEqual(lua.eval("out.median_s"), 20)
        self.assertEqual(lua.eval("out.open"), 1)

    def test_paired_over_all_so_one_near_two_answers_the_other(self):
        # (Codex: lost at x 0 and 10, new ones at 4 and 0 in one look: both answered)
        lua = self.runtime()
        lua.execute("""
            put(1, 0, 0); put(2, 10, 0)
            Proving.loss_answers(npc, s, 60)
            turrets[1] = nil; turrets[2] = nil; Proving.loss_answers(npc, s, 120)
            put(3, 4, 0); put(4, 0, 0); Proving.loss_answers(npc, s, 720)
            out = Proving.loss_summary(s, 720)""")
        self.assertEqual(lua.eval("out.answered"), 2)
        self.assertEqual(lua.eval("out.open"), 0)

    def test_nothing_looked_at_yet_is_no_summary(self):
        lua = self.runtime()
        self.assertIsNone(lua.eval("Proving.loss_summary(s, 1)"))


if __name__ == "__main__":
    unittest.main()
