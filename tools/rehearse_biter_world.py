"""A headless rehearsal of the Biter World live test: the save the user will host, served privately, the
7 engineers spawned (no player needed) and the attach runner's loop answering them for N minutes; then a
report of what happened.

    python tools/rehearse_biter_world.py --minutes 20            # catalog planner and scripted strategist: free
    python tools/rehearse_biter_world.py --minutes 20 --llm      # designer, System One and strategists (Codex)
    python tools/rehearse_biter_world.py --minutes 25 --research logistics --goal coal-supply
                                                                 # further along, one planner first

The report (report.json and report.txt in the run folder): plans accepted and refused per engineer,
buildings per engineer with turrets and walls, engineers alive and killed, biters killed, what the biters
destroyed, script errors in the server log, and the LLM cost.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import attach_runner as ar  # noqa: E402
import threading  # noqa: E402
from stall_watch import StallWatch  # noqa: E402
import biter_world as bw  # noqa: E402
import make_biter_world_save  # noqa: E402
import package_mod  # noqa: E402
from companion.designer_bridge import locked_log, make_planners  # noqa: E402

PROBE = """/sc local s=game.surfaces[1]
local out={npcs={},biters_killed=0,lost_to_biters={},loaders={}}
local fighters={unit=true,['unit-spawner']=true,turret=true,['segmented-unit']=true}
for i=1,10 do local f=game.forces['npc-faction-'..i] if f then
  local n={buildings=0,turrets=0,walls=0,loaders=0,alive=false}
  for _,e in ipairs(s.find_entities_filtered{force=f}) do
    if e.type=='loader' then n.loaders=n.loaders+1
      if #out.loaders<20 then local c=e.loader_container
        out.loaders[#out.loaders+1]={force=f.name,name=e.name,x=e.position.x,y=e.position.y,direction=e.direction,
          type=e.loader_type,container=c and c.name or nil} end end
    if e.type=='character' then n.alive=true else n.buildings=n.buildings+1
      if e.type=='ammo-turret' or e.type=='electric-turret' or e.type=='fluid-turret' or e.type=='artillery-turret'
        then n.turrets=n.turrets+1 end
      if e.type=='wall' or e.type=='gate' then n.walls=n.walls+1 end end end
  for name,c in pairs(f.get_kill_count_statistics(s).input_counts) do
    local p=prototypes.entity[name]
    if p and fighters[p.type] then out.biters_killed=out.biters_killed+c end end
  out.npcs[f.name]=n end end
for name,c in pairs(game.forces.enemy.get_kill_count_statistics(s).input_counts) do out.lost_to_biters[name]=c end
out.evolution=game.forces.enemy.get_evolution_factor(s)
out.tick=game.tick
rcon.print(helpers.table_to_json(out))"""


RESEARCH = """/sc local names=helpers.json_to_table('%s')
local done={}
local function finish(t) if t.researched then return end
  for _,p in pairs(t.prerequisites) do finish(p) end
  t.researched=true; done[t.name]=true end
for _,f in pairs(game.forces) do if f.name=='player' or f.name:find('^npc%%-faction%%-') then
  for _,n in ipairs(names) do local t=f.technologies[n] if t then finish(t) end end end end
local out={} for n in pairs(done) do out[#out+1]=n end table.sort(out)
rcon.print(helpers.table_to_json(out))"""


def research(probe, techs: list[str]) -> list[str]:
    """`techs` and everything they need researched for the team (the players and every engineer), so a
    rehearsal can start further along. Returns what was newly researched."""
    text = probe.text(RESEARCH % json.dumps(techs)).strip()
    return json.loads(text) if text.startswith("[") else []


def with_goal(build, goal: str):
    """Each engineer's planner, made to choose `goal` the first time it is free to choose a goal and that
    one can be built: a rehearsal of one planner on the real game (as a strategist asking for it)."""
    from companion.catalog_planner import available

    def make(npc_id: int):
        planner = build(npc_id)
        choose, given = planner.choose_purpose, []

        def choose_purpose(site):
            if not given and available(goal, site, designer=planner.designer is not None):
                given.append(goal)
                planner.log(f"{site.get('request_id')}: next goal {goal} (the rehearsal's --goal)")
                return goal
            return choose(site)
        planner.choose_purpose = choose_purpose
        return planner
    return make


def per_engineer(handled: dict) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for request_id, outcome in handled.items():
        npc = str(request_id).split(":", 1)[0]
        kind = "research" if str(outcome).startswith("research") else str(outcome)
        counts = out.setdefault(npc, {})
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(out.items()))


def summary_text(report: dict) -> str:
    lines = [f"Biter World rehearsal: {report['minutes']} min, game tick {report['game'].get('tick')}, "
             f"evolution {report['game'].get('evolution', 0):.3f}, strategist {report['strategist']}, "
             f"designer {'on' if report['llm'] else 'off'}"]
    for name, n in sorted((report["game"].get("npcs") or {}).items()):
        plans = report["plans"].get(name.rsplit("-", 1)[-1], {})
        lines.append(f"  {name}: {'alive' if n.get('alive') else 'DEAD'}, {n.get('buildings')} buildings "
                     f"({n.get('turrets')} turrets, {n.get('walls')} walls, {n.get('loaders', 0)} loaders), "
                     f"plans {plans}")
    for loader in report["game"].get("loaders") or []:
        lines.append(f"  {loader['force']} {loader['name']} at {loader['x']},{loader['y']} facing {loader['direction']}: "
                     f"{loader['type']}, {'into' if loader['type'] == 'input' else 'from'} "
                     f"{loader.get('container') or 'nothing'}")
    lost = report["game"].get("lost_to_biters") or {}
    lines.append(f"  biters killed {report['game'].get('biters_killed')}; lost to biters "
                 f"{sum(lost.values())} ({', '.join(f'{k} {v}' for k, v in sorted(lost.items())) or 'nothing'})")
    lines.append(f"  script errors: {len(report['script_errors'])}; LLM {report['cost']['calls']} calls, "
                 f"${report['cost']['cost']:.4f}")
    return "\n".join(lines)


def save_end(probe, run: Path) -> Path | None:
    """The game as it stood at the end, kept in the run (what a finding after the run is read back from)."""
    saved = run / "profile" / "saves" / "rehearsal-end.zip"
    probe.text("/save rehearsal-end")
    deadline, size = time.monotonic() + 120, -1
    while time.monotonic() < deadline:   # (written in the background: wait until it stops growing)
        time.sleep(3)
        now = saved.stat().st_size if saved.exists() else -1
        if now > 0 and now == size:
            return saved
        size = now
    return None


def rehearse(minutes: float, *, llm: bool = False, save: Path | None = None, run: Path | None = None,
             source: Path = bw.MOD_SOURCE, strategist: str | None = None, ai: str = "codex",
             techs: list[str] | None = None, goal: str | None = None, speed: float = 1.0,
             stall: str = "record") -> dict:
    run = run or ROOT / "tests" / "runs" / time.strftime("rehearsal-%Y%m%d-%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    os.environ["NPC_FACTIONS_DATA"] = str(run / "data")   # blueprints and experience stay in the run folder
    if save is None:
        save = ROOT / "dist" / f"{make_biter_world_save.SAVE_NAME}.zip"
        if not save.exists():
            make_biter_world_save.make(save, source=source, death_world=False)   # (a normal world: the user, 2026-10-30)
    mod_zip = package_mod.build(run / "dist")
    config, mods = bw.prepare_run(run, mod_zip, source)
    (run / "saves").mkdir(exist_ok=True)
    served = run / "saves" / save.name
    shutil.copy2(save, served)
    strategist = strategist or ("auto" if llm else "rules")
    log = locked_log(ar.make_log(run / "runner.log"))
    with bw.Server(run, config, mods, served) as server:
        probe = server.connect()
        try:
            spawned = bw.parse(probe.text("/npc-team spawn"), bw.TEAM_MARK)
            log(f"spawned: {spawned}")
            if speed != 1.0:   # (the game's own play runs the same; only the AI's thinking takes more game time)
                # (a silent command answers nothing, and RCON waits for an answer: it says the speed back)
                said = probe.text(f"/silent-command game.speed = {speed} rcon.print(game.speed)").strip()
                log(f"game speed {said}")
            if techs:
                log(f"researched at the start: {', '.join(research(probe, techs)) or 'nothing new'}")
            link = ar.Link(server.endpoint)
            planners = make_planners("catalog", log=log, designer=llm, chooser=llm, ai=ai,
                                     catalog=run / "data" / "blueprints")
            if goal:
                planners = with_goal(planners, goal)
            runner = ar.Runner(link, run, planners=planners, strategist=strategist, ai=ai, log=log,
                               experience=run / "data" / "experience")
            # (each engineer watched for a stall on a line of its own; seven of them: one stuck is recorded and the
            # game goes on, unless stall="stop")
            watch, done = StallWatch(run, server.log_path, mode=stall), threading.Event()

            def watching():
                line = server.connect()
                try:
                    while not done.wait(10):
                        for stuck in watch.look(line):
                            log(f"[stuck] {stuck['name']} {stuck['why']} ({stuck['phase']}, goal {stuck['goal']})")
                        if watch.stop:
                            (run / "stop-now").write_text("stuck", encoding="utf-8")
                            return
                finally:
                    line.close()
            watcher = threading.Thread(target=watching, daemon=True)
            watcher.start()
            runner.run(until=time.monotonic() + minutes * 60)
            done.set()
            watcher.join(timeout=30)   # (a look under way finishes before the report reads its stalls)
            game = json.loads(probe.text(PROBE).strip() or "{}")
            end_save = save_end(probe, run)
            log(f"end save: {end_save or 'not written'}")
        finally:
            probe.close()
        errors = server.errors()
    report = {"minutes": minutes, "llm": llm, "ai": ai if llm else None, "strategist": strategist, "save": str(save),
              "research": techs or [], "goal": goal,
              "spawned": spawned.get("spawned"), "end_save": str(end_save) if end_save else None,
              "plans": per_engineer(runner.bridge.handled), "stuck": watch.stalls,
              "game": game, "script_errors": errors[:20], "cost": runner.cost(),
              "cost_by_system": ar.usage.summary(ar.usage.entries(runner.mark))["systems"]}
    (run / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = summary_text(report)
    (run / "report.txt").write_text(text + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=20)
    parser.add_argument("--llm", action="store_true", help="layout designer, System One and LLM strategists")
    parser.add_argument("--ai", choices=["codex", "api"], default="codex",
                        help="with --llm: through Codex (your ChatGPT plan, the default) or the paid OpenAI API")
    parser.add_argument("--strategist", help="override: an OpenAI model, rules or off")
    parser.add_argument("--save", type=Path, help="a Biter World save (default dist/biter-world-5150.zip, made as a normal world if missing)")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--mods-from", type=Path, default=bw.MOD_SOURCE)
    parser.add_argument("--research", default="",
                        help="technologies (comma separated) researched for the team at the start, with what they need")
    parser.add_argument("--goal", help="a goal (catalog purpose) every engineer takes as soon as it can build it")
    parser.add_argument("--stall", choices=["record", "stop"], default="record",
                        help="an engineer stuck: written to stuck.json and the game goes on (record), or the run ends")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="game speed (2 runs two game minutes a real one; --minutes stays real minutes)")
    args = parser.parse_args()
    techs = [t.strip() for t in args.research.split(",") if t.strip()]
    report = rehearse(args.minutes, llm=args.llm, save=args.save, run=args.run_dir, source=args.mods_from,
                      strategist=args.strategist, ai=args.ai, techs=techs, goal=args.goal, speed=args.speed,
                      stall=args.stall)
    print(summary_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
