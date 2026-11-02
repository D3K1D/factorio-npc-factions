-- Native-map camp survey. Generated cells are observations; at an assigned start, what a start
-- needs and does not have close by is laid down (find_at_start), as the map does for the player.
local Settlements={}
local WATER="water_tile"   -- (water by the game's collision mask, a mod's too, F-21)
local MAX_RANGES={["iron-ore"]=20,coal=256,["copper-ore"]=512,stone=512,wood=128,water=128}
local MAX_CHUNKS_PER_FIND=2400
local MAX_CHUNKS_PER_SURFACE=16000

function Settlements.generate_route(surface,start,goal)
  local dx,dy=goal.x-start.x,goal.y-start.y
  local segments=math.max(1,math.ceil(math.sqrt(dx*dx+dy*dy)/96))
  for i=0,segments do
    surface.request_to_generate_chunks({x=start.x+dx*i/segments,y=start.y+dy*i/segments},3)
  end
  surface.force_generate_chunk_requests()
end

local function distance_squared(a,b)
  local x,y=a.x-b.x,a.y-b.y
  return x*x+y*y
end
local function distance(a,b) return math.sqrt(distance_squared(a,b)) end
local function copy_position(p) return {x=p.x,y=p.y} end

-- Request every chunk intersecting a finite query disc before searching it.
-- The cache is saved and synchronized, so later searches see the same budget
-- after a reload or when another multiplayer peer joins.
local function generate_disc(surface,p,radius,context)
  storage.settlement_survey_cache=storage.settlement_survey_cache or {}
  local caches=storage.settlement_survey_cache
  local cache=caches[surface.index]
  if not cache then cache={cells={},requested=0};caches[surface.index]=cache end
  local lowx,highx=math.floor((p.x-radius)/32),math.floor((p.x+radius)/32)
  local lowy,highy=math.floor((p.y-radius)/32),math.floor((p.y+radius)/32)
  local missing={}
  for cx=lowx,highx do for cy=lowy,highy do
    local left,right,top,bottom=cx*32,(cx+1)*32,cy*32,(cy+1)*32
    local nx=math.max(left,math.min(p.x,right))
    local ny=math.max(top,math.min(p.y,bottom))
    if (nx-p.x)^2+(ny-p.y)^2<=radius*radius then
      local key=cx..":"..cy
      if not cache.cells[key] then
        if surface.is_chunk_generated({x=cx,y=cy}) then cache.cells[key]=true
        else missing[#missing+1]={key=key,x=left+16,y=top+16} end
      end
    end
  end end
  if context.requested+#missing>MAX_CHUNKS_PER_FIND or
      cache.requested+#missing>MAX_CHUNKS_PER_SURFACE then return false end
  for _,cell in ipairs(missing) do
    surface.request_to_generate_chunks({x=cell.x,y=cell.y},0)
    cache.cells[cell.key]=true
  end
  if #missing>0 then surface.force_generate_chunk_requests() end
  context.requested=context.requested+#missing
  cache.requested=cache.requested+#missing
  if context.trace then context.trace.generated_chunks=context.requested end
  return true
end

local function preferred(p,a,b)
  if not b then return true end
  local da,db=distance_squared(p,a.position),distance_squared(p,b.position)
  if da~=db then return da<db end
  if a.position.x~=b.position.x then return a.position.x<b.position.x end
  if a.position.y~=b.position.y then return a.position.y<b.position.y end
  return (a.unit_number or 0)<(b.unit_number or 0)
end
local function nearest(surface,query,p,radius)
  local best
  query.position,query.radius=p,radius
  for _,entity in ipairs(surface.find_entities_filtered(query)) do
    if entity.valid and entity.minable and (entity.type~="resource" or entity.amount>0)
        and preferred(p,entity,best) then best=entity end
  end
  return best
end
local function marker(entity,home,item)
  if not entity then return {available=false,reason="not_found_within_survey_range"} end
  return {available=true,type=entity.type,name=entity.name,item=item,
    position=copy_position(entity.position),distance=distance(home,entity.position),
    amount=entity.type=="resource" and entity.amount or nil,
    minable=entity.minable==true,unit_number=entity.unit_number}
end
local function nearest_water(surface,home)
  local best
  for _,tile in ipairs(surface.find_tiles_filtered{position=home,radius=128,collision_mask=WATER}) do
    if distance_squared(home,tile.position)<=128*128 and preferred(home,tile,best) then best=tile end
  end
  return best
end
local function water_marker(tile,home)
  if not tile then return {available=false,reason="not_found_within_survey_range"} end
  return {available=true,type="tile",name=tile.name,position=copy_position(tile.position),
    distance=distance(home,tile.position),native_water_observation=true,
    pump_footprint_verified=false}
end
local function rejected(trace,reason)
  if trace then trace[reason]=(trace[reason] or 0)+1 end
end

function Settlements.find(player,id,count,spacing,trace)
  local surface=player.surface
  local context={requested=0,trace=trace}
  if trace then
    trace.generated_chunks=0
    trace.per_actor_chunk_budget=MAX_CHUNKS_PER_FIND
    trace.per_surface_chunk_budget=MAX_CHUNKS_PER_SURFACE
  end
  local radius=math.max(spacing,spacing/(2*math.sin(math.pi/math.max(3,count))))+160
  local sector=2*math.pi/count
  -- Adjacent bearings offer a finite fallback when one actor's sector has no
  -- safe iron/water/coal combination. Spacing is still checked against homes.
  local offsets={0}
  for step=1,21 do offsets[#offsets+1]=-step;offsets[#offsets+1]=step end
  local surveyed_iron_cells={}
  for ring=0,11 do
    for _,offset in ipairs(offsets) do
      local angle=(id-1)*sector+offset*sector/14
      local r=radius+ring*192
      local candidate={x=player.position.x+math.cos(angle)*r,y=player.position.y+math.sin(angle)*r}
      if not generate_disc(surface,candidate,128,context) then
        rejected(trace,"generation_budget_exhausted")
        return nil,"survey_generation_budget_exhausted",nil
      end
      local irons=surface.find_entities_filtered{name="iron-ore",position=candidate,radius=128}
      if #irons==0 then rejected(trace,"no_iron") end
      local cells={}
      for _,iron in ipairs(irons) do
        if iron.valid and iron.minable and iron.amount>0 then
          local key=math.floor(iron.position.x/24)..":"..math.floor(iron.position.y/24)
          if not surveyed_iron_cells[key] and preferred(candidate,iron,cells[key]) then cells[key]=iron end
        end
      end
      local representatives={}
      for _,iron in pairs(cells) do representatives[#representatives+1]=iron end
      table.sort(representatives,function(a,b) return preferred(candidate,a,b) end)
      for _,iron in ipairs(representatives) do
        local key=math.floor(iron.position.x/24)..":"..math.floor(iron.position.y/24)
        surveyed_iron_cells[key]=true
        if trace then trace.surveyed_iron_cells=(trace.surveyed_iron_cells or 0)+1 end
        if not generate_disc(surface,iron.position,20,context) then
          rejected(trace,"generation_budget_exhausted")
          return nil,"survey_generation_budget_exhausted",nil
        end
        local home=surface.find_non_colliding_position("character",iron.position,20,.5)
        local separated=home~=nil and distance(home,player.position)>=spacing
        for _,other in pairs(storage.npcs or {}) do
          if home and other.home and distance(home,other.home)<spacing then separated=false end
        end
        if not home then rejected(trace,"no_character_position")
        elseif not separated then rejected(trace,"unsafe_spacing")
        else
          if not generate_disc(surface,home,128,context) then
            rejected(trace,"generation_budget_exhausted")
            return nil,"survey_generation_budget_exhausted",nil
          end
          local wood=nearest(surface,{type="tree"},home,128)
          local water=nearest_water(surface,home)
          if not wood then rejected(trace,"no_tree")
          elseif not water then rejected(trace,"no_water")
          elseif surface.count_entities_filtered{position=home,radius=80,
              type={"unit-spawner","turret"},force="enemy"}>0 then
            rejected(trace,"near_enemy_base")
          else
            local coal=nearest(surface,{name="coal"},home,128)
            if not coal then
              if not generate_disc(surface,home,256,context) then
                rejected(trace,"generation_budget_exhausted")
                return nil,"survey_generation_budget_exhausted",nil
              end
              coal=nearest(surface,{name="coal"},home,256)
            end
            if not coal then rejected(trace,"no_coal")
            else
              local copper=nearest(surface,{name="copper-ore"},home,128)
              local stone=nearest(surface,{name="stone"},home,128)
              local full_survey=true
              if not copper or not stone then
                full_survey=generate_disc(surface,home,512,context)
                if full_survey then
                  copper=nearest(surface,{name="copper-ore"},home,512)
                  stone=nearest(surface,{name="stone"},home,512)
                end
              end
              local survey={scope="engine_initial_camp_survey",surface_index=surface.index,
                observed_tick=game.tick,home=copy_position(home),
                max_ranges={["iron-ore"]=MAX_RANGES["iron-ore"],coal=MAX_RANGES.coal,
                  ["copper-ore"]=MAX_RANGES["copper-ore"],stone=MAX_RANGES.stone,
                  wood=MAX_RANGES.wood,water=MAX_RANGES.water},
                landmarks={["iron-ore"]=marker(iron,home,"iron-ore"),
                  coal=marker(coal,home,"coal"),
                  ["copper-ore"]=copper and marker(copper,home,"copper-ore") or
                    {available=false,reason=full_survey and "not_found_within_survey_range" or "unsurveyed_generation_budget"},
                  stone=stone and marker(stone,home,"stone") or
                    {available=false,reason=full_survey and "not_found_within_survey_range" or "unsurveyed_generation_budget"},
                  wood=marker(wood,home,"wood"),water=water_marker(water,home)}}
              if trace then trace.selected=(trace.selected or 0)+1 end
              return home,nil,survey
            end
          end
        end
      end
    end
  end
  return nil,"no_native_iron_coal_water_tree_camp_with_safe_spacing",nil
end

-- A start for each engineer, as the map gives the player one: the spot near its assigned starting
-- point where iron, coal, copper, stone, water and trees lie closest together (as a player picks a
-- spot), completed with whatever is still missing close by. Nests near it are cleared, and a small
-- patch of each missing ore, a pond or a few trees are laid down around home: an engineer always
-- starts with some iron, coal, copper, stone and water within reach.
local COMPACT={coal=1.0,["copper-ore"]=0.8,stone=0.5,water=0.6,wood=0.3}
local MISSING=300            -- what a resource not found nearby costs a spot
local CANDIDATES=16          -- iron cells tried near the assigned start
local ZONE_NEAR=64           -- a start needs each resource this close to home
local ZONE_CLEAR=100         -- no enemy buildings this close to home
local PATCH_RADIUS,PATCH_AMOUNT=5,2500
local PATCH_SPOTS={{14,0},{-14,0},{0,-14},{0,14},{12,12},{-12,-12},{12,-12},{-12,12},{20,0},{-20,0},{0,20},{0,-20}}

local function is_water(surface,x,y)
  local tile=surface.get_tile(x,y)
  local ok,wet=pcall(function() return tile.collides_with(WATER) end)
  if ok then return wet or tile.name=="out-of-map" end
  for _,w in ipairs({"water","deepwater","water-green","deepwater-green"}) do if tile.name==w then return true end end
  return tile.name=="out-of-map"
end

-- Where to lay a small ore patch (or pond, or trees): the first spot round home whose disc is dry
-- land, clear of ore and of the other things laid.
local function free_spot(surface,home,taken,radius)
  for _,o in ipairs(PATCH_SPOTS) do
    local cx,cy=math.floor(home.x+o[1]),math.floor(home.y+o[2])
    local ok=true
    for _,t in ipairs(taken) do if (t.x-cx)^2+(t.y-cy)^2<(t.r+radius+2)^2 then ok=false end end
    if ok then
      for x=cx-radius,cx+radius do for y=cy-radius,cy+radius do
        if ok and (x-cx)^2+(y-cy)^2<=radius*radius and is_water(surface,x,y) then ok=false end
      end end
    end
    if ok and surface.count_entities_filtered{area={{cx-radius,cy-radius},{cx+radius+1,cy+radius+1}},
        type="resource"}>0 then ok=false end
    if ok then taken[#taken+1]={x=cx,y=cy,r=radius}; return cx,cy end
  end
end

local function clear(surface,x0,y0,x1,y1)
  for _,e in ipairs(surface.find_entities_filtered{area={{x0,y0},{x1,y1}},type={"tree","simple-entity","cliff"}}) do
    if e.valid then e.destroy() end
  end
end

local function complete_start(surface,home,trace)
  local made={}
  for _,e in ipairs(surface.find_entities_filtered{position=home,radius=ZONE_CLEAR,force="enemy"}) do
    if e.valid and e.type~="character" then e.destroy() end
  end
  local taken={{x=home.x,y=home.y,r=4}}
  for _,name in ipairs({"iron-ore","copper-ore","coal","stone"}) do
    if not nearest(surface,{name=name},home,ZONE_NEAR) then
      local cx,cy=free_spot(surface,home,taken,PATCH_RADIUS)
      if cx then
        clear(surface,cx-PATCH_RADIUS,cy-PATCH_RADIUS,cx+PATCH_RADIUS+1,cy+PATCH_RADIUS+1)
        for x=cx-PATCH_RADIUS,cx+PATCH_RADIUS do for y=cy-PATCH_RADIUS,cy+PATCH_RADIUS do
          if (x-cx)^2+(y-cy)^2<=PATCH_RADIUS*PATCH_RADIUS then
            surface.create_entity{name=name,position={x+0.5,y+0.5},amount=PATCH_AMOUNT}
          end
        end end
        made[#made+1]=name
      end
    end
  end
  local water=nearest_water(surface,home)
  if not water or distance(home,water.position)>ZONE_NEAR then
    local cx,cy=free_spot(surface,home,taken,3)
    if cx then
      clear(surface,cx-3,cy-3,cx+4,cy+4)
      local tiles={}
      for x=cx-2,cx+2 do for y=cy-2,cy+2 do tiles[#tiles+1]={name="water",position={x,y}} end end
      surface.set_tiles(tiles)
      made[#made+1]="water"
    end
  end
  if not nearest(surface,{type="tree"},home,ZONE_NEAR) then
    local cx,cy=free_spot(surface,home,taken,3)
    if cx then
      for k=0,7 do
        local p=surface.find_non_colliding_position("tree-01",{cx+(k%3)*2-2,cy+math.floor(k/3)*2-2},3,1)
        if p then surface.create_entity{name="tree-01",position=p} end
      end
      made[#made+1]="wood"
    end
  end
  if trace and #made>0 then trace.prepared=table.concat(made,",") end
  return made
end

local function survey_of(surface,id,point,home,made)
  local function find(name) return nearest(surface,{name=name},home,128) end
  local iron,coal,copper,stone=find("iron-ore"),find("coal"),find("copper-ore"),find("stone")
  local wood=nearest(surface,{type="tree"},home,128)
  return {scope="engine_initial_camp_survey",surface_index=surface.index,
    observed_tick=game.tick,home=copy_position(home),
    start_position=copy_position(point),assigned_start_index=id+1,prepared=made,
    max_ranges={["iron-ore"]=MAX_RANGES["iron-ore"],coal=MAX_RANGES.coal,
      ["copper-ore"]=MAX_RANGES["copper-ore"],stone=MAX_RANGES.stone,
      wood=MAX_RANGES.wood,water=MAX_RANGES.water},
    landmarks={["iron-ore"]=marker(iron,home,"iron-ore"),coal=marker(coal,home,"coal"),
      ["copper-ore"]=marker(copper,home,"copper-ore"),stone=marker(stone,home,"stone"),
      wood=marker(wood,home,"wood"),water=water_marker(nearest_water(surface,home),home)}}
end
-- What lies round a home chosen elsewhere (a team world's plot).
function Settlements.survey_at(surface,id,home) return survey_of(surface,id,home,home,{}) end

-- Optional fresh-world mode: index 1 belongs to the human; NPC id owns id+1.
-- An assigned start is never replaced with another NPC's start or a far ring.
function Settlements.find_at_start(player,id,count,spacing,trace)
  local surface=player.surface
  if type(id)~="number" or id%1~=0 or id<1 or id>count then
    return nil,"invalid_assigned_start_id",nil
  end
  local points=surface.map_gen_settings.starting_points or {}
  local point=points[id+1]
  if not point or type(point.x)~="number" or type(point.y)~="number" then
    return nil,"assigned_native_start_unavailable",nil
  end
  point=copy_position(point)
  local context={requested=0,trace=trace}
  if trace then
    trace.generated_chunks=0
    trace.per_actor_chunk_budget=MAX_CHUNKS_PER_FIND
    trace.per_surface_chunk_budget=MAX_CHUNKS_PER_SURFACE
    trace.assigned_start_position=copy_position(point)
  end
  if not generate_disc(surface,point,180,context) then
    rejected(trace,"generation_budget_exhausted")
    return nil,"assigned_start_survey_generation_budget_exhausted",nil
  end
  local function spaced(home)
    for _,other in pairs(storage.npcs or {}) do
      if other.home and distance(home,other.home)<spacing then return false end
    end
    for _,other in ipairs(trace and trace.reserved_homes or {}) do
      if distance(home,other)<spacing then return false end
    end
    return true
  end
  local cells={}
  for _,iron in ipairs(surface.find_entities_filtered{name="iron-ore",position=point,radius=160}) do
    if iron.valid and iron.minable and iron.amount>0 then
      local key=math.floor(iron.position.x/24)..":"..math.floor(iron.position.y/24)
      if preferred(point,iron,cells[key]) then cells[key]=iron end
    end
  end
  local representatives={}
  for _,iron in pairs(cells) do representatives[#representatives+1]=iron end
  table.sort(representatives,function(a,b) return preferred(point,a,b) end)
  -- The most compact spot: coal, copper, stone, water and trees nearest the iron, nests not too close.
  local best,best_score
  for index,iron in ipairs(representatives) do
    if index>CANDIDATES then break end
    if trace then trace.surveyed_iron_cells=(trace.surveyed_iron_cells or 0)+1 end
    local home=surface.find_non_colliding_position("character",iron.position,20,.5)
    if not home then rejected(trace,"no_character_position")
    elseif distance(home,point)>180 then rejected(trace,"outside_assigned_start")
    elseif not spaced(home) then rejected(trace,"unsafe_spacing")
    elseif not generate_disc(surface,home,128,context) then
      rejected(trace,"generation_budget_exhausted")
      break
    elseif surface.count_entities_filtered{position=home,radius=60,type={"unit-spawner","turret"},
        force="enemy"}>0 then
      rejected(trace,"near_enemy_base")
    else
      local function cost(entity) return entity and math.min(distance(home,entity.position),MISSING) or MISSING end
      local score=COMPACT.coal*cost(nearest(surface,{name="coal"},home,128))
        +COMPACT["copper-ore"]*cost(nearest(surface,{name="copper-ore"},home,128))
        +COMPACT.stone*cost(nearest(surface,{name="stone"},home,128))
        +COMPACT.water*cost(nearest_water(surface,home))
        +COMPACT.wood*cost(nearest(surface,{type="tree"},home,128))
      if not best or score<best_score then best,best_score=home,score end
    end
  end
  if not best then   -- no iron near the start: home at the start itself, its resources laid down
    best=surface.find_non_colliding_position("character",point,40,1)
    if not best or not spaced(best) then
      return nil,"assigned_start_no_room_for_a_start",nil
    end
    if not generate_disc(surface,best,128,context) then
      return nil,"assigned_start_survey_generation_budget_exhausted",nil
    end
  end
  local made=complete_start(surface,best,trace)
  if trace then trace.selected=(trace.selected or 0)+1; trace.score=best_score end
  return best,nil,survey_of(surface,id,point,best,made)
end
return Settlements
