-- Receipt-bound physical upkeep of the saved copper plate conveyor. This
-- module never creates, removes or moves items/entities through scripting.
local Recovery={}
local Construction=require('scripts.construction_planner')
local RETRY=1800
local function xy(p) return p and {x=p.x or p[1],y=p.y or p[2]} end
local function same(a,b)
  return a and b and math.abs(a.x-b.x)<.06 and math.abs(a.y-b.y)<.06
end
local function owned(npc,e)
  return e and e.valid and e.force==npc.force and e.surface==npc.character.surface
end
local function scope(npc,key,resolve_site)
  local p=npc.factory_project
  local s=p and p.plan and p.plan.sites and p.plan.sites[key]
  if not s or s.surface_index~=npc.character.surface.index then return nil end
  local e=resolve_site(npc,key)
  if not owned(npc,e) or e.name~=s.name or not same(e.position,xy(s.position)) then return nil end
  if s.unit_number and e.unit_number~=s.unit_number then return nil end
  if s.existing_unit_number and e.unit_number~=s.existing_unit_number then return nil end
  return e,s
end
local function count_belt(e,item)
  local n=0
  for lane=1,2 do n=n+e.get_transport_line(lane).get_item_count{name=item,quality='normal'} end
  return n
end
local function ordered(npc,resolve_site)
  local list={}
  for key in pairs(npc.factory_project.plan.sites or {}) do
    local index=tonumber(key:match('^copper_plate_belt(%d+)$'))
    if index then list[#list+1]={key=key,index=index} end
  end
  table.sort(list,function(a,b) return a.index<b.index end)
  if #list<2 then return nil,'copper_plate_route_missing' end
  for i,row in ipairs(list) do
    if row.index~=i then return nil,'copper_plate_route_not_contiguous' end
    row.entity,row.site=scope(npc,row.key,resolve_site)
    if not row.entity or row.entity.type~='transport-belt' then
      return nil,'copper_plate_route_scope_revoked'
    end
  end
  return list
end
local function vector(direction)
  if direction==defines.direction.north then return 0,-1
  elseif direction==defines.direction.east then return 1,0
  elseif direction==defines.direction.south then return 0,1
  elseif direction==defines.direction.west then return -1,0 end
end
local function terminal(npc,list,resolve_site)
  local tail,previous=list[#list],list[#list-1]
  local inserter=scope(npc,'science_input_inserter',resolve_site)
  local assembler=scope(npc,'science_assembler',resolve_site)
  if not inserter or inserter.type~='inserter' or not assembler
      or assembler.type~='assembling-machine' then return nil,'science_receiver_scope_revoked' end
  local pickup=inserter.pickup_position
  if not pickup or math.floor(pickup.x)~=math.floor(tail.entity.position.x)
      or math.floor(pickup.y)~=math.floor(tail.entity.position.y)
      or inserter.drop_target~=assembler then return nil,'science_receiver_port_mismatch' end
  local dx,dy=vector(previous.entity.direction)
  if not dx or not same({x=previous.entity.position.x+dx,y=previous.entity.position.y+dy},
      tail.entity.position) then return nil,'previous_belt_not_feeding_terminal' end
  return {tail=tail,previous=previous,inserter=inserter,assembler=assembler,
    incoming=previous.entity.direction}
end
local function pin(e,key)
  return {name=e.name,position=xy(e.position),surface_index=e.surface.index,
    force_name=e.force.name,unit_number=e.unit_number,key=key}
end
local function resolve_pin(npc,s)
  if not s or s.force_name~=npc.force.name or s.surface_index~=npc.character.surface.index then return nil end
  for _,e in ipairs(npc.character.surface.find_entities_filtered{
      name=s.name,position=s.position,radius=.05,force=npc.force}) do
    if owned(npc,e) and e.unit_number==s.unit_number and same(e.position,s.position) then return e end
  end
end
-- Survey only: shared route identity and native placement, without reserving
-- resources or altering the saved project. The producer assets remain in place.
function Recovery.separation_preview(npc,resolve_site)
  local list=ordered(npc,resolve_site)
  if not list then return nil end
  local drill=scope(npc,'electric_drill',resolve_site)
  local first=scope(npc,'electric_belt1',resolve_site)
  local shared=scope(npc,'electric_belt2',resolve_site)
  local output=scope(npc,'electric_output',resolve_site)
  local buffer=scope(npc,'electric_buffer',resolve_site)
  local pole=scope(npc,'electric_pole',resolve_site)
  if not drill or not first or not shared or not output or not buffer or not pole then return nil end
  local alias
  for _,row in ipairs(list) do if row.entity==shared then alias=row.key;break end end
  if not alias then return nil end
  local drop=drill.drop_position
  if drill.type~='mining-drill' or first.type~='transport-belt' or output.type~='inserter'
      or math.floor(drop.x)~=math.floor(first.position.x) or math.floor(drop.y)~=math.floor(first.position.y)
      or first.direction~=defines.direction.north or not same({x=first.position.x,y=first.position.y-1},shared.position)
      or output.pickup_target~=shared or output.drop_target~=buffer then return nil,'shared_electric_port_scope_changed' end
  local x,y=first.position.x,first.position.y
  local specs={buffer={name='wooden-chest',item='wooden-chest',position={x=x-2,y=y},direction=0},
    inserter={name='inserter',item='inserter',position={x=x-1,y=y},direction=defines.direction.east},
    pole={name='small-electric-pole',item='small-electric-pole',position={x=x-2,y=y+1},direction=0}}
  for _,key in ipairs({'buffer','inserter','pole'}) do
    local s=specs[key]
    if not npc.character.surface.can_place_entity{name=s.name,position=s.position,direction=s.direction,
        force=npc.force,build_check_type=defines.build_check_type.manual} then return nil,'separation_site_blocked:'..key end
  end
  local dx,dy=specs.pole.position.x-pole.position.x,specs.pole.position.y-pole.position.y
  if not pole.electric_network_id or dx*dx+dy*dy>7.5*7.5 then return nil,'separation_power_anchor_unavailable' end
  -- The old inserter will face east, with its pickup empty and its drop
  -- empty or occupied only by an owned pole. It cannot
  -- withdraw the preserved mixed chest or continue stealing copper plates.
  for _,side in ipairs({-1,1}) do
    local pos={x=output.position.x+side,y=output.position.y}
    local matches=npc.character.surface.find_entities_filtered{area={{pos.x-.49,pos.y-.49},{pos.x+.49,pos.y+.49}}}
    for _,e in ipairs(matches) do
      local harmless_drop=side==-1 and e.type=='electric-pole' and owned(npc,e)
      if e.type~='character' and e.type~='resource' and not harmless_drop then
        return nil,'separation_idle_port_occupied:'..e.name..':'..e.position.x..':'..e.position.y
      end
    end
  end
  return {revision='electric-copper-separation-v1',alias=alias,specs=specs,
    drill=pin(drill,'electric_drill'),first=pin(first,'electric_belt1'),shared=pin(shared,alias),
    output=pin(output,'electric_output'),old_buffer=pin(buffer,'electric_buffer'),anchor=pin(pole,'electric_pole'),
    original_first_direction=first.direction,original_output_direction=output.direction}
end
local function issue(npc,state,job,purpose,key,tick,extra)
  state.sequence=(state.sequence or 0)+1
  state.issued={sequence=state.sequence,kind=job.kind,item=job.item,count=job.count,
    purpose=purpose,target=job.target and pin(job.target,key),tick=tick,
    project_id=npc.factory_project.id,project_index=npc.factory_project.index}
  for k,v in pairs(extra or {}) do state.issued[k]=v end
  job.upkeep_source='conveyor_recovery'
  state.reason=nil
  return job
end
local function stock(npc,item) return npc.character.get_main_inventory().get_item_count{name=item,quality='normal'} end
local function separation_scope(npc,s)
  for _,key in ipairs({'drill','first','shared','output','old_buffer','anchor'}) do
    if not resolve_pin(npc,s.plan[key]) then return false end
  end
  return true
end
function Recovery.resolve_buffer(npc)
  local s=npc.conveyor_recovery and npc.conveyor_recovery.separation
  if not s or s.status~='ready' or not separation_scope(npc,s) then return nil end
  for _,key in ipairs({'buffer','inserter','pole'}) do
    local funding=s.build_receipts and s.build_receipts[key]
    local receipt=funding and npc.receipts and npc.receipts[funding.job_id]
    local paid=s.paid and s.paid[key]
    if not funding or not receipt or not paid or receipt.id~=funding.id or receipt.kind~='build'
        or receipt.status~='complete' or receipt.item~=funding.item
        or (receipt.consumed or {})[funding.item]~=1 or funding.consumed~=1
        or funding.unit_number~=paid.unit_number or not owned(npc,receipt.entity)
        or receipt.entity.unit_number~=paid.unit_number then return nil end
  end
  for _,key in ipairs({'first','output'}) do
    local bound=s.configured and s.configured[key]
    local receipt=bound and npc.receipts and npc.receipts[bound.job_id]
    if not bound or not receipt or receipt.id~=bound.id or receipt.kind~='configure_direction'
        or receipt.status~='complete' or receipt.target_unit_number~=s.plan[key].unit_number
        or receipt.configured_direction~=bound.direction then return nil end
  end
  local buffer=resolve_pin(npc,s.paid.buffer)
  local inserter=resolve_pin(npc,s.paid.inserter)
  local pole=resolve_pin(npc,s.paid.pole)
  local first=resolve_pin(npc,s.plan.first)
  local output=resolve_pin(npc,s.plan.output)
  if not buffer or not inserter or not pole or not first or not output
      or first.direction~=defines.direction.south or output.direction~=defines.direction.east
      or inserter.direction~=defines.direction.east or inserter.pickup_target~=first
      or inserter.drop_target~=buffer or not pole.electric_network_id
      or inserter.electric_network_id~=pole.electric_network_id then return nil end
  return buffer,s
end
local function separation_next(npc,state,tick,resolve_site)
  local s=state.separation
  if not s then
    local plan,reason=Recovery.separation_preview(npc,resolve_site)
    if not plan then return nil,reason end
    s={plan=plan,status='building',paid={},build_receipts={},failures=0,started_tick=tick}
    state.separation=s;npc.conveyor_recovery=state
  end
  if s.status=='blocked' then return nil end
  if s.status=='ready' then
    if not Recovery.resolve_buffer(npc) then s.status='blocked';s.reason='separation_ready_binding_revoked' end
    return nil,s.reason
  end
  if not separation_scope(npc,s) then s.status='blocked';s.reason='separation_source_scope_revoked';return nil,s.reason end
  for _,key in ipairs({'buffer','inserter','pole'}) do
    local spec=s.plan.specs[key]
    if not s.paid[key] then
      if stock(npc,spec.item)<1 then
        local budget,reason=Construction.craft_plan(npc,spec.item,1)
        if not budget then s.reason=reason;return nil,reason end
        local raw_keys={}for raw in pairs(budget.raw_deficits or {})do raw_keys[#raw_keys+1]=raw end
        table.sort(raw_keys)
        for _,raw in ipairs(raw_keys) do local count=budget.raw_deficits[raw]
          if count>0 and raw~='wood' then s.reason='separation_raw_stock_unavailable:'..raw;return nil,s.reason end
        end
        if (budget.raw_deficits.wood or 0)>0 then
          return issue(npc,state,{kind='mine',item='wood',count=4,search_radius=128},'fund_separation_wood',nil,tick,
            {separation=true})
        end
        local recipe=npc.force.recipes[spec.item]
        local expected={}
        for _,p in ipairs(recipe.products) do if p.type=='item' and p.amount then expected[p.name]=p.amount end end
        return issue(npc,state,{kind='craft',recipe=spec.item,count=1},'craft_separation_'..key,nil,tick,
          {separation=true,recipe=spec.item,expected=expected})
      end
      if #npc.character.surface.find_entities_filtered{name=spec.name,position=spec.position,radius=.05}>0 then
        s.status='blocked';s.reason='separation_unreceipted_site_occupied:'..key;return nil,s.reason
      end
      return issue(npc,state,{kind='build',item=spec.item,entity_name=spec.name,position=spec.position,direction=spec.direction},
        'build_separation_'..key,nil,tick,{separation=true,spec_key=key})
    elseif not resolve_pin(npc,s.paid[key]) then s.status='blocked';s.reason='separation_paid_asset_revoked';return nil,s.reason end
  end
  for _,entry in ipairs({{'first',defines.direction.south},{'output',defines.direction.east}}) do
    local e=resolve_pin(npc,s.plan[entry[1]])
    if not s.configured then s.configured={} end
    if not s.configured[entry[1]] then
      if entry[1]=='output' and e.held_stack.valid_for_read then return nil,'separation_wait_empty_output_hand' end
      return issue(npc,state,{kind='configure_direction',target=e,direction=entry[2]},
        'separate_electric_'..entry[1],s.plan[entry[1]].key,tick,
        {separation=true,config_key=entry[1],direction=entry[2],previous_direction=e.direction})
    end
  end
  s.status='ready'
  if not Recovery.resolve_buffer(npc) then s.status='blocked';s.reason='separation_native_port_or_power_mismatch';return nil,s.reason end
  s.ready_tick=tick;s.reason=nil
end
function Recovery.next(npc,tick,resolve_site)
  if not npc or not npc.character or not npc.character.valid or npc.force~=npc.character.force
      or not npc.factory_project or not npc.factory_project.plan or type(resolve_site)~='function' then return nil end
  local state=npc.conveyor_recovery
  if state and state.issued then return nil,'conveyor_job_pending' end
  if state and tick<(state.retry_at or 0) then return nil end
  local list,reason=ordered(npc,resolve_site)
  if not list then return nil,reason end
  local port,reason=terminal(npc,list,resolve_site)
  if not port then return nil,reason end
  state=state or {started_tick=tick,sequence=0,ledger={},cleared_iron=0}
  local separation,reason=separation_next(npc,state,tick,resolve_site)
  if separation then return separation end
  if state.separation and state.separation.status~='ready' then return nil,reason end
  local tail=port.tail.entity
  if tail.direction==(port.incoming+8)%16 then
    npc.conveyor_recovery=state
    return issue(npc,state,{kind='configure_direction',target=tail,direction=port.incoming},
      'correct_opposed_copper_terminal',port.tail.key,tick,
      {previous_direction=tail.direction,direction=port.incoming,
       previous=pin(port.previous.entity,port.previous.key),receiver=pin(port.inserter,'science_input_inserter'),
       assembler=pin(port.assembler,'science_assembler'),saved_direction=port.tail.site.direction})
  end
  -- Iron from a mixed native drill footprint cannot be used by this copper
  -- lane. Recover only observed wrong output/route stock into carried inventory.
  local furnace=scope(npc,'copper_furnace',resolve_site)
  if not furnace or furnace.type~='furnace' then return nil,'copper_furnace_scope_revoked' end
  local output=furnace.get_output_inventory()
  local available=output and output.get_item_count{name='iron-plate',quality='normal'} or 0
  local capacity=npc.character.get_main_inventory().get_insertable_count{name='iron-plate',quality='normal'}
  if capacity<=0 then return nil,'iron_cleanup_destination_full' end
  if available>0 then
    npc.conveyor_recovery=state
    return issue(npc,state,{kind='transfer',target=furnace,item='iron-plate',count=math.min(available,8,capacity),
      mode='withdraw',inventory=defines.inventory.crafter_output},'withdraw_wrong_copper_furnace_output',
      'copper_furnace',tick)
  end
  for i=#list,1,-1 do
    local row=list[i]
    for _,item in ipairs({'iron-plate','iron-ore'}) do
    available=count_belt(row.entity,item)
    capacity=npc.character.get_main_inventory().get_insertable_count{name=item,quality='normal'}
    if available>0 and capacity>0 then
      npc.conveyor_recovery=state
      return issue(npc,state,{kind='belt_pickup',target=row.entity,item=item,
        count=math.min(available,8,capacity)},'collect_wrong_copper_conveyor_item',row.key,tick)
    end
    end
  end
  if npc.conveyor_recovery then state.reason=nil;state.last_clear_tick=tick end
end
function Recovery.bind(npc,job_id)
  local issued=npc.conveyor_recovery and npc.conveyor_recovery.issued
  if not issued or issued.job_id or type(job_id)~='string' then return false end
  issued.job_id=job_id;return true
end
function Recovery.reject_unbound(npc,reason)
  local state=npc.conveyor_recovery
  if not state or not state.issued or state.issued.job_id then return false end
  if state.issued.separation and state.separation then
    local s=state.separation;s.failures=(s.failures or 0)+1;s.reason=reason or 'conveyor_enqueue_rejected'
    if s.failures>=3 then s.status='blocked' end
  end
  state.issued=nil;state.reason=reason or 'conveyor_enqueue_rejected';state.retry_at=game.tick+RETRY
  return true
end
function Recovery.on_receipt(npc,receipt)
  local state=npc and npc.conveyor_recovery
  local issued=state and state.issued
  if not issued or not issued.job_id or not receipt or issued.job_id~=receipt.job_id
      or issued.kind~=receipt.kind then return false end
  if issued.separation then
    local s=state.separation;local project=npc.factory_project;local reason
    if receipt.npc_id~=npc.id or receipt.id~=tostring(npc.id)..':'..issued.job_id
        or not receipt.started_tick or receipt.started_tick<issued.tick then reason='separation_receipt_actor_or_time_mismatch'
    elseif not s or not separation_scope(npc,s) or not project or project.id~=issued.project_id
        or project.index~=issued.project_index then reason='separation_saved_scope_changed'
    elseif receipt.status~='complete' then reason=receipt.reason or 'separation_worker_failed'
    elseif issued.kind=='build' then
      local spec=s.plan.specs[issued.spec_key];local e=receipt.entity
      if not owned(npc,e) or e.name~=spec.name or receipt.item~=spec.item or not same(e.position,spec.position)
          or e.direction~=spec.direction or (receipt.consumed or {})[spec.item]~=1 then
        reason='separation_paid_build_receipt_mismatch'
      else
        s.paid[issued.spec_key]=pin(e,'separation_'..issued.spec_key)
        s.build_receipts[issued.spec_key]={id=receipt.id,job_id=receipt.job_id,item=spec.item,consumed=1,unit_number=e.unit_number}
      end
    elseif issued.kind=='craft' then
      if receipt.recipe~=issued.recipe or receipt.count~=1 then reason='separation_craft_receipt_mismatch' end
      for item,count in pairs(issued.expected) do if (receipt.produced or {})[item]~=count then reason='separation_craft_product_mismatch' end end
      if not next(receipt.consumed or {}) then reason='separation_craft_funding_missing' end
    elseif issued.kind=='mine' then
      if receipt.item~='wood' or receipt.count~=4 or (receipt.produced or {}).wood~=4 then reason='separation_wood_receipt_mismatch' end
    elseif issued.kind=='configure_direction' then
      local e=resolve_pin(npc,issued.target)
      if not e or receipt.target_unit_number~=e.unit_number or receipt.target_force~=npc.force.name
          or receipt.target_surface_index~=e.surface.index or e.direction~=issued.direction
          or receipt.configured_direction~=issued.direction or receipt.previous_direction~=issued.previous_direction then
        reason='separation_direction_receipt_mismatch'
      else s.configured[issued.config_key]={id=receipt.id,job_id=receipt.job_id,direction=issued.direction} end
    else reason='separation_receipt_kind_unsupported' end
    state.ledger[#state.ledger+1]={id=receipt.id,job_id=receipt.job_id,sequence=issued.sequence,kind=receipt.kind,
      purpose=issued.purpose,status=receipt.status,reason=reason,item=receipt.item,count=receipt.count,
      consumed=receipt.consumed,produced=receipt.produced,tick=receipt.completed_tick or receipt.tick}
    if #state.ledger>64 then table.remove(state.ledger,1) end
    state.issued=nil;state.reason=reason
    if reason then
      s.failures=s.failures+1;s.reason=reason;state.retry_at=game.tick+RETRY
      if s.failures>=3 then s.status='blocked' end
    end
    return true
  end
  local target=resolve_pin(npc,issued.target)
  local project=npc.factory_project
  local saved=project and project.plan and project.plan.sites[issued.target.key]
  local reason
  if not project or project.id~=issued.project_id or project.index~=issued.project_index
      or not saved or saved.name~=issued.target.name
      or saved.surface_index~=issued.target.surface_index
      or not same(xy(saved.position),issued.target.position) then reason='conveyor_saved_scope_changed'
  elseif not target or receipt.target_unit_number~=issued.target.unit_number
      or receipt.target_force~=issued.target.force_name
      or receipt.target_surface_index~=issued.target.surface_index then reason='conveyor_receipt_target_mismatch'
  elseif issued.item and issued.item~=receipt.item then reason='conveyor_receipt_item_mismatch'
  elseif receipt.status~='complete' then reason=receipt.reason or 'conveyor_worker_failed'
  elseif issued.kind=='configure_direction' then
    local previous=resolve_pin(npc,issued.previous)
    local receiver=resolve_pin(npc,issued.receiver)
    local assembler=resolve_pin(npc,issued.assembler)
    local site=npc.factory_project and npc.factory_project.plan.sites[issued.target.key]
    if not previous or not receiver or not assembler or not site
        or not same(xy(site.position),issued.target.position)
        or site.direction~=issued.saved_direction
        or previous.direction~=issued.direction or receiver.drop_target~=assembler
        or math.floor(receiver.pickup_position.x)~=math.floor(target.position.x)
        or math.floor(receiver.pickup_position.y)~=math.floor(target.position.y)
        or target.direction~=issued.direction or receipt.configured_direction~=issued.direction
        or receipt.previous_direction~=issued.previous_direction then reason='conveyor_configuration_receipt_mismatch'
    else
      site.direction=issued.direction
      state.corrected_tick=receipt.completed_tick or receipt.tick
      state.corrected_site=issued.target.key
    end
  elseif type(receipt.count)~='number' or receipt.count<=0 or receipt.count>issued.count
      or (receipt.consumed or {})[issued.item]~=receipt.count
      or (receipt.produced or {})[issued.item]~=receipt.count
      or issued.kind=='belt_pickup' and receipt.removed_from_belt~=receipt.count then
    reason='conveyor_receipt_quantity_mismatch'
  else state.cleared_iron=(state.cleared_iron or 0)+receipt.count end
  state.ledger[#state.ledger+1]={id=receipt.id,job_id=receipt.job_id,sequence=issued.sequence,
    kind=receipt.kind,purpose=issued.purpose,target=issued.target,status=receipt.status,
    reason=reason,count=receipt.count,item=receipt.item,consumed=receipt.consumed,produced=receipt.produced,
    removed_from_belt=receipt.removed_from_belt,previous_direction=receipt.previous_direction,
    configured_direction=receipt.configured_direction,tick=receipt.completed_tick or receipt.tick}
  if #state.ledger>64 then table.remove(state.ledger,1) end
  state.reason=reason;state.issued=nil
  if reason then state.retry_at=game.tick+RETRY end
  return true
end
function Recovery.observe(npc)
  local s=npc and npc.conveyor_recovery
  if not s then return {} end
  return {started_tick=s.started_tick,sequence=s.sequence,issued=s.issued,reason=s.reason,
    retry_at=s.retry_at,corrected_tick=s.corrected_tick,corrected_site=s.corrected_site,
    cleared_iron=s.cleared_iron,ledger=s.ledger,last_clear_tick=s.last_clear_tick}
end
return Recovery
