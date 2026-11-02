-- Save-safe, bounded history. Requiring this module never touches storage.
local Chronicle = {}
local CAP, MAX_NODES, MAX_DEPTH = 2048, 512, 8
local listeners = {}
local kinds = {}
for _, kind in ipairs({"treaty_accepted", "treaty_broken", "offer_made", "offer_rejected",
  "war_declared", "peace_made", "gift", "helped", "trade_completed", "trade_failed",
  "delivery_completed", "delivery_failed", "theft", "sabotage", "attacked", "killed",
  "promise_made", "promise_kept", "promise_broken", "utterance", "insult", "compliment",
  "threat", "plan_completed", "plan_failed", "research_completed"}) do kinds[kind] = true end
local sources = {native=true, society=true, chat=true, llm=true, teacher=true, player=true}
local function finite(n) return type(n)=="number" and n==n and math.abs(n)<math.huge end
local function key(k)
  return type(k)=="string" and #k<=256 and
    (k:match("^npc:[1-9]%d*$") or k:match("^player:[1-9]%d*$") or k:match("^force:.+$"))
end
-- Validate before copying: functions, userdata, metatables and cycles cannot enter a save.
local function copy(value, seen, budget, depth)
  local t = type(value)
  if t=="nil" or t=="boolean" then return value end
  if t=="number" and finite(value) then return value end
  if t=="string" and #value<=4096 then return value end
  if t~="table" or getmetatable(value) or seen[value] or depth>MAX_DEPTH then error("unsafe event data") end
  seen[value]=true
  local keys={}
  for k in pairs(value) do
    if not (type(k)=="string" and #k<=256 or finite(k)) then error("unsafe event key") end
    budget.n=budget.n+1
    if budget.n>MAX_NODES then error("event data too large") end
    keys[#keys+1]=k
  end
  table.sort(keys, function(a,b)
    if type(a)==type(b) then return a<b end
    return type(a)<type(b)
  end)
  local out={}
  for _, k in ipairs(keys) do out[k]=copy(value[k],seen,budget,depth+1) end
  seen[value]=nil
  return out
end
local function clone(value) return copy(value, {}, {n=0}, 0) end
function Chronicle.ensure()
  storage.character_version=1
  storage.chronicle=storage.chronicle or {next_seq=1, ring={}, count=0, cap=CAP}
  return storage.chronicle
end
function Chronicle.subscribe(fn)
  assert(type(fn)=="function", "chronicle listener must be a function")
  for _, existing in ipairs(listeners) do if existing==fn then return end end
  listeners[#listeners+1]=fn
end
function Chronicle.record(event)
  if type(event)~="table" or not kinds[event.kind] then return nil,"unknown_kind" end
  if not finite(event.tick) or event.tick<0 or event.tick%1~=0 then return nil,"invalid_tick" end
  if event.actor~=nil and not key(event.actor) or event.target~=nil and not key(event.target) then
    return nil,"invalid_counterpart"
  end
  if not sources[event.source] then return nil,"invalid_source" end
  if event.refs~=nil and type(event.refs)~="table" or event.payload~=nil and type(event.payload)~="table" then
    return nil,"invalid_data"
  end
  if event.witnesses~=nil then
    if type(event.witnesses)~="table" or getmetatable(event.witnesses) then return nil,"invalid_witnesses" end
    local count=0
    for k,v in pairs(event.witnesses) do
      count=count+1
      if not finite(k) or k%1~=0 or k<1 or k>128 or not key(v) then return nil,"invalid_witnesses" end
    end
    if count~=#event.witnesses then return nil,"invalid_witnesses" end
  end
  -- Reserve one node for seq, so all later copies fit the same validation budget.
  local ok, stored=pcall(copy, {kind=event.kind,actor=event.actor,target=event.target,
    tick=event.tick,refs=event.refs,payload=event.payload,source=event.source,witnesses=event.witnesses},
    {}, {n=1}, 0)
  if not ok then return nil,"unsafe_data" end
  local s=Chronicle.ensure()
  stored.seq=s.next_seq
  s.next_seq=s.next_seq+1
  s.ring[(stored.seq-1)%CAP+1]=stored
  s.count=math.min(CAP,s.count+1)
  -- Each listener gets its own copy, in registration order; nested recording is allowed.
  -- A failing listener must never abort the game: errors are logged and skipped
  -- (pcall is deterministic, so every multiplayer peer behaves the same).
  for _, fn in ipairs(listeners) do
    local ok, problem = pcall(fn, clone(stored))
    if not ok and log then log("[npc-factions] chronicle listener error: " .. tostring(problem)) end
  end
  return clone(stored)
end
function Chronicle.get(seq)
  if not finite(seq) or seq%1~=0 then return nil end
  local s=Chronicle.ensure()
  local event=s.ring[(seq-1)%CAP+1]
  if event and event.seq==seq then return clone(event) end
end
function Chronicle.recent(n, filter)
  local s=Chronicle.ensure()
  local result, allowed={},nil
  n=math.max(0,math.min(CAP,math.floor(n or 10)))
  filter=filter or {}
  if filter.kinds then
    allowed={}
    for _, kind in ipairs(filter.kinds) do allowed[kind]=true end
  end
  for seq=s.next_seq-1,s.next_seq-s.count,-1 do
    if #result>=n then break end
    local e=s.ring[(seq-1)%CAP+1]
    local involving=not filter.involving or e.actor==filter.involving or e.target==filter.involving
    if not involving then
      for _, witness in ipairs(e.witnesses or {}) do if witness==filter.involving then involving=true; break end end
    end
    if involving and (not allowed or allowed[e.kind]) then result[#result+1]=clone(e) end
  end
  return result
end
return Chronicle
