"""The token audit's fixes (2026-10-23): biter fights answered by rule, the tools in their compact form for
Codex, one research ask at a time for the team, and reliable habits repeated without asking System One.
(Fix 1, routine thoughts only when something changed, and the minute between event thoughts: test_agent.)"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion.experience import ExperienceLibrary  # noqa: E402
from companion.strategist import AgentBridge, CodexStrategist, biter_fight, item_names, tools  # noqa: E402


def request(*events, reason="events", team=True, rid="1:s1"):
    return {"request_id": rid, "npc_id": 1, "reason": reason, "events": list(events),
            "situation": {"name": "Ada", "team": team, "home": {"x": 0, "y": 0},
                          "armed": {"guns": 1, "magazines": 40}}}


class Spy:
    """A strategist that must not be asked."""
    source = "llm"

    def __init__(self):
        self.asked = []

    def decide(self, r):
        self.asked.append(r["request_id"])
        return [{"tool": "say", "args": {"text": "Thinking."}, "priority": "next"}]


class FightTests(unittest.TestCase):
    def test_a_fight_against_biters_alone_is_answered_by_rule(self):
        hit = {"kind": "attacked", "by": "enemy", "position": {"x": 20, "y": 5}}
        self.assertTrue(biter_fight(request(hit, {"kind": "breach", "edge": "north"})))
        self.assertTrue(biter_fight(request({"kind": "home_attacked", "by": "biters"})))
        self.assertFalse(biter_fight(request(hit, {"kind": "chat", "from": "Andre", "text": "need help?"})))
        self.assertFalse(biter_fight(request({"kind": "attacked", "by": "Kevin"})))       # a player or faction
        self.assertFalse(biter_fight(request({"kind": "home_attacked", "by": "biters, Kevin"})))
        self.assertFalse(biter_fight(request(reason="routine")))
        spy = Spy()
        bridge = AgentBridge(lambda c: {}, spy, log=lambda _: None)
        intentions, source = bridge.think(request(hit), [])
        self.assertEqual((source, spy.asked), ("rules", []))
        self.assertEqual([i["tool"] for i in intentions], ["attack", "arm_up"])   # fight there, then arm up
        # (in the team world the plot's turrets go up at once anyway: no fort of its own)
        _, _ = bridge.think(request(hit, team=False, rid="1:s2"), [])
        faction_answer, _ = bridge.think(request(hit, team=False, rid="1:s3"), [])
        self.assertIn("outpost-defense", json.dumps(faction_answer))


class CompactToolTests(unittest.TestCase):
    def test_codex_gets_the_item_list_and_priorities_once_not_in_every_tool(self):
        situation = {"team": True, "military": [f"bob-item-{i}" for i in range(80)]}
        names = item_names(situation)
        full, compact = json.dumps(tools(names, team=True)), json.dumps(tools(names, team=True, compact=True))
        self.assertGreaterEqual(full.count("bob-item-77"), 3)
        self.assertNotIn("bob-item-77", compact)
        self.assertNotIn("drop what you are building", compact)

        class Codex:
            def ask(self, prompt, schema, **kw):
                self.prompt, self.schema = prompt, schema
                return {"calls": []}
        codex = Codex()
        CodexStrategist(codex, "gpt-6.1-sol", log=lambda _: None).decide(
            {"request_id": "1:s1", "npc_id": 1, "reason": "routine", "events": [], "situation": situation})
        self.assertEqual(codex.prompt.count("bob-item-77"), 1)
        self.assertIn("Priorities: now: drop what you are building", codex.prompt)
        self.assertNotIn("bob-item-77", json.dumps(codex.schema))


class HabitTests(unittest.TestCase):
    def test_a_decision_with_a_strong_record_is_repeated_without_asking_system_one(self):
        with tempfile.TemporaryDirectory() as folder:
            library = ExperienceLibrary(Path(folder))
            routine = request({"kind": "home_attacked", "by": "Kevin"}, rid="1:s0")
            decision = [{"tool": "arm_up", "args": {"focus": "all"}, "priority": "now"}]
            for n in range(5):
                record = library.record(dict(routine, request_id=f"1:h{n}"), decision)
                library.outcome(f"1:h{n}", "arm_up", "done")
            asked = []

            class Chooser:
                def choose(self, *a, **k):
                    asked.append(a)
                    return "think", {}
            spy = Spy()
            sent = []
            bridge = AgentBridge(lambda c: sent.append(c) or {"requests": [routine], "accepted": 1}, spy,
                                 library=library, chooser=Chooser(), log=lambda _: None)
            bridge.poll_once()
            self.assertEqual((asked, spy.asked), ([], []))             # neither System One nor the strategist
            self.assertIn('"tool":"arm_up"', "".join(sent))
            # a record not yet that strong: System One weighs it
            record.done, record.failed = 3, 2
            bridge.forget()
            bridge.poll_once()
            for _ in range(100):
                if bridge.running == {} and bridge.ready == {}:
                    break
                bridge.poll_once()
            self.assertEqual(len(asked), 1)


class ResearchAskTests(unittest.TestCase):
    def test_in_the_team_world_one_engineer_asks_about_research_at_a_time(self):
        import test_retire
        lua = test_retire.runtime()
        lua.execute("""
            settings.global['npc-factions-scenario'] = {value='team'}
            for id = 1, 3 do storage.npcs[id].character = {valid=true} end
            storage.npcs[2].research_request = {tick=100, options={}}""")
        self.assertTrue(lua.eval("DS.teammate_asking(storage.npcs[1], 200)"))
        self.assertFalse(lua.eval("DS.teammate_asking(storage.npcs[2], 200)"))      # (its own question)
        self.assertFalse(lua.eval("DS.teammate_asking(storage.npcs[1], 100 + 1801)"))  # waited its time
        lua.execute("settings.global['npc-factions-scenario'] = {value='factions'}")
        self.assertFalse(lua.eval("DS.teammate_asking(storage.npcs[1], 200)"))


if __name__ == "__main__":
    unittest.main()
