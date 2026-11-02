"""Write checked live outputs into a new directory without clobbering old runs."""

import hashlib
import json
from pathlib import Path

from ._reference_contract import ContractError, canonical_bytes
from .preview import render_preview
from .private_config import assert_no_credentials


def write_json(path, value):
    assert_no_credentials(value)
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def export_outcome(outcome, directory):
    target = Path(directory)
    if outcome.accepted:
        doc = outcome.accepted.checked()
        if doc["provider"]["mock"]:
            raise ContractError("Mock results cannot be exported as user deliverables")
        target.mkdir(parents=True, exist_ok=False)
        write_json(target / "accepted-plan.json", doc)
        write_json(target / "request.json", doc["request"])
        write_json(target / "model-proposal.json", doc["provider_proposal"])
        write_json(target / "manifest.json", doc["manifest"])
        if doc["schema"] == "designer-accepted-v2":
            write_json(target / "entity-configurations.json", doc["manifest"]["entity_configurations"])
            write_json(target / "circuit-wires.json", doc["manifest"]["circuit_wires"])
            write_json(target / "configured-item-requirements.json", doc["manifest"]["configured_item_requirements"])
        write_json(target / "validation.json", {
            "status": outcome.status, "reason": outcome.reason,
            "design_sha256": doc["design_sha256"], **doc["validation"],
            "native_flow_qualified": False, "construction_status": "proposed_unbuilt"})
        (target / "blueprint.txt").write_text(doc["blueprint"] + "\n", encoding="ascii")
        raw = doc.get("raw_proposal_text")
        if raw is not None:
            (target / "model-proposal.raw.txt").write_text(raw, encoding="utf-8")
        if "model_tool_trace" in doc:
            write_json(target / "model-tool-actions.json", doc["model_tool_trace"])
        drawing = render_preview(outcome.accepted, target / "preview.png")
        write_json(target / "preview-input.json", drawing)
    else:
        provider = outcome.evidence.get("provider", {})
        if provider.get("mock", True):
            raise ContractError("Mock diagnostics cannot be exported as user deliverables")
        target.mkdir(parents=True, exist_ok=False)
        write_json(target / "rejection.json", {"status": outcome.status,
                                               "reason": outcome.reason, **outcome.evidence})
    index = {"status": outcome.status, "files": {}}
    for path in sorted(target.iterdir()):
        index["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(target / "artifact-index.json", index)
    return target
