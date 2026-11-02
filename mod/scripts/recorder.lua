-- The flight recorder for test runs: where every engineer goes, what gets built and lost, the
-- biters about, deaths, research, chat and the tools the strategist ran, written as JSON lines to
-- script-output/npc-record/<name>.jsonl (server only) for tools/replay.py to play back.
-- It only reads the game: nothing an engineer does depends on it.
--   /npc-record start {"name":"run","every":60,"scan":600} | stop | status   (RCON or admins)
local Chronicle=require("scripts.chronicle")

local Recorder={}

local VERSION=1
local BLOCK=64        -- terrain is kept in 64x64 blocks round wherever engineers go...
local CELL=2          -- ...as one letter per 2x2 cell
local TREE_CELL=8     -- and a tree density digit per 8x8 cell
local BLOCKS_PER_SAMPLE=2
local ENEMY_SIGHT=40  -- biters this close to an engineer or its home are recorded
local NEST_SIGHT=128  -- nests and worms this close
local STATS=3600
local ORE={["iron-ore"]="i",["copper-ore"]="c",["coal"]="k",["stone"]="s",["crude-oil"]="o",["uranium-ore"]="u"}
local PRODUCTS={"iron-plate","copper-plate","steel-plate","electronic-circuit","automation-science-pack",
  "logistic-science-pack","military-science-pack","chemical-science-pack","rocket-part"}
-- Not buildings: bodies, vehicles and robots move (engineers in vehicles are in the position samples).
local SKIP={character=true,car=true,["spider-vehicle"]=true,locomotive=true,["cargo-wagon"]=true,
  ["fluid-wagon"]=true,["artillery-wagon"]=true,["construction-robot"]=true,["logistic-robot"]=true,
  ["combat-robot"]=true,["entity-ghost"]=true,["tile-ghost"]=true,["item-request-proxy"]=true,
  ["deconstructible-tile-proxy"]=true,["item-entity"]=true,corpse=true,["character-corpse"]=true,
  projectile=true,stream=true,fire=true,sticker=true,["cargo-pod"]=true,["rocket-silo-rocket"]=true,
  ["rocket-silo-rocket-shadow"]=true,["highlight-box"]=true,["speech-bubble"]=true,unit=true}

local function round(v,places)
  local m=10^(places or 1)
  return math.floor(v*m+0.5)/m
end

local function write(r,line)
  helpers.write_file(r.file,helpers.table_to_json(line).."\n",true,0)
  r.lines=r.lines+1
end

local function text(v,limit)
  if v==nil then return nil end
  if type(v)~="string" then
    local ok,json=pcall(helpers.table_to_json,v)
    v=ok and json or tostring(v)
  end
  return v:sub(1,limit or 200)
end

local function npc_of_force(force)
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    if npc and npc.force==force then return npc end
  end
end

local function force_index(r,force)
  local name=force.name
  local i=r.forces[name]
  if i then return i end
  r.nforces=r.nforces+1; i=r.nforces; r.forces[name]=i
  local npc=npc_of_force(force)
  local c=force.color
  write(r,{k="force",i=i,name=name,npc=npc and npc.id or nil,color=c and {round(c.r,2),round(c.g,2),round(c.b,2)} or nil})
  return i
end

local function proto_index(r,entity)
  local name=entity.name
  local i=r.protos[name]
  if i then return i end
  r.nprotos=r.nprotos+1; i=r.nprotos; r.protos[name]=i
  local p=entity.prototype
  write(r,{k="proto",i=i,name=name,type=entity.type,w=p.tile_width,h=p.tile_height})
  return i
end

local function settings_now()
  local out={global={},startup={}}
  for name,s in pairs(settings.global) do if name:find("^npc%-factions") then out.global[name]=s.value end end
  for name,s in pairs(settings.startup) do if name:find("^npc%-factions") then out.startup[name]=s.value end end
  return out
end

local function world_now(surface)
  local out={}
  pcall(function()
    local gen=surface.map_gen_settings
    out.seed=gen.seed
    out.starting_points=#(gen.starting_points or {})
    out.peaceful=gen.peaceful_mode
    out.no_enemies=gen.no_enemies_mode
    local enemy=gen.autoplace_controls and gen.autoplace_controls["enemy-base"]
    out.enemy_bases=enemy and {frequency=enemy.frequency,size=enemy.size} or nil
    out.cliffs=gen.cliff_settings and gen.cliff_settings.richness
  end)
  pcall(function()
    local m=game.map_settings
    out.enemy_expansion=m.enemy_expansion.enabled
    out.enemy_evolution=m.enemy_evolution.enabled
    out.pollution=m.pollution.enabled
  end)
  pcall(function() out.evolution=round(game.forces.enemy.get_evolution_factor(surface),3) end)
  pcall(function() out.research_cost=game.difficulty_settings.technology_price_multiplier end)
  return out
end

local function traits(npc)
  local p=npc.personality
  if type(p)~="table" or type(p.traits)~="table" then return nil end
  local out={}
  for i,name in ipairs(p.traits) do out[i]=name end
  return out
end

-- Terrain: one 64x64 block, once, when the land is there.
local function capture_block(r,surface,bx,by)
  local x0,y0=bx*BLOCK,by*BLOCK
  for cx=x0/32,x0/32+BLOCK/32-1 do
    for cy=y0/32,y0/32+BLOCK/32-1 do
      if not surface.is_chunk_generated({cx,cy}) then return false end
    end
  end
  local n=BLOCK/CELL
  local grid={}
  local area={{x0,y0},{x0+BLOCK,y0+BLOCK}}
  for _,e in pairs(surface.find_entities_filtered{area=area,type="resource"}) do
    local p=e.position
    local i=math.floor((p.y-y0)/CELL)*n+math.floor((p.x-x0)/CELL)+1
    if i>=1 and i<=n*n then grid[i]=ORE[e.name] or "x" end
  end
  for _,tile in pairs(surface.find_tiles_filtered{area=area,collision_mask="water_tile"}) do
    local p=tile.position
    local i=math.floor((p.y-y0)/CELL)*n+math.floor((p.x-x0)/CELL)+1
    if i>=1 and i<=n*n then grid[i]="w" end
  end
  local cells={}
  for i=1,n*n do cells[i]=grid[i] or "." end
  local m=BLOCK/TREE_CELL
  local trees={}
  for _,e in pairs(surface.find_entities_filtered{area=area,type="tree"}) do
    local p=e.position
    local i=math.floor((p.y-y0)/TREE_CELL)*m+math.floor((p.x-x0)/TREE_CELL)+1
    if i>=1 and i<=m*m then trees[i]=(trees[i] or 0)+1 end
  end
  local density={}
  for i=1,m*m do density[i]=tostring(math.min(9,math.ceil((trees[i] or 0)/3))) end
  write(r,{k="terrain",bx=bx,by=by,g=table.concat(cells),tr=table.concat(density)})
  return true
end

local function want_blocks(r,x,y,radius)
  local bx,by=math.floor(x/BLOCK),math.floor(y/BLOCK)
  for dx=-radius,radius do
    for dy=-radius,radius do
      local key=(bx+dx)..","..(by+dy)
      if not r.blocks[key] then r.blocks[key]="queued"; r.queue[#r.queue+1]={bx+dx,by+dy} end
    end
  end
end

local function terrain_step(r,surface)
  local done=0
  local tries=math.min(#r.queue,BLOCKS_PER_SAMPLE*4)
  while done<BLOCKS_PER_SAMPLE and tries>0 and #r.queue>0 do
    tries=tries-1
    local b=table.remove(r.queue,1)
    if capture_block(r,surface,b[1],b[2]) then r.blocks[b[1]..","..b[2]]=true; done=done+1
    else r.queue[#r.queue+1]=b end   -- not generated yet: later
  end
end

-- What an engineer is about, written whenever any of it changes.
local function state_of(npc)
  local a=npc.agent
  local doing=a and a.agenda and a.agenda[1]
  local d=npc.design
  return {phase=text(npc.phase,80) or "",
    doing=doing and ((doing.tool or "?")..(doing.reason and (": "..doing.reason) or "")):sub(1,120) or "",
    plan=d and ((d.purpose or "?").." "..tostring(d.status or "")):sub(1,80) or "",
    foe=npc.foe and text(npc.foe.name,40) or ""}
end

local function enemies_near(surface,position,out)
  for _,u in pairs(surface.find_entities_filtered{position=position,radius=ENEMY_SIGHT,force="enemy",type="unit",limit=60}) do
    local p=u.position
    out[#out+1]=math.floor(p.x+0.5); out[#out+1]=math.floor(p.y+0.5)
  end
end

local function sample(r,tick)
  local surface=game.surfaces[r.surface]
  local actors,enemies={},{}
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    if npc then
      local c=npc.character
      if not r.npcs[id] then
        r.npcs[id]=true
        force_index(r,npc.force)
        local color=type(npc.color)=="table" and npc.color.r and {round(npc.color.r,2),round(npc.color.g,2),round(npc.color.b,2)} or nil
        write(r,{t=tick,k="npc",id=id,name=npc.name,force=npc.force.name,home=npc.home and {x=round(npc.home.x),y=round(npc.home.y)} or nil,
          traits=traits(npc),warlord=npc.warlord and true or nil,color=color})
        if npc.home then want_blocks(r,npc.home.x,npc.home.y,2) end
      end
      if c and c.valid then
        local p=c.position
        local v=c.vehicle
        actors[#actors+1]={id,round(p.x),round(p.y),round(c.get_health_ratio(),2),v and v.valid and v.name or 0}
        if c.surface.index==r.surface then
          want_blocks(r,p.x,p.y,1)
          enemies_near(surface,p,enemies)
          if npc.home and (p.x-npc.home.x)^2+(p.y-npc.home.y)^2>ENEMY_SIGHT^2 then enemies_near(surface,npc.home,enemies) end
        end
        local now,was=state_of(npc),r.state[id] or {}
        local changed,any={t=tick,k="state",id=id},false
        for key,value in pairs(now) do if was[key]~=value then changed[key]=value; any=true end end
        if any then write(r,changed); r.state[id]=now end
      elseif not r.dead[id] then
        r.dead[id]=true
        write(r,{t=tick,k="gone",id=id})
      end
    end
  end
  local players={}
  for _,player in pairs(game.connected_players) do
    if player.surface.index==r.surface then
      local p=player.position
      players[#players+1]={player.name,round(p.x),round(p.y)}
      if player.character and player.character.valid then want_blocks(r,p.x,p.y,1) end
    end
  end
  write(r,{t=tick,k="a",a=actors,p=#players>0 and players or nil,e=#enemies>0 and enemies or nil})
  terrain_step(r,surface)
end

-- Buildings: one force a step, all of them each round; only what was added or removed is written.
local function scan_force(r,tick,surface,force)
  local fi=force_index(r,force)
  local known=r.known[force.name] or {}
  local now,added={},{}
  for _,e in pairs(surface.find_entities_filtered{force=force}) do
    local name=e.name
    local skip=r.skip[name]
    if skip==nil then skip=SKIP[e.type] or false; r.skip[name]=skip end
    if not skip then
      local p=e.position
      local key=e.unit_number or (name.."@"..p.x..","..p.y)
      now[key]=true
      if not known[key] then added[#added+1]={key,proto_index(r,e),round(p.x,2),round(p.y,2),e.direction} end
    end
  end
  local removed={}
  for key in pairs(known) do if not now[key] then removed[#removed+1]=key end end
  r.known[force.name]=now
  if #added>0 or #removed>0 then
    write(r,{t=tick,k="b",f=fi,add=#added>0 and added or nil,del=#removed>0 and removed or nil})
  end
end

-- Nests and worms near engineers and their homes; one gone from a place looked at was destroyed.
local function scan_nests(r,tick,surface)
  local enemy=game.forces.enemy
  local fi=force_index(r,enemy)
  local known=r.nests
  local seen,looked,added={}, {}, {}
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    local c=npc and npc.character
    if c and c.valid and c.surface.index==r.surface then
      looked[#looked+1]=c.position
      if npc.home then looked[#looked+1]=npc.home end
    end
  end
  for _,at in ipairs(looked) do
    for _,e in pairs(surface.find_entities_filtered{position=at,radius=NEST_SIGHT,force=enemy,type={"unit-spawner","turret"}}) do
      local key=e.unit_number
      if key and not seen[key] then
        seen[key]=true
        if not known[key] then
          local p=e.position
          known[key]={round(p.x),round(p.y)}
          added[#added+1]={key,proto_index(r,e),round(p.x,2),round(p.y,2),e.direction}
        end
      end
    end
  end
  local removed={}
  for key,p in pairs(known) do
    if not seen[key] then
      for _,at in ipairs(looked) do
        if (p[1]-at.x)^2+(p[2]-at.y)^2<(NEST_SIGHT-8)^2 then removed[#removed+1]=key; known[key]=nil; break end
      end
    end
  end
  if #added>0 or #removed>0 then
    write(r,{t=tick,k="b",f=fi,add=#added>0 and added or nil,del=#removed>0 and removed or nil})
  end
end

local function scan_step(r,tick)
  local surface=game.surfaces[r.surface]
  if #r.round==0 then
    local seen={}
    for id=1,10 do
      local npc=storage.npcs and storage.npcs[id]
      if npc and npc.force and npc.force.valid and not seen[npc.force.name] then
        seen[npc.force.name]=true; r.round[#r.round+1]=npc.force.name
      end
    end
    for _,player in pairs(game.players) do
      if not seen[player.force.name] then seen[player.force.name]=true; r.round[#r.round+1]=player.force.name end
    end
    if not seen.player then r.round[#r.round+1]="player" end
    r.round[#r.round+1]="~nests"
    r.step=math.max(30,math.floor(r.scan/#r.round))
  end
  local name=table.remove(r.round,1)
  if name=="~nests" then scan_nests(r,tick,surface)
  elseif game.forces[name] then scan_force(r,tick,surface,game.forces[name]) end
  r.next_scan=tick+r.step
end

local function stats(r,tick)
  local surface=game.surfaces[r.surface]
  local rows,seen={},{}
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    local force=npc and npc.force
    if force and force.valid and not seen[force.name] then
      seen[force.name]=true
      local made={}
      pcall(function()
        local flow=force.get_item_production_statistics(surface)
        for _,item in ipairs(PRODUCTS) do
          local n=flow.get_input_count(item)
          if n and n>0 then made[item]=n end
        end
      end)
      local techs=0
      for _,tech in pairs(force.technologies) do
        if tech.researched and tech.enabled and tech.research_unit_count and tech.research_unit_count>0 then techs=techs+1 end
      end
      local now=force.current_research
      local buildings=0
      for _ in pairs(r.known[force.name] or {}) do buildings=buildings+1 end
      rows[#rows+1]={f=force_index(r,force),id=id,made=made,techs=techs,research=now and now.name or nil,
        progress=now and round(force.research_progress,2) or nil,kills=r.kills[force.name],buildings=buildings}
    end
  end
  local evolution
  pcall(function() evolution=round(game.forces.enemy.get_evolution_factor(surface),3) end)
  write(r,{t=tick,k="stats",s=rows,evo=evolution})
end

-- A recorder bug must never end a test: it logs, writes what went wrong and stops recording.
local function fail(r,problem)
  log("[npc-factions] recorder stopped: "..tostring(problem))
  pcall(write,r,{t=game.tick,k="error",error=tostring(problem):sub(1,400)})
  r.active=false
end

local function step(r,tick)
  if tick%r.every==0 then sample(r,tick) end
  if tick>=r.next_scan then scan_step(r,tick) end
  if tick%STATS==0 then stats(r,tick) end
end

function Recorder.tick(tick)
  local r=storage.recorder
  if not (r and r.active) then return end
  local ok,problem=pcall(step,r,tick)
  if not ok then fail(r,problem) end
end

function Recorder.start(opts)
  opts=type(opts)=="table" and opts or {}
  local name=type(opts.name)=="string" and opts.name:gsub("[^%w%-_]","_"):sub(1,64) or "run"
  local every=math.max(10,math.min(600,math.floor(tonumber(opts.every) or 60)))
  local scan=math.max(60,math.min(36000,math.floor(tonumber(opts.scan) or 600)))
  local surface=game.surfaces[1]
  local r={active=true,file="npc-record/"..name..".jsonl",name=name,started=game.tick,every=every,scan=scan,
    surface=surface.index,lines=0,forces={},nforces=0,protos={},nprotos=0,skip={},known={},nests={},
    npcs={},dead={},state={},blocks={},queue={},round={},step=scan,next_scan=game.tick,kills={}}
  storage.recorder=r
  local mods={}
  for mod,version in pairs(script.active_mods) do mods[mod]=version end
  helpers.write_file(r.file,"",false,0)   -- a new recording replaces an old one of the same name
  write(r,{t=game.tick,k="start",version=VERSION,every=every,scan=scan,block=BLOCK,cell=CELL,tree_cell=TREE_CELL,
    mods=mods,speed=game.speed,multiplayer=game.is_multiplayer(),settings=settings_now(),world=world_now(surface),
    test=storage.test and storage.test.mode or nil})
  for _,player in pairs(game.players) do
    if player.character and player.character.valid then want_blocks(r,player.position.x,player.position.y,1) end
  end
  return {ok=true,file=r.file,tick=game.tick}
end

function Recorder.stop()
  local r=storage.recorder
  if not (r and r.active) then return {ok=false,reason="not_recording"} end
  stats(r,game.tick)
  write(r,{t=game.tick,k="stop",settings=settings_now(),world=world_now(game.surfaces[r.surface])})
  r.active=false
  local out={ok=true,file=r.file,lines=r.lines,tick=game.tick}
  storage.recorder=nil
  return out
end

function Recorder.status()
  local r=storage.recorder
  return {ok=true,active=r and r.active or false,file=r and r.file or nil,lines=r and r.lines or 0,
    tick=game.tick,speed=game.speed,terrain_queue=r and #r.queue or 0}
end

-- Hooks the rest of the mod calls; each does nothing unless a recording is on.
local function on() local r=storage and storage.recorder; return r and r.active and r or nil end
local function hook(fn)
  return function(...)
    local r=on(); if not r then return end
    local ok,problem=pcall(fn,r,...)
    if not ok then fail(r,problem) end
  end
end

Recorder.say=hook(function(r,npc,to,message)
  if not npc then return end
  write(r,{t=game.tick,k="say",id=npc.id,to=type(to)=="string" and to:sub(1,40) or nil,text=text(message,400)})
end)

Recorder.task=hook(function(r,npc,it,status,reason)
  if not (npc and it) then return end
  write(r,{t=game.tick,k="task",id=npc.id,tool=it.tool,status=status,reason=text(reason,120),why=text(it.reason,120)})
end)

Recorder.chat=hook(function(r,event)
  if not event.player_index then return end
  local player=game.get_player(event.player_index)
  write(r,{t=game.tick,k="chat",player=player and player.name or "?",text=text(event.message,400)})
end)

Recorder.on_research_finished=hook(function(r,event)
  local tech=event.research
  if tech and tech.valid then
    write(r,{t=game.tick,k="research",f=force_index(r,tech.force),tech=tech.name,by_script=event.by_script or nil})
  end
end)

Recorder.on_rocket_launch_ordered=hook(function(r,event)
  local silo=event.rocket_silo
  if silo and silo.valid then write(r,{t=game.tick,k="rocket",f=force_index(r,silo.force),x=round(silo.position.x),y=round(silo.position.y)}) end
end)

Recorder.on_entity_died=hook(function(r,event)
  local e=event.entity
  if not (e and e.valid) then return end
  local by=event.force and event.force.valid and event.force or nil
  local t=e.type
  if t=="unit" then
    if by then
      local k=r.kills[by.name] or {}
      r.kills[by.name]=k
      k.biters=(k.biters or 0)+1
    end
    return
  end
  local p=e.position
  local cause=event.cause and event.cause.valid and event.cause.name or nil
  if t=="character" then
    local who
    for id=1,10 do local npc=storage.npcs and storage.npcs[id]; if npc and npc.character==e then who=id end end
    local player=e.player
    write(r,{t=game.tick,k="died",id=who,player=player and player.name or nil,x=round(p.x),y=round(p.y),
      by=by and by.name or nil,cause=cause})
  elseif t=="unit-spawner" or (t=="turret" and e.force.name=="enemy") then
    if by then
      local k=r.kills[by.name] or {}
      r.kills[by.name]=k
      k.nests=(k.nests or 0)+1
    end
    write(r,{t=game.tick,k="nest",name=e.name,x=round(p.x),y=round(p.y),by=by and by.name or nil})
  elseif not SKIP[t] then
    write(r,{t=game.tick,k="lost",f=force_index(r,e.force),name=e.name,x=round(p.x),y=round(p.y),
      by=by and by.name or nil,cause=cause})
  end
end)

-- The chronicle (diplomacy, trades, attacks, plans) as it happens.
Chronicle.subscribe(hook(function(r,event)
  if event.kind=="utterance" then return end
  write(r,{t=event.tick or game.tick,k="chron",kind=event.kind,actor=event.actor,target=event.target,
    payload=text(event.payload,240),source=event.source})
end))

function Recorder.register_commands()
  commands.add_command("npc-record","Test runs: record the game for tools/replay.py (start {json} | stop | status)",function(command)
    local player=command.player_index and game.get_player(command.player_index)
    if player and not player.admin then player.print("Only admins can record."); return end
    local p=command.parameter or "status"
    local ok,result=pcall(function()
      if p=="start" or p:sub(1,6)=="start " then
        local parsed,value=pcall(helpers.json_to_table,p:sub(7))
        return Recorder.start(parsed and value or {})
      elseif p=="stop" then return Recorder.stop() end
      return Recorder.status()
    end)
    if not ok then result={ok=false,reason=tostring(result):sub(1,300)} end
    if player then player.print(helpers.table_to_json(result))
    else rcon.print("NPC_RECORD_RESULT "..helpers.table_to_json(result)) end
  end)
end

return Recorder
