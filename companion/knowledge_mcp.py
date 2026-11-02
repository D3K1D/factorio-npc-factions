"""The designer's lookup tool: a read-only MCP server (stdio, newline-delimited JSON-RPC) that Codex starts
for a design run, for facts the brief did not carry.

It reads one file, written by the companion into that run's empty folder: the game's facts, the recipes
the engineer can make and its factory ledger's budgets. It has no game connection and writes nothing;
players' chat never reaches the designer, and this server could not act on it if it did. It answers at
most `--limit` lookups a run (each one is another model turn re-reading the whole request, so they are
dear), then tells the model to answer with what it has.

    python knowledge_mcp.py knowledge.json --limit 3
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion import rates  # noqa: E402

TOOLS = [
    {"name": "recipe", "description": "A recipe that makes an item: time, ingredients, products, and items a second "
     "in each machine that can make it.", "inputSchema": {"type": "object", "properties": {
         "item": {"type": "string"}}, "required": ["item"]}},
    {"name": "machine", "description": "A building: crafting or mining speed, fuel burn or electric draw, module "
     "slots, size; for inserters and belts, items a second.", "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}}, "required": ["name"]}},
    {"name": "rate", "description": "How many machines make an item at a rate (items a second), what they take a "
     "second and draw.", "inputSchema": {"type": "object", "properties": {
         "item": {"type": "string"}, "per_second": {"type": "number"}, "machine": {"type": "string"}},
         "required": ["item", "per_second"]}},
    {"name": "steam", "description": "How a steam power plant fits together: pumps, boilers, engines, fuel.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "supply", "description": "The engineer's factory: what it makes of an item a second, what its blocks "
     "use on average and at peak, and what is left over.", "inputSchema": {"type": "object", "properties": {
         "item": {"type": "string"}}, "required": ["item"]}},
]
NAMES = [t["name"] for t in TOOLS]
MAX_ANSWER = 2000   # characters a lookup returns at most


class Knowledge:
    def __init__(self, data: Mapping[str, Any], limit: int = 3):
        self.facts = rates.Facts(data.get("facts") or {})
        self.recipes = [r for r in data.get("recipes") or [] if isinstance(r, Mapping)]
        self.budgets = {b["item"]: b for b in data.get("ledger") or [] if isinstance(b, Mapping) and b.get("item")}
        self.limit, self.used = limit, 0

    def _recipes_for(self, item: str) -> list[Mapping]:
        found = [r for r in self.recipes if any(p.get("name") == item for p in r.get("products") or [])]
        return sorted(found, key=lambda r: (r.get("name") != item, len(r.get("products") or []), str(r.get("name"))))

    def recipe(self, item: str) -> str:
        found = self._recipes_for(item)
        if not found:
            return f"No recipe the engineer can make produces {item}."
        lines = []
        for r in found[:3]:
            ins = ", ".join(f"{i.get('amount')} {i.get('name')}" for i in r.get("ingredients") or [])
            outs = ", ".join(f"{p.get('amount')} {p.get('name')}" for p in r.get("products") or [])
            made = "; ".join(f"{m} {v:g} a second" for m, v in self.facts.per_machine(r))
            lines.append(f"{r.get('name')}: {r.get('energy')} s, {ins} -> {outs}" + (f"; made in {made}" if made else ""))
        return "\n".join(lines)

    def machine(self, name: str) -> str:
        e = self.facts.entities.get(name)
        if not e:
            return f"No building called {name} in this game."
        parts = [f"{name}: {e.get('kind')}, {e.get('width')}x{e.get('height')} tiles",
                 "buildable now" if e.get("ready") else "not buildable yet"]
        for key, label in (("speed", "speed"), ("modules", "module slots"), ("productivity", "base productivity"),
                           ("max_kw", "gives up to kW"), ("per_second", "pumps a second")):
            if e.get(key):
                parts.append(f"{label} {e[key]:g}")
        burn = self.facts.fuel_per_second(name)
        if burn:
            parts.append(f"burns {burn:g} {rates.FUEL} a second")
        draw = self.facts.kw(name)
        if draw:
            parts.append(f"draws {draw:g} kW")
        moved = self.facts.inserter_rate(name) or self.facts.belt_rate(name)
        if moved:
            parts.append(f"moves {moved:g} items a second")
        return ", ".join(parts) + "."

    def rate(self, item: str, per_second: float, machine: str | None = None) -> str:
        found = self._recipes_for(item)
        if not found:
            return f"No recipe the engineer can make produces {item}."
        r = found[0]
        made = dict(self.facts.per_machine(r, limit=8))
        if machine and machine not in made:
            return f"{machine} cannot make {r.get('name')}; it is made in: {', '.join(made) or 'nothing buildable'}."
        name = machine or next(iter(made), None)
        if not name or not made.get(name):
            return f"Nothing in this game makes {r.get('name')} at a known rate."
        count = per_second / made[name]
        crafts = per_second / float((r.get("products") or [{}])[0].get("amount") or 1)
        takes = ", ".join(f"{crafts * float(i.get('amount') or 0):.3g} {i.get('name')}" for i in r.get("ingredients") or [])
        out = (f"{per_second:g} {item} a second: {count:.2f} {name} (build {math.ceil(count - 1e-9)}), "
               f"taking {takes} a second")
        burn, draw = self.facts.fuel_per_second(name), self.facts.kw(name)
        if burn:
            out += f", burning {burn * count:.3g} {rates.FUEL} a second"
        if draw:
            out += f", drawing {draw * count:.0f} kW"
        return out + "."

    def steam(self) -> str:
        plant = self.facts.steam()
        if not plant:
            return "This game has no steam plant the facts describe."
        text = (f"A {plant['boiler']} makes {plant['steam_per_boiler']:g} steam a second for "
                f"{plant['engines_per_boiler']:g} {plant['engine']} ({plant['engine_kw']:g} kW each)")
        if plant.get("boiler_fuel"):
            text += f", burning {plant['boiler_fuel']:g} {rates.FUEL} a second"
        if plant.get("boilers_per_pump"):
            text += f"; one {plant['pump']} feeds {plant['boilers_per_pump']:g} boilers"
        return text + "."

    def supply(self, item: str) -> str:
        b = self.budgets.get(item)
        if not b:
            return f"The engineer's factory makes no {item} that it has measured or planned."
        return (f"{item}: supply {b.get('supply', 0):.3g} a second, used {b.get('average', 0):.3g} on average and "
                f"{b.get('peak', 0):.3g} at peak, {b.get('made', 0):.3g} measured made; "
                f"{b.get('headroom', 0):.3g} a second left over.")

    def call(self, name: str, args: Mapping[str, Any]) -> tuple[str, bool]:
        """A lookup's answer and whether it is an error."""
        if self.used >= self.limit:
            return f"Lookup limit reached ({self.limit} a design): answer with what you have.", True
        self.used += 1
        try:
            if name == "recipe":
                text = self.recipe(str(args.get("item")))
            elif name == "machine":
                text = self.machine(str(args.get("name")))
            elif name == "rate":
                text = self.rate(str(args.get("item")), float(args.get("per_second") or 0),
                                 str(args["machine"]) if args.get("machine") else None)
            elif name == "steam":
                text = self.steam()
            elif name == "supply":
                text = self.supply(str(args.get("item")))
            else:
                return f"No tool {name}.", True
        except (TypeError, ValueError, KeyError) as exc:
            return f"Bad lookup: {exc}", True
        return text[:MAX_ANSWER], False


def handle(message: Mapping[str, Any], knowledge: Knowledge) -> dict | None:
    """One JSON-RPC message's reply (None for a notification)."""
    method, mid = message.get("method"), message.get("id")
    if mid is None:
        return None
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "factorio", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        text, error = knowledge.call(str(params.get("name")), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": error}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 3
    knowledge = Knowledge(json.loads(path.read_text(encoding="utf-8")), limit)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = handle(message, knowledge) if isinstance(message, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


def knowledge_for(site: Mapping[str, Any]) -> dict | None:
    """What the server reads for a design: the game's facts, the recipes it can make, its ledger's budgets.
    None without the game's facts (then there is nothing worth looking up)."""
    facts = site.get("game_facts")
    if not isinstance(facts, Mapping) or not facts.get("entities"):
        return None
    from companion import ledger
    book = ledger.Ledger.from_site(site)
    budgets = []
    for item in book.items():
        b = book.budget(item)
        budgets.append({"item": item, "supply": round(b.supply, 4), "average": round(b.average, 4),
                        "peak": round(b.peak, 4), "made": round(b.made, 4), "headroom": round(b.headroom, 4)})
    keys = ("name", "category", "categories", "energy", "ingredients", "products")
    recipes = [{k: r.get(k) for k in keys if r.get(k) is not None} for r in site.get("recipes") or []
               if isinstance(r, Mapping)]
    return {"facts": facts, "recipes": recipes, "ledger": budgets}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
