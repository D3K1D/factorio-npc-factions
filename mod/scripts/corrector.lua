-- The corrector: an engineer's hands for small repairs to what it built, without a redesign.
--
-- Every few seconds the engineer looks over a slice of its own buildings and reads what each
-- one reports. A building with a known problem gets a small fix, carried out like any other
-- job and paid for with the engineer's own items:
--   * an inserter that waits on a machine with nothing to give, while the belt or chest it
--     drops into holds what that machine needs, faces the wrong way: rotate it;
--   * a chest that is full (and not limited on purpose) while the engineer is short of what it holds: add an
--     inserter and a second chest beside it, or empty it by hand (a store that fills while the engineer has plenty
--     is no fault: its producer waits, and a smelting store that fills sends the rest on to the bus);
--   * a building on no power network, or on one no generator feeds: close the gap between that network (or the
--     building) and the nearest live pole with a pole line (medium poles once researched);
--   * a belt lane carrying what does not belong on it (a lost piece turned a junction into a curve, and coal
--     flowed onto the ore lane: run 32, 2026-10-25): pick the wrong items off along its line, as players do.
-- What a small fix cannot answer is passed on: when many buildings report low power the
-- engineer notes a power shortage (in its export), and the planner builds a power plant.
-- A fix is judged a while after it is done (is the problem gone?), and the engineer learns
-- which fix works for which problem. Twice failed on one building, it gives up on that
-- building and notes that it needs a redesign. Repairs never block building: one at a time,
-- and the scan looks at a bounded slice per pass so large bases stay cheap; out at far work, one far
-- from the engineer waits for its return unless the plan being built needs it. A repair's pieces are got at
-- once and put down in one walk; one that takes more pieces than the buildings it brings back are worth
-- waits while a stage is being built; and one not done within REPAIR_FOR counts as failed.
local Corrector = {}
local Hands = require("scripts.hands")

local SCAN_EVERY = 600     -- ticks between scan slices
local SLICE = 150          -- buildings looked at per slice
local CHECK_AFTER = 3600   -- a fix is judged this long after it is done
local MAX_TRIES = 2        -- per building and problem
local MAX_STEPS = 12
local MAX_POLES = 56       -- a pole line out to a far field that was never joined
local POLE_SEARCH = 360
local SHORT_AFTER = 3      -- buildings on low power in one slice that make a power shortage
local SHORT_FOR = 36000    -- how long a noted shortage stands without new reports
local GIVE_UP_FOR = 216000 -- a building given up on is looked at again after an hour (things change: a rebuild)
local SWEEP_BELTS = 128    -- belts along a line looked over by one clean-up
local SWEEP_PICKS = 60     -- pickups per clean-up at most (a source still feeding wrong items is not chased forever)
local REPAIR_FOR = 18000   -- ticks a repair may take from its start to its last piece (5 minutes), else it failed
-- Pieces a repair may take while a stage is being built: a few, and a couple more for each building it brings back
-- (run 59, 2026-10-26: 26 poles for one coal field ran 32 game minutes beside a bus stage, a pole a trip).
local REPAIR_FREE, REPAIR_EACH = 4, 2

-- Problems and the fixes that answer them, in the order they are first tried.
Corrector.FIXES = {backwards={"rotate"}, full_chest={"overflow_chest", "empty_by_hand"}, unpowered={"pole_bridge"},
  wrong_lane={"pick_off"}, blocked_feed={"pick_off"}}

local function state(npc)
  npc.corrector = npc.corrector or {plan=1, index=0, next_scan=0, tries={}, gave_up={}, log={}}
  return npc.corrector
end

local function stats()
  storage.corrector_stats = storage.corrector_stats or {}
  return storage.corrector_stats
end

local function holds(e, item)
  if not (e and e.valid) then return false end
  if e.type == "transport-belt" or e.type == "underground-belt" or e.type == "splitter" or e.type == "loader" then
    for line = 1, 2 do
      local ok, n = pcall(function() return e.get_transport_line(line).get_item_count(item) end)
      if ok and n > 0 then return true end
    end
    return false
  end
  local ok, n = pcall(function() return e.get_item_count(item) end)
  return ok and n > 0
end

-- Electric networks the engineer's generators feed. A building on another network (a pole group
-- that never got joined to the base) has no power just as one on no network at all. Recomputed on
-- each use (no cache that a loaded save would not share).
function Corrector.live_networks(npc, surface)
  local ids, any = {}, false
  local ok, found = pcall(function()
    return surface.find_entities_filtered{force=npc.force, type={"generator", "solar-panel", "burner-generator"}}
  end)
  for _, g in ipairs(ok and found or {}) do
    if g.valid and g.electric_network_id then ids[g.electric_network_id], any = true, true end
  end
  return any and ids or nil
end

local function line_items(e, line)
  local ok, contents = pcall(function() return e.get_transport_line(line).get_contents() end)
  return ok and contents or {}
end

-- The items on a belt's lanes that do not belong there, as {line=, name=, count=}. A belt's plan record may say what
-- each lane is meant to carry (record.lanes[line]: a set of item names, from companion/belt_flow.contracts); a lane
-- it says nothing about holds nothing wrong. Without the plan's word, `useful` (what the machines it feeds can use)
-- judges, and which lane is wrong is not known: what counts is taken off both (line nil). Seen from one belt, it is
-- blocked when every lane holds items and none of them is useful (a free lane may just be waiting for its ore);
-- in a clean-up already under way (`sweeping`), whatever is not useful on a belt with nothing useful on it goes:
-- the jammed lane looks the same as one waiting once the other lane has drained (run 32's junction, 2026-10-25).
function Corrector.wrong_items(e, record, useful, sweeping)
  local out = {}
  if not (e and e.valid and e.type == "transport-belt") then return out end
  local lanes = record and record.lanes
  if lanes then
    for line = 1, 2 do
      local allowed = lanes[line]
      if allowed then
        for _, it in ipairs(line_items(e, line)) do
          if not allowed[it.name] then
            out[#out + 1] = {line=line, name=it.name, count=it.count, quality=it.quality or "normal"}
          end
        end
      end
    end
    return out
  end
  if not useful then return out end
  local by = {}
  for line = 1, 2 do
    local contents = line_items(e, line)
    if #contents == 0 and not sweeping then return {} end
    for _, it in ipairs(contents) do
      if useful[it.name] then return {} end
      local key = it.name .. "@" .. (it.quality or "normal")   -- (each quality its own: Codex, W-25)
      if not by[key] then
        by[key] = {name=it.name, count=0, quality=it.quality or "normal"}
        out[#out + 1] = by[key]
      end
      by[key].count = by[key].count + it.count
    end
  end
  return out
end

-- What a machine can use from a belt: its recipe's ingredients; for a furnace, anything it can smelt (from the
-- prototypes, the same on every machine). Fuel is left out: a feed inserter waiting has none to give it.
local smeltable = {}
local function recipe_categories(recipe)   -- (Factorio 2.1: a list of categories; 2.0: one)
  local ok, list = pcall(function() return recipe.categories end)
  if ok and type(list) == "table" then return list end
  local ok2, one = pcall(function() return recipe.category end)
  return ok2 and one and {one} or {}
end
local function useful_for(m)
  if not (m and m.valid) then return nil end
  if m.type == "furnace" then
    local known = smeltable[m.name]
    if not known then
      known = {}
      local cats = m.prototype.crafting_categories or {}
      for _, recipe in pairs(prototypes.recipe or {}) do
        for _, cat in ipairs(recipe_categories(recipe)) do
          if cats[cat] then
            for _, ing in ipairs(recipe.ingredients or {}) do if ing.type == "item" then known[ing.name] = true end end
          end
        end
      end
      smeltable[m.name] = known
    end
    return known
  end
  local ok, recipe = pcall(function() return m.get_recipe() end)
  if not (ok and recipe) then return nil end
  local out = {}
  for _, ing in ipairs(recipe.ingredients or {}) do if ing.type == "item" then out[ing.name] = true end end
  return out
end

local function starved(m)
  local S = defines.entity_status
  return m and m.valid and (m.type == "furnace" or m.type == "assembling-machine")
    and (m.status == S.no_ingredients or (S.item_ingredient_shortage ~= nil and m.status == S.item_ingredient_shortage))
end

-- Whether a chest is the store at the end of a belt (an inserter or loader moves into it from a belt): a store meant
-- to fill, its line backing up for whatever else takes from it (a smelting store sends the rest on to the bus,
-- plate_feed). An overflow chest there would turn the line into a sink.
local BELTS = {["transport-belt"]=true, ["underground-belt"]=true, splitter=true}
local function belt_fed(e)
  for _, x in ipairs(e.surface.find_entities_filtered{type={"inserter", "loader", "loader-1x1"}, position=e.position,
      radius=4}) do
    if x.valid then
      if x.type == "inserter" then
        local from = x.pickup_target
        if x.drop_target == e and from and from.valid and BELTS[from.type] then return true end
      elseif x.loader_container == e then
        return true
      end
    end
  end
  return false
end

-- What a chest holds most of.
local function main_item(inv)
  local best
  for _, stack in ipairs(inv.get_contents() or {}) do
    if not best or stack.count > best.count then best = stack end
  end
  return best and best.name
end

-- What is wrong with this building, if it is something the corrector knows. `live`: the powered
-- networks (Corrector.live_networks), if known. `record`: its plan's entry (what its belt lanes carry).
-- `short(item)`: whether the engineer is short of an item (a full chest of what it has plenty of is no fault).
function Corrector.symptom(e, live, record, short)
  if not (e and e.valid) then return nil end
  local S = defines.entity_status
  if e.type == "transport-belt" and record and record.lanes and Corrector.wrong_items(e, record)[1] then
    return "wrong_lane"
  end
  if e.type == "inserter" and e.status == S.waiting_for_source_items then
    local from, to = e.pickup_target, e.drop_target
    -- Only an inserter that drops into a supply (a belt or a chest) can be facing the wrong way: one
    -- passing items from machine to machine (a mall chain) shares ingredients with both ends.
    local supply = to and to.valid and (to.type == "transport-belt" or to.type == "underground-belt"
      or to.type == "splitter" or to.type == "container" or to.type == "logistic-container")
    if from and supply and from.valid and (from.type == "assembling-machine" or from.type == "furnace") then
      local out = from.get_output_inventory()
      local recipe = from.get_recipe()
      if out and out.is_empty() and recipe then
        for _, ing in ipairs(recipe.ingredients) do
          if ing.type == "item" and holds(to, ing.name) then return "backwards" end
        end
      end
    end
    -- A machine starving beside a belt whose every lane holds only what it cannot use: a lane is blocked.
    if from and from.valid and from.type == "transport-belt" and starved(to) then
      local useful = useful_for(to)
      if useful and Corrector.wrong_items(from, nil, useful)[1] then return "blocked_feed" end
    end
  elseif e.type == "container" then
    local inv = e.get_inventory(defines.inventory.chest)
    if inv and inv.count_empty_stacks() == 0 then
      local limited = inv.supports_bar() and inv.get_bar() <= #inv
      -- (a belt's store fills on purpose; the engineer takes from a store when it needs its items: acquire)
      if not limited and (not short or short(main_item(inv))) and not belt_fed(e) then return "full_chest" end
    end
  end
  if e.status == S.no_power and e.prototype.electric_energy_source_prototype
      and (not e.electric_network_id or live and not live[e.electric_network_id]) then
    return "unpowered"
  end
  return nil
end

-- The fix to try: untried ones first (in the listed order), then the best record, with a little
-- room for the others (UCB).
function Corrector.pick(symptom)
  local options, s = Corrector.FIXES[symptom], stats()
  local total = 0
  for _, fix in ipairs(options) do total = total + ((s[symptom .. "/" .. fix] or {}).tries or 0) end
  local best, score = nil, -1
  for _, fix in ipairs(options) do
    local row = s[symptom .. "/" .. fix]
    if not row or row.tries == 0 then return fix end
    local value = row.wins / row.tries + math.sqrt(2 * math.log(math.max(total, 1)) / row.tries)
    if value > score then best, score = fix, value end
  end
  return best
end

local function free(surface, name, position, direction, force)
  return surface.can_place_entity{name=name, position=position, direction=direction or 0, force=force,
    build_check_type=defines.build_check_type.manual}
end

local STEP = {[0]={0, -1}, [4]={1, 0}, [8]={0, 1}, [12]={-1, 0}}

-- The pole a repair lays: medium once researched (no wood, farther apart), else small.
local function pole_kind(npc)
  local r = npc.force.recipes["medium-electric-pole"]
  return (r and r.enabled) and "medium-electric-pole" or "small-electric-pole"
end

local function wire_reach(name)
  local p = prototypes.entity[name]
  return p and p.get_max_wire_distance and p.get_max_wire_distance() or 7.5
end

local function dist2(a, b) return (a.x - b.x)^2 + (a.y - b.y)^2 end

-- Poles of `kind` from `current` (a pole there of `current_reach`, or a placed first pole) toward `target`, each within
-- wire reach of the last (the shorter reach of two poles joins them); nil when the line gets stuck or too long.
local function extend_line(surface, force, kind, out, current, current_reach, target)
  local own, target_reach = wire_reach(kind), wire_reach(target.name)
  while dist2(current, target.position) > (math.min(current_reach, target_reach) - 0.5)^2 do
    if #out >= MAX_POLES then return nil end
    local step = math.min(current_reach, own) - 0.5
    local span = math.ceil(step)
    local nxt, nd = nil, math.huge
    for dx = -span, span do
      for dy = -span, span do
        local p = {x=math.floor(current.x) + dx + 0.5, y=math.floor(current.y) + dy + 0.5}
        if dist2(p, current) <= step * step then
          local d = dist2(p, target.position)
          if d < nd and free(surface, kind, p, 0, force) then nxt, nd = p, d end
        end
      end
    end
    if not nxt then return nil end
    current, current_reach = nxt, own
    out[#out + 1] = {name=kind, position=current}
  end
  return out
end

-- Poles that join `e` to a live network: across the gap between the network `e` stands on (no generator feeds it:
-- a pole group never joined to the base) and the nearest live pole, or from beside `e` when that is nearer or `e`
-- is on no network. (Run 59, 2026-10-26: a coal field's own 26-pole line ended 8.5 tiles from the engines' pole; a
-- line from the drill itself to the nearest live pole took 26 poles more, one would have done.)
local function pole_line(npc, e)
  local surface, force = e.surface, npc.force
  local live = Corrector.live_networks(npc, surface)
  local poles = surface.find_entities_filtered{type="electric-pole", force=force, position=e.position, radius=POLE_SEARCH}
  local dead = e.electric_network_id
  local targets, starts = {}, {}
  for _, p in ipairs(poles) do
    if p.valid and p.electric_network_id then
      if not live or live[p.electric_network_id] then targets[#targets + 1] = p
      elseif dead and p.electric_network_id == dead then starts[#starts + 1] = p end
    end
  end
  local kind = pole_kind(npc)
  -- From beside the building: the live pole nearest it, a pole more than the gap.
  local target, best = nil, math.huge
  for _, t in ipairs(targets) do
    local d = dist2(t.position, e.position)
    if d < best then target, best = t, d end
  end
  if not target then return nil end
  local from, gap = nil, math.sqrt(best) + wire_reach(kind)
  for _, s in ipairs(starts) do
    for _, t in ipairs(targets) do
      local d = math.sqrt(dist2(s.position, t.position))
      if d < gap then from, target, gap = s, t, d end
    end
  end
  if from then
    local line = extend_line(surface, force, kind, {}, from.position, wire_reach(from.name), target)
    if line and line[1] then return line end
    -- (within reach already, yet not joined: start beside the building instead)
    target, best = nil, math.huge
    for _, t in ipairs(targets) do
      local d = dist2(t.position, e.position)
      if d < best then target, best = t, d end
    end
  end
  local box = e.bounding_box
  -- First pole: on a free tile right beside the building, nearest the target.
  local choices = {}
  for x = math.floor(box.left_top.x) - 1, math.ceil(box.right_bottom.x) do
    for y = math.floor(box.left_top.y) - 1, math.ceil(box.right_bottom.y) do
      local p = {x=x + 0.5, y=y + 0.5}
      if free(surface, kind, p, 0, force) then
        choices[#choices + 1] = {p=p, d=dist2(p, target.position)}
      end
    end
  end
  table.sort(choices, function(a, b) return a.d < b.d end)
  if not choices[1] then return nil end
  return extend_line(surface, force, kind, {{name=kind, position=choices[1].p}}, choices[1].p, wire_reach(kind), target)
end

-- What a repair of `e` brings back: the buildings on the dead network it stands on in its plan (a coal field's
-- drills, a module's machines), or itself.
local function restores(e, plan)
  local dead = e.electric_network_id
  if not dead or not plan then return 1 end
  local n = 0
  for _, id in ipairs(plan.order or {}) do
    local r = plan.entities[id]
    local x = r and r.entity
    if x and x.valid and x.electric_network_id == dead and x.prototype.electric_energy_source_prototype then n = n + 1 end
  end
  return math.max(1, n)
end

-- Whether a live network reaches the tile at `at`: a pole of one whose supply area covers it.
local function powered_at(npc, surface, at, live)
  if not live then return false end
  local R = 16   -- (a supply area is a square: a substation's reaches 9 out each way, more at a higher quality)
  for _, p in ipairs(surface.find_entities_filtered{type="electric-pole", force=npc.force,
      area={{at.x - R, at.y - R}, {at.x + R, at.y + R}}}) do
    if p.valid and p.electric_network_id and live[p.electric_network_id] then
      local ok, r = pcall(function() return p.prototype.get_supply_area_distance(p.quality) end)
      r = ok and r or p.prototype.supply_area_distance or 2.5
      if math.abs(p.position.x - at.x) <= r + 0.35 and math.abs(p.position.y - at.y) <= r + 0.35 then return true end
    end
  end
  return false
end

-- Whether burner entity `name` burns `item`: a fuel, of one of its burner's categories (as DesignedStart.takes_fuel).
local function burns(name, item)
  local p = prototypes.item[item]
  if not (p and (p.fuel_value or 0) > 0) then return false end
  local ok, fits = pcall(function()
    local cats = prototypes.entity[name].burner_prototype.fuel_categories
    local own = p.fuel_categories or {p.fuel_category}
    if not (cats and own) then return true end
    for _, c in ipairs(own) do if cats[c] then return true end end
    return false
  end)
  return not ok or fits ~= false
end

-- The inserter an overflow chest takes at `at`: an electric one where a live network reaches it, a burner one where
-- what it moves is a fuel (it feeds itself from it); none else. (Run 60, 2026-10-26: an electric one with no power
-- beside a stone store, then a pole line for it.)
local function overflow_inserter(npc, e, at, live)
  if powered_at(npc, e.surface, at, live) then return "inserter" end
  local inv = e.get_inventory(defines.inventory.chest)
  local item = inv and main_item(inv)
  local recipe = npc.force.recipes["burner-inserter"]
  if item and recipe and recipe.enabled and burns("burner-inserter", item) then return "burner-inserter" end
  return nil
end

-- The steps of a fix: {kind="rotate"|"place"|"empty", ...}.
function Corrector.steps(npc, e, symptom, fix)
  if fix == "rotate" then
    return {{kind="rotate", direction=(e.direction + 8) % 16}}
  elseif fix == "overflow_chest" then
    if belt_fed(e) then return nil end
    local live = Corrector.live_networks(npc, e.surface)
    for _, d in ipairs({0, 4, 8, 12}) do
      local v = STEP[d]
      local ins = {x=e.position.x + v[1], y=e.position.y + v[2]}
      local box = {x=e.position.x + 2 * v[1], y=e.position.y + 2 * v[2]}
      local picks = (d + 8) % 16   -- the inserter picks from the full chest's side
      local kind = overflow_inserter(npc, e, ins, live)
      if kind and free(e.surface, kind, ins, picks, npc.force) and free(e.surface, e.name, box, 0, npc.force) then
        return {{kind="place", name=e.name, position=box, direction=0},
          {kind="place", name=kind, position=ins, direction=picks}}
      end
    end
  elseif fix == "empty_by_hand" then
    return {{kind="empty"}}
  elseif fix == "pick_off" then
    return {{kind="sweep"}}
  elseif fix == "pole_bridge" then
    local line = pole_line(npc, e)
    if line then
      local out = {}
      for i, p in ipairs(line) do out[i] = {kind="place", name=p.name, position=p.position, direction=0} end
      return out
    end
  end
  return nil
end

-- The belts along a line from `start`: what it feeds, through undergrounds, and what feeds it. With `branches` every
-- belt feeding in counts; without, only the one straight behind (a feed column back to its head, not the ore and
-- coal lines that join it there).
local function line_of(start, limit, branches)
  local seen, out, queue, i = {}, {}, {start}, 1
  while queue[i] and #out < limit do
    local e = queue[i]
    i = i + 1
    if e.valid and not seen[e.unit_number] then
      seen[e.unit_number] = true
      if e.type == "transport-belt" then out[#out + 1] = e end
      local ok, n = pcall(function() return e.belt_neighbours end)
      for side, list in ipairs(ok and n and {n.inputs or {}, n.outputs or {}} or {}) do
        for _, o in ipairs(list) do
          local along = branches or side == 2 or o.direction == e.direction
          if along and o.valid and o.force == e.force and not seen[o.unit_number] then queue[#queue + 1] = o end
        end
      end
      if e.type == "underground-belt" then
        local ok2, other = pcall(function() return e.neighbours end)
        if ok2 and other and other.valid and not seen[other.unit_number] then queue[#queue + 1] = other end
      end
    end
  end
  return out
end

-- What a clean-up looks over: the belts along the line (with their plan records, which say what each lane
-- carries), and for a blocked feed what its machine can use.
local function sweep_scope(e, symptom, plan)
  local start, useful = e, nil
  if symptom == "blocked_feed" then start, useful = e.pickup_target, useful_for(e.drop_target) end
  local records = {}
  for _, id in ipairs(plan and plan.order or {}) do
    local r = plan.entities[id]
    local x = r and r.entity
    if r and r.lanes and x and x.valid and x.unit_number then records[x.unit_number] = r end
  end
  local belts = {}
  if start and start.valid then
    for _, b in ipairs(line_of(start, SWEEP_BELTS, useful == nil)) do
      belts[#belts + 1] = {e=b, record=records[b.unit_number]}
    end
  end
  return belts, useful
end

-- Whether a finished fix answered its problem: the building no longer reports it, and for a blocked feed its machine
-- works again (the belt it picks from can look clean while the jammed lane still holds what it cannot use).
local function resolved(npc, fix)
  local target = fix.target
  local still = Corrector.symptom(target, target and target.valid
    and Corrector.live_networks(npc, target.surface) or nil, fix.record) == fix.symptom
  if still then return false end
  if fix.symptom == "blocked_feed" then return not (target and target.valid and starved(target.drop_target)) end
  return true
end

local function key(e, symptom) return tostring(e.unit_number or (e.position.x .. "," .. e.position.y)) .. ":" .. symptom end

-- A repair that failed backs off for its whole place, not one building (W-28): the grown bus's iron stage stood complete
-- with its inserters unpowered, and a pole bridge was tried for each inserter in turn, twice, five minutes each, while
-- every other goal waited 200 game minutes. Its place: the same problem in the same plan (else the same 32-tile cell);
-- held BACKOFF, doubling with each failure there (to 8 times), and lifted when a repair there works.
Corrector.BACKOFF = 36000
Corrector.BACKOFF_DOUBLINGS = 3
function Corrector.place_key(symptom, plan, at)
  local where = plan and plan.plan_id or (at and (math.floor(at.x / 32) .. "," .. math.floor(at.y / 32))) or "?"
  return symptom .. "@" .. tostring(where)
end

function Corrector.backed_off(c, symptom, plan, at, tick)
  local b = c.backoff and c.backoff[Corrector.place_key(symptom, plan, at)]
  return b ~= nil and tick < b.till
end

-- The engineer's own buildings, a slice at a time across all its plans.
local function scan(npc, c, tick, short)
  c.live = npc.character and npc.character.valid and Corrector.live_networks(npc, npc.character.surface) or nil
  -- The plan being built comes first (a stage that will not finish is where help is needed).
  local plans = {}
  local d = npc.design
  if d and d.plan and d.status == "building" then plans[1] = d.plan end
  -- In the plan being built, only stages that are finished (or running) count: a building whose
  -- stage is still going up is not broken, just not wired yet.
  local live = {}
  for _, s in ipairs(d and d.plan and d.plan.stages or {}) do
    if s.status == "complete" or s.status == "producing" then live[s.id] = true end
  end
  for _, plan in ipairs(npc.design_history or {}) do plans[#plans + 1] = plan end
  if #plans == 0 then return nil end
  local looked, low = 0, 0
  local LOW = defines.entity_status.low_power
  while looked < SLICE do
    if c.plan > #plans then c.plan, c.index = 1, 0; break end
    local plan = plans[c.plan]
    c.index = c.index + 1
    local id = plan.order[c.index]
    if not id then
      c.plan, c.index = c.plan + 1, 0
    else
      looked = looked + 1
      local entity = plan.entities[id]
      local e = (plan ~= (d and d.plan) or live[entity.stage]) and entity.entity or nil
      if e and e.valid and LOW and e.status == LOW then low = low + 1 end
      local ok, symptom = pcall(Corrector.symptom, e, c.live, entity, short)   -- (a repair is never worth the game)
      if not ok then
        if not c.error_logged then log("[npc-factions] npc=" .. npc.id .. " corrector check failed: " .. tostring(symptom)) end
        c.error_logged, symptom = true, nil
      end
      local k = symptom and key(e, symptom)
      if k and c.gave_up[k] and tick - c.gave_up[k] >= GIVE_UP_FOR then c.gave_up[k], c.tries[k] = nil, nil end
      if symptom and not c.gave_up[k] and not Corrector.backed_off(c, symptom, plan, e.position, tick) then
        return e, symptom, plan, entity
      end
    end
  end
  if low >= SHORT_AFTER then
    if not (npc.power_short and tick - npc.power_short < SHORT_FOR) then
      log("[npc-factions] npc=" .. npc.id .. " power shortage: " .. low .. " buildings on low power")
    end
    npc.power_short = tick
  end
end

-- A power shortage noted recently enough to act on (the export tells the planner).
function Corrector.power_short(npc, tick)
  return npc.power_short ~= nil and tick - npc.power_short < SHORT_FOR
end

local function record(npc, c, fix, result, tick)
  c.log[#c.log + 1] = {tick=tick, symptom=fix.symptom, fix=fix.fix, result=result, reason=fix.reason,
    picked=fix.picked, x=fix.where and fix.where.x, y=fix.where and fix.where.y}
  while #c.log > 12 do table.remove(c.log, 1) end
end

local function finish(npc, c, fix, worked, tick)
  local s = stats()
  local row = s[fix.symptom .. "/" .. fix.fix] or {tries=0, wins=0}
  row.tries = row.tries + 1
  if worked then row.wins = row.wins + 1 end
  s[fix.symptom .. "/" .. fix.fix] = row
  c.backoff = c.backoff or {}
  local place = Corrector.place_key(fix.symptom, fix.plan, fix.where)
  if worked then
    c.backoff[place] = nil
  else
    local b = c.backoff[place] or {n=0}
    b.till = tick + Corrector.BACKOFF * 2 ^ math.min(b.n, Corrector.BACKOFF_DOUBLINGS)
    b.n = b.n + 1
    c.backoff[place] = b
    log("[npc-factions] npc=" .. npc.id .. " leaves " .. fix.symptom .. " repairs in " .. place .. " for "
      .. math.floor((b.till - tick) / 3600) .. " min (" .. tostring(fix.reason or "not fixed") .. ")")
  end
  if not worked then
    c.tries[fix.key] = (c.tries[fix.key] or 0) + 1
    if c.tries[fix.key] >= MAX_TRIES then
      c.gave_up[fix.key] = tick
      log("[npc-factions] npc=" .. npc.id .. " cannot fix " .. fix.symptom .. " at " .. tostring(fix.where and fix.where.x)
        .. "," .. tostring(fix.where and fix.where.y) .. ": needs a redesign")
      -- (a piece of the plan being built: that plan is handed back for it, its stage left producing for good otherwise;
      -- DesignedStart.next acts on it, W-32)
      local d = npc.design
      if fix.plan and d and d.plan == fix.plan and d.status == "building" then
        d.repair_given_up = {symptom=fix.symptom, tick=tick, x=fix.where and fix.where.x, y=fix.where and fix.where.y,
          stage=fix.record and fix.record.stage}   -- (its stage: only that one is handed back for it, Codex)
      end
    end
  end
  record(npc, c, fix, worked and "fixed" or "not fixed", tick)
  local took = {}
  for item, n in pairs(fix.picked or {}) do took[#took + 1] = n .. " " .. item end
  table.sort(took)
  log("[npc-factions] npc=" .. npc.id .. " repair " .. fix.fix .. " for " .. fix.symptom .. " at "
    .. tostring(fix.where and fix.where.x) .. "," .. tostring(fix.where and fix.where.y) .. ": "
    .. (worked and "fixed" or "not fixed") .. (fix.reason and (" (" .. fix.reason .. ")") or "")
    .. (#took > 0 and (", picked off " .. table.concat(took, ", ")) or ""))
  c.fix = nil
end

-- Remember a placed piece as part of the plan it repairs, so it is kept and maintained.
local function adopt(plan, entity, tick)
  if not (plan and entity and entity.valid) then return end
  local id = "fix-" .. tostring(entity.unit_number or tick)
  local item = entity.prototype.items_to_place_this and entity.prototype.items_to_place_this[1]
  local stage = plan.stages[#plan.stages]
  plan.entities[id] = {id=id, prototype=entity.name, type=entity.type, item=item and (item.name or item[1]),
    world={x=entity.position.x, y=entity.position.y}, direction=entity.direction, failures=0,
    stage=stage and stage.id, entity=entity, built_tick=tick, repair=true}
  plan.order[#plan.order + 1] = id
  if stage then stage.entity_ids[#stage.entity_ids + 1] = id end
end

-- One job toward the current repair (or the start of a new one); nil when there is nothing to do.
-- `acquire(npc, item, count, depth)` returns a job that gets the item, nil when it is in hand,
-- or nil and a reason when it cannot be had. `ctx` (the caller's, never kept: it holds a function): `short(item)`,
-- whether the engineer is short of an item; `away`, whether it is out at far work; `near`, tiles from the engineer a
-- repair may be while it is away.
function Corrector.step(npc, tick, acquire, ctx)
  ctx = ctx or {}
  local c = state(npc)
  local fix = c.fix
  if fix then
    if not (fix.target and fix.target.valid) then c.fix = nil; return nil end
    -- (an overflow fix whose inserter would no longer run where it goes, a pole lost meanwhile or one laid out before
    -- power was weighed, kept in a save, or whose chest is a belt's store: dropped, before each piece)
    if fix.fix == "overflow_chest" and not fix.check_at then
      local live = Corrector.live_networks(npc, fix.target.surface)
      for _, s in ipairs(fix.steps) do
        if s.kind == "place" and not s.done and (s.name == "inserter" or s.name == "burner-inserter")
            and (belt_fed(fix.target) or overflow_inserter(npc, fix.target, s.position, live) ~= s.name) then
          log("[npc-factions] npc=" .. npc.id .. " drops the overflow chest at " .. fix.where.x .. "," .. fix.where.y
            .. ": its inserter would not run there, or it is a belt's store")
          c.fix = nil
          return nil
        end
      end
    end
    if fix.check_at then
      if tick < fix.check_at then return nil end
      finish(npc, c, fix, resolved(npc, fix), tick)   -- (by the building alone: a still-full chest is not fixed)
      return nil
    end
    local step = fix.steps[fix.step]
    if not step then fix.check_at = tick + CHECK_AFTER; return nil end
    if tick - (fix.started or tick) > REPAIR_FOR then   -- (bounded: not done in time, it failed)
      if fix.reason == "in_danger" then
        -- (waiting on danger, not failing: its place backed off a while, no try counted against the building; W-30)
        c.backoff = c.backoff or {}
        local place = Corrector.place_key(fix.symptom, fix.plan, fix.where)
        c.backoff[place] = {till=tick + Corrector.BACKOFF, n=(c.backoff[place] and c.backoff[place].n or 0)}
        log("[npc-factions] npc=" .. npc.id .. " repair " .. fix.fix .. " for " .. fix.symptom .. " waits: in danger")
        c.fix = nil
        return nil
      end
      fix.reason = fix.reason or "took too long"
      finish(npc, c, fix, false, tick)
      return nil
    end
    -- (its jobs end with the repair's time: REPAIR_FOR bounds one under way too)
    local deadline = (fix.started or tick) + REPAIR_FOR
    if step.kind == "rotate" then
      return {kind="configure_direction", target=fix.target, direction=step.direction, deadline=deadline},
        {upkeep="repair", step=fix.step}
    elseif step.kind == "sweep" then
      -- The nearest belt along the line still holding what does not belong: its items off, until none is left.
      local best, far
      if (fix.picks or 0) < SWEEP_PICKS then
        local at = npc.character and npc.character.valid and npc.character.position or fix.where
        for _, b in ipairs(fix.belts or {}) do
          local wrong
          for _, it in ipairs(b.e.valid and Corrector.wrong_items(b.e, b.record, fix.useful, true) or {}) do
            -- (what its hands can take: a plain item of normal quality, as the pickup takes; W-25)
            if not wrong and it.quality == "normal" and Hands.belt_pickable(it.name) then wrong = it end
          end
          if wrong then
            local d = (b.e.position.x - at.x)^2 + (b.e.position.y - at.y)^2
            if not far or d < far then best, far = {e=b.e, item=wrong}, d end
          end
        end
      end
      if not best then fix.step = fix.step + 1; return nil end
      fix.picks = (fix.picks or 0) + 1
      return {kind="belt_pickup", target=best.e, item=best.item.name, count=best.item.count, line=best.item.line,
        up_to=true, deadline=deadline}, {upkeep="repair", step=fix.step, sweep=true}
    elseif step.kind == "empty" then
      local contents = fix.target.get_inventory(defines.inventory.chest).get_contents()
      table.sort(contents, function(a, b) return a.count > b.count end)
      local top = contents[1]
      if not top then fix.step = fix.step + 1; return nil end
      return {kind="transfer", target=fix.target, item=top.name, count=math.min(top.count, 400), mode="withdraw",
        deadline=deadline}, {upkeep="repair", step=fix.step}
    elseif step.kind == "place" then
      -- Every piece still to place from here, got at once (crafted together) and put down in one walk: a pole line
      -- placed a pole a trip went back and forth between other work for 32 game minutes (run 59, 2026-10-26).
      local todo, need, order = {}, {}, {}
      for i = fix.step, #fix.steps do
        local s = fix.steps[i]
        if s.kind ~= "place" then break end
        if not s.done then
          local item = prototypes.entity[s.name].items_to_place_this[1]
          item = item.name or item[1]
          todo[#todo + 1] = {i=i, s=s, item=item}
          if not need[item] then order[#order + 1] = item end
          need[item] = (need[item] or 0) + 1
        end
      end
      for _, item in ipairs(order) do
        local job, why = acquire(npc, item, need[item], 0)
        if job then
          job.deadline = math.min(job.deadline or deadline, deadline)
          return job, {upkeep="repair", acquire=item}
        end
        if why then
          -- (on its way: crafted in the background, or its machines make it: designed_start's waits are no failure)
          local w = tostring(why)
          if w:sub(1, 9) == "crafting:" or w:sub(1, 9) == "smelting:" or w:sub(1, 12) == "drills_make:"
              or w:sub(1, 15) == "machine_making:" then return nil end
          fix.failures = (fix.failures or 0) + 1
          if fix.failures > 20 then finish(npc, c, fix, false, tick) end
          return nil
        end
      end
      -- One at a time where a walk left pieces it could not put down: the single builder clears their spots.
      if not todo[2] or fix.single then
        local t = todo[1]
        return {kind="build", entity_name=t.s.name, item=t.item, position=t.s.position, direction=t.s.direction,
          deadline=deadline}, {upkeep="repair", step=t.i, place=true}
      end
      local placements = {}
      for k, t in ipairs(todo) do
        if k > 64 then break end   -- (a run takes 64 at most: the rest in the next)
        placements[k] = {id="fix-" .. t.i, entity_name=t.s.name, item=t.item, position=t.s.position,
          direction=t.s.direction}
      end
      return {kind="build_run", placements=placements, deadline=deadline}, {upkeep="repair", run=true}
    end
    fix.step = fix.step + 1
    return nil
  end
  if tick < (c.next_scan or 0) then return nil end
  c.next_scan = tick + SCAN_EVERY
  local e, symptom, plan, record = scan(npc, c, tick, ctx.short)
  if not e then return nil end
  -- Out at far work, a repair far from the engineer waits for its return (as supplies do), unless the plan being built
  -- needs it.
  local here = npc.character and npc.character.valid and npc.character.position
  if ctx.away and here and plan ~= (npc.design and npc.design.plan)
      and dist2(e.position, here) > (ctx.near or 64)^2 then
    return nil
  end
  local building = npc.design and npc.design.status == "building"
  -- (what waits, waits for this build: between builds, and in the next, every repair is weighed again)
  local build = building and tostring(npc.design.plan and npc.design.plan.plan_id or npc.design.purpose) or nil
  if not building or c.waiting_for ~= build then c.waiting, c.waiting_for = {}, build end
  if building and c.waiting[key(e, symptom)] then return nil end   -- (still waits for the build)
  local choice = Corrector.pick(symptom)
  local steps = Corrector.steps(npc, e, symptom, choice)
  -- (a fix that does not apply here, the next that does: an overflow chest at a belt's store, say)
  for _, other in ipairs(steps and {} or Corrector.FIXES[symptom]) do
    if other ~= choice then
      steps = Corrector.steps(npc, e, symptom, other)
      if steps then choice = other; break end
    end
  end
  if not steps then
    -- No fix can be laid out here: count it as a failed try (twice, and it needs a redesign).
    finish(npc, c, {symptom=symptom, fix=choice, key=key(e, symptom), where=e.position, plan=plan}, false, tick)
    return nil
  end
  -- Weighed against what it brings back: a repair taking more pieces than that is worth waits while a stage is being
  -- built, and is done between builds.
  local pieces, worth = 0, REPAIR_FREE + REPAIR_EACH * restores(e, plan)
  for _, s in ipairs(steps) do if s.kind == "place" then pieces = pieces + 1 end end
  if building and pieces > worth then
    c.waiting[key(e, symptom)] = tick
    log("[npc-factions] npc=" .. npc.id .. " repair of " .. symptom .. " at " .. e.position.x .. "," .. e.position.y
      .. " waits for the build: " .. pieces .. " pieces for what " .. worth .. " are worth")
    return nil
  end
  c.waiting[key(e, symptom)] = nil
  c.fix = {symptom=symptom, fix=choice, target=e, plan=plan, steps=steps, step=1, key=key(e, symptom),
    where={x=e.position.x, y=e.position.y}, started=tick, record=record, surface=e.surface}
  if choice == "pick_off" then c.fix.belts, c.fix.useful = sweep_scope(e, symptom, plan) end
  log("[npc-factions] npc=" .. npc.id .. " repairing " .. symptom .. " at " .. e.position.x .. "," .. e.position.y
    .. " (" .. e.name .. "): " .. choice)
  return nil
end

function Corrector.on_receipt(npc, meta, receipt, tick)
  local c = state(npc)
  local fix = c.fix
  if not fix or meta.acquire then return end
  if meta.run then   -- a walk putting a repair's pieces down: each one placed is done and kept with its plan
    for _, placed in ipairs(receipt.placed or {}) do
      local i = tonumber(tostring(placed.id or ""):match("^fix%-(%d+)$"))
      local s = i and fix.steps[i]
      if s and not s.done and placed.entity and placed.entity.valid then
        s.done = true
        adopt(fix.plan, placed.entity, tick)
      end
    end
    -- (a walk cut short says nothing of what it put down: each piece standing on its spot is done all the same)
    -- (its own surface: the target may be gone while what the walk put down stands)
    local surface = fix.surface or (fix.target and fix.target.valid and fix.target.surface)
    for i = fix.step, surface and #fix.steps or 0 do
      local s = fix.steps[i]
      if s.kind ~= "place" then break end
      if not s.done then
        local x = surface.find_entity(s.name, s.position)
        if x and x.valid and x.force and x.force.name == npc.force.name then s.done = true; adopt(fix.plan, x, tick) end
      end
    end
    while fix.steps[fix.step] and fix.steps[fix.step].done do fix.step = fix.step + 1 end
    if fix.steps[fix.step] and fix.steps[fix.step].kind == "place" then fix.single = true end
    if receipt.reason == "in_danger" then fix.reason = "in_danger"   -- (it waits: Corrector.step backs off; W-30)
    elseif receipt.status ~= "complete" then
      fix.failures = (fix.failures or 0) + 1
      fix.reason = receipt.reason and tostring(receipt.reason) or fix.reason
      if fix.failures > 3 then finish(npc, c, fix, false, tick) end
    end
    return
  end
  if receipt.reason == "in_danger" then fix.reason = "in_danger"; return end   -- (in danger: it waits, no failure; W-30)
  if receipt.status ~= "complete" then
    fix.failures = (fix.failures or 0) + 1
    fix.reason = receipt.reason and tostring(receipt.reason) or fix.reason
    if fix.failures > 3 then finish(npc, c, fix, false, tick) end
    return
  end
  if meta.place and receipt.entity and receipt.entity.valid then adopt(fix.plan, receipt.entity, tick) end
  if meta.sweep then   -- a clean-up goes on until its line is clean; what it took is kept for the log
    fix.picked = fix.picked or {}
    if receipt.item then fix.picked[receipt.item] = (fix.picked[receipt.item] or 0) + (receipt.count or 0) end
    return
  end
  if meta.place and fix.steps[meta.step] then fix.steps[meta.step].done = true end
  if meta.step == fix.step then fix.step = fix.step + 1 end
  while fix.steps[fix.step] and fix.steps[fix.step].done do fix.step = fix.step + 1 end
end

-- Recent repairs, for the export (what the engineer fixed lately and how it went).
function Corrector.summary(npc)
  local c = npc.corrector
  return c and c.log or {}
end

return Corrector
