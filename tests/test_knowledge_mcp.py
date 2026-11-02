"""The designer's lookup tool (companion/knowledge_mcp.py) and the Codex runs that carry it."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import codex_backend, knowledge_mcp  # noqa: E402
from companion.codex_backend import Codex  # noqa: E402
from test_codex_backend import SCHEMA, FakeCodex, FakeRun  # noqa: E402
from test_rates import FACTS, GEARS, PLATES  # noqa: E402

KNOWLEDGE = {"facts": FACTS, "recipes": [GEARS, PLATES],
             "ledger": [{"item": "iron-plate", "supply": 1.25, "average": 0.6, "peak": 1.0, "made": 0.82,
                         "headroom": 0.65}]}


def call(k, tool, **arguments):
    reply = knowledge_mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                  "params": {"name": tool, "arguments": arguments}}, k)
    return reply["result"]["content"][0]["text"], reply["result"]["isError"]


class ServerTests(unittest.TestCase):
    def test_it_speaks_mcp_and_lists_its_read_only_tools(self):
        k = knowledge_mcp.Knowledge(KNOWLEDGE)
        init = knowledge_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18"}}, k)
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(knowledge_mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, k))
        tools = knowledge_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, k)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], codex_backend.LOOKUP_TOOLS)
        self.assertEqual(knowledge_mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, k)["error"]["code"],
                         -32601)

    def test_its_answers_come_from_the_games_numbers(self):
        k = knowledge_mcp.Knowledge(KNOWLEDGE, limit=10)
        text, error = call(k, "recipe", item="iron-gear-wheel")
        self.assertFalse(error)
        self.assertIn("2 iron-plate -> 1 iron-gear-wheel; made in assembling-machine-1 1 a second", text)
        self.assertIn("burns 0.0225 coal a second", call(k, "machine", name="stone-furnace")[0])
        self.assertIn("moves 0.84 items a second", call(k, "machine", name="inserter")[0])
        rate = call(k, "rate", item="iron-gear-wheel", per_second=3, machine="assembling-machine-2")[0]
        self.assertIn("2.00 assembling-machine-2 (build 2), taking 6 iron-plate a second", rate)
        self.assertIn("one offshore-pump feeds 20 boilers", call(k, "steam")[0])
        self.assertIn("supply 1.25 a second, used 0.6 on average", call(k, "supply", item="iron-plate")[0])
        self.assertIn("No recipe", call(k, "recipe", item="plastic-bar")[0])
        self.assertIn("No building", call(k, "machine", name="nonesuch")[0])

    def test_it_answers_three_lookups_a_design_then_says_to_answer(self):
        k = knowledge_mcp.Knowledge(KNOWLEDGE, limit=3)
        for _ in range(3):
            self.assertFalse(call(k, "steam")[1])
        text, error = call(k, "steam")
        self.assertTrue(error)
        self.assertIn("answer with what you have", text)

    def test_the_server_runs_on_its_own_reading_only_its_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "knowledge.json"
            path.write_text(json.dumps(KNOWLEDGE), encoding="utf-8")
            lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"},
                     {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "machine", "arguments": {"name": "boiler"}}}]
            done = subprocess.run([sys.executable, str(ROOT / "companion" / "knowledge_mcp.py"), str(path), "--limit", "1"],
                                  input="\n".join(json.dumps(m) for m in lines) + "\n", capture_output=True, text=True,
                                  timeout=30)
        replies = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertIn("burns 0.45 coal a second", replies[1]["result"]["content"][0]["text"])

    def test_what_a_design_may_look_up_and_none_without_the_games_facts(self):
        from test_ledger import block
        site = {"game_facts": FACTS, "recipes": [GEARS], "base": [block("smelting-iron-plate", makes={"iron-plate": 2})]}
        k = knowledge_mcp.knowledge_for(site)
        self.assertEqual(k["recipes"][0]["name"], "iron-gear-wheel")
        self.assertEqual(k["ledger"][0]["item"], "iron-plate")
        self.assertIsNone(knowledge_mcp.knowledge_for({"recipes": [GEARS]}))


class CodexRunTests(unittest.TestCase):
    def test_a_design_run_gets_the_tool_and_only_it_keeps_the_code_mode_host(self):
        run = FakeRun([{"goal": "x"}])
        seen = {}

        def capture(command, **kw):
            facts = command[command.index("-C") + 1]
            seen["knowledge"] = json.loads((Path(facts) / "knowledge.json").read_text(encoding="utf-8"))
            seen["instructions"] = (Path(facts) / "instructions.md").read_text(encoding="utf-8")
            return run(command, **kw)
        Codex("codex.exe", run=capture).ask("Design.", SCHEMA, model="gpt-6.1-sol", system="designer",
                                            knowledge=KNOWLEDGE, lookups=3)
        command = run.calls[0][0]
        configs = [command[i + 1] for i, c in enumerate(command) if c == "-c"]
        disabled = {command[i + 1] for i, c in enumerate(command) if c == "--disable"}
        self.assertNotIn("code_mode_host", disabled)
        self.assertIn("shell_tool", disabled)                                  # still no shell
        self.assertIn('mcp_servers.factorio.default_tools_approval_mode="approve"', configs)
        self.assertIn("mcp_servers.factorio.enabled_tools=" + json.dumps(codex_backend.LOOKUP_TOOLS), configs)
        args = next(c for c in configs if c.startswith("mcp_servers.factorio.args="))
        self.assertIn("knowledge_mcp.py", args)
        self.assertIn('"--limit", "3"', args)
        self.assertEqual(seen["knowledge"]["ledger"][0]["item"], "iron-plate")
        self.assertIn("look up at most 3", seen["instructions"])
        # any other run: as locked down as before
        plain = FakeRun([{"goal": "x"}])
        Codex("codex.exe", run=plain).ask("Pick.", SCHEMA, model="gpt-6.1-sol", system="strategist")
        command = plain.calls[0][0]
        self.assertIn("code_mode_host", {command[i + 1] for i, c in enumerate(command) if c == "--disable"})
        self.assertFalse(any("mcp_servers" in c for c in command))

    def test_the_designer_passes_its_knowledge_only_with_the_games_facts(self):
        from test_designer_planner import site, wire
        from designer_llm.proposal_schema import to_wire
        from companion import layout_planner
        from companion.designer_planner import LLMPlanner, build_context
        with_facts = {**site(), "game_facts": FACTS}
        good = layout_planner.plan(with_facts)
        codex = FakeCodex(to_wire(wire(build_context(with_facts), good)), to_wire(wire(build_context(site()), good)))
        LLMPlanner(model="gpt-6.1-sol", codex=codex, log=lambda _: None)(with_facts)
        LLMPlanner(model="gpt-6.1-sol", codex=codex, log=lambda _: None)(copy.deepcopy(site()))
        first, second = codex.asked[0][2], codex.asked[1][2]
        self.assertEqual((first["lookups"], bool(first["knowledge"]["facts"])), (codex_backend.DESIGNER_LOOKUPS, True))
        self.assertNotIn("knowledge", second)


if __name__ == "__main__":
    unittest.main()
