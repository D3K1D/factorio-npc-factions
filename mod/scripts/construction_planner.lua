-- Original base-game starter layout. Planning never creates entities/items or
-- changes recipes/research: every operation is interpreted as a physical job.
local Recipes = require("scripts.recipes")
local Planner = {revision="starter-cardinal-v6"}
local BASE = {['iron-plate']=true,['copper-plate']=true,stone=true,wood=true,coal=true}
local function xy(p) return {x=p.x or p[1],y=p.y or p[2]} end
local function sorted(t) local out={} for k in pairs(t) do out[#out+1]=k end table.sort(out) return out end
local function stock(npc,item) return npc.character.get_main_inventory().get_item_count{name=item,quality="normal"} end
local function fail(reason) error(reason,0) end
local function direction(dx,dy)
  if dx>0 then return defines.direction.east elseif dx<0 then return defines.direction.west
  elseif dy>0 then return defines.direction.south else return defines.direction.north end
end
local function yield_of(recipe,item)
  for _,p in ipairs(recipe.products or {}) do
    if p.name==item and p.type=="item" and p.amount and (not p.probability or p.probability==1) then return p.amount end
  end
end

-- A budget can inspect locked FUTURE recipes only when explicitly requested.
-- It reports those locks; this never makes them executable. Normal craft_plan
-- checks enabled recipes/categories and exposes raw deficits rather than grants.
function Planner.material_budget(npc,demands,allow_locked)
  local ledger,roots,crafts,locks,visiting={},{},{},{},{}
  local function need(item,amount)
    if ledger[item]==nil then ledger[item]=stock(npc,item) end
    local used=math.min(ledger[item],amount)
    ledger[item]=ledger[item]-used; amount=amount-used
    if amount<=0 then return end
    if BASE[item] then roots[item]=(roots[item] or 0)+amount; return end
    local recipe=Recipes.for_item(npc.character.force,item,Recipes.hand(npc.character),true)
    if not recipe then fail("missing_recipe:"..item) end
    if not recipe.enabled then
      if not allow_locked then fail("recipe_locked:"..item) end
      locks[item]=true
    end
    if not Recipes.made_in(recipe,npc.character.prototype.crafting_categories) then
      fail("recipe_not_handcraftable:"..item)
    end
    local produced=yield_of(recipe,item)
    if not produced then fail("non_deterministic_product:"..item) end
    if visiting[item] then fail("recipe_cycle:"..item) end
    visiting[item]=true
    local batches=math.ceil(amount/produced)
    local ingredients={}
    for _,ingredient in ipairs(recipe.ingredients or {}) do
      if ingredient.type~="item" then fail("fluid_craft_unsupported:"..item) end
      ingredients[#ingredients+1]=ingredient
    end
    table.sort(ingredients,function(a,b) return a.name<b.name end)
    for _,ingredient in ipairs(ingredients) do need(ingredient.name,ingredient.amount*batches) end
    visiting[item]=nil
    ledger[item]=ledger[item]+produced*batches-amount
    crafts[#crafts+1]={kind="craft",recipe=recipe.name,count=batches}
  end
  local ok,reason=pcall(function()
    for _,item in ipairs(sorted(demands)) do
      local n=demands[item]
      if type(n)~="number" or n<0 or n%1~=0 then fail("invalid_material_demand:"..item) end
      need(item,n)
    end
  end)
  if not ok then return nil,reason end
  return {raw_deficits=roots,crafts=crafts,locked_recipes=sorted(locks),allows_future_locked_recipes=allow_locked==true}
end
function Planner.craft_plan(npc,item,count) return Planner.material_budget(npc,{[item]=count},false) end

local function box(site)
  local b=prototypes.entity[site.name].collision_box
  local a,c=xy(b.left_top or b[1]),xy(b.right_bottom or b[2])
  local turns=({[defines.direction.north]=0,[defines.direction.east]=1,
    [defines.direction.south]=2,[defines.direction.west]=3})[site.direction] or 0
  local x1,y1,x2,y2=math.huge,math.huge,-math.huge,-math.huge
  for _,p in ipairs({{a.x,a.y},{a.x,c.y},{c.x,a.y},{c.x,c.y}}) do
    local x,y=p[1],p[2]
    for _=1,turns do x,y=-y,x end
    x1,y1,x2,y2=math.min(x1,x),math.min(y1,y),math.max(x2,x),math.max(y2,y)
  end
  return {x1=site.position.x+x1,y1=site.position.y+y1,
    x2=site.position.x+x2,y2=site.position.y+y2}
end
local function overlap(a,b) return a.x1<b.x2 and b.x1<a.x2 and a.y1<b.y2 and b.y1<a.y2 end
local function existing(npc,site)
  for _,e in ipairs(npc.character.surface.find_entities_filtered{name=site.name,position=site.position,radius=0.05,force=npc.character.force}) do
    if e.valid and (site.name=="stone-furnace" or e.direction==site.direction) then return e end
  end
end
local function can_place(npc,site)
  return npc.character.surface.can_place_entity{name=site.name,position=site.position,
    direction=site.direction,force=npc.character.force,build_check_type=defines.build_check_type.manual}
end
local function clearable(npc,site)
  local surface=npc.character.surface
  local b=box(site)
  -- A forced ghost tests the underlying terrain after deconstructible entities
  -- are ignored. This is only a preflight: real Worker mining and a fresh
  -- manual placement check still have to succeed before any item is spent.
  if not surface.can_place_entity{name=site.name,position=site.position,
      direction=site.direction,force=npc.character.force,
      build_check_type=defines.build_check_type.blueprint_ghost,forced=true} then return nil end
  -- (water by the game's collision mask, a mod's too, F-21)
  if surface.count_tiles_filtered{area={{b.x1,b.y1},{b.x2,b.y2}},collision_mask="water_tile"}>0
      or surface.count_tiles_filtered{area={{b.x1,b.y1},{b.x2,b.y2}},name={"lava"}}>0 then return nil end
  local blockers={}
  for _,entity in ipairs(surface.find_entities_filtered{area={{b.x1,b.y1},{b.x2,b.y2}}}) do
    local eb=entity.bounding_box
    if eb and b.x1<eb.right_bottom.x and eb.left_top.x<b.x2
        and b.y1<eb.right_bottom.y and eb.left_top.y<b.y2
        and entity.type~="resource" then
      if entity.force.name~="neutral" or not entity.minable
          or (entity.type~="tree" and entity.type~="simple-entity") then return nil end
      local props=entity.prototype.mineable_properties
      if not props or not props.minable or props.required_fluid then return nil end
      local item
      for _,product in ipairs(props.products or {}) do
        if product.type=="item" and (product.amount or product.amount_min or 0)>=1
            and (not product.probability or product.probability==1) then item=product.name;break end
      end
      if not item then return nil end
      blockers[#blockers+1]={name=entity.name,position=xy(entity.position),
        unit_number=entity.unit_number,item=item,surface_index=surface.index}
    end
  end
  if #blockers==0 then return nil end
  table.sort(blockers,function(a,c)
    return a.position.x<c.position.x or (a.position.x==c.position.x and
      (a.position.y<c.position.y or (a.position.y==c.position.y and a.name<c.name)))
  end)
  return blockers
end
local function make_site(npc,name,p,d)
  return {name=name,item=name,position=xy(p),direction=d or defines.direction.north,surface_index=npc.character.surface.index}
end
local function compatible(npc,site,sites)
  local found=existing(npc,site)
  local blockers
  if not found and not can_place(npc,site) then
    blockers=clearable(npc,site)
    if not blockers then return false end
  end
  local b=box(site)
  for _,other in pairs(sites) do if overlap(b,box(other)) then return false end end
  site.clear_blocks=blockers
  return true
end
local function add(plan,npc,key,name,p,d)
  local site=make_site(npc,name,p,d)
  if not compatible(npc,site,plan.sites) then fail("site_blocked:"..key) end
  local e=existing(npc,site)
  if e then site.unit_number=e.unit_number; site.existing=true end
  plan.sites[key]=site
  return key
end

-- Deterministic bounded A* avoids current collisions and reserved future sites.
-- A clearable footprint costs extra, so an open route wins when one exists.
local function path(npc,sites,name,start,finish,anchors,requested_margin)
  local sx,sy=math.floor(start.x),math.floor(start.y)
  local tx,ty=math.floor(finish.x),math.floor(finish.y)
  local function key(x,y) return x..":"..y end
  local margin=math.max(24,math.min(128,requested_margin or 24))
  local minx,maxx=math.min(sx,tx)-margin,math.max(sx,tx)+margin
  local miny,maxy=math.min(sy,ty)-margin,math.max(sy,ty)+margin
  local area=(maxx-minx+1)*(maxy-miny+1)
  local budget=math.min(50000,math.max(12000,area*2))
  local legal_cache={}
  local function legal(x,y)
    -- Pole links start and finish at already validated, reserved pole sites.
    -- No pole is placed at either anchor by pole_connection below.
    if anchors and ((x==sx and y==sy) or (x==tx and y==ty)) then return 0 end
    local k=key(x,y)
    if legal_cache[k]==false then return nil end
    if legal_cache[k]~=nil then return legal_cache[k] end
    local site=make_site(npc,name,{x=x+0.5,y=y+0.5})
    if compatible(npc,site,sites) then
      legal_cache[k]=site.clear_blocks and #site.clear_blocks or 0
      return legal_cache[k]
    end
    legal_cache[k]=false
  end
  if legal(sx,sy)==nil then fail("route_start_blocked:"..name) end
  if legal(tx,ty)==nil then fail("route_finish_blocked:"..name) end
  local function heuristic(x,y) return math.abs(tx-x)+math.abs(ty-y) end
  local function before(a,b)
    if a.f~=b.f then return a.f<b.f end
    if a.h~=b.h then return a.h<b.h end
    if a.y~=b.y then return a.y<b.y end
    return a.x<b.x
  end
  local heap={}
  local function push(value)
    local i=#heap+1
    while i>1 do
      local parent=math.floor(i/2)
      if not before(value,heap[parent]) then break end
      heap[i]=heap[parent];i=parent
    end
    heap[i]=value
  end
  local function pop()
    local root=heap[1]
    local last=heap[#heap];heap[#heap]=nil
    if #heap>0 then
      local i=1
      while i*2<=#heap do
        local child=i*2
        if child+1<=#heap and before(heap[child+1],heap[child]) then child=child+1 end
        if not before(heap[child],last) then break end
        heap[i]=heap[child];i=child
      end
      heap[i]=last
    end
    return root
  end
  local first=key(sx,sy)
  local scores,parents={[first]=0},{}
  push{x=sx,y=sy,g=0,h=heuristic(sx,sy),f=heuristic(sx,sy)}
  local expanded=0
  while #heap>0 and expanded<budget do
    local current=pop()
    local current_key=key(current.x,current.y)
    if current.g==scores[current_key] then
    expanded=expanded+1
    if current.x==tx and current.y==ty then
      local reversed={}; local k=current_key
      while k do
        local x,y=string.match(k,"^(-?%d+):(-?%d+)$")
        reversed[#reversed+1]={x=tonumber(x)+0.5,y=tonumber(y)+0.5}
        k=parents[k]
      end
      local out={} for i=#reversed,1,-1 do out[#out+1]=reversed[i] end return out
    end
    -- Electric wire requires legal endpoint poles, not pole-sized clearance
    -- at each intervening belt or terrain tile. Six-tile cardinal links stay
    -- below native small-pole reach (7.5); one-tile moves align the endpoints.
    local neighbors=anchors and {{6,0},{0,6},{-6,0},{0,-6},
      {1,0},{0,1},{-1,0},{0,-1}} or {{1,0},{0,1},{-1,0},{0,-1}}
    for _,offset in ipairs(neighbors) do
      local x,y=current.x+offset[1],current.y+offset[2]
      local k=key(x,y)
      if x>=minx and x<=maxx and y>=miny and y<=maxy then
        local blockers=legal(x,y)
        if blockers~=nil then
          local g=current.g+math.abs(offset[1])+math.abs(offset[2])+blockers*4
          if not scores[k] or g<scores[k] then
            scores[k]=g;parents[k]=current_key
            local h=heuristic(x,y)
            push{x=x,y=y,g=g,h=h,f=g+h}
          end
        end
      end
    end
    end
  end
  fail(expanded>=budget and "route_search_budget:"..name or "no_bounded_route:"..name)
end
local function belts(plan,npc,prefix,start,finish,last_direction)
  local points=path(npc,plan.sites,"transport-belt",start,finish)
  for i,p in ipairs(points) do
    local following=points[i+1]
    add(plan,npc,prefix..i,"transport-belt",p,following and direction(following.x-p.x,following.y-p.y)
      or last_direction or defines.direction.east)
  end
end
local function pole_connection(plan,npc,prefix,from,to,prepared_points)
  local points=prepared_points or path(npc,plan.sites,"small-electric-pole",from,to,true)
  -- Keep the farthest later legal point within native small-pole reach.
  -- The route already validated all endpoints. Sparse relays avoid dense
  -- clusters exhausting native automatic copper-wire connection slots.
  -- Both reserved anchor poles remain; no new position is synthesized.
  local sparse={points[1]}
  local current=1
  while current<#points do
    local following
    for i=#points,current+1,-1 do
      local dx,dy=points[i].x-points[current].x,points[i].y-points[current].y
      if dx*dx+dy*dy<=7.5*7.5 then following=i;break end
    end
    if not following then fail("wire_route_gap:"..prefix) end
    sparse[#sparse+1]=points[following];current=following
  end
  if prefix=="power_link" then plan.power_wire_route=sparse end
  for i=2,#sparse-1 do add(plan,npc,prefix..i,"small-electric-pole",sparse[i]) end
end

local function power_sites(npc,plan)
  local surface=npc.character.surface
  local waters=surface.find_tiles_filtered{position=npc.home,radius=128,collision_mask="water_tile"}
  -- The radius bounds this survey. Native query order is not nearest-first:
  -- truncating to 4096 tiles can omit a usable shore beside the actor's camp
  -- when a large distant lake occupies the first returned chunks.
  table.sort(waters,function(a,b)
    local da=(a.position.x-npc.home.x)^2+(a.position.y-npc.home.y)^2
    local db=(b.position.x-npc.home.x)^2+(b.position.y-npc.home.y)^2
    return da<db or (da==db and (a.position.x<b.position.x or (a.position.x==b.position.x and a.position.y<b.position.y)))
  end)
  -- Rotate the complete connected template about the observed water tile.
  -- Mirroring its dry-side machinery offers the other side of a narrow shore.
  -- Every variant still passes native placement, clearance and reservations.
  local orientations={defines.direction.north,defines.direction.east,
    defines.direction.south,defines.direction.west}
  local template={pump={"offshore-pump",0,1},boiler={"boiler",4,2.5},
    engine={"steam-engine",4,-2},water_pipe1={"pipe",0,2},
    water_pipe2={"pipe",0,3},water_pipe3={"pipe",1,3},
    water_pipe4={"pipe",2,3},steam_pipe={"pipe",4,1},
    engine_pole={"small-electric-pole",6,-1}}
  for _,tile in ipairs(waters) do
    for turns,orientation in ipairs(orientations) do
      for _,mirror in ipairs({1,-1}) do
        local candidates={}
        for key,spec in pairs(template) do
          local x,y=spec[2]*mirror,spec[3]
          for _=1,turns-1 do x,y=-y,x end
          local facing=(spec[1]=="offshore-pump" or spec[1]=="boiler" or spec[1]=="steam-engine")
            and orientation or defines.direction.north
          -- Native steam engines expose only the north/east axis directions.
          if spec[1]=="steam-engine" then facing=facing%8 end
          candidates[key]=make_site(npc,spec[1],
            {x=tile.position.x+.5+x,y=tile.position.y+.5+y},facing)
        end
        local reserved={}; for k,s in pairs(plan.sites) do reserved[k]=s end
        local valid=compatible(npc,candidates.pump,reserved)
        if valid then
          for _,key in ipairs(sorted(candidates)) do
            local site=candidates[key]
            if not compatible(npc,site,reserved) then valid=false; break end
            reserved[key]=site
          end
        end
        local wire_points
        if valid then
          local reachable,points=pcall(path,npc,reserved,"small-electric-pole",
            candidates.engine_pole.position,plan.sites.iron_pole.position,true,96)
          if reachable then wire_points=points
          elseif tostring(points):match("^no_bounded_route:")
              or tostring(points):match("^route_search_budget:")
              or tostring(points):match("^route_start_blocked:")
              or tostring(points):match("^route_finish_blocked:") then
            valid=false
          else fail(points) end
        end
        if valid then
          for key,site in pairs(candidates) do
            local e=existing(npc,site); if e then site.existing=true; site.unit_number=e.unit_number end
            plan.sites[key]=site
          end
          plan.power_orientation=orientation
          plan.power_mirrored=mirror==-1
          plan.power_anchor=xy(tile.position)
          plan.power_wire_route=wire_points
          plan.power_wire_margin=96
          return
        end
      end
    end
  end
  fail("no_clear_cardinal_shore_power_site_within_128")
end

local function copper_footprint_yield(npc,p)
  local drill=prototypes.entity['burner-mining-drill']
  local radius=drill and drill.get_mining_drill_radius()
  if not radius or radius<=0 then fail('native_copper_drill_radius_missing') end
  local total=0
  for _,ore in ipairs(npc.character.surface.find_entities_filtered{
      name='copper-ore',area={{p.x-radius,p.y-radius},{p.x+radius,p.y+radius}}}) do
    if ore.valid and ore.minable and ore.force.name=='neutral' and ore.amount>0 then
      total=total+ore.amount
    end
  end
  return total
end

local function copper_sites(npc,plan,min_yield)
  local ores=npc.character.surface.find_entities_filtered{name="copper-ore",position=npc.home,radius=128}
  table.sort(ores,function(a,b)
    local da=(a.position.x-npc.home.x)^2+(a.position.y-npc.home.y)^2
    local db=(b.position.x-npc.home.x)^2+(b.position.y-npc.home.y)^2
    return da<db or (da==db and (a.position.x<b.position.x or (a.position.x==b.position.x and a.position.y<b.position.y)))
  end)
  for _,ore in ipairs(ores) do
    local p={x=math.floor(ore.position.x),y=math.floor(ore.position.y)}
    local yield=copper_footprint_yield(npc,p)
    if yield>=(min_yield or 0) then
    local trial={}; for k,s in pairs(plan.sites) do trial[k]=s end
    local candidates={copper_drill=make_site(npc,"burner-mining-drill",p),
      copper_furnace=make_site(npc,"stone-furnace",{x=p.x,y=p.y-8}),
      copper_ore_inserter=make_site(npc,"burner-inserter",{x=p.x-0.5,y=p.y-6.5},defines.direction.south),
      copper_output_inserter=make_site(npc,"inserter",{x=p.x-0.5,y=p.y-9.5},defines.direction.south),
      copper_pole=make_site(npc,"small-electric-pole",{x=p.x+1.5,y=p.y-9.5})}
    for i=1,5 do candidates["copper_ore_belt"..i]=make_site(npc,"transport-belt",{x=p.x-0.5,y=p.y-i-0.5}) end
    local valid=true
    for _,k in ipairs(sorted(candidates)) do
      if not compatible(npc,candidates[k],trial) then valid=false; break end
      trial[k]=candidates[k]
    end
    if valid and not compatible(npc,make_site(npc,"transport-belt",
        {x=p.x-.5,y=p.y-10.5}),trial) then valid=false end
    if valid then
      for k,s in pairs(candidates) do
        local e=existing(npc,s)
        if e then s.existing=true; s.unit_number=e.unit_number end
        plan.sites[k]=s
      end
      plan.copper_drill_observed_ore=yield
      plan.copper_drill_ore_observed_tick=game.tick
      return p
    end
    end
  end
  fail((min_yield or 0)>0 and 'no_copper_drill_footprint_with_required_ore_within_128'
    or 'no_clear_copper_module_within_128')
end

local function make_plan(npc,min_copper_yield)
  if not npc.character or not npc.character.valid or not npc.furnace or not npc.furnace.valid then fail("requires_owned_iron_camp") end
  if npc.furnace.force~=npc.character.force then fail("iron_furnace_not_owned") end
  local f=xy(npc.furnace.position)
  local plan={id="starter_power_science",revision=Planner.revision,layout=Planner.revision,sites={},steps={}}
  plan.sites.iron_furnace={name=npc.furnace.name,item=npc.furnace.name,position=f,direction=npc.furnace.direction,
    surface_index=npc.character.surface.index,unit_number=npc.furnace.unit_number,existing=true}
  add(plan,npc,"gear_assembler","assembling-machine-1",{x=f.x+6.5,y=f.y-5.5})
  add(plan,npc,"science_assembler","assembling-machine-1",{x=f.x+10.5,y=f.y-5.5})
  add(plan,npc,"lab","lab",{x=f.x+13.5,y=f.y+0.5})
  -- A south-facing starter drill occupies the furnace's north edge. Prefer the
  -- original north output only if its inserter AND whole first belt row fit;
  -- otherwise collect through the east edge and route around the real drill.
  local north=make_site(npc,"inserter",{x=f.x-0.5,y=f.y-1.5},defines.direction.south)
  local north_clear=compatible(npc,north,plan.sites)
  if north_clear then
    for i=0,7 do
      local s=make_site(npc,"transport-belt",{x=f.x-0.5+i,y=f.y-2.5},defines.direction.east)
      if not compatible(npc,s,plan.sites) then north_clear=false; break end
    end
  end
  local iron_start
  if north_clear then
    add(plan,npc,"iron_output_inserter","inserter",north.position,north.direction)
    iron_start={x=f.x-0.5,y=f.y-2.5}
  else
    add(plan,npc,"iron_output_inserter","inserter",{x=f.x+1.5,y=f.y-0.5},defines.direction.west)
    iron_start={x=f.x+2.5,y=f.y-0.5}
  end
  plan.iron_output_side=north_clear and "north" or "east"
  add(plan,npc,"gear_input_inserter","inserter",{x=f.x+6.5,y=f.y-3.5},defines.direction.south)
  add(plan,npc,"gear_output_inserter","inserter",{x=f.x+8.5,y=f.y-5.5},defines.direction.west)
  add(plan,npc,"science_input_inserter","inserter",{x=f.x+10.5,y=f.y-7.5},defines.direction.north)
  add(plan,npc,"science_output_inserter","inserter",{x=f.x+12.5,y=f.y-5.5},defines.direction.west)
  add(plan,npc,"lab_input_inserter","inserter",{x=f.x+13.5,y=f.y-1.5},defines.direction.north)
  for i=0,3 do add(plan,npc,"science_belt"..i,"transport-belt",{x=f.x+13.5,y=f.y-5.5+i},defines.direction.south) end
  -- Both variants cover their output inserter and wire to the lower pole.
  add(plan,npc,"iron_pole","small-electric-pole",north_clear and {x=f.x+1.5,y=f.y-3.5}
    or {x=f.x+3.5,y=f.y-1.5})
  add(plan,npc,"lower_pole","small-electric-pole",{x=f.x+8.5,y=f.y-1.5})
  add(plan,npc,"upper_pole","small-electric-pole",{x=f.x+8.5,y=f.y-7.5})
  add(plan,npc,"right_pole","small-electric-pole",{x=f.x+14.5,y=f.y-3.5})
  add(plan,npc,"lab_pole","small-electric-pole",{x=f.x+15.5,y=f.y-1.5})
  belts(plan,npc,"iron_belt",iron_start,{x=f.x+6.5,y=f.y-2.5},defines.direction.east)
  local copper=copper_sites(npc,plan,min_copper_yield)
  belts(plan,npc,"copper_plate_belt",{x=copper.x-0.5,y=copper.y-10.5},{x=f.x+10.5,y=f.y-8.5})
  power_sites(npc,plan)
  pole_connection(plan,npc,"power_link",plan.sites.engine_pole.position,plan.sites.iron_pole.position,
    plan.power_wire_route)
  pole_connection(plan,npc,"copper_link",plan.sites.upper_pole.position,plan.sites.copper_pole.position)

  local demands={['automation-science-pack']=10}
  for _,site in pairs(plan.sites) do if not site.existing then demands[site.item]=(demands[site.item] or 0)+1 end end
  local budget,reason=Planner.material_budget(npc,demands,true)
  if not budget then fail(reason) end
  plan.materials=budget; plan.materials.construction_items=demands
  local function step(s) plan.steps[#plan.steps+1]=s end
  local function wait(condition) step{kind="wait",condition=condition} end
  local function carried(item,n,source) wait{kind="inventory",item=item,count=n,source_site=source} end
  local function technology(name,source) wait{kind="technology",technology=name,source_site=source} end
  -- Track inventory across the ordered plan, including native intermediate
  -- crafting. Reading today's pipe stock independently for every later group
  -- is unsafe: an earlier boiler/engine recipe may have consumed that stock.
  local planned_stock={}
  local function available(item)
    if planned_stock[item]==nil then
      planned_stock[item]=stock(npc,item)+(BASE[item] and (budget.raw_deficits[item] or 0) or 0)
    end
    return planned_stock[item]
  end
  local consume
  local function simulate_craft(item,batches)
    local recipe=npc.character.force.recipes[item]
    local per=recipe and yield_of(recipe,item)
    if not per then fail("craft_simulation_recipe_missing:"..item) end
    for _,ingredient in ipairs(recipe.ingredients or {}) do
      if ingredient.type~="item" then fail("fluid_craft_unsupported:"..item) end
      consume(ingredient.name,ingredient.amount*batches)
    end
    planned_stock[item]=available(item)+per*batches
  end
  consume=function(item,amount)
    local missing=math.max(0,amount-available(item))
    if missing>0 then
      if BASE[item] then fail("material_budget_underflow:"..item) end
      local recipe=npc.character.force.recipes[item]
      local per=recipe and yield_of(recipe,item)
      if not per then fail("ingredient_recipe_missing:"..item) end
      simulate_craft(item,math.ceil(missing/per))
    end
    planned_stock[item]=available(item)-amount
  end
  local function batch(keys)
    local quantities,groups={},{}
    for _,key in ipairs(keys) do local s=plan.sites[key]; if not s.existing then
      quantities[s.item]=(quantities[s.item] or 0)+1
      groups[s.item]=groups[s.item] or {}; groups[s.item][#groups[s.item]+1]=key
    end end
    for _,item in ipairs(sorted(quantities)) do
      local recipe=npc.character.force.recipes[item]
      local per=recipe and yield_of(recipe,item)
      if not per then fail("placement_recipe_missing:"..item) end
      local batches=math.ceil(math.max(0,quantities[item]-available(item))/per)
      if batches>0 then
        simulate_craft(item,batches)
        step{kind="craft",recipe=item,count=batches}
      end
      -- Commit this placement group before another recipe can auto-craft using
      -- its inventory items (e.g. steam-engine consumes finished pipe items).
      for _,key in ipairs(groups[item]) do local s=plan.sites[key]
        step{kind="build",site=key,target_site=key,item=s.item,entity_name=s.name,position=s.position,direction=s.direction}
      end
      consume(item,quantities[item])
    end
  end
  for _,item in ipairs({"coal","stone","wood"}) do
    local target=(budget.raw_deficits[item] or 0)+(item=="coal" and 120 or 0)
    if target>0 then step{kind="mine",item=item,count=target,search_radius=128} end
  end
  technology("steam-power","iron_furnace")
  carried("iron-plate",math.max(60,(budget.raw_deficits['iron-plate'] or 0)+stock(npc,"iron-plate")),"iron_furnace")
  batch({"copper_furnace"})
  step{kind="transfer",target_site="copper_furnace",item="coal",count=4,mode="deposit"}
  technology("electronics","copper_furnace")
  carried("copper-plate",math.max(20,(budget.raw_deficits['copper-plate'] or 0)+stock(npc,"copper-plate")),"copper_furnace")
  local early={"pump","water_pipe1","water_pipe2","water_pipe3","water_pipe4","boiler","steam_pipe","engine","lab"}
  for _,key in ipairs(sorted(plan.sites)) do if plan.sites[key].name=="small-electric-pole" then early[#early+1]=key end end
  batch(early)
  step{kind="transfer",target_site="boiler",item="coal",count=20,mode="deposit"}
  wait{kind="powered",target_site="lab"}
  technology("automation-science-pack")
  simulate_craft("automation-science-pack",10)
  step{kind="craft",recipe="automation-science-pack",count=10}
  step{kind="transfer",target_site="lab",item="automation-science-pack",count=10,mode="deposit",inventory=defines.inventory.lab_input}
  consume("automation-science-pack",10)
  step{kind="research",technology="automation"}
  technology("automation")
  local remaining={}
  local early_set={} for _,key in ipairs(early) do early_set[key]=true end early_set.copper_furnace=true
  for _,key in ipairs(sorted(plan.sites)) do if not early_set[key] and key~="iron_furnace" then remaining[#remaining+1]=key end end
  batch(remaining)
  step{kind="configure_recipe",target_site="gear_assembler",recipe="iron-gear-wheel"}
  step{kind="configure_recipe",target_site="science_assembler",recipe="automation-science-pack"}
  step{kind="transfer",target_site="copper_drill",item="coal",count=8,mode="deposit"}
  step{kind="transfer",target_site="copper_ore_inserter",item="coal",count=4,mode="deposit"}
  step{kind="transfer",target_site="copper_furnace",item="coal",count=4,mode="deposit"}
  step{kind="research",technology="electric-mining-drill"}
  wait{kind="products_finished",target_site="science_assembler",count=25,source_site="iron_furnace"}
  technology("electric-mining-drill","iron_furnace")
  plan.completion={technology="electric-mining-drill",native_science_products=25,
    requires_physical_belt_delivery=true,scripted_construction_teacher=true}
  return plan
end

function Planner.plan(npc,project_id)
  if project_id~="starter_power_science" then return nil,"unknown_project" end
  local minimum=0
  for _=1,2 do
    local ok,result=pcall(make_plan,npc,minimum)
    if not ok then return nil,result end
    -- The first copper plates are hand-mined before the planned drill is
    -- built. Preserve enough actual ore in its native mining footprint for
    -- that material bill and a further 500 plates of starter production.
    local required=(result.materials.raw_deficits['copper-plate'] or 0)+500
    result.copper_drill_required_ore=required
    if result.copper_drill_observed_ore>=required then return result end
    minimum=required
  end
  return nil,'no_copper_drill_footprint_with_required_ore_within_128'
end
-- Narrow saved-plan proposal. The caller owns the atomic adoption and scoped
-- cancellation of outstanding Worker work; this function only observes native
-- sites/stock and returns a serializable future-plan replacement.
function Planner.refresh_saved_copper(npc,project)
  if not npc.character or not npc.character.valid or not project
      or project.id~='starter_power_science' or type(project.plan)~='table'
      or type(project.plan.steps)~='table' or type(project.plan.sites)~='table' then
    return nil,'matching_saved_starter_required'
  end
  local old=project.plan
  local current=old.steps[project.index]
  if not current or current.kind~='build' or current.target_site~='copper_drill'
      or not old.sites.copper_drill or existing(npc,old.sites.copper_drill) then
    return nil,'unbuilt_current_copper_drill_required'
  end
  local furnace=old.sites.copper_furnace
  local inserter=old.sites.copper_ore_inserter
  if not furnace or not inserter or not existing(npc,furnace)
      or not existing(npc,inserter) or inserter.direction~=defines.direction.south then
    return nil,'owned_north_pickup_copper_furnace_required'
  end
  for key,site in pairs(old.sites) do
    if key:match('^copper_ore_belt%d+$') and existing(npc,site) then
      return nil,'built_copper_route_preserved'
    end
  end
  local function copy(value)
    if type(value)~='table' then return value end
    local out={} for k,v in pairs(value) do out[k]=copy(v) end return out
  end
  local function propose()
    local plan=copy(old)
    plan.sites.copper_drill=nil
    for key in pairs(plan.sites) do
      if key:match('^copper_ore_belt%d+$') then plan.sites[key]=nil end
    end
    local ores=npc.character.surface.find_entities_filtered{
      name='copper-ore',position=npc.home,radius=128}
    table.sort(ores,function(a,b)
      local da=(a.position.x-npc.home.x)^2+(a.position.y-npc.home.y)^2
      local db=(b.position.x-npc.home.x)^2+(b.position.y-npc.home.y)^2
      return da<db or (da==db and (a.position.x<b.position.x or
        (a.position.x==b.position.x and a.position.y<b.position.y)))
    end)
    local seen={}
    for _,ore in ipairs(ores) do
      local p={x=math.floor(ore.position.x),y=math.floor(ore.position.y)}
      local unique=p.x..':'..p.y
      local observed=copper_footprint_yield(npc,p)
      if not seen[unique] and observed>=500 then
        seen[unique]=true
        local trial=copy(plan)
        local drill=make_site(npc,'burner-mining-drill',p,defines.direction.north)
        if compatible(npc,drill,trial.sites) then
          trial.sites.copper_drill=drill
          local okay,points=pcall(path,npc,trial.sites,'transport-belt',
            {x=p.x-.5,y=p.y-1.5},
            {x=inserter.position.x,y=inserter.position.y+1})
          if okay then
            for i,point in ipairs(points) do
              local following=points[i+1]
              add(trial,npc,'copper_ore_belt'..i,'transport-belt',point,
                following and direction(following.x-point.x,following.y-point.y)
                or defines.direction.north)
            end
            local steps,inserted={},false
            local belt_craft
            for i,step in ipairs(old.steps) do
              if i<project.index then steps[#steps+1]=copy(step)
              elseif step.kind=='build' and (step.target_site or ''):match('^copper_ore_belt%d+$') then
                if not inserted then
                  for j=1,#points do
                    local key='copper_ore_belt'..j
                    local s=trial.sites[key]
                    steps[#steps+1]={kind='build',site=key,target_site=key,
                      item=s.item,entity_name=s.name,position=copy(s.position),direction=s.direction}
                  end
                  inserted=true
                end
              else
                local s=copy(step)
                if s.kind=='build' and s.target_site=='copper_drill' then
                  s.position=copy(p);s.direction=defines.direction.north
                elseif s.kind=='craft' and s.recipe=='transport-belt' then
                  if belt_craft then fail('ambiguous_future_belt_craft') end
                  belt_craft=s
                end
                steps[#steps+1]=s
              end
            end
            if not inserted or not belt_craft then fail('future_copper_route_build_group_missing') end
            local demands={}
            for i=project.index,#steps do
              local step=steps[i]
              if step.kind=='build' then
                local site=trial.sites[step.target_site]
                if not site then fail('unknown_future_site') end
                if not existing(npc,site) then
                  demands[site.item]=(demands[site.item] or 0)+1
                end
              end
            end
            local budget,reason=Planner.material_budget(npc,demands,false)
            if not budget then fail(reason) end
            local required=500+(budget.raw_deficits['copper-plate'] or 0)
            if observed>=required then
              belt_craft.count=math.ceil(math.max(0,
                (demands['transport-belt'] or 0)-stock(npc,'transport-belt'))/2)
              local supplies={}
              local function supply(s) supplies[#supplies+1]=s end
              for _,item in ipairs(sorted(budget.raw_deficits)) do
                local amount=budget.raw_deficits[item]
                if amount>0 then
                  if item=='iron-plate' or item=='copper-plate' then
                    local source=item=='iron-plate' and 'iron_furnace' or 'copper_furnace'
                    local ore_item=item=='iron-plate' and 'iron-ore' or 'copper-ore'
                    supply{kind='mine',item=ore_item,count=amount,search_radius=128}
                    supply{kind='transfer',target_site=source,item='coal',count=4,mode='deposit'}
                    supply{kind='transfer',target_site=source,item=ore_item,count=amount,
                      mode='deposit',inventory=defines.inventory.crafter_input}
                    supply{kind='wait',condition={kind='inventory',target_site=source,
                      item=item,count=amount}}
                    supply{kind='transfer',target_site=source,item=item,count=amount,mode='withdraw'}
                  else supply{kind='mine',item=item,count=amount,search_radius=128} end
                end
              end
              local final={}
              for i=1,project.index-1 do final[#final+1]=steps[i] end
              for _,s in ipairs(supplies) do final[#final+1]=s end
              for i=project.index,#steps do final[#final+1]=steps[i] end
              trial.steps=final
              trial.materials=budget;trial.materials.construction_items=demands
              trial.copper_drill_observed_ore=observed
              trial.copper_drill_required_ore=required
              trial.copper_drill_ore_observed_tick=game.tick
              trial.saved_copper_refresh={revision='saved-copper-route-v1',tick=game.tick,
                prior_revision=old.revision,prior_index=project.index,
                prior_drill=copy(old.sites.copper_drill),old_ore_belts=5,
                new_ore_belts=#points,supply_steps=#supplies,
                preserved_furnace_unit=existing(npc,furnace).unit_number,
                preserved_inserter_unit=existing(npc,inserter).unit_number}
              return trial
            end
          end
        end
      end
    end
    fail('no_legal_funded_saved_copper_route_within_128')
  end
  local okay,result=pcall(propose)
  if not okay then return nil,result end
  return result
end
return Planner
