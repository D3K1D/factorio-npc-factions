-- A finite mask of executable procedures. The model can prioritize supported
-- stages; an absent oil/rail/rocket economy procedure is never a legal choice.
local Strategy=require("scripts.strategy_policy")
local Oil=require("scripts.oil_projects")
local Rocket=require("scripts.rocket_projects")
local RailTrade=require("scripts.rail_trade_projects")
local Advanced=require("scripts.advanced_projects")
local Defense=require("scripts.defense_projects")
local Choices={refusal_revision="early-project-preflight-v1"}
local early={"green_science","electric_mining","basic_defense","steel_processing"}
local supported={} for _,id in ipairs(early) do supported[id]=true end
-- These planners only survey and copy work descriptions. Projects.start still
-- performs its fresh authoritative check before paid Worker work is assigned.
function Choices.preview(npc,id)
  if id=="basic_defense" then return Defense.plan(npc,id) end
  for _,supported in ipairs(early) do
    if supported==id then return Advanced.plan(npc,id) end
  end
  return nil,"unsupported_early_project"
end
local function scope(npc)
  local character=npc.character
  local plan=npc.factory_project and npc.factory_project.plan
  local parts={Choices.refusal_revision,Advanced.revision,Defense.revision,
    npc.force.name,tostring(character.surface.index),plan and plan.id or "",
    plan and plan.revision or ""}
  for _,id in ipairs({"starter_power_science","green_science","electric_mining",
      "basic_defense","steel_processing","oil_processing","chemical_science"}) do
    parts[#parts+1]=(npc.completed_projects and npc.completed_projects[id]) and "1" or "0"
  end
  local technology=npc.force.technologies["electric-mining-drill"]
  parts[#parts+1]=technology and technology.researched and "1" or "0"
  local sites=plan and plan.sites or {}
  local keys={} for key in pairs(sites) do keys[#keys+1]=key end
  table.sort(keys)
  for _,key in ipairs(keys) do
    local s=sites[key]
    local unit=0
    for _,entity in ipairs(character.surface.find_entities_filtered{
        position=s.position,radius=.1,name=s.name,force=npc.force}) do
      if entity.valid and entity.unit_number and (unit==0 or entity.unit_number<unit) then
        unit=entity.unit_number
      end
    end
    parts[#parts+1]=table.concat({key,s.name,tostring(s.position.x),
      tostring(s.position.y),tostring(s.direction or 0),tostring(unit)},":")
  end
  return table.concat(parts,"|")
end
local function refusal_type(reason)
  if reason=="starter_project_sites_required" or reason=="owned_science_lab_required" then
    return "missing_owned_context",6000
  end
  if reason:find("site_blocked",1,true) or reason:find("no_legal",1,true) then
    -- Placement refusal can include the actor's temporary body obstruction.
    -- Do not escalate it into a structural blacklist or long layout cooldown.
    return "placement_unavailable",600
  end
  if reason:find("budget",1,true) or reason:find("recipe",1,true)
      or reason:find("ingredient",1,true) then return "material_unavailable",600 end
  return "planner_unavailable",600
end
local function diagnostic(cache,record,tick,event)
  cache.history[#cache.history+1]={project_id=record.project_id,action=record.action,
    kind=record.kind,reason=record.reason,tick=tick,event=event,retry_at=record.retry_at}
  while #cache.history>8 do table.remove(cache.history,1) end
end
function Choices.preflight(npc,id,tick,context)
  if not supported[id] then return false,"unsupported_early_project" end
  context=context or scope(npc)
  local cache=npc.project_preflight
  if not cache or cache.revision~=Choices.refusal_revision then
    cache={revision=Choices.refusal_revision,refusals={},history={}}
    npc.project_preflight=cache
  end
  local old=cache.refusals[id]
  if old and not old.available and old.scope==context and tick<old.retry_at then
    return false,old.reason,old
  end
  local plan,reason=Choices.preview(npc,id)
  if plan then
    if old and not old.available then
      old.available=true;old.last_checked_tick=tick;old.retry_at=tick
      diagnostic(cache,old,tick,"available")
    end
    return true
  end
  reason=tostring(reason or "planner_unavailable"):sub(1,256)
  local kind,delay=refusal_type(reason)
  local repeated=old and not old.available and old.scope==context and old.reason==reason
  local count=repeated and math.min(255,old.check_count+1) or 1
  if kind=="missing_owned_context" then delay=math.min(36000,delay*2^math.min(3,count-1)) end
  local record={project_id=id,action="project_"..id,kind=kind,reason=reason,
    first_checked_tick=repeated and old.first_checked_tick or tick,last_checked_tick=tick,
    check_count=count,retry_at=tick+delay,scope=context,available=false}
  cache.refusals[id]=record
  diagnostic(cache,record,tick,"refused")
  -- No policy token exists for a masked action, hence no learned feedback.
  return false,reason,record
end
function Choices.candidates(npc,tick)
  local done=npc.completed_projects or {}
  local list,blocker={},nil
  local context
  for _,id in ipairs(early) do
    local gate=done.starter_power_science~=nil
    if id=="electric_mining" then
      local technology=npc.force.technologies["electric-mining-drill"]
      gate=gate and technology and technology.researched
    end
    local retry=npc.project_retry and npc.project_retry[id] or 0
    if gate and not done[id] and tick>=retry then
      context=context or scope(npc)
      local available,reason=Choices.preflight(npc,id,tick,context)
      if available then list[#list+1]={id=id,project_id=id,action="project_"..id}
      else blocker=reason end
    end
  end
  for _,id in ipairs({"oil_processing","chemical_science"}) do
    local gate=done.green_science and done.steel_processing
    if id=="chemical_science" then gate=gate and done.oil_processing end
    if gate and not done[id] and tick>=(npc.project_retry and npc.project_retry[id] or 0) then
      -- A physical layout survey and complete material budget must exist before
      -- the policy sees this action. No absent oil or impossible port geometry.
      local plan,reason=Oil.plan(npc,id)
      if plan then list[#list+1]={id=id,project_id=id,action="project_"..id}
      else blocker=reason end
    end
  end
  for _,id in ipairs({"rocket_materials","platform_launch"}) do
    local gate=done.chemical_science
    if id=="platform_launch" then gate=gate and done.rocket_materials end
    if gate and not done[id] and tick>=(npc.project_retry and npc.project_retry[id] or 0) then
      local plan,reason=Rocket.plan(npc,id)
      if plan then list[#list+1]={id=id,project_id=id,action="project_"..id}
      else blocker=reason end
    end
  end
  if tick>=(npc.rail_retry_at or 0) then
    for _,candidate in ipairs(RailTrade.candidates(npc)) do
      list[#list+1]={id="ground_rail_trade_"..candidate.receiver_id,
        project_id="ground_rail_trade",action="project_ground_rail_trade",
        target_id=candidate.receiver_id,item=candidate.item,rail_options=candidate.options}
    end
  end
  list[#list+1]={id="maintain",action="idle"}
  return list,blocker
end
function Choices.choose(npc,tick)
  local candidates,blocker=Choices.candidates(npc,tick)
  npc.growth_wait_reason=blocker
  -- Private paired evaluation can hold the common origin or use a reproducible
  -- procedure teacher. Ordinary games keep the saved probabilistic policy.
  local dev=storage.dev
  local mode=dev and dev.enabled and dev.training and dev.training.enabled
    and dev.training.project_policy
  if mode=="hold" then return nil end
  if mode=="teacher" then
    local by_id={} for _,candidate in ipairs(candidates) do by_id[candidate.project_id or "idle"]=candidate end
    local threat=npc.character.surface.count_entities_filtered{position=npc.character.position,
      radius=64,force="enemy",type={"unit","unit-spawner","turret"},limit=1}>0
    if threat and by_id.basic_defense then return "basic_defense" end
    for _,id in ipairs({"electric_mining","green_science","steel_processing","basic_defense",
        "oil_processing","chemical_science","rocket_materials","platform_launch","ground_rail_trade"}) do
      local candidate=by_id[id]
      if candidate then return id,nil,candidate.rail_options end
    end
    return nil
  end
  local choice,token=Strategy.choose(npc,candidates,{contacts={}})
  if not choice or not choice.project_id then
    if token then Strategy.close(npc,token,"maintain_existing_factory") end
    return nil
  end
  return choice.project_id,token,choice.rail_options
end
return Choices
