-- Native UI-equivalent platform request and launch, plus evidence-based victory.
-- Creating the pending shell is never victory. No parts, payloads or hubs granted.
local Progression=require("scripts.progression")
local Launch={}
local function state()
  storage.platform_launch=storage.platform_launch or {requests={},pods={},winners={},events={},used_pods={}}
  storage.platform_launch.used_pods=storage.platform_launch.used_pods or {}
  return storage.platform_launch
end
local function owned(npc,silo)
  return npc.character and npc.character.valid and silo and silo.valid
    and silo.type=="rocket-silo" and silo.force==npc.force and silo.surface==npc.character.surface
end
function Launch.request(npc,name)
  if not script.active_mods["space-age"] then return false,"space_age_disabled" end
  -- space-platform itself is a create-space-platform trigger technology;
  -- requiring it before the first request would create a circular gate.
  local tech=npc.force.technologies["rocket-silo"]
  if not tech or not tech.researched then return false,"space_platform_technology_required" end
  local s=state();local existing=s.requests[npc.id]
  if existing and existing.platform and existing.platform.valid then return true,existing.platform end
  local platform=npc.force.create_space_platform{name=name or (npc.name.." orbital platform"),
    planet="nauvis",starter_pack={name="space-platform-starter-pack",quality="normal"}}
  if not platform then return false,"native_platform_request_rejected" end
  s.requests[npc.id]={actor_id=npc.id,force=npc.force.name,platform=platform,
    requested_tick=game.tick,starter_pack="space-platform-starter-pack",status="pending_shell"}
  return true,platform
end
function Launch.launch(npc,silo)
  if not owned(npc,silo) then return false,"owned_nauvis_silo_required" end
  if not npc.character.can_reach_entity(silo) then return false,"silo_out_of_reach" end
  local request=state().requests[npc.id]
  if not request or not request.platform or not request.platform.valid then return false,"pending_platform_required" end
  local platform=request.platform
  if platform.hub then return Launch.send_cargo(npc,silo,request) end
  if request.silo==silo and request.payload_confirmed and request.native_ready then
    -- Native logistics may start the rocket between the deposit and this job.
    -- Victory still waits for ordered+ascending events and the actual built hub.
    if request.status=="native_launch_ordered" then return true end
    local pod=silo.rocket and silo.rocket.valid and silo.rocket.attached_cargo_pod
    local destination=pod and pod.valid and pod.cargo_pod_destination
    if destination and destination.type==defines.cargo_destination.space_platform
        and destination.space_platform==platform then return true end
  end
  local cargo=silo.get_inventory(defines.inventory.rocket_silo_rocket)
  -- The native parts accumulator resets when the assembled rocket appears.
  -- products_finished counts completed rockets here, not 50 individual parts.
  local rocket=silo.rocket
  if silo.rocket_silo_status~=defines.rocket_silo_status.rocket_ready
      or not rocket or not rocket.valid or rocket.force~=npc.force
      or silo.products_finished<1 then return false,"native_rocket_not_ready" end
  local pod=rocket.attached_cargo_pod
  if not pod or not pod.valid or not pod.unit_number then return false,"native_cargo_pod_required" end
  local payload=pod.get_inventory(defines.inventory.cargo_unit)
  local count=(cargo and cargo.get_item_count{name="space-platform-starter-pack",quality="normal"} or 0)
    +(payload and payload.get_item_count{name="space-platform-starter-pack",quality="normal"} or 0)
  if count~=1 then return false,"exactly_one_economic_starter_pack_required" end
  if state().used_pods[pod.unit_number] then return false,"rocket_already_launched" end
  request.silo=silo;request.native_ready=true;request.pod_unit_number=pod.unit_number
  request.completed_rocket_count=silo.products_finished
  request.payload_before=1;request.launch_attempt_tick=game.tick
  local ok=silo.launch_rocket{type=defines.cargo_destination.space_platform,space_platform=platform}
  if not ok then return false,"native_launch_not_ready" end
  state().used_pods[request.pod_unit_number]=true
  request.status="native_launch_ordered"
  return true
end
-- Cargo for a built platform (R-8): what the engineer loaded goes up to the hub; the pod is kept until it lands, so
-- its load counts as on its way (Launch.in_flight).
function Launch.send_cargo(npc,silo,request)
  local platform=request.platform
  if not (platform.hub and platform.hub.valid) then return false,"platform_hub_missing" end
  local rocket=silo.rocket
  if silo.rocket_silo_status~=defines.rocket_silo_status.rocket_ready or not rocket or not rocket.valid
      or rocket.force~=npc.force then return false,"native_rocket_not_ready" end
  local cargo=silo.get_inventory(defines.inventory.rocket_silo_rocket)
  if not cargo or cargo.is_empty() then return false,"cargo_empty" end
  local pod=rocket.attached_cargo_pod
  local ok=silo.launch_rocket{type=defines.cargo_destination.station,station=platform.hub}   -- (space_platform: starter packs only)
  if not ok then return false,"native_launch_not_ready" end
  if pod and pod.valid then
    local s=state()
    s.cargo=s.cargo or {}
    local list=s.cargo[npc.id] or {}
    list[#list+1]=pod;s.cargo[npc.id]=list
  end
  request.cargo_launches=(request.cargo_launches or 0)+1
  return true
end
-- What the engineer's launched pods carry still on the way up ({item=count}); landed pods are dropped.
function Launch.in_flight(npc)
  local s=state()
  local list=s.cargo and s.cargo[npc.id]
  local out,kept={},{}
  for _,pod in ipairs(list or {}) do
    if pod.valid then
      kept[#kept+1]=pod
      local inv=pod.get_inventory(defines.inventory.cargo_unit)
      for _,stack in ipairs(inv and inv.get_contents() or {}) do out[stack.name]=(out[stack.name] or 0)+stack.count end
    end
  end
  if list then s.cargo[npc.id]=kept end
  return out
end
function Launch.bind_payload(npc,silo,amount)
  if amount~=1 or not owned(npc,silo) then return false end
  local request=state().requests[npc.id]
  local rocket=silo.rocket
  if not request or not request.platform or not request.platform.valid or request.platform.hub
      or silo.rocket_silo_status~=defines.rocket_silo_status.rocket_ready
      or not rocket or not rocket.valid or rocket.force~=npc.force or silo.products_finished<1 then return false end
  local pod=rocket.attached_cargo_pod
  if not pod or not pod.valid or not pod.unit_number or state().used_pods[pod.unit_number] then return false end
  request.silo=silo;request.native_ready=true;request.pod_unit_number=pod.unit_number
  request.completed_rocket_count=silo.products_finished;request.payload_transfer_pending=true
  request.payload_before=1;request.launch_attempt_tick=game.tick
  return true
end
function Launch.confirm_payload(npc,silo,inserted,job_id)
  local request=state().requests[npc.id]
  if not request or request.silo~=silo or not request.payload_transfer_pending then return end
  request.payload_transfer_pending=nil
  request.payload_confirmed=inserted==1;request.payload_job_id=job_id
  if request.payload_confirmed then request.status="economic_payload_deposited" end
  local record=request.pod_unit_number and state().pods[request.pod_unit_number]
  if record then record.payload_confirmed=request.payload_confirmed end
end
function Launch.on_ordered(event)
  local silo,rocket=event.rocket_silo,event.rocket
  if not silo or not silo.valid or not rocket or not rocket.valid then return end
  local pod=rocket.attached_cargo_pod
  if not pod or not pod.valid then return end
  local destination=pod.cargo_pod_destination
  if destination.type~=defines.cargo_destination.space_platform then return end
  for id,request in pairs(state().requests) do
    if request.silo==silo and request.platform==destination.space_platform
        and request.pod_unit_number==pod.unit_number and request.native_ready
        and (request.payload_confirmed or request.payload_transfer_pending) then
      local inv=pod.get_inventory(defines.inventory.cargo_unit)
      local payload=inv and inv.get_item_count{name="space-platform-starter-pack",quality="normal"} or 0
      state().pods[pod.unit_number]={actor_id=id,pod_id=pod.unit_number,
        platform=request.platform,force=silo.force.name,native_ready=request.native_ready,
        completed_rocket_count=request.completed_rocket_count,pod_unit_number=request.pod_unit_number,
        payload=payload,payload_confirmed=request.payload_confirmed,ordered_tick=event.tick,origin=silo.surface.name}
      request.status="native_launch_ordered";state().used_pods[pod.unit_number]=true
    end
  end
end
function Launch.on_ascending(event)
  local pod=event.cargo_pod
  if not pod or not pod.valid or not event.launched_by_rocket then return end
  local record=state().pods[pod.unit_number]
  if not record then return end
  record.native_cargo_finished=true;record.cargo_finished_tick=event.tick
end
local function coalition_members(id)
  local members={id};local s=storage.society
  local actor=s and s.actors[id]
  local coalition=actor and actor.coalition_id and s.coalitions[actor.coalition_id]
  if coalition and coalition.status~="dissolved" then
    members={id}
    local founder=storage.npcs[id]
    for member in pairs(coalition.members or {}) do
      local npc=storage.npcs[member]
      local participant=s.actors[member]
      if member~=id and npc and founder and participant and participant.coalition_id==coalition.id
          and npc.force.get_friend(founder.force) and founder.force.get_friend(npc.force)
          and npc.force.get_cease_fire(founder.force) and founder.force.get_cease_fire(npc.force) then
        members[#members+1]=member
      end
    end
    table.sort(members)
  end
  return members
end
function Launch.tick(tick)
  if tick%60~=0 then return end
  local s=state()
  for _,record in pairs(s.pods) do
    local platform=record.platform
    if not record.qualified and record.native_cargo_finished and platform and platform.valid then
      local hub=platform.hub
      local evidence={starter_pack_consumed=record.payload==1 and record.payload_confirmed==true and hub~=nil,
        native_cargo_finished=true,platform_hub_valid=hub and hub.valid or false,
        native_parts_completed=record.native_ready==true and (record.completed_rocket_count or 0)>=1,
        same_force=platform.force.name==record.force and hub and hub.force.name==record.force or false,
        orbit_nauvis=platform.space_location and platform.space_location.name=="nauvis" or false,
        starter_pack="space-platform-starter-pack",platform_index=platform.index,completed_tick=tick}
      local qualified=Progression.qualifies_first_platform(evidence)
      if qualified then
        record.qualified=true;record.evidence=evidence
        local members=coalition_members(record.actor_id)
        local result={actor_id=record.actor_id,members=members,platform_index=platform.index,
          completed_tick=tick,evidence=evidence,goal="first_orbital_platform"}
        for _,id in ipairs(members) do s.winners[id]=result end
        game.print("[NPC factions] "..platform.name.." reached Nauvis orbit. Winning engineers: "..table.concat(members,", "))
      end
    end
  end
end
function Launch.observe()
  local s=state();local requests={}
  for id,r in pairs(s.requests) do
    requests[#requests+1]={actor_id=id,status=r.status,requested_tick=r.requested_tick,
      platform_index=r.platform and r.platform.valid and r.platform.index or nil,
      hub_valid=r.platform and r.platform.valid and r.platform.hub and r.platform.hub.valid or false}
  end
  table.sort(requests,function(a,b)return a.actor_id<b.actor_id end)
  return {goal="first_orbital_platform",requests=requests,winners=s.winners}
end
return Launch
