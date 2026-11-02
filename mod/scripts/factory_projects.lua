-- Executes a saved, serializable construction plan one physical Worker job at a
-- time. The planner chooses sites and material order; this module never creates
-- an item or entity. The factory loop owns scheduling and Worker.enqueue.
local Projects = {}
local Planner = require("scripts.construction_planner")
local Advanced = require("scripts.advanced_projects")
local Defense = require("scripts.defense_projects")
local Oil = require("scripts.oil_projects")
local Rocket = require("scripts.rocket_projects")
local Worker = require("scripts.worker")
local MiningRecovery = require("scripts.mining_recovery")
local ConveyorRecovery = require("scripts.conveyor_recovery")
local SupplyRecovery = require("scripts.construction_supply_recovery")
local CraftSupply = require("scripts.craft_supply_recovery")
local Obstruction = require("scripts.obstruction_recovery")

local WORKER_KINDS = {move=true, mine=true, craft=true, build=true, transfer=true,
  configure_recipe=true,configure_direction=true,belt_pickup=true, select_research=true,request_platform=true,launch_platform=true}
local RAW_DEPOSITS={coal=true,wood=true,stone=true,["iron-ore"]=true,["copper-ore"]=true}

local function position_of(value)
  if not value then return nil end
  local x, y = value.x or value[1], value.y or value[2]
  if type(x) ~= "number" or type(y) ~= "number" then return nil end
  return {x=x,y=y}
end

local function inventory_count(npc, item, site, inventory)
  local inv
  if site then
    local entity = Projects.resolve_site(npc, site)
    if not entity then return nil end
    if inventory then inv = entity.get_inventory(inventory)
    elseif entity.get_output_inventory then inv = entity.get_output_inventory() end
  else
    inv = npc.character.get_main_inventory()
  end
  if not inv or not inv.valid then return nil end
  return inv.get_item_count(item)
end

local function site_spec(npc, key)
  local project = npc.factory_project
  return project and project.plan and project.plan.sites and project.plan.sites[key]
end

-- The saved unit number is a hint, never authority. A replacement entity must
-- still match this actor's own force, expected prototype and exact plan site.
function Projects.resolve_site(npc, key)
  local site = site_spec(npc, key)
  if not site or not npc.character or not npc.character.valid then return nil end
  local surface = game.surfaces[site.surface_index or npc.character.surface.index]
  if not surface then return nil end
  local position = position_of(site.position)
  if not position then return nil end
  local matches = surface.find_entities_filtered{position=position,radius=0.15,
    name=site.name,force=npc.force}
  for _, entity in ipairs(matches) do
    if entity.valid and math.abs(entity.position.x-position.x)<0.15
        and math.abs(entity.position.y-position.y)<0.15 then
      return entity
    end
  end
  return nil
end

local function wait_satisfied(npc, condition)
  if type(condition) ~= "table" then return false,"invalid_wait" end
  local handled,reason=Rocket.wait_satisfied(npc,condition)
  if handled~=nil then return handled,reason end
  local kind = condition.kind
  if kind == "technology" or kind == "researched" then
    local technology = npc.force.technologies[condition.technology or condition.name]
    if not technology then return false,"unknown_technology" end
    return technology.researched, "waiting_for_technology"
  elseif kind == "entity" then
    return Projects.resolve_site(npc,condition.target_site) ~= nil,"waiting_for_entity"
  elseif kind == "inventory" then
    local amount = inventory_count(npc,condition.item,condition.target_site,condition.inventory)
    if not amount then return false,"inventory_unavailable" end
    return amount >= (condition.count or 1),"waiting_for_inventory"
  elseif kind == "products_finished" then
    local entity = Projects.resolve_site(npc,condition.target_site)
    if not entity then return false,"entity_missing" end
    return entity.products_finished >= (condition.count or 1),"waiting_for_products"
  elseif kind == "item_consumption" then
    if type(condition.item)~="string" or type(condition.count)~="number" then return false,"invalid_consumption_wait" end
    local consumed=npc.force.get_item_production_statistics(npc.character.surface).get_output_count(condition.item)
    return consumed>=condition.count,"waiting_for_native_consumption"
  elseif kind == "powered" then
    local entity = Projects.resolve_site(npc,condition.target_site)
    if not entity then return false,"entity_missing" end
    return entity.is_connected_to_electric_network() and entity.energy > 0,
      "waiting_for_power"
  elseif kind == "fluid" then
    local entity = Projects.resolve_site(npc,condition.target_site)
    if not entity then return false,"entity_missing" end
    local amount = 0
    for i=1,entity.fluids_count do
      local fluid = entity.get_fluid(i)
      if fluid and (not condition.fluid or fluid.name==condition.fluid) then
        amount = amount + fluid.amount
      end
    end
    return amount >= (condition.amount or 1),"waiting_for_fluid"
  end
  return false,"unknown_wait_kind"
end

local function carried(npc,item)
  return npc.character.get_main_inventory().get_item_count(item)
end
local function native_resource_near(npc,item,position,radius)
  if not position or not npc.character or not npc.character.valid then return nil end
  local best,distance
  for _,entity in ipairs(npc.character.surface.find_entities_filtered{
      name=item,position=position,radius=radius}) do
    if entity.valid and entity.minable and entity.force.name=="neutral" then
      local dx,dy=entity.position.x-position.x,entity.position.y-position.y
      local d=dx*dx+dy*dy
      if not best or d<distance or (d==distance and
          (entity.position.x<best.position.x or
           (entity.position.x==best.position.x and entity.position.y<best.position.y))) then
        best,distance=entity,d
      end
    end
  end
  return best
end

-- Keep a waiting furnace running using only mined fuel/ore and actual inventory
-- transfers. This is deliberately limited to the furnace named by the plan.
local function waiting_upkeep(npc,condition)
  local defense=Defense.maintenance(npc,condition)
  if defense then defense.upkeep_source="defense";return defense end
  local rocket=Rocket.maintenance(npc,condition)
  if rocket then rocket.upkeep_source="rocket";return rocket end
  local oil=Oil.maintenance(npc,condition)
  if oil then oil.upkeep_source="oil";return oil end
  local advanced = Advanced.maintenance(npc,condition)
  if advanced then advanced.upkeep_source="advanced";return advanced end
  -- Once the automated line exists, leave plates/gears/science on its belts.
  -- Only fuel production machines; native inserters must move the products.
  if Projects.resolve_site(npc,"science_assembler") then
    return Projects.maintenance(npc,true)
  end
  local boiler=Projects.resolve_site(npc,"boiler")
  if boiler then
    local boiler_fuel=boiler.get_fuel_inventory()
    if boiler_fuel and boiler_fuel.valid and boiler_fuel.get_item_count("coal")<1 then
      if carried(npc,"coal")<4 then return {kind="mine",item="coal",count=12} end
      return {kind="transfer",target=boiler,item="coal",mode="deposit",count=4}
    end
  end
  local source_key=condition.source_site
  if not source_key and condition.kind=="technology" then
    if condition.technology=="steam-power" then source_key="iron_furnace"
    elseif condition.technology=="electronics" then source_key="copper_furnace" end
  end
  if not source_key then return nil end
  local furnace=Projects.resolve_site(npc,source_key)
  if not furnace then return nil end
  local ore=source_key=="copper_furnace" and "copper-ore" or "iron-ore"
  local plate=source_key=="copper_furnace" and "copper-plate" or "iron-plate"
  local drill=source_key=="copper_furnace" and Projects.resolve_site(npc,"copper_drill") or npc.miner
  local out=furnace.get_output_inventory()
  local available=out and out.valid and out.get_item_count(plate) or 0
  local need=condition.kind=="inventory" and condition.item==plate
      and math.max(0,(condition.count or 1)-carried(npc,plate)) or 0
  if available>0 and (need>0 or available>=20) then
    return {kind="transfer",target=furnace,item=plate,mode="withdraw",
      count=math.min(available,math.max(need,8))}
  end
  local fuel=furnace.get_fuel_inventory()
  if fuel and fuel.valid and fuel.get_item_count("coal")<1 then
    if carried(npc,"coal")<4 then return {kind="mine",item="coal",count=12,
      target=native_resource_near(npc,"coal",npc.home,128)} end
    return {kind="transfer",target=furnace,item="coal",mode="deposit",count=4}
  end
  if drill and drill.valid then
    local drill_fuel=drill.get_fuel_inventory()
    if drill_fuel and drill_fuel.valid and drill_fuel.get_item_count("coal")<1 then
      if carried(npc,"coal")<4 then return {kind="mine",item="coal",count=12,
        target=native_resource_near(npc,"coal",npc.home,128)} end
      return {kind="transfer",target=drill,item="coal",mode="deposit",count=4}
    end
  end
  local input=furnace.get_inventory(defines.inventory.crafter_input)
  if input and input.valid and input.get_item_count(ore)<2 then
    if carried(npc,ore)<4 then
      -- Nearby copper is discovered from the actor's real position. The
      -- Worker may retarget a depleted tile; distant surveyed outposts use
      -- its separate saved-landmark fallback, not an omniscient ore grant.
      return {kind="mine",item=ore,count=8,search_radius=128}
    end
    return {kind="transfer",target=furnace,item=ore,mode="deposit",count=4,
      inventory=defines.inventory.crafter_input}
  end
  return nil
end

function Projects.start(npc, project_id)
  if npc.factory_project and npc.factory_project.status == "active" then
    return false,"project_already_active"
  end
  local plan, reason
  if project_id=="green_science" or project_id=="electric_mining" or project_id=="steel_processing" then
    plan,reason=Advanced.plan(npc,project_id)
  elseif project_id=="basic_defense" then plan,reason=Defense.plan(npc,project_id)
  elseif project_id=="oil_processing" or project_id=="chemical_science" then plan,reason=Oil.plan(npc,project_id)
  elseif project_id=="rocket_materials" or project_id=="platform_launch" then plan,reason=Rocket.plan(npc,project_id)
  else plan,reason=Planner.plan(npc,project_id) end
  if not plan then return false,reason or "no_plan" end
  if type(plan) ~= "table" or type(plan.steps) ~= "table" or #plan.steps == 0
      or type(plan.sites) ~= "table" then return false,"invalid_plan" end
  npc.factory_project = {id=plan.id or project_id,plan=plan,index=1,status="active",
    started_tick=game.tick,failures=0}
  return true,npc.factory_project
end

local function make_worker_job(npc, step)
  local job = {kind=step.kind,item=step.item,count=step.count,recipe=step.recipe,
    entity_name=step.entity_name,direction=step.direction,mode=step.mode,
    inventory=step.inventory,search_radius=step.search_radius,technology=step.technology,
    platform_name=step.platform_name}
  if step.kind == "build" then
    local site = site_spec(npc,step.target_site)
    if not site then return nil,"unknown_site" end
    if site.surface_index and site.surface_index ~= npc.character.surface.index then
      return nil,"wrong_surface"
    end
    job.position = position_of(site.position)
    job.entity_name = site.name
    job.item = site.item
    job.direction = site.direction
    if not job.position then return nil,"invalid_site_position" end
  elseif step.target_site then
    local entity = Projects.resolve_site(npc,step.target_site)
    if not entity then return nil,"target_site_missing" end
    job.target=entity
  elseif step.position then
    job.position=position_of(step.position)
    if not job.position then return nil,"invalid_step_position" end
  end
  if step.kind == "configure_recipe" then
    if not job.target or not job.recipe then return nil,"invalid_recipe_step" end
    local site=step.target_site and site_spec(npc,step.target_site)
    job.direction=step.direction or (site and site.direction)
  elseif step.kind=="configure_direction" then
    local site=step.target_site and site_spec(npc,step.target_site)
    job.direction=step.direction or (site and site.direction)
    if not job.target or job.direction==nil then return nil,"invalid_direction_step" end
  end
  return job
end
local function clear_job_for_site(npc,step)
  local site=step.target_site and site_spec(npc,step.target_site)
  if not site then return nil,"unknown_site" end
  if not site.clear_blocks or #site.clear_blocks==0 then return nil,nil end
  local surface=npc.character and npc.character.valid and npc.character.surface
  if not surface or (site.surface_index and site.surface_index~=surface.index) then return nil,"wrong_surface" end
  for _,block in ipairs(site.clear_blocks or {}) do
    if block.surface_index~=surface.index then return nil,"clearance_wrong_surface" end
    for _,entity in ipairs(surface.find_entities_filtered{name=block.name,
        position=block.position,radius=0.05}) do
      if entity.valid and math.abs(entity.position.x-block.position.x)<0.05
          and math.abs(entity.position.y-block.position.y)<0.05
          and (not block.unit_number or entity.unit_number==block.unit_number) then
        if entity.force.name~="neutral" or not entity.minable
            or (entity.type~="tree" and entity.type~="simple-entity") then
          return nil,"clearance_target_changed"
        end
        local props=entity.prototype.mineable_properties
        local yields=false
        for _,product in ipairs(props and props.products or {}) do
          if product.type=="item" and product.name==block.item
              and (product.amount or product.amount_min or 0)>=1
              and (not product.probability or product.probability==1) then yields=true;break end
        end
        if not yields then return nil,"clearance_yield_changed" end
        return {kind="mine",target=entity,item=block.item,count=1},nil
      end
    end
  end
  return nil,nil
end

-- Returns one of: {kind="job",job=...}, {kind="wait",reason=...},
-- {kind="complete"}, or {kind="blocked"}.
-- Calls are idempotent until a matching receipt is delivered to on_receipt.
-- A planner's preferred standing point is optional when the next paid build
-- is already legal from the actual body. Never treat an arbitrary move/scout
-- as satisfied, and never synthesize a movement or construction receipt.
function Projects.can_skip_build_approach(npc)
  local p,c=npc.factory_project,npc.character
  if not p or not p.plan or not c or not c.valid or c.force~=npc.force then return false end
  local step,next_step=p.plan.steps[p.index],p.plan.steps[p.index+1]
  if not step or step.kind~="move" or step.target_site or step.purpose
      or not next_step or next_step.kind~="build" then return false end
  local site=p.plan.sites and p.plan.sites[next_step.target_site]
  local goal=position_of(step.position)
  local stance=site and position_of(site.body_staging)
  if not site or not goal or not stance or math.abs(goal.x-stance.x)>0.01
      or math.abs(goal.y-stance.y)>0.01 or not site.item or not site.name
      or (site.surface_index and site.surface_index~=c.surface.index)
      or Projects.resolve_site(npc,next_step.target_site) then return false end
  local point=position_of(site.position)
  if not point then return false end
  local healthy=false
  local inventory=c.get_main_inventory()
  for i=1,#inventory do
    local stack=inventory[i]
    if stack.valid_for_read and stack.name==site.item and stack.quality.name=="normal" and stack.health==1 then
      healthy=true;break
    end
  end
  if not healthy then return false end
  local args={name=site.name,position=point,direction=site.direction,force=npc.force}
  if not c.can_place_entity(args) then return false end
  args.build_check_type=defines.build_check_type.manual
  if not c.surface.can_place_entity(args) then return false end
  return true,{site=next_step.target_site,position=point,approach=goal,
    body_position={x=c.position.x,y=c.position.y},item=site.item}
end

function Projects.resume_reachable_build_approach(npc,tick)
  local p=npc.factory_project
  if not p or p.status~="blocked" or (p.reason~="no_path" and p.reason~="movement_stalled")
      or p.pending_job or npc.pending_job or npc.current_job
      or (npc.jobs and #npc.jobs>0) then return false end
  if npc.suspended and (npc.phase~="needs-help:"..p.reason or npc.last_problem~=p.reason
      or npc.obstruction_recovery or npc.blocked_factory_upkeep) then return false end
  for _,key in ipairs({"craft_supply_recovery","construction_supply_recovery","conveyor_recovery","mining_recovery"}) do
    local state=npc[key]
    if state and (state.quarantined or state.issued) then return false end
  end
  local okay,evidence=Projects.can_skip_build_approach(npc)
  if not okay then return false end
  p.approach_resumes=p.approach_resumes or {}
  local key=tostring(p.index)
  if p.approach_resumes[key] then return false end
  p.approach_resumes[key]={tick=tick,prior_reason=p.reason,prior_failures=p.failures,evidence=evidence,
    prior_suspended=npc.suspended or false,prior_phase=npc.phase,prior_last_problem=npc.last_problem}
  p.status="active";p.reason=nil
  if npc.suspended then npc.suspended=nil;npc.phase="reachable-build-resumed" end
  -- Failure history remains until an ordinary paid Worker receipt succeeds.
  return true
end

function Projects.next(npc,tick)
  local project = npc.factory_project
  if not project then return {kind="blocked",reason="no_project"} end
  if project.status ~= "active" then return {kind=project.status,reason=project.reason} end
  if project.pending_job then return {kind="wait",reason="worker_pending"} end
  local craft_state=npc.craft_supply_recovery
  if craft_state and craft_state.quarantined then
    -- An authentic terminal paid receipt may require recursive accounting.
    -- Reconcile only while idle, before advancing the old step or upkeep.
    local recovered,reason=CraftSupply.rehabilitate_quarantine(npc,tick)
    if not recovered then
      return {kind="wait",reason=reason or "craft_supply_receipt_quarantined"}
    end
  end
  local function before_action()
    local craft,craft_reason=CraftSupply.next(npc,tick,Projects.resolve_site)
    if craft then return {kind="job",job=craft,index=project.index,maintenance=true} end
    if craft_reason=="craft_ready" then return nil end
    if craft_reason~="current_handcraft_required" and craft_reason~="craft_actor_scope_invalid"
        and craft_reason~="craft_category_unsupported" and craft_reason~="craft_single_product_required"
        and craft_reason~="craft_deterministic_named_item_required"
        and craft_reason~="craft_fluid_or_fractional_input_unsupported" then
      return {kind="wait",reason=craft_reason or "craft_supply_wait"}
    end
    local supply,supply_reason=SupplyRecovery.next(npc,tick,Projects.resolve_site)
    if supply then return {kind="job",job=supply,index=project.index,maintenance=true} end
    -- Preserve the current placement while its paid supply is being acquired.
    -- A ready placement takes precedence over other inventory consumers.
    if supply_reason=="placement_item_ready" then return nil end
    if supply_reason~="current_plain_build_required"
        and supply_reason~="current_build_already_exists"
        and supply_reason~="actor_scope_invalid" then
      return {kind="wait",reason=supply_reason or "construction_supply_wait"}
    end
    local step=project.plan.steps[project.index]
    if step and step.kind=="transfer" and step.mode=="deposit" then
      local job=make_worker_job(npc,step)
      if job and job.target and job.target.valid and job.target.force==npc.force
          and job.target.surface==npc.character.surface and carried(npc,job.item)>=(job.count or 1) then
        return nil
      end
    end
    local conveyor=ConveyorRecovery.next(npc,tick,Projects.resolve_site)
    if conveyor then return {kind="job",job=conveyor,index=project.index,maintenance=true} end
    local recovery=MiningRecovery.next(npc,nil,tick,Projects.resolve_site)
    if recovery then return {kind="job",job=recovery,index=project.index,maintenance=true} end
  end
  for _=1,16 do -- bounded progression through already-satisfied checks
    local step = project.plan.steps[project.index]
    if not step then
      project.status="complete"; project.completed_tick=tick
      return {kind="complete"}
    end
    local skip_approach,evidence=Projects.can_skip_build_approach(npc)
    if step.kind=="move" and skip_approach then
      project.approach_skip_history=project.approach_skip_history or {}
      project.approach_skip_history[#project.approach_skip_history+1]={tick=tick,index=project.index,
        reason="next_paid_build_already_reachable",evidence=evidence}
      while #project.approach_skip_history>64 do table.remove(project.approach_skip_history,1) end
      project.index=project.index+1
    elseif step.kind=="craft" and CraftSupply.finished(npc) then
      -- Actual original and recovery batches satisfy this one step once.
      -- Keep its original count and all paid receipts for later inspection.
      project.index=project.index+1;project.last_progress_tick=tick
    elseif step.kind == "wait" then
      local okay,reason=wait_satisfied(npc,step.condition)
      if not okay then
        local recovery=before_action()
        if recovery then return recovery end
        local upkeep=waiting_upkeep(npc,step.condition)
        if upkeep then return {kind="job",job=upkeep,index=project.index,maintenance=true} end
        return {kind="wait",reason=reason}
      end
      project.index=project.index+1;project.last_progress_tick=tick
    elseif step.kind == "research" then
      local tech=npc.force.technologies[step.technology]
      if not tech then return {kind="blocked",reason="unknown_technology"} end
      if tech.researched then project.index=project.index+1
      else
        local recovery=before_action()
        if recovery then return recovery end
        return {kind="job",job={kind="select_research",technology=step.technology},index=project.index}
      end
    elseif WORKER_KINDS[step.kind] then
      -- Re-evaluate after every satisfied check advanced in this same call.
      local recovery=before_action()
      if recovery then return recovery end
      if step.kind=="build" then
        project.pending_clearance=nil
        local clearing,clear_reason=clear_job_for_site(npc,step)
        if clear_reason then return {kind="blocked",reason=clear_reason} end
        if clearing then
          project.pending_clearance={site=step.target_site,item=clearing.item,
            target_name=clearing.target.name,
            target_position={x=clearing.target.position.x,y=clearing.target.position.y},
            target_unit_number=clearing.target.unit_number}
          return {kind="job",job=clearing,index=project.index,
            maintenance=true,clearing_site=step.target_site}
        end
      end
      local job,reason=make_worker_job(npc,step)
      if not job then return {kind="blocked",reason=reason} end
      if job.kind=="transfer" and job.mode=="deposit" and RAW_DEPOSITS[job.item]
          and job.target and job.target.valid and job.target.force==npc.force
          and job.target.surface==npc.character.surface then
        local deficit=math.max(0,(job.count or 1)-carried(npc,job.item))
        if deficit>0 then
          -- Native burners may consume fuel while other construction work is
          -- underway. Acquire the actual shortfall before this fixed deposit;
          -- the maintenance receipt leaves its construction step pending.
          return {kind="job",job={kind="mine",item=job.item,count=deficit,
            search_radius=128},index=project.index,maintenance=true}
        end
      end
      return {kind="job",job=job,index=project.index}
    else
      project.status="blocked"; project.reason="unknown_step_kind"
      return {kind="blocked",reason=project.reason}
    end
  end
  return {kind="wait",reason="step_progress_budget"}
end

-- Resume a temporary shortage without resetting other failed construction.
-- This preserves old receipts/failures and never moves stock or grants items.
function Projects.recover_raw_supply(npc,tick)
  local project=npc.factory_project
  if not project or project.status~="blocked"
      or (project.reason~="source_empty" and project.reason~="partial_transfer")
      or tick<(project.raw_supply_retry_at or 0) then return false end
  if type(project.plan)~="table" or type(project.plan.steps)~="table" then return false end
  if not npc.character or not npc.character.valid or project.pending_job
      or npc.pending_job or npc.current_job or #(npc.jobs or {})>0 then return false end
  local step=project.plan.steps[project.index]
  if not step or step.kind~="transfer" or step.mode~="deposit" or not RAW_DEPOSITS[step.item]
      or type(step.count)~="number" or step.count<=0 then return false end
  local job=make_worker_job(npc,step)
  local target=job and job.target
  if not target or not target.valid or target.force~=npc.force
      or target.surface~=npc.character.surface then return false end
  local inventory
  if job.inventory then inventory=target.get_inventory(job.inventory)
  else inventory=target.get_fuel_inventory() end
  if not inventory or not inventory.valid
      or inventory.get_insertable_count{name=step.item,quality="normal"}<=0 then return false end
  project.raw_supply_recoveries=(project.raw_supply_recoveries or 0)+1
  project.raw_supply_recovery_history=project.raw_supply_recovery_history or {}
  local history=project.raw_supply_recovery_history
  history[#history+1]={tick=tick,index=project.index,reason=project.reason,
    prior_failures=project.failures,item=step.item,remaining=step.count}
  while #history>32 do table.remove(history,1) end
  project.raw_supply_retry_at=tick+math.min(72000,
    2400*2^math.min(5,project.raw_supply_recoveries-1))
  project.status="active";project.reason=nil;project.failures=0
  return true
end

-- Refresh only an idle, unbuilt copper module. Already-earned state and the
-- completed prefix remain in the same project; future work pays the new bill.
function Projects.recover_saved_copper(npc,tick)
  local p=npc.factory_project
  if not p or p.id~="starter_power_science" or p.status~="blocked"
      or p.reason~="placement_blocked_or_out_of_reach" or not p.plan
      or p.plan.saved_copper_refresh or not Planner.refresh_saved_copper then return false end
  if tick<(p.copper_site_retry_at or 0) then return false,"copper_site_retry_cooldown" end
  if not npc.character or not npc.character.valid or npc.force~=npc.character.force
      or p.pending_job or npc.pending_job or npc.current_job or npc.social_job or npc.ai_running
      or next(npc.jobs or {})~=nil then return false,"idle_owned_actor_required" end
  local current=p.plan.steps[p.index]
  if not current or current.kind~="build" or current.target_site~="copper_drill" then return false end
  local surface=npc.character.surface
  for _,key in ipairs({"copper_furnace","copper_ore_inserter"}) do
    local spec=p.plan.sites[key]
    local entity=Projects.resolve_site(npc,key)
    if not spec or spec.surface_index~=surface.index or not entity or not entity.valid
        or entity.force~=npc.force or entity.surface~=surface
        or spec.existing_unit_number and spec.existing_unit_number~=entity.unit_number then
      return false,"retained_owned_copper_receiver_required"
    end
    if key=="copper_ore_inserter" and (entity.name~="burner-inserter"
        or entity.direction~=defines.direction.south or spec.direction~=entity.direction) then
      return false,"supported_native_copper_pickup_required"
    end
  end
  if Projects.resolve_site(npc,"copper_drill") then return false,"built_copper_drill_preserved" end
  -- This migration supports the qualified late build group. Mixed future
  -- hardware crafting groups are refused rather than guessed from today's stock.
  local allowed={["burner-mining-drill"]=true,inserter=true,["transport-belt"]=true}
  local demands,crafts={},{}
  for i=p.index,#p.plan.steps do
    local step=p.plan.steps[i]
    if step.kind=="build" then
      local spec=p.plan.sites[step.target_site]
      if not spec then return false,"unknown_remaining_build_site" end
      if not Projects.resolve_site(npc,step.target_site) then
        if not allowed[spec.item] then return false,"unsupported_mixed_remaining_hardware" end
        demands[spec.item]=(demands[spec.item] or 0)+1
      end
    elseif step.kind=="craft" and allowed[step.recipe] then
      local recipe=npc.force.recipes[step.recipe];local per
      for _,product in ipairs(recipe and recipe.products or {}) do
        if product.name==step.recipe and product.type=="item" then per=product.amount end
      end
      if not per then return false,"unsupported_remaining_recipe_yield" end
      crafts[step.recipe]=(crafts[step.recipe] or 0)+per*step.count
    end
  end
  for _,item in ipairs({"burner-mining-drill","inserter"}) do
    if (crafts[item] or 0)~=math.max(0,(demands[item] or 0)-carried(npc,item)) then
      return false,"remaining_hardware_craft_stock_mismatch:"..item
    end
  end
  p.copper_site_recovery_attempts=(p.copper_site_recovery_attempts or 0)+1
  p.copper_site_retry_at=tick+math.min(72000,2400*2^math.min(5,p.copper_site_recovery_attempts-1))
  local plan,reason=Planner.refresh_saved_copper(npc,p)
  if not plan then p.copper_site_recovery_reason=reason;return false,reason end
  local history=p.copper_site_recovery_history or {}
  history[#history+1]={tick=tick,index=p.index,status=p.status,reason=p.reason,
    prior_failures=p.failures,started_tick=p.started_tick,
    prior_revision=p.plan.revision,refresh=plan.saved_copper_refresh}
  while #history>32 do table.remove(history,1) end
  p.copper_site_recovery_history=history
  p.plan=plan;p.status="active";p.reason=nil;p.copper_site_recovery_reason=nil
  p.copper_site_recovered_tick=tick
  return true
end

function Projects.recover_missing_build_supply(npc,tick)
  local p=npc.factory_project
  if not p or p.status~="blocked" or p.reason~="missing_healthy_normal_placement_item"
      or tick<(p.construction_supply_retry_at or 0) or p.pending_job or npc.pending_job
      or npc.current_job or npc.social_job or npc.ai_running or next(npc.jobs or {})~=nil then return false end
  local eligible=SupplyRecovery.eligible(npc)
  if not eligible then
    -- An independently delivered healthy item may already resolve the shortage.
    -- Probe a detached wrapper; no proposal or issued job is saved on the actor.
    local shadow={}
    for key,value in pairs(npc) do shadow[key]=value end
    shadow.construction_supply_recovery=nil
    local _,reason=SupplyRecovery.next(shadow,tick,Projects.resolve_site)
    if reason~="placement_item_ready" then return false end
  end
  p.construction_supply_recoveries=(p.construction_supply_recoveries or 0)+1
  p.construction_supply_retry_at=tick+math.min(72000,2400*2^math.min(5,p.construction_supply_recoveries-1))
  local history=p.construction_supply_recovery_history or {}
  history[#history+1]={tick=tick,index=p.index,reason=p.reason,prior_failures=p.failures,
    started_tick=p.started_tick,item=p.plan.steps[p.index].item}
  while #history>32 do table.remove(history,1) end
  p.construction_supply_recovery_history=history
  p.status="active";p.reason=nil
  return true
end

function Projects.recover_missing_craft_supply(npc,tick)
  local p=npc.factory_project
  if not p or p.status~="blocked" or p.reason~="missing_craft_ingredients"
      or tick<(p.craft_supply_retry_at or 0) or not CraftSupply.eligible(npc) then return false end
  p.craft_supply_recoveries=(p.craft_supply_recoveries or 0)+1
  p.craft_supply_retry_at=tick+math.min(72000,2400*2^math.min(5,p.craft_supply_recoveries-1))
  local history=p.craft_supply_recovery_history or {}
  history[#history+1]={tick=tick,index=p.index,reason=p.reason,prior_failures=p.failures,
    started_tick=p.started_tick,recipe=p.plan.steps[p.index].recipe,count=p.plan.steps[p.index].count}
  while #history>32 do table.remove(history,1) end
  p.craft_supply_recovery_history=history;p.status="active";p.reason=nil
  return true
end


-- A blocked expansion must not starve an already paid producer. This separate
-- binding never advances the project or reports its old refusal as a new one.
local function blocked_scope(npc)
  local p=npc.factory_project
  if not p or p.status~="blocked" or p.pending_job then return nil end
  return {id=p.id,index=p.index,started_tick=p.started_tick,reason=p.reason,
    failures=p.failures,force_name=npc.force.name,surface_index=npc.character.surface.index,
    strategy_token=npc.project_strategy_token}
end
local function blocked_scope_matches(npc,s)
  local actual=blocked_scope(npc)
  if not actual or not s then return false end
  for _,key in ipairs({"id","index","started_tick","reason","failures","force_name",
      "surface_index","strategy_token"}) do if actual[key]~=s[key] then return false end end
  return true
end
local function blocked_issued(npc)
  local all=npc.mining_recovery
  local pending=all and all.issued
  local plan=pending and all[pending.kind]
  local issued=plan and plan.issued
  if not issued or issued.purpose~="fuel_existing_paid_producer"
      or issued.sequence~=pending.sequence then return nil end
  return issued,pending
end
-- Blocked clearance jobs have their own authenticated scope and receipts.
local function obstruction_equal(a,b)
  -- Native table_to_json requires a table even for an absent optional token.
  return helpers.table_to_json{value=a}==helpers.table_to_json{value=b}
end
local function obstruction_quarantined(npc)
  for _,key in ipairs({"craft_supply_recovery","construction_supply_recovery",
      "conveyor_recovery","mining_recovery"}) do
    local state=npc[key];if state and state.quarantined then return true end
  end
  return false
end
local function suspension_jobs_match(npc)
  local state=npc.obstruction_recovery;local i=state and state.issued
  local p=npc.factory_project
  if npc.social_job or npc.ai_running then return false end
  if npc.pending_job or p and p.pending_job or npc.current_job or next(npc.jobs or {}) then
    if not i or not i.job_id or npc.pending_job~=i.job_id or not p or p.pending_job~=i.job_id then return false end
    if npc.current_job and npc.current_job.id~=i.job_id then return false end
    for _,job in pairs(npc.jobs or {}) do if job.id~=i.job_id then return false end end
  end
  return true
end
local function suspension_scope_matches(npc,s)
  local p=npc.factory_project;local c=npc.character
  local step=p and p.plan and p.plan.steps[p.index]
  local site=step and p.plan.sites[step.target_site]
  if not p or p.status~="blocked" or not c or not c.valid or c.force~=npc.force
      or not s or not site or step.kind~="build" or step.target_site~=s.key
      or p.id~=s.project or p.index~=s.index or p.started_tick~=s.started_tick
      or p.last_progress_tick~=s.last_progress_tick or p.reason~=s.prior_reason
      or p.failures~=s.prior_failures or p.plan.revision~=s.plan_revision
      or npc.force.name~=s.actor_force or c.surface.index~=s.site.surface_index
      or not obstruction_equal(npc.project_strategy_token,s.strategy_token)
      or not obstruction_equal(site.clear_blocks,s.clearance) then return false end
  return site.name==s.site.name and site.item==s.site.item and site.direction==s.site.direction
      and site.surface_index==s.site.surface_index
      and math.abs(site.position.x-s.site.position.x)<.01 and math.abs(site.position.y-s.site.position.y)<.01
end
function Projects.suspended_obstruction_mode(npc,tick)
  if not npc.suspended or npc.blocked_factory_upkeep or obstruction_quarantined(npc)
      or not suspension_jobs_match(npc) then return false end
  local marker=npc.obstruction_suspension
  if marker then
    return not marker.released_tick and suspension_scope_matches(npc,marker.scope)
  end
  if npc.phase~="needs-help:job_deadline" or npc.last_problem~="job_deadline"
      or (npc.step or 0)<=8 or npc.obstruction_resume then return false end
  local okay,_,scope=Obstruction.eligible(npc)
  if not okay then return false end
  npc.obstruction_suspension={tick=tick,scope=scope,prior_phase=npc.phase,
    prior_suspended=npc.suspended,prior_recovery_count=npc.recovery_count,
    prior_last_problem=npc.last_problem,prior_retry_at=npc.retry_at}
  return true
end

function Projects.obstruction_pending(npc)
  local state=npc.obstruction_recovery
  return state and state.issued and state.issued.job_id~=nil or false
end
function Projects.observe_obstruction_navigation(npc,tick)
  return Obstruction.observe_navigation(npc,tick)
end
function Projects.obstruction_maintenance(npc,tick)
  if obstruction_quarantined(npc) then return nil,"obstruction_receipt_quarantined" end
  if npc.blocked_factory_upkeep then return nil,"blocked_producer_upkeep_pending" end
  local okay,reason=Obstruction.eligible(npc)
  if not okay then return nil,reason end
  return Obstruction.next(npc,tick)
end
function Projects.bind_obstruction(npc,job_id)
  local p=npc.factory_project;local state=npc.obstruction_recovery
  if not p or p.status~="blocked" or p.pending_job or npc.pending_job~=job_id
      or not state or not state.issued or state.issued.job_id then return false end
  p.pending_job=job_id;p.pending_maintenance=true
  if Obstruction.bind(npc,job_id) then return true end
  p.pending_job=nil;p.pending_maintenance=nil
  return false
end
function Projects.reject_obstruction(npc,reason)
  return Obstruction.reject_unbound(npc,reason)
end
function Projects.resume_obstruction(npc,tick)
  if npc.obstruction_resume then return false,"obstruction_resume_already_recorded" end
  local proposal,reason=Obstruction.resume_proposal(npc)
  if not proposal then return false,reason end
  local p=npc.factory_project;local state=npc.obstruction_recovery
  local suspended=npc.obstruction_suspension
  if obstruction_quarantined(npc) or npc.suspended and (not suspended
      or suspended.released_tick or not obstruction_equal(suspended.scope,proposal.scope)
      or not suspension_scope_matches(npc,suspended.scope)) then
    return false,"obstruction_suspension_scope_required"
  end
  -- Re-read the native scope immediately before committing the once marker.
  local again=Obstruction.resume_proposal(npc)
  if not again or helpers.table_to_json(again)~=helpers.table_to_json(proposal)
      or not p or p.status~="blocked" or p.pending_job or npc.pending_job
      or p.id~=proposal.scope.project or p.index~=proposal.scope.index
      or p.started_tick~=proposal.scope.started_tick or p.reason~=proposal.prior_reason
      or p.failures~=proposal.prior_failures or not state or state.issued then
    return false,"obstruction_resume_scope_changed"
  end
  npc.obstruction_resume={tick=tick,proposal=proposal,prior_status=p.status,
    prior_reason=p.reason,prior_failures=p.failures,prior_index=p.index,
    prior_started_tick=p.started_tick,prior_last_progress_tick=p.last_progress_tick}
  p.status="active";p.reason=nil
  if suspended and not suspended.released_tick and npc.suspended
      and obstruction_equal(suspended.scope,proposal.scope) then
    suspended.released_tick=tick;suspended.resume_receipt=proposal.receipt_id
    npc.suspended=nil
  end
  -- Ordinary subsequent paid construction owns progress and failure reset.
  return true
end
function Projects.on_obstruction_receipt(npc,receipt,tick)
  if not Projects.obstruction_pending(npc) then return false end
  if not Obstruction.receipt_matches_binding(npc,receipt) then
    return false,"obstruction_receipt_binding_mismatch"
  end
  local handled,reason=Obstruction.on_receipt(npc,receipt)
  if not handled then return false,reason end
  local p=npc.factory_project
  npc.pending_job=nil;p.pending_job=nil;p.pending_maintenance=nil
  if not reason and Obstruction.finished(npc) then
    local resumed,refusal=Projects.resume_obstruction(npc,tick)
    if not resumed then npc.obstruction_resume_refusal=refusal end
  end
  return true,reason
end

function Projects.blocked_maintenance(npc,tick)
  if not npc.character or not npc.character.valid or npc.blocked_factory_upkeep
      or npc.current_job or npc.pending_job or (npc.jobs and #npc.jobs>0) then return nil end
  local scope=blocked_scope(npc)
  if not scope then return nil end
  for _,kind in ipairs({"iron","copper"}) do
    local job=MiningRecovery.fuel_maintenance(npc,kind,tick,4)
    if job then
      npc.blocked_factory_upkeep={scope=scope,kind=kind,issued_tick=tick}
      return job
    end
  end
  return nil
end
function Projects.bind_blocked_upkeep(npc,job_id)
  local bound=npc.blocked_factory_upkeep
  local issued,pending=blocked_issued(npc)
  if not bound or bound.job_id or type(job_id)~="string" or npc.pending_job~=job_id
      or not blocked_scope_matches(npc,bound.scope) or not issued
      or pending.kind~=bound.kind or issued.job_id then return false end
  if not MiningRecovery.bind(npc,job_id) then return false end
  bound.job_id=job_id
  return true
end
function Projects.reject_blocked_upkeep(npc,reason)
  local bound=npc.blocked_factory_upkeep
  if not bound or bound.job_id then return false end
  MiningRecovery.reject_unbound(npc,reason or "blocked_upkeep_enqueue_rejected")
  npc.blocked_factory_upkeep=nil
  return true
end
function Projects.on_blocked_upkeep_receipt(npc,receipt)
  local bound=npc.blocked_factory_upkeep
  local issued,pending=blocked_issued(npc)
  if not bound or not bound.job_id or not receipt or receipt.job_id~=bound.job_id
      or not npc.receipts or npc.receipts[bound.job_id]~=receipt or not issued
      or issued.job_id~=bound.job_id or pending.job_id~=bound.job_id
      or pending.kind~=bound.kind or receipt.kind~=issued.kind then return false end
  local scope_revoked=not blocked_scope_matches(npc,bound.scope)
  -- Once a real native terminal receipt exists, consume it into the separate
  -- upkeep ledger even if expansion changed; never abandon fulfilled work.
  if not MiningRecovery.on_receipt(npc,receipt) then return false end
  npc.blocked_upkeep_last_receipt={id=receipt.id,job_id=receipt.job_id,tick=receipt.tick,
    status=receipt.status,count=receipt.count,scope_revoked=scope_revoked,
    diagnostic=scope_revoked and "blocked_upkeep_scope_revoked" or nil}
  npc.blocked_factory_upkeep=nil
  return true
end

function Projects.bind(npc,job_id,maintenance)
  local project=npc.factory_project
  if not project or (project.status~="active" and project.status~="complete")
      or project.pending_job then return false end
  project.pending_job=job_id
  if maintenance and npc.craft_supply_recovery and npc.craft_supply_recovery.issued then
    CraftSupply.bind(npc,job_id)
  end
  if maintenance and npc.construction_supply_recovery and npc.construction_supply_recovery.issued then
    SupplyRecovery.bind(npc,job_id)
  end
  if maintenance and npc.conveyor_recovery and npc.conveyor_recovery.issued then
    ConveyorRecovery.bind(npc,job_id)
  end
  if Rocket.bind then Rocket.bind(npc,job_id) end
  if maintenance and npc.mining_recovery and npc.mining_recovery.issued then
    MiningRecovery.bind(npc,job_id)
  end
  project.pending_maintenance=maintenance or false
  return true
end

function Projects.receipt_matches_binding(npc,receipt)
  if Projects.obstruction_pending(npc) then return Obstruction.receipt_matches_binding(npc,receipt) end
  local project=npc.factory_project
  if not project or not project.pending_job then return true end
  if not receipt or receipt.job_id~=project.pending_job and receipt.id~=project.pending_job then return false end
  if project.pending_maintenance then
    local mining=npc.mining_recovery
    local mining_plan=mining and mining.issued and mining[mining.issued.kind]
    if mining and mining.issued and mining.issued.job_id
        and (not mining_plan or not mining_plan.issued
          or mining.issued.job_id~=mining_plan.issued.job_id
          or mining.issued.sequence~=mining_plan.issued.sequence) then return false end
    local issued={npc.construction_supply_recovery and npc.construction_supply_recovery.issued,
      npc.conveyor_recovery and npc.conveyor_recovery.issued,mining_plan and mining_plan.issued,
      npc.craft_supply_recovery and npc.craft_supply_recovery.issued}
    for key=1,4 do
      local bound=issued[key]
      if bound and bound.job_id and (bound.job_id~=project.pending_job
          or receipt.job_id~=bound.job_id or receipt.kind~=bound.kind) then return false end
    end
  end
  return true
end

function Projects.on_receipt(npc,receipt)
  if Projects.obstruction_pending(npc) then return Projects.on_obstruction_receipt(npc,receipt,game.tick) end
  local project=npc.factory_project
  if not project or (project.status~="active" and project.status~="complete") or not project.pending_job
      or receipt.job_id ~= project.pending_job and receipt.id ~= project.pending_job then
    return false
  end
  if not Projects.receipt_matches_binding(npc,receipt) then return false end
  local oil_action
  local completed_step=project.plan and project.plan.steps[project.index]
  if Oil.on_receipt and not project.pending_maintenance and receipt.status=="complete"
      and receipt.kind=="move" and completed_step and completed_step.kind=="move" then
    -- Discovery authenticates the still-bound native move before its pending
    -- slot clears. A successful scout can replace only this actor's own plan.
    oil_action=Oil.on_receipt(npc,receipt)
  end
  project.pending_job=nil
  local maintenance=project.pending_maintenance
  project.pending_maintenance=nil
  if maintenance and CraftSupply.on_receipt(npc,receipt) then return true end
  if maintenance and SupplyRecovery.on_receipt(npc,receipt) then return true end
  if maintenance and ConveyorRecovery.on_receipt(npc,receipt) then return true end
  if maintenance and MiningRecovery.on_receipt(npc,receipt) then return true end
  local clearance=project.pending_clearance
  project.pending_clearance=nil
  if clearance and maintenance and receipt.kind=="mine" then
    project.clearance_receipts=project.clearance_receipts or {}
    project.clearance_receipts[#project.clearance_receipts+1]={site=clearance.site,
      target_name=clearance.target_name,target_position=clearance.target_position,
      target_unit_number=clearance.target_unit_number,receipt_id=receipt.id,
      status=receipt.status,reason=receipt.reason,item=receipt.item,count=receipt.count,
      produced=receipt.produced}
    if #project.clearance_receipts>128 then table.remove(project.clearance_receipts,1) end
  end
  Rocket.on_receipt(npc,receipt)
  if receipt.status~="complete" then
    if receipt.status=="canceled" and (receipt.reason=="combat_interrupt" or receipt.reason=="project_replan") then
      -- Retry only the uncompleted remainder. Native cancellation already
      -- refunded the unfinished craft; completed output stays in inventory.
      local step=not maintenance and project.plan.steps[project.index]
      if step and (step.kind=="craft" or step.kind=="mine" or step.kind=="transfer") then
        local actual=receipt.kind=="craft" and (receipt.completed or 0) or (receipt.count or 0)
        step.count=math.max(0,(step.count or 1)-actual)
        if step.count==0 then project.index=project.index+1 end
      end
      project.last_problem=receipt.reason
      return true
    end
    if not maintenance and receipt.kind=="transfer" and receipt.reason=="partial_transfer" then
      local step=project.plan.steps[project.index]
      if step and step.kind=="transfer" and step.item==receipt.item and step.mode==receipt.mode
          and type(receipt.count)=="number" and receipt.count>=0 then
        -- Partial native transfers already moved real stock. Retry only the
        -- remainder; replay is refused by the matching pending-job gate above.
        step.count=math.max(0,(step.count or 1)-receipt.count)
        if step.count==0 then project.index=project.index+1 end
      end
    end
    if receipt.reason~="in_danger" then project.failures=project.failures+1 end   -- (in danger: a wait, W-30)
    project.last_problem=receipt.reason or "worker_failed"
    if project.failures>=3 and project.status=="active" then
      project.status="blocked"; project.reason=project.last_problem
    end
    return true
  end
  if oil_action and (oil_action.kind=="replace_plan" or oil_action.kind=="oil_scout_blocked") then
    local next_plan=oil_action.plan
    if oil_action.kind=="replace_plan" and (type(next_plan)~="table" or next_plan.id~=project.id
        or type(next_plan.steps)~="table" or #next_plan.steps==0 or type(next_plan.sites)~="table") then
      oil_action={kind="oil_scout_blocked",reason="oil_scout_invalid_replacement_plan"}
    end
    project.oil_scout_history=project.oil_scout_history or {}
    project.oil_scout_history[#project.oil_scout_history+1]={tick=receipt.tick or game.tick,
      receipt_id=receipt.id,job_id=receipt.job_id,prior_index=project.index,
      started_tick=project.started_tick,prior_failures=project.failures,
      action=oil_action.kind,reason=oil_action.reason,
      from_revision=project.plan.revision,to_revision=next_plan and next_plan.revision}
    while #project.oil_scout_history>32 do table.remove(project.oil_scout_history,1) end
    project.last_progress_tick=receipt.completed_tick or receipt.tick or game.tick
    if oil_action.kind=="replace_plan" then
      project.plan=next_plan;project.index=1;project.status="active";project.reason=nil
      project.replanned_tick=game.tick
    else
      project.status="blocked";project.reason=oil_action.reason or "oil_scout_exhausted"
      project.last_problem=project.reason
    end
    return true
  end
  if maintenance then
    project.failures=0
    return true
  end
  local step=project.plan.steps[project.index]
  if step and step.kind=="build" then
    local entity=receipt.entity
    if entity and entity.valid and entity.force==npc.force then
      local site=site_spec(npc,step.target_site)
      if site then site.existing_unit_number=entity.unit_number end
    end
  elseif step and step.kind=="configure_recipe" and step.target_site then
    local site=site_spec(npc,step.target_site)
    if site then site.recipe=receipt.configured_recipe or step.recipe end
  end
  project.failures=0
  project.index=project.index+1
  project.last_progress_tick=receipt.completed_tick or receipt.tick or game.tick
  return true
end

-- Private paused development migration: preserve earned state and only
-- replace this actor's existing rocket plan. No stock/research grants occur.
function Projects.replan(npc,project_id)
  local old=npc.factory_project
  if not game.tick_paused then return false,"replan_requires_pause" end
  if not old or old.id~=project_id or (project_id~="rocket_materials" and project_id~="platform_launch")
      or (old.status~="active" and old.status~="blocked") then return false,"matching_rocket_project_required" end
  if not npc.character or not npc.character.valid then return false,"actor_unavailable" end
  local pending=old.pending_job
  if npc.social_job or npc.ai_running or (npc.pending_job and npc.pending_job~=pending)
      or (npc.current_job and npc.current_job.id~=pending) then return false,"unrelated_work_pending" end
  for _,job in ipairs(npc.jobs or {}) do
    if job.id~=pending then return false,"unrelated_work_queued" end
  end
  local receipt
  if pending then
    receipt=npc.receipts and npc.receipts[pending]
    if not receipt then
      local reason
      receipt,reason=Worker.cancel_job(npc,pending,"project_replan")
      if not receipt then return false,reason end
    end
    Projects.on_receipt(npc,receipt)
    npc.pending_job=nil
  end
  -- Plan after native cancellation, so refunded ingredients are available to
  -- ordinary supply checks. If planning fails the adjusted old plan is retained.
  local plan,reason=Rocket.plan(npc,project_id)
  if not plan then return false,reason or "no_plan" end
  if type(plan)~="table" or type(plan.steps)~="table" or #plan.steps==0
      or type(plan.sites)~="table" then return false,"invalid_plan" end
  local history=old.replans or {}
  history[#history+1]={tick=game.tick,from_revision=old.plan.revision,to_revision=plan.revision,
    prior_index=old.index,prior_status=old.status,prior_started_tick=old.started_tick,
    receipt_id=receipt and receipt.id,cancellation=receipt and receipt.reason=="project_replan" or false}
  while #history>32 do table.remove(history,1) end
  npc.factory_project={id=project_id,plan=plan,index=1,status="active",started_tick=old.started_tick,
    replanned_tick=game.tick,replans=history,failures=0,last_progress_tick=old.last_progress_tick}
  npc.requested_project=nil
  npc.phase="building-replanned:"..project_id
  return true,{project=project_id,revision=plan.revision,steps=#plan.steps,tick=game.tick,
    canceled_job=receipt and receipt.reason=="project_replan" and receipt.job_id or nil,
    stock_grants=0,research_grants=0,energy_grants=0}
end

function Projects.observe_rocket(npc) return Rocket.observe(npc) end
function Projects.reject_unbound(npc,job,reason)
  if job and job.upkeep_source=="obstruction_recovery" then return Obstruction.reject_unbound(npc,reason) end
  if job and job.upkeep_source=="craft_supply_recovery" then
    return CraftSupply.reject_unbound(npc,reason)
  end
  if job and job.upkeep_source=="construction_supply_recovery" then
    return SupplyRecovery.reject_unbound(npc,reason)
  end
  if job and job.upkeep_source=="conveyor_recovery" then
    return ConveyorRecovery.reject_unbound(npc,reason)
  end
  if job and job.upkeep_source=="mining_recovery" then
    return MiningRecovery.reject_unbound(npc,reason)
  end
  return false
end

local function amount(entity,inventory,item)
  if not entity or not entity.valid then return 0 end
  local inv=entity.get_inventory(inventory)
  return inv and inv.valid and inv.get_item_count(item) or 0
end

local function deposit(entity,item,count,inventory)
  return {kind="transfer",target=entity,item=item,count=count,mode="deposit",inventory=inventory}
end

-- After the automated line is built, preserve native belt/inserter movement.
-- These jobs only replenish physically mined coal in burners; feeding machine
-- output or inputs by hand would conceal a broken conveyor layout.
function Projects.maintenance(npc,allow_active)
  local project=npc.factory_project
  if not project or project.pending_job or (project.status~="complete"
      and not (allow_active and project.status=="active")) then return nil end
  local conveyor=ConveyorRecovery.next(npc,game.tick,Projects.resolve_site)
  if conveyor then return conveyor end
  local recovery=MiningRecovery.next(npc,nil,game.tick,Projects.resolve_site)
  if recovery then return recovery end
  local defense=Defense.maintenance(npc)
  if defense then return defense end
  local rocket=not allow_active and Rocket.maintenance(npc)
  if rocket then return rocket end
  local oil=not allow_active and Oil.maintenance(npc)
  if oil then return oil end
  local advanced=not allow_active and Advanced.maintenance(npc)
  if advanced then return advanced end
  for _,key in ipairs({"boiler","iron_furnace","copper_furnace","copper_drill",
      "copper_ore_inserter"}) do
    local entity=Projects.resolve_site(npc,key)
    if entity and amount(entity,defines.inventory.fuel,"coal")<1 then
      if carried(npc,"coal")<4 then return {kind="mine",item="coal",count=12} end
      return deposit(entity,"coal",4)
    end
  end
  if npc.miner and npc.miner.valid and npc.miner.force==npc.force
      and amount(npc.miner,defines.inventory.fuel,"coal")<1 then
    if carried(npc,"coal")<4 then return {kind="mine",item="coal",count=12} end
    return deposit(npc.miner,"coal",4)
  end
  return nil
end

return Projects
