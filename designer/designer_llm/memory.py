"""Private host-side SQLite history. Chat and accepted plans are not world state."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

from .context import (
    ContextError, canonical_bytes, context_sha256, digest, epoch, freshness,
    identifier, integer, reject_credentials, validate_context,
    source_identifier,
)


class MemoryError(ValueError):
    """History, receipt authority, ordering, or export-size failure."""


class DesignerMemory:
    """World/force-isolated history; native receipts need an injected authenticator.

    The verifier must return exactly True after authenticating the exported native
    event. This component does not supply an engine exporter or verifier. Passing
    a function that merely trusts an event is not native qualification.
    """

    def __init__(self, path: str | Path, world_id: str, force_id: str,
                 evidence_mode: str = "fixture",
                 event_verifier: Callable[[dict], bool] | None = None):
        self.world_id = identifier(world_id, "world ID")
        self.force_id = identifier(force_id, "force ID")
        if evidence_mode == "native":
            evidence_mode = "native_export"
        if evidence_mode not in ("fixture", "native_export"):
            raise MemoryError("Unsupported memory evidence mode")
        if evidence_mode == "native_export" and not callable(event_verifier):
            raise MemoryError("Native memory requires an event verifier")
        self.evidence_mode = evidence_mode
        self._event_verifier = event_verifier
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS scopes (
                world_id TEXT NOT NULL, force_id TEXT NOT NULL, mode TEXT NOT NULL,
                PRIMARY KEY (world_id, force_id));
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, world_id TEXT NOT NULL,
                force_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
                tick INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS plans (
                world_id TEXT NOT NULL, force_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                revision INTEGER NOT NULL, design_hash TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL, status TEXT NOT NULL,
                parent_design_hash TEXT,
                PRIMARY KEY (world_id, force_id, plan_id, revision));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, world_id TEXT NOT NULL,
                force_id TEXT NOT NULL, session_epoch TEXT NOT NULL,
                event_id TEXT NOT NULL, receipt_id TEXT NOT NULL, tick INTEGER NOT NULL,
                cursor INTEGER NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                UNIQUE (world_id, force_id, event_id),
                UNIQUE (world_id, force_id, receipt_id),
                UNIQUE (world_id, force_id, session_epoch, cursor));
            CREATE TABLE IF NOT EXISTS summaries (
                world_id TEXT NOT NULL, force_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (world_id, force_id));
        """)
        if "parent_design_hash" not in {r["name"] for r in self._db.execute("PRAGMA table_info(plans)")}:
            self._db.execute("ALTER TABLE plans ADD COLUMN parent_design_hash TEXT")
        old = self._db.execute(
            "SELECT mode FROM scopes WHERE world_id=? AND force_id=?", self._scope).fetchone()
        if old is not None and old["mode"] != evidence_mode:
            self._db.close()
            raise MemoryError("Cannot mix fixture and native histories in one scope")
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO scopes VALUES (?,?,?)",
                             (*self._scope, evidence_mode))

    @property
    def _scope(self) -> tuple[str, str]:
        return self.world_id, self.force_id

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "DesignerMemory":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def add_message(self, role: str, text: str, tick: int) -> int:
        if role not in ("user", "assistant", "system") or not isinstance(text, str) or not text:
            raise MemoryError("Invalid conversation message")
        integer(tick, "message tick")
        reject_credentials(text)
        if len(text.encode("utf-8")) > 8_000:
            raise MemoryError("Message exceeds byte limit")
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO messages (world_id,force_id,role,text,tick) VALUES (?,?,?,?,?)",
                (*self._scope, role, text, tick))
        return int(cursor.lastrowid)

    def record_plan(self, plan_id: str, revision: int, design_hash: str,
                    snapshot_hash: str, status: str = "accepted",
                    parent_design_hash: str | None = None) -> bool:
        identifier(plan_id, "plan ID")
        integer(revision, "plan revision", 1)
        digest(design_hash, "design hash")
        digest(snapshot_hash, "snapshot hash")
        if status not in ("proposed", "accepted", "rejected", "superseded", "cancelled"):
            raise MemoryError("Invalid plan status")
        if parent_design_hash is not None:
            digest(parent_design_hash, "parent design hash")
        value = (design_hash, snapshot_hash, status, parent_design_hash)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            old = self._db.execute(
                "SELECT design_hash,snapshot_hash,status,parent_design_hash FROM plans "
                "WHERE world_id=? AND force_id=? AND plan_id=? AND revision=?",
                (*self._scope, plan_id, revision)).fetchone()
            if old is not None:
                if tuple(old) != value:
                    raise MemoryError("Conflicting immutable plan revision")
                self._db.commit()
                return False
            latest = self.get_plan(plan_id)
            if latest is None:
                if revision != 1 or parent_design_hash is not None:
                    raise MemoryError("Initial plan revision must be one without a parent")
            else:
                if revision != latest["revision"] + 1:
                    raise MemoryError("Plan revisions must be sequential")
                accepted = self._db.execute(
                    "SELECT design_hash FROM plans WHERE world_id=? AND force_id=? "
                    "AND plan_id=? AND status='accepted' ORDER BY revision DESC LIMIT 1",
                    (*self._scope, plan_id)).fetchone()
                expected_parent = accepted["design_hash"] if accepted else None
                if parent_design_hash != expected_parent:
                    raise MemoryError("Plan parent must match latest accepted design")
            self._db.execute(
                "INSERT INTO plans (world_id,force_id,plan_id,revision,design_hash,"
                "snapshot_hash,status,parent_design_hash) VALUES (?,?,?,?,?,?,?,?)",
                (*self._scope, plan_id, revision, *value))
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return True

    def get_plan(self, plan_id: str, revision: int | None = None) -> dict | None:
        identifier(plan_id, "plan ID")
        query = ("SELECT plan_id,revision,design_hash,snapshot_hash,status,parent_design_hash "
                 "FROM plans WHERE world_id=? AND force_id=? AND plan_id=?")
        parameters: tuple = (*self._scope, plan_id)
        if revision is not None:
            integer(revision, "plan revision", 1)
            query += " AND revision=?"
            parameters += (revision,)
        row = self._db.execute(query + " ORDER BY revision DESC LIMIT 1", parameters).fetchone()
        return dict(row) if row else None

    def record_event(self, event: dict) -> bool:
        reject_credentials(event)
        if not isinstance(event, dict):
            raise MemoryError("Event must be an object")
        owned = copy.deepcopy(event)
        required = {"event_id", "receipt_id", "world_id", "force_id", "session_epoch",
                    "tick", "cursor", "kind", "source_id", "evidence_mode"}
        optional = {"entity_id", "replacement_entity_id", "life_id", "plan_id",
                    "revision", "action_token", "data"}
        if required - owned.keys() or owned.keys() - required - optional:
            raise MemoryError("Missing or unsupported event field")
        for key in ("event_id", "receipt_id", "world_id", "force_id"):
            identifier(owned[key], key)
        source_identifier(owned["source_id"])
        if (owned["world_id"], owned["force_id"]) != self._scope:
            raise MemoryError("Event scope mismatch")
        epoch(owned["session_epoch"])
        for key in ("tick", "cursor"):
            integer(owned[key], key)
        for key in optional - {"revision", "data"}:
            if key in owned:
                identifier(owned[key], key)
        if "revision" in owned:
            integer(owned["revision"], "plan revision")
        if owned["kind"] not in {"entity_built", "entity_destroyed", "entity_replaced",
                                 "construction_executed", "stage_native_confirmed"}:
            raise MemoryError("Unsupported event kind")
        if owned["kind"].startswith("entity_") and "entity_id" not in owned:
            raise MemoryError("Entity event requires a stable entity ID")
        if owned["kind"] == "entity_replaced" and (
                "replacement_entity_id" not in owned or
                owned["replacement_entity_id"] == owned["entity_id"]):
            raise MemoryError("Replacement requires a different stable entity ID")
        if owned["kind"] == "construction_executed" and "action_token" not in owned:
            raise MemoryError("Executed construction requires an action token")
        expected = "fixture" if self.evidence_mode == "fixture" else "native_event"
        if owned["evidence_mode"] != expected:
            raise MemoryError("Event evidence does not match memory mode")
        payload = canonical_bytes(owned)
        if len(payload) > 16_000:
            raise MemoryError("Event exceeds byte limit")
        encoded_epoch = canonical_bytes(owned["session_epoch"]).decode("utf-8")
        payload_hash = hashlib.sha256(payload).hexdigest()
        # A transaction also protects cursor/receipt invariants across host processes.
        try:
            self._db.execute("BEGIN IMMEDIATE")
            duplicates = self._db.execute(
                "SELECT payload_hash FROM events WHERE world_id=? AND force_id=? AND "
                "(event_id=? OR receipt_id=? OR (session_epoch=? AND cursor=?))",
                (*self._scope, owned["event_id"], owned["receipt_id"], encoded_epoch,
                 owned["cursor"])).fetchall()
            if duplicates:
                if any(row["payload_hash"] != payload_hash for row in duplicates):
                    raise MemoryError("Conflicting event, receipt, or cursor")
                self._db.commit()
                return False
            last = self._db.execute(
                "SELECT tick,cursor FROM events WHERE world_id=? AND force_id=? "
                "AND session_epoch=? ORDER BY cursor DESC LIMIT 1",
                (*self._scope, encoded_epoch)).fetchone()
            if last is not None and (owned["cursor"] <= last["cursor"] or
                                     owned["tick"] < last["tick"]):
                raise MemoryError("Stale or out-of-order event cursor")
            if self.evidence_mode == "native_export":
                try:
                    verified = self._event_verifier(copy.deepcopy(owned))
                except Exception as exc:
                    raise MemoryError("Native event verification failed") from None
                if verified is not True:
                    raise MemoryError("Native event verification rejected event")
            self._db.execute(
                "INSERT INTO events (world_id,force_id,session_epoch,event_id,receipt_id,"
                "tick,cursor,payload,payload_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (*self._scope, encoded_epoch, owned["event_id"], owned["receipt_id"],
                 owned["tick"], owned["cursor"], payload.decode("utf-8"), payload_hash))
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return True

    def _summary(self, rows: list[sqlite3.Row]) -> dict:
        roles: dict[str, int] = {}
        for row in rows:
            roles[row["role"]] = roles.get(row["role"], 0) + 1
        originals = [{"id": r["id"], "role": r["role"], "text": r["text"], "tick": r["tick"]}
                     for r in rows]
        summary = {
            "kind": "deterministic_message_index", "assertions_are_world_state": False,
            "message_count": len(rows), "role_counts": roles,
            "first_message_id": rows[0]["id"] if rows else None,
            "last_message_id": rows[-1]["id"] if rows else None,
            "tick_range": [min(r["tick"] for r in rows), max(r["tick"] for r in rows)] if rows else None,
            "originals_sha256": hashlib.sha256(canonical_bytes(originals)).hexdigest(),
            "recent_older_excerpts": [self._message(r, 160) for r in rows[-8:]],
            "excerpt_policy": "verbatim prefix; no inferred or model-authored summary",
        }
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?)",
                             (*self._scope, canonical_bytes(summary).decode("utf-8")))
        return summary

    @staticmethod
    def _message(row: sqlite3.Row | dict, limit: int) -> dict:
        text = row["text"]
        return {"id": row["id"], "role": row["role"], "tick": row["tick"],
                "text": text[:limit], "omitted_characters": max(0, len(text) - limit),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}

    def context(self, current_context: dict, max_bytes: int = 16_000,
                recent_messages: int = 8) -> dict:
        """Return byte-bounded history bound to the exact current observation.

        Historical events never populate built_machines. The caller carries the
        complete validated current context once alongside this memory in a request;
        this export carries its identity/hash and derived freshness, not a duplicate.
        """
        integer(max_bytes, "context byte budget", 1_024, 128_000)
        integer(recent_messages, "recent message count", 0, 64)
        checked = validate_context(current_context)
        if (checked["identity"]["world_id"], checked["identity"]["force_id"]) != self._scope:
            raise MemoryError("Current context scope mismatch")
        if checked["evidence_mode"] != self.evidence_mode:
            raise MemoryError("Current context evidence does not match memory")
        all_messages = self._db.execute(
            "SELECT id,role,text,tick FROM messages WHERE world_id=? AND force_id=? ORDER BY id",
            self._scope).fetchall()
        selected = all_messages[-recent_messages:] if recent_messages else []
        older = all_messages[:-recent_messages] if recent_messages else all_messages
        plans = [dict(r) for r in self._db.execute(
            "SELECT plan_id,revision,design_hash,snapshot_hash,status,parent_design_hash FROM plans "
            "WHERE world_id=? AND force_id=? ORDER BY rowid DESC LIMIT 32", self._scope)]
        plan_count = self._db.execute(
            "SELECT COUNT(*) FROM plans WHERE world_id=? AND force_id=?", self._scope).fetchone()[0]
        events = [json.loads(r["payload"]) for r in self._db.execute(
            "SELECT payload FROM events WHERE world_id=? AND force_id=? ORDER BY id DESC LIMIT 32",
            self._scope)]
        event_count = self._db.execute(
            "SELECT COUNT(*) FROM events WHERE world_id=? AND force_id=?", self._scope).fetchone()[0]
        summary = self._summary(older)
        exported_events = [self._reconcile(e, checked) for e in reversed(events)]
        output = {
            "schema": "designer-memory-export-v1", "evidence_mode": self.evidence_mode,
            "authority": "current_context at its observation tick; conversation and plans are history",
            "current_context_identity": copy.deepcopy(checked["identity"]),
            "current_context_sha256": context_sha256(checked),
            "geometry_snapshot_sha256": checked["geometry_snapshot_sha256"],
            "freshness": freshness(checked),
            "conversation": {"assertions_are_world_state": False,
                             "recent_messages": [self._message(r, 1_200) for r in selected],
                             "older_summary": summary},
            "plans": {"establish_current_existence": False, "records": list(reversed(plans))},
            "events": {"establish_current_existence_without_reconciliation": False,
                       "records": exported_events},
            "omitted": {"messages": len(all_messages) - len(selected),
                        "plan_records": plan_count - len(plans),
                        "event_records": event_count - len(events),
                        "freshness_details": 0},
        }
        # Drop optional historical detail before any recent dialogue or current facts.
        while len(canonical_bytes(output)) > max_bytes:
            if output["events"]["records"]:
                output["events"]["records"].pop(0)
                output["omitted"]["event_records"] += 1
            elif output["plans"]["records"]:
                output["plans"]["records"].pop(0)
                output["omitted"]["plan_records"] += 1
            elif summary["recent_older_excerpts"]:
                summary["recent_older_excerpts"].pop(0)
            elif output["conversation"]["recent_messages"]:
                recent = output["conversation"]["recent_messages"]
                longest = max(recent, key=lambda r: len(r["text"]))
                if len(longest["text"]) > 160:
                    removed = len(longest["text"]) - 160
                    longest["text"] = longest["text"][:160]
                    longest["omitted_characters"] += removed
                else:
                    recent.pop(0)
                    output["omitted"]["messages"] += 1
            elif len(output["freshness"]) > 9:
                detail = next((p for p in reversed(output["freshness"])
                               if p.count("/") > 1), None)
                if detail is None:
                    raise MemoryError("Memory metadata exceeds budget; increase the budget")
                del output["freshness"][detail]
                output["omitted"]["freshness_details"] += 1
            else:
                raise MemoryError("Memory metadata exceeds budget; increase the budget")
        reject_credentials(output)
        return copy.deepcopy(output)

    @staticmethod
    def _reconcile(event: dict, context: dict) -> dict:
        output = copy.deepcopy(event)
        if "data" in output:
            output["data_sha256"] = hashlib.sha256(canonical_bytes(output.pop("data"))).hexdigest()
            output["data_omitted"] = True
        identity = context["identity"]
        if type(event["session_epoch"]) is not type(identity["session_epoch"]) or (
                event["session_epoch"] != identity["session_epoch"]):
            status = "historical_other_session"
        elif "life_id" in event and event["life_id"] != identity["life_id"]:
            status = "historical_other_life"
        elif event["tick"] > identity["observed_tick"]:
            status = "ahead_of_snapshot"
        else:
            machines = context["facts"]["built_machines"]
            value = machines["value"]
            if isinstance(value, dict):
                value = value.get("machines", value.get("entities"))
            if machines["knowledge"] != "known" or not isinstance(value, list):
                status = "not_reconciled_unknown_current_machines"
            elif event["tick"] > machines["observed_tick"]:
                status = "ahead_of_machine_observation"
            else:
                ids = {m.get("entity_id", m.get("id")) for m in value if isinstance(m, dict)}
                entity = event.get("entity_id")
                if event["kind"] == "entity_destroyed":
                    status = "contradicts_current_snapshot" if entity in ids else "consistent_current_snapshot"
                elif event["kind"] == "entity_built":
                    status = "consistent_current_snapshot" if entity in ids else "no_longer_observed"
                elif event["kind"] == "entity_replaced":
                    status = "consistent_current_snapshot" if (
                        entity not in ids and event["replacement_entity_id"] in ids) else "contradicts_current_snapshot"
                else:
                    status = "historical_execution_receipt"
        output["reconciliation"] = status
        output["does_not_override_current_snapshot"] = True
        return output
