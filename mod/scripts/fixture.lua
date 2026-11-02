-- World setup and observations are separate from the ordinary economy executor.
local Factory=require("scripts.factory")
local Worker=require("scripts.worker")
local Guards=require("scripts.guard_fixture")
local DesignedFixture=require("scripts.designed_fixture")
local Fixture={}
local MARKER="NPC_FACTIONS_TEST_RESULT "

local function json_result(result,console)
  local text=MARKER..helpers.table_to_json(result)
  if not console then log(text) end
  if console then rcon.print(text) end
end

function Fixture.start(mode)
  if mode=="guards" then return Guards.start() end
  if mode=="designed" then return DesignedFixture.start("a") end
  if mode=="designedb" then return DesignedFixture.start("b") end
  if mode=="designedc" then return DesignedFixture.start("c") end
  if mode=="designedd" then return DesignedFixture.start("d") end
  if mode=="designede" then return DesignedFixture.start("e") end
  if mode=="designedf" then return DesignedFixture.start("f") end
  if mode=="designedg" then return DesignedFixture.start("g") end
  if mode=="designedh" then return DesignedFixture.start("h") end
  if mode=="designedi" then return DesignedFixture.start("i") end
  if mode=="designedj" then return DesignedFixture.start("j") end
  if mode=="designedk" then return DesignedFixture.start("k") end
  if mode=="designedl" then return DesignedFixture.start("l") end
  if mode=="designedm" then return DesignedFixture.start("m") end
  if mode=="designedn" then return DesignedFixture.start("n") end
  if storage.test or next(storage.npcs) then return false,"start requires a fresh world without NPCs" end
  if mode~="iron" and mode~="blocked" then return false,"bootstrap fixture is not implemented yet" end
  local surface=game.surfaces[1]
  surface.request_to_generate_chunks({16,0},3); surface.force_generate_chunk_requests()
  local area={{-24,-20},{64,20}}
  for _,e in ipairs(surface.find_entities_filtered{area=area}) do if e.type~="character" then e.destroy() end end
  local tiles={}
  for x=-24,64 do for y=-20,20 do tiles[#tiles+1]={name="grass-1",position={x,y}} end end
  surface.set_tiles(tiles)
  storage.founding_force=game.forces.player
  storage.test={mode=mode,start_tick=game.tick,initial_ore=0,initial_coal=0,terrain_fixture=true}
  for id=1,2 do
    local x=(id-1)*32
    surface.create_entity{name="tree-01",position={x-4,-3}}
    surface.create_entity{name="coal",position={x-4,3},amount=1000}
    storage.test.initial_coal=storage.test.initial_coal+1000
    for dx=-2,2 do for dy=-2,2 do
      surface.create_entity{name="iron-ore",position={x+6+dx,dy},amount=1000}
      storage.test.initial_ore=storage.test.initial_ore+1000
    end end
    local npc,reason=Factory.spawn(id,surface,{x=x-8,y=-8},{x=x,y=0},game.forces.player)
    if not npc then error(reason) end
    npc.module={x=x+6,y=0}; npc.furnace_position={x=x+6,y=-2}
    Worker.enqueue(npc,{id="walk-home",kind="move",position=npc.home})
    npc.pending_job="walk-home"; npc.moving_home=true
    if mode=="blocked" then
      surface.create_entity{name="big-rock",position=npc.furnace_position}
    end
  end
  return true
end

local function inv(npc)
  local result={}
  for _,name in ipairs({"iron-plate","wood","coal","iron-ore","burner-mining-drill","stone-furnace"}) do
    result[name]=npc.character.get_main_inventory().get_item_count(name)
  end
  return result
end
local function count_item(entity,inventory,item)
  if not (entity and entity.valid) then return 0 end
  local i=entity.get_inventory(inventory)
  return i and i.get_item_count(item) or 0
end

function Fixture.snapshot(finalize)
  if not storage.test then return {schema=1,mode="none",tick=game.tick,phase="not-started",completed=false,checks={},state={}} end
  if storage.test.mode=="guards" then return Guards.snapshot() end
  if storage.test.mode=="designed" and not storage.test.result then return DesignedFixture.snapshot(finalize) end
  if storage.test.result then
    local result=storage.test.result
    return {schema=1,mode=result.mode,tick=game.tick,phase=result.phase,completed=result.completed,
      checks=result.checks,state=result.state,fingerprint=result.fingerprint}
  end
  local actors,checks={},{}
  local physical={wood=true,coal=true,iron=true}; local ready=true
  local total_plates,total_ore,all_materials=0,0,true
  local miner_fueled,furnace_fueled,blocked_recovered=true,true,true
  local forces={}
  for id=1,2 do
    local npc=storage.npcs[id]
    local mined={wood=0,coal=0,iron_ore=0}; local complete=0
    local built=0
    for _,key in ipairs(npc.receipt_order or {}) do
      local r=npc.receipts[key]
      if r.status=="complete" then
        complete=complete+1
        if r.kind=="mine" then
          local keyname=r.item=="iron-ore" and "iron_ore" or r.item
          if mined[keyname]~=nil then mined[keyname]=mined[keyname]+r.count end
        elseif r.kind=="build" and r.committed then built=built+1 end
      end
    end
    local items=inv(npc)
    local plates=npc.force.get_item_production_statistics(npc.character.surface).get_input_count("iron-plate")
    total_plates=total_plates+plates
    local furnace_ore=count_item(npc.furnace,defines.inventory.crafter_input,"iron-ore")
    total_ore=total_ore+items["iron-ore"]+furnace_ore
    physical.wood=physical.wood and mined.wood>0
    physical.coal=physical.coal and mined.coal>=12
    physical.iron=physical.iron and mined.iron_ore>=8
    -- More plates than the eight hand-mined ores proves the connected drill also fed it.
    ready=ready and npc.step>8 and not npc.current_job and #npc.jobs==0 and plates>=12
    miner_fueled=miner_fueled and npc.miner and npc.miner.valid and npc.miner.burner and npc.miner.burner.currently_burning~=nil
    furnace_fueled=furnace_fueled and npc.furnace and npc.furnace.valid and npc.furnace.burner and npc.furnace.burner.currently_burning~=nil
    local material_ok=npc.kit_granted and npc.grants["iron-plate"]==8 and items["iron-plate"]==8 and
      items.wood==1+mined.wood and built==2 and items["burner-mining-drill"]==0 and items["stone-furnace"]==0
    all_materials=all_materials and material_ok
    blocked_recovered=blocked_recovered and npc.obstacle_cleared==true and npc.obstruction_furnace_held==true and (npc.recovery_count or 0)>=1
    forces[npc.force.name]=true
    actors[#actors+1]={id=id,force=npc.force.name,position={x=npc.character.position.x,y=npc.character.position.y},
      start_position=npc.spawn,distance_walked=npc.travelled,inventory=items,mined=mined,
      jobs_complete=complete,phase=npc.phase,current_job=npc.current_job and npc.current_job.kind or nil,
      job_state=npc.current_job and npc.current_job.state or nil,
      mining_progress=npc.character.character_mining_progress,
      failures=#npc.failures,miner_valid=npc.miner and npc.miner.valid or false,
      furnace_valid=npc.furnace and npc.furnace.valid or false,iron_plates=plates,
      recovery_count=npc.recovery_count or 0,material_conserved=material_ok}
  end
  local remaining_ore=0
  for _,e in ipairs(game.surfaces[1].find_entities_filtered{name="iron-ore",area={{-24,-20},{64,20}}}) do remaining_ore=remaining_ore+e.amount end
  local depleted=storage.test.initial_ore-remaining_ore
  local ore_difference=depleted-total_ore-total_plates
  -- One ore can be in each smelting cycle, between input debit and plate credit.
  local ore_conserved=ore_difference>=0 and ore_difference<=2
  checks.two_independent_forces={passed=forces["npc-faction-1"] and forces["npc-faction-2"] or false}
  checks.physical_coal={passed=physical.coal}; checks.physical_iron={passed=physical.iron}; checks.physical_wood={passed=physical.wood}
  checks.miner_fueled={passed=not not miner_fueled}; checks.furnace_fueled={passed=not not furnace_fueled}
  checks.iron_plates={passed=total_plates>=24,detail=total_plates}
  checks.no_free_materials={passed=not not all_materials,detail="fixed one-time kit; two owned building items debited; natural mining receipts"}
  checks.inventory_conserved={passed=all_materials and ore_conserved,detail={ore_depleted=depleted,ore_in_inventory=total_ore,plates=total_plates,inflight=ore_difference}}
  checks.job_completed={passed=not not ready}
  checks.physical_walking={passed=actors[1].distance_walked>2 and actors[2].distance_walked>2}
  checks.no_enemy_units={passed=game.surfaces[1].count_entities_filtered{force="enemy",type={"unit","unit-spawner","turret"}}==0}
  if storage.test.mode=="blocked" then checks.blocked_recovered={passed=not not blocked_recovered} end
  local passed=true; for _,c in pairs(checks) do if not c.passed then passed=false end end
  local state={actors=actors,production={iron_plate=total_plates},free_materials=all_materials and 0 or -1,
    material_delta={ore_depleted=depleted,ore_remaining=total_ore,plates=total_plates,inflight=ore_difference},
    relative_tick=game.tick-storage.test.start_tick}
  -- Observation must not advertise a terminal snapshot before the tick handler
  -- freezes it. Otherwise polling can capture different relative completion ticks.
  return {schema=1,mode=storage.test.mode,tick=game.tick,phase=passed and finalize and "complete" or "working",completed=passed and finalize==true,
    checks=checks,state=state,fingerprint=helpers.table_to_json(state)}
end

function Fixture.tick(tick)
  if storage.test and storage.test.mode=="guards" then
    Guards.tick(tick)
    if storage.guards.result and not storage.guards.emitted then
      storage.guards.emitted=true; json_result(storage.guards.result)
    end
    return
  end
  if not storage.test or storage.test.result or tick%60~=0 then return end
  if not storage.test.debug_logged and tick-storage.test.start_tick>=600 then
    storage.test.debug_logged=true
    for _,npc in pairs(storage.npcs) do
      local job=npc.current_job; local c=npc.character
      if c and c.valid then
      log("NPC_MINING_DIAGNOSTIC "..helpers.table_to_json{npc=npc.id,position=c.position,
        target=job and job.target and job.target.valid and job.target.position or nil,
        kind=job and job.kind,item=job and job.item,resource_reach=c.resource_reach_distance,
        general_reach=c.reach_distance,selected=c.selected and c.selected.name,
        mining_progress=c.character_mining_progress,mining_state=c.mining_state})
      end
    end
  end
  local result=Fixture.snapshot(true)
  if result.completed then
    storage.test.result=result
    for _,npc in pairs(storage.npcs) do
      npc.suspended=true
      if npc.character and npc.character.valid then
        npc.character.walking_state={walking=false,direction=defines.direction.north}
      end
    end
    json_result(result)
  elseif tick-storage.test.start_tick>=30000 and not storage.test.timeout_logged then
    storage.test.timeout_logged=true; json_result(result)
  end
end

function Fixture.register_commands()
  commands.add_command("npc-factions-test","Private development fixture controls",function(command)
    if command.player_index then return end -- privileged server/RCON ingress only
    local parameter=command.parameter or "snapshot"
    local mode=parameter:match("^start (%a+)$")
    if mode then
      local ok,reason=Fixture.start(mode)
      if not ok then rcon.print("NPC_TEST_ERROR "..reason); return end
    end
    -- A resumed test save can be switched to a later test map's checks.
    local variant=parameter:match("^variant (%a)$")
    if variant and storage.test then storage.test.variant=variant; DesignedFixture.on_variant(variant) end
    if parameter=="profile" and storage.test then storage.test.profile=true end
    -- Cliffs only get in the way of test runs for now: remove them and stop new ones.
    if parameter=="nocliffs" then
      local surface=game.surfaces.nauvis
      for _,cliff in pairs(surface.find_entities_filtered{type="cliff"}) do cliff.destroy() end
      local settings=surface.map_gen_settings
      settings.cliff_settings.richness=0
      surface.map_gen_settings=settings
    end
    local save=parameter:match("^save ([%w_-]+)$")
    if save then game.server_save(save) end
    local speed=tonumber(parameter:match("^speed ([1-8])$"))
    if speed and storage.test and not storage.test.result then game.speed=speed end
    json_result(Fixture.snapshot(),true)
  end)
end
return Fixture
