"""The AI through Codex (the user's ChatGPT plan, 2026-10-23): one locked-down `codex exec` run per question,
answers forced into a schema, usage limits pause the AI. No Codex is run here: its process is faked."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from companion import codex_backend, decisions_api, layout_planner  # noqa: E402
from companion import usage as ledger  # noqa: E402
from companion.codex_backend import Codex, CodexChooser, CodexError  # noqa: E402

SCHEMA = {"type": "object", "properties": {"goal": {"type": "string"}}, "required": ["goal"],
          "additionalProperties": False}


class FakeRun:
    """Stands in for subprocess.run: answers like `codex exec`, and remembers how it was called."""

    def __init__(self, answers=(), returncode=0, stdout=None):
        self.answers, self.returncode, self.calls = list(answers), returncode, []
        self.stdout = stdout

    def __call__(self, command, **kw):
        self.calls.append((command, kw))
        answer_file = Path(command[command.index("-o") + 1])
        if self.answers:
            answer_file.write_text(json.dumps(self.answers.pop(0)), encoding="utf-8")
        events = self.stdout if self.stdout is not None else (
            '{"type":"thread.started"}\n{"type":"turn.completed","usage":{"input_tokens":1200,'
            '"cached_input_tokens":300,"output_tokens":40}}\n')
        return types.SimpleNamespace(returncode=self.returncode, stdout=events.encode(), stderr=b"")


class CodexRunTests(unittest.TestCase):
    def tearDown(self):
        decisions_api.resume_ai()

    def test_one_locked_down_run_on_the_chatgpt_login_and_its_answer(self):
        run = FakeRun([{"goal": "steam-power"}])
        mark = ledger.mark()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-real-key-1234567890"}):
            answer = Codex("codex.exe", run=run).ask("Pick a goal.", SCHEMA, model="gpt-6.1-sol", system="strategist")
        self.assertEqual(answer, {"goal": "steam-power"})
        command, kw = run.calls[0]
        for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--json"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("-s") + 1], "read-only")
        self.assertEqual(command[command.index("-m") + 1], "gpt-6.1-sol")
        disabled = {command[i + 1] for i, c in enumerate(command) if c == "--disable"}
        self.assertTrue({"shell_tool", "unified_exec", "browser_use", "computer_use", "apps", "plugins"} <= disabled)
        self.assertEqual(command[-1], "-")                          # the prompt comes on stdin, not the command line
        self.assertEqual(kw["input"], b"Pick a goal.")
        self.assertNotIn("OPENAI_API_KEY", kw["env"])               # so Codex answers on the ChatGPT login
        entry = ledger.entries(mark)[-1]
        self.assertEqual((entry["system"], entry["api"], entry["cost"], entry["input"], entry["output"]),
                         ("strategist", "codex", 0.0, 1200, 40))

    def test_a_usage_limit_pauses_every_caller_and_the_next_question_is_not_sent(self):
        run = FakeRun(returncode=1, stdout='{"type":"error","message":"You\'ve hit your usage limit. '
                                           'Try again in 2 hours 10 minutes."}\n')
        said = []
        codex = Codex("codex.exe", run=run, log=said.append)
        with self.assertRaises(decisions_api.OutOfCredit):
            codex.ask("Pick a goal.", SCHEMA, model="gpt-6.1-sol")
        self.assertTrue(decisions_api.out_of_credit())
        self.assertIn("usage limit", decisions_api.pause_reason())
        with self.assertRaises(decisions_api.OutOfCredit):
            codex.ask("Pick a goal.", SCHEMA, model="gpt-6.1-sol")
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(len(said), 1)

    def test_any_other_failure_is_an_ordinary_one(self):
        run = FakeRun(returncode=1, stdout='{"type":"error","message":"stream disconnected"}\n')
        with self.assertRaises(CodexError) as failed:
            Codex("codex.exe", run=run).ask("Pick a goal.", SCHEMA, model="gpt-6-luna")
        self.assertNotIsInstance(failed.exception, decisions_api.OutOfCredit)
        self.assertIn("stream disconnected", str(failed.exception))
        self.assertFalse(decisions_api.out_of_credit())

    def test_a_run_out_of_time_says_what_it_was_doing(self):
        # (one-engineer hour, 2026-10-24: a lab design took the whole 180 s and left nothing to tell why)
        import subprocess
        events = ('{"type":"turn.started"}\n'
                  '{"type":"item.started","item":{"type":"mcp_tool_call","tool":"rate","status":"in_progress"}}\n')

        def late(command, **kw):
            raise subprocess.TimeoutExpired(command, 120, output=events.encode())
        with self.assertRaises(CodexError) as failed:
            Codex("codex.exe", run=late).ask("Design.", SCHEMA, model="gpt-6.1-sol", system="designer")
        self.assertIn("no answer within", str(failed.exception))
        self.assertIn("rate in_progress", str(failed.exception))

    def test_no_codex_installed_and_the_engineers_go_by_rule(self):
        from companion.designer_bridge import make_agent
        from companion.strategist import RuleStrategist
        said = []
        with mock.patch.object(codex_backend, "find_codex", return_value=None):
            agent = make_agent("auto", lambda c: "", log=said.append, ai="codex")
        self.assertIsInstance(agent.strategist, RuleStrategist)
        self.assertTrue(any("Codex CLI was not found" in line for line in said))


LIMITED = ("2026-10-26T15:04:01.300981Z  WARN session_loop{thread_id=x}: codex_core::responses_retry: stream "
           "disconnected - retrying sampling request (1/5 in 373.9998598s)... turn_id=x retries=1 max_retries=5 "
           "sampling_error=exceeded retry limit, last status: 429 Too Many Requests\n")
STARTUP = ('{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Code Mode is unavailable '
           'because code-mode host is disabled."}}\n{"type":"turn.started"}\n')


class Clock:
    """Stands in for codex_backend's time: a run out of time takes its whole limit, a sleep its seconds."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    perf_counter = monotonic

    def sleep(self, seconds):
        self.now += seconds


class ModelRun(FakeRun):
    """Like FakeRun, with what each model does: "answer", a line of Codex's log (watched as it comes),
    "slow" (out of time) or "429" (Codex gave up on its own retries)."""

    def __init__(self, clock=None, **by_model):
        super().__init__()
        self.clock, self.by_model = clock, by_model

    def __call__(self, command, **kw):
        self.calls.append((command, kw))
        model = command[command.index("-m") + 1]
        does = self.by_model[model]
        if does == "slow":
            if self.clock:
                self.clock.sleep(kw["timeout"])
            raise subprocess.TimeoutExpired(command, kw["timeout"], output=STARTUP.encode())
        if does == "429":
            error = '{"type":"turn.failed","error":{"message":"exceeded retry limit, last status: 429 Too Many ' \
                    'Requests"}}\n'
            return types.SimpleNamespace(returncode=1, stdout=(STARTUP + error).encode(), stderr=b"")
        if does != "answer":
            reason = kw["watch"]("stderr", does)
            if reason:
                raise codex_backend.Stopped(reason, STARTUP.encode(), does.encode())
        Path(command[command.index("-o") + 1]).write_text(json.dumps({"goal": model}), encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=b'{"type":"turn.completed","usage":{}}\n', stderr=b"")

    def models(self):
        return [command[command.index("-m") + 1] for command, _ in self.calls]


class RateLimitTests(unittest.TestCase):
    """A model's rate limit (HTTP 429): Codex waits out the reset the server names (374 s) and says so only in
    its log, so the run sat silent until the 120 s limit (4 of 20 strategist runs in run 52, 2026-10-26)."""

    def setUp(self):
        Codex._cooling.clear()

    def tearDown(self):
        Codex._cooling.clear()
        decisions_api.resume_ai()

    def test_codex_retry_notices_are_read_from_its_log(self):
        self.assertEqual(codex_backend.retry_notice(LIMITED),
                         (373.9998598, "exceeded retry limit, last status: 429 Too Many Requests"))
        self.assertEqual(codex_backend.retry_notice("retrying sampling request (2/5 in 200ms)..."),
                         (0.2, "the stream broke"))
        self.assertIsNone(codex_backend.retry_notice('{"type":"turn.started"}'))

    def test_a_rate_limited_model_is_left_at_once_for_every_caller_and_the_fallback_answers(self):
        clock = Clock()
        with mock.patch.object(codex_backend, "time", clock):
            self.rate_limited_model(clock)

    def rate_limited_model(self, clock):
        run, said = ModelRun(clock, **{"gpt-6.1-sol": LIMITED, "gpt-6-luna": "answer"}), []
        codex = Codex("codex.exe", run=run, log=said.append)
        answer = codex.ask("Think.", SCHEMA, model="gpt-6.1-sol", system="strategist", timeout_s=50,
                           attempt_s=35, fallback="gpt-6-luna")
        self.assertEqual((answer, run.models()), ({"goal": "gpt-6-luna"}, ["gpt-6.1-sol", "gpt-6-luna"]))
        self.assertEqual(run.calls[0][1]["env"]["RUST_LOG"], codex_backend.RETRY_LOG)   # its retries, logged
        self.assertGreater(Codex.cooling("gpt-6.1-sol"), 360)
        self.assertTrue(any("gpt-6.1-sol is rate limited for 374 s" in line for line in said), said)
        self.assertTrue(any("asking gpt-6-luna" in line for line in said), said)
        self.assertFalse(decisions_api.out_of_credit())                 # one model's limit, not the account's
        # Another caller now goes straight to its fallback; one without a fallback fails at once, running nothing.
        codex.ask("Think again.", SCHEMA, model="gpt-6.1-sol", timeout_s=50, attempt_s=35, fallback="gpt-6-luna")
        self.assertEqual(run.models(), ["gpt-6.1-sol", "gpt-6-luna", "gpt-6-luna"])
        with self.assertRaises(CodexError) as failed:
            codex.ask("Design.", SCHEMA, model="gpt-6.1-sol", system="designer")
        self.assertIn("rate limited for another", str(failed.exception))
        self.assertEqual(len(run.calls), 3)
        # One with time to wait out the reset (the architect's 600 s, in the background) asks it after the
        # reset, not during it (asked during its limit, it was limited for 600 s more).
        run.by_model["gpt-6.1-sol"] = "answer"
        before = clock.now
        codex.ask("Plan.", SCHEMA, model="gpt-6.1-sol", system="architect", timeout_s=600)
        self.assertEqual(run.models()[-1], "gpt-6.1-sol")
        self.assertGreater(clock.now - before, 370)

    def test_a_retry_it_can_wait_for_is_left_to_codex(self):
        brief = "retrying sampling request (1/5 in 2.5s)... sampling_error=stream disconnected before completion"
        run = ModelRun(**{"gpt-6.1-sol": brief})
        self.assertEqual(Codex("codex.exe", run=run).ask("Think.", SCHEMA, model="gpt-6.1-sol"),
                         {"goal": "gpt-6.1-sol"})
        self.assertEqual(Codex.cooling("gpt-6.1-sol"), 0.0)             # a broken stream cools nothing down

    def test_a_first_model_out_of_time_gives_way_to_the_fallback_in_the_time_left(self):
        clock, said = Clock(), []
        run = ModelRun(clock, **{"gpt-6.1-sol": "slow", "gpt-6-luna": "answer"})
        with mock.patch.object(codex_backend, "time", clock):
            answer = Codex("codex.exe", run=run, log=said.append).ask(
                "Think.", SCHEMA, model="gpt-6.1-sol", timeout_s=50, attempt_s=35, fallback="gpt-6-luna")
        self.assertEqual(answer, {"goal": "gpt-6-luna"})
        self.assertEqual([kw["timeout"] for _, kw in run.calls], [35, 15])   # the fallback gets what is left
        self.assertTrue(any("no answer within 35 s (1 turns, nothing from the model yet)" in line
                            for line in said), said)                    # (the startup notice is no step)
        # Both out of time: one failure naming both.
        run = ModelRun(clock, **{"gpt-6.1-sol": "slow", "gpt-6-luna": "slow"})
        with mock.patch.object(codex_backend, "time", clock), self.assertRaises(CodexError) as failed:
            Codex("codex.exe", run=run, log=said.append).ask(
                "Think.", SCHEMA, model="gpt-6.1-sol", timeout_s=50, attempt_s=35, fallback="gpt-6-luna")
        self.assertRegex(str(failed.exception), r"gpt-6\.1-sol: no answer within 35 s.*gpt-6-luna: no answer within 15 s")
        # Too little left after the first: the fallback is not started.
        run = ModelRun(clock, **{"gpt-6.1-sol": "slow", "gpt-6-luna": "answer"})
        with mock.patch.object(codex_backend, "time", clock), self.assertRaises(CodexError) as failed:
            Codex("codex.exe", run=run, log=said.append).ask(
                "Think.", SCHEMA, model="gpt-6.1-sol", timeout_s=38, attempt_s=35, fallback="gpt-6-luna")
        self.assertIn("no time left for gpt-6-luna", str(failed.exception))
        self.assertEqual(run.models(), ["gpt-6.1-sol"])

    def test_a_model_that_gave_up_on_a_429_cools_down_and_pauses_nothing_else(self):
        # (LIMIT_WORDS had "too many requests": one model's 429 paused every AI caller for 30 minutes)
        run, said = ModelRun(**{"gpt-6.1-sol": "429", "gpt-6-luna": "answer"}), []
        answer = Codex("codex.exe", run=run, log=said.append).ask("Think.", SCHEMA, model="gpt-6.1-sol",
                                                                  fallback="gpt-6-luna")
        self.assertEqual(answer, {"goal": "gpt-6-luna"})
        self.assertFalse(decisions_api.out_of_credit())
        self.assertAlmostEqual(Codex.cooling("gpt-6.1-sol"), codex_backend.RATE_COOL_S, delta=5)
        self.assertFalse(codex_backend.limit_reached("Rate limit reached for gpt-6.1-sol. Please try again in 20s."))
        self.assertTrue(codex_backend.limit_reached("You've hit your usage limit. Try again in 2 hours."))

    def test_the_last_model_waits_out_a_reset_that_leaves_time_to_answer(self):
        clock = Clock()
        with mock.patch.object(codex_backend, "time", clock):
            Codex._cooling["gpt-6.1-sol"] = clock.now + 30
            run = ModelRun(clock, **{"gpt-6.1-sol": "answer"})
            Codex("codex.exe", run=run, log=lambda _: None).ask("Design.", SCHEMA, model="gpt-6.1-sol")
        self.assertEqual(clock.now, 1030)                                # asked once the limit was over
        self.assertAlmostEqual(run.calls[0][1]["timeout"], codex_backend.TIMEOUT_S - 30)

    def test_waiting_for_a_free_run_counts_against_the_time(self):
        held = [Codex._slots.acquire(timeout=1) for _ in range(codex_backend.MAX_RUNNING)]
        try:
            run, started = ModelRun(**{"gpt-6.1-sol": "answer"}), time.monotonic()
            with self.assertRaises(CodexError) as failed:
                Codex("codex.exe", run=run).ask("Think.", SCHEMA, model="gpt-6.1-sol", timeout_s=0.3)
            self.assertLess(time.monotonic() - started, 2)
            self.assertIn("no free Codex run in time", str(failed.exception))
            self.assertEqual(run.calls, [])
        finally:
            for got in held:
                if got:
                    Codex._slots.release()

    def test_the_usage_limit_is_not_asked_again_of_the_fallback(self):
        run = FakeRun(returncode=1, stdout='{"type":"error","message":"You\'ve hit your usage limit."}\n')
        with self.assertRaises(decisions_api.OutOfCredit):
            Codex("codex.exe", run=run, log=lambda _: None).ask("Think.", SCHEMA, model="gpt-6.1-sol",
                                                              fallback="gpt-6-luna")
        self.assertEqual(len(run.calls), 1)


class WatchedRunTests(unittest.TestCase):
    """The real process runner: output read as it comes, a run stopped as soon as its watcher says so."""

    def script(self, body):
        return [sys.executable, "-c", body]

    def test_its_answer_and_output_like_subprocess_run(self):
        done = codex_backend.watched_run(self.script("import sys; print(sys.stdin.read().upper())"),
                                         input=b"steam power", timeout=30)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, b"STEAM POWER"))

    def test_a_line_its_watcher_names_stops_the_run_at_once(self):
        seen, started = [], time.monotonic()
        body = "import sys, time; print('turn.started', flush=True); sys.stderr.write('429 now\\n'); " \
               "sys.stderr.flush(); time.sleep(30)"
        with self.assertRaises(codex_backend.Stopped) as stopped:
            codex_backend.watched_run(self.script(body), timeout=60,
                                      watch=lambda stream, line: seen.append(stream) or (
                                          "limited" if "429" in line else None))
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(stopped.exception.reason, "limited")
        self.assertIn(b"turn.started", stopped.exception.stdout)
        self.assertIn("stderr", seen)

    def test_out_of_time_it_says_what_it_wrote(self):
        body = "import time; print('turn.started', flush=True); time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired) as late:
            codex_backend.watched_run(self.script(body), timeout=3)
        self.assertIn(b"turn.started", late.exception.output)

    def test_a_long_prompt_it_is_slow_to_read_does_not_outlast_the_limit(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            codex_backend.watched_run(self.script("import sys, time; time.sleep(20); sys.stdin.read()"),
                                      input=b"x" * 2_000_000, timeout=1)
        self.assertLess(time.monotonic() - started, 1 + codex_backend.CLEANUP_S + 3)

    def test_what_it_started_goes_with_it(self):
        # (a lookup server holding its output open kept the readers, and the caller, waiting)
        body = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; "
                "time.sleep(30)'], stdout=sys.stdout, stderr=sys.stderr); print('started', flush=True); "
                "time.sleep(30)")
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            codex_backend.watched_run(self.script(body), timeout=2)
        self.assertLess(time.monotonic() - started, 2 + 2 * codex_backend.CLEANUP_S + 3)


class FakeCodex:
    """Stands in for codex_backend.Codex: queued answers, and the questions it was asked."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def ask(self, prompt, schema, **kw):
        self.asked.append((prompt, schema, kw))
        return copy.deepcopy(self.answers.pop(0))


class StrategistTests(unittest.TestCase):
    def test_the_tools_become_one_answer_and_its_calls_the_engineers_intentions(self):
        from companion.strategist import CodexStrategist
        codex = FakeCodex({"calls": [
            {"tool": "say", "arguments": {"text": "Power is up — a lab next; then turrets.", "to": None}},
            {"tool": "build", "arguments": {"goal": "research-lab", "why": "science", "priority": "next"}}]})
        brain = CodexStrategist(codex, "gpt-6.1-sol", log=lambda _: None)
        request = {"request_id": "1:s1", "npc_id": 1, "reason": "events", "events": [],
                   "situation": {"name": "Ada", "team": True}}
        intentions = brain.decide(request)
        self.assertEqual([i["tool"] for i in intentions], ["say", "build"])
        self.assertNotRegex(intentions[0]["args"]["text"], "[—;]")     # on the radio: no dashes
        self.assertEqual((intentions[1]["args"]["goal"], intentions[1]["priority"]), ("research-lab", "next"))
        prompt, schema, kw = codex.asked[0]
        offered = {option["properties"]["tool"]["enum"][0] for option in schema["properties"]["calls"]["items"]["anyOf"]}
        self.assertIn("say", offered)
        self.assertNotIn("trade", offered)                                  # one team: no deals
        self.assertIn("Ada", prompt)
        self.assertEqual((kw["model"], kw["system"], kw["npc"]), ("gpt-6.1-sol", "strategist", 1))
        self.assertEqual(kw["fallback"], codex_backend.STRATEGIST_FALLBACK)   # a smaller model when it fails

    def test_a_thought_is_answered_before_the_game_drops_it(self):
        import re
        from companion import strategist
        lua = (ROOT / "mod" / "scripts" / "agent.lua").read_text(encoding="utf-8")
        answer_wait = int(re.search(r"local ANSWER_WAIT=(\d+)", lua).group(1))   # ticks, 60 a second at 1x
        self.assertLessEqual(strategist.ANSWER_S + 5, answer_wait / 60)          # (and a poll or two)
        self.assertLess(strategist.FIRST_TRY_S + codex_backend.MIN_TRY_S, strategist.ANSWER_S)
        codex = FakeCodex({"calls": []})
        strategist.CodexStrategist(codex, "gpt-6.1-sol", log=lambda _: None).decide(
            {"request_id": "1:s1", "npc_id": 1, "reason": "routine", "situation": {"name": "Ada", "team": True}})
        kw = codex.asked[0][2]
        self.assertEqual((kw["timeout_s"], kw["attempt_s"]), (strategist.ANSWER_S, strategist.FIRST_TRY_S))
        # The bridge times it from when it saw the request: a wait before the thought comes off its time.
        codex = FakeCodex({"calls": []})
        strategist.CodexStrategist(codex, "gpt-6.1-sol", log=lambda _: None).decide(
            {"request_id": "1:s2", "npc_id": 1, "reason": "routine", "situation": {"name": "Ada", "team": True},
             "answer_by": time.monotonic() + 20})
        self.assertTrue(18 < codex.asked[0][2]["timeout_s"] <= 20)
        seen = []
        bridge = strategist.AgentBridge(lambda c: {"requests": [{"request_id": "1:s3", "npc_id": 1,
                                                                 "reason": "routine"}]} if "observe" in c else {},
                                        types.SimpleNamespace(decide=lambda r: seen.append(r) or [], source="llm"),
                                        log=lambda _: None)
        bridge.poll_once()
        bridge.pool.shutdown(wait=True)
        self.assertAlmostEqual(seen[0]["answer_by"], time.monotonic() + strategist.ANSWER_S, delta=5)


class ChooserTests(unittest.TestCase):
    def test_system_one_picks_one_of_the_offered_options(self):
        from companion.catalog_planner import DecisionsChooser
        codex = FakeCodex({"choice": "repeat-0"})
        chooser = DecisionsChooser(provider=CodexChooser(codex))
        picked, odds = chooser.choose({"npc_name": "Ada", "summary": "it woke up"},
                                      [{"id": "repeat-0", "description": "Repeat: build steam-power"},
                                       {"id": "think", "description": "Think it over."}], question="habit")
        self.assertEqual((picked, odds["repeat-0"], odds["think"]), ("repeat-0", 1.0, 0.0))
        self.assertEqual(codex.asked[0][1]["properties"]["choice"]["enum"], ["repeat-0", "think"])


class DesignerTests(unittest.TestCase):
    def test_a_design_through_codex_and_one_repair_round_with_the_checks_findings(self):
        from test_designer_planner import site, wire
        from designer_llm.proposal_schema import to_wire
        from companion.designer_planner import LLMPlanner, build_context
        context = build_context(site())
        good = layout_planner.plan(site())
        bad = copy.deepcopy(good)
        next(e for e in bad["entities"] if e["id"] == "furnace-1")["position"]["x"] -= 1
        codex = FakeCodex(to_wire(wire(context, bad)), to_wire(wire(context, good)))
        said = []
        plan = LLMPlanner(model="gpt-6.1-sol", codex=codex, log=said.append)(site())
        self.assertEqual([e["id"] for e in plan["entities"]], [e["id"] for e in good["entities"]])
        self.assertEqual(len(codex.asked), 2)
        self.assertIn("rejected by the local checks", codex.asked[1][0])
        self.assertIn("drill_output_unconnected", codex.asked[1][0])
        self.assertEqual((codex.asked[0][2]["model"], codex.asked[0][2]["system"]), ("gpt-6.1-sol", "designer"))
        self.assertTrue(any("asking Codex to fix it" in line for line in said))

    def test_a_design_that_runs_out_of_time_logs_what_codex_said(self):
        from test_designer_planner import site
        from companion.designer_planner import LLMPlanner
        from companion.layout_planner import PlanError

        class Late(FakeCodex):
            def ask(self, prompt, schema, **kw):
                raise CodexError("no answer within 120 s (rate in_progress)")
        said = []
        with self.assertRaises(PlanError):
            LLMPlanner(model="gpt-6.1-sol", codex=Late(), log=said.append)(site())
        self.assertTrue(any("provider_error: no answer within 120 s (rate in_progress)" in line for line in said),
                        said)


if __name__ == "__main__":
    unittest.main()
