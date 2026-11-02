-- An engineer's factory plan (companion/factory_plan.py): its large sections, what each makes and takes
-- and how busy it runs, where each goes and the room kept for it to grow, the bus lanes kept for later and
-- the ground to clear ahead. The architect plans it on the host; the game keeps it with the engineer, so it
-- survives restarts and reloads, and sends it back with every site export. A plan arrives as JSON from the
-- host and is copied field by field, bounded like a plan's other blocks: what does not fit is left out.
local FactoryPlan={}

local MAX_SECTIONS,MAX_ZONES,MAX_LANES,MAX_ITEMS,MAX_RETIRE,MAX_LINES=24,32,24,12,8,8
local MAX_SPAN=2048   -- a zone at most this many tiles across

local function finite(v) return type(v)=="number" and v==v and math.abs(v)<1e9 end
local function text(v,n) return type(v)=="string" and v:sub(1,n) or nil end
local function share(v) return finite(v) and math.max(0,math.min(1,v)) or nil end
local function whole(v) return finite(v) and math.floor(v) or nil end

-- items a second by name (sorted, so every peer keeps the same ones)
local function rates(raw)
  if type(raw)~="table" then return nil end
  local names={}
  for k in pairs(raw) do if type(k)=="string" and #k<=64 then names[#names+1]=k end end
  table.sort(names)
  local out,n={},0
  for _,item in ipairs(names) do
    local rate=raw[item]
    if finite(rate) and rate>=0 and n<MAX_ITEMS then out[item]=rate; n=n+1 end
  end
  return n>0 and out or nil
end

local function names(raw,limit,len)
  if type(raw)~="table" then return nil end
  local out={}
  for _,v in ipairs(raw) do if type(v)=="string" and #out<limit then out[#out+1]=v:sub(1,len) end end
  return #out>0 and out or nil
end

-- world tiles {x0,y0,x1,y1}
local function box(raw)
  if type(raw)~="table" then return nil end
  local x0,y0,x1,y1=raw[1],raw[2],raw[3],raw[4]
  if finite(x0) and finite(y0) and finite(x1) and finite(y1) and x1>x0 and y1>y0
      and x1-x0<=MAX_SPAN and y1-y0<=MAX_SPAN then
    return {x0,y0,x1,y1}
  end
end

local function section(raw)
  if type(raw)~="table" or type(raw.id)~="string" or type(raw.kind)~="string" then return nil end
  return {id=raw.id:sub(1,48),kind=raw.kind:sub(1,32),makes=rates(raw.makes),takes=rates(raw.takes),
    load=share(raw.load),zone=text(raw.zone,48),room=text(raw.room,48),grow=text(raw.grow,16),
    status=text(raw.status,16),priority=share(raw.priority),progress=share(raw.progress),
    why=text(raw.why,160),quality=text(raw.quality,32),replaces=names(raw.replaces,4,48),
    spm=finite(raw.spm) and raw.spm>0 and raw.spm or nil}
end

local function zone(raw)
  local b=type(raw)=="table" and box(raw.box)
  if not (b and type(raw.id)=="string") then return nil end
  local left
  if type(raw.left)=="table" then
    left={}
    for _,k in ipairs({"trees","rocks","cliffs"}) do
      if finite(raw.left[k]) then left[k]=math.max(0,math.floor(raw.left[k])) end
    end
  end
  return {id=raw.id:sub(1,48),box=b,clear=raw.clear==true,priority=share(raw.priority) or 0,why=text(raw.why,160),
    ["for"]=text(raw["for"],48),use=text(raw.use,8),left=left,cleared=whole(raw.cleared)}
end

local function lane(raw)
  if type(raw)~="table" or type(raw.item)~="string" then return nil end
  return {item=raw.item:sub(1,64),belts=math.max(1,math.min(8,whole(raw.belts) or 1)),["for"]=text(raw["for"],48)}
end

local function retirement(raw)
  if type(raw)~="table" or type(raw.section)~="string" then return nil end
  return {section=raw.section:sub(1,48),after=text(raw.after,48),what=names(raw.what,16,64),done=whole(raw.done)}
end

local function list(raw,limit,copy)
  local out={}
  for _,v in ipairs(type(raw)=="table" and raw or {}) do
    if #out>=limit then break end
    local c=copy(v)
    if c then out[#out+1]=c end
  end
  return out
end

-- What the plan was made from, for telling later when the factory or its goals moved enough to plan again.
local function basis(raw)
  if type(raw)~="table" then return nil end
  return {packs=names(raw.packs,12,64),ready=whole(raw.ready),recipes=whole(raw.recipes),supply=rates(raw.supply),
    priorities=rates(raw.priorities),short=names(raw.short,8,64),paused=raw.paused==true or nil,
    path=whole(raw.path)}
end

-- A plan as the host sent it, bounded; nil and why when it is not one.
function FactoryPlan.copy(raw)
  if type(raw)~="table" or not finite(raw.version) or raw.version<1 then return nil,"plan_shape" end
  local out={schema="npc-factory-plan-v1",version=math.floor(raw.version),tick=whole(raw.tick),asked=whole(raw.asked),
    updated=whole(raw.updated),
    source=text(raw.source,16),label=text(raw.label,64),why=text(raw.why,160),summary=text(raw.summary,240),
    sections=list(raw.sections,MAX_SECTIONS,section),zones=list(raw.zones,MAX_ZONES,zone),
    lanes=list(raw.lanes,MAX_LANES,lane),retire=list(raw.retire,MAX_RETIRE,retirement),
    notes=names(raw.notes,MAX_LINES,160),changes=names(raw.changes,MAX_LINES,160),basis=basis(raw.basis)}
  return out
end

-- Keep `raw` as the engineer's plan. An older version than the one kept is refused (a slow host thread must
-- not undo a newer plan); the same version may be stored again (progress, cleared ground).
function FactoryPlan.store(npc,raw,tick)
  local plan,why=FactoryPlan.copy(raw)
  if not plan then return nil,why end
  local old=npc.factory_plan
  if old and old.version and plan.version<old.version then return nil,"older_version" end
  plan.stored=tick
  npc.factory_plan=plan
  -- a waiting request's export was made before: it carries the new plan now (made again whole, the export froze the
  -- game for 30 s and more at every plan request: OP-3, 2026-10-26)
  local d=npc.design
  if d and d.status=="waiting" and d.export_cache then d.export_cache.factory_plan=plan end
  return plan
end

function FactoryPlan.export(npc) return npc and npc.factory_plan or nil end

-- The path to space in a few numbers, from the technology tree to `dag.target` (progression.lua): how
-- many technologies and research units are left, the units of each science pack still needed, and the
-- next technologies on it (prerequisites first).
function FactoryPlan.space_path(dag)
  if type(dag)~="table" or type(dag.nodes)~="table" then return nil end
  local left,units,packs,ahead=0,0,{},{}
  for _,name in ipairs(dag.order or {}) do
    local node=dag.nodes[name]
    if node and not node.researched then
      left=left+1
      local count=finite(node.research_unit_count) and node.research_unit_count or 0
      units=units+count
      for _,s in ipairs(node.science or {}) do
        if type(s.name)=="string" then packs[s.name]=(packs[s.name] or 0)+count*(finite(s.amount) and s.amount or 1) end
      end
      if #ahead<6 then ahead[#ahead+1]=name end
    end
  end
  return {target=dag.target,left=left,units=units,packs=packs,next=ahead}
end

return FactoryPlan
