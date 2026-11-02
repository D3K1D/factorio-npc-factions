"""Ground the game handed back as unbuildable (a piece refused there, mod/scripts/obstacles.lua's Obstacles.avoid,
exported with a plan request as `avoid`): every planner keeps off it, so a layout is put elsewhere rather than offered
on the same spot again (a saved lab layout was refused three times at one spot, the proving ground 2026-10-28)."""
from __future__ import annotations

from typing import Any, Mapping


def rects(site: Mapping[str, Any]) -> list[tuple[int, int, int, int]]:
    """The boxes as half-open tile rects (x0, y0, x1, y1), local tiles; malformed entries left out."""
    out = []
    for box in site.get("avoid") or []:
        if isinstance(box, Mapping) and all(isinstance(box.get(k), (int, float)) for k in ("x0", "y0", "x1", "y1")):
            out.append((int(box["x0"]), int(box["y0"]), int(box["x1"]) + 1, int(box["y1"]) + 1))
    return out


def tiles(site: Mapping[str, Any]) -> set[tuple[int, int]]:
    return {(x, y) for x0, y0, x1, y1 in rects(site) for x in range(x0, x1) for y in range(y0, y1)}
