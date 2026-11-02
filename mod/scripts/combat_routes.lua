-- Long walks on autopilot, the way a game's NPCs path (no AI in it): before setting off, the land along
-- the way is generated and surveyed (every nest and worm on it, and which chunks are mostly water), and
-- the whole route is planned round their detection range on a grid; the game's own pathfinder walks each
-- leg between the points. On the way it keeps looking (every 2 s, 200 tiles round): a nest it did not
-- know of, a group of biters on the move that would notice it on the way ahead, or a leg it cannot walk
-- makes a new route. As a player does, it slips between nests (each its own circle), not round fields.
local Routes = {}

local CELL = 12
local MARGIN = 384          -- how far a route may stray from the straight line (round a whole field)
-- A bigger area is searched on coarser cells (drive15: whole searches of 40000 cells, eighteen to a
-- car's route, held the game still for over five seconds).
local MAX_CELLS = 20000
local BUFFER = 8            -- room kept beyond a danger's detection range
local AVOID = 100           -- a cell in a nest's or turret's reach costs this much: long detours beat it
local WALL = 1000           -- a blocked cell (water, somewhere it found no way): only if there is no other way
local SOFT = 30            -- a cell of thick woods, for a car: round them when that is not much longer
local BUCKET = 64          -- dangers kept by squares this big, for checking a walk against those near it
local SIGHT = 200           -- what it notices round itself on the way, nests and biters on the move alike
local MAX_EXPANSIONS = MAX_CELLS  -- the whole grid at most (a route is planned again only when it is stale)
local WORM = {["small-worm-turret"] = 27, ["medium-worm-turret"] = 32, ["big-worm-turret"] = 40,
  ["behemoth-worm-turret"] = 52}
-- How far a nest's biters notice someone (the user, who walks past them: about 48 tiles); worms reach as
-- far as they spit; the turrets of those at war with it, as far as they shoot.
local REACH = {["unit-spawner"] = 48, ["ammo-turret"] = 24, ["electric-turret"] = 30, ["fluid-turret"] = 34,
  turret = 36}
-- A group of biters on the move (an attack or an expansion party): noticed this far (they see about 30),
-- grouped by 24-tile squares, and avoided while it was seen in the last 10 s.
local GROUP_REACH, GROUP_CELL, GROUP_FOR = 40, 24, 600
local ENEMY_TYPES = {"unit-spawner", "turret"}
local TURRET_TYPES = {"ammo-turret", "electric-turret", "fluid-turret"}

local function distance(a, b)
  local dx, dy = a.x - b.x, a.y - b.y
  return math.sqrt(dx * dx + dy * dy)
end

-- How far from a nest, worm or turret is out of its reach (a nest's defenders come out to about here).
function Routes.reach(kind, name)
  return WORM[name] or REACH[kind] or 30
end

-- Whether the straight walk a-b stays out of every danger's reach.
local function clear(a, b, threats)
  local dx, dy = b.x - a.x, b.y - a.y
  local len2 = dx * dx + dy * dy
  for _, t in ipairs(threats) do
    local u = len2 > 0 and math.max(0, math.min(1, ((t.x - a.x) * dx + (t.y - a.y) * dy) / len2)) or 0
    local px, py = a.x + u * dx - t.x, a.y + u * dy - t.y
    if px * px + py * py < (t.r + BUFFER) ^ 2 then return false end
  end
  return true
end
Routes.clear = clear

-- A small binary heap of {cost, index}.
local function push(heap, cost, index)
  heap[#heap + 1] = {cost, index}
  local i = #heap
  while i > 1 do
    local parent = math.floor(i / 2)
    if heap[parent][1] <= heap[i][1] then break end
    heap[parent], heap[i] = heap[i], heap[parent]
    i = parent
  end
end
local function pop(heap)
  local top = heap[1]
  local last = table.remove(heap)
  if #heap > 0 then
    heap[1] = last
    local i = 1
    while true do
      local l, r, m = 2 * i, 2 * i + 1, i
      if heap[l] and heap[l][1] < heap[m][1] then m = l end
      if heap[r] and heap[r][1] < heap[m][1] then m = r end
      if m == i then break end
      heap[m], heap[i] = heap[i], heap[m]
      i = m
    end
  end
  return top
end

-- The points to walk through from `from` to `to` (`to` last), keeping out of the reach of every
-- threat ({x, y, r}); those within `spare` of the goal are the place being attacked and are not
-- avoided. Straight there when nothing is in the way. `wide` (0 or more): biters' reach (nests and
-- worms) is a wall, this much wider than it, crossed only if there is no other way. Also returns whether
-- the route keeps out of every reach (but those it starts or ends in, which it cannot help) and how
-- many it crosses.
-- (It goes in steps, a slice of work at a time when asked through Routes.request: the dangers painted
-- on a grid, the search, the straightening. Routes.plan does them all at once.)
local function grid(prep, from, to)
  if prep.cell then return end
  local gx0, gy0 = math.min(from.x, to.x) - MARGIN, math.min(from.y, to.y) - MARGIN
  local gx1, gy1 = math.max(from.x, to.x) + MARGIN, math.max(from.y, to.y) + MARGIN
  local size = CELL
  if ((gx1 - gx0) / size) * ((gy1 - gy0) / size) > MAX_CELLS then
    size = math.ceil(math.sqrt((gx1 - gx0) * (gy1 - gy0) / MAX_CELLS))
  end
  prep.x0, prep.y0, prep.cell = gx0, gy0, size
  prep.w, prep.h = math.ceil((gx1 - gx0) / size), math.ceil((gy1 - gy0) / size)
  prep.live, prep.checked, prep.buckets, prep.danger, prep.done = {}, {}, {}, {}, 0
end
local function cell_of(prep, p)
  return math.max(0, math.min(prep.h - 1, math.floor((p.y - prep.y0) / prep.cell))) * prep.w
    + math.max(0, math.min(prep.w - 1, math.floor((p.x - prep.x0) / prep.cell)))
end
local function centre(prep, i)
  return {x = prep.x0 + (i % prep.w + 0.5) * prep.cell, y = prep.y0 + (math.floor(i / prep.w) + 0.5) * prep.cell}
end

-- Each danger once (`prep`: the same table for every pass of one route, the threats list only added to:
-- a pass after the first paints only the new ones; the range: painting thousands again for each of ten
-- passes was most of a plan's time): what it spares, the wider berth, the checks of what a walk
-- crosses, the cells it makes dear. Returns the work left of `budget`.
local function paint(job, budget)
  local prep, from, to, threats = job.prep, job.from, job.to, job.threats
  local spare, spare_nests, wide = job.spare, job.spare_nests, job.wide
  local x0, y0, cell, w, h = prep.x0, prep.y0, prep.cell, prep.w, prep.h
  local live, checked, buckets, danger = prep.live, prep.checked, prep.buckets, prep.danger
  local floor, sqrt, max, min = math.floor, math.sqrt, math.max, math.min
  while prep.done < #threats and budget > 0 do
    prep.done = prep.done + 1
    local t = threats[prep.done]
    -- spared: the defenses of the place it comes to fight (biter nests only when it comes for nests)
    local spared = spare and distance(t, to) <= spare and (spare_nests or not t.nest)
    if not spared then
      if wide and t.nest and not t.blocked then
        t = {x = t.x, y = t.y, r = t.r + wide, nest = true, wall = true, id = t.id}
      end
      live[#live + 1] = t
      -- what a walk crosses is counted against the dangers of the 64-tile squares it passes, not all
      -- of them (woods are no danger: a way through them is still clear)
      local room = t.r + BUFFER
      if not t.soft and distance(from, t) >= room and distance(to, t) >= room then
        checked[#checked + 1] = t
        local ci = #checked
        for bx = floor((t.x - room) / BUCKET), floor((t.x + room) / BUCKET) do
          for by = floor((t.y - room) / BUCKET), floor((t.y + room) / BUCKET) do
            local key = bx .. ":" .. by
            local b = buckets[key]
            if not b then b = {}; buckets[key] = b end
            b[#b + 1] = ci
          end
        end
      end
      -- the cells it makes dear, dearer toward the middle: a route that must cross (or starts inside)
      -- keeps as far off as it can (run 19 walked a leg ending 3.5 tiles from a nest it was already
      -- inside the reach of)
      local weight = t.soft and SOFT or (t.blocked or t.wall) and WALL or AVOID
      local reach = t.r + BUFFER + cell * 0.71
      local r2 = reach * reach
      local cx0, cx1 = max(0, floor((t.x - reach - x0) / cell)), min(w - 1, floor((t.x + reach - x0) / cell))
      local cy0, cy1 = max(0, floor((t.y - reach - y0) / cell)), min(h - 1, floor((t.y + reach - y0) / cell))
      budget = budget - (cx1 - cx0 + 1) * (cy1 - cy0 + 1) - 8
      for cy = cy0, cy1 do
        local py = y0 + (cy + 0.5) * cell - t.y
        local py2 = py * py
        if py2 <= r2 then
          local row = cy * w
          for cx = cx0, cx1 do
            local px = x0 + (cx + 0.5) * cell - t.x
            local d2 = px * px + py2
            if d2 <= r2 then
              local wt = weight * (1 + (reach - sqrt(d2)) / reach)
              local i = row + cx
              local had = danger[i]
              if not had or had < wt then danger[i] = wt end
            end
          end
        end
      end
    else
      budget = budget - 1
    end
  end
  return budget
end

-- How many dangers' reach the walk crosses (0: safe).
local function crossings(job, points)
  local prep = job.prep
  local buckets, checked = prep.buckets, prep.checked
  local floor = math.floor
  local n, a, crossed = 0, job.from, {}
  for _, p in ipairs(points) do
    local steps = math.max(1, math.ceil(distance(a, p) / (BUCKET / 4)))
    local looked = {}
    for s = 0, steps do
      local key = floor((a.x + (p.x - a.x) * s / steps) / BUCKET) .. ":" .. floor((a.y + (p.y - a.y) * s / steps) / BUCKET)
      if not looked[key] then
        looked[key] = true
        for _, i in ipairs(buckets[key] or {}) do
          if not crossed[i] and not clear(a, p, {checked[i]}) then crossed[i] = true; n = n + 1 end
        end
      end
    end
    a = p
  end
  return n
end

local function guess(job, i)
  local w = job.prep.w
  local dx, dy = math.abs(i % w - job.fx), math.abs(math.floor(i / w) - job.fy)
  return math.max(dx, dy) + 0.41 * math.min(dx, dy)
end
local function search_start(job)
  local prep = job.prep
  job.start, job.finish = cell_of(prep, job.from), cell_of(prep, job.to)
  job.fx, job.fy = job.finish % prep.w, math.floor(job.finish / prep.w)
  job.cost, job.came, job.heap, job.expansions = {[job.start] = 0}, {}, {}, 0
  push(job.heap, guess(job, job.start), job.start)
end
-- The search, a slice at a time: true when it reached the goal, false when there is no way, nil when
-- the budget ran out first.
local function search(job, budget)
  local prep = job.prep
  local w, h, danger = prep.w, prep.h, prep.danger
  local cost, came, heap, finish = job.cost, job.came, job.heap, job.finish
  while #heap > 0 do
    if budget <= 0 then return nil end
    local top = pop(heap)
    local i = top[2]
    if i == finish then return true end
    -- (only a cell's own expansion counts, not the stale entries a dearer way left in the heap:
    -- drive14 planned a long trip through woods and walls, ran out counting those, and was given a
    -- straight line through a worm's reach instead)
    if top[1] - guess(job, i) <= cost[i] + 1e-9 then
      job.expansions = job.expansions + 1
      budget = budget - 8
      if job.expansions > MAX_EXPANSIONS then return false end
      local cx, cy = i % w, math.floor(i / w)
      for dy = -1, 1 do
        for dx = -1, 1 do
          local nx, ny = cx + dx, cy + dy
          if (dx ~= 0 or dy ~= 0) and nx >= 0 and ny >= 0 and nx < w and ny < h then
            local n = ny * w + nx
            local step = (dx ~= 0 and dy ~= 0) and 1.41 or 1
            local total = cost[i] + step * ((n ~= finish and danger[n]) or 1)
            if not cost[n] or total < cost[n] - 1e-9 then
              cost[n], came[n] = total, i
              push(heap, total + guess(job, n), n)
            end
          end
        end
      end
    else
      budget = budget - 1
    end
  end
  return false
end
local function finish_plan(job, found)
  local prep, from, to = job.prep, job.from, job.to
  local goal = {x = to.x, y = to.y}
  job.cost, job.heap = nil, nil
  local cells = {}
  local i = found and job.finish or nil
  while i do
    table.insert(cells, 1, centre(prep, i))
    i = job.came[i]
  end
  job.came = nil
  if #cells < 2 then
    local n = crossings(job, {goal})
    job.points, job.safe, job.crossed = {goal}, n == 0, n
    return
  end
  cells[1] = {x = from.x, y = from.y}
  cells[#cells] = goal
  -- Straight on for as long as it stays clear: only the corners round dangers remain. (Clear by the cells
  -- it painted, not by every danger again for each try: that was most of a plan's time.)
  local danger, cell = prep.danger, prep.cell
  local function open_way(a, b)
    local steps = math.max(1, math.ceil(distance(a, b) / (cell / 2)))
    for s = 1, steps - 1 do
      if danger[cell_of(prep, {x = a.x + (b.x - a.x) * s / steps, y = a.y + (b.y - a.y) * s / steps})] then return false end
    end
    return true
  end
  local out, at = {}, 1
  while at < #cells do
    local reach = at + 1
    while reach < #cells and open_way(cells[at], cells[reach + 1]) do reach = reach + 1 end
    out[#out + 1] = cells[reach]
    at = reach
  end
  local n = crossings(job, out)
  job.points, job.safe, job.crossed = out, n == 0, n
end

local function plan_start(from, to, threats, spare, spare_nests, wide, prep)
  prep = prep or {}
  grid(prep, from, to)
  return {from = from, to = to, threats = threats or {}, spare = spare, spare_nests = spare_nests, wide = wide,
    prep = prep, stage = "paint"}
end
local function plan_step(job, budget)
  if job.stage == "paint" then
    budget = paint(job, budget)
    if job.prep.done < #job.threats then return false end
    if clear(job.from, job.to, job.prep.live) then
      job.points, job.safe, job.crossed = {{x = job.to.x, y = job.to.y}}, true, 0
      return true
    end
    search_start(job)
    job.stage = "search"
    if budget <= 0 then return false end
  end
  local found = search(job, budget)
  if found == nil then return false end
  finish_plan(job, found)
  return true
end

function Routes.plan(from, to, threats, spare, spare_nests, wide, prep)
  local job = plan_start(from, to, threats, spare, spare_nests, wide, prep)
  while not plan_step(job, math.huge) do end
  return job.points, job.safe, job.crossed
end

local function at_war(force, other)
  return other ~= force and other.name ~= "enemy" and other.name ~= "neutral"
    and not force.get_friend(other) and not other.get_friend(force)
    and not force.get_cease_fire(other) and not other.get_cease_fire(force)
end

-- What an engineer knows of the dangers out there is what it has seen itself: nests, worms and the
-- turrets of those it is at war with, within sight as it walks (a player's screen and a bit), kept
-- in its memory until it sees them gone. (Factorio keeps no map for a force without players.)
local MEMORY = 8000
local BLOCKED_REACH, BLOCKED_FOR = 32, 108000   -- a spot it could not reach: kept clear of for 30 minutes
function Routes.block(npc, p, tick, reach)
  local seen = npc.seen_dangers or {count = 0, list = {}}
  npc.seen_dangers = seen
  -- one block to a spot (the range: a lake crossing blocked every 8 tiles, again at each plan, until
  -- thousands of them made a plan take seconds)
  local now, r = tick or game.tick, reach or BLOCKED_REACH
  seen.blocked_at = seen.blocked_at or {}
  local key = math.floor(p.x / 12) .. "," .. math.floor(p.y / 12)
  local had = seen.blocked_at[key]
  if had and had.until_tick > now and had.r >= r then return end
  seen.blocked_at[key] = {until_tick = now + BLOCKED_FOR, r = r}
  seen.next_block = (seen.next_block or 0) - 1   -- its own keys, below any unit number
  seen.list[seen.next_block] = {x = p.x, y = p.y, r = r, nest = true, blocked = true, until_tick = now + BLOCKED_FOR}
  seen.count = seen.count + 1
end

-- Water a planned route would cross: points every WATER_STEP tiles along it that are in a lake.
-- (each spot its own circle now, so a lake takes a few more tries to get round)
local WATER_STEP, WATER_REACH, WATER_TRIES, WATER_APART = 8, 24, 4, 16
local function wet(surface, points, from)
  local out, a, last = {}, from, nil
  for _, b in ipairs(points) do
    local dx, dy = b.x - a.x, b.y - a.y
    local n = math.max(1, math.floor(math.sqrt(dx * dx + dy * dy) / WATER_STEP))
    for i = 1, n do
      local p = {x = a.x + dx * i / n, y = a.y + dy * i / n}
      if (not last or distance(p, last) > WATER_APART)
          and surface.count_tiles_filtered{position = p, radius = 2, collision_mask = "water_tile", limit = 1} > 0 then
        out[#out + 1] = p
        last = p
      end
    end
    a = b
  end
  return out
end

-- A nest, worm or turret it now knows of (seen by itself, or by its friends and radars: intel.lua).
-- True when it is new to it.
function Routes.note(npc, e)
  local seen = npc.seen_dangers or {count = 0, list = {}}
  npc.seen_dangers = seen
  local id = e.unit_number
  if not id or seen.list[id] or seen.count >= MEMORY then return false end
  seen.list[id] = {x = e.position.x, y = e.position.y, r = Routes.reach(e.type, e.name),
    nest = e.force.name == "enemy", entity = e, kind = e.type}
  seen.count = seen.count + 1
  return true
end

function Routes.look(npc)
  local c = npc.character
  if not (c and c.valid) then return false end
  local seen = npc.seen_dangers or {count = 0, list = {}}
  npc.seen_dangers = seen
  local new = false
  local function note(e)
    if Routes.note(npc, e) then new = true end
  end
  local surface, force = c.surface, npc.force
  for _, e in ipairs(surface.find_entities_filtered{force = "enemy", type = ENEMY_TYPES, position = c.position,
      radius = SIGHT}) do note(e) end
  -- Biters on the move (and those about their nests): where they are now, for a few seconds.
  local groups, now = {}, game.tick or 0
  for _, u in ipairs(surface.find_entities_filtered{force = "enemy", type = "unit", position = c.position,
      radius = SIGHT}) do
    local key = math.floor(u.position.x / GROUP_CELL) .. "," .. math.floor(u.position.y / GROUP_CELL)
    local g = groups[key]
    if not g then g = {x = 0, y = 0, n = 0}; groups[key] = g end
    g.x, g.y, g.n = g.x + u.position.x, g.y + u.position.y, g.n + 1
  end
  seen.moving = {}
  for key, g in pairs(groups) do
    seen.moving["m" .. key] = {x = g.x / g.n, y = g.y / g.n, r = GROUP_REACH, nest = true, moving = true,
      until_tick = now + GROUP_FOR}
  end
  for _, other in pairs(game.forces) do
    if at_war(force, other) then
      for _, e in ipairs(surface.find_entities_filtered{force = other, type = TURRET_TYPES,
          position = c.position, radius = SIGHT}) do note(e) end
    end
  end
  -- what it sees is gone (cleared nests, turrets taken down) is forgotten, and blocked spots in time
  local now = game.tick
  for id, d in pairs(seen.list) do
    if d.blocked then
      if now >= d.until_tick then seen.list[id] = nil; seen.count = seen.count - 1 end
    elseif not (d.entity and d.entity.valid) and (d.x - c.position.x) ^ 2 + (d.y - c.position.y) ^ 2 <= SIGHT * SIGHT then
      seen.list[id] = nil
      seen.count = seen.count - 1
    end
  end
  return new
end

-- The land along a trip, generated before setting off (a band 4 chunks either side of the straight
-- line): nests are only placed where the map exists. True once it is there, or after a while anyway.
local BAND, PREPARE_FOR = 4, 1800
local function band_points(from, to)
  local dx, dy = to.x - from.x, to.y - from.y
  local n = math.max(1, math.ceil(math.sqrt(dx * dx + dy * dy) / 64))
  local out = {}
  for i = 0, n do out[#out + 1] = {x = from.x + dx * i / n, y = from.y + dy * i / n} end
  return out
end
function Routes.prepare(npc, to, tick)
  local c = npc.character
  local surface = c.surface
  local prep = npc.route_prep
  if not prep or prep.to.x ~= to.x or prep.to.y ~= to.y then
    prep = {to = {x = to.x, y = to.y}, from = {x = c.position.x, y = c.position.y}, since = tick}
    npc.route_prep = prep
    if surface.request_to_generate_chunks then
      for _, p in ipairs(band_points(prep.from, prep.to)) do surface.request_to_generate_chunks(p, BAND) end
    end
  end
  if tick - prep.since >= PREPARE_FOR or not surface.is_chunk_generated then return true end
  for _, p in ipairs(band_points(prep.from, prep.to)) do
    if not surface.is_chunk_generated({x = math.floor(p.x / 32), y = math.floor(p.y / 32)}) then return false end
  end
  return true
end

-- The land along a planned route, generated: a detour may leave the band generated before setting off,
-- and nests are only placed where the map exists (run 19 met one that was not there when it planned).
-- Asks for what is missing; true once it is all there.
function Routes.route_ready(npc, from, points)
  local surface = npc.character.surface
  if not (surface.is_chunk_generated and surface.request_to_generate_chunks) then return true end
  local ready, a, asked = true, from, {}
  for _, b in ipairs(points) do
    for _, p in ipairs(band_points(a, b)) do
      local cx, cy = math.floor(p.x / 32), math.floor(p.y / 32)
      for dy = -BAND, BAND, BAND do
        for dx = -BAND, BAND, BAND do
          if not surface.is_chunk_generated({x = cx + dx, y = cy + dy}) then
            ready = false
            local key = cx .. "," .. cy
            if not asked[key] then asked[key] = true; surface.request_to_generate_chunks(p, BAND) end
          end
        end
      end
    end
    a = b
  end
  return ready
end

-- Water by chunk, the same for everyone and worked out once: a chunk mostly water is a wall.
-- Water is known by the quarter of a chunk (16x16 tiles), wet when 40% of it is (the range: a lake 20-40
-- tiles across lay a quarter wet in each chunk it ran through, none of them counted, and the car's route
-- went over it again and again): which quarters of a chunk are wet, as a list of their middles.
local WATER_CELL, WATER_SHARE = 16, 102   -- tiles of 256
local function water_chunk(surface, cx, cy)
  if type(storage) ~= "table" then return nil end
  storage.route_water_cells = storage.route_water_cells or {}
  local key = surface.index .. ":" .. cx .. "," .. cy
  local known = storage.route_water_cells[key]
  if known ~= nil then return known end
  if not (surface.is_chunk_generated and surface.is_chunk_generated({x = cx, y = cy})) then return nil end
  local wet = {}
  for qx = 0, 1 do
    for qy = 0, 1 do
      local x, y = cx * 32 + qx * WATER_CELL, cy * 32 + qy * WATER_CELL
      if surface.count_tiles_filtered{area = {{x, y}, {x + WATER_CELL, y + WATER_CELL}},
          collision_mask = "water_tile"} >= WATER_SHARE then
        wet[#wet + 1] = {x = x + WATER_CELL / 2, y = y + WATER_CELL / 2}
      end
    end
  end
  storage.route_water_cells[key] = wet
  return wet
end

-- What lies along a trip, as a game NPC's pathing knows it: the lay of the land for biters (every
-- nest and worm in the area, noted in its memory) and the water chunks of the band along the way. The
-- turrets of engineers and players it knows only by sight: its own, its friends' and its radars'.
function Routes.survey(npc, area, from, to)
  local c = npc.character
  local surface = c.surface
  for _, e in ipairs(surface.find_entities_filtered{force = "enemy", type = ENEMY_TYPES, area = area}) do
    Routes.note(npc, e)
  end
  local walls, done = {}, {}
  if surface.count_tiles_filtered then
    for _, p in ipairs(band_points(from, to)) do
      local pcx, pcy = math.floor(p.x / 32), math.floor(p.y / 32)
      for cy = pcy - BAND, pcy + BAND do
        for cx = pcx - BAND, pcx + BAND do
          local key = cx .. "," .. cy
          if not done[key] then
            done[key] = true
            for i, q in ipairs(water_chunk(surface, cx, cy) or {}) do
              walls[#walls + 1] = {x = q.x, y = q.y, r = 11, nest = true, blocked = true, id = "w" .. key .. ":" .. i}
            end
          end
        end
      end
    end
  end
  return walls
end

-- The dangers it knows of in an area, each its own circle (so the gaps between nests stay open, as a
-- player slips through them), and the groups of biters on the move it saw lately.
local function dangers(npc, area)
  local out, seen = {}, {}
  local function inside(d)
    return not area or (d.x >= area[1][1] and d.x <= area[2][1] and d.y >= area[1][2] and d.y <= area[2][2])
  end
  local memory = npc.seen_dangers and npc.seen_dangers.list or {}
  for id, d in pairs(memory) do
    if inside(d) then
      local t = {x = d.x, y = d.y, r = d.r, id = id, nest = d.nest, blocked = d.blocked, kind = d.kind}
      out[#out + 1] = t
      seen[id] = t
    end
  end
  local now = game and game.tick or 0
  for id, g in pairs(npc.seen_dangers and npc.seen_dangers.moving or {}) do
    if now < g.until_tick and inside(g) then
      local t = {x = g.x, y = g.y, r = g.r, id = id, nest = true, moving = true}
      out[#out + 1] = t
      seen[id] = t
    end
  end
  return out, seen
end
Routes.dangers = dangers

-- Whether a straight walk from a to b keeps out of the reach of every danger it knows of near it (a short
-- walk that does not is planned round them like a long one: navigation.lua).
function Routes.straight_clear(npc, a, b)
  local area = {{math.min(a.x, b.x) - 64, math.min(a.y, b.y) - 64}, {math.max(a.x, b.x) + 64, math.max(a.y, b.y) + 64}}
  local threats = dangers(npc, area)
  return clear(a, b, threats)
end

-- What a vehicle need not keep as far from (drive.lua): biters cannot keep up with a car or a tank, so a
-- nest is only something not to hit, and a group on the move nothing at all; a car keeps out of worms'
-- and turrets' reach, a tank goes nearer worms (its armor takes a few spits).
local VEHICLE_NEST = {car = 14, tank = 6}
local function for_vehicle(threats, vehicle)
  if not vehicle then return threats end
  local out = {}
  for _, t in ipairs(threats) do
    if t.moving then
      -- left behind
    elseif t.kind == "unit-spawner" then
      out[#out + 1] = {x = t.x, y = t.y, r = VEHICLE_NEST[vehicle] or 14, id = t.id, nest = true, kind = t.kind}
    elseif t.kind == "turret" and vehicle == "tank" then
      out[#out + 1] = {x = t.x, y = t.y, r = math.max(10, t.r * 0.6), id = t.id, nest = true, kind = t.kind}
    else
      out[#out + 1] = t
    end
  end
  return out
end
Routes.for_vehicle = for_vehicle

-- Thick woods along a route, for a car (drive2: wedged among eight trees, as a car is in any forest; a
-- player drives round them, a tank knocks them down, on foot they are no bother): points every
-- WOODS_STEP along it with WOODS_DENSE trees or more within WOODS_RADIUS.
local WOODS_STEP, WOODS_RADIUS, WOODS_DENSE, WOODS_REACH, WOODS_TRIES = 12, 8, 7, 24, 5
local function woods(surface, points, from)
  local out, a = {}, from
  for _, b in ipairs(points) do
    local dx, dy = b.x - a.x, b.y - a.y
    local n = math.max(1, math.floor(math.sqrt(dx * dx + dy * dy) / WOODS_STEP))
    for i = 1, n do
      local p = {x = a.x + dx * i / n, y = a.y + dy * i / n}
      if surface.count_entities_filtered{type = "tree", position = p, radius = WOODS_RADIUS,
          limit = WOODS_DENSE} >= WOODS_DENSE then
        out[#out + 1] = p
      end
    end
    a = b
  end
  return out
end

-- A route for an engineer from where it stands to `to`; `spare` and `spare_nests` as in plan; `vehicle`
-- ("car" or "tank") when it drives. Planned in steps (the survey; a pass of the plan; water or woods on it,
-- another pass with them), a slice of work at a time through Routes.request.
-- Work a tick, for all the engineers planning together (cells painted, eight for each cell the search
-- expands): six planning at once, a slice each, still held a tick for a tenth of a second.
local SLICE = 10000
local REQUEST_STALE = 600  -- a request not asked about this long is started afresh
local NEAR_TRIP = 300      -- a trip this short surveyed and planned at once
local function route_start(to, spare, spare_nests, wide, vehicle)
  return {to = {x = to.x, y = to.y}, spare = spare, spare_nests = spare_nests, wide = wide, vehicle = vehicle,
    stage = "survey"}
end
local function route_step(npc, job, budget)
  local surface = npc.character.surface
  if job.stage == "survey" then
    Routes.look(npc)
    local from = npc.character.position
    local to = job.to
    job.from = {x = from.x, y = from.y}
    job.area = {{math.min(from.x, to.x) - MARGIN, math.min(from.y, to.y) - MARGIN},
      {math.max(from.x, to.x) + MARGIN, math.max(from.y, to.y) + MARGIN}}
    local walls = Routes.survey(npc, job.area, job.from, to)
    local threats, seen = dangers(npc, job.area)
    threats = for_vehicle(threats, job.vehicle)
    for _, w in ipairs(walls) do threats[#threats + 1] = w end
    job.threats, job.seen, job.prep, job.walls = threats, seen, {}, #walls
    job.plan = plan_start(job.from, to, threats, job.spare, job.spare_nests, job.wide, job.prep)
    job.stage, job.tries, job.water_tries, job.woods_tries = "plan", 1, 0, 0
    -- (the survey of a far trip is this slice; a near one is planned on in the same)
    if budget ~= math.huge and distance(job.from, to) > NEAR_TRIP then return nil end
  end
  if not plan_step(job.plan, budget) then return nil end
  job.points, job.safe, job.crossed = job.plan.points, job.plan.safe, job.plan.crossed
  -- Nests are what it remembers; lakes it finds by looking along the route: water the route would
  -- cross is remembered as blocked (for a while) and the route planned again round it.
  if job.water_tries < WATER_TRIES and surface.count_tiles_filtered then
    local water = wet(surface, job.points, job.from)
    if #water > 0 then
      job.water_tries = job.water_tries + 1
      for _, p in ipairs(water) do
        Routes.block(npc, p, nil, WATER_REACH)
        job.threats[#job.threats + 1] = {x = p.x, y = p.y, r = WATER_REACH, nest = true, blocked = true}
      end
      job.tries = job.tries + 1
      job.plan = plan_start(job.from, job.to, job.threats, job.spare, job.spare_nests, job.wide, job.prep)
      return nil
    end
    job.water_tries = WATER_TRIES
  end
  -- A car goes round thick woods too, found the same way (they are for this route only: on foot they are
  -- no bother).
  if job.vehicle == "car" and job.woods_tries < WOODS_TRIES and surface.count_entities_filtered then
    local dense = woods(surface, job.points, job.from)
    if #dense > 0 then
      job.woods_tries = job.woods_tries + 1
      for _, p in ipairs(dense) do
        job.threats[#job.threats + 1] = {x = p.x, y = p.y, r = WOODS_REACH, soft = true}
      end
      job.tries = job.tries + 1
      job.plan = plan_start(job.from, job.to, job.threats, job.spare, job.spare_nests, job.wide, job.prep)
      return nil
    end
    job.woods_tries = WOODS_TRIES
  end
  if job.tries > 1 then
    local _
    _, job.seen = dangers(npc, job.area)   -- (the spots it blocked on the way known to the route)
  end
  return {points = job.points, k = 1, known = job.seen, goal = {x = job.to.x, y = job.to.y}, spare = job.spare,
    spare_nests = job.spare_nests, safe = job.safe, wide = job.wide, crossings = job.crossed}
end

local function profiling() return type(storage) == "table" and storage.profile and helpers and helpers.create_profiler end
local function describe(npc, job)
  return {"", tostring(npc.name or npc.id), " ", math.floor(distance(job.from or job.to, job.to)), " tiles, ",
    #(job.threats or {}), " dangers, ", job.walls or 0, " water, ", job.tries or 0, " plans, ", job.vehicle or "on foot"}
end

function Routes.for_npc(npc, to, spare, spare_nests, wide, vehicle)
  local prof = profiling() and helpers.create_profiler()
  local job = route_start(to, spare, spare_nests, wide, vehicle)
  local route
  repeat route = route_step(npc, job, math.huge) until route
  if prof then prof.stop(); log({"", "[npc-factions] route plan, all at once: ", describe(npc, job), ": ", prof}) end
  return route
end

-- The same, a slice of work each time it is asked (the range: plans of up to eleven seconds through
-- nest country, the game held still for each): nil until it is planned; asked again (each tick, say),
-- it goes on where it was; a different request, or one let lie, starts afresh.
function Routes.request(npc, to, spare, spare_nests, wide, vehicle)
  local key = string.format("%.0f,%.0f|%s|%s|%s|%s", to.x, to.y, tostring(spare), tostring(spare_nests),
    tostring(wide), tostring(vehicle))
  local tick = game and game.tick
  local now = tick or 0
  local job = npc.route_job
  if not job or job.key ~= key or now - (job.asked or now) > REQUEST_STALE then
    job = route_start(to, spare, spare_nests, wide, vehicle)
    job.key, job.began = key, now
    npc.route_job = job
  end
  job.asked = now
  -- one engineer's slice a tick, the others' next tick (no clock, as in a test: a slice each time)
  local spent = tick and type(storage) == "table" and storage.route_slice
  if tick and type(storage) == "table" and (not spent or spent.tick ~= tick) then
    spent = {tick = tick, left = SLICE}
    storage.route_slice = spent
  end
  if spent and spent.left <= 0 then return nil end
  local prof = profiling() and helpers.create_profiler()
  local route = route_step(npc, job, SLICE)
  if spent then spent.left = 0 end
  if prof then
    prof.stop()
    log({"", "[npc-factions] route slice ", job.stage, " ", describe(npc, job), ": ", prof})
  end
  if route then
    npc.route_job = nil
    if prof then log({"", "[npc-factions] route plan, in slices: ", describe(npc, job), " in ", now - job.began, " ticks"}) end
  end
  return route
end

-- Whether it now sees a danger near it that its route was planned without (and not the place it
-- comes to fight).
function Routes.new_sighting(npc, route, vehicle)
  -- (compared with what it remembers, whoever looked last: its regular look round may have seen it first)
  Routes.look(npc)
  local c = npc.character.position
  local area = {{c.x - SIGHT, c.y - SIGHT}, {c.x + SIGHT, c.y + SIGHT}}
  local threats, ids = dangers(npc, area)
  if vehicle then
    -- A vehicle plans again only for what is in the way ahead, as it may pass it (drive15: every nest
    -- seen off to the side planned the trip again, and the way round flipped from one side to the
    -- other and back until the trip took longer than walking); biters on the move are left behind.
    local ahead = {}
    for _, t in ipairs(for_vehicle(threats, vehicle)) do
      local spared = route.spare and distance(t, route.goal) <= route.spare and (route.spare_nests or not t.nest)
      if t.id and not route.known[t.id] and not spared then ahead[#ahead + 1] = t end
    end
    if #ahead == 0 then return false end
    local a = c
    for i = route.k, math.min(#route.points, route.k + 3) do
      local b = route.points[i]
      if not clear(a, b, ahead) then return true end
      a = b
    end
    return false
  end
  for id, t in pairs(ids) do
    local spared = route.spare and distance(t, route.goal) <= route.spare and (route.spare_nests or not t.nest)
    if not t.moving and not route.known[id] and not spared then return true end
  end
  -- biters on the move: a new route only when they would notice it on the way ahead (the next legs)
  local moving = {}
  for _, t in ipairs(threats) do
    if t.moving and not (route.spare and distance(t, route.goal) <= route.spare) then moving[#moving + 1] = t end
  end
  if #moving > 0 then
    local a = c
    for i = route.k, math.min(#route.points, route.k + 3) do
      local b = route.points[i]
      if not clear(a, b, moving) then return true end
      a = b
    end
  end
  return false
end

return Routes
