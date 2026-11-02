-- Replace previously owned factory assets only through native Worker jobs.
-- This module plans one physical step; Factory owns scheduling and receipts.
local Planner=require("scripts.construction_planner")
local Projects=require("scripts.factory_projects")
local Advanced=require("scripts.advanced_projects")
local Oil=require("scripts.oil_projects")
local Rocket=require("scripts.rocket_projects")
local Recipes=require("scripts.recipes")
local Repairs={}
local RAW={stone=true,wood=true,coal=true,["iron-ore"]=true,["copper-ore"]=true}
local NATIVE_ROOTS={
  ["steel-plate"]=true,["stone-brick"]=true,["plastic-bar"]=true,sulfur=true}
local INHERITED_RECIPES={gear_assembler="iron-gear-wheel",
  science_assembler="automation-science-pack",green_assembler="logistic-science-pack",
  plastic_plant="plastic-bar",sulfur_plant="sulfur",
  engine_assembler="engine-unit",blue_assembler="chemical-science-pack",
  oil_refinery="basic-oil-processing"}

local function count(npc,item)
  local inv=npc.character.get_main_inventory()
  return inv and inv.valid and inv.get_item_count{name=item,quality="normal"} or 0
end

-- The starter budget intentionally refuses non-handcraftable recipes. Late
-- replacement items may depend on existing native furnaces or chemical plants.
-- Stop expansion at those physical producer outputs; never count them as free.
local function late_budget(npc,item)
  local ledger,roots,crafts,visiting={},{},{},{}
  local categories=npc.character.prototype.crafting_categories or {}
  local function need(name,amount)
    if ledger[name]==nil then ledger[name]=count(npc,name) end
    local used=math.min(ledger[name],amount)
    ledger[name]=ledger[name]-used;amount=amount-used
    if amount<=0 then return end
    if RAW[name] or name=="iron-plate" or name=="copper-plate" or NATIVE_ROOTS[name] then
      roots[name]=(roots[name] or 0)+amount;return
    end
    local recipe=Recipes.for_item(npc.force,name,categories)
    if not recipe then error("repair_recipe_locked:"..name,0) end
    if not Recipes.made_in(recipe,categories) then error("repair_native_machine_required:"..name,0) end
    if visiting[name] then error("repair_recipe_cycle:"..name,0) end
    local produced
    for _,product in ipairs(recipe.products or {}) do
      if product.name==name and product.type=="item" and product.amount
          and (not product.probability or product.probability==1) then
        produced=product.amount;break
      end
    end
    if not produced then error("repair_product_unavailable:"..name,0) end
    local batches=math.ceil(amount/produced)
    local ingredients={}
    for _,ingredient in ipairs(recipe.ingredients or {}) do
      if ingredient.type~="item" then error("repair_fluid_recipe:"..name,0) end
      ingredients[#ingredients+1]=ingredient
    end
    table.sort(ingredients,function(a,b) return a.name<b.name end)
    visiting[name]=true
    for _,ingredient in ipairs(ingredients) do
      need(ingredient.name,ingredient.amount*batches)
    end
    visiting[name]=nil
    ledger[name]=ledger[name]+produced*batches-amount
    crafts[#crafts+1]={kind="craft",recipe=recipe.name,count=batches}
  end
  local ok,reason=pcall(need,item,1)
  if not ok then return nil,reason end
  return {raw_deficits=roots,crafts=crafts}
end

local function same_site(entity,npc,name,position,direction)
  return entity and entity.valid and entity.force==npc.force
      and entity.surface==npc.character.surface and entity.name==name
      and math.abs(entity.position.x-position.x)<0.2
      and math.abs(entity.position.y-position.y)<0.2
      and (not direction or entity.direction==direction)
end

local function at_site(npc,name,position,direction)
  for _,entity in ipairs(npc.character.surface.find_entities_filtered{
      name=name,force=npc.force,position=position,radius=.2}) do
    if same_site(entity,npc,name,position,direction) then return entity end
  end
end

local function find_missing(npc,remember)
  if (npc.step or 0)<=8 then return nil end
  -- A designed engineer rebuilds from its plans (and takes burner pairs down on purpose when it
  -- builds electric mining over them): its first drill and furnace are no core site to restore.
  if npc.furnace_position and not npc.design then
    local furnace=at_site(npc,"stone-furnace",npc.furnace_position)
    if furnace then if remember then npc.furnace=furnace end
    else return {key="core_furnace",name="stone-furnace",item="stone-furnace",
      position=npc.furnace_position,direction=defines.direction.north} end
  end
  if npc.module and not npc.design then
    local miner=at_site(npc,"burner-mining-drill",npc.module,npc.module_direction)
    if miner then if remember then npc.miner=miner end
    else return {key="core_miner",name="burner-mining-drill",item="burner-mining-drill",
      position=npc.module,direction=npc.module_direction or defines.direction.north} end
  end
  local project=npc.factory_project
  local plan=project and project.plan
  if not plan or not plan.sites or not plan.steps then return nil end
  local last=project.status=="complete" and #plan.steps or math.max(0,(project.index or 1)-1)
  local seen={}
  local configured={}
  for i=1,last do
    local step=plan.steps[i]
    if step and step.kind=="configure_recipe" and step.target_site and step.recipe then
      configured[step.target_site]=step.recipe
    end
  end
  local function missing_site(key,site)
    if site and (site.surface_index==nil or site.surface_index==npc.character.surface.index)
        and site.position and not Projects.resolve_site(npc,key) then
      local position=site.position
      return {key="project:"..key,site_key=key,name=site.name,item=site.item or site.name,
        position={x=position.x or position[1],y=position.y or position[2]},
        direction=site.direction or defines.direction.north,
        recipe=configured[key] or site.recipe or INHERITED_RECIPES[key]}
    end
  end
  for i=1,last do
    local step=plan.steps[i]
    local key=step and step.kind=="build" and step.target_site
    local site=key and plan.sites[key]
    if site and not seen[key] then
      seen[key]=true
      local missing=missing_site(key,site)
      if missing then return missing end
    end
  end
  local inherited={}
  for key,site in pairs(plan.sites) do
    if not seen[key] and (site.existing==true or site.existing_unit_number) then
      inherited[#inherited+1]=key
    end
  end
  table.sort(inherited)
  for _,key in ipairs(inherited) do
    local missing=missing_site(key,plan.sites[key])
    if missing then return missing end
  end
end

local function owned_furnaces(npc)
  local out={}
  local entities=npc.character.surface.find_entities_filtered{
    position=npc.home,radius=256,force=npc.force,type="furnace"}
  for _,entity in ipairs(entities) do
    if entity.valid and (entity.name=="stone-furnace"
        or entity.name=="steel-furnace") then out[#out+1]=entity end
  end
  table.sort(out,function(a,b)
    local am=a==npc.furnace and 0 or 1
    local bm=b==npc.furnace and 0 or 1
    if am~=bm then return am<bm end
    return (a.unit_number or 0)<(b.unit_number or 0)
  end)
  return out
end

local function plate_job(npc,item,amount)
  local inputs={
    ["iron-plate"]="iron-ore",["copper-plate"]="copper-ore",
    ["steel-plate"]="iron-plate",["stone-brick"]="stone"}
  local ore=inputs[item]
  if not ore then return nil,"unsupported_furnace_product:"..tostring(item) end
  local furnaces=owned_furnaces(npc)
  for _,furnace in ipairs(furnaces) do
    local output=furnace.get_output_inventory()
    local available=output and output.valid and output.get_item_count{name=item,quality="normal"} or 0
    if available>0 then
      return {kind="transfer",target=furnace,item=item,count=math.min(amount,available),mode="withdraw"},"withdraw_plate"
    end
  end
  for _,furnace in ipairs(furnaces) do
    local source=furnace.get_inventory(defines.inventory.crafter_input)
    local existing=source and source.valid and source.get_item_count() or 0
    local matching=source and source.valid and source.get_item_count{name=ore,quality="normal"} or 0
    local output=furnace.get_output_inventory()
    local output_count=output and output.valid and output.get_item_count() or 0
    local matching_output=output and output.valid
        and output.get_item_count{name=item,quality="normal"} or 0
    if existing==matching and output_count==matching_output then
      local fuel=furnace.get_fuel_inventory()
      local fueled=fuel and fuel.valid and fuel.get_item_count{name="coal",quality="normal"} or 0
      if fueled<1 then
        if count(npc,"coal")<4 then return {kind="mine",item="coal",count=4},"mine_fuel" end
        return {kind="transfer",target=furnace,item="coal",count=4,mode="deposit"},"fuel_furnace"
      end
      local feed=item=="steel-plate" and 5 or item=="stone-brick" and 2 or 4
      if matching<feed then
        local deficit=feed-matching
        if ore=="iron-plate" and count(npc,ore)<deficit then
          return plate_job(npc,ore,deficit),"supply_steel_iron"
        end
        if RAW[ore] and count(npc,ore)<deficit then
          return {kind="mine",item=ore,count=math.max(8,deficit)},"mine_ore"
        end
        return {kind="transfer",target=furnace,item=ore,count=deficit,mode="deposit",
          inventory=defines.inventory.crafter_input},"feed_furnace"
      end
      return nil,"smelting_"..item
    end
  end
  return nil,"no_owned_compatible_furnace"
end

function Repairs.next(npc,tick)
  if not npc.character or not npc.character.valid then return nil end
  npc.repair_state=npc.repair_state or {attempts=0}
  local state=npc.repair_state
  if state.configure then
    local spec=state.configure
    -- Some assembling machines accept orientation only after a fluid recipe
    -- is assigned. Find the exact owned replacement first, then configure it.
    local entity=at_site(npc,spec.name,spec.position)
    if not entity then state.configure=nil
    else
      local applied=entity.get_recipe()
      if applied and applied.name==spec.recipe and entity.direction==spec.direction then
        state.configure=nil;state.completed=(state.completed or 0)+1
        state.last_rebuilt=spec.key
      elseif state.pending_job then
        return {kind="wait",reason="repair_worker_pending",target=spec}
      elseif tick<(state.retry_tick or 0) then
        return {kind="wait",reason="repair_backoff",target=spec}
      else
        return {kind="job",stage="configure",target=spec,
          job={kind="configure_recipe",target=entity,recipe=spec.recipe,
            direction=spec.direction}}
      end
    end
  end
  local target=find_missing(npc,true)
  if not target then return nil end
  state.target_key=target.key
  if state.pending_job then return {kind="wait",reason="repair_worker_pending",target=target} end
  if tick<(state.retry_tick or 0) then
    return {kind="wait",reason="repair_backoff",target=target}
  end
  if count(npc,target.item)>=1 then
    return {kind="job",stage="build",target=target,
      job={kind="build",entity_name=target.name,item=target.item,count=1,
        position=target.position,direction=target.direction}}
  end
  local budget,reason=Planner.craft_plan(npc,target.item,1)
  if not budget then
    budget,reason=late_budget(npc,target.item)
    if not budget then
      local project=npc.factory_project
      if project and (project.id=="rocket_materials" or project.id=="platform_launch") then
        local job=Rocket.maintenance(npc,{kind="inventory",item=target.item,count=1})
        if job then return {kind="job",stage="native_supply",target=target,job=job} end
        return {kind="wait",reason="native_supply_pending:"..target.item,target=target}
      end
      return {kind="blocked",reason=reason,target=target}
    end
  end
  for _,item in ipairs({"stone","wood","coal","iron-ore","copper-ore",
      "iron-plate","copper-plate","steel-plate","stone-brick",
      "plastic-bar","sulfur"}) do
    local amount=budget.raw_deficits[item] or 0
    if amount>0 then
      if item=="iron-plate" or item=="copper-plate" or item=="steel-plate"
          or item=="stone-brick" then
        local upstream
        if item=="steel-plate" then
          upstream=Advanced.maintenance(npc,{kind="inventory",item=item,
            count=count(npc,item)+amount,source_site="steel_furnace"})
        elseif item=="stone-brick" then
          upstream=Oil.maintenance(npc,{kind="inventory",item=item,
            count=count(npc,item)+amount,source_site="brick_furnace"},true)
        end
        if upstream then return {kind="job",stage="native_furnace_supply",
          target=target,job=upstream} end
        local job,stage=plate_job(npc,item,amount)
        if job then return {kind="job",stage=stage,target=target,job=job} end
        return {kind="wait",reason=stage,target=target}
      end
      if item=="plastic-bar" or item=="sulfur" then
        local project=npc.factory_project
        local key=item=="plastic-bar" and "plastic_plant" or "sulfur_plant"
        local site=project and project.plan and project.plan.sites[key]
        if not site then return {kind="blocked",reason="native_producer_missing:"..key,
          target=target} end
        local job=Oil.maintenance(npc,{kind="inventory",item=item,
          count=count(npc,item)+amount,source_site=key},true)
        if not job and project and (project.id=="rocket_materials"
            or project.id=="platform_launch") then
          job=Rocket.maintenance(npc,{kind="inventory",item=item,
            count=count(npc,item)+amount})
        end
        if job then return {kind="job",stage="native_chemical_supply",
          target=target,job=job} end
        return {kind="wait",reason="native_producer_pending:"..key,target=target}
      end
      if RAW[item] then
        return {kind="job",stage="mine_raw",target=target,
          job={kind="mine",item=item,count=math.min(12,amount)}}
      end
    end
  end
  for item,amount in pairs(budget.raw_deficits) do
    if amount>0 then return {kind="blocked",reason="unsupported_raw_material:"..item,target=target} end
  end
  local craft=budget.crafts[1]
  if craft then
    return {kind="job",stage="craft",target=target,
      job={kind="craft",recipe=craft.recipe,count=craft.count}}
  end
  return {kind="blocked",reason="repair_material_plan_empty",target=target}
end

function Repairs.bind(npc,job_id,action)
  if not action or action.kind~="job" or not action.target or not job_id then return false end
  local state=npc.repair_state or {attempts=0}
  if state.pending_job then return false end
  state.pending_job=job_id;state.stage=action.stage
  state.target_key=action.target.key
  state.target={key=action.target.key,name=action.target.name,
    position={x=action.target.position.x,y=action.target.position.y},
    direction=action.target.direction,recipe=action.target.recipe}
  npc.repair_state=state
  return true
end

function Repairs.on_receipt(npc,receipt)
  local state=npc.repair_state
  if not state or not state.pending_job
      or (receipt.job_id~=state.pending_job and receipt.id~=state.pending_job) then return false end
  state.pending_job=nil
  if receipt.status=="complete" then
    if state.stage=="build" then
      local target=state.target
      local entity=receipt.entity
      local check_direction=target and target.direction or nil
      if target and target.recipe and entity and entity.valid
          and entity.type=="assembling-machine" then check_direction=nil end
      if target and target.key==state.target_key and same_site(entity,npc,
          target.name,target.position,check_direction) then
        if target.key=="core_miner" then npc.miner=entity
        elseif target.key=="core_furnace" then npc.furnace=entity end
        if target.recipe and entity.type=="assembling-machine" then
          state.configure=target
        else
          state.completed=(state.completed or 0)+1
          state.last_rebuilt=target.key
        end
      else
        state.last_failure="build_receipt_site_mismatch"
        state.attempts=(state.attempts or 0)+1
        state.retry_tick=game.tick+600
        return true
      end
    elseif state.stage=="configure" then
      state.configure=nil
      state.completed=(state.completed or 0)+1
      state.last_rebuilt=state.target_key
    end
    state.attempts=0;state.last_failure=nil;state.retry_tick=nil
  else
    state.attempts=(state.attempts or 0)+1
    state.last_failure=receipt.reason or "repair_worker_failed"
    state.retry_tick=game.tick+math.min(18000,600*state.attempts)
  end
  state.last_receipt_id=receipt.id
  return true
end

function Repairs.observe(npc)
  local target=npc.character and npc.character.valid and find_missing(npc,false) or nil
  local state=npc.repair_state or {}
  return {target=target and {key=target.key,name=target.name,item=target.item,
    position=target.position} or nil,
    pending_job=state.pending_job,attempts=state.attempts or 0,
    retry_tick=state.retry_tick,last_failure=state.last_failure,
    completed=state.completed or 0,last_rebuilt=state.last_rebuilt}
end

return Repairs
