"""The team world's building rules, for the planners. An engineer builds in its own plot or out beyond
the team area (an outpost past the wall), never in another plot (a teammate's, the players', the team
yard), never across a plot's edge, and never within 32 tiles of what a player built. The game sends the
rules with the site (local tiles) and the map dump (world tiles) as `team = {plot, area, no_build}`, and
checks every plan against them again; nothing is sent outside the team world, and then nothing here
limits anything."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

Rect = tuple[float, float, float, float]   # x0, y0, x1, y1: tile edges


@dataclass(frozen=True)
class Team:
    plot: Rect | None
    area: Rect
    no_build: tuple[Rect, ...]
    bands: tuple[Rect, ...] = ()   # the team yard's outer edges this engineer holds (its wall line runs on)

    def box_ok(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        """An entity's box: wholly in the plot or wholly outside the team area, and clear of every
        no-build zone (the game's rule exactly)."""
        p, a = self.plot, self.area
        own = any(r is not None and x0 >= r[0] and y0 >= r[1] and x1 <= r[2] and y1 <= r[3]
                  for r in (p,) + self.bands)
        apart = x1 <= a[0] or y1 <= a[1] or x0 >= a[2] or y0 >= a[3]
        if not (own or apart):
            return False
        return not any(x0 < r[2] and x1 > r[0] and y0 < r[3] and y1 > r[1] for r in self.no_build)

    def tile_ok(self, x: int, y: int) -> bool:
        return self.box_ok(x, y, x + 1, y + 1)

    def others(self) -> list[Rect]:
        """The team area outside the plot: four strips round it (its bands of the yard lie in them)."""
        a, p = self.area, self.plot
        if p is None:
            out = [a]
        else:
            out = [(a[0], a[1], a[2], p[1]), (a[0], p[3], a[2], a[3]),
                   (a[0], p[1], p[0], p[3]), (p[2], p[1], a[2], p[3])]
        return [r for r in out if r[2] > r[0] and r[3] > r[1]]

    def forbidden_rects(self) -> list[Rect]:
        """The team area outside the plot and the no-build zones (bands left in: a designer's site
        window is never out at the yard's edge)."""
        return self.others() + list(self.no_build)

    def forbidden_tiles(self, within: Rect) -> set[tuple[int, int]]:
        """Every tile in `within` an engineer may not build on (for a planner's blocked set)."""
        wx0, wy0, wx1, wy1 = within

        def tiles(rects: list[Rect] | tuple[Rect, ...]) -> set[tuple[int, int]]:
            out: set[tuple[int, int]] = set()
            for r in rects:
                x0, y0 = math.floor(max(r[0], wx0)), math.floor(max(r[1], wy0))
                x1, y1 = math.ceil(min(r[2], wx1)), math.ceil(min(r[3], wy1))
                for x in range(x0, x1):
                    out.update((x, y) for y in range(y0, y1))
            return out
        return (tiles(self.others()) - tiles(self.bands)) | tiles(self.no_build)


def window_for(team: Team, x: float, y: float, half: float = 32, keep: float = 24) -> tuple[float, float] | None:
    """The centre of a site window (2*half across) on the engineer's own ground that still has (x, y) within
    `keep` of its centre: round the point itself, or slid inward to fit its plot or one of its bands; None
    when none does (the point is someone else's, or the ground round it is)."""
    candidates = [(x, y)]
    for r in ((team.plot,) if team.plot else ()) + team.bands:
        if r[2] - r[0] >= 2 * half and r[3] - r[1] >= 2 * half:
            candidates.append((min(max(x, r[0] + half), r[2] - half), min(max(y, r[1] + half), r[3] - half)))
    # (an outpost past the wall: the site wholly outside the team area, on whichever side is nearest)
    a = team.area
    candidates += [(min(x, a[0] - half), y), (max(x, a[2] + half), y), (x, min(y, a[1] - half)), (x, max(y, a[3] + half))]
    for cx, cy in candidates:
        if abs(cx - x) <= keep and abs(cy - y) <= keep and team.box_ok(cx - half, cy - half, cx + half, cy + half):
            return cx, cy
    return None


def _rect(r: Any) -> Rect | None:
    if isinstance(r, (list, tuple)) and len(r) == 4 and all(isinstance(v, (int, float)) for v in r):
        return (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
    return None


def team_of(obj: Mapping[str, Any] | None) -> Team | None:
    """The rules a site or map dump carries (in its own frame), or None outside the team world."""
    t = obj.get("team") if isinstance(obj, Mapping) else None
    area = _rect(t.get("area")) if isinstance(t, Mapping) else None
    if area is None:
        return None
    zones = t.get("no_build")
    zones = zones if isinstance(zones, list) else []
    bands = t.get("bands") if isinstance(t.get("bands"), list) else []
    return Team(plot=_rect(t.get("plot")), area=area,
                no_build=tuple(r for r in (_rect(z) for z in zones) if r is not None),
                bands=tuple(r for r in (_rect(b.get("box")) for b in bands if isinstance(b, Mapping)) if r is not None))


def death_world(obj: Mapping[str, Any] | None) -> bool:
    """Whether the save maker made the team world a death world: in a site's or map dump's team table, else at the top
    (a research request's or the strategist's situation, whose `team` is only true)."""
    if not isinstance(obj, Mapping):
        return False
    t = obj.get("team")
    return bool(t.get("death_world") if isinstance(t, Mapping) else obj.get("death_world"))


def forbidden(dump: Mapping[str, Any]) -> set[tuple[int, int]]:
    """The tiles of a map dump the engineer may not build on (worked out once per dump)."""
    team = team_of(dump)
    if team is None:
        return set()
    cached = dump.get("_team_forbidden") if isinstance(dump, dict) else None
    if cached is None:
        area = dump.get("area") or team.area
        cached = team.forbidden_tiles(tuple(area))
        if isinstance(dump, dict):
            dump["_team_forbidden"] = cached
    return cached


def site_forbidden(site: Mapping[str, Any]) -> set[tuple[int, int]]:
    """The same for a site window (local tiles, within its bounds)."""
    team = team_of(site)
    if team is None:
        return set()
    bounds = site.get("bounds") or (0, 0, 64, 64)
    return team.forbidden_tiles(tuple(bounds))


def pumps(dump: Mapping[str, Any]) -> list[dict]:
    """The dump's offshore pump spots the engineer may use."""
    team = team_of(dump)
    spots = list(dump.get("pumps") or [])
    if team is None:
        return spots
    return [p for p in spots if team.box_ok(p["x"] - 1, p["y"] - 1, p["x"] + 1, p["y"] + 1)]


def misplaced(plan: Mapping[str, Any], site: Mapping[str, Any]) -> list[str]:
    """The entities of a finished plan (local tiles) the game would refuse under the team rules."""
    team = team_of(site)
    if team is None:
        return []
    facts = site.get("prototypes") if isinstance(site.get("prototypes"), Mapping) else {}
    return [str(e.get("id")) for e in plan.get("entities") or []
            if not fits(team, e.get("prototype"), e["position"]["x"], e["position"]["y"], e.get("direction") or 0,
                        facts)]


def fits(team: Any, name: Any, x: float, y: float, direction: int, facts: Mapping[str, Any] | None = None) -> bool:
    """Whether a building of `name` at (x, y) facing `direction` stands where the team rules let it (the same test
    misplaced makes of a finished plan, so a planner searching a site can use it and never pick one it refuses)."""
    if team is None:
        return True
    f = (facts or {}).get(name) or {}
    sw, sh = _size(name)
    w, h = f.get("width") or sw, f.get("height") or sh
    if (direction or 0) in (4, 12):
        w, h = h, w
    return team.box_ok(x - w / 2, y - h / 2, x + w / 2, y + h / 2)


# Sizes of the bigger buildings, for entities the site's prototype facts leave out: a side, or
# (width, height) facing north for the ones that are not square (the loaders, two tiles long).
SIZES = {"electric-mining-drill": 3, "assembling-machine-1": 3, "assembling-machine-2": 3,
         "assembling-machine-3": 3, "stone-furnace": 2, "steel-furnace": 2, "electric-furnace": 3, "lab": 3,
         "chemical-plant": 3, "oil-refinery": 5, "pumpjack": 3, "storage-tank": 3, "rocket-silo": 9,
         "gun-turret": 2, "radar": 3, "boiler": 3, "steam-engine": 5, "burner-mining-drill": 2,
         "laser-turret": 2, "flamethrower-turret": 3, "artillery-turret": 3,
         "loader": (1, 2), "fast-loader": (1, 2), "express-loader": (1, 2), "turbo-loader": (1, 2)}


def _size(name: Any) -> tuple[int, int]:
    size = SIZES.get(str(name), 1)
    return size if isinstance(size, tuple) else (size, size)
