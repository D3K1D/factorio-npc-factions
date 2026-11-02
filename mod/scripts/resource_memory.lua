-- Initial camp survey is declared engine map knowledge, not physical scouting.
-- Memories nominate a bounded patch; every mining target is resolved natively.
local Memory={}
local MAX={['iron-ore']=128,coal=256,['copper-ore']=512,stone=512,wood=128,water=128}
local function finite(n) return type(n)=='number' and n==n and math.abs(n)<=1000000 end
local function position(p)
  if type(p)~='table' then return nil end
  local x,y=p.x or p[1],p.y or p[2]
  if not finite(x) or not finite(y) then return nil end
  return {x=x,y=y}
end
local function distance(a,b) local x,y=a.x-b.x,a.y-b.y;return math.sqrt(x*x+y*y) end
local function key(e) return e.unit_number or (e.name..':'..e.position.x..':'..e.position.y) end
local function yields(e,item)
  if not (e and e.valid and e.force.name=='neutral' and e.minable) then return false end
  if e.type~='resource' and e.type~='tree' and e.type~='simple-entity' then return false end
  local p=e.prototype.mineable_properties
  if not p or not p.minable or p.required_fluid then return false end
  for _,product in ipairs(p.products or {}) do
    if product.type=='item' and product.name==item then return true end
  end
  return false
end

function Memory.attach(npc,survey)
  local c=npc and npc.character
  if not (c and c.valid and type(survey)=='table' and survey.scope=='engine_initial_camp_survey'
      and survey.surface_index==c.surface.index and type(survey.observed_tick)=='number'
      and survey.observed_tick>=0 and survey.observed_tick<=game.tick) then return false,'invalid_camp_survey' end
  local home=position(survey.home)
  local npc_home=position(npc.home)
  if not home or not npc_home or distance(home,npc_home)>.5 then return false,'camp_survey_home_mismatch' end
  local saved={scope=survey.scope,surface_index=survey.surface_index,observed_tick=survey.observed_tick,
    home=home,landmarks={},max_ranges={}}
  for item,cap in pairs(MAX) do
    local entry=type(survey.landmarks)=='table' and survey.landmarks[item]
    local range=type(survey.max_ranges)=='table' and survey.max_ranges[item]
    if type(range)=='number' and range>0 and range<=cap then
      saved.max_ranges[item]=range
      if type(entry)=='table' and entry.available==true then
        local p=position(entry.position)
        local valid_name=type(entry.name)=='string' and (item=='wood' or item=='water' or entry.name==item)
          and (item=='water' and prototypes.tile[entry.name] or item~='water' and prototypes.entity[entry.name])
        local expected_type=item=='water' and 'tile' or item=='wood' and 'tree' or 'resource'
        local expiry=entry.expires_tick
        if p and valid_name and entry.type==expected_type and distance(home,p)<=range
            and (expiry==nil or type(expiry)=='number' and expiry==math.floor(expiry) and expiry>=0 and expiry<9007199254740991) then
          saved.landmarks[item]={available=true,name=entry.name,type=entry.type,position=p,
            observed_tick=survey.observed_tick,expires_tick=entry.expires_tick}
        else saved.landmarks[item]={available=false,reason='invalid_observed_landmark'} end
      else saved.landmarks[item]={available=false,reason=type(entry)=='table' and entry.reason or 'unsurveyed'} end
    end
  end
  npc.camp_survey=saved
  return true
end

function Memory.recall(npc,item,excluded)
  local c=npc and npc.character
  local survey=npc and npc.camp_survey
  local remembered_home=survey and position(survey.home)
  local npc_home=npc and position(npc.home)
  if not (c and c.valid and survey and survey.scope=='engine_initial_camp_survey'
      and survey.surface_index==c.surface.index and remembered_home and npc_home
      and distance(remembered_home,npc_home)<=.5) then return nil,'no_matching_camp_survey' end
  if distance(c.position,npc_home)>536 then return nil,'outside_camp_departure_region' end
  local cap=MAX[item]
  local entry=survey.landmarks and survey.landmarks[item]
  local range=survey.max_ranges and survey.max_ranges[item]
  local p=entry and position(entry.position)
  if not cap or item=='water' or type(range)~='number' or range<=0 or range>cap
      or not entry or entry.available~=true or not p or distance(npc.home,p)>range then
    return nil,'unavailable_observed_resource'
  end
  if type(entry.name)~='string' or not prototypes.entity[entry.name]
      or (item~='wood' and entry.name~=item) or (entry.expires_tick and
      (type(entry.expires_tick)~='number' or entry.expires_tick<game.tick)) then
    return nil,'stale_observed_resource'
  end
  if entry.retry_at then
    if type(entry.retry_at)~='number' or entry.retry_at~=entry.retry_at then return nil,'invalid_observed_resource_cooldown' end
    if game.tick<entry.retry_at then return nil,'observed_resource_route_cooldown' end
  end
  local best,bd
  for _,e in ipairs(c.surface.find_entities_filtered{position=p,radius=24,
      name=entry.name,type={'resource','tree','simple-entity'},force='neutral'}) do
    if yields(e,item) and distance(npc.home,e.position)<=range and not (excluded and excluded[key(e)]) then
      local d=distance(c.position,e.position)
      if not best or d<bd or d==bd and (e.position.x<best.position.x or
          e.position.x==best.position.x and e.position.y<best.position.y) then best,bd=e,d end
    end
  end
  if not best then
    entry.last_unavailable='observed_patch_depleted_or_excluded';entry.retry_at=game.tick+600
    return nil,entry.last_unavailable
  end
  -- An actual local revisit refreshes the observation. A distant lookup does not.
  if bd<=128 then
    entry.position={x=best.position.x,y=best.position.y};entry.observed_tick=game.tick
  end
  entry.last_unavailable=nil
  return best
end

function Memory.route_failed(npc,item,tick,reason)
  local entry=npc.camp_survey and npc.camp_survey.landmarks and npc.camp_survey.landmarks[item]
  if entry then entry.retry_at=tick+600;entry.last_unavailable=reason end
end
return Memory
