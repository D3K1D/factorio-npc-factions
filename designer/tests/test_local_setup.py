"""Controller tests only: no real provider calls or user-facing mock artifacts."""

import json
import copy
import hashlib
from pathlib import Path
import queue
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from designer_llm._reference_contract import ContractError
from designer_llm._reference_contract import canonical_bytes, snapshot_sha256
from designer_llm.__main__ import read_context
from designer_llm.editor import DraftGridController
from designer_llm.request import make_request
from designer_llm.tool_provider import prepare_tool_request
from designer_llm.local_setup import LocalSession, SetupWindow, grid_geometry, entity_inspection
from designer_llm.private_config import PrivateConfigError, ResponsesConfig
from designer_llm.workflow import Outcome


BASE = Path(__file__).resolve().parents[1]
TEST_TEMP = BASE / "work" / "test-temp"
TEST_MODEL = "explicit-local-test-model"
TEST_CREDENTIAL = "synthetic_local_test_bearer_12345"


class LocalSetupTests(unittest.TestCase):
    def setUp(self):
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context_path = self.root / "context.json"
        self.context_path.write_bytes((BASE / "fixtures" / "context.json").read_bytes())
        self.progress_path = self.root / "progress.json"
        self.output_path = self.root / "test-only-result"
        self.session = LocalSession(context_path=self.context_path,
                                    memory_path=self.root / "test-memory.sqlite3",
                                    output_path=self.output_path,
                                    progress_path=self.progress_path)

    def progress(self):
        return json.loads(self.progress_path.read_text(encoding="utf-8"))

    def public_text(self):
        return "\n".join(path.read_text(encoding="utf-8") for path in self.root.rglob("*.json"))

    def factory(self, calls):
        def build(config, supplier):
            self.assertEqual(config.model, TEST_MODEL)
            self.assertEqual(config.max_retries, 0)
            self.assertEqual(supplier(), TEST_CREDENTIAL)
            calls.append(config)
            return object()
        return build

    def rejected_outcome(self):
        return Outcome("rejected", "provider_incomplete", evidence={
            "provider": {"provider_id": "test-only-fake", "mock": True,
                         "metadata": {"model": TEST_MODEL, "attempt_count": 1}}})

    def grid_state(self):
        return {"schema": "designer-draft-grid-v1", "survey_bounds": [0, 0, 20, 10],
                "occupied_boxes": [[1, 1, 3, 3]],
                "prototypes": {"test-machine": {"placement_item": "test-item", "width": 2, "height": 4}},
                "entities": [{"id": "machine_a", "prototype": "test-machine",
                              "position": {"x": 10, "y": 5}, "direction": 4, "recipe": None}],
                "selected_entity_id": "machine_a", "revision": 2,
                "construction_status": "proposed_unbuilt", "native_flow_qualified": False}

    def tool_module(self, **overrides):
        return types.SimpleNamespace(prepare_tool_request=Mock(return_value={"test_only_tools": [], "safe": True}),
                                     **overrides)

    def test_preflight_never_reads_key_or_calls_provider(self):
        with patch("builtins.input", side_effect=AssertionError("unexpected key input")), \
                patch("getpass.getpass", side_effect=AssertionError("unexpected key input")), \
                patch("designer_llm.local_setup.validate_credential",
                      side_effect=AssertionError("unexpected credential use")), \
                patch("designer_llm.local_setup.OpenAIHTTPTransport.__call__",
                      side_effect=AssertionError("unexpected network")):
            checkpoint = self.session.preflight(TEST_MODEL, "2048")
        self.assertEqual(checkpoint.config.max_output_tokens, 2048)
        self.assertGreater(checkpoint.summary["http_payload_bytes"], checkpoint.summary["input_json_bytes"])
        self.assertEqual(checkpoint.summary["maximum_requests"], 1)
        self.assertEqual(checkpoint.summary["automatic_retries"], 0)
        self.assertEqual(checkpoint.summary["model_account_compatibility"],
                         "unverified_until_provider_response")
        self.assertEqual(self.progress()["stage"], "awaiting_local_key")
        self.assertFalse(self.session.attempted)
        self.assertFalse(self.output_path.exists())

    def test_key_shaped_model_invalidates_checkpoint_without_public_echo(self):
        self.session.preflight(TEST_MODEL, 2048)
        mistaken_model = "sk-proj-testcredential1234567890"
        with self.assertRaises(PrivateConfigError):
            self.session.preflight(mistaken_model, 2048)
        self.assertIsNone(self.session.checkpoint)
        self.assertNotIn(mistaken_model, self.public_text())
        self.assertNotIn("model", self.progress())
        self.assertEqual(self.progress()["stage"], "awaiting_model")

    def test_missing_model_bad_budget_and_explicit_invalidation_cannot_send(self):
        calls = []
        for model, budget in (("", 2048), (TEST_MODEL, 255), (TEST_MODEL, 8193),
                              (TEST_MODEL, "not-a-number")):
            with self.assertRaises((PrivateConfigError, ContractError, ValueError)):
                self.session.preflight(model, budget)
            self.assertIsNone(self.session.checkpoint)
        self.session.preflight(TEST_MODEL, 2048)
        self.session.invalidate()
        with self.assertRaises(ContractError):
            self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))
        self.assertEqual(calls, [])
        self.assertFalse(self.session.attempted)

    def test_changed_observation_fails_before_provider_and_consumes_no_retry(self):
        self.session.preflight(TEST_MODEL, 2048)
        context = json.loads(self.context_path.read_text(encoding="utf-8"))
        context["identity"]["session_epoch"] += 1
        self.context_path.write_text(json.dumps(context), encoding="utf-8")
        calls = []
        with patch("designer_llm.local_setup.OpenAIHTTPTransport.__call__",
                   side_effect=AssertionError("unexpected network")):
            result = self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))
        self.assertEqual(result["stage"], "local_error")
        self.assertEqual(calls, [])
        self.assertTrue(self.session.attempted)
        self.assertNotIn(TEST_CREDENTIAL, self.public_text())

    def test_duplicate_execute_creates_exactly_one_provider(self):
        self.session.preflight(TEST_MODEL, 2048)
        calls = []
        with patch("designer_llm.local_setup.propose", return_value=self.rejected_outcome()) as propose, \
                patch("designer_llm.local_setup.export_outcome", return_value=self.output_path) as export:
            result = self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))
            with self.assertRaises(ContractError):
                self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))
        self.assertEqual(result["stage"], "rejected")
        self.assertEqual(len(calls), 1)
        propose.assert_called_once()
        export.assert_called_once()
        self.assertTrue(result["provider"]["mock"])
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(result))
        self.assertNotIn(TEST_CREDENTIAL, self.public_text())
        self.assertNotIn(TEST_CREDENTIAL, repr(self.session.__dict__))
        self.assertFalse(self.output_path.exists())

    def test_export_failure_preserves_recovery_status_without_second_request(self):
        self.session.preflight(TEST_MODEL, 2048)
        calls = []
        accepted = Outcome("accepted_offline", "structural_checks_passed", accepted=object())
        with patch("designer_llm.local_setup.propose", return_value=accepted), \
                patch("designer_llm.local_setup.export_outcome", side_effect=OSError("test-only export failure")), \
                patch("designer_llm.local_setup.Path.is_file", return_value=True):
            result = self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))
        self.assertEqual(result["stage"], "local_artifacts_incomplete")
        progress = self.progress()
        self.assertEqual(progress["result_status"], "accepted_offline")
        self.assertTrue(progress["accepted_package_available"])
        self.assertEqual(progress["output_directory"], str(self.output_path))
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.output_path.exists())
        with self.assertRaises(ContractError):
            self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))

    def test_raw_exception_and_credential_never_enter_status(self):
        self.session.preflight(TEST_MODEL, 2048)
        calls = []
        with patch("designer_llm.local_setup.propose", side_effect=RuntimeError(TEST_CREDENTIAL)):
            result = self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))
        self.assertEqual(result, {"stage": "local_error", "reason": "local_setup_or_request_failed"})
        self.assertEqual(len(calls), 1)
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(result))
        self.assertNotIn(TEST_CREDENTIAL, self.public_text())
        self.assertNotIn(TEST_CREDENTIAL, repr(self.session.__dict__))

    def test_progress_write_failure_returns_safe_result_before_provider(self):
        self.session.preflight(TEST_MODEL, 2048)
        calls = []
        with patch.object(self.session, "write_progress", side_effect=OSError(TEST_CREDENTIAL)):
            result = self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory(calls))
        self.assertEqual(result, {"stage": "local_error", "reason": "local_setup_or_request_failed"})
        self.assertEqual(calls, [])
        self.assertTrue(self.session.attempted)
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(result))
        self.assertNotIn(TEST_CREDENTIAL, self.public_text())

    def test_tool_preflight_reviews_shared_limits_without_key_or_provider(self):
        self.session.set_mode(True)
        module = self.tool_module()
        goal = "Place two furnaces in a row with enough space between them."
        with patch.dict("sys.modules", {"designer_llm.tool_provider": module}), \
                patch("designer_llm.local_setup.prepare_request", side_effect=AssertionError("wrong mode")), \
                patch("designer_llm.local_setup.validate_credential", side_effect=AssertionError("unexpected key")), \
                patch("designer_llm.local_setup.OpenAIHTTPTransport.__call__", side_effect=AssertionError("network")):
            checkpoint = self.session.preflight(TEST_MODEL, 8192, goal)
        self.assertTrue(checkpoint.tool_mode)
        self.assertEqual(checkpoint.request["goal"], goal)
        self.assertEqual(checkpoint.summary["maximum_requests"], 3)
        self.assertEqual(checkpoint.summary["maximum_tool_calls"], 12)
        self.assertEqual(checkpoint.summary["max_output_tokens"], 8192)
        self.assertEqual(checkpoint.summary["output_token_budget_scope"], "aggregate_session")
        self.assertEqual(checkpoint.summary["total_provider_deadline_seconds"], 90)
        self.assertFalse(checkpoint.summary["native_game_placement"])
        module.prepare_tool_request.assert_called_once()
        self.assertEqual(module.prepare_tool_request.call_args.args[1]["request"]["goal"], goal)
        self.assertFalse(self.session.attempted)
        self.assertFalse(self.output_path.exists())

    def test_real_tool_payload_preflights_offline_without_credential(self):
        self.session.set_mode(True)
        with patch("designer_llm.local_setup.validate_credential", side_effect=AssertionError("unexpected key")), \
                patch("designer_llm.local_setup.OpenAIHTTPTransport.__call__", side_effect=AssertionError("network")):
            checkpoint = self.session.preflight(TEST_MODEL, 8192, "Place a useful furnace and drill pair.")
        self.assertTrue(checkpoint.tool_mode)
        self.assertGreater(checkpoint.summary["http_payload_bytes"], checkpoint.summary["input_json_bytes"])
        self.assertFalse(self.session.attempted)

    def test_mode_and_instruction_changes_require_fresh_preflight(self):
        self.session.preflight(TEST_MODEL, 2048)
        self.session.set_mode(True)
        self.assertIsNone(self.session.checkpoint)
        self.assertEqual(self.progress()["maximum_requests"], 3)
        with self.assertRaises(ContractError):
            self.session.execute(TEST_CREDENTIAL, provider_factory=self.factory([]))
        module = self.tool_module()
        with patch.dict("sys.modules", {"designer_llm.tool_provider": module}):
            self.session.preflight(TEST_MODEL, 2048, "Arrange a useful furnace pair.")
            with self.assertRaises(ContractError):
                self.session.preflight(TEST_MODEL, 2048, "")
        self.assertIsNone(self.session.checkpoint)
        self.session.set_mode(False)
        checkpoint = self.session.preflight(TEST_MODEL, 2048)
        self.assertFalse(checkpoint.tool_mode)
        self.assertEqual(checkpoint.summary["maximum_requests"], 1)

    def test_tool_session_provider_limits_callbacks_trace_and_duplicate_block(self):
        calls, updates = [], []
        state = self.grid_state()

        def create_provider(config, transport, supplier, **kwargs):
            self.assertEqual(supplier(), TEST_CREDENTIAL)
            self.assertEqual(config.max_output_tokens, 8192)
            self.assertEqual(kwargs["max_requests"], 3)
            self.assertEqual(kwargs["max_tool_calls"], 12)
            self.assertEqual(kwargs["total_deadline_s"], 90)
            calls.append(config)
            kwargs["on_change"](state)
            return types.SimpleNamespace(trace=[{"tool": "place_entity", "status": "applied"}])

        module = self.tool_module(ToolEditingProvider=create_provider)
        self.session.set_mode(True)
        goal = "Build a proposed furnace pair."
        with patch.dict("sys.modules", {"designer_llm.tool_provider": module}), \
                patch("designer_llm.local_setup.propose", return_value=self.rejected_outcome()) as propose, \
                patch("designer_llm.local_setup.export_outcome", return_value=self.output_path) as export, \
                patch("designer_llm.local_setup.DesignerMemory.add_message") as messages:
            self.session.preflight(TEST_MODEL, 8192, goal)
            result = self.session.execute(TEST_CREDENTIAL, on_change=updates.append)
            with self.assertRaises(ContractError):
                self.session.execute(TEST_CREDENTIAL)
            with self.assertRaises(ContractError):
                self.session.set_mode(False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(updates, [state])
        self.assertEqual(propose.call_args.args[2].trace[0]["tool"], "place_entity")
        self.assertEqual(messages.call_args_list[0].args[1], goal)
        self.assertEqual(result["stage"], "rejected")
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(result))
        self.assertNotIn(TEST_CREDENTIAL, self.public_text())
        self.assertNotIn(TEST_CREDENTIAL, repr(self.session.__dict__))

    def test_tool_callback_exact_credential_echo_is_blocked(self):
        emitted = []

        def create_provider(config, transport, supplier, **kwargs):
            state = self.grid_state()
            state["unexpected_text"] = supplier()
            kwargs["on_change"](state)
            return object()

        module = self.tool_module(ToolEditingProvider=create_provider)
        self.session.set_mode(True)
        with patch.dict("sys.modules", {"designer_llm.tool_provider": module}), \
                patch("designer_llm.local_setup.propose") as propose, \
                patch("designer_llm.local_setup.export_outcome") as export:
            self.session.preflight(TEST_MODEL, 2048)
            result = self.session.execute(TEST_CREDENTIAL, on_change=emitted.append)
        self.assertEqual(result["stage"], "local_error")
        self.assertEqual(emitted, [])
        propose.assert_not_called()
        export.assert_not_called()
        self.assertNotIn(TEST_CREDENTIAL, self.public_text())

    def test_grid_projection_preserves_exact_model_position_and_rotated_footprint(self):
        state = self.grid_state()
        before = copy.deepcopy(state)
        drawing = grid_geometry(state, 500, 300)
        shape = drawing["shapes"][0]
        self.assertEqual(shape["position"], (10, 5))
        self.assertEqual(shape["center"], (250, 150))
        self.assertEqual(shape["box"], (210, 130, 290, 170))
        self.assertEqual(shape["direction"], 4)
        self.assertTrue(shape["selected"])
        self.assertEqual(state, before)
        self.assertEqual(drawing["occupied"], [(70, 70, 110, 110)])

    def test_grid_update_queue_clones_data_and_does_not_end_busy_session(self):
        window = SetupWindow.__new__(SetupWindow)
        window.messages = queue.Queue()
        window.root = types.SimpleNamespace(after=Mock())
        window.draw_grid = Mock()
        window.show_preview = Mock()
        window.status = Mock()
        window.busy = True
        state = self.grid_state()
        window.queue_grid(state)
        state["entities"][0]["position"]["x"] = 99
        window.poll()
        self.assertTrue(window.busy)
        self.assertEqual(window.grid_state["entities"][0]["position"]["x"], 10)
        window.draw_grid.assert_called_once()
        window.show_preview.assert_not_called()
        window.status.configure.assert_not_called()

    def test_rich_inspector_keeps_qualities_modules_settings_and_incident_wires(self):
        state = self.grid_state()
        state.update(configuration_export_scope="rich_declared_configuration",
                     entity_configurations={"machine_a": {
                         "quality": "legendary", "recipe_quality": "normal",
                         "modules": [{"item": "speed-module", "quality": "rare", "count": 2}],
                         "settings": {"kind": "arithmetic", "operation": "*", "second_constant": 2,
                                      "networks": {"red": True, "green": False}}}},
                     circuit_wires=[{"from_id": "machine_a", "from_connector": 1,
                                     "to_id": "machine_b", "to_connector": 3, "color": "red"},
                                    {"from_id": "other_a", "from_connector": 1,
                                     "to_id": "other_b", "to_connector": 2, "color": "green"}])
        state["entities"][0]["recipe"] = "iron-plate"
        before = copy.deepcopy(state)
        capabilities = {"test-machine": {"qualities": ["normal", "legendary"], "module_slots": {"legendary": 2}}}
        text = entity_inspection(state, "machine_a", capabilities)
        self.assertIn("Entity quality: legendary", text)
        self.assertIn("Recipe: iron-plate", text)
        self.assertIn("Recipe quality: normal", text)
        self.assertIn("2 × speed-module (rare)", text)
        self.assertIn('"operation": "*"', text)
        self.assertIn('"green": false', text)
        self.assertIn("red: machine_a:1 → machine_b:3", text)
        self.assertNotIn("other_a", text)
        self.assertIn('"module_slots"', text)
        self.assertEqual(state, before)

    def test_catalog_review_is_exact_and_stale_limits_fail_before_key(self):
        context = json.loads(self.context_path.read_text(encoding="utf-8"))
        identity = context["identity"]
        specification = {"qualities": ["normal", "legendary"], "directions": [0, 4, 8, 12],
                         "upgrade_targets": [], "module_slots": {"normal": 0, "legendary": 0},
                         "allowed_modules": [], "settings_kinds": [], "recipe_qualities": ["normal"]}
        catalog = {"schema": "designer-entity-capabilities-v1", "identity": copy.deepcopy(identity),
                   "source": "synthetic_fixture", "entities": {"stone-furnace": specification},
                   "modules": {}, "signals": {}}
        context["entity_capabilities"] = {"knowledge": "known", "value": catalog,
                                           "observed_tick": identity["observed_tick"], "revision": identity["revision"],
                                           "provenance": {"kind": "fixture", "source_id": "test-only-capabilities"}}
        self.context_path.write_text(json.dumps(context), encoding="utf-8")
        self.session.set_mode(True)
        with patch("designer_llm.local_setup.validate_credential", side_effect=AssertionError("unexpected key")), \
                patch("designer_llm.local_setup.OpenAIHTTPTransport.__call__", side_effect=AssertionError("network")):
            checkpoint = self.session.preflight(TEST_MODEL, 8192)
            self.assertEqual(checkpoint.summary["configuration_export_scope"], "rich_declared_configuration")
            self.assertEqual(checkpoint.summary["configuration_capabilities"], catalog["entities"])
            self.assertEqual(checkpoint.summary["allowed_prototypes"], ["burner-mining-drill", "stone-furnace"])
            context["entity_capabilities"]["revision"] -= 1
            self.context_path.write_text(json.dumps(context), encoding="utf-8")
            with self.assertRaises(ContractError):
                self.session.preflight(TEST_MODEL, 8192)
        self.assertIsNone(self.session.checkpoint)
        self.assertFalse(self.session.attempted)

    def test_public_rich_fixture_is_bounded_identity_bound_state_with_editable_configuration(self):
        path = BASE / "fixtures" / "context-rich.json"
        self.assertLessEqual(path.stat().st_size, 64_000)
        context = read_context(path)
        identity = context["identity"]
        catalog = context["entity_capabilities"]["value"]
        snapshot = context["geometry_snapshot"]
        self.assertEqual(context["evidence_mode"], "fixture")
        self.assertEqual(catalog["source"], "synthetic_fixture")
        self.assertEqual(catalog["identity"], identity)
        self.assertEqual(snapshot["source_hash"], identity["source_hash"])
        self.assertEqual(context["geometry_snapshot_sha256"], snapshot_sha256(snapshot))
        fingerprint = {"schema": "synthetic-rich-prototype-fingerprint-v1",
                       "geometry": snapshot["prototypes"], "capabilities": catalog["entities"],
                       "modules": catalog["modules"], "signals": catalog["signals"],
                       "items": snapshot["items"], "recipes": snapshot["unlocked_recipes"]}
        self.assertEqual(identity["prototype_hash"], hashlib.sha256(canonical_bytes(fingerprint)).hexdigest())
        for spec in catalog["entities"].values():
            self.assertIsNone(spec["module_inventory_index"])
            self.assertEqual(spec["circuit_connectors"], {})
        goal = "Design a small synthetic assembler and legendary arithmetic layout without modules or wires."
        request = make_request(context, goal, max_entities=4, max_stages=2)
        controller = DraftGridController(request, context)
        self.assertEqual(controller.state["entities"], [])
        self.assertEqual(controller.state["configuration_export_scope"], "rich_declared_configuration")
        placed = controller.call("place_entity", {"id": "logic", "prototype": "arithmetic-combinator",
                                                  "x": 10, "y": 20, "direction": 0, "recipe": None})
        self.assertTrue(placed["ok"])
        configured = controller.call("configure_entity", {"id": "logic", "configuration": {
            "quality": "legendary", "recipe_quality": None, "modules": [],
            "settings": {"kind": "arithmetic", "operation": "*",
                         "first": {"signal": {"type": "item", "name": "iron-gear-wheel", "quality": "normal"},
                                   "constant": None, "networks": {"red": True, "green": False}},
                         "second": {"signal": None, "constant": 2, "networks": {"red": True, "green": True}},
                         "output_signal": {"type": "virtual", "name": "signal-A", "quality": None}}}})
        self.assertTrue(configured["ok"])
        self.assertEqual(controller.state["entity_configurations"]["logic"]["quality"], "legendary")
        self.assertFalse(controller.state["native_flow_qualified"])
        self.assertIsNone(controller.submitted_wire)
        payload = prepare_tool_request(ResponsesConfig(model=TEST_MODEL, max_output_tokens=8192,
                                                       max_request_bytes=96_000),
                                       {"request": request, "context": context, "memory": {}})
        self.assertLessEqual(len(canonical_bytes(payload)), 96_000)


if __name__ == "__main__":
    unittest.main()
