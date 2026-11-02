"""Make the Biter World save the user hosts: seed 5150 with the scenario's map settings, the user's mod
set and the packaged npc-factions, the team world switched on for 7 engineers and its area ready
(nests cleared, starter ore laid). The engineers are not spawned: they arrive when the host joins.
A normal world (the user stopped the death world, 2026-10-30); --death-world makes one.

    python tools/make_biter_world_save.py [--out dist/biter-world-5150.zip] [--mods-from <folder>] [--death-world]

Runs a private headless server for a minute or two; nothing of the user's Factorio folder is touched.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import biter_world as bw  # noqa: E402
import package_mod  # noqa: E402

SAVE_NAME = "biter-world-5150"


def make(out: Path, *, run: Path | None = None, source: Path = bw.MOD_SOURCE, engines: int = 7,
         death_world: bool = False, log=print) -> dict:
    """Build the save at `out` and return a summary of it."""
    run = run or ROOT / "tests" / "runs" / "biter-world-save"
    if run.exists():
        shutil.rmtree(run / "saves", ignore_errors=True)
        shutil.rmtree(run / "profile" / "saves", ignore_errors=True)
    run.mkdir(parents=True, exist_ok=True)
    mod_zip = package_mod.build(run / "dist")
    _, version = package_mod.mod_info()
    config, mods = bw.prepare_run(run, mod_zip, source)
    (run / "saves").mkdir(exist_ok=True)
    fresh = run / "saves" / "fresh.zip"
    map_gen = bw.MAP_GEN if death_world else bw.NORMAL_MAP_GEN
    seed = json.loads(map_gen.read_text(encoding="utf-8")).get("seed")
    log(f"creating the seed {seed} {'death' if death_world else 'normal'} map with {len(list(mods.glob('*.zip')))} mods "
        f"(npc-factions {version})")
    bw.create_map(config, mods, fresh, run / "create.log", death_world=death_world)
    saved = run / "profile" / "saves" / f"{SAVE_NAME}.zip"
    with bw.Server(run, config, mods, fresh) as server:
        rcon = server.connect()
        try:
            on = bw.parse(rcon.text('/npc-team on ' + json.dumps({"count": engines, "death_world": death_world})),
                          bw.TEAM_MARK)
            if not on.get("active"):
                raise RuntimeError(f"the team world did not switch on: {on}")
            started = time.monotonic()
            status = bw.wait_ready(rcon)
            if status.get("status") != "ready":
                raise RuntimeError(f"the team area was not ready in time: {status.get('status')}")
            log(f"team area ready at tick {status.get('ready_tick')} ({time.monotonic() - started:.0f} s)")
            since = saved.stat().st_mtime_ns if saved.exists() else 0
            rcon.text(f"/save {SAVE_NAME}")
            bw.wait_file(saved, since)
        finally:
            rcon.close()
        errors = server.errors()
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(saved, out)
    added = [a for a in status.get("added") or [] if not a.get("failed")]
    summary = {"save": str(out), "seed": seed, "npc_factions": version, "engines": engines,
               "death_world": status.get("death_world"), "world_traits": status.get("world_traits"),
               "ready_tick": status.get("ready_tick"), "starter_patches": len(added),
               "enemies_cleared": status.get("cleared"), "plots": len(status.get("plots") or []),
               "script_errors": errors[:10], "size_kb": out.stat().st_size // 1024}
    (run / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / f"{SAVE_NAME}.zip")
    parser.add_argument("--run-dir", type=Path, help="work folder (default tests/runs/biter-world-save)")
    parser.add_argument("--mods-from", type=Path, default=bw.MOD_SOURCE,
                        help="a folder of the user's mod zips with its mod-list.json and mod-settings.dat (read only)")
    parser.add_argument("--engines", type=int, default=7)
    parser.add_argument("--death-world", action="store_true",
                        help="a death world, every engineer a survivor (default: an ordinary team world)")
    args = parser.parse_args()
    summary = make(args.out, run=args.run_dir, source=args.mods_from, engines=args.engines,
                   death_world=args.death_world)
    print(f"save {summary['save']} ({summary['size_kb']} KB): seed {summary['seed']}, npc-factions "
          f"{summary['npc_factions']}, {summary['engines']} engines, team area ready at tick {summary['ready_tick']}, "
          f"{summary['starter_patches']} starter patches, {summary['enemies_cleared']} enemies cleared, "
          f"{len(summary['script_errors'])} script errors")
    return 0 if not summary["script_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
