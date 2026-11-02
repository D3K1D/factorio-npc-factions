-- The team world's shared stores. One team, no trading: an engineer short of something takes it from
-- a teammate's chests (leaving them a share), and asks a player in chat before touching theirs. Each of
-- a player's chests is asked about once, by one engineer, and a player has one question open at a time.
-- A yes covers that chest, that item and that many for half an hour; a no (or no answer within a
-- minute) leaves the chest alone for good and that item unasked for a while.
local TeamWorld=require("scripts.team_world")
local Voice=require("scripts.voice")
local TeamSupply={}

local REACH=640          -- chests this far from the engineer are worth the walk
local LEAVE_SHARE=0.2    -- a teammate keeps a fifth of what a chest holds
local LEAVE_MIN=10       -- and never less than this
local ANSWER_WAIT=3600   -- a question waits a minute for its answer
local REMEMBER=108000    -- a yes or a no holds for 30 game minutes
local SILENT=36000       -- no answer: that player is not asked about that item for 10 minutes
local MISS=600           -- nothing anywhere: not looked for again for 10 seconds
local LIST_FOR=600       -- the team's chests are listed again every 10 seconds (plans hold thousands of buildings)
local CHESTS={"container","logistic-container"}

-- (apostrophes are dropped before matching: "don't" is "dont")
-- Answers to "mind if I grab...?" that sound like no but mean yes: read first, as a plain yes.
local IDIOMS={"no problem","not a problem","no worries","not at all","i dont mind","dont mind","why not"}
local YES={"go ahead","take it","help yourself","of course","go for it","all yours","yes","yeah","yep","yup",
  "sure","ok","okay","fine","np","k"}
-- Any of these left in the answer makes it no: a refusal, or too mixed to take as a yes.
local NO={"no","nope","nah","dont","do not","not","never","stop","leave","wont","cant","mine"}
local ADDRESSED_WORDS,PLAIN_WORDS=8,4   -- longer than this is talk, not an answer
-- An answer is made of these words only (and the engineers' names): "yes build labs" is talk, not a yes.
local REPLY_WORDS={}
for _,list in ipairs({IDIOMS,YES,NO,{"please","thanks","thank","you","mate","man","dude","buddy","bro","lol",
    "haha","yea","ya","ye","of","course","grab","take","them","some","what","need","whatever","the","it",
    "just","sorry","hey","yo","ok","alright","right","those","that","i","dont","mind","go","ahead"}}) do
  for _,phrase in ipairs(list) do for w in phrase:gmatch("%S+") do REPLY_WORDS[w]=true end end
end

local function state()
  storage.team_supply=storage.team_supply or {asks={},answers={},miss={}}
  local s=storage.team_supply
  s.asked=s.asked or {}   -- every player's chest an engineer has asked about, and what came of it
  return s
end

-- (a chest by where it stands: one put back in the same place is the same chest)
local function chest_key(chest)
  return string.format("%s:%d,%d",chest.surface.name,math.floor(chest.position.x),math.floor(chest.position.y))
end

local function distance2(a,b) return (a.x-b.x)^2+(a.y-b.y)^2 end

local function inventory_of(e) return e.get_inventory(defines.inventory.chest) end

local function player_of(owner)
  return owner and owner.valid and owner.object_name=="LuaPlayer" and owner or nil
end

local function npc_force(force)
  return force and force.valid and force.name:find("^npc%-faction%-")~=nil
end

-- A teammate's chests worth taking from: its storage chests and the chests of its plans (a bus end, a
-- mall), but not the chests it fills by hand to feed its own lines.
local function teammate_chests(other)
  local out,seen={},{}
  local function add(e) if e and e.valid and not seen[e] then seen[e]=true; out[#out+1]=e end end
  local plans={other.design and other.design.plan}
  for _,old in ipairs(other.design_history or {}) do plans[#plans+1]=old end
  for _,plan in ipairs(plans) do
    local fed={}
    for _,stage in ipairs(plan.stages or {}) do
      for _,supply in ipairs(stage.supplies or {}) do fed[supply.entity_id]=true end
    end
    for _,id in ipairs(plan.order or {}) do
      local e=plan.entities[id].entity
      if e and e.valid and not fed[id] and (e.type=="container" or e.type=="logistic-container") then add(e) end
    end
  end
  for _,chest in ipairs(other.storage_chests or {}) do add(chest) end
  return out
end

-- Every engineer's store chests, listed now and then: {chest, owner id, owner name}.
local function team_chests(tick)
  local s=state()
  local cache=s.chests
  if cache and tick<cache.until_tick then return cache.list end
  local list={}
  for _,other in pairs(storage.npcs or {}) do
    for _,chest in ipairs(teammate_chests(other)) do list[#list+1]={chest=chest,owner=other.id,name=other.name} end
  end
  s.chests={list=list,until_tick=tick+LIST_FOR}
  return list
end

-- What a teammate can spare from a chest: all but a fifth (and at least LEAVE_MIN stay).
local function spare(count)
  return math.max(0,count-math.max(LEAVE_MIN,math.ceil(count*LEAVE_SHARE)))
end

local function answer_of(player_index,item,tick)
  local a=state().answers[player_index..":"..item]
  if a and tick<a.until_tick then return a end
end

-- What came of a question (yes, no, or none in time): its chest is not asked about again; a no or
-- silence also keeps that player from being asked about that item for a while. A word already given
-- that lasts longer stands (a question that ran out never undoes a player's no).
local function settle(s,ask,yes,tick,silent)
  if ask.chest then
    s.asked[ask.chest]={yes=yes,item=ask.item,left=yes and ask.count or 0,until_tick=tick+REMEMBER,
      silent=silent or nil}
  end
  if not yes then
    local key=ask.player_index..":"..ask.item
    local until_tick=tick+(silent and SILENT or REMEMBER)
    local had=s.answers[key]
    if not (had and had.until_tick>=until_tick) then
      s.answers[key]={yes=false,silent=silent or nil,until_tick=until_tick}
    end
  end
end

-- Questions nobody answered within a minute run out, whether or not the engineer that asked is still
-- waiting (it may have gone on to something else).
local function expire(s,tick)
  for id,a in pairs(s.asks) do
    if tick-a.tick>=ANSWER_WAIT then settle(s,a,false,tick,true); s.asks[id]=nil end
  end
end

-- May this engineer withdraw `item` from `target` (another force's chest)? Teammates' chests yes, a
-- player's only while that player's yes for that chest and item stands.
function TeamSupply.may_withdraw(npc,target,item,tick)
  if not TeamWorld.active() or not (target and target.valid) then return false end
  if target.type~="container" and target.type~="logistic-container" then return false end
  local own,other=npc.character.force,target.force
  if npc_force(other) then return own.get_friend(other) and other.get_friend(own) end
  if other==game.forces.player then
    local a=state().asked[chest_key(target)]
    return a~=nil and a.yes==true and a.item==item and (tick or game.tick)<a.until_tick
  end
  return false
end

local function grab(chest,item,count)
  return {kind="transfer",target=chest,item=item,count=count,mode="withdraw"}
end

-- Whether the team could hand over `item`, as TeamSupply.take would find it (read only: no job, no word said, no
-- question asked): a teammate's chest within reach with some to spare, or a player's chest holding some that its player
-- could say yes to. A research witness is not dropped while its packs can be fetched so (DesignedStart.pack_at_hand, R-7).
function TeamSupply.has(npc,item,tick)
  if not TeamWorld.active() or not (npc.character and npc.character.valid) then return false end
  local c=npc.character
  for _,row in ipairs(team_chests(tick or game.tick)) do
    local chest=row.chest
    if row.owner~=npc.id and chest.valid and chest.surface==c.surface and c.force.get_friend(chest.force)
        and spare(inventory_of(chest).get_item_count({name=item,quality="normal"}))>0
        and distance2(c.position,chest.position)<=REACH*REACH then
      return true
    end
  end
  tick=tick or game.tick
  local s=state()
  for _,chest in ipairs(c.surface.find_entities_filtered{type=CHESTS,force=game.forces.player,position=c.position,
      radius=REACH}) do
    local owner=player_of(chest.last_user)
    if owner and inventory_of(chest).get_item_count({name=item,quality="normal"})>0 then
      -- (as take: a yes standing for it, a question still open, or one it could ask; not after a no or silence, nor
      -- of a player not online)
      local said=s.asked[chest_key(chest)]
      if said then
        -- (open: about this item, and not past its wait; a yes: for it, still standing, some left)
        if said.item==item and tick<said.until_tick and (said.pending or (said.yes and said.left>0)) then return true end
      elseif owner.connected and not answer_of(owner.index,item,tick) then
        return true
      end
    end
  end
  return false
end

-- A job that gets `missing` of `item` from the team: the nearest teammate's chest with some to spare,
-- else a player's chest that player said yes to. With ask_players, a player's chest that holds it gets
-- a question (and the engineer waits up to a minute for the answer). Nil (and why) when there is none.
-- `worth(chest,n)`: whether the trip to that chest beats getting it otherwise (DesignedStart.fetch_worth); none, any
-- chest in REACH is. Five stone from a teammate's chest 420 tiles off, with rocks by the losses it wanted them for, left
-- three rebuilds standing five minutes (rehearsal 7g, 2026-10-29).
function TeamSupply.take(npc,item,missing,tick,ask_players,worth)
  if not TeamWorld.active() or not (npc.character and npc.character.valid) then return nil end
  local s=state()
  local miss_key=npc.id..":"..item
  if tick<(s.miss[miss_key] or 0) then return nil end
  local c=npc.character
  local here=c.position
  -- a teammate's chests: taken from freely
  local rows,rejected={},false
  for _,row in ipairs(team_chests(tick)) do
    local chest=row.chest
    if row.owner~=npc.id and chest.valid and chest.surface==c.surface and c.force.get_friend(chest.force) then
      local n=spare(inventory_of(chest).get_item_count({name=item,quality="normal"}))
      local d=distance2(here,chest.position)
      if n>0 and d<=REACH*REACH then rows[#rows+1]={chest=chest,d=d,n=n,name=row.name} end
    end
  end
  table.sort(rows,function(a,b) return a.d<b.d end)
  -- (the nearest worth the walk: one too far, or too few in it, gives way to the next)
  local best,best_n,best_name
  for _,r in ipairs(rows) do
    if not worth or worth(r.chest,r.n) then best,best_n,best_name=r.chest,r.n,r.name; break end
    rejected=true
  end
  if best then
    Voice.say(npc,"team_grab",{count=math.min(best_n,missing),item=Voice.pretty(item),target=best_name},{priority=1})
    return grab(best,item,math.min(best_n,missing)),nil,"team_chest"
  end
  -- a player's chests: only with that player's yes, each chest asked about once
  expire(s,tick)
  local ask=s.asks[npc.id]
  if ask and ask.item==item then return nil,"waiting_for_answer:"..ask.player_name end
  local waiting={}   -- players with a question open: one at a time, whoever asked it
  for _,a in pairs(s.asks) do waiting[a.player_index]=true end
  local candidates=c.surface.find_entities_filtered{type=CHESTS,force=game.forces.player,position=here,radius=REACH}
  table.sort(candidates,function(a,b) return distance2(here,a.position)<distance2(here,b.position) end)
  for _,chest in ipairs(candidates) do
    local n=inventory_of(chest).get_item_count({name=item,quality="normal"})
    local owner=player_of(chest.last_user)
    if n>0 and owner and worth and not worth(chest,n) then rejected=true end
    if n>0 and owner and not (worth and not worth(chest,n)) then
      local key=chest_key(chest)
      local said=s.asked[key]
      if said then
        if said.yes and said.item==item and tick<said.until_tick and said.left>0 then
          local take_n=math.min(n,missing,said.left)
          said.left=said.left-take_n
          return grab(chest,item,take_n),nil,"player_chest"
        end
      elseif ask_players and not ask and owner.connected and not waiting[owner.index]
          and not answer_of(owner.index,item,tick) then
        local count=math.min(n,math.max(missing,10))
        s.asks[npc.id]={player_index=owner.index,player_name=owner.name,item=item,count=count,tick=tick,chest=key}
        s.asked[key]={pending=true,item=item,left=0,until_tick=tick+ANSWER_WAIT}
        Voice.say(npc,"ask_chest",{target=owner.name,count=count,item=Voice.pretty(item),
          where=string.format("[gps=%d,%d,%s]",math.floor(chest.position.x),math.floor(chest.position.y),
            chest.surface.name)},{player=owner,direct=true})
        return nil,"waiting_for_answer:"..owner.name
      end
    end
  end
  -- (none at all: not looked for again a while; some not worth it now: looked at again, the walk or the need may change)
  if not rejected then s.miss[miss_key]=tick+MISS end
  return nil
end

local function has(t,words)
  for _,w in ipairs(words) do
    if t:find(" "..w.." ",1,true) then return true end
  end
  return false
end

-- "yes", "no", or nil when the line is no answer: short (a question answered, not a conversation), and
-- a yes only when nothing in it says no.
function TeamSupply.answer(text,addressed)
  local t=text:lower():gsub("\226\128\153",""):gsub("'","")   -- ("don't", or "don’t" from a phone)
  t=" "..t:gsub("%p"," "):gsub("%s+"," "):gsub("^ ",""):gsub(" $","").." "
  local n=select(2,t:gsub("%S+",""))
  if n==0 or n>(addressed and ADDRESSED_WORDS or PLAIN_WORDS) then return nil end
  for _,idiom in ipairs(IDIOMS) do t=t:gsub(" "..idiom:gsub("%s","%%s").." "," ok ") end
  if has(t,NO) then return "no" end   -- (a refusal is always safe to take as one)
  -- a yes is a plain one: nothing else said in it
  local names={}
  for _,npc in pairs(storage and storage.npcs or {}) do if npc.name then names[npc.name:lower()]=true end end
  for w in t:gmatch("%S+") do
    if not (REPLY_WORDS[w] or names[w]) then return nil end
  end
  if has(t,YES) then return "yes" end
  return nil
end

-- A player's answer to an engineer's question: addressed to it (npc given), or a plain reply from the
-- player that was asked, within a minute. True when the line was that answer.
function TeamSupply.reply(player,text,npc,tick)
  if not TeamWorld.active() or not player or not player.valid or type(text)~="string" then return false end
  local s=state()
  local id,ask
  for asker,a in pairs(s.asks) do
    if a.player_index==player.index and (not npc or npc.id==asker) and tick-a.tick<ANSWER_WAIT
        and (not ask or a.tick>ask.tick) then id,ask=asker,a end
  end
  if not ask then return false end
  local answer=TeamSupply.answer(text,npc~=nil)
  if not answer then return false end
  local yes=answer=="yes"
  settle(s,ask,yes,tick,false)
  s.asks[id]=nil
  s.miss[id..":"..ask.item]=nil
  local asker=storage.npcs and storage.npcs[id]
  if asker and asker.character and asker.character.valid then
    Voice.say(asker,yes and "chest_yes" or "chest_no",{target=player.name,item=Voice.pretty(ask.item)},
      {player=player,direct=true})
  end
  return true
end

return TeamSupply
