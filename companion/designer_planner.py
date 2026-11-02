"""LLM layout planner: the designer package designs, the game builds.

Converts an ``npc-site-v1`` export from the game into a ``designer-context-v1``
observation, runs one bounded tool-mode designer session (OpenAI Responses),
and turns the accepted, locally validated plan into the ``/npc-plan submit``
format. Rejected or failed sessions raise PlanError; the caller decides the
fallback. The API key comes from OPENAI_API_KEY and is never logged.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "designer"))

from designer_llm.context import canonical_bytes, validate_context  # noqa: E402
from designer_llm.private_config import PrivateConfigError, ResponsesConfig, validate_credential  # noqa: E402
from designer_llm.providers import OpenAIHTTPTransport, ProviderResult, prepare_request  # noqa: E402
from designer_llm.request import make_request  # noqa: E402
from designer_llm.tool_provider import ToolEditingProvider  # noqa: E402
from designer_llm.workflow import propose  # noqa: E402

from companion import usage as ledger  # noqa: E402
from companion import ledger as factory_ledger  # noqa: E402
from companion import avoid, rates, team_area  # noqa: E402
from companion import decisions_api  # noqa: E402
from companion.decisions_api import api_key_from_environment, out_of_credit  # noqa: E402
from companion.layout_planner import PlanError, tiles_from_rows  # noqa: E402

DEFAULT_MODEL = "gpt-6.1-sol"
METRIC_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")   # an item the context schema can key by
CONTEXT_BUDGET = 96_000   # bytes of context: a designer request takes 120 KB in all, the request itself too
GOAL_CHARS = 4000         # the most a request's goal may hold
REPAIR_ROOM = 1650        # of it kept for the repair round's findings
START_CHARS = 1500        # an adaptation's brief (the saved build, its gaps and notes), at most
PROTOTYPE_FACTS = ("type", "energy_source", "fuel_categories", "crafting_categories", "resource_categories",
                   "mining_area_radius", "drop_vector", "pickup_vector", "supply_area_distance",
                   "max_wire_distance", "produces_power", "fluid_boxes", "requires_water", "container_distance")


def as_list(value: Any) -> list:
    # Factorio's JSON writer turns empty Lua tables into {}.
    if isinstance(value, dict) and not value:
        return []
    return list(value or [])


def normalize_fluid_boxes(boxes: Any) -> list:
    """Restore the null fields Factorio's JSON writer drops, and keep only connection kinds the
    designer understands (normal and underground; linked connections are skipped)."""
    out = []
    for index, box in enumerate(as_list(boxes), start=1):
        connections = []
        for c in as_list(box.get("connections")):
            kind = c.get("type", "normal")
            if kind not in ("normal", "underground"):
                continue
            position = as_list(c.get("position"))
            if len(position) != 2:
                continue
            distance = c.get("max_underground_distance")
            connections.append({"position": [float(position[0]), float(position[1])],
                                "direction": int(c.get("direction", 0)) % 16, "type": kind,
                                "max_underground_distance": distance if kind == "underground" else None})
        production = box.get("production", "none")
        out.append({"index": int(box.get("index", index)),
                    "production": production if production in ("input", "output", "input-output") else "none",
                    "filter": box.get("filter"), "connections": connections})
    return out


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def recipe_rows(site: Mapping[str, Any]) -> list[dict]:
    """The recipes it can make; with the game's facts, each one's time and what each machine that can make
    it turns out a second (its main product, at full speed)."""
    facts = rates.of(site)
    rows = []
    for recipe in as_list(site.get("recipes")):
        ingredients = [{"item": i["name"], "amount": i["amount"]} for i in as_list(recipe.get("ingredients"))]
        results = [{"item": p["name"], "amount": p["amount"]} for p in as_list(recipe.get("products"))]
        row = {"name": recipe["name"], "category": recipe.get("category"),
               "handcraftable": bool(recipe.get("handcraft")),
               "ingredients": ingredients, "results": results}
        if facts and recipe.get("energy"):
            row["seconds"] = recipe["energy"]
            made = facts.per_machine(recipe, limit=2)
            if made:
                row["made_in"] = [{"machine": name, "per_second": rate} for name, rate in made]
        rows.append(row)
    return rows


METRIC_ITEMS = 16   # items whose measured rates the designer sees (the busiest)


def item_metrics(site: Mapping[str, Any], wrap: Callable, unknown: Callable) -> dict | None:
    """The factory ledger's measured rates for the items the base makes: what it makes a second over the
    last half hour or so, how much of what it can make is used, and what is left over."""
    book = factory_ledger.Ledger.from_site(site)
    rows = []
    for item in book.items():
        b = book.budget(item)
        minutes = max((p.minutes for p in book.producers(item)), default=0)
        if b.supply <= 0 or not METRIC_ID.fullmatch(item):
            continue
        window = min(minutes, factory_ledger.MATURE_MINUTES) * 3600
        measured = b.made > 0 and window > 0
        rows.append((b.made, item, {
            "cumulative_produced": unknown("ledger"), "stock": unknown("ledger"),
            "rate_items_per_second": wrap(round(b.made, 4), "ledger") if measured else unknown("ledger"),
            "measured_window_ticks": wrap(window, "ledger") if measured else unknown("ledger"),
            "utilization": wrap(round(min(1.0, b.share), 3), "ledger"),
            "spare_capacity_items_per_second": wrap(round(max(0.0, b.headroom), 4), "ledger")}))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return {item: m for _, item, m in rows[:METRIC_ITEMS]} or None


def ranked_recipes(rows: list[dict], site: Mapping[str, Any]) -> list[dict]:
    """The recipes in the order a design needs them: smelting, science packs, what the base already runs
    and what makes a building it can place first, then the intermediates the most other recipes use
    (an item nothing else uses, an incinerator mod's ash, last)."""
    used: dict[str, int] = {}
    for row in rows:
        for i in row["ingredients"]:
            used[i["item"]] = used.get(i["item"], 0) + 1
    running = {str(name) for b in as_list(site.get("base")) if isinstance(b, Mapping)
               for name in as_list(b.get("recipes"))}
    placed = {facts.get("placement_item") for facts in (site.get("prototypes") or {}).values() if facts}

    def key(row: dict) -> tuple:
        first = (row["category"] == "smelting" or row["name"] in running
                 or any(str(r["item"]).endswith("-science-pack") or r["item"] in placed for r in row["results"]))
        return (not first, -sum(used.get(r["item"], 0) for r in row["results"]), row["name"])
    return sorted(rows, key=key)


def build_context(site: Mapping[str, Any], budget: int = CONTEXT_BUDGET) -> dict[str, Any]:
    """The designer's observation of a site. A heavily modded game unlocks more recipes than fit: then
    only the most useful are kept, as many as fit in `budget` bytes."""
    rows = recipe_rows(site)
    context = assemble_context(site, rows)
    if len(canonical_bytes(context)) > budget:
        ranked = ranked_recipes(rows, site)
        keep = len(ranked)
        while keep and len(canonical_bytes(context)) > budget:
            keep = int(keep * 0.8)
            kept = {row["name"] for row in ranked[:keep]}
            context = assemble_context(site, [row for row in rows if row["name"] in kept])
    return validate_context(context)


def assemble_context(site: Mapping[str, Any], recipes: list[dict]) -> dict[str, Any]:
    tick, revision = int(site["tick"]), 1
    force, surface = site["force"], site.get("surface", "nauvis")
    actor = f"npc-{site['npc_id']}"

    def wrap(value: Any, source: str) -> dict:
        return {"knowledge": "known", "value": value, "observed_tick": tick, "revision": revision,
                "provenance": {"kind": "native_snapshot", "source_id": f"npc-site:{source}"}}

    def unknown(source: str) -> dict:
        return {"knowledge": "unknown", "value": None, "observed_tick": tick, "revision": revision,
                "provenance": {"kind": "native_snapshot", "source_id": f"npc-site:{source}"}}

    prototypes = {}
    for name, facts in (site.get("prototypes") or {}).items():
        if not facts or not facts.get("placement_item"):
            continue
        spec = {"placement_item": facts["placement_item"], "width": int(facts["width"]),
                "height": int(facts["height"])}
        for key in PROTOTYPE_FACTS:
            if key in facts and facts[key] is not None:
                value = facts[key]
                if key in ("fuel_categories", "crafting_categories", "resource_categories"):
                    value = as_list(value)
                if key == "fluid_boxes":
                    value = normalize_fluid_boxes(value)
                spec[key] = value
        prototypes[name] = spec

    items = set(site.get("inventory") or {})
    for recipe in recipes:
        items.update(row["item"] for row in recipe["ingredients"] + recipe["results"])
    items.update(spec["placement_item"] for spec in prototypes.values())
    # Mined resources (vanilla ore entities yield the same-named item) and starter fuels.
    items.update(r["resource"] for r in as_list(site.get("resources")))
    items.update(("coal", "wood"))

    bounds = as_list(site["bounds"])
    # The team world: other plots and the ground round players' builds come first (the list is capped).
    team = team_area.team_of(site)
    occupied, obstructions = [list(r) for r in team.forbidden_rects()] if team else [], []
    occupied += [list(r) for r in avoid.rects(site)]   # (ground the game handed back as unbuildable)
    for obstacle in as_list(site.get("obstacles")):
        row = {"name": obstacle["name"], "box": obstacle["box"],
               "removal": "npc-mines-it-first" if obstacle.get("mineable") else "cannot-build-here"}
        obstructions.append(row)
        if not obstacle.get("mineable"):
            occupied.append(obstacle["box"])
    water = [[x1, y, x2 + 1, y + 1] for y, x1, x2 in as_list(site.get("water_rows"))]
    occupied.extend(water)
    built = []
    for entity in as_list(site.get("built")):
        spec = prototypes.get(entity["name"], {"width": 1, "height": 1})
        x, y = entity["position"]["x"], entity["position"]["y"]
        w, h = (spec["height"], spec["width"]) if entity.get("direction", 0) in (4, 12) else (spec["width"], spec["height"])
        occupied.append([x - w / 2, y - h / 2, x + w / 2, y + h / 2])   # (a loader facing east lies east-west)
        row = {"entity_id": f"unit-{entity.get('unit_number')}", "native_unit_number": entity.get("unit_number"),
               "prototype": entity["name"], "owner_force_id": force, "surface_id": surface,
               "position": {"x": x, "y": y}, "direction": entity.get("direction", 0)}
        if entity.get("powered") is not None:   # a pole known to be on a live network
            row["powered"] = bool(entity["powered"])
        if entity.get("mirror") is True:   # built mirrored: its pipe connections flip (the checks read it)
            row["mirror"] = True
        built.append(row)
    patches, summary = [], []
    for resource in as_list(site.get("resources")):
        rows = as_list(resource.get("rows"))
        summary.append({"resource": resource["resource"], "tiles": len(tiles_from_rows(rows)),
                        "remaining_amount": resource.get("amount")})
        for index, (y, x1, x2) in enumerate(rows):
            patches.append({"patch_id": f"{resource['resource']}-row-{index}", "resource": resource["resource"],
                            "bounds": [x1, y, x2 + 1, y + 1]})

    mod_hash = sha256({"mod": "npc-factions", "version": site.get("mod_version", "unknown")})
    snapshot = {
        "schema": "designer-snapshot-v1", "game_version": site.get("game_version", "2.0.77"),
        "source_hash": mod_hash, "world_id": site["world_id"], "force_id": force, "observed_tick": tick,
        "quality": "normal", "researched_technologies": sorted(as_list(site.get("researched"))),
        "unlocked_recipes": sorted(r["name"] for r in recipes), "items": sorted(items),
        "prototypes": prototypes, "survey_bounds": bounds, "occupied_boxes": occupied[:4096],
    }
    inventory = {k: v for k, v in (site.get("inventory") or {}).items() if v}
    context = {
        "schema": "designer-context-v1", "evidence_mode": "native_export",
        "identity": {"world_id": site["world_id"], "force_id": force, "surface_id": surface,
                     "actor_id": actor, "life_id": f"{actor}-life-1", "session_epoch": 1,
                     "game_version": snapshot["game_version"], "source_hash": mod_hash,
                     "prototype_hash": sha256(prototypes), "observed_tick": tick, "revision": revision},
        "geometry_snapshot": snapshot,
        "facts": {
            "built_machines": wrap(built, "built"),
            # The engineer's base, block by block: where each built plan stands and how it is doing,
            # so a design fits beside it (and can upgrade instead of adding).
            "blocks": wrap([{k: b[k] for k in ("id", "purpose", "stage", "box", "machines", "recipes", "status")
                             if b.get(k) is not None} for b in as_list(site.get("base"))[:64]
                            if isinstance(b, Mapping)], "blocks"),
            "connections": unknown("connections"),
            "recipes": wrap(recipes, "recipes"),
            "inventories": wrap([{"inventory_id": "actor-main", "owner_force_id": force, "owner_actor_id": actor,
                                  "accessible": wrap(True, "inventory-access"),
                                  "contents": wrap(inventory, "inventory"), "access_reason": "owned-carried"}],
                                "inventories"),
            "demand": wrap([{"item": "iron-plate", "metric": "rate_items_per_second", "amount": 0.5,
                             "window_ticks": 3600, "purpose": site.get("goal", "start production"),
                             "priority": 1}], "demand"),
            "item_metrics": wrap(metrics, "item-metrics") if (metrics := item_metrics(site, wrap, unknown))
            else unknown("item-metrics"),
            "terrain": wrap({"survey_bounds": bounds, "occupied_declared_boxes": occupied[:4096],
                             "resource_patches": wrap(patches, "resources"),
                             "resource_summary": summary,
                             "water": wrap(water, "water"),
                             "obstructions": wrap(obstructions, "obstacles")}, "terrain"),
            "pending_work": wrap({"active_job": None, "accepted_plans": [], "executed_construction_receipts": [],
                                  "native_confirmed_construction_events": [],
                                  "last_productive_event": unknown("last-production"),
                                  "available_construction_material_scope": "only the carried inventory; later "
                                  "stages are paid for with plates this plan produces and hand-mined stone",
                                  "chat_claims_are_authoritative": False}, "pending"),
        },
    }
    return context


def loader_ends(document: Mapping[str, Any]) -> dict[str, str]:
    """Which end ("input"/"output") each loader of an accepted design is: the proposal schema has no field
    for it, so it is read from what stands at the loader's ends, as the local checks did (they accept a
    loader only with a container or machine at exactly one end and a belt at the other)."""
    from designer_llm._reference_contract import Entity
    from designer_llm.functional_validation import existing_geometry, loader_ends as ends_of
    context = document.get("context") or {}
    catalog = (context.get("geometry_snapshot") or {}).get("prototypes") or {}
    rows = document["manifest"]["entities"]
    if not any((catalog.get(e["prototype"]) or {}).get("type") == "loader" for e in rows):
        return {}
    entities = {e["id"]: Entity(e["id"], e["prototype"], e["position"]["x"], e["position"]["y"],
                                e.get("direction") or 0, e.get("recipe")) for e in rows}
    built, _ = existing_geometry(context)
    entities.update({k: e for k, e in built.items() if e.prototype in catalog})
    return ends_of(entities, catalog)[0]


def to_plan(document: Mapping[str, Any]) -> dict[str, Any]:
    manifest = document["manifest"]
    entities = [{key: e[key] for key in ("id", "prototype", "position", "direction", "recipe") if e.get(key) is not None}
                for e in manifest["entities"]]
    ends = loader_ends(document)
    for e in entities:
        if e["id"] in ends:
            e["belt_to_ground_type"] = ends[e["id"]]
    stages = []
    for stage in manifest["stages"]:
        stages.append({"id": stage["id"], "depends_on": list(stage.get("depends_on") or []),
                       "entity_ids": list(stage["entity_ids"]),
                       "manual_supplies": list(stage.get("manual_supplies") or []),
                       "witnesses": list(stage.get("witnesses") or [])})
    return {"plan_id": manifest["plan_id"], "source": "designer-llm", "entities": entities, "stages": stages,
            "design_sha256": document.get("design_sha256")}


class CodexDesignProvider:
    """The designer's proposal through Codex, on the user's ChatGPT plan: the same instructions and request,
    one answer in the proposal schema (Codex cannot drive the draft editor's tools; LLMPlanner gives it one
    repair round with the local checks' findings instead)."""
    provider_id = "codex-exec"
    mock = False

    def __init__(self, codex: Any, config: ResponsesConfig, *, effort: str = "medium", purpose: Any = None,
                 npc: Any = None, knowledge: Mapping[str, Any] | None = None, lookups: int = 0):
        """`knowledge` and `lookups`: what the run may look up, and how many times (knowledge_mcp.py)."""
        self.codex, self.config, self.effort, self.purpose, self.npc = codex, config, effort, purpose, npc
        self.knowledge, self.lookups = knowledge, lookups
        self.last_error: str | None = None   # what Codex said went wrong (a timeout says what it was doing)

    def propose(self, request: dict[str, Any], schema: dict[str, Any]) -> ProviderResult:
        started = time.perf_counter()

        def result(status: str, reason: str, proposal: dict | None = None) -> ProviderResult:
            return ProviderResult(status, proposal, self.provider_id, False, reason,
                                  {"latency_ms": int((time.perf_counter() - started) * 1000), "attempt_count": 1,
                                   "model": self.config.model})
        try:
            payload = prepare_request(self.config, request, schema)
        except PrivateConfigError as failure:
            return result("error", str(failure))
        text = (payload["input"][0]["content"] + "\n\nThe request and the observed world, as JSON:\n"
                + payload["input"][1]["content"])
        try:
            extra = {"knowledge": self.knowledge, "lookups": self.lookups} if self.knowledge and self.lookups else {}
            answer = self.codex.ask(text, payload["text"]["format"]["schema"], model=self.config.model,
                                    effort=self.effort, system="designer", purpose=self.purpose, npc=self.npc,
                                    **extra)
        except decisions_api.OutOfCredit:
            return result("error", "provider_rate_limited")
        except decisions_api.DecisionsAPIError as failed:
            self.last_error = str(failed)[:300]
            return result("error", "transport_error")
        return result("proposal", "codex_proposal", answer)


class LLMPlanner:
    def __init__(self, *, model: str = DEFAULT_MODEL, max_entities: int = 12, max_stages: int = 3,
                 output_tokens: int = 8192, transport: Callable | None = None,
                 credential: Callable[[], str] | None = None, log: Callable[[str], None] = print,
                 codex: Any = None, effort: str = "medium"):
        """`codex`: a codex_backend.Codex to design through (the ChatGPT plan) instead of the API."""
        self.config = ResponsesConfig(model=model, max_output_tokens=output_tokens, total_deadline_s=60)
        self.codex, self.effort = codex, effort
        self.transport = transport or (None if codex else OpenAIHTTPTransport())
        self.credential = credential or (lambda: validate_credential(api_key_from_environment()))
        self.max_entities, self.max_stages, self.log = max_entities, max_stages, log
        self.last_outcome = None

    def __call__(self, site: Mapping[str, Any]) -> dict[str, Any]:
        if out_of_credit():
            raise PlanError(decisions_api.pause_reason())
        context = build_context(site)
        request_id = f"npc{site['npc_id']}-r{str(site['request_id']).split(':')[-1]}"
        goal = site.get("goal", "Start automatic iron plate production.")
        # Adapting a saved build (companion/build_choice.py): the build where it stands, its gaps and notes,
        # and to change only what those need. Room for all of it: a bigger build may keep all its parts.
        start = site.get("start_from") if isinstance(site.get("start_from"), Mapping) else {}
        if start.get("text"):
            goal += "\n\n" + str(start["text"])[:START_CHARS]
        entities = max(self.max_entities, min(128, int(start.get("entities") or 0) + 8)) if start else self.max_entities
        facts = rates.of(site)
        numbers = facts.brief(site.get("prototypes") or ()) if facts else ""
        head = "\n\nThis game's numbers (its own data, mods included; full speed, normal quality):\n"
        room = GOAL_CHARS - REPAIR_ROOM - len(goal) - len(head)
        if numbers and room > 200:   # this game's own numbers, mods included: size the design on them
            goal += head + numbers[:room]
        request = make_request(context, goal, request_id=request_id, max_entities=entities,
                               max_stages=self.max_stages)
        if self.codex:
            # a few lookups for what the brief did not carry, when the request has the game's facts
            from companion import codex_backend, knowledge_mcp
            knowledge = knowledge_mcp.knowledge_for(site) if codex_backend.DESIGNER_LOOKUPS else None
            provider = CodexDesignProvider(self.codex, self.config, effort=self.effort,
                                           purpose=site.get("purpose") or goal, npc=site.get("npc_id"),
                                           knowledge=knowledge, lookups=codex_backend.DESIGNER_LOOKUPS)
        else:
            provider = ToolEditingProvider(self.config, self.transport, self.credential)
        outcome = propose(request, context, provider, max_input_bytes=120_000)
        detail = outcome.evidence.get("validation")
        if self.codex and not outcome.accepted and detail:
            # One repair round: the local checks' findings, as the draft editor would have shown them.
            self.log(f"designer {request_id}: {outcome.reason}; asking Codex to fix it")
            retry = make_request(context, goal + "\n\nYour last proposal for this was rejected by the local "
                                 "checks: " + json.dumps(detail)[:1500] + "\nFix every problem listed and "
                                 "propose again.", request_id=request_id, max_entities=entities,
                                 max_stages=self.max_stages)
            outcome = propose(retry, context, provider, max_input_bytes=120_000)
        self.last_outcome = outcome
        usage = outcome.evidence.get("provider", {}).get("metadata", {})
        if not self.codex:   # (Codex runs note themselves)
            ledger.record("designer", self.config.model, input_tokens=usage.get("input_tokens"),
                          output_tokens=usage.get("output_tokens"), latency_ms=usage.get("latency_ms"),
                          ok=outcome.accepted is not None, purpose=site.get("purpose") or site.get("goal"),
                          npc=site.get("npc_id"))
        if self.codex:
            said = f": {provider.last_error}" if getattr(provider, "last_error", None) else ""
            self.log(f"designer {request_id}: {outcome.status} ({outcome.reason}{said}) through Codex, "
                     f"{self.config.model}, {usage.get('latency_ms')} ms")
        else:
            self.log(f"designer {request_id}: {outcome.status} ({outcome.reason}); "
                     f"tokens in/out {usage.get('input_tokens')}/{usage.get('output_tokens')}, "
                     f"tool calls {usage.get('tool_call_count')}, {usage.get('latency_ms')} ms")
        if not outcome.accepted:
            detail = outcome.evidence.get("validation") or outcome.evidence.get("reason")
            raise PlanError(f"designer {outcome.reason}: {json.dumps(detail)[:300] if detail else ''}")
        return to_plan(outcome.accepted.document)


def with_fallback(primary: Callable, fallback: Callable, log: Callable[[str], None] = print) -> Callable:
    def plan(site: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return primary(site)
        except Exception as exc:
            log(f"designer failed ({exc}); using the deterministic starter layout")
            return fallback(site)
    return plan
