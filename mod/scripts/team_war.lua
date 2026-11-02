-- The team world at war: what the biters do to each engineer's plot, for its planners and strategist.
-- Where they broke through its wall line (a wall, gate or turret of its own lost to them near its plot's
-- outer edge or its wall ring's sides), what each attack on its plot was made of, and how far evolution has gone. And where an
-- engineer on foot takes cover: biters here outrun it, so it falls back to the team's turrets, not away.
local TeamWorld=require("scripts.team_world")
local TeamWaves=require("scripts.team_waves")   -- (where a wave's units came from: their nest, D-20)
local TeamWar={}

local KEEP=108000          -- 30 game minutes of breaches and attacks are kept
local SEGMENT=16           -- losses on the same 16 tiles of an edge are one breach
local EDGE_BAND=24         -- a loss this close to an outer edge is on its wall line
local MAX_LAYERS=4
local ATTACK_GAP=1800      -- hits on a plot half a minute apart are separate attacks
local MAX_ATTACKS=8
local MAX_SEEN=400         -- units counted in one attack
local COVER_RADIUS=150     -- the team's turrets this close are worth falling back to
local COVER_SLACK=4        -- cover may stand this much nearer the threat than the engineer does
local GUARDED={wall=true,gate=true,["ammo-turret"]=true,["electric-turret"]=true,["fluid-turret"]=true}
-- Worn down, not broken through: pieces of the wall line on one stretch hurt below HEAVY of their health this
-- many times within PRESSURE_WINDOW are a breach there too, and get its repair (a layer more and turrets: the
-- user, 2026-10-25: more turrets or walls where it is constantly hit and damaged heavily).
local HEAVY=0.5
local HEAVY_HITS=3
local PRESSURE_WINDOW=18000   -- 5 game minutes
local TURRET_TYPES={"ammo-turret","electric-turret","fluid-turret"}
-- The nests its threat level weighs: those its pollution reaches (they soak it up and send it back as attacks; a
-- player sees the cloud over them), and how far round home it knows no nest stands (what it looks over there:
-- BaseMemory.threats). Looked at again at most every NEST_EVERY ticks.
local NEST_LOOK=250
local CLOUD_REACH=640
local MAX_REACHED=8
local NEST_EVERY=600
-- The rows of the ring's wall line a crossing belt or pipe goes under it between, and its first layer's row
-- (perimeter.py: CROSS_OUT, CROSS_IN, WALL_OFFSET; Edge.row counts them in from the ring's box).
local CROSS_OUT,CROSS_IN,WALL_ROW=1,6,2
-- Unit sizes by the words mods name them with, biggest first; else by health.
local SIZES={"leviathan","behemoth","big","medium","small"}
local RANK={small=1,medium=2,big=3,behemoth=4,leviathan=5}

-- Set by agent.lua: what an engineer does when its wall line is broken (asks for a breach repair).
TeamWar.on_breach=nil

local function try(f) local ok,value=pcall(f); if ok then return value end end

local function war()
  local t=TeamWorld.state()
  t.war=t.war or {breaches={},attacks={}}
  t.war.pressure=t.war.pressure or {}
  t.war.worn=t.war.worn or {}       -- stretches of the wall line hurt hard or a piece lost behind others: damage, D-23
  t.war.ever=t.war.ever or {}       -- per engineer: every attack on its plot, kept for good (how many, first, last)
  t.war.nests=t.war.nests or {}
  return t.war
end

local function npc_of(force)
  local id=force and force.valid~=false and tonumber((force.name or ""):match("^npc%-faction%-(%d+)$"))
  return id and storage.npcs and storage.npcs[id]
end

local function within(box,p,margin)
  margin=margin or 0
  return p.x>=box[1]-margin and p.x<box[3]+margin and p.y>=box[2]-margin and p.y<box[4]+margin
end

-- The sides of the wall ring an engineer holds now (the newest one its defense plans keep), as wall edges: the
-- ring round its factory, moved out as the factory grows, at the plot's outer edges in the end.
function TeamWar.ring_edges(npc)
  local best
  for _,plan in ipairs(npc and npc.design_history or {}) do
    local k=plan.keep
    if k and k.ring and k.ring_gen and (not best or k.ring_gen>best.ring_gen) then best=k end
  end
  if not best then return {} end
  local out={}
  for _,name in ipairs({"north","east","south","west"}) do out[#out+1]={name=name,box=best.ring} end
  return out
end

-- The edge a position is on the wall line of (its plot's outer edges, a band of the yard it holds, or a side of
-- the engineer's wall ring), and the box that edge belongs to; or nil.
function TeamWar.edge_of(plot,p,npc)
  local best,best_d,best_box
  local lines=TeamWorld.wall_edges(plot)
  for _,w in ipairs(TeamWar.ring_edges(npc)) do lines[#lines+1]=w end
  for _,w in ipairs(lines) do
    local b=w.box
    if within(b,p) then
      local d=({north=p.y-b[2],south=b[4]-p.y,west=p.x-b[1],east=b[3]-p.x})[w.name]
      if d and d<=EDGE_BAND and (not best or d<best_d) then best,best_d,best_box=w.name,d,b end
    end
  end
  return best,best_box
end

-- Wall rows standing on an edge round `p` (the dying one not counted): its layers there.
local function layers_at(surface,force,b,edge,p,gone)
  local area
  if edge=="north" then area={{p.x-8,b[2]},{p.x+8,b[2]+EDGE_BAND}}
  elseif edge=="south" then area={{p.x-8,b[4]-EDGE_BAND},{p.x+8,b[4]}}
  elseif edge=="west" then area={{b[1],p.y-8},{b[1]+EDGE_BAND,p.y+8}}
  else area={{b[3]-EDGE_BAND,p.y-8},{b[3],p.y+8}} end
  local rows,n={},0
  for _,w in ipairs(surface.find_entities_filtered{area=area,force=force,type="wall"}) do
    if w.valid and w~=gone then
      local across=(edge=="north" or edge=="south") and math.floor(w.position.y) or math.floor(w.position.x)
      if not rows[across] then rows[across]=true; n=n+1 end
    end
  end
  return math.min(MAX_LAYERS,n)
end

local function prune(list,tick)
  for k,v in pairs(list) do if tick-(v.tick or v.last or 0)>KEEP then list[k]=nil end end
end

-- The engineer, the edge and its box, and the stretch's key of a wall, gate or turret of an engineer's own on its
-- wall line hurt by the biters; else nil.
local function on_line(event)
  if not TeamWorld.active() then return end
  local e=event.entity
  if not (e and e.valid and GUARDED[e.type]) then return end
  if not (event.force and event.force.valid and event.force.name=="enemy") then return end
  local npc=npc_of(e.force)
  local plot=npc and TeamWorld.plot("npc-"..npc.id)
  local edge,box
  if plot then edge,box=TeamWar.edge_of(plot,e.position,npc) end
  if not edge then return end
  local along=(edge=="north" or edge=="south") and e.position.x or e.position.y
  return npc,edge,box,npc.id..":"..edge..":"..math.floor(along/SEGMENT)
end

local function breach(npc,e,edge,box,key,tick,gone,damaged)
  local w=war()
  local b=w.breaches[key] or {npc_id=npc.id,edge=edge,losses=0}
  b.x,b.y,b.tick,b.lost=e.position.x,e.position.y,tick,e.name
  b.losses=b.losses+(damaged and 0 or 1)
  b.damaged=damaged or nil
  -- (a hole in the line, D-23: kept while the breach is, a turret lost there after does not close it; Codex's review)
  b.hole=b.hole or (not damaged and gone and (gone.type=="wall" or gone.type=="gate")) or nil
  if b.hole and gone and (gone.type=="wall" or gone.type=="gate") then
    b.hole_piece,b.hole_tick=gone.name,tick
    -- (where its column runs across the band: closed once a wall or gate stands there again, TeamWar.breaches)
    local p,col=gone.position,(edge=="north" or edge=="south") and math.floor(gone.position.x) or math.floor(gone.position.y)
    local a
    if edge=="north" then a={{col,box[2]},{col+1,box[2]+EDGE_BAND}}
    elseif edge=="south" then a={{col,box[4]-EDGE_BAND},{col+1,box[4]}}
    elseif edge=="west" then a={{box[1],col},{box[1]+EDGE_BAND,col+1}}
    else a={{box[3]-EDGE_BAND,col},{box[3],col+1}} end
    b.column,b.surface=a,gone.surface.index
  end
  b.layers=layers_at(e.surface,e.force,box,edge,e.position,gone)
  w.breaches[key]=b
  prune(w.breaches,tick)
  if TeamWar.on_breach then TeamWar.on_breach(npc,b) end
end

-- Whether losing `e` (a wall or gate on an edge's band) opens the wall line: no wall or gate of its force left standing
-- across the band in its column (the line's tile along the edge). One lost while another layer still stands behind or
-- before it is damage, not a hole (the user, D-23: "walls get damaged from attacks but don't need repair right away
-- unless a hole forms and biters break into the perimeter"). The hook Worker's repair timing reads.
function TeamWar.hole(e,edge,box,gone)
  if not (e and e.valid) then return false end
  local p,b=e.position,box
  local col=(edge=="north" or edge=="south") and math.floor(p.x) or math.floor(p.y)
  local area
  if edge=="north" then area={{col,b[2]},{col+1,b[2]+EDGE_BAND}}
  elseif edge=="south" then area={{col,b[4]-EDGE_BAND},{col+1,b[4]}}
  elseif edge=="west" then area={{b[1],col},{b[1]+EDGE_BAND,col+1}}
  else area={{b[3]-EDGE_BAND,col},{b[3],col+1}} end
  for _,w in ipairs(e.surface.find_entities_filtered{area=area,force=e.force,type={"wall","gate"}}) do
    if w.valid and w~=(gone or e) then return false end
  end
  return true
end

-- Damage on the wall line (a stretch hurt hard again and again, or a piece lost with the line still closed): kept,
-- for the turrets the stretch wants (hits) and its walls' repair when there is time (Worker); not a breach.
local function wear(npc,e,edge,key,tick)
  local w=war()
  local s=w.worn[key] or {npc_id=npc.id,edge=edge,hits=0}
  s.x,s.y,s.tick=e.position.x,e.position.y,tick
  s.hits=s.hits+1
  w.worn[key]=s
  prune(w.worn,tick)
end

-- A wall or gate of an engineer's own lost to the biters on its wall line where it opens the line, or a turret of its
-- own lost there: a breach (a hole is urgent: rebuilt and its turrets at once). A wall lost with the line still closed
-- is damage (D-23).
function TeamWar.on_entity_died(event)
  local npc,edge,box,key=on_line(event)
  if not npc then return end
  local e,tick=event.entity,event.tick or game.tick
  if (e.type=="wall" or e.type=="gate") and not TeamWar.hole(e,edge,box,e) then
    wear(npc,e,edge,key,tick)
    return
  end
  breach(npc,e,edge,box,key,tick,e,false)
end

-- One hurt below HEAVY of its health there (each time it goes below, not every bite after): HEAVY_HITS of them on
-- the same stretch within PRESSURE_WINDOW are a breach.
local function worn(event)
  local health=event.final_health
  if not (health and health>0 and event.final_damage_amount) then return end
  local e=event.entity
  local full=try(function() return e.max_health end) or try(function() return e.prototype.get_max_health() end)
  if not (full and full>0) then return end
  if not (health<HEAVY*full and health+event.final_damage_amount>=HEAVY*full) then return end
  local npc,edge,box,key=on_line(event)
  if not npc then return end
  local w,tick=war(),event.tick or game.tick
  local p=w.pressure[key]
  if not p or tick-p.first>PRESSURE_WINDOW then p={first=tick,hits=0}; w.pressure[key]=p end
  p.hits=p.hits+1
  for k,v in pairs(w.pressure) do if tick-v.first>PRESSURE_WINDOW then w.pressure[k]=nil end end
  if p.hits>=HEAVY_HITS then
    w.pressure[key]=nil
    wear(npc,e,edge,key,tick)   -- (damage, not a breach: the user, D-23)
  end
end

local function size_of(unit)
  local name=unit.name
  for _,word in ipairs(SIZES) do if name:find(word,1,true) then return word end end
  local hp=try(function() return unit.prototype.get_max_health() end) or unit.max_health or 0
  return hp<100 and "small" or hp<500 and "medium" or hp<2000 and "big" or "behemoth"
end

local function spits(unit)
  return try(function() return unit.prototype.attack_parameters.type=="stream" end)==true
end

-- A unit of theirs hurting something of an engineer's in its plot: part of an attack on it.
function TeamWar.on_entity_damaged(event)
  worn(event)
  local cause=event.cause
  if not (cause and cause.valid and cause.type=="unit") or not TeamWorld.active() then return end
  if cause.force.name~="enemy" then return end
  local e=event.entity
  local npc=e and e.valid and npc_of(e.force)
  local plot=npc and TeamWorld.plot("npc-"..npc.id)
  if not TeamWorld.holds(plot,e.position) then return end   -- (its plot, or its bands of the yard)
  local w,tick=war(),event.tick or game.tick
  local list=w.attacks[npc.id] or {}
  w.attacks[npc.id]=list
  local a=list[#list]
  local new=not a or tick-a.last>ATTACK_GAP
  if new then
    -- (where it first hit: the side it came from; what it hit last may lie deep inside)
    a={first=tick,units={},seen={},count=0,biters=0,spitters=0,fx=e.position.x,fy=e.position.y}
    list[#list+1]=a
    while #list>MAX_ATTACKS do table.remove(list,1) end
  end
  local ever=w.ever[npc.id] or {count=0,first=a.first}   -- (a save from before this was kept counts from here)
  w.ever[npc.id]=ever
  if new or ever.count==0 then ever.count=ever.count+1 end
  a.last,a.x,a.y=tick,e.position.x,e.position.y
  ever.last=tick
  -- (the nest it came from, the first of its units that tells: a wave's, or its own spawner's; D-20)
  if not a.nx then
    local at=TeamWaves.nest_of(cause)
    if at then a.nx,a.ny=at.x,at.y end
  end
  local id=cause.unit_number
  if id and not a.seen[id] and a.count<MAX_SEEN then
    a.seen[id]=true; a.count=a.count+1
    a.units[cause.name]=(a.units[cause.name] or 0)+1
    if spits(cause) then a.spitters=a.spitters+1 else a.biters=a.biters+1 end
    local size=size_of(cause)
    if not a.biggest or RANK[size]>RANK[a.biggest] then a.biggest=size end
  end
end

function TeamWar.evolution(surface)
  local enemy=game.forces.enemy
  return try(function() return enemy.get_evolution_factor(surface) end)
    or try(function() return enemy.evolution_factor end) or 0
end

local function kind(a)
  if a.spitters>0 and a.biters>0 then return "mixed" end
  return a.spitters>0 and "spitters" or "biters"
end

-- A hole closed: a wall or gate of its own standing in its column again; said once, with how long it stood open (runs
-- time holes by it; D-24).
local function close_hole(npc,b,tick)
  if not (b.hole and b.column and b.surface) then return end
  local surface=game.surfaces[b.surface]
  local found=surface and try(function()
    return surface.count_entities_filtered{area=b.column,force=npc.force,type={"wall","gate"}}
  end) or 0
  if found>0 then
    b.hole,b.closed=nil,tick   -- (mended; a loss there after is a breach again)
    log("[npc-factions] npc="..npc.id.." breach closed after "..math.floor((tick-(b.hole_tick or b.tick))/60)
      .." s at "..tostring(b.edge).." "..math.floor(b.x)..","..math.floor(b.y))
  end
end

-- Breaches on an engineer's plot (newest first), in the site's local tiles round `origin`. A hole closed since is
-- left out: it is mended (D-24: one hole drew three repair plans in 25 s, each a turret more).
function TeamWar.breaches(npc,origin,tick)
  tick=tick or game.tick
  origin=origin or {x=0,y=0}
  local out={}
  for _,b in pairs(war().breaches) do
    if b.npc_id==npc.id then close_hole(npc,b,tick) end
    if b.npc_id==npc.id and tick-b.tick<=KEEP and not (b.closed and b.closed>=b.tick) then
      out[#out+1]={x=b.x-origin.x,y=b.y-origin.y,tick=b.tick,lost=b.lost,layers=b.layers,edge=b.edge,
        damaged=b.damaged,hole=b.hole,hole_piece=b.hole_piece,hole_tick=b.hole_tick}
    end
  end
  table.sort(out,function(a,b) return a.tick>b.tick end)
  return out
end

-- Its wall line's worn stretches lately (newest first), local tiles round `origin`: where it is hit hard but holds (D-23).
function TeamWar.worn_stretches(npc,origin,tick)
  tick=tick or game.tick
  origin=origin or {x=0,y=0}
  local out={}
  for _,s in pairs(war().worn or {}) do
    if s.npc_id==npc.id and tick-s.tick<=KEEP then
      out[#out+1]={x=s.x-origin.x,y=s.y-origin.y,tick=s.tick,hits=s.hits,edge=s.edge}
    end
  end
  table.sort(out,function(a,b) return a.tick>b.tick end)
  return out
end

-- When the newest attack on an engineer's plot began, or its newest breach (a tick), or nil.
function TeamWar.newest(npc)
  local w=war()
  local list=w.attacks[npc.id] or {}
  local newest=list[#list] and list[#list].first or nil
  for _,b in pairs(w.breaches) do
    if b.npc_id==npc.id and (not newest or b.tick>newest) then newest=b.tick end
  end
  return newest
end

-- The attacks on an engineer's plot lately (newest first) and evolution, in local tiles round `origin`.
function TeamWar.threat_board(npc,origin,surface,tick)
  tick=tick or game.tick
  origin=origin or {x=0,y=0}
  local attacks={}
  local list=war().attacks[npc.id] or {}
  for i=#list,1,-1 do
    local a=list[i]
    if tick-a.last<=KEEP then
      attacks[#attacks+1]={x=a.x-origin.x,y=a.y-origin.y,tick=a.last,units=a.units,kind=kind(a),
        biggest=a.biggest,first=a.first,count=a.count,
        from=a.fx and {x=a.fx-origin.x,y=a.fy-origin.y} or nil,
        nest=a.nx and {x=a.nx-origin.x,y=a.ny-origin.y} or nil}   -- (the nest it came from, where known)
    end
  end
  -- The waves on its teammates' plots lately (when and what): they come to every plot in turn (D-15).
  local team={}
  for id,others in pairs(war().attacks) do
    if id~=npc.id then
      for _,a in ipairs(others) do
        if tick-a.last<=KEEP then team[#team+1]={tick=a.last,units=a.units,count=a.count} end
      end
    end
  end
  table.sort(team,function(a,b) return a.tick>b.tick end)
  while #team>MAX_ATTACKS do table.remove(team) end
  local ever=war().ever[npc.id]
  return {evolution=TeamWar.evolution(surface),attacks=attacks,team=team,
    worn=TeamWar.worn_stretches(npc,origin,tick),   -- (stretches hit hard that hold: more turrets there, D-23)
    nests=TeamWar.nests(npc,origin,surface,tick),
    ever=ever and {count=ever.count,first=ever.first,last=ever.last} or nil,
    crossings=try(function() return TeamWar.crossings(npc,surface,tick) end)}
end

-- Its own belts and pipes running straight across the wall line of the ring it holds (the newest its defense plans
-- keep), by side, each where along the side it crosses and the underground that takes it under: holes the biters walk
-- through until a wall plan closes them (the planner counts those on the stretch of the side it walls). The same as
-- perimeter.crossings finds: a belt across the line one way, nothing beside it joining it
-- (a pipe, a belt loading onto it). Looked at again at most every NEST_EVERY ticks.
local ACROSS={north={[0]=true,[8]=true},south={[0]=true,[8]=true},west={[4]=true,[12]=true},east={[4]=true,[12]=true}}
local STEP_ALONG={north={[4]=1,[12]=-1},south={[4]=1,[12]=-1},west={[8]=1,[0]=-1},east={[8]=1,[0]=-1}}
function TeamWar.crossings(npc,surface,tick)
  tick=tick or game.tick
  local w=war()
  w.crossings=w.crossings or {}
  local seen=w.crossings[npc.id]
  if seen and tick-seen.tick<NEST_EVERY and tick>=seen.tick then return seen.out end
  local ring
  for _,plan in ipairs(npc.design_history or {}) do
    local k=plan.keep
    if k and k.ring and k.ring_gen and (not ring or k.ring_gen>ring.ring_gen) then ring=k end
  end
  local out={}
  -- The lines it walls: the ring's four sides; the ring the whole plot, the yard's outer edges it holds too (bands)
  local lines={}
  if ring then
    for _,side in ipairs({"north","south","west","east"}) do lines[#lines+1]={name=side,box=ring.ring} end
    local plot=TeamWorld.plot("npc-"..npc.id)
    local r,p=ring.ring,plot and plot.box
    if p and r[1]==p[1] and r[2]==p[2] and r[3]==p[3] and r[4]==p[4] then
      for _,band in ipairs(plot.bands or {}) do lines[#lines+1]={name=band.name,box=band.box} end
    end
  end
  for _,line in ipairs(lines) do
    local side=line.name
    local x0,y0,x1,y1=line.box[1],line.box[2],line.box[3],line.box[4]
    local function tile(_,a,o)
      if side=="north" then return a,y0+o elseif side=="south" then return a,y1-1-o
      elseif side=="west" then return x0+o,a else return x1-1-o,a end
    end
    do
      local across=side=="north" or side=="south"
      local ax,ay=tile(side,across and x0 or y0,WALL_ROW)
      local bx,by=tile(side,(across and x1 or y1)-1,WALL_ROW)
      for _,e in ipairs(surface.find_entities_filtered{area={{math.min(ax,bx),math.min(ay,by)},
          {math.max(ax,bx)+1,math.max(ay,by)+1}},force=npc.force,type={"transport-belt","pipe"}}) do
        local a=math.floor(across and e.position.x or e.position.y)
        local straight=e.type~="transport-belt" or ACROSS[side][e.direction]==true
        for o=CROSS_OUT,CROSS_IN do
          if not straight then break end
          local x,y=tile(side,a,o)
          local f=surface.find_entity(e.name,{x+0.5,y+0.5})
          if not (f and f.force==e.force and (e.type~="transport-belt" or f.direction==e.direction)) then
            straight=false
            break
          end
          for _,s in ipairs({-1,1}) do
            local bx2,by2=tile(side,a+s,o)
            local kinds=e.type=="pipe" and {"pipe","pipe-to-ground"} or {"transport-belt","underground-belt"}
            for _,n in ipairs(surface.find_entities_filtered{area={{bx2,by2},{bx2+1,by2+1}},force=e.force,type=kinds}) do
              if e.type=="pipe" or STEP_ALONG[side][n.direction]==-s then straight=false end
            end
          end
        end
        if straight then
          local under=e.type=="pipe" and "pipe-to-ground" or (e.name:gsub("transport%-belt","underground-belt"))
          out[side]=out[side] or {}
          out[side][#out[side]+1]={along=a,under=under}
        end
      end
    end
  end
  w.crossings[npc.id]={tick=tick,out=out}
  return out
end

-- The nests its pollution reaches (in local tiles round `origin`, nearest home first, with the pollution where each
-- stands) and `free`: the tiles round home within which no nest stands as far as it knows; or nil with no home.
function TeamWar.nests(npc,origin,surface,tick)
  tick=tick or game.tick
  origin=origin or {x=0,y=0}
  local c=npc.character
  local home=npc.home or (c and c.valid and c.position)
  if not home then return nil end
  local w=war()
  local seen=w.nests[npc.id]
  if not seen or tick-seen.tick>=NEST_EVERY or tick<seen.tick or seen.hx~=home.x or seen.hy~=home.y
      or seen.surface~=surface.index then   -- (a new home: looked at afresh)
    seen={tick=tick,reached={},free=NEST_LOOK,hx=home.x,hy=home.y,surface=surface.index}
    for _,e in ipairs(surface.find_entities_filtered{force="enemy",type="unit-spawner",position=home,
        radius=CLOUD_REACH}) do
      local p=e.position
      local d=math.sqrt((p.x-home.x)^2+(p.y-home.y)^2)
      if d<seen.free then seen.free=d end
      local soaked=try(function() return surface.get_pollution(p) end) or 0
      if soaked>=1 then seen.reached[#seen.reached+1]={x=p.x,y=p.y,p=math.floor(soaked+0.5),d=d} end
    end
    table.sort(seen.reached,function(a,b) return a.d<b.d end)
    while #seen.reached>MAX_REACHED do table.remove(seen.reached) end
    seen.free=math.floor(seen.free)
    w.nests[npc.id]=seen
  end
  local reached={}
  for i,n in ipairs(seen.reached) do reached[i]={x=n.x-origin.x,y=n.y-origin.y,pollution=n.p} end
  return {reached=reached,free=seen.free}
end

-- A short view for its strategist: evolution, its plot's latest attack and breaches.
function TeamWar.threat(npc,tick,surface)
  local board=TeamWar.threat_board(npc,nil,surface,tick)
  local breaches=TeamWar.breaches(npc,nil,tick)
  local last=board.attacks[1]
  local out={evolution=math.floor(board.evolution*100)/100,attacks_lately=#board.attacks,breaches=#breaches}
  if last then
    local n=0
    for _,c in pairs(last.units) do n=n+c end
    out.last_attack={seconds_ago=math.floor((tick-last.tick)/60),units=n,kind=last.kind,biggest=last.biggest,
      x=math.floor(last.x),y=math.floor(last.y)}
  end
  local b=breaches[1]
  if b then
    out.newest_breach={edge=b.edge,x=math.floor(b.x),y=math.floor(b.y),layers=b.layers,lost=b.lost,
      seconds_ago=math.floor((tick-b.tick)/60)}
  end
  return out
end

-- The team's nearest loaded turret an engineer can fall back to from `threat` (its own or a teammate's,
-- the players' too), or nil: one not further toward the threat than the engineer stands.
function TeamWar.cover(npc,threat)
  local c=npc.character
  if not (c and c.valid) then return nil end
  local forces={c.force}
  for _,f in pairs(game.forces) do if f~=c.force and c.force.get_friend(f) then forces[#forces+1]=f end end
  local best,best_d
  local here=c.position
  -- (cover is no nearer the threat than it stands now, give or take a few tiles: never a run at them)
  local reach=math.max(0,math.sqrt((here.x-threat.x)^2+(here.y-threat.y)^2)-COVER_SLACK)
  for _,t in ipairs(c.surface.find_entities_filtered{position=here,radius=COVER_RADIUS,force=forces,
      type=TURRET_TYPES}) do
    local loaded
    if t.type=="ammo-turret" then
      local inv=t.get_inventory(defines.inventory.turret_ammo)
      loaded=inv~=nil and not inv.is_empty()
    elseif t.type=="electric-turret" then loaded=(t.energy or 0)>0
    elseif t.type=="fluid-turret" then   -- a flamethrower with nothing in its pipe is no cover
      local fluid=try(function() return t.fluids_count>0 and t.get_fluid(1) or nil end)
      loaded=fluid~=nil and (fluid.amount or 0)>0
    else loaded=false end                 -- (artillery cannot hit what is close)
    local behind=(t.position.x-threat.x)^2+(t.position.y-threat.y)^2>=reach*reach
    local d=(t.position.x-here.x)^2+(t.position.y-here.y)^2
    if t.valid and loaded and behind and (not best or d<best_d) then best,best_d=t,d end
  end
  return best
end

return TeamWar
