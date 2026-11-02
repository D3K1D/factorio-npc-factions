"""Plays a test run back: builds <run dir>/replay.html from the game's recording of the run
(mod/scripts/recorder.lua) and its run.json (tools/run_record.py). One self-contained file to open
in a browser: the map over time (engineers, buildings, biters, nests), a timeline of what happened,
and what the run was for, which systems and settings it used and what the LLMs cost. Real-graphics
videos of the run (tools/video.py, beside it in video/) play in step with the timeline.

    python tools/replay.py tests/runs/smoke-20261017-120000 [--open]
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "replay_template.html"
SLOT = "/*REPLAY_DATA*/null"
PALETTE = [[0.55, 0.85, 1], [1, 0.75, 0.45], [0.7, 1, 0.6], [1, 0.6, 0.8], [0.85, 0.75, 1],
           [1, 0.95, 0.5], [0.5, 1, 0.9], [1, 0.5, 0.45], [0.75, 0.75, 0.75], [0.6, 0.7, 1]]
# Runner log lines worth a mark on the timeline.
NOTABLE = re.compile(r"accepted|planner failed|chose|deferred|scouting|-> |PASS|FAIL|stopped|saved|warlord|"
                     r"designer .*: |error", re.IGNORECASE)


def find_recording(run_dir: Path) -> Path | None:
    here = run_dir / "recording.jsonl"
    if here.exists():
        return here
    found = sorted((run_dir / "profile" / "script-output" / "npc-record").glob("*.jsonl"),
                   key=lambda p: p.stat().st_mtime) if (run_dir / "profile").exists() else []
    return found[-1] if found else None


def read_lines(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:   # a line cut off when the game stopped
            continue
        if isinstance(value, dict):
            out.append(value)
    starts = [i for i, v in enumerate(out) if v.get("k") == "start"]
    return out[starts[-1]:] if starts else out


def wall_to_tick(clock: list) -> Callable[[float], int | None]:
    """Wall-clock time to game tick, from the (wall, tick, speed) samples the runner took."""
    points = sorted((w, t, s) for w, t, s in clock if isinstance(t, (int, float)))
    walls = [p[0] for p in points]

    def convert(wall: float) -> int | None:
        if not points:
            return None
        i = bisect.bisect_right(walls, wall)
        if i == 0:
            return int(points[0][1])
        if i == len(points):
            w, t, s = points[-1]
            return int(t + (wall - w) * 60 * (s or 0))
        (w0, t0, _), (w1, t1, _) = points[i - 1], points[i]
        return int(t0 + (t1 - t0) * (wall - w0) / (w1 - w0)) if w1 > w0 else int(t1)
    return convert


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []   # the game writes an empty list as {}


class Builder:
    def __init__(self, lines: list[dict[str, Any]]):
        self.lines = lines
        self.protos: dict[int, list] = {}
        self.forces: dict[int, dict] = {}
        self.npcs: dict[int, dict] = {}
        self.vehicles: list[str] = []
        self.cols: dict[str, list] = {k: [] for k in ("p", "x", "y", "d", "f", "t0", "t1")}
        self.open: dict[tuple, int] = {}
        self.scanned: set[int] = set()
        self.tracks: dict[int, dict[str, list]] = {}
        self.players: dict[str, dict[str, list]] = {}
        self.enemies: list = []
        self.states: dict[int, list] = {}
        self.current: dict[int, dict] = {}
        self.stats: list = []
        self.events: list[dict] = []
        self.header: dict = {}
        self.footer: dict = {}
        self.lost: dict | None = None

    def npc_name(self, npc_id: Any) -> str:
        n = self.npcs.get(npc_id)
        return n["name"] if n else f"engineer {npc_id}"

    def force_name(self, name: Any) -> str:
        """An engineer's force by the engineer's name."""
        for f in self.forces.values():
            if f["name"] == name and f.get("npc"):
                return self.npc_name(f["npc"])
        return str(name) if name else "?"

    def where(self, npc_id: Any, t: int) -> tuple[float, float] | None:
        track = self.tracks.get(npc_id)
        if not track or not track["t"]:
            return None
        i = max(0, bisect.bisect_right(track["t"], t) - 1)
        return track["x"][i], track["y"][i]

    def event(self, t: int, kind: str, text: str, *, npc: Any = None, at: tuple | None = None, tone: str = "") -> None:
        e: dict[str, Any] = {"t": t, "k": kind, "text": text}
        if npc is not None:
            e["id"] = npc
        if at is None and npc is not None:
            at = self.where(npc, t)
        if at:
            e["x"], e["y"] = round(at[0], 1), round(at[1], 1)
        if tone:
            e["tone"] = tone
        self.events.append(e)

    def flush_lost(self) -> None:
        if not self.lost:
            return
        lost, self.lost = self.lost, None
        names = ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in sorted(lost["names"].items(), key=lambda kv: -kv[1]))
        count = sum(lost["names"].values())
        by = f" to {self.force_name(lost['by'])}" if lost["by"] else ""
        self.event(lost["t"], "lost", f"{lost['who']} lost {count} building{'s' if count > 1 else ''}{by}: {names}",
                   at=lost["at"], tone="bad")

    def run(self) -> None:
        for line in self.lines:
            k, t = line.get("k"), int(line.get("t") or 0)
            if k != "lost" or (self.lost and t - self.lost["t"] > 600):
                self.flush_lost()
            handler = getattr(self, "on_" + str(k), None)
            if handler:
                handler(line, t)
        self.flush_lost()

    def on_start(self, line: dict, t: int) -> None:
        self.header = line
        self.event(t, "system", "Recording started", tone="info")

    def on_stop(self, line: dict, t: int) -> None:
        self.footer = line
        self.event(t, "system", "Recording stopped", tone="info")

    def on_error(self, line: dict, t: int) -> None:
        self.event(t, "system", f"Recorder stopped by an error: {line.get('error')}", tone="bad")

    def on_proto(self, line: dict, t: int) -> None:
        self.protos[int(line["i"])] = [line.get("name"), line.get("type"), line.get("w") or 1, line.get("h") or 1]

    def on_force(self, line: dict, t: int) -> None:
        self.forces[int(line["i"])] = {"name": line.get("name"), "npc": line.get("npc"), "color": line.get("color")}

    def on_npc(self, line: dict, t: int) -> None:
        npc_id = line["id"]
        self.npcs[npc_id] = {"id": npc_id, "name": line.get("name") or f"engineer {npc_id}", "force": line.get("force"),
                             "home": line.get("home"), "traits": as_list(line.get("traits")),
                             "warlord": bool(line.get("warlord")),
                             # the colour it speaks in (voice.lua): its chat colour, else the same palette
                             "color": line.get("color") or PALETTE[(npc_id - 1) % len(PALETTE)]}
        self.event(t, "npc", f"{self.npcs[npc_id]['name']} is in the game"
                   + (f" ({', '.join(self.npcs[npc_id]['traits'])})" if self.npcs[npc_id]["traits"] else ""),
                   npc=npc_id, at=(line["home"]["x"], line["home"]["y"]) if line.get("home") else None)

    def on_a(self, line: dict, t: int) -> None:
        for actor in as_list(line.get("a")):
            npc_id, x, y, hp, vehicle = (actor + [0, 0, 0, 1, 0])[:5]
            track = self.tracks.setdefault(npc_id, {"t": [], "x": [], "y": [], "hp": [], "v": []})
            if vehicle and vehicle not in self.vehicles:
                self.vehicles.append(vehicle)
            track["t"].append(t); track["x"].append(x); track["y"].append(y); track["hp"].append(hp)
            track["v"].append(self.vehicles.index(vehicle) + 1 if vehicle else 0)
        for name, x, y in (p[:3] for p in as_list(line.get("p")) if len(p) >= 3):
            track = self.players.setdefault(name, {"t": [], "x": [], "y": []})
            track["t"].append(t); track["x"].append(x); track["y"].append(y)
        enemies = as_list(line.get("e"))
        if enemies:
            self.enemies.append([t, enemies])

    def on_state(self, line: dict, t: int) -> None:
        npc_id = line["id"]
        now = self.current.setdefault(npc_id, {"phase": "", "doing": "", "plan": "", "foe": ""})
        had_foe = now["foe"]
        for key in now:
            if key in line:
                now[key] = line[key]
        self.states.setdefault(npc_id, []).append([t, now["phase"], now["doing"], now["plan"], now["foe"]])
        if "plan" in line and line["plan"]:
            self.event(t, "plan", f"{self.npc_name(npc_id)}: plan {line['plan']}", npc=npc_id)
        if "foe" in line:
            if line["foe"]:
                self.event(t, "war", f"{self.npc_name(npc_id)} now fights {line['foe']}", npc=npc_id, tone="bad")
            elif had_foe:
                self.event(t, "war", f"{self.npc_name(npc_id)} no longer fights {had_foe}", npc=npc_id)

    def on_gone(self, line: dict, t: int) -> None:
        self.event(t, "died", f"{self.npc_name(line.get('id'))} is gone", npc=line.get("id"), tone="bad")

    def on_b(self, line: dict, t: int) -> None:
        force = int(line.get("f") or 0)
        first = force not in self.scanned
        self.scanned.add(force)
        start = int(self.header.get("t", t))
        for item in as_list(line.get("add")):
            key, proto, x, y, d = (item + [0, 0, 0, 0, 0])[:5]
            ident = (force, str(key))
            if ident in self.open:
                continue
            self.open[ident] = len(self.cols["p"])
            for col, value in zip(("p", "x", "y", "d", "f", "t0", "t1"),
                                  (proto, x, y, d, force, start if first else t, -1)):
                self.cols[col].append(value)
        for key in as_list(line.get("del")):
            at = self.open.pop((force, str(key)), None)
            if at is not None:
                self.cols["t1"][at] = t

    def on_say(self, line: dict, t: int) -> None:
        to = f" → {line['to']}" if line.get("to") else ""
        self.event(t, "say", f"{self.npc_name(line.get('id'))}{to}: {line.get('text', '')}", npc=line.get("id"))

    def on_chat(self, line: dict, t: int) -> None:
        self.event(t, "chat", f"{line.get('player', '?')}: {line.get('text', '')}")

    def on_task(self, line: dict, t: int) -> None:
        status = line.get("status") or ""
        tool = line.get("tool") or "?"
        if status == "planned":
            text = f"{self.npc_name(line.get('id'))} plans {tool}" + (f" — {line['why']}" if line.get("why") else "")
        else:
            text = f"{self.npc_name(line.get('id'))}: {tool} {status}" + (f" ({line['reason']})" if line.get("reason") else "")
        tone = "bad" if status in ("failed", "rejected") else ("good" if status == "done" else "")
        self.event(t, "task", text, npc=line.get("id"), tone=tone)

    def on_research(self, line: dict, t: int) -> None:
        force = self.forces.get(int(line.get("f") or 0), {})
        who = self.npc_name(force["npc"]) if force.get("npc") else force.get("name", "?")
        self.event(t, "research", f"{who} researched {line.get('tech')}" + (" (granted)" if line.get("by_script") else ""),
                   npc=force.get("npc"), tone="good")

    def on_rocket(self, line: dict, t: int) -> None:
        force = self.forces.get(int(line.get("f") or 0), {})
        self.event(t, "research", f"{self.force_name(force.get('name'))} launched a rocket", at=(line["x"], line["y"]),
                   tone="good")

    def on_died(self, line: dict, t: int) -> None:
        who = self.npc_name(line["id"]) if line.get("id") else (line.get("player") or "a character")
        by = line.get("cause") or ""
        if line.get("by"):
            by = f"{self.force_name(line['by'])}" + (f"'s {by}" if by else "")
        self.event(t, "died", f"{who} died" + (f", killed by {by}" if by else ""), at=(line["x"], line["y"]), tone="bad")

    def on_nest(self, line: dict, t: int) -> None:
        by = self.force_name(line.get("by")) if line.get("by") else "someone"
        self.event(t, "war", f"{by} destroyed a {line.get('name')}", at=(line["x"], line["y"]), tone="good")

    def on_lost(self, line: dict, t: int) -> None:
        force = self.forces.get(int(line.get("f") or 0), {})
        who = self.npc_name(force["npc"]) if force.get("npc") else force.get("name", "?")
        if self.lost and (self.lost["who"] != who or self.lost["by"] != line.get("by")):
            self.flush_lost()
        if not self.lost:
            self.lost = {"t": t, "who": who, "by": line.get("by"), "names": {}, "at": (line["x"], line["y"])}
        names = self.lost["names"]
        names[line.get("name")] = names.get(line.get("name"), 0) + 1

    def on_chron(self, line: dict, t: int) -> None:
        def who(key: Any) -> str:
            m = re.match(r"npc:(\d+)$", str(key or ""))
            return self.npc_name(int(m.group(1))) if m else str(key or "")
        kind = str(line.get("kind") or "")
        actor, target = who(line.get("actor")), who(line.get("target"))
        text = f"{kind.replace('_', ' ')}: {actor}" + (f" → {target}" if target else "")
        payload = line.get("payload")
        if payload and payload not in ("{}", "[]"):
            text += f" {payload}"
        tone = "bad" if kind in ("war_declared", "attacked", "killed", "theft", "sabotage", "treaty_broken") else ""
        m = re.match(r"npc:(\d+)$", str(line.get("actor") or ""))
        self.event(t, "war" if tone == "bad" else "social", text, npc=int(m.group(1)) if m else None, tone=tone)

    def on_stats(self, line: dict, t: int) -> None:
        self.stats.append([t, as_list(line.get("s")), line.get("evo")])


def build_data(lines: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    b = Builder(lines)
    b.run()
    ticks = [int(v.get("t")) for v in lines if isinstance(v.get("t"), (int, float))]
    t0, t1 = (min(ticks), max(ticks)) if ticks else (0, 0)
    to_tick = wall_to_tick(manifest.get("clock") or [])
    log, llm = [], []
    for wall, text in manifest.get("log") or []:
        t = to_tick(wall)
        log.append([t if t is not None else t0, text])
        if NOTABLE.search(text) and t is not None:
            b.event(t, "runner", text[:300])
    for call in (manifest.get("llm") or {}).get("calls") or []:
        t = to_tick(call.get("at") or 0)
        llm.append({**{k: call.get(k) for k in ("system", "model", "input", "output", "cached", "cost", "latency_ms",
                                                  "ok", "purpose", "npc")}, "t": t if t is not None else t0})
    b.events.sort(key=lambda e: e["t"])
    meta = {k: v for k, v in manifest.items() if k not in ("log", "clock", "llm")}
    meta["llm"] = {k: v for k, v in (manifest.get("llm") or {}).items() if k != "calls"}
    return {"meta": meta, "header": b.header, "footer": b.footer, "t0": t0, "t1": t1,
            "every": b.header.get("every", 60), "block": b.header.get("block", 64), "cell": b.header.get("cell", 2),
            "tree_cell": b.header.get("tree_cell", 8),
            "protos": [b.protos.get(i, ["?", "?", 1, 1]) for i in range(1, max(b.protos, default=0) + 1)],
            "forces": [b.forces.get(i, {"name": "?"}) for i in range(1, max(b.forces, default=0) + 1)],
            "npcs": sorted(b.npcs.values(), key=lambda n: n["id"]),
            "terrain": [[v["bx"], v["by"], v["g"], v.get("tr", "")] for v in lines if v.get("k") == "terrain"],
            "buildings": b.cols, "tracks": {str(k): v for k, v in b.tracks.items()}, "vehicles": b.vehicles,
            "players": b.players, "enemies": b.enemies,
            "states": {str(k): v for k, v in b.states.items()}, "events": b.events, "stats": b.stats,
            "llm": llm, "log": log}


def video_data(run_dir: Path, page_dir: Path) -> dict[str, Any] | None:
    """The run's real-graphics videos (tools/video.py), their paths as the page sees them."""
    path = run_dir / "video" / "video.json"
    if not path.exists():
        return None
    video = json.loads(path.read_text(encoding="utf-8"))
    videos = []
    for v in video.get("videos") or []:
        if (run_dir / v["file"]).exists():
            src = Path(os.path.relpath(run_dir / v["file"], page_dir)).as_posix()
            videos.append({**v, "src": src})
    return {"fps": video.get("fps") or 6, "videos": videos, "every_minutes": video.get("every_minutes")}


def build(run_dir: Path, out: Path | None = None) -> Path:
    run_dir = Path(run_dir)
    manifest_path = run_dir / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else \
        {"runner": "unknown", "purpose": "(no run.json: recording only)", "run_dir": str(run_dir)}
    recording = find_recording(run_dir)
    if recording is None:
        raise FileNotFoundError(f"no recording in {run_dir} (recording.jsonl or profile/script-output/npc-record)")
    out = Path(out) if out else run_dir / "replay.html"
    data = build_data(read_lines(recording), manifest)
    data["meta"].setdefault("run_name", run_dir.name)
    data["video"] = video_data(run_dir, out.resolve().parent)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8")
    if SLOT not in page:
        raise ValueError(f"{TEMPLATE.name} has no data slot")
    out.write_text(page.replace(SLOT, text), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--open", action="store_true", help="open it in the default browser")
    args = parser.parse_args()
    out = build(args.run_dir, args.out)
    print(f"replay: {out} ({out.stat().st_size // 1024} KB)")
    if args.open:
        os.startfile(out)   # noqa: S606 - Windows: the default browser
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
