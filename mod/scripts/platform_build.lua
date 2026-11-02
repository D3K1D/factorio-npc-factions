-- Building on the platform (R-8). The engineer cannot go there: it lays ghosts of a platform layout on the
-- platform's surface (as a player does from the remote view), and the platform builds them from its hub's stock.
-- What the hub lacks goes up in the silo's rockets (Orbit.supply loads and launches them).
local Build={}
local Ghosts=require("scripts.ghosts")
local TILE="space-platform-foundation"
local PLACE_EVERY=600   -- ticks between looks for ghosts to lay (entity ghosts wait for the foundation under them)

-- The layout, from the hub's centre (positions are entity centres; the hub covers -4..3). The starter pack lays
-- foundation over -5..4; this widens it and puts on it what the platform needs, each piece once its force can make it.
-- North of the hub, three mining lines, one per kind of asteroid chunk: a collector at the edge (its arm over empty
-- space, its back on foundation) filtered to its line's kind (unfiltered, the kinds its crusher does not take would
-- fill it and stall the line), an inserter taking its chunks into a crusher set to crush that kind, an
-- inserter putting the crusher's ore, carbon or ice into the hub; the hub is the platform's store. Beside the hub, its
-- space science: west an electric furnace smelting the hub's iron ore, east an assembler making the packs from the
-- hub's plates, carbon and ice, each with an inserter in from the hub and one back. South of the hub, solar panels
-- (constant in orbit: 180 kW each over Nauvis, in a wing to the west), and medium poles joining it all. A piece named by several machines
-- takes the best its force can make. Later steps add the thrusters' fuel and oxidizer, thrusters and turrets.
local function layout()
  local entities={}
  local function add(name,x,y,extra)
    local piece={name=name,x=x,y=y,direction="north"}
    for k,v in pairs(extra or {}) do piece[k]=v end
    entities[#entities+1]=piece
  end
  for i,kind in ipairs({"metallic","carbonic","oxide"}) do
    local x0=-4+3*(i-1)   -- (the line's west column)
    local recipe=kind.."-asteroid-crushing"
    add("asteroid-collector",x0+1.5,-10.5,{filter=kind.."-asteroid-chunk"})
    add("inserter",x0+.5,-8.5)                      -- (north: picks from the collector, drops in the crusher)
    add("crusher",x0+1,-6.5,{recipe=recipe})
    add("inserter",x0+.5,-4.5)                      -- (from the crusher into the hub)
  end
  add("electric-furnace",-6.5,.5)
  add("inserter",-4.5,-.5,{direction="east",filter="iron-ore"})   -- (east: the hub's ore into the furnace; unfiltered
                                                                   -- it would put plates in too, for steel, and jam)
  add("inserter",-4.5,1.5,{direction="west"})       -- (west: from the furnace into the hub)
  add({"assembling-machine-3","assembling-machine-2","assembling-machine-1"},6.5,.5,{recipe="space-science-pack"})
  add("inserter",4.5,-.5,{direction="west"})        -- (from the hub into the assembler)
  add("inserter",4.5,1.5,{direction="east"})        -- (from the assembler into the hub)
  -- South of the hub, its thrust (laid once space-platform-thruster opens the thruster; the plants' recipes set then):
  -- at the hub's south-west and south-east corners a chemical plant melting the hub's ice, each piping its water
  -- (out of its south side) into the first fluid input of the plant beside it, the one a recipe's only fluid goes to;
  -- under the hub those two, turned to take water on their outer side and give out on their inner side: west the
  -- thruster fuel (the hub's carbon), east the oxidizer (the hub's iron ore); each output piped down its own column to
  -- the thruster's upper fuel (west) and oxidizer (east) inputs.
  add("chemical-plant",-6.5,3.5,{recipe="ice-melting"})
  add("inserter",-4.5,3.5,{direction="east"})       -- (the hub's ice into the west melter)
  add("chemical-plant",6.5,3.5,{recipe="ice-melting"})
  add("inserter",4.5,3.5,{direction="west"})        -- (into the east melter)
  add("chemical-plant",-2.5,6.5,{recipe="thruster-fuel",direction="west"})
  add("inserter",-2.5,4.5)                          -- (north: the hub's carbon in)
  add("chemical-plant",3.5,6.5,{recipe="thruster-oxidizer",direction="east"})
  add("inserter",3.5,4.5)                           -- (the hub's iron ore in)
  for _,p in ipairs({{-7.5,5.5},{-6.5,5.5},{-5.5,5.5},{-4.5,5.5},{-4.5,6.5},{-4.5,7.5},   -- (west water)
      {5.5,5.5},{6.5,5.5},{7.5,5.5},                                                     -- (east water)
      {-.5,5.5},{-.5,6.5},{-.5,7.5},{-.5,8.5},{-1.5,8.5},{-2.5,8.5},{-2.5,9.5},{-2.5,10.5},   -- (fuel)
      {1.5,5.5},{1.5,6.5},{1.5,7.5},{1.5,8.5},{2.5,8.5},{2.5,9.5},{2.5,10.5}}) do             -- (oxidizer)
    add("pipe",p[1],p[2])
  end
  -- Its defense against asteroids: an assembler west of the hub making magazines from its plates, lifting them into a
  -- gun turret to its north and the rest into the hub; a second turret east of the hub fed from the hub.
  add({"assembling-machine-3","assembling-machine-2","assembling-machine-1"},-6.5,-2.5,{recipe="firearm-magazine"})
  add("inserter",-4.5,-2.5,{direction="east"})      -- (the hub's plates in)
  add("inserter",-4.5,-1.5,{direction="west"})      -- (magazines into the hub)
  add("inserter",-6.5,-4.5,{direction="south"})     -- (magazines into the turret)
  add("gun-turret",-7,-6)
  add("gun-turret",6,-3)
  add("inserter",4.5,-3.5,{direction="west"})       -- (the hub's magazines into it)
  add("thruster",0,12.5)   -- (at the platform's back edge: open space behind it, its exhaust's, 90 tiles down)
  -- Its power: medium poles through the middle, and a wing of solar panels to the west (none behind the thruster).
  for _,p in ipairs({{-1.5,-6.5},{1.5,-6.5},{4.5,-2.5},{-4.5,.5},{-4.5,-4.5},{4.5,4.5},{.5,6.5}}) do
    add("medium-electric-pole",p[1],p[2])
  end
  for _,y in ipairs({-6.5,-2.5,1.5,5.5}) do
    for _,x in ipairs({-15.5,-9.5}) do add("medium-electric-pole",x,y) end
  end
  for _,y in ipairs({-8.5,-4.5,-.5,3.5,7.5}) do
    for _,x in ipairs({-15.5,-12.5,-9.5}) do add("solar-panel",x,y) end
  end
  return {foundation={{{-8,-11},{8,14}},{{-17,-10},{-9,8}}},entities=entities}
end
Build.LAYOUT=layout()

local function surface_of(platform)
  return platform and platform.valid and platform.hub and platform.hub.valid and platform.surface or nil
end

-- A piece's recipe, unlocked for its force (a crusher stands idle until then).
function Build.configurable(force,piece)
  local recipe=force.recipes[piece.recipe]
  return recipe~=nil and recipe.enabled
end

-- A built piece set as the layout wants it, its recipe (once unlocked) and its first filter; with `set`, made so.
-- Returns whether it was so before.
local function settled(e,piece,force,set)
  local ok=true
  if piece.recipe and Build.configurable(force,piece) then
    local current=e.get_recipe()
    if not current or current.name~=piece.recipe then
      ok=false
      if set then e.set_recipe(piece.recipe) end
    end
  end
  if piece.filter then
    local inserter=e.type=="inserter"   -- (an inserter's filter: an item, and its filters turned on)
    local current=e.get_filter(1)
    if not current or (current.name or current)~=piece.filter or (inserter and not e.use_filters) then
      ok=false
      if set then
        if inserter then e.use_filters=true end
        e.set_filter(1,inserter and {name=piece.filter} or piece.filter)
      end
    end
  end
  return ok
end

-- A layout's foundation: a list of rectangles {{x0,y0},{x1,y1}} from the hub's centre (or one rectangle).
local function rects(layout)
  local f=layout.foundation
  return type(f[1][1])=="number" and {f} or f
end

local function makeable(force,name)
  local recipe=force.recipes[name]
  return recipe~=nil and recipe.enabled and prototypes.entity[name]~=nil
end

-- The machines a piece may be (one name, or several best first), and the one its force can make now (best first).
local function names(piece)
  return type(piece.name)=="table" and piece.name or {piece.name}
end
local function choice(force,piece)
  for _,name in ipairs(names(piece)) do
    if makeable(force,name) then return name end
  end
end

-- Lays the layout's ghosts not yet laid or built, and sets its built pieces' recipes and filters (every PLACE_EVERY ticks).
-- Returns how many it laid or set.
function Build.place(npc,platform,tick,layout)
  local surface=surface_of(platform)
  if not surface then return 0 end
  if npc.platform_placed and tick-npc.platform_placed<PLACE_EVERY then return 0 end
  npc.platform_placed=tick
  layout=layout or Build.LAYOUT
  local force=npc.force
  local origin=platform.hub.position
  local laid=0
  local ox,oy=math.floor(origin.x),math.floor(origin.y)
  for _,area in ipairs(rects(layout)) do
    for x=ox+area[1][1],ox+area[2][1] do
      for y=oy+area[1][2],oy+area[2][2] do
        local tile=surface.get_tile(x,y)
        if tile.name~=TILE and not tile.has_tile_ghost(force) then
          if surface.create_entity{name="tile-ghost",inner_name=TILE,position={x+.5,y+.5},force=force} then laid=laid+1 end
        end
      end
    end
  end
  for _,piece in ipairs(layout.entities) do
    local name=choice(force,piece)
    if name then
      local position={origin.x+piece.x,origin.y+piece.y}
      local direction=defines.direction[piece.direction or "north"]
      local any={}
      for _,n in ipairs(names(piece)) do any[n]=true end
      local wanted=names(piece)
      local there=surface.find_entities_filtered{position=position,force=force,name={"entity-ghost",table.unpack(wanted)}}
      local found=false
      for _,e in ipairs(there) do
        -- (any of its machines there will do: a lesser one built is kept, not replaced)
        if any[e.name] or (e.name=="entity-ghost" and any[e.ghost_name]) then found=true end
        -- (its recipe and filter set once built, as from the remote view; a ghost is laid bare)
        if any[e.name] and not settled(e,piece,force,true) then laid=laid+1 end
      end
      if not found and Ghosts.create(surface,{inner_name=name,position=position,direction=direction,force=force}) then
        laid=laid+1   -- (only where a ghost may go: W-35)
      end
    end
  end
  return laid
end

-- What the platform still lacks for its ghosts: {{item=, count=}, ...}, foundation first (the rest stands on it).
-- Its hub's stock, the cargo on its way up (`in_flight`, {item=count}) and the rocket loaded now (`cargo`) count.
function Build.needs(npc,platform,in_flight,cargo)
  local surface=surface_of(platform)
  if not surface then return {} end
  local want={}
  for _,ghost in ipairs(surface.find_entities_filtered{type={"entity-ghost","tile-ghost"},force=npc.force}) do
    local items=ghost.ghost_prototype.items_to_place_this
    local first=items and items[1]
    if first then want[first.name]=(want[first.name] or 0)+(first.count or 1) end
  end
  local hub=platform.hub.get_inventory(defines.inventory.hub_main)
  local out={}
  for item,n in pairs(want) do
    local have=(hub and hub.get_item_count({name=item,quality="normal"}) or 0)+((in_flight or {})[item] or 0)
      +(cargo and cargo.valid and cargo.get_item_count({name=item,quality="normal"}) or 0)
    if n>have then out[#out+1]={item=item,count=n-have} end
  end
  table.sort(out,function(a,b)
    if (a.item==TILE)~=(b.item==TILE) then return a.item==TILE end
    return a.item<b.item
  end)
  return out
end

-- How many of an item one rocket lifts (by weight).
function Build.lift(item)
  local prototype=prototypes.item[item]
  local weight=prototype and prototype.weight or 0
  if weight<=0 then return prototype and prototype.stack_size or 1 end
  return math.max(1,math.floor(1000000/weight))   -- (default_rocket_lift_weight: 1000 kg)
end

-- The layout stands: every foundation tile and every piece its force can make, built (not ghosts).
function Build.done(npc,platform,layout)
  local surface=surface_of(platform)
  if not surface then return false end
  layout=layout or Build.LAYOUT
  local origin=platform.hub.position
  local ox,oy=math.floor(origin.x),math.floor(origin.y)
  for _,area in ipairs(rects(layout)) do
    for x=ox+area[1][1],ox+area[2][1] do
      for y=oy+area[1][2],oy+area[2][2] do
        if surface.get_tile(x,y).name~=TILE then return false end
      end
    end
  end
  for _,piece in ipairs(layout.entities) do
    if choice(npc.force,piece) then
      local e=surface.find_entities_filtered{name=names(piece),force=npc.force,
        position={origin.x+piece.x,origin.y+piece.y},limit=1}[1]
      if not e or not settled(e,piece,npc.force,false) then return false end
    end
  end
  return true
end

-- Whether a built machine of the platform's layout stands set to make `item` (its recipe set): what its force can
-- count on getting from space.
function Build.makes(npc,platform,item,layout)
  local surface=surface_of(platform)
  if not surface then return false end
  local origin=platform.hub.position
  for _,piece in ipairs((layout or Build.LAYOUT).entities) do
    local recipe=piece.recipe and prototypes.recipe[piece.recipe]
    local gives=false
    for _,product in ipairs(recipe and recipe.products or {}) do
      if product.name==item then gives=true end
    end
    if gives then
      local e=surface.find_entities_filtered{name=names(piece),force=npc.force,
        position={origin.x+piece.x,origin.y+piece.y},limit=1}[1]
      local current=e and e.get_recipe()
      if current and current.name==piece.recipe then return true end
    end
  end
  return false
end

-- What the platform's machines have put in its hub so far, of what they make ({item=count}; what a recipe gives back of
-- its own ingredients, a crusher's chunk, is not counted).
function Build.made(platform,layout)
  if not surface_of(platform) then return {} end
  local hub=platform.hub.get_inventory(defines.inventory.hub_main)
  local out={}
  for _,piece in ipairs((layout or Build.LAYOUT).entities) do
    local recipe=piece.recipe and prototypes.recipe[piece.recipe]
    local own={}
    for _,ingredient in ipairs(recipe and recipe.ingredients or {}) do own[ingredient.name]=true end
    for _,product in ipairs(recipe and recipe.products or {}) do
      if product.type=="item" and not own[product.name] and not out[product.name] then
        local n=hub and hub.get_item_count({name=product.name,quality="normal"}) or 0
        out[product.name]=n
      end
    end
  end
  return out
end

return Build
