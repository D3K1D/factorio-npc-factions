-- Dedicated native circuit production. Plates/plastic/fuel are still supplied
-- by ordinary Worker jobs; conveyor components and outputs are never inserted.
local Components={revision="dedicated-circuits-v1"}
local RECIPES={rc_cable_green="copper-cable",rc_green="electronic-circuit",
  rc_cable_red="copper-cable",rc_red="advanced-circuit"}
local RECIPE_ORDER={"rc_cable_green","rc_cable_red","rc_green","rc_red"}
local OUTPUTS={['electronic-circuit']="rc_green_buffer",['advanced-circuit']="rc_red_buffer"}
local function state(npc)
  local p=npc.factory_project
  if not p or p.id~="rocket_materials" or not p.plan or not npc.character or not npc.character.valid
      or npc.character.force~=npc.force then return nil,"component_project_scope_revoked" end
  return p.plan,p
end
local function valid(npc,ops,key)
  local plan=state(npc);local s=plan and plan.sites[key]
  if not s or s.surface_index~=npc.character.surface.index then return nil end
  local e=ops.resolve(npc,key)
  if e and e.valid and e.force==npc.force and e.surface==npc.character.surface and e.name==s.name
      and s.position and math.abs(e.position.x-s.position.x)<0.15
      and math.abs(e.position.y-s.position.y)<0.15
      and (not s.owned_unit_number or e.unit_number==s.owned_unit_number) then return e end
end
local function count(inv,item)
  return inv and inv.get_item_count{name=item,quality="normal"} or 0
end
local function ready(npc,ops,c)
  for _,key in ipairs(c.keys) do
    local e=valid(npc,ops,key);local s=c.sites[key]
    if not e then return false,"component_asset_scope_revoked:"..key end
    if (e.type=="transport-belt" or e.type=="inserter") and e.direction~=s.direction then
      return false,"component_native_direction_mismatch:"..key
    end
  end
  for _,key in ipairs(RECIPE_ORDER) do
    local name=RECIPES[key]
    local e=valid(npc,ops,key);local r=e and e.get_recipe();local recipe=npc.force.recipes[name]
    if not r or r.name~=name or not recipe or not recipe.enabled then return false,"component_recipe_scope_revoked:"..key end
  end
  return true
end
function Components.plan(npc,ops)
  local plan,reason=state(npc);if not plan then return nil,reason end
  local o=plan.rocket_origin;local anchor=plan.sites.rocket_engine_pole1a
  if not o or not anchor then return nil,"component_earned_rocket_layout_required" end
  for _,key in ipairs(RECIPE_ORDER) do
    local r=RECIPES[key]
    local recipe=npc.force.recipes[r]
    if not recipe or not recipe.enabled then return nil,"component_recipe_locked:"..r end
  end
  local x,y=o.x-30,o.y+38
  local trial={sites=ops.copy(plan.sites),new_keys={}};local keys={}
  local function absolute(key,name,px,py,d)
    ops.site(npc,trial,key,name,px,py,d)
    if not trial.sites[key] then error("component_reserved_site_alias:"..key,0) end
    keys[#keys+1]=key
  end
  local function s(key,name,dx,dy,d) absolute(key,name,x+dx,y+dy,d) end
  local ok,err=pcall(function()
    s("rc_cable_green","assembling-machine-1",-6,0)
    s("rc_green","assembling-machine-1",0,0)
    s("rc_cable_red","assembling-machine-1",12,-6)
    s("rc_red","assembling-machine-1",12,2)
    s("rc_green_buffer","wooden-chest",11,4)
    s("rc_red_buffer","wooden-chest",19,2)
    for _,q in ipairs({{"rc_cable_green_out",-4,0,12},{"rc_cable_green_in",-2,0,12},
        {"rc_green_out",2,0,12},{"rc_green_buffer_in",10,4,12},{"rc_green_red_in",10,2,12},
        {"rc_cable_red_out",12,-4,0},{"rc_cable_red_in",12,0,0},
        {"rc_red_out",14,2,12},{"rc_red_buffer_in",18,2,12}}) do s(q[1],"inserter",q[2],q[3],q[4]) end
    s("rc_cable_green_belt","transport-belt",-3,0,4)
    for n=3,9 do s("rc_green_belt"..n,"transport-belt",n,0,n==9 and 8 or 4) end
    for n=1,4 do s("rc_green_down"..n,"transport-belt",9,n,8) end
    for n=-3,-1 do s("rc_cable_red_belt"..n,"transport-belt",12,n,8) end
    for n=15,17 do s("rc_red_belt"..n,"transport-belt",n,2,4) end
    for _,q in ipairs({{-3,-1},{3,-1},{7,-3},{10,3},{14,-3},{14,-1},{14,4},{17,3}}) do
      s("rc_pole_"..q[1].."_"..q[2],"small-electric-pole",q[1],q[2])
    end
    local ax,ay=anchor.position.x,anchor.position.y
    for yy=ay+6,y-4,6 do absolute("rc_power_y"..yy,"small-electric-pole",ax,yy) end
    for xx=ax-6,x-6,-6 do absolute("rc_power_x"..xx,"small-electric-pole",xx,y-4) end
  end)
  if not ok then return nil,tostring(err) end
  local result={revision=Components.revision,origin={x=x,y=y},keys=keys,
    sites={},phase="construction",issued_sequence=0,receipt_ids={},actual_receipts={},
    ordinary_jobs_only=true,manual_plate_plastic_feed=true,continuous_raw_pipeline=false,
    no_blue_buffer_withdrawal=true,started_tick=game.tick}
  for _,key in ipairs(result.keys) do result.sites[key]=trial.sites[key] end
  return result
end
local function issue(c,job,purpose)
  if not job then return nil end
  c.issued_sequence=c.issued_sequence+1
  c.issued={sequence=c.issued_sequence,kind=job.kind,item=job.item,recipe=job.recipe,
    target_unit=job.target and job.target.valid and job.target.unit_number,purpose=purpose,tick=game.tick}
  return job
end
local function clear(npc,spec)
  local b=prototypes.entity[spec.name].collision_box
  local a,z=b.left_top or b[1],b.right_bottom or b[2];local p=spec.position
  local entities=npc.character.surface.find_entities_filtered{area={{p.x+(a.x or a[1]),p.y+(a.y or a[2])},
    {p.x+(z.x or z[1]),p.y+(z.y or z[2])}},type={"tree","simple-entity"}}
  table.sort(entities,function(a,b)
    if a.position.x~=b.position.x then return a.position.x<b.position.x end
    if a.position.y~=b.position.y then return a.position.y<b.position.y end
    if a.name~=b.name then return a.name<b.name end
    return (a.unit_number or 0)<(b.unit_number or 0)
  end)
  for _,e in ipairs(entities) do if e.force.name=="neutral" and e.minable then
    for _,product in ipairs(e.prototype.mineable_properties.products or {}) do
      if product.type=="item" and product.amount and (not product.probability or product.probability==1) then
        return {kind="mine",target=e,item=product.name,count=product.amount}
      end
    end
  end end
end
function Components.next(npc,ops)
  local plan,reason=state(npc);if not plan then return nil,reason end
  for _,key in ipairs(RECIPE_ORDER) do
    local r=npc.force.recipes[RECIPES[key]]
    if not r or not r.enabled then return nil,"component_recipe_scope_revoked:"..key end
  end
  local c=plan.components
  if not c then
    c,reason=Components.plan(npc,ops)
    if not c then plan.component_blocker=reason;return nil,reason end
    plan.components=c;for key,s in pairs(c.sites) do plan.sites[key]=s end
  end
  for _,key in ipairs(c.keys) do
    local s=c.sites[key];local e=valid(npc,ops,key)
    if not e then
      if s.surface_index~=npc.character.surface.index then return nil,"component_surface_scope_revoked" end
      local occupied=npc.character.surface.find_entities_filtered{name=s.name,position=s.position,radius=.15}
      if #occupied>0 then return nil,"component_asset_scope_revoked:"..key end
      if s.clearance_required then local job=clear(npc,s);if job then return issue(c,job,"physical_clearance") end end
      if ops.carried(npc,s.item)<1 then return issue(c,ops.supply(npc,s.item,1),"funded_hardware") end
      return issue(c,{kind="build",entity_name=s.name,item=s.item,position=s.position,direction=s.direction},"native_construction")
    end
    s.owned_unit_number=e.unit_number
    if (s.name=="transport-belt" or s.name=="inserter") and e.direction~=s.direction then
      return issue(c,{kind="configure_direction",target=e,direction=s.direction},"native_direction_repair")
    end
  end
  for _,key in ipairs(RECIPE_ORDER) do
    local recipe=RECIPES[key]
    local e=valid(npc,ops,key);local r=e.get_recipe()
    if not r or r.name~=recipe then
      if not npc.force.recipes[recipe].enabled then return nil,"component_recipe_scope_revoked:"..recipe end
      return issue(c,{kind="configure_recipe",target=e,recipe=recipe,direction=c.sites[key].direction},"native_recipe")
    end
  end
  c.phase="native_production";plan.component_blocker=nil
  return Components.upkeep(npc,ops)
end
function Components.upkeep(npc,ops)
  local plan,reason=state(npc);local c=plan and plan.components
  if not c or c.phase~="native_production" then return nil,reason end
  local legal,why=ready(npc,ops,c);if not legal then return nil,why end
  c.buffer_targets=c.buffer_targets or {['electronic-circuit']=200,['advanced-circuit']=40}
  local green=count(valid(npc,ops,"rc_green_buffer").get_inventory(defines.inventory.chest),"electronic-circuit")
  local red=count(valid(npc,ops,"rc_red_buffer").get_inventory(defines.inventory.chest),"advanced-circuit")
  local need_red=red<c.buffer_targets['advanced-circuit']
  local need_green=green<c.buffer_targets['electronic-circuit'] or need_red
  -- These are raw item inputs only. Cable and green circuits travel through
  -- native inserters/belts; no manual intermediate deposit is permitted here.
  local entries={{"rc_cable_green","copper-plate",8,40},
      {"rc_green","iron-plate",8,40},{"rc_cable_red","copper-plate",8,40},
      {"rc_red","plastic-bar",4,24}}
  for offset=0,#entries-1 do
    local index=((c.feed_cursor or 1)-1+offset)%#entries+1
    local entry=entries[index]
    local key,item,minimum,target=entry[1],entry[2],entry[3],entry[4]
    local e=valid(npc,ops,key)
    if not e or not e.get_recipe() or e.get_recipe().name~=RECIPES[key]
        or not npc.force.recipes[RECIPES[key]].enabled then return nil,"component_native_recipe_scope_revoked" end
    local demand=(index<=2 and need_green) or (index>=3 and need_red)
    if demand and (e.get_output_inventory().is_empty() or e.status~=defines.entity_status.full_output) then
      local input=e.get_inventory(defines.inventory.crafter_input)
      if count(input,item)<minimum then
        if ops.carried(npc,item)>0 then
          c.feed_cursor=index%#entries+1
          return issue(c,{kind="transfer",target=e,item=item,count=math.min(target-count(input,item),ops.carried(npc,item)),
            mode="deposit",inventory=defines.inventory.crafter_input},"manual_raw_input")
        end
        local job=ops.supply(npc,item,target)
        if job then c.feed_cursor=index%#entries+1;return issue(c,job,"earned_raw_supply") end
        c.feed_blocker=item
      end
    end
  end
end
function Components.withdraw(npc,ops,item,needed)
  local plan,reason=state(npc);local c=plan and plan.components;local key=OUTPUTS[item]
  if not c or c.phase~="native_production" or not key then return nil,reason end
  local legal,why=ready(npc,ops,c);if not legal then return nil,why end
  local e=valid(npc,ops,key);local inv=e and e.get_inventory(defines.inventory.chest)
  local available=count(inv,item);local deficit=math.max(0,needed-ops.carried(npc,item))
  if available>0 and deficit>0 then return issue(c,{kind="transfer",target=e,item=item,
    count=math.min(available,deficit),mode="withdraw",inventory=defines.inventory.chest},"owned_native_output") end
end
function Components.bind(npc,job_id)
  local plan=state(npc);local c=plan and plan.components
  if c and c.issued and not c.issued.job_id then c.issued.job_id=job_id;return true end
  return false
end
function Components.on_receipt(npc,receipt)
  local plan=state(npc);local c=plan and plan.components;local issued=c and c.issued
  if not issued or issued.job_id~=(receipt.job_id or receipt.id) or c.receipt_ids[receipt.id] then return false end
  c.receipt_ids[receipt.id]=true;c.issued=nil
  local r={id=receipt.id,kind=receipt.kind,status=receipt.status,reason=receipt.reason,
    item=receipt.item,count=receipt.count,completed=receipt.completed,produced=receipt.produced,
    purpose=issued.purpose,sequence=issued.sequence,tick=game.tick}
  c.actual_receipts[#c.actual_receipts+1]=r
  if #c.actual_receipts>256 then table.remove(c.actual_receipts,1) end
  if #c.actual_receipts==256 then
    c.receipt_ids={};for _,x in ipairs(c.actual_receipts) do c.receipt_ids[x.id]=true end
  end
  return true
end
function Components.observe(npc,ops)
  local plan,reason=state(npc);local c=plan and plan.components
  if not c then return {active=false,reason=reason or (plan and plan.component_blocker)} end
  local out={active=true,revision=c.revision,phase=c.phase,origin=c.origin,issued=c.issued,
    buffer_targets=ops.copy(c.buffer_targets),feed_blocker=c.feed_blocker,
    actual_receipts=ops.copy(c.actual_receipts),manual_plate_plastic_feed=true,
    continuous_raw_pipeline=false,no_blue_buffer_withdrawal=true,sites={}}
  for _,key in ipairs(c.keys) do
    local e=valid(npc,ops,key);local s=c.sites[key]
    local a={name=s.name,position=s.position,valid=e~=nil}
    if e then
      a.unit_number=e.unit_number;a.force=e.force.name;a.surface_index=e.surface.index
      a.direction=e.direction;a.energy=e.energy;a.status=e.status
      if e.type=="assembling-machine" then
        local r=e.get_recipe();a.recipe=r and r.name;a.products_finished=e.products_finished
        a.input=e.get_inventory(defines.inventory.crafter_input).get_contents()
        a.output=e.get_output_inventory().get_contents()
      elseif e.type=="container" then a.contents=e.get_inventory(defines.inventory.chest).get_contents()
      elseif e.type=="transport-belt" then a.lanes={e.get_transport_line(1).get_contents(),e.get_transport_line(2).get_contents()}
      elseif e.type=="inserter" then a.pickup=e.pickup_position;a.drop=e.drop_position end
    end
    out.sites[key]=a
  end
  return out
end
return Components
