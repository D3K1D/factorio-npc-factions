"""Shared pieces of the Biter World tools: a run folder with the user's mod set, and a private headless
server on it with an RCON line. Used by make_biter_world_save.py and rehearse_biter_world.py.

The user's mod set is read from a folder of their mod zips (by default tests/runs/mods-user-21 of the biter-world
checkout: the user's mods for 2.1, hard links of their own). Zips are hard-linked again, never changed; npc-factions
comes from the packaged zip, so a save records the released version.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.rcon import RconClient  # noqa: E402

ENGINE = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Factorio\bin\x64\factorio.exe")
MOD_SOURCE = ROOT.parent / "npc-biterworld" / "tests" / "runs" / "mods-user-21"   # (as tools/proving.py's)
MAP_GEN = ROOT / "tests" / "biter-world-map-gen-settings.json"
MAP_SETTINGS = ROOT / "tests" / "biter-world-map-settings.json"
# An ordinary team world: normal nests and the game's own evolution and expansion, cliffs still off.
NORMAL_MAP_GEN = ROOT / "tests" / "normal-world-map-gen-settings.json"
SERVER_SETTINGS = ROOT / "tests" / "server-settings.json"
GAME_PORT, RCON_PORT = 34331, 27131
TEAM_MARK, AGENT_MARK = "NPC_TEAM_RESULT ", "NPC_AGENT_RESULT "
# Bob's library notes (with a traceback) that Bob's Metals is not installed: harmless, at data loading.
NOISE = re.compile(r"bob-tungsten-processing|Prerequisite technology|__boblibrary__/")
ERRORS = re.compile(r"Error|attempt to|stack traceback|bad argument")
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse(text: str, marker: str) -> dict[str, Any]:
    at = text.rfind(marker)
    if at < 0:
        return {"ok": False, "raw": text[:300]}
    return json.loads(text[at + len(marker):].strip())


def prepare_run(run: Path, mod_zip: Path, source: Path = MOD_SOURCE, engine: Path = ENGINE) -> tuple[Path, Path]:
    """mods/ (the source's zips hard-linked, its mod list and settings, and our zip) and a config
    writing to run/profile. Returns (config, mods)."""
    if not (source / "mod-list.json").exists():
        raise FileNotFoundError(f"no mod set at {source} (mod-list.json missing)")
    mods = run / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    for old in mods.glob("npc-factions*"):
        if old.is_file():
            old.unlink()   # (a folder or junction of that name is never ours to remove)
    for z in sorted(source.glob("*.zip")):
        if z.name.startswith("npc-factions"):
            continue
        target = mods / z.name
        if not target.exists():
            try:
                os.link(z, target)       # the same file, not a copy: nothing of the source changes
            except OSError:
                shutil.copy2(z, target)
    shutil.copy2(source / "mod-settings.dat", mods / "mod-settings.dat")
    listing = json.loads((source / "mod-list.json").read_text(encoding="utf-8"))
    names = {m.get("name") for m in listing.get("mods", [])}
    if "npc-factions" not in names:
        listing["mods"].append({"name": "npc-factions", "enabled": True})
    for m in listing["mods"]:
        if m.get("name") == "npc-factions":
            m["enabled"] = True
    (mods / "mod-list.json").write_text(json.dumps(listing, indent=2), encoding="utf-8")
    shutil.copy2(mod_zip, mods / mod_zip.name)
    profile = run / "profile"
    (profile / "saves").mkdir(parents=True, exist_ok=True)
    config = run / "config" / "config.ini"
    config.parent.mkdir(parents=True, exist_ok=True)
    data = engine.parents[2] / "data"
    config.write_text(f"[path]\nread-data={data.as_posix()}\nwrite-data={profile.as_posix()}\n\n"
                      "[general]\nenable-steam-cloud=false\n", encoding="utf-8")
    return config, mods


def common_args(config: Path, mods: Path, engine: Path = ENGINE) -> list[str]:
    return [str(engine), "--config", str(config), "--mod-directory", str(mods), "--disable-audio"]


def create_map(config: Path, mods: Path, save: Path, log: Path, engine: Path = ENGINE, *,
               death_world: bool = False) -> None:
    args = [*common_args(config, mods, engine), "--create", str(save)]
    args += (["--map-gen-settings", str(MAP_GEN), "--map-settings", str(MAP_SETTINGS)] if death_world
             else ["--map-gen-settings", str(NORMAL_MAP_GEN)])
    with log.open("w", encoding="utf-8") as out:
        done = subprocess.run(args, stdout=out, stderr=subprocess.STDOUT, timeout=600, creationflags=CREATION_FLAGS)
    if done.returncode or not save.exists():
        raise RuntimeError(f"map creation failed (exit {done.returncode}); see {log}")


class Server:
    """A private headless server on a save, with a fresh RCON password kept in a file in the run folder
    (never printed; the file and any copy of it in the logs go when the server stops)."""

    def __init__(self, run: Path, config: Path, mods: Path, save: Path, *, game_port: int = GAME_PORT,
                 rcon_port: int = RCON_PORT, engine: Path = ENGINE, log_name: str = "server.log"):
        self.run, self.config, self.mods, self.save = run, config, mods, save
        self.game_port, self.rcon_port, self.engine = game_port, rcon_port, engine
        self.log_path = run / log_name
        self.secret_path = run / "rcon-password.txt"
        self.process: subprocess.Popen | None = None
        self._out = None

    @property
    def endpoint(self) -> dict[str, Any]:
        return {"host": "127.0.0.1", "port": self.rcon_port, "password": self.secret_path.read_text().strip()}

    def start(self) -> "Server":
        self.secret_path.write_text(secrets.token_hex(24))
        args = [*common_args(self.config, self.mods, self.engine), "--start-server", str(self.save),
                "--server-settings", str(SERVER_SETTINGS), "--port", str(self.game_port),
                "--rcon-bind", f"127.0.0.1:{self.rcon_port}", "--rcon-password", self.endpoint["password"]]
        self._out = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(args, stdout=self._out, stderr=subprocess.STDOUT,
                                        creationflags=CREATION_FLAGS)
        return self

    def connect(self, timeout_s: float = 120, socket_timeout: float = 60) -> RconClient:
        """An RCON line once the server is up, past the first /sc (which never answers)."""
        deadline = time.monotonic() + timeout_s
        while True:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"the server stopped while starting; see {self.log_path}")
            try:
                client = RconClient("127.0.0.1", self.rcon_port, self.endpoint["password"], timeout=socket_timeout)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
        try:
            client.sock.settimeout(5)
            client.text("/sc rcon.print('ready')")
            client.sock.settimeout(socket_timeout)
            return client
        except (TimeoutError, OSError):
            client.close()
            return RconClient("127.0.0.1", self.rcon_port, self.endpoint["password"], timeout=socket_timeout)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self._out:
            self._out.close()
            self._out = None
        if self.secret_path.exists():
            secret = self.secret_path.read_text().strip()
            for path in list(self.run.rglob("*.log")) + list(self.run.rglob("*.txt")) + list(self.run.rglob("*.json")):
                if path == self.secret_path or not path.is_file() or self.mods in path.parents:
                    continue   # (never the mods folder: its zips are the user's own files)
                text = path.read_text(encoding="utf-8", errors="replace")
                if secret and secret in text:
                    path.write_text(text.replace(secret, "REDACTED"), encoding="utf-8")
            self.secret_path.unlink()

    def __enter__(self) -> "Server":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()

    def errors(self) -> list[str]:
        """Script errors in the server log (the known harmless Bob's library notice left out)."""
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return [l.strip()[:300] for l in lines if ERRORS.search(l) and not NOISE.search(l)]


def wait_ready(rcon: RconClient, timeout_s: float = 300) -> dict[str, Any]:
    """Poll /npc-team status until the team area is ready."""
    deadline = time.monotonic() + timeout_s
    while True:
        status = parse(rcon.text("/npc-team status"), TEAM_MARK)
        if status.get("status") == "ready" or time.monotonic() > deadline:
            return status
        time.sleep(3)


def wait_file(path: Path, since_ns: int = 0, timeout_s: float = 120) -> None:
    """Until a save the game writes is complete (its size has stopped changing)."""
    deadline = time.monotonic() + timeout_s
    last, stable = -1, None
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_mtime_ns > since_ns and path.stat().st_size > 0:
            size = path.stat().st_size
            if size != last:
                last, stable = size, time.monotonic()
            elif stable is not None and time.monotonic() - stable >= 1.0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"the game did not finish writing {path}")
