-- The war side of an engineer's mind, between its reflexes in a fight (combat.lua) and its strategist:
-- what is happening to its home, and whether that calls it back. Any hit on its buildings at home
-- raises an alarm (biters too, which no "attacked" event reports); the alarm lasts while the hits go
-- on and the attack is over once it has been quiet a while. Whether the base holds without it is
-- judged from its loaded turrets near where it is hit against what is hitting it.
local TeamWorld = require("scripts.team_world")
local War = {}

local NPC_FORCE = "^npc%-faction%-(%d+)$"
local BASE_RADIUS = 300    -- its buildings this close to home are home (far outposts are not, yet)
local OVER_AFTER = 3600    -- a minute without a hit: the attack is over
local HOLD_RADIUS = 40     -- the turrets and attackers round the spot that is hit
local TURRET_HOLDS = 5     -- one loaded gun turret holds off this much (a small biter is 1, a big one 3.75)
local RECALL_DIST = 48     -- farther than this from the spot that is hit, it is away
local FAR, MINOR = 400, 3  -- this far away, fewer attackers than this (in small biters) can wait
local DEFENSE_TYPES = {"ammo-turret", "electric-turret", "fluid-turret"}
local ATTACKER_TYPES = {"unit", "character", "car", "combat-robot", "spider-vehicle"}

local function npc_of(force)
  local id = force and force.valid ~= false and tonumber((force.name or ""):match(NPC_FORCE))
  local npc = id and storage.npcs and storage.npcs[id]
  return npc
end

local function hostile(force, other)
  if not other or other == force or other.name == "neutral" then return false end
  return not force.get_friend(other) and not other.get_friend(force)
    and not force.get_cease_fire(other) and not other.get_cease_fire(force)
end

-- Something of its own at home (or at a base it took over) was hit (or destroyed) by `by` (a force, or
-- nil when unknown).
local OUTPOST_RADIUS = 120
local function at_home(npc, position)
  local h = npc.home
  if h and (position.x - h.x) ^ 2 + (position.y - h.y) ^ 2 <= BASE_RADIUS * BASE_RADIUS then return true end
  for _, o in ipairs(npc.outposts or {}) do
    if (position.x - o.x) ^ 2 + (position.y - o.y) ^ 2 <= OUTPOST_RADIUS * OUTPOST_RADIUS then return true end
  end
  return false
end
function War.alarm(npc, position, by, amount, destroyed, tick)
  if not at_home(npc, position) then return false end
  local a = npc.alarm
  if not a or tick - a.last > OVER_AFTER then
    a = {first = tick, hits = 0, damage = 0, lost = 0, by = {}}
    npc.alarm = a
  end
  a.last, a.position = tick, {x = position.x, y = position.y}
  npc.last_alarm = tick
  a.hits = a.hits + 1
  a.damage = a.damage + math.max(0, amount or 0)
  if destroyed then a.lost = a.lost + 1 end
  local who = by and by.name or "unknown"
  a.by[who] = true
  if who ~= "enemy" and who ~= "unknown" and who ~= "neutral" then a.people = true end   -- an engineer or a player
  return true
end

-- The team world: the players' things (and the players) hit by biters raise an alarm of theirs too, so
-- teammates come to help them as they help each other. One alarm per player (whoever built it).
local function player_hit(e, by, amount, destroyed, tick)
  if not (TeamWorld.active() and by and by.name == "enemy" and e.force == game.forces.player) then return end
  local player = e.type == "character" and e.player or e.last_user
  local index = player and player.index or 1
  storage.war_players = storage.war_players or {}
  local a = storage.war_players[index]
  if not a or tick - a.last > OVER_AFTER then
    a = {first = tick, hits = 0, damage = 0, lost = 0, by = {}}
    storage.war_players[index] = a
  end
  a.last, a.position = tick, {x = e.position.x, y = e.position.y}
  a.hits, a.damage = a.hits + 1, a.damage + math.max(0, amount or 0)
  if destroyed and e.type ~= "character" then a.lost = a.lost + 1 end
  a.by.enemy = true
end

local function hit(event, destroyed)
  local e = event.entity
  if not (e and e.valid) then return end
  player_hit(e, event.force, event.final_damage_amount, destroyed, event.tick or game.tick)
  if e.type == "character" then return end
  local npc = npc_of(e.force)
  if not npc then return end
  local by = event.force
  if by and (by == e.force or e.force.get_friend(by)) then return end   -- its own fire, or a friend's
  War.alarm(npc, e.position, by, event.final_damage_amount, destroyed and e.name, event.tick or game.tick)
end
function War.on_entity_damaged(event) hit(event, false) end
function War.on_entity_died(event) hit(event, true) end

-- Whether the base holds where it is hit, without it: its loaded turrets there against the
-- attackers there. Returns holds, turrets, strength.
local function supplied(t)
  if t.type == "ammo-turret" then
    local inv = t.get_inventory(defines.inventory.turret_ammo)
    return inv ~= nil and not inv.is_empty()
  elseif t.type == "electric-turret" then
    return (t.energy or 0) > 0
  end
  return true
end
local function holding_at(force, surface, a)
  local turrets = 0
  for _, t in ipairs(surface.find_entities_filtered{force = force, type = DEFENSE_TYPES,
      position = a.position, radius = HOLD_RADIUS}) do
    if t.valid and supplied(t) then turrets = turrets + 1 end
  end
  local strength = 0
  for _, e in ipairs(surface.find_entities_filtered{type = ATTACKER_TYPES, position = a.position,
      radius = HOLD_RADIUS}) do
    if e.valid and hostile(force, e.force) then
      strength = strength + math.max(1, math.min(8, (e.max_health or 100) / 100))
    end
  end
  return turrets * TURRET_HOLDS >= strength, turrets, strength
end
function War.holding(npc, a) return holding_at(npc.force, npc.character.surface, a) end

-- Whether the attack on its home calls it back: an engineer or a player at its buildings, buildings
-- lost, or its turrets there not holding. Only when it is away from the spot, and not fighting for
-- its own life first.
function War.calls_home(npc, tick)
  local a = npc.alarm
  local c = npc.character
  if not (a and c and c.valid) or npc.recall then return nil end
  if (c.position.x - a.position.x) ^ 2 + (c.position.y - a.position.y) ^ 2 <= RECALL_DIST * RECALL_DIST then
    return nil   -- there already: its combat fights them
  end
  if npc.combat and npc.combat.active then return nil end   -- itself first
  if a.people then return "attacked by an engineer or a player" end
  if a.lost > 0 then return "buildings lost" end
  local holds, turrets, strength = War.holding(npc, a)
  a.turrets, a.strength = turrets, strength
  -- (biters camped at its buildings, O-6: turrets near them that do not shoot them hold nothing off)
  if holds and not (a.lingering and tick - a.lingering <= OVER_AFTER) then return nil end
  -- A few biters at an undefended building are not worth a trip back across the map: only once they
  -- destroy something (above), or when it is near enough to come quickly.
  local far = (c.position.x - a.position.x) ^ 2 + (c.position.y - a.position.y) ^ 2 > FAR * FAR
  if far and strength < MINOR then return nil end
  return turrets == 0 and "undefended" or "turrets not holding"
end

-- Every second: the alarm lapses once the attack is over; a reason when it should go home now.
function War.tick(npc, tick)
  local a = npc.alarm
  if a and tick - a.last > OVER_AFTER then npc.alarm = nil end
  return War.calls_home(npc, tick)
end

-- Whether the attack it was called back for is over.
function War.over(npc, tick)
  local a = npc.alarm
  return not a or tick - a.last > OVER_AFTER
end

-- Its own home can do without it a while: loaded turrets there, and no attack on it lately.
local HOME_QUIET, HOME_GUARDS, HOME_GUARD_RADIUS = 36000, 2, 80
function War.home_holds(npc, tick)
  if not npc.home or (npc.alarm and tick - npc.alarm.last <= OVER_AFTER)
      or tick - (npc.last_alarm or -HOME_QUIET) < HOME_QUIET then
    return false
  end
  local n = 0
  for _, t in ipairs(npc.character.surface.find_entities_filtered{force = npc.force, type = DEFENSE_TYPES,
      position = npc.home, radius = HOME_GUARD_RADIUS}) do
    if t.valid and supplied(t) then n = n + 1 end
  end
  return n >= HOME_GUARDS
end

-- Whoever is attacking there it can fight: biters, or people it is at war with already (it does not
-- go to war for a friend on its own; its strategist may).
local function can_fight(npc, a)
  for who in pairs(a.by) do
    if who ~= "enemy" and who ~= "unknown" and who ~= "neutral" then
      local f = game.forces[who]
      if not (f and hostile(npc.force, f)) then return false end
    end
  end
  return true
end

-- A friend (an engineer) whose home is under attack and needs help, that this engineer could go and
-- help: its own home first (not attacked, holding without it), willing (cooperation), not busy with
-- a fight or a call home, able to fight the attackers, and no one else already on the way. The
-- nearest such friend, or nil.
local HELP_RANGE, WILLING = 2500, 0.35

-- A player as an ally to help (the team world): their alarm, where they are, their force. Or nil.
function War.player_ally(index)
  local a = storage.war_players and storage.war_players[index]
  local player = game.get_player(index)
  if not (a and player and player.valid) then return nil end
  return {id = "player:" .. index, name = player.name, alarm = a, player_index = index, force = player.force,
    character = player.character}
end
-- Whoever `key` names among those helped: an engineer (its id) or a player ("player:<index>").
function War.ally(key)
  local index = type(key) == "string" and tonumber(key:match("^player:(%d+)$"))
  if index then return War.player_ally(index) end
  return storage.npcs and storage.npcs[key]
end

-- The team world: help is weighed ("some, not all", the user). A teammate's plot (an engineer's or a player's)
-- hit and not holding wants as much help as the attack there outweighs its loaded turrets; each engineer brings
-- what it can put into the fight (turrets it can drop loaded, a gun better than the pistol), and one that brings
-- nothing stays home. The nearest who can arrive while the fight lasts go, until what they bring covers the
-- shortfall (at most TEAM_HELPERS): none for a wave its turrets hold, one for a small one, more for a big one. Never
-- one whose own plot was hit lately or stands without loaded turrets of its own (D-12, rehearsal 7a: three helpers,
-- some with only a pistol, crossed 384 tiles for 3 to 9 small biters while their own bases were hit; one, called
-- away again a minute after its own plot went quiet, died).
local TEAM_RANGE, TEAM_HELPERS = 600, 3
local HELPER_QUIET = 18000            -- its own plot hit this lately (5 minutes): it stays
local HELPER_GUARDS = 1               -- loaded turrets of its own round home it leaves behind, at least
local MAGAZINES = {"firearm-magazine", "piercing-rounds-magazine", "uranium-rounds-magazine"}
local MAGAZINES_A_TURRET = 10         -- a turret it drops is loaded with this many
local GUN_KEEPS = 10                  -- and its own gun keeps this many (combat_turrets)
local GUNS = {["submachine-gun"] = 2}   -- small biters a gun better than the pistol holds off (the pistol: none)
local FIGHT_SHORT_S, FIGHT_OPEN_S = 60, 150   -- how long a fight there lasts: turrets standing but short, or none
local WALK = 8.9                      -- tiles a second on foot (a character runs 0.15 a tick)
local function try(f) local ok, v = pcall(f); if ok then return v end end
local function dist(p, q) return math.sqrt((p.x - q.x) ^ 2 + (p.y - q.y) ^ 2) end
War.on_trip = nil   -- set by agent.lua: whether an engineer is on a trip it does not break off for help
local function free(n, tick)
  return n.character and n.character.valid and not n.recall and not n.helping
    and not (n.combat and n.combat.active) and not (n.alarm and tick - n.alarm.last <= OVER_AFTER)
    and not (War.on_trip and War.on_trip(n))
end

-- What an engineer brings to a fight, in small biters it holds off: the turrets it carries that it can load (its
-- magazines, ten a turret), and a gun better than the pistol while it has magazines.
function War.brings(n)
  local c = n.character
  if not (c and c.valid) then return 0 end
  local magazines = 0
  for _, m in ipairs(MAGAZINES) do magazines = magazines + (try(function() return c.get_item_count(m) end) or 0) end
  if magazines <= 0 then return 0 end
  local turrets = try(function() return c.get_item_count("gun-turret") end) or 0
  local out = math.min(turrets, math.floor(math.max(0, magazines - GUN_KEEPS) / MAGAZINES_A_TURRET)) * TURRET_HOLDS
  local guns = try(function() return c.get_inventory(defines.inventory.character_guns) end)
  local best = 0
  for i = 1, guns and #guns or 0 do
    local s = guns[i]
    if s and s.valid_for_read then best = math.max(best, GUNS[s.name] or 0) end
  end
  return out + best
end

-- Its own plot can do without it: not hit lately, and loaded turrets of its own round home.
local function home_guarded(n, tick)
  if tick - (n.last_alarm or -HELPER_QUIET) < HELPER_QUIET or not n.home then return false end
  local guards = 0
  for _, t in ipairs(n.character.surface.find_entities_filtered{force = n.force, type = DEFENSE_TYPES,
      position = n.home, radius = HOME_GUARD_RADIUS}) do
    if t.valid and supplied(t) then guards = guards + 1 end
  end
  return guards >= HELPER_GUARDS
end

-- How much the attack on a plot outweighs its loaded turrets there (small biters), 0 when they hold; how long the
-- fight there lasts (seconds: short with turrets standing, longer with none).
local function shortfall(force, surface, a)
  local _, turrets, strength = holding_at(force, surface, a)
  -- (biters camped at its buildings, O-6: turrets near them that do not shoot them hold nothing off)
  if a.lingering and game.tick - a.lingering <= OVER_AFTER then turrets = 0 end
  local short = strength - turrets * TURRET_HOLDS
  if (a.people or a.lost > 0) and short <= 0 and strength <= 0 then short = MINOR end   -- (lost, none seen)
  return math.max(0, short), turrets > 0 and FIGHT_SHORT_S or FIGHT_OPEN_S, strength
end

-- What is already on its way to a teammate (its key: an engineer's id or "player:<index>"): what the helpers going
-- bring, both those helping and those called to build its defense (designed_start's help_request), and how many.
local function committed(key)
  local brings, count = 0, 0
  for _, n in pairs(storage.npcs or {}) do
    if n.helping and n.helping.ally == key then
      brings, count = brings + (n.helping.brings or 0), count + 1
    elseif n.help_request and n.help_request.npc_id ~= nil and n.help_request.npc_id == key then
      brings, count = brings + (n.help_request.brings or 0), count + 1
    end
  end
  return brings, count
end

-- Who comes to defend a spot of `npc`'s plot where biters took a building (designed_start: an ally-defense plan of
-- turrets there), weighed as the team's help is: none when its turrets there hold; else the nearest engineer that
-- brings something to the fight, can arrive while it lasts, and leaves its own plot quiet and guarded. Or nil.
function War.helper_for(npc, position, tick)
  local c = npc.character
  if not (c and c.valid) then return nil end
  local a = {position = position, lost = 1, by = {enemy = true}}
  local short, lasts = shortfall(npc.force, c.surface, a)
  local going, sent = committed(npc.id)
  if short - going <= 0 or sent >= TEAM_HELPERS then return nil end   -- (covered already, by either way of help)
  local best, best_d
  for _, n in pairs(storage.npcs or {}) do
    if n ~= npc and n.design and not n.help_request and free(n, tick) and n.force.get_friend(npc.force)
        and npc.force.get_friend(n.force) and War.brings(n) > 0 and home_guarded(n, tick) then
      local d = dist(n.character.position, position)
      if d <= TEAM_RANGE and d / WALK <= lasts and (not best or d < best_d or (d == best_d and n.id < best.id)) then
        best, best_d = n, d
      end
    end
  end
  return best
end

-- Whether the help it went for is still wanted: the attack not over, and the plot not holding by itself.
function War.help_wanted(ally, tick)
  local a = ally and ally.alarm
  if not a or tick - a.last > OVER_AFTER then return false end
  local surface = ally.character and ally.character.valid and ally.character.surface or game.surfaces[1]
  local force = ally.force or (ally.character and ally.character.force)
  if not force then return true end
  local short, _, seen = shortfall(force, surface, a)
  return short > 0 or seen <= 0   -- (the attackers out of sight round where it was hit: still on, it goes on)
end
local function team_ally_in_need(npc, tick)
  local c = npc.character
  if not free(npc, tick) or War.brings(npc) <= 0 or not home_guarded(npc, tick) then return nil end
  local wanted = {}
  for _, other in pairs(storage.npcs or {}) do
    local a = other.alarm
    if other ~= npc and a and tick - a.last <= OVER_AFTER and other.character and other.character.valid then
      local short, lasts = shortfall(other.force, c.surface, a)
      if short > 0 then wanted[#wanted + 1] = {ally = other, key = other.id, a = a, short = short, lasts = lasts} end
    end
  end
  for index, a in pairs(storage.war_players or {}) do
    if tick - a.last <= OVER_AFTER then
      local short, lasts = shortfall(game.forces.player, c.surface, a)
      local ally = short > 0 and War.player_ally(index)
      if ally then wanted[#wanted + 1] = {ally = ally, key = ally.id, a = a, short = short, lasts = lasts} end
    end
  end
  local best, best_d
  for _, w in ipairs(wanted) do
    local d = dist(c.position, w.a.position)
    local going, sent = committed(w.key)   -- (what those on their way bring, counted when they set off; both ways)
    local left = w.short - going
    if d <= TEAM_RANGE and d / WALK <= w.lasts and left > 0 and sent < TEAM_HELPERS then
      -- the nearest worthwhile helpers first (the lower id on a tie): it goes if those nearer bring too little
      local nearer = 0
      for _, n in pairs(storage.npcs or {}) do
        if n ~= npc and n ~= w.ally and free(n, tick) and home_guarded(n, tick) then
          local dn = dist(n.character.position, w.a.position)
          local b = War.brings(n)
          if b > 0 and dn / WALK <= w.lasts and (dn < d or (dn == d and n.id < npc.id)) then nearer = nearer + b end
        end
      end
      if nearer < left and (not best or d < best_d) then best, best_d = w.ally, d end
    end
  end
  return best
end

function War.ally_in_need(npc, tick, cooperation)
  if TeamWorld.active() then return team_ally_in_need(npc, tick) end
  local c = npc.character
  if not (c and c.valid) or npc.recall or npc.helping or (npc.combat and npc.combat.active) then return nil end
  if (cooperation or 0.5) < WILLING or not War.home_holds(npc, tick) then return nil end
  local going = {}
  for _, n in pairs(storage.npcs or {}) do
    if n ~= npc and n.helping then going[n.helping.ally] = true end
  end
  local best, best_d
  for _, other in pairs(storage.npcs or {}) do
    local a = other.alarm
    if other ~= npc and a and tick - a.last <= OVER_AFTER and not going[other.id]
        and other.character and other.character.valid and npc.force.get_friend(other.force)
        and can_fight(npc, a) then
      local d = math.sqrt((c.position.x - a.position.x) ^ 2 + (c.position.y - a.position.y) ^ 2)
      if d <= HELP_RANGE and (not best or d < best_d)
          and (a.people or a.lost > 0 or a.lingering or not War.holding(other, a)) then
        best, best_d = other, d
      end
    end
  end
  return best
end

-- A beaten base (no turrets of theirs left round it, its owner dead or away): take it over, or take
-- what is in it. Its own call, as the user put it: farther along than them (more research), it takes
-- what they had; behind them and near enough home to hold the place, it takes the base; a vehicle to
-- load the loot into tips it to taking the loot. Returns "take" or "loot", and the score.
local HOLD = 800
local function researched(force)
  local n = 0
  for _, t in pairs(force.technologies) do if t.researched then n = n + 1 end end
  return n
end
function War.spoils(npc, force, place, vehicle)
  local h = npc.home or place
  local near = (place.x - h.x) ^ 2 + (place.y - h.y) ^ 2 <= HOLD * HOLD
  local mine, theirs = researched(npc.force), researched(force)
  local score = (near and 1 or -1) + (mine > theirs + 2 and -1 or theirs >= mine and 0.5 or 0)
    + (vehicle and -1 or 0)
  return score > 0 and "take" or "loot", score
end

-- Who is attacking, by name: "biters", an engineer's name, or a force's name for players.
function War.attackers(a)
  local out = {}
  for who in pairs(a.by) do
    local id = tonumber(who:match(NPC_FORCE))
    local npc = id and storage.npcs and storage.npcs[id]
    out[#out + 1] = who == "enemy" and "biters" or npc and npc.name or who
  end
  table.sort(out)
  return out
end

-- Friends attacked by people it is not at war with: whether to join their war is its own choice (its
-- strategist's), so it is only told, once per attack. A list of {ally, by, position}.
function War.friends_attacked(npc)
  local out = {}
  npc.told_attacks = npc.told_attacks or {}
  for _, other in pairs(storage.npcs or {}) do
    local a = other.alarm
    if other ~= npc and a and a.people and npc.told_attacks[other.id] ~= a.first
        and npc.force.get_friend(other.force) then
      local peace = false
      for who in pairs(a.by) do
        local f = who ~= "enemy" and who ~= "unknown" and who ~= "neutral" and game.forces[who]
        if f and f ~= npc.force and not hostile(npc.force, f) and not npc.force.get_friend(f) then peace = true end
      end
      if peace then
        npc.told_attacks[other.id] = a.first
        out[#out + 1] = {ally = other.name, by = table.concat(War.attackers(a), ", "),
          position = {x = math.floor(a.position.x), y = math.floor(a.position.y)}}
      end
    end
  end
  return out
end

War.OVER_AFTER = OVER_AFTER
return War
