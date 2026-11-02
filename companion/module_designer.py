"""Bus modules designed by the LLM designer: a rate goal with belt ports, checked before use.

The ratio calculator says what to build (machines per recipe for N items per
second); the designer decides how, inside a box whose west edge holds the belts
the bus taps arrive on and the belt that takes the product back to the bus. The
ports are the built-in module's, so a design can replace it on the bus. A design
is kept only when it passes the local checks here and, in the sandbox, makes
enough of its goal (tools/design_module.py); then it goes into the module
library, which the bus planner prefers over the built-in modules.

One proposal per call (OpenAI Responses, structured output): modules need far
more buildings than the tool-editing designer's twelve calls allow. The API key
comes from OPENAI_API_KEY and is never logged.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from companion import designer_planner as dp
from companion import usage as ledger
from companion import modules
from companion.modules import EAST, Module, Placed, Port, WEST
from companion.ratios import Book, solve
from companion.smelting import footprint

KIND_ITEM = {"gears": "iron-gear-wheel", "green_circuits": "electronic-circuit",
             "red_science": "automation-science-pack", "green_science": "logistic-science-pack"}
PROTOTYPES = ("transport-belt", "underground-belt", "splitter", "inserter", "fast-inserter",
              "long-handed-inserter", "assembling-machine-1", "assembling-machine-2", "small-electric-pole")
SPARE = (6, 4)     # extra columns and rows over the built-in module's box: room to do better
SPAN = 5           # yellow underground belts: exit at most five tiles past the entrance


class DesignError(Exception):
    """The designer gave no usable module."""


def reference(kind: str, rate: float, book: Book) -> Module:
    """The built-in module: its ports (and roughly its size) are what a design must keep."""
    return getattr(modules, kind)(rate, book)


def box(ref: Module) -> tuple[int, int]:
    return ref.width + SPARE[0], ref.height + SPARE[1]


def power_row(ref: Module, height: int) -> int:
    """A west-edge row clear of the ports (and their neighbours) for the network's pole."""
    taken = {p.y + d for p in ref.ports for d in (-1, 0, 1)}
    return next((y for y in range(height - 1, -1, -1) if y not in taken), height - 1)


# What inserters move when the game's facts are not at hand (vanilla, no research).
INSERTERS = "inserter about 0.8 items/s, fast-inserter 2.2, long-handed 1.1"


def inserter_rates(game) -> str:
    """The inserters' items a second in this game (rates.Facts), else the vanilla figures."""
    found = [f"{name} {game.inserter_rate(name):g} items/s" for name in ("inserter", "fast-inserter", "long-handed-inserter")
             if game and game.inserter_rate(name)]
    return ", ".join(found) if found else INSERTERS


def brief(kind: str, rate: float, book: Book, ref: Module, machine: str, game=None) -> str:
    """`game`: this game's facts (companion/rates.py), for what its inserters really move."""
    item = KIND_ITEM[kind]
    width, height = box(ref)
    inputs = sorted({p.item for p in ref.ports if p.kind == "in"})
    plan = solve({item: rate}, book, inputs=set(inputs), allowed={machine})
    counts = "; ".join(f"{s.count} x {s.recipe} ({s.rate:.2f}/s)" for s in plan.steps)
    feeds = ", ".join(f"{p.item} at y={p.y}" for p in ref.ports if p.kind == "in")
    out = next(p for p in ref.ports if p.kind == "out")
    pole = power_row(ref, height)
    return (
        f"Design a main-bus module that makes {rate:.2f} {item} per second ({rate * 60:.0f} per minute) "
        f"inside the box x 0..{width}, y 0..{height} (tile units, y grows south). "
        f"The ratio is already worked out for {machine}: {counts}. Build at least these machines and "
        f"enough inserters to keep them busy ({inserter_rates(game)}). "
        f"Bus input belts already exist on the west edge (x=0) facing east into the box: {feeds}. "
        f"Continue each one from x=1. The product must leave on a belt ending at x=1, y={out.y} facing west, "
        f"into the existing belt at x=0, y={out.y} that returns to the bus. Place nothing else at x=0. "
        f"An inserter with direction 0 picks up from the tile north of it and drops south; 4 picks east and "
        f"drops west; 8 picks south and drops north; 12 picks west and drops east. Long-handed inserters "
        f"reach two tiles each way. A belt moves items the way it faces; a belt pointing into the side of "
        f"another puts its items on that belt's near lane. Underground belts come in pairs facing the way "
        f"items travel: the first one along a line is the entrance, the next is the exit, at most "
        f"{SPAN - 1} tiles between them. Power every inserter and machine: a small electric pole powers "
        f"the 5x5 area around it and wires to poles up to 7.5 tiles away; a pole on the network stands "
        f"at x=0.5, y={pole + 0.5}. Feed machines directly from one another where that saves belts, and "
        f"keep it tidy, as a good player would. Use one stage holding every entity, with an entity_built "
        f"witness on one machine.")


def module_site(kind: str, ref: Module, facts: Mapping[str, Any], recipes: list[dict], goal: str,
                tick: int = 1) -> dict:
    """An npc-site-v1-like export of an empty box with the port belts and a pole already built."""
    width, height = box(ref)
    built, unit = [], 1
    for p in ref.ports:
        built.append({"name": "transport-belt", "position": {"x": 0.5, "y": p.y + 0.5},
                      "direction": EAST if p.kind == "in" else WEST, "unit_number": unit})
        unit += 1
    built.append({"name": "small-electric-pole", "position": {"x": 0.5, "y": power_row(ref, height) + 0.5},
                  "direction": 0, "unit_number": unit, "powered": True})
    return {"schema": "npc-site-v1", "npc_id": 1, "request_id": f"module-{kind}", "tick": tick,
            "force": "player", "surface": "nauvis", "world_id": "module-lab", "mod_version": "lab",
            "game_version": "2.0.77", "goal": goal, "bounds": [0, 0, width, height],
            "prototypes": {k: v for k, v in facts.items() if k in PROTOTYPES}, "recipes": recipes,
            "researched": [], "inventory": {}, "resources": [], "obstacles": [], "water_rows": [],
            "built": built}


def pair_undergrounds(entities: list[Placed]) -> list[Placed]:
    """Mark each underground belt as an entrance or an exit, as the game pairs them when placed:
    along a line in the direction of travel, an entrance pairs with the next one within reach."""
    out = [e for e in entities if e.name != "underground-belt"]
    tunnels = [e for e in entities if e.name == "underground-belt"]
    dx = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
    paired: set[int] = set()
    for i, e in sorted(enumerate(tunnels), key=lambda ie: (ie[1].direction, ie[1].x * dx[ie[1].direction][0]
                                                          + ie[1].y * dx[ie[1].direction][1])):
        if i in paired:
            continue
        sx, sy = dx[e.direction]
        exit_ = next((j for j, f in enumerate(tunnels) if j != i and j not in paired and f.direction == e.direction
                      and (f.x - e.x) * sy == (f.y - e.y) * sx
                      and 0 < (f.x - e.x) * sx + (f.y - e.y) * sy <= SPAN), None)
        if exit_ is None:
            continue
        paired |= {i, exit_}
        f = tunnels[exit_]
        out.append(Placed(e.name, e.x, e.y, e.direction, kind="input"))
        out.append(Placed(f.name, f.x, f.y, f.direction, kind="output"))
    unpaired = [e for i, e in enumerate(tunnels) if i not in paired]
    if unpaired:
        raise DesignError(f"{len(unpaired)} underground belts without a partner")
    return out


def to_module(kind: str, plan: Mapping[str, Any], ref: Module) -> Module:
    m = Module(f"{kind.replace('_', '-')}")
    placed = [Placed(e["prototype"], float(e["position"]["x"]), float(e["position"]["y"]),
                     int(e.get("direction") or 0), e.get("recipe")) for e in plan["entities"]]
    m.entities = pair_undergrounds(placed)
    # The port belts on the west edge, and the pole the design wires to, belong to the module: the
    # bus taps end on the belts, and the bus pole line reaches the pole.
    for p in ref.ports:
        m.entities.append(Placed("transport-belt", p.x + 0.5, p.y + 0.5, EAST if p.kind == "in" else WEST))
    m.width, m.height = box(ref)
    m.entities.append(Placed("small-electric-pole", 0.5, power_row(ref, m.height) + 0.5))
    m.ports = [Port(p.item, p.kind, p.x, p.y, p.direction, p.rate) for p in ref.ports]
    m.makes = dict(ref.makes)
    return m


def check(module: Module, kind: str, rate: float, book: Book, machine: str) -> list[str]:
    """What is wrong with a design before it is worth a sandbox run."""
    problems = []
    seen: dict[tuple[int, int], Placed] = {}
    for e in module.entities:
        tiles = footprint([e])
        if any(not (0 <= x < module.width and 0 <= y < module.height) for x, y in tiles):
            problems.append(f"{e.name} at ({e.x}, {e.y}) is outside the box")
        for t in tiles:
            if t in seen:
                problems.append(f"{e.name} and {seen[t].name} overlap at {t}")
            seen[t] = e
    item = KIND_ITEM[kind]
    inputs = {p.item for p in module.ports if p.kind == "in"}
    plan = solve({item: rate}, book, inputs=inputs, allowed={machine})
    for step in plan.steps:
        have = sum(1 for e in module.entities if e.recipe == step.recipe)
        if have < step.count:
            problems.append(f"{have} machines on {step.recipe}, the ratio needs {step.count}")
    for p in module.ports:   # every port belt continues into the box (or, for the output, is fed from it)
        x, y = p.x + (1 if p.kind == "in" else 1), p.y
        if (x, y) not in seen:
            problems.append(f"nothing at ({x}, {y}) next to the {p.item} port")
    return problems[:12]


class ModuleDesigner:
    """Asks the LLM designer for one module; raises DesignError when nothing usable comes back."""

    def __init__(self, *, model: str = dp.DEFAULT_MODEL, output_tokens: int = 24000, deadline_s: float = 240,
                 transport: Callable | None = None, credential: Callable[[], str] | None = None,
                 log: Callable[[str], None] = print):
        self.config = dp.ResponsesConfig(model=model, max_output_tokens=output_tokens, timeout_s=deadline_s,
                                         total_deadline_s=deadline_s)
        self.transport = transport or dp.OpenAIHTTPTransport()
        self.credential = credential or (lambda: dp.validate_credential(dp.api_key_from_environment()))
        self.log = log
        self.usage: dict = {}

    def __call__(self, kind: str, rate: float, book: Book, facts: Mapping[str, Any], recipes: list[dict],
                 machine: str = modules.ASSEMBLER, game=None) -> Module:
        from designer_llm.providers import ResponsesProvider
        ref = reference(kind, rate, book)
        goal = brief(kind, rate, book, ref, machine, game)
        # Only the recipes this module makes keep the request small.
        inputs = {p.item for p in ref.ports if p.kind == "in"}
        wanted = {s.recipe for s in solve({KIND_ITEM[kind]: rate}, book, inputs=inputs, allowed={machine}).steps}
        site = module_site(kind, ref, facts, [r for r in recipes if r.get("name") in wanted], goal)
        context = dp.build_context(site)
        item = KIND_ITEM[kind]
        demand = [{"item": item, "metric": "rate_items_per_second", "amount": rate, "window_ticks": 3600}]
        request = dp.make_request(context, goal, request_id=f"module-{kind}", demand=demand,
                                  max_entities=128, max_stages=1)
        provider = ResponsesProvider(self.config, self.transport, self.credential)
        outcome = dp.propose(request, context, provider, max_input_bytes=120_000)
        self.usage = outcome.evidence.get("provider", {}).get("metadata", {})
        ledger.record("module designer", self.config.model, input_tokens=self.usage.get("input_tokens"),
                      output_tokens=self.usage.get("output_tokens"), latency_ms=self.usage.get("latency_ms"),
                      ok=outcome.accepted is not None, purpose=f"{kind} {rate:.2f}/s")
        self.log(f"designer module {kind} {rate:.2f}/s: {outcome.status} ({outcome.reason}); tokens in/out "
                 f"{self.usage.get('input_tokens')}/{self.usage.get('output_tokens')}, {self.usage.get('latency_ms')} ms")
        if not outcome.accepted:
            detail = outcome.evidence.get("validation") or outcome.evidence.get("reason")
            raise DesignError(f"designer {outcome.reason}: {json.dumps(detail)[:400] if detail else ''}")
        module = to_module(kind, dp.to_plan(outcome.accepted.document), ref)
        problems = check(module, kind, rate, book, machine)
        if problems:
            raise DesignError("; ".join(problems))
        return module
