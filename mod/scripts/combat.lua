-- Physical local combat: native character weapons, real ammunition and walking.
-- No damage, healing, item creation, teleports or force/research changes occur here.
local Worker = require("scripts.worker")
local Movement = require("scripts.movement_policy")
local CombatPolicy = require("scripts.combat_policy")
local Squad = require("scripts.combat_squads")
local Equipment = require("scripts.combat_equipment")
local Turrets = require("scripts.combat_turrets")
local TeamWorld = require("scripts.team_world")
local TeamWar = require("scripts.team_war")
local Odds = require("scripts.combat_odds")
local loaded, Personality = pcall(require, "scripts.personality")
if not loaded then Personality = nil end
local Combat = {}
local MAX_COMBAT_RECOVERIES = 4
local RECOVERY_STALL_TICKS = 90
local RETREAT_LEG = 20
local LOCAL_HOME_RANGE = 32
local RETREAT_TURNS = {0, math.pi / 4, -math.pi / 4, math.pi / 2, -math.pi / 2}
-- Breaking contact in a fight it is losing: far enough that they lose it (biters give up the chase,
-- an engineer or a player no longer has it on screen), toward home unless home lies past them, and
-- not into a nest it knows of. It keeps going after they are out of its own sight, for a while.
local BREAK_BITERS, BREAK_PEOPLE, BREAK_FOR = 48, 100, 900
local BREAK_REASONS = {low_health = true, outnumbered = true, stronger_enemy = true, losing_exchange = true,
  ineffective_weapon = true, no_ammunition_or_gun = true, retreat_from_wave = true}
local TYPES = {"unit", "character", "unit-spawner", "turret", "ammo-turret", "electric-turret",
  "fluid-turret", "artillery-turret", "combat-robot", "car", "spider-vehicle"}

local function clamp(value)
  if type(value) ~= "number" or value ~= value then return 0.5 end
  return math.max(0, math.min(1, value))
end
local function axes(npc)
  local values = Personality and Personality.axes(npc) or {}
  return {courage = clamp(values.courage), aggression = clamp(values.aggression), defense = clamp(values.defense)}
end
local function distance(a, b)
  local x, y = a.x - b.x, a.y - b.y
  return math.sqrt(x * x + y * y)
end
local function copy_position(position) return {x = position.x, y = position.y} end
local function stop_walking(character)
  Movement.halt(character)   -- facing the way it walked
end
local function stop_shooting(character)
  if character and character.valid then
    character.shooting_state = {state = defines.shooting.not_shooting, position = character.position}
    character.selected = nil
  end
end

local function hostile(force, other)
  if not force or not other or force == other or other.name == "neutral" then return false end
  -- Native relations are unilateral. Honor either side's friendship or truce.
  return not force.get_friend(other) and not other.get_friend(force)
    and not force.get_cease_fire(other) and not other.get_cease_fire(force)
end
local function valid_enemy(character, entity)
  return entity and entity.valid and entity ~= character and entity.surface == character.surface
    and entity.health and entity.health > 0 and entity.is_military_target and hostile(character.force, entity.force)
end
-- What it fights of its own accord: whatever comes for it (biters, engineers, players, robots).
-- Nests, worms and turrets only where it chose to attack (its objective), or one that is hurting
-- it now (to get out of its reach). On a march it does not stop for biters that are not close.
local STATIC = {["unit-spawner"] = true, turret = true, ["ammo-turret"] = true, ["electric-turret"] = true,
  ["fluid-turret"] = true}
local OBJECTIVE_RADIUS = 64
local SWARM = 3   -- this many biters coming for it get its carried turrets, loaded at its feet
local MARCH_IGNORE = 14
local HURT_RECENT = 120
local function static_reach(entity)
  if entity.type == "unit-spawner" then return 15 end
  local ok, parameters = pcall(function() return entity.prototype.attack_parameters end)
  local range = ok and parameters and parameters.range
  return type(range) == "number" and range or 20
end
local function engage(npc, entity, tick, d)
  local objective = npc.combat_objective
  local on_mission = objective and tick < (npc.combat_mission_until or 0)
    and distance(entity.position, objective) <= OBJECTIVE_RADIUS
  if on_mission then return true end
  if STATIC[entity.type] then
    local state = npc.combat or {}
    return d <= static_reach(entity) + 2 and tick - (state.hurt_tick or -HURT_RECENT - 1) <= HURT_RECENT
  end
  if entity.type == "unit" and npc.marching and tick < (npc.marching.until_tick or 0) and d > MARCH_IGNORE
      and tick - ((npc.combat or {}).hurt_tick or -HURT_RECENT - 1) > HURT_RECENT then
    return false   -- keeping its distance, and not hurting it (spitters reach farther than 14)
  end
  return true
end
Combat.engage = engage
local AT_ITS_THROAT = 6
local function scan(character, radius, npc, tick)
  local nearest, nearest_distance, count, strength = nil, nil, 0, 0
  local person, person_distance
  for _,entity in ipairs(character.surface.find_entities_filtered{position = character.position, radius = radius, type = TYPES}) do
    if valid_enemy(character, entity) then
      local d = distance(character.position, entity.position)
      if not npc or engage(npc, entity, tick, d) then
        count = count + 1
        strength = strength + math.max(1, math.min(8, (entity.max_health or 100) / 100))
        if not nearest or d < nearest_distance or (d == nearest_distance and (entity.unit_number or 0) < (nearest.unit_number or 0)) then
          nearest, nearest_distance = entity, d
        end
        if entity.type == "character" and (not person or d < person_distance) then person, person_distance = entity, d end
      end
    end
  end
  -- The engineer or player it fights comes first (their turrets and buildings matter less), unless
  -- a biter is at its throat.
  if person and not (nearest.type == "unit" and nearest_distance <= AT_ITS_THROAT) then
    return person, person_distance, count, strength
  end
  return nearest, nearest_distance, count, strength
end

local function ammo_category(stack)
  if not stack.valid_for_read or stack.prototype.type ~= "ammo" then return nil end
  local category=stack.prototype.ammo_category
  return category and (category.name or category) or nil
end
local function gun_categories(stack)
  if not stack.valid_for_read or stack.prototype.type ~= "gun" then return nil end
  local attack=stack.prototype.attack_parameters
  local result={}
  for _,category in pairs(attack and attack.ammo_categories or {}) do
    local name=type(category)=="string" and category or category.name
    if name then result[name]=true end
  end
  return result
end
local function ammo_units(stack)
  if not ammo_category(stack) then return 0 end
  local magazine=math.max(1,stack.prototype.magazine_size or 1)
  return math.max(0,(stack.ammo or magazine)+(stack.count-1)*magazine)
end
local function compatible(gun,ammo)
  local categories=gun_categories(gun)
  local category=ammo_category(ammo)
  return categories and category and categories[category] or false
end
local AMMO_RANK={['firearm-magazine']=1,['piercing-rounds-magazine']=2,
  ['uranium-rounds-magazine']=3,['shotgun-shell']=1,['piercing-shotgun-shell']=2}
local ARMOR_RANK={['light-armor']=1,['heavy-armor']=2,['modular-armor']=3,
  ['power-armor']=4,['power-armor-mk2']=5,['mech-armor']=6}
local function ammo_rank(stack)
  return stack and stack.valid_for_read and (AMMO_RANK[stack.name] or 1) or 0
end
local function armor_rank(stack)
  if not stack or not stack.valid_for_read or stack.prototype.type~='armor' then return 0 end
  return ARMOR_RANK[stack.name] or 1
end
local function gun_score(gun,rounds,target_distance,available)
  if not gun_categories(gun) or not compatible(gun,rounds) then return -math.huge end
  local attack=gun.prototype.attack_parameters
  local range=attack and attack.range or 0
  local category=ammo_category(rounds)
  local d=target_distance or range*.75
  if category=='rocket' and d<8 or category=='flamethrower' and d<4 then return -math.huge end
  local cooldown=attack and attack.cooldown or 30
  local score=(d<=range*.95 and 6 or 0)-math.abs(d-range*.75)*.2
    +math.min(available or ammo_units(rounds),100)*.01+range*.01
    +12/math.max(1,cooldown)+ammo_rank(rounds)*1.5
  if category=='shotgun-shell' and d<10 then score=score+1 end
  return score
end
local function loose_rounds(main,gun)
  for j=1,#main do
    local round=main[j]
    if round.valid_for_read and compatible(gun,round) and ammo_units(round)>0 then return true end
  end
  return false
end
local function best_owned_rounds(character,gun)
  local best,best_rank
  local inventories={character.get_inventory(defines.inventory.character_ammo),
    character.get_main_inventory()}
  for _,inventory in ipairs(inventories) do if inventory then
    for i=1,#inventory do
      local round=inventory[i]
      if compatible(gun,round) and ammo_units(round)>0 then
        local rank=ammo_rank(round)
        if not best or rank>best_rank or rank==best_rank and ammo_units(round)>ammo_units(best) then
          best,best_rank=round,rank
        end
      end
    end
  end end
  return best
end
local function owned_ammo(character,category)
  local total=0
  for _,inventory in ipairs({character.get_inventory(defines.inventory.character_ammo),
      character.get_main_inventory()}) do
    if inventory then for i=1,#inventory do
      if ammo_category(inventory[i])==category then total=total+ammo_units(inventory[i]) end
    end end
  end
  local vehicle=character.vehicle
  if vehicle and vehicle.valid and vehicle.force==character.force then
    local car_ammo=vehicle.get_inventory(defines.inventory.car_ammo)
    if car_ammo then for i=1,#car_ammo do
      if ammo_category(car_ammo[i])==category then total=total+ammo_units(car_ammo[i]) end
    end end
  end
  return total
end
local function reserve_ammo(npc,character,category,tick)
  if not npc or not tick then return owned_ammo(character,category) end
  local loadout=npc.combat_loadout
  if not loadout or loadout.body_unit_number~=character.unit_number then
    return owned_ammo(character,category)
  end
  local cache=loadout.reserve
  if not cache or tick>=cache.until_tick then
    cache={until_tick=tick+15,totals={}}
    loadout.reserve=cache
  end
  if cache.totals[category]==nil then
    cache.totals[category]=owned_ammo(character,category)
  end
  return cache.totals[category]
end
local function weapon(character,target_distance,npc,tick)
  local guns=character.get_inventory(defines.inventory.character_guns)
  local ammo=character.get_inventory(defines.inventory.character_ammo)
  local best,best_score
  local bullet_total=reserve_ammo(npc,character,"bullet",tick)
  for i=1,math.min(#guns,#ammo) do
    if compatible(guns[i],ammo[i]) and ammo_units(ammo[i])>0 then
      local category=ammo_category(ammo[i])
      local attack=guns[i].prototype.attack_parameters
      local range=attack and attack.range or 0
      local available=reserve_ammo(npc,character,category,tick)
      local score=gun_score(guns[i],ammo[i],target_distance,available)
      if score>-math.huge then
        if not best or score>best_score then
          best={ready=true,slot=i,gun=guns[i].name,range=range,
            category=category,ammo_name=ammo[i].name,
            cooldown=attack and attack.cooldown or 30,
            ammo_units=available,ammunition_bullets=bullet_total,
            bullets=bullet_total,selected_bullets=ammo_units(ammo[i])}
          best_score=score
        end
      end
    end
  end
  return best or {ready=false,range=0,ammo_units=0,ammunition_bullets=bullet_total,
    bullets=bullet_total}
end
local function equip_owned(npc,target_distance,tick)
  local character=npc.character
  local main=character.get_main_inventory()
  local guns=character.get_inventory(defines.inventory.character_guns)
  local ammo=character.get_inventory(defines.inventory.character_ammo)
  if not main or not guns or not ammo then return false end
  local changed=false
  local armor=character.get_inventory(defines.inventory.character_armor)
  if armor and #armor>0 then
    local best_slot,best_rank=nil,armor_rank(armor[1])
    for j=1,#main do
      local rank=armor_rank(main[j])
      if rank>best_rank then best_slot,best_rank=j,rank end
    end
    if best_slot then
      local ok,result=pcall(function() return armor[1].swap_stack(main[best_slot]) end)
      if ok and result~=false then changed=true end
    end
  end
  -- Native paired gun/ammo slots own every equipped item. Transfers and swaps
  -- conserve the exact existing stacks, including partial magazines/quality.
  for slot=1,math.min(#guns,#ammo) do
    if not guns[slot].valid_for_read then
      local best_slot,best_score
      for j=1,#main do
        if gun_categories(main[j]) then
          local rounds=best_owned_rounds(character,main[j])
          local score=rounds and gun_score(main[j],rounds,target_distance) or -math.huge
          if score>-math.huge and (not best_slot or score>best_score) then
            best_slot,best_score=j,score
          end
        end
      end
      if best_slot then
        -- With no magazines of its own in its pockets, it goes in the place of a worse gun that holds some (put in the
        -- empty slot, it had none, and the pistol kept firing: the review, 2026-10-26).
        local other
        if not loose_rounds(main,main[best_slot]) then
          for s=1,math.min(#guns,#ammo) do
            if s~=slot and guns[s].valid_for_read and compatible(main[best_slot],ammo[s]) and ammo_units(ammo[s])>0
                and gun_score(main[best_slot],ammo[s],target_distance)>gun_score(guns[s],ammo[s],target_distance) then
              other=s
              break
            end
          end
        end
        if other then
          local ok,result=pcall(function() return guns[other].swap_stack(main[best_slot]) end)
          if ok and result~=false then changed=true end
        else
          guns[slot].transfer_stack(main[best_slot])
          changed=true
        end
      end
    end
  end
  if tick>=(npc.next_combat_gun_swap_tick or 0) then
    -- A negative search needs a deadline too; otherwise an unchanged arsenal
    -- rechecks every main-inventory slot on every combat tick.
    npc.next_combat_gun_swap_tick=tick+120
    local best_main,best_main_score,worst_slot,worst_score
    for j=1,#main do if gun_categories(main[j]) then
      local rounds=best_owned_rounds(character,main[j])
      local score=rounds and gun_score(main[j],rounds,target_distance) or -math.huge
      if score>-math.huge and (not best_main or score>best_main_score) then
        best_main,best_main_score=j,score
      end
    end end
    for slot=1,math.min(#guns,#ammo) do if guns[slot].valid_for_read then
      local rounds=best_owned_rounds(character,guns[slot])
      local score=rounds and gun_score(guns[slot],rounds,target_distance) or -math.huge
      if not worst_slot or score<worst_score then worst_slot,worst_score=slot,score end
    end end
    if best_main and worst_slot and best_main_score>worst_score+1.5 then
      local ok,swapped=pcall(function() return guns[worst_slot].swap_stack(main[best_main]) end)
      if ok and swapped~=false then changed=true end
    end
  end
  for slot=1,math.min(#guns,#ammo) do
    if guns[slot].valid_for_read then
      local best_main,best_rank
      for j=1,#main do
        if compatible(guns[slot],main[j]) and ammo_units(main[j])>0 then
          local rank=ammo_rank(main[j])
          if not best_main or rank>best_rank or
              rank==best_rank and ammo_units(main[j])>ammo_units(main[best_main]) then
            best_main,best_rank=j,rank
          end
        end
      end
      local current_rank=compatible(guns[slot],ammo[slot]) and
        ammo_units(ammo[slot])>0 and ammo_rank(ammo[slot]) or 0
      if best_main and best_rank>current_rank then
        if ammo[slot].valid_for_read then
          local ok,result=pcall(function() return ammo[slot].swap_stack(main[best_main]) end)
          if ok and result~=false then changed=true end
        else
          ammo[slot].transfer_stack(main[best_main])
          changed=true
        end
      elseif best_main and current_rank==0 then
        ammo[slot].transfer_stack(main[best_main])
        changed=true
      end
    end
  end
  return changed
end

local function equipped_signature(character)
  local guns=character.get_inventory(defines.inventory.character_guns)
  local ammo=character.get_inventory(defines.inventory.character_ammo)
  local names={}
  local ready=false
  if guns and ammo then for slot=1,math.min(#guns,#ammo) do
    local gun,rounds=guns[slot],ammo[slot]
    names[#names+1]=gun.valid_for_read and gun.name or "-"
    names[#names+1]=rounds.valid_for_read and rounds.name or "-"
    if not ready and compatible(gun,rounds) and ammo_units(rounds)>0 then
      ready=true
    end
  end end
  return table.concat(names,"|"),ready
end

local function refresh_loadout(npc,target_distance,tick)
  local body=npc.character
  local signature,ready=equipped_signature(body)
  local state=npc.combat_loadout
  if not state or state.body_unit_number~=body.unit_number then
    state={body_unit_number=body.unit_number,next_scan_tick=tick}
    npc.combat_loadout=state
  end
  local changed_slots=signature~=state.signature
  local depleted=state.last_ready==true and not ready
  local vehicle=body.vehicle
  local vehicle_id=vehicle and vehicle.valid and vehicle.unit_number or 0
  if changed_slots or depleted or state.vehicle_unit_number~=vehicle_id then
    state.next_scan_tick=tick
    state.reserve=nil
  end
  if tick>=(state.next_scan_tick or 0) then
    local changed=equip_owned(npc,target_distance,tick)
    state.next_scan_tick=tick+15
    if changed then state.reserve=nil end
    signature,ready=equipped_signature(body)
  end
  state.signature,state.last_ready,state.vehicle_unit_number=signature,ready,vehicle_id
end

local function clear_movement(npc)
  local nav = npc.combat and npc.combat.movement
  if nav then
    Movement.finish(npc, nav.pending_episode, game.tick, "combat_goal_changed", false)
    if nav.recovery then
      Movement.finish(npc, nav.recovery.episode, game.tick, "combat_goal_changed", false)
    end
  end
  if nav and nav.request_id and storage.combat_paths then storage.combat_paths[nav.request_id] = nil end
  if npc.combat then npc.combat.movement = nil end
  stop_walking(npc.character)
end
local function retry(npc, nav, tick, reason)
  if nav.request_id and storage.combat_paths then storage.combat_paths[nav.request_id] = nil end
  nav.request_id, nav.path, nav.index = nil, nil, nil
  if nav.recovery then
    Movement.finish(npc, nav.recovery.episode, tick,
      reason == "combat_movement_stalled" and "movement_stalled" or reason, false)
    nav.recovery = nil
  end
  if reason == "combat_no_path" or reason == "combat_movement_stalled" then
    Movement.note_failure(nav, nav.goal, tick, reason)
  end
  nav.reason, nav.retry_at = reason, tick + 30 * math.max(1, nav.requests)
  local recoverable = reason == "combat_no_path" or reason == "combat_movement_stalled"
    or reason == "combat_path_timeout"
  if nav.requests >= 3 and
      (not recoverable or (nav.recovery_attempts or 0) >= MAX_COMBAT_RECOVERIES) then
    nav.failed = reason
  end
  stop_walking(npc.character)
end
local function start_recovery(npc, nav, target, tick, cause)
  if (nav.recovery_attempts or 0) >= MAX_COMBAT_RECOVERIES then return false end
  local choice, episode = Movement.choose(npc, nav, target, tick, cause)
  if not choice then return false end
  nav.recovery_attempts = (nav.recovery_attempts or 0) + 1
  npc.combat.movement_recoveries = (npc.combat.movement_recoveries or 0) + 1
  nav.recovery = {target = copy_position(choice.target), kind = choice.id,
    started_tick = tick, progress_tick = tick,
    best_distance = distance(npc.character.position, choice.target),
    episode = episode, follow_target = copy_position(target)}
  nav.reason = choice.id == "yield" and "combat_yielding" or "combat_local_recovery"
  return true
end
local function move(npc, goal, radius, tick, direct_first)
  local character, state = npc.character, npc.combat
  if distance(character.position, goal) <= radius then clear_movement(npc); return "arrived" end
  local nav = state.movement
  if not nav or distance(nav.goal, goal) > 1 then
    clear_movement(npc)
    nav = {goal = copy_position(goal), radius = radius, requests = 0, revision = 0,
      direct = direct_first == true, progress_tick = tick,
      recovery_budget_goal_distance = distance(character.position, goal)}
    state.movement = nav
  end
  nav.failed_targets = npc.navigation_failed_targets or nav.failed_targets or {}
  npc.navigation_failed_targets = nav.failed_targets
  nav.recovery_budget_goal_distance = nav.recovery_budget_goal_distance or distance(character.position, goal)
  if nav.failed then return "failed", nav.failed end
  if nav.direct then
    local d = distance(character.position, nav.goal)
    if not nav.best_distance or d < nav.best_distance - 0.12 then
      nav.best_distance, nav.progress_tick = d, tick
    elseif tick - nav.progress_tick >= 45 then
      -- A local straight leg can meet a tree or cliff. Ask the native pathfinder
      -- only after actual walking stops making progress.
      nav.direct, nav.best_distance = false, nil
      stop_walking(character)
    end
    if nav.direct then
      character.walking_state = {walking = true, direction = Movement.steer(nav, character.position, nav.goal)}
      return "active"
    end
  end
  if nav.request_id then
    stop_walking(character)
    if tick - nav.request_tick > 600 then
      Movement.finish(npc, nav.pending_episode, tick, "path_timeout", false)
      nav.pending_episode = nil
      retry(npc, nav, tick, "combat_path_timeout")
      if not nav.failed then start_recovery(npc, nav, nav.goal, tick, "path_timeout") end
    end
    return "active"
  end
  if nav.recovery then
    local recovery = nav.recovery
    local d = distance(character.position, recovery.target)
    if recovery.kind == "yield" then
      stop_walking(character)
      if tick - recovery.started_tick < 45 then return "active" end
    elseif d > 0.45 then
      if d < recovery.best_distance - 0.08 then
        recovery.best_distance, recovery.progress_tick = d, tick
      elseif tick - recovery.progress_tick >= RECOVERY_STALL_TICKS then
        nav.recovery = nil
        Movement.finish(npc, recovery.episode, tick, "recovery_timeout", false)
        Movement.note_failure(nav, recovery.target, tick, "recovery_timeout")
        if not start_recovery(npc, nav, recovery.follow_target, tick, "movement_stalled") then
          retry(npc, nav, tick, "combat_movement_stalled")
        end
        return "active"
      end
      character.walking_state = {walking = true, direction = Movement.steer(nav, character.position, recovery.target)}
      return "active"
    end
    -- The body actually walked or waited. The following native path result
    -- decides whether this local move reopened a route.
    nav.recovery, nav.path, nav.index = nil, nil, nil
    nav.pending_episode = recovery.episode
    nav.retry_at = tick + 1
    stop_walking(character)
    return "active"
  end
  if not nav.path then
    stop_walking(character)
    if tick < (nav.retry_at or 0) then return "active" end
    nav.requests, nav.revision = nav.requests + 1, nav.revision + 1
    local ok, id = pcall(function() return character.surface.request_path{
      bounding_box = character.prototype.collision_box, collision_mask = character.prototype.collision_mask,
      start = character.position, goal = nav.goal, radius = nav.radius, force = character.force,
      entity_to_ignore = character, can_open_gates = true, pathfind_flags = {cache = false,
        allow_destroy_friendly_entities = false, allow_paths_through_own_entities = false}} end)
    if not ok or not id then
      Movement.finish(npc, nav.pending_episode, tick, "path_request_rejected", false)
      nav.pending_episode = nil
      retry(npc, nav, tick, "combat_path_request_rejected")
    else
      storage.combat_paths = storage.combat_paths or {}
      nav.request_id, nav.request_tick = id, tick
      storage.combat_paths[id] = {npc_id = npc.id, episode = state.episode, revision = nav.revision}
      state.path_requests = (state.path_requests or 0) + 1
    end
    return "active"
  end
  -- Waypoints within reach are passed without stopping, so the walk never halts between them.
  local passed, p, aim = Movement.follow(nav, character.position)
  if passed then
    nav.best_distance, nav.progress_tick = nil, tick
    local global_distance = distance(character.position, nav.goal)
    if global_distance <= nav.recovery_budget_goal_distance - 6 then
      nav.recovery_budget_goal_distance, nav.recovery_attempts = global_distance, 0
    end
  end
  if not p then retry(npc, nav, tick, "combat_path_ended_outside_reach"); return "active" end
  local d = distance(character.position, p)
  Movement.note_position(nav, character.position, tick)
  do
    if not nav.best_distance or d < nav.best_distance - 0.12 then nav.best_distance, nav.progress_tick = d, tick
    elseif tick - nav.progress_tick >= 180 then
      if not start_recovery(npc, nav, p, tick, "movement_stalled") then
        retry(npc, nav, tick, "combat_movement_stalled")
      end
      return "active"
    end
    if tick >= (nav.next_traffic_check or 0) then
      nav.next_traffic_check = tick + 30
      if Movement.priority_blocker(npc, nav.goal)
          and start_recovery(npc, nav, nav.goal, tick, "traffic") then return "active" end
    end
    character.walking_state = {walking = true, direction = Movement.steer(nav, character.position, aim)}
  end
  return "active"
end

function Combat.on_path_finished(event)
  local pending = storage.combat_paths and storage.combat_paths[event.id]
  if not pending then return false end
  storage.combat_paths[event.id] = nil
  local npc = storage.npcs and storage.npcs[pending.npc_id]
  local state = npc and npc.combat
  local nav = state and state.movement
  if not nav or state.episode ~= pending.episode or nav.revision ~= pending.revision or nav.request_id ~= event.id then return true end
  nav.request_id = nil
  if event.try_again_later then
    Movement.finish(npc, nav.pending_episode, event.tick or game.tick, "pathfinder_busy", false)
    nav.pending_episode = nil
    retry(npc, nav, event.tick or game.tick, "combat_pathfinder_busy")
  elseif not event.path or #event.path == 0 then
    Movement.finish(npc, nav.pending_episode, event.tick or game.tick, "no_path", false)
    nav.pending_episode = nil
    retry(npc, nav, event.tick or game.tick, "combat_no_path")
    if not nav.failed then start_recovery(npc, nav, nav.goal, event.tick or game.tick, "no_path") end
  else
    Movement.finish(npc, nav.pending_episode, event.tick or game.tick, "path_reopened", true)
    nav.pending_episode = nil
    nav.path, nav.index, nav.best_distance, nav.progress_tick = event.path, 1, nil, event.tick or game.tick
    state.path_results = (state.path_results or 0) + 1
  end
  return true
end

-- Faster than it: a biter it cannot get away from on foot (small biters run 0.24 against its 0.15 with the user's
-- mods): a fight with one is a fight it cannot leave, and a run is only worth it to cover a short way off.
function Combat.outrun_by(npc, target)
  if not (target and target.valid and target.type == "unit") then return false end
  local c = npc.character
  local enemy = math.max(target.speed or 0, target.prototype.speed or 0)
  local own = (c.prototype.running_speed or .15) * (1 + math.max(0, c.force.character_running_speed_modifier or 0))
  return own <= enemy * 1.05
end
local RUN_TO_COVER = 48   -- (Combat.cornered)
local COVER_SLACK = 4     -- cover may stand this much nearer them than it does
local function loaded(turret)
  if turret.type == "ammo-turret" then return not turret.get_inventory(defines.inventory.turret_ammo).is_empty()
  elseif turret.type == "electric-turret" then return turret.energy > 0 and turret.is_connected_to_electric_network() end
  for i = 1, turret.fluids_count do local fluid = turret.get_fluid(i); if fluid and fluid.amount > 0 then return true end end
  return false
end
-- Somewhere it gets to before they catch it: anywhere from what is no faster than it; from faster biters a short run
-- only (RUN_TO_COVER), and never past them or toward them: the way there comes no nearer them than it stands now (a
-- friend's turret on their far side was a backup to run to before, through the biters).
local function nearest_on_way(a, b, p)
  local dx, dy = b.x - a.x, b.y - a.y
  local l2 = dx * dx + dy * dy
  local t = l2 > 0 and math.max(0, math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2)) or 0
  return distance(p, {x = a.x + dx * t, y = a.y + dy * t})
end
local function reachable(npc, threat, p)
  if not Combat.outrun_by(npc, threat) then return true end
  local here = npc.character.position
  local d = distance(here, threat.position)
  -- (the slack shrinks with them close: three tiles off, a way past them is no way out)
  return distance(here, p) <= RUN_TO_COVER
    and nearest_on_way(here, p, threat.position) >= math.max(d - COVER_SLACK, d * 0.75)
end
Combat.reachable = reachable
local function safe_goal(npc, threat, attempt, skip_backup)
  local c = npc.character
  if not skip_backup and Squad.backup_options then
    for _, o in ipairs(Squad.backup_options(npc, threat, game.tick) or {}) do
      if o.goal and reachable(npc, threat, o.goal) then
        return copy_position(o.goal), (o.kind or "defended_backup") .. ":" .. tostring(o.provider_unit_number)
      end
    end
  end
  -- The team world: from biters it falls back behind the team's nearest loaded turrets (its own, a
  -- teammate's, the players'), which it can reach before they catch it; walking off would not lose them.
  if TeamWorld.active() and threat.force and threat.force.name == "enemy" then
    local t = TeamWar.cover(npc, threat.position)
    -- (from faster biters only a short run that is not past them; with no ammunition left, any run is all it has)
    if t and not (reachable(npc, threat, t.position) or npc.combat and npc.combat.reason == "no_ammunition_or_gun") then
      t = nil
    end
    if t then
      local x, y = t.position.x - threat.position.x, t.position.y - threat.position.y
      local length = math.max(0.01, math.sqrt(x * x + y * y))
      local position = {x = t.position.x + x / length * 4, y = t.position.y + y / length * 4}
      return c.surface.find_non_colliding_position("character", position, 8, 0.5) or position, "team_defense"
    end
  end
  local chosen, chosen_distance
  -- Breaking contact, it runs to turrets that stand behind it, not to one it just dropped to slow them.
  local breaking, dropped = npc.combat and npc.combat.breaking, {}
  for _, t in ipairs(breaking and npc.combat_turrets and npc.combat_turrets.dropped or {}) do dropped[t] = true end
  for _,turret in ipairs(c.surface.find_entities_filtered{position = c.position, radius = 64,
      force = c.force, type = {"ammo-turret", "electric-turret", "fluid-turret"}}) do
    local supplied = false
    if breaking and (dropped[turret]
        or distance(turret.position, threat.position) < distance(c.position, threat.position)) then
      supplied = false
    else supplied = loaded(turret) and reachable(npc, threat, turret.position) end
    local d = distance(c.position, turret.position)
    if turret.health and turret.health > 0 and supplied and (not chosen or d < chosen_distance) then chosen, chosen_distance = turret, d end
  end
  local position, source
  if chosen then
    local x, y = chosen.position.x - threat.position.x, chosen.position.y - threat.position.y
    local length = math.max(0.01, math.sqrt(x * x + y * y))
    position, source = {x = chosen.position.x + x / length * 4, y = chosen.position.y + y / length * 4}, "owned_defense"
  elseif npc.home and distance(c.position, npc.home) <= LOCAL_HOME_RANGE
      and distance(npc.home, threat.position) > distance(c.position, threat.position) + 4
      and not Combat.outrun_by(npc, threat) then   -- (home without turrets is no cover from faster biters)
    position, source = copy_position(npc.home), "home"
  else
    position, source = Combat.away(npc, c.position, threat.position, attempt)
  end
  return c.surface.find_non_colliding_position("character", position, 8, 0.5) or position, source
end

-- Where to go from `here`, away from `from`: a short leg back, or when breaking contact
-- (npc.combat.breaking) a long one, bent toward home and round the dangers it remembers. `leg`: as far as
-- that, bent and kept round dangers the same way (keeping clear of biters, below).
local function clear_of_dangers(npc, p)
  for _, d in pairs(npc.seen_dangers and npc.seen_dangers.list or {}) do
    if (p.x - d.x) ^ 2 + (p.y - d.y) ^ 2 <= (d.r + 8) ^ 2 then return false end
  end
  return true
end
function Combat.away(npc, here, from, attempt, far)
  local x, y = here.x - from.x, here.y - from.y
  local length = math.sqrt(x * x + y * y)
  if length < 0.01 then x, y, length = 1, 0, 1 end
  x, y = x / length, y / length
  local breaking = npc.combat and npc.combat.breaking or far and {}
  local leg = RETREAT_LEG
  if breaking then
    leg = far or breaking.people and BREAK_PEOPLE or BREAK_BITERS
    local h = npc.home
    local hx, hy = h and h.x - here.x or 0, h and h.y - here.y or 0
    local hl = math.sqrt(hx * hx + hy * hy)
    if hl > leg / 2 and (hx * x + hy * y) / hl > -0.3 then   -- home is not past them: bend toward it
      x, y = x + hx / hl * 0.7, y + hy / hl * 0.7
      local l = math.sqrt(x * x + y * y)
      x, y = x / l, y / l
    end
  end
  local first
  for k = 0, #RETREAT_TURNS - 1 do
    local turn = RETREAT_TURNS[(((attempt or 0) + k) % #RETREAT_TURNS) + 1]
    local cosine, sine = math.cos(turn), math.sin(turn)
    local p = {x = here.x + (x * cosine - y * sine) * leg, y = here.y + (x * sine + y * cosine) * leg}
    first = first or p
    if not breaking or clear_of_dangers(npc, p) then
      return p, far and "keep_clear" or breaking and "break_contact" or "local_away_from_threat"
    end
  end
  return first, far and "keep_clear" or "break_contact"
end

-- Out of a person's sight after the long run away, a hard turn left or right, so one who chases loses
-- the trail: toward home more often than not, never toward a nest it knows of. Once per break.
local BREAK_TURN = 60
function Combat.break_turn(npc, state, tick)
  local b = state.breaking
  if not (b and b.people and not b.turned and b.from) then return false end
  local c = npc.character
  local here = c.position
  local ax, ay = here.x - b.from.x, here.y - b.from.y
  local l = math.sqrt(ax * ax + ay * ay)
  if l < 1 then return false end
  ax, ay = ax / l, ay / l
  local first = {x = here.x - ay * BREAK_TURN, y = here.y + ax * BREAK_TURN}
  local second = {x = here.x + ay * BREAK_TURN, y = here.y - ax * BREAK_TURN}
  local h = npc.home
  if h and distance(second, h) < distance(first, h) then first, second = second, first end
  if math.random() < 0.3 then first, second = second, first end   -- not always the same way
  if not clear_of_dangers(npc, first) and clear_of_dangers(npc, second) then first = second end
  b.turned = true
  state.goal = c.surface.find_non_colliding_position("character", first, 8, 0.5) or first
  state.goal_source = "break_contact"
  state.quiet_since = tick   -- the second leg gets its own while
  return true
end

local function enemy_reach(target)
  local fallback=target.type=="unit" and 3.5 or target.type=="character" and 9 or 2
  if target.type=="character" then
    local guns=target.get_inventory(defines.inventory.character_guns)
    local slot=target.selected_gun_index or 1
    local gun=guns and guns[slot]
    local attack=gun and gun.valid_for_read and gun.prototype.attack_parameters
    if attack and type(attack.range)=="number" then
      return math.max(1,math.min(32,attack.range))
    end
  end
  local ok,parameters=pcall(function() return target.prototype.attack_parameters end)
  local range=ok and parameters and parameters.range
  return type(range)=="number" and math.max(1,math.min(32,range)) or fallback
end
-- The reach it keeps out of: an enemy's, unless it cannot outrange them (a gunfight with an engineer
-- or a player as well armed): then it must come within their range to shoot at all.
local function kept_reach(target,weapon_range)
  local reach=enemy_reach(target)
  if target.type=="character" and reach>=weapon_range-0.5 then return 0 end
  return reach
end
Combat.kept_reach=kept_reach
local function useful_band(weapon_range,reach)
  local high=math.max(2,weapon_range*.9)
  local low=math.max(reach+2,weapon_range*.5)
  return math.min(low,high-.5),high
end
local function native_position(character,proposed)
  return character.surface.find_non_colliding_position("character",proposed,1,.5)
end
-- With its turrets down by it, its place is behind them: they take the bites and it shoots past them. Backing off,
-- it goes there (three tiles behind the nearest of its loaded ones, away from them), not on away from its turrets;
-- with none near, four tiles straight back. Returns the point and whether it is a turret's.
local BY_ITS_TURRETS=14   -- (combat_turrets NEAR)
local function back_off(npc,target,x,y)
  local c=npc.character
  local best,best_d,behind
  for _,t in ipairs(c.surface.find_entities_filtered{force=c.force,position=c.position,radius=BY_ITS_TURRETS,
      type={"ammo-turret","electric-turret","fluid-turret"}}) do
    local d=distance(t.position,c.position)
    if t.valid and loaded(t) and (not best or d<best_d) then
      local tx,ty=t.position.x-target.position.x,t.position.y-target.position.y
      local tl=math.max(.01,math.sqrt(tx*tx+ty*ty))
      local p={x=t.position.x+tx/tl*3,y=t.position.y+ty/tl*3}
      if reachable(npc,target,p) then best,best_d,behind=t,d,p end   -- (not past the biters to get there)
    end
  end
  if best then return native_position(c,behind),true end
  local here=c.position
  return native_position(c,{x=here.x-x*4,y=here.y-y*4}),false
end
Combat.back_off=back_off
local function tactic_options(npc,target,gear,low,high,mandatory)
  local c=npc.character
  local here=c.position;local d=distance(here,target.position)
  local x,y=target.position.x-here.x,target.position.y-here.y
  local length=math.max(.01,math.sqrt(x*x+y*y));x,y=x/length,y/length
  local middle=(low+high)/2
  local options={}
  local function add(id,point,legal,metadata)
    if point then options[#options+1]={id=id,target=copy_position(point),
      distance_to_enemy=distance(point,target.position),legal=legal~=false,
      action=metadata and metadata.action or id,
      backup_goal=metadata and metadata.backup_goal,
      backup_kind=metadata and metadata.backup_kind,
      provider_id=metadata and metadata.provider_id,
      source=metadata and metadata.source} end
  end
  local retreat_goal,retreat_source=safe_goal(npc,target,npc.combat.retreat_failures or 0,true)
  local function add_backups()
    if not Squad.backup_options then return end
    local providers=Squad.backup_options(npc,target,game.tick) or {}
    for i=1,math.min(4,#providers) do
      local provider=providers[i]
      local p=provider.goal
      local blocked=npc.combat.backup_failed_provider==provider.id
        and game.tick<(npc.combat.backup_failed_until or 0)
      if p and type(p.x)=="number" and type(p.y)=="number"
          and p.x==p.x and p.y==p.y and math.abs(p.x)<1000000
          and math.abs(p.y)<1000000 and not blocked and reachable(npc,target,p) then
        add("seek_backup:"..tostring(provider.id),p,true,
          {action="seek_backup",backup_goal=copy_position(p),
            backup_kind=provider.kind,provider_id=provider.id,
            source=provider.kind})
      end
    end
  end
  if mandatory then
    add("retreat",retreat_goal,true,{source=retreat_source})
    add_backups()
    return options
  end
  local kite,by_turret=back_off(npc,target,x,y)
  if d<=low and (target.type=="unit" or target.type=="character"
      or target.type=="combat-robot") then
    add("kite",kite,kite and distance(kite,here)>=1)
    if by_turret then add("hold",here,true) end   -- (behind its turrets already: it stands there and shoots)
    add("retreat",retreat_goal,true,{source=retreat_source})
    add_backups()
    return options
  end
  add("hold",here,d<=gear.range*.95 and d>=kept_reach(target,gear.range)+1)
  local advance=native_position(c,{x=here.x+x*4,y=here.y+y*4})
  add("advance",advance,d>low and advance and distance(advance,here)>=1
    and distance(advance,target.position)>=kept_reach(target,gear.range)+1)
  add("kite",kite,d<high+2 and kite and distance(kite,here)>=1)
  add("retreat",retreat_goal,true,{source=retreat_source})
  local radial=math.max(-1,math.min(1,(d-middle)/2))
  local left=native_position(c,{x=here.x-y*4+x*radial,y=here.y+x*4+y*radial})
  local right=native_position(c,{x=here.x+y*4+x*radial,y=here.y-x*4+y*radial})
  add("strafe_left",left,d>=low-2 and d<=high+2 and left and distance(left,here)>=1)
  add("strafe_right",right,d>=low-2 and d<=high+2 and right and distance(right,here)>=1)
  add_backups()
  return options
end
local function finish_tactic(npc,tick,reason)
  local state=npc.combat
  local tactic=state and state.tactical
  if not tactic then return end
  local c=npc.character
  local ammo_now=c and c.valid and owned_ammo(c,tactic.ammo_category) or tactic.start_ammo
  local alive=reason~="body_lost" and c and c.valid and (c.health or 0)>0
  local moved=c and c.valid and distance(c.position,tactic.start_position) or 0
  local backup_progress=0
  if tactic.id=="seek_backup" and tactic.backup_goal and c and c.valid then
    backup_progress=distance(tactic.start_position,tactic.backup_goal)
      -distance(c.position,tactic.backup_goal)
  end
  local backup_reached_supported=false
  if tactic.id=="seek_backup" and tactic.backup_goal and c and c.valid
      and distance(c.position,tactic.backup_goal)<=4
      and Squad.backup_supported then
    backup_reached_supported=Squad.backup_supported(npc,tactic.provider_id,
      tactic.threat_force_name)==true
  end
  local metrics={damage_dealt=(state.damage_dealt_total or 0)-tactic.start_damage,
    kills=(state.kills_total or 0)-tactic.start_kills,
    pursuer_clears=(state.pursuer_clears_total or 0)-tactic.start_pursuer_clears,
    health_lost=(state.health_lost_total or 0)-tactic.start_health_loss,
    ammo_spent=math.max(0,tactic.start_ammo-ammo_now),
    in_range_ticks=tactic.in_range_ticks or 0,
    alive=alive==true,start_health_fraction=tactic.start_health_fraction,
    end_health_fraction=alive and (c.health or 0)/math.max(1,c.max_health or 1) or 0,
    backup_progress=backup_progress,moved_distance=moved,
    backup_reached_supported=backup_reached_supported,
    target_cleared=((state.pursuer_clears_total or 0)-tactic.start_pursuer_clears)>0
      or (tactic.target_entity and not tactic.target_entity.valid or false)}
  CombatPolicy.finish(npc,tactic.episode,tick,reason,metrics)
  if tick-tactic.episode.decision_tick>=15 then
    state.last_tactical_outcome={tick=tick,damage_dealt=metrics.damage_dealt,
      health_lost=metrics.health_lost,ammo_spent=metrics.ammo_spent,
      target_unit_number=tactic.target_unit_number}
  end
  state.tactical=nil
end
local function choose_tactic(npc,target,gear,traits,tick,mandatory)
  local c=npc.character;local state=npc.combat
  local d=distance(c.position,target.position)
  local reach=kept_reach(target,gear.range)
  local low,high=useful_band(gear.range,reach)
  local observation={position=copy_position(c.position),distance=d,
    band_low=low,band_high=high,enemy_reach=reach,
    weapon_range=gear.range,weapon_ready=gear.ready,health_fraction=(c.health or 0)/math.max(1,c.max_health or 1),
    ammo_units=gear.ammo_units or 0,threat_strength=state.threat_strength or 0,
    aggression=traits.aggression,courage=traits.courage,defense=traits.defense,
    target_unit_number=target.unit_number or 0}
  local decision,episode=CombatPolicy.choose(npc,observation,
    tactic_options(npc,target,gear,low,high,mandatory),tick)
  if not decision then return nil end
  -- Outside a forced retreat the move is set by rule, as players fight: too close to something that
  -- bites or shoots, back off (kite); in range, stand and shoot; too far, close in.
  local pick=not mandatory and episode and Combat.rule_pick(episode.candidates,d,low,high,
    target.type=="unit" or target.type=="character" or target.type=="combat-robot")
  if pick then decision=episode.candidates[pick]; episode.chosen_candidate_index=pick end
  local category=gear.category
  state.tactical={id=decision.action or decision.id,target=decision.target,
    source=decision.source,provider_id=decision.provider_id,
    backup_goal=decision.backup_goal,episode=episode,target_entity=target,
    threat_force_name=target.force.name,
    target_unit_number=target.unit_number or 0,until_tick=tick+45,
    start_damage=state.damage_dealt_total or 0,start_kills=state.kills_total or 0,
    start_pursuer_clears=state.pursuer_clears_total or 0,
    start_health_loss=state.health_lost_total or 0,start_ammo=category and owned_ammo(c,category) or 0,
    start_position=copy_position(c.position),
    start_health_fraction=(c.health or 0)/math.max(1,c.max_health or 1),
    ammo_category=category,in_range_ticks=0,band_low=low,band_high=high}
  return state.tactical
end
local function fire(npc,target,gear,tick)
  local c=npc.character
  local state=npc.combat
  local mobile=target.type=="unit" or target.type=="character" or target.type=="combat-robot"
  local d=distance(c.position,target.position)
  local target_id=target.unit_number or 0
  if state.fire_sprint_target_id~=target_id then
    state.fire_sprint=nil
    state.fire_sprint_cooldown_until=nil
    state.fire_sprint_unreachable_until=nil
    state.fire_sprint_target_id=target_id
  end
  -- A bare character cannot create an escape gap from a faster native unit.
  -- Keep moving-fire in that case; an exclusive sprint would just surrender
  -- shots while the pursuer continues to close. Modifiers are read from the
  -- owned body/force and deliberately overestimate possible running speed.
  local enemy_speed=mobile and target.type=="unit"
    and math.max(target.effective_speed or 0,target.speed or 0,
      target.prototype.speed or 0) or nil
  local own_base=c.prototype.running_speed or .15
  local own_modifier=math.max(0,c.force.character_running_speed_modifier or 0)
    +math.max(0,c.speed_bonus or 0)
  local own_speed_upper=own_base*(1+own_modifier)
  local can_open_gap=not enemy_speed or own_speed_upper>enemy_speed*1.05
  if mobile and gear.ready and can_open_gap then
    local low=useful_band(gear.range,kept_reach(target,gear.range))
    if state.fire_sprint then
      local elapsed=tick-(state.fire_sprint_started_tick or tick)
      local gain=d-(state.fire_sprint_start_distance or d)
      if d>=low+2 then
        state.fire_sprint=nil
        state.fire_sprint_cooldown_until=tick+90
        state.sprint_abort_reason="band_reopened"
      elseif elapsed>=24 or elapsed>=18 and gain<.75 then
        state.fire_sprint=nil
        state.fire_sprint_cooldown_until=tick+90
        if gain<.75 then state.fire_sprint_unreachable_until=tick+180 end
        state.sprint_abort_reason=gain<.75 and "no_distance_gain" or "burst_limit"
      end
    elseif d<=low and tick>=(state.fire_sprint_cooldown_until or 0)
        and tick>=(state.fire_sprint_unreachable_until or 0) then
      state.fire_sprint=true
      state.fire_sprint_started_tick=tick
      state.fire_sprint_start_distance=d
      state.sprint_abort_reason=nil
    end
  else
    state.fire_sprint=nil
    if enemy_speed and not can_open_gap then
      state.sprint_abort_reason="pursuer_faster_than_body"
    end
  end
  if state.fire_sprint then
    state.sprint_ticks=(state.sprint_ticks or 0)+1
    stop_shooting(c)
    return false,"sprint"
  end
  if gear.ready and d<=gear.range*.95 then
    if Squad.fire_safe(npc,target,gear) then
      c.selected_gun_index=gear.slot
      c.selected=target
      c.shooting_state={state=defines.shooting.shooting_selected,position=target.position}
      return true,nil
    end
    stop_shooting(c)
    return false,"unsafe_line"
  end
  stop_shooting(c)
  return false,gear.ready and "out_of_range" or "unarmed"
end

local function npc_for_character(entity)
  if not entity or not entity.valid or entity.type~="character" then return nil end
  local id=entity.unit_number and storage.combat_body_index and storage.combat_body_index[entity.unit_number]
  local indexed=id and storage.npcs and storage.npcs[id]
  if indexed and indexed.character==entity then return indexed end
  for _,npc in pairs(storage.npcs or {}) do
    if npc.character==entity then
      if entity.unit_number then
        storage.combat_body_index=storage.combat_body_index or {}
        storage.combat_body_index[entity.unit_number]=npc.id
      end
      return npc
    end
  end
  return nil
end
function Combat.on_entity_damaged(event)
  local amount=event.final_damage_amount or 0
  if amount<=0 then return end
  local shooter=npc_for_character(event.cause)
  if not shooter and event.cause then
    local id=Equipment.owner_for_cause(event.cause)
    shooter=id and storage.npcs and storage.npcs[id]
  end
  if shooter and event.entity and event.entity.valid
      and hostile(shooter.force,event.entity.force) then
    shooter.combat=shooter.combat or {}
    shooter.combat.damage_dealt_total=(shooter.combat.damage_dealt_total or 0)+amount
  end
  local victim=npc_for_character(event.entity)
  if victim then
    victim.combat=victim.combat or {}
    victim.combat.health_lost_total=(victim.combat.health_lost_total or 0)+amount
    victim.combat.hurt_tick=event.tick or game.tick
  end
end
function Combat.on_entity_died(event)
  -- The fixture and main loop may never tick this body again. Close its live
  -- decision here, while the native dying LuaEntity is still inspectable.
  local victim=npc_for_character(event.entity)
  if victim and victim.combat then
    finish_tactic(victim,event.tick or game.tick,"body_lost")
    clear_movement(victim)
    victim.combat.active=false
    victim.combat.mode="dead"
    victim.combat.reason="body_lost"
  end
  local shooter=npc_for_character(event.cause)
  if not shooter and event.cause then
    local id=Equipment.owner_for_cause(event.cause)
    shooter=id and storage.npcs and storage.npcs[id]
  end
  if shooter and event.entity and event.entity.valid
      and hostile(shooter.force,event.entity.force) then
    shooter.combat=shooter.combat or {}
    shooter.combat.kills_total=(shooter.combat.kills_total or 0)+1
  end
  -- A pursuer physically dying clears the threat even when an armed teammate,
  -- player, or supplied turret fired the final shot. Credit requires the exact
  -- native target unit identity and current hostile force relation.
  if event.entity and event.entity.valid and event.entity.unit_number then
    local dead_id=event.entity.unit_number
    for _,npc in pairs(storage.npcs or {}) do
      local state=npc.combat
      local tracking=state and (state.target and state.target.valid
        and state.target.unit_number==dead_id
        or state.tactical and state.tactical.target_unit_number==dead_id)
      if state and state.active and tracking and npc.force
          and hostile(npc.force,event.entity.force) then
        state.pursuer_clears_total=(state.pursuer_clears_total or 0)+1
      end
    end
  end
end

function Combat.observe(npc)
  local c = npc.character
  if not c or not c.valid then return {body_valid = false, active = false, mode = "unavailable"} end
  local traits = axes(npc)
  local target, d, count, strength = scan(c, 18 + traits.defense * 12, npc, game.tick)
  local equipment = weapon(c,d)
  local armor_inventory=c.get_inventory(defines.inventory.character_armor)
  local armor_stack=armor_inventory and armor_inventory[1]
  local state = npc.combat or {}
  local result = {body_valid = true, active = state.active == true, mode = state.mode or "idle",
    health_fraction = (c.health or 0) / math.max(1, c.max_health or 1), weapon_ready = equipment.ready,
    gun = equipment.gun,gun_category=equipment.category,
    gun_cooldown=equipment.cooldown,ammo_name=equipment.ammo_name,
    armor=armor_stack and armor_stack.valid_for_read and armor_stack.name or nil,
    ammunition_bullets = equipment.ammunition_bullets,
    ammunition_units=equipment.ammo_units,weapon_range = equipment.range,
    nearby_hostiles = count, threat_strength = strength, reason = state.reason,
    entries = state.entries or 0, interrupted_job_id = state.interrupted_job_id,
    canceled_jobs = state.canceled_jobs or 0, path_requests = state.path_requests or 0,
    path_results = state.path_results or 0, movement_recoveries = state.movement_recoveries or 0,
    damage_dealt=state.damage_dealt_total or 0,kills=state.kills_total or 0,
    pursuer_clears=state.pursuer_clears_total or 0,
    health_lost=state.health_lost_total or 0,
    sprint_ticks=state.sprint_ticks or 0,fire_sprint=state.fire_sprint==true,
    sprint_abort_reason=state.sprint_abort_reason,
    sprint_cooldown_until=state.fire_sprint_cooldown_until,
    sprint_unreachable_until=state.fire_sprint_unreachable_until,
    courage = traits.courage, aggression = traits.aggression, defense = traits.defense}
  if target then result.nearest_hostile = {name = target.name, type = target.type, force = target.force.name,
    position = copy_position(target.position), distance = d, health = target.health} end
  if state.goal then result.retreat_goal = copy_position(state.goal); result.retreat_source = state.goal_source end
  if state.movement then
    result.movement_reason = state.movement.reason or state.movement.failed
    result.movement_attempts = state.movement.recovery_attempts or 0
    result.movement_recovery = state.movement.recovery and {
      kind = state.movement.recovery.kind, target = state.movement.recovery.target,
      started_tick = state.movement.recovery.started_tick}
  end
  if state.tactical then result.tactical={id=state.tactical.id,
    target=state.tactical.target,until_tick=state.tactical.until_tick,
    in_range_ticks=state.tactical.in_range_ticks or 0} end
  local w=state.wave
  if w then result.wave={n=w.n,ratio=math.floor(math.min(w.ratio,99)*100+0.5)/100,distance=math.floor(w.distance),
    standoff=w.standoff,on_it=w.on_it,tick=w.tick} end
  result.keeping_clear=state.clear~=nil
  result.clears=state.clears or 0
  result.odds_log=state.odds_log
  result.squad=Squad.observe(npc)
  if Equipment and Equipment.observe then result.equipment=Equipment.observe(npc) end
  return result
end

-- true means combat/recovery owns the physical body; caller pauses Worker/Factory.
-- Put on the best armor and take up the best gun and ammunition it owns now (after arming up), not
-- only when a fight starts.
-- Biters faster than it, and no turret to fall back on: no loaded one of its own, or of the team's, within a short run
-- (RUN_TO_COVER) that is not past them. Cover that near, it backs off to it, shooting as it goes (the user, 2026-10-26:
-- run to where turrets cover it, firing its gun); farther, the run only gets it bitten from behind: it stands and fights.
-- (Its own turrets past the biters counted as cover before, and the run to them went through them.)
function Combat.cornered(npc, target)
  local c = npc.character
  if not Combat.outrun_by(npc, target) then return false end
  for _, t in ipairs(c.surface.find_entities_filtered{force = c.force, type = {"ammo-turret", "electric-turret",
      "fluid-turret"}, position = c.position, radius = RUN_TO_COVER}) do
    if t.valid and loaded(t) and reachable(npc, target, t.position) then return false end
  end
  local cover = TeamWorld.active() and target.position and TeamWar.cover(npc, target.position)
  return not (cover and reachable(npc, target, cover.position))
end

-- Whether the turrets it carries go down now: against a player or an engineer, or a group of biters (SWARM) wherever it
-- is, home or not (the user, 2026-10-26: run 52's engineer, caught by 18 biters near home, kept its turrets for the
-- spot they were meant for and died; a player puts them down at their feet and picks them up after). Getting away,
-- one behind it and no more.
function Combat.drops_turrets(npc, target, state)
  if not (target and target.valid) then return false end
  if state.breaking and (state.breaking.dropped or 0) >= 1 then return false end
  return Turrets.wanted(target) or (target.type == "unit" and (state.threat_count or 0) >= SWARM)
end

-- The rule for moving in a fight, over the options that are possible now (their ids): the index of
-- the one to take, or nil to leave it to the scorer.
function Combat.rule_pick(options, d, low, high, mobile)
  local function find(...)
    for _, id in ipairs({...}) do
      for i, option in ipairs(options) do if option.id == id then return i end end
    end
  end
  if mobile and d <= low then return find("kite", "hold", "retreat") end   -- (hold: behind its turrets already)
  if d <= high then return find("hold", "strafe_left", "strafe_right", "kite") end
  return find("advance", "hold")
end

function Combat.equip_now(npc)
  if npc and npc.character and npc.character.valid then return equip_owned(npc, 20, game.tick) end
  return false
end

-- What it would fight with now (its gun and the rounds it owns for it), for the odds of an attack (combat_odds.lua).
function Combat.gear(npc)
  if npc and npc.character and npc.character.valid then return weapon(npc.character, 15, npc, game.tick) end
end

-- Biters it cannot walk away from: a fight with them is one it cannot leave, so whether to have it is decided before
-- they are on it (O-2: run 34's engineer walked into a wave of 60 to build turrets; run 52's walked back to an attack
-- with a pistol). Every WATCH_EVERY ticks it looks at the biters within WATCH: the odds of a fight where it stands (its
-- gun, the turrets it would put down at its feet, the loaded turrets of its own and its team's that reach it), whether
-- any is on it already, and how far they see.
local WATCH, WATCH_EVERY, FENCE_HOLD, FENCE_MAX, FENCE_RESET, ODDS_KEPT, WAVE_CELL = 56, 30, 120, 600, 3600, 8, 8
local function try(f) local ok, v = pcall(f); if ok then return v end end
-- On it: hurt lately, one at its throat, or one going for it (its attack is on it).
function Combat.on_it(npc, foes, tick)
  local c = npc.character
  if tick - ((npc.combat or {}).hurt_tick or -HURT_RECENT - 1) <= HURT_RECENT then return true end
  for _, f in ipairs(foes) do
    local e = f.entity
    if e and e.valid then
      if distance(e.position, c.position) <= AT_ITS_THROAT then return true end
      -- (an attack it was sent on, or one it turned to: either may be on it)
      for _, field in ipairs({"distraction_command", "command"}) do
        local after = try(function() local d = e.commandable[field]; return d and d.target end)
        if after == c then return true end
      end
    end
  end
  return false
end
-- Static things that shoot (worms, enemy turrets) do not come for it but hit anything inside their reach, and hard:
-- a recall walked it 68 tiles from a behemoth worm's nest and it died in two seconds (O-8). The nearest whose reach
-- (plus WORM_MARGIN) it stands in, unless it chose that fight (an attack on nests: its objective).
local WORM_MARGIN, WORM_LOOK = 6, 80
-- Hurt on an attack it chose (O-9, the user's clear: below 60% it stepped out of the worms' and biters' reach, and went
-- back in healed): below ATTACK_HURT the worms at its objective count against it like any others, and it breaks off as
-- if badly hurt, until it is back over ATTACK_HEALED.
Combat.ATTACK_HURT, Combat.ATTACK_HEALED = 0.6, 0.8   -- (healed: as agent.lua regrouping)
function Combat.stepping_out(npc, c, tick)
  local state = npc.combat or {}
  if not (npc.combat_objective and tick < (npc.combat_mission_until or 0)) then state.stepped_out = nil; return false end
  local health = (c.health or 0) / math.max(1, c.max_health or 1)
  if health < Combat.ATTACK_HURT then state.stepped_out = true
  elseif health >= Combat.ATTACK_HEALED then state.stepped_out = nil end
  return state.stepped_out == true
end
local function static_reach_of(npc, c, tick)
  local objective = npc.combat_objective and tick < (npc.combat_mission_until or 0) and npc.combat_objective
  if objective and Combat.stepping_out(npc, c, tick) then objective = nil end
  local best, deepest
  for _, e in ipairs(c.surface.find_entities_filtered{position = c.position, radius = WORM_LOOK,
      type = {"turret", "ammo-turret", "electric-turret", "fluid-turret"}}) do
    if e.valid and Odds.hostile(c.force, e.force)
        and not (objective and distance(e.position, objective) <= OBJECTIVE_RADIUS) then
      local reach = static_reach(e) + WORM_MARGIN
      local inside = reach - distance(e.position, c.position)
      if inside > 0 then
        local one = {x = e.position.x, y = e.position.y, reach = reach, inside = inside}
        if not deepest or inside > deepest then best, deepest = one, inside end
        best.all = best.all or {}
        best.all[#best.all + 1] = one
      end
    end
  end
  if best then   -- (every one it stands in the reach of, the deepest first: its way out is away from all of them)
    local all = {}
    for _, one in ipairs(best.all or {best}) do if one ~= best then all[#all + 1] = one end end
    table.insert(all, 1, best)
    best.all = all
  end
  return best
end
function Combat.watch(npc, tick)
  local state, c = npc.combat, npc.character
  local w = state.wave
  if w and tick - w.tick < WATCH_EVERY then return w end
  local foes = Odds.theirs(npc, c.position, WATCH)
  local worm = static_reach_of(npc, c, tick)
  if #foes == 0 and worm then
    -- (none that move: only the reach of what stands and shoots, which it leaves however it reads its odds)
    w = {tick = tick, n = 0, ratio = 0, nearest = {x = worm.x, y = worm.y}, distance = distance(c.position, worm),
      standoff = worm.reach, keep = worm.reach, on_it = false, at = {}, worm = worm}
    state.wave = w
    return w
  end
  if #foes == 0 then state.wave = nil; return nil end
  local nearest, near, sees, fastest = nil, nil, Odds.NOTICE, 0
  for _, f in ipairs(foes) do
    local d = distance(f.entity.position, c.position)
    if not near or d < near then nearest, near = f.entity, d end
    sees = math.max(sees, f.facts.sees or 0)
    fastest = math.max(fastest, f.facts.speed or 0, f.entity.speed or 0)
  end
  local gear = weapon(c, 15, npc, tick)
  local fight = Odds.fight(Odds.ours(npc, c.position, gear, {carried = #foes >= SWARM, cover = true,
    foes = foes, gear_of = function(h) return weapon(h, 15) end}), foes)
  -- (where they are, every one of them, kept as the middles of the 8-tile cells they stand in: Combat.in_reach)
  local at, cells = {}, {}
  for _, f in ipairs(foes) do
    local p = f.entity.position
    local key = math.floor(p.x / WAVE_CELL) .. ":" .. math.floor(p.y / WAVE_CELL)
    if not cells[key] then
      cells[key] = true
      at[#at + 1] = {x = (math.floor(p.x / WAVE_CELL) + 0.5) * WAVE_CELL, y = (math.floor(p.y / WAVE_CELL) + 0.5) * WAVE_CELL}
    end
  end
  w = {worm = worm, tick = tick, n = fight.n, ratio = fight.ratio, nearest = copy_position(nearest.position), distance = near,
    standoff = sees + Odds.MARGIN, on_it = Combat.on_it(npc, foes, tick), at = at,
    -- (and as far again as the fastest of them comes in two of its looks: a wave of 23 coming through its base was
    -- on it before it stepped out, the death-world run of 2026-10-28)
    keep = sees + Odds.MARGIN + fastest * WATCH_EVERY * 2}
  state.wave = w
  return w
end
-- What it chose against them and why, the last few times (for learning the odds' weights from how its fights go).
local function note_odds(npc, w, tick, choice)
  local s = npc.combat
  s.odds_log = s.odds_log or {}
  s.odds_log[#s.odds_log + 1] = {tick = tick, n = w.n, ratio = math.floor(math.min(w.ratio, 99) * 100 + 0.5) / 100,
    on_it = w.on_it, distance = math.floor(w.distance), choice = choice}
  while #s.odds_log > ODDS_KEPT do table.remove(s.odds_log, 1) end
end

-- A fight with biters it would lose, and is not in yet: it keeps clear. A fight it is in that turns bad while they are
-- not on it (more coming than it took on) it leaves the same way. With them on it there is no choice: it fights.
function Combat.keeps_clear(npc, w, tick)
  if w and w.worm then return true end   -- (inside a worm's or turret's reach: out, whatever else; they do not chase)
  if not w or w.on_it then return false end
  local state = npc.combat
  -- (bitten since the last look: on it now, without waiting for the next)
  if tick and tick - (state.hurt_tick or -HURT_RECENT - 1) <= HURT_RECENT then w.on_it = true; return false end
  return w.ratio < (state.active and not state.clear and Odds.LOSING or Odds.ENGAGE)
end
-- Keeping clear: it holds its fire (a shot calls the rest of them on it) and keeps out of their sight, back toward home
-- and round the nests it knows of, while their attack is spent on the walls and turrets. No walk of its own takes it in
-- again meanwhile (the fence): each time it finds itself inside their sight, the work that walked it there is broken
-- off, as a fight breaks off work (its owner plans it again), and once out it holds a moment before anything else moves
-- it. It goes in once the odds turn (fewer of them, more turrets) or they have gone. True while it owns the body.
local function keep_clear(npc, w, tick)
  local c, state = npc.character, npc.combat
  stop_shooting(c); state.march_fire = nil
  if state.active then
    finish_tactic(npc, tick, "keep_clear")
    state.active, state.finished_tick, state.retreat_latched, state.breaking = false, tick, nil, nil
  end
  if not state.clear then
    state.clear = {since = tick}
    state.clears = (state.clears or 0) + 1
    note_odds(npc, w, tick, "keep_clear")
  end
  state.mode, state.reason = "keep_clear", "losing_odds"
  if w.worm and distance(c.position, w.worm) < w.worm.reach then
    if state.goal_source ~= "keep_clear" or tick >= (state.clear.replan_at or 0) then
      clear_movement(npc)
      -- (away from all of them together, and from the biters about it too: away from one worm only walked it into
      -- the biters, or back and forth between two, the review)
      local here = c.position
      local vx, vy = 0, 0
      for _, one in ipairs(w.worm.all or {w.worm}) do
        local dx, dy = here.x - one.x, here.y - one.y
        local l = math.max(0.01, math.sqrt(dx * dx + dy * dy))
        vx, vy = vx + dx / l * one.inside, vy + dy / l * one.inside
      end
      if (w.n or 0) > 0 and w.nearest and not (w.nearest.x == w.worm.x and w.nearest.y == w.worm.y) then
        local dx, dy = here.x - w.nearest.x, here.y - w.nearest.y
        local l = math.max(0.01, math.sqrt(dx * dx + dy * dy))
        local weight = math.max(w.worm.inside or 1, 10)
        vx, vy = vx + dx / l * weight, vy + dy / l * weight
      end
      local from = {x = here.x - vx, y = here.y - vy}
      local p = Combat.away(npc, here, from, state.clear.turns or 0, (w.worm.inside or 6) + 6)
      state.goal = c.surface.find_non_colliding_position("character", p, 8, 0.5) or p
      state.goal_source, state.clear.replan_at = "keep_clear", tick + WATCH_EVERY
    end
    if not state.clear.inside then
      state.clear.inside = true
      state.clear.entries = (state.clear.entries or 0) + 1
      if Worker.cancel and Worker.cancel(npc, "combat_interrupt") then state.canceled_jobs = (state.canceled_jobs or 0) + 1 end
    end
    state.fence_until = tick + FENCE_HOLD
    if move(npc, state.goal, 1.5, tick, true) == "failed" then
      state.clear.turns, state.clear.replan_at = (state.clear.turns or 0) + 1, tick
    end
    return true
  end
  if w.distance < (w.keep or w.standoff) then
    if not state.clear.inside then
      state.clear.inside = true
      -- (a quiet minute out here since it was last walked in: the next hold starts short again)
      if tick - (state.clear.out_since or tick) >= FENCE_RESET then state.clear.entries = 0 end
      state.clear.entries = (state.clear.entries or 0) + 1
      if state.clear.entries > 1 then note_odds(npc, w, tick, "fence:" .. state.clear.entries) end
      if Worker.cancel and Worker.cancel(npc, "combat_interrupt") then state.canceled_jobs = (state.canceled_jobs or 0) + 1 end
    end
    -- (each time its work walks it in again while they are there, it holds out here twice as long, 10 s at most: its
    -- work out of their reach goes on in between)
    state.fence_until = tick + math.min(FENCE_MAX, FENCE_HOLD * 2 ^ ((state.clear.entries or 1) - 1))
    if state.goal_source ~= "keep_clear" or tick >= (state.clear.replan_at or 0) then
      clear_movement(npc)
      local p = Combat.away(npc, c.position, w.nearest, state.clear.turns or 0, (w.keep or w.standoff) - w.distance + 4)
      state.goal = c.surface.find_non_colliding_position("character", p, 8, 0.5) or p
      state.goal_source, state.clear.replan_at = "keep_clear", tick + WATCH_EVERY
    end
    if move(npc, state.goal, 1.5, tick, true) == "failed" then
      state.clear.turns, state.clear.replan_at = (state.clear.turns or 0) + 1, tick
    end
    return true
  end
  if state.clear.inside or not state.clear.out_since then state.clear.out_since = tick end
  state.clear.inside = nil   -- (out of their sight again: the next walk in is broken off too)
  if state.goal_source == "keep_clear" then clear_movement(npc); state.goal, state.goal_source = nil, nil end
  if tick < (state.fence_until or 0) then stop_walking(c); return true end
  return false
end
-- Whether a place is inside the sight of biters it is keeping clear of (no building there meanwhile: designed_start's
-- fought_over; the fence would only send it out of a site it walked in to, again and again, O-4).
function Combat.in_reach(npc, position, tick)
  local s = npc and npc.combat
  local w = s and s.clear and s.wave
  if not (w and position and (tick or game.tick) - w.tick <= 2 * WATCH_EVERY) then return false end
  for _, p in ipairs(w.at or {w.nearest}) do
    if distance(p, position) < (w.keep or w.standoff) + WAVE_CELL * 0.71 then return true end   -- (a cell's middle to its corner)
  end
  return false
end
-- Biters lingering at its own buildings, not attacking it (O-6): a wave's leftovers camped by a drill field or by
-- what they broke. Nothing else sees them: no hit raises the alarm, they stand beyond what its fights look at, and
-- the rebuild waits on them ("biters_near") for ever. Looked for round home (LINGER_LOOK) now and then: a group
-- within LINGER_NEAR of its own buildings, there since an earlier look at least LINGER_FOR before (not a wave passing
-- through, which hits and is answered as an attack). Its odds are judged as the fight's look judges them where it
-- would stand (its gun and the turrets it would put down, against every biter within WATCH of that spot; no cover
-- counted, so the look on the way in never reads them worse than this). The group {x, y, n, ratio, since}, nearest
-- first, or nil.
Combat.LINGER_LOOK, Combat.LINGER_NEAR, Combat.LINGER_FOR, Combat.LINGER_GROUP = 300, 24, 1800, 16
Combat.CLEAR_NEAR, Combat.CLEAR_RETRY = 18, 3600
-- (its own buildings; in the team world its teammates' and the players' too: one team, O-5's note on O-6)
local function sides(c)
  local out = {c.force}
  if TeamWorld.active() then
    for _, f in pairs(game.forces) do if f ~= c.force and c.force.get_friend(f) then out[#out + 1] = f end end
  end
  return out
end
local function own_building_near(npc, c, p, radius)
  for _, e in ipairs(c.surface.find_entities_filtered{position = p, radius = radius, force = sides(c), limit = 4}) do
    if e.valid and e.type ~= "character" then return true end
  end
  for _, r in ipairs(npc.rebuild or {}) do   -- (or one they broke, waiting to go back: the review)
    if r.at and r.wait == "biters_near" and distance(r.at, p) <= radius then return true end
  end
  return false
end
Combat.OUTPOST_LOOK = 120   -- round each base of its own away from home (a taken one, an outpost) too
function Combat.intruders(npc, tick)
  local c, home = npc.character, npc.home
  if not (c and c.valid and home) then return nil end
  local groups, counted = {}, {}
  local places = {{home, Combat.LINGER_LOOK}}
  for _, o in ipairs(npc.outposts or {}) do places[#places + 1] = {o, Combat.OUTPOST_LOOK} end
  local found = {}
  for _, place in ipairs(places) do
    for _, e in ipairs(c.surface.find_entities_filtered{position = place[1], radius = place[2], type = "unit",
        limit = 300}) do
      local id = e.valid and (e.unit_number or e)
      if id and not counted[id] then counted[id] = true; found[#found + 1] = e end
    end
  end
  for _, e in ipairs(found) do
    if e.valid and Odds.hostile(c.force, e.force) then
      local into
      for _, g in ipairs(groups) do
        if distance(g, e.position) <= Combat.LINGER_GROUP then into = g; break end
      end
      if not into then into = {x = e.position.x, y = e.position.y, n = 0}; groups[#groups + 1] = into end
      into.n = into.n + 1
      into.x, into.y = into.x + (e.position.x - into.x) / into.n, into.y + (e.position.y - into.y) / into.n
    end
  end
  local seen, best = {}, nil
  local before = npc.lingering or {}
  for _, g in ipairs(groups) do
    if own_building_near(npc, c, g, Combat.LINGER_NEAR) then
      local key = math.floor(g.x / 32) .. ":" .. math.floor(g.y / 32)
      g.since = before[key] or tick
      seen[key] = g.since
      if tick - g.since >= Combat.LINGER_FOR and (not best or distance(g, c.position) < distance(best, c.position)) then
        best = g
      end
    end
  end
  npc.lingering = seen
  if not best then return nil end
  -- where it would stand to shoot them: CLEAR_NEAR short of them, on its side
  local dx, dy = c.position.x - best.x, c.position.y - best.y
  local l = math.max(0.01, math.sqrt(dx * dx + dy * dy))
  local spot = {x = best.x + dx / l * Combat.CLEAR_NEAR, y = best.y + dy / l * Combat.CLEAR_NEAR}
  local foes = Odds.theirs(npc, spot, WATCH)
  local gear = weapon(c, 15, npc, tick)
  local fight = Odds.fight(Odds.ours(npc, spot, gear, {carried = #foes >= SWARM}), foes)
  best.ratio, best.x, best.y, best.n = fight.ratio, math.floor(best.x), math.floor(best.y), #foes
  return best
end
-- Going to clear a lingering group it can beat (npc.clearing, set by agent.lua): it walks to CLEAR_NEAR of them and
-- its fight takes over from there (turrets at its feet against a group, as against any). The work in hand is broken
-- off as a fight breaks it off. A walk that finds no way, or takes longer than it was given, is given up. True while it
-- holds the body.
function Combat.go_clear(npc, tick)
  local cl, c, state = npc.clearing, npc.character, npc.combat
  if not (cl and tick < (cl.until_tick or 0)) then
    if state.goal_source == "clear" then clear_movement(npc); state.goal, state.goal_source = nil, nil end
    return false
  end
  -- (there, and still none in its sight: they moved; the next look sends it on to where they are now)
  if cl.reached then return false end
  if not cl.started then
    cl.started = tick
    if Worker.cancel and Worker.cancel(npc, "combat_interrupt") then state.canceled_jobs = (state.canceled_jobs or 0) + 1 end
    note_odds(npc, {n = cl.n or 0, ratio = cl.ratio or 0, on_it = false, distance = distance(c.position, cl)}, tick, "clear")
  end
  if state.goal_source ~= "clear" or not state.goal or not cl.aimed or distance(cl.aimed, cl) > 1 then   -- (re-aimed: walked anew)
    clear_movement(npc)
    cl.aimed = {x = cl.x, y = cl.y}
    local dx, dy = c.position.x - cl.x, c.position.y - cl.y
    local l = math.max(0.01, math.sqrt(dx * dx + dy * dy))
    local p = {x = cl.x + dx / l * (Combat.CLEAR_NEAR - 4), y = cl.y + dy / l * (Combat.CLEAR_NEAR - 4)}
    state.goal = c.surface.find_non_colliding_position("character", p, 8, 0.5) or p
    state.goal_source = "clear"
  end
  state.mode, state.reason = "clear", "lingering_biters"
  local moved = move(npc, state.goal, 2, tick, false)
  if moved == "failed" then   -- no way there: not tried again a while (agent.lua calls for help meanwhile)
    npc.clear_failed = {x = cl.x, y = cl.y, until_tick = tick + Combat.CLEAR_RETRY}
    npc.clearing, state.goal, state.goal_source = nil, nil, nil
    return false
  end
  if moved == "arrived" then
    state.goal, state.goal_source = nil, nil
    -- (there and in reach of them, its fight's; short of them still, on it walks)
    if distance(c.position, cl) <= Combat.CLEAR_NEAR then cl.reached = tick; return false end
    return true
  end
  return true
end

-- No way of getting about may bypass its judgment of a fight before contact (O-8: on a build job it drove its tank
-- to a nest cluster, 57 biters and big worms, and got out among them: while it drives, its fights are not ticked).
-- Whether a drive should stop short: biters it would lose to near the road ahead (`ahead`, DRIVE_AHEAD tiles on) or
-- near where it is going (`goal`), judged as on foot (its gun, the turrets it would put down, the team's turrets
-- that reach the spot; the vehicle's own gun is not counted). A reason ("biters_ahead", "biters_at_goal") or nil.
Combat.DRIVE_AHEAD = 60
function Combat.should_abort_drive(npc, tick, goal, ahead)
  local c = npc.character
  if not (c and c.valid) then return nil end
  for _, at in ipairs({ahead or false, goal or false}) do
    if at then
      local foes = Odds.theirs(npc, at, WATCH)
      if #foes > 0 then
        local gear = weapon(c, 15, npc, tick)
        local fight = Odds.fight(Odds.ours(npc, at, gear, {carried = #foes >= SWARM, cover = true, foes = foes,
          gear_of = function(h) return weapon(h, 15) end}), foes)
        if fight.ratio < Odds.ENGAGE then
          local w = {n = fight.n, ratio = fight.ratio, on_it = false, distance = distance(c.position, at)}
          npc.combat = npc.combat or {}
          note_odds(npc, w, tick, at == goal and "drive_stop:goal" or "drive_stop:ahead")
          return at == goal and "biters_at_goal" or "biters_ahead"
        end
      end
    end
  end
  return nil
end

-- Whether it is keeping clear of a fight it would lose (for the walks that would take it there: the recall, agent.lua).
function Combat.keeping_clear(npc, tick)
  local s = npc and npc.combat
  return s ~= nil and s.clear ~= nil and s.wave ~= nil and (tick or game.tick) - s.wave.tick <= 2 * WATCH_EVERY
end

function Combat.tick(npc, tick)
  local c = npc.character
  if not c or not c.valid then
    if npc.combat then
      finish_tactic(npc,tick,"body_lost")
      clear_movement(npc)
      npc.combat.active,npc.combat.mode=false,"unavailable"
    end
    return false
  end
  if c.unit_number then
    storage.combat_body_index=storage.combat_body_index or {}
    storage.combat_body_index[c.unit_number]=npc.id
  end
  local state=npc.combat or {};npc.combat=state
  if state.active==nil then state.active=false end
  state.mode=state.mode or "idle"
  state.episode=state.episode or 0
  state.entries=state.entries or 0
  local traits = axes(npc)
  if not state.last_scan or tick - state.last_scan >= 15 or (state.target and not valid_enemy(c, state.target)) then
    state.target, state.target_distance, state.threat_count, state.threat_strength = scan(c, 18 + traits.defense * 12, npc, tick)
    state.last_scan = tick
  end
  -- Biters it would lose to and not on it yet: it keeps clear of them, whatever it was doing (see keep_clear).
  local wave = Combat.watch(npc, tick)
  if Combat.keeps_clear(npc, wave, tick) then return keep_clear(npc, wave, tick) end
  if state.clear then
    state.clear = nil
    if state.goal_source == "keep_clear" then clear_movement(npc); state.goal, state.goal_source = nil, nil end
    if not state.active then state.mode, state.reason = "idle", nil end
  end
  local target=Squad.select_target(npc,state.target,tick)
  if target and not (valid_enemy(c,target) and engage(npc,target,tick,distance(c.position,target.position))) then
    target=nil
  end
  state.target=target
  if not target and state.march_fire then
    stop_shooting(c); state.march_fire=nil
  end
  if not target then
    finish_tactic(npc,tick,"target_lost")
    state.fire_sprint=nil
    state.squad_fire_reposition=nil
    local held=Equipment.tick(npc,tick,nil,nil,traits)
    if held then return true end
    if not state.active and Combat.go_clear(npc,tick) then return true end
    if not state.active then return false end
    stop_shooting(c)
    state.quiet_since = state.quiet_since or tick
    -- (breaking contact: on until it is as far as it meant to go, for a while)
    local breaking = state.breaking and state.goal and tick - state.quiet_since < BREAK_FOR
    if tick - state.quiet_since >= 60 and not breaking then
      clear_movement(npc)
      state.active, state.mode, state.goal, state.reason, state.retreat_latched = false, "idle", nil, nil, nil
      state.breaking = nil
      state.finished_tick = tick
      return false
    end
    state.mode = "recover"
    if state.goal then
      local moved = move(npc, state.goal, 1.5, tick, state.goal_source == "break_contact")
      if moved == "arrived" and Combat.break_turn(npc, state, tick) then
        -- out of their sight: now hard to one side
      elseif moved == "arrived" or moved == "failed" then state.breaking = nil end   -- as far as it goes
    else stop_walking(c) end
    return true
  end
  state.quiet_since = nil
  -- On a march, biters are shot at on the move: it keeps walking its route, away from the nest that
  -- sends them, instead of stopping to fight one that keeps sending more (it only stops for a fight it
  -- chose, or once it has no gun or ammunition to shoot with).
  -- (a group of them on it is no longer a march past: it stops and fights, its turrets down at its feet, the review,
  -- 2026-10-26: marching back to an attack on its base, it walked on through a wave without them)
  if not state.active and target.type == "unit" and npc.marching and tick < (npc.marching.until_tick or 0)
      and (state.threat_count or 0) < SWARM
      and not (npc.combat_objective and tick < (npc.combat_mission_until or 0)
        and distance(target.position, npc.combat_objective) <= OBJECTIVE_RADIUS) then
    local d = distance(c.position, target.position)
    refresh_loadout(npc, d, tick)
    local gear = weapon(c, d, npc, tick)
    if gear.ready then
      if d <= gear.range * .95 then
        c.selected_gun_index = gear.slot
        c.selected = target
        c.shooting_state = {state = defines.shooting.shooting_selected, position = target.position}
        state.march_fire = true
      end
      state.mode, state.reason = "march_fire", nil
      return false
    end
  end
  if not state.active then
    state.active, state.episode, state.entries = true, state.episode + 1, state.entries + 1
    state.retreat_latched, state.breaking = nil, nil
    state.started_tick = tick
    state.interrupted_job_id = npc.current_job and npc.current_job.id or nil
    local receipt = Worker.cancel(npc, "combat_interrupt")
    if receipt then state.canceled_jobs = (state.canceled_jobs or 0) + 1 end
    if wave and target.type == "unit" then note_odds(npc, wave, tick, wave.on_it and "fight_on_it" or "fight") end
    -- Keep queued jobs. The canceled active job has a terminal receipt and must
    -- be re-planned by its project/decision owner; never resurrect its identity.
  end
  local target_distance=distance(c.position,target.position)
  refresh_loadout(npc,target_distance,tick)
  -- Against a player or an engineer, or a group of biters: turrets it carries go down between them, loaded, as it
  -- fights (picked up again once it is over: defense_kit.lua).
  if Combat.drops_turrets(npc,target,state) then
    local before=npc.combat_turrets and npc.combat_turrets.placed or 0
    Turrets.tick(npc,target,tick,function(spec) return Worker.place_one(npc,spec) end,state.threat_strength)
    if state.breaking and (npc.combat_turrets and npc.combat_turrets.placed or 0)>before then
      state.breaking.dropped=(state.breaking.dropped or 0)+1   -- one behind it as it gets away
    end
  end
  local equipment = weapon(c,target_distance,npc,tick)
  local health = (c.health or 0) / math.max(1, c.max_health or 1)
  -- Hurt in a fight: eat a fish it carries, as players do (one a second, each heals 80).
  if health < 0.5 and tick >= (state.next_meal or 0) then
    local inventory = c.get_main_inventory()
    if inventory and inventory.get_item_count("raw-fish") > 0 and inventory.remove({name="raw-fish", count=1}) == 1 then
      c.health = math.min(c.max_health, c.health + 80)
      state.next_meal = tick + 60
      health = c.health / math.max(1, c.max_health)
    end
  end
  local reason
  local prior=state.last_tactical_outcome
  -- (against biters their odds say whether it is losing, not a count of them: combat_odds.lua)
  local biters=target.type=="unit" and wave~=nil
  if health < 0.35 + (0.5 - traits.courage) * 0.3 or Combat.stepping_out(npc, c, tick) then reason = "low_health"
  elseif not equipment.ready then reason = "no_ammunition_or_gun"
  elseif prior and prior.target_unit_number==(target.unit_number or 0)
      and tick-prior.tick<=300 and prior.health_lost>=8
      and prior.damage_dealt<prior.health_lost*1.25 then reason="losing_exchange"
  elseif prior and prior.target_unit_number==(target.unit_number or 0)
      and tick-prior.tick<=300 and prior.ammo_spent>=8
      and prior.damage_dealt<math.max(2,prior.ammo_spent*.4)
      and distance(c.position,target.position)<equipment.range*.9 then reason="ineffective_weapon"
  elseif biters and wave.ratio < Odds.LOSING then reason = "outnumbered"
  elseif not biters and (state.threat_strength or 0) > 2 + traits.courage * 6 + traits.defense * 2 + traits.aggression * 2 then reason = "outnumbered"
  elseif (target.type == "unit-spawner" or target.type == "turret") and traits.aggression < 0.85
      and tick >= (npc.combat_mission_until or 0) then reason = "enemy_base_avoidance"   -- not on an attack it chose
  elseif not biters and target.max_health > c.max_health * (1 + traits.courage) then reason = "stronger_enemy"
  end
  -- Running from biters that are faster than it, with none of its own turrets near to run to, only
  -- gets it bitten from behind without shooting back: it stands and fights instead (as players do,
  -- dropping a turret if it has one; see below).
  if reason and reason ~= "no_ammunition_or_gun" and reason ~= "enemy_base_avoidance"
      and Combat.cornered(npc, target) then
    reason, state.retreat_latched = nil, nil
  end
  if reason == "outnumbered" or reason == "low_health" or reason == "stronger_enemy"
      or reason=="losing_exchange" or reason=="ineffective_weapon" then
    state.retreat_latched = true
  elseif state.retreat_latched and not reason then
    -- Do not charge back into a wave after the first casualty drops its score.
    reason = "retreat_from_wave"
  end
  -- The team world: biters outrun it on foot (0.24 against its 0.15). With the team's turrets near it
  -- falls back to them (safe_goal); with none, running only gets it caught from behind: it fights.
  local outrun=TeamWorld.active() and target.force.name=="enemy"
  if reason and outrun and reason~="no_ammunition_or_gun" then
    if not state.cover_seen or tick-state.cover_seen.tick>=60 then   -- (a look round once a second)
      state.cover_seen={tick=tick,found=TeamWar.cover(npc,target.position)~=nil}
    end
    if not state.cover_seen.found then reason,state.retreat_latched=nil,nil end
  end
  local equipment_target=target
  if reason and reason~="no_ammunition_or_gun" then equipment_target=nil end
  local equipment_owns=Equipment.tick(npc,tick,equipment_target,equipment,traits)
  if equipment_owns then
    if npc.combat_loadout then npc.combat_loadout.reserve=nil end
    finish_tactic(npc,tick,"equipment_owned")
    clear_movement(npc)
    state.mode,state.reason="equipment","equipment_owned"
    return true
  end
  if reason then
    state.squad_fire_reposition=nil
    if BREAK_REASONS[reason] and not state.breaking and not outrun then
      state.breaking={people=target.force.name~="enemy",since=tick,from=copy_position(c.position)}
      npc.broke_off=tick   -- it stays out a while and heals before going back in (agent.lua)
      if target.force.name~="enemy" then npc.lost_to={force=target.force.name,tick=tick} end
      state.goal=nil   -- a long way out now, not the next short leg
    end
    if state.breaking and Equipment.cover then Equipment.cover(npc,tick,target) end   -- slowdown, a distractor
    state.reason = reason   -- (safe_goal: out of ammunition, any run to cover is all it has)
    if state.mode ~= "retreat" or not state.goal or
        (state.goal_source == "home" and distance(c.position, state.goal) > LOCAL_HOME_RANGE) then
      clear_movement(npc)
      state.retreat_failures = 0
      state.goal, state.goal_source = safe_goal(npc, target)
    end
    state.mode, state.reason = "retreat", reason
    if not state.tactical or state.tactical.id~="retreat"
        and state.tactical.id~="seek_backup"
        or state.tactical.target_unit_number~=(target.unit_number or 0)
        or tick>=state.tactical.until_tick then
      local prior_id=state.tactical and state.tactical.id
      local prior_goal=state.goal and copy_position(state.goal)
      local prior_source=state.goal_source
      finish_tactic(npc,tick,"retreat_redecision")
      local tactic=choose_tactic(npc,target,equipment,traits,tick,true)
      if tactic then
        if tactic.id=="retreat" and prior_id=="retreat" and prior_goal
            and distance(c.position,prior_goal)>2 then
          state.goal=prior_goal
          state.goal_source=prior_source
          tactic.target=copy_position(prior_goal)
        else
          state.goal=copy_position(tactic.target)
          state.goal_source=tactic.source or tactic.id
        end
      end
    end
    local movement, failure = move(npc, state.goal, 1.5, tick,
      state.goal_source == "local_away_from_threat" or state.goal_source == "break_contact")
    if movement == "failed" then
      state.reason = failure
      if tick >= (state.replan_at or 0) then
        if state.tactical and state.tactical.id=="seek_backup" then
          state.backup_failed_provider=state.tactical.provider_id
          state.backup_failed_until=tick+180
        end
        finish_tactic(npc,tick,"retreat_replanned")
        clear_movement(npc)
        state.retreat_failures = (state.retreat_failures or 0) + 1
        state.goal, state.goal_source = safe_goal(npc, target, state.retreat_failures)
        state.replan_at = tick + 120
      end
    elseif movement == "arrived" and Combat.break_turn(npc, state, tick) then
      finish_tactic(npc,tick,"break_turn")   -- out of their reach: now hard to one side
    elseif movement == "arrived" and distance(c.position, target.position) < 10 then
      finish_tactic(npc,tick,"retreat_leg_arrived")
      state.goal, state.goal_source = safe_goal(npc, target)
    end
    -- Native shooting continues during real retreat walking when safe.
    fire(npc,target,equipment,tick)
    if state.tactical and equipment.ready
        and distance(c.position,target.position)>=state.tactical.band_low
        and distance(c.position,target.position)<=state.tactical.band_high then
      state.tactical.in_range_ticks=state.tactical.in_range_ticks+1
    end
    return true
  end
  state.reason,state.mode,state.goal="hostile_in_range","attack",nil
  local current_low=useful_band(equipment.range,kept_reach(target,equipment.range))
  local emergency=distance(c.position,target.position)<=current_low
    and (target.type=="unit" or target.type=="character" or target.type=="combat-robot")
  local reposition=state.squad_fire_reposition
  if reposition and (emergency or state.fire_sprint
      or reposition.target_unit_number~=(target.unit_number or 0)
      or tick>=reposition.until_tick or distance(c.position,reposition.goal)<=.7) then
    state.squad_fire_reposition=nil;reposition=nil
  end
  if reposition then
    move(npc,reposition.goal,.7,tick,true)
    local fired=fire(npc,target,equipment,tick)
    if fired and distance(c.position,target.position)>=current_low then
      state.squad_fire_reposition=nil
    end
    return true
  end
  if not state.tactical or state.tactical.target_unit_number~=(target.unit_number or 0)
      or tick>=state.tactical.until_tick
      or emergency and state.tactical.id~="kite" and state.tactical.id~="retreat"
        and state.tactical.id~="seek_backup" then
    finish_tactic(npc,tick,emergency and "emergency_sprint" or "tactical_redecision")
    choose_tactic(npc,target,equipment,traits,tick,false)
  end
  local tactic=state.tactical
  if tactic then
    local action=tactic.id
    local goal=tactic.target
    if action=="hold" then
      local spacing=Squad.spacing_goal(npc,target,(tactic.band_low+tactic.band_high)/2,tick)
      if spacing then goal=spacing else clear_movement(npc);goal=nil end
    end
    if goal then
      local movement=move(npc,goal,action=="retreat" and 1.5 or .7,tick,true)
      if movement=="failed" then finish_tactic(npc,tick,"movement_failed") end
    end
  else
    clear_movement(npc)
  end
  -- The body may walk and fire during the same native tick. Ammunition and
  -- damage are still consumed/applied only by the Factorio engine.
  local _,fire_reason=fire(npc,target,equipment,tick)
  if fire_reason=="unsafe_line" and state.tactical
      and (state.tactical.id=="hold" or state.tactical.id=="strafe_left"
        or state.tactical.id=="strafe_right") then
    local flank=Squad.blocked_fire_goal(npc,target,equipment,tick)
    if flank then
      finish_tactic(npc,tick,"squad_fire_reposition")
      state.squad_fire_reposition={goal=copy_position(flank),
        target_unit_number=target.unit_number or 0,until_tick=tick+90}
      move(npc,flank,.7,tick,true)
    end
  end
  if state.tactical and equipment.ready
      and distance(c.position,target.position)>=state.tactical.band_low
      and distance(c.position,target.position)<=state.tactical.band_high then
    state.tactical.in_range_ticks=state.tactical.in_range_ticks+1
  end
  return true
end

-- (the driving autopilot asks it before it goes on: drive.lua does not require combat, so it is handed the check)
local ok_drive, Drive = pcall(require, "scripts.drive")
if ok_drive and type(Drive) == "table" then Drive.abort_check = Combat.should_abort_drive end

return Combat
