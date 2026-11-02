"""Regression checks for the contract exposed to the model; no provider calls."""

import copy
import json
from pathlib import Path
import unittest

from designer_llm._reference_contract import ContractError, parse_proposal, parse_snapshot
from designer_llm.private_config import ResponsesConfig
from designer_llm.proposal_schema import from_wire, proposal_schema, to_wire, validate_wire
from designer_llm.providers import prepare_request


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class GenerationContractTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads((FIXTURES / "proposal.json").read_text(encoding="utf-8"))
        context = json.loads((FIXTURES / "context.json").read_text(encoding="utf-8"))
        self.snapshot = parse_snapshot(context["geometry_snapshot"])
        self.wire = to_wire(self.raw)

    def test_actual_payload_teaches_same_stage_functional_witnesses(self):
        schema = proposal_schema()
        payload = prepare_request(ResponsesConfig("explicit-fixture-model"),
                                  {"request": {"goal": "fixture contract test"}}, schema)
        instructions = payload["input"][0]["content"]
        for rule in ("every proposed entity must belong to exactly one stage",
                     "No witness-only", "same stage", "nonempty placement bill", "1-64",
                     "product_counter_delta", "resource_extraction_delta", "energy_generated_delta",
                     "alone do not satisfy this flow rule", "null item/min_delta", "acyclic",
                     "manual_supplies", "wire chains", "compatible resources", "crafting categories",
                     "Missing facts cause rejection"):
            with self.subTest(rule=rule):
                self.assertIn(rule, instructions)
        self.assertEqual(payload["text"]["format"]["schema"], schema)
        self.assertFalse(payload["store"])

    def test_rejected_witness_only_stage_shape_is_blocked_by_wire_schema(self):
        for field, empty in (("entity_ids", []), ("planned_bill", []), ("witnesses", [])):
            proposal = copy.deepcopy(self.wire)
            proposal["stages"][1][field] = empty
            with self.subTest(field=field), self.assertRaises(ContractError):
                from_wire(proposal)

    def test_short_ids_and_name_hash_formats_match_reference_limits(self):
        validate_wire("A" * 64, proposal_schema()["properties"]["plan_id"])
        for value in ("A" * 65, "1starts-with-number", "spaces are forbidden", "valid-id\n", ""):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_wire(value, proposal_schema()["properties"]["plan_id"])
        entity = proposal_schema()["properties"]["entities"]["items"]["properties"]
        validate_wire("P" * 128, entity["prototype"])
        validate_wire(None, entity["recipe"])
        with self.assertRaises(ContractError):
            validate_wire("P" * 129, entity["prototype"])
        for value in ("a" * 63, "A" * 64, "a" * 65):
            with self.subTest(hash=value), self.assertRaises(ContractError):
                validate_wire(value, proposal_schema()["properties"]["snapshot_sha256"])

    def test_provider_bounds_match_frozen_counts_and_delta_ranges(self):
        props = proposal_schema()["properties"]
        stage = props["stages"]["items"]["properties"]
        for row, minimum, maximum in ((props["entities"], 1, 128), (props["stages"], 1, 64),
                                      (stage["planned_bill"], 1, 128), (stage["witnesses"], 1, 32),
                                      (stage["entity_ids"], 1, 4096), (stage["depends_on"], 0, 4096)):
            self.assertEqual((row["minItems"], row["maxItems"]), (minimum, maximum))
        count = stage["planned_bill"]["items"]["properties"]["count"]
        delta = stage["witnesses"]["items"]["properties"]["min_delta"]
        for schema, good_values, bad_values in (
            (count, (1, 100_000), (0, 100_001, True, 10 ** 1000)),
            (delta, (None, 1, 1_000_000), (0, 1_000_001)),
            (props["observed_tick"], (0, 1_000_000_000), (-1, 1_000_000_001)),
        ):
            for value in good_values:
                validate_wire(value, schema)
            for value in bad_values:
                with self.subTest(value=value), self.assertRaises(ContractError):
                    validate_wire(value, schema)
        position = props["entities"]["items"]["properties"]["position"]["properties"]["x"]
        for value in (-1_000_001, 1_000_001, float("nan"), float("inf")):
            with self.subTest(position=value), self.assertRaises(ContractError):
                validate_wire(value, position)

    def test_wire_array_bounds_are_enforced_locally(self):
        props = proposal_schema()["properties"]
        for field in ("entities", "stages"):
            too_many = copy.deepcopy(self.wire)
            too_many[field] = [self.wire[field][0]] * (props[field]["maxItems"] + 1)
            empty = copy.deepcopy(self.wire)
            empty[field] = []
            for value in (too_many, empty):
                with self.subTest(field=field), self.assertRaises(ContractError):
                    validate_wire(value)

    def test_frozen_parser_still_enforces_semantics_after_wire_shape(self):
        self.assertEqual(from_wire(self.wire), self.raw)
        parse_proposal(from_wire(self.wire), self.snapshot)
        missing_flow = copy.deepcopy(self.wire)
        missing_flow["stages"][0]["witnesses"] = missing_flow["stages"][0]["witnesses"][:2]
        with self.assertRaisesRegex(ContractError, "Functional stage lacks a planned native flow witness"):
            parse_proposal(from_wire(missing_flow), self.snapshot)
        cross_stage = copy.deepcopy(self.wire)
        cross_stage["stages"][0]["witnesses"][-1]["entity_id"] = "second-furnace"
        with self.assertRaisesRegex(ContractError, "Invalid stage witness"):
            parse_proposal(from_wire(cross_stage), self.snapshot)


if __name__ == "__main__":
    unittest.main()
