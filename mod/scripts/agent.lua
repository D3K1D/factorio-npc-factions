-- The strategist's side in the game. An engineer notices things (events), asks its strategist (an
-- LLM in the companion) what to do when something happens or now and then, and works through the
-- agenda it gets back with its own body and items. The companion only proposes: every intention is
-- checked here and carried out as ordinary jobs, and nothing is ever granted.
--
-- Priorities: "now" pauses the plan being built (it resumes afterwards), "next" runs before more
-- building, "later" waits until no plan is being built.
local Character=require("scripts.character")
local Personality=require("scripts.personality")
local BaseMemory=require("scripts.base_memory")
-- Factorio allows require only while control.lua loads: everything used later is required here.
local Society=require("scripts.society")
local Combat=require("scripts.combat")
local Odds=require("scripts.combat_odds")
local PlayerTrade=require("scripts.player_trade")
local Diplomacy=require("scripts.diplomacy")
local Aggro=require("scripts.aggro")
local Routes=require("scripts.combat_routes")
local War=require("scripts.war")
local Intel=require("scripts.intel")
local Drive=require("scripts.drive")
local Worker=require("scripts.worker")
local Recipes=require("scripts.recipes")
local Hands=require("scripts.hands")
local Armory=require("scripts.armory")
local TeamWorld=require("scripts.team_world")
local TeamResearch=require("scripts.team_research")
local Radio=require("scripts.radio")
local TeamWar=require("scripts.team_war")
local Priorities=require("scripts.priorities")
local Agent={}

local MAX_EVENTS=16
local MAX_AGENDA=8
local MAX_NOTES=12
local MAX_RESULTS=32
local HEARTBEAT=10800                     -- routine thought every 3 game minutes, when something changed...
local CALM_HEARTBEAT=36000                -- ...and every 10 while nothing has
local EVENT_GAP=3600                      -- events gather: a thought about them once a minute at most,
local AT_ONCE={chat=true,answer=true}     -- but a player talking to it is answered at once,
local QUIET={plan_done=true}              -- and news that wakes nobody rides along with its next thought
local INITIATIVE_MIN,INITIATIVE_MAX=72000,108000   -- reaching out to someone: every 20-30 minutes
local ANSWER_WAIT=3600                    -- a request the companion leaves unanswered is dropped
local BRIDGE_ALIVE=18000                  -- the strategist counts as connected this long after a poll
local DEADLINE=36000                      -- an intention not done in 10 game minutes is given up
local owned,loadout,arm_wants,diplomacy_view,creep,raid
-- The user's rule (2026-10-13): engineers never go into the biters' detection range (nests, worms and
-- the biters round them) but to destroy those nests, whatever their gear. Their reach is a wall for every
-- route (Routes.plan with no extra berth); only the nests an attack on nests is for are spared.
local KEEP_OUT=0
local MAX_SECRETS=6
local TOOLS={say=true,remember=true,research=true,build=true,stockpile=true,deliver=true,guard=true,ask=true,
  visit=true,give=true,trade=true,join=true,attack=true,build_at=true,arm_up=true,diplomacy=true,quiet=true,
  relocate=true,prioritize=true}
local TRIPS={deliver=true,guard=true,visit=true,give=true,attack=true,arm_up=true,relocate=true}
local STANDING_SKIP={deliver=true,guard=true,visit=true,give=true,attack=true,relocate=true}   -- (trips away: not while it stands off)   -- these take longer: a walk across the map
local PRIORITY={now=1,next=2,later=3}
local FAR_TRIP=400                       -- a trip this far crosses biter country: armor, a gun, more magazines
local CHESTS={"iron-chest","wooden-chest"}
-- Whether a trip ended off its own ground, so the march home (urgent) is wanted: one that ends on its own base
-- (its plot's edge, its patches 50 tiles out, its wall) is home already (W-36: distance from the home point alone
-- sent it "home" from its own wall). Past FAR_TRIP it is in biter country whatever of its own stands there (a far
-- outpost, a turret it left on a raid): the safe route home is wanted (Codex). Without the game's own map
-- (tests), distance decides as before.
function Agent.off_own_ground(npc,pos)
  local D=Agent.designed
  local h=npc.home
  if not (D and D.on_own_ground and h) or (pos.x-h.x)^2+(pos.y-h.y)^2>FAR_TRIP^2 then return true end
  return not D.on_own_ground(npc,pos)
end
-- Items the strategist is told whether it can make yet (so it does not promise what it cannot).
local KEY_ITEMS={"gun-turret","firearm-magazine","piercing-rounds-magazine","stone-wall","gate","grenade","car",
  "tank","repair-pack","radar","construction-robot","logistic-robot","roboport","locomotive","cargo-wagon"}

-- Filled in by designed_start (scripts may only be required while loading): acquire, begin,
-- active, count, completed.
Agent.designed=nil
Agent.combat=Combat   -- (designed_start.lua: no building inside the sight of biters it keeps clear of)

local function state(npc)
  npc.agent=npc.agent or {events={},agenda={},notes={},results={},seq=0}
  return npc.agent
end

local function text(v,limit) return type(v)=="string" and v:sub(1,limit or 200) or nil end

-- What would be news to a routine thought: what it set out to do (its own tasks), whom it is helping,
-- and whether its plan is stuck. Building going on as planned is not news (a plan finished, a stage
-- done, the next plan begun: the planner sees to those), so an engineer that just builds thinks every
-- 10 minutes (the user's choice, 2026-10-23). The same as at its last thought: nothing new.
local function gist(npc)
  local d,a=npc.design,npc.agent
  return table.concat({a and #(a.agenda or {}) or 0,tostring(npc.helping and npc.helping.ally),
    tostring(d and d.status=="blocked")},"|")
end

function Agent.connected()
  return storage.agent_bridge_tick~=nil and game.tick-storage.agent_bridge_tick<BRIDGE_ALIVE
end

-- Whether this engineer thinks with a strategist (designed engineers, while one is connected).
function Agent.thinking(npc)
  return npc and npc.design~=nil and Agent.connected()
end

function Agent.event(npc,event)
  if not Agent.thinking(npc) then return false end
  local a=state(npc)
  event.tick=event.tick or game.tick
  a.events[#a.events+1]=event
  while #a.events>MAX_EVENTS do table.remove(a.events,1) end
  return true
end

local function result(npc,it,status,reason)
  if it.tool=="attack" and (status=="done" or status=="failed") then
    npc.combat_objective,npc.combat_mission_until,npc.marching=nil,nil,nil   -- the attack is over
    if it.args and it.args.target=="nests" and Agent.attack_made then pcall(Agent.attack_made,npc,it,status,reason) end
  end
  -- A trip that ended far from home, off its own ground: it marches back by a safe route (see Agent.next).
  local c=npc.character
  if TRIPS[it.tool] and (status=="done" or status=="failed") and npc.home and c and c.valid
      and (c.position.x-npc.home.x)^2+(c.position.y-npc.home.y)^2>FAR_TRIP^2 and Agent.off_own_ground(npc,c.position) then
    npc.homeward={tool="homeward",args={},created=game.tick,priority="next"}
  end
  local a=state(npc)
  a.results[#a.results+1]={request_id=it.request_id,tool=it.tool,status=status,reason=reason,tick=game.tick}
  while #a.results>MAX_RESULTS do table.remove(a.results,1) end
  a.log=a.log or {}
  a.log[#a.log+1]=it.tool.." "..status..(reason and (" ("..tostring(reason)..")") or "").." @"..game.tick
  while #a.log>8 do table.remove(a.log,1) end
  if Agent.recorder then Agent.recorder.task(npc,it,status,reason) end
end

-- When to ask the strategist: something happened, it is time to reach out to someone, or routine.
-- Someone it is no longer at war with (peace made, or allied since) is not its foe any more.
local function foe_current(npc)
  local foe=npc.foe
  if not foe or foe.key=="everyone" then return end
  local index=tonumber(tostring(foe.key):match("^player:(%d+)$"))
  local player=index and game.get_player(index)
  local other_id=tonumber(tostring(foe.key):match("^npc:(%d+)$"))
  local other=other_id and storage.npcs and storage.npcs[other_id]
  local force=player and player.force or other and other.force
  if not (force and force.valid) or Diplomacy.state(npc.force,force)~="war" then npc.foe=nil end
end

-- Its home is under attack and needs it (war.lua): whatever trip it is on waits (and starts over
-- afterwards), the raid's objective is dropped, the job in hand is broken off, and it heads back to
-- where it is hit. Turrets it put down out here (a creep, a fight) come along when it is safe.
local function begin_recall(npc,why,tick)
  local a=npc.alarm
  npc.helping=nil   -- its own home first
  local pack={}
  for _,t in ipairs(npc.combat_turrets and npc.combat_turrets.dropped or {}) do pack[#pack+1]=t end
  for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do
    for _,t in pairs(it.creep and it.creep.turrets or {}) do pack[#pack+1]=t end
    if it.artillery then   -- (its artillery and guards out there come along too: O-7)
      if it.artillery.turret then pack[#pack+1]=it.artillery.turret end
      for _,t in ipairs(it.artillery.guards or {}) do pack[#pack+1]=t end
    end
    if TRIPS[it.tool] then
      it.staged,it.arrived,it.creep,it.route,it.regroup,it.resting,it.started,it.wait_since=nil,nil,nil,nil,nil,nil,nil,nil
      it.artillery=nil
    end
  end
  npc.recall={tool="recall",args={},place={x=a.position.x,y=a.position.y},since=tick,created=tick,why=why,pack=pack}
  npc.combat_objective,npc.combat_mission_until,npc.marching=nil,nil,nil
  if npc.homeward then npc.homeward.route=nil end
  -- (broken off the way a fight breaks off work: whoever owned the job plans it again later)
  pcall(Worker.cancel,npc,"combat_interrupt")
  local by={}
  for who in pairs(a.by) do by[#by+1]=who=="enemy" and "biters" or who end
  table.sort(by)
  Agent.event(npc,{kind="home_attacked",by=table.concat(by,", "),lost=a.lost,why=why,
    position={x=math.floor(a.position.x),y=math.floor(a.position.y)}})
end

-- A friend's home under attack that it can help with (War.ally_in_need): it arms up, goes, fights
-- beside them, leaves them turrets once the attack is over, and goes home. Not in the middle of a trip.
local function under_way(npc)
  if npc.homeward then return true end
  for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do
    if TRIPS[it.tool] and it.started then return true end
  end
  return false
end
-- On a trip of war (a raid, a call home, helping a friend, the march back): what the faction's society
-- would have it do on the side (theft, sabotage, deliveries) waits; run 18 had a sabotage job walk the
-- raider straight into the base it meant to stop short of and look at first.
function Agent.on_war_trip(npc)
  return npc.recall~=nil or npc.helping~=nil or npc.homeward~=nil or under_way(npc)
end
Agent.helper_for=function(...) return War.helper_for(...) end   -- (designed_start's call for help: weighed)
War.on_trip=function(n) return under_way(n) end
Agent.helper_brings=function(n) return War.brings(n) end   -- (on a trip it does not break off: not free to help, D-12)
local function begin_help(npc,ally,tick)
  local a=ally.alarm
  npc.helping={tool="help",args={},ally=ally.id,name=ally.name,place={x=a.position.x,y=a.position.y},
    since=tick,created=tick,step="arm",brings=War.brings and War.brings(npc) or nil}   -- (what it adds: War.ally_in_need)
  local at={x=math.floor(a.position.x),y=math.floor(a.position.y)}
  Agent.event(npc,{kind="helping",ally=ally.name,position=at})
  Agent.event(ally,{kind="help_coming",from=npc.name})
end

-- Kept clear of biters it would lose to with nothing it can do out of their reach either (idle that long): its
-- strategist hears of it, once a while, rather than it standing still unheard (O-4). What it decides (arm up, build
-- turrets elsewhere, call for help) is its own.
Agent.KEPT_IDLE,Agent.KEPT_TOLD=7200,36000
function Agent.kept_from_work(npc,tick)
  local kept=Combat.keeping_clear and Combat.keeping_clear(npc,tick)
  if not kept or npc.current_job then npc.kept_idle_since=nil; return false end
  npc.kept_idle_since=npc.kept_idle_since or tick
  if tick-npc.kept_idle_since<Agent.KEPT_IDLE or tick<(npc.kept_told or 0) then return false end
  local w=npc.combat and npc.combat.wave
  if not Agent.event(npc,{kind="kept_from_work",biters=w and w.n,odds=w and math.floor(math.min(w.ratio,99)*100)/100,
      position=w and {x=math.floor(w.nearest.x),y=math.floor(w.nearest.y)},
      minutes=math.floor((tick-npc.kept_idle_since)/3600)}) then return false end   -- (no strategist yet: told later)
  npc.kept_told=tick+Agent.KEPT_TOLD
  return true
end

-- Biters camped at its own buildings, not attacking it (Combat.intruders; O-6: a wave's four small biters stayed by
-- its iron field and the furnace they broke waited "biters_near" all run). A group it can beat it clears: there it
-- goes, unless it is away on a trip of war or far from them; its fight does the rest. A group it cannot beat is an
-- attack on its base still going on: the alarm is kept up there, so it is called back and stands off as from any
-- attack it would lose (its work going on), friends weigh coming to help (War.ally_in_need), and its strategist
-- hears home_attacked. Decided anew at every look; with the group gone it is over.
Agent.LINGER_EVERY,Agent.CLEAR_FROM,Agent.CLEAR_FOR,Agent.CLEAR_GIVE_UP=300,200,5400,18000
Agent.LINGER_ARM_EVERY,Agent.LINGER_AMMO=18000,20
function Agent.has_intention(npc,tool)
  for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do if it.tool==tool then return true end end
  return false
end
-- What it knows of the nests round it (the user's rules: 2026-10-13, only what it, its radars or its friends have seen,
-- and roughly where engineers started; 2026-10-31, every real nest its own pollution cloud reaches, seen or not, since
-- those are the ones that will attack it): a cluster is known when the cloud reaches its chunk or the chunk beside it
-- (`cloud`: the most pollution there, `near_cloud`), when its side sees it or it remembers it (`seen`), or when an
-- attack on its base came from it. The rest it does not know of, however near.
-- (Filtered before `limit` are kept, the cloud's and the attackers' groups first, nearest first in each, so groups it
-- only knows of do not hide a target further out.)
Agent.CLOUD_NEAR,Agent.SEEN_NEAR,Agent.SOURCE_NEAR=32,32,64
function Agent.known_nests(npc,clusters,tick,limit)
  local c=npc.character
  local out,rest={},{}
  local sources={}
  local okb,board=pcall(TeamWar.threat_board,npc,nil,c.surface,tick)
  for _,a in ipairs(okb and board and board.attacks or {}) do
    local at=a.nest or a.from
    if at then sources[#sources+1]=at end
  end
  for _,k in ipairs(clusters or {}) do
    local here=0
    local ok,v=pcall(function() return c.surface.get_pollution({x=k.x,y=k.y}) end)
    if ok then here=tonumber(v) or 0 end
    local near=here
    for _,d in ipairs({{1,0},{-1,0},{0,1},{0,-1}}) do
      local okn,vn=pcall(function() return c.surface.get_pollution({x=k.x+d[1]*Agent.CLOUD_NEAR,y=k.y+d[2]*Agent.CLOUD_NEAR}) end)
      if okn then near=math.max(near,tonumber(vn) or 0) end
    end
    local seen=Intel.sees(npc,{x=k.x,y=k.y},tick)
    if not seen then
      for _,d in pairs(npc.seen_dangers and npc.seen_dangers.list or {}) do     -- (only biter nests and worms it saw,
        if d.nest and d.kind and not d.blocked and d.x                            --  not water, woods or failed routes)
          and (d.x-k.x)^2+(d.y-k.y)^2<=Agent.SEEN_NEAR^2 then seen=true; break end
      end
    end
    local source=false
    for _,at in ipairs(sources) do
      if (at.x-k.x)^2+(at.y-k.y)^2<=Agent.SOURCE_NEAR^2 then source=true; break end
    end
    if here>0 or near>0 or seen or source then
      k.cloud=here>0 and math.max(0.1,math.floor(here*10+0.5)/10) or 0   -- (a trace still counts)
      k.near_cloud=here<=0 and near>0 or nil
      k.seen=seen or nil
      k.source=source or nil
      if here>0 or near>0 or source then out[#out+1]=k else rest[#rest+1]=k end
    end
  end
  for _,k in ipairs(rest) do out[#out+1]=k end
  while limit and #out>limit do table.remove(out) end
  return out
end

function Agent.lingering(npc,tick)
  local g=Combat.intruders and Combat.intruders(npc,tick)
  local c=npc.character
  if not g then npc.clearing=nil; return nil end
  local near=(c.position.x-g.x)^2+(c.position.y-g.y)^2<=Agent.CLEAR_FROM^2
  local failed=npc.clear_failed
  failed=failed and tick<failed.until_tick and math.abs(failed.x-g.x)<=16 and math.abs(failed.y-g.y)<=16
  if g.ratio>=Odds.ENGAGE and near and not failed and not Agent.on_war_trip(npc) and not npc.combat_objective then
    local cl=npc.clearing
    if cl and math.abs(cl.x-g.x)<=16 and math.abs(cl.y-g.y)<=16 then
      if tick-(cl.since or tick)<Agent.CLEAR_GIVE_UP then
        -- (the same group: kept at, where it is now; and a walk that got there with none in sight goes on to them)
        if cl.reached and (cl.x~=g.x or cl.y~=g.y) then cl.reached=nil end
        cl.x,cl.y,cl.n,cl.ratio=g.x,g.y,g.n,g.ratio
        cl.until_tick=tick+Agent.CLEAR_FOR
        return "clear"
      end
      -- (at them this long and they are still there: not again a while; as for one it cannot beat, below)
      npc.clear_failed={x=g.x,y=g.y,until_tick=tick+Agent.CLEAR_FOR}
    else
      npc.clearing={x=g.x,y=g.y,n=g.n,ratio=g.ratio,since=tick,until_tick=tick+Agent.CLEAR_FOR}
      return "clear"
    end
  end
  npc.clearing=nil
  -- Not one it can beat yet: it arms up for them, what it can make (a gun and magazines, armor, turrets), once a while
  -- (the death-world run, 2026-10-28: four small biters read odds 0, its gun had nothing to shoot, and nothing
  -- armed it before gun turrets were researched)...
  local armed
  if tick>=(npc.linger_armed or 0) and not Agent.has_intention(npc,"arm_up") then
    npc.linger_armed=tick+Agent.LINGER_ARM_EVERY
    local ok,took=pcall(Agent.order,npc,{tool="arm_up",priority="next",reason="biters camped at my buildings that I cannot beat yet",
      args={focus="all",ammo=Agent.LINGER_AMMO}})
    armed=ok and took==true
  end
  -- ...and it is an attack still going on meanwhile: its turrets near them do not count as holding them off, friends
  -- weigh coming to help, its strategist hears.
  if War.alarm(npc,{x=g.x,y=g.y},game.forces.enemy,0,nil,tick) and npc.alarm then npc.alarm.lingering=tick end
  return armed and "arm" or "alarm"
end

function Agent.tick(npc,tick)
  foe_current(npc)
  if tick>=(npc.next_look or 0) then   -- what it sees round itself: the nests and turrets it remembers
    npc.next_look=tick+300
    if npc.character and npc.character.valid then
      Routes.look(npc)
      Intel.watch(npc,tick,Routes.note)   -- and what its friends and radars see
    end
  end
  if npc.character and npc.character.valid then
    Agent.kept_from_work(npc,tick)
    if tick>=(npc.next_linger_look or 0) then
      npc.next_linger_look=tick+Agent.LINGER_EVERY
      Agent.lingering(npc,tick)
      pcall(Agent.sample_income,npc,tick)
    end
    local why=War.tick(npc,tick)
    if why then begin_recall(npc,why,tick)
    elseif not npc.helping and tick>=(npc.next_help_look or 0) then
      npc.next_help_look=tick+300
      local axes=Personality.axes and Personality.axes(npc) or {}
      local ally=War.ally_in_need(npc,tick,axes.cooperation)
      if ally and not under_way(npc) then begin_help(npc,ally,tick) end
      -- a friend attacked by someone it is at peace with: joining that war is its strategist's call
      if Agent.thinking(npc) then
        for _,e in ipairs(War.friends_attacked(npc)) do
          Agent.event(npc,{kind="ally_attacked",ally=e.ally,by=e.by,position=e.position})
        end
      end
    end
  end
  if not Agent.thinking(npc) then return end
  local a=state(npc)
  if a.pending and tick-a.pending.tick>ANSWER_WAIT then
    for _,e in ipairs(a.pending.events) do a.events[#a.events+1]=e end   -- still to think about
    a.pending=nil
  end
  if a.pending then return end
  a.next_initiative=a.next_initiative or tick+math.random(INITIATIVE_MIN,INITIATIVE_MAX)
  a.next_heartbeat=a.next_heartbeat or tick+HEARTBEAT
  -- Each thought is an AI call, so it thinks when there is something to think about (the token audit,
  -- 2026-10-23): events at most once a minute (a fight's reports gather into one thought), and a routine
  -- thought only when there is news (see gist), else every 10 minutes. A finished plan wakes nobody: the
  -- planner picks the next goal by itself, and its next thought, 10 minutes at most, hears of it (it was
  -- most thoughts left, replay 2026-10-23).
  local reason,waiting
  local loud=false
  for _,e in ipairs(a.events) do loud=loud or not QUIET[e.kind] end
  if loud then
    local at_once=tick-(a.last_thought or -EVENT_GAP)>=EVENT_GAP
    for _,e in ipairs(a.events) do at_once=at_once or AT_ONCE[e.kind]==true end
    if at_once then reason="events" else waiting=true end
  end
  if not reason and not waiting then
    if tick>=a.next_initiative then reason="initiative"
    elseif tick>=a.next_heartbeat then
      if gist(npc)~=a.gist or tick-(a.last_thought or 0)>=CALM_HEARTBEAT then reason="routine"
      else a.next_heartbeat=tick+HEARTBEAT end
    end
  end
  if not reason then return end
  if reason=="initiative" then a.next_initiative=tick+math.random(INITIATIVE_MIN,INITIATIVE_MAX) end
  a.next_heartbeat=tick+HEARTBEAT
  a.last_thought,a.gist=tick,gist(npc)
  a.seq=a.seq+1
  a.pending={request_id=npc.id..":s"..a.seq,tick=tick,reason=reason,events=a.events}
  a.events={}
end

-- What the engineer holds and has stored, largest stocks first.
local function stock(npc)
  local totals={}
  local function add(contents)
    for _,s in ipairs(contents or {}) do totals[s.name]=(totals[s.name] or 0)+s.count end
  end
  add(npc.character.get_main_inventory().get_contents())
  for _,chest in ipairs(npc.storage_chests or {}) do
    if chest.valid then add(chest.get_inventory(defines.inventory.chest).get_contents()) end
  end
  local out={}
  for name,n in pairs(totals) do out[#out+1]={name=name,count=n} end
  table.sort(out,function(a,b) return a.count>b.count end)
  while #out>30 do table.remove(out) end
  return out
end

-- What it could lose in an attack and make again (O-5, the user's own play: "if I had more turrets and more ammo to
-- spare I would have went for it. I would lose turrets doing so, but it wouldnt be a big deal since my iron output was
-- so high"): turrets and magazines in its pockets and chests beyond what home keeps (its defense kit, a refill of ten
-- for each loaded turret round home), its iron plates made a minute lately (the force's production, sampled every
-- look), and the research upgrades its guns and turrets have.
Agent.HOME_REFILL,Agent.INCOME_SPAN=10,36000
function Agent.sample_income(npc,tick)
  local stats=npc.force.get_item_production_statistics(npc.character.surface)
  local made=stats.get_input_count("iron-plate")
  npc.income=npc.income or {}
  local list=npc.income
  if #list==0 or tick-list[#list].tick>=3600 then list[#list+1]={tick=tick,made=made} end
  while #list>2 and tick-list[2].tick>=Agent.INCOME_SPAN do table.remove(list,1) end
end
function Agent.war_chest(npc,tick)
  local c=npc.character
  -- (counted by name, in its pockets, gun and ammo slots and every chest: stock() keeps only its biggest items)
  local held={}
  local kit0=Agent.creep_kit(npc)
  local turret_item=kit0 and kit0.item or "gun-turret"   -- (its creep kit's turret: a modded one counts as itself)
  for _,name in ipairs({turret_item,"firearm-magazine","piercing-rounds-magazine","uranium-rounds-magazine"}) do
    local n=owned(npc,name)
    for _,chest in ipairs(npc.storage_chests or {}) do
      if chest.valid then n=n+chest.get_inventory(defines.inventory.chest).get_item_count(name) end
    end
    held[name]=n
  end
  local mags=(held["firearm-magazine"] or 0)+(held["piercing-rounds-magazine"] or 0)+(held["uranium-rounds-magazine"] or 0)
  local carried=0   -- (magazines on its body: the rest are in its chests)
  for _,name in ipairs({"firearm-magazine","piercing-rounds-magazine","uranium-rounds-magazine"}) do carried=carried+owned(npc,name) end
  local kit_turrets,kit_mags=2,30
  if Agent.kit and Agent.kit.size then kit_turrets,kit_mags=Agent.kit.size(npc) end
  local loaded=0
  for _,t in ipairs(npc.home and c.surface.find_entities_filtered{force=c.force,type="ammo-turret",position=npc.home,
      radius=120} or {}) do
    local inv=t.get_inventory(defines.inventory.turret_ammo)
    if inv and not inv.is_empty() then loaded=loaded+1 end
  end
  -- (what holds its home while it is away: those, and its electric turrets with power and fluid turrets with fluid;
  -- a late base's laser turrets counted none, so it never went out under waves: D-20, NPC Offense's war chest)
  local holding=loaded
  for _,t in ipairs(npc.home and c.surface.find_entities_filtered{force=c.force,type={"electric-turret","fluid-turret"},
      position=npc.home,radius=120} or {}) do
    local ok,ready=pcall(function()
      if t.type=="electric-turret" then return (t.energy or 0)>0 end
      -- (a fluid turret: what its fluid box holds; get_fluid_count before 2.1, its fluid boxes since; NPC Offense)
      local ok2,n=pcall(function() return t.get_fluid_count() end)
      if ok2 and n then return n>0 end
      local f=(t.fluids_count or 0)>0 and t.get_fluid(1)
      return f~=nil and f~=false and (f.amount or 0)>0
    end)
    if ok and ready then holding=holding+1 end
  end
  local list,per_min=npc.income or {},nil
  if #list>=2 and list[#list].tick>list[1].tick then
    per_min=(list[#list].made-list[1].made)/((list[#list].tick-list[1].tick)/3600)
  end
  local f=npc.force
  local art,shell=Agent.artillery_kit(npc)
  local shells=0   -- (shells stack one to a slot: most lie in its chests, counted too)
  if shell then
    shells=owned(npc,shell)
    for _,chest in ipairs(npc.storage_chests or {}) do
      if chest.valid then shells=shells+chest.get_inventory(defines.inventory.chest).get_item_count(shell) end
    end
  end
  local kit,kit_ammo=Agent.creep_kit(npc)
  return {turrets=held[turret_item] or 0,magazines=mags,stored_magazines=math.max(0,mags-carried),
    carried_turrets=owned(npc,turret_item),
    artillery=art and {name=art.name,range=math.floor(art.range),shells=shells,shell=shell} or nil,
    creep_turret=kit and kit.item or nil,creep_ammo=kit_ammo,
    -- (what its turret, a magazine of its ammo and a shell cost in plates, from the recipes: offense_brain prices)
    prices={turret=kit and math.floor(Odds.plates(f,kit.item)+0.5) or nil,
      magazine=kit_ammo and math.floor(Odds.plates(f,kit_ammo)*10+0.5)/10 or nil,
      shell=shell and math.floor(Odds.plates(f,shell)+0.5) or nil},
    spare_turrets=math.max(0,(held[turret_item] or 0)-kit_turrets),
    spare_magazines=math.max(0,mags-kit_mags-loaded*Agent.HOME_REFILL),
    iron_per_min=per_min and math.floor(per_min+0.5) or nil,home_turrets=holding,
    upgrades={damage=math.floor((f.get_ammo_damage_modifier("bullet") or 0)*100+0.5)/100,
      speed=math.floor((f.get_gun_speed_modifier("bullet") or 0)*100+0.5)/100,
      turret=math.floor((f.get_turret_attack_modifier("gun-turret") or 0)*100+0.5)/100}}
end

local function direction_word(dx,dy)
  local a=math.atan2(dy,dx)
  local names={"east","south-east","south","south-west","west","north-west","north","north-east"}
  return names[(math.floor((a/(math.pi/4))+0.5)%8)+1]
end

function Agent.situation(npc,tick)
  local c=npc.character
  local d=npc.design
  local a=state(npc)
  local home=npc.home or c.position
  local threats,nearest={},nil
  for _,t in ipairs(BaseMemory.threats(npc,{x=0,y=0},250,16)) do
    threats[t.kind]=(threats[t.kind] or 0)+1
    local dist=math.sqrt((t.x-home.x)^2+(t.y-home.y)^2)
    if not nearest or dist<nearest.distance then
      nearest={kind=t.kind,distance=math.floor(dist),direction=direction_word(t.x-home.x,t.y-home.y)}
    end
  end
  local players={}
  for _,player in pairs(game.connected_players) do
    -- where they are while its side sees them, else roughly where they started
    local seen=Intel.sees(npc,player.position,tick)
    local p=seen and player.position or Intel.spawn_of(npc,player)
    players[#players+1]={name=player.name,relation=Diplomacy.state(npc.force,player.force),
      distance=math.floor(math.sqrt((p.x-home.x)^2+(p.y-home.y)^2)),seen=seen,
      marked=storage.player_marks and storage.player_marks[player.index] or nil}
  end
  local engineers={}
  for id,other in pairs(storage.npcs or {}) do
    if other~=npc and other.character and other.character.valid then
      engineers[#engineers+1]={name=other.name,relation=Diplomacy.state(npc.force,other.force),
        distance=other.home and math.floor(math.sqrt((other.home.x-home.x)^2+(other.home.y-home.y)^2)) or nil}
    end
  end
  local research=npc.force.current_research
  local agenda={}
  for _,it in ipairs(a.agenda) do
    agenda[#agenda+1]={tool=it.tool,priority=it.priority,reason=it.reason,step=it.step,
      goal=it.args.goal,item=it.args.item,to=it.args.to}
  end
  local p=Personality.observe(npc)
  local D=Agent.designed
  local can_make,cannot={},{}
  for _,name in ipairs(KEY_ITEMS) do
    if Recipes.ready(npc.force,name) then can_make[#can_make+1]=name else cannot[#cannot+1]=name end
  end
  local view={name=npc.name,traits=p and p.traits,mood=p and p.mood,axes=p and p.axes,
    health=math.floor(c.health),position={x=math.floor(c.position.x),y=math.floor(c.position.y)},
    home={x=math.floor(home.x),y=math.floor(home.y)},
    doing={phase=npc.phase,plan=d and d.purpose,status=d and d.status,stage=d and d.stage_index,
      stages=d and d.plan and #d.plan.stages or nil,paused=npc.paused_design and npc.paused_design.purpose or nil},
    built=D and D.completed and D.completed(npc) or {},
    research={current=research and research.name,progress=research and math.floor(npc.force.research_progress*100)},
    stock=stock(npc),threats=threats,nearest_threat=nearest,
    social=Character.social_summary(npc,tick,4),players=players,engineers=engineers,
    can_make=can_make,cannot_make_yet=cannot,failed_recently=D and D.failed and D.failed(npc) or nil,
    military=Armory.makeable(npc.force),
    joined=npc.joined and npc.joined.name or nil,
    armed=loadout(npc),quiet=npc.quiet or nil,silent=npc.silent or nil,mission=npc.mission,
    foe=npc.foe and npc.foe.name or nil,running_out=npc.running_out and #npc.running_out>0 and npc.running_out or nil,
    diary={plan=a.plan,secrets=a.secrets},diplomacy=diplomacy_view(npc),
    agenda=agenda,notes=a.notes,recently=a.log,priorities=Priorities.list(npc,tick)}
  -- the nests round it, grouped, with what they soak up of its pollution: what it might take out
  local ok_nests,nests=pcall(BaseMemory.nest_clusters,npc,600,400)   -- (as far as an attack goes: offense_brain MAX_TRIP)
  view.nests=ok_nests and Agent.known_nests(npc,nests,tick,12) or nil
  local h=npc.home or c.position
  view.home={x=math.floor(h.x),y=math.floor(h.y)}   -- (where its nest distances are from: a tour goes round it)
  -- and the odds of a fight there: the biters there and what the spawners send out, against what it carries (it
  -- cannot walk away from that fight either: combat_odds.lua; companion/offense_brain.py weighs it)
  local gear=view.nests and Combat.gear and Combat.gear(npc)
  local okc,chest=pcall(Agent.war_chest,npc,tick)
  view.war_chest=okc and chest or nil
  -- (what it would take along: every turret it holds, its kit's among them, and the magazines in its chests beside
  -- those it carries; what it can afford to lose is the spare beyond its kit, offense_brain.py)
  local wc=view.war_chest
  local kit=wc and Agent.creep_kit(npc)
  local bring=wc and {turrets=kit and owned(npc,kit.item) or wc.turrets,rounds=wc.stored_magazines*10,
    turret=kit and kit.name,turret_item=kit and kit.item,turret_ammo=wc.creep_ammo,
    guards=wc.artillery and Agent.ART_GUARD or nil} or nil
  -- (where the attacks on its base came from: offense_brain.py weighs the nests they came from)
  local okb,board=pcall(TeamWar.threat_board,npc,nil,c.surface,tick)
  local sources={}
  for _,a in ipairs(okb and board and board.attacks or {}) do
    -- (the nest it came from where known, else where it first hit: a wave's first hit is the base's edge, D-20)
    local at=a.nest or a.from
    if at then sources[#sources+1]={x=math.floor(at.x),y=math.floor(at.y),count=a.count or 1} end
  end
  view.attack_sources=#sources>0 and sources or nil
  for _,k in ipairs(view.nests or {}) do
    local ok,odds=pcall(Odds.cluster,npc,k,gear,bring)
    if ok then k.odds=odds end
  end
  view.attacks_made=npc.attack_log and #npc.attack_log>0 and npc.attack_log or nil
  -- its belt lines (companion/belt_lines.py): what each carries, what its connections take, what is spare
  local lines={}
  for _,line in pairs(npc.belt_lines or {}) do
    local taken,n=0,0
    for _,rate in pairs(line.claims or {}) do taken=taken+rate; n=n+1 end
    if #lines<8 and line.kind~="bus" then   -- (the bus's lanes are the bus planner's)
      local on=math.min(line.supply or 0,15)
      lines[#lines+1]={item=line.item,on=math.floor(on*100)/100,taken=math.floor(taken*100)/100,
        spare=math.floor((on-taken)*100)/100,connections=n}
    end
  end
  view.belt_lines=#lines>0 and lines or nil
  -- its long trips, its vehicle and the ones it could make (drive.lua; companion/vehicle_brain.py)
  local ok_travel,travel=pcall(function() return Drive.travel and Drive.travel(npc,tick) end)
  view.travel=ok_travel and travel or nil
  view.tick=view.tick or tick
  if TeamWorld.active() then
    -- one team: its plot, the team's research (the players run the queue) and what was said on the radio;
    -- no diplomacy, no teams to join
    local plot=TeamWorld.plot("npc-"..npc.id)
    view.team=true
    view.plot=plot and {name=plot.name,box=plot.box,outer_edges=plot.outer} or nil
    view.team_research=TeamResearch.view()
    view.radio=Agent.heard(npc,tick)
    view.threat=TeamWar.threat(npc,tick,c.surface)   -- evolution, the latest attack on its plot, breaches
    view.death_world=TeamWorld.death_world() or nil
    view.diplomacy,view.joined=nil,nil
  end
  return view
end

-- Everything of an item the engineer has: carried, worn, in its gun and ammo slots.
owned=function(npc,name)
  local c=npc.character
  local n=0
  for _,inv in ipairs({defines.inventory.character_main,defines.inventory.character_guns,
      defines.inventory.character_ammo,defines.inventory.character_armor}) do
    local i=c.get_inventory(inv)
    if i then n=n+i.get_item_count({name=name,quality="normal"}) end
  end
  return n
end

-- What it fights with, for the strategist: armor worn, guns, ammunition, capsules, fish, health.
loadout=function(npc)
  local c=npc.character
  local worn=c.get_inventory(defines.inventory.character_armor)
  local armor=worn and worn[1] and worn[1].valid_for_read and worn[1].name or nil
  local guns={}
  local g=c.get_inventory(defines.inventory.character_guns)
  for i=1,(g and #g or 0) do if g[i].valid_for_read then guns[#guns+1]=g[i].name end end
  return {armor=armor,guns=guns,magazines=owned(npc,"firearm-magazine"),piercing=owned(npc,"piercing-rounds-magazine"),
    poison=owned(npc,"poison-capsule"),turrets=owned(npc,"gun-turret"),fish=owned(npc,"raw-fish"),
    health=math.floor(c.health),max_health=math.floor(c.max_health)}
end

-- Who it knows and how things stand with them, and offers waiting for its answer (diplomacy tool).
diplomacy_view=function(npc)
  local ok,view=pcall(function() return Society.observe(npc) end)
  if not ok or type(view)~="table" then return nil end
  local out={contacts={},offers={}}
  for _,c in ipairs(view.contacts or {}) do
    if #out.contacts<8 then
      out.contacts[#out.contacts+1]={name=c.name,relation=c.force_relation,trade=c.trade or nil,
        coalition=c.coalition_id and true or nil,trust=c.trust and math.floor(c.trust*100)/100}
    end
  end
  for _,o in ipairs(view.incoming_offers or {}) do
    local from=storage.npcs and storage.npcs[o.proposer]
    out.offers[#out.offers+1]={id=o.id,kind=o.kind,from=from and from.name or o.proposer}
  end
  return out
end

-- Turret creep, as players clear nests before they have tanks: two turrets placed and loaded just
-- short of the nearest enemy building, the engineer fighting beside them until nothing hostile stands
-- near, then the turrets picked up and set down closer to the next. Returns a job, or a status
-- ("hold" while they fight, "done" when nothing is left or time is up and the turrets are back in hand).
local CREEP_GAP,CREEP_HOLD=15,3600
Agent.CREEP_PER,Agent.CREEP_MAX,Agent.CREEP_REACH,Agent.CREEP_SPREAD,Agent.CREEP_FILL=3,40,12,0.4,20

-- Nest attacks remembered with how they went (newest last), for its offense judgment: a cluster that was
-- not cleared is not tried again until it is clearly stronger (companion/offense_brain.py). The same small
-- nest was attacked four times running in run 19 (2026-10-25), each ending "done" on its timer.
Agent.ATTACKS_KEPT,Agent.NEST_REACH=8,48
function Agent.hostile_count(npc,place,radius)
  local n=0
  for _,e in ipairs(npc.character.surface.find_entities_filtered{position=place,radius=radius,
      type={"unit-spawner","turret","ammo-turret","electric-turret","fluid-turret"}}) do
    if e.valid and e.force~=npc.force and not npc.force.get_cease_fire(e.force) then n=n+1 end
  end
  return n
end
function Agent.attack_made(npc,it,status,reason)
  local c=npc.character
  if not (c and c.valid and it.args.place) then return end
  local after=Agent.hostile_count(npc,it.args.place,Agent.NEST_REACH)
  local outcome=after==0 and "cleared" or status=="failed" and tostring(reason or "failed")
    or it.outcome or (it.before and after<it.before and "weakened") or "left_standing"
  npc.attack_log=npc.attack_log or {}
  local log=npc.attack_log
  log[#log+1]={x=math.floor(it.args.place.x),y=math.floor(it.args.place.y),tick=game.tick,outcome=outcome,
    before=it.before,after=after,armed=it.armed,minutes=math.floor((game.tick-(it.created or game.tick))/3600)}
  while #log>Agent.ATTACKS_KEPT do table.remove(log,1) end
  Agent.event(npc,{kind="attack_over",target="nests",outcome=outcome,before=it.before,after=after})
end
-- Magazines from its gun's ammo slots into its pockets for turrets, keeping GUN_RESERVE for the gun itself.
-- Everything it owns counts toward its strength (the offense judgment), and 100 magazines sat in its gun
-- while two creep turrets stood empty, attack after attack (run 19, Codex's review, 2026-10-25).
Agent.GUN_RESERVE=10
function Agent.spare_ammo(npc,name,want)
  local ok,moved=pcall(function()
    local c=npc.character
    local main=c.get_main_inventory()
    local slots=c.get_inventory(defines.inventory.character_ammo)
    if not (main and slots) or main==slots then return 0 end
    local n=math.min(want,slots.get_item_count({name=name,quality="normal"})-Agent.GUN_RESERVE)
    if n<=0 then return 0 end
    local got=main.insert({name=name,count=n})
    if got>0 then slots.remove({name=name,count=got}) end
    return got
  end)
  return ok and moved or 0
end
-- A raid on an engineer's base: with nothing armed left to fight there (its combat takes the gun for
-- that), it shoots their production the way players raid one another: the best of what is in reach
-- (assemblers and furnaces first), else it closes in on their nearest building. Returns a move job,
-- "hold" while it shoots, or nil with nothing left to hit. (A character shoots a building only when
-- the building is selected, as a player's cursor selects it.)
local RAID_TYPES={"assembling-machine","furnace","lab","mining-drill","boiler","generator","offshore-pump",
  "electric-pole","inserter","container"}
local RAID_RANK={}
for i,t in ipairs(RAID_TYPES) do RAID_RANK[t]=i end
local RAID_SEARCH,RAID_REACH=60,14   -- a pistol reaches 15, a submachine gun 18
local SEEN_LATELY=300                -- a player seen this recently is where it saw them
local HOME_GROUND=120                -- an attack this close to home is defending it
raid=function(npc,args,tick)
  local other=storage.npcs and storage.npcs[args.engineer]
  local player=args.player_index and game.get_player(args.player_index)
  local force=other and other.force or player and player.force
  local c=npc.character
  if not (force and force.valid) then return nil end
  if npc.combat and npc.combat.active then return nil,"hold" end
  if owned(npc,"firearm-magazine")+owned(npc,"piercing-rounds-magazine")==0 then return nil end
  local here=c.position
  local best,best_rank,best_d,near,near_d
  for _,e in ipairs(c.surface.find_entities_filtered{force=force,position=args.place,radius=RAID_SEARCH,
      type=RAID_TYPES,limit=400}) do
    if e.valid and e.health and e.health>0 then
      local d=math.sqrt((e.position.x-here.x)^2+(e.position.y-here.y)^2)
      local rank=RAID_RANK[e.type] or 99
      if d<=RAID_REACH and (not best or rank<best_rank or (rank==best_rank and d<best_d)) then
        best,best_rank,best_d=e,rank,d
      end
      if not near or d<near_d then near,near_d=e,d end
    end
  end
  if best then
    c.selected=best
    c.shooting_state={state=defines.shooting.shooting_selected,position=best.position}
    return nil,"hold"
  end
  if not near then return nil end
  local spot=c.surface.find_non_colliding_position("character",near.position,8,1) or near.position
  return {kind="move",position={x=spot.x,y=spot.y},deadline=tick+3600}
end

-- A beaten base: nothing armed of theirs left round it, and its owner dead or away. It takes it over
-- (what stands there becomes its own, an outpost it defends) or takes what is in it (their chests and
-- furnaces, until it can carry no more); War.spoils decides. A job, "done", or nil while it is not
-- beaten yet (the raid goes on).
local TAKE_RADIUS,OWNER_NEAR,LOOT_MAX=100,150,40
local LOOT_ITEMS={"iron-plate","copper-plate","steel-plate","iron-gear-wheel","electronic-circuit",
  "advanced-circuit","plastic-bar","piercing-rounds-magazine","firearm-magazine","coal","stone-brick","stone"}
local function spoils(npc,it,args,force,owner,tick)
  local c=npc.character
  local surface,place=c.surface,args.place
  if not it.spoils then
    if owner and owner.valid and (owner.position.x-place.x)^2+(owner.position.y-place.y)^2<=OWNER_NEAR^2 then
      return nil
    end
    if surface.count_entities_filtered{force=force,position=place,radius=RAID_SEARCH,
        type={"ammo-turret","electric-turret","fluid-turret"},limit=1}>0 then return nil end
    local car=owned(npc,"car")>0 or surface.count_entities_filtered{type="car",force=npc.force,
      position=c.position,radius=60,limit=1}>0
    it.spoils=War.spoils(npc,force,place,car)
    if it.spoils=="take" then
      local taken=0
      for _,e in ipairs(surface.find_entities_filtered{force=force,position=place,radius=TAKE_RADIUS}) do
        if e.valid and e.type~="character" and pcall(function() e.force=npc.force end) then taken=taken+1 end
      end
      npc.outposts=npc.outposts or {}
      table.insert(npc.outposts,{x=place.x,y=place.y,since=tick,from=args.target})
      Agent.event(npc,{kind="base_taken",from=args.target,buildings=taken,
        position={x=math.floor(place.x),y=math.floor(place.y)}})
      return "done"
    end
    npc.transfer_permissions=npc.transfer_permissions or {}
    npc.transfer_permissions[force.name]={withdraw=true}   -- the spoils: theirs to take now
    Agent.event(npc,{kind="looting",from=args.target})
  end
  local main=c.get_main_inventory()
  if (it.looted or 0)<LOOT_MAX and main and main.count_empty_stacks()>0 then
    for _,e in ipairs(surface.find_entities_filtered{force=force,position=place,radius=RAID_SEARCH,
        type={"container","logistic-container","furnace","assembling-machine"}}) do
      local inv=e.valid and (e.type=="container" or e.type=="logistic-container")
        and e.get_inventory(defines.inventory.chest) or e.valid and e.get_output_inventory()
      for _,name in ipairs(inv and LOOT_ITEMS or {}) do
        local n=inv.get_item_count({name=name,quality="normal"})
        if n>0 then
          it.looted=(it.looted or 0)+1
          return {kind="transfer",target=e,item=name,count=n,mode="withdraw"}
        end
      end
    end
  end
  if npc.transfer_permissions then npc.transfer_permissions[force.name]=nil end
  return "done"
end
Agent.spoils=spoils

-- A long walk the way players make one in biter country: by a route round the nests, worms and
-- enemy turrets it knows of, a fresh one when the goal moves, now and then, or when it sees a danger
-- the route did not know of. The next point as a move job, or nil once within `near` of the goal.
-- While marching it does not stop for biters that keep their distance. `spare`: dangers this close
-- to the goal are what it comes to fight, and are not walked round.
local REPLAN_EVERY,LOOK_EVERY,PASSED,REST_BELOW,RESTED=7200,120,12,0.5,0.9
local DRIVE_RETRY=18000   -- a drive that failed: walked for the next 5 minutes of that trip
local WAIT_CLEAR,WAIT_CLEAR_RECALL=10800,3600   -- going home with no way clear of biters: waiting this long
local STRICT={attack=true,help=true}   -- trips it need not make: not set off on without a way clear of biters
local MAX_LEG=40   -- shorter than it sees beyond a nest's berth (176 - 100): it looks again before it is close
local LOW_AMMO,AMMO_BATCH=20,20
-- Out of magazines on the way, it makes more from the plates it carries, as a player would
-- (with mods that first takes empty magazines: the first craft of the most it can make).
-- (crafted in the background as it walks on, and what its queue still makes counted: worker.lua's craft_coming)
local ROUNDS={"firearm-magazine","piercing-rounds-magazine","uranium-rounds-magazine"}
local function ammo_coming(npc)
  local n=0
  for _,item in ipairs(ROUNDS) do n=n+(Worker.craft_coming and Worker.craft_coming(npc,item) or 0) end
  return n
end
Agent.ammo_coming=ammo_coming
local function resupply(npc)
  local have=ammo_coming(npc)
  for _,item in ipairs(ROUNDS) do have=have+owned(npc,item) end
  if have>=LOW_AMMO then return nil end
  local main=npc.character.get_main_inventory()
  if not (main and Recipes.ready(npc.force,"firearm-magazine")) then return nil end
  local hand=Recipes.hand(npc.character)
  local function held(item) return main.get_item_count({name=item,quality="normal"}) end
  for n=AMMO_BATCH,1,-1 do
    local steps=Recipes.hand_steps(npc.force,hand,held,"firearm-magazine",n)
    -- (the magazines themselves queued: the game queues what goes into them first, and the queue shows them coming)
    if steps then return {kind="craft",recipe=steps[#steps].recipe,count=steps[#steps].count,background=true} end
  end
  return nil
end
Agent.resupply=resupply
local function march(npc,it,goal,near,spare,tick)
  local c=npc.character.position
  if (c.x-goal.x)^2+(c.y-goal.y)^2<=near*near then it.route=nil; npc.marching=nil; return nil end
  local craft=resupply(npc)
  if craft then return craft end
  -- Hurt on the way: it stops out of the fight and heals before walking on.
  local body=npc.character
  local health=body.health/math.max(1,body.max_health)
  if it.resting and health>=RESTED then it.resting=nil end
  -- (not with biters on it: it walks on, shooting, and rests once clear)
  local hunted=npc.combat and npc.combat.target and npc.combat.target.valid
  if not hunted and (it.resting or health<REST_BELOW) then
    it.resting=true
    npc.marching={until_tick=tick+900}
    return "hold"
  end
  if it.land_check_at and tick<it.land_check_at then return "hold" end   -- the land is being made
  -- With a car or a tank of its own (and fuel), a long trip is driven (drive.lua): it holds here while
  -- the autopilot drives, and walks the rest from where it parked; a drive that failed is walked.
  local d=npc.drive
  if d and d.goal and (d.goal.x-goal.x)^2+(d.goal.y-goal.y)^2<=48*48 then
    if d.failed or d.done then
      local a=state(npc)
      a.log=a.log or {}
      a.log[#a.log+1]="drive "..tostring(d.why).." @"..tick
      while #a.log>8 do table.remove(a.log,1) end
    end
    if d.failed then npc.drive=nil; it.walk_until=tick+DRIVE_RETRY
    elseif d.done then npc.drive=nil
    else return "hold" end
  elseif tick>=(it.walk_until or 0) and Drive.can(npc,goal) then
    -- its parked vehicle a little way off: walked to first, on foot, the usual way round things
    local at=Drive.vehicle_at and Drive.vehicle_at(npc)
    if at and (c.x-at.x)^2+(c.y-at.y)^2>16 then
      local spot=npc.character.surface.find_non_colliding_position("character",at,6,0.5) or at
      return {kind="move",position={x=spot.x,y=spot.y},deadline=tick+3600}
    end
    Drive.start(npc,goal,near,tick,{spare=spare,strict=STRICT[it.tool],
      spare_nests=it.tool=="attack" and it.args.target=="nests"})
    pcall(Worker.cancel,npc,"combat_interrupt")
    npc.marching=nil
    return "hold"
  end
  local r=it.route
  local stale=not r or (r.goal.x-goal.x)^2+(r.goal.y-goal.y)^2>48*48 or tick>=r.until_tick
  if not stale and tick>=r.look_at then
    r.look_at=tick+LOOK_EVERY
    stale=Routes.new_sighting(npc,r)
  end
  if stale then
    -- before the first route (and a new goal): the land along the way generated, so it is all known
    if not r and Routes.prepare and not Routes.prepare(npc,goal,tick) then
      npc.marching={until_tick=tick+900}
      return "hold"
    end
    local nests=it.tool=="attack" and it.args.target=="nests"
    -- (planned a slice a tick: a far trip through nest country took seconds at once, the game held still)
    r=Routes.request(npc,goal,spare,nests,KEEP_OUT)
    if not r then
      npc.marching={until_tick=tick+900}
      return "hold"
    end
    -- No way there clear of the biters: a trip it chose is not set off on, and one under way is given up
    -- (back the way it came, which it just walked clear of them) when a new plan finds the way ahead is
    -- not clear any more: run 25 pressed on through seven groups of nests seen on the way, and died. Home
    -- (or a call home) it goes all the same, the way that spends least time in their reach.
    -- The land along this route generated first (it may stray from what was made ready), then planned
    -- again with what is there (looked at every 2 s, half a minute at most).
    if Routes.route_ready and not Routes.route_ready(npc,{x=c.x,y=c.y},r.points) then
      it.land_until=it.land_until or tick+1800
      if tick<it.land_until then
        it.route,it.land_check_at=nil,tick+120
        npc.marching={until_tick=tick+900}
        return "hold"
      end
    end
    it.land_until,it.land_check_at=nil,nil
    if not r.safe and STRICT[it.tool] then return "unsafe" end
    -- Home it must go, but not through them while it can wait: biters on the move move on (drive6: the
    -- way home through a field of 49 nests, taken at once, and died). It waits where it is, looking again
    -- every 10 s, three minutes at most (one, called home), then goes the least bad way.
    if not r.safe then
      it.unclear_since=it.unclear_since or tick
      if tick-it.unclear_since<(it.tool=="recall" and WAIT_CLEAR_RECALL or WAIT_CLEAR) then
        it.route,it.land_check_at=nil,tick+600
        npc.marching={until_tick=tick+900}
        return "hold"
      end
    else
      it.unclear_since=nil
    end
    r.until_tick,r.look_at=tick+REPLAN_EVERY,tick+LOOK_EVERY
    it.route=r
  end
  while r.points[r.k+1] and (c.x-r.points[r.k].x)^2+(c.y-r.points[r.k].y)^2<=PASSED*PASSED do r.k=r.k+1 end
  local p=r.points[r.k]
  if not r.points[r.k+1] then
    -- The last leg stops short of the goal, where it means to stand (out of their turrets' reach).
    local dx,dy=p.x-c.x,p.y-c.y
    local d=math.sqrt(dx*dx+dy*dy)
    local short=math.max(0,near-2)
    if d>short then p={x=p.x-dx/d*short,y=p.y-dy/d*short} end
  end
  -- A leg at a time, no longer than it can see: after each it looks again (a route is only as good as
  -- what it knew when it planned it, and one long leg would walk it past what it sees on the way).
  local dx,dy=p.x-c.x,p.y-c.y
  local d=math.sqrt(dx*dx+dy*dy)
  if d>MAX_LEG then p={x=c.x+dx/d*MAX_LEG,y=c.y+dy/d*MAX_LEG} end
  npc.marching={until_tick=tick+900,routed=true}
  local surface=npc.character.surface
  local spot=surface.find_non_colliding_position("character",p,12,1)
    or surface.find_non_colliding_position("character",p,64,1) or p   -- (a point out in a lake: dry land)
  if (spot.x-c.x)^2+(spot.y-c.y)^2<16 and (p.x-c.x)^2+(p.y-c.y)^2>64 then
    -- the dry land nearest that point is where it stands: no way there; blocked, planned again
    Routes.block(npc,p,tick)
    it.route=nil
    it.blocked_legs=(it.blocked_legs or 0)+1
    if it.blocked_legs>8 then return "blocked" end   -- no way there it can find (not: there)
    return march(npc,it,goal,near,spare,tick)
  end
  r.leg={x=spot.x,y=spot.y}
  return {kind="move",position={x=spot.x,y=spot.y},deadline=tick+36000}
end
Agent.march=march

-- Going after someone (an engineer or a player) the way a player does: it stops short, out of their
-- turrets' reach, and looks at what defends them. Turrets there and none of its own to creep with:
-- it backs off to get some (event attack_aborted) instead of walking into them. Hurt, it falls back
-- to where it stopped and heals before going in again; out of magazines, it goes home to re-arm.
-- Returns a job (and meta), a status, or nil to carry on with the attack.
local STAGE,THEIR_DEFENSES,REGROUP_AT,HEALED=70,40,0.35,0.8
local FAR_MIN_AMMO,FAR_PLATES,TURN_BACK_AMMO=100,100,30   -- a far raid: set off with, carry for more, turn back at
local function magazines(npc)
  return owned(npc,"firearm-magazine")+owned(npc,"piercing-rounds-magazine")+owned(npc,"uranium-rounds-magazine")
end
-- No way there clear of the biters' reach ("unsafe"), or no way there at all it could find ("blocked":
-- water, cliffs): the trip is off. Run 18 took a march that gave up as having arrived, looked for the
-- base 1500 tiles from it, and stood about in biter country.
local function no_safe_way(npc,args,why)
  -- walled off: home it stays, and its research and arming turn to what gets it through (designed_start)
  if why~="blocked" then npc.walled_off={target=args.target,tick=game.tick} end
  if why=="blocked" then
    Agent.event(npc,{kind="attack_aborted",target=args.target,why="no way there that I could find"})
    return "failed","no_way"
  end
  Agent.event(npc,{kind="attack_aborted",target=args.target,why="no way there clear of the biters"})
  return "failed","no_safe_way"
end
local function their_turrets(npc,args)
  local other=args.engineer and storage.npcs and storage.npcs[args.engineer]
  local player=args.player_index and game.get_player(args.player_index)
  local force=other and other.force or player and player.force
  if not (force and force.valid) then return 0 end
  local c=npc.character
  local n=0
  for _,e in ipairs(c.surface.find_entities_filtered{force=force,position=args.place,radius=THEIR_DEFENSES,
      type={"ammo-turret","electric-turret","fluid-turret"}}) do
    n=n+1   -- in sight from where it stopped
  end
  return n
end
-- Biters attacking their base (drawn by its pollution): it waits back where it stopped, out of their
-- way, until they are done or gone, instead of walking into the wave (they do its work meanwhile).
-- After a long wait it goes in anyway. "hold" (or a move back) while it waits, nil to go on.
local BITERS_BUSY,BUSY_RADIUS,BUSY_WAIT=5,80,18000
local function wait_out(npc,it,args,tick)
  local c=npc.character
  local busy=it.staged and c.surface.count_entities_filtered{force="enemy",type="unit",position=args.place,
    radius=BUSY_RADIUS,limit=BITERS_BUSY}>=BITERS_BUSY
  if not busy then it.wait_since=nil; return nil end
  it.wait_since=it.wait_since or tick
  if tick-it.wait_since>=BUSY_WAIT then return nil end
  local s=it.staged
  if (c.position.x-s.x)^2+(c.position.y-s.y)^2>8*8 then
    local spot=c.surface.find_non_colliding_position("character",s,8,1) or s
    return {kind="move",position={x=spot.x,y=spot.y},deadline=tick+3600}
  end
  return "hold"
end
Agent.wait_out=wait_out
-- After breaking off a fight it was losing (combat.lua), it stays out of it and heals before it goes
-- back in: half a minute at least, until it is at 80% health, two minutes at most.
local REGROUP_MIN,REGROUP_HEALED,REGROUP_MAX=1800,0.8,7200
local function regrouping(npc,tick)
  local t=npc.broke_off
  if not t or tick-t>=REGROUP_MAX then return false end
  local c=npc.character
  return tick-t<REGROUP_MIN or c.health/math.max(1,c.max_health)<REGROUP_HEALED
end
Agent.regrouping=regrouping
-- Their base, found by looking from where it stopped (it only knew roughly where they started): the
-- building of theirs in sight nearest the middle of what it sees of them near that place, or nil.
local LOOK,BASE_SPREAD=176,150
local function locate(npc,args)
  local other=storage.npcs and storage.npcs[args.engineer]
  local force=other and other.force
  if not (force and force.valid) then return nil end
  local c=npc.character
  local seen,sx,sy={},0,0
  for _,e in ipairs(c.surface.find_entities_filtered{force=force,position=c.position,radius=LOOK,limit=300}) do
    if e.valid and e.type~="character"
        and (e.position.x-args.place.x)^2+(e.position.y-args.place.y)^2<=BASE_SPREAD^2 then
      seen[#seen+1]=e; sx,sy=sx+e.position.x,sy+e.position.y
    end
  end
  if #seen==0 then return nil end
  local mx,my=sx/#seen,sy/#seen
  local best,best_d
  for _,e in ipairs(seen) do
    local d=(e.position.x-mx)^2+(e.position.y-my)^2
    if not best or d<best_d then best,best_d=e,d end
  end
  return {x=best.position.x,y=best.position.y}
end
Agent.locate=locate
local function campaign(npc,it,args,tick)
  local c=npc.character
  -- Far from home, it turns back while it still has magazines for the way (biters find it on the road),
  -- unless it can make more from the plates it carries.
  local h=npc.home
  local far=h and (c.position.x-h.x)^2+(c.position.y-h.y)^2>FAR_TRIP^2
  if far and magazines(npc)+ammo_coming(npc)<TURN_BACK_AMMO and not resupply(npc) then   -- (queued ones count)
    Agent.event(npc,{kind="attack_aborted",target=args.target,why="out of ammunition"})
    return "failed","out_of_ammo"
  end
  if not it.staged then
    local job=march(npc,it,args.place,STAGE,STAGE+THEIR_DEFENSES,tick)
    if job=="unsafe" or job=="blocked" then return no_safe_way(npc,args,job) end
    if job then return job,{step="attack"} end
    if args.rough and not args.located then
      local found=locate(npc,args)
      if not found then
        Agent.event(npc,{kind="attack_aborted",target=args.target,why="their base is not where I thought"})
        return "failed","not_found"
      end
      args.place,args.located,it.route=found,true,nil
      return campaign(npc,it,args,tick)   -- now stopping short of where it really is
    end
    it.staged={x=c.position.x,y=c.position.y}
    local turrets=their_turrets(npc,args)
    it.their_turrets=turrets
    if turrets>0 and owned(npc,"gun-turret")<2 then
      Agent.event(npc,{kind="attack_aborted",target=args.target,turrets=turrets,
        why="their turrets and none of my own to creep with"})
      return "failed","outgunned"
    end
  end
  local waiting=wait_out(npc,it,args,tick)
  if waiting then return waiting,{step="attack"} end
  local health=c.health/math.max(1,c.max_health)
  if it.regroup and health>=HEALED then it.regroup=nil end
  if it.regroup or health<REGROUP_AT or regrouping(npc,tick) then
    it.regroup=true
    local s=it.staged
    if (c.position.x-s.x)^2+(c.position.y-s.y)^2>8*8 then
      local spot=c.surface.find_non_colliding_position("character",s,8,1) or s
      return {kind="move",position={x=spot.x,y=spot.y},deadline=tick+3600},{step="attack"}
    end
    return "hold"   -- out of the fight, healing
  end
  if owned(npc,"firearm-magazine")+owned(npc,"piercing-rounds-magazine")+owned(npc,"uranium-rounds-magazine")<5 then
    Agent.event(npc,{kind="attack_aborted",target=args.target,why="out of ammunition"})
    return "failed","out_of_ammo"
  end
  return nil
end
Agent.campaign=campaign

local function hostile_buildings(npc,around,radius)
  local out={}
  for _,e in ipairs(npc.character.surface.find_entities_filtered{position=around,radius=radius,
      type={"unit-spawner","turret","ammo-turret","electric-turret","fluid-turret"}}) do
    if e.valid and e.force~=npc.force and not npc.force.get_cease_fire(e.force) then out[#out+1]=e end
  end
  return out
end
-- The turret it creeps with and its ammunition (O-7, late game: uranium rounds, whatever turrets the mods add): of the
-- ammo turrets it holds two of, the one that reaches farthest (then the sturdiest), with the best ammunition it holds
-- five of for it, by what the game data says (armory.lua). Before, gun turrets and plain or piercing magazines only.
function Agent.creep_kit(npc)
  local best,ammo
  for _,row in ipairs(Armory.catalog().turrets or {}) do
    if row.kind=="ammo" then
      for _,item in ipairs(row.items or {}) do
        local better=not best or (row.range or 0)>(best.range or 0)
          or (row.range or 0)==(best.range or 0) and (row.health or 0)>(best.health or 0)
        if better and owned(npc,item)>=2 then
          for _,a in ipairs(Armory.ammo_for(row.name)) do
            if owned(npc,a)>=5 then best,ammo={name=row.name,item=item,range=row.range,health=row.health},a; break end
          end
        end
      end
    end
  end
  return best,ammo
end
-- A campaign (O-9, the user's clear: one loop round the base, home only to restock): a group cleared, it goes on to
-- the next stop of its tour when what it carries covers what that one takes (turrets, magazines), else home. Each
-- stop is logged as its own attack. True when it moved on.
Agent.TOUR_MAX=8
-- Its pieces taken back from a fight: Worker's pickup (DesignedStart.pickup_job: all in reach at once, turrets'
-- ammunition back to its pockets), else one mine job at a time. Nil when nothing is left standing.
function Agent.pick_up(npc,entities)
  local left={}
  for _,e in pairs(entities or {}) do if e and e.valid then left[#left+1]=e end end
  if #left==0 then return nil end
  local D=Agent.designed
  if D and D.pickup_job then
    local ok,job=pcall(D.pickup_job,npc,left)
    if ok and job then return job end
  end
  return {kind="mine",target=left[1],count=1,mode="deconstruct"}
end
function Agent.tour_on(npc,it,args,tick)
  local tour=args.tour
  if args.target~="nests" or not (tour and tour[1]) or args.engineer then return false end
  if Agent.hostile_count(npc,args.place,Agent.NEST_REACH)>0 then return false end   -- (not cleared: no further)
  local stop=tour[1]
  local kit=Agent.creep_kit(npc)
  local tur=kit and kit.item or "gun-turret"
  if owned(npc,tur)<stop.turrets or magazines(npc)<stop.magazines then
    Agent.event(npc,{kind="tour_restock",turrets=owned(npc,tur),magazines=magazines(npc),
      needs={turrets=stop.turrets,magazines=stop.magazines}})
    return false
  end
  pcall(Agent.attack_made,npc,it,"done")
  table.remove(tour,1)
  args.place,args.x,args.y={x=stop.x,y=stop.y},stop.x,stop.y
  args.turrets,args.magazines=stop.turrets,stop.magazines
  it.arrived,it.creep,it.before,it.outcome,it.armed,it.route=nil,nil,nil,nil,nil,nil
  it.created,it.started=tick,tick   -- (each leg its own trip time: Agent.next times trips out from started)
  npc.combat_objective,npc.combat_mission_until=nil,nil
  Agent.event(npc,{kind="tour_next",x=stop.x,y=stop.y,left=#tour})
  return true
end
creep=function(npc,it,args,tick,over)
  local cr=it.creep or {phase="aim",cycles=0}
  it.creep=cr
  local kit,kit_ammo=Agent.creep_kit(npc)
  cr.kit=cr.kit or kit and {name=kit.name,item=kit.item} or {name="gun-turret",item="gun-turret"}
  cr.ammo=kit_ammo or cr.ammo
  local surface,c=npc.character.surface,npc.character.position
  local function mine_back()
    local job=Agent.pick_up(npc,cr.turrets)
    if not job then cr.turrets={} end
    return job
  end
  if over or cr.phase=="pickup" then
    local job=mine_back()
    if job then return job end
    if over or cr.give_up then return nil,"done" end
    cr.phase="aim"
  end
  if cr.phase=="aim" then
    local targets=hostile_buildings(npc,args.place,48)
    if #targets==0 then cr.phase="pickup"; local job=mine_back(); if job then return job end; return nil,"done" end
    table.sort(targets,function(a,b)
      return (a.position.x-c.x)^2+(a.position.y-c.y)^2<(b.position.x-c.x)^2+(b.position.y-c.y)^2 end)
    local e=targets[1].position
    local dx,dy=c.x-e.x,c.y-e.y
    local d=math.max(1,math.sqrt(dx*dx+dy*dy))
    cr.spot={x=e.x+dx/d*CREEP_GAP,y=e.y+dy/d*CREEP_GAP}
    -- O-9, the user's clear: a ring of about CREEP_PER turrets for each nest and worm of the group, all round it just
    -- outside its outer buildings (34-37 turrets on groups of 10-12, each group dead in 8-25 s, its health never under
    -- 191; its thin rings of 2-3 were where it fell to 148), put down in one walk round from its side, up to CREEP_MAX.
    -- With fewer than that a line of them across the way to the nearest, as before.
    local n=math.max(2,math.min(Agent.CREEP_MAX,args.turrets or 2,owned(npc,cr.kit.item)+#(cr.turrets or {}),
      Agent.CREEP_PER*#targets))
    cr.slots,cr.tries={},{}
    if n>=Agent.CREEP_PER*#targets then
      -- (each building its CREEP_PER, CREEP_REACH out on its outer side, a little apart, within their turrets' reach
      -- however spread the group is; the lot in order round the group from its side: one walk round)
      local cx,cy,far=0,0,0
      for _,t in ipairs(targets) do cx,cy=cx+t.position.x,cy+t.position.y end
      cx,cy=cx/#targets,cy/#targets
      local start=math.atan2(c.y-cy,c.x-cx)
      local spots={}
      for _,t in ipairs(targets) do
        local b=t.position
        local ox,oy=b.x-cx,b.y-cy
        far=math.max(far,math.sqrt(ox*ox+oy*oy))
        local out=math.sqrt(ox*ox+oy*oy)>=1 and math.atan2(oy,ox) or start   -- (one in the middle: toward it)
        for j=1,Agent.CREEP_PER do
          local a=out+(j-(Agent.CREEP_PER+1)/2)*Agent.CREEP_SPREAD
          local p={x=b.x+math.cos(a)*Agent.CREEP_REACH,y=b.y+math.sin(a)*Agent.CREEP_REACH}
          spots[#spots+1]={p=p,turn=(math.atan2(p.y-cy,p.x-cx)-start)%(2*math.pi)}
        end
      end
      table.sort(spots,function(u,v) return u.turn<v.turn end)
      for k=1,n do cr.slots[k],cr.tries[k]=spots[k].p,0 end
      cr.hold_at,cr.hold_r={x=cx,y=cy},far+4
    else
      for k=1,n do
        local side=(k-(n+1)/2)*2.6
        cr.slots[k]={x=cr.spot.x-dy/d*side,y=cr.spot.y+dx/d*side}
        cr.tries[k]=0
      end
      cr.hold_at,cr.hold_r=cr.spot,CREEP_GAP+6
    end
    cr.turrets,cr.fill,cr.loaded={},nil,{}   -- (each ring its own share of what it carries)
    cr.phase,cr.cycles="place",cr.cycles+1
  end
  -- What it carries loose goes into a turret, and the spare from its gun's ammo slot when that is short (the gun keeps
  -- a reserve); filled, O-9: what it carries shared among the ring, up to CREEP_FILL each. A job, or nil and what it has.
  local main=npc.character.get_main_inventory()
  local function loose(name) return main and main.get_item_count(name) or 0 end
  local ammo=cr.ammo or (owned(npc,"piercing-rounds-magazine")>=5 and "piercing-rounds-magazine" or "firearm-magazine")
  local function fill(t)
    cr.fill=cr.fill or math.max(5,math.min(Agent.CREEP_FILL,
      math.floor((owned(npc,ammo)-Agent.GUN_RESERVE)/math.max(1,#(cr.slots or {})))))
    local inv=t.get_inventory(defines.inventory.turret_ammo)
    local has=inv and inv.get_item_count(ammo) or 0
    if inv and has<cr.fill and loose(ammo)<5 then Agent.spare_ammo(npc,ammo,20) end
    if inv and has<cr.fill and loose(ammo)>=5 then
      return {kind="transfer",target=t,item=ammo,count=math.min(cr.fill-has,loose(ammo)),mode="deposit",
        inventory=defines.inventory.turret_ammo},has
    end
    return nil,has
  end
  if cr.phase=="place" then
    -- (each loaded as soon as it stands, before the next: none stands empty while it walks on round them)
    for k=1,#cr.slots do
      local t=cr.turrets[k]
      if not (t and t.valid) then
        t=surface.find_entities_filtered{name=cr.kit.name,force=npc.force,position=cr.slots[k],radius=2}[1]
        cr.turrets[k]=t
      end
      if t and t.valid then
        if not (cr.loaded or {})[k] then   -- (once each as it goes round; the load phase tops them all up after)
          local job=fill(t)
          if job then return job end
          cr.loaded=cr.loaded or {}
          cr.loaded[k]=true
        end
      elseif cr.tries[k]<3 and owned(npc,cr.kit.item)>0 then
        cr.tries[k]=cr.tries[k]+1
        local at=Hands.free_spot(npc,surface,cr.kit.name,cr.slots[k],4,npc.force) or cr.slots[k]   -- (W-25)
        cr.slots[k]=at
        return {kind="build",entity_name=cr.kit.name,item=cr.kit.item,position={x=at.x,y=at.y},direction=0}
      end
    end
    cr.phase,cr.since="load",tick
  end
  if cr.phase=="load" then
    local loaded=0
    for k=1,#cr.slots do
      local t=cr.turrets[k]
      if t and t.valid then
        local job,has=fill(t)
        if job then return job end
        if has>0 then loaded=loaded+1 end
      end
    end
    if loaded==0 then
      -- No ammunition for them: standing by empty turrets does nothing. They come back and the attack ends.
      cr.phase,cr.give_up,it.outcome="pickup",true,"turrets_empty"
      return creep(npc,it,args,tick,over)
    end
    cr.phase,cr.since="hold",tick
  end
  if cr.phase=="hold" then
    local standing=0
    for k=1,#cr.slots do local t=cr.turrets[k]; if t and t.valid then standing=standing+1 end end
    if #hostile_buildings(npc,cr.hold_at or cr.spot,cr.hold_r or CREEP_GAP+6)==0 or tick-cr.since>CREEP_HOLD or standing==0 then
      cr.phase="pickup"
      return creep(npc,it,args,tick,over)
    end
    return nil,"hold"
  end
  return nil,"hold"
end

-- Artillery on a nest cluster (O-7, late game): its artillery put down far out of their reach (their worms', and the
-- notice of their biters) yet within its own (ART_REACH of its range, research counted), loaded with shells, its creep
-- turrets round it toward them; the shells clear the nests, and what they send at it is fought there by the turrets
-- and by itself. Picked up once nothing is left in reach, the time is up, or it has no shells left.
Agent.ART_REACH,Agent.ART_SHELLS,Agent.ART_GUARD=0.8,20,3
-- (on it or in its chests: shells stack one to a slot, most lie in its store)
function Agent.held(npc,name)
  local n=owned(npc,name)
  for _,chest in ipairs(npc.storage_chests or {}) do
    if chest.valid then n=n+chest.get_inventory(defines.inventory.chest).get_item_count(name) end
  end
  return n
end
function Agent.artillery_kit(npc)
  for _,row in ipairs(Armory.catalog().turrets or {}) do
    if row.kind=="artillery" then
      for _,item in ipairs(row.items or {}) do
        if Agent.held(npc,item)>=1 then
          for _,a in ipairs(Armory.ammo_for(row.name)) do
            if Agent.held(npc,a)>=1 then
              local bonus=1+((npc.force.artillery_range_modifier or 0))
              return {name=row.name,item=item,range=(row.range or 0)*bonus},a
            end
          end
        end
      end
    end
  end
end
function Agent.artillery(npc,it,args,tick,over)
  local ar=it.artillery or {phase="place"}
  it.artillery=ar
  local surface,c=npc.character.surface,npc.character.position
  local kit,shell=Agent.artillery_kit(npc)
  ar.kit=ar.kit or kit and {name=kit.name,item=kit.item,range=kit.range}
  ar.shell=shell or ar.shell
  if not ar.kit then return nil,"done" end
  local function mine_back()
    local all={}
    for _,t in pairs(ar.guards or {}) do all[#all+1]=t end
    all[#all+1]=ar.turret
    return Agent.pick_up(npc,all)
  end
  if over or ar.phase=="pickup" then
    local job=mine_back()
    if job then return job end
    return nil,"done"
  end
  -- guards placed are found again by where they were put
  if ar.pending then
    local t=surface.find_entities_filtered{name=ar.pending.name,force=npc.force,position=ar.pending.at,radius=2}[1]
    if t then ar.guards=ar.guards or {}; ar.guards[#ar.guards+1]=t end
    ar.pending=nil
  end
  if ar.phase=="place" then
    if not (ar.turret and ar.turret.valid) then
      ar.turret=surface.find_entities_filtered{name=ar.kit.name,force=npc.force,position=c,radius=10}[1]
    end
    if not (ar.turret and ar.turret.valid) then
      ar.tries=(ar.tries or 0)+1
      if ar.tries>3 or owned(npc,ar.kit.item)<1 then ar.phase="pickup"; it.outcome="artillery_not_placed"; return nil,"hold" end
      local at=Hands.free_spot(npc,surface,ar.kit.name,c,8,npc.force) or c
      return {kind="build",entity_name=ar.kit.name,item=ar.kit.item,position={x=at.x,y=at.y},direction=0}
    end
    -- its guard: creep turrets between it and them, as many as it held when it set them out (ART_GUARD at most)
    if not ar.guard_kit then
      local gk0,ga0=Agent.creep_kit(npc)
      ar.guard_kit=gk0 and {name=gk0.name,item=gk0.item} or false
      ar.guard_ammo0=ga0
      ar.guard_want=gk0 and math.min(Agent.ART_GUARD,owned(npc,gk0.item)) or 0
    end
    local gk,gammo=ar.guard_kit or nil,ar.guard_ammo0
    ar.guards=ar.guards or {}
    if gk and #ar.guards<ar.guard_want and owned(npc,gk.item)>0 and (ar.guard_tries or 0)<Agent.ART_GUARD*2 then
      ar.guard_tries=(ar.guard_tries or 0)+1
      local dx,dy=args.place.x-ar.turret.position.x,args.place.y-ar.turret.position.y
      local d=math.max(1,math.sqrt(dx*dx+dy*dy))
      local side=(#ar.guards-1)*3
      local want={x=ar.turret.position.x+dx/d*6-dy/d*side,y=ar.turret.position.y+dy/d*6+dx/d*side}
      local at=Hands.free_spot(npc,surface,gk.name,want,4,npc.force) or want
      ar.guard_ammo=gammo
      ar.pending={name=gk.name,at={x=at.x,y=at.y}}
      return {kind="build",entity_name=gk.name,item=gk.item,position={x=at.x,y=at.y},direction=0}
    end
    ar.phase,ar.since="load",tick
  end
  if ar.phase=="load" then
    if not ar.turret.valid then ar.phase="pickup"; return nil,"hold" end
    local inv=ar.turret.get_inventory(defines.inventory.artillery_turret_ammo)
    local main=npc.character.get_main_inventory()
    if ar.shell and inv and inv.get_item_count(ar.shell)<5 and main and main.get_item_count(ar.shell)>0 then
      -- (as many as fit: a vanilla artillery turret holds 15, and a transfer that cannot all go in reads as failed)
      local room=0
      local ok,n=pcall(function() return inv.get_insertable_count(ar.shell) end)
      room=ok and tonumber(n) or 15
      local count=math.min(Agent.ART_SHELLS,main.get_item_count(ar.shell),room)
      if count>0 then
        return {kind="transfer",target=ar.turret,item=ar.shell,count=count,mode="deposit",
          inventory=defines.inventory.artillery_turret_ammo}
      end
    end
    for _,t in ipairs(ar.guards or {}) do
      local ginv=t.valid and t.get_inventory(defines.inventory.turret_ammo)
      if ginv and ar.guard_ammo and ginv.get_item_count(ar.guard_ammo)<5 and main and main.get_item_count(ar.guard_ammo)>=5 then
        return {kind="transfer",target=t,item=ar.guard_ammo,count=math.min(20,main.get_item_count(ar.guard_ammo)),
          mode="deposit",inventory=defines.inventory.turret_ammo}
      end
    end
    if not (inv and ar.shell and inv.get_item_count(ar.shell)>0) then ar.phase="pickup"; it.outcome="no_shells"; return nil,"hold" end
    ar.phase,ar.since="hold",tick
  end
  if ar.phase=="hold" then
    local left=0
    for _,e in ipairs(hostile_buildings(npc,args.place,Agent.NEST_REACH)) do
      if ar.turret.valid and (e.position.x-ar.turret.position.x)^2+(e.position.y-ar.turret.position.y)^2<=ar.kit.range^2 then
        left=left+1
      end
    end
    local inv=ar.turret.valid and ar.turret.get_inventory(defines.inventory.artillery_turret_ammo)
    if not ar.turret.valid or left==0 or tick-ar.since>CREEP_HOLD*3 then
      ar.phase="pickup"
      return Agent.artillery(npc,it,args,tick,over)   -- (straight to picking it all up)
    end
    if inv and inv.get_item_count(ar.shell or "")<3 then
      -- more shells in, while it has them; none on it but some in its store: it fetches them (its pockets hold few)
      local main=npc.character.get_main_inventory()
      if ar.shell and main and main.get_item_count(ar.shell)==0 and Agent.held(npc,ar.shell)>0 and Agent.designed then
        local job=Agent.designed.acquire(npc,ar.shell,math.max(1,math.min(Agent.ART_SHELLS,main.count_empty_stacks())),0)
        if job then return job end
      end
      ar.phase="load"
    end
    return nil,"hold"
  end
  return nil,"hold"
end

-- What arm_up gets, best first: the best armor and gun it can make that beat what it has (a group
-- stops at what it already owns), ammunition (piercing once researched), poison capsules, turrets.
local ARMORS={"modular-armor","heavy-armor","light-armor"}
local GUNS={"submachine-gun","pistol"}
local CAPSULES={{"cluster-grenade",4},{"grenade",10},{"poison-capsule",10},{"slowdown-capsule",5,"foe"},
  {"distractor-capsule",3},{"defender-capsule",5}}   -- (made by hand: a grenade is 10 coal and 5 iron)
-- The vehicles it can drive, sturdiest first (drive.lua, from the game's data), and whether it has one of its own.
local function vehicle_kinds()
  return Drive.vehicles and Drive.vehicles() or {{name="tank",item="tank",heavy=true},{name="car",item="car"}}
end
local function has_vehicle(npc)
  if npc.parked and npc.parked.valid then return true end
  for _,k in ipairs(vehicle_kinds()) do if owned(npc,k.item)>0 then return true end end
  return false
end
arm_wants=function(npc,args,skipped)
  local function ready(n) return not skipped[n] and Recipes.ready(npc.force,n) end
  local focus=args.focus or "all"
  local out={}
  local function best(list)
    for _,name in ipairs(list) do
      if owned(npc,name)>0 then return end
      if ready(name) then out[#out+1]={name,1} return end
    end
  end
  local ammo_name=ready("piercing-rounds-magazine") and "piercing-rounds-magazine" or "firearm-magazine"
  if npc.foe and focus=="all" then   -- someone to take out: magazines and turrets to put down first
    if ready(ammo_name) then out[#out+1]={ammo_name,math.min(50,args.ammo or 200)} end
    if ready("gun-turret") then out[#out+1]={"gun-turret",4} end
    -- walled off by biters from them: the sturdiest vehicle it can make (a tank drives through the nests, a car
    -- past them)
    if npc.walled_off and not has_vehicle(npc) then
      for _,k in ipairs(vehicle_kinds()) do
        if ready(k.item) then out[#out+1]={k.item,1} break end
      end
    end
  end
  -- A vehicle of its own, when its long trips make one pay (companion/vehicle_brain.py names which), and fuel
  -- for it: the best it can get (a fuel no recipe makes, coal or wood, is mined).
  if focus=="vehicle" and args.vehicle then
    if not has_vehicle(npc) and ready(args.vehicle) then out[#out+1]={args.vehicle,1} end
    local kind
    for _,k in ipairs(vehicle_kinds()) do if k.item==args.vehicle then kind=k end end
    local fuels=Drive.fuels and kind and Drive.fuels(kind.name) or {}
    local have=0
    for _,f in ipairs(fuels) do have=have+owned(npc,f) end
    if have<10 then
      for _,f in ipairs(fuels) do
        local raw=not skipped[f] and not (prototypes and prototypes.recipe and prototypes.recipe[f])
        if ready(f) or raw then out[#out+1]={f,20} break end
      end
    end
  end
  if focus=="all" or focus=="armor" then best(ARMORS) end
  if focus=="all" or focus=="weapon" then best(GUNS) end
  if focus=="all" or focus=="ammo" then
    local ammo=ready("piercing-rounds-magazine") and "piercing-rounds-magazine" or "firearm-magazine"
    if ready(ammo) then out[#out+1]={ammo,args.ammo or 200} end
  end
  if focus=="all" or focus=="capsules" then
    -- what it throws (combat_equipment's playbook): grenades for groups and nests, poison for worms,
    -- slowdown for people, robots to fight beside it or to cover a getaway
    for _,c in ipairs(CAPSULES) do
      if ready(c[1]) and (c[3]~="foe" or npc.foe) then out[#out+1]={c[1],c[2]} end
    end
  end
  -- (a ring for the group it means to take: as many as asked, O-9)
  if (focus=="all" or focus=="turrets") and ready("gun-turret") then out[#out+1]={"gun-turret",args.turrets or 4} end
  -- repair packs for a vehicle of its own (badly hurt, it is driven clear and mended with them)
  if (focus=="all" or focus=="vehicle") and ready("repair-pack") and has_vehicle(npc) then
    out[#out+1]={"repair-pack",20}
  end
  return out
end

-- The companion's poll: engineers waiting for a decision, and what came of earlier intentions.
function Agent.observe()
  storage.agent_bridge_tick=game.tick
  local requests,results={},{}
  for id,npc in pairs(storage.npcs or {}) do
    local a=npc.agent
    if a and npc.character and npc.character.valid then
      if a.pending then
        requests[#requests+1]={request_id=a.pending.request_id,npc_id=id,reason=a.pending.reason,
          events=a.pending.events,situation=Agent.situation(npc,game.tick)}
      end
      for _,r in ipairs(a.results) do r.npc_id=id; results[#results+1]=r end
      a.results={}
    end
  end
  return {ok=true,tick=game.tick,requests=requests,results=results}
end

local function find_player(name)
  if type(name)~="string" then return nil end
  for _,player in pairs(game.players) do if player.name==name then return player end end
end

local function find_engineer(name)
  for _,other in pairs(storage.npcs or {}) do
    if other.name==name and other.character and other.character.valid then return other end
  end
end

-- Where someone is: a player (by name), an engineer (by name), or {x,y}.
local function place_of(to)
  if type(to)=="table" and type(to.x)=="number" and type(to.y)=="number" then return {x=to.x,y=to.y} end
  local player=find_player(to)
  if player then return {x=player.position.x,y=player.position.y},player end
  local other=find_engineer(to)
  if other then return {x=other.home.x,y=other.home.y},nil,other end
end

-- "(120, -31)" or "x=120, y=-31" in a line becomes [gps=120,-31,nauvis]: click it to see the place on the map.
function Agent.gps(message,surface)
  local function tag(x,y)
    return "[gps="..math.floor(tonumber(x)+0.5)..","..math.floor(tonumber(y)+0.5)..","..(surface or "nauvis").."]"
  end
  message=message:gsub("%(%s*[xX]%s*[=:]%s*(%-?%d+%.?%d*)%s*,%s*[yY]%s*[=:]%s*(%-?%d+%.?%d*)%s*%)",tag)
  message=message:gsub("[xX]%s*[=:]%s*(%-?%d+%.?%d*)%s*,%s*[yY]%s*[=:]%s*(%-?%d+%.?%d*)",tag)
  message=message:gsub("%((%-?%d+%.?%d*)%s*,%s*(%-?%d+%.?%d*)%)",tag)
  return message
end

-- The team radio: every teammate hears what an engineer says aloud. Lines are kept for its next
-- thought (situation.radio); only a line that names it wakes it at once, so they do not chatter.
local HEARD,HEARD_FOR=6,36000
function Agent.heard(npc,tick)
  local out={}
  for _,h in ipairs(state(npc).heard or {}) do
    if tick-h.tick<=HEARD_FOR then out[#out+1]={from=h.from,text=h.text,seconds_ago=math.floor((tick-h.tick)/60)} end
  end
  return #out>0 and out or nil
end
local function radio(npc,to,message)
  local tick=game.tick
  for _,other in pairs(storage.npcs or {}) do
    if other~=npc and Agent.thinking(other) then
      local a=state(other)
      a.heard=a.heard or {}
      table.insert(a.heard,{from=npc.name,text=message,tick=tick})
      while #a.heard>HEARD do table.remove(a.heard,1) end
      local name=other.name:lower()
      local named=(to and to:lower()==name) or (" "..message:lower().." "):find("[^%w]"..name.."[^%w]")
      if named then Agent.event(other,{kind="radio",from=npc.name,text=message}) end
    end
  end
end

-- How often an engineer speaks: a line to the whole team at most every 15 s (and the radio carries one
-- line every 3 s between them all); an answer to a player at most every 3 s.
local SAY_GAP,REPLY_GAP,RADIO_GAP=900,180,180
local function may_speak(npc,to,tick)
  local a=state(npc)
  if find_player(to) then   -- an answer to a player (its own pace: it does not hold up the radio)
    if tick<(a.replied_tick or -1e9)+REPLY_GAP then return false,"spoke_just_now" end
    a.replied_tick=tick
    return true
  end
  if tick<(a.said_tick or -1e9)+SAY_GAP then return false,"spoke_just_now" end
  if tick<(storage.radio_tick or -1e9)+RADIO_GAP then return false,"radio_busy" end
  a.said_tick,storage.radio_tick=tick,tick
  return true
end

local function say(npc,to,message)
  message=Radio.line(message)   -- like a player on the radio: short, no dashes (before places become links)
  local surface=npc.character and npc.character.valid and npc.character.surface.name or nil
  local line="["..npc.name.."] "..Agent.gps(message,surface)
  local player=find_player(to)
  if player then player.print(line) else game.print(line) end
  if Agent.recorder then Agent.recorder.say(npc,to,message) end
  if TeamWorld.active() and not player then radio(npc,to,message) end
end

-- The team world: no deals, no treaties, and each engineer stays in its own plot (no moving house,
-- no building blocks at a player's base).
local NOT_ON_A_TEAM={trade=true,diplomacy=true,join=true,relocate=true,build_at=true}

local function validate(npc,it,request_id,heard)
  if type(it)~="table" or not TOOLS[it.tool] then return nil,"unknown_tool" end
  if TeamWorld.active() and NOT_ON_A_TEAM[it.tool] then return nil,"one_team" end
  local args=type(it.args)=="table" and it.args or {}
  local out={tool=it.tool,args={},priority=PRIORITY[it.priority] and it.priority or "next",
    reason=text(it.reason,160),request_id=request_id,created=game.tick,step=1}
  -- (the attack on its home it answers, if any: once that is over, "now" is over too: Agent.settle)
  if npc.alarm and not War.over(npc,game.tick) then out.answers=npc.alarm.first end
  local t=it.tool
  if t=="say" then
    out.args.text=text(args.text,240); out.args.to=text(args.to,64)
    if not out.args.text then return nil,"say_text" end
  elseif t=="remember" then
    -- a note, the current plan (one, replaced), or a secret (never said: plans it keeps to itself)
    out.args.note=text(args.note,200)
    out.args.kind=({note=true,plan=true,secret=true})[args.kind] and args.kind or "note"
    if not out.args.note then return nil,"note" end
  elseif t=="diplomacy" then
    -- Offers and war, through the same rules every faction's diplomacy follows.
    local action=args.action
    if action=="accept" or action=="reject" then
      local id=math.floor(tonumber(args.offer) or -1)
      if id<0 then return nil,"offer" end
      out.args.action,out.args.candidate=action,action..":"..id
    else
      local other=find_engineer(args.target)
      local player=not other and find_player(args.target)
      if player then
        -- A player: war is declared, peace or an alliance offered (they accept with /npc).
        if not ({declare_war=true,offer_peace=true,offer_alliance=true})[action] then return nil,"action" end
        if action=="declare_war" and Personality.has and Personality.has(npc,"loyal")
            and (Diplomacy.state(npc.force,player.force)=="allied"
              or (npc.joined and npc.joined.player_index==player.index)) then
          return nil,"loyal"
        end
        out.args.target,out.args.player_index,out.args.action=player.name,player.index,action
        return out
      end
      if not other or other==npc then return nil,"target" end
      out.args.target,out.args.engineer=other.name,other.id
      if action=="declare_war" then
        if npc.force.get_friend(other.force) and Personality.has and Personality.has(npc,"loyal") then
          return nil,"loyal"   -- a loyal engineer does not turn on an ally
        end
        out.args.action="declare_war"
      elseif action=="offer_alliance" or action=="offer_peace" or action=="offer_trade" then
        out.args.action=action
        out.args.candidate=({offer_alliance="offer-alliance:",offer_peace="offer-peace:",
          offer_trade="offer-trade:"})[action]..other.id
      else return nil,"action" end
    end
  elseif t=="quiet" then
    out.args.on=args.on~=false
  elseif t=="prioritize" then
    -- an item or a section of its factory raised or lowered, for a while or until something happens
    local checked,why=Priorities.check(npc,args)
    if not checked then return nil,why end
    out.args=checked
  elseif t=="relocate" then
    -- start again somewhere safer: a place, or (none given) away from the nearest threat
    if type(args.x)=="number" and type(args.y)=="number" then out.args.place={x=args.x,y=args.y} end
    out.args.distance=math.max(150,math.min(800,math.floor(tonumber(args.distance) or 350)))
  elseif t=="research" then
    local tech=npc.force.technologies[args.technology or ""]
    if not (tech and tech.enabled and not tech.researched) then return nil,"technology" end
    out.args.technology=tech.name
  elseif t=="build" then
    out.args.goal=text(args.goal,64)
    if not out.args.goal then return nil,"goal" end
    local D=Agent.designed
    for _,purpose in ipairs(D and D.failed and D.failed(npc) or {}) do
      if purpose==out.args.goal then return nil,"failed_recently" end   -- the planners just could not
    end
    out.args.why=text(args.why,200)
  elseif t=="stockpile" then
    if not (type(args.item)=="string" and prototypes.item[args.item]) then return nil,"item" end
    out.args.item=args.item
    out.args.count=math.max(1,math.min(500,math.floor(tonumber(args.count) or 10)))
  elseif t=="deliver" then
    local items={}
    for _,i in ipairs(type(args.items)=="table" and args.items or {}) do
      if type(i)=="table" and type(i.name)=="string" and prototypes.item[i.name] and #items<6 then
        items[#items+1]={name=i.name,count=math.max(1,math.min(400,math.floor(tonumber(i.count) or 1)))}
      end
    end
    if #items==0 then return nil,"items" end
    local where,player,other=place_of(args.to)
    if not where then return nil,"recipient" end
    out.args.items,out.args.to,out.args.place=items,text(args.to,64),where
    out.args.player_index=player and player.index or nil
    out.args.engineer=other and other.id or nil
    out.args.asked=(player and heard and heard[player.name]) or nil   -- (Agent.away_needed)
  elseif t=="guard" then
    local where,player=place_of(args.place)
    if args.place=="marked" and type(args.for_player)=="string" then
      local p=find_player(args.for_player)
      where=p and storage.player_marks and storage.player_marks[p.index]
      player=p
    end
    if not where then return nil,"place" end
    out.args.place,out.args.player_index=where,player and player.index or nil
    out.args.minutes=math.max(1,math.min(60,math.floor(tonumber(args.minutes) or 10)))
  elseif t=="visit" then
    local where,player,other=place_of(args.to)
    if type(args.x)=="number" and type(args.y)=="number" then where,player,other={x=args.x,y=args.y},nil,nil
    elseif args.to=="home" then where,player,other={x=npc.home.x,y=npc.home.y},nil,nil end
    if args.to=="marked" and type(args.for_player)=="string" then
      player=find_player(args.for_player)
      where=player and storage.player_marks and storage.player_marks[player.index]
      player=nil   -- the spot, not the person
    end
    if not where then return nil,"place" end
    out.args.to=text(args.to,64); out.args.place=where
    out.args.player_index=player and player.index or nil
    out.args.engineer=other and other.id or nil
    out.args.minutes=math.max(0,math.min(30,math.floor(tonumber(args.minutes) or 3)))
    out.args.asked=(player and heard and heard[player.name]) or nil   -- (the player it goes to asked: Agent.away_needed)
  elseif t=="give" then
    local items={}
    for _,i in ipairs(type(args.items)=="table" and args.items or {}) do
      if type(i)=="table" and type(i.name)=="string" and prototypes.item[i.name] and #items<6 then
        items[#items+1]={name=i.name,count=math.max(1,math.min(400,math.floor(tonumber(i.count) or 1)))}
      end
    end
    if #items==0 then return nil,"items" end
    local _,player,other=place_of(args.to)
    if not (player or other) then return nil,"recipient" end   -- hands it to a person
    out.args.items,out.args.to=items,text(args.to,64)
    out.args.player_index=player and player.index or nil
    out.args.engineer=other and other.id or nil
    out.args.asked=(player and heard and heard[player.name]) or nil   -- (Agent.away_needed)
  elseif t=="arm_up" then
    -- armor, gun, ammo, capsules (and turrets to creep with), or all of it
    local focus=({all=true,armor=true,weapon=true,ammo=true,capsules=true,turrets=true,vehicle=true})[args.focus]
      and args.focus or "all"
    out.args.focus=focus
    if tonumber(args.turrets) then out.args.turrets=math.max(2,math.min(Agent.CREEP_MAX,math.floor(tonumber(args.turrets)))) end
    -- (a vehicle of its own: which one, by its item)
    if focus=="vehicle" and type(args.vehicle)=="string" then
      for _,k in ipairs(vehicle_kinds()) do if k.item==args.vehicle then out.args.vehicle=k.item end end
    end
    out.args.ammo=math.max(20,math.min(400,math.floor(tonumber(args.ammo) or 200)))
  elseif t=="build_at" then
    -- A production block somewhere (a player's base): what it makes, how many a second, where.
    if not (type(args.item)=="string" and prototypes.item[args.item]) then return nil,"item" end
    local where,player=place_of(args.place)
    if args.place=="marked" and type(args.for_player)=="string" then
      player=find_player(args.for_player)
      where=player and storage.player_marks and storage.player_marks[player.index]
    elseif type(args.x)=="number" and type(args.y)=="number" then where={x=args.x,y=args.y} end
    if not player and type(args.for_player)=="string" then player=find_player(args.for_player) end
    if not where then return nil,"place" end
    out.args.item,out.args.place=args.item,where
    out.args.rate=math.max(0.1,math.min(60,tonumber(args.rate) or 1))
    out.args.player_index=player and player.index or nil
  elseif t=="join" then
    local player=find_player(args.to)
    if not player then return nil,"player" end
    out.args.to,out.args.player_index,out.args.join=player.name,player.index,args.join~=false
  elseif t=="attack" then
    -- Another engineer's base, a player (where they are), biter nests near a place (or the nearest
    -- ones), or a place.
    local other=find_engineer(args.target)
    if other and other==npc then return nil,"target" end
    local player=not other and find_player(args.target)
    if TeamWorld.active() and (other or player) then return nil,"one_team" end   -- only nests and places
    if player and not (player.character and player.character.valid) then return nil,"target_not_in_the_world" end
    if player and Personality.has and Personality.has(npc,"loyal")
        and (Diplomacy.state(npc.force,player.force)=="allied"
          or (npc.joined and npc.joined.player_index==player.index)) then
      return nil,"loyal"
    end
    -- Where they are is known only roughly (where they started, see Intel), or where its side sees them.
    local place
    if other then
      place=Intel.roughly(npc,"npc:"..other.id,other.home)
      out.args.rough=place.x~=other.home.x or place.y~=other.home.y   -- (it looks for the real place)
    elseif player then
      local spawn=Intel.spawn_of(npc,player)
      place=Intel.sees(npc,player.position) and {x=player.position.x,y=player.position.y} or spawn
      out.args.player_index,out.args.spawn=player.index,spawn
    elseif type(args.x)=="number" and type(args.y)=="number" then place={x=args.x,y=args.y}
    elseif args.target=="nests" then
      local nests=BaseMemory.threats(npc,{x=0,y=0},400,1)
      if nests[1] then place={x=nests[1].x,y=nests[1].y} end
    end
    if not place then return nil,"target" end
    out.args.target,out.args.engineer,out.args.place=text(args.target,64),other and other.id or nil,place
    out.args.minutes=math.max(1,math.min(20,math.floor(tonumber(args.minutes) or 5)))
    -- (what it takes along: as many turrets as it can spare, magazines for the fight; O-5)
    if tonumber(args.turrets) then out.args.turrets=math.max(2,math.min(Agent.CREEP_MAX,math.floor(tonumber(args.turrets)))) end
    if tonumber(args.magazines) then out.args.magazines=math.max(10,math.min(800,math.floor(tonumber(args.magazines)))) end
    if args.method=="artillery" or args.method=="creep" then out.args.method=args.method end
    if tonumber(args.shells) then out.args.shells=math.max(1,math.min(60,math.floor(tonumber(args.shells)))) end
    -- (O-9: a campaign, the groups after this one in order round its base, each with what it takes)
    if out.args.target=="nests" and type(args.tour)=="table" then
      local tour={}
      for _,s in ipairs(args.tour) do
        if #tour>=Agent.TOUR_MAX then break end
        if type(s)=="table" and type(s.x)=="number" and type(s.y)=="number" then
          tour[#tour+1]={x=s.x,y=s.y,turrets=math.max(2,math.min(Agent.CREEP_MAX,math.floor(tonumber(s.turrets) or 2))),
            magazines=math.max(10,math.min(800,math.floor(tonumber(s.magazines) or 40)))}
        end
      end
      if #tour>0 then out.args.tour=tour end
    end
  elseif t=="trade" then
    local player=find_player(args.to)
    if not player then return nil,"player" end
    local give,want=args.give_item,args.want_item
    if not (type(give)=="string" and prototypes.item[give] and type(want)=="string" and prototypes.item[want]
        and give~=want) then return nil,"items" end
    local gn=math.floor(tonumber(args.give_count) or 0)
    local wn=math.floor(tonumber(args.want_count) or 0)
    if gn<1 or wn<1 or gn>5000 or wn>5000 then return nil,"counts" end
    -- Worth a trip: a couple of stacks at least (players trade in the hundreds and thousands).
    local stack=prototypes.item[give].stack_size or 100
    if gn<math.min(200,2*stack) then return nil,"too_small" end
    out.args.to,out.args.player_index=player.name,player.index
    out.args.give={{name=give,count=gn}}
    out.args.want={name=want,count=wn}
  elseif t=="ask" then
    local player=find_player(args.to)
    if not player then return nil,"player" end
    out.args.question=text(args.question,240)
    if not out.args.question then return nil,"question" end
    out.args.question=Radio.line(out.args.question)
    out.args.player_index,out.args.wants_place=player.index,args.wants_place==true
  end
  return out
end

-- The strategist's answer: say and remember at once, the rest goes on the agenda by priority.
function Agent.submit(payload)
  if type(payload)~="table" or type(payload.request_id)~="string" then return {ok=false,reason="request_id"} end
  local npc
  for _,candidate in pairs(storage.npcs or {}) do
    if candidate.agent and candidate.agent.pending and candidate.agent.pending.request_id==payload.request_id then
      npc=candidate
    end
  end
  if not npc then return {ok=false,reason="unknown_request",request_id=payload.request_id} end
  local a=state(npc)
  local heard={}   -- players who spoke to it directly in what this thought answers (a visit then is theirs to call)
  for _,e in ipairs(a.pending.events or {}) do
    if e.kind=="chat" and type(e.from)=="string" and not e.to_everyone then heard[e.from]=true end
  end
  a.pending=nil
  local accepted,rejected=0,{}
  for _,raw in ipairs(type(payload.intentions)=="table" and payload.intentions or {}) do
    local it,why=validate(npc,raw,payload.request_id,heard)
    if not it then
      local tool=type(raw)=="table" and type(raw.tool)=="string" and raw.tool or nil
      rejected[#rejected+1]={tool=tool,reason=why}
      result(npc,{request_id=payload.request_id,tool=tool or "unknown"},"rejected",why)
    elseif (it.tool=="say" or it.tool=="ask") and npc.silent then
      rejected[#rejected+1]={tool=it.tool,reason="silent"}
      result(npc,it,"rejected","silent")
    elseif it.tool=="say" then
      local ok,why=may_speak(npc,it.args.to,game.tick)
      if ok then say(npc,it.args.to,it.args.text); accepted=accepted+1; result(npc,it,"done")
      else rejected[#rejected+1]={tool="say",reason=why}; result(npc,it,"rejected",why) end
    elseif it.tool=="remember" then
      if it.args.kind=="plan" then a.plan=it.args.note
      elseif it.args.kind=="secret" then
        a.secrets=a.secrets or {}
        a.secrets[#a.secrets+1]=it.args.note
        while #a.secrets>MAX_SECRETS do table.remove(a.secrets,1) end
      else
        a.notes[#a.notes+1]=it.args.note
        while #a.notes>MAX_NOTES do table.remove(a.notes,1) end
      end
      accepted=accepted+1; result(npc,it,"done")
    elseif it.tool=="diplomacy" then
      local ok,why
      if it.args.player_index then
        ok,why=Diplomacy.npc_to_player(npc,game.get_player(it.args.player_index),it.args.action)
        if ok and it.args.action=="declare_war" then
          npc.foe={key="player:"..it.args.player_index,name=it.args.target,since=game.tick}
        end
      elseif it.args.action=="declare_war" then
        ok,why=Society.declare_war(npc,storage.npcs and storage.npcs[it.args.engineer])
      else
        ok,why=Society.apply(npc,{id=it.args.candidate})
      end
      if ok then accepted=accepted+1; result(npc,it,"done")
      else rejected[#rejected+1]={tool="diplomacy",reason=tostring(why)}; result(npc,it,"failed",tostring(why)) end
    elseif it.tool=="quiet" then
      npc.quiet=it.args.on or nil; accepted=accepted+1; result(npc,it,"done")
    elseif it.tool=="prioritize" then
      Priorities.set(npc,it.args,game.tick,it.reason); accepted=accepted+1; result(npc,it,"done")
    elseif it.tool=="research" then
      npc.research_choice=it.args.technology; accepted=accepted+1; result(npc,it,"done")
    elseif it.tool=="join" then
      local player=game.get_player(it.args.player_index)
      if it.args.join then
        npc.joined={player_index=it.args.player_index,name=it.args.to,since=game.tick}
        if player and Diplomacy.state(npc.force,player.force)~="allied" then
          Diplomacy.declare(npc,player.force,"ally")
        end
        game.print("["..npc.name.."] joins "..it.args.to.."'s team.")
      elseif npc.joined then
        npc.joined=nil
        if player and Diplomacy.state(npc.force,player.force)=="allied" then
          Diplomacy.declare(npc,player.force,"peace")
        end
        game.print("["..npc.name.."] leaves "..it.args.to.."'s team.")
      end
      accepted=accepted+1; result(npc,it,"done")
    elseif (function()   -- already planned: keep one, at the more urgent priority
        for i,other in ipairs(a.agenda) do
          if other.tool==it.tool and other.args.goal==it.args.goal and other.args.item==it.args.item
              and other.args.to==it.args.to and (other.step or 1)==1 then
            if PRIORITY[it.priority]<PRIORITY[other.priority] then table.remove(a.agenda,i); return false end
            return true
          end
        end
        return false
      end)() then
      rejected[#rejected+1]={tool=it.tool,reason="already_planned"}
    elseif #a.agenda<MAX_AGENDA then
      local at=#a.agenda+1
      for i,other in ipairs(a.agenda) do
        if PRIORITY[it.priority]<PRIORITY[other.priority] then at=i; break end
      end
      table.insert(a.agenda,at,it); accepted=accepted+1
      if Agent.recorder then Agent.recorder.task(npc,it,"planned") end
    else
      rejected[#rejected+1]={tool=it.tool,reason="agenda_full"}
    end
  end
  return {ok=true,request_id=payload.request_id,accepted=accepted,rejected=rejected}
end

-- An intention from outside the strategist's poll (a test map, or a direct order), checked like any.
-- Kept by priority among what it already means to do.
function Agent.order(npc,raw)
  local a=state(npc)
  local it,why=validate(npc,raw,"order-"..game.tick)
  if not it then return false,why end
  local at=#a.agenda+1
  for i,other in ipairs(a.agenda) do
    if PRIORITY[it.priority]<PRIORITY[other.priority] then at=i; break end
  end
  table.insert(a.agenda,at,it)
  return true
end

-- Makes or fetches what is to be handed over: a job, nil (waiting), true (ready) or false (none of it
-- can be had: locked recipes count what is there).
-- What it brings is bounded like a stockpile made by hand: past GATHER_HAND of its own work on it, or once a fetch
-- found nothing where it looked (source_empty, LIVE-7e-b: Elin stood 5 minutes in a give of 100 walls and 100
-- magazines), it brings what it has; with nothing at all, the gift is off.
Agent.GATHER_HAND=3600
local function gather(npc,items,it)
  local D=Agent.designed
  if it and ((it.fails or 0)>0 or (it.spent or 0)>=Agent.GATHER_HAND) then it.short=true end
  if it and it.short then
    local any=false
    for _,i in ipairs(items) do
      i.count=math.min(i.count,D.count(npc,i.name))
      any=any or i.count>0
    end
    return any
  end
  for _,i in ipairs(items) do
    if D.count(npc,i.name)<i.count then
      local job,why=D.acquire(npc,i.name,i.count,0)
      if job then return job end
      if why and (why:find("recipe_locked",1,true) or why:find("fluid_ingredient",1,true)) then
        i.count=D.count(npc,i.name)   -- bring what there is
      elseif why then return nil end
    end
  end
  for _,i in ipairs(items) do if i.count>0 then return true end end
  return false
end

-- Where a person is now (they move), or nil.
local function person_at(args)
  local player=args.player_index and game.get_player(args.player_index)
  if player and player.valid and player.character and player.character.valid then return player.position,player end
  local other=args.engineer and storage.npcs and storage.npcs[args.engineer]
  if other and other.character and other.character.valid then return other.character.position,nil,other end
end

local function chest_item(npc)
  local D=Agent.designed
  for _,name in ipairs(CHESTS) do if D.count(npc,name)>0 then return name end end
end

-- One step of an intention: a job (and meta) to do, "done", "failed" (with why), or nil to wait.
local function step(npc,it,tick)
  local D=Agent.designed
  local args=it.args
  if it.tool=="build" then
    if D.active(npc) and npc.design.purpose==args.goal then return "done" end   -- already on it
    if not D.active(npc) then
      if npc.design and npc.design.status~="complete" then
        log("[npc-factions] npc="..npc.id.." begins "..tostring(args.goal).." in place of "..tostring(npc.design.purpose)
          .." ("..tostring(npc.design.status)..")")
      end
      D.begin(npc,tick,"Build what you decided: "..args.goal..(args.why and (". "..args.why) or ""),args.goal)
      npc.design.ask=it.priority   -- (not "now": the planner weighs it with the engineer's own next goals)
      return "done"
    end
    if it.priority=="now" and not npc.paused_design then
      Agent.set_aside(npc,args.goal)
      D.begin(npc,tick,"Build what you decided: "..args.goal,args.goal)
      npc.design.ask="now"
      return "done"
    end
    return nil
  elseif it.tool=="stockpile" then
    if D.count(npc,args.item)>=args.count then return "done" end
    -- A reserve made by hand is bounded by the time it takes (Agent.STOCKPILE_HAND): its strategist asked for
    -- walls again and again, and a fifth of the engineer's time went to crafting them (run 20, 2026-10-25).
    if it.priority~="now" and (it.spent or 0)>=Agent.STOCKPILE_HAND
        and not (D.machine_made and D.machine_made(npc,args.item)) then
      return "done","hand_budget"
    end
    -- What its own machines make is taken as they make it, not made by hand, but in a hurry: a strategist's
    -- 200 walls were crafted by hand beside the wall row (the user, 2026-10-24: walls by machine, by hand
    -- only in an emergency).
    if it.priority~="now" and D.machine_made and D.machine_made(npc,args.item) then
      return D.collect(npc,args.item,args.count-D.count(npc,args.item))
    end
    local job,why=D.acquire(npc,args.item,args.count,0)
    if job then return job end
    if why and (why:find("recipe_locked",1,true) or why:find("fluid_ingredient",1,true)) then return "failed",why end
    return nil
  elseif it.tool=="guard" then
    if not npc.help_request then
      npc.help_request={kind="defend",player_index=args.player_index,tick=tick,position=args.place,
        until_tick=tick+args.minutes*3600}
    end
    if not D.active(npc) then
      D.begin(npc,tick,"Build a small gun fort here and keep it loaded.","ally-defense")
      return "done"
    end
    if it.priority=="now" and not npc.paused_design then
      npc.paused_design=npc.design; npc.design=nil
      D.begin(npc,tick,"Build a small gun fort here and keep it loaded.","ally-defense")
      return "done"
    end
    return nil
  elseif it.tool=="visit" then
    -- Go to them (where they are now: they may have moved), then stay close for a while.
    local target=person_at(args) or args.place
    local c=npc.character.position
    local far=(c.x-target.x)^2+(c.y-target.y)^2
    if it.arrived and tick-it.arrived>=args.minutes*3600 then return "done" end
    if far>(it.arrived and 400 or 64) then   -- on the way, or they walked off: follow
      local spot=npc.character.surface.find_non_colliding_position("character",target,10,1) or target
      return {kind="move",position={x=spot.x,y=spot.y},deadline=tick+54000},{step="visit"}
    end
    if not it.arrived then
      it.arrived=tick
      Agent.event(npc,{kind="arrived",at=args.to})   -- its strategist decides what to say or do there
      if args.minutes==0 then return "done" end
    end
    return "hold"
  elseif it.tool=="trade" then
    -- What it offers is in hand first (from its chests and machines, or made), then the offer stands
    -- for the player to accept; the swap itself happens in person when they do.
    local ready=gather(npc,args.give)
    if type(ready)=="table" then return ready end
    if ready==nil then return nil end
    local give=args.give[1]
    if not ready or give.count<1 then return "failed","nothing_to_offer" end
    local player=game.get_player(args.player_index)
    if not (player and player.valid) then return "failed","player_left" end
    local deal,why=PlayerTrade.offer(npc,player,{npc_gives=give.name,npc_count=give.count,
      player_gives=args.want.name,player_count=args.want.count},true)
    if not deal then return "failed",why end
    player.print("["..npc.name.."] "..give.count.." "..(give.name:gsub("-"," ")).." for "..args.want.count.." "
      ..(args.want.name:gsub("-"," "))..". To take it, say \""..npc.name..": accept "..deal.id.."\".")
    return "done","deal "..deal.id
  elseif it.tool=="build_at" then
    local order={item=args.item,rate=args.rate,player_index=args.player_index,at=args.place}
    local goal="Build a block making "..args.item.." at "..args.rate.." a second, at a player's base."
    if not D.active(npc) then D.begin(npc,tick,goal,"module",args.place,order); return "done" end
    if it.priority=="now" and not npc.paused_design then
      npc.paused_design=npc.design; npc.design=nil
      D.begin(npc,tick,goal,"module",args.place,order)
      return "done"
    end
    return nil
  elseif it.tool=="relocate" then
    -- Leave the base (it stays standing; upkeep no longer tends it), walk to the new place, start over.
    if not args.place then
      local c=npc.character.position
      local threat=BaseMemory.threats(npc,{x=c.x,y=c.y},400,1)[1]
      local ax,ay=1,0
      if threat then
        local dx,dy=c.x-threat.x,c.y-threat.y
        local d=math.max(1,math.sqrt(dx*dx+dy*dy)); ax,ay=dx/d,dy/d
      end
      local spot={x=npc.home.x+ax*args.distance,y=npc.home.y+ay*args.distance}
      args.place=npc.character.surface.find_non_colliding_position("character",spot,40,2) or spot
    end
    local c=npc.character.position
    if (c.x-args.place.x)^2+(c.y-args.place.y)^2>8*8 then
      return {kind="move",position={x=args.place.x,y=args.place.y},deadline=tick+72000},{step="relocate"}
    end
    D.relocate(npc,{x=math.floor(args.place.x),y=math.floor(args.place.y)},tick)
    Agent.event(npc,{kind="relocated",at={x=math.floor(args.place.x),y=math.floor(args.place.y)}})
    return "done"
  elseif it.tool=="arm_up" then
    -- What a player takes into a fight, made or fetched honestly, one thing at a time: the best armor
    -- and gun it can make, ammunition, capsules for worms, a few turrets to creep with. What it cannot
    -- get now is skipped; then it puts on the best of what it owns.
    it.skipped=it.skipped or {}
    for _=1,8 do   -- what it cannot get is skipped, and the next best (a pistol for a machine gun) tried
      local skipped=false
      for _,want in ipairs(arm_wants(npc,args,it.skipped)) do
        local name,n=want[1],want[2]
        if owned(npc,name)<n then
          local job=D.acquire(npc,name,n,0)
          if job then return job end
          it.skipped[name]=true
          skipped=true
          break
        end
      end
      if not skipped then break end
    end
    pcall(Combat.equip_now,npc)
    local skipped={}
    for name in pairs(it.skipped) do skipped[#skipped+1]=name end
    if #skipped>0 then it.note="could not get yet: "..table.concat(skipped,", ") end
    -- Heavy armor it can make but for steel, which only furnaces make: a steel smelter, unless it has one.
    if it.skipped["heavy-armor"] and Recipes.ready(npc.force,"heavy-armor")
        and Recipes.ready(npc.force,"steel-plate") then
      local built=false
      for _,purpose in ipairs(D.completed and D.completed(npc) or {}) do
        if purpose=="steel-smelting" then built=true end
      end
      if not built then
        pcall(Agent.order,npc,{tool="build",priority="next",reason="steel for heavy armor",
          args={goal="steel-smelting",why="steel for heavy armor"}})
      end
    end
    return "done"
  elseif it.tool=="attack" then
    -- Ammunition first (its own magazines, and two turrets when it can make them), then war if it is at
    -- peace with them, then the march; at the target its combat takes over against whatever defends
    -- it, for a while. With turrets in hand it creeps up on their buildings the way players do.
    if it.step==1 then
      -- Defending its own ground (a place near home): what it carries will do, no turrets; it goes now.
      local h=npc.home
      local at_home=not args.engineer and h and (args.place.x-h.x)^2+(args.place.y-h.y)^2<=HOME_GROUND^2
      local far=h and (args.place.x-h.x)^2+(args.place.y-h.y)^2>FAR_TRIP^2
      local need=at_home and 10 or math.max(far and 150 or 80,args.magazines or 0)
      if at_home then it.no_turrets=true end
      if far then
        -- A trip across biter country: the best armor and gun it can make first, as a player would.
        it.skipped=it.skipped or {}
        for _,want in ipairs(arm_wants(npc,{focus="armor"},it.skipped)) do
          if owned(npc,want[1])<want[2] then
            local job=D.acquire(npc,want[1],want[2],0)
            if job then return job end
            it.skipped[want[1]]=true
          end
        end
        for _,want in ipairs(arm_wants(npc,{focus="weapon"},it.skipped)) do
          if owned(npc,want[1])<want[2] then
            local job=D.acquire(npc,want[1],want[2],0)
            if job then return job end
            it.skipped[want[1]]=true
          end
        end
        pcall(Combat.equip_now,npc)
      end
      if not at_home then
        -- a vehicle of its own: repair packs to mend it on the way
        it.skipped=it.skipped or {}
        for _,want in ipairs(arm_wants(npc,{focus="vehicle"},it.skipped)) do
          if owned(npc,want[1])<want[2] then
            local job=D.acquire(npc,want[1],want[2],0)
            if job then return job end
            it.skipped[want[1]]=true
          end
        end
      end
      local makes_ammo=Recipes.ready(npc.force,"firearm-magazine")
      if makes_ammo and owned(npc,"firearm-magazine")+owned(npc,"piercing-rounds-magazine")<need then
        local job=D.acquire(npc,"firearm-magazine",need,0)
        if job then return job end
      end
      if far then
        -- and plates to make more on the road; without enough magazines it does not set off at all
        if makes_ammo and owned(npc,"iron-plate")<FAR_PLATES then
          local job=D.acquire(npc,"iron-plate",FAR_PLATES,0)
          if job then return job end
        end
        if magazines(npc)<FAR_MIN_AMMO then
          Agent.event(npc,{kind="attack_aborted",target=args.target,why="not enough ammunition for the trip"})
          return "failed","not_enough_ammo"
        end
      end
      -- shelling: the shells it needs first, from its chests or made (they stack one to a slot: the pockets hold few)
      local _,shell=Agent.artillery_kit(npc)
      local art_kit=args.method=="artillery" and Agent.artillery_kit(npc)
      if art_kit and owned(npc,art_kit.item)<1 and not it.no_artillery then   -- (one in its chests: fetched)
        local job=D.acquire(npc,art_kit.item,1,0)
        if job then return job end
        it.no_artillery=true
      end
      if args.method=="artillery" and shell and args.shells and not it.no_shells then
        -- (as many as its pockets take: they stack one to a slot; a craft under way is waited for, a failure is not)
        local main=npc.character.get_main_inventory()
        local want=math.min(args.shells,owned(npc,shell)+(main and main.count_empty_stacks() or 0))
        if owned(npc,shell)<want then
          local job,why=D.acquire(npc,shell,want,0)
          if job then return job end
          if type(why)=="string" and (why:find("^crafting") or why:find("^waiting")) then return nil end
          it.no_shells=true
        end
      end
      local turrets=args.turrets or 2
      local kit=Agent.creep_kit(npc)
      local tur=kit and kit.item or "gun-turret"
      if Recipes.ready(npc.force,tur) and owned(npc,tur)<turrets and not it.no_turrets then
        local job=D.acquire(npc,tur,turrets,0)
        if job then return job end
        it.no_turrets=true
      end
      if args.engineer then
        local other=storage.npcs and storage.npcs[args.engineer]
        if not (other and other.character and other.character.valid) then return "failed","target_gone" end
        if npc.force.get_cease_fire(other.force) or other.force.get_cease_fire(npc.force) then
          local ok,why=Society.declare_war(npc,other)
          if not ok then return "failed",why end
        end
      elseif args.player_index then
        local player=game.get_player(args.player_index)
        if not (player and player.valid) then return "failed","target_gone" end
        if Diplomacy.state(npc.force,player.force)~="war" then
          local ok,why=Diplomacy.npc_to_player(npc,player,"declare_war")
          if not ok then return "failed",why end
        end
        npc.foe={key="player:"..player.index,name=player.name,since=tick}
        if npc.joined and npc.joined.player_index==player.index then npc.joined=nil end   -- not on their team
      end
      it.step=2
    end
    if args.player_index then
      -- A player moves, and it knows where only while its side sees them (Intel): it goes where they
      -- are then, else where they were last seen, else roughly where they started; not finding them
      -- there, it raids what they built round it. Its combat takes over once they are in range.
      local player=game.get_player(args.player_index)
      local body=player and player.valid and player.character
      if not (body and body.valid) then return "done" end   -- down (or gone): the raid is over
      if body.surface~=npc.character.surface then return "failed","target_elsewhere" end
      if Intel.sees(npc,body.position,tick) then
        it.last_seen={x=body.position.x,y=body.position.y,tick=tick}
      end
      local seen=it.last_seen and tick-it.last_seen.tick<=SEEN_LATELY
      args.place=it.last_seen and {x=it.last_seen.x,y=it.last_seen.y} or args.spawn or args.place
      local c=npc.character.position
      if it.arrived and tick-it.arrived>=args.minutes*3600 then return "done" end
      local job,meta=campaign(npc,it,args,tick)
      if job then return job,meta end
      job=march(npc,it,args.place,14,60,tick)
      if job=="unsafe" or job=="blocked" then return no_safe_way(npc,args,job) end
      if job=="hold" then return "hold" end   -- healing on the way
      if job then
        job.deadline=tick+3600   -- they move: look again where they are
        return job,{step="attack"}
      end
      npc.combat_objective={x=args.place.x,y=args.place.y}
      npc.combat_mission_until=(it.arrived or tick)+args.minutes*3600
      if not it.arrived then
        it.arrived=tick
        Agent.event(npc,{kind="arrived",at=args.target,to="attack"})
      end
      if not seen then
        -- (their base: they are not about, as far as its side sees)
        local won=spoils(npc,it,args,player.force,nil,tick)
        if won=="done" then return "done" end
        if type(won)=="table" then return won,{step="attack"} end
        local job=raid(npc,args,tick)
        if job then return job,{step="attack"} end
      end
      return "hold"
    end
    local c=npc.character.position
    local shelling=args.target=="nests" and (it.artillery or args.method=="artillery" and Agent.artillery_kit(npc)~=nil)
    local creeping=not shelling and (it.creep or Agent.creep_kit(npc)~=nil   -- (or, the armory knowing none, gun turrets)
      or owned(npc,"gun-turret")>=2 and owned(npc,"firearm-magazine")+owned(npc,"piercing-rounds-magazine")>=20)
    if args.engineer and not it.creep then
      local job,meta=campaign(npc,it,args,tick)
      if job then return job,meta end
    elseif args.engineer then
      local waiting=wait_out(npc,it,args,tick)   -- creeping or raiding when biters come: back off a while
      if waiting then return waiting,{step="attack"} end
    end
    if not it.arrived then
      it.armed=it.armed or loadout(npc)   -- (what it set off with: its offense judgment weighs a retry by it)
      local art=shelling and Agent.artillery_kit(npc)
      local stop=art and math.max(60,art.range*Agent.ART_REACH) or creeping and 40 or 24
      local job=march(npc,it,args.place,stop,stop+60,tick)
      if job=="unsafe" or job=="blocked" then return no_safe_way(npc,args,job) end
      if job then return job,{step="attack"} end
      it.arrived=tick
      if args.target=="nests" then it.before=Agent.hostile_count(npc,args.place,Agent.NEST_REACH) end
      Agent.event(npc,{kind="arrived",at=args.target,to="attack"})
    end
    local over=tick-it.arrived>=args.minutes*3600
    -- There: what defends the place is its to fight, nests and turrets too.
    npc.combat_objective={x=args.place.x,y=args.place.y}
    npc.combat_mission_until=it.arrived+args.minutes*3600   -- its combat may hold the body all that time
    -- hurt (O-9): out of their reach until it has healed, nothing of the attack walks it back in meanwhile
    if args.target=="nests" and Combat.stepping_out(npc,npc.character,tick) then return "hold" end
    if shelling then
      local job,status=Agent.artillery(npc,it,args,tick,over)
      if job then return job,{step="attack"} end
      if status then return status end
    end
    if creeping then
      local job,status=creep(npc,it,args,tick,over)
      if job then return job,{step="attack"} end
      if status=="done" and Agent.tour_on(npc,it,args,tick) then return "hold" end   -- on to the next group
      if status=="hold" or (status and not args.engineer) then return status end
      -- an engineer's base with its turrets down: raided (below) until the time is up
    end
    if over then
      if args.engineer then
        npc.character.selected=nil
        npc.character.shooting_state={state=defines.shooting.not_shooting,position=npc.character.position}
        local h=npc.home
        -- The raid is over: back home. From far away by the safe route (homeward, see result); run 16
        -- had a plain walk home from 1500 tiles off stuck against a lake shore for 12 minutes.
        local d2=h and (c.x-h.x)^2+(c.y-h.y)^2 or 0
        if d2>30*30 and d2<=FAR_TRIP^2 then
          return {kind="move",position={x=h.x,y=h.y},deadline=tick+54000},{step="attack"}
        end
      end
      return "done"
    end
    if args.engineer then
      local other=storage.npcs[args.engineer]
      local won=other and other.force and other.force.valid and spoils(npc,it,args,other.force,other.character,tick)
      if won=="done" then return "done" end
      if type(won)=="table" then return won,{step="attack"} end
      local job=raid(npc,args,tick)
      if job then return job,{step="attack"} end
    end
    return "hold"   -- standing its ground there: its combat fights what comes in range
  elseif it.tool=="give" then
    if it.step==1 then
      local ready=gather(npc,args.items,it)
      if type(ready)=="table" then return ready end
      if ready==nil then return nil end
      if ready==false then return "failed","nothing_to_give" end
      it.step=2
    end
    local target,player,other=person_at(args)
    if not target then return "failed","recipient_gone" end
    local c=npc.character.position
    if (c.x-target.x)^2+(c.y-target.y)^2>36 then
      local spot=npc.character.surface.find_non_colliding_position("character",target,6,1) or target
      return {kind="move",position={x=spot.x,y=spot.y},deadline=tick+54000},{step="give"}
    end
    -- Hand it over: from its inventory into theirs (nothing is created).
    local body=player and player.character or other and other.character
    local theirs=body and body.get_main_inventory()
    local mine=npc.character.get_main_inventory()
    if not (theirs and mine) then return "failed","no_inventory" end
    local given={}
    for _,i in ipairs(args.items) do
      local n=math.min(i.count,mine.get_item_count({name=i.name,quality="normal"}))
      if n>0 then
        local moved=theirs.insert({name=i.name,count=n,quality="normal"})
        if moved>0 then mine.remove({name=i.name,count=moved,quality="normal"}); given[#given+1]=moved.." "..i.name end
      end
    end
    if #given==0 then return "failed","their_inventory_full" end
    return "done",table.concat(given,", ")
  elseif it.tool=="ask" then
    local player=game.get_player(args.player_index)
    if not (player and player.valid) then return "failed","player_left" end
    player.print("["..npc.name.."] "..args.question)
    if args.wants_place then
      storage.agent_questions=storage.agent_questions or {}
      storage.agent_questions[player.index]={npc_id=npc.id,question=args.question,tick=tick}
      player.print({"","[",npc.name,"] (Open the engineers window and use Mark location to show me.)"})
    end
    return "done"
  elseif it.tool=="deliver" then
    -- One team: handed over in person, as give does (a chest by their base would be building on ground
    -- that is not its own).
    if TeamWorld.active() then it.tool="give"; return nil end
    if it.step==1 then   -- gather
      local ready=gather(npc,args.items,it)
      if type(ready)=="table" then return ready end
      if ready==nil then return nil end
      if ready==false then return "failed","nothing_to_give" end
      if not chest_item(npc) then
        local job,why=D.acquire(npc,"wooden-chest",1,0)
        if job then return job end
        if why then return nil end
      end
      it.step=2
    end
    if it.step==2 then   -- walk over (to open ground by their base: its centre may be built over)
      local surface=npc.character.surface
      args.walk=args.walk or surface.find_non_colliding_position("character",args.place,40,1) or args.place
      local c=npc.character.position
      if (c.x-args.walk.x)^2+(c.y-args.walk.y)^2>64 then
        return {kind="move",position={x=args.walk.x,y=args.walk.y},deadline=tick+54000},{step="walk"}
      end
      it.step=3
    end
    if it.step==3 then   -- the chest, within reach
      local name=chest_item(npc)
      if not name then it.step=1; return nil end
      local spot=Hands.free_spot(npc,npc.character.surface,name,npc.character.position,6,npc.force)   -- (W-25)
      if not spot then return "failed","no_room_for_chest" end
      return {kind="build",entity_name=name,item=name,position=spot,direction=0},{step="chest"}
    end
    if it.step==4 then   -- fill it
      local chest=it.chest
      if not (chest and chest.valid) then it.step=3; return nil end
      for _,i in ipairs(args.items) do
        local have=D.count(npc,i.name)
        if have>0 and (i.given or 0)<i.count then
          return {kind="transfer",target=chest,item=i.name,count=math.min(have,i.count-(i.given or 0)),
            mode="deposit"},{step="fill",item=i.name}
        end
      end
      -- Hand it over: the chest becomes theirs, marked on their map, and they are told.
      local force=args.player_index and game.get_player(args.player_index) and game.get_player(args.player_index).force
        or (args.engineer and storage.npcs[args.engineer] and storage.npcs[args.engineer].force)
      local listing={}
      for _,i in ipairs(args.items) do
        if (i.given or 0)>0 then
          local nice=i.name:gsub("-"," ")
          listing[#listing+1]=i.given.." "..nice
        end
      end
      if force then
        pcall(function() chest.force=force end)
        pcall(function()
          force.add_chart_tag(chest.surface,{position=chest.position,text=npc.name..": supplies",
            icon={type="item",name=args.items[1].name}})
        end)
      end
      say(npc,args.to,"I left "..table.concat(listing,", ").." in a chest by your base ("
        ..math.floor(chest.position.x)..", "..math.floor(chest.position.y).."), marked on your map.")
      return "done"
    end
  end
  return "failed","unknown_step"
end

-- The next job from the agenda, or nil. "later" waits while a plan is being built.
-- Back from a far trip with nothing else it means to do: home by the safe route, resting when hurt and
-- making magazines when it runs out; routine upkeep does not walk it straight across instead.
-- Bounded (SO-5: blocked again and again, it retried every 10 s for as long as the run went): after
-- HOMEWARD_TRIES fresh plans with no way it gives the march up, says so in its log and tells its strategist
-- (event homeward_given_up), which decides what next. (Not by time: resting, a drive or a recall hold it up
-- rightly, and it may be home by then: Codex.)
local HOMEWARD_TRIES=6
local function homeward(npc,tick)
  local it,c,h=npc.homeward,npc.character,npc.home
  if not (it and c and c.valid and h) then npc.homeward=nil; return nil end
  local function give_up(why)
    npc.homeward,npc.marching=nil,nil
    local a=state(npc)
    a.log=a.log or {}
    a.log[#a.log+1]="homeward given up ("..why..") @"..tick
    while #a.log>8 do table.remove(a.log,1) end
    log("[npc-factions] npc="..tostring(npc.id).." gave up marching home from "..math.floor(c.position.x)..","
      ..math.floor(c.position.y)..": "..why)
    Agent.event(npc,{kind="homeward_given_up",why=why,tries=it.tries or 0,
      position={x=math.floor(c.position.x),y=math.floor(c.position.y)}})
    return nil
  end
  if tick<(it.retry_at or 0) then return false,"homeward" end
  local job=march(npc,it,h,30,nil,tick)
  if job=="hold" then return false,"homeward" end
  if type(job)=="table" then return job,{upkeep="agent",intention=it} end
  if job=="blocked" then   -- home it must go: a while later, by a fresh plan
    it.tries=(it.tries or 0)+1
    if it.tries>=HOMEWARD_TRIES then return give_up("no way home") end
    it.blocked_legs,it.route,it.retry_at=0,nil,tick+600
    return false,"homeward"
  end
  npc.homeward=nil
  return nil
end

-- Called home (begin_recall): its turrets out here picked up first when nothing hostile is near them,
-- then back by the safe route, magazines from home if it is short, and it stands by where its base
-- is hit (its combat fights what comes) until the attack is over.
local RECALL_FOR,PACK_REACH,PACK_CLEAR,RECALL_AMMO,LOST_WAIT=27000,40,24,20,36000
local ARM_WAIT=1800   -- what is being crafted for its kit it waits for this long before it walks back to the attack
local function foes_near(npc,p,radius)
  for _,e in ipairs(npc.character.surface.find_entities_filtered{type={"unit","character","car"},position=p,radius=radius}) do
    if e.valid and e.force~=npc.force and not npc.force.get_friend(e.force) and not npc.force.get_cease_fire(e.force) then
      return true
    end
  end
  return false
end
local function recall(npc,tick)
  local r,c=npc.recall,npc.character
  if not (c and c.valid) then npc.recall=nil; return nil end
  if tick-r.since>RECALL_FOR then npc.recall=nil; result(npc,r,"failed","timeout"); return nil end
  local meta={upkeep="agent",intention=r}
  local here=c.position
  while #r.pack>0 do
    local t=r.pack[#r.pack]
    if t.valid and (r.packing or 0)<8 and (t.position.x-here.x)^2+(t.position.y-here.y)^2<=PACK_REACH^2
        and not foes_near(npc,t.position,PACK_CLEAR) then
      r.packing=(r.packing or 0)+1
      return {kind="mine",target=t,count=1,mode="deconstruct"},meta
    end
    table.remove(r.pack)
  end
  if npc.alarm then r.place={x=npc.alarm.position.x,y=npc.alarm.position.y} end   -- where it is hit now
  if regrouping(npc,tick) then npc.marching=nil; return false,"regroup" end
  -- Beaten by the raider still at its base: no stronger now than when it lost, it does not walk back
  -- into them; it waits the raid out (10 minutes at most) and deals with what is left after.
  local lost=npc.lost_to
  if lost and tick-lost.tick<LOST_WAIT and npc.alarm and npc.alarm.by[lost.force] and not War.over(npc,tick) then
    npc.marching=nil
    return false,"waiting_out"
  end
  if tick<(r.retry_at or 0) then return false,"recall" end
  -- Armed first (defense_kit.lua: its best gun and armor, magazines, turrets to put down at its feet): it walks into
  -- the attack, and a pistol and a dozen magazines against a wave got an engineer killed (run 52, 2026-10-26).
  -- (what is being crafted for it it waits for, a little; a fetch that failed is not tried again at once)
  if not r.kitted and Agent.kit and Agent.designed then
    local job,how=Agent.kit.next(npc,tick,Agent.designed.acquire,true)
    if type(job)=="table" then how=type(how)=="table" and how or {upkeep="kit"}; how.intention=r; return job,how end
    r.arming_since=r.arming_since or tick
    if how=="crafting" and tick-r.arming_since<ARM_WAIT then return false,"arming" end
    r.kitted=true
  end
  -- A wave there it would lose to (combat.lua keeps it clear of them): it stands off out of their sight while its walls
  -- and turrets take them on, and goes in once the odds turn or they have gone. It cannot outrun biters, and run 34's
  -- engineer died walking into a wave of 60 to build turrets (O-2).
  -- Out of their sight it does not stand idle either: the rest of its work goes on away from them (combat.lua keeps
  -- it out of their reach) until the odds turn or they have gone (O-4: it stood off 14 minutes of 30, crafting nothing
  -- of the gun and turrets that would have let it fight, while a group it could not beat idled by its base).
  if Combat.keeping_clear and Combat.keeping_clear(npc,tick) then npc.marching=nil; npc.standing_off=tick; return nil end
  local job=march(npc,r,r.place,24,nil,tick)
  if job=="hold" then return false,"recall" end
  if type(job)=="table" then return job,meta end
  if job=="blocked" then r.blocked_legs,r.route,r.retry_at=0,nil,tick+600; return false,"recall" end
  if War.over(npc,tick) then
    npc.recall=nil
    result(npc,r,"done")
    return nil
  end
  if not r.armed and magazines(npc)<RECALL_AMMO and Agent.designed then
    r.armed=true   -- once: what home has, or what it can make
    local get=Agent.designed.acquire(npc,"firearm-magazine",2*RECALL_AMMO,0)
    if get then return get,meta end
  end
  return false,"defend_home"
end

-- Helping a friend (begin_help): magazines and turrets to give first, then there by the safe route,
-- standing by where they are hit until the attack is over, then their turrets (loaded, theirs), home.
local HELP_FOR,HELP_AMMO,GIFTS,KEEP_FOR_HOME=36000,50,2,30
local function help(npc,tick)
  local h,c=npc.helping,npc.character
  local ally=War.ally(h.ally)   -- an engineer, or a player in the team world
  local meta={upkeep="agent",intention=h}
  local function finish(status,why)
    npc.helping=nil
    result(npc,h,status,why)
    local home=npc.home
    if c and c.valid and home and (c.position.x-home.x)^2+(c.position.y-home.y)^2>60*60
        and Agent.off_own_ground(npc,c.position) then
      npc.homeward={tool="homeward",args={},created=tick,priority="next"}
    end
    return nil
  end
  if not (c and c.valid) then npc.helping=nil; return nil end
  if not (ally and (ally.player_index or ally.character and ally.character.valid)) then
    return finish("failed","ally_gone")
  end
  if tick-h.since>HELP_FOR then return finish("failed","timeout") end
  if h.step=="arm" then
    h.tries=(h.tries or 0)+1
    if h.tries<=6 and Agent.designed then
      if magazines(npc)<HELP_AMMO then
        local get=Agent.designed.acquire(npc,"firearm-magazine",HELP_AMMO,0)
        if get then return get,meta end
      end
      if Recipes.ready(npc.force,"gun-turret") and owned(npc,"gun-turret")<GIFTS then
        local get=Agent.designed.acquire(npc,"gun-turret",GIFTS,0)
        if get then return get,meta end
      end
    end
    h.step="go"
  end
  if ally.alarm and not War.over(ally,tick) then h.place={x=ally.alarm.position.x,y=ally.alarm.position.y} end
  -- (on the way, the attack over or the plot holding by itself: no longer wanted, it turns back; D-12)
  if h.step=="go" and TeamWorld.active() and War.help_wanted and not War.help_wanted(ally,tick) then
    return finish("done","no_longer_needed")
  end
  if regrouping(npc,tick) then npc.marching=nil; return false,"regroup" end
  -- (a wave there it would lose to: it waits out of their sight, as on a call home; O-2)
  if Combat.keeping_clear and Combat.keeping_clear(npc,tick) then npc.marching=nil; npc.standing_off=tick; return nil end
  local job=march(npc,h,h.place,24,nil,tick)
  if job=="unsafe" or job=="blocked" then return finish("failed",job=="blocked" and "no_way" or "no_safe_way") end
  if job=="hold" then return false,"help" end
  if type(job)=="table" then return job,meta end
  h.route=nil
  if not War.over(ally,tick) then return false,"helping" end   -- its combat fights what comes
  -- Over: turrets of its own for them where they were hit, loaded and handed over. (Not in the team
  -- world: that is a teammate's plot, where it never builds; its turrets go on its own wall line.)
  if (h.placing or 0)<GIFTS and owned(npc,"gun-turret")>0 and magazines(npc)>=KEEP_FOR_HOME+10
      and not TeamWorld.active() then
    local spot=Hands.free_spot(npc,c.surface,"gun-turret",h.place,8,npc.force)   -- (W-25)
    if spot then
      h.placing=(h.placing or 0)+1
      return {kind="build",entity_name="gun-turret",item="gun-turret",position={x=spot.x,y=spot.y},direction=0},
        {upkeep="agent",intention=h,step="gift"}
    end
  end
  return finish("done")
end
local function gift(npc,h,t)
  local ally=War.ally(h.ally)
  local main=npc.character.get_main_inventory()
  for _,name in ipairs({"piercing-rounds-magazine","firearm-magazine"}) do
    local n=math.min(10,main and main.get_item_count(name) or 0)
    if n>0 then
      local put=t.insert{name=name,count=n}
      if put>0 then main.remove{name=name,count=put} end
      break
    end
  end
  if ally and ally.force and ally.force.valid then pcall(function() t.force=ally.force end) end
  h.given=(h.given or 0)+1
end

-- "Now" lasts while its reason does, not until what it set going is all done (ST-2, rehearsal 7g: a build(now) of
-- starter turrets, ordered when biters reached the plot, waited behind a reinforce plan already going up, the
-- fight won and the turrets standing, and held the engineer's rebuilds and its whole agenda 10 minutes):
-- - one ordered during an attack on its home is no more than "next" once that attack is over (War.over), or
--   another has taken its place;
-- - anything "now" that only waited (nothing it could do) for NOW_WAIT is "next": it no longer holds the rest.
-- Either way it is still wanted: it is carried out in its turn, or lapses as any intention does.
-- - an action that takes it away from a plan being built (a visit, a gift or delivery, a stockpile: AWAY) with
--   nothing urgent there is "later", for when the plan is done (LIVE-7c-b, normal-7c: Dax answered radio chatter
--   with visit(now) "I'll cover while your iron ramps up" three times in five minutes, nobody under attack, and
--   its science build stood still until the stall watchdog fired; LIVE-7e-b, normal-7e: Elin's deliver(now) of
--   100 walls and 100 magazines, offered on the radio, did the same). Urgent: a fight where it goes, the attack
--   on its home it was decided in still on, or a player who asked it directly.
Agent.NOW_WAIT=7200
Agent.AWAY={visit=true,give=true,deliver=true,stockpile=true}
Agent.AWAY_FIGHT_RADIUS=48   -- biters this close to where it goes: a fight there
Agent.AWAY_LOOK=60           -- ticks between looks (a fight seen counts for a minute: no flicker)
function Agent.away_needed(npc,it,tick)
  local args=it.args
  if args.asked then return true end   -- the player it is for asked for it: theirs to call
  if it.answers and npc.alarm and npc.alarm.first==it.answers and not War.over(npc,tick) then return true end
  if tick<(it.fight_until or 0) then return true end
  if tick<(it.next_look or 0) then return false end
  it.next_look=tick+Agent.AWAY_LOOK
  local other=args.engineer and storage.npcs and storage.npcs[args.engineer]
  local fight=(other and other.alarm and not War.over(other,tick))
    or (args.to=="home" and npc.alarm and not War.over(npc,tick))
  if not fight then
    local at=person_at(args) or args.place
    local c=npc.character
    fight=at~=nil and c~=nil and c.valid and c.surface.count_entities_filtered{force="enemy",type="unit",
      position=at,radius=Agent.AWAY_FIGHT_RADIUS,limit=1}>0
  end
  if fight then it.fight_until=tick+3600 end
  return fight
end
function Agent.settle(npc,tick)
  local a=npc.agent
  if not a then return end
  local D=Agent.designed
  local building=D and D.active and D.active(npc)
  local calmed,parked={},{}
  local i=1
  while i<=#a.agenda do
    local it=a.agenda[i]
    local over=it.answers and (not npc.alarm or npc.alarm.first~=it.answers or War.over(npc,tick))
    local stayed=it.tool=="visit" and it.arrived and tick-it.arrived>=it.args.minutes*3600   -- (Agent.next ends it)
    if it.parked_from and not Agent.away_needed(npc,it,tick) then
      i=i+1
    elseif it.parked_from then   -- a fight there now: back to what it was asked as
      it.priority,it.parked_from=it.parked_from,nil
      table.remove(a.agenda,i); calmed[#calmed+1]=it
    elseif building and Agent.AWAY[it.tool] and it.priority~="later" and not stayed
        and not Agent.away_needed(npc,it,tick) then
      -- (what it did so far is kept: a visit's stay, what it gathered; after the plan it goes on from there)
      it.parked_from,it.priority,it.now_waits,it.settled=it.priority,("later"),nil,tick
      table.remove(a.agenda,i); parked[#parked+1]=it
      if Agent.recorder then Agent.recorder.task(npc,it,"later","nothing_urgent") end
    elseif it.priority=="now" and (over or (it.now_waits and tick-it.now_waits>Agent.NOW_WAIT)) then
      it.priority,it.now_waits,it.settled=("next"),nil,tick
      table.remove(a.agenda,i); calmed[#calmed+1]=it
    else
      i=i+1
    end
  end
  for _,it in ipairs(parked) do a.agenda[#a.agenda+1]=it end
  for _,it in ipairs(calmed) do   -- behind what is as urgent, before what is less
    local at=#a.agenda+1
    for j,other in ipairs(a.agenda) do
      if PRIORITY[other.priority]>PRIORITY[it.priority] then at=j; break end
    end
    table.insert(a.agenda,at,it)
  end
end

-- What may not wait for a plan's share of its time: called back, helping, coming home from far, a fight, or
-- something its strategist wants done now.
function Agent.urgent(npc)
  if npc.recall or npc.helping or npc.homeward or npc.combat_objective then return true end
  Agent.settle(npc,game.tick)
  for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do
    if it.priority=="now" then return true end
  end
  return false
end

function Agent.next(npc,tick)
  local a=npc.agent
  -- Called home or helping and standing off out of the biters' sight (it would lose there): what it means to do
  -- meanwhile goes on, arming up first, but no trip away (O-4: the rules' arm_up waited behind the recall).
  local standing
  if npc.recall then
    local job,meta=recall(npc,tick)
    if job~=nil or npc.standing_off~=tick then return job,meta end
    standing=true
  end
  if npc.helping and not standing then
    local job,meta=help(npc,tick)
    if npc.standing_off==tick and job==nil then standing=true
    elseif job~=nil or not npc.homeward then return job,meta end
  end
  if standing and not (a and #a.agenda>0 and Agent.designed) then return nil end
  -- Back from a far trip, home comes first: what it means to do next (the next raid's arming up
  -- included) is done from there. Run 26: arming for the next raid where the last one ended sent Ada
  -- off for materials into a spitter nest.
  if npc.homeward and not standing then return homeward(npc,tick) end
  if not (a and #a.agenda>0) or not Agent.designed then return nil end
  Agent.settle(npc,tick)
  local building=Agent.designed.active(npc)
  -- Turrets going up against an attack come before anything it means to do that is not urgent.
  local defending=Agent.designed.defending and Agent.designed.defending(npc)
  local i=1
  while i<=#a.agenda do
    local it=a.agenda[i]
    -- Given up when not done in time (trips longer), counted from when it set about it, not from when it
    -- was decided (an attack waiting behind another raid); waiting that long untouched, it lapses too.
    local limit=TRIPS[it.tool] and 2*DEADLINE or DEADLINE
    if not it.arrived and (tick-(it.started or tick)>limit or tick-it.created>limit+2*DEADLINE) then
      table.remove(a.agenda,i); result(npc,it,"failed","timeout")
    elseif standing and STANDING_SKIP[it.tool] then
      i=i+1
    elseif not ((it.priority=="later" and building) or (it.priority~="now" and defending)) then
      it.started=it.started or tick
      local job,meta=step(npc,it,tick)
      if job=="hold" then return false,it.tool end   -- staying put (visiting): nothing else for now
      if job=="done" or job=="failed" then
        table.remove(a.agenda,i); result(npc,it,job,meta)
        -- A trip just over far from home: home first, in this same round (drive16: this round went to
        -- upkeep, a walk of 2200 tiles straight home to empty a furnace, the car left by the raid;
        -- the next raid's walk home that way went past a nest, and Ada died).
        if npc.homeward then return homeward(npc,tick) end
        return nil
      end
      if type(job)=="table" then
        it.now_waits=nil
        meta=meta or {}
        meta.upkeep,meta.intention=("agent"),it
        return job,meta
      end
      if it.priority=="now" then   -- waits, and so does everything after it (for NOW_WAIT at most: Agent.settle)
        it.now_waits=it.now_waits or tick
        return nil
      end
      i=i+1
    else
      i=i+1
    end
  end
  return nil
end

local MAX_BLOCKED_LEGS=6
Agent.STOCKPILE_HAND=7200   -- ticks of its own work one stockpile of what its machines do not make may take
function Agent.on_receipt(npc,meta,receipt,tick)
  local it=meta.intention
  if not it then return end
  if type(receipt.tick)=="number" and type(receipt.started_tick)=="number" and receipt.tick>receipt.started_tick then
    it.spent=(it.spent or 0)+(receipt.tick-receipt.started_tick)   -- (its time on it: a stockpile's hand budget)
  end
  if receipt.reason=="combat_interrupt" then return end   -- a fight on the way: it carries on after
  -- A leg of a march it could not walk (no path: a lake, cliffs): that spot is remembered as blocked
  -- and the next route goes round it; only after several such does the march give up.
  local r=it.route
  if receipt.status~="complete" and r and r.leg and (it.blocked_legs or 0)<MAX_BLOCKED_LEGS then
    it.blocked_legs=(it.blocked_legs or 0)+1
    Routes.block(npc,r.leg,tick)
    it.route=nil
    return
  end
  if receipt.status~="complete" then
    if receipt.reason~="in_danger" then it.fails=(it.fails or 0)+1 end   -- (in danger: a wait, W-30)
    if (it.fails or 0)>=3 then
      local a=state(npc)
      for i,other in ipairs(a.agenda) do if other==it then table.remove(a.agenda,i); break end end
      result(npc,it,"failed",receipt.reason)
    end
    return
  end
  it.fails=0   -- only failures in a row give an intention up
  if meta.step=="gift" and receipt.entity and receipt.entity.valid then gift(npc,it,receipt.entity) end
  if meta.step=="chest" and receipt.entity and receipt.entity.valid then it.chest=receipt.entity; it.step=4 end
  if meta.step=="fill" then
    for _,i in ipairs(it.args.items) do
      if i.name==meta.item then i.given=(i.given or 0)+(receipt.count or 0) end
    end
  end
end

-- The design under way set aside for one asked for now (resumed when that is done), and said so.
function Agent.set_aside(npc,for_what)
  local d=npc.design
  if d then
    log("[npc-factions] npc="..npc.id.." sets aside "..tostring(d.purpose).." ("..tostring(d.status)..") for "
      ..tostring(for_what))
  end
  npc.paused_design=d; npc.design=nil
end

-- A plan finished: a paused one picks up where it stopped.
function Agent.plan_finished(npc,purpose,status,reason)
  Agent.event(npc,{kind=status=="complete" and "plan_done" or "plan_failed",purpose=purpose,reason=reason})
  if npc.paused_design and (not npc.design or npc.design.status=="complete" or npc.design.status=="blocked"
      or npc.design.status=="deferred") then
    log("[npc-factions] npc="..npc.id.." resumes "..tostring(npc.paused_design.purpose).." after "..tostring(purpose)
      .." ("..tostring(status)..")")
    npc.design=npc.paused_design; npc.paused_design=nil
    -- (a request set aside unanswered: asked afresh now, its wait timed from here; Codex, W-27)
    if npc.design.status=="waiting" then npc.design.requested_tick,npc.design.export_cache=game.tick,nil end
  end
end

-- Mark location: the player's answer to a question that wanted a place (or a mark for later).
function Agent.location_marked(player,position)
  storage.player_marks=storage.player_marks or {}
  storage.player_marks[player.index]={x=math.floor(position.x),y=math.floor(position.y)}
  pcall(function()
    player.force.add_chart_tag(player.surface,{position=position,text="Marked",icon={type="virtual",name="signal-dot"}})
  end)
  local q=storage.agent_questions and storage.agent_questions[player.index]
  if q then
    storage.agent_questions[player.index]=nil
    local npc=storage.npcs and storage.npcs[q.npc_id]
    if npc then
      Agent.event(npc,{kind="answer",from=player.name,question=q.question,
        place={x=math.floor(position.x),y=math.floor(position.y)}})
      player.print({"","[",npc.name,"] Got it."})
      return npc
    end
  end
  player.print("Location marked. Engineers you talk to can use it.")
end

Character.thinks=function(npc) return Agent.thinking(npc) end

-- An engineer at war with everyone from the start (a test opponent): aggressive, vengeful and brave,
-- silent in chat, and out to take out the players and the other engineers, military first.
local WARLORD="Take out the players and every other engineer: you are at war with all of them from the "
  .."start. Build only what feeds the fight (iron, copper, ammunition, turrets, armor, guns, walls), "
  .."research military first, arm up, and attack them one by one, the weakest first. Never talk."
function Agent.warlord(npc)
  local seed=Personality.ensure and Personality.ensure(npc).seed or 1
  local ok,why=Personality.assign(npc,{"aggressive","evil","vengeful","brave"},seed)
  if not ok then return false,why end
  npc.quiet,npc.silent,npc.mission,npc.warlord=true,true,WARLORD,true
  npc.foe={key="everyone",name="everyone",since=game.tick}
  local players=storage.founding_force or game.forces.player
  if players and Diplomacy.state(npc.force,players)~="war" then Diplomacy.declare(npc,players,"war") end
  for _,other in pairs(storage.npcs or {}) do
    if other~=npc and Diplomacy.state(npc.force,other.force)~="war" then Society.declare_war(npc,other) end
  end
  return true
end

-- Every hit on an engineer's body or buildings by a player or another engineer: it keeps count, and
-- once it has had enough it is at war with them (its turrets fire on them from then on, theirs on
-- it) and taking them out becomes its aim: magazines and turrets first, then after them.
Character.damage_listener=function(victim,attacker,amount,entity,destroyed)
  local id=tonumber(tostring(victim):match("^npc:(%d+)$"))
  local npc=id and storage.npcs and storage.npcs[id]
  if not (npc and npc.character and npc.character.valid) then return end
  local index=tonumber(tostring(attacker):match("^player:(%d+)$"))
  local player=index and game.get_player(index)
  local other_id=tonumber(tostring(attacker):match("^npc:(%d+)$"))
  local other=other_id and storage.npcs[other_id]
  local force,name
  if player then force,name=player.force,player.name
  elseif other then force,name=other.force,other.name
  else return end
  if not (force and force.valid) or force==npc.force then return end
  if TeamWorld.active() then return end   -- a teammate's stray shot or car is no reason for war
  local relation=Diplomacy.state(npc.force,force)
  local axes=Personality.axes and Personality.axes(npc) or {}
  local fed_up=Aggro.hit(npc,attacker,amount,game.tick,{personal=entity==npc.character,destroyed=destroyed,
    aggression=axes.aggression,ally=relation=="allied",at_war=relation=="war"})
  if not fed_up then return end
  local ok
  if player then ok=Diplomacy.declare(npc,force,"war") else ok=Society.declare_war(npc,other) end
  if not ok then return end
  npc.foe={key=attacker,name=name,since=game.tick}
  game.print("[NPC] "..npc.name.." is now at war with "..name..(destroyed and " (they destroyed a "
    ..tostring(destroyed):gsub("-"," ")..")." or " (attacked)."))
  Agent.event(npc,{kind="war",with=name,why=destroyed and ("destroyed my "..tostring(destroyed)) or "attacked me"})
  for _,it in ipairs(state(npc).agenda) do if it.tool=="arm_up" then return end end
  pcall(Agent.order,npc,{tool="arm_up",priority="now",reason="at war with "..name,args={focus="all",ammo=100}})
end

-- Attacks the game reports (rate-limited there): the engineer that was hit notices, and when a
-- player is hit, the engineers allied with them notice too.
Character.attack_listener=function(entry,payload)
  local victim,attacker=entry.victim,entry.attacker
  local by=Character.name_of(attacker)
  local id=tonumber(tostring(victim):match("^npc:(%d+)$"))
  if id then
    local npc=storage.npcs and storage.npcs[id]
    if npc then
      local fight=npc.combat or {}
      -- Hit by an engineer or a player (not biters): where it happened is a threat to defend against,
      -- for a while, even with no building of theirs standing there.
      if tostring(entry.attacker):match("^npc:") or tostring(entry.attacker):match("^player:") then
        npc.raids=npc.raids or {}
        table.insert(npc.raids,{x=entry.position.x,y=entry.position.y,tick=game.tick,by=by})
        while #npc.raids>8 do table.remove(npc.raids,1) end
      end
      Agent.event(npc,{kind="attacked",by=by,position=entry.position,damage=payload.damage,destroyed=payload.destroyed,
        enemies=fight.threat_count,strength=fight.threat_strength and math.floor(fight.threat_strength*10)/10})
    end
    return
  end
  local index=tonumber(tostring(victim):match("^player:(%d+)$"))
  local player=index and game.get_player(index)
  if player then
    for _,npc in pairs(storage.npcs or {}) do
      if npc.force.get_friend(player.force) then
        Agent.event(npc,{kind="ally_attacked",ally=player.name,by=by,position=entry.position,
          destroyed=payload.destroyed})
      end
    end
  end
end

-- The team world: the biters broke through its wall line. It drops what it is building for a repair
-- (one more wall layer and turrets there), at once, unless it is building defenses already; and its
-- strategist hears of it (once a minute at most).
local BREACH_ORDER_EVERY,BREACH_NEWS_EVERY=3600,3600
function Agent.breach(npc,b)
  local tick=game.tick
  if tick>=(npc.breach_news or 0) then
    npc.breach_news=tick+BREACH_NEWS_EVERY
    Agent.event(npc,{kind="breach",edge=b.edge,lost=b.lost,layers=b.layers,
      position={x=math.floor(b.x),y=math.floor(b.y)}})
  end
  local D=Agent.designed
  if not D or tick<(npc.breach_ordered or 0) then return end
  if D.defending and D.defending(npc) then return end
  if npc.design and npc.design.purpose=="breach-repair" and npc.design.status=="waiting" then return end
  for _,it in ipairs(state(npc).agenda) do
    if it.tool=="build" and it.args.goal=="breach-repair" then return end
  end
  npc.breach_ordered=tick+BREACH_ORDER_EVERY
  pcall(Agent.order,npc,{tool="build",priority="now",reason="biters broke through on the "..tostring(b.edge).." wall",
    args={goal="breach-repair",why="the wall line was broken there; "..tostring(b.layers).." layers stand"}})
end
TeamWar.on_breach=function(npc,b) Agent.breach(npc,b) end

-- Whether a finished tech lets this engineer go on: what it builds waits for an item the tech unlocks.
local function unblocks(npc,tech)
  local waiting=type(npc.phase)=="string" and npc.phase:match("recipe_locked:([%w%-_]+)")
  if not waiting then return false end
  for _,effect in ipairs(tech.prototype.effects or {}) do
    if effect.type=="unlock-recipe" then
      if effect.recipe==waiting then return true end
      local recipe=prototypes.recipe[effect.recipe]
      for _,p in ipairs(recipe and recipe.products or {}) do if p.name==waiting then return true end end
    end
  end
  return false
end

function Agent.on_research_finished(event)
  local tech=event.research
  if not (tech and tech.valid) then return end
  -- The team world has one tech tree, led by the players' queue, and the engineers' picks go in through the
  -- team's research: a finished tech wakes only an engineer it unblocks (all seven thinking "research next"
  -- at once was three quarters of their AI calls, rehearsal 2026-10-23).
  local team=TeamWorld.active()
  for _,npc in pairs(storage.npcs or {}) do
    if (not team and npc.force==tech.force) or (team and unblocks(npc,tech)) then
      Agent.event(npc,{kind="research_done",technology=tech.name})
    end
  end
end

function Agent.register_commands()
  commands.add_command("npc-agent","Strategist bridge (companion only)",function(command)
    if command.player_index then return end   -- server/RCON only
    local p=command.parameter or "observe"
    local out
    if p=="observe" then out=Agent.observe()
    elseif p:sub(1,7)=="submit " then
      local ok,payload=pcall(helpers.json_to_table,p:sub(8))
      out=ok and Agent.submit(payload) or {ok=false,reason="json"}
    elseif p=="status" then   -- what each engineer intends and how far it got
      local npcs={}
      for _,npc in pairs(storage.npcs or {}) do
        local a=npc.agent or {agenda={},notes={}}
        local agenda={}
        for _,it in ipairs(a.agenda or {}) do
          agenda[#agenda+1]={tool=it.tool,priority=it.priority,step=it.step,fails=it.fails,age=game.tick-it.created,
            to=it.args.to,goal=it.args.goal,item=it.args.item}
        end
        npcs[#npcs+1]={name=npc.name,phase=npc.phase,agenda=agenda,notes=a.notes,pending=a.pending and a.pending.reason,
          paused=npc.paused_design and npc.paused_design.purpose,design=npc.design and npc.design.purpose,
          log=a.log}
      end
      out={ok=true,tick=game.tick,connected=Agent.connected(),npcs=npcs}
    elseif p:sub(1,6)=="event " then   -- tests: something happens to an engineer ({npc=name or id, event={...}})
      local ok,v=pcall(helpers.json_to_table,p:sub(7))
      local npc
      for _,c in pairs(ok and type(v)=="table" and storage.npcs or {}) do
        if c.id==v.npc or c.name==v.npc then npc=c end
      end
      out=npc and type(v.event)=="table" and type(v.event.kind)=="string"
        and {ok=Agent.event(npc,v.event)} or {ok=false,reason="npc_or_event"}
    else out={ok=false,reason="unknown_command"} end
    rcon.print("NPC_AGENT_RESULT "..helpers.table_to_json(out))
  end)
end

return Agent
