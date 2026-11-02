-- Pure prospective starter-site survey. Native quantities and legal footprints
-- nominate a site; the ordinary Factory/Worker still walks, mines and builds it.
local Iron={revision='starter-iron-reserve-v1'}
local function dist(a,b)return math.sqrt((a.x-b.x)^2+(a.y-b.y)^2)end
local function xy(p)return {x=p.x,y=p.y}end
local function clamp(v)return math.max(0,math.min(1,tonumber(v) or .5))end
function Iron.footprint(surface,p)
  local radius=prototypes.entity['burner-mining-drill'].get_mining_drill_radius('normal')
  if not radius or radius<=0 then return nil,'native_mining_radius_unavailable' end
  local amount,positive,mixed=0,0,{}
  for _,e in ipairs(surface.find_entities_filtered{type='resource',force='neutral',
      area={{p.x-radius,p.y-radius},{p.x+radius,p.y+radius}}}) do
    if e.valid and e.minable and e.amount>0 then
      if e.name=='iron-ore' then amount=amount+e.amount;positive=positive+1
      else mixed[e.name]=(mixed[e.name] or 0)+e.amount end
    end
  end
  return {amount=amount,positive_tiles=positive,mixed=mixed,radius=radius,
    area={{p.x-radius,p.y-radius},{p.x+radius,p.y+radius}}}
end
function Iron.select(npc,options)
  options=options or {}
  local c=npc and npc.character
  if not(c and c.valid and npc.home)then return nil,'valid_owned_character_and_home_required' end
  if not npc.force or c.force~=npc.force then return nil,'character_force_mismatch' end
  local range=128;local cap=512
  -- A planning allowance is explicit, not an exact researched-machine bill.
  local target=908;local short=136
  if options.target_reserve~=nil then
    local requested=options.target_reserve
    if type(requested)~='number' or requested~=math.floor(requested) or requested<target or requested>1000000 then
      return nil,'target_reserve_outside_bounded_range'
    end
    target=requested
  end
  local report={revision=Iron.revision,scope='bounded_engine_local_native_survey',observed_tick=game.tick,
    range=range,candidate_cap=cap,target=target,short_fallback_minimum=short,
    target_basis={bootstrap_ore=8,first_growth_planning_allowance=400,production_reserve=500,
      planning_allowance_is_estimate=true},rejected={},eligible_target=0,eligible_short=0}
  report.target_override=options.target_reserve
  local centres,seen={},{}
  for _,e in ipairs(c.surface.find_entities_filtered{name='iron-ore',position=npc.home,radius=range,force='neutral'}) do
    if e.valid and e.minable and e.amount>0 then
      local x,y=math.floor(e.position.x),math.floor(e.position.y)
      for dx=0,1 do for dy=0,1 do
        local p={x=x+dx,y=y+dy};local key=p.x..':'..p.y
        if not seen[key] and dist(p,npc.home)<=range then
          seen[key]=true;centres[#centres+1]=p
        end
      end end
    end
  end
  table.sort(centres,function(a,b)
    local da,db=dist(a,npc.home),dist(b,npc.home)
    return da<db or da==db and(a.x<b.x or a.x==b.x and a.y<b.y)
  end)
  report.discovered_centres=#centres;report.evaluated_centres=math.min(#centres,cap)
  report.candidate_bound_reached=#centres>cap
  local best,fallback
  local richness_bonus=4+8*clamp(npc.profile and npc.profile.resource_conservatism)
  local function better(a,b)
    return not b or a.score<b.score or a.score==b.score and(a.footprint.amount>b.footprint.amount or
      a.footprint.amount==b.footprint.amount and(a.position.x<b.position.x or
        a.position.x==b.position.x and a.position.y<b.position.y))
  end
  local function reject(reason)report.rejected[reason]=(report.rejected[reason] or 0)+1 end
  for i=1,math.min(#centres,cap)do
    local p=centres[i];local free=true
    for id,other in pairs(storage.npcs or {})do
      if id~=npc.id and other.module and dist(other.module,p)<10 then free=false;break end
    end
    if not free then reject('reserved_by_other_module')
    else
      local footprint,reason=Iron.footprint(c.surface,p)
      local furnace={x=p.x,y=p.y-2}
      if not footprint then reject(reason)
      elseif next(footprint.mixed)then reject('mixed_resource_footprint')
      elseif footprint.amount<short then reject('insufficient_short_reserve')
      elseif not c.surface.can_place_entity{name='burner-mining-drill',position=p,
          direction=defines.direction.north,force=npc.force,build_check_type=defines.build_check_type.manual} then reject('drill_not_currently_native_legal')
      elseif not c.surface.can_place_entity{name='stone-furnace',position=furnace,
          force=npc.force,build_check_type=defines.build_check_type.manual}then reject('furnace_port_not_currently_native_legal')
      else
        local d=dist(p,npc.home)
        local a={position=xy(p),furnace_position=furnace,direction=defines.direction.north,
          surface_index=c.surface.index,footprint=footprint,distance=d,
          score=d-richness_bonus*math.min(1,footprint.amount/(4*target)),
          target=target,meets_target=footprint.amount>=target,observed_tick=game.tick,
          neutral_obstacle_clearing_required=false}
        report.eligible_short=report.eligible_short+1
        if better(a,fallback)then fallback=a end
        if a.meets_target then
          report.eligible_target=report.eligible_target+1
          if better(a,best)then best=a end
        end
      end
    end
  end
  if best then best.reason='target_reserve_selected';return best,nil,report end
  if options.allow_short_fallback==true and fallback then
    fallback.reason='disclosed_short_reserve_fallback';return fallback,nil,report
  end
  return nil,fallback and 'no_target_reserve_site_within_bounded_candidates' or 'no_eligible_starter_iron_site',report
end
return Iron
