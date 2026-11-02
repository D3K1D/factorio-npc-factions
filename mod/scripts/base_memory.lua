-- What an engineer knows about its own base, block by block: each plan it built (a smelting
-- column, a bus module, a power plant...) with where it stands, what it holds and how its
-- machines are doing right now. Planners and the LLMs read this so new work fits the base
-- (no clashes, upgrade before adding, see what is starved or backed up). Read-only.
local BaseMemory = {}
local has_logistics, Logistics = pcall(require, "scripts.logistics")   -- (tests load this file on its own)

local MACHINES = {["assembling-machine"]=true, furnace=true, ["mining-drill"]=true, lab=true, boiler=true,
  generator=true, ["rocket-silo"]=true}
-- Counted among a block's machines too, for the power it gives, but not sampled (a solar panel idles every night):
-- companion/power_outlook.py
local COUNTED = {["solar-panel"]=true, reactor=true}

local FLUID_SHORT = {}
if defines and defines.entity_status then
  for _, k in ipairs({"fluid_ingredient_shortage", "no_input_fluid"}) do
    if defines.entity_status[k] then FLUID_SHORT[defines.entity_status[k]] = true end
  end
end

local function status_names()
  local S, out = defines.entity_status, {}
  local function put(name, as) if S[name] then out[S[name]] = as end end
  put("working", "working"); put("normal", "working")
  put("item_ingredient_shortage", "starved"); put("fluid_ingredient_shortage", "starved")
  put("no_input_fluid", "starved"); put("missing_science_packs", "starved"); put("no_ingredients", "starved")
  put("full_output", "backed_up"); put("waiting_for_space_in_destination", "backed_up")
  put("no_power", "no_power"); put("low_power", "low_power"); put("no_fuel", "no_fuel")
  put("no_minable_resources", "depleted"); put("no_recipe", "idle")
  return out
end

-- The ore still under a block's drills (each tile counted once), what it is, and how long the drills
-- take to mine it out at their speed: the engineer keeps track of what is left at its patches.
local function ore_under(drills)
  local seen, left, rate, kinds = {}, 0, 0, {}
  for _, d in ipairs(drills) do
    local r = d.prototype.mining_drill_radius or 1
    local p = d.position
    for _, res in ipairs(d.surface.find_entities_filtered{type="resource",
        area={{p.x - r, p.y - r}, {p.x + r, p.y + r}}}) do
      local key = res.position.x .. ":" .. res.position.y
      if not seen[key] and res.prototype.resource_category == "basic-solid" then
        seen[key] = true
        left = left + res.amount
        kinds[res.name] = (kinds[res.name] or 0) + res.amount
      end
    end
    rate = rate + (d.prototype.mining_speed or 0.5)
  end
  local name, most = nil, 0
  for k, n in pairs(kinds) do if n > most or (n == most and name and k < name) then name, most = k, n end end
  if not name then return nil end
  return {name=name, left=math.floor(left), rate=rate, minutes=rate > 0 and math.floor(left / rate / 60) or nil}
end

-- The factory ledger: what each block could make at full speed (its capacity, from the machines as
-- they stand: their recipe, crafting speed with modules and beacons, productivity) and what it really
-- makes (sampled about once a minute: crafts finished since the last sample, and how its machines
-- spent their time). The companion weighs one against the other (companion/ledger.py).
local CRAFTERS = {["assembling-machine"]=true, furnace=true, ["rocket-silo"]=true}
local SAMPLE_GAP = 3600           -- a block is sampled at most once a minute
local MARK_GAP, MARKS = 5 * 3600, 7   -- a checkpoint every 5 minutes, the last 7 kept (30 minutes apart)
local TOP = 8                     -- items kept for a block's rates (the biggest)

local function try(f) local ok, v = pcall(f); if ok then return v end end

-- A machine's recipe: the one set, or the one a furnace last smelted (a furnace has none while idle).
local function recipe_of(e)
  local r = try(function() return e.get_recipe() end)
  if r then return r end
  if e.type ~= "furnace" then return nil end
  local prev = try(function() return e.previous_recipe end)
  local name = prev and prev.name
  if type(name) == "table" or type(name) == "userdata" then name = try(function() return name.name end) end
  return type(name) == "string" and prototypes and prototypes.recipe[name] or nil
end

local function amount(p)
  local n = p.amount or (((p.amount_min or 0) + (p.amount_max or 0)) / 2)
  return n * (p.probability or p.independent_probability or 1)
end

local function add(t, k, v) if k then t[k] = (t[k] or 0) + v end end

-- What a drill mines and how fast at full speed (items/s), from the resource it works on.
local function drill_rate(e)
  local target = try(function() return e.mining_target end)
  if not (target and target.valid ~= false) then return nil end
  local mine = try(function() return target.prototype.mineable_properties end)
  local product = mine and mine.products and mine.products[1]
  if not product or product.type == "fluid" then return nil end
  local speed = (try(function() return e.prototype.mining_speed end) or 0.5)
    * (1 + (try(function() return e.speed_bonus end) or 0))
  local prod = 1 + (try(function() return e.productivity_bonus end) or 0)
  local time = (mine.mining_time or 1) > 0 and mine.mining_time or 1
  return product.name, speed * prod / time
end

-- A crafting machine's full-speed flow: items/s out and in.
local function crafter_flow(e, makes, takes)
  local r = recipe_of(e)
  if not (r and (r.energy or 0) > 0) then return end
  local speed = try(function() return e.crafting_speed end)
    or try(function() return e.prototype.get_crafting_speed() end) or 1
  local prod = 1 + (try(function() return e.productivity_bonus end) or 0)
  local crafts = speed / r.energy
  for _, p in ipairs(r.products or {}) do add(makes, p.name, crafts * prod * amount(p)) end
  for _, i in ipairs(r.ingredients or {}) do add(takes, i.name, crafts * i.amount) end
end

-- The biggest few rates, rounded (an export a planner reads, kept small).
local function top(rates)
  local list = {}
  for k, v in pairs(rates or {}) do if v >= 0.0005 then list[#list + 1] = {k, v} end end
  table.sort(list, function(a, b) return a[2] > b[2] or (a[2] == b[2] and a[1] < b[1]) end)
  local out = {}
  for i = 1, math.min(#list, TOP) do out[list[i][1]] = math.floor(list[i][2] * 1000 + 0.5) / 1000 end
  return next(out) and out or nil
end

local function machines_of(plan)
  local out = {}
  for _, id in ipairs(plan.order or {}) do
    local entity = plan.entities[id]
    local e = entity and entity.entity
    if e and e.valid and MACHINES[e.type] then out[#out + 1] = e end
  end
  return out
end

local function capacity(machines)
  local makes, takes = {}, {}
  for _, e in ipairs(machines) do
    if CRAFTERS[e.type] then crafter_flow(e, makes, takes)
    elseif e.type == "mining-drill" then add(makes, drill_rate(e)) end
  end
  return makes, takes
end

local function copy(t) local out = {} for k, v in pairs(t or {}) do out[k] = v end return out end

-- Where the counters stood: kept every MARK_GAP, the last MARKS of them (about the last 30 to 35 minutes).
local function mark_of(s)
  return {tick=s.tick, out=copy(s.out), used=copy(s.used), states=copy(s.states), samples=s.samples, watts=s.watts,
    arms=copy(s.arms)}
end

-- How its inserters spent their time (companion/ledger.py: a machine short of inputs while its inserters never wait
-- for any is held up by its inserters, not its supply; the user, 2026-10-29: "inserters matter").
local function arm_names()   -- (built from the game's constants each time: the same on every machine)
  local S, out = defines.entity_status, {}
  local function put(name, as) if S[name] then out[S[name]] = as end end
  put("working", "working"); put("normal", "working")
  put("waiting_for_source_items", "waiting_source"); put("waiting_for_space_in_destination", "waiting_target")
  put("no_power", "no_power"); put("low_power", "low_power"); put("no_fuel", "no_fuel")
  return out
end

local function arms_of(plan)
  local out = {}
  for _, id in ipairs(plan.order or {}) do
    local entity = plan.entities[id]
    local e = entity and entity.entity
    if e and e.valid and e.type == "inserter" then out[#out + 1] = e end
  end
  return out
end

-- One sample of a finished plan's block. Counted since its first sample: items made and used (crafts
-- finished on each machine's counter, what working drills mine), how its machines spent their time (a
-- share of them in each state, summed over samples) and what its generators gave.
function BaseMemory.sample(plan, tick)
  local machines = machines_of(plan)
  if #machines == 0 then return end
  local names = status_names()
  local s = plan.ledger_samples
  if not s then
    s = {first=tick, out={}, used={}, states={}, samples=0, watts=0, marks={}}
    plan.ledger_samples = s
  end
  local secs = s.tick and (tick - s.tick) / 60 or 0
  local finished, watts = {}, 0
  for _, e in ipairs(machines) do
    local state = names[e.status] or "other"
    if secs > 0 then add(s.states, state, 1 / #machines) end
    -- (short of a fluid, not of what its inserters bring: kept apart too, for the inserter-bound judgment)
    if secs > 0 and FLUID_SHORT[e.status] then add(s.states, "starved_fluid", 1 / #machines) end
    if CRAFTERS[e.type] then
      local r = recipe_of(e)
      local n = try(function() return e.products_finished end)   -- crafts, not items (checked on 2.1)
      if r and n and e.unit_number then
        finished[e.unit_number] = {n, r.name}
        local before = s.finished and s.finished[e.unit_number]
        if before and before[2] == r.name and n >= before[1] then
          local crafts = n - before[1]
          local prod = 1 + (try(function() return e.productivity_bonus end) or 0)
          for _, p in ipairs(r.products or {}) do add(s.out, p.name, crafts * amount(p)) end
          for _, i in ipairs(r.ingredients or {}) do add(s.used, i.name, crafts / prod * i.amount) end
        end
      end
    elseif e.type == "mining-drill" and state == "working" and secs > 0 then
      local item, rate = drill_rate(e)
      add(s.out, item, (rate or 0) * secs)
    elseif e.type == "generator" then
      watts = watts + (try(function() return e.energy_generated_last_tick end) or 0) * 60
    end
  end
  if secs > 0 then
    s.arms = s.arms or {}
    local arms, names_ = arms_of(plan), arm_names()
    -- (by side: "in" feeding a machine, "out" taking from one; each side's time apart, so busy feeders are not hidden
    -- by idle takers)
    -- (each end against this block's own machines: an arm taking from one and feeding another counts for both)
    local own = {}
    for _, m in ipairs(machines) do if m.unit_number then own[m.unit_number] = true end end
    local sides, count = {}, {}
    for _, e in ipairs(arms) do
      local drop, pick = try(function() return e.drop_target end), try(function() return e.pickup_target end)
      for side, t in pairs({["in"]=drop, out=pick}) do
        if t and t.valid and t.unit_number and own[t.unit_number] then
          sides[#sides + 1] = {e, side}
          count[side] = (count[side] or 0) + 1
        end
      end
    end
    for _, a in ipairs(sides) do
      add(s.arms, a[2] .. ":" .. (names_[a[1].status] or "other"), 1 / count[a[2]])
    end
    s.samples, s.watts = s.samples + 1, s.watts + watts
  end
  s.tick, s.finished = tick, finished
  local last = s.marks[#s.marks]
  if not last or tick - last.tick >= MARK_GAP then
    s.marks[#s.marks + 1] = mark_of(s)
    if #s.marks > MARKS then table.remove(s.marks, 1) end
  end
end

-- How full the base's belt lines run (companion/belt_lines.py): at each line's probe tiles, the items on the belt
-- over what it holds (both lanes, 4 a lane a tile), summed over samples with checkpoints like the blocks', so the
-- companion reads the last half hour and the last 5 minutes. A moving belt this full carries that share of what it
-- can; a backed-up one is full. One line per engineer a second, each at most every LINE_GAP. A probe given its belt's
-- way is read over WINDOW tiles behind it: at one instant those hold what passed the probe over WINDOW tiles' travel
-- (a yellow belt: about two seconds), so a sampling rhythm that keeps step with a steady trickle cannot read it empty
-- (one sample a tile, every 600 ticks, could miss an item every 120 ticks throughout: Codex, L-1).
local LINE_GAP = 600
-- The gap between a line's samples varies by whole seconds (0 to 6 more), so a slow, steady trickle is not met at the
-- same point of its rhythm every time: with the window behind each probe, every phase of it is read in turn
-- (an item every 600 ticks read every 600 ticks could be missed throughout: Codex, L-2).
local LINE_JITTER = 7
local ABSENT_RETRY = 3600   -- a tile behind a probe with no belt on it is looked at again after this long
local LINE_PROBES = 6     -- a line recorded without probes (before they were kept) is sampled this evenly along it
local WINDOW = 4
local BACK = {[0]={0, 1}, [4]={-1, 0}, [8]={0, -1}, [12]={1, 0}}   -- a step back against the belt's way

local function default_probes(tiles)
  local n, out, seen = #(tiles or {}), {}, {}
  if n == 0 then return out end
  for k = 0, LINE_PROBES - 1 do
    local i = math.floor(1 + (n - 1) * k / (LINE_PROBES - 1) + 0.5)
    if not seen[i] then seen[i] = true; out[#out + 1] = {tiles[i][1], tiles[i][2], tiles[i][3]} end
  end
  return out
end

-- The belts a probe reads: its tile and, given the belt's way, the tiles behind it (each found once, then kept).
local function belts_at(line, key, p, surface, force)
  line.probe_at = line.probe_at or {}
  local kept = line.probe_at[key]
  if type(kept) ~= "table" or kept.valid ~= nil then kept = {}; line.probe_at[key] = kept end
  kept.retry = kept.retry or {}
  if not kept.at then
    -- The tiles read: on a line laid tile by tile, the probe and those before it along its path (a trunk turns: a
    -- straight step back from a bend reads another belt or none, Codex, L-2); a bus lane runs straight, its two ends
    -- all that is kept of it, so straight back against the probe's way.
    kept.at = {}
    local tiles = line.tiles or {}
    local found = nil
    if line.kind ~= "bus" and #tiles > 2 then
      for i, t in ipairs(tiles) do if t[1] == p[1] and t[2] == p[2] then found = i break end end
    end
    if found then
      for k = 0, WINDOW - 1 do
        local t = tiles[found - k]
        if t then kept.at[#kept.at + 1] = {t[1], t[2]} end
      end
    else
      local back = p[3] and BACK[p[3]]
      for k = 0, back and WINDOW - 1 or 0 do
        kept.at[#kept.at + 1] = {p[1] + (back and back[1] * k or 0), p[2] + (back and back[2] * k or 0)}
      end
    end
  end
  local out = {}
  for k, at in ipairs(kept.at) do
    k = k - 1
    local e = kept[k + 1]
    if not (e and e.valid) and (kept.retry[k + 1] or 0) <= (game and game.tick or 0) then
      local x, y = at[1], at[2]
      e = try(function()
        return surface.find_entities_filtered{area={{x + 0.25, y + 0.25}, {x + 0.75, y + 0.75}},
          type="transport-belt", force=force, limit=1}[1]
      end)
      kept[k + 1] = e or false
      kept.retry[k + 1] = not e and (game and game.tick or 0) + ABSENT_RETRY or nil
    end
    if e and e.valid then out[#out + 1] = e end
  end
  return out
end

-- Fill now over some belt tiles: (items on them over what they hold, the fuller lane's share alike, items a second
-- the first carries full and moving). A belt fed from one side carries on one lane only: backed up, it is half full,
-- its fuller lane full (two coal trunks read 0.5 throughout on the bus checkpoint, 2026-10-26).
local function fill_of(belts, height)
  height = height or 1   -- (items a stack on a belt may hold: a lane tile holds 4 stacks of it)
  local items, room, lane = 0, 0, {0, 0}
  local lane_room = {0, 0}
  for _, e in ipairs(belts) do
    for i = 1, 2 do
      local tl = try(function() return e.get_transport_line(i) end)
      if tl then
        local n = try(function() return tl.get_item_count() end) or 0
        local holds = math.max(1, math.floor((try(function() return tl.line_length end) or 1) * 4 * height + 0.5))
        items, room = items + n, room + holds
        lane[i], lane_room[i] = lane[i] + n, lane_room[i] + holds
      end
    end
  end
  if room == 0 then return nil end
  local fuller = 0
  for i = 1, 2 do
    if lane_room[i] > 0 then fuller = math.max(fuller, math.min(1, lane[i] / lane_room[i])) end
  end
  local left = lane_room[1] > 0 and math.min(1, lane[1] / lane_room[1]) or 0
  local right = lane_room[2] > 0 and math.min(1, lane[2] / lane_room[2]) or 0
  return math.min(1, items / room), fuller,
    (try(function() return belts[1].prototype.belt_speed end) or 0) * 480 * height, left, right
end

local function line_measured(line, probes)
  local f = line.fill
  local base = f.marks[1]
  local recent = base
  for i = #f.marks, 1, -1 do
    if f.tick - f.marks[i].tick >= MARK_GAP then recent = f.marks[i]; break end
  end
  local function avg(what, key, since)   -- (`what`: "sum" both lanes, "top" the fuller lane)
    local n = (f.n[key] or 0) - (since.n[key] or 0)
    if n <= 0 then return nil end
    return math.floor((((f[what] or {})[key] or 0) - ((since[what] or {})[key] or 0)) / n * 1000 + 0.5) / 1000
  end
  local out = {}
  for _, p in ipairs(probes) do
    local key = p[1] .. "," .. p[2]
    local f30 = avg("sum", key, base)
    if f30 then
      -- (`min`: how long this probe has been read; one added when a lane grows is young however old its line)
      out[#out + 1] = {x=p[1], y=p[2], f=f30, f5=avg("sum", key, recent), m=avg("top", key, base),
        m5=avg("top", key, recent), min=math.floor((f.tick - (f.since[key] or f.tick)) / 3600),
        l=avg("left", key, base), r=avg("right", key, base)}
    end
  end
  return {minutes=math.floor((f.tick - f.first) / 3600), rate=f.rate, probes=out}
end

function BaseMemory.sample_line(line, surface, force, tick)
  if not line.probes then line.probes = default_probes(line.tiles) end
  local f = line.fill
  if not f then
    f = {first=tick, sum={}, top={}, n={}, since={}, marks={}}
    line.fill = f
  end
  f.top, f.since, f.left, f.right = f.top or {}, f.since or {}, f.left or {}, f.right or {}
  local keep = {}
  for _, p in ipairs(line.probes) do
    local key = p[1] .. "," .. p[2]
    keep[key] = true
    local belts = belts_at(line, key, p, surface, force)
    local now, fuller, rate, left, right = nil, nil, nil, nil, nil
    if #belts > 0 then
      local height = 1 + (try(function() return force.belt_stack_size_bonus end) or 0)
      now, fuller, rate, left, right = fill_of(belts, height)
    end
    if now then
      f.sum[key], f.top[key], f.n[key] = (f.sum[key] or 0) + now, (f.top[key] or 0) + fuller, (f.n[key] or 0) + 1
      -- (each lane alone too: a belt with an item a side is two lines, Ledger's L-4)
      f.left[key], f.right[key] = (f.left[key] or 0) + left, (f.right[key] or 0) + right
      f.since[key] = f.since[key] or tick
      if rate and rate > 0 then f.rate = rate end
    end
  end
  -- (probes the line no longer has: a bus lane reported again further down)
  local stale = {f.sum, f.top, f.n, f.since, f.left, f.right, line.probe_at or {}}
  for _, m in ipairs(f.marks) do
    stale[#stale + 1] = m.sum; stale[#stale + 1] = m.top or {}; stale[#stale + 1] = m.n
    stale[#stale + 1] = m.left or {}; stale[#stale + 1] = m.right or {}
  end
  for _, t in ipairs(stale) do
    for key in pairs(t) do if not keep[key] then t[key] = nil end end
  end
  f.tick = tick
  f.count = (f.count or 0) + 1
  f.next = tick + LINE_GAP + 60 * ((f.count * 3) % LINE_JITTER)
  local last = f.marks[#f.marks]
  if not last or tick - last.tick >= MARK_GAP then
    f.marks[#f.marks + 1] = {tick=tick, sum=copy(f.sum), top=copy(f.top), n=copy(f.n), left=copy(f.left),
      right=copy(f.right)}
    if #f.marks > MARKS then table.remove(f.marks, 1) end
  end
  line.measured = line_measured(line, line.probes)
end

-- The engineer's lines for the site export: what each carries and claims and what the game measured (no tiles but
-- its two ends: the map export has them all). companion/lane_ledger.py reads them.
function BaseMemory.lines(npc)
  local out = {}
  local c = npc.character
  local surface = c and c.valid and c.surface
  local function stands(t)   -- (a belt still there: a line broken at either end is no line to count on)
    if not (t and surface) then return false end
    local n = try(function()
      return surface.count_entities_filtered{area={{t[1], t[2]}, {t[1] + 1, t[2] + 1}}, force=npc.force,
        type={"transport-belt", "splitter", "underground-belt"}, limit=1}
    end)
    return (n or 0) > 0
  end
  for _, l in pairs(npc.belt_lines or {}) do
    local t = l.tiles or {}
    out[#out + 1] = {id=l.id, item=l.item, kind=l.kind, group=l.group, supply=l.supply, claims=l.claims,
      head=t[1], tail=t[#t], measured=l.measured, ok=stands(t[1]) and stands(t[#t]), unread=l.unread,
      sides=l.sides, side_supply=l.side_supply}
  end
  table.sort(out, function(a, b) return tostring(a.id) < tostring(b.id) end)
  return #out > 0 and out or nil
end

function BaseMemory.line_tick(npc, tick)
  local c = npc.character
  if not (npc.belt_lines and c and c.valid) then return end
  local ids = {}
  for id in pairs(npc.belt_lines) do ids[#ids + 1] = id end
  if #ids == 0 then return end
  table.sort(ids)
  local i = ((npc.line_cursor or 0) % #ids) + 1
  local line = npc.belt_lines[ids[i]]
  if line.fill and line.fill.tick and tick < (line.fill.next or line.fill.tick + LINE_GAP) then return end
  npc.line_cursor = i
  BaseMemory.sample_line(line, c.surface, npc.force, tick)
end

-- About once a second per engineer: the next of its finished blocks due a sample (each about once a
-- minute), and the next of its belt lines. A block still being built is not measured: its half-built
-- time would drag its numbers down.
function BaseMemory.tick(npc, tick)
  BaseMemory.line_tick(npc, tick)
  local plans = npc.design_history or {}
  if #plans == 0 then return end
  local i = ((npc.ledger_cursor or 0) % #plans) + 1
  local plan = plans[i]
  local s = plan.ledger_samples
  if s and s.tick and tick - s.tick < SAMPLE_GAP then return end
  npc.ledger_cursor = i
  BaseMemory.sample(plan, tick)
end

-- Items a second between two points of a block's counters.
local function rates(now, before, secs)
  local out = {}
  for k, v in pairs(now) do
    local d = v - (before[k] or 0)
    if d > 0 then out[k] = d / secs end
  end
  return out
end

local function measured(plan)
  local s = plan.ledger_samples
  if not (s and s.tick and s.marks and s.marks[1]) then return nil end
  -- the last 30 minutes or so: since the oldest checkpoint kept (since the first sample, while younger)
  local base = s.marks[1]
  local secs = (s.tick - base.tick) / 60
  local samples = s.samples - (base.samples or 0)
  if secs <= 0 or samples <= 0 then return nil end
  -- lately: since the newest checkpoint at least 5 minutes old
  local recent = base
  for i = #s.marks, 1, -1 do
    if s.tick - s.marks[i].tick >= MARK_GAP then recent = s.marks[i]; break end
  end
  -- the busiest 5 minutes: the highest rate between checkpoints (and since the last, once that long)
  local peak = {}
  local points = copy(s.marks)
  if s.tick - s.marks[#s.marks].tick >= MARK_GAP then points[#points + 1] = s end
  for i = 2, #points do
    for k, v in pairs(rates(points[i].out, points[i - 1].out, (points[i].tick - points[i - 1].tick) / 60)) do
      if v > (peak[k] or 0) then peak[k] = v end
    end
  end
  -- every state the machines spent time in (a state the companion has no word for still counts)
  local shares = {}
  for k, n in pairs(s.states) do
    local v = (n - (base.states[k] or 0)) / samples
    if v >= 0.001 then shares[k] = math.floor(v * 1000 + 0.5) / 1000 end
  end
  local arms = {}   -- (its inserters' time, as the machines': companion/ledger.py's inserter-bound signal)
  for k, n in pairs(s.arms or {}) do
    local v = (n - ((base.arms or {})[k] or 0)) / samples
    if v >= 0.001 then arms[k] = math.floor(v * 1000 + 0.5) / 1000 end
  end
  local watts = (s.watts - (base.watts or 0)) / samples
  local out = rates(s.out, base.out, secs)
  return {minutes=math.floor((s.tick - s.first) / 3600), out=top(out), arms=next(arms) and arms or nil,
    out5=top(rates(s.out, recent.out, math.max(1, (s.tick - recent.tick) / 60))),
    peak=top(next(peak) and peak or out), takes=top(rates(s.used, base.used, secs)),
    shares=next(shares) and shares or nil, power_mw=watts > 0 and math.floor(watts / 1000 + 0.5) / 1000 or nil}
end

-- The plan's ledger block (kind, expected load, ports, the library build it came from...), positions back
-- in export-local coordinates.
local function intent(plan, ox, oy)
  local l = plan.ledger
  if not l then return nil end
  local out = {kind=l.kind, load=l.load, makes=l.makes, takes=l.takes, build=l.build}
  if l.ports then
    out.ports = {}
    for i, p in ipairs(l.ports) do
      out.ports[i] = {item=p.item, dir=p.dir, rate=p.rate, x=p.x and p.x - ox or nil, y=p.y and p.y - oy or nil}
    end
  end
  if l.grow then out.grow = {l.grow[1] - ox, l.grow[2] - oy, l.grow[3] - ox, l.grow[4] - oy} end
  return out
end

local PART_GAP = 16   -- a plan's machines this near each other (between their boxes) are one part of it

-- A plan's machines grouped by nearness, in export-local coordinates: where its parts stand, each with its own
-- machines. A block with furnaces by the factory and drills at a far patch is two places, not one box spanning the
-- ground between (run 55's wall ring took in its far fields so). `boxes` are {x0, y0, x1, y1, name}.
local function parts(boxes, ox, oy)
  local cell, parent, cells = PART_GAP * 2, {}, {}   -- (cells this big: machines in reach are in neighbouring ones)
  local function root(i)
    while parent[i] ~= i do
      parent[i] = parent[parent[i]]
      i = parent[i]
    end
    return i
  end
  for i, b in ipairs(boxes) do
    parent[i] = i
    local cx, cy = math.floor((b[1] + b[3]) / 2 / cell), math.floor((b[2] + b[4]) / 2 / cell)
    for dx = -1, 1 do
      for dy = -1, 1 do
        for _, j in ipairs(cells[(cx + dx) .. ":" .. (cy + dy)] or {}) do
          local o = boxes[j]
          if math.max(b[1] - o[3], o[1] - b[3], b[2] - o[4], o[2] - b[4]) <= PART_GAP then
            parent[root(i)] = root(j)
          end
        end
      end
    end
    local key = cx .. ":" .. cy
    cells[key] = cells[key] or {}
    cells[key][#cells[key] + 1] = i
  end
  local out, at = {}, {}
  for i, b in ipairs(boxes) do   -- (in the plan's order: the part of its first machine first)
    local r = root(i)
    local p = at[r]
    if not p then
      p = {b[1], b[2], b[3], b[4], machines={}}
      at[r] = p
      out[#out + 1] = p
    end
    p[1], p[2] = math.min(p[1], b[1]), math.min(p[2], b[2])
    p[3], p[4] = math.max(p[3], b[3]), math.max(p[4], b[4])
    p.machines[b[5]] = (p.machines[b[5]] or 0) + 1
  end
  for i, p in ipairs(out) do
    out[i] = {box={math.floor(p[1] - ox), math.floor(p[2] - oy), math.ceil(p[3] - ox), math.ceil(p[4] - oy)},
      machines=p.machines}
  end
  return out
end

-- What a block's crafting machines take by hand, recipe by recipe (the user, 2026-10-29: "chests with items mean it
-- won't be automated"): an input that comes from a plain chest nothing fills from a belt, a machine or the bus (an
-- inserter, drill or loader filling it from a chest the engineer fills counts as by hand too), straight from the
-- chest or along a belt that nothing else puts items on; or the ingredients of a machine nothing feeds. A recipe
-- counts only when every one of its machines in the block takes something by hand (one fed by belts makes it without
-- the engineer). {recipe = {items, sorted}}; and the hand chests themselves, {x, y, items} in export-local tiles,
-- for belts to be brought to them (NPC Factory's retrofit). Each nil when none.
local CRAFTERS = {["assembling-machine"]=true, furnace=true, ["rocket-silo"]=true}
local BELTS = {["transport-belt"]=true, ["underground-belt"]=true, splitter=true, loader=true, ["loader-1x1"]=true,
  ["linked-belt"]=true, ["lane-splitter"]=true}
local REACH = 3      -- an inserter's reach round a building (a long-handed one's, rounded up)
local LINKS = 3      -- chests passed along from chest to chest that are followed back to their source
local LINE = 400     -- belts read back along a line; one longer is taken as fed (a bus lane, say)

local function near(e, kind, pad)
  local b = e.bounding_box
  return e.surface.find_entities_filtered{type=kind, force=e.force,
    area={{b.left_top.x - pad, b.left_top.y - pad}, {b.right_bottom.x + pad, b.right_bottom.y + pad}}}
end

local function key(e) return e.position.x .. "," .. e.position.y end

-- Whether a plain chest is filled from a belt, a machine or the bus: an inserter or drill dropping into it (an
-- inserter's own source followed back while it is another chest), or a loader loading into it.
local function sourced(chest, links)
  if links <= 0 then return false end
  for _, d in ipairs(near(chest, "mining-drill", REACH)) do
    if d.drop_target == chest then return true end
  end
  for _, ins in ipairs(near(chest, "inserter", REACH)) do
    local src = ins.drop_target == chest and ins.pickup_target   -- (nil: nothing to take from, no feed)
    if src and (src.type ~= "container" or sourced(src, links - 1)) then return true end
  end
  for _, kind in ipairs({"loader", "loader-1x1"}) do
    for _, l in ipairs(near(chest, kind, 1)) do
      if l.loader_type == "input" and l.loader_container == chest then return true end   -- (belt into the chest)
    end
  end
  return false
end

local function fed_any(e)   -- a drill or a loader putting items straight into the machine
  for _, d in ipairs(near(e, "mining-drill", REACH)) do
    if d.drop_target == e then return true end
  end
  for _, kind in ipairs({"loader", "loader-1x1"}) do
    for _, l in ipairs(near(e, kind, 1)) do
      if l.loader_type == "input" and l.loader_container == e then return true end
    end
  end
  return false
end

-- The belts running into `belt`, back along its line (undergrounds' pairs too): {belts}, {key = true}.
local function upstream(belt)
  local on, list, i = {[key(belt)]=true}, {belt}, 1
  while i <= #list and #list < LINE do
    local b = list[i]
    i = i + 1
    local from = {}
    local ok, n = pcall(function() return b.belt_neighbours end)
    for _, x in ipairs(ok and n and n.inputs or {}) do from[#from + 1] = x end
    if b.type == "underground-belt" and b.belt_to_ground_type == "output" then
      local paired, pair = pcall(function() return b.underground_belt_neighbour end)   -- (its entrance)
      if paired and pair then from[#from + 1] = pair end
    end
    for _, x in ipairs(from) do
      if not on[key(x)] then on[key(x)], list[#list + 1] = true, x end
    end
  end
  return list, on
end

-- What a source puts on a belt: a machine's products, a drill's ore, a chest's contents ({name = true}); nil when it
-- cannot be told (another belt, an empty chest): anything.
local function gives(src)
  local out = {}
  if CRAFTERS[src.type] then
    local r = recipe_of(src)
    for _, p in ipairs(r and r.products or {}) do out[p.name] = true end
  elseif src.type == "mining-drill" then
    local ok, products = pcall(function() return src.mining_target.prototype.mineable_properties.products end)
    for _, p in ipairs(ok and products or {}) do out[p.name] = true end
  elseif src.type == "container" or src.type == "logistic-container" then
    local inv = src.get_inventory(defines.inventory.chest)
    for _, it in ipairs(inv and inv.get_contents() or {}) do out[it.name] = true end
  end
  return next(out) and out or nil
end

-- What a belt carries from hand chests: those nothing fills that an inserter or loader empties onto it or onto the
-- belts running into it, and what else puts items on the line, item by item ({chests}, {name = true} or true for
-- anything): a hand chest's item counts as by hand where nothing else brings that item (a maker on one lane does not
-- make the hand chest's item on the other one fed). No chests and no other source: nothing it sees, which adds nothing
-- to a belt passing items on to it.
local function belt_hand(belt, memo, links)
  links = links or LINKS
  local k = key(belt) .. "#" .. links   -- (read as deep as it may go: a shallower read is another)
  memo.active = memo.active or {}
  local result = memo[k]
  if result == nil then
    local me = key(belt)
    memo.active[me] = true   -- (being read: a loop back to it adds nothing)
    local cut = {}           -- (belts being read above it that a loop back skipped: its reading is partial till then)
    local line, on = upstream(belt)
    local x0, y0, x1, y1 = math.huge, math.huge, -math.huge, -math.huge
    for _, b in ipairs(line) do
      local bb = b.bounding_box
      x0, y0 = math.min(x0, bb.left_top.x), math.min(y0, bb.left_top.y)
      x1, y1 = math.max(x1, bb.right_bottom.x), math.max(y1, bb.right_bottom.y)
    end
    local area = {{x0 - REACH, y0 - REACH}, {x1 + REACH, y1 + REACH}}
    local chests, real = {}, #line >= LINE or {}   -- (a line longer than is read: anything, taken as fed)
    local function add(src)
      local g = src and gives(src)
      if real == true then return end
      if not g then real = true return end
      for name in pairs(g) do real[name] = true end
    end
    local function find(kind) return belt.surface.find_entities_filtered{type=kind, force=belt.force, area=area} end
    for _, ins in ipairs(find("inserter")) do
      local dt = ins.drop_target
      local src = dt and on[key(dt)] and ins.pickup_target
      if src and src.type == "container" and not sourced(src, LINKS) then chests[#chests + 1] = src
      elseif src and BELTS[src.type] and on[key(src)] then   -- (from its own line: nothing new)
      elseif src and BELTS[src.type] and memo.active[key(src)] then   -- (a loop back: nothing new here)
        cut[key(src)] = true
      elseif src and BELTS[src.type] and links > 1 then   -- (from another belt: what that one carries by hand too)
        local sub = belt_hand(src, memo, links - 1)
        for c in pairs(sub.cut or {}) do cut[c] = true end
        for _, c in ipairs(sub.chests) do chests[#chests + 1] = c end
        if sub.real == true then real = true
        elseif real ~= true then for name in pairs(sub.real) do real[name] = true end end
      elseif src then add(src) end
    end
    for _, kind in ipairs({"loader", "loader-1x1"}) do
      for _, l in ipairs(find(kind)) do
        local c = on[key(l)] and l.loader_type == "output" and l.loader_container   -- (unloading onto the line)
        if c and c.type == "container" and not sourced(c, LINKS) then chests[#chests + 1] = c
        elseif c then add(c) end
      end
    end
    for _, d in ipairs(find("mining-drill")) do
      if d.drop_target and on[key(d.drop_target)] then add(d) end
    end
    cut[me] = nil   -- (a loop back to itself: complete once it is read)
    memo.active[me] = nil
    result = {chests=chests, real=real, cut=next(cut) and cut or nil}
    if not result.cut then memo[k] = result end   -- (a partial reading is not kept for a later reader)
  end
  return result
end

local function hand_fed(machines, ox, oy)
  local per, count, chests, memo = {}, {}, {}, {}   -- (recipe -> its machines fed by hand and their items; recipe ->
  --                                                    its machines; hand chests by tile; belts read)
  -- (what a hand chest holds of `items`; all of them where it holds none and feeds the machine itself, the only
  -- source of what it takes there, or is empty. On a belt among other sources, only what it holds)
  local function by_chest(chest, items, into, direct)
    local inv = chest.get_inventory(defines.inventory.chest)
    local held, any = {}, false
    for _, it in ipairs(inv and inv.get_contents() or {}) do
      any = true
      if items[it.name] then held[it.name] = true end
    end
    if not next(held) and (direct or not any) then held = items end
    if not next(held) then return end
    local c = chests[key(chest)] or {x=math.floor(chest.position.x - ox), y=math.floor(chest.position.y - oy), items={}}
    for name in pairs(held) do into[name], c.items[name] = true, true end
    chests[key(chest)] = c
  end
  local function check(e)
    local recipe = recipe_of(e)
    if not recipe then return end
    count[recipe.name] = (count[recipe.name] or 0) + 1
    local items = {}
    for _, i in ipairs(recipe.ingredients or {}) do
      if i.type ~= "fluid" then items[i.name] = true end
    end
    if not next(items) then return end
    local by_hand, fed = {}, false
    for _, ins in ipairs(near(e, "inserter", REACH)) do
      local src = ins.drop_target == e and ins.pickup_target   -- (nil: nothing to take from, no feed)
      if src then
        fed = true
        if src.type == "container" and not sourced(src, LINKS) then
          by_chest(src, items, by_hand, true)
        elseif BELTS[src.type] then   -- (a belt carries from where it is loaded: a hand chest's is by hand)
          local line = belt_hand(src, memo)
          if #line.chests > 0 and line.real ~= true then
            local left = {}   -- (what this machine takes that nothing else on the line brings)
            for name in pairs(items) do if not line.real[name] then left[name] = true end end
            if next(left) then
              for _, chest in ipairs(line.chests) do by_chest(chest, left, by_hand) end
            end
          end
        end
      end
    end
    if not fed and not fed_any(e) then by_hand = items end
    if next(by_hand) then
      local r = per[recipe.name] or {machines=0, items={}}
      r.machines = r.machines + 1
      for name in pairs(by_hand) do r.items[name] = true end
      per[recipe.name] = r
    end
  end
  for _, e in ipairs(machines) do   -- (a reading only: a machine it cannot read is left out, never an error)
    if CRAFTERS[e.type] and e.surface then pcall(check, e) end
  end
  local out = {}
  for name, r in pairs(per) do
    if r.machines >= (count[name] or 0) then
      local list = {}
      for item in pairs(r.items) do list[#list + 1] = item end
      table.sort(list)
      out[name] = list
    end
  end
  local spots = {}
  for _, c in pairs(chests) do
    local list = {}
    for item in pairs(c.items) do list[#list + 1] = item end
    table.sort(list)
    spots[#spots + 1] = {x=c.x, y=c.y, items=list}
  end
  table.sort(spots, function(a, b) return a.y < b.y or (a.y == b.y and a.x < b.x) end)
  return next(out) and out or nil, #spots > 0 and spots or nil
end

-- Blocks in export-local coordinates (relative to `origin`), newest last.
function BaseMemory.blocks(npc, origin, limit)
  local names = status_names()
  local plans = {}
  for _, plan in ipairs(npc.design_history or {}) do plans[#plans + 1] = plan end
  local d = npc.design
  if d and d.plan and d.status == "building" then plans[#plans + 1] = d.plan end
  local out = {}
  for i = math.max(1, #plans - (limit or 64) + 1), #plans do
    local plan = plans[i]
    local x0, y0, x1, y1 = math.huge, math.huge, -math.huge, -math.huge
    local m0, n0, m1, n1 = math.huge, math.huge, -math.huge, -math.huge   -- (its machines alone: no belts out to a field)
    local machines, recipes, status, built, drills, stock, boxes = {}, {}, {}, 0, {}, {}, {}
    local pieces = {}   -- (every building by name: what robots would place of it again, companion/network_ledger.py)
    for _, id in ipairs(plan.order or {}) do
      local entity = plan.entities[id]
      local e = entity.entity
      if e and e.valid then
        built = built + 1
        pieces[e.name] = (pieces[e.name] or 0) + 1
        if e.type == "container" or e.type == "logistic-container" then   -- what its chests hold (walls stocked
          -- for the wall line, say; a mall's provider chests too)
          local inv = e.get_inventory(defines.inventory.chest)
          for _, it in ipairs(inv and inv.get_contents() or {}) do
            if (it.quality or "normal") == "normal" then stock[it.name] = (stock[it.name] or 0) + it.count end
          end
        end
        local box = e.bounding_box
        x0, y0 = math.min(x0, box.left_top.x), math.min(y0, box.left_top.y)
        x1, y1 = math.max(x1, box.right_bottom.x), math.max(y1, box.right_bottom.y)
        if MACHINES[e.type] then
          m0, n0 = math.min(m0, box.left_top.x), math.min(n0, box.left_top.y)
          m1, n1 = math.max(m1, box.right_bottom.x), math.max(n1, box.right_bottom.y)
          boxes[#boxes + 1] = {box.left_top.x, box.left_top.y, box.right_bottom.x, box.right_bottom.y, e.name}
          machines[e.name] = (machines[e.name] or 0) + 1
          local recipe = recipe_of(e)   -- (an idle furnace: what it last smelted, as its capacity counts it)
          if recipe then recipes[recipe.name] = true end
          local s = names[e.status] or "other"
          status[s] = (status[s] or 0) + 1
          if e.type == "mining-drill" and e.name ~= "pumpjack" then drills[#drills + 1] = e end
        elseif COUNTED[e.type] then   -- (its ground as a machine's: a solar block's footprint is its panels)
          m0, n0 = math.min(m0, box.left_top.x), math.min(n0, box.left_top.y)
          m1, n1 = math.max(m1, box.right_bottom.x), math.max(n1, box.right_bottom.y)
          boxes[#boxes + 1] = {box.left_top.x, box.left_top.y, box.right_bottom.x, box.right_bottom.y, e.name}
          machines[e.name] = (machines[e.name] or 0) + 1
        end
      end
    end
    if built > 0 then
      local list = {}
      for r in pairs(recipes) do list[#list + 1] = r end
      table.sort(list)
      local ox, oy = origin and origin.x or 0, origin and origin.y or 0
      local makes, takes = capacity(machines_of(plan))
      local fed, chests = hand_fed(machines_of(plan), ox, oy)
      out[#out + 1] = {id=plan.plan_id, purpose=plan.purpose, stage=plan.base_plan and plan.base_plan.stage or nil,
        accepted=plan.accepted_tick,
        box={math.floor(x0 - ox), math.floor(y0 - oy), math.ceil(x1 - ox), math.ceil(y1 - oy)},
        machines_box=m1 > m0 and {math.floor(m0 - ox), math.floor(n0 - oy), math.ceil(m1 - ox), math.ceil(n1 - oy)} or nil,
        parts=#boxes > 0 and parts(boxes, ox, oy) or nil,
        buildings=built, planned=#(plan.order or {}), machines=machines, recipes=list, status=status,
        building=(plan == (d and d.plan) and d.status == "building") or nil,
        ore=#drills > 0 and ore_under(drills) or nil,
        capacity=(next(makes) or next(takes)) and {makes=top(makes), takes=top(takes)} or nil,
        stock=next(stock) and stock or nil, measured=measured(plan), ledger=intent(plan, ox, oy), keep=plan.keep,
        hand_fed=fed, hand_chests=chests, pieces=next(pieces) and pieces or nil}
    end
  end
  return out
end

local RADAR_SEES = 224   -- a working radar's scans keep this far round it charted (7 chunks)
local MAX_RADARS = 6
local RAID_MEMORY = 36000  -- a raid on the base is defended against for 10 minutes

-- Enemy structures (biter nests, worms) the engineer's base could be reached from, nearest
-- first, in export-local coordinates: what outpost defenses are planned against. It knows what
-- lies round its home, and what its working radars see farther out.
function BaseMemory.threats(npc, origin, radius, limit)
  local c = npc.character
  if not (c and c.valid) then return {} end
  local home = npc.home or c.position
  local found = c.surface.find_entities_filtered{force="enemy", type={"unit-spawner", "turret"},
    position=home, radius=radius or 250}
  local seen = {}
  for _, e in pairs(found) do seen[e.unit_number or e] = true end
  local working = defines and defines.entity_status and defines.entity_status.working
  local radars = c.surface.find_entities_filtered{force=npc.force, type="radar", position=home,
    radius=(radius or 250) + RADAR_SEES, limit=MAX_RADARS}
  for _, radar in pairs(radars) do
    if working and radar.status == working then
      for _, e in pairs(c.surface.find_entities_filtered{force="enemy", type={"unit-spawner", "turret"},
          position=radar.position, radius=RADAR_SEES}) do
        if not seen[e.unit_number or e] then
          seen[e.unit_number or e] = true
          found[#found + 1] = e
        end
      end
    end
  end
  table.sort(found, function(a, b)
    return (a.position.x - home.x)^2 + (a.position.y - home.y)^2 < (b.position.x - home.x)^2 + (b.position.y - home.y)^2
  end)
  local out, ox, oy = {}, origin and origin.x or 0, origin and origin.y or 0
  for i = 1, math.min(#found, limit or 32) do
    local e = found[i]
    -- (the pollution where a nest stands: what it soaks up, it sends back as attacks)
    local ok, p = pcall(function() return e.type == "unit-spawner" and c.surface.get_pollution(e.position) or nil end)
    p = ok and type(p) == "number" and p or nil
    out[i] = {x=e.position.x - ox, y=e.position.y - oy, kind=e.type, name=e.name,
      pollution=p and p > 0 and math.floor(p * 10 + 0.5) / 10 or nil}
  end
  -- Where engineers or players hit its base lately (a raider may have no building standing there).
  local now = game and game.tick or 0
  for _, raid in ipairs(npc.raids or {}) do
    if now - (raid.tick or 0) <= RAID_MEMORY and #out < (limit or 32) + 8 then
      out[#out + 1] = {x=raid.x - ox, y=raid.y - oy, kind="raid", name=raid.by}
    end
  end
  -- Engineers at war with this one: their turrets, and their nearest buildings, are threats too.
  for _, other in pairs(storage.npcs or {}) do
    local force = other.force
    if other ~= npc and force and force.valid and not npc.force.get_cease_fire(force)
        and not npc.force.get_friend(force) and #out < (limit or 32) + 8 then
      local theirs = c.surface.find_entities_filtered{force=force, type={"ammo-turret", "electric-turret",
        "fluid-turret", "assembling-machine", "furnace"}, position=home, radius=radius or 250, limit=64}
      table.sort(theirs, function(a, b)
        return (a.position.x - home.x)^2 + (a.position.y - home.y)^2 < (b.position.x - home.x)^2 + (b.position.y - home.y)^2
      end)
      for i = 1, math.min(#theirs, 4) do
        local e = theirs[i]
        out[#out + 1] = {x=e.position.x - ox, y=e.position.y - oy, kind="hostile-engineer", name=e.name,
          force=force.name}
      end
    end
  end
  return out
end

-- The pollution round its home, chunk by chunk (export-local chunk centres; only where there is some):
-- what draws attacks to which part of its base. `radius` in chunks.
function BaseMemory.pollution(npc, origin, radius)
  local c = npc.character
  if not (c and c.valid) then return {} end
  local home, surface, r = npc.home or c.position, c.surface, radius or 8
  local hx, hy = math.floor(home.x / 32), math.floor(home.y / 32)
  local ox, oy = origin and origin.x or 0, origin and origin.y or 0
  local out = {}
  for cx = hx - r, hx + r do
    for cy = hy - r, hy + r do
      if surface.is_chunk_generated({cx, cy}) then
        local p = surface.get_pollution({cx * 32 + 16, cy * 32 + 16})
        if p and p >= 1 then
          out[#out + 1] = {x=cx * 32 + 16 - ox, y=cy * 32 + 16 - oy, p=math.floor(p + 0.5)}
        end
      end
    end
  end
  return out
end

-- Its own turrets round home (export-local): where its defense stands, and whether it can shoot (ammo held,
-- or power for an electric one).
function BaseMemory.guards(npc, origin, radius, limit)
  local c = npc.character
  if not (c and c.valid) then return {} end
  local home = npc.home or c.position
  local ox, oy = origin and origin.x or 0, origin and origin.y or 0
  local out = {}
  for _, t in ipairs(c.surface.find_entities_filtered{force=npc.force, type={"ammo-turret", "electric-turret",
      "fluid-turret"}, position=home, radius=radius or 400, limit=limit or 300}) do
    local ammo
    if t.type == "ammo-turret" then
      local inv = t.get_inventory(defines.inventory.turret_ammo)
      ammo = inv and inv.get_item_count() or 0
    elseif t.type == "fluid-turret" then
      local ok, n = pcall(function() return t.get_fluid_count() end)   -- (the fluid API moved in 2.1)
      ammo = ok and math.floor(n or 0) or nil
    end
    out[#out + 1] = {x=t.position.x - ox, y=t.position.y - oy, name=t.name, kind=t.type, ammo=ammo,
      health=t.max_health and t.max_health > 0 and math.floor(t.health / t.max_health * 100) / 100 or nil}
  end
  return out
end

-- The nests round it, grouped (spawners and worms within 40 tiles of each other), world tiles: how many
-- of each, the pollution they soak up, how far from home. What an attack on them would face.
local WORM_SIZE = {small=1, medium=2, big=3, behemoth=4}
function BaseMemory.nest_clusters(npc, radius, limit)
  local c = npc.character
  if not (c and c.valid) then return {} end
  local home = npc.home or c.position
  local found = c.surface.find_entities_filtered{force="enemy", type={"unit-spawner", "turret"},
    position=home, radius=radius or 400, limit=400}
  local clusters = {}
  for _, e in ipairs(found) do
    local p, into = e.position, nil
    for _, k in ipairs(clusters) do
      if (k.x - p.x)^2 + (k.y - p.y)^2 <= 40 * 40 then into = k break end
    end
    if not into then
      into = {x=p.x, y=p.y, n=0, spawners=0, worms={}, pollution=0}
      clusters[#clusters + 1] = into
    end
    into.n = into.n + 1
    into.x, into.y = into.x + (p.x - into.x) / into.n, into.y + (p.y - into.y) / into.n
    if e.type == "unit-spawner" then
      into.spawners = into.spawners + 1
      local ok, soaked = pcall(function() return c.surface.get_pollution(p) end)
      into.pollution = into.pollution + (ok and tonumber(soaked) or 0)
    else
      local size = "small"
      for word in pairs(WORM_SIZE) do if e.name:find(word, 1, true) then size = word end end
      into.worms[size] = (into.worms[size] or 0) + 1
    end
  end
  for _, k in ipairs(clusters) do
    k.distance = math.floor(math.sqrt((k.x - home.x)^2 + (k.y - home.y)^2))
    k.x, k.y, k.pollution, k.n = math.floor(k.x), math.floor(k.y), math.floor(k.pollution + 0.5), nil
  end
  table.sort(clusters, function(a, b) return a.distance < b.distance end)
  while #clusters > (limit or 8) do table.remove(clusters) end
  return clusters
end

-- Resources the engineer's drills are running out of: minutes left over all its blocks on each, for
-- those under `minutes` (what the strategist hears; the planners read the blocks themselves).
function BaseMemory.running_out(blocks, minutes)
  local left, rate = {}, {}
  for _, b in ipairs(blocks or {}) do
    if b.ore then
      left[b.ore.name] = (left[b.ore.name] or 0) + b.ore.left
      rate[b.ore.name] = (rate[b.ore.name] or 0) + b.ore.rate
    end
  end
  local out = {}
  for name, n in pairs(left) do
    local m = rate[name] > 0 and math.floor(n / rate[name] / 60) or 0
    if m < (minutes or 30) then out[#out + 1] = {resource=name, minutes=m, left=n} end
  end
  table.sort(out, function(a, b) return a.minutes < b.minutes end)
  return out
end

-- Its logistic networks (scripts/logistics.lua), for the site export.
function BaseMemory.logistics(npc)
  return has_logistics and try(function() return Logistics.export(npc) end) or nil
end

return BaseMemory
