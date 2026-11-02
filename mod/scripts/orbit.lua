-- Getting to orbit from a designed base. The silo builds rockets on its own from the bus; once one
-- stands ready, the engineer brings a space platform starter pack (from its own chests, or crafted
-- from what its machines made), asks for a platform over Nauvis, loads the pack into the rocket and
-- launches. Every step is an ordinary job; platform_launch.lua checks the native evidence.
local Launch=require("scripts.platform_launch")
local Build=require("scripts.platform_build")
local Feed=require("scripts.space_feed")
local Orbit={}
local PACK="space-platform-starter-pack"
local PREPARE_AT=40   -- rocket parts: start making the pack while the last ones are built

local function silo_of(npc)
  for _,plan in ipairs(npc.design_history or {}) do
    for _,id in ipairs(plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and e.type=="rocket-silo" and e.force==npc.force then return e end
    end
  end
end
Orbit.silo=silo_of

local function request_of(npc)
  local s=storage.platform_launch
  return s and s.requests[npc.id]
end

-- In orbit: the engineer's platform has its hub.
function Orbit.reached(npc)
  local request=request_of(npc)
  return request~=nil and request.platform~=nil and request.platform.valid and request.platform.hub~=nil
end

-- Whether packs come down to its labs without hands: a landing pad, and its feed into the lab row (by robots or
-- inserters, never carried: npc.space_feed, laid by the ground side once it stands).
function Orbit.fed(npc)
  local feed=npc.space_feed
  if not feed then return false end
  for _,e in pairs(feed) do
    if not (e and e.valid) then return false end
  end
  return next(feed)~=nil
end

-- Whether the engineer's platform, in orbit, makes `item` (space science) and it reaches the labs: research counts it as
-- a pack it can get (counted before the feed stands, a research of it would sit chosen, never fed).
function Orbit.makes(npc,item)
  if not (Orbit.reached(npc) and Orbit.fed(npc)) then return false end
  return Build.makes(npc,request_of(npc).platform,item)
end

-- The next job toward the platform, or nil (and why). While it builds (`building`), only once a rocket stands ready:
-- the way to space goes before the build under way, its pack made ahead only between plans (R-8: on a running base it
-- was always building, and a ready rocket waited the whole run).
function Orbit.step(npc,tick,acquire,count,building)
  if not script.active_mods["space-age"] then return nil end
  local silo=silo_of(npc)
  if not silo then return nil end
  local tech=npc.force.technologies["rocket-silo"]
  if not (tech and tech.researched) then return nil end
  npc.orbit_wait=nil
  if Orbit.reached(npc) then
    local job,meta=Orbit.supply(npc,tick,acquire,count,building,silo)
    if job then return job,meta end
    local down,why=Orbit.ground(npc,acquire,count,building)
    if down then return down,why end
    if why then npc.orbit_wait=why end
    return nil,meta or why
  end
  local request=request_of(npc)
  if request and request.status=="native_launch_ordered" then return nil end
  local ready=silo.rocket_silo_status==defines.rocket_silo_status.rocket_ready
  if not ready and (building or (silo.rocket_parts or 0)<PREPARE_AT) then return nil end
  local cargo=silo.get_inventory(defines.inventory.rocket_silo_rocket)
  local loaded=cargo and cargo.valid and cargo.get_item_count({name=PACK,quality="normal"}) or 0
  if loaded<1 and count(npc,PACK)<1 then
    local job,why=acquire(npc,PACK,1,0)
    npc.orbit_wait=not job and ("pack:"..tostring(why)) or nil   -- (why the way to space waits, for the export)
    if job then return job,{upkeep="orbit",step="pack"} end
    return nil,why
  end
  if not ready then return nil,"waiting_for_rocket" end
  if not (request and request.platform and request.platform.valid) then
    return {kind="request_platform",platform_name=npc.name.." orbital platform"},{upkeep="orbit",step="request"}
  end
  if loaded<1 then
    return {kind="transfer",target=silo,item=PACK,count=1,mode="deposit",
      inventory=defines.inventory.rocket_silo_rocket},{upkeep="orbit",step="load"}
  end
  return {kind="launch_platform",target=silo},{upkeep="orbit",step="launch"}
end

-- In orbit: the engineer lays the platform's layout as ghosts (Build.place) and the platform builds them from its hub's
-- stock; what the hub lacks (less what is on its way up) the engineer loads into the next rocket and launches. Like the
-- first launch, a ready rocket goes before the build under way; the cargo is gathered ahead only between plans.
function Orbit.supply(npc,tick,acquire,count,building,silo)
  local platform=request_of(npc).platform
  Build.place(npc,platform,tick)
  local ready=silo.rocket_silo_status==defines.rocket_silo_status.rocket_ready
  if not ready and (building or (silo.rocket_parts or 0)<PREPARE_AT) then return nil end
  local cargo=ready and silo.get_inventory(defines.inventory.rocket_silo_rocket) or nil
  if cargo and not cargo.valid then cargo=nil end
  for _,need in ipairs(Build.needs(npc,platform,Launch.in_flight(npc),cargo)) do
    local item={name=need.item,quality="normal"}
    local want=math.min(need.count,cargo and cargo.get_insertable_count(item) or Build.lift(need.item))
    if want>0 then
      local held=count(npc,need.item)
      if cargo and held>0 then
        return {kind="transfer",target=silo,item=need.item,count=math.min(held,want),mode="deposit",
          inventory=defines.inventory.rocket_silo_rocket},{upkeep="orbit",step="cargo_load"}
      end
      if held<want then
        local job,why=acquire(npc,need.item,want,0)
        if job then return job,{upkeep="orbit",step="cargo_gather"} end
        if tostring(why):find("^crafting:") then return nil,why end   -- (on its way in its hands: not sent short)
        npc.orbit_wait=tostring(why)..":cargo_wait:"..need.item   -- (and on to the next item: what it can get goes up)
      end
    end
  end
  if cargo and not cargo.is_empty() then
    return {kind="launch_platform",target=silo},{upkeep="orbit",step="cargo_launch"}
  end
  return nil,npc.orbit_wait
end

-- A lab's input (a science pack): any lab prototype takes it. (Space science is a plain item in 2.x, not a tool.)
local science
local function is_science(item)
  if not science then
    science={}
    for _,lab in pairs(prototypes.get_entity_filtered{{filter="type",type="lab"}}) do
      for _,input in ipairs(lab.lab_inputs or {}) do science[input]=true end
    end
  end
  return science[item]==true
end

-- On the ground, once its platform makes science packs: a landing pad beside a lab and an inserter into it
-- (space_feed.lua); the pad keeps asking the platform for them. Once packs wait in the hub it goes before the build
-- under way, as a ready rocket does (on a running base it is always building: the packs waited the whole run).
function Orbit.ground(npc,acquire,count,building)
  local platform=request_of(npc).platform
  local packs,waiting={},false
  for name,n in pairs(Build.made(platform)) do
    if is_science(name) and Build.makes(npc,platform,name) then
      packs[#packs+1]=name
      waiting=waiting or n>0
    end
  end
  table.sort(packs)
  if #packs==0 then return nil end
  if Orbit.fed(npc) then
    for _,item in ipairs(packs) do Feed.ask(npc.space_feed.pad,item,Feed.REQUEST) end
    return nil
  end
  if building and not waiting then return nil end
  return Feed.step(npc,acquire,count,packs)
end

-- Its landing pad as a belt source for the site export (space_feed.lua; Factory's input hookup reads it).
function Orbit.pads(npc,origin)
  return Feed.export(npc,origin)
end

return Orbit
