"""What engineers learned from their strategist: situations, decisions and how they turned out.

Every strategist decision is recorded with a compact description of the situation it answered
and, later, what came of it (were its intentions carried out?). The library is kept across games,
next to the blueprint library. When an engineer wakes up in a situation like one it (or another
engineer of this install) handled well before, the proven decision can be repeated without
calling the LLM: names in it (the ally who was attacked, the player who spoke) are swapped for the
ones in the new situation. Conversations always go to the LLM: words are not reused.

The LLM is the teacher; this library is what the engineers keep. The more they play, the less
they need to ask.

    library = ExperienceLibrary()
    found = library.recall(request)          # a proven decision, or None
    offered = library.options(request)       # decisions System One may choose to repeat
    library.record(request, intentions, source="llm")
    library.outcome(request_id, tool, "done")
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "npc-experience-v1"
MIN_USES = 3            # tried at least this often...
MIN_SUCCESS = 0.7       # ...and carried out this often, before it is repeated without asking
OPTION_USES = 2        # with System One deciding, decisions are offered sooner...
OPTION_SUCCESS = 0.5    # ...and it weighs their record itself; but one with a record this strong...
RELIABLE_USES = 5
RELIABLE_SUCCESS = 0.85  # ...is repeated without asking System One (each ask is an AI call)
MAX_RECORDS = 4000
ROLES = ("from", "ally", "by", "with")   # names in events that a decision may refer to


def default_path() -> Path:
    base = os.environ.get("NPC_FACTIONS_DATA")
    if base:
        return Path(base) / "experience"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "npc-factions" / "experience"
    return Path.home() / ".local" / "share" / "npc-factions" / "experience"


def _bucket(n: float, edges: Iterable[float]) -> int:
    return sum(1 for e in edges if n >= e)


def signature(request: Mapping[str, Any]) -> str:
    """A coarse description of the situation: why the engineer woke up, what happened, how
    threatened it is, its main trait, whether it has a foe and how armed it is. Similar situations
    share a signature."""
    events = request.get("events") or []
    kinds = sorted({str(e.get("kind")) for e in events if isinstance(e, Mapping)})
    situation = request.get("situation") or {}
    threats = situation.get("threats") or {}
    total = sum(v for v in threats.values() if isinstance(v, (int, float))) if isinstance(threats, Mapping) else 0
    nearest = (situation.get("nearest_threat") or {}).get("distance") if isinstance(situation.get("nearest_threat"), Mapping) else None
    traits = situation.get("traits") or []
    trait = str(traits[0]) if traits else "-"
    # Whether it has someone to take out, and how armed it is: arming up is no habit for one already armed.
    armed = situation.get("armed") if isinstance(situation.get("armed"), Mapping) else {}
    magazines = sum(v for v in (armed.get("magazines"), armed.get("piercing")) if isinstance(v, (int, float)))
    foe = "f1" if situation.get("foe") else "f0"
    return "|".join([str(request.get("reason")), ",".join(kinds) or "-", f"t{_bucket(total, (1, 4, 12))}",
                     f"d{_bucket(-(nearest or 999), (-200, -100, -50))}", trait, foe,
                     f"a{_bucket(magazines, (20, 100))}"])


def roles(request: Mapping[str, Any]) -> dict[str, str]:
    """Who the situation is about, by role: the speaker, the ally under attack, the attacker."""
    out: dict[str, str] = {}
    for e in request.get("events") or []:
        if not isinstance(e, Mapping):
            continue
        for role in ROLES:
            if isinstance(e.get(role), str) and role not in out:
                out[role] = e[role]
    return out


def _swap(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_swap(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _swap(v, mapping) for k, v in value.items()}
    return value


@dataclass
class Record:
    signature: str
    intentions: list[dict]
    source: str = "llm"
    uses: int = 0
    done: int = 0
    failed: int = 0
    created: float = field(default_factory=time.time)

    @property
    def success(self) -> float:
        judged = self.done + self.failed
        return self.done / judged if judged else 0.0


class ExperienceLibrary:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()
        self.path.mkdir(parents=True, exist_ok=True)
        self.file = self.path / "experience.json"
        self.records: list[Record] = []
        self.pending: dict[str, Record] = {}     # request id -> the record its outcome counts for
        if self.file.exists():
            try:
                data = json.loads(self.file.read_text(encoding="utf-8"))
                if data.get("schema") == SCHEMA:
                    self.records = [Record(**r) for r in data.get("records", [])]
            except (ValueError, TypeError):
                self.records = []

    def save(self) -> None:
        self.records = self.records[-MAX_RECORDS:]
        data = {"schema": SCHEMA, "records": [r.__dict__ for r in self.records]}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.file)

    def _template(self, intentions: list[dict], request: Mapping[str, Any]) -> list[dict]:
        names = {name: "{" + role + "}" for role, name in roles(request).items()}
        return [_swap(dict(i), names) for i in intentions]

    def record(self, request: Mapping[str, Any], intentions: list[dict], source: str = "llm") -> Record | None:
        """Remember what was decided here (only decisions that do something: tools other than say)."""
        acting = [i for i in intentions if i.get("tool") not in ("say", "remember")]
        if not acting:
            return None
        sig = signature(request)
        template = self._template(acting, request)
        same = next((r for r in self.records if r.signature == sig and r.intentions == template), None)
        record = same or Record(sig, template, source)
        if not same:
            self.records.append(record)
        record.uses += 1
        if isinstance(request.get("request_id"), str):
            self.pending[request["request_id"]] = record
        self.save()
        return record

    def outcome(self, request_id: str, tool: str, status: str) -> None:
        """What came of an intention: carried out ("done") or not."""
        record = self.pending.get(request_id)
        if record is None or tool in ("say", "remember"):
            return
        if status == "done":
            record.done += 1
        elif status == "failed":
            record.failed += 1
        self.save()

    def candidates(self, request: Mapping[str, Any], limit: int = 4, uses: int = MIN_USES,
                   success: float = MIN_SUCCESS) -> list[Record]:
        """Proven decisions for a situation like this one, best first."""
        sig = signature(request)
        found = [r for r in self.records if r.signature == sig and r.uses >= uses and r.success >= success]
        found.sort(key=lambda r: (-r.success, -r.uses))
        return found[:limit]

    def fill(self, record: Record, request: Mapping[str, Any]) -> list[dict] | None:
        """A record's decision with this situation's names, or None when it needs someone missing here."""
        names = {"{" + role + "}": name for role, name in roles(request).items()}
        filled = _swap(record.intentions, names)
        text = json.dumps(filled)
        if any("{" + role + "}" in text for role in ROLES):
            return None
        return filled

    def options(self, request: Mapping[str, Any], limit: int = 3) -> list[Record]:
        """Decisions that have worked at least sometimes in situations like this: what System One may
        choose to repeat (it weighs how often they were carried out against thinking again)."""
        if any(isinstance(e, Mapping) and e.get("kind") == "chat" for e in request.get("events") or []):
            return []
        sig = signature(request)
        found = [r for r in self.records if r.signature == sig and r.uses >= OPTION_USES
                 and r.success >= OPTION_SUCCESS and self.fill(r, request) is not None]
        found.sort(key=lambda r: (-r.success, -r.uses))
        return found[:limit]

    def recall(self, request: Mapping[str, Any], reliable: bool = False) -> list[dict] | None:
        """The best proven decision for this situation, with names filled in for it; None when there is
        none, when someone is talking to the engineer (that needs words), or when a name it needs is
        missing. reliable: only one with a strong record (repeated without asking System One)."""
        if any(isinstance(e, Mapping) and e.get("kind") == "chat" for e in request.get("events") or []):
            return None
        found = (self.candidates(request, 1, RELIABLE_USES, RELIABLE_SUCCESS) if reliable
                 else self.candidates(request, 1))
        return self.fill(found[0], request) if found else None
