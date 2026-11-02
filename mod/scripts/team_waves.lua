-- A death world's attack waves (the team world with the death-world flag, TeamWorld.death_world): beside the
-- biters' own attacks, which their nests send where the pollution reaches, the nearest live nests send waves at
-- the team's bases from the first minutes. Everything comes from the world as it is: the nests that stand, their
-- own spawn tables at the current evolution, and the bases' polluting buildings; how often and how many grow with
-- evolution and game time, scaled by the setting npc-factions-death-waves (0 is off, 1 as designed, 2 twice as
-- hard). A softer start, then a fast ramp (the user's choice, 2026-10-27): the first wave comes after a grace (GRACE,
-- shorter the harder the setting), and each wave is sized against what its target base can field: its loaded turrets
-- and its people's guns, in the game's own measure (combat_odds.lua, NPC Offense's), so the base is meant to win the
-- first ones at EASY_ODDS; the odds aimed for fall toward HARD_ODDS (more than it can hold) as game time, evolution,
-- the base's pollution and its size grow, and a floor from time and evolution keeps a base with nothing up pressed.
-- A wave's size is a budget of threat, counted as the game counts it (what a unit's spawner must absorb before it
-- joins an attack: a small biter 4, a behemoth 400 in the base game), so evolved or modded biters come in fewer.
-- Waves go round the plots fairly: the base that has had the fewest goes next. Each wave is the game's own unit
-- group, made at its nests (as they spawn biters) and told to attack: no teleports. Every wave is logged; with no
-- nest left in reach (the engineers cleared them), the waves pause until the biters expand again.
local TeamWorld=require("scripts.team_world")
local Odds=require("scripts.combat_odds")
local TeamWaves={}

local CHECK=60               -- ticks between looks
local GRACE=9*3600           -- the first wave this long after the engineers arrive (strength 1; over strength)
local EASY_ODDS=4            -- the first waves: its defense this much stronger than the wave (combat_odds ratio)
local HARD_ODDS=0.7          -- fully ramped: the wave stronger than its defense
local RAMP_MINUTES=40        -- game minutes since the first wave that count once toward the ramp
local RAMP_EVOLUTION=0.25    -- evolution that counts once
local RAMP_POLLUTION=3000    -- pollution over its plot that counts once
local RAMP_BUILDINGS=150     -- buildings of its own that count once
local MIN_UNITS=2            -- a wave is never fewer
local INTERVAL=6*3600        -- between waves at the start (strength 1)
local MIN_INTERVAL=90*60     -- and never closer than this
local GROWTH_MINUTES=30      -- every this many game minutes the pace counts once more
local BASE_SIZE=5            -- the first wave's threat, in small biters (strength 1)
local SIZE_PER_EVOLUTION=60  -- more as the biters evolve (at evolution 1: 60 more)
local SIZE_PER_HOUR=10       -- and as the game goes on
local MAX_SIZE=400           -- a wave's threat never more than this many small biters
local MAX_UNITS=150          -- nor more units than this
local SMALL=4                -- a small biter's threat (its spawner absorbs this much pollution before it attacks)
local NESTS=3                -- the nearest this many nests that can spawn send a wave between them
local SEARCH={512,1024,2048,4096}   -- how far out nests are looked for, nearest first
local RETRY=600              -- no nest or no unit could come: look again this soon
local LOG=50

local function strength()
  local s=settings.global["npc-factions-death-waves"]
  return s and tonumber(s.value) or 1
end

local function state()
  local t=TeamWorld.state()
  t.waves=t.waves or {sent=0,per_plot={},log={}}
  return t.waves
end

-- Units an evolution brings from a spawner's own table: {name=weight}, the weight read between its spawn points.
function TeamWaves.weights(result_units,evolution)
  local out={}
  for _,u in ipairs(result_units or {}) do
    local points={}
    for _,p in ipairs(u.spawn_points or {}) do points[#points+1]=p end
    table.sort(points,function(a,b) return a.evolution_factor<b.evolution_factor end)
    local w=0
    if #points>0 then
      if evolution<=points[1].evolution_factor then w=points[1].weight
      elseif evolution>=points[#points].evolution_factor then w=points[#points].weight
      else
        for i=1,#points-1 do
          local p,q=points[i],points[i+1]
          if evolution<q.evolution_factor then
            w=p.weight+(q.weight-p.weight)*(evolution-p.evolution_factor)/(q.evolution_factor-p.evolution_factor)
            break
          end
        end
      end
    end
    if w>0 then out[u.unit]=(out[u.unit] or 0)+w end
  end
  return out
end

-- How many biters the next wave brings and how long until the one after, from evolution and game minutes.
function TeamWaves.pace(evolution,minutes,s)
  local age=1+minutes/GROWTH_MINUTES
  local size=math.min(MAX_SIZE,math.floor(s*(BASE_SIZE+SIZE_PER_EVOLUTION*evolution+SIZE_PER_HOUR*minutes/60)+0.5))
  local interval=math.max(MIN_INTERVAL,math.floor(INTERVAL/(s*(1+2*evolution)*math.sqrt(age))))
  return size,interval
end

-- Plots with a base to attack: an engineer's plot while it is alive there, a player's half once anything of the
-- players' force stands in it. The one with the fewest waves so far goes first (then the longest since one).
-- What does not make a base: bodies, vehicles, ghosts, corpses.
local MOBILE={"character","car","tank","spider-vehicle","locomotive","cargo-wagon","fluid-wagon","artillery-wagon",
  "entity-ghost","tile-ghost","item-request-proxy","corpse","character-corpse","item-entity"}
local mobile={}
for _,kind in ipairs(MOBILE) do mobile[kind]=true end

-- A building of `force` in `area` (something with health that stays put), or nil.
local function building_in(surface,area,force)
  for _,e in ipairs(surface.find_entities_filtered{area=area,force=force,limit=256}) do
    if e.valid and not mobile[e.type] and e.is_entity_with_health then return e end
  end
end

local function bases(surface)
  local out={}
  for _,plot in ipairs(TeamWorld.state().plots) do
    local b=plot.box
    local area={{b[1],b[2]},{b[3],b[4]}}
    if plot.kind=="npc" then
      -- (an engineer alive on this surface with a building on its plot)
      local npc=storage.npcs and storage.npcs[plot.index]
      local c=npc and npc.character
      if c and c.valid and c.surface==surface and npc.force and npc.force.valid
          and building_in(surface,area,npc.force) then
        out[#out+1]={plot=plot,force=npc.force,area=area,npc=npc}
      end
    elseif plot.kind=="player" then
      if building_in(surface,area,game.forces.player) then
        out[#out+1]={plot=plot,force=game.forces.player,area=area}
      end
    end
  end
  return out
end

local SOURCES={"burner_prototype","electric_energy_source_prototype","fluid_energy_source_prototype",
  "heat_energy_source_prototype","void_energy_source_prototype"}
local function polluting(e)
  local p=e.prototype
  local ok,own=pcall(function() return p.emissions_per_second end)
  for _,v in pairs(ok and own or {}) do if v>0 then return true end end
  for _,key in ipairs(SOURCES) do
    local fine,per=pcall(function() local src=p[key] return src and src.emissions_per_joule end)
    for _,v in pairs(fine and per or {}) do if v>0 then return true end end
  end
  return false
end

-- A unit's threat (the game's own measure, absorptions_to_join_attack; else from its health).
local function threat_of(name)
  local p=prototypes.entity[name]
  if not p then return SMALL end
  local ok,cost=pcall(function() local a=p.absorptions_to_join_attack return a and a.pollution end)
  if ok and cost and cost>0 then return cost end
  local fine,h=pcall(function() return p.get_max_health() end)
  return math.max(1,(fine and tonumber(h) or 15)/15*SMALL)
end


-- How far the ramp has gone (0 to 1), and its parts: game minutes since the first wave, evolution, the base's
-- pollution and its buildings, each over what counts once, scaled by the setting.
function TeamWaves.ramp(minutes,evolution,pollution,buildings,s)
  local x=s*(minutes/RAMP_MINUTES+evolution/RAMP_EVOLUTION+pollution/RAMP_POLLUTION+buildings/RAMP_BUILDINGS)
  return 1-math.exp(-x)
end

-- The odds its defense is meant to have against a wave at ramp r: from EASY_ODDS down to HARD_ODDS.
function TeamWaves.aim(r)
  return EASY_ODDS^(1-r)*HARD_ODDS^r
end

-- The most units of `mix` ({name=share}) its defense `us` (combat_odds' sides) beats at `aim` odds: at least
-- MIN_UNITS (a base with nothing up still gets a few), at most MAX_UNITS.
function TeamWaves.fit(us,mix,aim)
  local facts={}
  for name,share in pairs(mix) do
    local p=prototypes.entity[name]
    if p then facts[#facts+1]={facts=Odds.foe_facts(p),share=share} end
  end
  if #facts==0 or #us.shooters==0 then return MIN_UNITS end
  local function ratio(n)
    local them={}
    for _,f in ipairs(facts) do them[#them+1]={hp=f.facts.hp,count=n*f.share,facts=f.facts} end
    return Odds.fight(us,them).ratio
  end
  if ratio(MIN_UNITS)<aim then return MIN_UNITS end
  local lo,hi=MIN_UNITS,MAX_UNITS
  if ratio(hi)>=aim then return hi end
  while hi-lo>1 do
    local mid=math.floor((lo+hi)/2)
    if ratio(mid)>=aim then lo=mid else hi=mid end
  end
  return lo
end

local function dist2(a,b) return (a.x-b.x)^2+(a.y-b.y)^2 end
local TURRETS={"ammo-turret","electric-turret"}   -- (what combat_odds can weigh: a flamethrower's fuel it cannot)
local ENGAGE_LOOK=16         -- a turret counts when the spot a wave strikes lies within its range and this much more

-- The best gun a person carries (in its gun slots or its pockets: an engineer equips one when a fight comes) with the
-- ammo it has for it, as combat_odds' shooter; nil when none can fire.
local function armed(c)
  local guns,rounds={},{}
  for _,which in ipairs({"character_guns","character_ammo","character_main"}) do
    local inv=c.get_inventory(defines.inventory[which])
    for i=1,inv and #inv or 0 do
      local stack=inv[i]
      if stack.valid_for_read then
        local item=prototypes.item[stack.name]
        local kind=item and item.type
        if kind=="gun" then guns[stack.name]=true
        elseif kind=="ammo" then rounds[stack.name]=(rounds[stack.name] or 0)+stack.count end
      end
    end
  end
  local best,best_dps
  local names={}
  for gun in pairs(guns) do names[#names+1]=gun end
  table.sort(names)
  for _,gun in ipairs(names) do
    local fits={}
    for _,cat in ipairs(prototypes.item[gun].attack_parameters and prototypes.item[gun].attack_parameters.ammo_categories
        or {}) do fits[cat]=true end
    local ammo={}
    for name in pairs(rounds) do ammo[#ammo+1]=name end
    table.sort(ammo)
    for _,name in ipairs(ammo) do
      local f=Odds.weapon_facts(gun,name,false)
      if f and fits[f.category] then
        local s=Odds.shooter(c.force,gun,name,rounds[name]*f.size,false)
        local dps=0
        for _,v in pairs(s and s.by or {}) do dps=dps+v end
        dps=s and dps*s.scale*s.rate or 0
        if s and (not best or dps>best_dps) then best,best_dps=s,dps end
      end
    end
  end
  return best
end
-- What the base can field (combat_odds' sides) where a wave strikes (`at`, else anywhere on the plot): the loaded
-- turrets standing on it, of its own force or a friend's, that reach that spot, and the guns of its people (its
-- engineer; for a player's half, the players on it) with the rounds they carry.
function TeamWaves.defenders(surface,base,at)
  local us={shooters={},bodies={}}
  local forces={base.force}
  for _,f in pairs(game.forces) do if f~=base.force and base.force.get_friend(f) then forces[#forces+1]=f end end
  for _,turret in ipairs(surface.find_entities_filtered{area=base.area,force=forces,type=TURRETS}) do
    local s=turret.valid and Odds.loaded_turret(turret.force,turret)
    if s and at and dist2(turret.position,at)>(s.range+ENGAGE_LOOK)^2 then s=nil end
    if s then
      us.shooters[#us.shooters+1]=s
      us.bodies[#us.bodies+1]={hp=s.hp or 400,resist=s.resist}
    end
  end
  local people={}
  if base.npc then people[1]=base.npc.character
  else
    local a=base.area
    for _,c in ipairs(surface.find_entities_filtered{area={{a[1][1]-64,a[1][2]-64},{a[2][1]+64,a[2][2]+64}},
        force=base.force,type="character"}) do people[#people+1]=c end
  end
  for _,c in ipairs(people) do
    if c and c.valid then
      local s=armed(c)
      if s then us.shooters[#us.shooters+1]=s end
      us.bodies[#us.bodies+1]={hp=c.health or 250,resist={}}
    end
  end
  return us
end

-- Pollution over a base's plot (its chunks) and its buildings: what draws the biters, and how big it has grown.
local function pull_of(surface,base)
  local a,pollution=base.area,0
  for x=a[1][1]+16,a[2][1],32 do
    for y=a[1][2]+16,a[2][2],32 do pollution=pollution+(surface.get_pollution({x,y}) or 0) end
  end
  local buildings=surface.count_entities_filtered{area=a,force=base.force}
    -surface.count_entities_filtered{area=a,force=base.force,type=MOBILE}
  return pollution,math.max(0,buildings)
end

-- The nearest live nests to a base that can spawn now, nearest first: {nest, weights}.
local function nests_near(surface,centre,evolution)
  local out={}
  for _,r in ipairs(SEARCH) do
    local found=surface.find_entities_filtered{type="unit-spawner",force="enemy",position=centre,radius=r}
    table.sort(found,function(a,b) return dist2(a.position,centre)<dist2(b.position,centre) end)
    out={}
    for _,nest in ipairs(found) do
      local weights=TeamWaves.weights(nest.prototype.result_units,evolution)
      if next(weights) then out[#out+1]={nest=nest,weights=weights} end
      if #out>=NESTS*3 then return out end   -- (spares, should the nearest be hemmed in)
    end
  end
  return out
end

-- The base's polluting building nearest the nests (else any building of its force there, else its centre).
local function target_of(surface,base,from)
  local best,best_d,any,any_d
  for _,e in ipairs(surface.find_entities_filtered{area=base.area,force=base.force}) do
    if e.valid and not mobile[e.type] and e.is_entity_with_health then
      local d=dist2(e.position,from)
      if polluting(e) and (not best or d<best_d) then best,best_d=e,d end
      if not any or d<any_d then any,any_d=e,d end
    end
  end
  return best or any
end

-- One unit from a nest's weights.
local function pick(weights)
  local names,total={},0
  for name,w in pairs(weights) do names[#names+1]=name; total=total+w end
  if total<=0 then return nil end
  table.sort(names)
  local r=math.random()*total
  for _,name in ipairs(names) do
    r=r-weights[name]
    if r<=0 then return name end
  end
  return names[#names]
end

-- Sends one wave at `base` now, sized to its defense at the ramp's odds (and no less than `floor` small biters'
-- threat): {size, threat, kinds, plot, from, why} or nil and why.
function TeamWaves.send(surface,base,floor,evolution,minutes,s,tick)
  local centre={x=(base.area[1][1]+base.area[2][1])/2,y=(base.area[1][2]+base.area[2][2])/2}
  local nests=nests_near(surface,centre,evolution)
  if #nests==0 then return nil,"no_nests" end
  local from=nests[1].nest.position
  local target=target_of(surface,base,from)
  -- the wave's mix (the nests' own tables), what the base can field against it, and how many it is meant to beat
  local mix,total={},0
  for k=1,math.min(NESTS,#nests) do
    for name,wt in pairs(nests[k].weights) do mix[name]=(mix[name] or 0)+wt; total=total+wt end
  end
  local per=0
  for name,wt in pairs(mix) do mix[name]=wt/total; per=per+mix[name]*threat_of(name) end
  local pollution,buildings=pull_of(surface,base)
  local r=TeamWaves.ramp(minutes,evolution,pollution,buildings,s)
  local aim=TeamWaves.aim(r)
  local us=TeamWaves.defenders(surface,base,target and target.position or centre)
  local n=TeamWaves.fit(us,mix,aim)
  local ramped=floor*r*r   -- (the floor comes in with the ramp: nothing early, the full pace late)
  -- units of the mix: what the fight says it should beat (the mix's own strength is in that), or the floor's threat
  local floor_units=math.ceil(ramped*SMALL/math.max(1,per))
  local units=math.min(MAX_UNITS,math.max(n,floor_units))
  -- the units drawn, by the nests' weights; while the fight sets the size, the drawn ones are checked against it
  -- (a few strong ones drawn by chance make a small wave unbeatable): the strongest give way until it holds
  local drawn={}
  local live={}
  for k=1,math.min(NESTS,#nests) do live[k]=nests[k] end
  for k=1,units do drawn[k]=pick(live[(k-1)%#live+1].weights) end
  local trimmed,short=0,false
  if n>=floor_units and #us.shooters>0 then
    while true do
      local them,count={},{}
      for _,name in ipairs(drawn) do count[name]=(count[name] or 0)+1 end
      for name,c in pairs(count) do
        local proto=prototypes.entity[name]
        if proto then local f=Odds.foe_facts(proto); them[#them+1]={hp=f.hp,count=c,facts=f} end
      end
      if Odds.fight(us,them).ratio>=aim then break end
      if #drawn<=MIN_UNITS then short=true; break end   -- (never fewer: the minimum over the fight, said in the log)
      local worst,worst_cost=1,-1
      for k,name in ipairs(drawn) do if threat_of(name)>worst_cost then worst,worst_cost=k,threat_of(name) end end
      table.remove(drawn,worst)
      trimmed=trimmed+1
    end
  end
  local why=string.format("its defense %d shooters, sized to beat %d at odds %.2f (ramp %.2f: %.0f min, evolution %.3f, "
    .."pollution %.0f, %d buildings); floor %.1f small biters (%d units)%s: %d units%s",#us.shooters,n,aim,r,minutes,
    evolution,pollution,buildings,ramped,floor_units,floor_units>n and ", the floor over the fight" or "",#drawn,
    (trimmed>0 and (", "..trimmed.." strong ones drawn by chance left out") or "")
      ..(short and ", the minimum over the fight" or ""))
  local kinds,made,spent={},0,0
  local group=surface.create_unit_group{position=from,force="enemy"}
  -- the nearest NESTS send by turns; one that cannot place a unit twice gives way to the next nearest
  local senders,spare,misses,i={},NESTS+1,{},0
  for k=1,math.min(NESTS,#nests) do senders[k]=nests[k] end
  local next_unit=1
  while next_unit<=#drawn and #senders>0 do
    i=i+1
    local slot=(i-1)%#senders+1
    local source=senders[slot]
    local name=drawn[next_unit]
    if (source.weights[name] or 0)<=0 then
      -- (a stand-in nest that cannot make the drawn kind sends its own weakest, never a stronger one)
      local weakest,low
      for kind in pairs(source.weights) do
        if not low or threat_of(kind)<low or threat_of(kind)==low and kind<weakest then weakest,low=kind,threat_of(kind) end
      end
      name=(low and low<=threat_of(name)) and weakest or nil
    end
    local cost=name and threat_of(name) or 0
    if not name then next_unit=next_unit+1 end   -- (none it can make in its place: one fewer)
    local at=name and surface.find_non_colliding_position(name,source.nest.position,16,1)
    local unit=at and surface.create_entity{name=name,position=at,force="enemy"}
    if unit then
      group.add_member(unit)
      TeamWaves.from_nest(unit,source.nest.position,tick)
      kinds[name]=(kinds[name] or 0)+1
      made,spent=made+1,spent+cost
      next_unit=next_unit+1
    else
      misses[source]=(misses[source] or 0)+1
      if misses[source]>=2 then
        senders[slot]=nests[spare]   -- (nil when no spare is left: the turn order closes up)
        spare=spare+1
        if not senders[slot] then table.remove(senders,slot) end
      end
    end
  end
  if made==0 then group.destroy(); return nil,"no_units" end
  if target then
    group.set_command{type=defines.command.attack,target=target,distraction=defines.distraction.by_enemy}
  else
    group.set_command{type=defines.command.attack_area,destination=centre,radius=32,
      distraction=defines.distraction.by_enemy}
  end
  return {size=made,threat=math.floor(spent/SMALL+0.5),kinds=kinds,plot=base.plot.name,from=from,why=why,
    target=target and {name=target.name,x=target.position.x,y=target.position.y} or nil,tick=tick}
end

-- Each wave unit's nest, by unit number (bounded): where an attack on a plot really came from, for its strike back
-- (NPC Offense: "the nest the waves come from"; a wave's first hit is the base's edge, no nest near it; D-20).
TeamWaves.ORIGINS_KEPT=4000
function TeamWaves.from_nest(unit,at,tick)
  if not (unit and unit.valid and unit.unit_number) then return end
  storage.team=storage.team or {}
  local o=storage.team.wave_origins or {n=0,by={}}
  storage.team.wave_origins=o
  if not o.by[unit.unit_number] then o.n=o.n+1 end
  o.by[unit.unit_number]={x=at.x,y=at.y,tick=tick or game.tick}
  if o.n>TeamWaves.ORIGINS_KEPT then   -- (the oldest half dropped: units long dead or long arrived)
    local ticks={}
    for _,v in pairs(o.by) do ticks[#ticks+1]=v.tick end
    table.sort(ticks)
    local cut=ticks[math.floor(#ticks/2)]
    for k,v in pairs(o.by) do if v.tick<=cut then o.by[k]=nil; o.n=o.n-1 end end
  end
end
function TeamWaves.nest_of(unit)
  local o=storage.team and storage.team.wave_origins
  local at=o and unit and unit.valid and unit.unit_number and o.by[unit.unit_number]
  if at then return {x=at.x,y=at.y} end
  -- (one its own nest sent: the spawner it came from, where the game knows it)
  local ok,sp=pcall(function() return unit.commandable and unit.commandable.spawner end)   -- (2.0: its commandable's)
  if ok and sp and sp.valid then return {x=sp.position.x,y=sp.position.y} end
  return nil
end

function TeamWaves.tick(tick)
  if tick%CHECK~=0 or not TeamWorld.death_world() or not TeamWorld.ready() then return end
  local s=strength()
  if s<=0 then return end
  local w=state()
  if not w.start then
    if not (storage.npcs and next(storage.npcs)) then return end
    w.start,w.next_tick=tick,tick+math.floor(GRACE/s)
  end
  if tick<w.next_tick then return end
  local surface=game.surfaces[TeamWorld.state().surface_index]
  local evolution=game.forces.enemy.get_evolution_factor(surface)
  local minutes=(tick-(w.first or tick))/3600   -- (since the first wave sent: the grace and any wait no part of it)
  local size,interval=TeamWaves.pace(evolution,minutes,s)
  w.next_tick=tick+interval
  local all=bases(surface)
  local why_none="no_bases"
  table.sort(all,function(a,b)
    local na,nb=w.per_plot[a.plot.name] or 0,w.per_plot[b.plot.name] or 0
    if na~=nb then return na<nb end
    return a.plot.name<b.plot.name
  end)
  for _,base in ipairs(all) do
    local wave,why=TeamWaves.send(surface,base,size,evolution,minutes,s,tick)
    if wave then
      w.first=w.first or tick
      w.paused=nil
      w.sent=w.sent+1
      w.per_plot[base.plot.name]=(w.per_plot[base.plot.name] or 0)+1
      wave.evolution=math.floor(evolution*1000)/1000
      w.log[#w.log+1]=wave
      while #w.log>LOG do table.remove(w.log,1) end
      local parts={}
      local names={}
      for name in pairs(wave.kinds) do names[#names+1]=name end
      table.sort(names)
      for _,name in ipairs(names) do parts[#parts+1]=wave.kinds[name].." "..name end
      log("[npc-factions] death-world wave "..w.sent.." at minute "..math.floor(tick/3600).." on "..wave.plot..": "
        ..wave.size.." units, threat "..wave.threat.." ("..table.concat(parts,", ")..") from "..math.floor(wave.from.x)..","..math.floor(wave.from.y)
        ..", evolution "..wave.evolution..", next in "..math.floor(interval/60).." s; "..wave.why)
      return
    end
    w.per_plot[base.plot.name]=w.per_plot[base.plot.name] or 0   -- (no nest near it: the next base instead)
    w.last_skip={plot=base.plot.name,why=why,tick=tick}
    why_none=why
  end
  -- None could be sent (no base yet, no nest left in reach, no unit could stand): look again soon, and say so once.
  w.next_tick=tick+RETRY
  if w.paused~=why_none then
    w.paused=why_none
    log("[npc-factions] death-world waves wait at minute "..math.floor(tick/3600)..": "..why_none)
  end
end

return TeamWaves
