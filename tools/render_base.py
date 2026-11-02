"""Top-down pictures of what an NPC engineer built.

Renders the game's `/npc-plan map` dump (npc-map-v1) to a PNG: ore, water,
trees, and every building coloured by kind, with belt and inserter flow
arrows and short recipe labels on machines.

    python tools/render_base.py SAVE.zip OUT.png [--npc-id 1] [--radius 96]

renders straight from a saved game (a private headless server loads it).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

GROUND = (46, 54, 40)
WATER = (38, 78, 132)
TREE = (30, 72, 34)
ORE = {"iron-ore": (88, 108, 138), "copper-ore": (186, 106, 58), "coal": (22, 22, 22), "stone": (164, 144, 104),
       "crude-oil": (96, 40, 118), "uranium-ore": (90, 196, 70)}
KIND = {  # by entity type, then overridden by name
    "transport-belt": (214, 178, 40), "underground-belt": (190, 150, 30), "splitter": (230, 200, 60),
    "inserter": (205, 196, 90), "assembling-machine": (104, 126, 150), "furnace": (150, 104, 66),
    "mining-drill": (92, 84, 74), "lab": (178, 136, 222), "electric-pole": (128, 92, 50),
    "pipe": (100, 146, 166), "pipe-to-ground": (80, 126, 146), "boiler": (170, 88, 58),
    "generator": (78, 152, 160), "offshore-pump": (60, 120, 174), "container": (140, 98, 58),
    "logistic-container": (150, 110, 70), "ammo-turret": (186, 58, 58), "wall": (150, 150, 150),
    "storage-tank": (90, 120, 140), "pump": (90, 140, 150), "radar": (130, 130, 160),
    "solar-panel": (50, 60, 90), "accumulator": (90, 90, 120), "rocket-silo": (200, 200, 210),
}
NAME = {"oil-refinery": (140, 88, 160), "chemical-plant": (112, 160, 116), "pumpjack": (64, 60, 66),
        "long-handed-inserter": (206, 80, 80), "fast-inserter": (80, 140, 210), "burner-inserter": (150, 120, 90),
        "steam-engine": (78, 152, 160)}
LABEL = {"iron-gear-wheel": "gear", "automation-science-pack": "RED", "logistic-science-pack": "GRN",
         "chemical-science-pack": "BLU", "electronic-circuit": "GC", "advanced-circuit": "RC",
         "engine-unit": "eng", "copper-cable": "cab", "plastic-bar": "plas", "sulfur": "S",
         "basic-oil-processing": "oil", "advanced-oil-processing": "oil+", "steel-plate": "Stl",
         "iron-plate": "Fe", "copper-plate": "Cu", "stone-brick": "brk", "transport-belt": "belt",
         "inserter": "ins", "pipe": "pipe", "firearm-magazine": "ammo"}
LEGEND = [("belt", KIND["transport-belt"]), ("inserter", KIND["inserter"]), ("assembler", KIND["assembling-machine"]),
          ("furnace", KIND["furnace"]), ("drill", KIND["mining-drill"]), ("lab", KIND["lab"]),
          ("pole", KIND["electric-pole"]), ("pipe", KIND["pipe"]), ("steam", KIND["generator"]),
          ("refinery", NAME["oil-refinery"]), ("chem plant", NAME["chemical-plant"]), ("chest", KIND["container"]),
          ("turret", KIND["ammo-turret"]), ("iron ore", ORE["iron-ore"]), ("copper ore", ORE["copper-ore"]),
          ("coal", ORE["coal"]), ("stone", ORE["stone"]), ("oil", ORE["crude-oil"]), ("water", WATER)]
# Unit vectors for 16-way directions (north = 0, east = 4, ...).
VEC = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def _bounds(data: Mapping[str, Any], crop: bool, margin: int = 6) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = data["area"]
    if not crop:
        return x0, y0, x1, y1
    own = [e for e in data.get("entities", []) if e.get("f") == data.get("force")]
    if not own:
        return x0, y0, x1, y1
    xs = [e["x"] for e in own] + [data["home"]["x"]]
    ys = [e["y"] for e in own] + [data["home"]["y"]]
    return (max(x0, int(min(xs)) - margin), max(y0, int(min(ys)) - margin),
            min(x1, int(max(xs)) + margin), min(y1, int(max(ys)) + margin))


def render(data: Mapping[str, Any], out: Path, *, scale: int = 8, crop: bool = True) -> Path:
    x0, y0, x1, y1 = _bounds(data, crop)
    width, height = (x1 - x0 + 1) * scale, (y1 - y0 + 1) * scale
    legend_w = 150
    image = Image.new("RGB", (width + legend_w, max(height, 22 * len(LEGEND) // 1 + 60)), GROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    def tile(x: int, y: int, colour, inset: int = 0) -> None:
        inset = min(inset, (scale - 1) // 2)
        if x0 <= x <= x1 and y0 <= y <= y1:
            px, py = (x - x0) * scale, (y - y0) * scale
            draw.rectangle([px + inset, py + inset, px + scale - 1 - inset, py + scale - 1 - inset], fill=colour)

    for y, a, b in data.get("water", []):
        for x in range(a, b + 1):
            tile(x, y, WATER)
    for name, rows in (data.get("resources") or {}).items():
        colour = ORE.get(name, (120, 120, 120))
        for y, a, b in rows:
            for x in range(a, b + 1):
                tile(x, y, colour, inset=1 if name != "crude-oil" else 0)
    for x, y in data.get("trees", []):
        tile(x, y, TREE, inset=2)
    for e in data.get("entities", []):
        if e.get("t") == "simple-entity":  # rocks
            tile(int(e["x"] // 1), int(e["y"] // 1), (110, 108, 100), inset=2)

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (x - x0) * scale, (y - y0) * scale

    def arrow(cx: float, cy: float, vector, length: float, colour) -> None:
        (vx, vy) = vector
        sx, sy = to_px(cx - vx * length / 2, cy - vy * length / 2)
        ex, ey = to_px(cx + vx * length / 2, cy + vy * length / 2)
        draw.line([sx, sy, ex, ey], fill=colour, width=max(1, scale // 6))
        # arrow head
        hx, hy = -vy, vx
        size = scale * 0.25
        draw.polygon([(ex, ey), (ex - vx * size + hx * size, ey - vy * size + hy * size),
                      (ex - vx * size - hx * size, ey - vy * size - hy * size)], fill=colour)

    entities = sorted((e for e in data.get("entities", []) if e.get("t") != "simple-entity"),
                      key=lambda e: (e.get("w", 1) * e.get("h", 1)), reverse=True)
    for e in entities:
        w, h = e.get("w", 1), e.get("h", 1)
        d = e.get("d", 0) or 0
        if d in (4, 12):
            w, h = h, w
        colour = NAME.get(e["n"], KIND.get(e["t"], (120, 120, 120)))
        if e.get("f") != data.get("force"):
            colour = tuple(min(255, c + 60) for c in colour)
        left, top = to_px(e["x"] - w / 2, e["y"] - h / 2)
        right, bottom = to_px(e["x"] + w / 2, e["y"] + h / 2)
        small = e["t"] in ("transport-belt", "pipe", "pipe-to-ground", "electric-pole", "inserter")
        pad = 0 if small and e["t"] != "electric-pole" else 1
        if e["t"] == "electric-pole":
            pad = scale // 3
        if e["t"] in ("pipe", "pipe-to-ground"):
            pad = scale // 4
        pad = min(pad, max(0, int((min(right - left, bottom - top) - 1) // 2)))
        draw.rectangle([left + pad, top + pad, right - 1 - pad, bottom - 1 - pad], fill=colour,
                       outline=None if small else (20, 20, 20))
        vector = VEC.get(d - d % 4, (0, -1))
        if e["t"] in ("transport-belt", "underground-belt", "splitter"):
            arrow(e["x"], e["y"], vector, 0.7, (60, 40, 0))
        elif e["t"] == "inserter":
            arrow(e["x"], e["y"], (-vector[0], -vector[1]), 0.8, (30, 30, 30))
        elif e["t"] == "mining-drill" and e["n"] != "pumpjack":
            arrow(e["x"], e["y"], vector, min(w, h) * 0.8, (230, 230, 230))
        label = LABEL.get(e.get("r") or "")
        if label and min(w, h) >= 2:
            tx, ty = to_px(e["x"], e["y"])
            draw.text((tx - 3 * len(label), ty - 5), label, fill=(255, 255, 255), font=font)
    if data.get("character"):
        cx, cy = to_px(data["character"]["x"], data["character"]["y"])
        draw.ellipse([cx - scale // 2, cy - scale // 2, cx + scale // 2, cy + scale // 2], fill=(255, 255, 255))
    hx, hy = to_px(data["home"]["x"], data["home"]["y"])
    draw.line([hx - scale, hy, hx + scale, hy], fill=(255, 80, 80), width=2)
    draw.line([hx, hy - scale, hx, hy + scale], fill=(255, 80, 80), width=2)

    # legend and title
    lx = width + 10
    own = sum(1 for e in data.get("entities", []) if e.get("f") == data.get("force"))
    draw.text((lx, 6), f"{data.get('npc_name', 'NPC')}", fill=(255, 255, 255), font=font)
    draw.text((lx, 20), f"tick {data.get('tick')}", fill=(200, 200, 200), font=font)
    draw.text((lx, 34), f"{own} buildings", fill=(200, 200, 200), font=font)
    for i, (name, colour) in enumerate(LEGEND):
        y = 56 + i * 20
        draw.rectangle([lx, y, lx + 14, y + 14], fill=colour, outline=(20, 20, 20))
        draw.text((lx + 20, y + 2), name, fill=(230, 230, 230), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an NPC engineer's base from a saved game.")
    parser.add_argument("save", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--npc-id", type=int, default=1)
    parser.add_argument("--radius", type=int, default=96)
    parser.add_argument("--scale", type=int, default=8)
    parser.add_argument("--json", type=Path, help="also write the raw map dump here")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "tools"))
    from run_designed_start_test import SocketRcon
    from run_engine_tests import (ENGINE_DEFAULT, engine_args, prepare_profile, server_session,
                                  validate_engine)
    from companion.designer_bridge import parse_result

    engine = ENGINE_DEFAULT.resolve(strict=True)
    run_dir = ROOT / "tests" / "runs" / time.strftime("render-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    config, mods = prepare_profile(ROOT / "mod", run_dir, validate_engine(engine))
    save = run_dir / "profile" / "saves" / "render.zip"
    save.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.save.resolve(strict=True), save)
    with server_session(engine_args(engine, config, mods), save, run_dir, "render",
                        ROOT / "tests" / "server-settings.json") as sock:
        rcon = SocketRcon(sock)
        rcon.text("/npc-factions-test speed 1")
        command = "/npc-plan map " + json.dumps({"npc_id": args.npc_id, "radius": args.radius})
        deadline = time.monotonic() + 120
        data = parse_result(rcon.text(command))
        while data.get("reason") in ("generating", "building") and time.monotonic() < deadline:
            time.sleep(0.5)   # the game generates land / reads the map a few chunks per tick
            data = parse_result(rcon.text(command))
    if not data.get("ok"):
        print(f"map failed: {data.get('reason')}")
        return 1
    if args.json:
        args.json.write_text(json.dumps(data), encoding="utf-8")
    print(render(data, args.out, scale=args.scale))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
