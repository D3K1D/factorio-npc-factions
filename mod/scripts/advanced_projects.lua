-- Physical next-stage templates. Component hand-crafting/deposit is declared
-- bootstrap work; green packs themselves are made and transported natively.
local Advanced={revision="green-electric-v4"}
local Construction=require("scripts.construction_planner")
local Capacity=require("scripts.production_capacity")
local Conveyor=require("scripts.conveyor_recovery")
local function copy(value)
  if type(value)~="table" then return value end
  local out={} for k,v in pairs(value) do out[k]=copy(v) end return out
end
local function carried(npc,item) return npc.character.get_main_inventory().get_item_count{name=item,quality="normal"} end
local function site(npc,name,x,y,direction)
  return {name=name,item=name,position={x=x,y=y},direction=direction or defines.direction.north,surface_index=npc.character.surface.index}
end
local function resolve(npc,key)
  if key=="electric_buffer" then
    local repaired=Conveyor.resolve_buffer(npc)
    if repaired or npc.conveyor_recovery and npc.conveyor_recovery.separation then return repaired end
  end
  local spec=npc.factory_project and npc.factory_project.plan.sites[key]
  if not spec then return nil end
  for _,entity in pairs(npc.character.surface.find_entities_filtered{position=spec.position,radius=.1,name=spec.name,force=npc.force}) do
    if entity.valid then return entity end
  end
end
local function legal(npc,spec)
  local matches=npc.character.surface.find_entities_filtered{position=spec.position,radius=.1,name=spec.name,force=npc.force}
  if #matches>0 then spec.existing=true;return true end
  return npc.character.surface.can_place_entity{name=spec.name,position=spec.position,direction=spec.direction,force=npc.force,
    build_check_type=defines.build_check_type.manual}
end
local function products(recipe,item)
  for _,p in pairs(recipe.products) do if p.name==item and p.type=="item" and p.amount and (not p.probability or p.probability==1) then return p.amount end end
end
local function base_plan(npc,id)
  local previous=npc.factory_project and npc.factory_project.plan
  if not previous or not previous.sites or not previous.sites.lab then return nil,"starter_project_sites_required" end
  local plan={id=id,revision=Advanced.revision,sites=copy(previous.sites),steps={},layout="starter-expansion-east"}
  local repaired,recovery=Conveyor.resolve_buffer(npc)
  if repaired then
    plan.sites.electric_buffer={name=repaired.name,item=repaired.name,position={x=repaired.position.x,y=repaired.position.y},
      surface_index=repaired.surface.index,direction=repaired.direction,unit_number=repaired.unit_number,existing=true}
    plan.inherited_recovery={kind='paid_electric_copper_separation',ready_tick=recovery.ready_tick,
      buffer_receipt=copy(recovery.build_receipts.buffer),original_buffer=copy(recovery.plan.old_buffer)}
  end
  -- Historical starter build specs now refer to actual existing entities.
  for _,spec in pairs(plan.sites) do spec.existing=true end
  return plan
end
local function add(plan,record) plan.steps[#plan.steps+1]=record end
local function wait(plan,condition) add(plan,{kind="wait",condition=condition}) end
local function amount(plan,npc,item)
  plan._stock=plan._stock or {}
  if plan._stock[item]==nil then plan._stock[item]=carried(npc,item) end
  return plan._stock[item]
end
local consume
local function simulate(npc,plan,item,batches)
  local recipe=npc.force.recipes[item]
  local per=recipe and products(recipe,item)
  if not per then error("craft_ledger_recipe_unavailable:"..item,0) end
  for _,ingredient in ipairs(recipe.ingredients) do
    if ingredient.type~="item" then error("handcraft_fluid_unsupported:"..item,0) end
    consume(npc,plan,ingredient.name,ingredient.amount*batches)
  end
  plan._stock[item]=amount(plan,npc,item)+per*batches
end
consume=function(npc,plan,item,count)
  local missing=math.max(0,count-amount(plan,npc,item))
  if missing>0 then
    if item=="iron-plate" or item=="copper-plate" or item=="stone" or item=="wood" or item=="coal" then
      error("advanced_material_budget_underflow:"..item,0)
    end
    local recipe=npc.force.recipes[item]
    local per=recipe and products(recipe,item)
    if not per then error("ingredient_recipe_unavailable:"..item,0) end
    simulate(npc,plan,item,math.ceil(missing/per))
  end
  plan._stock[item]=amount(plan,npc,item)-count
end
local function build(npc,plan,key)
  local s=plan.sites[key]
  if s.existing then return end
  -- Reserve by placement immediately, before later recipes can consume this item.
  local per=products(npc.force.recipes[s.item],s.item)
  if not per then error("placement_recipe_unavailable:"..s.item,0) end
  local batches=math.ceil(math.max(0,1-amount(plan,npc,s.item))/per)
  if batches>0 then simulate(npc,plan,s.item,batches);add(plan,{kind="craft",recipe=s.item,count=batches}) end
  add(plan,{kind="build",target_site=key,item=s.item,entity_name=s.name,position=s.position,direction=s.direction})
  consume(npc,plan,s.item,1)
end
local function prepare_materials(npc,plan,new_keys,extra)
  local demands=copy(extra or {})
  for _,key in ipairs(new_keys) do local s=plan.sites[key]
    if not legal(npc,s) then error("expansion_site_blocked:"..key,0) end
    if not s.existing then demands[s.item]=(demands[s.item] or 0)+1 end
  end
  local budget,reason=Construction.material_budget(npc,demands,true)
  if not budget then error(reason,0) end
  plan.materials=budget;plan.materials.construction_items=demands
  for _,item in ipairs({"iron-plate","copper-plate","stone","wood","coal"}) do
    plan._stock=plan._stock or {}
    plan._stock[item]=carried(npc,item)+(budget.raw_deficits[item] or 0)
  end
  -- Dedicated furnaces avoid stealing transient plates from an active belt.
  add(plan,{kind="mine",item="stone",count=10,search_radius=128})
  build(npc,plan,"construction_iron_furnace")
  build(npc,plan,"construction_copper_furnace")
  for _,item in ipairs({"wood","stone"}) do if (budget.raw_deficits[item] or 0)>0 then
    add(plan,{kind="mine",item=item,count=budget.raw_deficits[item],search_radius=128})
  end end
  for _,entry in ipairs({{"iron-plate","construction_iron_furnace"},{"copper-plate","construction_copper_furnace"}}) do
    local count=carried(npc,entry[1])+(budget.raw_deficits[entry[1]] or 0)
    if count>0 then wait(plan,{kind="inventory",item=entry[1],count=count,source_site=entry[2]}) end
  end
end
local function add_stock_furnaces(npc,plan)
  local f=plan.sites.iron_furnace.position
  plan.sites.construction_iron_furnace=site(npc,"stone-furnace",f.x,f.y+8)
  plan.sites.construction_copper_furnace=site(npc,"stone-furnace",f.x+4,f.y+8)
end
-- The historical east layout remains first. Alternatives are straight, native
-- cardinal ports from the actual lab; every extra belt is paid by the same bill.
local function green_layout(npc,plan)
  local f=plan.sites.iron_furnace.position
  local fixed={green_assembler=site(npc,"assembling-machine-1",f.x+21.5,f.y+.5),
    green_output=site(npc,"inserter",f.x+19.5,f.y+.5,defines.direction.east),
    green_lab_input=site(npc,"inserter",f.x+15.5,f.y+.5,defines.direction.east),
    green_pole=site(npc,"small-electric-pole",f.x+19.5,f.y-1.5)}
  for i=1,3 do fixed["green_belt"..i]=site(npc,"transport-belt",f.x+19.5-i,f.y+.5,defines.direction.west) end
  local keys={"green_assembler","green_output","green_belt1","green_belt2","green_belt3","green_lab_input","green_pole"}
  local function legal_trial(trial,ordered)
    for _,key in ipairs(ordered) do if not legal(npc,trial[key]) then return false,key end end
    return true
  end
  -- Preserve actual paid green assets when a later caller surveys this stage.
  local saved=true
  for _,key in ipairs(keys) do
    local s=plan.sites[key]
    if not s or #npc.character.surface.find_entities_filtered{position=s.position,radius=.1,name=s.name,force=npc.force}==0 then saved=false;break end
  end
  if saved then
    local ordered=copy(keys)
    if plan.sites.green_input_pole then ordered[#ordered+1]="green_input_pole" end
    local i=4
    while plan.sites["green_belt"..i] do ordered[#ordered+1]="green_belt"..i;i=i+1 end
    return ordered
  end
  if legal_trial(fixed,keys) then
    for _,key in ipairs(keys) do plan.sites[key]=fixed[key] end
    return keys
  end
  local lab_spec=plan.sites.lab
  local lab=lab_spec and npc.character.surface.find_entities_filtered{position=lab_spec.position,radius=.1,name="lab",force=npc.force}[1]
  if not lab then error("owned_lab_required_for_green_layout",0) end
  local anchors={}
  for _,s in pairs(plan.sites) do if s.name=="small-electric-pole" then
    local e=npc.character.surface.find_entities_filtered{position=s.position,radius=.1,name=s.name,force=npc.force}[1]
    if e and e.electric_network_id then anchors[#anchors+1]=e end
  end end
  table.sort(anchors,function(a,b)return a.unit_number<b.unit_number end)
  local function wired(p)
    for _,a in ipairs(anchors) do local dx,dy=p.x-a.position.x,p.y-a.position.y
      if dx*dx+dy*dy<=7.5*7.5 then return a.unit_number end
    end
  end
  local evaluated=0
  for _,distance in ipairs({8}) do
    for _,d in ipairs({{x=1,y=0,pick=defines.direction.east,belt=defines.direction.west,label="east"},
      {x=0,y=1,pick=defines.direction.south,belt=defines.direction.north,label="south"},
      {x=-1,y=0,pick=defines.direction.west,belt=defines.direction.east,label="west"},
      {x=0,y=-1,pick=defines.direction.north,belt=defines.direction.south,label="north"}}) do
      for _,side in ipairs({-1,1}) do
        evaluated=evaluated+1
        local function at(along,across)return lab.position.x+along*d.x-across*d.y,lab.position.y+along*d.y+across*d.x end
        local x,y=at(distance,0)
        local trial={green_assembler=site(npc,"assembling-machine-1",x,y)}
        x,y=at(distance-2,0);trial.green_output=site(npc,"inserter",x,y,d.pick)
        x,y=at(2,0);trial.green_lab_input=site(npc,"inserter",x,y,d.pick)
        x,y=at(5,side*2);trial.green_pole=site(npc,"small-electric-pole",x,y)
        x,y=at(2,side*2);trial.green_input_pole=site(npc,"small-electric-pole",x,y)
        local ordered={"green_assembler","green_output"}
        for i=1,distance-5 do x,y=at(distance-2-i,0)
          local key="green_belt"..i;trial[key]=site(npc,"transport-belt",x,y,d.belt);ordered[#ordered+1]=key
        end
        ordered[#ordered+1]="green_lab_input";ordered[#ordered+1]="green_pole"
        ordered[#ordered+1]="green_input_pole"
        local anchor=wired(trial.green_input_pole.position)
        -- Small poles have native 5x5 coverage. The paid second pole powers
        -- the lab input; the first covers the output and assembler footprint.
        local op=trial.green_output.position;local pp=trial.green_pole.position
        local supplies=math.abs(op.x-pp.x)<=2.5 and math.abs(op.y-pp.y)<=2.5
        if anchor and supplies and legal_trial(trial,ordered) then
          for _,key in ipairs(ordered) do plan.sites[key]=trial[key] end
          plan.layout="lab-anchored-green-"..d.label.."-"..distance
          plan.geometry={kind="lab_anchored_cardinal_green",candidates_evaluated=evaluated,
            candidate_limit=8,lab_unit_number=lab.unit_number,power_anchor_unit_number=anchor,
            source_scope="already_generated_native_lab_neighborhood",additional_belts=distance-8}
          return ordered
        end
      end
    end
  end
  error("no_legal_lab_anchored_green_layout",0)
end
local function green(npc)
  local plan,reason=base_plan(npc,"green_science");if not plan then return nil,reason end
  add_stock_furnaces(npc,plan)
  local new={"construction_iron_furnace","construction_copper_furnace"}
  for _,key in ipairs(green_layout(npc,plan)) do new[#new+1]=key end
  prepare_materials(npc,plan,new,{inserter=40,["transport-belt"]=40})
  for _,key in ipairs(new) do if key~="construction_iron_furnace" and key~="construction_copper_furnace" then build(npc,plan,key) end end
  if plan.geometry then
    for _,key in ipairs({"green_assembler","green_output","green_lab_input"}) do wait(plan,{kind="powered",target_site=key}) end
  end
  add(plan,{kind="research",technology="logistic-science-pack"})
  wait(plan,{kind="technology",technology="logistic-science-pack"})
  add(plan,{kind="configure_recipe",target_site="green_assembler",recipe="logistic-science-pack"})
  for _,item in ipairs({"inserter","transport-belt"}) do
    local per=products(npc.force.recipes[item],item)
    local batches=math.ceil(math.max(0,40-amount(plan,npc,item))/per)
    if batches>0 then simulate(npc,plan,item,batches);add(plan,{kind="craft",recipe=item,count=batches}) end
    add(plan,{kind="transfer",target_site="green_assembler",item=item,count=40,mode="deposit",inventory=defines.inventory.crafter_input})
    consume(npc,plan,item,40)
  end
  add(plan,{kind="research",technology="steel-processing"})
  wait(plan,{kind="technology",technology="steel-processing"})
  add(plan,{kind="research",technology="automation-2"})
  wait(plan,{kind="products_finished",target_site="green_assembler",count=40})
  wait(plan,{kind="technology",technology="automation-2"})
  plan.completion={technology="automation-2",native_green_products=40,requires_physical_belt_delivery=true,
    bootstrap_components="physically_handcrafted_and_transferred",continuous_component_factory=false}
  plan._stock=nil
  return plan
end
local function electric(npc)
  local plan,reason=base_plan(npc,"electric_mining");if not plan then return nil,reason end
  if not npc.force.technologies["electric-mining-drill"].researched then return nil,"electric_mining_technology_required" end
  add_stock_furnaces(npc,plan)
  local resources=npc.character.surface.find_entities_filtered{type="resource",name="iron-ore",position=npc.character.position,radius=128}
  table.sort(resources,function(a,b) if a.position.x~=b.position.x then return a.position.x<b.position.x end return a.position.y<b.position.y end)
  local anchors={}
  for _,s in pairs(plan.sites) do if s.name=="small-electric-pole" then anchors[#anchors+1]=s end end
  if #anchors==0 then return nil,"existing_power_anchor_required" end
  local chosen
  for _,ore in ipairs(resources) do
    local x,y=math.floor(ore.position.x)+.5,math.floor(ore.position.y)+.5
    local within_wire=false
    for _,anchor in ipairs(anchors) do
      local dx,dy=x+2-anchor.position.x,y-2-anchor.position.y
      if dx*dx+dy*dy<=7.5*7.5 then within_wire=true;break end
    end
    if within_wire then
      local trial={electric_drill=site(npc,"electric-mining-drill",x,y),electric_belt1=site(npc,"transport-belt",x,y-2),
        electric_belt2=site(npc,"transport-belt",x,y-3),electric_output=site(npc,"inserter",x,y-4,defines.direction.south),
        electric_buffer=site(npc,"wooden-chest",x,y-5),electric_pole=site(npc,"small-electric-pole",x+2,y-2)}
      -- A paid belt already serving another route cannot silently become an
      -- electric mining component. Its actual orientation is part of its port.
      local ok=true
      for _,key in ipairs({"electric_drill","electric_belt1","electric_belt2","electric_output","electric_buffer","electric_pole"}) do
        local s=trial[key]
        for previous_key,previous in pairs(plan.sites) do
          if previous_key~=key and s.name~="small-electric-pole"
              and math.abs(previous.position.x-s.position.x)<.1
              and math.abs(previous.position.y-s.position.y)<.1 then ok=false;break end
        end
        if not ok or not legal(npc,s) then ok=false;break end
        if s.existing and (s.name=="transport-belt" or s.name=="inserter" or s.name=="electric-mining-drill") then
          local e=npc.character.surface.find_entities_filtered{position=s.position,radius=.1,name=s.name,force=npc.force}[1]
          if not e or e.direction~=s.direction then ok=false;break end
          if key=="electric_drill" then
            local drop=e.drop_position;local wanted=trial.electric_belt1.position
            if math.floor(drop.x)~=math.floor(wanted.x) or math.floor(drop.y)~=math.floor(wanted.y) then ok=false;break end
          elseif key=="electric_output" then
            local pickup,drop=e.pickup_position,e.drop_position
            local input,output=trial.electric_belt2.position,trial.electric_buffer.position
            if math.floor(pickup.x)~=math.floor(input.x) or math.floor(pickup.y)~=math.floor(input.y)
                or math.floor(drop.x)~=math.floor(output.x) or math.floor(drop.y)~=math.floor(output.y) then ok=false;break end
          end
        end
      end
      if ok then chosen=trial;break end
    end
  end
  if not chosen then return nil,"no_legal_powered_iron_drill_site" end
  local new={"construction_iron_furnace","construction_copper_furnace"}
  for key,s in pairs(chosen) do plan.sites[key]=s;new[#new+1]=key end table.sort(new)
  prepare_materials(npc,plan,new)
  for _,key in ipairs(new) do if key~="construction_iron_furnace" and key~="construction_copper_furnace" then build(npc,plan,key) end end
  wait(plan,{kind="powered",target_site="electric_drill"})
  wait(plan,{kind="inventory",target_site="electric_buffer",item="iron-ore",count=10,inventory=defines.inventory.chest})
  plan.completion={native_mined_iron_in_buffer=10,requires_physical_belt_delivery=true}
  plan._stock=nil
  return plan
end
local function steel(npc)
  local plan,reason=base_plan(npc,"steel_processing");if not plan then return nil,reason end
  add_stock_furnaces(npc,plan)
  local f=plan.sites.iron_furnace.position
  plan.sites.steel_furnace=site(npc,"stone-furnace",f.x+8,f.y+8)
  local new={"construction_iron_furnace","construction_copper_furnace","steel_furnace"}
  prepare_materials(npc,plan,new)
  build(npc,plan,"steel_furnace")
  add(plan,{kind="research",technology="steel-processing"})
  wait(plan,{kind="technology",technology="steel-processing"})
  wait(plan,{kind="inventory",item="iron-plate",count=100,source_site="construction_iron_furnace"})
  add(plan,{kind="transfer",target_site="steel_furnace",item="iron-plate",count=100,mode="deposit",inventory=defines.inventory.crafter_input})
  -- The native furnace selects the enabled smelting recipe from its input.
  wait(plan,{kind="products_finished",target_site="steel_furnace",count=20,source_site="steel_furnace"})
  wait(plan,{kind="inventory",item="steel-plate",count=20,source_site="steel_furnace"})
  plan.completion={native_steel_products=20,physically_supplied_iron_plates=100,
    bootstrap_inputs="owned_physical_furnace_output",continuous_component_factory=false}
  plan._stock=nil;return plan
end
function Advanced.plan(npc,id)
  local fn=id=="green_science" and green or id=="electric_mining" and electric or id=="steel_processing" and steel
  if not fn then return nil,"physical_stage_template_unavailable:"..tostring(id) end
  local ok,plan,reason=pcall(fn,npc);if not ok then return nil,plan end return plan,reason
end

-- Root calls this before starter waiting_upkeep. Jobs use real inventories and
-- mine nearby actual resources; this function never produces stock by script.
function Advanced.maintenance(npc,condition)
  local key=condition and condition.source_site
  if key~="construction_iron_furnace" and key~="construction_copper_furnace" and key~="steel_furnace" then
    -- A different producer may be fulfilling an explicitly reserved stock
    -- wait (bricks/plastic/engines). Green logistics must not spend its budget.
    if key then return nil end
    if condition and condition.kind~="technology" and condition.kind~="item_consumption" then return nil end
    -- Declared manual component logistics: packs still emerge from the native
    -- assembler and reach the lab through the previously qualified conveyors.
    local green=resolve(npc,"green_assembler")
    local recipe=green and green.get_recipe()
    if not recipe or recipe.name~="logistic-science-pack" then return nil end
    local input=green.get_inventory(defines.inventory.crafter_input)
    for _,item in ipairs({"inserter","transport-belt"}) do
      if input and not Capacity.green_links_ready(npc,{resolve=resolve})
          and input.get_item_count(item)<4 then
        if carried(npc,item)>0 then
          local deposit=math.min(12-input.get_item_count(item),carried(npc,item),
            input.get_insertable_count{name=item,quality="normal"})
          if deposit>0 then return {kind="transfer",target=green,item=item,count=deposit,
            mode="deposit",inventory=defines.inventory.crafter_input} end
          return nil
        end
        local budget=Construction.craft_plan(npc,item,12)
        if not budget then return nil end
        for _,entry in ipairs({{"iron-plate","construction_iron_furnace"},{"copper-plate","construction_copper_furnace"}}) do
          local deficit=budget.raw_deficits[entry[1]] or 0
          if deficit>0 then
            return Advanced.maintenance(npc,{kind="inventory",item=entry[1],count=carried(npc,entry[1])+deficit,source_site=entry[2]})
          end
        end
        if next(budget.raw_deficits)==nil then
          local per=products(npc.force.recipes[item],item)
          return {kind="craft",recipe=item,count=math.ceil(12/per)}
        end
      end
    end
    return nil
  end
  local furnace=resolve(npc,key);if not furnace then return nil end
  local ore=key=="construction_copper_furnace" and "copper-ore" or key=="steel_furnace" and "iron-plate" or "iron-ore"
  local plate=key=="construction_copper_furnace" and "copper-plate" or key=="steel_furnace" and "steel-plate" or "iron-plate"
  local out=furnace.get_output_inventory()
  local available=out and out.get_item_count{name=plate,quality="normal"} or 0
  local deficit=math.max(0,(condition.count or 1)-carried(npc,plate))
  if deficit==0 and condition.kind=="inventory" then return nil end
  if available>0 and deficit>0 then return {kind="transfer",target=furnace,item=plate,count=math.min(available,deficit),mode="withdraw"} end
  local spare_key=key=="construction_iron_furnace" and "capacity_iron_furnace"
    or key=="construction_copper_furnace" and "capacity_copper_furnace"
  local spare=spare_key and resolve(npc,spare_key)
  local spare_output=spare and spare.get_output_inventory()
  local spare_available=spare_output and spare_output.get_item_count{name=plate,quality="normal"} or 0
  if spare_available>0 and deficit>0 then
    return {kind="transfer",target=spare,item=plate,count=math.min(spare_available,deficit),mode="withdraw"}
  end
  local fuel=furnace.get_fuel_inventory()
  if fuel and fuel.get_item_count("coal")<1 then
    if carried(npc,"coal")<16 then
      return Capacity.coal_withdraw(npc,{resolve=resolve},24)
        or {kind="mine",item="coal",count=24,search_radius=128}
    end
    return {kind="transfer",target=furnace,item="coal",count=16,mode="deposit"}
  end
  local input=furnace.get_inventory(defines.inventory.crafter_input)
  if input and input.get_item_count(ore)<(key=="steel_furnace" and 5 or 2) then
    -- Steel needs actual iron plate production upstream, never a mining request
    -- for an unmineable manufactured item.
    if key=="steel_furnace" and carried(npc,ore)<40 then
      return Advanced.maintenance(npc,{kind="inventory",item="iron-plate",count=40,source_site="construction_iron_furnace"})
    end
    if carried(npc,ore)<40 then
      -- The qualified electric drill already fills an owned chest. Consume
      -- that native output before sending the character to hand-mine iron.
      if ore=="iron-ore" then
        local buffer=resolve(npc,"electric_buffer")
        local stock=buffer and buffer.get_inventory(defines.inventory.chest)
        local available=stock and stock.get_item_count{name="iron-ore",quality="normal"} or 0
        if available>0 then return {kind="transfer",target=buffer,item="iron-ore",
          count=math.min(48,available),mode="withdraw",inventory=defines.inventory.chest} end
      end
      return {kind="mine",item=ore,count=48,search_radius=128}
    end
    return {kind="transfer",target=furnace,item=ore,count=40,mode="deposit",inventory=defines.inventory.crafter_input}
  end
  return nil
end
return Advanced
