"""Render a declared Factorio layout as an offline coordinate diagram.

This module reads no game state and makes no claim of native connectivity or flow.
Internal API: render_plan(document, output_path) -> pathlib.Path.
User previews enter through preview.render_preview after accepted-plan checks.
Requires Pillow; no other third-party packages.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


CANVAS = (1500, 1000)
PLOT = (95, 165, 1090, 845)
PANEL = (1120, 165, 1455, 845)
BG = "#f7f9f8"
INK = "#18282c"
MUTED = "#64777b"
GRID = "#dce7e5"
EXISTING = "#87959b"
STAGE_ONE = "#0f8e82"
LATER = "#c98a32"
ZONE = "#e2efe7"
LINK = "#477d86"
FONT_PATHS = (
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)
BOLD_PATHS = (
    Path("C:/Windows/Fonts/seguisb.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)


class RenderError(ValueError):
    pass


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path in BOLD_PATHS if bold else FONT_PATHS:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1_000_000:
        raise RenderError(f"{label} must be a finite coordinate")
    return float(value)


def _position(raw: Any, label: str) -> tuple[float, float]:
    if not isinstance(raw, dict) or set(raw) != {"x", "y"}:
        raise RenderError(f"{label} needs position {{x,y}}")
    return _number(raw["x"], label + ".x"), _number(raw["y"], label + ".y")


def _box(raw: Any, label: str) -> tuple[float, float, float, float]:
    if isinstance(raw, list) and len(raw) == 4:
        x1, y1, x2, y2 = (_number(value, label) for value in raw)
    elif isinstance(raw, dict) and set(raw) == {"left", "top", "right", "bottom"}:
        x1, y1, x2, y2 = (_number(raw[key], label) for key in
                          ("left", "top", "right", "bottom"))
    else:
        raise RenderError(f"{label} needs [left,top,right,bottom]")
    if x1 >= x2 or y1 >= y2:
        raise RenderError(f"{label} must have positive area")
    return x1, y1, x2, y2


def _safe_text(value: Any, label: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit \
            or any(ord(c) < 32 and c not in "\t\n" for c in value):
        raise RenderError(f"Invalid {label}")
    return " ".join(value.split())


def _size(raw: dict, prototype: str, catalog: dict) -> tuple[float, float]:
    declared = raw.get("size")
    if declared is not None:
        if not isinstance(declared, dict) or set(declared) != {"w", "h"}:
            raise RenderError(f"{raw['id']}: size requires {{w,h}}")
        w, h = declared["w"], declared["h"]
    else:
        spec = catalog.get(prototype)
        if not isinstance(spec, dict):
            raise RenderError(f"{raw['id']}: no declared size for {prototype}")
        w = spec.get("width", spec.get("w"))
        h = spec.get("height", spec.get("h"))
    w, h = _number(w, "entity width"), _number(h, "entity height")
    if not 0 < w <= 256 or not 0 < h <= 256:
        raise RenderError(f"{raw['id']}: invalid size")
    return (h, w) if raw.get("direction", 0) in (4, 12) else (w, h)


def _entities(rows: Any, catalog: dict, state: str) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > 256:
        raise RenderError(f"{state} entities must be a bounded list")
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise RenderError("Entity must be an object")
        entity_id = _safe_text(raw.get("id"), "entity ID", 64)
        name = _safe_text(raw.get("prototype", raw.get("name")), "prototype", 128)
        x, y = _position(raw.get("position"), entity_id)
        direction = raw.get("direction", 0)
        if type(direction) is not int or direction not in (0, 4, 8, 12):
            raise RenderError(f"{entity_id}: only N/E/S/W direction is supported")
        w, h = _size(raw, name, catalog)
        recipe = raw.get("recipe")
        if recipe is not None:
            recipe = _safe_text(recipe, "recipe", 128)
        result.append({"id": entity_id, "name": name, "x": x, "y": y,
                       "w": w, "h": h, "direction": direction,
                       "recipe": recipe, "state": state})
    return result


def _stage_rows(document: dict, proposal: dict, proposed: list[dict]) -> tuple[list[dict], dict[str, int]]:
    raw = document.get("stages", proposal.get("stages", []))
    if not isinstance(raw, list) or len(raw) > 64:
        raise RenderError("Stages must be a bounded list")
    proposed_ids = {entity["id"] for entity in proposed}
    stages, mapping = [], {}
    for index, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise RenderError("Stage must be an object")
        stage_id = _safe_text(row.get("id", f"stage-{index}"), "stage ID", 64)
        ids = row.get("entity_ids", [])
        if not isinstance(ids, list) or len(ids) > 256:
            raise RenderError("Stage entity IDs must be a list")
        for entity_id in ids:
            if entity_id not in proposed_ids or entity_id in mapping:
                raise RenderError("Stage references unknown or duplicate proposal entity")
            mapping[entity_id] = index
        stages.append({"id": stage_id, "label": _safe_text(row.get("label", stage_id),
                                                             "stage label", 100),
                       "entity_ids": ids})
    if raw and set(mapping) != proposed_ids:
        raise RenderError("Every proposed entity needs a stage")
    return stages, mapping


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont,
          width: int, max_lines: int = 99) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > width:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                return lines
        else:
            current = trial
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def _dashed(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
            color: str, width: int = 2, dash: int = 9) -> None:
    x1, y1, x2, y2 = box
    for start in range(int(x1), int(x2), dash * 2):
        draw.line((start, y1, min(start + dash, x2), y1), fill=color, width=width)
        draw.line((start, y2, min(start + dash, x2), y2), fill=color, width=width)
    for start in range(int(y1), int(y2), dash * 2):
        draw.line((x1, start, x1, min(start + dash, y2)), fill=color, width=width)
        draw.line((x2, start, x2, min(start + dash, y2)), fill=color, width=width)


def _arrow(draw: ImageDraw.ImageDraw, start: tuple[float, float],
           end: tuple[float, float], color: str, width: int = 3) -> None:
    draw.line((*start, *end), fill=color, width=width)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 3:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip = end
    back = (end[0] - ux * 10, end[1] - uy * 10)
    draw.polygon([tip, (back[0] + px * 5, back[1] + py * 5),
                  (back[0] - px * 5, back[1] - py * 5)], fill=color)


def _direction_arrow(draw: ImageDraw.ImageDraw, center: tuple[float, float],
                     direction: int, color: str) -> None:
    vectors = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
    dx, dy = vectors[direction]
    _arrow(draw, (center[0] - dx * 7, center[1] - dy * 7),
           (center[0] + dx * 12, center[1] + dy * 12), color, 2)


def render_plan(document: dict, output_path: str | Path) -> Path:
    """Write one deterministic PNG from declared geometry; never interrogate Factorio."""
    if not isinstance(document, dict):
        raise RenderError("Input must be a JSON object")
    metadata = document.get("metadata", {})
    proposal = document.get("proposal", {})
    snapshot = document.get("snapshot", {})
    if not isinstance(metadata, dict) or not isinstance(proposal, dict) \
            or not isinstance(snapshot, dict):
        raise RenderError("Metadata, proposal and snapshot must be objects")
    catalog = document.get("prototypes", snapshot.get("prototypes", {}))
    if not isinstance(catalog, dict):
        raise RenderError("Prototype geometry map must be an object")
    existing = _entities(document.get("existing_entities", []), catalog, "existing")
    proposed = _entities(proposal.get("entities", []), catalog, "proposed")
    if not proposed:
        raise RenderError("At least one proposed entity is required")
    all_entities = existing + proposed
    by_id = {entity["id"]: entity for entity in all_entities}
    if len(by_id) != len(all_entities):
        raise RenderError("Duplicate entity ID")
    stages, stage_map = _stage_rows(document, proposal, proposed)
    zones = document.get("resource_zones", [])
    annotations = document.get("annotations", [])
    connections = document.get("connections", [])
    if not all(isinstance(rows, list) for rows in (zones, annotations, connections)) \
            or len(zones) > 64 or len(annotations) > 64 or len(connections) > 256:
        raise RenderError("Drawing overlays must be bounded lists")
    parsed_zones = []
    for zone in zones:
        if not isinstance(zone, dict):
            raise RenderError("Resource zone must be an object")
        parsed_zones.append((_box(zone.get("box"), "resource zone"),
                             _safe_text(zone.get("item"), "resource item", 80)))

    def endpoint(value: Any) -> tuple[float, float]:
        if isinstance(value, str) and value in by_id:
            entity = by_id[value]
            return entity["x"], entity["y"]
        if isinstance(value, dict):
            return _position(value, "external connector")
        raise RenderError("Connection endpoint must name an entity or {x,y}")

    parsed_links = []
    for link in connections:
        if not isinstance(link, dict):
            raise RenderError("Connection must be an object")
        source, target = endpoint(link.get("from")), endpoint(link.get("to"))
        kind = _safe_text(link.get("kind", "connection"), "connection kind", 40)
        item = link.get("item", "")
        item = _safe_text(item, "connection item", 60) if item else ""
        rate = link.get("estimated_rate")
        if rate is not None:
            if isinstance(rate, str):
                rate = _safe_text(rate, "estimated rate", 40)
            else:
                rate = f"{_number(rate, 'estimated rate'):g}"
        parsed_links.append((source, target, kind, item, rate))
    parsed_notes = []
    circuit_wires = document.get("circuit_wires", [])
    if type(circuit_wires) is not list or len(circuit_wires) > 128:
        raise RenderError("Circuit wires must be a bounded list")
    parsed_circuits = []
    for wire in circuit_wires:
        if type(wire) is not dict or set(wire) != {"from_id", "from_connector", "to_id", "to_connector", "color"} \
                or wire["color"] not in ("red", "green", "copper") \
                or any(type(wire[key]) is not int or not 1 <= wire[key] <= 255
                       for key in ("from_connector", "to_connector")):
            raise RenderError("Invalid circuit wire")
        parsed_circuits.append((endpoint(wire["from_id"]), endpoint(wire["to_id"]), wire))
    for row in annotations:
        if not isinstance(row, dict):
            raise RenderError("Annotation must be an object")
        parsed_notes.append((_number(row.get("x"), "annotation x"),
                             _number(row.get("y"), "annotation y"),
                             _safe_text(row.get("text"), "annotation", 140)))

    extents = []
    for entity in all_entities:
        extents.append((entity["x"] - entity["w"] / 2, entity["y"] - entity["h"] / 2,
                        entity["x"] + entity["w"] / 2, entity["y"] + entity["h"] / 2))
    extents += [box for box, _ in parsed_zones]
    extents += [(x, y, x, y) for x, y, _ in parsed_notes]
    extents += [(x, y, x, y) for start, end, *_ in parsed_links for x, y in (start, end)]
    left = min(box[0] for box in extents) - 2
    top = min(box[1] for box in extents) - 2
    right = max(box[2] for box in extents) + 2
    bottom = max(box[3] for box in extents) + 2
    world_w, world_h = max(4, right - left), max(4, bottom - top)
    scale = min((PLOT[2] - PLOT[0] - 45) / world_w,
                (PLOT[3] - PLOT[1] - 45) / world_h)
    xpad = (PLOT[2] - PLOT[0] - world_w * scale) / 2
    ypad = (PLOT[3] - PLOT[1] - world_h * scale) / 2

    def point(x: float, y: float) -> tuple[float, float]:
        return PLOT[0] + xpad + (x - left) * scale, PLOT[1] + ypad + (y - top) * scale

    image = Image.new("RGB", CANVAS, BG)
    draw = ImageDraw.Draw(image)
    font12, font14, font16 = _font(12), _font(14), _font(16)
    font18, font24, font34 = _font(18), _font(24, True), _font(34, True)
    title = _safe_text(metadata.get("title", "Proposed factory layout"), "title", 100)
    subtitle = _safe_text(metadata.get("subtitle", "Declared staged blueprint geometry"),
                          "subtitle", 180)
    draw.text((70, 39), title, fill=INK, font=font34)
    draw.text((72, 91), subtitle, fill=MUTED, font=font16)
    draw.rounded_rectangle(PLOT, radius=20, fill="#ffffff", outline="#d4e0df", width=2)
    draw.rounded_rectangle(PANEL, radius=20, fill="#ffffff", outline="#d4e0df", width=2)
    draw.text((PLOT[0] + 18, PLOT[1] + 14), "LAYOUT · WORLD TILES", fill=MUTED, font=font12)
    # Fixed six-division coordinate grid makes the diagram readable at any extent.
    for i in range(7):
        gx = left + world_w * i / 6
        px, _ = point(gx, top)
        draw.line((px, PLOT[1] + 38, px, PLOT[3] - 15), fill=GRID, width=1)
        draw.text((px + 3, PLOT[3] - 31), f"{gx:g}", fill=MUTED, font=font12)
        gy = top + world_h * i / 6
        _, py = point(left, gy)
        draw.line((PLOT[0] + 14, py, PLOT[2] - 14, py), fill=GRID, width=1)
        draw.text((PLOT[0] + 17, py + 2), f"{gy:g}", fill=MUTED, font=font12)
    for box, item in parsed_zones:
        rect = (*point(box[0], box[1]), *point(box[2], box[3]))
        draw.rounded_rectangle(rect, radius=8, fill=ZONE)
        _dashed(draw, rect, "#7eaf8e", 2)
        draw.text((rect[0] + 6, rect[1] + 5), item + " resource", fill="#386a4a", font=font12)
    for start, end, kind, item, rate in parsed_links:
        p1, p2 = point(*start), point(*end)
        _arrow(draw, p1, p2, LINK, 3)
        label = " · ".join(x for x in (kind, item,
                                       f"~{rate} estimated" if rate is not None else "") if x)
        midpoint = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        draw.text((midpoint[0] + 5, midpoint[1] - 19), label[:60], fill=LINK, font=font12,
                  stroke_width=3, stroke_fill="white")

    for start, end, wire in parsed_circuits:
        p1, p2 = point(*start), point(*end)
        wire_color = {"red": "#c64545", "green": "#31935a", "copper": "#b17838"}[wire["color"]]
        draw.line((*p1, *p2), fill=wire_color, width=3)
        midpoint = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        label = f"{wire['color']} {wire['from_connector']}:{wire['to_connector']}"
        draw.text((midpoint[0] + 5, midpoint[1] - 19), label, fill=wire_color, font=font12,
                  stroke_width=3, stroke_fill="white")
    label_boxes: list[tuple[float, float, float, float]] = []
    for entity in all_entities:
        x1, y1 = point(entity["x"] - entity["w"] / 2, entity["y"] - entity["h"] / 2)
        x2, y2 = point(entity["x"] + entity["w"] / 2, entity["y"] + entity["h"] / 2)
        stage = stage_map.get(entity["id"], 0)
        color = EXISTING if entity["state"] == "existing" else STAGE_ONE if stage <= 1 else LATER
        fill = "#e2e8e9" if entity["state"] == "existing" else \
            "#d4efea" if stage <= 1 else "#fff0d9"
        draw.rounded_rectangle((x1, y1, x2, y2), radius=min(9, max(1, int(scale / 6))),
                               fill=fill, outline=color, width=3)
        if entity["state"] == "proposed" and stage > 1:
            _dashed(draw, (x1 + 2, y1 + 2, x2 - 2, y2 - 2), color, 1, 6)
        _direction_arrow(draw, point(entity["x"], entity["y"]), entity["direction"], color)
        label = f"{entity['id']} · {entity['name']}"
        if len(label) > 40:
            label = label[:37] + "…"
        configuration = document.get("entity_configurations", {}).get(entity["id"], {})
        quality = configuration.get("quality", "normal")
        if quality != "normal":
            label = quality + " " + label
        label_w = draw.textlength(label, font=font12)
        # Greedy local placements avoid most entity label collisions without moving geometry.
        anchors = [(x1, y2 + 5), (x1, y1 - 19), (x2 + 6, y1), (x1 - label_w - 6, y1)]
        chosen = anchors[0]
        chosen_box = None
        for candidate in anchors:
            box = (candidate[0], candidate[1], candidate[0] + label_w + 5, candidate[1] + 17)
            if candidate[0] >= PLOT[0] + 10 and box[2] <= PLOT[2] - 10 \
                    and candidate[1] >= PLOT[1] + 38 and box[3] <= PLOT[3] - 12 \
                    and not any(box[0] < other[2] and other[0] < box[2]
                                and box[1] < other[3] and other[1] < box[3]
                                for other in label_boxes):
                chosen = candidate
                chosen_box = box
                break
        if chosen_box is None:
            # Still record the fallback label so later labels can route around it.
            chosen_box = (chosen[0], chosen[1], chosen[0] + label_w + 5,
                          chosen[1] + 17)
        label_boxes.append(chosen_box)
        draw.text(chosen, label, fill=INK, font=font12, stroke_width=2, stroke_fill="white")
    for x, y, note in parsed_notes:
        px, py = point(x, y)
        draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill="#2e608a")
        for offset, line in enumerate(_wrap(draw, note, font12, 190, 3)):
            draw.text((px + 9, py - 9 + offset * 15), line, fill="#2e608a", font=font12,
                      stroke_width=2, stroke_fill="white")

    panel_x, panel_y = PANEL[0] + 22, PANEL[1] + 22
    draw.text((panel_x, panel_y), "PLAN REVIEW", fill=MUTED, font=font12)
    status = _safe_text(metadata.get("review_status", "Offline draft"), "review status", 80)
    draw.rounded_rectangle((panel_x, panel_y + 30, PANEL[2] - 21, panel_y + 67),
                           radius=10, fill="#e8f2ee")
    draw.text((panel_x + 11, panel_y + 39), status, fill="#2e6f59", font=font14)
    y = panel_y + 87
    draw.text((panel_x, y), "Stages", fill=INK, font=font18)
    y += 31
    for index, stage in enumerate(stages[:7], 1):
        color = STAGE_ONE if index == 1 else LATER
        draw.ellipse((panel_x, y + 3, panel_x + 19, y + 22), fill=color)
        draw.text((panel_x + 6, y + 3), str(index), fill="white", font=font12)
        lines = _wrap(draw, stage["label"], font14, 266, 2)
        for j, line in enumerate(lines):
            draw.text((panel_x + 30, y + j * 17), line, fill=INK, font=font14)
        y += max(33, len(lines) * 17 + 7)
    if len(stages) > 7:
        draw.text((panel_x, y), f"+ {len(stages) - 7} later stages", fill=MUTED, font=font14)
        y += 29
    y += 9
    draw.text((panel_x, y), "Legend", fill=INK, font=font18)
    y += 31
    for color, label in ((EXISTING, "Existing machine"), (STAGE_ONE, "First proposed stage"),
                         (LATER, "Later proposed stage"), (LINK, "Declared connector")):
        draw.rectangle((panel_x, y + 3, panel_x + 15, y + 16), fill=color)
        draw.text((panel_x + 25, y), label, fill=INK, font=font14)
        y += 24
    y += 8
    assumptions = metadata.get("assumptions", [])
    if isinstance(assumptions, str):
        assumptions = [assumptions]
    if not isinstance(assumptions, list) or len(assumptions) > 12:
        raise RenderError("Assumptions must be a short list")
    draw.text((panel_x, y), "Assumptions", fill=INK, font=font18)
    y += 30
    for assumption in assumptions[:5]:
        for line in _wrap(draw, "• " + _safe_text(assumption, "assumption", 220),
                          font12, PANEL[2] - panel_x - 20, 2):
            if y > PANEL[3] - 55:
                break
            draw.text((panel_x, y), line, fill=MUTED, font=font12)
            y += 16
        y += 5
    origin = _safe_text(metadata.get("origin", "Declared offline design"), "origin", 120)
    draw.text((70, 878), "Origin: " + origin, fill=MUTED, font=font14)
    footer = _safe_text(metadata.get("footer", "Proposed / unbuilt; native flow unqualified"),
                        "provenance footer", 180)
    draw.text((70, 916), footer, fill="#725725", font=font16)
    draw.text((70, 952), "Positions and sizes are declared; native reach, payment, power and flow need separate proof.",
              fill=MUTED, font=font14)
    target = Path(output_path)
    if target.suffix.lower() != ".png":
        raise RenderError("Output path must end in .png")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=False)
    return target


