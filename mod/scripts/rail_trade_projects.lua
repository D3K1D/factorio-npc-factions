-- Owned railway controller, plus separate read-only contract/terrain preflight.
-- Native recipes and placement counts fund ordinary Worker construction;
-- scoped fresh contracts authorize cargo and the final foreign handoff.
local RailTrade={revision="ground-rail-trade-controller-v2"}
local Rail=require("scripts.rail_support")
local Progression=require("scripts.progression")
local Rocket=require("scripts.rocket_projects")
local Cooperation=require("scripts.cooperation")
local Advanced=require("scripts.advanced_projects")
local Oil=require("scripts.oil_projects")
local Society=require("scripts.society")
-- Worker loads this route validator. Bind social callbacks after the complete
-- module graph loads, avoiding Worker -> RailTrade -> SocialJobs -> Worker.
local SocialJobs
function RailTrade.bind_social_adapter(adapter)
  assert(type(adapter)=="table" and type(adapter.next)=="function" and type(adapter.complete)=="function")
  SocialJobs=adapter
end
local function copy(v) if type(v)~="table" then return v end local t={} for k,x in pairs(v) do t[k]=copy(x) end return t end
local function reciprocal(a,b)
  return a.get_friend(b) and b.get_friend(a) and a.get_cease_fire(b) and b.get_cease_fire(a)
end
local function trade_permitted(a,b)
  local s=storage.society;if not s then return false end
  local first,last=math.min(a.id,b.id),math.max(a.id,b.id)
  local relation=s.relations and s.relations[first..":"..last]
  if relation and relation.trade==true then return true end
  local coalition=relation and relation.coalition_id and s.coalitions and s.coalitions[relation.coalition_id]
  return coalition and coalition.members[a.id] and coalition.members[b.id] or false
end
local function actor(id)
  local n=storage.npcs and storage.npcs[id]
  return n and n.character and n.character.valid and n.force and n.force.valid and n or nil
end
local function proposal(npc,request_id,options,infrastructure)
  options=options or {}
  local request=storage.society and storage.society.requests and storage.society.requests[request_id]
  local receiver=actor(infrastructure and options.receiver_id or request and request.requester_id)
  if not receiver or receiver.id==npc.id then error("different_live_rail_receiver_required",0) end
  if not infrastructure and (not request or request.status~="accepted" or request.supplier_id~=npc.id) then error("accepted_supplier_trade_contract_required",0) end
  if not infrastructure and request.expires_tick<=game.tick then error("rail_contract_expired",0) end
  if not reciprocal(npc.force,receiver.force) or not trade_permitted(npc,receiver) then error("current_reciprocal_route_treaty_required",0) end
  local item=infrastructure and (options.item or "iron-plate") or request.item
  if item~="coal" and item~="iron-plate" then error("initial_ground_rail_cargo_must_be_coal_or_iron",0) end
  local surface=npc.character.surface
  if receiver.character.surface~=surface or not surface.planet or surface.planet.name~="nauvis" then error("ground_rail_trade_requires_same_nauvis_surface",0) end
  local x,y1,y2=options.x,options.source_y,options.destination_y
  if type(x)~="number" or type(y1)~="number" or type(y2)~="number"
      or x~=math.floor(x) or y1~=math.floor(y1) or y2~=math.floor(y2)
      or x%2~=0 or y1%2~=0 or y2%2~=0 then error("explicit_even_ground_rail_grid_required",0) end
  if math.abs(y2-y1)<64 or math.abs(y2-y1)>2048 then error("rail_corridor_length_must_be_64_to_2048",0) end
  if not infrastructure and (request.accepted_count~=request.count or (request.remaining or request.count)~=request.count) then error("unstarted_exact_quantity_contract_required",0) end
  local quantity=infrastructure and (options.quantity or 20) or request.count
  if type(quantity)~="number" or quantity<1 or quantity>1000 or quantity~=math.floor(quantity) then error("bounded_integer_trade_quantity_required",0) end
  local p={id="ground_rail_trade",revision=RailTrade.revision,request_id=request_id,carrier_id=npc.id,
    supplier_id=npc.id,receiver_id=receiver.id,carrier_force=npc.force.name,surface_index=surface.index,
    receiver_force=receiver.force.name,item=item,quantity=quantity,expires_tick=request and request.expires_tick,sites={},steps={},construction_items={},
    status="proposal",implemented=false,manual_boundary_loading=true,train_count=1,ground_only=true,
    blockers={"carrier_schedule_worker_adapter_required","accepted_rail_cargo_source_capability_required",
      "journey_departure_arrival_and_loss_receipts_required","fresh_contract_after_owned_infrastructure_build_required"},
    qualification="read_only_budget_and_terrain_preflight"}
  local function site(key,name,xx,yy,d)
    local placement={name=name,position={x=xx,y=yy},direction=d or defines.direction.north,force=npc.force}
    local item=Rail.item_for_entity(npc,name)
    if not item then item={name=name,count=1,quality="normal"} end
    local s={name=name,item=item.name,item_count=item.count,position=placement.position,direction=placement.direction,surface_index=surface.index}
    local matches=surface.find_entities_filtered{position=s.position,radius=.1,name=name}
    if #matches>0 then
      local e=matches[1]
      if e.force~=npc.force or e.direction~=s.direction then error("rail_foreign_or_mismatched_existing_entity:"..key,0) end
      s.existing=true;s.existing_unit_number=e.unit_number
    elseif name~="locomotive" and name~="cargo-wagon" and name~="train-stop" then
      if not surface.can_place_entity{name=name,position=s.position,direction=s.direction,force=npc.force,build_check_type=defines.build_check_type.manual} then
        error("rail_native_terrain_or_entity_blocker:"..key,0)
      end
    else s.native_validation_after_owned_track=true end
    p.sites[key]=s
    if not s.existing then
      p.construction_items[item.name]=(p.construction_items[item.name] or 0)+item.count
      p.steps[#p.steps+1]={kind="wait",condition={kind="inventory",item=item.name,count=item.count,rail_supply=true}}
      p.steps[#p.steps+1]={kind="build",target_site=key}
    end
  end
  local bottom,top=math.max(y1,y2)+20,math.min(y1,y2)-20
  for y=top,bottom,2 do site("track_"..y,"straight-rail",x,y) end
  -- Single isolated bidirectional line, one train, no intersecting blocks.
  -- Signals are unnecessary here; a multi-train extension needs a new bill.
  site("north_stop","train-stop",x+2,math.min(y1,y2),defines.direction.north)
  site("south_stop","train-stop",x-2,math.max(y1,y2),defines.direction.south)
  site("front_locomotive","locomotive",x,math.max(y1,y2)-14,defines.direction.north)
  site("cargo_wagon","cargo-wagon",x,math.max(y1,y2)-7,defines.direction.north)
  site("rear_locomotive","locomotive",x,math.max(y1,y2),defines.direction.south)
  -- Boundary buffers and transfers belong to the carrier. Native cargo cannot
  -- be inserted into a receiver's foreign inventory merely by friendship.
  site("source_buffer","iron-chest",x+6,y1)
  site("destination_buffer","iron-chest",x+6,y2)
  local targets={}
  for name,t in pairs(npc.force.technologies) do for _,effect in pairs(t.prototype.effects or {}) do
    if effect.type=="unlock-recipe" and effect.recipe=="train-stop" then targets[#targets+1]=name;break end
  end end
  table.sort(targets)
  if #targets~=1 then error("rail_train_stop_research_unlock_ambiguous",0) end
  local dag,reason=Progression.technology_dag(npc.force,targets[1])
  if not dag then error(reason,0) end
  p.research_budget={packs={},technologies={},native_triggers={},normal_lab_ticks=0}
  for _,name in ipairs(dag.order) do local t=npc.force.technologies[name]
    if not t.researched then
      if t.prototype.research_trigger then
        p.research_budget.native_triggers[#p.research_budget.native_triggers+1]={technology=name,trigger=copy(t.prototype.research_trigger)}
        p.blockers[#p.blockers+1]="native_research_trigger:"..name
      else
        p.research_budget.technologies[#p.research_budget.technologies+1]=name
        p.research_budget.normal_lab_ticks=p.research_budget.normal_lab_ticks+t.research_unit_count*t.research_unit_energy
        for _,i in pairs(t.research_unit_ingredients) do p.research_budget.packs[i.name]=(p.research_budget.packs[i.name] or 0)+i.amount*t.research_unit_count end
      end
    end
  end
  p.materials=Rocket.material_budget(npc.force,p.construction_items)
  p.materials.starting_locomotive_fuel={coal=40} -- 20 each, separately mined.
  p.materials.trade_cargo={name=item,count=quantity,already_accepted_reservation=not infrastructure,
    fresh_contract_required_after_infrastructure=infrastructure==true}
  p.materials.operating_fuel_must_be_measured=true
  p.source_position={x=x+6,y=y1};p.destination_position={x=x+6,y=y2}
  p.pending_station_names={source="NPC carrier "..npc.id.." source "..tostring(request_id or "planned"),
    destination="NPC carrier "..npc.id.." delivery "..tostring(request_id or "planned")}
  p.source_stop_key=y1<y2 and "north_stop" or "south_stop"
  p.destination_stop_key=y1<y2 and "south_stop" or "north_stop"
  return p
end
-- Only registered canonical routes authorize Worker train controls.
function RailTrade.worker_route(npc,job)
  local board=storage.rail_trade
  local r=board and board.routes and board.routes[job.route_id]
  if not r or r.id~=job.route_id or r.carrier_id~=npc.id or r.carrier_force~=npc.force.name
      or not npc.character or not npc.character.valid or npc.character.force~=npc.force then
    return nil,"registered_owned_rail_route_required"
  end
  if r.status=="lost" or r.status=="canceled" or r.status=="complete" then return nil,"rail_route_inactive" end
  local partner=actor(r.receiver_id)
  if not partner or partner.force.name~=r.receiver_force or not reciprocal(npc.force,partner.force) or not trade_permitted(npc,partner) then
    return nil,"current_reciprocal_rail_partner_required"
  end
  local surface=npc.character.surface
  if surface.index~=r.surface_index or partner.character.surface~=surface
      or not surface.planet or surface.planet.name~="nauvis" then return nil,"same_nauvis_rail_route_required" end
  local expected={source_stop="train-stop",destination_stop="train-stop",wagon="cargo-wagon",
    front_locomotive="locomotive",rear_locomotive="locomotive"}
  local entities={}
  for key,name in pairs(expected) do
    local unit=r.entity_units and r.entity_units[key]
    local e=unit and game.get_entity_by_unit_number(unit)
    if not e or not e.valid or e.name~=name or e.force~=npc.force or e.surface~=surface then
      return nil,"registered_rail_entity_lost:"..key
    end
    entities[key]=e
  end
  if job.kind=="rail_move" then
    if type(r.expected_journey_number)~="number" or r.expected_journey_number<1
        or r.expected_journey_number~=math.floor(r.expected_journey_number)
        or r.expected_journey_number>9007199254740991
        or job.journey_id~=r.id.."_journey_"..r.expected_journey_number
        or job.journey_id~=r.expected_journey_id or job.destination_role~=r.expected_destination_role then
      return nil,"unexpected_registered_rail_journey"
    end
    if r.active_request_id then
      if not Cooperation.transport_valid then return nil,"rail_contract_claim_adapter_unavailable" end
      local valid,reason=Cooperation.transport_valid(r.active_request_id,npc,r.id,entities.wagon)
      if not valid then return nil,reason or "rail_contract_claim_invalid" end
    elseif job.destination_role~="source" then return nil,"fresh_rail_shipment_claim_required" end
    if not job.journey_started and not r.active_request_id
        and not entities.wagon.get_inventory(defines.inventory.cargo_wagon).is_empty() then
      return nil,"empty_positioning_train_required"
    end
  end
  return r,entities
end
local function held(n,item) return n.character.get_main_inventory().get_item_count{name=item,quality="normal"} end
local function owned_site(n,key)
  local p=n.factory_project and n.factory_project.plan;local s=p and p.sites[key]
  if not s then return nil end
  for _,e in pairs(n.character.surface.find_entities_filtered{position=s.position,radius=.1,name=s.name,force=n.force}) do
    if e.valid then return e end
  end
end
local function route_board()
  storage.rail_trade=storage.rail_trade or {routes={},next_route=1}
  return storage.rail_trade
end
local function partner_current(n,r)
  local b=actor(r.receiver_id)
  return b and b.force.name==r.receiver_force and n.force.name==r.carrier_force
    and n.character.surface.index==r.surface_index and b.character.surface==n.character.surface
    and reciprocal(n.force,b.force) and trade_permitted(n,b) or false
end
local function own_fuel(n)
  local keys={"boiler","rocket_boiler1","rocket_boiler2","rocket_boiler3","iron_furnace","copper_furnace","copper_drill","copper_ore_inserter"}
  local entities={};if n.miner and n.miner.valid and n.miner.force==n.force then entities[#entities+1]=n.miner end
  for _,key in ipairs(keys) do local e=owned_site(n,key);if e then entities[#entities+1]=e end end
  for _,e in ipairs(entities) do local inv=e.get_fuel_inventory()
    if inv and inv.get_item_count("coal")<1 then
      if held(n,"coal")<4 then return {kind="mine",item="coal",count=24,search_radius=128} end
      return {kind="transfer",target=e,item="coal",count=4,mode="deposit",inventory=defines.inventory.fuel}
    end
  end
end
function RailTrade.plan(n,options)
  if not n.factory_project or not n.factory_project.plan or not owned_site(n,"engine_assembler")
      or not owned_site(n,"construction_iron_furnace") or not owned_site(n,"construction_copper_furnace")
      or not owned_site(n,"steel_furnace") or not owned_site(n,"lab") then return nil,"owned_earned_production_required" end
  local ok,p=pcall(proposal,n,nil,options,true);if not ok then return nil,p end
  if p.sites[p.source_stop_key].position.y>=p.sites[p.destination_stop_key].position.y then
    return nil,"initial_economic_rail_source_must_be_north"
  end
  if #p.research_budget.native_triggers>0 then return nil,"existing_native_research_triggers_required" end
  p.implemented=true;p.status="planned";p.blockers={};p.qualification="physical_funded_infrastructure_then_fresh_contract"
  p.manual_boundary_loading=true;p.continuous_boundary_factory=false
  return p
end
function RailTrade.start(n,options)
  if n.rail_project and n.rail_project.status=="active" then return false,"rail_project_already_active" end
  if n.current_job or n.pending_job or n.social_job or # (n.jobs or {})>0 then return false,"actor_work_pending" end
  local previous=n.rail_project
  if previous and previous.status~="complete" then return false,"existing_owned_route_recovery_required" end
  if previous and previous.status=="complete" then
    local r=storage.rail_trade and storage.rail_trade.routes[previous.route_id]
    local plan=previous.plan;options=options or {}
    if not r or options.receiver_id~=r.receiver_id or (options.item or "iron-plate")~=plan.item
        or (options.quantity or 20)~=plan.quantity or options.x~=plan.sites.north_stop.position.x-2
        or options.source_y~=plan.sites[plan.source_stop_key].position.y
        or options.destination_y~=plan.sites[plan.destination_stop_key].position.y then
      return false,"existing_owned_route_reuse_required"
    end
    if not partner_current(n,r) then return false,"rail_partner_permission_withdrawn" end
    for key,name in pairs({source_stop="train-stop",destination_stop="train-stop",wagon="cargo-wagon",
        front_locomotive="locomotive",rear_locomotive="locomotive"}) do
      local e=game.get_entity_by_unit_number(r.entity_units[key])
      if not e or not e.valid or e.name~=name or e.force~=n.force or e.surface~=n.character.surface then
        return false,"registered_rail_entity_lost:"..key
      end
    end
    local wagon=game.get_entity_by_unit_number(r.entity_units.wagon)
    if not wagon.get_inventory(defines.inventory.cargo_wagon).is_empty() then return false,"empty_reusable_rail_wagon_required" end
    previous.deliveries=previous.deliveries or {};previous.deliveries[#previous.deliveries+1]=copy(previous.result)
    while #previous.deliveries>16 do table.remove(previous.deliveries,1) end
    previous.result=nil;previous.status="active";previous.phase="fuel_train";previous.reason=nil
    previous.request_id=nil;previous.quantity=nil;previous.infrastructure_completed_tick=nil;previous.completed_tick=nil
    r.status="infrastructure";r.active_request_id=nil
    return true,{route_id=r.id,reused_owned_route=true,inventory_grants=0,research_grants=0}
  end
  local p,reason=RailTrade.plan(n,options);if not p then return false,reason end
  local board=route_board();local id="rail_"..n.id.."_"..board.next_route;board.next_route=board.next_route+1
  local r={id=id,carrier_id=n.id,receiver_id=p.receiver_id,carrier_force=n.force.name,receiver_force=p.receiver_force,
    surface_index=p.surface_index,status="infrastructure",entity_units={},journey_counter=0,
    station_names={source="NPC "..n.id.." source "..id,destination="NPC "..n.id.." delivery "..id}}
  board.routes[id]=r
  n.rail_project={id="ground_rail_trade",route_id=id,plan=p,status="active",phase="research",research_index=1,
    construction_index=1,started_tick=game.tick,receipts={},ledger={mined={},crafted={},transferred={},built={},cargo={loaded=0,unloaded=0,delivered=0}},losses={}}
  return true,{route_id=id,material_budget=p.materials,research_budget=p.research_budget,
    inventory_grants=0,research_grants=0,ordinary_production_project_preserved=true}
end
local function current(n)
  local p=n.rail_project;local b=storage.rail_trade;local r=p and b and b.routes[p.route_id]
  return p,r
end
local function entity_for(p,key)
  local s=p.plan.sites[key];local e=s and s.existing_unit_number and game.get_entity_by_unit_number(s.existing_unit_number)
  return e and e.valid and e or nil
end
local function rolling_center(n,p,s)
  local front=entity_for(p,"front_locomotive")
  -- Native 2.0.77 vertical even-grid stock normalizes one tile east of the
  -- planner coordinate. Once the first locomotive exists, use its real centre.
  return front and front.force==n.force and front.position.x or s.position.x+1
end
local function rolling_stage(n,p,s)
  local center=rolling_center(n,p,s);local at=n.character.position
  local dx,dy=at.x-center,at.y-s.position.y
  if math.abs(dx)>=3 and dx*dx+dy*dy<=math.min(9,n.character.build_distance-.75)^2 then return nil end
  local choices={}
  for _,side in ipairs({-1,1}) do
    local goal=n.character.surface.find_non_colliding_position("character",{x=center+side*5,y=s.position.y},1,.25)
    if goal and math.abs(goal.x-center)>=3.5 then
      choices[#choices+1]={position={x=goal.x,y=goal.y},distance=(goal.x-at.x)^2+(goal.y-at.y)^2}
    end
  end
  table.sort(choices,function(a,b) return a.distance<b.distance end)
  if #choices==0 then return nil,"native_rolling_stock_build_side_unavailable" end
  return {kind="move",position=choices[1].position},nil,center
end
local function emit(p,job,action)
  if job then p.pending_action=action or {kind="upkeep"};p.reason=nil end
  return job
end
local function move(n,p,r,role)
  local sequence=r.journey_counter+1
  r.expected_journey_number=sequence;r.expected_journey_id=r.id.."_journey_"..sequence;r.expected_destination_role=role
  return emit(p,{kind="rail_move",target=game.get_entity_by_unit_number(r.entity_units.wagon),route_id=r.id,
    journey_id=r.expected_journey_id,destination_role=role},{kind="journey",role=role,sequence=sequence})
end
local function fail(n,p,r,reason,receipt)
  p.status="failed";p.reason=reason;r.status="lost"
  if r.active_request_id then
    local request=Cooperation.find(storage.society,r.active_request_id)
    local wagon=request and request.transport and request.transport.wagon
    Cooperation.release_transport(r.active_request_id,n,r.id,wagon,reason)
  end
  p.losses[#p.losses+1]={tick=game.tick,reason=reason,receipt_id=receipt and receipt.id,
    cargo_loss=receipt and copy(receipt.cargo_loss),cargo_loss_unknown=receipt and receipt.cargo_loss_unknown}
  while #p.losses>32 do table.remove(p.losses,1) end
end
function RailTrade.next(n)
  local p,r=current(n);if not p or not r or p.status~="active" or p.pending_job then return nil end
  if not partner_current(n,r) then fail(n,p,r,"rail_partner_permission_withdrawn");return nil end
  -- Existing earned production keeps its saved plan and finishes first.
  if n.factory_project and n.factory_project.status=="active" then p.reason="earned_production_project_active";return nil end
  if not r.active_request_id then local fuel=own_fuel(n);if fuel then return emit(p,fuel) end end
  if p.phase=="research" then
    while p.research_index<=#p.plan.research_budget.technologies do
      local name=p.plan.research_budget.technologies[p.research_index];local t=n.force.technologies[name]
      if t.researched then p.research_index=p.research_index+1
      elseif n.force.current_research~=t then return emit(p,{kind="select_research",technology=name})
      else
        local job=Advanced.maintenance(n,{kind="technology",technology=name})
        if not job then job=Oil.research_maintenance(n) end
        p.reason="native_rail_research_wait";return emit(p,job)
      end
    end
    p.phase="construction"
  end
  if p.phase=="construction" then
    while p.construction_index<=#p.plan.steps do
      local step=p.plan.steps[p.construction_index]
      if step.kind=="wait" then
        if held(n,step.condition.item)>=(step.condition.count or 1) then p.construction_index=p.construction_index+1
        else p.reason="funding_native_rail_items";return emit(p,Rocket.supply(n,step.condition.item,step.condition.count)) end
      else
        local s=p.plan.sites[step.target_site];local e=entity_for(p,step.target_site)
        if e then p.construction_index=p.construction_index+1
        else
          if s.name=="locomotive" or s.name=="cargo-wagon" then
            local move_job,reason,center=rolling_stage(n,p,s)
            if reason then fail(n,p,r,reason);return nil end
            if move_job then return emit(p,move_job,{kind="rolling_stage",site_key=step.target_site,center=center}) end
          end
          return emit(p,{kind="build",entity_name=s.name,item=s.item,position=s.position,direction=s.direction},
            {kind="build",site_key=step.target_site})
        end
      end
    end
    for logical,key in pairs({source_stop=p.plan.source_stop_key,destination_stop=p.plan.destination_stop_key,
      wagon="cargo_wagon",front_locomotive="front_locomotive",rear_locomotive="rear_locomotive"}) do
      local e=entity_for(p,key);if not e or e.force~=n.force then fail(n,p,r,"owned_rail_infrastructure_missing:"..logical);return nil end
      r.entity_units[logical]=e.unit_number
    end
    p.phase="configure_source"
  end
  if p.phase=="configure_source" or p.phase=="configure_destination" then
    local role=p.phase=="configure_source" and "source" or "destination"
    return emit(p,{kind="configure_train_stop",target=game.get_entity_by_unit_number(r.entity_units[role.."_stop"]),
      route_id=r.id,station_role=role},{kind="station",role=role})
  end
  if p.phase=="fuel_train" then
    for _,key in ipairs({"front_locomotive","rear_locomotive"}) do
      local e=game.get_entity_by_unit_number(r.entity_units[key]);local amount=e.get_fuel_inventory().get_item_count("coal")
      if amount<20 then
        local need=20-amount
        if held(n,"coal")<need then return emit(p,{kind="mine",item="coal",count=need-held(n,"coal"),search_radius=128}) end
        return emit(p,{kind="transfer",target=e,item="coal",count=need,mode="deposit",inventory=defines.inventory.fuel})
      end
    end
    p.phase="position_source"
  end
  if p.phase=="position_source" then return move(n,p,r,"source") end
  if p.phase=="fresh_contract" then
    r.status="ready";p.reason="fresh_accepted_shipment_required_after_infrastructure"
    for _,request in ipairs(storage.society.requests or {}) do
      if request.status=="accepted" and request.supplier_id==n.id and request.requester_id==r.receiver_id
          and request.source_kind=="supplier_character_main" and request.destination_kind=="requester_character_main"
          and request.item==p.plan.item and type(request.count)=="number" and request.count>=1
          and request.count<=p.plan.quantity and request.count==math.floor(request.count) and request.accepted_count==request.count
          and request.remaining==request.count and not request.transport and request.expires_tick>game.tick
          and request.accepted_tick and request.accepted_tick>=p.infrastructure_completed_tick then
        if held(n,request.item)<request.accepted_count then p.reason="accepted_shipment_physical_stock_missing";return nil end
        local wagon=game.get_entity_by_unit_number(r.entity_units.wagon)
        local claim,reason=Cooperation.claim_transport(request.id,n,r.id,wagon)
        if not claim then p.reason=reason;return nil end
        r.active_request_id=request.id;p.request_id=request.id;p.phase="loading";p.quantity=request.accepted_count
        r.status="shipping";break
      end
    end
    if p.phase=="fresh_contract" then return nil end
  end
  if p.phase=="loading" or p.phase=="unloading" then
    local wagon=game.get_entity_by_unit_number(r.entity_units.wagon)
    local valid,reason=Cooperation.transport_valid(p.request_id,n,r.id,wagon)
    if not valid then fail(n,p,r,reason);return nil end
    local loading=p.phase=="loading"
    return emit(p,{kind="transfer",target=wagon,item=p.plan.item,count=p.quantity,mode=loading and "deposit" or "withdraw",
      inventory=defines.inventory.cargo_wagon},{kind="cargo",stage=loading and "loaded" or "unloaded"})
  end
  if p.phase=="shipping" then return move(n,p,r,"destination") end
  if p.phase=="last_mile" then
    if not SocialJobs then p.reason="rail_social_adapter_not_bound";return nil end
    local request=Cooperation.find(storage.society,p.request_id)
    if not request or request.status~="accepted" or request.expires_tick<=game.tick then fail(n,p,r,"rail_last_mile_contract_expired");return nil end
    Society.ensure(n).pending_intent={intent={kind="supply",actor_id=n.id,target_id=r.receiver_id,
      request_id=p.request_id,item=p.plan.item,count=p.quantity,stage="deliver"}}
    local job,pending=SocialJobs.next(n)
    if not job then fail(n,p,r,"rail_last_mile_capability_unavailable");return nil end
    return emit(p,job,{kind="last_mile",social_pending=pending})
  end
  return nil
end
function RailTrade.bind(n,job_id)
  local p,r=current(n);if not p or not r or not p.pending_action or p.pending_job then return false end
  p.pending_job=job_id;p.pending_task=p.pending_action;p.pending_action=nil
  if p.pending_task.kind=="journey" then r.journey_counter=p.pending_task.sequence end
  return true
end
function RailTrade.on_receipt(n,receipt)
  local p,r=current(n);if not p or not r or p.pending_job~=receipt.job_id then return false end
  local task=p.pending_task;p.pending_job=nil;p.pending_task=nil
  if not p.receipts[receipt.id] then
    p.receipts[receipt.id]=true;local count=math.max(0,receipt.count or 0)
    p.ledger.cargo=p.ledger.cargo or {loaded=0,unloaded=0,delivered=0}
    if task.kind=="cargo" and count>0 then
      local key=task.stage=="loaded" and "loaded" or "unloaded";p.ledger.cargo[key]=p.ledger.cargo[key]+count
    elseif task.kind=="last_mile" and count>0 then p.ledger.cargo.delivered=p.ledger.cargo.delivered+count end
    if receipt.kind=="mine" and count>0 then p.ledger.mined[receipt.item]=(p.ledger.mined[receipt.item] or 0)+count
    elseif receipt.kind=="craft" and (receipt.completed or 0)>0 then p.ledger.crafted[receipt.recipe]=(p.ledger.crafted[receipt.recipe] or 0)+receipt.completed
    elseif receipt.kind=="transfer" and count>0 then local key=tostring(receipt.mode)..":"..receipt.item
      p.ledger.transferred[key]=(p.ledger.transferred[key] or 0)+count
    elseif receipt.kind=="build" and receipt.status=="complete" then p.ledger.built[receipt.item]=(p.ledger.built[receipt.item] or 0)+(receipt.consumed[receipt.item] or 1) end
  end
  if receipt.status~="complete" then
    if task.kind=="last_mile" and SocialJobs then SocialJobs.complete(n,task.social_pending,receipt) end
    if receipt.status=="canceled" and receipt.reason=="combat_interrupt" and not r.active_request_id then p.reason="combat_interrupted_rail_work";return true end
    fail(n,p,r,receipt.reason or "rail_worker_failed",receipt);return true
  end
  if task.kind=="build" then
    local e=receipt.entity
    if not e or not e.valid or e.force~=n.force then fail(n,p,r,"rail_build_world_loss",receipt);return true end
    if e.name=="locomotive" or e.name=="cargo-wagon" then
      local separation=math.abs(n.character.position.x-e.position.x)
      if separation<3 then fail(n,p,r,"unsafe_native_rolling_stock_build_body_position",receipt);return true end
      p.rolling_build_safety=p.rolling_build_safety or {}
      p.rolling_build_safety[task.site_key]={receipt_id=receipt.id,native_unit_number=e.unit_number,
        native_center_x=e.position.x,body_x=n.character.position.x,side_separation=separation}
    end
    p.plan.sites[task.site_key].existing_unit_number=e.unit_number;p.construction_index=p.construction_index+1
  elseif task.kind=="rolling_stage" then
    if math.abs(n.character.position.x-task.center)<3 then fail(n,p,r,"rolling_stock_build_staging_not_reached",receipt);return true end
    p.rolling_stage_moves=(p.rolling_stage_moves or 0)+1
  elseif task.kind=="station" then p.phase=task.role=="source" and "configure_destination" or "fuel_train"
  elseif task.kind=="journey" then
    if task.role=="source" then p.phase="fresh_contract";p.infrastructure_completed_tick=game.tick;r.status="ready"
    else
      local valid,reason=Cooperation.transport_stage(p.request_id,n,r.id,game.get_entity_by_unit_number(r.entity_units.wagon),"traveled",p.quantity,receipt)
      if not valid then fail(n,p,r,reason,receipt);return true end;p.phase="unloading"
    end
  elseif task.kind=="cargo" then
    local valid,reason=Cooperation.transport_stage(p.request_id,n,r.id,game.get_entity_by_unit_number(r.entity_units.wagon),task.stage,p.quantity,receipt)
    if not valid then fail(n,p,r,reason,receipt);return true end
    p.phase=task.stage=="loaded" and "shipping" or "last_mile"
  elseif task.kind=="last_mile" then
    SocialJobs.complete(n,task.social_pending,receipt)
    local request=Cooperation.find(storage.society,p.request_id)
    if not request or request.status~="delivered" or request.delivered_count~=p.quantity then fail(n,p,r,"rail_delivery_credit_mismatch",receipt);return true end
    p.completed_shipments=(p.completed_shipments or #(p.deliveries or {}))+1
    p.status="complete";p.completed_tick=game.tick;p.phase="delivered";r.status="complete"
    p.result={native_delivery_count=request.delivered_count,request_id=request.id,physical_last_mile_receipt=receipt.id,
      grants=0,manual_boundary_loading=true,continuous_boundary_factory=false}
  end
  return true
end
function RailTrade.observe(n)
  local p,r=current(n);if not p then return nil end
  return {id=p.id,route_id=p.route_id,status=p.status,phase=p.phase,reason=p.reason,request_id=p.request_id,
    accepted_quantity=p.quantity,shipment_limit=p.plan.quantity,
    completed_shipments=p.completed_shipments or (#(p.deliveries or {})+(p.status=="complete" and 1 or 0)),
    rolling_stage_moves=p.rolling_stage_moves or 0,rolling_build_safety=copy(p.rolling_build_safety or {}),
    started_tick=p.started_tick,infrastructure_completed_tick=p.infrastructure_completed_tick,pending_job=p.pending_job,
    construction_index=p.construction_index,construction_steps=#p.plan.steps,research_index=p.research_index,
    minimum_material_budget=p.plan.materials,research_budget=p.plan.research_budget,actual_receipt_ledger=copy(p.ledger),
    losses=copy(p.losses),result=copy(p.result),route_status=r and r.status,
    manual_boundary_loading=true,continuous_boundary_factory=false,ordinary_production_project_preserved=true}
end
-- Bounded survey of remembered, explicitly permitted partners. Terrain/native
-- placement checks remain authoritative; rejected corridors are not candidates.
function RailTrade.candidates(n)
  if not n.character or not n.character.valid or not n.factory_project or n.factory_project.status~="complete"
      or (n.rail_project and n.rail_project.status=="active") then return {} end
  if n.rail_project and n.rail_project.status~="complete" then return {} end
  local candidates={};local at=n.character.position
  local saved=storage.society and storage.society.actors and storage.society.actors[n.id]
  local known=saved and saved.known or {}
  if n.rail_project and n.rail_project.status=="complete" then
    local p=n.rail_project.plan;local id=p.receiver_id;local b=actor(id);local seen=known[id]
    if b and seen and seen.last_seen_position and game.tick-seen.last_seen_tick<=36000
        and reciprocal(n.force,b.force) and trade_permitted(n,b) then
      return {{id="ground_rail_trade",receiver_id=id,item=p.item,quantity=p.quantity,reused_owned_route=true,
        options={receiver_id=id,x=p.sites.north_stop.position.x-2,source_y=p.sites[p.source_stop_key].position.y,
          destination_y=p.sites[p.destination_stop_key].position.y,item=p.item,quantity=p.quantity}}}
    end
    return {}
  end
  local ids={} for id,seen in pairs(known) do local b=actor(id)
    if id~=n.id and b and seen.last_seen_position and seen.last_seen_tick
        and game.tick-seen.last_seen_tick<=36000 and seen.surface_index==n.character.surface.index
        and reciprocal(n.force,b.force) and trade_permitted(n,b) then ids[#ids+1]=id end
  end;table.sort(ids)
  local even=function(x) return math.floor(x/2)*2 end
  for _,id in ipairs(ids) do local dest=known[id].last_seen_position
    -- First controller supports a vertical, north-to-south isolated corridor.
    -- Both boundary walks stay bounded; distant/off-axis partners wait for a
    -- separate native curved-route qualification.
    if dest.y>=at.y+48 and dest.y-at.y<=512 and math.abs(dest.x-at.x)<=96 then
      for _,offset in ipairs({16,-16,32,-32}) do
        local options={receiver_id=id,x=even(at.x+offset),source_y=even(at.y),
          destination_y=even(math.max(at.y+64,dest.y)),item="iron-plate",quantity=20}
        local plan=RailTrade.plan(n,options)
        if plan then candidates[#candidates+1]={id="ground_rail_trade",options=options,receiver_id=id,
          quantity=20,item=options.item,minimum_material_budget=plan.materials};break end
      end
    end
    if #candidates>=2 then break end
  end
  return candidates
end
return RailTrade
