"""Plans that take longer than a request may wait, worked out in a thread of their own.

The game waits two game minutes for a plan, then two more for a late one, then asks again under a new request id
(designed_start.lua, REQUEST_TIMEOUT): a plan that took longer was thrown away when it came, and the next request
began the same search over (run 55, 2026-10-26: three bus plans of 7 to 8 real minutes each, every one refused as
`unknown_request`, the game held at 1x meanwhile). Here the search runs in a thread: a request waits for it at most
a little while, then goes on without it (its engineer builds something else meanwhile), and a later request for the
same thing takes the result, checked by the planner against the map as it is then.

A search is known by its owner (the engineer) and a key whose first part is the goal it plans. One search an owner
at a time: a search asked for while another of the owner's runs waits its turn.
"""

from __future__ import annotations

import contextvars
import threading
import time
from typing import Any, Callable, Hashable

WAIT_S = 20.0          # what a request waits for a search before it goes on without it
KEEP_S = 1800.0        # a result nobody took this long after it was found is dropped (its goal was given up)


class Pending(Exception):
    """The plan for `purpose` is still being worked out in the background: build something else meanwhile."""

    def __init__(self, purpose: str, seconds: float):
        super().__init__(f"the {purpose} plan is being worked out in the background ({seconds:.0f} s so far)")
        self.purpose, self.seconds = purpose, seconds


class Search:
    """One search: `fn` run in a thread, with the context it was asked in (the planner's settings, such as
    undergrounds and coal taps, are context variables)."""

    def __init__(self, owner: Hashable, key: tuple, tick: int, fn: Callable[[], Any]):
        self.owner, self.key, self.tick = owner, key, int(tick)
        self.started, self.finished = time.monotonic(), None
        self.result: Any = None
        self.error: BaseException | None = None
        context = contextvars.copy_context()

        def work() -> None:
            try:
                self.result = context.run(fn)
            except BaseException as exc:   # (raised to whoever takes the result)
                self.error = exc
            finally:
                self.finished = time.monotonic()
        self.thread = threading.Thread(target=work, name=f"search-{owner}-{key[0]}", daemon=True)

    @property
    def done(self) -> bool:
        return self.finished is not None

    @property
    def seconds(self) -> float:
        return (self.finished or time.monotonic()) - self.started

    def outcome(self) -> Any:
        """Its result; or the error it ended in, raised."""
        if self.error is not None:
            raise self.error
        return self.result


_jobs: dict[tuple, Search] = {}
_lock = threading.Lock()


def search(owner: Hashable, key: tuple, fn: Callable[[], Any], *, tick: int = 0,
           wait_s: float = WAIT_S, adopt: Callable[[Search], bool] | None = None) -> Search | None:
    """The search for `key` by `owner`, started if there is none, waited for at most `wait_s`: returned once done
    (and taken: a result is used once), else None. A search begun at a later tick than `tick` was for a game since
    loaded from an earlier save: once done it is dropped and this one starts afresh (a thread cannot be stopped: until
    then this one waits its turn)."""
    now = time.monotonic()
    with _lock:
        for k, job in list(_jobs.items()):
            if job.done and now - job.finished > KEEP_S:
                del _jobs[k]
        job = _jobs.get((owner, key))
        if job is None and adopt is not None:
            # (a plan of the same goal found for the site as it was a little before, its key since changed: taken when
            # it still holds, `adopt` says; else it waited unused until dropped and a new search began, F-21)
            old = next((k for k, j in _jobs.items() if k[0] == owner and k[1][0] == key[0] and j.done
                        and j.error is None and j.tick <= tick and adopt(j)), None)
            if old is not None:
                job = _jobs.pop(old)
                _jobs[(owner, key)] = job
        if job is not None and job.tick > tick:
            if not job.done:
                return None   # (still under way: a thread cannot be stopped, so the new one waits its turn)
            del _jobs[(owner, key)]
            job = None
        if job is None:
            if any(k[0] == owner and not j.done for k, j in _jobs.items()):
                return None   # (another of its searches is under way: this one waits its turn)
            job = _jobs[(owner, key)] = Search(owner, key, tick, fn)
            job.thread.start()
    if not job.done:
        job.thread.join(wait_s)
    if not job.done:
        return None
    with _lock:
        if _jobs.get((owner, key)) is job:
            del _jobs[(owner, key)]
    return job


def running(owner: Hashable) -> list[Search]:
    """The owner's searches under way (or done and not yet taken)."""
    with _lock:
        return [j for k, j in _jobs.items() if k[0] == owner]


def ready_goals(owner: Hashable) -> set[str]:
    """The goals whose plans were worked out for `owner` and wait to be taken: chosen first, so a plan found is not
    left until it is dropped (KEEP_S) while other goals are weighed above it."""
    return {str(j.key[0]) for j in running(owner) if j.done and j.error is None}


def pending_goals(owner: Hashable) -> set[str]:
    """The goals whose plans are being worked out for `owner` (or wait to be taken): those a request should not
    choose again until the search is done."""
    return {str(j.key[0]) for j in running(owner) if not j.done}
