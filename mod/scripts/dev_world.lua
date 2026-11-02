-- Opt-in private demonstration terrain and creative human observation.
-- Terrain and explicit private curriculum stock grants are dev setup;
-- subsequent NPC work still uses ordinary physical items and machines.
local Factory=require("scripts.factory")
local DriveRange=require("scripts.drive_range")
local DriveLab=require("scripts.drive_lab")
local Worker=require("scripts.worker")
local Behavior=require("scripts.behavior")
local Projects=require("scripts.factory_projects")
local Personality=require("scripts.personality")
local Society=require("scripts.society")
local Progression=require("scripts.progression")
local Combat=require("scripts.combat")
local Strategy=require("scripts.strategy_policy")
local Repairs=require("scripts.repairs")
local PlatformLaunch=require("scripts.platform_launch")
local Settlements=require("scripts.settlements")
local SocialFixture=require("scripts.social_fixture")
local Navigation=require("scripts.navigation")
local RailTrade=require("scripts.rail_trade_projects")
local Scorecard=require("scripts.scorecard")
local Agent=require("scripts.agent")
local Dev={}
local function state()
  storage.dev=storage.dev or {enabled=false,creative=false,bots=2,homes={}}
  return storage.dev
end
local prepare_oil_site
local function module_layout(home,id,d)
  local module={x=home.x+6,y=home.y}
  local directions={defines.direction.north,defines.direction.east,defines.direction.south,defines.direction.west}
  local ports={{x=0,y=-2},{x=2,y=0},{x=0,y=2},{x=-2,y=0}}
  local variant=d.varied_layout and ((id-1)%4+1) or 1
  return module,{x=module.x+ports[variant].x,y=module.y+ports[variant].y},directions[variant]
end
local function prepare_departure(surface,start,home)
  local dx,dy=home.x-start.x,home.y-start.y
  local steps=math.max(1,math.ceil(math.sqrt(dx*dx+dy*dy)))
  local tiles,seen={},{}
  for i=0,steps do
    local x,y=math.floor(start.x+dx*i/steps),math.floor(start.y+dy*i/steps)
    for ox=-3,3 do for oy=-3,3 do
      local key=(x+ox)..":"..(y+oy)
      if not seen[key] then seen[key]=true;tiles[#tiles+1]={name="grass-1",position={x+ox,y+oy}} end
    end end
    if i%12==0 or i==steps then
      for _,e in ipairs(surface.find_entities_filtered{position={x,y},radius=8,
          type={"tree","simple-entity","cliff"},force="neutral"}) do e.destroy() end
    end
    if i%64==0 or i==steps then
      for _,e in ipairs(surface.find_entities_filtered{position={x,y},radius=80,
          type={"unit","unit-spawner","turret"},force="enemy"}) do e.destroy() end
    end
  end
  -- Explicit private world setup, performed once before departure. Native
  -- enemies/expansion remain enabled elsewhere and future raids are untouched.
  surface.set_tiles(tiles)
end

local function prepare()
  local d=state(); if d.prepared then return end
  local surface=game.surfaces[1]
  surface.request_to_generate_chunks({0,0},6); surface.force_generate_chunk_requests()
  local area={{-168,-104},{169,105}}
  for _,entity in ipairs(surface.find_entities_filtered{area=area}) do
    if entity.type~="character" then entity.destroy() end
  end
  local tiles={}
  for x=-168,168 do for y=-104,104 do tiles[#tiles+1]={name="grass-1",position={x,y}} end end
  surface.set_tiles(tiles)
  surface.always_day=true
  d.homes={}; d.terrain_fixture=true
  for id=1,d.bots do
    local spacing=d.spacing or 64
    local vertical=spacing==64 and 128 or spacing
    local home={x=((id-1)%5-2)*spacing,y=math.floor((id-1)/5)*vertical-vertical/2}
    if spacing>64 then
      -- Wide tests prepare small declared camp islands, not one enormous
      -- cleared rectangle. Natural enemies remain outside these fixtures.
      surface.request_to_generate_chunks(home,3);surface.force_generate_chunk_requests()
      local camp={{home.x-32,home.y-48},{home.x+32,home.y+48}}
      for _,entity in ipairs(surface.find_entities_filtered{area=camp}) do
        if entity.type~="character" then entity.destroy() end
      end
      local ground={}
      for x=home.x-32,home.x+32 do for y=home.y-48,home.y+48 do
        ground[#ground+1]={name="grass-1",position={x,y}}
      end end
      surface.set_tiles(ground);game.forces.player.chart(surface,camp)
    end
    d.homes[id]=home
    surface.create_entity{name="tree-01",position={home.x-8,home.y-6}}
    local coal=d.scenario=="coal-far" and {x=home.x-30,y=home.y+24} or {x=home.x-8,y=home.y+6}
    surface.create_entity{name="coal",position=coal,amount=50000}
    for dx=-2,2 do for dy=-2,2 do
      surface.create_entity{name="iron-ore",position={home.x+6+dx,home.y+dy},amount=5000}
    end end
    if d.factory_growth then
      -- Declared construction terrain fixture. No extra inventory, machines,
      -- research flags or power are supplied to the engineers.
      for dx=-1,1 do for dy=-1,1 do
        surface.create_entity{name="copper-ore",position={home.x-12+dx,home.y-4+dy},amount=5000}
        surface.create_entity{name="stone",position={home.x-12+dx,home.y+12+dy},amount=5000}
      end end
      if d.advanced_growth then
        for x=home.x-6,home.x-2 do for y=home.y-2,home.y+2 do
          surface.create_entity{name="iron-ore",position={x,y},amount=5000}
        end end
      end
      for i=1,5 do surface.create_entity{name="tree-01",position={home.x-15-i*2,home.y-10}} end
      local water={}
      for x=home.x+15,home.x+23 do for y=home.y+14,home.y+22 do
        water[#water+1]={name="water",position={x=x,y=y}}
      end end
      surface.set_tiles(water)
      if d.advanced_growth then prepare_oil_site(surface,home) end
    end
    if d.scenario=="obstructed" then
      local _,furnace=module_layout(home,id,d)
      surface.create_entity{name="big-rock",position=furnace}
    end
    rendering.draw_text{text="Camp "..id,target={x=home.x,y=home.y-10},surface=surface,
      color={r=0.85,g=0.9,b=1},alignment="center",scale=1.6}
  end
  game.forces.player.chart(surface,area)
  d.prepared=true
end

-- Explicit private terrain curriculum for oil, including resumed economy
-- checkpoints. No machines, items, research, power or fluid are supplied.
prepare_oil_site=function(surface,home)
  local area={{home.x+26,home.y-8},{home.x+62,home.y+36}}
  surface.request_to_generate_chunks({home.x+44,home.y+6},2);surface.force_generate_chunk_requests()
  for _,e in ipairs(surface.find_entities_filtered{area=area,force="neutral",type={"tree","simple-entity","cliff"}}) do e.destroy() end
  local tiles={}
  for x=home.x+26,home.x+62 do for y=home.y-8,home.y+36 do
    tiles[#tiles+1]={name="grass-1",position={x,y}}
  end end
  surface.set_tiles(tiles)
  local position={x=home.x+34.5,y=home.y+4.5}
  local existing=surface.find_entity("crude-oil",position)
  local resource=existing or surface.create_entity{name="crude-oil",position=position,amount=500000}
  if not resource then return {ok=false,reason="oil_resource_creation_failed"} end
  for i=1,12 do
    local tree_position={home.x+27+i*2,home.y-7}
    if surface.can_place_entity{name="tree-01",position=tree_position} then
      surface.create_entity{name="tree-01",position=tree_position}
    end
  end
  state().oil_terrain_fixture=true
  return {ok=true,terrain_fixture=true,crude_position=resource.position,amount=resource.amount,
    inventory_research_or_power_grants=false}
end

local function construction_snapshot(npc)
  local project=npc.factory_project
  if not project then return nil end
  if not (npc.character and npc.character.valid) then
    return {id=project.id,status=project.status,index=project.index,
      steps=project.plan and #project.plan.steps or 0,body_valid=false,
      failures=npc.failures or {},reason="actor_unavailable"}
  end
  local sites={}
  for key,_ in pairs(project.plan.sites or {}) do
    local e=Projects.resolve_site(npc,key)
    if e then
      local value={name=e.name,position=e.position,energy=e.energy,status=e.status}
      if e.type=="furnace" or e.type=="assembling-machine" then value.products_finished=e.products_finished end
      if e.type=="assembling-machine" then local r=e.get_recipe(); value.recipe=r and r.name end
      local output=e.get_output_inventory()
      if output and output.valid then value.output={}
        for i=1,#output do local stack=output[i]
          if stack.valid_for_read then value.output[stack.name]=(value.output[stack.name] or 0)+stack.count end
        end
      end
      if e.type=="container" then value.inventory={}
        local chest=e.get_inventory(defines.inventory.chest)
        for i=1,#chest do local stack=chest[i]
          if stack.valid_for_read then value.inventory[stack.name]=(value.inventory[stack.name] or 0)+stack.count end
        end
      end
      if e.fluids_count>0 then
        value.fluids={}
        for i=1,e.fluids_count do local f=e.get_fluid(i) if f then value.fluids[#value.fluids+1]=f end end
      end
      sites[key]=value
    end
  end
  local inventory={}
  local inv=npc.character.get_main_inventory()
  for i=1,#inv do local stack=inv[i]
    if stack.valid_for_read then inventory[stack.name]=(inventory[stack.name] or 0)+stack.count end
  end
  local tech={}
  for _,name in ipairs({"steam-power","electronics","automation-science-pack","automation","logistics",
      "logistic-science-pack","automation-2","steel-processing","electric-mining-drill",
      "engine","fluid-handling","oil-gathering","oil-processing","sulfur-processing","plastics",
      "advanced-circuit","chemical-science-pack","advanced-oil-processing","rocket-silo"}) do
    local t=npc.force.technologies[name]; tech[name]=t and t.researched or false
  end
  local statistics=npc.force.get_item_production_statistics(npc.character.surface)
  local assembly_transfers,lab_manual_science,hand_science=0,0,0
  for _,receipt in pairs(npc.receipts or {}) do
    if receipt.status=="complete" then
      if receipt.kind=="craft" and receipt.recipe=="automation-science-pack" then
        hand_science=hand_science+(receipt.produced["automation-science-pack"] or 0)
      elseif receipt.kind=="transfer" and receipt.target and receipt.target.valid then
        if receipt.target.type=="assembling-machine" then assembly_transfers=assembly_transfers+receipt.count end
        if receipt.target.type=="lab" and receipt.item=="automation-science-pack" and receipt.mode=="deposit" then
          lab_manual_science=lab_manual_science+receipt.count
        end
      end
    end
  end
  local failures={}
  for i=math.max(1,#(npc.failures or {})-127),#(npc.failures or {}) do
    local fact=npc.failures[i];local value={}
    for k,v in pairs(fact) do value[k]=v end
    local receipt=npc.receipts and npc.receipts[fact.job_id]
    value.item=value.item or receipt and receipt.item
    value.actual_count=receipt and receipt.count
    failures[#failures+1]=value
  end
  local completed={}
  for id,_ in pairs(npc.completed_projects or {}) do completed[#completed+1]=id end
  table.sort(completed)
  return {id=project.id,status=project.status,index=project.index,steps=#project.plan.steps,
    completed_projects=completed,
    reason=project.reason,last_problem=project.last_problem,supply_blocker=project.supply_blocker,
    maintenance_source=project.maintenance_source,pending_maintenance=project.pending_maintenance,
    rocket_maintenance=project.rocket_maintenance,
    rocket_economy=Projects.observe_rocket(npc),
    sites=sites,inventory=inventory,
    technologies=tech,
    science_produced=statistics.get_input_count{name="automation-science-pack",quality="normal"},
    science_consumed=statistics.get_output_count{name="automation-science-pack",quality="normal"},
    green_produced=statistics.get_input_count{name="logistic-science-pack",quality="normal"},
    green_consumed=statistics.get_output_count{name="logistic-science-pack",quality="normal"},
    blue_produced=statistics.get_input_count{name="chemical-science-pack",quality="normal"},
    blue_consumed=statistics.get_output_count{name="chemical-science-pack",quality="normal"},
    hand_science_produced=hand_science,lab_manual_science=lab_manual_science,
    assembly_manual_transfers=assembly_transfers,starter_grants=npc.grants,
    research=npc.force.current_research and npc.force.current_research.name,
    research_progress=npc.force.research_progress,failures=failures}
end

function Dev.spawn_roster(player)
  local d=state(); if not d.enabled then return false end
  if d.native_starts then return false end
  prepare()
  local surface=game.surfaces[1]
  storage.founding_force=player.force
  player.teleport({0,0},surface)
  local spawned=0
  for id=1,d.bots do
    local angle=(id-1)*2*math.pi/d.bots+math.random()*0.12
    local spawn={x=math.cos(angle)*(12+id),y=math.sin(angle)*(12+id)}
    local home=d.homes[id]
    Settlements.generate_route(surface,spawn,home)
    if d.safe_departure and (d.spacing or 64)>64 then
      prepare_departure(surface,spawn,home);d.departure_terrain_fixture=true
    end
    local npc,reason=Factory.spawn(id,surface,spawn,home,player.force)
    if npc then
      npc.module,npc.furnace_position,npc.module_direction=module_layout(home,id,d)
      local ok,job_id=Worker.enqueue(npc,{id="dev-walk-home",kind="move",position=home})
      if not ok then error("dev walk enqueue: "..tostring(job_id)) end
      npc.pending_job=job_id; npc.moving_home=true
      spawned=spawned+1
    else error("dev NPC "..id..": "..tostring(reason)) end
  end
  storage.pending_founder=nil
  log("NPC_DEV_ROSTER "..spawned)
  return true
end

function Dev.enable_observer(player)
  local d=state(); if not (d.enabled and d.creative) then return end
  player.admin=true
  if player.controller_type~=defines.controllers.editor then player.toggle_map_editor() end
  player.cheat_mode=true; player.zoom=0.65
  game.tick_paused=false
  if player.gui.top.npc_dev_panel then player.gui.top.npc_dev_panel.destroy() end
  if player.gui.screen.npc_dev_panel then player.gui.screen.npc_dev_panel.destroy() end
  local panel=player.gui.screen.add{type="frame",name="npc_dev_panel",direction="vertical",caption="Watch the NPC engineers"}
  panel.location={x=370,y=40}
  local buttons=panel.add{type="table",column_count=5}
  for id=1,d.bots do
    local npc=storage.npcs[id]
    buttons.add{type="button",name="npc_dev_watch_"..id,caption=npc and npc.name or ("NPC "..id),
      tooltip="Move your camera to this engineer",tags={npc_dev_watch=id}}
  end
  panel.add{type="label",caption="WASD: fly  |  Click a name: watch that bot  |  Editor toolbar: create items"}
  player.print("Creative editor is on. "..d.bots.." separate camps are marked on the map. Click an engineer's name to watch them.")
end

local function watch(player,id)
  local npc=storage.npcs[id]
  if not npc or not npc.character or not npc.character.valid then return false end
  player.teleport(npc.character.position,npc.character.surface); player.zoom=0.85
  return true
end

local CASES={
  ["fuel-empty"]=true, ["distant-coal"]=true, ["full-output"]=true,
  ["hand-feed"]=true, ["ore-starved"]=true, ["balanced"]=true,
}
local TRAITS={"resource_conservatism","expansion_drive","cooperation","risk_tolerance"}

local function clamp(value,low,high) return math.max(low,math.min(high,value)) end
local function amount(seed,id,channel,span)
  return (seed+id*1009+channel*7919)%span
end
local function normal(name) return {name=name,quality="normal"} end
local function clear_normal(inventory,name)
  local count=inventory.get_item_count(normal(name))
  if count>0 then return inventory.remove{name=name,quality="normal",count=count} end
  return 0
end
local function put(inventory,name,count)
  if count<=0 then return 0 end
  local inserted=inventory.insert{name=name,quality="normal",count=count}
  if inserted~=count then error("curriculum inventory did not fit: "..name) end
  return inserted
end

local function training_targets(value)
  local d=state()
  local ids={}
  if value.npc_id then ids[1]=value.npc_id
  else for id=1,d.bots do ids[#ids+1]=id end end
  local targets,busy_ids={},{}
  for _,id in ipairs(ids) do
    local npc=storage.npcs[id]
    if not npc or not npc.character or not npc.character.valid or npc.step<=8
        or not npc.miner or not npc.miner.valid or not npc.furnace or not npc.furnace.valid
        or npc.miner.force~=npc.character.force or npc.furnace.force~=npc.character.force then
      return nil,"requires_bootstrapped_owned_camp:"..id
    end
    if npc.current_job or npc.pending_job or (npc.jobs and #npc.jobs>0)
        or npc.ai_decision or npc.ai_running then busy_ids[#busy_ids+1]=id end
    local main=npc.character.get_main_inventory()
    local miner_fuel=npc.miner.get_fuel_inventory()
    local furnace_fuel=npc.furnace.get_fuel_inventory()
    local source=npc.furnace.get_inventory(defines.inventory.crafter_input)
    local output=npc.furnace.get_output_inventory()
    if not (main and miner_fuel and furnace_fuel and source and output) then
      return nil,"machine_inventory_missing:"..id
    end
    targets[#targets+1]={npc=npc,main=main,miner_fuel=miner_fuel,
      furnace_fuel=furnace_fuel,source=source,output=output}
  end
  if #busy_ids>0 then return nil,"training_actors_busy",busy_ids end
  return targets
end

local function reset_coal_patch(npc,case,seed)
  local home=npc.home
  local surface=npc.character.surface
  -- Cells are smaller than the dev camp spacing, so another camp's patch is untouched.
  local area={{home.x-31,home.y-48},{home.x+31,home.y+48}}
  for _,entity in ipairs(surface.find_entities_filtered{area=area,name="coal",force="neutral"}) do
    entity.destroy()
  end
  local far=case=="distant-coal"
  local position={x=home.x-(far and 25 or 8)-amount(seed,npc.id,1,7),
    y=home.y+(far and 18 or 4)+amount(seed,npc.id,2,7)}
  local coal=surface.create_entity{name="coal",position=position,amount=50000}
  if not coal then error("curriculum coal patch placement failed") end
  return position
end

local function set_case(target,case,seed)
  local npc=target.npc
  local main,miner_fuel,furnace_fuel,source,output=
    target.main,target.miner_fuel,target.furnace_fuel,target.source,target.output
  -- Private fixture setup explicitly removes prior stock and voids active fuel.
  local removed={carried={}}
  for _,name in ipairs({"coal","iron-ore","iron-plate"}) do
    removed.carried[name]=clear_normal(main,name)
  end
  removed.miner_fuel_coal=miner_fuel.get_item_count(normal("coal"))
  removed.furnace_fuel_coal=furnace_fuel.get_item_count(normal("coal"))
  removed.furnace_input_ore=source.get_item_count(normal("iron-ore"))
  removed.furnace_output_plates=output.get_item_count(normal("iron-plate"))
  removed.miner_active_fuel=npc.miner.burner.currently_burning~=nil
  removed.furnace_active_fuel=npc.furnace.burner.currently_burning~=nil
  removed.previous_health=npc.character.health
  local miner_output=npc.miner.get_output_inventory()
  if miner_output and miner_output.valid then
    removed.miner_output_ore=miner_output.get_item_count(normal("iron-ore"))
    miner_output.clear()
  end
  npc.miner.burner.currently_burning=nil
  npc.furnace.burner.currently_burning=nil
  -- The active item is voided first; the explicit zero also documents the
  -- intended fixture state (the API ignores this write if no item is burning).
  npc.miner.burner.remaining_burning_fuel=0
  npc.furnace.burner.remaining_burning_fuel=0
  miner_fuel.clear(); furnace_fuel.clear(); source.clear(); output.clear()
  if npc.miner.mining_progress~=nil then npc.miner.mining_progress=0 end
  npc.furnace.crafting_progress=0

  local grants={coal=0,iron_ore=0,iron_plate=0,miner_coal=0,
    furnace_coal=0,furnace_ore=0,furnace_output_plates=0}
  local jitter=amount(seed,npc.id,3,3)
  if case=="fuel-empty" then
    grants.coal=put(main,"coal",3+jitter)
    grants.furnace_ore=put(source,"iron-ore",2+jitter)
  elseif case=="distant-coal" then
    grants.coal=put(main,"coal",amount(seed,npc.id,4,2))
    grants.furnace_ore=put(source,"iron-ore",2+jitter)
  elseif case=="full-output" then
    grants.coal=put(main,"coal",2+jitter)
    grants.miner_coal=put(miner_fuel,"coal",2)
    grants.furnace_coal=put(furnace_fuel,"coal",2)
    grants.furnace_ore=put(source,"iron-ore",3+jitter)
    grants.furnace_output_plates=put(output,"iron-plate",output.get_insertable_count(normal("iron-plate")))
  elseif case=="hand-feed" then
    grants.iron_ore=put(main,"iron-ore",8+jitter)
    grants.coal=put(main,"coal",24+jitter)
    grants.miner_coal=put(miner_fuel,"coal",2)
    grants.furnace_coal=put(furnace_fuel,"coal",2)
  elseif case=="ore-starved" then
    grants.coal=put(main,"coal",24+jitter)
    grants.miner_coal=put(miner_fuel,"coal",2)
    grants.furnace_coal=put(furnace_fuel,"coal",2)
  elseif case=="balanced" then
    grants.coal=put(main,"coal",24+jitter)
    grants.miner_coal=put(miner_fuel,"coal",2+jitter)
    grants.furnace_coal=put(furnace_fuel,"coal",2+jitter)
    grants.furnace_ore=put(source,"iron-ore",3+jitter)
  end
  local coal_position=reset_coal_patch(npc,case,seed)
  -- A fixed, collision-free camp location removes the prior provider's travel
  -- outcome from the next episode. The seed changes it reproducibly.
  local start_position={x=npc.home.x-5+amount(seed,npc.id,11,3)-1,
    y=npc.home.y-8+amount(seed,npc.id,12,3)-1}
  if not npc.character.teleport(start_position) then
    error("curriculum character start position blocked")
  end
  npc.last_position={x=npc.character.position.x,y=npc.character.position.y}
  Behavior.refresh(npc) -- Rebuild role baseline before applying seed offsets.
  local base_profile={}
  for _,key in ipairs(TRAITS) do base_profile[key]=npc.profile[key] end
  for index,key in ipairs(TRAITS) do
    local axis=amount(seed,npc.id,index+5,201)/100-1
    npc.profile[key]=clamp(base_profile[key]+0.15*axis,0,1)
  end
  npc.suspended=false; npc.retry_at=0; npc.next_decision=game.tick
  npc.progress_watch=nil; npc.production_sample=nil
  npc.behavior.failure_streak=0
  npc.failures={}; npc.recovery_count=0; npc.last_problem=nil
  npc.character.health=npc.character.max_health
  grants.restored_health=npc.character.health
  npc.training_curriculum={case=case,seed=seed,configured_tick=game.tick,
    coal_position=coal_position,start_position=start_position,grants=grants,removed=removed}
  return {id=npc.id,coal_position=coal_position,start_position=start_position,
    grants=grants,removed=removed,profile=npc.profile}
end

local function curriculum(value)
  local d=state()
  if not (d.enabled and d.prepared and game.tick_paused) then
    return {ok=false,reason="requires_paused_dev_world"}
  end
  if type(value)~="table" or not CASES[value.case] then
    return {ok=false,reason="unknown_curriculum_case"}
  end
  if type(value.seed)~="number" or value.seed~=value.seed or value.seed%1~=0
      or value.seed<0 or value.seed>2147483647 then
    return {ok=false,reason="seed_must_be_integer_0_to_2147483647"}
  end
  if value.npc_id~=nil and (type(value.npc_id)~="number" or value.npc_id%1~=0
      or value.npc_id<1 or value.npc_id>d.bots) then
    return {ok=false,reason="invalid_training_npc_id"}
  end
  local targets,reason,busy_ids=training_targets(value)
  if not targets then return {ok=false,reason=reason,busy_ids=busy_ids} end
  local configured={}
  for _,target in ipairs(targets) do configured[#configured+1]=set_case(target,value.case,value.seed) end
  d.training={enabled=true,case=value.case,seed=value.seed,
    configured_tick=game.tick,configured=configured}
  return {ok=true,training=d.training,tick=game.tick,paused=game.tick_paused}
end

function Dev.on_tick(tick)
  local d=storage.dev
  local training=d and d.training
  if training and training.advance_until and tick>=training.advance_until then
    training.last_barrier_tick=tick
    training.advance_until=nil
    game.tick_paused=true
  end
end

function Dev.snapshot()
  local d=state(); local npcs={}
  for id=1,10 do
    local npc=storage.npcs[id]
    if npc then npcs[#npcs+1]={id=id,name=npc.name,force=npc.force.name,phase=npc.phase,step=npc.step,
      position=npc.character and npc.character.valid and npc.character.position or nil,
      travelled=npc.travelled,miner=npc.miner and npc.miner.valid or false,
      furnace=npc.furnace and npc.furnace.valid or false,
      behavior=npc.behavior,profile=npc.profile,module_direction=npc.module_direction,
      home=npc.home,personality=Personality.observe(npc),society=Society.observe(npc),combat=Combat.observe(npc),
      progression=Progression.status(npc),strategy=Strategy.observe(npc),
      actual_walk_modifier=npc.character and npc.character.valid and npc.character.character_running_speed_modifier or 0,
      recovery_count=npc.recovery_count or 0,
      growth_wait_reason=npc.growth_wait_reason,
      starter_plan_failures=npc.starter_plan_failures,starter_plan_retry_at=npc.starter_plan_retry_at,
      construction=construction_snapshot(npc),repairs=Repairs.observe(npc),rail=RailTrade.observe(npc),worker=Worker.observe(npc),
      plates=npc.furnace and npc.furnace.valid and npc.furnace.products_finished or 0,
      design=npc.design and {status=npc.design.status,purpose=npc.design.purpose,stage=npc.design.stage_index,
        plans=#(npc.design_history or {}),next_goal_tick=npc.design.next_goal_tick,reason=npc.design.reason} or nil} end
  end
  local players={}
  for _,player in pairs(game.connected_players) do
    players[#players+1]={index=player.index,editor=player.controller_type==defines.controllers.editor,
      creative=player.cheat_mode,position=player.position}
  end
  return {ok=true,bots=d.bots,creative=d.creative,prepared=d.prepared,terrain_fixture=d.terrain_fixture,
    native_starts=d.native_starts or false,native_start_roster=storage.native_start_roster,
    scenario=d.scenario or "standard",varied_layout=d.varied_layout or false,
    factory_growth=d.factory_growth or false,
    advanced_growth=d.advanced_growth or false,spacing=d.spacing or 64,
    oil_terrain_fixture=d.oil_terrain_fixture or false,
    departure_terrain_fixture=d.departure_terrain_fixture or false,
    tick=game.tick,speed=game.speed,paused=game.tick_paused,npcs=npcs,players=players,
    training=d.training,victory=PlatformLaunch.observe(),
    world={space_age=script.active_mods["space-age"]~=nil,no_enemies_mode=game.surfaces[1].map_gen_settings.no_enemies_mode,
      enemy_expansion=game.map_settings.enemy_expansion.enabled,
      native_enemies=game.surfaces[1].count_entities_filtered{force="enemy",type={"unit","unit-spawner","turret"}}}}
end

function Dev.register_commands()
  commands.add_command("npc-dev","Private development world and observation controls",function(command)
    local p=command.parameter or "status"
    local result
    if p:sub(1,10)=="configure " and not command.player_index then
      local ok,value=pcall(helpers.json_to_table,p:sub(11))
      if not ok or type(value)~="table" or next(storage.npcs) then
        result={ok=false,reason="configure_requires_fresh_world"}
      elseif value.bots~=nil and (type(value.bots)~="number" or value.bots%1~=0 or value.bots<1 or value.bots>10) then
        result={ok=false,reason="bots_must_be_1_to_10"}
      elseif value.creative~=nil and type(value.creative)~="boolean" then
        result={ok=false,reason="creative_must_be_boolean"}
      elseif value.native_starts~=nil and type(value.native_starts)~="boolean" then
        result={ok=false,reason="native_starts_must_be_boolean"}
      elseif value.varied_layout~=nil and type(value.varied_layout)~="boolean" then
        result={ok=false,reason="varied_layout_must_be_boolean"}
      elseif value.factory_growth~=nil and type(value.factory_growth)~="boolean" then
        result={ok=false,reason="factory_growth_must_be_boolean"}
      elseif value.spacing~=nil and (type(value.spacing)~="number" or value.spacing%1~=0 or value.spacing<64 or value.spacing>4096) then
        result={ok=false,reason="spacing_must_be_64_to_4096"}
      elseif value.scenario~=nil and value.scenario~="standard" and value.scenario~="coal-far" and value.scenario~="obstructed" then
        result={ok=false,reason="unknown_scenario"}
      else
        local d=state(); d.enabled=true; d.bots=value.bots or settings.global["npc-factions-count"].value
        if value.creative~=nil then d.creative=value.creative end
        d.scenario=value.scenario or "standard"
        d.varied_layout=value.varied_layout~=false
        d.factory_growth=value.factory_growth==true
        d.advanced_growth=value.advanced_growth==true
        d.progression_external=value.progression_policy=="external"
        d.spacing=value.spacing or 64
        d.safe_departure=value.safe_departure~=false
        d.native_starts=value.native_starts==true
        settings.global["npc-factions-count"]={value=d.bots}
        settings.global["npc-factions-native-starts"]={value=d.native_starts}
        if d.native_starts then settings.global["npc-factions-home-spacing"]={value=math.max(256,d.spacing)} end
        settings.global["npc-factions-factory-growth"]={value=d.factory_growth}
        settings.global["npc-factions-orbital-progression"]={value=d.advanced_growth}
        if remote.interfaces.freeplay then
          if remote.interfaces.freeplay.set_skip_intro then remote.call("freeplay","set_skip_intro",true) end
          if remote.interfaces.freeplay.set_disable_crashsite then remote.call("freeplay","set_disable_crashsite",true) end
        end
        if not d.native_starts then prepare() end
        result=Dev.snapshot()
      end
    elseif p=="drive-lab" and not command.player_index and not next(storage.npcs) then
      -- the drive lab (tests): a player's driving written down
      result=DriveLab.setup()
      if result.ok then state().enabled=true end
    elseif p=="drive-lab-status" and not command.player_index then
      result=DriveLab.status()
    elseif (p=="drive-range-status" or p:match("^drive%-range%-status %d+$")) and not command.player_index then
      result=DriveRange.status(p:match("(%d+)$"))
    elseif p:sub(1,12)=="drive-range " and not command.player_index and not next(storage.npcs) then
      -- the driving range (tests): engineers with a vehicle each, trip after trip, at random from a seed
      local ok,value=pcall(helpers.json_to_table,p:sub(13))
      result=ok and type(value)=="table" and DriveRange.setup(value) or {ok=false,reason="invalid_drive_range_json"}
      if result.ok then state().enabled=true end
    elseif p=="status" then result=Dev.snapshot()
    elseif p=="scorecard" then result=Scorecard.all()
    elseif (p=="profile on" or p=="profile off") and not command.player_index then
      storage.profile=p=="profile on" or nil; result={ok=true,profile=storage.profile==true}
    elseif p:sub(1,15)=="worker-observe " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(16))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      result=npc and {ok=true,worker=Worker.observe(npc)} or {ok=false,reason="unknown_actor"}
    elseif p:sub(1,19)=="navigation-observe " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(20))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      result=npc and {ok=true,navigation=Navigation.observe(npc)} or {ok=false,reason="unknown_actor"}
    elseif p:sub(1,12)=="oil-fixture " and not command.player_index and state().enabled and game.tick_paused
        and state().training and state().training.enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(13))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      result=npc and npc.character and npc.character.valid and prepare_oil_site(npc.character.surface,npc.home)
        or {ok=false,reason="actor_unavailable"}
    elseif p=="social-fixture status" and not command.player_index and state().enabled and game.tick_paused
        and state().training and state().training.enabled then
      result=SocialFixture.observe()
    elseif p:sub(1,15)=="social-fixture " and not command.player_index and state().enabled and game.tick_paused
        and state().training and state().training.enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(16))
      result=ok and SocialFixture.setup(value) or {ok=false,reason="invalid_social_fixture_json"}
    elseif p:sub(1,17)=="strategy-observe " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(18))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      result=npc and {ok=true,strategy=Strategy.observe(npc,true)} or {ok=false,reason="unknown_actor"}
    elseif p:sub(1,16)=="strategy-import " and not command.player_index and state().enabled and game.tick_paused
        and state().training and state().training.enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(17))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc and not npc.social_job and not Society.ensure(npc).pending_intent then
        local imported,reason=Strategy.import_model(npc,value.model,value.learning)
        result={ok=imported,reason=not imported and reason or nil,strategy=Strategy.observe(npc)}
      else result={ok=false,reason="actor_missing_or_social_work_pending"} end
    elseif (p=="society-policy external" or p=="society-policy bandit" or p=="society-policy teacher") and not command.player_index and state().enabled then
      if not storage.society then Society.init() end
      local mode=p:sub(16)
      storage.society.policy_mode=mode;result={ok=true,mode=mode}
    elseif p=="personality-catalog" and not command.player_index and state().enabled then
      result={ok=true,catalog=Personality.catalog()}
    elseif p:sub(1,20)=="personality-observe " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(21))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc then
        local personality=Personality.observe(npc);personality.catalog=Personality.catalog()
        result={ok=true,personality=personality,catalog=personality.catalog}
      else result={ok=false,reason="unknown_actor"} end
    elseif p:sub(1,19)=="personality-assign " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(20))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc and type(value.traits)=="table" then
        local assigned,reason=Personality.assign(npc,value.traits,Personality.ensure(npc).seed)
        if assigned then Behavior.refresh(npc) end
        result={ok=assigned,reason=not assigned and reason or nil,personality=Personality.observe(npc)}
      else result={ok=false,reason="invalid_assignment"} end
    elseif p:sub(1,8)=="warlord " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(9))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc then
        local made,reason=Agent.warlord(npc)
        if made then Behavior.refresh(npc) end
        result={ok=made,reason=reason,name=npc.name,personality=Personality.observe(npc)}
      else result={ok=false,reason="unknown_actor"} end
    elseif p:sub(1,16)=="society-observe " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(17))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      result=npc and {ok=true,observation=Society.observe(npc),candidates=Society.candidates(npc)} or {ok=false,reason="unknown_actor"}
    elseif p:sub(1,14)=="society-apply " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(15))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc then local applied,detail=Society.apply(npc,type(value.decision)=="string" and {id=value.decision} or value.decision);result={ok=applied,result=detail}
      else result={ok=false,reason="unknown_actor"} end
    elseif p:sub(1,11)=="rail-start " and not command.player_index and state().enabled and game.tick_paused then
      local ok,value=pcall(helpers.json_to_table,p:sub(12))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc then
        local started,detail=RailTrade.start(npc,value)
        result={ok=started,detail=started and detail or nil,reason=not started and detail or nil}
      else result={ok=false,reason="unknown_actor"} end
    elseif p:sub(1,13)=="rail-observe " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(14))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      result=npc and {ok=true,rail=RailTrade.observe(npc)} or {ok=false,reason="unknown_actor"}
    elseif p:sub(1,16)=="progress-replan " and not command.player_index
        and state().enabled and state().prepared and game.tick_paused then
      local ok,value=pcall(helpers.json_to_table,p:sub(17))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc then
        local replanned,detail=Projects.replan(npc,value.project)
        result={ok=replanned,detail=replanned and detail or nil,reason=not replanned and detail or nil}
      else result={ok=false,reason="actor_missing_or_invalid_replan"} end
    elseif p:sub(1,15)=="progress-start " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(16))
      local npc=ok and type(value)=="table" and storage.npcs[value.id]
      if npc then
        npc.requested_project=value.project
        result={ok=true,queued=true,project=value.project}
      else result={ok=false,reason="actor_busy_or_missing"} end
    elseif p=="training-enable" and not command.player_index and state().enabled and game.tick_paused then
      local d=state()
      if not d.training then d.training={enabled=true,case="unconfigured",configured_tick=game.tick} end
      d.training.enabled=true
      result=Dev.snapshot()
    elseif (p=="project-policy bandit" or p=="project-policy teacher" or p=="project-policy hold")
        and not command.player_index and state().enabled and game.tick_paused
        and state().training and state().training.enabled then
      state().training.project_policy=p:sub(16)
      result={ok=true,mode=state().training.project_policy}
    elseif (p=="pause true" or p=="pause false") and not command.player_index and state().enabled then
      local paused=p=="pause true"
      local training=state().training
      if training then training.advance_until=nil end
      game.tick_paused=paused
      result=Dev.snapshot()
    elseif p:match("^advance %d+$") and not command.player_index and state().enabled then
      local ticks=tonumber(p:sub(9))
      local training=state().training
      if not training or not training.enabled or not game.tick_paused then
        result={ok=false,reason="advance_requires_paused_training"}
      elseif ticks<1 or ticks>36000 then
        result={ok=false,reason="advance_ticks_must_be_1_to_36000"}
      else
        training.advance_until=game.tick+ticks
        game.tick_paused=false
        result=Dev.snapshot()
      end
    elseif p:sub(1,11)=="curriculum " and not command.player_index and state().enabled then
      local ok,value=pcall(helpers.json_to_table,p:sub(12))
      result=ok and curriculum(value) or {ok=false,reason="invalid_curriculum_json"}
    elseif p:match("^speed %d+$") and not command.player_index and state().enabled then
      local speed=tonumber(p:sub(7))
      if speed>=1 and speed<=100 then game.speed=speed; result=Dev.snapshot()
      else result={ok=false,reason="speed_must_be_1_to_100"} end
    elseif p=="start-native" and not command.player_index and state().enabled and state().native_starts
        and not next(storage.npcs) and Dev.native_roster then
      -- The real native roster (designed or scripted, as the start-mode setting says), no player needed.
      local proxy={valid=true,surface=game.surfaces[1],force=game.forces.player,position={x=0,y=0}}
      local spawned,reason=Dev.native_roster(proxy)
      result=Dev.snapshot(); result.spawned=spawned; result.reason=reason
    elseif p=="start-bots" and not command.player_index and state().enabled and not next(storage.npcs) then
      -- Server-only smoke exercises the same real roster with the player force.
      local proxy={force=game.forces.player,teleport=function() end}
      Dev.spawn_roster(proxy); result=Dev.snapshot()
    else
      local id=tonumber(p:match("^watch (%d+)$"))
      local player=command.player_index and game.get_player(command.player_index)
      result={ok=player and state().enabled and id and watch(player,id) or false}
    end
    if command.player_index then game.get_player(command.player_index).print(helpers.table_to_json(result))
    else rcon.print("NPC_DEV_RESULT "..helpers.table_to_json(result)) end
  end)
  -- control.lua owns the on_gui_click registration (one handler per mod) and calls this.
  Dev.on_gui_click=function(event)
    local element=event.element
    if not element or not element.valid then return end
    local id=element.tags.npc_dev_watch
    if id then local player=game.get_player(event.player_index); if player then watch(player,id) end end
  end
end
return Dev
