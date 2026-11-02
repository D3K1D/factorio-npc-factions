"""What a test run was, for watching it afterwards: why it ran, which systems and settings it used,
what the LLMs cost, how it ended, and the game's own recording of it (mod/scripts/recorder.lua).

A runner makes one RunRecord, passes its log through it, starts the in-game recorder once the
server is up, polls it now and then (to tie wall-clock time to game ticks) and finishes it at the
end. finish() writes <run dir>/run.json, copies the recording beside it and builds replay.html
(tools/replay.py), which plays the run back in a browser.

With video (game minutes between frames) it also saves a frame every so often and, at the end,
draws them in a graphical Factorio and joins them into MP4s the replay shows (tools/video.py).
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import usage  # noqa: E402
from video import ENGINE_DEFAULT, VideoCapture, render as render_video  # noqa: E402

RECORD_NAME = "run"
POLL_S = 3.0


def git_state() -> dict[str, Any]:
    """Which code ran: the commit, and any files changed since it."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    changed = [line[3:] for line in git("status", "--porcelain").splitlines() if line.strip()]
    return {"commit": git("rev-parse", "--short", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "subject": git("log", "-1", "--format=%s"), "changed": changed[:60]}


def plain(value: Any) -> Any:
    """Arguments as JSON: paths become strings."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def parse(text: str, marker: str) -> dict[str, Any]:
    at = text.rfind(marker)
    if at < 0:
        return {}
    try:
        value = json.loads(text[at + len(marker):].strip().splitlines()[0])
    except (ValueError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


class RunRecord:
    def __init__(self, run_dir: Path, *, runner: str, purpose: str, args: Any = None,
                 systems: Mapping[str, Any] | None = None, enabled: bool = True, video: float | None = None,
                 video_render: bool = True, engine: Path = ENGINE_DEFAULT):
        self.run_dir = Path(run_dir)
        self.enabled = enabled
        self.video = VideoCapture(self.run_dir, video) if video and enabled else None
        self.video_render = video_render
        self.engine = engine
        self.started = time.time()
        self.mark = usage.mark()
        self.lines: list[tuple[float, str]] = []
        self.clock: list[tuple[float, int, float]] = []   # (wall time, tick, game speed)
        self.recording: dict[str, Any] = {}
        self.last_poll = 0.0
        info = json.loads((ROOT / "mod" / "info.json").read_text(encoding="utf-8"))
        self.manifest: dict[str, Any] = {
            "schema": 1, "runner": runner, "purpose": purpose, "command": [Path(sys.argv[0]).name, *sys.argv[1:]],
            "args": plain(vars(args) if args is not None and hasattr(args, "__dict__") else args or {}),
            "systems": plain(dict(systems or {})), "mod_version": info.get("version"),
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "python": platform.python_version(), "git": git_state(), "run_dir": str(self.run_dir)}
        if self.video:
            self.manifest["systems"]["video (real graphics)"] = f"a frame every {video:g} game minutes"

    def wrap(self, log: Callable[[str], None]) -> Callable[[str], None]:
        """The runner's log, kept with the time of each line."""
        def logged(message: str) -> None:
            self.lines.append((time.time(), str(message)))
            log(message)
        return logged

    def tick(self, tick: Any, speed: Any = None) -> None:
        """The runner learned the game tick (any status reply carries it)."""
        if isinstance(tick, (int, float)):
            self.clock.append((time.time(), int(tick), float(speed) if isinstance(speed, (int, float)) else 0.0))

    def start(self, call_text: Callable[[str], str]) -> None:
        if not self.enabled:
            return
        reply = parse(call_text("/npc-record start " + json.dumps({"name": RECORD_NAME})), "NPC_RECORD_RESULT ")
        self.recording = reply
        self.tick(reply.get("tick"))
        if not reply.get("ok"):
            print(f"recorder did not start: {reply or 'no answer'}", flush=True)

    def poll(self, call_text: Callable[[str], str]) -> None:
        """Every few seconds: the tick now, so wall-clock things (LLM calls, log lines) land on the timeline."""
        if not self.enabled or time.monotonic() - self.last_poll < POLL_S:
            return
        self.last_poll = time.monotonic()
        try:
            reply = parse(call_text("/npc-record status"), "NPC_RECORD_RESULT ")
        except Exception:   # a slow answer must not end the run
            return
        self.tick(reply.get("tick"), reply.get("speed"))
        if self.video and self.video.due(reply.get("tick")):
            self.frame(call_text)

    def frame(self, call_text: Callable[[str], str]) -> None:
        """A save for the video, marked for the camera (the game pauses while it saves)."""
        try:
            self.video.frame(call_text)
        except Exception as exc:   # a missed frame must not end the run
            print(f"video frame failed ({type(exc).__name__}: {exc})", flush=True)

    def stop(self, call_text: Callable[[str], str]) -> None:
        if not self.enabled:
            return
        if self.video:
            self.frame(call_text)   # the end as it was
        try:
            reply = parse(call_text("/npc-record stop"), "NPC_RECORD_RESULT ")
            self.tick(reply.get("tick"))
            self.recording.update(stopped=reply)
        except Exception as exc:
            self.recording.update(stop_error=str(exc))

    def finish(self, outcome: Mapping[str, Any], *, build: bool = True) -> Path | None:
        """run.json, the recording beside it, and replay.html. Returns the replay's path."""
        ended = time.time()
        rows = usage.entries(self.mark)
        self.manifest.update(
            ended=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended)), wall_s=round(ended - self.started, 1),
            outcome=plain(dict(outcome)), llm={"calls": rows, **usage.summary(rows)},
            clock=[[round(w, 2), t, s] for w, t, s in self.clock], log=[[round(w, 2), text] for w, text in self.lines],
            recording=self.recording, prices={"responses": usage.RESPONSES, "decisions": usage.DECISIONS})
        source = self.run_dir / "profile" / "script-output" / "npc-record" / f"{RECORD_NAME}.jsonl"
        if source.exists():
            shutil.copyfile(source, self.run_dir / "recording.jsonl")
        if self.video:
            self.manifest["video"] = self.make_video()
        (self.run_dir / "run.json").write_text(json.dumps(self.manifest, indent=1) + "\n", encoding="utf-8")
        if not (build and self.enabled):
            return None
        try:
            from replay import build as build_replay
            out = build_replay(self.run_dir)
            print(f"replay: {out}", flush=True)
            return out
        except Exception as exc:   # the run's own result stands even if its replay cannot be made
            print(f"replay not built ({type(exc).__name__}: {exc}); try: python tools/replay.py {self.run_dir}",
                  flush=True)
            return None

    def make_video(self) -> dict[str, Any]:
        """The frame saves drawn and joined into MP4s (the server is down by now)."""
        summary = self.video.summary()
        later = f"python tools/video.py {self.run_dir}"
        if not self.video.frames:
            return {**summary, "rendered": False}
        if not self.video_render:
            print(f"video: {len(self.video.frames)} frame saves kept; draw them with: {later}", flush=True)
            return {**summary, "rendered": False, "later": later}
        try:
            result = render_video(self.run_dir, self.engine, capture=summary)
            return {**summary, "rendered": True, "videos": len(result["videos"]), "render_s": result["render_s"],
                    "not_drawn": result["failed"]}
        except Exception as exc:   # the run's own result stands; the saves are kept for another try
            print(f"video not made ({type(exc).__name__}: {exc}); try: {later}", flush=True)
            return {**summary, "rendered": False, "error": str(exc)[:300], "later": later}
