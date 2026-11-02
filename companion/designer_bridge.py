"""Host bridge between the game's layout requests and a layout planner.

Polls ``/npc-plan observe`` over RCON. For each NPC waiting for a layout it asks
the planner for a staged plan and submits it with ``/npc-plan submit``; if the
planner cannot produce one it reports ``/npc-plan fail`` so the NPC falls back
to the scripted starter camp instead of waiting. The game re-validates every plan.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion import layout_planner, ledger, priorities
from companion.rcon import RconClient

MARKER = "NPC_PLAN_RESULT"
Planner = Callable[[Mapping[str, Any]], dict]


def parse_result(payload: str, marker: str = MARKER) -> dict[str, Any]:
    for line in payload.splitlines():
        at = line.find(marker)
        if at >= 0:
            value = json.loads(line[at + len(marker):].strip())
            if isinstance(value, dict):
                return value
    raise ValueError(f"RCON response lacked {marker}: {payload[:200]}")


def locked_log(log: Callable[[str], None] = print) -> Callable[[str], None]:
    """A log several designers can write to at once, a whole line at a time."""
    lock = threading.Lock()

    def write(message: str) -> None:
        with lock:
            log(message)
    return write


AI_BACKENDS = ("codex", "api")


def codex_for(ai: str, log: Callable[[str], None] = print):
    """The Codex runner when the AI goes through Codex (the ChatGPT plan) and it is installed, else None."""
    if ai != "codex":
        return None
    from companion.codex_backend import Codex, CodexError
    try:
        return Codex(log=log)
    except CodexError as exc:
        log(f"no AI: {exc}")
        return None


def make_agent(strategist: str, call_text: Callable[[str], str], *, log: Callable[[str], None] = print,
               experience: Path | None = None, ai: str = "codex"):
    """The strategist bridge: "off", "rules" (scripted stand-in), "auto" (the AI's own default model) or a
    model name, asked through Codex (the ChatGPT plan, the default) or the OpenAI API."""
    if not strategist or strategist == "off":
        return None
    from companion.experience import ExperienceLibrary
    from companion.strategist import DEFAULT_MODEL, AgentBridge, CodexStrategist, RuleStrategist, Strategist
    codex = codex_for(ai, log) if strategist != "rules" else None
    if strategist == "rules" or (ai == "codex" and codex is None):
        brain = RuleStrategist()
    elif codex:
        from companion.codex_backend import STRATEGIST_MODEL
        brain = CodexStrategist(codex, STRATEGIST_MODEL if strategist == "auto" else strategist, log=log)
    else:
        brain = Strategist(DEFAULT_MODEL if strategist == "auto" else strategist, log=log)
    library = ExperienceLibrary(experience) if experience or strategist != "rules" else None
    chooser = None
    if not isinstance(brain, RuleStrategist):
        try:   # System One decides when a decision that worked is repeated instead of asking again
            from companion.catalog_planner import DecisionsChooser
            if codex:
                from companion.codex_backend import CodexChooser
                chooser = DecisionsChooser(provider=CodexChooser(codex))
            else:
                chooser = DecisionsChooser()
        except Exception as exc:
            log(f"System One unavailable for habits ({exc}); proven decisions repeat by rule")
    return AgentBridge(lambda command: parse_result(call_text(command), "NPC_AGENT_RESULT "), brain,
                       library=library, chooser=chooser, log=log)


class DesignerBridge:
    """Answers the engineers' plan and research requests.

    `planner`: one planner for every engineer, asked in turn within poll_once (as it always was).
    `planners`: a factory giving each engineer its own planner (npc_id -> planner, built on first use).
    Then every engineer's designer works at once in a worker of its own, one request per engineer at a
    time, and poll_once never waits for a design: a slow LLM design for one engineer holds up nobody
    else's plans, research or strategist. Finished designs are submitted on a later poll_once."""

    def __init__(self, rcon: Any, planner: Planner | None = None, log: Callable[[str], None] = print,
                 on_request: Callable[[dict], None] | None = None, *,
                 planners: Callable[[int], Planner] | None = None, workers: int = 10):
        if planner is None and planners is None:
            raise ValueError("a planner or a planner factory is needed")
        self.rcon = rcon
        self.on_request = on_request  # e.g. a test harness slowing the game while a layout is designed
        self.planner = planner
        self.factory = planners
        self.log = log
        self.handled: dict[str, str] = {}
        self._lock = threading.RLock()            # one RCON exchange at a time (workers fetch maps too)
        self._planners: dict[int, Planner] = {}
        self._busy: dict[int, Future] = {}        # an engineer's design under way
        self._jobs: dict[str, tuple[str, int, Future]] = {}   # request id -> (plan or research, npc, future)
        self._unsent: set[str] = set()            # finished, but sending it failed: sent again on a later poll
        self._orphans: dict[int, Future] = {}     # designs from before a save was loaded: never sent
        self._deadline: dict[Any, float] = {}     # a job's (request id) or an orphan's (npc) time to give up on it
        self._last_tick: int | None = None
        self._facts: dict[Any, tuple[int, dict | None]] = {}   # force -> (research done, the game's facts export)
        self.generation = 0                       # how many times a save was loaded under it
        self._workers, self._stuck = workers, 0      # (threads of expired jobs still running: cannot be stopped)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="designer") if planners else None
        if planner is not None:
            self._attach(planner)

    # How long a plan or research job may take before it is given up (a hung design held its engineer's later
    # requests for good: SO-5): the request's own answer_by and a margin when it has one, else this long (a bus
    # plan has taken 8 real minutes, run 53).
    JOB_LIFETIME_S = 900
    JOB_MARGIN_S = 60
    MAP_RADIUS = 256   # bases grow: a main bus with blue science spans well over 200 tiles
    MAP_WAIT_S = 60

    @property
    def parallel(self) -> bool:
        return self._pool is not None

    def _attach(self, planner: Planner) -> Planner:
        if getattr(planner, "world", "absent") is None:
            planner.world = self.world
        if getattr(planner, "factory_store", "absent") is None:
            planner.factory_store = self.store_factory_plan
        return planner

    def store_factory_plan(self, npc_id: int, plan: Mapping[str, Any]) -> bool:
        """The engineer's factory plan, for the game to keep in the save (companion/factory_plan.py)."""
        result = self.call("/npc-plan factory " + json.dumps({"npc_id": npc_id, "plan": plan}, separators=(",", ":")))
        return bool(result.get("ok"))

    def planner_for(self, npc_id: int) -> Planner:
        """The engineer's own planner (or the shared one)."""
        if self.factory is None:
            return self.planner
        if npc_id not in self._planners:
            self._planners[npc_id] = self._attach(self.factory(npc_id))
        return self._planners[npc_id]

    @staticmethod
    def npc_of(request: Mapping[str, Any]) -> int:
        npc = request.get("npc_id")
        if isinstance(npc, int):
            return npc
        head = str(request.get("request_id", "")).split(":", 1)[0]
        return int(head) if head.isdigit() else 0

    def world(self, site: Mapping[str, Any], center: Mapping[str, float] | None = None,
              radius: int | None = None) -> dict[str, Any]:
        """A wide map of the engineer's surroundings, for planning a whole base (or, with `center` and
        `radius`, of the ground a plan needs: a wall line far out at the plot's edge). Land the game has not
        generated yet is being generated meanwhile: ask again until it is there."""
        request = {"npc_id": site.get("npc_id", 1), "radius": self.MAP_RADIUS}
        at = (site.get("order") or {}).get("at") if isinstance(site.get("order"), dict) else None
        if isinstance(at, dict) and isinstance(at.get("x"), (int, float)):
            request.update(x=at["x"], y=at["y"], radius=128)   # a build at someone else's base: map around it
        if center is not None:
            request.update(x=center["x"], y=center["y"], radius=int(radius or self.MAP_RADIUS))
        command = "/npc-plan map " + json.dumps(request)
        deadline = time.monotonic() + self.MAP_WAIT_S
        while True:
            dump = self.call(command)
            if dump.get("reason") not in ("generating", "building") or time.monotonic() > deadline:
                return dump
            if dump.get("reason") == "generating":
                self.log(f"waiting for the game to generate land around the base ({dump.get('missing')} chunks)")
                time.sleep(2)
            else:
                time.sleep(0.25)   # the game reads the map a few chunks per tick

    def call(self, command: str) -> dict[str, Any]:
        with self._lock:
            return parse_result(self.rcon.text(command))

    def poll_once(self) -> list[dict[str, Any]]:
        observed = self.call("/npc-plan observe")
        self._notice_reload(observed.get("tick"))
        outcomes = self._collect(observed)
        for row in observed.get("status", []):
            if isinstance(row, dict) and isinstance(row.get("request_id"), str):
                record = getattr(self.planner_for(self.npc_of(row)), "record", None)
                if record:
                    record(row["request_id"], row)
        self._expire()
        for request in observed.get("requests", []):
            request_id = request.get("request_id")
            if not isinstance(request_id, str) or request_id in self.handled:
                continue
            npc = self.npc_of(request)
            if self.parallel and (npc in self._busy or self._orphan_running(npc)):
                continue   # its designer is busy with an earlier request: this one waits its turn
            request = self.with_facts(request, npc)
            if self.on_request:
                self.on_request(request)
            planner = self.planner_for(npc)
            if self.parallel:
                self.handled[request_id] = "designing"
                self._busy[npc] = future = self._pool.submit(planner, request)
                self._jobs[request_id] = ("plan", npc, future)
                self._deadline[request_id] = self._lifetime(request)
                continue
            try:
                plan = planner(request)
            except Exception as exc:  # planner failure must not stall the NPC
                outcome = self._finish(request_id, exc=exc)
            else:
                outcome = self._finish(request_id, plan=plan)
            outcomes.append(outcome)
        learner = self.planner if self.factory is None else self.planner_for(min(self._planners, default=1))
        learn = getattr(learner, "learn", None)
        for entry in observed.get("taught", []) if learn else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), int):
                continue
            try:
                purpose = learn(entry)
            except Exception as exc:  # a blueprint we cannot use must not stall the bridge
                purpose = None
                self.log(f"taught blueprint {entry.get('label')!r} not usable ({exc})")
            self.call("/npc-plan learned " + json.dumps({"id": entry["id"], "purpose": purpose or "unused"}))
            outcomes.append({"taught": entry["id"], "purpose": purpose})
        for request in observed.get("research_requests", []):
            request_id = request.get("request_id")
            if not isinstance(request_id, str) or request_id in self.handled:
                continue
            npc = self.npc_of(request)
            choose_research = getattr(self.planner_for(npc), "choose_research", None)
            if not choose_research:
                continue
            if self.parallel:   # (System One may take a while: not on the loop either)
                self.handled[request_id] = "choosing"
                self._jobs[request_id] = ("research", npc, self._pool.submit(choose_research, request))
                self._deadline[request_id] = self._lifetime(request)
                continue
            try:
                technology = choose_research(request)
            except Exception as exc:
                self.handled[request_id] = "research-failed"
                self.log(f"{request_id}: research choice failed ({exc}); the NPC uses its default")
                continue
            outcomes.append(self._research(request_id, technology))
        return outcomes + self._collect(observed)

    def with_facts(self, request: dict, npc: int) -> dict:
        """The request with the game's facts (what its machines do, companion/rates.py), fetched once per
        force and research level: research changes what can be built and the bonuses."""
        force, level = request.get("force"), len(request.get("researched") or ())
        kept = self._facts.get(force)
        if kept is None or kept[0] != level:   # only the latest per force is kept
            try:
                facts = self.call("/npc-plan facts " + json.dumps({"npc_id": npc}))
            except OSError:
                raise   # the line dropped: the runner reconnects, as for any other call
            except Exception as exc:   # an answer it cannot read (an older mod): planned without them
                self.log(f"game facts for {force} not read ({exc})")
                facts = {}
            kept = self._facts[force] = (level, facts if facts.get("ok") and facts.get("entities") else None)
        return {**request, "game_facts": kept[1]} if kept[1] else request

    def _notice_reload(self, tick: Any) -> None:
        """A save was loaded (the game's tick went back): request ids start over, so nothing asked before
        may be answered now. Designs under way are left to finish and thrown away; until then their
        engineer gets no new design (its designer is still busy)."""
        if not isinstance(tick, int):
            return
        if self._last_tick is not None and tick < self._last_tick:
            self.forget(f"the game went back to tick {tick} (a save was loaded)")
        self._last_tick = tick

    def forget(self, why: str = "the game was reconnected") -> None:
        """Nothing asked before may be answered now (a save loaded; or a reconnect, after which a loaded
        save may already be past the old tick): designs under way finish and are thrown away."""
        self.generation += 1
        self.log(f"{why}: earlier requests are forgotten")
        ledger.forget()   # (the factory as it stood in the other game)
        priorities.forget()
        from companion import failures, goal_brain
        failures.forget()   # (goals held for failures in the other game, and what its strategist asked: SO-4)
        goal_brain.forget()
        self._facts.clear()
        for request_id, (kind, npc, future) in self._jobs.items():
            if kind == "plan" and not future.done():
                self._orphans[npc] = future
                self._deadline[npc] = self._deadline.get(request_id, time.monotonic() + self.JOB_LIFETIME_S)
        self._jobs.clear()
        self._deadline = {k: v for k, v in self._deadline.items() if not isinstance(k, str)}
        self._busy.clear()
        self._unsent.clear()
        self.handled.clear()
        self._last_tick = None

    def _orphan_running(self, npc: int) -> bool:
        future = self._orphans.get(npc)
        if future is not None and future.done():
            del self._orphans[npc]
            self._deadline.pop(npc, None)
            future = None
        return future is not None

    def _lifetime(self, request: Mapping[str, Any]) -> float:
        """When to give up on a job for `request` (time.monotonic())."""
        by = request.get("answer_by")
        if isinstance(by, (int, float)):
            return float(by) + self.JOB_MARGIN_S
        return time.monotonic() + self.JOB_LIFETIME_S

    def _expire(self) -> None:
        """Jobs and orphans past their lifetime given up: a plan's request is failed in the game (the engineer goes
        on with its next goal), a research choice left to the game's default, and the engineer's next request let
        through. A worker thread cannot be stopped: a hung one keeps its thread, and its late result is dropped."""
        now = time.monotonic()
        for request_id, (kind, npc, future) in list(self._jobs.items()):
            if future.done() or now < self._deadline.get(request_id, float("inf")):
                continue   # (one done is delivered, late or not: the game takes late plans)
            if not future.cancel():
                self._stuck += 1   # (its thread runs on)
            self._jobs.pop(request_id, None)
            self._deadline.pop(request_id, None)
            self._unsent.discard(request_id)
            if kind == "plan":
                self._busy.pop(npc, None)
                # (the next request gets a fresh planner: the stuck one may still be writing its own state)
                self._planners.pop(npc, None)
                self.handled[request_id] = "expired"
                self.log(f"{request_id}: the design did not come in time; given up, the engineer goes on")
                try:
                    self.call("/npc-plan fail " + json.dumps({"request_id": request_id, "reason": "designer_expired"}))
                except OSError:
                    raise   # (the line dropped: the runner reconnects, and forget() clears the rest)
                except Exception as exc:
                    self.log(f"{request_id}: the game was not told ({exc})")
            else:
                self.handled[request_id] = "research-expired"
                self.log(f"{request_id}: the research choice did not come in time; the NPC uses its default")
        for npc, future in list(self._orphans.items()):
            if not future.done() and now >= self._deadline.get(npc, float("inf")):
                if not future.cancel():
                    self._stuck += 1
                del self._orphans[npc]
                self._deadline.pop(npc, None)
                self.log(f"engineer {npc}: a design from before the save was loaded did not finish; given up")
        if self._pool is not None and self._stuck >= max(1, self._workers // 2):
            # Half the workers hold jobs given up on: a fresh pool for what comes next (the old threads end when
            # their calls do; the old pool is shut down without waiting).
            self.log(f"{self._stuck} given-up jobs still hold worker threads; starting a fresh pool")
            old_pool, self._pool, self._stuck = self._pool, ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix="designer"), 0
            old_pool.shutdown(wait=False)

    @staticmethod
    def _answered(request_id: str, kind: str, observed: Mapping[str, Any]) -> bool:
        """Whether the game no longer waits for this request (an answer sent before got there, or it
        moved on). A design the engineer stopped waiting for is still welcome: the game takes late plans."""
        if kind == "research":
            return not any(isinstance(r, dict) and r.get("request_id") == request_id
                           for r in observed.get("research_requests", []))
        if any(isinstance(r, dict) and r.get("request_id") == request_id for r in observed.get("requests", [])):
            return False
        row = next((r for r in observed.get("status", []) if isinstance(r, dict) and r.get("request_id") == request_id),
                   None)
        return not (row and row.get("status") == "deferred" and row.get("reason") == "designer_timeout")

    def _collect(self, observed: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Results of the workers that are done, sent to the game. A result is kept until it is sent: when
        the line drops while sending, it goes again on a later poll, unless the game shows it no longer
        waits for it."""
        outcomes = []
        for request_id, (kind, npc, future) in list(self._jobs.items()):
            if not future.done():
                continue
            if request_id in self._unsent and observed is not None and self._answered(request_id, kind, observed):
                self._done(request_id, kind, npc)
                self.handled[request_id] = "sent"
                self.log(f"{request_id}: sent before the line dropped (the game is not waiting for it now)")
                continue
            try:
                outcome = self._deliver(request_id, kind, future)
            except Exception:
                self._unsent.add(request_id)   # (the line to the game dropped: kept for the next poll)
                raise
            self._done(request_id, kind, npc)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _done(self, request_id: str, kind: str, npc: int) -> None:
        self._jobs.pop(request_id, None)
        self._deadline.pop(request_id, None)
        self._unsent.discard(request_id)
        if kind == "plan":
            self._busy.pop(npc, None)

    def _deliver(self, request_id: str, kind: str, future: Future) -> dict[str, Any] | None:
        """One worker's result told to the game. Raises only when talking to the game fails."""
        try:
            value = future.result()
        except Exception as exc:  # planner failure must not stall the NPC
            if kind == "plan":
                return self._finish(request_id, exc=exc)
            self.handled[request_id] = "research-failed"
            self.log(f"{request_id}: research choice failed ({exc}); the NPC uses its default")
            return None
        if kind == "plan":
            return self._finish(request_id, plan=value)
        return self._research(request_id, value)

    def busy(self) -> bool:
        """Whether any designer (or research choice) is still at work."""
        return any(not future.done() for _, _, future in self._jobs.values())

    def drain(self, timeout: float = 60) -> list[dict[str, Any]]:
        """Wait for the designers at work (at most `timeout` seconds) and send what they made."""
        deadline = time.monotonic() + timeout
        while self.busy() and time.monotonic() < deadline:
            time.sleep(0.05)
        return self._collect()

    def close(self) -> None:
        """Stop taking work; designs under way are abandoned (the game falls back after its own wait)."""
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _research(self, request_id: str, technology: Any) -> dict[str, Any]:
        payload = {"request_id": request_id, "technology": technology}
        try:   # (how much it is needed, for the team's research queue: catalog_planner.research_need, R-9)
            from companion import catalog_planner
            need = catalog_planner.RESEARCH_NEEDS.get(str(request_id))
        except Exception:
            catalog_planner, need = None, None
        if need:
            payload["need"] = need
        if technology == "":   # (catalog_planner.IDLE: nothing offered serves anything, the labs wait; R-10)
            payload["idle"] = True
            unused = catalog_planner.RESEARCH_UNUSED.get(str(request_id)) if catalog_planner is not None else None
            if unused is not None:   # (only these set aside: military held back for its share stays on offer)
                payload["unused"] = unused
        result = self.call("/npc-plan research " + json.dumps(payload))
        if catalog_planner is not None:   # (told: kept until then, a failed send retries with it)
            catalog_planner.RESEARCH_NEEDS.pop(str(request_id), None)
            catalog_planner.RESEARCH_UNUSED.pop(str(request_id), None)
        self.handled[request_id] = f"research:{technology}" if result.get("accepted") else "research-rejected"
        return {"request_id": request_id, "result": result}

    def _finish(self, request_id: str, plan: dict | None = None, exc: BaseException | None = None) -> dict[str, Any]:
        """What the planner came back with, told to the game: a plan, a move, scouting, a wait or a failure."""
        if exc is not None and type(exc).__name__ == "MoveSite":
            result = self.call("/npc-plan site " + json.dumps(
                {"request_id": request_id, "purpose": exc.purpose, "dx": exc.dx, "dy": exc.dy, "goal": exc.goal}))
            self.handled[request_id] = "moved"
            if not result.get("accepted"):
                self.call("/npc-plan fail " + json.dumps(
                    {"request_id": request_id, "reason": f"site:{result.get('reason')}"[:80]}))
            return {"request_id": request_id, "result": result}
        if exc is not None and type(exc).__name__ == "Scout":
            result = self.call("/npc-plan scout " + json.dumps(
                {"request_id": request_id, "purpose": exc.purpose, "resource": exc.resource}))
            self.handled[request_id] = "scouting"
            self.log(f"{request_id}: scouting for {exc.resource}")
            return {"request_id": request_id, "result": result}
        if exc is not None and type(exc).__name__ == "Defer":
            result = self.call("/npc-plan defer " + json.dumps(
                {"request_id": request_id, "reason": exc.reason, "ticks": exc.ticks}))
            self.handled[request_id] = "deferred"
            self.log(f"{request_id}: deferred ({exc.reason})")
            return {"request_id": request_id, "result": result}
        if exc is not None:
            failed = {"request_id": request_id, "reason": f"{type(exc).__name__}: {exc}"[:80]}
            if isinstance(getattr(exc, "purpose", None), str):
                failed["purpose"] = exc.purpose   # the goal it failed at (a chosen one's request says "choose")
            if isinstance(getattr(exc, "escalate", None), int):
                failed["escalate"] = exc.escalate   # failed so often for one reason: its strategist hears (SO-4)
            result = self.call("/npc-plan fail " + json.dumps(failed))
            self.handled[request_id] = "failed"
            self.log(f"{request_id}: planner failed ({exc}); NPC falls back")
            if type(exc).__name__ != "PlanError":   # a bug, not a plan that cannot be made: say where
                import traceback
                frames = traceback.format_exception(type(exc), exc, exc.__traceback__)
                self.log("  " + " | ".join(" ".join(f.split()) for f in frames[-4:-1]))
            return {"request_id": request_id, "result": result}
        from companion import rcon
        command = "/npc-plan submit " + json.dumps({"request_id": request_id, "plan": plan}, separators=(",", ":"))
        if len(command.encode("utf-8")) > rcon.MAX_COMMAND:
            # (too big to send: the request fails, as a plan that cannot be made, rather than the link)
            self.call("/npc-plan fail " + json.dumps({"request_id": request_id, "reason": "plan_too_large",
                                                      "purpose": plan.get("purpose")}))
            self.handled[request_id] = "failed"
            self.log(f"{request_id}: plan {plan.get('plan_id')} too large to send ({len(command)} bytes); NPC falls back")
            return {"request_id": request_id, "result": {"accepted": False, "reason": "plan_too_large"}}
        result = self.call(command)
        self.handled[request_id] = "accepted" if result.get("accepted") else "rejected"
        if not result.get("accepted"):
            # Tell the game now so the NPC retries later instead of waiting out the timeout.
            self.call("/npc-plan fail " + json.dumps(
                {"request_id": request_id, "reason": f"rejected:{result.get('reason')}"[:80]}))
        resolved = result.get("overlaps_resolved") or 0
        self.log(f"{request_id}: plan {plan.get('plan_id')} "
                 f"{'accepted' if result.get('accepted') else 'rejected: ' + str(result.get('reason'))}"
                 + (f" ({resolved} overlapping buildings merged or gave way)" if resolved else ""))
        return {"request_id": request_id, "result": result}


def starter_planner(request: Mapping[str, Any]) -> dict:
    return layout_planner.plan(request)


def make_planners(name: str, *, model: str | None = None, log: Callable[[str], None] = print,
                  catalog: str | Path | None = None, designer: bool = True,
                  chooser: bool = True, ai: str = "codex") -> Callable[[int], Planner]:
    """A planner for each engineer (npc_id -> planner): its own designer and its own System One, so the
    engineers design at once (DesignerBridge(planners=...)); all of them share one blueprint library.
    starter: deterministic only. llm: designer LLM, deterministic fallback. llm-only: no fallback.
    catalog: reuse saved blueprints chosen by System One; design new ones only when needed.
    ai: "codex" (the ChatGPT plan, the default) or "api" (the paid OpenAI API)."""
    if name == "starter":
        return lambda npc_id: starter_planner
    codex = codex_for(ai, log) if designer or chooser else None
    if ai == "codex":
        from companion.codex_backend import DESIGNER_MODEL
        usable, default_model = codex is not None, DESIGNER_MODEL
    else:
        from companion.decisions_api import api_key_from_environment
        from companion.designer_planner import DEFAULT_MODEL
        usable, default_model = api_key_from_environment() is not None, DEFAULT_MODEL

    def designer_llm():
        from companion.designer_planner import LLMPlanner
        return LLMPlanner(model=model or default_model, log=log, codex=codex)

    if name == "catalog":
        from companion.blueprints import Catalog
        from companion.catalog_planner import CatalogPlanner, DecisionsChooser
        library = Catalog(catalog)
        log(f"blueprint library {library.path}: {len(library)} saved; AI through {ai}; designer "
            f"{(model or default_model) if designer and usable else 'off'}, "
            f"system one chooser {'on' if chooser and usable else 'off'}")

        def build(npc_id: int) -> Planner:
            llm = designer_llm() if designer and usable else None
            pick = None
            if chooser and usable:
                if codex:
                    from companion.codex_backend import CodexChooser
                    pick = DecisionsChooser(provider=CodexChooser(codex))
                else:
                    pick = DecisionsChooser()
            # The architect plans the factory's large sections with the designer's model (through Codex).
            architect = None
            if llm is not None and codex:
                from companion.architect import Architect
                architect = Architect(codex, model=model or default_model, log=log)
            planner = CatalogPlanner(library, designer=llm, chooser=pick, log=log, architect=architect)
            if llm is not None and codex:   # (a blueprint pack's layout the rules cannot read: the designer, once)
                from companion.codex_backend import CHOOSER_MODEL
                from companion.blueprint_packs import ASK_SECONDS
                planner.annotator = lambda prompt, schema: codex.ask(prompt, schema, model=CHOOSER_MODEL,
                                                                     system="blueprint-pack", purpose="blueprint-pack",
                                                                     timeout_s=ASK_SECONDS)
            return planner
        return build

    def build_llm(npc_id: int) -> Planner:
        from companion.designer_planner import with_fallback
        llm = designer_llm()
        return llm if name == "llm-only" else with_fallback(llm, starter_planner, log)
    return build_llm


def make_planner(name: str, *, model: str | None = None, log: Callable[[str], None] = print,
                 catalog: str | Path | None = None, designer: bool = True, chooser: bool = True,
                 ai: str = "codex") -> Planner:
    """One planner for every engineer (see make_planners for the kinds)."""
    return make_planners(name, model=model, log=log, catalog=catalog, designer=designer, chooser=chooser,
                         ai=ai)(0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rcon-file", type=Path, required=True,
                        help="JSON with host, port and password for the local RCON endpoint")
    parser.add_argument("--planner", choices=["starter", "llm", "llm-only", "catalog"], default="catalog")
    parser.add_argument("--ai", choices=AI_BACKENDS, default="codex",
                        help="the AI through Codex (your ChatGPT plan, the default) or the paid OpenAI API")
    parser.add_argument("--model", help="designer model (default: gpt-6.1-sol)")
    parser.add_argument("--catalog", type=Path, help="blueprint library directory (default: per-user data folder)")
    parser.add_argument("--no-designer", action="store_true", help="catalog mode: never call the designer LLM")
    parser.add_argument("--no-chooser", action="store_true", help="catalog mode: pick by score, not System One")
    parser.add_argument("--enable-designed-start", action="store_true",
                        help="switch the game's start mode to designed when connected")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--strategist", default="off",
                        help="engineers' decisions: off, rules (scripted), or an OpenAI model such as gpt-6-luna")
    parser.add_argument("--experience", type=Path, help="experience library directory (default: local data)")
    args = parser.parse_args()
    endpoint = json.loads(args.rcon_file.read_text(encoding="utf-8"))
    with RconClient(endpoint.get("host", "127.0.0.1"), int(endpoint["port"]), endpoint["password"]) as rcon:
        # every engineer its own designer, all at work at once
        log = locked_log(lambda line: print(line, flush=True))
        planners = make_planners(args.planner, model=args.model, catalog=args.catalog, log=log, ai=args.ai,
                                 designer=not args.no_designer, chooser=not args.no_chooser)
        bridge = DesignerBridge(rcon, log=log, planners=planners)
        if args.enable_designed_start:
            print("start mode:", bridge.call("/npc-plan mode designed").get("mode"), flush=True)
        agent = make_agent(args.strategist, rcon.text, log=log, experience=args.experience, ai=args.ai)
        try:
            while True:
                bridge.poll_once()
                if agent:
                    agent.poll_once()
                time.sleep(args.poll_seconds)
        finally:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
