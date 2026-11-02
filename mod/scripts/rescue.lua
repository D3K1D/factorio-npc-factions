-- An engineer that has boxed itself in with its own buildings frees itself, as a player would:
-- when its paths keep failing it checks whether it is really shut in (the ground it can walk
-- on around it never reaches open land), and if so picks up the nearest small piece of its own
-- at the edge of its pocket (an inserter, a pole, a pipe, an underground belt...), preferring
-- one from the plan it is building, which puts the piece back later.
local Rescue = {}

local WINDOW = 3600       -- path failures within this many ticks...
local NEEDED = 3          -- ...this many of them make it look
local RADIUS = 7          -- the pocket it checks around itself
local WALKABLE = {["transport-belt"]=true, ["simple-entity-with-owner"]=false, ["resource"]=true,
  ["corpse"]=true, ["character"]=true, ["item-entity"]=true}
local SMALL = {inserter=true, ["electric-pole"]=true, pipe=true, ["pipe-to-ground"]=true,
  ["underground-belt"]=true, splitter=true, loader=true, container=true}

function Rescue.note(npc, reason, tick)
  if not (reason and tostring(reason):find("path")) then return end
  npc.path_failures = npc.path_failures or {}
  table.insert(npc.path_failures, tick)
  while #npc.path_failures > 6 do table.remove(npc.path_failures, 1) end
end

local function key(x, y) return x .. "," .. y end

-- The walkable tiles reachable from `at` (the engineer's spot by default) within RADIUS, and whether they reach the
-- edge. `extra`: boxes ({{x1,y1},{x2,y2}}) counted solid as well (pieces still to be built there).
local function pocket(c, at, extra)
  local here = at or c.position
  local surface, cx, cy = c.surface, math.floor(here.x), math.floor(here.y)
  local area = {{cx - RADIUS - 1, cy - RADIUS - 1}, {cx + RADIUS + 2, cy + RADIUS + 2}}
  local solid, owner = {}, {}
  for _, e in ipairs(surface.find_entities_filtered{area=area}) do
    if e.valid and e ~= c and not WALKABLE[e.type] then
      local box = e.bounding_box
      for x = math.floor(box.left_top.x + 0.01), math.ceil(box.right_bottom.x - 0.01) - 1 do
        for y = math.floor(box.left_top.y + 0.01), math.ceil(box.right_bottom.y - 0.01) - 1 do
          solid[key(x, y)] = true
          if e.force == c.force and SMALL[e.type] and e.minable then owner[key(x, y)] = e end
        end
      end
    end
  end
  for _, t in ipairs(surface.find_tiles_filtered{area=area, collision_mask="water_tile"}) do
    solid[key(math.floor(t.position.x), math.floor(t.position.y))] = true
  end
  for _, b in ipairs(extra or {}) do
    for x = math.floor(b[1][1] + 0.01), math.ceil(b[2][1] - 0.01) - 1 do
      for y = math.floor(b[1][2] + 0.01), math.ceil(b[2][2] - 0.01) - 1 do solid[key(x, y)] = true end
    end
  end
  local seen, queue, edge, walls = {[key(cx, cy)]=true}, {{cx, cy}}, false, {}
  while #queue > 0 do
    local x, y = table.unpack(table.remove(queue))
    if math.abs(x - cx) >= RADIUS or math.abs(y - cy) >= RADIUS then edge = true; break end
    for _, d in ipairs({{1, 0}, {-1, 0}, {0, 1}, {0, -1}}) do
      local nx, ny = x + d[1], y + d[2]
      local k = key(nx, ny)
      if not seen[k] then
        seen[k] = true
        if solid[k] then
          if owner[k] then walls[#walls + 1] = owner[k] end
        else
          queue[#queue + 1] = {nx, ny}
        end
      end
    end
  end
  return edge, walls
end

-- Whether a spot is open ground (it can walk away from it), with `extra` boxes counted solid: a stand point is never one
-- in a closed pocket (worker.lua's stand points; the Workers Union review, 2026-10-29: their construct.lua:265-447).
function Rescue.open_at(c, at, extra)
  local ok, open = pcall(function() return (pocket(c, at, extra)) end)
  return not ok or open == true
end

local function planned(npc, e)
  local d = npc.design
  if not (d and d.plan) then return false end
  for _, id in ipairs(d.plan.order) do if d.plan.entities[id].entity == e then return true end end
  return false
end

-- The piece to pick up when the engineer is shut in, or nil.
function Rescue.blocker(npc, tick)
  local f, c = npc.path_failures, npc.character
  if not (f and #f >= NEEDED and tick - f[#f - NEEDED + 1] <= WINDOW and c and c.valid) then return nil end
  npc.path_failures = {}
  local open, walls = pocket(c)
  if open or #walls == 0 then return nil end
  table.sort(walls, function(a, b)
    local pa, pb = planned(npc, a), planned(npc, b)
    if pa ~= pb then return pa end
    local da = (a.position.x - c.position.x)^2 + (a.position.y - c.position.y)^2
    local db = (b.position.x - c.position.x)^2 + (b.position.y - c.position.y)^2
    return da < db
  end)
  local e = walls[1]
  log("[npc-factions] npc=" .. npc.id .. " is boxed in at " .. math.floor(c.position.x) .. "," .. math.floor(c.position.y)
    .. ": picking up " .. e.name .. " at " .. e.position.x .. "," .. e.position.y)
  return e
end

return Rescue
