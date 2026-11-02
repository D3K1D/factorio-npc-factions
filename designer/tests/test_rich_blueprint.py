"""Offline compiler tests from synthetic declarations; no native import claim."""

import base64
import copy
import json
import unittest
import zlib

from designer_llm._reference_contract import (
    ContractError, SUPPORTED_BLUEPRINT_VERSION, parse_proposal, parse_snapshot,
)
from designer_llm.rich_blueprint import (
    decode_rich_blueprint, encode_rich_blueprint, rich_blueprint_document,
    rich_blueprint_matches,
)


def _operand(signal=None, constant=None, *, red=True, green=True):
    return {"signal": signal, "constant": constant, "networks": {"red": red, "green": green}}


def _encode_document(document):
    raw = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    return "0" + base64.b64encode(zlib.compress(raw, 9)).decode()


class RichBlueprintTests(unittest.TestCase):
    def setUp(self):
        identity = {"world_id": "test-world", "force_id": "test-force", "surface_id": "nauvis",
                    "actor_id": "test-actor", "life_id": "life-1", "session_epoch": 1,
                    "game_version": "2.0.77", "source_hash": "a" * 64, "prototype_hash": "b" * 64,
                    "observed_tick": 12000, "revision": 7}
        types = {"assembling-machine-3": "assembling-machine", "arithmetic-combinator": "arithmetic-combinator",
                 "splitter": "splitter", "stack-inserter": "inserter", "small-electric-pole": "electric-pole",
                 "stone-furnace": "furnace"}
        prototypes = {name: {"placement_item": name, "width": 2, "height": 2} for name in types}
        self.snapshot = parse_snapshot({"schema": "designer-snapshot-v1", "game_version": "2.0.77",
                                       "source_hash": identity["source_hash"], "world_id": "test-world",
                                       "force_id": "test-force", "observed_tick": 12000, "quality": "normal",
                                       "researched_technologies": [], "unlocked_recipes": ["iron-plate"],
                                       "items": list(types) + ["iron-plate", "speed-module"],
                                       "prototypes": prototypes, "survey_bounds": [0, 0, 100, 40], "occupied_boxes": []})
        names = ["assembling-machine-3", "arithmetic-combinator", "splitter", "stack-inserter", "small-electric-pole", "small-electric-pole"]
        self.raw = {key: self.snapshot.raw[key] for key in
                    ("game_version", "source_hash", "world_id", "force_id", "observed_tick", "quality")}
        self.raw.update({"schema": "designer-proposal-v1", "snapshot_sha256": self.snapshot.sha256,
                         "plan_id": "rich-test-plan", "entities": [], "stages": []})
        for number, name in enumerate(names, 1):
            entity = {"id": "entity-" + str(number), "prototype": name,
                      "position": {"x": 10 + 8 * (number - 1), "y": 10}, "direction": 0}
            if number == 1: entity["recipe"] = "iron-plate"
            self.raw["entities"].append(entity)
        bill = {name: names.count(name) for name in set(names)}
        self.raw["stages"] = [{"id": "functional-stage", "depends_on": [],
                               "entity_ids": [row["id"] for row in self.raw["entities"]], "planned_bill": bill,
                               "witnesses": [{"kind": "product_counter_delta", "entity_id": "entity-1", "item": "iron-plate", "min_delta": 1}]}]
        self.proposal = parse_proposal(self.raw, self.snapshot)
        self.catalog = {"schema": "designer-entity-capabilities-v1", "identity": identity, "source": "synthetic_fixture",
                        "entities": {}, "modules": {"speed-module": ["normal", "legendary"]},
                        "signals": {"iron-plate": {"type": "item", "qualities": ["normal", "legendary"]},
                                    "signal-A": {"type": "virtual", "qualities": []}}}
        settings_by_type = {"assembling-machine": ["enable_condition"], "arithmetic-combinator": ["arithmetic"],
                            "splitter": ["splitter"], "inserter": ["inserter", "enable_condition"],
                            "electric-pole": [], "furnace": ["enable_condition"]}
        for name, entity_type in types.items():
            self.catalog["entities"][name] = {
                "qualities": ["normal", "legendary"], "directions": [0, 4, 8, 12], "upgrade_targets": [],
                "module_slots": {"normal": 4 if entity_type == "assembling-machine" else 0,
                                 "legendary": 4 if entity_type == "assembling-machine" else 0},
                "allowed_modules": ["speed-module"] if entity_type == "assembling-machine" else [],
                "settings_kinds": settings_by_type[entity_type], "recipe_qualities": ["normal", "legendary"],
                "entity_type": entity_type,
                "module_inventory_index": 4 if entity_type == "assembling-machine" else None,
                "allowed_recipes": ["iron-plate"], "recipe_modules": {"iron-plate": ["speed-module"] if entity_type == "assembling-machine" else []},
                "filter_slots": 5 if entity_type == "inserter" else 0, "max_stack_size": 16,
                "circuit_connectors": {"1": {"color": "red", "max_distance": 50},
                                       "2": {"color": "green", "max_distance": 50}}}
        self.catalog["entities"]["arithmetic-combinator"]["circuit_connectors"].update({
            "3": {"color": "red", "max_distance": 50}, "4": {"color": "green", "max_distance": 50}})
        self.catalog["entities"]["small-electric-pole"]["circuit_connectors"]["5"] = {"color": "copper", "max_distance": 50}
        self.configurations = {row["id"]: {"quality": "normal", "recipe_quality": None, "modules": [], "settings": None}
                               for row in self.raw["entities"]}
        self.configurations["entity-1"].update({"quality": "legendary", "recipe_quality": "legendary",
                                              "modules": [{"item": "speed-module", "quality": "legendary", "count": 2},
                                                          {"item": "speed-module", "quality": "normal", "count": 1}]})
        self.configurations["entity-2"]["settings"] = {
            "kind": "arithmetic", "first": _operand(signal={"type": "item", "name": "iron-plate", "quality": "legendary"}, green=False),
            "second": _operand(constant=2, red=False), "operation": "*",
            "output_signal": {"type": "virtual", "name": "signal-A", "quality": None}}
        self.configurations["entity-3"]["settings"] = {"kind": "splitter", "input_priority": "left", "output_priority": "right", "filter": "iron-plate"}
        self.configurations["entity-4"]["settings"] = {"kind": "inserter", "filter_mode": "blacklist", "filters": ["iron-plate"], "stack_size": 4}
        self.wires = [{"from_id": "entity-2", "from_connector": 3, "to_id": "entity-1", "to_connector": 1, "color": "red"},
                      {"from_id": "entity-2", "from_connector": 4, "to_id": "entity-4", "to_connector": 2, "color": "green"},
                      {"from_id": "entity-5", "from_connector": 5, "to_id": "entity-6", "to_connector": 5, "color": "copper"}]

    def compile(self, configurations=None, wires=None, catalog=None, proposal=None):
        return encode_rich_blueprint(self.proposal if proposal is None else proposal,
                                     self.configurations if configurations is None else configurations,
                                     self.wires if wires is None else wires,
                                     self.catalog if catalog is None else catalog)

    def test_module_quality_counts_and_zero_based_inventory_slots_roundtrip(self):
        encoded = self.compile()
        self.assertTrue(rich_blueprint_matches(encoded, self.proposal, self.configurations, self.wires, self.catalog))
        blueprint = decode_rich_blueprint(encoded)["blueprint"]
        self.assertEqual(blueprint["version"], SUPPORTED_BLUEPRINT_VERSION)
        assembler = blueprint["entities"][0]
        self.assertEqual(assembler["quality"], "legendary")
        self.assertEqual(assembler["recipe_quality"], "legendary")
        self.assertEqual(assembler["recipe"], "iron-plate")
        self.assertEqual(assembler["items"], [
            {"id": {"name": "speed-module", "quality": "legendary"}, "items": {"in_inventory": [{"inventory": 4, "stack": 0, "count": 1}, {"inventory": 4, "stack": 1, "count": 1}]}},
            {"id": {"name": "speed-module", "quality": "normal"}, "items": {"in_inventory": [{"inventory": 4, "stack": 2, "count": 1}]}},
        ])

    def test_arithmetic_networks_signals_constants_and_output_are_preserved(self):
        arithmetic = decode_rich_blueprint(self.compile())["blueprint"]["entities"][1]["control_behavior"]["arithmetic_conditions"]
        self.assertEqual(arithmetic, {"first_signal": {"type": "item", "name": "iron-plate", "quality": "legendary"},
                                      "first_signal_networks": {"red": True, "green": False}, "second_constant": 2,
                                      "second_signal_networks": {"red": False, "green": True}, "operation": "*",
                                      "output_signal": {"type": "virtual", "name": "signal-A"}})

    def test_splitter_inserter_and_wire_connector_ids_are_exported_exactly(self):
        blueprint = decode_rich_blueprint(self.compile())["blueprint"]
        splitter, inserter = blueprint["entities"][2:4]
        self.assertEqual((splitter["input_priority"], splitter["output_priority"], splitter["filter"]), ("left", "right", "iron-plate"))
        self.assertEqual(inserter["filter_mode"], "blacklist")
        self.assertTrue(inserter["use_filters"])
        self.assertEqual(inserter["filters"], [{"index": 1, "name": "iron-plate"}])
        self.assertEqual(inserter["override_stack_size"], 4)
        self.assertEqual(blueprint["wires"], [[2, 3, 1, 1], [2, 4, 4, 2], [5, 5, 6, 5]])

    def test_enable_condition_maps_only_supported_operands_and_entity_types(self):
        configurations = copy.deepcopy(self.configurations)
        configurations["entity-4"]["settings"] = {"kind": "enable_condition",
            "first": _operand(signal={"type": "item", "name": "iron-plate", "quality": "normal"}),
            "second": _operand(constant=100), "comparator": "<="}
        behavior = decode_rich_blueprint(self.compile(configurations))["blueprint"]["entities"][3]["control_behavior"]
        self.assertEqual(behavior, {"circuit_enabled": True, "circuit_condition": {
            "first_signal": {"type": "item", "name": "iron-plate", "quality": "normal"}, "constant": 100, "comparator": "<="}})
        for edit, message in (
            (lambda row: row["settings"].update(first=_operand(constant=1)), "enable_condition_first_constant_unsupported"),
            (lambda row: row["settings"]["first"]["networks"].update(red=False), "enable_condition_network_selection_unsupported"),
        ):
            value = copy.deepcopy(configurations)
            edit(value["entity-4"])
            with self.assertRaisesRegex(ContractError, message):
                self.compile(value)

    def test_uncertain_inventory_recipe_and_settings_mappings_reject_without_loss(self):
        mutations = [
            (lambda c: c["entities"]["assembling-machine-3"].pop("module_inventory_index"), "module_inventory_mapping_unavailable"),
            (lambda c: c["entities"]["assembling-machine-3"].pop("recipe_modules"), "module_recipe_mapping_unavailable"),
            (lambda c: c["entities"]["assembling-machine-3"].pop("allowed_recipes"), "recipe_capability_mapping_unavailable"),
            (lambda c: c["entities"]["arithmetic-combinator"].pop("entity_type"), "arithmetic_blueprint_mapping_unavailable"),
            (lambda c: c["entities"]["stack-inserter"].pop("filter_slots"), "inserter_filter_mapping_unavailable"),
            (lambda c: c["entities"]["stack-inserter"].pop("max_stack_size"), "inserter_stack_size_mapping_unavailable"),
        ]
        original = copy.deepcopy(self.configurations)
        for mutate, message in mutations:
            catalog = copy.deepcopy(self.catalog)
            mutate(catalog)
            with self.assertRaisesRegex(ContractError, message):
                self.compile(catalog=catalog)
            self.assertEqual(self.configurations, original)

    def test_wire_identity_color_reach_duplicates_and_unknown_connectors_reject(self):
        for edit in (
            lambda w: w[0].update(to_id="absent"),
            lambda w: w[0].update(color="green"),
            lambda w: w[0].update(from_connector=255),
            lambda w: w[0].update(from_connector=True),
            lambda w: w[0].update(to_id="entity-2", to_connector=1),
            lambda w: w[0].update(extra_field=True),
            lambda w: w.append({"from_id": "entity-1", "from_connector": 1, "to_id": "entity-2", "to_connector": 3, "color": "red"}),
        ):
            wires = copy.deepcopy(self.wires)
            edit(wires)
            with self.assertRaises(ContractError):
                self.compile(wires=wires)
        catalog = copy.deepcopy(self.catalog)
        catalog["entities"]["assembling-machine-3"]["circuit_connectors"]["1"]["max_distance"] = 1
        with self.assertRaisesRegex(ContractError, "rich_wire_exceeds_declared_reach"):
            self.compile(catalog=catalog)

    def test_payload_matching_detects_rich_setting_module_and_wire_tampering(self):
        payload = rich_blueprint_document(self.proposal, self.configurations, self.wires, self.catalog)
        for edit in (
            lambda p: p["blueprint"]["entities"][0].update(quality="normal"),
            lambda p: p["blueprint"]["entities"][0]["items"][0]["id"].update(quality="normal"),
            lambda p: p["blueprint"]["entities"][1]["control_behavior"]["arithmetic_conditions"].update(second_constant=3),
            lambda p: p["blueprint"]["wires"][0].__setitem__(1, 1),
        ):
            value = copy.deepcopy(payload)
            edit(value)
            self.assertFalse(rich_blueprint_matches(_encode_document(value), self.proposal, self.configurations, self.wires, self.catalog))

    def test_decoder_rejects_duplicate_keys_trailing_stream_wrong_version_and_oversize(self):
        for raw in ('{"blueprint":{},"blueprint":{}}', '{"blueprint":{"x":1e999}}', '[]'):
            encoded = "0" + base64.b64encode(zlib.compress(raw.encode())).decode()
            with self.assertRaisesRegex(ContractError, "invalid_rich_blueprint"):
                decode_rich_blueprint(encoded)
        encoded = self.compile()
        compressed = base64.b64decode(encoded[1:])
        with self.assertRaises(ContractError):
            decode_rich_blueprint("0" + base64.b64encode(compressed + b"trailing-data").decode())
        with self.assertRaises(ContractError):
            decode_rich_blueprint("0" + base64.b64encode(zlib.compress(b" " * 1_000_001)).decode())
        payload = decode_rich_blueprint(encoded)
        payload["blueprint"]["version"] += 1
        with self.assertRaises(ContractError):
            decode_rich_blueprint(_encode_document(payload))

    def test_full_configuration_identity_and_geometry_integrity_remain_bound(self):
        configurations = copy.deepcopy(self.configurations)
        configurations.pop("entity-6")
        with self.assertRaisesRegex(ContractError, "rich_entity_configuration_ownership_invalid"):
            self.compile(configurations)
        catalog = copy.deepcopy(self.catalog)
        catalog["identity"]["observed_tick"] -= 1
        with self.assertRaisesRegex(ContractError, "rich_capability_geometry_identity_mismatch"):
            self.compile(catalog=catalog)
        before = (copy.deepcopy(self.proposal.raw), copy.deepcopy(self.configurations), copy.deepcopy(self.wires), copy.deepcopy(self.catalog))
        self.compile()
        self.assertEqual(before, (self.proposal.raw, self.configurations, self.wires, self.catalog))
        self.proposal.raw["entities"][0]["position"]["x"] += 1
        with self.assertRaisesRegex(ContractError, "Parsed proposal changed"):
            self.compile()


if __name__ == "__main__":
    unittest.main()
