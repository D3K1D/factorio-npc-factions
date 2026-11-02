"""Ready-made blueprints in the layout library (F-16): the user's blueprint packs (strings of blueprints and books, one
a .txt file in the Blueprints folder) imported as layouts the engineer reuses as it reuses its own, so it places a good
standard blueprint as a player would rather than always designing one.

The class: the engineer reused only layouts it had designed or been taught, so every new kind of build waited on the
designer, while players keep books of proven blueprints. Its variants: a blueprint or a book, books within books,
strings from 1.1 (8-way directions) and from 2.x (16-way), a mod's buildings (bobinserters' pickup and drop settings,
undergrounds, splitters with priorities), buildings this mod set lacks, items not researched yet, tiles and rails,
furnace rows with no recipe to tell what they are for.

    decode      a string: its version character, then base64, then zlib, then JSON (BlueprintError when not one)
    blueprints  every blueprint of a string, books walked to any depth, with where it came from (book labels)
    convert     one blueprint as a plan the library takes (blueprints.from_plan): building centres, directions as 2.x
                has them, recipes, underground and loader sides, inserter pickup and drop, splitter settings; tiles,
                rails and buildings of no known size are left out and said
    annotate    once a blueprint (cached by its content): what it makes (its recipes' end products), what it is for
                (its label, its products, its drills: the rules; the designer asked only when they cannot tell, its
                answer kept so it is never asked again), its ports (belts and pipes at its edge, with the item where
                its recipes say), its footprint and rate (blueprints.from_plan, build_history.job_of)
    sync        every pack in the folder, against the user's mod set (the game's facts: a building it lacks rejects
                the blueprint, said with the reason); imported ones go into the library marked "import:<file>/<book>/
                <label>", found by purpose or by what they make like a saved layout (build_choice.peers), locked until
                research allows what they are built of (build_choice's parts check, blueprints.locked_items)

What a pack gave is kept in the data folder's imports/ (one record a string, by its hash): nothing is imported, judged
or asked twice; a new mod set judges the same strings again, their annotations kept.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import zlib
from collections import deque
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

MAX_BYTES = 64 * 1024 * 1024    # (a decompressed string larger than this is not a blueprint pack: the user's first
#                                  book of books was over 8 MB)
MAX_DEPTH = 12                  # (books within books)
SKIPPED_TYPES = ("rail", "curved-rail", "straight-rail", "half-diagonal-rail", "curved-rail-a", "curved-rail-b",
                 "rail-ramp", "elevated-straight-rail", "elevated-curved-rail-a", "elevated-curved-rail-b",
                 "elevated-half-diagonal-rail", "rail-support", "rail-signal", "rail-chain-signal", "train-stop",
                 "locomotive", "cargo-wagon", "fluid-wagon", "artillery-wagon")
KEPT_SETTINGS = ("mirror", "pickup_position", "drop_position", "input_priority", "output_priority", "filter", "filters",
                 "use_filters")
# Optional parts, by role (books like autosaved.org's put roboports in every build, beacons and modules in most, mark
# their flow with filtered inserters, and use provider chests wherever a chest stands): what only serves a logistic
# network, a beacon or a module, or marks something, is left out when the engineer builds it, said, and the build's
# rate is reckoned without it; a logistic chest that is part of the flow becomes a plain chest.
OPTIONAL_TYPES = {"roboport": "roboports", "beacon": "beacons (it runs slower without them)",
                  "lamp": "lamps (indicators)", "programmable-speaker": "speakers"}
COMBINATORS = ("constant-combinator", "arithmetic-combinator", "decider-combinator", "selector-combinator")
PLAIN_CHESTS = ("wooden-chest", "iron-chest", "steel-chest")
EDGE_KINDS = {"belt": "belt", "underground": "belt", "splitter": "belt", "loader": "belt", "pipe": "fluid"}
# The kinds of the game's facts as entity types (the library's from_plan reads drills and pumps by type).
TYPES = {"crafter": "assembling-machine", "drill": "mining-drill", "pump": "offshore-pump", "lab": "lab", "belt": "transport-belt",
         "underground": "underground-belt", "splitter": "splitter", "inserter": "inserter", "pole": "electric-pole",
         "chest": "container", "wall": "wall", "turret": "ammo-turret"}
# What the game builds from a plan (designed_start.lua's PLACEABLE; a test keeps the two the same): a blueprint with
# anything else is rejected, said, rather than failing in the game.
PLACEABLE = frozenset({"furnace", "mining-drill", "container", "assembling-machine", "rocket-silo", "splitter",
                       "underground-belt", "loader", "transport-belt", "inserter", "electric-pole", "boiler",
                       "generator", "lab", "pipe", "offshore-pump", "solar-panel", "pipe-to-ground", "ammo-turret",
                       "wall", "gate", "radar", "electric-turret", "fluid-turret", "artillery-turret", "land-mine",
                       "roboport", "logistic-container", "reactor", "heat-pipe", "storage-tank"})
ASK_PER_MINUTE = 4      # the designer asked about at most this many blueprints a minute, all engineers together
ASK_PER_SYNC = 1        # (and one a judging pass: the rest on later requests)
# (a 20-minute rehearsal of 7 engineers made 198 calls, 1.1 M tokens, at 2 a request each, every run asking afresh:
# rehearse-death-7h, F-16c); its answers are kept with the packs (<file>.annotations.json), so asked once, ever
ASK_SECONDS = 30.0      # (each answer within this)
DRILL_RADIUS = 2.49     # (a drill whose mining area the facts do not give: an electric drill's)
ASK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["purpose", "products", "why"],
              "properties": {"purpose": {"type": "string"}, "products": {"type": "array", "items": {"type": "string"}},
                             "why": {"type": "string"}}}


class BlueprintError(ValueError):
    pass


def decode(text: str) -> dict:
    """A blueprint string's JSON: its version character, then base64 of zlib-compressed JSON."""
    s = "".join(str(text).split())
    if len(s) < 2:
        raise BlueprintError("empty")
    try:
        raw = base64.b64decode(s[1:], validate=False)
        z = zlib.decompressobj()
        data = z.decompress(raw, MAX_BYTES)
        if z.unconsumed_tail:
            raise BlueprintError(f"larger than {MAX_BYTES // (1024 * 1024)} MB")
        obj = json.loads(data.decode("utf-8"))
    except BlueprintError:
        raise
    except Exception as exc:
        raise BlueprintError(f"not a blueprint string ({type(exc).__name__})") from exc
    if not isinstance(obj, Mapping):
        raise BlueprintError("not a blueprint string (no object)")
    return dict(obj)


def strings(text: str) -> list[tuple[int, str]]:
    """The blueprint strings in a file's text: (its line, the string), one a line; blank lines and any other text
    between them (a label line before each) passed over."""
    out = []
    for n, line in enumerate(str(text).splitlines(), start=1):
        s = line.strip()
        if len(s) >= 20 and s[0] == "0" and re.fullmatch(r"[A-Za-z0-9+/=]+", s[1:] or "x"):
            out.append((n, s))
    return out


def encode(obj: Mapping[str, Any]) -> str:
    """A blueprint string of `obj` (the game's format: "0", base64 of zlib-compressed JSON)."""
    return "0" + base64.b64encode(zlib.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), 9)).decode()


def blueprints(obj: Mapping[str, Any], path: tuple[str, ...] = (), depth: int = 0
               ) -> Iterator[tuple[tuple[str, ...], dict]]:
    """Every blueprint in a decoded string with the labels of the books it is in, books walked to MAX_DEPTH;
    deconstruction and upgrade planners are passed over."""
    if depth > MAX_DEPTH:
        return
    if isinstance(obj.get("blueprint"), Mapping):
        yield path, dict(obj["blueprint"])
    book = obj.get("blueprint_book")
    if isinstance(book, Mapping):
        label = str(book.get("label") or "book")
        for slot in book.get("blueprints") or []:
            if isinstance(slot, Mapping):
                yield from blueprints(slot, path + (label,), depth + 1)


def major(version: Any) -> int:
    """The game's major version a string was made in (its version number's top 16 bits)."""
    try:
        return int(version) >> 48
    except (TypeError, ValueError):
        return 2


def direction(d: Any, version: Any) -> int:
    """A building's direction as 2.x has it (16 ways): 1.1's eight ways doubled."""
    d = int(d or 0)
    return (d * 2) % 16 if major(version) < 2 else d % 16


def facts_prototypes(site: Mapping[str, Any]) -> dict[str, dict]:
    """The buildings of this mod set with their size: the site's own prototypes over the game's facts."""
    out: dict[str, dict] = {}
    for name, e in ((site.get("game_facts") or {}).get("entities") or {}).items():
        if isinstance(e, Mapping):
            out[name] = {"type": str(e.get("type") or TYPES.get(str(e.get("kind")), str(e.get("kind") or ""))),
                         "width": int(e.get("width") or 1), "height": int(e.get("height") or 1),
                         "placement_item": name,
                         **({"mining_area_radius": float(e["radius"])} if e.get("radius") else {})}
    for name, p in (site.get("prototypes") or {}).items():
        if isinstance(p, Mapping):
            out[name] = {**out.get(name, {}), **dict(p)}
    return out


def convert(blueprint: Mapping[str, Any], prototypes: Mapping[str, Any], *,
            network: bool = False) -> tuple[dict, list[str], list[str]]:
    """(the blueprint as a plan's entities, what was left out and why, the buildings this mod set lacks). `network`:
    the engineer's base has a logistic network, so roboports, logistic chests (their requests and limits with them)
    and the robot network's stores are kept, not left out or made plain (Ledger's L-3; default off)."""
    version = blueprint.get("version")
    entities, skipped, missing = [], [], []
    optional = Counter()
    # (markers by their role: a filtered inserter at a logistic chest marks what the chest is for, as in autosaved.org's
    # books; a filter elsewhere is part of the flow and kept; combinators beside lamps only light them)
    markers = [(float((e.get("position") or {}).get("x") or 0), float((e.get("position") or {}).get("y") or 0))
               for e in blueprint.get("entities") or [] if isinstance(e, Mapping)
               and any(w in str(e.get("name")) for w in ("provider-chest", "requester-chest", "buffer-chest"))]
    lamps = [(float((e.get("position") or {}).get("x") or 0), float((e.get("position") or {}).get("y") or 0))
             for e in blueprint.get("entities") or [] if isinstance(e, Mapping)
             and str((prototypes.get(str(e.get("name"))) or {}).get("type")) == "lamp"]
    if blueprint.get("tiles"):
        skipped.append(f"{len(blueprint['tiles'])} tiles (not placed yet)")
    rails = Counter()
    for n, e in enumerate(blueprint.get("entities") or [], start=1):
        if not isinstance(e, Mapping) or not e.get("name"):
            continue
        name = str(e["name"])
        if name in SKIPPED_TYPES or (prototypes.get(name) or {}).get("type") in SKIPPED_TYPES:
            rails[name] += 1
            continue
        role = _optional(e, prototypes, markers, lamps)
        if network and role in ("chest", "robot network stores", OPTIONAL_TYPES["roboport"]) and name in prototypes:
            role = None   # (the logistic network is the point: kept as the book has them)
        if role == "chest":
            plain = next((c for c in PLAIN_CHESTS if c in prototypes), None)
            if plain:
                optional[f"logistic chests as {plain}"] += 1
                e = {**e, "name": plain}
                name = plain
        elif role:
            optional[role] += 1
            continue
        if name not in prototypes:
            missing.append(name)
            continue
        ptype = str((prototypes.get(name) or {}).get("type") or "")
        if ptype and ptype not in PLACEABLE:
            missing.append(f"{name} (the engineer does not build a {ptype})")
            continue
        if int(e.get("direction") or 0) % (2 if major(version) < 2 else 4):
            missing.append(f"{name} (set diagonally)")
            continue
        pos = e.get("position") or {}
        out = {"id": f"bp-{n}", "prototype": name, "direction": direction(e.get("direction"), version),
               "position": {"x": float(pos.get("x") or 0), "y": float(pos.get("y") or 0)}}
        if e.get("recipe"):
            out["recipe"] = str(e["recipe"])
        if e.get("type") in ("input", "output"):
            out["belt_to_ground_type"] = e["type"]
        for key in KEPT_SETTINGS + (NETWORK_SETTINGS if network else ()):
            if e.get(key) is not None:
                out[key] = e[key]
        entities.append(out)
    if rails:
        skipped.append(", ".join(f"{k} x{v}" for k, v in sorted(rails.items())) + " (rails and trains not placed yet)")
    if optional:
        skipped.append("optional parts left out: " + ", ".join(f"{k} x{v}" for k, v in sorted(optional.items())))
    if any(isinstance(e, Mapping) and e.get("items") for e in blueprint.get("entities") or []):
        skipped.append("modules left out (it runs slower without them)")
    return {"entities": entities}, skipped, sorted(set(missing))


LOGISTIC_ONLY = ("storage-chest", "buffer-chest")   # (a robot network's stores: not in a build's flow)
NETWORK_SETTINGS = ("request_filters", "bar", "request_from_buffers")   # (kept in network mode: a requester's asks)


def _optional(e: Mapping[str, Any], prototypes: Mapping[str, Any], markers: list | None = None,
              lamps: list | None = None) -> str | None:
    """A part's optional role, or None: "chest" for a logistic chest in the flow (a plain chest instead); the role
    left out for a roboport, beacon, a robot network's own store, or a filtered inserter (a marker in such books)."""
    name = str(e.get("name") or "")
    ptype = str((prototypes.get(name) or {}).get("type") or "")
    if ptype in OPTIONAL_TYPES or name in OPTIONAL_TYPES:
        return OPTIONAL_TYPES.get(ptype) or OPTIONAL_TYPES[name]
    if ptype == "logistic-container" or name.endswith("-chest") and any(
            w in name for w in ("provider", "requester", "storage", "buffer")):
        return "robot network stores" if any(w in name for w in LOGISTIC_ONLY) else "chest"
    if (ptype == "inserter" or name.endswith("inserter")) and (e.get("use_filters") or e.get("filters")) \
            and _near(e, markers or []):
        return "filtered inserters (markers at a logistic chest)"
    if ptype in COMBINATORS and _near(e, lamps or [], 2.01):
        return "combinators (indicators, beside its lamps)"
    return None


def _near(e: Mapping[str, Any], spots: list[tuple[float, float]], reach: float = 1.01) -> bool:
    p = e.get("position") or {}
    x, y = float(p.get("x") or 0), float(p.get("y") or 0)
    return any(abs(x - a) <= reach and abs(y - b) <= reach for a, b in spots)


def _recipes(site: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The recipes the site knows (those researched: a blueprint set to another waits for its research)."""
    return {str(r["name"]): r for r in site.get("recipes") or [] if isinstance(r, Mapping) and r.get("name")}


def _known(site: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Every recipe the game's facts list for this force, researched or not, as site recipe rows."""
    out = {}
    for name, r in ((site.get("game_facts") or {}).get("recipes") or {}).items():
        if isinstance(r, Mapping):
            out[str(name)] = {"name": str(name), **{k: v for k, v in r.items() if k != "researched"}}
    return out


def products_of(entities: list[dict], site: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(its end products: what its recipes make that none of them takes; what it takes from outside)."""
    book = _recipes(site)
    made, taken = Counter(), set()
    for e in entities:
        r = book.get(str(e.get("recipe") or ""))
        if not r:
            continue
        for p in r.get("products") or []:
            if isinstance(p, Mapping) and p.get("name"):
                made[str(p["name"])] += 1
        taken |= {str(i["name"]) for i in r.get("ingredients") or [] if isinstance(i, Mapping) and i.get("name")}
    return sorted(i for i in made if i not in taken), sorted(i for i in taken if i not in made)


def _traced(build: Mapping[str, Any], site: Mapping[str, Any]) -> dict | None:
    """Its ports read from its own belts (book_blocks.trace), when that finds an input it can name; else None."""
    from companion import book_blocks
    try:
        traced = book_blocks.trace(list(build.get("entities") or []), site)
    except Exception:
        return None   # (a block the trace cannot read: its edge belts, as before)
    return traced if any(p["way"] == "in" and p["item"] for p in traced["ports"]) else None


def ports(build: Mapping[str, Any], prototypes: Mapping[str, Any], site: Mapping[str, Any]) -> list[dict]:
    """Belts and pipes at its edge: (x, y, kind belt or fluid, way in or out, the item where its recipes say)."""
    w, h = (build.get("size") or [0, 0])[:2]
    finals, inputs = products_of(list(build.get("entities") or []), site)
    facts = (site.get("game_facts") or {}).get("entities") or {}
    out = []
    step = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
    for e in build.get("entities") or []:
        kind = EDGE_KINDS.get(str((facts.get(e["prototype"]) or {}).get("kind") or ""))
        if not kind:
            continue
        x, y = float(e["position"]["x"]), float(e["position"]["y"])
        edge = [s for s, hit in (("north", y < 1), ("south", y > h - 1), ("west", x < 1), ("east", x > w - 1)) if hit]
        if not edge:
            continue
        way = None
        if kind == "belt":
            dx, dy = step.get(int(e.get("direction") or 0) // 4 * 4, (0, 0))
            leaving = (dy < 0 and "north" in edge) or (dy > 0 and "south" in edge) or (dx < 0 and "west" in edge) \
                or (dx > 0 and "east" in edge)
            way = "out" if leaving else "in"
        items = finals if way == "out" else inputs if way == "in" else []
        out.append({"x": x, "y": y, "edge": edge[0], "kind": kind, "way": way,
                    "item": items[0] if len(items) == 1 else None})
    return out


def sidecar(folder: Path | None, file: str) -> dict | None:
    """A pack's ports sidecar, when it has one: `<pack>.ports.json` beside `<pack>.txt` (the optimizer writes one per
    block: {"box": {w, h}, "inputs": [...], "outputs": [...]}, each port {item, rate, fluid, edge, x, y}, its tile
    just outside the box). It says what the edge guess cannot: which way a pipe's fluid goes, every input item."""
    if folder is None:
        return None
    path = Path(folder) / f"{Path(file).stem}.ports.json"
    try:
        side = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return side if isinstance(side, Mapping) and isinstance(side.get("box"), Mapping) else None


def heading(text: str, line: int) -> str | None:
    """The label line just before a string's line (the optimizer pack's "optimizer: <job>"), if there is one."""
    lines = str(text).splitlines()
    for k in range(line - 2, -1, -1):
        s = lines[k].strip()
        if s:
            return None if strings(s) else s
    return None


def sidecar_named(folder: Path | None, name: str | None) -> dict | None:
    """The ports sidecar a label names: `<name>.ports.json` in the pack folder or a folder in it, for the label line
    itself or what follows its last colon ("optimizer: oil_plastic_sulfur" names oil_plastic_sulfur.ports.json)."""
    if folder is None or not name:
        return None
    names = [n for n in dict.fromkeys((name.strip(), name.rsplit(":", 1)[-1].strip())) if n and "/" not in n and "\\" not in n]
    for n in names:
        for path in sorted(Path(folder).glob(f"{n}.ports.json")) + sorted(Path(folder).glob(f"*/{n}.ports.json")):
            try:
                side = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(side, Mapping) and isinstance(side.get("box"), Mapping):
                return side
    return None


def sidecar_ports(side: Mapping[str, Any]) -> list[dict]:
    """The sidecar's ports as ports() gives them (x, y the edge tile's centre inside the box, edge, kind belt or
    fluid, way in or out, item), with each one's rate and the tile outside where its belt or pipe joins."""
    w, h = int(side["box"].get("w") or 0), int(side["box"].get("h") or 0)
    out = []
    for way, rows in (("in", side.get("inputs")), ("out", side.get("outputs"))):
        for row in rows or ():
            if not isinstance(row, Mapping) or not isinstance(row.get("x"), (int, float))                     or not isinstance(row.get("y"), (int, float)):
                continue
            x, y = int(row["x"]), int(row["y"])
            inside = (min(max(x, 0), max(w - 1, 0)) + 0.5, min(max(y, 0), max(h - 1, 0)) + 0.5)
            out.append({"x": inside[0], "y": inside[1], "edge": row.get("edge"),
                        "kind": "fluid" if row.get("fluid") else "belt", "way": way,
                        "item": row.get("item") or row.get("name"), "rate": row.get("rate"), "outside": [x, y]})
    return out


def _shifted(rows: list[dict], before: list[dict], after: list[dict]) -> list[dict]:
    """Ports given in the blueprint's own tiles, moved as the library moved its entities (to start at the origin)."""
    def corner(entities):
        xs = [float(e["position"]["x"]) for e in entities]
        ys = [float(e["position"]["y"]) for e in entities]
        return (min(xs), min(ys)) if xs else (0.0, 0.0)
    (bx, by), (ax, ay) = corner(before), corner(after)
    dx, dy = round(ax - bx), round(ay - by)
    return [{**r, "x": r["x"] + dx, "y": r["y"] + dy,
             **({"outside": [r["outside"][0] + dx, r["outside"][1] + dy]} if r.get("outside") else {})} for r in rows]


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def purpose_by_rules(label: str, entities: list[dict], prototypes: Mapping[str, Any], finals: list[str],
                     known: set[str], site: Mapping[str, Any] | None = None) -> str | None:
    """What it is for, where the rules can tell: a goal named in its label; its end products (science packs by their
    goal; else what it makes, "import:<items>", found by what it makes); drills by the taught rules."""
    from companion import taught
    low = label.lower()
    for purpose in sorted(known, key=len, reverse=True):
        if purpose in low:
            return purpose
    if finals:
        for item in finals:
            if item in taught.SCIENCE:
                return taught.SCIENCE[item]
        return "import:" + "+".join(finals[:3])
    kinds = [{"kind": (prototypes.get(e["prototype"]) or {}).get("type")} for e in entities]
    if any(k["kind"] == "mining-drill" for k in kinds):
        # (its ore named: as taught layouts are read; else mining for whichever patch it is put on, no one ore's goal)
        return taught.purpose_of({"label": label, "entities": kinds}, known) if ore_of(label, site or {}) \
            else "import:mining"
    named = named_items(label, entities, prototypes, site or {})
    if named:
        return "import:" + "+".join(named[:3])
    return None


def named_items(label: str, entities: list[dict], prototypes: Mapping[str, Any], site: Mapping[str, Any]) -> list[str]:
    """What rows with no recipe set make, by their label: its words naming an item a recipe their machines could run
    makes or takes ("Stone Smelter": stone into bricks; "Iron smelting": iron plates). The rows' crafting kind decides
    which recipes count (furnaces: smelting)."""
    kinds = {(prototypes.get(e["prototype"]) or {}).get("type") for e in entities if not e.get("recipe")}
    categories = {"smelting"} if "furnace" in kinds else set()
    if not categories:
        return []
    words = set(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())
    out = []
    for r in (site.get("recipes") or []):
        if not isinstance(r, Mapping) or (r.get("category") or "crafting") not in categories:
            continue
        items = [str(p["name"]) for p in r.get("products") or [] if isinstance(p, Mapping) and p.get("name")]
        takes = [str(i["name"]) for i in r.get("ingredients") or [] if isinstance(i, Mapping) and i.get("name")]
        if any(set(n.replace("-", " ").split()) - {"ore", "plate"} <= words for n in items + takes):
            out += [i for i in items if i not in out]
    return out


def ore_of(label: str, site: Mapping[str, Any]) -> str | None:
    """The resource a blueprint's drills are for, from its label (its words against the map's resources and the
    usual ores): a drill blueprint names what it mines, or cannot be placed."""
    names = {str(r.get("resource")) for r in site.get("resources") or [] if isinstance(r, Mapping) and r.get("resource")}
    names |= {"iron-ore", "copper-ore", "coal", "stone", "uranium-ore"}
    low = " " + re.sub(r"[^a-z0-9]+", " ", label.lower()) + " "
    for name in sorted(names, key=len, reverse=True):
        word = name.replace("-ore", "").replace("-", " ")
        if word and f" {word} " in low:   # (whole words: "charcoal" is no coal)
            return name
    return None


def ore_site(entities: list[dict], prototypes: Mapping[str, Any], resource: str) -> dict:
    """The ground its drills stand on, in its own tiles: each drill's centre tile on the ore (a drill there always
    mines; its whole body or mining area asked for, one at a patch's edge never fit), recorded by from_plan."""
    import math
    rows: dict[int, set[int]] = {}
    for e in entities:
        p = prototypes.get(e["prototype"]) or {}
        if p.get("type") != "mining-drill":
            continue
        tx, ty = math.floor(e["position"]["x"]), math.floor(e["position"]["y"])
        rows.setdefault(ty, set()).add(tx)
    return {"resources": [{"resource": resource, "rows": [[y, x, x] for y, xs in rows.items() for x in sorted(xs)]}]}


TIER_WORDS = {"am1": 1, "am2": 2, "am3": 3, "yellow": 1, "red": 2, "blue": 3, "green": 4, "turbo": 4,
              "basic": 1, "fast": 2, "express": 3, "mk1": 1, "mk2": 2, "mk3": 3}


def variant_of(label: str) -> tuple[str, int]:
    """(the design a blueprint is a variant of, its tier): its label without the words that only name the assembler or
    belt tier ("Logistic Science AM2", "Gears In-Out (red)"); the selection takes the best variant that can be made
    now (the parts check locks the rest) and a better one once it can."""
    words = re.sub(r"[^a-z0-9]+", " ", label.lower()).split()
    tiers = [TIER_WORDS[w] for w in words if w in TIER_WORDS and not (w in ("red", "green", "blue") and
                                                                       "science" in words)]
    rest = [w for w in words if w not in TIER_WORDS or (w in ("red", "green", "blue") and "science" in words)]
    return " ".join(rest), max(tiers, default=0)


_ASKED: deque = deque()     # (when the designer was asked lately, all engineers' planners in this process)
_LOCK = threading.Lock()
_SYNC = threading.Lock()    # (one engineer's planner judges the packs at a time: no blueprint asked about twice at
#                            once, no import record written over by another)


def may_ask(now: float | None = None) -> bool:
    """A turn to ask the designer about a blueprint: fewer than ASK_PER_MINUTE asked in the last minute (taken)."""
    now = time.monotonic() if now is None else now
    with _LOCK:
        while _ASKED and now - _ASKED[0] > 60:
            _ASKED.popleft()
        if len(_ASKED) >= ASK_PER_MINUTE:
            return False
        _ASKED.append(now)
        return True


def answers_path(folder: Path, file: str) -> Path:
    """Where the designer's answers about a pack's blueprints are kept: beside the pack (<file>.annotations.json),
    so every run and every engineer reads them."""
    return Path(folder) / f"{Path(file).stem}.annotations.json"


def answers(folder: Path | None, file: str) -> dict:
    """The designer's answers about a pack's blueprints, by the blueprint's content (as placed: its buildings)."""
    if folder is None:
        return {}
    path = answers_path(folder, file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in (data.get("answers") or {}).items() if isinstance(v, Mapping)}


def all_answers(folder: Path | None) -> dict:
    """Every pack's kept answers in the folder, by content: a blueprint moved to another pack, or kept in two, is
    asked about once."""
    out: dict = {}
    for path in sorted(Path(folder).glob("*.annotations.json")) if folder is not None else []:
        out.update(answers(folder, path.name[:-len(".annotations.json")] + ".txt"))
    return out


def keep_answer(folder: Path | None, file: str, key: str, note: Mapping[str, Any]) -> None:
    """One more answer kept beside the pack (read, added to, written whole; one writer at a time in this process)."""
    if folder is None:
        return
    path = answers_path(folder, file)
    with _LOCK:
        kept = answers(folder, file)
        kept[key] = dict(note)
        tmp = path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"file": file, "answers": kept}, indent=1), encoding="utf-8")
        os.replace(tmp, path)


def descriptions(folder: Path) -> list[dict]:
    """Builds described where the packs came from (a folder's *.descriptions.json: {"source", "builds": [{"label",
    "section", "text"}]}, as autosaved.org's page gives them), each with its words for matching."""
    out = []
    for path in sorted(Path(folder).glob("*.descriptions.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for b in data.get("builds") or []:
            if isinstance(b, Mapping) and b.get("label") and b.get("text"):
                out.append({"label": str(b["label"]), "section": str(b.get("section") or ""),
                            "text": str(b["text"])[:2000], "source": str(data.get("source") or path.name),
                            "words": _words(b["label"])})
    return out


def _words(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split()) - {"the", "a", "and", "of", "with"}


def describe(label: str, books: tuple[str, ...], known: list[dict]) -> dict | None:
    """The description of a blueprint: the build whose heading's words its label (with the book it is in) holds, at
    least one of its own label's, the most first; None when none matches 0.6 of its heading."""
    own = _words(label)
    mine = own | (_words(books[-1]) if books else set())   # (its own book's words; the outer books are too general)
    best, score = None, 0.0
    for d in known:
        if not d["words"] or not d["words"] & own:   # (at least a word of its own label)
            continue
        hit = len(d["words"] & mine) / len(d["words"])
        if hit > score or (hit == score and best is not None and len(d["words"]) > len(best["words"])):
            best, score = d, hit
    return best if best is not None and score >= 0.6 else None


def default_folder() -> Path | None:
    """The packs a game's runner names (NPC_BLUEPRINT_PACKS, set by tools/attach_runner.Runner: user_folder); none
    otherwise, so tests and tools never read the user's folder unasked."""
    folder = os.environ.get("NPC_BLUEPRINT_PACKS")
    return Path(folder) if folder else None


def user_folder() -> Path | None:
    """The user's Blueprints folder (their README says: one string a .txt file), when it is there."""
    home = Path.home() / "OneDrive" / "Documents" / "Factorio Mod NPC" / "Blueprints"
    return home if home.is_dir() else None


def _records(catalog: Any) -> Path:
    base = Path(getattr(catalog, "path", None) or ".").parent
    return base / "imports"


RECORD_VERSION = 7   # (a pack's record judged by older rules is judged again: 7, ports traced from its belts)


def mod_key(prototypes: Mapping[str, Any]) -> str:
    return _sha([RECORD_VERSION, sorted(prototypes)])


STAMPED = ("nuclear",)   # (subfolders of books stamped whole by their own builder, not the library's: nuclear_book.py)


def pack_files(folder: Path) -> list[Path]:
    """The packs in the folder and its subfolders (the optimizer writes its blocks to optimizer\\), but not the
    STAMPED books; of two files with one name, the newer (a copy left at the top went stale: the optimizer's fuel
    cell block was only in optimizer\\'s, 2026-10-31)."""
    newest: dict[str, Path] = {}
    for path in sorted(folder.rglob("*.txt")):
        rel = path.relative_to(folder)
        if len(rel.parts) > 1 and rel.parts[0].lower() in STAMPED:
            continue
        key = path.name.lower()
        if key not in newest or path.stat().st_mtime > newest[key].stat().st_mtime:
            newest[key] = path
    return sorted(newest.values())


def sync(catalog: Any, site: Mapping[str, Any], *, folder: Path | None = None,
         ask: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
         log: Callable[[str], None] = print) -> list[dict]:
    # (all engineers' planners one at a time; one that finds another judging goes on with its plan at once, and a
    # pass asks at most ASK_PER_SYNC new questions: a plan request never waits on the designer's answers for long)
    if not _SYNC.acquire(blocking=False):
        return []
    try:
        return _sync(catalog, site, folder=folder, ask=ask, log=log)
    finally:
        _SYNC.release()


def _sync(catalog: Any, site: Mapping[str, Any], *, folder: Path | None = None,
          ask: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
          log: Callable[[str], None] = print) -> list[dict]:
    """Every pack in `folder` not judged yet for this mod set, imported into `catalog`; what each gave (also kept in
    the data folder's imports/). `ask(prompt, schema)`: the designer, asked about a blueprint the rules cannot read
    once ever (its answer kept beside the pack), and no more than ASK_PER_MINUTE a minute for all engineers (the rest
    wait for later requests: a pack is judged whole once none waits). Only a blueprint that could be offered now is
    asked about: rejected ones and those waiting for research are judged before."""
    folder = folder or default_folder()
    if folder is None or not Path(folder).is_dir():
        return []
    prototypes = facts_prototypes(site)
    if not prototypes:
        return []   # (no game facts: judged when they come)
    records = _records(catalog)
    records.mkdir(parents=True, exist_ok=True)
    key = _sha([mod_key(prototypes), [(d["label"], d["text"]) for d in descriptions(Path(folder))]])
    turns = [ASK_PER_SYNC]
    out = []
    for path in pack_files(Path(folder)):
        if path.stem.lower().startswith("readme"):
            continue   # (the folder's own note)
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        named = [sidecar_named(Path(folder), heading(text, n)) for n, _ in strings(text)]
        side_text = json.dumps([sidecar(Path(folder), path.name)] + named, sort_keys=True)   # (a new sidecar: judged again)
        if not any([sidecar(Path(folder), path.name)] + named):
            side_text = "null"
        sha = hashlib.sha256((text + (side_text if side_text != "null" else "")).encode()).hexdigest()[:16]
        record_path = records / f"{sha}.json"
        old = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
        same = old.get("mods") == key
        if same and not old.get("pending") and not _research_came(old, site):
            continue   # (judged already for this mod set, and no blueprint waits on research done since)
        done = (old.get("done") or {}) if same else {}
        notes = dict(old.get("annotations") or {})
        if old.get("version") != RECORD_VERSION:   # (older rules' readings go; the designer's answers are kept)
            notes = {k: v for k, v in notes.items() if isinstance(v, Mapping) and v.get("by") == "designer"}
        record = _import(catalog, site, path.name, text, prototypes, notes, done, ask, Path(folder),
                         descriptions(Path(folder)), turns)
        if same:   # (what earlier requests imported, rejected or left out stays in the account)
            record["imported"] = list(old.get("imported") or []) + record["imported"]
            record["rejected"] = list(old.get("rejected") or []) + record["rejected"]
            seen = {r.get("label") for r in record["skipped"]}
            record["skipped"] = [r for r in old.get("skipped") or [] if r.get("label") not in seen] + record["skipped"]
        record.update(file=path.name, sha=sha, mods=key, version=RECORD_VERSION)
        tmp = record_path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
        os.replace(tmp, record_path)
        reasons = Counter(r["why"].split(":")[0] for r in record["rejected"])
        log(f"blueprint pack {path.name}: {len(record['imported'])} imported, {len(record['rejected'])} rejected"
            + (" (" + ", ".join(f"{n} {why}" for why, n in reasons.most_common(4)) + ")" if reasons else "")
            + (f", {len(record['pending'])} waiting for the designer" if record.get("pending") else "")
            + (f", {len(record['waiting'])} waiting for research" if record.get("waiting") else ""))
        out.append(record)
    return out


def _import(catalog: Any, site: Mapping[str, Any], file: str, text: str, prototypes: Mapping[str, Any],
            annotations: dict, done: dict, ask: Callable | None, folder: Path | None = None,
            known: list | None = None, turns: list | None = None) -> dict:
    from companion import build_history
    from companion.catalog_planner import PURPOSES
    record: dict = {"imported": [], "rejected": [], "skipped": [], "pending": [], "waiting": [],
                    "annotations": annotations, "done": dict(done)}
    kept = all_answers(folder)   # (the designer's answers beside the packs: every run's, every engineer's)
    found = strings(text)
    side = sidecar(folder, file)
    if not found:
        record["rejected"].append({"label": file, "why": "not a blueprint string (no string in it)"})
        return record
    stem = Path(file).stem
    researched = site   # (each blueprint read against what is researched; a locked one's recipes added for itself)
    for line, books, blueprint in _all(found, record, file):
        site = researched
        label = str(blueprint.get("label") or "unnamed")
        where = "/".join((f"{stem}@{line}" if len(found) > 1 else stem, *books, label))
        plan, skipped, missing = convert(blueprint, prototypes)
        # (the sidecar is this pack's one blueprint's, or the one its label names, or the one its label line names)
        mine = side if side and ((len(found) == 1 and not books) or side.get("label") == label) else None
        if mine is None and not books:
            mine = sidecar_named(folder, heading(text, line))
        content = _sha([where, plan["entities"], missing] + ([sidecar_ports(mine)] if mine else []))
        if content in record["done"]:
            continue   # (imported or rejected on an earlier request)
        if skipped:
            record["skipped"].append({"label": where, "what": skipped})

        def reject(why: str) -> None:
            record["rejected"].append({"label": where, "why": why})
            record["done"][content] = "rejected"
        if missing:
            absent = [m for m in missing if " (" not in m]
            reject(("not in this mod set: " + ", ".join(absent[:6])) if absent
                   else ("cannot be built here: " + ", ".join(missing[:4])))
            continue
        railed = sum(1 for e in blueprint.get("entities") or [] if isinstance(e, Mapping)
                     and str(e.get("name") or "") in SKIPPED_TYPES)
        if railed and railed >= len(plan["entities"]):   # (its rails and trains are what it is: not placed yet)
            reject("a rail build (rails and trains not placed yet)")
            continue
        if not plan["entities"]:
            reject("nothing it could place")
            continue
        unknown = sorted({str(e["recipe"]) for e in plan["entities"] if e.get("recipe")} - set(_recipes(site)))
        if unknown and not set(unknown) <= set(_known(site)):
            # (a recipe the game does not know: another mod set's, or facts from an older build. What it makes is
            # read from its recipes once researched: judged then)
            record["waiting"].append({"label": where, "recipes": unknown})
            continue
        # (one whose recipes the game knows but has not researched yet is read from them now and kept locked till they
        # are: its recipes_needed, blueprints.locked_items. The bus plans its room ahead, Factory's U-2)
        site = {**site, "recipes": list(_recipes(site).values()) + [r for n, r in _known(site).items() if n in unknown]} \
            if unknown else site
        drills = any((prototypes.get(e["prototype"]) or {}).get("type") == "mining-drill" for e in plan["entities"])
        # (its ore by its label; else whichever patch it is placed on, as the library's mining layouts:
        # blueprints.ANY_ORE)
        from companion import blueprints as bp_lib
        ore = (ore_of(label + " " + " ".join(books), site) or bp_lib.ANY_ORE) if drills else None
        note = annotations.get(content)
        finals, _ = products_of(plan["entities"], site)
        if mine:   # (what the sidecar says leaves it: its products, mods' recipes too)
            finals = [str(r.get("item") or r.get("name")) for r in mine.get("outputs") or ()
                      if isinstance(r, Mapping) and (r.get("item") or r.get("name"))] or finals
        said = describe(label, books, known or [])
        if note is None:
            purpose = purpose_by_rules(label + " " + " ".join(books), plan["entities"], prototypes, finals,
                                       set(PURPOSES), site)
            if purpose and purpose.startswith("import:") and not finals:   # (named by its label: what it makes)
                finals = purpose[len("import:"):].split("+")
            asked = kept.get(_sha(plan["entities"]))
            if purpose:
                note = {"purpose": purpose, "products": finals, "by": "rules"}
            elif asked is not None:   # (answered before, by any run or engineer: never asked again)
                note = dict(asked)
            elif ask is not None and (turns is None or turns[0] > 0) and may_ask():
                if turns is not None:
                    turns[0] -= 1
                note = _ask(ask, where, plan["entities"], said)
                if note is None:   # (no answer now: asked again on a later request)
                    record["pending"].append(where)
                    continue
                keep_answer(folder, file, _sha(plan["entities"]), note)
                kept[_sha(plan["entities"])] = dict(note)   # (the same content again in this pack: answered)
            elif ask is not None:
                record["pending"].append(where)   # (the designer's turn comes on a later request)
                continue
            else:
                note = {"purpose": None, "products": finals, "by": "none"}
            annotations[content] = note
        purpose = note.get("purpose") or "import:unknown"
        products = list(note.get("products") or finals)
        witnesses = [{"kind": "entity_built", "entity_id": plan["entities"][0]["id"]}]
        for item in products:   # (each product's own maker: a block making red and green is found for either)
            maker = next((e for e in plan["entities"] if e.get("recipe") and _makes(site, e["recipe"], [item])), None)
            if maker:
                witnesses.append({"kind": "product_counter_delta", "entity_id": maker["id"], "item": item,
                                  "min_delta": 1})
        full = {"plan_id": f"import-{content}", "entities": plan["entities"],
                "stages": [{"id": "imported", "depends_on": [], "entity_ids": [e["id"] for e in plan["entities"]],
                            "manual_supplies": [], "witnesses": witnesses}]}
        ground = ore_site(plan["entities"], prototypes, ore) if ore else {"resources": []}
        site_for = {**site, "prototypes": {**prototypes}, **ground}
        try:
            build = build_history.new_build(full, site_for, source=f"import:{where}"[:160], purpose=purpose)
        except Exception as exc:
            reject(f"could not be read as a layout ({str(exc)[:80]})")
            continue
        build["label"] = label[:80]
        design, tier = variant_of(label)
        build["recipes_needed"] = sorted({str(e["recipe"]) for e in plan["entities"] if e.get("recipe")})
        # (its ports: the optimizer's sidecar; else read from its own belts, items, lanes and rates
        # (companion/book_blocks.py); else its edge belts)
        traced = None if mine else _traced(build, site)
        build["import"] = {"file": file, "books": list(books), "label": label, "products": products,
                           "annotated_by": note.get("by"),
                           "ports": _shifted(sidecar_ports(mine), plan["entities"], build["entities"]) if mine
                           else traced["ports"] + [p for p in ports(build, prototypes, site) if p["kind"] == "fluid"]
                           if traced else ports(build, prototypes, site),   # (its pipes at the edge as before)
                           **({"ports_from": "sidecar", "box": dict(mine["box"])} if mine else {}),
                           **({"ports_from": "trace", "bus_side": traced["bus_side"], "passes": traced["passes"]}
                              if traced else {}),
                           "skipped": skipped, "design": design, "tier": tier, **({"ore": ore} if ore else {}),
                           **({"description": {"text": said["text"][:800], "heading": said["label"],
                                               "source": said["source"]}} if said else {})}
        # (an import is itself and where it came from: the same row kept in two books for two ores is two layouts;
        # the same import judged again replaces its own entry, what it has learned in use kept)
        build["id"] = _sha([build["id"], file, where])   # (the whole path: the shown source is cut short)
        had = catalog.blueprints.get(build["id"])
        if had is not None:
            catalog.save({**build, **{k: had[k] for k in ("stats", "uses", "notes", "lineage") if k in had}})
        else:
            catalog.add(build)
        record["imported"].append({"id": build["id"], "label": where, "purpose": purpose})
        record["done"][content] = "imported"
    return record


def _research_came(old: Mapping[str, Any], site: Mapping[str, Any]) -> bool:
    """A blueprint of the pack waited on a recipe the site now knows (it is judged again)."""
    known = set(_recipes(site))
    return any(set(w.get("recipes") or ()) <= known for w in old.get("waiting") or [])


def _all(found: list[tuple[int, str]], record: dict, file: str) -> Iterator[tuple[int, tuple[str, ...], dict]]:
    """Every blueprint of a file's strings with its line; a string that does not decode said with its line."""
    for line, text in found:
        try:
            obj = decode(text)
        except BlueprintError as exc:
            record["rejected"].append({"label": f"{file} line {line}", "why": str(exc)})
            continue
        for books, blueprint in blueprints(obj):
            yield line, books, blueprint


def _makes(site: Mapping[str, Any], recipe: str, items: list[str]) -> bool:
    r = _recipes(site).get(recipe) or {}
    return any(isinstance(p, Mapping) and p.get("name") in items for p in r.get("products") or [])


def _ask(ask: Callable, where: str, entities: list[dict], said: Mapping[str, Any] | None = None) -> dict | None:
    """The designer, asked once what a blueprint the rules cannot read is for (kept: never asked again)."""
    from companion.catalog_planner import PURPOSES
    counts = Counter(e["prototype"] for e in entities)
    prompt = (f"A Factorio blueprint the player keeps, \"{where}\", has: "
              + ", ".join(f"{n} x{c}" for n, c in counts.most_common(20))
              + (f". Its author says: \"{said['text'][:600]}\"" if said else "")
              + ". What is it for? Answer with one goal from this list, or \"none\": "
              + ", ".join(sorted(PURPOSES)) + "; and the items it makes.")
    try:
        got = ask(prompt, ASK_SCHEMA)
    except Exception:
        return None
    purpose = str(got.get("purpose") or "")
    return {"purpose": purpose if purpose in PURPOSES else None,
            "products": [str(p) for p in got.get("products") or []][:6], "by": "designer",
            "why": str(got.get("why") or "")[:200]}
