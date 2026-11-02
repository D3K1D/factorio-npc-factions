-- Turrets it carries, put down in a fight the way players do it (against a player or another engineer, or a group of
-- biters coming for it: Combat.drops_turrets): dropped within reach between it and them and filled with magazines
-- from its own pockets, one about every half second, while it fights on. Only what it carries, never out of reach;
-- picked up again once the fight is over (defense_kit.lua).
local Turrets = {}
local Hands = require("scripts.hands")

local GAP = 30          -- ticks between one turret and the next
local NEAR = 14         -- its own turrets this close are cover already
local ENOUGH = 3        -- this many loaded around it is enough against people (or what it cannot weigh)
local HOLDS = 5         -- small biters one loaded turret holds off (war.lua TURRET_HOLDS)
local MOST = 8          -- turrets it puts down in one fight at most
local FILL = 10         -- magazines into each
local LOW = 5           -- a turret with fewer is topped up
local KEEP = 10         -- magazines kept back for its own gun (counting those already in it)
local AMMO = {"uranium-rounds-magazine", "piercing-rounds-magazine", "firearm-magazine"}

local function distance(a, b)
  local dx, dy = a.x - b.x, a.y - b.y
  return math.sqrt(dx * dx + dy * dy)
end

-- Against people always; against biters, only a group of them (Combat.drops_turrets).
function Turrets.wanted(target)
  return target ~= nil and target.valid and target.force ~= nil and target.force.name ~= "enemy"
end

-- Where the k-th goes: three tiles toward them, on the line and then to either side of it.
function Turrets.spot(from, to, k)
  local dx, dy = to.x - from.x, to.y - from.y
  local d = math.max(1, math.sqrt(dx * dx + dy * dy))
  local ux, uy = dx / d, dy / d
  local side = ({0, 3, -3, 6, -6})[((k - 1) % 5) + 1]
  return {x = from.x + ux * 3 - uy * side, y = from.y + uy * 3 + ux * side}
end

-- The magazines it can spare for a turret: which kind, and how many. Those in its gun's ammo slot count too (taking up
-- a gun moves the whole stack of magazines in there: counted from its pockets alone, it had none to spare and put no
-- turret down), less KEEP for the gun.
local function spare(c)
  local main = c.get_main_inventory()
  local slot = c.get_inventory(defines.inventory.character_ammo)
  for _, name in ipairs(AMMO) do
    local loose = main and main.get_item_count(name) or 0
    local loaded = slot and slot.get_item_count(name) or 0
    local n = loose + loaded - KEEP
    if n > 0 then return name, n end
  end
end

local function fill(c, turret, name, n)
  if not name or n <= 0 then return 0 end
  local put = turret.insert{name = name, count = math.min(FILL, n)}
  local left = put
  for _, inv in ipairs({c.get_main_inventory(), c.get_inventory(defines.inventory.character_ammo)}) do
    if left > 0 and inv then left = left - inv.remove{name = name, count = left} end
  end
  return put
end

-- How many loaded turrets round it are enough against what comes (`strength`: small biters, combat.lua's scan):
-- one for every HOLDS of them (at most MOST), never fewer than ENOUGH; ENOUGH against people. (D-14: the user put six
-- down at their first fight, against a group a fixed three would not have held.)
-- What comes for it: the biters round it (spawners and worms do not walk up), weighed as combat.lua's scan does.
function Turrets.biters_near(c)
  local n = 0
  for _, e in ipairs(c.surface.find_entities_filtered{type = "unit", force = "enemy", position = c.position,
      radius = 30}) do
    if e.valid then n = n + math.max(1, math.min(8, (e.max_health or 100) / 100)) end
  end
  return n
end

function Turrets.enough(target, strength)
  if Turrets.wanted(target) or not strength or strength <= 0 then return ENOUGH end
  return math.max(ENOUGH, math.min(MOST, math.ceil(strength / HOLDS)))
end

-- At most one thing a call, when one is due: top up a turret of its own nearby that runs low, or
-- put down the next. place(spec) puts an item down as a player would (Worker.place_one); `strength`: what comes.
function Turrets.tick(npc, target, tick, place, strength)
  local c = npc.character
  local st = npc.combat_turrets or {placed = 0, filled = 0}
  npc.combat_turrets = st
  if tick < (st.next or 0) then return false end
  local ammo, n = spare(c)
  local loaded = 0
  for _, t in ipairs(c.surface.find_entities_filtered{type = "ammo-turret", force = c.force, position = c.position,
      radius = NEAR}) do
    local inside = t.get_inventory(defines.inventory.turret_ammo)
    local count = inside and inside.get_item_count() or 0
    if count > 0 then loaded = loaded + 1 end
    if count < LOW and ammo and distance(c.position, t.position) <= c.reach_distance then
      local put = fill(c, t, ammo, n)
      if put > 0 then
        st.filled, st.next = st.filled + put, tick + GAP / 2
        return true
      end
    end
  end
  local main = c.get_main_inventory()
  st.next = tick + GAP / 2   -- looked: nothing to do for a moment
  if not Turrets.wanted(target) then strength = Turrets.biters_near(c) end   -- (biters only: Codex, D-14-kit-size)
  if loaded >= Turrets.enough(target, strength) or not ammo or not main or main.get_item_count("gun-turret") < 1 then
    return false
  end
  st.next = tick + GAP
  for k = 1, 5 do
    local want = Turrets.spot(c.position, target.position, st.placed + k)
    local at = Hands.free_spot(npc, c.surface, "gun-turret", want, 3, c.force, tick)   -- (on its grid: W-25)
    if at and distance(c.position, at) <= c.build_distance - 1 then
      local turret = place({entity_name = "gun-turret", item = "gun-turret", position = at,
        direction = defines.direction.north})
      if turret then
        st.placed = st.placed + 1
        st.dropped = st.dropped or {}   -- picked up again when it is called home (agent.lua)
        st.dropped[#st.dropped + 1] = turret
        while #st.dropped > 8 do table.remove(st.dropped, 1) end
        st.filled = st.filled + fill(c, turret, ammo, n)
        return true
      end
    end
  end
  return false
end

return Turrets
