-- Clearing ground ahead of time: an engineer mines what stands where a later section of its factory will go
-- (the zones its factory plan keeps, or a zone someone asked for), keeping what it gives, and blasts cliffs
-- once it can make what blasts them. Big sections then fit when their turn comes.
--
-- What is in the way is the game's to say, not a list of names (mods add trees, rocks and ruins by the
-- hundred): anything of the map's own (the neutral force) whose collision layers meet a building's, that
-- does not move or pass on its own. Mineable: mined by hand. A cliff: blasted with the item its prototype
-- names. Anything else stays and is reported. Decoratives never stop a building (the game clears them under
-- whatever is placed), so they are left. Only its own ground: what lies where it may not build is skipped.
--
-- A clearing plan (companion/clearing.py) is a few stages, each a box (a slice of the zone, nearest home
-- first), and shares one budget (things cleared) and one time bound across them, so it never holds the
-- engineer from other work for long. Anything urgent (an ally's call, short power, an attack on its plot,
-- biters near) ends it at once. Each stage surveys its box when it ends; the site export reports what each
-- zone has left (`clearing`), so the planner knows when a zone is done.
local TeamWorld=require("scripts.team_world")
local TeamWar=require("scripts.team_war")
local Corrector=require("scripts.corrector")
local Recipes=require("scripts.recipes")
local Voice=require("scripts.voice")
local Character=require("scripts.character")
local Clearing={}

local BUDGET,MAX_BUDGET=50,200       -- things cleared by one plan (it may ask for fewer or more, up to this)
local TICKS,MAX_TICKS=14400,36000    -- and the longest one plan may take, walking there included
local MAX_SIDE=128                   -- a stage's box, tiles a side (the planner sends 32)
local MAX_ZONES=32                   -- zones remembered per engineer (the oldest go first)
local MAX_BOXES=256                  -- surveyed boxes kept per zone (a 512-tile zone in 32-tile slices)
local DANGER=48                      -- nothing is cleared this close to biters (units, nests, worms)
local CHECK_EVERY=300                -- ticks between looks for something more urgent while clearing
local FREE_SLOTS=2                   -- inventory slots it keeps free; fewer and the plan ends (storing is upkeep's)
local BLAST_WAIT=120                 -- ticks a thrown charge has to land before the cliff counts as standing
local NEWS=3600                      -- an attack or breach on its plot this recent ends the clearing
Clearing.GOAL_DELAY=300              -- after a clearing plan the next goal comes sooner than after a build

-- Types that move or pass on their own (or are no thing at all): never ground to clear, whatever their layers.
local NEVER={character=true,unit=true,["segmented-unit"]=true,car=true,["spider-vehicle"]=true,locomotive=true,
  ["cargo-wagon"]=true,["fluid-wagon"]=true,["artillery-wagon"]=true,["infinity-cargo-wagon"]=true,
  ["item-entity"]=true,corpse=true,["character-corpse"]=true,["rail-remnants"]=true,projectile=true,
  ["artillery-projectile"]=true,["combat-robot"]=true,["construction-robot"]=true,["logistic-robot"]=true,
  ["capture-robot"]=true,explosion=true,fire=true,sticker=true,stream=true,beam=true,["smoke-with-trigger"]=true,
  ["particle-source"]=true,["entity-ghost"]=true,["tile-ghost"]=true,["item-request-proxy"]=true,
  ["deconstructible-tile-proxy"]=true,["highlight-box"]=true,["speech-bubble"]=true,resource=true,
  asteroid=true,fish=true}
-- What a section is built from: the layers they collide with, together, are what "in the way" means.
local BUILDINGS={"assembling-machine","furnace","mining-drill","container","inserter","transport-belt",
  "underground-belt","splitter","electric-pole","pipe","pipe-to-ground","wall","lab","boiler","generator",
  "ammo-turret","radar","solar-panel","storage-tank","beacon","roboport"}

local function try(f) local ok,value=pcall(f); if ok then return value end end
local function finite(v) return type(v)=="number" and v==v and math.abs(v)<1e6 end
local function count(npc,item) return npc.character.get_main_inventory().get_item_count({name=item,quality="normal"}) end
local function box_key(b) return b[1]..","..b[2]..","..b[3]..","..b[4] end
local function area_of(b) return {{b[1],b[2]},{b[3],b[4]}} end
local function dist2(a,b) return (a.x-b.x)^2+(a.y-b.y)^2 end
local function obstacle_key(e) return e.name..":"..e.position.x..":"..e.position.y end

-- Worked out once per game load from the prototypes (the same on every peer).
local building_layers
local function building_mask()
  if building_layers then return building_layers end
  local layers={}
  for _,kind in ipairs(BUILDINGS) do   -- (one type at a time: a type a game lacks costs only itself)
    for _,p in pairs(try(function() return prototypes.get_entity_filtered{{filter="type",type=kind}} end) or {}) do
      local m=p.collision_mask
      if m and not m.colliding_with_tiles_only then for layer in pairs(m.layers or {}) do layers[layer]=true end end
    end
  end
  building_layers=layers
  return layers
end
Clearing.building_mask=building_mask

-- How a thing of this prototype goes: "mine", "blast", "fixed" (nothing clears it), or false (not in the way).
local kinds={}
local function kind_of(proto)
  local known=kinds[proto.name]
  if known~=nil then return known end
  local how=false
  local m=not NEVER[proto.type] and proto.collision_mask
  if m and not m.colliding_with_tiles_only then
    local layers=building_mask()
    for layer in pairs(m.layers or {}) do if layers[layer] then how="fixed"; break end end
  end
  if how then
    local mineable=proto.mineable_properties
    if mineable and mineable.minable and not mineable.required_fluid then how="mine"
    elseif proto.cliff_explosive_prototype then how="blast" end
  end
  kinds[proto.name]=how
  return how
end

-- A box from a plan (export-local, kept in world tiles): four numbers, sides at most `side` tiles, corners
-- within `bounds` ({lo,hi} in local tiles) when given. Nil when it is no such box.
local function box_from(raw,origin,side,bounds)
  if type(raw)~="table" then return nil end
  for i=1,4 do if not finite(raw[i]) then return nil end end
  local x0,y0,x1,y1=math.min(raw[1],raw[3]),math.min(raw[2],raw[4]),math.max(raw[1],raw[3]),math.max(raw[2],raw[4])
  if x1-x0<1 or y1-y0<1 or x1-x0>side or y1-y0>side then return nil end
  if bounds and (x0<bounds[1] or y0<bounds[1] or x1>bounds[2] or y1>bounds[2]) then return nil end
  return {origin.x+x0,origin.y+y0,origin.x+x1,origin.y+y1}
end

-- Whether the engineer may clear where this box lies (its own ground in the team world; anywhere else).
local function own_ground(npc,box)
  return TeamWorld.may_build(npc,area_of(box))==true
end

local function explosive_ready(npc,item)
  return count(npc,item)>0 or Recipes.ready(npc.force,item)
end

-- What stands in the way of building in `box` (world tiles), by how it goes: mine, blast (it can make or
-- holds the charge), later (the charge is still to be researched), fixed, outside (not its ground). The
-- things it can clear now come back too, for a stage to work through.
function Clearing.survey(npc,box)
  local surface=npc.character.surface
  local whole=own_ground(npc,box)
  local out={mine=0,blast=0,later=0,fixed=0,outside=0,explosives={}}
  local work={}
  for _,e in ipairs(surface.find_entities_filtered{area=area_of(box),force="neutral"}) do
    local how=kinds[e.name]
    if how==nil then how=kind_of(e.prototype) end
    if how=="mine" and not e.minable then how="fixed" end   -- (a script may lock one)
    if how and not whole and TeamWorld.may_build(npc,{{e.bounding_box.left_top.x,e.bounding_box.left_top.y},
        {e.bounding_box.right_bottom.x,e.bounding_box.right_bottom.y}},surface)~=true then how="outside" end
    local item=how=="blast" and e.prototype.cliff_explosive_prototype
    if item and not explosive_ready(npc,item) then how="later"; out.explosives[item]=true end
    if how then
      out[how]=out[how]+1
      if how=="mine" or how=="blast" then work[#work+1]={entity=e,how=how,item=item or nil,key=obstacle_key(e)} end
    end
  end
  return out,work
end

-- The clearing a plan asks for (zone and shared budget), checked; nil when the plan clears nothing.
function Clearing.plan_copy(raw,origin)
  if type(raw)~="table" then return nil end
  local z=type(raw.zone)=="table" and raw.zone or {}
  local priority=finite(z.priority) and math.max(0,math.min(1,z.priority)) or 0
  return {zone={id=type(z.id)=="string" and z.id:sub(1,64) or "zone",box=box_from(z.box,origin,4096),
      priority=priority,why=type(z.why)=="string" and z.why:sub(1,160) or nil,
      source=z.source=="plan" and "plan" or "asked"},
    budget=math.max(1,math.min(MAX_BUDGET,math.floor(tonumber(raw.budget) or BUDGET))),
    ticks=math.max(600,math.min(MAX_TICKS,math.floor(tonumber(raw.ticks) or TICKS))),
    done=0,failed=0,blasted=0}
end

-- A plan may hold no buildings at all when it clears ground.
function Clearing.clears(payload) return type(payload.clearing)=="table" end

-- A plan stage's ground to clear, and the area_clear witnesses tied to it (or to a box of their own). Nil,
-- or why the stage is refused.
function Clearing.stage(payload_stage,stage,origin,bounds)
  if payload_stage.clear~=nil then
    local box=type(payload_stage.clear)=="table" and box_from(payload_stage.clear.box,origin,MAX_SIDE,bounds)
    if not box then return "clear_box" end
    stage.clear={box=box,done=0,blasted=0,failed=0,skip={},short={}}
  end
  for i,w in ipairs(payload_stage.witnesses or {}) do
    local witness=stage.witnesses[i]
    if type(w)=="table" and w.kind=="area_clear" and witness then
      witness.box=w.box~=nil and box_from(w.box,origin,MAX_SIDE,bounds) or nil
      if w.box~=nil and not witness.box then return "witness_box" end
      if not witness.box and not stage.clear then return "witness_box" end
      if not witness.box then witness.clear=stage.clear end
    end
  end
end

-- The zone's record (what each surveyed box has left), kept for the site export.
local function remember(npc,tally,c,survey,tick)
  local zone=tally.zone
  npc.clearing=npc.clearing or {zones={}}
  local zones=npc.clearing.zones
  local z=zones[zone.id]
  local function same(a,b) return a and b and a[1]==b[1] and a[2]==b[2] and a[3]==b[3] and a[4]==b[4] end
  if not z or not same(z.box,zone.box) then   -- new, or redrawn: what was surveyed before no longer holds
    z={id=zone.id,box=zone.box,boxes={},cleared=0,stages=0}
    zones[zone.id]=z
  end
  z.priority,z.why,z.source,z.tick=zone.priority,zone.why,zone.source,tick
  z.cleared=z.cleared+c.done+c.blasted
  z.stages=z.stages+1
  local explosives={}
  for item in pairs(survey.explosives) do explosives[#explosives+1]=item end
  table.sort(explosives)
  local short=false
  for _ in pairs(c.short) do short=true end
  z.boxes[box_key(c.box)]={box=c.box,mine=survey.mine,blast=survey.blast,later=survey.later,fixed=survey.fixed,
    outside=survey.outside,short=short and survey.blast>0 or nil,explosives=#explosives>0 and explosives or nil,tick=tick}
  local n,oldest,oldest_key=0,nil,nil
  for k,b in pairs(z.boxes) do
    n=n+1
    if not oldest or b.tick<oldest then oldest,oldest_key=b.tick,k end
  end
  if n>MAX_BOXES then z.boxes[oldest_key]=nil end
  n,oldest,oldest_key=0,nil,nil
  for k,other in pairs(zones) do
    n=n+1
    if not oldest or (other.tick or 0)<oldest then oldest,oldest_key=other.tick or 0,k end
  end
  if n>MAX_ZONES then zones[oldest_key]=nil end
end

-- Something more urgent than clearing: why, or nil. (The planner put the clearing after everything waiting
-- when it chose it; this is for what comes up while it works.)
local function urgent(npc,tick)
  if npc.help_request then return "help" end
  if Corrector.power_short(npc,tick) then return "power" end
  local c=npc.character
  if c.surface.count_entities_filtered{position=c.position,radius=DANGER,force="enemy",limit=1}>0 then
    return "biters"
  end
  if TeamWorld.active() then
    local board=try(function() return TeamWar.threat_board(npc,nil,c.surface,tick) end)
    local last=board and board.attacks and board.attacks[1]
    if last and tick-last.tick<=NEWS then return "attack" end
    local breach=(try(function() return TeamWar.breaches(npc,nil,tick) end) or {})[1]
    if breach and tick-breach.tick<=NEWS then return "breach" end
  end
end

local function throw_range(item)
  local action=try(function() return prototypes.item[item].capsule_action end)
  local range=action and action.attack_parameters and action.attack_parameters.range
  return finite(range) and range>2 and range or 10
end

-- Throw a charge at a cliff as a player throws one (from its inventory: spent whatever happens).
local function throw(npc,item,target)
  local inventory=npc.character.get_main_inventory()
  local spec={name=item,quality="normal"}
  local stack
  for i=1,#inventory do
    local s=inventory[i]
    if s.valid_for_read and s.name==item and (not s.quality or s.quality.name=="normal") then stack=s; break end
  end
  if not stack then return false end
  local before=inventory.get_item_count(spec)
  pcall(function() return stack.use_capsule(npc.character,target.position) end)
  -- (an API path that throws without spending the charge, or throws nothing: one is spent all the same)
  if inventory.get_item_count(spec)==before and inventory.remove{name=item,quality="normal",count=1}<1 then
    return false
  end
  return true
end

local function used(tally) return tally.done+tally.blasted+tally.failed end

-- The stage's work: what it can clear now in its box, nearest home first, as much as the plan's budget has
-- left; things near biters wait for another time.
local function start(npc,tally,c,tick)
  c.started=tick
  local _,work=Clearing.survey(npc,c.box)
  local home=npc.home or npc.character.position
  table.sort(work,function(a,b)
    local da,db=dist2(a.entity.position,home),dist2(b.entity.position,home)
    if da~=db then return da<db end
    return a.key<b.key
  end)
  local room=tally.budget-used(tally)
  local surface=npc.character.surface
  c.queue={}
  for _,w in ipairs(work) do
    if #c.queue>=room then break end
    if surface.count_entities_filtered{position=w.entity.position,radius=DANGER,force="enemy",limit=1}==0 then
      c.queue[#c.queue+1]=w
    end
  end
end

-- The stage is over: its box surveyed again and remembered for the zone. `plan_over`: the whole plan stops
-- here (urgent work, its time or budget spent, a full inventory); its later stages then end unsurveyed.
local function finish(npc,d,tally,c,reason,plan_over,tick)
  c.over=reason
  if plan_over then tally.over=reason end
  local survey=Clearing.survey(npc,c.box)
  c.left=survey.mine+survey.blast
  remember(npc,tally,c,survey,tick)
  if (plan_over or d.stage_index>=#d.plan.stages) and not tally.spoke and tally.done+tally.blasted>0 then
    tally.spoke=true
    Character.speak(npc,"clear_done",nil,{count=tally.done+tally.blasted},{priority=1})
  end
end

-- One job for the stage's clearing: (job, meta); (nil, nil, wait reason) while a thrown charge lands; or nil
-- once the stage's clearing is over (its area_clear witness then passes). `acquire` is designed_start's: how
-- the engineer gets any item (its chests, its machines, by hand).
function Clearing.step(npc,d,stage,tick,acquire)
  local c,tally=stage.clear,d.plan.clearing
  if not tally or c.over then return nil end
  if tally.over then c.over=tally.over; return nil end
  tally.started=tally.started or tick
  if not c.started then start(npc,tally,c,tick) end
  if not c.checked or tick>=c.checked+CHECK_EVERY then
    c.checked=tick
    local why=urgent(npc,tick)
    if why then return finish(npc,d,tally,c,"urgent:"..why,true,tick) end
  end
  if tick-tally.started>=tally.ticks then return finish(npc,d,tally,c,"time",true,tick) end
  if npc.character.get_main_inventory().count_empty_stacks()<FREE_SLOTS then
    return finish(npc,d,tally,c,"inventory_full",true,tick)
  end
  -- the nearest of what is left to the engineer, as a player works through a stand of trees
  local here=npc.character.position
  local best,best_d,charges
  for i=#c.queue,1,-1 do
    local w=c.queue[i]
    local e=w.entity
    if not (e and e.valid) then
      if w.thrown then tally.blasted=tally.blasted+1; c.blasted=c.blasted+1 end
      table.remove(c.queue,i)
    elseif w.thrown then
      if tick-w.thrown<BLAST_WAIT then return nil,nil,"blasting" end
      -- The charge was spent and did not take it down (it landed beside it): what it does, done.
      if pcall(function() e.destroy{do_cliff_correction=true} end) and not e.valid then
        tally.blasted=tally.blasted+1; c.blasted=c.blasted+1
      else c.skip[w.key]=true end
      table.remove(c.queue,i)
    elseif not c.skip[w.key] and not (w.item and c.short[w.item]) then
      local dd=dist2(e.position,here)
      if not best_d or dd<best_d then best,best_d=w,dd end
      if w.item then charges=(charges or 0)+1 end
    end
  end
  if used(tally)>=tally.budget then return finish(npc,d,tally,c,"budget",true,tick) end
  if not best then return finish(npc,d,tally,c,"done",false,tick) end
  local e=best.entity
  if best.how=="mine" then
    return {kind="mine",target=e,count=1,mode="clear"},{clearing=true,stage=stage,tally=tally,key=best.key,how="mine"}
  end
  local item=best.item
  if count(npc,item)<1 then
    -- enough for the cliffs it still means to blast here, made or fetched in one go
    local job,why=acquire(npc,item,math.max(1,charges or 1),0)
    if job then return job,{clearing=true,stage=stage,tally=tally,item=item,how="acquire"} end
    c.short[item]=why or true   -- it cannot get one now: these cliffs wait for another time
    return Clearing.step(npc,d,stage,tick,acquire)
  end
  local range=throw_range(item)
  if math.sqrt(best_d)>range-1 then
    local spot=npc.character.surface.find_non_colliding_position("character",e.position,range-2,0.5)
    if not spot then c.skip[best.key]=true; return Clearing.step(npc,d,stage,tick,acquire) end
    return {kind="move",position={x=spot.x,y=spot.y}},{clearing=true,stage=stage,tally=tally,key=best.key,how="approach"}
  end
  if not throw(npc,item,e) then c.skip[best.key]=true; return Clearing.step(npc,d,stage,tick,acquire) end
  best.thrown=tick
  return nil,nil,"blasting"
end

Clearing.throw,Clearing.throw_range,Clearing.BLAST_WAIT=throw,throw_range,BLAST_WAIT   -- (a cliff under a planned piece: obstacles)

-- A clearing job's receipt: what it cleared, or what it could not reach (skipped for this plan).
function Clearing.on_receipt(npc,meta,receipt,tick)
  local c,tally=meta.stage and meta.stage.clear,meta.tally
  if not (c and tally) then return end
  if receipt.status=="complete" then
    if meta.how=="mine" then c.done=c.done+1; tally.done=tally.done+1 end
  elseif receipt.reason~="combat_interrupt" then   -- (a fight broke it off: tried again)
    if meta.key then c.skip[meta.key]=true end
    if meta.item then c.short[meta.item]=receipt.reason or true end
    c.failed=c.failed+1; tally.failed=tally.failed+1
  end
end

-- A witness that ground is clear: a clearing stage's, met once its clearing is over (what is left is
-- observed: a stage may end on its budget with more to do); or one with a box of its own (any plan's), met
-- when nothing it could clear now is left in the box.
function Clearing.witness(npc,w)
  if w.clear then
    w.observed=w.clear.left or 0
    return w.clear.over~=nil
  end
  if w.box and npc and npc.character and npc.character.valid then
    local survey=Clearing.survey(npc,w.box)
    w.observed=survey.mine+survey.blast
    return w.observed==0
  end
  return false
end

-- For the site export (world tiles: the factory plan's zones are): each zone it has worked on, with what
-- every surveyed box had left when it was last surveyed.
function Clearing.export(npc)
  local out={}
  for id,z in pairs(npc.clearing and npc.clearing.zones or {}) do
    local boxes,left={},0
    for _,b in pairs(z.boxes or {}) do
      boxes[#boxes+1]={box=b.box,mine=b.mine,blast=b.blast,later=b.later,fixed=b.fixed,outside=b.outside,
        short=b.short,explosives=b.explosives,tick=b.tick}
      left=left+b.mine+b.blast
    end
    table.sort(boxes,function(a,b) return box_key(a.box)<box_key(b.box) end)
    out[#out+1]={id=id,box=z.box,priority=z.priority,why=z.why,source=z.source,cleared=z.cleared,
      stages=z.stages,tick=z.tick,left=left,boxes=boxes}
  end
  table.sort(out,function(a,b) return a.id<b.id end)
  return {frame="world",zones=out}
end

Voice.TEMPLATES.clear_done=Voice.TEMPLATES.clear_done or {
  warm={"Cleared some ground for the next build.","That patch is clear now. Room to grow."},
  curt={"Ground cleared."},
  playful={"Fewer trees, more factory.","Rocks out, room in."}}

return Clearing
