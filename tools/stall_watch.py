"""A run ends as soon as an engineer is stuck (the user, 2026-10-30: not run on for 200 minutes), with a report of why.

Stuck is either:
- the game's own watchdog (System One's SO-5) logging "[npc-factions] npc=N STALL ..." to the server log; or
- until then, and as a backstop: an engineer alive whose pieces built and plans finished have not changed for
  IDLE_MINUTES game minutes.

The first stall stops the run (mode "stop"), or is only recorded (mode "record": a multi-engineer run, where one
engineer's stall should not end the game). Each stall gets a report in the run folder's stuck.json: the engineer, its
phase, goal and what it waits on, its last 20 decisions, and the tail of its traces.

    watch = StallWatch(run_dir, server_log, mode="stop")
    for stall in watch.look(rcon):   # (every few seconds)
        ...                           # watch.stop is True once a stall in "stop" mode was seen

The mode comes from NPC_STALL (stop|record) when set, else the caller's default.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

IDLE_MINUTES = 10
STALL_LINE = re.compile(r"\[npc-factions\] npc=(\d+) STALL\b(.*)")

# Each engineer as the game has it: phase, goal, what it waits on, and its progress: pieces built, the newest plan it
# finished (its history is capped at 64, so a count alone stops moving), the stage under way, and its research
STATE = ("/sc __npc-factions__ local out={tick=game.tick,npcs={}} for id,npc in pairs(storage.npcs or {}) do "
         "local d=npc.design local c=npc.character local built=0 "
         "for _,p in ipairs(npc.design_history or {}) do built=built+#(p.order or {}) end "
         "if d and d.plan then for _,eid in ipairs(d.plan.order or {}) do local e=d.plan.entities[eid] "
         "if e and e.entity and e.entity.valid then built=built+1 end end end "
         "local h=npc.design_history or {} local r=npc.force.current_research "
         "out.npcs[#out.npcs+1]={id=id,name=npc.name,alive=c~=nil and c.valid,phase=npc.phase,"
         "goal=d and d.purpose,status=d and d.status,reason=d and d.reason and tostring(d.reason),"
         "request=d and d.request_id,plans=#h,newest=h[#h] and h[#h].plan_id,built=built,stage=d and d.stage_index,"
         "research=r and (r.name..':'..math.floor(npc.force.research_progress*100)) or nil} end "
         "rcon.print(helpers.table_to_json(out))")


def mode_of(default: str) -> str:
    mode = (os.environ.get("NPC_STALL") or default).strip().lower()
    return mode if mode in ("stop", "record") else default


class StallWatch:
    def __init__(self, run: Path, server_log: Path, *, mode: str = "stop", idle_minutes: float = IDLE_MINUTES):
        self.run, self.server_log = Path(run), Path(server_log)
        self.mode = mode_of(mode)
        self.idle_ticks = int(idle_minutes * 3600)
        self.seen: dict[Any, tuple] = {}   # npc id -> ((plans, built), tick it last changed)
        self.log_at = 0
        self.stalls: list[dict] = []
        self.stop = False

    def look(self, rcon) -> list[dict]:
        """The stalls new since the last look (each reported to stuck.json); sets self.stop in "stop" mode."""
        found = []
        logged = self._log_stalls()   # (read even when the state cannot be had)
        try:
            state = json.loads(rcon.text(STATE).strip())
        except Exception:   # (a slow or busy line: its engineers next look)
            state = {}
        tick = state.get("tick") or 0
        npcs = {n.get("id"): n for n in state.get("npcs") or []}
        for npc_id, why in logged:
            found.append(self._stall(npcs.get(npc_id) or {"id": npc_id}, f"watchdog: {why.strip()}", tick))
        for npc_id, n in npcs.items():
            progress = (n.get("newest"), n.get("plans"), n.get("built"), n.get("stage"), n.get("research"))
            last = self.seen.get(npc_id)
            if last is None or last[0] != progress:
                self.seen[npc_id] = (progress, tick)
            elif n.get("alive") and tick - last[1] >= self.idle_ticks:
                found.append(self._stall(n, f"no piece built, no plan or stage finished and no research done for "
                                            f"{(tick - last[1]) / 3600:.0f} game minutes", tick))
                self.seen[npc_id] = (progress, tick)   # (recorded once a stretch, in "record" mode)
        if found and self.mode == "stop":
            self.stop = True
        return found

    def _log_stalls(self) -> list[tuple[int, str]]:
        out = []
        try:
            if self.server_log.stat().st_size < self.log_at:
                self.log_at = 0   # (a new or shortened log: read from its start)
            with self.server_log.open(encoding="utf-8", errors="replace") as f:
                f.seek(self.log_at)
                text = f.read()
                self.log_at = f.tell()
        except OSError:
            return out
        for m in STALL_LINE.finditer(text):
            out.append((int(m.group(1)), m.group(2)))
        return out

    def _stall(self, npc: dict, why: str, tick: int) -> dict:
        npc_id = npc.get("id")
        report = {"npc": npc_id, "name": npc.get("name"), "why": why, "game_minute": round(tick / 3600, 2),
                  "phase": npc.get("phase"), "goal": npc.get("goal"),
                  "waits_on": {"status": npc.get("status"), "reason": npc.get("reason"), "request": npc.get("request")},
                  "plans_finished": npc.get("plans"), "pieces_built": npc.get("built"),
                  "decisions": self._decisions(npc_id), "trace": self._trace(npc_id),
                  "stopped": self.mode == "stop", "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.stalls.append(report)
        (self.run / "stuck.json").write_text(json.dumps(self.stalls, indent=2), encoding="utf-8")
        return report

    def _decisions(self, npc_id: Any, keep: int = 20) -> list:
        """Its last decisions from the run's decision logs (data/decisions/*.jsonl), newest last."""
        rows = []
        for path in sorted((self.run / "data" / "decisions").glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                who = row.get("npc", row.get("npc_id")) if isinstance(row, dict) else None
                if who is None or str(who) == str(npc_id):
                    rows.append({"log": path.stem, **row} if isinstance(row, dict) else {"log": path.stem, "row": row})
        rows.sort(key=lambda r: r.get("tick") or r.get("at") or r.get("time") or 0)
        return rows[-keep:]

    def _trace(self, npc_id: Any, keep: int = 30) -> dict:
        """The tail of the runner's log and the game's lines about it."""
        def tail(path: Path, match=None) -> list[str]:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return []
            if match:
                lines = [ln for ln in lines if match in ln]
            return [ln[:300] for ln in lines[-keep:]]
        return {"runner": tail(self.run / "runner.log"), "game": tail(self.server_log, f"npc={npc_id} ")}
