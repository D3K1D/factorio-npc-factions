-- The armory: every military thing the installed mods add, sorted by what the game data says it does
-- (range, damage, health, resistances), never by its name. With dozens of modded turrets, ammo and
-- walls, the engineers and the companion's planners pick from this instead of a list of vanilla names.
local Recipes=require("scripts.recipes")
local Armory={}

local TURRETS={["ammo-turret"]="ammo",["electric-turret"]="electric",["fluid-turret"]="fluid",
  ["artillery-turret"]="artillery"}
-- Technology effects that make weapons stronger (the rest of a military tech is what it unlocks).
local BONUSES={["turret-attack"]=true,["ammo-damage"]=true,["gun-speed"]=true,["artillery-range"]=true,
  ["maximum-following-robots-count"]=true}

-- The classified prototypes, made once per game session from the prototypes (the same on every
-- machine and fixed while the game runs: a cache of game data, not game state).
local catalog

local function try(f) local ok,value=pcall(f); if ok then return value end end

local function placing(p)
  local out={}
  for _,item in ipairs(try(function() return p.items_to_place_this end) or {}) do out[#out+1]=item.name or item[1] end
  return out
end

local function health(p)
  return try(function() return p.get_max_health() end) or try(function() return p.max_health end)
end

-- {type={decrease,percent}} with percent as a whole number, only the resistances it has.
local function resistances(p)
  local out={}
  for kind,r in pairs(try(function() return p.resistances end) or {}) do
    if (r.decrease or 0)>0 or (r.percent or 0)>0 then
      out[kind]={decrease=r.decrease or 0,percent=math.floor((r.percent or 0)*100+0.5)}
    end
  end
  return out
end

-- Damage per use by type, read from a trigger (an ammo type's action): damage effects anywhere in it,
-- times repeats (shotgun pellets). Damage dealt by a projectile's own impact is not in the ammo's data;
-- such ammo ranks by its order among its kind.
local function damage(action,out,times,depth)
  if type(action)~="table" or depth>10 then return out end
  times=times*(tonumber(action.repeat_count) or 1)
  if action.type=="damage" and type(action.damage)=="table" then
    local d=action.damage
    if d.type then out[d.type]=(out[d.type] or 0)+(d.amount or 0)*times end
  end
  for key,value in pairs(action) do
    if key~="damage" and type(value)=="table" then damage(value,out,times,depth+1) end
  end
  return out
end

local function total(by_type)
  local n=0
  for _,v in pairs(by_type) do n=n+v end
  return n
end
-- (the odds of a fight read ammo and bites the same way: combat_odds.lua)
function Armory.damage_by_type(action) return damage(action,{},1,0) end

local function ammo_category(item)
  return try(function() return item.ammo_category.name end) or try(function() return item.get_ammo_type().category end)
end

local function turret_row(p)
  -- (an artillery turret shoots with its gun: the gun has the range and the ammo)
  local a=try(function() return p.attack_parameters end)
  if not a then
    for _,gun in pairs(try(function() return p.guns end) or {}) do a=a or try(function() return gun.attack_parameters end) end
  end
  a=a or {}
  a.range=a.range or try(function() return p.turret_range end)
  local row={name=p.name,kind=TURRETS[p.type],health=health(p),range=a.range,min_range=a.min_range,
    cooldown=a.cooldown,size={p.tile_width,p.tile_height},items=placing(p),resistances=resistances(p),
    ammo_categories=a.ammo_categories or (a.ammo_category and {a.ammo_category}) or {}}
  if row.kind=="electric" then
    -- (a laser's damage is in its beam, not here: then it is unknown, not nought)
    local by=damage(a.ammo_type and a.ammo_type.action,{},1,0)
    if total(by)>0 then row.damage,row.damage_types=total(by),by end
  elseif row.kind=="fluid" then
    row.fluids={}
    for _,f in ipairs(a.fluids or {}) do row.fluids[#row.fluids+1]=f.type end
  end
  return row
end

local function by_order(list,key)
  table.sort(list,function(a,b)
    if (a[key] or 0)~=(b[key] or 0) then return (a[key] or 0)>(b[key] or 0) end
    if a.order~=b.order then return (a.order or "")>(b.order or "") end   -- (mods number tiers upward)
    return a.name<b.name
  end)
  return list
end

local function items_of(kind)
  return try(function() return prototypes.get_item_filtered{{filter="type",type=kind}} end) or {}
end

local function entities_of(kinds)
  local filters={}
  for _,kind in ipairs(kinds) do filters[#filters+1]={filter="type",type=kind} end
  return try(function() return prototypes.get_entity_filtered(filters) end) or {}
end

local function build()
  local c={turrets={},ammo={},walls={},mines={},repair={},guns={},armor={},capsules={},vehicles={},packs={},
    techs={}}
  local wanted={}   -- item -> true: what a military tech is for
  for _,p in pairs(entities_of({"ammo-turret","electric-turret","fluid-turret","artillery-turret"})) do
    c.turrets[#c.turrets+1]=turret_row(p)
  end
  table.sort(c.turrets,function(a,b) return a.name<b.name end)
  for _,p in pairs(entities_of({"wall","gate"})) do
    c.walls[#c.walls+1]={name=p.name,gate=p.type=="gate",health=health(p),resistances=resistances(p),items=placing(p),
      order=p.order}
  end
  by_order(c.walls,"health")
  for _,p in pairs(entities_of({"land-mine"})) do
    c.mines[#c.mines+1]={name=p.name,health=health(p),items=placing(p),
      trigger_radius=try(function() return p.trigger_radius end)}
  end
  table.sort(c.mines,function(a,b) return a.name<b.name end)
  -- Repair turrets are roboports without robots and with a slot for repair packs: they mend what
  -- stands in reach. (Hidden ones belong to other mods' machinery, a Factorissimo building's.)
  for _,p in pairs(entities_of({"roboport"})) do
    local robots=try(function() return p.get_inventory_size(defines.inventory.roboport_robot) end)
    local packs=try(function() return p.get_inventory_size(defines.inventory.roboport_material) end) or 0
    local reach=try(function() return p.construction_radius end) or 0
    if robots==0 and packs>0 and reach>0 and not try(function() return p.hidden end) then
      c.repair[#c.repair+1]={name=p.name,radius=reach,items=placing(p),health=health(p)}
    end
  end
  table.sort(c.repair,function(a,b) return a.name<b.name end)
  for _,p in pairs(entities_of({"car","spider-vehicle"})) do
    local guns={}
    for name in pairs(try(function() return p.guns end) or {}) do guns[#guns+1]=name end
    table.sort(guns)
    c.vehicles[#c.vehicles+1]={name=p.name,kind=p.type,health=health(p),guns=guns,items=placing(p),
      resistances=resistances(p),order=p.order}
  end
  by_order(c.vehicles,"health")
  for name,item in pairs(items_of("ammo")) do
    local at=try(function() return item.get_ammo_type("turret") end) or try(function() return item.get_ammo_type() end)
    local by=damage(at and at.action,{},1,0)
    c.ammo[#c.ammo+1]={name=name,category=ammo_category(item),damage=total(by),damage_types=by,order=item.order,
      magazine=try(function() return item.magazine_size end)}
  end
  by_order(c.ammo,"damage")
  -- A turret is of use only if it needs no ammo, or some ammo fits it (not an ion cannon's targeter).
  local categories={}
  for _,ammo in ipairs(c.ammo) do if ammo.category then categories[ammo.category]=true end end
  for _,t in ipairs(c.turrets) do
    t.usable=t.kind=="electric" or t.kind=="fluid"
    for _,category in ipairs(t.ammo_categories) do t.usable=t.usable or categories[category]==true end
  end
  for name,item in pairs(items_of("gun")) do
    local a=try(function() return item.attack_parameters end) or {}
    c.guns[#c.guns+1]={name=name,range=a.range,cooldown=a.cooldown,ammo_categories=a.ammo_categories or {},
      order=item.order}
  end
  by_order(c.guns,"range")
  for name,item in pairs(items_of("armor")) do
    local grid=try(function() return item.equipment_grid end)
    c.armor[#c.armor+1]={name=name,resistances=resistances(item),order=item.order,
      inventory_bonus=try(function() return item.inventory_size_bonus end) or 0,
      grid=grid and {grid.width,grid.height} or nil}
  end
  for _,a in ipairs(c.armor) do
    local sum=0
    for _,r in pairs(a.resistances) do sum=sum+r.percent end
    a.protection=sum
  end
  by_order(c.armor,"protection")
  for name,item in pairs(items_of("capsule")) do
    local action=try(function() return item.capsule_action end) or {}
    local a=action.attack_parameters or {}
    local by=damage(a.ammo_type and a.ammo_type.action,{},1,0)
    c.capsules[#c.capsules+1]={name=name,use=action.type,range=a.range,damage=total(by),order=item.order}
  end
  by_order(c.capsules,"damage")
  local packs={}
  for _,lab in pairs(entities_of({"lab"})) do
    for _,pack in ipairs(try(function() return lab.lab_inputs end) or {}) do packs[pack]=true end
  end
  for name in pairs(packs) do
    local item=prototypes.item[name]
    c.packs[#c.packs+1]={name=name,order=item and item.order}
  end
  table.sort(c.packs,function(a,b) return (a.order or a.name)<(b.order or b.name) end)
  -- What a military tech is for: items placing any turret, wall, mine, repair turret or vehicle,
  -- and every gun, ammo, armor and capsule.
  for _,list in ipairs({c.turrets,c.walls,c.mines,c.repair,c.vehicles}) do
    for _,row in ipairs(list) do for _,item in ipairs(row.items) do wanted[item]=true end end
  end
  for _,list in ipairs({c.ammo,c.guns,c.armor,c.capsules}) do
    for _,row in ipairs(list) do wanted[row.name]=true end
  end
  for name,tech in pairs(prototypes.technology or {}) do
    local unlocks,bonuses={},{}
    for _,effect in ipairs(tech.effects or {}) do
      if effect.type=="unlock-recipe" then
        local recipe=prototypes.recipe[effect.recipe]
        for _,product in ipairs(recipe and recipe.products or {}) do
          if wanted[product.name] then unlocks[#unlocks+1]=product.name end
        end
      elseif BONUSES[effect.type] then
        bonuses[#bonuses+1]={type=effect.type,of=effect.ammo_category or effect.turret_id,modifier=effect.modifier}
      end
    end
    if #unlocks>0 or #bonuses>0 then
      local ingredients={}
      for _,i in ipairs(tech.research_unit_ingredients or {}) do ingredients[#ingredients+1]=i.name end
      local before={}
      for prerequisite in pairs(tech.prerequisites or {}) do before[#before+1]=prerequisite end
      table.sort(before)
      c.techs[#c.techs+1]={name=name,unlocks=unlocks,bonuses=bonuses,packs=ingredients,before=before,
        count=try(function() return tech.research_unit_count end),order=tech.order}
    end
  end
  table.sort(c.techs,function(a,b) return a.name<b.name end)
  -- What shoots, by name (a tech may share a turret's name): turrets over guns.
  c.by_name={}
  for _,list in ipairs({c.guns,c.turrets}) do
    for _,row in ipairs(list) do c.by_name[row.name]=row end
  end
  return c
end

function Armory.catalog()
  catalog=catalog or build()
  return catalog
end

-- Ammo that fits the turret (or gun) `name`, best first; with a force, only what it can make now or holds.
function Armory.ammo_for(name,force,holds)
  local c=Armory.catalog()
  local row=c.by_name[name]
  local fits={}
  for _,category in ipairs(row and row.ammo_categories or {}) do fits[category]=true end
  local out={}
  for _,ammo in ipairs(c.ammo) do
    if fits[ammo.category] and (not force or Recipes.ready(force,ammo.name) or (holds and holds(ammo.name)>0)) then
      out[#out+1]=ammo.name
    end
  end
  return out
end

-- Item names of every kind of military thing the force can make now (for the strategist's tools),
-- turrets, walls and ammo first.
function Armory.makeable(force,limit)
  local c,out,seen=Armory.catalog(),{},{}
  local function add(item)
    if not seen[item] and #out<(limit or 80) and Recipes.ready(force,item) then out[#out+1]=item end
    seen[item]=true
  end
  for _,key in ipairs({"turrets","walls","repair","mines","vehicles"}) do
    for _,row in ipairs(c[key]) do for _,item in ipairs(row.items) do add(item) end end
  end
  for _,key in ipairs({"ammo","capsules","guns","armor"}) do
    for _,row in ipairs(c[key]) do add(row.name) end
  end
  return out
end

-- What a force can defend with now, best first, for its defense planners: turrets (by range, then
-- health), walls and gates (by health), ammo for each category, repair turrets; and how far the biters
-- have evolved (`evolution`, given by the caller).
function Armory.defense(force,evolution)
  local c=Armory.catalog()
  local function makeable(items)
    for _,item in ipairs(items or {}) do if Recipes.ready(force,item) then return true end end
    return false
  end
  local turrets={}
  for _,t in ipairs(c.turrets) do if t.usable and makeable(t.items) then turrets[#turrets+1]=t end end
  table.sort(turrets,function(a,b)
    if (a.range or 0)~=(b.range or 0) then return (a.range or 0)>(b.range or 0) end
    if (a.health or 0)~=(b.health or 0) then return (a.health or 0)>(b.health or 0) end
    return a.name<b.name
  end)
  local out={turrets={},walls={},gates={},ammo={},repair={},evolution=evolution or 0}
  for i,t in ipairs(turrets) do
    out.turrets[i]={name=t.name,kind=t.kind,range=t.range,min_range=t.min_range or 0,w=t.size[1],h=t.size[2],
      ammo_categories=t.ammo_categories}
  end
  for _,w in ipairs(c.walls) do   -- (sorted by health already)
    if makeable(w.items) then
      local list=w.gate and out.gates or out.walls
      list[#list+1]={name=w.name,health=w.health}
    end
  end
  for _,a in ipairs(c.ammo) do   -- (best first already)
    if a.category and Recipes.ready(force,a.name) then
      out.ammo[a.category]=out.ammo[a.category] or {}
      table.insert(out.ammo[a.category],a.name)
    end
  end
  for _,r in ipairs(c.repair) do
    if makeable(r.items) then out.repair[#out.repair+1]={name=r.name,radius=r.radius} end
  end
  return out
end

-- Everything, with what this force can make now and what it has researched (for the companion).
function Armory.export(force)
  local c=Armory.catalog()
  local out={schema="npc-armory-v1",mods=script.active_mods}
  for key,list in pairs(c) do
    if key~="by_name" then
      local rows={}
      for i,row in ipairs(list) do
        local copy={}
        for k,v in pairs(row) do copy[k]=v end
        if force then
          if key=="techs" then
            local t=force.technologies[row.name]
            copy.researched=t and t.researched or false
          elseif row.items then
            local ready=false
            for _,item in ipairs(row.items) do ready=ready or Recipes.ready(force,item) end
            copy.can_make=ready
          else
            copy.can_make=Recipes.ready(force,row.name)
          end
        end
        rows[i]=copy
      end
      out[key]=rows
    end
  end
  return out
end

return Armory
