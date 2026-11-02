import copy
import json
import unittest

from designer_llm._reference_contract import (
    ContractError, blueprint_matches_proposal, decode_blueprint, encode_blueprint,
    incremental_frontier, load_json, parse_proposal, parse_snapshot,
)


SOURCE = "a" * 64


def sample_snapshot():
    return {
        "schema": "designer-snapshot-v1", "game_version": "2.0.77",
        "source_hash": SOURCE, "world_id": "world-1", "force_id": "npc-1",
        "observed_tick": 100, "quality": "normal",
        "researched_technologies": [], "unlocked_recipes": ["iron-plate"],
        "items": ["burner-mining-drill", "stone-furnace", "iron-plate"],
        "prototypes": {
            "burner-mining-drill": {"placement_item": "burner-mining-drill", "width": 2, "height": 2},
            "stone-furnace": {"placement_item": "stone-furnace", "width": 2, "height": 2},
        },
        "survey_bounds": [0, 0, 200, 10], "occupied_boxes": [],
    }


def sample_proposal(snapshot, pairs=2):
    entities, stages = [], []
    for index in range(pairs):
        drill, furnace = f"drill-{index}", f"furnace-{index}"
        x = 3 + index * 8
        entities.extend([
            {"id": drill, "prototype": "burner-mining-drill",
             "position": {"x": x, "y": 3}, "direction": 4},
            {"id": furnace, "prototype": "stone-furnace",
             "position": {"x": x + 3, "y": 3}, "direction": 0, "recipe": "iron-plate"},
        ])
        stages.append({
            "id": f"pair-{index}", "depends_on": [f"pair-{index-1}"] if index else [],
            "entity_ids": [drill, furnace],
            "planned_bill": {"burner-mining-drill": 1, "stone-furnace": 1},
            "witnesses": [
                {"kind": "entity_built", "entity_id": drill},
                {"kind": "entity_built", "entity_id": furnace},
                {"kind": "product_counter_delta", "entity_id": furnace,
                 "item": "iron-plate", "min_delta": 1},
            ],
        })
    return {
        "schema": "designer-proposal-v1", "snapshot_sha256": snapshot.sha256,
        "game_version": snapshot.raw["game_version"], "source_hash": SOURCE,
        "world_id": "world-1", "force_id": "npc-1", "observed_tick": 100,
        "quality": "normal", "plan_id": "twenty-pairs", "entities": entities,
        "stages": stages,
    }


class DesignerContractTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = parse_snapshot(sample_snapshot())
        self.raw = sample_proposal(self.snapshot)

    def reject(self, mutated):
        with self.assertRaises(ContractError):
            parse_proposal(mutated, self.snapshot)

    def test_blueprint_round_trip_and_manifest_binding(self):
        proposal = parse_proposal(self.raw, self.snapshot)
        encoded = encode_blueprint(proposal)
        decoded = decode_blueprint(encoded)
        self.assertEqual(decoded["blueprint"]["version"], 2 << 48 | 77 << 16)
        self.assertEqual(len(decoded["blueprint"]["entities"]), 4)
        self.assertTrue(blueprint_matches_proposal(encoded, proposal))
        other = copy.deepcopy(self.raw)
        other["entities"][0]["position"]["y"] = 6
        self.assertFalse(blueprint_matches_proposal(encoded, parse_proposal(other, self.snapshot)))
        with self.assertRaises(ContractError):
            decode_blueprint(encoded[:-3] + "!!!")

    def test_twenty_pair_frontier_never_reserves_future_bill(self):
        proposal = parse_proposal(sample_proposal(self.snapshot, pairs=20), self.snapshot)
        self.assertEqual(incremental_frontier(proposal, set(), set()),
                         ("pair-0", ("drill-0", "furnace-0")))
        self.assertEqual(incremental_frontier(proposal, {"drill-0", "furnace-0"}, set()),
                         ("pair-0", ()))  # Built does not imply productive or verified.
        self.assertEqual(incremental_frontier(proposal, {"drill-0", "furnace-0"}, {"pair-0"}),
                         ("pair-1", ("drill-1", "furnace-1")))
        with self.assertRaises(ContractError):
            incremental_frontier(proposal, set(), {"pair-0"})

    def test_stale_identity_and_locked_recipe(self):
        for key, value in (("observed_tick", 101), ("world_id", "world-2"),
                           ("source_hash", "b" * 64), ("snapshot_sha256", "b" * 64)):
            row = copy.deepcopy(self.raw)
            row[key] = value
            self.reject(row)
        row = copy.deepcopy(self.raw)
        row["entities"][1]["recipe"] = "automation-science-pack"
        self.reject(row)

    def test_cycles_unknown_entities_and_duplicate_ids(self):
        for edit in (lambda r: r["stages"][0].update(depends_on=["pair-1"]),
                     lambda r: r["stages"][1].update(depends_on=["missing"]),
                     lambda r: r["stages"][1].update(entity_ids=["drill-0"]),
                     lambda r: r["entities"][1].update(id="drill-0")):
            row = copy.deepcopy(self.raw)
            edit(row)
            self.reject(row)

    def test_collision_bill_and_native_witness_requirements(self):
        row = copy.deepcopy(self.raw)
        row["entities"][1]["position"]["x"] = 3
        self.reject(row)
        row = copy.deepcopy(self.raw)
        row["stages"][0]["planned_bill"].pop("stone-furnace")
        self.reject(row)
        row = copy.deepcopy(self.raw)
        row["stages"][0]["witnesses"] = [{"kind": "entity_built", "entity_id": "drill-0"}]
        self.reject(row)

    def test_nonfinite_and_duplicate_json_keys_fail_closed(self):
        row = copy.deepcopy(self.raw)
        row["entities"][0]["position"]["x"] = float("nan")
        self.reject(row)
        with self.assertRaises(ContractError):
            load_json('{"schema":"x","schema":"y"}')
        with self.assertRaises(ContractError):
            load_json('{"x":NaN}')

    def test_snapshot_collision_and_unsupported_quality(self):
        snap = sample_snapshot()
        snap["occupied_boxes"] = [[2, 2, 4, 4]]
        occupied = parse_snapshot(snap)
        with self.assertRaises(ContractError):
            parse_proposal(sample_proposal(occupied), occupied)
        snap["quality"] = "legendary"
        with self.assertRaises(ContractError):
            parse_snapshot(snap)

    def test_caller_mutation_cannot_change_accepted_contract(self):
        source_snapshot = sample_snapshot()
        snapshot = parse_snapshot(source_snapshot)
        source_proposal = sample_proposal(snapshot)
        proposal = parse_proposal(source_proposal, snapshot)
        original = encode_blueprint(proposal)
        source_snapshot["game_version"] = "2.1.0"
        source_snapshot["source_hash"] = "b" * 64
        source_snapshot["prototypes"]["stone-furnace"]["width"] = 100
        source_proposal["entities"][0]["position"]["x"] = 190
        self.assertEqual(encode_blueprint(proposal), original)
        self.assertEqual(incremental_frontier(proposal, set(), set())[0], "pair-0")

    def test_parsed_metadata_tampering_is_detected_before_export_or_frontier(self):
        def sealed():
            snap = parse_snapshot(sample_snapshot())
            return snap, parse_proposal(sample_proposal(snap), snap)

        for field, value in (("game_version", "2.1.0"), ("source_hash", "b" * 64)):
            snapshot, proposal = sealed()
            snapshot.raw[field] = value
            with self.assertRaises(ContractError):
                encode_blueprint(proposal)
            with self.assertRaises(ContractError):
                incremental_frontier(proposal, set(), set())
        snapshot, proposal = sealed()
        snapshot.prototypes["stone-furnace"]["width"] = 50
        with self.assertRaises(ContractError):
            encode_blueprint(proposal)
        snapshot, proposal = sealed()
        proposal.raw["entities"][0]["position"]["x"] = 100
        with self.assertRaises(ContractError):
            incremental_frontier(proposal, set(), set())
        snapshot, proposal = sealed()
        proposal.stages[0].planned_bill["stone-furnace"] = 99
        with self.assertRaises(ContractError):
            encode_blueprint(proposal)

    def test_supported_game_versions_are_2_0_77_and_factorio_2_1(self):
        # (Factorio 2.1 came to Steam on 2026-10-23; the geometry checks use the snapshot's own facts)
        for version in ("2.0.77", "2.1.21", "2.1.23"):
            snap = sample_snapshot()
            snap["game_version"] = version
            parse_snapshot(snap)
        for version in ("2.0.76", "2.2.0", "3.0.0", "2.1"):
            snap = sample_snapshot()
            snap["game_version"] = version
            with self.assertRaises(ContractError):
                parse_snapshot(snap)


if __name__ == "__main__":
    unittest.main()
