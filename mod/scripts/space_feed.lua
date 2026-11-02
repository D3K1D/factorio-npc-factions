-- Space science down to the labs without hands (R-8): a cargo landing pad set beside a lab, asking the platform above
-- for the packs it makes, and an inserter from the pad into the lab (the labs pass packs on to their neighbours as
-- they are fed). Where no pad fits beside a lab, it goes on free ground near them, exported as a source
-- (site.space_pads) for Factory's input hookup to belt into the lab row (F-20). Not robots: in Space Age requester chests come with logistic-system, which itself needs space
-- science. Carried by the engineer is not automated (the user's rule: belts, inserters or robots).
local TeamWorld=require("scripts.team_world")
local Feed={}
local PAD="cargo-landing-pad"
Feed.REQUEST=200   -- packs the pad asks of the platform, of each kind it makes

local function placeable(surface,force,name,position,direction)
  return surface.can_place_entity{name=name,position=position,direction=direction,force=force,
    build_check_type=defines.build_check_type.manual}
end

-- Within an electric pole's supply area (the inserter runs on the lab's power).
local function powered(surface,force,position)
  for _,pole in ipairs(surface.find_entities_filtered{type="electric-pole",force=force,position=position,radius=12}) do
    local d=pole.prototype.get_supply_area_distance()
    if math.abs(pole.position.x-position.x)<=d and math.abs(pole.position.y-position.y)<=d then return true end
  end
  return false
end

local SIDES={{"east",1,0},{"west",-1,0},{"south",0,1},{"north",0,-1}}

local function even(v) return math.floor(v+.5)%2==0 end

-- Where a pad and its inserter go beside a lab: the inserter on the tile by the middle of one of the lab's sides,
-- facing the pad (an inserter faces where it picks up), the pad's near edge within its reach; on its own ground, the
-- inserter powered. The pad stands on its 2x2 build grid (even centres): where a plain inserter would leave it off the
-- grid, a long-handed one reaches the tile beyond. {lab=, pad=, inserter=, picker=, direction=} or nil.
function Feed.site(npc,lab)
  local surface,force=lab.surface,npc.force
  local c=lab.position
  local plot=TeamWorld.active() and TeamWorld.plot("npc-"..npc.id)
  for _,side in ipairs(SIDES) do
    local dx,dy=side[2],side[3]
    local ins={x=c.x+2*dx,y=c.y+2*dy}
    local along=dx~=0 and ins.x or ins.y
    for _,pick in ipairs({{"inserter",4.5},{"long-handed-inserter",5.5}}) do
      local picker,far=pick[1],pick[2]
      if prototypes.entity[picker] and even(along+(dx+dy)*far)
          and placeable(surface,force,picker,ins,defines.direction[side[1]]) and powered(surface,force,ins) then
        for _,shift in ipairs({.5,-.5,1.5,-1.5,2.5,-2.5,3.5,-3.5}) do
          local pad={x=ins.x+dx*far+(dy~=0 and shift or 0),y=ins.y+dy*far+(dx~=0 and shift or 0)}
          local mine=not plot or (TeamWorld.holds(plot,{x=pad.x-4,y=pad.y-4}) and TeamWorld.holds(plot,{x=pad.x+4,y=pad.y+4}))
          if even(pad.x) and even(pad.y) and mine and placeable(surface,force,PAD,pad) then
            return {lab=lab,pad=pad,inserter=ins,picker=picker,direction=side[1]}
          end
        end
      end
    end
  end
end

-- The pad asks the platform above for `item` (once; a request the player or another step set is kept).
function Feed.ask(pad,item,count)
  local sections=pad.get_logistic_sections()
  if not sections then return end
  for i=1,sections.sections_count do
    local section=sections.get_section(i)
    for j=1,section.filters_count do
      local f=section.get_slot(j)
      if f and f.value and f.value.name==item then return end
    end
  end
  local section=sections.sections_count>0 and sections.get_section(1) or sections.add_section()
  section.set_slot(section.filters_count+1,{value={name=item,quality="normal"},min=count})
end

-- A free spot for a pad near a lab, on its 2x2 grid (even centres), on its own ground, nearest first; or nil.
function Feed.free_pad(npc,lab,radius)
  local surface,force=lab.surface,npc.force
  local plot=TeamWorld.active() and TeamWorld.plot("npc-"..npc.id)
  local cx,cy=2*math.floor(lab.position.x/2+.5),2*math.floor(lab.position.y/2+.5)
  for r=0,math.floor((radius or 24)/2) do
    for dy=-r,r do
      for dx=-r,r do
        if math.max(math.abs(dx),math.abs(dy))==r then
          local pad={x=cx+2*dx,y=cy+2*dy}
          local mine=not plot or (TeamWorld.holds(plot,{x=pad.x-4,y=pad.y-4}) and TeamWorld.holds(plot,{x=pad.x+4,y=pad.y+4}))
          if mine and placeable(surface,force,PAD,pad) then return pad end
        end
      end
    end
  end
end

-- A lab of its force holding `item` (it came down and reached one), or nil.
local function lab_holding(surface,force,item)
  for _,lab in ipairs(surface.find_entities_filtered{type="lab",force=force}) do
    local input=lab.get_inventory(defines.inventory.lab_input)
    if input and input.get_item_count({name=item,quality="normal"})>0 then return lab end
  end
end

local BELTS={["transport-belt"]=true,["underground-belt"]=true,splitter=true}
Feed.ROUTE=300   -- belt pieces a route from the pad is followed at most

-- The way from the pad into a lab: an inserter unloading the pad onto a belt, the belt followed downstream, an
-- inserter lifting from it into a lab. {unloader=, feeder=, lab=} or nil (a pack in a lab and an inserter at the pad
-- are not proof by themselves that one leads to the other).
local function route(surface,force,pad)
  local p=pad.position
  for _,out in ipairs(surface.find_entities_filtered{type="inserter",force=force,
      area={{p.x-6,p.y-6},{p.x+6,p.y+6}}}) do
    local belt=out.pickup_target==pad and out.drop_target
    local seen,queue,n={},{},0
    if belt and BELTS[belt.type] then queue[1]=belt end
    while #queue>0 and n<Feed.ROUTE do
      local b=table.remove(queue,1)
      if not seen[b.unit_number] then
        seen[b.unit_number]=true
        n=n+1
        for _,ins in ipairs(surface.find_entities_filtered{type="inserter",force=force,position=b.position,radius=1.5}) do
          local into=ins.pickup_target==b and ins.drop_target
          if into and into.type=="lab" then return {unloader=out,feeder=ins,lab=into} end
        end
        for _,next_belt in ipairs(b.belt_neighbours.outputs) do queue[#queue+1]=next_belt end
        local under=b.type=="underground-belt" and b.belt_to_ground_type=="input" and b.neighbours
        if under then queue[#queue+1]=under end
      end
    end
  end
end

local function built(surface,force,name,position)
  return surface.find_entities_filtered{name=name,force=force,position=position,limit=1}[1]
end

-- The next job toward the feed (gather or build the pad, then the inserter), or nil and why. Once both stand:
-- npc.space_feed (Orbit.fed), and the pad asks for `packs`. A pad on free ground (no inserter of its own) counts as
-- fed once its packs reach a lab, by Factory's belt.
function Feed.step(npc,acquire,count,packs)
  local c=npc.character
  if not (c and c.valid) then return nil end
  local surface,force=c.surface,npc.force
  local plan=npc.space_feed_plan
  if plan then
    local pad=plan.lab.valid and built(surface,force,PAD,plan.pad)
    local ins=plan.lab.valid and plan.picker and built(surface,force,plan.picker,plan.inserter)
    if pad then
      npc.space_pad=pad
      npc.space_pad_items=packs
      for _,item in ipairs(packs) do Feed.ask(pad,item,Feed.REQUEST) end
    end
    if pad and not plan.picker then   -- (on free ground: the belt is Factory's)
      -- (fed: its packs in a lab, and a way traced from the pad into a lab; its ends kept, so it lapses with either)
      for _,item in ipairs(packs) do
        local way=lab_holding(surface,force,item) and route(surface,force,pad)
        if way then npc.space_feed={pad=pad,unloader=way.unloader,feeder=way.feeder,lab=way.lab}; return nil end
      end
      return nil,"space_feed:belt_awaited"
    end
    if pad and ins then
      -- (joined as planned: it lifts from this pad into this lab)
      if ins.pickup_target~=pad or ins.drop_target~=plan.lab then return nil,"space_feed:not_joined" end
      for _,item in ipairs(packs) do Feed.ask(pad,item,Feed.REQUEST) end
      npc.space_feed={pad=pad,inserter=ins,lab=plan.lab}
      return nil
    end
    -- (its spot taken since, or its lab gone: a new site)
    if not plan.lab.valid or (not pad and not placeable(surface,force,PAD,plan.pad))
        or (plan.picker and not ins
          and not placeable(surface,force,plan.picker,plan.inserter,defines.direction[plan.direction])) then
      plan=nil
    end
  end
  if not plan then
    local home=npc.home or c.position
    local labs=surface.find_entities_filtered{type="lab",force=force}
    table.sort(labs,function(a,b)
      return (a.position.x-home.x)^2+(a.position.y-home.y)^2<(b.position.x-home.x)^2+(b.position.y-home.y)^2
    end)
    for _,lab in ipairs(labs) do
      plan=Feed.site(npc,lab)
      if plan then break end
    end
    if not plan and labs[1] then   -- (no room beside one: free ground near the nearest, for a belt)
      local pad=Feed.free_pad(npc,labs[1])
      plan=pad and {lab=labs[1],pad=pad} or nil
    end
    npc.space_feed_plan=plan
    if not plan then return nil,#labs==0 and "space_feed:no_lab" or "space_feed:no_room" end
  end
  for _,piece in ipairs({{PAD,plan.pad},{plan.picker,plan.inserter,plan.direction}}) do
    local name,position,direction=piece[1],piece[2],piece[3]
    if name and not built(surface,force,name,position) then
      if count(npc,name)<1 then
        local job,why=acquire(npc,name,1,0)
        if job then return job,{upkeep="space_feed",step="gather"} end
        return nil,tostring(why)..":feed_wait:"..name
      end
      return {kind="build",entity_name=name,item=name,position=position,
        direction=direction and defines.direction[direction] or nil},{upkeep="space_feed",step="build"}
    end
  end
end

-- Its landing pad as a source for the site export (export-local): one entry per pack it asks for.
function Feed.export(npc,origin)
  local pad=npc.space_pad
  if not (pad and pad.valid) then return nil end
  local ox,oy=origin and origin.x or 0,origin and origin.y or 0
  local out={}
  for _,item in ipairs(npc.space_pad_items or {}) do
    out[#out+1]={x=pad.position.x-ox,y=pad.position.y-oy,item=item}
  end
  return #out>0 and out or nil
end

return Feed
