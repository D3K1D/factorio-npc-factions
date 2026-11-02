"""Native smoke run of a normal game: several engineers in designed mode, everything switched on.

Unlike the fixture tests (which switch off society, combat and the social layer), this starts a
fresh world with native starting areas and biters, spawns the roster the way a player's arrival
would, answers the engineers' plan requests with the catalog planner (no paid APIs), runs at speed
and reports what each engineer is doing every minute, plus any script errors in the server log.

    python tools/run_world_smoke.py --bots 3 --minutes 60

--live plays it with you: a random world at normal speed with every system on (designer, System One,
strategist), saved every 10 minutes; join with the join.cmd it writes (your Factorio, only this
game's mods, connected to the local server). Lines in <run dir>/rcon-in.txt are sent to the server
(results in rcon-out.txt); touch <run dir>/stop-now to save and stop.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from native_start_world import map_settings  # noqa: E402
from run_designed_start_test import SocketRcon, relay  # noqa: E402
from run_record import RunRecord  # noqa: E402
from run_engine_tests import (ENGINE_DEFAULT, engine_args, invoke, json_write, prepare_profile,  # noqa: E402
                              server_session, validate_engine)
from companion.decisions_api import api_key_from_environment  # noqa: E402
from companion.designer_bridge import DesignerBridge, locked_log, make_agent, make_planners  # noqa: E402

ERRORS = re.compile(r"Error|attempt to|stack traceback|bad argument", re.IGNORECASE)


def dev(rcon: SocketRcon, command: str) -> dict:
    text = rcon.text("/npc-dev " + command)
    at = text.rfind("NPC_DEV_RESULT ")
    return json.loads(text[at + len("NPC_DEV_RESULT "):].strip()) if at >= 0 else {"raw": text[:200]}


def score_line(n: dict) -> str:
    """One engineer's scorecard in a line."""
    if n.get("dead"):
        return f"  {n.get('name')}: dead"
    d, f, p = n.get("drills") or {}, n.get("furnaces") or {}, n.get("power") or {}
    made = n.get("produced") or {}
    return (f"  {n['name']} @{n.get('minutes')}m idle {n.get('idle_pct')}% | made iron {made.get('iron-plate', 0)} "
            f"copper {made.get('copper-plate', 0)} red {made.get('automation-science-pack', 0)} green "
            f"{made.get('logistic-science-pack', 0)} | drills b{d.get('burner')}/e{d.get('electric')} thin "
            f"{d.get('thin')} empty {d.get('empty')} nofuel {d.get('no_fuel')} | loops {n.get('coal_loops')} | "
            f"furnaces {f.get('total')} full {f.get('out_full')} nofuel {f.get('no_fuel')} | power pumps "
            f"{p.get('pumps')} boilers {p.get('boilers')} engines {p.get('engines_working')}/{p.get('engines')} "
            f"users {p.get('consumers')} poles {p.get('poles')} | asm {(n.get('assemblers') or {}).get('total')} "
            f"labs {(n.get('labs') or {}).get('total')} research {n.get('researched')} | milestones "
            f"{n.get('milestones')} | carrying {n.get('carrying')} | {str(n.get('phase'))[:40]}")


def write_join(engine: Path, run_dir: Path, mods: Path, endpoint: str, log) -> None:
    """join.cmd: your Factorio with only this game's mods (your own mod list is left alone), connected."""
    client_mods = run_dir / "client-mods"
    if client_mods.exists():
        shutil.rmtree(client_mods)
    shutil.copytree(mods, client_mods)
    join = run_dir / "join.cmd"
    # %~dp0: the folder join.cmd is in, wherever it is started from.
    join.write_text(f'@echo off\r\nstart "" "{engine}" --mod-directory "%~dp0client-mods" --mp-connect {endpoint}\r\n',
                    encoding="utf-8")
    log(f"live game at {endpoint}; join with {join}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", type=Path, default=ENGINE_DEFAULT)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--bots", type=int, default=3)
    parser.add_argument("--spacing", type=int, default=512)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--minutes", type=float, default=30, help="wall-clock minutes to run")
    parser.add_argument("--speed", type=int, default=8)
    parser.add_argument("--catalog", type=Path, default=ROOT / "tests" / "runs" / "library-smoke")
    parser.add_argument("--strategist", default="off",
                        help="off, rules (scripted stand-in) or an OpenAI model (e.g. gpt-6-luna; paid)")
    parser.add_argument("--experience", type=Path, default=ROOT / "tests" / "runs" / "experience-smoke")
    parser.add_argument("--profile", action="store_true", help="log how long each part of the tick takes")
    parser.add_argument("--live", action="store_true",
                        help="play along: random seed, speed 1, all systems on, join.cmd for your client")
    parser.add_argument("--warlord", type=int, default=0,
                        help="this engineer (by id) is at war with everyone from the start, silent, military first")
    parser.add_argument("--purpose", help="what this run is for (shown in its replay)")
    parser.add_argument("--no-record", action="store_true", help="no in-game recording and no replay.html")
    parser.add_argument("--video", type=float, nargs="?", const=1.0, metavar="MINUTES",
                        help="real-graphics video in the replay: a frame save every MINUTES game minutes (default 1), "
                        "drawn afterwards by a graphical Factorio (a small window per frame) into MP4s")
    parser.add_argument("--video-later", action="store_true",
                        help="with --video: keep the frame saves, draw them later with tools/video.py <run dir>")
    args = parser.parse_args()
    if args.live:
        if args.seed == parser.get_default("seed"):
            args.seed = random.randrange(1, 2**32)
        if args.speed == parser.get_default("speed"):
            args.speed = 1
        if args.minutes == parser.get_default("minutes"):
            args.minutes = 600
        if args.strategist == "off":
            args.strategist = "gpt-6-luna"
        if args.experience == parser.get_default("experience"):
            args.experience = None          # the engineers' own experience, kept across games

    run_dir = args.run_dir or ROOT / "tests" / "runs" / time.strftime("smoke-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    engine = args.engine.resolve(strict=True)
    config, mods = prepare_profile(ROOT / "mod", run_dir, validate_engine(engine), space_age=True)
    common = engine_args(engine, config, mods)
    world = map_settings(args.seed, args.bots, args.spacing, 2.0)
    world_file = run_dir / "map-gen-settings.json"
    world_file.write_text(json.dumps(world), encoding="utf-8")
    save = run_dir / "profile" / "saves" / "world.zip"
    save.parent.mkdir(parents=True, exist_ok=True)
    invoke([*common, "--create", str(save), "--map-gen-settings", str(world_file)], run_dir / "create.log", 300,
           run_dir / "profile")
    args.catalog.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    has_key = bool(api_key_from_environment())
    purpose = args.purpose or (
        f"{'Live game with you' if args.live else 'Smoke run'}: {args.bots} engineers on a fresh native world "
        f"(seed {args.seed}, {args.spacing} tiles apart), everything on"
        + (f", engineer {args.warlord} a warlord" if args.warlord else ""))
    record = RunRecord(run_dir, runner="world smoke (tools/run_world_smoke.py)", purpose=purpose, args=args,
                       enabled=not args.no_record, video=args.video, video_render=not args.video_later,
                       engine=engine, systems={
                           "start mode": "designed", "planner": "catalog (blueprint library)",
                           "designer LLM": args.live and has_key, "System One (Decisions API)": args.live and has_key,
                           "strategist": args.strategist,
                           "System One habits": args.strategist not in ("off", "rules") and has_key,
                           "experience": str(args.experience) if args.experience else "the engineers' own, kept across games",
                           "warlord": args.warlord or None, "engineers": args.bots, "game speed": args.speed,
                           "live (you join)": args.live})

    def log(message: str) -> None:
        lines.append(message)
        print(message, flush=True)
    log = locked_log(record.wrap(log))   # (the engineers' designers log from their own threads)

    settings = ROOT / "tests" / "server-settings.json"
    if args.live:
        live = json.loads(settings.read_text(encoding="utf-8"))
        live.update(name="NPC factions (local)", description="Play along with the NPC engineers",
                    max_players=4, autosave_interval=10, autosave_slots=6, autosave_only_on_server=True)
        settings = run_dir / "server-settings.json"
        settings.write_text(json.dumps(live, indent=1), encoding="utf-8")
    connection: dict = {}
    with server_session(common, save, run_dir, "smoke", settings, connection_info=connection) as sock:
        rcon = SocketRcon(sock)
        if args.live:
            write_join(engine, run_dir, mods, connection["endpoint"], log)
        log("start mode: " + rcon.text("/npc-plan mode designed").strip()[:80])
        configured = dev(rcon, "configure " + json.dumps({"bots": args.bots, "native_starts": True,
                                                         "spacing": args.spacing}))
        log(f"configured: bots={configured.get('bots')} native={configured.get('native_starts')}")
        record.start(rcon.text)   # after configure: the recording keeps the settings the run uses
        started = dev(rcon, "start-native")
        log(f"spawned: {len(started.get('npcs') or [])} engineers" + (f" ({started.get('reason')})"
                                                                       if started.get("reason") else ""))
        if args.warlord:
            made = dev(rcon, "warlord " + json.dumps({"id": args.warlord}))
            log(f"warlord: {made.get('name')} ({'at war with everyone, silent' if made.get('ok') else made.get('reason')})")
        dev(rcon, f"speed {args.speed}")
        if args.profile:
            dev(rcon, "profile on")
        # every engineer its own designer, all at work at once
        planners = make_planners("catalog", log=log, catalog=args.catalog, designer=args.live, chooser=args.live)
        slowed: list = []

        def slow_down(request):
            # Planning takes wall-clock time: plan at normal game speed, or the game moves on first.
            dev(rcon, "speed 1")
            slowed.append(request.get("request_id"))

        bridge = DesignerBridge(rcon, log=log, on_request=slow_down if args.speed > 1 else None, planners=planners)
        agent = make_agent(args.strategist, rcon.text, log=log, experience=args.experience)
        deadline = time.monotonic() + args.minutes * 60
        last = 0.0
        status: dict = {}
        scorecards: list = []
        next_card = 10 * 3600
        while time.monotonic() < deadline:
            bridge.poll_once()
            if slowed and not bridge.busy():   # back to speed once every designer is done
                dev(rcon, f"speed {args.speed}")
                slowed.clear()
            if agent:
                agent.poll_once()
            if (run_dir / "stop-now").exists():
                log("stopped on request (stop-now)")
                break
            relay(rcon, run_dir)
            record.poll(rcon.text)
            if time.monotonic() - last > 60:
                status = dev(rcon, "status")
                record.tick(status.get("tick"), status.get("speed"))
                if (status.get("tick") or 0) >= next_card:
                    card = dev(rcon, "scorecard")
                    scorecards.append(card)
                    next_card = (int(status["tick"]) // 36000 + 1) * 36000
                    for n in card.get("npcs") or []:
                        log(score_line(n))
                parts = []
                for npc in status.get("npcs") or []:
                    alive = npc.get("position") is not None
                    d = npc.get("design") or {}
                    parts.append(f"{npc.get('name')}: {'' if alive else 'DEAD '}{str(npc.get('phase'))[:40]} "
                                 f"[{d.get('status')} {d.get('purpose')} plans={d.get('plans')}]")
                tokens = getattr(getattr(agent, "strategist", None), "tokens", None)
                if tokens and tokens.get("calls"):
                    parts.append(f"strategist {tokens['calls']} calls, {tokens['input']}+{tokens['output']} tokens")
                log(f"tick {status.get('tick')}: " + " | ".join(parts))
                last = time.monotonic()
            time.sleep(0.5)
        bridge.close()
        status = dev(rcon, "status")
        record.stop(rcon.text)
        if args.live:
            log("saving: " + rcon.text("/server-save").strip()[:120])
            time.sleep(5)
    server_log = (run_dir / "smoke.log").read_text(encoding="utf-8", errors="replace") \
        if (run_dir / "smoke.log").exists() else ""
    errors = [line for line in server_log.splitlines() if ERRORS.search(line) and "npc-factions" in line]
    npcs = status.get("npcs") or []
    report = {"bots": args.bots, "tick": status.get("tick"), "alive": sum(1 for n in npcs if n.get("position")),
              "phases": {n.get("name"): n.get("phase") for n in npcs},
              "plans_accepted": sum(1 for line in lines if line.endswith("accepted")),
              "planner_failed": [line for line in lines if "planner failed" in line][:20],
              "script_errors": errors[:40], "victory": status.get("victory"),
              "scorecards": scorecards,
              "strategist": {"decisions": sum(1 for line in lines if "] -> " in line),
                             "tokens": getattr(getattr(agent, "strategist", None), "tokens", None)}}
    json_write(run_dir / "report.json", report)
    log(json.dumps({k: v for k, v in report.items() if k != "victory"}, indent=1)[:4000])
    ok = report["alive"] == args.bots and not errors
    log(("PASS" if ok else "FAIL") + f": world smoke; report {run_dir / 'report.json'}")
    record.finish({"passed": ok, "reason": None if ok else ("script errors" if errors else "an engineer died"),
                   "summary": {"engineers alive": f"{report['alive']}/{args.bots}", "game tick": report["tick"],
                               "plans accepted": report["plans_accepted"],
                               "planner failures": len(report["planner_failed"]), "script errors": len(errors),
                               "strategist decisions": report["strategist"]["decisions"]}})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
