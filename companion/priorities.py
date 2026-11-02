"""Priorities on the factory ledger: how much each item, and each section of the factory, matters to an
engineer now.

Not everything in a factory has to run all the time. A player short of iron for steel, with no plans for
steel soon, lets that wait until steel matters; one fighting biters lets science stall while ammunition
comes first. So every item and every ledger segment gets a weight from 0 to 1, weighed from the engineer's
whole situation, and the ledger's shortfalls are acted on only when they matter:

- **Signals.** Each is a function of the situation (a Context) giving pulls: an item or a section raised
  with a reason, or for a while everything else lowered. They are combined, so no single one decides,
  and a new one plugs in with @signal. The examples the user gave are tests they pass, not the cases.
- **Recipe chains.** What a wanted item is made from is wanted too, a little less at each step.
- **Sections.** A segment matters as much as what it makes, or more when a pull names its kind or itself.
- **Hysteresis.** Things take time in Factorio: a level holds until the signals have said otherwise for a
  while (longer for patient engineers); something urgent changes it at once.
- **The strategist's settings** (its prioritize tool) go on top. The mod keeps them with the engineer and
  sends them with every site (site["priorities"]), so they survive runner restarts and travel with saves;
  an urgent need still overrides a lowering. While the AI is paused the signals alone are the defaults.

The engineers' purpose is speed, space as early as possible, so the path to space is always a baseline.
In the team world (Biter World) survival weighs heavily.

    prio = priorities.assess(site)                    # this engineer now (with a strategist request: fresher)
    prio.of("iron-plate").level, prio.of("iron-plate").why()
    prio.section(segment)                             # a ledger segment (or a kind, a stage) and its priority
    prio.weigh(kind="mall", makes=["transport-belt"]) # a section not built yet
    priorities.shortfalls(site)                       # what is short, each weighed: acted on or held
    priorities.fits(site, {"iron-plate": 20}, load=0.15, kind="mall")   # a new section, by its priority
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping

from companion import ledger, ratios, team_area
from companion.tier_path import _num

MINUTE = 3600                 # game ticks
LEVELS = (("top", 0.85), ("high", 0.6), ("normal", 0.35), ("low", 0.15), ("ignore", 0.0))
LEVEL_NAMES = tuple(name for name, _ in LEVELS)
SET_WEIGHT = {"top": 0.95, "high": 0.72, "normal": 0.47, "low": 0.25, "ignore": 0.05}   # the strategist's levels
ACT = 0.35                    # a shortfall this important (normal) or more is acted on; below it is held
FLOOR = 0.1                   # nothing in the factory counts for nothing unless the strategist says so
CHAIN = 0.85                  # what a wanted item is made from is wanted this much less, at each step
DEPTH = 10                    # recipe steps followed at most
HOLD_MINUTES = 10             # a lower level is taken once the signals have said so this long (times patience)
RISE_MINUTES = 2              # a higher one sooner; at once when urgent
MARGIN = 0.04                 # a weight must cross a level's line by this much before it counts as moved
CALM = 0.1                    # fight heat under this: in the clear
DAMP = 0.7                    # in the thick of a fight, what is not for the fight counts this much less
TEAM_SPACE = 0.75             # the team world: the far part of the path to space counts this much
FIGHT_EVENTS = ("attacked", "home_attacked", "breach", "helping", "help_coming", "war")
# Sections are the ledger's kinds (and any segment by its stage or purpose).
SECTIONS = tuple(dict.fromkeys(k for k, _ in ledger._KINDS if k not in ledger.OUTSIDE)) + ("intermediate",)
# The science on the way to the rocket, when the game does not say (an older mod): its techs share the
# packs' names, and red needs none.
SPACE_PACKS = ("automation-science-pack", "logistic-science-pack", "chemical-science-pack",
               "production-science-pack", "utility-science-pack")
INTENT_WEIGHT = {"now": 0.8, "next": 0.6, "later": 0.42}   # an agenda entry's priority, as a weight


def level_of(weight: float) -> str:
    for name, line in LEVELS:
        if weight >= line:
            return name
    return LEVELS[-1][0]


def _index(weight: float) -> int:
    return next((i for i, (_, line) in enumerate(LEVELS) if weight >= line), len(LEVELS) - 1)


def _band(index: int) -> tuple[float, float]:
    """The weights of a level: from its line up to the next one's."""
    return LEVELS[index][1], (LEVELS[index - 1][1] if index > 0 else 1.0 + MARGIN)


def _either(weights: Iterable[float]) -> float:
    """Signals combined: each makes it more likely to matter (a noisy OR), none alone decides."""
    left = 1.0
    for w in weights:
        left *= 1.0 - min(1.0, max(0.0, w))
    return 1.0 - left


def _list(value: Any) -> list:
    if isinstance(value, Mapping):
        return list(value.values())
    return list(value) if isinstance(value, (list, tuple)) else []


def _map(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _npc_of(obj: Mapping[str, Any]) -> int | None:
    """Whose site or request this is: its npc_id, else the head of its request id ("3:r12")."""
    npc = obj.get("npc_id")
    if isinstance(npc, int):
        return npc
    head = str(obj.get("request_id", "")).split(":", 1)[0]
    return int(head) if head.isdigit() else None


@dataclass(frozen=True)
class Pull:
    """One signal's say on one target: an item, a section (a ledger kind such as "science", or a segment's
    stage or purpose), or with damp=True a lowering of `target` ("*": everything but `spare`)."""
    target: str
    weight: float
    why: str
    urgent: bool = False
    damp: bool = False
    spare: frozenset = frozenset()
    section: bool = False   # a section by name though none is built yet (a goal, a bus stage)


@dataclass
class Priority:
    weight: float = FLOOR
    reasons: tuple[str, ...] = ()
    urgent: bool = False      # something urgent wants it: it rises at once and a strategist's lowering yields
    direct: float = 0.0       # what the signals pull on it themselves, not through what it goes into
    held: bool = False        # kept at its level for now (hysteresis), though the signals say otherwise
    set: str | None = None    # the strategist's own setting when that decides ("high until calm")
    sudden: bool = False      # changed by something urgent: no waiting either way
    via: str | None = None    # wanted for what it goes into (the reasons are then that item's)

    @property
    def level(self) -> str:
        return level_of(self.weight)

    @property
    def acted(self) -> bool:
        return self.weight >= ACT

    def why(self, n: int = 2) -> str:
        return "; ".join(([f"for {self.via}"] if self.via else []) + list(dict.fromkeys(self.reasons))[:n])


@dataclass
class Setting:
    """One of the strategist's settings, as the game sent it (site["priorities"] or the situation's)."""
    name: str
    level: str
    until: str | None = None
    left: float | None = None      # minutes until it runs out
    ago: float | None = None       # minutes since it was set
    reason: str | None = None

    @property
    def weight(self) -> float:
        return SET_WEIGHT.get(self.level, SET_WEIGHT["normal"])

    def label(self) -> str:
        return f"you set it {self.level}" + (f" until {self.until}" if self.until else "")


class Context:
    """What the signals read: the planner's latest site for the engineer (its base, plans, research and
    threats, site["focus"]) and, when a strategist request is in hand, its fresher situation and events."""

    def __init__(self, site: Mapping[str, Any] | None, request: Mapping[str, Any] | None = None,
                 book: ledger.Ledger | None = None):
        self.site = _map(site)
        self.request = _map(request)
        self.situation = _map(self.request.get("situation"))
        self.events = [e for e in _list(self.request.get("events")) if isinstance(e, Mapping)]
        self.focus = _map(self.site.get("focus"))
        self.book = book if book is not None else ledger.Ledger.from_site(self.site)
        tick = self.site.get("tick")
        self.tick = int(tick) if isinstance(tick, (int, float)) else None
        self.team = bool(team_area.team_of(self.site)) or bool(self.situation.get("team"))
        self.npc = _npc_of(self.site) if _npc_of(self.site) is not None else _npc_of(self.request)
        self._recipes: Any = False
        self._facts: Any = False

    # --- the engineer --------------------------------------------------------

    def axes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for source in (_map(self.site.get("personality")).get("axes"), self.focus.get("axes"),
                       self.situation.get("axes")):
            for k, v in _map(source).items():
                if isinstance(v, (int, float)):
                    out[str(k)] = float(v)
        return out

    def axis(self, name: str) -> float:
        return self.axes().get(name, 0.5)

    def traits(self) -> set[str]:
        return {str(t) for t in _list(_map(self.site.get("personality")).get("traits"))
                + _list(self.situation.get("traits"))}

    def researched(self) -> set[str]:
        return {str(t) for t in _list(self.site.get("researched"))}

    def built(self) -> set[str]:
        return {str(p) for p in _list(self.site.get("completed_purposes")) + _list(self.situation.get("built"))}

    @property
    def recipes(self) -> ratios.Book | None:
        if self._recipes is False:
            try:
                self._recipes = ratios.Book.from_site(self.site)
            except Exception:   # (a malformed export: no recipe chains, the rest still weighs)
                self._recipes = None
        return self._recipes

    @property
    def facts(self):
        if self._facts is False:
            from companion import rates
            try:
                self._facts = rates.of(self.site)
            except Exception:
                self._facts = None
        return self._facts

    # --- what it works toward ------------------------------------------------

    def research(self) -> list[tuple[str, tuple[str, ...]]] | None:
        """The research under way and queued, each with the packs it needs; [] when the labs have nothing
        to do, None when the game did not say."""
        rows = self.focus.get("research")
        known = rows is not None
        out = []
        for r in _list(rows):
            if isinstance(r, Mapping) and r.get("name"):
                out.append((str(r["name"]), tuple(str(p) for p in _list(r.get("packs")))))
        # The strategist's situation is fresher (names only; the packs from the site when it knows them):
        # the team's queue in the team world, else its own research.
        team = _map(self.situation.get("team_research"))
        if team or "research" in self.situation:
            fresh = ([team.get("current")] + [_map(q).get("name") for q in _list(team.get("queue"))] if team
                     else [_map(self.situation.get("research")).get("current")])
            fresh = list(dict.fromkeys(str(n) for n in fresh if isinstance(n, str) and n))
            packs = dict(out)
            queue = [] if team else [r for r in out[1:] if r[0] not in fresh]   # (its own: only the current one)
            out = [(n, packs.get(n, ())) for n in fresh] + queue
            known = True
        return out if known else None

    def goals(self) -> list[tuple[str, float, str]]:
        """Purposes it means to build, with how much each matters: the plan it is asking for, the one under
        way, goals on its agenda, the next step of its opening."""
        out = []
        purpose = self.site.get("purpose")
        if isinstance(purpose, str) and purpose not in ("choose", "module"):
            out.append((purpose, 0.55, f"building {purpose}"))
        doing = _map(self.situation.get("doing")).get("plan")
        if isinstance(doing, str):
            out.append((doing, 0.5, f"building {doing}"))
        for a in self.asks():
            if a.get("tool") == "build" and isinstance(a.get("goal"), str):
                out.append((a["goal"], INTENT_WEIGHT.get(str(a.get("priority")), 0.5), f"means to build {a['goal']}"))
        try:
            from companion import catalog_planner
            nxt = catalog_planner.opening_goal(self.site) if self.site.get("base") is not None else None
        except Exception:
            nxt = None
        if nxt:
            out.append((nxt, 0.4, f"its opening builds {nxt} next"))
        return out

    def asks(self) -> list[Mapping[str, Any]]:
        """What it has said it will do (its agenda: often a teammate's or a player's ask), the game's
        export first, then what the fresher situation adds."""
        out = [a for a in _list(self.focus.get("asks")) if isinstance(a, Mapping)]
        seen = {(a.get("tool"), a.get("goal"), a.get("reason")) for a in out}
        for a in _list(self.situation.get("agenda")):
            if isinstance(a, Mapping) and (a.get("tool"), a.get("goal"), a.get("reason")) not in seen:
                items = {a["item"]: 1} if isinstance(a.get("item"), str) else None
                out.append({**a, "items": items})
        return out

    # --- danger --------------------------------------------------------------

    def last_attack(self) -> tuple[float, float] | None:
        """(seconds since its base was last hit, how many came), from the freshest source there is."""
        best: tuple[float, float] | None = None

        def seen(seconds: float, units: float) -> None:
            nonlocal best
            if best is None or seconds < best[0]:
                best = (max(0.0, seconds), units)
        if any(e.get("kind") in ("attacked", "home_attacked") for e in self.events):
            seen(0, 10)
        last = _map(_map(self.situation.get("threat")).get("last_attack"))
        if "seconds_ago" in last:
            seen(_num(last.get("seconds_ago")), _num(last.get("units"), 5))
        for a in _list(_map(self.site.get("threat_board")).get("attacks")):
            if isinstance(a, Mapping) and self.tick is not None and isinstance(a.get("tick"), (int, float)):
                units = sum(_num(v) for v in _map(a.get("units")).values()) or 5
                seen((self.tick - a["tick"]) / 60, units)
        recent = _num(_map(self.site.get("social")).get("recent_attacks"))
        if recent > 0:
            seen(300, 5 * recent)   # (within the last 10 minutes, no time given)
        return best

    def breaches(self) -> int:
        """Breaches in its wall line not mended yet."""
        open_ = sum(1 for b in _list(self.site.get("breaches"))
                    if isinstance(b, Mapping) and _num(b.get("layers")) < 4)
        threat = _map(self.situation.get("threat"))
        newest = _map(threat.get("newest_breach"))
        if newest and _num(newest.get("seconds_ago"), 1e9) < 600:
            open_ = max(open_, 1)
        return max(open_, sum(1 for e in self.events if e.get("kind") == "breach"))

    def evolution(self) -> float:
        for source in (_map(self.situation.get("threat")), _map(self.site.get("threat_board")),
                       _map(self.site.get("defense")), _map(self.focus.get("arms"))):
            if isinstance(source.get("evolution"), (int, float)):
                return max(0.0, min(1.0, float(source["evolution"])))
        return 0.0

    def heat(self) -> tuple[float, str, bool]:
        """How hot things are, 0 (calm) to 1 (in the thick of a fight), why, and whether it is urgent."""
        parts, why, urgent = [], [], False
        last = self.last_attack()
        if last is not None:
            seconds, units = last
            parts.append(math.exp(-seconds / 600) * min(1.0, 0.6 + units / 25))   # (a fight is a fight)
            why.append("attacked just now" if seconds < 60 else f"attacked {seconds / 60:.0f} minutes ago")
            urgent = seconds < 180
        breaches = self.breaches()
        if breaches:
            parts.append(min(1.0, 0.6 + 0.2 * breaches))
            why.append(f"{breaches} breach{'es' if breaches > 1 else ''} in its wall")
            urgent = True
        fights = [e.get("kind") for e in self.events if e.get("kind") in FIGHT_EVENTS]
        if fights:
            parts.append(0.9)
            why.append(str(fights[0]).replace("_", " "))
            urgent = True
        if self.situation.get("foe") or self.focus.get("foe"):
            parts.append(0.45)
            why.append(f"at war with {self.situation.get('foe') or self.focus.get('foe')}")
        return _either(parts), ", ".join(why), urgent

    def arms(self) -> dict[str, list[str]]:
        """What it can fight with, best first, from the game's armory (mods included): ammunition,
        turrets, walls and gates, repair packs."""
        d = _map(self.site.get("defense")) or _map(self.focus.get("arms"))
        ammo: list[str] = []
        for _, names in sorted(_map(d.get("ammo")).items()):
            ammo += [str(n) for n in _list(names)[:2]]
        def names(key: str) -> list[str]:
            return [str(r.get("name")) for r in _list(d.get(key)) if isinstance(r, Mapping) and r.get("name")]
        return {"ammo": ammo, "turrets": names("turrets"), "walls": names("walls") + names("gates")[:1],
                "repair": names("repair")}


# --- signals ---------------------------------------------------------------------------------------------

SIGNALS: dict[str, Callable[[Context], Iterable[Pull]]] = {}


def signal(name: str):
    """Register a signal: a function of a Context that yields Pulls. New ones plug in the same way."""
    def wrap(fn):
        SIGNALS[name] = fn
        return fn
    return wrap


def _space_progress(ctx: Context) -> float:
    space = _map(ctx.focus.get("space"))
    total, left = _num(space.get("total")), _num(space.get("left"))
    if total > 0:
        return max(0.0, min(1.0, 1 - left / total))
    done = ctx.researched()
    return sum(1 for p in SPACE_PACKS[1:] if p in done) / len(SPACE_PACKS)


@signal("space")
def path_to_space(ctx: Context) -> Iterable[Pull]:
    """The engineers' purpose is speed: space as early as possible, much faster than players. The science
    the rest of the way to the rocket needs (the game's own tech tree to it, mods included) always counts,
    the next step most; the rocket's own parts a little from the start, more as it nears. Science and labs
    are the way there. In the team world survival comes first, so the far part counts less."""
    far = TEAM_SPACE if ctx.team else 1.0
    space = _map(ctx.focus.get("space"))
    nxt = _map(space.get("next"))
    if space:
        techs = ", ".join(str(t) for t in _list(nxt.get("techs"))[:2]) or "the rocket"
        for pack in _list(nxt.get("packs")):
            yield Pull(str(pack), 0.55, f"the next step to space ({techs})")
        packs = {str(k): _num(v) for k, v in _map(space.get("packs")).items()}
        total = sum(packs.values()) or 1.0
        for pack, units in packs.items():
            yield Pull(pack, (0.2 + 0.25 * units / total) * far, f"the way to space needs {units:.0f} units")
    else:
        done = ctx.researched()
        nxt_pack = next((p for p in SPACE_PACKS[1:] if p not in done), None)
        for i, pack in enumerate(SPACE_PACKS):
            if pack == nxt_pack:
                yield Pull(pack, 0.5, "the next step to space")
            elif i == 0 or pack in done:
                yield Pull(pack, 0.45, "science on the way to space")
            else:
                yield Pull(pack, 0.2 * far, "science on the way to space, later")
    near = _space_progress(ctx)
    yield Pull("rocket-part", (0.15 + 0.4 * near) * far, "the rocket")
    yield Pull("rocket", (0.2 + 0.5 * near) * far, "the rocket")
    yield Pull("science", 0.45, "science is the way to space")
    yield Pull("labs", 0.45, "science is the way to space")


@signal("research")
def research(ctx: Context) -> Iterable[Pull]:
    """The research under way needs its packs now, the queue's soon."""
    for i, (name, packs) in enumerate(ctx.research() or []):
        w = 0.6 if i == 0 else 0.45 * CHAIN ** (i - 1)
        for pack in packs:
            yield Pull(pack, w, f"research {name} needs it")


@signal("idle_labs")
def idle_labs(ctx: Context) -> Iterable[Pull]:
    """Labs with nothing to research: packs pile up, so science can wait a little (the next pick comes)."""
    if ctx.research() == []:
        for kind in ("science", "labs"):
            yield Pull(kind, 0.3, "nothing is being researched", damp=True)


@signal("goals")
def goals(ctx: Context) -> Iterable[Pull]:
    """What it means to build wants what that makes and what it is built from, and builds of that purpose:
    the plan it is asking for or building, goals on its agenda, its opening's next step, and its bus's
    science and the stages it has left."""
    from companion.base_plan import EXTENSIONS
    from companion.catalog_planner import PURPOSES
    for purpose, w, why in ctx.goals():
        for item in PURPOSES.get(purpose, {}).get("requires_recipes", ()):
            yield Pull(str(item), w, why)
        yield Pull(purpose, w * 0.9, why, section=True)   # (its builds by name: not every build of its kind)
    saved = _map(ctx.site.get("base_plan"))
    # (its extensions that are items: not growth, expansions, outposts or malls, which name sections)
    for pack in _list(saved.get("packs")) + [g for g in _list(saved.get("extra")) if g in EXTENSIONS]:
        yield Pull(str(pack), 0.45, "its bus is built for it")
    done = _map(saved.get("done"))
    for stage in [s for s in _list(saved.get("stages")) if s not in done][:3]:
        yield Pull(str(stage), 0.35, f"its bus has {stage} to build", section=True)


@signal("fight")
def fight(ctx: Context) -> Iterable[Pull]:
    """Its base hit, a breach in its wall, a fight it is in or a war: ammunition, turrets, walls and
    repairs at once and for a while after, and what is not for the fight can wait (science starves for a
    bit while ammunition comes first). It cools over about ten minutes."""
    heat, why, urgent = ctx.heat()
    if heat < 0.02:
        return
    arms = ctx.arms()
    raised = {"ammo", "defense", "power"}
    for key, base, scale in (("ammo", 0.3, 0.65), ("turrets", 0.25, 0.6), ("walls", 0.2, 0.6), ("repair", 0.2, 0.55)):
        for i, name in enumerate(arms[key][:4]):
            raised.add(name)
            yield Pull(name, (base + scale * heat) * (1.0 if i == 0 else 0.85), why, urgent=urgent)
    yield Pull("ammo", 0.3 + 0.65 * heat, why, urgent=urgent)
    yield Pull("defense", 0.25 + 0.6 * heat, why, urgent=urgent)
    yield Pull("*", DAMP * heat, f"{why}: the rest can wait", urgent=urgent, damp=True, spare=frozenset(raised))


@signal("survival")
def survival(ctx: Context) -> Iterable[Pull]:
    """A death world (the team world): the team holds its wall line first, so ammunition, turrets and walls
    always matter, the more the biters have evolved; military science is the team's own research."""
    if not ctx.team:
        return
    evo = ctx.evolution()
    arms = ctx.arms()
    for key, base in (("ammo", 0.45), ("turrets", 0.4), ("walls", 0.35), ("repair", 0.3)):
        for i, name in enumerate(arms[key][:3]):
            yield Pull(name, (base + 0.3 * evo) * (1.0 if i == 0 else 0.85), "survival comes first on a death world")
    yield Pull("ammo", 0.45 + 0.3 * evo, "survival comes first on a death world")
    yield Pull("defense", 0.4 + 0.3 * evo, "survival comes first on a death world")
    yield Pull("military-science-pack", 0.45, "the team arms first")


@signal("asks")
def asks(ctx: Context) -> Iterable[Pull]:
    """What it has said it will do, often for a teammate or a player: stock up, bring or hand over items,
    arm up, guard or fight; and an ally's call for help."""
    arms = ctx.arms()
    for a in ctx.asks():
        tool, prio = str(a.get("tool")), str(a.get("priority") or "next")
        w, urgent = INTENT_WEIGHT.get(prio, 0.5), prio == "now"
        why = f"it means to {tool.replace('_', ' ')}" + (f" ({a['reason']})" if a.get("reason") else "")
        for item in _map(a.get("items")):
            yield Pull(str(item), w, why, urgent=urgent)
        if tool in ("arm_up", "attack", "guard"):
            for name in arms["ammo"][:1] + (arms["turrets"][:1] if tool != "arm_up" else []):
                yield Pull(name, w, why, urgent=urgent)
            yield Pull("ammo", w * 0.9, why)
    help_ = _map(ctx.site.get("help"))
    if help_:
        for name in arms["turrets"][:1] + arms["ammo"][:1]:
            yield Pull(name, 0.55, "an ally asked for help")


@signal("teammates")
def teammates(ctx: Context) -> Iterable[Pull]:
    """On one team, what a teammate is short of and this engineer makes is worth keeping up: teammates take
    from each other's chests. Cooperative engineers weigh it more."""
    if not ctx.team or ctx.npc is None:
        return
    mine = {i for s in ctx.book.segments for i in s.makes}
    lean = 0.6 + 0.8 * ctx.axis("cooperation")
    for name, items in _team_short(ctx.npc):
        for item in items & mine:
            yield Pull(item, 0.3 * lean, f"{name} is short of it")


@signal("busy")
def busy(ctx: Context) -> Iterable[Pull]:
    """What the factory is busy with: lines measurably at work are worth keeping fed."""
    for s in ctx.book.segments:
        if s.mature and s.share("working") >= 0.5:
            for item in s.makes:
                yield Pull(item, 0.08 + 0.12 * s.share("working"), f"{s.name} is busy making it")


@signal("power")
def power(ctx: Context) -> Iterable[Pull]:
    """Everything electric runs on power, and runs slow without it: short power is urgent, and so is what
    makes it and what it burns."""
    yield Pull("power", 0.4, "everything electric runs on it")
    starved = [s for s in ctx.book.segments if s.mature and s.share("no_power") + s.share("low_power") >= 0.25]
    short = bool(ctx.site.get("power_short")) or bool(starved)
    if not short:
        return
    why = "power is short" + (f" ({starved[0].name} stood without it)" if starved else "")
    yield Pull("power", 0.85, why, urgent=True)
    facts = ctx.facts
    for kind in ("boiler", "generator", "solar", "accumulator", "pump"):
        for name in (facts.of_kind(kind, ready=True)[:2] if facts else ()):
            yield Pull(name, 0.6, why)
    for s in ctx.book.segments:
        if s.kind == "power":
            for item in s.takes:
                yield Pull(item, 0.7, why, urgent=True)


@signal("fuel")
def fuel(ctx: Context) -> Iterable[Pull]:
    """Burner machines stop without fuel: what they burn matters the more they burn, and at once when a
    line measurably ran out."""
    from companion import catalog_planner, rates
    try:
        draw = catalog_planner.coal_draw(ctx.site)
    except Exception:
        draw = 0.0
    if draw > 0:
        yield Pull(rates.FUEL, 0.25 + min(0.4, draw * 2), f"its burners take {draw:.2g} a second by hand")
    for s in ctx.book.segments:
        if s.mature and s.share("no_fuel") >= 0.1:
            yield Pull(rates.FUEL, 0.6, f"{s.name} ran out of fuel", urgent=True)


@signal("ore")
def ore(ctx: Context) -> Iterable[Pull]:
    """Drills running out of ore: new ground before the old runs dry, so mining that ore matters more."""
    facts = ctx.facts
    drills = (facts.of_kind("drill", ready=True)[:1] if facts else []) or ["electric-mining-drill"]
    for block in _list(ctx.site.get("base")):
        left = _map(_map(block).get("ore"))
        minutes = left.get("minutes")
        if left.get("name") and isinstance(minutes, (int, float)) and minutes < 30:
            w = 0.35 + 0.4 * (1 - max(0.0, float(minutes)) / 30)
            why = f"the {left['name']} under {block.get('stage') or block.get('purpose')} runs out in {minutes:.0f} minutes"
            yield Pull(str(left["name"]), w, why)
            for d in drills:
                yield Pull(d, w * 0.8, why)


@signal("robots")
def robots(ctx: Context) -> Iterable[Pull]:
    """Later the factory is refitted and grown by robots from blueprints, quickly, and robots place only
    what was made: the buildings its own plans use are worth automating (its mall), more as robots near."""
    done = ctx.researched()
    near = max(_space_progress(ctx), 1.0 if any("robot" in t for t in done) else 0.0)
    w = 0.12 + 0.3 * near
    yield Pull("mall", w, "robots will place what it makes")
    used: dict[str, int] = {}
    for block in _list(ctx.site.get("base")):
        for name, n in _map(_map(block).get("machines")).items():
            used[str(name)] = used.get(str(name), 0) + int(_num(n))
    for name, n in sorted(used.items(), key=lambda kv: -kv[1])[:12]:
        yield Pull(name, w * min(1.0, 0.5 + n / 40), "its plans keep using it, and robots will")
    facts = ctx.facts   # and what carries between them: the belts and inserters it can make now
    for kind in ("belt", "underground", "splitter", "inserter"):
        for name in (facts.of_kind(kind, ready=True)[:3] if facts else ()):
            yield Pull(name, w, "its plans keep using it, and robots will")


AXIS_SECTIONS = {"science": ("science", "labs"), "defense": ("defense", "ammo"), "aggression": ("ammo",),
                 "logistics": ("bus", "mall"), "power": ("power",), "ambition": ("rocket", "science")}


@signal("personality")
def personality(ctx: Context) -> Iterable[Pull]:
    """Its character leans its priorities: a scientist's labs, a fortifier's walls, a logistician's bus,
    an ambitious one's rocket, a power specialist's power; a frugal one keeps its mall lean."""
    axes = ctx.axes()
    for axis, kinds in AXIS_SECTIONS.items():
        lean = (axes.get(axis, 0.5) - 0.5) * 2
        if lean > 0.05:
            for kind in kinds:
                yield Pull(kind, 0.3 * lean, f"its {axis}")
    frugal = (axes.get("frugality", 0.5) - 0.5) * 2
    if frugal > 0.2:
        yield Pull("mall", 0.25 * frugal, "it is frugal", damp=True)


# --- weighing --------------------------------------------------------------------------------------------

class Priorities:
    """Every item's and every segment's priority for one engineer at one moment."""

    def __init__(self, ctx: Context, items: dict[str, Priority], sections: dict[str, Priority],
                 targets: dict[str, Priority], settings: list[Setting], errors: list[str]):
        self.ctx, self.items, self.sections, self.targets = ctx, items, sections, targets
        self.settings, self.errors = settings, errors

    def of(self, item: str) -> Priority:
        return self.items.get(item) or Priority(FLOOR, ("nothing pulls on it",))

    def section(self, segment: ledger.Segment | str) -> Priority:
        """A built segment's priority; by name, the most important segment of that kind, stage or purpose
        (a planned one weighed with `weigh` when none is built)."""
        if isinstance(segment, ledger.Segment):
            return self.sections.get(segment.id) or Priority(FLOOR)
        matches = [self.sections[s.id] for s in self.ctx.book.segments if s.id in self.sections
                   and segment in (s.kind, s.stage, s.purpose, s.id)]
        if matches:
            return max(matches, key=lambda p: p.weight)
        return self.weigh(kind=segment if segment in SECTIONS else None, name=segment)

    def weigh(self, kind: str | None = None, makes: Iterable[str] = (), name: str | None = None) -> Priority:
        """A section not built yet (the section planner's): as much as the pulls on its kind or name and
        the most wanted thing it would make."""
        parts = [self.targets[k] for k in (kind, name) if k and k in self.targets]
        made = [(self.of(i), i) for i in makes]
        for setting in self.settings:
            if setting.name in (kind, name):
                return Priority(setting.weight, (setting.label(),), set=setting.label())
        best = max(made, key=lambda pm: pm[0].weight, default=None)
        weight = _either([p.weight for p in parts] + ([best[0].weight] if best else []))
        reasons = tuple(r for p in parts for r in p.reasons[:1]) + ((f"makes {best[1]}",) if best else ())
        return Priority(max(FLOOR, weight), reasons or ("nothing pulls on it",),
                        urgent=any(p.urgent for p in parts))

    def ranked(self, n: int = 10) -> list[tuple[str, Priority]]:
        return sorted(self.items.items(), key=lambda kv: (-kv[1].weight, kv[0]))[:n]

    def summary(self, n: int = 6) -> str:
        """The most wanted items in a line (for logs and lookups)."""
        return "; ".join(f"{item} {p.level} ({p.why(1)})" for item, p in self.ranked(n))


def _section_keys(book: ledger.Ledger) -> set[str]:
    keys = set(SECTIONS)
    for s in book.segments:
        keys.update(k for k in (s.stage, s.purpose, s.id) if k)
    return keys


def _settings(ctx: Context) -> list[Setting]:
    """The strategist's settings still in force: not run out, their condition not met. A strategist
    request's situation is the freshest copy; else the site's."""
    raw = ctx.situation.get("priorities") if ctx.situation else None
    if raw is None and not ctx.situation:
        raw = ctx.site.get("priorities")
    out = []
    short = None
    for r in _list(raw):
        if not isinstance(r, Mapping) or not isinstance(r.get("name"), str) or r.get("level") not in SET_WEIGHT:
            continue
        s = Setting(r["name"], r["level"], r.get("until") if isinstance(r.get("until"), str) else None,
                    _num(r.get("left"), 1e9) if r.get("left") is not None else None,
                    _num(r.get("ago")) if r.get("ago") is not None else None,
                    r.get("reason") if isinstance(r.get("reason"), str) else None)
        if s.left is not None and s.left <= 0:
            continue
        if s.until:
            if short is None:
                short = {b.item for b in ctx.book.short()}
            if _met(s, ctx, short):
                continue
        out.append(s)
    return out


def _met(s: Setting, ctx: Context, short: set[str]) -> bool:
    """Whether a setting's condition has come about (an unknown one only runs out with time)."""
    kind, _, arg = s.until.strip().partition(" ")
    arg = arg.strip()
    if kind == "researched":
        return arg in ctx.researched()
    if kind == "built":
        return arg in ctx.built()
    if kind == "calm":
        return ctx.heat()[0] < CALM
    if kind == "attacked":
        last = ctx.last_attack()
        return last is not None and s.ago is not None and last[0] < s.ago * 60
    if kind == "supplied":
        return (arg or s.name) not in short
    return False


def _compute(ctx: Context, settings: list[Setting]) -> Priorities:
    errors: list[str] = []
    pulls: list[Pull] = []
    for name, fn in SIGNALS.items():
        try:
            pulls.extend(p for p in fn(ctx) or () if isinstance(p, Pull))
        except Exception as exc:   # one signal that cannot read its part of the situation never stops the rest
            errors.append(f"{name}: {exc}")
    raised: dict[str, list[Pull]] = {}
    damps = [p for p in pulls if p.damp and p.weight > 0]
    for p in pulls:
        if not p.damp and p.weight > 0:
            raised.setdefault(p.target, []).append(p)
    # Every pull on a target together: each makes it more likely to matter, none alone decides.
    targets: dict[str, Priority] = {}
    for target, ps in raised.items():
        ps.sort(key=lambda p: -p.weight)
        weight = _either(p.weight for p in ps)
        urgent = any(p.urgent for p in ps)
        targets[target] = Priority(weight, tuple(p.why for p in ps), urgent=urgent, direct=weight, sudden=urgent)
    keys = _section_keys(ctx.book) | {p.target for p in pulls if p.section}
    items = {t: p for t, p in targets.items() if t not in keys}
    # The strategist's settings fix their targets (an urgent need it lowered still wins).
    fixed: set[str] = set()
    for s in settings:
        p = targets.get(s.name) or Priority(FLOOR)
        if _index(s.weight) > _index(p.weight) and p.urgent:   # (lowered below what is urgent now)
            targets[s.name] = replace(p, reasons=p.reasons + (f"you set it {s.level}, but this is urgent",))
            continue
        targets[s.name] = Priority(s.weight, (s.label(),) + p.reasons[:1], direct=s.weight, set=s.label(),
                                   sudden=True)
        fixed.add(s.name)
        if s.name not in keys:
            items[s.name] = targets[s.name]
    book = ctx.recipes
    sections: dict[str, Priority] = {}

    def lift(item: str, weight: float, why: str, via: str | None = None) -> bool:
        """`item` wanted at least this much, for `via` (what it goes into) or for a section using it."""
        if item in fixed:
            return False
        old = items.get(item)
        if old is not None and old.weight >= weight - 1e-9:
            return False
        own = targets[item].reasons[:1] if item in targets else ()   # (what pulls on it itself, kept in sight)
        items[item] = Priority(weight, ((why,) if why else ()) + own, urgent=bool(old and old.urgent),
                               direct=old.direct if old else 0.0, sudden=bool(old and old.sudden), via=via)
        return True

    def chains() -> None:
        todo = sorted(items, key=lambda i: -items[i].weight)
        for _ in range(DEPTH):
            nxt = []
            for item in todo:
                p = items[item]
                for ingredient in _ingredients(book, item):
                    if lift(ingredient, p.weight * CHAIN, p.reasons[0] if p.reasons else "", via=item):
                        nxt.append(ingredient)
            if not nxt:
                break
            todo = nxt

    def segments() -> bool:
        changed = False
        for s in ctx.book.segments:
            parts = [targets[k] for k in dict.fromkeys((s.kind, s.stage, s.purpose, s.id)) if k and k in targets]
            mine = next((targets[k] for k in (s.id, s.stage, s.purpose, s.kind) if k in fixed and k in targets), None)
            if mine is not None:
                p = mine
            else:
                made = max(((items[i], i) for i in s.makes if i in items), key=lambda pm: pm[0].weight, default=None)
                weight = _either([q.weight for q in parts] + ([made[0].weight] if made else []))
                reasons = tuple(r for q in parts for r in q.reasons[:1])
                if made:
                    reasons = (f"makes {made[1]}" + (f" ({made[0].why(1)})" if made[0].why(1) else ""),) + reasons
                p = Priority(max(FLOOR, weight), reasons or ("nothing pulls on it",),
                             urgent=any(q.urgent for q in parts) or bool(made and made[0].urgent),
                             sudden=any(q.sudden for q in parts) or bool(made and made[0].sudden))
            old = sections.get(s.id)
            if old is None or abs(old.weight - p.weight) > 1e-9:
                changed = True
            sections[s.id] = p
        # what an important section takes is important too
        for s in ctx.book.segments:
            for item in s.takes:
                if lift(item, sections[s.id].weight * CHAIN, f"{s.name} uses it"):
                    changed = True
        return changed

    chains()
    for _ in range(4):
        if not segments():
            break
        chains()
    _lower(ctx, damps, items, sections, targets, fixed, book)
    for item, p in items.items():
        if p.weight < FLOOR and item not in fixed:
            items[item] = replace(p, weight=FLOOR)
    return Priorities(ctx, items, sections, targets, settings, errors)


def _ingredients(book: ratios.Book | None, item: str) -> list[str]:
    recipe = None if book is None or item in ratios.RAW or item in SECTIONS else book.recipe_for(item)
    return [name for name, _ in recipe.ingredients] if recipe else []


def _lower(ctx: Context, damps: list[Pull], items: dict[str, Priority], sections: dict[str, Priority],
           targets: dict[str, Priority], fixed: set[str], book: ratios.Book | None) -> None:
    """The lowerings, last, on what everything else made of each target. A fight's lowers all but what the
    fight needs, and that all the way down: the recipe chains of what it spares, the sections making
    those or of a kind it spares, and what they take (iron for ammunition is for the fight). One with a
    target lowers that target. The strategist's own settings stay as it set them."""
    reach: list[set[str]] = []
    for d in damps:
        spare = set(d.spare)
        grown = d.target == "*"
        while grown:
            todo = list(spare)
            while todo:
                for item in _ingredients(book, todo.pop()):
                    if item not in spare:
                        spare.add(item)
                        todo.append(item)
            grown = False   # then what the sections making it (or of a kind it spares) take
            for s in ctx.book.segments:
                if (s.kind in spare or spare & set(s.makes)) and not set(s.takes) <= spare:
                    spare.update(s.takes)
                    grown = True
        reach.append(spare)

    def factor(names: set[str], makes: Iterable[str] = ()) -> tuple[float, str | None, bool]:
        out, why, now = 1.0, None, False
        for d, spare in zip(damps, reach):
            if d.target in names or (d.target == "*" and not names & spare and not set(makes) & spare):
                out *= 1.0 - min(1.0, d.weight)
                why, now = why or d.why, now or d.urgent
        return out, why, now

    def lowered(p: Priority, f: float, why: str | None, now: bool) -> Priority:
        return replace(p, weight=p.weight * f, direct=p.direct * f, reasons=((why,) if why else ()) + p.reasons,
                       sudden=p.sudden or now)
    for item, p in list(items.items()):
        f, why, now = factor({item}) if item not in fixed else (1.0, None, False)
        if f < 1.0:
            items[item] = lowered(p, f, why, now)
    for s in ctx.book.segments:
        p = sections.get(s.id)
        if p is None or p.set:
            continue
        f, why, now = factor({k for k in (s.kind, s.stage, s.purpose, s.id) if k}, s.makes)
        if f < 1.0:
            sections[s.id] = lowered(p, f, why, now)
    for key, p in list(targets.items()):
        if key in items or key in fixed:
            continue
        f, why, now = factor({key})
        if f < 1.0:
            targets[key] = lowered(p, f, why, now)


# --- memory: hysteresis and held shortfalls, per engineer ------------------------------------------------

class _Memory:
    def __init__(self) -> None:
        self.tick: int | None = None
        self.levels: dict[str, list] = {}       # key -> [level, backed since, lower since] (see _hold)
        self.held: dict[str, list] = {}         # item -> [first held tick, times seen held, last tick seen]
        self.site: Mapping[str, Any] | None = None
        self.book: ledger.Ledger | None = None
        self.logged: dict[str, str] = {}


_memory: dict[int, _Memory] = {}
_lock = threading.RLock()


def _mem(npc: int, tick: int | None) -> _Memory:
    """An engineer's memory. (A save loaded is forget()'s: a site's tick may lag the game's, as the game
    keeps one export per request, so an earlier tick here is no sign of one.)"""
    m = _memory.get(npc)
    if m is None:
        m = _memory[npc] = _Memory()
    if tick is not None:
        m.tick = max(tick, m.tick or tick)
    return m


def _hold(m: _Memory, key: str, p: Priority, tick: int, hold: float, rise: float) -> Priority:
    """A level changes once the signals have said so for a while: up once they have not backed the level
    for `rise` ticks, down once they have said lower for `hold` ticks (a lull is no reason to drop what
    mattered); at once for something urgent or the strategist's own setting."""
    index = _index(p.weight)
    state = m.levels.get(key)   # [level, last tick the signals backed it, tick they first said lower]
    if state is None or p.set or p.sudden or p.urgent:
        m.levels[key] = [index, tick, None]
        return p
    level, backed, lower = state
    lo, hi = _band(level)
    if index == level or lo - MARGIN <= p.weight < hi + MARGIN:   # (still there, or near enough)
        state[1], state[2] = tick, None
        return p if index == level else replace(p, weight=min(max(p.weight, lo), hi - 1e-6), held=True)
    if index < level:
        state[2] = None
        moved = tick - backed >= rise
    else:
        if lower is None:
            state[2] = lower = tick
        moved = tick - lower >= hold
    if moved:
        m.levels[key] = [index, tick, None]
        return p
    return replace(p, weight=min(max(p.weight, lo), hi - 1e-6), held=True,
                   reasons=p.reasons + (f"kept {LEVELS[level][0]} for now",))


def _held_back(m: _Memory, prio: Priorities, tick: int) -> None:
    patience = prio.ctx.axis("patience")
    hold = HOLD_MINUTES * MINUTE * (0.6 + 0.8 * patience)
    rise = RISE_MINUTES * MINUTE
    for item, p in list(prio.items.items()):
        prio.items[item] = _hold(m, "item:" + item, p, tick, hold, rise)
    for sid, p in list(prio.sections.items()):
        prio.sections[sid] = _hold(m, "section:" + sid, p, tick, hold, rise)


# --- the interface ---------------------------------------------------------------------------------------

def assess(site: Mapping[str, Any] | None, request: Mapping[str, Any] | None = None, *,
           tick: int | None = None, hold: bool = True, book: ledger.Ledger | None = None) -> Priorities:
    """Every item's and segment's priority for the engineer whose site this is (its strategist's request,
    when in hand, adds a fresher situation). With hold, levels keep to the engineer's hysteresis (needs the
    game tick: the site's, or `tick`)."""
    ctx = Context(site, request, book)
    prio = _compute(ctx, _settings(ctx))
    tick = tick if tick is not None else ctx.tick
    if hold and ctx.npc is not None and tick is not None:
        with _lock:
            _held_back(_mem(ctx.npc, tick), prio, tick)
    return prio


@dataclass
class Shortfall:
    budget: ledger.Budget
    priority: Priority
    unfed: str | None = None      # the most important consumer left without its share
    held: bool = False            # below ACT: waits until it matters more
    raised: bool = False          # held before, acted on now
    times_held: int = 0

    def line(self) -> str:
        return (self.budget.line() + f"; priority {self.priority.level}"
                + (f": {self.priority.why()}" if self.priority.reasons else "")
                + (" (raised again)" if self.raised else ""))


def shortfalls(site: Mapping[str, Any] | None, request: Mapping[str, Any] | None = None, *,
               prio: Priorities | None = None, tick: int | None = None) -> list[Shortfall]:
    """The ledger's short items (Ledger.short), each weighed, the most important first. A shortfall matters
    as much as the most important consumer it leaves without its share: consumers are fed in order of
    priority, and the first one supply does not cover is the one that waits (one measurably starved
    counts in any case: belts do not feed by priority). What the signals pull on the item itself (an ask,
    the strategist's setting) counts too. Those under ACT are held, counted, and raised again once they
    matter more."""
    prio = prio or assess(site, request, tick=tick)
    book = prio.ctx.book
    out = []
    for b in book.short():
        consumers = sorted(book.consumers(b.item), key=lambda c: -prio.section(c).weight)
        unfed = consumers[0] if consumers and b.supply <= 0 else None
        total = 0.0
        for c in consumers if unfed is None else ():
            total += book._claim(c, b.item)
            if total > b.supply * (1 + 1e-9):
                unfed = c
                break
        starved = [c for c in consumers if c.mature and c.share("starved") >= ledger.STARVED]
        if starved:
            hungriest = max(starved, key=lambda c: prio.section(c).weight)
            if unfed is None or prio.section(hungriest).weight > prio.section(unfed).weight:
                unfed = hungriest
        p = prio.section(unfed) if unfed is not None else Priority(FLOOR)
        reasons = (f"{unfed.name} goes short" if unfed is not None else "",) + p.reasons[:1]
        item = prio.of(b.item)
        if item.set:
            weight, reasons = item.weight, (item.set,) + reasons
        elif item.direct > p.weight:
            weight, reasons = item.direct, item.reasons[:1] + reasons
        else:
            weight = p.weight
        priority = Priority(weight, tuple(r for r in reasons if r), urgent=p.urgent or item.urgent)
        out.append(Shortfall(b, priority, unfed.name if unfed is not None else None, held=not priority.acted))
    out.sort(key=lambda s: (-s.priority.weight, -s.budget.share))
    npc, tick = prio.ctx.npc, tick if tick is not None else prio.ctx.tick
    if npc is not None:
        with _lock:
            m = _mem(npc, tick)
            now = {s.budget.item for s in out}
            for item in [i for i in m.held if i not in now]:
                del m.held[item]   # not short any more
            for s in out:
                seen = m.held.get(s.budget.item)
                if s.held:
                    if seen is None:
                        m.held[s.budget.item] = seen = [tick, 0, None]
                    if tick is None or seen[2] != tick:   # (once per moment, however many ask)
                        seen[1], seen[2] = seen[1] + 1, tick
                    s.times_held = seen[1]
                elif seen is not None:
                    s.raised, s.times_held = True, seen[1]
                    del m.held[s.budget.item]
    return out


def pressing(site: Mapping[str, Any], request: Mapping[str, Any] | None = None, *,
             log: Callable[[str], None] | None = None) -> list[ledger.Budget]:
    """The shortfalls that matter now, most important first: what supply_first acts on. The held ones are
    told once each time they change, and so is one raised again."""
    found = shortfalls(site, request)
    if log is not None and found:
        npc = _npc_of(site)
        held = ", ".join(f"{s.budget.item} ({s.priority.level})" for s in found if s.held)
        raised = ", ".join(s.budget.item for s in found if s.raised)
        text = (f"held for now: {held}" if held else "") + ("; " if held and raised else "") \
            + (f"raised again: {raised}" if raised else "")
        with _lock:
            m = _memory.get(npc) if isinstance(npc, int) else None
            fresh = m is None or m.logged.get("pressing") != text
            if m is not None:
                m.logged["pressing"] = text
        if text and fresh:
            log(f"{site.get('request_id')}: the ledger's shortfalls {text}")
    return [s.budget for s in found if not s.held]


def fits(site: Mapping[str, Any] | ledger.Ledger, takes: Mapping[str, float], load: float = 1.0, *,
         weight: float | None = None, kind: str | None = None, makes: Iterable[str] = (),
         name: str | None = None, buffers: Mapping[str, float] | None = None,
         prio: Priorities | None = None) -> tuple[bool, list[str]]:
    """Whether a new section fits what the factory makes, weighed by priority, for the section planner.
    Its priority is `weight`, else weighed from its `kind`, `name` and what it `makes` (Priorities.weigh).
    Built sections that matter less than it yield to it: their claims count only in proportion (a mall
    may run dry while ammunition is made). The 85% rule and the notes are Ledger.fits's otherwise.
    `site` may be a Ledger, then with every claim in full unless `prio` is given."""
    if isinstance(site, ledger.Ledger):
        book = site
        if prio is None:
            return book.fits(takes, load, buffers)
    else:
        prio = prio or assess(site)
        book = prio.ctx.book
    if weight is None:
        weight = prio.weigh(kind=kind, makes=makes, name=name).weight
    return book.fits_ranked(takes, load, weight=weight, rank=lambda s: prio.section(s).weight, buffers=buffers)


def remember(site: Mapping[str, Any]) -> None:
    """The planner's latest site for its engineer, for the strategist's notes and teammates' signals."""
    npc = _npc_of(site)
    if npc is None or not isinstance(site.get("base"), list):
        return
    book = ledger.Ledger.from_site(site)
    with _lock:
        m = _mem(npc, site.get("tick") if isinstance(site.get("tick"), int) else None)
        m.site, m.book = site, book


def _team_short(npc: int) -> list[tuple[str, set[str]]]:
    with _lock:
        others = [(m.site, m.book) for other, m in _memory.items() if other != npc and m.site and m.book]
    return [(str(s.get("npc_name") or "a teammate"), {b.item for b in book.short()}) for s, book in others]


def notes_for(request: Mapping[str, Any], tick: int | None = None) -> dict[str, str]:
    """What the strategist's prompt says of its factory: `factory`, the shortfalls acted on with their
    priority, and `priorities`, the ones held for now. Empty when nothing is short (no news, no wake)."""
    npc = _npc_of(request)
    with _lock:
        m = _memory.get(npc) if npc is not None else None
        site, book = (m.site, m.book) if m is not None else (None, None)
    if site is None:
        return {}
    prio = assess(site, request, tick=tick, book=book)
    found = shortfalls(site, request, prio=prio, tick=tick)
    acted, held = [s for s in found if not s.held], [s for s in found if s.held]
    out = {}
    if acted:
        out["factory"] = " | ".join(s.line() for s in acted[:3])
    if held:
        named = ", ".join(f"{s.budget.item} ({s.priority.level}: {s.priority.why(2)})" for s in held[:4])
        out["priorities"] = (f"Short but held for now, as they matter less at the moment: {named}"
                             + (f" and {len(held) - 4} more" if len(held) > 4 else "")
                             + ". They come back as they matter more; prioritize changes that.")
    return out


def forget() -> None:
    """A save loaded or the game reconnected: what was held belongs to the other game."""
    with _lock:
        _memory.clear()
