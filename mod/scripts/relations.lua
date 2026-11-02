-- Event direction: actor performs the action, target receives it (including
-- failed deliveries/trades and broken treaties/promises). Only live NPCs hold state.
local Chronicle = require("scripts.chronicle")
local Personality = require("scripts.personality")
local Relations = {}
local MINUTE, PAIR_CAP, EPISODE_CAP, TOPIC_CAP = 3600, 128, 64, 16
local FIELDS={"trust","affinity","fear","grudge","debt"}
local REPUTATION_FIELDS={"trust","affinity","fear","grudge"}
local COUNTERS={"helped","harmed","promises_kept","promises_broken","trades","gifts"}
local LIMITS={trust={-100,100},affinity={-100,100},fear={0,100},grudge={0,100},debt={-1000,1000}}
local function clamp(x,lo,hi) return math.max(lo,math.min(hi,x)) end
local function finite(x) return type(x)=="number" and x==x and math.abs(x)<math.huge end
local function valid_key(key)
  return type(key)=="string" and #key<=256 and
    (key:match("^npc:[1-9]%d*$") or key:match("^player:[1-9]%d*$") or key:match("^force:.+$"))
end
local function keys(map)
  local result={}
  for key in pairs(map) do result[#result+1]=key end
  table.sort(result)
  return result
end
local function npc_for(id) return storage.npcs and storage.npcs[id] end
local function id_for(npc) return type(npc)=="table" and npc.id or npc end
local function axes(npc) return npc and Personality.axes(npc) or {} end
local function axis(a,k) return finite(a[k]) and clamp(a[k],0,1) or 0.5 end
local function baseline(npc)
  local selected=npc and Personality.ensure(npc).selected or {}
  if selected.trusting or selected.cooperative or selected.diplomatic then return 10 end
  if selected.suspicious or selected.abrasive or selected.independent then return -10 end
  return 0
end
function Relations.ensure()
  Chronicle.ensure()
  storage.relations=storage.relations or {}
  storage.memory=storage.memory or {}
  storage.mood=storage.mood or {}
  storage.refusals=storage.refusals or {}
end
-- Fixed-size maps evict the least recently written entry; lexical key breaks ties.
-- Reads never change timestamps. Refusal pair and topic timestamps use the same rule.
local function reserve(map,key,stamp,cap)
  if map[key] then return end
  local ordered=keys(map)
  if #ordered<cap then return end
  local oldest=ordered[1]
  for _,k in ipairs(ordered) do if (map[k][stamp] or 0)<(map[oldest][stamp] or 0) then oldest=k end end
  map[oldest]=nil
end
local function decay(value,base,elapsed,half_life)
  return base+(value-base)*2^(-elapsed/half_life)
end
function Relations.get(npc,counterpart,tick)
  Relations.ensure()
  local id=id_for(npc)
  npc=type(npc)=="table" and npc or npc_for(id)
  local map=storage.relations[id]
  local saved=map and map[counterpart]
  tick=tick or 0
  local t=saved and saved.t or tick
  local elapsed=math.max(0,tick-t)
  local a=axes(npc)
  return {
    -- Trust and affinity half-life: 120 game minutes; fear: 20 minutes.
    trust=decay(saved and saved.trust or baseline(npc),baseline(npc),elapsed,120*MINUTE),
    affinity=decay(saved and saved.affinity or 0,0,elapsed,120*MINUTE),
    fear=decay(saved and saved.fear or 0,0,elapsed,20*MINUTE),
    grudge=decay(saved and saved.grudge or 0,0,elapsed,(30+210*axis(a,"grudge"))*MINUTE),
    debt=saved and saved.debt or 0,t=math.max(t,tick)}
end
-- Pure modulation, used by event rules; apply() itself accepts literal deltas.
-- Cooperative NPCs gain trust more readily and lose it less readily. Grudge/fear
-- gains depend on grudge/courage; losses and multiplicative scales stay literal.
function Relations.modulate(a,deltas)
  a=a or {}
  local out={}
  for _,field in ipairs(FIELDS) do
    local d=deltas[field]
    if finite(d) then
      if field=="trust" then
        -- Algebraically 0.6+0.8*c / 1.4-0.8*c; centered form makes c=0.5 exactly neutral.
        d=d*(d>=0 and (1+0.8*(axis(a,"cooperation")-0.5)) or (1+0.8*(0.5-axis(a,"cooperation"))))
      elseif field=="grudge" and d>0 then d=d*(0.5+axis(a,"grudge"))
      elseif field=="fear" and d>0 then d=d*(1.5-axis(a,"courage")) end
      out[field]=d
    end
  end
  if deltas.scale then
    out.scale={}
    for _,field in ipairs(FIELDS) do
      if finite(deltas.scale[field]) then out.scale[field]=deltas.scale[field] end
    end
  end
  return out
end
function Relations.apply(npc_id,counterpart,deltas,tick)
  if type(deltas)~="table" then return nil,"invalid_deltas" end
  Relations.ensure()
  if not npc_for(npc_id) or not valid_key(counterpart) then return nil,"missing_npc_or_counterpart" end
  if tick~=nil and (not finite(tick) or tick<0 or tick%1~=0) then return nil,"invalid_tick" end
  local result=Relations.get(npc_id,counterpart,tick)
  for _,field in ipairs(FIELDS) do
    local scale=deltas.scale and deltas.scale[field]
    local d=deltas[field]
    result[field]=clamp(result[field]*(finite(scale) and scale or 1)+(finite(d) and d or 0),
      LIMITS[field][1],LIMITS[field][2])
  end
  local map=storage.relations[npc_id] or {}
  storage.relations[npc_id]=map
  reserve(map,counterpart,"t",PAIR_CAP)
  map[counterpart]=result
  -- Never expose mutable storage through public reads or writes.
  return Relations.get(npc_id,counterpart,result.t)
end
local function holders(key)
  local result={}
  if not valid_key(key) then return result end
  local id=tonumber(key:match("^npc:(%d+)$"))
  if id then if npc_for(id) then result[1]=id end; return result end
  local name=key:match("^force:(.+)$")
  if name then
    -- The mod has exactly ten NPC slots; this also bounds faction expansion.
    for i=1,10 do
      local npc=npc_for(i)
      local force=npc and npc.force
      if force and (type(force)=="string" and force==name or
        type(force)~="string" and force.valid~=false and force.name==name) then
        result[#result+1]=i
      end
    end
  end
  return result
end
local function add(out,key,about,deltas,salience,reputation)
  if not valid_key(about) then return end
  for _,id in ipairs(holders(key)) do
    if about~="npc:"..id then
      out[#out+1]={holder=id,about=about,deltas=deltas,salience=salience,reputation=reputation}
    end
  end
end
local function witness_ids(event)
  local seen,result={},{}
  for _,key in ipairs(event.witnesses or {}) do
    -- Reputation is specifically for npc: witnesses, not entire witness forces.
    local id=tonumber(key:match("^npc:(%d+)$"))
    if id and npc_for(id) and not seen[id] then seen[id]=true; result[#result+1]=id end
  end
  table.sort(result)
  return result
end
local function fraction(deltas,factor)
  local result={}
  for _,field in ipairs(REPUTATION_FIELDS) do
    if deltas[field] then result[field]=deltas[field]*factor end
  end
  if deltas.scale then
    result.scale={}
    for _,field in ipairs(REPUTATION_FIELDS) do
      if deltas.scale[field] then result.scale[field]=1+(deltas.scale[field]-1)*factor end
    end
  end
  return result
end
local function directed(event,deltas,salience,reverse)
  local out={}
  add(out,event.target,event.actor,deltas,salience)
  if reverse then add(out,event.actor,event.target,reverse,salience) end
  local participants={}
  for _,key in ipairs({event.actor or "",event.target or ""}) do
    for _,id in ipairs(holders(key)) do participants[id]=true end
  end
  for _,id in ipairs(witness_ids(event)) do
    if not participants[id] then add(out,"npc:"..id,event.actor,fraction(deltas,0.25),salience*0.25,true) end
  end
  return out
end
local function bilateral(event,deltas,salience) return directed(event,deltas,salience,deltas) end
local function severity(e)
  local s=e.payload and e.payload.severity
  return clamp(finite(s) and s or 1,0.2,3)
end
local function value(e)
  local v=e.payload and e.payload.value
  return clamp(finite(v) and v or 1,0,1000)
end
-- Public closed rules table: handlers return ordered impact lists and do not write state.
local RULES={}
Relations.RULES=RULES
RULES.attacked=function(e) return directed(e,{trust=-10*severity(e),grudge=15*severity(e),fear=5,affinity=-8},0.8) end
RULES.killed=function(e)
  local out={}
  for _,id in ipairs(witness_ids(e)) do add(out,"npc:"..id,e.actor,{trust=-15,grudge=10},1,true) end
  return out
end
for _,kind in ipairs({"theft","sabotage"}) do
  RULES[kind]=function(e) return directed(e,{trust=-20,grudge=20},0.9) end
end
for _,kind in ipairs({"gift","helped","delivery_completed"}) do
  RULES[kind]=function(e) return directed(e,{trust=6,affinity=5,debt=value(e)},0.5,{affinity=2}) end
end
RULES.trade_completed=function(e) return bilateral(e,{trust=3,affinity=1},0.35) end
for _,kind in ipairs({"trade_failed","delivery_failed"}) do
  RULES[kind]=function(e) return directed(e,{trust=-8},0.5) end
end
RULES.promise_kept=function(e) return directed(e,{trust=5},0.6) end
RULES.promise_broken=function(e) return directed(e,{trust=-12,grudge=10},0.9) end
RULES.treaty_accepted=function(e) return bilateral(e,{trust=8},0.7) end
RULES.treaty_broken=function(e) return directed(e,{trust=-30,grudge=30},1) end
RULES.war_declared=function(e) return bilateral(e,{trust=-20,fear=10},1) end
RULES.peace_made=function(e) return bilateral(e,{trust=5,scale={grudge=0.7}},0.8) end
RULES.insult=function(e) return directed(e,{affinity=-8,grudge=3},0.4) end
RULES.compliment=function(e) return directed(e,{affinity=4},0.3) end
RULES.threat=function(e) return directed(e,{fear=10,affinity=-5,trust=-5},0.7) end
RULES.offer_rejected=function(e)
  -- Here actor is the rejector and target is the original offerer.
  return directed(e,{affinity=-3},0.3)
end
for _,kind in ipairs({"offer_made","promise_made","utterance","plan_completed","plan_failed","research_completed"}) do
  RULES[kind]=function() return {} end
end
local function memory_for(id)
  local m=storage.memory[id]
  if not m then m={episodes={},counters={}}; storage.memory[id]=m end
  return m
end
local function remember(id,seq,salience)
  local episodes=memory_for(id).episodes
  -- An episode reference cannot outlive the chronicle. Reclaim stale slots so old
  -- salient events cannot permanently crowd out all retrievable new memories.
  for i=#episodes,1,-1 do
    if episodes[i].seq<seq-2047 then table.remove(episodes,i) end
  end
  episodes[#episodes+1]={seq=seq,salience=clamp(salience,0,1)}
  table.sort(episodes,function(a,b)
    if a.salience==b.salience then return a.seq>b.seq end
    return a.salience>b.salience
  end)
  if #episodes>EPISODE_CAP then episodes[#episodes]=nil end
end
local function count_event(impact,event)
  if impact.reputation then return end
  local map=memory_for(impact.holder).counters
  reserve(map,impact.about,"last_tick",PAIR_CAP)
  local c=map[impact.about]
  if not c then
    c={last_tick=event.tick}
    for _,field in ipairs(COUNTERS) do c[field]=0 end
    map[impact.about]=c
  end
  c.last_tick=math.max(c.last_tick,event.tick)
  local recipient=impact.about==event.actor
  local kind=event.kind
  local field
  if recipient then
    if kind=="gift" then field="gifts"
    elseif kind=="helped" or kind=="delivery_completed" then field="helped"
    elseif kind=="attacked" or kind=="theft" or kind=="sabotage" or kind=="treaty_broken" then field="harmed"
    elseif kind=="promise_kept" then field="promises_kept"
    elseif kind=="promise_broken" then field="promises_broken" end
  end
  if kind=="trade_completed" then field="trades" end
  if field then c[field]=math.min(1000000,c[field]+1) end
  local npc=npc_for(impact.holder)
  if recipient and (kind=="treaty_broken" or kind=="promise_broken") then
    Personality.drift(npc,"grudge",0.02,kind,event.tick)
    Personality.drift(npc,"cooperation",-0.01,kind,event.tick)
  elseif recipient and (kind=="gift" or kind=="helped") then
    -- Successful deliveries have their own counter but do not trigger kindness drift.
    c.kindness=math.min(1000000,(c.kindness or 0)+1)
    if c.kindness>3 then Personality.drift(npc,"cooperation",0.01,"repeated_kindness",event.tick) end
  end
end
function Relations.memories(npc_id,counterpart,n)
  Relations.ensure()
  local result={}
  local m=storage.memory[npc_id]
  n=math.max(0,math.min(EPISODE_CAP,math.floor(n or 3)))
  if not m then return result end
  for _,entry in ipairs(m.episodes) do
    if #result>=n then break end
    local e=Chronicle.get(entry.seq)
    local involving=e and (not counterpart or e.actor==counterpart or e.target==counterpart)
    if e and not involving then
      for _,key in ipairs(e.witnesses or {}) do if key==counterpart then involving=true; break end end
    end
    if involving then result[#result+1]=e end
  end
  return result
end
function Relations.mood(npc_id,tick)
  Relations.ensure()
  tick=tick or 0
  local m=storage.mood[npc_id] or {stress=0,morale=50,anger=0,t=tick}
  local elapsed=math.max(0,tick-m.t)
  return {stress=decay(m.stress,0,elapsed,10*MINUTE),
    morale=decay(m.morale,50,elapsed,10*MINUTE),anger=decay(m.anger,0,elapsed,10*MINUTE),t=math.max(tick,m.t)}
end
local function adjust_mood(id,deltas,tick)
  local m=Relations.mood(id,tick)
  for _,field in ipairs({"stress","morale","anger"}) do m[field]=clamp(m[field]+(deltas[field] or 0),0,100) end
  storage.mood[id]=m
end
-- Labels (first match): enemy trust<=-50/grudge>=60; rival trust<=-20,
-- affinity<=-25/grudge>=30; wary trust<-5/fear>=25/grudge>=10;
-- ally trust>=50 & affinity>=30; friend trust>=20 & affinity>=10; else neutral.
local function label(r)
  if r.trust<=-50 or r.grudge>=60 then return "enemy" end
  if r.trust<=-20 or r.affinity<=-25 or r.grudge>=30 then return "rival" end
  if r.trust< -5 or r.fear>=25 or r.grudge>=10 then return "wary" end
  if r.trust>=50 and r.affinity>=30 then return "ally" end
  if r.trust>=20 and r.affinity>=10 then return "friend" end
  return "neutral"
end
Relations.label=label
function Relations.stance(npc_id,counterpart,tick)
  local r=Relations.get(npc_id,counterpart,tick)
  return clamp((0.45*r.trust+0.35*r.affinity-0.4*r.grudge-0.1*r.fear)/100,-1,1)
end
function Relations.note_refusal(npc_id,counterpart,topic,tick,cooldown_ticks)
  Relations.ensure()
  if not npc_for(npc_id) or not valid_key(counterpart) or type(topic)~="string" or #topic>256 then return false end
  if not finite(tick) or not finite(cooldown_ticks) then return false end
  local expiry=tick+math.max(0,cooldown_ticks)
  if tick<0 or tick%1~=0 or not finite(expiry) then return false end
  local map=storage.refusals[npc_id] or {}
  storage.refusals[npc_id]=map
  reserve(map,counterpart,"t",PAIR_CAP)
  local pair=map[counterpart] or {t=tick,topics={}}
  map[counterpart]=pair
  pair.t=math.max(pair.t,tick)
  reserve(pair.topics,topic,"t",TOPIC_CAP)
  local old=pair.topics[topic]
  pair.topics[topic]={t=tick,until_tick=math.max(old and old.until_tick or 0,expiry)}
  return true
end
function Relations.refused_recently(npc_id,counterpart,topic,tick)
  if not finite(tick) then return false end
  Relations.ensure()
  local map=storage.refusals[npc_id]
  local pair=map and map[counterpart]
  local entry=pair and pair.topics[topic]
  return entry~=nil and tick>=entry.t and tick<entry.until_tick
end
local function on_event(event)
  Relations.ensure()
  local impacts=RULES[event.kind](event)
  local saliences={}
  -- Record participating NPCs even when the event has no relationship rule.
  for _,key in ipairs({event.actor or "",event.target or ""}) do
    for _,id in ipairs(holders(key)) do saliences[id]=0.2 end
  end
  for _,id in ipairs(witness_ids(event)) do saliences[id]=saliences[id] or 0.1 end
  for _,impact in ipairs(impacts) do
    Relations.apply(impact.holder,impact.about,Relations.modulate(axes(npc_for(impact.holder)),impact.deltas),event.tick)
    count_event(impact,event)
    saliences[impact.holder]=math.max(saliences[impact.holder] or 0,impact.salience)
  end
  for _,id in ipairs(keys(saliences)) do remember(id,event.seq,saliences[id]) end
  local d
  if event.kind=="attacked" then d={stress=12*severity(event),anger=15*severity(event)}
  elseif event.kind=="theft" or event.kind=="sabotage" then d={stress=12,anger=18}
  elseif event.kind=="gift" or event.kind=="helped" then d={morale=8} end
  if d then for _,id in ipairs(holders(event.target)) do adjust_mood(id,d,event.tick) end end
  if event.kind=="plan_completed" or event.kind=="research_completed" or event.kind=="plan_failed" then
    d=event.kind=="plan_failed" and {stress=10} or {morale=10}
    for _,id in ipairs(holders(event.actor)) do adjust_mood(id,d,event.tick) end
  end
end
Chronicle.subscribe(on_event)
return Relations
