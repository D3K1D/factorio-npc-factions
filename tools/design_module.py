"""Design lab: ask the LLM designer for a bus module, measure it in the sandbox, keep it if it works.

    python tools/design_module.py green_circuits 3.0 [--library DIR] [--seconds 180] [--picture out.png]

The designer gets the ratio calculator's machine counts and the built-in
module's ports (see companion/module_designer.py). Prototype facts and recipes
come from the sandbox game itself. A design that makes at least 85% of its goal
in the sandbox is saved to the module library, where the bus planner prefers
it over the built-in module. This calls the paid designer API (OPENAI_API_KEY).
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

from companion import module_designer as md  # noqa: E402
from companion.module_library import ModuleLibrary  # noqa: E402
from companion.ratios import Book  # noqa: E402
from render_base import render  # noqa: E402
from run_engine_tests import (ENGINE_DEFAULT, engine_args, invoke, prepare_profile,  # noqa: E402
                              server_session, validate_engine)
from run_module_test import BUILD, REPORT, _command  # noqa: E402

FACTS = """
local out={prototypes={},recipes={}}
local function v(p) return {p.x or p[1],p.y or p[2]} end
local function keys(t) local o={} for k in pairs(t or {}) do o[#o+1]=k end table.sort(o) return o end
for _,name in ipairs(helpers.json_to_table(%s)) do
  local p=prototypes.entity[name]
  if p then
    local item=p.items_to_place_this and p.items_to_place_this[1]
    local f={type=p.type,width=p.tile_width,height=p.tile_height,placement_item=item and (item.name or item[1]),
      energy_source=p.electric_energy_source_prototype and 'electric' or 'none'}
    if p.crafting_categories then f.crafting_categories=keys(p.crafting_categories) end
    if p.type=='inserter' then f.pickup_vector=v(p.inserter_pickup_position) f.drop_vector=v(p.inserter_drop_position) end
    if p.type=='electric-pole' then f.supply_area_distance=p.get_supply_area_distance() f.max_wire_distance=p.get_max_wire_distance() end
    out.prototypes[name]=f
  end
end
for name,r in pairs(game.forces.player.recipes) do
  if r.category=='crafting' or r.category=='advanced-crafting' or r.category=='basic-crafting' then
    local ing,prod={},{}
    for _,i in ipairs(r.ingredients) do if i.type=='item' then ing[#ing+1]={name=i.name,amount=i.amount} end end
    for _,i in ipairs(r.products) do if i.type=='item' then prod[#prod+1]={name=i.name,amount=i.amount or 1} end end
    out.recipes[#out.recipes+1]={name=name,category=r.category,handcraft=true,ingredients=ing,products=prod}
  end
end
rcon.print('FACTS '..helpers.table_to_json(out))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=sorted(md.KIND_ITEM))
    parser.add_argument("rate", type=float, help="target items per second")
    parser.add_argument("--library", type=Path, help="module library folder (default: the user's data folder)")
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--picture", type=Path)
    parser.add_argument("--model", default=md.dp.DEFAULT_MODEL)
    args = parser.parse_args()

    book = Book.vanilla()
    engine = ENGINE_DEFAULT.resolve(strict=True)
    run_dir = ROOT / "tests" / "runs" / time.strftime(f"design-{args.kind}-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    config, mods = prepare_profile(ROOT / "mod", run_dir, validate_engine(engine))
    common = engine_args(engine, config, mods)
    save = run_dir / "profile" / "saves" / "lab.zip"
    save.parent.mkdir(parents=True, exist_ok=True)
    invoke([*common, "--create", str(save), "--map-gen-settings", str(ROOT / "tests" / "map-gen-settings.json")],
           run_dir / "create.log", 180, run_dir / "profile")
    with server_session(common, save, run_dir, "lab", ROOT / "tests" / "server-settings.json") as sock:
        _command(sock, "for _,t in pairs(game.forces.player.technologies) do t.researched=true end "
                       "rcon.print('RESEARCHED')", "RESEARCHED")
        text = _command(sock, FACTS % json.dumps(json.dumps(list(md.PROTOTYPES))), "FACTS ")
        facts = json.loads(text[text.find("FACTS ") + 6:])
        designer = md.ModuleDesigner(model=args.model)
        try:
            module = designer(args.kind, args.rate, book, facts["prototypes"], facts["recipes"])
        except md.DesignError as exc:
            print(f"no usable design: {exc}")
            return 1
        print(f"design: {module.counts()}")
        spec = {"w": module.width, "h": module.height, "entities": module.plan(),
                "ports": [p.__dict__ for p in module.ports], "power": {"x": 0.5, "y": md.power_row(
                    md.reference(args.kind, args.rate, book), module.height) + 0.5}}
        built = _command(sock, BUILD % json.dumps(json.dumps(spec)), "BUILT")
        failed = json.loads(built[built.find("BUILT ") + 6:])
        time.sleep(args.seconds / 8 + 3)
        text = _command(sock, REPORT, "MODULE_TEST ")
    out = json.loads(text[text.find("MODULE_TEST ") + 12:])
    seconds = out["ticks"] / 60
    item = md.KIND_ITEM[args.kind]
    recipe = book.recipes[item]
    made = out["machines"].get(item, {}).get("crafts", 0) * recipe.makes(item) / seconds
    for name, m in out["machines"].items():
        print(f"  {m['count']} x {name:<26} {m['crafts']:>5} crafts, status {sorted(m['status'])}")
    print(f"  {item}: {made:.2f}/s over {seconds:.0f} s (goal {args.rate:.2f}/s); could not place: {failed}")
    if args.picture:
        print(render(out["dump"], args.picture, scale=16, crop=False))
    if failed or made < 0.85 * args.rate:
        print("not kept")
        return 1
    key = ModuleLibrary(args.library).save(args.kind, module, made,
                                           {"source": "designer-llm", "model": args.model, "goal": args.rate,
                                            "usage": designer.usage})
    print(f"kept as {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
