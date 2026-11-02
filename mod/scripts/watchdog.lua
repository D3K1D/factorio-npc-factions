-- Whether each engineer gets anywhere (SO-5, the user: "How do we keep getting the NPCs stuck and not immediately
-- ending the run? Stuck in thought or planning"). Most stalls were one class: a wait or hold with no bound (W-10,
-- W-16, W-17, W-18, W-24, W-28, D-16, O-4, ST-2, R-7, SO-4). This is the bound over all of them.
--
-- Its progress is what it does itself, not what its machines make (a running factory hides an engineer stuck in
-- thought): buildings its force placed, plans and their stages finished, research advancing, jobs done. Nothing of
-- that for STALL_MINUTES (LONG_MINUTES on a trip, scouting, a fight) and it is stalled:
--   * one "[npc-factions] npc=N STALL ..." log line (phase, goal, the design's status and reason, the plan's stage),
--     for test harnesses to stop on;
--   * its strategist hears it (the Agent event `stalled`), to think again;
--   * its goal goes to the planner as `stalled` with the next request, to be held as a failure (companion/failures.py);
--   * still nothing after UNSTICK_AFTER times as long: the plan it builds is given up as one that cannot be finished,
--     the one it waits on asked again, a long deferral cut short (DesignedStart.unstick), and "UNSTICK" logged.
--
-- Progress that repeats is none (SO-7): the same goal finished on the same target again and again while the same
-- blocker stands (REPEATS times in REPEAT_WINDOW: a breach repaired every 3 minutes all run, a wall plan blocked
-- "entity_failed:...:target_exhausted" between, checkpoints\bus) is stalled at once, logged with reason=repeat and the
-- repeated key. Work that repeats with nothing blocked (supply rounds, refuels: jobs, not plans) is progress as ever.
local Watchdog={}

Watchdog.EVERY=600            -- ticks between looks at its progress
Watchdog.STALL_MINUTES=5      -- game minutes of no progress: stalled
Watchdog.LONG_MINUTES=15      -- (on a trip, scouting or in a fight: they take longer with nothing built)
Watchdog.UNSTICK_AFTER=3      -- times the stall before its plan or wait is set aside
Watchdog.GRACE=3*3600         -- ticks after its start before it is watched (the first plan is on its way)
Watchdog.REPEATS=3            -- the same plan finished this often with the same blocker standing...
Watchdog.REPEAT_WINDOW=15*3600   -- ...within this many ticks: no progress
local LONG={"scout","trip","visit","march","homeward","drive","combat","war","attack","raid","guard","help","fight"}

local function call(f,...)
  local ok,out=pcall(f,...)
  if ok then return out end
end

-- Buildings its force has placed, all told (the game's own count).
local function built(npc)
  local force,c=npc.force,npc.character
  if type(force)~="table" and type(force)~="userdata" then return 0 end
  local stats=force.get_entity_build_count_statistics and call(force.get_entity_build_count_statistics,c.surface)
  local counts=stats and call(function() return stats.input_counts end)
  local n=0
  for _,k in pairs(counts or {}) do n=n+(tonumber(k) or 0) end
  return n
end

local function research(npc)
  local force=npc.force
  local now=call(function() return force.current_research end)
  local name=call(function() return now and now.name end)
  local progress=call(function() return force.research_progress end) or 0
  return tostring(name or "-")..":"..tostring(math.floor((tonumber(progress) or 0)*1000))
end

-- Work of its hands done since the last look: every job finished since (the worker's receipts, in order), with
-- something built, crafted, mined or moved (walking, and a job that came to nothing, are no progress: a loop of them
-- is a stall).
local WORK={build=true,build_run=true,craft=true,mine=true,transfer=true,landfill=true,belt_pickup=true}
local function jobs(npc,w)
  local order=npc.receipt_order or {}
  local from=(w.read or 0)+1
  if from>#order+1 then from=1 end   -- (the order started again)
  for i=from,#order do
    local r=npc.receipts and npc.receipts[order[i]]
    if r and r.status=="complete" and WORK[r.kind]
        and ((tonumber(r.count) or 0)>0 or (tonumber(r.completed) or 0)>0 or r.kind=="build" or r.kind=="build_run") then
      w.done=(w.done or 0)+1
    end
  end
  w.read=#order
  return w.done or 0
end

-- The newest plan finished whole (a partial one put away when a plan is given up is no progress; and its history is
-- capped, so not a count).
local function finished(npc)
  local history=npc.design_history or {}
  for i=#history,1,-1 do
    local plan=history[i]
    if not (type(plan)=="table" and plan.partial) then
      return tostring(type(plan)=="table" and plan.plan_id or "?")..":"..tostring(type(plan)=="table" and plan.accepted_tick or "-")
    end
  end
  return "-"
end

local function newest(npc)
  local history=npc.design_history or {}
  for i=#history,1,-1 do
    local plan=history[i]
    if type(plan)=="table" and not plan.partial then return plan end
  end
end

-- Where a plan builds: its first building's place, to 16 tiles (a repair at one breach, not another).
local function target(plan)
  local first=plan.entities and plan.order and plan.entities[plan.order[1]]
  local at=type(first)=="table" and (first.world or first.position)
  if type(at)~="table" then return "-" end
  return math.floor((tonumber(at.x) or 0)/16)..","..math.floor((tonumber(at.y) or 0)/16)
end

-- What a plan builds: how many pieces of what (a repair of 17 walls is not one of 6 with turrets).
local function pieces(plan)
  local count,names=0,{}
  for _,id in ipairs(plan.order or {}) do
    local e=plan.entities and plan.entities[id]
    count=count+1
    local name=type(e)=="table" and tostring(e.name or e.prototype or "?") or "?"
    names[name]=(names[name] or 0)+1
  end
  local out={}
  for name,n in pairs(names) do out[#out+1]=name.."="..n end
  table.sort(out)   -- (deterministic, whatever order pairs gives)
  return count..":"..table.concat(out,",")
end

-- Buildings of its force destroyed, all told (the game's kill statistics: what it lost).
local function losses(npc)
  local force,c=npc.force,npc.character
  if type(force)~="table" and type(force)~="userdata" then return 0 end
  local stats=force.get_kill_count_statistics and call(force.get_kill_count_statistics,c.surface)
  local counts=stats and call(function() return stats.output_counts end)
  local n=0
  for _,k in pairs(counts or {}) do n=n+(tonumber(k) or 0) end
  return n
end

-- A plan finished: whether it is one finished again and again on the same target with the same blocker standing
-- (the reason its design was last blocked or deferred, within the window). Sets w.repeating to its key once it is.
local function repeats(npc,w,tick)
  local d=npc.design
  if d and (d.status=="blocked" or d.status=="deferred") and d.reason then
    local reason=tostring(d.reason):sub(1,80)
    if w.blocker and reason~=w.blocker then w.repeating,w.repeats=nil,nil end   -- (another blocker: a new story)
    w.blocker,w.blocker_goal,w.blocker_tick=reason,d.purpose,tick
  elseif d and w.blocker and d.purpose==w.blocker_goal and (d.status=="building" or d.status=="complete") then
    -- (the blocked goal going again: its blocker gone, and the loop it held with it)
    w.blocker,w.blocker_goal,w.blocker_tick,w.repeating,w.repeats=nil,nil,nil,nil,nil
  end
  local fin=finished(npc)
  if w.finished==nil then w.finished=fin end
  if fin==w.finished then
    if w.repeating and tick-(w.repeat_tick or 0)>Watchdog.REPEAT_WINDOW then w.repeating=nil end
    return
  end
  w.finished=fin
  local plan=newest(npc)
  if not (plan and w.blocker and tick-(w.blocker_tick or 0)<=Watchdog.REPEAT_WINDOW) then
    w.repeating=nil
    return
  end
  -- (the same pieces on the same target: a repair of another size, or after something was destroyed again, is the
  -- work of a base under attack, not a loop; Optimization's stage-nuclear, a breach thickened layer by layer)
  local key=tostring(plan.purpose or "-").."@"..target(plan).."#"..pieces(plan).."|"..w.blocker
  local lost=losses(npc)
  -- (losses since its last finish, anywhere on its base: every streak starts over, and a repeat stall ends. A loss
  -- elsewhere hides a loop a while longer; holding a repair while biters break the wall is worse)
  if lost>(w.lost or 0) then w.repeats,w.repeating=nil,nil end
  w.lost=lost
  w.repeats=w.repeats or {}
  local seen={}
  for _,t in ipairs(w.repeats[key] or {}) do
    if tick-t<=Watchdog.REPEAT_WINDOW then seen[#seen+1]=t end
  end
  -- (the blocker seen again since its last finish: standing all along, not one look long ago)
  if #seen>0 and (w.blocker_tick or 0)<=seen[#seen] then seen={} end
  seen[#seen+1]=tick
  for k in pairs(w.repeats) do if k~=key then w.repeats[k]=nil end end   -- (another key: that streak is over)
  w.repeats[key]=seen
  if #seen>=Watchdog.REPEATS then
    w.repeating,w.repeat_tick,w.repeat_goal=key,tick,plan.purpose
  elseif w.repeating~=key then
    w.repeating=nil
  end
end

-- Whether the worker still has this job (in hand or queued): one it has not is lost.
local function has_job(npc,id)
  if npc.current_job and npc.current_job.id==id then return true end
  for _,job in ipairs(npc.jobs or {}) do
    if job.id==id then return true end
  end
  return false
end

-- What it has got done: buildings placed, plans finished, research, work of its hands. (Not its design's status or
-- plan: a goal asked, deferred and asked again flips them with nothing done, SO-4.)
function Watchdog.mark(npc,w)
  return table.concat({built(npc),finished(npc),research(npc),jobs(npc,w)},"|")
end

function Watchdog.limit(npc)
  local phase=tostring(npc.phase or "")
  for _,word in ipairs(LONG) do
    if phase:find(word,1,true) then return Watchdog.LONG_MINUTES end
  end
  return Watchdog.STALL_MINUTES
end

function Watchdog.describe(npc,minutes)
  local d=npc.design or {}
  return {minutes=minutes,phase=tostring(npc.phase or "-"):sub(1,80),goal=tostring(d.purpose or "-"),
    status=tostring(d.status or "-"),reason=tostring(d.reason or "-"):sub(1,80),stage=tostring(d.stage_index or "-")}
end

local function line(npc,word,s)
  return "[npc-factions] npc="..tostring(npc.id).." "..word.." minutes="..s.minutes.." phase="..s.phase
    .." goal="..s.goal.." status="..s.status.." reason="..s.reason.." stage="..s.stage
end

local function flag(npc,tick,idle,repeated,goal)
  local s=Watchdog.describe(npc,math.floor(idle/3600))
  if repeated then   -- (the goal that repeats, held by the planner: not the blocked one its design is back on)
    s.reason="repeat key="..repeated
    s.goal=tostring(goal or s.goal)
  end
  log(line(npc,"STALL",s))
  npc.stalled={goal=s.goal,phase=s.phase,status=s.status,reason=s.reason,minutes=s.minutes,tick=tick}
  local ok,Agent=pcall(require,"scripts.agent")
  if ok and Agent and Agent.event then
    call(Agent.event,npc,{kind="stalled",minutes=s.minutes,phase=s.phase,goal=s.goal,status=s.status,why=s.reason})
  end
end

local function unstick(npc,tick,idle)
  local s=Watchdog.describe(npc,math.floor(idle/3600))
  -- a job it waits on that never came back (no receipt all this while): cancelled, so its receipt frees the rest
  local job=npc.pending_job
  local cancelled=false
  if job and not (npc.receipts and npc.receipts[job]) and not has_job(npc,job) then
    local ok,Worker=pcall(require,"scripts.worker")
    cancelled=ok and Worker and Worker.cancel_job and call(Worker.cancel_job,npc,job,"stalled") and true or false
  end
  local ok,D=pcall(require,"scripts.designed_start")
  local done=ok and D and D.unstick and call(D.unstick,npc,tick,"stalled:"..s.phase)
  log(line(npc,"UNSTICK",s).." acted="..tostring(done and true or false).." job_cancelled="..tostring(cancelled))
end

function Watchdog.tick(npc,tick)
  local c=npc.character
  if not (c and c.valid) then npc.watch=nil; return end
  local w=npc.watch
  if not w then
    w={since=tick,checked=tick}
    npc.watch=w
    w.mark=Watchdog.mark(npc,w)
    return
  end
  if tick-w.checked<Watchdog.EVERY then return end
  w.checked=tick
  repeats(npc,w,tick)
  local mark=Watchdog.mark(npc,w)
  -- (what changed is the same plan again: no progress, stalled at once. While it loops, its builds and crafts are the
  -- loop's own; only a different plan finished, or the blocker going, ends it)
  if w.repeating then
    w.mark=mark
    if not w.told and tick>=Watchdog.GRACE then w.told=tick; flag(npc,tick,tick-w.since,w.repeating,w.repeat_goal) end
    local limit=Watchdog.limit(npc)*3600
    if tick-w.since>=Watchdog.UNSTICK_AFTER*limit and not w.unstuck then
      w.unstuck=tick; unstick(npc,tick,tick-w.since)
    end
    return
  end
  if mark~=w.mark then
    if w.told then log("[npc-factions] npc="..tostring(npc.id).." UNSTALLED minutes="..math.floor((tick-w.since)/3600)) end
    w.mark,w.since,w.told,w.unstuck=mark,tick,nil,nil
    npc.stalled=nil
    return
  end
  if tick<Watchdog.GRACE then return end
  local limit,idle=Watchdog.limit(npc)*3600,tick-w.since
  if idle>=limit and not w.told then w.told=tick; flag(npc,tick,idle) end
  if idle>=Watchdog.UNSTICK_AFTER*limit and not w.unstuck then w.unstuck=tick; unstick(npc,tick,idle) end
end

return Watchdog
