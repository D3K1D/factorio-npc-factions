"""Exercise the NPC factions mod in an isolated Factorio 2.1 profile.

Python standard library only. Also the shared helpers (profile, server session, RCON) the other
native runners use. Its own runs: the old scripted fixtures (iron, blocked, guards).
This tool never reads or writes Factorio's normal player profile.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


ENGINE_DEFAULT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Factorio\bin\x64\factorio.exe")
MARKER = "NPC_FACTIONS_TEST_RESULT "
EXPECTED_ENGINE = "2.1"   # the release series the mod is ported to (any 2.1.x)
IRON_CHECKS = {
    "two_independent_forces", "physical_coal", "physical_iron", "physical_wood",
    "miner_fueled", "furnace_fueled", "iron_plates", "no_free_materials",
    "inventory_conserved", "job_completed", "no_enemy_units", "physical_walking",
}
GUARD_CHECKS = {
    "natural_furnace_craft", "cancel_craft_refund_once", "full_inventory_mining",
    "foreign_inventory_denied", "partial_transfer_conserved",
    "blocked_build_item_preserved", "stale_path_callback_ignored",
}
BAD_LOG = re.compile(r"\b(?:Error (?:Util|MainLoop|CrashHandler|Server)|LuaError|ModManager failed)\b")
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_engine(engine: Path) -> Path:
    engine = engine.resolve(strict=True)
    data = engine.parents[2] / "data"
    info = json.loads((data / "base" / "info.json").read_text(encoding="utf-8"))
    found = str(info.get("version") or "")
    if found != EXPECTED_ENGINE and not found.startswith(EXPECTED_ENGINE + "."):
        raise RuntimeError(f"Expected Factorio base {EXPECTED_ENGINE}.x; found {found!r}")
    return data


def prepare_profile(mod_root: Path, run_dir: Path, data: Path, *, space_age: bool = False) -> tuple[Path, Path]:
    info = json.loads((mod_root / "info.json").read_text(encoding="utf-8"))
    name, version = info["name"], info["version"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Unsafe mod name in info.json")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version):
        raise ValueError("Unsafe mod version in info.json")
    profile = run_dir / "profile"
    mods = run_dir / "mods"
    config = run_dir / "config" / "config.ini"
    for item in (profile, mods, config.parent):
        item.mkdir(parents=True, exist_ok=True)
    staged = mods / f"{name}_{version}"
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(mod_root, staged, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "profile", "runs"))
    json_write(mods / "mod-list.json", {"mods": [{"name": "base", "enabled": True}, {"name": name, "enabled": True},
                                               {"name": "space-age", "enabled": space_age},
                                               {"name": "quality", "enabled": space_age},
                                               {"name": "elevated-rails", "enabled": space_age}]})
    config.write_text(
        f"[path]\nread-data={data.as_posix()}\nwrite-data={profile.as_posix()}\n\n"
        "[general]\nenable-steam-cloud=false\n",
        encoding="utf-8",
    )
    return config, mods


def engine_args(engine: Path, config: Path, mods: Path) -> list[str]:
    return [str(engine), "--config", str(config), "--mod-directory", str(mods), "--disable-audio"]


def read_log(path: Path, profile: Path) -> str:
    value = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if not value.strip():
        fallback = profile / "factorio-current.log"
        if fallback.exists():
            value = fallback.read_text(encoding="utf-8", errors="replace")
            path.write_text(value, encoding="utf-8")
    return value


def invoke(args: list[str], log_path: Path, timeout: int, profile: Path) -> str:
    with log_path.open("w", encoding="utf-8") as output:
        result = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT,
                                timeout=timeout, creationflags=CREATION_FLAGS)
    value = read_log(log_path, profile)
    if result.returncode or BAD_LOG.search(value):
        raise RuntimeError(f"Engine failed (exit {result.returncode}); inspect {log_path}")
    return value


def snapshots(text: str) -> list[dict]:
    result = []
    for line in text.splitlines():
        if MARKER in line:
            payload = line.split(MARKER, 1)[1].strip()
            try:
                value = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Malformed test result: {payload[:200]}") from exc
            if not isinstance(value, dict):
                raise RuntimeError("Test result must be a JSON object")
            result.append(value)
    return result


def validate_result(value: dict, mode: str, *, completed: bool) -> None:
    if value.get("schema") != 1 or value.get("mode") != mode:
        raise RuntimeError(f"Wrong test schema/mode: {value.get('schema')!r}/{value.get('mode')!r}")
    if not isinstance(value.get("tick"), int) or value["tick"] < 0:
        raise RuntimeError("Test result has no valid tick")
    if completed and value.get("completed") is not True:
        raise RuntimeError(f"{mode} test did not complete; phase={value.get('phase')!r}")
    checks = value.get("checks")
    if not isinstance(checks, dict):
        raise RuntimeError("Test result has no checks object")
    failed = [key for key, check in checks.items()
              if not isinstance(check, dict) or (completed and check.get("passed") is not True)]
    if failed:
        raise RuntimeError(f"{mode} failed checks: {', '.join(failed)}")
    if completed and mode in ("iron", "blocked"):
        required = IRON_CHECKS | ({"blocked_recovered"} if mode == "blocked" else set())
        missing = required - set(checks)
        if missing:
            raise RuntimeError(f"{mode} test omitted required checks: {', '.join(sorted(missing))}")
    if completed and mode == "guards":
        missing = GUARD_CHECKS - set(checks)
        if missing:
            raise RuntimeError(f"Guard test omitted required checks: {', '.join(sorted(missing))}")
    if "state" not in value or "fingerprint" not in value:
        raise RuntimeError("Test result must include state and deterministic fingerprint")
    if completed and mode in ("iron", "blocked"):
        verify_iron_state(value["state"], blocked=mode == "blocked")
    if completed and mode == "guards":
        verify_guard_state(value["state"])


def verify_iron_state(state: dict, *, blocked: bool = False) -> None:
    if not isinstance(state, dict):
        raise RuntimeError("Iron state must be an object")
    actors = state.get("actors")
    if not isinstance(actors, list) or len(actors) != 2:
        raise RuntimeError("Iron fixture must report exactly two actors")
    if len({actor.get("force") for actor in actors if isinstance(actor, dict)}) != 2:
        raise RuntimeError("Engineers did not retain distinct forces")
    for index, actor in enumerate(actors):
        if not isinstance(actor, dict):
            raise RuntimeError(f"Actor {index} state is malformed")
        mined = actor.get("mined")
        if not isinstance(mined, dict):
            raise RuntimeError(f"Actor {index} has no mining receipts")
        for item in ("wood", "coal", "iron_ore"):
            if not isinstance(mined.get(item), (int, float)) or mined[item] <= 0:
                raise RuntimeError(f"Actor {index} did not physically acquire {item}")
        for field in ("miner_valid", "furnace_valid"):
            if actor.get(field) is not True:
                raise RuntimeError(f"Actor {index} lacks valid {field}")
        if actor.get("jobs_complete", 0) < 1 or actor.get("iron_plates", 0) < 12:
            raise RuntimeError(f"Actor {index} lacks completed work or twelve new plates")
        if actor.get("material_conserved") is not True:
            raise RuntimeError(f"Actor {index} failed its material ledger")
        if blocked and actor.get("recovery_count", 0) < 1:
            raise RuntimeError(f"Actor {index} did not recover from its blocked build")
        travelled = actor.get("distance_walked")
        if travelled is None:
            origin, position = actor.get("start_position"), actor.get("position")
            if not isinstance(origin, dict) or not isinstance(position, dict):
                raise RuntimeError(f"Actor {index} lacks walked distance or start/current positions")
            travelled = abs(position.get("x", 0) - origin.get("x", 0)) + abs(position.get("y", 0) - origin.get("y", 0))
        if not isinstance(travelled, (int, float)) or travelled <= 0.25:
            raise RuntimeError(f"Actor {index} did not physically travel")
    production = state.get("production")
    if not isinstance(production, dict) or production.get("iron_plate", 0) < 24:
        raise RuntimeError("Production receipts show fewer than 24 new iron plates")
    if state.get("free_materials") != 0:
        raise RuntimeError("Material ledger contains undeclared free grants")
    delta = state.get("material_delta")
    if not isinstance(delta, dict):
        raise RuntimeError("Ore depletion ledger missing")
    depleted, remaining, plates, inflight = (delta.get(key) for key in
                                              ("ore_depleted", "ore_remaining", "plates", "inflight"))
    if any(not isinstance(value, (int, float)) for value in (depleted, remaining, plates, inflight)):
        raise RuntimeError("Ore depletion ledger is malformed")
    if depleted - remaining - plates != inflight or not 0 <= inflight <= 2:
        raise RuntimeError("Ore depletion does not reconcile with plates and in-flight smelting")


def verify_guard_state(state: dict) -> None:
    if not isinstance(state, dict):
        raise RuntimeError("Guard state must be an object")
    if state.get("fixture_setup") is not True or state.get("ordinary_economy_claim") is not False:
        raise RuntimeError("Guard fixture did not declare its supplied-material scope")
    if state.get("executed") != len(GUARD_CHECKS):
        raise RuntimeError("Guard fixture did not execute every case")
    for field in ("grants", "discards", "receipts"):
        if not isinstance(state.get(field), list):
            raise RuntimeError(f"Guard fixture omitted {field} ledger")
    if state.get("terminal_reason") not in (None, ""):
        raise RuntimeError(f"Guard fixture terminated with reason {state['terminal_reason']}")


def free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def exact(sock: socket.socket, count: int) -> bytes:
    value = bytearray()
    while len(value) < count:
        part = sock.recv(count - len(value))
        if not part:
            raise RuntimeError("RCON connection closed")
        value.extend(part)
    return bytes(value)


def recv_packet(sock: socket.socket) -> tuple[int, int, str]:
    size = struct.unpack("<i", exact(sock, 4))[0]
    if not 10 <= size <= 1024 * 1024:
        raise RuntimeError(f"Invalid RCON packet size {size}")
    body = exact(sock, size)
    rid, kind = struct.unpack("<ii", body[:8])
    return rid, kind, body[8:-2].decode("utf-8", errors="replace")


def send_packet(sock: socket.socket, rid: int, kind: int, payload: str) -> None:
    body = struct.pack("<ii", rid, kind) + payload.encode("utf-8") + b"\0\0"
    sock.sendall(struct.pack("<i", len(body)) + body)


def connect_rcon(port: int, password: str, process: subprocess.Popen, deadline: float) -> socket.socket:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Factorio server exited during startup")
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1)
            sock.settimeout(5)
            send_packet(sock, 1, 3, password)
            while True:
                rid, kind, _ = recv_packet(sock)
                if rid == -1:
                    raise RuntimeError("RCON authentication failed")
                if rid == 1 and kind == 2:
                    return sock
        except (ConnectionRefusedError, TimeoutError, socket.timeout):
            time.sleep(0.2)
    raise RuntimeError("Factorio RCON startup timed out")


def command(sock: socket.socket, value: str) -> dict:
    send_packet(sock, 2, 2, value)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        rid, kind, payload = recv_packet(sock)
        if rid == 2 and kind == 0:
            found = snapshots(payload)
            if found:
                return found[-1]
            if payload.strip():
                raise RuntimeError(f"No {MARKER.strip()} in RCON response: {payload[:300]}")
    raise RuntimeError("RCON command returned no test result")


def save_wait(path: Path, action) -> dict:
    old = path.stat().st_mtime_ns if path.exists() else 0
    result = action()
    deadline = time.monotonic() + 20
    last_size = -1
    stable_since = None
    while time.monotonic() < deadline:
        if path.exists():
            stat = path.stat()
            if stat.st_size > 0 and stat.st_mtime_ns > old:
                if stat.st_size != last_size:
                    last_size, stable_since = stat.st_size, time.monotonic()
                elif stable_since is not None and time.monotonic() - stable_since >= 0.5:
                    return result
        time.sleep(0.1)
    raise RuntimeError(f"Fresh save was not written: {path}")


def server_session(common: list[str], save: Path, run_dir: Path, label: str, settings: Path,
                   *, connection_info: dict | None = None):
    """Context manager keeping one private server/RCON session alive."""
    from contextlib import contextmanager

    @contextmanager
    def session():
        rcon_port = free_port(socket.SOCK_STREAM)
        game_port = free_port(socket.SOCK_DGRAM)
        password = secrets.token_hex(24)
        args = [*common, "--start-server", str(save), "--server-settings", str(settings),
                "--bind", f"127.0.0.1:{game_port}", "--rcon-bind", f"127.0.0.1:{rcon_port}",
                "--rcon-password", password]
        log_path = run_dir / f"{label}.log"
        # Engine echoes launch arguments, including the ephemeral credential. Raw logs stay private.
        with log_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=CREATION_FLAGS)
            if connection_info is not None:
                connection_info.update(endpoint=f"127.0.0.1:{game_port}", server_pid=process.pid)
            try:
                with connect_rcon(rcon_port, password, process, time.monotonic() + 45) as sock:
                    yield sock
            except Exception as exc:
                raise RuntimeError(f"{exc}; inspect {log_path}") from exc
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    return session()


def wait_for(sock: socket.socket, mode: str, predicate, timeout: int, label: str) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = command(sock, "/npc-factions-test snapshot")
        validate_result(last, mode, completed=False)
        if last.get("phase") == "failed":
            failed = [key for key, check in last.get("checks", {}).items() if check.get("passed") is not True]
            raise RuntimeError(f"{mode} fixture failed: {', '.join(failed)}")
        if predicate(last):
            return last
        time.sleep(0.1 if mode == "guards" else 0.5)
    raise RuntimeError(f"Timed out waiting for {label}; last={json.dumps(last, sort_keys=True)[:500]}")


def compare_checkpoint(before: dict, after: dict, mode: str) -> None:
    if after["tick"] < before["tick"]:
        raise RuntimeError("Reload moved game tick backward")
    old, new = before["state"], after["state"]
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise RuntimeError("Checkpoint state is malformed")
    if mode == "guards":
        if new.get("executed", -1) < old.get("executed", 0) or new.get("stage", -1) < old.get("stage", 0):
            raise RuntimeError("Guard case progress was lost on reload")
        for field in ("grants", "discards", "receipts"):
            previous, current = old.get(field), new.get(field)
            # Factorio emits {} for an empty Lua sequence, then [] when populated.
            if previous == {}:
                previous = []
            if current == {}:
                current = []
            if not isinstance(previous, list) or not isinstance(current, list) or current[:len(previous)] != previous:
                raise RuntimeError(f"Guard {field} ledger changed on reload")
        return
    old_actors, new_actors = old.get("actors"), new.get("actors")
    if not isinstance(old_actors, list) or not isinstance(new_actors, list) or len(old_actors) != len(new_actors):
        raise RuntimeError("Actors missing after reload")
    for prior, current in zip(old_actors, new_actors):
        if prior.get("id") != current.get("id") or prior.get("force") != current.get("force"):
            raise RuntimeError("Actor identity/force changed on reload")
        for item, quantity in prior.get("mined", {}).items():
            if current.get("mined", {}).get(item, -1) < quantity:
                raise RuntimeError(f"Mined {item} receipt was lost on reload")
        if current.get("iron_plates", -1) < prior.get("iron_plates", 0):
            raise RuntimeError("Plate production receipt was lost on reload")
        if current.get("jobs_complete", -1) < prior.get("jobs_complete", 0):
            raise RuntimeError("Completed job receipt was lost on reload")
    if new.get("free_materials") != old.get("free_materials"):
        raise RuntimeError("Free-material ledger changed on reload")


def benchmark(common: list[str], seed: Path, run_dir: Path, profile: Path, mode: str,
              ticks: int, timeout: int) -> dict:
    output = invoke([*common, "--benchmark", str(seed), "--benchmark-ticks", str(ticks),
                     "--benchmark-runs", "2"], run_dir / f"{mode}-benchmark.log", timeout, profile)
    values = [value for value in snapshots(output) if value.get("completed") is True]
    if len(values) != 2:
        raise RuntimeError(f"Expected two completed benchmark snapshots, got {len(values)}")
    for value in values:
        validate_result(value, mode, completed=True)
    if values[0] != values[1]:
        raise RuntimeError("Identical benchmark runs produced different JSON snapshots")
    checksums = re.findall(r"^\s*checksum:\s*(\d+)", output, re.MULTILINE)
    if len(checksums) != 2 or checksums[0] != checksums[1]:
        raise RuntimeError(f"Expected two matching Factorio checksums, got {checksums}")
    return {"checksums": checksums, "result": values[0], "ticks": ticks}


def run_mode(args, common: list[str], run_dir: Path, profile: Path, settings: Path, mode: str) -> dict:
    seed = profile / "saves" / f"npc-test-{mode}-seed.zip"
    with server_session(common, args.fresh_save, run_dir, f"{mode}-seed-server", settings) as sock:
        initial = command(sock, f"/npc-factions-test start {mode}")
        validate_result(initial, mode, completed=False)
        if args.simulation_speed != 1:
            command(sock, f"/npc-factions-test speed {args.simulation_speed}")
        save_wait(seed, lambda: command(sock, f"/npc-factions-test save {seed.stem}"))
    bench = benchmark(common, seed, run_dir, profile, mode, args.benchmark_ticks, args.benchmark_timeout)
    if args.skip_live:
        return {"mode": mode, "initial": initial, "benchmark": bench}
    checkpoint = profile / "saves" / f"npc-test-{mode}-checkpoint.zip"
    with server_session(common, seed, run_dir, f"{mode}-live-server", settings) as sock:
        if args.simulation_speed != 1:
            command(sock, f"/npc-factions-test speed {args.simulation_speed}")
        if mode == "guards":
            predicate = lambda v: (not v["completed"] and
                                   (args.guard_checkpoint_case == "any" or
                                    v.get("phase") == args.guard_checkpoint_case) and
                                   v.get("state", {}).get("executed", 7) < len(GUARD_CHECKS))
            checkpoint_label = f"guard case {args.guard_checkpoint_case}"
        else:
            predicate = lambda v: (not v["completed"] and any(
                actor.get("current_job") == args.checkpoint_kind and
                (args.checkpoint_kind != "mine" or actor.get("mining_progress", 1) > 0)
                for actor in v.get("state", {}).get("actors", [])))
            checkpoint_label = f"active {args.checkpoint_kind} job"
        active = wait_for(sock, mode, predicate, args.live_timeout, checkpoint_label)
        before = save_wait(checkpoint,
                           lambda: command(sock, f"/npc-factions-test save {checkpoint.stem}"))
        validate_result(before, mode, completed=False)
        finished = wait_for(sock, mode, lambda v: v["completed"] is True,
                            args.live_timeout, "completed work")
        validate_result(finished, mode, completed=True)
    with server_session(common, checkpoint, run_dir, f"{mode}-reload-server", settings) as sock:
        if args.simulation_speed != 1:
            command(sock, f"/npc-factions-test speed {args.simulation_speed}")
        after = command(sock, "/npc-factions-test snapshot")
        validate_result(after, mode, completed=False)
        compare_checkpoint(before, after, mode)
        resumed = wait_for(sock, mode, lambda v: v["completed"] is True,
                           args.live_timeout, "completion after reload")
        validate_result(resumed, mode, completed=True)
    if resumed["state"] != finished["state"] or resumed["fingerprint"] != finished["fingerprint"]:
        raise RuntimeError("Completed state differs after mid-action save/reload")
    if bench["result"]["state"] != finished["state"] or bench["result"]["fingerprint"] != finished["fingerprint"]:
        raise RuntimeError("Benchmark and live run finished in different states")
    return {"mode": mode, "initial": initial, "active": active, "checkpoint": before,
            "reloaded": after, "finished": finished, "resumed": resumed, "benchmark": bench}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ENGINE_DEFAULT)
    parser.add_argument("--mod-root", type=Path, required=True, help="Unpacked mod root containing info.json")
    parser.add_argument("--run-dir", type=Path, help="Private disposable test profile directory")
    parser.add_argument("--mode", action="append", choices=("iron", "blocked", "guards"))
    parser.add_argument("--benchmark-ticks", type=int, default=30000)
    parser.add_argument("--simulation-speed", type=int, choices=range(1, 9), default=1,
                        help="Test-only game speed; simulation physics and tick budget stay unchanged")
    parser.add_argument("--benchmark-timeout", type=int, default=360)
    parser.add_argument("--live-timeout", type=int, default=360)
    parser.add_argument("--skip-live", action="store_true", help="Run seed and repeatable benchmark only")
    parser.add_argument("--checkpoint-kind", choices=("move", "mine", "craft"), default="mine",
                        help="Physical worker job type to checkpoint during full iron/blocked runs")
    parser.add_argument("--guard-checkpoint-case", choices=("any", *tuple(sorted(GUARD_CHECKS))),
                        default="any", help="Case to checkpoint in guards mode; any is robust to startup timing")
    args = parser.parse_args()
    source = args.mod_root.resolve(strict=True)
    engine = args.engine.resolve(strict=True)
    data = validate_engine(engine)
    modes = args.mode or ["iron"]
    run_dir = (args.run_dir or Path(__file__).resolve().parents[1] / "tests" / "runs" / time.strftime("%Y%m%d-%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    config, mods = prepare_profile(source, run_dir, data)
    common = engine_args(engine, config, mods)
    profile = run_dir / "profile"
    saves = profile / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    args.fresh_save = saves / "npc-test-fresh.zip"
    map_gen = Path(__file__).resolve().parents[1] / "tests" / "map-gen-settings.json"
    settings = Path(__file__).resolve().parents[1] / "tests" / "server-settings.json"
    old = args.fresh_save.stat().st_mtime_ns if args.fresh_save.exists() else 0
    invoke([*common, "--create", str(args.fresh_save), "--map-gen-settings", str(map_gen)],
           run_dir / "create.log", 120, profile)
    if not args.fresh_save.exists() or args.fresh_save.stat().st_size == 0 or args.fresh_save.stat().st_mtime_ns <= old:
        raise RuntimeError("Engine did not create a fresh fixture save")
    report = {"engine": EXPECTED_ENGINE, "mod": str(source), "simulation_speed": args.simulation_speed,
              "checkpoint_kind": args.checkpoint_kind, "guard_checkpoint_case": args.guard_checkpoint_case,
              "modes": {}}
    for mode in modes:
        print(f"Running {mode} in isolated Factorio profile...", flush=True)
        report["modes"][mode] = run_mode(args, common, run_dir, profile, settings, mode)
        json_write(run_dir / "results.json", report)
    print(f"PASS: {', '.join(modes)}; report: {run_dir / 'results.json'}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
