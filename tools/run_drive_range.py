"""The driving range: engineers with a car (or a tank) each drive trip after trip at full speed.

Trips are laid out at random from a seed, so a batch is never the same course twice:

    python tools/run_drive_range.py --mode course --trips 24 --drivers 4
    python tools/run_drive_range.py --mode terrain --trips 24 --vehicle tank
    python tools/run_drive_range.py --mode course --walled --trips 16

"--walled" puts a wall ring with one door round each course's start and parks the car inside by the door: the
trip goes out through the door and home again to park (M-1).

"course" clears the land along each trip and puts down rock fields, tree patches, a belt of trees with
gaps, a wall of rocks with a narrow gap, lakes and a dead-end bay at random; "terrain" drives across the
map as generated (no biters on either). Only the drivers tick (no factories, no strategist), so it runs
as fast as the game can. Prints each trip as it ends and a summary: how many arrived, how long they took
against walking straight there, how often the car stuck. report.json keeps every trip, with the last
drive events of those that did not arrive. Touch <run dir>/stop-now to end early.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from run_designed_start_test import SocketRcon, relay  # noqa: E402
from run_drive_lab import dev  # noqa: E402
from run_engine_tests import (ENGINE_DEFAULT, engine_args, invoke, json_write, prepare_profile,  # noqa: E402
                              server_session, validate_engine)

ERRORS = re.compile(r"Error|attempt to|stack traceback|bad argument", re.IGNORECASE)


def trip_line(res: dict) -> str:
    line = (f"  trip {res.get('n'):>3}: {res.get('outcome'):<14} {res.get('length', 0):>5} tiles"
            f" in {res.get('ticks', 0) / 60:6.0f} s  driven {res.get('driven', 0):>5}  stuck {res.get('stuck', 0)}"
            f"  clears {res.get('clears', 0)}  no-way {res.get('no_way', 0)}  routes {res.get('routes', 0)}")
    if res.get("enemies"):
        line += (f"  | biters {res.get('enemies')}: vehicle hp {res.get('vehicle_hp')}, driver {res.get('driver_hp')},"
                 f" near {res.get('threat_s')} s at {res.get('threat_speed')}, shooting {res.get('shooting_s')} s")
    if res.get("mends") or res.get("mend_breaks"):
        line += f"  | mended {res.get('mends')} (broken off {res.get('mend_breaks')}), packs {res.get('packs_used')}"
    if res.get("outcome") not in ("arrived", None):
        line += f"  ended {res.get('gap')} tiles short"
    w = res.get("walled")
    if w:
        line += (f"  | door {w.get('door')}: through {w.get('through_door')}, tightest {w.get('tightest')},"
                 f" walls hit {w.get('walls_hurt')}, parked inside {w.get('parked_inside')}")
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", type=Path, default=ENGINE_DEFAULT)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--mode", choices=("course", "terrain"), default="course")
    parser.add_argument("--vehicle", choices=("car", "tank"), default="car")
    parser.add_argument("--drivers", type=int, default=4)
    parser.add_argument("--trips", type=int, default=24)
    parser.add_argument("--length", type=int, default=600, help="tiles from start to goal")
    parser.add_argument("--density", type=float, default=1.0, help="how much is in the way on a course")
    parser.add_argument("--biters", type=float, default=0,
                        help="biters on a course (nests, worms, groups in the way; 1 the usual) and on the map")
    parser.add_argument("--repair", type=int, default=20, help="repair packs a trip (0 none)")
    parser.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    parser.add_argument("--cliffs", action="store_true", help="terrain with cliffs")
    parser.add_argument("--walled", action="store_true", help="a wall ring with a door round each course's start")
    parser.add_argument("--speed", type=int, default=100)
    parser.add_argument("--minutes", type=float, default=20, help="wall-clock minutes at most")
    parser.add_argument("--only", type=int, nargs="+", help="only these trips of the batch (as laid out in it)")
    parser.add_argument("--profile", action="store_true", help="log how long each route plan takes")
    parser.add_argument("--hold", type=int, default=0,
                        help="seconds to keep the game up after the trips (rcon-in.txt relay, to look about)")
    args = parser.parse_args()

    run_dir = args.run_dir or ROOT / "tests" / "runs" / time.strftime("range-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    engine = args.engine.resolve(strict=True)
    config, mods = prepare_profile(ROOT / "mod", run_dir, validate_engine(engine), space_age=True)
    common = engine_args(engine, config, mods)
    world = {"seed": args.seed, "no_enemies_mode": not args.biters, "peaceful_mode": not args.biters,
             "cliff_settings": {"name": "cliff", "richness": 1 if args.cliffs else 0}}
    if not args.biters:
        world["autoplace_controls"] = {"enemy-base": {"frequency": 0, "size": 0, "richness": 0}}
    world_file = run_dir / "map-gen-settings.json"
    world_file.write_text(json.dumps(world), encoding="utf-8")
    save = run_dir / "profile" / "saves" / "world.zip"
    save.parent.mkdir(parents=True, exist_ok=True)
    invoke([*common, "--create", str(save), "--map-gen-settings", str(world_file)], run_dir / "create.log", 300,
           run_dir / "profile")

    lines: list[str] = []

    def log(message: str) -> None:
        lines.append(message)
        print(message, flush=True)

    spec = {"drivers": args.drivers, "trips": args.trips, "seed": args.seed, "mode": args.mode,
            "vehicle": args.vehicle, "length": args.length, "density": args.density, "biters": args.biters,
            "repair": args.repair, "walled": args.walled}
    if args.only:
        spec["only"] = args.only
    results: list[dict] = []
    summary: dict = {}
    started = time.monotonic()
    with server_session(common, save, run_dir, "range", ROOT / "tests" / "server-settings.json") as sock:
        sock.settimeout(60)   # at full speed a tick laying out a course may take a moment
        rcon = SocketRcon(sock)
        made = dev(rcon, "drive-range " + json.dumps(spec))
        if not made.get("ok"):
            log(f"range not set up: {made}")
            return 1
        log(f"range: {args.trips} {args.mode} trips of {args.length} tiles, {args.drivers} drivers, "
            f"{args.vehicle}, seed {args.seed}")
        dev(rcon, f"speed {args.speed}")
        if args.profile:
            dev(rcon, "profile on")
        deadline = started + args.minutes * 60
        while time.monotonic() < deadline:
            summary = dev(rcon, f"drive-range-status {len(results) + 1}")
            for res in summary.get("results") or []:
                results.append(res)
                log(trip_line(res))
            if summary.get("finished") and len(results) >= summary.get("total", 0):
                break
            if (run_dir / "stop-now").exists():
                log("stopped on request (stop-now)")
                break
            relay(rcon, run_dir)
            if not summary.get("results"):
                time.sleep(2)
        summary = dev(rcon, f"drive-range-status {len(results) + 1}")
        for res in summary.get("results") or []:
            results.append(res)
            log(trip_line(res))
        if args.hold:
            log(f"holding {args.hold} s: commands in {run_dir / 'rcon-in.txt'}")
            dev(rcon, "speed 1")
            held = time.monotonic() + args.hold
            while time.monotonic() < held and not (run_dir / "stop-now").exists():
                relay(rcon, run_dir)
                time.sleep(0.5)
    server_log = (run_dir / "range.log").read_text(encoding="utf-8", errors="replace") \
        if (run_dir / "range.log").exists() else ""
    errors = [line for line in server_log.splitlines() if ERRORS.search(line) and "npc-factions" in line]
    report = {"spec": spec, "tick": summary.get("tick"), "done": summary.get("done"), "total": summary.get("total"),
              "arrived": summary.get("arrived"), "outcomes": summary.get("outcomes"),
              "walk_ratio": summary.get("walk_ratio"), "stuck_per_trip": summary.get("stuck_per_trip"),
              "wrecked": summary.get("wrecked"), "drivers_hurt": summary.get("drivers_hurt"),
              "vehicle_hp": summary.get("vehicle_hp"), "mends": summary.get("mends"),
              "packs_used": summary.get("packs_used"),
              "spent_percent": summary.get("spent"),
              "wall_seconds": round(time.monotonic() - started), "script_errors": errors[:40], "trips": results}
    json_write(run_dir / "report.json", report)
    done = summary.get("done") or 0
    log(f"arrived {summary.get('arrived')}/{done} ({summary.get('outcomes')}); trip time {summary.get('walk_ratio')}x "
        f"walking straight; stuck {summary.get('stuck_per_trip')} a trip; {report['wall_seconds']} s; "
        f"{len(errors)} script errors; report {run_dir / 'report.json'}")
    log(f"time spent (% of all trips): {summary.get('spent')}")
    if args.biters:
        log(f"among biters: {summary.get('wrecked')} vehicles wrecked, {summary.get('drivers_hurt')} drivers hurt, "
            f"vehicle health at its lowest {summary.get('vehicle_hp')} on average; mended {summary.get('mends')} times, "
            f"{summary.get('packs_used')} repair packs")
    return 0 if done and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
