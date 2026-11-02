-- Bilateral barter with the founding player's current physical character.
-- Worker reaches the exact saved target and calls execute for one atomic tick.
local Personality=require("scripts.personality")
local Relations=require("scripts.relations")
local TeamWorld=require("scripts.team_world")
local PlayerTrade={}
local LIFETIME=60*60*5
local MAX_DEALS=128
local GOODS={coal=1,["iron-plate"]=2,["copper-plate"]=2,["steel-plate"]=10}
local RESERVE={coal=4,["iron-plate"]=8,["copper-plate"]=4,["steel-plate"]=2}

-- Set by the character layer; called once when a deal reaches an outcome.
PlayerTrade.on_resolved=nil
local function resolved(deal)
  if PlayerTrade.on_resolved then
    local ok,problem=pcall(PlayerTrade.on_resolved,deal)
    if not ok and log then log("[npc-factions] trade listener error: "..tostring(problem)) end
  end
end

local function state()
  storage.player_trade=storage.player_trade or {next_id=1,deals={}}
  return storage.player_trade
end
local function npc_live(id)
  local npc=storage.npcs and storage.npcs[id]
  return npc and npc.character and npc.character.valid and npc.force and npc.force.valid
      and npc.character.force==npc.force and npc or nil
end
local function player_live(index)
  local player=game.get_player(index)
  return player and player.valid and player.character and player.character.valid and player or nil
end
local function integer(value,maximum)
  return type(value)=="number" and value==math.floor(value) and value>=1 and value<=maximum
end
local function normal(item,count)
  return {name=item,count=count or 1,quality="normal"}
end
local function plain_count(inventory,item)
  local total=0
  for i=1,#inventory do
    local stack=inventory[i]
    if stack.valid_for_read and stack.name==item and stack.quality.name=="normal"
        and stack.health==1 then total=total+stack.count end
  end
  return total
end
local function remove_plain(inventory,item,count)
  local remaining=count
  for i=1,#inventory do
    if remaining<=0 then break end
    local stack=inventory[i]
    if stack.valid_for_read and stack.name==item and stack.quality.name=="normal"
        and stack.health==1 then
      local n=math.min(remaining,stack.count)
      stack.count=stack.count-n;remaining=remaining-n
    end
  end
  return count-remaining
end
local function restore(inventory,item,count)
  return count>0 and inventory.insert(normal(item,count)) or 0
end
local function find(id)
  for _,deal in ipairs(state().deals) do if deal.id==id then return deal end end
end
local function projection(deal)
  return {id=deal.id,npc_id=deal.npc_id,player_index=deal.player_index,
    npc_gives=deal.npc_gives,npc_count=deal.npc_count,
    player_gives=deal.player_gives,player_count=deal.player_count,
    status=deal.status,expires_tick=deal.expires_tick,npc_accepted=deal.npc_accepted,
    player_accepted=deal.player_accepted,decision_probability=deal.decision_probability,
    receipt_id=deal.receipt_id,job_id=deal.job_id,committed_tick=deal.committed_tick,
    failure_reason=deal.failure_reason}
end
local function founding_pair(npc,player)
  return npc and player and storage.founding_force and player.force==storage.founding_force
      and npc.character.surface==player.character.surface
      and npc.force.get_cease_fire(player.force) and player.force.get_cease_fire(npc.force)
end
-- `large`: a deal an engineer's strategist proposes: any item, in the amounts players trade
-- (hundreds to thousands); otherwise the small barter of the four basic goods.
local function validate_terms(terms,large)
  local known=function(name) return type(name)=="string" and (GOODS[name] or large and prototypes.item[name]) end
  local most=large and 5000 or 100
  if type(terms)~="table" or not known(terms.npc_gives)
      or not known(terms.player_gives) or terms.npc_gives==terms.player_gives
      or not integer(terms.npc_count,most) or not integer(terms.player_count,most) then
    return nil,"invalid_trade_terms"
  end
  return {npc_gives=terms.npc_gives,npc_count=terms.npc_count,
    player_gives=terms.player_gives,player_count=terms.player_count}
end
local function reserved_outbound(npc,item)
  local total=0
  for _,deal in ipairs(state().deals) do
    if deal.npc_id==npc.id and deal.npc_gives==item
        and (deal.status=="accepted" or deal.status=="pending_player")
        and deal.expires_tick>game.tick then total=total+deal.npc_count end
  end
  return total
end
local function stock_ok(npc,terms)
  local inventory=npc.character.get_main_inventory()
  return plain_count(inventory,terms.npc_gives)-reserved_outbound(npc,terms.npc_gives)
      -terms.npc_count>=(RESERVE[terms.npc_gives] or 0)
end
-- The NPC's private threshold for one player and item pair holds for five
-- minutes: asking again cannot re-roll it, only better terms or a better
-- relationship can change the answer.
local function threshold(npc,player,terms)
  local personality=Personality.ensure(npc)
  local text=tostring(personality.seed)..":"..player.index..":"..terms.npc_gives..":"..terms.player_gives
      ..":"..math.floor(game.tick/18000)
  local h=5381
  for i=1,#text do h=(h*33+text:byte(i))%2147483647 end
  return h/2147483647
end
local function independent_npc_accept(npc,terms,player)
  local inventory=npc.character.get_main_inventory()
  if not stock_ok(npc,terms) then
    return false,0,"npc_stock_reserved"
  end
  local axes=Personality.axes(npc)
  local sell=GOODS[terms.npc_gives]*terms.npc_count
  local buy=GOODS[terms.player_gives]*terms.player_count
  local need_bonus=plain_count(inventory,terms.player_gives)<RESERVE[terms.player_gives]*2 and .25 or 0
  local stance=Relations.stance(npc.id,"player:"..player.index,game.tick)
  local score=buy/math.max(1,sell)-1 + axes.trade*.45+axes.cooperation*.25
      -axes.frugality*.35+need_bonus+stance*.4
  local chance=math.max(.05,math.min(.95,.5+score*.55))
  return threshold(npc,player,terms)<chance,chance,nil
end
local function append_deal(deal)
  local s=state()
  while #s.deals>=MAX_DEALS do
    local index
    for i,entry in ipairs(s.deals) do
      if entry.status~="accepted" and entry.status~="pending_player" then index=i;break end
    end
    if not index then return false,"deal_board_full" end
    table.remove(s.deals,index)
  end
  s.deals[#s.deals+1]=deal
  return true
end
local function create(npc,player,terms,npc_accepted,player_accepted,chance)
  local s=state()
  local deal={id=s.next_id,npc_id=npc.id,player_index=player.index,
    npc_gives=terms.npc_gives,npc_count=terms.npc_count,
    player_gives=terms.player_gives,player_count=terms.player_count,
    npc_accepted=npc_accepted,player_accepted=player_accepted,
    decision_probability=chance,created_tick=game.tick,expires_tick=game.tick+LIFETIME,
    target_character=player.character,target_force=player.force.name,
    target_surface_index=player.character.surface.index,
    status=npc_accepted and player_accepted and "accepted"
        or npc_accepted and "pending_player" or "rejected"}
  local added,reason=append_deal(deal)
  if not added then return nil,reason end
  s.next_id=s.next_id+1
  return deal
end

function PlayerTrade.propose(player,npc_id,raw_terms)
  if TeamWorld.active() then return nil,"one_team" end   -- teammates do not barter
  local npc=npc_live(npc_id)
  player=player and player_live(player.index)
  if not founding_pair(npc,player) then return nil,"founder_or_target_unavailable" end
  local terms,reason=validate_terms(raw_terms)
  if not terms then return nil,reason end
  local accepted,chance,why=independent_npc_accept(npc,terms,player)
  local deal,create_reason=create(npc,player,terms,accepted,true,chance)
  if not deal then return nil,create_reason end
  deal.failure_reason=why
  return projection(deal)
end

function PlayerTrade.offer(npc,player,raw_terms,large)
  if TeamWorld.active() then return nil,"one_team" end
  player=player and player_live(player.index)
  if not npc or not npc_live(npc.id) or not founding_pair(npc,player) then
    return nil,"founder_or_target_unavailable"
  end
  local terms,reason=validate_terms(raw_terms,large)
  if not terms then return nil,reason end
  if not stock_ok(npc,terms) then return nil,"npc_stock_reserved" end
  local deal,create_reason=create(npc,player,terms,true,false,1)
  if not deal then return nil,create_reason end
  deal.npc_offered=true
  return projection(deal)
end

function PlayerTrade.accept(player,id)
  local deal=find(id)
  player=player and player_live(player.index)
  local npc=deal and npc_live(deal.npc_id)
  if not deal or deal.status~="pending_player" or deal.player_index~=(player and player.index)
      or deal.expires_tick<=game.tick or not founding_pair(npc,player)
      or deal.target_character~=player.character or deal.target_force~=player.force.name
      or deal.target_surface_index~=player.character.surface.index then
    return nil,"deal_not_pending_for_player"
  end
  deal.player_accepted=true;deal.status="accepted";deal.accepted_tick=game.tick
  return projection(deal)
end

function PlayerTrade.reject(player,id)
  local deal=find(id)
  if not deal or deal.player_index~=(player and player.index)
      or (deal.status~="pending_player" and deal.status~="accepted") then
    return nil,"deal_not_rejectable" end
  deal.status="rejected";deal.failure_reason="player_rejected";deal.resolved_tick=game.tick
  resolved(deal)
  return projection(deal)
end

function PlayerTrade.next(npc,tick)
  if not npc_live(npc.id) or TeamWorld.active() then return nil end
  for _,deal in ipairs(state().deals) do
    if deal.npc_id==npc.id and deal.status=="accepted" and not deal.job_id
        and deal.expires_tick>tick then
      local player=player_live(deal.player_index)
      if founding_pair(npc,player) and deal.target_character==player.character
          and deal.target_force==player.force.name
          and deal.target_surface_index==player.character.surface.index then
        return {kind="player_trade",target=player.character,deal_id=deal.id,
          deadline=math.min(deal.expires_tick,tick+36000)},deal.id
      end
    end
  end
end

function PlayerTrade.bind(npc,deal_id,job_id)
  local deal=find(deal_id)
  if not deal or deal.npc_id~=npc.id or deal.status~="accepted" or deal.job_id then return false end
  deal.job_id=job_id
  return true
end

local function fail(deal,reason)
  deal.status="failed";deal.failure_reason=reason;deal.resolved_tick=game.tick
  resolved(deal)
  return false,reason
end

-- All checks and four inventory mutations occur in one Factorio tick, after
-- Worker has reached the exact target. There are no remote inventory reads.
function PlayerTrade.execute(npc,job)
  local deal=find(job.deal_id)
  local player=deal and player_live(deal.player_index)
  if not deal or deal.status~="accepted" or not deal.npc_accepted
      or not deal.player_accepted or deal.npc_id~=npc.id or deal.job_id~=job.id
      or deal.expires_tick<=game.tick then return false,"deal_unavailable" end
  if npc_live(npc.id)~=npc or not founding_pair(npc,player)
      or job.target~=deal.target_character
      or player.character~=deal.target_character
      or player.force.name~=deal.target_force
      or player.character.surface.index~=deal.target_surface_index then
    return fail(deal,"trade_target_changed")
  end
  if not npc.character.can_reach_entity(player.character) then return false,"trade_out_of_reach" end
  local npc_inv=npc.character.get_main_inventory()
  local player_inv=player.character.get_main_inventory()
  if not npc_inv or not npc_inv.valid or not player_inv or not player_inv.valid then
    return fail(deal,"trade_inventory_missing")
  end
  if plain_count(npc_inv,deal.npc_gives)-deal.npc_count<(RESERVE[deal.npc_gives] or 0)
      or plain_count(player_inv,deal.player_gives)<deal.player_count then
    if plain_count(player_inv,deal.player_gives)<deal.player_count then deal.blame="player" end
    return fail(deal,"trade_source_short")
  end
  if npc_inv.get_insertable_count{name=deal.player_gives,quality="normal"}<deal.player_count
      or player_inv.get_insertable_count{name=deal.npc_gives,quality="normal"}<deal.npc_count then
    return fail(deal,"trade_destination_full")
  end
  local removed_npc=remove_plain(npc_inv,deal.npc_gives,deal.npc_count)
  local removed_player=remove_plain(player_inv,deal.player_gives,deal.player_count)
  if removed_npc~=deal.npc_count or removed_player~=deal.player_count then
    restore(npc_inv,deal.npc_gives,removed_npc)
    restore(player_inv,deal.player_gives,removed_player)
    return fail(deal,"trade_remove_short")
  end
  local inserted_npc=npc_inv.insert(normal(deal.player_gives,deal.player_count))
  local inserted_player=player_inv.insert(normal(deal.npc_gives,deal.npc_count))
  if inserted_npc~=deal.player_count or inserted_player~=deal.npc_count then
    remove_plain(npc_inv,deal.player_gives,inserted_npc)
    remove_plain(player_inv,deal.npc_gives,inserted_player)
    local returned_npc=restore(npc_inv,deal.npc_gives,removed_npc)
    local returned_player=restore(player_inv,deal.player_gives,removed_player)
    if returned_npc~=removed_npc or returned_player~=removed_player then
      -- Physical spill preserves material if another mod unexpectedly changes
      -- inventory capacity during this synchronous tick.
      if returned_npc<removed_npc then npc.character.surface.spill_item_stack{
        position=npc.character.position,stack=normal(deal.npc_gives,removed_npc-returned_npc),
        allow_belts=false} end
      if returned_player<removed_player then player.character.surface.spill_item_stack{
        position=player.character.position,stack=normal(deal.player_gives,removed_player-returned_player),
        allow_belts=false} end
      return fail(deal,"trade_rollback_world_spill")
    end
    return fail(deal,"trade_insert_short_rolled_back")
  end
  deal.status="committed";deal.committed_tick=game.tick
  deal.resolved_tick=game.tick
  deal.committed={npc_gives=deal.npc_gives,npc_count=deal.npc_count,
    player_gives=deal.player_gives,player_count=deal.player_count}
  resolved(deal)
  return true,{deal_id=deal.id,npc_gives=deal.npc_gives,npc_count=deal.npc_count,
    player_gives=deal.player_gives,player_count=deal.player_count}
end

function PlayerTrade.on_receipt(npc,receipt)
  local deal=receipt and receipt.deal_id and find(receipt.deal_id)
  if not deal or deal.npc_id~=npc.id or deal.job_id~=receipt.job_id
      or deal.receipt_id then return false end
  deal.receipt_id=receipt.id;deal.worker_status=receipt.status
  if receipt.status~="complete" and deal.status~="committed"
      and deal.status~="rejected" and deal.status~="expired" then
    deal.status="failed";deal.failure_reason=receipt.reason or "worker_failed"
    deal.resolved_tick=game.tick
    resolved(deal)
  end
  return true
end

function PlayerTrade.expire(tick)
  for _,deal in ipairs(state().deals) do
    if (deal.status=="accepted" or deal.status=="pending_player")
        and tick>=deal.expires_tick then
      deal.status="expired";deal.resolved_tick=tick
      resolved(deal)
    end
  end
end

function PlayerTrade.observe(player)
  local out={}
  if not player then return out end
  for _,deal in ipairs(state().deals) do
    if deal.player_index==player.index then out[#out+1]=projection(deal) end
  end
  return out
end

return PlayerTrade
