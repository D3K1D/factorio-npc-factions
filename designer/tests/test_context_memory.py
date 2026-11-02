"""Offline fixtures only: no provider/network/game calls or credentials."""

import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from designer_llm.context import (
    ContextError, FACT_GROUPS, METRICS, canonical_bytes, context_sha256,
    freshness, validate_context,
)
from designer_llm.memory import DesignerMemory, MemoryError


def observation(value=None, *, known=False, tick=100, revision=2, native=False):
    return {"knowledge": "known" if known else "unknown", "value": value,
            "observed_tick": tick, "revision": revision,
            "provenance": {"kind": "native_snapshot" if native else "fixture",
                           "source_id": "fixture/test-context"}}


def context(*, native=False):
    geometry = {
        "schema": "factorio-local-snapshot-v1", "game_version": "2.0.77",
        "source_hash": "a" * 64, "world_id": "world-one", "force_id": "force-one",
        "observed_tick": 100, "quality": "normal", "researched_technologies": [],
        "unlocked_recipes": ["iron-plate"], "items": ["stone-furnace", "iron-plate"],
        "prototypes": {"stone-furnace": {"placement_item": "stone-furnace", "width": 2, "height": 2}},
        "survey_bounds": [-10, -10, 10, 10], "occupied_boxes": [],
    }
    # The pinned reference schema is intentionally consumed rather than duplicated.
    from designer_llm._reference_contract import SNAPSHOT_SCHEMA
    geometry["schema"] = SNAPSHOT_SCHEMA
    return {"schema": "designer-context-v1", "evidence_mode": "native_export" if native else "fixture",
            "identity": {"world_id": "world-one", "force_id": "force-one", "surface_id": "nauvis",
                         "actor_id": "npc-one", "life_id": "life-one", "session_epoch": 3,
                         "game_version": "2.0.77", "source_hash": "a" * 64,
                         "prototype_hash": "b" * 64, "observed_tick": 100, "revision": 2},
            "geometry_snapshot": geometry,
            "facts": {group: observation(native=native) for group in FACT_GROUPS}}


def event(number=1, *, tick=100, cursor=None, kind="entity_built", session_epoch=3,
          native=False, entity_id="furnace-one"):
    return {"event_id": f"event-{number}", "receipt_id": f"receipt-{number}",
            "world_id": "world-one", "force_id": "force-one", "session_epoch": session_epoch,
            "tick": tick, "cursor": number if cursor is None else cursor, "kind": kind,
            "entity_id": entity_id, "life_id": "life-one",
            "source_id": "fixture/receipt", "evidence_mode": "native_event" if native else "fixture"}


class ContextTests(unittest.TestCase):
    def test_owned_copy_inserted_hash_and_canonical_consistency(self):
        raw = context()
        checked = validate_context(raw)
        self.assertNotIn("geometry_snapshot_sha256", raw)
        self.assertEqual(context_sha256(raw), context_sha256(checked))
        raw["geometry_snapshot"]["occupied_boxes"].append([1, 1, 3, 3])
        self.assertEqual(checked["geometry_snapshot"]["occupied_boxes"], [])
        self.assertNotEqual(context_sha256(raw), context_sha256(checked))

    def test_identity_and_hash_mismatch_rejected(self):
        for modify in (
            lambda c: c["geometry_snapshot"].update(observed_tick=99),
            lambda c: c.update(geometry_snapshot_sha256="d" * 64),
            lambda c: c["identity"].update(world_id="different"),
        ):
            c = context()
            modify(c)
            with self.assertRaises(ContextError):
                validate_context(c)

    def test_twenty_thousand_produced_is_separate_from_stock_and_rate(self):
        values = {"cumulative_produced": 20_000, "stock": 120,
                  "rate_items_per_second": 2.5, "measured_window_ticks": 3600,
                  "utilization": .5, "spare_capacity_items_per_second": None}
        metrics = {key: observation(value, known=value is not None) for key, value in values.items()}
        c = context()
        c["facts"]["item_metrics"] = observation({"iron-plate": metrics}, known=True)
        result = validate_context(c)["facts"]["item_metrics"]["value"]["iron-plate"]
        self.assertEqual(result["cumulative_produced"]["value"], 20_000)
        self.assertEqual(result["stock"]["value"], 120)
        self.assertEqual(result["rate_items_per_second"]["value"], 2.5)
        self.assertIsNone(result["spare_capacity_items_per_second"]["value"])
        self.assertEqual(result["spare_capacity_items_per_second"]["knowledge"], "unknown")

    def test_metrics_require_explicit_all_quantities_and_measured_window(self):
        c = context()
        metrics = {key: observation() for key in METRICS}
        c["facts"]["item_metrics"] = observation({"iron-plate": metrics}, known=True)
        validate_context(c)
        metrics["rate_items_per_second"] = observation(2.5, known=True)
        with self.assertRaises(ContextError):
            validate_context(c)
        metrics["measured_window_ticks"] = observation(60, known=True)
        validate_context(c)
        del metrics["stock"]
        with self.assertRaises(ContextError):
            validate_context(c)

    def test_stale_age_and_unknown_are_preserved(self):
        c = context()
        c["facts"]["inventories"] = observation([], known=True, tick=80, revision=1)
        result = freshness(c)
        self.assertEqual(result["facts/inventories"]["age_ticks"], 20)
        self.assertEqual(result["facts/inventories"]["revision_lag"], 1)
        self.assertEqual(result["facts/inventories"]["freshness"], "stale")
        self.assertEqual(result["facts/built_machines"]["freshness"], "unknown")
        self.assertIsNone(validate_context(c)["facts"]["built_machines"]["value"])

    def test_future_facts_and_unknown_values_rejected(self):
        for bad in (observation([], known=True, tick=101), observation([], known=True, revision=3),
                    observation(0, known=False)):
            c = context()
            c["facts"]["inventories"] = bad
            with self.assertRaises(ContextError):
                validate_context(c)

    def test_nested_provenance_cannot_promote_fixture_to_native(self):
        c = context(native=True)
        c["facts"]["built_machines"] = observation(
            [{"id": "furnace", "status": observation("working", known=True)}], known=True, native=True)
        with self.assertRaises(ContextError):
            validate_context(c)
        c["facts"]["built_machines"]["value"][0]["status"]["provenance"]["kind"] = "native_event"
        validate_context(c)

    def test_json_depth_size_and_finite_numbers_are_bounded(self):
        c = context()
        nested = []
        for _ in range(20):
            nested = [nested]
        c["facts"]["terrain"] = observation(nested, known=True)
        with self.assertRaises(ContextError):
            validate_context(c)
        c = context()
        c["facts"]["terrain"] = observation({"x": float("nan")}, known=True)
        with self.assertRaises(ContextError):
            validate_context(c)
        c["facts"]["terrain"] = observation({"description": "x" * 40_000}, known=True)
        with self.assertRaises(ContextError):
            validate_context(c)
        c["facts"]["terrain"] = observation({"count": 10**500}, known=True)
        with self.assertRaises(ContextError):
            validate_context(c)

    def test_credentials_rejected_without_false_positive_authority_label(self):
        c = context()
        c["facts"]["pending_work"] = observation({"chat_claims_are_authoritative": False}, known=True)
        validate_context(c)
        c["facts"]["pending_work"]["value"]["api_key"] = "unused-fixture-value"
        with self.assertRaisesRegex(ContextError, "Credential-like"):
            validate_context(c)

    def test_rich_shared_fixture_validates_and_hash_matches_request(self):
        root = Path(__file__).resolve().parents[1]
        c = json.loads((root / "fixtures/context.json").read_text(encoding="utf-8"))
        request = json.loads((root / "fixtures/request.json").read_text(encoding="utf-8"))
        self.assertEqual(context_sha256(c), request["context_sha256"])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        test_dir = Path(__file__).resolve().parents[1] / "work" / "test-temp"
        test_dir.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_dir)
        self.path = Path(self.temp.name) / "designer.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def memory(self, **kwargs):
        return DesignerMemory(self.path, "world-one", "force-one", **kwargs)

    def test_sqlite_reload_and_world_force_isolation(self):
        with self.memory() as m:
            m.add_message("user", "Build two furnaces; this is a request", 90)
            m.record_plan("smelter", 1, "c" * 64, "d" * 64)
            m.record_event(event())
        with self.memory() as m:
            result = m.context(context())
            self.assertEqual(len(result["conversation"]["recent_messages"]), 1)
            self.assertEqual(result["plans"]["records"][0]["design_hash"], "c" * 64)
            self.assertEqual(len(result["events"]["records"]), 1)
            self.assertNotIn("current_context", result)
        for world, force in (("other-world", "force-one"), ("world-one", "other-force")):
            c = context()
            c["identity"].update(world_id=world, force_id=force)
            c["geometry_snapshot"].update(world_id=world, force_id=force)
            with DesignerMemory(self.path, world, force) as isolated:
                self.assertEqual(isolated.context(c)["conversation"]["recent_messages"], [])
                self.assertIsNone(isolated.get_plan("smelter"))

    def test_plan_lineage_idempotence_immutable_revision_and_reload(self):
        with self.memory() as m:
            self.assertTrue(m.record_plan("smelter", 1, "c" * 64, "d" * 64))
            self.assertFalse(m.record_plan("smelter", 1, "c" * 64, "d" * 64))
            with self.assertRaises(MemoryError):
                m.record_plan("smelter", 1, "e" * 64, "d" * 64)
            with self.assertRaises(MemoryError):
                m.record_plan("smelter", 3, "e" * 64, "d" * 64, parent_design_hash="c" * 64)
            with self.assertRaises(MemoryError):
                m.record_plan("smelter", 2, "e" * 64, "d" * 64, parent_design_hash="a" * 64)
            m.record_plan("smelter", 2, "e" * 64, "d" * 64, parent_design_hash="c" * 64)
        with self.memory() as m:
            self.assertEqual(m.get_plan("smelter")["revision"], 2)
            self.assertEqual(m.get_plan("smelter", 1)["design_hash"], "c" * 64)
            with self.assertRaises(MemoryError):
                m.record_plan("new-plan", 2, "c" * 64, "d" * 64)

    def test_ordered_same_tick_receipts_duplicate_and_conflicts(self):
        with self.memory() as m:
            self.assertTrue(m.record_event(event(1)))
            self.assertTrue(m.record_event(event(2)))
            self.assertFalse(m.record_event(event(1)))
            conflict = event(3)
            conflict["receipt_id"] = "receipt-1"
            with self.assertRaises(MemoryError):
                m.record_event(conflict)
            with self.assertRaises(MemoryError):
                m.record_event(event(3, cursor=2))
            with self.assertRaises(MemoryError):
                m.record_event(event(3, tick=99))
            self.assertEqual(len(m.context(context())["events"]["records"]), 2)
        with self.memory() as m:
            self.assertTrue(m.record_event(event(3)))

    def test_native_requires_verifier_no_mode_promotion_and_verified_copy(self):
        with self.assertRaises(MemoryError):
            self.memory(evidence_mode="native_export")
        calls = []
        def verify(e):
            calls.append(e["receipt_id"])
            e["entity_id"] = "verifier-mutated-copy"
            return e["receipt_id"] == "receipt-1"
        with self.memory(evidence_mode="native_export", event_verifier=verify) as m:
            with self.assertRaises(MemoryError):
                m.record_event(event())
            self.assertTrue(m.record_event(event(native=True)))
            with self.assertRaises(MemoryError):
                m.record_event(event(2, native=True))
            result = m.context(context(native=True))
            self.assertEqual(result["events"]["records"][0]["entity_id"], "furnace-one")
        with self.assertRaises(MemoryError):
            self.memory(evidence_mode="fixture")
        self.assertEqual(calls, ["receipt-1", "receipt-2"])

    def test_chat_assertions_and_historical_build_do_not_override_current_absence(self):
        c = context()
        c["facts"]["built_machines"] = observation([], known=True)
        original = copy.deepcopy(c)
        with self.memory() as m:
            m.add_message("assistant", "I built 20 furnaces and have 20,000 iron", 99)
            m.record_plan("proposed-furnaces", 1, "c" * 64, "d" * 64)
            m.record_event(event())
            output = m.context(c)
            self.assertFalse(output["conversation"]["assertions_are_world_state"])
            self.assertFalse(output["plans"]["establish_current_existence"])
            self.assertEqual(output["events"]["records"][0]["reconciliation"], "no_longer_observed")
            self.assertEqual(c, original)
            self.assertEqual(output["current_context_sha256"], context_sha256(original))

    def test_destruction_replacement_session_life_and_snapshot_reconciliation(self):
        c = context()
        c["facts"]["built_machines"] = observation([{"entity_id": "furnace-two"}], known=True)
        with self.memory() as m:
            m.record_event(event(1, kind="entity_destroyed"))
            replaced = event(2, kind="entity_replaced")
            replaced["replacement_entity_id"] = "furnace-two"
            m.record_event(replaced)
            m.record_event(event(3, tick=101))
            m.record_event(event(4, tick=10, session_epoch=4))
            different_life = event(5, tick=101)
            different_life["life_id"] = "old-life"
            m.record_event(different_life)
            states = [r["reconciliation"] for r in m.context(c)["events"]["records"]]
            self.assertEqual(states, ["consistent_current_snapshot", "consistent_current_snapshot",
                                      "ahead_of_snapshot", "historical_other_session",
                                      "historical_other_life"])

    def test_stale_machine_observation_does_not_contradict_later_receipt(self):
        c = context()
        c["facts"]["built_machines"] = observation([], known=True, tick=80, revision=1)
        with self.memory() as m:
            m.record_event(event(tick=90))
            output = m.context(c)
            self.assertEqual(output["events"]["records"][0]["reconciliation"],
                             "ahead_of_machine_observation")
            self.assertEqual(output["freshness"]["facts/built_machines"]["age_ticks"], 20)

    def test_history_summary_is_exact_persistent_index_no_invented_fact(self):
        with self.memory() as m:
            for number in range(12):
                m.add_message("user", f"Requested furnace number {number}", 50 + number)
            output = m.context(context(), recent_messages=3)
            summary = output["conversation"]["older_summary"]
            self.assertEqual(summary["message_count"], 9)
            self.assertEqual(summary["role_counts"], {"user": 9})
            self.assertFalse(summary["assertions_are_world_state"])
            self.assertEqual([r["text"] for r in output["conversation"]["recent_messages"]],
                             [f"Requested furnace number {number}" for number in range(9, 12)])
        with closing(sqlite3.connect(self.path)) as db:
            saved = json.loads(db.execute("SELECT payload FROM summaries").fetchone()[0])
            self.assertEqual(saved["originals_sha256"], summary["originals_sha256"])

    def test_export_byte_cap_long_dialogue_trimmed_and_scope_not_duplicated(self):
        with self.memory() as m:
            for number in range(30):
                m.add_message("user", f"Request {number} " + "x" * 1500, number)
            for number in range(1, 20):
                e = event(number)
                e["data"] = {"details": "x" * 500}
                m.record_event(e)
            output = m.context(context(), max_bytes=4_096)
            self.assertLessEqual(len(canonical_bytes(output)), 4_096)
            self.assertNotIn("current_context", output)
            self.assertGreater(output["omitted"]["event_records"], 0)
            self.assertTrue(any(r["omitted_characters"] > 0
                                for r in output["conversation"]["recent_messages"]))
            self.assertEqual(output["current_context_sha256"], context_sha256(context()))

    def test_secret_like_content_rejected_before_sqlite_persistence(self):
        secret = "sk-proj-" + "A" * 40
        with self.memory() as m:
            with self.assertRaises(ContextError):
                m.add_message("user", secret, 100)
            e = event()
            e["data"] = {"authorization": "Bearer " + "B" * 40}
            with self.assertRaises(ContextError):
                m.record_event(e)
            self.assertEqual(m.context(context())["conversation"]["recent_messages"], [])
        self.assertNotIn(secret.encode(), self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
