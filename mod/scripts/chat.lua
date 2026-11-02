-- Classify addressed player chat locally, then call bounded game-owned actions.
local Intents=require("scripts.chat_intents")
local Diplomacy=require("scripts.diplomacy")
local Personality=require("scripts.personality")
local PlayerTrade=require("scripts.player_trade")
local Character=require("scripts.character")
local Teach=require("scripts.teach")
local Agent=require("scripts.agent")
local Radio=require("scripts.radio")
local TeamSupply=require("scripts.team_supply")
local Chat={}
local ITEMS={coal="coal",iron="iron-plate",copper="copper-plate",steel="steel-plate",
  ["iron plate"]="iron-plate",["iron plates"]="iron-plate",["iron-plate"]="iron-plate",
  ["copper plate"]="copper-plate",["copper plates"]="copper-plate",["copper-plate"]="copper-plate",
  ["steel plate"]="steel-plate",["steel plates"]="steel-plate",["steel-plate"]="steel-plate"}
local PROJECTS={starter_power_science="power and red science",green_science="green science",
  electric_mining="electric mining",basic_defense="base defenses",steel_processing="steel production",
  oil_processing="oil processing",chemical_science="blue science",rocket_materials="rocket research and components",
  platform_launch="my first orbital platform"}
function Chat.trade_terms(body)
  local a,itema,b,itemb=body:match("^%s*trade%s+(%d+)%s+([%a%- ]-)%s+for%s+(%d+)%s+([%a%- ]-)%s*[%.!%?]?%s*$")
  if not a or not ITEMS[itema] or not ITEMS[itemb] then return nil end
  return {player_gives=ITEMS[itema],player_count=tonumber(a),npc_gives=ITEMS[itemb],npc_count=tonumber(b)}
end
local function addressed(text)
  local lower=text:lower()
  local prefix,body=lower:match("^%s*@?([%w#%-]+)%s*:%s*(.+)$")
  if not prefix then return nil end
  local numeric=tonumber(prefix:match("^#?(%d+)$"))
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    if npc and (numeric==id or prefix==npc.name:lower()) then return npc,body end
  end
end
-- "everyone: ...", "all: ..." or "guys, ..." is for every engineer.
local function broadcast(text)
  local lower=text:lower()
  return lower:match("^%s*@?all%s*:%s*(.+)$") or lower:match("^%s*@?everyone[%s,:!]+(.+)$")
    or lower:match("^%s*@?everybody[%s,:!]+(.+)$") or lower:match("^%s*guys[%s,:!]+(.+)$")
end
local function say(player,npc,text)
  player.print("["..npc.name.."] "..Radio.clean(text))
end
local function peer_in(npc,text)
  for id=1,10 do
    local other=storage.npcs[id]
    if other and id~=npc.id and text:find("%f[%a]"..other.name:lower().."%f[%A]") then return id end
  end
end
function Chat.handle(player,text)
  if not player or not player.valid or type(text)~="string" or #text>320 then return false end
  local everyone=broadcast(text)
  if everyone then
    local heard=false
    for id=1,10 do
      local npc=storage.npcs and storage.npcs[id]
      if npc and npc.character and npc.character.valid and Agent.thinking(npc) then
        Agent.event(npc,{kind="chat",from=player.name,text=everyone:sub(1,280),to_everyone=true})
        heard=true
      end
    end
    if heard then return true end
  end
  local npc,body=addressed(text)
  -- (a plain "sure" or "no" from a player an engineer just asked about their chest is the answer)
  if not npc then return TeamSupply.reply(player,text,nil,game.tick) end
  if not npc.character or not npc.character.valid then say(player,npc,"My engineer has been lost.");return true end
  storage.chat=storage.chat or {last={},history={}}
  local key=player.index..":"..npc.id
  if game.tick-(storage.chat.last[key] or -600)<120 then return true end
  storage.chat.last[key]=game.tick
  -- An answer to its question about one of the player's chests (the team world).
  if TeamSupply.reply(player,body,npc,game.tick) then return true end
  -- Exact bounded consent controls run before the language classifier.
  -- A probabilistic label can never accept someone else's barter deal.
  local action,id=body:match("^%s*(accept)%s+#?(%d+)%s*$")
  if not action then action,id=body:match("^%s*(reject)%s+#?(%d+)%s*$") end
  if not action then local cancel=body:match("^%s*cancel%s+#?(%d+)%s*$");if cancel then action,id="reject",cancel end end
  if action then Diplomacy.trade_command(player,action,id,npc.id);return true end
  if body:match("^%s*learn") or body:match("^%s*study") or body:match("^%s*teach") then
    player.print(Teach.from_cursor(player, npc.id));return true
  end
  if body:match("^%s*trades%s*$") or body:match("^%s*trade status%s*$") then
    Diplomacy.trade_command(player,"trades",nil,npc.id);return true
  end
  -- An engineer with a strategist hears the message and decides itself (it only hears what is
  -- addressed to it); without one, the fixed phrases below apply.
  if Agent.thinking(npc) then
    Agent.event(npc,{kind="chat",from=player.name,text=body:sub(1,280)})
    return true
  end
  -- Asking an ally for help: turrets by the player's base, built and kept loaded by the engineer.
  if body:match("defend") or body:match("protect") or body:match("guard") then
    if not npc.force.get_friend(player.force) then
      say(player,npc,"I'm not your ally. Ask me 'can we team up?' first.")
    elseif not npc.design then
      say(player,npc,"I cannot build defenses for anyone yet.")
    else
      npc.help_request={kind="defend",player_index=player.index,tick=game.tick,
        position={x=player.position.x,y=player.position.y}}
      say(player,npc,"I will put turrets up by your base, facing the nests, and keep them loaded.")
    end
    return true
  end
  local result=Intents.classify(body)
  storage.chat.history[#storage.chat.history+1]={tick=game.tick,player_index=player.index,npc_id=npc.id,
    intent=result.intent,confidence=result.confidence,accepted=result.accepted,model=Intents.version}
  while #storage.chat.history>128 do table.remove(storage.chat.history,1) end
  if not result.accepted then
    say(player,npc,"I am unsure what you mean. Try 'what are you building?', 'can we team up?', 'defend my base' or /npc help.")
  elseif result.intent=="greeting" then
    -- How they answer depends on personality, mood and what they remember of you.
    Character.speak(npc,"greet","player:"..player.index,nil,{player=player,direct=true})
  elseif result.intent=="status" then
    local p=npc.factory_project
    local project=p and (PROJECTS[p.id] or p.id:gsub("_"," ")) or "my first mining and smelting setup"
    local progress=p and " ("..math.min(p.index or 1,#p.plan.steps).."/"..#p.plan.steps.." steps)" or ""
    local d=npc.design
    if d and d.plan and not p then
      -- A designed engineer: the plan it is building, stage by stage.
      local stages=d.plan.stages or {}
      local stage=stages[d.stage_index or 1]
      project=tostring(d.purpose or d.plan.purpose or "my factory"):gsub("-"," ")
      if npc.base_plan and npc.base_plan.stage and (d.purpose=="bus-base") then
        project="my main bus ("..tostring(npc.base_plan.stage):gsub("-"," ")..")"
      end
      progress=d.status=="building" and stage and " (stage "..(d.stage_index or 1).."/"..#stages..")" or ""
      if d.status~="building" and d.status~="waiting" then project="keeping my factory running" end
    end
    local job=npc.current_job
    local activity=job and ({mine="gathering ",craft="crafting ",build="placing ",transfer="moving ",move="traveling"})[job.kind]
    if activity and job.kind~="move" then activity=activity..tostring(job.item or job.recipe or job.entity_name or "materials"):gsub("-"," ") end
    say(player,npc,"I am working on "..project..progress..". I am "..(activity or "planning my next task")..".")
    local problem=p and (p.supply_blocker or p.reason or p.last_problem) or npc.last_problem
    if problem then say(player,npc,"I have a construction or supply problem to resolve before I can continue.") end
  elseif result.intent=="alliance" then
    local ok= Diplomacy.talk(player,"ally",npc.id,peer_in(npc,body))
    if not ok then say(player,npc,"You cannot negotiate for the founding faction.") end
  elseif result.intent=="peace" then
    if peer_in(npc,body) then say(player,npc,"Use /npc pact <id> <id> to broker an alliance between two engineers.")
    else Diplomacy.talk(player,"peace",npc.id) end
  elseif result.intent=="war" then
    -- A probabilistic label alone cannot declare a conflict from casual wording.
    -- The player must explicitly declare it or use the existing command.
    if body:find("i declare war",1,true) or body:find("my faction declares war",1,true) then
      Diplomacy.talk(player,"war",npc.id)
    else say(player,npc,"If you intend to declare war on me, say 'I declare war on you' or use /npc war "..npc.id..".") end
  elseif result.intent=="trade" then
    local terms=Chat.trade_terms(body)
    if not terms then say(player,npc,"Give exact terms: 'trade 20 coal for 10 iron' means you give me 20 coal and I give you 10 iron plates.")
    else
      local deal,reason=PlayerTrade.propose(player,npc.id,terms)
      if not deal then say(player,npc,"That trade is unavailable: "..tostring(reason)..".")
      elseif deal.status=="accepted" then
        say(player,npc,"Agreed (#"..deal.id.."). You give "..terms.player_count.." "..terms.player_gives:gsub("-"," ")..
          "; I give "..terms.npc_count.." "..terms.npc_gives:gsub("-"," ")..". I will come to you to exchange them.")
      else say(player,npc,"I decline that trade. My supplies and the terms matter to me.") end
    end
  else Diplomacy.talk(player,"help",npc.id) end
  return true,result
end
function Chat.on_message(event)
  local player=game.get_player(event.player_index)
  if player then Chat.handle(player,event.message) end
end
return Chat
