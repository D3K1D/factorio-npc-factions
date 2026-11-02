"""Tool-derived acceptance must replay to the same model-controlled geometry."""

import copy
import hashlib
import unittest

from designer_llm._reference_contract import ContractError, canonical_bytes
from designer_llm.workflow import AcceptedPlan, propose
from tests import test_tool_provider


class ToolWorkflowTests(unittest.TestCase):
    def test_acceptance_embeds_exact_actions_and_rechecks_geometry_binding(self):
        fixture = test_tool_provider.ToolProviderTests()
        fixture.setUp()
        provider, transport = fixture.provider([
            test_tool_provider.response([fixture.place(0, "drill"), fixture.place(1, "furnace")]),
            test_tool_provider.response([fixture.submit()]),
        ])
        outcome = propose(fixture.request, fixture.context, provider)
        self.assertEqual(outcome.status, "accepted_offline")
        doc = outcome.accepted.checked()
        self.assertEqual(doc["model_tool_trace"], provider.trace)
        self.assertEqual(doc["provider_proposal"], fixture.wire)
        self.assertEqual(doc["provider"]["metadata"]["generation_mode"], "tool_calls")
        self.assertNotIn("raw_proposal_text", doc)
        self.assertEqual(len(transport.calls), 2)
        self.assertFalse(doc["native_flow_qualified"])
        changed = copy.deepcopy(doc)
        changed["model_tool_trace"]["calls"][0]["result"]["state"]["entities"][0]["position"]["x"] += 2
        changed["provider"]["metadata"]["tool_trace_sha256"] = hashlib.sha256(
            canonical_bytes(changed["model_tool_trace"])).hexdigest()
        forged = AcceptedPlan(changed, hashlib.sha256(canonical_bytes(changed)).hexdigest())
        with self.assertRaises(ContractError):
            forged.checked()

    def test_budget_failure_preserves_only_rejected_actual_action_evidence(self):
        fixture = test_tool_provider.ToolProviderTests()
        fixture.setUp()
        provider, transport = fixture.provider([
            test_tool_provider.response([fixture.place(0, "drill")])], max_requests=1)
        outcome = propose(fixture.request, fixture.context, provider)
        self.assertEqual(outcome.status, "rejected")
        self.assertIsNone(outcome.accepted)
        self.assertEqual(outcome.evidence["reason"], "tool_session_budget_exhausted")
        self.assertEqual(outcome.evidence["model_tool_trace"], provider.trace)
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
