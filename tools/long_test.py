"""An hour-long headless test of one engineer on the Biter World save with the user's mods, the AI on through
Codex (designer, System One, strategist). The game runs fast while nothing waits on the AI and at real time
while a design or a thought is under way. Prints a progress line each real minute and [alert] lines for
script errors and stalls; stops at the game-time target, the real-time cap, or a `stop-now` file.

    python tools/long_test.py RUN_NAME [GAME_MINUTES=60] [REAL_CAP_MINUTES=90] [FAST=4] [AI=codex|rules]

Env: NPC_MODS_FROM=<a mods folder to copy> (tests/runs/mods-user-21: the user's mods for 2.1), NPC_SAVE=<the save>
(dist/biter-world-5150.zip); a fresh start takes its library from %LOCALAPPDATA%/npc-factions/blueprints. The
game serves RCON on 127.0.0.1:27131 and the game port 34331 (tools/biter_world.py): one run at a time, unless
NPC_GAME_PORT and NPC_RCON_PORT name others (a chat's own ports). NPC_TRACE=1 samples the engineer's job every ~2 game
seconds into the run's trace.jsonl (tools/trace_opening.py reads it). Stop a run
with a `stop-now` file in tests/runs/RUN_NAME. The harness stops on a death (in tests, reload a checkpoint).

Checkpoints: each milestone reached for the first time (a science automated, the bus, the silo, a launch)
saves the game and the engineer's library and experience to tests/runs/checkpoints/<milestone>. With
NPC_RESUME=<a checkpoint folder> a run starts from there (its save, library and experience; no team setup), made a
normal world on load as tools/proving.py makes it (the checkpoints were made on the death world).
"""
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # (the repo)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import attach_runner as ar  # noqa: E402
import biter_world as bw  # noqa: E402
import package_mod  # noqa: E402
from companion.designer_bridge import locked_log, make_planners  # noqa: E402

NAME = sys.argv[1] if len(sys.argv) > 1 else "long-1"
GAME_MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else 60
REAL_CAP = float(sys.argv[3]) if len(sys.argv) > 3 else 90
FAST = float(sys.argv[4]) if len(sys.argv) > 4 else 4
AI = sys.argv[5] if len(sys.argv) > 5 else "codex"
from stall_watch import StallWatch  # noqa: E402   (a stuck engineer ends the run, with stuck.json)

run = ROOT / "tests" / "runs" / NAME
if run.exists():
    shutil.rmtree(run)
(run / "data").mkdir(parents=True)
RESUME = Path(os.environ["NPC_RESUME"]) if os.environ.get("NPC_RESUME") else None
CHECKPOINTS = ROOT / "tests" / "runs" / "checkpoints"
achieved = set(json.loads((RESUME / "meta.json").read_text(encoding="utf-8")).get("achieved", [])) if RESUME else set()
if RESUME:   # the engineer's library and experience as they were at the checkpoint
    for part in ("blueprints", "experience"):
        if (RESUME / "data" / part).exists():
            shutil.copytree(RESUME / "data" / part, run / "data" / part)
else:
    shutil.copytree(Path(os.environ["LOCALAPPDATA"]) / "npc-factions" / "blueprints", run / "data" / "blueprints")
os.environ["NPC_FACTIONS_DATA"] = str(run / "data")
mod_zip = package_mod.build(run / "dist")
config, mods = bw.prepare_run(run, mod_zip, Path(os.environ["NPC_MODS_FROM"]))
(run / "saves").mkdir(exist_ok=True)
served = run / "saves" / "biter-world-5150.zip"
shutil.copy2(RESUME / "save.zip" if RESUME else Path(os.environ["NPC_SAVE"]), served)

STATE = ("/sc __npc-factions__ local out={tick=game.tick,speed=game.speed,npcs={}} "
         "for id,npc in pairs(storage.npcs or {}) do local d=npc.design local c=npc.character "
         "local names={} for _,p in ipairs(npc.design_history or {}) do names[#names+1]=p.purpose "
         "if p.base_plan and p.base_plan.stage then names[#names+1]=p.base_plan.stage end end "
         "for k in pairs(npc.retired or {}) do names[#names+1]=k end "
         "local techs={} for _,t in ipairs({'automation','logistic-science-pack','military-science-pack',"
         "'chemical-science-pack','production-science-pack','utility-science-pack','rocket-silo','space-science-pack'}) do "
         "if npc.force.technologies[t] and npc.force.technologies[t].researched then techs[#techs+1]=t end end "
         "out.npcs[#out.npcs+1]={id=id,name=npc.name,alive=c and c.valid or false,phase=npc.phase,"
         "purpose=d and d.purpose,status=d and d.status,stage=d and d.stage_index,"
         "stages=d and d.plan and #d.plan.stages or nil,history=#(npc.design_history or {}),names=names,techs=techs,"
         "rockets=npc.force.rockets_launched,respawns=npc.respawns or 0,"
         "built=(function() local n=0 for _,p in ipairs(npc.design_history or {}) do n=n+#(p.order or {}) end return n end)()} end "
         "rcon.print(helpers.table_to_json(out))")


def _has(*words):
    return lambda names, techs, rockets: any(any(w in str(n) for w in words) for n in names)


# First time each is true, the game is saved there (a run with a fix far into the game starts from the last).
MILESTONES = [
    ("red-science", _has("red-science")),
    ("green-science", _has("green-science")),
    ("military-science", _has("military-science")),
    ("bus", _has("bus-base")),
    ("blue-science", _has("blue-science", "chemical-science")),
    ("purple-science", _has("production-science", "purple-science")),
    ("yellow-science", _has("utility-science", "yellow-science")),
    ("rocket-silo", lambda names, techs, rockets: "rocket-silo" in techs),
    ("rocket", lambda names, techs, rockets: (rockets or 0) > 0),
]


def commit_id():
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return None


def checkpoint(probe, name, tick):
    """Save the game here, and keep it with the engineer's library and experience as they are now."""
    saved = run / "profile" / "saves" / f"cp-{name}.zip"
    probe.text(f"/save cp-{name}")
    deadline, size = time.monotonic() + 120, -1
    while time.monotonic() < deadline:   # (written in the background: wait until it stops growing)
        time.sleep(3)
        now = saved.stat().st_size if saved.exists() else -1
        if now > 0 and now == size:
            break
        size = now
    if not saved.exists():
        say(f"[alert] checkpoint {name}: the save did not appear")
        return
    target = CHECKPOINTS / name
    if target.exists():
        shutil.rmtree(target)
    (target / "data").mkdir(parents=True)
    shutil.copy2(saved, target / "save.zip")
    for part in ("blueprints", "experience"):
        if (run / "data" / part).exists():
            shutil.copytree(run / "data" / part, target / "data" / part)
    meta = {"milestone": name, "tick": tick, "game_minutes": round(tick / 3600, 1), "run": NAME,
            "commit": commit_id(), "achieved": sorted(achieved), "resumed_from": str(RESUME) if RESUME else None}
    (target / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    say(f"[checkpoint] {name} at game minute {tick / 3600:.1f} (tests/runs/checkpoints/{name})")


def say(line):
    print(time.strftime("%H:%M:%S ") + line, flush=True)


log = locked_log(ar.make_log(run / "runner.log"))
(run / "sites").mkdir(exist_ok=True)
SLOW_PLAN_S = 240   # a planner busy this long (no AI call that slow is expected): dump every thread's stack


def keep_sites(build):
    """Each engineer's planner, saving every request's site (to replay a slow or failed plan offline)."""
    def make(npc_id):
        planner = build(npc_id)
        inner = planner.__call__

        class Saving:
            def __getattr__(self, name):
                return getattr(planner, name)

            def __setattr__(self, name, value):   # (the bridge gives the planner its map reader this way)
                setattr(planner, name, value)

            def __call__(self, site):
                rid = str(site.get("request_id")).replace(":", "-")
                (run / "sites" / f"{rid}.json").write_text(json.dumps(site), encoding="utf-8")
                return inner(site)
        return Saving()
    return make


import faulthandler  # noqa: E402

PORTS = {"game_port": int(os.environ.get("NPC_GAME_PORT") or bw.GAME_PORT),
         "rcon_port": int(os.environ.get("NPC_RCON_PORT") or bw.RCON_PORT)}

with bw.Server(run, config, mods, served, **PORTS) as server:
    probe = server.connect()
    try:
        probe.text("/sc rcon.print(1)")   # (the first /sc on a save warns that achievements go)
        if RESUME:
            say(f"resumed from {RESUME.name} (achieved: {', '.join(sorted(achieved)) or 'nothing'})")
            # (a checkpoint made on the death world: a normal world now, the user 2026-10-30)
            say("normal world: " + json.dumps(bw.parse(probe.text("/npc-proving normal"), "NPC_PROVING_RESULT ")))
        else:
            say("one engineer: " + json.dumps(bw.parse(probe.text('/npc-team on {"count":1,"death_world":false}'),
                                                       bw.TEAM_MARK).get("count")))
            say("spawned: " + json.dumps(bw.parse(probe.text("/npc-team spawn"), bw.TEAM_MARK)))
        llm = AI != "rules"
        planners = keep_sites(make_planners("catalog", log=log, designer=llm, chooser=llm, ai="codex",
                                            catalog=run / "data" / "blueprints"))
        runner = ar.Runner(ar.Link(server.endpoint), run, planners=planners, strategist="auto" if llm else "rules",
                           ai="codex", log=log, experience=run / "data" / "experience")
        start = json.loads(probe.text(STATE).strip())["tick"]
        target = start + int(GAME_MINUTES * 3600)
        stop, alerts = threading.Event(), []

        def watch():
            speed, last_report, seen, changed_at = None, 0.0, None, start
            watch = StallWatch(run, server.log_path, mode="stop")
            busy_since, dumped, deaths_from = None, False, None
            with open(run / "progress.jsonl", "w", encoding="utf-8") as out:
                while not stop.is_set():
                    try:
                        agent = getattr(runner, "agent", None)
                        busy = runner.bridge.busy() or bool(agent and getattr(agent, "running", None))
                        planning = runner.bridge.busy()
                        busy_since = (busy_since or time.monotonic()) if planning else None
                        if planning and not dumped and time.monotonic() - busy_since > SLOW_PLAN_S:
                            with open(run / "stacks.txt", "w", encoding="utf-8") as f:
                                faulthandler.dump_traceback(f, all_threads=True)
                            say(f"[alert] a planner busy for {SLOW_PLAN_S} s: every thread's stack in stacks.txt")
                            dumped = True
                        want = 1.0 if busy else FAST
                        if want != speed:
                            probe.text(f"/sc game.speed={want} rcon.print(game.speed)")
                            speed = want
                        if time.monotonic() - last_report >= 60:
                            last_report = time.monotonic()
                            state = json.loads(probe.text(STATE).strip())
                            out.write(json.dumps(state) + "\n")
                            out.flush()
                            npc = (state.get("npcs") or [{}])[0]
                            now = (npc.get("phase"), npc.get("purpose"), npc.get("status"), npc.get("stage"),
                                   npc.get("history"), npc.get("built"))
                            if now != seen:
                                seen, changed_at = now, state["tick"]
                            minutes = (state["tick"] - start) / 3600
                            cost = runner.cost()
                            say(f"[progress] game {minutes:5.1f} min (at {state['tick'] / 3600:.1f}), speed "
                                f"{state.get('speed')}, {npc.get('phase')}, plan {npc.get('purpose')} {npc.get('status')} "
                                f"stage {npc.get('stage')}/{npc.get('stages')}, {npc.get('history')} plans done, "
                                f"AI {cost['calls']} calls")
                            # A death ends the run (the user, 2026-10-25: reload the last checkpoint with a
                            # fix, rather than carry on after it). (A respawn between two looks counts too.)
                            respawns = int(npc.get("respawns") or 0)
                            deaths_from = respawns if deaths_from is None else deaths_from
                            if not npc.get("alive") or respawns > deaths_from:
                                say(f"[alert] the engineer died (game minute {state['tick'] / 3600:.1f}): stopping; "
                                    f"resume from the last checkpoint once it is fixed")
                                alerts.append("died")
                                (run / "stop-now").write_text("died", encoding="utf-8")
                            for mname, reached in MILESTONES:
                                if mname not in achieved and reached(npc.get("names") or [], npc.get("techs") or [],
                                                                     npc.get("rockets")):
                                    achieved.add(mname)
                                    checkpoint(probe, mname, state["tick"])
                            for stall in watch.look(probe):
                                say(f"[alert] stuck: {stall['name']} {stall['why']} ({stall['phase']}, goal "
                                    f"{stall['goal']}): stuck.json")
                                alerts.append("stuck")
                            if watch.stop:
                                (run / "stop-now").write_text("stuck", encoding="utf-8")
                            errors = server.errors()
                            if errors:
                                say(f"[alert] script errors: {errors[:3]}")
                                alerts.append("script")
                                (run / "stop-now").write_text("script errors", encoding="utf-8")
                            if state["tick"] >= target:
                                say("[done] the game-time target is reached")
                                (run / "stop-now").write_text("target", encoding="utf-8")
                    except Exception as exc:   # a slow tick or a busy line: keep watching
                        say(f"[watch] {type(exc).__name__}: {str(exc)[:120]}")
                    stop.wait(2)
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        if os.environ.get("NPC_TRACE"):
            import trace_opening
            threading.Thread(target=trace_opening.sample, args=(server.connect, stop, run / "trace.jsonl"),
                             daemon=True).start()
        runner.run(until=time.monotonic() + REAL_CAP * 60)
        stop.set()
        watcher.join(timeout=30)
        final = json.loads(probe.text(STATE).strip())
    finally:
        probe.close()
    errors = server.errors()
cost = runner.cost()
systems = ar.usage.summary(ar.usage.entries(runner.mark)).get("systems")
handled = {}
for outcome in runner.bridge.handled.values():
    key = "research" if str(outcome).startswith("research") else str(outcome)
    handled[key] = handled.get(key, 0) + 1
report = {"game_minutes": (final["tick"] - start) / 3600, "final": final, "plans": handled, "cost": cost,
          "systems": systems, "script_errors": errors[:20], "alerts": alerts}
(run / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
say(f"[report] {report['game_minutes']:.1f} game minutes; plans {handled}; AI {cost}; script errors {len(errors)}")
say(f"[report] by system: {json.dumps(systems)[:900]}")
say("[report] final: " + json.dumps(final)[:600])
