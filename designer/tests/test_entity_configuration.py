"""Synthetic capability contract tests; no native exports or model proposals."""

import copy
import unittest

from designer_llm._reference_contract import ContractError
from designer_llm.context import ContextError, validate_context
from designer_llm.entity_configuration import (
    configuration_schema, is_legacy_configuration, validate_capability_catalog,
    validate_entity_configuration,
)
from designer_llm.proposal_schema import validate_wire


def synthetic_catalog(identity):
    """Inline declarations for tests only; no installed-prototype claims."""
    def spec(*, upgrades=(), slots=0, settings=(), recipes=False):
        return {"qualities": ["normal", "legendary"], "directions": [0, 4, 8, 12],
                "upgrade_targets": list(upgrades), "module_slots": {"normal": slots, "legendary": slots},
                "allowed_modules": ["speed-module"] if slots else [],
                "settings_kinds": list(settings), "recipe_qualities": ["normal", "legendary"] if recipes else []}
    catalog = {"schema": "designer-entity-capabilities-v1", "identity": copy.deepcopy(identity),
            "source": "synthetic_fixture",
            "entities": {
                "burner-mining-drill": spec(), "stone-furnace": spec(recipes=True),
                "assembling-machine-2": spec(upgrades=["assembling-machine-3"], slots=2, recipes=True),
                "assembling-machine-3": spec(slots=4, recipes=True),
                "arithmetic-combinator": spec(settings=["arithmetic"]),
                "splitter": spec(settings=["splitter"]),
                "stack-inserter": spec(settings=["inserter", "enable_condition"]),
            },
            "modules": {"speed-module": ["normal", "legendary"]},
            "signals": {"iron-plate": {"type": "item", "qualities": ["normal", "legendary"]},
                        "signal-A": {"type": "virtual", "qualities": []},
                        "water": {"type": "fluid", "qualities": []}}}
    for name, row in catalog["entities"].items():
        entity_type = {"burner-mining-drill": "mining-drill", "stone-furnace": "furnace",
                       "assembling-machine-2": "assembling-machine", "assembling-machine-3": "assembling-machine",
                       "stack-inserter": "inserter"}.get(name, name)
        row.update(entity_type=entity_type, module_inventory_index=4 if row["allowed_modules"] else None,
                   circuit_connectors={"1": {"color": "red", "max_distance": 9},
                                       "2": {"color": "green", "max_distance": 9}},
                   filter_slots=5 if entity_type == "inserter" else 0, max_stack_size=12,
                   allowed_recipes=["iron-plate"] if row["recipe_qualities"] else [],
                   recipe_modules={"iron-plate": ["speed-module"]} if row["allowed_modules"] else {})
    return catalog


def operand(*, signal=None, constant=None, red=True, green=True):
    return {"signal": signal, "constant": constant, "networks": {"red": red, "green": green}}


def arithmetic_configuration():
    return {"quality": "legendary", "recipe_quality": None, "modules": [],
            "settings": {"kind": "arithmetic",
                         "first": operand(signal={"type": "item", "name": "iron-plate", "quality": "legendary"}),
                         "second": operand(constant=2), "operation": "*",
                         "output_signal": {"type": "virtual", "name": "signal-A", "quality": None}}}


class EntityConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.identity = {"world_id": "synthetic-world", "force_id": "synthetic-force",
                         "surface_id": "nauvis", "actor_id": "synthetic-actor", "life_id": "life-1",
                         "session_epoch": 1, "game_version": "2.0.77", "source_hash": "a" * 64,
                         "prototype_hash": "b" * 64, "observed_tick": 12000, "revision": 7}
        self.catalog = validate_capability_catalog(synthetic_catalog(self.identity), self.identity)

    def configuration(self, **changes):
        row = {"quality": "normal", "recipe_quality": None, "modules": [], "settings": None}
        row.update(changes)
        return row

    def validate(self, row, prototype="assembling-machine-2", **kwargs):
        validate_wire(row, configuration_schema())
        return validate_entity_configuration(row, prototype, self.catalog, **kwargs)

    def test_catalog_is_bound_to_exact_identity_source_and_per_quality_slots(self):
        for mutate in (
            lambda c: c["identity"].update(session_epoch=2),
            lambda c: c.update(source="assumed-from-name"),
            lambda c: c["entities"]["assembling-machine-2"]["module_slots"].pop("legendary"),
            lambda c: c["entities"]["assembling-machine-2"].update(upgrade_targets=["absent-tier"]),
            lambda c: c["entities"]["assembling-machine-2"].update(allowed_modules=["absent-module"]),
            lambda c: c["signals"]["water"].update(qualities=["legendary"]),
        ):
            value = copy.deepcopy(self.catalog)
            mutate(value)
            with self.assertRaises(ContractError):
                validate_capability_catalog(value, self.identity)
        owned = validate_capability_catalog(self.catalog, self.identity)
        owned["entities"].clear()
        self.assertTrue(self.catalog["entities"])

    def test_entity_module_and_recipe_quality_are_distinct_preserved_choices(self):
        row = self.configuration(quality="legendary", recipe_quality="normal",
                                 modules=[{"item": "speed-module", "quality": "legendary", "count": 2}])
        self.assertEqual(self.validate(row), row)
        self.assertFalse(is_legacy_configuration(row))
        self.assertTrue(is_legacy_configuration(self.configuration()))
        self.assertTrue(is_legacy_configuration(self.configuration(recipe_quality="normal")))
        for changes in (
            {"quality": "unknown-tier"}, {"recipe_quality": "unknown-tier"},
            {"modules": [{"item": "speed-module", "quality": "unknown-tier", "count": 1}]},
            {"modules": [{"item": "productivity-module", "quality": "normal", "count": 1}]},
            {"modules": [{"item": "speed-module", "quality": "normal", "count": 3}]},
            {"modules": [{"item": "speed-module", "quality": "normal", "count": 1}] * 2},
        ):
            with self.assertRaises(ContractError):
                self.validate(self.configuration(**changes))

    def test_workstation_upgrade_requires_declared_graph_and_destination_capabilities(self):
        row = self.configuration(recipe_quality="normal", modules=[{"item": "speed-module", "quality": "normal", "count": 2}])
        self.assertEqual(self.validate(row, "assembling-machine-3", previous_prototype="assembling-machine-2"), row)
        with self.assertRaisesRegex(ContractError, "Workstation upgrade is unavailable"):
            self.validate(row, "assembling-machine-2", previous_prototype="assembling-machine-3")
        with self.assertRaises(ContractError):
            self.validate(row, "stone-furnace", previous_prototype="assembling-machine-2")

    def test_typed_arithmetic_preserves_networks_signals_constants_and_output(self):
        row = arithmetic_configuration()
        self.assertEqual(self.validate(row, "arithmetic-combinator"), row)
        for mutate in (
            lambda r: r["settings"]["first"].update(constant=2),
            lambda r: r["settings"]["second"].update(constant=None),
            lambda r: r["settings"]["first"]["networks"].update(red=1),
            lambda r: r["settings"].update(operation="execute-python"),
            lambda r: r["settings"]["first"]["signal"].update(name="unknown-signal"),
            lambda r: r["settings"]["first"]["signal"].update(quality="unknown-tier"),
            lambda r: r["settings"].update(extra_setting=True),
        ):
            value = copy.deepcopy(row)
            mutate(value)
            with self.assertRaises(ContractError):
                self.validate(value, "arithmetic-combinator")
        with self.assertRaisesRegex(ContractError, "Settings are unsupported by this entity"):
            self.validate(row, "stone-furnace")

    def test_splitter_inserter_and_condition_are_typed_settings_not_arbitrary_json(self):
        settings = {"kind": "splitter", "input_priority": "left", "output_priority": "right", "filter": "iron-plate"}
        self.assertEqual(self.validate(self.configuration(settings=settings), "splitter")["settings"], settings)
        settings = {"kind": "inserter", "filter_mode": "blacklist", "filters": ["iron-plate"], "stack_size": 4}
        self.assertEqual(self.validate(self.configuration(settings=settings), "stack-inserter")["settings"], settings)
        settings = {"kind": "enable_condition", "first": operand(signal={"type": "item", "name": "iron-plate", "quality": "normal"}),
                    "second": operand(constant=100, red=False), "comparator": "<"}
        self.assertEqual(self.validate(self.configuration(settings=settings), "stack-inserter")["settings"], settings)
        bad = copy.deepcopy(settings)
        bad["second"]["constant"] = 2**31
        with self.assertRaises(ContractError):
            self.validate(self.configuration(settings=bad), "stack-inserter")

    def test_configuration_schema_remains_closed_through_nullable_variants(self):
        def inspect(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for value in node.values(): inspect(value)
            elif isinstance(node, list):
                for value in node: inspect(value)
        inspect(configuration_schema())
        self.validate(self.configuration())
        for mutate in (lambda r: r.update(python_to_execute="arbitrary"),
                       lambda r: r.update(modules=[{"item": "speed-module", "quality": "normal", "count": True}])):
            value = self.configuration()
            mutate(value)
            with self.assertRaises(ContractError):
                self.validate(value)


if __name__ == "__main__":
    unittest.main()
