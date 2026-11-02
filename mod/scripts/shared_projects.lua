-- Coalition specialists prepare only a leader's current, concrete factory
-- need. Worker performs every collection, craft, and foreign handoff.
local Cooperation=require("scripts.cooperation")
local Recipes=require("scripts.recipes")
local SharedProjects={}
local MAX_COMPONENTS=20
SharedProjects.PROJECT_WAIT_TICKS=21600
local SPECIALISTS={
  ["automation-science-pack"]="researcher",
  ["small-electric-pole"]="power_technician",
}

local function stock(npc,item)
  local character=npc and npc.character
  local inventory=character and character.valid and character.get_main_inventory()
  return inventory and inventory.valid
      and inventory.get_item_count{name=item,quality="normal"} or 0
end

-- A planned hand-craft can consume implicit intermediate ingredients (for
-- example copper cable in a pole). Count their native recipe ingredients,
-- but never treat a furnace recipe as a character hand-craft.
local function craft_ingredient_need(force,categories,recipe,batches,item,depth,seen)
  if not recipe or depth>4 or batches<=0 then return 0 end
  local total=0
  for _,ingredient in pairs(recipe.ingredients or {}) do
    if ingredient.type=="item" and type(ingredient.amount)=="number" then
      local amount=math.ceil(ingredient.amount*batches)
      if ingredient.name==item then total=total+amount
      elseif depth<4 and not seen[ingredient.name] then
        local inner=force.recipes[ingredient.name]
        if inner and Recipes.made_in(inner,categories) then
          local yield
          for _,product in pairs(inner.products or {}) do
            if product.type=="item" and product.name==ingredient.name
                and type(product.amount)=="number"
                and (not product.probability or product.probability==1) then
              yield=product.amount;break
            end
          end
          if yield and yield>0 then
            seen[ingredient.name]=true
            total=total+craft_ingredient_need(force,categories,inner,
              math.ceil(amount/yield),item,depth+1,seen)
            seen[ingredient.name]=nil
          end
        end
      end
    end
  end
  return total
end

-- Protect only the uncompleted suffix of the helper's concrete plan. Future
-- planned physical acquisitions offset later consumption, so this reserve
-- shrinks as the plan advances and does not lock the initial whole-project
-- budget forever. Stock above this minimum can fund an ally's request.
local function own_project_reserve(helper,item)
  local project=helper.factory_project
  local plan=project and project.plan
  if not project or project.status~="active" or not plan or not plan.steps then return 0 end
  local categories=helper.character and helper.character.valid
      and helper.character.prototype.crafting_categories or {}
  local balance,needed=0,0
  for index=project.index or 1,#plan.steps do
    local step=plan.steps[index]
    local count=math.max(0,math.min(100,math.floor(step.count or 1)))
    if step.kind=="wait" and step.condition and step.condition.kind=="inventory"
        and not step.condition.target_site and step.condition.item==item then
      needed=math.max(needed,balance+math.max(0,math.min(100,
        math.floor(step.condition.count or 1))))
    elseif step.kind=="craft" and type(step.recipe)=="string" then
      local recipe=helper.force.recipes[step.recipe]
      if recipe then
        balance=balance+craft_ingredient_need(helper.force,categories,recipe,count,item,0,{})
        for _,product in pairs(recipe.products or {}) do
          if product.type=="item" and product.name==item
              and type(product.amount)=="number"
              and (not product.probability or product.probability==1) then
            balance=balance-math.floor(product.amount*count)
          end
        end
      end
    elseif step.kind=="build" then
      local site=step.target_site and plan.sites and plan.sites[step.target_site]
      if (step.item or site and (site.item or site.name))==item then
        balance=balance+1
      end
    elseif step.kind=="transfer" and step.mode=="deposit" and step.item==item then
      balance=balance+count
    elseif step.kind=="transfer" and step.mode=="withdraw" and step.item==item then
      balance=balance-count
    elseif step.kind=="mine" and step.item==item then
      balance=balance-count
    end
    needed=math.max(needed,balance)
  end
  return math.max(0,math.min(100,needed))
end

local function usable_stock(helper,item)
  return math.max(0,stock(helper,item)-own_project_reserve(helper,item))
end

local function recipe_output(recipe,item)
  if not recipe then return nil end
  for _,product in pairs(recipe.products or {}) do
    if product.type=="item" and product.name==item and product.amount
        and (not product.probability or product.probability==1) then return product.amount end
  end
end

local function project_need(leader)
  local project=leader and leader.factory_project
  if not project or project.status~="active" or project.pending_job
      or not project.plan or not project.plan.steps then return nil end
  local generation=project.replanned_tick or project.started_tick
  if type(generation)~="number" then return nil end
  local step=project.plan.steps[project.index]
  if not step then return nil end
  local item,count
  if step.kind=="craft" then
    item=step.recipe
    if not SPECIALISTS[item] then return nil end
    local output=recipe_output(leader.force.recipes[item],item)
    if not output then return nil end
    count=(step.count or 0)*output
  elseif step.kind=="build" then
    local site=step.target_site and project.plan.sites
        and project.plan.sites[step.target_site]
    item=step.item or site and (site.item or site.name)
    if item~="small-electric-pole" then return nil end
    count=1
  elseif step.kind=="transfer" and step.item=="automation-science-pack"
      and step.mode=="deposit" then
    item,count=step.item,step.count
  else return nil end
  if type(count)~="number" or count<1 or count>MAX_COMPONENTS
      or count~=math.floor(count) then return nil end
  local initial=stock(leader,item)
  local missing=count-initial
  if missing<1 then return nil end
  return {item=item,count=missing,purpose="coalition_project",
    specialist_role=SPECIALISTS[item],project={id=project.id,
      revision=project.plan.revision,generation_tick=generation,
      index=project.index,kind=step.kind,item=item,
      expected_count=count,initial_stock=initial}}
end

function SharedProjects.demands(leader)
  local society=storage.society
  local actor=society and society.actors and society.actors[leader.id]
  local coalition=actor and society.coalitions and society.coalitions[actor.coalition_id]
  if not coalition or not coalition.members[leader.id] then return {} end
  local need=project_need(leader)
  return need and {need} or {}
end

function SharedProjects.project_valid(request,leader)
  local metadata=request and request.project
  if not metadata or not leader or not leader.character or not leader.character.valid then return false end
  local project=leader.factory_project
  if not project or project.status~="active" or not project.plan
      or project.id~=metadata.id or project.plan.revision~=metadata.revision
      or (project.replanned_tick or project.started_tick)~=metadata.generation_tick
      or project.index~=metadata.index then return false end
  local step=project.plan.steps and project.plan.steps[metadata.index]
  local site=step and step.target_site and project.plan.sites
      and project.plan.sites[step.target_site]
  return step and step.kind==metadata.kind
      and ((step.kind=="craft" and step.recipe==metadata.item)
        or (step.kind=="build" and (step.item or site and (site.item or site.name))==metadata.item)
        or (step.kind=="transfer" and step.item==metadata.item and step.mode=="deposit"))
end

local function treaty(s,helper,leader,request)
  if not helper or not helper.character or not helper.character.valid
      or helper.character.force~=helper.force or not leader or not leader.character
      or not leader.character.valid or leader.character.force~=leader.force
      or helper.character.surface~=leader.character.surface then return false end
  local a,b=s.actors and s.actors[helper.id],s.actors and s.actors[leader.id]
  local coalition=a and b and s.coalitions and s.coalitions[a.coalition_id]
  if not coalition or a.coalition_id~=b.coalition_id
      or not coalition.members[helper.id] or not coalition.members[leader.id]
      or coalition.roles[helper.id]~=request.specialist_role then return false end
  return helper.force.get_friend(leader.force) and leader.force.get_friend(helper.force)
      and helper.force.get_cease_fire(leader.force) and leader.force.get_cease_fire(helper.force)
end

local function own_project_uses_site(helper,entity,item)
  local project=helper.factory_project
  local plan=project and project.plan
  if not project or project.status~="active" or not plan or not plan.sites then return false end
  for _,site in pairs(plan.sites) do
    local position=site and site.position
    local x=position and (position.x or position[1])
    local y=position and (position.y or position[2])
    if type(x)=="number" and type(y)=="number" and site.name==entity.name
        and math.abs(entity.position.x-x)<0.15
        and math.abs(entity.position.y-y)<0.15 then
      return true
    end
  end
  return false
end

local function smelter_compatible(helper,furnace,item,ore)
  if not (furnace and furnace.valid and furnace.force==helper.force
      and furnace.surface==helper.character.surface
      and (furnace.name=="stone-furnace" or furnace.name=="steel-furnace")
      and not own_project_uses_site(helper,furnace,item)) then return false end
  local input=furnace.get_inventory(defines.inventory.crafter_input)
  local output=furnace.get_output_inventory()
  local fuel=furnace.get_fuel_inventory()
  if not (input and input.valid and output and output.valid and fuel and fuel.valid) then return false end
  local recipe=furnace.get_recipe()
  if recipe and recipe.name~=item then return false end
  for i=1,#input do local stack=input[i]
    if stack.valid_for_read and (stack.name~=ore or stack.quality.name~="normal") then return false end
  end
  for i=1,#output do local stack=output[i]
    if stack.valid_for_read and (stack.name~=item or stack.quality.name~="normal") then return false end
  end
  return true,input,output,fuel
end

local function site_position(site)
  local p=site and site.position
  local x=p and (p.x or p[1])
  local y=p and (p.y or p[2])
  if type(x)=="number" and type(y)=="number" then return x,y end
end

local function planned_site_clear(helper,position,item)
  local surface=helper.character.surface
  if not surface.can_place_entity{name="stone-furnace",position=position,
      direction=defines.direction.north,force=helper.force,
      build_check_type=defines.build_check_type.manual} then return false end
  -- A specialist never clears a mineable obstacle or builds over another
  -- force's property as a side effect of a project request.
  for _,entity in ipairs(surface.find_entities_filtered{position=position,radius=1.6}) do
    if entity.valid and entity.type~="resource" and entity.type~="corpse"
        and entity.type~="item-entity" then return false end
  end
  local project=helper.factory_project
  if project and project.status=="active" then
    for _,site in pairs(project.plan and project.plan.sites or {}) do
      local x,y=site_position(site)
      if x and math.abs(position.x-x)<4 and math.abs(position.y-y)<4 then return false end
    end
  end
  for other,plan in pairs(helper.shared_smelter_plans or {}) do
    local x,y=site_position(plan.site)
    if other~=item and x and math.abs(position.x-x)<4
        and math.abs(position.y-y)<4 then return false end
  end
  return true
end

local function choose_smelter_site(helper,item)
  local home=helper.home or helper.character.position
  local position=helper.character.position
  local dx,dy=home.x-position.x,home.y-position.y
  local anchor=dx*dx+dy*dy<=128*128 and home or position
  local x0,y0=math.floor(anchor.x),math.floor(anchor.y)
  for ring=1,8 do
    local radius=ring*4
    for _,offset in ipairs({{radius,0},{0,radius},{-radius,0},{0,-radius},
        {radius,radius},{-radius,radius},{-radius,-radius},{radius,-radius}}) do
      local candidate={x=x0+offset[1],y=y0+offset[2]}
      local ax,ay=candidate.x-position.x,candidate.y-position.y
      if ax*ax+ay*ay<=128*128 and planned_site_clear(helper,candidate,item) then
        return {name="stone-furnace",item="stone-furnace",position=candidate,
          direction=defines.direction.north,surface_index=helper.character.surface.index,
          force_name=helper.force.name}
      end
    end
  end
end

local function planned_smelter(helper,item,ore,persist)
  local plans=helper.shared_smelter_plans
  local plan=plans and plans[item]
  if plan and (plan.force_name~=helper.force.name
      or plan.surface_index~=helper.character.surface.index) then plan=nil end
  if not plan then
    plan={item=item,ore=ore,force_name=helper.force.name,
      surface_index=helper.character.surface.index,stone_credit=0,furnace_credit=0}
    if persist then
      helper.shared_smelter_plans=plans or {}
      helper.shared_smelter_plans[item]=plan
    end
  end
  return plan
end

local function owned_stock_job(helper,item,count,persist)
  local function nearby_resource(resource_item)
    local entities=helper.character.surface.find_entities_filtered{
      position=helper.character.position,radius=128,
      type=resource_item=="wood" and {"tree"} or {"resource"}}
    for _,entity in ipairs(entities) do
      if entity.valid and entity.force.name=="neutral" and entity.minable then
        local properties=entity.prototype.mineable_properties
        if properties and properties.minable and not properties.required_fluid then
          for _,product in ipairs(properties.products or {}) do
            if product.type=="item" and product.name==resource_item then return true end
          end
        end
      end
    end
    return false
  end
  if item=="wood" or item=="coal" then
    if nearby_resource(item) then
      return {kind="mine",item=item,count=math.min(count,MAX_COMPONENTS),search_radius=128}
    end
    return nil
  end
  local nearby=helper.character.surface.find_entities_filtered{position=helper.character.position,
    radius=128,force=helper.force,type={"furnace","container","logistic-container"}}
  local best,best_inventory,best_distance,best_available
  for _,entity in ipairs(nearby) do
    if entity.valid and not own_project_uses_site(helper,entity,item) then
      local index=entity.type=="furnace" and defines.inventory.crafter_output
          or defines.inventory.chest
      local inventory=entity.get_inventory(index)
      local available=inventory and inventory.valid
          and inventory.get_item_count{name=item,quality="normal"} or 0
      if available>0 then
        local dx=entity.position.x-helper.character.position.x
        local dy=entity.position.y-helper.character.position.y
        local distance=dx*dx+dy*dy
        local tie=best and distance==best_distance and (
          entity.position.x<best.position.x or entity.position.x==best.position.x and (
            entity.position.y<best.position.y or entity.position.y==best.position.y and (
              entity.name<best.name or entity.name==best.name
                and (entity.unit_number or 0)<(best.unit_number or 0))))
        if not best or distance<best_distance or tie then
          best,best_inventory,best_distance,best_available=entity,index,distance,available
        end
      end
    end
  end
  if best then return {kind="transfer",target=best,item=item,
    count=math.min(count,best_available),
    mode="withdraw",inventory=best_inventory} end
  local ore=({["iron-plate"]="iron-ore",["copper-plate"]="copper-ore"})[item]
  if not ore then return nil end
  local assignments=helper.shared_smelters or {}
  local furnace=assignments[item]
  if not smelter_compatible(helper,furnace,item,ore) then furnace=nil end
  local plan=helper.shared_smelter_plans and helper.shared_smelter_plans[item]
  if not furnace and plan and plan.built_unit_number and plan.site then
    local x,y=site_position(plan.site)
    if x then
      for _,candidate in ipairs(helper.character.surface.find_entities_filtered{
          position={x=x,y=y},radius=.15,name="stone-furnace",force=helper.force}) do
        if candidate.unit_number==plan.built_unit_number
            and smelter_compatible(helper,candidate,item,ore) then
          furnace=candidate;break
        end
      end
    end
    if furnace and persist then
      helper.shared_smelters=assignments;assignments[item]=furnace
    end
  end
  if not furnace then
    local furnaces=helper.character.surface.find_entities_filtered{
      position=helper.character.position,radius=128,force=helper.force,
      name={"stone-furnace","steel-furnace"}}
    table.sort(furnaces,function(a,b)
      local ax,ay=a.position.x-helper.character.position.x,a.position.y-helper.character.position.y
      local bx,by=b.position.x-helper.character.position.x,b.position.y-helper.character.position.y
      local da,db=ax*ax+ay*ay,bx*bx+by*by
      return da<db or da==db and (a.unit_number or 0)<(b.unit_number or 0)
    end)
    for _,candidate in ipairs(furnaces) do
      local claimed=false
      for other,assigned in pairs(assignments) do
        if other~=item and assigned==candidate then claimed=true;break end
      end
      for other,other_plan in pairs(helper.shared_smelter_plans or {}) do
        if other~=item and other_plan.built_unit_number==candidate.unit_number then
          claimed=true;break
        end
      end
      if not claimed and smelter_compatible(helper,candidate,item,ore) then
        furnace=candidate;break
      end
    end
    if furnace and persist then
      helper.shared_smelters=assignments;assignments[item]=furnace
    end
  end
  if not furnace then
    plan=planned_smelter(helper,item,ore,persist)
    local site=plan.site
    local x,y=site_position(site)
    if x then
      local existing=helper.character.surface.find_entities_filtered{
        position={x=x,y=y},radius=.15,name="stone-furnace"}
      if #existing>0 then return nil,"unverified_planned_smelter" end
    end
    local furnace_credit=math.min(math.max(0,plan.furnace_credit or 0),
      usable_stock(helper,"stone-furnace"))
    local stone_credit=math.min(math.max(0,plan.stone_credit or 0),
      usable_stock(helper,"stone"))
    if furnace_credit>=1 then
      if not (x and site.force_name==helper.force.name
          and site.surface_index==helper.character.surface.index
          and planned_site_clear(helper,{x=x,y=y},item)) then
        site=choose_smelter_site(helper,item)
        if persist then plan.site=site end
      end
      if not site then return nil,"no_legal_owned_smelter_site" end
      return {kind="build",entity_name="stone-furnace",item="stone-furnace",
        position={x=site.position.x,y=site.position.y},direction=site.direction},
        "building_owned_smelter",{smelter_item=item,smelter_stage="build",
          smelter_site={name=site.name,position={x=site.position.x,y=site.position.y},
            surface_index=site.surface_index,force_name=site.force_name}}
    end
    if stone_credit>=5 then
      local recipe=helper.force.recipes["stone-furnace"]
      if not recipe or not recipe.enabled then return nil,"stone_furnace_recipe_locked" end
      return {kind="craft",recipe="stone-furnace",count=1},
        "crafting_owned_smelter",{smelter_item=item,smelter_stage="craft"}
    end
    if nearby_resource("stone") then
      return {kind="mine",item="stone",count=5-stone_credit,search_radius=128},
        "mining_owned_smelter_stone",{smelter_item=item,smelter_stage="mine"}
    end
    return nil,"no_nearby_stone_for_smelter"
  end
  local _,input,output,fuel=smelter_compatible(helper,furnace,item,ore)
  if not input then return nil,"smelter_assignment_changed" end
  local burning=furnace.burner and furnace.burner.remaining_burning_fuel or 0
  if fuel.get_item_count("coal")==0 and burning<=0 then
    if usable_stock(helper,"coal")>0 then
      return {kind="transfer",target=furnace,item="coal",count=1,
        mode="deposit",inventory=defines.inventory.fuel}
    end
    if nearby_resource("coal") then
      return {kind="mine",item="coal",count=1,search_radius=128}
    end
    return nil
  end
  local held=usable_stock(helper,ore)
  local loaded=input.get_item_count(ore)
  local missing=math.max(0,count-loaded)
  if missing>0 and held>0 then
    return {kind="transfer",target=furnace,item=ore,count=math.min(missing,held),
      mode="deposit",inventory=defines.inventory.crafter_input}
  end
  if missing>0 and nearby_resource(ore) then
    return {kind="mine",item=ore,count=math.min(missing,MAX_COMPONENTS),search_radius=128}
  end
  return nil,"smelting_in_progress"
end

local function production_job(helper,item,needed,depth,persist)
  local usable=usable_stock(helper,item)
  if usable>=needed then return nil,"already_carried" end
  local recipe=helper.force.recipes[item]
  local output=recipe and recipe.enabled and recipe_output(recipe,item)
  local categories=helper.character.prototype.crafting_categories or {}
  if not output or not Recipes.made_in(recipe,categories) or depth>3 then
    local job,reason,metadata=owned_stock_job(helper,item,needed-usable,persist)
    return job,reason or "raw_source",metadata
  end
  local batches=math.ceil((needed-usable)/output)
  if batches<1 or batches>MAX_COMPONENTS then return nil,"craft_budget" end
  for _,ingredient in ipairs(recipe.ingredients or {}) do
    if ingredient.type~="item" or not ingredient.amount then return nil,"fluid_recipe" end
    local required=ingredient.amount*batches
    if usable_stock(helper,ingredient.name)<required then
      return production_job(helper,ingredient.name,required,depth+1,persist)
    end
  end
  return {kind="craft",recipe=item,count=batches},"craft"
end

local function assignable(s,helper,request,tick)
  local leader=storage.npcs and storage.npcs[request.requester_id]
  return request.status=="open" and request.expires_tick>tick
      and request.specialist_role and request.project
      and (not request.assigned_to or request.assigned_to==helper.id)
      and (not request.specialist_retry_tick or tick>=request.specialist_retry_tick)
      and SharedProjects.project_valid(request,leader)
      and treaty(s,helper,leader,request)
      and Cooperation.walking_route(s,helper,leader),leader
end

function SharedProjects.next(helper,tick)
  local s=storage.society
  local actor=s and s.actors and s.actors[helper.id]
  if not s or not s.requests or not actor or actor.pending_intent or helper.social_job
      or helper.shared_specialist and helper.shared_specialist.job_id then return nil end
  for _,request in ipairs(s.requests) do
    local allowed,leader=assignable(s,helper,request,tick)
    if allowed then
      local needed=math.min(request.remaining or request.count,MAX_COMPONENTS)
      if Cooperation.capacity(helper,request.item,s)>=needed then
        local accepted=Cooperation.accept(s,request.id,helper,leader.id,true)
        if accepted then
          accepted.assigned_to=helper.id
          actor.pending_intent={intent={kind="supply",target_id=leader.id,
            request_id=accepted.id,item=accepted.item,count=accepted.accepted_count,
            purpose=accepted.purpose,actor_id=helper.id}}
          helper.shared_specialist=nil
          return nil,"handoff_ready"
        end
      else
        local job,_,metadata=production_job(helper,request.item,needed,0,true)
        if job then
          request.assigned_to=helper.id
          return job,{request_id=request.id,project=request.project,item=request.item,
            smelter=metadata}
        end
      end
    end
  end
end

function SharedProjects.bind(helper,job_id,task)
  if not job_id or not task or not task.request_id then return false end
  helper.shared_specialist={request_id=task.request_id,job_id=job_id,
    project=task.project,item=task.item,smelter=task.smelter,bound_tick=game.tick}
  return true
end

local function same_scope(a,b)
  return a and b and a.id==b.id and a.revision==b.revision
      and a.generation_tick==b.generation_tick and a.index==b.index
      and a.kind==b.kind and a.item==b.item
end

function SharedProjects.cancel_invalid(helper,tick,cancel_job)
  local pending=helper and helper.shared_specialist
  if not pending or not pending.job_id or helper.receipts
      and helper.receipts[pending.job_id] then return false end
  local s=storage.society
  local request=s and Cooperation.find(s,pending.request_id)
  local leader=request and storage.npcs and storage.npcs[request.requester_id]
  local valid=request and request.status=="open" and request.assigned_to==helper.id
      and request.expires_tick>tick and same_scope(request.project,pending.project)
      and SharedProjects.project_valid(request,leader)
      and treaty(s,helper,leader,request)
      and Cooperation.walking_route(s,helper,leader)
  if valid then return false end
  pending.invalidated=true
  if pending.smelter then
    local plans=helper.shared_smelter_plans
    local plan=plans and plans[pending.smelter.smelter_item]
    if plan then plan.stone_credit=0;plan.furnace_credit=0 end
  end
  if cancel_job then cancel_job(helper,pending.job_id,"specialist_scope_withdrawn") end
  return true
end

local function smelter_receipt(helper,pending,receipt)
  local task=pending.smelter
  if not task then return end
  local plans=helper.shared_smelter_plans
  local plan=plans and plans[task.smelter_item]
  if not plan or plan.force_name~=helper.force.name
      or plan.surface_index~=helper.character.surface.index then return end
  local actual=math.max(0,receipt.count or 0)
  if task.smelter_stage=="mine" and receipt.kind=="mine"
      and receipt.item=="stone" and not pending.invalidated then
    plan.stone_credit=math.min(5,math.max(0,plan.stone_credit or 0)+actual)
  elseif task.smelter_stage=="craft" and receipt.kind=="craft"
      and receipt.recipe=="stone-furnace" and not pending.invalidated then
    local spent=math.max(0,(receipt.consumed and receipt.consumed.stone or 0)
      -(receipt.refunded and receipt.refunded.stone or 0))
    plan.stone_credit=math.max(0,(plan.stone_credit or 0)-spent)
    plan.furnace_credit=math.min(1,(plan.furnace_credit or 0)
      +math.max(0,receipt.produced and receipt.produced["stone-furnace"] or 0))
  elseif task.smelter_stage=="build" and receipt.kind=="build" then
    local site=task.smelter_site
    local x,y=site_position(site)
    local entity=receipt.entity
    local px,py=entity and entity.valid and entity.position.x,
      entity and entity.valid and entity.position.y
    local sx,sy=site_position(plan.site)
    if receipt.status=="complete" and not receipt.world_loss
        and receipt.entity_name=="stone-furnace"
        and (receipt.consumed and receipt.consumed["stone-furnace"] or 0)==1
        and x and sx and math.abs(x-sx)<.15 and math.abs(y-sy)<.15
        and entity and entity.valid and entity.name=="stone-furnace"
        and entity.force==helper.force and entity.surface==helper.character.surface
        and math.abs(px-x)<.15 and math.abs(py-y)<.15
        and site.force_name==helper.force.name
        and site.surface_index==helper.character.surface.index then
      plan.built_unit_number=entity.unit_number
      plan.furnace_credit=math.max(0,(plan.furnace_credit or 0)-1)
      helper.shared_smelters=helper.shared_smelters or {}
      helper.shared_smelters[task.smelter_item]=entity
    elseif receipt.status~="complete" then
      -- A failed placement keeps its crafted item, but the site is rechecked
      -- before another attempt. No inventory or world entity is fabricated.
      plan.site=nil
    end
  end
end

function SharedProjects.on_receipt(helper,receipt)
  local pending=helper.shared_specialist
  if not pending or pending.job_id~=receipt.job_id then return false end
  smelter_receipt(helper,pending,receipt)
  helper.shared_specialist=nil
  if receipt.status~="complete" then
    local s=storage.society
    local request=s and Cooperation.find(s,pending.request_id)
    if request and request.assigned_to==helper.id and request.status=="open" then
      request.assigned_to=nil;request.specialist_retry_tick=game.tick+600
    end
  end
  return true
end

-- Only a completed exact Worker delivery, still in the leader's physical
-- inventory, can replace an unstarted planned craft step.
function SharedProjects.leader_supplied_step(leader)
  local project=leader.factory_project
  local s=storage.society
  if not s or not project or project.pending_job then return false end
  local step=project.plan and project.plan.steps and project.plan.steps[project.index]
  if not step or step.kind~="craft" or not SPECIALISTS[step.recipe] then return false end
  local output=recipe_output(leader.force.recipes[step.recipe],step.recipe)
  local expected=output and output*(step.count or 0)
  if not expected or expected<1 or expected>MAX_COMPONENTS then return false end
  for _,request in ipairs(s.requests or {}) do
    if request.requester_id==leader.id and request.item==step.recipe
        and request.status=="delivered" and request.receipt_id
        and request.delivered_count==request.count
        and request.project
        and (request.project.initial_stock or 0)+request.delivered_count>=expected
        and not request.project_step_used
        and SharedProjects.project_valid(request,leader)
        and stock(leader,step.recipe)>=expected then
      request.project_step_used=true
      project.index=project.index+1
      project.last_progress_tick=game.tick
      return true
    end
  end
  return false
end

-- Give a role-matched helper a bounded chance to fulfill an actionable
-- request before the leader starts the same craft/build itself. No open board
-- entry alone blocks progress: at least one live specialist must have a real
-- source item or an enabled, funded native production step.
function SharedProjects.leader_waiting(leader,tick)
  local s=storage.society
  if not s or not s.requests then return false end
  local need=project_need(leader)
  if not need then return false end
  for _,request in ipairs(s.requests) do
    if request.requester_id==leader.id and request.item==need.item
        and request.project and SharedProjects.project_valid(request,leader)
        and (request.status=="open" or request.status=="accepted")
        and request.expires_tick>tick then
      if not request.specialist_wait_started_tick then
        local viable=false
        for helper_id in pairs((s.coalitions[(s.actors[leader.id] or {}).coalition_id] or {}).members or {}) do
          if helper_id~=leader.id then
            local helper=storage.npcs and storage.npcs[helper_id]
            if helper and treaty(s,helper,leader,request)
                and Cooperation.walking_route(s,helper,leader) then
              local available=Cooperation.capacity(helper,request.item,s)
              local job,reason
              if available<(request.remaining or request.count) then
                job,reason=production_job(helper,request.item,request.remaining or request.count,0)
              end
              if available>0 or job or reason=="smelting_in_progress" then viable=true;break end
            end
          end
        end
        if viable then request.specialist_wait_started_tick=tick end
      end
      return request.specialist_wait_started_tick
          and tick<math.min(request.expires_tick,
            request.specialist_wait_started_tick+SharedProjects.PROJECT_WAIT_TICKS)
          or false
    end
  end
  return false
end

return SharedProjects
