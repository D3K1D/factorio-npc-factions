-- The team world (scenario "team", Biter World): one big biter-free area round spawn, cut into plots, one
-- for each player and each engineer, all touching, so nobody has biters between them. The players share
-- the centre plot; the engineers take the outer plots, each facing the biters on its own outer edge; the
-- eighth outer plot is the team yard.
--
--   +-------+-------+-------+
--   | npc-1 | npc-2 | npc-3 |
--   +-------+---+---+-------+
--   | npc-4 |p-1|p-2| npc-5 |
--   +-------+---+---+-------+
--   | npc-6 | npc-7 | yard  |
--   +-------+-------+-------+
--
-- Nests that generate inside the area (and a margin round it) are removed, and so is any base biters
-- build there later. Once the area is generated, every plot's home (the engineers' plots and each player's
-- half) gets the same easy start laid round it: a large rich patch of each of iron, copper, coal and stone, and
-- a pond, all at the same places relative to home, so mining and power are easy everywhere and every plot is
-- equal (the biters are the game).
local TeamWorld={}

local PLOT=384            -- plot side, tiles (12 chunks)
local MARGIN=0            -- nests are cleared this far beyond the area too (none: they may stand right at its edge)
-- The start laid round each home: {what, dx, dy, radius}. The patches stand at the corners of a 88 x 112 box
-- round home (the box fits a player's 192-wide half with room to spare), the pond between home and the southern
-- pair, about 28 tiles out, with room round it for several offshore pumps; home itself and the cross between the
-- patches stay free to build on.
local START={{"iron-ore",-44,-56,20},{"copper-ore",44,-56,20},{"coal",-44,56,20},{"stone",44,56,20},
  {"water",0,28,7}}
local HOME_CLEAR=12       -- dry ground round home, for the engineer to arrive and build on
-- Ore per tile at a patch's centre; it thins to 40% of that at the rim (a radius-20 iron patch is about 11M).
local PER_TILE={["iron-ore"]=12000,["copper-ore"]=12000,coal=10000,stone=8000}
local LAND="grass-1"      -- what water under a patch or home becomes
local GRID={{"npc-1","npc-2","npc-3"},{"npc-4","players","npc-5"},{"npc-6","npc-7","yard"}}
local WATER={"water","deepwater","water-green","deepwater-green","water-shallow","water-mud"}   -- (fallback only)
-- Traits every team engineer carries in a world of this kind (Personality.ensure adds them): on a death world,
-- survival first. They raise the priors of paths that exist (military research, defense weighed up, the
-- strategist's brief); the threat model still decides when.
local WORLD_TRAITS={death_world={"survivor"}}

function TeamWorld.active()
  local s=settings and settings.global["npc-factions-scenario"]
  return s~=nil and s.value=="team"
end

local function snap(v) return math.floor(v/32+0.5)*32 end

-- The plots round `origin` (snapped to the chunk grid), with the edges each one shares with the outside.
function TeamWorld.layout(origin)
  local ox,oy=snap(origin.x or origin[1]),snap(origin.y or origin[2])
  local half=PLOT*3/2
  local area={ox-half,oy-half,ox+half,oy+half}
  local plots={}
  local function add(name,kind,index,box)
    local outer={}
    if box[2]==area[2] then outer[#outer+1]="north" end
    if box[3]==area[3] then outer[#outer+1]="east" end
    if box[4]==area[4] then outer[#outer+1]="south" end
    if box[1]==area[1] then outer[#outer+1]="west" end
    plots[#plots+1]={name=name,kind=kind,index=index,box=box,outer=outer,
      centre={x=(box[1]+box[3])/2,y=(box[2]+box[4])/2}}
  end
  for r=1,3 do
    for c=1,3 do
      local x0,y0=area[1]+PLOT*(c-1),area[2]+PLOT*(r-1)
      local key=GRID[r][c]
      if key=="players" then   -- the centre plot, split west and east
        add("player-1","player",1,{x0,y0,x0+PLOT/2,y0+PLOT})
        add("player-2","player",2,{x0+PLOT/2,y0,x0+PLOT,y0+PLOT})
      elseif key=="yard" then add("yard","yard",nil,{x0,y0,x0+PLOT,y0+PLOT})
      else add(key,"npc",tonumber(key:sub(5)),{x0,y0,x0+PLOT,y0+PLOT}) end
    end
  end
  TeamWorld.assign_bands(plots)
  return {origin={x=ox,y=oy},area=area,plots=plots,bands_assigned=true}
end

-- The team yard is nobody's plot, yet its outer edges are part of the team's wall ring: each goes to
-- the engineer whose own outer edge runs on into it, as a band BAND tiles deep it may build in.
local BAND=24
local function band_box(b,edge)
  if edge=="north" then return {b[1],b[2],b[3],b[2]+BAND}
  elseif edge=="south" then return {b[1],b[4]-BAND,b[3],b[4]}
  elseif edge=="west" then return {b[1],b[2],b[1]+BAND,b[4]}
  else return {b[3]-BAND,b[2],b[3],b[4]} end
end
local function has(list,v) for _,x in ipairs(list or {}) do if x==v then return true end end return false end
function TeamWorld.assign_bands(plots)
  for _,yard in ipairs(plots) do
    if yard.kind=="yard" then
      local y=yard.box
      for _,edge in ipairs(yard.outer) do
        for _,p in ipairs(plots) do
          local b=p.box
          local runs_on=p.kind=="npc" and has(p.outer,edge) and (
            (edge=="east" or edge=="west") and b[1]==y[1] and b[3]==y[3] and (b[4]==y[2] or b[2]==y[4])
            or (edge=="north" or edge=="south") and b[2]==y[2] and b[4]==y[4] and (b[3]==y[1] or b[1]==y[3]))
          if runs_on then
            -- (where the band's line meets the yard's other outer edge, the corner is someone's: it stops short)
            local lo=(edge=="east" or edge=="west") and "north" or "west"
            local hi=(edge=="east" or edge=="west") and "south" or "east"
            p.bands=p.bands or {}
            p.bands[#p.bands+1]={name=edge,box=band_box(y,edge),corner_lo=has(yard.outer,lo),
              corner_hi=has(yard.outer,hi)}
            break
          end
        end
      end
    end
  end
end

-- Every stretch of wall line an engineer holds: its plot's outer edges and its bands of the yard.
function TeamWorld.wall_edges(plot)
  local out={}
  for _,edge in ipairs(plot and plot.outer or {}) do out[#out+1]={name=edge,box=plot.box} end
  for _,band in ipairs(plot and plot.bands or {}) do out[#out+1]={name=band.name,box=band.box} end
  return out
end

-- Whether a position is ground the engineer of `plot` holds (its plot, or one of its bands).
function TeamWorld.holds(plot,p)
  if not plot then return false end
  local function inside(b) return p.x>=b[1] and p.x<b[3] and p.y>=b[2] and p.y<b[4] end
  if inside(plot.box) then return true end
  for _,band in ipairs(plot.bands or {}) do if inside(band.box) then return true end end
  return false
end

function TeamWorld.state()
  if not storage.team then
    local surface=game.surfaces[1]
    local spawn=game.forces.player.get_spawn_position(surface)
    storage.team=TeamWorld.layout(spawn)
    storage.team.surface_index=surface.index
    storage.team.status="new"
    storage.team.homes={}
    storage.team.added={}
    storage.team.cleared=0
  elseif not storage.team.bands_assigned then
    TeamWorld.assign_bands(storage.team.plots)   -- (a team area made before the yard's edges had owners)
    storage.team.bands_assigned=true
  end
  return storage.team
end

local function within(box,p,margin)
  margin=margin or 0
  return p.x>=box[1]-margin and p.x<box[3]+margin and p.y>=box[2]-margin and p.y<box[4]+margin
end

function TeamWorld.inside(position,margin)
  return within(TeamWorld.state().area,position,margin)
end

function TeamWorld.plot(name)
  for _,plot in ipairs(TeamWorld.state().plots) do if plot.name==name then return plot end end
end

function TeamWorld.ready() return TeamWorld.active() and TeamWorld.state().status=="ready" end

-- A death world (set by the save maker with `/npc-team on {"death_world":true}`): many biters, hard from the start.
function TeamWorld.death_world()
  return TeamWorld.active() and storage.team~=nil and storage.team.death_world==true
end

-- The traits this world gives every team engineer, in order (empty outside the team world).
function TeamWorld.world_traits()
  local out={}
  if not TeamWorld.active() or not storage.team then return out end
  for _,kind in ipairs({"death_world"}) do
    if storage.team[kind] then for _,name in ipairs(WORLD_TRAITS[kind]) do out[#out+1]=name end end
  end
  return out
end

-- How many engineers have a plot of their own.
function TeamWorld.npc_plots()
  local n=0
  for _,plot in ipairs(TeamWorld.state().plots) do if plot.kind=="npc" then n=n+1 end end
  return n
end

function TeamWorld.home(id)
  return TeamWorld.state().homes["npc-"..id]
end

-- Enemy buildings and units in `box` (an area) are removed. The count removed.
local function clear_enemies(surface,box)
  local n=0
  for _,e in ipairs(surface.find_entities_filtered{area={{box[1],box[2]},{box[3],box[4]}},force="enemy"}) do
    if e.valid then e.destroy(); n=n+1 end
  end
  return n
end

function TeamWorld.on_chunk_generated(event)
  if not TeamWorld.active() then return end
  local t=TeamWorld.state()
  if event.surface.index~=t.surface_index then return end
  local a,l=t.area,event.area
  local box={math.max(a[1]-MARGIN,l.left_top.x),math.max(a[2]-MARGIN,l.left_top.y),
    math.min(a[3]+MARGIN,l.right_bottom.x),math.min(a[4]+MARGIN,l.right_bottom.y)}
  if box[1]>=box[3] or box[2]>=box[4] then return end
  t.cleared=t.cleared+clear_enemies(event.surface,box)
end

-- Biters never settle inside the team area: a base they build there is taken down at once.
function TeamWorld.on_biter_base_built(event)
  local e=event.entity
  if not (e and e.valid and TeamWorld.active()) then return end
  local t=TeamWorld.state()
  if e.surface.index==t.surface_index and within(t.area,e.position,MARGIN) then
    e.destroy()
    t.expansions_stopped=(t.expansions_stopped or 0)+1
  end
end

-- The tiles of a disc, {x,y} of each tile's corner.
local function disc_tiles(c,r)
  local out={}
  for x=math.floor(c.x-r),math.floor(c.x+r) do
    for y=math.floor(c.y-r),math.floor(c.y+r) do
      if (x+0.5-c.x)^2+(y+0.5-c.y)^2<=r*r then out[#out+1]={x,y} end
    end
  end
  return out
end

-- Water in a disc becomes land (a patch or a home on a lake would be no start).
local function dry(surface,c,r)
  local wet
  local ok=pcall(function() wet=surface.find_tiles_filtered{position=c,radius=r+1,collision_mask="water_tile"} end)
  if not ok then wet=surface.find_tiles_filtered{position=c,radius=r+1,name=WATER} end
  local tiles={}
  for _,t in ipairs(wet) do tiles[#tiles+1]={name=LAND,position=t.position} end
  if #tiles>0 then surface.set_tiles(tiles) end
end

-- What stands on a disc goes: trees, rocks, cliffs, the map's own ore (a patch is one ore throughout), corpses.
local function clear_disc(surface,c,r)
  for _,e in ipairs(surface.find_entities_filtered{position=c,radius=r+1,
      type={"tree","simple-entity","cliff","resource","corpse"}}) do
    if e.valid then e.destroy() end
  end
end

local function lay_patch(surface,ore,c,r)
  dry(surface,c,r)
  clear_disc(surface,c,r)
  local tiles,total=0,0
  for _,t in ipairs(disc_tiles(c,r)) do
    local d=math.sqrt((t[1]+0.5-c.x)^2+(t[2]+0.5-c.y)^2)
    local amount=math.max(1,math.floor(PER_TILE[ore]*(1-0.6*d/r)))
    if surface.create_entity{name=ore,position={t[1]+0.5,t[2]+0.5},amount=amount} then
      tiles,total=tiles+1,total+amount
    end
  end
  return tiles,total
end

local function lay_pond(surface,c,r)
  clear_disc(surface,c,r)
  local tiles={}
  for _,t in ipairs(disc_tiles(c,r)) do tiles[#tiles+1]={name="water",position=t} end
  surface.set_tiles(tiles)
  return #tiles
end

-- The start round one home: where each piece goes, as world positions.
function TeamWorld.start_layout(home)
  local out={}
  for _,s in ipairs(START) do
    out[#out+1]={what=s[1],position={x=math.floor(home.x+s[2])+0.5,y=math.floor(home.y+s[3])+0.5},radius=s[4]}
  end
  return out
end

-- Every plot's home (its centre) and the same start laid round it (once, when the whole area is generated). The
-- team yard is nobody's base and gets none.
local function prepare_plots(surface,t)
  for _,plot in ipairs(t.plots) do
    local home={x=math.floor(plot.centre.x)+0.5,y=math.floor(plot.centre.y)+0.5}
    if plot.kind~="yard" then
      dry(surface,home,HOME_CLEAR)
      clear_disc(surface,home,HOME_CLEAR)
      for _,piece in ipairs(TeamWorld.start_layout(home)) do
        if piece.what=="water" then
          local tiles=lay_pond(surface,piece.position,piece.radius)
          t.added[#t.added+1]={plot=plot.name,what="water",position=piece.position,tiles=tiles}
        else
          local tiles,total=lay_patch(surface,piece.what,piece.position,piece.radius)
          t.added[#t.added+1]={plot=plot.name,what=piece.what,position=piece.position,tiles=tiles,amount=total}
        end
      end
    end
    home=surface.find_non_colliding_position("character",home,24,1) or home
    t.homes[plot.name]={x=home.x,y=home.y}
  end
end

local function all_generated(surface,t)
  local a=t.area
  for cx=math.floor((a[1]-MARGIN)/32),math.floor((a[3]+MARGIN-1)/32) do
    for cy=math.floor((a[2]-MARGIN)/32),math.floor((a[4]+MARGIN-1)/32) do
      if not surface.is_chunk_generated({cx,cy}) then return false end
    end
  end
  return true
end

-- Once a second until the team area is ready: the area is generated, swept of nests (chunks made
-- before the scenario was switched on), and the plots prepared.
function TeamWorld.tick(tick)
  if tick%60~=0 or not TeamWorld.active() then return end
  local t=TeamWorld.state()
  if t.status=="ready" then return end
  local surface=game.surfaces[t.surface_index]
  if t.status=="new" then
    local half=(t.area[3]-t.area[1])/2+MARGIN
    surface.request_to_generate_chunks(t.origin,math.ceil(half/32))
    t.status,t.requested="generating",tick
    return
  end
  if not all_generated(surface,t) then return end
  local a=t.area
  t.cleared=t.cleared+clear_enemies(surface,{a[1]-MARGIN,a[2]-MARGIN,a[3]+MARGIN,a[4]+MARGIN})
  prepare_plots(surface,t)
  t.status,t.ready_tick="ready",tick
  log("[npc-factions] team area ready: "..#t.plots.." plots, "..#t.added.." starter patches and ponds, "
    ..t.cleared.." enemies cleared")
end

function TeamWorld.status()
  local t=TeamWorld.state()
  return {ok=true,schema="npc-team-v1",active=TeamWorld.active(),status=t.status,area=t.area,origin=t.origin,
    plots=t.plots,homes=t.homes,added=t.added,cleared=t.cleared,expansions_stopped=t.expansions_stopped or 0,
    death_world=t.death_world or false,world_traits=TeamWorld.world_traits(),
    waves=t.waves and {sent=t.waves.sent,per_plot=t.waves.per_plot,start=t.waves.start,next_tick=t.waves.next_tick,
      last=t.waves.log[#t.waves.log]} or nil,
    ready_tick=t.ready_tick,breaches=t.war and t.war.breaches or nil,tick=game.tick}
end

-- Where an engineer may build. In its own plot, or out beyond the team area (an outpost past the
-- wall); never in another plot (a teammate's, the players', the yard), never across a plot's edge, and
-- never within ZONE tiles of anything a player built (or marked for building: a ghost).
local ZONE=32
local IGNORED={character=true,car=true,["spider-vehicle"]=true,locomotive=true,["cargo-wagon"]=true,
  ["fluid-wagon"]=true,["artillery-wagon"]=true,corpse=true,["character-corpse"]=true}

local function box_inside(outer,b)
  return b[1][1]>=outer[1] and b[1][2]>=outer[2] and b[2][1]<=outer[3] and b[2][2]<=outer[4]
end
local function box_apart(outer,b)
  return b[2][1]<=outer[1] or b[2][2]<=outer[2] or b[1][1]>=outer[3] or b[1][2]>=outer[4]
end

-- Something a player built within ZONE tiles of `box` ({{x0,y0},{x1,y1}}), or nil. (No limit on the search: the
-- players' own bodies and cars there would fill it before a building.)
function TeamWorld.player_built_near(surface,box)
  local area={{box[1][1]-ZONE,box[1][2]-ZONE},{box[2][1]+ZONE,box[2][2]+ZONE}}
  for _,e in ipairs(surface.find_entities_filtered{area=area,force=game.forces.player}) do
    if e.valid and not IGNORED[e.type] then return e end
  end
end

-- True, or nil and why ("other_plot", "player_zone").
function TeamWorld.may_build(npc,box,surface)
  if not TeamWorld.active() then return true end
  local t=TeamWorld.state()
  local plot=TeamWorld.plot("npc-"..npc.id)
  local own=plot and box_inside(plot.box,box)
  for _,band in ipairs(plot and plot.bands or {}) do own=own or box_inside(band.box,box) end
  if not own and not box_apart(t.area,box) then return nil,"other_plot" end
  surface=surface or (npc.character and npc.character.valid and npc.character.surface)
  if surface and TeamWorld.player_built_near(surface,box) then return nil,"player_zone" end
  return true
end

-- Where players have built round `area`, as rectangles an engineer's planners keep out of (each chunk
-- with player buildings, widened by ZONE tiles). For the site export and the map dump.
function TeamWorld.no_build(surface,area)
  if not TeamWorld.active() then return nil end
  local cells,out={},{}
  for _,e in ipairs(surface.find_entities_filtered{area=area,force=game.forces.player}) do
    if e.valid and not IGNORED[e.type] then
      local cx,cy=math.floor(e.position.x/32),math.floor(e.position.y/32)
      local k=cx..":"..cy
      if not cells[k] then
        cells[k]=true
        out[#out+1]={cx*32-ZONE,cy*32-ZONE,cx*32+32+ZONE,cy*32+32+ZONE}
      end
    end
  end
  return out
end

-- An engineer's plot and the team area, for its planners (nil outside the team world).
function TeamWorld.bounds(npc)
  if not TeamWorld.active() then return nil end
  local t=TeamWorld.state()
  local plot=TeamWorld.plot("npc-"..npc.id)
  -- (its outer edges: its wall line; bands: the yard's outer edges it holds too)
  return {plot=plot and plot.box,area=t.area,outer=plot and plot.outer,bands=plot and plot.bands,
    death_world=t.death_world or nil}
end

-- One team: the players' force and every engineer's force are friends (allies: nobody shoots, they see
-- what the others see) with each other. Again whenever the roster or the mod changes.
function TeamWorld.ally_all()
  if not TeamWorld.active() then return end
  local forces={game.forces.player}
  for _,npc in pairs(storage.npcs or {}) do
    if npc.force and npc.force.valid then forces[#forces+1]=npc.force end
  end
  for _,a in ipairs(forces) do
    a.share_chart=true
    for _,b in ipairs(forces) do
      if a~=b then a.set_friend(b,true); a.set_cease_fire(b,true) end
    end
  end
end

-- Set by control.lua: spawns the engineers at their plots for a player (or a stand-in on a server).
TeamWorld.spawner=nil

function TeamWorld.register_commands()
  commands.add_command("npc-team","Team world: status | spawn (server console / RCON only)",function(command)
    if command.player_index then return end
    local p=command.parameter or "status"
    local result
    if p=="status" then result=TeamWorld.status()
    elseif p=="on" or p:sub(1,3)=="on " then
      -- (a save maker's switch: only the mod may change its settings) {"count":N} engineers, and
      -- {"death_world":true} for a death world (every engineer then carries its WORLD_TRAITS)
      local ok,payload=pcall(helpers.json_to_table,p:sub(4))
      payload=ok and type(payload)=="table" and payload or {}
      local count=tonumber(payload.count)
      settings.global["npc-factions-scenario"]={value="team"}
      if payload.death_world~=nil then TeamWorld.state().death_world=payload.death_world==true end
      if count then settings.global["npc-factions-count"]={value=math.max(1,math.min(TeamWorld.npc_plots(),count))} end
      result=TeamWorld.status()
      result.count=settings.global["npc-factions-count"].value
    elseif p=="spawn" and TeamWorld.spawner then
      local proxy={valid=true,surface=game.surfaces[TeamWorld.state().surface_index],force=game.forces.player,
        position=TeamWorld.state().origin,print=function() end}
      local spawned,reason=TeamWorld.spawner(proxy)
      result={ok=spawned~=nil,spawned=spawned,reason=reason,tick=game.tick}
    else result={ok=false,reason="command"} end
    rcon.print("NPC_TEAM_RESULT "..helpers.table_to_json(result))
  end)
end

return TeamWorld
