"""Weighing the library's builds for a job, and choosing: reuse one as it is, adapt one, or design new.

Every build of the job (saved for its purpose, or making the same things from the same ground as the
job's own built-in layout does) is weighed against what the job needs here. Each question is a small
check that returns findings: does it fit this ground, does it make enough, is it the right size, are its
parts to be had, what do its record and its notes say. A new question is one more function in CHECKS. A
finding says how far off the build is (its gap: 0 none, 1 as far off as it gets) and whether it stops
the build from being built as it is.

    reports = build_choice.weigh(catalog, site, purpose, reference=built_in_plan)
    choice = build_choice.decide(reports, designer=True)   # reuse, adapt, new, or the built-in layout
    build_choice.options(reports, designer=True)           # the same choices, as System One's options
    build_choice.adapt_brief(report, site)                 # what the designer is given to adapt one

The rules, when System One does not choose: reuse what fits and works; adapt when there are gaps (the
closest build, the designer changing only what its gaps need); design new only when nothing comes close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from companion import blueprints as bp
from companion import build_history as history
from companion import ledger, rates

TOLERANCE = history.TOLERANCE
ADAPT_GAP = 0.25      # a gap this big is worth the designer's time
FAR = 0.75            # a build this far off on any question does not come close
OVERSIZE = 2.0        # making this many times what is needed: it could be smaller
MAX_REPORTS = 6       # builds offered to System One
NOTES = 4             # notes a report carries
STAGE_COST = 0.2      # score for each item of its first stage the engineer must make before it can start
ADAPT_CHARS = 1500    # the brief an adaptation adds to the designer's goal, at most
NEAR_OFFSETS = 600    # spots tried per rotation when looking for where a build comes nearest to fitting
SIZED = ("rate", "size")   # questions the job's own built-in layout answers by construction


@dataclass
class Finding:
    check: str                  # the question: fit, rate, size, parts, record, notes...
    text: str                   # its answer in plain words
    gap: float = 0.0            # how far off (0 none)
    blocks: bool = False        # it cannot be built as it is, here and now
    key: str | None = None      # what the gap is about (a reading's key), so a variant can say it was for it
    adapt: bool = True          # whether a change of layout could close it (not one an adaptation failed to)


@dataclass
class Report:
    build: dict
    job: dict = field(default_factory=dict)
    placement: dict | None = None     # where it fits
    near: dict | None = None          # where it comes nearest to fitting, when it does not
    findings: list[Finding] = field(default_factory=list)
    cost: float = 0.0                 # what building it costs beyond the others (items to make first)
    covered: set = field(default_factory=set)   # gaps a variant of it was made for already
    need: dict = field(default_factory=dict)     # what the job needs here (items a second)

    @property
    def id(self) -> str:
        return self.build["id"]

    @property
    def usable(self) -> bool:
        """It fits, and nothing stops it being built as it is."""
        return self.placement is not None and not any(f.blocks for f in self.findings)

    @property
    def gaps(self) -> list[Finding]:
        """What keeps it from working for the job: what stops it, and gaps worth an adaptation."""
        return [f for f in self.findings if f.blocks or (f.adapt and f.gap >= ADAPT_GAP)]

    @property
    def to_adapt(self) -> list[Finding]:
        """Its gaps that adapting it could still close: not those a variant of it was made for (that
        variant is weighed in its own right, and adapted in turn if it still has them)."""
        return [f for f in self.gaps if f.key is None or f.key not in self.covered]

    @property
    def close(self) -> bool:
        return all(f.gap < FAR for f in self.findings)

    @property
    def distance(self) -> float:
        return round(sum(min(1.0, f.gap) for f in self.to_adapt), 4)

    @property
    def score(self) -> float:
        """The library's score (record, speed, measured performance, distance from home) less what its
        gaps and its first stage cost; a build that cannot be built here has none."""
        if self.placement is None:
            return -math.inf
        return round(bp.score(self.build, self.placement) - self.cost
                     - sum(min(1.0, f.gap) for f in self.findings if not f.blocks), 6)

    def summary(self, limit: int = 5, skip: Iterable[str] = ()) -> str:
        """Its report in a sentence or two: what is off first, then what is fine (but for the questions in
        `skip` that are fine: what a description says already)."""
        off = lambda f: f.blocks or f.gap >= TOLERANCE
        ordered = sorted((f for f in self.findings if off(f) or f.check not in skip), key=lambda f: (-off(f), -f.gap))
        return "; ".join(f.text for f in ordered[:limit] if f.text)


@dataclass
class Choice:
    kind: str                         # reuse, adapt, new or builtin
    report: Report | None = None      # the build it is about (builtin: the saved build to fall back on)
    why: str = ""


@dataclass
class Job:
    """What the weighing knows about the job here."""
    site: Mapping[str, Any]
    purpose: str
    need: dict[str, float]            # items a second the job needs here (empty: not known)
    need_from: str
    key: tuple | None                 # the resources and outputs of its built-in layout


# --- the job's need -------------------------------------------------------------------------------------

def job_key(build: Mapping[str, Any]) -> tuple | None:
    """What a build does, as far as its layout says: the ground its buildings must stand on and what its
    stages count as their output. None when that says nothing (a lab, a wall): then only its purpose."""
    ground = tuple(sorted({r.get("resource") for r in bp._as_list(build.get("resource_requirements"))
                           if isinstance(r, Mapping) and r.get("resource")}))
    outputs = tuple(sorted(build.get("outputs") or ()))
    return (ground, outputs) if ground or outputs else None


def need_for(site: Mapping[str, Any], reference: Mapping[str, Any] | None = None) -> tuple[dict[str, float], str]:
    """What the job needs here, items a second: a target the request carries (a planner's, or the factory
    plan's: site["need"]); else what the job's own built-in layout would make here, raised to cover what
    the ledger says the base is short of. Empty when nothing says."""
    target = site.get("need")
    if isinstance(target, Mapping):
        wanted = ledger._rates(target)
        if wanted:
            return wanted, "target"
    need: dict[str, float] = {}
    source = ""
    if reference is not None and rates.of(site):
        need = {k: v for k, v in (history.job_of(reference, site).get("makes") or {}).items() if v > 0}
        source = "its built-in layout" if need else ""
    if need:
        try:
            short = ledger.Ledger.from_site(site).short()
        except Exception:   # the ledger must never stop a plan
            short = []
        for b in short:
            if b.item in need:
                more = b.average / ledger.AVERAGE_LIMIT - b.supply
                if more > need[b.item]:
                    need[b.item], source = round(more, 4), "the ledger"
    return need, source


# --- the questions ----------------------------------------------------------------------------------------

def nearest(build: Mapping[str, Any], site: Mapping[str, Any]) -> dict | None:
    """Where a build that does not fit comes nearest to it: the spot with the fewest of its tiles taken or
    past the edge, and of its drills' tiles without their ore. None when the ground has none of its ore."""
    resources, blocked, _ = bp.site_tiles(site)
    _, _, width, height = bp._as_list(site["bounds"])
    width, height = int(width), int(height)
    best = None
    for turns in range(4):
        shape = bp.rotated(build, turns)
        footprint = [tuple(t) for t in shape["footprint"]]
        requirements = shape["resource_requirements"]
        wet = {tuple(t) for r in requirements if r["resource"] == "water" for t in r["tiles"]}
        if requirements:
            anchor = requirements[0]
            ore = resources.get(anchor["resource"], set())
            ax, ay = anchor["tiles"][0]
            offsets = sorted({(ox - ax, oy - ay) for ox, oy in ore})
        else:
            offsets = [(x, y) for x in range(0, width, 2) for y in range(0, height, 2)]
        step = max(1, len(offsets) // NEAR_OFFSETS)
        for ox, oy in offsets[::step]:
            outside = taken = bare = 0
            limit = best["violations"] if best else math.inf
            for x, y in footprint:
                tx, ty = x + ox, y + oy
                if not (0 <= tx < width and 0 <= ty < height):
                    outside += 1
                elif (tx, ty) in blocked and (x, y) not in wet:
                    taken += 1
                if outside + taken >= limit:
                    break
            if outside + taken >= limit:
                continue
            for r in requirements:
                ground = resources.get(r["resource"], set())
                bare += sum(1 for x, y in r["tiles"] if (x + ox, y + oy) not in ground)
            total = outside + taken + bare
            if total < limit:
                best = {"rotation": turns, "offset": [ox, oy], "violations": total, "outside": outside,
                        "taken": taken, "bare": bare, "tiles": len(footprint),
                        "ground": sum(len(r["tiles"]) for r in requirements)}
    return best


def fit(job: Job, report: Report) -> list[Finding]:
    """Does it fit this ground (turned any way): every drill on its ore, nothing on water, cliffs or
    someone else's ground."""
    spots = bp.placements(report.build, job.site, limit=1)
    if spots:
        report.placement = spots[0]
        text = "fits this ground"
        if spots[0].get("relocations"):
            text += f", moving {spots[0]['relocations']} of the engineer's own buildings"
        return [Finding("fit", text)]
    report.near = near = nearest(report.build, job.site)
    if not near:
        ground = sorted({r.get("resource") for r in bp._as_list(report.build.get("resource_requirements"))
                         if isinstance(r, Mapping)})
        return [Finding("fit", f"there is no {' or '.join(ground) or 'ground'} here for it to stand on", gap=1.0,
                        blocks=True, key="fit")]
    parts = []
    if near["taken"]:
        parts.append(f"{near['taken']} of its {near['tiles']} tiles are taken")
    if near["outside"]:
        parts.append(f"{near['outside']} lie past the edge of the ground")
    if near["bare"]:
        parts.append(f"{near['bare']} of the {near['ground']} tiles its drills need have no ore")
    share = near["violations"] / max(1, near["tiles"] + near["ground"])
    return [Finding("fit", "does not fit as it is: at the nearest spot " + ", ".join(parts),
                    gap=round(min(1.0, 2 * share), 3), blocks=True, key="fit")]


def _tried(build: Mapping[str, Any], key: str) -> dict[str, float] | None:
    """The need a build was adapted for, when it was adapted for `key`: the same gap for no more than
    that need is one the designer could not close, not one to ask it about again."""
    lineage = build.get("lineage") or {}
    if key not in set(lineage.get("addressed") or ()):
        return None
    return ledger._rates(lineage.get("need"))


def rate(job: Job, report: Report) -> list[Finding]:
    """Does it make what the job needs: its full speed in this game, times how much of the time its
    measured uses could run."""
    made = report.job.get("makes") or {}
    if not job.need or not made:
        return []
    share = history.ability(report.build)
    share = 1.0 if share is None else share
    out = []
    for item, needed in sorted(job.need.items()):
        have = float(made.get(item, 0.0)) * share
        tried = _tried(report.build, f"out:{item}")
        again = tried is None or needed > tried.get(item, 0.0) * (1 + TOLERANCE)
        if have <= 0:
            out.append(Finding("rate", f"does not make {item}", gap=1.0, key=f"out:{item}"))
        elif have < needed * (1 - TOLERANCE):
            out.append(Finding("rate", f"makes {have:.3g} of the {needed:.3g} {item} a second needed: it needs "
                               "expanding", gap=round(1 - have / needed, 3), key=f"out:{item}", adapt=again))
        else:
            out.append(Finding("rate", f"makes {have:.3g} {item} a second, {needed:.3g} needed"))
    return out


def size(job: Job, report: Report) -> list[Finding]:
    """Is it the right size: one making far more than the job needs costs parts and time to build."""
    made = report.job.get("makes") or {}
    share = history.ability(report.build)
    share = 1.0 if share is None else share
    ratios = [float(made.get(item, 0.0)) * share / needed for item, needed in job.need.items() if needed > 0]
    if not ratios or min(ratios) < OVERSIZE:
        return []
    times = min(ratios)
    tried = _tried(report.build, "size")
    again = tried is None or any(needed < tried.get(item, 0.0) * (1 - TOLERANCE) for item, needed in job.need.items())
    return [Finding("size", f"makes {times:.1f} times what is needed: it could be smaller",
                    gap=round(1 - 1 / times, 3), key="size", adapt=again)]


def can_make(item: str, site: Mapping[str, Any], depth: int = 3, _seen: frozenset = frozenset()) -> bool:
    """Whether the engineer can come by an item it does not carry: an unlocked recipe whose ingredients it
    carries, its base makes, it can mine, or can make in turn."""
    if depth <= 0 or item in _seen:
        return False
    carried = site.get("inventory") or {}
    for recipe in bp._as_list(site.get("recipes")):
        if not isinstance(recipe, Mapping) or not (recipe.get("name") == item or any(
                isinstance(p, Mapping) and p.get("name") == item for p in bp._as_list(recipe.get("products")))):
            continue
        ingredients = [i for i in bp._as_list(recipe.get("ingredients")) if isinstance(i, Mapping)]
        if all(carried.get(i.get("name"), 0) >= float(i.get("amount") or 1) or _had(i.get("name"), site)
               or can_make(i.get("name"), site, depth - 1, _seen | {item}) for i in ingredients):
            return True
    return False


def _had(item: str, site: Mapping[str, Any]) -> bool:
    """Its base makes it, or the engineer can mine it by hand: ore on the ground it knows, or what the
    trees and rocks there give (the game says which, site["minable"])."""
    for block in bp._as_list(site.get("base")):
        if isinstance(block, Mapping) and (item in ((block.get("capacity") or {}).get("makes") or {})
                                           or item in bp._as_list(block.get("recipes"))):
            return True
    resources = {r.get("resource") for r in bp._as_list(site.get("resources")) if isinstance(r, Mapping)}
    resources |= {p.get("resource") for p in bp._as_list(site.get("known_patches")) if isinstance(p, Mapping)}
    return item in resources or item in set(bp._as_list(site.get("minable")))


def parts(job: Job, report: Report) -> list[Finding]:
    """Are its parts to be had: what cannot be made yet (a locked recipe, a building the game does not
    have ready) stops it, and what its first stage needs that the engineer must make first costs time.
    The game makes or fetches what a stage lacks; asking for the first stage carried in full refused every
    saved stone, copper and steam layout once the engineers had put their carried drills down (the live
    game of 2026-10-23: "no saved layout fits", and the designer asked 26 times)."""
    build, site = report.build, job.site
    locked = set(bp.locked_items(build, site))
    facts = rates.of(site)
    carried = site.get("inventory") or {}
    if facts:
        for e in bp._as_list(build.get("entities")):
            spec = facts.entities.get(e.get("prototype"))
            item = (build.get("prototypes") or {}).get(e.get("prototype"), {}).get("placement_item", e.get("prototype"))
            if spec is not None and not spec.get("ready") and not carried.get(item):
                locked.add(item)
    total = sum(sum(bill.values()) for bill in bp._as_list(build.get("stage_bills"))) or 1
    if locked:
        share = sum(n for bill in bp._as_list(build.get("stage_bills")) for item, n in bill.items() if item in locked) / total
        return [Finding("parts", "needs what cannot be made here yet: " + ", ".join(sorted(locked)[:4])
                        + ": other parts would do", gap=round(min(1.0, max(share, 0.25)), 3), blocks=True, key="parts")]
    first = (bp._as_list(build.get("stage_bills")) or [{}])[0]
    short = {item: n - int(carried.get(item, 0)) for item, n in first.items() if carried.get(item, 0) < n}
    if not short:
        return [Finding("parts", "its first stage is in hand")]
    hard = sorted(item for item in short if not can_make(item, site))
    report.cost += STAGE_COST * sum(short.values())
    text = "its first stage needs " + ", ".join(f"{n} more {item}" for item, n in sorted(short.items())) + " made first"
    if hard:
        return [Finding("parts", text + " (nothing to make " + ", ".join(hard[:3]) + " from yet)", gap=0.5,
                        blocks=True, key="parts")]
    return [Finding("parts", text)]


def record(job: Job, report: Report) -> list[Finding]:
    """What its record says: how often it was built and finished, how quickly it gave its first output,
    and how much of the time its measured uses could run."""
    stats = report.build.get("stats") or {}
    built, done, failed = int(stats.get("uses", 0)), int(stats.get("successes", 0)), int(stats.get("failures", 0))
    out = []
    if failed >= 3 and failed > done:
        out.append(Finding("record", f"failed {failed} of its {built} builds"
                           + (f", last: {str(stats.get('last_failure'))[:60]}" if stats.get("last_failure") else ""),
                           gap=round(failed / max(1, built), 3), blocks=True, key="built"))
    elif built:
        first = [t for t in bp._as_list(stats.get("first_output_ticks")) if isinstance(t, (int, float))]
        out.append(Finding("record", f"built {built} times, {done} finished"
                           + (f", first output after about {round(sum(first) / len(first) / 60)} s" if first else "")))
    else:
        out.append(Finding("record", "never built yet"))
    share = history.ability(report.build)
    measured = sum(1 for u in history.uses(report.build) if history.capability(u) is not None)
    if share is not None:
        out.append(Finding("record", f"its machines could run {share:.0%} of the time over {measured} measured uses"))
    return out


def notes(job: Job, report: Report) -> list[Finding]:
    """What its notes say: each one a gap, as big as it was and as often as it was seen. One that its
    parent was adapted for already and that is still there is not the layout's to close."""
    addressed = set((report.build.get("lineage") or {}).get("addressed") or ())
    out = []
    for note in history.notes(report.build)[:NOTES]:
        seen, of = int(note.get("seen") or 0), max(1, int(note.get("of") or 1))
        out.append(Finding("notes", f"{note.get('text')}, in {seen} of {of} uses",
                           gap=round(min(1.0, abs(float(note.get("gap") or 0))) * seen / of, 3),
                           key=note.get("key"), adapt=note.get("key") not in addressed))
    return out


# The questions, in order (fit first: the others may use where it stands).
def _inward(build: Mapping[str, Any], port: Mapping[str, Any]) -> bool:
    """Whether the edge belt at a port runs into the block (not along its edge, fed by its own drills)."""
    w, h = (build.get("size") or [0, 0])[:2]
    into = {"north": (0, 1), "south": (0, -1), "west": (1, 0), "east": (-1, 0)}.get(str(port.get("edge")))
    step = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
    for e in build.get("entities") or ():
        pos = e.get("position") or {}
        if abs(float(pos.get("x", -9)) - float(port.get("x", -1))) < 0.01 and \
                abs(float(pos.get("y", -9)) - float(port.get("y", -1))) < 0.01:
            return into is not None and step.get(int(e.get("direction") or 0) // 4 * 4) == into
    return into is not None   # (no belt found there: as the port says)


def _unfed(build: Mapping[str, Any], job: Any) -> list[str]:
    """What an imported block's machines take that none of them makes, when it holds no chest to put it in: it can
    only come by belt from outside (a user's red science block took its plates on undergrounds at its edge, read as
    no input port, and stood unfed, F-21)."""
    from companion import ratios
    if any("chest" in str(e.get("prototype")) or str(e.get("prototype")).endswith("container")
           for e in build.get("entities") or ()):
        return []
    try:
        book = ratios.Book.from_site(getattr(job, "site", None) or {})
    except Exception:
        return []
    recipes = [book.recipes[str(e["recipe"])] for e in build.get("entities") or ()
               if e.get("recipe") and str(e["recipe"]) in book.recipes]
    made = {n for r in recipes for n, _ in r.products}
    return sorted({n for r in recipes for n, _ in r.ingredients} - made)


def feed(job: Job, report: Report) -> list[Finding]:
    """An imported layout that takes its inputs by belt or pipe at its edge: the catalog's goals build it as it is,
    with nothing brought to those edges, so it would stand and wait for ever (a user's red science block, all belts in,
    stood 221 minutes with no bus to feed it, F-21). Such a block is built by the science path, which brings belts
    to it or chests (science_blocks, input_hookup); here it is turned down."""
    build = report.build if isinstance(report.build, Mapping) else {}
    record = build.get("import") or {}
    ports = [q for q in record.get("ports") or () if isinstance(q, Mapping)]
    pumped = any(str(e.get("prototype")) in ("offshore-pump", "pumpjack") for e in build.get("entities") or ())
    takes = sorted({str(q.get("item") or "?") for q in ports
                    if (q.get("way") == "in" and (record.get("ports_from") == "sidecar" or _inward(build, q)))
                    or (q.get("kind") == "fluid" and q.get("way") is None and not pumped)})
    if not takes and record:
        takes = _unfed(build, job)   # (inputs that come in some other way: undergrounds at its edge, F-21)
    if not takes:
        return []
    return [Finding("feed", f"takes {', '.join(takes[:4])} by belt or pipe at its edge, which this goal does not "
                            f"bring", gap=1.0, blocks=True, adapt=False)]


CHECKS: list[Callable[[Job, Report], list[Finding]]] = [fit, rate, size, parts, record, notes, feed]


# --- weighing ---------------------------------------------------------------------------------------------

def peers(catalog: Any, purpose: str, key: tuple | None) -> list[dict]:
    """Every build of the job: saved for its purpose, or doing the same (the same ground and outputs as
    the job's own built-in layout) under another purpose's name; a mining layout for whichever ore it is put on
    (an imported one naming none: blueprints.ANY_ORE) as one for the job's ore, its ground resolved to it."""
    with catalog._lock:
        saved = list(catalog.blueprints.values())
    out = []
    for b in saved:
        if b.get("purpose") == purpose or (key is not None and job_key(b) == key):
            out.append(b)
        elif key is not None and len(key[0]) == 1 and set(key[1]) <= {key[0][0]} \
                and key[0][0] not in ("water", "crude-oil") and job_key(b) == ((bp.ANY_ORE,), ()):
            ore = key[0][0]   # (a mining job: what it makes is that ore, if anything)
            out.append({**b, "resource_requirements": [{**r, "resource": ore} if r.get("resource") == bp.ANY_ORE
                                                       else r for r in bp._as_list(b.get("resource_requirements"))]})
    return out


def weigh(catalog: Any, site: Mapping[str, Any], purpose: str, *, reference: Mapping[str, Any] | None = None,
          checks: Iterable[Callable] | None = None) -> list[Report]:
    """A report for every build of the job, those that can be built as they are first (best score
    first), then the rest, closest first. `reference`: the job's built-in layout here, as a plan."""
    built_in = bp.from_plan(reference, site, source="reference", purpose=purpose) if reference else None
    need, need_from = need_for(site, built_in)
    job = Job(site=site, purpose=purpose, need=need, need_from=need_from,
              key=job_key(built_in) if built_in else None)
    reports = []
    for build in peers(catalog, purpose, job.key):
        report = Report(build=build, job=history.job_of(build, site), need=dict(need))
        for check in checks or CHECKS:
            report.findings.extend(check(job, report))
        with catalog._lock:
            for variant in (build.get("lineage") or {}).get("variants") or ():
                lineage = (catalog.blueprints.get(variant) or {}).get("lineage") or {}
                report.covered |= set(lineage.get("addressed") or ())
        reports.append(report)
    reports.sort(key=lambda r: (not r.usable, -r.score if r.usable else r.distance, r.id))
    return reports


# --- deciding ---------------------------------------------------------------------------------------------

def decide(reports: list[Report], *, designer: bool, busy: Iterable[str] = (), purpose: str = "") -> Choice:
    """The rules: reuse the best build that fits and works; else, with the designer, adapt the closest
    one (one another engineer is not adapting right now), or design new when nothing comes close; else
    the best build that fits, gaps and all, or the built-in layout (sized to the job by construction,
    so it serves before a saved build that is the wrong size)."""
    busy = set(busy)
    usable = [r for r in reports if r.usable]
    sound = [r for r in usable if not r.gaps]
    if sound:
        return Choice("reuse", max(sound, key=lambda r: (r.score, r.id)), "fits and works")
    best = max(usable, key=lambda r: (r.score, r.id), default=None)
    if designer:
        close = [r for r in reports if r.close and r.to_adapt and r.id not in busy]
        if close:
            pick = min(close, key=lambda r: (r.distance, -r.score if r.usable else 0.0, r.id))
            return Choice("adapt", pick, "closest, with gaps: " + "; ".join(f.text for f in pick.to_adapt[:2]))
        if not any(r.close for r in reports) and f"new:{purpose}" not in busy:
            return Choice("new", None, "nothing saved comes close" if reports else "nothing saved for this job")
    if best:
        if any(f.check in SIZED for f in best.gaps):
            return Choice("builtin", best, "the saved layouts are the wrong size; the built-in layout")
        return Choice("reuse", best, "fits; its gaps wait for the designer")
    return Choice("builtin", None, "no saved layout fits")


def options(reports: list[Report], *, designer: bool, describe: Callable[[Report], str] | None = None,
            limit: int = MAX_REPORTS) -> list[dict]:
    """System One's options for the builds: reuse one as it is (those that can be built), adapt one (those
    that come close, with gaps, when the designer may be asked), each with its report and notes."""
    out = []
    for r in reports[:limit]:
        if r.usable:
            text = describe(r) if describe else bp.describe({"blueprint": r.build, "placement": r.placement})
        else:
            text = shape(r.build)
        report = r.summary(skip=("fit", "record") if r.usable else ())
        if r.usable:
            out.append({"id": f"bp-{r.id}", "description": f"Reuse as it is. {text} Report: {report}."})
        if designer and r.close and r.to_adapt:
            out.append({"id": f"adapt-{r.id}", "description":
                        f"Adapt layout {r.id[:8]}: the designer changes only what its gaps need ("
                        + "; ".join(f.text for f in r.to_adapt[:3]) + "); slower and costs money; kept as a variant "
                        "of it." + ("" if r.usable else f" Report: {report}.")})
    return out


def shape(build: Mapping[str, Any]) -> str:
    """A saved build in a sentence, where it does not stand anywhere yet."""
    counts: dict[str, int] = {}
    for e in bp._as_list(build.get("entities")):
        counts[e["prototype"]] = counts.get(e["prototype"], 0) + 1
    parts_text = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    w, h = (list(build.get("size") or []) + [0, 0])[:2]
    return f"Saved layout {build['id'][:8]}: {parts_text} in {len(bp._as_list(build.get('stages')))} stages, {w}x{h} tiles."


def chosen(option: str, reports: list[Report]) -> Choice | None:
    """System One's pick as a choice."""
    by_id = {r.id: r for r in reports}
    kind, _, build_id = str(option).partition("-")
    if kind == "bp" and build_id in by_id and by_id[build_id].usable:
        return Choice("reuse", by_id[build_id], "system one")
    if kind == "adapt" and build_id in by_id:
        return Choice("adapt", by_id[build_id], "system one")
    return None


def claim(catalog: Any, choice: Choice, purpose: str) -> str | None:
    """Mark a build being adapted (or the job being designed afresh) for the engineers sharing the library:
    the others reuse or build what they have meanwhile instead of asking the designer for the same thing."""
    if choice.kind not in ("adapt", "new"):
        return None
    key = choice.report.id if choice.kind == "adapt" and choice.report else f"new:{purpose}"
    with catalog._lock:
        catalog.__dict__.setdefault("designing", set()).add(key)
    return key


def release(catalog: Any, key: str | None) -> None:
    if key:
        with catalog._lock:
            catalog.__dict__.setdefault("designing", set()).discard(key)


def busy(catalog: Any) -> set[str]:
    with catalog._lock:
        return set(catalog.__dict__.get("designing") or ())


# --- adapting ---------------------------------------------------------------------------------------------

def adapt_brief(report: Report, site: Mapping[str, Any], limit: int = ADAPT_CHARS) -> dict:
    """What the designer is given to adapt a build: the build itself where it stands here (or where it
    comes nearest to fitting), its gaps and notes, and to change only what those need."""
    build = report.build
    spot = report.placement or report.near
    stats = build.get("stats") or {}
    lines = [f"Start from saved layout {build['id'][:8]} (built {stats.get('uses', 0)} times) and change only what "
             "these gaps need, keeping the rest as it is:"]
    keys = []
    for f in report.to_adapt[:5]:
        lines.append(f"- {f.text}")
        if f.key:
            keys.append(f.key)
    for note in history.notes(build)[:3]:
        if note.get("key") not in keys:
            lines.append(f"- noted in its uses: {note.get('text')}")
    if spot and report.placement is None:
        lines.append("Below it stands where it comes nearest to fitting: move or reshape it so every building is "
                     "on free ground inside the area and every drill on its ore.")
    if spot:
        plan = bp.instantiate(build, spot, "adapt")
        rows = []
        for e in plan["entities"]:
            row = f"{e['id']} {e['prototype']} {e['position']['x']:g},{e['position']['y']:g} facing {e['direction']}"
            if e.get("recipe"):
                row += f" recipe {e['recipe']}"
            if e.get("belt_to_ground_type"):
                row += f" {e['belt_to_ground_type']} end"
            rows.append(row)
        lines.append("Its buildings here (entity centres in local tiles): " + "; ".join(rows) + ".")
        lines.append("Its stages: " + "; ".join(f"{s['id']}: {', '.join(s['entity_ids'])}" for s in plan["stages"]) + ".")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit - 3].rsplit(";", 1)[0] + "..."
    return {"text": text, "entities": len(bp._as_list(build.get("entities"))), "parent": build["id"],
            "addressed": sorted(set(keys)), "need": dict(report.need)}
