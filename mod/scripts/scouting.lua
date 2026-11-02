-- What an engineer knows about resources beyond its base, and scouting trips
-- to find more. Knowledge comes only from what its body has been close
-- enough to see; a trip is a series of walks to points around home.
local TeamWorld = require("scripts.team_world")
local Scouting = {}

local SIGHT = 40            -- tiles an engineer can make out resources around itself
local CELL = 16             -- patches are remembered per 16x16 cell and resource
local MAX_CELLS = 256
local LOOK_EVERY = 600
local RINGS = {56, 88, 120}

local function cell_key(name, x, y) return name .. ":" .. math.floor(x / CELL) .. ":" .. math.floor(y / CELL) end

-- Oil wells are a few scattered entities, not a carpet of ore tiles.
local fluid_cache = {}
local function fluid(name)
  if fluid_cache[name] == nil then
    local products = prototypes.entity[name] and prototypes.entity[name].mineable_properties.products or {}
    fluid_cache[name] = products[1] ~= nil and products[1].type == "fluid"
  end
  return fluid_cache[name]
end
local function enough(cell, tiles) return cell.tiles >= (fluid(cell.resource) and 1 or tiles) end

-- Resource entities, by cell: {key = {resource, sx, sy, tiles, amount}}.
local function cells_of(entities)
  local seen = {}
  for _, e in ipairs(entities) do
    local k = cell_key(e.name, e.position.x, e.position.y)
    local cell = seen[k]
    if not cell then cell = {resource=e.name, sx=0, sy=0, tiles=0, amount=0}; seen[k] = cell end
    cell.sx, cell.sy = cell.sx + e.position.x, cell.sy + e.position.y
    cell.tiles, cell.amount = cell.tiles + 1, cell.amount + e.amount
  end
  return seen
end

local function remember(npc, seen, tick, surface_index)
  local known = npc.known_patches or {}
  npc.known_patches = known
  local keys = {}
  for k in pairs(seen) do keys[#keys + 1] = k end
  table.sort(keys)
  for _, k in ipairs(keys) do
    local cell = seen[k]
    local old = known[k]
    -- A cell at the edge of sight is only partly visible: keep the bigger count.
    if not old or cell.tiles >= old.tiles or tick - old.tick > 216000 then
      known[k] = {resource=cell.resource, x=cell.sx / cell.tiles, y=cell.sy / cell.tiles, tiles=cell.tiles,
        amount=cell.amount, tick=tick, surface_index=surface_index}
    else
      old.tick = tick
    end
  end
  return known
end

-- What its own ground holds, known from the start (the team world lays a start round each home: its patches
-- lie beyond what it sees from home, and it would go scouting for ore it was given). Once.
local function learn_ground(npc, surface, tick)
  if npc.ground_learned or not TeamWorld.active() then return end
  npc.ground_learned = tick
  local mine = "npc-" .. npc.id
  for _, piece in ipairs(storage.team and storage.team.added or {}) do
    if piece.plot == mine and piece.position and piece.what ~= "water" then
      local r = math.sqrt((piece.tiles or 0) / math.pi) + 1
      remember(npc, cells_of(surface.find_entities_filtered{position=piece.position, radius=r, type="resource"}),
        tick, surface.index)
    end
  end
end

-- The resources in an area known as if scouted (a field a scenario put down where no one has looked).
function Scouting.learn(npc, surface, area, tick)
  return remember(npc, cells_of(surface.find_entities_filtered{area=area, type="resource"}), tick or game.tick,
    surface.index)
end

-- Remember resources in sight; forget cells in plain view that are now empty.
function Scouting.observe(npc, tick, now)
  local c = npc.character
  if not (c and c.valid) then return end
  learn_ground(npc, c.surface, tick)
  if not now and tick < (npc.scout_seen_tick or -LOOK_EVERY) + LOOK_EVERY then return end
  npc.scout_seen_tick = tick
  local seen = cells_of(c.surface.find_entities_filtered{position=c.position, radius=SIGHT, type="resource"})
  local known = remember(npc, seen, tick, c.surface.index)
  local stale = {}
  for k, cell in pairs(known) do
    local dx, dy = cell.x - c.position.x, cell.y - c.position.y
    if not seen[k] and cell.surface_index == c.surface.index and dx * dx + dy * dy <= (SIGHT - CELL) ^ 2 then
      stale[#stale + 1] = k
    end
  end
  for _, k in ipairs(stale) do known[k] = nil end
  local all = {}
  for k, cell in pairs(known) do all[#all + 1] = {k, cell.tick} end
  if #all > MAX_CELLS then
    table.sort(all, function(a, b) if a[2] ~= b[2] then return a[2] < b[2] end return a[1] < b[1] end)
    for i = 1, #all - MAX_CELLS do known[all[i][1]] = nil end
  end
end

-- Known patches whose centre lies outside `box` (world {{x0,y0},{x1,y1}}),
-- relative to home, nearest first: the nearest few of every resource (so a near
-- coal patch is never crowded out by nearer cells of iron), with what is left in each.
local PER_RESOURCE = 4
function Scouting.patches(npc, box, limit)
  local c = npc.character
  if c and c.valid then learn_ground(npc, c.surface, game.tick) end   -- (its first request may come first)
  local out = {}
  local home = npc.home
  for _, cell in pairs(npc.known_patches or {}) do
    local inside = box and cell.x >= box[1][1] and cell.x < box[2][1] and cell.y >= box[1][2] and cell.y < box[2][2]
    if not inside and enough(cell, 4) then
      out[#out + 1] = {resource=cell.resource, dx=math.floor(cell.x - home.x + 0.5), dy=math.floor(cell.y - home.y + 0.5),
        tiles=cell.tiles, amount=cell.amount}
    end
  end
  table.sort(out, function(a, b)
    local da, db = a.dx * a.dx + a.dy * a.dy, b.dx * b.dx + b.dy * b.dy
    if da ~= db then return da < db end
    if a.resource ~= b.resource then return a.resource < b.resource end
    return a.dx < b.dx or a.dx == b.dx and a.dy < b.dy
  end)
  local kept, per = {}, {}
  for _, patch in ipairs(out) do
    per[patch.resource] = (per[patch.resource] or 0) + 1
    if per[patch.resource] <= PER_RESOURCE and #kept < (limit or 24) then kept[#kept + 1] = patch end
  end
  return kept
end

-- In the team world a patch is worth finding only on the engineer's own ground, a little inside its plot
-- (the planner's site slides inward up to 24 tiles to reach one), or out beyond the team area: one in a
-- teammate's plot ends no trip (a trip that stopped at the same patch past its plot's edge never saw the
-- copper field beside home, rehearsal 2026-10-22).
local GROUND_MARGIN = 12
local function usable(npc, cell)
  local b = TeamWorld.active() and TeamWorld.bounds(npc)
  if not (b and b.area) then return true end
  local x, y, m, p, a = cell.x, cell.y, GROUND_MARGIN, b.plot, b.area
  if p and x >= p[1] + m and x < p[3] - m and y >= p[2] + m and y < p[4] - m then return true end
  return x < a[1] - m or x >= a[3] + m or y < a[2] - m or y >= a[4] + m
end
Scouting.usable = usable

local function found(npc)
  local trip = npc.scout
  local home = npc.home
  for _, cell in pairs(npc.known_patches or {}) do
    if cell.resource == trip.resource and enough(cell, 16) and usable(npc, cell) then
      if math.abs(cell.x - home.x) > trip.window or math.abs(cell.y - home.y) > trip.window then return cell end
    end
  end
end

function Scouting.start(npc, resource, purpose, window, tick)
  local points = {}
  for ring, r in ipairs(RINGS) do
    for k = 0, 7 do
      local a = k * math.pi / 4 + (ring % 2 == 0 and math.pi / 8 or 0)
      points[#points + 1] = {x=npc.home.x + r * math.cos(a), y=npc.home.y + r * math.sin(a)}
    end
  end
  npc.scout = {resource=resource, purpose=purpose, window=window, points=points, index=1, started=tick}
end

-- Whether a straight walk from a to b keeps out of the biters' detection range (a nest's biters and
-- worms see about this far).
local DETECT, STEP = 76, 24
local function clear_way(surface, a, b)
  local dx, dy = b.x - a.x, b.y - a.y
  local n = math.max(1, math.ceil(math.sqrt(dx * dx + dy * dy) / STEP))
  for i = 0, n do
    local p = {x = a.x + dx * i / n, y = a.y + dy * i / n}
    if surface.count_entities_filtered{position=p, radius=DETECT, force="enemy",
        type={"unit-spawner", "turret"}, limit=1} > 0 then return false end
  end
  return true
end

-- The next walk of the trip, or nil and the outcome ("found"/"exhausted").
function Scouting.next(npc, tick)
  local trip = npc.scout
  if not trip then return nil end
  if found(npc) then return nil, "found" end
  local surface = npc.character.surface
  while trip.index <= #trip.points do
    local p = trip.points[trip.index]
    trip.index = trip.index + 1
    -- Skip points it could only reach through the biters' detection range (or with nowhere to stand):
    -- it never goes near a nest but to destroy it.
    local spot = clear_way(surface, npc.character.position, p)
      and surface.find_non_colliding_position("character", p, 10, 1)
    if spot then return {kind="move", position=spot}, nil end
  end
  return nil, "exhausted"
end

-- Called with every scouting walk's receipt: look around wherever it ended.
function Scouting.arrived(npc, tick)
  Scouting.observe(npc, tick, true)
end

-- All engineers notice resources around them as they go about their work.
function Scouting.tick(tick)
  for id = 1, 10 do
    local npc = storage.npcs and storage.npcs[id]
    if npc and npc.character and npc.character.valid and (tick + id * 60) % LOOK_EVERY == 0 then
      Scouting.observe(npc, tick, true)
    end
  end
end

return Scouting
