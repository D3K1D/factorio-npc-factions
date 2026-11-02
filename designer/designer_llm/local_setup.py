"""Visible local model/budget review and masked, memory-only key entry."""

from __future__ import annotations

import json
import math
import queue
from dataclasses import dataclass, field
from pathlib import Path
import threading
import uuid

from .__main__ import read_context
from ._reference_contract import ContractError, canonical_bytes
from .artifacts import export_outcome
from .diagnostics import validation_message
from .entity_configuration import validate_capability_catalog
from .memory import DesignerMemory
from .private_config import ResponsesConfig, assert_no_credentials, validate_credential
from .proposal_schema import proposal_schema
from .providers import OpenAIHTTPTransport, ResponsesProvider, prepare_request
from .request import make_request, validate_request
from .workflow import propose


BASE = Path(__file__).resolve().parents[1]
PUBLISHED = BASE.parents[1] / "outputs" / "designer-llm"
HOST = BASE / ".host"
DEFAULT_GOAL = ("Propose a small iron expansion, beginning with one useful funded pair, "
                "in this declared synthetic factory state. Use only the pinned supported "
                "geometry and aim for useful staged output. No native flow is qualified yet.")


def catalog_review(context, tool_mode):
    """Expose precisely the supplied draft catalog before any credential use."""
    result = {"configuration_export_scope": "legacy_normal_geometry_only",
              "allowed_prototypes": sorted(context["geometry_snapshot"]["prototypes"]),
              "unlocked_recipes": sorted(context["geometry_snapshot"]["unlocked_recipes"]),
              "configuration_capabilities": {}, "module_capabilities": {}}
    fact = context.get("entity_capabilities")
    if tool_mode and fact is not None and fact["knowledge"] == "known":
        identity = context["identity"]
        if fact["observed_tick"] != identity["observed_tick"] or fact["revision"] != identity["revision"]:
            raise ContractError("Capability observations are stale")
        catalog = validate_capability_catalog(fact["value"], identity)
        result.update(configuration_export_scope="rich_declared_configuration",
                      configuration_capabilities=catalog["entities"], module_capabilities=catalog["modules"])
    return result


@dataclass(frozen=True)
class Checkpoint:
    config: ResponsesConfig
    context: dict = field(repr=False)
    request: dict = field(repr=False)
    summary: dict
    tool_mode: bool = False


class LocalSession:
    """One preflight followed by one user-triggered, bounded session.

    No credential is retained on this object or written in progress/artifacts.
    A new model/budget selection requires another preflight before key entry.
    """

    def __init__(self, *, context_path=None, memory_path=None, output_path=None, progress_path=None,
                 tools=False):
        if type(tools) is not bool:
            raise ContractError("Invalid local session mode")
        self.tool_mode = tools
        self.context_path = Path(context_path or BASE / "fixtures" / "context.json")
        self.memory_path = Path(memory_path or HOST / "designer-memory.sqlite3")
        self.output_path = Path(output_path or PUBLISHED / ("live-" + uuid.uuid4().hex[:12]))
        self.progress_path = Path(progress_path or HOST / "setup-progress.json")
        self.checkpoint = None
        self.attempted = False
        self._lock = threading.Lock()
        self.write_progress("awaiting_model", maximum_requests=3 if self.tool_mode else 1,
                            automatic_retries=0, operation_mode=self.mode_name)

    @property
    def mode_name(self):
        return "draft_grid_tools" if self.tool_mode else "single_proposal"

    def set_mode(self, tools):
        if type(tools) is not bool:
            raise ContractError("Invalid local session mode")
        with self._lock:
            if self.attempted:
                raise ContractError("Session mode cannot change after sending")
            self.tool_mode = tools
        self.invalidate()

    def write_progress(self, stage, **safe_fields):
        record = {"schema": "designer-local-setup-progress-v1", "stage": stage,
                  "output_directory": str(self.output_path), **safe_fields}
        assert_no_credentials(record)
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.progress_path.with_suffix(".new")
        temporary.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(self.progress_path)

    def invalidate(self):
        with self._lock:
            if self.attempted:
                return
            self.checkpoint = None
        self.write_progress("awaiting_model", maximum_requests=3 if self.tool_mode else 1,
                            automatic_retries=0, operation_mode=self.mode_name)

    def preflight(self, model, output_tokens, goal=None):
        self.invalidate()
        assert_no_credentials(model)
        config = ResponsesConfig(model=model.strip(), max_output_tokens=int(output_tokens),
                                 max_retries=0, max_request_bytes=96_000)
        if not 256 <= config.max_output_tokens <= 8192:
            raise ContractError("Local output budget must be 256..8192")
        if config.model.upper().startswith(("CHOOSE_", "SET_EXPLICIT", "YOUR_MODEL", "REPLACE_")):
            raise ContractError("Explicit actual model required")
        with self._lock:
            if self.attempted:
                raise ContractError("This local session has already attempted one session")
        context = read_context(self.context_path)
        if context["evidence_mode"] != "fixture":
            raise ContractError("First checkpoint requires declared synthetic state")
        request = make_request(context, DEFAULT_GOAL if goal is None else goal,
                               request_id="request-" + uuid.uuid4().hex[:12],
                               max_entities=4, max_stages=2)
        if self.output_path.exists():
            raise ContractError("Output directory must be fresh")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with DesignerMemory(self.memory_path, context["identity"]["world_id"],
                            context["identity"]["force_id"], evidence_mode="fixture") as memory:
            history = memory.context(context, max_bytes=16_000)
        envelope = {"request": request, "context": context, "memory": history}
        input_bytes = len(canonical_bytes(envelope))
        if input_bytes > 64_000:
            raise ContractError("Input context exceeds local checkpoint budget")
        if self.tool_mode:
            from .tool_provider import prepare_tool_request
            payload = prepare_tool_request(config, envelope)
        else:
            payload = prepare_request(config, envelope, proposal_schema())
        summary = {
            "model": config.model, "max_output_tokens": config.max_output_tokens,
            "max_input_bytes": 64_000, "input_json_bytes": input_bytes,
            "http_payload_bytes": len(canonical_bytes(payload)),
            "maximum_requests": 3 if self.tool_mode else 1,
            "automatic_retries": 0, "transport_timeout_seconds": config.timeout_s,
            "total_provider_deadline_seconds": 90 if self.tool_mode else config.total_deadline_s,
            "max_entities": 4, "max_stages": 2, "state_source": "synthetic_fixture",
            "snapshot_sha256": request["snapshot_sha256"], "context_sha256": request["context_sha256"],
            "model_account_compatibility": "unverified_until_provider_response",
            "operation_mode": self.mode_name,
            **catalog_review(context, self.tool_mode),
        }
        if self.tool_mode:
            summary.update(maximum_tool_calls=12, output_token_budget_scope="aggregate_session",
                           construction_status="proposed_unbuilt", native_game_placement=False)
        checkpoint = Checkpoint(config, context, request, summary, self.tool_mode)
        with self._lock:
            self.checkpoint = checkpoint
        self.write_progress("awaiting_local_key", **summary)
        return checkpoint

    def execute(self, credential, *, provider_factory=None, on_change=None):
        # Check the credential privately; do not echo it or retain it on self.
        credential = validate_credential(credential)
        with self._lock:
            if self.attempted or self.checkpoint is None:
                raise ContractError("Fresh preflight required; duplicate send rejected")
            checkpoint = self.checkpoint
            self.attempted = True
        try:
            self.write_progress("request_running", **checkpoint.summary)
            current = read_context(self.context_path)
            validate_request(checkpoint.request, current)
            with DesignerMemory(self.memory_path, current["identity"]["world_id"],
                                current["identity"]["force_id"], evidence_mode="fixture") as memory:
                def safe_change(state):
                    assert_no_credentials(state)
                    encoded = canonical_bytes(state)
                    if len(encoded) > 200_000 or credential.encode("utf-8") in encoded:
                        raise ContractError("Invalid public grid update")
                    if on_change is not None:
                        on_change(state)

                if provider_factory is not None:
                    factory = provider_factory
                elif checkpoint.tool_mode:
                    from .tool_provider import ToolEditingProvider
                    factory = lambda config, supplier: ToolEditingProvider(
                        config, OpenAIHTTPTransport(), supplier, on_change=safe_change,
                        max_requests=3, max_tool_calls=12, total_deadline_s=90)
                else:
                    factory = lambda config, supplier: ResponsesProvider(config, OpenAIHTTPTransport(), supplier)
                provider = factory(checkpoint.config, lambda: credential)
                outcome = propose(checkpoint.request, checkpoint.context, provider,
                                  latest_context=lambda: read_context(self.context_path), memory=memory,
                                  max_input_bytes=64_000)
                outcome.evidence["request"] = checkpoint.request
                outcome.evidence["context_sha256"] = checkpoint.request["context_sha256"]
                try:
                    directory = export_outcome(outcome, self.output_path)
                except Exception:
                    # A paid accepted package may already be on disk. Preserve
                    # it, report the local artifact failure, never call again.
                    self.write_progress("local_artifacts_incomplete", **checkpoint.summary,
                                        result_status=outcome.status, reason="local_export_failed",
                                        accepted_package_available=(self.output_path / "accepted-plan.json").is_file())
                    return {"stage": "local_artifacts_incomplete", "reason": "local_export_failed"}
                memory.add_message("user", checkpoint.request["goal"], current["identity"]["observed_tick"])
                memory.add_message("assistant", "Designer outcome: " + outcome.status + "; " + outcome.reason,
                                   current["identity"]["observed_tick"])
            record = {"stage": outcome.status, "reason": outcome.reason,
                      "output_directory": str(directory), "native_flow_qualified": False}
            if outcome.accepted:
                doc = outcome.accepted.checked()
                record.update(design_sha256=doc["design_sha256"], geometry_sha256=doc["geometry_sha256"],
                              provider=doc["provider"])
            else:
                record["provider"] = outcome.evidence.get("provider", {})
                if outcome.reason == "local_validation_failed":
                    record["validation"] = outcome.evidence.get("validation", {})
            self.write_progress(record["stage"], **checkpoint.summary,
                                **{k: v for k, v in record.items() if k not in ("stage", "output_directory")})
            return record
        except Exception:
            try:
                self.write_progress("local_error", **checkpoint.summary, reason="local_setup_or_request_failed")
            except Exception:
                pass
            return {"stage": "local_error", "reason": "local_setup_or_request_failed"}
        finally:
            credential = ""  # Never persist it; Python does not promise memory zeroization.


def grid_geometry(state, width=1000, height=560):
    """Map controller coordinates to canvas coordinates without inventing entities.

    This returns drawing geometry only. It never accepts a plan, writes an image,
    or changes the controller's grid.
    """
    assert_no_credentials(state)
    if type(state) is not dict or state.get("schema") != "designer-draft-grid-v1":
        raise ContractError("Invalid draft grid state")
    bounds = state.get("survey_bounds")
    if type(bounds) is not list or len(bounds) != 4 \
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in bounds):
        raise ContractError("Invalid draft grid bounds")
    x1, y1, x2, y2 = bounds
    if not x1 < x2 or not y1 < y2:
        raise ContractError("Invalid draft grid bounds")
    scale = min(max(1, width - 100) / (x2 - x1), max(1, height - 100) / (y2 - y1))
    left = (width - (x2 - x1) * scale) / 2
    top = (height - (y2 - y1) * scale) / 2

    def point(x, y):
        return (left + (x - x1) * scale, top + (y - y1) * scale)

    shapes = []
    catalog = state["prototypes"]
    for entity in state["entities"]:
        spec = catalog[entity["prototype"]]
        span_x, span_y = spec["width"], spec["height"]
        if entity["direction"] in (4, 12):
            span_x, span_y = span_y, span_x
        x, y = entity["position"]["x"], entity["position"]["y"]
        shapes.append({"id": entity["id"], "prototype": entity["prototype"],
                       "position": (x, y), "direction": entity["direction"], "recipe": entity["recipe"],
                       "box": (*point(x - span_x / 2, y - span_y / 2),
                               *point(x + span_x / 2, y + span_y / 2)),
                       "center": point(x, y),
                       "selected": entity["id"] == state["selected_entity_id"]})
    occupied = [(*point(box[0], box[1]), *point(box[2], box[3])) for box in state["occupied_boxes"]]
    return {"scale": scale, "bounds": bounds, "screen_bounds": (*point(x1, y1), *point(x2, y2)),
            "shapes": shapes, "occupied": occupied}


def entity_inspection(state, entity_id=None, capabilities=None):
    """Read-only entity/configuration details from the actual current draft."""
    assert_no_credentials(state)
    entity_id = entity_id or state.get("selected_entity_id")
    entity = next((row for row in state.get("entities", []) if row["id"] == entity_id), None)
    if entity is None:
        return "Select a proposed entity to inspect its current configuration."
    configuration = state.get("entity_configurations", {}).get(entity_id, {})
    position = entity["position"]
    lines = [f"Entity: {entity_id}", f"Prototype: {entity['prototype']}",
             f"Position: ({position['x']:g}, {position['y']:g})", f"Direction: {entity['direction']}",
             "Entity quality: " + str(configuration.get("quality", "not supplied")),
             "Recipe: " + str(entity.get("recipe") or "none"),
             "Recipe quality: " + str(configuration.get("recipe_quality") or "not specified"), "", "Modules:"]
    modules = configuration.get("modules", [])
    lines.extend([f"{row['count']} × {row['item']} ({row['quality']})" for row in modules] or ["None"])
    lines.extend(["", "Control settings:"])
    settings = configuration.get("settings")
    lines.append("None" if settings is None else json.dumps(settings, indent=2, ensure_ascii=False, allow_nan=False))
    wires = [row for row in state.get("circuit_wires", [])
             if entity_id in (row["from_id"], row["to_id"])]
    lines.extend(["", "Declared wires:"])
    lines.extend([f"{row['color']}: {row['from_id']}:{row['from_connector']} → "
                  f"{row['to_id']}:{row['to_connector']}" for row in wires] or ["None"])
    allowed = (capabilities or {}).get(entity["prototype"])
    lines.extend(["", "Export scope: " + state.get("configuration_export_scope", "legacy_normal_geometry_only")])
    if allowed is not None:
        lines.extend(["", "Supplied capability limits:", json.dumps(allowed, indent=2, ensure_ascii=False, allow_nan=False)])
    else:
        lines.append("No richer capability limits supplied for this prototype.")
    lines.extend(["", "Proposed / unbuilt. Native operation remains unqualified."])
    return "\n".join(lines)


class SetupWindow:
    def __init__(self, root, session, *, initial_model="", initial_budget=2048, updated=False,
                 initial_goal=DEFAULT_GOAL):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.session = root, session
        self.busy = False
        self.messages = queue.Queue()
        self.grid_window = None
        self.grid_canvas = None
        self.grid_state = None
        self.inspected_entity_id = None
        self.model = tk.StringVar(value=initial_model)
        self.budget = tk.StringVar(value=str(initial_budget))
        self.tools = tk.BooleanVar(value=session.tool_mode)
        root.title("Factorio Designer - LLM Layout Editor" if session.tool_mode else
                   "Factorio Designer - Updated Setup" if updated else "Factorio Designer - Private Local Setup")
        root.geometry("820x860")
        root.minsize(760, 800)
        root.report_callback_exception = self.safe_callback_error
        root.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill="both", expand=True)
        self.heading = ttk.Label(outer, text="LLM-controlled layout editor" if session.tool_mode else
                                 "One paid blueprint request", font=("Segoe UI", 19, "bold"))
        self.heading.pack(anchor="w")
        ttk.Label(outer, text="Choose the OpenAI model and review limits first. Then enter your key locally.",
                  wraplength=710).pack(anchor="w", pady=(8, 18))
        form = ttk.Frame(outer)
        form.pack(fill="x")
        ttk.Label(form, text="Exact OpenAI API model ID").grid(row=0, column=0, sticky="w", pady=6)
        self.model_entry = ttk.Entry(form, textvariable=self.model, width=44)
        self.model_entry.grid(row=0, column=1, sticky="ew", padx=(18, 0), pady=6)
        ttk.Label(form, text="Maximum output tokens").grid(row=1, column=0, sticky="w", pady=6)
        self.budget_entry = ttk.Spinbox(form, textvariable=self.budget, from_=256, to=8192, increment=256, width=12)
        self.budget_entry.grid(row=1, column=1, sticky="w", padx=(18, 0), pady=6)
        form.columnconfigure(1, weight=1)
        self.mode_button = ttk.Checkbutton(outer, text="Use tools to edit the external draft grid",
                                           variable=self.tools, command=self.mode_changed)
        self.mode_button.pack(anchor="w", pady=(8, 2))
        self.mode_note = ttk.Label(outer, text=self.mode_description(), wraplength=710)
        self.mode_note.pack(anchor="w", pady=(2, 6))
        ttk.Label(outer, text="Layout instructions").pack(anchor="w", pady=(4, 4))
        self.goal_entry = tk.Text(outer, height=2, width=78, wrap="word")
        self.goal_entry.pack(fill="x", pady=(0, 6))
        self.goal_entry.insert("1.0", initial_goal)
        self.goal_entry.edit_modified(False)
        self.goal_entry.bind("<<Modified>>", self.goal_changed)
        ttk.Label(outer, text=("Your previous model and token choices are filled in; review them before sending. "
                              if updated else "No model is selected automatically. ") +
                  "API access and structured-output support are checked by the provider, not by local preflight.",
                  wraplength=710).pack(anchor="w", pady=(6, 10))
        self.preflight_button = ttk.Button(outer, text="1. Review limits and run local preflight", command=self.preflight)
        self.preflight_button.pack(anchor="w", pady=(0, 12))
        review_body = ttk.Frame(outer)
        review_body.pack(fill="x", pady=(0, 12))
        self.review = tk.Text(review_body, height=8, width=78, wrap="word", state="disabled", bg="#f5f7f7", relief="flat")
        self.review.pack(side="left", fill="x", expand=True)
        review_scroll = ttk.Scrollbar(review_body, orient="vertical", command=self.review.yview)
        review_scroll.pack(side="right", fill="y")
        self.review.configure(yscrollcommand=review_scroll.set)
        self.set_review(self.initial_review())
        ttk.Label(outer, text="API key (masked; memory only; never saved)").pack(anchor="w", pady=(4, 5))
        self.key_entry = ttk.Entry(outer, show="\u2022", state="disabled", width=70)
        self.key_entry.pack(fill="x")
        self.send_button = ttk.Button(outer, text=self.send_label(), command=self.send, state="disabled")
        self.send_button.pack(anchor="w", pady=(12, 10))
        self.status = ttk.Label(outer, text=("Tool editing is ready. Review preflight before entering your key. No new request has been sent."
                                           if session.tool_mode else
                                           "The previous candidate was rejected. Updated stage rules are ready. "
                                           "Review preflight, then enter your key for one new paid request. No new request has been sent."
                                           if updated else "Enter a model ID, then run preflight. No key has been read and no request has been sent."),
                                wraplength=710)
        self.status.pack(anchor="w")
        self.model.trace_add("write", self.changed)
        self.budget.trace_add("write", self.changed)
        self.model_entry.focus_set()
        root.after(150, self.poll)

    def send_label(self):
        return ("2. Start tool session (up to 3 paid requests)" if self.session.tool_mode else
                "2. Send one paid request")

    def mode_description(self):
        if self.session.tool_mode:
            return ("The LLM places, moves and selects proposed entities in an external layout editor. "
                    "This edits a draft only; it does not place entities in the native game.")
        return "One complete blueprint proposal; proposed / unbuilt until native construction is integrated."

    def initial_review(self):
        if self.session.tool_mode:
            return ("Up to 3 paid API requests for tool feedback; zero automatic retries.\n"
                    "Up to 12 tool calls; selected output-token limit is shared across the session.\n"
                    "External draft grid in synthetic factory state; proposed / unbuilt; normal quality only.\n"
                    "Up to four entities and two stages. 64,000 input bytes.\n"
                    "30-second transport / 90-second total session deadline.\n"
                    "The token limit is not a dollar spending cap. Review model pricing before starting.")
        return ("One request; zero automatic retries.\nSynthetic factory state; actual model blueprint if accepted.\n"
                "Up to four entities and two stages. 64,000 input bytes.\n"
                "30-second transport / 45-second total provider deadline.\n"
                "The token limit is not a dollar spending cap. Review your model's pricing before sending.")

    def mode_changed(self):
        if self.busy or self.session.attempted:
            return
        self.session.set_mode(bool(self.tools.get()))
        self.heading.configure(text="LLM-controlled layout editor" if self.session.tool_mode else
                               "One paid blueprint request")
        self.mode_note.configure(text=self.mode_description())
        self.send_button.configure(text=self.send_label())
        self.set_review(self.initial_review())
        self.changed()

    def set_review(self, text):
        self.review.configure(state="normal")
        self.review.delete("1.0", "end")
        self.review.insert("1.0", text)
        self.review.configure(state="disabled")

    def goal_changed(self, *_):
        if self.goal_entry.edit_modified():
            self.goal_entry.edit_modified(False)
            self.changed()

    def changed(self, *_):
        if self.busy or self.session.attempted:
            return
        self.key_entry.delete(0, "end")
        self.key_entry.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.session.invalidate()
        self.status.configure(text="Model, budget, instructions or mode changed. Run preflight again before key entry.")

    def preflight(self):
        self.key_entry.delete(0, "end")
        self.key_entry.configure(state="disabled")
        self.send_button.configure(state="disabled")
        try:
            checkpoint = self.session.preflight(self.model.get(), self.budget.get(),
                                                self.goal_entry.get("1.0", "end-1c"))
        except Exception:
            self.key_entry.configure(state="disabled")
            self.send_button.configure(state="disabled")
            self.status.configure(text="Preflight failed. Check the model ID, layout instructions and output limit (256-8,192). No key was read or request sent.")
            return
        s = checkpoint.summary
        limit_text = (f"Shared output-token budget: {s['max_output_tokens']:,}; up to 3 paid requests / 12 tool calls; zero retries."
                      if checkpoint.tool_mode else
                      f"Maximum output tokens: {s['max_output_tokens']:,}; one request; zero retries.")
        scope_text = ("Supplied per-prototype catalog controls quality, module, setting and wire choices."
                      if s["configuration_export_scope"] == "rich_declared_configuration" else
                      "Current export supports normal geometry only; richer capabilities are not supplied.")
        self.set_review(f"Model: {s['model']}\n{limit_text}\n"
                        f"Request: {s['input_json_bytes']:,} input JSON bytes / {s['http_payload_bytes']:,} HTTP payload bytes.\n"
                        "Synthetic factory state; up to four entities / two stages; proposed / unbuilt.\n"
                        f"Allowed prototypes: {', '.join(s['allowed_prototypes'])}.\n"
                        f"Unlocked recipe names: {', '.join(s['unlocked_recipes']) or 'none'}.\n{scope_text}\n"
                        f"30-second transport / {s['total_provider_deadline_seconds']:g}-second total session deadline.\n"
                        "Token limits are not a dollar spending cap. Account/model compatibility remains unverified.")
        self.key_entry.configure(state="normal")
        self.send_button.configure(state="normal")
        self.key_entry.focus_set()
        self.status.configure(text=("Preflight passed. Enter the key in the masked field, then click Start tool session. "
                                    "The session can send up to three paid requests for tool feedback."
                                    if checkpoint.tool_mode else
                                    "Preflight passed. Enter the key in the masked field below, then click Send one paid request."))

    def send(self):
        if self.busy or self.session.attempted:
            return
        try:
            credential = validate_credential(self.key_entry.get())
        except Exception:
            self.status.configure(text="Enter your API key in the masked local field. It will not be saved.")
            return
        self.key_entry.delete(0, "end")
        self.key_entry.configure(state="disabled")
        self.model_entry.configure(state="disabled")
        self.budget_entry.configure(state="disabled")
        self.goal_entry.configure(state="disabled")
        self.mode_button.configure(state="disabled")
        self.preflight_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.busy = True
        self.status.configure(text=("Starting the bounded tool session: up to 3 paid requests, 12 tool calls, "
                                    "and one shared output budget. The key field was cleared. Zero retries."
                                    if self.session.tool_mode else
                                    "Sending one bounded paid request. The masked field has been cleared. No automatic retry."))
        if self.session.tool_mode:
            self.open_grid()

        def work():
            try:
                result = self.session.execute(credential, on_change=self.queue_grid)
                assert_no_credentials(result)
            except Exception:
                result = {"stage": "local_error", "reason": "local_setup_or_request_failed"}
            # Always let the UI leave the busy state; never print a raw traceback.
            self.messages.put(result)
        threading.Thread(target=work, daemon=True, name="designer-bounded-session").start()

    def queue_grid(self, state):
        assert_no_credentials(state)
        self.messages.put({"stage": "grid_update", "state": json.loads(canonical_bytes(state))})

    def open_grid(self):
        if self.grid_window is not None and self.grid_window.winfo_exists():
            self.grid_window.lift()
            return
        window = self.tk.Toplevel(self.root)
        window.title("LLM-controlled draft grid - proposed / unbuilt")
        window.geometry("1220x750")
        window.minsize(1000, 580)
        self.grid_window = window
        self.ttk.Label(window, text="Actual model tool edits appear here as they are applied to the draft.",
                       font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        self.grid_info = self.ttk.Label(window, text="Waiting for model tool calls. No native game placement.",
                                       wraplength=980)
        self.grid_info.pack(anchor="w", padx=16, pady=(0, 4))
        body = self.ttk.Frame(window)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        self.grid_canvas = self.tk.Canvas(body, bg="#edf2f1", highlightthickness=0)
        self.grid_canvas.pack(side="left", fill="both", expand=True)
        self.grid_canvas.bind("<Configure>", self.draw_grid)
        sidebar = self.ttk.Frame(body, padding=(12, 0, 0, 0), width=340)
        sidebar.pack(side="right", fill="y")
        self.ttk.Label(sidebar, text="Inspect draft entity (read only)",
                       font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))
        self.inspection_choice = self.ttk.Combobox(sidebar, state="readonly", width=34)
        self.inspection_choice.pack(fill="x", pady=(0, 8))
        self.inspection_choice.bind("<<ComboboxSelected>>", self.inspect_choice)
        inspection_body = self.ttk.Frame(sidebar)
        inspection_body.pack(fill="both", expand=True)
        self.inspection_text = self.tk.Text(inspection_body, width=39, height=26,
                                            wrap="word", state="disabled", bg="#f8faf9", relief="flat")
        self.inspection_text.pack(side="left", fill="both", expand=True)
        scroll = self.ttk.Scrollbar(inspection_body, orient="vertical", command=self.inspection_text.yview)
        scroll.pack(side="right", fill="y")
        self.inspection_text.configure(yscrollcommand=scroll.set)
        self.update_inspector()
        self.ttk.Label(window, text="Gray: observed occupied footprints. Teal: proposed model entities. "
                       "Orange outline: model-selected entity. Lines: declared wires. Click an entity to inspect it. "
                       "Draft edits do not build or move native machinery.",
                       wraplength=1150).pack(anchor="w", padx=16, pady=(0, 12))

    def inspect_choice(self, *_):
        self.inspected_entity_id = self.inspection_choice.get() or None
        self.update_inspector()

    def inspect_entity(self, entity_id):
        self.inspected_entity_id = entity_id
        self.update_inspector()

    def update_inspector(self):
        state = self.grid_state
        if state is None:
            text = "Waiting for actual model tool edits."
        else:
            ids = [row["id"] for row in state["entities"]]
            self.inspection_choice.configure(values=ids)
            if self.inspected_entity_id not in ids:
                self.inspected_entity_id = state.get("selected_entity_id")
            self.inspection_choice.set(self.inspected_entity_id or "")
            checkpoint = self.session.checkpoint
            capabilities = checkpoint.summary.get("configuration_capabilities", {}) if checkpoint else {}
            text = entity_inspection(state, self.inspected_entity_id, capabilities)
        self.inspection_text.configure(state="normal")
        self.inspection_text.delete("1.0", "end")
        self.inspection_text.insert("1.0", text)
        self.inspection_text.configure(state="disabled")

    def draw_grid(self, *_):
        if self.grid_state is None or self.grid_window is None or not self.grid_window.winfo_exists():
            return
        canvas, state = self.grid_canvas, self.grid_state
        drawing = grid_geometry(state, max(canvas.winfo_width(), 500), max(canvas.winfo_height(), 380))
        canvas.delete("all")
        left, top, right, bottom = drawing["screen_bounds"]
        x1, y1, x2, y2 = drawing["bounds"]
        step = max(1, math.ceil(max(x2 - x1, y2 - y1) / 100))
        scale = drawing["scale"]
        for x in range(math.ceil(x1 / step) * step, math.floor(x2) + 1, step):
            px = left + (x - x1) * scale
            canvas.create_line(px, top, px, bottom, fill="#d6dfdc")
        for y in range(math.ceil(y1 / step) * step, math.floor(y2) + 1, step):
            py = top + (y - y1) * scale
            canvas.create_line(left, py, right, py, fill="#d6dfdc")
        canvas.create_rectangle(left, top, right, bottom, outline="#697773", width=2)
        canvas.create_text(left, top - 16, anchor="w", text=f"({x1:g}, {y1:g})", fill="#44534e")
        canvas.create_text(right, bottom + 16, anchor="e", text=f"({x2:g}, {y2:g})", fill="#44534e")
        for box in drawing["occupied"]:
            canvas.create_rectangle(*box, fill="#9aaba4", outline="#657770", stipple="gray50")
        arrow_vectors = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
        centers = {shape["id"]: shape["center"] for shape in drawing["shapes"]}
        wire_colors = {"red": "#d84040", "green": "#2eaa4e", "copper": "#b47431"}
        for wire in state.get("circuit_wires", []):
            if wire["from_id"] in centers and wire["to_id"] in centers:
                first, second = centers[wire["from_id"]], centers[wire["to_id"]]
                canvas.create_line(*first, *second, fill=wire_colors[wire["color"]], width=3,
                                   dash=(5, 3), tags="declared_wire")
        for shape in drawing["shapes"]:
            entity_tag = "entity_" + shape["id"]
            canvas.create_rectangle(*shape["box"], fill="#3c8b7f",
                                    outline="#d68127" if shape["selected"] else "#246056",
                                    width=4 if shape["selected"] else 2, tags=entity_tag)
            cx, cy = shape["center"]
            vx, vy = arrow_vectors[shape["direction"]]
            span = min(20, max(6, scale * 0.6))
            canvas.create_line(cx, cy, cx + vx * span, cy + vy * span,
                               fill="#ffffff", width=2, arrow="last", tags=entity_tag)
            px, py = shape["position"]
            label = f"{shape['id']} · {shape['prototype']}\n({px:g}, {py:g})"
            canvas.create_text(cx, shape["box"][3] + 6, text=label, anchor="n",
                               fill="#173e35", font=("Segoe UI", 9), width=190, tags=entity_tag)
            canvas.tag_bind(entity_tag, "<Button-1>", lambda _, entity_id=shape["id"]: self.inspect_entity(entity_id))
        selected = state["selected_entity_id"] or "none"
        scope = state.get("configuration_export_scope", "legacy_normal_geometry_only")
        self.grid_info.configure(text=f"Draft revision {state['revision']} · {len(drawing['shapes'])} proposed entities · "
                                 f"Model selection: {selected} · {len(state.get('circuit_wires', []))} declared wires · "
                                 f"{scope} · Proposed / unbuilt; native flow unqualified.")
        self.update_inspector()

    def poll(self):
        record = None
        while True:
            try:
                pending = self.messages.get_nowait()
            except queue.Empty:
                break
            if pending["stage"] == "grid_update":
                self.grid_state = pending["state"]
                self.draw_grid()
            else:
                record = pending
        if record is None:
            self.root.after(150, self.poll)
            return
        self.busy = False
        stage = record["stage"]
        if stage == "accepted_offline":
            self.status.configure(text="Model proposal accepted for offline geometry. The blueprint and its exact PNG are saved. Proposed / unbuilt; native flow unqualified.")
            self.show_preview(Path(record["output_directory"]) / "preview.png")
        elif stage == "local_artifacts_incomplete":
            self.status.configure(text="The paid result was received, but local export needs repair. Existing artifacts were preserved. No retry was sent.")
        elif stage == "rejected":
            detail = (validation_message(record.get("validation")) if record["reason"] == "local_validation_failed"
                      else record["reason"])
            self.status.configure(text="No accepted plan or PNG: " + detail + " Safe diagnostics saved; no retry was sent.")
        else:
            self.status.configure(text="Local setup/request failed. No automatic retry. Check safe progress diagnostics before starting another session.")
        self.root.after(150, self.poll)

    def show_preview(self, path):
        from PIL import Image, ImageTk
        window = self.tk.Toplevel(self.root)
        window.title("Actual accepted model blueprint - proposed / unbuilt")
        with Image.open(path) as source:
            picture = source.copy()
        picture.thumbnail((1050, 700))
        photo = ImageTk.PhotoImage(picture)
        label = self.ttk.Label(window, image=photo)
        label.image = photo
        label.pack(padx=12, pady=12)
        self.ttk.Label(window, text="Rendered from the accepted model geometry. Native material flow and construction remain unqualified.").pack(pady=(0, 12))

    def safe_callback_error(self, *_):
        self.key_entry.delete(0, "end")
        self.status.configure(text="Local UI error. The key field was cleared; no automatic retry will be sent.")

    def close(self):
        if self.busy:
            self.status.configure(text="The paid session is running. Wait for its bounded result before closing this window.")
            return
        self.key_entry.delete(0, "end")
        if not self.session.attempted:
            self.session.write_progress("closed_before_request", paid_requests=0)
        self.root.destroy()


def main():
    import argparse
    import tkinter as tk
    parser = argparse.ArgumentParser(description="Private local setup; opening never submits a request.")
    parser.add_argument("--model", default="", help="Explicit public model choice; never a credential.")
    parser.add_argument("--output-tokens", type=int, default=2048)
    parser.add_argument("--updated", action="store_true")
    parser.add_argument("--tools", action="store_true", help="Explicit tool session: at most 3 paid requests and 12 tool calls.")
    parser.add_argument("--context", type=Path, help="Explicit public context/catalog file; default is the declared synthetic iron fixture.")
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="Public draft layout instructions; editable before preflight.")
    options = parser.parse_args()
    assert_no_credentials(options.model)
    assert_no_credentials(options.goal)
    if not 256 <= options.output_tokens <= 8192:
        raise ContractError("Invalid output-token choice")
    root = tk.Tk()
    session = LocalSession(tools=options.tools, context_path=options.context)
    SetupWindow(root, session, initial_model=options.model,
                initial_budget=options.output_tokens, updated=options.updated, initial_goal=options.goal)
    root.lift()
    root.after(250, root.lift)
    root.mainloop()


if __name__ == "__main__":
    main()
