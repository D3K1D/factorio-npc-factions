"""Native test: build a generated module in a sandbox with endless inputs and measure its output.

Each input port gets a loader fed by an infinity chest, the output port empties
into one, a fluid port gets an infinity pipe full of its fluid, and an electric
energy interface powers the module. After the run
the tool reports every recipe's crafts against the module's target rate and
draws a picture.

    python tools/run_module_test.py green_circuits 3.0 [--seconds 120] [--picture out.png]
    python tools/run_module_test.py mall [--seconds 300]      (passes when every machine crafts)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from companion import modules  # noqa: E402
from companion.ratios import Book  # noqa: E402
from render_base import render  # noqa: E402
from run_engine_tests import (ENGINE_DEFAULT, engine_args, invoke, prepare_profile, recv_packet,  # noqa: E402
                              send_packet, server_session, validate_engine)

BUILD = """
local s=game.surfaces[1] local f=game.forces.player
for _,t in pairs(f.technologies) do t.researched=true end
local spec=helpers.json_to_table(%s)
local area={{-12,-8},{spec.w+12,spec.h+8}}
for _,e in ipairs(s.find_entities_filtered{area=area}) do if e.type~='character' then e.destroy() end end
local tiles={} for x=area[1][1],area[2][1] do for y=area[1][2],area[2][2] do tiles[#tiles+1]={name='grass-1',position={x,y}} end end
s.set_tiles(tiles)
local fails={}
for _,e in ipairs(spec.entities) do
  local made=s.create_entity{name=e.prototype,position=e.position,direction=e.direction,force=f,type=e.belt_to_ground_type}
  if not made then fails[#fails+1]=e.id elseif e.recipe then made.set_recipe(e.recipe)
    if e.direction and made.direction~=e.direction then pcall(function() made.direction=e.direction end) end end
  if made and e.bar then made.get_inventory(defines.inventory.chest).set_bar(e.bar+1) end
end
local step={[0]={0,-1},[4]={1,0},[8]={0,1},[12]={-1,0}}
for _,p in ipairs(spec.ports) do
  if p.kind=='ext' then
    local d=step[p.direction]
    s.create_entity{name='transport-belt',position={p.x+0.5,p.y+0.5},direction=p.direction,force=f}
    s.create_entity{name='loader',position={p.x+0.5-1.5*d[1],p.y+0.5-1.5*d[2]},direction=p.direction,type='output',force=f}
    local chest=s.create_entity{name='infinity-chest',position={p.x+0.5-3*d[1],p.y+0.5-3*d[2]},force=f}
    chest.set_infinity_container_filter(1,{name=p.item,count=100,mode='exactly'})
  elseif p.kind=='fluid' or p.kind=='fluid-out' then
    local d=step[p.direction]
    local pipe=s.create_entity{name='infinity-pipe',position={p.x+d[1]+0.5,p.y+d[2]+0.5},force=f}
    pipe.set_infinity_pipe_filter{name=p.item,percentage=p.kind=='fluid' and 1 or 0,mode='exactly'}
  else
  local chest=s.create_entity{name='infinity-chest',position={p.x-2.5,p.y+0.5},force=f}
  if p.kind=='in' then
    chest.set_infinity_container_filter(1,{name=p.item,count=100,mode='exactly'})
    s.create_entity{name='loader',position={p.x-1,p.y+0.5},direction=defines.direction.east,type='output',force=f}
  else
    chest.remove_unfiltered_items=true
    s.create_entity{name='loader',position={p.x-1,p.y+0.5},direction=defines.direction.west,type='input',force=f}
  end
  end
end
for _,sup in ipairs(spec.supplies or {}) do
  local e=s.find_entities_filtered{name=sup.name,position={sup.x,sup.y},radius=0.5}[1]
  if e then e.insert{name=sup.item,count=100} end
end
s.create_entity{name='electric-energy-interface',position={spec.power.x-9,spec.power.y},force=f}
s.create_entity{name='small-electric-pole',position={spec.power.x-6,spec.power.y},force=f}
storage.module_test={t0=game.tick,w=spec.w,h=spec.h}
game.speed=8
rcon.print('BUILT '..helpers.table_to_json(fails))
"""

REPORT = """
local s=game.surfaces[1] local p=storage.module_test
local names={} for k,v in pairs(defines.entity_status) do names[v]=k end
local out={ticks=game.tick-p.t0,machines={},inserters={},silos={}}
for _,e in ipairs(s.find_entities_filtered{area={{0,0},{p.w,p.h}},type='rocket-silo'}) do
  out.silos[#out.silos+1]={parts=e.rocket_parts,status=e.rocket_silo_status,rockets=e.products_finished}
end
for _,e in ipairs(s.find_entities_filtered{area={{0,0},{p.w,p.h}},type={'assembling-machine','furnace'}}) do
  local r=e.get_recipe() local k=r and r.name
  if not k and e.type=='furnace' then local ok,pr=pcall(function() return e.previous_recipe end) k=ok and pr and (type(pr.name)=='string' and pr.name or pr.name.name) end
  k=k or 'none'
  local m=out.machines[k] or {count=0,crafts=0,status={}}
  m.count=m.count+1 m.crafts=m.crafts+e.products_finished m.status[names[e.status] or tostring(e.status)]=true
  m.fluids=m.fluids or {}
  for i=1,#e.fluidbox do
    local f=e.fluidbox[i] local c=#e.fluidbox.get_connections(i)
    m.fluids[#m.fluids+1]=(f and (f.name..' '..math.floor(f.amount)) or 'empty')..' links '..c
  end
  out.machines[k]=m
end
for _,e in ipairs(s.find_entities_filtered{area={{0,0},{p.w,p.h}},type='inserter'}) do
  local k=names[e.status] or tostring(e.status) out.inserters[k]=(out.inserters[k] or 0)+1
end
local ents={}
for _,e in ipairs(s.find_entities_filtered{area={{-12,-4},{p.w+2,p.h+2}}}) do
  if e.type~='character' and e.type~='resource' and e.type~='tree' then
    local rec=nil if e.type=='assembling-machine' and e.get_recipe() then rec=e.get_recipe().name end
    ents[#ents+1]={n=e.name,t=e.type,x=e.position.x,y=e.position.y,d=e.direction,w=e.prototype.tile_width,
      h=e.prototype.tile_height,f='player',r=rec}
  end
end
out.dump={area={-12,-4,p.w+2,p.h+2},home={x=-12,y=-4},force='player',npc_name='module test',tick=game.tick,entities=ents}
rcon.print('MODULE_TEST '..helpers.table_to_json(out))
"""


def _command(sock, lua: str, marker: str) -> str:
    """Run a console Lua command (sent twice: the first only acknowledges the achievement warning)."""
    sock.settimeout(30)
    one_line = " ".join(line.strip() for line in lua.strip().splitlines())
    last = ""
    for rid in (31, 32):
        send_packet(sock, rid, 2, "/sc " + one_line)
        while True:
            got, _, payload = recv_packet(sock)
            if got == rid:
                if marker in payload:
                    return payload
                last = payload
                break
    raise RuntimeError(f"no {marker}: {last[:400]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("module", help="a module function in companion.modules, e.g. green_circuits, or mall")
    parser.add_argument("rate", type=float, nargs="?", default=0.0, help="target items per second")
    parser.add_argument("--seconds", type=float, default=120, help="game seconds to run")
    parser.add_argument("--picture", type=Path)
    parser.add_argument("--space-age", action="store_true")
    args = parser.parse_args()

    book = Book.vanilla(args.space_age)
    if args.module.startswith("mall"):
        from companion.mall import mall
        module = mall(book, part=args.module.split(":")[1] if ":" in args.module else None)
    elif args.module == "steel_direct":
        # The direct-insertion steel column: ore and coal arrive by belt where the planner's routes
        # end (loaders behind them), and the steel belt runs on west along a row to an output chest.
        from companion.modules import Placed, Port, WEST
        from companion.smelting import steel_column, steel_pairs
        module = steel_column(steel_pairs(args.rate))
        bottom = module.height
        module.entities += [Placed("transport-belt", x + 0.5, bottom + 0.5, WEST) for x in range(0, 9)]
        module.ports = [Port(p.item, "ext", p.x, p.y, p.direction, p.rate) for p in module.ports if p.kind == "in"] + \
            [Port("steel-plate", "out", 0, bottom, WEST, module.makes["steel-plate"])]
        module.height = bottom + 1
    else:
        from companion import blue_modules, growth, military_modules, oil_complex, rocket_modules
        source = next(s for s in (modules, blue_modules, rocket_modules, oil_complex, growth, military_modules)
                      if hasattr(s, args.module))
        module = getattr(source, args.module)(args.rate, book)
    poles = sorted((e.x, e.y) for e in module.entities if e.name == "small-electric-pole")
    spec = {"w": module.width, "h": module.height, "entities": module.plan(), "ports": [p.__dict__ for p in module.ports],
            "power": {"x": poles[0][0], "y": poles[0][1]},
            "supplies": [{"name": s["entity"][0], "x": s["entity"][1], "y": s["entity"][2], "item": s["item"]}
                         for s in getattr(module, "supplies", [])]}
    engine = ENGINE_DEFAULT.resolve(strict=True)
    run_dir = ROOT / "tests" / "runs" / time.strftime(f"module-{args.module}-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    config, mods = prepare_profile(ROOT / "mod", run_dir, validate_engine(engine), space_age=args.space_age)
    common = engine_args(engine, config, mods)
    save = run_dir / "profile" / "saves" / "module.zip"
    save.parent.mkdir(parents=True, exist_ok=True)
    invoke([*common, "--create", str(save), "--map-gen-settings", str(ROOT / "tests" / "map-gen-settings.json")],
           run_dir / "create.log", 180, run_dir / "profile")
    with server_session(common, save, run_dir, "module", ROOT / "tests" / "server-settings.json") as sock:
        built = _command(sock, BUILD % json.dumps(json.dumps(spec)), "BUILT")
        failed = json.loads(built[built.find("BUILT ") + 6:])
        time.sleep(args.seconds / 8 + 3)
        text = _command(sock, REPORT, "MODULE_TEST ")
    out = json.loads(text[text.find("MODULE_TEST ") + 12:])
    seconds = out["ticks"] / 60
    product, target = next(iter(module.makes.items()))
    made = out["machines"].get(product, {}).get("crafts", 0)  # module recipes are named after their product
    print(f"{module.name}: {module.counts()}")
    for recipe, m in out["machines"].items():
        print(f"  {m['count']} x {recipe:<26} {m['crafts']:>5} crafts, status {sorted(m['status'])}")
        if m.get("fluids"):
            print(f"      fluid boxes: {m['fluids'][:8]}")
    print(f"  inserters: {out['inserters']}")
    for silo in out["silos"]:
        print(f"  silo: {silo}")
    rate = made * book.recipes[product].makes(product) / seconds if product in book.recipes else 0.0
    if out["silos"]:   # a silo counts parts until a rocket is ready, then whole rockets
        silo = out["silos"][0]
        rate = (silo["parts"] + 50 * silo["rockets"]) / seconds
    print(f"  {product}: {rate:.2f}/s over {seconds:.0f} s (target {target:.2f}/s)")
    if failed:
        print(f"  could not place: {failed}")
    if args.picture:
        print(render(out["dump"], args.picture, scale=16, crop=False))
    if args.module.startswith("mall"):
        idle = [r for r in module.makes if out["machines"].get(r, {}).get("crafts", 0) == 0]
        print(f"  mall: {len(module.makes) - len(idle)} of {len(module.makes)} machines crafted"
              + (f"; idle: {idle}" if idle else ""))
        return 0 if not failed and not idle else 1
    return 0 if not failed and rate >= 0.85 * target else 1


if __name__ == "__main__":
    raise SystemExit(main())
