-- Mending (W-33): what biters hurt and left standing gets its health back from the engineer's repair packs, as a
-- player walks the wall line after a wave. Its turrets first (its firepower), then its walls and gates, then the rest;
-- one 32-tile patch at a time, the worst first. Only between its building (no stage going up, nothing urgent), never
-- where biters are, never where its own robots mend (a network holding packs and robots covers it).
local Hands = require("scripts.hands")
local Logistics = require("scripts.logistics")

local Mend = {}

local PACK = "repair-pack"
Mend.PACK = PACK
Mend.CELL = 32          -- tiles: one patch mended at a time
Mend.MAX_TARGETS = 32   -- per job
Mend.MAX_HURT = 512     -- remembered per force
Mend.LOOK_EVERY = 300   -- ticks between looks for something to mend
Mend.BACK_OFF = 3600    -- ticks after a look that found packs it could not get
Mend.CRAFT_BATCH = 5    -- packs made by hand when none are at hand
Mend.MEND_FOR = 3600    -- ticks one job mends at most before it lets its engineer look again
Mend.HP_PER_PACK = 300  -- (a pack's durability; drained one per health point, as drive.lua mends its car)

local NPC_FORCE = "^npc%-faction%-%d+$"
-- (what moves is not a building it walks to: a car, a wagon, a robot; Codex)
local MOBILE = {car=true, ["spider-vehicle"]=true, locomotive=true, ["cargo-wagon"]=true, ["fluid-wagon"]=true,
  ["artillery-wagon"]=true, ["construction-robot"]=true, ["logistic-robot"]=true, ["combat-robot"]=true, unit=true,
  character=true}
Mend.ATTACK_CALM = 3600   -- (as DesignedStart.ATTACK_CALM: a minute without a hit and the attack is over)
local CLASS = {["ammo-turret"]=3, ["electric-turret"]=3, ["fluid-turret"]=3, ["artillery-turret"]=3,
  wall=2, gate=2}

local function try(f, ...) local ok, v = pcall(f, ...) if ok then return v end end

local function state()
  storage.mend = storage.mend or {hurt={}, n={}}
  return storage.mend
end

-- Every hit on an NPC's building that leaves it standing: remembered by its force till it is whole again.
function Mend.on_entity_damaged(event)
  local e = event.entity
  if not (e and e.valid) or (event.final_health or 1) <= 0 then return end
  local force = e.force and e.force.name
  if not (force and force:match(NPC_FORCE)) or MOBILE[e.type] or not e.unit_number then return end
  local s = state()
  local list = s.hurt[force] or {}
  s.hurt[force] = list
  if list[e.unit_number] then return end
  if (s.n[force] or 0) >= Mend.MAX_HURT then return end
  list[e.unit_number] = e
  s.n[force] = (s.n[force] or 0) + 1
end

local function forget(force, id)
  local s = state()
  local list = s.hurt[force]
  if list and list[id] then list[id] = nil; s.n[force] = math.max(0, (s.n[force] or 1) - 1) end
end

function Mend.class(e) return CLASS[e.type] or 1 end

local function missing(e)
  return math.max(0, (try(function() return e.max_health end) or 0) - (try(function() return e.health end) or 0))
end

-- Its own robots mend it: a network of its own covering it holds packs and construction robots (NPC Ledger's L-5,
-- Logistics.can_repair; until that is in, the networks looked at here).
function Mend.robots_mend(npc, pos, Robots)
  if Logistics and Logistics.can_repair then
    local n = try(Logistics.can_repair, npc, pos)
    return n == true or (tonumber(n) or 0) > 0
  end
  for _, net in ipairs(Robots and Robots.networks(npc, pos) or {}) do
    if (try(function() return net.all_construction_robots end) or 0) > 0
        and (try(function() return net.get_item_count(PACK) end) or 0) >= 1 then return true end
  end
  return false
end

-- The patch to mend now: up to MAX_TARGETS of its hurt buildings in one 32-tile cell, turrets first, then the worst
-- hurt; the cell holding its most important hurt (turrets over walls over the rest), then the most health missing.
-- `reach`: tiles from home it mends within. Returns targets and the health they miss, or nil.
function Mend.patch(npc, reach, Robots)
  local c = npc.character
  if not (c and c.valid) then return nil end
  local force = npc.force and npc.force.name or c.force.name
  local list = state().hurt[force]
  if not list then return nil end
  local home = npc.home or c.position
  local cells = {}
  for id, e in pairs(list) do
    local lost = e.valid and missing(e) or 0
    if lost <= 0.5 then
      forget(force, id)
    else
      local p = e.position
      -- (what its robots mend left to them, each where it stands, before the patches are ranked; Codex)
      if e.surface == c.surface and (p.x - home.x) ^ 2 + (p.y - home.y) ^ 2 <= reach * reach
          and not Mend.robots_mend(npc, p, Robots) then
        local key = math.floor(p.x / Mend.CELL) .. ":" .. math.floor(p.y / Mend.CELL)
        local cell = cells[key]
        if not cell then
          local mid = {x=(math.floor(p.x / Mend.CELL) + 0.5) * Mend.CELL, y=(math.floor(p.y / Mend.CELL) + 0.5) * Mend.CELL}
          cell = {key=key, mid=mid, rank=0, lost=0, list={}}
          cells[key] = cell
        end
        local cls = Mend.class(e)
        cell.list[#cell.list + 1] = {e=e, cls=cls, lost=lost, share=lost / math.max(1, e.max_health)}
        cell.rank = math.max(cell.rank, cls)
        cell.lost = cell.lost + lost
      end
    end
  end
  local order = {}
  for _, cell in pairs(cells) do order[#order + 1] = cell end
  table.sort(order, function(a, b)
    if a.rank ~= b.rank then return a.rank > b.rank end
    if a.lost ~= b.lost then return a.lost > b.lost end
    return a.key < b.key
  end)
  for _, cell in ipairs(order) do
    if not Hands.in_danger(npc, cell.mid) then   -- (biters about the patch: the next one)
      table.sort(cell.list, function(a, b)
        if a.cls ~= b.cls then return a.cls > b.cls end
        if a.share ~= b.share then return a.share > b.share end
        return a.e.unit_number < b.e.unit_number
      end)
      local targets, lost = {}, 0
      for i = 1, math.min(#cell.list, Mend.MAX_TARGETS) do
        targets[i] = cell.list[i].e
        lost = lost + cell.list[i].lost
      end
      return targets, lost
    end
  end
  return nil
end

-- Packs a patch missing `lost` health wants, at least one.
function Mend.packs_for(lost) return math.max(1, math.min(20, math.ceil(lost / Mend.HP_PER_PACK))) end

-- What it would rather do than mend, as the worker sees it: called home, helping, heading home, a fight; an order
-- of its agenda for now; a stage of its plan going up.
function Mend.yields(npc)
  if npc.recall or npc.helping or npc.homeward or npc.combat_objective then return true end
  if npc.alarm and (game.tick - (npc.alarm.last or 0)) <= Mend.ATTACK_CALM then return true end   -- (Codex)
  for _, it in ipairs(npc.agent and npc.agent.agenda or {}) do
    if it.priority == "now" then return true end
  end
  return npc.design ~= nil and npc.design.status == "building"
end

local function rate(e)
  local speed, mod = 2, 1
  pcall(function() speed = prototypes.item[PACK].speed or speed end)
  pcall(function() mod = e.prototype.repair_speed_modifier or mod end)
  return speed * mod
end

local function pack(c)
  local inv = c.get_main_inventory()
  local stack = inv and inv.find_item_stack and inv.find_item_stack(PACK)
  return stack and stack.valid_for_read and stack or nil
end

-- One tick of a mend job: "walk" toward a target, "mend" a little, or a finished outcome ("complete", "yielded",
-- "no_packs", "in_danger") with the target it was at. `navigate(position, radius)` walks it (true once there).
function Mend.step(npc, job, tick, navigate)
  local c = npc.character
  job.started = job.started or tick
  job.danger = job.danger or {}
  if Mend.yields(npc) then return "yielded" end
  if tick - job.started > Mend.MEND_FOR then return "complete" end
  local targets = job.targets or {}
  local i = job.at or 1
  while targets[i] and not (targets[i].valid and targets[i].surface == c.surface and missing(targets[i]) > 0.5) do
    if targets[i].valid then forget(targets[i].force.name, targets[i].unit_number) end
    i = i + 1
  end
  job.at = i
  local e = targets[i]
  if not e then return "complete" end
  if Hands.danger_at(npc, job.danger, e.unit_number, e.position, tick) then return "in_danger", e end
  local stack = pack(c)
  if not stack then return "no_packs", e end
  if not c.can_reach_entity(e) then
    -- (there and still out of reach, a wall behind something: passed over, not walked at for the job's whole time;
    -- Codex)
    if navigate(e.position, math.max(0.3, (c.reach_distance or 10) - 0.75)) then
      forget(e.force.name, e.unit_number)
      job.at = i + 1
    end
    return "walk", e
  end
  local heal = math.min(rate(e), missing(e))
  e.health = e.health + heal
  stack.drain_durability(heal)
  job.mended = (job.mended or 0) + heal
  -- (what it mended by kind, and the kind it began with: for whoever reads how it mends, the proving ground)
  local kind = ({"other", "wall", "turret"})[Mend.class(e)]
  job.by_kind = job.by_kind or {}
  job.by_kind[kind] = (job.by_kind[kind] or 0) + heal
  job.first_kind = job.first_kind or kind
  return "mend", e
end

return Mend
