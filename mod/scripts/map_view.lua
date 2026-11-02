-- Engineers on the players' map, as teammates would be: a name marker that follows each engineer,
-- and the land around it and its base kept charted ("Show engineers on the map" setting).
local MapView={}

local NEAR=48     -- tiles charted around an engineer every few seconds (it walks about 45 in that time)
local BASE=96     -- and around its home about once a minute

local function enabled()
  local s=settings.global["npc-factions-show-on-map"]
  return s==nil or s.value==true
end

function MapView.tick(tick)
  storage.map_view=storage.map_view or {}
  local forces={}
  for _,player in pairs(game.connected_players) do forces[player.force.name]=player.force end
  local on=enabled()
  for name,tags in pairs(storage.map_view) do
    if not forces[name] or not on then
      for _,tag in pairs(tags) do if tag.valid then tag.destroy() end end
      storage.map_view[name]=nil
    end
  end
  if not on then return end
  for name,force in pairs(forces) do
    local tags=storage.map_view[name] or {}
    storage.map_view[name]=tags
    for id,npc in pairs(storage.npcs or {}) do
      local old=tags[id]
      if old and old.valid then old.destroy() end
      tags[id]=nil
      local c=npc.character
      if c and c.valid and npc.force~=force then
        local p=c.position
        force.chart(c.surface,{{p.x-NEAR,p.y-NEAR},{p.x+NEAR,p.y+NEAR}})
        local h=npc.home
        if h and tick%3600<300 then force.chart(c.surface,{{h.x-BASE,h.y-BASE},{h.x+BASE,h.y+BASE}}) end
        tags[id]=force.add_chart_tag(c.surface,{position=p,text=npc.name,icon={type="virtual",name="signal-dot"}})
      end
    end
  end
end

return MapView
