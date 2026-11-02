"""When an engineer is strong enough to take out a cluster of biter nests, and which first (the user, 2026-10-25:
"they need to start planning to attack biters when they are powerful enough to take out nests").

From what its strategist sees (`situation`): the nest clusters round it (spawners, worms by size, the
pollution they soak up, how far), what it carries to fight with (ammunition by kind, turrets to creep with,
armor, a gun, health), how far the biters have evolved, and whether its base was hit lately.

    theirs      a cluster's strength: its spawners (what they send while attacked) and worms by size,
                scaled up as the biters evolve
    ours        its own: ammunition (better kinds count more), turrets to creep up with, armor, health
    worth       the clusters soaking up its pollution first (they are the ones that attack it), then the
                nearest

It goes when its strength clears theirs by SAFETY, it has turrets to creep with (but against a cluster with no
worms), the cluster is within reach and its home was not hit lately; with a polluting cluster nearly within
its strength, it arms up toward it. Every number is a weight to be learned from how the attacks go: each
decision is written down (`record`) with what it saw.

The game's own odds come first where it sends them (a cluster's `odds`, mod/scripts/combat_odds.lua): the
biters there and what its spawners send out while attacked, read from the game data (their health, bites and
resistances: plain magazines do about 1 damage to a medium biter), against its gun, its turrets and its health
through its armor. It cannot walk away from that fight (small biters outrun it: 0.24 against 0.15, O-2), so it
goes only at ODDS_GO; the weights above stand in where the game sent no odds.

What it can afford to lose (O-5, the user's own play: "too risky with no ammo upgrades; if I had more turrets and more
ammo to spare I would have went for it. I would lose turrets doing so, but it wouldnt be a big deal since my iron
output was so high"): a won fight still costs turrets and magazines. The game's odds give the share of its side it
loses and the rounds it spends (research upgrades are in them); its war chest (`war_chest`) gives the turrets and
magazines it has beyond what home keeps, and its iron income. The expected cost in plates must fit in what it can
spare plus REPLACE_MINUTES of income. A rich engineer goes at lower odds (AFFORD_ODDS, losses within AFFORD_SHARE of
what it can afford), takes along as many turrets as it can spare, and creeps; a poor one waits and stocks up.

What an attack is worth: a cluster soaking up its pollution sends the waves at its base; one that soaks up
none is worth the trip only near enough to spread onto its ground (EXPANSION_REACH). How its attacks went
(the game's `attacks_made`): a cluster it attacked and did not clear is not tried again until it is clearly
stronger than it was then (IMPROVE), or a long while has passed. The same small nest far off, soaking up
nothing, was attacked four times running in run 19 (2026-10-25), each time with empty creep turrets.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

SPAWNER = 6.0                 # a spawner's strength (the biters it sends at whoever attacks it)
WORM = {"small": 2.0, "medium": 5.0, "big": 12.0, "behemoth": 30.0}
EVOLUTION_SCALE = 3.0         # their strength grows by this much at full evolution
AMMO = {"magazines": 0.1, "piercing": 0.2, "uranium": 0.4}   # strength per magazine it carries, by kind
TURRET = 3.0                  # a turret to creep up with
ARMOR = {"light-armor": 2.0, "heavy-armor": 5.0, "modular-armor": 8.0, "power-armor": 15.0, "power-armor-mk2": 25.0}
SAFETY = 1.5                  # its strength over theirs to go
GETTING_CLOSE = 0.7           # below SAFETY but this close, with a polluting cluster: it arms up toward it
ODDS_GO = 1.2                 # the game's odds of the fight there to go (combat_odds.lua ENGAGE)
ODDS_ARMING = 0.5             # below ODDS_GO but this close, with a polluting cluster: it arms up toward it
AFFORD_ODDS = 1.0             # a fight it wins at these odds it takes on when its losses are small beside its means
AFFORD_SHARE = 0.5            # ... within this share of what it can afford to lose
REPLACE_MINUTES = 10          # its income for this long counts as what it can afford to lose
TURRET_PLATES = 50            # plates in a gun turret (10 gears, 10 copper, 20 iron)
MAGAZINE_PLATES = 4           # in a magazine
ROUNDS_PER_MAGAZINE = 10
FROM_IT = 64                  # an attack on its base that came from this near a cluster came from it
SHELL_PLATES = 30             # in an artillery shell (explosives, a radar's worth, steel): a weight
SHELLS_EACH = 2               # shells a nest or worm takes (the first often misses a little)
ARTILLERY_GUARDS = 3          # turrets it puts round its artillery (agent.lua ART_GUARD)
CHEAP_SHARE = 0.25            # a quiet cluster within MAX_TRIP is cleared when it costs no more than this of its means
EVO_PER_SPAWNER = 0.002       # evolution a destroyed spawner adds, times (1 - evolution) (vanilla's destroy factor)
HOME_HOLDS = 4                # loaded turrets round home: it can strike back at the nests its waves come from
MAX_TRIP = 600                # tiles: farther clusters wait for vehicles (and for their time)
MIN_TURRETS = 2               # creeping takes two turrets
TOUR_MAX = 8                  # stops after the first on one campaign (agent.lua Agent.TOUR_MAX)
RING_EACH = 3                 # creep turrets for each nest and worm of a group (O-9: the user's rings, 34-37 on 10-12)
RING_MAX = 40                 # ... and at most (agent.lua Agent.CREEP_MAX)
FILL_EACH = 10                # magazines it brings for each of them at least
CALM = 300                    # seconds since its base was last hit before it leaves to attack
EXPANSION_REACH = 200         # tiles: a cluster soaking up none of its pollution is worth attacking this near
SAME_CLUSTER = 64             # tiles: an attack this near a cluster was an attack on it
IMPROVE = 1.3                 # after an attack that did not clear it, this much stronger than then to try again
RETRY_TICKS = 216000          # (or this long after: an hour)
LOG_NAME = "offense.jsonl"


def their_strength(cluster: Mapping[str, Any], evolution: float) -> float:
    worms = cluster.get("worms") if isinstance(cluster.get("worms"), Mapping) else {}
    base = SPAWNER * float(cluster.get("spawners") or 0) + sum(WORM.get(str(k), WORM["small"]) * float(v or 0)
                                                              for k, v in worms.items())
    return base * (1.0 + EVOLUTION_SCALE * max(0.0, min(1.0, evolution)))


def our_strength(armed: Mapping[str, Any]) -> float:
    if not armed.get("guns"):
        return 0.0   # (nothing to shoot with)
    total = sum(AMMO[k] * float(armed.get(k) or 0) for k in AMMO)
    total += TURRET * float(armed.get("turrets") or 0)
    total += ARMOR.get(str(armed.get("armor") or ""), 0.0)
    health, top = armed.get("health"), armed.get("max_health")
    if isinstance(health, (int, float)) and isinstance(top, (int, float)) and top > 0:
        total *= max(0.25, health / top)
    return total


def assess(situation: Mapping[str, Any]) -> list[dict]:
    """Every known nest cluster with theirs, ours, the ratio and why it is worth taking, worth first."""
    armed = situation.get("armed") if isinstance(situation.get("armed"), Mapping) else {}
    threat = situation.get("threat") if isinstance(situation.get("threat"), Mapping) else {}
    evolution = float(threat.get("evolution") or 0)
    ours = our_strength(armed)
    out = []
    for c in situation.get("nests") or []:
        if not isinstance(c, Mapping) or "x" not in c:
            continue
        theirs = their_strength(c, evolution)
        row = {"x": c["x"], "y": c["y"], "distance": float(c.get("distance") or 0),
               **{k: c[k] for k in ("cloud", "near_cloud", "seen", "source") if k in c},
               "spawners": int(c.get("spawners") or 0), "worms": dict(c.get("worms") or {}),
               "pollution": float(c.get("pollution") or 0), "theirs": round(theirs, 1), "ours": round(ours, 1),
               "ratio": round(ours / theirs, 2) if theirs > 0 else None, "grounded": False}
        odds = c.get("odds") if isinstance(c.get("odds"), Mapping) else None
        if odds and isinstance(odds.get("ratio"), (int, float)):
            # the game's odds of the fight there (what is there and comes out, against what it could bring)
            row.update(ratio=round(float(odds["ratio"]), 2), grounded=True,
                       defenders=int(odds.get("defenders") or 0), reinforcements=int(odds.get("reinforcements") or 0),
                       turrets=int(odds.get("turrets") or 0), lost=float(1 if odds.get("lost") is None else odds["lost"]),
                       ratio_far=odds.get("ratio_far"), lost_far=odds.get("lost_far"),
                       rounds_needed=int(odds.get("rounds_needed") or 0))
            row["cost"] = cost(row, situation)
        row["attacks"] = attacks_from(situation, row["x"], row["y"])
        out.append(row)
    out.sort(key=lambda c: (-(c["pollution"] > 0 or c["attacks"] > 0), -c["attacks"], c["distance"]))
    return out


def attacks_from(situation: Mapping[str, Any], x: float, y: float) -> int:
    """How many of the attacks on its base lately came from near this cluster (where the wave was first seen): the
    game's `attack_sources`, else the threat's attacks."""
    n = 0
    threat = situation.get("threat") if isinstance(situation.get("threat"), Mapping) else {}
    listed = situation.get("attack_sources") or [a.get("from") | {"count": a.get("count")} for a in threat.get("attacks") or []
                                                if isinstance(a, Mapping) and isinstance(a.get("from"), Mapping)]
    for source in listed:
        if isinstance(source, Mapping) and "x" in source and math.hypot(float(source["x"]) - x, float(source["y"]) - y) <= FROM_IT:
            n += int(source.get("count") or 1)
    return n


def price(situation: Mapping[str, Any] | None, what: str, default: float) -> float:
    """Plates in its turret, a magazine of its ammo, or a shell, from the game's recipes (war_chest.prices), else the
    vanilla weight."""
    chest = situation.get("war_chest") if isinstance(situation, Mapping) and isinstance(situation.get("war_chest"), Mapping) else {}
    prices = chest.get("prices") if isinstance(chest.get("prices"), Mapping) else {}
    value = prices.get(what)
    return float(value) if isinstance(value, (int, float)) and value > 0 else default


def cost(c: Mapping[str, Any], situation: Mapping[str, Any] | None = None) -> float:
    """What a won fight there costs it in plates: the turrets it loses (its turrets take the bites first) and the
    magazines it spends."""
    lost = 1.0 if c.get("lost") is None else float(c["lost"])   # (0 for a nest with none to fight back: no loss)
    turrets_lost = min(float(c.get("turrets") or 0), lost * float(c.get("turrets") or 0) + (0.5 if lost > 0 else 0))
    magazines = float(c.get("rounds_needed") or 0) / ROUNDS_PER_MAGAZINE
    return round(turrets_lost * price(situation, "turret", TURRET_PLATES)
                 + magazines * price(situation, "magazine", MAGAZINE_PLATES), 1)


def artillery_cost(chest: Mapping[str, Any], c: Mapping[str, Any]) -> float | None:
    """What shelling the cluster costs, or None where it cannot: no artillery, too few shells, or too few turrets to
    guard it (the odds of what they send at it count those guards)."""
    art = chest.get("artillery") if isinstance(chest, Mapping) and isinstance(chest.get("artillery"), Mapping) else None
    if not (art and c.get("grounded")) or int(art.get("shells") or 0) < shells_for(c):
        return None
    if held_turrets(chest, {}) < ARTILLERY_GUARDS:
        return None
    lost = c.get("lost") if c.get("lost_far") is None else c["lost_far"]   # (from far out: what comes out at it, not the worms)
    lost = 1.0 if lost is None else float(lost)
    return round(shells_for(c) * float((chest.get("prices") or {}).get("shell") or SHELL_PLATES)
                 + ARTILLERY_GUARDS * lost * float((chest.get("prices") or {}).get("turret") or TURRET_PLATES), 1)


def held_turrets(chest: Mapping[str, Any], armed: Mapping[str, Any]) -> int:
    """The creep kit's turrets it holds: the war chest's count, else (older data) what it carries and its spare."""
    if isinstance(chest, Mapping) and "turrets" in chest:
        return int(chest.get("turrets") or 0)
    return int(armed.get("turrets") or 0) + int((chest or {}).get("spare_turrets") or 0)


def shells_for(c: Mapping[str, Any]) -> int:
    """Artillery shells to clear a cluster: SHELLS_EACH for each spawner and worm."""
    return SHELLS_EACH * (int(c.get("spawners") or 0) + sum(int(v or 0) for v in (c.get("worms") or {}).values()))


def means(situation: Mapping[str, Any]) -> float | None:
    """What it can afford to lose, in plates: its spare turrets and magazines, and REPLACE_MINUTES of iron income.
    None where the game sent no war chest."""
    chest = situation.get("war_chest") if isinstance(situation.get("war_chest"), Mapping) else None
    if not chest:
        return None
    spare = (float(chest.get("spare_turrets") or 0) * price(situation, "turret", TURRET_PLATES)
             # (mixed grades: spare magazines valued as plain ones at most, never as its best ammo)
             + float(chest.get("spare_magazines") or 0) * min(MAGAZINE_PLATES, price(situation, "magazine", MAGAZINE_PLATES)))
    # (and the shells it holds: every supply an attack spends is part of what it can spend, O-8: it priced the shells
    # an artillery attack used but not the 48 in its store, and armed up instead of shelling)
    art = chest.get("artillery") if isinstance(chest.get("artillery"), Mapping) else {}
    spare += float(art.get("shells") or 0) * price(situation, "shell", SHELL_PLATES)
    return round(spare + float(chest.get("iron_per_min") or 0) * REPLACE_MINUTES, 1)


def calm_at_home(situation: Mapping[str, Any]) -> bool:
    last = ((situation.get("threat") or {}).get("last_attack") or {}) if isinstance(situation.get("threat"), Mapping) else {}
    ago = last.get("seconds_ago") if isinstance(last, Mapping) else None
    return not isinstance(ago, (int, float)) or ago >= CALM


def worth(cluster: Mapping[str, Any]) -> bool:
    """Whether taking it out is worth a trip from its factory: it soaks up its pollution or its attacks came from
    there (so its waves come at the base), or it is near enough to spread onto its ground. Where the game marks the
    cloud (O-9, the user's clear: every group in or near the pollution cloud, not the whole map), a group in or by the
    cloud, or one attacks come from."""
    if "cloud" in cluster or "near_cloud" in cluster or "source" in cluster:
        return in_targets(cluster)
    return (float(cluster.get("pollution") or 0) > 0 or int(cluster.get("attacks") or 0) > 0
            or float(cluster.get("distance") or 0) <= EXPANSION_REACH)


def in_targets(cluster: Mapping[str, Any]) -> bool:
    """A target of the clear (O-9): the cloud reaches it or the chunk beside it, or attacks on its base came from it."""
    return (float(cluster.get("cloud") or 0) > 0 or bool(cluster.get("near_cloud")) or bool(cluster.get("source"))
            or int(cluster.get("attacks") or 0) > 0)


def evolution_cost(clusters: list[Mapping[str, Any]], evolution: float) -> float:
    """What clearing them adds to the biters' evolution: each spawner destroyed adds EVO_PER_SPAWNER × (1 - evolution)
    (the user's clear: 51 spawners, +0.086 in 25 minutes, more than the 61 minutes of play before)."""
    return round(sum(int(c.get("spawners") or 0) for c in clusters) * EVO_PER_SPAWNER * (1 - max(0.0, min(1.0, evolution))), 4)


def targets(situation: Mapping[str, Any]) -> dict:
    """The clear's target set (O-9): every known group in or by its cloud, or that attacks came from; "done" when
    none is left. With its evolution cost."""
    rows = [c for c in assess(situation) if in_targets(c)]
    evolution = float(((situation.get("threat") or {}) if isinstance(situation.get("threat"), Mapping) else {}).get("evolution") or 0)
    return {"targets": rows, "done": not rows, "evolution_cost": evolution_cost(rows, evolution)}


def pressing(cluster: Mapping[str, Any]) -> bool:
    """The one that matters: it soaks up its pollution, or its attacks came from there."""
    return float(cluster.get("pollution") or 0) > 0 or int(cluster.get("attacks") or 0) > 0


def tried(situation: Mapping[str, Any], x: float, y: float) -> dict | None:
    """Its latest attack on the cluster at (x, y) that did not clear it, if it is not clearly stronger now than
    it was then and it was not long ago: the reason not to try again yet."""
    tick = situation.get("tick")
    ours = our_strength(situation.get("armed") if isinstance(situation.get("armed"), Mapping) else {})
    last = None
    for a in situation.get("attacks_made") or []:
        if (isinstance(a, Mapping) and "x" in a and a.get("outcome") != "cleared"
                and math.hypot(float(a["x"]) - float(x), float(a["y"]) - float(y)) <= SAME_CLUSTER):
            if last is None or float(a.get("tick") or 0) >= float(last.get("tick") or 0):
                last = dict(a)
    if last is None:
        return None
    if isinstance(tick, (int, float)) and isinstance(last.get("tick"), (int, float)) \
            and tick - last["tick"] >= RETRY_TICKS:
        return None
    then = our_strength(last["armed"]) if isinstance(last.get("armed"), Mapping) else 0.0
    if then > 0 and ours >= then * IMPROVE:
        return None
    last["ours_then"], last["ours_now"] = round(then, 1), round(ours, 1)
    return last


def choose(situation: Mapping[str, Any]) -> tuple[str, dict] | None:
    """("attack", cluster) when it is strong enough for the first worth taking, ("arm_up", cluster) when it is
    nearly there against one soaking up its pollution, else None."""
    armed = situation.get("armed") if isinstance(situation.get("armed"), Mapping) else {}
    # (O-7: a base under waves was never calm, so it never struck back at the nests sending them; with home holding
    # without it, it goes for those, and only those, meanwhile)
    calm = calm_at_home(situation)
    chest_all = situation.get("war_chest") if isinstance(situation.get("war_chest"), Mapping) else {}
    holds = int(chest_all.get("home_turrets") or 0) >= HOME_HOLDS
    if not calm and not holds:
        return None
    budget_all = means(situation)
    short = None   # (the first that matters, short of its ring: armed up for if no smaller group goes first)
    for c in assess(situation):
        if not calm and not c["attacks"]:
            continue
        # (rich enough, a quiet cluster within its reach is cleared while it is cheap, by shells or turrets: late
        # game it has the means)
        art_cost = artillery_cost(chest_all, c)
        cheapest = min(x for x in (c.get("cost"), art_cost) if x is not None) if (c.get("cost") is not None or art_cost is not None) else None
        cheap_clear = (c["grounded"] and budget_all is not None and cheapest is not None
                       and cheapest <= CHEAP_SHARE * budget_all
                       and ("cloud" not in c and "near_cloud" not in c and "source" not in c or in_targets(c)))
        if c["distance"] > MAX_TRIP or c["ratio"] is None or not (worth(c) or cheap_clear):
            continue
        if tried(situation, c["x"], c["y"]):
            if pressing(c):
                return "arm_up", (short or c)   # (the one that matters held last time: stronger first)
            continue
        chest = situation.get("war_chest") if isinstance(situation.get("war_chest"), Mapping) else {}
        held = held_turrets(chest, armed)   # (its creep kit's turret)
        can_creep = held >= MIN_TURRETS
        creeps = can_creep or not c["worms"]
        go, close = (ODDS_GO, ODDS_ARMING) if c["grounded"] else (SAFETY, GETTING_CLOSE)
        budget = means(situation)
        if c["grounded"] and budget is not None:
            # what it can afford to lose: a won fight that costs more than its means waits; a cheap one goes at lower odds
            c["means"] = budget
            affordable = c.get("cost", 0) <= budget
            cheap = c.get("cost", 0) <= AFFORD_SHARE * budget
            # artillery (late game, O-7): shells from far out of their reach; it fights there only what they send at
            # it, beside its guard turrets, so the same odds of the fight hold
            art_cost = artillery_cost(chest, c)
            far = c["ratio"] if c.get("ratio_far") is None else c["ratio_far"]   # (from far out the worms do not reach it)
            if art_cost is not None and art_cost <= budget and far is not None and far >= AFFORD_ODDS:
                c["method"], c["shells"], c["cost"] = "artillery", shells_for(c), art_cost
                return "attack", c
            if (c["worms"] or c["spawners"]) and float(c.get("lost") or 0) > 0:
                # (O-9: a full ring or not at all; short of one, a smaller group, else its turret maker first; a group
                # with nothing left to fight back takes none)
                ring = ring_for(c)
                if held < ring:
                    c["ring_short"] = ring - held
                    if pressing(c) and short is None:
                        short = c
                    continue
            if creeps and affordable and (c["ratio"] >= go or (cheap and c["ratio"] >= AFFORD_ODDS)):
                return "attack", c
            if pressing(c) and c["ratio"] >= close:
                return "arm_up", (short or c)   # (stocking up: turrets and magazines to spare, or the upgrades)
            if pressing(c):
                return ("arm_up", short) if short else None
            continue
        if c["ratio"] >= go and creeps:
            return "attack", c
        if pressing(c) and c["ratio"] >= close:
            return "arm_up", (short or c)
        if pressing(c):
            return ("arm_up", short) if short else None   # (the one that matters is out of reach for now: not some other one instead)
    return ("arm_up", short) if short else None


def ring_for(c: Mapping[str, Any]) -> int:
    """The creep ring a group takes (O-9, the user's clear): RING_EACH turrets for each nest and worm, up to RING_MAX,
    or the turrets the odds counted if more. Its thin rings of 2-3 were where it got hurt."""
    structures = int(c.get("spawners") or 0) + sum(int(v or 0) for v in (c.get("worms") or {}).values())
    return min(RING_MAX, max(MIN_TURRETS, int(c.get("turrets") or MIN_TURRETS), RING_EACH * structures))


def kit_for(c: Mapping[str, Any]) -> dict:
    """What it takes along for a group: its ring of turrets, the magazines for the fight with a margin, and enough
    to fill the ring."""
    ring = ring_for(c)
    return {"turrets": ring,
            "magazines": max(20, FILL_EACH * ring, int(1.5 * float(c.get("rounds_needed") or 0) / ROUNDS_PER_MAGAZINE) + 10)}


def tour(situation: Mapping[str, Any], first: Mapping[str, Any]) -> list[dict]:
    """The groups after `first` on one campaign (O-9, the user's clear: one loop round the base, home only to
    restock): every other target it would win standing (by creeping) and can afford together within its means, in
    order round its home from `first`, whichever way round is shorter."""
    home = situation.get("home") if isinstance(situation.get("home"), Mapping) else None
    if not home or not first.get("grounded"):
        return []
    budget = means(situation)
    spent = float(first.get("cost") or 0)
    chest = situation.get("war_chest") if isinstance(situation.get("war_chest"), Mapping) else {}
    armed = situation.get("armed") if isinstance(situation.get("armed"), Mapping) else {}
    creeps = held_turrets(chest, armed) >= MIN_TURRETS
    calm = calm_at_home(situation)
    picked = []
    for c in assess(situation):
        if (c["x"], c["y"]) == (first["x"], first["y"]) or not in_targets(c) or not c["grounded"]:
            continue
        if not calm and not c["attacks"]:
            continue   # (under waves, as choose: only the groups they come from)
        if c["distance"] > MAX_TRIP or c["ratio"] is None or c["ratio"] < ODDS_GO or not (creeps or not c["worms"]):
            continue
        if tried(situation, c["x"], c["y"]):
            continue
        if ring_for(c) > held_turrets(chest, armed):
            continue   # (its ring would be thin: O-9)
        cost = float(c.get("cost") or 0)
        if budget is not None and spent + cost > budget:
            continue
        spent += cost
        picked.append(c)
    if not picked:
        return []
    hx, hy = float(home["x"]), float(home["y"])
    start = math.atan2(first["y"] - hy, first["x"] - hx)

    def turn(c, way):
        return (way * (math.atan2(c["y"] - hy, c["x"] - hx) - start)) % (2 * math.pi)

    def length(order):   # (and back home at the end)
        path, at = 0.0, first
        for c in order[:TOUR_MAX]:
            path += math.hypot(c["x"] - at["x"], c["y"] - at["y"])
            at = c
        return path + math.hypot(at["x"] - hx, at["y"] - hy)

    ways = [sorted(picked, key=lambda c, w=w: turn(c, w)) for w in (1, -1)]
    order = min(ways, key=length)[:TOUR_MAX]
    return [{"x": c["x"], "y": c["y"], **kit_for(c)} for c in order]


def intentions(request: Mapping[str, Any]) -> list[dict]:
    """What its offense calls for now, as intentions for the game."""
    situation = request.get("situation") if isinstance(request.get("situation"), Mapping) else {}
    picked = choose(situation)
    if not picked:
        return []
    what, c = picked
    why = (f"{c['spawners']} spawners, {sum(c['worms'].values())} worms"
           + (f", soaking up {c['pollution']:.0f} pollution" if c["pollution"] else "") + "; " + comparison(c))
    if what == "attack":
        args = {"target": "nests", "x": c["x"], "y": c["y"], "minutes": 10}
        if c.get("grounded"):
            # what it takes along: the turrets the odds counted, and the magazines for the fight with a margin
            args.update(kit_for(c))
            if c.get("method"):
                args["method"] = c["method"]
            if c.get("method") == "artillery":
                args["shells"] = int(c.get("shells") or shells_for(c))
            if c.get("method") != "artillery":
                stops = tour(situation, c)
                if stops:
                    args["tour"] = stops
        out = [{"tool": "attack", "priority": "next", "reason": f"taking out the nests at ({c['x']}, {c['y']}): {why}",
                "args": args}]
    else:
        args = {"focus": "all", "ammo": 200}
        if c.get("ring_short"):
            args["turrets"] = ring_for(c)   # (the ring it takes: O-9)
            why += f"; a ring of {ring_for(c)} turrets, {c['ring_short']} short"
        out = [{"tool": "arm_up", "priority": "next", "reason": f"arming up to take out the nests at "
                f"({c['x']}, {c['y']}): {why}", "args": args}]
    record(request, picked, out)
    return out


def comparison(c: Mapping[str, Any]) -> str:
    """How it stands against a cluster: the game's odds of the fight there where it has them, else the weights."""
    if c.get("grounded"):
        line = (f"odds {c['ratio']} ({ODDS_GO} or better to go; {c.get('defenders', 0)} biters there, "
                f"{c.get('reinforcements', 0)} more come out)")
        if "cost" in c:
            line += f", costing about {c['cost']:.0f} plates" + (f" of the {c['means']:.0f} you can spare" if "means" in c else "")
        return line
    return f"strength {c['ours']} against {c['theirs']}"


def describe(situation: Mapping[str, Any]) -> str:
    """One line for its strategist: the clusters worth taking and how its strength compares."""
    rows = assess(situation)[:3]
    if not rows:
        return ""
    parts = [f"({c['x']}, {c['y']}) {c['distance']:.0f} tiles: {c['spawners']} spawners, {sum(c['worms'].values())} "
             f"worms{', soaking up your pollution' if c['pollution'] else ''}; your " + comparison(c) for c in rows]
    return ("Nest clusters near you (take one out only when you would win the fight there standing: biters outrun "
            "you, so you cannot leave it; the ones soaking up your pollution first; turrets to creep with count): "
            + " | ".join(parts))


def screened(request: Mapping[str, Any], decided: list[dict]) -> list[dict]:
    """The strategist's attacks on nests it attacked lately and did not clear are held back until it is clearly
    stronger (arming up instead): from its own memory of how they went, whoever orders them."""
    situation = request.get("situation") if isinstance(request.get("situation"), Mapping) else {}
    out = []
    for i in decided:
        args = i.get("args") if isinstance(i, Mapping) and isinstance(i.get("args"), Mapping) else {}
        if (i.get("tool") == "attack" and args.get("target") == "nests" and "x" in args and "y" in args
                and tried(situation, args["x"], args["y"])):
            out.append({"tool": "arm_up", "priority": i.get("priority") or "next",
                        "reason": f"not ready for the nests at ({args['x']}, {args['y']}) yet: the last attack there "
                                  "did not clear them", "args": {"focus": "all", "ammo": 200}})
            continue
        out.append(i)
    return out


def with_reflex(request: Mapping[str, Any], decided: list[dict]) -> list[dict]:
    """The strategist's decision, with an attack (or arming up for one) added when its offense calls for one
    and neither the decision nor its agenda has one: the engineer's own judgment, whatever the model said.
    Its attacks on nests it did not clear last time wait until it is stronger (`screened`)."""
    decided = screened(request, decided)
    if request.get("reason") not in ("routine", "initiative"):
        return decided
    fighting = {"attack", "arm_up"}
    if any(isinstance(i, Mapping) and i.get("tool") in fighting for i in decided):
        return decided
    agenda = ((request.get("situation") or {}).get("agenda") or []) if isinstance(request.get("situation"), Mapping) else []
    if any(isinstance(i, Mapping) and i.get("tool") in fighting for i in agenda):
        return decided
    return list(decided) + intentions(request)


_LOG_LOCK = threading.Lock()


def record(request: Mapping[str, Any], picked: tuple[str, dict], out: list[dict]) -> None:
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    situation = request.get("situation") if isinstance(request.get("situation"), Mapping) else {}
    row = {"time": time.time(), "npc": request.get("npc_id"), "request": request.get("request_id"),
           "armed": situation.get("armed"), "evolution": (situation.get("threat") or {}).get("evolution")
           if isinstance(situation.get("threat"), Mapping) else None,
           "clusters": assess(situation), "attacks_made": situation.get("attacks_made"),
           "chose": picked[0], "cluster": picked[1], "intentions": out}
    path = Path(folder) / "decisions" / LOG_NAME
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
