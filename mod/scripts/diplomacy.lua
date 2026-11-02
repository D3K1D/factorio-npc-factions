-- Authoritative treaty actions shared by structured commands and local chat.
local Diplomacy = {}
local Strategy=require("scripts.strategy_policy")
local PlayerTrade=require("scripts.player_trade")
local Relations=require("scripts.relations")
local Character=require("scripts.character")
local Teach=require("scripts.teach")
local Aggro=require("scripts.aggro")
local TeamWorld=require("scripts.team_world")
local REFUSAL_COOLDOWN = 60 * 60 * 5

local MAX_NPC_ID = 10
local MAX_HISTORY = 64
local OFFER_LIFETIME = 1800
local NPC_OFFER_LIFETIME = 18000   -- an engineer's own offer to a player stands for 5 minutes

local function reply(player, message)
  player.print("[NPC] " .. message)
end

local function diplomacy()
  return storage.diplomacy
end

local function force_of(value)
  if value == nil then return nil end
  if type(value) == "string" or type(value) == "number" then
    local force = game.forces[value]
    return force and force.valid and force or nil
  end
  return value.valid and value or nil
end

local function npc_by_id(token)
  local id = tonumber(token)
  if not id or id % 1 ~= 0 or id < 1 or id > MAX_NPC_ID then return nil end
  local npc = storage.npcs and storage.npcs[id]
  if not npc or npc.id ~= id or not npc.character or not npc.character.valid then return nil end
  local force = force_of(npc.force)
  if not force or npc.character.force ~= force then return nil end
  return npc, force
end

local function founder_force()
  return force_of(storage.founding_force)
end

local function can_negotiate(player)
  local founder = founder_force()
  if storage.founding_force ~= nil and not founder then return false end
  return not founder or player.force == founder
end

local function pair_key(a, b)
  local first, second = a.name, b.name
  if second < first then first, second = second, first end
  return first .. "|" .. second
end

local function current_state(a, b)
  if a.get_friend(b) and b.get_friend(a) then return "allied" end
  if a.get_cease_fire(b) and b.get_cease_fire(a) then return "peace" end
  return "hostile"
end

local function trim_history(list)
  while #list > MAX_HISTORY do table.remove(list, 1) end
end

local function record(message)
  local state = diplomacy()
  state.transcript[#state.transcript + 1] = {tick = game.tick, text = message}
  trim_history(state.transcript)
end

local function make_offer(kind, proposer, scope, a, b)
  local state = diplomacy()
  local offer = {
    id = state.next_offer_id,
    kind = kind,
    proposer = proposer,
    scope = scope,
    parties = {a.name, b.name},
    created_tick = game.tick,
    expires_tick = game.tick + OFFER_LIFETIME,
    status = "pending",
    revision = 1
  }
  state.next_offer_id = state.next_offer_id + 1
  state.offers[#state.offers + 1] = offer
  trim_history(state.offers)
  return offer
end

-- Who the NPC is dealing with: the asking player, or another engineer's force.
local function counterpart(other, player)
  if player then return "player:" .. player.index end
  local other_id=tonumber(other.name:match("^npc%-faction%-(%d+)$"))
  return other_id and "npc:" .. other_id or "force:" .. other.name
end

-- Relationship first (grudges refuse, strong trust agrees to peace), then the
-- learned policy. A refusal is remembered, so asking again does not re-roll it.
local function accepts(npc, other, kind, player)
  if kind == "war" then return false, "War is a declaration, not an NPC offer." end
  local key = counterpart(other, player)
  local tick = game.tick
  local topic = "treaty:" .. kind
  if Relations.refused_recently(npc.id, key, topic, tick) then
    return false, "I already gave you my answer. Give it some time."
  end
  local r = Relations.get(npc, key, tick)
  local stance = Relations.stance(npc.id, key, tick)
  local accepted, reason
  if stance <= -0.35 or r.grudge >= 50 then
    accepted, reason = false, "I haven't forgotten what happened between us."
  elseif kind == "peace" and stance >= 0.4 then
    accepted, reason = true, "You've been fair with me."
  else
    local other_id=tonumber(other.name:match("^npc%-faction%-(%d+)$")) or 0
    local choice,token=Strategy.choose(npc,{{id="accept",action="accept",target_id=other_id},
      {id="reject",action="reject",target_id=other_id}},
      {contacts={{id=other_id,key=key,trust=r.trust,force_relation=current_state(npc.force,other)}}})
    -- Resolving words is not evidence of a profitable/successful physical action.
    -- Economic/combat outcomes provide the learned reward in their own adapters.
    if token then Strategy.close(npc,token,"player_treaty_decision:"..kind) end
    accepted = choice and choice.action=="accept" or false
    if accepted then reason = "It makes sense for both of us."
    elseif r.trust < -5 then reason = "I don't trust you enough yet."
    else reason = "Not now. My situation doesn't call for it." end
  end
  if not accepted then Relations.note_refusal(npc.id, key, topic, tick, REFUSAL_COOLDOWN) end
  return accepted, reason
end

local function set_relationship(a, b, kind)
  local old = {
    a_friend = a.get_friend(b), b_friend = b.get_friend(a),
    a_cease = a.get_cease_fire(b), b_cease = b.get_cease_fire(a)
  }
  local friend = kind == "ally"
  local cease = kind ~= "war"
  local ok, err = pcall(function()
    a.set_friend(b, friend)
    b.set_friend(a, friend)
    a.set_cease_fire(b, cease)
    b.set_cease_fire(a, cease)
  end)
  if not ok then
    -- Restore any directional flag changed before the engine rejected a call.
    pcall(function()
      a.set_friend(b, old.a_friend)
      b.set_friend(a, old.b_friend)
      a.set_cease_fire(b, old.a_cease)
      b.set_cease_fire(a, old.b_cease)
    end)
    return false, err
  end
  return true
end

local function commit(offer, a, b)
  local ok = set_relationship(a, b, offer.kind)
  if not ok then
    offer.status = "failed"
    record("Offer #" .. offer.id .. " failed while applying force relationship flags.")
    return false
  end
  offer.status = "accepted"
  offer.resolved_tick = game.tick
  local state = diplomacy()
  state.contracts[pair_key(a, b)] = {
    kind = offer.kind,
    parties = {a.name, b.name},
    proposer = offer.proposer,
    scope = offer.scope,
    offer_id = offer.id,
    revision = offer.revision,
    accepted_tick = game.tick
  }
  state.relations[pair_key(a, b)] = {
    status = current_state(a, b),
    changed_tick = game.tick,
    offer_id = offer.id
  }
  record("Offer #" .. offer.id .. " accepted: " .. a.name .. " and " .. b.name .. " are " .. current_state(a, b) .. ".")
  return true
end

local function tokens(parameter)
  local result = {}
  for token in (parameter or ""):gmatch("%S+") do
    result[#result + 1] = token:lower()
    if #result > 4 then break end
  end
  return result
end

local function help(player)
  if TeamWorld.active() then
    -- the team world: one team, no treaties and no trading
    reply(player, "The engineers are on your team: allies from the start, one tech tree with you, each in its own plot round yours.")
    reply(player, "You run the research queue. They add what they want at the end, and anything of theirs you take out stays out.")
    reply(player, "Talk to them in chat: Ada: what are you building? (everyone: ... reaches all of them). /npc status [1-10] shows each one.")
    reply(player, "The Engineers button on the shortcut bar answers a question with Mark location, shows them part of your base, or teaches a blueprint or book (also /npc teach [engineer]).")
    return
  end
  reply(player, "Engineers start at peace with you: a truce, not a team. Hurt one enough (or destroy its buildings) and it goes to war with you; its turrets and yours then fire on sight.")
  reply(player, "Use /npc status [1-10], /npc peace <id>, /npc ally <id>, /npc war <id>, or /npc pact <id> <id>.")
  reply(player, "Alliance grants both forces unrestricted building access and prevents turret targeting. Each faction keeps its own force and research.")
  reply(player,"You can also type Ada: can we team up? or Borin: what are you building? in chat (everyone: ... reaches every engineer).")
  reply(player,"Barter: Ada: trade 20 coal for 10 iron. Review /npc trades; accept /npc accept <deal-id> or cancel /npc reject <deal-id> before exchange.")
  reply(player,"The Engineers button on the shortcut bar shows where everyone stands, answers a question with Mark location, shows them part of your base, or teaches a blueprint or book (also /npc teach [engineer], or say Ada: learn this).")
end

local function show_status(player, token)
  local state = diplomacy()
  if not state then reply(player, "Diplomacy is not initialized yet."); return end
  local own_force = player.force
  if token then
    local npc, force = npc_by_id(token)
    if not npc then reply(player, "Choose a living NPC ID from 1 to 10."); return end
    reply(player, npc.name .. " (#" .. npc.id .. "): " .. current_state(own_force, force) .. " with your force.")
    return
  end
  local any = false
  for id = 1, MAX_NPC_ID do
    local npc, force = npc_by_id(tostring(id))
    if npc then
      any = true
      reply(player, npc.name .. " (#" .. id .. "): " .. current_state(own_force, force) .. " with your force.")
    end
  end
  if not any then reply(player, "No living NPC factions are available yet.") end
end

local function resolve_player_offer(player, kind, token)
  if not can_negotiate(player) then reply(player, "Only a player on the founding force can negotiate for it."); return end
  local npc, other = npc_by_id(token)
  if not npc then reply(player, "Choose a living NPC ID from 1 to 10."); return end
  local own = player.force
  if not own or not own.valid or own == other then reply(player, "That force is not a valid treaty partner."); return end
  local offer = make_offer(kind, "player:" .. player.index, "founding-force-to-npc", own, other)
  if kind ~= "war" then
    -- What the engineer offered itself is already its yes.
    local state = diplomacy()
    local key = npc.id .. "|" .. player.index
    local offered = state.npc_offers and state.npc_offers[key]
    local accepted, reason
    if offered and offered.kind == kind and game.tick - offered.tick <= NPC_OFFER_LIFETIME then
      accepted, reason = true, "I offered it."
      state.npc_offers[key] = nil
    else
      accepted, reason = accepts(npc, own, kind, player)
    end
    offer.npc_decision = {npc_id = npc.id, accepted = accepted, reason = reason}
    if not accepted then
      offer.status = "rejected"
      offer.resolved_tick = game.tick
      record("Offer #" .. offer.id .. " rejected by " .. npc.name .. ": " .. reason)
      reply(player, npc.name .. ": I decline " .. kind .. ". " .. reason)
      return
    end
  end
  if not commit(offer, own, other) then reply(player, "Treaty change failed; previous relation was restored."); return end
  if kind ~= "war" then Aggro.forgive(npc, "player:" .. player.index) end
  Character.on_player_treaty(npc, player, kind, true)
  if kind == "ally" then
    reply(player, npc.name .. " accepted alliance. Both forces now have unrestricted building access and turrets will not target each other. Research remains separate.")
  elseif kind == "peace" then
    reply(player, npc.name .. " accepted peace. Both forces now have a cease-fire, without building access.")
  else
    reply(player, "War declared on " .. npc.name .. ". Both forces are now hostile: its turrets and yours fire on sight.")
  end
end

local function resolve_pact(player, first, second)
  if not can_negotiate(player) then reply(player, "Only a player on the founding force can broker a pact."); return end
  local a, force_a = npc_by_id(first)
  local b, force_b = npc_by_id(second)
  if not a or not b or a.id == b.id or force_a == force_b then
    reply(player, "Choose two distinct living NPC IDs from 1 to 10.")
    return
  end
  local offer = make_offer("ally", "player:" .. player.index, "npc-to-npc", force_a, force_b)
  local accept_a, reason_a = accepts(a, force_b, "ally")
  local accept_b, reason_b = accepts(b, force_a, "ally")
  offer.npc_decisions = {
    {npc_id = a.id, accepted = accept_a, reason = reason_a},
    {npc_id = b.id, accepted = accept_b, reason = reason_b}
  }
  if not (accept_a and accept_b) then
    offer.status = "rejected"
    offer.resolved_tick = game.tick
    record("Offer #" .. offer.id .. " rejected: " .. a.name .. " " .. reason_a .. " " .. b.name .. " " .. reason_b)
    reply(player, "Pact declined. " .. a.name .. ": " .. reason_a .. " " .. b.name .. ": " .. reason_b)
    return
  end
  if not commit(offer, force_a, force_b) then reply(player, "Pact change failed; previous relation was restored."); return end
  reply(player, a.name .. " and " .. b.name .. " accepted an alliance. Their forces now have unrestricted building access and reciprocal cease-fire; research remains separate.")
end

function Diplomacy.trade_command(player,action,id,npc_id)
  if not player or not player.valid then return false end
  if TeamWorld.active() then reply(player,"No trading on one team: teammates just help each other.");return true end
  local deals=PlayerTrade.observe(player)
  if action=="trades" then
    local shown=0
    for i=#deals,1,-1 do local deal=deals[i]
      if not npc_id or deal.npc_id==npc_id then
        local npc=storage.npcs and storage.npcs[deal.npc_id]
        reply(player,"Deal #"..deal.id.." with "..(npc and npc.name or "engineer #"..deal.npc_id)..": "..
          deal.status..". You give "..deal.player_count.." "..deal.player_gives:gsub("-"," ")..
          "; they give "..deal.npc_count.." "..deal.npc_gives:gsub("-"," ")..".")
        shown=shown+1;if shown>=10 then break end
      end
    end
    if shown==0 then reply(player,"No barter deals to show.") end
    return true
  end
  if action~="accept" and action~="reject" then return false end
  id=tonumber(id)
  local found
  for _,deal in ipairs(deals) do
    if deal.id==id and (not npc_id or deal.npc_id==npc_id) then found=true;break end
  end
  if not found then reply(player,"That deal is not yours with this engineer.");return true end
  local deal,reason
  if action=="accept" then deal,reason=PlayerTrade.accept(player,id)
  else deal,reason=PlayerTrade.reject(player,id) end
  if not deal then reply(player,"That deal cannot be "..(action=="accept" and "accepted" or "canceled")..": "..tostring(reason)..".")
  elseif action=="accept" then reply(player,"Deal #"..deal.id.." accepted. The engineer will walk to you to exchange the agreed goods.")
  else reply(player,"Deal #"..deal.id.." canceled. The exchange will not take place.") end
  return true
end

local function on_command(command)
  if not command.player_index then return end -- No server/RCON negotiation.
  local player = game.get_player(command.player_index)
  if not player or not player.valid then return end
  local args = tokens(command.parameter)
  local action = args[1] or "help"
  if TeamWorld.active() and not (action == "help" or action == "status" or action == "teach") then
    help(player); return   -- one team: no treaties, no trades
  end
  if action == "help" and #args == 1 then help(player)
  elseif action == "status" and #args <= 2 then show_status(player, args[2])
  elseif action=="trades" and #args==1 then Diplomacy.trade_command(player,"trades")
  elseif action=="teach" and #args<=2 then reply(player, Teach.from_cursor(player, args[2]))
  elseif (action=="accept" or action=="reject") and #args==2 then Diplomacy.trade_command(player,action,args[2])
  elseif (action == "peace" or action == "ally" or action == "war") and #args == 2 then
    if not diplomacy() then reply(player, "Diplomacy is not initialized yet."); return end
    resolve_player_offer(player, action, args[2])
  elseif action == "pact" and #args == 3 then
    if not diplomacy() then reply(player, "Diplomacy is not initialized yet."); return end
    resolve_pact(player, args[2], args[3])
  else help(player) end
end

function Diplomacy.talk(player,kind,id,peer_id)
  if not player or not player.valid or not can_negotiate(player) or not diplomacy() then return false,"not_authorized_to_negotiate" end
  if kind=="status" then show_status(player,tostring(id));return true end
  if kind=="help" then help(player);return true end
  if TeamWorld.active() then help(player);return false,"one_team" end
  if kind=="ally" and peer_id then resolve_pact(player,tostring(id),tostring(peer_id));return true end
  if kind=="peace" or kind=="ally" or kind=="war" then
    resolve_player_offer(player,kind,tostring(id));return true
  end
  return false,"unsupported_treaty"
end

-- Where two forces stand, as the game sees it: "allied", "peace" (a cease-fire) or "war".
function Diplomacy.state(a, b)
  local state = current_state(a, b)
  return state == "hostile" and "war" or state
end

-- An engineer's own change of stance toward a force (war when it has had enough of a player, an
-- alliance when it joins one): the same flags, contract and transcript as a negotiated treaty.
function Diplomacy.declare(npc, force, kind)
  if not diplomacy() then return false, "not_initialized" end
  if TeamWorld.active() then return false, "one_team" end   -- allies for good, nothing to declare
  local offer = make_offer(kind, "npc:" .. npc.id, "npc-to-force", npc.force, force)
  if not commit(offer, npc.force, force) then return false, "flags" end
  return true
end

-- An engineer's own move toward a player: war is declared outright; peace or an alliance is offered,
-- and the player takes it with /npc peace|ally <id> (the offer counts as the engineer's yes).
function Diplomacy.npc_to_player(npc, player, action)
  local state = diplomacy()
  if not state or not player or not player.valid then return false, "player" end
  if TeamWorld.active() then return false, "one_team" end
  local now = Diplomacy.state(npc.force, player.force)
  if action == "declare_war" then
    if now == "war" then return false, "already_at_war" end
    local ok, why = Diplomacy.declare(npc, player.force, "war")
    if not ok then return false, why end
    game.print("[NPC] " .. npc.name .. " declares war on " .. player.name .. ".")
    return true
  end
  local kind = ({offer_alliance = "ally", offer_peace = "peace"})[action]
  if not kind then return false, "action" end
  if (kind == "ally" and now == "allied") or (kind == "peace" and now == "peace") then
    return false, "already_" .. now
  end
  state.npc_offers = state.npc_offers or {}
  state.npc_offers[npc.id .. "|" .. player.index] = {kind = kind, tick = game.tick}
  reply(player, npc.name .. " offers you " .. (kind == "ally" and "an alliance" or "peace") .. ": type /npc "
    .. kind .. " " .. npc.id .. " to accept.")
  return true
end

-- Engineers start out at peace with the players (a cease-fire, nobody's team). A save from before
-- that, where they began as allies, keeps only the alliances made since: an accepted treaty or a
-- player an engineer joined.
function Diplomacy.migrate_start_alliances(founder)
  local state = diplomacy()
  if not state or not founder or not founder.valid then return 0 end
  local changed = 0
  for _, npc in pairs(storage.npcs or {}) do
    local force = force_of(npc.force)
    local contract = force and state.contracts[pair_key(force, founder)]
    if force and current_state(force, founder) == "allied" and not npc.joined
        and not (contract and contract.kind == "ally") then
      if set_relationship(force, founder, "peace") then changed = changed + 1 end
    end
  end
  return changed
end

function Diplomacy.register_commands()
  commands.add_command("npc", "Talk to an NPC faction: /npc help", on_command)
end

function Diplomacy.init()
  storage.diplomacy = {
    offers = {}, contracts = {}, relations = {}, transcript = {}, next_offer_id = 1,
    policy = {}, trust = {}
  }
end

local TAG = {war = {text = "at war with you", color = {1, 0.3, 0.25}},
  allied = {text = "your ally", color = {0.45, 1, 0.45}}}
local function stance_tags()
  local players = founder_force() or game.forces.player
  if not players then return end
  for _, npc in pairs(storage.npcs or {}) do
    local c = npc.character
    if c and c.valid then
      local now = Diplomacy.state(npc.force, players)
      if npc.stance_shown ~= now then
        if npc.stance_tag and npc.stance_tag.valid then npc.stance_tag.destroy() end
        npc.stance_tag = nil
        local tag = TAG[now]
        if tag then
          npc.stance_tag = rendering.draw_text{text = tag.text, target = c, surface = c.surface,
            target_offset = {0, -2.75}, color = tag.color, alignment = "center", scale = 0.8}
        end
        npc.stance_shown = now
      end
    end
  end
end

function Diplomacy.tick(tick)
  local state = diplomacy()
  if not state then return end
  if tick % 120 == 7 then stance_tags() end
  -- Offers resolve synchronously today; expiry also protects future queued offers.
  for _, offer in ipairs(state.offers) do
    if offer.status == "pending" and tick >= offer.expires_tick then
      offer.status = "expired"
      offer.resolved_tick = tick
      record("Offer #" .. offer.id .. " expired.")
    end
  end
end

return Diplomacy
