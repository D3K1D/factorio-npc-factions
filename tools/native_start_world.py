"""Fresh-world settings for independent native faction starting areas."""
from __future__ import annotations

import math


def map_settings(seed: int, bots: int, spacing: int, starting_area: float,
                 *, no_biters: bool = False) -> dict:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
        raise ValueError("seed must be a 32-bit unsigned integer")
    if isinstance(bots, bool) or not isinstance(bots, int) or not 1 <= bots <= 10:
        raise ValueError("bots must be between 1 and 10")
    if isinstance(spacing, bool) or not isinstance(spacing, int) or not 256 <= spacing <= 4096:
        raise ValueError("native starting-area spacing must be 256..4096 tiles")
    if not math.isfinite(starting_area) or not 1 <= starting_area <= 6:
        raise ValueError("starting area must be between 1 and 6")
    # A camp may sit up to 180 tiles from its assigned native point. Leave
    # that margin on both sides, plus grid rounding, before promising spacing.
    separation = spacing + 384
    radius = max(1536, separation if bots <= 2 else separation / (2 * math.sin(math.pi / bots)))
    radius = math.ceil(radius / 32) * 32
    points = [{"x": 0, "y": 0}]
    for index in range(bots):
        angle = 2 * math.pi * index / bots
        points.append({"x": round(radius * math.cos(angle) / 32) * 32,
                       "y": round(radius * math.sin(angle) / 32) * 32})
    return {"seed": seed, "starting_points": points, "starting_area": starting_area,
            "cliff_settings": {"name": "cliff", "richness": 0},   # no cliffs for now (user, 2026-10-09)
            "no_enemies_mode": no_biters, "peaceful_mode": no_biters,
            "autoplace_controls": {"enemy-base": {"frequency": 0 if no_biters else 1,
                "size": 0 if no_biters else 1, "richness": 0 if no_biters else 1}}}
