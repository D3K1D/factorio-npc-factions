-- Designed start: instead of the scripted starter camp, an NPC asks the host
-- companion for a staged layout and builds it with ordinary paid Worker jobs.
-- A submitted plan is only a proposal: positions, items and recipes are
-- re-checked here and by the Worker against the live world at every step.
local Personality=require("scripts.personality")
local Chronicle=require("scripts.chronicle")
local Character=require("scripts.character")
local Scouting=require("scripts.scouting")
local Voice=require("scripts.voice")
local BaseMap=require("scripts.base_map")
local Teach=require("scripts.teach")
local Corrector=require("scripts.corrector")
local Orbit=require("scripts.orbit")
local Agent=require("scripts.agent")
local BaseMemory=require("scripts.base_memory")
local Progression=require("scripts.progression")
local Recipes=require("scripts.recipes")
local Armory=require("scripts.armory")
local GameFacts=require("scripts.game_facts")
local TeamWorld=require("scripts.team_world")
local TeamResearch=require("scripts.team_research")
local TeamSupply=require("scripts.team_supply")
local TeamWar=require("scripts.team_war")
local Priorities=require("scripts.priorities")
local Clearing=require("scripts.clearing")
local FactoryPlan=require("scripts.factory_plan")
local DesignedStart={}
DesignedStart.Obstacles=require("scripts.obstacles")   -- (what is in the way of a planned piece: the ladder)

local WINDOW=32              -- export half-size around home, in tiles
local REACH=128              -- native layouts and power lines may reach this far from home
local PLAN_REACH=512         -- a planned base (the main bus and its fields) may spread this far from home
local OUTPUT_BACKLOG=10      -- stop hand-feeding a machine whose products are piling up
local REQUEST_TIMEOUT=7200   -- ticks (2 game minutes) to wait for a plan before falling back
local MAX_ENTITIES=900          -- belt lines are long; a bus section with its modules is bigger still
local MAX_STAGES=16
local CARRY_LOW,CARRY_KEEP=10,30   -- magazines carried: craft more below the first, up to the second
local REBUILD_CALM=1800            -- ticks after a building is destroyed before it is rebuilt
local LOCKED_FOR=7200              -- a plan waiting this long on a locked recipe is given up
local REBUILD_CLEAR=24             -- and no biters this close to it
local MAX_OBSTACLES=256
local MAX_FAILURES=6         -- failed jobs for one entity before the plan is blocked
local SHORT_OF=200            -- carrying fewer plates than this, a full furnace is worth a trip
local BUILD_BATCH=50          -- buildings of one kind made or fetched at a time for a plan stage
local FUEL_LOW,FUEL_FILL=5,50 -- keep burners between these fuel item counts (a full stack: fewer trips)
local FIRST_FUEL=10           -- what a cold burner gets first, to start it now
local INPUT_LOW,INPUT_FILL=10,60   -- hand-fed machine inputs: fewer, bigger trips
local AMMO_LOW,AMMO_FILL=5,20  -- magazines per turret
local COLLECT_AT=50          -- empty outputs/chests before they fill and stop production
local CARRY_CAP=400          -- stop collecting an item once carrying this many (surplus is stored)
local FREE_SLOTS=8           -- keep room in the inventory for mining and crafting
local GOAL_DELAY=120         -- ticks between finishing a plan and asking for the next goal: asking costs the
                             -- engineer nothing (its upkeep goes on while the plan is made), and the half minute
                             -- this was, with upkeep first, came to 11 game minutes of the first 40 (run 35, 2026-10-25)
local MAX_HISTORY=64
local RESEARCH_WAIT=1800       -- ticks to wait for the host to pick research
local DIRECTIONS={[0]=true,[4]=true,[8]=true,[12]=true}
local PLACEABLE={furnace=true,["mining-drill"]=true,container=true,["assembling-machine"]=true,["rocket-silo"]=true,
  splitter=true,["underground-belt"]=true,loader=true,
  ["transport-belt"]=true,inserter=true,["electric-pole"]=true,boiler=true,generator=true,
  lab=true,pipe=true,["offshore-pump"]=true,["solar-panel"]=true,["pipe-to-ground"]=true,
  ["ammo-turret"]=true,wall=true,gate=true,radar=true,
  -- (modded defenses: laser, plasma and tesla turrets, flamethrowers, artillery, mines, repair turrets)
  ["electric-turret"]=true,["fluid-turret"]=true,["artillery-turret"]=true,["land-mine"]=true,roboport=true,
  ["logistic-container"]=true,   -- (a robot network's chests: L-3)
  reactor=true,["heat-pipe"]=true,["storage-tank"]=true}   -- (a nuclear plant: companion/nuclear_plant.py, DS-4)
local RAW={["iron-ore"]=true,["copper-ore"]=true,coal=true,stone=true,wood=true}
local SMELTED={["iron-plate"]=true,["copper-plate"]=true,["stone-brick"]=true}
local WITNESSES={entity_built=true,product_counter_delta=true,resource_extraction_delta=true,
  power_connected=true,research_progress_delta=true,belt_items=true,ammo_loaded=true,fluid_output=true,
  area_clear=true}
local MARKER="NPC_PLAN_RESULT "
local SHOWN_AXES={"curiosity","ambition","patience","frugality","cognition","adaptability","cooperation"}

local function try(f) local ok,value=pcall(f); if ok then return value end end
local function keys(set) local out={}; for k in pairs(set or {}) do out[#out+1]=k end; table.sort(out); return out end
local function count(npc,item) return npc.character.get_main_inventory().get_item_count({name=item,quality="normal"}) end
local function finite(v) return type(v)=="number" and v==v and math.abs(v)<1e6 end

local function rotate(v,direction)
  local x,y=v[1],v[2]
  if direction==4 then return {-y,x} elseif direction==8 then return {-x,-y}
  elseif direction==12 then return {y,-x} end
  return {x,y}
end

-- Entities whose box contains a point (find_entities_filtered{position=} only
-- matches entity centers).
local function entities_at(surface,point,filter)
  filter=filter or {}
  filter.area={{point.x-0.05,point.y-0.05},{point.x+0.05,point.y+0.05}}
  return surface.find_entities_filtered(filter)
end
DesignedStart.entities_at=entities_at

local function entity_box(name,position,direction)
  local p=prototypes.entity[name]
  local w,h=p.tile_width,p.tile_height
  if direction==4 or direction==12 then w,h=h,w end
  return {{position.x-w/2,position.y-h/2},{position.x+w/2,position.y+h/2}}
end

-- Facts the designer needs to place things that actually work, read from the
-- installed prototypes rather than a hand-written table.
local function prototype_facts(name)
  local p=prototypes.entity[name]
  if not p then return nil end
  local item=p.items_to_place_this and p.items_to_place_this[1]
  local facts={type=p.type,width=p.tile_width,height=p.tile_height,
    placement_item=item and (item.name or item[1]) or nil,energy_source="none"}
  if try(function() return p.burner_prototype end) then
    facts.energy_source="burner"
    facts.fuel_categories=keys(p.burner_prototype.fuel_categories)
  elseif try(function() return p.electric_energy_source_prototype end) then facts.energy_source="electric"
  elseif try(function() return p.void_energy_source_prototype end) then facts.energy_source="void" end
  local crafting=try(function() return p.crafting_categories end)
  if crafting then facts.crafting_categories=keys(crafting) end
  if p.type=="mining-drill" then
    facts.resource_categories=keys(p.resource_categories)
    facts.mining_area_radius=try(function() return p.get_mining_drill_radius() end) or p.mining_drill_radius
    local v=p.vector_to_place_result
    facts.drop_vector={v.x or v[1],v.y or v[2]}
  elseif p.type=="inserter" then
    local a,b=p.inserter_pickup_position,p.inserter_drop_position
    facts.pickup_vector={a.x or a[1],a.y or a[2]}; facts.drop_vector={b.x or b[1],b.y or b[2]}
    -- How much it moves: a swing there and back is one turn (revolutions per tick), its hand holding
    -- one item and its own bonus, plus the force's researched bonus (bulk inserters have theirs).
    facts.rotation_speed=try(function() return p.get_inserter_rotation_speed() end)
    facts.hand_bonus=p.inserter_stack_size_bonus
    facts.bulk=p.bulk
    facts.stack_bonus=p.uses_inserter_stack_size_bonus
  elseif p.type=="electric-pole" then
    facts.supply_area_distance=p.get_supply_area_distance()
    facts.max_wire_distance=p.get_max_wire_distance()
  elseif p.type=="loader" then
    -- Two tiles long (width 1, height 2 facing north), items moving the way it faces: an input loader
    -- puts into the building this far ahead of its centre, an output loader takes from the one this far
    -- behind it, and its other end is a belt's.
    facts.container_distance=p.container_distance
  end
  if p.type=="generator" or p.type=="solar-panel" then facts.produces_power=true end
  if p.type=="offshore-pump" then facts.requires_water=true end
  local boxes=try(function() return p.fluidbox_prototypes end)
  if boxes and #boxes>0 then
    facts.fluid_boxes={}
    for index,box in ipairs(boxes) do
      local connections={}
      for _,c in ipairs(box.pipe_connections or {}) do
        local at=c.position or (c.positions and c.positions[1])
        if at then
          connections[#connections+1]={position={at.x or at[1],at.y or at[2]},direction=c.direction or 0,
            type=c.connection_type or "normal",max_underground_distance=c.max_underground_distance}
        end
      end
      local filter=try(function() return box.filter and box.filter.name end)
      facts.fluid_boxes[#facts.fluid_boxes+1]={index=index,production=box.production_type or "none",
        filter=filter,connections=connections}
    end
  end
  return facts
end

local function placeable_from_item(item_name)
  local item=prototypes.item[item_name]
  local result=item and item.place_result
  if result and PLACEABLE[result.type] then return result.name end
end

local function candidate_prototypes(npc)
  local names={["burner-mining-drill"]=true,["stone-furnace"]=true}
  for _,stack in ipairs(npc.character.get_main_inventory().get_contents()) do
    local entity=placeable_from_item(stack.name); if entity then names[entity]=true end
  end
  for _,recipe in pairs(npc.force.recipes) do
    if recipe.enabled then
      for _,product in ipairs(recipe.products) do
        local entity=product.type=="item" and placeable_from_item(product.name)
        if entity then names[entity]=true end
      end
    end
  end
  local facts={}
  for _,name in ipairs(keys(names)) do facts[name]=prototype_facts(name) end
  return facts
end

-- Crafting machines this engineer can build, with speeds, for the ratio calculator.
local function machines(npc)
  local out={}
  for name,p in pairs(prototypes.get_entity_filtered{{filter="crafting-machine"}}) do
    if Recipes.ready_entity(npc.force,name) then
      local categories={}
      for c in pairs(p.crafting_categories or {}) do categories[#categories+1]=c end
      table.sort(categories)
      out[name]={speed=p.get_crafting_speed(),categories=categories}
    end
  end
  return out
end

-- The unlocked recipes this engineer could make: by hand or in a crafting machine it can build. Modded
-- games unlock hundreds it never could (an incinerator mod's one per item), too many for a designer.
local function recipes(npc)
  local out={}
  local hand=npc.character.prototype.crafting_categories or {}
  local makeable={}
  for c in pairs(hand) do makeable[c]=true end
  for _,m in pairs(machines(npc)) do
    for _,c in ipairs(m.categories) do makeable[c]=true end
  end
  for name,recipe in pairs(npc.force.recipes) do
    if recipe.enabled and not recipe.hidden and Recipes.made_in(recipe,makeable) then
      local row={name=name,category=Recipes.category(recipe),categories=Recipes.categories(recipe),
        energy=recipe.energy,handcraft=Recipes.made_in(recipe,hand),
        ingredients={},products={}}
      for _,i in ipairs(recipe.ingredients) do row.ingredients[#row.ingredients+1]={name=i.name,amount=i.amount} end
      for _,p in ipairs(recipe.products) do
        -- the chance as the game gives it now (independent or shared, not just `probability`: 2.1 reports 1 there
        -- for uranium processing's U-235, one time in 140)
        local amount=p.amount or ((p.amount_min or 1)+(p.amount_max or 1))/2
        local chance=amount>0 and DesignedStart.expected_yield(p)/amount or 1
        row.products[#row.products+1]={name=p.name,amount=amount,probability=math.abs(chance-1)>1e-9 and chance or nil}
      end
      out[#out+1]=row
    end
  end
  table.sort(out,function(a,b) return a.name<b.name end)
  return out
end

-- Run-length rows {y,x1,x2} in local tiles: compact enough for a prompt.
local RICH_MIN=300   -- ore per tile below which a spot is the patch's thin edge
local function rows_from_tiles(tiles)
  local by_row,out={},{}
  for _,t in ipairs(tiles) do by_row[t[2]]=by_row[t[2]] or {}; table.insert(by_row[t[2]],t[1]) end
  for _,y in ipairs(keys(by_row)) do
    local xs=by_row[y]; table.sort(xs)
    local start,last=xs[1],xs[1]
    for i=2,#xs+1 do
      local x=xs[i]
      if x~=last+1 then out[#out+1]={y,start,last}; start=x end
      last=x
    end
  end
  return out
end


-- Native layout suggestions for purposes whose geometry is easy to get wrong.
-- They are what the host uses when no saved blueprint or designer applies.
local WATER="water_tile"   -- (water by the game's collision mask, a mod's too, F-21)
-- The proven starter steam template: offshore pump on a water tile, pipes,
-- boiler, steam engine and a pole, relative to the water tile's centre.
local STEAM_TEMPLATE={{"pump","offshore-pump",0,1},{"boiler","boiler",4,2.5},{"engine","steam-engine",4,-2},
  {"water_pipe1","pipe",0,2},{"water_pipe2","pipe",0,3},{"water_pipe3","pipe",1,3},{"water_pipe4","pipe",2,3},
  {"steam_pipe","pipe",4,1},{"engine_pole","small-electric-pole",6,-1}}

-- A recipe of this name is unlocked, or one that makes this item (mods name recipes freely).
local function recipe_ready(npc,name)
  local recipe=npc.force.recipes[name]
  return (recipe and recipe.enabled) or Recipes.ready(npc.force,name)
end

-- What a suggestion's search spends of its check budget (npc.checks_left, set while it is searched: suggestions
-- below); past it, the search gives up.
function DesignedStart.spend(npc,checks)
  if npc.checks_left then
    if npc.checks_left<checks then error("no_room_within_checks",0) end
    npc.checks_left=npc.checks_left-checks
  end
end
-- What a search cost, charged once it is done (what it found is kept; the next spend stops past the budget).
function DesignedStart.spend_after(npc,checks)
  if npc.checks_left then npc.checks_left=npc.checks_left-math.floor(checks) end
end
-- The budget's other units, each about one placement check's time (45 us, measured on run 55's bus checkpoint, OP-3,
-- 2026-10-26): tiles a belt or pipe search floods (9 us each; one walled in floods up to 8000), and entities or tiles
-- a map read returns.
DesignedStart.TILES_PER_CHECK=5
DesignedStart.READS_PER_CHECK=50

-- Placeable now, or blocked only by trees/rocks the NPC will mine away. One check of the budget.
local function land_clear(surface,name,position,direction,npc)
  DesignedStart.spend(npc,1)
  local force=npc.force
  if surface.can_place_entity{name=name,position=position,direction=direction,force=force,
      build_check_type=defines.build_check_type.manual} then return true end
  local box=entity_box(name,position,direction)
  if surface.count_tiles_filtered{area=box,collision_mask=WATER}>0 then return false end
  for _,e in ipairs(surface.find_entities_filtered{area=box}) do
    if e.type~="tree" and e.type~="simple-entity" and e.type~="resource" and e.type~="character" then return false end
  end
  return true
end

-- Each position read once: read in the comparison, every LuaEntity.position is a call into the game, four a comparison.
local function nearest_first(list,home,position_of)
  local keyed={}
  for i,item in ipairs(list) do
    local p=position_of(item)
    keyed[i]={item=item,x=p.x,y=p.y,d=(p.x-home.x)^2+(p.y-home.y)^2}
  end
  table.sort(keyed,function(a,b)
    if a.d~=b.d then return a.d<b.d end
    return a.x<b.x or (a.x==b.x and a.y<b.y)
  end)
  for i,k in ipairs(keyed) do list[i]=k.item end
end

local function steam_power_suggestion(npc)
  for _,spec in ipairs(STEAM_TEMPLATE) do if not recipe_ready(npc,spec[2]) then return nil end end
  local surface,home=npc.character.surface,npc.home
  local waters=surface.find_tiles_filtered{position=home,radius=WINDOW,collision_mask=WATER}
  nearest_first(waters,home,function(t) return t.position end)
  for index=1,math.min(#waters,400) do
    local tile=waters[index]
    for turns=0,3 do
      for _,mirror in ipairs({1,-1}) do
        local entities,ok={},true
        for _,spec in ipairs(STEAM_TEMPLATE) do
          local key,name,x,y=spec[1],spec[2],spec[3]*mirror,spec[4]
          for _=1,turns do x,y=-y,x end
          local facing=(name=="offshore-pump" or name=="boiler" or name=="steam-engine") and turns*4 or 0
          if name=="steam-engine" then facing=facing%8 end -- engines only use the north/east axis
          local position={x=tile.position.x+.5+x,y=tile.position.y+.5+y}
          local placeable
          if name=="offshore-pump" then
            DesignedStart.spend(npc,1)
            placeable=surface.can_place_entity{name=name,position=position,direction=facing,force=npc.force,
              build_check_type=defines.build_check_type.manual}
          else placeable=land_clear(surface,name,position,facing,npc) end
          if not placeable then ok=false; break end
          entities[#entities+1]={id=key,prototype=name,position=position,direction=facing}
        end
        if ok then
          return {entities=entities,stages={{id="power",depends_on={},
            entity_ids=(function() local ids={} for i,e in ipairs(entities) do ids[i]=e.id end return ids end)(),
            manual_supplies={{entity_id="boiler",item="coal",slot="fuel"}},
            witnesses={{kind="entity_built",entity_id="engine"},{kind="power_connected",entity_id="engine"}}}}}
        end
      end
    end
  end
end

local function research_lab_suggestion(npc)
  if not (recipe_ready(npc,"lab") and recipe_ready(npc,"small-electric-pole")) then return nil end
  local surface,home=npc.character.surface,npc.home
  local poles=surface.find_entities_filtered{position=home,radius=WINDOW,force=npc.force,type="electric-pole"}
  nearest_first(poles,home,function(e) return e.position end)
  local reach=prototypes.entity["small-electric-pole"].get_max_wire_distance()-0.5
  for _,pole in ipairs(poles) do
    if pole.electric_network_id then
      local spots={}
      for dx=-6,6 do for dy=-6,6 do
        if (dx~=0 or dy~=0) and dx*dx+dy*dy<=reach*reach then
          spots[#spots+1]={x=pole.position.x+dx,y=pole.position.y+dy}
        end
      end end
      nearest_first(spots,home,function(p) return p end)
      for _,spot in ipairs(spots) do
        if land_clear(surface,"small-electric-pole",spot,0,npc) then
          for _,offset in ipairs({{2,0},{-2,0},{0,2},{0,-2}}) do
            local lab={x=spot.x+offset[1],y=spot.y+offset[2]}
            local box=entity_box("lab",lab,0)
            local overlaps_pole=box[1][1]<pole.position.x+0.5 and pole.position.x-0.5<box[2][1]
              and box[1][2]<pole.position.y+0.5 and pole.position.y-0.5<box[2][2]
            if not overlaps_pole and land_clear(surface,"lab",lab,0,npc) then
              return {entities={{id="lab_pole",prototype="small-electric-pole",position=spot,direction=0},
                  {id="lab",prototype="lab",position=lab,direction=0}},
                stages={{id="research",depends_on={},entity_ids={"lab_pole","lab"},
                  manual_supplies={{entity_id="lab",item="automation-science-pack",slot="input"}},
                  witnesses={{kind="entity_built",entity_id="lab"},{kind="power_connected",entity_id="lab"},
                    {kind="research_progress_delta",entity_id="lab",min_delta=1}}}}}
            end
          end
        end
      end
    end
  end
end

-- Choose the inserter direction that picks up inside `from` and drops inside `to`.
local function inserter_direction(position,from,to,name)
  local p=prototypes.entity[name or "inserter"]
  local a,b=p.inserter_pickup_position,p.inserter_drop_position
  local pick,drop={a.x or a[1],a.y or a[2]},{b.x or b[1],b.y or b[2]}
  local function inside(box,v) return box[1][1]<v.x and v.x<box[2][1] and box[1][2]<v.y and v.y<box[2][2] end
  for _,direction in ipairs({0,4,8,12}) do
    local pv,dv=rotate(pick,direction),rotate(drop,direction)
    if inside(from,{x=position.x+pv[1],y=position.y+pv[2]}) and inside(to,{x=position.x+dv[1],y=position.y+dv[2]}) then
      return direction
    end
  end
end

-- An automated red science block next to the power network:
-- gear assembler -> inserter -> science assembler -> inserter -> lab, two poles.
local function red_science_suggestion(npc)
  for _,name in ipairs({"assembling-machine-1","inserter","small-electric-pole","lab","automation-science-pack"}) do
    if not recipe_ready(npc,name) then return nil end
  end
  local surface,home=npc.character.surface,npc.home
  local poles=surface.find_entities_filtered{position=home,radius=WINDOW,force=npc.force,type="electric-pole"}
  nearest_first(poles,home,function(e) return e.position end)
  local reach=prototypes.entity["small-electric-pole"].get_max_wire_distance()-0.5
  local layout={{"gear_assembler","assembling-machine-1",1.5,1.5},{"gear_inserter","inserter",3.5,1.5},
    {"science_assembler","assembling-machine-1",5.5,1.5},{"science_inserter","inserter",7.5,1.5},
    {"science_lab","lab",9.5,1.5},{"block_pole_a","small-electric-pole",3.5,0.5},
    {"block_pole_b","small-electric-pole",7.5,0.5}}
  for _,pole in ipairs(poles) do
    for dx=-12,12 do for dy=-8,8 do
      local ox,oy=math.floor(pole.position.x)+dx,math.floor(pole.position.y)+dy
      local pa={x=ox+3.5,y=oy+0.5}
      local px,py=pa.x-pole.position.x,pa.y-pole.position.y
      if px*px+py*py<=reach*reach then
        local entities,ok,boxes={},true,{}
        for _,spec in ipairs(layout) do
          local position={x=ox+spec[3],y=oy+spec[4]}
          if not land_clear(surface,spec[2],position,0,npc) then ok=false; break end
          boxes[spec[1]]=entity_box(spec[2],position,0)
          entities[#entities+1]={id=spec[1],prototype=spec[2],position=position,direction=0}
        end
        if ok then
          local d1=inserter_direction(entities[2].position,boxes.gear_assembler,boxes.science_assembler)
          local d2=inserter_direction(entities[4].position,boxes.science_assembler,boxes.science_lab)
          if d1 and d2 then
            entities[2].direction,entities[4].direction=d1,d2
            entities[1].recipe,entities[3].recipe="iron-gear-wheel","automation-science-pack"
            local ids={}
            for i,e in ipairs(entities) do ids[i]=e.id end
            return {entities=entities,stages={{id="red-science",depends_on={},entity_ids=ids,
              manual_supplies={{entity_id="gear_assembler",item="iron-plate",slot="input"},
                {entity_id="science_assembler",item="copper-plate",slot="input"}},
              witnesses={{kind="entity_built",entity_id="science_assembler"},
                {kind="power_connected",entity_id="science_assembler"},
                {kind="product_counter_delta",entity_id="science_assembler",item="automation-science-pack",min_delta=5}}}}}
          end
        end
      end
    end end
  end
end

-- Plate belts: a burner inserter takes plates out of one of the NPC's
-- furnaces onto a belt; the belt runs (shortest free path) to a burner
-- inserter feeding a science assembler. Burner inserters need no power; the
-- NPC keeps them in coal. Once plates arrive, hand delivery of that item stops.
local BELT_DIRECTION={["1,0"]=4,["-1,0"]=12,["0,1"]=8,["0,-1"]=0}
local MAX_BELT=70

local function key(x,y) return x..","..y end

-- A building of a kept plan of `purpose`: by its id, else the first of its kind in it (`prototype`, and `recipe` where
-- the plan has more than one). A layout saved to the library and built from there has ids of its own: by id alone,
-- red science's lab was never found, and green science, which goes beside it, was never offered (run 49, 2026-10-26).
local function history_entity(npc,purpose,id,prototype,recipe)
  for _,plan in ipairs(npc.design_history or {}) do
    if plan.purpose==purpose then
      local entry=plan.entities[id]
      if not (entry and entry.entity and entry.entity.valid) and prototype then
        entry=nil
        for _,eid in ipairs(plan.order or {}) do
          local x=plan.entities[eid]
          local e=x and x.entity
          if x and x.prototype==prototype and e and e.valid then
            local r=recipe and try(function() return e.get_recipe() end)
            if not recipe or (r and r.name==recipe) or x.recipe==recipe then entry=x break end
          end
        end
      end
      local e=entry and entry.entity
      if e and e.valid then return e end
    end
  end
end
DesignedStart.history_entity=history_entity

local function makes(furnace,product)
  local recipe=try(function() return furnace.get_recipe() end)
  if recipe and recipe.name==product then return true end
  local previous=try(function() return furnace.previous_recipe end)
  local name=previous and previous.name
  if type(name)=="table" then name=name.name end
  if name==product then return true end
  local output=furnace.get_output_inventory()
  return output and output.get_item_count(product)>0
end

local function designed_furnaces(npc,product)
  local out={}
  for _,plan in ipairs(npc.design_history or {}) do
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="furnace" and makes(e,product) then out[#out+1]=e end
    end
  end
  return out
end

local function tile_range(box)
  local lt,rb=box.left_top or box[1],box.right_bottom or box[2]
  return math.floor(lt.x or lt[1]),math.floor(lt.y or lt[2]),math.ceil(rb.x or rb[1])-1,math.ceil(rb.y or rb[2])-1
end

-- What stands in `area` (not trees, rocks, ore, characters or items on the ground) and water, as tile keys. The game
-- leaves out those types itself (`invert`): on ore and in forest they were thousands of entities read one by one.
-- Charged to `npc`'s check budget by what it read.
local function blocked_tiles(surface,area,npc)
  DesignedStart.spend(npc,1)
  local blocked={}
  local found=surface.find_entities_filtered{area=area,type={"tree","simple-entity","resource","character","item-entity"},
    invert=true}
  for _,e in ipairs(found) do
    local x0,y0,x1,y1=tile_range(e.bounding_box)
    for x=x0,x1 do for y=y0,y1 do blocked[key(x,y)]=true end end
  end
  local water=surface.find_tiles_filtered{area=area,collision_mask=WATER}
  for _,t in ipairs(water) do blocked[key(t.position.x,t.position.y)]=true end
  DesignedStart.spend_after(npc,(#found+#water)/DesignedStart.READS_PER_CHECK)
  return blocked
end

local function tile_box(x,y) return {{x,y},{x+1,y+1}} end

-- Inserter spots around `entity`; `taking` = inserter takes from the entity onto the belt.
local function endpoints(entity,blocked,toward,taking)
  local x0,y0,x1,y1=tile_range(entity.bounding_box)
  local spots={}
  for x=x0,x1 do
    spots[#spots+1]={x,y0-1,x,y0-2}; spots[#spots+1]={x,y1+1,x,y1+2}
  end
  for y=y0,y1 do
    spots[#spots+1]={x0-1,y,x0-2,y}; spots[#spots+1]={x1+1,y,x1+2,y}
  end
  table.sort(spots,function(a,b)
    local da=(a[3]-toward.x)^2+(a[4]-toward.y)^2
    local db=(b[3]-toward.x)^2+(b[4]-toward.y)^2
    if da~=db then return da<db end
    return a[1]<b[1] or (a[1]==b[1] and a[2]<b[2])
  end)
  local box={{x0,y0},{x1+1,y1+1}}
  local out={}
  for _,s in ipairs(spots) do
    if not blocked[key(s[1],s[2])] and not blocked[key(s[3],s[4])] then
      local position={x=s[1]+0.5,y=s[2]+0.5}
      local direction
      if taking then direction=inserter_direction(position,box,tile_box(s[3],s[4]),"burner-inserter")
      else direction=inserter_direction(position,tile_box(s[3],s[4]),box,"burner-inserter") end
      if direction then out[#out+1]={inserter=position,direction=direction,belt={s[3],s[4]}} end
    end
  end
  return out
end

-- The path, or nil; and how many tiles the search looked at (what it cost).
local function belt_path(blocked,start,goal,limit)
  local queue,head,came={start},1,{[key(start[1],start[2])]=false}
  local box={math.min(start[1],goal[1])-14,math.min(start[2],goal[2])-14,
    math.max(start[1],goal[1])+14,math.max(start[2],goal[2])+14}
  local steps={{0,-1},{1,0},{0,1},{-1,0}}
  while head<=#queue and #queue<8000 do
    local node=queue[head]; head=head+1
    if node[1]==goal[1] and node[2]==goal[2] then
      local path,k={},key(node[1],node[2])
      while k do
        local x,y=k:match("^(-?%d+),(-?%d+)$")
        table.insert(path,1,{tonumber(x),tonumber(y)})
        k=came[k] or nil
      end
      if #path>limit then return nil,#queue end
      return path,#queue
    end
    for i=1,4 do
      local d=steps[i]
      local x,y=node[1]+d[1],node[2]+d[2]
      local k=key(x,y)
      if came[k]==nil and not blocked[k] and x>=box[1] and x<=box[3] and y>=box[2] and y<=box[4] then
        came[k]=key(node[1],node[2]); queue[#queue+1]={x,y}
      end
    end
  end
  return nil,#queue
end

-- A furnace this far (centre to centre) from the target is never joined to it: its belt would be longer than MAX_BELT
-- (each end's belt tile lies within five tiles of its machine's centre).
DesignedStart.PLATE_REACH=MAX_BELT+10

-- Every furnace making `product`, nearest first, tried until a belt joins one to `target`. The tiles a try reserves
-- (its inserters) are marked on `blocked` and taken off again: copying the whole map for every try, over every furnace
-- of a bus base, froze the game 30 s at each plan request (OP-3, 2026-10-26).
local function plate_line(npc,blocked,prefix,product,target,automate)
  local furnaces=designed_furnaces(npc,product)
  local at=target.position
  nearest_first(furnaces,at,function(e) return e.position end)
  for _,furnace in ipairs(furnaces) do
    local fp=furnace.position
    if (fp.x-at.x)^2+(fp.y-at.y)^2>DesignedStart.PLATE_REACH^2 then break end   -- (and every one after it)
    for _,give in ipairs(endpoints(target,blocked,fp,false)) do
      local gk=key(math.floor(give.inserter.x),math.floor(give.inserter.y))
      local had_give=blocked[gk]
      blocked[gk]=true
      for _,take in ipairs(endpoints(furnace,blocked,{x=give.belt[1],y=give.belt[2]},true)) do
        local tk,bk=key(math.floor(take.inserter.x),math.floor(take.inserter.y)),key(give.belt[1],give.belt[2])
        -- Adjacent machines: the take inserter may sit where the line must end.
        local path
        if tk~=bk then
          DesignedStart.spend(npc,1)
          local had_take,had_belt=blocked[tk],blocked[bk]
          blocked[tk],blocked[bk]=true,nil
          local looked
          path,looked=belt_path(blocked,take.belt,give.belt,MAX_BELT)
          blocked[tk],blocked[bk]=had_take,had_belt
          DesignedStart.spend_after(npc,looked/DesignedStart.TILES_PER_CHECK)
        end
        if path then
          local entities={{id=prefix.."_take",prototype="burner-inserter",position=take.inserter,direction=take.direction}}
          for i,tile in ipairs(path) do
            local nxt=path[i+1] or path[i-1] and {2*tile[1]-path[i-1][1],2*tile[2]-path[i-1][2]} or {tile[1],tile[2]-1}
            entities[#entities+1]={id=prefix.."_belt_"..i,prototype="transport-belt",
              position={x=tile[1]+0.5,y=tile[2]+0.5},direction=BELT_DIRECTION[(nxt[1]-tile[1])..","..(nxt[2]-tile[2])] or 0}
            blocked[key(tile[1],tile[2])]=true
          end
          entities[#entities+1]={id=prefix.."_give",prototype="burner-inserter",position=give.inserter,direction=give.direction}
          blocked[key(math.floor(take.inserter.x),math.floor(take.inserter.y))]=true
          blocked[key(math.floor(give.inserter.x),math.floor(give.inserter.y))]=true
          local ids={}
          for i,e in ipairs(entities) do ids[i]=e.id end
          return entities,{id=prefix.."-belt",depends_on={},entity_ids=ids,
            manual_supplies={{entity_id=prefix.."_take",item="coal",slot="fuel"},{entity_id=prefix.."_give",item="coal",slot="fuel"}},
            witnesses={{kind="entity_built",entity_id=prefix.."_give"},
              {kind="belt_items",entity_id=prefix.."_belt_"..#path,min_delta=1}},
            automates={automate}}
        end
      end
      blocked[gk]=had_give
    end
  end
end

local function plate_belts_suggestion(npc)
  for _,name in ipairs({"transport-belt","burner-inserter"}) do if not recipe_ready(npc,name) then return nil end end
  local gear=history_entity(npc,"red-science-automation","gear_assembler","assembling-machine-1","iron-gear-wheel")
  local science=history_entity(npc,"red-science-automation","science_assembler","assembling-machine-1",
    "automation-science-pack")
  if not (gear and science) then return nil end
  local surface,home=npc.character.surface,npc.home
  local area={{home.x-WINDOW,home.y-WINDOW},{home.x+WINDOW,home.y+WINDOW}}
  local blocked=blocked_tiles(surface,area,npc)
  local entities,stages={},{}
  for _,line in ipairs({{"iron","iron-plate",gear,{purpose="red-science-automation",entity_id="gear_assembler",item="iron-plate"}},
      {"copper","copper-plate",science,{purpose="red-science-automation",entity_id="science_assembler",item="copper-plate"}}}) do
    -- (a line found is kept when the next runs out of the check budget)
    local ok,list,stage=pcall(plate_line,npc,blocked,line[1],line[2],line[3],line[4])
    if not ok and list~="no_room_within_checks" then error(list,0) end
    if ok and list then
      for _,e in ipairs(list) do entities[#entities+1]=e end
      stages[#stages+1]=stage
    end
  end
  if #stages==0 then return nil end
  return {entities=entities,stages=stages}
end

-- Green science: an assembler making logistic science packs, attached to the
-- red science block's lab from a free side (inserter into the same lab, one
-- pole wired to the block). The NPC hand-delivers inserters and belts for now.
local function green_science_suggestion(npc)
  for _,name in ipairs({"assembling-machine-1","inserter","small-electric-pole","logistic-science-pack"}) do
    if not recipe_ready(npc,name) then return nil end
  end
  local lab=history_entity(npc,"red-science-automation","science_lab","lab")
  if not lab then return nil end
  local surface,L=npc.character.surface,lab.position
  local lab_box=entity_box("lab",L,0)
  -- east, south, north (the red inserter uses the west side)
  for _,side in ipairs({{1,0,0,-1},{0,1,1,0},{0,-1,1,0}}) do
    local v,perp={side[1],side[2]},{side[3],side[4]}
    local inserter={x=L.x+2*v[1],y=L.y+2*v[2]}
    local assembler={x=L.x+4*v[1],y=L.y+4*v[2]}
    local pole={x=inserter.x+perp[1],y=inserter.y+perp[2]}
    local ok=land_clear(surface,"inserter",inserter,0,npc)
      and land_clear(surface,"assembling-machine-1",assembler,0,npc)
      and land_clear(surface,"small-electric-pole",pole,0,npc)
    local direction=ok and inserter_direction(inserter,entity_box("assembling-machine-1",assembler,0),lab_box)
    if direction then
      return {entities={{id="green_assembler",prototype="assembling-machine-1",position=assembler,direction=0,
            recipe="logistic-science-pack"},
          {id="green_inserter",prototype="inserter",position=inserter,direction=direction},
          {id="green_pole",prototype="small-electric-pole",position=pole,direction=0}},
        stages={{id="green-science",depends_on={},entity_ids={"green_assembler","green_inserter","green_pole"},
          manual_supplies={{entity_id="green_assembler",item="inserter",slot="input"},
            {entity_id="green_assembler",item="transport-belt",slot="input"}},
          witnesses={{kind="entity_built",entity_id="green_assembler"},
            {kind="power_connected",entity_id="green_assembler"},
            {kind="product_counter_delta",entity_id="green_assembler",item="logistic-science-pack",min_delta=5}}}}}
    end
  end
end

-- Steel: a burner inserter takes iron plates out of an existing iron furnace
-- into a second stone furnace, which turns them into steel.
local function steel_suggestion(npc)
  for _,name in ipairs({"steel-plate","burner-inserter","stone-furnace"}) do
    if not recipe_ready(npc,name) then return nil end
  end
  local surface,home=npc.character.surface,npc.home
  local blocked=blocked_tiles(surface,{{home.x-WINDOW,home.y-WINDOW},{home.x+WINDOW,home.y+WINDOW}},npc)
  local furnaces=designed_furnaces(npc,"iron-plate")
  nearest_first(furnaces,home,function(e) return e.position end)
  for _,furnace in ipairs(furnaces) do
    for _,take in ipairs(endpoints(furnace,blocked,home,true)) do
      local tx,ty=take.belt[1],take.belt[2]
      local ix,iy=math.floor(take.inserter.x),math.floor(take.inserter.y)
      -- A 2x2 furnace centred on a tile corner that covers the drop tile.
      for _,c in ipairs({{tx,ty},{tx+1,ty},{tx,ty+1},{tx+1,ty+1}}) do
        local center={x=c[1],y=c[2]}
        local x0,y0,x1,y1=tile_range(entity_box("stone-furnace",center,0))
        local free=not (ix>=x0 and ix<=x1 and iy>=y0 and iy<=y1)
        for x=x0,x1 do for y=y0,y1 do if blocked[key(x,y)] then free=false end end end
        if free and land_clear(surface,"stone-furnace",center,0,npc) then
          return {entities={{id="steel_inserter",prototype="burner-inserter",position=take.inserter,direction=take.direction},
              {id="steel_furnace",prototype="stone-furnace",position=center,direction=0}},
            stages={{id="steel",depends_on={},entity_ids={"steel_inserter","steel_furnace"},
              manual_supplies={{entity_id="steel_inserter",item="coal",slot="fuel"},
                {entity_id="steel_furnace",item="coal",slot="fuel"}},
              witnesses={{kind="entity_built",entity_id="steel_furnace"},
                {kind="product_counter_delta",entity_id="steel_furnace",item="steel-plate",min_delta=2}}}}}
        end
      end
    end
  end
end

-- Small poles from `start` back to the NPC's powered network, each within
-- wire reach of the last; nil when no route is found.
local function pole_chain(npc,start,blocked)
  local surface,home=npc.character.surface,npc.home
  local reach=prototypes.entity["small-electric-pole"].get_max_wire_distance()-0.5
  local powered={}
  for _,p in ipairs(surface.find_entities_filtered{position=home,radius=REACH+WINDOW,force=npc.force,
      type="electric-pole"}) do
    if p.electric_network_id then powered[#powered+1]={x=p.position.x,y=p.position.y} end
  end
  if #powered==0 then return nil end
  local chain,current={},start
  for _=1,32 do
    nearest_first(powered,current,function(p) return p end)
    local goal=powered[1]
    local d=math.sqrt((goal.x-current.x)^2+(goal.y-current.y)^2)
    if d<=reach then return chain end
    local best,best_d
    for dx=-7,7 do for dy=-7,7 do
      local p={x=math.floor(current.x)+dx+0.5,y=math.floor(current.y)+dy+0.5}
      local step=math.sqrt((p.x-current.x)^2+(p.y-current.y)^2)
      if step>=2 and step<=reach and not blocked[key(p.x-0.5,p.y-0.5)]
          and math.abs(p.x-home.x)<=REACH and math.abs(p.y-home.y)<=REACH then
        local gd=math.sqrt((goal.x-p.x)^2+(goal.y-p.y)^2)
        if gd<d-1 and (not best_d or gd<best_d) and land_clear(surface,"small-electric-pole",p,0,npc) then
          best,best_d=p,gd
        end
      end
    end end
    if not best then return nil end
    chain[#chain+1]=best
    blocked[key(best.x-0.5,best.y-0.5)]=true
    current=best
  end
end

-- `ore` under a drill's mining area (`radius` round `c`), or 0 where the area reaches another ore the drill mines too
-- (`mines`: its resource categories): a drill over both puts both out (copper ore in an iron furnace stops the drill),
-- as companion/smelting.foreign_tiles keeps the planner's fields off it.
function DesignedStart.ore_under(surface,ore,c,radius,mines)
  local n=0
  for _,r in ipairs(surface.find_entities_filtered{type="resource",
      area={{c.x-radius,c.y-radius},{c.x+radius,c.y+radius}}}) do
    if r.name==ore then n=n+1
    elseif mines[r.prototype.resource_category] then return 0 end
  end
  return n
end

-- Electric mining: two electric drills side by side on `ore`, each dropping
-- into a stone furnace, with a pole between them chained to the network.
local function electric_mining_suggestion(npc,ore,product)
  for _,name in ipairs({"electric-mining-drill","small-electric-pole","stone-furnace"}) do
    if not recipe_ready(npc,name) then return nil end
  end
  local surface,home=npc.character.surface,npc.home
  local area={{home.x-WINDOW+3,home.y-WINDOW+5},{home.x+WINDOW-6,home.y+WINDOW-4}}
  local tiles=surface.find_entities_filtered{area=area,type="resource",name=ore}
  if #tiles==0 then return nil end
  local poles=surface.find_entities_filtered{position=home,radius=WINDOW*1.5,force=npc.force,type="electric-pole"}
  if #poles==0 then return nil end
  local near=poles[1].position
  nearest_first(tiles,near,function(e) return e.position end)
  local drill=prototypes.entity["electric-mining-drill"]
  local v=drill.vector_to_place_result
  local drop={v.x or v[1],v.y or v[2]}
  local radius=drill.get_mining_drill_radius()
  local blocked=blocked_tiles(surface,{{home.x-WINDOW,home.y-WINDOW},{home.x+WINDOW,home.y+WINDOW}},npc)
  local mines=drill.resource_categories or {}
  local function ore_under(c) return DesignedStart.ore_under(surface,ore,c,radius,mines) end
  local function clear(name,p)
    local x0,y0,x1,y1=tile_range(entity_box(name,p,0))
    for x=x0,x1 do for y=y0,y1 do if blocked[key(x,y)] then return false end end end
    return land_clear(surface,name,p,0,npc)
  end
  for i=1,math.min(#tiles,200) do
    local cx,cy=math.floor(tiles[i].position.x),math.floor(tiles[i].position.y)
    local d1,d2={x=cx+0.5,y=cy+0.5},{x=cx+3.5,y=cy+0.5}
    local f1,f2={x=cx+1,y=cy-2},{x=cx+4,y=cy-2}
    local pole={x=cx+2.5,y=cy+2.5}
    local fits=ore_under(d1)>=12 and ore_under(d2)>=12
    -- Each furnace must cover its drill's drop point.
    for _,pair in ipairs({{d1,f1},{d2,f2}}) do
      local px,py=pair[1].x+drop[1],pair[1].y+drop[2]
      local box=entity_box("stone-furnace",pair[2],0)
      fits=fits and px>box[1][1] and px<box[2][1] and py>box[1][2] and py<box[2][2]
    end
    fits=fits and clear("electric-mining-drill",d1) and clear("electric-mining-drill",d2)
      and clear("stone-furnace",f1) and clear("stone-furnace",f2) and clear("small-electric-pole",pole)
    if fits then
      local planned=setmetatable({},{__index=blocked})
      for _,spec in ipairs({{"electric-mining-drill",d1},{"electric-mining-drill",d2},{"stone-furnace",f1},
          {"stone-furnace",f2},{"small-electric-pole",pole}}) do
        local x0,y0,x1,y1=tile_range(entity_box(spec[1],spec[2],0))
        for x=x0,x1 do for y=y0,y1 do planned[key(x,y)]=true end end
      end
      local chain=pole_chain(npc,pole,planned)
      if chain then
        local prefix=ore:gsub("%-ore","")
        local entities={{id=prefix.."_drill_1",prototype="electric-mining-drill",position=d1,direction=0},
          {id=prefix.."_drill_2",prototype="electric-mining-drill",position=d2,direction=0},
          {id=prefix.."_furnace_1",prototype="stone-furnace",position=f1,direction=0},
          {id=prefix.."_furnace_2",prototype="stone-furnace",position=f2,direction=0},
          {id=prefix.."_drill_pole",prototype="small-electric-pole",position=pole,direction=0}}
        for n,p in ipairs(chain) do
          entities[#entities+1]={id=prefix.."_line_pole_"..n,prototype="small-electric-pole",position=p,direction=0}
        end
        local ids={}
        for n,e in ipairs(entities) do ids[n]=e.id end
        return {entities=entities,stages={{id="electric-"..prefix,depends_on={},entity_ids=ids,
          manual_supplies={{entity_id=prefix.."_furnace_1",item="coal",slot="fuel"},
            {entity_id=prefix.."_furnace_2",item="coal",slot="fuel"}},
          witnesses={{kind="entity_built",entity_id=prefix.."_drill_1"},
            {kind="power_connected",entity_id=prefix.."_drill_1"},
            {kind="product_counter_delta",entity_id=prefix.."_furnace_1",item=product,min_delta=5}}}}}
      end
    end
  end
end

-- More power: a second steam engine end-to-end with the first (one boiler
-- drives two engines), plus a pole so it joins the network.
local function power_expansion_suggestion(npc)
  if not (recipe_ready(npc,"steam-engine") and recipe_ready(npc,"small-electric-pole")) then return nil end
  local engine=history_entity(npc,"steam-power","engine","steam-engine")
  local pole=history_entity(npc,"steam-power","engine_pole","small-electric-pole")
  if not (engine and pole) then return nil end
  local surface,p=npc.character.surface,engine.position
  local reach=prototypes.entity["small-electric-pole"].get_max_wire_distance()-0.5
  local axis=(engine.direction==4 or engine.direction==12) and {5,0} or {0,5}
  for _,sign in ipairs({1,-1}) do
    local spot={x=p.x+sign*axis[1],y=p.y+sign*axis[2]}
    if land_clear(surface,"steam-engine",spot,engine.direction,npc) then
      local box=entity_box("steam-engine",spot,engine.direction)
      local candidates={}
      for dx=-4,4 do for dy=-4,4 do
        local q={x=math.floor(spot.x)+dx+0.5,y=math.floor(spot.y)+dy+0.5}
        local inside=q.x>box[1][1] and q.x<box[2][1] and q.y>box[1][2] and q.y<box[2][2]
        local d=math.sqrt((q.x-pole.position.x)^2+(q.y-pole.position.y)^2)
        if not inside and d<=reach then candidates[#candidates+1]=q end
      end end
      nearest_first(candidates,spot,function(q) return q end)
      for _,q in ipairs(candidates) do
        if land_clear(surface,"small-electric-pole",q,0,npc) then
          return {entities={{id="engine_2",prototype="steam-engine",position=spot,direction=engine.direction},
              {id="engine_2_pole",prototype="small-electric-pole",position=q,direction=0}},
            stages={{id="more-power",depends_on={},entity_ids={"engine_2","engine_2_pole"},manual_supplies={},
              witnesses={{kind="entity_built",entity_id="engine_2"},{kind="power_connected",entity_id="engine_2"}}}}}
        end
      end
    end
  end
end

-- The oil block, relative to the pumpjack on an oil well (north-facing):
-- the refinery sits directly on the pumpjack's output, a pipe column carries
-- petroleum gas to a plastic plant and a sulfur plant, and water is piped
-- from an offshore pump to the sulfur plant's other input. A chemical plant
-- comes only once its recipe is unlocked (plastic often before sulfur), and
-- water only for a recipe that takes it; a later recipe grows the block in place.
local OIL_BLOCK={
  {"pumpjack","pumpjack",0,0,0},
  {"refinery","oil-refinery",0,-4,0,"basic-oil-processing"},
  {"plastic_plant","chemical-plant",0,-9,4,"plastic-bar"},
  {"gas_pipe_1","pipe",2,-7,0},{"gas_pipe_2","pipe",2,-8,0},{"gas_pipe_3","pipe",2,-9,0},
  {"gas_pipe_4","pipe",2,-10,0},{"gas_pipe_5","pipe",2,-11,0},{"gas_pipe_6","pipe",2,-12,0},
  {"gas_pipe_7","pipe",2,-13,0},
  {"sulfur_plant","chemical-plant",3,-15,8,"sulfur"},
  {"oil_pole_1","small-electric-pole",-2,-1,0},{"oil_pole_2","small-electric-pole",-2,-7,0},
  {"oil_pole_3","small-electric-pole",1,-12,0},
  {"brick_furnace","stone-furnace",-3.5,-12.5,0}}
local WATER_INLET={4,-13}

local function turn(x,y,turns) for _=1,turns do x,y=-y,x end return x,y end

local DIRECTION_VECTOR={[0]={0,-1},[4]={1,0},[8]={0,1},[12]={-1,0}}
-- Tiles where a pipe would connect to this machine's fluid boxes.
local function connection_tiles(name,center,direction)
  local out={}
  for _,box in ipairs(prototypes.entity[name].fluidbox_prototypes or {}) do
    for _,c in ipairs(box.pipe_connections or {}) do
      local p=c.positions and c.positions[direction/4+1] or c.position
      local facing=((c.direction or 0)+direction)%16
      local v=DIRECTION_VECTOR[facing]
      if p and v then
        out[#out+1]={math.floor(center.x+(p.x or p[1])+v[1]),math.floor(center.y+(p.y or p[2])+v[2])}
      end
    end
  end
  return out
end

-- Pipe tiles from `start` to `goal` that never touch other fluid networks, or nil; and how many tiles it looked at.
local function pipe_route(blocked,avoid,start,goal,limit)
  local queue,head,came={start},1,{[key(start[1],start[2])]=false}
  if blocked[key(start[1],start[2])] or avoid[key(start[1],start[2])] then return nil,0 end
  local steps={{0,-1},{1,0},{0,1},{-1,0}}
  while head<=#queue and #queue<6000 do
    local node=queue[head]; head=head+1
    if node[1]==goal[1] and node[2]==goal[2] then
      local path,k={},key(node[1],node[2])
      while k do
        local x,y=k:match("^(-?%d+),(-?%d+)$")
        table.insert(path,1,{tonumber(x),tonumber(y)})
        k=came[k] or nil
      end
      if #path>limit then return nil,#queue end
      return path,#queue
    end
    for i=1,4 do
      local d=steps[i]
      local x,y=node[1]+d[1],node[2]+d[2]
      local k=key(x,y)
      local free=not blocked[k] and (not avoid[k] or x==goal[1] and y==goal[2])
      if came[k]==nil and free then came[k]=key(node[1],node[2]); queue[#queue+1]={x,y} end
    end
  end
  return nil,#queue
end

local function in_window(p,home) return math.abs(p.x-home.x)<=WINDOW-1 and math.abs(p.y-home.y)<=WINDOW-1 end
local function within_reach(p,home) return math.abs(p.x-home.x)<=REACH and math.abs(p.y-home.y)<=REACH end

-- Whether a recipe takes water (the oil block pipes it in only then).
function DesignedStart.takes_water(name)
  local recipe=name and prototypes.recipe[name]
  for _,i in ipairs(recipe and recipe.ingredients or {}) do
    if i.type=="fluid" and i.name=="water" then return true end
  end
  return false
end

-- Everything the oil block needs around a pumpjack at `well` turned `turns`
-- quarter turns: entity list, water pipes from a new offshore pump, and a
-- pole chain to the network. nil and why when anything does not fit.
-- `standing`: id -> the built entity of the block's parts that stand; one counts only where it is this
-- template's part (its prototype on its spot), and is kept, marked standing=true.
local function oil_block(npc,well,turns,existing_pumpjack,standing)
  standing=standing or {}
  local surface,home=npc.character.surface,npc.home
  local around={{well.x-40,well.y-40},{well.x+40,well.y+40}}
  local blocked=blocked_tiles(surface,around,npc)
  local entities,water={},false
  for _,spec in ipairs(OIL_BLOCK) do
    local sx,sy=turn(spec[3],spec[4],turns)
    local there=standing[spec[1]]
    if there and there.valid and there.name==spec[2] and math.abs(there.position.x-(well.x+sx))<0.6
        and math.abs(there.position.y-(well.y+sy))<0.6 then
      entities[#entities+1]={id=spec[1],prototype=spec[2],position={x=well.x+sx,y=well.y+sy},
        direction=(spec[5]+4*turns)%16,recipe=spec[6],standing=true}
    elseif spec[2]~="chemical-plant" or recipe_ready(npc,spec[6]) then
      water=water or DesignedStart.takes_water(spec[6])
      local x,y=turn(spec[3],spec[4],turns)
      local p={x=well.x+x,y=well.y+y}
      local direction=(spec[5]+4*turns)%16
      if spec[2]=="pumpjack" or existing_pumpjack and spec[1]=="oil_pole_1" then
        if not existing_pumpjack then DesignedStart.spend(npc,1) end
        if not existing_pumpjack and not surface.can_place_entity{name="pumpjack",position=p,direction=direction,
            force=npc.force} then return nil,"pumpjack_unplaceable" end
      else
        if not within_reach(p,home) then return nil,"out_of_reach:"..spec[1] end
        local x0,y0,x1,y1=tile_range(entity_box(spec[2],p,direction))
        for tx=x0,x1 do for ty=y0,y1 do if blocked[key(tx,ty)] then return nil,"blocked:"..spec[1] end end end
        if not land_clear(surface,spec[2],p,direction,npc) then return nil,"not_clear:"..spec[1] end
      end
      entities[#entities+1]={id=spec[1],prototype=spec[2],position=p,direction=direction,recipe=spec[6]}
    end
  end
  -- Mark the block, and keep the water pipe away from every other fluid connection.
  local avoid={}
  local ix,iy=turn(WATER_INLET[1],WATER_INLET[2],turns)
  local inlet={math.floor(well.x+ix),math.floor(well.y+iy)}
  for _,e in ipairs(entities) do
    local x0,y0,x1,y1=tile_range(entity_box(e.prototype,e.position,e.direction))
    for tx=x0,x1 do for ty=y0,y1 do blocked[key(tx,ty)]=true end end
    if e.prototype=="pipe" then
      local tx,ty=math.floor(e.position.x),math.floor(e.position.y)
      for _,d in ipairs({{0,0},{0,-1},{1,0},{0,1},{-1,0}}) do avoid[key(tx+d[1],ty+d[2])]=true end
    elseif e.prototype~="small-electric-pole" then
      for _,t in ipairs(connection_tiles(e.prototype,e.position,e.direction)) do avoid[key(t[1],t[2])]=true end
    end
  end
  for _,pipe in ipairs(surface.find_entities_filtered{area=around,type={"pipe","pipe-to-ground"}}) do
    local tx,ty=math.floor(pipe.position.x),math.floor(pipe.position.y)
    for _,d in ipairs({{0,0},{0,-1},{1,0},{0,1},{-1,0}}) do avoid[key(tx+d[1],ty+d[2])]=true end
  end
  avoid[key(inlet[1],inlet[2])]=nil
  if standing.water_pump and standing.water_pump.valid then water=false end   -- (piped in by an earlier part)
  local waters=water and surface.find_tiles_filtered{position={x=inlet[1],y=inlet[2]},radius=40,collision_mask=WATER}
    or {}
  nearest_first(waters,{x=inlet[1],y=inlet[2]},function(t) return t.position end)
  local route,pump
  for index=1,math.min(#waters,60) do
    local tile=waters[index]
    for t=0,3 do
      local px,py=turn(0,1,t)
      local spot={x=tile.position.x+0.5+px,y=tile.position.y+0.5+py}
      local key_spot=key(math.floor(spot.x),math.floor(spot.y))
      local open=within_reach(spot,home) and not blocked[key_spot] and not avoid[key_spot]
      if open then DesignedStart.spend(npc,1) end
      if open and surface.can_place_entity{name="offshore-pump",position=spot,direction=4*t,force=npc.force,
          build_check_type=defines.build_check_type.manual} then
        local ox,oy=turn(0,1,t)
        local start={math.floor(spot.x+ox),math.floor(spot.y+oy)}
        blocked[key_spot]=true
        DesignedStart.spend(npc,1)
        local looked
        route,looked=pipe_route(blocked,avoid,start,inlet,90)
        DesignedStart.spend_after(npc,looked/DesignedStart.TILES_PER_CHECK)
        blocked[key_spot]=nil
        if route then pump={x=spot.x,y=spot.y,direction=4*t}; break end
      end
    end
    if route then break end
  end
  if water and not route then return nil,"no_water_route:"..#waters.." water tiles" end
  if water then
    entities[#entities+1]={id="water_pump",prototype="offshore-pump",position={x=pump.x,y=pump.y},direction=pump.direction}
    for n,tile in ipairs(route) do
      entities[#entities+1]={id="water_pipe_"..n,prototype="pipe",position={x=tile[1]+0.5,y=tile[2]+0.5},direction=0}
      blocked[key(tile[1],tile[2])]=true
    end
  end
  local first_pole
  for _,e in ipairs(entities) do if e.id=="oil_pole_1" then first_pole=e.position end end
  local chain=pole_chain(npc,first_pole,blocked)
  if not chain then return nil,"no_pole_chain" end
  for n,p in ipairs(chain) do
    entities[#entities+1]={id="oil_line_pole_"..n,prototype="small-electric-pole",position=p,direction=0}
  end
  return entities
end

local function pick(entities,ids)
  local wanted,out={},{}
  for _,id in ipairs(ids) do wanted[id]=true end
  for _,e in ipairs(entities) do
    if wanted[e.id] or wanted[e.id:gsub("_%d+$","_*")] then out[#out+1]=e end
  end
  return out
end

local function ids_of(list) local ids={} for i,e in ipairs(list) do ids[i]=e.id end return ids end

-- Oil gathering: a pumpjack on the nearest oil well where the whole oil
-- block will fit later. Mining crude oil unlocks oil processing.
local function oil_gathering_suggestion(npc)
  if not (recipe_ready(npc,"pumpjack") and recipe_ready(npc,"small-electric-pole")) then return nil end
  local surface,home=npc.character.surface,npc.home
  local wells=surface.find_entities_filtered{area={{home.x-WINDOW,home.y-WINDOW},{home.x+WINDOW,home.y+WINDOW}},
    type="resource",name="crude-oil"}
  local seen={}
  for _,w in ipairs(wells) do seen[w.unit_number or key(w.position.x,w.position.y)]=true end
  for _,cell in pairs(npc.known_patches or {}) do
    if cell.resource=="crude-oil" and within_reach(cell,home) then
      for _,w in ipairs(surface.find_entities_filtered{position={x=cell.x,y=cell.y},radius=12,name="crude-oil"}) do
        local k=w.unit_number or key(w.position.x,w.position.y)
        if not seen[k] then seen[k]=true; wells[#wells+1]=w end
      end
    end
  end
  nearest_first(wells,home,function(e) return e.position end)
  local why
  for i=1,math.min(#wells,8) do
    for turns=0,3 do
      local block,failed=oil_block(npc,wells[i].position,turns,false)
      why=why or failed
      if block then
        local entities=pick(block,{"pumpjack","oil_pole_1","oil_line_pole_*"})
        return {entities=entities,stages={{id="oil-well",depends_on={},entity_ids=ids_of(entities),
          manual_supplies={},witnesses={{kind="entity_built",entity_id="pumpjack"},
            {kind="power_connected",entity_id="pumpjack"},{kind="fluid_output",entity_id="pumpjack",min_delta=10}}}}}
      end
    end
  end
  if #wells>0 then log("[npc-factions] oil gathering: no layout at "..math.min(#wells,8).." wells (first: "..tostring(why)..")") end
end

-- Oil processing around the existing pumpjack: refinery, plastic and sulfur
-- plants, petroleum pipes, water from a new offshore pump, poles.
local function oil_processing_suggestion(npc)
  for _,name in ipairs({"oil-refinery","chemical-plant","basic-oil-processing","pipe","small-electric-pole"}) do
    if not recipe_ready(npc,name) then return nil end
  end
  local plastic,sulfur=recipe_ready(npc,"plastic-bar"),recipe_ready(npc,"sulfur")
  if not (plastic or sulfur) then return nil end
  if sulfur and DesignedStart.takes_water("sulfur") and not recipe_ready(npc,"offshore-pump") then return nil end
  local pumpjack=history_entity(npc,"oil-gathering","pumpjack","pumpjack")
  if not pumpjack then return nil end
  -- What of the block stands already: then only the plants of recipes unlocked since are added.
  local standing,grows={},false
  for _,plan in ipairs(npc.design_history or {}) do
    if plan.purpose=="oil-processing" then
      for id,x in pairs(plan.entities or {}) do
        if x.entity and x.entity.valid then standing[id]=x.entity; grows=true end
      end
    end
  end
  local block,why=oil_block(npc,pumpjack.position,(pumpjack.direction%16)/4,true,standing)
  if not block then log("[npc-factions] oil processing: no layout round its pumpjack ("..tostring(why)..")"); return nil end
  local entities,new={},{}
  for _,e in ipairs(block) do
    if not e.standing and e.id~="pumpjack" and e.id~="oil_pole_1" and not e.id:match("^oil_line_pole_") then
      entities[#entities+1]=e
      new[e.id]=true
    end
  end
  local matched=false
  for _,e in ipairs(block) do matched=matched or e.standing==true end
  -- (grown only round a block of this template: beside an oil plant built otherwise, nothing)
  if grows and (not matched or not (new.plastic_plant or new.sulfur_plant)) then return nil end
  local rest={}
  for _,e in ipairs(entities) do if e.id~="brick_furnace" then rest[#rest+1]=e.id end end
  local witnesses={}
  if new.refinery then
    witnesses={{kind="entity_built",entity_id="refinery"},{kind="power_connected",entity_id="refinery"},
      {kind="product_counter_delta",entity_id="refinery",item="petroleum-gas",min_delta=1}}
  end
  if new.plastic_plant then witnesses[#witnesses+1]={kind="product_counter_delta",entity_id="plastic_plant",item="plastic-bar",min_delta=4} end
  if new.sulfur_plant then witnesses[#witnesses+1]={kind="product_counter_delta",entity_id="sulfur_plant",item="sulfur",min_delta=2} end
  local stage={id=grows and "oil-more" or "oil-processing",depends_on={},entity_ids=rest,
    manual_supplies=new.plastic_plant and {{entity_id="plastic_plant",item="coal",slot="input"}} or {},
    witnesses=witnesses}
  if not new.brick_furnace then return {entities=entities,stages={stage}} end
  stage.depends_on={"bricks"}
  return {entities=entities,stages={{id="bricks",depends_on={},entity_ids={"brick_furnace"},
      manual_supplies={{entity_id="brick_furnace",item="coal",slot="fuel",once=true},
        {entity_id="brick_furnace",item="stone",slot="input",once=true}},
      witnesses={{kind="entity_built",entity_id="brick_furnace"},
        {kind="product_counter_delta",entity_id="brick_furnace",item="stone-brick",min_delta=10}}},
    stage}}
end

-- An assembler next to the network making `recipe`, hand-fed with `inputs`.
local function powered_assembler(npc,id,recipe,inputs,min_delta)
  if not (recipe_ready(npc,recipe) and recipe_ready(npc,"assembling-machine-1")
      and recipe_ready(npc,"small-electric-pole")) then return nil end
  local surface,home=npc.character.surface,npc.home
  local poles=surface.find_entities_filtered{position=home,radius=WINDOW,force=npc.force,type="electric-pole"}
  nearest_first(poles,home,function(e) return e.position end)
  local reach=prototypes.entity["small-electric-pole"].get_max_wire_distance()-0.5
  for _,pole in ipairs(poles) do
    if pole.electric_network_id then
      for dx=-6,6 do for dy=-6,6 do
        local spot={x=math.floor(pole.position.x)+dx+0.5,y=math.floor(pole.position.y)+dy+0.5}
        if dx*dx+dy*dy<=reach*reach and in_window(spot,home)
            and land_clear(surface,"small-electric-pole",spot,0,npc) then
          for _,o in ipairs({{2,0},{-2,0},{0,2},{0,-2}}) do
            local machine={x=spot.x+o[1],y=spot.y+o[2]}
            local box=entity_box("assembling-machine-1",machine,0)
            local covers=spot.x>box[1][1] and spot.x<box[2][1] and spot.y>box[1][2] and spot.y<box[2][2]
            if not covers and in_window(machine,home) and land_clear(surface,"assembling-machine-1",machine,0,npc) then
              local supplies={}
              for _,item in ipairs(inputs) do supplies[#supplies+1]={entity_id=id,item=item,slot="input"} end
              return {entities={{id=id,prototype="assembling-machine-1",position=machine,direction=0,recipe=recipe},
                  {id=id.."_pole",prototype="small-electric-pole",position=spot,direction=0}},
                stages={{id=id,depends_on={},entity_ids={id,id.."_pole"},manual_supplies=supplies,
                  witnesses={{kind="entity_built",entity_id=id},{kind="power_connected",entity_id=id},
                    {kind="product_counter_delta",entity_id=id,item=recipe,min_delta=min_delta}}}}}
            end
          end
        end
      end end
    end
  end
end

local function engine_assembly_suggestion(npc)
  return powered_assembler(npc,"engine_assembler","engine-unit",{"steel-plate","iron-gear-wheel","pipe"},2)
end

-- Blue science: an assembler making chemical science packs, inserted into the
-- red science block's lab from a free side, hand-fed with engines, advanced
-- circuits and sulfur.
local function blue_science_suggestion(npc)
  for _,name in ipairs({"assembling-machine-1","inserter","small-electric-pole","chemical-science-pack"}) do
    if not recipe_ready(npc,name) then return nil end
  end
  local lab=history_entity(npc,"red-science-automation","science_lab","lab")
  if not lab then return nil end
  local surface,L=npc.character.surface,lab.position
  local lab_box=entity_box("lab",L,0)
  for _,side in ipairs({{1,0,0,-1},{0,1,1,0},{0,-1,1,0}}) do
    local v,perp={side[1],side[2]},{side[3],side[4]}
    local inserter={x=L.x+2*v[1],y=L.y+2*v[2]}
    local assembler={x=L.x+4*v[1],y=L.y+4*v[2]}
    local pole={x=inserter.x+perp[1],y=inserter.y+perp[2]}
    local ok=land_clear(surface,"inserter",inserter,0,npc)
      and land_clear(surface,"assembling-machine-1",assembler,0,npc)
      and land_clear(surface,"small-electric-pole",pole,0,npc)
    local direction=ok and inserter_direction(inserter,entity_box("assembling-machine-1",assembler,0),lab_box)
    if direction then
      return {entities={{id="blue_assembler",prototype="assembling-machine-1",position=assembler,direction=0,
            recipe="chemical-science-pack"},
          {id="blue_inserter",prototype="inserter",position=inserter,direction=direction},
          {id="blue_pole",prototype="small-electric-pole",position=pole,direction=0}},
        stages={{id="blue-science",depends_on={},entity_ids={"blue_assembler","blue_inserter","blue_pole"},
          manual_supplies={{entity_id="blue_assembler",item="engine-unit",slot="input"},
            {entity_id="blue_assembler",item="advanced-circuit",slot="input"},
            {entity_id="blue_assembler",item="sulfur",slot="input"}},
          witnesses={{kind="entity_built",entity_id="blue_assembler"},
            {kind="power_connected",entity_id="blue_assembler"},
            {kind="product_counter_delta",entity_id="blue_assembler",item="chemical-science-pack",min_delta=2}}}}}
    end
  end
end

-- Blue science supply: an engine assembler and an advanced-circuit assembler
-- next to the blue assembler, each with an inserter into it, so the engineer
-- only hand-feeds bulk intermediates (steel, gears, pipes, plastic, cables,
-- circuits) instead of carrying finished engines and circuits. Each feeder may
-- sit on any free side, shifted along it, with a pole wherever it powers both.
local function blue_supply_suggestion(npc)
  for _,name in ipairs({"engine-unit","advanced-circuit","inserter","small-electric-pole"}) do
    if not recipe_ready(npc,name) then return nil end
  end
  local machine=recipe_ready(npc,"assembling-machine-2") and "assembling-machine-2" or "assembling-machine-1"
  if not recipe_ready(npc,machine) then return nil end
  local blue=history_entity(npc,"blue-science-automation","blue_assembler","assembling-machine-2",
    "chemical-science-pack") or history_entity(npc,"blue-science-automation","blue_assembler","assembling-machine-1",
    "chemical-science-pack")
  if not blue then return nil end
  local surface,B=npc.character.surface,blue.position
  local pole_proto=prototypes.entity["small-electric-pole"]
  local reach=pole_proto.get_max_wire_distance()-0.5
  local supply=pole_proto.get_supply_area_distance()
  local blue_box=entity_box(blue.name,B,0)
  local poles={}
  for _,p in ipairs(surface.find_entities_filtered{position=B,radius=16,force=npc.force,type="electric-pole"}) do
    if p.electric_network_id then poles[#poles+1]={x=p.position.x,y=p.position.y} end
  end
  local planned={}
  local function blocker(name,p)
    local box=entity_box(name,p,0)
    local x0,y0,x1,y1=tile_range(box)
    for x=x0,x1 do for y=y0,y1 do if planned[key(x,y)] then return "planned" end end end
    if land_clear(surface,name,p,0,npc) then return nil end
    for _,e in ipairs(surface.find_entities_filtered{area=box}) do
      if e.type~="tree" and e.type~="simple-entity" and e.type~="resource" and e.type~="character" then return e.name end
    end
    return "terrain"
  end
  local function plan(name,p)
    local x0,y0,x1,y1=tile_range(entity_box(name,p,0))
    for x=x0,x1 do for y=y0,y1 do planned[key(x,y)]=true end end
  end
  local function wired(p)
    for _,q in ipairs(poles) do
      if math.sqrt((p.x-q.x)^2+(p.y-q.y)^2)<=reach then return true end
    end
  end
  -- A pole powering both the inserter and the feeder, wired to the network.
  local function pole_for(inserter,feeder,feeder_box)
    local spots={}
    for dx=-3,3 do for dy=-3,3 do
      local p={x=math.floor(inserter.x)+dx+0.5,y=math.floor(inserter.y)+dy+0.5}
      local on_inserter=math.floor(p.x)==math.floor(inserter.x) and math.floor(p.y)==math.floor(inserter.y)
      local in_feeder=p.x>feeder_box[1][1] and p.x<feeder_box[2][1] and p.y>feeder_box[1][2] and p.y<feeder_box[2][2]
      local covers_inserter=not on_inserter and not in_feeder
        and math.abs(p.x-inserter.x)<=supply and math.abs(p.y-inserter.y)<=supply
      local covers_feeder=feeder_box[1][1]<p.x+supply and p.x-supply<feeder_box[2][1]
        and feeder_box[1][2]<p.y+supply and p.y-supply<feeder_box[2][2]
      if covers_inserter and covers_feeder then spots[#spots+1]=p end
    end end
    nearest_first(spots,inserter,function(q) return q end)
    for _,p in ipairs(spots) do
      if wired(p) and not blocker("small-electric-pole",p) then return p end
    end
  end
  local entities,automates,witnesses,supplies,why={},{},{},{},{}
  local sides={{1,0},{-1,0},{0,1},{0,-1}}
  local long=recipe_ready(npc,"long-handed-inserter")
  -- Where a feeder can go: right beside the blue assembler with an inserter,
  -- or one row further out with a long-handed inserter reaching over, so two
  -- feeders can share a side.
  local function slots(used)
    local out={}
    for index,v in ipairs(sides) do
      local perp={v[2],v[1]}
      if not used[index] then
        -- Off-centre first: it leaves room for a second feeder on the same side.
        for _,k in ipairs({-1,1,0}) do
          out[#out+1]={index=index,name="inserter",
            inserter={x=B.x+2*v[1]+k*perp[1],y=B.y+2*v[2]+k*perp[2]},
            feeder={x=B.x+4*v[1]+k*perp[1],y=B.y+4*v[2]+k*perp[2]}}
        end
      end
    end
    for index,v in ipairs(long and sides or {}) do
      local perp={v[2],v[1]}
      for _,k in ipairs({0,-1,1}) do for _,j in ipairs({0,-1,1}) do
        out[#out+1]={index=index,name="long-handed-inserter",
          inserter={x=B.x+3*v[1]+k*perp[1],y=B.y+3*v[2]+k*perp[2]},
          feeder={x=B.x+6*v[1]+(k+j)*perp[1],y=B.y+6*v[2]+(k+j)*perp[2]}}
      end end
    end
    return out
  end
  local used={}
  for _,f in ipairs({{"engine","engine-unit",{"steel-plate","iron-gear-wheel","pipe"}},
      {"circuit","advanced-circuit",{"plastic-bar","copper-cable","electronic-circuit"}}}) do
    local done=false
    for _,slot in ipairs(slots(used)) do
      if not done then
        local feeder_box=entity_box(machine,slot.feeder,0)
        local blocked=blocker(slot.name,slot.inserter) or blocker(machine,slot.feeder)
        local direction=not blocked and inserter_direction(slot.inserter,feeder_box,blue_box,slot.name)
        local pole=direction and pole_for(slot.inserter,slot.feeder,feeder_box)
        if pole then
          local id=f[1].."_feeder"
          entities[#entities+1]={id=id,prototype=machine,position=slot.feeder,direction=0,recipe=f[2]}
          entities[#entities+1]={id=f[1].."_feed_inserter",prototype=slot.name,position=slot.inserter,direction=direction}
          entities[#entities+1]={id=f[1].."_feed_pole",prototype="small-electric-pole",position=pole,direction=0}
          plan(machine,slot.feeder); plan(slot.name,slot.inserter); plan("small-electric-pole",pole)
          poles[#poles+1]=pole
          for _,item in ipairs(f[3]) do supplies[#supplies+1]={entity_id=id,item=item,slot="input"} end
          automates[#automates+1]={purpose="blue-science-automation",entity_id="blue_assembler",item=f[2]}
          witnesses[#witnesses+1]={kind="entity_built",entity_id=id}
          witnesses[#witnesses+1]={kind="product_counter_delta",entity_id=id,item=f[2],min_delta=1}
          if slot.name=="inserter" then used[slot.index]=true end
          done=true
        elseif slot.name=="inserter" and slot.inserter.x==B.x+2*sides[slot.index][1] and slot.inserter.y==B.y+2*sides[slot.index][2] then
          why[#why+1]=f[1].." side "..slot.index..": "..(blocked or (direction and "no pole" or "no inserter angle"))
        end
      end
    end
    if not done then
      -- No room beside the blue assembler: put this feeder near power anyway;
      -- the engineer carries its output over (still far quicker than crafting by hand).
      for _,pole0 in ipairs(poles) do
        for dx=-6,6 do for dy=-6,6 do
          local spot={x=math.floor(pole0.x)+dx+0.5,y=math.floor(pole0.y)+dy+0.5}
          if not done and math.sqrt(dx*dx+dy*dy)<=reach and not blocker("small-electric-pole",spot) then
            for _,o in ipairs({{2,0},{-2,0},{0,2},{0,-2}}) do
              local feeder={x=spot.x+o[1],y=spot.y+o[2]}
              local fb=entity_box(machine,feeder,0)
              local covers=spot.x>fb[1][1] and spot.x<fb[2][1] and spot.y>fb[1][2] and spot.y<fb[2][2]
              if not done and not covers and not blocker(machine,feeder) then
                local id=f[1].."_feeder"
                entities[#entities+1]={id=id,prototype=machine,position=feeder,direction=0,recipe=f[2]}
                entities[#entities+1]={id=f[1].."_feed_pole",prototype="small-electric-pole",position=spot,direction=0}
                plan(machine,feeder); plan("small-electric-pole",spot)
                for _,item in ipairs(f[3]) do supplies[#supplies+1]={entity_id=id,item=item,slot="input"} end
                witnesses[#witnesses+1]={kind="entity_built",entity_id=id}
                witnesses[#witnesses+1]={kind="product_counter_delta",entity_id=id,item=f[2],min_delta=1}
                done=true
              end
            end
          end
        end end
      end
    end
    if not done then
      if log then
        -- A small map of what surrounds the blue assembler (B = blue, # = other building, ~ = water).
        local rows={}
        for y=math.floor(B.y)-9,math.floor(B.y)+9 do
          local row={}
          for x=math.floor(B.x)-9,math.floor(B.x)+9 do
            local c="."
            local found=surface.find_entities_filtered{area={{x+0.1,y+0.1},{x+0.9,y+0.9}},limit=1,
              type={"assembling-machine","inserter","electric-pole","lab","transport-belt","generator","boiler",
                "furnace","container","pipe","mining-drill","ammo-turret","offshore-pump"}}
            if found[1] then c=found[1]==blue and "B" or found[1].type:sub(1,1) end
            local ok,wet=pcall(function() return surface.get_tile(x,y).collides_with("water_tile") end)
            if ok and wet then c="~" end
            row[#row+1]=c
          end
          rows[#rows+1]=table.concat(row)
        end
        log("[npc-factions] blue supply does not fit: "..table.concat(why,"; ").." long="..tostring(long)
          ..string.char(10)..table.concat(rows,string.char(10)))
      end
      return nil
    end
  end
  local ids={}
  for i,e in ipairs(entities) do ids[i]=e.id end
  return {entities=entities,stages={{id="blue-supply",depends_on={},entity_ids=ids,manual_supplies=supplies,
    witnesses=witnesses,automates=automates}}}
end

-- Defense: two gun turrets with magazines between the factory and whoever
-- attacked or robbed the engineer most recently (else the nearest enemies).
local function threat_point(npc)
  local me="npc:"..npc.id
  for _,e in ipairs(Chronicle.recent(64,{kinds={"attacked","theft","sabotage","killed","threat"},involving=me})) do
    if e.target==me and e.actor then
      local id=tonumber(e.actor:match("^npc:(%d+)$"))
      local other=id and storage.npcs[id]
      if other and other.character and other.character.valid then return other.character.position end
      local index=tonumber(e.actor:match("^player:(%d+)$"))
      local player=index and game.get_player(index)
      if player and player.character and player.character.valid then return player.character.position end
    end
  end
  local ok,enemy=pcall(function()
    return npc.character.surface.find_nearest_enemy{position=npc.home,max_distance=256,force=npc.force}
  end)
  return ok and enemy and enemy.position or nil
end

local function defense_suggestion(npc)
  if not (recipe_ready(npc,"gun-turret") and recipe_ready(npc,"firearm-magazine")) then return nil end
  local surface,home=npc.character.surface,npc.home
  local sx,sy,n=0,0,0
  for _,plan in ipairs(npc.design_history or {}) do
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if e and e.valid then sx,sy,n=sx+e.position.x,sy+e.position.y,n+1 end
    end
  end
  local center=n>0 and {x=math.floor(sx/n),y=math.floor(sy/n)} or {x=math.floor(home.x),y=math.floor(home.y)}
  local threat=threat_point(npc)
  local dx,dy=0,-1
  if threat then
    local tx,ty=threat.x-center.x,threat.y-center.y
    local len=math.sqrt(tx*tx+ty*ty)
    if len>=1 then dx,dy=tx/len,ty/len end
  end
  local aim={x=center.x+10*dx,y=center.y+10*dy}
  local spots={}
  for x=-14,14 do for y=-14,14 do
    local d2=x*x+y*y
    local p={x=center.x+x,y=center.y+y}
    if d2>=36 and d2<=196 and math.abs(p.x-home.x)<=WINDOW-3 and math.abs(p.y-home.y)<=WINDOW-3 then
      spots[#spots+1]=p
    end
  end end
  nearest_first(spots,aim,function(p) return p end)
  local chosen={}
  for _,p in ipairs(spots) do
    local apart=true
    for _,q in ipairs(chosen) do if math.abs(p.x-q.x)<4 and math.abs(p.y-q.y)<4 then apart=false end end
    if apart and land_clear(surface,"gun-turret",p,0,npc) then chosen[#chosen+1]=p end
    if #chosen==2 then break end
  end
  if #chosen<2 then return nil end
  return {entities={{id="turret_1",prototype="gun-turret",position=chosen[1],direction=0},
      {id="turret_2",prototype="gun-turret",position=chosen[2],direction=0}},
    stages={{id="defense",depends_on={},entity_ids={"turret_1","turret_2"},
      manual_supplies={{entity_id="turret_1",item="firearm-magazine",slot="ammo"},
        {entity_id="turret_2",item="firearm-magazine",slot="ammo"}},
      witnesses={{kind="entity_built",entity_id="turret_1"},{kind="entity_built",entity_id="turret_2"},
        {kind="ammo_loaded",entity_id="turret_1",min_delta=5},{kind="ammo_loaded",entity_id="turret_2",min_delta=5}}}}}
end

-- The native layouts, by goal: `repeats`, built again when the base is threatened; `resource`, built again on fresh
-- ore once its drills run out (as catalog_planner.available offers them).
DesignedStart.SUGGESTED={["steam-power"]={make=steam_power_suggestion},
  ["research-lab"]={make=research_lab_suggestion},
  ["red-science-automation"]={make=red_science_suggestion},["plate-belts"]={make=plate_belts_suggestion},
  ["green-science-automation"]={make=green_science_suggestion},
  ["basic-defense"]={make=defense_suggestion,repeats=true},
  ["steel-smelting"]={make=steel_suggestion},
  ["electric-iron-mining"]={make=function(n) return electric_mining_suggestion(n,"iron-ore","iron-plate") end,
    resource="iron-ore"},
  ["electric-copper-mining"]={make=function(n) return electric_mining_suggestion(n,"copper-ore","copper-plate") end,
    resource="copper-ore"},
  ["power-expansion"]={make=power_expansion_suggestion},
  ["oil-gathering"]={make=oil_gathering_suggestion,resource="crude-oil"},
  ["oil-processing"]={make=oil_processing_suggestion,grows=true},
  ["engine-assembly"]={make=engine_assembly_suggestion},
  ["blue-science-automation"]={make=blue_science_suggestion},
  ["blue-supply-automation"]={make=blue_supply_suggestion}}
-- Checks one suggestion may make (about 90 ms of searching). Round a home whose ground is built up every spot fails,
-- and the searches each ran to their end: 30 to 60 s of a frozen game at every plan request, on every peer (the export
-- runs inside an RCON command; OP-3, run 55's bus checkpoint, 2026-10-26). Measured, the opening's use 300 at most;
-- plate belts past it would take 26,000 and more (24 s) to find a way through the bus's furnaces. (Red science and
-- the lab could use about 1000 on a crowded start.)
DesignedStart.SUGGESTION_CHECKS=2000

-- `built`: goals built (completed_purposes): their layouts are not searched again unless they may repeat.
local function suggestions(npc,origin,built)
  local out,done,low={},{},{}
  for _,purpose in ipairs(built or {}) do done[purpose]=true end
  for _,r in ipairs(npc.running_out or {}) do low[r.resource]=true end
  for _,purpose in ipairs(keys(DesignedStart.SUGGESTED)) do
    local s=DesignedStart.SUGGESTED[purpose]
    if not done[purpose] or s.repeats or s.grows or (s.resource and low[s.resource]) then
      npc.checks_left=DesignedStart.SUGGESTION_CHECKS
      local ok,plan=pcall(s.make,npc)
      npc.checks_left=nil
      if not ok and log and plan~="no_room_within_checks" then
        log("[npc-factions] suggestion "..purpose.." failed: "..tostring(plan))
      end
      if ok and plan then
        for _,e in ipairs(plan.entities) do e.position={x=e.position.x-origin.x,y=e.position.y-origin.y} end
        out[purpose]=plan
      end
    end
  end
  return out
end
DesignedStart.suggestions=suggestions

-- Rocks round home: what mining them by hand gives (each item's expected amount over them all, from their prototypes'
-- products), how many give anything, and how far out they were counted. The goal weighing counts them as stone and
-- coal on hand: the opening's stone comes from the rocks round the spawn, not a stone drill (the user, 2026-10-26).
-- The reach is the worker's own search for something to mine (worker.lua, discover: 96 tiles by default). One search
-- by type; each kind of rock's products read once.
DesignedStart.ROCK_REACH=96

-- Items one mining of an item product gives on average: its amount (or the middle of its range), plus the chance of
-- one more (extra_count_fraction), times the chance it is given at all (independent_probability, and the share of the
-- one roll its shared_probability range takes; Factorio 2.0's `probability` where that is all there is).
function DesignedStart.expected_yield(product)
  local amount=product.amount or ((product.amount_min or 1)+math.max(product.amount_max or 0,product.amount_min or 1))/2
  local chance=product.independent_probability or product.probability or 1
  local shared=product.shared_probability
  if type(shared)=="table" and shared.min and shared.max then chance=chance*math.max(0,shared.max-shared.min) end
  return (amount+(product.extra_count_fraction or 0))*chance
end

function DesignedStart.rocks(npc,home)
  local kinds={}
  for _,e in ipairs(npc.character.surface.find_entities_filtered{position=home,radius=DesignedStart.ROCK_REACH,
      type="simple-entity",force="neutral"}) do
    if e.minable then kinds[e.name]=(kinds[e.name] or 0)+1 end   -- (as the worker takes one: worker.lua, yield_item)
  end
  local out,count={},0
  for _,name in ipairs(keys(kinds)) do
    local mine=prototypes.entity[name] and prototypes.entity[name].mineable_properties
    if mine and mine.minable and not mine.required_fluid then
      local gives=false
      for _,product in ipairs(mine.products or {}) do
        if product.type=="item" then
          local each=DesignedStart.expected_yield(product)
          if each>0 then
            out[product.name]=(out[product.name] or 0)+kinds[name]*each
            gives=true
          end
        end
      end
      if gives then count=count+kinds[name] end
    end
  end
  for item,amount in pairs(out) do out[item]=math.floor(amount+0.5) end
  out.count,out.radius=count,DesignedStart.ROCK_REACH
  return out
end

-- A purpose that failed is not offered again for a while (longer after
-- repeated failures), so the goal loop moves on instead of retrying forever.
local FAIL_COOLDOWN=18000
local function note_failure(npc,purpose,tick)
  if not purpose then return end
  npc.design_failures=npc.design_failures or {}
  local f=npc.design_failures[purpose] or {count=0}
  f.count,f.tick=f.count+1,tick
  npc.design_failures[purpose]=f
end

local function failed_purposes(npc,tick)
  local out={}
  for purpose,f in pairs(npc.design_failures or {}) do
    if tick-f.tick<math.min(216000,FAIL_COOLDOWN*2^(f.count-1)) then out[#out+1]=purpose end
  end
  table.sort(out)
  return out
end

-- How often each goal failed in the last FAIL_MEMORY ticks, and how long ago the last time was.
DesignedStart.FAIL_MEMORY=216000
function DesignedStart.failure_counts(npc,tick)
  local out={}
  for purpose,f in pairs(npc.design_failures or {}) do
    if tick-f.tick<DesignedStart.FAIL_MEMORY then out[purpose]={count=f.count,ago=math.floor((tick-f.tick)/60)} end
  end
  return next(out) and out or nil
end

local function completed_purposes(npc)
  local completed,seen={},{}
  for _,plan in ipairs(npc.design_history or {}) do
    if not plan.partial and not seen[plan.purpose] then completed[#completed+1]=plan.purpose; seen[plan.purpose]=true end
  end
  -- Retired builds were superseded (by the bus), not lost: they are not built again.
  for purpose in pairs(npc.retired or {}) do
    if not seen[purpose] then completed[#completed+1]=purpose; seen[purpose]=true end
  end
  table.sort(completed)
  return completed
end

-- The science packs ahead, their research and what each is made of (Progression.tiers; companion/tier_path.py).
function DesignedStart.tiers(npc)
  return try(function() return Progression.tiers(npc.force,npc.character.surface,{recipes=Recipes,
    military_techs=Armory.catalog().techs,team=TeamWorld.active(),
    holder=npc.character.get_main_inventory(),at={npc.home,npc.character.position}}) end)
end

function DesignedStart.export(npc)
  local d=npc.design
  local surface,origin=npc.character.surface,d.origin
  local area={{origin.x,origin.y},{origin.x+2*WINDOW,origin.y+2*WINDOW}}
  local function lp(p) return {x=p.x-origin.x,y=p.y-origin.y} end
  local function lbox(b)
    local lt,rb=b.left_top or b[1],b.right_bottom or b[2]
    return {(lt.x or lt[1])-origin.x,(lt.y or lt[2])-origin.y,(rb.x or rb[1])-origin.x,(rb.y or rb[2])-origin.y}
  end
  local by_resource={}
  for _,e in ipairs(surface.find_entities_filtered{area=area,type="resource"}) do
    local r=by_resource[e.name] or {tiles={},amounts={},amount=0}
    r.tiles[#r.tiles+1]={math.floor(e.position.x)-origin.x,math.floor(e.position.y)-origin.y}
    r.amounts[#r.amounts+1]=e.amount
    r.amount=r.amount+e.amount
    by_resource[e.name]=r
  end
  local resources={}
  for _,name in ipairs(keys(by_resource)) do
    -- The rich part of the patch: drills there last; the thin edge runs out within minutes.
    local r=by_resource[name]
    local sorted={}
    for i,a in ipairs(r.amounts) do sorted[i]=a end
    table.sort(sorted)
    local threshold=math.max(RICH_MIN,math.floor(0.6*(sorted[math.ceil(#sorted/2)] or 0)))
    local rich={}
    for i,t in ipairs(r.tiles) do if r.amounts[i]>=threshold then rich[#rich+1]=t end end
    resources[#resources+1]={resource=name,amount=r.amount,
      tile_count=#r.tiles,rows=rows_from_tiles(r.tiles),rich_rows=rows_from_tiles(rich),rich_threshold=threshold}
  end
  local home,obstacles=npc.home,{}
  local found=surface.find_entities_filtered{area=area,type={"tree","simple-entity","cliff"}}
  nearest_first(found,home,function(e) return e.position end)
  -- what mining its trees and rocks by hand gives (wood, stone...): the planners count it as to be had
  local minable,looked={},{}
  for i=1,math.min(#found,MAX_OBSTACLES) do
    local e=found[i]
    obstacles[#obstacles+1]={name=e.name,type=e.type,box=lbox(e.bounding_box),mineable=e.minable}
    if e.minable and not looked[e.name] then
      looked[e.name]=true
      local mine=e.prototype.mineable_properties
      for _,p in ipairs(mine and mine.products or {}) do if p.type~="fluid" then minable[p.name]=true end end
    end
  end
  local water={}
  for _,t in ipairs(surface.find_tiles_filtered{area=area,collision_mask=WATER}) do
    water[#water+1]={t.position.x-origin.x,t.position.y-origin.y}
  end
  local built={}
  for _,e in ipairs(surface.find_entities_filtered{area=area,force=npc.force}) do
    if e.type~="character" then
      built[#built+1]={name=e.name,unit_number=e.unit_number,position=lp(e.position),
        direction=e.direction,status=e.status,mirror=try(function() return e.mirroring end) or nil}
    end
  end
  local inventory={}
  for _,stack in ipairs(npc.character.get_main_inventory().get_contents()) do
    if stack.quality=="normal" or stack.quality==nil then inventory[stack.name]=(inventory[stack.name] or 0)+stack.count end
  end
  local p=Personality.ensure(npc)
  local personality={traits={},axes={}}
  for i,name in ipairs(p.traits or {}) do personality.traits[i]=name end
  for _,key in ipairs(SHOWN_AXES) do personality.axes[key]=p.axes[key] end
  local completed=completed_purposes(npc)
  local researched={}
  for name,tech in pairs(npc.force.technologies) do if tech.researched then researched[#researched+1]=name end end
  table.sort(researched)
  local coming=DesignedStart.coming(npc.force)
  -- Its blocks, with the ore left at their drills; what is running out is kept for its strategist too.
  local blocks=BaseMemory.blocks(npc,d.origin)
  npc.running_out=BaseMemory.running_out(blocks,30)
  -- The team world: where its planners may build (its plot, or out beyond the team area) and where
  -- players have built (kept clear), in local tiles.
  local team=TeamWorld.bounds(npc)
  if team then
    local function lrect(r) return r and {r[1]-origin.x,r[2]-origin.y,r[3]-origin.x,r[4]-origin.y} or nil end
    local zones={}
    local reach={{home.x-PLAN_REACH,home.y-PLAN_REACH},{home.x+PLAN_REACH,home.y+PLAN_REACH}}
    for i,r in ipairs(TeamWorld.no_build(surface,reach) or {}) do zones[i]=lrect(r) end
    local bands={}
    for i,b in ipairs(team.bands or {}) do
      bands[i]={name=b.name,box=lrect(b.box),corner_lo=b.corner_lo,corner_hi=b.corner_hi}
    end
    team={plot=lrect(team.plot),area=lrect(team.area),no_build=zones,outer=team.outer,bands=bands,
      death_world=team.death_world}   -- (Python's team_area.death_world)
  end
  -- The team world at war: what it can defend with, where the biters broke its wall line, what attacks.
  local defense,breaches,threat_board
  if team then
    local evolution=TeamWar.evolution(surface)
    defense=Armory.defense(npc.force,evolution)
    breaches=TeamWar.breaches(npc,origin)
    threat_board=TeamWar.threat_board(npc,origin,surface)
  end
  return {schema="npc-site-v1",request_id=d.request_id,npc_id=npc.id,npc_name=npc.name,team=team,
    defense=defense,breaches=breaches,threat_board=threat_board,
    world_id=storage.world_id or "unknown",force=npc.force.name,
    surface=surface.name,tick=game.tick,game_version=script.active_mods.base,
    mod_version=script.active_mods["npc-factions"],space_age=script.active_mods["space-age"]~=nil,
    origin={x=origin.x,y=origin.y},bounds={0,0,2*WINDOW,2*WINDOW},
    actor_position=lp(npc.character.position),home=lp(home),goal=d.goal,purpose=d.purpose,
    personality=personality,completed_purposes=completed,failed_purposes=failed_purposes(npc,game.tick),
    failures=DesignedStart.failure_counts(npc,game.tick),   -- (how often each failed lately: the weighing's odds)
    paused=npc.paused_design and npc.paused_design.purpose or nil,   -- set aside for this request (resumes after)
    ask=d.ask,   -- (its strategist asked for this purpose: "now" is built, "next" or "later" weighed with its own goals)
    stalled=npc.stalled,   -- (getting nowhere, by its watchdog: the goal it was on, held by the planner, SO-5)
    reconsider=d.reconsider or nil,   -- (asked because its base was hit: back to the build unless it is urgent)
    yielded=npc.yielded,yields=npc.yields,   -- (a plan handed back over its hand work, and how often each: F-11)
    hand_made=try(function() return DesignedStart.hand_made(npc,game.tick) end),   -- (across plans: W-23)
    under_attack=DesignedStart.attacked(npc,game.tick) or nil,
    repairs=Corrector.summary(npc),power_short=Corrector.power_short(npc,game.tick),
    rebuilt=npc.rebuild_log,   -- (what biters broke lately and how long each stood broken)
    base=blocks,threats=BaseMemory.threats(npc,d.origin),
    pollution=try(function() return BaseMemory.pollution(npc,d.origin) end),   -- (its defense brain)
    guards=try(function() return BaseMemory.guards(npc,d.origin) end),
    time_use=try(function() return DesignedStart.time_use(npc) end),   -- (where its time goes: hand work)
    hand_speed=try(DesignedStart.hand_speed),   -- (how much quicker its hands are than a player's)
    help=npc.help_request and {kind=npc.help_request.kind,player_index=npc.help_request.player_index,
      x=npc.help_request.position.x-d.origin.x,y=npc.help_request.position.y-d.origin.y} or nil,
    base_plan=npc.base_plan,power_rows=npc.power_rows,difficulty=settings.global["npc-factions-difficulty"].value,
    factory_plan=FactoryPlan.export(npc),space_path=FactoryPlan.space_path(Progression.technology_dag(npc.force,"rocket-silo")),
    -- the science packs ahead, their research and what each is made of (companion/tier_path.py)
    tiers=DesignedStart.tiers(npc),
    order=d.order,
    -- what its strategist set on its factory, and what the companion weighs the rest from (priorities.lua)
    priorities=Priorities.list(npc,game.tick),focus=Priorities.focus(npc),
    suggestions=suggestions(npc,origin,completed),social=Character.social_summary(npc,game.tick),
    known_patches=Scouting.patches(npc,area),
    avoid=DesignedStart.Obstacles.avoid(npc,origin),   -- (ground handed back as unbuildable: kept off)
    inventory=inventory,recipes=recipes(npc),machines=machines(npc),researched=researched,coming_recipes=coming,
    research_near=DesignedStart.near(npc.force),
    coal_flow=npc.item_flow and npc.item_flow.coal or nil,
    belt_lines=BaseMemory.lines(npc),   -- (its belt lines, carry and claims: companion/lane_ledger.py)
    logistics=BaseMemory.logistics(npc),   -- (its logistic networks: companion/network_ledger.py, robo_grid.py)
    -- (its cargo landing pad and the packs it brings down, for a belt into the lab row: NPC Factory's input hookup)
    space_pads=Orbit.pads(npc,origin),
    -- (research waiting on packs nothing makes, and which: that pack's line a goal; R-9, lesson 19)
    research_waits=DesignedStart.research_waits(npc),
    -- (what it waits on its machines for, and each plan's hand fuelling: planner inputs, NPC Factory's F-13)
    plate_wait=DesignedStart.export_wait(npc),hand_fuel=DesignedStart.export_hand_fuel(npc),
    inserter_bonus={stack=npc.force.inserter_stack_size_bonus,bulk=npc.force.bulk_inserter_capacity_bonus},
    prototypes=candidate_prototypes(npc),resources=resources,obstacles=obstacles,clearing=Clearing.export(npc),
    minable=keys(minable),
    rocks=try(function() return DesignedStart.rocks(npc,home) end),   -- (stone and coal on hand round home)
    water_rows=rows_from_tiles(water),built=built,
    rules={positions="entity centers in local tiles; 2x2 entities center on tile corners",
      directions="north=0 east=4 south=8 west=12",
      vectors="drop_vector/pickup_vector are for north-facing; rotate for other directions"}}
end

local function plan_npc(request_id)
  for _,npc in pairs(storage.npcs or {}) do
    if npc.design and npc.design.request_id==request_id then return npc end
  end
end

local function reject(reason,request_id) return {ok=false,reason=reason,request_id=request_id,tick=game.tick} end

-- The main bus an engineer is building, stage by stage: where it is, what it is for, and
-- which stages are done. The host plans each stage from this; the game remembers it.
-- The purposes a plan supersedes (names only, bounded).
local function retire_list(raw)
  if type(raw)~="table" then return nil end
  local out={}
  for _,p in ipairs(raw) do
    if type(p)=="string" and #p<=64 and #out<32 then out[#out+1]=p end
  end
  return #out>0 and out or nil
end

-- Map labels a plan leaves for the player it was built for (where to connect inputs and outputs).
local function markers_copy(raw)
  if type(raw)~="table" then return nil end
  local out={}
  for _,m in ipairs(raw) do
    if type(m)=="table" and finite(m.x) and finite(m.y) and type(m.text)=="string" and #out<16 then
      out[#out+1]={x=m.x,y=m.y,text=m.text:sub(1,48)}
    end
  end
  return #out>0 and out or nil
end

-- A boiler row a power plan built or extended (world frame): later power extends it.
local function power_row_copy(raw)
  if type(raw)~="table" then return nil end
  local out={}
  for _,k in ipairs({"wx","wy","turns","mirror","boilers"}) do
    if not finite(raw[k]) then return nil end
    out[k]=raw[k]
  end
  out.belt=raw.belt==true
  -- (boilers both sides of its coal belt: dropped, a double row was extended as a single one, its new boilers' water
  -- pipe four tiles short of the old ones, and the bus's power stood dry for an hour, run 47, 2026-10-26)
  out.double=raw.double==true
  return out
end

-- The plan's ledger block (companion/ledger.py): what kind of segment it is, how busy it is expected to
-- be, its ports and the ground kept for it to grow. Positions arrive export-local and are kept in world
-- coordinates (the next request's window may stand elsewhere).
-- What a block keeps to grow in place later (companion/electric_start.py): where its spot and fields were laid out,
-- as "key=value" strings the way the bus keeps its sites, and the size it grows to (items a second).
-- A defense plan keeps the wall ring it holds (world tiles, x0 y0 x1 y1), which ring that is (the first, the next one
-- farther out) and the side of it the plan is on: the ring moves out as the factory grows, and the sides that moved
-- are taken down by ring and side ("ring:2:north").
function DesignedStart.keep_copy(raw)
  if type(raw)~="table" then return nil end
  local out={sites={},target=finite(raw.target) and math.max(0,raw.target) or nil}
  for _,s in ipairs(type(raw.sites)=="table" and raw.sites or {}) do
    if #out.sites>=32 then break end
    if type(s)=="string" and #s<=96 and s:match("^[%w_%-%.,=]+$") then out.sites[#out.sites+1]=s end
  end
  if #out.sites==0 then out.sites=nil end
  local r=raw.ring
  if type(r)=="table" and finite(r[1]) and finite(r[2]) and finite(r[3]) and finite(r[4]) and r[3]>r[1] and r[4]>r[2]
      and finite(raw.ring_gen) and raw.ring_gen>=1 then
    out.ring={math.floor(r[1]),math.floor(r[2]),math.floor(r[3]),math.floor(r[4])}
    out.ring_gen=math.floor(raw.ring_gen)
    local side=raw.ring_side
    if side=="north" or side=="east" or side=="south" or side=="west" then out.ring_side=side end
    if finite(raw.ring_layer) and raw.ring_layer>=1 and raw.ring_layer<=8 then out.ring_layer=math.floor(raw.ring_layer) end
    -- (its pieces off its own layer: belts and pipes it took under the wall, perimeter.py)
    if finite(raw.extra) and raw.extra>=0 and raw.extra<=4096 then out.extra=math.floor(raw.extra) end
    if finite(raw.ends) and raw.ends>=0 and raw.ends<=4096 then out.ends=math.floor(raw.ends) end
  end
  -- (a turret line's turrets, a defense row's output chests and its item: local tiles, D-19)
  for _,key in ipairs({"turrets","outputs","head","tiles"}) do
    local list={}
    for _,p in ipairs(type(raw[key])=="table" and raw[key] or {}) do
      if #list>=((key=="tiles" or key=="turrets") and 256 or 32) then break end
      if type(p)=="table" and finite(p[1]) and finite(p[2]) then list[#list+1]={p[1],p[2]} end
    end
    if #list>0 then out[key]=list end
  end
  for _,key in ipairs({"item","ammo","source","side"}) do
    local s=raw[key]
    if type(s)=="string" and #s<=64 and s:match("^[%w_%-]+$") then out[key]=s end
  end
  -- (the ring an ammo loop runs round: world tiles, D-19)
  local l=raw.loop
  if type(l)=="table" and finite(l[1]) and finite(l[2]) and finite(l[3]) and finite(l[4]) and l[3]>l[1] and l[4]>l[2] then
    out.loop={math.floor(l[1]),math.floor(l[2]),math.floor(l[3]),math.floor(l[4])}
  end
  return (out.sites or out.target or out.ring or out.turrets or out.outputs or out.head or out.loop) and out or nil
end

local LEDGER_ITEMS=16
local function rates_copy(raw)
  if type(raw)~="table" then return nil end
  local out,n={},0
  for item,rate in pairs(raw) do
    if type(item)=="string" and #item<=64 and finite(rate) and rate>=0 and n<LEDGER_ITEMS then out[item]=rate; n=n+1 end
  end
  return n>0 and out or nil
end

local function ledger_copy(raw,origin)
  if type(raw)~="table" then return nil end
  local out={kind=type(raw.kind)=="string" and raw.kind:sub(1,32) or nil,
    load=finite(raw.load) and math.max(0,math.min(1,raw.load)) or nil,
    makes=rates_copy(raw.makes),takes=rates_copy(raw.takes),
    -- the library build it was made from: its measurements go back to that build's history
    build=type(raw.build)=="string" and #raw.build<=64 and raw.build:match("^[%w_%-]+$") or nil}
  if type(raw.ports)=="table" then
    out.ports={}
    for _,p in ipairs(raw.ports) do
      if #out.ports>=LEDGER_ITEMS then break end
      if type(p)=="table" and type(p.item)=="string" and #p.item<=64 and (p.dir=="in" or p.dir=="out") then
        local placed=finite(p.x) and finite(p.y)
        out.ports[#out.ports+1]={item=p.item,dir=p.dir,rate=finite(p.rate) and math.max(0,p.rate) or nil,
          x=placed and origin.x+p.x or nil,y=placed and origin.y+p.y or nil}
      end
    end
    if #out.ports==0 then out.ports=nil end
  end
  local g=raw.grow
  if type(g)=="table" and finite(g[1]) and finite(g[2]) and finite(g[3]) and finite(g[4]) then
    out.grow={origin.x+g[1],origin.y+g[2],origin.x+g[3],origin.y+g[4]}
  end
  return out
end

local function base_plan_copy(raw)
  if type(raw)~="table" then return nil end
  local out={stages={},packs={}}
  for _,k in ipairs({"x","y","columns","length","spm"}) do
    if not finite(raw[k]) then return nil end
    out[k]=raw[k]
  end
  if type(raw.stage)~="string" then return nil end
  out.stage=raw.stage:sub(1,64)
  if finite(raw.ahead) then out.ahead=raw.ahead end
  if finite(raw.tight) then out.tight=raw.tight end
  if finite(raw.loader_above) then out.loader_above=raw.loader_above end   -- items/s: loaders above it
  if finite(raw.aim) then out.aim=raw.aim end   -- science a minute it grows to (the first bus makes part of it)
  if type(raw.zones)=="table" then   -- (its whole planned ground, a box a stage, kept from other goals)
    for i,z in ipairs(raw.zones) do
      if i<=16 and type(z)=="table" and finite(z[1]) and finite(z[2]) and finite(z[3]) and finite(z[4]) then
        out.zones=out.zones or {}
        out.zones[#out.zones+1]={z[1],z[2],z[3],z[4]}
      end
    end
  end
  for _,k in ipairs({"style","machine"}) do
    if type(raw[k])=="string" then out[k]=raw[k]:sub(1,128) end
  end
  for i,name in ipairs(type(raw.stages)=="table" and raw.stages or {}) do
    if type(name)=="string" and i<=64 then out.stages[#out.stages+1]=name:sub(1,64) end
  end
  for i,name in ipairs(type(raw.packs)=="table" and raw.packs or {}) do
    if type(name)=="string" and i<=8 then out.packs[#out.packs+1]=name:sub(1,64) end
  end
  for _,k in ipairs({"mall","designs","extra","sites"}) do
    if type(raw[k])=="table" then
      out[k]={}
      for i,name in ipairs(raw[k]) do
        if type(name)=="string" and i<=16 then out[k][#out[k]+1]=name:sub(1,64) end
      end
    end
  end
  if type(raw.expand_failed)=="table" then   -- plate -> the tick a resource expansion found no room (tried later)
    local failed,n={},0
    for plate,at in pairs(raw.expand_failed) do
      if type(plate)=="string" and finite(at) and n<8 then failed[plate:sub(1,64)],n=at,n+1 end
    end
    if n>0 then out.expand_failed=failed end
  end
  return out
end
DesignedStart.base_plan_copy=base_plan_copy

local function validate(npc,payload)
  local d=npc.design
  if type(payload.entities)~="table" or type(payload.stages)~="table" then return nil,"plan_shape" end
  -- (a plan that clears ground ahead of later sections holds no buildings)
  if (#payload.entities==0 and not Clearing.clears(payload)) or #payload.entities>MAX_ENTITIES then
    return nil,"entity_count"
  end
  if #payload.stages==0 or #payload.stages>MAX_STAGES then return nil,"stage_count" end
  local entities,order,occupied={},{},{}
  -- Overlapping buildings are resolved, not rejected: an exact duplicate merges into the
  -- first (alias); otherwise belts and poles give way to anything else, and between equals
  -- the first one stays (dropped).
  local ids,tiles_of,alias,dropped,dropped_count={},{},{},{},0
  local function yields(kind) return kind=="transport-belt" and 0 or kind=="electric-pole" and 1 or 2 end
  for _,e in ipairs(payload.entities) do
    if type(e)~="table" or type(e.id)~="string" or #e.id>64 or ids[e.id] then return nil,"entity_id" end
    ids[e.id]=true
    local proto=type(e.prototype)=="string" and prototypes.entity[e.prototype]
    if not proto or not PLACEABLE[proto.type] then return nil,"unsupported_prototype:"..tostring(e.prototype) end
    local item=proto.items_to_place_this and proto.items_to_place_this[1]
    item=item and (item.name or item[1])
    if not item then return nil,"no_placement_item:"..e.prototype end
    local p=e.position
    if type(p)~="table" or not finite(p.x) or not finite(p.y) then return nil,"position:"..e.id end
    -- (the team world: its bands of the yard lie ~600 tiles out; may_build keeps it on its own ground)
    local margin=(TeamWorld.active() and 1024 or PLAN_REACH)-WINDOW
    if p.x<-margin or p.y<-margin or p.x>2*WINDOW+margin or p.y>2*WINDOW+margin then
      return nil,"outside_bounds:"..e.id
    end
    local direction=e.direction or 0
    if not DIRECTIONS[direction] then return nil,"direction:"..e.id end
    if e.recipe~=nil and (type(e.recipe)~="string" or not npc.force.recipes[e.recipe]) then return nil,"recipe:"..e.id end
    local world={x=d.origin.x+p.x,y=d.origin.y+p.y}
    if proto.type=="offshore-pump" and not npc.character.surface.can_place_entity{name=e.prototype,
        position=world,direction=direction,force=npc.force,build_check_type=defines.build_check_type.manual} then
      return nil,"pump_placement:"..e.id
    end
    if proto.type=="mining-drill" then
      local r=proto.get_mining_drill_radius()
      local found=npc.character.surface.count_entities_filtered{type="resource",
        area={{world.x-r,world.y-r},{world.x+r,world.y+r}}}
      if found==0 then return nil,"drill_not_on_resource:"..e.id end
    end
    local box=entity_box(e.prototype,world,direction)
    -- the team world: only in its own plot or out past the team area, and well clear of players' builds
    local allowed,why=TeamWorld.may_build(npc,box)
    if not allowed then return nil,why..":"..e.id end
    local x0,y0,x1,y1=tile_range(box)
    local tiles,clashes,seen_clash,keep={},{},{},true
    for x=x0,x1 do for y=y0,y1 do
      local k=key(x,y); tiles[#tiles+1]=k
      local other=occupied[k]
      if other and not seen_clash[other] then seen_clash[other]=true; clashes[#clashes+1]=other end
    end end
    for _,other in ipairs(clashes) do
      local o=entities[other]
      if o.prototype==e.prototype and o.world.x==world.x and o.world.y==world.y and o.direction==direction
          and (o.mirror==true)==(e.mirror==true) then   -- (a mirrored one is not the same piece: DS-3)
        alias[e.id]=other; keep=false; dropped_count=dropped_count+1; break
      end
      if yields(o.type)>=yields(proto.type) then keep=false end
    end
    if keep then
      for _,other in ipairs(clashes) do
        for _,k in ipairs(tiles_of[other]) do if occupied[k]==other then occupied[k]=nil end end
        entities[other],dropped[other]=nil,e.id
        dropped_count=dropped_count+1
      end
      for _,k in ipairs(tiles) do occupied[k]=e.id end
      tiles_of[e.id]=tiles
      -- An underground belt is placed as an entrance ("input") or exit ("output"); a loader puts into the
      -- building ahead of it ("input") or takes from the one behind it ("output").
      local tunnel=(proto.type=="underground-belt" or proto.type=="loader")
        and (e.belt_to_ground_type=="output" and "output" or "input") or nil
      -- A chest may be limited to its first `bar` slots (the red X players set).
      local bar=(proto.type=="container" or proto.type=="logistic-container") and finite(e.bar) and e.bar>=1
        and math.floor(e.bar) or nil
      -- What each lane of a belt is meant to carry ({left=[items], right=[items]}): the corrector picks off the
      -- rest. Kept by transport line (1 is the left lane), as sets of known items.
      local lanes=nil
      if proto.type=="transport-belt" and type(e.lanes)=="table" then
        for line,side in ipairs({"left","right"}) do
          local list=e.lanes[side]
          if type(list)=="table" then
            local set,n={},0
            for _,name in ipairs(list) do
              if type(name)=="string" and prototypes.item[name] and n<8 then set[name],n=true,n+1 end
            end
            if n>0 then lanes=lanes or {}; lanes[line]=set end
          end
        end
      end
      entities[e.id]={id=e.id,prototype=e.prototype,type=proto.type,item=item,world=world,belt_to_ground_type=tunnel,
        direction=direction,recipe=e.recipe,bar=bar,lanes=lanes,failures=0,mirror=e.mirror==true or nil,
        settings=DesignedStart.settings_copy(e,proto.type)}
      order[#order+1]=e.id
    elseif not alias[e.id] then
      dropped[e.id]=clashes[1]
      dropped_count=dropped_count+1
    end
  end
  do
    local kept={}
    for _,id in ipairs(order) do if entities[id] then kept[#kept+1]=id end end
    order=kept
  end
  -- The entity an id now stands for: itself, the one it duplicated, or nil when it gave way.
  local function resolve(id)
    for _=1,8 do if alias[id] then id=alias[id] else break end end
    return entities[id] and id or nil
  end
  local stages,seen={},{}
  local reach=(TeamWorld.active() and 1024 or PLAN_REACH)-WINDOW
  for _,s in ipairs(payload.stages) do
    if type(s)~="table" or type(s.id)~="string" or seen[s.id] then return nil,"stage_id" end
    local stage={id=s.id,entity_ids={},depends_on={},supplies={},witnesses={},status="pending"}
    for _,dep in ipairs(s.depends_on or {}) do
      if not seen[dep] then return nil,"stage_dependency:"..s.id end
      stage.depends_on[#stage.depends_on+1]=dep
    end
    for _,id in ipairs(s.entity_ids or {}) do
      if not (alias[id] or dropped[id]) then   -- merged or gave way: nothing of its own to build
        if not entities[id] or entities[id].stage then return nil,"stage_entity:"..tostring(id) end
        entities[id].stage=s.id; stage.entity_ids[#stage.entity_ids+1]=id
      end
    end
    for _,m in ipairs(s.manual_supplies or {}) do
      if type(m)~="table" then return nil,"manual_supply:"..s.id end
      if dropped[m.entity_id] then return nil,"overlap:"..tostring(m.entity_id)..":"..dropped[m.entity_id] end
      local id=resolve(m.entity_id)
      if not id or not prototypes.item[m.item or ""]
          or (m.slot~="fuel" and m.slot~="input" and m.slot~="ammo" and m.slot~="robot") then
        return nil,"manual_supply:"..s.id
      end
      -- once: only while this stage is being built (e.g. stone for a brick furnace). Ore, stone or wood carried into
      -- a furnace by hand only starts it, whatever the layout says: a block's furnaces are fed by its drills or belts
      -- (a designed layout kept a "bootstrap" furnace fed iron ore by hand for good, and the engineer mined ore by
      -- hand for it, a fifth of its time, run 35, 2026-10-25).
      local proto=entities[id] and prototypes.entity[entities[id].prototype or ""]
      local bootstrap=m.slot=="input" and RAW[m.item] and proto and proto.type=="furnace"
      -- (`robot`: robots put into a roboport, up to `count`: its network's share, companion/robo_grid.py)
      local robots=m.slot=="robot" and math.max(1,math.min(350,math.floor(tonumber(m.count) or 10))) or nil
      stage.supplies[#stage.supplies+1]={entity_id=id,item=m.item,slot=m.slot,once=m.once==true or bootstrap==true,
        count=robots}
    end
    for _,w in ipairs(s.witnesses or {}) do
      if type(w)~="table" or not w.kind then return nil,"witness:"..s.id end
      if w.entity_id and dropped[w.entity_id] then return nil,"overlap:"..tostring(w.entity_id)..":"..dropped[w.entity_id] end
      local wid=w.entity_id and resolve(w.entity_id)
      if w.entity_id and not wid then return nil,"witness_entity:"..s.id end
      -- A witness proves the machine works; it never waits for a stock (a layout asked 36 plates of its first
      -- furnace before the next four drills went up: 2.4 minutes at a quarter plate a second, run 35).
      stage.witnesses[#stage.witnesses+1]={kind=w.kind,entity_id=wid,item=w.item,
        min_delta=math.max(1,math.min(DesignedStart.WITNESS_MAX,math.floor(tonumber(w.min_delta) or 1))),
        supported=WITNESSES[w.kind]==true}
    end
    -- ground to clear, within the reach a building has (above)
    local unclear=Clearing.stage(s,stage,d.origin,{-reach,2*WINDOW+reach})
    if unclear then return nil,unclear..":"..s.id end
    for _,op in ipairs(type(s.line_ops)=="table" and s.line_ops or {}) do
      local kept=DesignedStart.line_op(op)
      if kept then stage.line_ops=stage.line_ops or {}; stage.line_ops[#stage.line_ops+1]=kept end
    end
    for _,a in ipairs(s.automates or {}) do
      if type(a)=="table" and type(a.purpose)=="string" and type(a.entity_id)=="string" and type(a.item)=="string" then
        stage.automates=stage.automates or {}
        stage.automates[#stage.automates+1]={purpose=a.purpose,entity_id=a.entity_id,item=a.item}
      end
    end
    seen[s.id]=true; stages[#stages+1]=stage
  end
  for _,id in ipairs(order) do if not entities[id].stage then return nil,"entity_without_stage:"..id end end
  return {plan_id=type(payload.plan_id)=="string" and payload.plan_id or d.request_id,
    purpose=type(payload.purpose)=="string" and payload.purpose:sub(1,64) or d.purpose,
    source=type(payload.source)=="string" and payload.source:sub(1,64) or "unknown",
    entities=entities,order=order,stages=stages,accepted_tick=game.tick,dropped=dropped_count,
    base_plan=base_plan_copy(payload.base_plan),retire=retire_list(payload.retire),
    power_row=power_row_copy(payload.power_row),markers=markers_copy(payload.markers),
    ledger=ledger_copy(payload.ledger,d.origin),clearing=Clearing.plan_copy(payload.clearing,d.origin),
    keep=DesignedStart.keep_copy(payload.keep),
    -- (ticks of hand work for its own items before it hands back to what would make them by machine: given only when
    -- something could, companion/plan_hold.py)
    hand_hold=tonumber(payload.hand_hold) and math.max(600,math.min(216000,math.floor(tonumber(payload.hand_hold))))
      or nil,hand_items=DesignedStart.item_set(payload.hand_items),
    hand_clear=DesignedStart.item_set(payload.hand_clear)}   -- (what it makes of what was made by hand: W-23)
end

function DesignedStart.submit(payload)
  if type(payload)~="table" or type(payload.request_id)~="string" then return reject("request_id") end
  local npc=plan_npc(payload.request_id)
  if not npc then return reject("unknown_request",payload.request_id) end
  local d=npc.design
  -- A plan that took longer than the engineer waited is still welcome, if nothing new was asked since:
  -- planning a big base can take minutes, and asking again would only start it over.
  local late=d.status=="deferred" and d.reason=="designer_timeout" and d.request_id==payload.request_id
  if d.status~="waiting" and not late then return reject("not_waiting:"..d.status,payload.request_id) end
  if not (npc.character and npc.character.valid) then return reject("actor_dead",payload.request_id) end
  local plan,reason=validate(npc,payload.plan or {})
  if not plan then
    d.rejections=(d.rejections or 0)+1; d.last_rejection=reason
    return reject(reason,payload.request_id)
  end
  d.plan,d.status,d.stage_index,d.purpose,d.export_cache=plan,"building",1,plan.purpose,nil
  npc.phase="designed-start:building"
  pcall(DesignedStart.Holograms.place,npc,plan)   -- (its whole layout as a hologram, filled in piece by piece)
  return {ok=true,accepted=true,request_id=payload.request_id,plan_id=plan.plan_id,tick=game.tick,
    overlaps_resolved=plan.dropped}
end

-- Designed start, not a fixture: its plans are its factory, and a first plan failing asks again in a while (W-31).
function DesignedStart.designed_mode()
  local s=settings and settings.global and settings.global["npc-factions-start-mode"]
  return not (storage and storage.test) and s~=nil and s.value=="designed"
end

local function fall_back(npc,reason)
  local d=npc.design
  if DesignedStart.designed_mode() then
    -- (a designed engineer never starts the scripted chain: it waited between goals for good after its first plan
    -- failed; its next goal in a while instead, and the strategist told, SO-5's audit: W-31)
    d.status,d.reason,d.export_cache,d.next_goal_tick="deferred",reason,nil,game.tick+7200
    npc.phase="designed:deferred:"..reason
    pcall(Agent.event,npc,{kind="plan_failed",purpose=d.purpose,reason=reason})
    return
  end
  d.status,d.reason,d.export_cache="fallback",reason,nil
  npc.phase="designed-start-fallback:"..reason
end

function DesignedStart.fail(payload)
  local npc=type(payload)=="table" and plan_npc(payload.request_id)
  if not npc then return reject("unknown_request") end
  if npc.design.status~="waiting" then return reject("not_waiting:"..npc.design.status,payload.request_id) end
  -- the goal the planner failed at (a request for a goal of its own choosing says "choose": the same goal,
  -- never noted, was chosen and failed again and again, one-engineer run 2026-10-24)
  local purpose=type(payload.purpose)=="string" and payload.purpose~="" and payload.purpose:sub(1,64) or npc.design.purpose
  -- failed so often for one reason that its strategist thinks again: another site, layout, or none (SO-4)
  if tonumber(payload.escalate) and Agent and Agent.event then
    Agent.event(npc,{kind="goal_stuck",goal=purpose,why=tostring(payload.reason or "failed"):sub(1,120),
      times=math.floor(tonumber(payload.escalate))})
  end
  if npc.design_history and #npc.design_history>0 then
    note_failure(npc,purpose,game.tick)
    return DesignedStart.defer({request_id=payload.request_id,reason="designer:"..tostring(payload.reason or "failed"),
      ticks=7200})
  end
  fall_back(npc,"designer:"..tostring(payload.reason or "failed"):sub(1,80))
  return {ok=true,request_id=payload.request_id,status=npc.design.status,tick=game.tick}
end

-- The host found no room at this site but knows a patch elsewhere: plan
-- there instead. The request gets a new id so the host treats it as new.
function DesignedStart.move_site(payload)
  local npc=type(payload)=="table" and plan_npc(payload.request_id)
  if not npc then return reject("unknown_request") end
  local d=npc.design
  if d.status~="waiting" then return reject("not_waiting:"..d.status,payload.request_id) end
  local dx,dy=tonumber(payload.dx),tonumber(payload.dy)
  if not (finite(dx) and finite(dy)) or math.abs(dx)>256 or math.abs(dy)>256 then
    return reject("site_offset",payload.request_id)
  end
  if (d.site_moves or 0)>=3 then return reject("too_many_site_moves",payload.request_id) end
  -- the team world: a site in its own plot, or out beyond the team area (an outpost); not in another plot
  local cx,cy=math.floor(npc.home.x+dx),math.floor(npc.home.y+dy)
  local allowed,why=TeamWorld.may_build(npc,{{cx-WINDOW,cy-WINDOW},{cx+WINDOW,cy+WINDOW}})
  if not allowed then return reject(why,payload.request_id) end
  local seq=(storage.design_sequence or 0)+1; storage.design_sequence=seq
  d.site_moves=(d.site_moves or 0)+1
  d.request_id,d.requested_tick=npc.id..":"..seq,game.tick
  d.origin={x=math.floor(npc.home.x+dx)-WINDOW,y=math.floor(npc.home.y+dy)-WINDOW}
  if type(payload.purpose)=="string" then d.purpose=payload.purpose:sub(1,64); d.ask=nil end   -- (its planner's pick now)
  if type(payload.goal)=="string" then d.goal=payload.goal:sub(1,600) end
  d.export_cache=nil
  npc.phase="designed:waiting-for-plan:"..tostring(d.purpose)
  return {ok=true,accepted=true,request_id=d.request_id,status="waiting",tick=game.tick}
end

-- The host knows no patch of this resource: go and look for one, then ask again.
function DesignedStart.scout(payload)
  local npc=type(payload)=="table" and plan_npc(payload.request_id)
  if not npc then return reject("unknown_request") end
  local d=npc.design
  if d.status~="waiting" then return reject("not_waiting:"..d.status,payload.request_id) end
  local proto=type(payload.resource)=="string" and prototypes.entity[payload.resource]
  if not (proto and proto.type=="resource") then return reject("resource",payload.request_id) end
  Scouting.start(npc,payload.resource,type(payload.purpose)=="string" and payload.purpose:sub(1,64) or nil,
    WINDOW,game.tick)
  if type(payload.purpose)=="string" then d.purpose=payload.purpose:sub(1,64); d.ask=nil end   -- (its planner's pick)
  d.status,d.reason,d.next_goal_tick="deferred","scouting:"..payload.resource,game.tick+216000
  d.export_cache=nil
  npc.phase="designed:scouting:"..payload.resource
  return {ok=true,accepted=true,request_id=payload.request_id,status="scouting",tick=game.tick}
end

-- The host chose not to build now (e.g. continue the scripted project chain).
function DesignedStart.defer(payload)
  local npc=type(payload)=="table" and plan_npc(payload.request_id)
  if not npc then return reject("unknown_request") end
  local d=npc.design
  if d.status~="waiting" then return reject("not_waiting:"..d.status,payload.request_id) end
  local ticks=math.max(600,math.min(216000,math.floor(tonumber(payload.ticks) or 36000)))
  d.status,d.reason,d.next_goal_tick="deferred",tostring(payload.reason or "deferred"):sub(1,80),game.tick+ticks
  d.export_cache=nil
  npc.phase="designed:deferred:"..d.reason
  -- An urgent plan the planners declined: the one it interrupted carries on, and the strategist hears why.
  if npc.paused_design then Agent.plan_finished(npc,d.purpose,"deferred",d.reason) end
  return {ok=true,request_id=payload.request_id,status="deferred",next_goal_tick=d.next_goal_tick,tick=game.tick}
end

-- A new home: the old base stays standing but is no longer tended (its plans are kept apart, at most
-- three bases back), and the engineer starts over there as it started at first, with what it carries.
function DesignedStart.relocate(npc,at,tick)
  npc.abandoned_bases=npc.abandoned_bases or {}
  table.insert(npc.abandoned_bases,{home=npc.home,history=npc.design_history,tick=tick})
  while #npc.abandoned_bases>3 do table.remove(npc.abandoned_bases,1) end
  npc.home={x=at.x,y=at.y}
  npc.design_history={}
  for _,d in ipairs({npc.design or {},npc.paused_design or {}}) do pcall(DesignedStart.Holograms.clear,d.plan) end
  npc.design,npc.paused_design,npc.base_plan,npc.power_rows,npc.storage_chests=nil,nil,nil,nil,nil
  npc.factory_plan=nil   -- (planned for the old ground)
  DesignedStart.begin(npc,tick,nil,"iron-smelting-start")
end

-- A plan handed straight to the engineer (the proving ground's setup) while it waits for one: the request it replaces
-- is set aside, a new id of its own (its planner may have read the old one), and asked again once the handed plan is
-- done (Agent.plan_finished resumes a paused design). The steam plan handed at the start of death-world-waves-power took
-- the place of its first request, iron-smelting-start, and its opening never smelted iron (W-27). Returns the request
-- set aside, or nil (none waiting, or one set aside already).
function DesignedStart.keep_request(npc,tick)
  local d=npc.design
  if not (d and d.status=="waiting") or npc.paused_design then return nil end
  local kept={}
  for k,v in pairs(d) do kept[k]=v end
  local seq=(storage.design_sequence or 0)+1; storage.design_sequence=seq
  kept.request_id,kept.requested_tick,kept.export_cache=npc.id..":"..seq,tick,nil
  return kept
end

-- `at`/`order`: a build somewhere else (a production block at a player's base): the site is around
-- `at`, and the order (what to make, how fast, for whom) goes to the planner with the site.
function DesignedStart.begin(npc,tick,goal,purpose,at,order)
  local seq=(storage.design_sequence or 0)+1; storage.design_sequence=seq
  local home=at or npc.home
  npc.design={status="waiting",request_id=npc.id..":"..seq,requested_tick=tick,order=order,
    origin={x=math.floor(home.x)-WINDOW,y=math.floor(home.y)-WINDOW},
    purpose=purpose or "iron-smelting-start",
    goal=goal or "Start automatic iron plate production near home using what you carry: "
      .."burner mining drills on iron ore feeding stone furnaces, fueled by hand with coal. "
      .."Stage 1 must use only the 1 burner drill and 1 stone furnace in the inventory; later "
      .."stages may add pairs paid for with plates from stage 1 and hand-mined stone."}
  npc.design_jobs=npc.design_jobs or {}
  npc.phase="designed:waiting-for-plan:"..npc.design.purpose
end

function DesignedStart.wanted(npc)
  return not npc.module and not npc.design and (storage.test and storage.test.mode=="designed" or
    DesignedStart.designed_mode())
end

function DesignedStart.active(npc)
  local d=npc.design
  return d and (d.status=="waiting" or d.status=="building")
end

-- Ownership of receipts: only jobs this module bound are interpreted here.
function DesignedStart.owns(npc,receipt)
  return npc.design_jobs~=nil and npc.design_jobs[receipt.job_id]~=nil
end

function DesignedStart.bind(npc,job_id,meta)
  npc.craft_idle_last=nil   -- (a job of any kind: the stage's idle wait on its crafts starts over)
  npc.design_jobs[job_id]=meta or {}
end

-- The plan cannot be finished: finished stages keep running, the rest is taken back down so no
-- half-built line is left behind, and a new goal is chosen later.
DesignedStart.STANDS={["ammo-turret"]=true,["electric-turret"]=true,["fluid-turret"]=true,["artillery-turret"]=true,
  wall=true,gate=true,radar=true}
local function block_plan(npc,d,reason,tick,yielded)
  pcall(DesignedStart.Holograms.clear,d.plan)   -- (what it never built: no hologram left standing)
  d.status,d.reason=yielded and "deferred" or "blocked",reason
  -- (handed back: a build set aside for it stays aside, so what makes its items is asked for first; it resumes after)
  local paused=yielded and npc.paused_design
  if paused then npc.paused_design=nil end
  Agent.plan_finished(npc,d.purpose,d.status,reason)
  if paused then npc.paused_design=paused end
  npc.phase="designed:"..d.status..":"..d.reason
  -- (a first plan handed back is kept as any other: what stands keeps its plan; Codex, W-31)
  if yielded and DesignedStart.designed_mode() then npc.design_history=npc.design_history or {} end
  if npc.design_history and (#npc.design_history>0 or yielded) then
    if yielded then
      d.next_goal_tick=tick+GOAL_DELAY
    else
      npc.design_blocked=(npc.design_blocked or 0)+1
      d.next_goal_tick=tick+7200
      note_failure(npc,d.purpose,tick)
    end
    Chronicle.record{kind=yielded and "plan_yielded" or "plan_failed",actor="npc:"..npc.id,tick=tick,source="native",
      payload={purpose=d.purpose,plan_id=d.plan.plan_id,reason=d.reason}}
    local kept=false
    npc.design_salvage=npc.design_salvage or {}
    for _,stage in ipairs(d.plan.stages) do
      if stage.status=="complete" then kept=true
      else
        for _,id in ipairs(stage.entity_ids) do
          local e=d.plan.entities[id].entity
          -- (turrets and walls stay standing where they went up: they defend there plan or no plan, and their ammo is
          -- kept up all the same, DesignedStart.defense_supply; picked up, a turret plan dropped in an attack took its
          -- turrets with it)
          if e and e.valid and not d.plan.entities[id].adopted and not DesignedStart.STANDS[e.type] and not yielded then
            table.insert(npc.design_salvage,e)
          elseif e and e.valid and (DesignedStart.STANDS[e.type] or yielded) then
            kept=true   -- (kept as a part plan: what stands is rebuilt if it is lost)
          end
        end
      end
    end
    -- (and what of it was lost and waits to go back, as what stands: a reinforce plan handed back with its turrets all
    -- fallen was dropped, and they were given up as "its plan is gone", W-24)
    for _,r in ipairs(npc.rebuild or {}) do
      local e=r.plan==d.plan and d.plan.entities[r.id]
      local p=e and prototypes.entity[e.prototype]
      if p and (yielded or DesignedStart.STANDS[p.type]) then kept=true end
    end
    if kept then
      d.plan.partial=true
      table.insert(npc.design_history,d.plan)
      while #npc.design_history>MAX_HISTORY do table.remove(npc.design_history,1) end
    end
  elseif DesignedStart.designed_mode() then
    -- (its first plan, blocked: a next goal in a while, as any other's; it stood blocked for good, SO-5's audit: W-31)
    d.next_goal_tick=tick+(yielded and GOAL_DELAY or 7200)
    if not yielded then note_failure(npc,d.purpose,tick) end
  end
end

-- Getting nowhere for long (scripts/watchdog.lua, SO-5): the plan it builds is given up as one that cannot be finished
-- (its finished stages kept), a deferral or block is cut short; one it waits for is left (the companion bounds its
-- planning). True if it acted.
function DesignedStart.unstick(npc,tick,why)
  local d=npc.design
  if not d then return false end
  if d.status=="building" and d.plan then
    block_plan(npc,d,tostring(why or "stalled"):sub(1,80),tick)
    return true
  end
  -- (not one it waits for: a big plan may still come, and a late answer is taken only while it waits)
  if (d.status=="deferred" and d.reason~="designer_timeout") or d.status=="blocked" then   -- (a late plan still taken)
    d.status,d.reason,d.next_goal_tick="deferred",tostring(why or "stalled"):sub(1,80),tick
    d.export_cache=nil
    return true
  end
  return false
end

-- A plan whose own items come only by hand (mined, hand-smelted, crafted) holds the engineer while the goals that
-- would make them by machine never get a turn: one plot-turrets plan took a death world's whole hour, its turrets
-- crafted from hand-smelted iron and power never built (the proving ground, F-11, 2026-10-27). Its hand work is
-- counted; over the budget its planner gave it (only when something could make them) and with no attack on, it hands
-- back: what stands stays, no failure is noted, and the next goal comes at once (the planner's: what makes them).
DesignedStart.HAND_GAP=3600       -- (a longer wait on its hand furnace between two calls counts this much at most)
DesignedStart.HAND_KINDS={mine=true,craft=true}   -- (hand work besides its own items: mined or crafted, not carried)
DesignedStart.ATTACK_CALM=3600    -- (a minute without a hit: the attack is over, as war.lua's OVER_AFTER)
-- What is made by hand over and over is counted across plans too (npc.hand_made, per item): under steady attack each
-- reinforce plan finished within its own budget, the next began at nought, and turrets and piercing rounds were made
-- by hand for hours while a row for them could be built (normal-7b, NPC 4, W-23). An item's count is forgotten once it
-- has not been made by hand for HAND_MADE_FOR, and cleared when its plan hands back or a plan to make it comes.
DesignedStart.HAND_MADE_FOR=30*3600
DesignedStart.HAND_MADE_MAX=32
function DesignedStart.hand_add(d,item,dt,npc)
  if not (item and dt and dt>0) then return end
  if npc then
    npc.hand_made=npc.hand_made or {}
    local e=npc.hand_made[item]
    if not e then
      local n=0
      for _ in pairs(npc.hand_made) do n=n+1 end
      if n<DesignedStart.HAND_MADE_MAX then e={ticks=0}; npc.hand_made[item]=e end
    end
    if e then e.ticks=e.ticks+dt; e.last=game.tick end
  end
  local only=d.plan and d.plan.hand_items   -- (what its planner found a machine maker for: those alone)
  if only and not only[item] then return end
  d.hand_ticks=(d.hand_ticks or 0)+dt
  d.hand_items=d.hand_items or {}
  d.hand_items[item]=(d.hand_items[item] or 0)+dt
end

-- Its hand work per item lately ({item=ticks}), the forgotten ones dropped.
function DesignedStart.hand_made(npc,tick)
  local out={}
  for item,e in pairs(npc.hand_made or {}) do
    if tick-(e.last or 0)>DesignedStart.HAND_MADE_FOR then npc.hand_made[item]=nil else out[item]=e.ticks end
  end
  return out
end

function DesignedStart.hand_made_clear(npc,items)
  if not (npc.hand_made and type(items)=="table") then return end
  for item in pairs(items) do npc.hand_made[item]=nil end
end

-- A wait on its own hand smelting (`item`), or nil once it does something else: the time between counts.
function DesignedStart.hand_clock(npc,d,tick,item)
  if d.hand_item and d.hand_at then
    DesignedStart.hand_add(d,d.hand_item,math.max(0,math.min(tick-d.hand_at,DesignedStart.HAND_GAP)),npc)
  end
  d.hand_item,d.hand_at=item,tick
end

-- The proving ground's power given (proving.lua, death-world-waves-power): the plan done, so its witness saw the engine
-- take steam (power_connected), and the engine still standing.
function DesignedStart.proving_power(npc,plan_id)
  for _,plan in ipairs(npc.design_history or {}) do
    if plan.plan_id==plan_id and not plan.partial then
      local e=plan.entities.engine and plan.entities.engine.entity
      return e~=nil and e.valid
    end
  end
  return false
end

-- A wait on its own hand smelting or its crafting queue: the plan may hand back there (only the smelting wait runs the
-- clock; a queued craft is counted once, on its receipt).
function DesignedStart.hand_wait(why)
  return why~=nil and (why:sub(1,9)=="smelting:" or why:sub(1,9)=="crafting:")
end

-- Hand work done while the plan holds the engineer, its time counted: getting one of the plan's items (all of it: mined,
-- smelted, crafted, carried to the furnace), or any other mining or crafting its upkeep asks meanwhile (magazines
-- for its turrets, a kit): run 61 on the proving ground spent 35 to 45 of its 60 minutes on upkeep crafts while one
-- turret plan held it, and no other goal came (F-11, 2026-10-27). Only work done: a job that failed is the failure
-- path's.
function DesignedStart.hand_receipt(npc,meta,receipt)
  local d=npc.design
  if not (d and d.status=="building" and receipt.status=="complete") then return end
  local began,done=DesignedStart.work_ticks(npc,receipt)
  if type(began)~="number" or type(done)~="number" then return end
  if meta and meta.acquire then DesignedStart.hand_add(d,meta.acquire,done-began,npc)
  elseif DesignedStart.HAND_KINDS[receipt.kind or ""] then   -- (charged to what it is for: ore mined for magazines)
    DesignedStart.hand_add(d,(meta and meta.item) or receipt.item or receipt.recipe,done-began,npc)
  end
end

function DesignedStart.attacked(npc,tick)
  return npc.recall~=nil or (npc.alarm~=nil and tick-(npc.alarm.last or 0)<=DesignedStart.ATTACK_CALM)
end

function DesignedStart.item_set(list)
  if type(list)~="table" then return nil end
  local out,n={},0
  for _,item in ipairs(list) do
    if type(item)=="string" and n<32 then out[item:sub(1,64)]=true; n=n+1 end
  end
  return n>0 and out or nil
end

-- Handed back over its hand-work budget (true), or nil to carry on.
-- Its hand work counts with what the same items took by hand in the plans before it (hand_made: W-23).
function DesignedStart.hand_back(npc,d,tick)
  local hold=d.plan and d.plan.hand_hold
  if not hold or DesignedStart.attacked(npc,tick) then return nil end
  local per,total={},0
  for item,t in pairs(d.hand_items or {}) do per[item]=t end
  local made=DesignedStart.hand_made(npc,tick)
  for item in pairs(d.plan.hand_items or {}) do
    if made[item] and made[item]>(per[item] or 0) then per[item]=made[item] end
  end
  for _,t in pairs(per) do total=total+t end
  total=math.max(total,d.hand_ticks or 0)
  if total<hold then return nil end
  d.hand_ticks=total
  local items={}
  for item,t in pairs(per) do items[#items+1]={item=item,ticks=t} end
  table.sort(items,function(a,b) return a.ticks>b.ticks end)
  local names={}
  for i=1,math.min(3,#items) do names[i]=items[i].item end
  npc.yields=npc.yields or {}
  npc.yields[d.purpose]=(npc.yields[d.purpose] or 0)+1
  npc.yielded={purpose=d.purpose,items=names,tick=tick,hand_ticks=d.hand_ticks,count=npc.yields[d.purpose]}
  log("[npc-factions] npc="..npc.id.." hands back "..tostring(d.purpose).." after "
    ..math.floor(d.hand_ticks/60).." s of hand work for "..table.concat(names,", "))
  DesignedStart.hand_made_clear(npc,per)
  block_plan(npc,d,"yielded:"..table.concat(names,","),tick,true)
  return true
end

DesignedStart.Holograms=require("scripts.holograms")
DesignedStart.Hands=require("scripts.hands")
DesignedStart.Robots=require("scripts.robots")
-- (its robots given at its tier, looked at now and then from Factory.tick: W-26)
function DesignedStart.robots_tick(npc,tick) pcall(DesignedStart.Robots.tick,npc,tick) end

-- A blueprint's own settings for a building (companion/blueprint_packs.py): an inserter's pickup and drop vectors
-- (bobinserters' and 2.x's own), a splitter's priorities and filter. Copied only when well formed; applied once placed.
function DesignedStart.vector_ok(v)
  return type(v)=="table" and type(v.x)=="number" and type(v.y)=="number" and v.x==v.x and v.y==v.y
    and math.abs(v.x)<=8 and math.abs(v.y)<=8
end
function DesignedStart.settings_copy(e,ptype)
  local out
  if ptype=="inserter" then
    for _,k in ipairs({"pickup_position","drop_position"}) do
      if DesignedStart.vector_ok(e[k]) then out=out or {}; out[k]={x=e[k].x,y=e[k].y} end
    end
    if type(e.filters)=="table" then   -- (its filters: what it moves, as the blueprint set them)
      local list={}
      for _,f in ipairs(e.filters) do
        local name=type(f)=="table" and (f.name or (f.value and f.value.name)) or nil
        local index=type(f)=="table" and tonumber(f.index) or nil
        if type(name)=="string" and prototypes.item[name] and index and #list<5 then list[#list+1]={index=index,name=name} end
      end
      if #list>0 then out=out or {}; out.filters=list end
    end
    if type(e.use_filters)=="boolean" then out=out or {}; out.use_filters=e.use_filters end
  elseif ptype=="splitter" then
    for _,k in ipairs({"input_priority","output_priority"}) do
      if e[k]=="left" or e[k]=="right" or e[k]=="none" then out=out or {}; out[k]=e[k] end
    end
    local f=type(e.filter)=="table" and e.filter.name or e.filter
    if type(f)=="string" and prototypes.item[f] then out=out or {}; out.filter=f end
  elseif ptype=="logistic-container" then
    -- (a requester's or buffer's asks, as the blueprint has them: sections of {item, count}; Ledger's L-3)
    local raw=type(e.request_filters)=="table" and e.request_filters or nil
    local sections=raw and (raw.sections or (raw[1] and raw[1].filters and raw) or (raw[1] and {{filters=raw}})) or {}
    local asks={}
    for _,sec in ipairs(sections) do
      if #asks>=4 then break end
      local list={}
      for _,f in ipairs(type(sec)=="table" and sec.filters or {}) do
        local name=type(f)=="table" and (f.name or (f.value and f.value.name)) or nil
        local n=type(f)=="table" and tonumber(f.count or f.min) or nil
        -- (asks for normal quality only, as it builds: an ask for another quality or by comparison is left out, not
        -- turned into a different one)
        local q=type(f)=="table" and (f.quality or (f.value and f.value.quality)) or nil
        local cmp=type(f)=="table" and (f.comparator or (f.value and f.value.comparator)) or nil
        local plain=(q==nil or q=="normal") and (cmp==nil or cmp=="=")
        if plain and type(name)=="string" and prototypes.item[name] and n and n>0 and #list<20 then
          list[#list+1]={name=name,count=math.min(math.floor(n),100000)}
        end
      end
      if #list>0 then asks[#asks+1]=list end
    end
    if #asks>0 then out=out or {}; out.requests=asks end
    local from=raw and raw.request_from_buffers
    if from==nil then from=e.request_from_buffers end
    if type(from)=="boolean" then out=out or {}; out.request_from_buffers=from end
  end
  return out
end

function DesignedStart.apply_settings(built,settings)
  if not settings then return end
  local at=built.position
  for _,k in ipairs({"pickup_position","drop_position"}) do
    local v=settings[k]
    if v then pcall(function() built[k]={x=at.x+v.x,y=at.y+v.y} end) end
  end
  for _,f in ipairs(settings.filters or {}) do
    pcall(function() built.set_filter(f.index,{name=f.name}) end)
  end
  -- (its filters on as the blueprint has them; listed filters with no word on it: on)
  local on=settings.use_filters
  if on==nil and settings.filters then on=true end
  if on~=nil then pcall(function() built.use_filters=on end) end
  if settings.input_priority then pcall(function() built.splitter_input_priority=settings.input_priority end) end
  if settings.output_priority then pcall(function() built.splitter_output_priority=settings.output_priority end) end
  if settings.filter then pcall(function() built.splitter_filter={name=settings.filter} end) end
  if settings.requests then   -- (a logistic chest's asks: each section a list of {item, count})
    pcall(function()
      local point=built.get_logistic_point(defines.logistic_member_index.logistic_container)
      if not point then return end
      for _,list in ipairs(settings.requests) do
        local section=point.add_section()
        for i,f in ipairs(list) do section.set_slot(i,{value={name=f.name,quality="normal"},min=f.count}) end
      end
    end)
  end
  if settings.request_from_buffers~=nil then
    pcall(function() built.request_from_buffers=settings.request_from_buffers end)
  end
end

-- A planned building just placed: its recipe and facing, a chest's limit, where a drill drops.
local function settle(npc,entity,built,tick)
  entity.entity=built; entity.built_tick=tick
  -- (in the index of what is lost, at once: one built and lost before the index is made again was never put back)
  if npc.unit_index and built.unit_number and npc.design and npc.design.plan then
    npc.unit_index.units[built.unit_number]={plan=npc.design.plan,id=entity.id}
  end
  if entity.recipe and built.type=="assembling-machine" then
    pcall(function() built.set_recipe(entity.recipe) end)
    -- A machine without a fluid recipe cannot turn: face it the planned way once it has one.
    if entity.direction and built.direction~=entity.direction then
      pcall(function() built.direction=entity.direction end)
    end
  end
  DesignedStart.apply_settings(built,entity.settings)
  DesignedStart.Holograms.settle(entity)   -- (the real building in its hologram's place)
  if entity.bar and (built.type=="container" or built.type=="logistic-container") then
    pcall(function() built.get_inventory(defines.inventory.chest).set_bar(entity.bar+1) end)
  end
  if entity.type=="mining-drill" then
    entity.drop_target="ground"
    for _,t in ipairs(entities_at(built.surface,built.drop_position,{force=npc.force})) do
      if t~=built then entity.drop_target=t.name end
    end
  end
end

-- Where its time goes: each finished job's ticks under what it was for (upkeep's kind, building, its agenda)
-- and what it did (mine coal, carry iron plates), halved every TALLY_HALF so the recent counts most. The
-- measure of the hand work its base asks of it (a run spent most of its time hand-mining coal for its
-- burners, and nothing knew), for its planners to weigh what a build adds or removes of it.
DesignedStart.TALLY_HALF=36000
DesignedStart.TALLY_TAGS=40
-- What the engineer moves of each item, in totals since the game began (the user, 2026-10-25: how much coal does it
-- mine, put away, put into furnaces, use for power): mined by hand, taken from (by what it came out of), put into (by
-- what it went into; surplus put away in its storage chests counted as "storage").
DesignedStart.FLOW_ITEMS=24   -- items followed at most
function DesignedStart.flow(npc,meta,receipt)
  if receipt.status~="complete" or not receipt.item or not (receipt.count and receipt.count>0) then return end
  if receipt.kind=="sweep" then   -- (each stop of it, as a transfer of its own)
    for _,m in ipairs(receipt.moved or {}) do
      DesignedStart.flow(npc,meta,{status="complete",kind="transfer",mode=receipt.mode,item=receipt.item,count=m.count,
        target_type=m.type,tick=receipt.tick})
    end
    return
  end
  local into
  if receipt.kind=="mine" then into="mined"
  elseif receipt.kind=="transfer" then
    if receipt.mode=="withdraw" then into="from:"..tostring(receipt.target_type)
    elseif meta and meta.upkeep=="store" then into="into:storage"
    else into="into:"..tostring(receipt.target_type) end
  else return end
  local all=npc.item_flow or {}
  npc.item_flow=all
  local f=all[receipt.item]
  if not f then
    local n=0
    for _ in pairs(all) do n=n+1 end
    if n>=DesignedStart.FLOW_ITEMS then return end
    f={since=receipt.tick}
    all[receipt.item]=f
  end
  f[into]=(f[into] or 0)+receipt.count
end

-- A job's (start, end) ticks: a craft queued in the background takes no walking time, but its hands are still making it:
-- the time its queue spends on it counts, as what the goals that would make it by machine take over
-- (goal_brain.craft_saved; F-11's hand clock).
function DesignedStart.work_ticks(npc,receipt)
  local done,began=receipt.tick,receipt.started_tick
  if receipt.kind=="craft" and receipt.queued and type(done)=="number" then
    local r=prototypes.recipe[receipt.recipe or ""]
    local c=npc.character
    local speed=(c and c.valid and try(function() return 1+c.character_crafting_speed_modifier
      +c.force.manual_crafting_speed_modifier end)) or 1
    if r then began=done-math.ceil((r.energy or 0.5)*receipt.queued*60/math.max(speed,0.01)) end
  end
  return began,done
end

function DesignedStart.tally(npc,meta,receipt)
  local began,done=DesignedStart.work_ticks(npc,receipt)
  if type(done)~="number" or type(began)~="number" or done<=began then return end
  local what=meta and (meta.upkeep or (meta.build and "build") or (meta.clearing and "clear") or (meta.build_run and "build"))
    or "plan"
  local tag=what..":"..tostring(receipt.kind)
  if receipt.item or receipt.recipe then tag=tag..":"..tostring(receipt.item or receipt.recipe) end
  local waited=math.min(done-began,math.max(0,tonumber(receipt.craft_wait) or 0))   -- (waiting on a queued craft)
  local t=npc.time_use
  if not t or done-t.since>10*DesignedStart.TALLY_HALF or done<t.since then t={since=done,tags={}} end
  for _=1,10 do   -- (at most ten halvings: a longer gap started afresh above)
    if done-t.since<DesignedStart.TALLY_HALF then break end
    for k,v in pairs(t.tags) do t.tags[k]=v/2 end
    t.since=t.since+DesignedStart.TALLY_HALF
  end
  t.tags[tag]=(t.tags[tag] or 0)+(done-began-waited)
  if waited>0 then t.tags["wait:craft"]=(t.tags["wait:craft"] or 0)+waited end
  local n,least,least_key=0,nil,nil
  for k,v in pairs(t.tags) do n=n+1; if not least or v<least then least,least_key=v,k end end
  if n>DesignedStart.TALLY_TAGS and least_key then t.tags[least_key]=nil end
  npc.time_use=t
end

-- A plan being built gets at least BUILD_SHARE of the engineer's time lately, whatever else is asked of it:
-- its strategist's jobs and its upkeep came strictly first, and run 20's turrets stood half built for 11
-- minutes and more while it crafted walls for a stockpile and carried coal (2026-10-25).
DesignedStart.BUILD_SHARE=0.3
DesignedStart.BUILD_TAGS={build=true,plan=true,clear=true}
function DesignedStart.build_starved(npc)
  local d=npc.design
  if not (d and d.status=="building") then return false end
  -- (a round at home before far work goes first, from the moment it is home: cut short, it walks back for the rest;
  -- DesignedStart.leaving)
  if DesignedStart.leaving(npc,game.tick) then return false end
  local t=npc.time_use
  if not t then return false end
  local total,build=0,0
  for k,v in pairs(t.tags) do
    total=total+v
    if DesignedStart.BUILD_TAGS[k:match("^[^:]+") or ""] then build=build+v end
  end
  return total>0 and build/total<DesignedStart.BUILD_SHARE
end

-- Its time lately, as shares of what was tallied (the biggest first), for the export.
function DesignedStart.time_use(npc)
  local t=npc.time_use
  if not t then return nil end
  local total=0
  for _,v in pairs(t.tags) do total=total+v end
  if total<=0 then return nil end
  local out={}
  for k,v in pairs(t.tags) do out[k]=math.floor(v/total*1000+0.5)/1000 end
  return {seconds=math.floor(total/60),shares=out}
end

-- A plan whose own jobs keep failing with no progress between them gives up, whatever the jobs are: a brick
-- block on ground it could not walk to failed 93 build runs in a row, none charged to any building, and stood
-- unbuilt for 50 minutes (run 29, 2026-10-25). Its goal is then set aside (and weighed lower: its odds).
DesignedStart.MAX_STALLED=24
function DesignedStart.stall(npc,d,reason,tick)
  d.stalled=(d.stalled or 0)+1
  d.last_failure=reason
  if d.stalled>=DesignedStart.MAX_STALLED and d.status=="building" then
    block_plan(npc,d,"stalled:"..tostring(reason),tick)
  end
end

-- The obstacle ladder (scripts/obstacles.lua) for a piece of the plan in hand: after it failed for good, or at once when
-- the game's own check finds it blocked by what no blueprint clears.
function DesignedStart.ladder(npc,d,entity,receipt,tick)
  return DesignedStart.Obstacles.resolve(npc,d,entity,receipt,tick,{
    box=entity_box,
    ours=function(e)
      for _,id in ipairs(d.plan.order) do if d.plan.entities[id].entity==e then return true end end
      return false
    end,
    leave_out=DesignedStart.leave_out,
    can_get=function(item) return count(npc,item)>0 or Recipes.ready(npc.force,item) end,
    hand_back=function(reason) block_plan(npc,d,reason,tick) end})
end

function DesignedStart.on_receipt(npc,receipt,tick)
  local d=npc.design
  local meta=npc.design_jobs[receipt.job_id]; npc.design_jobs[receipt.job_id]=nil
  pcall(DesignedStart.tally,npc,meta,receipt)
  pcall(DesignedStart.flow,npc,meta,receipt)
  pcall(DesignedStart.hand_receipt,npc,meta,receipt)
  if receipt.kind=="belt_pickup" then npc.belt_items=nil end   -- (what its belts carry is looked over again)
  -- A building to put down whose item it counted but could not use (gone meanwhile, another quality, one a mod keeps
  -- data in): what it holds of it is no use for building until that changes, and a new one is got, never the same
  -- build tried again (DesignedStart.usable).
  if receipt.status=="failed" and receipt.reason=="missing_healthy_normal_placement_item" and receipt.item then
    npc.unusable=npc.unusable or {}
    npc.unusable[receipt.item]={held=count(npc,receipt.item),tick=tick}
    log("[npc-factions] npc="..npc.id.." holds "..count(npc,receipt.item).." "..receipt.item
      .." it cannot build with: gets another")
  end
  if meta.build_run then   -- every building the run put down, whether or not it got through them all
    local put={}
    for _,p in ipairs(receipt.placed or {}) do
      local entity=d and d.plan and d.plan.entities[p.id]
      if entity and p.entity and p.entity.valid then settle(npc,entity,p.entity,tick); put[p.id]=true end
    end
    -- What a finished run did not put down (blocked, water, out of its reach) goes to the single builder, which clears
    -- the spot or takes it up the ladder (scripts/obstacles.lua); planned into the next run again, it was never tried
    -- and never failed: a pipe's three pieces on a pond, 20 game minutes (the proving ground, 2026-10-27).
    if receipt.status=="complete" and d and d.plan then
      for _,id in ipairs(meta.ids or {}) do
        local entity=d.plan.entities[id]
        if entity and not put[id] then entity.solo=true end
      end
    end
    if d and #(receipt.placed or {})>0 then d.last_progress_tick=tick; d.stalled=nil
    elseif d and receipt.status~="complete" and receipt.reason~="combat_interrupt" then
      DesignedStart.stall(npc,d,receipt.reason,tick)
    end
    return
  end
  if meta.upkeep=="scout" then Scouting.arrived(npc,tick) end
  if meta.upkeep=="kit" and receipt.status~="complete" then npc.kit_next,npc.kit_failed_until=tick+600,tick+600 end
  if meta.upkeep=="repair" then Corrector.on_receipt(npc,meta,receipt,tick) end
  if meta.upkeep=="pickup" then   -- (how its pickups went, counted: the proving ground reads it)
    local p=npc.pickup_stats or {jobs=0,picked=0,passed=0}
    npc.pickup_stats=p
    p.jobs,p.picked,p.passed=p.jobs+1,p.picked+(receipt.picked or 0),p.passed+(receipt.passed or 0)
  end
  if meta.upkeep=="mend" or meta.upkeep=="mend_packs" then   -- (how it mends, counted: the proving ground reads it)
    local m=npc.mend_stats or {jobs=0,hp=0,outcomes={},by_kind={},first={},packs={}}
    npc.mend_stats=m
    if meta.upkeep=="mend_packs" then
      local how=receipt.kind=="craft" and "crafted" or "fetched"
      m.packs[how]=(m.packs[how] or 0)+1
    else
      m.jobs=m.jobs+1
      m.hp=m.hp+(receipt.mended or 0)
      local o=receipt.status..(receipt.reason and (":"..receipt.reason) or "")
      m.outcomes[o]=(m.outcomes[o] or 0)+1
      for k,v in pairs(receipt.mended_by_kind or {}) do m.by_kind[k]=(m.by_kind[k] or 0)+math.floor(v) end
      if receipt.first_kind then m.first[receipt.first_kind]=(m.first[receipt.first_kind] or 0)+1 end
    end
  end
  if meta.upkeep=="mend" and receipt.status=="failed" then   -- (biters there, or packs gone: a while before again)
    npc.mend_next=tick+DesignedStart.Mend.BACK_OFF
  end
  if meta.upkeep=="agent" then Agent.on_receipt(npc,meta,receipt,tick) end
  if meta.upkeep=="rebuild" and meta.plan then
    if receipt.status=="complete" and receipt.entity and receipt.entity.valid
        and not (receipt.already and DesignedStart.owned_elsewhere(npc,meta.plan,receipt.entity)) then
      DesignedStart.rebuilt(npc,meta.plan,meta.entity_id,receipt.entity,tick)   -- (not another plan's: Codex)
    else
      for _,r in ipairs(npc.rebuild or {}) do
        if r.plan==meta.plan and r.id==meta.entity_id and receipt.reason~="combat_interrupt" then
          -- (only the spot's failures spend its tries; another, an item gone say, is a wait: got again next turn)
          local why=tostring(receipt.reason)
          if receipt.blocked_by or DesignedStart.REBUILD_SPOT[why] or why:sub(1,14)=="not_its_ground" then
            r.fails=(r.fails or 0)+1
          end
          DesignedStart.rebuild_wait(npc,r,meta.plan.entities[r.id],"build_failed:"..why,tick)
        end
      end
    end
  end
  if meta.upkeep=="rebuild_run" and meta.entries then   -- every loss the walk put back, whether or not it got to all
    local back={}
    for _,placed in ipairs(receipt.placed or {}) do
      local at=meta.entries[placed.id]
      if at and placed.entity and placed.entity.valid then
        DesignedStart.rebuilt(npc,at.plan,at.id,placed.entity,tick)
        back[placed.id]=true
      end
    end
    -- (one a walk did not put down goes back on its own next, its spot cleared and its failures counted, as a plan's
    -- piece does: planned into the next walk again, it was never tried)
    if receipt.reason~="combat_interrupt" then
      for pid,at in pairs(meta.entries) do
        if not back[pid] then
          for _,r in ipairs(npc.rebuild or {}) do
            if r.plan==at.plan and r.id==at.id then r.solo=true end   -- (its tries are spent on its own build)
          end
        end
      end
    end
  end
  -- A supply that keeps failing is set aside for a while instead of retried forever: the plan's own as well (a
  -- turret's magazine the engineer could not hand over was tried every two seconds for 20 game minutes, and the
  -- turret plan, which supplies before it builds, never went on: run 44, 2026-10-26).
  if (meta.upkeep=="supply" or meta.supply) and meta.item and meta.entity_id then
    npc.supply_backoff=npc.supply_backoff or {}
    local k=meta.entity_id..":"..meta.item
    local b=npc.supply_backoff[k] or {failures=0,until_tick=0}
    if receipt.status=="complete" then b.failures=0
    else
      b.failures=b.failures+1
      if b.failures>=3 then b.until_tick=tick+1800*math.min(8,b.failures-2) end
    end
    npc.supply_backoff[k]=b
    -- (a burner's share of a fuel round, counted off by what went in, a part even of one that failed)
    local round=receipt.mode=="deposit" and npc.fuel_rounds and npc.fuel_rounds[meta.item]
    local key=receipt.target_unit_number
    if round and round.split and key and round.split[key] and (receipt.count or 0)>0 then
      round.split[key]=math.max(0,round.split[key]-receipt.count)
    end
    for _,m in ipairs(round and round.split and receipt.moved or {}) do   -- (a sweep: each burner's share)
      if m.unit_number and round.split[m.unit_number] then
        round.split[m.unit_number]=math.max(0,round.split[m.unit_number]-(m.count or 0))
      end
    end
  end
  -- (a plan's burners fuelled by hand: its cost, for the planner; DesignedStart.note_hand_fuel)
  if (meta.upkeep=="supply" or meta.supply) and meta.slot=="fuel" and meta.purpose
      and (receipt.status=="complete" or (receipt.count or 0)>0) then
    local began,done=DesignedStart.work_ticks(npc,receipt)
    if type(began)=="number" and type(done)=="number" and done>began then
      DesignedStart.note_hand_fuel(npc,meta.purpose,done-began,receipt.mode=="deposit" and (receipt.count or 0) or 0,tick)
    end
  end
  -- (gathering it from trees or rocks failed, none it could get to: from them again in a while; DesignedStart.natural_near)
  local nm=npc.natural_mine
  if receipt.kind=="mine" and receipt.status=="failed" and nm and nm.item==receipt.item then
    npc.natural_backoff=npc.natural_backoff or {}
    npc.natural_backoff[receipt.item]=tick+DesignedStart.NATURAL_BACKOFF
    npc.natural_mine=nil
  end
  -- (a turret it could not load: another one first, for a while)
  -- (broken off on the way, a wave's sight in between: a while, not as long; combat.lua's fence lets it go later)
  if meta.upkeep=="turret_ammo" and meta.target and receipt.status~="complete" then
    npc.turret_backoff=npc.turret_backoff or {}
    npc.turret_backoff[meta.target]=tick+(receipt.reason=="combat_interrupt" and DesignedStart.TURRET_BACKOFF/2
      or DesignedStart.TURRET_BACKOFF)
  end
  if meta.upkeep and receipt.status=="complete" then
    npc.design_upkeep_done=(npc.design_upkeep_done or 0)+1
    if meta.upkeep=="collect" then npc.design_collects=(npc.design_collects or 0)+1 end
    if meta.upkeep=="store" then npc.design_stored=(npc.design_stored or 0)+(receipt.count or 0) end
    if meta.upkeep=="storage_chest" and receipt.entity and receipt.entity.valid then
      npc.storage_chests=npc.storage_chests or {}
      table.insert(npc.storage_chests,receipt.entity)
    end
  end
  if meta.clearing then Clearing.on_receipt(npc,meta,receipt,tick) end
  if not d then return end   -- (upkeep between plans: nothing of a plan to keep count for)
  local entity=meta.entity_id and d.plan and d.plan.entities[meta.entity_id]
  local own=not (meta.upkeep or meta.supply)   -- (the plan's own work: building, fetching for it, clearing)
  if receipt.status=="complete" then
    if meta.clear then d.cleared=(d.cleared or 0)+1 end
    -- (one standing there already, put up by its robots or another hand: taken, unless another plan of its own has it)
    if entity and meta.build and receipt.entity and receipt.entity.valid
        and not (receipt.already and DesignedStart.owned_elsewhere(npc,d.plan,receipt.entity)) then
      settle(npc,entity,receipt.entity,tick)
    end
    d.last_progress_tick=tick
    if own then d.stalled=nil end
  elseif receipt.reason~="combat_interrupt" and receipt.reason~="in_danger" then
    -- (a fight that broke off the work is no failure of it; nor a spot in danger refused before the walk: it waits for
    -- calm, as a fought-over piece does, W-30)
    d.failed_jobs=(d.failed_jobs or 0)+1
    d.last_failure=receipt.reason
    if own then DesignedStart.stall(npc,d,receipt.reason,tick) end
    -- Only a building it cannot get up blocks the plan. Keeping one that stands supplied (ammo, fuel,
    -- ingredients) can fail for a while (what it carried went elsewhere first) and backs off on its own:
    -- a turret's ammo it no longer held blocked a whole turret plan, one-engineer run 2026-10-25.
    if entity and (meta.build or not (entity.entity and entity.entity.valid)) then
      -- (refused for good, a cliff in the way: no use trying again; a plan made after keeps off it)
      entity.failures=receipt.blocked_by and MAX_FAILURES or entity.failures+1
      if entity.failures>=MAX_FAILURES then
        -- Something in the way, or the spot cannot be got to: the ladder (scripts/obstacles.lua) clears it, works round
        -- it, leaves the piece out where the stage shows the block works without it (one spot out of reach threw away a
        -- built electric iron block of 378 buildings, run 35, 2026-10-25), or hands the plan back; every rung recorded.
        if receipt.blocked_by or DesignedStart.Obstacles.IN_THE_WAY[tostring(receipt.reason)] then
          DesignedStart.ladder(npc,d,entity,receipt,tick)
        else
          block_plan(npc,d,"entity_failed:"..entity.id..":"..tostring(receipt.reason),tick)
        end
      end
    end
  end
end

-- Never into a fight: biters, worms or nests this close to where a building goes, and it is not put down now (run 34,
-- 2026-10-25: the engineer walked into a 60-strong wave to put up the turrets meant against it, and died). The
-- rest of the stage goes on meanwhile; with every building left fought over, the plan waits for calm, as a rebuild
-- does (REBUILD_CLEAR).
-- Biters this close; a worm within what it reaches (and a little more); a nest itself only right beside the spot (its
-- guards are biters, counted as such). A nest 30 tiles off kept a death world's wall from ever going up, the user's
-- test world 2026-10-27.
DesignedStart.DANGER=32
DesignedStart.WORM_MARGIN=4
DesignedStart.NEST_NEAR=12
DesignedStart.FOUGHT_WAIT=10800   -- ticks a stage waits on only fought-over pieces before it goes back to be planned again
function DesignedStart.fought_over(npc,position)
  local c=npc.character
  if not (c and c.valid and position) then return false end
  -- (and inside the sight of biters it is keeping clear of, combat.lua: a site 32 tiles off them sent it in, the
  -- fence sent it out, again and again, O-4)
  local Combat=package.loaded["scripts.combat"] or Agent.combat
  if Combat and Combat.in_reach and Combat.in_reach(npc,position) then return true end
  -- (the spot's own danger, as the worker judges every placement it walks to: Hands.in_danger, W-30)
  local H=DesignedStart.Hands
  H.DANGER,H.NEST_NEAR,H.WORM_MARGIN=DesignedStart.DANGER,DesignedStart.NEST_NEAR,DesignedStart.WORM_MARGIN
  return H.in_danger(npc,position)
end

local function stage_entities(d,stage)
  local out={}
  for _,id in ipairs(stage.entity_ids) do
    local e=d.plan.entities[id]
    if not (e.left_out or e.skipped) then out[#out+1]=e end   -- (left out, or not built on purpose: obstacles.lua)
  end
  return out
end

DesignedStart.UNREACHABLE={path_ended_outside_reach=true,no_path=true}
DesignedStart.LEFT_OUT_GRACE=7200   -- ticks a stage with a building left out has to prove it works without it

-- Leaves a building out of its stage (true), unless the stage's witness watches it or cannot tell whether the
-- block works without it (false).
function DesignedStart.leave_out(d,entity,tick)
  local stage
  for _,s in ipairs(d.plan.stages) do
    for _,id in ipairs(s.entity_ids) do if id==entity.id then stage=s end end
  end
  if not stage then return false end
  -- Never a line's piece (a belt, pipe or pole chain with a gap carries nothing): obstacles.lua works round it instead.
  local p=prototypes.entity[entity.prototype]
  if p and DesignedStart.Obstacles.LINE[p.type] then return false end
  -- Only where the stage's witness shows the block works (what it makes, mines, or its power), never where it only sees
  -- buildings stand: there a belt line left with a gap would pass, and carry nothing.
  local proves=false
  for _,w in ipairs(stage.witnesses or {}) do
    if w.entity_id==entity.id then return false end
    if w.kind~="entity_built" then proves=true end
  end
  if not proves then return false end
  entity.left_out=tick
  stage.left_out_tick=stage.left_out_tick or tick
  d.left_out=(d.left_out or 0)+1
  return true
end

-- What the engineer can put down in one go, in walking order from `first`: every building of the stage
-- not built yet whose item it carries (counting as it goes), nearest next. A player lays a belt line
-- or a row of poles in one walk; one job per building had the engineer stop after each.
local RUN_MAX=120   -- (a wall line in one or two walks: the user laid 489 walls in 3.1 min, 2026-10-30)
local function build_run_for(npc,d,stage,first)
  if first.solo then return nil end   -- (one a run could not put down: on its own, DesignedStart.on_receipt)
  local pending,held={},{}
  local c=npc.character
  for _,e in ipairs(stage_entities(d,stage)) do
    if e~=first and not e.solo and not (e.entity and e.entity.valid) and not DesignedStart.fought_over(npc,e.world)
        and DesignedStart.Obstacles.verdict(c.surface,e.prototype,e.world,e.direction,npc.force)~="blocked" then
      pending[#pending+1]=e   -- (a blocked one is the main loop's: the game's check, then the ladder)
    end
  end
  if #pending<2 then return nil end
  local function have(item)
    if held[item]==nil then held[item]=(DesignedStart.usable(npc,item)) end
    return held[item]
  end
  if have(first.item)<1 then return nil end
  held[first.item]=held[first.item]-1
  local out,used,here={first},{},first.world
  while #out<RUN_MAX do
    local best,bd
    for _,e in ipairs(pending) do
      if not used[e] and have(e.item)>0 then
        local dd=(e.world.x-here.x)^2+(e.world.y-here.y)^2
        if not bd or dd<bd then best,bd=e,dd end
      end
    end
    if not best then break end
    used[best]=true; held[best.item]=held[best.item]-1
    out[#out+1]=best; here=best.world
  end
  if #out<3 then return nil end
  local list={}
  for i,e in ipairs(out) do
    list[i]={id=e.id,entity_name=e.prototype,item=e.item,position={x=e.world.x,y=e.world.y},
      direction=e.direction,belt_to_ground_type=e.belt_to_ground_type,mirror=e.mirror}
  end
  return list
end

-- How many of `item` a stage gets at a time, of the `need` it has left: BUILD_BATCH made by hand, but what its own
-- stores hold ready-made taken in one trip, up to its pockets' room with POCKET_SPARE slots kept free (repair packs,
-- ammo, fuel). The user took 306-523 walls a trip (2026-10-30); 50 a trip were 8 trips for 400.
DesignedStart.POCKET_SPARE=4
local owned_output
function DesignedStart.stage_batch(npc,item,need)
  if need<=BUILD_BATCH then return need end
  local _,avail=owned_output(npc,item)
  if not avail or avail<=BUILD_BATCH then return BUILD_BATCH end
  local c=npc.character
  local inv=c and c.valid and c.get_main_inventory()
  local stack=try(function() return prototypes.item[item].stack_size end) or 50
  local room=inv and try(function() return inv.get_insertable_count(item) end) or 0
  room=room-DesignedStart.POCKET_SPARE*stack
  local have=(DesignedStart.usable(npc,item))
  return math.max(BUILD_BATCH,math.min(need,have+avail,have+room))
end

-- The engineer's own building holding the most `item` ready to take: furnace and machine
-- outputs and chests, or chests only (`chests_only`).
owned_output=function(npc,item,chests_only)
  local d,best,amount=npc.design,nil,0
  local candidates,planned={},{}
  local plans={d and d.plan}   -- (no plan at all between some: a player's order done)
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    -- A chest the engineer fills by hand (a bus lane it feeds) is where items go, not where they come from.
    local fed={}
    for _,stage in ipairs(plan.stages or {}) do
      for _,supply in ipairs(stage.supplies or {}) do fed[supply.entity_id]=true end
    end
    for _,id in ipairs(plan.order or {}) do
      local e=plan.entities[id].entity
      if e then planned[e]=true end
      if e and e.valid and not (fed[id] and e.type=="container")
          and (e.type=="container" or not chests_only and (e.type=="furnace" or e.type=="assembling-machine")) then
        candidates[#candidates+1]=e
      end
    end
  end
  for _,chest in ipairs(npc.storage_chests or {}) do
    if chest.valid then candidates[#candidates+1]=chest end
  end
  -- (and any other chest of its own near home; one a plan has since taken over is that plan's, as above)
  for _,chest in ipairs(DesignedStart.home_chests(npc)) do
    if chest.valid and not planned[chest] then candidates[#candidates+1]=chest end
  end
  local smelter=npc.hand_smelter   -- a furnace it put down to smelt by hand, in no plan
  if not chests_only and smelter and smelter.valid then candidates[#candidates+1]=smelter end
  for _,s in ipairs(not chests_only and npc.hand_smelters or {}) do   -- (and the others it smelts in by hand)
    if s.valid and s~=smelter then candidates[#candidates+1]=s end
  end
  local all,listed={},{}
  for _,e in ipairs(candidates) do
    local output=e.type=="container" and e.get_inventory(defines.inventory.chest) or e.get_output_inventory()
    local n=output and output.get_item_count({name=item,quality="normal"}) or 0
    if n>0 and not listed[e] then all[#all+1]={e=e,n=n}; listed[e]=true end   -- (a hand smelter in a plan: once)
    if n>amount then best,amount=e,n end
  end
  return best,amount,all
end
DesignedStart.owned_output=owned_output

-- Every chest of its own within PLAN_REACH of home that no plan of its own fills by hand (a lane or a machine's input
-- chest it feeds is where items go, not where they come from), looked over every HOME_CHESTS_LOOK ticks: a store near
-- home is one whether a plan put it there or not. The silo's parts lay in a chest by home that was in no plan, and its
-- input chests stayed empty (stage-silo, W-22).
DesignedStart.HOME_CHESTS_LOOK=600
function DesignedStart.home_chests(npc)
  -- (its plans: the one going up, one set aside for an urgent one, and those it keeps)
  local plans={npc.design and npc.design.plan or false,npc.paused_design and npc.paused_design.plan or false}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  local home=npc.home
  local c=npc.home_chests
  -- (looked over again in a while, and at once when home moved or its plans changed)
  if c and game.tick>=c.tick and game.tick<c.tick+DesignedStart.HOME_CHESTS_LOOK and home and c.x==home.x and c.y==home.y
      and c.current==plans[1] and c.paused==plans[2] and c.kept==#plans then
    return c.list
  end
  c={tick=game.tick,list={},x=home and home.x,y=home and home.y,current=plans[1],paused=plans[2],kept=#plans}
  npc.home_chests=c
  local ch=npc.character
  if not (ch and ch.valid and home) then return c.list end
  local fed={}
  for _,plan in ipairs(plans) do
    for _,stage in ipairs(plan and plan.stages or {}) do
      for _,supply in ipairs(stage.supplies or {}) do
        local e=plan.entities[supply.entity_id]
        if e and e.entity and e.entity.valid then fed[e.entity]=true end
      end
    end
  end
  for _,e in ipairs(try(function()
      return ch.surface.find_entities_filtered{type="container",force=npc.force,position=npc.home,radius=PLAN_REACH}
    end) or {}) do
    if not fed[e] then c.list[#c.list+1]=e end
  end
  return c.list
end

-- What its own assemblers make (a wall row's walls, an ammo row's magazines), looked over every 10 seconds.
function DesignedStart.machine_made(npc,item)
  local c=npc.machine_made
  if not (c and game.tick>=c.tick and game.tick<c.tick+600) then
    c={tick=game.tick,items={}}
    local plans={npc.design and npc.design.plan}
    for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
    for _,plan in ipairs(plans) do
      for _,id in ipairs(plan and plan.order or {}) do
        local e=plan.entities[id].entity
        if e and e.valid and e.type=="assembling-machine" then
          local r=try(function() return e.get_recipe() end)
          for _,p in ipairs(r and r.products or {}) do if p.type~="fluid" then c.items[p.name]=true end end
        end
      end
    end
    npc.machine_made=c
  end
  return c.items[item]==true
end

-- Its own machine making `item` now (an assembler of its plans set to it, at work): looked at every MACHINE_LOOK ticks.
DesignedStart.MACHINE_LOOK=120
function DesignedStart.machine_making(npc,item)
  local c=npc.machine_making
  if not (c and game.tick>=c.tick and game.tick<c.tick+DesignedStart.MACHINE_LOOK) then
    c={tick=game.tick,items={}}
    local working=defines.entity_status and defines.entity_status.working
    local plans={npc.design and npc.design.plan}
    for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
    for _,plan in ipairs(plans) do
      for _,id in ipairs(plan and plan.order or {}) do
        local e=plan.entities[id].entity
        if e and e.valid and e.type=="assembling-machine" and working and try(function() return e.status end)==working then
          local r=try(function() return e.get_recipe() end)
          for _,p in ipairs(r and r.products or {}) do if p.type~="fluid" then c.items[p.name]=true end end
        end
      end
    end
    npc.machine_making=c
  end
  return c.items[item]==true
end

-- Lesson 15 (the user's play, 2026-10-30: 62 turrets by hand until the turret machine ran, then none): an item its own
-- machine makes now comes from that machine, not its hands. What it has made is taken; with none made yet it waits
-- ("machine_making:<item>", logged), up to MACHINE_WAIT; past that its hands make it after all, as when attacked or on
-- urgent work. Only for what is asked itself (`depth` 0): an ingredient its machines feed on to others never comes free.
-- (its strategist's and its kit's asks, arming up for a fight or a trip, never wait on a machine: the acquire they
-- are given turns it off, npc.machine_wait_off; Codex)
DesignedStart.MACHINE_WAIT=3600
function DesignedStart.machine_wait(npc,item,missing,depth,source,available)
  if npc.machine_wait_off or depth>0 or not DesignedStart.machine_making(npc,item) then return nil end
  local tick=game.tick
  npc.machine_waits=npc.machine_waits or {}
  local w=npc.machine_waits[item]
  -- (its clock runs from the first time, taking what it made or not: an output its inserters drain leaves an empty
  -- trip and the clock goes on, so its hands make it in the end; Codex)
  if w and tick-w.since>DesignedStart.MACHINE_WAIT then
    if tick-w.since>2*DesignedStart.MACHINE_WAIT then npc.machine_waits[item]=nil end   -- (then it may wait again)
    return nil
  end
  if not w then npc.machine_waits[item]={since=tick} end
  if source and (available or 0)>0 then
    return {kind="transfer",target=source,item=item,count=math.min(available,missing),mode="withdraw",up_to=true}
  end
  if DesignedStart.attacked(npc,tick) or (Agent.urgent and Agent.urgent(npc)) then return nil end
  if not (w and w.told) then
    npc.machine_waits[item].told=true
    log("[npc-factions] npc="..npc.id.." "..item.." waits: machine_making:"..item.." (its own machine makes it; not by hand)")
  end
  return nil,"machine_making:"..item
end
-- acquire as its strategist and its kit ask it: never a wait on a machine (above).
function DesignedStart.acquire_now(npc,...)
  npc.machine_wait_off=true   -- (on the engineer, in storage: set and reset within this call)
  local r=table.pack(pcall(DesignedStart.acquire_raw,npc,...))
  npc.machine_wait_off=nil
  if not r[1] then error(r[2],0) end
  return table.unpack(r,2,r.n)   -- (just what acquire returned, no more)
end

-- `item` ready-made from its own chests and machines, or nil while there is none.
function DesignedStart.collect(npc,item,need)
  local source,amount=owned_output(npc,item)
  if source and amount>0 then
    return {kind="transfer",target=source,item=item,count=math.min(amount,need),mode="withdraw",up_to=true}
  end
  return nil
end

-- Burner drills that feed each other (a coal loop) keep the spare in their fuel slots; the engineer
-- takes it, leaving enough that the loop keeps running (and above the refuel mark).
local LOOP_RESERVE=8
local function loop_fuel(npc,item)
  local best,amount=nil,0
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan and plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="mining-drill" and e.drop_target and e.drop_target.valid
          and e.drop_target.type=="mining-drill" then
        local target=e.drop_target
        local fuel=target.get_fuel_inventory()
        local n=fuel and fuel.get_item_count({name=item,quality="normal"})-LOOP_RESERVE or 0
        if n>amount then best,amount=target,n end
      end
    end
  end
  return best,amount
end

-- With nothing smelted anywhere (its smelting starved, or lost to a raid), the engineer smelts by hand
-- as a player does: ore and coal into one of its own furnaces that stands idle (the one it fed last
-- first), then it takes the plates. What smelts from what:
local SMELT_FROM={["iron-plate"]="iron-ore",["copper-plate"]="copper-ore",["stone-brick"]="stone"}
local HAND_SMELT_REACH=48   -- tiles from home a furnace for smelting by hand may stand
DesignedStart.HAND_SMELTERS=4   -- furnaces at work smelting by hand at most
DesignedStart.HAND_WAIT=30      -- seconds the ones at work may take over what is short before one more goes down

-- Ore for one of `item` (its smelting recipe's), 1 if unknown.
function DesignedStart.ore_per(item,ore)
  local n=try(function()
    for _,ing in ipairs(prototypes.recipe[item].ingredients) do if ing.name==ore then return ing.amount end end
  end)
  return type(n)=="number" and n>0 and n or 1
end

-- Whether one more furnace is worth putting down to smelt `short` more of `item` by hand, `busy` at work already.
function DesignedStart.more_smelters(npc,item,short,busy)
  if busy>=DesignedStart.HAND_SMELTERS or #DesignedStart.hand_smelters(npc)>=DesignedStart.HAND_SMELTERS then return false end
  local energy=try(function() return prototypes.recipe[item].energy end)
  return short*(type(energy)=="number" and energy or 3.2)/math.max(1,busy)>DesignedStart.HAND_WAIT
end

-- The furnaces in no plan it smelts in by hand (kept so their output is taken, and their number capped).
function DesignedStart.hand_smelters(npc)
  local out={}
  for _,s in ipairs(npc.hand_smelters or {}) do if s.valid then out[#out+1]=s end end
  npc.hand_smelters=out
  return out
end
function DesignedStart.note_smelter(npc,f)
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan.order or {}) do if plan.entities[id].entity==f then return end end
  end
  local list=DesignedStart.hand_smelters(npc)
  for _,s in ipairs(list) do if s==f then return end end
  list[#list+1]=f
end

-- Returns the furnace to feed (one holding this ore that only lacks fuel first, then an empty one,
-- nearest first), how much of this ore its furnaces are smelting already and how many: as a player feeds
-- several furnaces at once, it feeds another while that is less than it needs.
-- A furnace in a line (fed or emptied by an inserter, or fed by a drill) is its line's, never one to smelt in by
-- hand: stone put into an idle furnace of the bus's iron column came out as bricks on its iron lane, and the
-- bus's modules starved (run 30, 2026-10-25). Looked up once a while per furnace.
DesignedStart.LINE_CHECK=1800
function DesignedStart.in_line(e)
  local seen=storage.npc_line_furnaces or {}
  storage.npc_line_furnaces=seen
  local key=e.unit_number
  local hit=key and seen[key]
  if hit and game.tick-hit.tick<DesignedStart.LINE_CHECK then return hit.line end
  local line=false
  local ok=pcall(function()
    for _,i in ipairs(e.surface.find_entities_filtered{type="inserter",position=e.position,radius=3}) do
      if i.drop_target==e or i.pickup_target==e then line=true return end
    end
    for _,d in ipairs(e.surface.find_entities_filtered{type="mining-drill",position=e.position,radius=4}) do
      if d.drop_target==e then line=true return end
    end
  end)
  if not ok then line=false end
  if key then seen[key]={tick=game.tick,line=line} end
  return line
end

local function idle_furnace(npc,ore,per)
  local c=npc.character.position
  local best,best_score
  local working,busy=0,0
  local seen={}
  per=per or 1
  local function consider(e)
    local fuel=e and e.valid and e.type=="furnace" and not seen[e.unit_number or e] and e.get_fuel_inventory()
    if not fuel then return false end
    seen[e.unit_number or e]=true
    if e~=npc.hand_smelter and DesignedStart.in_line(e) then return false end
    local source=e.get_inventory(defines.inventory.crafter_input)
    local output=e.get_output_inventory()
    local mixed=false
    for _,it in pairs(source and source.get_contents() or {}) do if it.name~=ore then mixed=true end end
    if not (source and output and output.is_empty() and not mixed) then return false end
    local held=source.get_item_count({name=ore,quality="normal"})
    local has_ore=held>0
    -- (smelting this ore already; one holding less than a craft takes, a stone where a brick takes two, smelts nothing
    -- and is one to feed: counted at work, it held its engineer waiting for good, the live game replayed 2026-10-26)
    if held>=per and not fuel.is_empty() then working,busy=working+held,busy+1 return end
    local d=(e.position.x-c.x)^2+(e.position.y-c.y)^2
    local score=(has_ore and 0 or 1e9)+d+(e==npc.hand_smelter and -1e6 or 0)
    if not best or score<best_score then best,best_score=e,score end
  end
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan and plan.order or {}) do consider(plan.entities[id].entity) end
  end
  -- its other furnaces nearby or at home (one it put down to smelt by hand stands in no plan)
  consider(npc.hand_smelter)
  for _,where in ipairs({c,npc.home}) do
    for _,e in ipairs(npc.character.surface.find_entities_filtered{force=npc.force,type="furnace",position=where,
        radius=64,limit=40} or {}) do
      consider(e)
    end
  end
  return best,working,busy
end

-- What of `item` its crafting queue will make (crafts queued in the background, scripts/worker.lua
-- background_craft; not the intermediates the game queued for them, which they use up).
function DesignedStart.queued(npc,item)
  local c=npc.character
  if not (c and c.valid) or (c.crafting_queue_size or 0)==0 then return 0 end
  local n=0
  for _,entry in ipairs(c.crafting_queue or {}) do
    local r=not entry.prerequisite and prototypes.recipe[entry.recipe]
    for _,p in ipairs(r and r.products or {}) do
      if p.name==item and p.type=="item" then n=n+(p.amount or 1)*(entry.count or 0) end
    end
  end
  return n
end

-- How many of `item` it can craft by hand now from what it carries (its recipe's ingredients in hand, one level).
function DesignedStart.makeable(npc,item)
  return try(function()
    local hand=Recipes.hand(npc.character)
    local recipe=Recipes.for_item(npc.force,item,hand)
    if not (recipe and Recipes.made_in(recipe,hand)) then return 0 end
    local batches
    for _,ing in ipairs(recipe.ingredients) do
      if ing.type=="fluid" then return 0 end
      local b=math.floor(count(npc,ing.name)/math.max(1,ing.amount))
      batches=batches and math.min(batches,b) or b
    end
    return (batches or 0)*Recipes.yield(recipe,item)
  end) or 0
end

-- What the research under way unlocks once it is this far along (a burner goal closes for an electric one only
-- then, not for research merely to be had: burner iron closed at power while the electric mining drill was still to
-- research, F-21).
DesignedStart.NEAR=0.5
function DesignedStart.near(force)
  local out={}
  pcall(function()
    local t=force.current_research
    if t and (force.research_progress or 0)>=DesignedStart.NEAR then
      for _,effect in pairs(t.prototype and t.prototype.effects or {}) do
        if effect.type=="unlock-recipe" then out[#out+1]=effect.recipe end
      end
    end
  end)
  return out
end

-- What the research under way and queued will unlock: a goal waiting only on it is coming soon. And what it can
-- research now at little cost (red science alone, COMING_UNITS units at most): research goes to what a goal waits
-- on, so it comes next (eight burner pairs on stone went up while automation, ten red packs, waited only for the
-- electric mining drill's research to finish, and the coal loop could grow no more, run 44, 2026-10-26).
function DesignedStart.coming(force)
  local coming={}
  pcall(function()
    local queue=force.research_queue or {force.current_research}
    for _,tech in pairs(queue) do
      local t=type(tech)=="string" and force.technologies[tech] or tech
      for _,effect in pairs(t and t.prototype and t.prototype.effects or {}) do
        if effect.type=="unlock-recipe" then coming[#coming+1]=effect.recipe end
      end
    end
  end)
  pcall(function()
    for _,t in pairs(force.technologies) do
      local p=t.prototype
      if t.enabled and not t.researched and not p.research_trigger
          and (t.research_unit_count or 0)<=DesignedStart.COMING_UNITS then
        local ready=true
        for _,ing in ipairs(t.research_unit_ingredients or {}) do
          if ing.name~="automation-science-pack" then ready=false end
        end
        for _,pre in pairs(t.prerequisites or {}) do if not pre.researched then ready=false end end
        if ready then
          for _,effect in pairs(p.effects or {}) do
            if effect.type=="unlock-recipe" then coming[#coming+1]=effect.recipe end
          end
        end
      end
    end
  end)
  return coming
end

-- What its machines stock for it, item by item: what its assemblers put into chests (the mall, a wall row) and what
-- its belts fill chests with (the bus's end), looked over every STOCKED_TICKS; once seen, kept.
DesignedStart.STOCKED_TICKS=600
DesignedStart.COMING_UNITS=50   -- research of red science alone this cheap counts as coming (the export)
function DesignedStart.stocked(npc)
  local c=npc.stocked
  if c and game.tick>=c.tick and game.tick<c.tick+DesignedStart.STOCKED_TICKS then return c.items end
  local items=c and c.items or {}
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan and plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="inserter" then
        local drop,pick=e.drop_target,e.pickup_target
        if drop and drop.valid and drop.type=="container" and pick and pick.valid then
          if pick.type=="assembling-machine" then
            local r=try(function() return pick.get_recipe() end)
            for _,p in ipairs(r and r.products or {}) do if p.type=="item" then items[p.name]=true end end
          elseif pick.type=="transport-belt" then
            local inv=drop.get_inventory(defines.inventory.chest)
            for _,it in ipairs(inv and inv.get_contents() or {}) do items[it.name]=true end
          end
        end
      end
    end
  end
  npc.stocked={tick=game.tick,items=items}
  return items
end

-- The engineer's hands are quicker than a player's (the mod setting, 10 times by default: the user, 2026-10-26, so
-- the early game, when everything is made by hand, goes at a good pace), but only for what its machines do not stock
-- for it yet: that is crafted at a player's speed (and fetched instead: acquire), so the boost fades as the base
-- automates. Set by what its crafting queue is making now.
function DesignedStart.hand_speed()
  local s=settings.global["npc-factions-hand-crafting-speed"]
  return s and tonumber(s.value) or 10
end
function DesignedStart.crafting_pace(npc,tick)
  local c=npc.character
  if not (c and c.valid) or (c.crafting_queue_size or 0)==0 then return end
  local head=(c.crafting_queue or {})[1]
  local r=head and prototypes.recipe[head.recipe]
  local stocked=DesignedStart.stocked(npc)
  local slow=false
  for _,p in ipairs(r and r.products or {}) do if stocked[p.name] then slow=true end end
  local want=slow and 0 or DesignedStart.hand_speed()-1
  if c.character_crafting_speed_modifier~=want then c.character_crafting_speed_modifier=want end
end

-- What its own belts carry, item by item, looked over every BELT_INDEX_TICKS while it is asked: each belt holding the
-- item and how many. Once an item is made by machines, the engineer grabs some off the belts for its hand crafting
-- rather than crafting it from plates (the user, 2026-10-25: gears or green circuits, any crafting resource).
DesignedStart.BELT_INDEX_TICKS=600
DesignedStart.GRAB_MIN=5      -- of an item on its belts round one spot (or in a machine) worth fetching instead
DesignedStart.GRAB_REACH=8    -- tiles round that spot whose belts count, and are swept within its reach
function DesignedStart.on_belts(npc,item)
  local idx=npc.belt_items
  if not (idx and game.tick>=idx.tick and game.tick<idx.tick+DesignedStart.BELT_INDEX_TICKS) then
    idx={tick=game.tick,items={}}
    local plans={npc.design and npc.design.plan}
    for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
    -- (what a chest is filled with by hand is on the belts it heads on its way, its plan's and those of the same goal
    -- (a loop's other sides): never a source. The ammo loop's chest was filled, its arm put the magazines on the loop,
    -- and the next supply trip took them off it again; its turrets' arms stood over a bare belt, late-combat, D-24)
    local delivered={}
    -- (a loop set aside counts too: an urgent build's supply trips took its magazines off it; Codex's D-24 review)
    local judged={npc.paused_design and npc.paused_design.plan or false}
    for _,plan in ipairs(plans) do judged[#judged+1]=plan end
    for _,plan in ipairs(judged) do
      for _,stage in ipairs(plan and plan.stages or {}) do
        for _,sup in ipairs(stage.supplies or {}) do
          local t=sup.slot=="input" and plan.entities and plan.entities[sup.entity_id]
          if t and t.entity and t.entity.valid and t.entity.type=="container" then
            local key=tostring(plan.purpose)
            delivered[key]=delivered[key] or {}
            delivered[key][sup.item]=true
          end
        end
      end
    end
    for _,plan in ipairs(plans) do
      local away=plan and delivered[tostring(plan.purpose)] or {}
      for _,id in ipairs(plan and plan.order or {}) do
        local e=plan.entities[id].entity
        if e and e.valid and e.type=="transport-belt" then
          local here={}   -- (only what its hands can take off a belt: DesignedStart.Hands.belt_pickable, W-25)
          for line=1,2 do
            for _,it in ipairs(e.get_transport_line(line).get_contents() or {}) do
              if (it.quality or "normal")=="normal" and DesignedStart.Hands.belt_pickable(it.name)
                  and not away[it.name] then
                here[it.name]=(here[it.name] or 0)+it.count
              end
            end
          end
          for name,n in pairs(here) do
            local list=idx.items[name] or {}
            idx.items[name]=list
            if #list<300 then list[#list+1]={e=e,n=n} end
          end
        end
      end
    end
    npc.belt_items=idx
  end
  local list=idx.items[item]
  if not list then return nil,0 end
  local best,best_n,best_near
  local r2=DesignedStart.GRAB_REACH^2
  for _,a in ipairs(list) do
    if a.e.valid then
      local n,near=0,{}
      for _,b in ipairs(list) do
        if b.e.valid and (a.e.position.x-b.e.position.x)^2+(a.e.position.y-b.e.position.y)^2<=r2 then
          n=n+b.n; near[#near+1]=b.e
        end
      end
      if not best_n or n>best_n then best,best_n,best_near=a.e,n,near end
    end
  end
  return best,best_n or 0,best_near
end

-- Whether `count` of `item` can be made by hand from here, the whole tree looked at before anything is fetched, and how
-- long its hands would take: {seconds, base={[item]=n}} (raw and smelted things, and what its machines make that it
-- has, gathered or taken as they are), or nil and why not (a locked or machine-only ingredient it has none of, a fluid,
-- too deep). One budget for what it carries, so two recipes wanting the same plates count them once. Fetching went
-- depth-first and mined ore for a recipe that could never be finished (the Workers Union review, 2026-10-29; their
-- crafting.lua:177-208 and 294-400, MIT, the idea taken, no code).
DesignedStart.MAKE_DEPTH=5
DesignedStart.MAKE_NODES=200
DesignedStart.MAKE_CACHE=30   -- ticks a tree's answer is kept (acquire asks often, and again for each ingredient)
function DesignedStart.make_plan(npc,item,count)
  local cache=npc.make_cache
  if not cache or game.tick>=cache.tick+DesignedStart.MAKE_CACHE then cache={tick=game.tick,plans={}}; npc.make_cache=cache end
  local key=item..":"..count
  local hit=cache.plans[key]
  if hit then return hit.plan,hit.why end
  local plan,why=DesignedStart.make_plan_now(npc,item,count)
  cache.plans[key]={plan=plan,why=why}
  return plan,why
end
function DesignedStart.make_plan_now(npc,item,count)
  local hand=Recipes.hand(npc.character)
  local carried,base,seconds,nodes={},{},0,0
  local speed=DesignedStart.stocked(npc)[item] and 1 or math.max(1,DesignedStart.hand_speed())
  local why
  local function need(name,n,depth,visiting,top)
    if n<=0 then return true end
    nodes=nodes+1
    if nodes>DesignedStart.MAKE_NODES then why="too_deep:"..item; return false end
    if not top then
      local have=carried[name]
      if have==nil then have=(DesignedStart.usable(npc,name)) end
      local use=math.min(have,n)
      carried[name]=have-use
      n=n-use
      if n<=0 then return true end
      if RAW[name] or SMELTED[name] then base[name]=(base[name] or 0)+n; return true end
    end
    local recipe=Recipes.for_item(npc.force,name,hand)
    if not recipe then why="recipe_locked:"..name; return false end
    if not Recipes.made_in(recipe,hand) then
      -- (made by machines: what its own have of it, or a wait)
      local _,_,outputs=owned_output(npc,name)
      local available=0
      for _,o in ipairs(outputs or {}) do available=available+o.n end   -- (all its outputs: acquire takes from each)
      if not top and available>=n then base[name]=(base[name] or 0)+n; return true end
      why="waiting_for:"..name; return false
    end
    if depth>DesignedStart.MAKE_DEPTH or visiting[name] then why="acquire_depth:"..name; return false end
    visiting[name]=true
    local crafts=math.ceil(n/Recipes.yield(recipe,name))
    seconds=seconds+crafts*(recipe.energy or 0.5)/speed
    for _,ing in ipairs(recipe.ingredients or {}) do
      if ing.type=="fluid" then why="fluid_ingredient:"..name; visiting[name]=nil; return false end
      if not need(ing.name,ing.amount*crafts,depth+1,visiting,false) then visiting[name]=nil; return false end
    end
    visiting[name]=nil
    return true
  end
  if need(item,count,1,{},true) then return {seconds=seconds,base=base} end
  return nil,why
end

-- Fetch it ready-made (a chest, a machine's output, a belt) when that is quicker than its hands make it, by the time each
-- takes: the walk there and back beyond what it reaches, against the plan's seconds for as many (MARGIN quicker, and
-- MIN_SAVING at least); a choice held DECISION ticks. In place of fixed amounts (5 or 10 at least, 2 s recipes).
DesignedStart.FETCH_MARGIN=0.8
DesignedStart.FETCH_MIN_SAVING=1
DesignedStart.FETCH_DECISION=600
function DesignedStart.fetch_worth(npc,item,source,available,missing,plan)
  if not (source and source.valid and available and available>0) then return false end
  if not plan then return true end   -- (its hands cannot make it now: take what there is)
  -- (what its own machines make is taken where it is, as the user asked: "grab gears or green circuits off belts")
  if DesignedStart.machine_made(npc,item) then return true end
  local key=item..":"..tostring(source.unit_number or source.position.x..","..source.position.y)..":"..available..":"..missing
  local memo=npc.fetch_memo and npc.fetch_memo[key]
  if memo and game.tick<memo.until_tick then return memo.fetch end
  local c=npc.character
  local v=(try(function() return c.character_running_speed end) or 0.15)*60
  local d=math.max(0,math.sqrt((source.position.x-c.position.x)^2+(source.position.y-c.position.y)^2)-DesignedStart.reach(npc))
  local fetch_s=2*d/math.max(1,v)+0.25
  local n=math.min(available,missing)
  local craft_s=plan.seconds*n/math.max(1,missing)
  local fetch=fetch_s<=craft_s*DesignedStart.FETCH_MARGIN and craft_s-fetch_s>=DesignedStart.FETCH_MIN_SAVING
  npc.fetch_memo=npc.fetch_memo or {}
  npc.fetch_memo[key]={fetch=fetch,until_tick=game.tick+DesignedStart.FETCH_DECISION}
  for k,m in pairs(npc.fetch_memo) do if game.tick>=m.until_tick then npc.fetch_memo[k]=nil end end   -- (kept small)
  return fetch
end

-- What its hands take to get `missing` of a raw item near where it stands, as make_plan's {seconds,base} for
-- fetch_worth: mined from a tree or rock near (its natural_near rule), or from a patch its drills do not mine; nil when
-- none is near (then any source is worth the trip). Its mining time over its mining speed, a mine per yield, and the walk.
DesignedStart.HAND_SOURCES=64   -- trees, rocks or patch tiles near looked at for it, at the most
-- The resources whose mining gives the item (Obstacles.resource_names).
function DesignedStart.resource_names(item) return DesignedStart.Obstacles.resource_names(item) end
function DesignedStart.hand_plan(npc,item,missing)
  local c=npc.character
  if not (c and c.valid and missing and missing>0) then return nil end
  local speed=(try(function() return c.prototype.mining_speed end) or 0.5)
    *(1+(try(function() return c.force.manual_mining_speed_modifier end) or 0)
      +(try(function() return c.character_mining_speed_modifier end) or 0))   -- (its own boost too: boosts.lua)
  local v=(try(function() return c.character_running_speed end) or 0.15)*60
  -- (what it mines at: the smaller of its reaches, as Worker's mine job)
  local reach=math.min(try(function() return c.resource_reach_distance end) or 2.7,
    try(function() return c.reach_distance end) or 10)
  local function yield(e)
    local mp=e.valid and e.minable and try(function() return e.prototype.mineable_properties end)
    local per=0
    for _,q in ipairs(mp and mp.minable and not mp.required_fluid and mp.products or {}) do
      if q.name==item then per=per+DesignedStart.expected_yield(q) end
    end
    return per,mp
  end
  -- (the nearest first, from one to the next, until enough: a tree or rock mined once, a patch tile as often as it has
  -- units; too few near for it all: no plan)
  local function route(list,mines)
    local at=c.position
    local left={}
    for _,e in ipairs(list) do if e.valid and e.position then left[#left+1]=e end end
    local got,s=0,0
    while #left>0 do
      local k,kd   -- (the nearest to where it stands now)
      for n,o in ipairs(left) do
        local d2=(o.position.x-at.x)^2+(o.position.y-at.y)^2
        if not kd or d2<kd then k,kd=n,d2 end
      end
      local e=table.remove(left,k)
      local per,mp=yield(e)
      if per>0 then
        local d=math.max(0,math.sqrt((e.position.x-at.x)^2+(e.position.y-at.y)^2)-reach)
        local times=mines(e,per)
        local take=math.min(times,math.ceil((missing-got)/per))
        s=s+d/math.max(1,v)+take*(mp.mining_time or 1)/math.max(0.1,speed)
        got,at=got+take*per,e.position
        if got>=missing then return s end
      end
    end
  end
  local best
  if DesignedStart.natural_near(npc,item) then
    best=route(try(function()
      return c.surface.find_entities_filtered{name=DesignedStart.Obstacles.natural_names(item),position=c.position,
        radius=DesignedStart.NATURAL_NEAR,force="neutral",limit=DesignedStart.HAND_SOURCES}
    end) or {},function() return 1 end)
  end
  local patches=DesignedStart.resource_names(item)
  if #patches>0 and DesignedStart.drills_make(npc,item)==0 then
    local s=route(try(function()
      return c.surface.find_entities_filtered{type="resource",name=patches,position=c.position,
        radius=DesignedStart.NATURAL_NEAR,limit=DesignedStart.HAND_SOURCES}
    end) or {},function(e) return math.max(0,math.floor(try(function() return e.amount end) or 0)) end)
    if s and (not best or s<best) then best=s end
  end
  return best and {seconds=best,base={}} or nil
end

-- One job that moves the NPC toward holding `need` of `item`, or a wait reason.
DesignedStart.PLATE_BATCH=100   -- plates fetched at a time from its own furnaces and chests
local function acquire(npc,item,need,depth,by_hand)
  -- (what a build found it could not use does not count: every builder that asks here gets a fresh one, a plan's, a
  -- rebuild's or a repair's alike; DesignedStart.usable)
  local have=(DesignedStart.usable(npc,item))
  if have>=need then return nil end
  -- (being crafted in the background: it comes while the engineer does something else)
  local coming=DesignedStart.queued(npc,item)
  if coming>0 and have+coming>=need then return nil,"crafting:"..item end
  local missing=need-have-coming
  if depth>4 then return nil,"acquire_depth:"..item end
  if RAW[item] then
    -- Prefer the NPC's own supply chests (e.g. a coal-supply layout) over hand mining.
    local chest,available=owned_output(npc,item)
    -- (a far chest of its own, against mining it near by hand: weighed by time; a fuel never, its drills' store comes
    -- first whatever the walk, the user's rule)
    local hand=DesignedStart.hand_plan(npc,item,missing)
    local worth=function(source,n) return DesignedStart.fuel_value(item)>0 or DesignedStart.fetch_worth(npc,item,source,n,missing,hand) end
    if chest and not worth(chest,available) then
      -- (another of its own, nearer, may be: each by its walk, the nearest first)
      local _,_,all=owned_output(npc,item)
      local c=npc.character.position
      local function d2(e) return (e.position.x-c.x)^2+(e.position.y-c.y)^2 end
      table.sort(all or {},function(a,b) return d2(a.e)<d2(b.e) end)
      chest,available=nil,0
      for _,o in ipairs(all or {}) do
        if o.e.type=="container" and worth(o.e,o.n) then chest,available=o.e,o.n; break end
      end
    end
    -- What its coal loop spares, even a few: coal is rarely if ever mined by hand once the loop runs (the user,
    -- 2026-10-25); ten at the least sent it mining whenever the loop had a little less.
    local drill,spare=loop_fuel(npc,item)
    local loop_ok=drill and spare>=math.min(10,missing)
    -- (the store holding more: a chest with a coal or two drew the engineer back and forth between it and the drills
    -- filling it while its coal loop sat on forty, trace 2026-10-26)
    if chest and chest.type=="container" and available>0 and not (loop_ok and spare>available) then
      return {kind="transfer",target=chest,item=item,count=math.min(available,math.max(missing,30)),mode="withdraw",
        up_to=true},nil,"collect"
    end
    if loop_ok then
      return {kind="transfer",target=drill,item=item,count=math.min(spare,math.max(missing,30)),mode="withdraw",
        inventory=defines.inventory.fuel},nil,"collect"
    end
    -- the team world: a teammate's chest before mining by hand (players are not asked for ore)
    local team_job,team_why,team_tag=TeamSupply.take(npc,item,missing,game.tick,false,worth)
    if team_job or team_why then return team_job,team_why,team_tag end
    -- A fuel its own drills fill a store of is never mined by hand, whoever wants it (a burner, a hand smelter, a
    -- plastic plant): it comes from them (the user, 2026-10-26: never coal mined by hand). `by_hand`: the piece that
    -- starts a burner they fuel, gone cold.
    if not by_hand and DesignedStart.fuel_value(item)>0 and DesignedStart.fed_stores(npc).items[item] then
      return nil,"store_filling:"..item
    end
    -- (its drills mine it: never from its patch by hand once they run; from trees or rocks near, as ever)
    if DesignedStart.drills_make(npc,item)>0 then
      if DesignedStart.natural_near(npc,item) then
        npc.natural_mine={item=item,tick=game.tick}
        return {kind="mine",item=item,count=math.max(DesignedStart.NATURAL_BATCH,math.min(50,missing)),natural_only=true}
      end
      DesignedStart.note_wait(npc,item,need,game.tick)
      local r=npc.hand_rescue   -- (a plate its hands smelt meanwhile: its ore with it)
      -- (none of its drills leaves any loose: a dead wait, its hands help soon)
      local dead=(DesignedStart.looked(npc,game.tick).loose[item] or 0)==0
      if not (r and r.item==item and game.tick-r.tick<=DesignedStart.WAIT_GAP)
          and DesignedStart.machines_wait(npc,item,game.tick,dead and DesignedStart.DEAD_WAIT or nil) then
        return nil,"drills_make:"..item
      end
    end
    -- Far from home (e.g. at an oil field) with nothing of this kind around:
    -- walk back toward home, where the engineer knows its patches.
    local c,home=npc.character.position,npc.home
    if (c.x-home.x)^2+(c.y-home.y)^2>32*32 then
      local nearby=item=="wood"
        and npc.character.surface.count_entities_filtered{position=c,radius=64,type="tree",limit=1}
        or npc.character.surface.count_entities_filtered{position=c,radius=64,name=item,limit=1}
      if nearby==0 then return {kind="move",position={x=home.x,y=home.y}},nil,"return_home" end
    end
    -- (from trees and rocks, a trip's worth at the least: one tree gives four, a rock dozens, and orders of one to nine
    -- wood for poles came again and again, long-64's minute 37: its hands' time on wood 16.5%)
    local least=#DesignedStart.Obstacles.natural_names(item)>0 and DesignedStart.NATURAL_BATCH or 1
    return {kind="mine",item=item,count=math.max(least,math.min(50,missing))}
  end
  if SMELTED[item] then
    local furnace,available,outputs=owned_output(npc,item)
    -- (far off, a furnace holding less than it needs is no trip yet: it walked home from far work for five bricks a
    -- wall while the furnace made more, the user's live game replayed 2026-10-26)
    local c=npc.character
    if furnace and available<missing and c and c.valid
        and (furnace.position.x-c.position.x)^2+(furnace.position.y-c.position.y)^2>DesignedStart.far(npc)^2 then
      -- (while it still has ore to smelt: one done is taken from, whatever it holds)
      local ore=SMELT_FROM[item]
      local left=ore and try(function()
        return furnace.get_inventory(defines.inventory.crafter_input).get_item_count({name=ore,quality="normal"}) end) or 0
      if ore and left>=DesignedStart.ore_per(item,ore) then furnace=nil end
    end
    if furnace then
      -- A stack's worth at a time, as a player grabs one, not the few plates the next belt takes: fewer trips while
      -- it builds, and what is over stays below what it would put away again (STORE_ABOVE).
      local take=math.max(missing,DesignedStart.PLATE_BATCH-have)
      -- (the fullest first, then every other near it holding some: one sweep along the furnace row)
      table.sort(outputs or {},function(a,b) return a.n>b.n end)
      return DesignedStart.sweep_of(npc,{kind="transfer",target=furnace,item=item,count=math.min(available,take),
        mode="withdraw",up_to=true},DesignedStart.collect_more(outputs,furnace,take,math.min(available,take))),nil,"collect"
    end
    local belt,on,near=DesignedStart.on_belts(npc,item)
    if belt and on>=math.min(missing,DesignedStart.GRAB_MIN) then
      return {kind="belt_pickup",target=belt,item=item,count=math.min(missing,500),up_to=true,sweep=near},nil,"collect"
    end
    -- the team world: a teammate's chest, or a player's chest once that player says yes
    local team_job,team_why,team_tag=TeamSupply.take(npc,item,missing,game.tick,true)
    if team_job or team_why then return team_job,team_why,team_tag end
    local ore=SMELT_FROM[item]
    local f,working,busy=nil,0,0
    -- (what it fetches for this is raw, or a furnace made from stone: no deeper chain to limit)
    if ore then f,working,busy=idle_furnace(npc,ore,DesignedStart.ore_per(item,ore)) end
    local coming=math.floor(working/DesignedStart.ore_per(item,ore))   -- (in what it makes: two stone a brick)
    local tick=game.tick
    if coming>=missing then
      DesignedStart.note_wait(npc,item,need,tick)
      if DesignedStart.machines_wait(npc,item,tick) then return nil,"smelting:"..item end
    end
    -- After the opening (drills of its own mine the ore): the furnaces are waited on, ore it holds is smelted, and no
    -- ore is mined nor a furnace put down by hand for it (DesignedStart.drills_make).
    local automated=ore and DesignedStart.drills_make(npc,ore,tick)>0
    if automated and not (f and (count(npc,ore)>0 or DesignedStart.natural_near(npc,ore))) then
      DesignedStart.note_wait(npc,item,need,tick,DesignedStart.furnaces_make(npc,item,tick)==0)
      if DesignedStart.machines_wait(npc,item,tick) then return nil,"smelting:"..item end
      npc.hand_rescue={item=ore,tick=tick}
    end
    -- Its furnaces all at work and still short: one more is put down (below), as a player drops a few more and feeds
    -- them all, while the ones at work would take long over the rest. It stood 44 s with raw stone in hand while one
    -- furnace smelted its bricks (the user's live game, 2026-10-23).
    if working>0 and not f and not DesignedStart.more_smelters(npc,item,missing-coming,busy) then
      DesignedStart.note_wait(npc,item,need,tick)
      if DesignedStart.machines_wait(npc,item,tick) then return nil,"smelting:"..item end
    end
    if f then
      npc.hand_smelter=f
      DesignedStart.note_smelter(npc,f)
      -- (fuelled as any burner is: what it carries, then its stores, wood gathered before any store stands; never coal
      -- mined by hand once its drills make it, the user, 2026-10-26)
      local fuel_inv=f.get_fuel_inventory()
      local left=DesignedStart.fuel_time(f,fuel_inv,"coal")
      if (left and left<120) or (not left and fuel_inv.get_item_count({name="coal",quality="normal"})<3) then
        local fuel,held=DesignedStart.fuel_in_hand(npc,f,fuel_inv,"coal")
        if fuel then
          return {kind="transfer",target=f,item=fuel,count=math.min(held,math.ceil(10*DesignedStart.fuel_value("coal")
            /math.max(1,DesignedStart.fuel_value(fuel)))),mode="deposit",inventory=defines.inventory.fuel},nil,"hand_smelt"
        end
        local job,why=DesignedStart.fuel_fetch(npc,f,fuel_inv,"coal",10,10,true,not (left and left>0))
        if job then return job,why end
        if not (left and left>0) then return nil,why or "no_fuel" end
      end
      local n=math.max(10,math.min(50,missing-working))
      local job,why=acquire(npc,ore,n,0)
      if job then return job,why end
      if count(npc,ore)==0 then return nil,why or ("no_"..ore) end
      return {kind="transfer",target=f,item=ore,count=math.min(n,count(npc,ore)),mode="deposit",
        inventory=defines.inventory.crafter_input},nil,"hand_smelt"
    end
    if ore then
      -- No furnace of its own left to use (or all at work and slow over what is short): it puts one down (five stone,
      -- made by hand) and smelts there, HAND_SMELTERS of them at most (what those hold comes out first).
      if #DesignedStart.hand_smelters(npc)>=DesignedStart.HAND_SMELTERS then
        DesignedStart.note_wait(npc,item,need,tick)
        return nil,"smelting:"..item
      end
      local job,why=acquire(npc,"stone-furnace",1,0)
      if job then return job,why end
      if why and why:sub(1,9)=="crafting:" then return nil,why end
      if count(npc,"stone-furnace")>0 then
        local c=npc.character
        -- At home, as a player smelts by hand at base: not out where it happens to be (a turret site at
        -- its plot's far edge, live game 2026-10-22), nor on someone else's ground in the team world.
        local away=(c.position.x-npc.home.x)^2+(c.position.y-npc.home.y)^2>HAND_SMELT_REACH^2
        if away or TeamWorld.active() and not TeamWorld.holds(TeamWorld.plot("npc-"..npc.id),c.position) then
          return {kind="move",position={x=npc.home.x,y=npc.home.y}},nil,"return_home"
        end
        -- (on its grid, where the game itself would build it, never a spot refused lately: W-25)
        local spot=DesignedStart.Hands.free_spot(npc,c.surface,"stone-furnace",{x=c.position.x+3,y=c.position.y},12,
          c.force)
        if spot then
          return {kind="build",entity_name="stone-furnace",item="stone-furnace",position=spot},nil,"hand_smelt"
        end
      end
    end
    return nil,"waiting_for:"..item
  end
  local hand=Recipes.hand(npc.character)
  local recipe=Recipes.for_item(npc.force,item,hand)
  if not recipe then return nil,"recipe_locked:"..item end
  if not Recipes.made_in(recipe,hand) then
    -- Made by machines (steel, plastic, sulfur, engines): take it from the NPC's own machines.
    local source,available=owned_output(npc,item)
    if source and available>0 then
      return {kind="transfer",target=source,item=item,count=math.min(available,missing),mode="withdraw",up_to=true},
        nil,"collect"
    end
    local team_job,team_why,team_tag=TeamSupply.take(npc,item,missing,game.tick,true)
    if team_job or team_why then return team_job,team_why,team_tag end
    return nil,"waiting_for:"..item
  end
  -- The whole tree first (DesignedStart.make_plan), and its hands' time: fetched ready-made where that is quicker, as a
  -- player takes from the mall or a machine rather than craft again (DesignedStart.fetch_worth); nothing is fetched for
  -- a recipe that cannot be finished.
  local plan,cannot=DesignedStart.make_plan(npc,item,missing)
  local chest,stocked=owned_output(npc,item,true)
  if DesignedStart.fetch_worth(npc,item,chest,stocked,missing,plan) then
    return {kind="transfer",target=chest,item=item,count=math.min(stocked,missing),mode="withdraw",up_to=true},nil,
      "collect"
  end
  local source,available=owned_output(npc,item)
  if DesignedStart.fetch_worth(npc,item,source,available,missing,plan) then
    return {kind="transfer",target=source,item=item,count=math.min(available,missing),mode="withdraw",up_to=true},
      nil,"collect"
  end
  local belt,on,near=DesignedStart.on_belts(npc,item)
  if belt and DesignedStart.fetch_worth(npc,item,belt,on,missing,plan) then
    return {kind="belt_pickup",target=belt,item=item,count=math.min(missing,500),up_to=true,sweep=near},nil,"collect"
  end
  -- the team world: ready-made in a teammate's chest beats making it (players are asked only for what
  -- takes machines or furnaces)
  local team_job,team_why,team_tag=TeamSupply.take(npc,item,missing,game.tick,false,function(source,n)
    return DesignedStart.fetch_worth(npc,item,source,n,missing,plan)
  end)
  if team_job or team_why then return team_job,team_why,team_tag end
  do   -- (its own machine makes it now: from there, not its hands; lesson 15)
    local job,why=DesignedStart.machine_wait(npc,item,missing,depth,source,available)
    if job then return job,nil,"collect" end
    if why then return nil,why end
  end
  -- (it cannot make it now: what its own stores hold of it is taken, however far; the walk beats nothing. The proving
  -- ring's loop chest stood empty while 116 magazines lay in its home store, no iron to craft more: D-19)
  if not plan then
    local s,n=(chest and stocked>0) and chest or source,(chest and stocked>0) and stocked or available
    if s and n>0 then
      return {kind="transfer",target=s,item=item,count=math.min(n,missing),mode="withdraw",up_to=true},nil,"collect"
    end
    return nil,cannot   -- (the tree cannot be finished: nothing fetched or mined for it)
  end
  local batches=math.ceil(missing/Recipes.yield(recipe,item))
  for _,ingredient in ipairs(recipe.ingredients) do
    if ingredient.type=="fluid" then return nil,"fluid_ingredient:"..item end
    local job,why=acquire(npc,ingredient.name,ingredient.amount*batches,depth+1)
    if job or why then return job,why end
  end
  return {kind="craft",recipe=recipe.name,count=batches,background=true}
end

-- A building of a kept plan was destroyed (biters, a raid): remember it, to rebuild once it is calm.
-- Plan buildings by unit number, so a wave of losses costs a lookup each, not a scan of every plan.
-- Rebuilt when a lost building is not in it (at most every INDEX_EVERY ticks).
local INDEX_EVERY=600
local ALLY_CALL_EVERY=36000   -- an engineer under attack calls an ally at most every 10 minutes
local function unit_index(npc,tick,force)
  local idx=npc.unit_index
  if idx and not force then return idx end
  if idx and tick<(idx.built or 0)+INDEX_EVERY then return idx end
  idx={built=tick,units={}}
  -- (the plan being built too: a building of a stage already done is not visited again by its stage)
  local plans={}
  for _,plan in ipairs(npc.design_history or {}) do plans[#plans+1]=plan end
  if npc.design and npc.design.plan and npc.design.status=="building" then plans[#plans+1]=npc.design.plan end
  if npc.paused_design and npc.paused_design.plan then plans[#plans+1]=npc.paused_design.plan end
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and e.unit_number then idx.units[e.unit_number]={plan=plan,id=id} end
    end
  end
  npc.unit_index=idx
  return idx
end

function DesignedStart.on_entity_died(event)
  local e=event.entity
  if not (e and e.valid) or e.type=="character" or not e.unit_number then return end
  for _,npc in pairs(storage.npcs or {}) do
    if npc.force==e.force then
      if not (npc.design_history or npc.design) then return end
      local hit=unit_index(npc,event.tick).units[e.unit_number]
      if not hit then hit=unit_index(npc,event.tick,true).units[e.unit_number] end
      if hit and hit.plan.entities[hit.id] and hit.plan.entities[hit.id].entity==e then
        npc.rebuild=npc.rebuild or {}
        if #npc.rebuild<200 then npc.rebuild[#npc.rebuild+1]={plan=hit.plan,id=hit.id,tick=event.tick,lost=event.tick} end
        -- Biters took it: an allied engineer comes to help defend this spot (one call per while).
        local cause=event.cause
        if cause and cause.valid and cause.force and cause.force.name=="enemy"
            and event.tick>=(npc.help_called_at or -ALLY_CALL_EVERY)+ALLY_CALL_EVERY then
          -- (the team world: weighed, as its help is, War.helper_for: D-12, a helper called away four minutes after
          -- its own plot was hit died; elsewhere the first friend free)
          local chosen=TeamWorld.active() and Agent.helper_for and Agent.helper_for(npc,{x=e.position.x,y=e.position.y},event.tick)
          for _,ally in pairs(TeamWorld.active() and {chosen} or storage.npcs) do
            if ally~=npc and ally.design and not ally.help_request and ally.character and ally.character.valid
                and ally.force.get_friend(npc.force) and npc.force.get_friend(ally.force) then
              ally.help_request={kind="defend",npc_id=npc.id,tick=event.tick,position={x=e.position.x,y=e.position.y},
                brings=Agent.helper_brings and Agent.helper_brings(ally) or nil}
              npc.help_called_at=event.tick
              log("[npc-factions] npc="..ally.id.." will help npc="..npc.id.." defend "..e.position.x..","..e.position.y)
              break
            end
          end
        end
      end
      return
    end
  end
end

-- A lost building put back where its plan had it: the plan's record, its settings, and how long it stood broken
-- (a gap is what spoils a belt's lanes): for the log, the export and learning.
function DesignedStart.rebuilt(npc,plan,id,e,tick)
  local entity=plan.entities[id]
  if not entity then return end
  entity.entity,entity.built_tick=e,tick
  if npc.unit_index and e.unit_number then npc.unit_index.units[e.unit_number]={plan=plan,id=id} end
  if entity.recipe and e.type=="assembling-machine" then
    pcall(function() e.set_recipe(entity.recipe) end)
    if entity.direction and e.direction~=entity.direction then pcall(function() e.direction=entity.direction end) end
  end
  DesignedStart.apply_settings(e,entity.settings)   -- (a blueprint's own settings, as when first placed)
  if entity.bar and (e.type=="container" or e.type=="logistic-container") then
    pcall(function() e.get_inventory(defines.inventory.chest).set_bar(entity.bar+1) end)
  end
  npc.rebuilt=(npc.rebuilt or 0)+1
  local queue=npc.rebuild or {}
  for i,r in ipairs(queue) do
    if r.plan==plan and r.id==id then
      if r.lost then
        npc.rebuild_log=npc.rebuild_log or {}
        table.insert(npc.rebuild_log,{name=entity.prototype,x=entity.world.x,y=entity.world.y,lost=r.lost,rebuilt=tick})
        while #npc.rebuild_log>12 do table.remove(npc.rebuild_log,1) end
        log("[npc-factions] npc="..npc.id.." rebuilt "..entity.prototype.." at "..entity.world.x..","..entity.world.y
          .." "..math.floor((tick-r.lost)/60).." s after it was lost")
      end
      table.remove(queue,i)
      break
    end
  end
end

-- What biters or raiders destroyed, put back once it is calm there. Every calm loss counts (one still fought over
-- holds back no other), what is lost of one kind is fetched or made at once, and they go back in one walk, nearest
-- next, as a player repairs after a wave: one trip a building kept copper unmined for twenty minutes after an
-- attack (run 33, 2026-10-25).
-- Nothing lost is dropped without a word: each waits for a reason it keeps (r.wait, logged when it changes, and told
-- to its strategist once it has waited long: DesignedStart.rebuild_wait), one whose item cannot be had now waits on
-- that alone (the rest go on), what it lacks is got as any item is (acquire), and only a spot it cannot build on after
-- REBUILD_TRIES is given up, and reported. Of 28 buildings lost in a death-world rehearsal one came back, 19 minutes
-- later, with nothing to say why (rehearsal 7b, 2026-10-27).
DesignedStart.REBUILD_RUN=40       -- lost buildings put back in one walk at most
DesignedStart.REBUILD_TRIES=3      -- builds that failed on the spot before it is given up
DesignedStart.REBUILD_RETRY=120    -- ticks an item it could not get waits before it is asked for again (doubling, to 8x)
DesignedStart.REBUILD_TELL=7200    -- a loss waiting this long is told to its strategist (once per reason)
-- Ticks a loss that does not defend waits for a defense going up, at most, from the loss itself: counted from when it
-- was first looked at, a loss a slow stage had kept from being looked at waited the whole hold again after (W-34:
-- NPC 6's drills and furnaces, 450-556 s, normal-7d).
DesignedStart.REBUILD_DEFENSE_HOLD=3600
-- Ticks past its calm (REBUILD_CALM) a loss is overdue: then it goes back even while a stage is short of its building
-- share (Factory.tick). Behind a stuck reinforce stage NPC 6's belts, poles and a whole steam block were not even
-- looked at for up to 1154 s, and never went back (W-34, normal-7d; the user's aim: back within a minute or two).
DesignedStart.REBUILD_DUE=2700
DesignedStart.REBUILD_RUN_STARVED=8   -- pieces an overdue turn puts back at most, past a starved stage
DesignedStart.OVERDUE_EVERY=1800      -- ticks between overdue turns past a starved stage, at least
-- Failures that are the spot's (something there, or it cannot be got to): only these spend a loss's tries.
DesignedStart.REBUILD_SPOT={placement_blocked_or_out_of_reach=true,placement_collision=true,no_reachable_build_stance=true,
  path_ended_outside_reach=true,no_path=true,["not_its_ground"]=true}
function DesignedStart.rebuild_wait(npc,r,entity,reason,tick)
  r.at=r.at or {x=entity.world.x,y=entity.world.y}   -- (biters camped on what they broke: combat.lua's intruders)
  if r.wait==reason then
    if not r.told and tick-(r.wait_since or tick)>=DesignedStart.REBUILD_TELL then
      -- (told once it is taken: with no strategist listening yet, asked again on a later turn)
      local ok,took=pcall(Agent.event,npc,{kind="rebuild_waiting",building=entity.prototype,why=reason,
        position={x=math.floor(entity.world.x),y=math.floor(entity.world.y)},seconds=math.floor((tick-(r.lost or tick))/60)})
      r.told=ok and took~=false
    end
    return
  end
  r.wait,r.wait_since,r.told=reason,tick,nil
  log("[npc-factions] npc="..npc.id.." rebuild of "..tostring(entity.prototype).." at "..entity.world.x..","
    ..entity.world.y.." waits: "..reason.." (lost "..math.floor((tick-(r.lost or tick))/60).." s ago)")
end
-- A lost piece's owner now (W-24): the plan kept (being built, set aside, or in its history, newest first) that
-- wants the same building at the same spot, as (plan, id); else nil. Never a piece left out or skipped (Obstacles: not
-- to be built).
function DesignedStart.rebuild_owner(npc,entity)
  local plans={}
  if npc.design and npc.design.plan and npc.design.status=="building" then plans[#plans+1]=npc.design.plan end
  if npc.paused_design and npc.paused_design.plan then plans[#plans+1]=npc.paused_design.plan end
  local history=npc.design_history or {}
  for k=#history,1,-1 do plans[#plans+1]=history[k] end
  local w=entity.world
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan.order or {}) do
      local e=plan.entities[id]
      if e and e~=entity and e.prototype==entity.prototype and e.world and math.abs(e.world.x-w.x)<=0.5
        and math.abs(e.world.y-w.y)<=0.5 and not (e.left_out or e.skipped) then   -- (standing already: nothing to do)
        return plan,id
      end
    end
  end
  return nil
end

-- A loss taken over by a plan under way (being built or set aside) whose stage for that piece is now or still to come:
-- that stage puts it up with its ground and inputs, so it waits; once the plan is done or shelved it goes back as any
-- loss (Codex, W-24).
function DesignedStart.rebuild_ahead(npc,r,entity)
  if not r.moved then return false end
  for _,d in ipairs({npc.design or false,npc.paused_design or false}) do   -- (either may be absent)
    if d and d.plan==r.plan and (d~=npc.design or d.status=="building") then
      for k,stage in ipairs(d.plan.stages or {}) do
        if stage.id==entity.stage then return k>=(d.stage_index or 1) end
      end
    end
  end
  return false
end

-- Whether a plan of its own other than `plan` (being built, set aside, kept) has `e` as one of its pieces.
function DesignedStart.owned_elsewhere(npc,plan,e)
  local plans={npc.design and npc.design.plan or false,npc.paused_design and npc.paused_design.plan or false}
  for _,p in ipairs(npc.design_history or {}) do plans[#plans+1]=p end
  for _,p in ipairs(plans) do
    if p and p~=plan then
      for _,id in ipairs(p.order or {}) do if p.entities[id].entity==e then return true end end
    end
  end
  return false
end

-- What makes or carries its power (W-34): a pump, boiler or generator of any kind, and a pole beside a pole of its own
-- still on a network (one standing alone powers nothing yet). Looked at once a minute per loss.
DesignedStart.POWER_TYPES={["offshore-pump"]=true,boiler=true,generator=true,reactor=true,["solar-panel"]=true,
  accumulator=true,["heat-pipe"]=true,["burner-generator"]=true}
function DesignedStart.feeds_power(npc,r,entity,tick)
  if r.power~=nil and tick-(r.power_tick or 0)<3600 then return r.power end
  local p=prototypes.entity[entity.prototype]
  local kind=p and p.type
  local power=DesignedStart.POWER_TYPES[kind or ""]==true
  if kind=="electric-pole" then
    local reach=(try(function() return p.get_max_wire_distance() end) or try(function() return p.max_wire_distance end)
      or 7.5)
    local near=try(function() return npc.character.surface.find_entities_filtered{type="electric-pole",
      position=entity.world,radius=reach,force=npc.force} end) or {}
    for _,e in ipairs(near) do
      if (try(function() return e.electric_network_id end) or 0)>0 then power=true; break end
    end
  end
  r.power,r.power_tick=power,tick
  return power
end

-- Whether a loss waiting is overdue (REBUILD_DUE past its calm): then it goes back ahead of a stage short of its
-- building share. Each told once in the log, the run's count of overdue losses.
function DesignedStart.rebuild_overdue(npc,tick)
  local any=false
  for _,r in ipairs(npc.rebuild or {}) do
    if tick>=(r.tick or r.lost or tick)+REBUILD_CALM+DesignedStart.REBUILD_DUE then
      any=true
      if not r.overdue_told then
        r.overdue_told=true
        local e=r.plan and r.plan.entities and r.plan.entities[r.id]
        log("[npc-factions] npc="..npc.id.." rebuild of "..tostring(e and e.prototype).." at "
          ..(e and e.world and (e.world.x..","..e.world.y) or "?").." overdue (lost "
          ..math.floor((tick-(r.lost or tick))/60).." s ago, waits: "..tostring(r.wait or "-")..")")
        npc.rebuild_overdue_count=(npc.rebuild_overdue_count or 0)+1
      end
    end
  end
  return any
end

-- Given up for good (the spot refuses it, or nothing wants it there any more): logged and told, never just dropped.
function DesignedStart.rebuild_given_up(npc,r,entity,reason,tick)
  if entity and entity.ghost and entity.ghost.valid then entity.ghost.destroy(); entity.ghost=nil end   -- (W-26)
  log("[npc-factions] npc="..npc.id.." gives up rebuilding "..tostring(entity and entity.prototype).." at "
    ..(entity and entity.world and (entity.world.x..","..entity.world.y) or "?").." ("..reason..")")
  if entity and entity.world then
    -- (kept until its strategist takes it: rebuild_job tells again)
    local event={kind="rebuild_given_up",building=entity.prototype,why=reason,
      position={x=math.floor(entity.world.x),y=math.floor(entity.world.y)}}
    local ok,took=pcall(Agent.event,npc,event)
    if not (ok and took~=false) then
      npc.rebuild_untold=npc.rebuild_untold or {}
      table.insert(npc.rebuild_untold,event)
      while #npc.rebuild_untold>16 do table.remove(npc.rebuild_untold,1) end
    end
  end
  npc.rebuild_given_up=npc.rebuild_given_up or {}
  table.insert(npc.rebuild_given_up,{name=entity and entity.prototype,why=reason,tick=tick})
  while #npc.rebuild_given_up>16 do table.remove(npc.rebuild_given_up,1) end
end
-- `defending`: a defense is going up: only what defends (turrets, walls) goes back meanwhile; the rest waits for it.
local function rebuild_job(npc,tick,defending,opts)
  local near=opts and opts.near
  while npc.rebuild_untold and npc.rebuild_untold[1] do
    local ok,took=pcall(Agent.event,npc,npc.rebuild_untold[1])
    if not (ok and took~=false) then break end
    table.remove(npc.rebuild_untold,1)
  end
  local queue=npc.rebuild
  local calm={}
  local surface=npc.character.surface
  local i=1
  while queue and queue[i] do
    local r=queue[i]
    local entity=r.plan.entities[r.id]
    -- (its plan: kept, being built, or set aside for an urgent one, a defense ordered now: picked up again after)
    local kept=npc.design~=nil and npc.design.plan==r.plan and npc.design.status=="building"
      or npc.paused_design~=nil and npc.paused_design.plan==r.plan
    for _,plan in ipairs(npc.design_history or {}) do if plan==r.plan then kept=true end end
    -- (taken over, then left out or skipped by that plan: it wants it there no more, so another may; Codex, W-24)
    if r.moved and entity and (entity.left_out or entity.skipped) then kept=false end
    local dup=false
    if not kept and entity and entity.world then
      -- (its plan gone, handed back, merged or replaced: the plan that still wants this piece here takes it, W-24)
      local plan,id=DesignedStart.rebuild_owner(npc,entity)
      if plan then
        for _,q in ipairs(queue) do if q~=r and q.plan==plan and q.id==id then dup=true end end
        r.plan,r.id,r.moved,entity,kept=plan,id,true,plan.entities[id],true
      end
    end
    if dup then
      table.remove(queue,i)   -- (that plan's own loss of it waits already)
    elseif not kept or not entity or (entity.entity and entity.entity.valid) or (r.fails or 0)>=DesignedStart.REBUILD_TRIES then
      if entity and not (entity.entity and entity.entity.valid) and not r.reported then
        DesignedStart.rebuild_given_up(npc,r,entity,not kept and "no plan of its own wants it there any more"
          or ("the spot refused it "..math.floor(r.fails or 0).." times"),tick)
      end
      table.remove(queue,i)
    else
      i=i+1
      -- (it stands again: put down by a walk that broke off before it reported, or by someone else)
      local back=try(function() return surface.find_entities_filtered{name=entity.prototype,position=entity.world,
        radius=0.5,force=npc.force,limit=1}[1] end)
      -- (mirrored as planned, or it is not the piece back: DS-3)
      if back and (try(function() return back.mirroring end)==true)~=(entity.mirror==true) then back=nil end
      local p=prototypes.entity[entity.prototype]
      local defends=p and DesignedStart.STANDS[p.type] or false
      if back and back.valid then
        DesignedStart.rebuilt(npc,r.plan,r.id,back,tick)
        i=i-1   -- (rebuilt took it off the queue)
      elseif DesignedStart.rebuild_ahead(npc,r,entity) then
        DesignedStart.rebuild_wait(npc,r,entity,"stage_to_come",tick)
      elseif surface.count_entities_filtered{position=entity.world,radius=REBUILD_CLEAR,force="enemy",
          type="unit",limit=1}>0 or DesignedStart.fought_over(npc,entity.world) then   -- (as a plan's spot: nests, worms)
        r.tick=tick   -- still being fought over: wait (biters first, so the reason holds while they stay)
        DesignedStart.rebuild_wait(npc,r,entity,"biters_near",tick)
      elseif tick<r.tick+REBUILD_CALM then
        DesignedStart.rebuild_wait(npc,r,entity,"calm",tick)
      elseif TeamWorld.active() and TeamWorld.player_built_near(surface,entity_box(entity.prototype,entity.world,
          entity.direction)) then
        r.fails=DesignedStart.REBUILD_TRIES   -- a player has built there since: it is theirs now, not put back
      -- (held for defenses REBUILD_DEFENSE_HOLD in all, once: one defense plan after another restarted it, and burner
      -- drills waited 714 and 824 s, rehearsal 7d, 2026-10-28)
      elseif defending and not defends and tick-(r.lost or r.defense_since or tick)<DesignedStart.REBUILD_DEFENSE_HOLD then
        r.defense_since=r.defense_since or tick
        DesignedStart.rebuild_wait(npc,r,entity,"defense_going_up",tick)
      elseif near and (function()   -- (away on a trip: only one it reaches from here, with one it holds)
          local c=npc.character
          local R=math.min(DesignedStart.reach(npc),(try(function() return c.build_distance end) or 10))-1   -- (what it builds at)
          local b=prototypes.entity[entity.prototype] and entity_box(entity.prototype,entity.world,entity.direction)
          local on=b and c.position.x>b[1][1] and c.position.x<b[2][1] and c.position.y>b[1][2] and c.position.y<b[2][2]
          return (entity.world.x-c.position.x)^2+(entity.world.y-c.position.y)^2>R*R
            or (DesignedStart.usable(npc,entity.item,tick))<1
            or on   -- (it stands on the spot: the build would step it aside)
        end)() then
        DesignedStart.rebuild_wait(npc,r,entity,"urgent_trip",tick)
      elseif tick>=(r.retry_at or 0) then   -- (else waiting on its item: r.wait says which)
        -- (past every hold, waiting only its turn: said so, not the last wait it logged; W-36)
        if r.wait~="queued" and not (r.wait or ""):find("^materials:") and not (r.wait or ""):find("^crafting:") then
          r.wait,r.wait_since,r.told="queued",tick,nil
        end
        calm[#calm+1]={r=r,entity=entity,defends=defends,power=DesignedStart.feeds_power(npc,r,entity,tick)}
      end
    end
  end
  if #calm==0 then return nil end
  -- what defends first, then what feeds its power (the coordinator, W-34: a steam block down stops all it powers),
  -- then the oldest loss
  table.sort(calm,function(a,b)
    if a.defends~=b.defends then return a.defends end
    if a.power~=b.power then return a.power end
    return (a.r.lost or 0)<(b.r.lost or 0)
  end)
  -- The first kind of building whose item it has or can get: one it cannot get now waits on that alone, the rest go on.
  local first
  local tried={}
  for _,c in ipairs(calm) do
    local item=c.entity.item
    if near then first=c; break end   -- (in hand already: nothing fetched, nothing made; the near filter)
    -- (its network holds it for its robots there: nothing fetched, the network's robots put it up; Codex, W-26b)
    if not c.entity.by_hand and DesignedStart.Robots.network_has(npc,c.entity.world,item) then first=c; break end
    if not tried[item] then
      tried[item]=true
      local need=0
      for _,o in ipairs(calm) do if o.entity.item==item then need=need+1 end end
      local job,why=acquire(npc,item,math.max(1,math.min(need,BUILD_BATCH)),0)
      if not job and why and need>1 then job,why=acquire(npc,item,1,0) end
      if job then return job,{upkeep="rebuild_acquire",item=item} end   -- (what it is for: F-11's hand clock)
      if not why then first=c; break end
      local crafting=why:sub(1,9)=="crafting:"
      for _,o in ipairs(calm) do
        if o.entity.item==item then
          if not crafting then
            o.r.misses=(o.r.misses or 0)+1
            o.r.retry_at=tick+DesignedStart.REBUILD_RETRY*math.min(8,2^(o.r.misses-1))
          end
          DesignedStart.rebuild_wait(npc,o.r,o.entity,crafting and why or ("materials:"..why),tick)
        end
      end
    end
  end
  if not first then return nil end
  first.r.misses=nil
  -- (a building of another plan it keeps stands there now: that plan took the spot; not taken down for this one)
  if prototypes.entity[first.entity.prototype] then
    local box=entity_box(first.entity.prototype,first.entity.world,first.entity.direction)
    for _,e in ipairs(try(function() return surface.find_entities_filtered{area=box,force=npc.force} end) or {}) do
      local theirs=false
      local plans={npc.design and npc.design.plan}
      for _,plan in ipairs(npc.design_history or {}) do plans[#plans+1]=plan end
      for _,plan in pairs(plans) do
        if plan~=first.r.plan then
          for _,id in ipairs(plan.order or {}) do if plan.entities[id].entity==e then theirs=true end end
        end
      end
      if theirs then
        first.r.fails,first.r.reported=DesignedStart.REBUILD_TRIES,true
        DesignedStart.rebuild_given_up(npc,first.r,first.entity,"a later plan's "..e.name.." stands there",tick)
        return nil
      end
    end
  end
  -- (its spot cleared first, as a plan's building's is: trees grown, a rock, what lies there, a leftover of its own)
  local clear=DesignedStart.clear_footprint and prototypes.entity[first.entity.prototype]
    and DesignedStart.clear_footprint(npc,first.entity)
  -- (away on a trip, a spot to clear first waits for its return: a tree may be beyond what it mines at from here)
  if clear and near then DesignedStart.rebuild_wait(npc,first.r,first.entity,"urgent_trip",tick); return nil end
  if clear then
    local job,why=DesignedStart.Robots.instead(npc,clear,tick)   -- (a tree or rock there: its robots', W-26)
    if why then DesignedStart.rebuild_wait(npc,first.r,first.entity,why,tick); return nil end
    return job,{upkeep="rebuild_clear"}
  end
  local held,run,used={},{},{}
  local function have(item)
    if held[item]==nil then held[item]=(DesignedStart.usable(npc,item,tick)) end
    return held[item]
  end
  local at=first
  while at and #run<(opts and opts.run or DesignedStart.REBUILD_RUN) do
    used[at]=true
    held[at.entity.item]=have(at.entity.item)-1
    run[#run+1]=at
    local here,best,bd=at.entity.world,nil,nil
    for _,c in ipairs(calm) do
      -- (one a walk could not put down goes on its own: solo)
      if not used[c] and not c.r.solo and have(c.entity.item)>0 then
        local dd=(c.entity.world.x-here.x)^2+(c.entity.world.y-here.y)^2
        if not bd or dd<bd then best,bd=c,dd end
      end
    end
    at=best
  end
  -- (its robots: every loss in the run a ghost they put up from its pockets, while it stands in their reach; by hand
  -- what they cannot do or have not done in a while: W-26)
  if DesignedStart.Robots.reach(npc) or DesignedStart.Robots.network(npc,first.entity.world) then
    local e=first.entity
    local hand={kind="build",entity_name=e.prototype,item=e.item,position=e.world,direction=e.direction,
      belt_to_ground_type=e.belt_to_ground_type,mirror=e.mirror}
    for _,o in ipairs(run) do DesignedStart.Robots.ghost(npc,o.entity) end
    local job,why=DesignedStart.Robots.instead(npc,hand,tick,e)
    if why then
      for _,o in ipairs(run) do DesignedStart.rebuild_wait(npc,o.r,o.entity,why,tick) end
      return nil
    end
    if job~=hand then return job,{upkeep="rebuild_robots"} end
  end
  if #run==1 or first.r.solo then
    local entity=first.entity
    return {kind="build",entity_name=entity.prototype,item=entity.item,position=entity.world,
      direction=entity.direction,belt_to_ground_type=entity.belt_to_ground_type,mirror=entity.mirror},
      {upkeep="rebuild",plan=first.r.plan,entity_id=first.r.id}
  end
  local placements,entries={},{}
  for k,c in ipairs(run) do
    local e,pid=c.entity,"rebuild-"..k
    placements[k]={id=pid,entity_name=e.prototype,item=e.item,position={x=e.world.x,y=e.world.y},
      direction=e.direction,belt_to_ground_type=e.belt_to_ground_type,mirror=e.mirror}
    entries[pid]={plan=c.r.plan,id=c.r.id}
  end
  return {kind="build_run",placements=placements},{upkeep="rebuild_run",entries=entries}
end
DesignedStart.rebuild_job=rebuild_job
-- Its losses ahead of its plans and its agenda (Factory.tick), not only in upkeep: a building, a strategist's recall or
-- standing off from a wave came first every tick, and two lost drills went 472 and 582 s before their rebuild was even
-- looked at (rehearsal 7d, 2026-10-28). Only calm losses whose item it has or gets come back here (rebuild_job).
-- Off its own ground: no building of its own within DesignedStart.far of where it stands (W-36). Measured from its home
-- point, its own plot's edge counted as away: NPC 4's defense there, 64 tiles and more from home, held every loss of its
-- base as an urgent trip's (normal-7e: 630 s). Looked at once a second.
DesignedStart.OWN_TYPES={"assembling-machine","furnace","mining-drill","transport-belt","underground-belt","splitter",
  "electric-pole","inserter","container","boiler","generator","pipe","offshore-pump","lab","wall","gate","ammo-turret",
  "electric-turret","roboport","radar","rocket-silo","chemical-plant","oil-refinery","pumpjack","storage-tank"}
function DesignedStart.on_own_ground(npc,pos)
  local c=npc.character
  if not (c and c.valid and pos) then return true end
  local m=npc.own_ground
  if m and game.tick-m.tick<60 and math.abs(m.x-pos.x)<4 and math.abs(m.y-pos.y)<4 then return m.on end
  local f=DesignedStart.far(npc)
  local function near(p) return p and (p.x-pos.x)^2+(p.y-pos.y)^2<=f*f end
  -- (by its home, or by what it lost: a wave that took every building there leaves its ground its own; Codex)
  local on=near(npc.home)
  for _,r in ipairs(not on and npc.rebuild or {}) do
    local e=r.plan and r.plan.entities and r.plan.entities[r.id]
    if e and near(e.world) then on=true; break end
  end
  on=on or (try(function() return c.surface.count_entities_filtered{position=pos,radius=f,
    force=npc.force,type=DesignedStart.OWN_TYPES,limit=1} end) or 1)>0
  npc.own_ground={tick=game.tick,x=pos.x,y=pos.y,on=on}
  return on
end
-- On a trip to others: helping one, an attack's march, or an order now to visit or give (wherever it stands: a building
-- of its own by the one it visits makes it no less a trip; Codex, W-36).
-- (called back or heading home: on its way too. Only the order it is on now, the agenda's first "now": a visit queued
-- behind its own work at home is no trip yet; Codex, W-36)
DesignedStart.TRIP_TOOLS={visit=true,give=true,help=true,attack=true,deliver=true,relocate=true}
function DesignedStart.on_trip(npc)
  if npc.helping or npc.combat_objective or npc.marching or npc.recall or npc.homeward then return true end
  for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do
    if it.priority=="now" then return DesignedStart.TRIP_TOOLS[it.tool or ""]==true end
  end
  return false
end
-- Away on urgent work: on a trip to others, or off its own ground (not merely far from its home point: W-36).
function DesignedStart.away_from_home(npc)
  local c=npc.character
  if not (c and c.valid) then return false end
  return DesignedStart.on_trip(npc) or not DesignedStart.on_own_ground(npc,c.position)
end
-- How its rebuilds go with what it urgently does: nil (no urgent work: all of them); agenda_first (urgent work on its
-- own ground: its order has its turn, its losses while it waits, and an overdue one a short turn now and then,
-- Factory.tick); near (urgent work off its own ground: only what it reaches from where it stands, with what it holds).
-- Whether a plan has a piece still to put up (not left out or skipped on purpose).
function DesignedStart.pieces_left(d)
  if d and d.status=="waiting" then return true end   -- (asked for, not begun)
  local plan=d and d.plan
  if not plan then return false end
  for _,id in ipairs(plan.order or {}) do
    local e=plan.entities[id]
    if e and not (e.left_out or e.skipped) and not (e.entity and e.entity.valid) then return true end
  end
  return false
end

function DesignedStart.rebuild_opts(npc)
  if not (Agent.urgent and Agent.urgent(npc)) then return nil end
  if DesignedStart.away_from_home(npc) then return {near=true} end
  return {agenda_first=true}
end
-- `opts.near`: only losses in reach of where it stands, and only with what it holds (no walk, no fetch).
function DesignedStart.rebuild_now(npc,tick,opts)
  if not (((npc.rebuild and #npc.rebuild>0) or (npc.rebuild_untold and #npc.rebuild_untold>0))
      and npc.character and npc.character.valid) then return nil end
  -- (a defense going up, or asked for and on its way: rebuild_job's hold)
  -- (a defense whose pieces all stand, only its ammo or its witness left, holds nothing: rehearsal 7g's reinforce turrets
  -- stood with no biters left and three losses waited the whole hold)
  local defending=npc.design and (npc.design.status=="building" or npc.design.status=="waiting") and DesignedStart.DEFENSES
    and DesignedStart.DEFENSES[npc.design.purpose] and DesignedStart.pieces_left(npc.design)
  return rebuild_job(npc,tick,defending,opts)
end

-- Whether any plan the engineer keeps (or is building) uses this building.
local function used_by_a_plan(npc,target)
  -- (one set aside for an urgent one too: still its, picked back up after; Codex, lesson 17)
  local plans={npc.design and npc.design.plan or false,npc.paused_design and npc.paused_design.plan or false}
  for _,plan in ipairs(npc.design_history or {}) do plans[#plans+1]=plan end
  for _,plan in pairs(plans) do
    for _,id in ipairs(plan and plan.order or {}) do if plan.entities[id].entity==target then return true end end
  end
  return false
end

-- A pickup job for its own `entities` (lesson 17: Offense's creep, its turrets taken back with their ammo), or nil:
-- each still standing, its own, minable and in no plan of its own (a plan's building stays), at most
-- Hands.MAX_PICKUP, in walking order from where it stands. (job, meta) for the caller to give, as any upkeep job.
function DesignedStart.pickup_job(npc,entities)
  local c=npc.character
  if not (c and c.valid) or type(entities)~="table" then return nil end
  local left={}
  for _,e in ipairs(entities) do
    if e and e.valid and e.force==c.force and e.minable and not used_by_a_plan(npc,e) then left[#left+1]=e end
  end
  local out,here={},c.position
  while #left>0 and #out<DesignedStart.Hands.MAX_PICKUP do
    local best,bd
    for k,e in ipairs(left) do
      local d=(e.position.x-here.x)^2+(e.position.y-here.y)^2
      if not bd or d<bd then best,bd=k,d end
    end
    local e=table.remove(left,best)
    out[#out+1]=e; here=e.position
  end
  if #out==0 then return nil end
  return {kind="pickup",targets=out},{upkeep="pickup",count=#out}
end

-- A piece the corrector added to a plan (a pole, an overflow chest) gives way to the plan's own
-- buildings.
local function repair_piece(d,target)
  for _,id in ipairs(d.plan and d.plan.order or {}) do
    local entity=d.plan.entities[id]
    if entity.entity==target then return entity.repair==true end
  end
  return false
end

local function in_plan(d,target)
  for _,id in ipairs(d.plan.order) do if d.plan.entities[id].entity==target then return true end end
  return false
end

-- Buildings whose facing does not matter: any direction counts as the same building.
local UNDIRECTED={furnace=true,container=true,lab=true,["electric-pole"]=true,["ammo-turret"]=true,wall=true,pipe=true,
  ["electric-turret"]=true,["artillery-turret"]=true,["land-mine"]=true,roboport=true}

-- An underground belt's or a loader's end ("input"/"output"); nil for anything else.
local function belt_end(e)
  if e.type=="underground-belt" then return e.belt_to_ground_type end
  if e.type=="loader" then return e.loader_type end
end

-- An identical building of the engineer's own already standing where the plan wants one
-- (left from an earlier plan or a bootstrap build) is simply used: same name, place,
-- facing, underground or loader end and recipe.
local function adopt(npc,entity)
  local surface=npc.character.surface
  for _,e in ipairs(surface.find_entities_filtered{name=entity.prototype,position=entity.world,radius=0.5,
      force=npc.force}) do
    if e.valid and math.abs(e.position.x-entity.world.x)<0.01 and math.abs(e.position.y-entity.world.y)<0.01
        and (UNDIRECTED[e.type] or e.direction==entity.direction)
        and (not entity.belt_to_ground_type or belt_end(e)==entity.belt_to_ground_type)
        and (try(function() return e.mirroring end)==true)==(entity.mirror==true)   -- (as planned: DS-3)
        and not in_plan(npc.design,e) then
      local recipe=e.type=="assembling-machine" and e.get_recipe()
      if not entity.recipe or e.type~="assembling-machine" or (recipe and recipe.name==entity.recipe) then return e end
    end
  end
end

-- Trees and rocks are mined away; the NPC's own buildings that are not part of
-- this plan are picked up (item and contents return to its inventory), so the
-- planned building replaces whatever it overlaps.
-- Whether the planned piece replaces as a player does what stands on its spot (the same group: a stone furnace where a
-- steel one goes): the old one is left for the build's fast replace (Worker.fast_replace), which keeps what is in it.
function DesignedStart.fast_replaceable(npc,entity)
  local surface=npc.character.surface
  return try(function()
    return surface.can_fast_replace{name=entity.prototype,position=entity.world,direction=entity.direction,force=npc.force}
  end)==true
end

local function clear_footprint(npc,entity)
  local surface=npc.character.surface
  local box=entity_box(entity.prototype,entity.world,entity.direction)
  -- What lies on the ground there is picked up first, as a player clears a spot: a belt put down over it takes
  -- it on. Bricks spilled from furnaces it cleared out of the bus's way rode its lanes and starved its modules
  -- (run 30, 2026-10-25).
  for _,e in ipairs(surface.find_entities_filtered{area=box,type="item-entity"}) do
    local stack=e.valid and e.stack
    if stack and stack.valid_for_read then
      local got=npc.character.get_main_inventory().insert({name=stack.name,count=stack.count,quality=stack.quality})
      if got>=stack.count then e.destroy() elseif got>0 then stack.count=stack.count-got end
    end
  end
  for _,e in ipairs(surface.find_entities_filtered{area=box,type={"tree","simple-entity"}}) do
    if e.minable and e.prototype.mineable_properties.products then
      return {kind="mine",target=e,count=1,clears=true}
    end
  end
  -- (one the planned one replaces as a player does: left for the build, DesignedStart.fast_replaceable)
  for _,e in ipairs(DesignedStart.fast_replaceable(npc,entity) and {} or surface.find_entities_filtered{area=box,force=npc.force}) do
    if e.type~="character" and e.type~="entity-ghost" and e.type~="tile-ghost" and e.minable
        and (not in_plan(npc.design,e) or repair_piece(npc.design,e)) then
      if e==npc.miner then npc.miner,npc.module=nil,nil end        -- replaced on purpose: not missing
      if e==npc.furnace then npc.furnace,npc.furnace_position=nil,nil end
      return {kind="mine",target=e,count=1,mode="deconstruct",clears=true}
    end
  end
end
DesignedStart.clear_footprint=clear_footprint

-- The game's own check, before anything is fetched or walked to (a player's cursor shows red before a trip, not after
-- six): what only a blueprint would clear is broken by hand; what no blueprint clears goes up the obstacle ladder now.
-- Its own character on the spot is the worker's to step off. Returns a job and meta, or nil and why it waits, or nil.
DesignedStart.PASSING={car=true,["spider-vehicle"]=true,locomotive=true,["cargo-wagon"]=true,["fluid-wagon"]=true,
  ["artillery-wagon"]=true}
DesignedStart.PASSING_WAIT=3600   -- ticks a spot only someone passing stands on is waited for, then the ladder
function DesignedStart.check_spot(npc,d,entity,tick)
  local c=npc.character
  local Ob=DesignedStart.Obstacles
  local box=entity_box(entity.prototype,entity.world,entity.direction)
  if not (c and c.valid and box) or Ob.on_spot(c,box) then return nil end
  local proto=prototypes.entity[entity.prototype]
  if not proto or tostring(proto.type):find("rail",1,true) then return nil end   -- (rails: the worker's own rules)
  if DesignedStart.fast_replaceable(npc,entity) then return nil end   -- (what stands there is replaced, not broken)
  local verdict=Ob.verdict(c.surface,entity.prototype,entity.world,entity.direction,npc.force)
  if verdict=="free" or verdict==nil then return nil end
  local ours=function(e) return in_plan(d,e) end
  if verdict=="clearable" then
    local e=Ob.breakable(npc,box,ours,entity.prototype)
    if e then
      local own=e.force==npc.force
      Ob.record(npc,d,entity,"break",e.type..":"..e.name,tick)
      return {kind="mine",target=e,count=1,mode=own and "deconstruct" or nil,clears=true},{clear=true,entity_id=entity.id}
    end
  end
  -- Only someone passing (anyone else's character or vehicle): waited out a while, not a rung of the ladder spent on it.
  local s=Ob.survey(npc,box,ours,entity.prototype)
  local passing=#s.characters>0
  for _,e in ipairs(s.kept) do
    if DesignedStart.PASSING[e.type] then passing=true else passing=false; break end
  end
  passing=passing and #s.water+#s.cliffs+#s.tiles==0
  if passing then
    entity.passing_since=entity.passing_since or tick
    if tick-entity.passing_since<DesignedStart.PASSING_WAIT then
      npc.phase="designed-start:waiting-for:"..entity.id
      return nil,"waiting_for_passing:"..entity.id
    end
  end
  entity.passing_since=nil
  -- (blocked; or clearable by nothing it may break by hand, a cliff: the ladder blasts, bridges, moves or hands back)
  local rung=DesignedStart.ladder(npc,d,entity,{reason="placement_blocked_or_out_of_reach",checked=verdict},tick)
  npc.phase="designed-start:in-the-way:"..entity.id
  return nil,"in_the_way:"..tostring(rung)
end

-- A supply that failed again and again, set aside for now (DesignedStart.on_receipt).
function DesignedStart.backed_off(npc,supply,tick)
  local b=npc.supply_backoff and supply.entity_id and supply.item and npc.supply_backoff[supply.entity_id..":"..supply.item]
  return b~=nil and tick<b.until_tick
end

-- How many of `item` it can hand over: of ammo, a stack's first magazine, if partly spent, stays with it (as the
-- worker's transfer keeps it). Counted whole, one spent magazine sent it to a turret every two seconds (run 44).
function DesignedStart.giveable(npc,item)
  local n=count(npc,item)
  local proto=prototypes.item[item]
  if n==0 or not (proto and proto.type=="ammo") then return n end
  local inv=npc.character.get_main_inventory()
  for i=1,#inv do
    local stack=inv[i]
    if stack.valid_for_read and stack.name==item and stack.ammo<proto.magazine_size then n=n-1 end
  end
  return math.max(0,n)
end

-- A turret running low: the best ammo for it (armory order) that it holds, else the best it can get
-- now, else what the plan named. Only ammo that fits beside what the turret still has (one slot).
local function ammo_job(npc,turret,inventory,planned,low,fill)
  local choices=Armory.ammo_for(turret.name,npc.force,function(item) return count(npc,item) end)
  local listed=false
  for _,item in ipairs(choices) do listed=listed or item==planned end
  if not listed then choices[#choices+1]=planned end
  local present,fits,rooms=0,{},{}
  for _,item in ipairs(choices) do
    present=present+inventory.get_item_count({name=item,quality="normal"})
    local room=try(function() return inventory.get_insertable_count({name=item,quality="normal"}) end)
    if room==nil or room>0 then fits[#fits+1]=item; rooms[item]=room end
  end
  if present>=low or #fits==0 then return nil end
  local want=fill-present
  -- (an artillery piece keeps its shells in an inventory of its own; never more than goes in: a part left over failed)
  local slot=turret.type=="artillery-turret" and defines.inventory.artillery_turret_ammo or defines.inventory.turret_ammo
  for _,item in ipairs(fits) do
    local n=DesignedStart.giveable(npc,item)
    if n>0 then
      return {kind="transfer",target=turret,item=item,count=math.max(1,math.min(want,n,rooms[item] or want)),
        mode="deposit",inventory=slot}
    end
  end
  -- A row of its own makes it (the defense supply): taken from what the row made, every output holding some in one
  -- sweep, and never crafted from plates by hand meanwhile; it waits on the row (a turret standing empty: a short wait,
  -- then its hands help, DesignedStart.machines_wait). The turret upkeep crafted magazines from plates a quarter to near
  -- half of its time (D-19, the user's U-1).
  local made=nil
  for _,item in ipairs(fits) do
    if DesignedStart.assemblers_make(npc,item)>0 then
      made=made or item
      local _,_,all=owned_output(npc,item)
      local outputs={}
      for _,o in ipairs(all or {}) do   -- (what a row made: an assembler, or a chest its own arm fills from one)
        if DesignedStart.made_into(npc,o.e) then outputs[#outputs+1]=o end
      end
      table.sort(outputs,function(a,b) return a.n>b.n end)
      local source=outputs[1] and outputs[1].e
      local available=outputs[1] and outputs[1].n or 0
      if source and available>0 then
        local take=math.max(want,DesignedStart.AMMO_BATCH)
        return DesignedStart.sweep_of(npc,{kind="transfer",target=source,item=item,count=math.min(available,take),
          mode="withdraw",up_to=true},DesignedStart.collect_more(outputs,source,take,math.min(available,take))),nil,item
      end
    end
  end
  -- (none made ready of any it takes: it waits on its row, the best first, Codex's review: never on the best while a
  -- lesser one stood made; but only while that row is at work and the turret still has some: an empty turret is an
  -- emergency, its magazines made by hand from plates as the bridge, then the row again. D-22: stage-silo's waves broke
  -- the wall while its turrets waited on their row, cd55ed0)
  if made and present>DesignedStart.AMMO_EMPTY and DesignedStart.assemblers_working(npc,made)>0 then
    local tick=game.tick
    DesignedStart.note_wait(npc,made,want,tick)
    if DesignedStart.machines_wait(npc,made,tick) then
      return nil,"assembling:"..made
    end
  end
  local why
  for _,item in ipairs(fits) do
    local job,reason=acquire(npc,item,want,0)
    if job then return job,nil,item end   -- (and what it is for: its ore mined is the ammunition's hand work)
    why=why or reason
  end
  return nil,why
end
DesignedStart.ammo_job=ammo_job
DesignedStart.AMMO_BATCH=50   -- magazines taken from its row at a time: one trip for a sweep of its turrets
DesignedStart.AMMO_EMPTY=2    -- magazines in a turret at or below which it never waits on its row (D-22)
-- Whether `e` holds what a machine made: an assembler's own output, or a chest an arm of its own fills from an
-- assembler; not a chest that feeds a row (its input, filled from a belt or by hand: Codex's D-19 review).
function DesignedStart.made_into(npc,e)
  if not (e and e.valid) then return false end
  if e.type=="assembling-machine" then return true end
  if e.type~="container" then return false end
  -- (an end chest: an arm of its own puts into it, from an assembler or the row's output belt, and none takes out of it
  -- into a machine; a row's input chest is taken from: Codex's review)
  local into=false
  for _,arm in ipairs(DesignedStart.fed_stores(npc).inserted[e.unit_number] or {}) do
    local p=arm.valid and arm.pickup_target
    if p and p.valid and (p.type=="assembling-machine" or p.type=="transport-belt" or p.type=="underground-belt") then
      into=true
    end
  end
  if not into then return false end
  local takes=try(function()
    for _,arm in ipairs(e.surface.find_entities_filtered{type="inserter",force=e.force,position=e.position,radius=1.6}) do
      local d=arm.pickup_target==e and arm.drop_target   -- (into a machine: its input; onto a belt, a line: still an end)
      if d and d.valid and (d.type=="assembling-machine" or d.type=="furnace") then return true end
    end
    return false
  end)
  return takes~=true
end

-- Machines of its own make it: once a drill of its own mines `item` (the opening's first pair, say), a shortage of it
-- or of what is smelted from it is never met by the engineer's hands, as a player stops mining ore once the drills run:
-- it waits on its machines and tells its planner (DesignedStart.note_wait). The NPC spent 21% of its time mining iron
-- ore at minute 39 where the player never mined ore (the user's recorded play, 2026-10-28). Items rocks or trees give
-- (stone, coal, wood) are gathered from them as ever.
DesignedStart.DRILLS_LOOK=600
DesignedStart.MACHINE_WAIT=18000   -- ticks waited on its machines before the hands help after all (logged)
DesignedStart.DEAD_WAIT=1800       -- the same where none of its drills leaves the item anywhere to take (a dead wait)
-- (a drill that runs, or stands full: one out of fuel or power, or mining nothing, is no source)
function DesignedStart.producing(e)
  local s=try(function() return e.status end)
  local st=defines.entity_status or {}
  -- (one out of fuel too: its burner is fuelled, the engineer does not mine in its place)
  return s==nil or s==st.working or s==st.low_power or s==st.waiting_for_space_in_destination or s==st.full_output
    or s==st.no_fuel
end
function DesignedStart.looked(npc,tick)
  local seen=npc.drills_seen
  if seen and tick-seen.tick<DesignedStart.DRILLS_LOOK then return seen end
  seen={tick=tick,items={},smelts={}}
  local c=npc.character
  local at=npc.home or (c and c.valid and c.position)
  local function find(kind)
    return c and c.valid and at and try(function()
      -- (as far as any plan of its own may reach: the team world's go twice PLAN_REACH)
      return c.surface.find_entities_filtered{type=kind,force=npc.force,position=at,radius=2*PLAN_REACH}
    end) or {}
  end
  seen.loose={}
  for _,d in ipairs(find("mining-drill")) do
    local target=d.valid and DesignedStart.producing(d) and try(function() return d.mining_target end)
    -- (what it leaves where it can be taken: on the ground, in a chest, on a belt; into a machine it is not)
    local into=target and try(function() return d.drop_target end)
    -- (into a chest only, the kind acquire takes raw items from (owned_output); off the ground or a belt it does not)
    local takeable=into and into.valid and into.type=="container"
    for _,p in ipairs(target and try(function() return target.prototype.mineable_properties.products end) or {}) do
      if p.name then
        seen.items[p.name]=(seen.items[p.name] or 0)+1
        if takeable then seen.loose[p.name]=(seen.loose[p.name] or 0)+1 end
      end
    end
  end
  -- (furnaces of its own smelting each item now, or last: what makes it, for `none`)
  for _,f in ipairs(find("furnace")) do
    local r=f.valid and (try(function() return f.get_recipe() end) or try(function() return f.previous_recipe end))
    local name=r and (r.name and (type(r.name)=="string" and r.name or r.name.name))
    local recipe=name and prototypes.recipe[name]
    for _,p in ipairs(recipe and recipe.products or {}) do
      if p.name then seen.smelts[p.name]=(seen.smelts[p.name] or 0)+1 end
    end
  end
  -- (assemblers of its own set to make each item: its defense row's magazines, D-19)
  seen.assembles={}
  for _,a in ipairs(find("assembling-machine")) do
    local r=a.valid and try(function() return a.get_recipe() end)
    for _,p in ipairs(r and r.products or {}) do
      if p.name then
        local list=seen.assembles[p.name] or {}
        list[#list+1]=a
        seen.assembles[p.name]=list
      end
    end
  end
  npc.drills_seen=seen
  return seen
end
-- Assemblers of its own near home set to make `item` that run now (or stand full): unpowered or short of what goes in,
-- one makes nothing and the hands help (looked at now, not as when the list was made: Codex's review, D-19).
function DesignedStart.assemblers_make(npc,item,tick)
  local st=defines.entity_status or {}
  local n=0
  for _,a in ipairs((DesignedStart.looked(npc,tick or game.tick).assembles or {})[item] or {}) do
    local s=a.valid and try(function() return a.status end)
    if a.valid and (s==nil or s==st.working or s==st.low_power or s==st.full_output
        or s==st.waiting_for_space_in_destination) then n=n+1 end
  end
  return n
end
-- Those of them at work now (crafting, not stood full or starved): a row that makes them, not one that only could.
function DesignedStart.assemblers_working(npc,item,tick)
  local st=defines.entity_status or {}
  local n=0
  for _,a in ipairs((DesignedStart.looked(npc,tick or game.tick).assembles or {})[item] or {}) do
    local s=a.valid and try(function() return a.status end)
    if a.valid and (s==nil or s==st.working or s==st.low_power) then n=n+1 end
  end
  return n
end
-- Drills of its own near home mining `item` and running.
function DesignedStart.drills_make(npc,item,tick)
  return DesignedStart.looked(npc,tick or game.tick).items[item] or 0
end
-- Furnaces of its own near home making `item` (now or last).
function DesignedStart.furnaces_make(npc,item,tick)
  return DesignedStart.looked(npc,tick or game.tick).smelts[item] or 0
end
-- Whether the hands still wait on the machines for `item`: true until it has waited MACHINE_WAIT in a row; then (once,
-- logged) its hands help, as a rescue, until the wait ends.
function DesignedStart.machines_wait(npc,item,tick,limit)
  local w=npc.plate_wait and npc.plate_wait.items[item]
  local waited=w and w.last and tick-w.last<=DesignedStart.WAIT_GAP and tick-(w.since or tick) or 0
  if waited<(limit or DesignedStart.MACHINE_WAIT) then return true end
  if not w.rescued then
    w.rescued=true
    log("[npc-factions] npc="..npc.id.." waited "..math.floor(waited/60).." s on its machines for "..item
      ..": by hand meanwhile")
  end
  return false
end
-- Trees or rocks giving `item` stand within NATURAL_NEAR of the engineer (then it may be gathered from them by hand,
-- never from its patch: the mine job's natural_only). Space Age's rocks give iron ore: counted as a rock's item anywhere,
-- iron ore was mined from its patch by hand 20 to 64% of the time while its drills ran (long-61, 2026-10-28).
DesignedStart.NATURAL_NEAR=48
DesignedStart.AVOID_NEAR=12   -- tiles round a piece whose unbuilt neighbours its builder's stand point keeps out of
DesignedStart.AVOID_MAX=24
DesignedStart.NATURAL_BATCH=20   -- what one gathering by hand from trees or rocks takes at the least
DesignedStart.NATURAL_BACKOFF=1800   -- ticks after a gathering of it failed (none it could get to) before it tries again
function DesignedStart.natural_near(npc,item,tick)
  local back=npc.natural_backoff and npc.natural_backoff[item]
  if back and (tick or game.tick)<back then return false end
  local names=DesignedStart.Obstacles.natural_names(item)
  local c=npc.character
  if #names==0 or not (c and c.valid) then return false end
  for _,e in ipairs(try(function()
      return c.surface.find_entities_filtered{name=names,position=c.position,radius=DesignedStart.NATURAL_NEAR,
        force="neutral",limit=8}
    end) or {}) do
    if e.valid and e.minable then return true end   -- (one the worker may take: as its own mine job's rule)
  end
  return false
end
-- What the engineer waits on its machines for, lately (for the planner: export plate_wait, agreed with NPC Factory
-- 2026-10-28): ticks waited (halved every TALLY_HALF, as time_use), how many it wanted, since when, and `none` when no
-- machine of its own makes it at all (a missing source, answered first). Only time spent waiting counts: a wait seen
-- again within WAIT_GAP ticks runs on.
DesignedStart.WAIT_GAP=120
-- (halved every TALLY_HALF since `since`, its rows' ticks and items; ten halvings at most, a longer gap starts afresh)
function DesignedStart.age(t,key,tick)
  if tick-t.since>10*DesignedStart.TALLY_HALF then t[key]={}; t.since=tick; return end
  while tick-t.since>=DesignedStart.TALLY_HALF do
    for _,r in pairs(t[key]) do r.ticks=r.ticks/2; if r.items then r.items=r.items/2 end end
    t.since=t.since+DesignedStart.TALLY_HALF
  end
end
function DesignedStart.note_wait(npc,item,want,tick,none)
  local W=npc.plate_wait
  if not W or tick<W.since then W={since=tick,items={}}; npc.plate_wait=W end
  DesignedStart.age(W,"items",tick)
  local w=W.items[item] or {ticks=0}
  if w.last and tick-w.last<=DesignedStart.WAIT_GAP then w.ticks=w.ticks+(tick-w.last) else w.since=tick end
  if not (w.last and tick-w.last<=DesignedStart.WAIT_GAP) then w.rescued=nil end
  w.last,w.want,w.none=tick,want,none or nil
  W.items[item]=w
end
function DesignedStart.export_wait(npc)
  if npc.plate_wait then DesignedStart.age(npc.plate_wait,"items",game.tick) end
  local out,any={},false
  for item,w in pairs(npc.plate_wait and npc.plate_wait.items or {}) do
    if w.ticks>=1 then out[item]={ticks=math.floor(w.ticks),want=w.want,since=w.since,none=w.none}; any=true end
  end
  return any and out or nil
end
-- Hand fuelling per plan, lately (export hand_fuel, a planner input): ticks of fuel carried and fetched by hand for each
-- plan's burners, and how much went in, halved every TALLY_HALF. The NPC still carried coal 25% of its time at minute
-- 162 where the player stopped at 49 (the recorded play): a plan that costs this is one to rework or retire.
function DesignedStart.note_hand_fuel(npc,purpose,ticks,items,tick)
  local H=npc.hand_fuel
  if not H or tick<H.since then H={since=tick,plans={}}; npc.hand_fuel=H end
  DesignedStart.age(H,"plans",tick)
  local p=H.plans[purpose] or {ticks=0,items=0}
  p.ticks,p.items=p.ticks+ticks,p.items+items
  H.plans[purpose]=p
end
function DesignedStart.export_hand_fuel(npc)
  if npc.hand_fuel then DesignedStart.age(npc.hand_fuel,"plans",game.tick) end
  local out,any={},false
  for purpose,p in pairs(npc.hand_fuel and npc.hand_fuel.plans or {}) do
    if p.ticks>=1 then out[purpose]={ticks=math.floor(p.ticks),items=math.floor(p.items)}; any=true end
  end
  return any and out or nil
end

-- Hand logistics as sweeps (the user's even-distribution mod, 2026-10-28: "hold ctrl and collect all the coal from the
-- loop, then hold ctrl again and distribute it among the furnaces and drills within seconds"): a transfer becomes one
-- sweep over every other entity near it that gives or takes the same, from where it stands (the worker's sweep job), as
-- far as it reaches (far reach, W-14). SWEEP_MAX entities a sweep at most.
DesignedStart.SWEEP_MAX=24
-- `job`: the first transfer; `more`: further candidates, {target, count, inventory} each (they are checked here: same
-- item, near it, what it holds for a deposit). Returns a sweep, or `job` itself when nothing joins it.
function DesignedStart.sweep_of(npc,job,more)
  if not (job and job.kind=="transfer" and job.target and job.target.valid) then return job end
  local R=DesignedStart.reach(npc)
  local at=job.target.position
  local entries={{target=job.target,count=job.count,inventory=job.inventory}}
  local seen={[job.target.unit_number or job.target]=true}
  local held=job.mode=="deposit" and DesignedStart.giveable(npc,job.item)-job.count or math.huge
  for _,m in ipairs(more or {}) do
    if #entries>=DesignedStart.SWEEP_MAX then break end
    local e=m.target
    local key=e and e.valid and (e.unit_number or e)
    if key and not seen[key] and (m.count or 0)>=1 and (e.position.x-at.x)^2+(e.position.y-at.y)^2<=R*R then
      local n=job.mode=="deposit" and math.min(m.count,held) or m.count
      if n>=1 then
        seen[key]=true
        entries[#entries+1]={target=e,count=n,inventory=m.inventory}
        if job.mode=="deposit" then held=held-n end
      end
    end
  end
  if #entries<2 then return job end
  return {kind="sweep",item=job.item,mode=job.mode,entries=entries,deadline=job.deadline}
end

-- More stores to collect from in the same sweep (`list`: {e, n, inventory} each, the first one taken already): up to
-- `need` in all, as much as each holds.
function DesignedStart.collect_more(list,first,need,first_count)
  local more,left={},need-(first_count or 0)
  for _,s in ipairs(list or {}) do
    if left<1 then break end
    if s.e~=first and s.e.valid and (s.n or 0)>0 then
      local n=math.min(s.n,left)
      more[#more+1]={target=s.e,count=n,inventory=s.inventory}
      left=left-n
    end
  end
  return more
end

-- How many of `item` it can build with: what it holds, less what a build found it could not use while it still holds
-- just that (on_receipt); after UNUSABLE_FOR, or once what it holds changes, all of it counts again.
DesignedStart.UNUSABLE_FOR=18000
function DesignedStart.usable(npc,item,tick)
  local n=count(npc,item)
  local u=npc.unusable and npc.unusable[item]
  if u then
    if n~=u.held or (tick or game.tick)-u.tick>DesignedStart.UNUSABLE_FOR then npc.unusable[item]=nil
    else return 0,n end
  end
  return n,0
end

-- Every turret of its own that takes ammo, in a plan or not (one a dropped plan left standing, its kit's, a gift, one
-- a mod adds: what fits it comes from the game's ammo categories, Armory.ammo_for), kept loaded where it matters: the
-- turrets biters are coming at filled up, the rest topped up once low. Only where the engineer can go without walking
-- into them (out of their sight: combat_odds' NOTICE and MARGIN, or the standoff of the wave it watches). The engineer
-- carried 200 magazines while four turrets stood empty for 165 s and were lost: their ammo was only a plan's supply,
-- topped up below 5, and nothing of upkeep ran while it stood off from the wave (the proving ground, 2026-10-27).
-- `urgent`: only turrets biters are at or coming to. (job, meta) or nil.
DesignedStart.Odds=require("scripts.combat_odds")
DesignedStart.TURRET_REACH=PLAN_REACH   -- tiles from home its turrets are looked for in (as far as its plans go)
DesignedStart.TURRET_LOOK=300    -- ticks between looks for them
DesignedStart.THREAT_NEAR=32     -- biters this much beyond a turret's range are coming to it
DesignedStart.ATTACK_RECENT=3600 -- an attack on its plot this recent: every turret is filled up
DesignedStart.TURRET_BACKOFF=600 -- ticks a turret it failed to load waits
DesignedStart.TURRET_FULL=100    -- magazines a turret is filled to at most (what it carries, shared out)
function DesignedStart.defense_supply(npc,tick,urgent)
  local c=npc.character
  if not (c and c.valid) then return nil end
  local surface=c.surface
  local seen=npc.turrets_seen
  if not seen or tick-seen.tick>=DesignedStart.TURRET_LOOK then
    local at=npc.home or c.position
    seen={tick=tick,list=try(function()
      return surface.find_entities_filtered{position=at,radius=DesignedStart.TURRET_REACH,force=npc.force,
        type={"ammo-turret","artillery-turret"}}
    end) or {}}
    npc.turrets_seen=seen
  end
  local newest=TeamWorld.active() and TeamWar.newest and TeamWar.newest(npc)
  local attacked=type(newest)=="number" and tick-newest<=DesignedStart.ATTACK_RECENT
  local w=npc.combat and npc.combat.wave
  local O=DesignedStart.Odds
  local keep_off=(w and (w.keep or w.standoff)) or (O.NOTICE+O.MARGIN)   -- (as far as combat keeps clear: O-6)
  local here=c.position
  local due={}
  -- How full: what it carries that fits, shared over its turrets, at least AMMO_FILL. Twenty a turret ran dry inside a
  -- wave while 200 sat in its pocket; the turrets started with 25, and none was ever below the line to top up.
  local share={}
  local function full(t)
    if share[t.name]==nil then
      local held=0
      for _,item in ipairs(Armory.ammo_for(t.name,nil,nil)) do held=held+DesignedStart.giveable(npc,item) end
      share[t.name]=math.max(AMMO_FILL,math.min(DesignedStart.TURRET_FULL,math.floor(held/math.max(1,#seen.list))))
    end
    return share[t.name]
  end
  for _,t in ipairs(seen.list) do
    local inv=t.valid and try(function()
      return t.get_inventory(t.type=="artillery-turret" and defines.inventory.artillery_turret_ammo
        or defines.inventory.turret_ammo)
    end)
    local back=inv and npc.turret_backoff and npc.turret_backoff[t.unit_number]
    if back and tick>=back then npc.turret_backoff[t.unit_number]=nil; back=nil end
    -- (its line's inserter brings it ammunition: no hands; by hand only a new one, the kit's, or a line run dry: D-19)
    if inv and not back and t.type=="ammo-turret" then
      for _,item in ipairs(Armory.ammo_for(t.name,nil,nil)) do
        if DesignedStart.auto_fed(npc,t,item) then back=true break end
      end
    end
    if inv and not back then
      local n=inv.get_item_count()
      local range=try(function() return t.prototype.attack_parameters.range end) or 18
      local coming=try(function()
        return surface.count_entities_filtered{position=t.position,radius=range+DesignedStart.THREAT_NEAR,force="enemy",
          type="unit",limit=1}
      end) or 0
      local threatened=coming>0 or attacked
      local fill=full(t)
      -- (calm: once down to well under its fill; biters coming: filled up)
      if (threatened or not urgent) and n<(threatened and fill or math.max(AMMO_LOW,math.floor(fill*0.6))) then
        -- (biters at it now: not walked into; its odds say when it goes in, combat.lua)
        local at_it=try(function()
          return surface.count_entities_filtered{position=t.position,radius=keep_off,force="enemy",type="unit",limit=1}
        end) or 1
        if at_it==0 then
          local dx,dy=t.position.x-here.x,t.position.y-here.y
          due[#due+1]={turret=t,inv=inv,n=n,threatened=threatened,d=dx*dx+dy*dy,fill=fill}
        end
      end
    end
  end
  table.sort(due,function(a,b)
    if (a.n==0)~=(b.n==0) then return a.n==0 end
    if a.threatened~=b.threatened then return a.threatened end
    if a.n~=b.n then return a.n<b.n end
    return a.d<b.d
  end)
  for i,f in ipairs(due) do
    local job,_,item=ammo_job(npc,f.turret,f.inv,nil,f.fill,f.fill)
    if job then
      -- (the other turrets near it that are due, the same magazines, in one sweep)
      if job.kind=="transfer" and job.mode=="deposit" then
        local more={}
        for k=i+1,#due do
          local g=due[k]
          local j2=ammo_job(npc,g.turret,g.inv,nil,g.fill,g.fill)
          if j2 and j2.kind=="transfer" and j2.mode=="deposit" and j2.item==job.item then
            more[#more+1]={target=j2.target,count=j2.count,inventory=j2.inventory}
          end
        end
        job=DesignedStart.sweep_of(npc,job,more)
      end
      return job,{upkeep="turret_ammo",urgent=f.threatened or nil,target=f.turret.unit_number,item=item}
    end
  end
end

-- A machine fed by hand is there to make something for the engineer, so holding plenty of what it makes
-- already serves it: it is not fed, and its stage's output witness counts as met. A reused layout's
-- bootstrap furnace was hand fed mined ore and chopped wood for 9 plates while the engineer carried 228,
-- seven game minutes (one-engineer hour, 2026-10-24). Its product: its recipe, or a furnace's last one.
-- (A field, not a local: the file's main chunk is at Lua's limit of 200 locals.)
function DesignedStart.held_plenty(npc,e)
  if not (e and e.valid) then return false end
  local r=try(function() return e.get_recipe() end)
  if not r and e.type=="furnace" then
    local prev=try(function() return e.previous_recipe end)
    local name=prev and prev.name
    if type(name)~="string" then name=try(function() return name.name end) end
    r=type(name)=="string" and prototypes.recipe[name] or nil
  end
  for _,p in ipairs(r and r.products or {}) do
    if p.type~="fluid" and count(npc,p.name)>=SHORT_OF then return true end
  end
  return false
end

-- Burners are fuelled by how long their fuel lasts, not by a count, and the fuel for every burner due soon
-- comes in one trip and is handed round while it is in hand. One machine a trip made the trips grow with
-- every burner: about 40 of them kept a one-engineer base fetching coal for an hour (Codex's review,
-- 2026-10-25). In seconds at full draw (a big burner, a boiler, holds less than these: then a share of a fill):
-- LOW, the next trip is due; URGENT, while building (only what is about to stop); SWEEP, what fuel in hand
-- tops up; ROUND, ticks a round of handing it out lasts; BATCH, the most fetched for a round.
-- A round collects from its stores what the burners due want, then splits it over them (DesignedStart.split_fuel).
-- Fuel gathered by hand is gathered for now, not for the fill: HAND, the seconds it keeps each burner due running;
-- START, the pieces a cold burner gets that its own drills fuel (a coal loop's drills feed each other: one piece, taken
-- into its burner at once, leaves the slot free for their coal); TRIP, the least worth a trip to its stores, unless a
-- burner is about to stop; TREES, tiles round it that trees count as near. Filling every burner to a stack by hand had
-- three quarters of the engineer's first eight minutes go to coal, the coal loop built and making it meanwhile
-- (trace 2026-10-26).
DesignedStart.WAITING_FOR=18000   -- ticks a building waits on an item nothing of its own makes before its plan goes
-- GIVE, the least handed to a burner or taken from a store in a round (one piece a burner, a piece or two from a store,
-- had it walking to and fro with single coals while its loop could barely keep up, trace 2026-10-26: a burner left
-- standing a few seconds costs less).
DesignedStart.FUEL={LOW=180,URGENT=45,SWEEP=600,LOW_SHARE=0.25,SWEEP_SHARE=0.5,ROUND=7200,BATCH=200,HAND=300,START=1,
  TRIP=10,TREES=48,GIVE=3,NEAR=32}   -- (NEAR: tiles from it a store may stand to collect from it in a round)
-- Fuel gathered by hand while no store of its own has the burner's fuel coming, the first one a burner burns and that
-- stands near: wood in the first furnace and drill, never coal mined by hand (the user, 2026-10-26).
DesignedStart.HAND_FUELS={"wood","coal"}

-- Seconds its fuel lasts at full draw, and seconds a fill lasts; nil when the game cannot say.
-- What a burner really burns, joules a second: a moving average of how its stored energy (burning and in its slot) fell
-- between looks a second or more apart, a refill only starting the next look; within a quarter of its full draw and the
-- full draw. Assumed at full draw, an idle furnace looked due as often as one at work, and every trip to it was early
-- (the Workers Union review, 2026-10-29: their routes.lua:619-663, MIT, the idea taken, no code). Kept in storage by
-- unit number, for every engineer alike.
DesignedStart.BURN_FLOOR=0.25
DesignedStart.FUEL_NEXT_MIN=300    -- ticks a burner not due waits before it is looked at again, at the least
DesignedStart.FUEL_NEXT_MAX=7200   -- and at the most
DesignedStart.BURN_LOOK=60
DesignedStart.BURN_KEPT=4000
function DesignedStart.measured_draw(e,energy,full)
  local id=e and e.unit_number
  if not (id and storage and full and full>0) then return full end
  local seen=storage.burn_seen
  if not seen then seen={n=0,units={}}; storage.burn_seen=seen end
  local m=seen.units[id]
  local tick=game.tick
  if not m then
    if seen.n>=DesignedStart.BURN_KEPT then seen.units,seen.n={},0 end   -- (a long game's gone burners: started over)
    seen.units[id]={tick=tick,energy=energy}; seen.n=seen.n+1
    return full
  end
  if energy and tick-m.tick>=DesignedStart.BURN_LOOK then
    if energy<=m.energy then
      local sample=(m.energy-energy)/((tick-m.tick)/60)
      m.rate=m.rate and (0.7*m.rate+0.3*sample) or sample
    end
    m.tick,m.energy=tick,energy
  end
  if not m.rate then return full end
  return math.max(full*DesignedStart.BURN_FLOOR,math.min(full,m.rate))
end

function DesignedStart.fuel_time(target,inventory,item)
  local per_tick=try(function() return target.prototype.get_max_energy_usage() end)
  local value=try(function() return prototypes.item[item].fuel_value end)
  local contents=try(function() return inventory.get_contents() end)
  if not (type(per_tick)=="number" and per_tick>0 and type(value)=="number" and value>0 and contents) then return nil end
  local energy=try(function() return target.burner.remaining_burning_fuel end) or 0
  for _,stack in ipairs(contents) do
    energy=energy+(try(function() return prototypes.item[stack.name].fuel_value end) or 0)*(stack.count or 0)
  end
  local full=per_tick*60
  local per_second=DesignedStart.measured_draw(target,energy,full)
  -- (near empty, judged at full draw all the same: an idle one put to work burns at full draw, and a boiler a busy
  -- night wakes would run dry on the measured idle rate)
  if energy/full<DesignedStart.FUEL.LOW then per_second=full end
  return energy/per_second,FUEL_FILL*value/per_second,per_second   -- (and the rate it was judged at)
end

-- The fuel that keeps a burner running `horizon` seconds at full draw, less what it has; nil when the game cannot say.
function DesignedStart.fuel_share(target,inventory,item,horizon)
  local left,_,per_second=DesignedStart.fuel_time(target,inventory,item)   -- (the rate its time left was judged at)
  local value=try(function() return prototypes.item[item].fuel_value end)
  if not (left and type(per_second)=="number" and type(value)=="number" and value>0) then return nil end
  return math.max(0,math.ceil((horizon-left)*per_second/value))
end

-- Stores its own drills fill (a coal loop's drills, a chest drills drop into): what is short in one now comes in a
-- while without its hands. Looked over every FED_CHECK ticks: the items such stores get from drills that run (`items`:
-- a coal chest whose drills all ran dry has nothing coming, and fuel is gathered by hand again), and the stores
-- whatever their drills do (`stores`: a loop's drill gone cold is still one its partner fuels). And the machines its
-- own inserters drop into, by unit number, with what they take from (`inserted`: DesignedStart.auto_fed).
DesignedStart.FED_CHECK=600
function DesignedStart.fed_stores(npc)
  local c=npc.fed_stores
  if c and game.tick>=c.tick and game.tick<c.tick+DesignedStart.FED_CHECK and c.inserted and c.drilled then return c end
  c={tick=game.tick,items={},stores={},inserted={},drilled={}}
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan and plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="inserter" then
        local into=e.drop_target
        if into and into.valid and into.unit_number then
          local list=c.inserted[into.unit_number] or {}
          list[#list+1]=e   -- (the arm itself: what it takes from, and whether it can, is looked at as asked)
          c.inserted[into.unit_number]=list
        end
      end
      local drop=e and e.valid and e.type=="mining-drill" and e.drop_target
      -- (a machine a drill of its own drops straight into, with what: its ore needs no hands; DesignedStart.drill_fed)
      if drop and drop.valid and drop.unit_number and DesignedStart.producing(e) then   -- (one at work: not mined out)
        for _,p in ipairs(try(function() return e.mining_target.prototype.mineable_properties.products end) or {}) do
          local set=c.drilled[drop.unit_number] or {}
          set[p.name]=true
          c.drilled[drop.unit_number]=set
        end
      end
      if drop and drop.valid and (drop.type=="mining-drill" or drop.type=="container") then
        local runs=not e.burner or (try(function() return e.burner.remaining_burning_fuel end) or 0)>0
          or try(function() return #e.get_fuel_inventory().get_contents()>0 end)==true
        for _,p in ipairs(try(function()   -- (what it mines now, else the first resource under it)
            local r=e.mining_target
            if not (r and r.valid) then
              local radius=e.prototype.get_mining_drill_radius()
              r=e.surface.find_entities_filtered{type="resource",limit=1,
                area={{e.position.x-radius,e.position.y-radius},{e.position.x+radius,e.position.y+radius}}}[1]
            end
            return r and r.prototype.mineable_properties.products
          end) or {}) do
          if runs then c.items[p.name]=true end
          if drop.unit_number then c.stores[drop.unit_number..":"..p.name]=true end
        end
      end
    end
  end
  npc.fed_stores=c
  return c
end

-- Whether a drill of its own drops `item` straight into `e` (a burner drill over its furnace): a hand supply of it there
-- does nothing the drill does not, and waiting to fetch it waits forever, for the drill leaves none loose (long-61's
-- iron expansion waited 9 minutes for ore to carry to furnaces their own drills fed, 2026-10-29).
function DesignedStart.drill_fed(npc,e,item)
  local set=e and e.valid and e.unit_number and DesignedStart.fed_stores(npc).drilled[e.unit_number]
  return set~=nil and set[item]==true
end

-- Whether `e` gets `item` from inserters of its own: one drops into it from a belt, a chest or a machine that has the
-- item now. Such a machine is fed by hand only once it runs out (DesignedStart.supply_job), a safety net for a
-- blackout that stops the electric drills and inserters: run 57's bus power row, belt-fed, its coal drills backed up,
-- read as low with the dozen coal an inserter keeps in a boiler, had the engineer walk 210 tiles to top it up
-- (NPC Factory's analysis, 2026-10-26). What a source holds is looked at as it is asked.
-- An arm counts while it still drops into `e`, is not stopped (no power, switched off by a circuit or a script), its
-- filter (if any) lets the item through, and what it takes from now has the item.
DesignedStart.ARM_STOPPED={"no_power","no_fuel","disabled","frozen","disabled_by_control_behavior","disabled_by_script",
  "marked_for_deconstruction"}
function DesignedStart.arm_passes(arm,e,item)
  return try(function()
    if not (arm.valid and arm.drop_target==e) or arm.active==false then return false end
    local s,statuses=arm.status,defines.entity_status or {}
    for _,name in ipairs(DesignedStart.ARM_STOPPED) do
      if s~=nil and statuses[name]~=nil and s==statuses[name] then return false end
    end
    if arm.use_filters then
      local listed=false
      for i=1,arm.filter_slot_count or 0 do
        local f=arm.get_filter(i)
        local name=type(f)=="table" and f.name or f
        if name==item then listed=true end
      end
      if (arm.inserter_filter_mode=="blacklist")==listed then return false end
    end
    return true
  end)==true
end
function DesignedStart.auto_fed(npc,e,item)
  local arms=e and e.valid and e.unit_number and DesignedStart.fed_stores(npc).inserted[e.unit_number]
  for _,arm in ipairs(arms or {}) do
    local p=DesignedStart.arm_passes(arm,e,item) and arm.pickup_target
    if p and p.valid and try(function()
          if p.type=="transport-belt" or p.type=="underground-belt" or p.type=="splitter" or p.type=="loader"
              or p.type=="loader-1x1" or p.type=="linked-belt" or p.type=="lane-splitter" then
            for line=1,p.get_max_transport_line_index() do
              if p.get_transport_line(line).get_item_count(item)>0 then return true end
            end
            return false
          end
          local inv=(p.type=="container" or p.type=="logistic-container" or p.type=="infinity-container")
            and p.get_inventory(defines.inventory.chest) or p.get_output_inventory()
          return inv~=nil and inv.get_item_count({name=item,quality="normal"})>0
        end)==true then
      return true
    end
  end
  return false
end

-- A burner making power (a boiler) that its own inserters are there to feed while their source is empty (its coal line
-- not done yet, or broken) waits for that feed: hand fuel only while the base is short of power or no other power
-- burner of its own burns on a power network known to be the same (a blackout, or one it cannot tell). Its feed: an
-- arm that could bring the fuel (DesignedStart.arm_passes: not stopped, its filter letting it through). Run 60
-- carried coal by hand to four new bus boilers for an hour while their coal line was being built, then broken, the base
-- never short of power: every coal of it only spared the belt-fed boilers theirs (2026-10-26).
DesignedStart.POWER_BURNERS={boiler=true,["burner-generator"]=true}
-- The power network a power burner gives to: its own (a burner generator), else that of a steam engine or turbine of
-- its force beside it; nil when none is found (then no other burner is taken to stand in for it).
function DesignedStart.power_net(e)
  return try(function()
    if e.electric_network_id then return e.electric_network_id end
    for _,g in ipairs(e.surface.find_entities_filtered{type="generator",position=e.position,radius=6,force=e.force}) do
      if g.electric_network_id then return g.electric_network_id end
    end
  end)
end
function DesignedStart.waits_for_feed(npc,e,item)
  if not (e and e.valid and e.unit_number and DesignedStart.POWER_BURNERS[e.type]) then return false end
  local armed=false
  for _,arm in ipairs(DesignedStart.fed_stores(npc).inserted[e.unit_number] or {}) do
    if DesignedStart.arm_passes(arm,e,item) then armed=true end
  end
  if not armed or Corrector.power_short(npc,game.tick) then return false end
  local seen=npc.power_burning   -- (power burners of its own that burn, with their networks, looked over once a tick)
  if not (seen and seen.tick==game.tick) then
    seen={tick=game.tick,list={}}
    local plans={npc.design and npc.design.plan}
    for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
    for _,plan in ipairs(plans) do
      for _,id in ipairs(plan.order or {}) do
        local o=plan.entities[id].entity
        if o and o.valid and DesignedStart.POWER_BURNERS[o.type]
            and (try(function() return o.burner.remaining_burning_fuel end) or 0)>0 then
          seen.list[#seen.list+1]={e=o,net=DesignedStart.power_net(o)}
        end
      end
    end
    npc.power_burning=seen
  end
  local net=DesignedStart.power_net(e)
  for _,o in ipairs(seen.list) do
    if o.e.valid and o.e~=e and net~=nil and o.net==net then return true end
  end
  return false
end

-- A machine's supply, fetched where it costs the engineer least: `need` from a store (a chest, its coal loop: one
-- trip), but by hand only `now`, what the machine needs for a while. Mining by hand is the slowest way to anything,
-- and what it mines past that ties its hands while its drills would make it. Unless the machine is about to stop
-- (`critical`), nothing yet while a store its drills fill is filling: neither a trip there for less than TRIP nor
-- mining by hand meanwhile.
function DesignedStart.supply_fetch(npc,item,need,now,critical)
  local job,why,kind=acquire(npc,item,need,0)
  if not job then return job,why,kind end
  if job.kind=="mine" and job.item==item and not job.target then
    if not critical and DesignedStart.fed_stores(npc).items[item] then return nil end
    local n=math.min(job.count or 1,now-count(npc,item))
    if n<1 then return nil end
    job.count=n
  elseif job.kind=="transfer" and job.mode=="withdraw" and job.item==item and not critical
      and (job.count or 0)<math.min(need,DesignedStart.FUEL.TRIP)
      and job.target and job.target.valid and job.target.unit_number
      and DesignedStart.fed_stores(npc).stores[job.target.unit_number..":"..item] then
    return nil
  end
  return job,why,kind
end

-- Energy (J) of `item` as fuel, 0 if it is none; what burner `e` burns a second at full draw (J), nil if unknown.
function DesignedStart.fuel_value(item)
  local v=try(function() return prototypes.item[item].fuel_value end)
  return type(v)=="number" and v or 0
end
function DesignedStart.burn_rate(e)
  local per_tick=try(function() return e.prototype.get_max_energy_usage() end)
  if not (type(per_tick)=="number" and per_tick>0) then return nil end
  local m=e.unit_number and storage and storage.burn_seen and storage.burn_seen.units[e.unit_number]
  local full=per_tick*60
  if m and m.rate then return math.max(full*DesignedStart.BURN_FLOOR,math.min(full,m.rate)) end   -- (as it burns)
  return full
end

-- Whether burner `target` burns `item` (its fuel categories) and its fuel slots have room for it now (one slot that
-- holds another fuel takes none until that is burnt).
function DesignedStart.takes_fuel(target,inventory,item)
  if DesignedStart.fuel_value(item)<=0 then return false end
  local fits=try(function()   -- (the item's categories, a list; the burner's, a set: as scripts/combat_equipment.lua)
    local cats=target.burner and target.burner.fuel_categories
    local own=prototypes.item[item].fuel_categories
    if not (cats and own) then return true end
    for _,c in ipairs(own) do if cats[c] then return true end end
    return false
  end)
  if fits==false then return false end
  return try(function() return inventory.can_insert({name=item,quality="normal"}) end)~=false
end

-- The fuel it carries that `target` takes, and how much: its planned fuel first, then what is gathered by hand, but
-- that only while no store of its own has the planned fuel coming. Wood it carried for poles went into burners already
-- burning wood, one walk each, its coal loop standing, and it chopped more for the poles (trace 2026-10-26).
function DesignedStart.fuel_in_hand(npc,target,inventory,item)
  local names={item}
  if not DesignedStart.fed_stores(npc).items[item] then
    for _,n in ipairs(DesignedStart.HAND_FUELS) do if n~=item then names[#names+1]=n end end
  end
  for _,n in ipairs(names) do
    local held=count(npc,n)
    if held>0 and DesignedStart.takes_fuel(target,inventory,n) then return n,held end
  end
  return nil,0
end

-- Its stores of `item` to take fuel from, the fullest first, and what they hold in all: its chests (not a lane it
-- feeds by hand) and its coal loops' fuel slots above what keeps them running (LOOP_RESERVE).
function DesignedStart.fuel_stores(npc,item)
  local cached=npc.fuel_stores_seen   -- (once a tick: every burner due asks)
  if cached and cached.tick==game.tick and cached.item==item then return cached.out,cached.total end
  local out,total,seen={},0,{}
  local function add(e,n,inventory)
    local k=e.unit_number or e
    if n>0 and not seen[k] then seen[k]=true; out[#out+1]={e=e,n=n,inventory=inventory}; total=total+n end
  end
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    local fed={}
    for _,stage in ipairs(plan and plan.stages or {}) do
      for _,supply in ipairs(stage.supplies or {}) do fed[supply.entity_id]=true end
    end
    for _,id in ipairs(plan and plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="container" and not fed[id] then
        add(e,e.get_inventory(defines.inventory.chest).get_item_count({name=item,quality="normal"}),defines.inventory.chest)
      elseif e and e.valid and e.type=="mining-drill" and e.drop_target and e.drop_target.valid
          and e.drop_target.type=="mining-drill" then
        local t=e.drop_target
        local fuel=t.get_fuel_inventory()
        add(t,(fuel and fuel.get_item_count({name=item,quality="normal"}) or 0)-LOOP_RESERVE,defines.inventory.fuel)
      end
    end
  end
  for _,chest in ipairs(npc.storage_chests or {}) do
    if chest.valid then
      add(chest,chest.get_inventory(defines.inventory.chest).get_item_count({name=item,quality="normal"}),
        defines.inventory.chest)
    end
  end
  table.sort(out,function(a,b) return a.n>b.n end)
  npc.fuel_stores_seen={tick=game.tick,item=item,out=out,total=total}
  return out,total
end

-- How `carried` pieces of `fuel` are split over the burners due (`list`: key, seconds left, J a second, a fill's
-- seconds): the lowest raised first, all toward the same seconds of running, none past its fill. The user, 2026-10-26:
-- every now and then what the loop has is taken and split evenly over all furnaces, drills and boilers; it takes a
-- while to reach a full stack and does not need to. Pieces by burner.
function DesignedStart.split_fuel(list,fuel,carried)
  local value=DesignedStart.fuel_value(fuel)
  local out={}
  if value<=0 or #list==0 or carried<=0 then return out end
  local energy=carried*value
  local lo,hi=0,0
  for _,b in ipairs(list) do hi=math.max(hi,b.cap) end
  for _=1,30 do   -- (the level they all rise to, by halving)
    local mid=(lo+hi)/2
    local need=0
    for _,b in ipairs(list) do need=need+math.max(0,math.min(mid,b.cap)-b.left)*b.rate end
    if need>energy then hi=mid else lo=mid end
  end
  table.sort(list,function(a,b) return a.left<b.left end)
  local left=carried
  for _,b in ipairs(list) do
    local n=math.ceil(math.max(0,math.min(hi,b.cap)-b.left)*b.rate/value)
    -- (at least GIVE a burner it walks to, so the last of a short round go without rather than get one piece each)
    if n>0 then n=math.min(left,math.max(n,math.min(DesignedStart.FUEL.GIVE,math.floor((b.cap-b.left)*b.rate/value)))) end
    if n>0 then out[b.key]=n; left=left-n end
  end
  return out
end

-- What to fetch for a round of fuel for `target` (`need` pieces of `item` from its stores, `now` if gathered by hand):
-- from its stores, the fullest first (no trip for less than TRIP, a burner about to stop or not); never mined by hand
-- once its own drills fill a store of it, only a piece to start a burner they fuel (a loop gone cold), and a teammate's
-- chest; before that, by hand, the first of HAND_FUELS the burner burns that stands near (wood before coal), for now.
function DesignedStart.fuel_fetch(npc,target,inventory,item,need,now,critical,cold)
  local F=DesignedStart.FUEL
  local stores,total=DesignedStart.fuel_stores(npc,item)
  -- (a cold one, standing still, takes whatever there is: production stopped may leave a store short for good)
  if stores[1] and (total>=math.min(need,F.TRIP) or cold) then
    local first=math.min(stores[1].n,need)
    -- (every store near the fullest in one sweep: its loop's drills one after another, as a player's ctrl-drag)
    return DesignedStart.sweep_of(npc,{kind="transfer",target=stores[1].e,item=item,count=first,mode="withdraw",
      inventory=stores[1].inventory,up_to=true},DesignedStart.collect_more(stores,stores[1].e,need,first)),nil,"collect"
  end
  local fed=DesignedStart.fed_stores(npc)
  local starts=cold and target.unit_number and fed.stores[target.unit_number..":"..item]
  if fed.items[item] and not starts then
    if not critical then return nil end
    return TeamSupply.take(npc,item,need,game.tick,false)
  end
  local c=npc.character
  for _,fuel in ipairs(DesignedStart.HAND_FUELS) do
    local near=fuel~="wood" or try(function()
      return c.surface.count_entities_filtered{type="tree",position=c.position,radius=F.TREES,limit=1}>0 end)
    if near and DesignedStart.takes_fuel(target,inventory,fuel) then
      local n=starts and F.START or math.max(1,math.ceil(now*DesignedStart.fuel_value(item)/DesignedStart.fuel_value(fuel)))
      local job,why,kind=acquire(npc,fuel,n,0,starts)
      if job and job.kind=="mine" and not job.target then job.count=math.max(1,math.min(job.count or n,n-count(npc,fuel))) end
      if job or why then return job,why,kind end
    end
  end
  return nil
end

-- A burner's fuel: nothing while it has enough; what is in hand, once due (or its share of a round under way);
-- otherwise a fetch for a round, for this one and the others due soon (`batch(item)`: their fills, their shares for
-- HAND seconds, and them, for the split).
function DesignedStart.fuel_job(npc,target,inventory,item,urgent,batch)
  local F=DesignedStart.FUEL
  local left,full=DesignedStart.fuel_time(target,inventory,item)
  local present=inventory.get_item_count({name=item,quality="normal"})
  local stock=left and full and full>0 and left*FUEL_FILL/full or present   -- (all its fuel, in pieces of `item`)
  local want=math.floor(FUEL_FILL-stock)
  if want<=0 then return nil end
  local key=target.unit_number or 0
  -- (a round for each fuel, until what it gathered is handed out, and ROUND at most from when the handing out began)
  local rounds=npc.fuel_rounds or {}
  npc.fuel_rounds=rounds
  local round=rounds[item]
  local carried=round and count(npc,round.fuel or item) or 0
  if round and (carried<=0 or round.until_tick and game.tick>round.until_tick) then rounds[item]=nil; round=nil end
  -- (looked at again when it falls due, not every turn: no round handing out, not about to leave on far work, and still
  -- well above where it falls due: a burner emptied meanwhile, by hand or a raid, is looked at at once)
  local next_look=npc.fuel_next and npc.fuel_next[key]
  -- (the highest its line can be: a round's sweep, and the walk there beyond its reach, whatever is in hand)
  local walk=0
  local c=npc.character
  if c and c.valid and target.position then
    walk=math.max(0,math.sqrt((target.position.x-c.position.x)^2+(target.position.y-c.position.y)^2)
      -DesignedStart.reach(npc))/6
  end
  if next_look and game.tick<next_look.tick and left and left>2*(math.max(next_look.low,F.SWEEP)+walk)
      and not round and urgent~="leaving" then
    return nil
  end
  local fuel,held=DesignedStart.fuel_in_hand(npc,target,inventory,item)
  local cold=(left and left<=0) or (not left and present==0)
  -- (its own inserters feed it this from a belt or a chest: by hand only once it stands cold, DesignedStart.auto_fed)
  if not cold and DesignedStart.auto_fed(npc,target,item) then return nil end
  -- (a boiler waiting for its own feed, the base not short of power: DesignedStart.waits_for_feed)
  if DesignedStart.waits_for_feed(npc,target,item) then return nil end
  -- (`urgent` "leaving": about to go off to far work, so everything due within the sweep, DesignedStart.leaving)
  local leaving=urgent=="leaving"
  urgent=urgent==true
  local due,low
  if left then
    low=math.min(urgent and F.URGENT or F.LOW,full*F.LOW_SHARE)
    if held>0 then low=urgent and math.min(F.LOW,full*F.LOW_SHARE) or round and math.min(F.SWEEP,full*F.SWEEP_SHARE) or low end
    if leaving then low=math.min(F.SWEEP,full*F.SWEEP_SHARE) end
    -- (while building, about to stop counts the walk there: a home boiler's fill lasts 111 s, a quarter of it less
    -- than the walk back from far work, Codex's review W-3-far-building)
    if urgent then
      local c=npc.character
      local p=target.position
      -- (6 tiles a second: detours; only the walk beyond its reach)
      if c and c.valid and p then
        low=low+math.max(0,math.sqrt((p.x-c.position.x)^2+(p.y-c.position.y)^2)-DesignedStart.reach(npc))/6
      end
    end
    due=left<low
  else   -- (the game cannot say how long it lasts: by the count)
    due=present<(urgent and 1 or FUEL_LOW)
  end
  -- (its share of the round; but not one that would call it home from far work: there only what is about to stop)
  if round and round.split and (round.split[key] or 0)>0 and not DesignedStart.away(npc) then due=true end
  if not due then
    -- (its next look: half the time until it falls due, NEXT_MIN to NEXT_MAX ticks)
    if left and low and not round then
      npc.fuel_next=npc.fuel_next or {}
      if table_size and table_size(npc.fuel_next)>500 then npc.fuel_next={} end   -- (gone burners: started over)
      npc.fuel_next[key]={tick=game.tick+math.max(DesignedStart.FUEL_NEXT_MIN,math.min(DesignedStart.FUEL_NEXT_MAX,
        math.floor((left-low)*30))),low=low}
    end
    return nil
  end
  if npc.fuel_next then npc.fuel_next[key]=nil end
  if round and not round.split and batch then
    -- Collecting: what its stores have, from one after another, until the round has what it wants; then the split.
    -- (only from stores near where it stands: out at far work with 126 coal in hand, it walked home to make up the
    -- round's 200, the user's live game replayed 2026-10-26)
    local c=npc.character
    if round.collect and carried<(round.target or 0) then
      local stores=DesignedStart.fuel_stores(npc,item)
      local s=stores[1]
      local near_r=math.max(F.NEAR,DesignedStart.reach(npc))   -- (what it reaches from here is near)
      local near=s and c and c.valid and (s.e.position.x-c.position.x)^2+(s.e.position.y-c.position.y)^2<=near_r^2
      if s and near and s.n>=math.min(F.GIVE,round.target-carried) then
        local first=math.min(stores[1].n,round.target-carried)
        return DesignedStart.sweep_of(npc,{kind="transfer",target=stores[1].e,item=item,count=first,
          mode="withdraw",inventory=stores[1].inventory,up_to=true},
          DesignedStart.collect_more(stores,stores[1].e,round.target-carried,first)),nil,"collect"
      end
    end
    round.split=DesignedStart.split_fuel(select(3,batch(item)) or {},round.fuel or item,carried)
    round.until_tick=game.tick+F.ROUND
  end
  if held>0 then
    local ratio=DesignedStart.fuel_value(item)/math.max(1,DesignedStart.fuel_value(fuel))   -- (pieces of `fuel` a piece)
    local give=math.min(held,math.max(1,math.ceil(want*ratio)))
    if round and fuel==(round.fuel or item) then
      if round.split then
        local share=round.split[key]
        if share then give=math.min(give,share)   -- (counted off as it goes in: DesignedStart.on_receipt)
        elseif not cold then return nil   -- (not in this round, and still running: the next one)
        else give=math.min(give,math.ceil(FIRST_FUEL*ratio)) end
      else   -- (a round not split yet, met outside the upkeep's sweep: this one's share for now, not the lot)
        local share=DesignedStart.fuel_share(target,inventory,item,F.HAND)
        if share and share<1 then return nil end
        give=math.min(give,math.ceil((share or FIRST_FUEL)*ratio))
      end
    end
    -- (into a burner its drills fuel, only a start of another fuel: more would keep their coal out of its one slot)
    if fuel~=item and target.unit_number and DesignedStart.fed_stores(npc).stores[target.unit_number..":"..item] then
      give=math.min(give,F.START)
    end
    if give<1 then return nil end
    return {kind="transfer",target=target,item=fuel,count=give,mode="deposit"}
  end
  -- (its slot holds another fuel that it burns first: nothing until that is gone)
  if not DesignedStart.takes_fuel(target,inventory,item) and not cold then return nil end
  -- One trip for a useful amount, for every burner due soon; a burner standing cold gets a handful first,
  -- as a player starts every machine of a row and then tops them up (four burners filled one after another
  -- cost six minutes of hand-mined coal before the last one ran, live game 2026-10-22). A cold one its drills fuel
  -- gets only a start: they keep it going.
  local fills,shares=0,0
  if batch then fills,shares=batch(item) end
  local share=DesignedStart.fuel_share(target,inventory,item,F.HAND)
  local need=cold and FIRST_FUEL or math.max(want,30,math.min(F.BATCH,fills or 0))
  local now=cold and FIRST_FUEL or math.max(share or want,math.min(F.BATCH,shares or 0))
  local critical=cold or (left and left<F.URGENT) or (not left and present<=1)
  local job,why,kind=DesignedStart.fuel_fetch(npc,target,inventory,item,need,now,critical,cold)
  if job then
    rounds[item]={fuel=job.item or item,target=need,
      collect=(job.kind=="transfer" or job.kind=="sweep") and job.mode=="withdraw"}
  end
  return job,why,kind
end

-- How many of `item` one craft of what machine `e` makes takes (its recipe, a furnace's last one), 1 if unknown.
function DesignedStart.per_craft(e,item)
  local r=try(function() return e.get_recipe() end)
  if not r and e.type=="furnace" then
    local prev=try(function() return e.previous_recipe end)
    local name=prev and prev.name
    if type(name)~="string" then name=try(function() return name.name end) end
    r=type(name)=="string" and prototypes.recipe[name] or nil
  end
  for _,ing in ipairs(r and r.ingredients or {}) do
    if ing.name==item then return math.max(1,math.ceil(ing.amount or 1)) end
  end
  return 1
end

-- `urgent`: only when the machine is about to stop (used while building, so
-- routine top-ups do not starve construction). `batch(item)`: the fuel the burners due soon want.
local function supply_job(npc,entity,supply,urgent,batch)
  local target=entity.entity
  if not (target and target.valid) or supply.automated then return nil end
  if supply.slot=="input" and DesignedStart.held_plenty(npc,target) then return nil end
  if supply.slot=="input" and target.type=="assembling-machine" then
    local recipe=target.get_recipe()
    local output=recipe and target.get_output_inventory()
    for _,product in ipairs(recipe and recipe.products or {}) do
      if product.type=="item" and output.get_item_count(product.name)>=OUTPUT_BACKLOG then return nil end
    end
  end
  local inventory
  if supply.slot=="fuel" then inventory=target.get_fuel_inventory()
  elseif supply.slot=="ammo" then
    -- (as DesignedStart.defense_supply: an artillery piece's own inventory, and never walked to with biters at it)
    local O=DesignedStart.Odds
    local w=npc.combat and npc.combat.wave
    local at_it=try(function()
      return target.surface.count_entities_filtered{position=target.position,radius=(w and w.standoff) or (O.NOTICE+O.MARGIN),
        force="enemy",type="unit",limit=1}
    end)
    if at_it and at_it>0 then return nil end
    if DesignedStart.auto_fed(npc,target,supply.item) then return nil end   -- (its line feeds it: D-19)
    inventory=target.get_inventory(target.type=="artillery-turret" and defines.inventory.artillery_turret_ammo
      or defines.inventory.turret_ammo)
  elseif supply.slot=="robot" then inventory=target.get_inventory(defines.inventory.roboport_robot)
  elseif target.type=="furnace" then inventory=target.get_inventory(defines.inventory.crafter_input)
  elseif target.type=="lab" then inventory=target.get_inventory(defines.inventory.lab_input)
  elseif target.type=="container" then inventory=target.get_inventory(defines.inventory.chest)   -- a lane fed by hand
  else inventory=target.get_inventory(defines.inventory.crafter_input) end
  if not inventory then return nil end
  if supply.slot=="fuel" then return DesignedStart.fuel_job(npc,target,inventory,supply.item,urgent,batch) end
  local low,fill=FUEL_LOW,FUEL_FILL
  if supply.slot=="input" then low,fill=INPUT_LOW,INPUT_FILL end
  if supply.slot=="ammo" then low,fill=AMMO_LOW,AMMO_FILL end
  if supply.slot=="robot" then low,fill=math.ceil((supply.count or 10)/2),supply.count or 10 end   -- (half gone: refill)
  if urgent==true then low=1 end
  if supply.slot=="ammo" then return ammo_job(npc,target,inventory,supply.item,low,fill) end
  -- (never below what one craft of its recipe takes: fed less, a machine whose recipe takes 50 stone stood forever)
  local per_craft=supply.slot=="input" and DesignedStart.per_craft(target,supply.item) or 1
  low,fill=math.max(low,per_craft),math.max(fill,per_craft)
  local present=inventory.get_item_count({name=supply.item,quality="normal"})
  if present>=low then return nil end
  -- (its own inserters feed it this from a belt or a chest: by hand only once it has none, DesignedStart.auto_fed)
  if supply.slot=="input" and present>0 and DesignedStart.auto_fed(npc,target,supply.item) then return nil end
  if supply.slot=="input" and DesignedStart.drill_fed(npc,target,supply.item) then return nil end   -- (its drill feeds it)
  -- (what the machine takes: an assembler holds a few crafts' worth, not the fill; 60 inserters for one that took 50
  -- failed the supply, stage-silo W-22)
  local room=try(function() return inventory.get_insertable_count({name=supply.item,quality="normal"}) end)
  local want=math.min(fill-present,room or math.huge)
  if want<1 then return nil end
  if count(npc,supply.item)>0 then   -- what it carries goes in at once: one trip
    -- (`up_to`: what goes in is the supply done; what is left stays in hand)
    return {kind="transfer",target=target,item=supply.item,count=math.min(want,count(npc,supply.item)),mode="deposit",
      up_to=true,
      inventory=supply.slot=="robot" and defines.inventory.roboport_robot
        or supply.slot=="input" and (target.type=="furnace" and defines.inventory.crafter_input
        or target.type=="lab" and defines.inventory.lab_input
        or target.type=="container" and defines.inventory.chest
        or defines.inventory.crafter_input) or nil}
  end
  -- What it has to make for a machine is made a handful at a time, each handful taken over as it comes,
  -- between its other jobs, as a player feeds a lab while crafting the packs. Making the whole fill first
  -- left a lab idle for 5 minutes of hand crafting before its first pack (one-engineer hour, 2026-10-24).
  -- What is mined or smelted is fetched ready, so it comes in one trip (bricks for a wall row by the chestful); but
  -- mined by hand, a handful too (a bootstrap furnace's 60 ore mined by hand for the five plates its stage waited on
  -- took two minutes, while plates came from the furnaces already standing, trace 2026-10-26).
  -- (A local here: the file's main chunk is at Lua's limit of 200 locals.)
  local INPUT_BATCH=math.max(10,per_craft)
  local batched=supply.slot=="input" and not (RAW[supply.item] or SMELTED[supply.item])
  -- Once its own machines make it, a machine fed by hand gets their spare, not what it crafts: a first lab
  -- fed red packs crafted by hand kept on after red science ran by machine, half the engineer's time going
  -- to the packs and the iron for them (one-engineer run 19, 2026-10-25). Hand work starts what machines
  -- then take over, as with walls (by hand only in an emergency, the user, 2026-10-24).
  if batched and DesignedStart.machine_made(npc,supply.item) then
    return DesignedStart.collect(npc,supply.item,want)
  end
  -- (a chest fed by hand, a lane's head as the ammo loop's: what its own stores hold of it goes in first, nothing made
  -- while they hold it; the proving ring's loop chest stood empty beside 116 magazines in its home store, D-19)
  if supply.slot=="input" and target.type=="container" then
    local store,stocked=owned_output(npc,supply.item,true)
    if store and store~=target and stocked>0 then
      return {kind="transfer",target=store,item=supply.item,count=math.min(stocked,want),mode="withdraw",up_to=true}
    end
  end
  local n=batched and math.min(want,INPUT_BATCH) or want
  return DesignedStart.supply_fetch(npc,supply.item,n,math.min(n,INPUT_BATCH),true)
end
DesignedStart.supply_job=supply_job

local function resource_under(entity)
  local e=entity.entity
  if not (e and e.valid) then return 0 end
  local r=e.prototype.get_mining_drill_radius()
  local total=0
  for _,res in ipairs(e.surface.find_entities_filtered{type="resource",
      area={{e.position.x-r,e.position.y-r},{e.position.x+r,e.position.y+r}}}) do total=total+res.amount end
  return total
end


local function labs(npc)
  local out={}
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in pairs(plans) do
    for _,id in ipairs(plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="lab" then out[#out+1]=e end
    end
  end
  return out
end

-- The team world: its labs get every pack the team's research needs that it carries (grey packs from
-- a chest it emptied, say): a lab's plan names only the packs of the lines built beside it.
local LAB_LOW,LAB_FILL=5,20
local function lab_packs(npc)
  local research=TeamWorld.active() and npc.force.current_research
  if not research then return nil end
  for _,lab in ipairs(labs(npc)) do
    local inventory=lab.get_inventory(defines.inventory.lab_input)
    for _,ingredient in ipairs(research.research_unit_ingredients or {}) do
      local item={name=ingredient.name,quality="normal"}
      local carried=count(npc,ingredient.name)
      if carried>0 and inventory and inventory.get_item_count(item)<LAB_LOW and inventory.get_insertable_count(item)>0 then
        return {kind="transfer",target=lab,item=ingredient.name,count=math.min(carried,LAB_FILL),mode="deposit",
          inventory=defines.inventory.lab_input},{upkeep="lab_packs",item=ingredient.name}
      end
    end
  end
end

-- Science packs the NPC can supply to labs: those it can hand-craft now, and those its space platform makes (R-8).
local function suppliable_pack(npc,name)
  local hand=Recipes.hand(npc.character)
  local recipe=Recipes.for_item(npc.force,name,hand)
  return (recipe and Recipes.made_in(recipe,hand)) or Orbit.makes(npc,name)
end

-- Technologies the NPC's labs could research now, cheapest on the rocket path
-- first. Trigger technologies unlock by themselves and are not offered.
-- What a player researches first: what replaces hand work (assemblers, electric drills, belts),
-- then the second science pack and steel.
local EARLY_RESEARCH={"automation","electric-mining-drill","logistics","logistic-science-pack","steel-processing",
  "electronics","fast-inserter","automation-2","advanced-material-processing"}
local early_rank={}
for i,name in ipairs(EARLY_RESEARCH) do early_rank[name]=i end
-- At war (it has a foe), weapons and defenses come first, as a player under attack researches them:
-- the first WAR_FIRST of these it has not researched yet, before anything else.
-- (Heavy armor needs steel processing in 2.0: without it heavy armor is never offered, run 23.)
local WAR_RESEARCH={"military","gun-turret","stone-wall","steel-processing","heavy-armor","military-2",
  "physical-projectile-damage-1","weapon-shooting-speed-1","military-3"}
local WAR_FIRST=7
local war_rank={}
for i,name in ipairs(WAR_RESEARCH) do war_rank[name]=i end
-- Walled off from those it is at war with (no way there clear of the biters, agent.lua npc.walled_off):
-- the means to get through come first, a vehicle to drive past the nests, a tank to drive through them,
-- artillery to clear them from afar, for as long as any is still to research.
local VEHICLE_RESEARCH={"automobilism","tank","artillery"}
local vehicle_rank={}
for i,name in ipairs(VEHICLE_RESEARCH) do vehicle_rank[name]=i end
-- The team world (a death world): the team arms first, in this order, then any other research the
-- armory counts as military (it unlocks weapons, turrets, walls or ammunition, or makes them stronger),
-- then the rest as everywhere else.
local TEAM_RESEARCH={"military","gun-turret","stone-wall","military-2","logistic-science-pack","gate",
  "military-science-pack","physical-projectile-damage-1","weapon-shooting-speed-1","heavy-armor"}
local team_rank={}
for i,name in ipairs(TEAM_RESEARCH) do team_rank[name]=i end
local TEAM_MILITARY=50   -- (after the list, before everything that is not military)
local WALLED_FOR=216000   -- an hour of game time since it last found itself walled off
local function walled(npc)
  local w=npc.walled_off
  if not (npc.foe and w and game.tick-w.tick<WALLED_FOR) then return false end
  for _,name in ipairs(VEHICLE_RESEARCH) do
    local t=npc.force.technologies[name]
    if t and not t.researched then return true end
  end
  return false
end
local function war_research(npc)
  if not npc.foe then return false end
  if walled(npc) then return true end
  local done=0
  for _,name in ipairs(WAR_RESEARCH) do
    local t=npc.force.technologies[name]
    if t and t.researched then done=done+1 end
  end
  return done<WAR_FIRST
end
DesignedStart.war_research=war_research
DesignedStart.walled=walled

-- The science packs its force's labs take (lab_inputs of its labs), once per lab kind.
function DesignedStart.lab_inputs(npc)
  local out={}
  for _,lab in ipairs(labs(npc)) do
    for _,input in ipairs(try(function() return lab.prototype.lab_inputs end) or {}) do out[input]=true end
  end
  return out
end

-- Whether a science pack is being made, so research on it runs (R-9, the user's play: their labs stood 41 minutes on
-- a tech whose military packs nothing made, lesson 19): its platform makes it, it is had or made lately where its labs
-- can get it (pack_at_hand: labs, hands, chests, a teammate's, the belt feeding a lab, made in the last hour), or a
-- machine of its force stands set to make it. In the opening, before any machine makes a science pack, a first-tier
-- pack (one some research takes alone) its hands can craft counts (its first lab is fed by hand). `seen` keeps the
-- answers of one look.
function DesignedStart.pack_made(npc,pack,seen)
  seen=seen or {}
  if seen[pack]~=nil then return seen[pack] end
  local out=Orbit.makes(npc,pack)
  local lab=labs(npc)[1]
  seen.at_hand=seen.at_hand or {}
  if not out and lab then out=DesignedStart.pack_at_hand(npc,lab,pack,seen.at_hand) end
  if not out then
    if not seen.lines then   -- (what its machines stand set to make, of the packs its labs take)
      seen.lines={}
      local inputs=DesignedStart.lab_inputs(npc)
      local c=npc.character
      for _,m in ipairs(try(function()
          return c.surface.find_entities_filtered{type="assembling-machine",force=npc.force} end) or {}) do
        local recipe=try(function() return m.get_recipe() end)
        for _,p in ipairs(recipe and recipe.products or {}) do
          if inputs[p.name] then seen.lines[p.name]=true end
        end
      end
    end
    if not seen.first then   -- (the first tier's packs: those some research takes alone, red in the base game)
      seen.first={}
      for _,t in pairs(try(function() return npc.force.technologies end) or {}) do
        local ings=try(function() return t.research_unit_ingredients end) or {}
        if #ings==1 then seen.first[ings[1].name]=true end
      end
    end
    -- (by hand only in the opening, and only a first-tier pack: military packs made by nothing do not count)
    out=seen.lines[pack]==true
      or (next(seen.lines)==nil and seen.first[pack]==true and suppliable_pack(npc,pack) and true or false)
  end
  seen[pack]=out
  return out
end

-- The packs of a technology nothing makes (DesignedStart.pack_made), as a list.
function DesignedStart.unmade_packs(npc,tech,seen)
  local out={}
  for _,ingredient in pairs(tech.research_unit_ingredients or {}) do
    if not DesignedStart.pack_made(npc,ingredient.name,seen) then out[#out+1]=ingredient.name end
  end
  return out
end

-- (the team's queue reads it so: its picks on packs nothing makes go last, the first of them gives way; R-9)
TeamResearch.unmade=function(tech)
  for _,npc in pairs(storage.npcs or {}) do
    if npc.character and npc.character.valid and labs(npc)[1] then return DesignedStart.unmade_packs(npc,tech,{}) end
  end
  return {}
end

-- What research waits on packs nothing makes, for the planners (that pack's line a goal): the team's queue, else its
-- own current research. {{tech=, packs={...}, by=}} or nil.
function DesignedStart.research_waits(npc)
  if TeamWorld.active() then
    local out=TeamResearch.waits()
    return #out>0 and out or nil
  end
  local current=npc.force.current_research
  local packs=current and DesignedStart.unmade_packs(npc,current,{}) or {}
  return #packs>0 and {{tech=current.name,packs=packs,by="engineer"}} or nil
end

-- The recipes a technology and the techs after it not yet researched unlock (those it leads to, sorted, sixty at most),
-- the bonus types of those after it, and whether one after it is `marked` (military, survival, the road to space): the
-- companion counts a tech whose only use comes later as used (R-10); `cut` when the recipes ran past sixty (the rest
-- unseen: the companion counts it as used). `seen` keeps each tech's answer for one call.
DesignedStart.LEADS_KEPT=60
function DesignedStart.tech_leads(force,name,seen,marked)
  if seen[name] then return seen[name] end
  local out,set,bonuses,bonus_set,queue,visited={},{},{},{},{name},{[name]=true}
  local armed,cut=false,false
  local i=1
  while queue[i] do
    local here=queue[i]
    local t=force.technologies[here]; i=i+1
    if i>2 and marked[here] then armed=true end
    for _,effect in pairs(t and try(function() return t.prototype.effects end) or {}) do
      if effect.type=="unlock-recipe" then
        if not set[effect.recipe] then
          if #out<DesignedStart.LEADS_KEPT then set[effect.recipe]=true; out[#out+1]=effect.recipe else cut=true end
        end
      elseif i>2 and not bonus_set[effect.type] then bonus_set[effect.type]=true; bonuses[#bonuses+1]=effect.type end
    end
    for next_name,next_t in pairs(t and try(function() return t.successors end) or {}) do
      if not visited[next_name] and not next_t.researched then visited[next_name]=true; queue[#queue+1]=next_name end
    end
  end
  table.sort(out)
  table.sort(bonuses)
  seen[name]={recipes=out,bonuses=bonuses,armed=armed,cut=cut}
  return seen[name]
end

-- What the labs set aside: techs the companion found no use for (none of its goals, layouts, mall or bonuses: Bob's
-- inserters, lamps, Factorissimo's buildings took red packs in normal-7e; R-10). Offered again after this long.
DesignedStart.UNUSED_TICKS=36000

local function research_options(npc)
  local force=npc.force
  local team=TeamWorld.active()
  local armed={}
  if team then for _,t in ipairs(Armory.catalog().techs) do armed[t.name]=true end end
  local function military_rank(name)
    if not team then return 99 end
    return team_rank[name] or (armed[name] and TEAM_MILITARY) or 99
  end
  -- (the road to space: to the rocket silo, then on to the platform's thrusters, R-8)
  local silo=force.technologies["rocket-silo"]
  local target=silo and silo.researched and force.technologies["space-platform-thruster"] and "space-platform-thruster"
    or "rocket-silo"
  local dag=Progression.technology_dag(force,target)
  local on_path={}
  for i,name in ipairs(dag and dag.order or {}) do on_path[name]=i end
  local survival=Progression.survival_techs(Armory.catalog().techs)   -- (weighed by the threat: research_brain.py)
  local out,made_seen={},{}
  for name,t in pairs(force.technologies) do
    local trigger=try(function() return t.prototype.research_trigger end)
    local units=try(function() return t.research_unit_count end)
    -- (one tech tree: nothing already in the team's queue, nothing a player took out lately)
    local open=not TeamWorld.active() or TeamResearch.open(name,game.tick)
    local unused=npc.research_unused and npc.research_unused[name]
    if unused and unused>game.tick then open=false end
    if t.enabled and not t.researched and not trigger and units and open then
      local ok=true
      for _,p in pairs(t.prerequisites) do if not p.researched then ok=false; break end end
      -- (every pack one it can supply and being made: queued on packs nothing makes, labs stand still; lesson 19)
      local packs={}
      for _,ingredient in pairs(t.research_unit_ingredients) do
        if not suppliable_pack(npc,ingredient.name) or not DesignedStart.pack_made(npc,ingredient.name,made_seen) then
          ok=false; break
        end
        packs[#packs+1]=ingredient.name
      end
      if ok and #packs>0 then
        local unlocks,bonuses,bonus_seen={},{},{}
        for _,effect in pairs(t.prototype.effects or {}) do
          if effect.type=="unlock-recipe" then unlocks[#unlocks+1]=effect.recipe
          elseif not bonus_seen[effect.type] then bonus_seen[effect.type]=true; bonuses[#bonuses+1]=effect.type end
        end
        table.sort(unlocks)
        table.sort(bonuses)
        -- (its lab time, units x time per unit: the same key the team's queue sorts picks by, R-9)
        local per=try(function() return t.research_unit_energy end) or 0
        out[#out+1]={name=name,units=units,packs=packs,unlocks=unlocks,rocket_path=on_path[name]~=nil,
          early=early_rank[name]~=nil,military=military_rank(name)<99 or nil,survival=survival[name] or nil,
          lab=units*per,bonuses=bonuses}
      end
    end
  end
  local war=war_research(npc)
  local wall=walled(npc)
  table.sort(out,function(a,b)
    local ta,tb=military_rank(a.name),military_rank(b.name)
    if ta~=tb then return ta<tb end
    local wa,wb=war and war_rank[a.name] or 99,war and war_rank[b.name] or 99
    if wall then wa,wb=vehicle_rank[a.name] and vehicle_rank[a.name]-10 or wa,vehicle_rank[b.name] and vehicle_rank[b.name]-10 or wb end
    if wa~=wb then return wa<wb end
    local ea,eb=early_rank[a.name] or 99,early_rank[b.name] or 99
    if ea~=eb then return ea<eb end
    if a.rocket_path~=b.rocket_path then return a.rocket_path end
    if (a.lab or 0)~=(b.lab or 0) then return (a.lab or 0)<(b.lab or 0) end
    if a.units~=b.units then return a.units<b.units end
    return a.name<b.name
  end)
  -- Twelve at most, no more than half of them military, so the way to the rocket is always on offer too
  -- (in the team world the military ones came first and filled the list: research would never reach space).
  local kept,extra,military={},{},0
  for _,o in ipairs(out) do
    if o.military and military>=6 then extra[#extra+1]=o
    elseif #kept<12 then kept[#kept+1]=o; if o.military then military=military+1 end end
  end
  for _,o in ipairs(extra) do if #kept>=12 then break end kept[#kept+1]=o end
  -- and up to three survival options cut by that (cheapest first): weighed by the threat, never left off the list
  local listed,cut={},{}
  for _,o in ipairs(kept) do listed[o.name]=true end
  for _,o in ipairs(out) do if o.survival and not listed[o.name] then cut[#cut+1]=o end end
  table.sort(cut,function(a,b)
    local ca,cb=a.units*#a.packs,b.units*#b.packs
    if ca~=cb then return ca<cb end
    return a.name<b.name
  end)
  for i=1,math.min(3,#cut) do kept[#kept+1]=cut[i] end
  local leads_seen,marked={},{}
  for name in pairs(survival) do marked[name]=true end
  for name in pairs(on_path) do marked[name]=true end
  for name in pairs(armed) do marked[name]=true end
  for _,o in ipairs(kept) do
    local leads=DesignedStart.tech_leads(force,o.name,leads_seen,marked)
    o.leads,o.lead_bonuses,o.leads_armed,o.leads_cut=leads.recipes,leads.bonuses,leads.armed or nil,leads.cut or nil
  end
  return kept
end

DesignedStart.research_options=research_options

-- What it has researched so far, for weighing the next pick: military techs (the team's list and the
-- armory's), and techs on the way to the rocket silo.
function DesignedStart.research_tally(npc)
  local force=npc.force
  local armed={}
  for _,t in ipairs(Armory.catalog().techs) do armed[t.name]=true end
  -- (the road to space: to the rocket silo, then on to the platform's thrusters, R-8)
  local silo=force.technologies["rocket-silo"]
  local target=silo and silo.researched and force.technologies["space-platform-thruster"] and "space-platform-thruster"
    or "rocket-silo"
  local dag=Progression.technology_dag(force,target)
  local path={}
  for _,name in ipairs(dag and dag.order or {}) do path[name]=true end
  local out={military=0,rocket_path=0}
  for name,t in pairs(force.technologies) do
    if t.researched then
      if armed[name] or team_rank[name] then out.military=out.military+1 end
      if path[name] then out.rocket_path=out.rocket_path+1 end
    end
  end
  return out
end

local function researched_count(force)
  local n=0
  for _,t in pairs(force.technologies) do if t.researched then n=n+1 end end
  return n
end

-- A stage that has been running long without meeting its goal: what its buildings are
-- doing (no power, waiting for ingredients, output full...), for the log and the export.
local STUCK_AFTER=18000
DesignedStart.STUCK_FOR=3*STUCK_AFTER   -- ticks a producing stage waits on its witness at most (W-31)
local function stuck_report(d,stage,tick)
  local counts,names={},{}
  for k,v in pairs(defines.entity_status) do names[v]=k end
  for _,id in ipairs(stage.entity_ids) do
    local e=d.plan.entities[id].entity
    if e and e.valid and e.status and e.type~="transport-belt" and e.type~="underground-belt" and e.type~="splitter"
        and e.type~="loader" then
      local key=e.name..":"..tostring(names[e.status] or e.status)
      counts[key]=(counts[key] or 0)+1
    end
  end
  local parts={}
  for _,k in ipairs(keys(counts)) do parts[#parts+1]=counts[k].." "..k end
  return table.concat(parts,", ")
end

-- Whether a machine of its own other than `except` makes `item` with no hands: a furnace or assembler of a plan it keeps,
-- whose recipe (now or last) makes it, and that a drill or an inserter of its own feeds (DesignedStart.fed_stores).
function DesignedStart.made_without_hands(npc,item,except)
  local fed=DesignedStart.fed_stores(npc)
  local plans={npc.design and npc.design.plan}
  for _,old in ipairs(npc.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan and plan.order or {}) do
      local e=plan.entities[id].entity
      local n=e and e.valid and e~=except and (e.type=="furnace" or e.type=="assembling-machine") and e.unit_number
      if n and (fed.drilled[n] or (fed.inserted[n] and #fed.inserted[n]>0)) then
        local r=try(function() return e.get_recipe() end) or try(function() return e.previous_recipe end)
        local name=r and (type(r.name)=="string" and r.name or (r.name and r.name.name))
        local recipe=name and prototypes.recipe[name]
        local makes=false
        for _,p in ipairs(recipe and recipe.products or {}) do if p.name==item then makes=true end end
        -- (and every item it takes comes to it: from a drill, or an arm of its own with it at hand)
        for _,ing in ipairs(makes and recipe.ingredients or {}) do
          if ing.type~="fluid" and not (DesignedStart.drill_fed(npc,e,ing.name) or DesignedStart.auto_fed(npc,e,ing.name)) then
            makes=false
          end
        end
        if makes then return true end
      end
    end
  end
  return false
end

local function start_witnesses(d,stage)
  for _,w in ipairs(stage.witnesses) do
    local entity=w.entity_id and d.plan.entities[w.entity_id]
    if w.kind=="product_counter_delta" and entity and entity.entity and entity.entity.valid then
      -- A machine this plan built counts everything it made: it may have filled its output
      -- while the rest of the stage was still going up (a science lane with no labs yet).
      -- One adopted from an earlier build counts from now.
      w.baseline=entity.adopted and entity.entity.products_finished or 0
    elseif w.kind=="resource_extraction_delta" and entity then
      w.baseline=resource_under(entity)
    elseif w.kind=="research_progress_delta" and entity and entity.entity and entity.entity.valid then
      local force=entity.entity.force
      w.baseline={researched=researched_count(force),research=force.current_research and force.current_research.name,
        progress=force.research_progress}
    end
  end
  stage.witness_tick=game.tick
end

local function witness_met(d,w,npc)
  local entity=w.entity_id and d.plan.entities[w.entity_id]
  local e=entity and entity.entity
  if w.kind=="entity_built" then return (e~=nil and e.valid) or (entity~=nil and entity.skipped~=nil) end
  if w.kind=="area_clear" then return Clearing.witness(npc,w) end
  if not (e and e.valid) then return false end
  if w.kind=="product_counter_delta" then
    w.observed=e.products_finished-(w.baseline or 0)
    if w.observed>=w.min_delta then return true end
    -- a machine fed by hand, whose product the engineer already holds plenty of, or other machines of its own already
    -- make with no hands (fed by their drills or inserters): what it is there for is done (DesignedStart.held_plenty,
    -- DesignedStart.made_without_hands). A bootstrap furnace's stage wanted nine plates of its own while drill-fed
    -- furnaces made them, and its ore was mined by hand, every drill's going into a furnace (opening-pace, W-21).
    if not (w.item and DesignedStart.made_otherwise(npc,w.item,e)) then return false end
    for _,stage in ipairs(d.plan.stages) do
      for _,s in ipairs(stage.supplies or {}) do
        if s.entity_id==w.entity_id and s.slot=="input" then return true end
      end
    end
    return false
  elseif w.kind=="resource_extraction_delta" then
    w.observed=(w.baseline or 0)-resource_under(entity)
    if w.observed>=w.min_delta then return true end
    -- A drill whose output goes into another drill of this same plan (a loop that feeds itself), and waits because
    -- that drill's fuel is full, has filled its own loop: a coal loop topped up full before its witness was set never
    -- mined again and its stage waited for ever (T-5: opening-pace with the quick start, 2 runs in 4 on a fresh base).
    -- A drill blocked by a furnace, a chest or anything outside the plan proves nothing.
    if try(function() return e.status end)~=(defines.entity_status or {}).waiting_for_space_in_destination then return false end
    local into=try(function() return e.drop_target end)
    if not (into and into.valid and into.type=="mining-drill") then return false end
    for _,other in pairs(d.plan.entities) do
      if other.entity==into then w.observed_full=true; return true end
    end
    return false
  elseif w.kind=="power_connected" then
    if e.type=="generator" then
      local steam=e.fluids_count>0 and e.get_fluid(1) or nil
      w.observed=steam and math.floor(steam.amount) or 0
      return w.observed>0
    end
    w.observed=e.is_connected_to_electric_network() and 1 or 0
    return w.observed>0
  elseif w.kind=="belt_items" then
    local carried=0
    for line=1,2 do
      local transport=try(function() return e.get_transport_line(line) end)
      if transport then carried=carried+transport.get_item_count() end
    end
    w.observed=math.max(w.observed or 0,carried)
    return carried>=w.min_delta
  elseif w.kind=="fluid_output" then
    local total=0
    for i=1,e.fluids_count do local fluid=e.get_fluid(i); if fluid then total=total+fluid.amount end end
    w.observed=math.max(w.observed or 0,math.floor(total))
    return total>=w.min_delta
  elseif w.kind=="ammo_loaded" then
    local inventory=e.get_inventory(defines.inventory.turret_ammo)
    w.observed=inventory and inventory.get_item_count() or 0
    return w.observed>=w.min_delta
  elseif w.kind=="research_progress_delta" then
    if TeamWorld.active() then
      -- one tech tree: the team's progress moves with everyone's labs, so this lab has to be working itself
      w.observed=e.status==defines.entity_status.working and 1 or 0
      return w.observed>0
    end
    local force,base=e.force,w.baseline or {}
    if researched_count(force)>(base.researched or math.huge) then w.observed=1; return true end
    local now=force.current_research and force.current_research.name
    if base.research==nil and now then base.research,base.progress=now,force.research_progress end
    w.observed=now and now==base.research and force.research_progress-(base.progress or 0) or 0
    return now~=nil and now==base.research and w.observed>0
  end
  return true -- unsupported kinds are recorded as unverified, not blocking
end
-- Witnesses that can never pass (R-7, OP-8b: a research witness held its plan forever, the only research open needing
-- packs its lab could not get). Why a witness cannot be met as things stand, or nil when it can (or it cannot be told):
-- a research witness whose research needs a pack nothing of its own makes or holds, with no other research its packs
-- run; a production witness whose machine has no recipe, or one that does not make its item; an extraction witness
-- with no ore left under its drill. An unmet witness that cannot pass for WITNESS_DOOMED_AFTER is dropped, said why,
-- and the stage goes on without it.
DesignedStart.WITNESS_DOOMED_AFTER=7200   -- ticks a witness must stay impossible before it is dropped (2 minutes)
DesignedStart.WITNESS_CHECK_EVERY=600     -- ticks between checks of one witness (the research one reads every tech)

-- Whether `pack` can reach the lab: in the lab, in hand, in its storage chests or a teammate's (TeamSupply.has), on
-- the belt or in the chest an inserter feeding the lab takes from, or made in the last hour by its force or a friendly
-- one (its own lines, a teammate's: false drops are the worst case, so any sign it is had now counts; a line gone an
-- hour with none left is no sign). Of any quality. `seen` keeps the answers of one check.
DesignedStart.PACK_RECENT="one_hour"   -- the production window a pack must have been made in (flow_precision_index)
function DesignedStart.pack_at_hand(npc,lab,pack,seen)
  if seen and seen[pack]~=nil then return seen[pack] end
  local qualities={}
  for name in pairs(try(function() return prototypes.quality end) or {}) do
    if name~="quality-unknown" then qualities[#qualities+1]=name end
  end
  if #qualities==0 then qualities={"normal"} end
  local function held(f)   -- (any quality of it, counted by f{name, quality})
    for _,q in ipairs(qualities) do
      if (try(function() return f{name=pack,quality=q} end) or 0)>0 then return true end
    end
    return false
  end
  local function made_by(force)
    local stats=try(function() return force.get_item_production_statistics(lab.surface) end)
    return stats~=nil and held(function(id)
      return stats.get_flow_count{name=id,category="input",   -- (the item with its quality, as `name`)
        precision_index=defines.flow_precision_index[DesignedStart.PACK_RECENT],count=true}
    end)
  end
  local function had()
    local inv=try(function() return lab.get_inventory(defines.inventory.lab_input) end)
    if inv and held(inv.get_item_count) then return true end
    local mine=try(function() return npc.character.get_main_inventory() end)
    if mine and held(mine.get_item_count) then return true end
    for _,chest in ipairs(npc.storage_chests or {}) do
      if chest.valid and held(chest.get_item_count) then return true end
    end
    if try(function() return TeamSupply.has(npc,pack) end) then return true end   -- (a teammate's chest, fetched so)
    for _,ins in ipairs(try(function() return lab.surface.find_entities_filtered{type="inserter",   -- (any force's)
        area={{lab.position.x-4,lab.position.y-4},{lab.position.x+4,lab.position.y+4}}} end) or {}) do
      local src=ins.drop_target==lab and ins.pickup_target
      if src and src.valid then   -- (the belt or chest it takes from: packs there reach the lab)
        if src.type=="transport-belt" or src.type=="underground-belt" or src.type=="splitter" then
          for line=1,2 do
            local t=try(function() return src.get_transport_line(line) end)
            if t and held(t.get_item_count) then return true end
          end
        elseif held(src.get_item_count) then return true end
      end
    end
    if made_by(lab.force) then return true end
    for _,f in pairs(try(function() return game.forces end) or {}) do
      if f~=lab.force and try(function() return lab.force.get_friend(f) end) and made_by(f) then return true end
    end
    return false
  end
  local out=had()
  if seen then seen[pack]=out end
  return out
end

function DesignedStart.witness_cannot(npc,d,w)
  local entity=w.entity_id and d.plan.entities[w.entity_id]
  local e=entity and entity.entity
  if not (e and e.valid) then return nil end
  if w.kind=="research_progress_delta" then
    local force,seen=e.force,{}
    local function runs(tech)   -- (labs run it, every pack it takes at hand; else those it cannot have)
      local ings=tech.research_unit_ingredients or {}
      if #ings==0 then return false,"no packs (a trigger technology: labs do not run it)" end
      local missing={}
      for _,ing in ipairs(ings) do
        if not DesignedStart.pack_at_hand(npc,e,ing.name,seen) then missing[#missing+1]=ing.name end
      end
      return #missing==0,table.concat(missing,", ")
    end
    local now=force.current_research
    if now then
      local ok,missing=runs(now)
      if ok then return nil end
      -- (another research its packs run may be picked in its place: not doomed while one is open)
      for _,t in pairs(force.technologies) do
        if t.enabled and not t.researched and t~=now and runs(t) then
          local ready=true
          for _,pre in pairs(t.prerequisites) do if not pre.researched then ready=false break end end
          if ready then return nil end
        end
      end
      return "research "..now.name.." needs "..missing..", which nothing of its own makes or holds, and no other "..
        "research its packs run is open"
    end
    for _,t in pairs(force.technologies) do
      if t.enabled and not t.researched and runs(t) then
        local ready=true
        for _,pre in pairs(t.prerequisites) do if not pre.researched then ready=false break end end
        if ready then return nil end
      end
    end
    return "no research open that the packs it can have run"
  elseif w.kind=="product_counter_delta" and e.type=="assembling-machine" then
    local r=try(function() return e.get_recipe() end)
    if not r then return "its "..e.name.." has no recipe" end
    if w.item then
      for _,p in ipairs(r.products or {}) do if p.name==w.item then return nil end end
      return "its "..e.name.." makes "..r.name..", not "..w.item
    end
  elseif w.kind=="resource_extraction_delta" then
    if resource_under(entity)<=0 then return "no ore left under its "..e.name end
  end
  return nil
end

-- An unmet witness that cannot pass (DesignedStart.witness_cannot) for WITNESS_DOOMED_AFTER: dropped (true), said why.
function DesignedStart.witness_doomed(npc,d,stage,w,tick)
  if w.dropped then return true end
  if w.cannot_checked and tick<w.cannot_checked+DesignedStart.WITNESS_CHECK_EVERY then return false end
  w.cannot_checked=tick
  local ok,why=pcall(DesignedStart.witness_cannot,npc,d,w)
  why=ok and why or nil
  if not why then w.cannot_since=nil; return false end
  w.cannot_since=w.cannot_since or tick
  if tick-w.cannot_since<DesignedStart.WITNESS_DOOMED_AFTER then return false end
  w.dropped=why
  log("[npc-factions] npc="..npc.id.." stage "..stage.id.." witness "..w.kind..
    (w.entity_id and (" on "..w.entity_id) or "").." dropped: it cannot pass ("..why..")")
  Chronicle.record{kind="witness_dropped",actor="npc:"..npc.id,tick=tick,source="native",
    payload={purpose=d.purpose,plan_id=d.plan.plan_id,stage=stage.id,witness=w.kind,why=why}}
  return true
end

-- What a machine fed by hand is there for is had otherwise: plenty in hand, or other machines of its own make it with
-- no hands (witness_met).
function DesignedStart.made_otherwise(npc,item,e)
  if not prototypes.item[item] then return false end   -- (a fluid witness, a refinery's gas: never held in hand)
  return count(npc,item)>=SHORT_OF or DesignedStart.made_without_hands(npc,item,e)
end
-- A start-up input (`once`) to a machine whose product, as the stage witnesses it, is made already or had otherwise: not
-- fed. A
-- bootstrap furnace's ore was mined by hand for nine plates of its own while drill-fed furnaces made them (W-21).
function DesignedStart.start_up_unneeded(npc,d,stage,supply)
  if not (supply.once and supply.slot=="input") then return false end
  local e=d.plan.entities[supply.entity_id]
  e=e and e.entity
  if not (e and e.valid) then return false end
  for _,w in ipairs(stage.witnesses or {}) do
    -- (made already, or had otherwise: witness_met, which reads both)
    if w.kind=="product_counter_delta" and w.entity_id==supply.entity_id and w.item and witness_met(d,w,npc) then
      return true
    end
  end
  return false
end

-- Items flow from plan P into plan Q when one of P's drills or inserters drops into a
-- building of Q, one of Q's inserters picks from a building of P, a belt of P runs into
-- a belt of Q, or a loader moves items between a building and a belt of different plans.
-- Returns {[P]={[Q]=true}}.
-- The newest wall ring among plans (their keep) and a plan holding it, and whether a position lies on its wall band:
-- the layers its wall may have along each of its sides (companion/perimeter.py: WALL_OFFSET 2 tiles in, MAX_LAYERS 4
-- deep), all four, or only the plot's outer edges once the ring is the whole plot (the team's ring). (Fields, not
-- locals: this file is at Lua's 200 locals.)
function DesignedStart.newest_ring(plans)
  local best,owner
  for _,plan in ipairs(plans) do
    local k=plan.keep
    if k and k.ring and k.ring_gen and (not best or k.ring_gen>best.ring_gen) then best,owner=k,plan end
  end
  return best and best.ring,owner
end

function DesignedStart.on_ring(ring,p,plot)
  if not (ring and p) then return false end
  local x,y=math.floor(p.x),math.floor(p.y)
  if x<ring[1] or x>=ring[3] or y<ring[2] or y>=ring[4] then return false end
  local sides={north=true,east=true,south=true,west=true}
  local b=plot and plot.box
  if b and b[1]==ring[1] and b[2]==ring[2] and b[3]==ring[3] and b[4]==ring[4] then
    sides={}
    for _,name in ipairs(plot.outer or {}) do sides[name]=true end
  end
  local function band(d) return d>=2 and d<2+4 end
  return (sides.north and band(y-ring[2])) or (sides.south and band(ring[4]-1-y))
    or (sides.west and band(x-ring[1])) or (sides.east and band(ring[3]-1-x)) or false
end

local function flows(plans)
  local owners={}
  for _,plan in ipairs(plans) do
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if e and e.valid and e.unit_number then
        owners[e.unit_number]=owners[e.unit_number] or {}
        owners[e.unit_number][plan]=true
      end
    end
  end
  local out={}
  local function link(from,to)
    for p in pairs(from or {}) do for q in pairs(to or {}) do
      if p~=q then out[p]=out[p] or {}; out[p][q]=true end
    end end
  end
  local function owner(e) return e and e.valid and e.unit_number and owners[e.unit_number] end
  for _,plan in ipairs(plans) do
    local mine={[plan]=true}
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if e and e.valid then
        if e.type=="inserter" then
          link(owner(e.pickup_target),mine); link(mine,owner(e.drop_target))
        elseif e.type=="mining-drill" then
          link(mine,owner(e.drop_target))
        elseif e.type=="transport-belt" or e.type=="underground-belt" or e.type=="splitter" or e.type=="loader" then
          local ok,n=pcall(function() return e.belt_neighbours end)
          for _,next_belt in ipairs(ok and n and n.outputs or {}) do link(mine,owner(next_belt)) end
          if e.type=="loader" then
            if e.loader_type=="input" then link(mine,owner(e.loader_container)) else link(owner(e.loader_container),mine) end
          end
        end
      end
    end
  end
  return out
end

-- A finished plan may supersede older builds (the bus replaces the first furnaces and
-- science lines). They are taken down unless the engineer still needs them: a build
-- whose items flow into one that stays, stays too. What goes is picked up by the
-- salvage upkeep (items and contents return to the inventory); buildings another plan
-- also uses are left standing.
function DesignedStart.retire(npc,purposes,tick)
  local wanted={}
  for _,p in ipairs(purposes or {}) do wanted[p]=true end
  local history=npc.design_history or {}
  local keep,candidates={},{}
  for _,plan in ipairs(history) do
    local stage=plan.base_plan and plan.base_plan.stage
    local k=plan.keep
    local ring=k and k.ring_gen and k.ring_side and "ring:"..k.ring_gen..":"..k.ring_side
    if wanted[plan.purpose] or (stage and wanted["stage:"..stage]) or (ring and wanted[ring]) then
      candidates[#candidates+1]=plan
    else keep[plan]="kept" end
  end
  if #candidates==0 then return end
  local flow=flows(history)
  local changed=true
  while changed do
    changed=false
    for _,plan in ipairs(candidates) do
      if not keep[plan] then
        for target in pairs(flow[plan] or {}) do
          if keep[target] then keep[plan]="needed by "..tostring(target.purpose); changed=true; break end
        end
      end
    end
  end
  local stays,retired,needed={},{},{}
  for _,plan in ipairs(history) do
    if keep[plan] then
      stays[#stays+1]=plan
      if keep[plan]~="kept" then needed[#needed+1]=plan.purpose.." ("..keep[plan]..")" end
    else retired[#retired+1]=plan end
  end
  local shared={}
  for _,plan in ipairs(stays) do
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if e and e.valid and e.unit_number then shared[e.unit_number]=true end
    end
  end
  npc.retired=npc.retired or {}
  npc.design_salvage=npc.design_salvage or {}
  -- A wall or gate of an old ring's side that stands on the ring now held stays where it is: an old corner the new
  -- ring runs through (the side that stayed was built round it). Taken down, it left a hole there (the review,
  -- 2026-10-26).
  local held_ring,holder=DesignedStart.newest_ring(stays)
  local plot=TeamWorld.active() and TeamWorld.plot("npc-"..npc.id) or nil
  local names={}
  for _,plan in ipairs(retired) do
    local stage=plan.base_plan and plan.base_plan.stage
    local k=plan.keep
    local ring=k and k.ring_gen and k.ring_side and "ring:"..k.ring_gen..":"..k.ring_side
    if ring and wanted[ring] and not wanted[plan.purpose] then
      names[#names+1]=plan.purpose.." ("..ring..")"   -- a side of an old wall ring: the purpose stays
    elseif stage and wanted["stage:"..stage] then names[#names+1]=stage   -- one bus stage: the purpose stays
    else npc.retired[plan.purpose]=tick; names[#names+1]=plan.purpose end
    local ring_side=ring and wanted[ring]
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if ring_side and holder and e and e.valid and ((e.type=="wall" or e.type=="gate")
          and DesignedStart.on_ring(held_ring,e.position,plot) or e.type=="underground-belt" or e.type=="pipe-to-ground") then
        -- kept by the ring held now (rebuilt by it when lost, as its own; partial plans share ids: a fresh one); so are
        -- the ends of a belt or pipe the old side took under it, which keeps running under
        local nid,n=tostring(plan.plan_id).."/"..id,1
        while holder.entities[nid] do n=n+1; nid=tostring(plan.plan_id).."/"..id.."/"..n end
        holder.entities[nid]=plan.entities[id]
        holder.entities[nid].id=nid
        holder.order[#holder.order+1]=nid
        if e.type=="underground-belt" or e.type=="pipe-to-ground" then   -- (no wall of its line: perimeter's tally)
          holder.keep=holder.keep or {}
          holder.keep.extra=(holder.keep.extra or 0)+1
          holder.keep.ends=(holder.keep.ends or 0)+1
        end
      elseif e and e.valid and not (e.unit_number and shared[e.unit_number]) and not DesignedStart.on_line(npc,e)
          and not (ring_side and (e.type=="underground-belt" or e.type=="pipe-to-ground")) then
        -- (a belt or pipe an old side took under it keeps running under: its ends stay)
        table.insert(npc.design_salvage,e)
      end
    end
  end
  npc.design_history=stays
  npc.unit_index=nil   -- (what owns each building has changed: a loss now is rebuilt by the plan that stays)
  log("[npc-factions] npc="..npc.id.." retired: "..(#names>0 and table.concat(names,", ") or "nothing")
    ..(#needed>0 and "; still needed: "..table.concat(needed,", ") or ""))
  if #retired>0 then
    Character.speak(npc,"retire_old",nil,{count=#retired},{priority=1})
  elseif #needed>0 then
    Character.speak(npc,"retire_keep",nil,{},{priority=1})
  end
end

local function hand_off(npc,tick)
  local d=npc.design
  pcall(DesignedStart.Holograms.clear,d.plan)
  d.status,d.completed_tick="complete",tick
  -- Its first drill and furnace may be gone (retired burner pairs, picked up for the bus).
  if npc.miner and not npc.miner.valid then npc.miner=nil end
  if npc.furnace and not npc.furnace.valid then npc.furnace=nil end
  for _,id in ipairs(d.plan.order) do
    local e=d.plan.entities[id].entity
    if e and e.valid then
      if e.type=="mining-drill" and not npc.miner then npc.miner=e end
      if e.type=="furnace" and not npc.furnace then npc.furnace=e end
    end
  end
  if npc.miner then npc.module={x=npc.miner.position.x,y=npc.miner.position.y}; npc.module_direction=npc.miner.direction end
  if npc.furnace then npc.furnace_position={x=npc.furnace.position.x,y=npc.furnace.position.y} end
  npc.step=9 -- past the scripted bootstrap; ordinary upkeep and projects take over
  npc.design_history=npc.design_history or {}
  if not d.plan.clearing then   -- (cleared ground is no build: the history keeps the factory's plans)
    table.insert(npc.design_history,d.plan)
    while #npc.design_history>MAX_HISTORY do table.remove(npc.design_history,1) end
  end
  if npc.design_failures and d.purpose then npc.design_failures[d.purpose]=nil end
  if npc.yields and d.purpose then npc.yields[d.purpose]=nil end   -- (its hand-backs start over: F-11)
  DesignedStart.hand_made_clear(npc,d.plan.hand_clear)   -- (what it now makes by machine: counted afresh, W-23)
  local bp=d.plan.base_plan
  if bp then
    local saved=npc.base_plan or {done={}}
    for _,k in ipairs({"x","y","columns","length","spm","stages","packs","mall","designs","ahead","tight",
        "extra","style","machine","sites","loader_above","expand_failed","aim","zones"}) do
      saved[k]=bp[k]
    end
    saved.done=saved.done or {}
    saved.done[bp.stage]=tick
    npc.base_plan=saved
  end
  if d.plan.retire then DesignedStart.retire(npc,d.plan.retire,tick) end
  local for_player=d.order and d.order.player_index and game.get_player(d.order.player_index)
  if for_player and for_player.valid then
    for _,id in ipairs(d.plan.order) do
      local e=d.plan.entities[id].entity
      if e and e.valid then pcall(function() e.force=for_player.force end) end
    end
    for _,m in ipairs(d.plan.markers or {}) do
      pcall(function()
        for_player.force.add_chart_tag(npc.character.surface,{position={x=d.origin.x+m.x,y=d.origin.y+m.y},
          text=npc.name..": "..m.text,icon={type="virtual",name="signal-dot"}})
      end)
    end
    for_player.print("["..npc.name.."] The "..tostring(d.order.item or "block"):gsub("-"," ")
      .." block is built and yours; its connections are marked on your map.")
    Agent.plan_finished(npc,d.purpose,"complete")
    d.status,d.completed_tick="complete",tick
    -- (what was set aside, unless plan_finished resumed it already: both were lost so; Codex, W-27)
    if npc.paused_design then npc.design=npc.paused_design; npc.paused_design=nil end
    return
  end
  if d.plan.power_row then   -- the row as it now stands (extended, or a new one)
    npc.power_rows=npc.power_rows or {}
    local row,placed=d.plan.power_row,false
    for i,old in ipairs(npc.power_rows) do
      if old.wx==row.wx and old.wy==row.wy then npc.power_rows[i]=row; placed=true end
    end
    if not placed then npc.power_rows[#npc.power_rows+1]=row end
  end
  -- An ally asked for this defense: it is done.
  if d.plan.purpose=="ally-defense" and npc.help_request then
    local request=npc.help_request
    local player=request.player_index and game.get_player(request.player_index)
    npc.help_request=nil
    if player and player.valid then
      player.print({"","[",npc.name,"] Your turrets are up and loaded. I will keep them supplied."})
    end
    local helped=request.npc_id and storage.npcs[request.npc_id]
    if helped then
      Chronicle.record{kind="helped",actor="npc:"..npc.id,target="npc:"..helped.id,tick=tick,source="native",
        payload={what="turrets",x=request.position.x,y=request.position.y}}
    end
  end
  -- An upgrade replaced machines where they stood: the plans that placed them now point at the
  -- new ones (their records, the base memory and repairs keep working).
  if d.plan.purpose=="upgrade" then
    for _,id in ipairs(d.plan.order) do
      local new=d.plan.entities[id].entity
      if new and new.valid then
        for _,plan in ipairs(npc.design_history) do
          if plan~=d.plan then
            for _,oid in ipairs(plan.order) do
              local old=plan.entities[oid]
              if not (old.entity and old.entity.valid) and old.world
                  and math.abs(old.world.x-new.position.x)<0.01 and math.abs(old.world.y-new.position.y)<0.01 then
                old.entity,old.prototype,old.type=new,new.name,new.type
              end
            end
          end
        end
      end
    end
  end
  Chronicle.record{kind="plan_completed",actor="npc:"..npc.id,tick=tick,source="native",
    payload={purpose=d.purpose,plan_id=d.plan.plan_id}}
  d.next_goal_tick=tick+(d.plan.clearing and Clearing.GOAL_DELAY or GOAL_DELAY)
  npc.phase="designed:complete:"..tostring(d.purpose)
  Agent.plan_finished(npc,d.purpose,"complete")
end

-- The ladder's first rung for ground a piece cannot stand on: a cliff blasted with the charge it holds or can make,
-- water filled with landfill. Not a cliff or deep water under a wall piece (it keeps biters out as well: obstacles.lua
-- lets it be); shallow water there is filled (biters wade through it). (job, meta), (nil, wait reason), or nil.
function DesignedStart.ground_job(npc,d,entity,tick)
  local O=DesignedStart.Obstacles
  local p=prototypes.entity[entity.prototype]
  if not p then return nil end
  local wall=O.BARRIER[p.type]
  -- a charge thrown: it lands, and what still stands of the cliff it was thrown at goes (as clearing.lua does)
  if entity.blast then
    if tick-entity.blast.tick<Clearing.BLAST_WAIT then return nil,"blasting" end
    local cliff=entity.blast.cliff
    entity.blast=nil
    if cliff and cliff.valid then pcall(function() cliff.destroy{do_cliff_correction=true} end) end
    if not (cliff and cliff.valid) then O.record(npc,d,entity,"blasted","cliffs:1",tick) end
  end
  local box=entity_box(entity.prototype,entity.world,entity.direction)
  local s=O.survey(npc,box,nil,entity.prototype)
  -- (a wall piece where a cliff or deep water already keeps biters out: not built, at once, not after six tries)
  if wall and (#s.cliffs>0 or s.deep>0 or O.stops_biters(s.kept)) then
    O.skip(entity,"covered",tick)
    O.record(npc,d,entity,"covered",O.what(s),tick,"what stands there keeps biters out")
    return nil,"covered"
  end
  for _,cliff in ipairs(wall and {} or s.cliffs) do
    local item=O.charge(cliff)
    if item and (count(npc,item)>0 or Recipes.ready(npc.force,item)) then
      if count(npc,item)<1 then
        local job,why=acquire(npc,item,1,0)
        if job then return job,{acquire=item,entity_id=entity.id} end
        return nil
      end
      local c=npc.character
      local range=Clearing.throw_range(item)
      local dx,dy=cliff.position.x-c.position.x,cliff.position.y-c.position.y
      if dx*dx+dy*dy>(range-1)^2 then
        local spot=c.surface.find_non_colliding_position("character",cliff.position,range-2,0.5)
        if spot then return {kind="move",position={x=spot.x,y=spot.y}},{clear=true,entity_id=entity.id} end
        return nil
      end
      if Clearing.throw(npc,item,cliff) then
        entity.blast={tick=tick,cliff=cliff}
        O.record(npc,d,entity,"clear","cliffs:"..#s.cliffs,tick,"charge thrown")
        return nil,"blasting"
      end
      return nil
    end
  end
  local fill=#s.water>0 and not (wall and s.deep>0) and O.on_land(entity.prototype) and O.landfill(s.water_names)
  if fill and (count(npc,fill.item)>0 or Recipes.ready(npc.force,fill.item)) then
    if count(npc,fill.item)<#s.water then
      local job=acquire(npc,fill.item,#s.water-count(npc,fill.item),0)
      if job then return job,{acquire=fill.item,entity_id=entity.id} end
      return nil
    end
    O.record(npc,d,entity,"clear","water:"..#s.water,tick,"landfill")
    return {kind="landfill",item=fill.item,positions=s.water},{clear=true,entity_id=entity.id}
  end
end

-- Returns (job spec, meta) to enqueue, or (nil, reason) while waiting.
function DesignedStart.next(npc,tick)
  local d=npc.design
  -- (a hologram its spot refused at the start: tried again now and then; W-35)
  if d.status=="building" and d.plan and tick>=(d.holo_retry or 0) then
    d.holo_retry=tick+DesignedStart.Holograms.RETRY
    pcall(DesignedStart.Holograms.retry,npc,d.plan)
  end
  if d.status=="waiting" then
    if tick-d.requested_tick>REQUEST_TIMEOUT then
      if npc.design_history and #npc.design_history>0 then
        d.status,d.reason,d.next_goal_tick="deferred","designer_timeout",tick+7200
      else fall_back(npc,"designer_timeout") end
    end
    return nil,"waiting_for_plan"
  end
  if d.status~="building" then return nil,d.status end
  -- (its hand work over its budget, F-11: here only while its stage is still going up; a stage built and producing
  -- gets to its witnesses, and hands back only from a hand supply it is still making, below)
  local current=d.plan.stages[d.stage_index]
  if not (current and current.witness_tick) and DesignedStart.hand_back(npc,d,tick) then return nil,"yielded" end
  -- Keep every finished or current stage supplied before building more (what only starts a stage, `once`, while
  -- that stage is being built).
  for index=1,math.min(d.stage_index,#d.plan.stages) do
    local stage=d.plan.stages[index]
    if index<d.stage_index or stage.witness_tick then
      for _,supply in ipairs(stage.supplies) do
        local entity=d.plan.entities[supply.entity_id]
        local job,why
        if not (supply.once and index<d.stage_index) and not DesignedStart.backed_off(npc,supply,tick)
            and not DesignedStart.start_up_unneeded(npc,d,stage,supply) then
          job,why=supply_job(npc,entity,supply)
        end
        if why and why:sub(1,9)=="smelting:" then DesignedStart.hand_clock(npc,d,tick,supply.item) end
        if (job or DesignedStart.hand_wait(why)) and DesignedStart.hand_back(npc,d,tick) then return nil,"yielded" end
        if job then
          return job,{supply=true,entity_id=supply.entity_id,item=supply.item,slot=supply.slot,purpose=d.plan.purpose}
        end
        if why then npc.phase="designed-start:supply-wait:"..why end
      end
    end
  end
  local stage=d.plan.stages[d.stage_index]
  if not stage then hand_off(npc,tick); return nil,"complete" end
  if stage.clear then   -- ground to clear (scripts/clearing.lua), before anything the stage builds
    local job,meta,wait=Clearing.step(npc,d,stage,tick,acquire)
    if job and job.kind=="mine" then   -- (trees and rocks marked: its robots take them, W-26)
      local why
      job,why=DesignedStart.Robots.instead(npc,job,tick)
      if why then npc.phase="designed-start:clearing:"..why; return nil,why end
    end
    if job then npc.phase="designed-start:clearing:"..stage.id; return job,meta end
    if wait then npc.phase="designed-start:clearing:"..wait; return nil,wait end
  end
  local fought,crafting,skip=false,nil,{}
  for _,entity in ipairs(stage_entities(d,stage)) do
    local existing=not (entity.entity and entity.entity.valid) and adopt(npc,entity)
    if existing then
      entity.entity,entity.built_tick,entity.adopted=existing,tick,true
      d.adopted=(d.adopted or 0)+1
      for i=#(npc.design_salvage or {}),1,-1 do   -- taken over: no longer to be picked up
        if npc.design_salvage[i]==existing then table.remove(npc.design_salvage,i) end
      end
    end
    local here_fought=not (entity.entity and entity.entity.valid) and DesignedStart.fought_over(npc,entity.world)
    fought=fought or here_fought
    if not (entity.entity and entity.entity.valid) and not here_fought and not skip[entity.item] then
      -- The team world: a player has built near here since the plan was made. The rest is given up (what
      -- stands is kept), as with any plan that cannot go on.
      if TeamWorld.active() and TeamWorld.player_built_near(npc.character.surface,
          entity_box(entity.prototype,entity.world,entity.direction)) then
        block_plan(npc,d,"player_zone:"..entity.id,tick)
        return nil,"player_zone"
      end
      local job=clear_footprint(npc,entity)
      if job then
        local why
        job,why=DesignedStart.Robots.instead(npc,job,tick)   -- (a tree or rock there: its robots', W-26)
        if why then npc.phase="designed-start:clearing:"..why; return nil,why end
        return job,{clear=true,entity_id=entity.id}
      end
      local ground_meta
      job,ground_meta=DesignedStart.ground_job(npc,d,entity,tick)
      if job then return job,ground_meta end
      if ground_meta then npc.phase="designed-start:clearing:"..entity.id; return nil,ground_meta end
      job,ground_meta=DesignedStart.check_spot(npc,d,entity,tick)
      if job then return job,ground_meta end
      if ground_meta then return nil,ground_meta end
      local why,kind
      -- (its network holds it for its robots: not fetched nor made, W-26b)
      if DesignedStart.usable(npc,entity.item,tick)<1 and (entity.by_hand
          or not DesignedStart.Robots.network_has(npc,entity.world,entity.item)) then
        -- Out of it: make or fetch a batch for the stage's remaining ones, as a player crafts a stack of
        -- belts before laying the line, not one belt per trip; one if a batch cannot be had yet.
        local need=0
        for _,other in ipairs(stage_entities(d,stage)) do
          if other.item==entity.item and not (other.entity and other.entity.valid) then need=need+1 end
        end
        need=math.max(1,math.min(need,DesignedStart.stage_batch(npc,entity.item,need)))
        job,why,kind=acquire(npc,entity.item,need,0)
        -- (short of a whole batch: as many as what it carries makes now, not one: walls crafted one at a time while it
        -- carried 57 bricks, the user's live game 2026-10-23)
        if not job and why and need>1 then
          local now=DesignedStart.usable(npc,entity.item,tick)+DesignedStart.queued(npc,entity.item)
            +DesignedStart.makeable(npc,entity.item)
          job,why,kind=acquire(npc,entity.item,math.max(1,math.min(need,now)),0)
        end
        -- (nothing to build with and none to be had: it waits, and the plan is given up in time, never built blind)
        if not job and not why then why="waiting_for:"..entity.item end
      end
      if job or why then
        DesignedStart.hand_clock(npc,d,tick,why and why:sub(1,9)=="smelting:" and entity.item or nil)
        if DesignedStart.hand_back(npc,d,tick) then return nil,"yielded" end
      end
      if job then npc.phase="designed-start:acquire:"..entity.item; return job,{acquire=entity.item,entity_id=entity.id,kind=kind} end
      if why and why:sub(1,9)=="crafting:" then
        -- Its crafts are under way in the background: meanwhile the stage's other buildings, from what it holds or
        -- can make, as a player builds while the crafting queue runs.
        crafting=crafting or why
        skip[entity.item]=true
      elseif why then
        npc.phase="designed-start:need:"..why
        -- A building whose recipe is still locked: research may never come in time. Give the plan up. So
        -- too, in a while, one waiting on what nothing of its own makes (steel for a loader, with no steel
        -- smelting: the wall row waited 11 minutes and more, run 20, 2026-10-25).
        local waiting=why:sub(1,12)=="waiting_for:"
        if why:sub(1,14)=="recipe_locked:" or waiting then
          d.locked_since=d.locked_since or tick
          if tick-d.locked_since>=(waiting and DesignedStart.WAITING_FOR or LOCKED_FOR) then block_plan(npc,d,why,tick) end
        else
          -- (every other reason too: store filling, smelting, drills make it, too deep... none had a bound, and an
          -- engineer could wait on one for ever (SO-5's audit). In WAITING_FOR without a build, the plan is handed back,
          -- what stands kept and no failure noted, the strategist told: W-31)
          d.waiting_since=d.waiting_since or tick
          if tick-d.waiting_since>=DesignedStart.WAITING_FOR then
            block_plan(npc,d,"waits:"..why,tick,true)
            note_failure(npc,d.purpose,tick)   -- (held a while, its next goal another: never the same wait again; Codex)
            return nil,"yielded"
          end
        end
        return nil,why
      else
        d.locked_since,d.waiting_since=nil,nil
        DesignedStart.hand_clock(npc,d,tick,nil)
        npc.phase="designed-start:building:"..stage.id
        d.work_at=entity.world   -- (where its work is now: DesignedStart.leaving)
        -- (about to walk off to far work from home: the round at home first, the upkeep's next tick)
        if DesignedStart.leaving(npc,tick) then return nil,"home_round" end
        -- (its robots: the stage's pieces it holds the items for as ghosts they put up from its pockets, while it
        -- stands in their reach; by hand what they cannot do or have not done in a while: W-26)
        if DesignedStart.Robots.reach(npc) or DesignedStart.Robots.network(npc,entity.world) then
          local hand={kind="build",entity_name=entity.prototype,item=entity.item,position=entity.world,
            direction=entity.direction,belt_to_ground_type=entity.belt_to_ground_type,mirror=entity.mirror}
          for _,o in ipairs(stage_entities(d,stage)) do
            if not (o.entity and o.entity.valid) and (DesignedStart.usable(npc,o.item,tick)>0
                or DesignedStart.Robots.network_has(npc,o.world,o.item)) then
              DesignedStart.Robots.ghost(npc,o)
            end
          end
          local job,why=DesignedStart.Robots.instead(npc,hand,tick,entity)
          if why then npc.phase="designed-start:"..why; return nil,why end
          if job~=hand then return job,{robots=true,entity_id=entity.id} end
        end
        local run=build_run_for(npc,d,stage,entity)
        if run then
          local ids={}
          for i,r in ipairs(run) do ids[i]=r.id end
          return {kind="build_run",placements=run},{build_run=true,ids=ids}
        end
        -- (the pieces still to go up near it: no stand point in their places, Worker.stand_point)
        local avoid={}
        for _,o in ipairs(stage_entities(d,stage)) do
          if #avoid>=DesignedStart.AVOID_MAX then break end
          if o~=entity and o.world and not (o.entity and o.entity.valid) and prototypes.entity[o.prototype]
              and math.abs(o.world.x-entity.world.x)<=DesignedStart.AVOID_NEAR
              and math.abs(o.world.y-entity.world.y)<=DesignedStart.AVOID_NEAR then
            avoid[#avoid+1]=entity_box(o.prototype,o.world,o.direction)
          end
        end
        return {kind="build",entity_name=entity.prototype,item=entity.item,position=entity.world,
          direction=entity.direction,belt_to_ground_type=entity.belt_to_ground_type,mirror=entity.mirror,avoid=avoid},
          {build=true,entity_id=entity.id}
      end
    end
  end
  if fought then
    -- Only fought-over pieces left: calm is waited for a while, then the plan goes back to be planned again with where
    -- the biters are (a nest does not leave), and any fight for the spot is the war's to choose (its odds).
    -- (the war is told at once where the work waits on a fight; it weighs the odds: npc.fight_wanted)
    if not d.fought_since then
      for _,e in ipairs(stage_entities(d,stage)) do
        if not (e.entity and e.entity.valid) then
          npc.fight_wanted={position=e.world,tick=tick,purpose=d.purpose}; break
        end
      end
    end
    d.fought_since=d.fought_since or tick
    -- (a defense going up waits longer: it is what the fight is about, and planned again it goes up in the same place)
    local wait=DesignedStart.DEFENSES and DesignedStart.DEFENSES[d.purpose] and 4*DesignedStart.FOUGHT_WAIT
      or DesignedStart.FOUGHT_WAIT
    if not crafting and tick-d.fought_since>=wait then
      local first
      for _,e in ipairs(stage_entities(d,stage)) do
        if not (e.entity and e.entity.valid) then first=e; break end
      end
      npc.fight_wanted={position=first and first.world,tick=tick,purpose=d.purpose}
      DesignedStart.Obstacles.record(npc,d,first,"hand_back","enemies",tick,"waited "..(tick-d.fought_since).." ticks")
      block_plan(npc,d,"in_the_way:"..tostring(first and first.id)..":enemies",tick)
      return nil,"blocked"
    end
    npc.phase="designed-start:waiting-for-calm:"..stage.id
    return nil,"fought_over"
  end
  d.fought_since=nil
  if crafting then
    DesignedStart.hand_clock(npc,d,tick,nil)   -- (its queued crafts were counted on their receipts)
    if DesignedStart.hand_back(npc,d,tick) then return nil,"yielded" end
    npc.phase="designed-start:crafting:"..stage.id
    -- Only what is still being crafted left: it walks to where the nearest of it goes meanwhile, as a player crafts on
    -- the way to the build; there already, it waits, and the wait is tallied (time_use "wait:craft").
    local c=npc.character
    local best,bd
    for _,e in ipairs(stage_entities(d,stage)) do
      if skip[e.item] and not (e.entity and e.entity.valid) then
        local dd=(e.world.x-c.position.x)^2+(e.world.y-c.position.y)^2
        if not bd or dd<bd then best,bd=e,dd end
      end
    end
    local reach=(try(function() return c.build_distance end) or 10)-1
    if best and bd>reach*reach then
      npc.craft_idle_last=nil
      return {kind="move",position={x=best.world.x,y=best.world.y}},{upkeep="preposition"}
    end
    local last=npc.craft_idle_last
    if last and tick-last<=DesignedStart.WAIT_GAP and tick>last then
      local t=npc.time_use or {since=tick,tags={}}
      npc.time_use=t
      t.tags["wait:craft"]=(t.tags["wait:craft"] or 0)+(tick-last)
    end
    npc.craft_idle_last=tick
    return nil,crafting
  end
  npc.craft_idle_last=nil
  if not stage.witness_tick then start_witnesses(d,stage); stage.status="producing"; return nil,"started" end
  local waited_why,smelt_item
  -- (its witnesses met already, by what it made or what made it unneeded: nothing more fed for it; a start-up supply
  -- waiting would hold the stage before they were looked at, W-21)
  for _,supply in ipairs(stage.supplies) do
    local job,why
    if not DesignedStart.backed_off(npc,supply,tick) and not DesignedStart.start_up_unneeded(npc,d,stage,supply) then
      job,why=supply_job(npc,d.plan.entities[supply.entity_id],supply)
    end
    if why and why:sub(1,9)=="smelting:" and not smelt_item then smelt_item=supply.item end
    if job or DesignedStart.hand_wait(why) then DesignedStart.hand_clock(npc,d,tick,smelt_item) end   -- (before a yield)
    if (job or DesignedStart.hand_wait(why)) and DesignedStart.hand_back(npc,d,tick) then return nil,"yielded" end
    if job then
      if supply.once then stage.once_wait=nil end
      return job,{supply=true,entity_id=supply.entity_id,item=supply.item,slot=supply.slot,purpose=d.purpose}
    end
    -- (one supply waiting holds neither the others nor the stage: what it is for may run without it, its witnesses say;
    -- returned at once, one ore wait held a whole expansion 9 minutes, long-64. What only starts the stage, `once`, still
    -- holds it: its start-up input)
    if why and supply.once then
      DesignedStart.hand_clock(npc,d,tick,smelt_item)
      npc.phase="designed-start:supply-wait:"..why
      -- (its start-up input waited on no longer than WAITING_FOR: then handed back, what stands kept; W-31)
      stage.once_wait=stage.once_wait or tick
      if tick-stage.once_wait>=DesignedStart.WAITING_FOR then
        block_plan(npc,d,"start_up_waits:"..tostring(supply.item)..":"..why,tick,true)
        note_failure(npc,d.purpose,tick)
        return nil,"yielded"
      end
      return nil,why
    end
    if why and not waited_why then waited_why=why end
  end
  DesignedStart.hand_clock(npc,d,tick,smelt_item)   -- (once for the pass: a later supply no longer resets a smelting wait)
  for _,w in ipairs(stage.witnesses) do
    -- (one that can never pass is dropped after a while, said why: it held its whole plan forever, R-7)
    if not w.dropped and not witness_met(d,w,npc) and not DesignedStart.witness_doomed(npc,d,stage,w,tick) then
      npc.phase=waited_why and ("designed-start:supply-wait:"..waited_why) or ("designed-start:producing:"..stage.id)
      local waited=tick-(stage.witness_tick or tick)
      -- (a building left out, and the stage does not work without it in a while: the plan is given up)
      if stage.left_out_tick and tick-math.max(stage.left_out_tick,stage.witness_tick or 0)>DesignedStart.LEFT_OUT_GRACE then
        block_plan(npc,d,"left_out_needed:"..stage.id,tick)
        return nil,"blocked"
      end
      if waited>=STUCK_AFTER and tick>=(stage.stuck_logged or 0) then
        stage.stuck_logged=tick+STUCK_AFTER
        stage.stuck=stuck_report(d,stage,tick)
        log("[npc-factions] npc="..npc.id.." stage "..stage.id.." producing for "..waited.." ticks: "..stage.stuck)
      end
      -- (its witness not met in STUCK_FOR: handed back, what stands kept, no failure noted, its stuck report its reason
      -- and the strategist told; a lab stage waited 496,000 ticks for packs, normal-7b, SO-5's audit: W-31)
      -- (its repair given up, the corrector says it needs a redesign: handed back now, what stands kept, its goal held a
      -- while; it held every goal "producing", Factory's grown bus, W-32)
      if d.repair_given_up and d.repair_given_up.stage~=nil and d.repair_given_up.stage~=stage.id then
        d.repair_given_up=nil   -- (a piece of another stage, done or to come: this one works without it; Codex)
      end
      if d.repair_given_up then
        local g=d.repair_given_up
        block_plan(npc,d,"repair_given_up:"..tostring(g.symptom)..":"..tostring(g.x)..","..tostring(g.y),tick,true)
        note_failure(npc,d.purpose,tick)
        return nil,"yielded"
      end
      if waited>=DesignedStart.STUCK_FOR then
        block_plan(npc,d,"stuck:"..stage.id..":"..tostring(stage.stuck or stuck_report(d,stage,tick)):sub(1,60),tick,true)
        note_failure(npc,d.purpose,tick)
        return nil,"yielded"
      end
      return nil,"producing"
    end
  end
  stage.status,stage.completed_tick="complete",tick
  if stage.line_ops then DesignedStart.lines_apply(npc,stage.line_ops,tick) end
  for _,a in ipairs(stage.automates or {}) do
    for _,plan in ipairs(npc.design_history or {}) do
      if plan.purpose==a.purpose then
        for _,s in ipairs(plan.stages) do
          for _,supply in ipairs(s.supplies) do
            if supply.entity_id==a.entity_id and supply.item==a.item then supply.automated=tick end
          end
        end
      end
    end
  end
  d.stage_index=d.stage_index+1
  npc.phase="designed-start:stage-complete:"..stage.id
  if not d.plan.stages[d.stage_index] then hand_off(npc,tick) end
  return nil,"stage_complete"
end


-- Storage: surplus goes into the NPC's own chests near home instead of
-- filling its inventory. acquire() looks in these chests before mining.
local STORAGE_CHEST="iron-chest"
local STORE_ABOVE=150        -- carrying more than this of a material: store the rest
local KEEP=50                -- keep this many in the inventory when storing

local function storage_chests(npc)
  local out={}
  for _,chest in ipairs(npc.storage_chests or {}) do if chest.valid then out[#out+1]=chest end end
  npc.storage_chests=out
  return out
end

local function storable(name)
  local item=prototypes.item[name]
  return item and item.type=="item" and not item.place_result and not name:match("science%-pack$")
end

local function storage_spot(npc)
  local surface,home=npc.character.surface,npc.home
  for radius=2,12 do
    for dx=-radius,radius do for dy=-radius,radius do
      if math.max(math.abs(dx),math.abs(dy))==radius then
        local p={x=math.floor(home.x)+dx+0.5,y=math.floor(home.y)+dy+0.5}
        -- Truly free ground: a chest is not worth clearing trees for, and a tree there made the
        -- engineer try the same spot forever.
        if surface.can_place_entity{name=STORAGE_CHEST,position=p,force=npc.force,
            build_check_type=defines.build_check_type.manual} then return p end
      end
    end end
  end
end

local function store_job(npc)
  local inventory=npc.character.get_main_inventory()
  local crowded=inventory.count_empty_stacks()<FREE_SLOTS+4
  local surplus
  for _,stack in ipairs(inventory.get_contents()) do
    if (stack.quality=="normal" or stack.quality==nil) and storable(stack.name) then
      local keep=stack.name=="coal" and 100 or KEEP
      if stack.count>STORE_ABOVE or (crowded and stack.count>keep) then
        if not surplus or stack.count-keep>surplus.count then surplus={name=stack.name,count=stack.count-keep} end
      end
    end
  end
  if not surplus then return nil end
  for _,chest in ipairs(storage_chests(npc)) do
    if chest.get_inventory(defines.inventory.chest).can_insert({name=surplus.name,quality="normal"}) then
      local room=chest.get_inventory(defines.inventory.chest).get_insertable_count({name=surplus.name,quality="normal"})
      return {kind="transfer",target=chest,item=surplus.name,count=math.min(surplus.count,room),mode="deposit"},
        {upkeep="store",item=surplus.name}
    end
  end
  if count(npc,STORAGE_CHEST)>0 then
    local spot=storage_spot(npc)
    if spot then
      return {kind="build",entity_name=STORAGE_CHEST,item=STORAGE_CHEST,position=spot,direction=0},
        {upkeep="storage_chest"}
    end
    return nil
  end
  local job=acquire(npc,STORAGE_CHEST,1,0)
  if job then return job,{upkeep="storage_parts"} end
end

-- Keep every finished designed factory running: refuel and hand-feed declared
-- supplies, and empty furnace outputs and drill chests before they fill up.
-- Upkeep comes before building, but while a plan is being built at least every third job builds:
-- an engineer with many burner machines to fuel and chests to empty would otherwise never finish.
local UPKEEP_STREAK=2
local DEFENSES={["outpost-defense"]=true,["basic-defense"]=true,["ally-defense"]=true,
  -- the team world's wall line: turret groups first, the wall, more layers where they broke through
  ["plot-turrets"]=true,["plot-wall"]=true,["breach-repair"]=true,
  -- defense where the base is pressed (companion/defense_brain.py), and the turrets it grew from
  ["reinforce"]=true,["starter-turrets"]=true}
DesignedStart.DEFENSES=DEFENSES
-- A new attack on its plot (or a breach) while it builds something else: the build is set aside and the planner
-- asked what now, with the threat it weighs (companion/threat.py): a defense when it is urgent, else straight
-- back to the build. Urgency was looked at only between goals: a coal build ran 60 minutes and more whatever
-- hit the base, and only its rule strategist could break in (the review, 2026-10-25). Once a wave, at most
-- once a RECONSIDER_EVERY, never with a build already set aside or a defense going up.
DesignedStart.RECONSIDER_EVERY=3600
function DesignedStart.reconsider(npc,tick)
  local d=npc.design
  if not (d and d.status=="building") or npc.paused_design or DEFENSES[d.purpose] then return false end
  local newest=TeamWorld.active() and TeamWar.newest and TeamWar.newest(npc)
  if not newest or newest<=math.max(npc.reconsidered_for or 0,d.requested_tick or 0) then return false end
  if tick-(npc.reconsidered_tick or -1e9)<DesignedStart.RECONSIDER_EVERY then return false end
  npc.reconsidered_for,npc.reconsidered_tick=newest,tick
  Agent.set_aside(npc,"choose (attacked)")
  DesignedStart.begin(npc,tick,"Its base was attacked while it built "..tostring(d.purpose)..": defend it if that is "
    .."urgent, else carry on with the build.","choose")
  npc.design.reconsider=true
  return true
end

-- The team world: one engineer asks which research to add at a time. The queue takes one or two picks of
-- theirs, so seven asking at once threw six answers away (the token audit, 2026-10-23). A question that
-- has waited its time (it is answered by default then) holds nobody up.
local function teammate_asking(npc,tick)
  if not TeamWorld.active() then return false end
  for _,other in pairs(storage.npcs or {}) do
    local r=other~=npc and other.research_request
    if r and tick-r.tick<=RESEARCH_WAIT and other.character and other.character.valid then return true end
  end
  return false
end
DesignedStart.teammate_asking=teammate_asking

local upkeep_job
-- Back after dying: what it carried lies in its corpse, and it fetches it once no biters are near it, as
-- a player goes back for its body (a death world's walls, bricks and magazines, and its gun). Only what
-- the worker moves (items, ammo, packs); given up after a while, or once the corpse is gone or empty.
DesignedStart.CORPSE_KEEP=108000   -- ticks (30 minutes)
function DesignedStart.corpse_job(npc,tick)
  local at=npc.corpse_at
  local c=npc.character
  if not (at and c and c.valid) then return nil end
  if tick-(at.tick or tick)>DesignedStart.CORPSE_KEEP or (at.tries or 0)>=30 then npc.corpse_at=nil return nil end
  local surface=c.surface
  local corpse=surface.find_entities_filtered{type="character-corpse",position=at,radius=12,limit=1}[1]
  local inv=corpse and corpse.valid and corpse.get_inventory(defines.inventory.character_corpse)
  if not inv or inv.is_empty() then npc.corpse_at=nil return nil end
  if surface.count_entities_filtered{force="enemy",type="unit",position=corpse.position,radius=32,limit=1}>0 then
    return nil   -- (not back into the fight it lost)
  end
  for _,it in ipairs(inv.get_contents()) do
    local proto=prototypes.item[it.name]
    local kind=proto and proto.type
    if (it.quality or "normal")=="normal" and (kind=="item" or kind=="ammo" or kind=="tool") then
      at.tries=(at.tries or 0)+1
      return {kind="transfer",target=corpse,item=it.name,count=it.count,mode="withdraw",
        inventory=defines.inventory.character_corpse}
    end
  end
  npc.corpse_at=nil   -- (nothing left it can carry off)
  return nil
end

-- Its hurt buildings mended (W-33, scripts/mend.lua): low work, only with no stage of a plan going up, nothing urgent
-- and no fight on; a patch at a time, its turrets first. Repair packs from its stores or the mall, else a few made by
-- hand. (job, meta) or nil.
DesignedStart.Mend=require("scripts.mend")
function DesignedStart.mend_now(npc,tick)
  local M=DesignedStart.Mend
  local c=npc.character
  if not (c and c.valid) or tick<(npc.mend_next or 0) then return nil end
  if M.yields(npc) or (Agent.urgent and Agent.urgent(npc)) or DesignedStart.attacked(npc,tick) then return nil end
  npc.mend_next=tick+M.LOOK_EVERY
  local targets,lost=M.patch(npc,PLAN_REACH,DesignedStart.Robots)
  if not targets then return nil end
  local have=(DesignedStart.usable(npc,M.PACK))
  if have<1 then
    local job,why=acquire(npc,M.PACK,math.min(M.packs_for(lost),M.CRAFT_BATCH),0)
    if job then return job,{upkeep="mend_packs",acquire=M.PACK} end
    if why and not why:match("^crafting:") then npc.mend_next=tick+M.BACK_OFF end
    return nil
  end
  return {kind="mend",targets=targets},{upkeep="mend",count=#targets,lost=math.floor(lost)}
end

function DesignedStart.upkeep(npc,tick)
  do
    local job=DesignedStart.corpse_job(npc,tick)
    if job then return job,{upkeep="corpse"} end
  end
  -- Its turrets loaded before anything else of upkeep, and past the building's share (DesignedStart.defense_supply).
  do
    local job,meta=DesignedStart.defense_supply(npc,tick)
    if job then return job,meta end
  end
  -- What biters or raiders destroyed goes back before the routine rounds, before anything is taken down, and past the
  -- building's share (a lost drill or belt stops everything it fed until then); while a defense goes up, what defends
  -- comes first (rebuild_job).
  if ((npc.rebuild and #npc.rebuild>0) or (npc.rebuild_untold and #npc.rebuild_untold>0)) and npc.character
      and npc.character.valid then
    local defending=npc.design and (npc.design.status=="building" or npc.design.status=="waiting")
      and DEFENSES[npc.design.purpose] and DesignedStart.pieces_left(npc.design)   -- (as rebuild_now: pieces to go up)
    local opts=DesignedStart.rebuild_opts(npc)
    local again,again_meta=rebuild_job(npc,tick,defending,opts and opts.near and opts or nil)
    if again then return again,again_meta end
  end
  local building=npc.design and npc.design.status=="building"
  -- (at home with its work far off: the whole round before it goes, the streak no limit; DesignedStart.leaving)
  local leaving=building and DesignedStart.leaving(npc,tick)
  if building and not leaving and (npc.upkeep_streak or 0)>=UPKEEP_STREAK then npc.upkeep_streak=0; return nil end
  local job,meta=upkeep_job(npc,tick,leaving)
  if job and building then npc.upkeep_streak=(npc.upkeep_streak or 0)+1 elseif not job then npc.upkeep_streak=0 end
  if leaving and not job then npc.home_round.done=true end
  return job,meta
end

-- Work far off and the engineer at home: before it goes (or once a burner about to stop brought it back), everything
-- due within the sweep is seen to in one visit, then it goes and stays. Two jobs of upkeep and back out had it walk
-- 260 tiles home (45 s each way) between walls, the user's live game 2026-10-23. AWAY: tiles from home that count as
-- far; HOME_ROUND: ticks such a round lasts at most.
DesignedStart.AWAY=64
DesignedStart.HOME_ROUND=3600
-- How far it reaches (its body's reach, far-reach's bonus with it: reach.lua), and so how far is far: AWAY at the least,
-- else what it reaches from where it stands. With the user's far reach a furnace 100 tiles off is in reach; counted far
-- by vanilla's measure, it would be left for a walk that is not needed (W-14, 2026-10-28).
function DesignedStart.reach(npc)
  local c=npc.character
  return c and c.valid and (try(function() return c.reach_distance end) or 10) or 10
end
function DesignedStart.far(npc) return math.max(DesignedStart.AWAY,DesignedStart.reach(npc)) end
function DesignedStart.leaving(npc,tick)
  local d,c=npc.design,npc.character
  local at,home=d and d.work_at,npc.home
  local A=DesignedStart.far(npc)
  if not (at and home and c and c.valid and (at.x-home.x)^2+(at.y-home.y)^2>A*A
      and (c.position.x-home.x)^2+(c.position.y-home.y)^2<=A*A) then
    npc.home_round=nil
    return false
  end
  local r=npc.home_round
  if not r then r={since=tick}; npc.home_round=r end
  return not r.done and tick-r.since<=DesignedStart.HOME_ROUND
end

-- Out at far work: its plan's work and the engineer both more than AWAY from home (what can wait at home waits).
function DesignedStart.away(npc)
  local d,c=npc.design,npc.character
  local at,home=d and d.status=="building" and d.work_at,npc.home
  local A=DesignedStart.far(npc)
  return (at and home and c and c.valid and (at.x-home.x)^2+(at.y-home.y)^2>A*A
    and (c.position.x-home.x)^2+(c.position.y-home.y)^2>A*A) and true or false
end

upkeep_job=function(npc,tick,leaving)
  if not (npc.character and npc.character.valid) then return nil end
  -- Research: ask the host (System One) which technology to research next;
  -- without an answer in time, take the cheapest one on the rocket path.
  -- (one tech tree: the players run the queue, so it asks when there is room for a pick of its own)
  -- (alone: also when nothing makes its current research's packs, a pick then going ahead of it; lesson 19)
  local current=not TeamWorld.active() and npc.force.current_research
  local stuck=current and #labs(npc)>0 and #DesignedStart.unmade_packs(npc,current,{})>0 or false
  local wants_research=TeamWorld.active() and TeamResearch.room() or not TeamWorld.active() and (not current or stuck)
  if #labs(npc)>0 and wants_research and tick>=(npc.research_retry_at or 0) then
    local technology=npc.research_choice
    local request=npc.research_request
    if not technology and request and tick-request.tick>RESEARCH_WAIT then
      technology=request.options[1].name; request.defaulted=true
    end
    if technology then
      local need=npc.research_need
      npc.research_choice,npc.research_request,npc.research_need=nil,nil,nil
      npc.research_retry_at=tick+600
      return {kind="select_research",technology=technology,need=need,ahead=stuck or nil},
        {upkeep="research",technology=technology}
    end
    if not request and not teammate_asking(npc,tick) then
      local options=research_options(npc)
      if #options>0 then
        local seq=(storage.design_sequence or 0)+1; storage.design_sequence=seq
        npc.research_request={request_id=npc.id..":r"..seq,tick=tick,options=options}
        npc.phase="designed:choosing-research"
      end
    end
  end
  -- A scouting trip goes on before anything else here: an engineer whose very first plan sent it looking
  -- has nothing built yet (a Factorio 2.1 map, 2026-10-23: it never walked and sat in the scripted start).
  do
    local building=DesignedStart.active(npc)
    if npc.scout and not building then
      local job,outcome=Scouting.next(npc,tick)
      if job then return job,{upkeep="scout"} end
      local trip=npc.scout
      npc.scout=nil
      local d=npc.design
      if outcome=="found" then
        -- the plan it went scouting for is asked for again (its first one too: not some other plan)
        if d and d.status=="deferred" and d.purpose==trip.purpose then DesignedStart.begin(npc,tick,d.goal,d.purpose)
        elseif d and d.status=="deferred" then d.next_goal_tick=tick+300 end
        Character.speak(npc,"scout_found",nil,{resource=Voice.pretty(trip.resource)},{priority=1})
      else
        note_failure(npc,trip.purpose,tick)
        if d and d.status=="deferred" then d.next_goal_tick=tick+1800 end
      end
    end
  end
  if not npc.design_history or #npc.design_history==0 then return nil end
  -- While a new plan is being built, only keep machines from stopping.
  local building=DesignedStart.active(npc)
  -- Take back what a blocked plan left standing (contents and the building
  -- return to the inventory); give up on an entity after repeated failures.
  if npc.design_salvage and not building then
    while #npc.design_salvage>0 do
      local e=npc.design_salvage[#npc.design_salvage]
      -- A piece a later plan took over (an identical building it adopted) stays where it is.
      if e.valid and e.minable and (npc.salvage_tries or 0)<4 and not used_by_a_plan(npc,e) then
        npc.salvage_tries=(npc.salvage_tries or 0)+1
        return {kind="mine",target=e,count=1,mode="deconstruct"},{upkeep="salvage"}
      end
      table.remove(npc.design_salvage)
      npc.salvage_tries=0
    end
    npc.design_salvage=nil
  end
  -- A furnace whose output is full has stopped: when the engineer is short of what it makes, empty the
  -- nearest one before topping up machines that still run. With plenty on hand it may back up, as a
  -- player's smelting does while nothing uses it yet (a trip to empty it would be wasted).
  local inventory=npc.character.get_main_inventory()
  local best,best_d,best_id
  local here=npc.character.position
  -- (out at far work, plates can wait at home: a trip home for a full furnace's plates cost 90 s of its walls, the
  -- user's live game replayed 2026-10-26)
  local away=DesignedStart.away(npc)
  for _,plan in ipairs(away and {} or npc.design_history) do
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="furnace" then
        for _,stack in ipairs(e.get_output_inventory().get_contents()) do
          if stack.count>=90 and count(npc,stack.name)<SHORT_OF and inventory.can_insert({name=stack.name,quality="normal"}) then
            local d=(e.position.x-here.x)^2+(e.position.y-here.y)^2
            if not best_d or d<best_d then best,best_d,best_id=e,d,id end
          end
        end
      end
    end
  end
  if best and inventory.count_empty_stacks()>=2 then
    local stack=best.get_output_inventory().get_contents()[1]
    return {kind="transfer",target=best,item=stack.name,count=stack.count,mode="withdraw"},
      {upkeep="collect",entity_id=best_id}
  end
  -- Machines to keep fed, nearest first: one sweep along a row instead of walking the build order.
  -- The plan being built counts too: a boiler standing before its coal belt is done is fed by hand.
  local feed={}
  local fed_plans={}
  for _,plan in ipairs(npc.design_history) do fed_plans[#fed_plans+1]=plan end
  if building and npc.design and npc.design.plan then fed_plans[#fed_plans+1]=npc.design.plan end
  for _,plan in ipairs(fed_plans) do
    for _,stage in ipairs(plan.stages) do
      for _,supply in ipairs(stage.supplies) do
        local entity=plan.entities[supply.entity_id]
        local e=entity and entity.entity
        if e and e.valid then
          feed[#feed+1]={entity=entity,supply=supply,d=(e.position.x-here.x)^2+(e.position.y-here.y)^2,plan=plan}
        end
      end
    end
  end
  table.sort(feed,function(a,b) return a.d<b.d end)
  local batched={}
  -- the fuel every burner due within the sweep wants (its fill, and its share for FUEL.HAND seconds: what is gathered
  -- by hand for them), worked out once, and those burners (for the round's split); burners their own drills fuel (a
  -- coal loop) want no share and are no part of it
  local function batch(item)
    if batched[item] then return batched[item][1],batched[item][2],batched[item][3] end
    local F,total,shares,due=DesignedStart.FUEL,0,0,{}
    local fed=DesignedStart.fed_stores(npc).stores
    local seen={}
    for _,f in ipairs(feed) do
      local e=f.entity.entity
      local inv=f.supply.slot=="fuel" and f.supply.item==item and not f.supply.automated and try(function() return e.get_fuel_inventory() end)
      local left,full
      local judged
      if inv then left,full,judged=DesignedStart.fuel_time(e,inv,item) end
      if left and left<math.min(F.SWEEP,full*F.SWEEP_SHARE) and not seen[e.unit_number or e] then
        seen[e.unit_number or e]=true
        -- (nor those its inserters feed, still burning)
        if not (e.unit_number and fed[e.unit_number..":"..item]) and not (left>0 and DesignedStart.auto_fed(npc,e,item)) then
          total=total+math.max(0,math.floor(FUEL_FILL-left*FUEL_FILL/full))
          shares=shares+(DesignedStart.fuel_share(e,inv,item,F.HAND) or 0)
          local rate=judged or DesignedStart.burn_rate(e)   -- (as its time left was judged)
          if rate and e.unit_number then due[#due+1]={key=e.unit_number,left=left,rate=rate,cap=full} end
        end
      end
    end
    batched[item]={total,shares,due}
    return total,shares,due
  end
  local function wanted(f)   -- (its supply job now, unless set aside: once, backed off, far off from far work)
    local supply=f.supply
    local backoff=npc.supply_backoff and npc.supply_backoff[supply.entity_id..":"..supply.item]
    -- Brick furnaces in plans saved before "once" existed are build-time only too, as is any furnace's ore carried
    -- by hand in a plan kept from before that rule (a bootstrap: its drills feed it).
    local e=f.entity.entity
    local once=supply.once or supply.entity_id=="brick_furnace"
      or (supply.slot=="input" and RAW[supply.item] and e and e.valid and e.type=="furnace")
    -- (out at far work, a machine's inputs far off wait for its return; only fuel about to stop and turrets' ammo call it:
    -- it walked home for a machine's 84 plates in the replayed live game, 2026-10-26)
    if away and supply.slot~="fuel" and supply.slot~="ammo" and e and e.valid
        and (e.position.x-here.x)^2+(e.position.y-here.y)^2>DesignedStart.far(npc)^2 then
      once=true
    end
    return not once and not (backoff and tick<backoff.until_tick)
      and supply_job(npc,f.entity,supply,leaving and "leaving" or building,batch) or nil
  end
  for _,f in ipairs(feed) do
    local supply=f.supply
    local job=wanted(f)
    if job then
      -- (the others near it due the same, fuel by how low each is, ore or plates into inputs: one sweep from one spot)
      if job.kind=="transfer" and job.mode=="deposit" and job.target and job.target.valid then
        local R=DesignedStart.reach(npc)
        local at=job.target.position
        local more={}
        for _,g in ipairs(feed) do
          if #more>=DesignedStart.SWEEP_MAX then break end
          local e=g~=f and g.supply.item==supply.item and g.supply.slot==supply.slot and g.entity.entity
          if e and e.valid and (e.position.x-at.x)^2+(e.position.y-at.y)^2<=R*R then
            local j2=wanted(g)
            if j2 and j2.kind=="transfer" and j2.mode=="deposit" and j2.item==job.item then
              more[#more+1]={target=j2.target,count=j2.count,inventory=j2.inventory}
            end
          end
        end
        job=DesignedStart.sweep_of(npc,job,more)
      end
      return job,{upkeep="supply",entity_id=supply.entity_id,item=supply.item,slot=supply.slot,
        purpose=f.plan and f.plan.purpose}
    end
  end
  -- (labs fed between other work, not from far work: they wait for its return home)
  local packs,packs_meta
  if not away then packs,packs_meta=lab_packs(npc) end
  if packs then return packs,packs_meta end
  -- Small repairs to what was built (rotate an inserter, an overflow chest, a pole line):
  -- one at a time, from what the buildings report, without a redesign; a full store only when short of what it holds
  -- (as a full furnace above), and out at far work, one far from it waits for its return.
  local fix,fix_meta=Corrector.step(npc,tick,acquire,{away=away,near=DesignedStart.far(npc),
    short=function(item) return item~=nil and count(npc,item)<SHORT_OF end})
  if fix then return fix,fix_meta end
  -- Its own magazines (it fights with them and loads turrets from them): a few in reserve, made
  -- like any other item, as a player keeps some on hand.
  if not building and count(npc,"firearm-magazine")<CARRY_LOW then
    if Recipes.ready(npc.force,"firearm-magazine") then
      local job=acquire(npc,"firearm-magazine",CARRY_KEEP,0)
      if job then return job,{upkeep="ammo"} end
    end
  end
  -- A rocket ready in the silo: the starter pack, a platform, the launch (before a build under way: Orbit.step).
  local launch,launch_meta=Orbit.step(npc,tick,acquire,count,building)
  if launch then return launch,launch_meta end
  -- Surplus goes to a chest when the inventory runs short of room (or between plans), not in a rush.
  if not building or inventory.count_empty_stacks()<FREE_SLOTS then
    local stored,store_meta=store_job(npc)
    if stored then return stored,store_meta end
  end
  for _,plan in ipairs(away and {} or npc.design_history) do
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id].entity
      if e and e.valid and (e.type=="furnace" or e.type=="container") then
        local output=e.type=="container" and e.get_inventory(defines.inventory.chest) or e.get_output_inventory()
        -- While building, a chest is emptied only when it is nearly full (a furnace's one output
        -- slot at 90): collecting sooner would keep the engineer from the plan it is building.
        if building and e.type=="container" and output and output.count_empty_stacks()>1 then output=nil end
        for _,stack in ipairs(output and output.get_contents() or {}) do
          local item={name=stack.name,quality="normal"}
          if stack.count>=(building and 90 or COLLECT_AT) and count(npc,stack.name)<CARRY_CAP
              and inventory.count_empty_stacks()>=FREE_SLOTS and inventory.can_insert(item) then
            return {kind="transfer",target=e,item=stack.name,count=stack.count,mode="withdraw"},
              {upkeep="collect",entity_id=id}
          end
        end
      end
    end
  end
end

-- After a designed factory is finished, the NPC periodically asks the host
-- what to build next (the host may also defer to the scripted project chain).
DesignedStart.WITNESS_MAX=5   -- items a production witness asks for at most

-- The base's belt lines (companion/belt_lines.py): what a stage lays (a line: its plain belts' tiles in order, its
-- item, what goes on at its head, and the claim of what it was laid for) or taps (a claim more on a line), recorded
-- once the stage stands, so every later plan knows what each line carries and what is spare. A bus stage reports the
-- bus's lanes (kind "bus": a lane's two ends, its group, all its taps' claims at once). Probe tiles are where the
-- game samples how full the line is (BaseMemory.line_tick).
DesignedStart.LINE_PROBES=48
function DesignedStart.line_op(op)
  if type(op)~="table" then return nil end
  local function short(v) return type(v)=="string" and #v<=64 and v or nil end   -- (no main-chunk local: at 200)
  local function tile(t)   -- {x, y} or {x, y, direction}: the belt's way, for reading a few tiles behind it
    if type(t)~="table" then return nil end
    local x,y,d=tonumber(t[1]),tonumber(t[2]),tonumber(t[3])
    if not (x and y and math.abs(x)<1e6 and math.abs(y)<1e6) then return nil end
    return {math.floor(x),math.floor(y),d and math.floor(d) or nil}
  end
  local rate=tonumber(op.rate)
  if op.op=="tap" and short(op.line) and short(op.key) and rate then
    return {op="tap",line=op.line,key=op.key,rate=rate,probe=tile(op.probe)}
  end
  -- (a line's claims come one at a time, its consumer's with it, or all at once: a bus lane's taps)
  local claims
  if type(op.claims)=="table" then
    claims={}
    local n=0
    for k,v in pairs(op.claims) do
      n=n+1
      if n>64 or not short(k) or not tonumber(v) then return nil end
      claims[k]=tonumber(v)
    end
  end
  if op.op=="line" and short(op.id) and short(op.item) and (claims or (short(op.key) and rate))
      and type(op.tiles)=="table" then
    local tiles={}
    for i,t in ipairs(op.tiles) do
      if i>4000 then break end
      local x,y,d=type(t)=="table" and tonumber(t[1]),type(t)=="table" and tonumber(t[2]),type(t)=="table" and tonumber(t[3])
      if not (x and y and d) then return nil end
      tiles[#tiles+1]={math.floor(x),math.floor(y),math.floor(d)}
    end
    if #tiles==0 then return nil end
    local probes
    if type(op.probes)=="table" then
      probes={}
      for _,t in ipairs(op.probes) do
        local at=tile(t)
        if at and #probes<DesignedStart.LINE_PROBES then probes[#probes+1]=at end
      end
    end
    return {op="line",id=op.id,item=op.item,key=not claims and op.key or nil,rate=not claims and rate or nil,
      claims=claims,supply=tonumber(op.supply) or 0,tiles=tiles,kind=short(op.kind),group=short(op.group),
      probes=probes,unread=tonumber(op.unread),sides=DesignedStart.line_sides(op.sides,short),
      side_supply=DesignedStart.line_sides(op.side_supply,tonumber)}
  end
  return nil
end

-- A belt's two sides (left and right along its way), each its own item or rate: a belt may carry two items, one a
-- side (ore and coal into a furnace row, iron and gears for red science; Ledger's L-4). Kept only when well formed.
function DesignedStart.line_sides(raw,check)
  if type(raw)~="table" then return nil end
  local out
  for _,side in ipairs({"left","right"}) do
    local v=check(raw[side])
    if v then out=out or {}; out[side]=v end
  end
  return out
end

function DesignedStart.lines_apply(npc,ops,tick)
  npc.belt_lines=npc.belt_lines or {}
  for _,op in ipairs(ops or {}) do
    if op.op=="line" then
      local old=npc.belt_lines[op.id]
      local claims=op.claims or old and old.claims or {}
      if not op.claims then claims[op.key]=op.rate end
      -- (what the game measured of it so far stays: a bus lane is reported again with every stage)
      npc.belt_lines[op.id]={id=op.id,item=op.item,tiles=op.tiles,supply=op.supply,claims=claims,tick=tick,
        kind=op.kind,group=op.group,probes=op.probes or old and old.probes,unread=op.unread,sides=op.sides,
        side_supply=op.side_supply,fill=old and old.fill,
        measured=old and old.measured}
    elseif op.op=="tap" then
      local line=npc.belt_lines[op.line]
      if line then
        line.claims[op.key]=op.rate
        -- how full the line comes to the tap: a probe on the tile before it
        local p=op.probe
        local probes=line.probes or {}
        local known=false
        for _,q in ipairs(probes) do if p and q[1]==p[1] and q[2]==p[2] then known=true end end
        if p and not known and #probes<DesignedStart.LINE_PROBES and line.probes then probes[#probes+1]=p end
      end
    end
  end
end

-- Whether a building stands on one of the base's belt lines laid for their consumers: it stays when the build that
-- laid it is retired (what else the line feeds would starve). (The bus's lanes go as the bus planner says.)
function DesignedStart.on_line(npc,e)
  if not (npc.belt_lines and e and e.valid) then return false end
  local x,y=math.floor(e.position.x),math.floor(e.position.y)
  for _,line in pairs(npc.belt_lines) do
    if line.kind~="bus" then
      for _,t in ipairs(line.tiles) do if t[1]==x and t[2]==y then return true end end
    end
  end
  return false
end

function DesignedStart.goal_due(npc,tick,grace)
  local d=npc.design
  if not d then return false end
  local retry_blocked=d.status=="blocked" and (npc.design_history and #npc.design_history>0 or d.next_goal_tick~=nil)
  if d.status~="complete" and d.status~="deferred" and not retry_blocked then return false end
  if storage.test then
    if not storage.test.goals then return false end
  elseif not DesignedStart.designed_mode() then return false end
  if npc.factory_project and npc.factory_project.status=="active" then return false end
  return tick>=(d.next_goal_tick or 0)+(grace or 0)
end

function DesignedStart.choose_research(payload)
  if type(payload)~="table" then return reject("payload") end
  for _,npc in pairs(storage.npcs or {}) do
    local r=npc.research_request
    if r and r.request_id==payload.request_id then
      if payload.idle then
        -- (none of the offered serves anything: the labs wait, those techs set aside for a while; R-10)
        npc.research_unused=npc.research_unused or {}
        for name,until_tick in pairs(npc.research_unused) do
          if until_tick<=game.tick then npc.research_unused[name]=nil end
        end
        -- (only those the companion judged unused: military it held back for its share stays on offer)
        local judged=type(payload.unused)=="table" and payload.unused or nil
        for _,option in ipairs(r.options) do
          local listed=not judged
          for _,n in ipairs(judged or {}) do if n==option.name then listed=true; break end end
          if listed then npc.research_unused[option.name]=game.tick+DesignedStart.UNUSED_TICKS end
        end
        npc.research_request,npc.research_choice,npc.research_need=nil,nil,nil
        return {ok=true,accepted=true,request_id=r.request_id,idle=true,tick=game.tick}
      end
      for _,option in ipairs(r.options) do
        if option.name==payload.technology then
          npc.research_choice=option.name
          -- (how much it is needed, for the team's queue: catalog_planner.research_need, team_research.lua; R-9)
          local need=type(payload.need)=="table" and payload.need or nil
          npc.research_need=need and {class=({a="a",b="b",c="c",d="d"})[need.class] or "d",
            step=tonumber(need.step)} or nil
          return {ok=true,accepted=true,request_id=r.request_id,technology=option.name,tick=game.tick}
        end
      end
      return reject("technology_not_offered",payload.request_id)
    end
  end
  return reject("unknown_request",payload.request_id)
end

function DesignedStart.observe()
  local requests,status={},{}
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    if npc and npc.design then
      local d=npc.design
      local stage_ticks,plates={},0
      for i,s in ipairs(d.plan and d.plan.stages or {}) do stage_ticks[i]=s.completed_tick or -1 end
      for _,eid in ipairs(d.plan and d.plan.order or {}) do
        local e=d.plan.entities[eid].entity
        if e and e.valid and e.type=="furnace" then plates=plates+e.products_finished end
      end
      status[#status+1]={npc_id=id,request_id=d.request_id,status=d.status,reason=d.reason,
        stage_index=d.stage_index,stages=d.plan and #d.plan.stages or 0,phase=npc.phase,
        rejections=d.rejections,last_rejection=d.last_rejection,plan_id=d.plan and d.plan.plan_id,
        requested_tick=d.requested_tick,accepted_tick=d.plan and d.plan.accepted_tick,
        completed_tick=d.completed_tick,stage_completed_ticks=stage_ticks,
        failed_jobs=d.failed_jobs or 0,products_finished=plates}
      if d.status=="waiting" and npc.character and npc.character.valid then
        -- Computed once per request: RCON commands run in the simulation on every peer.
        d.export_cache=d.export_cache or DesignedStart.export(npc)
        requests[#requests+1]=d.export_cache
      end
    end
  end
  local research={}
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    local r=npc and npc.research_request
    if r and npc.character and npc.character.valid then
      local p=Personality.ensure(npc)
      local completed=completed_purposes(npc)
      -- (what the science tiers wait on as the request is asked: research moves on between its plan requests)
      r.tiers=r.tiers or DesignedStart.tiers(npc)
      research[#research+1]={request_id=r.request_id,npc_id=id,npc_name=npc.name,options=r.options,tiers=r.tiers,
        personality={traits=p.traits},completed_purposes=completed,tick=r.tick,war=war_research(npc),
        walled=walled(npc),team=TeamWorld.active() or nil,death_world=TeamWorld.death_world() or nil,
        social=Character.social_summary(npc,game.tick,2),researched=DesignedStart.research_tally(npc),
        threat_board=TeamWorld.active() and TeamWar.threat_board(npc,nil,npc.character.surface) or nil}
    end
  end
  return {ok=true,schema="npc-plan-observe-v1",tick=game.tick,requests=requests,status=status,
    research_requests=research,taught=Teach.waiting(4)}
end

-- The largest plan the game takes in one command (its RCON took 1 MB in a headless check, 2026-10-25; a bus's
-- copper smelting stage was over 64 KB, more than the companion's old cap).
DesignedStart.MAX_PLAN_BYTES=4*1024*1024

function DesignedStart.register_commands()
  commands.add_command("npc-plan","Private host interface for designed layouts",function(command)
    if command.player_index then return end -- server console / RCON only
    local p=command.parameter or "observe"
    local result
    if p=="observe" then result=DesignedStart.observe()
    elseif p:sub(1,7)=="submit " and #p<=DesignedStart.MAX_PLAN_BYTES then
      local ok,payload=pcall(helpers.json_to_table,p:sub(8))
      result=ok and DesignedStart.submit(payload) or reject("invalid_json")
    elseif p=="mode designed" or p=="mode scripted" then
      settings.global["npc-factions-start-mode"]={value=p:sub(6)}
      result={ok=true,mode=p:sub(6),tick=game.tick}
    elseif p:sub(1,9)=="research " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(10))
      result=ok and DesignedStart.choose_research(payload) or reject("invalid_json")
    elseif p:sub(1,8)=="learned " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(9))
      local learned=ok and type(payload)=="table" and Teach.learned(tonumber(payload.id),
        type(payload.purpose)=="string" and payload.purpose:sub(1,64) or nil)
      result=learned and {ok=true,id=payload.id,tick=game.tick} or reject("unknown_blueprint")
    elseif p:sub(1,4)=="map " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(5))
      local npc=ok and type(payload)=="table" and storage.npcs and storage.npcs[tonumber(payload.npc_id) or 1]
      local timer=storage.test and storage.test.profile and helpers.create_profiler()
      local center=type(payload.x)=="number" and type(payload.y)=="number" and {x=payload.x,y=payload.y} or nil
      result=npc and BaseMap.dump(npc,payload.radius,center) or reject("unknown_npc")
      if timer then
        timer.stop()
        log({"","[npc-factions] profile map dump radius ",tostring(payload.radius),": ",timer})
      end
    elseif p:sub(1,5)=="site " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(6))
      result=ok and DesignedStart.move_site(payload) or reject("invalid_json")
    elseif p:sub(1,6)=="scout " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(7))
      result=ok and DesignedStart.scout(payload) or reject("invalid_json")
    elseif p:sub(1,6)=="defer " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(7))
      result=ok and DesignedStart.defer(payload) or reject("invalid_json")
    elseif p:sub(1,5)=="fail " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(6))
      result=ok and DesignedStart.fail(payload) or reject("invalid_json")
    elseif p:sub(1,8)=="factory " and #p<=65536 then
      -- {"npc_id":N,"plan":{...}}: the engineer's factory plan (companion/factory_plan.py), kept in the save
      local ok,payload=pcall(helpers.json_to_table,p:sub(9))
      local npc=ok and type(payload)=="table" and storage.npcs and storage.npcs[tonumber(payload.npc_id) or 0]
      if npc then
        local plan,why=FactoryPlan.store(npc,payload.plan,game.tick)
        result=plan and {ok=true,npc_id=payload.npc_id,version=plan.version,tick=game.tick} or reject(why)
      else result=reject("unknown_npc") end
    elseif p:sub(1,6)=="facts " then
      -- {"npc_id":N}: what that engineer's force can build and what it does (companion/rates.py)
      local ok,payload=pcall(helpers.json_to_table,p:sub(7))
      local npc=ok and type(payload)=="table" and storage.npcs and storage.npcs[tonumber(payload.npc_id) or 0]
      result=npc and GameFacts.export(npc.force) or reject("unknown_npc")
    elseif p=="armory" or p:sub(1,7)=="armory " then
      -- {"npc_id":N} or {"force":name}: with what that force can make and has researched
      local ok,payload=pcall(helpers.json_to_table,p:sub(8))
      payload=ok and type(payload)=="table" and payload or {}
      local npc=storage.npcs and storage.npcs[tonumber(payload.npc_id) or 0]
      local force=npc and npc.force or type(payload.force)=="string" and game.forces[payload.force] or nil
      result=Armory.export(force)
      result.ok,result.tick=true,game.tick
    elseif p:sub(1,7)=="recipe " then
      -- {"item":name,"force":name}: the recipe an engineer of that force would use for the item
      local ok,payload=pcall(helpers.json_to_table,p:sub(8))
      local force=ok and type(payload)=="table" and type(payload.force)=="string" and game.forces[payload.force]
      if force and type(payload.item)=="string" then
        local hand=prototypes.entity["character"] and prototypes.entity["character"].crafting_categories or {}
        local recipe=Recipes.for_item(force,payload.item,hand,true)
        result={ok=true,item=payload.item,recipe=recipe and recipe.name,enabled=recipe and recipe.enabled,
          candidates=Recipes.candidates(force,payload.item)}
      else result=reject("unknown_force") end
    else result=reject("command") end
    rcon.print(MARKER..helpers.table_to_json(result))
  end)
end

-- The strategist's executor uses the engineer's own ways of getting things and starting plans.
DesignedStart.acquire_raw=acquire
Agent.designed={relocate=function(...) return DesignedStart.relocate(...) end,
  acquire=DesignedStart.acquire_now,begin=DesignedStart.begin,active=DesignedStart.active,count=count,  -- begin(npc,tick,goal,purpose,at,order)
  used_by_a_plan=function(npc,e) return used_by_a_plan(npc,e) end,
  machine_made=DesignedStart.machine_made,collect=DesignedStart.collect,away=DesignedStart.away,
  completed=completed_purposes,failed=function(npc) return failed_purposes(npc,game.tick) end,
  defending=function(npc) local d=npc.design return d~=nil and d.status=="building" and DEFENSES[d.purpose]==true end,
  on_own_ground=function(npc,pos) return DesignedStart.on_own_ground(npc,pos) end}

return DesignedStart
