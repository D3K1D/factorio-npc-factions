"""The user's pace targets as a signal for the goal weighing: which milestones a goal brings nearer, and how near or
past its aim each one is now (the user, 2026-10-25: play like a higher-level experienced player; aim for the faster
half of that range). The goal weighing had no sense of time: red science was due at 25 minutes, it was 45, and burner
goals still won by a hair (run 46, 2026-10-26).

A goal serves a milestone it makes the thing of (its layout's products, the recipes it needs, a bus for "main bus
begun"), in full, or for "mostly electric mining" the share of the drills still missing it adds; and, at
PREREQ_SHARE of that, a milestone of a goal that waits on it being built first. Its `due` is the most pressing of
them: 0 until LEAD minutes before the milestone's aim, 1 at the aim, more past it (up to DUE_CAP).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Milestone:
    name: str
    lo: float      # an experienced player's range, game minutes
    hi: float
    aim: float     # the faster half of it: the aim
    thing: str     # the item or building that shows it ("bus": the main bus)


# The user's table (experienced players, cumulative game minutes; memory user-pace-targets, as tools/pace_report.py)
MILESTONES = (
    Milestone("steam power", 5, 15, 10, "steam-engine"),
    Milestone("mostly electric mining", 10, 30, 22, "electric-mining-drill"),
    Milestone("red science automated", 20, 50, 25, "automation-science-pack"),
    Milestone("green science automated", 45, 120, 50, "logistic-science-pack"),
    Milestone("main bus begun", 60, 180, 90, "bus"),
    # (plates on the bus: the user's own play had one iron and one copper lane at about 68, Play-Analysis-2026-10-28)
    Milestone("first bus lane", 60, 180, 68, "bus"),
    Milestone("steel", 60, 180, 90, "steel-plate"),
    Milestone("military science", 90, 240, 120, "military-science-pack"),
)
BY_NAME = {m.name: m for m in MILESTONES}
LEAD = 10.0             # minutes before its aim a milestone starts to press
DUE_CAP = 3.0           # (and how far past it counts at most: LEAD * DUE_CAP minutes)
PREREQ_SHARE = 0.5      # of a milestone served by a goal that waits on this one
ELECTRIC_MIN = 6        # "mostly electric": this many electric drills, and twice the burner drills


def minute(site: Mapping[str, Any]) -> float:
    return float(site.get("tick") or 0) / 3600.0


def drills(site: Mapping[str, Any]) -> tuple[int, int]:
    """(electric, burner) mining drills in its base's blocks."""
    electric = burner = 0
    for b in site.get("base") or []:
        machines = b.get("machines") if isinstance(b, Mapping) else None
        if isinstance(machines, Mapping):
            electric += int(machines.get("electric-mining-drill") or 0)
            burner += int(machines.get("burner-mining-drill") or 0)
    return electric, burner


def first_lane(site: Mapping[str, Any]) -> bool:
    """Plates on the bus: its smelting (or its feed from the base's) and its first segment built (the lanes down to its
    first module), not only its power row ("main bus begun": the long run's 145 minutes was its power row, F-18)."""
    plan = site.get("base_plan") if isinstance(site.get("base_plan"), Mapping) else {}
    done = {str(k) for k in (plan.get("done") or {})} if isinstance(plan.get("done"), Mapping) else set()
    return any(d.startswith(("smelting-", "feed-")) for d in done) and any(d.startswith("bus-") for d in done)


RED_PACK, GREEN_PACK, MILITARY_PACK = "automation-science-pack", "logistic-science-pack", "military-science-pack"


def reached(site: Mapping[str, Any]) -> set[str]:
    """The milestones its base shows (the same reading as tools/pace_report.py)."""
    done = {str(p) for p in site.get("completed_purposes") or []}
    bus = bool((site.get("base_plan") or {}).get("stages")) if isinstance(site.get("base_plan"), Mapping) else False
    out = set()
    if done & {"steam-power", "power-plant"}:
        out.add("steam power")
    electric, burner = drills(site)
    if electric >= ELECTRIC_MIN and electric >= 2 * burner:
        out.add("mostly electric mining")
    # (a pack line fed from chests by hand is not automated, the user 2026-10-29: its milestone waits on its belts)
    from companion import tier_path
    hand = {p: bool(tier_path.pack_hand_fed(site, p)) for p in (RED_PACK, GREEN_PACK, MILITARY_PACK)}
    if ("red-science-automation" in done or bus) and not hand[RED_PACK]:
        out.add("red science automated")
    if "green-science-automation" in done and not hand[GREEN_PACK]:
        out.add("green science automated")
    if bus:
        out.add("main bus begun")
    if first_lane(site):
        out.add("first bus lane")
    machines = [b.get("machines") or {} for b in site.get("base") or [] if isinstance(b, Mapping)]
    if "steel-smelting" in done or any(int(m.get("steel-furnace") or 0) for m in machines) \
            or "steel-plate" in str(site.get("base")):
        out.add("steel")
    if "military-science-automation" in done and not hand[MILITARY_PACK]:
        out.add("military science")
    return out


@dataclass
class State:
    """What the pace term reads of a site, read once for all its candidates."""
    minute: float
    reached: set
    electric: int
    burner: int
    bridged: dict = field(default_factory=dict)   # packs made by machine but fed by hand: {pack: what by hand}

    @classmethod
    def of(cls, site: Mapping[str, Any]) -> "State":
        from companion import tier_path
        electric, burner = drills(site)
        return cls(minute(site), reached(site), electric, burner, tier_path.bridged(site))


def retired(purpose: str, site: Mapping[str, Any], makes: Mapping[str, float]) -> tuple[int, int]:
    """(burner drills, electric drills) in the blocks a goal takes down once built: an electric goal those it
    retires (companion/electric_start.py: not a hoarder's, not before its parts cover them), the bus its superseded
    smelting."""
    from companion import bus_planner, catalog_planner, electric_start
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    if spec.get("bus"):
        gone = set(bus_planner.SUPERSEDED)
    elif spec.get("electric_smelting") or spec.get("electric_supply"):
        try:
            gone = set(electric_start.retiring(site, purpose, adds=sum(float(v) for v in makes.values())))
        except Exception:   # (a judgment: nothing retired)
            gone = set()
    else:
        gone = set()
    burner = electric = 0
    for b in site.get("base") or []:
        if isinstance(b, Mapping) and b.get("purpose") in gone:
            machines = b.get("machines") or {}
            burner += int(machines.get("burner-mining-drill") or 0)
            electric += int(machines.get("electric-mining-drill") or 0)
    return burner, electric


def electric_share(purpose: str, site: Mapping[str, Any], machines: Mapping[str, int], state: State,
                   makes: Mapping[str, float] | None = None) -> float:
    """The share of the electric drills still missing for "mostly electric mining" a goal adds (counting the drills
    in what it retires): a smelting block's dozen drills more than a coal supply's two."""
    need = max(ELECTRIC_MIN, 2 * state.burner) - state.electric
    if need <= 0:
        return 0.0
    added = int(machines.get("electric-mining-drill") or 0) or 1   # (a layout not known yet: at least one)
    gone_burner, gone_electric = retired(purpose, site, makes or {})
    burner = max(0, state.burner - gone_burner)
    after = max(0, max(ELECTRIC_MIN, 2 * burner) - (state.electric - gone_electric + added))
    return max(0.0, min(1.0, (need - after) / need))


def direct(purpose: str, site: Mapping[str, Any], makes: Mapping[str, float], machines: Mapping[str, int],
           state: State) -> dict[str, float]:
    """The milestones not reached yet a goal makes the thing of, each with its share."""
    from companion import catalog_planner
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    things = set(makes) | {str(n) for n in spec.get("requires_recipes") or ()}
    if spec.get("bus"):
        things.add("bus")
    if makes.get("power"):
        things.add("steam-engine")
    out = {}
    for m in MILESTONES:
        if m.name in state.reached or m.thing not in things:
            continue
        share = electric_share(purpose, site, machines, state, makes) if m.thing == "electric-mining-drill" else 1.0
        if share > 0:
            out[m.name] = share
    # (a pack line standing on bridge chests: what brings its inputs by belt serves its milestone, pressing harder the
    # longer it stands past its aim, so the chests do not linger; the coordinator, 2026-10-29)
    if spec.get("bus") or spec.get("connects"):
        for m in MILESTONES:
            if m.thing in state.bridged and m.name not in state.reached:
                out[m.name] = max(out.get(m.name, 0.0), 1.0)
    return out


def prerequisites(purpose: str, done: set) -> set[str]:
    """The goals not built yet that `purpose` waits on, all the way down."""
    from companion import catalog_planner
    out, todo = set(), [purpose]
    while todo:
        for n in (catalog_planner.PURPOSES.get(todo.pop()) or {}).get("requires_built") or ():
            if n not in out and not catalog_planner.has_built(n, done):
                out.add(n)
                todo.append(n)
    return out


def stands_for(purpose: str) -> set[str]:
    """A goal and those it replaces: what waits on any of them waits on it."""
    from companion import catalog_planner
    return {purpose, *((catalog_planner.PURPOSES.get(purpose) or {}).get("replaces") or ())}


def serves(purpose: str, site: Mapping[str, Any], makes: Mapping[str, float] | None = None,
           machines: Mapping[str, int] | None = None, state: State | None = None) -> dict[str, float]:
    """The milestones not reached yet a goal brings nearer, each with its share: those it makes the thing of, and at
    PREREQ_SHARE those of the goals not built yet that wait on it (or on what it replaces: the bus waits on electric
    iron mining, which electric iron smelting stands for)."""
    from companion import catalog_planner
    state = state or State.of(site)
    out = direct(purpose, site, makes or {}, machines or {}, state)
    done = catalog_planner.built_purposes(site)
    mine = stands_for(purpose)
    for other in catalog_planner.PURPOSES:
        if other == purpose or other in done or not mine & prerequisites(other, done):
            continue
        for name, share in direct(other, site, {}, {}, state).items():
            out[name] = max(out.get(name, 0.0), PREREQ_SHARE * share)
    return out


def pressing(name: str, at: float) -> float:
    """How hard a milestone presses at game minute `at`: 0 until LEAD minutes before its aim, 1 at it, up to
    DUE_CAP past it."""
    m = BY_NAME[name]
    return max(0.0, min(DUE_CAP, (at - (m.aim - LEAD)) / LEAD))


def due(purpose: str, site: Mapping[str, Any], makes: Mapping[str, float] | None = None,
        machines: Mapping[str, int] | None = None, state: State | None = None) -> tuple[float, str | None]:
    """(how due a goal is by the pace targets, the milestone that makes it so): its served milestones' shares times
    how hard each presses now, the most of them."""
    state = state or State.of(site)
    best, which = 0.0, None
    for name, share in serves(purpose, site, makes, machines, state).items():
        value = share * pressing(name, state.minute)
        if value > best:
            best, which = value, name
    return best, which
