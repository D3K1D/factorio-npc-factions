-- The engineers' boosts (the user, 2026-10-29: "simple cheats, improve them immensely but not too crazy"): quicker
-- hands at mining, more pockets, more health, and a quick start, each a runtime setting the host tunes. They go on
-- every engineer's body, never a player's: once for each new body (a spawn, a respawn, a swap), every LOOK after, and
-- at once when a setting changes (Boosts.changed). A field another mod also sets keeps that mod's part: ours is added
-- on top of what was there, and a change made by someone else since is kept as theirs; turned down, only ours comes
-- off. Hand crafting has its own setting already (npc-factions-hand-crafting-speed, designed_start.lua: quicker for
-- what its machines do not stock yet).
local Armory = require("scripts.armory")
local Boosts = {}

Boosts.LOOK = 600          -- ticks between looks that a body still has them
Boosts.KIT_WITHIN = 3600   -- the quick start is for an engineer this new (its first spawn), never an old one

local function try(f) local ok, v = pcall(f); if ok then return v end end
local function setting(name, default)
  local v = try(function() return settings.global[name].value end)
  if v == nil then return default end
  return v
end

-- The character field each boost sets, and its value for a body (0: none).
Boosts.FIELDS = {
  character_mining_speed_modifier = function()
    return math.max(0, setting("npc-factions-boost-mining-speed", 2) - 1)
  end,
  character_inventory_slots_bonus = function()
    return math.max(0, math.floor(setting("npc-factions-boost-inventory", 30)))
  end,
  character_health_bonus = function(c)   -- (the setting is a multiple of the body's own health)
    local base = try(function() return c.prototype.get_max_health() end) or try(function() return c.prototype.max_health end)
      or 250
    return math.max(0, (setting("npc-factions-boost-health", 2.5) - 1) * base)
  end,
}

-- The quick start, as a quick-start mod gives one: what it holds at size 1, counting what it already carries (its
-- freeplay kit's drill, furnace, plates and magazines among them); a gun only if it has none.
Boosts.KIT = {{"burner-mining-drill", 8}, {"stone-furnace", 8}, {"iron-plate", 50}, {"copper-plate", 20}}
Boosts.KIT_ROUNDS = 50
-- Fuel to start each burner building in the kit (its own and its freeplay ones together are many more burners than a
-- freeplay start: unfuelled, a copper stage waited 25 minutes with its drill and furnace out of fuel, opening-pace).
Boosts.FUEL_PER_BURNER = 10

-- Whether an item places a building that burns fuel.
local function burner(name)
  return try(function() return prototypes.item[name].place_result.burner_prototype end) ~= nil
end

-- The fuel it starts them with: coal where the game has it, else the most energetic chemical fuel.
function Boosts.fuel()
  if prototypes.item["coal"] then return "coal" end
  local best, most
  for name, item in pairs(prototypes.item) do
    local chemical = try(function()   -- (2.1: fuel_categories; older: fuel_category)
      local cats = item.fuel_categories
      if type(cats) == "table" then return cats.chemical ~= nil or cats[1] == "chemical" end
      return item.fuel_category == "chemical"
    end)
    local value = chemical and try(function() return item.fuel_value end)
    if value and value > 0 and (not most or value > most or value == most and name < best) then best, most = name, value end
  end
  return best
end

-- A gun it can shoot from the start (the base game's pistol, else the weakest gun the mods have) and rounds for it.
function Boosts.sidearm()
  local gun = prototypes.item["pistol"] and "pistol"
  if not gun then
    local guns = try(function() return Armory.catalog().guns end) or {}
    gun = guns[#guns] and guns[#guns].name   -- (the catalog lists them best first)
  end
  if not gun then return nil end
  local rounds = prototypes.item["firearm-magazine"] and "firearm-magazine"
  local fits = try(function() return Armory.ammo_for(gun) end) or {}
  if rounds then
    local ok = false
    for _, name in ipairs(fits) do if name == rounds then ok = true end end
    if not ok then rounds = nil end   -- (only rounds the armory says fit)
  end
  rounds = rounds or fits[#fits]   -- (the weakest that fits: a start, not an arsenal)
  return gun, rounds
end

-- Items whose making opens a technology its force has not researched yet (Factorio 2's research triggers: steam
-- power opens once 50 iron plates are made, electronics once 10 copper plates). Handed over, they are never made, and
-- the tech tree stays shut, so the kit leaves them for it to make. Read from the game's own technologies (mods too).
function Boosts.trigger_items(force)
  local out = {}
  for name, tech in pairs(try(function() return prototypes.technology end) or {}) do
    local trigger = try(function() return tech.research_trigger end)
    local item = trigger and trigger.type == "craft-item" and trigger.item
    if type(item) == "table" then item = item.name end
    local own = try(function() return force.technologies[name] end)
    if item and not (own and own.researched) then out[item] = name end
  end
  return out
end

local function has_gun(c)
  for _, which in ipairs({defines.inventory.character_guns, defines.inventory.character_main}) do
    local inv = try(function() return c.get_inventory(which) end)
    for i = 1, inv and #inv or 0 do
      local s = inv[i]
      if s.valid_for_read and try(function() return s.prototype.type end) == "gun" then return true end
    end
  end
  return false
end

-- The quick start, once, for an engineer at its first spawn: what it got, or nil and why not.
function Boosts.quick_start(npc, tick)
  if npc.quick_start then return nil, "given" end
  local c = npc.character
  if not (c and c.valid) then return nil, "no_body" end
  if storage.test then npc.quick_start = {skipped = "fixture"}; return nil, "fixture" end
  if not setting("npc-factions-quick-start", true) then return nil, "off" end   -- (asked again if turned on in time)
  if tick - (npc.created_tick or 0) > Boosts.KIT_WITHIN then npc.quick_start = {skipped = "not_new"}; return nil, "not_new" end
  local size = math.max(0, setting("npc-factions-quick-start-size", 1))
  local given, spilled, left, unplaced = {}, {}, {}, {}
  local triggers = Boosts.trigger_items(c.force)
  -- (up to `count` held: what it carries already counts; what does not fit goes on the ground at its feet)
  local function give(name, count)
    if not (name and prototypes.item[name]) then return end
    if triggers[name] then left[name] = triggers[name]; return end   -- (it makes them itself: the tech opens)
    local held = try(function() return c.get_item_count(name) end) or 0
    count = math.floor(count + 0.5) - held
    if count <= 0 then return end
    local n = try(function() return c.insert{name = name, count = count} end) or 0
    if n > 0 then given[name] = (given[name] or 0) + n end
    if n < count then
      local ok, placed = pcall(function()
        return c.surface.spill_item_stack{position = c.position, stack = {name = name, count = count - n}} end)
      if ok and type(placed) == "table" and #placed > 0 then spilled[name] = count - n
      else unplaced[name] = count - n end   -- (no room on the ground either: said, not counted as given)
    end
  end
  local burners = 0
  for _, entry in ipairs(Boosts.KIT) do
    give(entry[1], entry[2] * size)
    if burner(entry[1]) then burners = burners + entry[2] * size end
  end
  if burners > 0 then give(Boosts.fuel(), burners * Boosts.FUEL_PER_BURNER) end
  local gun, rounds = Boosts.sidearm()
  if gun and not has_gun(c) then give(gun, 1) end
  give(rounds, Boosts.KIT_ROUNDS * size)
  npc.quick_start = {tick = tick, size = size, items = given, spilled = next(spilled) and spilled or nil,
    left_for_triggers = next(left) and left or nil, unplaced = next(unplaced) and unplaced or nil}
  return given
end

-- Puts the boosts on its body (and gives the quick start once).
function Boosts.keep(npc, tick)
  local c = npc.character
  if not (c and c.valid) then return end
  local body = c.unit_number
  if npc.boost_body == body and tick < (npc.boost_next or 0) then return end
  npc.boost_body, npc.boost_next = body, tick + Boosts.LOOK
  local set = npc.boost_set
  local mine = set and set.body == body and set.fields or {}
  local now = {}
  for field, value_of in pairs(Boosts.FIELDS) do
    local want = value_of(c)
    local have = try(function() return c[field] end)
    if type(have) == "number" then
      -- (what is not ours: all of it on a body it has not set yet; else what it wrote less its own part; or, if
      -- someone has written the field since, what they wrote, all theirs: a mod sets a field outright)
      local was = mine[field]
      local theirs = not was and have or (have == was.wrote and was.wrote - was.ours or have)
      local write = theirs + want
      if have ~= write then pcall(function() c[field] = write end) end
      if want ~= 0 or was then now[field] = {ours = want, wrote = write} end
    end
  end
  npc.boost_set = {body = body, fields = now}
  if not npc.quick_start then Boosts.quick_start(npc, tick) end
end

-- Engineers already in a game when the quick start came in (a mod update): none for them, however young.
function Boosts.migrate()
  for _, npc in pairs(storage.npcs or {}) do npc.quick_start = npc.quick_start or {skipped = "before"} end
end

-- A setting changed: every body is looked at again on its next tick.
function Boosts.changed()
  for _, npc in pairs(storage.npcs or {}) do npc.boost_next = 0 end
end

return Boosts
