"""The companion for a Biter World game you host from your Steam client.

Once, before the first game:

    python tools/attach_runner.py --setup

writes the RCON endpoint (with a fresh password) to %LOCALAPPDATA%\\npc-factions\\rcon.json and the two
lines to add under [other] in Factorio's config.ini to config-ini-lines.txt beside it. The password is
never printed or logged.

Every game, after you host the save:

    python tools/attach_runner.py

connects (and waits while the game is not up yet), answers the engineers' plan requests (blueprints, the
layout designer and System One) and runs their strategists, reconnecting whenever the game goes away.
Its run folder (default %LOCALAPPDATA%\\npc-factions\\runs\\<time>) holds runner.log and status.json
(connected, game tick, roster, plans, LLM calls and cost so far). Create a file named stop-now there,
or press Ctrl+C, to stop.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion import usage  # noqa: E402
from companion.decisions_api import out_of_credit  # noqa: E402
from companion.designer_bridge import DesignerBridge, locked_log, make_agent, make_planners  # noqa: E402
from companion.rcon import ProtocolError, RconClient  # noqa: E402

HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "npc-factions"
ENDPOINT = HOME / "rcon.json"
DEFAULT_PORT = 27015
AGENT_MARK = "NPC_AGENT_RESULT "
TEAM_MARK = "NPC_TEAM_RESULT "
BACKOFF = (1, 2, 4, 8, 15, 30)


class Disconnected(ConnectionError):
    """The game is not there (not hosted yet, closed, or the line dropped)."""


def setup(path: Path = ENDPOINT, port: int = DEFAULT_PORT, *, force: bool = False,
          say: Callable[[str], None] = print) -> tuple[Path, Path]:
    """Write the endpoint file (keeping an existing password unless `force`) and the config.ini lines
    beside it. Says only where the files are."""
    path.parent.mkdir(parents=True, exist_ok=True)
    endpoint = None
    if path.exists() and not force:
        try:
            endpoint = load_endpoint(path)
        except (ValueError, KeyError, json.JSONDecodeError):
            endpoint = None
    if endpoint is None:
        endpoint = {"host": "127.0.0.1", "port": int(port), "password": secrets.token_urlsafe(24)}
        path.write_text(json.dumps(endpoint), encoding="utf-8")
    lines = path.with_name("config-ini-lines.txt")
    lines.write_text("[other]\n"
                     f"local-rcon-socket=127.0.0.1:{endpoint['port']}\n"
                     f"local-rcon-password={endpoint['password']}\n", encoding="utf-8")
    say(f"RCON endpoint: {path}")
    say(f"Lines for Factorio's config.ini (under [other], replacing the two commented ones): {lines}")
    return path, lines


def load_endpoint(path: Path = ENDPOINT) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    host, port, password = raw.get("host", "127.0.0.1"), raw.get("port"), raw.get("password")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("the RCON endpoint must be this computer")
    if not isinstance(port, int) or not 1 <= port <= 65535 or not isinstance(password, str) or not password:
        raise ValueError(f"bad RCON endpoint file {path}")
    return {"host": host, "port": port, "password": password}


class Link:
    """One RCON line to the game that is opened when needed: text() raises Disconnected while the game is
    away, and the next call tries again."""

    def __init__(self, endpoint: dict[str, Any], *, timeout: float = 60,
                 connect: Callable[[dict[str, Any], float], Any] | None = None):
        self._endpoint = endpoint
        self.timeout = timeout
        self._connect = connect or (lambda e, t: RconClient(e["host"], e["port"], e["password"], timeout=t))
        self.client: Any = None
        self.connects = 0
        self._lock = threading.RLock()   # (the engineers' designers fetch maps from their own threads)

    @property
    def connected(self) -> bool:
        return self.client is not None

    def ensure(self) -> None:
        """Connected now (or Disconnected): the runner connects here, in its own thread, before polling."""
        with self._lock:
            if self.client is None:
                try:
                    self.client = self._connect(self._endpoint, self.timeout)
                except (OSError, ProtocolError, PermissionError) as exc:
                    raise Disconnected(f"cannot reach the game: {type(exc).__name__}") from exc
                self.connects += 1

    def text(self, command: str) -> str:
        with self._lock:
            # Only ensure() connects (the runner's own thread, which then makes the bridges forget what
            # was asked before): a designer's thread finding the line down just fails its map.
            if self.client is None:
                raise Disconnected("not connected to the game")
            try:
                return self.client.text(command)
            except (OSError, ProtocolError, ConnectionError) as exc:
                self.close()
                raise Disconnected(f"lost the game: {type(exc).__name__}") from exc

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except OSError:
                pass
            self.client = None


def make_log(path: Path, echo: Callable[[str], None] | None = print) -> Callable[[str], None]:
    """A log that stamps each line, appends it to `path` and echoes it."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(line: str) -> None:
        text = f"{time.strftime('%H:%M:%S')} {line}"
        with path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
        if echo:
            echo(text)
    return log


def parse(text: str, marker: str) -> dict[str, Any]:
    at = text.rfind(marker)
    return json.loads(text[at + len(marker):].strip()) if at >= 0 else {}


class Runner:
    """The bridge (plans) and the strategists, polled until stopped; status and cost written as it goes."""

    def __init__(self, link: Link, run_dir: Path, *, planner: Any = None, planners: Any = None,
                 strategist: str = "auto", ai: str = "codex",
                 experience: Path | None = None, poll_seconds: float = 1.0, status_every: float = 5,
                 cost_every: float = 600, log: Callable[[str], None] | None = None,
                 bridge_factory: Callable[..., Any] = DesignerBridge, agent_factory: Callable[..., Any] = make_agent,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.link, self.run_dir = link, run_dir
        # (the user's blueprint packs for this game's library, unless a folder is named: companion/blueprint_packs.py)
        from companion import blueprint_packs
        packs = blueprint_packs.user_folder()
        if packs is not None:
            os.environ.setdefault("NPC_BLUEPRINT_PACKS", str(packs))
        run_dir.mkdir(parents=True, exist_ok=True)
        self.status_path = run_dir / "status.json"
        self.stop_path = run_dir / "stop-now"
        self.log = log or make_log(run_dir / "runner.log")
        self.sleep, self.clock = sleep, clock
        self.poll_seconds, self.status_every, self.cost_every = poll_seconds, status_every, cost_every
        self.mark = usage.mark()
        self.started = clock()
        # Where the plans come from: each engineer its own designer (planners: npc_id -> planner), all at
        # once; or one shared planner (planner), as the tests use.
        if planners is not None:
            self.bridge = bridge_factory(link, log=self.log, planners=planners)
        else:
            self.bridge = bridge_factory(link, planner, log=self.log)
        self.ai = ai
        self.agent = agent_factory(strategist, link.text, log=self.log, experience=experience, ai=ai)
        self.last_tick: int | None = None
        self._connects = 0
        self.last_error: str | None = None
        self.roster: list[dict] = []

    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def step(self) -> None:
        # Connected before anything is sent: after a reconnect a loaded save may already be past the old
        # tick, so nothing asked before is answered now.
        self.link.ensure()
        if self.link.connects != self._connects:
            if self._connects:
                for side in (self.bridge, self.agent):
                    forget = getattr(side, "forget", None)
                    if forget:
                        forget("reconnected to the game")
            self._connects = self.link.connects
        self.bridge.poll_once()
        if self.agent:
            self.agent.poll_once()

    def plans(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in getattr(self.bridge, "handled", {}).values():
            key = "research" if str(outcome).startswith("research") else str(outcome)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def cost(self) -> dict[str, Any]:
        return usage.summary(usage.entries(self.mark))["total"]

    def look(self) -> None:
        """The game's tick and roster, for the status file. (A reload, the tick going back, is noticed by
        the bridges themselves, before they answer anything: request ids start over.)"""
        state = parse(self.link.text("/npc-agent status"), AGENT_MARK)
        tick = state.get("tick")
        if isinstance(tick, int):
            self.last_tick = tick
        npcs = state.get("npcs")
        self.roster = [{"name": n.get("name"), "phase": n.get("phase"), "design": n.get("design")}
                       for n in npcs if isinstance(n, dict)] if isinstance(npcs, list) else []

    def write_status(self, connected: bool) -> None:
        cost = self.cost()
        status = {"connected": connected, "tick": self.last_tick, "minutes": round((self.clock() - self.started) / 60, 1),
                  "roster": self.roster, "plans": self.plans(),
                  "llm": {"calls": cost["calls"], "failed": cost["failed"], "cost_usd": cost["cost"],
                          "through": self.ai, "out_of_credit": out_of_credit()},
                  "last_error": self.last_error, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    def cost_line(self) -> None:
        cost = self.cost()
        self.log(f"LLM so far: {cost['calls']} calls, ${cost['cost']:.4f}"
                 + (" (Codex: on your ChatGPT plan)" if self.ai == "codex" else "")
                 + (f" ({cost['unpriced']} unpriced)" if cost["unpriced"] else "")
                 + ("; OpenAI credit used up, the engineers go by rule" if out_of_credit() else ""))

    def run(self, until: float | None = None) -> None:
        """Until stop-now, Ctrl+C or `until` (a clock() time): poll, back off while the game is away."""
        failures, next_status, next_cost = 0, self.clock(), self.clock() + self.cost_every
        connected = False
        try:
            while not self.stop_requested() and (until is None or self.clock() < until):
                try:
                    self.step()
                    if not connected:
                        self.log("connected to the game")
                    connected, failures, self.last_error = True, 0, None
                    if self.clock() >= next_status:
                        self.look()
                except Disconnected as exc:
                    if connected or failures == 0:
                        self.log(f"{exc}; waiting for the game")
                    connected, self.last_error = False, str(exc)
                    self.sleep(BACKOFF[min(failures, len(BACKOFF) - 1)])
                    failures += 1
                    continue
                except Exception as exc:   # one bad answer must not end the game's companion
                    self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    self.log(f"poll failed ({self.last_error}); going on")
                    self.sleep(5)
                    continue
                if self.clock() >= next_status:
                    self.write_status(connected)
                    next_status = self.clock() + self.status_every
                if self.clock() >= next_cost:
                    self.cost_line()
                    next_cost = self.clock() + self.cost_every
                self.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            self.log("stopping (Ctrl+C)")
        finally:
            if self.stop_requested():
                self.log("stopping (stop-now)")
            close = getattr(self.bridge, "close", None)
            if close:
                close()   # (designs under way are dropped: the game asks again)
            self.write_status(connected)
            self.cost_line()
            self.link.close()


def spawn_now(link: Link, log: Callable[[str], None], *, sleep: Callable[[float], None] = time.sleep,
              tries: int = 40) -> dict[str, Any]:
    """Bring the engineers in now (they normally arrive when the host joins), once the game answers."""
    for attempt in range(tries):
        try:
            link.ensure()
            result = parse(link.text("/npc-team spawn"), TEAM_MARK)
            log(f"spawn: {result.get('spawned')} engineers" if result.get("ok")
                else f"spawn refused: {result.get('reason')}")
            return result
        except Disconnected:
            sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    log("spawn: the game never answered")
    return {"ok": False, "reason": "no_game"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--setup", action="store_true", help="write the endpoint and the config.ini lines, then stop")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="RCON port for --setup")
    parser.add_argument("--force", action="store_true", help="--setup: a new password even if one exists")
    parser.add_argument("--endpoint", type=Path, default=ENDPOINT)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--planner", choices=["starter", "llm", "llm-only", "catalog"], default="catalog")
    parser.add_argument("--no-designer", action="store_true", help="never call the layout designer")
    parser.add_argument("--no-chooser", action="store_true", help="pick blueprints by score, not System One")
    parser.add_argument("--ai", choices=["codex", "api"], default="codex",
                        help="the AI through Codex (your ChatGPT plan, the default) or the paid OpenAI API")
    parser.add_argument("--model", help="layout designer model (default: gpt-6.1-sol)")
    parser.add_argument("--catalog", type=Path, help="blueprint library folder (default: per-user data folder)")
    parser.add_argument("--strategist", default="auto",
                        help="auto (gpt-6.1-sol on Codex, gpt-6-luna on the API), a model name, rules, or off")
    parser.add_argument("--experience", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--minutes", type=float, help="stop after this many minutes")
    parser.add_argument("--spawn-now", action="store_true",
                        help="bring the engineers in now, if they did not arrive when you joined")
    args = parser.parse_args()
    if args.setup:
        setup(args.endpoint, args.port, force=args.force)
        return 0
    try:
        endpoint = load_endpoint(args.endpoint)
    except FileNotFoundError:
        print(f"No endpoint at {args.endpoint}: run this once with --setup first.")
        return 2
    run_dir = args.run_dir or HOME / "runs" / time.strftime("%Y%m%d-%H%M%S")
    log = locked_log(make_log(run_dir / "runner.log"))
    log(f"run folder: {run_dir} (create a file named stop-now there to stop)")
    link = Link(endpoint)
    if args.spawn_now:
        spawn_now(link, log)
    planners = make_planners(args.planner, model=args.model, catalog=args.catalog, log=log, ai=args.ai,
                             designer=not args.no_designer, chooser=not args.no_chooser)
    runner = Runner(link, run_dir, planners=planners, strategist=args.strategist, ai=args.ai,
                    experience=args.experience,
                    poll_seconds=args.poll_seconds, log=log)
    runner.run(until=time.monotonic() + args.minutes * 60 if args.minutes else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
