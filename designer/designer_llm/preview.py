"""Deterministic geometry rendering of a checked accepted provider package."""

import hashlib
import math
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from ._reference_contract import ContractError, canonical_bytes
from ._coordinate_renderer import render_plan


def preview_document(accepted, *, allow_mock_for_tests=False):
    document = accepted.checked()
    if document["provider"]["mock"] and not allow_mock_for_tests:
        raise ContractError("Mock proposals are test data; no user preview is exported")
    provider = document["provider"]
    model = provider["metadata"].get("model", "explicit configured model")
    source = "TEST ONLY mock output" if provider["mock"] else f"{provider['provider_id']} / {model}"
    # Context comes from the current snapshot, never from conversation assertions.
    existing = []
    omitted_existing = 0
    used_ids = {e["id"] for e in document["manifest"]["entities"]}
    machines = document["context"]["facts"]["built_machines"]
    if machines["knowledge"] == "known" and type(machines["value"]) is list:
        for index, row in enumerate(machines["value"]):
            if type(row) is not dict or row.get("surface_id") != document["context"]["identity"]["surface_id"]:
                continue
            if row.get("prototype") not in document["context"]["geometry_snapshot"]["prototypes"]:
                omitted_existing += 1
                continue
            if "entity_id" in row and "position" in row:
                position, direction = row["position"], row.get("direction", 0)
                if type(position) is dict and position.get("knowledge") == "known":
                    position = position["value"]
                if type(direction) is dict and direction.get("knowledge") == "known":
                    direction = direction["value"]
                if type(position) is not dict or set(position) != {"x", "y"} \
                        or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1_000_000
                               for v in position.values()) \
                        or type(direction) is not int or direction not in (0, 4, 8, 12):
                    omitted_existing += 1
                    continue
                overlay_id = "observed-" + str(index)
                while overlay_id in used_ids:
                    overlay_id = "x-" + overlay_id
                used_ids.add(overlay_id)
                existing.append({"id": overlay_id, "prototype": row["prototype"],
                                 "position": position, "direction": direction})
            else:
                omitted_existing += 1
    if len(existing) > 256:
        omitted_existing += len(existing) - 256
        existing = existing[:256]
    return {
        "metadata": {
            "title": "Proposed layout: " + document["manifest"]["plan_id"],
            "subtitle": "Accepted offline geometry | " + document["design_sha256"][:20],
            "review_status": "Proposed / unbuilt",
            "origin": source[:120],
            "footer": "Model proposal rendered from accepted geometry; native factory flow unqualified",
            "assumptions": ["State source: " + document["context"]["evidence_mode"],
                            "Footprints use the pinned declared prototype catalog.",
                            "Arrows show facing only; they do not prove material flow.",
                            "Stage bills declare placement items; native payment still requires receipts.",
                            "Unrenderable observed overlays omitted: " + str(omitted_existing)],
        },
        "proposal": document["manifest"],
        "snapshot": document["context"]["geometry_snapshot"],
        "existing_entities": existing,
        "entity_configurations": document["manifest"].get("entity_configurations", {}),
        "circuit_wires": document["manifest"].get("circuit_wires", []),
        "binding": {key: document[key] for key in
                    ("design_sha256", "geometry_sha256", "blueprint_sha256", "snapshot_sha256", "context_sha256")},
    }


def render_preview(accepted, output_path, *, allow_mock_for_tests=False):
    drawing = preview_document(accepted, allow_mock_for_tests=allow_mock_for_tests)
    target = Path(output_path)
    if target.exists():
        raise ContractError("Preview output already exists")
    render_plan(drawing, target)
    # Embed full hashes in PNG metadata as well as the canonical drawing input.
    info = PngInfo()
    for key, value in drawing["binding"].items():
        info.add_text(key, value)
    info.add_text("construction_status", "proposed_unbuilt")
    info.add_text("native_flow_qualified", "false")
    info.add_text("mock", str(accepted.checked()["provider"]["mock"]).lower())
    info.add_text("render_input_sha256", hashlib.sha256(canonical_bytes(drawing)).hexdigest())
    with Image.open(target) as picture:
        picture.save(target, format="PNG", pnginfo=info, optimize=False)
    return drawing
