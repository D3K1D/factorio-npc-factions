"""Loaders in a design: which end each one is, read from what stands at its ends, and the checks that a
container or machine stands at exactly one end and a belt carries to or from the other."""

import json
from pathlib import Path
import unittest

from designer_llm._reference_contract import Entity, decode_blueprint
from designer_llm.context import validate_context
from designer_llm.functional_validation import loader_ends
from designer_llm.proposal_schema import to_wire
from designer_llm.providers import ReplayProvider
from designer_llm.request import make_request
from designer_llm.workflow import propose

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CATALOG = {"iron-chest": {"type": "container", "width": 1, "height": 1},
           "transport-belt": {"type": "transport-belt", "width": 1, "height": 1},
           "loader": {"type": "loader", "width": 1, "height": 2, "container_distance": 1.5}}


def entity(eid, prototype, x, y, direction=0):
    return Entity(eid, prototype, x, y, direction, None)


class LoaderEndTests(unittest.TestCase):
    def test_a_chest_behind_is_emptied_onto_the_belt_ahead(self):
        found = {e.id: e for e in (entity("chest", "iron-chest", 0.5, 0.5), entity("loader", "loader", 2.0, 0.5, 4),
                                   entity("belt", "transport-belt", 3.5, 0.5, 8))}   # (turning south: fine)
        self.assertEqual(loader_ends(found, CATALOG), ({"loader": "output"}, []))

    def test_a_chest_ahead_is_filled_from_the_belt_behind_facing_the_same_way(self):
        found = {e.id: e for e in (entity("belt", "transport-belt", 0.5, 3.5, 0), entity("loader", "loader", 0.5, 2.0, 0),
                                   entity("chest", "iron-chest", 0.5, 0.5))}
        self.assertEqual(loader_ends(found, CATALOG), ({"loader": "input"}, []))

    def test_unclear_or_unfed_loaders_are_named(self):
        def issue(*parts):
            return loader_ends({e.id: e for e in parts}, CATALOG)[1]
        loader = entity("loader", "loader", 2.0, 0.5, 4)
        self.assertEqual(issue(loader, entity("belt", "transport-belt", 3.5, 0.5, 4)),
                         [("loader", "loader_container_end_missing")])
        self.assertEqual(issue(loader, entity("a", "iron-chest", 0.5, 0.5), entity("b", "iron-chest", 3.5, 0.5)),
                         [("loader", "loader_container_end_ambiguous")])
        self.assertEqual(issue(loader, entity("chest", "iron-chest", 0.5, 0.5)),
                         [("loader", "loader_belt_end_unconnected")])
        # a belt ahead running back into an output loader carries nothing away
        self.assertEqual(issue(loader, entity("chest", "iron-chest", 0.5, 0.5),
                               entity("belt", "transport-belt", 3.5, 0.5, 12)),
                         [("loader", "loader_belt_end_unconnected")])
        # an input loader takes only from a belt behind it running its way
        self.assertEqual(issue(entity("loader", "loader", 0.5, 2.0, 0), entity("chest", "iron-chest", 0.5, 0.5),
                               entity("belt", "transport-belt", 0.5, 3.5, 4)),
                         [("loader", "loader_belt_end_unconnected")])
        without = {"loader": {k: v for k, v in CATALOG["loader"].items() if k != "container_distance"}}
        self.assertEqual(loader_ends({"loader": loader}, {**CATALOG, **without})[1],
                         [("loader", "prototype_facts_missing:loader")])


class LoaderCapTests(unittest.TestCase):
    def test_a_design_keeps_to_a_few_loaders(self):
        from designer_llm.functional_validation import MAX_DESIGN_LOADERS, loaders_over_cap
        found = {f"l{i}": entity(f"l{i}", "loader", 2.0 * i, 0.5, 4) for i in range(MAX_DESIGN_LOADERS + 2)}
        found["belt"] = entity("belt", "transport-belt", 0.5, 3.5)
        self.assertEqual(loaders_over_cap(found, CATALOG), [f"l{MAX_DESIGN_LOADERS}", f"l{MAX_DESIGN_LOADERS + 1}"])
        self.assertEqual(loaders_over_cap({k: found[k] for k in ("l0", "belt")}, CATALOG), [])


class LoaderProposalTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / "context.json").read_text())
        self.raw = json.loads((FIXTURES / "proposal.json").read_text())
        self.raw["entities"] = self.raw["entities"][:2]
        self.raw["stages"] = self.raw["stages"][:1]
        for name, facts in CATALOG.items():
            self.context["geometry_snapshot"]["items"].append(name)
            self.context["geometry_snapshot"]["prototypes"][name] = {"placement_item": name, "energy_source": "none",
                                                                     **facts}

    def add(self, eid, prototype, x, y, direction=0):
        self.raw["entities"].append({"id": eid, "prototype": prototype, "position": {"x": x, "y": y},
                                     "direction": direction})
        stage = self.raw["stages"][0]
        stage["entity_ids"].append(eid)
        stage["planned_bill"][prototype] = stage["planned_bill"].get(prototype, 0) + 1

    def outcome(self):
        self.context.pop("geometry_snapshot_sha256", None)
        self.context = validate_context(self.context)
        self.raw["snapshot_sha256"] = self.context["geometry_snapshot_sha256"]
        return propose(make_request(self.context, "A chest unloaded onto a belt"), self.context,
                       ReplayProvider(to_wire(self.raw)))

    def test_a_chest_unloaded_onto_a_belt_is_accepted(self):
        self.add("chest", "iron-chest", 30.5, 20.5)
        self.add("unloader", "loader", 32.0, 20.5, 4)
        self.add("belt", "transport-belt", 33.5, 20.5, 4)
        outcome = self.outcome()
        self.assertEqual(outcome.status, "accepted_offline", outcome.evidence)
        # the blueprint string says the loader's end, as Factorio's own blueprints do
        rows = decode_blueprint(outcome.accepted.document["blueprint"])["blueprint"]["entities"]
        self.assertEqual([(r["name"], r.get("type")) for r in rows if r["name"] != "burner-mining-drill"
                          and r["name"] != "stone-furnace"],
                         [("iron-chest", None), ("loader", "output"), ("transport-belt", None)])

    def test_a_loader_with_no_chest_is_rejected_with_its_path(self):
        self.add("unloader", "loader", 32.0, 20.5, 4)
        self.add("belt", "transport-belt", 33.5, 20.5, 4)
        outcome = self.outcome()
        self.assertEqual(outcome.status, "rejected")
        issues = [(i["code"], i["path"]) for i in outcome.evidence["validation"]["issues"]]
        self.assertIn(("loader_container_end_missing", "entities[2]"), issues)


if __name__ == "__main__":
    unittest.main()
