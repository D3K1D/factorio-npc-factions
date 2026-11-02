-- Physical supplementary mining for exhausted starter iron/copper producers.
-- All state is saved on the NPC. This module never creates inventory or entities.
local Recovery={}
local MAX_ROUTE=8
local RETRY=1800
local NO_ROUTE_RETRY=36000
local MAX_GENERATIONS=4
local MAX_FAILURES=3
local function xy(p) return p and {x=p.x or p[1],y=p.y or p[2]} end
local function same(a,b) return a and b and math.abs(a.x-b.x)<.06 and math.abs(a.y-b.y)<.06 end
local function stock(npc,item)
  return npc.character.get_main_inventory().get_item_count{name=item,quality='normal'}
end
local function held(npc,entity)
  return entity and entity.valid and entity.force==npc.force
      and entity.surface==npc.character.surface
end
local function resolve(npc,spec)
  if not spec or spec.surface_index~=npc.character.surface.index
      or spec.force_name~=npc.force.name then return nil end
  for _,e in ipairs(npc.character.surface.find_entities_filtered{
      name=spec.name,force=npc.force,position=spec.position,radius=.15}) do
    if e.valid and same(e.position,spec.position)
        and (not spec.unit_number or spec.unit_number==e.unit_number) then return e end
  end
end
local function pos(site)
  return site and xy(site.position)
end
local function saved_receiver(npc,key,resolve_site)
  local project=npc.factory_project
  local site=project and project.plan and project.plan.sites and project.plan.sites[key]
  local e=site and resolve_site(npc,key)
  if not (site and e and held(npc,e) and same(e.position,pos(site))) then return nil end
  return e
end
local function resolve_project_site(npc,key)
  local project=npc.factory_project
  local s=project and project.plan and project.plan.sites and project.plan.sites[key]
  if not (s and s.name and s.position and npc.character and npc.character.valid) then
    return nil
  end
  return resolve(npc,{name=s.name,position=xy(s.position),
    unit_number=s.existing_unit_number or s.unit_number,
    surface_index=s.surface_index or npc.character.surface.index,
    force_name=npc.force.name})
end
local function resource_amount(surface,ore,area)
  local count=0
  for _,e in ipairs(surface.find_entities_filtered{name=ore,area=area}) do
    if e.valid and e.amount and e.amount>0 and e.force.name=='neutral' and e.minable then
      count=count+e.amount
    end
  end
  return count
end
local function drill_ore(npc,old,p,ore)
  local a=old.mining_area
  if not a then return 0 end
  local x,y=p.x-old.position.x,p.y-old.position.y
  return resource_amount(npc.character.surface,ore,{
    {a.left_top.x+x,a.left_top.y+y},{a.right_bottom.x+x,a.right_bottom.y+y}})
end
local function nearby(npc,item,center,radius)
  local best,score
  for _,e in ipairs(npc.character.surface.find_entities_filtered{
      position=center,radius=radius,name=item}) do
    if e.valid and e.force.name=='neutral' and e.minable
        and (e.type~='resource' or e.amount>0) then
      local d=(e.position.x-center.x)^2+(e.position.y-center.y)^2
      if not best or d<score or d==score and (e.position.x<best.position.x
          or e.position.x==best.position.x and e.position.y<best.position.y) then
        best,score=e,d
      end
    end
  end
  return best
end
local function no_ore(drill)
  return drill and drill.valid and drill.status==defines.entity_status.no_minable_resources
end
local function site(name,item,p,d,npc)
  return {name=name,item=item,position={x=p.x,y=p.y},direction=d,
    surface_index=npc.character.surface.index,force_name=npc.force.name}
end
local function bound(spec)
  local b=prototypes.entity[spec.name].collision_box
  return {x1=spec.position.x+b.left_top.x,y1=spec.position.y+b.left_top.y,
    x2=spec.position.x+b.right_bottom.x,y2=spec.position.y+b.right_bottom.y}
end
local function overlaps(a,b)
  local x=math.min(a.x2,b.x2)-math.max(a.x1,b.x1)
  local y=math.min(a.y2,b.y2)-math.max(a.y1,b.y1)
  return x>.02 and y>.02
end
local function placement(npc,s)
  local surface=npc.character.surface
  if surface.can_place_entity{name=s.name,position=s.position,direction=s.direction,
      force=npc.force} then return true end
  if not surface.can_place_entity{name=s.name,position=s.position,direction=s.direction,
      force=npc.force,build_check_type=defines.build_check_type.blueprint_ghost,forced=true} then
    return false,'native_terrain_blocked'
  end
  local b=bound(s);local blockers={}
  for _,e in ipairs(surface.find_entities_filtered{area={{b.x1,b.y1},{b.x2,b.y2}}}) do
    local eb=e.bounding_box
    if e.valid and e.type~='resource' and eb
        and overlaps(b,{x1=eb.left_top.x,y1=eb.left_top.y,x2=eb.right_bottom.x,y2=eb.right_bottom.y})
        and e~=npc.character then
      if e.force.name~='neutral' or not e.minable
          or (e.type~='tree' and e.type~='simple-entity') then
        return false,'native_nonclearable_entity'
      end
      local props=e.prototype.mineable_properties
      local item
      if props and props.minable and not props.required_fluid then
        for _,product in ipairs(props.products or {}) do
          if product.type=='item' and (product.amount or product.amount_min or 0)>=1
              and (not product.probability or product.probability==1) then
            item=product.name;break
          end
        end
      end
      if not item then return false,'native_clearance_yield_unverified' end
      blockers[#blockers+1]={entity=e,item=item}
    end
  end
  table.sort(blockers,function(a,c)
    return a.entity.position.x<c.entity.position.x
      or a.entity.position.x==c.entity.position.x and a.entity.position.y<c.entity.position.y
  end)
  return true,nil,blockers[1]
end
local function legal(npc,sites)
  local surface=npc.character.surface
  local reservations=npc.factory_project.plan.sites
  for _,s in ipairs(sites) do
    if s.reuse then
      if not s.unit_number or not resolve(npc,s) then
        return false,'reused_route_identity_missing'
      end
    else
      local ok,why=placement(npc,s)
      if not ok then return false,why end
    end
    local a=bound(s)
    for _,other in ipairs(sites) do
      if other~=s and overlaps(a,bound(other)) then return false,'recovery_sites_overlap' end
    end
    if not s.reuse then
      for _,other in pairs(reservations) do
        if other.position and other.name and overlaps(a,bound(other)) then
          return false,'reserved_project_site_overlap'
        end
      end
    end
  end
  return true
end
local function old_scope(npc,kind,resolve_site)
  if not npc.character or not npc.character.valid or not npc.factory_project
      or not npc.factory_project.plan or not npc.force
      or npc.character.force~=npc.force then return nil,'actor_scope_revoked' end
  local furnace=saved_receiver(npc,kind=='iron' and 'iron_furnace' or 'copper_furnace',resolve_site)
  local old=kind=='iron' and npc.miner or saved_receiver(npc,'copper_drill',resolve_site)
  if not (held(npc,furnace) and held(npc,old) and old.name=='burner-mining-drill'
      and old.direction==defines.direction.north) then return nil,'old_mining_scope_revoked' end
  if kind=='iron' then
    if not same(furnace.position,{x=old.position.x,y=old.position.y-2}) then
      return nil,'core_furnace_port_mismatch'
    end
  else
    local belt=saved_receiver(npc,'copper_ore_belt1',resolve_site)
    local drop=old.drop_position
    if not (held(npc,belt) and belt.name=='transport-belt'
        and belt.direction==defines.direction.north and drop
        and math.floor(drop.x)==math.floor(belt.position.x)
        and math.floor(drop.y)==math.floor(belt.position.y)
        and old.drop_target==belt) then return nil,'copper_conveyor_port_mismatch' end
    return {old=old,furnace=furnace,belt=belt}
  end
  return {old=old,furnace=furnace}
end
local function useful(npc,kind,scope)
  if not no_ore(scope.old) then return false end
  local plate=kind=='iron' and 'iron-plate' or 'copper-plate'
  local input=scope.furnace.get_inventory(defines.inventory.crafter_input)
  local output=scope.furnace.get_output_inventory()
  if input and input.get_item_count(kind=='iron' and 'iron-ore' or 'copper-ore')>=4 then return false end
  if output and output.get_item_count(plate)>=8 then return false end
  -- Carried plates can fund hardware but cannot feed this native ore port.
  -- A depleted producer with a starved receiver still needs its paid route.
  return true
end
local function copper_sites(npc,scope,x)
  local old,port=scope.old,scope.belt
  local sign=x<old.position.x and 1 or -1
  local first=x-.5
  local stop=port.position.x-sign
  local s={site('burner-mining-drill','burner-mining-drill',
    {x=x,y=old.position.y},defines.direction.north,npc)}
  for bx=first,stop,sign do
    s[#s+1]=site('transport-belt','transport-belt',
      {x=bx,y=port.position.y},sign==1 and defines.direction.east or defines.direction.west,npc)
  end
  return s
end
local function iron_sites(npc,scope,x,south)
  local old,f=scope.old,scope.furnace
  local east=x>old.position.x
  local y=f.position.y-.5
  local s={site('burner-mining-drill','burner-mining-drill',
    {x=x,y=old.position.y},defines.direction.north,npc)}
  local sx=x-.5
  local target=f.position.x+(east and 2.5 or -2.5)
  local step=sx<target and 1 or -1
  for bx=sx,target,step do
    s[#s+1]=site('transport-belt','transport-belt',
      {x=bx,y=f.position.y+.5},
      bx==target and not south and defines.direction.north
        or (east and defines.direction.west or defines.direction.east),npc)
  end
  if not south then
    s[#s+1]=site('transport-belt','transport-belt',
      {x=target,y=y},defines.direction.north,npc)
  else y=f.position.y+.5 end
  s[#s+1]=site('burner-inserter','burner-inserter',
    {x=f.position.x+(east and 1.5 or -1.5),y=y},
    east and defines.direction.east or defines.direction.west,npc)
  return s
end
local function copy_site(s,reuse)
  return {name=s.name,item=s.item,position=xy(s.position),direction=s.direction,
    surface_index=s.surface_index,force_name=s.force_name,
    unit_number=s.unit_number,reuse=reuse or nil}
end
local function extension_sites(kind,proposed,previous)
  if not previous or not previous.sites or not previous.sites[2] then return proposed end
  local anchor=previous.sites[2]
  local prior_drill=previous.sites[1]
  if not (anchor.unit_number and prior_drill and prior_drill.unit_number) then return nil end
  for i=2,#proposed do
    if proposed[i].name==anchor.name and same(proposed[i].position,anchor.position) then
      local sites={proposed[1]}
      for j=2,i-1 do sites[#sites+1]=proposed[j] end
      for j=2,#previous.sites do
        sites[#sites+1]=copy_site(previous.sites[j],true)
      end
      return sites
    end
  end
end
local function select_plan(npc,kind,scope,tick,previous)
  local ore=kind=='iron' and 'iron-ore' or 'copper-ore'
  local old=scope.old
  local candidates={}
  for _,dx in ipairs({-2,2,-4,4,-6,6,-8,8}) do
    -- A north-facing drill drops to its west. An iron drill two tiles east
    -- drops into the inserter's lane and cannot feed the east-side pickup.
    if (kind~='iron' or dx~=2)
        and (not previous or dx*(previous.sites[1].position.x-old.position.x)>0
          and math.abs(dx)>math.abs(previous.sites[1].position.x-old.position.x)) then
      local x=old.position.x+dx
      local amount=drill_ore(npc,old,{x=x,y=old.position.y},ore)
      if amount>=(kind=='iron' and 32 or 16) then
        for _,variant in ipairs(kind=='iron' and {'north','south'} or {'north'}) do
          local proposed=kind=='iron' and iron_sites(npc,scope,x,variant=='south') or copper_sites(npc,scope,x)
          local sites=extension_sites(kind,proposed,previous)
          if sites and #sites<=MAX_ROUTE+2 then
            local ok=legal(npc,sites)
            if ok then candidates[#candidates+1]={dx=dx,amount=amount,sites=sites,variant=variant} end
          end
        end
      end
    end
  end
  table.sort(candidates,function(a,b)
    if math.abs(a.dx)~=math.abs(b.dx) then return math.abs(a.dx)<math.abs(b.dx) end
    if a.amount~=b.amount then return a.amount>b.amount end
    if a.dx~=b.dx then return a.dx<b.dx end
    return a.variant<b.variant
  end)
  local chosen=candidates[1]
  if not chosen then return nil,'no_bounded_native_recovery_route' end
  local funding=previous and previous.funding_site
  if funding and not resolve(npc,funding) then funding=nil end
  return {kind=kind,ore=ore,old_unit=old.unit_number,
    receiver_unit=scope.furnace.unit_number,
    port_unit=scope.belt and scope.belt.unit_number,
    force_name=npc.force.name,surface_index=npc.character.surface.index,
    started_tick=tick,generation=previous and (previous.generation or 1)+1 or 1,
    previous_generation=previous and (previous.generation or 1) or nil,
    anchor_unit=previous and previous.sites[2].unit_number or nil,
    funding_site=funding and copy_site(funding,true) or nil,
    sites=chosen.sites,observed_ore=chosen.amount,
    receipts={},issued_sequence=0,status='building',retry_at=0,
    failure_count=0}
end
local function plan_scope(npc,plan,scope)
  return plan.force_name==npc.force.name
      and plan.surface_index==npc.character.surface.index
      and scope.old.unit_number==plan.old_unit
      and scope.furnace.unit_number==plan.receiver_unit
      and (not plan.port_unit or scope.belt and scope.belt.unit_number==plan.port_unit)
end
local function issue(plan,job,purpose,tick,site_index)
  plan.issued_sequence=plan.issued_sequence+1
  plan.issued={sequence=plan.issued_sequence,kind=job.kind,item=job.item,
    target_unit=job.target and job.target.unit_number,
    target_name=job.target and job.target.name,
    target_position=job.target and xy(job.target.position),
    position=job.position and xy(job.position),site_index=site_index,
    purpose=purpose,tick=tick}
  job.upkeep_source='mining_recovery'
  return job
end
local function fuel(npc,target,plan,tick)
  if not held(npc,target) then return nil,'owned_fuel_target_missing' end
  local inventory=target.get_fuel_inventory()
  if not inventory or inventory.get_item_count('coal')>=1 then return nil end
  if stock(npc,'coal')<4 then
    local e=nearby(npc,'coal',npc.character.position,128)
    if not e then return nil,'no_local_coal' end
    return issue(plan,{kind='mine',item='coal',count=12,search_radius=128},'mine_fuel',tick)
  end
  return issue(plan,{kind='transfer',target=target,item='coal',count=4,
    mode='deposit',inventory=defines.inventory.fuel},'fuel_owned_machine',tick)
end
local function funding_site(npc,plan,scope)
  local f=scope.furnace.position
  for _,r in ipairs({4,6,8,10}) do
    for _,offset in ipairs({{r,0},{-r,0},{0,r},{0,-r},
        {r,r},{-r,r},{r,-r},{-r,-r}}) do
      local s=site('stone-furnace','stone-furnace',
        {x=f.x+offset[1],y=f.y+offset[2]},defines.direction.north,npc)
      if npc.character.surface.can_place_entity{name=s.name,position=s.position,
          direction=s.direction,force=npc.force} then
        local free=true
        local box=bound(s)
        for _,other in ipairs(plan.sites) do
          if overlaps(box,bound(other)) then free=false;break end
        end
        if free then
          for _,other in pairs(npc.factory_project.plan.sites) do
            if other.position and other.name and overlaps(box,bound(other)) then
              free=false;break
            end
          end
        end
        if free then return s end
      end
    end
  end
end
local function plates(npc,scope,plan,tick)
  local output=scope.furnace.get_output_inventory()
  local available=output and output.get_item_count('iron-plate') or 0
  if available>0 then
    return issue(plan,{kind='transfer',target=scope.furnace,item='iron-plate',
      count=math.min(available,24-stock(npc,'iron-plate')),
      mode='withdraw',inventory=defines.inventory.crafter_output},
      'withdraw_earned_plates',tick)
  end
  if not plan.funding_site then
    plan.funding_site=funding_site(npc,plan,scope)
    if not plan.funding_site then return nil,'no_legal_funding_furnace_site' end
  end
  local spec=plan.funding_site
  local furnace=resolve(npc,spec)
  if not furnace then
    if spec.unit_number then return nil,'funding_furnace_destroyed' end
    if not npc.character.surface.can_place_entity{name=spec.name,
        position=spec.position,direction=spec.direction,force=npc.force} then
      return nil,'funding_furnace_site_blocked'
    end
    if stock(npc,'stone-furnace')<1 then
      if npc.character.get_craftable_count('stone-furnace')>=1 then
        return issue(plan,{kind='craft',recipe='stone-furnace',
          item='stone-furnace',count=1},'craft_funding_furnace',tick,'funding')
      end
      local e=nearby(npc,'stone',npc.character.position,128)
      if not e then return nil,'no_local_funding_stone' end
      return issue(plan,{kind='mine',item='stone',count=5,
        search_radius=128},'mine_funding_stone',tick,'funding')
    end
    return issue(plan,{kind='build',entity_name='stone-furnace',
      item='stone-furnace',position=spec.position,direction=spec.direction},
      'build_funding_furnace',tick,'funding')
  end
  if not spec.unit_number then return nil,'unverified_funding_furnace' end
  output=furnace.get_output_inventory()
  available=output and output.get_item_count('iron-plate') or 0
  if available>=4 then
    return issue(plan,{kind='transfer',target=furnace,item='iron-plate',
      count=math.min(available,24-stock(npc,'iron-plate')),
      mode='withdraw',inventory=defines.inventory.crafter_output},
      'withdraw_funding_plates',tick)
  end
  local need=furnace.get_fuel_inventory().get_item_count('coal')<1
  if need then return fuel(npc,furnace,plan,tick) end
  local input=furnace.get_inventory(defines.inventory.crafter_input)
  if input and input.get_item_count('iron-ore')>=4 then return nil,'await_native_smelting' end
  if stock(npc,'iron-ore')<8 then
    local e=nearby(npc,'iron-ore',npc.character.position,128)
    if not e then return nil,'no_local_iron_ore' end
    return issue(plan,{kind='mine',item='iron-ore',count=8,search_radius=128},
      'mine_recovery_iron',tick)
  end
  return issue(plan,{kind='transfer',target=furnace,item='iron-ore',count=8,
    mode='deposit',inventory=defines.inventory.crafter_input},
    'feed_owned_iron_furnace',tick)
end
local function next_hardware(npc,plan,scope,tick)
  local surface=npc.character.surface
  for i,spec in ipairs(plan.sites) do
    local e=resolve(npc,spec)
    if not e then
      if spec.reuse then return nil,'reused_route_identity_missing' end
      if spec.unit_number then return nil,'built_recovery_site_missing' end
      local placeable,blocked,clearing=placement(npc,spec)
      if not placeable then return nil,blocked end
      if clearing then
        return issue(plan,{kind='mine',target=clearing.entity,item=clearing.item,count=1},
          'clear_paid_route_site',tick,i)
      end
      if i==1 and drill_ore(npc,scope.old,spec.position,plan.ore)<=0 then
        return nil,'observed_recovery_ore_depleted'
      end
      if stock(npc,spec.item)<1 then
        local recipe=npc.force.recipes[spec.item]
        if not recipe or not recipe.enabled then return nil,'recovery_hardware_recipe_locked' end
        if npc.character.get_craftable_count(spec.item)>=1 then
          return issue(plan,{kind='craft',recipe=spec.item,count=1,item=spec.item},
            'craft_funded_hardware',tick,i)
        end
        if spec.item=='burner-mining-drill' and stock(npc,'stone-furnace')<1
            and stock(npc,'stone')<5 then
          local e=nearby(npc,'stone',npc.character.position,128)
          if not e then return nil,'no_local_stone' end
          return issue(plan,{kind='mine',item='stone',
            count=5-stock(npc,'stone'),search_radius=128},'mine_drill_stone',tick,i)
        end
        if stock(npc,'iron-plate')<24 then return plates(npc,scope,plan,tick) end
        return nil,'hardware_ingredients_unavailable'
      end
      return issue(plan,{kind='build',entity_name=spec.name,item=spec.item,
        position=spec.position,direction=spec.direction},'build_paid_hardware',tick,i)
    end
    if not spec.unit_number then return nil,'unverified_recovery_site' end
    if e.direction~=spec.direction and (spec.name=='transport-belt'
        or spec.name=='burner-inserter') then
      if spec.reuse then return nil,'reused_route_direction_changed' end
      return issue(plan,{kind='configure_direction',target=e,direction=spec.direction},
        'configure_native_route',tick,i)
    end
  end
  return nil
end
local function ready(npc,plan,scope)
  local drill=resolve(npc,plan.sites[1])
  local belt=resolve(npc,plan.sites[2])
  if not (drill and belt and drill.mining_target
      and drill.mining_target.valid and drill.mining_target.name==plan.ore
      and drill.mining_target.amount>0) then return false,'native_drill_not_mining_ore' end
  local drop=drill.drop_position
  if not (drop and math.floor(drop.x)==math.floor(belt.position.x)
      and math.floor(drop.y)==math.floor(belt.position.y)
      and drill.drop_target==belt) then return false,'native_drop_port_mismatch' end
  if plan.kind=='iron' then
    local inserter=resolve(npc,plan.sites[#plan.sites])
    local target_belt=resolve(npc,plan.sites[#plan.sites-1])
    if not (inserter and target_belt and inserter.pickup_position
        and math.floor(inserter.pickup_position.x)==math.floor(target_belt.position.x)
        and math.floor(inserter.pickup_position.y)==math.floor(target_belt.position.y)
        and inserter.drop_position
        and math.abs(inserter.drop_position.x-scope.furnace.position.x)<1.1
        and math.abs(inserter.drop_position.y-scope.furnace.position.y)<1.1) then
      return false,'native_furnace_inserter_port_mismatch'
    end
  else
    local tail=resolve(npc,plan.sites[#plan.sites])
    if not (tail and math.abs(tail.position.x-scope.belt.position.x)==1
        and tail.position.y==scope.belt.position.y) then
      return false,'native_copper_conveyor_endpoint_mismatch'
    end
  end
  return true
end
local function retry(plan,reason,tick,failed_job)
  plan.reason=reason
  if failed_job then
    plan.failure_count=(plan.failure_count or 0)+1
    if plan.failure_count>=MAX_FAILURES then
      plan.status='blocked';plan.blocked_tick=tick
      return
    end
  end
  local factor=failed_job and 2^math.min((plan.failure_count or 1)-1,4) or 1
  plan.retry_at=tick+RETRY*factor
end
local function depleted(npc,plan)
  local drill=plan.sites and plan.sites[1] and resolve(npc,plan.sites[1])
  return drill and drill_ore(npc,drill,drill.position,plan.ore)<=0
end
local function archive(all,kind,plan,tick)
  all.history=all.history or {}
  plan.status='depleted';plan.depleted_tick=plan.depleted_tick or tick
  all.history[#all.history+1]=plan
  if #all.history>MAX_GENERATIONS*2 then table.remove(all.history,1) end
  all[kind]=nil
end
function Recovery.next(npc,condition,tick,resolve_site)
  if not (npc and npc.character and npc.character.valid and npc.factory_project
      and npc.factory_project.plan and type(resolve_site)=='function') then return nil end
  local all=npc.mining_recovery or {}
  npc.mining_recovery=all
  if all.issued then return nil,'mining_recovery_job_pending' end
  for _,kind in ipairs({'iron','copper'}) do
    local scope,reason=old_scope(npc,kind,resolve_site)
    local plan=all[kind]
    if plan and scope and not plan_scope(npc,plan,scope) then
      plan.status='blocked';plan.reason='recovery_scope_revoked'
      plan.blocked_tick=plan.blocked_tick or tick
    end
    if plan and plan.status=='operational' and scope and depleted(npc,plan) then
      plan.status='depleted';plan.reason='native_patch_exhausted'
      plan.depleted_tick=tick;plan.retry_at=tick
    end
    local needed=scope and useful(npc,kind,scope)
    if plan and plan.status=='complete' and needed then
      plan.status='operational';plan.completed_tick=nil
    end
    if scope and needed and (not plan or plan.status=='depleted') then
      local previous=plan
      local generation=previous and (previous.generation or 1) or 0
      local cooling=all.cooldowns and all.cooldowns[kind]
      if generation>=MAX_GENERATIONS then
        previous.reason='bounded_route_generations_exhausted'
        previous.status='blocked';previous.blocked_tick=tick
      elseif tick>=(previous and previous.retry_at or cooling and cooling.retry_at or 0) then
        local selected,why=select_plan(npc,kind,scope,tick,previous)
        if selected then
          if previous then archive(all,kind,previous,tick) end
          all[kind]=selected;plan=selected
          if all.cooldowns then all.cooldowns[kind]=nil end
        else
          if previous then retry(previous,why,tick,false)
          else
            all.cooldowns=all.cooldowns or {}
            all.cooldowns[kind]={reason=why,retry_at=tick+NO_ROUTE_RETRY}
          end
        end
      end
    elseif plan and plan.status=='operational' and not needed and scope
        and not no_ore(scope.old) then
      plan.status='complete';plan.completed_tick=tick
    end
    if plan and scope and (plan.status=='building'
        or plan.status=='operational' and needed) and tick>=(plan.retry_at or 0) then
      local job,why=next_hardware(npc,plan,scope,tick)
      if job then all.issued={kind=kind,sequence=plan.issued.sequence};return job end
      if why then retry(plan,why,tick,false);return nil,why end
      local drill=resolve(npc,plan.sites[1])
      local refuel
      refuel,why=fuel(npc,drill,plan,tick)
      if refuel then all.issued={kind=kind,sequence=plan.issued.sequence};return refuel end
      if why then retry(plan,why,tick,false);return nil,why end
      if plan.kind=='iron' then
        local inserter=resolve(npc,plan.sites[#plan.sites])
        refuel,why=fuel(npc,inserter,plan,tick)
        if refuel then all.issued={kind=kind,sequence=plan.issued.sequence};return refuel end
        if why then retry(plan,why,tick,false);return nil,why end
      end
      local ok,why=ready(npc,plan,scope)
      if not ok then retry(plan,why,tick,false);return nil,why end
      plan.status='operational';plan.reason=nil
      plan.native_ready_tick=plan.native_ready_tick or tick
      plan.failure_count=0
    end
    if kind=='iron' and plan and plan.status=='building' then
      return nil,plan.reason
    end
  end
end
-- Read-only eligibility for an existing paid producer. Never selects new ore
-- or changes route/depletion state. A reserve is always retained for construction.
function Recovery.fuel_preview(npc,kind,reserve)
  if not(npc and npc.character and npc.character.valid and npc.force)then return nil,'fuel_actor_scope_invalid'end
  if kind~='iron'and kind~='copper'then return nil,'fuel_kind_invalid'end
  if type(reserve)~='number'or reserve~=reserve or reserve~=math.floor(reserve)or reserve<4 or reserve>10000 then
    return nil,'fuel_construction_reserve_invalid'end
  local all=npc.mining_recovery;local plan=all and all[kind]
  if not(plan and plan.status=='operational')then return nil,'fuel_operational_paid_plan_required'end
  if all.issued or plan.issued then return nil,'fuel_recovery_job_pending'end
  if npc.current_job or npc.pending_job or(npc.jobs and #npc.jobs>0)
      or(npc.factory_project and npc.factory_project.pending_job)then return nil,'fuel_worker_busy'end
  if npc.craft_supply_recovery and npc.craft_supply_recovery.quarantined then return nil,'fuel_receipt_quarantined'end
  if plan.force_name~=npc.force.name or plan.surface_index~=npc.character.surface.index then return nil,'fuel_saved_scope_revoked'end
  local scope=old_scope(npc,kind,resolve_project_site)
  if not scope or not plan_scope(npc,plan,scope)then return nil,'fuel_receiver_scope_revoked'end
  local spec=plan.sites and plan.sites[1];local drill=spec and resolve(npc,spec)
  if not(spec and spec.unit_number and held(npc,drill)and drill.type=='mining-drill')then return nil,'fuel_exact_producer_missing'end
  if drill.direction~=spec.direction then return nil,'fuel_producer_direction_changed'end
  local paid
  for _,saved in ipairs(plan.receipts or{})do
    local actual=npc.receipts and npc.receipts[saved.job_id]
    if saved.purpose=='build_paid_hardware'and saved.site_index==1 and actual
        and saved.id==actual.id and saved.job_id==actual.job_id and saved.kind=='build'and actual.kind=='build'
        and saved.status=='complete'and actual.status=='complete'and saved.item==spec.item and actual.item==spec.item
        and saved.count==1 and actual.count==1 and saved.tick==actual.tick
        and saved.consumed and saved.consumed[spec.item]==1
        and actual.consumed and actual.consumed[spec.item]==1 and actual.entity==drill then paid=true;break end
  end
  if not paid then return nil,'fuel_paid_producer_receipt_required'end
  local ore=kind=='iron'and'iron-ore'or'copper-ore'
  if not drill.mining_area or resource_amount(drill.surface,ore,drill.mining_area)<=0 then return nil,'fuel_native_ore_exhausted'end
  local inventory=drill.get_fuel_inventory()
  if not inventory or inventory.get_item_count('coal')>=1 then return nil,'fuel_not_required'end
  if stock(npc,'coal')<reserve+4 then return nil,'fuel_construction_reserve_held'end
  local project=npc.factory_project;local step=project and project.plan and project.plan.steps and project.plan.steps[project.index]
  if step and step.kind=='transfer'and step.mode=='deposit'and step.item=='coal'
      and stock(npc,'coal')-4<(step.count or 1)then return nil,'fuel_current_step_reserve_held'end
  if step and step.kind=='craft'then
    local recipe=prototypes.recipe[step.recipe or step.item or'']
    for _,ingredient in ipairs(recipe and recipe.ingredients or{})do
      if ingredient.name=='coal'and ingredient.type=='item'
          and stock(npc,'coal')-4<ingredient.amount*(step.count or 1)then
        return nil,'fuel_current_step_reserve_held'
      end
    end
  end
  return{kind='transfer',target=drill,item='coal',count=4,mode='deposit',inventory=defines.inventory.fuel,
    upkeep_source='mining_recovery'},nil
end
function Recovery.fuel_maintenance(npc,kind,tick,reserve)
  local job,why=Recovery.fuel_preview(npc,kind,reserve)
  if not job then return nil,why end
  local all=npc.mining_recovery;local plan=all[kind]
  issue(plan,job,'fuel_existing_paid_producer',tick)
  all.issued={kind=kind,sequence=plan.issued.sequence}
  return job
end
function Recovery.bind(npc,job_id)
  local all=npc.mining_recovery
  local pending=all and all.issued
  local plan=pending and all[pending.kind]
  if not (plan and plan.issued and plan.issued.sequence==pending.sequence
      and not plan.issued.job_id and type(job_id)=='string') then return false end
  plan.issued.job_id=job_id;pending.job_id=job_id
  return true
end
function Recovery.reject_unbound(npc,reason)
  local all=npc and npc.mining_recovery
  local pending=all and all.issued
  local plan=pending and all[pending.kind]
  if not (plan and plan.issued and not plan.issued.job_id
      and plan.issued.sequence==pending.sequence) then return false end
  retry(plan,reason or 'recovery_enqueue_rejected',game.tick,true)
  plan.issued=nil;all.issued=nil
  return true
end
function Recovery.on_receipt(npc,receipt)
  local all=npc.mining_recovery
  local pending=all and all.issued
  local plan=pending and all[pending.kind]
  local issued=plan and plan.issued
  if not (issued and issued.job_id and issued.job_id==receipt.job_id
      and pending.job_id==receipt.job_id and issued.kind==receipt.kind) then return false end
  local scope=old_scope(npc,pending.kind,resolve_project_site)
  if not scope or not plan_scope(npc,plan,scope) then
    retry(plan,'recovery_scope_revoked_during_job',game.tick,true)
    plan.status='blocked';plan.blocked_tick=game.tick
  elseif issued.purpose=='fuel_existing_paid_producer'and(receipt.status~='complete'
      or receipt.kind~='transfer'or receipt.item~='coal'or receipt.count~=4
      or not npc.receipts or npc.receipts[receipt.job_id]~=receipt
      or receipt.target_unit_number~=issued.target_unit
      or not receipt.consumed or receipt.consumed.coal~=4
      or not receipt.produced or receipt.produced.coal~=4)then
    retry(plan,'recovery_fuel_receipt_mismatch',game.tick,true)
  elseif issued.purpose=='clear_paid_route_site' then
    local gone=true
    for _,e in ipairs(npc.character.surface.find_entities_filtered{name=issued.target_name,
        position=issued.target_position,radius=.15}) do
      if e.valid and e.unit_number==issued.target_unit then gone=false end
    end
    if receipt.status=='complete' and receipt.kind=='mine' and receipt.item==issued.item
        and type(receipt.count)=='number' and receipt.count>0 and gone then
      plan.failure_count=0;plan.reason=nil
    else retry(plan,'recovery_clearance_receipt_mismatch',game.tick,true) end
  elseif issued.target_unit and issued.target_unit~=receipt.target_unit_number then
    retry(plan,'recovery_receipt_target_mismatch',game.tick,true)
  elseif issued.item and issued.item~=receipt.item then
    retry(plan,'recovery_receipt_item_mismatch',game.tick,true)
  elseif receipt.status=='complete' and (receipt.kind=='mine'
      or receipt.kind=='transfer' or receipt.kind=='craft')
      and (type(receipt.count)~='number' or receipt.count<=0) then
    retry(plan,'recovery_receipt_no_physical_quantity',game.tick,true)
  elseif receipt.status=='complete' and issued.site_index
      and (issued.purpose=='build_paid_hardware'
          or issued.purpose=='build_funding_furnace') then
    local spec=issued.site_index=='funding' and plan.funding_site
      or plan.sites[issued.site_index]
    local e=receipt.entity
    if held(npc,e) and e.name==spec.name and same(e.position,spec.position)
        and receipt.count==1 and receipt.consumed
        and (receipt.consumed[spec.item] or 0)>=1 then
      spec.unit_number=e.unit_number
      plan.failure_count=0;plan.reason=nil
    else retry(plan,'recovery_build_receipt_mismatch',game.tick,true) end
  elseif receipt.status~='complete' then
    retry(plan,receipt.reason or 'recovery_worker_failed',game.tick,true)
  else
    plan.failure_count=0;plan.reason=nil
  end
  local ledger=plan.receipts
  if issued.purpose=='fuel_existing_paid_producer'then
    plan.fuel_receipts=plan.fuel_receipts or{};ledger=plan.fuel_receipts
  end
  ledger[#ledger+1]={id=receipt.id,job_id=receipt.job_id,
    kind=receipt.kind,item=receipt.item,status=receipt.status,reason=receipt.reason,
    count=receipt.count,produced=receipt.produced,consumed=receipt.consumed,
    target_unit=issued.target_unit,target_name=issued.target_name,
    target_position=issued.target_position,
    purpose=issued.purpose,site_index=issued.site_index,tick=receipt.tick}
  local limit=issued.purpose=='fuel_existing_paid_producer'and 8 or 64
  if #ledger>limit then table.remove(ledger,1) end
  plan.issued=nil;all.issued=nil
  return true
end
function Recovery.observe(npc)
  local all=npc and npc.mining_recovery or {}
  local result={cooldowns=all.cooldowns}
  result.history={}
  for _,p in ipairs(all.history or {}) do
    result.history[#result.history+1]={kind=p.kind,generation=p.generation or 1,
      status=p.status,reason=p.reason,started_tick=p.started_tick,
      depleted_tick=p.depleted_tick,old_unit=p.old_unit,
      receiver_unit=p.receiver_unit,anchor_unit=p.anchor_unit,
      sites=p.sites,receipts=p.receipts}
  end
  for _,kind in ipairs({'iron','copper'}) do
    local p=all[kind]
    if p then result[kind]={status=p.status,reason=p.reason,started_tick=p.started_tick,
      native_ready_tick=p.native_ready_tick,old_unit=p.old_unit,
      receiver_unit=p.receiver_unit,port_unit=p.port_unit,
      generation=p.generation or 1,previous_generation=p.previous_generation,
      anchor_unit=p.anchor_unit,failure_count=p.failure_count,
      retry_at=p.retry_at,depleted_tick=p.depleted_tick,
      sites=p.sites,funding_site=p.funding_site,issued=p.issued,fuel_receipts=p.fuel_receipts,
      receipts=p.receipts} end
  end
  return result
end
return Recovery
