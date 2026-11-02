"""Real graphics for a test run's replay. A headless server cannot take screenshots, so while the run
goes the runner saves the game every few game minutes, each save marked for the camera
(mod/scripts/camera.lua: where every engineer and its home are). After the run a graphical Factorio
loads each save on its own (--benchmark-graphics: a few ticks, then it quits by itself), the mod
takes the pictures on the first tick, and ffmpeg joins them into one MP4 per engineer and view under
<run dir>/video/. video/video.json says which frame is which game tick, so replay.html keeps the
videos in step with its timeline.

The client opens a small window for each save. It uses its own config and write-data (shared by
runs, which keeps its sprite cache), the run's mod directory, and never Factorio's normal profile.

    python tools/video.py tests/runs/designed-20261017-120000   (render a run's frame saves now)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from run_engine_tests import CREATION_FLAGS, ENGINE_DEFAULT, validate_engine  # noqa: E402

MARKER = "NPC_CAMERA_RESULT "
CLIENT = ROOT / "tests" / "runs" / "_video-client"
FPS = 6
CLIENT_TICKS = 5
CLIENT_TIMEOUT = 300
# Started outside Steam, the Steam build asks Steam to start it again (without our arguments).
STEAM_ENV = {"SteamAppId": "427520", "SteamGameId": "427520"}


def parse(text: str) -> dict[str, Any]:
    at = text.rfind(MARKER)
    if at < 0:
        return {}
    try:
        value = json.loads(text[at + len(MARKER):].strip().splitlines()[0])
    except (ValueError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []   # the game writes an empty list as {}


def written(path: Path, timeout: float) -> bool:
    """The save is there and has stopped growing."""
    deadline = time.monotonic() + timeout
    size, since = -1, 0.0
    while time.monotonic() < deadline:
        now = path.stat().st_size if path.exists() else 0
        if now > 0 and now == size:
            if time.monotonic() - since >= 0.5:
                return True
        else:
            size, since = now, time.monotonic()
        time.sleep(0.1)
    return False


def find_ffmpeg() -> Path | None:
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    builds = sorted(packages.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe")) if packages.exists() else []
    return builds[-1] if builds else None


def slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-") or "engineer"


class VideoCapture:
    """During a run: a marked save every few game minutes (and one at the end)."""

    def __init__(self, run_dir: Path, every_minutes: float, *, log: Callable[[str], None] = print):
        self.run_dir = Path(run_dir)
        self.every = max(600, int(round(every_minutes * 3600)))
        self.saves = self.run_dir / "profile" / "saves"
        self.log = log
        self.frames: list[dict[str, Any]] = []
        self.failed: list[str] = []
        self.next_tick: int | None = None

    def due(self, tick: Any) -> bool:
        if not isinstance(tick, (int, float)):
            return False
        if self.next_tick is None:   # the first frame a whole interval in: engineers have arrived
            self.next_tick = (int(tick) // self.every + 1) * self.every
        return tick >= self.next_tick

    def frame(self, call_text: Callable[[str], str]) -> dict[str, Any]:
        name = f"video-{len(self.frames) + len(self.failed) + 1:04d}"
        reply = parse(call_text("/npc-camera frame " + json.dumps({"name": name})))
        tick = reply.get("tick")
        if isinstance(tick, int):
            self.next_tick = (tick // self.every + 1) * self.every
        if not reply.get("ok"):
            self.failed.append(name)
            self.log(f"video frame not saved: {reply or 'no answer'}")
            return reply
        ok = written(self.saves / f"{name}.zip", 30)
        call_text("/npc-camera done")   # later saves (checkpoints, the end) carry no mark
        if ok:
            self.frames.append({"save": name, "tick": tick, "views": reply.get("views")})
        else:
            self.failed.append(name)
            self.log(f"video frame {name} was not written")
        return reply

    def summary(self) -> dict[str, Any]:
        return {"every_minutes": self.every / 3600, "frames_saved": len(self.frames), "frames_failed": self.failed}


def client_config(data: Path, client: Path | None = None) -> Path:
    """The client's own config: windowed, no vsync, normal-resolution sprites, sprite atlas cached."""
    client = client or CLIENT
    config = client / "config" / "config.ini"
    (client / "profile").mkdir(parents=True, exist_ok=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"[path]\nread-data={data.as_posix()}\nwrite-data={(client / 'profile').as_posix()}\n\n"
        "[general]\nenable-steam-cloud=false\n\n"
        "[graphics]\nfull-screen=false\nv-sync=false\ngraphics-quality=medium\ncache-sprite-atlas=true\n"
        "halt-rendering-when-minimized=false\nshow-game-simulations-in-background=false\n",
        encoding="utf-8")
    return config


def shoot(engine: Path, config: Path, mods: Path, save: Path, out: Path, log_path: Path,
          client: Path | None = None) -> bool:
    """One save in the graphical client: its pictures end up in out/. True if they did."""
    made = (client or CLIENT) / "profile" / "script-output" / "npc-video" / save.stem
    if made.exists():
        shutil.rmtree(made)
    args = [str(engine), "--config", str(config), "--mod-directory", str(mods), "--disable-audio",
            "--disable-migration-window", "--benchmark-graphics", str(save), "--benchmark-ticks", str(CLIENT_TICKS),
            "--window-size", "640x360"]
    startup = None
    if hasattr(subprocess, "STARTUPINFO"):   # a small window that does not take the focus
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 4   # SW_SHOWNOACTIVATE
    with log_path.open("w", encoding="utf-8") as output:
        try:
            subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, timeout=CLIENT_TIMEOUT,
                           env={**os.environ, **STEAM_ENV}, startupinfo=startup)
        except subprocess.TimeoutExpired:
            output.write(f"\n(video.py: no exit in {CLIENT_TIMEOUT} s)\n")
    if not (made / "frame.json").exists():
        return False
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(made), str(out))
    return True


def encode(video_dir: Path, ffmpeg: Path, fps: int = FPS) -> list[dict[str, Any]]:
    """One MP4 per engineer and view from every frame there is, in game-tick order."""
    groups: dict[tuple[int, str], list[tuple[int, dict, Path]]] = {}
    for meta_path in sorted((video_dir / "frames").glob("*/frame.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for view in as_list(meta.get("views")):
            picture = meta_path.parent / str(view.get("file") or "")
            if view.get("file") and picture.exists():
                groups.setdefault((int(view["id"]), str(view["kind"])), []).append((int(meta["tick"]), view, picture))
    videos = []
    for (npc_id, kind), items in sorted(groups.items()):
        items.sort(key=lambda item: item[0])
        name = items[-1][1].get("name") or f"engineer {npc_id}"
        out = video_dir / f"{npc_id}-{slug(name)}-{kind}.mp4"
        seq = video_dir / "seq"
        if seq.exists():
            shutil.rmtree(seq)
        seq.mkdir()
        for i, (_, _, picture) in enumerate(items):
            shutil.copyfile(picture, seq / f"{i:05d}{picture.suffix}")
        # Every frame a key frame (-g 1): the replay page seeks frame by frame.
        result = subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-framerate", str(fps),
                                 "-i", str(seq / f"%05d{items[0][2].suffix}"), "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-g", "1",
                                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                                capture_output=True, text=True, timeout=600, creationflags=CREATION_FLAGS)
        shutil.rmtree(seq)
        if result.returncode or not out.exists():
            raise RuntimeError(f"ffmpeg failed for {out.name}: {result.stderr.strip()[-400:]}")
        videos.append({"id": npc_id, "name": name, "kind": kind, "file": f"video/{out.name}",
                       "ticks": [tick for tick, _, _ in items], "zoom": items[-1][1].get("zoom")})
    return videos


def render(run_dir: Path, engine: Path = ENGINE_DEFAULT, *, fps: int = FPS, keep_saves: bool = False,
           capture: dict[str, Any] | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Pictures from every frame save not drawn yet, then the MP4s; writes video/video.json."""
    run_dir = Path(run_dir)
    started = time.time()
    engine = Path(engine).resolve(strict=True)
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found (winget install Gyan.FFmpeg); the frame saves are kept")
    video_dir = run_dir / "video"
    video_dir.mkdir(exist_ok=True)
    config = client_config(validate_engine(engine))
    saves = sorted((run_dir / "profile" / "saves").glob("video-*.zip"))
    failed = []
    if saves:
        log(f"video: drawing {len(saves)} frame saves in a graphical Factorio (a small window opens for each)")
    for i, save in enumerate(saves, 1):
        out = video_dir / "frames" / save.stem
        if shoot(engine, config, run_dir / "mods", save, out, video_dir / "client.log"):
            if not keep_saves:
                save.unlink()
        else:
            failed.append(save.stem)
            shutil.copyfile(video_dir / "client.log", video_dir / f"client-{save.stem}.log")
        if i % 10 == 0 or i == len(saves):
            log(f"video: {i}/{len(saves)} frame saves drawn ({len(failed)} failed)")
    videos = encode(video_dir, ffmpeg, fps)
    manifest = video_dir / "video.json"
    if capture is None:   # drawn later: what the run saved still stands
        capture = {}
        for path, key in ((manifest, None), (run_dir / "run.json", "video")):
            before = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            before = (before.get(key) or {}) if key else before
            capture = capture or {k: before[k] for k in ("every_minutes", "frames_saved", "frames_failed") if k in before}
    result = {"fps": fps, "videos": videos, "frames": len(list((video_dir / "frames").glob("*/frame.json"))),
              "failed": failed, "render_s": round(time.time() - started, 1), **(capture or {})}
    manifest.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    log(f"video: {len(videos)} MP4s from {result['frames']} frames in {result['render_s']} s"
        + (f"; {len(failed)} saves not drawn (kept, see video/client-*.log)" if failed else ""))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--engine", type=Path, default=ENGINE_DEFAULT)
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--keep-saves", action="store_true", help="keep the frame saves once drawn")
    parser.add_argument("--no-replay", action="store_true", help="do not rebuild replay.html")
    args = parser.parse_args()
    result = render(args.run_dir, args.engine, fps=args.fps, keep_saves=args.keep_saves)
    run_json = args.run_dir / "run.json"
    if run_json.exists():   # the run's record says the video is made now
        manifest = json.loads(run_json.read_text(encoding="utf-8"))
        before = {k: v for k, v in (manifest.get("video") or {}).items() if k not in ("later", "error")}
        manifest["video"] = {**before, "rendered": True, "videos": len(result["videos"]),
                             "render_s": result["render_s"], "not_drawn": result["failed"]}
        run_json.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        if not args.no_replay:
            from replay import build
            print(f"replay: {build(args.run_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
