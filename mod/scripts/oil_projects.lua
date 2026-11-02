-- Native oil/chemistry with disclosed manual component logistics. All writes
-- are ordinary Worker jobs; no items, fluids, energy or research are granted.
local Oil={revision="native-oil-blue-priority-paid-alternate-v3"}
local Advanced=require("scripts.advanced_projects")
local Recipes=require("scripts.recipes")
local Capacity=require("scripts.production_capacity")
local ROOTS={['iron-plate']=true,['copper-plate']=true,['steel-plate']=true,['stone-brick']=true,
  ['plastic-bar']=true,sulfur=true,stone=true,wood=true,coal=true}
local function copy(v) if type(v)~="table" then return v end local t={} for k,x in pairs(v) do t[k]=copy(x) end return t end
local function carried(npc,item) return npc.character.get_main_inventory().get_item_count{name=item,quality="normal"} end
local function resolve(npc,key)
  local s=npc.factory_project and npc.factory_project.plan.sites[key];if not s then return nil end
  for _,e in pairs(npc.character.surface.find_entities_filtered{position=s.position,radius=.1,name=s.name,force=npc.force}) do
    if e.valid then return e end
  end
end
local function add(plan,step) plan.steps[#plan.steps+1]=step end
local function wait(plan,c) add(plan,{kind="wait",condition=c}) end
local function footprint(spec)
  local b=prototypes.entity[spec.name].collision_box
  local a,z=b.left_top or b[1],b.right_bottom or b[2]
  local turns=({[defines.direction.north]=0,[defines.direction.east]=1,[defines.direction.south]=2,[defines.direction.west]=3})[spec.direction]or 0
  local x1,y1,x2,y2=math.huge,math.huge,-math.huge,-math.huge
  for _,p in ipairs({{a.x or a[1],a.y or a[2]},{a.x or a[1],z.y or z[2]},
      {z.x or z[1],a.y or a[2]},{z.x or z[1],z.y or z[2]}})do
    local x,y=p[1],p[2];for _=1,turns do x,y=-y,x end
    x1,y1,x2,y2=math.min(x1,x),math.min(y1,y),math.max(x2,x),math.max(y2,y)
  end
  return {x1=spec.position.x+x1,y1=spec.position.y+y1,x2=spec.position.x+x2,y2=spec.position.y+y2}
end
local function paid_clearance(npc,spec)
  local surface=npc.character.surface;local b=footprint(spec)
  if not surface.can_place_entity{name=spec.name,position=spec.position,direction=spec.direction,force=npc.force,
      build_check_type=defines.build_check_type.blueprint_ghost,forced=true}then return nil end
  if surface.count_tiles_filtered{area={{b.x1,b.y1},{b.x2,b.y2}},collision_mask='water_tile'}>0   -- (a mod's water too)
      or surface.count_tiles_filtered{area={{b.x1,b.y1},{b.x2,b.y2}},name={'lava'}}>0 then return nil end
  local blocks={}
  for _,e in ipairs(surface.find_entities_filtered{area={{b.x1,b.y1},{b.x2,b.y2}}})do local eb=e.bounding_box
    if e.type~='resource'and eb and b.x1<eb.right_bottom.x and eb.left_top.x<b.x2 and b.y1<eb.right_bottom.y and eb.left_top.y<b.y2 then
      if e.force.name~='neutral'or not e.minable or(e.type~='tree'and e.type~='simple-entity')then return nil end
      local props=e.prototype.mineable_properties;local item
      if not props or not props.minable or props.required_fluid then return nil end
      for _,product in ipairs(props.products or{})do if product.type=='item'and(product.amount or product.amount_min or 0)>=1
          and(not product.probability or product.probability==1)then item=product.name;break end end
      if not item then return nil end
      blocks[#blocks+1]={name=e.name,position=copy(e.position),unit_number=e.unit_number,item=item,surface_index=surface.index}
    end
  end
  if #blocks==0 then return nil end
  table.sort(blocks,function(a,b)return a.position.x<b.position.x or a.position.x==b.position.x and(a.position.y<b.position.y or a.position.y==b.position.y and a.name<b.name)end)
  return blocks
end
local function site(npc,plan,key,name,x,y,d)
  local spec={name=name,item=name,position={x=x,y=y},direction=d or defines.direction.north,surface_index=npc.character.surface.index}
  local surface=npc.character.surface
  for _,e in pairs(surface.find_entities_filtered{position=spec.position,radius=.1,name=name,force=npc.force}) do
    if e.valid and (name=="stone-furnace" or e.direction==spec.direction) then spec.existing=true;break end
  end
  if not spec.existing and not surface.can_place_entity{name=name,position=spec.position,direction=spec.direction,force=npc.force,
    build_check_type=defines.build_check_type.manual} then
    spec.clear_blocks=plan.id=='chemical_science'and paid_clearance(npc,spec)or nil
    if not spec.clear_blocks then error("oil_site_blocked:"..key,0)end
  end
  local b=footprint(spec)
  for other_key,s in pairs(plan.sites) do
    local other=footprint(s)
    if b.x1<other.x2 and other.x1<b.x2 and b.y1<other.y2 and other.y1<b.y2 then
      if name==s.name and x==s.position.x and y==s.position.y then return other_key end
      error("oil_reserved_site_collision:"..key..":"..other_key,0)
    end
  end
  plan.sites[key]=spec;plan.new_keys[#plan.new_keys+1]=key;return key
end
local function pipes(npc,plan,prefix,x1,y1,x2,y2)
  if x1~=x2 and y1~=y2 then error("non_cardinal_oil_pipe_segment",0) end
  local dx=x1==x2 and 0 or x2>x1 and 1 or -1;local dy=y1==y2 and 0 or y2>y1 and 1 or -1
  for n=0,math.max(math.abs(x2-x1),math.abs(y2-y1)) do site(npc,plan,prefix..n,"pipe",x1+n*dx,y1+n*dy) end
end
local function yield_of(recipe,item)
  for _,p in pairs(recipe.products) do if p.name==item and p.type=="item" and p.amount and (not p.probability or p.probability==1) then return p.amount end end
end
local function budget(npc,demands)
  local raw,ledger,visiting={},{},{}
  local function need(item,count)
    ledger[item]=ledger[item] or 0
    local used=math.min(ledger[item],count);ledger[item]=ledger[item]-used;count=count-used
    if count<=0 then return end
    if ROOTS[item] then raw[item]=(raw[item] or 0)+count;return end
    local recipe=npc.force.recipes[item];local per=recipe and yield_of(recipe,item)
    if not per or visiting[item] then error("oil_budget_recipe_unavailable:"..item,0) end
    if not Recipes.made_in(recipe,npc.character.prototype.crafting_categories) then error("oil_budget_machine_required:"..item,0) end
    visiting[item]=true;local batches=math.ceil(count/per)
    for _,i in ipairs(recipe.ingredients) do if i.type~="item" then error("oil_budget_fluid_recipe:"..item,0) end;need(i.name,i.amount*batches) end
    visiting[item]=nil;ledger[item]=ledger[item]+batches*per-count
  end
  local keys={} for k in pairs(demands) do keys[#keys+1]=k end;table.sort(keys)
  for _,k in ipairs(keys) do need(k,demands[k]) end
  return raw
end
local consume
local function simulate(npc,plan,item,batches)
  local recipe=npc.force.recipes[item];local per=recipe and yield_of(recipe,item)
  if not per then error("oil_ledger_recipe_unavailable:"..item,0) end
  for _,i in ipairs(recipe.ingredients) do if i.type~="item" then error("oil_handcraft_fluid_unsupported:"..item,0) end;consume(npc,plan,i.name,i.amount*batches) end
  plan.stock[item]=(plan.stock[item] or 0)+per*batches
end
consume=function(npc,plan,item,count)
  local missing=math.max(0,count-(plan.stock[item] or 0))
  if missing>0 then
    if ROOTS[item] then error("oil_material_ledger_underflow:"..item,0) end
    simulate(npc,plan,item,math.ceil(missing/yield_of(npc.force.recipes[item],item)))
  end
  plan.stock[item]=(plan.stock[item] or 0)-count
end
local function craft(npc,plan,item,count)
  local batches=math.ceil(math.max(0,count-(plan.stock[item] or 0))/yield_of(npc.force.recipes[item],item))
  if batches>0 then simulate(npc,plan,item,batches);add(plan,{kind="craft",recipe=item,count=batches}) end
end
local function build(npc,plan,key)
  local s=plan.sites[key];if s.existing then return end
  craft(npc,plan,s.item,1);if s.body_staging then add(plan,{kind='move',position=copy(s.body_staging),avoid_danger=true})end;add(plan,{kind="build",target_site=key});consume(npc,plan,s.item,1)
end
local SOURCES={['iron-plate']='construction_iron_furnace',['copper-plate']='construction_copper_furnace',
  ['steel-plate']='steel_furnace',['stone-brick']='brick_furnace',['plastic-bar']='plastic_plant',sulfur='sulfur_plant'}
local function prepare(npc,plan,extra)
  local demands=copy(extra or {})
  for _,key in ipairs(plan.new_keys) do local s=plan.sites[key];if not s.existing then demands[s.item]=(demands[s.item] or 0)+1 end end
  plan.materials={physical_demands=demands,raw_requirements=budget(npc,demands),conservative_full_budget=true}
  plan.stock=copy(plan.materials.raw_requirements)
  -- Acquire the complete declared budget AFTER research component upkeep. This
  -- avoids treating stock consumed during prerequisite research as reserved.
  for _,item in ipairs({"wood","stone","coal"}) do local count=plan.stock[item] or 0
    if count>carried(npc,item) then add(plan,{kind="mine",item=item,count=count-carried(npc,item),search_radius=128}) end
  end
  if plan.sites.brick_furnace and not plan.sites.brick_furnace.existing then build(npc,plan,"brick_furnace") end
  -- Manufacture upstream roots before reserving the final iron/copper budget:
  -- real steel production itself consumes carried iron plates.
  for _,item in ipairs({"steel-plate","stone-brick","plastic-bar","sulfur","iron-plate","copper-plate"}) do
    if (plan.stock[item] or 0)>0 then wait(plan,{kind="inventory",item=item,count=plan.stock[item],source_site=SOURCES[item]}) end
  end
end
local function base(npc,id)
  local planet=npc.character.surface.planet
  if not planet or planet.name~="nauvis" then error("oil_goal_requires_nauvis",0) end
  local old=npc.factory_project and npc.factory_project.plan
  if not old or not old.sites or not old.sites.green_assembler or not old.sites.steel_furnace then error("qualified_green_and_steel_sites_required",0) end
  local p={id=id,revision=Oil.revision,sites=copy(old.sites),steps={},new_keys={},manual_component_logistics=true,continuous_component_factory=false}
  -- A blocked serialized plan may contain future placements. Only physical
  -- owned entities survive replanning as existing infrastructure.
  for key,s in pairs(p.sites) do
    if resolve(npc,key) then s.existing=true else p.sites[key]=nil end
  end
  for _,key in ipairs({"iron_furnace","construction_iron_furnace","construction_copper_furnace","green_assembler","steel_furnace","lab","engine","water_pipe2"}) do
    if not p.sites[key] then error("owned_existing_infrastructure_required:"..key,0) end
  end
  return p
end
local function tech(plan,name) add(plan,{kind="research",technology=name});wait(plan,{kind="technology",technology=name}) end
local function observed_well(npc,observation)
  if type(observation)~="table" or observation.npc_id~=npc.id or observation.force~=npc.force.name or observation.force_index~=npc.force.index
      or observation.surface_index~=npc.character.surface.index or observation.name~="crude-oil"
      or type(observation.position)~="table" or type(observation.body_position)~="table" then return nil end
  local receipt=npc.receipts and npc.receipts[observation.job_id]
  if observation.source~="completed_worker_move_local_oil_observation" or not receipt
      or receipt.id~=observation.receipt_id or receipt.npc_id~=npc.id or receipt.status~="complete" or receipt.kind~="move" then return nil end
  local p,b=observation.position,observation.body_position
  if type(p.x)~="number" or type(p.y)~="number" or type(b.x)~="number" or type(b.y)~="number"
      or (p.x-b.x)^2+(p.y-b.y)^2>64^2 then return nil end
  for _,e in ipairs(npc.character.surface.find_entities_filtered{name="crude-oil",type="resource",position=p,radius=.05}) do
    if e.valid and e.amount>0 and math.abs(e.position.x-p.x)<.05 and math.abs(e.position.y-p.y)<.05 then return e end
  end
end
local function power_relay(npc,plan,destination)
  local anchors={}
  for key,s in pairs(plan.sites) do if s.name=='small-electric-pole' then
    local e=resolve(npc,key)
    if e and e.electric_network_id then anchors[#anchors+1]={key=key,position=copy(e.position),
      d=(e.position.x-destination.x)^2+(e.position.y-destination.y)^2} end
  end end
  table.sort(anchors,function(a,b)return a.d<b.d or a.d==b.d and a.key<b.key end)
  local anchor=anchors[1];if not anchor then error('oil_actual_power_anchor_required',0)end
  local surface=npc.character.surface
  -- Sparse 5-tile graph: cardinal/diagonal edges are <=sqrt(50)<7.5.
  -- Pure preflight on already generated terrain; each returned pole is paid.
  local open,seen,parent,nodes={{x=anchor.position.x,y=anchor.position.y,g=0}},{},{},{}
  local function key(x,y)return x..':'..y end
  local start=key(anchor.position.x,anchor.position.y);seen[start]=0;nodes[start]=open[1]
  local visited=0;local finish
  while #open>0 and visited<4096 do
    local best=1
    for i=2,#open do local a,b=open[i],open[best]
      local fa=a.g+math.sqrt((a.x-destination.x)^2+(a.y-destination.y)^2)
      local fb=b.g+math.sqrt((b.x-destination.x)^2+(b.y-destination.y)^2)
      if fa<fb or fa==fb and(a.y<b.y or a.y==b.y and a.x<b.x)then best=i end
    end
    local q=table.remove(open,best);local qkey=key(q.x,q.y);visited=visited+1
    if (q.x-destination.x)^2+(q.y-destination.y)^2<=7.5^2 then finish=qkey;break end
    for _,d in ipairs({{-5,0},{0,-5},{0,5},{5,0},{-5,-5},{-5,5},{5,-5},{5,5}})do
      local x,y=q.x+d[1],q.y+d[2];local k=key(x,y);local cost=q.g+math.sqrt(d[1]^2+d[2]^2)
      if not seen[k] and (x-npc.home.x)^2+(y-npc.home.y)^2<=256^2
          and surface.is_chunk_generated{x=math.floor(x/32),y=math.floor(y/32)} then
        local trial=copy(plan)
        local ok=pcall(site,npc,trial,'oil_relay_trial','small-electric-pole',x,y)
        if ok then seen[k]=cost;parent[k]=qkey;local n={x=x,y=y,g=cost};nodes[k]=n;open[#open+1]=n end
      end
    end
  end
  if not finish then error('oil_bounded_generated_power_relay_unavailable',0)end
  local reverse={};local k=finish
  while k~=start do reverse[#reverse+1]=nodes[k];k=parent[k] end
  if #reverse>64 then error('oil_power_relay_budget_limit',0)end
  for i=#reverse,1,-1 do local p=reverse[i];site(npc,plan,'oil_relay'..(#reverse-i+1),'small-electric-pole',p.x,p.y)end
  plan.power_relay={anchor_site=anchor.key,anchor_position=anchor.position,destination=copy(destination),
    pole_count=#reverse,max_edge_distance=math.sqrt(50),survey_scope='generated_construction_corridor_to_physically_observed_well'}
end
local function oil(npc,observation)
  local plan=base(npc,"oil_processing")
  local seen=observed_well(npc,observation)
  if not seen then error("bound_physical_oil_observation_required",0) end
  local ores={seen}
  table.sort(ores,function(a,b)
    local da=(a.position.x-npc.home.x)^2+(a.position.y-npc.home.y)^2
    local db=(b.position.x-npc.home.x)^2+(b.position.y-npc.home.y)^2
    return da<db or da==db and (a.position.x<b.position.x or a.position.x==b.position.x and a.position.y<b.position.y)
  end)
  if #ores==0 then error("nearby_actual_crude_oil_required",0) end
  local p={x=math.floor(ores[1].position.x)+.5,y=math.floor(ores[1].position.y)+.5};plan.oil_origin=copy(p)
  local f=plan.sites.iron_furnace.position;site(npc,plan,"brick_furnace","stone-furnace",f.x+12,f.y+8)
  site(npc,plan,"oil_pumpjack","pumpjack",p.x,p.y)
  site(npc,plan,"oil_refinery","oil-refinery",p.x+8,p.y+8)
  for i,q in ipairs({{0,13},{-2,8},{-2,2},{4,8},{6,2},{12,2},{18,2}}) do
    site(npc,plan,"oil_pole"..i,"small-electric-pole",p.x+q[1],p.y+q[2])
  end
  pipes(npc,plan,"crude_a",p.x+1,p.y-2,p.x-3,p.y-2)
  pipes(npc,plan,"crude_b",p.x-3,p.y-2,p.x-3,p.y+11)
  pipes(npc,plan,"crude_c",p.x-3,p.y+11,p.x+9,p.y+11)
  pipes(npc,plan,"gas_a",p.x+10,p.y+5,p.x+10,p.y)
  pipes(npc,plan,"gas_b",p.x+10,p.y,p.x+16,p.y)
  pipes(npc,plan,"gas_c",p.x+16,p.y,p.x+16,p.y+4)
  pipes(npc,plan,"gas_d",p.x+16,p.y+4,p.x+19,p.y+4)
  power_relay(npc,plan,plan.sites.oil_pole1.position)
  -- Basic oil has no water ingredient. This first outpost reserves neither a
  -- fictitious water route nor an unverified steam port; chemistry needs a
  -- separately qualified physical water/power extension.
  plan.layout='observed-well-basic-oil-existing-generation'
  plan.chemical_water_route_qualified=false
  tech(plan,"engine");tech(plan,"fluid-handling");tech(plan,"oil-gathering")
  prepare(npc,plan)
  for _,key in ipairs(plan.new_keys) do if key~="oil_refinery" and key~="brick_furnace" then build(npc,plan,key) end end
  wait(plan,{kind="powered",target_site="oil_pumpjack"})
  wait(plan,{kind="technology",technology="oil-processing"}) -- Native crude extraction trigger.
  build(npc,plan,"oil_refinery")
  add(plan,{kind="configure_recipe",target_site="oil_refinery",recipe="basic-oil-processing"})
  local old_refinery=resolve(npc,"oil_refinery")
  plan.production_baselines={oil_refinery=old_refinery and old_refinery.products_finished or 0}
  wait(plan,{kind="products_finished",target_site="oil_refinery",count=plan.production_baselines.oil_refinery+3})
  plan.completion={native_basic_oil_cycles=3,native_oil_trigger_required=true,fluid_setter_calls=0,
    power="existing_owned_native_generation_and_paid_sparse_relay",chemical_water_route_qualified=false}
  plan.stock=nil;return plan
end
local function scout_goal(npc,position)
  local surface=npc.character.surface
  local offsets={}
  for y=-12,12 do for x=-12,12 do offsets[#offsets+1]={x=x,y=y,d=x*x+y*y} end end
  table.sort(offsets,function(a,b)return a.d<b.d or a.d==b.d and (a.y<b.y or a.y==b.y and a.x<b.x) end)
  for _,d in ipairs(offsets) do local p={x=position.x+d.x,y=position.y+d.y}
    if (p.x-npc.home.x)^2+(p.y-npc.home.y)^2<=128^2
        and surface.is_chunk_generated{x=math.floor(p.x/32),y=math.floor(p.y/32)}
        and surface.can_place_entity{name="character",position=p,force=npc.force,build_check_type=defines.build_check_type.manual} then return p end
  end
end
local function discovery_plan(npc)
  local plan=base(npc,"oil_processing")
  plan.layout="embodied-local-oil-survey"
  plan.discovery={kind="embodied_local_oil_survey",npc_id=npc.id,force=npc.force.name,force_index=npc.force.index,
    surface_index=npc.character.surface.index,home=copy(npc.home),local_radius=64,
    waypoint_limit=8,waypoint_radius=128,status="scouting",receipt_ids={},observations={},
    knowledge_scope="generated_terrain_waypoints_then_completed_worker_move_local_observation"}
  -- No remote oil query chooses these waypoints. First inspect the actual
  -- home neighborhood, then a finite declared exterior circuit.
  for _,offset in ipairs({{4,0},{0,-112},{-112,0},{0,112},{112,0},{-80,-80},{-80,80},{80,80}}) do
    local p=scout_goal(npc,{x=npc.home.x+offset[1],y=npc.home.y+offset[2]})
    if p then add(plan,{kind="move",position=p,oil_scout=true}) end
  end
  plan.discovery.waypoint_count=#plan.steps
  if #plan.steps==0 then error("no_legal_generated_oil_scout_waypoint",0) end
  wait(plan,{kind="oil_scout_exhausted"})
  plan.completion={requires_bound_physical_observation=true,scouting_only=true}
  return plan
end

function Oil.on_receipt(npc,receipt)
  local p=npc.factory_project;local d=p and p.plan and p.plan.discovery
  local step=p and p.plan and p.plan.steps[p.index]
  if not d or d.status~="scouting" or p.id~="oil_processing" or p.status~="active"
      or p.pending_maintenance or not p.pending_job or not step or step.kind~="move" or not step.oil_scout
      or not receipt or receipt.status~="complete" or receipt.kind~="move" or receipt.npc_id~=npc.id
      or receipt.job_id~=p.pending_job or not npc.receipts or npc.receipts[receipt.job_id]~=receipt
      or type(receipt.id)~="string" or type(d.receipt_ids)~="table" or d.receipt_ids[receipt.id] then return nil end
  local c=npc.character
  local planet=c and c.valid and c.surface.planet
  if not c or not c.valid or c.force~=npc.force or d.npc_id~=npc.id or d.force~=c.force.name or d.force_index~=c.force.index
      or d.surface_index~=c.surface.index or not planet or planet.name~="nauvis"
      or (c.position.x-step.position.x)^2+(c.position.y-step.position.y)^2>1.5^2 then return nil end
  d.receipt_ids[receipt.id]=true;d.completed_waypoints=(d.completed_waypoints or 0)+1
  local wells=c.surface.find_entities_filtered{name="crude-oil",type="resource",position=c.position,radius=64}
  table.sort(wells,function(a,b)
    local da=(a.position.x-c.position.x)^2+(a.position.y-c.position.y)^2
    local db=(b.position.x-c.position.x)^2+(b.position.y-c.position.y)^2
    return da<db or da==db and (a.position.x<b.position.x or a.position.x==b.position.x and a.position.y<b.position.y)
  end)
  local last_reason
  for index=1,math.min(8,#wells) do local well=wells[index]
    local observation={source="completed_worker_move_local_oil_observation",receipt_id=receipt.id,job_id=receipt.job_id,npc_id=npc.id,
      force=c.force.name,force_index=c.force.index,surface_index=c.surface.index,name=well.name,position=copy(well.position),
      body_position=copy(c.position),observed_tick=game.tick,amount=well.amount}
    if observed_well(npc,observation) then
      npc.oil_observation=copy(observation)
      d.observations[#d.observations+1]=copy(observation)
      if #d.observations>16 then table.remove(d.observations,1) end
      local ok,planned=pcall(oil,npc,observation)
      if ok and planned then
        d.status="observed_and_planned";planned.oil_observation=copy(observation)
        planned.discovery_audit=copy(d)
        return {kind="replace_plan",plan=planned}
      end
      last_reason=tostring(planned)
    end
  end
  if last_reason then d.last_geometry_reason=last_reason end
  if p.index>=d.waypoint_count then
    d.status="exhausted"
    local reason=last_reason or d.last_geometry_reason or "no_observed_crude_oil_in_bounded_body_survey"
    return {kind="oil_scout_blocked",reason=reason}
  end
  return {kind="oil_scout_progress"}
end
local function native_water_connected(npc,from,to)
  local source=resolve(npc,from);local surface=npc.character.surface
  local target=surface.find_entities_filtered{name='pipe',position=to,radius=.05,force=npc.force}[1]
  if not source or not target then return false end
  local queue,seen={target},{[target.unit_number]=true};local index=1
  while index<=#queue and index<=1024 do local e=queue[index];index=index+1
    if e==source then return true end
    for _,c in ipairs(e.get_fluid_box_pipe_connections(1))do if c.target then local next_entity=c.target
      if next_entity.valid and next_entity.type=='pipe'and next_entity.force==npc.force
          and next_entity.surface==surface and not seen[next_entity.unit_number]then
        seen[next_entity.unit_number]=true;queue[#queue+1]=next_entity
      end
    end end
  end
  return false
end
local function water_route(npc,plan,to)
  local from=plan.sites.water_pipe2.position;local surface=npc.character.surface
  if native_water_connected(npc,'water_pipe2',to)then
    plan.water_extension={kind='existing_owned_native_pipe_connection',from=copy(from),to=copy(to)};return
  end
  local forbidden={}
  local function key(x,y)return x..':'..y end
  for name,s in pairs(plan.sites)do if s.name=='pipe' then
    local water=name:sub(1,5)=='water'or name:sub(1,13)=='outpost_water'
    local e=resolve(npc,name)
    if e and e.get_fluid_contents().water then water=true end
    if not water then
      for _,d in ipairs({{0,0},{-1,0},{0,-1},{0,1},{1,0}})do forbidden[key(s.position.x+d[1],s.position.y+d[2])]=true end
    end
  end end
  local minx,maxx=math.min(from.x,to.x)-24,math.max(from.x,to.x)+24
  local miny,maxy=math.min(from.y,to.y)-24,math.max(from.y,to.y)+24
  local open,seen,parent,nodes={{x=from.x,y=from.y,g=0}},{},{},{}
  local start=key(from.x,from.y);local goal=key(to.x,to.y);seen[start]=true;nodes[start]=open[1]
  local finish;local visited=0
  while #open>0 and visited<12000 do
    local best=1
    for i=2,#open do local a,b=open[i],open[best]
      local fa=a.g+math.abs(a.x-to.x)+math.abs(a.y-to.y)
      local fb=b.g+math.abs(b.x-to.x)+math.abs(b.y-to.y)
      if fa<fb or fa==fb and(a.g>b.g or a.g==b.g and(a.y<b.y or a.y==b.y and a.x<b.x))then best=i end
    end
    local q=table.remove(open,best);local qkey=key(q.x,q.y);visited=visited+1
    if qkey==goal then finish=qkey;break end
    for _,d in ipairs({{-1,0},{0,-1},{0,1},{1,0}})do local x,y=q.x+d[1],q.y+d[2];local k=key(x,y)
      if not seen[k]and not forbidden[k]and x>=minx and x<=maxx and y>=miny and y<=maxy
          and surface.is_chunk_generated{x=math.floor(x/32),y=math.floor(y/32)}then
        seen[k]=true;local trial=copy(plan)
        local ok,result=pcall(site,npc,trial,'water_trial','pipe',x,y)
        -- Route around vegetation; only machine footprints use paid clearance.
        if ok and trial.sites[result].clear_blocks then ok=false end
        if ok then parent[k]=qkey;local n={x=x,y=y,g=q.g+1};nodes[k]=n;open[#open+1]=n end
      end
    end
  end
  if not finish then error('oil_bounded_generated_water_trunk_unavailable',0)end
  local reverse={};local k=finish
  while k~=start do reverse[#reverse+1]=nodes[k];k=parent[k] end
  if #reverse>512 then error('oil_water_trunk_budget_limit',0)end
  local route_keys={}
  for i=#reverse,1,-1 do local p=reverse[i];local name='outpost_water'..(#reverse-i+1)
    local actual=site(npc,plan,name,'pipe',p.x,p.y);route_keys[#route_keys+1]=actual
  end
  -- Move to a real open side before placing each paid pipe. Future pipe and
  -- machine footprints stay reserved; the body never stages on its own trunk.
  local previous=from;local offsets={{-1,0},{0,-1},{0,1},{1,0},{-2,0},{0,-2},{0,2},{2,0}}
  for _,name in ipairs(route_keys)do local spec=plan.sites[name]
    if not spec.existing then local candidates={}
      for _,d in ipairs(offsets)do local p={x=spec.position.x+d[1],y=spec.position.y+d[2]}
        local legal=surface.can_place_entity{name=npc.character.name,position=p,force=npc.force,build_check_type=defines.build_check_type.manual}
        if legal then
          local b=footprint{name=npc.character.name,position=p,direction=defines.direction.north}
          for _,reserved in pairs(plan.sites)do local other=footprint(reserved)
            if b.x1<other.x2 and other.x1<b.x2 and b.y1<other.y2 and other.y1<b.y2 then legal=false;break end
          end
        end
        if legal then candidates[#candidates+1]={position=p,d=(p.x-previous.x)^2+(p.y-previous.y)^2}end
      end
      table.sort(candidates,function(a,b)return a.d<b.d or a.d==b.d and(a.position.y<b.position.y or a.position.y==b.position.y and a.position.x<b.position.x)end)
      if #candidates==0 then error('oil_water_trunk_body_staging_unavailable:'..name,0)end
      spec.body_staging=copy(candidates[1].position);previous=spec.body_staging
    end
  end
  plan.water_extension={kind='paid_generated_native_pipe_trunk',from=copy(from),to=copy(to),
    pipe_count=#reverse,visited=visited,search_margin=24,node_limit=12000,neutral_clearance_targets=0,paid_pipe_side_moves=true,
    knowledge_scope='bounded_construction_corridor_owned_water_outlet_to_physically_observed_oil_outpost'}
end
local function extra_generation(npc,plan)
  if resolve(npc,'second_engine')then plan.power_extension={kind='existing_owned_second_engine'};return end
  local engine=resolve(npc,'engine');local poles={}
  for key,s in pairs(plan.sites)do if s.name=='small-electric-pole'then local e=resolve(npc,key)
    if e and e.electric_network_id==engine.electric_network_id then poles[#poles+1]={key=key,position=copy(e.position)}end
  end end
  table.sort(poles,function(a,b)return a.key<b.key end)
  for _,port in ipairs(engine.get_fluid_box_pipe_connections(1))do if not port.target then
    local dx=(port.target_position.x-engine.position.x)/3;local dy=(port.target_position.y-engine.position.y)/3
    if math.abs(dx)+math.abs(dy)==1 then
      local trial=copy(plan);local ok=pcall(function()
        site(npc,trial,'second_engine','steam-engine',engine.position.x+dx*6,engine.position.y+dy*6,engine.direction)
        site(npc,trial,'second_steam_pipe','pipe',port.target_position.x,port.target_position.y)
        local center=trial.sites.second_engine.position;local candidates={}
        for y=-3,3 do for x=-3,3 do if math.abs(x)+math.abs(y)>=2 then
          local p={x=center.x+x,y=center.y+y}
          for _,pole in ipairs(poles)do local distance=(pole.position.x-p.x)^2+(pole.position.y-p.y)^2
            if distance<=7.5^2 then candidates[#candidates+1]={position=p,anchor=pole.key,d=distance}end
          end
        end end end
        table.sort(candidates,function(a,b)return a.d<b.d or a.d==b.d and(a.position.y<b.position.y or a.position.y==b.position.y and a.position.x<b.position.x)end)
        for _,candidate in ipairs(candidates)do local next_trial=copy(trial)
          if pcall(site,npc,next_trial,'second_engine_pole','small-electric-pole',candidate.position.x,candidate.position.y)then
            trial=next_trial;trial.power_extension={kind='paid_native_inline_steam_engine',engine_direction=engine.direction,
              native_source_port=copy(port.target_position),anchor_site=candidate.anchor};return
          end
        end
        error('oil_inline_engine_pole_unavailable',0)
      end)
      if ok then return trial end
    end
  end end
  error('oil_native_inline_engine_layout_unavailable',0)
end
local function chemical(npc)
  local plan=base(npc,"chemical_science");local p=plan.oil_origin or npc.factory_project.plan.oil_origin
  if not p or not resolve(npc,"oil_refinery") then error("native_oil_stage_required",0) end
  plan.oil_origin=copy(p)
  plan=extra_generation(npc,plan)or plan
  site(npc,plan,"plastic_plant","chemical-plant",p.x+14,p.y+2)
  site(npc,plan,"sulfur_plant","chemical-plant",p.x+20,p.y+2,defines.direction.south)
  site(npc,plan,"engine_assembler","assembling-machine-1",p.x+14,p.y-4)
  site(npc,plan,"blue_assembler","assembling-machine-1",p.x+20,p.y-4)
  site(npc,plan,"engine_assembler_pole","small-electric-pole",p.x+12,p.y-4)
  site(npc,plan,"blue_assembler_pole","small-electric-pole",p.x+18,p.y-4)
  water_route(npc,plan,{x=p.x+21,y=p.y+4})
  tech(plan,"sulfur-processing");tech(plan,"plastics")
  -- Construction budget is acquired first; plastic/sulfur demands are acquired
  -- separately after the new native plants have recipes and power.
  prepare(npc,plan)
  for _,key in ipairs(plan.new_keys) do build(npc,plan,key) end
  wait(plan,{kind='powered',target_site='second_engine'})
  wait(plan,{kind='fluid',target_site='second_engine',fluid='steam',amount=1})
  add(plan,{kind="configure_recipe",target_site="plastic_plant",recipe="plastic-bar"})
  add(plan,{kind="configure_recipe",target_site="sulfur_plant",recipe="sulfur"})
  plan.production_baselines={}
  for _,key in ipairs({"plastic_plant","sulfur_plant","blue_assembler"}) do
    local machine=resolve(npc,key);plan.production_baselines[key]=machine and machine.products_finished or 0
  end
  wait(plan,{kind="products_finished",target_site="plastic_plant",count=plan.production_baselines.plastic_plant+1})
  wait(plan,{kind="products_finished",target_site="sulfur_plant",count=plan.production_baselines.sulfur_plant+1})
  tech(plan,"advanced-circuit");tech(plan,"chemical-science-pack")
  -- Ten packs: ten native engines, fifteen hand-crafted advanced circuits,
  -- five native sulfur. Machine ingredients follow installed runtime recipes.
  local raw=budget(npc,{['iron-gear-wheel']=10,pipe=20,['steel-plate']=10,['advanced-circuit']=15,sulfur=5})
  plan.stock=copy(raw);plan.materials.blue_bootstrap_raw=copy(raw)
  for _,item in ipairs({"steel-plate","plastic-bar","sulfur","iron-plate","copper-plate"}) do
    if (raw[item] or 0)>0 then wait(plan,{kind="inventory",item=item,count=raw[item],source_site=SOURCES[item]}) end
  end
  add(plan,{kind="configure_recipe",target_site="engine_assembler",recipe="engine-unit"})
  craft(npc,plan,"iron-gear-wheel",10);craft(npc,plan,"pipe",20)
  for _,entry in ipairs({{"steel-plate",10},{"iron-gear-wheel",10},{"pipe",20}}) do
    add(plan,{kind="transfer",target_site="engine_assembler",item=entry[1],count=entry[2],mode="deposit",inventory=defines.inventory.crafter_input})
    consume(npc,plan,entry[1],entry[2])
  end
  wait(plan,{kind="inventory",item="engine-unit",count=10,source_site="engine_assembler"})
  craft(npc,plan,"advanced-circuit",15)
  add(plan,{kind="configure_recipe",target_site="blue_assembler",recipe="chemical-science-pack"})
  for _,entry in ipairs({{"engine-unit",10},{"advanced-circuit",15},{"sulfur",5}}) do
    add(plan,{kind="transfer",target_site="blue_assembler",item=entry[1],count=entry[2],mode="deposit",inventory=defines.inventory.crafter_input})
  end
  wait(plan,{kind="products_finished",target_site="blue_assembler",count=plan.production_baselines.blue_assembler+5})
  wait(plan,{kind="inventory",item="chemical-science-pack",count=10,source_site="blue_assembler"})
  add(plan,{kind="transfer",target_site="lab",item="chemical-science-pack",count=10,mode="deposit",inventory=defines.inventory.lab_input})
  local consumed=npc.force.get_item_production_statistics(npc.character.surface).get_output_count("chemical-science-pack")
  add(plan,{kind="research",technology="advanced-oil-processing"})
  wait(plan,{kind="item_consumption",item="chemical-science-pack",count=consumed+10})
  plan.completion={native_blue_packs=10,native_blue_consumption=10,manual_component_and_blue_lab_delivery=true,
    advanced_oil_research_complete=false,fluid_setter_calls=0}
  plan.chemical_water_route_qualified=true
  plan.stock=nil;return plan
end
function Oil.plan(npc,id)
  if not npc.character or not npc.character.valid or npc.character.force~=npc.force then return nil,"oil_actor_scope_invalid" end
  local fn
  if id=="oil_processing" then
    fn=function(n)
      if observed_well(n,n.oil_observation) then return oil(n,n.oil_observation) end
      return discovery_plan(n)
    end
  elseif id=="chemical_science" then fn=chemical end
  if not fn then return nil,"unknown_oil_project" end
  local ok,plan=pcall(fn,npc);if not ok then return nil,plan end;return plan
end
local function withdraw(npc,key,item,count)
  local entity=resolve(npc,key);if not entity then return nil end
  local available=entity.get_output_inventory().get_item_count(item)
  if available>0 then return {kind="transfer",target=entity,item=item,count=math.min(available,count),mode="withdraw"} end
end
local function funded_craft(npc,item,count)
  local recipe=npc.force.recipes[item]
  if not recipe or not recipe.enabled then return nil end
  local raw=budget(npc,{[item]=count})
  -- Upstream steel burns real iron plates. Fund manufactured roots before
  -- reserving the final iron/copper for character crafting.
  for _,root in ipairs({"steel-plate","plastic-bar","sulfur","iron-plate","copper-plate"}) do
    if carried(npc,root)<(raw[root] or 0) then
      if root=="plastic-bar" or root=="sulfur" then return withdraw(npc,SOURCES[root],root,raw[root]-carried(npc,root)) end
      return Advanced.maintenance(npc,{kind="inventory",item=root,count=raw[root],source_site=SOURCES[root]})
    end
  end
  return {kind="craft",recipe=item,count=math.ceil(count/yield_of(recipe,item))}
end
local function deposit_input(npc,entity,inventory,item,desired)
  local input=entity.get_inventory(inventory)
  if not input then return nil end
  local count=math.min(math.max(0,desired-input.get_item_count(item)),carried(npc,item),
    input.get_insertable_count{name=item,quality="normal"})
  if count>0 then return {kind="transfer",target=entity,item=item,count=count,
    mode="deposit",inventory=inventory} end
end
-- The engine and blue assemblers have a three-tile gap. Two ordinary powered
-- inserters and one eastbound belt bridge it without changing either recipe.
-- Build this only from the completed chemical plan, so its original material
-- ledger and research sequence remain intact.
local BRIDGE_KEYS={"engine_bridge_output","engine_bridge_belt","engine_bridge_input"}
local BRIDGE_REVISION="paid-alternate-blue-v1"
local function intersects(a,b)
  return a.x1<b.x2 and b.x1<a.x2 and a.y1<b.y2 and b.y1<a.y2
end
local function body_stage(npc,plan,spec)
  local c=npc.character
  for _,offset in ipairs({{0,-2},{0,2},{-2,0},{2,0}})do
    local p={x=spec.position.x+offset[1],y=spec.position.y+offset[2]}
    local b=footprint{name=c.name,position=p,direction=defines.direction.north}
    local safe=true
    for _,reserved in pairs(plan.sites)do if intersects(b,footprint(reserved))then safe=false;break end end
    if safe and c.surface.can_place_entity{name=c.name,position=p,force=npc.force,
        build_check_type=defines.build_check_type.manual}then return p end
  end
end
local function bridge_power(npc,spec)
  -- Native poles report is_connected_to_electric_network=false in 2.0.77;
  -- use their network ID, tied to the actual powered producer and consumer.
  local engine=resolve(npc,"engine_assembler");local blue=resolve(npc,"blue_assembler")
  if not engine or not blue or not engine.is_connected_to_electric_network()
      or not blue.is_connected_to_electric_network()or engine.energy<=0 or blue.energy<=0
      or engine.electric_network_id~=blue.electric_network_id then return nil end
  for _,pole in ipairs(npc.character.surface.find_entities_filtered{position=spec.position,radius=12,type="electric-pole",force=npc.force})do
    local range=pole.prototype.get_supply_area_distance("normal")
    if pole.electric_network_id and pole.electric_network_id==engine.electric_network_id
        and math.abs(spec.position.x-pole.position.x)<=range
        and math.abs(spec.position.y-pole.position.y)<=range then return pole.unit_number end
  end
end
local function engine_bridge(npc)
  local project=npc.factory_project;local plan=project and project.plan
  if not plan then return nil end
  local bindings=0;for _,key in ipairs(BRIDGE_KEYS)do if plan.sites[key]then bindings=bindings+1 end end
  if bindings>0 and bindings~=3 then plan.engine_bridge_reason="partial_saved_engine_bridge";return nil end
  if bindings==0 then
    if project.id~="chemical_science"or project.status~="complete"then return nil end
    if plan.engine_bridge_preview_revision==BRIDGE_REVISION then return nil end
    plan.engine_bridge_preview_revision=BRIDGE_REVISION
    local engine=resolve(npc,"engine_assembler");local blue=resolve(npc,"blue_assembler")
    if not engine or not blue or blue.position.x-engine.position.x~=6 or blue.position.y~=engine.position.y then
      plan.engine_bridge_reason="owned_engine_blue_gap_required";return nil
    end
    local er,br=engine.get_recipe(),blue.get_recipe()
    if not er or er.name~="engine-unit"or not br or br.name~="chemical-science-pack"then
      plan.engine_bridge_reason="native_engine_blue_recipes_required";return nil
    end
    plan.engine_bridge_trials={}
    for _,offset in ipairs({1,-1})do
      local trial={sites=copy(plan.sites),new_keys={}}
      local ok,reason=pcall(function()
        local e=engine.position
        site(npc,trial,BRIDGE_KEYS[1],"inserter",e.x+2,e.y+offset,defines.direction.west)
        site(npc,trial,BRIDGE_KEYS[2],"transport-belt",e.x+3,e.y+offset,defines.direction.east)
        site(npc,trial,BRIDGE_KEYS[3],"inserter",e.x+4,e.y+offset,defines.direction.west)
        for _,key in ipairs(BRIDGE_KEYS)do
          local s=trial.sites[key];if not s then error("engine_bridge_site_alias:"..key,0)end
          s.body_staging=body_stage(npc,trial,s)
          if not s.body_staging then error("engine_bridge_body_side_blocked:"..key,0)end
          if s.name=="inserter"then
            s.power_pole_unit=bridge_power(npc,s)
            if not s.power_pole_unit then error("engine_bridge_native_power_required:"..key,0)end
          end
        end
      end)
      plan.engine_bridge_trials[#plan.engine_bridge_trials+1]={offset=offset,eligible=ok,reason=not ok and tostring(reason)or nil,tick=game.tick}
      if ok then
        for _,key in ipairs(BRIDGE_KEYS)do plan.sites[key]=trial.sites[key]end
        plan.engine_bridge_layout={revision=BRIDGE_REVISION,offset=offset,engine_unit=engine.unit_number,blue_unit=blue.unit_number,observed_tick=game.tick}
        plan.engine_bridge_reason=nil;break
      end
      plan.engine_bridge_reason=tostring(reason)
    end
    if not plan.sites[BRIDGE_KEYS[1]]then return nil end
  end
  for _,key in ipairs(BRIDGE_KEYS)do
    local spec=plan.sites[key];local existing=resolve(npc,key)
    if not existing then
      if not npc.character.surface.can_place_entity{name=spec.name,position=spec.position,direction=spec.direction,force=npc.force,
          build_check_type=defines.build_check_type.manual}then plan.engine_bridge_reason="bound_engine_bridge_site_blocked:"..key;return nil end
      if carried(npc,spec.item)<1 then return funded_craft(npc,spec.item,1)end
      if not spec.body_staging then spec.body_staging=body_stage(npc,plan,spec)end
      if not spec.body_staging then plan.engine_bridge_reason="bound_engine_bridge_body_side_blocked:"..key;return nil end
      local intent=plan.engine_bridge_staging
      if not intent or intent.key~=key then
        local request={kind="move",position=copy(spec.body_staging),avoid_danger=true}   -- (W-30)
        plan.engine_bridge_staging={key=key,request=request,npc_id=npc.id,force=npc.force.name,
          surface_index=npc.character.surface.index,project_id=project.id,project_started_tick=project.started_tick,issued_tick=game.tick}
        return request
      end
      local r=intent.request.id and npc.receipts and npc.receipts[intent.request.id]
      if not r then plan.engine_bridge_reason="engine_bridge_approach_receipt_pending:"..key;return nil end
      local p=npc.character.position;local goal=spec.body_staging
      local valid=r.kind=="move"and r.status=="complete"and r.npc_id==npc.id and r.job_id==intent.request.id
        and npc.completed_jobs[r.job_id]==r.id and(r.started_tick or 0)>=intent.issued_tick
        and intent.npc_id==npc.id and intent.force==npc.force.name and intent.surface_index==npc.character.surface.index
        and intent.project_id==project.id and intent.project_started_tick==project.started_tick
        and r.position and math.abs(r.position.x-goal.x)<=.6 and math.abs(r.position.y-goal.y)<=.6
      if not valid then plan.engine_bridge_reason="engine_bridge_approach_receipt_invalid:"..key;return nil end
      if math.abs(p.x-goal.x)>.6 or math.abs(p.y-goal.y)>.6 then
        plan.engine_bridge_staging=nil;return engine_bridge(npc)
      end
      plan.engine_bridge_approach_receipts=plan.engine_bridge_approach_receipts or{}
      plan.engine_bridge_approach_receipts[key]=r.id
      plan.engine_bridge_reason=nil
      return {kind="build",entity_name=spec.name,item=spec.item,position=spec.position,direction=spec.direction}
    end
    local wanted=key==BRIDGE_KEYS[2]and defines.direction.east or defines.direction.west
    spec.direction=wanted
    if existing.direction~=wanted then plan.engine_bridge_reason="bound_engine_bridge_direction_repair:"..key
      return {kind="configure_direction",target=existing,direction=wanted}end
  end
  local belt=resolve(npc,BRIDGE_KEYS[2]);local misplaced=0
  for line=1,2 do misplaced=misplaced+belt.get_transport_line(line).get_item_count("chemical-science-pack")end
  if misplaced>0 then return {kind="belt_pickup",target=belt,item="chemical-science-pack",count=misplaced}end
  plan.engine_bridge_reason=nil;plan.engine_bridge_ready=true
end

local function engine_bridge_ready(npc)
  local a=resolve(npc,"engine_bridge_output")
  local b=resolve(npc,"engine_bridge_belt")
  local c=resolve(npc,"engine_bridge_input")
  if not (a and b and c and a.direction==defines.direction.west
      and b.direction==defines.direction.east and c.direction==defines.direction.west) then return false end
  -- A prior, reversed input inserter could have spilled finished blue packs
  -- onto this belt. Never switch off physical manual engine delivery until
  -- its lanes contain only engines that the native bridge can carry.
  for line=1,2 do
    for _,stack in pairs(b.get_transport_line(line).get_contents()) do
      if stack.name~="engine-unit" then return false end
    end
  end
  return true
end
-- A finite starter copper footprint can deplete during the long silo research.
-- Add one funded adjacent drill and two belts into the existing native red
-- science conveyor. No copper/science is hand-injected into that line.
local function red_mining_recovery(npc)
  local project=npc.factory_project;local plan=project and project.plan
  if not plan then return nil end
  local lab=resolve(npc,"lab");local old=resolve(npc,"copper_drill")
  if not lab or not old then return nil end
  local input=lab.get_inventory(defines.inventory.lab_input)
  local keys={"red_recovery_drill","red_recovery_belt1","red_recovery_belt2"}
  if not plan.sites[keys[1]] then
    if input.get_item_count("automation-science-pack")>=2
        or old.status~=defines.entity_status.no_minable_resources then return nil end
    if old.direction~=defines.direction.north then
      plan.red_recovery_reason="unsupported_depleted_copper_orientation";return nil
    end
    local target=resolve(npc,"copper_ore_belt1");local drop=old.drop_position
    if not target or target.direction~=defines.direction.north
        or math.floor(target.position.x)~=math.floor(drop.x)
        or math.floor(target.position.y)~=math.floor(drop.y) then
      plan.red_recovery_reason="owned_copper_conveyor_port_required";return nil
    end
    local x,y=old.position.x-2,old.position.y
    local amount=0
    for _,e in ipairs(npc.character.surface.find_entities_filtered{name="copper-ore",
        area={{x-1,y-1},{x+1,y+1}}}) do amount=amount+e.amount end
    if amount<=0 then plan.red_recovery_reason="adjacent_native_copper_exhausted";return nil end
    local trial={sites=copy(plan.sites),new_keys={}}
    local ok,reason=pcall(function()
      site(npc,trial,keys[1],"burner-mining-drill",x,y,defines.direction.north)
      site(npc,trial,keys[2],"transport-belt",target.position.x-2,target.position.y,defines.direction.east)
      site(npc,trial,keys[3],"transport-belt",target.position.x-1,target.position.y,defines.direction.east)
    end)
    if not ok then plan.red_recovery_reason=tostring(reason);return nil end
    for _,key in ipairs(keys) do plan.sites[key]=trial.sites[key] end
    plan.red_recovery={started_tick=game.tick,observed_native_ore=amount,
      original_drill_unit=old.unit_number,minimum_items={['burner-mining-drill']=1,['transport-belt']=2},
      ordinary_jobs_only=true,manual_red_component_injection=false}
  end
  for _,key in ipairs(keys) do
    local spec=plan.sites[key];local entity=resolve(npc,key)
    if not entity then
      if carried(npc,spec.item)<1 then
        -- The drill's recursively crafted furnace needs five actual stone.
        if spec.item=="burner-mining-drill" and carried(npc,"stone")<5 then
          return {kind="mine",item="stone",count=5-carried(npc,"stone"),search_radius=128}
        end
        return funded_craft(npc,spec.item,1)
      end
      return {kind="build",entity_name=spec.name,item=spec.item,position=spec.position,direction=spec.direction}
    end
    if spec.name=="transport-belt" and entity.direction~=spec.direction then
      return {kind="configure_direction",target=entity,direction=spec.direction}
    end
  end
  local drill=resolve(npc,keys[1]);local drop=drill.drop_position;local belt=resolve(npc,keys[2])
  if math.floor(drop.x)~=math.floor(belt.position.x) or math.floor(drop.y)~=math.floor(belt.position.y) then
    plan.red_recovery_reason="native_recovery_drop_port_mismatch";return nil
  end
  if drill.get_fuel_inventory().get_item_count("coal")<1 then
    if carried(npc,"coal")<4 then
      return Capacity.coal_withdraw(npc,{resolve=resolve},16) or {kind="mine",item="coal",count=16,search_radius=128}
    end
    return {kind="transfer",target=drill,item="coal",count=4,mode="deposit",inventory=defines.inventory.fuel}
  end
  plan.red_recovery.native_drill_unit=drill.unit_number
  plan.red_recovery.native_port_verified=true
  plan.red_recovery_reason=drill.status==defines.entity_status.no_minable_resources
    and "recovery_native_copper_exhausted" or nil
end
local function blue_upkeep(npc)
  local assembler=resolve(npc,"blue_assembler");local engine=resolve(npc,"engine_assembler");local lab=resolve(npc,"lab")
  if not assembler or not engine or not lab then return nil end
  local recipe=assembler.get_recipe();if not recipe or recipe.name~="chemical-science-pack" then return nil end
  local lab_input=lab.get_inventory(defines.inventory.lab_input)
  -- Keep the native green line supplied while a later technology consumes
  -- both green and blue. Its finished packs travel to this lab on belts.
  if lab_input.get_item_count("chemical-science-pack")>=2
      and lab_input.get_item_count("logistic-science-pack")==0 then
    local green_ops={copy=copy,site=site,resolve=resolve,funded_craft=funded_craft,
      deposit_input=deposit_input,withdraw=withdraw,carried=carried}
    local green_job=Capacity.green_links_ready(npc,green_ops)and Capacity.green_components(npc,green_ops)
      or Advanced.maintenance(npc,{kind="technology",technology="rocket-silo"})
    if green_job then return green_job end
  end
  if lab_input.get_item_count("chemical-science-pack")<20 then
    if carried(npc,"chemical-science-pack")>0 then
      local job=deposit_input(npc,lab,defines.inventory.lab_input,"chemical-science-pack",20)
      if job then return job end
    end
    local job=withdraw(npc,"blue_assembler","chemical-science-pack",20);if job then return job end
  end
  if lab_input.get_item_count("chemical-science-pack")>=20 then return nil end
  local input=assembler.get_inventory(defines.inventory.crafter_input)
  local capacity_ops={copy=copy,site=site,resolve=resolve,funded_craft=funded_craft,
    deposit_input=deposit_input,withdraw=withdraw,carried=carried}
  local red_link=Capacity.red_link_ready(npc,capacity_ops)
  -- Required native blue deficits precede optional component-buffer fills.
  if red_link and input.get_item_count("advanced-circuit")<3 then
    local job=Capacity.red_circuits(npc,capacity_ops);if job then return job end
  end
  local bridge=engine_bridge_ready(npc)
  -- Native inserters keep only two blue recipe cycles of engines buffered.
  -- Fund circuits and sulfur first when the engine belt is installed; the
  -- belt refills engines as each cycle frees an input slot.
  local bound=npc.factory_project.plan.sites.engine_bridge_output
    or npc.factory_project.plan.sites.engine_bridge_belt or npc.factory_project.plan.sites.engine_bridge_input
  if bound and not bridge then
    npc.factory_project.plan.engine_bridge_reason=npc.factory_project.plan.engine_bridge_reason or "bound_engine_bridge_not_ready"
    return nil
  end
  local entries={{"sulfur",2,4},{"engine-unit",4,4},{"advanced-circuit",3,6}}
  for _,entry in ipairs(entries) do
    local item,minimum,target=entry[1],entry[2],entry[3]
    if not (item=="advanced-circuit" and red_link) and input.get_item_count(item)<minimum then
      if carried(npc,item)>0 and not (bridge and item=="engine-unit") then
        local job=deposit_input(npc,assembler,defines.inventory.crafter_input,item,target)
        if job then return job end
      end
      if item=="advanced-circuit" then return funded_craft(npc,item,target)
      elseif item=="sulfur" then return withdraw(npc,"sulfur_plant",item,target)
      else
        if not bridge then
          local job=withdraw(npc,"engine_assembler",item,target);if job then return job end
        elseif engine.get_output_inventory().get_item_count(item)>=4 then
          return nil
        end
        local engine_input=engine.get_inventory(defines.inventory.crafter_input)
        for _,ingredient in ipairs({{"steel-plate",4},{"iron-gear-wheel",4},{"pipe",8}}) do
          local name,count=ingredient[1],ingredient[2]
          if engine_input.get_item_count(name)<count then
            if carried(npc,name)>0 then
              local supply=deposit_input(npc,engine,defines.inventory.crafter_input,name,count)
              if supply then return supply end
            end
            if name=="steel-plate" then return Advanced.maintenance(npc,{kind="inventory",item=name,count=count,source_site="steel_furnace"}) end
            return funded_craft(npc,name,count)
          end
        end
      end
    end
  end
end
function Oil.maintenance(npc,condition,external_research)
  if not npc.character or not npc.character.valid or npc.character.force~=npc.force
      or not npc.factory_project or not npc.factory_project.plan then return nil end
  if not npc.factory_project or (not external_research and npc.factory_project.id~="oil_processing"
      and npc.factory_project.id~="chemical_science") then return nil end
  -- Prioritize generation over component work so green research cannot strand
  -- itself while gathering materials. Native mining is the only fuel source.
  local boiler=resolve(npc,"boiler");local fuel=boiler and boiler.get_fuel_inventory()
  if fuel and fuel.get_item_count("coal")<8 then
    if carried(npc,"coal")<24 then
      return Capacity.coal_withdraw(npc,{resolve=resolve},32)
        or {kind="mine",item="coal",count=32,search_radius=128}
    end
    return {kind="transfer",target=boiler,item="coal",count=24,mode="deposit"}
  end
  local plastic=resolve(npc,"plastic_plant")
  if plastic and plastic.get_recipe() then
    local input=plastic.get_inventory(defines.inventory.crafter_input)
    if input.get_item_count("coal")<2 then
      if carried(npc,"coal")<8 then
        return Capacity.coal_withdraw(npc,{resolve=resolve},16)
          or {kind="mine",item="coal",count=16,search_radius=128}
      end
      return {kind="transfer",target=plastic,item="coal",count=8,mode="deposit",inventory=defines.inventory.crafter_input}
    end
  end
  local key=condition and condition.source_site
  if key=="brick_furnace" then
    local furnace=resolve(npc,key);if not furnace then return nil end
    local deficit=math.max(0,(condition.count or 1)-carried(npc,"stone-brick"));local out=furnace.get_output_inventory().get_item_count("stone-brick")
    if out>0 and deficit>0 then return {kind="transfer",target=furnace,item="stone-brick",count=math.min(out,deficit),mode="withdraw"} end
    if furnace.get_fuel_inventory().get_item_count("coal")<1 then
      if carried(npc,"coal")<8 then return {kind="mine",item="coal",count=16,search_radius=128} end
      return {kind="transfer",target=furnace,item="coal",count=8,mode="deposit"}
    end
    if furnace.get_inventory(defines.inventory.crafter_input).get_item_count("stone")<2 then
      if carried(npc,"stone")<20 then return {kind="mine",item="stone",count=20,search_radius=128} end
      return {kind="transfer",target=furnace,item="stone",count=20,mode="deposit",inventory=defines.inventory.crafter_input}
    end
  elseif key=="plastic_plant" or key=="sulfur_plant" or key=="engine_assembler" or key=="blue_assembler" then
    local entity=resolve(npc,key);if not entity then return nil end
    local item=condition.item;local deficit=math.max(0,(condition.count or 1)-carried(npc,item))
    local out=entity.get_output_inventory().get_item_count(item)
    if out>0 and deficit>0 then return {kind="transfer",target=entity,item=item,count=math.min(out,deficit),mode="withdraw"} end
  end
  local capacity_ops={copy=copy,site=site,resolve=resolve,
    funded_craft=funded_craft,carried=carried,deposit_input=deposit_input}
  local plan=npc.factory_project.plan
  if plan.sites.capacity_coal_link and not resolve(npc,"capacity_coal_buffer") then
    local coal_build=Capacity.coal_drill(npc,capacity_ops)
    if coal_build then return coal_build end
  end
  -- Only after the explicit construction ledger has finished may ongoing
  -- component logistics spend carried stock. Native recipes remain unchanged.
  if external_research or (npc.factory_project.status=="complete" and npc.factory_project.id=="chemical_science") then
    local red=red_mining_recovery(npc)
    if red then return red end
    local blue=resolve(npc,"blue_assembler");local lab=resolve(npc,"lab")
    if blue and lab and lab.get_inventory(defines.inventory.lab_input).get_item_count("chemical-science-pack")<20 then
      local input=blue.get_inventory(defines.inventory.crafter_input)
      if input.get_item_count("sulfur")<2 then
        local sulfur=deposit_input(npc,blue,defines.inventory.crafter_input,"sulfur",4)
          or withdraw(npc,"sulfur_plant","sulfur",4)
        if sulfur then return sulfur end
      end
      local bridge=engine_bridge(npc);if bridge then return bridge end
    end
    local science=blue_upkeep(npc)
    if science then return science end
    return Capacity.coal_drill(npc,capacity_ops) or Capacity.ore_furnaces(npc,capacity_ops)
  end
  return nil
end
-- Later stages reuse the physically owned chemical line for blue research.
-- The caller schedules these ordinary Worker jobs while its plan waits on a
-- technology; this function never inserts stock or changes native recipes.
function Oil.research_maintenance(npc)
  return Oil.maintenance(npc,nil,true)
end
return Oil
