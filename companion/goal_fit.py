"""The goal weighing's weights (companion/goal_brain.py) fitted offline from runs: each goal choice a run logged
(data/decisions/goals.jsonl) replayed on its site snapshot (sites/<request>.json) through today's weighing, and judged
by how the run went against the user's pace targets (companion/pace.py).

The judgment, in hindsight: of the goals it could build then, the one that served best the milestones that were due
(their aim at most pace.LEAD minutes off) and came late in that run, for the time it takes (Smith's rule: lateness
saved per minute spent, so a quick goal for a late milestone goes first). Where no candidate served a late milestone
the run says nothing about the choice: there the fit keeps today's choice, at ANCHOR's weight. The weights are fitted
as a softmax over each choice's candidates, held near today's (REGULARIZE), and checked run by run on runs left out.

    python -m companion.goal_fit tests/runs/long-42 tests/runs/long-43 ... [--out weights.json] [--json]

The file it writes is used only when NPC_GOAL_WEIGHTS names it (goal_brain.fitted_weights).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from companion import goal_brain, pace

# Weights fitted, each as today's times a factor (so it keeps its sign), but `due`, which starts at nothing.
SCALED = ("value", "unlocks", "speed", "hand", "cost_scale", "prior", "continue")
FITTED = (*SCALED, "due")
# What may be fitted: the pace term's weight alone, everything else as today (the default: one weight the outcomes
# can pin down), or every weight (the judged choices then pull the others about: fitted so on runs 42 to 55, the
# speed-up's weight fell to a quarter and the coal loop's growth lost to a lab in five runs whose outcome said nothing
# of it, 2026-10-26).
MODELS = {"due": ("due",), "all": FITTED}
TEMPERATURE = 0.5        # score units per e-fold of odds between two candidates
ANCHOR = 1.0             # weight of keeping today's choice where the run's outcome says nothing about it
REGULARIZE = 1.0         # per weight: squared log of its factor from today's
REGULARIZE_DUE = 0.5     # (and due's squared value)
MIN_MINUTES = 2.0        # the least time a goal is counted to take, in the judgment
ITERATIONS = 600


@dataclass
class Decision:
    """One logged goal choice, weighed again today, with its judgment in hindsight."""
    run: str
    request: str
    minute: float
    logged: str                       # what the run chose
    rows: list[dict]                  # today's weighing: each candidate's parts
    served: dict[str, dict[str, float]] = field(default_factory=dict)   # candidate: {milestone: share}
    gains: dict[str, float] = field(default_factory=dict)               # candidate: late minutes it serves
    target: dict[str, float] = field(default_factory=dict)               # the judgment: shares by worth (empty: no say)
    label: set[str] = field(default_factory=set)                         # the best in hindsight
    today: str = ""                                                      # today's choice, today's weights
    measured: float | None = None     # minutes until its next plan request (how long the build took, about)
    tick: int = 0                     # (with the request, the same choice in runs resumed from one checkpoint)


@dataclass
class Outcome:
    """A run's milestones: the first game minute each showed, and the minutes its snapshots span."""
    start: float
    end: float
    first: dict[str, float]

    def late(self, name: str) -> float | None:
        """Minutes past its aim the milestone came (or was still missing at the end), None without evidence (it came
        before the run began, or the run ended before its aim)."""
        aim = pace.BY_NAME[name].aim
        at = self.first.get(name)
        if at is not None:
            return None if at <= self.start else max(0.0, at - aim)
        return self.end - aim if self.end > aim else None


def snapshots(run: Path) -> list[tuple[float, str, Path]]:
    """(game minute, request, path) of a run's site snapshots, in game order."""
    out = []
    for path in (run / "sites").glob("*.json"):
        try:
            tick = json.loads(path.read_text(encoding="utf-8")).get("tick")
        except (OSError, ValueError):
            continue
        out.append((float(tick or 0) / 3600.0, path.stem.replace("-", ":", 1), path))
    return sorted(out)


def outcome(run: Path, snaps: Sequence[tuple[float, str, Path]] | None = None) -> Outcome:
    snaps = list(snapshots(run) if snaps is None else snaps)
    first: dict[str, float] = {}
    for at, _, path in snaps:
        try:
            site = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for name in pace.reached(site):
            first.setdefault(name, at)
    return Outcome(start=snaps[0][0] if snaps else 0.0, end=snaps[-1][0] if snaps else 0.0, first=first)


def weigh_again(site: Mapping[str, Any], logged: Iterable[str], asked: Iterable[str] = ()) -> list[dict]:
    """Today's weighing of a logged choice: the opening's open steps on that site (the prior), and the other
    candidates the run weighed that can still be built there (a shortfall's supply, a bus under way, what its
    strategist asked for, `asked`, weighed with ASK_PRIOR as it was)."""
    from companion import catalog_planner
    steps = catalog_planner.opening_steps(site, designer=True)
    extra = [g for g in logged if g in catalog_planner.PURPOSES and g not in steps
             and catalog_planner.available(g, site, designer=True)]
    return goal_brain.weigh(site, [*steps, *extra], opening=steps, asked=list(asked))


def load_run(run: Path, log: Callable[[str], None] = print) -> list[Decision]:
    """A run's logged goal choices, weighed again and judged against how the run went."""
    from companion import ratios
    path = run / "data" / "decisions" / goal_brain.LOG_NAME
    if not path.exists():
        return []
    snaps = snapshots(run)
    after = {request: at for at, request, _ in snaps}
    times = [at for at, _, _ in snaps]
    result = outcome(run, snaps)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        request = str(d.get("request"))
        if d.get("fuel_first"):   # (made by the coal rule over the scores: not a choice of the weights)
            continue
        snap = run / "sites" / (request.replace(":", "-") + ".json")
        try:
            site = json.loads(snap.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log(f"{run.name} {request}: no snapshot")
            continue
        logged = [c for c in d.get("candidates") or [] if isinstance(c, Mapping)]
        rows = weigh_again(site, [str(c.get("goal")) for c in logged],
                           [str(c.get("goal")) for c in logged if c.get("asked")])
        if not rows:
            continue
        minute = pace.minute(site)
        decision = Decision(run=run.name, request=request, minute=minute, logged=str(d.get("chose")), rows=rows,
                            tick=int(site.get("tick") or 0))
        try:
            book = ratios.Book.from_site(site)
        except Exception:
            book = None
        state = pace.State.of(site)
        for row in rows:
            try:
                _, _, machines = goal_brain.full_flows(row["goal"], site, book)
            except Exception:
                machines = {}
            decision.served[row["goal"]] = pace.serves(row["goal"], site, row.get("makes") or {}, machines, state)
        judge(decision, result)
        later = [t for t in times if t > after.get(request, minute) + 1e-6]
        decision.measured = (later[0] - minute) if later else None
        out.append(decision)
    return out


def unjudged(purpose: str) -> bool:
    """A goal the pace targets have no milestone for: defenses (walls, turrets). A choice with one among its
    candidates is not judged (judged, the wall row lost to every late milestone), until the targets have one."""
    from companion import catalog_planner
    spec = catalog_planner.PURPOSES.get(purpose) or {}
    return goal_brain.defense(purpose) or bool(spec.get("wall_block"))


def judge(decision: Decision, result: Outcome) -> None:
    """The hindsight judgment of one choice: each candidate's late minutes served (shares of the milestones that were
    due then and came late) per minute it takes, its worth; the target its share of the worth among them (a close
    call pulls little), none where nothing served a late milestone, where a due one has no outcome yet, or where a
    candidate is one the targets cannot judge."""
    worth, unknown = {}, False
    for row in decision.rows:
        gain = 0.0
        for name, share in decision.served.get(row["goal"], {}).items():
            if decision.minute < pace.BY_NAME[name].aim - pace.LEAD:
                continue
            late = result.late(name)
            unknown |= late is None   # (the run ended before its aim: against it a late one would always win)
            gain += share * (late or 0.0)
        decision.gains[row["goal"]] = round(gain, 2)
        worth[row["goal"]] = gain / max(MIN_MINUTES, float(row["cost"]))
    total = sum(worth.values())
    decision.today = top(decision, goal_brain.default_weights())
    if total <= 0 or unknown or any(unjudged(row["goal"]) for row in decision.rows):
        decision.target, decision.label = {}, set()
        return
    best = max(worth.values())
    decision.target = {g: v / total for g, v in worth.items() if v > 0}
    decision.label = {g for g, v in worth.items() if v >= best - 1e-9}


# --- the fit -------------------------------------------------------------------------------------------------

def weights_of(theta: Sequence[float], base: Mapping[str, float], names: Sequence[str] = FITTED) -> dict[str, float]:
    w = dict(base)
    for name, t in zip(names, theta):
        w[name] = max(0.0, t) if name == "due" else base[name] * math.exp(t)
    return w


def scores(decision: Decision, w: Mapping[str, float]) -> list[float]:
    return [goal_brain.score(row, w) for row in decision.rows]


def top(decision: Decision, w: Mapping[str, float]) -> str:
    s = scores(decision, w)
    return decision.rows[max(range(len(s)), key=lambda i: s[i])]["goal"]


def log_share(decision: Decision, w: Mapping[str, float], goals: set[str]) -> float:
    """The log of the softmax share of `goals` among the choice's candidates."""
    s = [x / TEMPERATURE for x in scores(decision, w)]
    peak = max(s)
    total = sum(math.exp(x - peak) for x in s)
    mine = sum(math.exp(x - peak) for x, row in zip(s, decision.rows) if row["goal"] in goals)
    return math.log(max(mine, 1e-300)) - math.log(total)


def loss(theta: Sequence[float], decisions: Sequence[Decision], base: Mapping[str, float],
         names: Sequence[str] = FITTED) -> float:
    w = weights_of(theta, base, names)
    if w["cost_scale"] <= 0:
        return math.inf
    fit = 0.0
    for d in decisions:
        if len(d.rows) < 2:
            continue
        if d.target:
            fit -= sum(share * log_share(d, w, {goal}) for goal, share in d.target.items())
        else:
            fit -= ANCHOR * log_share(d, w, {d.today})
    penalty = (REGULARIZE * sum(t * t for name, t in zip(names, theta) if name != "due")
               + REGULARIZE_DUE * (w["due"] - base["due"]) ** 2)
    return fit + penalty


def nelder_mead(f: Callable[[list[float]], float], x0: Sequence[float], step: float = 0.5,
                iterations: int = ITERATIONS) -> list[float]:
    """The least of f near x0 (Nelder and Mead's simplex: no gradients, no libraries)."""
    n = len(x0)
    simplex = [list(x0)] + [[x + (step if i == j else 0.0) for j, x in enumerate(x0)] for i in range(n)]
    values = [f(p) for p in simplex]
    for _ in range(iterations):
        order = sorted(range(n + 1), key=lambda i: values[i])
        simplex, values = [simplex[i] for i in order], [values[i] for i in order]
        size = max(abs(a - b) for p in simplex[1:] for a, b in zip(p, simplex[0]))
        if abs(values[-1] - values[0]) < 1e-7 and size < 1e-4:   # (flat and small: equal ends can straddle the least)
            break
        centre = [sum(p[j] for p in simplex[:-1]) / n for j in range(n)]
        worst = simplex[-1]
        reflect = [c + (c - w) for c, w in zip(centre, worst)]
        fr = f(reflect)
        if fr < values[0]:
            expand = [c + 2 * (c - w) for c, w in zip(centre, worst)]
            fe = f(expand)
            simplex[-1], values[-1] = (expand, fe) if fe < fr else (reflect, fr)
        elif fr < values[-2]:
            simplex[-1], values[-1] = reflect, fr
        else:
            outside = fr < values[-1]   # (between the second worst and the worst: contract toward the reflection)
            contract = [c + 0.5 * ((r if outside else w) - c) for c, r, w in zip(centre, reflect, worst)]
            fc = f(contract)
            if fc < (fr if outside else values[-1]):
                simplex[-1], values[-1] = contract, fc
            else:   # (shrink toward the best)
                best = simplex[0]
                simplex = [best] + [[b + 0.5 * (x - b) for b, x in zip(best, p)] for p in simplex[1:]]
                values = [values[0]] + [f(p) for p in simplex[1:]]
    return simplex[min(range(n + 1), key=lambda i: values[i])]


def fit(decisions: Sequence[Decision], base: Mapping[str, float] | None = None,
        names: Sequence[str] = MODELS["due"]) -> dict[str, float]:
    """The weights (`names` of them; the rest as `base`) that best explain the hindsight judgments, and keep today's
    choices where there is none."""
    base = dict(base or goal_brain.default_weights())
    theta = nelder_mead(lambda t: loss(t, decisions, base, names), [0.0] * len(names))
    return weights_of(theta, base, names)


# --- the check -----------------------------------------------------------------------------------------------

@dataclass
class Tally:
    judged: int = 0           # choices the outcome judged
    right_today: int = 0      # ...today's weights pick the judged best
    right_fitted: int = 0     # ...the fitted weights do
    unjudged: int = 0         # choices it did not
    kept: int = 0             # ...the fitted weights keep today's choice
    flips: list[dict] = field(default_factory=list)

    def add(self, decision: Decision, w: Mapping[str, float]) -> None:
        picked = top(decision, w)
        if decision.label:
            self.judged += 1
            self.right_today += decision.today in decision.label
            self.right_fitted += picked in decision.label
        else:
            self.unjudged += 1
            self.kept += picked == decision.today
        if picked != decision.today:
            self.flips.append({"run": decision.run, "request": decision.request, "minute": round(decision.minute, 1),
                               "today": decision.today, "fitted": picked, "judged": sorted(decision.label)})

    def line(self) -> str:
        return (f"judged {self.judged}: best in hindsight today {self.right_today}, fitted {self.right_fitted}; "
                f"not judged {self.unjudged}: today's choice kept {self.kept}; {len(self.flips)} choices change")


def distinct(decisions: Sequence[Decision]) -> list[Decision]:
    """Each choice once: runs resumed from one checkpoint replay the choices before it (runs 52 to 55 all hold
    request 1:28 at minute 60)."""
    seen, out = set(), []
    for d in decisions:
        if (d.request, d.tick) not in seen:
            seen.add((d.request, d.tick))
            out.append(d)
    return out


def lineages(decisions: Sequence[Decision]) -> dict[str, str]:
    """Each run's family: runs sharing a choice (resumed from one checkpoint) are one, left out together."""
    family = {d.run: d.run for d in decisions}

    def root(run: str) -> str:
        while family[run] != run:
            run = family[run]
        return run
    first: dict[tuple, str] = {}
    for d in decisions:
        key = (d.request, d.tick)
        if key in first:
            a, b = root(first[key]), root(d.run)
            if a != b:
                family[b] = a
        else:
            first[key] = d.run
    return {run: root(run) for run in family}


def held_out(decisions: Sequence[Decision], base: Mapping[str, float] | None = None,
             names: Sequence[str] = MODELS["due"]) -> tuple[Tally, dict[str, dict[str, float]]]:
    """Each family of runs in turn left out: fitted on the others, checked on it (each choice once)."""
    family = lineages(decisions)
    decisions = distinct(decisions)
    total, per_run = Tally(), {}
    for root in dict.fromkeys(family[d.run] for d in decisions):
        train = [d for d in decisions if family[d.run] != root]
        if not any(d.target for d in train):
            continue
        w = fit(train, base, names)
        per_run[root] = w
        for d in decisions:
            if family[d.run] == root:
                total.add(d, w)
    return total, per_run


def durations(decisions: Sequence[Decision]) -> dict[str, float]:
    """How the weighing's cost (minutes) of the goal each run chose compares with the minutes until its next plan
    request (its build, about): the median ratio, and how many."""
    ratios = []
    for d in decisions:
        row = next((r for r in d.rows if r["goal"] == d.logged), None)
        if row is not None and d.measured is not None and row["cost"] > 0:
            ratios.append(d.measured / row["cost"])
    ratios.sort()
    return {"count": len(ratios), "median_ratio": round(ratios[len(ratios) // 2], 2) if ratios else 0.0}


def report(runs: Sequence[Path], out: Path | None = None, model: str = "due",
           log: Callable[[str], None] = print) -> dict:
    """Every model fitted and checked run by run on runs left out; `model`'s weights, fitted on all, kept."""
    decisions = [d for run in runs for d in load_run(run, log)]
    base = goal_brain.default_weights()
    compare = {}
    for name, names in MODELS.items():
        check, per_run = held_out(decisions, base, names)
        compare[name] = {"line": check.line(), "judged": check.judged, "right_today": check.right_today,
                         "right_fitted": check.right_fitted, "unjudged": check.unjudged, "kept": check.kept,
                         "flips": check.flips,
                         "spread": {k: [round(w[k], 3) for w in per_run.values()] for k in names}}
    final = fit(distinct(decisions), base, MODELS[model])
    everything = Tally()
    for d in distinct(decisions):
        everything.add(d, final)
    judged = [d for d in distinct(decisions) if d.target]
    # (how far to trust the judgment: a best goal under the time floor is judged by lateness alone; a tie, not at all)
    caveats = {"best_under_floor": sum(1 for d in judged if any(r["goal"] in d.label and r["cost"] < MIN_MINUTES
                                                                 for r in d.rows)),
               "all_tied": sum(1 for d in judged if len(d.label) == len(d.rows))}
    result = {"schema": goal_brain.WEIGHTS_SCHEMA, "fitted_at": time.strftime("%Y-%m-%d %H:%M"), "model": model,
              "runs": [r.name for r in runs], "decisions": len(distinct(decisions)), "caveats": caveats,
              "families": sorted(set(lineages(decisions).values())),
              "weights": {k: round(v, 4) for k, v in final.items()},
              "today": {k: round(v, 4) for k, v in base.items()},
              "held_out": compare[model], "compare": compare,
              "all_runs": {"line": everything.line(), "flips": everything.flips},
              "durations": durations(decisions),
              "judged": [{"run": d.run, "request": d.request, "minute": round(d.minute, 1), "logged": d.logged,
                          "today": d.today, "best": sorted(d.label),
                          "gains": {g: v for g, v in d.gains.items() if v}} for d in judged]}
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit the goal weighing's weights from runs' decision logs.")
    parser.add_argument("runs", nargs="+", type=Path, help="run folders (with data/decisions and sites)")
    parser.add_argument("--model", choices=sorted(MODELS), default="due", help="which weights to fit and keep")
    parser.add_argument("--out", type=Path, help="write the fitted weights and the check here")
    parser.add_argument("--json", action="store_true", help="print the whole result as JSON")
    args = parser.parse_args(argv)
    result = report(args.runs, args.out, args.model)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    print(f"{result['decisions']} goal choices from {len(result['runs'])} runs")
    for name, check in result["compare"].items():
        print(f"held out, run by run, fitting {name}: " + check["line"])
        for f in check["flips"]:
            print(f"  {f['run']} {f['request']} minute {f['minute']}: {f['today']} -> {f['fitted']}"
                  f" (best in hindsight: {', '.join(f['judged']) or 'no say'})")
    print(f"fitted on all ({result['model']}): " + result["all_runs"]["line"])
    print("weights (today -> fitted): " + ", ".join(
        f"{k} {result['today'][k]:g} -> {result['weights'][k]:g}" for k in MODELS[result["model"]]))
    d = result["durations"]
    print(f"build minutes against the weighing's cost: median {d['median_ratio']}x over {d['count']} choices")
    c = result["caveats"]
    print(f"judged with a best goal under {MIN_MINUTES:g} minutes: {c['best_under_floor']}; all tied: {c['all_tied']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
