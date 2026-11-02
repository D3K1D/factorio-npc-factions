-- Ghosts, the one way the mod puts them down or clears them (W-35). A ghost the engine finds on a tile its own real
-- building holds aborts the whole game (ConsistencyScraper: "Build check failed: entity-in-the-way"): stage-nuclear,
-- tick 627252, an inner transport-belt ghost under a real belt at -464.5,-602.5, a live game killed by it. Ghosts were
-- put down with no build check (holograms, robots), and the engineer's hands built over its own ghosts keeping them
-- (preserve_ghosts_and_corpses). Now every ghost goes down here, only where the game would let a ghost go, and every
-- own ghost under a building is cleared before the building goes down (Ghosts.clear, worker.lua).
local Ghosts = {}

local function try(f) local ok, v = pcall(f); if ok then return v end end

-- (2.1 has script, manual, manual_ghost, script_ghost, blueprint_ghost, ghost_revive: no ghost_place)
local function check_type()
  local b = defines.build_check_type
  return b and (b.manual_ghost or b.script_ghost)
end

-- A ghost of `spec.inner_name` (create_entity's own fields: position, direction, force, type, mirror, expires, ...),
-- or nil when the game would not let a ghost go there (a real building in the way: its own included). One of the same
-- already there is that one, not a second. The check is asked of the inner building, as a ghost's: asked as
-- "entity-ghost" the game says no even on bare ground (2.1, the live engine at stage-nuclear: false there, true for
-- transport-belt with manual_ghost, false on a tile its own belt holds; Codex, W-35).
function Ghosts.create(surface, spec)
  if not (surface and spec and spec.inner_name and spec.position) then return nil end
  for _, g in ipairs(try(function() return surface.find_entities_filtered{name="entity-ghost", position=spec.position,
      radius=0.5, force=spec.force} end) or {}) do
    if g.valid and g.ghost_name == spec.inner_name then return g end
  end
  local placeable = surface.can_place_entity == nil   -- (a stub world without the game's check: as before)
    or try(function() return surface.can_place_entity{name=spec.inner_name, position=spec.position,
      direction=spec.direction, force=spec.force, build_check_type=check_type()} end)
  if not placeable then return nil end
  local def = {}
  for k, v in pairs(spec) do def[k] = v end
  def.name = "entity-ghost"
  return try(function() return surface.create_entity(def) end)
end

-- Its footprint as an area: `name` at `position` facing `direction` (a long one turned east or west lies across).
function Ghosts.area(name, position, direction)
  local p = prototypes.entity[name]
  local w, h = p and p.tile_width or 1, p and p.tile_height or 1
  local d = direction or 0
  if d == 4 or d == 12 then w, h = h, w end   -- (east, west: 16-way directions)
  return {{position.x - w / 2 + 0.01, position.y - h / 2 + 0.01}, {position.x + w / 2 - 0.01, position.y + h / 2 - 0.01}}
end

-- Every ghost of `force` on the footprint of `name` about to go down at `position` taken away first: the building
-- stands there now, so no ghost of its own (a hologram's, a robot's) is left under it. Returns how many.
function Ghosts.clear(surface, name, position, direction, force)
  if not (surface and name and position) then return 0 end
  local n = 0
  for _, g in ipairs(try(function() return surface.find_entities_filtered{type="entity-ghost",
      area=Ghosts.area(name, position, direction), force=force} end) or {}) do
    if g.valid then g.destroy(); n = n + 1 end
  end
  return n
end

return Ghosts
