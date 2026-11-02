-- Bounded additions to an earned chemical factory. Every placement, recipe
-- selection, transfer, and craft is an ordinary Worker job funded by native
-- inventory. A layout that cannot fit leaves the existing factory running.
local Capacity={revision="red-circuit-feed-v1"}

local GREEN_KEYS={"capacity_inserter_assembler","capacity_belt_assembler","capacity_green_pole",
  "capacity_inserter_output","capacity_inserter_belt","capacity_inserter_input",
  "capacity_belt_output","capacity_belt_south","capacity_belt_1","capacity_belt_2",
  "capacity_belt_3","capacity_belt_north","capacity_belt_input",
  "capacity_belt_4","capacity_belt_5","capacity_belt_6","capacity_belt_pole",
  "capacity_green_link_pole","capacity_green_input_pole","capacity_green_lower_pole"}
local GREEN_FEED_KEYS={"capacity_belt_south","capacity_belt_1","capacity_belt_2",
  "capacity_belt_3","capacity_belt_4","capacity_belt_5","capacity_belt_6"}

local function green_links(npc,ops)
  local a=ops.resolve(npc,"capacity_inserter_assembler")
  local b=ops.resolve(npc,"capacity_belt_assembler")
  if not (a and b and ops.resolve(npc,"capacity_green_pole")
      and ops.resolve(npc,"capacity_green_link_pole")
      and ops.resolve(npc,"capacity_green_input_pole")
      and ops.resolve(npc,"capacity_green_lower_pole")
      and ops.resolve(npc,"capacity_belt_pole")) then return false end
  local ar,br=a.get_recipe(),b.get_recipe()
  if not ar or ar.name~="inserter" or not br or br.name~="transport-belt" then return false end
  for _,entry in ipairs({{"capacity_inserter_output",defines.direction.east},
      {"capacity_inserter_belt",defines.direction.west},
      {"capacity_inserter_input",defines.direction.east},
      {"capacity_belt_output",defines.direction.east},
      {"capacity_belt_south",defines.direction.south},
      {"capacity_belt_1",defines.direction.south},
      {"capacity_belt_2",defines.direction.south},
      {"capacity_belt_3",defines.direction.south},
      {"capacity_belt_4",defines.direction.south},
      {"capacity_belt_5",defines.direction.south},
      {"capacity_belt_6",defines.direction.south},
      {"capacity_belt_north",defines.direction.west},
      {"capacity_belt_input",defines.direction.east}}) do
    local entity=ops.resolve(npc,entry[1])
    if not entity or entity.direction~=entry[2] then return false end
  end
  return true
end

function Capacity.green_links_ready(npc,ops)
  return green_links(npc,ops)
end

function Capacity.green_components(npc,ops)
  local project=npc.factory_project
  if not project or not project.plan or not project.plan.sites then return nil end
  local plan=project.plan
  if plan.capacity_green_reason then return nil end
  if not plan.sites[GREEN_KEYS[1]] then
    if not ((project.id=="chemical_science" and project.status=="complete")
      or project.id=="rocket_materials") then return nil end
    local green=plan.sites.green_assembler and plan.sites.green_assembler.position
    if not green then return nil end
    local trial={sites=ops.copy(plan.sites),new_keys={}}
    local ok,reason=pcall(function()
      ops.site(npc,trial,GREEN_KEYS[1],"assembling-machine-1",green.x+6,green.y)
      ops.site(npc,trial,GREEN_KEYS[2],"assembling-machine-1",green.x+6,green.y-8)
      ops.site(npc,trial,GREEN_KEYS[3],"small-electric-pole",green.x+6,green.y-3)
      ops.site(npc,trial,GREEN_KEYS[4],"inserter",green.x+4,green.y,defines.direction.east)
      ops.site(npc,trial,GREEN_KEYS[5],"transport-belt",green.x+3,green.y,defines.direction.west)
      ops.site(npc,trial,GREEN_KEYS[6],"inserter",green.x+2,green.y,defines.direction.east)
      ops.site(npc,trial,GREEN_KEYS[7],"inserter",green.x+4,green.y-8,defines.direction.east)
      ops.site(npc,trial,GREEN_KEYS[8],"transport-belt",green.x+3,green.y-8,defines.direction.south)
      ops.site(npc,trial,GREEN_KEYS[9],"transport-belt",green.x+3,green.y-7,defines.direction.south)
      ops.site(npc,trial,GREEN_KEYS[10],"transport-belt",green.x+3,green.y-6,defines.direction.south)
      ops.site(npc,trial,GREEN_KEYS[11],"transport-belt",green.x+3,green.y-5,defines.direction.south)
      ops.site(npc,trial,GREEN_KEYS[12],"transport-belt",green.x+3,green.y-1,defines.direction.west)
      ops.site(npc,trial,GREEN_KEYS[13],"inserter",green.x+2,green.y-1,defines.direction.east)
      ops.site(npc,trial,GREEN_KEYS[14],"transport-belt",green.x+3,green.y-4,defines.direction.south)
      ops.site(npc,trial,GREEN_KEYS[15],"transport-belt",green.x+3,green.y-3,defines.direction.south)
      ops.site(npc,trial,GREEN_KEYS[16],"transport-belt",green.x+3,green.y-2,defines.direction.south)
      ops.site(npc,trial,GREEN_KEYS[17],"small-electric-pole",green.x+8,green.y-7)
      ops.site(npc,trial,GREEN_KEYS[18],"small-electric-pole",green.x+1,green.y-3)
      ops.site(npc,trial,GREEN_KEYS[19],"small-electric-pole",green.x+2,green.y+2)
      ops.site(npc,trial,GREEN_KEYS[20],"small-electric-pole",green.x+2,green.y-7)
      for _,key in ipairs(GREEN_KEYS) do if not trial.sites[key] then error("capacity_green_site_alias:"..key,0) end end
    end)
    if not ok then
      plan.capacity_green_reason=tostring(reason)
      log("NPC green capacity unavailable: "..plan.capacity_green_reason)
      return nil
    end
    for _,key in ipairs(GREEN_KEYS) do plan.sites[key]=trial.sites[key] end
  end
  -- Existing saves may already have the machines but not their power bridges.
  local green=plan.sites.green_assembler and plan.sites.green_assembler.position
  if not green then return nil end
  -- Older earned saves placed the feeder northward, away from its destination.
  -- Correct the saved specifications, then let ordinary Worker rotation jobs
  -- turn each already owned belt without replacing or granting anything.
  for _,key in ipairs(GREEN_FEED_KEYS) do
    if plan.sites[key] then plan.sites[key].direction=defines.direction.south end
  end
  for _,entry in ipairs({{"capacity_green_link_pole",green.x+1,green.y-3},
      {"capacity_green_input_pole",green.x+2,green.y+2},
      {"capacity_green_lower_pole",green.x+2,green.y-7}}) do
    local key=entry[1]
    if not plan.sites[key] then
      local trial={sites=ops.copy(plan.sites),new_keys={}}
      local ok,reason=pcall(ops.site,npc,trial,key,"small-electric-pole",entry[2],entry[3])
      if not ok then
        plan.capacity_green_reason=tostring(reason)
        log("NPC green power link unavailable: "..plan.capacity_green_reason)
        return nil
      end
      plan.sites[key]=trial.sites[key]
    end
    if not ops.resolve(npc,key) then
      local spec=plan.sites[key]
      if ops.carried(npc,spec.item)<1 then return ops.funded_craft(npc,spec.item,1) end
      return {kind="build",entity_name=spec.name,item=spec.item,
        position=spec.position,direction=spec.direction}
    end
  end
  for _,key in ipairs(GREEN_KEYS) do
    local spec=plan.sites[key]
    local entity=ops.resolve(npc,key)
    if not entity then
      if ops.carried(npc,spec.item)<1 then return ops.funded_craft(npc,spec.item,1) end
      return {kind="build",entity_name=spec.name,item=spec.item,position=spec.position,
        direction=spec.direction}
    end
    if (spec.name=="inserter" or spec.name=="transport-belt") and entity.direction~=spec.direction then
      return {kind="configure_direction",target=entity,direction=spec.direction}
    end
  end
  for _,entry in ipairs({{"capacity_inserter_assembler","inserter"},
      {"capacity_belt_assembler","transport-belt"}}) do
    local machine=ops.resolve(npc,entry[1]);local recipe=machine.get_recipe()
    if not recipe or recipe.name~=entry[2] then
      return {kind="configure_recipe",target=machine,recipe=entry[2]}
    end
  end
  if not green_links(npc,ops) then return nil end
  local green=ops.resolve(npc,"green_assembler")
  local green_input=green and green.get_inventory(defines.inventory.crafter_input)
  if not green_input then return nil end
  for _,entry in ipairs({{"capacity_inserter_assembler","inserter",
      {{"iron-plate",8,24},{"iron-gear-wheel",8,24},{"electronic-circuit",8,24}}},
      {"capacity_belt_assembler","transport-belt",
      {{"iron-plate",8,24},{"iron-gear-wheel",8,24}}}}) do
    local machine=ops.resolve(npc,entry[1]);local item=entry[2]
    local output=machine.get_output_inventory()
    if output.get_item_count(item)<8 then
      local input=machine.get_inventory(defines.inventory.crafter_input)
      for _,part in ipairs(entry[3]) do
        local name,minimum,target=part[1],part[2],part[3]
        if input.get_item_count(name)<minimum then
          if ops.carried(npc,name)>0 then
            local job=ops.deposit_input(npc,machine,defines.inventory.crafter_input,name,target)
            if job then return job end
          end
          return ops.funded_craft(npc,name,target)
        end
      end
    end
  end
  return nil
end

-- Reserve smelting capacity uses two ordinary coal-fired furnaces beside the
-- earned ore furnaces. The native electric drill chest supplies iron first;
-- copper and fuel still come from physical mining jobs.
function Capacity.ore_furnaces(npc,ops)
  local project=npc.factory_project
  if not project or not project.plan or not project.plan.sites then return nil end
  if not ((project.id=="chemical_science" and project.status=="complete")
      or project.id=="rocket_materials") then return nil end
  local plan=project.plan
  if plan.capacity_furnace_reason then return nil end
  local keys={"capacity_iron_furnace","capacity_copper_furnace"}
  if not plan.sites[keys[1]] then
    local iron=plan.sites.construction_iron_furnace and plan.sites.construction_iron_furnace.position
    if not iron then return nil end
    local trial={sites=ops.copy(plan.sites),new_keys={}}
    local ok,reason=pcall(function()
      ops.site(npc,trial,keys[1],"stone-furnace",iron.x+2,iron.y-4)
      ops.site(npc,trial,keys[2],"stone-furnace",iron.x+6,iron.y-4)
      for _,key in ipairs(keys) do if not trial.sites[key] then error("capacity_furnace_alias:"..key,0) end end
    end)
    if not ok then
      plan.capacity_furnace_reason=tostring(reason)
      log("NPC reserve furnaces unavailable: "..plan.capacity_furnace_reason)
      return nil
    end
    for _,key in ipairs(keys) do plan.sites[key]=trial.sites[key] end
  end
  for _,key in ipairs(keys) do
    local spec=plan.sites[key]
    if not ops.resolve(npc,key) then
      if ops.carried(npc,"stone")<5 and ops.carried(npc,spec.item)<1 then
        return {kind="mine",item="stone",count=10,search_radius=128}
      end
      if ops.carried(npc,spec.item)<1 then return ops.funded_craft(npc,spec.item,1) end
      return {kind="build",entity_name=spec.name,item=spec.item,
        position=spec.position,direction=spec.direction}
    end
  end
  for _,entry in ipairs({{"capacity_copper_furnace","construction_copper_furnace","copper-ore","copper-plate"},
      {"capacity_iron_furnace","construction_iron_furnace","iron-ore","iron-plate"}}) do
    local spare=ops.resolve(npc,entry[1]);local main=ops.resolve(npc,entry[2])
    local ore,plate=entry[3],entry[4]
    if spare and main and spare.get_output_inventory().get_item_count(plate)<20
        and main.get_output_inventory().get_item_count(plate)<20 then
      local fuel=spare.get_fuel_inventory()
      if fuel and fuel.get_item_count("coal")<2 then
        if ops.carried(npc,"coal")<16 then
          return Capacity.coal_withdraw(npc,ops,24)
            or {kind="mine",item="coal",count=24,search_radius=128}
        end
        return ops.deposit_input(npc,spare,defines.inventory.fuel,"coal",16)
      end
      local input=spare.get_inventory(defines.inventory.crafter_input)
      if input and input.get_item_count(ore)<8 then
        if ops.carried(npc,ore)<40 then
          if ore=="iron-ore" then
            local buffer=ops.resolve(npc,"electric_buffer")
            local stock=buffer and buffer.get_inventory(defines.inventory.chest)
            local available=stock and stock.get_item_count{name="iron-ore",quality="normal"} or 0
            if available>0 then return {kind="transfer",target=buffer,item="iron-ore",
              count=math.min(48,available),mode="withdraw",inventory=defines.inventory.chest} end
          end
          return {kind="mine",item=ore,count=48,search_radius=128}
        end
        return ops.deposit_input(npc,spare,defines.inventory.crafter_input,ore,40)
      end
    end
  end
  return nil
end

-- A real electric drill replenishes a bounded coal chest. It joins the
-- existing steam network through two owned poles; worker fuel transfers may
-- then withdraw only coal actually mined and transported into that chest.
function Capacity.coal_drill(npc,ops)
  local project=npc.factory_project
  if not project or not project.plan or not project.plan.sites then return nil end
  if not ((project.id=="chemical_science" and project.status=="complete")
      or project.id=="rocket_materials") then return nil end
  local plan=project.plan
  if plan.capacity_coal_reason then return nil end
  local keys={"capacity_coal_link","capacity_coal_pole","capacity_coal_drill",
    "capacity_coal_belt1","capacity_coal_belt2","capacity_coal_output","capacity_coal_buffer"}
  if not plan.sites[keys[1]] then
    local surface=npc.character.surface
    local resources=surface.find_entities_filtered{name="coal",position=npc.character.position,radius=128}
    table.sort(resources,function(a,b)
      if a.position.y~=b.position.y then return a.position.y>b.position.y end
      return a.position.x<b.position.x
    end)
    local failure="no_legal_powered_coal_drill_site"
    for _,ore in ipairs(resources) do
      local x,y=math.floor(ore.position.x)+.5,math.floor(ore.position.y)+.5
      local middle={x=x+7,y=y-3}
      local poles=surface.find_entities_filtered{position=middle,radius=7.5,
        type="electric-pole",force=npc.force}
      local powered=false
      for _,pole in ipairs(poles) do
        local dx,dy=pole.position.x-middle.x,pole.position.y-middle.y
        if pole.valid and dx*dx+dy*dy<=7.5*7.5 then powered=true;break end
      end
      if powered then
        local trial={sites=ops.copy(plan.sites),new_keys={}}
        local ok,reason=pcall(function()
          ops.site(npc,trial,keys[1],"small-electric-pole",middle.x,middle.y)
          ops.site(npc,trial,keys[2],"small-electric-pole",x+2,y-2)
          ops.site(npc,trial,keys[3],"electric-mining-drill",x,y)
          ops.site(npc,trial,keys[4],"transport-belt",x,y-2,defines.direction.north)
          ops.site(npc,trial,keys[5],"transport-belt",x,y-3,defines.direction.north)
          ops.site(npc,trial,keys[6],"inserter",x,y-4,defines.direction.south)
          ops.site(npc,trial,keys[7],"wooden-chest",x,y-5)
          for _,key in ipairs(keys) do if not trial.sites[key] then error("capacity_coal_site_alias:"..key,0) end end
        end)
        if ok then
          for _,key in ipairs(keys) do plan.sites[key]=trial.sites[key] end
          break
        end
        failure=tostring(reason)
      end
    end
    if not plan.sites[keys[1]] then
      plan.capacity_coal_reason=failure
      log("NPC native coal buffer unavailable: "..failure)
      return nil
    end
  end
  for _,key in ipairs(keys) do
    local spec=plan.sites[key]
    if not ops.resolve(npc,key) then
      if (spec.item=="small-electric-pole" or spec.item=="wooden-chest")
          and ops.carried(npc,"wood")<4 and ops.carried(npc,spec.item)<1 then
        return {kind="mine",item="wood",count=10,search_radius=128}
      end
      if ops.carried(npc,spec.item)<1 then return ops.funded_craft(npc,spec.item,1) end
      return {kind="build",entity_name=spec.name,item=spec.item,
        position=spec.position,direction=spec.direction}
    end
  end
  return nil
end

function Capacity.coal_withdraw(npc,ops,count)
  local buffer=ops.resolve(npc,"capacity_coal_buffer")
  local inventory=buffer and buffer.get_inventory(defines.inventory.chest)
  local available=inventory and inventory.get_item_count{name="coal",quality="normal"} or 0
  if available>0 then
    return {kind="transfer",target=buffer,item="coal",count=math.min(count,available),
      mode="withdraw",inventory=defines.inventory.chest}
  end
end

local function native_link(npc,ops)
  local machine=ops.resolve(npc,"capacity_red_assembler")
  local output=ops.resolve(npc,"capacity_red_output")
  local belt=ops.resolve(npc,"capacity_red_belt")
  local input=ops.resolve(npc,"capacity_red_input")
  local pole=ops.resolve(npc,"capacity_red_pole")
  if not (machine and output and belt and input and pole) then return false end
  local recipe=machine.get_recipe()
  if not recipe or recipe.name~="advanced-circuit" then return false end
  if output.direction~=defines.direction.east or belt.direction~=defines.direction.west
      or input.direction~=defines.direction.east then return false end
  for line=1,2 do
    for _,stack in pairs(belt.get_transport_line(line).get_contents()) do
      if stack.name~="advanced-circuit" then return false end
    end
  end
  return true
end

function Capacity.red_link_ready(npc,ops)
  return native_link(npc,ops)
end

function Capacity.red_circuits(npc,ops)
  local project=npc.factory_project
  if not project or not project.plan or not project.plan.sites then return nil end
  local plan=project.plan
  if plan.capacity_red_reason then return nil end
  local keys={"capacity_red_assembler","capacity_red_pole","capacity_red_output",
    "capacity_red_belt","capacity_red_input"}
  if not plan.sites[keys[1]] then
    if not ((project.id=="chemical_science" and project.status=="complete")
      or project.id=="rocket_materials") then return nil end
    local blue=plan.sites.blue_assembler and plan.sites.blue_assembler.position
    if not blue then return nil end
    local trial={sites=ops.copy(plan.sites),new_keys={}}
    local ok,reason=pcall(function()
      ops.site(npc,trial,keys[1],"assembling-machine-1",blue.x+6,blue.y)
      ops.site(npc,trial,keys[2],"small-electric-pole",blue.x+4,blue.y+1)
      ops.site(npc,trial,keys[3],"inserter",blue.x+4,blue.y-1,defines.direction.east)
      ops.site(npc,trial,keys[4],"transport-belt",blue.x+3,blue.y-1,defines.direction.west)
      ops.site(npc,trial,keys[5],"inserter",blue.x+2,blue.y-1,defines.direction.east)
      for _,key in ipairs(keys) do if not trial.sites[key] then error("capacity_red_site_alias:"..key,0) end end
    end)
    if not ok then plan.capacity_red_reason=tostring(reason);return nil end
    for _,key in ipairs(keys) do plan.sites[key]=trial.sites[key] end
  end
  for _,key in ipairs(keys) do
    local spec=plan.sites[key]
    local entity=ops.resolve(npc,key)
    if not entity then
      if ops.carried(npc,spec.item)<1 then return ops.funded_craft(npc,spec.item,1) end
      return {kind="build",entity_name=spec.name,item=spec.item,position=spec.position,
        direction=spec.direction}
    end
    if entity.direction~=spec.direction and spec.name~="small-electric-pole" then
      return {kind="configure_direction",target=entity,direction=spec.direction}
    end
  end
  local machine=ops.resolve(npc,keys[1])
  local recipe=machine.get_recipe()
  if not recipe or recipe.name~="advanced-circuit" then
    return {kind="configure_recipe",target=machine,recipe="advanced-circuit",
      direction=plan.sites[keys[1]].direction}
  end
  local blue=ops.resolve(npc,"blue_assembler")
  if not blue or not native_link(npc,ops) then return nil end
  local output=machine.get_output_inventory()
  if output.get_item_count("advanced-circuit")>=8 then return nil end
  local input=machine.get_inventory(defines.inventory.crafter_input)
  for _,entry in ipairs({{"plastic-bar",4,24},{"copper-cable",8,48},{"electronic-circuit",4,24}}) do
    local item,minimum,target=entry[1],entry[2],entry[3]
    if input.get_item_count(item)<minimum then
      if ops.carried(npc,item)>0 then
        local job=ops.deposit_input(npc,machine,defines.inventory.crafter_input,item,target)
        if job then return job end
      end
      if item=="plastic-bar" then return ops.withdraw(npc,"plastic_plant",item,target) end
      return ops.funded_craft(npc,item,target)
    end
  end
  return nil
end

return Capacity
