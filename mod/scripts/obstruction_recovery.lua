-- Explicit bounded maintenance for an authenticated blocked clearance goal.
-- No entity/inventory/path/project mutations; ordinary Worker jobs only.
local Recovery={}
local MAX_CLEAR,MAX_GOAL,MAX_JOBS=2,3,5
local function copy(v) if type(v)~='table' then return v end local o={} for k,x in pairs(v) do o[k]=copy(x) end return o end
local function equal(a,b) return helpers.table_to_json(a)==helpers.table_to_json(b) end
local function xy(p) return p and {x=p.x or p[1],y=p.y or p[2]} end
local function same(a,b) return a and b and math.abs(a.x-b.x)<.01 and math.abs(a.y-b.y)<.01 end
local function integer(v) return type(v)=='number' and v>=0 and v==math.floor(v) end
local function stocks(n)
  local out={} for _,s in ipairs(n.character.get_main_inventory().get_contents()) do
    if s.quality=='normal' then out[s.name]=(out[s.name] or 0)+s.count end
  end return out
end
local function pin(e) return {name=e.name,position=xy(e.position),unit_number=e.unit_number,force_name=e.force.name,surface_index=e.surface.index} end
local function resolve(n,s)
  if not s or s.surface_index~=n.character.surface.index then return nil end
  for _,e in ipairs(n.character.surface.find_entities_filtered{name=s.name,position=s.position,radius=.01}) do
    if e.valid and same(e.position,s.position) and e.force.name==s.force_name and (not s.unit_number or s.unit_number==e.unit_number) then return e end
  end
end
local function yield(e,item)
  if not e or not e.valid or not e.minable or e.force.name~='neutral' or (e.type~='tree' and e.type~='simple-entity') then return nil end
  local p=e.prototype.mineable_properties
  if not p or not p.minable or p.required_fluid or #(p.products or {})~=1 then return nil end
  local v=p.products[1]
  if v.type~='item' or item and v.name~=item or not integer(v.amount) or v.amount<1 or (v.probability or 1)~=1 then return nil end
  return v.name,v.amount
end
local function idle(n)
  local p=n.factory_project
  return not n.current_job and not n.pending_job and not p.pending_job and not n.social_job and not n.ai_running and next(n.jobs or {})==nil
end
local function terminal(n,r)
  if not r or r.npc_id~=n.id or r.kind~='mine' or r.id~=tostring(n.id)..':'..tostring(r.job_id)
      or not equal((n.receipts or {})[r.job_id],r) or (n.completed_jobs or {})[r.job_id]~=r.id then return false end
  local ordered,seen=0,0
  for _,id in ipairs(n.receipt_order or {}) do if id==r.job_id then ordered=ordered+1 end end
  for _,v in pairs(n.receipts or {}) do if v.id==r.id then seen=seen+1 end end
  return ordered==1 and seen==1
end
local function context(n)
  local c=n and n.character;local p=n and n.factory_project
  if not c or not c.valid or c.type~='character' or n.force~=c.force then return nil,'obstruction_actor_scope_invalid' end
  local step=p and p.plan and p.plan.steps and p.plan.steps[p.index]
  local site=step and step.kind=='build' and p.plan.sites and p.plan.sites[step.target_site]
  if not site or p.status~='blocked' or p.reason~='job_deadline' or not integer(p.failures) or p.failures<3
      or not integer(p.index) or not integer(p.started_tick) or not integer(p.last_progress_tick)
      or site.surface_index~=c.surface.index or not xy(site.position) then return nil,'obstruction_blocked_clearance_scope_required' end
  if #c.surface.find_entities_filtered{name=site.name,position=site.position,radius=.05,force=n.force}>0 then return nil,'obstruction_built_site_preserved' end
  local state=n.obstruction_recovery
  for _,block in ipairs(site.clear_blocks or {}) do
    if block.surface_index==c.surface.index and block.position and block.item then
      local spec={name=block.name,position=xy(block.position),unit_number=block.unit_number,force_name='neutral',surface_index=block.surface_index}
      local goal=resolve(n,spec);local item,amount=yield(goal,block.item)
      if not goal and state and equal(state.scope.goal,spec) and (state.finished or state.issued
          and state.issued.purpose~='clear_intersecting_blocker' and equal(state.issued.target,spec)) then item,amount=state.scope.item,state.scope.yield end
      if item then
        for k=#(p.clearance_receipts or {}),1,-1 do
          local entry=p.clearance_receipts[k]
          if entry.site==step.target_site and entry.target_name==block.name and same(entry.target_position,xy(block.position))
              and entry.item==item and entry.status=='failed' and entry.reason=='job_deadline' and entry.count==0 then
            local found=nil
            for _,r in pairs(n.receipts or {}) do if r.id==entry.receipt_id then found=r end end
            if found and terminal(n,found) and found.status=='failed' and found.reason=='job_deadline' and found.item==item
                and found.count==0 and integer(found.tick) and integer(found.started_tick)
                and found.tick>=p.started_tick and found.started_tick>=p.last_progress_tick and equal(found.produced,entry.produced) then
              local history=false
              for _,f in ipairs(n.failures or {}) do
                if f.job_id==found.job_id and f.tick==found.tick and f.reason==found.reason and f.kind=='mine'
                    and f.item==item and f.target_name==block.name and same(f.target_position,spec.position) then history=true end
              end
              if history then return {project=p.id,index=p.index,started_tick=p.started_tick,last_progress_tick=p.last_progress_tick,
                key=step.target_site,plan_revision=p.plan.revision,strategy_token=copy(n.project_strategy_token),clearance=copy(site.clear_blocks),
                site={name=site.name,item=site.item,position=xy(site.position),direction=site.direction,surface_index=site.surface_index},
                goal=spec,item=item,yield=amount,prior_failures=p.failures,prior_reason=p.reason,actor_force=n.force.name,
                anchor_receipt=found.id,anchor_tick=found.tick},nil end
            end
          end
        end
      end
    end
  end
  return nil,'obstruction_authenticated_failed_clearance_required'
end
function Recovery.eligible(n)
  local s,why=context(n);if not s then return false,why end
  if not idle(n) then return false,'obstruction_actor_not_idle' end
  return true,nil,s
end
local function scope(n,state)
  local s,why=context(n)
  if not s then return nil,why end
  if state and not equal(s,state.scope) then return nil,'obstruction_scope_changed' end
  return s
end
-- Exact segment vs expanded native collision rectangle (character swept box).
local function intersects(c,from,to,e)
  local b=e.bounding_box;local a=c.prototype.collision_box
  local limits={{from.x,to.x-from.x,b.left_top.x-a.right_bottom.x,b.right_bottom.x-a.left_top.x},
    {from.y,to.y-from.y,b.left_top.y-a.right_bottom.y,b.right_bottom.y-a.left_top.y}}
  local lo,hi=0,1
  for _,v in ipairs(limits) do
    if math.abs(v[2])<1e-9 then if v[1]<v[3] or v[1]>v[4] then return false end
    else
      local q,r=(v[3]-v[1])/v[2],(v[4]-v[1])/v[2];if q>r then q,r=r,q end
      lo,hi=math.max(lo,q),math.min(hi,r);if lo>hi then return false end
    end
  end
  return true,lo
end
local function witness_valid(n,state)
  local w=state and state.witness
  if not w or w.source~='native_bound_stalled_navigation' or not equal(w.scope,state.scope)
      or not integer(w.witness_tick) or w.witness_tick>game.tick or game.tick-w.witness_tick>1200 then return false end
  for _,l in ipairs(state.ledger or {}) do
    if l.job_id==w.job_id and l.sequence==w.sequence and l.purpose~='clear_intersecting_blocker' and equal(l.witness,w)
        and terminal(n,(n.receipts or {})[w.job_id]) then return true end
  end
  return false
end
function Recovery.blocker_eligible(n,target)
  local state=n and n.obstruction_recovery;local s,why=scope(n,state)
  if not s then return false,why end
  if not witness_valid(n,state) then return false,'obstruction_native_route_witness_required' end
  local e=resolve(n,target)
  if not e or e.force.name~='neutral' or target.force_name~='neutral' or e.surface~=n.character.surface then return false,'obstruction_neutral_native_blocker_required' end
  if equal(pin(e),s.goal) then return false,'obstruction_original_goal_excluded' end
  local item,amount=yield(e)
  if not item then return false,'obstruction_deterministic_yield_required' end
  local c=n.character;local dx,dy=c.position.x-e.position.x,c.position.y-e.position.y
  if not c.can_reach_entity(e) or math.sqrt(dx*dx+dy*dy)>math.max(.3,math.min(c.resource_reach_distance,c.reach_distance)-.45) then
    return false,'obstruction_blocker_unreachable'
  end
  if not ((e.prototype.collision_mask.layers or {}).player) then return false,'obstruction_character_collision_required' end
  local hit,along=intersects(c,c.position,state.witness.waypoint,e)
  if not hit then return false,'obstruction_swept_route_intersection_required' end
  if c.get_main_inventory().get_insertable_count{name=item,quality='normal'}<amount then return false,'obstruction_inventory_capacity_required' end
  for _,l in ipairs(state.ledger or {}) do if l.purpose=='clear_intersecting_blocker' and equal(l.target,pin(e)) then
    return false,'obstruction_blocker_attempt_already_recorded' end end
  return true,nil,{target=pin(e),item=item,count=amount,intersection_parameter=along,waypoint=copy(state.witness.waypoint),actor_position=xy(c.position)},e
end
function Recovery.blocker_proposal(n)
  local state=n and n.obstruction_recovery
  if not state or state.phase~='clear' or not witness_valid(n,state) then return nil,'obstruction_native_route_witness_required' end
  local list={}
  for _,e in ipairs(n.character.surface.find_entities_filtered{position=n.character.position,radius=n.character.resource_reach_distance,type={'tree','simple-entity'},force='neutral'}) do
    local okay,_,p=Recovery.blocker_eligible(n,pin(e));if okay then list[#list+1]=p end
  end
  table.sort(list,function(a,b)
    return a.intersection_parameter<b.intersection_parameter or a.intersection_parameter==b.intersection_parameter
      and (a.target.position.x<b.target.position.x or a.target.position.x==b.target.position.x and a.target.position.y<b.target.position.y)
  end)
  if #list==0 then return nil,'obstruction_no_eligible_intersecting_blocker' end
  return list[1]
end
local function issue(n,state,s,e,item,count,purpose,tick,deadline,proof)
  if state.sequence>=MAX_JOBS then return nil,'obstruction_job_budget_exhausted' end
  state.sequence=state.sequence+1
  state.issued={sequence=state.sequence,scope=copy(s),kind='mine',target=pin(e),item=item,count=count,purpose=purpose,
    tick=tick,carried_before=stocks(n),intersection=proof}
  return {kind='mine',target=e,item=item,count=count,deadline=tick+deadline,upkeep_source='obstruction_recovery'}
end
function Recovery.next(n,tick)
  local state=n and n.obstruction_recovery;local s,why=scope(n,state)
  if not s then return nil,why end
  if state and state.finished then return nil,'obstruction_goal_cleared' end
  if state and state.held then return nil,state.reason end
  if state and state.issued then return nil,'obstruction_worker_pending' end
  if not idle(n) then return nil,'obstruction_actor_not_idle' end
  if not state then state={scope=copy(s),phase='probe',sequence=0,goal_attempts=0,clear_attempts=0,ledger={},started_tick=tick};n.obstruction_recovery=state end
  if state.phase=='clear' then
    if state.clear_attempts>=MAX_CLEAR then state.held=true;state.reason='obstruction_clear_budget_exhausted';return nil,state.reason end
    local proposal,reason=Recovery.blocker_proposal(n)
    if not proposal then state.held=true;state.reason=reason;return nil,reason end
    local okay,refusal,proof,e=Recovery.blocker_eligible(n,proposal.target)
    if not okay then return nil,refusal end
    state.clear_attempts=state.clear_attempts+1
    return issue(n,state,s,e,proof.item,proof.count,'clear_intersecting_blocker',tick,600,proof)
  end
  if state.goal_attempts>=MAX_GOAL then state.held=true;state.reason='obstruction_goal_budget_exhausted';return nil,state.reason end
  local e=resolve(n,s.goal);local item,amount=yield(e,s.item)
  if not item then return nil,'obstruction_original_goal_changed' end
  if n.character.get_main_inventory().get_insertable_count{name=item,quality='normal'}<amount then return nil,'obstruction_inventory_capacity_required' end
  state.goal_attempts=state.goal_attempts+1
  return issue(n,state,s,e,item,amount,state.phase=='probe' and 'observe_original_goal_route' or 'retry_original_goal',tick,state.phase=='probe' and 600 or 1200)
end
function Recovery.bind(n,id)
  local state=n.obstruction_recovery;local i=state and state.issued
  if not i or i.job_id or type(id)~='string' or id=='' or n.pending_job~=id or n.factory_project.pending_job~=id then return false end
  local s=scope(n,state);if not s then return false end
  local e=resolve(n,i.target);if not yield(e,i.item) then return false end
  if i.purpose=='clear_intersecting_blocker' then local okay=Recovery.blocker_eligible(n,i.target);if not okay then return false end end
  i.job_id=id;return true
end
function Recovery.reject_unbound(n,reason)
  local state=n.obstruction_recovery;local i=state and state.issued
  if not i or i.job_id then return false end
  state.issued=nil;state.held=true;state.reason=reason or 'obstruction_enqueue_rejected';return true
end
function Recovery.observe_navigation(n,tick)
  local state=n.obstruction_recovery;local i=state and state.issued;local j=n.current_job
  if not i or not i.job_id or i.purpose=='clear_intersecting_blocker' or not j or j.id~=i.job_id
      or j.kind~='mine' or j.item~=i.item or j.count~=i.count or j.upkeep_source~='obstruction_recovery'
      or not j.target or not j.target.valid or not equal(pin(j.target),i.target)
      or n.pending_job~=j.id or n.factory_project.pending_job~=j.id or not scope(n,state) then return false end
  local nav=j.navigation;local w=nav and nav.path and nav.path[nav.index]
  if not w or not nav.progress_tick or tick-nav.progress_tick<60 or nav.recovery then return false end
  local waypoint=xy(w.position or w)
  if not waypoint then return false end
  local route={source='native_bound_stalled_navigation',scope=copy(state.scope),job_id=j.id,sequence=i.sequence,
    witness_tick=tick,actor_position=xy(n.character.position),waypoint=waypoint,revision=nav.revision,
    requests=nav.requests,index=nav.index,progress_tick=nav.progress_tick}
  local hit=false
  for _,e in ipairs(n.character.surface.find_entities_filtered{position=n.character.position,radius=n.character.resource_reach_distance,type={'tree','simple-entity'},force='neutral'}) do
    if not equal(pin(e),state.scope.goal) and yield(e) and intersects(n.character,n.character.position,waypoint,e) then hit=true end
  end
  if hit then i.witness=route;return true end
  return false
end
function Recovery.receipt_matches_binding(n,r)
  local state=n and n.obstruction_recovery;local i=state and state.issued;local p=n and n.factory_project
  return i and i.job_id and i.kind=='mine' and i.sequence==state.sequence and equal(i.scope,state.scope) and scope(n,state)~=nil
    and p and p.pending_job==i.job_id and n.pending_job==i.job_id and r and r.job_id==i.job_id
    and r.kind=='mine' and terminal(n,r) and (not r.target_force or r.target_force==i.target.force_name)
    and (not r.target_surface_index or r.target_surface_index==i.target.surface_index)
    and (not r.target_unit_number or r.target_unit_number==i.target.unit_number)
    and integer(r.started_tick) and r.started_tick>=i.tick and integer(r.tick) and r.tick>=r.started_tick or false
end
function Recovery.on_receipt(n,r)
  if not Recovery.receipt_matches_binding(n,r) then return false,'obstruction_receipt_binding_mismatch' end
  local state=n.obstruction_recovery;local i=state.issued;local s,reason=scope(n,state)
  if not s then state.held=true;state.reason=reason;return false,reason end
  local earned=integer(r.count) and r.count or -1
  local after=stocks(n);local expected=copy(i.carried_before);expected[i.item]=(expected[i.item] or 0)+math.max(0,earned)
  if expected[i.item]==0 then expected[i.item]=nil end
  if r.item~=i.item or earned<0 or earned>i.count or not equal(r.produced,{[i.item]=earned}) or next(r.consumed or {})~=nil
      or next(r.refunded or {})~=nil or not equal(expected,after) then reason='obstruction_receipt_conservation_mismatch'
  elseif r.status=='complete' and (earned~=i.count or resolve(n,i.target)~=nil) then reason='obstruction_native_target_completion_required'
  elseif r.status~='complete' and earned~=0 then reason='obstruction_partial_yield_quarantined'
  end
  local entry={id=r.id,job_id=r.job_id,sequence=i.sequence,scope=copy(i.scope),purpose=i.purpose,target=copy(i.target),
    status=r.status,reason=reason or r.reason,item=r.item,count=r.count,produced=copy(r.produced),tick=r.tick,witness=copy(i.witness),
    intersection=copy(i.intersection),carried_before=copy(i.carried_before),carried_after=after}
  state.ledger[#state.ledger+1]=entry;while #state.ledger>32 do table.remove(state.ledger,1) end
  state.issued=nil
  if reason then state.held=true;state.reason=reason;return true,reason end
  if r.status=='complete' then
    if i.purpose=='clear_intersecting_blocker' then state.phase='resume';state.reason=nil
    else state.finished=true;state.phase='complete';state.completed_receipt=r.id;state.completed_tick=r.tick;state.reason=nil end
  elseif i.purpose=='clear_intersecting_blocker' then state.held=true;state.reason='obstruction_clear_no_yield'
  elseif i.witness then state.witness=copy(i.witness);state.phase='clear';state.reason=nil
  else state.held=true;state.reason='obstruction_no_native_stalled_route_witness' end
  return true,state.reason
end
function Recovery.finished(n)
  local state=n and n.obstruction_recovery
  if not state or not state.finished or state.issued or scope(n,state)==nil or resolve(n,state.scope.goal)~=nil then return false end
  local last=(state.ledger or {})[#(state.ledger or {})];local r=last and (n.receipts or {})[last.job_id]
  return last and r and terminal(n,r) and last.id==state.completed_receipt and last.status=='complete'
    and last.purpose~='clear_intersecting_blocker' and equal(last.target,state.scope.goal) and equal(last.scope,state.scope)
    and r.status=='complete' and r.item==state.scope.item and r.count==state.scope.yield and last.count==r.count
    and equal(r.produced,{[r.item]=r.count}) and equal(r.produced,last.produced) and last.tick==state.completed_tick
    and equal(last.carried_after,stocks(n)) or false
end
function Recovery.resume_proposal(n)
  local state=n and n.obstruction_recovery
  if not Recovery.finished(n) or not idle(n) then return nil,'obstruction_authenticated_goal_completion_required' end
  local site=state.scope.site
  if not n.character.surface.can_place_entity{name=site.name,position=site.position,direction=site.direction,
      force=n.force,build_check_type=defines.build_check_type.manual} then return nil,'obstruction_original_footprint_still_blocked' end
  local receipts={};for _,l in ipairs(state.ledger)do receipts[#receipts+1]=l.id end
  return {scope=copy(state.scope),receipt_id=state.completed_receipt,completed_tick=state.completed_tick,prior_failures=state.scope.prior_failures,
    prior_reason=state.scope.prior_reason,sequence=state.sequence,receipts=receipts,native_goal_absent=true,legal_original_footprint=true,
    method='separate_native_obstruction_and_goal_receipts'}
end
function Recovery.observe(n) return n and n.obstruction_recovery and copy(n.obstruction_recovery) or {} end
return Recovery
