-- Convert an accepted faction intention into reach-checked physical work.
-- Target discovery is local; foreign private inventories are not strategic input.
local Society=require("scripts.society")
local Cooperation=require("scripts.cooperation")
local Strategy=require("scripts.strategy_policy")
local Worker=require("scripts.worker")
local SocialJobs={}
local function distance(a,b) local x,y=a.x-b.x,a.y-b.y;return math.sqrt(x*x+y*y) end
local function hostile(kind) return kind=="theft" or kind=="sabotage" end
local function failed(npc,pending,reason)
  Society.on_worker_receipt(npc,pending.intent or pending,
    {status="failed",reason=reason,count=0})
  if pending.token then Strategy.feedback(npc,pending.token,-.5,reason) end
end
local function hostile_target(npc,other,kind)
  local entities=npc.character.surface.find_entities_filtered{position=npc.character.position,
    radius=48,force=other.force,type={"container","logistic-container","furnace","mining-drill","boiler"}}
  table.sort(entities,function(a,b)
    local da,db=distance(npc.character.position,a.position),distance(npc.character.position,b.position)
    return da<db or da==db and (a.unit_number or 0)<(b.unit_number or 0)
  end)
  for _,target in ipairs(entities) do
    local inventory,index
    if kind=="sabotage" then inventory=target.get_fuel_inventory();index=defines.inventory.fuel
    elseif target.type=="container" or target.type=="logistic-container" then
      inventory=target.get_inventory(defines.inventory.chest);index=defines.inventory.chest
    else inventory=target.get_output_inventory() end
    if inventory and inventory.valid then
      for _,item in ipairs(kind=="sabotage" and {"coal"} or {"iron-plate","copper-plate","coal","steel-plate"}) do
        local amount=inventory.get_item_count{name=item,quality="normal"}
        if amount>0 then return target,item,math.min(8,amount),index end
      end
    end
  end
end
function SocialJobs.next(npc)
  local actor=Society.ensure(npc)
  local pending=actor.pending_intent
  if npc.social_job then return nil end
  if not pending then
    local job,task=Cooperation.shared_site_job(storage.society,npc)
    if job then return job,{intent={kind="shared_site"},site_task=task} end
    return nil
  end
  local intent=pending.intent or pending
  local other=storage.npcs[intent.target_id]
  local target,item,amount,index,request
  if intent.kind=="supply" and intent.request_id then
    local held=Cooperation.find(storage.society,intent.request_id)
    if held and held.transport and held.transport.status~="ready_last_mile"
        and held.status=="accepted" and held.expires_tick>game.tick then
      return nil
    end
  end
  if hostile(intent.kind) then
    local allowed,reason=Society.hostile_mission_allowed(npc,intent.target_id,intent)
    actor.pending_intent=nil
    if not allowed then failed(npc,pending,reason);return nil end
    if intent.stage~="search" then
      return {kind="move",position=intent.last_seen_position,
        deadline=intent.expires_tick},{intent=intent,token=pending.token,stage="approach"}
    end
    if distance(npc.character.position,intent.last_seen_position)>48 then
      failed(npc,pending,"remembered_contact_not_reached");return nil
    end
    if other and other.force and other.force.valid then
      target,item,amount,index=hostile_target(npc,other,intent.kind)
    end
  end
  if other and other.character and other.character.valid and other.character.surface==npc.character.surface then
    if intent.kind=="supply" then
      request=Cooperation.valid_delivery(storage.society,intent,npc,other,
        Society.delivery_allowed(npc,other))
      if request then
        if request.delivery_mode=="rail" and not request.transport then return nil end
        if not request.transport then
          local walk,reason=Cooperation.walking_route(storage.society,npc,other)
          if not walk then
            actor.pending_intent=nil
            failed(npc,pending,reason)
            return nil
          end
        end
        target=other.character;item=intent.item;amount=intent.count
      end
    end
  end
  actor.pending_intent=nil
  if not target or not amount or amount<1 then
    failed(npc,pending,"no_local_physical_target")
    return nil
  end
  if request and request.source_kind=="supplier_owned_site_chest" and intent.stage~="deliver" then
    local collect=Cooperation.collection_job(request)
    if not collect then
      Society.on_worker_receipt(npc,intent,{status="failed",reason="owned_source_chest_missing",count=0})
      if pending.token then Strategy.feedback(npc,pending.token,-.5,"owned_source_chest_missing") end
      return nil
    end
    return collect,{intent=intent,token=pending.token,stage="collect"}
  end
  npc.social_sequence=(npc.social_sequence or 0)+1
  local id=tostring(npc.id).."-social-"..npc.social_sequence
  storage.social_intents=storage.social_intents or {}
  storage.social_intents[id]={id=id,status="pending",actor_id=npc.id,kind=intent.kind,
    target_entity=target,target_unit_number=target.unit_number,target_force=target.force.name,
    surface_index=target.surface.index,item=item,count=amount,request_id=intent.request_id,
    expires_tick=math.min(game.tick+(hostile(intent.kind) and 3600 or 36000),
      (intent.request_id and Cooperation.find(storage.society,intent.request_id).expires_tick) or game.tick+36000)}
  if hostile(intent.kind) then
    storage.social_intents[id].expires_tick=math.min(
      storage.social_intents[id].expires_tick,intent.expires_tick)
  end
  intent.item,intent.count=item,amount
  local job={kind=intent.kind=="supply" and "transfer" or intent.kind,target=target,
    item=item,count=amount,mode=intent.kind=="supply" and "deposit" or "withdraw",
    inventory=index,social_intent_id=id}
  return job,{intent=intent,token=pending.token,intent_id=id}
end
function SocialJobs.complete(npc,pending,receipt)
  if pending.site_task then
    return Cooperation.shared_site_receipt(storage.society,npc,pending.site_task,receipt)
  end
  if pending.stage=="collect" then
    if receipt.status=="complete" then
      pending.intent.stage="deliver"
      Society.ensure(npc).pending_intent={intent=pending.intent,token=pending.token}
    else
      Society.on_worker_receipt(npc,pending.intent,{status="failed",
        reason=receipt.reason or "collection_failed",count=0,job_id=receipt.job_id})
      if pending.token then Strategy.feedback(npc,pending.token,-.5,"collection_failed") end
    end
    return true
  end
  if pending.stage=="approach" then
    if receipt.status=="complete" then
      local allowed,reason=Society.hostile_mission_allowed(npc,
        pending.intent.target_id,pending.intent)
      if allowed then
        pending.intent.stage="search"
        Society.ensure(npc).pending_intent={intent=pending.intent,token=pending.token}
      else failed(npc,pending,reason) end
    else failed(npc,pending,receipt.reason or "hostile_approach_failed") end
    return true
  end
  local grant=storage.social_intents and storage.social_intents[pending.intent_id]
  if not grant or grant.status~="pending" or receipt.social_intent_id~=pending.intent_id then return false end
  grant.status=receipt.status;grant.completed_tick=game.tick
  Society.on_worker_receipt(npc,pending.intent,receipt)
  if pending.token then
    Strategy.feedback(npc,pending.token,receipt.status=="complete" and .6 or -.5,
      pending.intent.kind..":"..receipt.status)
  end
  return true
end
-- Called before Worker.tick so a truce or stale contact stops an active
-- approach or theft/sabotage job before its next native action.
function SocialJobs.cancel_invalid(npc,tick)
  local social=npc.social_job
  local pending=Society.ensure(npc).pending_intent
  local entry=social or pending
  local intent=entry and (entry.intent or entry)
  if not intent then return false end
  local allowed,reason
  if intent.kind=="supply" and intent.request_id then
    local request=Cooperation.find(storage.society,intent.request_id)
    if not request or not request.project then return false end
    local leader=storage.npcs and storage.npcs[request.requester_id]
    allowed=request.status=="accepted" and request.expires_tick>tick
        and Cooperation.project_valid(request,leader)
        and leader and Society.delivery_allowed(npc,leader)
    reason="coalition_project_request_withdrawn"
    if not allowed and request.status=="accepted" then
      request.status="withdrawn";request.resolved_tick=tick
    end
  elseif hostile(intent.kind) then
    allowed,reason=Society.hostile_mission_allowed(npc,intent.target_id,intent)
  else return false end
  if allowed then return false end
  if social and social.job_id then
    Worker.cancel_job(npc,social.job_id,reason)
  elseif pending then
    Society.ensure(npc).pending_intent=nil
    failed(npc,pending,reason)
  end
  return true
end
function SocialJobs.cleanup(tick)
  for id,intent in pairs(storage.social_intents or {}) do
    if tick>intent.expires_tick+36000 then storage.social_intents[id]=nil end
  end
end
return SocialJobs
