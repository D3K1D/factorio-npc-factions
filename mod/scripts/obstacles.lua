-- Something in the way of a planned piece, and what is done about it: one ladder for every plan's pieces.
--   1. clear what is natural or clearable (designed_start: items, trees, rocks and its own buildings before each try;
--      cliffs with a charge, water with landfill; biters wait for calm; the worker cuts through to a spot it
--      cannot walk to);
--   2. work around what is worth keeping: a line piece goes under it (underground belt, pipe-to-ground), a piece
--      that may stand a little aside does, and a wall piece on a cliff or water is not needed (the ground is the wall);
--   3. otherwise the piece is left out where its stage shows the block works without it (never a line's piece: a
--      belt, pipe or pole chain counts only whole), else the plan goes back to be planned again, told what was there;
--   4. every rung is recorded (d.obstacles, npc.obstacle_log, the log), and no piece is dropped without one.
-- (Run 60, 2026-10-27: a supply belt's piece on a tree it could not reach was left out and the line carried nothing.)
local TeamWorld=require("scripts.team_world")
local Holograms=require("scripts.holograms")

local Obstacles={}

-- A line counts only connected end to end: none of its pieces is left out.
Obstacles.LINE={["transport-belt"]=true,["underground-belt"]=true,splitter=true,pipe=true,["pipe-to-ground"]=true,
  ["electric-pole"]=true,["heat-pipe"]=true}
Obstacles.BARRIER={wall=true,gate=true}            -- what keeps biters out: a cliff or water there does it too
Obstacles.MOVABLE={["electric-pole"]=true,["ammo-turret"]=true,["electric-turret"]=true,["fluid-turret"]=true,
  radar=true,lamp=true}                             -- no neighbour needs them on one exact tile
Obstacles.SHIFT=2                                   -- tiles a movable piece may stand aside
Obstacles.KEPT=64                                   -- records kept per plan and per engineer
Obstacles.UNREACHABLE_FOR=36000                     -- ticks a spot it could not get to is kept off by the next plans
-- Failures that mean something is in the way (or the spot cannot be got to), not a missing item.
Obstacles.IN_THE_WAY={path_ended_outside_reach=true,no_path=true,placement_blocked_or_out_of_reach=true,
  placement_collision=true,no_reachable_build_stance=true,landfill_out_of_reach=true}

local VEC={[0]={0,-1},[4]={1,0},[8]={0,1},[12]={-1,0}}
local function try(f) local ok,v=pcall(f); if ok then return v end end
local function key(p) return string.format("%.1f,%.1f",p.x,p.y) end
local function type_of(entity) local p=prototypes.entity[entity.prototype]; return p and p.type end

-- What lies in `area`: items on the ground, trees and rocks (natural), its own buildings that are not the plan's
-- (own), cliffs, water tiles, biters, and what is kept (anyone else's building, anything it may not take), of what
-- collides with the piece `name` (ore under it, a corpse or smoke does not). `ours(e)`: the plan's own piece there.
local function layers_of(p) return try(function() return p.collision_mask.layers end) end
local function collides(e,mine)
  if not mine then return true end
  local theirs=layers_of(e.prototype)
  if not theirs then return false end
  for layer in pairs(theirs) do if mine[layer] then return true end end
  return false
end
Obstacles.TOUCH=0.05   -- a box that only meets the piece's edge is in its way (the game refuses it so)
local function meets(e,area)
  local b=try(function() return e.bounding_box end)
  local x0,y0=area[1][1] or area[1].x,area[1][2] or area[1].y
  local x1,y1=area[2][1] or area[2].x,area[2][2] or area[2].y
  local m=Obstacles.TOUCH
  return b~=nil and b.left_top.x-m<x1 and x0<b.right_bottom.x+m and b.left_top.y-m<y1 and y0<b.right_bottom.y+m
end
-- The game's own answer for a piece there, as a player's cursor gives it (the user, 2026-10-28: "use the blueprint
-- placement system like the vanilla game to check if something is blocking a placement and if its breakable or not"):
--   "free"       it goes down now (the manual check a player's hand makes);
--   "clearable"  only what a blueprint would clear is in the way (trees, rocks, items on the ground, its own
--                buildings: what vanilla marks for deconstruction): broken first, then it goes down;
--   "blocked"    something no blueprint clears (water, anyone else's building, the map's edge): named, worked round,
--                left out or planned again, never tried as is.
-- nil when the game cannot be asked (a prototype it does not know).
function Obstacles.verdict(surface,name,position,direction,force)
  if not (surface and name and prototypes.entity[name] and position) then return nil end
  local spec={name=name,position=position,direction=direction,force=force,
    build_check_type=defines.build_check_type.manual}
  local ok,free=pcall(function() return surface.can_place_entity(spec) end)
  if not ok then return nil end
  if free then return "free" end
  spec.build_check_type,spec.forced=defines.build_check_type.blueprint_ghost,true
  local ok2,clear=pcall(function() return surface.can_place_entity(spec) end)
  if not ok2 then return nil end
  return clear and "clearable" or "blocked"
end

-- Whether its own character stands on `area` (touching counts): the worker steps aside from it, nothing else to do.
function Obstacles.on_spot(c,area)
  return c~=nil and c.valid and meets(c,area)
end

-- Something in `area` it may break by hand, as a blueprint marks it (minable, not a character, not ore under it, not
-- the plan's own piece, nobody else's building): the nearest first. nil when nothing there is its to break.
-- (only what collides with the piece, and what the worker can mine: a natural thing that yields an item, or its own
-- building picked up; a minable thing yielding nothing would send it on a mine job it cannot finish)
local function yields(e)
  local props=try(function() return e.prototype.mineable_properties end)
  for _,p in ipairs(props and props.minable and props.products or {}) do if p.type=="item" then return true end end
  return false
end
-- (a ghost is never in the way: a plan's hologram stands where its pieces go, holograms.lua)
Obstacles.GHOSTS={["entity-ghost"]=true,["tile-ghost"]=true}

function Obstacles.breakable(npc,area,ours,name)
  local c=npc.character
  local piece=name and prototypes.entity[name]
  local mine=piece and layers_of(piece)
  local best,best_d
  for _,e in ipairs(try(function() return c.surface.find_entities_filtered{area=area} end) or {}) do
    local force=e.valid and e.force and e.force.name
    local natural=e.valid and (e.type=="tree" or e.type=="simple-entity")
    if e.valid and e~=c and e.minable and not Obstacles.GHOSTS[e.type] and collides(e,mine) and not (ours and ours(e))
        and ((force=="neutral" and natural and yields(e)) or (e.force==c.force and e.type~="character")) then
      local d=(e.position.x-c.position.x)^2+(e.position.y-c.position.y)^2
      if not best or d<best_d then best,best_d=e,d end
    end
  end
  return best
end

-- `at` ({position, direction}, optional): where the piece goes, to ask the game itself when nothing is seen.
function Obstacles.survey(npc,area,ours,name,at)
  local c=npc.character
  local surface=c.surface
  local piece=name and prototypes.entity[name]
  local mine=piece and layers_of(piece)
  local s={items={},natural={},own={},cliffs={},water={},water_names={},deep=0,enemies={},kept={},
    self=false,characters={},tiles={}}
  -- Characters are found by their own boxes (one standing on the edge is in the way too): itself, which steps aside,
  -- and anyone else's, who moves on. Skipped, its own stood unseen on a lab's spot: "nothing_seen", handed back.
  if c and c.valid and meets(c,area) then s.self=true end
  local wide={{(area[1][1] or area[1].x)-1,(area[1][2] or area[1].y)-1},{(area[2][1] or area[2].x)+1,(area[2][2] or area[2].y)+1}}
  for _,e in ipairs(try(function() return surface.find_entities_filtered{area=wide,type="character"} end) or {}) do
    if e.valid and e~=c and e.type=="character" and meets(e,area) then s.characters[#s.characters+1]=e end
  end
  for _,e in ipairs(try(function() return surface.find_entities_filtered{area=area} end) or {}) do
    if e.valid and e~=c and e.type~="character" and not Obstacles.GHOSTS[e.type] then
      local t,force=e.type,e.force and e.force.name
      if t=="item-entity" then s.items[#s.items+1]=e
      elseif t=="cliff" then s.cliffs[#s.cliffs+1]=e
      elseif force=="enemy" then s.enemies[#s.enemies+1]=e
      elseif (t=="tree" or t=="simple-entity" or t=="fish") and e.minable then s.natural[#s.natural+1]=e
      elseif e.force==c.force and (ours and ours(e)) then   -- (its own piece of this plan: nothing in the way)
      elseif e.force==c.force and e.minable then s.own[#s.own+1]=e
      elseif t~="resource" and t~="corpse" and t~="character-corpse" and t~="entity-ghost" and t~="tile-ghost"
          and collides(e,mine) then
        s.kept[#s.kept+1]=e
      end
    end
  end
  for _,t in ipairs(try(function() return surface.find_tiles_filtered{area=area,collision_mask="water_tile"} end) or {}) do
    s.water[#s.water+1]=t.position
    s.water_names[#s.water_names+1]=try(function() return t.name end)
    -- (deep water stops a biter as a wall does; shallow water it wades through)
    if try(function() return t.collides_with("player") end) then s.deep=s.deep+1 end
  end
  -- Any other ground the piece cannot stand on (out of the map, a modded tile, lava...): named, never cleared.
  local layers={}
  for layer in pairs(mine or {}) do if layer~="water_tile" then layers[#layers+1]=layer end end
  if #layers>0 then
    local seen={}
    for _,t in ipairs(try(function() return surface.find_tiles_filtered{area=area,collision_mask=layers} end) or {}) do
      local n=try(function() return t.name end) or "?"
      if not seen[n] and not try(function() return t.collides_with("water_tile") end) then
        seen[n]=true; s.tiles[#s.tiles+1]=n
      end
    end
  end
  -- Nothing found: the game is asked whether the piece fits there now. It does (what was there has gone: a passer-by,
  -- a moment's overlap): try again. It does not: a rule nothing here names (where it may stand, a mod's own rule).
  if at and name and #s.items+#s.natural+#s.own+#s.cliffs+#s.water+#s.enemies+#s.kept+#s.characters+#s.tiles==0
      and not s.self then
    local v=Obstacles.verdict(surface,name,at.position,at.direction,c.force)
    if v=="free" or v=="clearable" then s.clear_now=true elseif v=="blocked" then s.refused=true end
  end
  return s
end

-- One line for the log and a re-plan: what was there ("tree:2 cliff:1 water:3 kept:assembling-machine").
function Obstacles.what(s,reason)
  local out={}
  for _,k in ipairs({"items","natural","own","cliffs","water","enemies"}) do
    if #s[k]>0 then out[#out+1]=k..":"..#s[k] end
  end
  local kept={}
  for _,e in ipairs(s.kept) do kept[#kept+1]=e.type end
  if #kept>0 then out[#out+1]="kept:"..table.concat(kept,",") end
  if s.self then out[#out+1]="self" end
  if #(s.characters or {})>0 then out[#out+1]="characters:"..#s.characters end
  if #(s.tiles or {})>0 then out[#out+1]="tiles:"..table.concat(s.tiles,",") end
  if s.clear_now then out[#out+1]="clear_now" end
  if s.refused then out[#out+1]="refused" end
  if #out==0 then out[1]=(reason=="path_ended_outside_reach" or reason=="no_path") and "unreachable" or "nothing_seen" end
  return table.concat(out," ")
end

-- A piece that goes on land: water under it needs filling (not one built on water, as an offshore pump is).
function Obstacles.on_land(name)
  local p=prototypes.entity[name]
  -- (rules of where it may stand, as an offshore pump's; the game gives every other building an empty list: a pipe's
  -- read as rules, its pond pieces were never filled, the proving ground 2026-10-27)
  local rules=try(function() return p.tile_buildability_rules end)
  if not p or (rules and #rules>0) then return false end
  local layers=try(function() return p.collision_mask.layers end)
  return layers~=nil and layers.water_tile==true
end

-- The item that fills water (it may only be put down where the ground is not land) and the tile it makes, for water
-- tiles named `names`: landfill itself first; one kept to some tiles (tile_condition: Space Age's landfill lists the
-- waters it goes on, a mod's soil fewer) only where every tile is one of them. Taken as "goes on none", landfill was
-- refused and a pipe's pond pieces were never tried (the proving ground, 2026-10-27).
local fills
function Obstacles.landfill(names)
  if fills==nil then
    fills={}
    local items={}
    for name in pairs(prototypes.item) do items[#items+1]=name end
    table.sort(items,function(a,b) if a=="landfill" or b=="landfill" then return a=="landfill" end return a<b end)
    for _,name in ipairs(items) do
      local r=try(function() return prototypes.item[name].place_as_tile_result end)
      local layers=r and try(function() return r.condition.layers end)
      if layers and layers.ground_tile and r.result then
        local only
        for _,t in ipairs(try(function() return r.tile_condition end) or {}) do
          only=only or {}
          only[type(t)=="string" and t or t.name]=true
        end
        fills[#fills+1]={item=name,tile=r.result.name,only=only}
      end
    end
  end
  for _,f in ipairs(fills) do
    local fits=true
    for _,n in ipairs(f.only and names or {}) do if not f.only[n] then fits=false end end
    if fits then return f end
  end
end

-- The trees and rocks that give `item` when mined, by name (a mod's rocks may give ore too: Space Age's volcanic rocks
-- and stromatolites give iron ore, so what counts is what stands near, not what some rock somewhere gives).
local naturals
function Obstacles.natural_names(item)
  if not naturals then
    naturals={}
    for name,p in pairs(try(function()
        return prototypes.get_entity_filtered{{filter="type",type={"tree","simple-entity"}}}
      end) or {}) do
      for _,q in ipairs(try(function() return p.mineable_properties.products end) or {}) do
        if q.name then
          naturals[q.name]=naturals[q.name] or {}
          table.insert(naturals[q.name],name)
        end
      end
    end
    for _,list in pairs(naturals) do table.sort(list) end
  end
  return naturals[item] or {}
end

-- The resources whose mining gives the item, as natural_names (DesignedStart.hand_plan).
local resources
function Obstacles.resource_names(item)
  if not resources then
    resources={}
    for name,p in pairs(try(function()
        return prototypes.get_entity_filtered{{filter="type",type="resource"}}
      end) or {}) do
      for _,q in ipairs(try(function() return p.mineable_properties.products end) or {}) do
        if q.name then
          resources[q.name]=resources[q.name] or {}
          table.insert(resources[q.name],name)
        end
      end
    end
    for _,list in pairs(resources) do table.sort(list) end
  end
  return resources[item] or {}
end

-- The charge that blasts a cliff, or nil.
function Obstacles.charge(cliff)
  return try(function() return cliff.prototype.cliff_explosive_prototype end)
end

-- Recorded: what stood in the way of a piece and the rung taken. `entity` may be nil (the worker's cut-through).
function Obstacles.record(npc,d,entity,rung,what,tick,detail)
  local r={tick=tick,purpose=d and d.purpose,id=entity and entity.id,prototype=entity and entity.prototype,
    rung=rung,what=what,detail=detail}
  local function keep(list) list[#list+1]=r; while #list>Obstacles.KEPT do table.remove(list,1) end end
  if d then d.obstacles=d.obstacles or {}; keep(d.obstacles) end
  npc.obstacle_log=npc.obstacle_log or {}; keep(npc.obstacle_log)
  if entity then entity.rungs=entity.rungs or {}; entity.rungs[rung]=(entity.rungs[rung] or 0)+1 end
  log("[npc-factions] npc="..tostring(npc.id).." in the way of "..tostring(r.prototype or "its work").." "
    ..tostring(r.id or "").." ("..tostring(what).."): "..rung..(detail and (" "..detail) or ""))
  return r
end

-- Something kept (a teammate's or a player's building) that biters cannot walk through: in a wall's line it holds them
-- as the wall would (they must break it first), so the wall goes on past it; handed back, one chest in the line threw
-- the whole wall plan away (the proving ground, 2026-10-27).
function Obstacles.stops_biters(kept)
  if #kept==0 then return false end
  for _,e in ipairs(kept) do
    local layers=layers_of(e.prototype)
    if not (layers and layers.player) then return false end
  end
  return true
end

-- Not to be built, with why (bridged under, covered by a cliff): its stage goes on and its line stays whole.
function Obstacles.skip(entity,rung,tick)
  entity.skipped={rung=rung,tick=tick}
  pcall(Holograms.settle,entity)   -- (not to be built: no hologram of it left)
end

local function plan_index(d)
  local at={}
  for _,id in ipairs(d.plan.order) do
    local e=d.plan.entities[id]
    if not (e.skipped or e.left_out) and e.world then at[key(e.world)]=e end
  end
  return function(p) return at[key(p)] end
end

-- The underground that carries a line piece past what is in the way: its prototype and how far it reaches.
local function underground_for(p)
  if p.type=="transport-belt" then
    local u=try(function() return p.related_underground_belt end)
    if u then return u,try(function() return u.max_underground_distance end) or 5 end
  elseif p.type=="pipe" then
    local u=prototypes.entity["pipe-to-ground"]
    if not u then
      for _,q in pairs(try(function() return prototypes.get_entity_filtered{{filter="type",type="pipe-to-ground"}} end) or {}) do
        u=q; break
      end
    end
    if u then
      local far
      for _,fb in ipairs(try(function() return u.fluidbox_prototypes end) or {}) do
        for _,pc in ipairs(try(function() return fb.pipe_connections end) or {}) do
          if pc.connection_type=="underground" then far=pc.max_underground_distance end
        end
      end
      return u,far or 10
    end
  end
end

local function item_of(p)
  local i=p.items_to_place_this and p.items_to_place_this[1]
  return i and (i.name or i[1])
end

-- Work around for a line piece: an underground pair from the nearest piece of the line behind what is in the way to
-- the nearest one past it, the pieces between not built (true, detail). `blocked(position)` says a tile of the line
-- is in the way; `can_get(item)`, that the engineer holds or can make the underground; `box_of(name,pos,dir)`.
function Obstacles.bridge(npc,d,entity,blocked,can_get,box_of)
  local p=prototypes.entity[entity.prototype]
  local u,far
  if p then u,far=underground_for(p) end
  if not u then return nil end
  local item=item_of(u)
  if not (item and can_get(item)) then return nil,"no_underground:"..tostring(item) end
  local at=plan_index(d)
  local watched={}   -- (a piece a stage's witness watches is never the one not built)
  for _,stage in ipairs(d.plan.stages or {}) do
    for _,w in ipairs(stage.witnesses or {}) do if w.entity_id then watched[w.entity_id]=true end end
  end
  -- (only pieces of the stage being built or later: a stage done is not visited again to put its ends down)
  local open={}
  for i,stage in ipairs(d.plan.stages or {}) do
    if i>=(d.stage_index or 1) then for _,id in ipairs(stage.entity_ids or {}) do open[id]=true end end
  end
  local belt=p.type=="transport-belt"
  local function same(e) return e.prototype==entity.prototype and (not belt or e.direction==entity.direction) end
  local axes=belt and {entity.direction or 0} or {0,4}
  for _,dir in ipairs(axes) do
    local v=VEC[dir]
    if v then
      local function tile(k) return {x=entity.world.x+v[1]*k,y=entity.world.y+v[2]*k} end
      -- the nearest piece of the line on one side not in the way, within reach of an underground
      local function line_end(sign)
        for k=1,far do
          local e=at(tile(sign*k))
          if not (e and same(e) and open[e.id]) then return nil end
          -- (an underground's entrance takes a belt from behind only: what feeds it must come straight on)
          local feeder=sign<0 and belt and at(tile(sign*(k+1)))
          if not blocked(tile(sign*k)) and not (feeder and not same(feeder)) then return e,k end
        end
      end
      local back,kb=line_end(-1)
      local front,kf=line_end(1)
      if back and front and kb+kf<=far then
        -- nothing joins the line from the side where it goes under (an inserter, a side-loaded belt, a pipe branch)
        local between,side_ok={},true
        for k=-kb,kf do
          local t=tile(k)
          if k>-kb and k<kf then
            between[#between+1]=at(t)
            if watched[between[#between].id] or not open[between[#between].id] then side_ok=false end
          end
          for _,s in ipairs({{v[2],v[1]},{-v[2],-v[1]}}) do
            local n=at({x=t.x+s[1],y=t.y+s[2]})
            local nt=n and type_of(n)
            local into=n and VEC[n.direction or -1]
            into=into and into[1]==-s[1] and into[2]==-s[2]
            -- (an arm beside it, or a belt or pipe that joins it from the side; a lane running beside it does not)
            if nt=="inserter" or nt=="loader" or nt=="loader-1x1" or ((nt=="transport-belt" or nt=="underground-belt"
                or nt=="splitter") and into) or (nt and not belt and Obstacles.LINE[nt]) then side_ok=false end
          end
        end
        -- (what stands beside the line and is no piece of the plan: an arm, a belt joining it, a pipe or machine with
        -- fluid beside a pipe; a teammate's or an older plan's, it would be cut off as well)
        if side_ok then
          local surface=npc.character.surface
          for k=-kb,kf do
            local t=tile(k)
            for _,s in ipairs({{v[2],v[1]},{-v[2],-v[1]}}) do
              local q={x=t.x+s[1],y=t.y+s[2]}
              for _,e in ipairs(try(function()
                  return surface.find_entities_filtered{area={{q.x-0.4,q.y-0.4},{q.x+0.4,q.y+0.4}}}
                end) or {}) do
                local et=e.valid and e.type
                local into=VEC[e.direction or -1]
                into=into and into[1]==-s[1] and into[2]==-s[2]
                if et=="inserter" or et=="loader" or et=="loader-1x1"
                    or (belt and (et=="transport-belt" or et=="underground-belt" or et=="splitter") and into)
                    or (not belt and (try(function() return #e.fluidbox end) or 0)>0) then
                  side_ok=false
                end
              end
            end
          end
        end
        -- (a pipe-to-ground joins nothing at its sides: no branch, no machine beside either end or what goes under)
        if side_ok and not belt then
          for k=-kb,kf do
            local t=tile(k)
            for _,s in ipairs({{v[2],v[1]},{-v[2],-v[1]}}) do
              local q={x=t.x+s[1],y=t.y+s[2]}
              for _,id in ipairs(d.plan.order) do
                local e=d.plan.entities[id]
                if e.world and not (e.skipped or e.left_out) and prototypes.entity[e.prototype] then
                  local b=box_of(e.prototype,e.world,e.direction)
                  if q.x>b[1][1] and q.x<b[2][1] and q.y>b[1][2] and q.y<b[2][2] then side_ok=false end
                end
              end
            end
          end
        end
        if side_ok then
          local function rewrite(e,end_type,facing)
            if e.entity and e.entity.valid then e.entity=nil end   -- (a belt already down: picked up as not the plan's)
            e.prototype,e.item=u.name,item
            if belt then e.belt_to_ground_type=end_type; e.direction=dir
            else e.direction=facing end
          end
          -- (a pipe-to-ground faces its pipe above ground: the entrance back toward the line, the exit on along it)
          rewrite(back,"input",(dir+8)%16)
          rewrite(front,"output",dir)
          for _,e in ipairs(between) do
            if e.entity and e.entity.valid then
              npc.design_salvage=npc.design_salvage or {}
              table.insert(npc.design_salvage,e.entity)
              e.entity=nil
            end
            Obstacles.skip(e,"bridged",game.tick)
          end
          back.failures,front.failures=0,0
          return true,string.format("%s %s..%s",u.name,back.id,front.id)
        end
      end
    end
  end
  return nil,"no_line_room"
end

local function overlaps(a,b) return a[1][1]<b[2][1] and b[1][1]<a[2][1] and a[1][2]<b[2][2] and b[1][2]<a[2][2] end

-- Work around for a piece that may stand aside: the nearest free spot within SHIFT tiles that crosses no other piece
-- of the plan and, for a pole, keeps every wire and every machine it powered (true, detail).
function Obstacles.shift(npc,d,entity,box_of)
  local p=prototypes.entity[entity.prototype]
  if not (p and Obstacles.MOVABLE[p.type]) then return nil end
  local c=npc.character
  local others={}
  for _,id in ipairs(d.plan.order) do
    local e=d.plan.entities[id]
    if e~=entity and e.world and not (e.skipped or e.left_out) then others[#others+1]=e end
  end
  local w0=entity.world
  local turret=p.type:find("turret")~=nil
  if turret then   -- (one fed by an inserter must stand where the arm drops)
    for _,e in ipairs(others) do
      if type_of(e)=="inserter" and math.max(math.abs(e.world.x-w0.x),math.abs(e.world.y-w0.y))<=3 then return nil end
    end
  end
  local pole=p.type=="electric-pole"
  local wire=pole and (try(function() return p.get_max_wire_distance() end) or try(function() return p.max_wire_distance end) or 7.5)
  local reach=pole and (try(function() return p.get_supply_area_distance() end) or try(function() return p.supply_area_distance end) or 2.5)
  local function dist(a,b) return math.sqrt((a.x-b.x)^2+(a.y-b.y)^2) end
  local linked,powered={},{}
  if pole then
    local area={{w0.x-reach,w0.y-reach},{w0.x+reach,w0.y+reach}}
    for _,e in ipairs(others) do
      local q=prototypes.entity[e.prototype]
      if q and q.type=="electric-pole" and dist(e.world,w0)<=wire then linked[#linked+1]=e end
      if q and try(function() return q.electric_energy_source_prototype end) and overlaps(box_of(e.prototype,e.world,e.direction),area) then
        powered[#powered+1]=e
      end
    end
  end
  local spots={}
  for dx=-Obstacles.SHIFT,Obstacles.SHIFT do
    for dy=-Obstacles.SHIFT,Obstacles.SHIFT do
      if dx~=0 or dy~=0 then spots[#spots+1]={x=w0.x+dx,y=w0.y+dy,d=dx*dx+dy*dy} end
    end
  end
  table.sort(spots,function(a,b) if a.d~=b.d then return a.d<b.d end; if a.x~=b.x then return a.x<b.x end; return a.y<b.y end)
  for _,s in ipairs(spots) do
    local pos={x=s.x,y=s.y}
    local box=box_of(entity.prototype,pos,entity.direction)
    local ok=true
    for _,e in ipairs(others) do
      if overlaps(box,box_of(e.prototype,e.world,e.direction)) then ok=false; break end
    end
    if ok and pole then
      for _,e in ipairs(linked) do if dist(e.world,pos)>wire then ok=false; break end end
      local area={{pos.x-reach,pos.y-reach},{pos.x+reach,pos.y+reach}}
      for _,e in ipairs(powered) do
        if ok and not overlaps(box_of(e.prototype,e.world,e.direction),area) then ok=false end
      end
    end
    if ok and TeamWorld.active() and not TeamWorld.may_build(npc,box) then ok=false end
    if ok and try(function()
        return c.surface.can_place_entity{name=entity.prototype,position=pos,direction=entity.direction,force=c.force,
          build_check_type=defines.build_check_type.manual}
      end) then
      entity.moved_from=entity.moved_from or w0
      entity.world=pos
      entity.failures=0
      pcall(Holograms.moved,npc,entity)   -- (its hologram goes with it)
      return true,string.format("to %.1f,%.1f",pos.x,pos.y)
    end
  end
  return nil,"no_spot"
end

-- Ground handed back as unbuildable, still so, in local tiles round `origin` (inclusive tile boxes): for the planners,
-- so a layout is put elsewhere rather than offered on the same spot again (a saved lab layout was refused at the same
-- spot three times, the proving ground 2026-10-28). What stood there and is gone frees it; a spot it could not get to
-- is kept off for a while; water, cliffs, tiles and the game's own refusals stay.
function Obstacles.avoid(npc,origin,tick)
  local c=npc.character
  local surface=c and c.valid and c.surface
  local out={}
  for _,u in ipairs(npc.unbuildable or {}) do
    local keep
    if u.until_tick then keep=(tick or game.tick)<u.until_tick
    elseif surface and u.kind=="water" then   -- (filled since, landfill: free again)
      local ok,n=pcall(function() return surface.count_tiles_filtered{area={{u.x0,u.y0},{u.x1+1,u.y1+1}},
        collision_mask="water_tile",limit=1} end)
      keep=not ok or n>0
    elseif surface and not (u.kind=="tile" or u.kind=="refused" or u.kind=="unreachable") then
      -- (a kind of building: kept off while one still stands there)
      local ok,n=pcall(function() return surface.count_entities_filtered{area={{u.x0,u.y0},{u.x1+1,u.y1+1}},
        type=u.kind,limit=1} end)
      keep=not ok or n>0
    else keep=true end
    if keep then
      out[#out+1]={x0=u.x0-origin.x,y0=u.y0-origin.y,x1=u.x1-origin.x,y1=u.y1-origin.y,kind=u.kind}
    end
  end
  return #out>0 and out or nil
end

-- The ladder for a piece that failed for good with something in the way. `h`: box(name,pos,dir),
-- leave_out(d,entity,tick), hand_back(reason), can_get(item), ours(e). Returns the rung taken.
function Obstacles.resolve(npc,d,entity,receipt,tick,h)
  local p=entity.world
  local box=p and (prototypes.entity[entity.prototype] and h.box(entity.prototype,p,entity.direction)
    or {{p.x-0.5,p.y-0.5},{p.x+0.5,p.y+0.5}})
  -- (the game is asked whether it fits there only after a refusal to place: a spot it could not get to fits, and is
  -- still out of reach)
  local placing=receipt.reason=="placement_blocked_or_out_of_reach" or receipt.reason=="placement_collision"
  local s=box and Obstacles.survey(npc,box,h.ours,entity.prototype,placing and {position=p,direction=entity.direction} or nil)
    or {items={},natural={},own={},cliffs={},water={},water_names={},deep=0,enemies={},kept={},characters={},tiles={}}
  local what=Obstacles.what(s,receipt.reason)
  if receipt.blocked_by and #s.cliffs+#s.kept==0 then what=what.." "..receipt.blocked_by end
  local t=type_of(entity)
  local rungs=entity.rungs or {}
  if not box then rungs={clear=1,bridged=1,shifted=1} end   -- (nowhere known: only left out or planned again)
  -- 1. what it can clear now and has not cleared yet: tried again (designed_start clears it first)
  local fill=Obstacles.landfill(s.water_names)
  -- (itself on the spot steps aside; anyone else moves on; a spot that fits now was a moment's block)
  local fixable=(#s.items+#s.natural+#s.own+#s.characters)>0 or s.self or s.clear_now==true
  local wall=Obstacles.BARRIER[t]
  if #s.water>0 and fill and h.can_get(fill.item) and Obstacles.on_land(entity.prototype) and not (wall and s.deep>0) then
    fixable=true
  end
  if not wall then
    for _,cliff in ipairs(s.cliffs) do
      local item=Obstacles.charge(cliff)
      if item and h.can_get(item) then fixable=true end
    end
  end
  if fixable and #s.kept==0 and (rungs.clear or 0)<1 then
    entity.failures=0
    Obstacles.record(npc,d,entity,"clear",what,tick)
    return "clear"
  end
  -- 2. work around what is worth keeping
  if wall and (#s.cliffs>0 or s.deep>0 or Obstacles.stops_biters(s.kept)) then
    Obstacles.skip(entity,"covered",tick)
    Obstacles.record(npc,d,entity,"covered",what,tick,"what stands there keeps biters out")
    return "covered"
  end
  -- (a tile of the line is in the way: the failed piece's own, or one with what it keeps or cannot clear now)
  local function blocked(pos)
    if pos.x>box[1][1] and pos.x<box[2][1] and pos.y>box[1][2] and pos.y<box[2][2] then return true end
    local q=Obstacles.survey(npc,{{pos.x-0.4,pos.y-0.4},{pos.x+0.4,pos.y+0.4}},h.ours,entity.prototype)
    return #q.kept+#q.cliffs+#q.water>0
  end
  if (rungs.bridged or 0)<1 then
    local ok,detail=Obstacles.bridge(npc,d,entity,blocked,h.can_get,h.box)
    if ok then
      Obstacles.skip(entity,"bridged",tick)
      Obstacles.record(npc,d,entity,"bridged",what,tick,detail)
      return "bridged"
    end
    if detail then Obstacles.record(npc,d,entity,"no_bridge",what,tick,detail) end
  end
  if (rungs.shifted or 0)<1 then
    local ok,detail=Obstacles.shift(npc,d,entity,h.box)
    if ok then
      Obstacles.record(npc,d,entity,"shifted",what,tick,detail)
      return "shifted"
    end
  end
  -- 3. left out where its stage shows the block works without it (never a line's piece), else planned again
  if not Obstacles.LINE[t] and h.leave_out(d,entity,tick) then
    Obstacles.record(npc,d,entity,"left_out",what,tick)
    return "left_out"
  end
  Obstacles.record(npc,d,entity,"hand_back",what,tick)
  -- (where it was, for the plan made after to keep off: base_map's avoid list)
  if box then
    npc.unbuildable=npc.unbuildable or {}
    local kind=(#s.water>0 and "water") or (#s.cliffs>0 and "cliff") or (#s.kept>0 and s.kept[1].type)
      or (#s.tiles>0 and "tile") or (s.refused and "refused") or (#s.natural>0 and s.natural[1].type) or "unreachable"
    table.insert(npc.unbuildable,{x0=math.floor(box[1][1]),y0=math.floor(box[1][2]),x1=math.ceil(box[2][1])-1,
      y1=math.ceil(box[2][2])-1,kind=kind,tick=tick,
      until_tick=(kind=="unreachable" or kind=="tile" or kind=="refused") and tick+Obstacles.UNREACHABLE_FOR or nil})
    while #npc.unbuildable>64 do table.remove(npc.unbuildable,1) end
  end
  h.hand_back("in_the_way:"..tostring(entity.id)..":"..what:gsub(" ",","))
  return "hand_back"
end

return Obstacles
