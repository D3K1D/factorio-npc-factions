local Worker=require("scripts.worker")
local Reach=require("scripts.reach")
local Boosts=require("scripts.boosts")
local Factory=require("scripts.factory")
local Fixture=require("scripts.fixture")
local Diplomacy=require("scripts.diplomacy")
local Dev=require("scripts.dev_world")
local Behavior=require("scripts.behavior")
local Society=require("scripts.society")
local Combat=require("scripts.combat")
local Settlements=require("scripts.settlements")
local SocialJobs=require("scripts.social_jobs")
local PlatformLaunch=require("scripts.platform_launch")
local Chat=require("scripts.chat")
local PlayerTrade=require("scripts.player_trade")
local SharedProjects=require("scripts.shared_projects")
local DesignedStart=require("scripts.designed_start")
local BaseMap=require("scripts.base_map")
local Agent=require("scripts.agent")
local War=require("scripts.war")
local Drive=require("scripts.drive")
local DriveRange=require("scripts.drive_range")
local DriveLab=require("scripts.drive_lab")
local MapView=require("scripts.map_view")
local Scorecard=require("scripts.scorecard")
local Character=require("scripts.character")
local Scouting=require("scripts.scouting")
local TeachGui=require("scripts.teach_gui")
local Teach=require("scripts.teach")
local Recorder=require("scripts.recorder")
local Voice=require("scripts.voice")
local Camera=require("scripts.camera")
local TeamWorld=require("scripts.team_world")
local TeamWaves=require("scripts.team_waves")
local TeamResearch=require("scripts.team_research")
local TeamWar=require("scripts.team_war")
local BaseMemory=require("scripts.base_memory")
local Proving=require("scripts.proving")
local Watchdog=require("scripts.watchdog")

local function disable_bugs()
  if not settings.startup["npc-factions-disable-bugs"].value then return end
  game.map_settings.enemy_expansion.enabled=false
  game.map_settings.enemy_evolution.enabled=false
  for _,surface in pairs(game.surfaces) do
    local gen=surface.map_gen_settings
    gen.no_enemies_mode=true
    gen.autoplace_controls=gen.autoplace_controls or {}
    gen.autoplace_controls["enemy-base"]={frequency=0,size=0,richness=0}
    surface.map_gen_settings=gen
    for _,entity in ipairs(surface.find_entities_filtered{force="enemy",type={"unit","unit-spawner","turret"}}) do entity.destroy() end
  end
end

script.on_init(function()
  storage.npcs={}; storage.path_requests={}
  storage.version=1
  Diplomacy.init()
  Society.init()
  storage.world_id="npc-world-"..math.random(100000000,999999999)   -- names this world to the layout designer
  disable_bugs()
  local mode=settings.startup["npc-factions-test-mode"].value
  if mode~="none" then local ok,reason=Fixture.start(mode); if not ok then log(reason) end end
end)
script.on_configuration_changed(function()
  -- The old local decision service is gone: its world id is kept, the rest of its state dropped.
  storage.world_id=storage.world_id or (storage.ai and storage.ai.world_id) or "npc-world-"..math.random(100000000,999999999)
  storage.ai=nil
  if not storage.society then Society.init() end
  storage.allow_war=nil   -- war is always possible now
  Boosts.migrate()   -- (engineers already here get no quick start: boosts.lua)
  for _,npc in pairs(storage.npcs or {}) do Behavior.refresh(npc) end
  if TeamWorld.active() then
    -- one team: allies stay allies (the start-alliance migration would make them strangers again)
    TeamWorld.ally_all(); TeamResearch.sync()
  elseif storage.founding_force then Diplomacy.migrate_start_alliances(storage.founding_force) end
end)

-- No on_load mutations: actor state, paths, jobs and permissions live in storage.
script.on_event(defines.events.on_script_path_request_finished,function(event)
  if Drive.on_path_finished(event) then return end
  Worker.on_path_finished(event);Combat.on_path_finished(event)
end)
script.on_event(defines.events.on_entity_damaged,function(event)
  if storage.drive_lab then DriveLab.on_entity_damaged(event); return end
  Combat.on_entity_damaged(event);Character.on_entity_damaged(event);War.on_entity_damaged(event)
  TeamWar.on_entity_damaged(event)   -- (the team world: what attacks each plot)
  DesignedStart.Mend.on_entity_damaged(event)   -- (what it mends after: W-33)
end)
script.on_event(defines.events.on_entity_died,function(event)
  Recorder.on_entity_died(event)
  if storage.drive_lab then DriveLab.on_entity_died(event); return end
  Combat.on_entity_died(event);Character.on_entity_died(event);DesignedStart.on_entity_died(event)
  War.on_entity_died(event)
  TeamWar.on_entity_died(event)   -- (the team world: breaches of a plot's wall line)
end)
-- The team world (scenario "team"): no nests inside the team area, from map generation on.
script.on_event(defines.events.on_chunk_generated,TeamWorld.on_chunk_generated)
script.on_event(defines.events.on_robot_built_entity,function(event) pcall(DesignedStart.Robots.on_built,event) end)
script.on_event(defines.events.on_robot_mined_entity,function(event) pcall(DesignedStart.Robots.on_mined,event) end)
script.on_event(defines.events.on_biter_base_built,TeamWorld.on_biter_base_built)
script.on_event(defines.events.on_research_finished,function(event)
  -- one tech tree in the team world: a tech anyone finishes is everyone's (the copies are no news)
  if TeamWorld.active() and TeamResearch.on_research_finished(event) then return end
  if not TeamWorld.active() then TeamResearch.log_finished(event) end   -- (when each finished: R-9)
  Character.on_research_finished(event);Agent.on_research_finished(event);Recorder.on_research_finished(event)
end)
script.on_event(defines.events.on_research_cancelled,function(event)
  if TeamWorld.active() then TeamResearch.on_research_cancelled(event) end
end)
script.on_event(defines.events.on_rocket_launch_ordered,function(event)
  PlatformLaunch.on_ordered(event);Recorder.on_rocket_launch_ordered(event)
end)
script.on_event(defines.events.on_cargo_pod_finished_ascending,PlatformLaunch.on_ascending)
script.on_event(defines.events.on_console_chat,function(event) Chat.on_message(event);Recorder.chat(event) end)
script.on_event(defines.events.on_player_created,function(event)
  if not storage.founding_force and not storage.test and not storage.drive_lab then storage.pending_founder=event.player_index end
end)
script.on_event(defines.events.on_player_joined_game,function(event)
  if not storage.founding_force and not storage.test and not storage.drive_lab then storage.pending_founder=event.player_index end
end)
script.on_event(defines.events.on_runtime_mod_setting_changed,function(event)
  if tostring(event.setting):sub(1,10)=="far-reach-" then Reach.changed() end   -- (the engineers' reach too: reach.lua)
  if tostring(event.setting):find("^npc%-factions%-boost%-") or tostring(event.setting):find("^npc%-factions%-quick%-start")
    then Boosts.changed() end   -- (the engineers' boosts: boosts.lua)
  local mapping={["npc-factions-conservation"]="resource_conservatism",
    ["npc-factions-expansion"]="expansion_drive",["npc-factions-cooperation"]="cooperation",
    ["npc-factions-risk"]="risk_tolerance"}
  if event.setting=="npc-factions-scenario" and TeamWorld.active() then
    TeamWorld.ally_all(); TeamResearch.sync()   -- switched to the team world mid-game
  end
  local key=mapping[event.setting]
  if key or event.setting=="npc-factions-variation" or event.setting=="npc-factions-adaptation" then
    for _,npc in pairs(storage.npcs) do
      npc.profile=npc.profile or {}
      if key then npc.profile[key]=settings.global[event.setting].value end
      Behavior.refresh(npc)
    end
  end
end)

local function spawn_native_roster(player)
  local count=settings.global["npc-factions-count"].value
  local spacing=settings.global["npc-factions-home-spacing"].value
  local points=player.surface.map_gen_settings.starting_points or {}
  if #points<count+1 or not Settlements.find_at_start then
    return nil,"This mode needs a fresh world with a native starting point for each faction."
  end
  -- Survey every assigned area before creating bodies or granting starter kits.
  if next(storage.npcs) then return nil,"The NPC roster already exists." end
  local plans,reserved={},{}
  local ok,problem=pcall(function()
    for id=1,count do
      local home,reason,survey=Settlements.find_at_start(player,id,count,spacing,{reserved_homes=reserved})
      if not home then error("NPC "..id..": "..tostring(reason),0) end
      local point=points[id+1]
      local near={x=point.x or point[1],y=point.y or point[2]}
      if not player.surface.find_non_colliding_position("character",near,24,.5) then
        error("NPC "..id..": no arrival space near its starting point",0)
      end
      plans[id]={home=home,survey=survey,near=near}
      reserved[#reserved+1]=home
    end
  end)
  if not ok then return nil,"Starting-area preflight failed: "..tostring(problem) end
  storage.founding_force=player.force
  local spawned=0
  for id=1,count do
    local plan=plans[id]
    Settlements.generate_route(player.surface,plan.near,plan.home)
    local npc,reason=Factory.spawn(id,player.surface,plan.near,plan.home,player.force,plan.survey)
    if npc then spawned=spawned+1
    else game.print("NPC "..id.." could not arrive: "..tostring(reason)) end
  end
  storage.pending_founder=nil
  storage.native_start_roster={tick=game.tick,requested=count,spawned=spawned}
  return spawned
end

-- Server-only smoke runs (tools/run_world_smoke.py) start the same native roster a player's arrival would.
Dev.native_roster=spawn_native_roster

-- The team world: each engineer arrives at the home of its own plot (once the area is ready).
local function spawn_team_roster(player)
  if not TeamWorld.ready() then return nil,"waiting" end
  if next(storage.npcs) then return nil,"The NPC roster already exists." end
  local surface=game.surfaces[TeamWorld.state().surface_index]
  local count=math.min(settings.global["npc-factions-count"].value,TeamWorld.npc_plots())
  storage.founding_force=player.force
  local spawned=0
  for id=1,count do
    local home=TeamWorld.home(id)
    local npc,reason=Factory.spawn(id,surface,home,home,player.force,Settlements.survey_at(surface,id,home))
    if npc then spawned=spawned+1
    else game.print("NPC "..id.." could not arrive: "..tostring(reason)) end
  end
  storage.pending_founder=nil
  -- one team from the first moment: allies, one tech tree
  TeamWorld.ally_all(); TeamResearch.sync()
  return spawned
end
TeamWorld.spawner=spawn_team_roster

local function spawn_roster(player)
  if Dev.spawn_roster(player) then
    Dev.enable_observer(player)
    return
  end
  if TeamWorld.active() then
    local spawned,reason=spawn_team_roster(player)
    if reason=="waiting" then storage.spawn_retry=game.tick+120; return end   -- the area is still being made
    if not spawned then player.print(reason); storage.pending_founder=nil; return end
    player.print(spawned.." engineers arrived, each in its own plot round yours. Use /npc help to talk to them.")
    return
  end
  if settings.global["npc-factions-native-starts"].value then
    local spawned,reason=spawn_native_roster(player)
    if not spawned then
      player.print(reason)
      -- Retrying the same static seed every ten seconds cannot repair its map.
      storage.pending_founder=nil
      storage.native_start_roster={tick=game.tick,requested=settings.global["npc-factions-count"].value,
        spawned=0,problem=reason}
      Dev.enable_observer(player)
      return
    end
    player.print(spawned.." engineers arrived in separate native starting areas. Use /npc help to talk to them.")
    Dev.enable_observer(player)
    return
  end
  local surface=player.surface
  storage.founding_force=player.force
  local spawned=0
  for id=1,settings.global["npc-factions-count"].value do
    local angle=(id-1)*2*math.pi/settings.global["npc-factions-count"].value
    local home,problem,survey=Settlements.find(player,id,settings.global["npc-factions-count"].value,
      settings.global["npc-factions-home-spacing"].value)
    local near={x=player.position.x+math.cos(angle)*(12+id*2),y=player.position.y+math.sin(angle)*(12+id*2)}
    local npc,reason
    if home then
      Settlements.generate_route(surface,near,home)
      npc,reason=Factory.spawn(id,surface,near,home,player.force,survey)
    else reason=problem end
    if not npc then game.print("NPC "..id.." could not spawn: "..reason)
    else spawned=spawned+1 end
  end
  storage.pending_founder=nil
  player.print(spawned.." NPC engineers arrived. Use /npc help to talk to them. Goal: first orbital platform; unsupported factory stages are shown in development status.")
  if spawned==0 then storage.founding_force=nil; storage.pending_founder=player.index; storage.spawn_retry=game.tick+600 end
end

-- Timing of each part of the tick (/npc-factions-test profile in fixtures, /npc-dev profile on in
-- development worlds), logged every 3600 ticks.
local timers={}
local function timed(name,f,...)
  local prof=storage.test and storage.test.profile or storage.profile
  if not prof then return f(...) end
  local t=helpers.create_profiler()
  local a,b=f(...)
  t.stop()
  if not timers[name] then timers[name]=helpers.create_profiler(true) end
  timers[name].add(t)
  return a,b
end
local function report_timers(tick)
  if not (storage.test and storage.test.profile or storage.profile) or tick%3600~=0 then return end
  for name,t in pairs(timers) do log({"","[npc-factions] profile ",name," over 3600 ticks: ",t}); t.reset(); t.stop() end
end

script.on_event(defines.events.on_tick,function(event)
  -- a test run's frame save loaded in a graphical client (tools/video.py): screenshots, once
  if storage.camera then Camera.tick() end
  -- the flight recorder (tests, /npc-record): reads the game, changes nothing
  if storage.recorder then Recorder.tick(event.tick) end
  -- the driving range (tests): its drivers drive trip after trip, nothing else ticks
  if storage.drive_range then DriveRange.tick(event.tick); return end
  -- the drive lab (tests): a player's driving written down, no engineers
  if storage.drive_lab then DriveLab.tick(event.tick); return end
  report_timers(event.tick)
  Dev.on_tick(event.tick)
  if event.tick%300==17 then MapView.tick(event.tick) end
  Scorecard.tick(event.tick)
  TeamWorld.tick(event.tick)
  TeamWaves.tick(event.tick)
  if TeamWorld.ready() then TeamResearch.tick(event.tick) end
  if storage.pending_founder and event.tick>=(storage.spawn_retry or 0) then
    local player=game.get_player(storage.pending_founder)
    if player and player.valid and player.character and player.character.valid then spawn_roster(player) end
  end
  for id=1,10 do
    local npc=storage.npcs[id]
    if npc then
      Behavior.ensure(npc)
      if event.tick%60==id then Agent.tick(npc,event.tick) end
      -- the factory ledger: a block's real rates, sampled about once a minute (base_memory.lua)
      if event.tick%60==(id+30)%60 then timed("ledger",BaseMemory.tick,npc,event.tick) end
      if not storage.test and SocialJobs.cancel_invalid then SocialJobs.cancel_invalid(npc,event.tick) end
      if not storage.test and SharedProjects.cancel_invalid then
        SharedProjects.cancel_invalid(npc,event.tick,Worker.cancel_job)
      end
      -- whether it gets anywhere, whatever holds it (before anything below can return: SO-5)
      if event.tick%60==(id+15)%60 then timed("watchdog",Watchdog.tick,npc,event.tick) end
      -- driving on autopilot it owns the body (combat would stop the car); else combat, then work
      if timed("drive",Drive.tick,npc,event.tick) then
      elseif storage.test or not timed("combat",Combat.tick,npc,event.tick) then
        timed("worker",Worker.tick,npc,event.tick); timed("factory",Factory.tick,npc,event.tick)
      end
    end
  end
  timed("diplomacy",Diplomacy.tick,event.tick)
  if not storage.test then timed("society",Society.tick,event.tick) end
  if event.tick%600==0 then SocialJobs.cleanup(event.tick);PlayerTrade.expire(event.tick) end
  timed("character",Character.tick,event.tick)
  timed("scouting",Scouting.tick,event.tick)
  timed("map",BaseMap.tick,event.tick)
  PlatformLaunch.tick(event.tick)
  Fixture.tick(event.tick)
  if storage.proving then Proving.tick(event.tick) end   -- (tools/proving.py: a practice scenario)
  TeachGui.tick(event.tick)
end)

Diplomacy.register_commands()
Fixture.register_commands()
Dev.register_commands()
DesignedStart.register_commands()
Agent.register_commands()
Recorder.register_commands()
Camera.register_commands()
TeamWorld.register_commands()
Proving.register_commands()
-- what engineers say and the strategist's tools as they finish, for the flight recorder
Agent.recorder=Recorder
Voice.recorder=Recorder
script.on_event(defines.events.on_gui_click,function(event)
  if TeachGui.on_click(event) then return end
  if Dev.on_gui_click then Dev.on_gui_click(event) end
end)
script.on_event(defines.events.on_lua_shortcut,TeachGui.on_shortcut)
script.on_event(defines.events.on_player_selected_area,function(event)
  local player=game.get_player(event.player_index)
  if not player then return end
  if event.item=="npc-show-area" then
    local reply,what=Teach.from_area(player,event.entities,event.area)
    player.print(reply)
    if what then   -- engineers with a strategist hear what they were shown, and may answer or ask about it
      for _,npc in pairs(storage.npcs or {}) do
        Agent.event(npc,{kind="shown",from=player.name,what=table.concat(what,", ")})
      end
    end
    player.clear_cursor()
    return
  end
  if event.item~="npc-mark-location" then return end
  local a=event.area
  Agent.location_marked(player,{x=(a.left_top.x+a.right_bottom.x)/2,y=(a.left_top.y+a.right_bottom.y)/2})
  player.clear_cursor()
end)
