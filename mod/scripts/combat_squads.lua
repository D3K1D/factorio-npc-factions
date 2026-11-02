-- Local allied coordination. All movement and damage remain native combat actions.
-- Membership is inferred from nearby, mutually friendly forces; no diplomacy changes.
local Squads = {}
local Personality = require("scripts.personality")
local RADIUS, MAX_MEMBERS = 48, 12
local function distance(a,b)
  local dx,dy=a.x-b.x,a.y-b.y
  return math.sqrt(dx*dx+dy*dy)
end
local function body(npc)
  return npc and npc.character and npc.character.valid and npc.character
end
local function friendly(a,b)
  return a==b or (a.get_friend(b) and b.get_friend(a))
end
local function hostile(a,b)
  return a~=b and b.name~="neutral" and not a.get_friend(b) and not b.get_friend(a)
    and not a.get_cease_fire(b) and not b.get_cease_fire(a)
end
local function target_ok(c,target)
  return target and target.valid and target.surface==c.surface and target.health
    and target.health>0 and target.is_military_target and hostile(c.force,target.force)
    and distance(c.position,target.position)<=RADIUS
end
local function willing(npc)
  local axes=Personality.axes(npc)
  return (axes.cooperation or .5)>=.35
end
local function members(npc)
  local c=body(npc)
  if not c or not willing(npc) then return {npc} end
  local cached=npc.combat_peer_cache
  if cached and game.tick<(cached.refresh_tick or 0) then
    local active={}
    for _,id in ipairs(cached.ids) do
      local other=storage.npcs[id]
      local b=body(other)
      if other==npc or (b and b.surface==c.surface and friendly(c.force,b.force)
          and willing(other) and distance(c.position,b.position)<=RADIUS) then
        active[#active+1]=other
      end
    end
    return active
  end
  local found={npc}
  for _,other in pairs(storage.npcs or {}) do
    local b=body(other)
    if other~=npc and b and b.surface==c.surface and friendly(c.force,b.force)
        and willing(other) and distance(c.position,b.position)<=RADIUS then
      found[#found+1]=other
    end
  end
  table.sort(found,function(a,b)
    if a==npc then return true elseif b==npc then return false end
    local da,db=distance(c.position,a.character.position),distance(c.position,b.character.position)
    return da==db and a.id<b.id or da<db
  end)
  while #found>MAX_MEMBERS do table.remove(found) end
  table.sort(found,function(a,b) return a.id<b.id end)
  local ids={}
  for i,peer in ipairs(found) do ids[i]=peer.id end
  npc.combat_peer_cache={refresh_tick=game.tick+15,ids=ids}
  return found
end

function Squads.select_target(npc,scanned,tick)
  local c=body(npc)
  if not c then return scanned end
  local group=members(npc)
  local selected=target_ok(c,scanned) and scanned or nil
  local source=npc.id
  -- A near ally's live engagement is enough to focus fire. Stale or peaceful
  -- targets are checked against current native force relations on every use.
  for _,peer in ipairs(group) do
    local seen=peer.combat_squad
    local candidate=seen and seen.target
    if seen and tick-(seen.tick or -1000)<=30 and target_ok(c,candidate)
        and (not selected or peer.id<source) then selected,source=candidate,peer.id end
  end
  local ids,rank={},1
  for i,peer in ipairs(group) do ids[i]=peer.id; if peer==npc then rank=i end end
  local old=npc.combat_squad or {}
  npc.combat_squad={tick=tick,target=selected,target_id=selected and selected.unit_number,
    members=ids,leader=ids[1],rank=rank,role=rank==1 and "lead" or "support",
    focus_source=source,spacing_goal=old.spacing_goal,spacing_until=old.spacing_until,
    spacing_moves=old.spacing_moves or 0,unsafe_shots=old.unsafe_shots or 0,
    fire_goal=old.fire_goal,fire_until=old.fire_until,
    blocked_fire_repositions=old.blocked_fire_repositions or 0}
  return selected or scanned
end

function Squads.spacing_goal(npc,target,desired_range,tick)
  local c=body(npc)
  local s=npc.combat_squad
  if not c or not s or not target_ok(c,target) or #s.members<2 then return nil end
  if s.spacing_goal and tick<(s.spacing_until or 0)
      and distance(c.position,s.spacing_goal)>.7 then return s.spacing_goal end
  s.spacing_goal=nil
  local crowded=false
  local own_vehicle=c.vehicle
  local clearance=own_vehicle and own_vehicle.valid and 6 or 2.8
  for _,id in ipairs(s.members) do
    local peer=storage.npcs[id]
    local b=body(peer)
    if b and peer~=npc and b.surface==c.surface
        and distance(c.position,b.position)<clearance then crowded=true;break end
  end
  if not crowded then return nil end
  local dx,dy=c.position.x-target.position.x,c.position.y-target.position.y
  local length=math.max(.1,math.sqrt(dx*dx+dy*dy))
  local side=s.rank%2==0 and 1 or -1
  local offset=clearance*side
  local goal={x=c.position.x-dy/length*offset,y=c.position.y+dx/length*offset}
  if distance(goal,target.position)>desired_range then
    local gx,gy=goal.x-target.position.x,goal.y-target.position.y
    local gl=math.sqrt(gx*gx+gy*gy)
    goal={x=target.position.x+gx/gl*desired_range*.95,
      y=target.position.y+gy/gl*desired_range*.95}
  end
  local p=c.surface.find_non_colliding_position("character",goal,3,.5)
  if p then s.spacing_goal={x=p.x,y=p.y};s.spacing_until=tick+45;
    s.spacing_moves=s.spacing_moves+1;return s.spacing_goal end
end

local function line_safe(npc,target,weapon,origin,count)
  local c=body(npc)
  if not c or not target_ok(c,target) then return false end
  local gun=weapon and (weapon.gun or weapon.name) or ""
  local ammo=weapon and (weapon.ammo_name or weapon.ammo) or ""
  local wide=gun=="rocket-launcher" or gun=="flamethrower" or gun=="shotgun"
      or gun=="combat-shotgun" or gun=="tank-cannon"
      or tostring(ammo):find("explosive",1,true)~=nil
  if not wide then return true end
  local impact=gun=="rocket-launcher" or gun=="flamethrower" or gun=="tank-cannon"
      or tostring(ammo):find("explosive",1,true)~=nil
  if impact then
    local entities=c.surface.find_entities_filtered{position=target.position,radius=5,
      type={"character","car","spider-vehicle"}}
    for _,b in ipairs(entities) do
      if b~=c and b~=c.vehicle and b.valid and not hostile(c.force,b.force)
          and distance(b.position,target.position)<4 then
        if count and npc.combat_squad then
          npc.combat_squad.unsafe_shots=npc.combat_squad.unsafe_shots+1
        end
        return false
      end
    end
  end
  if gun=="flamethrower" or gun=="shotgun" or gun=="combat-shotgun" then
    local start=origin or c.position
    local dx,dy=target.position.x-start.x,target.position.y-start.y
    local squared=dx*dx+dy*dy
    if squared>.01 then
      local range=weapon and weapon.range or math.sqrt(squared)
      for _,b in ipairs(c.surface.find_entities_filtered{position=start,
          radius=range,type={"character","car","spider-vehicle"}}) do
        if b~=c and b~=c.vehicle and b.valid and not hostile(c.force,b.force) then
          local bx,by=b.position.x-start.x,b.position.y-start.y
          local progress=(bx*dx+by*dy)/squared
          local lateral=math.abs(bx*dy-by*dx)/math.sqrt(squared)
          local along=progress*math.sqrt(squared)
          if along>0 and along<range and lateral<.5+along*.18 then
            if count and npc.combat_squad then
              npc.combat_squad.unsafe_shots=npc.combat_squad.unsafe_shots+1
            end
            return false
          end
        end
      end
    end
  end
  return true
end

function Squads.fire_safe(npc,target,weapon)
  return line_safe(npc,target,weapon,nil,true)
end

function Squads.blocked_fire_goal(npc,target,weapon,tick)
  local c=body(npc)
  local s=npc.combat_squad
  if not c or not s or not target_ok(c,target) then return nil end
  if line_safe(npc,target,weapon,nil,false) then s.fire_goal=nil;return nil end
  if s.fire_goal and tick<(s.fire_until or 0)
      and distance(c.position,s.fire_goal)>.7
      and line_safe(npc,target,weapon,s.fire_goal,false) then return s.fire_goal end
  local dx,dy=c.position.x-target.position.x,c.position.y-target.position.y
  local length=math.max(.1,math.sqrt(dx*dx+dy*dy))
  local ring=math.max(5,math.min(length,math.max(5,(weapon.range or 15)*.8)))
  local angle=math.atan2(dy,dx)
  local preferred=s.rank%2==0 and 1 or -1
  local best,best_distance
  for _,offset in ipairs({preferred*.7,-preferred*.7,preferred*1.2,-preferred*1.2}) do
    local at={x=target.position.x+math.cos(angle+offset)*ring,
      y=target.position.y+math.sin(angle+offset)*ring}
    local goal=c.surface.find_non_colliding_position("character",at,2,.5)
    if goal and distance(goal,target.position)<(weapon.range or 15)*.95
        and line_safe(npc,target,weapon,goal,false) then
      local cost=distance(c.position,goal)
      if cost>1 and (not best or cost<best_distance) then best,best_distance=goal,cost end
    end
  end
  s.fire_goal=best and {x=best.x,y=best.y} or nil
  s.fire_until=tick+30
  if best then s.blocked_fire_repositions=s.blocked_fire_repositions+1 end
  return s.fire_goal
end

function Squads.observe(npc)
  local s=npc.combat_squad
  if not s then return {members={npc.id},role="solo"} end
  return {members=s.members,leader=s.leader,rank=s.rank,role=s.role,
    target_id=s.target_id,focus_source=s.focus_source,spacing_moves=s.spacing_moves,
    unsafe_shots=s.unsafe_shots,spacing_goal=s.spacing_goal,
    blocked_fire_repositions=s.blocked_fire_repositions,fire_goal=s.fire_goal}
end

local function armed_character(character)
  if not character.valid or not character.health or character.health<=0
      or character.health/math.max(1,character.max_health)<.35 then return false,0 end
  if character.vehicle and character.vehicle.valid then return false,0 end
  local main=character.get_main_inventory()
  local guns=character.get_inventory(defines.inventory.character_guns)
  local ammo=character.get_inventory(defines.inventory.character_ammo)
  local categories={}
  for _,inventory in ipairs({main,ammo}) do if inventory then
    for slot=1,#inventory do
      local stack=inventory[slot]
      if stack.valid_for_read and stack.prototype.type=="ammo" then
        local category=stack.prototype.ammo_category
        local key=category and (type(category)=="string" and category or category.name)
        if key and (stack.ammo or 0)+(stack.count-1)*(stack.prototype.magazine_size or 1)>0 then
          categories[key]=true
        end
      end
    end
  end end
  local range=0
  for _,inventory in ipairs({main,guns}) do if inventory then
    for slot=1,#inventory do
      local stack=inventory[slot]
      if stack.valid_for_read and stack.prototype.type=="gun" then
        local attack=stack.prototype.attack_parameters
        for _,category in pairs(attack and attack.ammo_categories or {}) do
          local key=type(category)=="string" and category or category.name
          if categories[key] then range=math.max(range,attack.range or 0) end
        end
      end
    end
  end end
  return range>0,range
end

local function supplied_turret(turret)
  if turret.type=="ammo-turret" then
    local inventory=turret.get_inventory(defines.inventory.turret_ammo)
    return inventory and not inventory.is_empty()
  elseif turret.type=="electric-turret" then return (turret.energy or 0)>0
  elseif turret.type=="fluid-turret" then
    for index=1,turret.fluids_count do
      local fluid=turret.get_fluid(index)
      if fluid and fluid.amount>0 then return true end
    end
  end
  return false
end

local function armed_vehicle(vehicle)
  if vehicle.health/math.max(1,vehicle.max_health)<.35 or not vehicle.get_driver() then
    return false,0
  end
  local burner=vehicle.burner
  if burner and (burner.remaining_burning_fuel or 0)<=0 and burner.inventory.is_empty() then
    return false,0
  end
  local inventory=vehicle.get_inventory(defines.inventory.car_ammo)
  local guns=vehicle.prototype.indexed_guns or {}
  local range=0
  if inventory then for index=1,math.min(#inventory,#guns) do
    local stack=inventory[index]
    local category=stack.valid_for_read and stack.prototype.type=="ammo"
        and stack.prototype.ammo_category
    if category and (stack.ammo or 0)+(stack.count-1)*(stack.prototype.magazine_size or 1)>0 then
      for _,key in pairs(guns[index].attack_parameters.ammo_categories or {}) do
        local name=type(key)=="string" and key or key.name
        if name==category.name then range=math.max(range,guns[index].attack_parameters.range or 0) end
      end
    end
  end end
  return range>0,range
end

-- The policy can choose an actual armed friend or supplied defense. These
-- goals do not enlist a neutral force, move supplies, or teleport a body.
function Squads.backup_options(npc,threat,tick)
  local c=body(npc)
  if not c or not threat or not threat.valid then return {} end
  local candidates={}
  for _,provider in ipairs(c.surface.find_entities_filtered{position=c.position,radius=96,
      type={"character","car","ammo-turret","electric-turret","fluid-turret"}}) do
    if provider~=c and provider.valid and provider.health and provider.health>0
        and friendly(c.force,provider.force) and hostile(provider.force,threat.force) then
      local ready,range
      if provider.type=="character" then ready,range=armed_character(provider)
      elseif provider.type=="car" then ready,range=armed_vehicle(provider)
      else
        ready=supplied_turret(provider)
        local ok,attack=pcall(function() return provider.prototype.attack_parameters end)
        range=ok and attack and attack.range or 18
      end
      if ready then
        local dx,dy=provider.position.x-threat.position.x,provider.position.y-threat.position.y
        local length=math.max(.1,math.sqrt(dx*dx+dy*dy))
        local offset=provider.type=="character" and 3 or provider.type=="car" and 6 or 4
        local proposed={x=provider.position.x+dx/length*offset,
          y=provider.position.y+dy/length*offset}
        local goal=c.surface.find_non_colliding_position("character",proposed,4,.5)
        if goal and distance(c.position,goal)>2 then
          local kind=(provider.type=="character" or provider.type=="car") and "armed_ally" or "supplied_turret"
          candidates[#candidates+1]={id=tostring(provider.unit_number),
            goal={x=goal.x,y=goal.y},kind=kind,provider_unit_number=provider.unit_number,
            distance=distance(c.position,goal),support_range=range,
            provider_position={x=provider.position.x,y=provider.position.y}}
        end
      end
    end
  end
  table.sort(candidates,function(a,b)
    if a.distance==b.distance then return a.provider_unit_number<b.provider_unit_number end
    return a.distance<b.distance
  end)
  while #candidates>4 do table.remove(candidates) end
  npc.combat_backup={tick=tick,options=candidates}
  return candidates
end

-- Recheck the exact provider at outcome time. A remembered goal alone is not
-- proof that the ally still has ammunition or that the turret is powered.
-- threat_force_name is captured while the pursuer is valid, before native death.
function Squads.backup_supported(npc,provider_id,threat_force_name)
  local c=body(npc)
  if not c or not provider_id then return false end
  local enemy=type(threat_force_name)=="string" and game.forces[threat_force_name]
  if not enemy then return false end
  for _,provider in ipairs(c.surface.find_entities_filtered{position=c.position,radius=96,
      type={"character","car","ammo-turret","electric-turret","fluid-turret"}}) do
    if provider.valid and tostring(provider.unit_number)==tostring(provider_id)
        and provider.health and provider.health>0 and friendly(c.force,provider.force)
        and hostile(provider.force,enemy) then
      if provider.type=="character" then return armed_character(provider)
      elseif provider.type=="car" then return armed_vehicle(provider)
      else return supplied_turret(provider) end
    end
  end
  return false
end

return Squads
