"""Bounded fixed-text explanations for rejection; never acceptance authority."""

import re

from ._reference_contract import _FLOW

_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_MESSAGES = {
    "prototype_facts_missing": "Supply the required functional prototype facts; unknown facts cannot establish feasibility.",
    "burner_fuel_unplanned": "Plan hand-carried fuel or an inserter from a known fuel source for each burner machine.",
    "electric_consumer_unpowered": "Place consumers within a pole supply area connected to a planned producer or an observed powered pole.",
    "drill_not_on_resource": "Place mining areas over a known patch matching the drill resource categories and extraction witness.",
    "drill_output_unconnected": "Rotate and place each drill so its drop point enters a compatible furnace, container, belt or coal burner.",
    "crafting_input_unplanned": "Plan input for product machines; labs need an input science-pack supply or a feeding inserter.",
    "recipe_category_mismatch": "Choose a recipe category supported by the machine's crafting categories.",
    "fluid_input_unconnected": "Connect boiler water to a shore pump and engine steam to a water-fed boiler's separate steam output.",
    "fluid_mixed": "Keep filtered fluids in separate pipe components; water and steam cannot share a component.",
    "pump_not_on_water": "Place the pump footprint or the tile behind its fluid output on known water.",
    "loader_container_end_missing": "Put a container or machine at one end of each loader, container_distance from its centre: ahead to fill it, behind to empty it.",
    "loader_container_end_ambiguous": "A loader has a container or machine at both ends; keep one at exactly one end.",
    "loader_belt_end_unconnected": "Give each loader a belt at its other end: behind it facing the same way to fill, ahead of it not facing back to empty.",
    "stage_id_invalid": "Stage IDs must start with a letter and contain at most 64 letters, digits, underscores or hyphens.",
    "stage_entities_empty": "Every construction stage must own at least one entity; a separate witness-only stage is unsupported.",
    "planned_bill_empty": "Every stage needs a nonempty placement-item bill for its own entities.",
    "planned_flow_witness_missing": "Every stage needs a planned product, extraction, energy or research witness; a power-only stage may use power_connected on an engine or pole.",
    "witness_outside_stage": "A witness must refer to an entity owned by the same stage.",
    "declared_collision": "The proposed footprints overlap each other or an occupied footprint.",
    "outside_surveyed_bounds": "A proposed footprint extends outside the surveyed bounds.",
    "placement_bill_incomplete": "A stage's bill does not cover all of its declared placement items.",
    "proposal_identity_mismatch": "The proposal does not match the current context identity or snapshot hash.",
    "local_structural_validation_failed": "The proposal failed local structural validation.",
}
_ERROR_CODES = {
    "Invalid stage ID": "stage_id_invalid",
    "Functional stage lacks a planned native flow witness": "planned_flow_witness_missing",
    "Invalid stage witness": "witness_outside_stage",
    "Declared collision": "declared_collision",
    "Entity extends beyond surveyed bounds": "outside_surveyed_bounds",
    "Planned bill omits placement items": "placement_bill_incomplete",
    "Stale or unsupported proposal": "proposal_identity_mismatch",
    **{"Proposal identity mismatch: " + key: "proposal_identity_mismatch"
       for key in ("game_version", "source_hash", "world_id", "force_id", "observed_tick", "quality")},
}


def rejection_validation(wire, failure):
    """Explain known stage errors without changing or repairing model output.

    Only fixed catalog text, validated prototype names in missing-fact codes,
    and bounded array-index paths leave this function. Shared structural and
    functional validators decide acceptance; this report is diagnostic.
    Arbitrary exception text and model-authored values are never returned.
    """
    issues = []

    def add(code, path):
        row = {"code": code, "path": path, "message": _MESSAGES[code]}
        if len(issues) < 16 and row not in issues:
            issues.append(row)

    from .functional_validation import FunctionalValidationError
    if isinstance(failure, FunctionalValidationError):
        for issue in failure.issues:
            code = issue["code"]
            base = "prototype_facts_missing" if code.startswith("prototype_facts_missing:") else code
            row = {"code": code, "path": issue["path"], "message": _MESSAGES[base]}
            if len(issues) < 16:
                issues.append(row)

    stages = wire.get("stages") if type(wire) is dict else None
    if type(stages) is list:
        for index, stage in enumerate(stages[:64]):
            if type(stage) is not dict:
                continue
            path = "stages[" + str(index) + "]"
            stage_id = stage.get("id")
            if type(stage_id) is not str or not _ID.fullmatch(stage_id):
                add("stage_id_invalid", path + ".id")
            entity_ids = stage.get("entity_ids")
            if type(entity_ids) is list and not entity_ids:
                add("stage_entities_empty", path + ".entity_ids")
            bill = stage.get("planned_bill")
            if type(bill) in (dict, list) and not bill:
                add("planned_bill_empty", path + ".planned_bill")
            witnesses = stage.get("witnesses")
            if type(witnesses) is not list:
                continue
            own_ids = {value for value in entity_ids if type(value) is str} if type(entity_ids) is list else set()
            own_flow = False
            outside = False
            for witness in witnesses[:32]:
                if type(witness) is not dict:
                    continue
                entity_id, kind = witness.get("entity_id"), witness.get("kind")
                own = type(entity_id) is str and entity_id in own_ids
                outside |= not own
                own_flow |= own and type(kind) is str and kind in _FLOW
                # The diagnostic lacks the prototype catalog. Acceptance checks
                # power-only ownership and target type in the contract.
                own_flow |= own and kind == "power_connected"
            if not own_flow:
                add("planned_flow_witness_missing", path + ".witnesses")
            if outside:
                add("witness_outside_stage", path + ".witnesses")
    code = _ERROR_CODES.get(str(failure))
    if code and not any(issue["code"] == code for issue in issues):
        add(code, "proposal")
    if not issues:
        add("local_structural_validation_failed", "proposal")
    return {"status": "rejected", "acceptance_authority": "structural_and_functional_contract",
            "issues": issues}


def validation_message(report):
    """UI text comes from catalog codes, never arbitrary report values."""
    issues = report.get("issues") if type(report) is dict else None
    if type(issues) is list:
        for issue in issues[:16]:
            code = issue.get("code") if type(issue) is dict else None
            if type(code) is str and code.startswith("prototype_facts_missing:"):
                return _MESSAGES["prototype_facts_missing"]
            if type(code) is str and code in _MESSAGES:
                return _MESSAGES[code]
    return _MESSAGES["local_structural_validation_failed"]
