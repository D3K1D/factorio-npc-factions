-- Small local probabilistic contextual bandit. The cold start uses transparent
-- personality priors. Learned corrections come only from attributed outcomes.
-- Multiplayer replicas share saved weights, RNG state and exactly-once feedback.
local Personality=require("scripts.personality")
local Relations=require("scripts.relations")
local Strategy={version="context-bandit-v3"}
local AXES={"morality","aggression","courage","loyalty","honesty","cooperation",
  "trade","ambition","cognition","work_rate","defense","science","logistics",
  "power","frugality","territoriality","adaptability","sociability","humor",
  "curiosity","grudge","stealth","resourcefulness","patience"}
local WORLD_FEATURES={"project_fraction","project_wait_age","research_progress",
  "research_red","research_green","research_blue","lab_red","lab_green","lab_blue",
  "iron_stock","copper_stock","steel_stock","ammo_stock","iron_ore_stock",
  "copper_ore_stock","plastic_stock","sulfur_stock","local_threat","recent_failures",
  "mining_stall","fuel_pressure","power_pressure","asset_damage","inbound_request",
  "outbound_request","coalition_member","faction_age","launch_pending"}
-- v3 appends how the engineer feels about the counterpart (from memory).
local BOND_FEATURES={"affinity","grudge","fear","debt","anger"}
local V2_DIM=34+#WORLD_FEATURES
local DIM=V2_DIM+#BOND_FEATURES
local ALLOWED={accept=true,reject=true,offer_trade=true,offer_alliance=true,offer_coalition_shared=true,
  offer_coalition_separate=true,offer_peace=true,declare_war=true,theft=true,sabotage=true,
  supply=true,claim_resource=true,belief_claim=true,idle=true,project_green_science=true,
  project_electric_mining=true,project_basic_defense=true,project_steel_processing=true,
  project_oil_processing=true,project_chemical_science=true,
  project_rocket_materials=true,project_platform_launch=true,project_ground_rail_trade=true}
local function clamp(x,a,b) return math.max(a,math.min(b,x)) end
local function adaptation_value()
  local setting=settings and settings.global and settings.global["npc-factions-adaptation"]
  local value=setting and setting.value or .5
  if type(value)~="number" or value~=value or value==math.huge or value==-math.huge then value=.5 end
  return clamp(value,0,1)
end
function Strategy.learning_rate(npc)
  -- Default .5 preserves the historical update exactly. The runtime-global
  -- setting is authoritative and synchronized; the feature/model shape stays.
  return .03*(.5+Personality.axes(npc).adaptability)*(.5+adaptation_value())
end

local function ensure(npc)
  if npc.strategy and npc.strategy.version==Strategy.version then return npc.strategy end
  local old=npc.strategy and npc.strategy.version
  if old=="context-bandit-v1" or old=="context-bandit-v2" then
    -- Keep learned corrections and unresolved outcome identities when adding
    -- observations. Historical decisions keep their historical feature values.
    local first=old=="context-bandit-v1" and 35 or V2_DIM+1
    for _,w in pairs(npc.strategy.weights or {}) do
      for i=first,DIM do w[i]=0 end
    end
    npc.strategy.version=Strategy.version
    npc.strategy.migrated_from=old
    return npc.strategy
  end
  local p=Personality.ensure(npc)
  npc.strategy={version=Strategy.version,rng=p.seed,weights={},decisions=0,updates=0,
    pending={},history={},reward_sum=0,action_updates={},outcome_counts={},action_outcome_counts={},
    outcome_counts_since_tick=game.tick,source="transparent_priors_with_online_outcome_learning"}
  return npc.strategy
end
local function world_features(npc)
  local c=npc.character
  if not c or not c.valid then local out={} for i=1,#WORLD_FEATURES do out[i]=0 end return out end
  local inv=c.get_main_inventory()
  local function stock(item,scale) return clamp(inv.get_item_count{name=item,quality="normal"}/scale,0,1) end
  local p=npc.factory_project
  local lab,assets,low_fuel,disconnected,damaged,burners,electric= nil,0,0,0,0,0,0
  local sites=p and p.plan and p.plan.sites or {}
  local ordered={} for key in pairs(sites) do ordered[#ordered+1]=key end table.sort(ordered)
  -- Read only owned planned assets; no enemy factory or hidden global inventory.
  for _,key in ipairs(ordered) do
    local spec=sites[key]
    local matches=c.surface.find_entities_filtered{position=spec.position,radius=.15,
      name=spec.name,force=npc.force,limit=1}
    local e=matches[1]
    if e then
      assets=assets+1
      if key=="lab" then lab=e end
      if e.health and e.max_health and e.health<e.max_health*.75 then damaged=damaged+1 end
      if e.burner and e.burner.inventory then
        burners=burners+1
        if e.burner.inventory.is_empty() and e.burner.remaining_burning_fuel<=0 then low_fuel=low_fuel+1 end
      end
      if e.electric_buffer_size and e.electric_buffer_size>0 then
        electric=electric+1
        if not e.is_connected_to_electric_network() then disconnected=disconnected+1 end
      end
    end
  end
  local research=npc.force.current_research
  local science={}
  for _,ingredient in pairs(research and research.research_unit_ingredients or {}) do science[ingredient.name]=true end
  local labinv=lab and lab.get_inventory(defines.inventory.lab_input)
  local function lab_stock(item) return labinv and clamp(labinv.get_item_count(item)/20,0,1) or 0 end
  local ammo=inv.get_item_count("firearm-magazine")
  local equipped=c.get_inventory(defines.inventory.character_ammo)
  if equipped then ammo=ammo+equipped.get_item_count("firearm-magazine") end
  local threats=c.surface.count_entities_filtered{position=c.position,radius=64,force="enemy",
    type={"unit","unit-spawner","turret"},limit=16}
  local failures=0
  for _,f in ipairs(npc.failures or {}) do if game.tick-(f.tick or 0)<3600 then failures=failures+1 end end
  local inbound,outbound=0,0
  for _,r in ipairs(storage.society and storage.society.requests or {}) do
    if game.tick<(r.expires_tick or 0) then
      if r.requester_id==npc.id and r.status=="open" then inbound=inbound+1 end
      if r.supplier_id==npc.id and r.status=="accepted" then outbound=outbound+1 end
    end
  end
  local job=npc.current_job
  local launch=storage.platform_launch and storage.platform_launch.requests
  local actor=storage.society and storage.society.actors and storage.society.actors[npc.id]
  local pending_launch=false
  for _,request in pairs(launch or {}) do
    if request.actor_id==npc.id and request.status~="complete" then pending_launch=true end
  end
  local out={
    p and clamp(((p.index or 1)-1)/math.max(1,#p.plan.steps),0,1) or 0,
    p and clamp((game.tick-(p.last_progress_tick or p.started_tick or game.tick))/7200,0,1) or 0,
    research and npc.force.research_progress or 0,
    science["automation-science-pack"] and 1 or 0,science["logistic-science-pack"] and 1 or 0,
    science["chemical-science-pack"] and 1 or 0,
    lab_stock("automation-science-pack"),lab_stock("logistic-science-pack"),lab_stock("chemical-science-pack"),
    stock("iron-plate",200),stock("copper-plate",200),stock("steel-plate",100),clamp(ammo/40,0,1),
    stock("iron-ore",100),stock("copper-ore",100),stock("plastic-bar",100),stock("sulfur",50),
    clamp(threats/16,0,1),clamp(failures/8,0,1),
    job and job.kind=="mine" and clamp((game.tick-(job.last_gain_tick or job.started_tick or game.tick))/1800,0,1) or 0,
    clamp(low_fuel/math.max(1,burners),0,1),clamp(disconnected/math.max(1,electric),0,1),
    clamp(damaged/math.max(1,assets),0,1),clamp(inbound/8,0,1),clamp(outbound/8,0,1),
    actor and actor.coalition_id and 1 or 0,
    clamp((game.tick-(npc.created_tick or game.tick))/72000,0,1),pending_launch and 1 or 0}
  return out
end
local function contact(view,id)
  for _,c in ipairs(view and view.contacts or {}) do if c.id==id then return c end end
  return {}
end
-- How this engineer feels about the candidate's counterpart (player or NPC).
local function bond(npc,c,peer)
  local mood=Relations.mood(npc.id,game.tick)
  local key=peer.key or (type(c.target_id)=="number" and c.target_id>0 and "npc:"..c.target_id) or nil
  if not key then return {affinity=0,grudge=0,fear=0,debt=0,anger=mood.anger} end
  local r=Relations.get(npc,key,game.tick)
  return {affinity=r.affinity,grudge=r.grudge,fear=r.fear,debt=r.debt,anger=mood.anger}
end
local function features(npc,c,view,world)
  local x={1};local a=Personality.axes(npc)
  for _,name in ipairs(AXES) do x[#x+1]=a[name]-.5 end
  local peer=contact(view,c.target_id)
  x[#x+1]=clamp((peer.trust or 0)/100,-1,1)
  x[#x+1]=peer.force_relation=="war" and 1 or 0
  x[#x+1]=peer.force_relation=="allied" and 1 or 0
  x[#x+1]=peer.trade and 1 or 0
  x[#x+1]=clamp((peer.last_seen_distance or peer.distance or 0)/1024,0,1)
  local character=npc.character
  x[#x+1]=character and character.valid and clamp(character.health/character.max_health,0,1) or 0
  local inventory=character and character.valid and character.get_main_inventory()
  x[#x+1]=inventory and clamp(inventory.get_item_count("coal")/24,0,1) or 0
  x[#x+1]=npc.factory_project and npc.factory_project.status=="active" and 1 or 0
  x[#x+1]=c.item and 1 or 0
  for _,value in ipairs(world) do x[#x+1]=value end
  local b=bond(npc,c,peer)
  x[#x+1]=clamp(b.affinity/100,-1,1);x[#x+1]=clamp(b.grudge/100,0,1);x[#x+1]=clamp(b.fear/100,0,1)
  x[#x+1]=clamp(b.debt/100,-1,1);x[#x+1]=clamp(b.anger/100,0,1)
  return x,a,peer,b
end
-- Relationships on top of personality: grudges push toward war, theft and
-- refusal; affinity toward offers, acceptance and help; owing someone
-- (positive debt) toward supplying them; fear toward peace.
function Strategy.social_prior(action,b)
  local affinity,grudge,fear=b.affinity/100,b.grudge/100,b.fear/100
  local debt,anger=clamp(b.debt/100,-1,1),b.anger/100
  if action=="accept" then return 1.5*affinity-2*grudge end
  if action=="reject" then return -1.5*affinity+2*grudge end
  if action=="offer_trade" or action=="offer_alliance" or action=="offer_coalition_shared"
      or action=="offer_coalition_separate" then return 1.5*affinity-2.5*grudge end
  if action=="offer_peace" then return 2*fear-grudge end
  if action=="declare_war" then return 4*grudge+2*anger-2*affinity-fear end
  if action=="theft" or action=="sabotage" then return 3*grudge+anger-3*affinity end
  if action=="supply" then return 2*affinity+2*math.max(0,debt)-2*grudge end
  return 0
end
local function prior(c,a,peer)
  local action=c.action
  local trust=clamp((peer.trust or 0)/100,-1,1)
  if action=="accept" then return -1+3*a.cooperation+1.5*a.loyalty+trust end
  if action=="reject" then return 1.2-3*a.cooperation-1.2*a.loyalty-trust end
  if action=="offer_trade" then return -2+3*a.trade+1.2*a.cooperation+trust end
  if action=="offer_alliance" then return -3+4*a.cooperation+1.5*a.loyalty+trust end
  if action=="offer_coalition_shared" then return -4+4*a.cooperation+2*a.loyalty+trust end
  if action=="offer_coalition_separate" then return -3+3*a.cooperation+1.5*a.logistics+trust end
  if action=="offer_peace" then return -1+2*a.cooperation+2*(1-a.courage)-a.aggression end
  if action=="declare_war" then return -6+4*a.aggression+2*a.grudge+2*a.territoriality
      -2*a.morality-2*a.cooperation-2*trust end
  if action=="theft" then return -6+3*(1-a.morality)+2*a.stealth+2*a.aggression-2*a.loyalty end
  if action=="sabotage" then return -7+3*(1-a.morality)+3*a.aggression+2*a.stealth-2*a.loyalty end
  if action=="supply" then return -2+2*a.trade+2*a.cooperation+trust end
  if action=="claim_resource" then return -2+3*a.territoriality+a.ambition end
  if action=="belief_claim" then return -4+3*(1-a.honesty)+a.sociability end
  if action=="project_green_science" then return -1+3*a.science+a.ambition end
  if action=="project_electric_mining" then return -1+2*a.work_rate+2*a.resourcefulness end
  if action=="project_basic_defense" then return -1+3*a.defense+a.courage end
  if action=="project_steel_processing" then return -1+2*a.ambition+a.science+a.power end
  if action=="project_oil_processing" then return -1+2*a.power+2*a.ambition+a.resourcefulness end
  if action=="project_chemical_science" then return -1+3*a.science+2*a.ambition+a.cognition end
  if action=="project_rocket_materials" then return -.5+2*a.science+3*a.ambition+a.resourcefulness end
  if action=="project_platform_launch" then return 1+4*a.ambition+2*a.cognition end
  if action=="project_ground_rail_trade" then return -2+3*a.logistics+2*a.trade+a.cooperation+trust end
  return -.5+a.patience
end
local function dot(w,x)
  local value=0;for i=1,DIM do value=value+(w[i] or 0)*(x[i] or 0) end;return value
end
local function environment_bias(c,world)
  -- Transparent cold-start preferences make immediate observations useful
  -- before enough attributed outcomes exist to train the learned correction.
  local threat,damage,fuel,power=world[18],world[23],world[21],world[22]
  if c.action=="project_basic_defense" then return 4*threat+3*damage end
  if c.action=="project_electric_mining" then return 2*world[20]+1-world[10] end
  if c.action=="project_green_science" then return world[5]*(1-world[8]) end
  if c.action=="project_chemical_science" then return 2*world[6]*(1-world[9]) end
  if c.action=="offer_peace" then return 2*threat+damage+fuel end
  if c.action=="declare_war" then return -3*threat-2*damage-fuel-power end
  if c.action=="project_ground_rail_trade" then return -3*threat-2*damage-fuel-power end
  if c.action=="idle" then return fuel+power end
  return 0
end
function Strategy.choose(npc,candidates,view)
  if type(candidates)~="table" or #candidates==0 then return nil end
  local s=ensure(npc);local scores,xs={},{};local maximum=-math.huge
  local world=world_features(npc)
  for i,c in ipairs(candidates) do
    local x,a,peer,b=features(npc,c,view,world);xs[i]=x
    local score=clamp(prior(c,a,peer)+Strategy.social_prior(c.action,b)+environment_bias(c,world)
      +dot(s.weights[c.action] or {},x),-12,12)
    scores[i]=score;maximum=math.max(maximum,score)
  end
  local total=0
  local temperature=.55+.45*(1-Personality.axes(npc).cognition)
  for i,score in ipairs(scores) do scores[i]=math.exp((score-maximum)/temperature);total=total+scores[i] end
  s.rng=(s.rng*48271)%2147483647
  local draw=s.rng/2147483647*total;local selected=#candidates
  for i,value in ipairs(scores) do draw=draw-value;if draw<=0 then selected=i;break end end
  s.decisions=s.decisions+1
  local token=tostring(npc.id)..":"..s.decisions
  local c=candidates[selected]
  local record={token=token,action=c.action,candidate_id=c.id,features=xs[selected],
    probability=scores[selected]/total,tick=game.tick,applied=false}
  s.pending[token]=record;s.history[#s.history+1]=record
  while #s.history>256 do
    local old=table.remove(s.history,1)
    if old.applied or old.closed then s.pending[old.token]=nil end
  end
  return c,token
end
function Strategy.feedback(npc,token,reward,reason)
  local s=ensure(npc);local record=s.pending[token]
  if not record or record.applied then return false,"unknown_or_duplicate_outcome" end
  if type(reward)~="number" or reward~=reward then return false,"invalid_reward" end
  reward=clamp(reward,-1,1)
  if s.learning_enabled~=false then
    local w=s.weights[record.action] or {};s.weights[record.action]=w
    local prediction=math.tanh(dot(w,record.features))
    local rate=Strategy.learning_rate(npc)
    record.learning_rate=rate
    record.adaptation_setting=adaptation_value()
    for i=1,DIM do w[i]=clamp((w[i] or 0)+rate*(reward-prediction)*(record.features[i] or 0),-2,2) end
    s.updates=s.updates+1
    s.action_updates=s.action_updates or {}
    s.action_updates[record.action]=(s.action_updates[record.action] or 0)+1
  end
  s.outcome_counts=s.outcome_counts or {}
  s.outcome_counts_since_tick=s.outcome_counts_since_tick or game.tick
  local counter
  if reason=="supply:complete" then counter="completed_supply"
  elseif type(reason)=="string" and reason:sub(1,7)=="supply:" then counter="failed_supply"
  elseif reason=="offer_rejected" then counter="offer_rejected"
  elseif type(reason)=="string" and reason:sub(1,26)=="physical_project_complete:" then counter="completed_project"
  elseif record.action:sub(1,8)=="project_" and reward<0 then counter="failed_project" end
  if counter then
    s.outcome_counts[counter]=(s.outcome_counts[counter] or 0)+1
    s.action_outcome_counts=s.action_outcome_counts or {}
    local counts=s.action_outcome_counts[record.action] or {}
    s.action_outcome_counts[record.action]=counts
    counts[counter]=(counts[counter] or 0)+1
  end
  record.applied=true;record.reward=reward;record.outcome=reason;record.outcome_tick=game.tick
  s.pending[token]=nil
  s.outcomes=(s.outcomes or 0)+1;s.reward_sum=s.reward_sum+reward
  return true
end
function Strategy.import_model(npc,model,learning)
  -- Older models import with the newer features starting at zero.
  local legacy=type(model)=="table" and (model.version=="context-bandit-v1" and model.feature_count==34
    or model.version=="context-bandit-v2" and model.feature_count==V2_DIM) and model.feature_count or nil
  if type(model)~="table" or not legacy and (model.version~=Strategy.version or model.feature_count~=DIM)
      or type(model.weights)~="table" then return false,"incompatible_strategy_model" end
  if learning~=nil and type(learning)~="boolean" then return false,"invalid_learning_mode" end
  local weights={}
  for action,values in pairs(model.weights) do
    local expected=legacy or DIM
    if not ALLOWED[action] or type(values)~="table" or #values~=expected then return false,"invalid_action_or_dimensions" end
    weights[action]={}
    for key,value in pairs(values) do
      if type(key)~="number" or key%1~=0 or key<1 or key>expected or type(value)~="number"
          or value~=value or value==math.huge or value==-math.huge or math.abs(value)>2 then
        return false,"nonfinite_or_out_of_range_weight"
      end
      weights[action][key]=value
    end
    if legacy then for i=legacy+1,DIM do weights[action][i]=0 end end
  end
  local s=ensure(npc);s.weights=weights;s.learning_enabled=learning~=false
  s.action_updates={};s.outcome_counts={};s.action_outcome_counts={};s.outcome_counts_since_tick=game.tick
  s.imported_tick=game.tick;s.source="transparent_priors_with_imported_outcome_weights"
  return true
end
function Strategy.close(npc,token,reason)
  local s=ensure(npc);local record=s.pending[token]
  if not record then return false end
  record.closed=true;record.outcome=reason;record.outcome_tick=game.tick;s.pending[token]=nil
  return true
end
function Strategy.actor_lost(npc)
  local s=ensure(npc)
  local tokens={}
  for token in pairs(s.pending) do tokens[#tokens+1]=token end
  table.sort(tokens)
  for _,token in ipairs(tokens) do
    local action=s.pending[token].action
    if action=="supply" or action=="theft" or action=="sabotage" or action:sub(1,8)=="project_" then
      Strategy.feedback(npc,token,-1,"actor_lost_during_physical_work")
    else Strategy.close(npc,token,"actor_lost_before_social_outcome") end
  end
end
function Strategy.observe(npc,detailed)
  local s=ensure(npc)
  return {version=s.version,source=s.source,decisions=s.decisions,updates=s.updates,
    reward_sum=s.reward_sum,rng=s.rng,feature_count=DIM,outcomes=s.outcomes or 0,
    learning_enabled=s.learning_enabled~=false,
    action_updates=s.action_updates,outcome_counts=s.outcome_counts,action_outcome_counts=s.action_outcome_counts,
    outcome_counts_since_tick=s.outcome_counts_since_tick,
    world_feature_names=detailed and WORLD_FEATURES or nil,
    weights=detailed and s.weights or nil,history=detailed and s.history or nil}
end
return Strategy
