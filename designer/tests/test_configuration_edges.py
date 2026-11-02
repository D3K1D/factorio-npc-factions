"""Malformed and provenance edges for declared rich intent; no engine calls."""

import copy
import json
from pathlib import Path
import unittest

from designer_llm._reference_contract import ContractError
from designer_llm.context import ContextError, validate_context
from designer_llm.editor import DraftGridController
from designer_llm.entity_configuration import (
    is_legacy_configuration, validate_capability_catalog, validate_entity_configuration,
)
from designer_llm.request import make_request
from tests.test_entity_configuration import arithmetic_configuration, synthetic_catalog


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def normal_configuration():
    return {"quality": "normal", "recipe_quality": None, "modules": [], "settings": None}


class ConfigurationEdgeTests(unittest.TestCase):
    def setUp(self):
        self.context = validate_context(json.loads((FIXTURES / "context.json").read_text(encoding="utf-8")))
        self.identity = self.context["identity"]
        self.catalog = validate_capability_catalog(synthetic_catalog(self.identity), self.identity)

    def context_with_catalog(self, *, catalog=None):
        row = copy.deepcopy(self.context)
        row["entity_capabilities"] = {
            "knowledge": "known", "value": copy.deepcopy(self.catalog if catalog is None else catalog),
            "observed_tick": self.identity["observed_tick"], "revision": self.identity["revision"],
            "provenance": {"kind": "fixture", "source_id": "synthetic-capability-edge-test"},
        }
        return row

    def test_malformed_nested_settings_raise_fixed_contract_errors(self):
        malformed_settings = [[], True, 1, "arithmetic", {}, {"kind": []}, {"kind": {}}]
        for settings in malformed_settings:
            value = normal_configuration()
            value["settings"] = settings
            with self.subTest(settings=settings), self.assertRaises(ContractError):
                validate_entity_configuration(value, "arithmetic-combinator", self.catalog)
        for mutation in (
            lambda r: r["settings"].update(first=[]),
            lambda r: r["settings"]["first"].update(signal=True),
            lambda r: r["settings"]["first"]["signal"].update(type=[]),
            lambda r: r["settings"]["first"]["signal"].update(quality={}),
            lambda r: r["settings"]["second"].update(constant=[2]),
            lambda r: r["settings"]["second"].update(networks=[]),
            lambda r: r["settings"]["second"]["networks"].update(red=[]),
            lambda r: r["settings"].update(operation=[]),
            lambda r: r["settings"].update(output_signal=[]),
        ):
            value = arithmetic_configuration()
            mutation(value)
            with self.assertRaises(ContractError):
                validate_entity_configuration(value, "arithmetic-combinator", self.catalog)

    def test_malformed_prototype_arguments_are_contract_failures(self):
        for prototype in ([], {}, True, None, ""):
            with self.subTest(prototype=prototype), self.assertRaises(ContractError):
                validate_entity_configuration(normal_configuration(), prototype, self.catalog)
        for previous in ([], {}, True, ""):
            with self.subTest(previous=previous), self.assertRaises(ContractError):
                validate_entity_configuration(normal_configuration(), "assembling-machine-3", self.catalog,
                                              previous_prototype=previous)

    def test_capability_identity_requires_exact_json_types(self):
        for key in ("observed_tick", "revision", "session_epoch"):
            catalog = copy.deepcopy(self.catalog)
            catalog["identity"][key] = float(catalog["identity"][key])
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_capability_catalog(catalog, self.identity)

    def test_item_filters_require_declared_item_type(self):
        for name in ("water", "signal-A", "absent-item"):
            for prototype, settings in (
                ("splitter", {"kind": "splitter", "input_priority": "none",
                              "output_priority": "right", "filter": name}),
                ("stack-inserter", {"kind": "inserter", "filter_mode": "whitelist",
                                   "filters": [name], "stack_size": None}),
            ):
                value = normal_configuration()
                value["settings"] = settings
                with self.subTest(name=name, prototype=prototype), self.assertRaises(ContractError):
                    validate_entity_configuration(value, prototype, self.catalog)

    def test_signal_type_and_quality_cannot_borrow_other_capabilities(self):
        for mutation in (
            lambda r: r["settings"]["first"]["signal"].update(type="fluid"),
            lambda r: r["settings"]["output_signal"].update(type="item"),
            lambda r: r["settings"]["output_signal"].update(quality="legendary"),
            lambda r: r["settings"].update(output_signal={"type": "fluid", "name": "water", "quality": "normal"}),
        ):
            value = arithmetic_configuration()
            mutation(value)
            with self.assertRaises(ContractError):
                validate_entity_configuration(value, "arithmetic-combinator", self.catalog)

    def test_module_capacity_uses_entity_quality_separately_from_module_quality(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["entities"]["assembling-machine-2"]["module_slots"] = {"normal": 1, "legendary": 3}
        catalog = validate_capability_catalog(catalog, self.identity)
        value = normal_configuration()
        value["modules"] = [{"item": "speed-module", "quality": "legendary", "count": 2}]
        with self.assertRaisesRegex(ContractError, "Module capacity exceeded"):
            validate_entity_configuration(value, "assembling-machine-2", catalog)
        value.update(quality="legendary", recipe_quality="normal",
                     modules=[{"item": "speed-module", "quality": "normal", "count": 3}])
        preserved = validate_entity_configuration(value, "assembling-machine-2", catalog)
        self.assertEqual(preserved, value)
        self.assertFalse(is_legacy_configuration(preserved))
        preserved["modules"][0]["count"] = 1
        self.assertEqual(value["modules"][0]["count"], 3)

    def test_optional_context_capabilities_preserve_unknown_source_and_freshness_rules(self):
        unknown = self.context_with_catalog()
        unknown["entity_capabilities"].update(knowledge="unknown", value=None)
        controller = DraftGridController(make_request(unknown, "Synthetic unknown capabilities"), unknown)
        self.assertFalse(controller.state["configuration_capabilities_known"])
        for mutation in (
            lambda c: c["entity_capabilities"]["provenance"].update(kind="native_snapshot"),
            lambda c: c["entity_capabilities"]["value"].update(source="native_export_unverified"),
            lambda c: c["entity_capabilities"].update(observed_tick=self.identity["observed_tick"] + 1),
            lambda c: c["entity_capabilities"]["value"]["identity"].update(session_epoch=999),
        ):
            row = self.context_with_catalog()
            mutation(row)
            with self.assertRaises((ContextError, ContractError)):
                validate_context(row)
        stale = self.context_with_catalog()
        stale["entity_capabilities"]["observed_tick"] = 0
        with self.assertRaisesRegex(ContractError, "Entity capabilities are stale"):
            make_request(stale, "Cannot use stale capabilities")
        outdated = self.context_with_catalog()
        outdated["entity_capabilities"].update(observed_tick=self.identity["observed_tick"] - 1)
        with self.assertRaises(ContractError):
            DraftGridController(make_request(outdated, "Require current capability observation"), outdated)

    def test_orientation_and_upgrade_remain_gated_when_catalog_is_known(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["entities"]["burner-mining-drill"]["directions"] = [0]
        row = self.context_with_catalog(catalog=catalog)
        controller = DraftGridController(make_request(row, "Capability-gated edits"), row)
        self.assertTrue(controller.call("place_entity", {"id": "drill", "prototype": "burner-mining-drill",
                        "x": 10, "y": 10, "direction": 0, "recipe": None})["ok"])
        before = controller.state
        rotated = controller.call("rotate_entity", {"id": "drill", "direction": 4})
        self.assertFalse(rotated["ok"])
        self.assertEqual(controller.state, before)
        replaced = controller.call("upgrade_entity", {"id": "drill", "prototype": "stone-furnace"})
        self.assertFalse(replaced["ok"])
        self.assertEqual(controller.state, before)

    def test_malformed_exporter_metadata_is_rejected_before_use(self):
        for mutation in (
            lambda spec: spec.update(entity_type=[]),
            lambda spec: spec.update(module_inventory_index=True),
            lambda spec: spec.update(module_inventory_index=0),
            lambda spec: spec.update(circuit_connectors={"01": {"color": "red", "max_distance": 9}}),
            lambda spec: spec.update(circuit_connectors={"1": {"color": [], "max_distance": 9}}),
            lambda spec: spec.update(circuit_connectors={"1": {"color": "red", "max_distance": []}}),
            lambda spec: spec.update(recipe_modules={"iron-plate": ["absent-module"]}),
            lambda spec: spec.update(filter_slots=129),
            lambda spec: spec.update(max_stack_size=0),
        ):
            catalog = copy.deepcopy(self.catalog)
            mutation(catalog["entities"]["assembling-machine-2"])
            with self.assertRaises(ContractError):
                validate_capability_catalog(catalog, self.identity)

    def test_rich_submit_refuses_unsupported_mapping_without_losing_configuration(self):
        row = self.context_with_catalog()
        controller = DraftGridController(make_request(row, "Preserve synthetic rich intent"), row)
        for entity in (
            {"id": "first-drill", "prototype": "burner-mining-drill", "x": 10, "y": 10, "direction": 4, "recipe": None},
            {"id": "first-furnace", "prototype": "stone-furnace", "x": 12, "y": 10, "direction": 0, "recipe": "iron-plate"},
        ):
            self.assertTrue(controller.call("place_entity", entity)["ok"])
        configuration = normal_configuration()
        configuration.update(quality="legendary", recipe_quality="normal")
        self.assertTrue(controller.call("configure_entity", {"id": "first-furnace", "configuration": configuration})["ok"])
        raw = json.loads((FIXTURES / "proposal.json").read_text(encoding="utf-8"))
        stage = copy.deepcopy(raw["stages"][0])
        stage["planned_bill"] = [{"item": item, "count": count} for item, count in stage["planned_bill"].items()]
        for witness in stage["witnesses"]:
            witness.setdefault("item", None)
            witness.setdefault("min_delta", None)
        result = controller.call("submit_plan", {"plan_id": "synthetic-rich-intent", "stages": [stage]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "recipe_blueprint_mapping_unavailable")
        self.assertEqual(controller.state["entity_configurations"]["first-furnace"], configuration)
        self.assertTrue(controller.state["editable"])
        self.assertIsNone(controller.submitted_wire)


if __name__ == "__main__":
    unittest.main()
