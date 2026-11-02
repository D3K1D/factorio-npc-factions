-- Priorities on the engineer's factory. Its strategist raises or lowers an item or a section (the
-- prioritize tool) for a while or until something happens; here the setting is checked, kept with the
-- engineer (so it lasts through runner restarts and travels with the save), timed out, and sent with every
-- site (site.priorities) and situation. The companion weighs it with everything else it reads from the
-- engineer (companion/priorities.py) and judges the conditions. Priorities.focus adds what the companion
-- reads to weigh the rest: the research under way and the way to space with the packs they need, the
-- asks on its agenda, what it can fight with, and its character.
local Armory=require("scripts.armory")
local Progression=require("scripts.progression")
local TeamWorld=require("scripts.team_world")
local TeamResearch=require("scripts.team_research")
local Personality=require("scripts.personality")
local Priorities={}

local LEVELS={top=true,high=true,normal=true,low=true,ignore=true,auto=true}   -- auto: back to the companion's own
-- The ledger's kinds of section (companion/ledger.py); a section may also be named by a build's purpose
-- or a bus stage of its own.
local SECTIONS={science=true,labs=true,rocket=true,mall=true,ammo=true,defense=true,power=true,smelting=true,
  mining=true,bus=true,intermediate=true}
local CONDITIONS={researched=true,built=true,calm=true,attacked=true,supplied=true}
local MAX_SET=12
local MINUTE=3600
local DEFAULT_MINUTES,MAX_MINUTES=30,240   -- a condition still runs out after MAX_MINUTES
local MAX_RESEARCH,MAX_ASKS,MAX_NEXT=6,8,3
-- The axes the companion's priorities lean on, besides those every site carries.
local AXES={"aggression","defense","science","logistics","power","courage","patience","cooperation","ambition",
  "frugality"}

local function try(f,...) local ok,value=pcall(f,...); if ok then return value end end

-- A build of its own: a purpose it built, or a stage of its bus.
local function own(npc,name)
  for _,plan in ipairs(npc.design_history or {}) do if plan.purpose==name then return true end end
  for _,stage in ipairs(npc.base_plan and npc.base_plan.stages or {}) do if stage==name then return true end end
  return false
end

-- The tool's arguments checked: {name, level, until, minutes}, or nil and why not.
function Priorities.check(npc,args)
  args=type(args)=="table" and args or {}
  local name=type(args.name)=="string" and args.name:sub(1,64) or ""
  if name=="" then return nil,"name" end
  local level=LEVELS[args.level] and args.level or nil
  if not level then return nil,"level" end
  local item=prototypes.item[name] or (prototypes.fluid and prototypes.fluid[name])
  if not (SECTIONS[name] or item or own(npc,name)) then return nil,"name" end
  local out={name=name,level=level}
  local cond=args["until"]
  if type(cond)=="string" and cond:find("%S") then
    local kind,rest=cond:match("^%s*(%a+)%s*(.-)%s*$")
    if not (kind and CONDITIONS[kind]) then return nil,"until" end
    if kind=="researched" and not (npc.force.technologies and npc.force.technologies[rest]) then return nil,"until" end
    if kind=="built" and rest=="" then return nil,"until" end
    out["until"]=rest~="" and kind.." "..rest:sub(1,64) or kind
  end
  local minutes=math.floor(tonumber(args.minutes) or (out["until"] and MAX_MINUTES or DEFAULT_MINUTES))
  out.minutes=math.max(1,math.min(MAX_MINUTES,minutes))
  return out
end

-- Kept with the engineer, newest first; one per name (auto takes it off).
function Priorities.set(npc,args,tick,reason)
  local list=npc.priorities or {}
  for i=#list,1,-1 do if list[i].name==args.name then table.remove(list,i) end end
  if args.level~="auto" then
    table.insert(list,1,{name=args.name,level=args.level,["until"]=args["until"],tick=tick,
      till=tick+args.minutes*MINUTE,reason=type(reason)=="string" and reason:sub(1,80) or nil})
    while #list>MAX_SET do table.remove(list) end
  end
  npc.priorities=list
end

-- What is set now, for the site and the situation (nil when nothing is): minutes left and since set.
function Priorities.list(npc,tick)
  local list,out=npc.priorities or {},{}
  for i=#list,1,-1 do if list[i].till<=tick then table.remove(list,i) end end
  for _,p in ipairs(list) do
    out[#out+1]={name=p.name,level=p.level,["until"]=p["until"],left=math.ceil((p.till-tick)/MINUTE),
      ago=math.floor((tick-p.tick)/MINUTE),reason=p.reason}
  end
  return #out>0 and out or nil
end

local function packs_of(tech)
  local out={}
  for _,i in pairs(tech and tech.research_unit_ingredients or {}) do out[#out+1]=i.name end
  table.sort(out)
  return out
end

-- The research under way and queued (the team's in the team world, where the players lead it), each
-- with its packs; an empty list when the labs have nothing to do.
local function research(npc)
  local force,names=npc.force,{}
  if TeamWorld.active() then
    local view=try(TeamResearch.view) or {}
    names[1]=view.current
    for _,q in ipairs(view.queue or {}) do names[#names+1]=q.name end
  else
    names[1]=force.current_research and force.current_research.name
    for _,t in ipairs(try(function() return force.research_queue end) or {}) do
      names[#names+1]=type(t)=="string" and t or t.name
    end
  end
  local out,seen={},{}
  for _,name in ipairs(names) do
    if not seen[name] and #out<MAX_RESEARCH then
      seen[name]=true
      out[#out+1]={name=name,packs=packs_of(force.technologies[name])}
    end
  end
  return out
end

-- The rest of the way to space (the game's tech tree to the space platform, else the rocket silo, mods
-- included): the units of each pack still needed, how many techs are left of how many, and the next
-- research that needs packs: its prerequisites done, or done by doing (trigger techs, Space Age's first
-- steps, need no packs and come on their own).
local function space(force)
  local target=force.technologies["space-platform"] and "space-platform" or "rocket-silo"
  local dag=force.technologies[target] and try(Progression.technology_dag,force,target)
  if not dag then return nil end
  local packs,next_packs,next_techs,left,waits={},{},{},0,{}
  for _,name in ipairs(dag.order) do   -- (prerequisites come first)
    local node=dag.nodes[name]
    if not node.researched then
      left=left+1
      for _,s in ipairs(node.science or {}) do
        packs[s.name]=(packs[s.name] or 0)+(node.research_unit_count or 0)*(s.amount or 1)
      end
      local needs,behind=#(node.science or {})>0,false   -- (behind: a research with packs comes first)
      for _,p in ipairs(node.prerequisites) do if waits[p] then behind=true; break end end
      waits[name]=needs or behind
      if needs and not behind and #next_techs<MAX_NEXT then
        next_techs[#next_techs+1]=name
        for _,s in ipairs(node.science or {}) do next_packs[s.name]=true end
      end
    end
  end
  local listed={}
  for name in pairs(next_packs) do listed[#listed+1]=name end
  table.sort(listed)
  return {target=target,left=left,total=#dag.order,packs=packs,next={techs=next_techs,packs=listed}}
end

-- What it has said it will do (its agenda: a teammate's or a player's ask, often), with the items.
local function asks(npc)
  local out={}
  for _,it in ipairs(npc.agent and npc.agent.agenda or {}) do
    if #out>=MAX_ASKS then break end
    local a,items=it.args or {},{}
    if type(a.item)=="string" then items[a.item]=a.count or 1 end
    for _,i in ipairs(a.items or a.give or {}) do items[i.name]=(items[i.name] or 0)+(i.count or 1) end
    out[#out+1]={tool=it.tool,priority=it.priority,goal=a.goal,items=next(items) and items or nil,
      reason=type(it.reason)=="string" and it.reason:sub(1,60) or nil}
  end
  return out
end

function Priorities.focus(npc)
  local force=npc.force
  local all=Personality.axes and try(Personality.axes,npc)
  local axes
  if all then
    axes={}
    for _,k in ipairs(AXES) do axes[k]=all[k] end
  end
  -- what it can fight with (the team world's site carries it already), against biters evolved this far
  local arms
  if not TeamWorld.active() then
    local evolution=try(function() return game.forces.enemy.get_evolution_factor(npc.character.surface) end)
    arms=try(Armory.defense,force,evolution or 0)
  end
  return {research=try(research,npc),space=try(space,force),asks=asks(npc),axes=axes,
    foe=npc.foe and npc.foe.name or nil,arms=arms}
end

return Priorities
