-- Holograms of a plan's whole layout while it is built (F-16b): the user wants to see a blueprint the engineer takes
-- from the packs they gave it standing as a hologram from the start, filled in piece by piece. Entity ghosts of the
-- engineer's own force (its allies see them as well); for a player's force that is not its ally, the same drawn with
-- LuaRendering. A real building replaces its ghost when placed (taken from the engineer's inventory as any build,
-- never revived for free); what is left goes when the plan is done, handed back or given up.
-- The mod setting npc-factions-holograms: "imported" (the default: plans from the user's packs), "all", or "off".

local Holograms={}
local Ghosts=require("scripts.ghosts")

local function mode()
  local s=settings.global["npc-factions-holograms"]
  return s and s.value or "imported"
end

function Holograms.wanted(plan)
  local m=mode()
  if m=="off" or type(plan)~="table" then return false end
  if m=="all" then return true end
  return type(plan.source)=="string" and plan.source:find("import:",1,true)~=nil
end

-- The forces that do not see the engineer's ghosts (neither theirs nor friends both ways): every force a player is or
-- may come to be on (not the engineers' own, nor the biters' or nature's), so one who joins later sees it too.
function Holograms.viewers(force)
  local out,seen={},{}
  local function add(f)
    if f and f.valid and f~=force and not seen[f.name] and not (force.get_friend(f) and f.get_friend(force)) then
      seen[f.name]=true
      out[#out+1]=f
    end
  end
  for name,f in pairs(game.forces) do
    if name~="enemy" and name~="neutral" and not name:find("^npc%-") then add(f) end
  end
  return out
end

function Holograms.place(npc,plan)
  if not Holograms.wanted(plan) then return 0 end
  local c=npc.character
  if not (c and c.valid) then return 0 end
  local viewers=Holograms.viewers(npc.force)
  local n=0
  for _,id in ipairs(plan.order or {}) do
    local e=plan.entities[id]
    if e and e.world and not (e.entity and e.entity.valid) and Holograms.one(npc,e,viewers) then n=n+1 end
  end
  return n
end

-- One piece's hologram (its ghost, and its drawing for `viewers`): true when the ghost stands.
function Holograms.one(npc,e,viewers)
  local c=npc.character
  if not (c and c.valid) then return false end
  local surface=c.surface
  -- (only where the game lets a ghost go, never on a building standing there: Ghosts, W-35)
  local g=Ghosts.create(surface,{inner_name=e.prototype,position=e.world,
    direction=e.direction,force=npc.force,expires=false,type=e.belt_to_ground_type,   -- (an underground's side)
    mirror=e.mirror==true or nil})   -- (and a mirrored one's mirroring: DS-3)
  local ok=g~=nil
  if ok and g then
    e.ghost=g
    if e.recipe then pcall(function() g.set_recipe(e.recipe) end) end
  end
  viewers=viewers or Holograms.viewers(npc.force)
  if #viewers>0 then
    local okr,r=pcall(rendering.draw_sprite,{sprite="entity/"..e.prototype,target=e.world,surface=surface,
      forces=viewers,tint={r=0.45,g=0.7,b=1,a=0.45},orientation=(e.direction or 0)/16})
    if okr then e.holo=r end
  end
  return ok and g~=nil
end

-- Ghosts a piece could not have when the plan began (a tree, a rock, a leftover on its spot: Ghosts.create said no),
-- tried again now and then while it is built (DesignedStart.next, every RETRY ticks), so its allies see the whole
-- layout once the spot is clear (Codex, W-35). Its drawing is never made twice. Returns how many it put down.
Holograms.RETRY=600
function Holograms.retry(npc,plan)
  if not Holograms.wanted(plan) then return 0 end
  local c=npc.character
  if not (c and c.valid) then return 0 end
  local n=0
  for _,id in ipairs(plan.order or {}) do
    local e=plan.entities[id]
    if e and e.world and not (e.entity and e.entity.valid) and not (e.ghost and e.ghost.valid)
        and not (e.left_out or e.skipped) then
      local g=Ghosts.create(c.surface,{inner_name=e.prototype,position=e.world,direction=e.direction,force=npc.force,
        expires=false,type=e.belt_to_ground_type,mirror=e.mirror==true or nil})
      if g then
        e.ghost=g
        if e.recipe then pcall(function() g.set_recipe(e.recipe) end) end
        n=n+1
      end
    end
  end
  return n
end

-- A piece moved to another spot (scripts/obstacles.lua): its hologram goes with it, if it had one.
function Holograms.moved(npc,e)
  if not (e.ghost or e.holo) then return end
  Holograms.settle(e)
  Holograms.one(npc,e)
end

-- A planned building placed (or its plan over): its ghost and drawing go.
function Holograms.settle(entity)
  if not entity then return end
  if entity.ghost and entity.ghost.valid then entity.ghost.destroy() end
  entity.ghost=nil
  if entity.holo then pcall(function() entity.holo.destroy() end) end
  entity.holo=nil
end

function Holograms.clear(plan)
  for _,e in pairs(plan and plan.entities or {}) do Holograms.settle(e) end
end

return Holograms
