-- How each engineer's base is doing, judged the way a player would: what it produces, which machines
-- stand still and why, whether drills sit on ore that lasts, whether power has anything to power,
-- how much of the time the engineer does nothing useful, and when it reached the milestones of a
-- normal opening. `/npc-dev scorecard` reports it; test runs compare it with competent play.
local Scorecard={}

local SAMPLE=600   -- ticks between samples of what each engineer is doing
local PRODUCTS={"iron-plate","copper-plate","stone-brick","iron-gear-wheel","electronic-circuit",
  "automation-science-pack","logistic-science-pack","steel-plate"}
-- Phases in which the engineer is not moving its factory forward.
local IDLE={"deferred","need:","waiting","complete","fallback","rail-wait","^smelting","blocked"}

local function produced(force,surface,item)
  local ok,stats=pcall(function() return force.get_item_production_statistics(surface) end)
  if not ok or not stats then return 0 end
  return stats.get_input_count(item) or 0
end

local function idle(phase)
  phase=tostring(phase or "")
  if phase=="" then return true end
  for _,pattern in ipairs(IDLE) do if phase:find(pattern) then return true end end
  return false
end

-- Every SAMPLE ticks: what each engineer is doing, and the milestones it just reached.
function Scorecard.tick(tick)
  if tick%SAMPLE~=0 then return end
  for _,npc in pairs(storage.npcs or {}) do
    if npc.character and npc.character.valid then
      local s=npc.scorecard or {samples=0,idle=0,milestones={}}
      npc.scorecard=s
      s.samples=s.samples+1
      if idle(npc.phase) then s.idle=s.idle+1 end
      local m=s.milestones
      local surface=npc.character.surface
      local function reach(name,yes) if yes and not m[name] then m[name]=tick end end
      reach("copper_plate",produced(npc.force,surface,"copper-plate")>0)
      reach("red_science",produced(npc.force,surface,"automation-science-pack")>0)
      reach("green_science",produced(npc.force,surface,"logistic-science-pack")>0)
      reach("circuit",produced(npc.force,surface,"electronic-circuit")>0)
      local researched=0
      for _,tech in pairs(npc.force.technologies) do
        if tech.researched and tech.research_unit_count and tech.research_unit_count>0 and tech.enabled then
          researched=researched+1
        end
      end
      s.researched=researched
      reach("first_research",researched>0)
    end
  end
end

local function status_is(e,name)
  return defines.entity_status[name]~=nil and e.status==defines.entity_status[name]
end

-- The whole picture for one engineer (scans its buildings: on demand only).
function Scorecard.of(npc)
  local c=npc.character
  if not (c and c.valid) then return {name=npc.name,dead=true} end
  local surface,force=c.surface,npc.force
  local s=npc.scorecard or {samples=0,idle=0,milestones={}}
  local out={name=npc.name,minutes=math.floor(game.tick/3600),produced={},
    idle_pct=s.samples>0 and math.floor(100*s.idle/s.samples) or nil,milestones={},researched=s.researched or 0,
    drills={burner=0,electric=0,thin=0,empty=0,no_fuel=0,out_full=0},coal_loops=0,
    furnaces={total=0,no_fuel=0,out_full=0,no_input=0,working=0},
    power={pumps=0,boilers=0,engines=0,engines_working=0,boilers_no_fuel=0,consumers=0,poles=0},
    assemblers={total=0,working=0},labs={total=0,working=0},belts=0,inserters=0}
  for name,t in pairs(s.milestones) do out.milestones[name]=math.floor(t/3600) end
  for _,item in ipairs(PRODUCTS) do out.produced[item]=produced(force,surface,item) end
  for _,e in ipairs(surface.find_entities_filtered{force=force}) do
    local t=e.type
    if t=="mining-drill" then
      local burner=e.burner~=nil
      local d=out.drills
      if burner then d.burner=d.burner+1 else d.electric=d.electric+1 end
      local r=e.prototype.get_mining_drill_radius()
      local ore=0
      for _,res in ipairs(surface.find_entities_filtered{type="resource",
          area={{e.position.x-r,e.position.y-r},{e.position.x+r,e.position.y+r}}}) do ore=ore+res.amount end
      if ore==0 then d.empty=d.empty+1 elseif ore<(burner and 2000 or 8000) then d.thin=d.thin+1 end
      if status_is(e,"no_fuel") then d.no_fuel=d.no_fuel+1 end
      if status_is(e,"waiting_for_space_in_destination") then d.out_full=d.out_full+1 end
      if e.drop_target and e.drop_target.valid and e.drop_target.type=="mining-drill" then
        out.coal_loops=out.coal_loops+1
      end
    elseif t=="furnace" then
      local f=out.furnaces
      f.total=f.total+1
      if status_is(e,"no_fuel") then f.no_fuel=f.no_fuel+1
      elseif status_is(e,"full_output") then f.out_full=f.out_full+1
      elseif status_is(e,"no_ingredients") then f.no_input=f.no_input+1
      elseif status_is(e,"working") then f.working=f.working+1 end
    elseif t=="offshore-pump" then out.power.pumps=out.power.pumps+1
    elseif t=="boiler" then
      out.power.boilers=out.power.boilers+1
      if status_is(e,"no_fuel") then out.power.boilers_no_fuel=out.power.boilers_no_fuel+1 end
    elseif t=="generator" then
      out.power.engines=out.power.engines+1
      if status_is(e,"working") then out.power.engines_working=out.power.engines_working+1 end
    elseif t=="electric-pole" then out.power.poles=out.power.poles+1
    elseif t=="assembling-machine" then
      out.assemblers.total=out.assemblers.total+1
      if status_is(e,"working") then out.assemblers.working=out.assemblers.working+1 end
    elseif t=="lab" then
      out.labs.total=out.labs.total+1
      if status_is(e,"working") then out.labs.working=out.labs.working+1 end
    elseif t=="transport-belt" or t=="underground-belt" or t=="splitter" or t=="loader" then out.belts=out.belts+1
    elseif t=="inserter" then out.inserters=out.inserters+1 end
    if t~="electric-pole" and t~="generator" and e.prototype.electric_energy_source_prototype then
      out.power.consumers=out.power.consumers+1
    end
  end
  local inventory={}
  for _,stack in ipairs(c.get_main_inventory().get_contents()) do
    if stack.count>=50 then inventory[stack.name]=(inventory[stack.name] or 0)+stack.count end
  end
  out.carrying=inventory
  out.phase=npc.phase
  out.plans=#(npc.design_history or {})
  return out
end

function Scorecard.all()
  local out={}
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    if npc then out[#out+1]=Scorecard.of(npc) end
  end
  return {ok=true,tick=game.tick,npcs=out}
end

return Scorecard
