-- Glue between the world and the character core. Game events become chronicle
-- events (which update memory, relationships and mood), chronicle events give
-- NPCs something to say, and each NPC periodically takes the initiative toward
-- the player: offering trades, asking for help, greeting or threatening.
-- Nothing here moves items or changes force relations by itself.
local Chronicle = require("scripts.chronicle")
local Relations = require("scripts.relations")
local Personality = require("scripts.personality")
local PlayerTrade = require("scripts.player_trade")
local Voice = require("scripts.voice")
local TeamWorld = require("scripts.team_world")
local Character = {}

local MAX_NPC = 10
local NPC_FORCE = "^npc%-faction%-(%d+)$"
local ATTACK_FLUSH = 120        -- first report of an attack after 2 s of damage
local ATTACK_REPEAT = 1800      -- then at most every 30 s per attacker/victim pair
local INITIATIVE = 3600         -- each NPC considers the player about once a minute
local WITNESS_RANGE = 64
local MILESTONES = {["rocket-silo"]=true, ["space-platform"]=true, ["space-science-pack"]=true,
  ["logistic-science-pack"]=true, ["chemical-science-pack"]=true}

local function state()
  storage.character_layer = storage.character_layer or {damage={}, last_attack={}}
  return storage.character_layer
end

local function live(id)
  local npc = storage.npcs and storage.npcs[id]
  return npc and npc.character and npc.character.valid and npc or nil
end

local function npc_id(key)
  return type(key) == "string" and tonumber(key:match("^npc:(%d+)$")) or nil
end

local function player_index(key)
  return type(key) == "string" and tonumber(key:match("^player:(%d+)$")) or nil
end

function Character.key_for_force(force)
  local id = force and force.valid and tonumber(force.name:match(NPC_FORCE))
  return id and "npc:" .. id or nil
end

-- Who is behind a damaging entity: a player, an NPC, or a player force's turret.
function Character.key_for_cause(cause)
  if not (cause and cause.valid) then return nil end
  if cause.type == "character" then
    local player = cause.player
    if player then return "player:" .. player.index end
    for id = 1, MAX_NPC do
      local npc = storage.npcs and storage.npcs[id]
      if npc and npc.character == cause then return "npc:" .. id end
    end
  end
  local key = Character.key_for_force(cause.force)
  if key then return key end
  if cause.force and cause.force.valid and #cause.force.players > 0 then return "force:" .. cause.force.name end
end

function Character.name_of(key)
  local id = npc_id(key)
  if id then
    local npc = storage.npcs and storage.npcs[id]
    return npc and npc.name or ("engineer #" .. id)
  end
  local index = player_index(key)
  if index then
    local player = game.get_player(index)
    return player and player.name or "a player"
  end
  return type(key) == "string" and key:gsub("^force:", "") or "someone"
end

local function witnesses(surface, position, exclude)
  local out = {}
  for id = 1, MAX_NPC do
    local npc = live(id)
    if npc and not exclude["npc:" .. id] and npc.character.surface == surface then
      local p = npc.character.position
      local dx, dy = p.x - position.x, p.y - position.y
      if dx * dx + dy * dy <= WITNESS_RANGE * WITNESS_RANGE then out[#out + 1] = "npc:" .. id end
    end
  end
  return #out > 0 and out or nil
end

local function record(kind, actor, target, payload, source, seen)
  local event, reason = Chronicle.record{kind=kind, actor=actor, target=target, tick=game.tick,
    source=source or "native", payload=payload, witnesses=seen}
  if not event and log then log("[npc-factions] chronicle rejected " .. kind .. ": " .. tostring(reason)) end
  return event
end
Character.record = record

-- Society log entries (NPC to NPC diplomacy and physical intents).
function Character.on_society(actor, kind, details)
  if not actor then return end
  details = details or {}
  local me = "npc:" .. actor.id
  if kind == "offer" and details.target then
    record("offer_made", me, "npc:" .. details.target, {offer=details.offer_kind, offer_id=details.offer_id}, "society")
  elseif kind == "treaty" and details.target then
    record(details.offer_kind == "peace" and "peace_made" or "treaty_accepted", me, "npc:" .. details.target,
      {offer=details.offer_kind, offer_id=details.offer_id}, "society")
  elseif kind == "rejection" and details.proposer and details.proposer ~= actor.id then
    record("offer_rejected", me, "npc:" .. details.proposer, {offer=details.offer_kind, offer_id=details.offer_id}, "society")
  elseif kind == "war" and details.target then
    record("war_declared", me, "npc:" .. details.target, nil, "society")
  elseif kind == "trade_delivery" and details.intent and details.intent.target_id then
    record("delivery_completed", me, "npc:" .. details.intent.target_id,
      {item=details.intent.item, count=details.count, value=math.min(1000, details.count or 1)}, "society")
  elseif kind == "physical_failure" and details.intent and details.intent.kind == "supply" and details.intent.target_id then
    record("delivery_failed", me, "npc:" .. details.intent.target_id, {item=details.intent.item}, "society")
  elseif kind == "hostile_action" and details.intent and details.intent.target_id then
    local intent = details.intent
    local victim = live(intent.target_id)
    local seen = victim and witnesses(victim.character.surface, victim.character.position,
      {[me]=true, ["npc:" .. intent.target_id]=true})
    record(intent.kind == "theft" and "theft" or "sabotage", me, "npc:" .. intent.target_id,
      {item=intent.item, count=intent.count}, "society", seen)
  end
end

-- Damage to NPC property accumulates per attacker and is reported in bursts,
-- so a long fight becomes a few "attacked" events rather than thousands.
function Character.on_entity_damaged(event)
  local entity = event.entity
  if not (entity and entity.valid) then return end
  local victim = Character.key_for_force(entity.force)
  if not victim then return end
  local attacker = Character.key_for_cause(event.cause)
  if not attacker or attacker == victim then return end
  if Character.damage_listener then
    Character.damage_listener(victim, attacker, math.max(0, event.final_damage_amount or 0), entity)
  end
  local s = state()
  local key = victim .. "|" .. attacker
  local entry = s.damage[key]
  if not entry then
    entry = {victim=victim, attacker=attacker, amount=0, first=game.tick, surface_index=entity.surface.index,
      position={x=entity.position.x, y=entity.position.y}}
    s.damage[key] = entry
  end
  entry.amount = entry.amount + math.max(0, event.final_damage_amount or 0)
end

local function report_attack(s, entry, severity, payload)
  local key = entry.victim .. "|" .. entry.attacker
  s.last_attack[key] = game.tick
  if Character.attack_listener then Character.attack_listener(entry, payload or {}) end
  local surface = game.get_surface(entry.surface_index)
  local seen = surface and witnesses(surface, entry.position, {[entry.victim]=true, [entry.attacker]=true})
  payload = payload or {}
  payload.severity = severity
  record("attacked", entry.attacker, entry.victim, payload, "native", seen)
end

local function flush_damage(tick, force)
  local s = state()
  local keys = {}
  for key in pairs(s.damage) do keys[#keys + 1] = key end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local entry = s.damage[key]
    local due = tick - entry.first >= ATTACK_FLUSH and tick - (s.last_attack[key] or -ATTACK_REPEAT) >= ATTACK_REPEAT
    if force or due then
      s.damage[key] = nil
      if entry.amount >= 1 then
        report_attack(s, entry, math.max(0.2, math.min(3, entry.amount / 150)), {damage=math.floor(entry.amount)})
      end
    end
  end
end

function Character.on_entity_died(event)
  local entity = event.entity
  if not (entity and entity.valid) then return end
  local victim = Character.key_for_force(entity.force)
  if not victim then return end
  local attacker = Character.key_for_cause(event.cause)
  local id = npc_id(victim)
  local npc = storage.npcs and storage.npcs[id]
  if npc and npc.character == entity then
    local seen = witnesses(entity.surface, entity.position, {[victim]=true, [attacker or ""]=true})
    if attacker and attacker ~= victim then record("killed", attacker, victim, nil, "native", seen) end
    Voice.announce(npc.name .. " has fallen" .. (attacker and attacker ~= victim and
      (" to " .. Character.name_of(attacker)) or "") .. ". Engineers do not come back.")
    return
  end
  if attacker and attacker ~= victim then
    if Character.damage_listener then Character.damage_listener(victim, attacker, 0, entity, entity.name) end
    -- A destroyed building is reported at once, together with any damage
    -- this attacker had dealt that was still waiting to be reported.
    local s = state()
    local key = victim .. "|" .. attacker
    local pending = s.damage[key]
    s.damage[key] = nil
    local damage = pending and math.floor(pending.amount) or 0
    local entry = {victim=victim, attacker=attacker, surface_index=entity.surface.index,
      position={x=entity.position.x, y=entity.position.y}}
    report_attack(s, entry, math.max(1.5, math.min(3, damage / 150)), {destroyed=entity.name, damage=damage})
  end
end

function Character.on_research_finished(event)
  local research = event.research
  if not (research and research.valid) then return end
  local key = Character.key_for_force(research.force)
  if key then record("research_completed", key, nil, {technology=research.name}) end
end

-- Player barter outcomes (PlayerTrade calls this when a deal resolves).
function Character.on_trade_resolved(deal)
  local npc_key, player_key = "npc:" .. deal.npc_id, "player:" .. deal.player_index
  local payload = {deal_id=deal.id, npc_gives=deal.npc_gives, player_gives=deal.player_gives,
    value=math.min(1000, deal.npc_count or 1)}
  if deal.status == "committed" then
    record("trade_completed", npc_key, player_key, payload, "player")
  elseif deal.status == "failed" and deal.blame == "player" then
    record("trade_failed", player_key, npc_key, payload, "player")
  elseif deal.status == "rejected" and deal.failure_reason == "player_rejected" and deal.npc_offered then
    record("offer_rejected", player_key, npc_key, payload, "player")
  end
end

-- Treaty decisions involving a player (diplomacy commands and chat).
function Character.on_player_treaty(npc, player, kind, accepted)
  local me, them = "npc:" .. npc.id, "player:" .. player.index
  if kind == "war" then record("war_declared", them, me, nil, "player")
  elseif accepted then record(kind == "peace" and "peace_made" or "treaty_accepted", them, me, {offer=kind}, "player")
  end
end

local function relation(npc, key, tick)
  local r = Relations.get(npc, key, tick)
  return r, Relations.label(r)
end
Character.relation = relation

local VERBS = {attacked="attacked", killed="killed", theft="stole from", sabotage="sabotaged",
  gift="gave a gift to", helped="helped", delivery_completed="delivered goods to",
  delivery_failed="failed a delivery to", trade_completed="traded with", trade_failed="broke a trade with",
  treaty_accepted="made a treaty with", treaty_broken="broke a treaty with", war_declared="declared war on",
  peace_made="made peace with", offer_rejected="turned down", offer_made="made an offer to",
  threat="threatened", insult="insulted", compliment="complimented", promise_kept="kept a promise to",
  promise_broken="broke a promise to"}

-- "Ada attacked you", from the point of view of engineer `me`.
function Character.describe(e, me)
  local verb = VERBS[e.kind]
  if not verb then return nil end
  local actor = e.actor == me and "you" or e.actor and Character.name_of(e.actor) or "someone"
  local target = e.target == me and "you" or e.target and Character.name_of(e.target) or nil
  return actor .. " " .. verb .. (target and " " .. target or "")
end

-- What an engineer knows about its social situation, for planners and the
-- designer: mood, the strongest relationships with recent memories, and
-- whether it has been attacked lately.
function Character.social_summary(npc, tick, limit)
  tick = tick or game.tick
  local me = "npc:" .. npc.id
  local mood = Relations.mood(npc.id, tick)
  local entries = {}
  for key in pairs((storage.relations or {})[npc.id] or {}) do
    local r = Relations.get(npc, key, tick)
    entries[#entries + 1] = {key=key, name=Character.name_of(key), label=Relations.label(r),
      stance=Relations.stance(npc.id, key, tick), trust=math.floor(r.trust + 0.5),
      affinity=math.floor(r.affinity + 0.5), grudge=math.floor(r.grudge + 0.5), fear=math.floor(r.fear + 0.5)}
  end
  table.sort(entries, function(a, b)
    if math.abs(a.stance) ~= math.abs(b.stance) then return math.abs(a.stance) > math.abs(b.stance) end
    return a.key < b.key
  end)
  local relations = {}
  for i = 1, math.min(limit or 4, #entries) do
    local entry = entries[i]
    entry.stance = math.floor(entry.stance * 100 + 0.5) / 100
    -- Only what this person did or had done to them, not what they merely saw.
    entry.memories = {}
    for _, e in ipairs(Relations.memories(npc.id, entry.key, 8)) do
      if #entry.memories < 2 and (e.actor == entry.key or e.target == entry.key) then
        entry.memories[#entry.memories + 1] = Character.describe(e, me)
      end
    end
    relations[#relations + 1] = entry
  end
  local attacks, attackers, seen = 0, {}, {}
  for _, e in ipairs(Chronicle.recent(256, {kinds={"attacked", "theft", "sabotage"}, involving=me})) do
    if e.target == me and tick - e.tick <= 36000 then
      attacks = attacks + 1
      if e.actor and not seen[e.actor] then seen[e.actor] = true; attackers[#attackers + 1] = Character.name_of(e.actor) end
    end
  end
  return {mood={stress=math.floor(mood.stress + 0.5), morale=math.floor(mood.morale + 0.5),
    anger=math.floor(mood.anger + 0.5)}, relations=relations, recent_attacks=attacks, attackers=attackers}
end

local function speak(npc, kind, listener, vars, opts)
  opts = opts or {}
  local _, label = relation(npc, listener or "", game.tick)
  vars = vars or {}
  vars.target = vars.target or (listener and Character.name_of(listener)) or ""
  opts.label = opts.label or (listener and label or nil)
  opts.mood = opts.mood or Relations.mood(npc.id, game.tick)
  opts.target = opts.target or listener
  return Voice.say(npc, kind, vars, opts)
end
Character.speak = speak

-- Chronicle events give the people involved something to say.
local function on_chronicle(e)
  local actor_npc = live(npc_id(e.actor) or 0)
  local target_npc = live(npc_id(e.target) or 0)
  local p = e.payload or {}
  local kind = e.kind
  local to_player = player_index(e.target) and game.get_player(player_index(e.target)) or nil
  if kind == "offer_made" and actor_npc then
    speak(actor_npc, "offer_made", e.target, {offer=Voice.pretty(p.offer)}, {priority=2})
  elseif (kind == "treaty_accepted" or kind == "peace_made") and actor_npc then
    speak(actor_npc, "treaty", e.target, {offer=Voice.pretty(p.offer or (kind == "peace_made" and "peace" or "treaty"))},
      {priority=3})
  elseif kind == "offer_rejected" and actor_npc and not to_player then
    speak(actor_npc, "offer_rejected", e.target, nil, {priority=2})
  elseif kind == "war_declared" then
    if actor_npc then speak(actor_npc, "war_declared", e.target, nil, {priority=3}) end
    if target_npc then speak(target_npc, "war_received", e.actor, nil, {priority=3}) end
  elseif kind == "delivery_completed" and actor_npc then
    speak(actor_npc, "delivery_completed", e.target, {item=Voice.pretty(p.item), count=p.count or "some"}, {priority=2})
  elseif kind == "delivery_failed" and actor_npc then
    speak(actor_npc, "delivery_failed", e.target, {item=Voice.pretty(p.item)}, {priority=1})
  elseif (kind == "theft" or kind == "sabotage") and target_npc then
    speak(target_npc, kind == "theft" and "robbed" or "sabotaged", e.actor, {item=Voice.pretty(p.item or "things")},
      {priority=2})
  elseif kind == "attacked" and target_npc then
    speak(target_npc, "attacked", e.actor, nil, {priority=3})
  elseif kind == "research_completed" and actor_npc then
    speak(actor_npc, "research_completed", nil, {tech=Voice.pretty(p.technology)},
      {priority=MILESTONES[p.technology] and 3 or 1})
  elseif (kind == "plan_completed" or kind == "plan_failed") and actor_npc then
    speak(actor_npc, kind, nil, {purpose=Voice.pretty(p.purpose or "build")}, {priority=1})
  elseif (kind == "trade_completed") and actor_npc and to_player then
    speak(actor_npc, "trade_completed", e.target, nil, {player=to_player, direct=true})
  elseif kind == "trade_failed" and target_npc and player_index(e.actor) then
    local player = game.get_player(player_index(e.actor))
    if player then speak(target_npc, "trade_failed", e.actor, nil, {player=player, direct=true}) end
  end
end
Chronicle.subscribe(on_chronicle)

local function rng(npc)
  local seed = Personality.ensure(npc).seed or npc.id
  npc.voice_rng = ((npc.voice_rng or seed) * 48271) % 2147483647
  return npc.voice_rng / 2147483647
end

local function nearest_player(npc)
  local best, best_d
  local force = storage.founding_force
  if not (force and force.valid) then return nil end
  for _, player in pairs(force.connected_players) do
    if player.character and player.character.valid and player.character.surface == npc.character.surface then
      local a, b = player.character.position, npc.character.position
      local d = (a.x - b.x) ^ 2 + (a.y - b.y) ^ 2
      if not best_d or d < best_d or d == best_d and player.index < best.index then best, best_d = player, d end
    end
  end
  return best, best_d and math.sqrt(best_d) or nil
end

local GOODS = {"coal", "iron-plate", "copper-plate", "steel-plate"}
local VALUE = {coal=1, ["iron-plate"]=2, ["copper-plate"]=2, ["steel-plate"]=10}
local KEEP = {coal=40, ["iron-plate"]=60, ["copper-plate"]=40, ["steel-plate"]=10}

local function open_offer(npc, player)
  for _, deal in ipairs(PlayerTrade.observe(player)) do
    if deal.npc_id == npc.id and deal.status == "pending_player" then return true end
  end
end

-- Terms the NPC would propose: its biggest surplus for what it lacks most,
-- priced by personality and how it feels about the player.
function Character.trade_terms(npc, stance)
  local inventory = npc.character.get_main_inventory()
  if not inventory then return nil end
  local have = {}
  for _, item in ipairs(GOODS) do have[item] = inventory.get_item_count({name=item, quality="normal"}) end
  local give, spare = nil, 0
  for _, item in ipairs(GOODS) do
    local extra = have[item] - KEEP[item]
    if extra >= 20 and extra * VALUE[item] > spare * (give and VALUE[give] or 1) then give, spare = item, extra end
  end
  if not give then return nil end
  local steel = npc.force.technologies and npc.force.technologies["steel-processing"]
  local want, need = nil, math.huge
  for _, item in ipairs(GOODS) do
    local ratio = have[item] / KEEP[item]
    local useful = item ~= "steel-plate" or steel and steel.researched
    if item ~= give and useful and ratio < 1 and ratio < need then want, need = item, ratio end
  end
  if not want then return nil end
  local a = Personality.axes(npc)
  local count = math.min(spare, 10 + math.floor(20 * (a.trade or 0.5)), 100)
  local markup = 1 + 0.4 * (1 - (a.cooperation or 0.5)) - 0.2 * math.max(0, stance or 0)
  markup = math.max(0.8, math.min(1.5, markup))
  local price = math.max(1, math.min(100, math.ceil(count * VALUE[give] / VALUE[want] * markup)))
  return {npc_gives=give, npc_count=count, player_gives=want, player_count=price}
end

-- The item named in a "need:..." or "...wait:..." phase, e.g.
-- "designed-start:need:waiting_for:iron-plate".
local function blocked_item(npc)
  local phase = tostring(npc.phase or "")
  if not (phase:find("need:", 1, true) or phase:find("wait:", 1, true)) then return nil end
  local found
  for token in phase:gmatch("[%w%-]+") do if prototypes.item[token] then found = token end end
  return found
end

local function cooled(npc, name, tick, ticks)
  npc.initiative = npc.initiative or {}
  if tick < (npc.initiative[name] or 0) then return false end
  npc.initiative[name] = tick + ticks
  return true
end

-- One decision per NPC per minute: at most one thing said or offered.
function Character.initiative(npc, tick)
  if npc.quiet then return nil end   -- it chose to keep to itself (its strategist still speaks when it decides to)
  local player, distance = nearest_player(npc)
  if not player then return nil end
  if not (npc.force.get_cease_fire(player.force) and player.force.get_cease_fire(npc.force)) then return nil end
  local key = "player:" .. player.index
  local r, label = relation(npc, key, tick)
  local stance = Relations.stance(npc.id, key, tick)
  local mood = Relations.mood(npc.id, tick)
  local a = Personality.axes(npc)
  local roll = rng(npc)
  if not TeamWorld.active() and (r.grudge >= 40 or label == "enemy")   -- (teammates do not threaten each other)
      and ((a.aggression or 0.5) >= 0.45 or mood.anger >= 40) and cooled(npc, "threat", tick, 72000) then
    record("threat", "npc:" .. npc.id, key, nil, "native")
    speak(npc, "threat", key, nil, {priority=2, tone="hostile"})
    return "threat"
  end
  -- An engineer with a strategist reaches out when it decides to (now and then), not every minute.
  if Character.thinks and Character.thinks(npc) then return nil end
  local item = blocked_item(npc)
  if item and stance >= -0.1 and cooled(npc, "help", tick, 54000) then
    speak(npc, "need_help", key, {item=Voice.pretty(item)}, {priority=2})
    return "need_help"
  end
  if stance >= -0.2 and (a.trade or 0.5) >= 0.3 and roll < 0.25 + 0.5 * (a.trade or 0.5)
      and not open_offer(npc, player) and tick >= ((npc.initiative or {}).trade or 0) then
    local terms = Character.trade_terms(npc, stance)
    if terms then
      local deal = PlayerTrade.offer(npc, player, terms)
      if deal and deal.status == "pending_player" then
        cooled(npc, "trade", tick, math.floor(36000 * (1.5 - (a.trade or 0.5))))
        speak(npc, "trade_offer", key, {deal=deal.id, npc_count=terms.npc_count,
          npc_item=Voice.pretty(terms.npc_gives), player_count=terms.player_count,
          player_item=Voice.pretty(terms.player_gives)}, {player=player, direct=true})
        return "trade_offer"
      end
    end
  end
  if distance and distance <= 40 and (r.affinity >= 10 or (a.sociability or 0.5) >= 0.6 or label == "rival")
      and cooled(npc, "greet", tick, 54000) then
    speak(npc, "greet", key, nil, {priority=2})
    return "greet"
  end
  -- Talk to another engineer nearby: friends compliment, rivals needle.
  for id = 1, MAX_NPC do
    local other = live(id)
    if other and other ~= npc and other.character.surface == npc.character.surface then
      local p, q = other.character.position, npc.character.position
      if (p.x - q.x) ^ 2 + (p.y - q.y) ^ 2 <= 80 * 80 and cooled(npc, "banter", tick, 72000) then
        speak(npc, "banter", "npc:" .. id, nil, {priority=1})
        return "banter"
      end
    end
  end
end

function Character.tick(tick)
  if tick % 60 ~= 0 then return end
  flush_damage(tick)
  for id = 1, MAX_NPC do
    local npc = live(id)
    if npc then
      npc.next_initiative = npc.next_initiative or tick + INITIATIVE + id * 600
      if tick >= npc.next_initiative then
        npc.next_initiative = tick + INITIATIVE
        Character.initiative(npc, tick)
      end
    end
  end
end

PlayerTrade.on_resolved = function(deal) Character.on_trade_resolved(deal) end

return Character
