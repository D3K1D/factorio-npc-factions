-- Native first-rocket production. Component transport is explicitly manual;
-- every stock change is an ordinary Worker mine/craft/build/transfer job.
local Rocket={revision="native-rocket-v1"}
local Components=require("scripts.rocket_components")
local Recipes=require("scripts.recipes")
local Advanced=require("scripts.advanced_projects")
local Oil=require("scripts.oil_projects")
local Capacity=require("scripts.production_capacity")
local Progression=require("scripts.progression")
local Launch=require("scripts.platform_launch")
local function copy(v) if type(v)~="table" then return v end local t={} for k,x in pairs(v) do t[k]=copy(x) end return t end
local ACCOUNT_ITEMS={"iron-ore","copper-ore","stone","coal","wood","iron-plate","copper-plate","steel-plate",
  "stone-brick","electronic-circuit","advanced-circuit","processing-unit","engine-unit","electric-engine-unit",
  "plastic-bar","sulfur","solid-fuel","rocket-fuel","concrete","low-density-structure","space-platform-foundation",
  "space-platform-starter-pack","rocket-silo","automation-science-pack","logistic-science-pack","chemical-science-pack"}
local ACCOUNT_FLUIDS={"crude-oil","water","steam","petroleum-gas","heavy-oil","light-oil","lubricant","sulfuric-acid"}
local function force_counts(npc)
  local surface=npc.character.surface;local items=npc.force.get_item_production_statistics(surface)
  local fluids=npc.force.get_fluid_production_statistics(surface);local out={items={},fluids={}}
  for _,name in ipairs(ACCOUNT_ITEMS) do if prototypes.item[name] then out.items[name]={produced=items.get_input_count(name),consumed=items.get_output_count(name)} end end
  for _,name in ipairs(ACCOUNT_FLUIDS) do if prototypes.fluid[name] then out.fluids[name]={produced=fluids.get_input_count(name),consumed=fluids.get_output_count(name)} end end
  return out
end
local function held(npc,item) return npc.character.get_main_inventory().get_item_count{name=item,quality="normal"} end
local function resolve(npc,key)
  local s=npc.factory_project and npc.factory_project.plan.sites[key];if not s then return nil end
  local surface=game.surfaces[s.surface_index or npc.character.surface.index]
  for _,e in pairs(surface.find_entities_filtered{position=s.position,radius=.1,name=s.name,force=npc.force}) do if e.valid then return e end end
end
local function yield_of(recipe,item)
  for _,p in pairs(recipe and recipe.products or {}) do if p.type=="item" and p.name==item and p.amount and (not p.probability or p.probability==1) then return p.amount end end
end
-- Minimum whole recipe executions, shared batch remainders and exact
-- upstream material requirements. Research science is a separate real bill;
-- inherited stock is never credited as a grant or silently subtracted.
function Rocket.material_budget(force,demands)
  local raw,fluids,executions,surplus,visiting={},{},{},{},{}
  local roots={['iron-ore']=true,['copper-ore']=true,stone=true,wood=true,coal=true,water=true,['crude-oil']=true}
  local oil={['petroleum-gas']=true,['light-oil']=true,['heavy-oil']=true}
  local function need(item,count)
    local used=math.min(surplus[item] or 0,count);surplus[item]=(surplus[item] or 0)-used;count=count-used
    if count<=0 then return end
    if roots[item] then raw[item]=(raw[item] or 0)+count;return end
    if oil[item] then fluids[item]=(fluids[item] or 0)+count;return end
    local recipe=force.recipes[item=="solid-fuel" and "solid-fuel-from-light-oil" or item];local per
    if recipe then for _,p in pairs(recipe.products) do if p.name==item and p.amount and (not p.probability or p.probability==1) then per=p.amount end end end
    if not per or visiting[item] then error("rocket_budget_recipe_unresolved:"..item,0) end
    local batches=math.ceil(count/per);executions[item]=(executions[item] or 0)+batches;visiting[item]=true
    for _,i in ipairs(recipe.ingredients) do need(i.name,i.amount*batches) end
    visiting[item]=nil;surplus[item]=surplus[item]+batches*per-count
  end
  local keys={} for k in pairs(demands) do keys[#keys+1]=k end;table.sort(keys)
  for _,item in ipairs(keys) do need(item,demands[item]) end
  -- Dedicated advanced refinery reserves heavy/light for first hardware and
  -- retains its gas in a tank. Existing basic oil supplies all required gas;
  -- retained advanced gas cannot be claimed a second time as consumed input.
  local advanced=math.max(math.ceil((fluids['heavy-oil'] or 0)/25),math.ceil((fluids['light-oil'] or 0)/45))
  local basic=math.ceil((fluids['petroleum-gas'] or 0)/45)
  raw['crude-oil']=(raw['crude-oil'] or 0)+100*(advanced+basic)
  raw.water=(raw.water or 0)+50*advanced
  return {raw_requirements=raw,oil_requirements=fluids,recipe_executions=executions,batch_surplus=surplus,
    refinery_executions={basic=basic,advanced=advanced},retained_advanced_gas=advanced*55,
    fuel_and_operating_energy_excluded=true,minimum_recipe_materials=true,existing_items_not_subtracted=true,
    native_pipe_tank_fill_and_overproduction_extra=true,actual_cost_requires_native_statistics_and_receipts=true}
end
local function add(p,s) p.steps[#p.steps+1]=s end
local function wait(p,c) add(p,{kind="wait",condition=c}) end
local function site(npc,p,key,name,x,y,d)
  local s={name=name,item=name,position={x=x,y=y},direction=d or defines.direction.north,surface_index=npc.character.surface.index}
  local existing=npc.character.surface.find_entities_filtered{position=s.position,radius=.1,name=name,force=npc.force}
  if #existing>0 then
    -- Native fluid assemblers normalize to north while recipe-less. A saved
    -- interruption between build/configure must still schedule the physical
    -- configure_recipe direction job rather than reject its owned machine.
    local normalized_empty_assembler=name=="assembling-machine-2" and not existing[1].get_recipe()
    if name~="pipe" and name~="storage-tank" and existing[1].direction~=s.direction and not normalized_empty_assembler then error("rocket_site_orientation:"..key,0) end
    s.existing=true
  elseif not npc.character.surface.can_place_entity{name=name,position=s.position,direction=s.direction,force=npc.force,build_check_type=defines.build_check_type.manual} then
    local blockers=npc.character.surface.find_entities_filtered{area={{x-2.5,y-2.5},{x+2.5,y+2.5}},type={"tree","simple-entity"}}
    local mineable=false
    for _,e in pairs(blockers) do if e.force.name=="neutral" and e.minable and e.prototype.mineable_properties and #(e.prototype.mineable_properties.products or {})>0 then mineable=true end end
    if not mineable then
      local names={npc.character.surface.get_tile(s.position).name}
      for _,e in pairs(npc.character.surface.find_entities_filtered{area={{x-.5,y-.5},{x+.5,y+.5}}}) do names[#names+1]=e.name..":"..e.force.name end
      error("rocket_site_blocked:"..key..":"..table.concat(names,","),0)
    end
    s.clearance_required=true
  end
  local b=prototypes.entity[name].collision_box;local a,z=b.left_top or b[1],b.right_bottom or b[2]
  for k,q in pairs(p.sites) do
    local c=prototypes.entity[q.name].collision_box;local l,r=c.left_top or c[1],c.right_bottom or c[2]
    if x+(a.x or a[1])<q.position.x+(r.x or r[1]) and q.position.x+(l.x or l[1])<x+(z.x or z[1])
      and y+(a.y or a[2])<q.position.y+(r.y or r[2]) and q.position.y+(l.y or l[2])<y+(z.y or z[2]) then
      if q.name==name and q.position.x==x and q.position.y==y and q.direction==s.direction then return k end
      error("rocket_reserved_site_collision:"..key..":"..k,0)
    end
  end
  p.sites[key]=s;p.new_keys[#p.new_keys+1]=key;return key
end
local function pipes(npc,p,key,x1,y1,x2,y2)
  if x1~=x2 and y1~=y2 then error("rocket_non_cardinal_pipe",0) end
  local dx=x1==x2 and 0 or x2>x1 and 1 or -1;local dy=y1==y2 and 0 or y2>y1 and 1 or -1
  for n=0,math.max(math.abs(x2-x1),math.abs(y2-y1)) do site(npc,p,key..n,"pipe",x1+dx*n,y1+dy*n) end
end
local function base(npc,id)
  local old=npc.factory_project and npc.factory_project.plan
  if not old or not old.oil_origin then error("owned_chemical_factory_required",0) end
  if not npc.character.surface.planet or npc.character.surface.planet.name~="nauvis" then error("rocket_goal_requires_nauvis",0) end
  local p={id=id,revision=Rocket.revision,sites=copy(old.sites),oil_origin=copy(old.oil_origin),steps={},new_keys={},
    manual_component_logistics=true,continuous_component_factory=false,qualification="native_material_production_with_manual_component_transport"}
  if old.rocket_accounting then p.rocket_accounting=copy(old.rocket_accounting)
  else
    p.rocket_accounting={started_tick=game.tick,baselines=force_counts(npc),receipts={},mined={},crafted={},transfers={}}
    local prior=npc.factory_project
    if prior and (prior.id=="rocket_materials" or prior.id=="platform_launch") then
      p.rocket_accounting.unknown_before_adoption=true;p.rocket_accounting.prior_project_started_tick=prior.started_tick
    end
  end
  -- Older plans did not retain positive failed-transfer amounts. Recover only
  -- saved native receipts bound to this exact owned silo, without re-delivery.
  local silo=resolve(npc,"rocket_silo")
  local account=p.rocket_accounting
  for _,receipt in pairs(npc.receipts or {}) do
    local target=receipt.target
    local amount=math.max(0,receipt.count or 0)
    local id=receipt.job_id or receipt.id
    if silo and target and target.valid and target.unit_number==silo.unit_number
        and receipt.status=="failed" and receipt.kind=="transfer" and receipt.mode=="deposit"
        and (receipt.item=="processing-unit" or receipt.item=="low-density-structure" or receipt.item=="rocket-fuel")
        and amount>0 and id and not account.receipts[id] then
      account.receipts[id]=true
      local key="rocket-silo:deposit:"..receipt.item
      account.transfers[key]=(account.transfers[key] or 0)+amount
      account.partial_failures=account.partial_failures or {}
      account.partial_failures[id]={reason=receipt.reason,item=receipt.item,actual_count=amount,tick=receipt.tick,
        target_unit_number=target.unit_number,source="saved_native_receipt_import"}
    end
  end
  -- A legacy plan can first gain a canceled receipt during safe migration.
  -- Preserve that observed partial work, then start a distinct native count
  -- interval; the unknown earlier history remains explicitly marked.
  if not p.rocket_accounting.baselines then
    p.rocket_accounting.baselines=force_counts(npc);p.rocket_accounting.native_statistics_started_tick=game.tick
  elseif not p.rocket_accounting.native_statistics_started_tick then
    p.rocket_accounting.native_statistics_started_tick=p.rocket_accounting.started_tick
  end
  for k,s in pairs(p.sites) do if resolve(npc,k) then s.existing=true else p.sites[k]=nil end end
  for _,k in ipairs({"lab","green_assembler","blue_assembler","plastic_plant","sulfur_plant","engine_assembler",
    "construction_iron_furnace","construction_copper_furnace","steel_furnace","brick_furnace","boiler","oil_refinery"}) do
    if not p.sites[k] then error("rocket_owned_infrastructure_required:"..k,0) end
  end
  return p
end

-- The isolated advanced refinery preserves all co-products. First hardware
-- requires 3000 heavy oil and 5500 light oil; 123 cycles supply both. Gas is
-- stored, not deleted, while existing basic oil feeds blue science/plastic.
-- Three 25k tanks are sufficient for this bounded first-rocket subplan.
local function layout(npc,p)
  local o=p.oil_origin;local x,y=o.x+40,o.y+40
  p.rocket_origin={x=x,y=y}
  site(npc,p,"rocket_refinery","oil-refinery",x,y)
  site(npc,p,"rocket_heavy_tank","storage-tank",x-9,y-8)
  site(npc,p,"rocket_light_tank","storage-tank",x-1,y-8)
  site(npc,p,"rocket_gas_tank","storage-tank",x+7,y-8)
  site(npc,p,"rocket_lubricant","chemical-plant",x-9,y-14,defines.direction.south)
  site(npc,p,"rocket_solid_fuel","chemical-plant",x-3,y-14,defines.direction.south)
  site(npc,p,"rocket_electric_engine","assembling-machine-2",x-8,y-20,defines.direction.south)
  site(npc,p,"rocket_fuel_assembler","assembling-machine-2",x+4,y-14,defines.direction.south)
  site(npc,p,"rocket_acid","chemical-plant",x+16,y)
  site(npc,p,"rocket_processing_unit","assembling-machine-2",x+16,y+6)
  site(npc,p,"rocket_concrete","assembling-machine-2",x+24,y)
  -- Refinery heavy and gas lanes turn away from the straight light lane.
  pipes(npc,p,"rh_a",x-2,y-3,x-2,y-4);pipes(npc,p,"rh_b",x-2,y-4,x-8,y-4);pipes(npc,p,"rh_c",x-8,y-4,x-8,y-6)
  pipes(npc,p,"rl_a",x,y-3,x,y-6)
  pipes(npc,p,"rg_a",x+2,y-3,x+2,y-4);pipes(npc,p,"rg_b",x+2,y-4,x+8,y-4);pipes(npc,p,"rg_c",x+8,y-4,x+8,y-6)
  pipes(npc,p,"rh_d",x-10,y-10,x-10,y-12);pipes(npc,p,"rh_e",x-10,y-12,x-8,y-12)
  pipes(npc,p,"lube_a",x-8,y-16,x-8,y-18)
  pipes(npc,p,"rl_b",x-2,y-10,x-2,y-12);pipes(npc,p,"rl_c",x-2,y-12,x+4,y-12)
  pipes(npc,p,"acid_a",x+15,y+2,x+15,y+3);pipes(npc,p,"acid_b",x+15,y+3,x+16,y+3);pipes(npc,p,"acid_c",x+16,y+3,x+16,y+4)
  -- Existing water header is at oil-origin +21,+23 in the qualified template.
  -- Crude crosses it underground; every crossing retains native connectivity.
  pipes(npc,p,"water_bus_a",o.x+21,o.y+24,o.x+21,y+8)
  pipes(npc,p,"water_bus_b",o.x+21,y+8,x+28,y+8)
  pipes(npc,p,"water_bus_c",x+28,y+8,x+28,y-6)
  pipes(npc,p,"water_bus_d",x+28,y-6,x+15,y-6)
  pipes(npc,p,"water_ref",x-1,y+8,x-1,y+3)
  pipes(npc,p,"water_acid",x+15,y-6,x+15,y-2)
  pipes(npc,p,"water_concrete",x+24,y-6,x+24,y-2)
  -- Branch east of the live steam plant and its lake. The existing crude
  -- header already reaches x=+5,y=+11. Cross old water underground.
  pipes(npc,p,"rocket_crude_a",o.x+5,o.y+12,o.x+5,o.y+20)
  site(npc,p,"rocket_crude_cross_a","pipe-to-ground",o.x+5,o.y+21,defines.direction.north)
  site(npc,p,"rocket_crude_cross_b","pipe-to-ground",o.x+5,o.y+26,defines.direction.south)
  pipes(npc,p,"rocket_crude_b",o.x+5,o.y+27,o.x+5,y+10)
  pipes(npc,p,"rocket_crude_c",o.x+5,y+10,x+1,y+10)
  site(npc,p,"crude_water_cross_a","pipe-to-ground",x+1,y+9,defines.direction.south)
  site(npc,p,"crude_water_cross_b","pipe-to-ground",x+1,y+5,defines.direction.north)
  pipes(npc,p,"crude_d",x+1,y+4,x+1,y+3)
  -- Pole trunk follows a clear row above water. Branches are spaced within
  -- native 7.5-tile wire reach; no electric-energy setter is used.
  local last=p.sites.oil_pole7 and p.sites.oil_pole7.position
  if not last then error("rocket_existing_power_anchor_required",0) end
  for yy=last.y+6,o.y+14,6 do site(npc,p,"rocket_power_trunk_y"..yy,"small-electric-pole",last.x,yy) end
  local row=o.y+14
  for xx=last.x,x+25,6 do site(npc,p,"rocket_power_trunk_x"..xx,"small-electric-pole",xx,row) end
  for _,q in ipairs({{-11,-20},{-11,-14},{-5,-14},{2,-14},{10,-14},{-11,-8},{-5,-8},{4,-8},{10,-8},
    {-11,-2},{-4,-2},{4,-2},{8,-2},{14,-2},{20,-2},{26,-2},{14,4},{20,4},{26,4}}) do
    local key="rocket_power_"..q[1].."_"..q[2];site(npc,p,key,"small-electric-pole",x+q[1],y+q[2])
  end
  -- Vertical connection from trunk to producer grid avoids the water pipes.
  for yy=row+6,y-8,6 do site(npc,p,"rocket_power_branch"..yy,"small-electric-pole",x+10,yy) end
  -- Silo has a 9x9 footprint; this separate site avoids the fluid factory.
  site(npc,p,"rocket_silo","rocket-silo",x+34,y+12)
  site(npc,p,"rocket_silo_pole","small-electric-pole",x+29,y+12)
  site(npc,p,"rocket_silo_link","small-electric-pole",x+26,y+10)
  -- Three additional native boilers each feed two engines. All water comes
  -- from the already owned offshore-pump header; no fluid/power setters.
  site(npc,p,"rocket_power_water_cross_a","pipe-to-ground",o.x+21,o.y+49,defines.direction.north)
  site(npc,p,"rocket_power_water_cross_b","pipe-to-ground",o.x+21,o.y+52,defines.direction.south)
  pipes(npc,p,"rocket_power_water_v",o.x+21,o.y+53,o.x+21,o.y+63)
  pipes(npc,p,"rocket_power_water_a",o.x+21,o.y+63,o.x+26,o.y+63)
  for yy=o.y+43,o.y+55,6 do site(npc,p,"rocket_power_capacity_link"..yy,"small-electric-pole",o.x+30,yy) end
  for i=1,3 do
    local bx=o.x+28+(i-1)*12
    site(npc,p,"rocket_boiler"..i,"boiler",bx,o.y+62.5)
    site(npc,p,"rocket_engine"..i.."a","steam-engine",bx,o.y+58)
    site(npc,p,"rocket_engine"..i.."b","steam-engine",bx+6,o.y+58)
    pipes(npc,p,"rocket_steam"..i,bx,o.y+61,bx+6,o.y+61)
    site(npc,p,"rocket_engine_pole"..i.."a","small-electric-pole",bx+2,o.y+56)
    site(npc,p,"rocket_engine_pole"..i.."b","small-electric-pole",bx+8,o.y+56)
    if i<3 then pipes(npc,p,"rocket_power_water_"..i,bx+2,o.y+63,bx+10,o.y+63) end
  end
  p.power_expansion={additional_boilers=3,additional_steam_engines=6,
    capacity_watts=7200000,native_coal_and_offshore_water_required=true}
end
local function native_recipe(p,key,recipe) add(p,{kind="configure_recipe",target_site=key,recipe=recipe,direction=p.sites[key].direction}) end
local function inventory_wait(p,item,count) wait(p,{kind="inventory",item=item,count=count,rocket_supply=true}) end
local function material_plan(npc)
  local p=base(npc,"rocket_materials")
  local dag,reason=Progression.technology_dag(npc.force,"rocket-silo");if not dag then error(reason,0) end
  p.research_budget={packs={},technologies={},remaining_units={},progress_snapshot={},native_labs_required=true,
    existing_lab_buffers_not_subtracted=true}
  for _,name in ipairs(dag.order) do
    local t=npc.force.technologies[name]
    if not t.researched then
      if t.prototype.research_trigger then error("rocket_unfinished_native_trigger:"..name,0) end
      p.research_budget.technologies[#p.research_budget.technologies+1]=name
      local progress=npc.force.current_research==t and npc.force.research_progress or t.saved_progress
      local completed_units=math.floor(t.research_unit_count*(progress or 0)+1e-6)
      local remaining=t.research_unit_count-completed_units
      p.research_budget.remaining_units[name]=remaining;p.research_budget.progress_snapshot[name]=progress or 0
      for _,i in pairs(t.research_unit_ingredients) do p.research_budget.packs[i.name]=(p.research_budget.packs[i.name] or 0)+i.amount*remaining end
      add(p,{kind="research",technology=name});wait(p,{kind="technology",technology=name,rocket_research=true})
    end
  end
  layout(npc,p)
  p.materials={construction_items={},hardware_direct={['rocket-silo']=1,['space-platform-starter-pack']=1,
    ['processing-unit']=50,['low-density-structure']=50,['rocket-fuel']=50},whole_recipe_batches=true,
    inventory_grants=0,fluid_setter_calls=0,energy_setter_calls=0,research_setter_calls=0}
  for _,k in ipairs(p.new_keys) do local s=p.sites[k]
    if not s.existing then
      p.materials.construction_items[s.item]=(p.materials.construction_items[s.item] or 0)+1
      if k~="rocket_silo" then
        if s.clearance_required then wait(p,{kind="rocket_site_clear",target_site=k}) end
        inventory_wait(p,s.item,1);add(p,{kind="build",target_site=k})
      end
    end
  end
  local demands=copy(p.materials.construction_items)
  -- Silo appears in construction_items already. Starter pack and all 50
  -- native rocket-part executions are separate actual production demands.
  demands['space-platform-starter-pack']=1;demands['rocket-part']=50
  p.materials.production_budget=Rocket.material_budget(npc.force,demands)
  for item,count in pairs(p.research_budget.packs) do demands[item]=(demands[item] or 0)+count end
  p.materials.research_and_hardware_budget=Rocket.material_budget(npc.force,demands)
  for _,pair in ipairs({{"rocket_refinery","advanced-oil-processing"},{"rocket_lubricant","lubricant"},
    {"rocket_solid_fuel","solid-fuel-from-light-oil"},{"rocket_electric_engine","electric-engine-unit"},
    {"rocket_fuel_assembler","rocket-fuel"},{"rocket_acid","sulfuric-acid"},
    {"rocket_processing_unit","processing-unit"},{"rocket_concrete","concrete"}}) do native_recipe(p,pair[1],pair[2]) end
  if not p.sites.rocket_silo.existing then
    inventory_wait(p,"rocket-silo",1)
    if p.sites.rocket_silo.clearance_required then wait(p,{kind="rocket_site_clear",target_site="rocket_silo"}) end
    add(p,{kind="build",target_site="rocket_silo"})
  end
  wait(p,{kind="powered",target_site="rocket_silo"})
  inventory_wait(p,"space-platform-starter-pack",1)
  local silo=resolve(npc,"rocket_silo")
  local input=silo and silo.get_inventory(defines.inventory.crafter_input)
  local finished=silo and silo.rocket and silo.rocket.valid and silo.products_finished>=1
  local progress=silo and silo.crafting_progress or 0
  local assembled=finished and 50 or silo and math.min(50,silo.rocket_parts+(progress>0 and 1 or 0)) or 0
  p.materials.remaining_launch_inputs={}
  p.materials.native_funding_snapshot={tick=game.tick,silo_unit=silo and silo.unit_number,
    completed_parts=silo and silo.rocket_parts or 0,in_progress=progress>0,
    funded_part_cycles=assembled,inventory_counts={}}
  for _,item in ipairs({"processing-unit","low-density-structure","rocket-fuel"}) do
    local present=input and input.get_item_count{name=item,quality="normal"} or 0
    local remaining=math.max(0,50-assembled-present)
    p.materials.remaining_launch_inputs[item]=remaining
    p.materials.native_funding_snapshot.inventory_counts[item]=present
    if remaining>0 then inventory_wait(p,item,remaining) end
    while remaining>0 do
      local chunk=math.min(remaining,prototypes.item[item].stack_size,10)
      wait(p,{kind="rocket_input_space",target_site="rocket_silo",item=item,count=chunk,
        inventory=defines.inventory.crafter_input})
      add(p,{kind="transfer",target_site="rocket_silo",item=item,count=chunk,mode="deposit",inventory=defines.inventory.crafter_input})
      remaining=remaining-chunk
    end
  end
  wait(p,{kind="native_rocket_ready",target_site="rocket_silo"})
  p.completion={owned_native_silo=true,native_completed_rocket=true,economic_starter_pack=true,
    manual_component_logistics=true,continuous_component_factory=false}
  return p
end
local function launch_plan(npc)
  local p=base(npc,"platform_launch");local silo=resolve(npc,"rocket_silo")
  if not silo then error("owned_economic_silo_required",0) end
  if held(npc,"space-platform-starter-pack")~=1 then error("exactly_one_economic_starter_pack_required",0) end
  wait(p,{kind="native_rocket_ready",target_site="rocket_silo"})
  add(p,{kind="request_platform",platform_name=npc.name.." orbital platform"})
  add(p,{kind="transfer",target_site="rocket_silo",item="space-platform-starter-pack",count=1,mode="deposit",inventory=defines.inventory.rocket_silo_rocket})
  add(p,{kind="launch_platform",target_site="rocket_silo"})
  wait(p,{kind="platform_winner"})
  p.completion={goal="first_orbital_platform",native_payload_and_hub_required=true};return p
end
function Rocket.plan(npc,id)
  local fn=id=="rocket_materials" and material_plan or id=="platform_launch" and launch_plan
  if not fn then return nil,"unknown_rocket_project" end
  local ok,p=pcall(fn,npc);if not ok then return nil,p end;return p
end
function Rocket.wait_satisfied(npc,c)
  if c.kind=="rocket_input_space" then
    local e=resolve(npc,c.target_site);local inv=e and e.get_inventory(c.inventory)
    return inv and inv.valid and inv.get_insertable_count{name=c.item,quality="normal"}>=(c.count or 1) or false,
      "waiting_for_native_rocket_input_space"
  elseif c.kind=="rocket_site_clear" then
    local s=npc.factory_project.plan.sites[c.target_site]
    return npc.character.surface.can_place_entity{name=s.name,position=s.position,direction=s.direction,force=npc.force,build_check_type=defines.build_check_type.manual},"waiting_for_physical_site_clearance"
  elseif c.kind=="native_rocket_ready" then
    local e=resolve(npc,c.target_site);local r=e and e.rocket
    return e and e.products_finished>=1 and e.rocket_silo_status==defines.rocket_silo_status.rocket_ready and r and r.valid
      and r.force==npc.force and r.attached_cargo_pod and r.attached_cargo_pod.valid or false,"waiting_for_native_rocket"
  elseif c.kind=="platform_winner" then return Launch.observe().winners[npc.id]~=nil,"waiting_for_native_platform_hub" end
  return nil
end
local NATIVE={['processing-unit']='rocket_processing_unit',['electric-engine-unit']='rocket_electric_engine',concrete='rocket_concrete',
  ['rocket-fuel']='rocket_fuel_assembler',['solid-fuel']='rocket_solid_fuel',['engine-unit']='engine_assembler',
  ['plastic-bar']='plastic_plant',sulfur='sulfur_plant'}
local FURNACES={['iron-plate']='construction_iron_furnace',['copper-plate']='construction_copper_furnace',['steel-plate']='steel_furnace',['stone-brick']='brick_furnace'}
local function deposit(e,item,count,inv) return {kind="transfer",target=e,item=item,count=count,mode="deposit",inventory=inv or defines.inventory.crafter_input} end
local function withdraw(e,item,count)
  local out=e and e.get_output_inventory();local n=out and out.get_item_count(item) or 0
  if n>0 then return {kind="transfer",target=e,item=item,count=math.min(n,count),mode="withdraw"} end
end
local supply
local function hand_roots(npc,item,count,roots,visiting)
  local recipe=npc.force.recipes[item];local per=yield_of(recipe,item)
  local categories=npc.character.prototype.crafting_categories or {}
  if FURNACES[item] or NATIVE[item] or item=="iron-ore" or item=="stone" or item=="wood" or item=="coal" then roots[item]=(roots[item] or 0)+count;return end
  if not recipe or not per or not recipe.enabled or not Recipes.made_in(recipe,categories) or visiting[item] then error("rocket_handcraft_unavailable:"..item,0) end
  visiting[item]=true;local batches=math.ceil(count/per)
  for _,i in ipairs(recipe.ingredients) do if i.type~="item" then error("rocket_fluid_handcraft_rejected:"..item,0) end;hand_roots(npc,i.name,i.amount*batches,roots,visiting) end
  visiting[item]=nil
end
local function furnace_supply(npc,item,count)
  local key=FURNACES[item]
  if item~="stone-brick" then return Advanced.maintenance(npc,{kind="inventory",item=item,count=count,source_site=key}) end
  local e=resolve(npc,key);if not e then return nil end
  local job=withdraw(e,item,math.max(0,count-held(npc,item)));if job then return job end
  if e.get_fuel_inventory().get_item_count("coal")<1 then
    if held(npc,"coal")<4 then return Capacity.coal_withdraw(npc,{resolve=resolve},16)
      or {kind="mine",item="coal",count=16,search_radius=128} end
    return deposit(e,"coal",4,defines.inventory.fuel)
  end
  local input=e.get_inventory(defines.inventory.crafter_input)
  if input.get_item_count("stone")<2 then
    if held(npc,"stone")<20 then return {kind="mine",item="stone",count=20,search_radius=128} end
    return deposit(e,"stone",20,defines.inventory.crafter_input)
  end
end
supply=function(npc,item,count,depth)
  depth=(depth or 0)+1;if depth>16 or held(npc,item)>=count then return nil end
  if FURNACES[item] then return furnace_supply(npc,item,count) end
  if item=="iron-ore" or item=="stone" or item=="coal" or item=="wood" then return {kind="mine",item=item,count=count-held(npc,item),search_radius=128} end
  if NATIVE[item] then
    local e=resolve(npc,NATIVE[item]);if not e then return nil end
    local job=withdraw(e,item,count-held(npc,item));if job then return job end
    local recipe=e.get_recipe();if not recipe or not recipe.enabled then return nil end
    local input=e.get_inventory(defines.inventory.crafter_input)
    local per=yield_of(recipe,item);local batches=math.min(5,math.ceil((count-held(npc,item))/per))
    for _,i in ipairs(recipe.ingredients) do if i.type=="item" then
      local need=i.amount*batches-input.get_item_count(i.name)
      if need>0 then
        if held(npc,i.name)>=need then return deposit(e,i.name,need) end
        return supply(npc,i.name,need,depth)
      end
    end end
    return nil
  end
  local recipe=npc.force.recipes[item];local per=yield_of(recipe,item)
  if not recipe or not recipe.enabled or not per then return nil end
  local deficit=count-held(npc,item);local roots={};local ok,reason=pcall(hand_roots,npc,item,deficit,roots,{})
  if not ok then npc.factory_project.supply_blocker=reason;return nil end
  local keys={} for k in pairs(roots) do keys[#keys+1]=k end;table.sort(keys)
  for _,root in ipairs(keys) do if held(npc,root)<roots[root] then return supply(npc,root,roots[root],depth) end end
  return {kind="craft",recipe=item,count=math.ceil(deficit/per)}
end
local original_supply=supply
local function component_ops()
  return {copy=copy,site=site,resolve=resolve,carried=held,supply=original_supply}
end
supply=function(npc,item,count,depth)
  local c=npc.factory_project and npc.factory_project.plan.components
  if c and c.phase=="native_production" and (item=="electronic-circuit" or item=="advanced-circuit") then
    return Components.withdraw(npc,component_ops(),item,count) or Components.upkeep(npc,component_ops())
  end
  return original_supply(npc,item,count,depth)
end
local function fuel(npc)
  local miner=npc.miner
  local miner_fuel=miner and miner.valid and miner.force==npc.force and miner.get_fuel_inventory()
  if miner_fuel and miner_fuel.get_item_count("coal")<1 then
    if held(npc,"coal")<4 then return Capacity.coal_withdraw(npc,{resolve=resolve},16)
      or {kind="mine",item="coal",count=16,search_radius=128} end
    return deposit(miner,"coal",4,defines.inventory.fuel)
  end
  for _,key in ipairs({"boiler","rocket_boiler1","rocket_boiler2","rocket_boiler3","iron_furnace","copper_furnace","copper_drill","copper_ore_inserter"}) do
    local e=resolve(npc,key);local inv=e and e.get_fuel_inventory()
    local is_boiler=key=="boiler" or key:sub(1,13)=="rocket_boiler"
    local minimum=is_boiler and 8 or 1;local count=is_boiler and 24 or 4
    if inv and inv.get_item_count("coal")<minimum then
      if held(npc,"coal")<count then return Capacity.coal_withdraw(npc,{resolve=resolve},is_boiler and 40 or 16)
        or {kind="mine",item="coal",count=is_boiler and 40 or 16,search_radius=128} end
      return deposit(e,"coal",count,defines.inventory.fuel)
    end
  end
  local plastic=resolve(npc,"plastic_plant");local inv=plastic and plastic.get_inventory(defines.inventory.crafter_input)
  if inv and inv.get_item_count("coal")<2 then
    if held(npc,"coal")<8 then return Capacity.coal_withdraw(npc,{resolve=resolve},16)
      or {kind="mine",item="coal",count=16,search_radius=128} end
    return deposit(plastic,"coal",8)
  end
end
local function acid(npc)
  local e=resolve(npc,"rocket_acid");local recipe=e and e.get_recipe();if not recipe then return nil end
  -- Stop adding item feed when a full fluid output buffer already exists.
  local fluids=e.get_fluid_contents();if (fluids['sulfuric-acid'] or 0)>=80 then return nil end
  local input=e.get_inventory(defines.inventory.crafter_input)
  for _,i in ipairs(recipe.ingredients) do if i.type=="item" then
    local count=i.amount*2-input.get_item_count(i.name)
    if count>0 then if held(npc,i.name)>=count then return deposit(e,i.name,count) end;return supply(npc,i.name,count) end
  end end
end
function Rocket.maintenance(npc,c)
  local project=npc.factory_project
  if not project or (project.id~="rocket_materials" and project.id~="platform_launch") then return nil end
  local function observed(job,source)
    project.rocket_maintenance={tick=game.tick,source=source,kind=job and job.kind,item=job and job.item,
      count=job and job.count,target=job and job.target and job.target.valid and job.target.name}
    return job
  end
  local job=fuel(npc);if job then return observed(job,"physical_fuel") end
  if c and c.kind=="rocket_site_clear" then
    local s=project.plan.sites[c.target_site];local b=prototypes.entity[s.name].collision_box;local a,z=b.left_top or b[1],b.right_bottom or b[2]
    local position=s.position
    local blockers=npc.character.surface.find_entities_filtered{area={{position.x+(a.x or a[1]),position.y+(a.y or a[2])},
      {position.x+(z.x or z[1]),position.y+(z.y or z[2])}},type={"tree","simple-entity"}}
    table.sort(blockers,function(a,b) return (a.unit_number or 0)<(b.unit_number or 0) end)
    for _,e in ipairs(blockers) do if e.force.name=="neutral" and e.minable then
      for _,product in ipairs(e.prototype.mineable_properties.products or {}) do
        if product.type=="item" and product.amount and (not product.probability or product.probability==1) then
          return {kind="mine",target=e,item=product.name,count=product.amount}
        end
      end
    end end
    project.supply_blocker="rocket_unmineable_site_obstacle:"..c.target_site;return nil
  end
  if c and c.kind=="technology" then
    -- Oil's research controller observes green/blue lab buffers and preserves
    -- bounded priority between them. Independent green-first fallback would
    -- spend component stock before the depleted blue line can progress.
    return observed(Oil.research_maintenance(npc),"chemical_research")
  end
  job=Components.next(npc,component_ops());if job then return observed(job,"dedicated_circuit_factory") end
  job=acid(npc);if job then return observed(job,"native_acid_feed") end
  if c and c.kind=="inventory" and not c.target_site then return observed(supply(npc,c.item,c.count or 1),"material_supply") end
  return observed(nil,"native_process_wait")
end
-- Project interpreter calls this for a matching terminal receipt.
-- These are observed physical inputs, not estimated recipe expansion costs.
function Rocket.on_receipt(npc,receipt)
  Components.on_receipt(npc,receipt)
  local p=npc.factory_project
  if not p or (p.id~="rocket_materials" and p.id~="platform_launch") then return end
  local deliberate_cancel=receipt.status=="canceled" and
    (receipt.reason=="combat_interrupt" or receipt.reason=="project_replan")
  local positive_failure=receipt.status=="failed" and ((receipt.count or 0)>0 or (receipt.completed or 0)>0)
  if receipt.status~="complete" and not deliberate_cancel and not positive_failure then return end
  local a=p.plan.rocket_accounting
  if not a then
    a={started_tick=receipt.tick,unknown_before_adoption=true,receipts={},mined={},crafted={},transfers={}}
    p.plan.rocket_accounting=a
  end
  local id=receipt.job_id or receipt.id;if not id or a.receipts[id] then return end;a.receipts[id]=true
  local count=math.max(0,receipt.count or 0);local completed=math.max(0,receipt.completed or 0)
  if deliberate_cancel then
    a.deliberate_cancellations=a.deliberate_cancellations or {}
    a.deliberate_cancellations[id]={reason=receipt.reason,kind=receipt.kind,item=receipt.item,recipe=receipt.recipe,
      actual_count=count,completed_batches=completed,tick=receipt.tick}
  end
  if positive_failure then
    a.partial_failures=a.partial_failures or {}
    a.partial_failures[id]={reason=receipt.reason,kind=receipt.kind,item=receipt.item,recipe=receipt.recipe,
      actual_count=count,completed_batches=completed,tick=receipt.tick}
  end
  if receipt.kind=="mine" and receipt.item and count>0 then a.mined[receipt.item]=(a.mined[receipt.item] or 0)+count
  elseif receipt.kind=="craft" and receipt.recipe and completed>0 then a.crafted[receipt.recipe]=(a.crafted[receipt.recipe] or 0)+completed
  elseif receipt.kind=="transfer" and receipt.item and count>0 then
    local e=receipt.target;local key=(e and e.valid and e.name or "unavailable")..":"..tostring(receipt.mode)..":"..receipt.item
    a.transfers[key]=(a.transfers[key] or 0)+count
  end
end
function Rocket.observe(npc)
  local p=npc.factory_project
  if not p or (p.id~="rocket_materials" and p.id~="platform_launch") then return nil end
  local a=p.plan.rocket_accounting;local counts=force_counts(npc);local delta=copy(counts)
  if a and a.baselines then for kind,entries in pairs(delta) do for name,value in pairs(entries) do
    local old=a.baselines[kind][name] or {};value.produced=value.produced-(old.produced or 0);value.consumed=value.consumed-(old.consumed or 0)
  end end end
  local retained,machines,boilers,generators,networks,seen={},{},{},{},{},{}
  local engine_count=0
  for key in pairs(p.plan.sites) do local e=resolve(npc,key)
    if e and not seen[e.unit_number] then
      seen[e.unit_number]=true
      for name,amount in pairs(e.get_fluid_contents()) do retained[name]=(retained[name] or 0)+amount end
      if e.name=="steam-engine" then
        engine_count=engine_count+1
        generators[key]={status=e.status,network_id=e.electric_network_id,
          generated_watts_last_tick=e.energy_generated_last_tick*60,fluids=e.get_fluid_contents()}
      end
      if e.type=="electric-pole" and e.electric_network_id and not networks[e.electric_network_id] then
        local stats=e.electric_network_statistics
        -- Native electric statistics reverse the item/fluid convention:
        -- input_counts contains consumers, output_counts contains generators.
        networks[e.electric_network_id]={lifetime_generated_joules=stats.output_counts,
          lifetime_consumed_joules=stats.input_counts,scope="entire_connected_native_network"}
      end
      if e.type=="boiler" then local fuel=e.get_fuel_inventory()
        boilers[key]={energy=e.energy,status=e.status,coal=fuel.get_item_count("coal"),
          remaining_burning_fuel=e.burner.remaining_burning_fuel,fluids=e.get_fluid_contents()}
      end
      if key:sub(1,7)=="rocket_" and (e.type=="assembling-machine" or e.type=="rocket-silo") then
        machines[key]={energy=e.energy,status=e.status,powered=e.is_connected_to_electric_network(),products_finished=e.products_finished,
          fluids=e.get_fluid_contents(),input=e.get_inventory(e.type=="rocket-silo" and defines.inventory.crafter_input or defines.inventory.crafter_input).get_contents(),
          output=e.get_output_inventory() and e.get_output_inventory().get_contents()}
      end
    end
  end
  local boiler=resolve(npc,"boiler");local fuel=boiler and boiler.get_fuel_inventory()
  return {component_factory=Components.observe(npc,component_ops()),started_tick=a and a.started_tick,baseline_known=a and a.baselines~=nil or false,
    native_statistics_started_tick=a and a.native_statistics_started_tick,
    unknown_before_adoption=a and a.unknown_before_adoption or a==nil,
    prior_project_started_tick=a and a.prior_project_started_tick,
    force_production_and_consumption=delta,counts_are_lifetime_totals_without_baseline=not(a and a.baselines),
    actual_mined_receipts=a and copy(a.mined) or {},actual_craft_batches=a and copy(a.crafted) or {},
    actual_transfer_receipts=a and copy(a.transfers) or {},retained_owned_plan_fluids=retained,machines=machines,
    deliberate_cancellations=a and copy(a.deliberate_cancellations or {}) or {},
    partial_failures=a and copy(a.partial_failures or {}) or {},
    remaining_launch_inputs=copy((p.plan.materials or {}).remaining_launch_inputs or {}),
    native_funding_snapshot=copy((p.plan.materials or {}).native_funding_snapshot),
    boiler={energy=boiler and boiler.energy,status=boiler and boiler.status,coal=fuel and fuel.get_item_count("coal") or 0},
    boilers=boilers,generators=generators,native_electric_networks=networks,
    power_capacity_watts=engine_count*900000,power_capacity_basis="resolved_owned_native_steam_engine_count",
    minimum_budget_excludes_native_buffer_fill_and_overproduction=true}
end
function Rocket.bind(npc,id) return Components.bind(npc,id) end
Rocket.resolve_site=resolve
Rocket.supply=supply
return Rocket
