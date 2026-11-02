-- What the game's machines do, for what an engineer's force can build (mods included): the rates
-- library's data (companion/rates.py), from which the designer's brief and System One's options get
-- their numbers. Prototype values at normal quality; the force's research bonuses alongside. Read-only.
local Recipes=require("scripts.recipes")

local GameFacts={}

local function try(f) local ok,v=pcall(f); if ok then return v end end
local function keys(set) local out={}; for k in pairs(set or {}) do out[#out+1]=k end; table.sort(out); return out end
local function r3(v) return type(v)=="number" and math.floor(v*1000+0.5)/1000 or nil end

-- The kinds a planner needs, by prototype type.
local KINDS={["assembling-machine"]="crafter",furnace="crafter",["rocket-silo"]="crafter",["mining-drill"]="drill",
  lab="lab",["transport-belt"]="belt",["underground-belt"]="underground",splitter="splitter",loader="loader",
  ["loader-1x1"]="loader",inserter="inserter",boiler="boiler",generator="generator",["burner-generator"]="generator",
  ["solar-panel"]="solar",accumulator="accumulator",["offshore-pump"]="pump",beacon="beacon",
  -- (the rest a blueprint may hold: their size, and that this mod set has them; companion/blueprint_packs.py)
  ["electric-pole"]="pole",pipe="pipe",["pipe-to-ground"]="pipe",pump="fluid-pump",["storage-tank"]="tank",
  container="chest",["logistic-container"]="chest",wall="wall",gate="wall",["ammo-turret"]="turret",
  ["electric-turret"]="turret",["fluid-turret"]="turret",radar="radar",lamp="lamp",roboport="roboport",
  ["constant-combinator"]="circuit",["arithmetic-combinator"]="circuit",["decider-combinator"]="circuit",
  ["power-switch"]="circuit",reactor="reactor",["heat-pipe"]="heat",["heat-interface"]="heat"}
local MAX_ITEMS=48

-- What powers it and how much it draws at full work (kW).
local function energy(p,out)
  out.kw=r3(try(function() return p.get_max_energy_usage()*60/1000 end))
  local burner=try(function() return p.burner_prototype end)
  if burner then
    out.source,out.fuel,out.effectivity="burner",keys(burner.fuel_categories),r3(burner.effectivity)
  elseif try(function() return p.electric_energy_source_prototype end) then
    out.source="electric"
    out.drain_kw=r3(try(function() return p.electric_energy_source_prototype.drain*60/1000 end))
  end
end

local function modules(p,out)
  out.modules=try(function() return p.module_inventory_size end)
  out.productivity=r3(try(function() return p.effect_receiver.base_effect.productivity end))
end

-- The heat and fluid connections of the pieces a nuclear plant joins, as the game gives them facing north (x, y of
-- the edge tile from the centre, the way it reaches): companion/nuclear_cell.py stands each piece where they meet.
local function conns(list)
  local out={}
  for _,c in ipairs(list or {}) do
    local at=c.position or (c.positions and c.positions[1])
    if at then out[#out+1]={r3(at.x or at[1]),r3(at.y or at[2]),c.direction,c.flow_direction} end
  end
  return out
end
local function connections(p,out)
  local heat=try(function() return p.heat_buffer_prototype end) or try(function() return p.heat_energy_source_prototype end)
  if heat then out.heat=conns(heat.connections) end
  local boxes={}
  for _,b in ipairs(try(function() return p.fluidbox_prototypes end) or {}) do
    boxes[#boxes+1]={filter=b.filter and b.filter.name or nil,c=conns(b.pipe_connections)}
  end
  if #boxes>0 then out.fluid=boxes end
end

local function entity_facts(p,kind)
  local out={kind=kind,type=p.type,width=p.tile_width,height=p.tile_height}
  if kind=="crafter" then
    out.speed=r3(try(function() return p.get_crafting_speed() end))
    out.categories=keys(try(function() return p.crafting_categories end))
    energy(p,out); modules(p,out)
  elseif kind=="drill" then
    out.speed=r3(p.mining_speed)
    out.categories=keys(try(function() return p.resource_categories end))
    out.radius=r3(try(function() return p.get_mining_drill_radius() end))
    energy(p,out); modules(p,out)
  elseif kind=="lab" then
    out.speed=r3(try(function() return p.get_researching_speed() end))
    energy(p,out); modules(p,out)
  elseif kind=="belt" or kind=="underground" or kind=="splitter" or kind=="loader" then
    out.items_per_second=r3(try(function() return p.belt_speed*480 end))   -- both lanes
    if kind=="underground" then out.reach=try(function() return p.max_underground_distance end) end
  elseif kind=="inserter" then
    out.rotation=try(function() return p.get_inserter_rotation_speed() end)   -- turns a tick
    out.extension=try(function() return p.get_inserter_extension_speed() end)
    out.bulk=try(function() return p.bulk end) or nil
    out.stack_bonus=try(function() return p.inserter_stack_size_bonus end)
    out.force_bonus=try(function() return p.uses_inserter_stack_size_bonus end)
    local pick,drop=try(function() return p.inserter_pickup_position end),try(function() return p.inserter_drop_position end)
    if pick and drop then
      out.reach=r3(math.max(math.abs(pick.x or pick[1]),math.abs(pick.y or pick[2]),
        math.abs(drop.x or drop[1]),math.abs(drop.y or drop[2])))
    end
    energy(p,out)
  elseif kind=="boiler" then
    energy(p,out)
    if try(function() return p.heat_energy_source_prototype end) then out.source="heat" end   -- (a heat exchanger)
    out.target_temperature=try(function() return p.target_temperature end)
  elseif kind=="generator" then
    out.max_kw=r3(try(function() return p.get_max_power_output()*60/1000 end))
    out.fluid_per_second=r3(try(function() return p.get_fluid_usage_per_tick()*60 end))
  elseif kind=="solar" then
    out.max_kw=r3(try(function() return p.get_max_energy_production()*60/1000 end))
  elseif kind=="accumulator" then
    out.capacity_mj=r3(try(function() return p.electric_energy_source_prototype.buffer_capacity/1e6 end))
    out.output_kw=r3(try(function() return p.electric_energy_source_prototype.get_output_flow_limit()*60/1000 end))
  elseif kind=="reactor" then   -- (its heat at full burn, and what each working neighbour adds: companion/nuclear.py)
    energy(p,out)
    out.neighbour_bonus=r3(try(function() return p.neighbour_bonus end))
    out.max_temperature=try(function() return p.heat_buffer_prototype.max_temperature end)
  elseif kind=="pump" then
    out.per_second=r3(try(function() return p.get_pumping_speed()*60 end))
  elseif kind=="beacon" then
    out.effectivity=r3(try(function() return p.distribution_effectivity end))
    energy(p,out); modules(p,out)
  elseif kind=="roboport" then   -- (how far its robots reach: companion/robo_grid.py spaces the grid by it)
    out.logistic_radius=try(function() return p.logistic_radius end)
    out.construction_radius=try(function() return p.construction_radius end)
    energy(p,out)
  end
  if kind=="reactor" or kind=="heat" or (kind=="boiler" and out.source=="heat") or kind=="generator"
      or kind=="drill" then   -- (a drill's fluid in, for ore mined with acid: companion/uranium_plant.py)
    connections(p,out)
  end
  return out
end

local function effects(e)
  local out={}
  for _,k in ipairs({"speed","productivity","consumption","pollution","quality"}) do
    local v=e and e[k]
    if type(v)=="number" and v~=0 then out[k]=r3(v) end
  end
  return out
end

-- {"npc_id":N}: what that engineer's force can build, the fuels and modules of the game, its qualities
-- and the force's research bonuses.
function GameFacts.export(force)
  if not (force and force.valid) then return {ok=false,reason="unknown_force"} end
  local types={}
  for t in pairs(KINDS) do types[#types+1]=t end
  -- every building of these kinds a player could place (the planners plan ahead: the bus's assemblers
  -- before their research), marked `ready` when the force can make it now
  local entities={}
  for name,p in pairs(prototypes.get_entity_filtered{{filter="type",type=types}}) do
    local placed=try(function() return p.items_to_place_this end)
    if placed and #placed>0 and not try(function() return p.hidden end) then
      local facts=try(function() return entity_facts(p,KINDS[p.type]) end)
      if facts then
        facts.ready=Recipes.ready_entity(force,name) or nil
        entities[name]=facts
      end
    end
  end
  local fuels,n={},0
  for name,item in pairs(prototypes.get_item_filtered{{filter="fuel"}}) do
    if n<MAX_ITEMS and (item.fuel_value or 0)>0 then
      fuels[name]={mj=r3(item.fuel_value/1e6),categories=try(function() return item.fuel_categories end)}
      n=n+1
    end
  end
  local mods,m={},0
  for name,item in pairs(prototypes.get_item_filtered{{filter="type",type="module"}}) do
    if m<MAX_ITEMS then
      mods[name]={category=try(function() return item.category end),tier=try(function() return item.tier end),
        effects=effects(try(function() return item.module_effects end)),ready=Recipes.ready(force,name)}
      m=m+1
    end
  end
  local qualities={}
  for name,q in pairs(prototypes.quality) do
    if not try(function() return q.hidden end) then
      qualities[name]={level=q.level,next=q.next and q.next.name or nil,next_probability=r3(q.next_probability)}
    end
  end
  local fluids={}
  for _,name in ipairs({"water","steam"}) do
    local f=prototypes.fluid[name]
    if f then fluids[name]={heat_capacity_kj=r3(f.heat_capacity/1000),default_temperature=f.default_temperature,
      max_temperature=f.max_temperature} end
  end
  -- The resources that need a fluid mined in (uranium ore and sulfuric acid, mods' own): their mining time
  -- and how much fluid a mining cycle takes (companion/nuclear.py).
  local resources={}
  for name,p in pairs(prototypes.get_entity_filtered{{filter="type",type="resource"}}) do
    local mine=try(function() return p.mineable_properties end)
    if mine and mine.required_fluid then
      resources[name]={mining_time=r3(mine.mining_time),fluid=mine.required_fluid,fluid_amount=r3(mine.fluid_amount),
        category=try(function() return p.resource_category end)}   -- (the drills that mine it: their categories)
    end
  end
  -- The recipes this force has not researched yet (hidden ones and recycling left out; the researched ones are the
  -- site's own): a blueprint set to one is read from it and kept locked until it is (companion/blueprint_packs.py),
  -- not left waiting.
  local recipes={}
  for name,r in pairs(force.recipes) do
    local category=try(function() return r.category end)
    if not try(function() return r.enabled end) and not try(function() return r.hidden end)
        and category~="recycling" and category~="parameters" then
      local ing,prod={},{}
      for _,i in ipairs(try(function() return r.ingredients end) or {}) do ing[#ing+1]={name=i.name,amount=r3(i.amount)} end
      for _,p in ipairs(try(function() return r.products end) or {}) do
        local amount=p.amount or ((p.amount_min or 1)+(p.amount_max or p.amount_min or 1))/2
        local chance=p.independent_probability or p.probability or 1
        local shared=p.shared_probability
        if type(shared)=="table" and shared.min and shared.max then chance=chance*math.max(0,shared.max-shared.min) end
        prod[#prod+1]={name=p.name,amount=r3(amount),probability=chance~=1 and r3(chance) or nil}
      end
      recipes[name]={category=category,energy=r3(try(function() return r.energy end)),ingredients=ing,products=prod}
    end
  end
  local active={}
  for name,version in pairs(script.active_mods) do active[name]=version end
  return {ok=true,schema="npc-game-facts-v1",tick=game.tick,force=force.name,game_version=script.active_mods.base,
    mods=active,entities=entities,fuels=fuels,modules=mods,qualities=qualities,fluids=fluids,resources=resources,
    recipes=recipes,
    bonuses={inserter_stack=force.inserter_stack_size_bonus,bulk_inserter_capacity=force.bulk_inserter_capacity_bonus,
      mining_productivity=r3(force.mining_drill_productivity_bonus),lab_speed=r3(force.laboratory_speed_modifier),
      lab_productivity=r3(force.laboratory_productivity_bonus),belt_stack=force.belt_stack_size_bonus}}
end

return GameFacts
