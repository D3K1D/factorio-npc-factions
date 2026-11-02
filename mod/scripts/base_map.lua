-- A top-down dump of an engineer's base for pictures and audits: buildings
-- (with recipes and directions), ore and water as run-length rows. Read-only.
local TeamWorld = require("scripts.team_world")
local BaseMap = {}

local MAX_ENTITIES = 30000   -- a big base (a bus with its blocks) is 7000+; planners must see all of it
local SKIP = {character=true, ["item-entity"]=true, ["entity-ghost"]=true, ["tile-ghost"]=true,
  ["character-corpse"]=true, corpse=true, explosion=true, ["smoke-with-trigger"]=true, fire=true,
  projectile=true, ["deconstructible-tile-proxy"]=true}

local function rows(tiles)
  -- tiles: map y -> sorted list of x; returns {y, x1, x2} runs
  local out = {}
  local ys = {}
  for y in pairs(tiles) do ys[#ys + 1] = y end
  table.sort(ys)
  for _, y in ipairs(ys) do
    local xs = tiles[y]
    table.sort(xs)
    local start, last = xs[1], xs[1]
    for i = 2, #xs + 1 do
      local x = xs[i]
      if x ~= last + 1 then out[#out + 1] = {y, start, last}; start = x end
      last = x
    end
  end
  return out
end

local MAX_PUMPS = 200
local MAX_RADIUS = 400

local CHUNKS_PER_TICK = 4      -- a dump is built a few chunks per tick: no stutter on a big map
local JOB_TTL = 36000          -- an unfinished or uncollected dump is dropped after this long
local WET = {"water", "deepwater", "water-green", "deepwater-green", "water-shallow", "water-mud"}   -- (fallback only)

local function jobs()
  storage.map_jobs = storage.map_jobs or {}
  return storage.map_jobs
end

-- The tiles a box covers (x0, y0 inclusive; x1, y1 exclusive). A box with an orientation is turned about its
-- middle (a diagonal cliff's, at 45 degrees): the upright box round the turned one. Taken upright, the box of a
-- diagonal cliff missed tiles it stands on, and belts were planned onto them (runs 20 and 22, 2026-10-25).
function BaseMap.tile_box(b)
  local cx, cy = (b.left_top.x + b.right_bottom.x) / 2, (b.left_top.y + b.right_bottom.y) / 2
  local hw, hh = (b.right_bottom.x - b.left_top.x) / 2, (b.right_bottom.y - b.left_top.y) / 2
  local o = b.orientation or 0
  if o ~= 0 then
    local a = o * 2 * math.pi
    local c, s = math.abs(math.cos(a)), math.abs(math.sin(a))
    hw, hh = hw * c + hh * s, hw * s + hh * c
  end
  return math.floor(cx - hw + 1e-6), math.floor(cy - hh + 1e-6), math.ceil(cx + hw - 1e-6), math.ceil(cy + hh - 1e-6)
end

-- One chunk of a dump: buildings, ore, trees and water whose tile lies inside it (an entity crossing
-- a chunk edge is counted once, in the chunk holding its position).
local function scan_chunk(job, surface, chunk)
  local x0, y0 = math.max(chunk[1] * 32, job.area[1][1]), math.max(chunk[2] * 32, job.area[1][2])
  local x1, y1 = math.min(chunk[1] * 32 + 32, job.area[2][1]), math.min(chunk[2] * 32 + 32, job.area[2][2])
  if x0 >= x1 or y0 >= y1 then return end
  local box = {{x0, y0}, {x1, y1}}
  for _, e in ipairs(surface.find_entities_filtered{area=box}) do
    local px, py = math.floor(e.position.x), math.floor(e.position.y)
    if e.valid and not SKIP[e.type] and px >= x0 and px < x1 and py >= y0 and py < y1 then
      if e.type == "resource" then
        local by = job.ore[e.name] or {}
        job.ore[e.name] = by
        by[py] = by[py] or {}
        table.insert(by[py], px)
      elseif e.type == "tree" then
        if #job.trees < 4000 then job.trees[#job.trees + 1] = {px, py} end
      elseif #job.entities < MAX_ENTITIES then
        local row = {n=e.name, t=e.type, x=e.position.x, y=e.position.y, d=e.direction,
          w=e.prototype.tile_width, h=e.prototype.tile_height, f=e.force.name}
        -- A cliff's box depends on which way it runs, not on its prototype: its own box, as tiles
        -- (a turret group put on one, live game 2026-10-22).
        if e.type == "cliff" then
          local x0, y0, x1, y1 = BaseMap.tile_box(e.bounding_box)
          row.x, row.y, row.w, row.h, row.d = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, nil
        end
        -- Which network a pole is on: new buildings join only networks a generator feeds.
        if e.type == "electric-pole" then row.net = e.electric_network_id end
        -- Which end a loader is: it puts into the building ahead ("input") or takes from the one behind.
        if e.type == "loader" then row.io = e.loader_type end
        -- Which end an underground belt is (its entrance or its exit): the lane check pairs them by it.
        if e.type == "underground-belt" then row.ug = e.belt_to_ground_type end
        -- Which fluid a pipe holds: new pipes must join the right one and keep clear of the others.
        if e.type == "pipe" or e.type == "pipe-to-ground" or e.type == "storage-tank" or e.type == "pump" then
          local ok, fluid = pcall(function() local f = e.fluids_count > 0 and e.get_fluid(1) return f and f.name end)
          if ok and fluid then row.fl = fluid end
        end
        if e.type == "assembling-machine" or e.type == "furnace" then
          local ok, recipe = pcall(function() return e.get_recipe() end)
          if ok and recipe then row.r = recipe.name end
          if e.type == "furnace" and not row.r then
            local prev = e.previous_recipe
            local name = prev and prev.name
            if name ~= nil and type(name) ~= "string" then name = name.name end
            row.r = name
          end
          local fin = pcall(function() return e.products_finished end) and e.products_finished or nil
          row.p = fin
        end
        job.entities[#job.entities + 1] = row
      end
    end
  end
  -- (every tile the game counts as water, by its collision mask: a mod's water named otherwise was left out, and a
  -- bus belt planned on it was handed back by the builder, F-21; the names only where the mask cannot be asked)
  local ok, found = pcall(function() return surface.find_tiles_filtered{area=box, collision_mask="water_tile"} end)
  if not ok then
    local wet = {}
    for _, name in ipairs(WET) do if prototypes.tile[name] then wet[#wet + 1] = name end end
    found = surface.find_tiles_filtered{area=box, name=wet}
  end
  for _, t in ipairs(found) do
    local x, y = math.floor(t.position.x), math.floor(t.position.y)
    job.water[y] = job.water[y] or {}
    table.insert(job.water[y], x)
    job.shore[#job.shore + 1] = {x=x, y=y}
  end
end

local function finish(job, npc, surface)
  local cx, cy = job.cx, job.cy
  local resources = {}
  for name, tiles in pairs(job.ore) do resources[name] = rows(tiles) end
  -- Where an offshore pump fits, as the steam template places it: one tile from a water tile,
  -- in each of the four turns (the companion lays power plants from these).
  local pumps = {}
  if prototypes.entity["offshore-pump"] then
    table.sort(job.shore, function(a, b)
      return (a.x - cx)^2 + (a.y - cy)^2 < (b.x - cx)^2 + (b.y - cy)^2
    end)
    for _, t in ipairs(job.shore) do
      if #pumps >= MAX_PUMPS then break end
      local x, y = 0, 1
      for turns = 0, 3 do
        local position = {x=t.x + 0.5 + x, y=t.y + 0.5 + y}
        if surface.can_place_entity{name="offshore-pump", position=position, direction=turns * 4,
            force=npc.force, build_check_type=defines.build_check_type.manual} then
          pumps[#pumps + 1] = {wx=t.x, wy=t.y, turns=turns, x=position.x, y=position.y, d=turns * 4}
        end
        x, y = -y, x
      end
    end
  end
  -- The engineer's powered networks: those its generators feed.
  local live = {}
  for _, g in ipairs(surface.find_entities_filtered{area=job.area, force=npc.force,
      type={"generator", "solar-panel", "burner-generator"}}) do
    if g.electric_network_id and not live[g.electric_network_id] then live[g.electric_network_id] = true end
  end
  local live_nets = {}
  for id in pairs(live) do live_nets[#live_nets + 1] = id end
  table.sort(live_nets)
  local c = npc.character
  -- The team world: its plot, the team area and where players built (kept clear), in world tiles.
  local team = TeamWorld.bounds(npc)
  if team then team.no_build = TeamWorld.no_build(surface, job.area) end
  -- Ground the game refused to build on for good (scripts/worker.lua), while what stood there still does.
  local avoid = {}
  for _, u in ipairs(npc.unbuildable or {}) do
    local box = {{u.x0, u.y0}, {u.x1 + 1, u.y1 + 1}}
    -- (water while it is water; a spot it could not get to for a while: scripts/obstacles.lua)
    local ok, n = pcall(function()
      if u.kind == "water" then return surface.count_tiles_filtered{area=box, collision_mask="water_tile", limit=1} end
      if u.until_tick then return game.tick < u.until_tick and 1 or 0 end
      return surface.count_entities_filtered{area=box, type=u.kind, limit=1}
    end)
    if not ok or n > 0 then avoid[#avoid + 1] = {x0=u.x0, y0=u.y0, x1=u.x1, y1=u.y1, kind=u.kind} end
  end
  -- Its belt lines (companion/belt_lines.py), each with whether its belt still stands at both ends.
  local belt_lines = {}
  for _, line in pairs(npc.belt_lines or {}) do
    local function stands(t)
      local ok, n = pcall(function()
        return surface.count_entities_filtered{area={{t[1], t[2]}, {t[1] + 1, t[2] + 1}}, force=npc.force,
          type={"transport-belt", "splitter", "underground-belt"}, limit=1}
      end)
      return ok and n > 0
    end
    local first, last = line.tiles[1], line.tiles[#line.tiles]
    belt_lines[#belt_lines + 1] = {id=line.id, item=line.item, tiles=line.tiles, supply=line.supply,
      claims=line.claims, ok=first ~= nil and stands(first) and stands(last), kind=line.kind, group=line.group,
      measured=line.measured, unread=line.unread, sides=line.sides, side_supply=line.side_supply}
  end
  return {ok=true, schema="npc-map-v1", npc_id=npc.id, npc_name=npc.name, force=npc.force.name, live_nets=live_nets,
    tick=game.tick, home={x=job.home.x, y=job.home.y},
    area={cx - job.radius, cy - job.radius, cx + job.radius, cy + job.radius},
    character={x=c.position.x, y=c.position.y}, entities=job.entities, resources=resources, water=rows(job.water),
    trees=job.trees, pumps=pumps, team=team, avoid=#avoid > 0 and avoid or nil,
    belt_lines=#belt_lines > 0 and belt_lines or nil}
end

-- The map around an engineer's home. Built over a few ticks: the first call starts it (and the game
-- generates missing land), later calls return {ok=false, reason="building"} until it is ready.
function BaseMap.dump(npc, radius, center)
  local c = npc and npc.character
  if not (c and c.valid) then return {ok=false, reason="actor_missing"} end
  radius = math.max(16, math.min(MAX_RADIUS, math.floor(tonumber(radius) or 96)))
  -- Around home, or around a site it builds at for someone else (a player's base).
  local home = center or npc.home or c.position
  local cx, cy = math.floor(home.x), math.floor(home.y)
  local surface = c.surface
  -- A base grows outward: land not generated yet is queued (not forced, so players never stall)
  -- and the caller asks again shortly.
  local missing = 0
  for x = math.floor((cx - radius) / 32), math.floor((cx + radius) / 32) do
    for y = math.floor((cy - radius) / 32), math.floor((cy + radius) / 32) do
      if not surface.is_chunk_generated({x, y}) then missing = missing + 1 end
    end
  end
  if missing > 0 then
    surface.request_to_generate_chunks(home, math.ceil(radius / 32))
    return {ok=false, reason="generating", missing=missing}
  end
  local job = jobs()[npc.id]
  if job and (job.cx ~= cx or job.cy ~= cy) then job = nil end   -- a dump of somewhere else: start again
  if job and job.radius == radius and job.result then
    jobs()[npc.id] = nil
    return job.result
  end
  if job and job.radius == radius then
    return {ok=false, reason="building", progress=job.index / math.max(1, #job.chunks)}
  end
  local chunks = {}
  for x = math.floor((cx - radius) / 32), math.floor((cx + radius) / 32) do
    for y = math.floor((cy - radius) / 32), math.floor((cy + radius) / 32) do chunks[#chunks + 1] = {x, y} end
  end
  jobs()[npc.id] = {radius=radius, cx=cx, cy=cy, home={x=home.x, y=home.y}, started=game.tick,
    area={{cx - radius, cy - radius}, {cx + radius, cy + radius}}, chunks=chunks, index=0,
    entities={}, ore={}, trees={}, water={}, shore={}}
  return {ok=false, reason="building", progress=0}
end

-- A few chunks of each pending dump per tick.
function BaseMap.tick(tick)
  local all = storage.map_jobs
  if not all then return end
  for id, job in pairs(all) do
    local npc = storage.npcs and storage.npcs[id]
    local c = npc and npc.character
    if not (c and c.valid) or tick - job.started > JOB_TTL then
      all[id] = nil
    elseif not job.result then
      for _ = 1, CHUNKS_PER_TICK do
        job.index = job.index + 1
        local chunk = job.chunks[job.index]
        if not chunk then break end
        scan_chunk(job, c.surface, chunk)
      end
      if job.index >= #job.chunks then
        job.result = finish(job, npc, c.surface)
        job.entities, job.ore, job.trees, job.water, job.shore, job.chunks = nil, nil, nil, nil, nil, nil
      end
    end
  end
end

return BaseMap
