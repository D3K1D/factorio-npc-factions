-- What an engineer knows of the world beyond its own buildings (the user's rules, 2026-10-13): roughly
-- where everyone started (their spawn, give or take), and the rest only by sight: its own eyes, its
-- radars, and what its friends (engineers and players allied with it) see with theirs.
local Intel = {}

local EYES = 120         -- what an engineer or a player sees round itself (a screen and a bit)
local RADAR_LIVE = 96    -- a powered radar's live view (the chunks round it)
local RADARS_EVERY = 3600
local FUZZ_MIN, FUZZ_MAX = 16, 48

local RADAR_SCAN, SCAN_EVERY = 448, 3600   -- a radar's slow sweep of the land round it
local WATCH_TYPES = {"unit-spawner", "turret", "ammo-turret", "electric-turret", "fluid-turret"}

local function friendly(force, other)
  return force == other or (force.get_friend(other) and other.get_friend(force))
end

local function near(a, b, r)
  local dx, dy = a.x - b.x, a.y - b.y
  return dx * dx + dy * dy <= r * r
end

-- The radars of a force, looked up now and then.
local function radars(force, surface, tick)
  storage.intel_radars = storage.intel_radars or {}
  local key = force.name .. "@" .. surface.index
  local cached = storage.intel_radars[key]
  if not cached or tick >= cached.until_tick then
    cached = {until_tick = tick + RADARS_EVERY,
      list = surface.find_entities_filtered{type = "radar", force = force}}
    storage.intel_radars[key] = cached
  end
  return cached.list
end

-- Every pair of eyes on its side: {position, reach} for itself, friendly engineers and players, and
-- the powered radars of all of them.
function Intel.eyes(npc, tick)
  local c = npc.character
  local out = {}
  if not (c and c.valid) then return out end
  local surface, force = c.surface, npc.force
  local forces = {[force.name] = force}
  out[#out + 1] = {c.position, EYES, "self"}
  for _, other in pairs(storage.npcs or {}) do
    local body = other ~= npc and other.character
    if body and body.valid and body.surface == surface and friendly(force, other.force) then
      out[#out + 1] = {body.position, EYES}
      forces[other.force.name] = other.force
    end
  end
  for _, player in pairs(game.connected_players or {}) do
    local body = player.character
    if body and body.valid and body.surface == surface and friendly(force, player.force) then
      out[#out + 1] = {body.position, EYES}
      forces[player.force.name] = player.force
    end
  end
  for _, f in pairs(forces) do
    for _, r in ipairs(radars(f, surface, tick or game.tick)) do
      if r.valid and (r.energy or 0) > 0 then out[#out + 1] = {r.position, RADAR_LIVE, "radar"} end
    end
  end
  return out
end

-- Whether its side sees a place now.
function Intel.sees(npc, p, tick)
  for _, eye in ipairs(Intel.eyes(npc, tick)) do
    if near(eye[1], p, eye[2]) then return true end
  end
  return false
end

local function at_war(force, other)
  return other ~= force and other.name ~= "neutral"
    and not force.get_friend(other) and not other.get_friend(force)
    and not force.get_cease_fire(other) and not other.get_cease_fire(force)
end

-- What its friends and radars see of nests, worms and the turrets of those it is at war with goes into
-- its memory (note: Routes.note) as if it had seen it itself (its own eyes: Routes.look); now and then
-- its radars' slow sweep shows the land farther round them.
function Intel.watch(npc, tick, note)
  local c = npc.character
  if not (c and c.valid) then return end
  local sweep = tick >= (npc.next_radar_sweep or 0)
  if sweep then npc.next_radar_sweep = tick + SCAN_EVERY end
  for _, eye in ipairs(Intel.eyes(npc, tick)) do
    if eye[3] ~= "self" then
      local reach = eye[3] == "radar" and sweep and RADAR_SCAN or eye[2]
      for _, e in ipairs(c.surface.find_entities_filtered{type = WATCH_TYPES, position = eye[1], radius = reach}) do
        if e.valid and (e.force.name == "enemy" or at_war(npc.force, e.force)) then note(npc, e) end
      end
    end
  end
end

-- Roughly where someone started, as everyone knows it: their spawn, off by 16 to 48 tiles in a
-- direction that is the same each time for the same pair (it learns the real place by going there).
function Intel.roughly(npc, key, at)
  local h = 0
  for i = 1, #key do h = (h * 31 + key:byte(i)) % 1000003 end
  h = (h * 7919 + npc.id * 104729) % 1000003
  local angle = (h % 360) * math.pi / 180
  local d = FUZZ_MIN + (math.floor(h / 360) % (FUZZ_MAX - FUZZ_MIN + 1))
  return {x = at.x + math.cos(angle) * d, y = at.y + math.sin(angle) * d}
end

-- Where a player started (their force's spawn), roughly.
function Intel.spawn_of(npc, player)
  local surface = npc.character.surface
  local ok, at = pcall(function() return player.force.get_spawn_position(surface) end)
  if not ok or not at then at = {x = 0, y = 0} end
  return Intel.roughly(npc, "player:" .. player.index, at)
end

Intel.EYES = EYES
return Intel
