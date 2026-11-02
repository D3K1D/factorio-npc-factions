"""The engineers' AI through Codex, on the user's ChatGPT plan, instead of the paid OpenAI API.

One `codex exec` run per question, its answer forced into a JSON schema. It is locked down, because
players' chat lines reach the prompts: no shell, browser, apps, plugins, images or helpers; a read-only
sandbox in an empty folder; the user's Codex config, rules and history are not loaded, and nothing is
saved. The API key is taken out of its environment, so Codex answers on the ChatGPT login and nothing is
charged to API credit. Codex's usage limit reached: the AI pauses for a while and the engineers go on by
rule (decisions_api.pause_ai).

A model's rate limit (HTTP 429, per model on the ChatGPT plan) is another thing: Codex waits out the reset
time the server names, minutes at a time, and says so only in its log (2026-10-26: 4 of 20 strategist runs
in run 52 sat silent until the 120 s limit). So each run's log is read as it comes: a retry wait longer
than the run can afford stops it at once, the model cools down for every caller until the reset, and a
caller that named a fallback model (a smaller one, rate-limited on its own) is answered by that instead."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from companion import decisions_api
from companion import usage as ledger

# Codex features that act outside the answer: all off.
DISABLED = ("shell_tool", "unified_exec", "browser_use", "browser_use_external", "computer_use", "apps",
            "image_generation", "multi_agent", "plugins", "remote_plugin", "hooks", "view_image", "sleep_tool",
            "skill_search", "tool_suggest", "goals", "in_app_browser", "code_mode_host",
            "workspace_dependencies", "worktrees")
MAX_RUNNING = 5            # Codex runs at once, for every caller together (7 first designs: 3 kept the last waiting 80 s)
BACKGROUND_RUNNING = 2     # the architect's background runs, on slots of their own (up to 600 s each: on the shared
                           # ones, seven engineers' architects would hold up every design a request waits on)
BACKGROUND = ("architect",)
TIMEOUT_S = 120   # designs take 15 to 90 s on the user's mods; past this one is stuck (a lab design held its
                  # engineer the whole 180 s it was given, one-engineer hour 2026-10-24)
LIMIT_PAUSE_S = 30 * 60    # usage limit reached: ask again after this long
# Codex's own retry notices on stderr (and errors only, else nothing): "retrying sampling request (1/5 in
# 373.99s)... sampling_error=exceeded retry limit, last status: 429 Too Many Requests" (codex-cli 0.163).
RETRY_LOG = "error,codex_core::responses_retry=warn"
RETRY_NOTICE = re.compile(r"retrying sampling request \(\d+/\d+ in ([\d.]+)(ms|s)\)(?:.*?sampling_error=(.*))?")
RATE_LIMITED = ("429", "too many requests", "rate limit")   # one model's limit: only that model cools down
RATE_COOL_S = 60           # a model's rate limit that named no reset time: left alone this long
ANSWER_AFTER_S = 10        # a retry is worth waiting for only if this much is left after it to answer in
MIN_TRY_S = 5              # a fallback with less time than this left is not started
CLEANUP_S = 2              # a finished or stopped run's output still to be read: at most this long
LIMIT_WORDS = ("usage limit", "limit reached", "try again in", "quota")   # the plan's own limit: all AI pauses
SECRET_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID")
BASE_INSTRUCTIONS = ("You answer for the NPC engineers of a Factorio game, as the request describes. Use no tools. "
                     "Reply only with the JSON the response schema asks for.\n")
# A design run may look a few facts up (companion/knowledge_mcp.py) that its brief did not carry. Codex reaches
# tools only through its code mode, so that run keeps the code-mode host; the server is read-only and these
# runs never carry players' chat. Every other run stays as locked down as before.
DESIGNER_LOOKUPS = 3
LOOKUP_INSTRUCTIONS = ("You answer for the NPC engineers of a Factorio game, as the request describes. The request "
                       "carries this game's numbers; if a fact you need is missing, you may look up at most {n} with "
                       "the factorio tools (recipe, machine, rate, steam, supply). Use no other tools. Reply only "
                       "with the JSON the response schema asks for.\n")
KNOWLEDGE_SERVER = Path(__file__).with_name("knowledge_mcp.py")
LOOKUP_TOOLS = ["recipe", "machine", "rate", "steam", "supply"]

# What each caller asks, by default (the user's ChatGPT plan, not per-token prices).
STRATEGIST_MODEL = "gpt-6.1-sol"
STRATEGIST_FALLBACK = "gpt-6-luna"   # a thought the first model could not give in time (luna: 3-4 s)
CHOOSER_MODEL = "gpt-6-luna"
DESIGNER_MODEL = "gpt-6.1-sol"   # (gpt-6-astra works too; the user finds it overkill, 2026-10-23)


class CodexError(decisions_api.DecisionsAPIError):
    """A Codex run that gave no usable answer (it failed, timed out, or answered out of shape)."""


def find_codex() -> str | None:
    """The Codex CLI: NPC_CODEX, then `codex` on PATH, then the newest one the Codex app installed."""
    named = os.environ.get("NPC_CODEX")
    if named and Path(named).is_file():
        return named
    found = shutil.which("codex")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "OpenAI" / "Codex" / "bin"
    installed = sorted(base.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True) if base.is_dir() else []
    return str(installed[0]) if installed else None


def usage_of(events: str) -> dict[str, int]:
    """Token counts from `codex exec --json` events (any event carrying usage; the last one wins)."""
    out: dict[str, int] = {}
    for line in events.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        found = event.get("usage") if isinstance(event, Mapping) else None
        if isinstance(found, Mapping):
            for key, name in (("input_tokens", "input_tokens"), ("cached_input_tokens", "cached_tokens"),
                              ("output_tokens", "output_tokens")):
                if isinstance(found.get(key), int):
                    out[name] = found[key]
    return out


def last_steps(events: Any, keep: int = 3) -> str:
    """What a run that ran out of time had been doing, from the `--json` events it wrote before (a lab
    design that took the whole 180 s, one-engineer hour 2026-10-24, left nothing to tell why): how many
    turns and steps, and its last few items with their status. Empty when it wrote nothing."""
    text = events.decode("utf-8", "replace") if isinstance(events, bytes) else str(events or "")
    steps, turns = [], 0
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, Mapping):
            continue
        turns += event.get("type") == "turn.started"
        item = event.get("item") if isinstance(event.get("item"), Mapping) else None
        if item and event.get("type") in ("item.started", "item.completed"):
            if not turns and item.get("type") == "error":
                continue   # a notice before the turn, in every locked-down run ("code-mode host is disabled")
            name = item.get("tool") or item.get("type")
            steps.append(f"{name} {item.get('status') or event['type'].split('.')[1]}")
    if not steps and not turns:
        return ""
    if not steps:
        return f"{turns} turns, nothing from the model yet"
    return f"{turns} turns, {len(steps)} steps; last: " + ", ".join(steps[-keep:])


def retry_notice(line: str) -> tuple[float, str] | None:
    """Codex's notice that it waits before asking the model again (from its log): how long, and why."""
    found = RETRY_NOTICE.search(line)
    if not found:
        return None
    wait = float(found.group(1)) / (1000 if found.group(2) == "ms" else 1)
    return wait, re.sub(r"\s+", " ", found.group(3) or "the stream broke").strip()[:160]


class Stopped(Exception):
    """A run its watcher stopped early: why, and what the run wrote until then."""

    def __init__(self, reason: str, stdout: bytes = b"", stderr: bytes = b""):
        super().__init__(reason)
        self.reason, self.stdout, self.stderr = reason, stdout, stderr


def kill_tree(proc: subprocess.Popen) -> None:
    """The run and whatever it started (a lookup server, a sandbox helper), which may hold its output open."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def watched_run(command: list[str], *, input: bytes | None = None, env: Mapping[str, str] | None = None,
                cwd: str | None = None, timeout: float | None = None, creationflags: int = 0,
                watch: Callable[[str, str], str | None] | None = None, **_: Any) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output=True) for one Codex run, its output read as it comes: `watch(stream,
    line)` sees each line of "stdout" and "stderr", and a reason it returns stops the run (raised as Stopped).
    Out of time (the prompt's writing counts too): subprocess.TimeoutExpired, with what it wrote. Either way
    the run's whole process tree goes, and its output is read for at most CLEANUP_S more."""
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=cwd, creationflags=creationflags)
    kept: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    stop: list[str] = []
    over = threading.Event()   # (the answer is out: lines read after it are dropped, unwatched)

    def pump(name: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, b""):
                if over.is_set():
                    break
                kept[name].append(line)
                if watch is not None and not stop:
                    reason = watch(name, line.decode("utf-8", "replace"))
                    if reason and not stop:
                        stop.append(reason)
                        kill_tree(proc)
        except (OSError, ValueError):
            pass

    def feed() -> None:
        try:
            proc.stdin.write(input or b"")
            proc.stdin.close()
        except (OSError, ValueError):
            pass   # (it ended before reading it all: its output says why)
    helpers = [threading.Thread(target=pump, args=(name, getattr(proc, name)), daemon=True) for name in kept]
    helpers.append(threading.Thread(target=feed, daemon=True))
    for helper in helpers:
        helper.start()
    late = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        late = True
        kill_tree(proc)
        try:
            proc.wait(timeout=CLEANUP_S)
        except subprocess.TimeoutExpired:
            pass
    ends = time.monotonic() + CLEANUP_S
    for helper in helpers:
        helper.join(max(0.0, ends - time.monotonic()))
    over.set()
    for helper, stream in zip(helpers, (proc.stdout, proc.stderr, proc.stdin)):
        if not helper.is_alive():   # (one still reading is left to end with whatever holds its pipe)
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    out, err = b"".join(kept["stdout"]), b"".join(kept["stderr"])
    if late:
        raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err)
    if stop:
        raise Stopped(stop[0], out, err)
    return subprocess.CompletedProcess(command, proc.returncode, out, err)


def rate_limited(text: str) -> bool:
    """One model's rate limit (HTTP 429)."""
    low = text.lower()
    return any(word in low for word in RATE_LIMITED)


def limit_reached(text: str) -> bool:
    """The plan's usage limit, for every model; not one model's rate limit ("Rate limit reached ... try again
    in 20s"), which cools only that model down."""
    low = text.lower()
    if "usage limit" in low or "quota" in low:
        return True
    return not rate_limited(low) and any(word in low for word in LIMIT_WORDS)


class Codex:
    """Asks Codex one question at a time per call (at most MAX_RUNNING at once, shared by every caller)."""

    _slots = threading.BoundedSemaphore(MAX_RUNNING)
    _background = threading.BoundedSemaphore(BACKGROUND_RUNNING)
    _cooling: dict[str, float] = {}   # model: when its rate limit resets (time.monotonic), for every caller
    _cooling_lock = threading.Lock()

    def __init__(self, exe: str | None = None, *, run: Callable[..., Any] = watched_run,
                 timeout_s: float = TIMEOUT_S, log: Callable[[str], None] = print):
        self.exe = exe or find_codex()
        if not self.exe:
            raise CodexError("the Codex CLI was not found (install the Codex app, or set NPC_CODEX)")
        self.run, self.timeout_s, self.log = run, timeout_s, log

    @classmethod
    def cooling(cls, model: str) -> float:
        """Seconds until `model`'s rate limit resets (0 when it is not limited)."""
        with cls._cooling_lock:
            return max(0.0, cls._cooling.get(model, 0.0) - time.monotonic())

    def cool(self, model: str, wait: float, why: str) -> None:
        """`model` is rate-limited for `wait` seconds: every caller leaves it alone until then."""
        until = time.monotonic() + wait
        with self._cooling_lock:
            news = until > self._cooling.get(model, 0.0) + ANSWER_AFTER_S
            self._cooling[model] = max(until, self._cooling.get(model, 0.0))
        if news:
            self.log(f"Codex: {model} is rate limited for {wait:.0f} s ({why}); its callers use their fallback "
                     "model or go on without it meanwhile")

    def ask(self, prompt: str, schema: Mapping[str, Any], *, model: str, effort: str = "low",
            system: str = "codex", purpose: Any = None, npc: Any = None,
            knowledge: Mapping[str, Any] | None = None, lookups: int = 0,
            timeout_s: float | None = None, fallback: str | None = None,
            attempt_s: float | None = None) -> dict[str, Any]:
        """Codex's answer to `prompt`, shaped by `schema` (an object). With `knowledge` and `lookups`, the
        run may look up to that many facts in it (the designer's lookup tool). `timeout_s`: the time it has
        for an answer (the architect's, in the background, is longer). `fallback`: a model asked once,
        in the time left, when `model` fails, is rate-limited or is not done within `attempt_s`."""
        if decisions_api.out_of_credit():
            raise decisions_api.OutOfCredit(decisions_api.pause_reason())
        deadline = time.monotonic() + (self.timeout_s if timeout_s is None else timeout_s)
        tries = [model] + ([fallback] if fallback and fallback != model else [])
        failures: list[str] = []
        for n, name in enumerate(tries):
            last = n == len(tries) - 1
            ends = deadline if last or attempt_s is None else min(deadline, time.monotonic() + attempt_s)
            try:
                return self._attempt(prompt, schema, name, effort, system, purpose, npc, knowledge, lookups,
                                     ends, waits=last)
            except decisions_api.OutOfCredit:
                raise
            except CodexError as failed:
                failures.append(f"{name}: {failed}" if len(tries) > 1 else str(failed))
                if last:
                    break
                if deadline - time.monotonic() < MIN_TRY_S:
                    failures.append(f"no time left for {tries[n + 1]}")
                    break
                self.log(f"Codex {system}: {name} gave no answer ({str(failed)[:160]}); asking {tries[n + 1]}")
        raise CodexError("; ".join(failures))

    def _attempt(self, prompt: str, schema: Mapping[str, Any], model: str, effort: str, system: str,
                 purpose: Any, npc: Any, knowledge: Mapping[str, Any] | None, lookups: int, ends: float,
                 waits: bool) -> dict[str, Any]:
        """One Codex run on `model`, done by `ends` (time.monotonic), the wait for a free run included. A model
        cooling down is waited out here when `waits` (no fallback after it) and its reset leaves time to
        answer, else left alone: asked during its limit, it may only be limited for longer (to 600 s)."""
        resets = self.cooling(model)
        if resets and (not waits or resets + ANSWER_AFTER_S > ends - time.monotonic()):
            raise CodexError(f"{model} is rate limited for another {resets:.0f} s")
        if resets:
            time.sleep(resets)
        slots = self._background if system in BACKGROUND else self._slots
        if not slots.acquire(timeout=max(0.0, ends - time.monotonic())):
            raise CodexError("no free Codex run in time")
        try:
            return self._run(prompt, schema, model, effort, system, purpose, npc, knowledge, lookups, ends)
        finally:
            slots.release()

    def _run(self, prompt: str, schema: Mapping[str, Any], model: str, effort: str, system: str, purpose: Any,
             npc: Any, knowledge: Mapping[str, Any] | None, lookups: int, ends: float) -> dict[str, Any]:
        """The Codex run itself, on a free slot."""
        started = time.perf_counter()
        limit = ends - time.monotonic()
        if limit < MIN_TRY_S:
            raise CodexError("no time left after waiting for a free Codex run")
        # (a file Codex or its child still holds when the run ends must not turn an answer into an error: ST-1)
        with tempfile.TemporaryDirectory(prefix="npc-codex-", ignore_cleanup_errors=True) as folder:
            work = Path(folder)
            schema_file, answer_file = work / "schema.json", work / "answer.json"
            schema_file.write_text(json.dumps(schema), encoding="utf-8")
            # A short base instruction in place of Codex's own coding-agent one, and no web search: a third
            # of the tokens each run (14,300 to 5,250 for a small question, tested 2026-10-23).
            instructions = work / "instructions.md"
            tools = bool(knowledge) and lookups > 0
            instructions.write_text(LOOKUP_INSTRUCTIONS.format(n=lookups) if tools else BASE_INSTRUCTIONS,
                                    encoding="utf-8")
            command = [self.exe, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                       "--ignore-rules", "-s", "read-only", "-C", str(work), "-m", model,
                       "-c", f'model_reasoning_effort="{effort}"',
                       "-c", f'model_instructions_file="{instructions.as_posix()}"',
                       "-c", 'web_search="disabled"', "--color", "never", "--json"]
            if tools:
                facts = work / "knowledge.json"
                facts.write_text(json.dumps(knowledge), encoding="utf-8")
                server = [Path(sys.executable).as_posix(), KNOWLEDGE_SERVER.as_posix(), facts.as_posix(),
                          "--limit", str(lookups)]
                command += ["-c", f"mcp_servers.factorio.command={json.dumps(server[0])}",
                            "-c", f"mcp_servers.factorio.args={json.dumps(server[1:])}",
                            "-c", "mcp_servers.factorio.startup_timeout_sec=20",
                            "-c", "mcp_servers.factorio.enabled_tools=" + json.dumps(LOOKUP_TOOLS),
                            "-c", 'mcp_servers.factorio.default_tools_approval_mode="approve"']
            for feature in DISABLED:
                if not (tools and feature == "code_mode_host"):
                    command += ["--disable", feature]
            command += ["--output-schema", str(schema_file), "-o", str(answer_file), "-"]
            env = {k: v for k, v in os.environ.items() if k.upper() not in SECRET_ENV}
            env["RUST_LOG"] = RETRY_LOG

            def watch(stream: str, line: str) -> str | None:
                """A retry Codex would wait out longer than this run can afford stops the run now."""
                notice = retry_notice(line) if stream == "stderr" else None
                if not notice:
                    return None
                wait, why = notice
                limited = rate_limited(why)
                if limited:
                    self.cool(model, wait, why)
                if wait + ANSWER_AFTER_S <= ends - time.monotonic():
                    return None   # (Codex asks again in time by itself)
                return f"{model} is rate limited for {wait:.0f} s" if limited else \
                    f"{model} would be asked again only in {wait:.0f} s ({why})"
            try:
                done = self.run(command, input=prompt.encode("utf-8"), capture_output=True, env=env,
                                timeout=limit, cwd=str(work), watch=watch,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
            except Stopped as stopped:
                self._record(system, model, usage_of(stopped.stdout.decode("utf-8", "replace")), started, False,
                             purpose, npc)
                raise CodexError(stopped.reason) from None
            except subprocess.TimeoutExpired as late:
                self._record(system, model, {}, started, False, purpose, npc)
                said = last_steps(late.stdout)
                raise CodexError(f"no answer within {limit:.0f} s" + (f" ({said})" if said else "")) from None
            stdout = (done.stdout or b"").decode("utf-8", "replace")
            stderr = (done.stderr or b"").decode("utf-8", "replace")
            tokens = usage_of(stdout)
            answer = answer_file.read_text(encoding="utf-8").strip() if answer_file.exists() else ""
        if done.returncode != 0 or not answer:
            self._record(system, model, tokens, started, False, purpose, npc)
            said = _last_error(stdout, stderr)
            if limit_reached(said):
                if decisions_api.pause_ai(LIMIT_PAUSE_S, "Codex's usage limit is reached"):
                    self.log(f"Codex: {said[:200]}")
                raise decisions_api.OutOfCredit(decisions_api.pause_reason())
            if rate_limited(said):   # (Codex gave up on its own retries: no reset time is known)
                self.cool(model, RATE_COOL_S, said[:160])
            raise CodexError(f"Codex failed ({done.returncode}): {said[:300]}")
        self._record(system, model, tokens, started, True, purpose, npc)
        try:
            parsed = json.loads(answer)
        except ValueError:
            raise CodexError("Codex's answer is not JSON") from None
        if not isinstance(parsed, dict):
            raise CodexError("Codex's answer is not an object")
        return parsed

    @staticmethod
    def _record(system: str, model: str, tokens: Mapping[str, int], started: float, ok: bool,
                purpose: Any, npc: Any) -> None:
        ledger.record(system, model, api="codex", input_tokens=tokens.get("input_tokens"),
                      output_tokens=tokens.get("output_tokens"), cached_tokens=tokens.get("cached_tokens"),
                      latency_ms=(time.perf_counter() - started) * 1000, ok=ok, purpose=purpose, npc=npc)


def _last_error(stdout: str, stderr: str) -> str:
    """What Codex said went wrong: an error event's message, else the end of its stderr (no prompt text)."""
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, Mapping):
            continue
        error = event.get("error")
        message = error.get("message") if isinstance(error, Mapping) else error
        if not message and any(word in str(event.get("type", "")) for word in ("error", "failed")):
            message = event.get("message")
        if message:
            return re.sub(r"\s+", " ", str(message)).strip()
    tail = [line for line in stderr.strip().splitlines() if line.strip()][-3:]
    for line in reversed(tail):
        notice = retry_notice(line)
        if notice:
            return f"retrying in {notice[0]:.0f} s: {notice[1]}"
    said = re.sub(r"\buser\.\w+=\S+", "", " ".join(tail))   # (log fields naming the ChatGPT account)
    return re.sub(r"\s+", " ", said).strip() or "no message"


class CodexChooser:
    """System One's choices through Codex: the same request the Decisions API takes, one option picked."""

    backend = "codex"

    def __init__(self, codex: Codex, *, model: str = CHOOSER_MODEL, effort: str = "low"):
        self.codex, self.model, self.effort = codex, model, effort

    def decide(self, request: Mapping[str, Any]) -> dict[str, Any]:
        ids = [str(c["id"]) for c in request["candidates"]]
        options = "\n".join(f"- {c['id']}: {c['description']}" for c in request["candidates"])
        prompt = (f"{decisions_api.INSTRUCTIONS}\n\n{decisions_api.build_input(request)}\n\nOptions:\n{options}\n\n"
                  "Answer with the id of the option you choose.")
        schema = {"type": "object", "properties": {"choice": {"type": "string", "enum": ids}},
                  "required": ["choice"], "additionalProperties": False}
        answer = self.codex.ask(prompt, schema, model=self.model, effort=self.effort, system="system one",
                                purpose=request.get("purpose"))
        choice = answer.get("choice")
        if choice not in ids:
            raise CodexError("Codex chose an option that was not offered")
        return {"selected_id": choice, "probabilities": {cid: float(cid == choice) for cid in ids},
                "confidence": None, "backend": self.backend, "model_revision": self.model,
                "calibration_revision": "uncalibrated-factorio"}
