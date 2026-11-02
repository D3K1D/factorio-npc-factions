-- How much an engineer puts up with. Damage from a player or another engineer adds up, and fades
-- while it is left alone; past its limit (lower for aggressive characters, higher for peaceful ones
-- and for allies, at once when something of its own is destroyed) it goes to war with them.
local Aggro = {}

local FADE = 18000              -- the tally halves every 5 minutes it is left alone
local LEAST, SPAN = 100, 250    -- the most aggressive put up with 100 hit points of it, the most peaceful 350
local PERSONAL = 2              -- hits on its own body count double
local ALLY = 2                  -- an ally gets twice the patience

function Aggro.limit(aggression, ally)
  aggression = math.max(0, math.min(1, aggression or 0.5))
  return (LEAST + SPAN * (1 - aggression)) * (ally and ALLY or 1)
end

-- One hit (or a building destroyed: how.destroyed). True the moment it has had enough of them;
-- already at war with them (how.at_war), it only keeps count.
function Aggro.hit(npc, key, amount, tick, how)
  how = how or {}
  npc.aggro = npc.aggro or {}
  local e = npc.aggro[key]
  if not e then
    e = {damage = 0, tick = tick}
    npc.aggro[key] = e
  end
  if tick > e.tick then e.damage = e.damage * 0.5 ^ ((tick - e.tick) / FADE) end
  e.tick = tick
  e.damage = e.damage + math.max(0, amount or 0) * (how.personal and PERSONAL or 1)
  if how.at_war then return false end
  return how.destroyed and true or e.damage >= Aggro.limit(how.aggression, how.ally)
end

-- Peace made: the tally starts over.
function Aggro.forgive(npc, key)
  if npc and npc.aggro then npc.aggro[key] = nil end
end

return Aggro
