-- The drive lab (tests only; the user, 2026-10-14): a player drives about a map while what they do is
-- written down, to learn from how a person drives round trees, rocks and water. A car with coal (and
-- more coal in its trunk) is put beside each player who joins, a chest of other vehicles and fuels by
-- it. About fifteen times a second, for each player in a vehicle: where it is, its heading and speed,
-- the player's keys (accelerate, brake, reverse; left, right), its health, the fuel burning, and what
-- is ahead (trees, rocks, cliffs within a cone, water); and every hit the vehicle takes, every tree or
-- rock it breaks. Written to script-output/drive_lab.jsonl, a JSON object a line.
-- While it runs nothing else ticks (control.lua): no engineers.
local Lab = {}

local SAMPLE_EVERY, FLUSH_EVERY = 4, 300
local AHEAD, SIDE, KEPT = 26, 6, 5
local FILE = "drive_lab.jsonl"
-- And twice a second what is about each player (the user: "all the objects around the vehicle within a
-- large radius"), to drive_lab_world.jsonl: every tree, rock and cliff within WORLD_RADIUS the first
-- time it comes near (position and size; the land does not move), the biters, nests and worms there
-- each time, and water by 4x4 tiles, each cell once.
local WORLD_EVERY, WORLD_RADIUS, WORLD_FILE = 30, 48, "drive_lab_world.jsonl"
local ACCEL, TURN = {}, {}

function Lab.setup()
  if storage.drive_lab then return {ok = false, reason = "lab_running"} end
  storage.drive_lab = {given = {}, lines = {}, samples = 0, hits = 0, broke = 0,
    world = {seen = {}, wet = {}, lines = {}, snaps = 0, objects = 0}}
  if remote.interfaces.freeplay then
    if remote.interfaces.freeplay.set_skip_intro then remote.call("freeplay", "set_skip_intro", true) end
    if remote.interfaces.freeplay.set_disable_crashsite then remote.call("freeplay", "set_disable_crashsite", true) end
  end
  helpers.write_file(FILE, "", false, 0)
  helpers.write_file(WORLD_FILE, "", false, 0)
  return {ok = true}
end

local function names()
  if next(ACCEL) then return end
  for k, v in pairs(defines.riding.acceleration) do ACCEL[v] = k end
  for k, v in pairs(defines.riding.direction) do TURN[v] = k end
end

local function put(lab, t)
  lab.lines[#lab.lines + 1] = helpers.table_to_json(t)
end

local function flush(lab)
  if #lab.lines > 0 then
    helpers.write_file(FILE, table.concat(lab.lines, "\n") .. "\n", true, 0)
    lab.lines = {}
  end
  local w = lab.world
  if w and #w.lines > 0 then
    helpers.write_file(WORLD_FILE, table.concat(w.lines, "\n") .. "\n", true, 0)
    w.lines = {}
  end
end

local function tenth(v) return math.floor(v * 10) / 10 end
local function look_about(lab, player, tick)
  local w = lab.world
  local v = player.vehicle
  local pos = (v and v.valid and v.position) or (player.character and player.character.valid and player.character.position)
  if not pos then return end
  local s = player.surface
  local line = {t = tick, p = player.index, x = tenth(pos.x), y = tenth(pos.y), car = v and v.valid and v.name or nil,
    new = {}, enemies = {}, wet = {}}
  for _, e in ipairs(s.find_entities_filtered{position = pos, radius = WORLD_RADIUS,
      type = {"tree", "simple-entity", "cliff"}}) do
    local key = string.format("%.1f,%.1f", e.position.x, e.position.y)
    if not w.seen[key] then
      w.seen[key] = true
      local b = e.bounding_box
      line.new[#line.new + 1] = {e.type == "tree" and "tree" or e.name, tenth(e.position.x), tenth(e.position.y),
        tenth(b.right_bottom.x - b.left_top.x), tenth(b.right_bottom.y - b.left_top.y)}
      w.objects = w.objects + 1
    end
  end
  for _, e in ipairs(s.find_entities_filtered{position = pos, radius = WORLD_RADIUS, force = "enemy",
      type = {"unit", "unit-spawner", "turret"}}) do
    line.enemies[#line.enemies + 1] = {e.name, tenth(e.position.x), tenth(e.position.y)}
  end
  local cells = math.ceil(2 * WORLD_RADIUS / 4)
  local cx0, cy0 = math.floor((pos.x - WORLD_RADIUS) / 4), math.floor((pos.y - WORLD_RADIUS) / 4)
  for cx = cx0, cx0 + cells do
    for cy = cy0, cy0 + cells do
      local key = cx .. "," .. cy
      if w.wet[key] == nil then
        local n = s.count_tiles_filtered{area = {{cx * 4, cy * 4}, {cx * 4 + 4, cy * 4 + 4}},
          collision_mask = "water_tile"}
        w.wet[key] = n
        if n > 0 then line.wet[#line.wet + 1] = {cx * 4, cy * 4, n} end
      end
    end
  end
  w.lines[#w.lines + 1] = helpers.table_to_json(line)
  w.snaps = w.snaps + 1
end

-- A car with coal and coal in its trunk beside the player; a chest of a tank, better fuels and repair
-- packs by it.
local function give(lab, player)
  local c = player.character
  local s = c.surface
  local at = s.find_non_colliding_position("car", {c.position.x + 4, c.position.y}, 20, 1)
  if not at then return end
  local car = s.create_entity{name = "car", position = at, force = player.force}
  car.get_fuel_inventory().insert{name = "coal", count = 50}
  car.get_inventory(defines.inventory.car_trunk).insert{name = "coal", count = 200}
  car.get_inventory(defines.inventory.car_ammo).insert{name = "piercing-rounds-magazine", count = 100}
  -- (the user: ammo to shoot off biters that give chase, and repair packs)
  for _, item in ipairs({{"submachine-gun", 1}, {"piercing-rounds-magazine", 200}, {"repair-pack", 50}}) do
    player.insert{name = item[1], count = item[2]}
  end
  local box = s.find_non_colliding_position("wooden-chest", {at.x + 4, at.y}, 20, 1)
  if box then
    local chest = s.create_entity{name = "wooden-chest", position = box, force = player.force}
    for _, item in ipairs({{"tank", 1}, {"car", 1}, {"coal", 400}, {"solid-fuel", 100}, {"rocket-fuel", 40},
        {"nuclear-fuel", 4}, {"repair-pack", 50}, {"piercing-rounds-magazine", 200}, {"cannon-shell", 50},
        {"explosive-cannon-shell", 20}}) do
      chest.insert{name = item[1], count = item[2]}
    end
  end
  lab.given[player.index] = true
  player.print("Drive lab: a car with coal (more coal in its trunk, its gun loaded) is beside you; you have a "
    .. "submachine gun, magazines and repair packs; a tank, cannon shells, better fuels and more ammo are in the "
    .. "chest by it. Everything you do in a vehicle is being written down.")
end

-- What is ahead of the vehicle: trees, rocks and cliffs within AHEAD tiles and SIDE to either side,
-- nearest first, as {kind, how far ahead, how far to the side (+ right)}; and water ahead.
local function ahead(v)
  local s, p = v.surface, v.position
  local o = v.orientation * 2 * math.pi
  local fx, fy = math.sin(o), -math.cos(o)
  local found = {}
  for _, e in ipairs(s.find_entities_filtered{position = {p.x + fx * AHEAD / 2, p.y + fy * AHEAD / 2},
      radius = AHEAD / 2 + SIDE, type = {"tree", "simple-entity", "cliff"}}) do
    local dx, dy = e.position.x - p.x, e.position.y - p.y
    local along, side = dx * fx + dy * fy, dx * -fy + dy * fx
    if along > 0 and along < AHEAD and math.abs(side) < SIDE then
      found[#found + 1] = {e.type == "tree" and "tree" or e.type == "cliff" and "cliff" or e.name,
        math.floor(along * 10) / 10, math.floor(side * 10) / 10}
    end
  end
  table.sort(found, function(a, b) return a[2] < b[2] end)
  while #found > KEPT do table.remove(found) end
  local water = -1
  for d = 2, AHEAD, 2 do
    if s.count_tiles_filtered{position = {p.x + fx * d, p.y + fy * d}, radius = 1.5,
        collision_mask = "water_tile", limit = 1} > 0 then water = d; break end
  end
  return found, water
end

function Lab.tick(tick)
  local lab = storage.drive_lab
  names()
  for _, player in pairs(game.connected_players) do
    if not lab.given[player.index] and player.character and player.character.valid then give(lab, player) end
    if lab.world and tick % WORLD_EVERY == 0 then look_about(lab, player, tick) end
    local v = player.vehicle
    if tick % SAMPLE_EVERY == 0 and v and v.valid and v.type ~= "locomotive" then
      local r = v.riding_state or {}
      local found, water = ahead(v)
      local burning = v.burner and v.burner.currently_burning
      local fuel = burning and (type(burning.name) == "string" and burning.name or burning.name and burning.name.name)
      -- biters about: how many within 40 tiles and the nearest; and whether it is shooting
      local near, nearest = 0, nil
      for _, e in ipairs(v.surface.find_entities_filtered{position = v.position, radius = 40, force = "enemy",
          type = {"unit", "unit-spawner", "turret"}}) do
        near = near + 1
        local dd = math.sqrt((e.position.x - v.position.x) ^ 2 + (e.position.y - v.position.y) ^ 2)
        if not nearest or dd < nearest[2] then nearest = {e.name, math.floor(dd * 10) / 10} end
      end
      local shooting = player.shooting_state and player.shooting_state.state ~= defines.shooting.not_shooting
      put(lab, {t = tick, p = player.index, v = v.name, x = math.floor(v.position.x * 10) / 10,
        y = math.floor(v.position.y * 10) / 10, o = math.floor(v.orientation * 1000) / 1000,
        s = math.floor(v.speed * 1000) / 1000, a = ACCEL[r.acceleration], d = TURN[r.direction],
        hp = math.floor(v.health or 0), f = fuel, ahead = found, water = water,
        enemies = near > 0 and near or nil, nearest = nearest, shoot = shooting or nil})
      lab.samples = lab.samples + 1
    end
  end
  if tick % FLUSH_EVERY == 0 then flush(lab) end
end

local function driven_by_player(v)
  if not (v and v.valid and v.type == "car") then return nil end
  local d = v.get_driver()
  if not d then return nil end
  if d.object_name == "LuaPlayer" then return d end
  return d.player
end

function Lab.on_entity_damaged(event)
  local lab = storage.drive_lab
  if not lab then return end
  local v = event.entity
  local player = driven_by_player(v)
  if not player then return end
  put(lab, {t = event.tick, p = player.index, hit = event.cause and event.cause.valid and event.cause.name or "?",
    kind = event.damage_type and event.damage_type.name, damage = math.floor(event.final_damage_amount * 10) / 10,
    s = math.floor(v.speed * 1000) / 1000, hp = math.floor(v.health)})
  lab.hits = lab.hits + 1
end

function Lab.on_entity_died(event)
  local lab = storage.drive_lab
  if not lab then return end
  local player = driven_by_player(event.cause)
  if not player then return end
  put(lab, {t = event.tick, p = player.index, broke = event.entity.name,
    s = math.floor(event.cause.speed * 1000) / 1000, hp = math.floor(event.cause.health)})
  lab.broke = lab.broke + 1
end

function Lab.status()
  local lab = storage.drive_lab
  if not lab then return {ok = false, reason = "no_lab"} end
  local players = {}
  for _, player in pairs(game.connected_players) do
    local v = player.vehicle
    players[#players + 1] = {name = player.name, vehicle = v and v.valid and v.name or nil,
      speed = v and v.valid and math.floor(v.speed * 1000) / 1000 or nil}
  end
  return {ok = true, tick = game.tick, samples = lab.samples, hits = lab.hits, broke = lab.broke, players = players,
    snapshots = lab.world and lab.world.snaps, objects = lab.world and lab.world.objects}
end

return Lab
