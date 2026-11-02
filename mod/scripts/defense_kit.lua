-- The engineer's own defense kit, kept up wherever biters are about: the best gun and armor it can make, magazines,
-- and a couple of gun turrets to put down at its feet, loaded, when a group comes for it (combat.lua, combat_turrets),
-- picked up again once the fight is over to go where they were meant to (the user, 2026-10-26). In run 52 an engineer
-- with only its starting pistol, 13 magazines and no turret walked out to put up turrets against 18 small biters, and
-- died: a submachine gun and light armor were researched, but nothing ever made it arm up against biters.
-- Got as anything else it needs is (DesignedStart's acquire: from its chests and machines, else crafted in the
-- background while it works); worn and taken up as soon as it has them (Combat.equip_now).
local Recipes = require("scripts.recipes")
local TeamWorld = require("scripts.team_world")
local Combat = require("scripts.combat")
local Agent = require("scripts.agent")
local TeamWar = require("scripts.team_war")

local Kit = {}

Kit.TURRETS = 2        -- turrets carried to put down at its feet, at least
Kit.MOST = 6           -- and at most (the user put six down at their first fight, 2026-10-28)
Kit.HOLDS = 5          -- small biters one loaded turret holds off (war.lua TURRET_HOLDS)
Kit.FILL = 10          -- magazines a turret is loaded with (combat_turrets FILL)
Kit.GUN = 10           -- and its own gun keeps (combat_turrets KEEP)
Kit.AMMO = 30          -- magazines for the least kit: ten for each turret, ten for its own gun
Kit.CHECK_EVERY = 600  -- ticks between looks while it has the kit (or cannot get the rest yet)
Kit.PACK_CLEAR = 24    -- nothing hostile this near a turret it put down: it is picked up
Kit.CALM = 600         -- ticks after its last fight before it goes to pick them up
Kit.PACK_TRIES = 3     -- a turret it could not get to this many times is left where it stands
Kit.NEAR = 32          -- out at far work, kit work only this near where it stands (else it waits to be home)
local GUNS = {"submachine-gun", "pistol"}       -- best first
local ARMORS = {"heavy-armor", "light-armor"}   -- (modular armor wants equipment in it: arm_up's)
local MAGAZINES = {"uranium-rounds-magazine", "piercing-rounds-magazine", "firearm-magazine"}

local function owned(c, name)
  local n = 0
  for _, inv in ipairs({defines.inventory.character_main, defines.inventory.character_guns,
      defines.inventory.character_ammo, defines.inventory.character_armor}) do
    local i = c.get_inventory(inv)
    if i then n = n + i.get_item_count({name = name, quality = "normal"}) end
  end
  return n
end
Kit.owned = owned

-- The kit to keep, sized to the waves its plot has seen (D-14): the biggest lately (its units weighed as war.lua
-- does: a small biter 1, a big one 3.75), grown as they grew from the one before (at most twice), one turret for
-- every HOLDS of it, between TURRETS and MOST; magazines to load them all and its gun's. {turrets, magazines}.
local SIZE = {small = 1, medium = 1, big = 3.75, behemoth = 8, leviathan = 8}
local function wave_size(a)
  local n = 0
  for name, count in pairs(a.units or {}) do
    local w = 1
    for word, s in pairs(SIZE) do if tostring(name):find(word, 1, true) then w = math.max(w, s) end end
    n = n + w * count
  end
  return n > 0 and n or (a.count or 0)
end
-- (the waves on any teammate's plot lately count too: the first big one on its own plot finds it ready; Codex)
function Kit.size(npc)
  local turrets = Kit.TURRETS
  if TeamWorld.active() and npc.character and npc.character.valid then
    local wave = 0
    for _, other in pairs(storage.npcs or {}) do
      if other.character and other.character.valid then
        local ok, board = pcall(TeamWar.threat_board, other, nil, other.character.surface)
        local attacks = ok and board and board.attacks or {}   -- (newest first)
        for _, a in ipairs(attacks) do wave = math.max(wave, wave_size(a)) end
        local last, before = attacks[1] and wave_size(attacks[1]) or 0, attacks[2] and wave_size(attacks[2]) or 0
        if before > 0 then wave = math.max(wave, last * math.min(2, math.max(1, last / before))) end
      end
    end
    turrets = math.max(Kit.TURRETS, math.min(Kit.MOST, math.ceil(wave / Kit.HOLDS)))
  end
  return turrets, math.max(Kit.AMMO, turrets * Kit.FILL + Kit.GUN)
end

-- Whether it keeps the kit: biters are about (the team world, a fight it has been in, its base hit), and it has
-- turned to defense (gun turrets researched): before that the opening's iron goes to the opening.
function Kit.wanted(npc)
  if not Recipes.ready(npc.force, "gun-turret") then return false end
  if TeamWorld.active() then return true end
  return (npc.combat and (npc.combat.entries or 0) > 0) or npc.alarm ~= nil
end

-- What its plan under way still needs: the pieces not built yet, and what it loads them with.
local function plan_needs(npc)
  local d, out = npc.design, {}
  if not (d and d.status == "building" and d.plan) then return out end
  for _, id in ipairs(d.plan.order or {}) do
    local en = d.plan.entities and d.plan.entities[id]
    if en and en.item and not (en.entity and en.entity.valid) then out[en.item] = true end
  end
  for _, stage in ipairs(d.plan.stages or {}) do
    for _, s in ipairs(stage.supplies or stage.manual_supplies or {}) do if s.item then out[s.item] = true end end
  end
  return out
end
Kit.plan_needs = plan_needs

-- What it lacks, best first: {item, how many to hold}. A gun or armor only where it has none as good; magazines
-- by the plainest kind (the better ones take steel its machines may not make yet), counting every kind it holds.
-- The base's own defense comes first, of the same scarce plates: while a plan of its own still needs turrets, the kit
-- takes none (D-11: in a death world its kit's pair, refilled as fast as its plan used them, kept a two-turret plan
-- building for 35 minutes and the base unguarded). Its magazines it keeps: they are what it reloads turrets from in a
-- fight (held to its own gun's while a plan wanted ammunition, its turrets stood empty three minutes and fell).
function Kit.wants(npc)
  local c = npc.character
  local needs = plan_needs(npc)
  local function ready(name) return Recipes.ready(npc.force, name) end
  local out = {}
  local function best(list)
    for _, name in ipairs(list) do
      if owned(c, name) > 0 then return end
      if ready(name) then out[#out + 1] = {name, 1}; return end
    end
  end
  best(GUNS)
  best(ARMORS)
  local turrets, magazines = Kit.size(npc)
  if needs["firearm-magazine"] or needs["gun-turret"] then magazines = Kit.AMMO end   -- (its plan's first: Codex)
  if ready("firearm-magazine") then
    local held = 0
    for _, name in ipairs(MAGAZINES) do held = held + owned(c, name) end
    if held < magazines then out[#out + 1] = {"firearm-magazine", owned(c, "firearm-magazine") + magazines - held} end
  end
  if ready("gun-turret") and not needs["gun-turret"] and owned(c, "gun-turret") < turrets then
    out[#out + 1] = {"gun-turret", turrets}
  end
  return out
end

local function foes_near(c, position, radius)
  for _, e in ipairs(c.surface.find_entities_filtered{type = {"unit", "character"}, position = position,
      radius = radius}) do
    if e.valid and e.force ~= c.force and not c.force.get_friend(e.force) and not c.force.get_cease_fire(e.force) then
      return true
    end
  end
  return false
end

-- A turret it put down in a fight, picked up again once the fight is over and nothing hostile is near it (with
-- `away`, out at far work: only one right by it, the rest when it is home).
function Kit.pickup(npc, tick, away)
  local st = npc.combat_turrets
  local dropped = st and st.dropped
  if not (dropped and #dropped > 0) then return nil end
  local fight = npc.combat
  if fight and fight.active then return nil end
  if tick - (fight and fight.finished_tick or 0) < Kit.CALM then return nil end
  local c = npc.character
  st.pack_tries = st.pack_tries or {}
  for i = #dropped, 1, -1 do
    local t = dropped[i]
    local tries = t.valid and st.pack_tries[t.unit_number or 0] or 0
    local owned = t.valid and Agent.designed and Agent.designed.used_by_a_plan
      and Agent.designed.used_by_a_plan(npc, t)   -- (a plan took it over where it stood: it stays, the plan's)
    if not t.valid or not t.minable or tries >= Kit.PACK_TRIES or owned then
      if t.valid and t.unit_number then st.pack_tries[t.unit_number] = nil end
      table.remove(dropped, i)
    elseif away and (t.position.x - c.position.x)^2 + (t.position.y - c.position.y)^2 > Kit.NEAR^2 then
      -- (left standing until it is home: no try counted)
    elseif not foes_near(c, t.position, Kit.PACK_CLEAR) then
      st.pack_tries[t.unit_number or 0] = tries + 1
      return {kind = "mine", target = t, count = 1, mode = "deconstruct"}
    end
  end
  return nil
end

-- Whether a job keeps it where it stands: made from what it carries, at a store right by it, or mining what lies by it.
local function at_hand(c, job)
  if job.kind == "craft" then return true end
  local p = job.target and job.target.valid and job.target.position or job.position
  if not p and job.kind == "mine" and job.item then   -- (the nearest of it, found when the job starts)
    local what = job.item == "wood" and {type = "tree"} or {name = job.item}
    what.position, what.radius, what.limit = c.position, Kit.NEAR, 1
    return c.surface.count_entities_filtered(what) > 0
  end
  return p ~= nil and (p.x - c.position.x)^2 + (p.y - c.position.y)^2 <= Kit.NEAR^2
end

-- One job toward the kit, or toward picking up what it put down in a fight, with its meta; else nil and, while part
-- of the kit is still being crafted for it, "crafting" (it comes soon: worth waiting for before walking into an
-- attack). `acquire`: DesignedStart's (one job toward holding so many of an item, or why it must wait). `now`: look
-- even if it looked lately (before it walks back to an attack on its base), but not straight after a failed fetch.
function Kit.next(npc, tick, acquire, now)
  local c = npc.character
  if not (c and c.valid and acquire) or not Kit.wanted(npc) then return nil end
  -- Out at far work, kit work that would take it off waits for it to be home (the user's live game, replayed
  -- 2026-10-26: 79 iron plates fetched from home as a far build started, a 90 s trip), unless it is about to walk
  -- back to an attack on its base (`now`).
  local away = not now and Agent.designed and Agent.designed.away and Agent.designed.away(npc)
  local job = Kit.pickup(npc, tick, away)
  if job then return job, {upkeep = "kit_pickup"} end
  if tick < (npc.kit_failed_until or 0) or not now and tick < (npc.kit_next or 0) then return nil end
  local coming
  for _, want in ipairs(Kit.wants(npc)) do
    local got, why = acquire(npc, want[1], want[2], 0)
    if got and away and not at_hand(c, got) then got = nil end
    if got then return got, {upkeep = "kit", item = want[1]} end
    if type(why) == "string" and why:find("^crafting") then coming = true end
  end
  pcall(Combat.equip_now, npc)   -- (what it has, worn and in hand now, not only once a fight starts)
  if coming then return nil, "crafting" end
  npc.kit_next = tick + Kit.CHECK_EVERY
  return nil
end

Agent.kit = Kit   -- (agent.lua: armed before it walks back to an attack on its base)

return Kit
