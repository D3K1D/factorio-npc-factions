-- The driving range (tests only; the user, 2026-10-14): engineers drive trip after trip, each laid out
-- at random from a seed, and how each one ends is kept, so the autopilot is judged by how many of many
-- trips arrive, and how fast, not by one trip in a long game. Nothing about a trip is known to the
-- driver beyond its goal: what is in the way it finds as on any map.
--   "course":  the land along the trip cleared, then rock fields, tree patches, a belt of trees with a
--              gap or two, a wall of rocks with a narrow gap, lakes, a bay that is a dead end and
--              scattered trees and rocks put down at random between start and goal;
--   "terrain": the map as it was generated.
-- While it runs nothing else ticks (control.lua), so it runs as fast as the game can go.
local Drive = require("scripts.drive")
local Factory = require("scripts.factory")
local Range = {}

local TREES = {"tree-01", "tree-02", "tree-03", "tree-04", "tree-05", "tree-06", "tree-07", "tree-08", "tree-09",
  "dead-grey-trunk", "dry-tree"}
local ROCKS = {"big-rock", "huge-rock", "big-sand-rock"}
local VEHICLES = {"car", "tank"}
local FUEL, FUEL_GIVEN = "solid-fuel", 50
local SPACING = 1600       -- courses this far apart: none in another's way
local WIDTH = 120          -- land cleared this far either side of a course's straight line
local CLEAR_ENDS = 18      -- nothing put down this near the start or the goal
local WALK_SPEED = 0.15
local TWO_PI = 2 * math.pi

local function distance(a, b)
  local dx, dy = a.x - b.x, a.y - b.y
  return math.sqrt(dx * dx + dy * dy)
end

local function existing(names, kind)
  local out = {}
  for _, name in ipairs(names) do
    local p = prototypes.entity[name]
    if p and (not kind or p.type == kind) then out[#out + 1] = name end
  end
  return out
end

-- Course coordinates: u along the way from the start, v across it.
local function along(t, u, v)
  return {x = t.start.x + t.dir.x * u - t.dir.y * v, y = t.start.y + t.dir.y * u + t.dir.x * v}
end

-- The land a trip needs, as boxes: asked for, made by the game in the background (made all at once it
-- held the game still for seconds, six courses on the first tick), then looked at until it is there.
-- (and as wide as the band the drive itself waits for, or the trip would time how fast the game makes
-- land at full speed: a third of each trip spent waiting for it)
local BAND = 176
local function boxes(t, mode)
  local a, b
  if mode == "course" then
    a, b = along(t, -BAND, -BAND), along(t, t.length + BAND, BAND)
  else
    a, b = t.start, t.goal
  end
  return {{{math.min(a.x, b.x) - (mode == "course" and 0 or BAND), math.min(a.y, b.y) - (mode == "course" and 0 or BAND)},
    {math.max(a.x, b.x) + (mode == "course" and 0 or BAND), math.max(a.y, b.y) + (mode == "course" and 0 or BAND)}}}
end
local function course_box(t)
  local a, b = along(t, -40, -WIDTH), along(t, t.length + 40, WIDTH)
  return {{math.min(a.x, b.x), math.min(a.y, b.y)}, {math.max(a.x, b.x), math.max(a.y, b.y)}}
end
local function ask_land(s, list)
  for _, box in ipairs(list) do
    local cx, cy = (box[1][1] + box[2][1]) / 2, (box[1][2] + box[2][2]) / 2
    local r = math.ceil(math.max(box[2][1] - box[1][1], box[2][2] - box[1][2]) / 64) + 1
    s.request_to_generate_chunks({cx, cy}, r)
  end
end
local function land_ready(s, list)
  for _, box in ipairs(list) do
    for cx = math.floor(box[1][1] / 32), math.floor(box[2][1] / 32) do
      for cy = math.floor(box[1][2] / 32), math.floor(box[2][2] / 32) do
        if not s.is_chunk_generated({cx, cy}) then return false end
      end
    end
  end
  return true
end

-- The land along a course cleared (trees, rocks, cliffs, water), then what is in the way put down.
-- Biters on a course (spec.biters, 0 none): a nest or two to the side of the way, small worms about
-- each, groups of small biters standing in the way, now and then a medium worm by it.
local function lay_biters(s, t, rng, biters)
  local L, k = t.length, t.length / 600 * biters
  local placed = 0
  local function enemy(name, u, v)
    local p = along(t, u, v)
    if distance(p, t.start) < 40 or distance(p, t.goal) < 40 then return end
    if s.get_tile(p.x, p.y).collides_with("water_tile") then return end
    local q = s.find_non_colliding_position(name, p, 8, 1)
    if q and pcall(s.create_entity, {name = name, position = q, force = "enemy"}) then placed = placed + 1 end
  end
  local function side() return (rng() < 0.5 and -1 or 1) * (20 + rng() * 25) end
  for _ = 1, math.max(1, math.floor((1 + rng(0, 1)) * k + 0.5)) do
    local u, v = 60 + rng() * (L - 120), side()
    enemy(rng() < 0.5 and "biter-spawner" or "spitter-spawner", u, v)
    for _ = 1, rng(1, 3) do enemy("small-worm-turret", u + (rng() * 2 - 1) * 10, v + (rng() * 2 - 1) * 10) end
  end
  for _ = 1, math.max(1, math.floor((1 + rng(0, 2)) * k + 0.5)) do
    local u, v = 60 + rng() * (L - 120), (rng() * 2 - 1) * 20
    for _ = 1, rng(4, 10) do enemy("small-biter", u + (rng() * 2 - 1) * 4, v + (rng() * 2 - 1) * 4) end
  end
  if rng() < 0.3 * biters then enemy("medium-worm-turret", 60 + rng() * (L - 120), side() * 0.6) end
  t.enemies = placed
end

local function lay_course(s, t, rng, density)
  local box = course_box(t)
  for _, e in ipairs(s.find_entities_filtered{area = box,
      type = {"tree", "simple-entity", "cliff", "fish", "unit", "unit-spawner", "turret"}}) do
    e.destroy()
  end
  local dry = {}
  for _, tile in ipairs(s.find_tiles_filtered{area = box, collision_mask = "water_tile"}) do
    dry[#dry + 1] = {name = "grass-1", position = tile.position}
  end
  if #dry > 0 then s.set_tiles(dry) end

  local L, k = t.length, t.length / 600 * density
  local trees, rocks = existing(TREES, "tree"), existing(ROCKS)
  local function u_any() return 40 + rng() * (L - 80) end
  local function v_near() return (rng() * 2 - 1) * 60 end
  local function away_from_ends(p) return distance(p, t.start) > CLEAR_ENDS and distance(p, t.goal) > CLEAR_ENDS end
  -- water first, so nothing is put down on it
  local water = {}
  local function wet(u, v)
    local p = along(t, u, v)
    if away_from_ends(p) then water[#water + 1] = {name = "water", position = {math.floor(p.x), math.floor(p.y)}} end
  end
  for _ = 1, math.floor(rng(0, 2) * k + 0.5) do   -- lakes
    local u, v, ra, rb = u_any(), v_near(), 8 + rng(0, 27), 6 + rng(0, 19)
    for du = -ra, ra do
      for dv = -rb, rb do
        if (du / ra) ^ 2 + (dv / rb) ^ 2 <= 1 then wet(u + du, v + dv) end
      end
    end
  end
  if rng() < 0.3 * density then   -- a bay open toward the start: driven into, no way on
    local u, v, R, th = 60 + rng() * (L - 120), v_near() / 2, 18 + rng(0, 16), 6 + rng(0, 4)
    for du = -R - th, R + th do
      for dv = -R - th, R + th do
        local q = math.sqrt(du * du + dv * dv)
        if q >= R and q <= R + th and du > -R * 0.5 then wet(u + du, v + dv) end
      end
    end
  end
  if #water > 0 then s.set_tiles(water) end

  local placed = 0
  local function put(names, u, v)
    if #names == 0 then return end
    local p = along(t, u, v)
    if not away_from_ends(p) or s.get_tile(p.x, p.y).collides_with("water_tile") then return end
    if pcall(s.create_entity, {name = names[rng(1, #names)], position = p}) then placed = placed + 1 end
  end
  for _ = 1, math.max(1, math.floor((1 + rng(0, 3)) * k + 0.5)) do   -- rock fields
    local u, v, r = u_any(), v_near(), 4 + rng() * 12
    for _ = 1, rng(4, 20) do
      local ang, q = rng() * TWO_PI, rng() * r
      put(rocks, u + math.cos(ang) * q, v + math.sin(ang) * q)
    end
  end
  for _ = 1, math.max(1, math.floor((1 + rng(0, 3)) * k + 0.5)) do   -- tree patches, thin to thick
    local u, v, ra, rb, dense = u_any(), v_near(), 10 + rng() * 40, 8 + rng() * 32, 0.05 + rng() * 0.4
    for _ = 1, math.floor(math.pi * ra * rb * dense) do
      local ang, q = rng() * TWO_PI, math.sqrt(rng())
      put(trees, u + math.cos(ang) * q * ra, v + math.sin(ang) * q * rb)
    end
  end
  if rng() < 0.5 * density then   -- a belt of trees across the way, a gap or two in it
    local u, thick, gaps = u_any(), 4 + rng(0, 10), {}
    for i = 1, rng(1, 2) do gaps[i] = {v = (rng() * 2 - 1) * (WIDTH - 30), w = 8 + rng(0, 12)} end
    for v = -WIDTH + 5, WIDTH - 5, 2 do
      local open = false
      for _, g in ipairs(gaps) do if math.abs(v - g.v) < g.w / 2 then open = true end end
      if not open then
        for du = 0, thick, 2 do
          if rng() < 0.7 then put(trees, u + du + rng() * 1.5, v + rng() * 1.5) end
        end
      end
    end
  end
  if rng() < 0.4 * density then   -- a wall of rocks across, one narrow gap
    local u, gv, gw = u_any(), (rng() * 2 - 1) * 50, 5 + rng(0, 4)
    for v = -70, 70, 2.5 do
      if math.abs(v - gv) > gw / 2 then put(rocks, u, v) end
    end
  end
  for _ = 1, math.floor(rng(10, 40) * k) do   -- scattered trees and rocks
    put(rng() < 0.6 and trees or rocks, u_any(), (rng() * 2 - 1) * 80)
  end
  t.obstacles, t.water = placed, #water
end

-- A walled home (spec.walled; M-1): a ring of walls round the start, one door of four gates on a side
-- picked at random, the car parked inside by a door as an engineer's would be. The trip goes out through
-- the door to the goal, then home again, parking inside.
local RING, DOOR = 30, 4
local function lay_ring(s, t, rng, npc)
  local cx, cy = math.floor(t.start.x), math.floor(t.start.y)
  local x0, y0, x1, y1 = cx - RING, cy - RING, cx + RING, cy + RING
  local area = {{x0 - 24, y0 - 24}, {x1 + 25, y1 + 25}}
  for _, e in ipairs(s.find_entities_filtered{area = area, type = {"tree", "simple-entity", "cliff", "fish", "wall", "gate"}}) do
    e.destroy()
  end
  local dry = {}
  for _, tile in ipairs(s.find_tiles_filtered{area = area, collision_mask = "water_tile"}) do
    dry[#dry + 1] = {name = "grass-1", position = tile.position}
  end
  if #dry > 0 then s.set_tiles(dry) end
  local side = rng(1, 4)   -- the door's: north, south, west, east
  local built = {}
  local function put(door, x, y, dir)
    local e = s.create_entity{name = door and "gate" or "stone-wall", position = {x + 0.5, y + 0.5}, force = npc.force,
      direction = door and dir or nil}
    if e then built[#built + 1] = e end
  end
  for x = x0, x1 do   -- (a gate in a wall running east-west faces east, crossed north-south)
    put(side == 1 and math.abs(x + 0.5 - cx) < DOOR / 2, x, y0, defines.direction.east)
    put(side == 2 and math.abs(x + 0.5 - cx) < DOOR / 2, x, y1, defines.direction.east)
  end
  for y = y0 + 1, y1 - 1 do
    put(side == 3 and math.abs(y + 0.5 - cy) < DOOR / 2, x0, y, defines.direction.north)
    put(side == 4 and math.abs(y + 0.5 - cy) < DOOR / 2, x1, y, defines.direction.north)
  end
  t.ring, t.built, t.door_side = {x0, y0, x1 + 1, y1 + 1}, built, side
  npc.design_history = {{keep = {ring = t.ring, ring_gen = t.n}}}
end

-- A start and a goal out on the map as generated.
local function lay_terrain(s, t)
  local a = s.find_non_colliding_position("car", t.start, 64, 1)
  local b = s.find_non_colliding_position("car", t.goal, 64, 1)
  if not (a and b) then return false end
  t.start, t.goal = {x = a.x, y = a.y}, {x = b.x, y = b.y}
  t.length = distance(t.start, t.goal)
  return true
end

-- spec: drivers (1-10), trips, seed, mode ("course" or "terrain"), vehicle ("car" or "tank"), length
-- (tiles start to goal), density (how much is in the way on a course, 1 the usual), biters (biters on a
-- course, 0 none, 1 the usual).
function Range.setup(spec)
  if storage.drive_range then return {ok = false, reason = "range_running"} end
  local drivers = math.floor(tonumber(spec.drivers) or 4)
  local trips = math.floor(tonumber(spec.trips) or 20)
  local mode = spec.mode or "course"
  local vehicle = spec.vehicle or "car"
  local length = tonumber(spec.length) or 600
  if drivers < 1 or drivers > 10 then return {ok = false, reason = "drivers_must_be_1_to_10"} end
  if trips < 1 or trips > 400 then return {ok = false, reason = "trips_must_be_1_to_400"} end
  if mode ~= "course" and mode ~= "terrain" then return {ok = false, reason = "unknown_mode"} end
  if not prototypes.entity[vehicle] or prototypes.entity[vehicle].type ~= "car" then
    return {ok = false, reason = "unknown_vehicle"}
  end
  if length < 150 or length > 1400 then return {ok = false, reason = "length_must_be_150_to_1400"} end
  local seed = math.floor(tonumber(spec.seed) or 1) % 4294967296
  -- only these trips of the batch (to look again at the ones that failed), laid out as in the whole batch
  local only
  if type(spec.only) == "table" and #spec.only > 0 then
    only = {}
    for _, n in ipairs(spec.only) do only[math.floor(tonumber(n) or 0)] = true end
  end
  local rng = game.create_random_generator(seed)
  local r = {spec = {drivers = drivers, trips = trips, seed = seed, mode = mode, vehicle = vehicle, length = length,
      density = tonumber(spec.density) or 1, biters = tonumber(spec.biters) or 0,
      repair = math.floor(tonumber(spec.repair) or 20), trace = only and 30 or 12,
      walled = mode == "course" and spec.walled == true},
    trips = {}, next = 1, active = {}, results = {}, drivers = {}}
  local dirs = {{x = 1, y = 0}, {x = -1, y = 0}, {x = 0, y = 1}, {x = 0, y = -1}}
  for i = 1, trips do
    local t = {n = i, seed = rng(1, 2147483646), length = length}
    if mode == "course" then
      local c = {x = 4000 + ((i - 1) % 6) * SPACING, y = 4000 + math.floor((i - 1) / 6) * SPACING}
      t.dir = dirs[rng(1, 4)]
      t.start = {x = c.x - t.dir.x * length / 2, y = c.y - t.dir.y * length / 2}
      t.goal = {x = c.x + t.dir.x * length / 2, y = c.y + t.dir.y * length / 2}
    else
      local a, q, b = rng() * TWO_PI, 600 + rng() * 2400, rng() * TWO_PI
      t.start = {x = math.cos(a) * q, y = math.sin(a) * q}
      t.goal = {x = t.start.x + math.cos(b) * length, y = t.start.y + math.sin(b) * length}
    end
    r.trips[#r.trips + 1] = (not only or only[i]) and t or nil
  end
  storage.drive_range = r
  local surface = game.surfaces[1]
  for id = 1, drivers do
    local npc, reason = Factory.spawn(id, surface, {x = id * 4, y = 0}, {x = 0, y = 0}, game.forces.player)
    if not npc then storage.drive_range = nil; return {ok = false, reason = "spawn: " .. tostring(reason)} end
    npc.drive_range = true
    r.drivers[#r.drivers + 1] = id
  end
  return {ok = true, drivers = drivers, trips = trips, mode = mode, vehicle = vehicle, length = length, seed = seed}
end

local function count(trace, what)
  local n = 0
  for _, line in ipairs(trace or {}) do
    if string.find(line, what, 1, true) then n = n + 1 end
  end
  return n
end

-- The vehicles it left about, and itself out of one.
local function clear_vehicles(npc)
  local c = npc.character
  if c and c.valid and c.vehicle and c.vehicle.valid then pcall(function() c.vehicle.set_driver(nil) end) end
  for _, e in ipairs(c.surface.find_entities_filtered{type = "car", force = npc.force}) do e.destroy() end
  npc.parked, npc.drive = nil, nil
end

local function start_trip(r, npc, t, tick)
  local s = npc.character.surface
  if r.spec.mode == "course" then
    lay_course(s, t, game.create_random_generator(t.seed), r.spec.density)
    if (r.spec.biters or 0) > 0 then lay_biters(s, t, game.create_random_generator(t.seed + 1), r.spec.biters) end
  elseif not lay_terrain(s, t) then
    r.results[#r.results + 1] = {n = t.n, outcome = "no_place", length = t.length}
    return
  end
  local c = npc.character
  c.teleport(s.find_non_colliding_position("character", t.start, 32, 0.5) or t.start)
  local main = c.get_main_inventory()
  for _, name in ipairs(VEHICLES) do
    local n = main.get_item_count(name)
    if n > 0 then main.remove{name = name, count = n} end
  end
  main.insert{name = r.spec.vehicle, count = 1}
  local fuel = main.get_item_count(FUEL)
  if fuel < FUEL_GIVEN then main.insert{name = FUEL, count = FUEL_GIVEN - fuel} end
  if (r.spec.biters or 0) > 0 then   -- magazines for the vehicle's gun (loaded from its pockets as it gets in)
    local mags = main.get_item_count("firearm-magazine")
    if mags < 100 then main.insert{name = "firearm-magazine", count = 100 - mags} end
    if r.spec.vehicle == "tank" and main.get_item_count("cannon-shell") < 20 then
      main.insert{name = "cannon-shell", count = 20 - main.get_item_count("cannon-shell")}
    end
  end
  -- repair packs (spec.repair a trip, 0 none): a vehicle badly hurt is driven clear and mended
  local packs = main.get_item_count("repair-pack")
  if packs < r.spec.repair then main.insert{name = "repair-pack", count = r.spec.repair - packs} end
  if r.spec.repair <= 0 and packs > 0 then main.remove{name = "repair-pack", count = packs} end
  t.packs = main.get_item_count("repair-pack")
  npc.drive, npc.parked, npc.seen_dangers, npc.route_prep, npc.drive_trace = nil, nil, nil, nil, {}
  npc.drive_legs = {}
  npc.home = {x = t.start.x, y = t.start.y}
  if r.spec.walled then
    lay_ring(s, t, game.create_random_generator(t.seed + 2), npc)
    c.teleport(s.find_non_colliding_position("character", t.start, 8, 0.5) or t.start)
    -- the car waits in its garage by the door nearest the goal, fueled, not in its pockets
    local spot = Drive.garage(npc, s, t.goal, r.spec.vehicle)
    local car = spot and s.create_entity{name = r.spec.vehicle, position = spot, force = npc.force}
    if car then
      main.remove{name = r.spec.vehicle, count = 1}
      car.insert{name = FUEL, count = 20}
      npc.parked = car
    end
    t.garage = spot
  end
  Drive.start(npc, t.goal, 8, tick, {})
  t.started, t.driven, t.last, t.driver = tick, 0, nil, npc.id
  t.deadline = tick + math.floor(3 * t.length / WALK_SPEED) + 7200
  r.active[npc.id] = t
end

local function finish_trip(r, npc, t, tick, outcome)
  local c = npc.character
  local here = c and c.valid and ((c.vehicle and c.vehicle.valid and c.vehicle.position) or c.position) or t.start
  local trace = npc.drive_trace or {}
  local result = {n = t.n, outcome = outcome, length = math.floor(t.length), ticks = tick - t.started,
    driven = math.floor(t.driven), gap = math.floor(distance(here, t.goal)),
    stuck = count(trace, "stuck "), clears = count(trace, "clearing"), no_way = count(trace, "no way"),
    routes = count(trace, "route of"), fled = count(trace, "under fire"), obstacles = t.obstacles, water = t.water,
    busy = count(trace, "pathfinder busy"), land_waits = count(trace, "waiting for the land"),
    busy_ticks = npc.drive and npc.drive.busy_ticks,
    start = {x = math.floor(t.start.x), y = math.floor(t.start.y)}, goal = {x = math.floor(t.goal.x), y = math.floor(t.goal.y)},
    spent = npc.drive and npc.drive.spent,
    -- how it fared among biters: the vehicle's lowest health (0 wrecked), its driver's, how long biters
    -- were near and its speed then, how long it was shooting
    enemies = t.enemies, vehicle_hp = t.min_hp and math.floor(100 * t.min_hp) / 100, lowest = t.lowest,
    driver_hp = c and c.valid and math.floor(100 * c.health / c.max_health) / 100 or 0,
    threat_s = t.threat_ticks and math.floor(t.threat_ticks / 6) / 10,
    threat_speed = t.threat_ticks and t.threat_ticks > 0 and math.floor(1000 * t.threat_speed / t.threat_ticks) / 1000 or nil,
    shooting_s = t.shooting_ticks and math.floor(t.shooting_ticks / 6) / 10,
    -- mending it: how often, how often broken off by biters coming, packs used
    mends = npc.drive and npc.drive.mends or 0,
    mend_breaks = count(trace, "back in, away") + count(trace, "hit as it stood"),
    packs_used = t.packs and c and c.valid and t.packs - c.get_main_inventory().get_item_count("repair-pack") or nil}
  if t.ring then
    -- the walled home: which way it went out and in, whether it touched the wall, where it left the car
    local all = {}
    for _, line in ipairs(t.out_trace or {}) do all[#all + 1] = line end
    for _, line in ipairs(t.back and trace or {}) do all[#all + 1] = line end
    local hurt, lost = 0, 0
    for _, e in ipairs(t.built or {}) do
      if not e.valid then lost = lost + 1 elseif e.health < e.max_health then hurt = hurt + 1 end
    end
    local v = c and c.valid and c.vehicle and c.vehicle.valid and c.vehicle or npc.parked and npc.parked.valid and npc.parked
    local at, box = v and v.position, t.ring
    result.walled = {door = ({"north", "south", "west", "east"})[t.door_side], out_ticks = t.out_ticks, back = t.back,
      to_door = count(all, "to the door"), through_door = count(all, "through the door"),
      round_ring = count(all, "round its ring"), tightest = count(all, "tightest way"),
      parking = count(all, "parking inside"), walls_hurt = hurt, walls_lost = lost,
      parked_inside = at and at.x > box[1] and at.x < box[3] and at.y > box[2] and at.y < box[4] or false,
      from_garage = at and t.garage and math.floor(distance(at, t.garage) * 10) / 10}
    if outcome ~= "arrived" and t.out_trace then result.out_trace = t.out_trace end
  end
  if outcome ~= "arrived" then
    local tail = {}
    for i = math.max(1, #trace - (r.spec.trace or 12) + 1), #trace do tail[#tail + 1] = trace[i] end
    result.trace = tail
    result.trail = t.trail
    result.door_trail = t.door_trail
    local legs = {}
    for _, l in ipairs(npc.drive_legs or {}) do legs[#legs + 1] = string.format("%d %d,%d>%d,%d", l.t, l.fx, l.fy, l.x, l.y) end
    result.legs = legs
  end
  r.results[#r.results + 1] = result
  r.active[npc.id] = nil
  clear_vehicles(npc)
  -- (it waits for its next course away from this one's goal: the biters that chased the car there
  -- killed drivers standing by, nothing else of theirs ticking on the range)
  if c and c.valid then c.teleport(c.surface.find_non_colliding_position("character", {x = npc.id * 4, y = 0}, 32, 0.5) or {x = npc.id * 4, y = 0}) end
end

function Range.tick(tick)
  local r = storage.drive_range
  for _, id in ipairs(r.drivers) do
    local npc = storage.npcs[id]
    local c = npc and npc.character
    local t = r.active[id]
    if not (c and c.valid) then
      if t then
        local trace, tail = npc and npc.drive_trace or {}, {}
        for i = math.max(1, #trace - 11), #trace do tail[#tail + 1] = trace[i] end
        r.results[#r.results + 1] = {n = t.n, outcome = "driver_lost", length = math.floor(t.length),
          ticks = t.started and tick - t.started, driven = t.driven and math.floor(t.driven), enemies = t.enemies,
          vehicle_hp = t.min_hp and math.floor(100 * t.min_hp) / 100, trace = tail, trail = t.trail,
          died_at = npc and npc.last_position and {x = math.floor(npc.last_position.x), y = math.floor(npc.last_position.y)},
          start = {x = math.floor(t.start.x), y = math.floor(t.start.y)}}
        r.active[id] = nil
      end
    elseif t and t.waiting then
      -- its land made, the trip laid out and begun (one a tick: laying one out takes a while)
      if r.laid ~= tick and land_ready(c.surface, t.land) then
        r.laid = tick
        t.waiting = nil
        r.active[id] = nil
        start_trip(r, npc, t, tick)
      end
    elseif t then
      Drive.tick(npc, tick)
      local v = c.vehicle
      local p = (v and v.valid and v.position) or c.position
      local own = (v and v.valid and v) or (npc.parked and npc.parked.valid and npc.parked)
      if own then
        local hp = own.health / own.max_health
        if hp < (t.min_hp or 1) then
          -- (when and doing what it came to its worst: in the seat or out of it, and at what)
          local d = npc.drive
          t.lowest = string.format("%d %s%s @%.0f,%.0f", tick, d and (d.flee and "flee" or d.backing and "backing"
            or d.mend and ("mend " .. d.mend.step) or d.phase) or "-", (v and v.valid) and "" or ", out",
            own.position.x, own.position.y)
        end
        t.min_hp = math.min(t.min_hp or 1, hp)
        t.car = own
      elseif t.car and not t.car.valid and Drive.state(npc) == "driving" then
        t.min_hp = 0   -- destroyed under it (not picked up at the end)
      end
      local d = npc.drive
      if d and d.threat then
        t.threat_ticks = (t.threat_ticks or 0) + 1
        t.threat_speed = (t.threat_speed or 0) + math.abs(v and v.valid and v.speed or 0)
      end
      if d and d.target and d.target.valid then t.shooting_ticks = (t.shooting_ticks or 0) + 1 end
      if t.last then t.driven = t.driven + distance(p, t.last) end
      t.last = {x = p.x, y = p.y}
      if (tick - t.started) % 600 == 0 then   -- where it was every ten seconds, for a trip that fails
        t.trail = t.trail or {}
        t.trail[#t.trail + 1] = string.format("%.0f,%.0f", p.x, p.y)
      end
      local dd = npc.drive
      if dd and dd.door and tick % 15 == 0 and #(t.door_trail or {}) < 80 then   -- and going to or through a door
        t.door_trail = t.door_trail or {}
        t.door_trail[#t.door_trail + 1] = string.format("%d %.1f,%.1f o%.2f v%.2f %s%d/%d", tick, p.x, p.y,
          v and v.valid and v.orientation or -1, v and v.valid and v.speed or 0, dd.door.through and "T" or "-",
          dd.pk or 0, dd.path and #dd.path or 0)
      end
      local state = Drive.state(npc)
      if state == "arrived" and t.ring and not t.back then
        -- out there: home again, in through the door, parked inside (what it noted on the way out kept)
        t.back, t.out_trace, t.out_ticks = true, npc.drive_trace or {}, tick - t.started
        npc.drive_trace = {}
        local rng = game.create_random_generator(t.seed + 3)
        local home = {x = t.start.x + rng(-12, 12), y = t.start.y + rng(-12, 12)}
        npc.drive = nil
        Drive.start(npc, home, 8, tick, {})
        t.home = home
        t.deadline = tick + math.floor(3 * t.length / WALK_SPEED) + 7200
      elseif state == "arrived" then finish_trip(r, npc, t, tick, "arrived")
      elseif state == "failed" then finish_trip(r, npc, t, tick, npc.drive.why or "failed")
      elseif state == nil then finish_trip(r, npc, t, tick, "dropped")
      elseif tick > t.deadline then finish_trip(r, npc, t, tick, "timeout") end
    elseif r.next <= #r.trips then
      local trip = r.trips[r.next]
      r.next = r.next + 1
      trip.land, trip.waiting = boxes(trip, r.spec.mode), true
      ask_land(c.surface, trip.land)
      r.active[id] = trip
    end
  end
end

-- How the trips went: how many arrived, how long they took against walking straight there, how often
-- it stuck, and the trips from `from` on, ten at most (with its last drive events when one did not
-- arrive; one RCON answer holds only so much).
local PAGE = 10
function Range.status(from)
  local r = storage.drive_range
  if not r then return {ok = false, reason = "no_range"} end
  local out = {ok = true, spec = r.spec, tick = game.tick, total = #r.trips, done = #r.results, outcomes = {},
    finished = #r.results >= #r.trips}
  local arrived, ratio, stuck, mends, packs = 0, 0, 0, 0, 0
  local wrecked, hurt, hp_sum, hp_n = 0, 0, 0, 0
  for _, res in ipairs(r.results) do
    if res.vehicle_hp then
      hp_sum, hp_n = hp_sum + res.vehicle_hp, hp_n + 1
      if res.vehicle_hp <= 0 then wrecked = wrecked + 1 end
    end
    if res.driver_hp and res.driver_hp < 1 then hurt = hurt + 1 end
    out.outcomes[res.outcome] = (out.outcomes[res.outcome] or 0) + 1
    if res.outcome == "arrived" then
      arrived = arrived + 1
      ratio = ratio + res.ticks / (res.length / WALK_SPEED)
    end
    stuck = stuck + (res.stuck or 0)
    mends, packs = mends + (res.mends or 0), packs + (res.packs_used or 0)
  end
  out.mends, out.packs_used = mends, packs
  -- the time of all trips, by what the drivers were doing
  local spent, all = {}, 0
  for _, res in ipairs(r.results) do
    for k, n in pairs(res.spent or {}) do spent[k] = (spent[k] or 0) + n; all = all + n end
  end
  out.spent = {}
  for k, n in pairs(spent) do out.spent[k] = math.floor(1000 * n / math.max(1, all)) / 10 end
  out.arrived = arrived
  out.wrecked, out.drivers_hurt = wrecked, hurt
  out.vehicle_hp = hp_n > 0 and math.floor(100 * hp_sum / hp_n) / 100 or nil
  out.walk_ratio = arrived > 0 and math.floor(100 * ratio / arrived) / 100 or nil
  out.stuck_per_trip = #r.results > 0 and math.floor(100 * stuck / #r.results) / 100 or nil
  from = math.max(1, math.floor(tonumber(from) or 1))
  out.from, out.results = from, {}
  for i = from, math.min(#r.results, from + PAGE - 1) do out.results[#out.results + 1] = r.results[i] end
  return out
end

return Range
