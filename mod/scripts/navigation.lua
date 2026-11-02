-- All mutable navigation state belongs to the saved job and storage.path_requests.
local Navigation = {}
local Movement = require("scripts.movement_policy")
local Routes = require("scripts.combat_routes")
local Drive = require("scripts.drive")
local MAX_REQUESTS, PATH_TIMEOUT, STALL_TICKS = 5, 600, 180
local MAX_LOCAL_RECOVERIES, RECOVERY_TICKS = 8, 90
local TURNS = {0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8}   -- long trips veer this far (radians) around lakes

local function position(p) return {x = p.x or p[1], y = p.y or p[2]} end
local function distance(a, b)
  local dx, dy = a.x - b.x, a.y - b.y
  return math.sqrt(dx * dx + dy * dy)
end
Navigation.distance = distance

function Navigation.observe(npc)
  local character=npc.character
  local job=npc.current_job
  local nav=job and job.navigation
  local waypoint=nav and nav.path and nav.path[nav.index]
  local p=waypoint and position(waypoint.position or waypoint)
  local result={position=character and character.valid and character.position or nil,
    job_id=job and job.id,job_kind=job and job.kind,
    goal=nav and nav.goal,path_goal=nav and nav.path_goal,
    segmented=nav and nav.segmented or false,requests=nav and nav.requests or 0,
    revision=nav and nav.revision or 0,request_id=nav and nav.request_id,
    index=nav and nav.index,path_length=nav and nav.path and #nav.path or 0,
    waypoint=p,best_distance=nav and nav.best_distance,
    progress_tick=nav and nav.progress_tick,retry_at=nav and nav.retry_at,
    reason=nav and nav.reason,failed=nav and nav.failed,
    recovery=nav and nav.recovery and {target=nav.recovery.target,
      kind=nav.recovery.kind,started_tick=nav.recovery.started_tick,
      best_distance=nav.recovery.best_distance},
    recovery_attempts=nav and nav.recovery_attempts or 0,
    failed_targets=nav and nav.failed_targets or {},
    idle_yield=npc.movement_idle and {target=npc.movement_idle.target,
      kind=npc.movement_idle.kind,until_tick=npc.movement_idle.until_tick},
    total_recoveries=npc.navigation_recovery_count or 0}
  if character and character.valid then
    result.current_tile=character.surface.get_tile(character.position).name
    if p then result.waypoint_tile=character.surface.get_tile(p).name end
  end
  return result
end

function Navigation.stop(character)
  Movement.halt(character)   -- facing the way it walked
end

-- Wedged among trees or rocks (a walk that stalls with one touching it): it chops the one in its way,
-- as a player would, at the engine's own mining speed, then walks on.
-- (A rock is bigger than a tree: one touching it has its middle farther off; run 17 stood 25 minutes
-- wedged between a huge rock, a big rock and trees.)
local CHOP_NEAR, ROCK_NEAR, CHOP_GIVE_UP, MAX_CHOPS = 1.3, 2.6, 600, 12
local function chop_begin(npc, nav, toward, tick)
  if (nav.chops or 0) >= MAX_CHOPS then return false end
  local c = npc.character
  local best, best_score
  for _, e in ipairs(c.surface.find_entities_filtered{position = c.position, radius = ROCK_NEAR,
      type = {"tree", "simple-entity"}}) do
    local touching = e.valid and distance(c.position, e.position) <= (e.type == "simple-entity" and ROCK_NEAR or CHOP_NEAR)
    if touching and e.minable and e.force.name == "neutral" then
      local ahead = toward and ((e.position.x - c.position.x) * (toward.x - c.position.x)
        + (e.position.y - c.position.y) * (toward.y - c.position.y)) or 0
      local score = distance(c.position, e.position) - (ahead > 0 and 0.5 or 0)
      if not best or score < best_score then best, best_score = e, score end
    end
  end
  if not best then return false end
  nav.chop = {target = best, until_tick = tick + CHOP_GIVE_UP}
  nav.chops = (nav.chops or 0) + 1
  npc.navigation_chops = (npc.navigation_chops or 0) + 1
  return true
end
Navigation.chop_begin = chop_begin
local function chopping(npc, nav, tick)
  local chop, c = nav.chop, npc.character
  if not chop.target.valid or tick >= chop.until_tick then
    nav.chop = nil
    c.mining_state = {mining = false}
    nav.best_distance, nav.progress_tick = nil, tick
    return false
  end
  Navigation.stop(c)
  c.selected = chop.target
  c.mining_state = {mining = true, position = chop.target.position}
  return true
end

-- Pressed against a turned box (a diagonal cliff piece, a car parked askew), the pathfinder sees the
-- engineer's own spot as blocked, though it fits there: every path from it fails, to any goal (run 57: 25
-- jobs and 12 game minutes lost flush against one diagonal cliff run; walking slides it flush along the
-- face). Engine probe on that map: flush or 1/256 tile off the face, no path; 2/256 off, every path;
-- straight boxes (walls, chests, straight cliff pieces) are fine flush. So the path is asked from a point
-- a little off any turned box it touches; the walk to the first waypoint starts from its feet.
local CLEAR = 1 / 32
local function turned(b)
  local o = b and b.orientation or 0
  return math.abs(o * 4 - math.floor(o * 4 + 0.5)) > 1e-6
end
local function layers_meet(a, b)
  for layer in pairs(a and a.layers or {}) do if b and b.layers and b.layers[layer] then return true end end
  return false
end
-- How far apart a box of half size hx, hy at p and a turned box are (the widest gap along their sides'
-- axes), and the way from the turned box to p along it.
local function gap_to(b, p, hx, hy)
  local cx, cy = (b.left_top.x + b.right_bottom.x) / 2, (b.left_top.y + b.right_bottom.y) / 2
  local w, h = (b.right_bottom.x - b.left_top.x) / 2, (b.right_bottom.y - b.left_top.y) / 2
  local a = (b.orientation or 0) * 2 * math.pi
  local u, v = {math.cos(a), math.sin(a)}, {-math.sin(a), math.cos(a)}
  local best, bx, by
  for _, n in ipairs({{1, 0}, {0, 1}, u, v}) do
    local along = (p.x - cx) * n[1] + (p.y - cy) * n[2]
    local gap = math.abs(along) - hx * math.abs(n[1]) - hy * math.abs(n[2])
      - w * math.abs(n[1] * u[1] + n[2] * u[2]) - h * math.abs(n[1] * v[1] + n[2] * v[2])
    if not best or gap > best then
      local s = along >= 0 and 1 or -1
      best, bx, by = gap, n[1] * s, n[2] * s
    end
  end
  return best, bx, by
end
function Navigation.start_from(character)
  local p = {x = character.position.x, y = character.position.y}
  local proto = character.prototype
  local box = proto and proto.collision_box
  if not (box and box.right_bottom) then return p end
  local hx, hy = box.right_bottom.x, box.right_bottom.y
  local mask = proto.collision_mask
  local layers = {}
  for layer in pairs(mask and mask.layers or {}) do layers[#layers + 1] = layer end
  -- only what it could be touching (its own box and a little more), and only what stops it
  local solid, near = {}, {}
  for _, e in ipairs(character.surface.find_entities_filtered{area = {{p.x - hx - 0.5, p.y - hy - 0.5},
      {p.x + hx + 0.5, p.y + hy + 0.5}}, collision_mask = layers}) do
    local b = e.valid and e ~= character and e.bounding_box
    if b and layers_meet(e.prototype and e.prototype.collision_mask, mask) then
      solid[#solid + 1] = b
      if turned(b) then near[#near + 1] = b end
    end
  end
  if not near[1] then return p end
  local feet = {x = p.x, y = p.y}
  for _ = 1, 3 do   -- in a corner two faces push it; a few rounds settle it
    local moved = false
    for _, b in ipairs(near) do
      local gap, nx, ny = gap_to(b, p, hx, hy)
      if gap < CLEAR and gap > -hx then
        p.x, p.y = p.x + nx * (CLEAR - gap), p.y + ny * (CLEAR - gap)
        moved = true
      end
    end
    if not moved then break end
  end
  -- a gap thinner than that on the other side (a wall or a shore a hair off): it asks from its feet as before
  for _, b in ipairs(solid) do if gap_to(b, p, hx, hy) < -1e-6 then return feet end end
  local e = 1e-3
  if character.surface.count_tiles_filtered{area = {{p.x - hx + e, p.y - hy + e}, {p.x + hx - e, p.y + hy - e}},
      collision_mask = layers, limit = 1} > 0 then return feet end
  return p
end

-- Stranded: paths keep failing from where it stands, to different goals (some other cause than a turned
-- box, or one not yet known): it walks back the way it came, without the pathfinder, along the crumbs it
-- dropped while walking, then asks again. Only once jobs with different goals fail there (one goal that
-- cannot be reached is the goal's fault, not the spot's).
local CRUMB, CRUMBS_KEPT, STRANDED_NEAR, STRANDED_FAILS, BACK_OFF, BACK_TICKS = 1.5, 24, 3, 3, 4, 600
local function crumb(npc, p)
  local crumbs = npc.crumbs or {}
  local last = crumbs[#crumbs]
  if last and distance(last, p) > 4 * CRUMB then crumbs, last = {}, nil end   -- moved some other way (drove, fought): a new trail
  npc.crumbs = crumbs
  if last and distance(last, p) < CRUMB then return end
  crumbs[#crumbs + 1] = {x = p.x, y = p.y}
  while #crumbs > CRUMBS_KEPT do table.remove(crumbs, 1) end
end
local function stranded(npc, from, goal)
  local t = npc.no_paths
  if not t or distance(t.at, from) > STRANDED_NEAR then
    t = {at = {x = from.x, y = from.y}, goals = {}, fails = 0}
    npc.no_paths = t
  end
  t.fails = t.fails + 1
  local new = true
  for _, g in ipairs(t.goals) do if distance(g, goal) < BACK_OFF then new = false; break end end
  if new then t.goals[#t.goals + 1] = {x = goal.x, y = goal.y} end
  return t.fails >= STRANDED_FAILS and #t.goals >= 2
end
local function back_off(npc, nav, tick)
  local crumbs, c = npc.crumbs or {}, npc.character
  local points = {}
  for i = #crumbs, 1, -1 do points[#points + 1] = crumbs[i] end
  npc.no_paths = nil
  if not points[1] then return false end
  npc.backtrack = {points = points, k = 1, from = {x = c.position.x, y = c.position.y}, until_tick = tick + BACK_TICKS,
    progress_tick = tick}
  npc.stranded_count = (npc.stranded_count or 0) + 1
  log("[npc-factions] npc=" .. tostring(npc.id) .. " stranded at " .. math.floor(c.position.x) .. ","
    .. math.floor(c.position.y) .. ": paths fail to every goal; walking back the way it came")
  -- the walk this job asked for gets a fresh try from where it ends up
  nav.failed, nav.requests, nav.recovery_attempts = nil, 0, 0
  return true
end
local function backtracking(npc, nav, tick)
  local b, c = npc.backtrack, npc.character
  local here = c.position
  local p = b.points[b.k]
  while p and distance(here, p) <= 0.5 do
    b.k, b.best, b.progress_tick = b.k + 1, nil, tick
    p = b.points[b.k]
  end
  if not p or distance(here, b.from) >= BACK_OFF or tick >= b.until_tick then
    -- the crumbs walked back over are spent: another back-off goes farther back, never toward the spot
    local crumbs = npc.crumbs or {}
    for _ = 1, b.k - 1 do table.remove(crumbs) end
    npc.backtrack = nil
    if nav.request_id and storage.path_requests then storage.path_requests[nav.request_id] = nil end
    nav.request_id, nav.path, nav.recovery = nil, nil, nil
    nav.retry_at, nav.best_distance, nav.progress_tick = tick, nil, tick
    Navigation.stop(c)
    return false
  end
  local d = distance(here, p)
  if not b.best or d < b.best - 0.08 then
    b.best, b.progress_tick = d, tick
  elseif tick - b.progress_tick >= 60 then   -- that crumb cannot be reached straight: the one before it
    b.k, b.best, b.progress_tick = b.k + 1, nil, tick
  end
  c.walking_state = {walking = true, direction = Movement.steer(nil, here, p)}
  return true
end

-- Its long walks (LONG_TRIP tiles or more, as the crow flies), the last TRIPS_KEPT: how far, how long, whether
-- driven. What a vehicle would save is judged from them (drive.lua Drive.travel, companion/vehicle_brain.py).
local LONG_TRIP, TRIPS_KEPT = 100, 40
local function trip_done(npc, nav, tick)
  local t = nav.trip
  nav.trip = nil
  if not t then return end
  npc.travel = npc.travel or {trips = {}}
  local list = npc.travel.trips
  list[#list + 1] = {t = tick, d = math.floor(t.d), ticks = tick - t.tick, drove = t.drove or nil}
  while #list > TRIPS_KEPT do table.remove(list, 1) end
end

-- A walk of its work driven (M-1: the user's ring sides are 255 tiles, upkeep rounds 260): with a vehicle of
-- its own that saves time (Drive.worth), it walks to the vehicle first if it is parked off, the autopilot
-- drives (it owns the body meanwhile: the worker waits), and it walks on from where it parks. A drive given
-- up is not tried again on this walk for DRIVE_RETRY. True while the drive has the walk.
local DRIVE_LOOK, DRIVE_RETRY, DRIVE_NEAR = 300, 18000, 10
local function reset_walk(nav, tick)
  if nav.request_id and storage.path_requests then storage.path_requests[nav.request_id] = nil end
  nav.request_id, nav.path, nav.index, nav.route, nav.stretch, nav.segmented = nil, nil, nil, nil, nil, nil
  nav.requests, nav.best_distance, nav.progress_tick, nav.retry_at = 0, nil, tick, nil
end
local function driving(npc, job, nav, tick)
  local d = npc.drive
  if d and d.job == job.id then
    if not (d.done or d.failed) then return true end
    if d.failed then nav.walk_until = tick + DRIVE_RETRY end
    npc.drive = nil
    reset_walk(nav, tick)
    return false
  end
  if d and not (d.done or d.failed) then return false end   -- (its own march has it: agent.lua)
  local c = npc.character
  local fetch = nav.fetch
  if fetch then
    if distance(c.position, fetch) <= 4 then
      nav.fetch = nil
      reset_walk(nav, tick)
      Drive.start(npc, nav.goal, DRIVE_NEAR, tick, {job = job.id})
      if nav.trip then nav.trip.drove = true end
      return true
    end
    if tick < (nav.fetch_until or 0) then return false end
    nav.fetch = nil   -- (not there in time: on foot)
    nav.walk_until = tick + DRIVE_RETRY
    reset_walk(nav, tick)
    return false
  end
  if tick < (nav.walk_until or 0) or tick < (nav.drive_look_at or 0) then return false end
  nav.drive_look_at = tick + DRIVE_LOOK
  local how, spot = Drive.worth(npc, nav.goal)
  if how == "fetch" then
    nav.fetch, nav.fetch_until = spot, tick + math.floor(2 * distance(c.position, spot) / 0.15) + 600
    reset_walk(nav, tick)
    return false
  elseif how == "drive" then
    Drive.start(npc, nav.goal, DRIVE_NEAR, tick, {job = job.id})
    if nav.trip then nav.trip.drove = true end
    return true
  end
  return false
end

function Navigation.clear(npc, job)
  local nav = job and job.navigation
  local d = npc.drive
  if job and d and d.job == job.id then   -- the walk it was driving gone: stopped, left where it stands
    Drive.stop(npc, "walk_cancelled")
    npc.drive = nil
  end
  if nav then
    Movement.finish(npc,nav.pending_episode,game.tick,"navigation_cleared",false)
    if nav.recovery then Movement.finish(npc,nav.recovery.episode,game.tick,"navigation_cleared",false) end
  end
  if nav and nav.request_id and storage.path_requests then
    storage.path_requests[nav.request_id] = nil
  end
  if job then job.navigation = nil end
  Navigation.stop(npc.character)
end

-- The pathfinder's cache stays off (OP-6, 2026-10-29, measured on a proving map with 400 trees and 60 rocks). It answers a
-- trip made before in 1 tick instead of 12, new trips about a third quicker but 2 percent longer, and it returned a
-- warmed route straight through a wall built after it (a fresh request went round): an engineer that builds where it
-- walks would stall on stale routes. prefer_straight_paths stays off too: 3 percent longer, a quarter slower to find.
Navigation.PATH_CACHE = false

local function retry(npc, nav, tick, reason)
  if nav.request_id then storage.path_requests[nav.request_id] = nil end
  nav.request_id, nav.path = nil, nil
  if nav.recovery then Movement.finish(npc,nav.recovery.episode,tick,reason,false) end
  nav.recovery=nil
  if reason=="no_path" or reason=="movement_stalled" then
    Movement.note_failure(nav,nav.path_goal or nav.goal,tick,reason)
  end
  nav.reason = reason
  nav.retry_at = tick + 60 * math.max(1, nav.requests)
  Navigation.stop(npc.character)
  local recoverable=reason=="no_path" or reason=="movement_stalled" or reason=="path_timeout"
  if nav.requests>=(nav.segmented and 2*#TURNS or MAX_REQUESTS) and
      (not recoverable or (nav.recovery_attempts or 0)>=MAX_LOCAL_RECOVERIES) then
    nav.failed=reason
  end
end

local function start_recovery(npc,nav,waypoint,tick,cause)
  -- Stuck once: this walk, and its walks for the next minute, keep to the path exactly (no corners
  -- cut among trees).
  if cause=="movement_stalled" then nav.strict=true; npc.walk_strict_until=tick+3600 end
  if (nav.recovery_attempts or 0)>=MAX_LOCAL_RECOVERIES then return false end
  local c=npc.character
  local choice,episode=Movement.choose(npc,nav,waypoint,tick,cause or "movement_stalled")
  if not choice then return false end
  nav.recovery_attempts=(nav.recovery_attempts or 0)+1
  npc.navigation_recovery_count=(npc.navigation_recovery_count or 0)+1
  nav.recovery={target=choice.target,started_tick=tick,start_position=position(c.position),
    best_distance=distance(c.position,choice.target),progress_tick=tick,
    episode=episode,kind=choice.id,follow_target=position(waypoint)}
  nav.reason=choice.id=="yield" and "yielding_to_traffic" or "walking_around_obstacle"
  return true
end

-- Returns "arrived", "active", or "failed", followed by a diagnostic reason.
function Navigation.step(npc, job, goal, radius, tick)
  local character = npc.character
  goal = position(goal)
  radius = math.max(0.15, radius or 0.3)
  if distance(character.position, goal) <= radius then
    local done = job.navigation
    if done and distance(done.goal, goal) <= 0.5 then trip_done(npc, done, tick) end
    Navigation.clear(npc, job)
    return "arrived"
  end
  local nav = job.navigation
  if not nav or distance(nav.goal, goal) > 0.5 or math.abs(nav.radius - radius) > 0.1 then
    if nav then Navigation.clear(npc, job) end
    local far = distance(character.position,goal)
    nav = {goal = goal, radius = radius, requests = 0, revision = 0, strict = tick < (npc.walk_strict_until or 0),
      recovery_budget_goal_distance=far, trip=far>=LONG_TRIP and {tick=tick,d=far} or nil}
    job.navigation = nav
  end
  nav.failed_targets=npc.navigation_failed_targets or nav.failed_targets or {}
  npc.navigation_failed_targets=nav.failed_targets
  nav.recovery_budget_goal_distance=nav.recovery_budget_goal_distance or distance(character.position,goal)
  if npc.backtrack then
    if backtracking(npc, nav, tick) then return "active" end
  else
    crumb(npc, character.position)
  end
  if nav.failed then return "failed", nav.failed end
  if driving(npc, job, nav, tick) then return "active" end
  if nav.chop and chopping(npc, nav, tick) then return "active" end
  if nav.request_id then
    Navigation.stop(character)
    if tick - nav.request_tick > PATH_TIMEOUT then
      Movement.finish(npc,nav.pending_episode,tick,"path_timeout",false)
      nav.pending_episode=nil
      retry(npc, nav, tick, "path_timeout")
      if not nav.failed then start_recovery(npc,nav,nav.path_goal or nav.goal,tick,"path_timeout") end
    end
    return "active"
  end
  if nav.recovery then
    local recovery=nav.recovery
    if recovery.kind=="yield" then
      Navigation.stop(character)
      if tick-recovery.started_tick<45 then return "active" end
      nav.recovery=nil
      if nav.path then
        Movement.finish(npc,recovery.episode,tick,"yielded",false)
      else
        nav.pending_episode=recovery.episode
      end
      nav.retry_at=tick+1
      return "active"
    end
    local d=distance(character.position,recovery.target)
    if d<=0.45 then
      nav.recovery=nil
      nav.pending_episode=recovery.episode
      nav.path,nav.index=nil,nil
      nav.best_distance,nav.progress_tick=nil,tick
      nav.retry_at=tick+1
      Navigation.stop(character)
      return "active"
    end
    if d<recovery.best_distance-0.08 then
      recovery.best_distance=d;recovery.progress_tick=tick
    elseif tick-(recovery.progress_tick or recovery.started_tick)>=RECOVERY_TICKS then
      nav.recovery=nil
      Movement.finish(npc,recovery.episode,tick,"recovery_timeout",false)
      Movement.note_failure(nav,recovery.target,tick,"recovery_timeout")
      local next_target=recovery.follow_target or nav.path_goal or nav.goal
      if chop_begin(npc,nav,next_target,tick) then return "active" end
      if not start_recovery(npc,nav,next_target,tick,"movement_stalled") then
        retry(npc,nav,tick,"movement_stalled")
      end
      return "active"
    end
    character.walking_state={walking=true,direction=Movement.steer(nil,character.position,recovery.target)}
    return "active"
  end
  if nav.segmented and nav.path_goal and distance(character.position,nav.path_goal)<=0.7 then
    nav.path,nav.index,nav.path_goal,nav.stretch=nil,nil,nil,nil
    nav.requests=0;nav.segmented=nil;nav.recovery_attempts=0
  end
  if not nav.path then
    Navigation.stop(character)
    if tick < (nav.retry_at or 0) then return "active" end
    nav.requests, nav.revision = nav.requests + 1, nav.revision + 1
    local remaining=distance(character.position,nav.goal)
    -- across country, or a shorter walk past a nest or worm it knows of: round them on a route
    -- (to its vehicle first, when it is to drive: straight there)
    local fetch=nav.fetch
    nav.segmented=not fetch and (remaining>160 or remaining>24 and Routes.straight_clear
      and not Routes.straight_clear(npc,character.position,nav.goal))
    nav.path_goal=fetch or nav.goal
    -- A new path after a stall (trees on a lake shore) keeps to the same stretch: aiming afresh each
    -- time sent it back and forth between two points. A new stretch only once this one is reached or
    -- no path to it is found.
    local stretch=nav.stretch
    if nav.segmented and stretch and not stretch.failed and distance(character.position,stretch.goal)>8 then
      nav.path_goal=stretch.goal
    elseif nav.segmented then
      -- Across country: toward the next point of a route round the nests, worms and enemy turrets it
      -- knows of (a fresh one now and then, or when it sees one the route did not know of), 128 tiles
      -- at a time; when a stretch fails (a lake, a wall), veer left and right, then go shorter. It does
      -- not stop for biters that keep their distance.
      local here=character.position
      local r=nav.route
      local marched=npc.marching and npc.marching.routed and tick<(npc.marching.until_tick or 0)
      if marched then
        r={points={nav.goal},k=1,until_tick=tick+600}   -- a march already chose this leg round the dangers
        nav.route=r
      elseif not r or tick>=r.until_tick or nav.replanning or (tick>=(r.look_at or 0) and Routes.new_sighting(npc,r)) then
        -- never into the biters' reach (their nests' range: a wall); planned a slice a tick (a far walk
        -- through nest country took seconds at once), the old route kept meanwhile, none: it waits
        local fresh=Routes.request(npc,nav.goal,nil,nil,0)
        if fresh then
          r=fresh
          r.until_tick=tick+7200
          nav.route,nav.replanning=r,nil
        elseif r then
          nav.replanning=true
        else
          nav.requests=nav.requests-1
          nav.retry_at=tick+1
          return "active"
        end
      end
      r.look_at=tick+120
      while r.points[r.k+1] and distance(here,r.points[r.k])<=12 do r.k=r.k+1 end
      local toward=r.points[r.k] or nav.goal
      if not marched then npc.marching={until_tick=tick+1200} end
      local angle=math.atan2(toward.y-here.y,toward.x-here.x)
      local reach=math.min(nav.requests>#TURNS and 64 or 128,math.max(16,distance(here,toward)))
      -- The stretch ends on dry land that gets it somewhere: a point out in a lake moves to the shore
      -- nearby, but a shore no farther than where it stands is no stretch at all (it asked again every
      -- tick), so the next angle round is tried instead.
      local surface=character.surface
      local goal
      for k=0,#TURNS-1 do
        local turn=TURNS[((nav.requests-1+k)%#TURNS)+1]
        local intermediate={x=here.x+math.cos(angle+turn)*reach,y=here.y+math.sin(angle+turn)*reach}
        local land=surface.find_non_colliding_position("character",intermediate,16,.5)
          or surface.find_non_colliding_position("character",intermediate,64,1)
        if land and distance(here,land)>=math.min(32,reach*.5) then goal=land; break end
        goal=goal or intermediate
      end
      nav.path_goal=goal
      nav.stretch={goal={x=goal.x,y=goal.y}}
    end
    -- Land nobody has been near is not generated yet, and no path crosses it: ask for the stretch
    -- ahead and wait for it (a little at a time, so the game never stalls generating a long route).
    local surface,from,to=character.surface,character.position,nav.path_goal
    if surface.is_chunk_generated then
      local ready=true
      for i=0,4 do
        local p={x=from.x+(to.x-from.x)*i/4,y=from.y+(to.y-from.y)*i/4}
        if not surface.is_chunk_generated({x=math.floor(p.x/32),y=math.floor(p.y/32)}) then
          surface.request_to_generate_chunks(p,2)
          ready=false
        end
      end
      if not ready then
        nav.requests=nav.requests-1
        nav.retry_at=tick+60
        return "active"
      end
    end
    storage.path_requests = storage.path_requests or {}
    local start = Navigation.start_from(character)
    nav.request_from = start
    local ok, id = pcall(function()
      return character.surface.request_path{
        bounding_box = character.prototype.collision_box,
        collision_mask = character.prototype.collision_mask,
        start = start, goal = nav.path_goal, radius = nav.segmented and .5 or fetch and 2 or nav.radius,
        force = character.force, entity_to_ignore = character, can_open_gates = true,
        pathfind_flags = {cache = Navigation.PATH_CACHE, prefer_straight_paths = false,
          allow_destroy_friendly_entities = false, allow_paths_through_own_entities = false},
      }
    end)
    if not ok or not id then
      Movement.finish(npc,nav.pending_episode,tick,"path_request_rejected",false)
      nav.pending_episode=nil
      if nav.stretch then nav.stretch.failed=true end
      retry(npc, nav, tick, "path_request_rejected")
      if not nav.failed then start_recovery(npc,nav,nav.path_goal or nav.goal,tick,"path_request_rejected") end
    else
      nav.request_id, nav.request_tick = id, tick
      storage.path_requests[id] = {npc_id = npc.id, job_id = job.id, revision = nav.revision}
    end
    return "active"
  end
  -- Waypoints within reach are passed without stopping, so the walk never halts between them.
  local passed, p, aim = Movement.follow(nav, character.position)
  if passed then
    nav.best_distance, nav.progress_tick = nil, tick
    local global_distance=distance(character.position,nav.goal)
    if global_distance<=(nav.recovery_budget_goal_distance or global_distance)-6 then
      nav.recovery_budget_goal_distance=global_distance
      nav.recovery_attempts=0
    end
  end
  if not p then
    retry(npc, nav, tick, "path_ended_outside_reach")
    return "active"
  end
  local d = distance(character.position, p)
  Movement.note_position(nav,character.position,tick)
  if not nav.best_distance or d < nav.best_distance - 0.12 then
    nav.best_distance, nav.progress_tick = d, tick
  elseif tick - nav.progress_tick >= STALL_TICKS then
    if chop_begin(npc,nav,p,tick) then return "active" end
    if not start_recovery(npc,nav,p,tick,"movement_stalled") then
      retry(npc, nav, tick, "movement_stalled")
    end
    return "active"
  end
  if tick>=(nav.next_traffic_check or 0) then
    nav.next_traffic_check=tick+30
    if Movement.priority_blocker(npc,nav.goal) then
      if start_recovery(npc,nav,nav.goal,tick,"traffic") then return "active" end
    end
  end
  character.walking_state = {walking = true, direction = Movement.steer(nav, character.position, aim)}
  return "active"
end

function Navigation.on_path_finished(event)
  local pending = storage.path_requests and storage.path_requests[event.id]
  if not pending then return end
  storage.path_requests[event.id] = nil
  local npc = storage.npcs and storage.npcs[pending.npc_id]
  local job = npc and npc.current_job
  local nav = job and job.navigation
  if not nav or job.id ~= pending.job_id or nav.revision ~= pending.revision
      or nav.request_id ~= event.id then return end
  nav.request_id = nil
  if event.try_again_later then
    Movement.finish(npc,nav.pending_episode,event.tick or game.tick,"pathfinder_busy",false)
    nav.pending_episode=nil
    retry(npc, nav, event.tick or game.tick, "pathfinder_busy")
  elseif not event.path or #event.path == 0 then
    if nav.stretch then nav.stretch.failed = true end   -- no way to that stretch: aim another next time
    Movement.finish(npc,nav.pending_episode,event.tick or game.tick,"no_path",false)
    nav.pending_episode=nil
    local tick = event.tick or game.tick
    local back = stranded(npc, nav.request_from or npc.character.position, nav.goal) and back_off(npc, nav, tick)
    retry(npc, nav, tick, "no_path")
    if not nav.failed and not back then start_recovery(npc,nav,nav.path_goal or nav.goal,tick,"no_path") end
  else
    npc.no_paths = nil
    Movement.finish(npc,nav.pending_episode,event.tick or game.tick,"path_reopened",true)
    nav.pending_episode=nil
    nav.path, nav.index = event.path, 1
    nav.best_distance, nav.progress_tick = nil, event.tick or game.tick
  end
end

-- Called after Worker.tick, before choosing new factory work. An unoccupied
-- actor gives human bodies and lower-numbered NPCs a short, physical passage.
-- A queued/active job always takes ownership back immediately.
function Navigation.idle_tick(npc,tick)
  local character=npc.character
  local project=npc.factory_project
  local ready=character and character.valid and not npc.current_job
    and not npc.pending_job and next(npc.jobs or {})==nil
    and not npc.social_job and not npc.ai_running
    and not (project and project.pending_job)
    and not npc.combat_target
    and not (type(npc.phase)=="string" and
      (string.find(npc.phase,"combat",1,true) or string.find(npc.phase,"fight",1,true)))
  local idle=npc.movement_idle
  if not ready then
    if idle then
      Movement.finish(npc,idle.episode,tick,"interrupted_by_job",false)
      npc.movement_idle=nil
    end
    return false
  end
  if idle then
    local reached=idle.kind~="yield" and distance(character.position,idle.target)<=.6
    if reached or tick>=idle.until_tick then
      Movement.finish(npc,idle.episode,tick,idle.kind=="yield" and "yielded"
        or reached and "reached_local_target" or "recovery_timeout",false)
      npc.movement_idle=nil
      npc.next_idle_traffic_tick=tick+180
      Navigation.stop(character)
      return false
    end
    if idle.kind=="yield" then Navigation.stop(character)
    else character.walking_state={walking=true,direction=Movement.steer(idle,character.position,idle.target)} end
    return true
  end
  if tick<(npc.next_idle_traffic_tick or 0) then return false end
  npc.next_idle_traffic_tick=tick+30
  local blocker=Movement.idle_blocker(npc)
  if not blocker then return false end
  local now=character.position
  local dx,dy=now.x-blocker.position.x,now.y-blocker.position.y
  local magnitude=math.sqrt(dx*dx+dy*dy)
  if magnitude<.05 then dx,dy,magnitude=(npc.id%2==0 and 1 or -1),0,1 end
  local goal={x=now.x+dx/magnitude*4,y=now.y+dy/magnitude*4}
  local choice,episode=Movement.choose(npc,{requests=0,progress_tick=tick},goal,tick,"idle_traffic")
  if not choice then return false end
  npc.movement_idle={target=choice.target,kind=choice.id,episode=episode,
    until_tick=tick+(choice.id=="yield" and 45 or 90)}
  if choice.id=="yield" then Navigation.stop(character)
  else character.walking_state={walking=true,direction=Movement.steer(npc.movement_idle,now,choice.target)} end
  return true
end

return Navigation
