"""Bus modules proven in the sandbox, kept as JSON files.

A module enters the library only after the native sandbox measured its output
(tools/design_module.py). The bus planner asks for the smallest proven module of
a kind that meets its rate, and remembers its choice for the whole bus so every
stage is planned with the same modules.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from companion.modules import Module, Placed, Port

WASTE = 3.0   # a module making more than three times the goal is not a good fit


def default_folder() -> Path:
    base = os.environ.get("NPC_FACTIONS_DATA") or os.path.join(
        os.environ.get("LOCALAPPDATA") or str(Path.home()), "npc-factions")
    return Path(base) / "modules"


def to_json(module: Module) -> dict:
    return {"name": module.name, "width": module.width, "height": module.height, "makes": module.makes,
            "ports": [asdict(p) for p in module.ports],
            "entities": [{k: v for k, v in asdict(e).items() if v is not None} for e in module.entities]}


def from_json(raw: Mapping[str, Any]) -> Module:
    m = Module(str(raw["name"]))
    m.entities = [Placed(**e) for e in raw["entities"]]
    m.ports = [Port(**p) for p in raw["ports"]]
    m.width, m.height = int(raw["width"]), int(raw["height"])
    m.makes = {str(k): float(v) for k, v in (raw.get("makes") or {}).items()}
    return m


class ModuleLibrary:
    def __init__(self, folder: Path | str | None = None):
        self.folder = Path(folder) if folder else default_folder()
        self.entries: dict[str, dict] = {}
        if self.folder.is_dir():
            for path in sorted(self.folder.glob("*.json")):
                try:
                    entry = json.loads(path.read_text(encoding="utf-8"))
                    from_json(entry["module"])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                self.entries[path.stem] = entry

    def save(self, kind: str, module: Module, measured: float, meta: Mapping[str, Any] | None = None) -> str:
        body = to_json(module)
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
        entry = {"schema": "npc-module-v1", "kind": kind, "measured_rate": round(float(measured), 4),
                 "module": body, "meta": dict(meta or {})}
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{key}.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
        self.entries[key] = entry
        return key

    def best(self, kind: str, rate: float) -> tuple[str, Module] | None:
        """The proven module of `kind` making at least `rate`, wasting the least."""
        fits = [(e["measured_rate"], key) for key, e in self.entries.items()
                if e.get("kind") == kind and rate <= e["measured_rate"] <= rate * WASTE]
        if not fits:
            return None
        _, key = min(fits)
        return key, from_json(self.entries[key]["module"])

    def get(self, key: str) -> Module | None:
        entry = self.entries.get(key)
        return from_json(entry["module"]) if entry else None
