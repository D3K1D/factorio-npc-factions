-- A small, saved linear tactical scorer. V3 gives continuous credit for
-- retained native health and confirmed support, not travel alone. Native
-- death closes an episode at -3.
local Policy={schema='npc-combat-v3',version=3}
Policy.feature_names={'bias','range_progress','range_fit','safety_margin','fire_window',
  'exposure','retreat_progress','flank','hold','advance','kite','retreat',
  'strafe_left','strafe_right','low_health_attack','danger_retreat',
  'ammo_attack','aggression_attack','courage_retreat','defense_kite',
  'seek_backup','backup_progress','backup_quality','low_health_backup','danger_backup'}
local DEFAULT={0,1.5,1,0.3,1.2,-1.5,-0.4,0.1,0.5,0,0.1,-0.8,0,0,
  -1.2,1.2,0.4,0.4,0.5,0.1,.2,.5,.4,.9,.6}
local MAX_RECORDS=512
local function clone(value)
  if type(value)~='table' then return value end
  local out={}
  for key,child in pairs(value) do out[key]=clone(child) end
  return out
end
local function clamp(value,lo,hi)return math.max(lo,math.min(hi,value))end
local function distance(a,b)
  local x,y=a.x-b.x,a.y-b.y
  return math.sqrt(x*x+y*y)
end
local function finite(value)
  return type(value)=='number' and value==value and value~=math.huge
    and value~=-math.huge and math.abs(value)<=100
end
local function state()
  storage.combat_policy=storage.combat_policy or {weights=clone(DEFAULT),training=false,
    explore=false,trace=false,records={},next_seq=1,dropped=0,decisions=0,
    schema=Policy.schema,version=Policy.version}
  local s=storage.combat_policy
  if s.schema~=Policy.schema or s.version~=Policy.version then
    -- A different reward schema cannot inherit an unqualified checkpoint.
    -- Preserve its receipts, then cold-start v3 unless a host explicitly
    -- imports a matching v3 checkpoint while paused.
    storage.combat_policy_archives=storage.combat_policy_archives or {}
    local previous=s.schema or 'npc-combat-v1'
    storage.combat_policy_archives[previous]={weights=s.weights,records=s.records,
      next_seq=s.next_seq,dropped=s.dropped,decisions=s.decisions,
      training=s.training,explore=s.explore,trace=s.trace}
    s.weights=clone(DEFAULT);s.records={};s.next_seq=1;s.dropped=0
    s.training=false;s.explore=false;s.trace=false
    s.schema=Policy.schema;s.version=Policy.version
  end
  return s
end
function Policy.status()
  local s=state()
  return {schema=Policy.schema,version=Policy.version,feature_names=clone(Policy.feature_names),
    weights=clone(s.weights),training=s.training,explore=s.explore,trace=s.trace,
    records=#s.records,next_seq=s.next_seq,dropped=s.dropped,decisions=s.decisions}
end
-- Only a server-owned paused fixture or integration calls configure. No
-- gameplay command exposes this function to a player.
function Policy.configure(config)
  if not game.tick_paused then return false,'paused_host_configuration_required' end
  if type(config)~='table' or config.schema~=Policy.schema
      or config.version~=nil and config.version~=Policy.version then
    return false,'combat_schema_mismatch'
  end
  if config.feature_names then
    if type(config.feature_names)~='table' or #config.feature_names~=#Policy.feature_names then
      return false,'combat_feature_names_mismatch'
    end
    for i,name in ipairs(Policy.feature_names) do
      if config.feature_names[i]~=name then return false,'combat_feature_names_mismatch' end
    end
  end
  local s=state()
  local weights=config.weights or s.weights
  if type(weights)~='table' or #weights~=#Policy.feature_names then
    return false,'combat_weight_count'
  end
  for i=1,#Policy.feature_names do
    if not finite(weights[i]) then return false,'combat_weight_nonfinite' end
  end
  for _,key in ipairs({'training','explore','trace'}) do
    if config[key]~=nil and type(config[key])~='boolean' then
      return false,'combat_flag_invalid:'..key
    end
  end
  s.weights=clone(weights)
  if config.training~=nil then s.training=config.training end
  if config.explore~=nil then s.explore=config.explore and s.training end
  if config.trace~=nil then s.trace=config.trace end
  return true,Policy.status()
end
function Policy.records(after_seq,limit)
  local s=state();local after=math.max(0,math.floor(tonumber(after_seq) or 0))
  local count=clamp(math.floor(tonumber(limit) or 64),1,128)
  local rows={}
  for _,row in ipairs(s.records) do
    if row.seq>after then
      rows[#rows+1]=clone(row)
      if #rows>=count then break end
    end
  end
  return {schema=Policy.schema,records=rows,next_seq=s.next_seq,dropped=s.dropped}
end
local function features(obs,choice)
  local id=choice.action or choice.id
  local d=obs.distance
  local next_d=choice.distance_to_enemy
  local middle=(obs.band_low+obs.band_high)/2
  local width=math.max(4,(obs.band_high-obs.band_low)/2)
  local advancing=id=='advance'
  local holding=id=='hold'
  local kiting=id=='kite'
  local retreating=id=='retreat'
  local strafing=id=='strafe_left' or id=='strafe_right'
  local attacking=advancing or holding or strafing
  local backup=id=='seek_backup'
  local distancing=kiting or retreating or backup
  local backup_progress=0
  if backup and obs.position and choice.backup_goal then
    backup_progress=clamp((distance(obs.position,choice.backup_goal)
      -distance(choice.target,choice.backup_goal))/8,-1,1)
  end
  local quality=choice.backup_kind=='supplied_turret' and 1
    or choice.backup_kind=='armed_ally' and .8
    or choice.backup_kind=='defended_home' and .5 or 0
  return {1,
    clamp((math.abs(d-middle)-math.abs(next_d-middle))/4,-1,1),
    clamp(1-math.abs(next_d-middle)/width,0,1),
    clamp((next_d-obs.enemy_reach)/math.max(4,obs.enemy_reach),-1,1),
    obs.weapon_ready and next_d<=obs.weapon_range*.95 and 1 or 0,
    clamp((obs.enemy_reach+2-next_d)/4,0,1),
    clamp((next_d-d)/4,-1,1),
    strafing and next_d>=obs.band_low and next_d<=obs.band_high and 1 or 0,
    holding and 1 or 0,advancing and 1 or 0,kiting and 1 or 0,
    retreating and 1 or 0,id=='strafe_left' and 1 or 0,
    id=='strafe_right' and 1 or 0,
    attacking and 1-obs.health_fraction or 0,
    distancing and clamp(obs.threat_strength/12,0,1) or 0,
    attacking and clamp(obs.ammo_units/60,0,1) or 0,
    attacking and obs.aggression or 0,
    distancing and 1-obs.courage or 0,
    (kiting or strafing) and obs.defense or 0,
    backup and 1 or 0,backup_progress,backup and quality or 0,
    backup and 1-obs.health_fraction or 0,
    backup and clamp((obs.enemy_reach+4-d)/8,0,1) or 0}
end
local function dot(weights,values)
  local result=0
  for i=1,#Policy.feature_names do result=result+weights[i]*values[i] end
  return result
end
function Policy.choose(npc,obs,options,tick)
  local s=state();local candidates={}
  for _,option in ipairs(options) do
    if option.legal then
      local values=features(obs,option)
      candidates[#candidates+1]={index=#candidates+1,id=option.id,
        action=option.action or option.id,target=clone(option.target),
        backup_goal=clone(option.backup_goal),backup_kind=option.backup_kind,
        provider_id=option.provider_id,source=option.source,
        features=values,base_score=dot(s.weights,values)}
    end
  end
  if #candidates==0 then return nil,nil end
  local best=1
  for i=2,#candidates do
    if candidates[i].base_score>candidates[best].base_score+1e-9 then best=i end
  end
  s.decisions=s.decisions+1
  local chosen=best;local exploration=false
  if s.training and s.explore and s.decisions%5==0 and #candidates>1 then
    chosen=1+((npc.id*131+s.decisions*17)%#candidates)
    exploration=chosen~=best
  end
  local decision=candidates[chosen]
  local episode={actor_id=npc.id,decision_tick=tick,
    target_unit_number=obs.target_unit_number,candidates=candidates,
    chosen_candidate_index=chosen,exploration=exploration,trace=s.trace}
  return decision,episode
end
function Policy.finish(npc,episode,tick,reason,metrics)
  if not episode or episode.finished then return end
  episode.finished=true
  metrics=metrics or {}
  local elapsed=math.max(0,tick-episode.decision_tick)
  local damage=math.max(0,metrics.damage_dealt or 0)
  local kills=math.max(0,metrics.kills or 0)
  local lost=math.max(0,metrics.health_lost or 0)
  local ammo=math.max(0,metrics.ammo_spent or 0)
  local in_range=clamp(metrics.in_range_ticks or 0,0,elapsed)
  local alive=metrics.alive~=false and reason~='body_lost'
  local start_health_fraction=clamp(metrics.start_health_fraction or 0,0,1)
  local end_health_fraction=alive and clamp(metrics.end_health_fraction or 0,0,1) or 0
  local backup_progress=metrics.backup_progress or 0
  local moved_distance=math.max(0,metrics.moved_distance or 0)
  local pursuer_clears=math.max(0,metrics.pursuer_clears or 0)
  local target_cleared=metrics.target_cleared==true or pursuer_clears>0
  local native_pursuer_cleared=pursuer_clears>0
  local backup_reached_supported=metrics.backup_reached_supported==true
  local health_loss_fraction=clamp(start_health_fraction-end_health_fraction,0,1)
  local idle=moved_distance<.5 and ammo==0 and damage==0 and not native_pursuer_cleared
  local reward
  if not alive then reward=-3
  else
    reward=clamp(.2*clamp(damage/30,0,1)+.35*clamp(kills,0,2)
      +(native_pursuer_cleared and .65 or 0)-3*health_loss_fraction
      -.25*(1-end_health_fraction)*clamp(elapsed/90,0,1)
      -.025*clamp(ammo,0,40)+.15*clamp(backup_progress/12,-1,1)
      +(backup_reached_supported and .35 or 0)
      +.08*in_range/math.max(1,elapsed)-.18*clamp(elapsed/180,0,1)
      -(idle and .4 or 0),-3,3)
  end
  if not episode.trace then return end
  local s=state()
  s.records[#s.records+1]={schema=Policy.schema,seq=s.next_seq,actor_id=episode.actor_id,
    decision_tick=episode.decision_tick,target_unit_number=episode.target_unit_number,
    candidates=episode.candidates,chosen_candidate_index=episode.chosen_candidate_index,
    exploration=episode.exploration,
    outcome={end_tick=tick,damage_dealt=damage,kills=kills,health_lost=lost,
      ammo_spent=ammo,in_range_ticks=in_range,elapsed_ticks=elapsed,
      alive=alive,start_health_fraction=start_health_fraction,
      end_health_fraction=end_health_fraction,
      health_loss_fraction=health_loss_fraction,
      backup_progress=backup_progress,moved_distance=moved_distance,
      backup_reached_supported=backup_reached_supported,
      pursuer_clears=pursuer_clears,target_cleared=target_cleared,idle=idle,
      terminal_reason=reason,reward=reward}}
  s.next_seq=s.next_seq+1
  while #s.records>MAX_RECORDS do table.remove(s.records,1);s.dropped=s.dropped+1 end
end
return Policy
