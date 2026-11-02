"""A top-down picture of a running headless game's base (the headless server renders nothing, so this draws one
from the live map, as the game's map view does): the engineer's plot and a margin round it, ore, water and
cliffs, every building coloured by kind, walls and turrets, nests and biters, the engineer.

    python tools/world_shot.py RUN OUT_DIR [EVERY_GAME_MINUTES=15]   (keeps drawing while the run lives)
    python tools/world_shot.py RUN OUT_DIR once

The RCON password is read from the run folder, never printed."""
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]   # (the repo)
sys.path.insert(0, str(ROOT))
from companion.rcon import RconClient  # noqa: E402

RUN = ROOT / "tests" / "runs" / (sys.argv[1] if len(sys.argv) > 1 else "long-35")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
EVERY = sys.argv[3] if len(sys.argv) > 3 else "15"
PORT = 27131        # (tools/biter_world.py RCON_PORT)
MARGIN = 40          # tiles drawn beyond the plot
SCALE = 3            # pixels a tile

SKIP = ("tree", "resource", "simple-entity", "fish", "item-entity", "corpse", "character-corpse", "decorative",
        "particle-source", "explosion", "smoke-with-trigger", "fire", "stream", "projectile", "beam",
        "highlight-box", "flying-text", "speech-bubble", "item-request-proxy", "entity-ghost", "tile-ghost",
        "deconstructible-tile-proxy", "simple-entity-with-owner", "arrow", "sticker", "rail-remnants")
ENTITIES = r"""/sc local s=game.surfaces[1] local skip={} for _,t in ipairs(SKIP) do skip[t]=true end
local out={} for _,e in ipairs(s.find_entities_filtered{area={{X1,Y1},{X2,Y2}}}) do
if not skip[e.type] then local b=e.bounding_box
out[#out+1]=e.type..','..e.name..','..e.force.name..','..string.format('%.1f,%.1f,%.1f,%.1f',b.left_top.x,b.left_top.y,b.right_bottom.x,b.right_bottom.y)
end end rcon.print(table.concat(out,';'))"""
RESOURCES = r"""/sc local s=game.surfaces[1] local by={} for _,e in ipairs(s.find_entities_filtered{area={{X1,Y1},{X2,Y2}},type='resource'}) do
local t=by[e.name] or {} by[e.name]=t t[#t+1]=math.floor(e.position.x)..','..math.floor(e.position.y) end
local o={} for k,v in pairs(by) do o[#o+1]=k..'='..table.concat(v,' ') end rcon.print(table.concat(o,'|'))"""
WATER = r"""/sc local s=game.surfaces[1] local o={} for _,t in ipairs(s.find_tiles_filtered{area={{X1,Y1},{X2,Y2}},collision_mask='water_tile'}) do
o[#o+1]=t.position.x..','..t.position.y end rcon.print(table.concat(o,' '))"""
STATE = r"""/sc __npc-factions__ local npc=storage.npcs and storage.npcs[1] local c=npc and npc.character
local p=c and c.valid and c.position or nil
rcon.print(helpers.table_to_json({tick=game.tick,x=p and p.x,y=p and p.y,phase=npc and npc.phase,
  purpose=npc and npc.design and npc.design.purpose,stage=npc and npc.design and npc.design.plan and npc.design.plan.stages
  and npc.design.plan.stages[npc.design.stage_index or 1] and npc.design.plan.stages[npc.design.stage_index or 1].id}))"""

ORE = {"iron-ore": (88, 120, 160), "copper-ore": (170, 98, 52), "coal": (20, 20, 22), "stone": (150, 132, 96),
       "crude-oil": (150, 70, 170), "uranium-ore": (80, 170, 60)}
GROUND, WATER_C, CLIFF = (44, 41, 33), (36, 62, 104), (110, 92, 58)
KIND = [  # (test on type/name, colour, label for the legend)
    (lambda t, n: t in ("transport-belt", "underground-belt", "splitter", "loader", "loader-1x1"), (236, 190, 52), "belts"),
    (lambda t, n: t == "inserter", (170, 205, 225), "inserters"),
    (lambda t, n: t == "furnace", (235, 110, 30), "furnaces"),
    (lambda t, n: t == "mining-drill" and n.startswith("burner"), (160, 90, 40), "burner drills"),
    (lambda t, n: t == "mining-drill", (60, 140, 235), "electric drills"),
    (lambda t, n: t == "assembling-machine", (95, 165, 210), "assemblers"),
    (lambda t, n: t in ("boiler", "generator", "offshore-pump", "solar-panel", "accumulator"), (205, 205, 215), "power"),
    (lambda t, n: t == "lab", (230, 230, 255), "labs"),
    (lambda t, n: t in ("container", "logistic-container"), (180, 130, 70), "chests"),
    (lambda t, n: t in ("pipe", "pipe-to-ground", "storage-tank", "pump"), (100, 130, 165), "pipes"),
    (lambda t, n: t == "electric-pole", (130, 130, 130), "poles"),
    (lambda t, n: t == "wall", (205, 205, 205), "walls"),
    (lambda t, n: t == "gate", (240, 240, 150), "gates"),
    (lambda t, n: t in ("ammo-turret", "electric-turret", "fluid-turret", "artillery-turret"), (235, 60, 60), "turrets"),
    (lambda t, n: t == "radar", (120, 240, 120), "radar"),
    (lambda t, n: True, (175, 175, 190), "other"),
]
ENEMY_SPAWNER, ENEMY_WORM, ENEMY_UNIT = (255, 30, 30), (255, 120, 120), (255, 80, 80)


def q(client, lua, box):
    x1, y1, x2, y2 = box
    lua = lua.replace("SKIP", "{" + ",".join(f"'{t}'" for t in SKIP) + "}")
    lua = lua.replace("X1", str(x1)).replace("Y1", str(y1)).replace("X2", str(x2)).replace("Y2", str(y2))
    return client.text(lua.replace("\n", " "))


def site_note():
    """From the companion's newest site snapshot: hand-fed burners and the share of time carrying coal."""
    try:
        f = max((RUN / "sites").glob("*.json"), key=lambda p: p.stat().st_mtime)
        s = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return ""
    from companion import catalog_planner as cp
    shares = ((s.get("time_use") or {}).get("shares") or {})
    coal = sum(v for k, v in shares.items() if k.startswith("supply:") and k.endswith(":coal"))
    hand = sum(sum(int((b.get("machines") or {}).get(k, 0)) for k in ("burner-mining-drill", "stone-furnace", "boiler"))
               for b in s.get("base") or [] if str(b.get("purpose")) not in cp.BELT_FED)
    return f"hand-fed burners {hand}, time carrying coal {coal:.0%}"


def draw(client, team_plot, box=None, scale=SCALE):
    """The picture of `box` (map tiles; default the plot and MARGIN round it), `scale` pixels a tile; and the box
    round the engineer's own buildings."""
    px0, py0, px1, py1 = team_plot
    box = box or (px0 - MARGIN, py0 - MARGIN, px1 + MARGIN, py1 + MARGIN)
    SCALE = scale
    w, h = (box[2] - box[0]) * SCALE, (box[3] - box[1]) * SCALE
    img = Image.new("RGB", (w, h + 70), GROUND)
    d = ImageDraw.Draw(img)

    def rect(x1, y1, x2, y2, colour, outline=None):
        a = ((x1 - box[0]) * SCALE, (y1 - box[1]) * SCALE)
        b = ((x2 - box[0]) * SCALE - 1, (y2 - box[1]) * SCALE - 1)
        if b[0] >= a[0] and b[1] >= a[1]:
            d.rectangle([a, b], fill=colour, outline=outline)
    for part in q(client, RESOURCES, box).split("|"):
        if "=" not in part:
            continue
        name, tiles = part.split("=", 1)
        colour = ORE.get(name, (120, 120, 60))
        for t in tiles.split():
            x, y = (int(v) for v in t.split(","))
            rect(x, y, x + 1, y + 1, colour)
    for t in q(client, WATER, box).split():
        x, y = (int(float(v)) for v in t.split(","))
        rect(x, y, x + 1, y + 1, WATER_C)
    rows = [r.split(",") for r in q(client, ENTITIES, box).split(";") if r]
    counts = {}
    mine = None
    own = []
    for r in rows:
        kind, name, force = r[0], r[1], r[2]
        x1, y1, x2, y2 = (float(v) for v in r[3:7])
        if kind == "cliff":
            rect(round(x1), round(y1), max(round(x2), round(x1) + 1), max(round(y2), round(y1) + 1), CLIFF)
            continue
        if force == "enemy":
            colour = ENEMY_SPAWNER if kind == "unit-spawner" else ENEMY_WORM if kind == "turret" else ENEMY_UNIT
            if kind == "unit":
                cx, cy = ((x1 + x2) / 2 - box[0]) * SCALE, ((y1 + y2) / 2 - box[1]) * SCALE
                d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=colour)
            else:
                rect(round(x1), round(y1), round(x2), round(y2), colour)
            counts["enemy " + kind] = counts.get("enemy " + kind, 0) + 1
            continue
        if kind == "character":
            continue
        mine = mine or force if force.startswith("npc-faction") else mine
        colour, label = next((c, lab) for test, c, lab in KIND if test(kind, name))
        if not force.startswith("npc-faction"):
            colour = tuple(int(v * 0.6) for v in colour)   # (others' buildings, dimmed)
        else:
            counts[label] = counts.get(label, 0) + 1
            own.append((x1, y1, x2, y2))
        rect(round(x1), round(y1), max(round(x2), round(x1) + 1), max(round(y2), round(y1) + 1), colour)
    # the plot's edge
    d.rectangle([((px0 - box[0]) * SCALE, (py0 - box[1]) * SCALE), ((px1 - box[0]) * SCALE, (py1 - box[1]) * SCALE)],
                outline=(255, 255, 255), width=1)
    state = json.loads(client.text(STATE.replace("\n", " ")))
    if state.get("x") is not None:
        cx, cy = (state["x"] - box[0]) * SCALE, (state["y"] - box[1]) * SCALE
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(255, 255, 255), width=2)
        d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 255, 255))
    try:
        font = ImageFont.truetype("arial.ttf", 15)
        small = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = small = ImageFont.load_default()
    minute = state.get("tick", 0) / 3600
    doing = state.get("purpose") or "-"
    if state.get("stage"):
        doing += f" / {state['stage']}"
    d.rectangle([(0, h), (w, h + 70)], fill=(18, 18, 18))
    d.text((8, h + 6), f"Run {RUN.name}, game minute {minute:.0f}.  Building: {doing}.  {site_note()}",
           fill=(255, 255, 255), font=font)
    x = 8
    for test, colour, label in KIND[:-1]:
        d.rectangle([(x, h + 34), (x + 12, h + 46)], fill=colour)
        d.text((x + 16, h + 32), f"{label} {counts.get(label, 0)}", fill=(220, 220, 220), font=small)
        x += 26 + int(d.textlength(f"{label} {counts.get(label, 0)}", font=small))
        if x > w - 120:
            break
    d.text((8, h + 52), "white ring: the engineer.  white line: its plot.  red: nests, worms, biters.  blue: iron ore, "
           "orange: copper, black: coal, tan: stone, green: uranium, purple: oil, dark blue: water, brown: cliffs",
           fill=(170, 170, 170), font=small)
    built = (min(b[0] for b in own), min(b[1] for b in own), max(b[2] for b in own), max(b[3] for b in own)) if own else None
    return img, minute, built


def main():
    secret = (RUN / "rcon-password.txt").read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True)
    plot = (-576, -576, -192, -192)
    try:
        sites = sorted((RUN / "sites").glob("*.json"), key=lambda p: p.stat().st_mtime)
        if sites:   # (the site's plot is in its own frame: round its origin)
            s = json.loads(sites[-1].read_text(encoding="utf-8"))
            t, o = s.get("team") or {}, s.get("origin") or {"x": 0, "y": 0}
            if t.get("plot"):
                x0, y0, x1, y1 = (int(v) for v in t["plot"])
                plot = (x0 + int(o["x"]), y0 + int(o["y"]), x1 + int(o["x"]), y1 + int(o["y"]))
    except Exception:
        pass
    last = None
    while True:
        try:
            client = RconClient("127.0.0.1", PORT, secret, timeout=60)
            try:
                tick = json.loads(client.text(STATE.replace("\n", " ")))["tick"]
                if EVERY == "once" or last is None or tick - last >= float(EVERY) * 3600:
                    img, minute, built = draw(client, plot)
                    path = OUT / f"min-{minute:03.0f}-plot.png"
                    img.save(path)
                    print(f"saved {path}", flush=True)
                    if built:   # the base up close: its buildings and 16 tiles round them
                        x0, y0, x1, y1 = (int(built[0]) - 16, int(built[1]) - 16, int(built[2]) + 16, int(built[3]) + 16)
                        scale = max(4, min(12, 1300 // max(x1 - x0, y1 - y0)))
                        img, _, _ = draw(client, plot, (x0, y0, x1, y1), scale)
                        path = OUT / f"min-{minute:03.0f}-base.png"
                        img.save(path)
                        print(f"saved {path}", flush=True)
                    last = tick
            finally:
                client.close()
        except (OSError, TimeoutError, ValueError, ConnectionError) as exc:
            if not (RUN / "rcon-password.txt").exists() or (RUN / "stop-now").exists():
                break
            print(f"(no picture this time: {str(exc)[:80]})", flush=True)
        if EVERY == "once":
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
