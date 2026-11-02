"""The drive lab: you drive about a map while what you do is written down, to learn from.

    python tools/run_drive_lab.py --run-dir tests/runs/lab1

Starts a local server on a fresh map (trees, rocks, lakes, biters; no cliffs unless --cliffs; no
engineers) and writes join.cmd: your Factorio with only this game's mods, connected. When you join,
a car with coal (more in its trunk, its gun loaded) is put beside you, with a submachine gun,
magazines and repair packs in your pockets, and a chest of a tank, shells, better fuels and more ammo.
About fifteen times a second while you are in a vehicle the mod writes down where you are, your heading
and speed, your keys, health, fuel, what is ahead of you (trees, rocks, cliffs, water), the biters
about and whether you shoot; and every hit the vehicle takes and everything it breaks.
Touch <run dir>/stop-now (or let --minutes run out) to save and stop; the recording is copied to
<run dir>/drive_lab.jsonl.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from run_designed_start_test import SocketRcon, relay  # noqa: E402
from run_engine_tests import (ENGINE_DEFAULT, engine_args, invoke, prepare_profile,  # noqa: E402
                              server_session, validate_engine)


def dev(rcon: SocketRcon, command: str) -> dict:
    text = rcon.text("/npc-dev " + command)
    at = text.rfind("NPC_DEV_RESULT ")
    return json.loads(text[at + len("NPC_DEV_RESULT "):].strip()) if at >= 0 else {"raw": text[:300]}


def write_join(engine: Path, run_dir: Path, mods: Path, endpoint: str) -> Path:
    """join.cmd: your Factorio with only this game's mods (your own mod list is left alone), connected."""
    client_mods = run_dir / "client-mods"
    if client_mods.exists():
        shutil.rmtree(client_mods)
    shutil.copytree(mods, client_mods)
    join = run_dir / "join.cmd"
    join.write_text(f'@echo off\r\nstart "" "{engine}" --mod-directory "%~dp0client-mods" --mp-connect {endpoint}\r\n',
                    encoding="utf-8")
    return join


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", type=Path, default=ENGINE_DEFAULT)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--cliffs", action="store_true")
    parser.add_argument("--no-biters", action="store_true")
    parser.add_argument("--minutes", type=float, default=180, help="wall-clock minutes at most")
    args = parser.parse_args()

    run_dir = args.run_dir or ROOT / "tests" / "runs" / time.strftime("lab-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    engine = args.engine.resolve(strict=True)
    config, mods = prepare_profile(ROOT / "mod", run_dir, validate_engine(engine), space_age=True)
    common = engine_args(engine, config, mods)
    seed = args.seed if args.seed is not None else random.randrange(1, 2**32)
    world = {"seed": seed, "cliff_settings": {"name": "cliff", "richness": 1 if args.cliffs else 0},
             "no_enemies_mode": args.no_biters, "peaceful_mode": False}
    if args.no_biters:
        world["autoplace_controls"] = {"enemy-base": {"frequency": 0, "size": 0, "richness": 0}}
    world_file = run_dir / "map-gen-settings.json"
    world_file.write_text(json.dumps(world), encoding="utf-8")
    save = run_dir / "profile" / "saves" / "world.zip"
    save.parent.mkdir(parents=True, exist_ok=True)
    invoke([*common, "--create", str(save), "--map-gen-settings", str(world_file)], run_dir / "create.log", 300,
           run_dir / "profile")
    settings = json.loads((ROOT / "tests" / "server-settings.json").read_text(encoding="utf-8"))
    settings.update(name="NPC factions drive lab (local)", description="Drive about; it is written down",
                    max_players=4, autosave_interval=10, autosave_slots=3, autosave_only_on_server=True)
    settings_file = run_dir / "server-settings.json"
    settings_file.write_text(json.dumps(settings, indent=1), encoding="utf-8")

    def log(message: str) -> None:
        print(message, flush=True)

    connection: dict = {}
    with server_session(common, save, run_dir, "lab", settings_file, connection_info=connection) as sock:
        sock.settimeout(30)
        rcon = SocketRcon(sock)
        made = dev(rcon, "drive-lab")
        if not made.get("ok"):
            log(f"lab not set up: {made}")
            return 1
        join = write_join(engine, run_dir, mods, connection["endpoint"])
        log(f"drive lab up (seed {seed}); join with {join}")
        deadline = time.monotonic() + args.minutes * 60
        last = 0.0
        while time.monotonic() < deadline and not (run_dir / "stop-now").exists():
            relay(rcon, run_dir)
            if time.monotonic() - last > 30:
                status = dev(rcon, "drive-lab-status")
                players = ", ".join(f"{p.get('name')} ({p.get('vehicle') or 'on foot'})" for p in status.get("players") or [])
                log(f"tick {status.get('tick')}: {status.get('samples')} samples, {status.get('hits')} hits, "
                    f"{status.get('broke')} broken, {status.get('objects')} objects about | {players or 'nobody on'}")
                last = time.monotonic()
            time.sleep(0.5)
        log("saving: " + rcon.text("/server-save").strip()[:120])
        time.sleep(5)
    for name in ("drive_lab.jsonl", "drive_lab_world.jsonl"):
        recorded = run_dir / "profile" / "script-output" / name
        if recorded.exists():
            shutil.copy(recorded, run_dir / name)
            lines = recorded.read_text(encoding="utf-8").count("\n")
            log(f"recording: {run_dir / name} ({lines} lines)")
        else:
            log(f"no {name} found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
