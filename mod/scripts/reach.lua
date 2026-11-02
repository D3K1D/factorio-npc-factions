-- The engineers' reach, as the players': with the far-reach mod on, the same build, reach, resource reach and item drop
-- and pickup bonuses its settings give every player, on every engineer's body (the mod sets them for players only).
-- The user, 2026-10-28: "give the NPC far reach like how I had it with the far reach mod". Its jobs then walk only as far
-- as the reach needs: they read the body's own reach (worker.lua, designed_start.lua). Without the mod: vanilla.
local Reach = {}

Reach.SETTINGS = {   -- character field = the far-reach setting that gives it
  character_build_distance_bonus = "far-reach-build-distance-bonus",
  character_reach_distance_bonus = "far-reach-reach-distance-bonus",
  character_resource_reach_distance_bonus = "far-reach-resource-reach-distance-bonus",
  character_item_drop_distance_bonus = "far-reach-item-drop-distance-bonus",
  character_item_pickup_distance_bonus = "far-reach-item-pickup-distance-bonus",
}
Reach.LOOK = 600   -- ticks between looks that a body still has them (a new body gets them at once)

local function try(f) local ok, v = pcall(f); if ok then return v end end

-- What the bonuses are now ({field = n}), or nil while far-reach is not on.
function Reach.wanted()
  if not (script.active_mods and script.active_mods["far-reach"]) then return nil end
  local out = {}
  for field, setting in pairs(Reach.SETTINGS) do
    local v = try(function() return settings.global[setting].value end)
    if type(v) == "number" then out[field] = v end
  end
  return next(out) and out or nil
end

-- Puts them on its body: once for each new body (a spawn, a respawn, a swap), every LOOK after, and at once when the
-- settings changed (Reach.changed).
function Reach.keep(npc, tick)
  local c = npc.character
  if not (c and c.valid) then return end
  local body = c.unit_number
  if npc.reach_body == body and tick < (npc.reach_next or 0) then return end
  npc.reach_body, npc.reach_next = body, tick + Reach.LOOK
  local want = Reach.wanted()
  local set = npc.reach_set
  if not want then
    -- (far-reach taken off: what it put on this body goes, while it is still what it put; another mod's own stays)
    if set and set.body == body then
      for field, v in pairs(set.fields) do
        if try(function() return c[field] end) == v then pcall(function() c[field] = 0 end) end
      end
    end
    npc.reach_set = nil
    return
  end
  npc.reach_set = {body = body, fields = {}}
  for field, v in pairs(want) do
    if try(function() return c[field] end) ~= v then pcall(function() c[field] = v end) end
    npc.reach_set.fields[field] = v
  end
end

-- The settings changed: every body is looked at again on its next tick.
function Reach.changed()
  for _, npc in pairs(storage.npcs or {}) do npc.reach_next = 0 end
end

return Reach
