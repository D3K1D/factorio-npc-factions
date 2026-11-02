"""Native test: an NPC requests a layout, then builds and runs it with its own materials.

Starts a private headless Factorio server in an isolated profile, creates the
"designed" fixture world, answers the NPC's layout request with the
deterministic starter planner over RCON (exactly as the companion would), and
waits until the fixture's checks pass or the time limit is reached.

    python tools/run_designed_start_test.py --mod-root mod
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from run_engine_tests import (ENGINE_DEFAULT, engine_args, invoke, json_write, prepare_profile,
                              recv_packet, send_packet, server_session, snapshots, validate_engine)
from run_record import RunRecord
from companion.designer_bridge import DesignerBridge, make_agent, make_planner

VARIANTS = {
    "a": "start automatic iron plates", "b": "moved ore, obstacles", "c": "four resources, goal loop",
    "d": "c plus water: power and research", "e": "d until automated red science", "f": "e plus plate belts",
    "g": "f plus green science", "h": "g plus steel and electric mining",
    "i": "no room at home: scout and build at a far patch", "j": "h plus oil and blue science",
    "k": "j with a far oil field and automated blue supply", "l": "k plus a main bus base",
    "m": "l with the bus extended for blue science", "n": "m and a rocket section: on to orbit",
    "o": "k with biter nests: defense", "p": "k raided by an armed engineer",
}


class SocketRcon:
    """Minimal text RCON over an already authenticated socket."""

    def __init__(self, sock):
        self.sock = sock
        self.next_id = 10
        self.lock = threading.Lock()   # one exchange at a time: designers fetch maps from their own threads

    def text(self, command: str) -> str:
        with self.lock:
            rid = self.next_id
            self.next_id += 1
            send_packet(self.sock, rid, 2, command)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                response_id, kind, payload = recv_packet(self.sock)
                if response_id == rid and kind == 0 and payload.strip():
                    return payload
            raise RuntimeError(f"No RCON response to {command.split()[0]}")


def fixture_snapshot(rcon: SocketRcon, command: str = "/npc-factions-test snapshot") -> dict:
    found = snapshots(rcon.text(command))
    if not found:
        raise RuntimeError("Fixture returned no snapshot")
    return found[-1]


def relay(rcon: SocketRcon, run_dir: Path) -> None:
    """Commands dropped into rcon-in.txt go to the server; what it says goes to rcon-out.txt."""
    inbox = run_dir / "rcon-in.txt"
    if not inbox.exists():
        return
    commands = [line.strip() for line in inbox.read_text(encoding="utf-8").splitlines() if line.strip()]
    inbox.unlink()
    with (run_dir / "rcon-out.txt").open("a", encoding="utf-8") as out:
        for command in commands:
            try:
                answer = rcon.text(command).strip()
            except Exception as exc:   # a command the server does not answer must not end the game
                answer = f"(no answer: {exc})"
            out.write(f"> {command}\n{answer}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", type=Path, default=ENGINE_DEFAULT)
    parser.add_argument("--mod-root", type=Path, default=ROOT / "mod")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--speed", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--timeout", type=int, default=900, help="wall-clock seconds")
    parser.add_argument("--planner", choices=["starter", "llm", "llm-only", "catalog"], default="starter",
                        help="llm/llm-only/catalog may call the paid designer API")
    parser.add_argument("--catalog", type=Path, help="blueprint library directory for --planner catalog")
    parser.add_argument("--no-designer", action="store_true")
    parser.add_argument("--no-chooser", action="store_true")
    parser.add_argument("--variant", choices=list(VARIANTS), default="a",
                        help="fixture terrain: " + ", ".join(f"{k} ({v})" for k, v in VARIANTS.items())
                             + "; n, o and p need --resume from a map-k save")
    parser.add_argument("--save-export", type=Path, help="write the first layout request export here")
    parser.add_argument("--save-when", help="save the game (as --save-name) the first time a log line matches "
                        "this regular expression, to resume late stages later")
    parser.add_argument("--save-name", default="checkpoint")
    parser.add_argument("--resume", type=Path, help="continue from a saved game instead of a fresh fixture "
                        "(the current mod code is loaded into it)")
    parser.add_argument("--overlap-probe", action="store_true",
                        help="add an exact duplicate and an overlapping pole to the first plan: the game should "
                        "merge one and drop the other instead of rejecting the plan")
    parser.add_argument("--render", type=Path, help="write a top-down picture of the base here at the end")
    parser.add_argument("--render-radius", type=int, default=64)
    parser.add_argument("--profile", action="store_true", help="log how long each part of the tick takes")
    parser.add_argument("--strategist", default="off",
                        help="off, rules (scripted stand-in) or an OpenAI model (e.g. gpt-6-luna; paid)")
    parser.add_argument("--purpose", help="what this run is for (shown in its replay)")
    parser.add_argument("--no-record", action="store_true", help="no in-game recording and no replay.html")
    parser.add_argument("--video", type=float, nargs="?", const=1.0, metavar="MINUTES",
                        help="real-graphics video in the replay: a frame save every MINUTES game minutes (default 1), "
                        "drawn afterwards by a graphical Factorio (a small window per frame) into MP4s")
    parser.add_argument("--video-later", action="store_true",
                        help="with --video: keep the frame saves, draw them later with tools/video.py <run dir>")
    args = parser.parse_args()

    engine = args.engine.resolve(strict=True)
    data = validate_engine(engine)
    run_dir = (args.run_dir or ROOT / "tests" / "runs" / time.strftime("designed-%Y%m%d-%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    config, mods = prepare_profile(args.mod_root.resolve(strict=True), run_dir, data)
    common = engine_args(engine, config, mods)
    save = run_dir / "profile" / "saves" / "designed-fresh.zip"
    save.parent.mkdir(parents=True, exist_ok=True)
    if args.resume:
        shutil.copyfile(args.resume.resolve(strict=True), save)
    else:
        invoke([*common, "--create", str(save), "--map-gen-settings", str(ROOT / "tests" / "map-gen-settings.json")],
               run_dir / "create.log", 120, run_dir / "profile")

    log_lines: list[str] = []
    saved: list[str] = []
    rcon_ref: list = []
    llm_planner = args.planner in ("llm", "llm-only") or (args.planner == "catalog" and not args.no_designer)
    record = RunRecord(run_dir, runner="designed start fixture (tools/run_designed_start_test.py)",
                       purpose=args.purpose or (f"Fixture map {args.variant}: {VARIANTS[args.variant]}"
                                                + (f" (resumed from {args.resume.name})" if args.resume else "")),
                       args=args, enabled=not args.no_record, video=args.video, video_render=not args.video_later,
                       engine=engine, systems={
                           "planner": args.planner, "designer LLM": llm_planner,
                           "System One (Decisions API)": args.planner == "catalog" and not args.no_chooser,
                           "strategist": args.strategist,
                           "System One habits": args.strategist not in ("off", "rules"),
                           "fixture map": args.variant, "resumed from": str(args.resume) if args.resume else None,
                           "game speed": args.speed})

    def log(message: str) -> None:
        log_lines.append(message)
        print(message, flush=True)
        if args.save_when and not saved and rcon_ref and re.search(args.save_when, message):
            rcon_ref[0].text(f"/npc-factions-test save {args.save_name}")
            saved.append(message)
            print(f"saved {save.parent / (args.save_name + '.zip')}", flush=True)
    log = record.wrap(log)

    with server_session(common, save, run_dir, "designed", ROOT / "tests" / "server-settings.json") as sock:
        rcon = SocketRcon(sock)
        rcon_ref.append(rcon)
        if not args.resume:
            started = rcon.text("/npc-factions-test start designed" + ("" if args.variant == "a" else args.variant))
            if "NPC_TEST_ERROR" in started:
                raise RuntimeError(started.strip())
        elif args.variant != "a":
            rcon.text(f"/npc-factions-test variant {args.variant}")
        # No cliffs for now (user, 2026-10-09): they only get in the way.
        rcon.text("/npc-factions-test nocliffs")
        record.start(rcon.text)
        if args.profile:
            rcon.text("/npc-factions-test profile")   # per-part tick timing in the server log
        # Normal speed until a plan is accepted: designer latency is wall-clock time.
        # A resumed game is already past its first plan.
        rcon.text(f"/npc-factions-test speed {args.speed if args.resume else 1}")
        sped_up = bool(args.resume)
        planner = make_planner(args.planner, log=log, catalog=args.catalog,
                               designer=not args.no_designer, chooser=not args.no_chooser)
        if args.overlap_probe:
            probed = planner

            def planner(site, inner=probed, done=[]):
                plan = inner(site)
                if not done and plan.get("entities") and plan.get("stages"):
                    done.append(True)
                    first = plan["entities"][0]
                    big = next((e for e in plan["entities"] if e["prototype"].endswith(("furnace", "drill"))), first)
                    extra = [dict(first, id="probe-duplicate"),
                             {"id": "probe-pole", "prototype": "small-electric-pole", "direction": 0,
                              "position": dict(big["position"])}]
                    plan["entities"] = plan["entities"] + extra
                    plan["stages"][0]["entity_ids"] = list(plan["stages"][0]["entity_ids"]) + [e["id"] for e in extra]
                    log(f"overlap probe: duplicated {first['id']}, pole on {big['id']}")
                return plan
        if args.save_export:
            inner = planner
            def planner(site, inner=inner):
                if not args.save_export.exists():
                    json_write(args.save_export, site)
                return inner(site)
        slowed = []

        def slow_down(request):
            # Designer latency is wall-clock time: plan at normal game speed.
            rcon.text("/npc-factions-test speed 1")
            slowed.append(request.get("request_id"))

        bridge = DesignerBridge(rcon, planner, log, on_request=slow_down)
        agent = make_agent(args.strategist, rcon.text, log=log)
        deadline = time.monotonic() + args.timeout
        last_report, snapshot = 0.0, {}
        while time.monotonic() < deadline:
            bridge.poll_once()
            if agent:
                agent.poll_once()
            if slowed:
                rcon.text(f"/npc-factions-test speed {args.speed}")
                slowed.clear()
            if not sped_up and bridge.handled:
                rcon.text(f"/npc-factions-test speed {args.speed}")
                sped_up = True
            snapshot = fixture_snapshot(rcon)
            record.tick(snapshot.get("tick"))
            record.poll(rcon.text)
            if snapshot.get("completed"):
                break
            # Touch <run dir>/stop-now to end the run cleanly (the game server is shut down with it).
            if (run_dir / "stop-now").exists():
                log("stopped on request (stop-now)")
                break
            relay(rcon, run_dir)   # commands dropped into <run dir>/rcon-in.txt (answers in rcon-out.txt)
            # Touch <run dir>/save-now to save the game mid-run (now-<tick>.zip, beside the run's save).
            if (run_dir / "save-now").exists():
                (run_dir / "save-now").unlink()
                name = f"now-{snapshot.get('tick')}"
                rcon.text(f"/npc-factions-test save {name}")
                log(f"saved {save.parent / (name + '.zip')}")
            # Touch <run dir>/render-now for a picture of the base mid-run (written to now.png).
            trigger = run_dir / "render-now"
            if trigger.exists():
                trigger.unlink()
                from render_base import render
                planning_radius, bridge.MAP_RADIUS = bridge.MAP_RADIUS, args.render_radius
                try:
                    dump = bridge.world({"npc_id": 1})
                finally:
                    bridge.MAP_RADIUS = planning_radius   # plans must keep seeing the same world
                if dump.get("ok"):
                    json_write(run_dir / "now.json", dump)
                    log(f"picture {render(dump, run_dir / 'now.png')}")
            if time.monotonic() - last_report > 10:
                state = snapshot.get("state", {})
                failing = [k for k, c in snapshot.get("checks", {}).items() if not c.get("passed")]
                log(f"tick {snapshot.get('tick')}: {state.get('phase')} | plates {state.get('plates')} "
                    f"| cleared {state.get('cleared')} "
                    f"| failing: {', '.join(failing)}")
                last_report = time.monotonic()
            time.sleep(0.5)
        bridge.poll_once()  # record the final outcome in the blueprint library
        observed = bridge.call("/npc-plan observe")
        record.stop(rcon.text)
        if args.render:
            from render_base import render
            bridge.MAP_RADIUS = args.render_radius
            dump = bridge.world({"npc_id": 1})   # waits while far land is generated
            if dump.get("ok"):
                print(f"picture {render(dump, args.render)}", flush=True)
                json_write(run_dir / "map.json", dump)

    report = {"completed": snapshot.get("completed") is True, "snapshot": snapshot,
              "plan_status": observed.get("status"), "bridge": bridge.handled, "log": log_lines}
    json_write(run_dir / "results.json", report)
    checks = snapshot.get("checks", {})
    for name, check in sorted(checks.items()):
        print(f"  {'PASS' if check.get('passed') else 'FAIL'} {name} {json.dumps(check.get('detail')) if check.get('detail') is not None else ''}")
    print(("PASS" if report["completed"] else "FAIL") + f": designed start; report {run_dir / 'results.json'}")
    state = snapshot.get("state", {})
    record.finish({"passed": report["completed"],
                   "reason": None if report["completed"] else
                   f"{sum(1 for c in checks.values() if not c.get('passed'))} checks failing",
                   "checks": checks, "summary": {"phase at the end": state.get("phase"), "plates": state.get("plates"),
                                                 "plan status": json.dumps(observed.get("status"))[:300],
                                                 "plans handled": len(bridge.handled)}})
    return 0 if report["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
