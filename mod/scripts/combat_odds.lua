-- The odds of a fight with biters, from what the game data says each side does. Small biters run 0.24 against the
-- engineer's 0.15 with the user's mods, so a fight it starts with them is one it cannot walk away from: run 34's
-- engineer walked into a wave of 60 to build turrets and died, run 52's walked back to an attack on its base with a
-- pistol. Whether to fight at all has to be decided before they are on it (combat.lua), and an attack on nests only
-- made when it can win the fight there standing (offense_brain.py).
--
-- Each side's strength is its firepower times its toughness (Lanchester's square law, as War-Plan.md has it):
--   ours    what its gun and its turrets take off them a second, through their resistances, times the health it
--           and its turrets have (armor counts as more health against their bites); FOCUS for shooting them down
--           one at a time while the rest still bite, which costs about half the bites of all of them at once
--   theirs  what they bite off a second, times the health they have
-- Short of rounds to kill them all, ours shrinks to the part it can kill. Every number is read from the prototypes
-- (modded biters, ammo, turrets and armor included); the weights are to be learned from how its fights go.
local Armory = require("scripts.armory")
local Recipes = require("scripts.recipes")
local Odds = {}

Odds.FOCUS = 2          -- shooting them down one at a time while the rest still bite
Odds.ENGAGE = 1.2       -- this much stronger than them, it takes a fight on (the estimate is rough)
Odds.LOSING = 0.8       -- a fight it is in that falls below this is lost
Odds.WAVE = 50          -- biters this near a fight join it (a biter shot at calls the others round it)
Odds.NOTICE = 30        -- how far biters see, where their prototype does not say
Odds.MARGIN = 8         -- kept beyond what they see
Odds.FILL = 10          -- magazines into each turret it puts down (combat_turrets.lua)
Odds.KEEP = 10          -- magazines kept back for its own gun
Odds.CARRIED = 8        -- turrets it puts down at its feet at most (combat_turrets MOST, D-14)
Odds.REINFORCE = 0.5    -- of what a spawner can keep, this much more comes out while it is attacked
Odds.NEST_REACH = 40    -- a nest cluster's buildings and biters lie this near its middle
Odds.WALLS = 12         -- walls this near it, on their side, are in their way (O-4: what fights beside it counts)
Odds.WALL_SHARE = 0.5   -- of a wall's health, what holds them up (some go round it): a weight to learn
Odds.WALLS_KEPT = 12    -- walls counted at most
Odds.WALL_LINE = 3      -- a wall this near the line from it to them is across their way
local FALLBACK_SHOT = {physical = 5}   -- a shot whose damage the data does not show (a projectile's, a beam's)
local COVER_LOOK = 30   -- turrets this near a spot may reach it (each counts only within its own range)
local TURRET_TYPES = {"ammo-turret", "electric-turret"}

local function try(f) local ok, v = pcall(f); if ok then return v end end
local function sum(t) local n = 0; for _, v in pairs(t or {}) do n = n + v end; return n end
local function distance(a, b) return math.sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2) end
local function hostile(force, other)
  if not force or not other or force == other or other.name == "neutral" then return false end
  return not force.get_friend(other) and not other.get_friend(force)
    and not force.get_cease_fire(other) and not other.get_cease_fire(force)
end
Odds.hostile = hostile

-- Damage after a resistance, as the game works it out: less the flat part (below 1 it tails off, never to nought),
-- then less the percentage.
function Odds.after(amount, r)
  if not r then return amount end
  local d = amount - (r.decrease or 0)
  if d < 1 then d = 1 / (2 - d) end
  return d * (1 - (r.percent or 0))
end
local function through(by_type, resist, scale)
  local n = 0
  for kind, amount in pairs(by_type) do n = n + Odds.after(amount * (scale or 1), resist and resist[kind]) end
  return n
end

-- Game data, read once per prototype (the same on every machine and fixed while the game runs: a cache of game data,
-- not game state, as the armory's).
local cache = {}
local function resist(p)
  local out = {}
  for kind, r in pairs(try(function() return p.resistances end) or {}) do
    out[kind] = {decrease = r.decrease or 0, percent = r.percent or 0}
  end
  return out
end
local function health(p)
  return try(function() return p.get_max_health() end) or try(function() return p.max_health end)
end

-- Damage by type of an attack or an ammo's action, followed into what it sends out: a spitter's or a worm's acid
-- flies as a stream, a rocket or a cannon shell as a projectile, a laser as a beam, and the damage is in that
-- prototype's own action (a spit read as 0 before, and the stand-in for it, times the spitter's damage modifier, made
-- a small spitter bite 60 and a small worm 1080). Returns nil where the data shows none.
-- (Combat Mechanics Overhaul makes a spit a projectile walls block, which then lands as the stream: followed too)
local DELIVERED = {stream = "stream", projectile = "projectile", beam = "beam", artillery = "projectile",
  ["create-entity"] = "entity_name"}
local function delivered(t, out, depth)
  if type(t) ~= "table" or depth > 10 then return end
  local key = DELIVERED[t.type]
  if key and type(t[key]) == "string" then out[#out + 1] = t[key] end
  for k, v in pairs(t) do if k ~= "damage" and type(v) == "table" then delivered(v, out, depth + 1) end end
end
local function action_damage(action, seen)
  seen = seen or {}
  local by = Armory.damage_by_type(action)
  local sent = {}
  delivered(action, sent, 0)
  for _, name in ipairs(sent) do
    if not seen[name] then
      seen[name] = true
      local p = try(function() return prototypes.entity[name] end)
      for _, field in ipairs({"initial_action", "action"}) do   -- (not final_action: only on a kill)
        local more = p and action_damage(try(function() return p[field] end), seen)
        for kind, v in pairs(more or {}) do by[kind] = (by[kind] or 0) + v end
      end
    end
  end
  return sum(by) > 0 and by or nil
end
Odds.action_damage = action_damage

-- A biter or a worm, by its prototype: its health, what one bite (or spit) does by type, bites a second, how far it
-- sees. What its attack does is read from its attack; a spitter's acid flies in a stream the data does not show,
-- and then a tenth of its health a bite stands in.
function Odds.foe_facts(p)
  local name = try(function() return p.name end) or "?"
  local f = cache["foe:" .. name]
  if f then return f end
  local a = try(function() return p.attack_parameters end) or {}
  local hp = tonumber(health(p)) or 15
  local hit = action_damage(a.ammo_type and a.ammo_type.action)
  if hit then
    local scale = tonumber(a.damage_modifier) or 1
    for kind, v in pairs(hit) do hit[kind] = v * scale end
  else
    hit = {physical = math.max(5, hp / 10)}   -- (nothing in the data: its modifier means nothing either)
  end
  f = {name = name, hp = hp, hit = hit, rate = 60 / math.max(1, tonumber(a.cooldown) or 35), resist = resist(p),
    sees = tonumber(try(function() return p.vision_distance end)) or Odds.NOTICE, reach = tonumber(a.range) or 1,
    speed = tonumber(try(function() return p.speed end)) or 0}
  cache["foe:" .. name] = f
  return f
end

-- A gun (an item's attack, or a turret's) firing an ammo: its cooldown and damage a shot by type, rounds a
-- magazine, and for a turret its health and resistances.
local function weapon_facts(gun, ammo, turret)
  local key = (turret and "turret:" or "gun:") .. tostring(gun) .. "|" .. tostring(ammo)
  local f = cache[key]
  if f ~= nil then return f or nil end
  local p = try(function() return (turret and prototypes.entity or prototypes.item)[gun] end)
  local item = ammo and try(function() return prototypes.item[ammo] end)
  local a = p and try(function() return p.attack_parameters end)
  local at = item and (try(function() return item.get_ammo_type(turret and "turret" or "player") end)
    or try(function() return item.get_ammo_type() end)) or a and a.ammo_type   -- (a laser turret's own)
  if not (a and at) then cache[key] = false; return nil end
  local by = action_damage(at.action) or FALLBACK_SHOT
  f = {by = by, cooldown = math.max(0.05, (tonumber(a.cooldown) or 30) * (tonumber(at.cooldown_modifier) or 1)),
    size = item and tonumber(try(function() return item.magazine_size end)) or 1,
    category = item and try(function() return item.ammo_category.name end)
      or a.ammo_categories and a.ammo_categories[1] or "bullet",   -- (a laser turret's: its own)
    range = tonumber(a.range) or 18, damage_modifier = tonumber(a.damage_modifier) or 1,
    hp = turret and tonumber(health(p)) or nil, resist = turret and resist(p) or nil}
  cache[key] = f
  return f
end
Odds.weapon_facts = weapon_facts

-- One thing shooting for it: shots a second and damage a shot with its force's research bonuses, and the rounds it
-- has. `turret`: the gun is a turret's (its own health counts toward ours).
function Odds.shooter(force, gun, ammo, rounds, turret)
  local f = weapon_facts(gun, ammo, turret)
  if not f or not (rounds and rounds > 0) then return nil end
  local scale = (1 + (try(function() return force.get_ammo_damage_modifier(f.category) end) or 0)) * f.damage_modifier
  if turret then scale = scale * (1 + (try(function() return force.get_turret_attack_modifier(gun) end) or 0)) end
  local speed = 1 + (try(function() return force.get_gun_speed_modifier(f.category) end) or 0)
  return {by = f.by, scale = scale, rate = 60 / f.cooldown * speed, rounds = rounds, range = f.range,
    hp = f.hp, resist = f.resist, size = f.size}
end

local function between(a, b, p)
  local dx, dy = b.x - a.x, b.y - a.y
  local l2 = dx * dx + dy * dy
  if l2 <= 0 then return false end
  local t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2
  return t > 0 and t < 1 and distance(p, {x = a.x + dx * t, y = a.y + dy * t}) <= Odds.WALL_LINE
end

local function loaded_turret(force, t)
  if t.type == "ammo-turret" then
    local inv = try(function() return t.get_inventory(defines.inventory.turret_ammo) end)
    local stack = inv and not inv.is_empty() and inv[1]
    if not (stack and stack.valid_for_read) then return nil end
    local f = weapon_facts(t.name, stack.name, true)
    local s = f and Odds.shooter(force, t.name, stack.name, inv.get_item_count(stack.name) * f.size, true)
    if s then s.hp = t.health or s.hp end
    return s
  elseif t.type == "electric-turret" and (t.energy or 0) > 0 then
    local s = Odds.shooter(force, t.name, nil, 1e6, true)
    if s then s.hp = t.health or s.hp end
    return s
  end
end

-- Its own side, fighting at `at`: its gun (`gear`: Combat's pick, with the rounds it owns for it), the turrets it
-- would put down at its feet (opts.carried: those it carries, filled from the magazines it can spare), the loaded
-- turrets of its own and its friends' that reach the spot (opts.cover), and its health through its armor. With
-- opts.cover also the armed engineers and players of its side near the spot (opts.gear_of(character): what each
-- fights with), and walls across their way to it (opts.foes: who they are), which they must chew through first.
function Odds.ours(npc, at, gear, opts)
  opts = opts or {}
  local c = npc.character
  local us = {shooters = {}, bodies = {}}
  local rounds = (gear and gear.ready and gear.ammo_units or 0) + (opts.extra_rounds or 0)
  -- (the turret it puts down and what it loads them with: opts.turret / turret_ammo, its creep kit, else gun turrets
  -- with its gun's own ammunition)
  local tname, tammo = opts.turret or "gun-turret", opts.turret_ammo or gear and gear.ammo_name
  if opts.carried and gear and tammo then
    local main = try(function() return c.get_main_inventory() end)
    local carried = math.min(Odds.CARRIED, opts.turrets or main and main.get_item_count(opts.turret_item or tname) or 0)
    local f = carried > 0 and weapon_facts(tname, tammo, true)
    -- (its gun's rounds fill them only when they take the same kind: rockets for one, bullets for the other cannot)
    local g = f and gear.gun and gear.ammo_name and weapon_facts(gear.gun, gear.ammo_name, false)
    if f and g and f.category ~= g.category then f = nil end
    if f then
      local spare = math.max(0, math.floor(rounds / f.size) - Odds.KEEP)
      local each = math.min(Odds.FILL, math.floor(spare / carried))
      for _ = 1, each > 0 and carried or 0 do
        local s = Odds.shooter(c.force, tname, tammo, each * f.size, true)
        if s then us.shooters[#us.shooters + 1] = s; us.bodies[#us.bodies + 1] = {hp = s.hp or 400, resist = s.resist}
          rounds = rounds - each * f.size end
      end
    end
  end
  if gear and gear.ready and gear.gun and rounds > 0 then
    us.shooters[#us.shooters + 1] = Odds.shooter(c.force, gear.gun, gear.ammo_name, rounds, false)
  end
  if opts.cover and at then
    local forces = {c.force}
    for _, f in pairs(game.forces) do if f ~= c.force and c.force.get_friend(f) then forces[#forces + 1] = f end end
    for _, t in ipairs(c.surface.find_entities_filtered{position = at, radius = COVER_LOOK, force = forces,
        type = TURRET_TYPES}) do
      local s = t.valid and loaded_turret(t.force, t)
      if s and distance(t.position, at) <= s.range + 1 then
        us.shooters[#us.shooters + 1] = s
        us.bodies[#us.bodies + 1] = {hp = s.hp or 400, resist = s.resist}
      end
    end
    for _, h in ipairs(opts.gear_of and c.surface.find_entities_filtered{position = at, radius = COVER_LOOK,
        force = forces, type = "character"} or {}) do
      local g = h.valid and h ~= c and try(function() return opts.gear_of(h) end)
      local s = g and g.ready and g.gun and Odds.shooter(h.force, g.gun, g.ammo_name, g.ammo_units, false)
      if s and distance(h.position, at) <= s.range then   -- (one farther off cannot shoot what comes at it)
        us.shooters[#us.shooters + 1] = s
        us.bodies[#us.bodies + 1] = {hp = h.health or 250, resist = {}}
      end
    end
    if opts.foes then
      -- Walls near it count for the share of them whose way to it they cross (a wave from two sides goes round
      -- a wall on one: the review), as health they must chew through first.
      local walls = {}
      for _, wall in ipairs(c.surface.find_entities_filtered{position = at, radius = Odds.WALLS, force = forces,
          type = "wall", limit = Odds.WALLS_KEPT}) do
        if wall.valid then walls[#walls + 1] = wall end
      end
      local crossed, total = 0, 0
      for _, f in ipairs(#walls > 0 and opts.foes or {}) do
        local e = f.entity
        if e and e.valid then
          total = total + (f.count or 1)
          for _, wall in ipairs(walls) do
            if between(at, e.position, wall.position) then crossed = crossed + (f.count or 1); break end
          end
        end
      end
      local share = total > 0 and crossed / total or 0
      for _, wall in ipairs(share > 0 and walls or {}) do
        us.bodies[#us.bodies + 1] = {hp = (wall.health or 350) * Odds.WALL_SHARE * share,
          resist = resist(try(function() return wall.prototype end))}
      end
    end
  end
  local worn = try(function() return c.get_inventory(defines.inventory.character_armor) end)
  local armor = worn and worn[1] and worn[1].valid_for_read and try(function() return worn[1].prototype end)
  us.bodies[#us.bodies + 1] = {hp = c.health or 250, resist = armor and resist(armor) or {}, me = true}
  return us
end

-- Theirs: the biters within `radius` of `at`, each {hp, count, facts, entity}.
function Odds.theirs(npc, at, radius)
  local c = npc.character
  local out = {}
  for _, e in ipairs(c.surface.find_entities_filtered{position = at, radius = radius or Odds.WAVE, type = "unit",
      limit = 400}) do
    if e.valid and hostile(c.force, e.force) then
      local facts = Odds.foe_facts(e.prototype)
      out[#out + 1] = {hp = e.health or facts.hp, count = 1, facts = facts, entity = e}
    end
  end
  return out
end

-- The fight: {ratio (ours over theirs; at least ENGAGE to take it on), ours, theirs, killable (the part of them its
-- rounds can kill), seconds (to kill them all), lasts (how long its side holds out), n}. `ground`: things that do not
-- fight back but must be shot down after (a nest's spawners): their health costs rounds only.
function Odds.fight(us, them, ground)
  local H, raw, n = 0, 0, 0
  for _, f in ipairs(them) do
    H = H + f.hp * (f.count or 1)
    raw = raw + sum(f.facts.hit) * f.facts.rate * (f.count or 1)
    n = n + (f.count or 1)
  end
  if H <= 0 then
    -- Nothing to fight back, but the ground (a nest with no biters) still takes a gun and the rounds to bring it down.
    local killable = (ground and #ground > 0) and 0 or 1
    for _, s in ipairs(ground and #ground > 0 and us.shooters or {}) do
      local need = 0
      for _, g in ipairs(ground) do need = need + g.hp * (g.count or 1) / math.max(0.01, through(s.by, g.resist, s.scale)) end
      killable = math.min(1, killable + (need > 0 and s.rounds / need or 1))
    end
    local spent = 0
    for _, s in ipairs(ground and #ground > 0 and us.shooters or {}) do
      for _, g in ipairs(ground) do spent = spent + g.hp * (g.count or 1) / math.max(0.01, through(s.by, g.resist, s.scale)) end
      break   -- (its first gun's count of rounds: they need not share)
    end
    return {ratio = killable >= 0.999 and math.huge or 0, ours = 0, theirs = 0, killable = killable, seconds = 0,
      lasts = math.huge, n = 0, spent = spent, lost = 0}
  end
  -- what each shooter takes off them a second, and the rounds it would spend on its share
  local F, shares = 0, {}
  for i, s in ipairs(us.shooters) do
    local need, after = 0, 0   -- rounds to kill them; and the ground's, after (rounds only, not firepower)
    for _, f in ipairs(them) do need = need + f.hp * (f.count or 1) / math.max(0.01, through(s.by, f.facts.resist, s.scale)) end
    for _, g in ipairs(ground or {}) do after = after + g.hp * (g.count or 1) / math.max(0.01, through(s.by, g.resist, s.scale)) end
    shares[i] = {need = need + after, f = s.rate * H / math.max(0.01, need)}
    F = F + shares[i].f
  end
  local killable, spent = 0, 0
  for i, s in ipairs(us.shooters) do
    local share = F > 0 and shares[i].f / F or 0
    local spend = shares[i].need * share
    spent = spent + math.min(spend, s.rounds)
    killable = killable + share * (spend > 0 and math.min(1, s.rounds / spend) or 1)
  end
  -- its side's health, armor counted as more of it against what they bite with
  local tough = 0
  for _, b in ipairs(us.bodies) do
    local against = 0
    for _, f in ipairs(them) do against = against + through(f.facts.hit, b.resist) * f.facts.rate * (f.count or 1) end
    tough = tough + b.hp * (raw > 0 and raw / math.max(0.01, against) or 1)
  end
  local ours, theirs = Odds.FOCUS * F * tough, raw * H
  local ratio = theirs > 0 and ours * killable / theirs or (F > 0 and math.huge or 0)
  -- Short of rounds for the last of them it cannot win at all: they outrun it, and it cannot leave (the review: 90% of
  -- the rounds still read as a win).
  if killable < 0.999 then ratio = math.min(ratio, Odds.LOSING * killable) end
  -- (what a won fight costs it: Lanchester's square law leaves the winner sqrt(1 - 1/ratio) of its strength)
  local lost = ratio > 1 and 1 - math.sqrt(1 - 1 / ratio) or 1
  return {ratio = ratio, ours = ours, theirs = theirs, killable = killable, n = n, spent = spent, lost = lost,
    seconds = F > 0 and H / F or math.huge, lasts = raw > 0 and tough / raw or math.huge}
end

-- What a spawner sends out at this evolution: {unit name = share}, from its result units' spawn points.
local function mix(p, evolution)
  local out, total = {}, 0
  for _, u in ipairs(try(function() return p.result_units end) or {}) do
    local points = u.spawn_points or {}
    local w = 0
    for k, point in ipairs(points) do
      local nxt = points[k + 1]
      local e0, w0 = point.evolution_factor or point[1] or 0, point.weight or point[2] or 0
      if evolution <= e0 then if k == 1 then w = w0 end break end
      if not nxt then w = w0 break end
      local e1, w1 = nxt.evolution_factor or nxt[1] or 1, nxt.weight or nxt[2] or 0
      if evolution <= e1 then w = w0 + (w1 - w0) * (evolution - e0) / math.max(1e-6, e1 - e0) break end
    end
    if w > 0 then out[u.unit or u[1]] = (out[u.unit or u[1]] or 0) + w; total = total + w end
  end
  for name, w in pairs(out) do out[name] = w / total end
  return out
end
Odds.mix = mix
Odds.loaded_turret = loaded_turret   -- (a turret's part in a fight, as it stands: team_waves.lua sizes waves by it)

-- An attack on the nest cluster at {x, y} (BaseMemory.nest_clusters'): the biters there now, what its spawners send
-- out while they are attacked (REINFORCE of what each can keep, as the biters are evolved now) and its worms (they
-- spit at its turrets), against what it carries, its turrets put down at its feet; the spawners' own health only
-- costs rounds. It cannot leave a fight there either: the attack is worth making only at ENGAGE or better.
function Odds.cluster(npc, cluster, gear, bring)
  local c = npc.character
  local at = {x = cluster.x, y = cluster.y}
  local them, ground, units, mobile = {}, {}, 0, {}   -- (mobile: what comes out at whoever attacks, worms stay)
  local evolution = try(function() return game.forces.enemy.get_evolution_factor(c.surface) end) or 0
  local sends = {}
  for _, e in ipairs(c.surface.find_entities_filtered{position = at, radius = Odds.NEST_REACH,
      type = {"unit", "unit-spawner", "turret"}, limit = 400}) do
    if e.valid and hostile(c.force, e.force) then
      local p = e.prototype
      if e.type == "unit-spawner" then
        ground[#ground + 1] = {hp = e.health or health(p) or 350, resist = resist(p)}
        local keeps = tonumber(try(function() return p.max_count_of_owned_units end)) or 7
        for name, share in pairs(mix(p, evolution)) do
          sends[name] = (sends[name] or 0) + share * keeps * Odds.REINFORCE
        end
      else
        if e.type == "unit" then units = units + 1 end
        local facts = Odds.foe_facts(p)
        them[#them + 1] = {hp = e.health or facts.hp, count = 1, facts = facts}
        if e.type == "unit" then mobile[#mobile + 1] = them[#them] end
      end
    end
  end
  local coming = 0
  for name, n in pairs(sends) do
    local p = try(function() return prototypes.entity[name] end)
    if p and n > 0 then
      local facts = Odds.foe_facts(p)
      them[#them + 1] = {hp = facts.hp, count = n, facts = facts}
      mobile[#mobile + 1] = them[#them]
      coming = coming + n
    end
  end
  -- (`bring`: the turrets and spare rounds it could take along beside what it holds, O-5: what it can afford to lose)
  bring = bring or {}
  local us = Odds.ours(npc, nil, gear, {carried = true, turrets = bring.turrets, extra_rounds = bring.rounds,
    turret = bring.turret, turret_item = bring.turret_item, turret_ammo = bring.turret_ammo})
  local fight = Odds.fight(us, them, ground)
  local rounds, turrets = 0, 0
  for _, s in ipairs(us.shooters) do rounds = rounds + s.rounds; if s.hp then turrets = turrets + 1 end end
  local function round(v) return math.floor(math.min(v, 99) * 100 + 0.5) / 100 end
  -- From far out (artillery, O-7): only what comes out of the nests reaches it, the worms stay where they are, out of
  -- range; it fights that beside its guard turrets (bring.guards). Ground and worms are the shells' work.
  local far
  if bring.guards then
    local us_far = Odds.ours(npc, nil, gear, {carried = true, turrets = math.min(bring.guards, bring.turrets or 0),
      extra_rounds = bring.rounds, turret = bring.turret, turret_item = bring.turret_item, turret_ammo = bring.turret_ammo})
    far = Odds.fight(us_far, mobile)
  end
  -- (the ratio rounded down: just short of a threshold never reads as on it, the review)
  return {ratio = math.floor(math.min(fight.ratio, 99) * 100) / 100, defenders = units, reinforcements = math.floor(coming + 0.5),
    spawners = #ground, killable = round(fight.killable), rounds = math.floor(rounds),
    evolution = round(evolution), turrets = turrets, rounds_needed = math.floor(fight.spent + 0.5),
    lost = round(fight.lost),
    ratio_far = far and math.floor(math.min(far.ratio, 99) * 100) / 100 or nil, lost_far = far and round(far.lost) or nil,
    worms = (function() local n = 0 for _, f in ipairs(them) do if f.facts.speed == 0 then n = n + 1 end end return n end)()}
end

-- What an item costs in plates and raw resources, down its recipes (O-5's late extension: a uranium magazine, a laser
-- turret or a modded one, an artillery shell; the weights stood for vanilla gun turrets and plain magazines). Plates
-- and what no recipe makes count one each; fluids nothing; per item its recipe yields. A cache of game data.
Odds.PRICE_DEPTH, Odds.PRICE_CHOICES, Odds.FLUID_PLATES = 8, 4, 0.1
function Odds.plates(force, item, depth, seen)
  local key = "price:" .. tostring(item)
  if cache[key] then return cache[key] end
  depth, seen = depth or 0, seen or {}
  if depth >= Odds.PRICE_DEPTH or tostring(item):find("%-plate$") or seen[item] then return 1 end
  seen[item] = true
  -- (the cheapest of its recipes, up to PRICE_CHOICES: with mods the first one offered may be a recycling or an odd
  -- one, and explosives priced 3000 plates through it, O-8)
  local best
  local names = try(function() return Recipes.candidates(force, item) end) or {}
  for k = 1, math.min(Odds.PRICE_CHOICES, #names) do
    local name = names[k]
    local recipe = type(name) == "table" and name
      or (try(function() return force.recipes[name] end) or try(function() return prototypes.recipe[name] end))
    local category = recipe and tostring(try(function() return recipe.category end) or "")
    if recipe and not category:find("recycl") then   -- (taking things apart is no way to make them)
      local n = 0
      -- (its own output among its ingredients is left out: the yield is already what it makes net; a fluid counts
      -- FLUID_PLATES a unit, so a casting from molten metal is not free)
      for _, ing in ipairs(try(function() return recipe.ingredients end) or {}) do
        if ing.type == "fluid" then n = n + (ing.amount or 0) * Odds.FLUID_PLATES
        elseif ing.name ~= item then
          n = n + (ing.amount or 1) * Odds.plates(force, ing.name, depth + 1, seen)
        end
      end
      n = n / math.max(1, tonumber(try(function() return Recipes.yield(recipe, item) end)) or 1)
      if not best or n < best then best = n end
    end
  end
  seen[item] = nil
  local n = best or 1
  if depth == 0 then cache[key] = n end
  return n
end

return Odds
