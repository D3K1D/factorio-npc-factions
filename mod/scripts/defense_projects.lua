-- A saved physical gun-turret project. Planning describes work; only Worker,
-- native furnaces/labs and the native turret perform that work.
local Defense={revision="basic-defense-v2",initial_magazines=20,personal_bullets=60,
  personal_target_bullets=100}
local Construction=require("scripts.construction_planner")
local Advanced=require("scripts.advanced_projects")

local function copy(value)
  if type(value)~="table" then return value end
  local out={} for k,v in pairs(value) do out[k]=copy(v) end return out
end
local function carried(npc,item)
  return npc.character.get_main_inventory().get_item_count{name=item,quality="normal"}
end
local function fresh_inventory(inventory)
  local count=0
  local size=prototypes.item["firearm-magazine"].magazine_size
  for i=1,#inventory do local s=inventory[i]
    if s.valid_for_read and s.name=="firearm-magazine" and s.quality.name=="normal"
        and s.health==1 then count=count+math.max(0,s.count-(s.ammo<size and 1 or 0)) end
  end
  return count
end
local function fresh_magazines(npc) return fresh_inventory(npc.character.get_main_inventory()) end
local function inventory_bullets(inventory)
  local count=0
  for i=1,#inventory do local s=inventory[i]
    if s.valid_for_read and s.name=="firearm-magazine" then
      count=count+s.ammo+(s.count-1)*prototypes.item[s.name].magazine_size
    end
  end
  return count
end
local function equipped_bullets(npc)
  return inventory_bullets(npc.character.get_inventory(defines.inventory.character_ammo))
end

local function personal_bullets(npc)
  return equipped_bullets(npc)+inventory_bullets(npc.character.get_main_inventory())
end
local function equipped_surplus(npc,wanted)
  local inventory=npc.character.get_inventory(defines.inventory.character_ammo)
  local count=math.min(wanted,fresh_inventory(inventory),
    math.max(0,math.floor((equipped_bullets(npc)-Defense.personal_bullets)/10)))
  if count>0 then return {kind="transfer",target=npc.character,item="firearm-magazine",mode="withdraw",
    count=count,inventory=defines.inventory.character_ammo} end
end
local function spec(npc,name,x,y)
  return {name=name,item=name,position={x=x,y=y},direction=defines.direction.north,
    surface_index=npc.character.surface.index}
end
local function find(npc,s)
  if not s or s.surface_index~=npc.character.surface.index then return nil end
  for _,entity in ipairs(npc.character.surface.find_entities_filtered{
      position=s.position,radius=.1,name=s.name,force=npc.force}) do
    if entity.valid then return entity end
  end
end
local function resolve(npc,key)
  local plan=npc.factory_project and npc.factory_project.plan
  return find(npc,plan and plan.sites and plan.sites[key])
end
local function box(s)
  local b=prototypes.entity[s.name].collision_box
  local a,c=b.left_top or b[1],b.right_bottom or b[2]
  return {s.position.x+(a.x or a[1]),s.position.y+(a.y or a[2]),
    s.position.x+(c.x or c[1]),s.position.y+(c.y or c[2])}
end
local function legal(npc,s,sites)
  if find(npc,s) then s.existing=true;return true end
  if not npc.character.surface.can_place_entity{name=s.name,position=s.position,
      direction=s.direction,force=npc.force,build_check_type=defines.build_check_type.manual} then return false end
  local a=box(s)
  for _,other in pairs(sites) do local b=box(other)
    if a[1]<b[3] and b[1]<a[3] and a[2]<b[4] and b[2]<a[4] then return false end
  end
  return true
end
local function choose_site(npc,plan,key,name,preferred)
  if plan.sites[key] and find(npc,plan.sites[key]) then
    plan.sites[key].existing=true;return plan.sites[key]
  end
  -- Bounded, deterministic candidates. Existing camp/power sites remain
  -- reserved; no clearing, teleportation or long-range placement is implied.
  for ring=0,10 do
    for _,offset in ipairs({{0,ring*2},{ring*2,0},{0,-ring*2},{-ring*2,0}}) do
      local s=spec(npc,name,math.floor(preferred.x)+offset[1],math.floor(preferred.y)+offset[2])
      if legal(npc,s,plan.sites) then plan.sites[key]=s;return s end
    end
  end
  return nil,"defense_site_blocked:"..key
end
local function add(plan,step) plan.steps[#plan.steps+1]=step end
local function build(plan,key)
  local s=plan.sites[key]
  if not s.existing then add(plan,{kind="build",target_site=key}) end
end
local function basic(npc)
  local previous=npc.factory_project and npc.factory_project.plan
  if not previous or not previous.sites or not previous.sites.lab or not previous.sites.iron_furnace then
    return nil,"starter_project_sites_required"
  end
  if not find(npc,previous.sites.lab) then return nil,"owned_science_lab_required" end
  local plan={id="basic_defense",revision=Defense.revision,sites=copy(previous.sites),steps={},
    layout="bounded-camp-defense",completion={technology="gun-turret",native_turret=true,
      initial_magazines=Defense.initial_magazines,personal_bullets=Defense.personal_bullets,
      automatic_ammunition_factory=false}}
  for _,s in pairs(plan.sites) do s.existing=find(npc,s)~=nil end
  local f=previous.sites.iron_furnace.position
  local chosen,reason=choose_site(npc,plan,"defense_turret","gun-turret",{x=f.x-10,y=f.y-8})
  if not chosen then return nil,reason end
  for _,entry in ipairs({{"construction_iron_furnace",f.x,f.y+8},
      {"construction_copper_furnace",f.x+4,f.y+8}}) do
    chosen,reason=choose_site(npc,plan,entry[1],"stone-furnace",{x=entry[2],y=entry[3]})
    if not chosen then return nil,reason end
  end
  local new_furnaces=0
  for _,key in ipairs({"construction_iron_furnace","construction_copper_furnace"}) do
    if not plan.sites[key].existing then new_furnaces=new_furnaces+1 end
  end
  -- Bootstrap stock furnaces are built before waiting for their plates. They
  -- require only mined stone; they do not take plates off the science belts.
  local furnace_budget
  furnace_budget,reason=Construction.material_budget(npc,{["stone-furnace"]=new_furnaces},false)
  if not furnace_budget then return nil,reason end
  local stone=furnace_budget.raw_deficits.stone or 0
  if stone>0 then add(plan,{kind="mine",item="stone",count=stone,search_radius=128}) end
  for _,craft in ipairs(furnace_budget.crafts) do add(plan,copy(craft)) end
  build(plan,"construction_iron_furnace");build(plan,"construction_copper_furnace")
  add(plan,{kind="research",technology="gun-turret"})
  add(plan,{kind="wait",condition={kind="technology",technology="gun-turret"}})
  -- A used leading magazine stays intact; its full magazine tails can move.
  -- Already equipped ammunition is NOT treated as available turret stock.
  local excluded=carried(npc,"firearm-magazine")-fresh_magazines(npc)
  local personal=math.max(0,math.ceil((Defense.personal_bullets-equipped_bullets(npc))/10))
  local demands={["firearm-magazine"]=Defense.initial_magazines+personal+excluded}
  if not plan.sites.defense_turret.existing then demands["gun-turret"]=1 end
  local budget
  budget,reason=Construction.material_budget(npc,demands,true)
  if not budget then return nil,reason end
  plan.materials=copy(budget);plan.materials.construction_items=demands
  plan.materials.stock_furnaces=furnace_budget
  plan.materials.excluded_magazines=excluded
  plan.materials.personal_fresh_magazines=personal
  for _,entry in ipairs({{"iron-plate","construction_iron_furnace"},
      {"copper-plate","construction_copper_furnace"}}) do
    local deficit=budget.raw_deficits[entry[1]] or 0
    if deficit>0 then add(plan,{kind="wait",condition={kind="inventory",item=entry[1],
      count=carried(npc,entry[1])+deficit,source_site=entry[2]}}) end
  end
  for _,item in ipairs({"stone","wood","coal"}) do local count=budget.raw_deficits[item] or 0
    if count>0 then add(plan,{kind="mine",item=item,count=count,search_radius=128}) end
  end
  for _,craft in ipairs(budget.crafts) do add(plan,copy(craft)) end
  build(plan,"defense_turret")
  -- Native handcrafting auto-loads an equipped gun. The saved wait lets
  -- maintenance physically withdraw only surplus full magazines into main.
  add(plan,{kind="wait",condition={kind="inventory",item="firearm-magazine",
    count=Defense.initial_magazines+excluded,source_site="defense_ammo"}})
  add(plan,{kind="transfer",target_site="defense_turret",item="firearm-magazine",
    count=Defense.initial_magazines,mode="deposit",inventory=defines.inventory.turret_ammo})
  return plan
end

function Defense.plan(npc,id)
  if id~="basic_defense" then return nil,"unknown_defense_project" end
  if not (npc and npc.character and npc.character.valid) then return nil,"actor_unavailable" end
  local okay,plan,reason=pcall(basic,npc)
  if not okay then return nil,plan end
  return plan,reason
end

local function bullets(turret)
  return inventory_bullets(turret.get_inventory(defines.inventory.turret_ammo))
end

-- Returns an ordinary physical Worker job. Empty ammo is replenished from
-- carried fresh magazines or from smelted iron; native shots alone spend ammo.
function Defense.maintenance(npc,condition)
  if not (npc and npc.character and npc.character.valid) then return nil end
  if condition then
    if condition.source_site=="defense_ammo" then
      return equipped_surplus(npc,math.max(0,Defense.initial_magazines-fresh_magazines(npc)))
    end
    return Advanced.maintenance(npc,condition)
  end
  local turret=resolve(npc,"defense_turret")
  if not turret then return nil end
  local fresh=fresh_magazines(npc)
  local personal=math.max(0,math.ceil((Defense.personal_bullets-equipped_bullets(npc))/10))
  local body_shortfall=math.max(0,personal-fresh)
  -- Maintain personal ammunition first, including an unarmed approaching
  -- danger. Combat still owns the body while it is actively fighting/fleeing.
  local desired=body_shortfall
  if desired==0 then
    if bullets(turret)>=40 then return nil end
    desired=math.max(1,math.ceil((100-bullets(turret))/10))
    if fresh>personal then return {kind="transfer",target=turret,item="firearm-magazine",mode="deposit",
      count=math.min(fresh-personal,desired),inventory=defines.inventory.turret_ammo} end
    local surplus=equipped_surplus(npc,desired)
    if surplus then return surplus end
  end
  local plate=carried(npc,"iron-plate")
  if plate>=4 then return {kind="craft",recipe="firearm-magazine",count=math.min(desired,math.floor(plate/4))} end
  return Advanced.maintenance(npc,{kind="inventory",item="iron-plate",count=4,
    source_site="construction_iron_furnace"})
end

-- Keep the starter pistol useful while the factory project is still active.
-- Saved credits follow real Worker receipts. The actor never spends carried
-- construction iron, ore, or coal merely because those items are present.
local function personal_supply(npc)
  npc.personal_ammo_supply=npc.personal_ammo_supply or
    {ore_credit=0,coal_credit=0,pending_ore=0,claimed_plates=0}
  return npc.personal_ammo_supply
end

local function personal_furnace(npc)
  local furnace=resolve(npc,"iron_furnace")
  if not (furnace and npc.furnace==furnace and furnace.force==npc.character.force
      and furnace.surface==npc.character.surface and furnace.name=="stone-furnace") then return nil end
  local source=furnace.get_inventory(defines.inventory.crafter_input)
  local output=furnace.get_output_inventory()
  local fuel=furnace.get_fuel_inventory()
  if not (source and source.valid and output and output.valid and fuel and fuel.valid) then return nil end
  local recipe=furnace.get_recipe()
  if recipe and recipe.name~="iron-plate" then return nil end
  for i=1,#source do local stack=source[i]
    if stack.valid_for_read and (stack.name~="iron-ore" or stack.quality.name~="normal") then return nil end
  end
  for i=1,#output do local stack=output[i]
    if stack.valid_for_read and (stack.name~="iron-plate" or stack.quality.name~="normal") then return nil end
  end
  return furnace,source,output,fuel
end

local function abandon_personal_batch(s)
  s.pending_ore=0;s.claimed_plates=0;s.products_at_deposit=nil
  s.wait_started_tick=nil;s.furnace_unit=nil
  s.batch_started_tick=nil;s.fresh_fuel=nil
end

local function mine_personal(item,count)
  return {kind="mine",item=item,count=count,search_radius=128}
end

-- Returning false asks Factory to hold its active project for this cadence
-- while the actor's own furnace performs bounded, native smelting.
function Defense.personal_maintenance(npc)
  if not (npc and npc.character and npc.character.valid and npc.factory_project) then return nil end
  local bullets=personal_bullets(npc)
  local s=personal_supply(npc)
  if bullets>=Defense.personal_target_bullets then
    npc.personal_ammo_funded_iron=0
    npc.personal_ammo_supply=nil
    return nil
  end
  s.ore_credit=math.min(math.max(0,s.ore_credit or 0),carried(npc,"iron-ore"))
  s.coal_credit=math.min(math.max(0,s.coal_credit or 0),carried(npc,"coal"))
  local funded=math.min(math.max(0,npc.personal_ammo_funded_iron or 0),carried(npc,"iron-plate"))
  npc.personal_ammo_funded_iron=funded
  if bullets>=Defense.personal_bullets and funded<4 then return nil end
  local wanted=math.ceil((Defense.personal_target_bullets-bullets)/10)
  if funded>=4 then
    return {kind="craft",recipe="firearm-magazine",
      count=math.min(4,wanted,math.floor(funded/4),math.floor(carried(npc,"iron-plate")/4))}
  end
  local furnace,source,output,fuel=personal_furnace(npc)
  if not furnace then abandon_personal_batch(s);return nil end
  if s.furnace_unit and s.furnace_unit~=furnace.unit_number then abandon_personal_batch(s) end
  if s.pending_ore>0 and (s.pending_ore<4 or
      game.tick-(s.wait_started_tick or game.tick)>1800) then
    abandon_personal_batch(s)
    s.retry_after=game.tick+3600
  end
  local available=output.get_item_count{name="iron-plate",quality="normal"}
  local creditable=available
  if s.pending_ore>0 then
    creditable=math.min(available,s.pending_ore,
      math.max(0,furnace.products_finished-(s.products_at_deposit or furnace.products_finished)
        -(s.claimed_plates or 0)))
  end
  local fit=npc.character.get_main_inventory().get_insertable_count{name="iron-plate",quality="normal"}
  local count=math.min(16,wanted*4,math.floor(creditable/4)*4,math.floor(fit/4)*4)
  if count>=4 and (s.pending_ore>0 or not s.batch_started_tick) then
    return {kind="transfer",target=furnace,item="iron-plate",mode="withdraw",count=count}
  end
  if s.pending_ore>0 then return false,"smelting_personal_ammo" end
  if s.retry_after and game.tick<s.retry_after then return nil end
  if not s.batch_started_tick then
    if available>=4 then return nil end -- inventory fit, not material, is blocking
    s.batch_started_tick=game.tick
  end
  if game.tick-s.batch_started_tick>6000 then
    abandon_personal_batch(s);s.retry_after=game.tick+3600
    if count>=4 then return {kind="transfer",target=furnace,item="iron-plate",mode="withdraw",count=count} end
    return nil
  end
  if not s.fresh_fuel then
    if fuel.get_insertable_count{name="coal",quality="normal"}<1 then
      if fuel.get_item_count{name="coal",quality="normal"}<1 then return nil end
      s.fresh_fuel=true -- A full owned fuel slot already funds smelting.
    elseif s.coal_credit<1 then return mine_personal("coal",1)
    else return {kind="transfer",target=furnace,item="coal",mode="deposit",count=1} end
  end
  if s.ore_credit<8 then return mine_personal("iron-ore",8-s.ore_credit) end
  if source.get_insertable_count{name="iron-ore",quality="normal"}<8 then
    return false,"waiting-for-owned-iron-furnace-input-space"
  end
  return {kind="transfer",target=furnace,item="iron-ore",mode="deposit",count=8,
    inventory=defines.inventory.crafter_input}
end

function Defense.personal_receipt(npc,receipt)
  if not (npc and receipt) then return end
  local s=personal_supply(npc)
  npc.personal_ammo_receipts=npc.personal_ammo_receipts or {}
  for _,prior in ipairs(npc.personal_ammo_receipts) do
    if prior.job_id==receipt.job_id then return end
  end
  npc.personal_ammo_receipts[#npc.personal_ammo_receipts+1]={job_id=receipt.job_id,kind=receipt.kind,item=receipt.item,
    recipe=receipt.recipe,mode=receipt.mode,status=receipt.status,count=receipt.count,
    consumed=copy(receipt.consumed),produced=copy(receipt.produced),
    target_unit_number=receipt.target_unit_number,reason=receipt.reason,
    resource_source=receipt.resource_source,tick=receipt.tick}
  while #npc.personal_ammo_receipts>32 do table.remove(npc.personal_ammo_receipts,1) end
  local actual=math.max(0,receipt.count or 0)
  if receipt.kind=="mine" and receipt.item=="iron-ore" then
    s.ore_credit=math.min(16,(s.ore_credit or 0)+actual)
  elseif receipt.kind=="mine" and receipt.item=="coal" then
    s.coal_credit=math.min(4,(s.coal_credit or 0)+actual)
  elseif receipt.kind=="transfer" and receipt.mode=="deposit" then
    local furnace=personal_furnace(npc)
    if receipt.item=="coal" then
      s.coal_credit=math.max(0,(s.coal_credit or 0)-actual)
      if actual>0 and furnace and receipt.target_unit_number==furnace.unit_number then
        s.fresh_fuel=true
      end
    elseif receipt.item=="iron-ore" then
      s.ore_credit=math.max(0,(s.ore_credit or 0)-actual)
      if actual>0 and furnace and receipt.target_unit_number==furnace.unit_number then
        s.pending_ore=actual;s.claimed_plates=0
        s.products_at_deposit=furnace.products_finished
        s.wait_started_tick=game.tick;s.furnace_unit=furnace.unit_number
        s.batch_started_tick=nil;s.fresh_fuel=nil
      end
    end
  elseif receipt.kind=="transfer" and receipt.item=="iron-plate" and receipt.mode=="withdraw" then
    local furnace=personal_furnace(npc)
    if actual>0 and furnace and receipt.target_unit_number==furnace.unit_number then
      npc.personal_ammo_funded_iron=math.min(16,math.max(0,npc.personal_ammo_funded_iron or 0)+actual)
      if s.pending_ore>0 then
        s.pending_ore=math.max(0,s.pending_ore-actual)
        s.claimed_plates=(s.claimed_plates or 0)+actual
        if s.pending_ore==0 then abandon_personal_batch(s) end
      end
    end
  elseif receipt.kind=="craft" and receipt.recipe=="firearm-magazine" then
    local spent=(receipt.consumed and receipt.consumed["iron-plate"]) or 0
    npc.personal_ammo_funded_iron=math.max(0,(npc.personal_ammo_funded_iron or 0)-spent)
    if receipt.status~="complete" then npc.personal_ammo_funded_iron=0 end
  end
end

return Defense
