-- What the engineer's hands can do, asked before a job is given, so the worker never refuses the same job over and
-- over (W-25): a free spot the game will build on, and an item its hands can take off a belt.
local Hands = {}

-- Item kinds that stack plainly, no data of their own (a blueprint's, an armor's grid): picked off a belt as a player
-- does. Magazines (ammo), packs (tool), capsules and modules too: a defense row's magazines on its belt were refused as
-- "not plain" again and again (normal-7b, NPC 6).
Hands.PLAIN = {item=true, ammo=true, capsule=true, module=true, tool=true, ["repair-tool"]=true, gun=true,
  ["rail-planner"]=true}

function Hands.belt_pickable(item)
  local proto = item and prototypes.item[item]
  return proto ~= nil and Hands.PLAIN[proto.type or "item"] == true   -- (a stub without its kind: a plain item)
end

local STATE = {"health", "spoil_percent", "ammo", "durability"}   -- (what a stack carries of its own)

-- Up to `count` normal `item` off these transport lines into `destination`, stack by stack with each one's own state
-- (a turret's health, a magazine's rounds, a pack's durability, how far it has spoiled): a fresh stack in its pockets
-- would have reset them (Codex, W-25). Only what went in leaves the belt. Returns how many moved.
function Hands.take_from_lines(lines, item, count, destination)
  local remaining = count
  for _, line in ipairs(lines) do
    for i = #line, 1, -1 do   -- (from the end: taking one off moves those after it up)
      if remaining <= 0 then return count end
      local st = line[i]
      if st and st.valid_for_read and st.name == item and (not st.quality or st.quality.name == "normal") then
        local n = math.min(st.count, remaining)
        local def = {name=item, count=n, quality="normal"}
        for _, field in ipairs(STATE) do
          local ok, v = pcall(function() return st[field] end)
          if ok and v ~= nil then def[field] = v end
        end
        local got = destination.insert(def)
        if got >= st.count then st.clear() elseif got > 0 then st.count = st.count - got end
        remaining = remaining - got   -- (one that would not go in, a damaged one say: the next may; Codex)
      end
    end
  end
  return count - remaining
end

local function snap(c, size)
  if size % 2 == 0 then return math.floor(c + 0.5) end
  return math.floor(c) + 0.5
end

Hands.REFUSED_FOR = 10 * 3600   -- (a spot the game refused with nothing there is not asked again for this long)
Hands.REFUSED_MAX = 32

-- Note a spot the game refused for `name` (its own verdict: reach, ground, alignment), so a free spot is looked for
-- elsewhere next time rather than the same try again.
function Hands.refused(npc, name, position, tick)
  npc.refused_spots = npc.refused_spots or {}
  local list = npc.refused_spots
  list[#list + 1] = {name=name, x=position.x, y=position.y, tick=tick}
  while #list > Hands.REFUSED_MAX do table.remove(list, 1) end
end

local function was_refused(npc, name, q, tick)
  for _, r in ipairs(npc.refused_spots or {}) do
    if r.name == name and tick - r.tick < Hands.REFUSED_FOR and math.abs(r.x - q.x) < 0.75
      and math.abs(r.y - q.y) < 0.75 then return true end
  end
  return false
end

-- A spot near `near` where `name` goes, on its own tile grid (a 2x2 on whole tiles, a 1x1 on tile centres) and where
-- the game itself would build it (surface.can_place_entity, manual check), nearest ring first; never one refused
-- lately; or nil. find_non_colliding_position kept a half-tile offset for a 2x2 furnace: the game refused it every
-- time, and the engineer asked again at the same spot twenty times (death-world-waves-power, W-25).
function Hands.free_spot(npc, surface, name, near, radius, force, tick)
  if not near then return nil end
  local proto = prototypes and prototypes.entity and prototypes.entity[name]
  if not (proto and surface.can_place_entity) then   -- (no prototype facts: the game's own finder, as before)
    return surface.find_non_colliding_position(name, near, radius or 8, 1)
  end
  local w, h = proto.tile_width or 1, proto.tile_height or 1
  local cx, cy = snap(near.x or near[1], w), snap(near.y or near[2], h)
  tick = tick or (game and game.tick) or 0
  for r = 0, math.floor(radius or 8) do
    for dy = -r, r do
      for dx = -r, r do
        if math.max(math.abs(dx), math.abs(dy)) == r then
          local q = {x=cx + dx, y=cy + dy}
          if not (npc and was_refused(npc, name, q, tick)) and surface.can_place_entity{name=name, position=q,
              force=force, build_check_type=defines.build_check_type.manual} then
            return q
          end
        end
      end
    end
  end
  return nil
end

-- A spot in danger (W-30): biters this close, a nest right beside it, a worm within what it reaches and a little more.
-- Judged for every placement it walks to, by the worker itself: a run's first piece was checked and its later ones not,
-- and the engineer died at minute 1 on a site inside a nest cluster's reach (Offense's late-combat runs).
Hands.DANGER = 32
Hands.NEST_NEAR = 12
Hands.WORM_MARGIN = 4
function Hands.in_danger(npc, position)
  local c = npc.character
  if not (c and c.valid and position) then return false end
  local ok, hit = pcall(function()
    local s = c.surface
    if s.count_entities_filtered{position=position, radius=Hands.DANGER, force="enemy", type="unit", limit=1} > 0 then
      return true
    end
    if s.count_entities_filtered{position=position, radius=Hands.NEST_NEAR, force="enemy", type="unit-spawner",
        limit=1} > 0 then return true end
    for _, w in ipairs(s.find_entities_filtered{position=position, radius=64, force="enemy", type="turret"}) do
      local range = w.prototype.attack_parameters and w.prototype.attack_parameters.range or Hands.DANGER
      local dx, dy = w.position.x - position.x, w.position.y - position.y
      if dx * dx + dy * dy <= (range + Hands.WORM_MARGIN) ^ 2 then return true end
    end
    return false
  end)
  return ok and hit == true
end

-- Looked at once a second per spot at most (a run's placements every tick otherwise): `memo` a table kept with the job.
function Hands.danger_at(npc, memo, key, position, tick)
  local m = memo[key]
  if m and tick - m.tick < 60 then return m.hit end
  local hit = Hands.in_danger(npc, position)
  memo[key] = {tick=tick, hit=hit}
  return hit
end

-- Picking up its own buildings (lesson 17, the user's play 2026-10-30: 130 pickups brought a creep's turrets and their
-- ammo back): one tick of a pickup job, each of `job.targets` taken where it stands from the reach it builds and mines
-- buildings at (far reach counts), walked to only when out of it, what it held coming back with it, as a player's
-- mining does. A spot in danger is passed over, unless what stands there fights (`brave`: turrets, walls; W-30's rule).
-- Returns "walk", "picked", "complete", or a failure ("inventory_full") with how many it picked up in job.picked.
Hands.MAX_PICKUP = 64
function Hands.pickup_step(npc, job, tick, navigate, brave)
  local c = npc.character
  job.picked = job.picked or 0
  job.danger = job.danger or {}
  local targets = job.targets or {}
  local i = job.at or 1
  while targets[i] and not (targets[i].valid and targets[i].surface == c.surface and targets[i].force == c.force) do
    i = i + 1
  end
  job.at = i
  local e = targets[i]
  if not e then return "complete" end
  if not brave(e.name) and Hands.danger_at(npc, job.danger, i, e.position, tick) then
    job.at = i + 1   -- (biters by it: left standing)
    job.passed = (job.passed or 0) + 1
    return "walk"
  end
  if not c.can_reach_entity(e) then
    if navigate(e.position, math.max(0.3, (c.reach_distance or 10) - 0.75)) then
      job.at = i + 1   -- (there and still out of reach: left standing)
      job.passed = (job.passed or 0) + 1
    end
    return "walk"
  end
  local inv = c.get_main_inventory()
  local ok = pcall(function() return e.mine{inventory=inv} end)
  if e.valid then   -- (its pockets could not take it, or what it held)
    if not ok then job.at = i + 1; job.passed = (job.passed or 0) + 1; return "walk" end
    return "inventory_full"
  end
  job.picked = job.picked + 1
  job.at = i + 1
  return "picked"
end

return Hands
