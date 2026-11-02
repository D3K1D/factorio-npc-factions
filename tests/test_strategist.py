"""The strategist (companion/strategist.py) and what engineers learn from it (companion/experience.py)."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from companion.experience import MIN_USES, ExperienceLibrary, signature
from companion.strategist import (AgentBridge, RuleStrategist, Strategist, instructions, intentions_from,
                                  item_names, tools)

ATTACK = {"request_id": "1:s1", "npc_id": 1, "reason": "events",
          "events": [{"kind": "ally_attacked", "ally": "Andre", "by": "biters"}],
          "situation": {"name": "Ada", "traits": ["cautious"], "threats": {"unit-spawner": 2},
                        "nearest_threat": {"distance": 140}}}


class StrategistTests(unittest.TestCase):
    def test_tools_are_strict_and_complete(self):
        for tool in tools():
            params = tool["parameters"]
            self.assertTrue(tool["strict"])
            self.assertEqual(sorted(params["required"]), sorted(params["properties"]))
        self.assertEqual({t["name"] for t in tools()},
                         {"say", "remember", "build", "stockpile", "deliver", "guard", "ask", "research",
                          "visit", "give", "trade", "join", "attack", "build_at", "arm_up", "diplomacy", "quiet",
                          "relocate", "prioritize"})

    def test_on_one_team_it_has_no_tools_for_deals_or_treaties_and_is_told_it_is_a_teammate(self):
        names = {t["name"] for t in tools(team=True)}
        self.assertFalse(names & {"trade", "join", "diplomacy", "relocate", "build_at"})   # (it stays in its plot)
        self.assertIn("give", names)
        text = instructions({"name": "Ada", "team": True, "plot": {"name": "npc-1"}})
        self.assertIn("one team with the human players", text)
        self.assertIn("npc-1", text)
        self.assertIn("players have the last word", text)
        self.assertNotIn("nobody's team", text)

    def test_it_is_told_which_goals_its_planners_can_build_now(self):
        # (it named goals not ready yet, again and again: "upgrade", one-engineer run 2026-10-25)
        from companion import catalog_planner
        from companion.strategist import prompt
        site = {"npc_id": 7, "purpose": "choose", "completed_purposes": ["iron-smelting-start"],
                "recipes": [{"name": "burner-mining-drill"}, {"name": "stone-furnace"}],
                "resources": [{"resource": "coal", "rows": [[1, 1, 4]]}]}
        catalog_planner.note_ready(site, designer=False)
        ready = catalog_planner.ready_for(7)
        self.assertIn("coal-loop", ready)
        self.assertNotIn("upgrade", ready)
        self.assertIsNone(catalog_planner.ready_for(8))                 # (not planned for yet: nothing said)
        text = prompt({"reason": "routine", "situation": {}, "ready_goals": ready})
        self.assertIn("Goals your planners can build now", text)
        self.assertIn("coal-loop", text)
        self.assertNotIn("can build now", prompt({"reason": "routine", "situation": {}}))

    def test_it_talks_like_a_player_on_the_radio_and_the_prompts_show_no_dashes(self):
        for situation in ({"name": "Ada"}, {"name": "Ada", "team": True}):
            text = instructions(situation)
            self.assertIn("voice comms", text)
            self.assertNotIn(" - ", text)
            self.assertNotRegex(text, "[—–]")
        out = intentions_from([
            {"type": "function_call", "name": "say", "arguments": json.dumps(
                {"text": "Biters north — two big ones; need a hand.", "to": None})},
            {"type": "function_call", "name": "ask", "arguments": json.dumps(
                {"to": "Andre", "question": "Walls here – or further out?", "wants_place": True})}])
        self.assertEqual(out[0]["args"]["text"], "Biters north, two big ones, need a hand.")
        self.assertEqual(out[1]["args"]["question"], "Walls here, or further out?")

    def test_modded_military_items_the_game_reports_can_be_named(self):
        names = item_names({"military": ["bob-gun-turret-2", "gun-turret", "bob-reinforced-wall", 7]})
        self.assertEqual(names.count("gun-turret"), 1)
        self.assertEqual(names[-2:], ["bob-gun-turret-2", "bob-reinforced-wall"])
        stockpile = next(t for t in tools(names) if t["name"] == "stockpile")
        self.assertIn("bob-gun-turret-2", json.dumps(stockpile))
        self.assertNotIn("bob-gun-turret-2", json.dumps(tools()))

    def test_tool_calls_become_intentions(self):
        out = intentions_from([
            {"type": "message", "content": []},
            {"type": "function_call", "name": "deliver", "arguments": json.dumps(
                {"to": "Andre", "items": [{"name": "gun-turret", "count": 4}], "priority": "now",
                 "reason": "he is under attack"})},
            {"type": "function_call", "name": "say", "arguments": json.dumps({"text": "Coming!", "to": None})}])
        self.assertEqual(out[0], {"tool": "deliver", "priority": "now", "reason": "he is under attack",
                                  "args": {"to": "Andre", "items": [{"name": "gun-turret", "count": 4}]}})
        self.assertEqual(out[1]["args"], {"text": "Coming!"})          # nulls dropped
        self.assertEqual(out[1]["priority"], "next")

    def test_a_model_call_carries_the_situation_and_the_tools_within_a_budget(self):
        sent = []

        def transport(url, body, key, timeout):
            sent.append(body)
            return {"usage": {"input_tokens": 1200, "output_tokens": 80},
                    "output": [{"type": "function_call", "name": "say", "arguments": '{"text":"Hi","to":"Andre"}'}]}
        brain = Strategist("gpt-6-luna", api_key="test", transport=transport)
        got = brain.decide(ATTACK)
        self.assertEqual(got[0]["tool"], "say")
        self.assertEqual(sent[0]["model"], "gpt-6-luna")
        self.assertIn("Andre", sent[0]["input"])
        self.assertIn("cautious", sent[0]["instructions"])
        self.assertEqual(brain.tokens, {"input": 1200, "output": 80, "calls": 1})
        brain.calls[1] = [time.time()] * 90          # the hour's budget is spent
        self.assertEqual(brain.decide(ATTACK), [])

    def test_the_scripted_stand_in_answers_the_usual_events(self):
        rules = RuleStrategist()
        chat = rules.decide({"events": [{"kind": "chat", "from": "Andre", "text": "biters are attacking, help"}]})
        self.assertEqual([i["tool"] for i in chat], ["say", "deliver"])
        self.assertEqual(rules.decide(ATTACK)[0]["args"]["to"], "Andre")
        answer = rules.decide({"events": [{"kind": "answer", "from": "Andre", "place": {"x": 1, "y": 2}}]})
        self.assertEqual(answer[0]["tool"], "guard")
        # at war with someone who hurt it: arm up at once, then go after them
        war = rules.decide({"events": [{"kind": "war", "with": "Andre", "why": "attacked me"}]})
        self.assertEqual([(i["tool"], i["priority"]) for i in war], [("arm_up", "now"), ("attack", "next")])
        self.assertEqual(war[1]["args"]["target"], "Andre")
        # it reaches out only to players it is allied with, not every player it is at peace with
        situation = {"players": [{"name": "Andre", "relation": "peace"}]}
        self.assertEqual(rules.decide({"reason": "initiative", "situation": situation}), [])
        situation["players"][0]["relation"] = "allied"
        self.assertEqual(rules.decide({"reason": "initiative", "situation": situation})[0]["tool"], "ask")
        # called home by an attack on its base (it goes by itself): a fort on that side for next time
        home = rules.decide({"events": [{"kind": "home_attacked", "by": "biters", "why": "undefended"}]})
        self.assertEqual([(i["tool"], i["args"].get("goal")) for i in home], [("build", "outpost-defense")])
        # kept clear of biters it cannot beat yet with nothing to do out of their reach (O-4): it arms up
        kept = rules.decide({"events": [{"kind": "kept_from_work", "biters": 12, "odds": 0.1}]})
        self.assertEqual([(i["tool"], i["priority"]) for i in kept], [("arm_up", "now")])
        # the team world: defense where it is pressed now, whatever it is building (2026-10-25)
        plot = rules.decide({"situation": {"team": True},
                             "events": [{"kind": "home_attacked", "by": "biters", "why": "undefended"}]})
        self.assertEqual([(i["tool"], i["priority"], i["args"].get("goal")) for i in plot],
                         [("build", "now", "reinforce")])
        # no way there clear of the biters: no gear gets it through, so nothing now (and that target counts
        # as one it backed off from)
        walled = rules.decide({"npc_id": 9, "events": [{"kind": "attack_aborted", "target": "Kevin",
                                                        "why": "no way there clear of the biters"}]})
        self.assertEqual(walled, [])
        self.assertIn((9, "Kevin"), rules.walled)
        # backed off from turrets it could not beat: turrets first (if it can make them), then again
        back = rules.decide({"events": [{"kind": "attack_aborted", "target": "Kevin",
                                         "why": "their turrets and none of my own to creep with"}],
                             "situation": {"can_make": ["gun-turret"]}})
        self.assertEqual([(i["tool"], i["args"].get("focus") or i["args"].get("target")) for i in back],
                         [("arm_up", "turrets"), ("attack", "Kevin")])
        self.assertEqual(rules.decide({"events": [{"kind": "attack_aborted", "target": "Kevin",
                                                   "why": "their turrets and none of my own"}],
                                       "situation": {"can_make": []}}), [])
        # backing off again and again from the same target's turrets: it stops going back
        again = {"npc_id": 3, "events": [{"kind": "attack_aborted", "target": "Kevin", "why": "their turrets"}],
                 "situation": {"can_make": ["gun-turret"]}}
        self.assertEqual(len(rules.decide(again)), 2)
        self.assertEqual(len(rules.decide(again)), 2)
        self.assertEqual(rules.decide(again), [])
        # with a foe it arms up, and once armed goes after the nearest it is at war with
        rules = RuleStrategist()
        foe = {"foe": "everyone", "engineers": [{"name": "Kevin", "relation": "war", "distance": 2400},
                                                {"name": "Lena", "relation": "war", "distance": 300},
                                                {"name": "Mo", "relation": "peace", "distance": 200}],
               "armed": {"guns": ["pistol"], "magazines": 5}}
        self.assertEqual(rules.decide({"reason": "routine", "situation": foe})[0]["tool"], "arm_up")
        foe["armed"]["magazines"] = 40
        hunt = rules.decide({"npc_id": 1, "reason": "routine", "situation": foe})[0]
        self.assertEqual((hunt["tool"], hunt["args"]["target"]), ("attack", "Lena"))
        # running short itself is no reason to give a target up
        for _ in range(3):
            rules.decide({"npc_id": 1, "events": [{"kind": "attack_aborted", "target": "Lena", "why": "out of ammunition"}]})
        self.assertEqual(rules.decide({"npc_id": 1, "reason": "routine", "situation": foe})[0]["args"]["target"], "Lena")
        # one whose turrets it backed off from twice is left for the next; that one is far: 100 magazines first
        for _ in range(2):
            rules.decide({"npc_id": 1, "events": [{"kind": "attack_aborted", "target": "Lena", "why": "their turrets"}]})
        self.assertEqual(rules.decide({"npc_id": 1, "reason": "routine", "situation": foe})[0]["tool"], "arm_up")
        foe["armed"]["magazines"] = 120
        hunt = rules.decide({"npc_id": 1, "reason": "routine", "situation": foe})[0]
        self.assertEqual((hunt["tool"], hunt["args"]["target"]), ("attack", "Kevin"))

    def test_walled_off_by_biters_it_goes_for_another_and_tries_again_in_a_while(self):
        from companion.strategist import RuleStrategist
        rules = RuleStrategist()
        foe = {"foe": "everyone", "engineers": [{"name": "Kevin", "relation": "war", "distance": 300},
                                                {"name": "Lena", "relation": "war", "distance": 900}],
               "armed": {"guns": ["submachine-gun"], "magazines": 150}}
        hunt = lambda: rules.decide({"npc_id": 1, "reason": "routine", "situation": foe})[0]["args"]["target"]
        self.assertEqual(hunt(), "Kevin")                                   # the nearest
        rules.decide({"npc_id": 1, "events": [{"kind": "attack_aborted", "target": "Kevin",
                                               "why": "no way there clear of the biters"}]})
        self.assertEqual(hunt(), "Lena")                                    # walled off: the next
        for _ in range(rules.WALLED_WAIT):
            hunt()
        self.assertEqual(hunt(), "Kevin")                                   # a while later: again

    def test_it_is_told_it_is_one_of_many_and_speaks_for_itself(self):
        text = instructions({"name": "Cora", "traits": ["cautious"]})
        self.assertIn("one of several independent engineers", text)
        self.assertIn("I, me, my", text)
        self.assertNotIn("Your mission", text)
        warlord = instructions({"name": "Dax", "mission": "Take out everyone.", "silent": True})
        self.assertIn("Your mission, above everything else: Take out everyone.", warlord)
        self.assertIn("You never speak in chat", warlord)


class ExperienceTests(unittest.TestCase):
    def test_hit_by_biters_it_fights_back_by_an_engineer_it_fortifies_first(self):
        rules = RuleStrategist()
        armed = {"situation": {"armed": {"guns": ["pistol"], "magazines": 40}}}
        biters = rules.decide({**armed, "events": [{"kind": "attacked", "by": "enemy", "position": {"x": 5, "y": 9}}]})
        self.assertEqual([(i["tool"], i["priority"]) for i in biters],
                         [("attack", "now"), ("arm_up", "now"), ("build", "next")])
        self.assertEqual((biters[0]["args"]["x"], biters[0]["args"]["y"]), (5, 9))   # where the base was hit
        raid = rules.decide({**armed, "events": [{"kind": "attacked", "by": "Borin", "position": {"x": 5, "y": 9}}]})
        self.assertEqual([(i["tool"], i["priority"]) for i in raid], [("build", "now"), ("arm_up", "next")])
        away = {"situation": {**armed["situation"], "home": {"x": -230, "y": -60}}}
        self.assertEqual(rules.decide({**away, "events": [{"kind": "attacked", "by": "Ada",
                                                            "position": {"x": 5, "y": 9}}]}), [])   # out raiding

    def test_a_proven_decision_is_repeated_for_someone_else_without_asking(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = ExperienceLibrary(Path(tmp))
            decision = [{"tool": "deliver", "priority": "now", "args": {"to": "Andre", "items": [
                {"name": "gun-turret", "count": 4}]}}, {"tool": "say", "args": {"text": "Coming!", "to": "Andre"}}]
            self.assertIsNone(library.recall(ATTACK))
            for n in range(MIN_USES):
                request = dict(ATTACK, request_id=f"1:s{n}")
                library.record(request, decision)
                library.outcome(request["request_id"], "deliver", "done")
            # Kevin's base is hit now: the same answer, for Kevin, from what was learned
            kevin = dict(ATTACK, request_id="2:s9", events=[{"kind": "ally_attacked", "ally": "Kevin", "by": "biters"}])
            recalled = library.recall(kevin)
            self.assertEqual(recalled, [{"tool": "deliver", "priority": "now", "args": {"to": "Kevin", "items": [
                {"name": "gun-turret", "count": 4}]}}])
            # it lasts across games
            self.assertIsNotNone(ExperienceLibrary(Path(tmp)).recall(kevin))
            # a different situation, or someone talking, still asks the strategist
            self.assertNotEqual(signature(kevin), signature(dict(kevin, reason="routine")))
            chat = dict(kevin, events=kevin["events"] + [{"kind": "chat", "from": "Kevin", "text": "help"}])
            self.assertIsNone(library.recall(chat))

    def test_an_armed_engineer_with_a_foe_is_not_in_an_unarmed_ones_situation(self):
        base = {"reason": "routine", "events": [], "situation": {"traits": ["aggressive"], "foe": "everyone",
                                                                 "armed": {"magazines": 5}}}
        armed = {"reason": "routine", "events": [], "situation": {"traits": ["aggressive"], "foe": "everyone",
                                                                  "armed": {"magazines": 220}}}
        calm = {"reason": "routine", "events": [], "situation": {"traits": ["aggressive"], "armed": {"magazines": 5}}}
        self.assertNotEqual(signature(base), signature(armed))      # arming up is no habit once armed
        self.assertNotEqual(signature(base), signature(calm))

    def test_decisions_that_fail_are_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = ExperienceLibrary(Path(tmp))
            decision = [{"tool": "build", "priority": "now", "args": {"goal": "outpost-defense"}}]
            for n in range(MIN_USES + 2):
                request = dict(ATTACK, request_id=f"1:f{n}")
                library.record(request, decision)
                library.outcome(request["request_id"], "build", "failed")
            self.assertIsNone(library.recall(ATTACK))


class AgentBridgeTests(unittest.TestCase):
    def test_requests_are_answered_and_outcomes_learned(self):
        calls = []
        state = {"requests": [ATTACK], "results": []}

        def call(command):
            calls.append(command)
            if command == "/npc-agent observe":
                out = {"ok": True, "requests": state["requests"], "results": state["results"]}
                state["results"] = []
                return out
            return {"ok": True, "accepted": 1}

        with tempfile.TemporaryDirectory() as tmp:
            library = ExperienceLibrary(Path(tmp))
            bridge = AgentBridge(call, RuleStrategist(), library=library, log=lambda m: None)
            for _ in range(50):
                bridge.poll_once()
                if any(c.startswith("/npc-agent submit") for c in calls):
                    break
                time.sleep(0.01)
            submitted = [c for c in calls if c.startswith("/npc-agent submit")]
            self.assertEqual(len(submitted), 1)
            self.assertIn('"deliver"', submitted[0])
            state["requests"] = []
            state["results"] = [{"request_id": "1:s1", "tool": "deliver", "status": "done"}]
            bridge.poll_once()
            self.assertEqual(library.records[0].done, 1)


class HabitTests(unittest.TestCase):
    class Chooser:
        def __init__(self, pick):
            self.pick, self.asked = pick, []

        def choose(self, site, options, question):
            self.asked.append((question, [o["id"] for o in options], site["npc_name"]))
            return self.pick, {}

    def library_with_a_habit(self, tmp):
        library = ExperienceLibrary(Path(tmp))
        decision = [{"tool": "deliver", "priority": "now", "args": {"to": "Andre", "items": [
            {"name": "gun-turret", "count": 4}]}}]
        for n in range(2):
            request = dict(ATTACK, request_id=f"1:h{n}")
            library.record(request, decision)
            library.outcome(request["request_id"], "deliver", "done")
        return library

    def run_bridge(self, library, chooser):
        calls = []
        kevin = dict(ATTACK, request_id="2:h9", events=[{"kind": "ally_attacked", "ally": "Kevin", "by": "biters"}])

        def call(command):
            calls.append(command)
            if command == "/npc-agent observe":
                return {"ok": True, "requests": [kevin], "results": []}
            return {"ok": True, "accepted": 1}

        class Brain:
            asked = 0

            def decide(self, request):
                Brain.asked += 1
                return [{"tool": "build", "priority": "now", "args": {"goal": "outpost-defense"}}]

        bridge = AgentBridge(call, Brain(), library=library, chooser=chooser, log=lambda m: None)
        for _ in range(100):
            bridge.poll_once()
            if any(c.startswith("/npc-agent submit") for c in calls):
                break
            time.sleep(0.01)
        submitted = [json.loads(c.split(" ", 2)[2]) for c in calls if c.startswith("/npc-agent submit")]
        return submitted[0]["intentions"], Brain.asked

    def test_system_one_may_repeat_what_worked_for_someone_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = self.library_with_a_habit(tmp)
            chooser = self.Chooser("repeat-0")
            intentions, asked = self.run_bridge(library, chooser)
            self.assertEqual(chooser.asked[0], ("habit", ["repeat-0", "think"], "Ada"))
            self.assertEqual((intentions[0]["tool"], intentions[0]["args"]["to"], asked), ("deliver", "Kevin", 0))

    def test_or_think_it_over_with_the_strategist(self):
        with tempfile.TemporaryDirectory() as tmp:
            intentions, asked = self.run_bridge(self.library_with_a_habit(tmp), self.Chooser("think"))
            self.assertEqual((intentions[0]["tool"], asked), ("build", 1))


class ExpiryTests(unittest.TestCase):
    def test_a_thought_not_done_by_its_deadline_is_answered_by_the_rules(self):
        # (ST-3, SO-5: a hung thought was waited for forever; the game drops the request after a minute)
        import threading
        from unittest import mock
        from companion import strategist
        release, said, sent = threading.Event(), [], []

        class Hung:
            source = "llm"

            def decide(self, request):
                release.wait(10)
                return [{"tool": "say", "args": {"text": "late"}, "priority": "next"}]

        def call(command):
            if "observe" in command:
                return {"requests": [{"request_id": "1:s1", "npc_id": 1, "reason": "events",
                                      "events": [{"kind": "chat", "from": "Andre", "text": "need help?"}],
                                      "situation": {"name": "Ada"}}]}
            sent.append(json.loads(command.split(" ", 2)[2]))
            return {"accepted": len(sent[-1]["intentions"])}
        bridge = AgentBridge(call, Hung(), log=said.append)
        first_pool = bridge.pool
        try:
            with mock.patch.object(strategist, "ANSWER_S", 0.2):
                bridge.poll_once()
                self.assertEqual(sent, [])                              # still thinking
                time.sleep(0.3)
                bridge.poll_once()
            self.assertIsNot(bridge.pool, first_pool)                   # later thoughts do not queue behind it
            self.assertEqual([s["request_id"] for s in sent], ["1:s1"])
            self.assertNotIn("late", json.dumps(sent))                  # the rules' answer, not the model's
            self.assertTrue(any("the rules answer" in line for line in said), said)
            self.assertEqual(bridge.running, {})
        finally:
            release.set()
            first_pool.shutdown(wait=True)
            bridge.pool.shutdown(wait=True)
        bridge.poll_once()                                              # its late answer is not sent again
        self.assertEqual(len(sent), 1)

    def test_a_thought_queued_behind_a_hung_one_moves_to_a_fresh_pool(self):
        import threading
        from unittest import mock
        from companion import strategist
        release, sent, asked = threading.Event(), [], ["1:s1"]

        class HangsOnFirst:
            source = "llm"

            def decide(self, request):
                if request["request_id"] == "1:s1":
                    release.wait(10)
                return [{"tool": "say", "args": {"text": "thought " + request["request_id"]}, "priority": "next"}]

        def call(command):
            if "observe" in command:
                return {"requests": [{"request_id": rid, "npc_id": int(rid[0]), "reason": "events",
                                      "events": [{"kind": "chat", "from": "Andre", "text": "hi"}],
                                      "situation": {"name": "Ada"}} for rid in asked]}
            sent.append(json.loads(command.split(" ", 2)[2]))
            return {"accepted": 1}
        bridge = AgentBridge(call, HangsOnFirst(), log=lambda _: None, workers=1)
        first_pool = bridge.pool
        try:
            with mock.patch.object(strategist, "ANSWER_S", 1.0):
                bridge.poll_once()                       # 1:s1 hangs on the only worker
                time.sleep(0.6)
                asked.append("2:s1")
                bridge.poll_once()                       # 2:s1 queues behind it
                time.sleep(0.5)
                bridge.poll_once()                       # 1:s1 past its time: the rules; 2:s1 moved over
                time.sleep(0.3)
                bridge.poll_once()
            by_rid = {s["request_id"]: json.dumps(s["intentions"]) for s in sent}
            self.assertEqual(set(by_rid), {"1:s1", "2:s1"})
            self.assertIn("thought 2:s1", by_rid["2:s1"])              # thought over, in its own time
            self.assertNotIn("thought 1:s1", by_rid["1:s1"])
        finally:
            release.set()
            first_pool.shutdown(wait=True)
            bridge.pool.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
