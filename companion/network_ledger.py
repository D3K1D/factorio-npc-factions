"""The logistic network's items, kept like the lanes (companion/lane_ledger.py): for each item robots build with, what
the network holds (stock), what the engineer should keep on hand for what its growth will place (target), and the
shortfall. The mall makes what is short (the user, 2026-10-29: "setup roboports and the correct chests and items for
them, automate inserters etc for robot use to expand factory automatically").

What it should keep is worked out from the base itself, not a fixed list of amounts: robots will place more of what the
base is made of, so every building it has built (each block's `pieces`, mod/scripts/base_memory.lua) sets a target of
GROWTH of its count, between a floor (enough for the next block of that kind) and a cap; robot-network buildings and the
robots themselves are sized by the network (its cells, its requester chests and the jobs waiting on its robots). A
build the planner is about to place claims its items on top (`claim`). Only what the engineer can make counts.

    book = NetworkBook.from_site(site)
    book.items()              # [Item]: stock, target, short, signal ("short", "low", "plenty")
    book.claim({"transport-belt": 120}, "bus-stage-3")
    book.mall_needs()         # [{item, target, stock, short, per_minute}]: what the mall should make, most short first
    remember(site)            # logged (data/decisions/network.jsonl) when the shortfalls change
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

GROWTH = 0.25          # robots place about this share of the base again before the mall can refill: kept on hand
REFILL_MINUTES = 10    # a shortfall made up within about this long (what the mall's rate is sized for)
LOW = 0.5              # under this share of its target an item is low (the mall works on it), under it all: short
# The least kept of what robots build with (one block's worth of each), and the most (a few stacks): the base's own
# make-up sets the rest. Buildings not listed here keep GROWTH of their count, between 1 and DEFAULT_CAP.
FLOOR = {"transport-belt": 200, "underground-belt": 20, "splitter": 10, "inserter": 50, "fast-inserter": 20,
         "long-handed-inserter": 10, "assembling-machine-1": 10, "assembling-machine-2": 10,
         "electric-mining-drill": 10, "stone-furnace": 10, "steel-furnace": 10, "electric-furnace": 10,
         "small-electric-pole": 50, "medium-electric-pole": 20, "big-electric-pole": 10, "pipe": 50,
         "pipe-to-ground": 10, "gun-turret": 10, "stone-wall": 200, "roboport": 4, "passive-provider-chest": 10,
         "requester-chest": 10, "storage-chest": 4, "iron-chest": 10}
CAP = {"transport-belt": 800, "stone-wall": 800, "pipe": 400, "small-electric-pole": 200, "inserter": 300}
DEFAULT_CAP = 100
# Robots: construction robots by the network's size and the jobs waiting on them, logistic by its chests.
ROBOTS_PER_CELL = 10   # construction robots a roboport's worth of ground keeps busy
ROBOTS_PER_GHOSTS = 4  # and one more for every so many buildings waiting to be built by them
ROBOT_CAP = 300
LOGISTIC_PER_REQUESTER = 4
LOGISTIC_BASE = 10
ROBOT_ITEMS = ("construction-robot", "logistic-robot")
# Repair packs its robots repair with (the user, 2026-10-30: late game, robots with repair packs repair walls and
# buildings in the roboports' construction areas): a floor, more for every wall and turret its networks cover, twice
# that while its base is attacked, at most a cap.
REPAIR = "repair-pack"
REPAIR_FLOOR = 50
REPAIR_PER_DEFENSE = 0.25
REPAIR_CAP = 400
LOG_NAME = "network.jsonl"


@dataclass
class Item:
    item: str
    stock: int
    target: int
    why: str
    claims: dict[str, int] = field(default_factory=dict)   # builds about to be placed: their items on top

    @property
    def wanted(self) -> int:
        return self.target + sum(self.claims.values())

    @property
    def short(self) -> int:
        return max(0, self.wanted - self.stock)

    @property
    def signal(self) -> str:
        if self.stock >= self.wanted:
            return "plenty"
        return "low" if self.stock >= LOW * self.wanted else "short"

    def row(self) -> dict:
        return {"item": self.item, "stock": self.stock, "target": self.wanted, "short": self.short,
                "signal": self.signal, "why": self.why}


def _count(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _makeable(site: Mapping[str, Any]) -> set[str] | None:
    """Recipe names the engineer can make (None: not told, every item counts)."""
    rows = site.get("recipes")
    if not isinstance(rows, list) or not rows:
        return None
    return {str(r.get("name")) for r in rows if isinstance(r, Mapping) and r.get("name")}


class NetworkBook:
    def __init__(self, site: Mapping[str, Any]):
        self.site = site
        self.networks = [n for n in site.get("logistics") or () if isinstance(n, Mapping)]
        self._items: dict[str, Item] = {}
        self._build()

    @classmethod
    def from_site(cls, site: Mapping[str, Any]) -> "NetworkBook":
        return cls(site)

    # --- what is held -------------------------------------------------------
    def stock(self) -> dict[str, int]:
        """What it holds for robots: its networks' contents; with no network yet, its chests' and its pockets'."""
        out: dict[str, int] = {}
        if self.networks:
            for net in self.networks:
                for item, n in (net.get("items") or {}).items():
                    out[str(item)] = out.get(str(item), 0) + _count(n)
                robots = net.get("robots") or {}
                out["construction-robot"] = out.get("construction-robot", 0) + _count(robots.get("construction"))
                out["logistic-robot"] = out.get("logistic-robot", 0) + _count(robots.get("logistic"))
                # (every kind of repair pack it holds, a mod's too, counted as repair packs)
                if net.get("repair_packs") is not None:
                    listed = _count((net.get("items") or {}).get(REPAIR))
                    out[REPAIR] = out.get(REPAIR, 0) - listed + _count(net.get("repair_packs"))
            return out
        from companion import handcraft
        # (robots count only in a network: in its pockets or a chest they build nothing; the robots stage held 60
        # in its pockets and put none in, 2026-10-29)
        return {item: int(n) for item, n in handcraft.held(self.site).items() if item not in ROBOT_ITEMS}

    def built(self) -> dict[str, int]:
        """Every building the base has, by name (each block's pieces)."""
        out: dict[str, int] = {}
        for block in self.site.get("base") or ():
            for name, n in ((block or {}).get("pieces") or {}).items():
                out[str(name)] = out.get(str(name), 0) + _count(n)
        return out

    # --- targets ------------------------------------------------------------
    def _build(self) -> None:
        # (nothing to keep for robots before there are any: no network and no construction robotics yet; a run from
        # green science logged every building as short of a stock no robot would use, 2026-10-29)
        if not self.networks and "construction-robotics" not in set(self.site.get("researched") or ()):
            return
        stock, built, makeable = self.stock(), self.built(), _makeable(self.site)

        def ok(item: str) -> bool:
            return makeable is None or item in makeable

        for name, n in sorted(built.items()):
            if not ok(name) or name in ROBOT_ITEMS:
                continue
            target = min(CAP.get(name, DEFAULT_CAP), max(FLOOR.get(name, 1), math.ceil(GROWTH * n)))
            self._items[name] = Item(name, stock.get(name, 0), target,
                                     f"{n} built: {GROWTH:.0%} of it kept for what robots place next")
        if self.networks or "construction-robotics" in set(self.site.get("researched") or ()):
            for name in ("roboport", "big-electric-pole", "storage-chest"):
                if ok(name) and name not in self._items:
                    self._items[name] = Item(name, stock.get(name, 0), FLOOR.get(name, 1),
                                             "the robot network grows with the base")
            cells = sum(len(n.get("cells") or ()) for n in self.networks)
            ghosts = sum(_count(n.get("ghosts")) for n in self.networks)
            if ok("construction-robot"):
                want = min(ROBOT_CAP, max(ROBOTS_PER_CELL, ROBOTS_PER_CELL * cells + math.ceil(ghosts / ROBOTS_PER_GHOSTS)))
                self._items["construction-robot"] = Item(
                    "construction-robot", stock.get("construction-robot", 0), want,
                    f"{cells} roboports and {ghosts} buildings waiting for robots")
            requesters = sum(_count((n.get("chests") or {}).get("requesters")) for n in self.networks)
            if ok("logistic-robot") and "logistic-robotics" in set(self.site.get("researched") or ()):
                want = min(ROBOT_CAP, LOGISTIC_BASE + LOGISTIC_PER_REQUESTER * requesters)
                self._items["logistic-robot"] = Item("logistic-robot", stock.get("logistic-robot", 0), want,
                                                     f"{requesters} requester chests")
                for name in ("passive-provider-chest", "requester-chest"):
                    if ok(name) and name not in self._items:
                        self._items[name] = Item(name, stock.get(name, 0), FLOOR.get(name, 1),
                                                 "chests for the mall's outputs and blocks' inputs")
            if self.networks and ok(REPAIR):
                defenses = sum(_count(n.get("defenses")) for n in self.networks)
                attacked = bool(self.site.get("under_attack"))
                want = min(REPAIR_CAP, REPAIR_FLOOR + math.ceil(REPAIR_PER_DEFENSE * defenses))
                if attacked:
                    want = min(REPAIR_CAP, 2 * want)
                self._items[REPAIR] = Item(REPAIR, stock.get(REPAIR, 0), want,
                                           f"{defenses} walls and turrets in its robots' reach"
                                           + (", its base under attack" if attacked else ""))
            # (a maker cell of the network's own asks the network for its inputs: they are wanted there too, for the
            # mall's providers to bring; companion/robo_grid.supply_plan)
            from companion import ratios
            from companion.robo_grid import REQUEST_CRAFTS, SUPPLY
            book = None
            for block in self.site.get("base") or ():
                if not (isinstance(block, Mapping) and block.get("purpose") == SUPPLY):
                    continue
                book = book or ratios.Book.from_site(self.site)
                for name in block.get("recipes") or ():
                    recipe = book.recipes.get(str(name))
                    for item, amount in (recipe.ingredients if recipe else ()):
                        if ok(item):
                            self.claim({item: int(round(amount * REQUEST_CRAFTS))}, f"{SUPPLY}:{name}")

    # --- reading it ---------------------------------------------------------
    def items(self) -> list[Item]:
        return sorted(self._items.values(), key=lambda i: (-i.short / max(1, i.wanted), i.item))

    def item(self, name: str) -> Item | None:
        return self._items.get(name)

    def claim(self, items: Mapping[str, int], key: str) -> None:
        """A build about to be placed: its items wanted on top of the targets (held for this book)."""
        for name, n in items.items():
            if _count(n) <= 0:
                continue
            entry = self._items.get(name)
            if entry is None:
                entry = self._items[name] = Item(name, self.stock().get(name, 0), 0, "a planned build")
            entry.claims[key] = entry.claims.get(key, 0) + _count(n)

    def mall_needs(self) -> list[dict]:
        """What the mall should make: every item under its target, most short first, with the rate that makes up the
        shortfall within REFILL_MINUTES."""
        return [{**i.row(), "per_minute": round(i.short / REFILL_MINUTES, 2)} for i in self.items() if i.short > 0]

    def summary(self, limit: int = 4) -> str | None:
        short = [i for i in self.items() if i.signal == "short"]
        if not short:
            return None
        return "Short for robots: " + ", ".join(f"{i.item} {i.stock} of {i.wanted}" for i in short[:limit])


# The latest shortfalls of each engineer: logged when they change.
_latest: dict[int, tuple] = {}
_lock = threading.Lock()


def remember(site: Mapping[str, Any]) -> NetworkBook | None:
    npc = site.get("npc_id")
    if not isinstance(npc, int):
        return None
    book = NetworkBook.from_site(site)
    needs = book.mall_needs()
    shape = tuple((n["item"], n["signal"], round(n["short"], -1)) for n in needs)
    with _lock:
        changed = _latest.get(npc) != shape
        _latest[npc] = shape
        if changed and (needs or book.networks):
            _log(site, book, needs)
    return book


def forget() -> None:
    with _lock:
        _latest.clear()


def _log(site: Mapping[str, Any], book: NetworkBook, needs: list[dict]) -> None:
    folder = os.environ.get("NPC_FACTIONS_DATA")
    if not folder:
        return
    row = {"time": time.time(), "tick": site.get("tick"), "npc": site.get("npc_id"), "request": site.get("request_id"),
           "networks": [{"id": n.get("id"), "cells": len(n.get("cells") or ()), "robots": n.get("robots"),
                         "chests": n.get("chests"), "ghosts": n.get("ghosts")} for n in book.networks][:8],
           "needs": needs[:32]}
    path = Path(folder) / "decisions" / LOG_NAME
    try:   # (called under _lock)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
