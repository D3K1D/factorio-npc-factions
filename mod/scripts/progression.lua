-- Read-only Nauvis progression. Runtime technology prerequisites are authority;
-- installed Space Age changes the rocket path from the base-game path.
local Progression = {revision="orbital-dag-v2",goal="first_orbital_platform"}
Progression.stages = {
  {id="starter_power_science",technology="electric-mining-drill",implemented=true},
  {id="green_science",technology="automation-2",implemented=true},
  {id="electric_mining",capabilities={"legal_ore_site","electric_power"},implemented=true},
  {id="steel_processing",technology="steel-processing",implemented=true},
  {id="oil_processing",technology="oil-processing",capabilities={"oil_site_survey","refinery_fluid_layout"},implemented=true},
  {id="chemical_science",technology="chemical-science-pack",capabilities={"sulfur_plastic_layout","engine_assembler","blue_science_layout"},implemented=true},
  {id="rocket_materials",technology="rocket-silo",capabilities={
    "processing_unit_assembly","electric_engine_assembly","concrete_assembly",
    "rocket_fuel_assembly","low_density_structure_assembly","lubricant_chemistry",
    "advanced_oil_processing","heavy_light_cracking","light_oil_solid_fuel",
    "sulfuric_acid_chemistry","sustained_owned_smelting_capacity"}},
  {id="platform_launch",capabilities={"native_silo_build","native_rocket_parts","economic_starter_pack","native_platform_payload_launch"}}
}
Progression.optional_branches={"military-science-pack","production-science-pack","utility-science-pack"}

local function keys(t) local r={} for k in pairs(t or {}) do r[#r+1]=k end table.sort(r) return r end
local function copy_science(technology)
  local out={}
  for _,item in pairs(technology.research_unit_ingredients or {}) do
    out[#out+1]={name=item.name,amount=item.amount}
  end
  table.sort(out,function(a,b)return a.name<b.name end)
  return out
end

function Progression.technology_dag(force,target)
  local visited,visiting,nodes,order={},{},{},{}
  local function visit(name)
    if visited[name] then return true end
    if visiting[name] then return false,"technology_cycle:"..name end
    local technology=force.technologies[name]
    if not technology then return false,"missing_technology:"..name end
    visiting[name]=true
    local prereqs={}
    for _,value in pairs(technology.prerequisites or {}) do prereqs[#prereqs+1]=value.name end
    table.sort(prereqs)
    for _,parent in ipairs(prereqs) do local ok,reason=visit(parent); if not ok then return false,reason end end
    nodes[name]={name=name,prerequisites=prereqs,researched=technology.researched,
      enabled=technology.enabled,science=copy_science(technology),
      research_unit_count=technology.research_unit_count}
    visiting[name]=nil;visited[name]=true;order[#order+1]=name
    return true
  end
  local ok,reason=visit(target or "rocket-silo")
  if not ok then return nil,reason end
  return {target=target or "rocket-silo",nodes=nodes,order=order,revision=Progression.revision}
end

-- The science tiers, read from the game: every science pack the labs take that can be made on this
-- surface and that the way to space or the weapons research takes, with the research still to do before
-- it can be made and what one pack is made of, down to what is mined (modded packs and recipes count the
-- same way). companion/tier_path.py reads from them what is due and when: the next pack's research, the
-- supplies its chain needs from other machines (steel for military science), its pack line and the labs.
-- Recipes and the armory's military techs are passed in (`opts.recipes`, `opts.military_techs`), so this
-- module needs nothing loaded beside it.
local function try(f) local ok,value=pcall(f); if ok then return value end end

local CHAIN_DEPTH=8       -- recipe levels walked under a pack
local CHAIN_ROWS=40       -- items listed per pack at most
local CHAIN_VISITS=400    -- (a loop of recipes is cut off here)
local RESEARCH_ROWS=40    -- technologies listed per pack at most
local TREES_SEEN=200      -- trees looked at for what kinds grow on a surface
local HELD_RADIUS=64      -- tiles round the engineer its chests are counted as at hand
local HELD_CHESTS=64      -- (chests counted at most)

-- Prototype data: the same on every peer and fixed while the game runs (a cache, not game state).
local proto={}

-- recipe -> the technologies that unlock it, cheapest first
local function unlocked_by()
  if proto.unlocks then return proto.unlocks end
  local out,costs={},{}
  for name,tech in pairs(prototypes.technology) do
    costs[name]=try(function() return tech.research_unit_count end) or 0
    for _,effect in ipairs(tech.effects or {}) do
      if effect.type=="unlock-recipe" then
        out[effect.recipe]=out[effect.recipe] or {}
        table.insert(out[effect.recipe],name)
      end
    end
  end
  for _,list in pairs(out) do
    table.sort(list,function(a,b) if costs[a]~=costs[b] then return costs[a]<costs[b] end return a<b end)
  end
  proto.unlocks=out
  return out
end

-- The technology this force can research to unlock `recipe` (the cheapest one enabled for it), or nil.
local function unlock_tech(force,recipe)
  for _,name in ipairs(unlocked_by()[recipe] or {}) do
    local t=force.technologies[name]
    if t and t.enabled and not t.researched then return name end
  end
end

local PRESENT_EVERY=216000   -- ticks between looks at what fluids and trees a surface has (an hour)

-- The fluids its tiles hold, the trees that grow and the resources the map does not name that lie on `surface`,
-- looked for on the map itself.
local function present_here(surface,settings)
  storage.npc_tier_present=storage.npc_tier_present or {}
  local key=surface.index
  local was=storage.npc_tier_present[key]
  if was and game.tick-was.tick<PRESENT_EVERY then return was end
  local by_fluid={}
  local tiles=settings.tile or {}
  local function consider(name,tile)
    local fluid=tile and try(function() return tile.fluid end)
    if fluid then by_fluid[fluid.name]=by_fluid[fluid.name] or {}; table.insert(by_fluid[fluid.name],name) end
  end
  for name,setting in pairs(tiles.settings or {}) do
    if not (type(setting)=="table" and setting.size==0) then consider(name,prototypes.tile[name]) end
  end
  if tiles.treat_missing_as_default then
    for name,tile in pairs(try(function() return prototypes.tile end) or {}) do
      if not (tiles.settings or {})[name] and try(function() return tile.autoplace_specification end) then consider(name,tile) end
    end
  end
  local out={tick=game.tick,fluids={},trees={},resources={}}
  -- (resources the map does not name, taken by default: only those found on it; Space Age's Nauvis "defaults" every
  -- planet's geysers and ice)
  local entities=settings.entity or {}
  if entities.treat_missing_as_default then
    local names={}
    for name,p in pairs(try(function() return prototypes.get_entity_filtered{{filter="type",type="resource"}} end) or {}) do
      if not (entities.settings or {})[name] and try(function() return p.autoplace_specification end) then
        names[#names+1]=name
      end
    end
    table.sort(names)
    for _,name in ipairs(names) do
      if (try(function() return surface.count_entities_filtered{name=name,limit=1} end) or 0)>0 then out.resources[name]=true end
    end
  end
  for fluid,names in pairs(by_fluid) do
    table.sort(names)
    if (try(function() return surface.count_tiles_filtered{name=names,limit=1} end) or 0)>0 then out.fluids[fluid]=true end
  end
  for _,tree in ipairs(try(function() return surface.find_entities_filtered{type="tree",limit=TREES_SEEN} end) or {}) do
    out.trees[tree.name]=true
  end
  storage.npc_tier_present[key]=out
  return out
end

-- What mining, cutting and pumping give on this surface: the bottom of every chain. Only what its map
-- places counts (Space Age's other planets have wrecks that give gears and geysers of acid).
local function raw_items(surface)
  local out={}
  local function mined(p)
    local m=p and try(function() return p.mineable_properties end)
    for _,product in ipairs(m and m.products or {}) do out[product.name]=true end
  end
  local settings=surface and try(function() return surface.map_gen_settings.autoplace_settings end) or {}
  local entities=settings.entity or {}
  for name,setting in pairs(entities.settings or {}) do
    if not (type(setting)=="table" and setting.size==0) then mined(prototypes.entity[name]) end   -- (size 0: none)
  end
  -- Fluids pumped from tiles, and what trees give: only those actually on this surface (a map's tile list names mods'
  -- and other planets' tiles it never places: sulfuric acid and heavy oil "on Nauvis"). Looked over once an hour per
  -- surface, kept in storage (the same on every peer, and on a joiner's machine).
  local seen=surface and present_here(surface,settings) or {fluids={},trees={}}
  for fluid in pairs(seen.fluids) do out[fluid]=true end
  for name in pairs(seen.trees) do mined(prototypes.entity[name]) end
  for name in pairs(seen.resources) do mined(prototypes.entity[name]) end
  return out
end

-- crafting category -> the machines that craft it (by name)
local function machines()
  if proto.machines then return proto.machines end
  local out={}
  for name,p in pairs(try(function() return prototypes.get_entity_filtered{{filter="crafting-machine"}} end) or {}) do
    for category in pairs(try(function() return p.crafting_categories end) or {}) do
      out[category]=out[category] or {}
      table.insert(out[category],name)
    end
  end
  for _,list in pairs(out) do table.sort(list) end
  proto.machines=out
  return out
end

local function fluid_amount(product)
  local n=product.amount or ((product.amount_min or 0)+(product.amount_max or product.amount_min or 0))/2
  return n*(product.independent_probability or product.probability or 1)
end

local function fluid_yield(recipe,fluid)
  local made=0
  for _,p in ipairs(recipe.products or {}) do if p.name==fluid and p.type=="fluid" then made=made+fluid_amount(p) end end
  for _,i in ipairs(recipe.ingredients or {}) do if i.name==fluid and i.type=="fluid" then made=made-(i.amount or 0) end end
  return made
end

local function makes_here(recipe,surface)
  local conditions=try(function() return recipe.prototype.surface_conditions end)
  if not (conditions and surface) then return true end
  for _,c in ipairs(conditions) do
    local value=try(function() return surface.get_property(c.property) end)
    if value and ((c.min and value<c.min) or (c.max and value>c.max)) then return false end
  end
  return true
end

-- The recipes that make a fluid, best first: the one named after it, then the fewest ingredients.
local function fluid_candidates(fluid,R)
  proto.fluids=proto.fluids or {}
  local names=proto.fluids[fluid]
  if names then return names end
  names={}
  local found=try(function()
    return prototypes.get_recipe_filtered{{filter="has-product-fluid",elem_filters={{filter="name",name=fluid}}}}
  end) or {}
  local list={}
  for _,r in pairs(found) do
    if not r.hidden and not R.made_in(r,{recycling=true}) and fluid_yield(r,fluid)>0 then list[#list+1]=r end
  end
  table.sort(list,function(a,b)
    if (a.name==fluid)~=(b.name==fluid) then return a.name==fluid end
    if #(a.ingredients or {})~=#(b.ingredients or {}) then return #(a.ingredients or {})<#(b.ingredients or {}) end
    return a.name<b.name
  end)
  for i,r in ipairs(list) do names[i]=r.name end
  proto.fluids[fluid]=names
  return names
end

local function candidates(force,name,kind,R)
  return kind=="fluid" and fluid_candidates(name,R) or R.candidates(force,name)
end

-- A recipe that takes something made from what it makes (emptying a barrel filled with it): no way to make it.
local function loops(force,recipe,name,R)
  for _,i in ipairs(recipe.ingredients or {}) do
    local first=candidates(force,i.name,i.type=="fluid" and "fluid" or "item",R)[1]
    local r=first and force.recipes[first]
    for _,j in ipairs(r and r.ingredients or {}) do if j.name==name then return true end end
  end
  return false
end

-- The force's best recipe for an item or fluid that can be made on this surface: an unlocked one first.
-- Whether `name` can be had here at all: mined on this surface (`raw`), or made by some recipe this surface allows.
local function obtainable(force,name,kind,R,surface,raw)
  if raw[name] then return true end
  for _,candidate in ipairs(candidates(force,name,kind,R)) do
    local r=force.recipes[candidate]
    if r and makes_here(r,surface) then return true end
  end
  return false
end

-- The force's best recipe for an item or fluid that can be made on this surface: an unlocked one first, and (given
-- `raw`, what this surface's map places) one whose ingredients can all be had here before one that needs something
-- from elsewhere (a mod's solid fuel from another planet's ice, while Nauvis makes it from light oil).
local function recipe_for(force,name,kind,R,surface,raw)
  local ranked={}
  for _,candidate in ipairs(candidates(force,name,kind,R)) do
    local r=force.recipes[candidate]
    if r and makes_here(r,surface) and not loops(force,r,name,R) then
      local here=true
      if raw then
        for _,i in ipairs(r.ingredients or {}) do
          if not obtainable(force,i.name,i.type=="fluid" and "fluid" or "item",R,surface,raw) then here=false; break end
        end
      end
      ranked[#ranked+1]={r=r,score=(here and 0 or 2)+(r.enabled and 0 or 1)}
    end
  end
  local best
  for _,c in ipairs(ranked) do if not best or c.score<best.score then best=c end end
  return best and best.r
end

-- The machines crafting `category` this force can make now, and else the cheapest technology to research for
-- one: "ready", or "research" and the technology, or "none" when no machine can be had.
local function machine_state(force,category,R,surface)
  local ready,best,best_units={},nil,nil
  for _,name in ipairs(machines()[category] or {}) do
    local entity=prototypes.entity[name]
    for _,item in ipairs(entity and try(function() return entity.items_to_place_this end) or {}) do
      local r=recipe_for(force,item.name or item[1],"item",R,surface)
      if r and r.enabled then ready[#ready+1]=name; break end
      local tech=r and unlock_tech(force,r.name)
      local units=tech and (try(function() return force.technologies[tech].research_unit_count end) or 0)
      if tech and (not best or units<best_units or (units==best_units and tech<best)) then best,best_units=tech,units end
    end
  end
  if #ready>0 then return "ready",nil,ready end
  if best then return "research",best,ready end
  return "none",nil,ready
end

-- What one pack takes, every item and fluid under it: per pack, its recipe, whether that is unlocked and
-- what unlocks it, the crafting category; mined things are `raw`.
local function chain_of(force,pack,recipe,R,raw,surface)
  local rows,index,recipes,visits={},{},{},0
  local function walk(name,kind,per,depth,parent)
    visits=visits+1
    if visits>CHAIN_VISITS then return end
    local row=index[name]
    if not row then
      if #rows>=CHAIN_ROWS then return end
      row={name=name,type=kind,per=0,by={}}
      index[name],rows[#rows+1]=row,row
      local r=not raw[name] and recipe_for(force,name,kind,R,surface,raw)
      if r then
        recipes[name]=r
        row.recipe,row.enabled,row.category=r.name,r.enabled or nil,R.category(r)
        if not r.enabled then
          row.tech=unlock_tech(force,r.name)
          if not row.tech then row.unavailable=true end   -- (no research left that unlocks it)
        end
        -- and a machine to make it in (a furnace for steel): its research, or none to be had
        local state,tech=machine_state(force,row.category,R,surface)
        if state=="research" then row.machine_tech=tech elseif state=="none" then row.unavailable=true end
      elseif raw[name] then
        row.raw=true      -- mined here
      else
        row.unavailable=true   -- nothing here makes it and this surface's map does not place it
      end
    end
    row.per=row.per+per
    -- what it is made into (the pack itself at the top): a supply matters only while something it goes into is short
    local into=parent or pack
    local listed=false
    for _,b in ipairs(row.by) do if b==into then listed=true end end
    if not listed then row.by[#row.by+1]=into end
    local r=recipes[name]
    if not r or depth>=CHAIN_DEPTH then return end
    local made=kind=="fluid" and fluid_yield(r,name) or R.yield(r,name)
    if made<=0 then return end
    for _,i in ipairs(r.ingredients or {}) do
      walk(i.name,i.type=="fluid" and "fluid" or "item",per*(i.amount or 0)/made,depth+1,name)
    end
  end
  local made=R.yield(recipe,pack)
  if made>0 then
    for _,i in ipairs(recipe.ingredients or {}) do
      walk(i.name,i.type=="fluid" and "fluid" or "item",(i.amount or 0)/made,1,pack)
    end
  end
  for _,row in ipairs(rows) do row.per=math.floor(row.per*10000+0.5)/10000 end
  return rows
end

local function unit_time(t)
  return (try(function() return t.research_unit_energy end) or 1800)/60   -- (seconds a lab takes per unit)
end

-- The technologies still to research before `target` is researched, prerequisites first.
local function research_left(force,target)
  local rows,seen={},{}
  local function visit(name,depth)
    if seen[name] or #rows>=RESEARCH_ROWS or depth>80 then return end
    seen[name]=true
    local t=force.technologies[name]
    if not t or t.researched then return end
    local prereqs={}
    for parent in pairs(t.prerequisites or {}) do prereqs[#prereqs+1]=parent end
    table.sort(prereqs)
    for _,parent in ipairs(prereqs) do visit(parent,depth+1) end
    local packs={}
    for _,i in pairs(t.research_unit_ingredients or {}) do packs[#packs+1]=i.name end
    table.sort(packs)
    local trigger=try(function() return t.prototype.research_trigger end)~=nil
    rows[#rows+1]={name=name,units=trigger and 0 or (try(function() return t.research_unit_count end) or 0),
      time=unit_time(t),packs=packs,trigger=trigger or nil,enabled=t.enabled or nil}
  end
  visit(target,0)
  return rows
end

-- Research units times packs over a technology and everything before it (researched too): how deep in the
-- tree a pack sits, the order of the tiers.
local function depth_cost(force,target)
  local dag=target and Progression.technology_dag(force,target)
  local total=0
  for _,node in pairs(dag and dag.nodes or {}) do
    total=total+(tonumber(node.research_unit_count) or 0)*math.max(1,#(node.science or {}))
  end
  return total
end

function Progression.tiers(force,surface,opts)
  opts=opts or {}
  local R=opts.recipes
  if not (R and force) then return nil end
  -- the packs the way to space researches with, and those its weapons research takes
  local rocket,rocket_units,rocket_time={},0,0
  for _,target in ipairs({"rocket-silo","space-platform"}) do
    local dag=force.technologies[target] and Progression.technology_dag(force,target)
    for name,node in pairs(dag and dag.nodes or {}) do
      for _,s in ipairs(node.science or {}) do rocket[s.name]=true end
      if not node.researched and target=="rocket-silo" then
        local units=tonumber(node.research_unit_count) or 0
        rocket_units=rocket_units+units
        rocket_time=rocket_time+units*unit_time(force.technologies[name])
      end
    end
  end
  -- a weapons tech still to research that needs one pack beside the rocket's: that pack is the military tier
  -- (grey science; damage upgrades that also need a later tier do not make that tier military)
  local military,serves={},{}
  local sets={}   -- the pack sets of the research ahead (each unit takes every pack of its set), with their units
  local function add_set(t)
    local packs={}
    for _,i in pairs(t.research_unit_ingredients or {}) do packs[#packs+1]=i.name end
    table.sort(packs)
    if #packs==0 then return end
    local key=table.concat(packs,"|")
    sets[key]=sets[key] or {packs=packs,units=0}
    sets[key].units=sets[key].units+(try(function() return t.research_unit_count end) or 0)
  end
  for _,t in ipairs(opts.military_techs or {}) do
    local tech=force.technologies[t.name]
    local extra,count=nil,0
    for _,p in ipairs(t.packs or {}) do if not rocket[p] then extra,count=p,count+1 end end
    if count==1 and tech and not tech.researched then
      military[extra]=true
      serves[extra]=(serves[extra] or 0)+(try(function() return tech.research_unit_count end) or 0)
      add_set(tech)
    end
  end
  do
    local dag=force.technologies["rocket-silo"] and Progression.technology_dag(force,"rocket-silo")
    for name,node in pairs(dag and dag.nodes or {}) do
      local t=force.technologies[name]
      if not node.researched and t and not try(function() return t.prototype.research_trigger end) then add_set(t) end
    end
  end
  local raw=raw_items(surface)
  local packs,speeds={},{}
  for name,lab in pairs(try(function() return prototypes.get_entity_filtered{{filter="type",type="lab"}} end) or {}) do
    speeds[name]=try(function() return lab.get_researching_speed() end) or try(function() return lab.researching_speed end) or 1
    for _,p in ipairs(try(function() return lab.lab_inputs end) or {}) do packs[p]=true end
  end
  local tiers,categories={},{}
  local function tier_of(pack,recipe)
    local unlock=not recipe.enabled and unlock_tech(force,recipe.name) or nil
    local chain=chain_of(force,pack,recipe,R,raw,surface)
    local tier={pack=pack,recipe=recipe.name,enabled=recipe.enabled or nil,unlock=unlock,
      category=R.category(recipe),rocket=rocket[pack] or nil,military=military[pack] or nil,serves=serves[pack],
      cost=depth_cost(force,unlock or (unlocked_by()[recipe.name] or {})[1]),chain=chain}
    if not recipe.enabled and not unlock then tier.unavailable=true end   -- (no research left unlocks it)
    -- the research before it can be made: a machine to make it in, its own recipe's, and every recipe of its
    -- chain still locked
    local state,machine=machine_state(force,tier.category,R,surface)
    tier.machine_ready=state=="ready"
    if state=="none" then tier.unavailable=true end
    local research,seen,techs={},{},{}
    techs[#techs+1]=machine
    techs[#techs+1]=unlock
    for _,row in ipairs(chain) do
      techs[#techs+1]=row.machine_tech
      techs[#techs+1]=row.tech
    end
    for _,tech in ipairs(techs) do
      for _,row in ipairs(research_left(force,tech)) do
        if not seen[row.name] and #research<RESEARCH_ROWS then seen[row.name]=true; research[#research+1]=row end
      end
    end
    tier.research=research
    categories[tier.category]=true
    for _,row in ipairs(chain) do if row.category then categories[row.category]=true end end
    return tier
  end
  for _,pack in ipairs(keys(packs)) do
    -- (only the packs the way to space or the weapons research takes: the rest is no tier anyone is after)
    local recipe=(rocket[pack] or military[pack]) and recipe_for(force,pack,"item",R,surface)
    if recipe then tiers[#tiers+1]=tier_of(pack,recipe) end
  end
  -- The launch, the last milestone: what the silo's research unlocks that only a rocket silo makes (the rocket's part).
  local silo_tech=try(function() return prototypes.technology["rocket-silo"] end)
  for _,effect in ipairs(silo_tech and silo_tech.effects or {}) do
    local r=effect.type=="unlock-recipe" and force.recipes[effect.recipe]
    local in_silo=false
    for _,name in ipairs(r and machines()[R.category(r)] or {}) do
      local e=try(function() return prototypes.entity[name] end)
      if e and e.type=="rocket-silo" then in_silo=true end
    end
    local product=in_silo and (r.products or {})[1]
    if product and product.type~="fluid" and makes_here(r,surface) then
      local tier=tier_of(product.name,r)
      tier.launch,tier.rocket=true,true
      tiers[#tiers+1]=tier
    end
  end
  table.sort(tiers,function(a,b) if a.cost~=b.cost then return a.cost<b.cost end return a.pack<b.pack end)
  local made_in={}
  for category in pairs(categories) do made_in[category]=select(3,machine_state(force,category,R,surface)) end
  local share=1
  if opts.team then
    share=0
    for _,npc in pairs(storage.npcs or {}) do if npc.character and npc.character.valid then share=share+1 end end
    share=math.max(1,share)
  end
  -- What of the tiers' chains it has at hand: in its pockets (`opts.holder`, an inventory) and its force's chests
  -- within HELD_RADIUS of each of `opts.at` (a supply held is no supply waited on: the silo's parts in a chest by home).
  local held,wanted={},{}
  for _,t in ipairs(tiers) do for _,row in ipairs(t.chain) do if row.type~="fluid" then wanted[row.name]=true end end end
  local function count(inv)
    for name in pairs(wanted) do
      local n=try(function() return inv.get_item_count(name) end) or 0
      if n>0 then held[name]=(held[name] or 0)+n end
    end
  end
  if opts.holder then count(opts.holder) end
  local counted={}
  for _,at in ipairs(opts.at or {}) do   -- (its home, where its stores are, and where it stands)
    local chests=surface and try(function() return surface.find_entities_filtered{type="container",force=force,
      position=at,radius=HELD_RADIUS,limit=HELD_CHESTS} end) or {}
    for _,c in ipairs(chests) do
      local id=c.unit_number
      local inv=not counted[id] and try(function() return c.get_inventory(defines.inventory.chest) end)
      if inv then counted[id]=true; count(inv) end
    end
  end
  return {tiers=tiers,made_in=made_in,labs={speeds=speeds,bonus=try(function() return force.laboratory_speed_modifier end) or 0},
    rocket={units=rocket_units,time=rocket_units>0 and rocket_time/rocket_units or nil},share=share,held=held,
    sets=(function() local out={} for _,k in ipairs(keys(sets)) do out[#out+1]=sets[k] end return out end)()}
end

-- Technologies that help survive the biters: the armory's (weapons, turrets, walls, armor, ammo, capsules,
-- repair turrets, vehicles and their bonuses, `armory_techs`), and those unlocking what mends (repair tools) or
-- warns (radars). Read from the prototypes, so modded ones count the same way. companion/research_brain.py
-- weighs them by the threat against the next science tier's research.
function Progression.survival_techs(armory_techs)
  if proto.survival then return proto.survival end
  local out={}
  for _,t in ipairs(armory_techs or {}) do out[t.name]=true end
  for name,tech in pairs(prototypes.technology) do
    for _,effect in ipairs(tech.effects or {}) do
      local r=effect.type=="unlock-recipe" and try(function() return prototypes.recipe[effect.recipe] end)
      for _,p in ipairs(r and r.products or {}) do
        local item=p.type~="fluid" and try(function() return prototypes.item[p.name] end)
        local placed=item and try(function() return item.place_result end)
        if item and (item.type=="repair-tool" or (placed and placed.type=="radar")) then out[name]=true end
      end
    end
  end
  proto.survival=out
  return out
end

function Progression.status(npc,capabilities)
  if not (npc and npc.character and npc.character.valid) then return {blocked="actor_unavailable"} end
  local surface=npc.character.surface
  if surface.planet and surface.planet.name~="nauvis" then return {blocked="goal_is_nauvis_only"} end
  if not script.active_mods["space-age"] then return {blocked="space_age_disabled"} end
  local result={goal=Progression.goal,revision=Progression.revision,stages={},optional_branches=Progression.optional_branches}
  for _,stage in ipairs(Progression.stages) do
    local row={id=stage.id,implemented=stage.implemented or false,blockers={}}
    local tech=stage.technology and npc.force.technologies[stage.technology]
    row.researched=tech and tech.researched or false
    for _,capability in ipairs(stage.capabilities or {}) do
      if not (capabilities and capabilities[capability]) then row.blockers[#row.blockers+1]=capability end
    end
    result.stages[#result.stages+1]=row
  end
  result.technology_dag=Progression.technology_dag(npc.force,"rocket-silo")
  return result
end

-- The root event adapter must supply independently observed economic launch
-- evidence. A queued/empty platform shell or an ordinary empty rocket fails.
function Progression.qualifies_first_platform(evidence)
  if type(evidence)~="table" then return false,"missing_evidence" end
  for _,field in ipairs({"starter_pack_consumed","native_cargo_finished","platform_hub_valid","native_parts_completed","same_force","orbit_nauvis"}) do
    if evidence[field]~=true then return false,"unverified_"..field end
  end
  if evidence.starter_pack~="space-platform-starter-pack" then return false,"wrong_payload" end
  if type(evidence.platform_index)~="number" or type(evidence.completed_tick)~="number" then return false,"missing_native_identity" end
  return true
end
return Progression
