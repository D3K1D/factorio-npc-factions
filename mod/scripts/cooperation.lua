-- Saved demand contracts. A contract reserves a supplier's decision, never
-- moves items: SocialJobs and Worker perform the only physical transfer.
local Cooperation={}
local Hands=require("scripts.hands")
local LIFETIME=60*60*3
local PROJECT_REQUEST_LIFETIME_TICKS=24000
Cooperation.PROJECT_REQUEST_LIFETIME_TICKS=PROJECT_REQUEST_LIFETIME_TICKS
local MAX_REQUESTS=128
local WALK_LIMIT=128
local ROUTE_COOLDOWN=60*60*10
local MAX_FAILED_ROUTES=64

local function distance(a,b)
  local dx,dy=a.x-b.x,a.y-b.y
  return math.sqrt(dx*dx+dy*dy)
end

local function stock(npc,item)
  local inv=npc.character.get_main_inventory()
  return inv and inv.valid and inv.get_item_count{name=item,quality="normal"} or 0
end

local function live(id)
  local npc=storage.npcs and storage.npcs[id]
  return npc and npc.character and npc.character.valid and npc.force and npc.force.valid
      and npc.character.force==npc.force and npc or nil
end

local function request_list(s)
  s.requests=s.requests or {}
  s.next_request=s.next_request or 1
  return s.requests
end

local function reserve(item)
  return ({coal=4,["iron-plate"]=8,["copper-plate"]=4,
    ["firearm-magazine"]=3,["automation-science-pack"]=0})[item] or 0
end

-- Protect the next few concrete factory actions, not the entire future plan.
-- This is character stock only: a site inventory is not available to trade.
local function project_reserve(npc,item)
  local project=npc.factory_project
  local plan=project and project.plan
  if not project or project.status~="active" or not plan or not plan.steps then return 0 end
  local needed=0
  for index=project.index or 1,math.min(#plan.steps,(project.index or 1)+2) do
    local step=plan.steps[index]
    local count=math.max(0,math.min(100,math.floor(step.count or 1)))
    if step.kind=="wait" and step.condition and step.condition.kind=="inventory"
        and not step.condition.target_site and step.condition.item==item then
      needed=math.max(needed,math.max(0,math.min(100,math.floor(step.condition.count or 1))))
    elseif step.kind=="craft" and type(step.recipe)=="string" then
      local recipe=npc.force.recipes[step.recipe]
      if recipe and recipe.enabled then
        for _,ingredient in pairs(recipe.ingredients or {}) do
          if ingredient.type=="item" and ingredient.name==item then
            needed=needed+math.max(0,math.min(100,math.ceil((ingredient.amount or 0)*count)))
          end
        end
      end
    elseif step.kind=="build" then
      local site=step.target_site and plan.sites and plan.sites[step.target_site]
      if (step.item or site and (site.item or site.name))==item then needed=needed+1 end
    elseif step.kind=="transfer" and step.mode=="deposit" and step.item==item then
      needed=needed+count
    end
  end
  return math.min(100,needed)
end

-- A designed engineer keeps what the stage it is building still needs to place.
local function design_reserve(npc,item)
  local d=npc.design
  if not (d and d.status=="building" and d.plan and d.plan.stages) then return 0 end
  local stage=d.plan.stages[d.stage_index or 1]
  local needed=0
  for _,id in ipairs(stage and stage.entity_ids or {}) do
    local e=d.plan.entities[id]
    if e and e.item==item and not (e.entity and e.entity.valid) then needed=needed+1 end
  end
  return math.min(100,needed)
end

local function own_site_chest(s,npc)
  local actor=s.actors and s.actors[npc.id]
  local coalition=actor and s.coalitions and s.coalitions[actor.coalition_id]
  local site=coalition and coalition.mode=="shared" and coalition.shared_site
  local node=site and site.nodes and site.nodes[npc.id]
  local chest=node and node.entity
  if chest and chest.valid and chest.force==npc.force
      and chest.surface==npc.character.surface and chest.name=="wooden-chest" then
    return chest
  end
end

local function chest_stock(s,npc,item)
  local chest=own_site_chest(s,npc)
  local inv=chest and chest.get_inventory(defines.inventory.chest)
  return chest,inv and inv.valid and inv.get_item_count{name=item,quality="normal"} or 0
end

function Cooperation.capacity(npc,item,s)
  local carried=math.max(0,stock(npc,item)-math.max(reserve(item),project_reserve(npc,item)+design_reserve(npc,item)))
  if not s then return carried end
  local _,stored=chest_stock(s,npc,item)
  return carried+stored
end

local function failed_routes(s)
  s.delivery_route_failures=s.delivery_route_failures or {}
  return s.delivery_route_failures
end

function Cooperation.walking_route(s,supplier,target)
  if not supplier or not target or not supplier.character or not target.character
      or not supplier.character.valid or not target.character.valid
      or supplier.character.surface~=target.character.surface then
    return false,"delivery_surface_mismatch"
  end
  if distance(supplier.character.position,target.character.position)>WALK_LIMIT then
    return false,"walking_delivery_too_far"
  end
  local key=supplier.id..":"..target.id..":"..supplier.character.surface.index
  local failed=s and (s.delivery_route_failures or {})[key]
  if failed and game.tick<failed.expires_tick
      and distance(supplier.character.position,failed.source_position)<32
      and distance(target.character.position,failed.destination_position)<32 then
    return false,"walking_route_cooldown"
  end
  return true
end

function Cooperation.remember_route_failure(s,supplier,target,reason,request)
  if reason~="no_path" or not s or not supplier or not target
      or not supplier.character or not target.character or not supplier.character.valid
      or not target.character.valid then return false end
  local routes=failed_routes(s)
  local key=supplier.id..":"..target.id..":"..supplier.character.surface.index
  local source=request and request.source_position or supplier.character.position
  routes[key]={supplier_id=supplier.id,requester_id=target.id,
    surface_index=supplier.character.surface.index,
    source_position={x=source.x,y=source.y},
    destination_position={x=target.character.position.x,y=target.character.position.y},
    failed_tick=game.tick,expires_tick=game.tick+ROUTE_COOLDOWN,reason=reason}
  local count,oldest,oldest_tick=0,nil,nil
  for route_key,entry in pairs(routes) do
    if entry.expires_tick<=game.tick then routes[route_key]=nil
    else
      count=count+1
      if not oldest_tick or entry.failed_tick<oldest_tick
          or entry.failed_tick==oldest_tick and route_key<oldest then
        oldest,oldest_tick=route_key,entry.failed_tick
      end
    end
  end
  if count>MAX_FAILED_ROUTES and oldest then routes[oldest]=nil end
  return true
end

-- A finished owned route can solicit its exact shipment beyond walking range.
-- Only RailTrade may claim that accepted request; ordinary SocialJobs cannot
-- walk it until the train has reached ready_last_mile.
local function rail_ready(supplier,target,request)
  local project=supplier.rail_project
  local board=storage.rail_trade
  local route=project and board and board.routes and board.routes[project.route_id]
  return project and project.status=="active" and project.phase=="fresh_contract"
      and project.infrastructure_completed_tick and route and route.status=="ready"
      and route.carrier_id==supplier.id and route.receiver_id==target.id
      and project.plan and project.plan.item==request.item
end

function Cooperation.delivery_route(s,supplier,target,request)
  local walk,reason=Cooperation.walking_route(s,supplier,target)
  if walk then return "walk" end
  if request and rail_ready(supplier,target,request) then return "rail" end
  return nil,reason
end

local function active_for(s,requester_id,item)
  for _,request in ipairs(request_list(s)) do
    if request.requester_id==requester_id and request.item==item
        and (request.status=="open" or request.status=="accepted") then return request end
  end
end

function Cooperation.project_valid(request,requester)
  local scope=request and request.project
  if not scope then return true end
  local project=requester and requester.factory_project
  if not project or project.status~="active" or not project.plan
      or project.id~=scope.id or project.plan.revision~=scope.revision
      or (project.replanned_tick or project.started_tick)~=scope.generation_tick
      or project.index~=scope.index then return false end
  local step=project.plan.steps and project.plan.steps[scope.index]
  local site=step and step.target_site and project.plan.sites
      and project.plan.sites[step.target_site]
  return step and step.kind==scope.kind
      and ((step.kind=="craft" and step.recipe==scope.item)
        or (step.kind=="build" and (step.item or site and (site.item or site.name))==scope.item)
        or (step.kind=="transfer" and step.item==scope.item and step.mode=="deposit"))
end

local function project_permission(s,supplier,request,target)
  if not request.project then return true end
  if not Cooperation.project_valid(request,target) then return false end
  local supplier_actor=s.actors and s.actors[supplier.id]
  local target_actor=s.actors and s.actors[target.id]
  local coalition=supplier_actor and target_actor and s.coalitions
      and s.coalitions[supplier_actor.coalition_id]
  return coalition and supplier_actor.coalition_id==target_actor.coalition_id
      and coalition.members[supplier.id] and coalition.members[target.id]
      and coalition.roles[supplier.id]==request.specialist_role
      and supplier.force.get_friend(target.force) and target.force.get_friend(supplier.force)
      and supplier.force.get_cease_fire(target.force) and target.force.get_cease_fire(supplier.force)
end

function Cooperation.project_allowed(s,supplier,request,target)
  return s and supplier and request and target
      and project_permission(s,supplier,request,target) or false
end

function Cooperation.refresh(s,npc,demands)
  request_list(s)
  local desired={}
  for _,demand in ipairs(demands) do desired[demand.item]=demand end
  for _,request in ipairs(s.requests) do
    if request.requester_id==npc.id and request.status=="accepted"
        and request.project and not Cooperation.project_valid(request,npc) then
      request.status="withdrawn";request.resolved_tick=game.tick
    elseif request.requester_id==npc.id and request.status=="open" then
      local demand=desired[request.item]
      local amount=demand and math.max(0,math.floor(demand.count or 0)) or 0
      if amount<1 then
        request.status="withdrawn";request.resolved_tick=game.tick
      else
        local old=request.project
        local fresh=demand.project
        if (old and not fresh) or (fresh and (not old or old.id~=fresh.id
            or old.revision~=fresh.revision or old.generation_tick~=fresh.generation_tick
            or old.index~=fresh.index)) then
          request.specialist_wait_started_tick=nil
        end
        request.count=math.min(20,amount);request.remaining=request.count
        request.purpose=demand.purpose or "stock"
        request.project=demand.project
        request.specialist_role=demand.specialist_role
      end
    end
  end
  for _,demand in ipairs(demands) do
    local amount=math.min(20,math.max(0,math.floor(demand.count or 0)))
    if amount>0 and not active_for(s,npc.id,demand.item) then
      local lifetime=demand.purpose=="coalition_project" and demand.project
          and PROJECT_REQUEST_LIFETIME_TICKS or LIFETIME
      local request={id=s.next_request,requester_id=npc.id,item=demand.item,count=amount,
        remaining=amount,created_tick=game.tick,expires_tick=game.tick+lifetime,
        status="open",source_kind="supplier_character_main",
        destination_kind="requester_character_main",purpose=demand.purpose or "stock",
        project=demand.project,specialist_role=demand.specialist_role}
      s.next_request=s.next_request+1
      s.requests[#s.requests+1]=request
    end
  end
  while #s.requests>MAX_REQUESTS do
    local drop
    for i,request in ipairs(s.requests) do
      if request.status~="accepted" and request.status~="open" then drop=i;break end
    end
    if not drop then break end
    table.remove(s.requests,drop)
  end
end

function Cooperation.find(s,id)
  for _,request in ipairs(request_list(s)) do if request.id==id then return request end end
end

function Cooperation.available(s,supplier,requester_id,allowed)
  if not allowed then return {} end
  local target=live(requester_id)
  if not target or target.character.surface~=supplier.character.surface then return {} end
  local out={}
  for _,request in ipairs(s.requests or {}) do
    if request.requester_id==requester_id and request.status=="open"
        and request.expires_tick>game.tick and Cooperation.capacity(supplier,request.item,s)>0
        and project_permission(s,supplier,request,target)
        and Cooperation.delivery_route(s,supplier,target,request) then
      out[#out+1]=request
    end
  end
  table.sort(out,function(a,b) return a.id<b.id end)
  return out
end

function Cooperation.accept(s,request_id,supplier,requester_id,allowed)
  local request=Cooperation.find(s,request_id)
  if not allowed or not request or request.status~="open"
      or request.requester_id~=requester_id or request.expires_tick<=game.tick then
    return nil,"request_not_available"
  end
  local target=live(requester_id)
  if not target or target.character.surface~=supplier.character.surface
      or not project_permission(s,supplier,request,target) then
    return nil,"requester_unreachable"
  end
  local route,reason=Cooperation.delivery_route(s,supplier,target,request)
  if not route then return nil,reason end
  local carried=Cooperation.capacity(supplier,request.item)
  local chest,stored=chest_stock(s,supplier,request.item)
  local source_kind=carried>0 and "supplier_character_main" or "supplier_owned_site_chest"
  local amount=math.min(request.remaining,carried>0 and carried or stored)
  if amount<1 then return nil,"supplier_stock_reserved" end
  request.status="accepted";request.supplier_id=supplier.id
  request.accepted_tick=game.tick;request.accepted_count=amount
  request.source_kind=source_kind
  request.source_entity=source_kind=="supplier_owned_site_chest" and chest or nil
  request.source_force=supplier.force.name;request.destination_force=target.force.name
  request.source_surface_index=supplier.character.surface.index
  request.destination_surface_index=target.character.surface.index
  local source_position=source_kind=="supplier_owned_site_chest" and chest.position
      or supplier.character.position
  request.source_position={x=source_position.x,y=source_position.y}
  request.destination_position={x=target.character.position.x,y=target.character.position.y}
  request.delivery_mode=route
  return request
end

function Cooperation.valid_delivery(s,intent,supplier,target,allowed)
  local request=Cooperation.find(s,intent.request_id)
  if request and request.transport and request.transport.status~="ready_last_mile" then
    return nil,"rail_transport_in_progress"
  end
  if not request or request.status~="accepted" or request.expires_tick<=game.tick
      or request.supplier_id~=supplier.id or request.requester_id~=target.id
      or not allowed or supplier.character.surface~=target.character.surface
      or request.source_force~=supplier.force.name or request.destination_force~=target.force.name
      or request.source_surface_index~=supplier.character.surface.index
      or request.destination_surface_index~=target.character.surface.index
      or not project_permission(s,supplier,request,target)
      or intent.item~=request.item or intent.count~=request.accepted_count then
    return nil,"contract_invalid_or_stock_reserved"
  end
  if request.source_kind=="supplier_owned_site_chest" and intent.stage~="deliver" then
    local chest,stored=chest_stock(s,supplier,intent.item)
    if chest~=request.source_entity or stored<intent.count then
      return nil,"contract_source_chest_missing_or_empty"
    end
  elseif ((intent.stage=="deliver" or request.transport) and stock(supplier,intent.item)<intent.count)
      or (intent.stage~="deliver" and not request.transport
        and Cooperation.capacity(supplier,intent.item)<intent.count) then
    return nil,"contract_supplier_stock_reserved"
  end
  return request
end

local function rail_treaty(supplier,request)
  local target=live(request.requester_id)
  local s=storage.society
  local pair=supplier.id<request.requester_id
      and supplier.id..":"..request.requester_id
      or request.requester_id..":"..supplier.id
  local relation=s and s.relations and s.relations[pair]
  local coalition=relation and relation.coalition_id and s.coalitions
      and s.coalitions[relation.coalition_id]
  local explicit=relation and (relation.trade==true or coalition
      and coalition.members[supplier.id] and coalition.members[request.requester_id])
  return target and target.character.surface==supplier.character.surface
      and explicit
      and request.source_surface_index==supplier.character.surface.index
      and request.destination_surface_index==target.character.surface.index
      and target.force.name==request.destination_force
      and supplier.force.name==request.source_force
      and supplier.force.get_friend(target.force) and target.force.get_friend(supplier.force)
      and supplier.force.get_cease_fire(target.force)
      and target.force.get_cease_fire(supplier.force)
end

-- Rail ownership and every stage stay bound to one accepted request. The
-- carrier is the supplier's own force; no foreign wagon inventory is opened.
function Cooperation.claim_transport(request_id,supplier,route_id,wagon)
  local s=storage.society
  local request=s and Cooperation.find(s,request_id)
  if not request or request.status~="accepted" or request.expires_tick<=game.tick
      or request.supplier_id~=supplier.id or request.source_kind~="supplier_character_main"
      or request.transport or request.worker_job_id or not rail_treaty(supplier,request)
      or type(route_id)~="string" or #route_id<1 or #route_id>128
      or not wagon or not wagon.valid or wagon.type~="cargo-wagon"
      or wagon.force~=supplier.force or wagon.surface~=supplier.character.surface
      or not wagon.unit_number
      or wagon.get_inventory(defines.inventory.cargo_wagon).get_item_count()~=0
      or stock(supplier,request.item)<request.accepted_count then
    return nil,"rail_contract_unavailable"
  end
  request.transport={route_id=route_id,carrier_id=supplier.id,
    carrier_force=supplier.force.name,wagon_unit_number=wagon.unit_number,
    wagon=wagon,status="claimed",count=request.accepted_count,
    item=request.item,quality="normal",claimed_tick=game.tick,
    expires_tick=request.expires_tick,receipt_ids={}}
  return request.transport
end

function Cooperation.transport_valid(request_id,supplier,route_id,wagon)
  local s=storage.society
  local request=s and Cooperation.find(s,request_id)
  local transport=request and request.transport
  if not request or not transport or request.status~="accepted"
      or request.expires_tick<=game.tick or transport.expires_tick<=game.tick
      or request.supplier_id~=supplier.id or transport.carrier_id~=supplier.id
      or transport.route_id~=route_id or transport.carrier_force~=supplier.force.name
      or transport.count~=request.accepted_count or transport.item~=request.item
      or transport.status=="failed" or transport.status=="expired"
      or transport.status=="ready_last_mile"
      or not wagon or not wagon.valid or wagon.unit_number~=transport.wagon_unit_number
      or wagon~=transport.wagon or wagon.force~=supplier.force
      or wagon.surface~=supplier.character.surface
      or not rail_treaty(supplier,request) then
    return false,"rail_contract_invalid"
  end
  return true,transport
end

local function receipt_unique(transport,receipt)
  if not receipt or not receipt.id or transport.receipt_ids[receipt.id]
      or receipt.status~="complete" then return false end
  transport.receipt_ids[receipt.id]=true
  return true
end

function Cooperation.transport_stage(request_id,supplier,route_id,wagon,stage,actual_count,receipt)
  local valid,transport=Cooperation.transport_valid(request_id,supplier,route_id,wagon)
  if not valid then return false,transport end
  local request=Cooperation.find(storage.society,request_id)
  if not receipt_unique(transport,receipt) then return false,"rail_receipt_duplicate_or_failed" end
  local amount=request.accepted_count
  if actual_count~=amount or type(actual_count)~="number" then
    transport.receipt_ids[receipt.id]=nil
    return false,"rail_quantity_mismatch"
  end
  if stage=="loaded" and transport.status=="claimed" then
    local cargo=wagon.get_inventory(defines.inventory.cargo_wagon)
    if receipt.kind~="transfer" or receipt.target_unit_number~=wagon.unit_number
        or receipt.mode~="deposit" or receipt.item~=request.item
        or receipt.count~=amount or cargo.get_item_count()~=amount
        or cargo.get_item_count{name=request.item,quality="normal"}~=amount then
      transport.receipt_ids[receipt.id]=nil;return false,"rail_load_receipt_mismatch"
    end
    transport.status="loaded";transport.load_receipt_id=receipt.id
    transport.loaded_tick=game.tick
    return true,transport
  end
  if stage=="traveled" and transport.status=="loaded" then
    if receipt.kind~="rail_move" or receipt.route_id~=route_id
        or not receipt.journey_id or receipt.cargo_loss_unknown
        or not receipt.train_id or not receipt.departure or not receipt.arrival
        or receipt.departure.train_id~=receipt.train_id
        or receipt.arrival.train_id~=receipt.train_id
        or not receipt.dispatch_tick or not receipt.departure_tick
        or not receipt.arrival_tick or receipt.departure_tick<receipt.dispatch_tick
        or receipt.arrival_tick<receipt.departure_tick
        or not receipt.arrival.station_unit_number
        or receipt.arrival.station_unit_number==receipt.departure.station_unit_number
        or not receipt.cargo_loss or next(receipt.cargo_loss)
        or not receipt.cargo_gain or next(receipt.cargo_gain) then
      transport.receipt_ids[receipt.id]=nil;return false,"rail_travel_receipt_mismatch"
    end
    local key=request.item.."@normal"
    local before=receipt.departure and receipt.departure.cargo
    local after=receipt.arrival and receipt.arrival.cargo
    if not before or not after or before[key]~=amount or after[key]~=amount then
      transport.receipt_ids[receipt.id]=nil;return false,"rail_cargo_not_conserved"
    end
    transport.status="arrived";transport.journey_id=receipt.journey_id
    transport.travel_receipt_id=receipt.id;transport.arrived_tick=game.tick
    return true,transport
  end
  if stage=="unloaded" and transport.status=="arrived" then
    if receipt.kind~="transfer" or receipt.target_unit_number~=wagon.unit_number
        or receipt.mode~="withdraw"
        or receipt.item~=request.item or receipt.count~=amount
        or stock(supplier,request.item)<amount
        or wagon.get_inventory(defines.inventory.cargo_wagon).get_item_count()~=0 then
      transport.receipt_ids[receipt.id]=nil;return false,"rail_unload_receipt_mismatch"
    end
    transport.status="ready_last_mile";transport.unload_receipt_id=receipt.id
    transport.unloaded_tick=game.tick
    return true,transport
  end
  transport.receipt_ids[receipt.id]=nil
  return false,"rail_stage_order_invalid"
end

function Cooperation.release_transport(request_id,supplier,route_id,wagon,reason)
  local s=storage.society
  local request=s and Cooperation.find(s,request_id)
  local transport=request and request.transport
  if not request or not transport or request.supplier_id~=supplier.id
      or transport.carrier_id~=supplier.id or transport.route_id~=route_id
      or transport.carrier_force~=supplier.force.name
      or (wagon and wagon~=transport.wagon)
      or (not wagon and transport.wagon and transport.wagon.valid)
      or transport.status=="delivered" or transport.status=="failed" then
    return false,"rail_release_identity_mismatch"
  end
  if transport.status=="expired" then
    transport.failure_reason=transport.failure_reason or reason or "contract_expired"
    return true,transport
  end
  local expired=request.expires_tick<=game.tick or request.status=="expired"
  transport.status=expired and "expired" or "failed"
  transport.failure_reason=reason or "rail_route_failed"
  transport.resolved_tick=game.tick
  request.status=expired and "expired" or "failed"
  request.failure_reason=transport.failure_reason
  request.resolved_tick=game.tick
  return true,transport
end

function Cooperation.collection_job(request)
  if request.source_kind~="supplier_owned_site_chest" then return nil end
  local chest=request.source_entity
  if not chest or not chest.valid then return nil end
  return {kind="transfer",target=chest,item=request.item,count=request.accepted_count,
    mode="withdraw",inventory=defines.inventory.chest}
end

-- Each member physically builds a small owned depot at the proposed common site.
-- Native Worker mine/craft/build receipts drive every transition. Cross-force
-- chest access remains prohibited; suppliers can later haul from their own depot.
function Cooperation.shared_site_job(s,npc)
  local actor=s.actors and s.actors[npc.id]
  local coalition=actor and s.coalitions and s.coalitions[actor.coalition_id]
  if not coalition or coalition.mode~="shared" or not coalition.members[npc.id] then return nil end
  local site=coalition.shared_site
  if not site or site.surface_index~=npc.character.surface.index then return nil end
  site.nodes=site.nodes or {}
  local node=site.nodes[npc.id]
  if not node then
    node={status="planned",position={x=site.x+3*npc.id,y=site.y}}
    site.nodes[npc.id]=node
  end
  local owned_chest=own_site_chest(s,npc)
  if owned_chest then
    node.status="complete"
    if game.tick<(node.retry_tick or 0) then return nil end
    if actor.role=="hauler" or actor.role=="merchant" then
      for _,entry in ipairs({{"iron-plate",20},{"coal",12},{"copper-plate",10}}) do
        local surplus=stock(npc,entry[1])-entry[2]
        if surplus>=4 then
          local amount=math.min(8,surplus)
          return {kind="transfer",target=owned_chest,item=entry[1],count=amount,
            mode="deposit",inventory=defines.inventory.chest},
            {coalition_id=coalition.id,actor_id=npc.id,stage="stage",item=entry[1]}
        end
      end
    end
    return nil
  end
  if node.status=="complete" then node.status="planned" end
  if game.tick<(node.retry_tick or 0) then return nil end
  if not node.position_checked then
    node.free_from=node.free_from or {x=node.position.x,y=node.position.y}   -- (where to look again from)
    local position=Hands.free_spot(npc,npc.character.surface,"wooden-chest",node.free_from,16,npc.force)   -- (W-25)
    if not position then node.status="blocked";node.retry_tick=game.tick+18000;return nil end
    node.position={x=position.x,y=position.y};node.position_checked=true
  end
  local kind,job
  if stock(npc,"wooden-chest")>0 then
    kind="build";job={kind="build",entity_name="wooden-chest",item="wooden-chest",
      position=node.position}
  elseif stock(npc,"wood")>=2 then
    kind="craft";job={kind="craft",recipe="wooden-chest",count=1}
  else
    kind="mine";job={kind="mine",item="wood",count=2-stock(npc,"wood")}
  end
  site.status="building";node.status=kind
  return job,{coalition_id=coalition.id,actor_id=npc.id,stage=kind}
end

function Cooperation.shared_site_receipt(s,npc,task,receipt)
  local coalition=s.coalitions and s.coalitions[task.coalition_id]
  local site=coalition and coalition.shared_site
  local node=site and site.nodes and site.nodes[npc.id]
  if not node then return false end
  if task.stage=="stage" then
    if receipt.status=="complete" then
      node.staged=node.staged or {}
      node.staged[task.item]=(node.staged[task.item] or 0)+(receipt.count or 0)
      node.last_stage_tick=game.tick
    else node.last_failure=receipt.reason;node.retry_tick=game.tick+18000 end
    return true
  end
  if receipt.status~="complete" then
    node.status="blocked";node.last_failure=receipt.reason
    node.retry_tick=game.tick+18000
    -- (its chest refused there: a spot looked for afresh next time, the refused one passed over; Codex, W-25)
    if task.stage=="build" then node.position_checked=nil end
    return true
  end
  if task.stage=="build" then
    local chest=receipt.entity
    if not chest or not chest.valid or chest.force~=npc.force
        or chest.name~="wooden-chest" then
      node.status="blocked";node.last_failure="unverified_depot_build"
      node.retry_tick=game.tick+18000;return true
    end
    node.entity=chest;node.status="complete";node.completed_tick=game.tick
    local complete=true
    for id in pairs(coalition.members) do
      if not site.nodes[id] or site.nodes[id].status~="complete" then complete=false;break end
    end
    if complete then site.status="active";site.completed_tick=game.tick end
  else node.status="planned" end
  return true
end

function Cooperation.receipt(s,intent,receipt)
  local request=Cooperation.find(s,intent.request_id)
  if not request or (request.status~="accepted" and request.status~="expired")
      or request.supplier_id~=intent.actor_id or request.receipt_id then
    return nil,"contract_receipt_mismatch"
  end
  local moved=math.max(0,math.min(request.accepted_count or 0,receipt.count or 0))
  request.delivered_count=moved
  request.receipt_id=receipt.id;request.worker_job_id=receipt.job_id
  request.resolved_tick=game.tick
  request.status=receipt.status=="complete" and moved==request.accepted_count
      and "delivered" or moved>0 and "partial" or "failed"
  request.failure_reason=receipt.status~="complete" and receipt.reason or nil
  if request.transport then
    request.transport.status=request.status
    request.transport.last_mile_receipt_id=receipt.id
    request.transport.resolved_tick=game.tick
  end
  return request
end

function Cooperation.expire(s,tick)
  for _,request in ipairs(request_list(s)) do
    if (request.status=="open" or request.status=="accepted") and tick>=request.expires_tick then
      request.status="expired";request.resolved_tick=tick
      if request.transport then
        request.transport.status="expired";request.transport.resolved_tick=tick
        request.transport.failure_reason="contract_expired"
      end
    end
  end
end

return Cooperation
