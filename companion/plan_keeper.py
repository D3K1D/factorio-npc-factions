"""Keeping an engineer's factory plan, for its catalog planner (CatalogPlanner takes this in).

Each request, before a goal is chosen: is a plan due (factory_plan.due: the first once the opening is
done and power runs, then only when something moved, never sooner than the cooldown), and if so the
architect plans it (the rules while the AI is paused or when it gives nothing usable). The plan goes to
the game to keep (`/npc-plan factory`, through the bridge) and rides on the site for this request's
planners. While a goal's planner reads the map, the plan's ground for other sections is kept from it
(the map's `reserved` boxes). After the request: progress, the bus's ground and cleared zones are
brought up to date from the map the request read, and the plan's due retirements ride on the plan that
goes to the game. Nothing here may stop a plan: a failure is logged and planning goes on without it.
"""

from __future__ import annotations

import copy
import inspect
import threading
from typing import Any, Callable, Mapping

from companion import decisions_api, factory_plan
from companion import outline as outlines

# The architect is never waited for within a request (the user, 2026-10-25: it designs ahead, the engineer fills it
# in): the request goes on with the plan in hand (or the rules'), and the architect's, when it comes, is kept for the
# next request. Waited for, it took its full 120 s in run 32 and the game gave up on the request it held.
ARCHITECT_WAIT_S = 0.5   # (an instant answer is still taken; a real design never comes this fast)
OUTLINE_EVERY = 36000    # ticks between two surveys of the outline (patches found, the bus's ground moved)


def _takes(fn: Callable, *names: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return all(n in params for n in names)


def bus_zones(site: Mapping[str, Any], purpose: Any) -> list[list[float]]:
    """The ground the engineer's bus is planned on (its record's zones, world tiles), kept from every goal but the
    bus's own and those that build nothing."""
    plan = site.get("base_plan") if isinstance(site.get("base_plan"), Mapping) else None
    if purpose in factory_plan.ON_THE_BUS or purpose in factory_plan.UNRESERVED:
        return []
    return [[float(v) for v in z] for z in (plan or {}).get("zones") or []
            if isinstance(z, (list, tuple)) and len(z) == 4 and all(isinstance(v, (int, float)) for v in z)]


class PlanKeeper:
    architect = None        # architect.Architect, or None: no plans are made (the planners keep their defaults)
    factory_store = None    # set by the bridge: (npc_id, plan) -> whether the game kept it
    _world_source = None
    _factories: dict | None = None    # engineer -> the newest plan this host made (the game may not have it yet)
    _purpose_now: Any = None          # the goal being planned (whose ground is not kept from it)
    _seen_world: Mapping[str, Any] | None = None
    _thinking: dict | None = None     # engineer -> the architect's thread still at a plan
    _chunk_jobs: dict | None = None   # engineer -> the chunk the architect is designing in the background
    _chunk_last: dict | None = None   # engineer -> the tick its last chunk design came in

    # --- the map, with the plan's ground kept ----------------------------------

    @property
    def world(self):
        """The map source the bridge set, wrapped: what it returns carries the plan's ground for other
        sections as `reserved` boxes, which every planner's blocked tiles keep clear (base_plan)."""
        return self._map if self._world_source is not None else None

    @world.setter
    def world(self, source) -> None:
        self._world_source = source

    def _map(self, site: Mapping[str, Any], center: Mapping[str, float] | None = None,
             radius: int | None = None) -> Any:
        source = self._world_source
        wide = center is not None and _takes(source, "center", "radius")
        key = (site.get("request_id"), (center.get("x"), center.get("y"), radius) if wide else None)
        cache = self.__dict__.setdefault("_maps", {})
        dump = cache.get(key)   # (a request reads each map once: the architect's, then its goal's planner)
        if dump is None:
            dump = source(site, center=center, radius=radius) if wide else source(site)
            if isinstance(dump, Mapping) and dump.get("ok", True) and "entities" in dump:
                cache[key] = dump
        if not isinstance(dump, Mapping) or not dump.get("ok", True) or "entities" not in dump:
            return dump
        boxes = factory_plan.reserved_boxes(factory_plan.plan_of(site), self._purpose_now)
        zones = bus_zones(site, self._purpose_now)   # (the bus's whole planned ground, with or without a factory plan)
        if zones:   # (its ore too: the bus's own fields are planned there)
            dump = {**dump, "reserved_hard": zones}
            dump.pop("_reserved_tiles", None)
        if boxes:
            dump = {**dump, "reserved": boxes}
            dump.pop("_reserved_tiles", None)
        self._seen_world = dump
        return dump

    # --- before a goal is chosen ---------------------------------------------

    def _with_plan(self, site: Mapping[str, Any]) -> Mapping[str, Any]:
        """The site with the engineer's current plan, made or revised first when one is due."""
        self._purpose_now, self._seen_world = None, None
        try:
            current = self._known(site)
            if current is None and self.architect is None:
                return site
            reason = self._reason(site, current) if self.architect is not None else None
            chunked = bool(getattr(self.architect, "chunked", False)) and callable(
                getattr(self.architect, "design_chunk", None))
            if reason and chunked:
                current = self._outlined(site, current, reason)
            elif reason:
                current = self._replan(site, current, reason) or current
            if current is not None and chunked:
                current = self._chunk_step(site, current)
            return {**site, "factory_plan": current} if current is not None else site
        except Exception as exc:   # the plan must never stop a plan
            self.log(f"{site.get('request_id')}: factory plan failed ({str(exc)[:160]})")
            return site

    def _known(self, site: Mapping[str, Any]) -> dict | None:
        """The newer of the plan the game sent and the one this host made last (a store that did not reach
        the game). A plan made later in game time than this site is from another game: a save was loaded."""
        exported = factory_plan.plan_of(site)
        mine = self._mine().get(site.get("npc_id"))
        tick = int(site.get("tick") or 0)
        if mine is not None and int(mine.get("updated") or mine.get("tick") or 0) > tick:
            del self._mine()[site.get("npc_id")]
            mine = None
        # (a tie is the same plan come back through the game, which keeps no outline: the host's copy has it)
        if mine is not None and (exported is None or (mine["version"], int(mine.get("updated") or 0))
                                 >= (exported["version"], int(exported.get("updated") or 0))):
            return mine
        return exported

    def _mine(self) -> dict:
        if self._factories is None:
            self._factories = {}
        return self._factories

    def _reason(self, site: Mapping[str, Any], current: Mapping[str, Any] | None) -> str | None:
        if site.get("purpose") == "factory-plan":
            asked = int((current or {}).get("asked") or 0)
            if current is None or int(site.get("tick") or 0) - asked >= factory_plan.ASKED_COOLDOWN:
                return "the strategist asked"
            self.log(f"{site.get('request_id')}: the strategist asked to plan the factory again; plan "
                     f"v{current['version']} is only {(int(site.get('tick') or 0) - asked) // 3600} minutes old, kept")
            return None
        return factory_plan.due(site, current, opening_over=self._opening_over(site),
                                ai_ready=not decisions_api.out_of_credit())

    def _opening_over(self, site: Mapping[str, Any]) -> bool:
        """Power runs and the small builds of the opening are behind it: the next step is the main bus (or
        nothing of the opening is left, or its bus has begun)."""
        from companion import catalog_planner as cp
        if not cp.has_power(site):
            return False
        if (site.get("base_plan") or {}).get("stages"):
            return True
        return cp.opening_goal(site, designer=getattr(self, "designer", None) is not None) in (None, "bus-base")

    def _replan(self, site: Mapping[str, Any], current: dict | None, reason: str) -> dict | None:
        rid = site.get("request_id")
        tick = int(site.get("tick") or 0)
        world = self.world(site) if self.world else None
        if not isinstance(world, Mapping) or not world.get("ok", True) or "entities" not in world:
            self.log(f"{rid}: a factory plan is due ({reason}) but there is no map of the surroundings yet")
            return None
        paused = decisions_api.out_of_credit()
        made, how = None, ""
        if not paused:
            try:
                made, how = self._ask_architect(site, world, current, reason)
            except Exception as exc:
                self.log(f"{rid}: architect failed ({str(exc)[:200]})")
        if made is None:
            if current is not None and current.get("source") != "rules":
                current["asked"] = tick   # (the architect's plan stays; it is asked again after the cooldown)
                self._keep(site, current)
                return current
            paused = paused or decisions_api.out_of_credit()   # (it may have paused during the call)
            made = factory_plan.default_plan(site, why=reason, paused=paused)
            how = "the AI is paused" if paused else "no plan from the architect"
        plan = factory_plan.revise(current, made, site)
        plan["asked"] = plan["updated"] = tick
        plan["why"] = reason[:160]
        self.log(f"{rid}: factory plan v{plan['version']} by the {plan.get('source')} ({reason}; {how}): "
                 f"{len(factory_plan.live(plan))} sections, {len(plan.get('zones') or [])} zones"
                 + (f"; {'; '.join(plan['changes'][:4])}" if plan.get("changes") else ""))
        self._keep(site, plan)
        return plan

    # --- the base designed chunk by chunk (an architect with design_chunk) -------

    def _outlined(self, site: Mapping[str, Any], current: dict | None, reason: str) -> dict:
        """A plan is due. The first is the rules' plan at once, outlined and then designed by the architect a
        chunk at a time in the background (the user, 2026-10-25: "plan the base chunk by chunk so it has time to
        design instead of all at once"); a later one designs again what moved, chunk by chunk, and nothing waits."""
        rid, tick = site.get("request_id"), int(site.get("tick") or 0)
        if current is None:
            plan = factory_plan.revise(None, factory_plan.default_plan(site, why=reason), site)
            plan["asked"] = plan["updated"] = tick
            plan["why"] = reason[:160]
            self.log(f"{rid}: factory plan v{plan['version']} by the rules ({reason}): {len(factory_plan.live(plan))} "
                     "sections; the architect designs it chunk by chunk from here")
        else:
            plan = current
            again = outlines.invalidate(plan.get("outline") or {})
            plan["asked"] = tick
            plan["basis"] = factory_plan.basis(site)
            plan["why"] = reason[:160]
            self.log(f"{rid}: the factory plan is designed again chunk by chunk ({reason}; {again} chunks)")
        self._keep(site, plan)
        return plan

    def _chunk_step(self, site: Mapping[str, Any], plan: dict) -> dict:
        """Each request, without waiting: a chunk design that came in is merged into the plan (only that chunk;
        one from another game, a save loaded since, or for inputs that changed meanwhile is dropped), the outline is
        made or surveyed again when due, and the next chunk goes to the architect in the background, one at a time."""
        rid, npc, tick = site.get("request_id"), site.get("npc_id"), int(site.get("tick") or 0)
        jobs = self.__dict__.setdefault("_chunk_jobs", None) or {}
        self._chunk_jobs = jobs
        last = self.__dict__.setdefault("_chunk_last", None) or {}
        self._chunk_last = last
        changed = False
        job = jobs.get(npc)
        if job is not None and not job["thread"].is_alive():
            del jobs[npc]
            last[npc] = tick
            out = plan.get("outline") or {}
            chunk = next((c for c in out.get("chunks") or [] if c.get("id") == job["chunk"]), None)
            if job["tick"] > tick:
                self.log(f"{rid}: the design of chunk {job['chunk']} is from another game (a save was loaded); dropped")
            elif "error" in job or job.get("design") is None:
                if chunk is not None:
                    outlines.failed(out, chunk["id"], tick)
                    changed = True
                self.log(f"{rid}: the architect's design of chunk {job['chunk']} failed "
                         f"({str(job.get('error') or 'no design')[:160]}); tried again later")
            elif chunk is None or chunk.get("input_key") != job["key"]:
                self.log(f"{rid}: the design of chunk {job['chunk']} came for inputs since changed; dropped")
            else:
                try:
                    plan, changes = factory_plan.revise_chunk(plan, chunk, job["design"], site)
                    outlines.designed(plan["outline"], chunk["id"], tick, job.get("notes") or ())
                    left = len(outlines.pending(plan["outline"]))
                    self.log(f"{rid}: factory plan v{plan['version']}: chunk {chunk['id']} designed by the architect "
                             f"({'; '.join(changes[:3]) or 'nothing changed'}); {left} chunks to go")
                    changed = True
                except Exception as exc:   # (a design, never in the way)
                    outlines.failed(out, chunk["id"], tick)
                    self.log(f"{rid}: merging chunk {chunk['id']} failed ({str(exc)[:160]})")
        out = plan.get("outline")
        world = None
        if out is None or tick - int(out.get("tick") or 0) >= OUTLINE_EVERY or (npc not in jobs and outlines.next_chunk(
                out, tick, last.get(npc)) is not None):
            world = self.world(site) if self.world else None
            if not isinstance(world, Mapping) or not world.get("ok", True) or "entities" not in world:
                world = None
        if world is not None and (out is None or tick - int(out.get("tick") or 0) >= OUTLINE_EVERY):
            made = outlines.make(site, world, plan)
            made["tick"] = tick
            if out is None:
                self.log(f"{rid}: the base outlined: {len(made['chunks'])} chunks (the bus, power, "
                         f"{sum(1 for c in made['chunks'] if c['kind'] == 'resource')} ore patches, the rest)")
            plan["outline"] = out = made
            changed = True
        chunk = outlines.next_chunk(out, tick, last.get(npc)) if out is not None and npc not in jobs else None
        if chunk is not None and world is not None and not decisions_api.out_of_credit():
            job = {"chunk": chunk["id"], "key": chunk.get("input_key"), "tick": tick}
            snapshot = copy.deepcopy(plan)

            def design() -> None:
                try:
                    job["design"], job["notes"] = self.architect.design_chunk(site, world, snapshot, chunk)
                except Exception as exc:   # (reported when it is collected)
                    job["error"] = exc
            job["thread"] = threading.Thread(target=design, name=f"architect-chunk-{npc}", daemon=True)
            jobs[npc] = job
            job["thread"].start()
            self.log(f"{rid}: the architect designs chunk {chunk['id']} in the background")
        if changed:
            self._keep(site, plan)
        return plan

    def _ask_architect(self, site: Mapping[str, Any], world: Mapping[str, Any], current: dict | None,
                       reason: str) -> tuple[Any, str]:
        """The architect's plan, waited for at most ARCHITECT_WAIT_S. Past that this request goes on without it
        (the plan in hand, or the rules'), and the architect's plan, when it comes, is revised onto the newest
        plan kept for the engineer and kept for its next request. One architect at a time per engineer."""
        if self._thinking is None:
            self._thinking = {}
        npc = site.get("npc_id")
        busy = self._thinking.get(npc)
        if busy is not None and busy.is_alive():
            return None, "the architect is still at an earlier plan"
        box: dict = {}

        def think() -> None:
            try:
                box["made"] = self.architect(site, world, current, reason, chooser=getattr(self, "chooser", None))
            except Exception as exc:   # (reported by whoever waits)
                box["error"] = exc
        thread = threading.Thread(target=think, name=f"architect-{npc}", daemon=True)
        self._thinking[npc] = thread
        thread.start()
        thread.join(ARCHITECT_WAIT_S)
        if not thread.is_alive():
            if "error" in box:
                raise box["error"]
            return box.get("made") or (None, "")

        def later() -> None:
            thread.join()
            made, how = box.get("made") or (None, "")
            rid = site.get("request_id")
            if "error" in box or made is None:
                self.log(f"{rid}: architect failed later ({str(box.get('error') or how or 'no plan')[:160]})")
                return
            try:
                base = self._mine().get(npc) or current
                plan = factory_plan.revise(base, made, site)
                plan["asked"] = int(site.get("tick") or 0)
                plan["updated"] = int((base or {}).get("updated") or plan["asked"]) + 1
                plan["why"] = reason[:160]
                self.log(f"{rid}: factory plan v{plan['version']} by the {plan.get('source')} came later ({reason}; "
                         f"{how}): {len(factory_plan.live(plan))} sections, {len(plan.get('zones') or [])} zones")
                self._keep(site, plan)
            except Exception as exc:   # (a plan, never in the way)
                self.log(f"{rid}: keeping the architect's later plan failed ({str(exc)[:160]})")
        threading.Thread(target=later, name=f"architect-later-{npc}", daemon=True).start()
        return None, f"the architect is still thinking after {ARCHITECT_WAIT_S} s: its plan comes later"

    def _keep(self, site: Mapping[str, Any], plan: dict) -> None:
        """This host's copy, and the game's (it keeps it in the save)."""
        self._mine()[site.get("npc_id")] = plan
        store = self.factory_store
        if store is None:
            return
        try:
            kept = store(site.get("npc_id"), factory_plan.bounded(plan))
        except Exception as exc:
            kept = False
            self.log(f"{site.get('request_id')}: storing the factory plan failed ({str(exc)[:120]})")
        if not kept:
            self.log(f"{site.get('request_id')}: the game did not keep factory plan v{plan.get('version')}")

    # --- choosing what to build ------------------------------------------------

    def planned_goal(self, site: Mapping[str, Any]) -> str | None:
        """The goal that builds the plan's most important section not built yet, when one can be built now."""
        from companion import catalog_planner as cp
        designer = getattr(self, "designer", None) is not None
        found = factory_plan.next_purpose(site, lambda p: p in cp.PURPOSES and cp.available(p, site, designer=designer))
        if not found:
            return None
        purpose, s = found
        self.log(f"{site.get('request_id')}: next goal {purpose} (the factory plan: {s['id']}, priority "
                 f"{factory_plan.effective_priority(s, site):.2f})")
        return purpose

    # --- after the request -------------------------------------------------------

    def _upkeep(self, site: Mapping[str, Any], made: dict | None) -> None:
        """Progress, the bus's ground and cleared zones from what this request saw; retirements due ride on
        the plan going to the game. Stored when something changed."""
        world, self._seen_world = self._seen_world, None
        self.__dict__.pop("_maps", None)   # (the maps this request read: the next one reads its own)
        try:
            plan = factory_plan.plan_of(site)
            if plan is None:
                return
            changed = factory_plan.refresh(site, plan, world)
            if made is not None:
                names, moved = factory_plan.retirements(site, plan)
                changed |= moved
                if names:
                    made["retire"] = list(dict.fromkeys([*(made.get("retire") or []), *names]))[:32]
                    self.log(f"{site.get('request_id')}: the factory plan retires {', '.join(names[:6])}")
            if changed:
                plan["updated"] = int(site.get("tick") or 0)
                self._keep(site, plan)
        except Exception as exc:
            self.log(f"{site.get('request_id')}: factory plan upkeep failed ({str(exc)[:160]})")
