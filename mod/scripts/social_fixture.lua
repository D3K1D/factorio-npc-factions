-- Explicit private test setup for physical cross-faction Worker receipts.
-- The declared starting stock and site entities are fixture grants only.
local Fixture={}
local Worker=require("scripts.worker")

local function normal(name) return {name=name,quality="normal"} end
local function count(inventory,name)
  return inventory and inventory.valid and inventory.get_item_count(normal(name)) or 0
end
local function set_count(inventory,name,amount)
  if not inventory or not inventory.valid then return false end
  local existing=count(inventory,name)
  if existing>0 then inventory.remove{name=name,quality="normal",count=existing} end
  if amount>0 then
    return inventory.insert{name=name,quality="normal",count=amount}==amount
  end
  return true
end
local function free_site(surface,center,name)
  -- Keep the test site closer to the actor than the target's pre-existing camp,
  -- while staying inside the 32-tile locally discovered hostile-action radius.
  for radius=24,28,2 do
    for offset=0,7 do
      local angle=offset*math.pi/4
      local position={x=math.floor(center.x+radius*math.cos(angle))+.5,
        y=math.floor(center.y+radius*math.sin(angle))+.5}
      if surface.can_place_entity{name=name,position=position} then return position end
    end
  end
end
local function idle(npc)
  return not npc.pending_job and not npc.current_job and #(npc.jobs or {})==0
      and not npc.social_job
end

function Fixture.setup(value)
  if not (storage.dev and storage.dev.enabled and storage.dev.training
      and storage.dev.training.enabled and game.tick_paused) then
    return {ok=false,reason="private_paused_training_only"}
  end
  if type(value)~="table" or (value.case~="trade" and value.case~="theft"
      and value.case~="sabotage" and value.case~="invalid-intent") then
    return {ok=false,reason="invalid_social_case"}
  end
  local actor=storage.npcs and storage.npcs[value.actor]
  local target=storage.npcs and storage.npcs[value.target]
  if not actor or not target or actor==target or not actor.character or not target.character
      or not actor.character.valid or not target.character.valid
      or actor.character.surface~=target.character.surface then
    return {ok=false,reason="invalid_actor_pair"}
  end
  if not idle(actor) or not idle(target) then return {ok=false,reason="actors_busy"} end
  if (actor.step or 0)<=8 or (target.step or 0)<=8 or actor.suspended or target.suspended then
    return {ok=false,reason="actors_not_bootstrapped"}
  end
  local surface=actor.character.surface
  local actor_inventory=actor.character.get_main_inventory()
  local target_inventory=target.character.get_main_inventory()
  if not actor_inventory or not target_inventory then
    return {ok=false,reason="character_inventory_missing"}
  end
  local target_position=surface.find_non_colliding_position("character",target.home,12,.5)
  if not target_position then return {ok=false,reason="target_site_blocked"} end
  local site_name=(value.case=="theft" or value.case=="invalid-intent") and "wooden-chest"
      or value.case=="sabotage" and "stone-furnace" or nil
  local site_position=value.case~="trade" and free_site(surface,target_position,site_name)
  if value.case~="trade" and not site_position then
    return {ok=false,reason="owned_site_blocked"}
  end
  local actor_center=site_position or target_position
  local actor_position=surface.find_non_colliding_position("character",
    {x=actor_center.x+2,y=actor_center.y},8,.5)
  if not actor_position then return {ok=false,reason="actor_site_blocked"} end
  if not target.character.teleport(target_position,surface)
      or not actor.character.teleport(actor_position,surface) then
    return {ok=false,reason="fixture_teleport_failed"}
  end
  local site
  if site_position then
    site=surface.create_entity{name=site_name,position=site_position,
      force=target.force,raise_built=true}
    if not site or not site.valid then return {ok=false,reason="owned_site_creation_failed"} end
  end
  if value.case=="trade" then
    if not set_count(actor_inventory,"coal",12) or not set_count(target_inventory,"coal",4) then
      return {ok=false,reason="character_stock_setup_failed"}
    end
  elseif value.case=="theft" or value.case=="invalid-intent" then
    if not set_count(actor_inventory,"iron-plate",0)
        or not set_count(site.get_inventory(defines.inventory.chest),"iron-plate",8) then
      return {ok=false,reason="chest_stock_setup_failed"}
    end
  else
    if not set_count(actor_inventory,"coal",0)
        or not set_count(site.get_fuel_inventory(),"coal",8) then
      return {ok=false,reason="fuel_stock_setup_failed"}
    end
  end
  actor.retry_at=game.tick
  actor.next_decision=value.case=="invalid-intent" and game.tick+36000 or game.tick
  target.next_decision=game.tick+36000
  local record={case=value.case,actor=actor.id,target=target.id,tick=game.tick,
    grants={relocation=true,site=site_name,
      stock_item=(value.case=="theft" or value.case=="invalid-intent") and "iron-plate" or "coal",
      stock_count=value.case=="trade" and 16 or 8},
    actor_position=actor.character.position,target_position=target.character.position,
    site=site and {name=site.name,position=site.position,unit_number=site.unit_number} or nil,
    actor_stock={coal=count(actor_inventory,"coal"),
      ["iron-plate"]=count(actor_inventory,"iron-plate")},
    target_stock={coal=count(target_inventory,"coal"),
      ["iron-plate"]=count(target_inventory,"iron-plate")},
    site_stock=site and ((value.case=="theft" or value.case=="invalid-intent")
      and count(site.get_inventory(defines.inventory.chest),"iron-plate")
      or count(site.get_fuel_inventory(),"coal")) or nil}
  if value.case=="invalid-intent" then
    local id="forged-social-"..actor.id.."-"..game.tick
    local queued,reason=Worker.enqueue(actor,{id=id,kind="theft",target=site,
      item="iron-plate",count=1,mode="withdraw",inventory=defines.inventory.chest,
      social_intent_id="forged:unregistered"})
    if not queued then return {ok=false,reason="forged_job_enqueue_failed:"..tostring(reason)} end
    record.forged_job_id=id
  end
  storage.dev.social_fixture=record
  return {ok=true,fixture=record}
end

function Fixture.observe()
  local record=storage.dev and storage.dev.social_fixture
  if not record then return {ok=false,reason="no_social_fixture"} end
  local actor=storage.npcs[record.actor]
  local target=storage.npcs[record.target]
  if not actor or not target or not actor.character.valid or not target.character.valid then
    return {ok=false,reason="fixture_actor_missing"}
  end
  local site
  if record.site then
    for _,entity in ipairs(actor.character.surface.find_entities_filtered{
        position=record.site.position,name=record.site.name,force=target.force}) do
      if entity.valid and entity.unit_number==record.site.unit_number then site=entity;break end
    end
  end
  local source=actor.character.get_main_inventory()
  local destination=target.character.get_main_inventory()
  local item=(record.case=="theft" or record.case=="invalid-intent") and "iron-plate" or "coal"
  local site_inventory=site and ((record.case=="theft" or record.case=="invalid-intent")
    and site.get_inventory(defines.inventory.chest) or site.get_fuel_inventory()) or nil
  local receipts={}
  for id,intent in pairs(storage.social_intents or {}) do
    if intent.actor_id==actor.id and intent.kind==record.case or
        intent.actor_id==actor.id and record.case=="trade" and intent.kind=="supply" then
      receipts[#receipts+1]={id=id,status=intent.status,item=intent.item,count=intent.count}
    end
  end
  local forged=record.forged_job_id and actor.receipts and actor.receipts[record.forged_job_id]
  return {ok=true,case=record.case,tick=game.tick,item=item,
    actor_count=count(source,item),target_count=count(destination,item),
    site_count=count(site_inventory,item),site_exists=site~=nil,
    actor_position=actor.character.position,target_position=target.character.position,
    actor_phase=actor.phase,actor_pending_job=actor.pending_job,
    actor_social_job=actor.social_job,receipts=receipts,
    forged_receipt=forged and {status=forged.status,reason=forged.reason,job_id=forged.job_id} or nil,
    initial=record}
end

return Fixture
