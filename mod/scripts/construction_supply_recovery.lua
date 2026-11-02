-- Dynamic paid replenishment of the current unbuilt construction item.
-- The adapter schedules/binds ordinary Worker jobs; this module only observes
-- native stock/sites and records their physical receipts.
local Recovery={}
local Planner=require('scripts.construction_planner')
local RETRY=1800
local function xy(p) return p and {x=p.x or p[1],y=p.y or p[2]} end
local function same(a,b) return a and b and math.abs(a.x-b.x)<.06 and math.abs(a.y-b.y)<.06 end
local function stock(npc,item) return npc.character.get_main_inventory().get_item_count{name=item,quality='normal'} end
local function healthy(npc,item)
  local n=0;local inv=npc.character.get_main_inventory()
  for i=1,#inv do local s=inv[i]
    if s.valid_for_read and s.name==item and s.quality.name=='normal' and s.health==1 then n=n+s.count end
  end
  return n
end
local function owned(npc,e) return e and e.valid and e.force==npc.force and e.surface==npc.character.surface end
local function pin(npc,e)
  return {name=e.name,position=xy(e.position),surface_index=e.surface.index,
    force_name=e.force.name,unit_number=e.unit_number}
end
local function resolve(npc,s)
  if not s or s.surface_index~=npc.character.surface.index or s.force_name~=npc.force.name then return nil end
  for _,e in ipairs(npc.character.surface.find_entities_filtered{name=s.name,force=npc.force,position=s.position,radius=.05}) do
    if owned(npc,e) and same(e.position,s.position) and (not s.unit_number or e.unit_number==s.unit_number) then return e end
  end
end
local function current(npc)
  if not npc or not npc.character or not npc.character.valid or npc.force~=npc.character.force then return nil,'actor_scope_invalid' end
  local p=npc.factory_project
  local step=p and p.plan and p.plan.steps and p.plan.steps[p.index]
  local site=step and step.kind=='build' and p.plan.sites and p.plan.sites[step.target_site]
  if not site or site.surface_index~=npc.character.surface.index or type(site.item)~='string'
      or not prototypes.item[site.item] or not prototypes.entity[site.name]
      or prototypes.item[site.item].type~='item' then return nil,'current_plain_build_required' end
  local pos=xy(site.position)
  if not pos then return nil,'build_position_missing' end
  if #npc.character.surface.find_entities_filtered{name=site.name,force=npc.force,position=pos,radius=.05}>0 then
    return nil,'current_build_already_exists'
  end
  return {project=p.id,index=p.index,item=site.item,name=site.name,position=pos,
    direction=site.direction,surface_index=site.surface_index,key=step.target_site,healthy=healthy(npc,site.item)}
end
function Recovery.eligible(npc)
  local p=npc and npc.factory_project
  if not p or p.status~='blocked' or p.reason~='missing_healthy_normal_placement_item'
      or p.pending_job or npc.pending_job or npc.current_job or #(npc.jobs or {})>0 then return false end
  local s=current(npc)
  return s~=nil and s.healthy<1
end
local function issue(npc,state,scope,job,purpose,tick)
  state.sequence=(state.sequence or 0)+1
  state.issued={sequence=state.sequence,scope=scope,kind=job.kind,item=job.item,recipe=job.recipe,
    count=job.count,purpose=purpose,tick=tick,target=job.target and pin(npc,job.target),
    position=job.position and xy(job.position),entity_name=job.entity_name,mode=job.mode,
    carried_before=job.item and stock(npc,job.item)}
  job.upkeep_source='construction_supply_recovery';state.reason=nil
  return job
end
local function mine(npc,state,s,item,count,tick,purpose)
  return issue(npc,state,s,{kind='mine',item=item,count=count,search_radius=128},purpose,tick)
end
local function transfer(npc,state,s,e,item,count,mode,inventory,tick,purpose)
  return issue(npc,state,s,{kind='transfer',target=e,item=item,count=count,mode=mode,inventory=inventory},purpose,tick)
end
local function source(npc,key,resolve_site)
  local spec=npc.factory_project.plan.sites[key]
  local e=spec and resolve_site(npc,key)
  if not spec or spec.surface_index~=npc.character.surface.index or not owned(npc,e)
      or e.type~='furnace' or e.name~=spec.name or not same(e.position,xy(spec.position)) then return nil end
  return e
end
local function funding(npc,state,s,anchor,tick)
  if state.funding_site then
    local e=resolve(npc,state.funding_site)
    if e then return e end
    if state.funding_site.unit_number then return nil,nil,'funding_furnace_lost' end
  else
    local sites={}
    for dx=-12,12,4 do for dy=-12,12,4 do
      local p={x=math.floor(anchor.position.x)+dx,y=math.floor(anchor.position.y)+dy}
      local reserved=false
      for _,site in pairs(npc.factory_project.plan.sites) do
        local q=xy(site.position)
        if q and math.abs(q.x-p.x)<3 and math.abs(q.y-p.y)<3 then reserved=true;break end
      end
      if not reserved and npc.character.surface.can_place_entity{name='stone-furnace',position=p,
          direction=defines.direction.north,force=npc.force,build_check_type=defines.build_check_type.manual} then
        sites[#sites+1]=p
      end
    end end
    table.sort(sites,function(a,b)
      local da=(a.x-npc.character.position.x)^2+(a.y-npc.character.position.y)^2
      local db=(b.x-npc.character.position.x)^2+(b.y-npc.character.position.y)^2
      return da<db or da==db and (a.x<b.x or a.x==b.x and a.y<b.y)
    end)
    if not sites[1] then return nil,nil,'no_legal_funding_furnace_site' end
    state.funding_site={name='stone-furnace',position=sites[1],direction=defines.direction.north,
      surface_index=npc.character.surface.index,force_name=npc.force.name}
  end
  if healthy(npc,'stone-furnace')<1 then
    if npc.character.get_craftable_count('stone-furnace')>=1 then
      return nil,issue(npc,state,s,{kind='craft',recipe='stone-furnace',count=1},'craft_paid_funding_furnace',tick)
    end
    return nil,mine(npc,state,s,'stone',math.max(1,5-stock(npc,'stone')),tick,'mine_funding_stone')
  end
  local spec=state.funding_site
  if not npc.character.surface.can_place_entity{name=spec.name,position=spec.position,
      direction=spec.direction,force=npc.force,build_check_type=defines.build_check_type.manual} then
    state.funding_site=nil;return nil,nil,'funding_furnace_site_changed'
  end
  return nil,issue(npc,state,s,{kind='build',entity_name='stone-furnace',item='stone-furnace',count=1,
    position=spec.position,direction=spec.direction},'build_paid_funding_furnace',tick)
end
local function plates(npc,state,s,item,amount,tick,resolve_site)
  local key=item=='iron-plate' and 'iron_furnace' or 'copper_furnace'
  local furnace=source(npc,key,resolve_site)
  if not furnace then return nil,'owned_plate_furnace_missing' end
  local output=furnace.get_output_inventory()
  local available=output.get_item_count{name=item,quality='normal'}
  local inserter=resolve_site(npc,item=='iron-plate' and 'iron_output_inserter' or 'copper_output_inserter')
  local automatic=owned(npc,inserter) and inserter.pickup_target==furnace
  if automatic or state.funding_site or (state.source_failures or 0)>0 then
    local job,reason
    furnace,job,reason=funding(npc,state,s,furnace,tick)
    if job or reason then return job,reason end
    output=furnace.get_output_inventory();available=output.get_item_count{name=item,quality='normal'}
  end
  if available>0 then
    return transfer(npc,state,s,furnace,item,math.min(available,amount,16),'withdraw',
      defines.inventory.crafter_output,tick,'withdraw_earned_supply_plates')
  end
  for _,other in ipairs({'iron-plate','copper-plate'}) do
    local n=output.get_item_count{name=other,quality='normal'}
    if other~=item and n>0 then return transfer(npc,state,s,furnace,other,math.min(n,16),'withdraw',
      defines.inventory.crafter_output,tick,'recover_other_paid_furnace_output') end
  end
  local fuel=furnace.get_fuel_inventory()
  if fuel.get_item_count('coal')<1 then
    if stock(npc,'coal')<4 then return mine(npc,state,s,'coal',4-stock(npc,'coal'),tick,'mine_supply_fuel') end
    return transfer(npc,state,s,furnace,'coal',4,'deposit',defines.inventory.fuel,tick,'fuel_owned_supply_furnace')
  end
  local ore=item=='iron-plate' and 'iron-ore' or 'copper-ore'
  local input=furnace.get_inventory(defines.inventory.crafter_input)
  if input.get_item_count(ore)>=1 or furnace.crafting_progress>0 then return nil,'await_native_supply_smelting' end
  if stock(npc,ore)<1 then return mine(npc,state,s,ore,math.min(amount,16),tick,'mine_paid_supply_ore') end
  return transfer(npc,state,s,furnace,ore,math.min(stock(npc,ore),amount,16),'deposit',
    defines.inventory.crafter_input,tick,'smelt_paid_supply_ore')
end
function Recovery.next(npc,tick,resolve_site)
  local s,reason=current(npc)
  if not s then return nil,reason end
  if s.healthy>=1 then return nil,'placement_item_ready' end
  if type(resolve_site)~='function' then return nil,'site_resolver_missing' end
  local state=npc.construction_supply_recovery
  if state and state.issued then return nil,'supply_job_pending' end
  state=state or {sequence=0,ledger={},started_tick=tick,source_failures=0}
  if state.scope and (state.scope.project~=s.project or state.scope.index~=s.index
      or state.scope.item~=s.item) then state.retry_at=0;state.source_failures=0 end
  state.scope=s;npc.construction_supply_recovery=state
  if tick<(state.retry_at or 0) then return nil,'supply_retry_cooldown' end
  local recipe=npc.force.recipes[s.item]
  if not recipe or not recipe.enabled then return nil,'supply_recipe_locked' end
  -- Force one NEW healthy product even if a damaged finished item is counted
  -- by the ordinary ingredient ledger. No existing stock is edited or removed.
  local budget,why=Planner.craft_plan(npc,s.item,stock(npc,s.item)+1)
  if not budget then state.reason=why;return nil,why end
  state.observed_budget=budget;state.observed_tick=tick
  if npc.character.get_craftable_count(s.item)>=1 then
    return issue(npc,state,s,{kind='craft',recipe=s.item,count=1},'craft_paid_current_placement_item',tick)
  end
  for _,item in ipairs({'stone','wood','coal','iron-plate','copper-plate'}) do
    local amount=budget.raw_deficits[item] or 0
    if amount>0 then
      if item=='iron-plate' or item=='copper-plate' then return plates(npc,state,s,item,amount,tick,resolve_site) end
      return mine(npc,state,s,item,amount,tick,'mine_paid_supply_material')
    end
  end
  return nil,'native_supply_craft_unavailable'
end
function Recovery.bind(npc,id)
  local i=npc.construction_supply_recovery and npc.construction_supply_recovery.issued
  if not i or i.job_id or type(id)~='string' or id=='' then return false end
  i.job_id=id;return true
end
function Recovery.reject_unbound(npc,reason)
  local state=npc and npc.construction_supply_recovery
  if not state or not state.issued or state.issued.job_id then return false end
  state.issued=nil;state.reason=reason or 'supply_enqueue_rejected';state.retry_at=game.tick+RETRY
  return true
end
function Recovery.on_receipt(npc,r)
  local state=npc and npc.construction_supply_recovery
  local i=state and state.issued
  if not i or not i.job_id or not r or r.job_id~=i.job_id or r.kind~=i.kind then return false end
  local s=current(npc)
  local reason
  if not s or s.project~=i.scope.project or s.index~=i.scope.index or s.item~=i.scope.item
      or s.name~=i.scope.name or not same(s.position,i.scope.position) then reason='supply_build_scope_changed'
  elseif i.target and (not resolve(npc,i.target) or r.target_unit_number~=i.target.unit_number
      or r.target_force~=i.target.force_name or r.target_surface_index~=i.target.surface_index) then reason='supply_receipt_target_mismatch'
  elseif r.status~='complete' then reason=r.reason or 'supply_worker_failed'
  elseif i.kind=='craft' and (r.recipe~=i.recipe or type(r.count)~='number' or r.count<1
      or r.count>i.count or (r.produced or {})[i.recipe]==nil or r.produced[i.recipe]<1
      or healthy(npc,i.recipe)<1) then reason='supply_craft_receipt_mismatch'
  elseif i.kind=='build' then
    local e=r.entity
    if i.purpose~='build_paid_funding_furnace' or not owned(npc,e) or e.name~=i.entity_name
        or not same(e.position,i.position) or (r.consumed or {})['stone-furnace']~=1 then reason='supply_funding_build_mismatch'
    else state.funding_site=pin(npc,e) end
  elseif i.kind=='transfer' and (r.item~=i.item or type(r.count)~='number' or r.count<=0
      or r.count>i.count or r.mode~=i.mode or (r.consumed or {})[i.item]~=r.count
      or (r.produced or {})[i.item]~=r.count
      or i.mode=='withdraw' and stock(npc,i.item)<i.carried_before+r.count
      or i.mode=='deposit' and stock(npc,i.item)>i.carried_before-r.count) then
    reason='supply_transfer_receipt_mismatch'
  elseif i.kind=='mine' and (r.item~=i.item or type(r.count)~='number' or r.count<=0
      or (r.produced or {})[i.item]~=r.count or stock(npc,i.item)<i.carried_before+r.count) then reason='supply_mining_receipt_mismatch' end
  state.ledger[#state.ledger+1]={id=r.id,job_id=r.job_id,sequence=i.sequence,purpose=i.purpose,
    kind=r.kind,item=r.item,recipe=r.recipe,count=r.count,status=r.status,reason=reason,
    scope=i.scope,target=i.target,position=i.position,consumed=r.consumed,produced=r.produced,
    tick=r.completed_tick or r.tick}
  if #state.ledger>64 then table.remove(state.ledger,1) end
  state.issued=nil;state.reason=reason
  if reason then
    state.retry_at=game.tick+RETRY
    if i.purpose=='withdraw_earned_supply_plates' then state.source_failures=(state.source_failures or 0)+1 end
  end
  return true
end
function Recovery.observe(npc)
  local s=npc and npc.construction_supply_recovery
  if not s then return {} end
  return {started_tick=s.started_tick,scope=s.scope,issued=s.issued,sequence=s.sequence,ledger=s.ledger,
    reason=s.reason,retry_at=s.retry_at,source_failures=s.source_failures,funding_site=s.funding_site,
    observed_budget=s.observed_budget,observed_tick=s.observed_tick}
end
return Recovery
