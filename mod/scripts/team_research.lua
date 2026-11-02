-- Shared research in the team world: one tech tree for the players and every engineer. The players'
-- force leads: its research queue is the team's, and only players reorder or take things out of it.
-- The engineers' labs work on the same tech, their progress goes into the players' research, and a tech
-- anyone finishes (a trigger tech too) is finished for everyone. An engineer that wants a tech puts it
-- in the queue after every player's item, among the engineers' own picks by how much it is needed; one a
-- player takes out stays out for a while.
local TeamResearch={}

local VETO=108000          -- a pick a player removed is not put back for 30 game minutes
local MAX_WAITING=2        -- engineers' picks waiting in the queue at once
local PROBE=1800           -- with the picks waiting all less needed, an engineer asks again this often (to displace one)
-- How much an engineer's pick is needed (catalog_planner.research_need): a build waits on it now, a later goal waits on
-- it, the science tier and rocket path, the rest. In arrival order the 20-unit logistics two engineers' bus waited on
-- sat behind green science and steel and came at minute 33 (normal-7d; R-9).
local CLASS={a=1,b=2,c=3,d=4}
local MASTER="player"

-- (true while finished techs are being copied: the copies are no news, nobody announces them)
local copying=false

local function state()
  storage.team_research=storage.team_research or {added={},vetoed={},last={}}
  return storage.team_research
end

function TeamResearch.master() return game.forces[MASTER] end

-- When a research finished, in the game's log (comparing research orders across runs: R-9).
function TeamResearch.log_finished(event)
  local tech=event.research
  if not (tech and tech.valid) then return end
  log(string.format("[npc-factions] research finished %s tick %d force %s",tech.name,game.tick,tech.force.name))
end

-- The engineers' forces (every one in the roster).
local function npc_forces()
  local out,seen={},{}
  for _,npc in pairs(storage.npcs or {}) do
    local f=npc.force
    if f and f.valid and not seen[f.name] then seen[f.name]=true; out[#out+1]=f end
  end
  table.sort(out,function(a,b) return a.name<b.name end)
  return out
end

local function team_forces()
  local out={TeamResearch.master()}
  for _,f in ipairs(npc_forces()) do out[#out+1]=f end
  return out
end

-- Every tech any team force has researched, all of them get (a new engineer's force on arrival, or
-- after anything got out of step).
function TeamResearch.sync()
  copying=true
  local ok,err=pcall(function()
    local forces,done=team_forces(),{}
    for _,f in ipairs(forces) do
      for name,tech in pairs(f.technologies) do if tech.researched then done[name]=true end end
    end
    for _,f in ipairs(forces) do
      for name in pairs(done) do
        local t=f.technologies[name]
        if t and not t.researched then t.researched=true end
      end
    end
  end)
  copying=false
  if not ok then log("[npc-factions] team research sync: "..tostring(err)) end
end

-- A tech one team force finished goes to all of them. True when this event is one of those copies (the
-- other research handlers then leave it alone).
function TeamResearch.on_research_finished(event)
  if copying then return true end
  local tech=event.research
  local from=tech and tech.force
  local team=false
  for _,f in ipairs(team_forces()) do if f==from then team=true end end
  if not team then return false end
  TeamResearch.log_finished(event)
  copying=true
  local ok,err=pcall(function()
    for _,f in ipairs(team_forces()) do
      local t=f~=from and f.technologies[tech.name]
      if t and not t.researched then t.researched=true end
    end
  end)
  copying=false
  if not ok then log("[npc-factions] team research copy: "..tostring(err)) end
  state().added[tech.name]=nil
  return false
end

-- A player who takes an engineer's pick out of the queue has the last word on it.
function TeamResearch.on_research_cancelled(event)
  if not event.player_index then return end
  local s=state()
  for name in pairs(event.research or {}) do
    if s.added[name] then
      s.added[name]=nil
      s.vetoed[name]=game.tick+VETO
    end
  end
end

local function queued(force)
  local out={}
  for i,t in ipairs(force.research_queue or {}) do out[i]=t.name end
  return out
end

-- The more needed of two needs (a lower class; within one, the nearer goal).
local function merged(old,new)
  if not old then return new end
  if not new then return old end
  local co,cn=CLASS[old.class] or 3,CLASS[new.class] or 3
  if cn~=co then return cn<co and new or old end
  return {class=old.class,step=math.min(old.step or 99,new.step or 99)}
end

-- The packs of a queued tech nothing makes (TeamResearch.unmade, set by designed_start: DesignedStart.pack_made; unset:
-- none). A pick queued on such packs holds the labs still (the user's labs, 41 minutes on military packs never made).
TeamResearch.unmade=nil
local function made(master,name)
  local tech=master.technologies[name]
  local check=TeamResearch.unmade
  return not (check and tech) or #check(tech)==0
end

-- A queued pick's sort key: its packs made first, then its class, the goal it serves, its lab time (units x time per
-- unit), its name.
local function key(s,master,name)
  local need=s.added[name] and s.added[name].need or {}
  local tech=master.technologies[name]
  local units=tech and tech.research_unit_count or 0
  local energy=tech and tech.research_unit_energy or 0
  return {made(master,name) and 0 or 1,CLASS[need.class] or 3,need.step or 99,units*energy,name}
end
local function before(a,b)
  for i=1,5 do if a[i]~=b[i] then return a[i]<b[i] end end
  return false
end

-- Whether the queue's i-th item is one the engineers may move or take out: their own pick, and not the research under
-- way (the first) unless nothing makes its packs (it cannot go on). A player's item is never moved, taken out or passed:
-- a player has the final say over it.
local function movable(s,master,queue,i)
  return s.added[queue[i]]~=nil and (i>1 or not made(master,queue[1]))
end

-- Each run of the engineers' picks between players' items sorted by need, those whose packs nothing makes last
-- (TeamResearch.request keeps it so). True if moved.
function TeamResearch.order()
  local master=TeamResearch.master()
  local s,queue=state(),queued(master)
  local out,changed,i={},false,1
  while i<=#queue do
    if movable(s,master,queue,i) then
      local run,keys,j={},{},i
      while j<=#queue and movable(s,master,queue,j) do
        run[#run+1]=queue[j]; keys[queue[j]]=key(s,master,queue[j]); j=j+1
      end
      table.sort(run,function(a,b) return before(keys[a],keys[b]) end)
      for k,name in ipairs(run) do
        out[#out+1]=name
        if queue[i+k-1]~=name then changed=true end
      end
      i=j
    else
      out[#out+1]=queue[i]; i=i+1
    end
  end
  if changed then master.research_queue=out end
  return changed
end

-- The queued techs some of whose packs nothing makes, and those packs (for the planners: that pack's line a goal).
function TeamResearch.waits()
  local master,out=TeamResearch.master(),{}
  local check=TeamResearch.unmade
  if not check then return out end
  for _,name in ipairs(queued(master)) do
    local tech=master.technologies[name]
    local packs=tech and check(tech) or {}
    if #packs>0 then out[#out+1]={tech=name,packs=packs,by=state().added[name] and "engineer" or "player"} end
  end
  return out
end

-- An engineer asks for a tech, with how much it is needed (`need`, {class=, step=}): it goes into the team's queue
-- among the engineers' picks by need (never ahead of a player's item), unless a player took it out lately or enough
-- engineers' picks are waiting already and none of them is less needed (that one, waiting and not under way, gives
-- way: the cap holds). True, or nil and why.
function TeamResearch.request(npc,name,tick,need)
  local master=TeamResearch.master()
  local tech=master.technologies[name]
  if not tech then return nil,"unknown_technology" end
  if tech.researched then return nil,"researched" end
  local s=state()
  local until_tick=s.vetoed[name]
  if until_tick and tick<until_tick then return nil,"a player took it out of the queue" end
  TeamResearch.order()   -- (as things stand now: a pick whose packs stopped being made goes back first)
  local queue=queued(master)
  local waiting={}
  for i,q in ipairs(queue) do
    if q==name then
      if s.added[q] then s.added[q].need=merged(s.added[q].need,need); TeamResearch.order() end
      return true
    end
    if s.added[q] and i>1 then waiting[#waiting+1]=i end   -- (the one under way is not waiting)
  end
  if #waiting>=MAX_WAITING then
    -- (the least needed waiting pick of the engineers', if this one is needed more: one on packs nothing makes first)
    -- (only one past the last player's item: taking out one before it would move the player's item up, and could
    -- take out what the player's research needs first)
    local last=0
    for i,q in ipairs(queue) do if not s.added[q] then last=i end end
    local mine={0,CLASS[need and need.class] or 3,need and need.step or 99}
    local worst,worst_key
    for _,i in ipairs(waiting) do
      if i>last and movable(s,master,queue,i) then
        local k=key(s,master,queue[i])
        if k[1]>mine[1] or (k[1]==mine[1] and (k[2]>mine[2] or (k[2]==mine[2] and k[3]>mine[3]))) then
          if not worst_key or before(worst_key,k) then worst,worst_key=i,k end
        end
      end
    end
    if not worst then s.probe_at=tick+PROBE; return nil,"queue_full" end
    local out={}
    for i,q in ipairs(queue) do if i~=worst then out[#out+1]=q end end
    s.added[queue[worst]]=nil
    master.research_queue=out
  end
  local ok=master.add_research(name)
  if not ok then return nil,"cannot_queue" end
  s.added[name]={by=npc and npc.id,tick=tick,need=need}
  TeamResearch.order()
  return true
end

-- Room in the queue for another engineer's pick (so engineers only weigh up research when one would be taken): fewer
-- picks waiting than the cap, or now and then, while one waiting is less needed than "a build waits on it".
function TeamResearch.room()
  local s,master=state(),TeamResearch.master()
  local queue=queued(master)
  local waiting,displaceable,last=0,false,0
  for i,q in ipairs(queue) do if not s.added[q] then last=i end end
  for i,name in ipairs(queue) do
    if s.added[name] and i>1 then   -- (the one under way is not waiting)
      waiting=waiting+1
      local k=key(s,master,name)
      if i>last and movable(s,master,queue,i) and (k[1]>0 or k[2]>CLASS.a) then displaceable=true end
    end
  end
  if waiting<MAX_WAITING then return true end
  -- (now and then: one waiting is less needed, or the labs stand on the one under way, its packs made by nothing)
  return (displaceable or (queue[1] and movable(s,master,queue,1))) and game.tick>=(s.probe_at or 0)
end

-- A tech worth offering an engineer: not in the queue already, not one a player took out lately.
function TeamResearch.open(name,tick)
  local until_tick=state().vetoed[name]
  if until_tick and tick<until_tick then return false end
  for _,q in ipairs(queued(TeamResearch.master())) do if q==name then return false end end
  return true
end

-- What the team is researching, for the engineers' strategists and planners.
function TeamResearch.view()
  local master=TeamResearch.master()
  local s=state()
  local queue={}
  for _,name in ipairs(queued(master)) do
    queue[#queue+1]={name=name,by=s.added[name] and "engineer" or "player"}
  end
  local current=master.current_research
  return {current=current and current.name,progress=current and math.floor(master.research_progress*100) or nil,
    queue=queue}
end

-- Once a second: every engineer's labs work on the team's current tech, and what they did goes into it.
function TeamResearch.tick(tick)
  -- (every ten seconds the engineers' picks sorted again: labs standing on packs nothing makes get the one that runs,
  -- without waiting for a new pick; lesson 19)
  if tick%600==7 and TeamResearch.unmade then TeamResearch.order() end
  if tick%60~=7 then return end
  local master=TeamResearch.master()
  local current=master.current_research
  local s=state()
  local gained=0
  local forces=npc_forces()
  for _,f in ipairs(forces) do
    local mine=f.current_research
    if current and mine and mine.name==current.name then
      local last=s.last[f.name]
      if last then gained=gained+math.max(0,f.research_progress-last) end
    end
  end
  if current and gained>0 then
    local progress=master.research_progress+gained
    if progress>=1 then
      master.technologies[current.name].researched=true   -- (copied to everyone, the queue moves on)
      current=master.current_research
    else
      master.research_progress=progress
    end
  end
  for _,f in ipairs(forces) do
    if not current then
      if f.current_research then f.research_queue={} end
      s.last[f.name]=nil
    else
      local mine=f.current_research
      if not (mine and mine.name==current.name) then
        f.research_queue={current.name}
        mine=f.current_research
      end
      if mine and mine.name==current.name then
        f.research_progress=master.research_progress
        s.last[f.name]=f.research_progress
      else
        s.last[f.name]=nil
      end
    end
  end
end

return TeamResearch
