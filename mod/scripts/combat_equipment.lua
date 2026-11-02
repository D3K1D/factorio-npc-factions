-- Native equipment actions only. This module never creates vehicles, robots,
-- ammunition, fuel, or damage. The character must already own every item.
local Squads = require("scripts.combat_squads")
local Equipment = {}

local ROBOT_CAPSULES = {"destroyer-capsule", "distractor-capsule", "defender-capsule"}
local POISON_CAPSULES = {"poison-capsule"}   -- on worms, as players clear them (two or so on a group)
local GRENADES = {"cluster-grenade", "grenade"}
local SLOWDOWN = {"slowdown-capsule"}
local COVER_ROBOTS = {"distractor-capsule", "defender-capsule"}   -- something else for them to shoot at
-- The playbook for what it throws (players' habits): grenades into a group or onto a nest, never
-- closer than their blast; poison on worms and on big groups; slowdown on a person it chases or who
-- chases it; robots on anything worth them. Each: names (best first), range, cooldown, mode, and how
-- close is too close.
local BLAST = 8
function Equipment.throws(target, d, count)
  local out = {}
  local kind = target.type
  local people = kind == "character" or kind == "car" or kind == "spider-vehicle"
  if kind == "turret" then out[#out + 1] = {POISON_CAPSULES, 23, 180, "poison_capsule", 0} end
  if kind == "unit-spawner" or (kind == "unit" and count >= 3) or (people and count >= 2) then
    out[#out + 1] = {GRENADES, 15, 60, "grenade", BLAST}
  end
  if kind == "unit" and count >= 6 then out[#out + 1] = {POISON_CAPSULES, 23, 300, "poison_capsule", BLAST} end
  if people then
    out[#out + 1] = {SLOWDOWN, 25, 300, "slowdown_capsule", 0}
    out[#out + 1] = {GRENADES, 15, 90, "grenade", BLAST}
  end
  out[#out + 1] = {ROBOT_CAPSULES, 16, 300, "robot_capsule", 0}
  return out
end
-- Breaking contact: a slowdown capsule on whoever chases it, or a distractor to keep them busy.
function Equipment.cover_throws(target)
  local people = target.type == "character" or target.type == "car" or target.type == "spider-vehicle"
  local out = {}
  if people then out[#out + 1] = {SLOWDOWN, 25, 300, "slowdown_capsule", 0} end
  out[#out + 1] = {COVER_ROBOTS, 16, 300, "robot_capsule", 0}
  return out
end
local TWO_PI = 2 * math.pi
local VEHICLE_PROBE_TICKS = 24
local CAPSULE_PROBE_TICKS = 18

local function distance(a, b)
  local dx, dy = a.x - b.x, a.y - b.y
  return math.sqrt(dx * dx + dy * dy)
end

local function copy_position(p) return {x = p.x, y = p.y} end

local function state(npc)
  npc.combat_equipment = npc.combat_equipment or {boardings = 0, capsules_spent = 0,
    capsule_uses = 0, vehicle_fire_ticks = 0, blocked_drive_ticks = 0, mode = "idle"}
  return npc.combat_equipment
end

local function stop(vehicle, character)
  if vehicle and vehicle.valid and vehicle.type == "car" then
    vehicle.riding_state = {acceleration = defines.riding.acceleration.braking,
      direction = defines.riding.direction.straight}
  end
  if character and character.valid then
    character.shooting_state = {state = defines.shooting.not_shooting, position = character.position}
  end
end

local function fuel_ready(vehicle)
  local burner = vehicle.burner
  if not burner then return false end
  return (burner.remaining_burning_fuel or 0) > 0 or not burner.inventory.is_empty()
end

-- Whether the burner takes this item as fuel (Factorio 2.1: an item lists its fuel categories).
local function burns(burner, item)
  for _, c in ipairs(item.prototype.fuel_categories or {}) do
    if burner.fuel_categories[c] then return true end
  end
  return false
end

local function fuel_available(character, vehicle)
  if fuel_ready(vehicle) then return true end
  local burner, main = vehicle.burner, character.get_main_inventory()
  if not burner or not main then return false end
  for i = 1, #main do
    local item = main[i]
    if item.valid_for_read and burns(burner, item) then return true end
  end
  return false
end

local function fuel_from_character(character, vehicle)
  if fuel_ready(vehicle) then return true end
  local burner, main = vehicle.burner, character.get_main_inventory()
  if not burner or not main then return false end
  for i = 1, #main do
    local item = main[i]
    if item.valid_for_read and burns(burner, item)
        and burner.inventory.can_insert{name = item.name, count = 1} then
      -- Transfer exactly one real item. The burner decides when to consume it.
      local name, quality = item.name, item.quality and item.quality.name or "normal"
      local removed = main.remove{name = name, quality = quality, count = 1}
      if removed == 1 then
        local inserted = burner.inventory.insert{name = name, quality = quality, count = 1}
        if inserted ~= 1 then main.insert{name = name, quality = quality, count = 1} end
      end
      if fuel_ready(vehicle) then return true end
    end
  end
  return false
end

local function ammo_units(stack)
  if not stack or not stack.valid_for_read then return 0 end
  return math.max(0, (stack.ammo or 0) + (stack.count - 1) * (stack.prototype.magazine_size or 1))
end

local function gun_accepts(gun, stack)
  if not gun or not stack or not stack.valid_for_read or stack.prototype.type ~= "ammo" then return false end
  local category = stack.prototype.ammo_category
  if not category then return false end
  for _, name in pairs(gun.attack_parameters.ammo_categories or {}) do
    if name == category.name then return true end
  end
  return false
end

local function vehicle_weapon(vehicle, character)
  local guns = vehicle.prototype.indexed_guns or {}
  local ammo = vehicle.get_inventory(defines.inventory.car_ammo)
  if not ammo then return nil end
  local main = character.get_main_inventory()
  for i = 1, math.min(#guns, #ammo) do
    local stack = ammo[i]
    if ammo_units(stack) == 0 and main then
      for j = 1, #main do
        if gun_accepts(guns[i], main[j]) then
          stack.transfer_stack(main[j])
          if ammo_units(stack) > 0 then break end
        end
      end
    end
    if gun_accepts(guns[i], stack) and ammo_units(stack) > 0 then
      return {ready = true, slot = i, gun = guns[i].name, ammo_name = stack.name,
        range = guns[i].attack_parameters.range or 0, ammo_units = ammo_units(stack),
        category = stack.prototype.ammo_category.name}
    end
  end
  return nil
end

local function vehicle_ammo_available(vehicle, character)
  local guns, ammo = vehicle.prototype.indexed_guns or {}, vehicle.get_inventory(defines.inventory.car_ammo)
  if not ammo then return false end
  local main = character.get_main_inventory()
  for i = 1, math.min(#guns, #ammo) do
    if gun_accepts(guns[i], ammo[i]) and ammo_units(ammo[i]) > 0 then return true end
    if main and not ammo[i].valid_for_read then
      for j = 1, #main do
        if gun_accepts(guns[i], main[j]) and ammo_units(main[j]) > 0 then return true end
      end
    end
  end
  return false
end

local function owned_vehicle(npc, character, tick, status)
  local current = character.vehicle
  if current and current.valid and current.type == "car" and current.force == npc.force
      and current.get_driver() == character then return current end
  if current and current.valid then return nil end
  local cached = status.vehicle_candidate
  if cached and cached.valid and cached.type == "car" and cached.force == npc.force
      and not cached.get_driver() and not cached.get_passenger()
      and cached.surface == character.surface
      and distance(character.position, cached.position) <= 3.5 then return cached end
  status.vehicle_candidate = nil
  local previous = status.vehicle_probe_position
  if not cached and tick < (status.next_vehicle_probe_tick or 0)
      and status.vehicle_probe_surface == character.surface
      and previous and distance(character.position, previous) < 1.5 then return nil end
  local nearest, best
  for _, vehicle in ipairs(character.surface.find_entities_filtered{
      position = character.position, radius = 4, type = "car", force = npc.force}) do
    local d = distance(character.position, vehicle.position)
    if vehicle.valid and not vehicle.get_driver() and not vehicle.get_passenger()
        and d <= 3.5 and (not nearest or d < best) then
      nearest, best = vehicle, d
    end
  end
  status.vehicle_candidate = nearest
  status.vehicle_probe_surface = character.surface
  status.vehicle_probe_position = copy_position(character.position)
  status.next_vehicle_probe_tick = tick + VEHICLE_PROBE_TICKS
  return nearest
end

local function friendly_lane(npc, vehicle, dx, dy)
  -- Brake early instead of trusting the car collision/damage model around NPCs.
  for _, entity in ipairs(vehicle.surface.find_entities_filtered{
      position = vehicle.position, radius = 9, type = {"character", "car", "spider-vehicle"}}) do
    if entity.valid and entity ~= npc.character and entity ~= vehicle
        and (entity.force == npc.force or npc.force.get_friend(entity.force)
          or entity.force.get_friend(npc.force) or npc.force.get_cease_fire(entity.force)
          or entity.force.get_cease_fire(npc.force)) then
      local x, y = entity.position.x - vehicle.position.x, entity.position.y - vehicle.position.y
      local along, across = x * dx + y * dy, math.abs(x * dy - y * dx)
      if along >= -1 and along <= 8 and across <= 3 then return true end
    end
  end
  return false
end

local function steer(vehicle, target, range, min_fire_distance, urgent_retreat,
    blocked_forward, blocked_reverse)
  local d = distance(vehicle.position, target.position)
  local stop_at = math.max(9, math.min(range * 0.8, range - 2))
  local dx, dy = target.position.x - vehicle.position.x, target.position.y - vehicle.position.y
  local wanted = (math.atan2(dx, -dy) / TWO_PI) % 1
  local delta = (wanted - vehicle.orientation + 0.5) % 1 - 0.5
  local forward_turn, reverse_turn = defines.riding.direction.straight,
      defines.riding.direction.straight
  if delta > 0.025 then
    forward_turn, reverse_turn = defines.riding.direction.right, defines.riding.direction.left
  elseif delta < -0.025 then
    forward_turn, reverse_turn = defines.riding.direction.left, defines.riding.direction.right
  end
  local retreat = urgent_retreat or d < math.max(min_fire_distance + 3, stop_at * 0.6)
  if retreat then
    -- A car cannot pivot by writing its orientation. Steering while physically
    -- backing supplies the turn that a stopped, off-axis tank cannot make.
    local target_in_front = math.cos(delta * TWO_PI) >= -0.1
    if target_in_front and not blocked_reverse then
      vehicle.riding_state = {acceleration = defines.riding.acceleration.reversing,
        direction = reverse_turn}
      return reverse_turn == defines.riding.direction.straight
          and "reversing_to_range" or "reverse_turn"
    end
    if not blocked_forward then
      vehicle.riding_state = {acceleration = defines.riding.acceleration.accelerating,
        direction = target_in_front and forward_turn or defines.riding.direction.straight}
      return "forward_escape"
    end
    if not blocked_reverse then
      vehicle.riding_state = {acceleration = defines.riding.acceleration.reversing,
        direction = reverse_turn}
      return "reverse_turn"
    end
    vehicle.riding_state = {acceleration = defines.riding.acceleration.braking,
      direction = defines.riding.direction.straight}
    return "ally_in_lane"
  end
  if blocked_forward or d <= stop_at then
    vehicle.riding_state = {acceleration = defines.riding.acceleration.braking,
      direction = defines.riding.direction.straight}
    return blocked_forward and "ally_in_lane" or "in_range"
  end
  vehicle.riding_state = {acceleration = defines.riding.acceleration.accelerating,
    direction = forward_turn}
  return "approaching"
end

-- Its own side's buildings (and its friends') in a blast at p: a grenade there would hit them (O-9: never onto its own
-- creep turrets, as the user's clear took care).
local NOT_BUILDINGS = {character = true, unit = true, ["combat-robot"] = true, ["construction-robot"] = true,
  ["logistic-robot"] = true, ["entity-ghost"] = true, ["tile-ghost"] = true, ["item-entity"] = true, corpse = true,
  projectile = true, stream = true, ["smoke-with-trigger"] = true, ["sticker"] = true}
function Equipment.own_in_blast(character, p, r)
  for _, e in ipairs(character.surface.find_entities_filtered{position = p, radius = r}) do
    if e.valid and not NOT_BUILDINGS[e.type] and e.force and e.force.name ~= "neutral"
        and (e.force == character.force or character.force.get_friend(e.force)) then return true end
  end
  return false
end

local function use_capsule(npc, tick, target, character, status, names, range, cooldown, mode, blast)
  if distance(character.position, target.position) > range then return false end
  if blast and blast > 0 and Equipment.own_in_blast(character, target.position, blast) then
    status.reason = "capsule_own_in_blast"; return false
  end
  status.next_capsule = status.next_capsule or {}
  status.next_probe = status.next_probe or {}
  if tick < (status.next_capsule[mode] or 0) then return false end
  local inventory = character.get_main_inventory()
  if not inventory then return false end
  -- A short negative cache avoids walking every inventory slot for every NPC
  -- each tick. Native item counts invalidate it immediately when new capsules
  -- arrive, so a newly supplied fighter can use one without waiting for TTL.
  if tick < (status.next_probe[mode] or 0) then
    local newly_available = false
    for _, name in ipairs(names) do
      if inventory.get_item_count{name = name} > 0 then newly_available = true; break end
    end
    if not newly_available then return false end
  end
  for _, name in ipairs(names) do
    for i = 1, #inventory do
      local stack = inventory[i]
      if stack.valid_for_read and stack.name == name then
        if not Squads.fire_safe(npc, target, {gun = "rocket-launcher", ammo_name = "robot-capsule"}) then
          status.reason = "capsule_ally_clearance"; return false
        end
        local quality = stack.quality and stack.quality.name or "normal"
        local before = inventory.get_item_count{name = name, quality = quality}
        local ok, created = pcall(function() return stack.use_capsule(character, target.position) end)
        local after = inventory.get_item_count{name = name, quality = quality}
        if not ok then status.reason = "capsule_native_rejected"; status.last_error = tostring(created); return false end
        local produced = type(created) == "table" and #created or 0
        if produced == 0 and after == before then
          status.reason = "capsule_no_native_effect"; return false
        end
        -- Some API paths produce an entity without consuming the stack. Enforce
        -- one-for-one ownership here; never credit a free throw.
        if after == before then
          if inventory.remove{name = name, quality = quality, count = 1} ~= 1 then
            status.reason = "capsule_spend_failed"; return false
          end
          after = inventory.get_item_count{name = name, quality = quality}
        end
        status.capsules_spent = status.capsules_spent + math.max(0, before - after)
        status.capsule_uses = status.capsule_uses + 1
        status.created_entities = (status.created_entities or 0) + produced
        for _, entity in ipairs(type(created) == "table" and created or {}) do
          if entity.valid and entity.type == "combat-robot" and entity.unit_number
              and entity.combat_robot_owner == character then
            storage.combat_equipment_robot_owner = storage.combat_equipment_robot_owner or {}
            storage.combat_equipment_robot_owner[entity.unit_number] = npc.id
            storage.combat_equipment_robot_receipts = storage.combat_equipment_robot_receipts or {}
            local receipts = storage.combat_equipment_robot_receipts
            receipts[#receipts + 1] = entity.unit_number
            while #receipts > 256 do
              storage.combat_equipment_robot_owner[table.remove(receipts, 1)] = nil
            end
          end
        end
        status.last_capsule = name
        status.last_capsule_tick = tick
        status.next_capsule[mode] = tick + cooldown
        status.next_probe[mode] = tick + CAPSULE_PROBE_TICKS
        status.mode, status.reason = mode, nil
        character.walking_state = {walking = false, direction = defines.direction.north}
        return true
      end
    end
  end
  status.next_probe[mode] = tick + CAPSULE_PROBE_TICKS
  return false
end

function Equipment.tick(npc, tick, target, weapon, axes)
  local character = npc and npc.character
  if not character or not character.valid then return false, {mode = "unavailable"} end
  local status = state(npc)
  if not target or not target.valid or target.surface ~= character.surface then
    local current = character.vehicle
    local owned = current and current.valid and current.type == "car"
        and current.force == npc.force and current.get_driver() == character
    stop(owned and current or nil, character)
    if owned then
      if math.abs(current.speed or 0) < 0.02 then
        pcall(function() current.set_driver(nil) end)
      end
      if current.get_driver() == character then
        status.mode = "vehicle_stopping"
        return true, Equipment.observe(npc)
      end
    end
    status.mode, status.reason = "idle", nil
    return false, Equipment.observe(npc)
  end

  local vehicle = owned_vehicle(npc, character, tick, status)
  if vehicle then
    local driven = vehicle.get_driver() == character
    local health = (character.health or 0) / math.max(1, character.max_health or 1)
    local vehicle_health = (vehicle.health or 0) / math.max(1, vehicle.max_health or 1)
    if not driven and (health < 0.35 or vehicle_health < 0.3) then
      status.reason = "health_too_low_to_board"
      return false, Equipment.observe(npc)
    end
    if not driven then
      if not fuel_available(character, vehicle) or not vehicle_ammo_available(vehicle, character) then
        status.reason = fuel_available(character, vehicle) and "no_vehicle_ammo" or "no_fuel"
        return false, Equipment.observe(npc)
      end
      local ok, err = pcall(function() vehicle.set_driver(character) end)
      if not ok or vehicle.get_driver() ~= character then
        status.reason, status.last_error = "vehicle_board_rejected", tostring(err)
        return false, Equipment.observe(npc)
      end
      status.boardings = status.boardings + 1
    end
    local fueled = fuel_from_character(character, vehicle)
    local gun = fueled and vehicle_weapon(vehicle, character) or nil
    if gun and fueled then
      character.walking_state = {walking = false, direction = defines.direction.north}
      vehicle.selected_gun_index = gun.slot
      local d = distance(vehicle.position, target.position)
      local heading_x, heading_y = math.sin(vehicle.orientation * TWO_PI), -math.cos(vehicle.orientation * TWO_PI)
      local blocked_forward = friendly_lane(npc, vehicle, heading_x, heading_y)
      local blocked_reverse = friendly_lane(npc, vehicle, -heading_x, -heading_y)
      local strength = npc.combat and npc.combat.threat_strength or 0
      local caution = axes and axes.courage or 0.5
      local urgent_retreat = vehicle_health < 0.4 or health < 0.4
          or strength > 3 + caution * 6
      local min_fire_distance = gun.category == "cannon-shell" and 8 or 2
      status.drive = steer(vehicle, target, gun.range, min_fire_distance, urgent_retreat,
        blocked_forward, blocked_reverse)
      if blocked_forward or blocked_reverse then
        status.blocked_drive_ticks = status.blocked_drive_ticks + 1
      end
      local fire_reason
      if d > gun.range - 1 then fire_reason = "out_of_vehicle_range"
      elseif d < min_fire_distance then fire_reason = "inside_vehicle_safe_range"
      elseif not Squads.fire_safe(npc, target, gun) then fire_reason = "squad_fire_clearance" end
      local safe = not fire_reason
      if safe then
        character.selected = target
        character.shooting_state = {state = defines.shooting.shooting_selected,
          position = target.position}
        status.vehicle_fire_ticks = status.vehicle_fire_ticks + 1
      else
        character.selected = nil
        character.shooting_state = {state = defines.shooting.not_shooting,
          position = character.position}
      end
      status.mode, status.reason, status.vehicle = "vehicle", fire_reason, vehicle
      status.gun, status.ammo_name, status.last_vehicle_tick = gun.gun, gun.ammo_name, tick
      return true, Equipment.observe(npc)
    end
    if vehicle.get_driver() == character then
      stop(vehicle, character)
      status.reason, status.vehicle = fueled and "no_vehicle_ammo" or "no_fuel", vehicle
      if math.abs(vehicle.speed or 0) < 0.02 then
        pcall(function() vehicle.set_driver(nil) end)
      end
      if vehicle.get_driver() == character then
        status.mode = "vehicle_stopping"
        return true, Equipment.observe(npc)
      end
      status.mode = "foot_available"
      return false, Equipment.observe(npc)
    end
    status.reason = fueled and "no_vehicle_ammo" or "no_fuel"
  end

  -- What it throws (Equipment.throws): worms get a poison cloud (it outlasts their attacks), groups and
  -- nests grenades, people slowdown capsules, anything robots; guns finish the rest.
  local d = distance(character.position, target.position)
  local count = npc.combat and npc.combat.threat_count or 1
  for _, t in ipairs(Equipment.throws(target, d, count)) do
    if d >= t[5] and use_capsule(npc, tick, target, character, status, t[1], t[2], t[3], t[4],
        t[4] == "grenade" and t[5] or nil) then
      return true, Equipment.observe(npc)
    end
  end
  status.mode = "foot_available"
  return false, Equipment.observe(npc)
end

-- Getting away from a fight it is losing: one throw to cover it, if it has one ready (combat.lua).
function Equipment.cover(npc, tick, target)
  local character = npc and npc.character
  if not (character and character.valid and target and target.valid) then return false end
  local status = state(npc)
  for _, t in ipairs(Equipment.cover_throws(target)) do
    if use_capsule(npc, tick, target, character, status, t[1], t[2], t[3], t[4]) then return true end
  end
  return false
end

function Equipment.observe(npc)
  local status = state(npc)
  local character = npc.character
  local vehicle = character and character.valid and character.vehicle
  local in_owned_vehicle = vehicle and vehicle.valid and vehicle.type == "car"
      and vehicle.force == npc.force and vehicle.get_driver() == character or false
  return {mode = status.mode, reason = status.reason, in_owned_vehicle = in_owned_vehicle,
    vehicle = in_owned_vehicle and vehicle.name or nil,
    vehicle_unit_number = in_owned_vehicle and vehicle.unit_number or nil,
    vehicle_position = in_owned_vehicle and copy_position(vehicle.position) or nil,
    vehicle_speed = in_owned_vehicle and vehicle.speed or nil,
    vehicle_gun = in_owned_vehicle and status.gun or nil,
    vehicle_ammo = in_owned_vehicle and status.ammo_name or nil,
    drive = in_owned_vehicle and status.drive or nil,
    boardings = status.boardings, vehicle_fire_ticks = status.vehicle_fire_ticks,
    blocked_drive_ticks = status.blocked_drive_ticks, capsules_spent = status.capsules_spent,
    capsule_uses = status.capsule_uses, last_capsule = status.last_capsule,
    last_capsule_tick = status.last_capsule_tick, created_entities = status.created_entities or 0,
    last_error = status.last_error}
end

-- Attribution follows the native driver/robot owner, or a receipt for an
-- actually returned combat robot. A matching faction alone is never credit.
function Equipment.owner_for_cause(cause)
  if not cause or not cause.valid then return nil end
  local character
  if cause.type == "car" then
    local driver = cause.get_driver()
    if driver and driver.valid and driver.type == "character" then character = driver end
  elseif cause.type == "combat-robot" then
    local owner = cause.combat_robot_owner
    if owner and owner.valid and owner.type == "character" then character = owner end
  end
  if character then
    local indexed = character.unit_number and storage.combat_body_index
        and storage.combat_body_index[character.unit_number]
    if indexed and storage.npcs and storage.npcs[indexed]
        and storage.npcs[indexed].character == character then return indexed end
    for id, npc in pairs(storage.npcs or {}) do
      if npc.character == character then
        if character.unit_number then
          storage.combat_body_index = storage.combat_body_index or {}
          storage.combat_body_index[character.unit_number] = id
        end
        return id
      end
    end
  end
  if cause.type == "combat-robot" and cause.unit_number then
    local id = storage.combat_equipment_robot_owner
        and storage.combat_equipment_robot_owner[cause.unit_number]
    if id and storage.npcs and storage.npcs[id] then return id end
  end
  return nil
end

return Equipment
