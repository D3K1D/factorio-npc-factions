-- Paid current handcraft remainder recovery. Native jobs are adapter-owned;
-- this module never alters a project step, stock, research, or native entity.
local Recovery={}
local Planner=require('scripts.construction_planner')
local Recipes=require("scripts.recipes")
local RETRY=1800
local RAW={['iron-plate']=true,['copper-plate']=true,coal=true,stone=true,wood=true}
local function xy(p) return p and {x=p.x or p[1],y=p.y or p[2]} end
local function same(a,b) return a and b and math.abs(a.x-b.x)<.06 and math.abs(a.y-b.y)<.06 end
local function integer(x) return type(x)=='number' and x>=0 and x%1==0 end
local function stock(n,item) return n.character.get_main_inventory().get_item_count{name=item,quality='normal'} end
local function counts(n)
  local out={};for _,entry in ipairs(n.character.get_main_inventory().get_contents()) do
    if entry.quality=='normal' then out[entry.name]=(out[entry.name] or 0)+entry.count end
  end;return out
end
local function owned(n,e) return e and e.valid and e.force==n.force and e.surface==n.character.surface end
local function pin(e) return {name=e.name,position=xy(e.position),surface_index=e.surface.index,
  force_name=e.force.name,unit_number=e.unit_number} end
local function resolve(n,s)
  if not s or s.force_name~=n.force.name or s.surface_index~=n.character.surface.index then return nil end
  for _,e in ipairs(n.character.surface.find_entities_filtered{name=s.name,force=n.force,position=s.position,radius=.05}) do
    if owned(n,e) and e.unit_number==s.unit_number and same(xy(e.position),s.position) then return e end
  end
end
local function current(n)
  if not n or not n.character or not n.character.valid or n.force~=n.character.force then return nil,'craft_actor_scope_invalid' end
  local p=n.factory_project;local step=p and p.plan and p.plan.steps and p.plan.steps[p.index]
  if not step or step.kind~='craft' or not integer(step.count) or step.count<1 or type(step.recipe)~='string' then
    return nil,'current_handcraft_required'
  end
  local r=n.force.recipes[step.recipe]
  if not r or not r.enabled then return nil,'craft_recipe_locked' end
  if not Recipes.made_in(r,n.character.prototype.crafting_categories) then return nil,'craft_category_unsupported' end
  if #r.products~=1 then return nil,'craft_single_product_required' end
  local prod=r.products[1]
  if prod.type~='item' or prod.name~=step.recipe or not integer(prod.amount) or prod.amount<1
      or prod.probability and prod.probability~=1 or not prototypes.item[prod.name]
      or prototypes.item[prod.name].type~='item' then return nil,'craft_deterministic_named_item_required' end
  for _,ingredient in ipairs(r.ingredients) do
    if ingredient.type~='item' or not integer(ingredient.amount) then return nil,'craft_fluid_or_fractional_input_unsupported' end
  end
  return {project=p.id,index=p.index,started_tick=p.started_tick,last_progress_tick=p.last_progress_tick,
    recipe=step.recipe,count=step.count,per=prod.amount,force_name=n.force.name,surface_index=n.character.surface.index}
end
local function equal(a,b)
  return a and b and a.project==b.project and a.index==b.index and a.started_tick==b.started_tick
    and a.recipe==b.recipe and a.count==b.count and a.per==b.per and a.force_name==b.force_name
    and a.surface_index==b.surface_index and a.last_progress_tick==b.last_progress_tick
end
local function map_copy(t) local out={} for k,v in pairs(t or {}) do out[k]=v end return out end
local function map_equal(a,b)
  for k,v in pairs(a or {}) do if v~=(b or {})[k] and not(v==0 and (b or {})[k]==nil) then return false end end
  for k,v in pairs(b or {}) do if v~=(a or {})[k] and not(v==0 and (a or {})[k]==nil) then return false end end
  return true
end
-- Native begin_crafting takes inventory inputs once PER requested batch. A
-- prerequisite's surplus returns to inventory and can be taken by a later
-- batch. Worker.consumed therefore includes this endogenous intermediate churn.
local function recursive_graph(n,root)
  local graph,visiting,number={},{},0
  local function visit(item,depth)
    if graph[item] then return true end
    if depth>12 or visiting[item] then return false end
    number=number+1;if number>32 then return false end
    if RAW[item] then graph[item]={raw=true};return true end
    local r=n.force.recipes[item];local prod=r and r.products[1]
    if not r or not r.enabled or not Recipes.made_in(r,n.character.prototype.crafting_categories)
        or #r.products~=1 or prod.type~='item' or prod.name~=item or not integer(prod.amount)
        or prod.amount<1 or prod.probability and prod.probability~=1
        or not prototypes.item[item] or prototypes.item[item].type~='item' then return false end
    visiting[item]=true;local ingredients={}
    for _,x in ipairs(r.ingredients) do
      if x.type~='item' or not integer(x.amount) or x.amount<1 or not visit(x.name,depth+1) then return false end
      ingredients[#ingredients+1]={name=x.name,amount=x.amount}
    end
    visiting[item]=nil;graph[item]={per=prod.amount,ingredients=ingredients,category=Recipes.category(r)};return true
  end
  if not visit(root,0) or graph[root].raw then return nil,'craft_recursive_graph_unsupported' end
  return graph
end
local function simulate(graph,root,before,batches)
  if not integer(batches) or batches>1000 then return nil,'craft_recursive_batch_bound' end
  local carried,consumed=map_copy(before),{}
  for _=1,batches do
    local pending={}
    local function need(item,amount,depth)
      if depth>12 then return false end
      local used=math.min(carried[item] or 0,amount)
      carried[item]=(carried[item] or 0)-used;amount=amount-used
      if used>0 then consumed[item]=(consumed[item] or 0)+used end
      used=math.min(pending[item] or 0,amount)
      pending[item]=(pending[item] or 0)-used;amount=amount-used
      if amount==0 then return true end
      local node=graph[item];if not node or node.raw then return false end
      local count=math.ceil(amount/node.per)
      for _,x in ipairs(node.ingredients) do if not need(x.name,x.amount*count,depth+1) then return false end end
      pending[item]=(pending[item] or 0)+count*node.per-amount
      return true
    end
    for _,x in ipairs(graph[root].ingredients) do
      if not need(x.name,x.amount,0) then return nil,'craft_recursive_material_shortfall' end
    end
    for item,count in pairs(pending) do carried[item]=(carried[item] or 0)+count end
    carried[root]=(carried[root] or 0)+graph[root].per
  end
  return {carried_after=carried,consumed=consumed}
end
local function native_credit(n,s,r)
  local c=r.production_credit
  if not c or c.schema~=1 or c.receipt_id~=r.id or c.force_name~=s.force_name
      or c.surface_index~=s.surface_index or c.quality~='normal' or next(c.errors or {})~=nil
      or not map_equal(c.observed,{[s.recipe]=r.completed*s.per}) then return false end
  local credited=(c.credited or {})[s.recipe] or 0;local delta=(c.native_delta or {})[s.recipe] or 0
  local baseline=(c.baseline or {})[s.recipe]
  return integer(credited) and integer(delta) and integer(baseline)
    and credited+delta>=r.completed*s.per
    and n.force.get_item_production_statistics(n.character.surface).get_input_count{name=s.recipe,quality='normal'}>=baseline+credited
end
local function native_terminal(n,r)
  local saved=(n.receipts or {})[r.job_id]
  if not saved or (n.completed_jobs or {})[r.job_id]~=r.id then return false end
  for _,key in ipairs({'id','job_id','npc_id','kind','status','recipe','count','completed','started_tick','tick'}) do
    if saved[key]~=r[key] then return false end
  end
  for _,key in ipairs({'produced','consumed','refunded'}) do if not map_equal(saved[key],r[key]) then return false end end
  if r.completed>0 then
    local a,b=saved.production_credit,r.production_credit;if not a or not b then return false end
    for _,key in ipairs({'schema','receipt_id','force_name','surface_index','quality'}) do if a[key]~=b[key] then return false end end
    for _,key in ipairs({'baseline','observed','credited','native_delta'}) do if not map_equal(a[key],b[key]) then return false end end
  end
  local count=0;for _,id in ipairs(n.receipt_order or {}) do if id==r.job_id then count=count+1 end end
  return count==1
end
local function craft_accounting(n,s,r,before)
  local graph,why=recursive_graph(n,s.recipe);if not graph then return nil,why end
  local expected;expected,why=simulate(graph,s.recipe,before,r.completed)
  if not expected then return nil,why end
  local net={}
  for item,count in pairs(r.consumed or {}) do
    if not integer(count) or not graph[item] then return nil,'craft_supply_craft_consumption_mismatch' end
    net[item]=count
  end
  for item,count in pairs(r.refunded or {}) do
    if not integer(count) or not graph[item] then return nil,'craft_supply_craft_consumption_mismatch' end
    net[item]=(net[item] or 0)-count
  end
  if not map_equal(net,expected.consumed) or not map_equal(r.produced,{[s.recipe]=r.completed*s.per}) then
    return nil,'craft_supply_craft_consumption_mismatch'
  end
  for item in pairs(graph) do
    if stock(n,item)~=(expected.carried_after[item] or 0) then return nil,'craft_supply_craft_consumption_mismatch' end
  end
  if r.completed>0 and not native_credit(n,s,r) then return nil,'craft_supply_native_credit_mismatch' end
  return {schema=2,method='deterministic_native_batch_inventory',carried_before=before,
    expected_consumed=expected.consumed,carried_after=expected.carried_after}
end
-- Legacy Worker receipts lack a project index. Accept only exact failed recipe
-- receipts newer than the saved current-step progress boundary, and require
-- the credited deterministic products to remain physically carried.
local function legacy(n,s)
  local total,evidence,seen=0,{},{}
  if not integer(s.last_progress_tick) or not integer(s.started_tick) then return nil,'craft_legacy_progress_boundary_missing' end
  for _,r in pairs(n.receipts or {}) do
    if r.kind=='craft' and r.recipe==s.recipe and r.started_tick and r.started_tick>s.last_progress_tick
        and r.started_tick>=s.started_tick then
      if r.status=='complete' then return nil,'craft_legacy_completed_receipt_scope_ambiguous' end
      if r.status~='failed' or r.reason~='missing_craft_ingredients' or r.npc_id~=n.id
          or type(r.job_id)~='string' or not r.job_id:match('^plan%-%d+$')
          or r.id~=tostring(n.id)..':'..r.job_id or not integer(r.completed)
          or r.count~=r.completed or (r.produced or {})[s.recipe]~=nil
            and r.produced[s.recipe]~=r.completed*s.per then return nil,'craft_legacy_partial_receipt_unsupported' end
      if r.completed>0 and ((r.produced or {})[s.recipe]~=r.completed*s.per
          or next(r.consumed or {})==nil) then return nil,'craft_legacy_partial_receipt_unsupported' end
      local credit=r.production_credit
      if not credit or credit.schema~=1 or credit.receipt_id~=r.id or credit.force_name~=s.force_name
          or credit.surface_index~=s.surface_index or credit.quality~='normal'
          or (credit.observed or {})[s.recipe]~=r.completed*s.per or next(credit.errors or {})~=nil then
        return nil,'craft_legacy_native_credit_scope_unsupported'
      end
      if seen[r.id] then return nil,'craft_legacy_duplicate_receipt_id' end;seen[r.id]=true
      for _,ledger in ipairs({r.consumed or {},r.refunded or {}}) do
        for _,amount in pairs(ledger) do if not integer(amount) then return nil,'craft_legacy_consumption_unsupported' end end
      end
      total=total+r.completed
      evidence[#evidence+1]={id=r.id,job_id=r.job_id,completed=r.completed,
        produced=(r.produced or {})[s.recipe] or 0,consumed=r.consumed,tick=r.tick}
    end
  end
  table.sort(evidence,function(a,b)return a.tick<b.tick or a.tick==b.tick and a.id<b.id end)
  if total>s.count or stock(n,s.recipe)<total*s.per then return nil,'craft_legacy_partial_output_not_carried' end
  return {completed=total,receipts=evidence}
end
function Recovery.eligible(n)
  local p=n and n.factory_project
  if not p or p.status~='blocked' or p.reason~='missing_craft_ingredients' or p.pending_job or n.pending_job
      or n.current_job or n.social_job or n.ai_running or next(n.jobs or {})~=nil
      or not n.character or not n.character.valid or n.character.crafting_queue_size>0 then return false end
  local s=current(n);if not s then return false end
  local credit=legacy(n,s);return credit~=nil
end
local function issue(n,state,s,job,purpose,tick)
  state.sequence=(state.sequence or 0)+1
  state.issued={sequence=state.sequence,scope=s,kind=job.kind,item=job.item,recipe=job.recipe,
    count=job.count,mode=job.mode,purpose=purpose,tick=tick,carried_before=counts(n),target=job.target and pin(job.target)}
  state.reason=nil;job.upkeep_source='craft_supply_recovery';return job
end
local function mine(n,state,s,item,count,tick,purpose)
  return issue(n,state,s,{kind='mine',item=item,count=math.min(count,16),search_radius=128},purpose,tick)
end
local function transfer(n,state,s,e,item,count,mode,inventory,tick,purpose)
  return issue(n,state,s,{kind='transfer',target=e,item=item,count=math.min(count,16),mode=mode,inventory=inventory},purpose,tick)
end
local function furnace(n,item,resolve_site)
  for _,key in ipairs(item=='iron-plate' and {'construction_iron_furnace','iron_furnace'}
      or {'construction_copper_furnace','copper_furnace'}) do
    local spec=n.factory_project.plan.sites[key];local e=spec and resolve_site(n,key)
    if spec and spec.surface_index==n.character.surface.index and owned(n,e) and e.type=='furnace'
        and e.name==spec.name and same(xy(e.position),xy(spec.position))
        and (not spec.existing_unit_number or spec.existing_unit_number==e.unit_number) then
      local automatic=false
      for _,i in ipairs(n.character.surface.find_entities_filtered{type='inserter',force=n.force,position=e.position,radius=3}) do
        if i.valid and i.pickup_target==e then automatic=true;break end
      end
      if not automatic then return e end
    end
  end
end
local function plates(n,state,s,item,amount,tick,resolve_site)
  local e=furnace(n,item,resolve_site)
  if not e then return nil,'craft_owned_dedicated_furnace_required' end
  local output=e.get_output_inventory();local available=output.get_item_count{name=item,quality='normal'}
  if available>0 then return transfer(n,state,s,e,item,math.min(available,amount),'withdraw',
    defines.inventory.crafter_output,tick,'withdraw_paid_craft_plates') end
  for _,other in ipairs({'iron-plate','copper-plate'}) do local count=output.get_item_count{name=other,quality='normal'}
    if other~=item and count>0 then return transfer(n,state,s,e,other,count,'withdraw',
      defines.inventory.crafter_output,tick,'clear_other_paid_craft_output') end
  end
  if e.get_fuel_inventory().get_item_count('coal')<1 then
    if stock(n,'coal')<4 then return mine(n,state,s,'coal',4-stock(n,'coal'),tick,'mine_paid_craft_fuel') end
    return transfer(n,state,s,e,'coal',4,'deposit',defines.inventory.fuel,tick,'fuel_paid_craft_furnace')
  end
  local ore=item=='iron-plate' and 'iron-ore' or 'copper-ore'
  local input=e.get_inventory(defines.inventory.crafter_input)
  if input.get_item_count(ore)>0 or e.crafting_progress>0 then return nil,'await_native_craft_smelting' end
  -- A foreign ore source must be physically recovered, never removed by script.
  local other=ore=='iron-ore' and 'copper-ore' or 'iron-ore'
  if input.get_item_count(other)>0 then return transfer(n,state,s,e,other,input.get_item_count(other),'withdraw',
    defines.inventory.crafter_input,tick,'recover_other_paid_craft_input') end
  if stock(n,ore)<1 then return mine(n,state,s,ore,amount,tick,'mine_paid_craft_ore') end
  return transfer(n,state,s,e,ore,math.min(stock(n,ore),amount),'deposit',
    defines.inventory.crafter_input,tick,'smelt_paid_craft_ore')
end
function Recovery.next(n,tick,resolve_site)
  local s,reason=current(n);if not s then return nil,reason end
  if type(resolve_site)~='function' then return nil,'craft_site_resolver_missing' end
  if n.character.crafting_queue_size>0 then return nil,'craft_native_queue_not_idle' end
  local state=n.craft_supply_recovery
  if state and state.issued then return nil,'craft_supply_job_pending' end
  if not state then
    local credit,why=legacy(n,s);if not credit then return nil,why end
    if credit.completed==0 and n.character.get_craftable_count(s.recipe)>=s.count then return nil,'craft_ready' end
  end
  state=state or {sequence=0,ledger={},history={},started_tick=tick}
  if not equal(state.scope,s) then
    local credit,why=legacy(n,s);if not credit then return nil,why end
    if state.scope then
      state.history[#state.history+1]={scope=state.scope,completed=state.completed_batches,tick=tick}
      while #state.history>32 do table.remove(state.history,1) end
    end
    state.scope=s;state.legacy_credit=credit;state.completed_batches=credit.completed;state.retry_at=0;state.reason=nil;state.quarantined=nil
  end
  n.craft_supply_recovery=state
  local remaining=s.count-state.completed_batches
  state.remaining_batches=remaining
  if state.quarantined then return nil,'craft_supply_receipt_quarantined' end
  if remaining==0 then return nil,'craft_step_satisfied' end
  if tick<(state.retry_at or 0) then return nil,'craft_supply_retry_cooldown' end
  local budget,why=Planner.craft_plan(n,s.recipe,stock(n,s.recipe)+remaining*s.per)
  if not budget then state.reason=why;return nil,why end
  state.observed_budget=budget;state.remaining_batches=remaining;state.observed_tick=tick
  for item in pairs(budget.raw_deficits) do if not RAW[item] then return nil,'craft_raw_material_unsupported' end end
  if n.character.get_craftable_count(s.recipe)>=remaining then
    local graph,graph_reason=recursive_graph(n,s.recipe);if not graph then return nil,graph_reason end
    local predicted,predict_reason=simulate(graph,s.recipe,counts(n),remaining)
    if not predicted then return nil,predict_reason end
    return issue(n,state,s,{kind='craft',recipe=s.recipe,count=remaining},'complete_paid_current_craft_remainder',tick)
  end
  for _,item in ipairs({'stone','wood','coal','iron-plate','copper-plate'}) do
    local amount=budget.raw_deficits[item] or 0
    if amount>0 then
      if item=='iron-plate' or item=='copper-plate' then return plates(n,state,s,item,amount,tick,resolve_site) end
      return mine(n,state,s,item,amount,tick,'mine_paid_craft_material')
    end
  end
  return nil,'native_craft_batch_unavailable'
end
function Recovery.bind(n,id)
  local i=n.craft_supply_recovery and n.craft_supply_recovery.issued
  if not i or i.job_id or type(id)~='string' or id=='' then return false end
  i.job_id=id;return true
end
function Recovery.reject_unbound(n,reason)
  local state=n.craft_supply_recovery;local i=state and state.issued
  if not i or i.job_id then return false end
  state.issued=nil;state.reason=reason or 'craft_supply_enqueue_rejected';state.retry_at=game.tick+RETRY;return true
end
function Recovery.on_receipt(n,r)
  local state=n.craft_supply_recovery;local i=state and state.issued
  if not i or not i.job_id or not r or r.job_id~=i.job_id or r.kind~=i.kind then return false end
  local s=current(n);local reason;local completed=0;local verified_craft=false
  if not equal(s,i.scope) then reason='craft_supply_scope_changed'
  elseif r.npc_id~=n.id or r.id~=tostring(n.id)..':'..i.job_id or not r.started_tick or r.started_tick<i.tick then
    reason='craft_supply_receipt_actor_or_time_mismatch'
  elseif i.target and (not resolve(n,i.target) or r.target_unit_number~=i.target.unit_number
      or r.target_force~=i.target.force_name or r.target_surface_index~=i.target.surface_index) then reason='craft_supply_receipt_target_mismatch'
  elseif i.kind=='craft' then
    local c=r.completed
    if r.recipe~=i.recipe or not integer(c) or c>i.count or r.count~=c
        or (r.produced or {})[i.recipe]~=nil and r.produced[i.recipe]~=c*s.per
        or c>0 and (r.produced or {})[i.recipe]~=c*s.per
        or stock(n,i.recipe)<(i.carried_before[i.recipe] or 0)+c*s.per
        or r.status=='complete' and c~=i.count then reason='craft_supply_craft_receipt_mismatch'
    else
      local accounting
      if not native_terminal(n,r) then reason='craft_supply_native_receipt_missing'
      else accounting,reason=craft_accounting(n,s,r,i.carried_before) end
      if accounting then i.accounting=accounting;verified_craft=true end
      if not reason then completed=c;state.completed_batches=state.completed_batches+c
        state.remaining_batches=s.count-state.completed_batches end
    end
  elseif i.kind=='transfer' then
    if r.status~='complete' then reason=r.reason or 'craft_supply_worker_failed'
    elseif r.item~=i.item or r.mode~=i.mode or not integer(r.count) or r.count<1 or r.count>i.count
        or (r.consumed or {})[i.item]~=r.count or (r.produced or {})[i.item]~=r.count
        or i.mode=='withdraw' and stock(n,i.item)~=(i.carried_before[i.item] or 0)+r.count
        or i.mode=='deposit' and stock(n,i.item)~=(i.carried_before[i.item] or 0)-r.count then
      reason='craft_supply_transfer_receipt_mismatch'
    end
  elseif i.kind=='mine' then
    if r.status~='complete' then reason=r.reason or 'craft_supply_worker_failed'
    elseif r.item~=i.item or not integer(r.count) or r.count<1 or r.count>i.count
        or (r.produced or {})[i.item]~=r.count
        or stock(n,i.item)~=(i.carried_before[i.item] or 0)+r.count then reason='craft_supply_mine_receipt_mismatch' end
  else reason='craft_supply_job_kind_unsupported' end
  if not reason and r.status~='complete' then reason=r.reason or 'craft_supply_worker_failed' end
  if i.kind=='craft' and reason and not verified_craft then state.quarantined=true end
  state.ledger[#state.ledger+1]={id=r.id,job_id=r.job_id,sequence=i.sequence,scope=i.scope,purpose=i.purpose,
    target=i.target,kind=r.kind,status=r.status,reason=reason,item=r.item,recipe=r.recipe,count=r.count,
    completed_batches=completed,produced=r.produced,consumed=r.consumed,tick=r.tick,accounting=i.accounting}
  while #state.ledger>64 do table.remove(state.ledger,1) end
  state.issued=nil;state.reason=reason;if reason then state.retry_at=game.tick+RETRY end
  return true
end
-- Compatibility with old F993 quarantines whose issued snapshot was discarded.
-- Infer only a UNIQUE bounded native per-batch inventory model. Authentication
-- uses the actor's terminal Worker receipt, completed-job marker, exact existing
-- maintenance ledger scope, native output credit and currently carried output.
-- This never replays a craft, changes an old receipt or grants production credit.
function Recovery.quarantine_proposal(n)
  local state=n and n.craft_supply_recovery;local s=current(n)
  if not state or not state.quarantined or state.reason~='craft_supply_craft_consumption_mismatch'
      or state.issued or not equal(state.scope,s) or n.pending_job or n.current_job or n.social_job
      or n.ai_running or next(n.jobs or {})~=nil or n.factory_project.pending_job
      or n.character.crafting_queue_size>0 or not integer(state.completed_batches)
      or not integer(state.observed_tick) or not integer(s.last_progress_tick)
      or not integer(state.sequence) then return nil,'craft_quarantine_scope_not_idle' end
  local ledger=state.ledger or {};local last=ledger[#ledger];local r=last and (n.receipts or {})[last.job_id]
  if not last or not r or not equal(last.scope,s) or last.reason~=state.reason
      or last.sequence~=state.sequence or last.kind~='craft' or last.status~='complete'
      or last.purpose~='complete_paid_current_craft_remainder' or last.completed_batches~=0
      or r.kind~='craft' or r.status~='complete' or r.recipe~=s.recipe or last.recipe~=s.recipe
      or r.npc_id~=n.id or r.id~=tostring(n.id)..':'..r.job_id or r.id~=last.id
      or not integer(r.completed) or r.completed<1 or r.count~=r.completed or last.count~=r.completed
      or r.completed~=s.count-(state.completed_batches or 0)
      or not integer(r.started_tick) or not integer(r.tick) or r.started_tick<s.last_progress_tick
      or r.started_tick<state.observed_tick or r.tick~=last.tick or r.tick<r.started_tick
      or (n.completed_jobs or {})[r.job_id]~=r.id or not map_equal(r.produced,last.produced)
      or not map_equal(r.consumed,last.consumed) or next(r.refunded or {})~=nil
      or not map_equal(r.produced,{[s.recipe]=r.completed*s.per}) or not native_credit(n,s,r)
      or stock(n,s.recipe)<s.count*s.per then return nil,'craft_quarantine_receipt_not_authenticated' end
  local seen,ordered,linked=0,0,0
  for _,x in pairs(n.receipts or {}) do if x.id==r.id then seen=seen+1 end end
  for _,id in ipairs(n.receipt_order or {}) do if id==r.job_id then ordered=ordered+1 end end
  for _,x in ipairs(ledger) do if x.id==r.id then linked=linked+1 end end
  if seen~=1 or ordered~=1 or linked~=1 then return nil,'craft_quarantine_duplicate_receipt_evidence' end
  for _,x in ipairs(state.reconciliations or {}) do if x.id==r.id then return nil,'craft_quarantine_already_reconciled' end end
  local graph,why=recursive_graph(n,s.recipe);if not graph then return nil,why end
  local before=counts(n);before[s.recipe]=stock(n,s.recipe)-r.completed*s.per
  local variables,combinations={},1
  for item,node in pairs(graph) do
    local amount=(r.consumed or {})[item] or 0
    if not integer(amount) then return nil,'craft_quarantine_consumption_unsupported' end
    if node.raw then before[item]=stock(n,item)+amount
    elseif item~=s.recipe then
      local bound=stock(n,item)+amount
      combinations=combinations*(bound+1)
      if combinations>4096 then return nil,'craft_quarantine_inverse_bound' end
      variables[#variables+1]={item=item,bound=bound}
    end
  end
  for item in pairs(r.consumed or {}) do if not graph[item] then return nil,'craft_quarantine_unknown_consumption' end end
  table.sort(variables,function(a,b)return a.item<b.item end)
  local matches,answer=0,nil
  local function search(index)
    if matches>1 then return end
    local v=variables[index]
    if not v then
      local proof=craft_accounting(n,s,r,before)
      if proof then matches=matches+1;answer=proof;answer.carried_before=map_copy(before) end
      return
    end
    for amount=0,v.bound do before[v.item]=amount;search(index+1) end
  end
  search(1)
  if matches~=1 then return nil,'craft_quarantine_inventory_model_ambiguous' end
  return {scope=s,id=r.id,job_id=r.job_id,sequence=last.sequence,completed=r.completed,accounting=answer,
    method='authenticated_unique_recursive_inventory',candidates=combinations,prior_reason=state.reason}
end
function Recovery.rehabilitate_quarantine(n,tick)
  local proposal,why=Recovery.quarantine_proposal(n);if not proposal then return false,why end
  local state=n.craft_supply_recovery
  state.reconciliations=state.reconciliations or {}
  proposal.tick=tick;state.reconciliations[#state.reconciliations+1]=proposal
  while #state.reconciliations>32 do table.remove(state.reconciliations,1) end
  state.completed_batches=state.completed_batches+proposal.completed;state.remaining_batches=0
  state.quarantined=nil;state.reason=nil;state.retry_at=0
  return true,'craft_quarantine_authenticated'
end
function Recovery.finished(n)
  local state=n and n.craft_supply_recovery
  return state and not state.issued and not state.quarantined and equal(state.scope,current(n))
    and state.completed_batches==state.scope.count or false
end
function Recovery.observe(n)
  local s=n and n.craft_supply_recovery
  if not s then return {} end
  return {scope=s.scope,sequence=s.sequence,issued=s.issued,legacy_credit=s.legacy_credit,
    completed_batches=s.completed_batches,remaining_batches=s.remaining_batches,ledger=s.ledger,history=s.history,
    reason=s.reason,retry_at=s.retry_at,quarantined=s.quarantined,observed_budget=s.observed_budget,observed_tick=s.observed_tick,started_tick=s.started_tick,
    reconciliations=s.reconciliations}
end
return Recovery
