-- The proving ground (tools/proving.py): short practice worlds. Each scenario sets up one situation in a few
-- game API calls on a flat practice map (an engineer just spawned on its plot), and scores it from the game:
--   /npc-proving setup {"scenario":NAME, ...params}   builds the situation; returns what it placed
--   /npc-proving score                                 the numbers so far, and whether the scenario is over
-- Server console / RCON only. Nothing ticks unless a scenario is set up.
local TeamWorld=require("scripts.team_world")
local Orbit=require("scripts.orbit")
local PlatformBuild=require("scripts.platform_build")
local Ghosts=require("scripts.ghosts")
local PlatformLaunch=require("scripts.platform_launch")
local TeamWar=require("scripts.team_war")
local DesignedStart=require("scripts.designed_start")
local Factory=require("scripts.factory")
local Settlements=require("scripts.settlements")
local Scouting=require("scripts.scouting")
local BaseMemory=require("scripts.base_memory")

local Proving={}

local function try(f) local ok,v=pcall(f); if ok then return v end end
local function state() return storage.proving end
local function npc_of(params) return storage.npcs and storage.npcs[tonumber(params.npc_id) or 1] end
local function minutes(ticks) return ticks and math.floor(ticks/36)/100 or nil end

local function plot_of(npc)
  return TeamWorld.active() and TeamWorld.plot("npc-"..npc.id) or nil
end

local function research(force,names)
  local done={}
  for _,name in ipairs(names or {}) do
    local t=force.technologies[name]
    if t and not t.researched then t.researched=true; done[#done+1]=name end
  end
  return done
end

local function give(npc,items)
  local given={}
  for name,count in pairs(items or {}) do
    if prototypes.item[name] and count>0 then given[name]=npc.character.insert{name=name,count=count} end
  end
  return given
end

-- A plan handed straight to the engineer, as if its planner had made it: entities in world tiles, stages as the
-- companion sends them. Buildings already standing there (its own) are adopted, the rest it builds.
-- The land between `a` and `b` generated now: biters sent from beyond the team area's generated ground found no path
-- across land not made yet, their group finished at once and they stood there (D-11: no wave ever reached the base).
local function land_between(surface,a,b)
  local steps=math.max(1,math.ceil(math.sqrt((a.x-b.x)^2+(a.y-b.y)^2)/32))
  for k=0,steps do
    surface.request_to_generate_chunks({x=a.x+(b.x-a.x)*k/steps,y=a.y+(b.y-a.y)*k/steps},2)
  end
  surface.force_generate_chunk_requests()
end

local function hand_plan(npc,purpose,entities,stages,source)
  local d=npc.design
  if not (d and d.origin) then return nil,"no_design" end
  if d.status=="building" then return nil,"engineer_busy_building" end   -- (never over a plan under way)
  local list={}
  for _,e in ipairs(entities) do
    local entry={id=e.id,prototype=e.prototype,direction=e.direction or 0,
      position={x=e.position.x-d.origin.x,y=e.position.y-d.origin.y}}
    for _,k in ipairs({"recipe","belt_to_ground_type","pickup_position","drop_position","input_priority",
        "output_priority","filter","filters","use_filters"}) do entry[k]=e[k] end
    list[#list+1]=entry
  end
  local rid="proving:"..game.tick
  local kept=DesignedStart.keep_request(npc,game.tick)   -- (the request it replaces, asked again after: W-27)
  local before={d.status,d.request_id,d.purpose,d.export_cache,d.rejections,d.last_rejection}
  d.status,d.request_id,d.purpose,d.export_cache="waiting",rid,purpose,nil
  local result=DesignedStart.submit({request_id=rid,plan={plan_id=purpose.."-"..game.tick,purpose=purpose,
    source=source or "proving",entities=list,stages=stages}})
  if not (result and result.accepted) then
    d.status,d.request_id,d.purpose,d.export_cache=before[1],before[2],before[3],before[4]
    d.rejections,d.last_rejection=before[5],before[6]   -- (the handed plan's rejection is not the request's: W-27)
    return nil,result and result.reason or "rejected"
  end
  if kept then npc.paused_design=kept end
  return d.plan
end

-- The plan as it stands: in hand, or done (moved to its history).
local function find_plan(npc,plan_id)
  local d=npc.design
  if d and d.plan and d.plan.plan_id==plan_id then return d.plan,d.status end
  for i=#(npc.design_history or {}),1,-1 do
    local p=npc.design_history[i]
    if p.plan_id==plan_id then return p,p.partial and "partial" or "complete" end
  end
end

-- Units of `kinds` ({name=weight}) round `at`, sent at `target`.
local function wave(surface,at,target,size,kinds)
  local names,total={},0
  for name,w in pairs(kinds) do
    if prototypes.entity[name] and w>0 then names[#names+1]={name=name,w=w}; total=total+w end
  end
  table.sort(names,function(a,b) return a.name<b.name end)
  if total==0 then return {} end
  local group=surface.create_unit_group{position=at,force="enemy"}
  local units={}
  for i=1,size do
    local pick,r=names[1].name,((i*7919)%1000)/1000*total
    for _,n in ipairs(names) do if r<n.w then pick=n.name; break end; r=r-n.w end
    local p=surface.find_non_colliding_position(pick,{x=at.x+(i%8)*2-8,y=at.y-math.floor(i/8)*2},16,0.5)
    local u=p and surface.create_entity{name=pick,position=p,force="enemy"}
    if u then group.add_member(u); units[#units+1]=u end
  end
  group.set_command{type=defines.command.attack_area,destination=target,radius=16,
    distraction=defines.distraction.by_enemy}
  return units
end

-- A rock and a tree as on Nauvis (big-rock, tree-01), else the first of their kind a mod set has.
function Proving.natural()
  local function pick(kind,prefer,want)
    local all=prototypes.get_entity_filtered{{filter="type",type=kind}}
    if all[prefer] then return prefer end
    local best
    for name,p in pairs(all) do if want(name,p) and (not best or name<best) then best=name end end
    return best
  end
  return pick("simple-entity","big-rock",function(name,p) return name:find("rock") and p.mineable_properties
      and p.mineable_properties.minable end),
    pick("tree","tree-01",function(name) return name:sub(1,5)=="tree-" end)
end

-- Rocks and trees round its home, the same every time (a fixed pseudo-random walk), off its starter patches.
function Proving.scatter(npc,rocks,trees,radius)
  local surface=npc.character.surface
  local home=npc.home or npc.character.position
  local rock,tree=Proving.natural()
  local n,seed={rock=0,tree=0},12345
  local function rnd() seed=(seed*1103515245+12345)%2147483648; return seed/2147483648 end
  for i=1,(rocks+trees)*4 do
    local kind=n.rock<rocks and (i%2==0 or n.tree>=trees) and "rock" or (n.tree<trees and "tree" or nil)
    if not kind then break end
    local a,r=rnd()*2*math.pi,12+rnd()*(radius-12)
    local p={x=home.x+math.cos(a)*r,y=home.y+math.sin(a)*r}
    local name=kind=="rock" and rock or tree
    if name and surface.count_entities_filtered{position=p,radius=3,limit=1}==0
        and surface.can_place_entity{name=name,position=p} then
      if surface.create_entity{name=name,position=p} then n[kind]=n[kind]+1 end
    end
  end
  return {rocks=n.rock,trees=n.tree,rock=rock,tree=tree}
end

-- Steam power standing by its home, handed to it as a finished plan (its upkeep feeds it): what a stage needs running
-- so the stage itself is what is tested (F-11). On the flat proving map with no water near home, a pond is made
-- on clear ground nearby. Returns the plan, or nil and why.
function Proving.steam_power(npc,surface,home)
  research(npc.force,{"steam-power","electronics","automation"})
  local made=DesignedStart.SUGGESTED["steam-power"].make(npc)
  if not made then
    for r=14,26,4 do
      for k=0,7 do
        local a=k*math.pi/4
        local x,y=math.floor(home.x+math.cos(a)*r),math.floor(home.y+math.sin(a)*r)
        local area={{x-10,y-10},{x+10,y+10}}
        if not made and surface.count_entities_filtered{area=area,force=npc.force}==0 then
          -- (its rocks and trees there cleared: the boiler and engine go beside the pond)
          for _,e in ipairs(surface.find_entities_filtered{area=area,type={"tree","simple-entity"}}) do e.destroy() end
          local tiles={}
          for dx=-3,2 do for dy=-3,2 do tiles[#tiles+1]={name="water",position={x+dx,y+dy}} end end
          surface.set_tiles(tiles,true)
          made=DesignedStart.SUGGESTED["steam-power"].make(npc)
        end
      end
    end
  end
  if not made then return nil,"no_water_for_power" end
  local plan,why=hand_plan(npc,"steam-power",made.entities,made.stages)
  if not plan then return nil,"power_not_handed:"..tostring(why) end
  -- (then put down, in the same tick: its pieces standing where the plan has them, adopted as it comes to each)
  for _,e in ipairs(made.entities) do
    local built=surface.create_entity{name=e.prototype,position=e.position,direction=e.direction,force=npc.force}
    if not built then return nil,"power_piece_not_placed:"..e.prototype end
    if built.type=="boiler" then built.insert{name="coal",count=50} end
  end
  return plan
end

---------------------------------------------------------------------------------------------------------------
-- The scenarios. Each: setup(npc,params,s) fills the scenario state s (returns a summary or nil,why);
-- tick(npc,s,tick) optional; score(npc,s,tick) the numbers, with done=true once there is nothing more to see.
local S={}

-- a. Walls under attack (NPC Defense, NPC Offense): a stretch of its plot's outer edge walled, gun turrets behind,
-- handed to it as its plan (it keeps them supplied); waves of a chosen size and kind at a chosen evolution.
S["wall-under-attack"]={
  setup=function(npc,params,s)
    local plot=plot_of(npc)
    if not plot then return nil,"no_plot" end
    local edge=params.edge or plot.outer[1]
    if not edge then return nil,"no_outer_edge" end
    local surface=npc.character.surface
    local b=plot.box
    local len=math.floor(params.wall_length or 64)
    local depth=math.floor(params.wall_depth or 2)
    local mx,my=math.floor((b[1]+b[3])/2),math.floor((b[2]+b[4])/2)
    local inward=({north={0,1},south={0,-1},west={1,0},east={-1,0}})[edge]
    local along=({north={1,0},south={1,0},west={0,1},east={0,1}})[edge]
    local line=({north={mx,b[2]+2},south={mx,b[4]-3},west={b[1]+2,my},east={b[3]-3,my}})[edge]
    local function at(a,i) return {x=line[1]+along[1]*a+inward[1]*i,y=line[2]+along[2]*a+inward[2]*i} end
    research(npc.force,params.research or {"military","gun-turret","stone-wall"})
    local entities,walls,turrets={},{},{}
    for a=-math.floor(len/2),math.floor(len/2)-1 do
      for i=0,depth-1 do
        local p=at(a,i); p.x,p.y=p.x+0.5,p.y+0.5
        local id="w"..a.."_"..i
        entities[#entities+1]={id=id,prototype="stone-wall",position=p}; walls[#walls+1]=id
      end
    end
    local n=math.floor(params.turrets or 4)
    for k=1,n do
      local a=math.floor(-len/2+(k-0.5)*len/n)
      local p=at(a,depth+3)
      local id="t"..k
      entities[#entities+1]={id=id,prototype=params.turret or "gun-turret",position={x=p.x,y=p.y}}; turrets[#turrets+1]=id
    end
    local ammo=params.ammo or "firearm-magazine"
    -- (placed now, adopted by its plan: the scenario starts with the defense standing; followed by reference, as
    -- the plan itself moves on once done)
    s.turrets,s.wall_list={},{}
    for _,e in ipairs(entities) do
      for _,old in ipairs(surface.find_entities_filtered{position=e.position,radius=1.5}) do
        if old.valid and old.type~="character" and old.type~="resource" and old.force~=npc.force then old.destroy() end
      end
      local made=surface.create_entity{name=e.prototype,position=e.position,force=npc.force}
      if made and made.type=="ammo-turret" and (params.ammo_each or 25)>0 then
        made.insert{name=ammo,count=params.ammo_each or 25}
      end
      if made then
        if made.type=="wall" then s.wall_list[#s.wall_list+1]=made else s.turrets[#s.turrets+1]=made end
      end
    end
    local supplies={}
    for _,id in ipairs(turrets) do supplies[#supplies+1]={entity_id=id,item=ammo,slot="ammo"} end
    local plan,why=hand_plan(npc,"proving-wall",entities,{{id="wall",depends_on={},entity_ids=walls},
      {id="turrets",depends_on={"wall"},entity_ids=turrets,manual_supplies=supplies}})
    if not plan then return nil,why end
    give(npc,params.give or {[ammo]=200,["stone-wall"]=50})
    game.forces.enemy.set_evolution_factor(params.evolution or 0.2,surface)
    local target=at(0,0)
    local out=math.floor(params.distance or 60)
    s.plan_id,s.edge,s.target=plan.plan_id,edge,{x=target.x,y=target.y}
    s.spawn={x=target.x-inward[1]*out,y=target.y-inward[2]*out}
    land_between(surface,s.spawn,s.target)
    s.waves,s.size=math.floor(params.waves or 3),math.floor(params.size or 20)
    s.kinds=params.kinds or {["small-biter"]=3,["medium-biter"]=1}
    s.every=math.floor(params.every or 3600)
    s.first,s.ready_wait=math.floor(params.first or 1800),math.floor(params.ready_wait or 10800)
    s.sent,s.units,s.spawned=0,{},0
    s.walls=#s.wall_list
    s.ammo_min,s.empty_samples,s.loaded_up=math.huge,0,0
    s.last_ammo={}
    s.quiet_for=math.floor(params.quiet or 1800)
    return {edge=edge,walls=#walls,turrets=#turrets,target=s.target,spawn=s.spawn}
  end,
  tick=function(npc,s,tick)
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    -- The waves come once its plan for the defense is done (adopted and owned, so it keeps it supplied), or after
    -- ready_wait at most (then noted: the defense was not its own yet).
    if not s.ready_tick then
      local _,status=find_plan(npc,s.plan_id)
      if status=="complete" or tick-s.start>=s.ready_wait then
        s.ready_tick,s.ready_complete=tick,status=="complete"
        s.next_wave=tick+s.first
      end
      return
    end
    if s.sent<s.waves and tick>=s.next_wave then
      for _,u in ipairs(wave(surface,s.spawn,s.target,s.size,s.kinds)) do s.units[#s.units+1]=u end
      s.sent,s.next_wave=s.sent+1,tick+s.every
      s.spawned=#s.units
      s.last_sent=tick
    end
    if tick%60==0 then
      local empty=false
      for i,t in ipairs(s.turrets) do
        if t.valid then
          local inv=t.get_inventory(defines.inventory.turret_ammo)
          local n=inv and inv.get_item_count() or 0
          s.ammo_min=math.min(s.ammo_min,n)
          if n==0 then empty=true end
          if s.last_ammo[i] and n>s.last_ammo[i] then s.loaded_up=s.loaded_up+(n-s.last_ammo[i]) end
          s.last_ammo[i]=n
        end
      end
      if empty then s.empty_samples=s.empty_samples+1 end   -- (seconds with any turret standing empty)
    end
  end,
  score=function(npc,s,tick)
    local alive_units=0
    for _,u in ipairs(s.units) do if u.valid then alive_units=alive_units+1 end end
    if alive_units>0 then s.calm_since=nil elseif s.sent>=s.waves then s.calm_since=s.calm_since or tick end
    local lost=0
    for _,t in ipairs(s.turrets) do if not t.valid then lost=lost+1 end end
    local walls_standing=0
    for _,w in ipairs(s.wall_list) do if w.valid then walls_standing=walls_standing+1 end end
    local breaches=0
    for _,br in ipairs(TeamWar.breaches(npc,nil,tick) or {}) do if br.tick>=s.start then breaches=breaches+1 end end
    local alive=npc.character~=nil and npc.character.valid
    local _,plan_status=find_plan(npc,s.plan_id)
    plan_status=plan_status or s.plan_status
    s.phases=s.phases or {}
    local phase=tostring(npc.phase):match("^[^:]+:[^:]+") or tostring(npc.phase)
    s.phases[phase]=(s.phases[phase] or 0)+1
    return {defense_ready_minute=s.ready_tick and minutes(s.ready_tick-s.start),defense_owned=s.ready_complete,
      plan_status=plan_status,plan_reason=s.plan_reason,phases_seen=s.phases,waves_sent=s.sent,waves=s.waves,units_sent=s.spawned,units_alive=alive_units,
      units_killed=s.spawned-alive_units,breaches=breaches,turrets_lost=lost,turrets=#s.turrets,
      walls_lost=s.walls-walls_standing,walls=s.walls,ammo_min=s.ammo_min==math.huge and 0 or s.ammo_min,
      turret_empty_seconds=s.empty_samples,units_short=s.waves*s.size-s.spawned,ammo_loaded_by_engineer=s.loaded_up,engineer_alive=alive,
      done=not alive or (s.calm_since~=nil and tick-s.calm_since>=s.quiet_for)}
  end}

-- b. Build through obstacles (NPC Worker, NPC Factory): a belt, a pipe and a wall line planned straight through
-- trees, rocks, a cliff, a pond and a teammate's building. Every piece must be built or handed back with a reason
-- (scripts/obstacles.lua's ladder), and the supply lines must run end to end.
S["build-through-obstacles"]={
  setup=function(npc,params,s)
    local surface=npc.character.surface
    local home=npc.home or npc.character.position
    local x0=math.floor(home.x+(params.dx or 8))
    local y0=math.floor(home.y+(params.dy or 12))
    local len=math.floor(params.length or 40)
    local rows={belt=y0,pipe=y0+4,wall=y0+8}
    -- clear the ground first, then the obstacles
    local area={{x0-2,y0-3},{x0+len+2,y0+12}}
    for _,e in ipairs(surface.find_entities_filtered{area=area}) do
      if e.valid and e.type~="character" and e.type~="resource" then e.destroy() end
    end
    local rock,tree=Proving.natural()
    local placed={}
    local function put(what,name,x,y,extra)
      if not name then return end
      local spec={name=name,position={x,y},force=extra and extra.force or "neutral"}
      for k,v in pairs(extra or {}) do spec[k]=v end
      local e=try(function() return surface.create_entity(spec) end)
      placed[#placed+1]={what=what,name=name,x=x,y=y,ok=e~=nil}
    end
    local mate=game.forces["proving-teammate"] or game.create_force("proving-teammate")
    mate.set_friend(npc.force,true); npc.force.set_friend(mate,true)
    put("tree",tree,x0+6.5,rows.belt+0.5)
    put("rock",rock,x0+12.5,rows.belt+0.5)
    put("tree",tree,x0+8.5,rows.pipe+0.5)
    put("rock",rock,x0+16.5,rows.pipe+0.5)
    put("teammate",params.teammate_building or "wooden-chest",x0+14.5,rows.wall+0.5,{force=mate})
    put("teammate",params.teammate_building or "wooden-chest",x0+20.5,rows.belt+0.5,{force=mate})
    put("cliff","cliff",x0+26,rows.pipe+1,{cliff_orientation="west-to-east"})
    local water={}
    for dx=0,2 do for dy=-1,1 do water[#water+1]={name="water",position={x0+32+dx,rows.pipe+dy}} end end
    for dx=0,2 do water[#water+1]={name="water",position={x0+33+dx,rows.wall}} end
    surface.set_tiles(water)
    placed[#placed+1]={what="pond",x=x0+33,y=rows.pipe,
      ok=surface.get_tile(x0+33,rows.pipe).name=="water"}
    local entities,ids={},{belt={},pipe={},wall={}}
    for i=0,len-1 do
      local x=x0+i+0.5
      entities[#entities+1]={id="b"..i,prototype="transport-belt",position={x=x,y=rows.belt+0.5},direction=defines.direction.east}
      entities[#entities+1]={id="p"..i,prototype="pipe",position={x=x,y=rows.pipe+0.5}}
      entities[#entities+1]={id="w"..i,prototype="stone-wall",position={x=x,y=rows.wall+0.5}}
      ids.belt[#ids.belt+1]="b"..i; ids.pipe[#ids.pipe+1]="p"..i; ids.wall[#ids.wall+1]="w"..i
    end
    research(npc.force,params.research or {"logistics","landfill","cliff-explosives","stone-wall"})
    give(npc,params.give or {["transport-belt"]=60,["underground-belt"]=20,pipe=60,["pipe-to-ground"]=20,
      ["stone-wall"]=60,landfill=40,["cliff-explosives"]=5})
    local plan,why=hand_plan(npc,"proving-obstacles",entities,{
      {id="belt",depends_on={},entity_ids=ids.belt},{id="pipe",depends_on={},entity_ids=ids.pipe},
      {id="wall",depends_on={},entity_ids=ids.wall}})
    if not plan then return nil,why end
    s.plan_id,s.rows,s.x0,s.len=plan.plan_id,rows,x0,len
    s.log_from=#(npc.obstacle_log or {})
    local missing=0
    for _,o in ipairs(placed) do if not o.ok then missing=missing+1 end end
    s.obstacles_missing=missing
    return {placed=placed,rows=rows,x0=x0,length=len}
  end,
  score=function(npc,s,tick)
    local plan,status=find_plan(npc,s.plan_id)
    status=status or s.plan_status
    s.stuck={}
    local built,handled,silent,pending={},{},{},0
    local reasons={}
    for id,e in pairs(plan and plan.entities or {}) do
      for rung in pairs(e.rungs or {}) do reasons[rung]=(reasons[rung] or 0)+1 end
      local kind=id:sub(1,1)
      built[kind]=built[kind] or 0; handled[kind]=handled[kind] or 0; silent[kind]=silent[kind] or 0
      if e.entity and e.entity.valid then built[kind]=built[kind]+1
      elseif e.rungs and (e.rungs.left_out or e.rungs.hand_back or e.rungs.bridged or e.rungs.covered
          or e.rungs.shifted) then   -- (a final rung: the piece is not needed there, or went back with a reason)
        handled[kind]=handled[kind]+1
      elseif status=="building" then
        pending=pending+1
        s.stuck[#s.stuck+1]={id=id,failures=e.failures or 0}
      else silent[kind]=silent[kind]+1 end
    end
    -- the belt line end to end: belts (or an underground pair) on every tile of its row from start to end
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    local function covered(row,types)
      local n=0
      local below=false
      for i=0,s.len-1 do
        local found=surface.find_entities_filtered{position={s.x0+i+0.5,row+0.5},type=types,limit=1}[1]
        if found then
          n=n+1
          if found.type=="underground-belt" or found.type=="pipe-to-ground" then
            below=not below   -- (entrance to exit: the tiles between are under ground)
          end
        elseif below then n=n+1 end
      end
      return n==s.len
    end
    table.sort(s.stuck,function(a,b) return a.failures>b.failures or (a.failures==b.failures and a.id<b.id) end)
    for i=13,#s.stuck do s.stuck[i]=nil end
    local done=status~="building"
    local all_silent=0
    for _,v in pairs(silent) do all_silent=all_silent+v end
    return {obstacles_not_placed=s.obstacles_missing,plan_status=status,built=built,handed_back_or_worked_round=handled,silently_missing=all_silent,
      pending=pending,pending_pieces=s.stuck,plan_reason=s.plan_reason,phase=npc.phase,rungs=reasons,obstacle_records=#(npc.obstacle_log or {})-s.log_from,
      belt_connected=covered(s.rows.belt,{"transport-belt","underground-belt","splitter"}),
      pipe_connected=covered(s.rows.pipe,{"pipe","pipe-to-ground"}),
      engineer_alive=npc.character~=nil and npc.character.valid,done=done}
  end}

-- c. Opening pace (NPC Worker, NPC System One): a fresh plot, nothing set up. The game minute of its first steam
-- engine, first lab and red science automated, and how much coal it mined by hand.
-- The last count due by params.run_to taken (its sampling runs every 300 ticks: done waits for it).
-- (U-2, green then blue from nothing but the engineer's own work: what the bus and the science stand on)
-- The bus's lanes standing (both ends): how many, and how many carry each item.
function Proving.bus_lanes(npc)
  local n,items=0,{}
  for _,l in ipairs(BaseMemory.lines(npc) or {}) do
    if l.kind=="bus" and l.ok then
      n=n+1
      if l.item then items[l.item]=(items[l.item] or 0)+1 end
    end
  end
  return {count=n,items=items}
end

-- A block, by a piece of its name (params.block: a plan's purpose, id or source holding it, or for a bus stage its
-- stage's name, as bus-science-block): the game minute its plan was first taken on and the one it was finished, or nil.
function Proving.block_watch(npc,s,tick,match)
  if not match or match=="" then return end
  s.block=s.block or {}
  local function is(plan)
    if not plan then return false end
    for _,k in ipairs({"purpose","plan_id","source"}) do
      if type(plan[k])=="string" and plan[k]:find(match,1,true) then return true end
    end
    local stage=plan.base_plan and plan.base_plan.stage
    return type(stage)=="string" and stage:find(match,1,true)~=nil
  end
  local d=npc.design
  if not s.block.placed and d and is(d.plan) then s.block.placed,s.block.plan_id=tick,d.plan.plan_id end
  for _,plan in ipairs(npc.design_history or {}) do
    if is(plan) and not plan.partial then
      s.block.placed=s.block.placed or tick
      s.block.complete=s.block.complete or tick
      s.block.plan_id=s.block.plan_id or plan.plan_id
    end
  end
end

-- What it waits on, each second: its phase and its plan's status and reason; a stretch of one of them 3 game minutes
-- or more is kept (what, from, how long), the longest first.
Proving.WAIT_LONG=3*3600
function Proving.wait_watch(npc,s,tick)
  local d=npc.design
  local status=d and d.status
  local key
  if status and status~="building" and status~="complete" then
    key=tostring(npc.phase).." | "..tostring(d.purpose).." "..tostring(status)..(d.reason and (" "..tostring(d.reason)) or "")
  elseif type(npc.phase)=="string" and npc.phase:find("producing",1,true) then
    key=npc.phase   -- (a stage built, waiting on what it makes)
  end
  local w=s.wait
  if w and w.key~=key then
    if tick-w.from>=Proving.WAIT_LONG then
      s.waits=s.waits or {}
      s.waits[#s.waits+1]={what=w.key,from_minute=minutes(w.from-s.start),minutes=minutes(tick-w.from)}
    end
    s.wait=nil
  end
  if key and not s.wait then s.wait={key=key,from=tick} end
end
function Proving.waits(s,tick)
  local out={}
  for _,w in ipairs(s.waits or {}) do out[#out+1]=w end
  if s.wait and tick-s.wait.from>=Proving.WAIT_LONG then
    out[#out+1]={what=s.wait.key,from_minute=minutes(s.wait.from-s.start),minutes=minutes(tick-s.wait.from),ongoing=true}
  end
  table.sort(out,function(a,b) return a.minutes>b.minutes end)
  while #out>20 do table.remove(out) end
  return out
end

-- Packs made by its machines, sampled each game minute: a pack's rate over the last five minutes.
Proving.PACKS={red="automation-science-pack",green="logistic-science-pack",blue="chemical-science-pack"}
function Proving.pack_sample(s,surface,force)
  local made={}
  for _,a in ipairs(surface.find_entities_filtered{type="assembling-machine",force=force}) do
    local r=try(function() return a.get_recipe() end)
    if r then made[r.name]=(made[r.name] or 0)+(a.products_finished or 0) end
  end
  s.packs=s.packs or {}
  table.insert(s.packs,made)
  while #s.packs>6 do table.remove(s.packs,1) end
end
function Proving.pack_rates(s)
  local out={}
  local now,back=s.packs and s.packs[#s.packs],s.packs and s.packs[1]
  if not now or #s.packs<2 then return out end
  for k,name in pairs(Proving.PACKS) do
    out[k]=math.floor(10*math.max(0,(now[name] or 0)-(back[name] or 0))/(#s.packs-1))/10
  end
  return out
end

function Proving.counted(s)
  local last
  for _,at in ipairs({15,40,60,100}) do if at<=s.run_to then last=at end end
  return last==nil or (s.counts or {})[last]~=nil
end

S["opening-pace"]={
  setup=function(npc,params,s)
    s.marks={}
    s.run_to=tonumber(params.run_to)
    s.block_match=params.block   -- (a block watched by name: its plan placed and finished, U-2)
    -- an ordinary start has rocks and trees about (stone and wood by hand): the flat map has none, so a fixed
    -- scatter round home, the same every run
    local placed=Proving.scatter(npc,params.rocks or 16,params.trees or 80,params.radius or 70)
    return {home=npc.home,placed=placed}
  end,
  tick=function(npc,s,tick)
    -- (how much it walks, and how long its fuel and plate trips take: W-14, its reach)
    if tick%60==0 and npc.character and npc.character.valid then
      s.samples=(s.samples or 0)+1
      if try(function() return npc.character.walking_state.walking end) then s.walking=(s.walking or 0)+1 end
      s.walk0=s.walk0 or npc.travelled or 0
      s.seen=s.seen or {}
      s.rounds=s.rounds or {fuel={n=0,ticks=0},plate={n=0,ticks=0}}
      for id,r in pairs(npc.receipts or {}) do
        if not s.seen[id] and (r.kind=="transfer" or r.kind=="sweep") and r.status=="complete" and r.started_tick
            and r.tick then
          s.seen[id]=true
          local k=(r.item=="coal" or r.item=="wood") and "fuel" or (r.item=="iron-plate" or r.item=="copper-plate") and "plate"
          if k then s.rounds[k].n=s.rounds[k].n+1; s.rounds[k].ticks=s.rounds[k].ticks+(r.tick-r.started_tick) end
          s.hand=(s.hand or 0)+(r.tick-r.started_tick)   -- (all hand logistics: its share of the time)
        end
      end
    end
    if tick%60==0 then
      Proving.wait_watch(npc,s,tick)
      Proving.block_watch(npc,s,tick,s.block_match)
    end
    if tick%3600==0 and npc.character and npc.character.valid then Proving.pack_sample(s,npc.character.surface,npc.force) end
    if tick%300~=0 or not (npc.character and npc.character.valid) then return end
    local surface,force=npc.character.surface,npc.force
    local function have(filter) filter.force=force; filter.limit=1; return surface.count_entities_filtered(filter)>0 end
    if not s.marks.steam and have{type="generator"} then s.marks.steam=tick end
    if not s.marks.lab and have{type="lab"} then s.marks.lab=tick end
    -- (what it has built at minutes 15, 40, 60 and 100, against the user's own play at the same minutes:
    -- Play-Analysis-2026-10-28, F-13)
    local m=math.floor(minutes(tick-s.start))
    for _,at in ipairs({15,40,60,100}) do
      if m>=at and not (s.counts and s.counts[at]) then
        s.counts=s.counts or {}
        local function n(f) f.force=force; return surface.count_entities_filtered(f) end
        s.counts[at]={furnaces=n{type="furnace"},burner_drills=n{name="burner-mining-drill"},
          electric_drills=n{name="electric-mining-drill"},assemblers=n{type="assembling-machine"},
          belts=n{type="transport-belt"},labs=n{type="lab"}}
      end
    end
    if not s.marks.red or tick%3600==0 then
      -- (green too: its first pack by machine and how many so far, the bus's science against the pace aim, F-21)
      local green=0
      for _,a in ipairs(surface.find_entities_filtered{type="assembling-machine",force=force}) do
        local r=try(function() return a.get_recipe() end)
        if r and r.name=="automation-science-pack" and (a.products_finished or 0)>0 and not s.marks.red then
          s.marks.red=tick
        end
        if r and r.name=="logistic-science-pack" then green=green+(a.products_finished or 0) end
      end
      if green>0 and not s.marks.green then s.marks.green=tick end
      s.green_made=green
    end
  end,
  score=function(npc,s,tick)
    local coal=npc.item_flow and npc.item_flow.coal or {}
    local function at(t) return t and minutes(t-s.start) or nil end
    local from_machines=0
    for k,v in pairs(coal) do if k:sub(1,5)=="from:" then from_machines=from_machines+v end end
    return {steam_minute=at(s.marks.steam),lab_minute=at(s.marks.lab),red_minute=at(s.marks.red),
      green_minute=at(s.marks.green),green_made=s.green_made,
      coal_mined_by_hand=coal.mined or 0,coal_taken_from_machines=from_machines,
      counts=s.counts and {["15"]=s.counts[15],["40"]=s.counts[40],["60"]=s.counts[60],["100"]=s.counts[100]} or nil,
      engineer_alive=npc.character~=nil and npc.character.valid,
      walking_share=s.samples and s.samples>0 and math.floor(1000*(s.walking or 0)/s.samples)/1000 or nil,
      tiles_walked_per_minute=s.walk0 and math.floor((npc.travelled-s.walk0)/math.max(1,minutes(tick-s.start))) or nil,
      fuel_trip_seconds=s.rounds and s.rounds.fuel.n>0 and math.floor(10*s.rounds.fuel.ticks/s.rounds.fuel.n/60)/10 or nil,
      fuel_trips=s.rounds and s.rounds.fuel.n or nil,
      plate_trip_seconds=s.rounds and s.rounds.plate.n>0 and math.floor(10*s.rounds.plate.ticks/s.rounds.plate.n/60)/10 or nil,
      plate_trips=s.rounds and s.rounds.plate.n or nil,
      reach=npc.character and npc.character.valid and npc.character.reach_distance or nil,
      hand_logistics_share=s.hand and math.floor(1000*s.hand/math.max(1,tick-s.start))/1000 or nil,
      -- (U-2: the bus's lanes standing, the watched block, packs a minute over the last five, waits of 3 min and more)
      bus_lanes=Proving.bus_lanes(npc),per_minute_last_5=Proving.pack_rates(s),waits=Proving.waits(s,tick),
      block=s.block_match and {match=s.block_match,placed_minute=at(s.block and s.block.placed),
        complete_minute=at(s.block and s.block.complete),plan_id=s.block and s.block.plan_id} or nil,
      -- (with params.run_to: on to that minute, for the counts; else done once red science runs)
      done=s.marks.red~=nil and (not s.run_to or (minutes(tick-s.start)>=s.run_to and Proving.counted(s)))}
  end}

-- Stubs for their owners to fill (setup, tick, score as above); tools/proving.py lists them too.
local function stub(owner)
  return {setup=function() return nil,"stub: to be filled by "..owner end,score=function() return {done=true} end}
end
S["power-fault"]=stub("NPC Factory")        -- a power network cut or short of fuel: found and fixed
-- e. A nest cluster near its plot (NPC Offense, O-5): what it has to fight with and to spare chosen, no waves. Does it
-- attack (weighed on its odds and on what it can afford to lose), and how does it go: nests left, turrets lost, alive.
S["fight-odds"]={
  setup=function(npc,params,s)
    local plot=plot_of(npc)
    if not plot then return nil,"no_plot" end
    local edge=params.edge or plot.outer[1]
    if not edge then return nil,"no_outer_edge" end
    local surface=npc.character.surface
    local t=TeamWorld.state()
    t.death_world=false   -- (no waves: the nest is what is measured)
    if params.evolution then game.forces.enemy.set_evolution_factor(params.evolution,surface) end
    if params.research then research(npc.force,params.research) end
    give(npc,{["submachine-gun"]=1,["light-armor"]=1,["firearm-magazine"]=params.magazines or 20,
      ["gun-turret"]=params.turrets or 2})
    local a,b=t.area,plot.box
    local mx,my=(b[1]+b[3])/2,(b[2]+b[4])/2
    local away=params.nest_distance or 24
    local centre=({north={mx,a[2]-away},south={mx,a[4]+away},west={a[1]-away,my},east={a[3]+away,my}})[edge]
    local along=({north={1,0},south={1,0},west={0,1},east={0,1}})[edge]
    land_between(surface,{x=centre[1],y=centre[2]},{x=mx,y=my})
    local nests=0
    for k=1,math.floor(params.spawners or 1) do
      local off=(k-(params.spawners or 1)/2-0.5)*10
      local at=surface.find_non_colliding_position("biter-spawner",{x=centre[1]+along[1]*off,y=centre[2]+along[2]*off},16,1)
      local e=at and surface.create_entity{name="biter-spawner",position=at,force="enemy"}
      if e then
        nests=nests+1
        if params.pollute then surface.pollute(at,params.pollute) end   -- (it soaks up the base's pollution: it matters)
      end
    end
    if nests==0 then return nil,"no_nest_placed" end
    s.centre={x=centre[1],y=centre[2]}
    s.limit=params.minutes or 20
    s.turrets_lost_from=game.forces.enemy.get_kill_count_statistics(surface).get_input_count("gun-turret")
    local h=npc.home or npc.character.position
    return {nests=nests,centre=s.centre,distance=math.floor(math.sqrt((centre[1]-h.x)^2+(centre[2]-h.y)^2)),
      evolution=game.forces.enemy.get_evolution_factor(surface)}
  end,
  tick=function(npc,s,tick)
    if tick%60~=0 then return end
    for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do
      if it.tool=="attack" and it.args and it.args.target=="nests" and not s.attack_ordered then
        s.attack_ordered=tick
        s.attack_args={turrets=it.args.turrets,magazines=it.args.magazines}
      end
      if it.tool=="arm_up" and not s.arm_ordered then s.arm_ordered=tick end
    end
  end,
  score=function(npc,s,tick)
    local c=npc.character
    local surface=c and c.valid and c.surface or game.surfaces[1]
    local left=surface.count_entities_filtered{position=s.centre,radius=60,force="enemy",type="unit-spawner"}
    local log=npc.attack_log or {}
    local mins=minutes(tick-s.start)
    return {attacked=s.attack_ordered~=nil,attack_minute=s.attack_ordered and minutes(s.attack_ordered-s.start) or nil,
      armed_up=s.arm_ordered~=nil,attack_args=s.attack_args,nests_left=left,attacks_made=#log,
      outcome=log[#log] and log[#log].outcome or nil,
      turrets_lost=game.forces.enemy.get_kill_count_statistics(surface).get_input_count("gun-turret")-s.turrets_lost_from,
      engineer_alive=c~=nil and c.valid,done=(mins or 0)>=s.limit or left==0}
  end}
S["vehicle-doors"]=stub("NPC Movement")     -- a car or tank through the wall's doors and back

-- d. Death-world waves (NPC Team): its plot on a death world, a few nests out beyond its outer edge, the waves
-- (team_waves.lua) left to come as they would: a grace, a first wave its starting gun can beat, then the ramp. The
-- engineer builds its opening meanwhile; how it holds is NPC Defense's and Offense's to read.
S["death-world-waves"]={
  setup=function(npc,params,s)
    local plot=plot_of(npc)
    if not plot then return nil,"no_plot" end
    local edge=params.edge or plot.outer[1]
    if not edge then return nil,"no_outer_edge" end
    local surface=npc.character.surface
    local t=TeamWorld.state()
    t.death_world=true
    t.waves=nil   -- (the grace counts from now)
    if params.evolution then game.forces.enemy.set_evolution_factor(params.evolution,surface) end
    local spawner=params.spawner
    if not spawner then
      for name,proto in pairs(prototypes.get_entity_filtered{{filter="type",type="unit-spawner"}}) do
        if (name=="biter-spawner" or not spawner) and #(try(function() return proto.result_units end) or {})>0 then
          spawner=name
          if name=="biter-spawner" then break end
        end
      end
    end
    if not spawner then return nil,"no_spawner_prototype" end
    local a=t.area
    local b=plot.box
    local away=params.nest_distance or 120
    local mx,my=(b[1]+b[3])/2,(b[2]+b[4])/2
    local centre=({north={mx,a[2]-away},south={mx,a[4]+away},west={a[1]-away,my},east={a[3]+away,my}})[edge]
    local along=({north={1,0},south={1,0},west={0,1},east={0,1}})[edge]
    land_between(surface,{x=centre[1],y=centre[2]},{x=mx,y=my})
    if params.research then research(npc.force,params.research) end   -- (e.g. turrets and walls known from the start)
    local nests={}
    for k=1,math.floor(params.nests or 3) do
      local off=(k-(params.nests or 3)/2-0.5)*24
      local at={x=centre[1]+along[1]*off,y=centre[2]+along[2]*off}
      local p=surface.find_non_colliding_position(spawner,at,32,1)
      local e=p and surface.create_entity{name=spawner,position=p,force="enemy"}
      if e then nests[#nests+1]={x=math.floor(p.x),y=math.floor(p.y)} end
    end
    if #nests==0 then return nil,"no_nest_placed" end
    -- (with params.turrets: that many gun turrets round its home, loaded, its own: the waves are to press them)
    local placed=0
    local home=npc.home or npc.character.position
    for k=1,math.floor(params.turrets or 0) do
      research(npc.force,{"gun-turret"})
      local a=(k-0.5)/params.turrets*math.pi*2
      local want={x=home.x+math.cos(a)*14,y=home.y+math.sin(a)*14}
      -- (params.row: in a row on the nests' side instead, 8 tiles apart, as a wall line's turrets stand: D-19's lines)
      if params.row then want={x=math.floor(home.x+(k-0.5-params.turrets/2)*8),y=math.floor(home.y-params.row)} end
      local p=surface.find_non_colliding_position("gun-turret",want,params.row and 2 or 8,1)
      local e=p and surface.create_entity{name="gun-turret",position=p,force=npc.force}
      if e then e.insert{name=params.ammo or "firearm-magazine",count=params.magazines or 20}; placed=placed+1 end
    end
    -- (with params.power: steam power standing by its home, handed to it as a finished plan, assemblers and poles in
    -- its pockets and automation known: what its defense supply by machine needs, so that is measured alone, F-11)
    local power
    if params.power then
      local plan,why=Proving.steam_power(npc,surface,home)
      if not plan then return nil,why end
      power="steam-power"
      s.power_plan=plan.plan_id
      give(npc,{["assembling-machine-1"]=4,["small-electric-pole"]=20})
    end
    if type(params.give)=="table" then give(npc,params.give) end   -- (what a scenario hands it besides)
    -- (params.ring: a wall ring of that half-size round home, a door gap mid each side, params.ring_turrets gun turrets
    -- on its north turret line, all standing and kept as a finished ring plan: the perimeter ammo loop's ground, D-19)
    if params.ring then
      local R=math.floor(params.ring)
      local x0,y0=math.floor(home.x)-R,math.floor(home.y)-R
      local x1,y1=x0+2*R,y0+2*R
      local built={}
      local function put(name,x,y)
        for _,old in ipairs(surface.find_entities_filtered{area={{x-1,y-1},{x+1,y+1}},type={"tree","simple-entity"}}) do
          old.destroy()
        end
        local e=surface.create_entity{name=name,position={x=x,y=y},force=npc.force}
        if e then built[#built+1]=e end
        return e
      end
      for a=x0+2,x1-3 do
        if math.abs(a-(x0+R))>2 then put("stone-wall",a+0.5,y0+2.5); put("stone-wall",a+0.5,y1-2.5) end
      end
      for b=y0+3,y1-4 do
        if math.abs(b-(y0+R))>2 then put("stone-wall",x0+2.5,b+0.5); put("stone-wall",x1-2.5,b+0.5) end
      end
      local n=math.floor(params.ring_turrets or 4)
      research(npc.force,{"gun-turret"})
      for k=1,n do
        local t=put("gun-turret",math.floor(x0+R+(k-0.5-n/2)*10),y0+8)
        if t then t.insert{name=params.ammo or "firearm-magazine",count=params.magazines or 20} end
      end
      -- (kept as the ring's plans: its walls a plot-wall plan a side, its turrets a plot-turrets plan, as the
      -- planner's own would be: perimeter.wall_progress counts the walls, Codex's review)
      local function side_of(e)
        local p=e.position
        if e.type=="ammo-turret" then return "turrets" end
        if p.y<y0+4 then return "north" elseif p.y>y1-4 then return "south" elseif p.x<x0+4 then return "west" end
        return "east"
      end
      local groups={}
      for _,e in ipairs(built) do
        local k=side_of(e)
        groups[k]=groups[k] or {}
        table.insert(groups[k],e)
      end
      npc.design_history=npc.design_history or {}
      for k,list in pairs(groups) do
        local purpose=k=="turrets" and "plot-turrets" or "plot-wall"
        local plan={plan_id="proving-ring-"..k.."-"..game.tick,purpose=purpose,order={},entities={},stages={},
          accepted_tick=game.tick,keep={ring={x0,y0,x1,y1},ring_gen=1,ring_side=k=="turrets" and "north" or k,ring_layer=1}}
        local ids={}
        for i,e in ipairs(list) do
          local id=k.."-"..i
          plan.order[#plan.order+1]=id; ids[#ids+1]=id
          plan.entities[id]={id=id,prototype=e.name,item=e.name,entity=e,built_tick=game.tick,
            world={x=e.position.x,y=e.position.y},direction=e.direction}
        end
        plan.stages[1]={id="ring",status="complete",entity_ids=ids,supplies={},witnesses={}}
        table.insert(npc.design_history,plan)
      end
      s.ring={x0,y0,x1,y1}
    end
    s.limit=params.minutes or 30
    s.lost_from=0
    for _,c in pairs(game.forces.enemy.get_kill_count_statistics(surface).input_counts) do s.lost_from=s.lost_from+c end
    s.turrets_placed=placed
    s.turrets_killed_from=game.forces.enemy.get_kill_count_statistics(surface).get_input_count("gun-turret")
    s.surface_index=surface.index   -- (its own surface, the engineer dead or not: Codex's review, D-17)
    s.turrets_seen=surface.count_entities_filtered{force=npc.force,type={"ammo-turret","electric-turret","fluid-turret"}}
    return {nests=nests,spawner=spawner,edge=edge,turrets=placed,power=power,
      evolution=game.forces.enemy.get_evolution_factor(surface)}
  end,
  -- How its defense keeps pace (NPC Defense, D-11): when its first turret and wall stood, and its turrets at each wave.
  tick=function(npc,s,tick)
    if tick%60~=0 then return end
    local surface=s.surface_index and game.surfaces[s.surface_index] or npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    local turrets=surface.count_entities_filtered{force=npc.force,type={"ammo-turret","electric-turret","fluid-turret"}}
    -- (its turrets as each wave on its own plot is sent: Codex's review)
    local plot=plot_of(npc)
    local mine=plot and ((storage.team and storage.team.waves and storage.team.waves.per_plot or {})[plot.name] or 0) or 0
    s.at_wave=s.at_wave or {}
    s.empty_at_wave=s.empty_at_wave or {}
    if #s.at_wave<mine then   -- (and how many of them were empty then: D-19)
      local empty=0
      for _,t in ipairs(surface.find_entities_filtered{force=npc.force,type="ammo-turret"}) do
        local inv=t.get_inventory(defines.inventory.turret_ammo)
        if inv and inv.is_empty() then empty=empty+1 end
      end
      while #s.empty_at_wave<mine do s.empty_at_wave[#s.empty_at_wave+1]=empty end
    end
    while #s.at_wave<mine do s.at_wave[#s.at_wave+1]=turrets end
    -- (a line's ammunition at a turret: a powered arm of its own into it, magazines on the belt it picks from: D-19)
    if tick%600==0 then   -- (and the most turrets fed so at once: the waves, sized to its defense, take them in time)
      local fed=0
      for _,t in ipairs(surface.find_entities_filtered{force=npc.force,type="ammo-turret"}) do
        for _,arm in ipairs(surface.find_entities_filtered{force=npc.force,type="inserter",position=t.position,radius=3}) do
          local p=arm.drop_target==t and arm.energy>0 and arm.pickup_target
          if p and p.valid and p.type=="transport-belt" then
            local on=0
            for line=1,p.get_max_transport_line_index() do on=on+p.get_transport_line(line).get_item_count() end
            if on>0 then fed=fed+1; s.line_ammo=s.line_ammo or tick; break end
          end
        end
      end
      s.fed_peak=math.max(s.fed_peak or 0,fed)
    end
    -- (the engineer's time loading turrets by hand, second by second: D-19)
    s.seconds=(s.seconds or 0)+1
    if tostring(npc.phase):find("turret_ammo",1,true) then s.ammo_hand=(s.ammo_hand or 0)+1 end
    -- (turrets that went up and that went away, second by second: lost to biters is the kill count, the rest picked up;
    -- D-17)
    local was=s.turrets_seen or turrets
    if turrets>was then s.turrets_up=(s.turrets_up or 0)+turrets-was
    elseif turrets<was then s.turrets_down=(s.turrets_down or 0)+was-turrets end
    s.turrets_seen=turrets
    if tick%3600==0 then s.turrets_by_minute=s.turrets_by_minute or {}; table.insert(s.turrets_by_minute,turrets) end
    if tick%600~=0 then return end
    local walls=surface.count_entities_filtered{force=npc.force,type="wall"}
    if turrets>(s.turrets_placed or 0) and not s.first_turret then s.first_turret=tick end
    if walls>0 and not s.first_wall then s.first_wall=tick end
    -- (what it was building, minute by minute: a defense plan that never finishes holds every goal after it)
    if tick%3600==0 then
      local d=npc.design
      s.designs=s.designs or {}
      local held=""
      local inv=npc.character and npc.character.valid and npc.character.get_main_inventory()
      for i=1,inv and #inv or 0 do
        local st=inv[i]
        if st.valid_for_read and st.type=="ammo-turret" or (st.valid_for_read and st.prototype.place_result and st.prototype.place_result.type=="ammo-turret") then
          held=held.." "..st.count.."@"..string.format("%.2f",st.health)
        end
      end
      s.designs[#s.designs+1]=(d and (tostring(d.purpose)..":"..tostring(d.status)..(d.reason and (":"..tostring(d.reason)) or "")
        ..(d.stage and (":s"..tostring(d.stage)) or "")) or "none")..(held~="" and (" turrets"..held) or "")
        .." | "..tostring(npc.phase)
      -- (what a turret takes, in its pockets: a turret plan that never gets its first turret, D-17)
      if inv then
        s.designs[#s.designs]=s.designs[#s.designs]..string.format(" | iron %d gear %d copper %d ore %d mag %d",
          inv.get_item_count("iron-plate"),inv.get_item_count("iron-gear-wheel"),inv.get_item_count("copper-plate"),
          inv.get_item_count("iron-ore"),inv.get_item_count("firearm-magazine"))
      end
    end
  end,
  score=function(npc,s,tick)
    local w=storage.team and storage.team.waves or {}
    local log=w.log or {}
    local first,last=log[1],log[#log]
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    local force=npc.force
    local killed,lost=0,-s.lost_from
    for _,c in pairs(force.get_kill_count_statistics(surface).input_counts) do killed=killed+c end
    for _,c in pairs(game.forces.enemy.get_kill_count_statistics(surface).input_counts) do lost=lost+c end
    local sized=#log>0
    for _,wave in ipairs(log) do sized=sized and type(wave.why)=="string" and wave.why:find("sized to beat")~=nil end
    local sizes={}
    for _,wave in ipairs(log) do sizes[#sizes+1]=wave.size.."/"..wave.threat end
    local mins=minutes(tick-s.start)
    return {waves=w.sent or 0,first_wave_minute=first and minutes(first.tick-s.start) or nil,
      first_wave_units=first and first.size or nil,first_wave_threat=first and first.threat or nil,
      last_wave_threat=last and last.threat or nil,ramp_grows=(first and last and #log>=3 and last.threat>first.threat)
        or false,sized=sized,sizes=sizes,last_why=last and last.why or nil,paused=w.paused,
      biters_killed=killed,buildings_lost=lost,
      turrets=surface.count_entities_filtered{force=force,type={"ammo-turret","electric-turret","fluid-turret"}},
      walls=surface.count_entities_filtered{force=force,type="wall"},
      first_turret_minute=s.first_turret and minutes(s.first_turret-s.start) or nil,
      first_wall_minute=s.first_wall and minutes(s.first_wall-s.start) or nil,turrets_at_wave=s.at_wave or {},
      turrets_up=s.turrets_up or 0,turrets_down=s.turrets_down or 0,turrets_by_minute=s.turrets_by_minute or {},
      empty_at_wave=s.empty_at_wave or {},turrets_fed=s.fed_peak or 0,
      -- (its turrets' arms at the end: how many stand, have power, have ammunition on the belt they pick from; D-19)
      arms=(function()
        local o={arms=0,powered=0,stocked=0,belts=0,on_belts=0}
        for _,t in ipairs(surface.find_entities_filtered{force=force,type="ammo-turret"}) do
          for _,arm in ipairs(surface.find_entities_filtered{force=force,type="inserter",position=t.position,radius=3}) do
            if arm.drop_target==t then
              o.arms=o.arms+1
              if arm.energy>0 then o.powered=o.powered+1 end
              local p=arm.pickup_target
              local n=0
              if p and p.valid and p.type=="transport-belt" then
                for line=1,p.get_max_transport_line_index() do n=n+p.get_transport_line(line).get_item_count() end
              end
              if n>0 then o.stocked=o.stocked+1 end
            end
          end
        end
        for _,b in ipairs(surface.find_entities_filtered{force=force,type="transport-belt"}) do
          local n=0
          for line=1,b.get_max_transport_line_index() do n=n+b.get_transport_line(line).get_item_count() end
          o.belts=o.belts+1
          if n>0 then o.on_belts=o.on_belts+1 end
        end
        -- (its ammo-line plans: where each stands, and what holds back their supplies)
        o.plans={}
        local function look(p,where)
          if p and p.purpose=="ammo-line" then
            local st={}
            for _,g in ipairs(p.stages or {}) do st[#st+1]=g.id..":"..tostring(g.status)..":"..#(g.supplies or {}) end
            o.plans[#o.plans+1]={id=p.plan_id,where=where,partial=p.partial,stages=table.concat(st," ")}
          end
        end
        for _,p in ipairs(npc.design_history or {}) do look(p,"history") end
        look(npc.design and npc.design.plan,"design:"..tostring(npc.design and npc.design.status))
        o.backoff={}
        for k,v in pairs(npc.supply_backoff or {}) do o.backoff[#o.backoff+1]=k..">"..tostring(v.until_tick) end
        -- (the loop's chests: what they hold, and their arms out onto a belt)
        o.chests={}
        for _,c in ipairs(surface.find_entities_filtered{force=force,name="iron-chest"}) do
          local entry={x=c.position.x,y=c.position.y,mags=c.get_inventory(defines.inventory.chest).get_item_count()}
          for _,arm in ipairs(surface.find_entities_filtered{force=force,type="inserter",position=c.position,radius=1.6}) do
            if arm.pickup_target==c then
              entry.arm={energy=arm.energy,status=arm.status,drop=arm.drop_target and arm.drop_target.name,
                d=arm.direction,x=arm.position.x,y=arm.position.y}
            end
          end
          o.chests[#o.chests+1]=entry
        end
        return o
      end)(),
      empty_most=(function() local m=0 for _,v in ipairs(s.empty_at_wave or {}) do m=math.max(m,v) end return m end)(),
      line_ammo_minute=s.line_ammo and minutes(s.line_ammo-s.start) or nil,
      ammo_hand_share=s.seconds and s.seconds>0 and math.floor((s.ammo_hand or 0)/s.seconds*1000)/1000 or 0,
      turrets_killed=game.forces.enemy.get_kill_count_statistics(game.surfaces[s.surface_index or surface.index])
        .get_input_count("gun-turret")-(s.turrets_killed_from or 0),   -- (normal quality: what this world places)
      designs=s.designs or {},power=s.power_plan and DesignedStart.proving_power(npc,s.power_plan) or nil,
      buildings=surface.count_entities_filtered{force=force}-1,
      engineer_alive=npc.character~=nil and npc.character.valid,done=(mins or 0)>=s.limit}
  end}

-- f. A later stage (NPC Factory, Worker, Research; OP-7): the engineer starts where the stage begins, with the
-- research for it (and all before it), steam power handed to it with an energy interface on its network (power is no
-- bottleneck, yet its planners see a generator), plates and the stage's machines in its pockets and a chest by its
-- home, a crude-oil field near, and for the silo the rocket part's inputs. Scored by the minute each step first stood
-- or ran, and every plan it gave up with why. Prototypes: base and Space Age (the user's set, mods-user-21).
local function with_prerequisites(force,names)
  local all,order={},{}
  local function add(name)
    local t=force.technologies[name]
    if not t or all[name] then return end
    all[name]=true
    for pre in pairs(t.prerequisites) do add(pre) end
    order[#order+1]=name
  end
  for _,name in ipairs(names) do add(name) end
  return order
end

-- New work since the setup: a machine counts once it has made more than it had then (on this recipe: one switched to it
-- counts from its count at the switch, as the baseline is kept per recipe).
local function made_since(s,e,recipe)
  local b=e.unit_number and s.baseline[e.unit_number]
  if b and b.recipe~=recipe then
    b.recipe,b.made,b.at_setup=recipe,e.products_finished or 0,nil   -- (switched since: counted from here)
    return false
  end
  return (e.products_finished or 0)>(b and b.made or 0)
end
local function producing(s,surface,force,recipe,names)
  local wanted=type(recipe)=="table" and recipe or {recipe}
  local any={}
  for _,r in ipairs(wanted) do any[r]=true end
  for _,e in ipairs(surface.find_entities_filtered{force=force,type={"assembling-machine","furnace"},name=names}) do
    local r=try(function() return e.get_recipe() end)
    if r and any[r.name] and made_since(s,e,r.name) then return true end
  end
  return false
end

-- A machine set to `recipe` since the setup (a new one, or one switched to it): a line laid out, before it runs.
local function set_since(s,surface,force,recipe)
  for _,e in ipairs(surface.find_entities_filtered{force=force,type="assembling-machine"}) do
    local r=try(function() return e.get_recipe() end)
    local b=e.unit_number and s.baseline[e.unit_number]
    if r and r.name==recipe and not (b and b.at_setup and b.recipe==recipe) then return true end
  end
  return false
end

-- Creative mode (the user, 2026-10-29: "give the NPCs creative mode so they have everything in the game"): whatever it
-- sets out to fetch, make or wait for is put in its pockets, a stack at a time. It still builds and connects all of it
-- itself; only materials are free. Read from what it says it is doing (its phase), so any item it wants comes.
Proving.CREATIVE_CHEST_STACKS=10   -- what a creative chest by home keeps of an item waited on, beyond the pockets
local function creative_chest(npc,s)
  if s.creative_chest and s.creative_chest.valid then return s.creative_chest end
  local c=npc.character
  local home=npc.home or c.position
  local at=c.surface.find_non_colliding_position("steel-chest",{x=home.x-6,y=home.y+6},16,1)
  s.creative_chest=at and c.surface.create_entity{name="steel-chest",position=at,force=npc.force} or nil
  return s.creative_chest
end
local function creative(npc,s)
  -- (its phase, and what the way to space waits on while it builds: orbit.lua's npc.orbit_wait, R-8)
  for _,said in ipairs({tostring(npc.phase or ""),tostring(npc.orbit_wait or "")}) do
    -- (any phase that fetches, needs or waits for something ends in what it is: acquire:<item>,
    -- need:waiting_for:<item>, supply-wait:smelting:<item>; a word that names no item is no want)
    local item=(said:find("acquire",1,true) or said:find("need",1,true) or said:find("wait",1,true))
      and said:match("([%w%-_]+)$")
    local proto=item and prototypes.item[item]
    if proto and s.creative_never and s.creative_never[item] then proto=nil end   -- (only what it builds)
    if proto then
      -- The whole want, however large (1,200 steel for a silo waited for ever on a stack at a time, R-8): every second
      -- it still waits, its pockets filled with it as far as they hold, and a chest by home kept stocked beyond that.
      local n=npc.character.insert{name=item,count=proto.stack_size*Proving.CREATIVE_CHEST_STACKS}
      local chest=n==0 and creative_chest(npc,s)
      if chest then
        local want=proto.stack_size*Proving.CREATIVE_CHEST_STACKS-chest.get_item_count(item)
        if want>0 then n=chest.insert{name=item,count=want} end
      end
      if n>0 then s.creative_given=s.creative_given or {}; s.creative_given[item]=(s.creative_given[item] or 0)+n
      else s.creative_full=s.creative_full or {}; s.creative_full[item]=(s.creative_full[item] or 0)+1 end   -- (no room)
    end
  end
end

-- Creative mode's floor: a stack of each of the common building items kept in its pockets, once a game minute (a repair
-- or upkeep names no item: unpowered inserters on a stamped bus waited two hundred game minutes on poles, grow-bus
-- 2026-10-30). What it sets out to fetch beyond these still comes by its phase (creative above).
Proving.CREATIVE_FLOOR={"small-electric-pole","medium-electric-pole","big-electric-pole","transport-belt",
  "underground-belt","splitter","inserter","long-handed-inserter","fast-inserter","pipe","pipe-to-ground",
  "assembling-machine-1","assembling-machine-2","stone-furnace","steel-furnace","electric-furnace","electric-mining-drill",
  "iron-chest","stone-wall","gun-turret","firearm-magazine","coal","iron-plate","copper-plate","steel-plate"}
local function creative_floor(npc,s)
  local c=npc.character
  for _,item in ipairs(Proving.CREATIVE_FLOOR) do
    local proto=prototypes.item[item]
    if proto and c.get_item_count(item)<proto.stack_size then
      local n=c.insert{name=item,count=proto.stack_size-c.get_item_count(item)}
      if n>0 then s.creative_given=s.creative_given or {}; s.creative_given[item]=(s.creative_given[item] or 0)+n end
    end
  end
end

-- What its fluid machines would make and use a second at full speed, from their recipes (pumpjacks from their wells,
-- offshore pumps their rate): the ratio built against ideal, per fluid; whether they run is in the statuses, sampled
-- once a minute.
local function fluid_balance(surface,force)
  local made,used={},{}
  local function add(t,name,v) t[name]=(t[name] or 0)+v end
  for _,e in ipairs(surface.find_entities_filtered{force=force,type={"assembling-machine","furnace"}}) do
    local r=try(function() return e.get_recipe() end)
    local speed=try(function() return e.crafting_speed end) or 1
    if r and (r.energy or 0)>0 then
      local per=speed/r.energy
      local fluid=false
      for _,p in ipairs(r.products or {}) do
        if p.type=="fluid" then
          fluid=true
          add(made,p.name,(p.amount or ((p.amount_min or 0)+(p.amount_max or 0))/2)*(p.probability or 1)*per)
        end
      end
      for _,i in ipairs(r.ingredients or {}) do if i.type=="fluid" then fluid=true; add(used,i.name,i.amount*per) end end
    end
  end
  local normal=prototypes.entity["crude-oil"] and prototypes.entity["crude-oil"].normal_resource_amount or 300000
  for _,e in ipairs(surface.find_entities_filtered{force=force,type="mining-drill",name="pumpjack"}) do
    local well=e.mining_target
    if well and well.valid then add(made,well.name,10*(well.amount/normal)*(try(function() return e.prototype.mining_speed end) or 1)) end
  end
  for _,e in ipairs(surface.find_entities_filtered{force=force,type="offshore-pump"}) do
    local f=try(function() return e.get_fluid(1) end)
    local per_tick=try(function() return e.prototype.get_pumping_speed() end) or try(function() return e.prototype.pumping_speed end) or 20
    add(made,f and f.name or "water",per_tick*60)
  end
  local out={}
  for name in pairs(made) do out[name]={made=math.floor(made[name]*10)/10,used=math.floor((used[name] or 0)*10)/10} end
  for name in pairs(used) do out[name]=out[name] or {made=0,used=math.floor(used[name]*10)/10} end
  return out
end
-- Blue packs made since the last look (crafts times the recipe's yield) against what its blue machines are laid out
-- to make a minute; one row each game minute and one for the minute the scenario ends in.
function Proving.blue_count(surface,force)
  local made,design=0,0
  local r=prototypes.recipe["chemical-science-pack"]
  if not r then return 0,0 end
  local out=0
  for _,p in ipairs(r.products) do if p.name=="chemical-science-pack" then out=out+(p.amount or 1)*(p.probability or 1) end end
  for _,e in ipairs(surface.find_entities_filtered{force=force,type="assembling-machine"}) do
    local cur=try(function() return e.get_recipe() end)
    if cur and cur.name==r.name then
      made=made+(e.products_finished or 0)*out
      design=design+(try(function() return e.crafting_speed end) or 1)/r.energy*60*out
    end
  end
  return made,design
end
function Proving.blue_minute(s,surface,force,tick)
  local made,design=Proving.blue_count(surface,force)
  if #s.blue_minutes<60 then
    s.blue_minutes[#s.blue_minutes+1]={minute=minutes(tick-s.start),made=made-(s.blue_total or made),
      design=math.floor(design*10)/10}
  end
  s.blue_total,s.blue_tick=made,tick
end
local STATUS_NAMES
local function status_name(v)
  if not STATUS_NAMES then
    STATUS_NAMES={}
    for k,n in pairs(defines.entity_status) do STATUS_NAMES[n]=k end
  end
  return STATUS_NAMES[v] or tostring(v)
end

local MACHINES={pumpjack=4,["oil-refinery"]=2,["chemical-plant"]=6,["assembling-machine-2"]=10,["storage-tank"]=2,
  ["offshore-pump"]=2,pipe=200,["pipe-to-ground"]=40,["small-electric-pole"]=60,["medium-electric-pole"]=30,
  inserter=60,["fast-inserter"]=20,["transport-belt"]=200,["underground-belt"]=20,splitter=10}
local PLATES={["iron-plate"]=1200,["copper-plate"]=800,["steel-plate"]=300,["stone-brick"]=200,
  ["electronic-circuit"]=200,coal=400,["iron-gear-wheel"]=200}
Proving.WATCH={["chemical-science-pack"]=true,["engine-unit"]=true,["advanced-circuit"]=true,
  ["electronic-circuit"]=true,["iron-gear-wheel"]=true,["pipe"]=true}   -- (sampled in the oil stages, F-20)
-- A stage: research (and all before it), steps {key, how, what} with how built|running|making|set|parts, pass (a
-- step's key), and as it needs: oil (a field and pond on open ground), items (in its pockets), part_inputs, creative
-- (whatever it sets out to fetch is put in its pockets), practice (a world made once: tools/make_practice_world.py),
-- and its own setup(npc,params,s,surface,home), tick(npc,s,tick,surface) (each second) and score(npc,s,tick,surface)
-- (fields added to the score). Other chats add stages here, and a row in tools/proving.py SCENARIOS.
Proving.STAGES={
  oil={research={"oil-processing","plastics"},
    steps={{"pumpjack","built","pumpjack"},{"refinery","built","oil-refinery"},
      {"refining","running","oil-refinery"},{"plastic","making","plastic-bar"}},pass="plastic",oil=true},
  blue={research={"chemical-science-pack"},
    steps={{"pumpjack","built","pumpjack"},{"refining","running","oil-refinery"},{"plastic","making","plastic-bar"},
      {"sulfur","making","sulfur"},{"red_circuits","making","advanced-circuit"},{"engines","making","engine-unit"},
      {"blue_science","making","chemical-science-pack"}},pass="blue_science",oil=true},
  -- (the user's oil practice world, OP-8: a running mid-game base, creative supply, straight into oil and blue)
  oil_practice={research={"oil-processing","advanced-oil-processing","plastics","sulfur-processing","advanced-circuit",
      "engine","lubricant","chemical-science-pack"},
    steps={{"pumpjack","built","pumpjack"},{"refinery","built","oil-refinery"},{"chemical_plant","built","chemical-plant"},
      {"plastic","making","plastic-bar"},{"sulfur","making","sulfur"},
      {"cracking","making",{"heavy-oil-cracking","light-oil-cracking"}},{"red_circuits","making","advanced-circuit"},
      {"engines","making","engine-unit"},{"blue_line","set","chemical-science-pack"},
      {"first_blue","making","chemical-science-pack"}},
    pass="first_blue",oil=true,creative=true,practice=true},
  -- (U-2, blue from green: from the save where green first ran, nothing given (no kit, no creative supply, no power
  -- interface), oil processing researched and the rest of the chain due; scored on the block (params.block), red
  -- circuits, sulfur and the first blue pack, with the bus, the rates and the long waits as green-from-zero has them)
  blue_from_green={research={"oil-processing"},kit=false,oil=true,
    steps={{"pumpjack","built","pumpjack"},{"refinery","built","oil-refinery"},{"sulfur","making","sulfur"},
      {"plastic","making","plastic-bar"},{"red_circuits","making","advanced-circuit"},
      {"first_blue","making","chemical-science-pack"}},
    pass="first_blue",
    setup=function(npc,params,s) s.block_match=params.block; return {block=params.block} end,
    tick=function(npc,s,tick,surface)
      Proving.wait_watch(npc,s,tick)
      Proving.block_watch(npc,s,tick,s.block_match)
      if tick%3600==0 then Proving.pack_sample(s,surface,npc.force) end
    end,
    score=function(npc,s,tick,surface)
      local function at(t) return t and minutes(t-s.start) or nil end
      return {bus_lanes=Proving.bus_lanes(npc),per_minute_last_5=Proving.pack_rates(s),waits=Proving.waits(s,tick),
        block=s.block_match and {match=s.block_match,placed_minute=at(s.block and s.block.placed),
          complete_minute=at(s.block and s.block.complete),plan_id=s.block and s.block.plan_id} or nil}
    end},
  silo={research={"rocket-silo"},
    steps={{"silo","built","rocket-silo"},{"rocket_parts","parts","rocket-silo"}},pass="rocket_parts",
    items={["rocket-silo"]=1},part_inputs=50},
  -- (R-8, the road to space: the silo's rocket carries the starter pack up and the platform's hub stands over Nauvis,
  -- then the engineer lays the platform's layout as ghosts and its rockets carry up what the hub lacks until it stands
  -- built (platform_build.lua); later slices build it out to travel-ready. Steps past the silo are read from the
  -- launch's own record, orbit.lua and platform_launch.lua)
  platform={research={"rocket-silo","solar-energy","advanced-material-processing-2","automation-2","oil-processing",
      "space-platform-thruster"},
    steps={{"silo","built","rocket-silo"},{"rocket_parts","parts","rocket-silo"}},pass="road",
    -- (the starter pack's parts in its pockets: it crafts the pack itself; 60 foundations take 1200 steel, more than
    -- creative supply's stack at a time brings; and the layout's foundations and pieces, ten seconds a foundation by
    -- hand being past a practice run)
    items={["space-platform-foundation"]=600,["processing-unit"]=20,["steel-plate"]=100,["crusher"]=3,
      ["asteroid-collector"]=3,["solar-panel"]=15,["inserter"]=14,["medium-electric-pole"]=13,["electric-furnace"]=1,
      ["assembling-machine-2"]=1,["cargo-landing-pad"]=1,["chemical-plant"]=4,["pipe"]=30,["thruster"]=1,
      ["gun-turret"]=2,["assembling-machine-1"]=1},creative=true,
    -- (no waves, and its silo handed to it standing and powered, as steam power is to other stages: on the bus checkpoint
    -- the plot's defense took the whole run and no silo was chosen, R-8; the silo's own choice and feeding are
    -- stage-silo's to measure)
    setup=function(npc,params,s,surface,home)
      -- (and no fights: nests near home cleared, biters peaceful; the engineer went to attack nests from minute 8 and
      -- the ground side of the way to space never came round, R-8; fighting is the combat stages' to measure)
      Proving.normal_world(npc,surface,home,400)
      surface.peaceful_mode=true
      local at
      for r=16,64,8 do
        for k=0,7 do
          local a=k*math.pi/4
          local c={x=math.floor(home.x+math.cos(a)*r)+0.5,y=math.floor(home.y+math.sin(a)*r)+0.5}
          local area={{c.x-7,c.y-7},{c.x+9,c.y+7}}
          if not at and surface.count_entities_filtered{area=area,force=npc.force,limit=1}==0
              and surface.count_entities_filtered{area=area,type="cliff",limit=1}==0
              and surface.count_tiles_filtered{area=area,collision_mask="water_tile",limit=1}==0 then
            for _,e in ipairs(surface.find_entities_filtered{area=area,type={"tree","simple-entity"}}) do e.destroy() end
            at=c
          end
        end
      end
      if not at then return nil,"no_room_for_the_silo" end
      local pole={x=at.x+6,y=at.y}
      local made={{id="silo",prototype="rocket-silo",position=at,recipe="rocket-part"},
        {id="silo_pole",prototype="medium-electric-pole",position=pole}}
      -- (as a finished plan in its history, what it keeps: a checkpoint's engineer is often mid-build, and a plan handed
      -- over that is refused)
      local plan={plan_id="proving-rocket-silo-"..game.tick,purpose="rocket-silo",source="proving",order={},entities={},
        stages={{id="silo",status="complete",completed_tick=game.tick,entity_ids={},supplies={},witnesses={}}}}
      for _,e in ipairs(made) do
        local built=surface.create_entity{name=e.prototype,position=e.position,force=npc.force,recipe=e.recipe}
        if not built then return nil,"silo_piece_not_placed:"..e.prototype end
        plan.order[#plan.order+1]=e.id
        plan.entities[e.id]={id=e.id,entity=built,prototype=e.prototype,item=e.prototype,world=e.position,failures=0}
        table.insert(plan.stages[1].entity_ids,e.id)
      end
      npc.design_history=npc.design_history or {}
      table.insert(npc.design_history,plan)
      -- (its power: an energy interface by its pole, as the stage's boost elsewhere)
      local p=surface.find_non_colliding_position("electric-energy-interface",{x=pole.x+2,y=pole.y},4,0.5)
      local eei=p and surface.create_entity{name="electric-energy-interface",position=p,force=npc.force}
      if eei then eei.electric_buffer_size=1e8; eei.power_production=20e6/60; eei.energy=1e8 end
      return {waves=false,silo={x=at.x,y=at.y},silo_power=eei~=nil}
    end,
    tick=function(npc,s,tick,surface)
      if tick%60~=0 then return end
      -- (its rocket parts free as any material, creative: the silo's own feeding is stage-silo's to measure; the
      -- engineer still builds the silo, makes and loads the starter pack and the cargo, asks for the platform and
      -- launches; a rocket for every cargo once in orbit)
      if not s.parts_given or s.marks.orbit then
        for _,silo in ipairs(surface.find_entities_filtered{force=npc.force,type="rocket-silo"}) do
          local need=try(function() return silo.prototype.rocket_parts_required end) or 50
          if (silo.rocket_parts or 0)<need and silo.rocket_silo_status~=defines.rocket_silo_status.rocket_ready
              and try(function() silo.rocket_parts=need; return true end) then s.parts_given=tick end
        end
      end
      local r=storage.platform_launch and storage.platform_launch.requests[npc.id]
      if r and r.platform and r.platform.valid and not s.marks.platform_requested then s.marks.platform_requested=tick end
      if r and r.status=="native_launch_ordered" and not s.marks.launched then s.marks.launched=tick end
      if r and r.platform and r.platform.valid and r.platform.hub and not s.marks.orbit then s.marks.orbit=tick end
      if s.marks.orbit and not s.marks.platform_built and tick%600==0 and PlatformBuild.done(npc,r.platform) then
        s.marks.platform_built=tick
      end
      -- (its crushers' ore, carbon and ice in the hub: the platform mines asteroids; then its space science)
      -- (made on the platform: its production statistics, the hub's stock being used up by the plants after)
      if s.marks.platform_built and tick%600==0 then
        local stats=try(function() return npc.force.get_item_production_statistics(r.platform.surface) end)
        local function made(item) return stats and (try(function() return stats.get_input_count(item) end) or 0) or 0 end
        if not s.marks.materials and made("iron-ore")>0 and made("carbon")>0 and made("ice")>0 then
          s.marks.materials=tick
        end
        if not s.marks.space_science and made("space-science-pack")>0 then s.marks.space_science=tick end
      end
      -- (what it was doing when, every 20 seconds: the last 60)
      if tick%1200==0 then
        s.phase_log=s.phase_log or {}
        s.phase_log[#s.phase_log+1]=string.format("%.1f %s %s",(tick-s.start)/3600,tostring(npc.phase or ""),
          tostring(npc.orbit_wait or ""))
        if #s.phase_log>60 then table.remove(s.phase_log,1) end
      end
      -- (its packs down in its landing pad, sent by the platform for the pad's request)
      local pad=npc.space_pad
      if s.marks.space_science and not s.marks.packs_landed and tick%600==0 and pad and pad.valid
          and pad.get_inventory(defines.inventory.cargo_landing_pad_main).get_item_count("space-science-pack")>0 then
        s.marks.packs_landed=tick
      end
      -- (its thruster holding both its fuel and its oxidizer: ready to push the platform)
      if s.marks.platform_built and not s.marks.thrust_ready and tick%600==0 then
        for _,t in ipairs(r.platform.surface.find_entities_filtered{type="thruster",force=npc.force}) do
          local held=try(function() return t.get_fluid_contents() end) or {}
          if (held["thruster-fuel"] or 0)>0 and (held["thruster-oxidizer"] or 0)>0 then s.marks.thrust_ready=tick end
        end
      end
      -- (a turret on it loaded with magazines it made: it holds against asteroids)
      if s.marks.platform_built and not s.marks.defended and tick%600==0 then
        for _,t in ipairs(r.platform.surface.find_entities_filtered{type="ammo-turret",force=npc.force}) do
          local ammo=t.get_inventory(defines.inventory.turret_ammo)
          if ammo and not ammo.is_empty() then s.marks.defended=tick end
        end
      end
      -- (the whole road so far: every mark in)
      if not s.marks.road and s.marks.materials and s.marks.space_science and s.marks.packs_in_lab
          and s.marks.thrust_ready and s.marks.defended then
        s.marks.road=tick
      end
      -- (and in a lab, by an inserter or a belt from the pad: never carried)
      if s.marks.space_science and not s.marks.packs_in_lab and tick%600==0 then
        for _,lab in ipairs(surface.find_entities_filtered{type="lab",force=npc.force}) do
          local input=lab.get_inventory(defines.inventory.lab_input)
          if input and input.get_item_count("space-science-pack")>0 then s.marks.packs_in_lab=tick end
        end
      end
    end,
    score=function(npc,s,tick)
      local out={}
      for _,k in ipairs({"platform_requested","launched","orbit","platform_built","materials","space_science","packs_landed","packs_in_lab","thrust_ready","defended","road"}) do
        out[k.."_minute"]=s.marks[k] and minutes(s.marks[k]-s.start) or nil
      end
      local r=storage.platform_launch and storage.platform_launch.requests[npc.id]
      out.launch_status=r and r.status or "none"
      out.orbit_phase=tostring(npc.phase or "")
      out.orbit_wait=npc.orbit_wait
      out.cargo_launches=r and r.cargo_launches or 0
      if r and r.platform and r.platform.valid and r.platform.hub then   -- (what the platform still lacks, and has laid)
        out.platform_needs={}
        for _,n in ipairs(PlatformBuild.needs(npc,r.platform,PlatformLaunch.in_flight(npc))) do out.platform_needs[n.item]=n.count end
        out.platform_ghosts=#r.platform.surface.find_entities_filtered{type={"entity-ghost","tile-ghost"},force=npc.force}
        out.platform_made=PlatformBuild.made(r.platform)
        out.space_fed=Orbit.fed(npc)
        out.phase_log=s.phase_log
        out.feed_wait=npc.orbit_wait
        out.thrusters={}
        for _,t in ipairs(r.platform.surface.find_entities_filtered{type="thruster",force=npc.force}) do
          local held=try(function() return t.get_fluid_contents() end) or {}
          out.thrusters[#out.thrusters+1]={fuel=math.floor(held["thruster-fuel"] or 0),
            oxidizer=math.floor(held["thruster-oxidizer"] or 0)}
        end
        out.plants={}
        for _,e in ipairs(r.platform.surface.find_entities_filtered{type="assembling-machine",name="chemical-plant",
            force=npc.force}) do
          local rec=e.get_recipe()
          out.plants[#out.plants+1]=(rec and rec.name or "none")..":"..tostring(e.status)
        end
        local pad=npc.space_pad
        out.pad_packs=pad and pad.valid
          and pad.get_inventory(defines.inventory.cargo_landing_pad_main).get_item_count("space-science-pack") or nil
        out.labs=#npc.character.surface.find_entities_filtered{type="lab",force=npc.force}
      end
      local silo=Orbit.silo(npc)   -- (the silo it keeps: how far its rocket got)
      if silo then
        local names={}
        for k,v in pairs(defines.rocket_silo_status) do names[v]=k end
        out.silo={status=names[silo.rocket_silo_status],parts=try(function() return silo.rocket_parts end),
          energy=math.floor(silo.energy),pack_held=npc.character.get_item_count("space-platform-starter-pack")}
      else out.silo="none of its own" end
      return out
    end},
}

-- Late-game combat (NPC Offense, O-7; NPC Defense's half after D-19): the user, "defend and attack correctly even late
-- game". On a running base (a practice world): the research of late combat (laser and flamethrower turrets,
-- artillery, power armor, uranium ammunition, tanks and spidertrons, the damage and speed upgrades), the biters evolved
-- (big and behemoth biters, spitters, big worms), nest clusters at several distances out from its plot, its late kit
-- (power armor with its equipment, uranium rounds, gun, laser and flamethrower turrets, artillery and shells, a tank),
-- creative supply. Waves (params.waves) for the defense half. Scored by the nests cleared, when, what it lost.
Proving.LATE_KIT={["power-armor-mk2"]=1,["submachine-gun"]=1,["uranium-rounds-magazine"]=600,["gun-turret"]=30,   -- (rings of 3 a building, O-9)
  ["laser-turret"]=10,["flamethrower-turret"]=4,["artillery-turret"]=2,["artillery-shell"]=60,["tank"]=1,
  ["uranium-cannon-shell"]=100,["rocket-fuel"]=20,["stone-wall"]=200,["repair-pack"]=50,["poison-capsule"]=20}
Proving.LATE_GRID={"fission-reactor-equipment","personal-laser-defense-equipment","personal-laser-defense-equipment",
  "energy-shield-mk2-equipment","energy-shield-mk2-equipment","exoskeleton-equipment","exoskeleton-equipment",
  "battery-mk2-equipment"}
-- {distance out from its plot's outer edge, spawners {biter, spitter}, worms {big, behemoth}}
Proving.LATE_NESTS={{40,{2,1},{2,0}},{140,{3,1},{2,1}},{300,{3,2},{3,1}}}
local function late_setup(npc,params,s,surface,home)
  local made_before=storage.practice and storage.practice[params.stage]
  local kept=made_before and made_before.combat
  if not kept then
    game.forces.enemy.set_evolution_factor(params.evolution or 0.75,surface)
    -- the kit: armor worn with its grid filled, the rest in its pockets
    local worn=npc.character.get_inventory(defines.inventory.character_armor)
    -- (worn instead of what it had on, which goes to its pockets: the first practice world left it in heavy armor)
    if worn and prototypes.item["power-armor-mk2"] then
      local old=not worn.is_empty() and worn[1].valid_for_read and worn[1].name~="power-armor-mk2" and worn[1].name
      if old then worn.clear() end
      if worn.is_empty() then worn.insert{name="power-armor-mk2"} end
      -- (the old one into its main inventory: a plain insert put it straight back on, and the power armor had no room)
      if old then npc.character.get_main_inventory().insert{name=old} end
    end
    local grid=worn and worn[1] and worn[1].valid_for_read and worn[1].grid
    for _,name in ipairs(grid and Proving.LATE_GRID or {}) do
      if prototypes.equipment[name] then try(function() return grid.put{name=name} end) end
    end
    -- (shells stack one to a slot: they go in a chest by home it keeps as its own store, the rest in its pockets)
    local kit={}
    for k,v in pairs(Proving.LATE_KIT) do if k~="power-armor-mk2" and k~="artillery-shell" then kit[k]=v end end
    give(npc,kit)
    local at=surface.find_non_colliding_position("steel-chest",{x=home.x-6,y=home.y+6},12,1)
    local store=at and surface.create_entity{name="steel-chest",position=at,force=npc.force}
    if not store then return nil,"no_room_for_the_shell_chest" end
    store.insert{name="artillery-shell",count=Proving.LATE_KIT["artillery-shell"]}
    npc.storage_chests=npc.storage_chests or {}
    npc.storage_chests[#npc.storage_chests+1]=store
    -- the nests: clusters at several distances out beyond its plot's outer edge, side by side along it
    local plot=plot_of(npc)
    local t=TeamWorld.state()
    local edge=plot and (params.edge or plot.outer[1])
    if not (plot and edge) then return nil,"no_plot_edge" end
    local a,b=t.area,plot.box
    local mx,my=(b[1]+b[3])/2,(b[2]+b[4])/2
    local out_dir=({north={0,-1},south={0,1},west={-1,0},east={1,0}})[edge]
    local along=({north={1,0},south={1,0},west={0,1},east={0,1}})[edge]
    local base=({north={mx,a[2]},south={mx,a[4]},west={a[1],my},east={a[3],my}})[edge]
    kept={clusters={}}
    for k,ring in ipairs(Proving.LATE_NESTS) do
      local off=(k-2)*90
      local c={x=base[1]+out_dir[1]*ring[1]+along[1]*off,y=base[2]+out_dir[2]*ring[1]+along[2]*off}
      land_between(surface,c,{x=mx,y=my})
      local placed=0
      local function put(name,n,spread)
        for i=1,n do
          local ang=(i/n)*math.pi*2+k
          local want={x=c.x+math.cos(ang)*spread,y=c.y+math.sin(ang)*spread}
          local at=prototypes.entity[name] and surface.find_non_colliding_position(name,want,16,1)
          if at and surface.create_entity{name=name,position=at,force="enemy"} then placed=placed+1 end
        end
      end
      put("biter-spawner",ring[2][1],8); put("spitter-spawner",ring[2][2],8)
      put("big-worm-turret",ring[3][1],14); put("behemoth-worm-turret",ring[3][2],4)
      kept.clusters[#kept.clusters+1]={x=math.floor(c.x),y=math.floor(c.y),placed=placed,
        distance=math.floor(math.sqrt((c.x-home.x)^2+(c.y-home.y)^2))}
    end
    storage.practice=storage.practice or {}
    storage.practice[params.stage]=storage.practice[params.stage] or {}
    storage.practice[params.stage].combat=kept
  end
  if params.waves then TeamWorld.state().death_world=true; TeamWorld.state().waves=nil end
  s.combat={clusters={},turrets_lost_from=0,lost_from=0}
  local kills=game.forces.enemy.get_kill_count_statistics(surface)
  for _,c in pairs(kills.input_counts) do s.combat.lost_from=s.combat.lost_from+c end
  for _,name in ipairs({"gun-turret","laser-turret","flamethrower-turret","artillery-turret","tank"}) do
    s.combat.turrets_lost_from=s.combat.turrets_lost_from+kills.get_input_count(name)
  end
  for _,c in ipairs(kept.clusters) do s.combat.clusters[#s.combat.clusters+1]={x=c.x,y=c.y,distance=c.distance} end
  return {clusters=kept.clusters,evolution=game.forces.enemy.get_evolution_factor(surface),waves=params.waves or nil}
end
local function late_left(surface,c)
  return surface.count_entities_filtered{position=c,radius=40,force="enemy",type={"unit-spawner","turret"}}
end
-- The robot network (NPC Ledger's L-3 with NPC Worker's W-26b): from a running base, the robots' research and kit; it
-- covers its base with roboports (companion/robo_grid.py) and stocks robots. Once a network of its own with construction
-- robots covers ground, the stage puts a row of ghosts (not the engineer's plan) in its reach and their items into the
-- network: robots, not the engineer, should build them from what the network holds.
Proving.ROBOT_GHOSTS=8
Proving.ROBOT_COVER=0.5   -- (the share of its machines in robot reach the stage passes at; tools/proving.py checks it)
-- (NPC Worker's W-26b: a plan of its own in the network's reach, its items in the network and none in its pockets:
-- the network's robots should build it, the engineer neither fetching nor walking)
Proving.ROBOT_PLAN={["stone-furnace"]=10,["wooden-chest"]=10}

-- The plan handed for W-26b: furnaces in a row and chests in a row below, on free ground in a cell's reach.
local function robot_plan(npc,surface,o)
  local offs={}
  for _,dx in ipairs({-22,-10,4}) do for _,dy in ipairs({-18,-8,4,12}) do offs[#offs+1]={dx,dy} end end
  for _,off in ipairs(offs) do
    local x0,y0=math.floor(o.position.x)+off[1],math.floor(o.position.y)+off[2]
    local list,ok={},true
    for i=0,Proving.ROBOT_PLAN["stone-furnace"]-1 do
      list[#list+1]={id="f"..i,prototype="stone-furnace",position={x=x0+2*i+1,y=y0+1}}
    end
    for i=0,Proving.ROBOT_PLAN["wooden-chest"]-1 do
      list[#list+1]={id="c"..i,prototype="wooden-chest",position={x=x0+2*i+0.5,y=y0+3.5}}
    end
    for _,e in ipairs(list) do
      if not surface.can_place_entity{name=e.prototype,position=e.position,force=npc.force,
          build_check_type=defines.build_check_type.ghost_place} then ok=false break end
    end
    if ok then return list end
  end
end
local function robot_networks(npc,surface,home)
  local seen,out={},{}
  for _,port in ipairs(surface.find_entities_filtered{type="roboport",force=npc.force,position=home,radius=400}) do
    local net=try(function() return port.logistic_network end)
    if net and net.valid and not seen[net.network_id] then seen[net.network_id]=true; out[#out+1]=net end
  end
  return out
end
Proving.STAGES.robots={research={"construction-robotics","logistic-robotics","logistic-system"},
  items={roboport=20,["construction-robot"]=50,["logistic-robot"]=20,["passive-provider-chest"]=10,
    ["requester-chest"]=10,["storage-chest"]=4,["medium-electric-pole"]=30},
  -- (passed once robots built the whole ghost row: its tick marks it; the first roboport is a step on the way)
  steps={{"roboport","built","roboport"}},pass="ghosts_built",
  setup=function(npc,params,s,surface,home)
    s.robots={home={x=home.x,y=home.y}}
    -- (its pockets before: belts it held then are not the ghosts' items)
    s.robots.belts_held=try(function() return npc.character.get_main_inventory().get_item_count("transport-belt") end) or 0
    return {ghosts=Proving.ROBOT_GHOSTS}
  end,
  tick=function(npc,s,tick,surface)
    local r=s.robots
    if not r or tick%600~=0 then return end
    if r.ghosts then
      local built=0
      for _,at in ipairs(r.ghosts) do
        if surface.count_entities_filtered{name="transport-belt",force=npc.force,position=at,radius=0.3}>0 then
          built=built+1
        end
      end
      -- Then a plan of its own in reach, its items in the network only (W-26b), once the row stands and nothing
      -- is being built.
      if built>=#r.ghosts and not r.plan and not DesignedStart.active(npc) then
        local net=robot_networks(npc,surface,r.home)[1]
        local list
        for _,cell in ipairs(net and net.cells or {}) do
          local o=cell.owner
          list=o and o.valid and robot_plan(npc,surface,o) or nil
          if list then break end
        end
        r.plan_why=list and nil or "no free ground in its reach"
        if list then
          local inv=npc.character.get_main_inventory()
          for item,n in pairs(Proving.ROBOT_PLAN) do
            inv.remove({name=item,count=1000000})
            net.insert({name=item,count=n})
          end
          local ids={}
          for _,e in ipairs(list) do ids[#ids+1]=e.id end
          local handed,why=hand_plan(npc,"robot-plan",list,{{id="robot-plan",depends_on={},entity_ids=ids,
            manual_supplies={},witnesses={{kind="entity_built",entity_id=ids[#ids]}}}},"proving:robots")
          if handed then r.plan,r.plan_tick,r.plan_why=#list,tick,nil else r.plan_why="not handed: "..tostring(why) end
        end
      end
      -- (passed once the row is built, the grid covers its base, and its own plan was built by the network's robots)
      local by_net=math.max(0,(npc.robots_built_network or 0)-built)
      if built>=#r.ghosts and (r.covered or 0)>=Proving.ROBOT_COVER and r.plan and by_net>=r.plan
          and not s.marks.ghosts_built then
        s.marks.ghosts_built=tick
      end
      if not DesignedStart.active(npc) and (r.covered or 0)<0.9 then
        DesignedStart.begin(npc,tick,"Build what you decided: robot-network","robot-network")
        npc.design.ask="now"
        r.asked=(r.asked or 0)+1
      end
      return
    end
    -- (the robot network asked for by name, as its strategist would, until a roboport of its own stands: what the
    -- engineer's own weighing picks is not what this stage tests; nothing weighs that goal yet, 2026-10-29)
    if not DesignedStart.active(npc) and (r.covered or 0)<0.9 then
      DesignedStart.begin(npc,tick,"Build what you decided: robot-network","robot-network")
      npc.design.ask="now"
      r.asked=(r.asked or 0)+1
    end
    for _,net in ipairs(robot_networks(npc,surface,r.home)) do
      if (net.all_construction_robots or 0)>=10 then
        local cell=net.cells and net.cells[1]
        local o=cell and cell.owner
        if o and o.valid then
          -- a free straight row in its reach, a few tiles off the roboport
          for _,off in ipairs({{6,-10},{-14,8},{8,10},{-12,-12}}) do
            local row,ok={},true
            for i=0,Proving.ROBOT_GHOSTS-1 do
              local at={x=math.floor(o.position.x)+off[1]+i+0.5,y=math.floor(o.position.y)+off[2]+0.5}
              if not surface.can_place_entity{name="transport-belt",position=at,force=npc.force,
                  build_check_type=defines.build_check_type.ghost_place} then ok=false break end
              row[#row+1]=at
            end
            if ok then
              for _,at in ipairs(row) do
                Ghosts.create(surface,{inner_name="transport-belt",position=at,force=npc.force,
                  direction=defines.direction.east})
              end
              net.insert({name="transport-belt",count=Proving.ROBOT_GHOSTS})
              r.ghosts,r.ghost_tick=row,tick
              r.belts_held=try(function() return npc.character.get_main_inventory().get_item_count("transport-belt") end) or 0
              return
            end
          end
        end
      end
    end
  end,
  score=function(npc,s,tick,surface)
    local r=s.robots or {}
    local home=r.home or npc.home or npc.character.position
    local nets=robot_networks(npc,surface,home)
    local ports=surface.count_entities_filtered{type="roboport",force=npc.force,position=home,radius=400}
    local bots=0
    for _,net in ipairs(nets) do bots=bots+(net.all_construction_robots or 0) end
    -- its machines in a network's logistic area: the share of the base robots can reach
    local machines,covered=0,0
    for _,e in ipairs(surface.find_entities_filtered{force=npc.force,position=home,radius=300,
        type={"assembling-machine","furnace","mining-drill","lab"}}) do
      machines=machines+1
      if try(function() return npc.force.find_logistic_network_by_position(e.position,surface) end) then
        covered=covered+1
      end
    end
    local built=0
    for _,at in ipairs(r.ghosts or {}) do
      if surface.count_entities_filtered{name="transport-belt",force=npc.force,position=at,radius=0.3}>0 then
        built=built+1
      end
    end
    local held=try(function() return npc.character.get_main_inventory().get_item_count("transport-belt") end) or 0
    r.covered=machines>0 and covered/machines or 0   -- (what the tick asks for the network by, while under 0.9)
    return {roboports_placed=ports,robots_in_network=bots,
      coverage_share=machines>0 and math.floor(covered/machines*1000+0.5)/1000 or 0,
      ghosts_placed=r.ghosts and #r.ghosts or 0,ghosts_built_by_bots=built,
      ghost_minutes=r.ghost_tick and math.floor((tick-r.ghost_tick)/360+0.5)/10 or nil,
      -- (belts it took from its pockets since the ghosts were put down: before that it builds as it likes)
      engineer_belts_used=r.ghosts and math.max(0,(r.belts_held or held)-held) or 0,asked=r.asked,
      -- (its own plan's pieces built by the robots of a network covering its base: NPC Worker's W-26b counts them)
      -- (its counter takes in every build by a covering network's robots: the stage's own ghost row less)
      plan_pieces_by_network=math.max(0,(npc.robots_built_network or 0)-built),plan_pieces=r.plan or 0,
      plan_why=r.plan_why,
      plan_minutes=r.plan_tick and math.floor((tick-r.plan_tick)/360+0.5)/10 or nil}
  end}

Proving.STAGES.late_combat_practice={research={"laser-turret","flamethrower","artillery","power-armor-mk2","uranium-ammo",
    "tank","spidertron","military-4","explosive-rocketry","physical-projectile-damage-6","weapon-shooting-speed-6",
    "laser-weapons-damage-6","laser-shooting-speed-6","refined-flammables-6","stronger-explosives-6",
    "artillery-shell-range-1","artillery-shell-speed-1","personal-laser-defense-equipment","energy-shield-mk2-equipment",
    "fission-reactor-equipment","exoskeleton-equipment","battery-mk2-equipment"},
  steps={},pass="cleared",creative=true,practice=true,setup=late_setup,
  tick=function(npc,s,tick,surface)
    -- (attacks started, by how: an artillery attack holds while targets stay in its range, so it may not be over, and
    -- in the attack log, by the end of a short run: NPC Defense's late run read 0 attacks with a cluster cleared)
    if tick%60==0 and s.combat then
      s.combat.started=s.combat.started or {}
      for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do
        if it.tool=="attack" and it.args and it.args.target=="nests" and it.started and not s.combat.started[it.request_id or it] then
          s.combat.started[it.request_id or it]=it.args.method or "creep"
        end
      end
    end
    if tick%600~=0 or not s.combat then return end
    local all=true
    for _,c in ipairs(s.combat.clusters) do
      local left=late_left(surface,c)
      if left==0 and not c.cleared then c.cleared=tick end
      if left>0 then all=false end
    end
    if all and not s.marks.cleared then s.marks.cleared=tick end
  end,
  score=function(npc,s,tick,surface)
    local out={clusters={},nests_cleared=0}
    local kills=game.forces.enemy.get_kill_count_statistics(surface)
    for _,c in ipairs(s.combat and s.combat.clusters or {}) do
      local left=late_left(surface,c)
      if left==0 then out.nests_cleared=out.nests_cleared+1 end
      out.clusters[#out.clusters+1]={distance=c.distance,left=left,cleared_minute=c.cleared and minutes(c.cleared-s.start) or nil}
    end
    local lost,turrets=-(s.combat and s.combat.lost_from or 0),-(s.combat and s.combat.turrets_lost_from or 0)
    for _,c in pairs(kills.input_counts) do lost=lost+c end
    for _,name in ipairs({"gun-turret","laser-turret","flamethrower-turret","artillery-turret","tank"}) do
      turrets=turrets+kills.get_input_count(name)
    end
    out.buildings_lost,out.turrets_lost=lost,turrets
    out.attacks_made=#(npc.attack_log or {})
    out.attacks_started={}
    for _,how in pairs(s.combat and s.combat.started or {}) do out.attacks_started[how]=(out.attacks_started[how] or 0)+1 end
    out.evolution=game.forces.enemy.get_evolution_factor(surface)
    return out
  end}

-- h. Nuclear power (NPC Designer, DS-4): from a running base, nuclear power and uranium processing researched, a
-- uranium field, a pond and a clear square of params.room (64) tiles on open ground, creative supply except fuel cells
-- (only what it builds: else nothing tells when cells run out). The game's own facts for what companion/nuclear.py
-- sizes from are logged once; the score: reactors hot, exchangers at temperature, turbine MW against the heat its
-- reactors give with their neighbour bonus as built, fuel cells a minute, cells never out once fuelled, spent cells
-- taken out, nothing idle while the reactors are hot.
-- A long run's checkpoint made a normal world on load (the user stopped the death world, 2026-10-30; the checkpoints
-- were made on it): the game's own evolution and expansion (map-settings.example.json; the death world's are 10x the
-- time factor and 4x as often), no death-world waves, every nest, worm and biter within `radius` of home gone, no
-- biter anywhere on its way (an attack under way or gathering), and the cliffs inside its plot gone. What was done.
function Proving.normal_world(npc,surface,home,radius)
  local ms=game.map_settings
  ms.enemy_evolution.time_factor=0.000004
  ms.enemy_evolution.destroy_factor=0.002
  ms.enemy_evolution.pollution_factor=0.0000009
  ms.enemy_expansion.min_expansion_cooldown=4*3600
  ms.enemy_expansion.max_expansion_cooldown=60*3600
  local t=TeamWorld.state()
  t.death_world=false; t.waves=nil
  local out={nests=0,units=0,cliffs=0,evolution=game.forces.enemy.get_evolution_factor(surface)}
  for _,e in ipairs(surface.find_entities_filtered{position=home,radius=radius or 400,force="enemy"}) do
    if e.type=="unit-spawner" then out.nests=out.nests+1 end
    e.destroy()
  end
  for _,e in ipairs(surface.find_entities_filtered{force="enemy",type="unit"}) do
    out.units=out.units+1; e.destroy()
  end
  local plot=plot_of(npc)
  local b=plot and plot.box
  if b then
    for _,e in ipairs(surface.find_entities_filtered{area={{b[1],b[2]},{b[3],b[4]}},type="cliff"}) do
      out.cliffs=out.cliffs+1; e.destroy()
    end
  end
  return out
end

-- An electric load on its network by the pole nearest home: `share` of what its generators can give at most, so its
-- buildings run on low power and the base reads short of power as a real shortfall would (Corrector.power_short).
-- The load and the capacity it was sized from, MW; or nil, why.
function Proving.power_load(npc,surface,home,share)
  local cap=0
  for _,e in ipairs(surface.find_entities_filtered{force=npc.force,type={"generator","solar-panel","burner-generator"}}) do
    local p=e.prototype
    cap=cap+(try(function() return p.get_max_power_output(e.quality) end) or try(function() return p.max_power_output end)
      or try(function() return p.get_max_energy_production(e.quality) end) or 0)*60/1e6
  end
  if cap<=0 then return nil,"no_generators" end
  local poles=surface.find_entities_filtered{force=npc.force,type="electric-pole",position=home,radius=96}
  table.sort(poles,function(a,b)
    local da=(a.position.x-home.x)^2+(a.position.y-home.y)^2
    local db=(b.position.x-home.x)^2+(b.position.y-home.y)^2
    if da~=db then return da<db end
    return a.position.x<b.position.x or (a.position.x==b.position.x and a.position.y<b.position.y)
  end)
  for _,pole in ipairs(poles) do
    if (pole.electric_network_id or 0)>0 then
      local at=surface.find_non_colliding_position("electric-energy-interface",pole.position,3,0.5)
      local e=at and surface.create_entity{name="electric-energy-interface",position=at,force=npc.force}
      if e then
        local mw=cap*share
        e.electric_buffer_size=mw*1e6
        e.power_production=0
        e.power_usage=mw*1e6/60
        e.destructible=false
        return {load_mw=math.floor(mw*10)/10,capacity_mw=math.floor(cap*10)/10,at={x=at.x-home.x,y=at.y-home.y}}
      end
    end
  end
  return nil,"no_powered_pole"
end

-- A machine making `item` (sulfur: a chemical plant; else a stone furnace smelting it, kept in ore and coal) above a
-- belt's head tile `at`, and an inserter from it onto the belt. {machine,inserter}; or nil.
function Proving.belt_head(npc,surface,at,item)
  local machine
  if item=="sulfur" then
    machine=surface.create_entity{name="chemical-plant",position={at.x+0.5,at.y-2.5},force=npc.force,recipe="sulfur"}
  else
    local ore=({["iron-plate"]="iron-ore",["copper-plate"]="copper-ore"})[item]
    machine=ore and surface.create_entity{name="stone-furnace",position={at.x+1,at.y-2},force=npc.force}
    if machine then machine.insert{name=ore,count=50}; machine.insert{name="coal",count=20} end
  end
  if not machine then return nil end
  local inserter=surface.create_entity{name=item=="sulfur" and "inserter" or "burner-inserter",
    position={at.x+0.5,at.y-0.5},direction=defines.direction.north,force=npc.force}
  if not inserter then machine.destroy(); return nil end
  if inserter.drop_position.y<inserter.pickup_position.y then   -- (it must take from the machine and drop on the belt)
    inserter.direction=defines.direction.south
  end
  if inserter.name=="burner-inserter" then inserter.insert{name="coal",count=10} end
  return {machine=machine,inserter=inserter}
end

-- An open square of `half` tiles round a point on a ring 40..140 out from home (none of its buildings, no enemy, no
-- water, nothing in `taken`, no ore unless `ore_ok`: a block may stand on ore); its trees, rocks and cliffs cleared.
function Proving.open_square(npc,surface,home,half,taken,ore_ok)
  for r=40,140,12 do
    for k=0,15 do
      local a=k*math.pi/8
      local c={x=math.floor(home.x+math.cos(a)*r),y=math.floor(home.y+math.sin(a)*r)}
      local area={{c.x-half,c.y-half},{c.x+half,c.y+half}}
      local clear=true
      for _,t in ipairs(taken or {}) do
        if area[1][1]<t[2][1] and t[1][1]<area[2][1] and area[1][2]<t[2][2] and t[1][2]<area[2][2] then clear=false end
      end
      if clear and surface.count_entities_filtered{area=area,force=npc.force,limit=1}==0
          and (ore_ok or surface.count_entities_filtered{area=area,type="resource",limit=1}==0)
          and surface.count_entities_filtered{area=area,force="enemy",limit=1}==0
          and surface.count_tiles_filtered{area=area,collision_mask="water_tile",limit=1}==0 then
        -- (cliffs in it blown away, as its charges would: a built-up plot has no open square without one)
        for _,e in ipairs(surface.find_entities_filtered{area=area,type={"tree","simple-entity","cliff"}}) do e.destroy() end
        return c,area
      end
    end
  end
end

-- What nuclear.py reads, from the game (checked here, in a game, for the first time).
function Proving.nuclear_facts()
  local out={reactors={},exchangers={},turbines={}}
  local function kw(p) return try(function() return p.get_max_energy_usage()*60/1000 end)
    or try(function() return p.max_energy_usage*60/1000 end) end
  -- (only what an engineer can place: the mods' hidden and scripted reactors left out)
  local function placeable(p)
    return not try(function() return p.hidden end) and #(try(function() return p.items_to_place_this end) or {})>0
  end
  for name,p in pairs(prototypes.get_entity_filtered{{filter="type",type="reactor"}}) do if placeable(p) then
    out.reactors[name]={kw=kw(p),neighbour_bonus=try(function() return p.neighbour_bonus end),
      max_temperature=try(function() return p.heat_buffer_prototype.max_temperature end)}
  end end
  for name,p in pairs(prototypes.get_entity_filtered{{filter="type",type="boiler"}}) do
    local target=try(function() return p.target_temperature end)
    if target and target>=500 and placeable(p) then out.exchangers[name]={kw=kw(p),target_temperature=target} end
  end
  for name,p in pairs(prototypes.get_entity_filtered{{filter="type",type="generator"}}) do
    local max_t=try(function() return p.maximum_temperature end)
    if max_t and max_t>=500 and placeable(p) then
      out.turbines[name]={max_kw=try(function() return p.get_max_power_output()*60/1000 end)
        or try(function() return p.max_power_output*60/1000 end),
        fluid_per_second=try(function() return p.fluid_usage_per_tick*60 end),max_temperature=max_t}
      local o=out.turbines[name]
      -- (none given when the turbine leaves it to its power: its power over the energy in a unit of steam at its top
      -- temperature)
      if not o.fluid_per_second and o.max_kw then
        local steam=prototypes.fluid["steam"]
        local per_unit=steam and try(function() return (max_t-steam.default_temperature)*steam.heat_capacity/1000 end)
        o.fluid_per_second=per_unit and per_unit>0 and math.floor(o.max_kw/per_unit*10)/10 or nil
      end
    end
  end
  local cell=prototypes.item["uranium-fuel-cell"]
  out.fuel_cell_mj=cell and try(function() return cell.fuel_value/1e6 end)
  local r=prototypes.recipe["uranium-processing"]
  out.uranium_processing={}
  for _,p in ipairs(r and r.products or {}) do
    -- (2.1: probability and independent_probability are 1 and the split is in shared_probability, a range of one
    -- roll: its chance is independent times the width of its range, NPC Designer)
    local chance=p.independent_probability or p.probability or 1
    local sp=p.shared_probability
    if type(sp)=="table" then
      local lo,hi=sp.min or sp[1],sp.max or sp[2]
      if lo and hi then chance=chance*(hi-lo) end
    end
    out.uranium_processing[#out.uranium_processing+1]={name=p.name,amount=p.amount,probability=chance}
  end
  return out
end

-- The heat its reactors give as they stand: each its own power times one plus the bonus for each reactor beside it (one
-- reactor's width away in a row or column, as the game counts them), the ones burning and hot.
local function nuclear_heat(surface,force)
  local reactors=surface.find_entities_filtered{force=force,type="reactor"}
  local mw,hot,burning=0,0,0
  for _,e in ipairs(reactors) do
    local p=e.prototype
    local w=try(function() return p.tile_width end) or 5
    local n=0
    for _,o in ipairs(reactors) do
      local dx,dy=math.abs(o.position.x-e.position.x),math.abs(o.position.y-e.position.y)
      if o~=e and ((dx==w and dy<0.1) or (dy==w and dx<0.1)) then n=n+1 end
    end
    local kw=(try(function() return p.get_max_energy_usage() end) or try(function() return p.max_energy_usage end) or 0)*60/1000
    mw=mw+kw/1000*(1+(try(function() return p.neighbour_bonus end) or 1)*n)
    if (try(function() return e.temperature end) or 0)>=500 then hot=hot+1 end
    if try(function() return e.burner and e.burner.currently_burning end) then burning=burning+1 end
  end
  return reactors,mw,hot,burning
end

Proving.STAGES.nuclear={research={"nuclear-power","uranium-processing","electric-energy-distribution-2"},
  items={centrifuge=4,["nuclear-reactor"]=4,["heat-exchanger"]=16,["steam-turbine"]=28,["heat-pipe"]=100,
    ["offshore-pump"]=4},
  steps={{"centrifuge","built","centrifuge"},{"u235","making","uranium-processing"},{"cells","making","uranium-fuel-cell"},
    {"reactor","built","nuclear-reactor"},{"fuelled","mark"},{"hot","mark"},{"turbines","mark"}},
  pass="turbines",creative=true,
  setup=function(npc,params,s,surface,home)
    s.creative_never={["uranium-fuel-cell"]=true}
    -- (the plant goes by the shore nearest home, NPC Designer's nuclear_plant: a long strip, 11x143 to 11x199. A pond
    -- and an open square of params.room tiles beside it only when asked; else where the engineer puts it is tested)
    local room,room_area,half
    if params.room then
      half=math.floor(params.room/2)
      room,room_area=Proving.open_square(npc,surface,home,half+14,nil,true)
      if not room then return nil,"no_open_square" end
      local tiles={}
      for dx=-5,5 do for dy=-5,5 do tiles[#tiles+1]={name="water",position={room.x+dx,room.y+half+8+dy}} end end
      surface.set_tiles(tiles,true)
    end
    local shore=surface.find_tiles_filtered{position=home,radius=params.shore_radius or 400,collision_mask="water_tile"}
    local near
    for _,tile in ipairs(shore) do
      local d=(tile.position.x-home.x)^2+(tile.position.y-home.y)^2
      if not near or d<near then near=d end
    end
    local field,field_area=Proving.open_square(npc,surface,home,22,room_area and {room_area} or nil)
    if not field then return nil,"no_open_field" end
    local n=0
    for x=field.x-20,field.x+19 do for y=field.y-20,field.y+19 do
      if surface.create_entity{name="uranium-ore",position={x+0.5,y+0.5},amount=params.ore_amount or 20000} then n=n+1 end
    end end
    -- (known to it as a scouted patch: a field put down by script is seen by no one, and the nuclear goals need it)
    Scouting.learn(npc,surface,field_area)
    -- (standing belts of sulfur and iron plate kept full, as an oil plant's sulfur belt and a smelting output would be:
    -- the fuel chain taps them by splitter, NPC Designer; params.belts={} none)
    s.fed_belts={}
    local taken={field_area,room_area}
    local belts={}
    for _,item in ipairs(params.belts or {"sulfur","iron-plate"}) do
      local c,area=Proving.open_square(npc,surface,home,12,taken)
      if not c then return nil,"no_room_for_the_"..item.."_belt" end
      taken[#taken+1]=area
      for dx=-10,9 do
        local b=surface.create_entity{name="transport-belt",position={c.x+dx+0.5,c.y+0.5},direction=defines.direction.east,
          force=npc.force}
        if b then s.fed_belts[#s.fed_belts+1]={belt=b,item=item} end
      end
      -- (a machine making it puts it on the belt's head through an inserter, as on a real line: the fuel chain's tap
      -- finds a belt by what feeds it, NPC Designer)
      local head=Proving.belt_head(npc,surface,{x=c.x-10,y=c.y},item)
      if not head then return nil,"no_machine_for_the_"..item.."_belt" end
      belts[item]={x=c.x-10-home.x,y=c.y-home.y,length=20,machine=head.machine.name}
      if item=="sulfur" then
        -- (its oil processing counted as built, as a checkpoint with an oil plant would have it: nuclear-fuel waits on it)
        local plan={plan_id="proving-oil-processing-"..game.tick,purpose="oil-processing",source="proving",order={},
          entities={},stages={{id="sulfur",status="complete",completed_tick=game.tick,entity_ids={},supplies={},witnesses={}}}}
        for id,e in pairs({machine=head.machine,inserter=head.inserter}) do
          plan.order[#plan.order+1]=id
          plan.entities[id]={id=id,entity=e,prototype=e.name,item=e.name,world=e.position,failures=0}
          table.insert(plan.stages[1].entity_ids,id)
        end
        table.sort(plan.order)
        npc.design_history=npc.design_history or {}
        table.insert(npc.design_history,plan)
      end
    end
    -- (power short, as a growing base's would read: the nuclear goal is offered on a shortfall, params.load 0 none)
    local load,load_why
    if (params.load or 1.5)>0 then
      load,load_why=Proving.power_load(npc,surface,home,params.load or 1.5)
      if not load then return nil,"power_load:"..tostring(load_why) end
      s.power_short=true
    end
    s.nuclear={reactors_planned=params.reactors or 4,samples=0,idle_hot=0,out_after_fuelled=0,spent_max=0,
      cells_minutes={},cells_last=nil}
    local facts=Proving.nuclear_facts()
    log("[npc-factions] proving nuclear facts: "..helpers.table_to_json(facts))
    return {room=room and {x=room.x-home.x,y=room.y-home.y,size=2*half} or nil,
      pond=room and {x=room.x-home.x,y=room.y+half+8-home.y} or nil,shore_tiles=near and math.floor(math.sqrt(near)) or nil,
      uranium={x=field.x-home.x,y=field.y-home.y,tiles=n},reactors=s.nuclear.reactors_planned,load=load,belts=belts,
      facts=facts}
  end,
  tick=function(npc,s,tick,surface)
    -- (its shortfall reported as the corrector would, which counts low-power buildings a slice at a time and noted it
    -- only at minute 19 of 20: while the stage's load stands and nothing new makes up for it)
    if s.power_short and not s.marks.turbines then npc.power_short=tick end
    for _,f in ipairs(s.fed_belts or {}) do   -- (each second: its fed belts topped up)
      if f.belt.valid then
        for i=1,2 do
          local line=f.belt.get_transport_line(i)
          for _=1,4 do if not line.insert_at_back({name=f.item,count=1}) then break end end
        end
      end
    end
    local nu=s.nuclear
    if not nu or tick%600~=0 then return end
    local reactors,mw,hot,burning=nuclear_heat(surface,npc.force)
    if burning>0 and not s.marks.fuelled then s.marks.fuelled=tick end
    if hot>0 and not s.marks.hot then s.marks.hot=tick end
    local made=0
    for _,t in ipairs(surface.find_entities_filtered{force=npc.force,type="generator"}) do
      local p=t.prototype
      if (try(function() return p.maximum_temperature end) or 0)>=500 then
        made=made+(try(function() return t.energy_generated_last_tick end) or 0)*60/1e6
      end
    end
    nu.turbine_mw,nu.heat_mw,nu.hot,nu.burning,nu.reactors=made,mw,hot,burning,#reactors
    if made>0 and not s.marks.turbines then s.marks.turbines=tick end
    -- (once fuelled: a reactor with no cell burning or waiting is out; spent cells left inside pile up)
    if s.marks.fuelled then
      for _,e in ipairs(reactors) do
        local fuel=try(function() return e.get_fuel_inventory().get_item_count() end) or 0
        if fuel==0 and not try(function() return e.burner.currently_burning end) then nu.out_after_fuelled=nu.out_after_fuelled+1 end
        local spent=try(function() return e.get_burnt_result_inventory().get_item_count() end) or 0
        nu.spent_max=math.max(nu.spent_max,spent)
      end
    end
    -- (reactors hot, and an exchanger or turbine of its standing idle)
    if hot>0 then
      nu.samples=nu.samples+1
      local idle=false
      for _,e in ipairs(surface.find_entities_filtered{force=npc.force,type={"boiler","generator"}}) do
        local p=e.prototype
        local nuclear=(try(function() return p.target_temperature end) or 0)>=500 or (try(function() return p.maximum_temperature end) or 0)>=500
        if nuclear and e.status~=defines.entity_status.working then idle=true end
      end
      if idle then nu.idle_hot=nu.idle_hot+1 end
    end
    if tick%3600==0 then
      local cells=0
      for _,e in ipairs(surface.find_entities_filtered{force=npc.force,type="assembling-machine"}) do
        local r=try(function() return e.get_recipe() end)
        if r and r.name=="uranium-fuel-cell" then cells=cells+(e.products_finished or 0) end
      end
      if nu.cells_last and #nu.cells_minutes<60 then nu.cells_minutes[#nu.cells_minutes+1]=cells-nu.cells_last end
      nu.cells_last=cells
    end
  end,
  score=function(npc,s,tick,surface)
    local nu=s.nuclear or {}
    local exchangers,hot_ex=0,0
    for _,e in ipairs(surface.find_entities_filtered{force=npc.force,type="boiler"}) do
      if (try(function() return e.prototype.target_temperature end) or 0)>=500 then
        exchangers=exchangers+1
        if (try(function() return e.temperature end) or 0)>=500 then hot_ex=hot_ex+1 end
      end
    end
    return {reactors=nu.reactors or 0,reactors_hot=nu.hot or 0,reactors_burning=nu.burning or 0,
      exchangers=exchangers,exchangers_hot=hot_ex,turbine_mw=nu.turbine_mw and math.floor(nu.turbine_mw*10)/10 or 0,
      heat_mw=nu.heat_mw and math.floor(nu.heat_mw*10)/10 or 0,
      mw_share=(nu.heat_mw or 0)>0 and math.floor((nu.turbine_mw or 0)/nu.heat_mw*100)/100 or nil,
      cells_per_minute=nu.cells_minutes or {},cells_out_after_fuelled=nu.out_after_fuelled or 0,
      spent_cells_max_in_reactor=nu.spent_max or 0,idle_while_hot_samples=nu.idle_hot or 0,hot_samples=nu.samples or 0}
  end}

S["stage"]={
  setup=function(npc,params,s)
    local spec=Proving.STAGES[params.stage or ""]
    if not spec then return nil,"unknown_stage" end
    local surface=npc.character.surface
    local home=npc.home or npc.character.position
    local missing={}
    -- (params.research: more done at the start than the stage's own, e.g. the oil chain "done" rather than "due")
    local wanted={}
    for _,name in ipairs(spec.research) do wanted[#wanted+1]=name end
    for _,name in ipairs(type(params.research)=="table" and params.research or {}) do wanted[#wanted+1]=name end
    local researched=with_prerequisites(npc.force,wanted)
    for _,name in ipairs(spec.research) do if not npc.force.technologies[name] then missing[#missing+1]="tech:"..name end end
    research(npc.force,researched)
    -- (power: steam handed to it on the flat map; from a checkpoint, the power its base already has, params.power=false)
    if params.power~=false then
      local plan,why=Proving.steam_power(npc,surface,home)
      if not plan then return nil,why end
      s.power_plan=plan.plan_id
    end
    -- (a practice world is made once, tools/make_practice_world.py: its field, interface and chest kept in storage; the
    -- scenario on its save only starts the scoring)
    storage.practice=storage.practice or {}
    local made_before=spec.practice and storage.practice[params.stage]
    -- (an energy interface on its network, by its pole nearest home: the stage, not the power, is what is tested)
    -- (params.boost_mw=0: none, the base's own power is then part of what is tested)
    local eei
    local boost=params.boost_mw==nil and 20 or tonumber(params.boost_mw) or 0
    if made_before then boost=made_before.boost_mw or 0 end
    if not made_before and boost>0 and prototypes.entity["electric-energy-interface"] then
      local poles=surface.find_entities_filtered{force=npc.force,type="electric-pole",position=home,radius=96}
      table.sort(poles,function(a,b)
        local da=(a.position.x-home.x)^2+(a.position.y-home.y)^2
        local db=(b.position.x-home.x)^2+(b.position.y-home.y)^2
        if da~=db then return da<db end
        return a.position.x<b.position.x or (a.position.x==b.position.x and a.position.y<b.position.y)
      end)
      for _,pole in ipairs(poles) do
        if not eei and pole.valid and (pole.electric_network_id or 0)>0 then
          local p=surface.find_non_colliding_position("electric-energy-interface",pole.position,2,0.5)
          eei=p and surface.create_entity{name="electric-energy-interface",position=p,force=npc.force}
        end
      end
      if eei then
        eei.electric_buffer_size=1e8
        eei.power_production=boost*1e6/60
        eei.energy=1e8
      end
    end
    -- (the stage's machines and the plates to make the rest: in its pockets, the plates also in a chest by home)
    local items={}
    for k,v in pairs(MACHINES) do items[k]=v end
    for k,v in pairs(spec.items or {}) do items[k]=v end
    for name in pairs(items) do if not prototypes.item[name] then missing[#missing+1]="item:"..name; items[name]=nil end end
    local names={}
    for name in pairs(items) do names[#names+1]=name end
    table.sort(names)   -- (the same order every run: what a full pocket leaves out is the same)
    local given={}
    local no_kit=spec.kit==false   -- (nothing given: what it has is what it made, U-2)
    if not made_before and not no_kit then   -- (on the practice save its kit was given when the world was made)
      for _,name in ipairs(names) do given[name]=npc.character.insert{name=name,count=items[name]} end
    end
    local chest_at=not made_before and not no_kit and surface.find_non_colliding_position("steel-chest",{x=home.x+6,y=home.y+6},12,1)
    local chest=chest_at and surface.create_entity{name="steel-chest",position=chest_at,force=npc.force}
    if not chest and not made_before and not no_kit then return nil,"no_room_for_the_chest" end
    local stocked,short={},{}
    local function stock(name,count)
      if made_before or no_kit then return end
      if not prototypes.item[name] then missing[#missing+1]="item:"..name; return end
      local n=chest.insert{name=name,count=count}
      stocked[name]=(stocked[name] or 0)+n
      if n<count then short[#short+1]="chest:"..name end
    end
    local plates={}
    for name in pairs(PLATES) do plates[#plates+1]=name end
    table.sort(plates)
    for _,name in ipairs(plates) do stock(name,PLATES[name]) end
    -- (the silo: what a rocket part is made of, from the recipe the game has: Space Age's or base's)
    local inputs={}
    if spec.part_inputs then
      local r=prototypes.recipe["rocket-part"]
      for _,ing in ipairs(r and r.ingredients or {}) do
        if ing.type=="item" then stock(ing.name,spec.part_inputs*ing.amount); inputs[#inputs+1]=ing.name end
      end
      if not r then missing[#missing+1]="recipe:rocket-part" end
    end
    -- (a crude-oil field near, on open ground: the first 40-tile square within reach of home with none of its
    -- buildings and no cliff, its trees and rocks cleared; five wells, and a pond 16 tiles off unless params.pond is
    -- false: the oil layout pipes water from a pond within 40 tiles of its well)
    local wells,field=0,nil
    if made_before then wells,field=made_before.wells,made_before.field end
    if not made_before and spec.oil and prototypes.entity["crude-oil"] then
      for r=40,100,12 do
        for k=0,7 do
          if not field then
            local a=k*math.pi/4
            local c={x=math.floor(home.x+math.cos(a)*r),y=math.floor(home.y+math.sin(a)*r)}
            local area={{c.x-20,c.y-20},{c.x+20,c.y+20}}
            if surface.count_entities_filtered{area=area,force=npc.force,limit=1}==0
                and surface.count_entities_filtered{area=area,type="cliff",limit=1}==0
                and surface.count_entities_filtered{area=area,force="enemy",limit=1}==0
                and surface.count_tiles_filtered{area=area,collision_mask="water_tile",limit=1}==0 then
              field=c
              for _,e in ipairs(surface.find_entities_filtered{area=area,type={"tree","simple-entity"}}) do e.destroy() end
            end
          end
        end
      end
      if field then
        for k=0,4 do
          local p={x=field.x-7+(k%3)*7,y=field.y-4+math.floor(k/3)*8}
          if surface.create_entity{name="crude-oil",position=p,amount=params.oil_amount or 600000} then wells=wells+1 end
        end
        if params.pond~=false then
          local tiles={}
          for dx=-3,3 do for dy=-3,3 do tiles[#tiles+1]={name="water",position={field.x+dx,field.y+16+dy}} end end
          surface.set_tiles(tiles,true)
        end
      else missing[#missing+1]="ground:no_open_oil_field" end
    elseif spec.oil and not made_before then missing[#missing+1]="entity:crude-oil" end
    if spec.practice and not made_before then
      storage.practice[params.stage]={field=field,wells=wells,boost_mw=eei and boost or 0,tick=game.tick}
    end
    -- (a stage set up short is no test of it: what its own items, the silo and the wells need, or the setup fails)
    for _,name in ipairs(names) do
      if not made_before and spec.items and spec.items[name] and (given[name] or 0)<spec.items[name] then
        short[#short+1]="pockets:"..name
      end
    end
    if spec.oil and wells<5 then short[#short+1]="wells:"..wells end
    if #missing>0 or #short>0 then
      return nil,"short:"..table.concat(missing," ")..(#missing>0 and #short>0 and " " or "")..table.concat(short," ")
    end
    -- (what its machines and silos had made before: a step counts new work only, not a checkpoint's history)
    s.baseline={}
    for _,e in ipairs(surface.find_entities_filtered{force=npc.force,type={"assembling-machine","furnace","rocket-silo"}}) do
      if e.unit_number then
        s.baseline[e.unit_number]={made=e.products_finished or 0,parts=try(function() return e.rocket_parts end) or 0,
          recipe=(try(function() return e.get_recipe() end) or {}).name,at_setup=true}
      end
    end
    s.boost_mw=made_before and (made_before.boost_mw or 0) or (eei and boost or 0)
    -- (params.fresh_ring: its wall ring taken away first, to be built again as its planners now would)
    local cleared=params.fresh_ring and Proving.clear_ring(npc,game.tick) or nil
    -- (a stage's own part, as other stages need it: spec.setup(npc,params,s,surface,home) -> extra summary, or nil, why)
    local extra
    if spec.setup then
      local why
      extra,why=spec.setup(npc,params,s,surface,home)
      if extra==nil then return nil,"stage_setup:"..tostring(why) end
    end
    s.stage,s.marks,s.designs,s.blocked=params.stage,{},{},{}
    s.creative=spec.creative or nil
    s.status,s.blue_minutes={},{}
    if spec.oil then s.blue_total=Proving.blue_count(surface,npc.force) end   -- (what stood made before: not counted)
    s.limit=params.minutes or 30
    return {stage=params.stage,researched=#researched,boost_mw=s.boost_mw,
      power=(params.power~=false and "steam handed" or "the base's own")..(eei and (" + "..boost.." MW interface") or ""),
      given=given,stocked=stocked,part_inputs=inputs,wells=wells,practice_world=made_before and true or nil,
      creative=spec.creative or nil,stage_setup=extra,fresh_ring=cleared,
      field=field and {x=field.x-home.x,y=field.y-home.y} or nil,missing=missing}
  end,
  tick=function(npc,s,tick)
    if tick%60~=0 or not (npc.character and npc.character.valid) then return end
    Proving.loss_answers(npc,s,tick)
    -- what it set out to build, and every plan given up with why (each request's change once, each second)
    local d=npc.design
    local now=d and (tostring(d.purpose)..":"..tostring(d.status)..":"..tostring(d.request_id)) or "none"
    if now~=s.last_design then
      s.last_design=now
      if #s.designs<80 then
        s.designs[#s.designs+1]=string.format("%.1f %s:%s",(tick-s.start)/3600,tostring(d and d.purpose),tostring(d and d.status))
      end
      if d and d.status=="blocked" and #s.blocked<40 then s.blocked[#s.blocked+1]=tostring(d.purpose)..": "..tostring(d.reason) end
    end
    if s.creative then creative(npc,s) end
    local surface,force=npc.character.surface,npc.force
    if tick%3600==0 and Proving.STAGES[s.stage].oil then
      for _,e in ipairs(surface.find_entities_filtered{force=force,name={"pumpjack","oil-refinery","chemical-plant"}}) do
        local per=s.status[e.name] or {}
        s.status[e.name]=per
        local k=status_name(e.status)
        per[k]=(per[k] or 0)+1
      end
      -- (the blue chain's assemblers: how they spend their time, and what a starved one lacks, F-20)
      for _,e in ipairs(surface.find_entities_filtered{force=force,type="assembling-machine"}) do
        local r=e.get_recipe()
        if r and Proving.WATCH[r.name] then
          local per=s.status["am:"..r.name] or {}
          s.status["am:"..r.name]=per
          local k=status_name(e.status)
          per[k]=(per[k] or 0)+1
          if e.status==defines.entity_status.item_ingredient_shortage then
            local inv=e.get_inventory(defines.inventory.crafter_input or defines.inventory.assembling_machine_input)
            for _,ing in ipairs(r.ingredients) do
              if ing.type=="item" and inv and inv.get_item_count(ing.name)<ing.amount then
                per["short:"..ing.name]=(per["short:"..ing.name] or 0)+1
              end
            end
          end
        end
      end
      -- (blue packs made this minute, against what its blue machines are laid out to make)
      Proving.blue_minute(s,surface,force,tick)
    end
    if Proving.STAGES[s.stage].tick then Proving.STAGES[s.stage].tick(npc,s,tick,surface) end
    if tick%300~=0 then return end
    local spec=Proving.STAGES[s.stage]
    for _,step in ipairs(spec.steps) do
      local key,how,what=step[1],step[2],step[3]
      if not s.marks[key] then
        local done
        if how=="built" then done=surface.count_entities_filtered{force=force,name=what,limit=1}>0
        elseif how=="running" then
          for _,e in ipairs(surface.find_entities_filtered{force=force,name=what}) do
            local r=try(function() return e.get_recipe() end)
            if made_since(s,e,r and r.name) then done=true end
          end
        elseif how=="making" then done=producing(s,surface,force,what,nil)
        elseif how=="set" then done=set_since(s,surface,force,what)
        elseif how=="parts" then
          for _,e in ipairs(surface.find_entities_filtered{force=force,name=what}) do
            local b=e.unit_number and s.baseline[e.unit_number]
            if (try(function() return e.rocket_parts end) or 0)>(b and b.parts or 0)
                or (e.products_finished or 0)>(b and b.made or 0) then done=true end
          end
        end
        if done then s.marks[key]=tick end
      end
    end
  end,
  score=function(npc,s,tick)
    local spec=Proving.STAGES[s.stage]
    local out={stage=s.stage,steps={},designs=s.designs,blocked=s.blocked}
    -- (its turrets an arm of its own feeds: standing, with power, with ammunition on the belt it picks from; the ammo
    -- loop on a normal map, D-24)
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    local fed={arms=0,powered=0,stocked=0}
    for _,t in ipairs(surface.find_entities_filtered{force=npc.force,type="ammo-turret"}) do
      for _,arm in ipairs(surface.find_entities_filtered{force=npc.force,type="inserter",position=t.position,radius=3}) do
        if arm.drop_target==t then
          fed.arms=fed.arms+1
          if arm.energy>0 then fed.powered=fed.powered+1 end
          local p=arm.pickup_target
          if p and p.valid and p.type=="transport-belt" then
            local n=0
            for line=1,p.get_max_transport_line_index() do n=n+p.get_transport_line(line).get_item_count() end
            if n>0 then fed.stocked=fed.stocked+1 end
          end
        end
      end
    end
    -- (and what feeds its belts: chests an arm takes from onto a belt, the ammunition in them, that arm powered)
    fed.feeds,fed.feed_ammo,fed.feed_powered=0,0,0
    for _,arm in ipairs(surface.find_entities_filtered{force=npc.force,type="inserter"}) do
      local from,to=arm.pickup_target,arm.drop_target
      if from and from.valid and from.type=="container" and to and to.valid and to.type=="transport-belt" then
        fed.feeds=fed.feeds+1
        if arm.energy>0 then fed.feed_powered=fed.feed_powered+1 end
        local inv=from.get_inventory(defines.inventory.chest)
        for _,it in ipairs(inv and inv.get_contents() or {}) do
          local proto=prototypes.item[it.name]
          if proto and proto.type=="ammo" then fed.feed_ammo=fed.feed_ammo+it.count end
        end
      end
    end
    out.turret_arms=fed
    out.loss_answered=Proving.loss_summary(s,game.tick)
    for _,step in ipairs(spec.steps) do
      out.steps[step[1]]=s.marks[step[1]] and minutes(s.marks[step[1]]-s.start) or false
      out[step[1].."_minute"]=s.marks[step[1]] and minutes(s.marks[step[1]]-s.start) or nil
    end
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    local counts={}
    for _,name in ipairs({"pumpjack","oil-refinery","chemical-plant","assembling-machine-1","assembling-machine-2",
        "rocket-silo","storage-tank","pipe","electric-mining-drill"}) do
      if prototypes.entity[name] then counts[name]=surface.count_entities_filtered{force=npc.force,name=name} end
    end
    out.built=counts
    out.passed=s.marks[spec.pass]~=nil
    out.boost_mw=s.boost_mw
    if spec.oil then
      out.fluids=fluid_balance(surface,npc.force)
      out.status=s.status
      local made,design=Proving.blue_count(surface,npc.force)
      out.blue_per_minute=s.blue_minutes
      out.blue_since_last=made-(s.blue_total or made)   -- (the minute under way, not yet in the list)
      out.blue_design_per_minute=math.floor(design*10)/10
    end
    if s.creative then out.creative_given,out.creative_no_room=s.creative_given or {},s.creative_full or {} end
    if spec.score then for k,v in pairs(spec.score(npc,s,tick,surface) or {}) do out[k]=v end end
    out.engineer_alive=npc.character~=nil and npc.character.valid
    -- (a practice world runs its whole time: the rates and the steps after the first pack are what it measures)
    out.done=(out.passed and not spec.practice) or (minutes(tick-s.start) or 0)>=s.limit
    return out
  end}

-- g. A base grown in creative mode (OP-8b, the practice world's mid-game base): the engineer plans its own base as it
-- would anyway (opening, power, smelting, the main bus, science, walls and turrets), and each plan it is given is put
-- down by script the moment it is accepted (an instant build), so a base hours in the making stands in minutes, all of
-- it its own plans (it adopts every piece). Large ore fields round home, research through red and green science.
-- Done once the bus stands and red and green are made at about params.rate a minute each, by belt-fed labs' measure.
function Proving.stamp(npc,s)
  local d=npc.design
  if not (d and d.status=="building" and d.plan and d.plan.plan_id) then return end
  if s.stamped_plans[d.plan.plan_id] then return end
  s.stamped_plans[d.plan.plan_id]=true
  local surface=npc.character.surface
  local placed,failed=0,0
  for _,id in ipairs(d.plan.order or {}) do
    local e=d.plan.entities[id]
    if e and not (e.entity and e.entity.valid) and prototypes.entity[e.prototype] then
      local box=try(function() return prototypes.entity[e.prototype].collision_box end)
      if box then   -- (trees and rocks under it go, as it would mine them)
        local area={{e.world.x+box.left_top.x-0.1,e.world.y+box.left_top.y-0.1},
          {e.world.x+box.right_bottom.x+0.1,e.world.y+box.right_bottom.y+0.1}}
        for _,o in ipairs(surface.find_entities_filtered{area=area,type={"tree","simple-entity","item-entity"}}) do o.destroy() end
      end
      local spec={name=e.prototype,position=e.world,direction=e.direction,force=npc.force,recipe=e.recipe}
      if e.belt_to_ground_type then spec.type=e.belt_to_ground_type end
      local made=try(function() return surface.create_entity(spec) end)
      if made then
        placed=placed+1
        -- (what settle() applies to a piece it builds: recipe and the planned turn of a fluid machine, inserter pickup
        -- and drop, filters, splitter priorities, a chest's limit; adopted, a stamped piece is never settled)
        if e.recipe and made.type=="assembling-machine" then
          pcall(function() made.set_recipe(e.recipe) end)
          if e.direction and made.direction~=e.direction then pcall(function() made.direction=e.direction end) end
        end
        pcall(DesignedStart.apply_settings,made,e.settings)
        if e.bar and made.type=="container" then
          pcall(function() made.get_inventory(defines.inventory.chest).set_bar(e.bar+1) end)
        end
      else failed=failed+1 end
    end
  end
  s.stamped=(s.stamped or 0)+placed
  s.stamp_failed=(s.stamp_failed or 0)+failed
end

-- The research a trigger completes, open now, for the triggers the instant build and creative supply skip: crafting
-- by hand (it is handed the items) and building (a stamped piece raises no build). Done, as they would be where it
-- crafts and builds by hand: without them the tech tree stayed shut and its lab stood with nothing to research for two
-- hundred game minutes. Others (mining crude oil for oil processing, a launch) still come as they do in a game.
Proving.SKIPPED_TRIGGERS={["craft-item"]=true,["craft-fluid"]=true,["build-entity"]=true}
local function triggers_done(force)
  local done={}
  for name,t in pairs(force.technologies) do
    local trigger=t.enabled and not t.researched and try(function() return t.prototype.research_trigger end)
    if trigger and Proving.SKIPPED_TRIGGERS[trigger.type] then
      local ok=true
      for _,p in pairs(t.prerequisites) do if not p.researched then ok=false end end
      if ok then t.researched=true; done[#done+1]=name end
    end
  end
  table.sort(done)
  return done
end

-- Science made by its machines this minute, and how many inserters take from a belt into a lab of it (the layout:
-- whether they move packs is in the rates).
local function science_rates(s,surface,force)
  local now={}
  for _,e in ipairs(surface.find_entities_filtered{force=force,type="assembling-machine"}) do
    local r=try(function() return e.get_recipe() end)
    if r and (r.name=="automation-science-pack" or r.name=="logistic-science-pack") then
      now[r.name]=(now[r.name] or 0)+(e.products_finished or 0)
    end
  end
  local belt_fed=0
  for _,i in ipairs(surface.find_entities_filtered{force=force,type="inserter"}) do
    local from,to=i.pickup_target,i.drop_target
    if to and to.valid and to.type=="lab" and from and from.valid
        and (from.type=="transport-belt" or from.type=="underground-belt" or from.type=="splitter") then
      belt_fed=belt_fed+1
    end
  end
  local rates={}
  for name,n in pairs(now) do rates[name]=n-((s.science_last or {})[name] or n) end
  s.science_last=now
  return rates,belt_fed
end

-- Where the plan in hand stands: its stage, each stage's status and witnesses, its pieces built (what a stall waits on).
function Proving.plan_state(npc)
  local d=npc.design
  if not (d and d.plan) then return nil end
  local out={purpose=d.purpose,status=d.status,stage=d.stage_index,reason=d.reason,stages={}}
  for i,st in ipairs(d.plan.stages or {}) do
    local built,total=0,0
    for _,id in ipairs(st.entity_ids or {}) do
      total=total+1
      local e=d.plan.entities[id]
      if e and e.entity and e.entity.valid then built=built+1 end
    end
    local w={}
    for _,x in ipairs(st.witnesses or {}) do w[#w+1]=tostring(x.kind) end
    out.stages[i]={id=st.id,status=st.status,built=built.."/"..total,witnesses=table.concat(w,","),
      supplies=#(st.supplies or {})}
  end
  return out
end

S["grow"]={
  setup=function(npc,params,s)
    local surface=npc.character.surface
    local home=npc.home or npc.character.position
    -- (none given unless params.research: its labs research as in a game. Given red and green's research, its lab had
    -- only research wanting green packs it could not yet make, its lab plan's witness never came, and the base stood
    -- still for two hundred game minutes)
    local tier=with_prerequisites(npc.force,params.research or {})
    research(npc.force,tier)
    s.triggers=triggers_done(npc.force)
    -- (large fields round home, out of the opening's way: a bus smelts a full belt a plate)
    local fields={}
    local ores={{"iron-ore",0},{"copper-ore",1},{"coal",2},{"stone",3}}
    for _,o in ipairs(ores) do
      if prototypes.entity[o[1]] then
        local a=o[2]*math.pi/2+math.pi/4
        local c={x=math.floor(home.x+math.cos(a)*(params.field_distance or 90)),
          y=math.floor(home.y+math.sin(a)*(params.field_distance or 90))}
        local half=math.floor((params.field_size or 48)/2)
        for _,e in ipairs(surface.find_entities_filtered{area={{c.x-half,c.y-half},{c.x+half,c.y+half}},
            type={"tree","simple-entity"}}) do e.destroy() end
        local n=0
        for x=c.x-half,c.x+half-1 do for y=c.y-half,c.y+half-1 do
          if surface.create_entity{name=o[1],position={x+0.5,y+0.5},amount=params.ore_amount or 20000} then n=n+1 end
        end end
        fields[o[1]]={x=c.x-home.x,y=c.y-home.y,tiles=n}
      end
    end
    s.stamped_plans,s.stamped,s.stamp_failed={},0,0
    s.creative=true
    -- (power given, as the practice world gives it: an energy interface put on its network once it has one, so power
    -- running short never sends it round a power goal that cannot be planned on its plot, run grow-bus 2026-10-29)
    s.boost_mw=params.boost_mw==nil and 50 or tonumber(params.boost_mw) or 0
    s.rate=params.rate or 50
    s.limit=params.minutes or 240
    s.marks,s.designs,s.blocked,s.science={},{},{},{}
    return {fields=fields,researched=#tier}
  end,
  tick=function(npc,s,tick)
    if not (npc.character and npc.character.valid) then return end
    Proving.stamp(npc,s)
    if tick%60~=0 then return end
    creative(npc,s)
    local d=npc.design
    local now=d and (tostring(d.purpose)..":"..tostring(d.status)..":"..tostring(d.request_id)) or "none"
    if now~=s.last_design then
      s.last_design=now
      if #s.designs<120 then
        s.designs[#s.designs+1]=string.format("%.1f %s:%s",(tick-s.start)/3600,tostring(d and d.purpose),tostring(d and d.status))
      end
      if d and d.status=="blocked" and #s.blocked<40 then s.blocked[#s.blocked+1]=tostring(d.purpose)..": "..tostring(d.reason) end
    end
    if tick%3600~=0 then return end
    creative_floor(npc,s)
    for _,name in ipairs(triggers_done(npc.force)) do s.triggers[#s.triggers+1]=name end
    if s.boost_mw>0 and not (s.eei and s.eei.valid) and prototypes.entity["electric-energy-interface"] then
      local home=npc.home or npc.character.position
      for _,pole in ipairs(npc.character.surface.find_entities_filtered{force=npc.force,type="electric-pole",position=home,
          radius=64}) do
        if not (s.eei and s.eei.valid) and (pole.electric_network_id or 0)>0 then
          local p=npc.character.surface.find_non_colliding_position("electric-energy-interface",pole.position,2,0.5)
          s.eei=p and npc.character.surface.create_entity{name="electric-energy-interface",position=p,force=npc.force}
          if s.eei then
            s.eei.electric_buffer_size=1e9
            s.eei.power_production=s.boost_mw*1e6/60
          end
        end
      end
    end
    local surface=npc.character.surface
    local rates,belt_fed=science_rates(s,surface,npc.force)
    s.science[#s.science+1]={minute=minutes(tick-s.start),red=rates["automation-science-pack"] or 0,
      green=rates["logistic-science-pack"] or 0,belt_fed_labs=belt_fed}
    while #s.science>20 do table.remove(s.science,1) end
    local bus=false
    for _,p in ipairs(npc.design_history or {}) do if p.purpose=="bus-base" then bus=true end end
    if bus and not s.marks.bus then s.marks.bus=tick end
    -- (red science automated, the long runs' red-science milestone: red at the rate, whatever feeds it)
    if (rates["automation-science-pack"] or 0)>=s.rate and not s.marks.red then s.marks.red=tick end
    if bus and (rates["automation-science-pack"] or 0)>=s.rate and (rates["logistic-science-pack"] or 0)>=s.rate
        and belt_fed>0 and not s.marks.science then s.marks.science=tick end
  end,
  score=function(npc,s,tick)
    local last=s.science[#s.science] or {}
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    local counts={}
    for _,t in ipairs({"assembling-machine","furnace","mining-drill","lab","ammo-turret","wall","transport-belt","boiler",
        "generator","electric-pole"}) do
      counts[t]=surface.count_entities_filtered{force=npc.force,type=t}
    end
    -- (bus_minute: a bus-base plan finished, its witnesses passed; science_minute: the rates above, with an inserter
    -- off a belt into a lab)
    return {bus_minute=s.marks.bus and minutes(s.marks.bus-s.start) or nil,
      red_minute=s.marks.red and minutes(s.marks.red-s.start) or nil,
      science_minute=s.marks.science and minutes(s.marks.science-s.start) or nil,
      red_per_minute=last.red,green_per_minute=last.green,lab_belt_inserters=last.belt_fed_labs,science=s.science,
      stamped=s.stamped,stamp_failed=s.stamp_failed,plans=#(npc.design_history or {}),built=counts,triggers=s.triggers,
      phase=npc.phase,plan_now=Proving.plan_state(npc),
      research_now=npc.force.current_research and npc.force.current_research.name or nil,
      research_queue=#(try(function() return npc.force.research_queue end) or {}),
      labs=(function() local out={} for _,l in ipairs(surface.find_entities_filtered{force=npc.force,type="lab"}) do
        out[#out+1]=status_name(l.status)..":"..(l.get_inventory(defines.inventory.lab_input) and
        l.get_inventory(defines.inventory.lab_input).get_item_count() or 0) end return out end)(),
      designs=s.designs,blocked=s.blocked,creative_given=s.creative_given or {},
      engineer_alive=npc.character~=nil and npc.character.valid,
      done=s.marks.science~=nil or (minutes(tick-s.start) or 0)>=s.limit}
  end}

-- f. A blueprint from the user's packs as an imported plan (F-16b, tools/proving.py imported-block): its hologram
-- (ghosts, drawn for the player's force when it is not an ally), built piece by piece from the pockets, and no ghost
-- left at its end, on a forced hand-back (params["end"]="handback") or a move ("relocate").
-- W-26, early robots: given its kit (modular armor, a personal roboport, power, construction robots) and the items, a
-- plan of furnaces and chests with trees standing on some of their spots: its robots clear the trees and put the plan
-- up from its pockets while it stands in their reach; what it builds by hand is what they did not.
S["robot-build"]={
  setup=function(npc,params,s)
    local surface=npc.character.surface
    local ok,why=DesignedStart.Robots.grant(npc,game.tick)
    if not ok then return nil,"no_kit:"..tostring(why) end
    local hx,hy=math.floor(npc.home.x),math.floor(npc.home.y)
    local n,chests=math.floor(params.pieces or 12),math.floor(params.chests or 4)
    local entities,ids={},{}
    for i=1,n do
      entities[#entities+1]={id="furnace"..i,prototype="stone-furnace",direction=0,
        position={x=hx+8+((i-1)%6)*3,y=hy-6+math.floor((i-1)/6)*3}}
    end
    for i=1,chests do
      entities[#entities+1]={id="chest"..i,prototype="wooden-chest",direction=0,
        position={x=hx+8.5+(i-1)*3,y=hy+2.5}}
    end
    -- (what its ghost must carry: an assembler's recipe, an underground's ends; Codex, W-26)
    local machines=math.floor(params.assemblers or 0)
    if machines>0 then research(npc.force,{"automation","logistics"}) end
    for i=1,machines do
      entities[#entities+1]={id="assembler"..i,prototype="assembling-machine-1",direction=0,recipe="iron-gear-wheel",
        position={x=hx+9.5+(i-1)*4,y=hy-11.5}}
    end
    if params.underground then
      local east=defines.direction.east
      entities[#entities+1]={id="under_in",prototype="underground-belt",direction=east,belt_to_ground_type="input",
        position={x=hx+20.5,y=hy+2.5}}
      entities[#entities+1]={id="under_out",prototype="underground-belt",direction=east,belt_to_ground_type="output",
        position={x=hx+24.5,y=hy+2.5}}
    end
    local area={{hx+5,hy-14},{hx+32,hy+5}}
    s.area=area
    for _,e in ipairs(surface.find_entities_filtered{area=area}) do
      if e.valid and e.type~="character" and e.type~="resource" then e.destroy() end
    end
    s.trees={}
    for i=1,math.min(math.floor(params.trees or 4),#entities) do
      local t=surface.create_entity{name=params.tree or "tree-01",position=entities[i*2-1].position}
      if t then s.trees[#s.trees+1]=t end
    end
    for _,e in ipairs(entities) do ids[#ids+1]=e.id end
    local plan,why2=hand_plan(npc,"proving-robots",entities,{{id="robots",depends_on={},entity_ids=ids}})
    if not plan then return nil,why2 end
    give(npc,{["stone-furnace"]=n,["wooden-chest"]=chests,["assembling-machine-1"]=machines,
      ["underground-belt"]=params.underground and 2 or 0})
    s.plan_id,s.pieces=plan.plan_id,#entities
    npc.robots_built,npc.robots_mined=0,0
    return {pieces=#entities,trees=#s.trees,kit=npc.robots_granted}
  end,
  tick=function(npc,s,tick)
    if s.done_tick then return end
    local _,status=find_plan(npc,s.plan_id)
    if status=="complete" then s.done_tick=tick end
  end,
  score=function(npc,s,tick)
    local plan,status=find_plan(npc,s.plan_id)
    local built,missing=0,{}
    for _,id in ipairs(plan and plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid then built=built+1 else missing[#missing+1]=id end
    end
    local left=0
    for _,t in ipairs(s.trees or {}) do if t.valid then left=left+1 end end
    local surface=npc.character and npc.character.valid and npc.character.surface
    local furnaces=surface and surface.count_entities_filtered{area=s.area,name="stone-furnace"} or nil
    return {pieces=s.pieces,built=built,missing=missing,furnaces_in_area=furnaces,plan_pieces=plan and #plan.order,robots_built=npc.robots_built or 0,robots_mined=npc.robots_mined or 0,
      trees_left=left,given=npc.robots_granted~=nil,done_minute=s.done_tick and minutes(s.done_tick-s.start) or nil,
      status=status,engineer_alive=npc.character~=nil and npc.character.valid,done=s.done_tick~=nil}
  end}

S["imported-block"]={
  setup=function(npc,params,s)
    if type(params.entities)~="table" or #params.entities==0 then return nil,"no_entities" end
    research(npc.force,params.research)
    if params.allied==false then
      local p=game.forces.player
      npc.force.set_friend(p,false); p.set_friend(npc.force,false)
      npc.force.set_cease_fire(p,true); p.set_cease_fire(npc.force,true)
    end
    -- (in its own plot, in a corner none of its buildings stand in: the block is large)
    local minx,miny,maxx,maxy=math.huge,math.huge,-math.huge,-math.huge
    for _,e in ipairs(params.entities) do
      minx,miny=math.min(minx,e.position.x),math.min(miny,e.position.y)
      maxx,maxy=math.max(maxx,e.position.x),math.max(maxy,e.position.y)
    end
    local surface=npc.character.surface
    local plot=plot_of(npc)
    local home=npc.home or npc.character.position
    local box=plot and plot.box or {home.x-100,home.y-100,home.x+100,home.y+100}
    local ox,oy
    for _,c in ipairs({{box[1]+4-minx,box[2]+4-miny},{box[3]-4-maxx,box[2]+4-miny},{box[1]+4-minx,box[4]-4-maxy},
        {box[3]-4-maxx,box[4]-4-maxy}}) do
      local x,y=math.floor(c[1]),math.floor(c[2])
      if not ox and surface.count_entities_filtered{area={{x+minx-2,y+miny-2},{x+maxx+2,y+maxy+2}},force=npc.force}==0 then
        ox,oy=x,y
      end
    end
    if not ox then return nil,"no_room_in_its_plot" end
    local entities={}
    for _,e in ipairs(params.entities) do
      local c={}
      for k,v in pairs(e) do c[k]=v end
      c.position={x=e.position.x+ox,y=e.position.y+oy}
      entities[#entities+1]=c
    end
    local clear={{ox+minx-2,oy+miny-2},{ox+maxx+2,oy+maxy+2}}
    surface.destroy_decoratives{area=clear}
    for _,t in ipairs(surface.find_entities_filtered{area=clear,type={"tree","simple-entity"}}) do
      t.destroy()
    end
    s.given=give(npc,params.items or {})
    s.items=params.items or {}
    local ids={}
    for i,e in ipairs(entities) do ids[i]=e.id end
    local plan,why=hand_plan(npc,"import-smoke",entities,{{id="imported",depends_on={},entity_ids=ids,
      manual_supplies={},witnesses={{kind="entity_built",entity_id=entities[1].id}}}},params.source)
    if not plan then return nil,"plan_refused:"..tostring(why) end
    s.plan,s.total=plan,#entities
    local ghosts,sides_bad,sides_seen,unders=0,0,0,0
    for _,e in pairs(plan.entities) do
      if e.belt_to_ground_type then unders=unders+1 end
      if e.ghost and e.ghost.valid then
        ghosts=ghosts+1
        if e.belt_to_ground_type then   -- (each underground's ghost read: its side as planned, or the check fails)
          local side=try(function() return e.ghost.belt_to_ground_type end)
          if side==e.belt_to_ground_type then sides_seen=sides_seen+1 else sides_bad=sides_bad+1 end
        end
      end
    end
    s.ghosts_start,s.ghost_sides_bad,s.ghost_sides_seen,s.undergrounds=ghosts,sides_bad,sides_seen,unders
    s.drawn=0
    for _,e in pairs(plan.entities) do if e.holo then s.drawn=s.drawn+1 end end
    s["end"]=params["end"]
    s.limit=params.minutes or 5
    return {entities=#entities,ghosts=ghosts,drawn=s.drawn,skipped=params.skipped}
  end,
  tick=function(npc,s,tick)
    if tick%60~=0 or s.ended_by then return end
    local d=npc.design
    if s["end"] and d and d.plan==s.plan and d.status=="building" and minutes(tick-s.start)>=1 then
      if s["end"]=="handback" then
        d.plan.hand_hold=1; d.hand_ticks=2
        DesignedStart.hand_back(npc,d,tick)
      elseif s["end"]=="relocate" then
        local h=npc.home
        DesignedStart.relocate(npc,{x=h.x+200,y=h.y},tick)
      end
      s.ended_by=s["end"]
    elseif not s["end"] and s.plan and (s.plan.status=="complete" or (d and d.plan==s.plan and d.status=="complete")
        or (npc.design_history and npc.design_history[#npc.design_history]==s.plan)) then
      s.ended_by="complete"
    end
  end,
  score=function(npc,s,tick)
    local plan=s.plan or {entities={}}
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    local built,sides,over=0,true,true
    for _,e in pairs(plan.entities) do
      if e.entity and e.entity.valid then
        built=built+1
        if e.belt_to_ground_type and e.entity.type=="underground-belt" and e.entity.belt_to_ground_type~=e.belt_to_ground_type then
          sides=false
        end
      end
      if e.ghost and e.ghost.valid and e.entity and e.entity.valid then over=false end   -- (a ghost under a built piece)
    end
    local inv=npc.character and npc.character.valid and npc.character.get_main_inventory()
    local from_inventory=true
    if s.ended_by=="complete" and inv then
      for item,n in pairs(s.items or {}) do
        if (s.given[item] or 0)>=n and inv.get_item_count(item)>(s.given[item] or 0)-n then from_inventory=false end
      end
    end
    local left=surface.count_entities_filtered{type="entity-ghost",force=npc.force}
    local drawings=0
    for _,e in pairs(plan.entities) do if e.holo and try(function() return e.holo.valid end) then drawings=drawings+1 end end
    return {entities=s.total,built=built,ghosts_placed=(s.ghosts_start or 0)==(s.total or -1),
      drawn_for_players=(s.drawn or 0)>0,undergrounds=s.undergrounds,
      sides_right=sides and (s.ghost_sides_bad or 0)==0 and (s.undergrounds or 0)>0
        and (s.ghost_sides_seen or 0)==(s.undergrounds or 0),
      built_over_ghosts=over and built==(s.total or -1),from_inventory=from_inventory,
      ghosts_left=s.ended_by and left or nil,drawings_left=s.ended_by and drawings or nil,ended_by=s.ended_by,
      engineer_alive=npc.character~=nil and npc.character.valid,done=s.ended_by~=nil}
  end}

-- (the same with params.turrets set: tools/proving.py death-world-waves-turrets)
S["death-world-waves-turrets"]=S["death-world-waves"]

-- e. Weighed help (NPC Defense, D-12): two engineers side by side, each with a loaded turret at home; the second
-- carries two turrets and magazines (it has something to bring). One wave hits the first one's plot: a small one its
-- turret holds wants no helper, a big one one or two; whoever goes turns back once it is over or held.
S["help-weighed"]={
  setup=function(npc,params,s)
    local plot=plot_of(npc)
    if not plot then return nil,"no_plot" end
    local surface=npc.character.surface
    -- (a second engineer on the next plot, as the roster spawns them)
    local id=npc.id==1 and 2 or 1
    if not (storage.npcs and storage.npcs[id]) then
      local home=TeamWorld.home(id)
      local made,why=Factory.spawn(id,surface,home,home,storage.founding_force or game.forces.player,
        Settlements.survey_at(surface,id,home))
      if not made then return nil,"no_second_engineer:"..tostring(why) end
      TeamWorld.ally_all()
    end
    local other
    for _,n in pairs(storage.npcs or {}) do if n~=npc and n.character and n.character.valid then other=n end end
    if not other then return nil,"no_second_engineer" end
    research(npc.force,{"military","gun-turret"}); research(other.force,{"military","gun-turret"})
    for _,n in ipairs({npc,other}) do
      local h=n.home or n.character.position
      local p=surface.find_non_colliding_position("gun-turret",{x=h.x,y=h.y-10},12,1)
      local t=p and surface.create_entity{name="gun-turret",position=p,force=n.force}
      if t then t.insert{name="firearm-magazine",count=50} end
    end
    if params.helper_armed~=false then give(other,{["gun-turret"]=2,["firearm-magazine"]=40}) end
    if params.helper_hit_ago then other.last_alarm=game.tick-params.helper_hit_ago end   -- (its own plot hit lately)
    local edge=plot.outer[1] or "north"
    local h=npc.home or npc.character.position
    local inward=({north={0,1},south={0,-1},west={1,0},east={-1,0}})[edge]
    s.target={x=h.x,y=h.y-10}
    s.spawn={x=h.x-inward[1]*70,y=h.y-10-inward[2]*70}
    land_between(surface,s.spawn,s.target)
    s.size=math.floor(params.size or 4)
    s.first=math.floor(params.first or 600)
    s.helped,s.other,s.went,s.back,s.units=npc.id,other.id,{},{},{}
    s.quiet_for=math.floor(params.quiet or 3600)
    return {other=other.id,distance=math.floor(math.sqrt((other.home.x-h.x)^2+(other.home.y-h.y)^2)),size=s.size}
  end,
  tick=function(npc,s,tick)
    local surface=npc.character and npc.character.valid and npc.character.surface or game.surfaces[1]
    if not s.sent and tick-s.start>=s.first then
      s.units=wave(surface,s.spawn,s.target,s.size,{["small-biter"]=1})
      s.sent=tick
    end
    for _,n in pairs(storage.npcs or {}) do
      if n.id~=s.helped then
        local going=(n.helping and n.helping.ally==s.helped) or (n.help_request and n.help_request.npc_id==s.helped)
        if going and not s.went[n.id] then s.went[n.id]=tick end
        if s.went[n.id] and not going and not s.back[n.id] then s.back[n.id]=tick end
      end
    end
  end,
  score=function(npc,s,tick)
    local alive=0
    for _,u in ipairs(s.units or {}) do if u.valid then alive=alive+1 end end
    if s.sent and alive==0 then s.calm_since=s.calm_since or tick else s.calm_since=nil end
    local went,back=0,0
    for _ in pairs(s.went) do went=went+1 end
    for _ in pairs(s.back) do back=back+1 end
    local other=storage.npcs and storage.npcs[s.other]
    return {wave=s.size,helpers=went,helpers_back=back,units_alive=alive,
      engineer_alive=npc.character~=nil and npc.character.valid,
      helper_alive=other~=nil and other.character~=nil and other.character.valid,
      done=s.calm_since~=nil and tick-s.calm_since>=s.quiet_for}
  end}

Proving.SCENARIOS=S

function Proving.setup(params)
  local scenario=S[params.scenario or ""]
  if not scenario then return {ok=false,reason="unknown_scenario"} end
  local npc=npc_of(params)
  if not (npc and npc.character and npc.character.valid) then return {ok=false,reason="no_engineer"} end
  local s={name=params.scenario,npc_id=npc.id,start=game.tick}
  -- (from a long run's checkpoint, made on the death world: a normal world first, whatever the scenario)
  local normal=params.normal_world and Proving.normal_world(npc,npc.character.surface,
    npc.home or npc.character.position,params.clear_radius or 400) or nil
  local summary,why=scenario.setup(npc,params,s)
  if not summary then return {ok=false,reason=why} end
  storage.proving=s
  return {ok=true,scenario=s.name,tick=game.tick,setup=summary,normal_world=normal}
end

function Proving.tick(tick)
  local s=state()
  if not s then return end
  local scenario=S[s.name]
  local npc=storage.npcs and storage.npcs[s.npc_id]
  if not npc then return end
  -- the scenario's own plan, while the engineer has it (once it moves on: done, or dropped as blocked)
  local d=npc.design
  if s.plan_id and d and d.plan and d.plan.plan_id==s.plan_id then
    s.plan_status,s.plan_reason=d.status,d.reason
  end
  if scenario and scenario.tick then scenario.tick(npc,s,tick) end
end

function Proving.score()
  local s=state()
  if not s then return {ok=false,reason="no_scenario"} end
  local npc=storage.npcs and storage.npcs[s.npc_id]
  if not npc then return {ok=false,reason="no_engineer"} end
  local out=S[s.name].score(npc,s,game.tick)
  out.ok,out.scenario,out.tick,out.game_minutes=true,s.name,game.tick,minutes(game.tick-s.start)
  -- (power given by script, not made by its base: energy interfaces on its network, a stage's boost or one left in a
  -- practice save by the grow; read none of it as the base's own)
  local surface=npc.character and npc.character.valid and npc.character.surface
  if surface then
    local given=0
    for _,e in ipairs(surface.find_entities_filtered{force=npc.force,name="electric-energy-interface"}) do
      given=given+(e.power_production or 0)*60/1e6
    end
    out.given_power_mw=math.floor(given*10)/10
  end
  return out
end

-- Its plans as they stand (`/npc-proving plans`, written to the run's plans.json): the one under way and the last one
-- finished, each piece with where it goes and whether it stands, the plan's status and reason, and what stood in its way.
-- A refused plan is read from here, not from the log (NPC Designer).
local function plan_dump(plan,d)
  if not plan then return nil end
  local stage_of={}
  for _,st in ipairs(plan.stages or {}) do for _,id in ipairs(st.entity_ids or {}) do stage_of[id]=st.id end end
  local pieces={}
  for _,id in ipairs(plan.order or {}) do
    local e=plan.entities and plan.entities[id]
    if e then
      local w=e.world or e.position or {}
      pieces[#pieces+1]={id=id,prototype=e.prototype,x=w.x,y=w.y,direction=e.direction,recipe=e.recipe,
        stage=stage_of[id],built=(e.entity and e.entity.valid) and true or false,failures=e.failures}
    end
  end
  local stages={}
  for _,st in ipairs(plan.stages or {}) do stages[#stages+1]={id=st.id,status=st.status} end
  return {plan_id=plan.plan_id,purpose=plan.purpose,source=plan.source,status=d and d.status,
    reason=d and d.reason and tostring(d.reason),obstacles=d and d.obstacles,stages=stages,pieces=pieces}
end

function Proving.plans()
  local s=state()
  local npc=s and storage.npcs and storage.npcs[s.npc_id]
  if not npc then return {ok=false,reason="no_engineer"} end
  local d=npc.design
  local h=npc.design_history or {}
  return {ok=true,tick=game.tick,current=d and plan_dump(d.plan,d),last_finished=plan_dump(h[#h])}
end

function Proving.register_commands()
  commands.add_command("npc-proving","Proving ground: setup {json} | score | normal (server console / RCON only)",function(command)
    if command.player_index then return end
    local p=command.parameter or "score"
    local result
    if p=="score" then result=Proving.score()
    elseif p=="plans" then result=Proving.plans()
    elseif p=="normal" then   -- (a long run's checkpoint resumed as a normal world, as setup's normal_world: tools/long_test.py)
      local npc=npc_of({})
      result=npc and npc.character and npc.character.valid
        and {ok=true,normal_world=Proving.normal_world(npc,npc.character.surface,npc.home or npc.character.position,400)}
        or {ok=false,reason="no_engineer"}
    elseif p:sub(1,6)=="setup " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(7))
      result=ok and type(payload)=="table" and Proving.setup(payload) or {ok=false,reason="invalid_json"}
    else result={ok=false,reason="command"} end
    rcon.print("NPC_PROVING_RESULT "..helpers.table_to_json(result))
  end)
end


-- A stage's start with its wall ring taken away (params.fresh_ring): the plans of the ring (its walls, turrets, breach
-- repairs and ammo loop) and all they placed, so the stage's engineer builds its ring again as its planners now would.
-- A start grown before a change of the ring's geometry has the old one (the coordinator, 2026-10-31: practice:bus grew
-- its ring before D-25; each version measured on a ring of its own). And its ring asked for at once, the build under
-- way set aside till it is up: else the grown bus's build and reinforce took the turns, and no ring stood in 13 minutes
-- (late-combat-bus, 2026-10-31). Returns what was taken away.
Proving.RING_PLANS={["plot-wall"]=true,["plot-turrets"]=true,["breach-repair"]=true,["ammo-line"]=true}
function Proving.clear_ring(npc,tick)
  local out={plans=0,entities=0}
  local kept={}
  for _,plan in ipairs(npc.design_history or {}) do
    if Proving.RING_PLANS[plan.purpose] then
      out.plans=out.plans+1
      for _,id in ipairs(plan.order or {}) do
        local e=plan.entities[id] and plan.entities[id].entity
        if e and e.valid then
          e.destroy()
          out.entities=out.entities+1
        end
      end
    else
      kept[#kept+1]=plan
    end
  end
  npc.design_history=kept
  for _,key in ipairs({"design","paused_design"}) do   -- (one of them under way: dropped too)
    local d=npc[key]
    if d and Proving.RING_PLANS[d.purpose] then npc[key]=nil end
  end
  npc.unit_index,npc.belt_items=nil,nil   -- (what owns each building has changed)
  if npc.design and not npc.paused_design then npc.paused_design,npc.design=npc.design,nil end
  npc.design=nil
  DesignedStart.begin(npc,tick,"Build what you decided: plot-wall","plot-wall")
  npc.design.ask="now"
  out.asked="plot-wall"
  return out
end

-- Each turret lost and how soon a new one stood at that spot (within LOSS_REACH tiles), from anywhere: the user's play,
-- 2026-10-30, lesson 11 (after every loss, more turrets there within about a minute, median 1.0, the player 150-320
-- tiles away). Looked at each second from the turrets standing: one gone is a loss, one new near an open loss answers it.
Proving.LOSS_REACH=6
function Proving.loss_answers(npc,s,tick)
  local surface=npc.character.surface
  local now={}
  for _,t in ipairs(surface.find_entities_filtered{force=npc.force,type={"ammo-turret","electric-turret","fluid-turret"}}) do
    if t.unit_number then now[t.unit_number]={x=t.position.x,y=t.position.y} end
  end
  local L=s.losses or {seen=nil,open={},done={}}
  s.losses=L
  if L.seen then
    for id,at in pairs(L.seen) do
      if not now[id] then L.open[#L.open+1]={x=at.x,y=at.y,tick=tick} end
    end
    -- (new turrets and open losses paired nearest first over all of them: each loss answered by the one at its spot, and
    -- one near two answers the other when the first has its own; Codex)
    local r2=Proving.LOSS_REACH^2
    local pairs_={}
    for id,at in pairs(now) do
      if not L.seen[id] then
        for i,o in ipairs(L.open) do
          local d=(o.x-at.x)^2+(o.y-at.y)^2
          if d<=r2 then pairs_[#pairs_+1]={d=d,id=id,i=i} end
        end
      end
    end
    table.sort(pairs_,function(p,q) if p.d~=q.d then return p.d<q.d end return p.i<q.i end)
    local used,answered={},{}
    for _,p in ipairs(pairs_) do
      if not used[p.id] and not answered[p.i] then
        used[p.id],answered[p.i]=true,true
        L.done[#L.done+1]=(tick-L.open[p.i].tick)/60
      end
    end
    local left={}
    for i,o in ipairs(L.open) do if not answered[i] then left[#left+1]=o end end
    L.open=left
  end
  L.seen=now
end

function Proving.loss_summary(s,tick)
  local L=s.losses
  if not L then return nil end
  local d={}
  for i,v in ipairs(L.done) do d[i]=v end
  table.sort(d)
  local oldest=0
  for _,o in ipairs(L.open) do oldest=math.max(oldest,(tick-o.tick)/60) end
  return {losses=#L.done+#L.open,answered=#L.done,median_s=#d>0 and d[math.ceil(#d/2)] or nil,
    max_s=d[#d],open=#L.open,open_oldest_s=#L.open>0 and math.floor(oldest) or nil}
end
return Proving
