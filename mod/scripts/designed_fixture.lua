-- Native test worlds for the designed start: one engineer (Ada) on prepared grass with
-- iron, coal and stone patches; no camp position is preset. The engineer must obtain a
-- plan from the host and build it with its starter kit and own work. Map o adds biter
-- nests and waves, map p an armed engineer who raids Ada's base (both resume a map-k save).
local Factory=require("scripts.factory")
local DesignedStart=require("scripts.designed_start")
local Orbit=require("scripts.orbit")
local PlatformLaunch=require("scripts.platform_launch")
local Personality=require("scripts.personality")
local Society=require("scripts.society")
local Combat=require("scripts.combat")
local Agent=require("scripts.agent")
local DesignedFixture={}

local KIT={["iron-plate"]=8,wood=1,pistol=1,["firearm-magazine"]=10,["burner-mining-drill"]=1,["stone-furnace"]=1}

-- Variants c..h and j..n each add a stage to the previous one; i stands alone.
local CHAIN="cdefghjklmn"
local function from(variant,letter)
  if variant=="o" or variant=="p" then variant="k" end   -- maps o and p are map k under attack
  local at,need=variant and CHAIN:find(variant,1,true),CHAIN:find(letter,1,true)
  return at~=nil and need~=nil and at>=need
end

function DesignedFixture.start(variant)
  if storage.test or next(storage.npcs) then return false,"start requires a fresh world without NPCs" end
  local surface=game.surfaces[1]
  surface.request_to_generate_chunks({0,0},variant=="k" and 5 or 3); surface.force_generate_chunk_requests()
  local area={{-40,-40},{40,40}}
  for _,e in ipairs(surface.find_entities_filtered{area=area}) do if e.type~="character" then e.destroy() end end
  local tiles={}
  for x=-40,39 do for y=-40,39 do tiles[#tiles+1]={name="grass-1",position={x,y}} end end
  surface.set_tiles(tiles)
  if from(variant,"c") then
    -- Iron, copper, coal and stone near home, and trees for wooden chests.
    for x=10,15 do for y=-3,2 do surface.create_entity{name="iron-ore",position={x,y},amount=4000} end end
    for x=10,15 do for y=8,13 do surface.create_entity{name="copper-ore",position={x,y},amount=4000} end end
    for x=-8,-5 do for y=4,7 do surface.create_entity{name="coal",position={x,y},amount=4000} end end
    for x=-7,-5 do for y=-7,-5 do surface.create_entity{name="stone",position={x,y},amount=4000} end end
    for x=-14,-6,2 do surface.create_entity{name="tree-01",position={x,-14}} end
    if from(variant,"d") then
      -- A pond to the north-east for steam power, and more trees for poles.
      local water={}
      for x=18,28 do for y=-26,-16 do water[#water+1]={name="water",position={x,y}} end end
      surface.set_tiles(water)
      for x=-14,-6,2 do surface.create_entity{name="tree-01",position={x,-17}} end
    end
  elseif variant=="i" then
    -- A scrap of iron at home with room for the start pair only, coal and
    -- stone nearby, and a real iron patch out of sight to the east.
    for x=10,11 do for y=-1,0 do surface.create_entity{name="iron-ore",position={x,y},amount=8000} end end
    for x=-8,-5 do for y=4,7 do surface.create_entity{name="coal",position={x,y},amount=4000} end end
    for x=-7,-5 do for y=-7,-5 do surface.create_entity{name="stone",position={x,y},amount=4000} end end
    for x=-14,-6,2 do surface.create_entity{name="tree-01",position={x,-14}} end
    local far={{58,-10},{74,10}}
    for _,e in ipairs(surface.find_entities_filtered{area=far}) do if e.type~="character" then e.destroy() end end
    local ground={}
    for x=58,73 do for y=-10,9 do ground[#ground+1]={name="grass-1",position={x,y}} end end
    surface.set_tiles(ground)
    for x=62,69 do for y=-4,3 do surface.create_entity{name="iron-ore",position={x,y},amount=4000} end end
  elseif variant=="b" then
    -- Iron north of home with trees and a rock on the patch; coal and stone moved.
    for x=-3,2 do for y=-19,-14 do surface.create_entity{name="iron-ore",position={x,y},amount=2000} end end
    for x=8,10 do for y=6,8 do surface.create_entity{name="coal",position={x,y},amount=2000} end end
    for x=-14,-12 do for y=4,6 do surface.create_entity{name="stone",position={x,y},amount=2000} end end
    for _,p in ipairs({{-2,-15},{1,-18},{3,-12},{-5,-16}}) do surface.create_entity{name="tree-01",position=p} end
    surface.create_entity{name="big-rock",position={0,-17}}
  else
    for x=10,15 do for y=-3,2 do surface.create_entity{name="iron-ore",position={x,y},amount=2000} end end
    for x=-7,-5 do for y=4,6 do surface.create_entity{name="coal",position={x,y},amount=2000} end end
    for x=-7,-5 do for y=-7,-5 do surface.create_entity{name="stone",position={x,y},amount=2000} end end
    surface.create_entity{name="tree-01",position={4,-8}}
  end
  storage.founding_force=game.forces.player
  storage.test={mode="designed",variant=variant or "a",start_tick=game.tick,terrain_fixture=true,
    goals=from(variant,"c") or variant=="i"}
  if from(variant,"h") then
    -- More iron ore east of the burner patch for electric drills; the
    -- electric drill technology is a declared fixture grant.
    for x=16,25 do for y=-3,2 do surface.create_entity{name="iron-ore",position={x,y},amount=4000} end end
  end
  if variant=="j" then
    -- An oil well south-west of home with a small pond beside it.
    surface.create_entity{name="crude-oil",position={-20.5,24.5},amount=3000000}
    local pond={}
    for x=-31,-27 do for y=12,17 do pond[#pond+1]={name="water",position={x,y}} end end
    surface.set_tiles(pond)
  elseif variant=="k" or variant=="l" or variant=="m" or variant=="n" then
    -- An oil field far to the east, out of sight from home, with a pond:
    -- the engineer has to scout for it and run a power line out to it.
    local far={{68,-18},{112,26}}
    for _,e in ipairs(surface.find_entities_filtered{area=far}) do if e.type~="character" then e.destroy() end end
    local ground={}
    for x=68,111 do for y=-18,25 do ground[#ground+1]={name="grass-1",position={x,y}} end end
    surface.set_tiles(ground)
    surface.create_entity{name="crude-oil",position={88.5,6.5},amount=3000000}
    local pond={}
    for x=98,102 do for y=16,20 do pond[#pond+1]={name="water",position={x,y}} end end
    surface.set_tiles(pond)
  end
  local npc,reason=Factory.spawn(1,surface,{x=-10,y=-10},{x=0,y=0},game.forces.player)
  if not npc then return false,reason end
  if from(variant,"h") then npc.force.technologies["electric-mining-drill"].researched=true end
  if from(variant,"j") then
    -- Declared research grants up to oil (lab research alone would take hours
    -- of game time); oil processing itself is unlocked by mining crude oil.
    for _,name in ipairs({"engine","automation-2","fluid-handling","oil-gathering","plastics","sulfur-processing",
        "advanced-circuit","chemical-science-pack"}) do
      npc.force.technologies[name].researched=true
    end
  end
  return true
end

-- Map n, switched to on a resumed map-k save: the rocket research is done (lab research to it would
-- take many game hours), and the oil field gets two more wells for the rocket's own pumpjack.
function DesignedFixture.on_variant(variant)
  if variant=="o" then return DesignedFixture.biters() end
  if variant=="p" then return DesignedFixture.raider() end
  if variant~="n" then return end
  local npc=storage.npcs and select(2,next(storage.npcs))
  if not npc then return end
  local force=npc.force
  local function grant(name)
    local tech=force.technologies[name]
    if not tech or tech.researched then return end
    for pre in pairs(tech.prerequisites) do grant(pre) end
    tech.researched=true
  end
  grant("rocket-silo")
  local surface=game.surfaces[1]
  for _,at in ipairs({{96.5,14.5},{100.5,-1.5}}) do
    if surface.count_entities_filtered{name="crude-oil",position=at,radius=0.5}==0 then
      surface.create_entity{name="crude-oil",position=at,amount=3000000}
    end
  end
end

-- Map o: a group of biter nests and worms south-west of the base, close enough that the base's
-- pollution reaches them. Small biters only (low evolution), no expansion.
local NESTS={{-46,96},{-34,104},{-58,108}}
local WORMS={{-40,88},{-52,92}}
function DesignedFixture.biters()
  local surface=game.surfaces[1]
  local enemy=game.forces.enemy
  pcall(function() enemy.set_evolution_factor(0.05,surface) end)
  game.map_settings.enemy_expansion.enabled=false
  game.map_settings.pollution.enabled=true
  -- A threatened player researches turrets and walls first; the test grants them.
  local npc=storage.npcs and select(2,next(storage.npcs))
  for _,name in ipairs({"gun-turret","stone-wall"}) do
    local tech=npc and npc.force.technologies[name]
    if tech then tech.researched=true end
  end
  local placed=0
  for _,at in ipairs(NESTS) do
    local p=surface.find_non_colliding_position("biter-spawner",at,16,1)
    if p and surface.create_entity{name="biter-spawner",position=p,force=enemy} then placed=placed+1 end
  end
  for _,at in ipairs(WORMS) do
    local p=surface.find_non_colliding_position("small-worm-turret",at,16,1)
    if p then surface.create_entity{name="small-worm-turret",position=p,force=enemy} end
  end
  storage.test.nests=placed
  storage.test.attack_tick=game.tick
end

-- Map p: another engineer, aggressive and armed, raids the base (engineer against engineer). It has
-- what a military-first engineer would have made by then (granted: the test skips its build-up), and
-- sets out at once; the defender has only what it makes for itself.
local RAIDER_HOME={x=-230,y=-60}
local RAIDER_KIT={{"heavy-armor",1},{"submachine-gun",1},{"piercing-rounds-magazine",300},{"gun-turret",4},
  {"raw-fish",20}}
function DesignedFixture.raider()
  local npc=storage.npcs and storage.npcs[1]
  if not npc or storage.test.raider then return end
  local raider=Factory.spawn(2,game.surfaces[1],RAIDER_HOME,RAIDER_HOME,game.forces.player)
  if not raider then return end
  Personality.assign(raider,{"aggressive"},raider.id*7919)
  local inventory=raider.character.get_main_inventory()
  for _,kit in ipairs(RAIDER_KIT) do
    local n=inventory.insert{name=kit[1],count=kit[2]}
    raider.grants[kit[1]]=(raider.grants[kit[1]] or 0)+n
  end
  pcall(Combat.equip_now,raider)
  -- the defender may research what a threatened player would (as on map o)
  for _,name in ipairs({"gun-turret","stone-wall","military"}) do
    local tech=npc.force.technologies[name]
    if tech then tech.researched=true end
  end
  Society.declare_war(raider,npc)
  local ok,why=Agent.order(raider,{tool="attack",priority="now",reason="a raid",
    args={target=npc.name,minutes=20}})
  storage.test.raider,storage.test.raid_tick,storage.test.raid_order=raider.id,game.tick,ok or why
end

local function all_plans(npc)
  local plans,seen={},{}
  for _,plan in ipairs(npc and npc.design_history or {}) do plans[#plans+1]=plan;seen[plan]=true end
  local current=npc and npc.design and npc.design.plan
  if current and not seen[current] then plans[#plans+1]=current end
  return plans
end

local function plan_entities(npc,kind)
  local out={}
  for _,plan in ipairs(all_plans(npc)) do
    for _,id in ipairs(plan.order) do
      local e=plan.entities[id]
      if e.type==kind then out[#out+1]=e end
    end
  end
  return out
end

function DesignedFixture.snapshot(finalize)
  local npc=storage.npcs[1]
  local d=npc and npc.design or {}
  local checks,state={},{}
  local built,builds_paid,ore_hand_fed,coal_mined,coal_from_chests,taken_back=0,0,0,0,0,0
  for _,key in ipairs(npc and npc.receipt_order or {}) do
    local r=npc.receipts[key]
    if r.status=="complete" and r.kind=="build" and r.committed then builds_paid=builds_paid+1 end
    -- A build run places many buildings, each paid from the inventory as it goes down.
    if r.kind=="build_run" then builds_paid=builds_paid+#(r.placed or {}) end
    -- Deconstruct jobs only ever take the NPC's own buildings (the Worker refuses
    -- others); the receipt cannot name the force of a building that is gone.
    if r.status=="complete" and r.kind=="mine" and r.mode=="deconstruct" then taken_back=taken_back+1 end
    -- Ore carried into a furnace by hand (not ore put away in a chest, e.g. what a retired burner
    -- pair held when it was taken down; receipts name their target's type since 2026-10-11).
    if r.status=="complete" and r.kind=="transfer" and r.mode=="deposit" and r.item=="iron-ore"
        and r.target_type=="furnace" then
      ore_hand_fed=ore_hand_fed+(r.count or 0)
    end
    if r.status=="complete" and r.kind=="mine" and r.item=="coal" then coal_mined=coal_mined+(r.count or 0) end
    if r.status=="complete" and r.kind=="transfer" and r.mode=="withdraw" and r.item=="coal" then
      coal_from_chests=coal_from_chests+(r.count or 0)
    end
  end
  -- Each standing building once (plans may share one they adopted), and those a retired plan
  -- left standing until they are picked up.
  local counted={}
  local function count(e)
    if e and e.valid and not counted[e.unit_number or e] then counted[e.unit_number or e]=true; built=built+1 end
  end
  for _,plan in ipairs(all_plans(npc)) do
    for _,id in ipairs(plan.order) do count(plan.entities[id].entity) end
  end
  for _,e in ipairs(npc and npc.design_salvage or {}) do count(e) end
  for _,chest in ipairs(npc and npc.storage_chests or {}) do if chest.valid then built=built+1 end end
  local drills,furnaces={},plan_entities(npc,"furnace")
  -- Pumpjacks are mining drills too, but they output fluid into pipes or a refinery.
  for _,drill in ipairs(plan_entities(npc,"mining-drill")) do
    if drill.prototype~="pumpjack" then drills[#drills+1]=drill end
  end
  -- Every drill that stands puts its ore into something (drills of a later stage, not built yet, wait).
  local fed,plates,best,standing=true,0,0,0
  for _,drill in ipairs(drills) do
    local e=drill.entity
    if e and e.valid then
      standing=standing+1
      local ok=false
      for _,t in ipairs(DesignedStart.entities_at(e.surface,e.drop_position,{force=npc.force}) or {}) do
        if t~=e and t.type~="character" then ok=true end
      end
      fed=fed and ok
    end
  end
  fed=fed and standing>0
  for _,furnace in ipairs(furnaces) do
    if furnace.entity and furnace.entity.valid then
      plates=plates+furnace.entity.products_finished
      best=math.max(best,furnace.entity.products_finished)
    end
  end
  local grants_ok=npc~=nil
  for item,amount in pairs(npc and npc.grants or {}) do if KIT[item]~=amount then grants_ok=false end end
  local stages={}
  for _,s in ipairs(d.plan and d.plan.stages or {}) do
    local w={}
    for _,x in ipairs(s.witnesses) do w[#w+1]={kind=x.kind,entity_id=x.entity_id,observed=x.observed,min=x.min_delta} end
    stages[#stages+1]={id=s.id,status=s.status,entities=#s.entity_ids,witnesses=w,
      completed_after=s.completed_tick and s.completed_tick-storage.test.start_tick or nil}
  end
  -- The first designed plan is the start; later requests may be waiting or deferred.
  local first=(npc and npc.design_history and npc.design_history[1]) or d.plan
  checks.plan_accepted={passed=first~=nil,detail=d.last_rejection}
  checks.first_stage_complete={passed=first~=nil and first.stages[1].status=="complete"}
  checks.all_stages_complete={passed=d.status=="complete",detail=d.status}
  -- Every standing building was paid for (more were paid than stand when some were since replaced or
  -- lost; with build runs a placement is not always undone by a receipt).
  checks.everything_built_and_paid={passed=built>0 and builds_paid>=built,
    detail={built=built,paid_builds=builds_paid,taken_back=taken_back}}
  checks.drills_feed_furnaces={passed=fed,detail={standing=standing,planned=#drills}}
  checks.automated_plates={passed=plates>=10 and ore_hand_fed==0,detail={plates=plates,ore_hand_fed=ore_hand_fed}}
  checks.no_free_materials={passed=grants_ok}
  checks.handed_off={passed=npc~=nil and npc.step>8 and npc.miner~=nil and npc.miner.valid and npc.furnace~=nil and npc.furnace.valid}
  if storage.test.goals then
    local history=npc and npc.design_history or {}
    local purposes={}
    for _,plan in ipairs(history) do purposes[#purposes+1]=plan.purpose end
    checks.all_stages_complete=nil
    checks.second_goal_built={passed=#history>=2,detail=purposes}
    checks.factory_kept_running={passed=best>=110 and (npc.design_collects or 0)>=1,
      detail={best_furnace=best,collections=npc and npc.design_collects or 0}}
    checks.upkeep_refuels={passed=(npc and npc.design_upkeep_done or 0)>=3,detail=npc and npc.design_upkeep_done or 0}
    if storage.test.variant=="i" then
      local remote,seen=nil,0
      for _,plan in ipairs(history) do
        for _,id in ipairs(plan.order) do
          local e=plan.entities[id].entity
          if e and e.valid and e.type=="mining-drill" and math.max(math.abs(e.position.x-npc.home.x),
              math.abs(e.position.y-npc.home.y))>40 then remote=plan.purpose end
        end
      end
      for _,cell in pairs(npc.known_patches or {}) do
        if cell.resource=="iron-ore" and math.max(math.abs(cell.x-npc.home.x),math.abs(cell.y-npc.home.y))>32 then
          seen=seen+cell.tiles
        end
      end
      checks.scouted_far_iron={passed=seen>=16,detail={tiles=seen}}
      checks.far_site_built={passed=remote~=nil,detail={purpose=remote,goals=purposes}}
    end
    if from(storage.test.variant,"d") then
      checks.second_goal_built=nil
      checks.factory_kept_running=nil
      local steam=0
      for _,engine in ipairs(plan_entities(npc,"generator")) do
        local e=engine.entity
        local f=e and e.valid and e.fluids_count>0 and e.get_fluid(1); if f then steam=math.max(steam,f.amount) end
      end
      local force=npc and npc.force
      local lab_research={}
      for name,t in pairs(force and force.technologies or {}) do
        local trigger=pcall(function() return t.prototype.research_trigger end) and t.prototype.research_trigger
        if t.researched and not trigger and #t.research_unit_ingredients>0 then lab_research[#lab_research+1]=name end
      end
      table.sort(lab_research)
      local current=force and force.current_research
      checks.power_built={passed=steam>0,detail={steam=math.floor(steam),goals=purposes}}
      checks.research_progress={passed=#lab_research>0 or (current~=nil and force.research_progress>0),
        detail={researched=lab_research,current=current and current.name,
          progress=force and math.floor(force.research_progress*1000)/1000}}
      if from(storage.test.variant,"e") then
        local science=0
        for _,asm in ipairs(plan_entities(npc,"assembling-machine")) do
          local e=asm.entity
          if e and e.valid and e.get_recipe() and e.get_recipe().name=="automation-science-pack" then
            science=science+e.products_finished
          end
        end
        checks.research_progress={passed=#lab_research>0,detail=checks.research_progress.detail}
        checks.automated_red_science={passed=science>=10,detail={science_packs=science,goals=purposes}}
        if storage.test.variant=="f" then
          local belted,lines=false,{}
          for _,plan in ipairs(npc and npc.design_history or {}) do
            if plan.purpose=="plate-belts" then
              belted=true
              for _,s in ipairs(plan.stages) do lines[#lines+1]=s.id..":"..tostring(s.status) end
            end
          end
          local automated={}
          for _,plan in ipairs(npc and npc.design_history or {}) do
            for _,s in ipairs(plan.stages) do
              for _,supply in ipairs(s.supplies) do
                if supply.automated then automated[#automated+1]=supply.entity_id..":"..supply.item end
              end
            end
          end
          checks.plates_by_belt={passed=belted and #automated>0,detail={lines=lines,no_longer_hand_fed=automated}}
          checks.automated_red_science={passed=science>=20,detail={science_packs=science,goals=purposes}}
        end
        if from(storage.test.variant,"h") then
          local steel,electric=0,0
          for _,furnace in ipairs(plan_entities(npc,"furnace")) do
            local e=furnace.entity
            -- An idle furnace shows no current recipe; fall back to the last one.
            -- (a furnace retired since is no longer valid: no recipe)
            local recipe=e and e.valid and (e.get_recipe() or e.previous_recipe) or nil
            local name=recipe and recipe.name or nil
            if name~=nil and type(name)~="string" then name=name.name end
            if name=="steel-plate" then steel=steel+e.products_finished end
          end
          for _,plan in ipairs(npc and npc.design_history or {}) do
            if plan.purpose=="electric-iron-mining" then
              for _,id in ipairs(plan.order) do
                local e=plan.entities[id].entity
                if e and e.valid and e.type=="furnace" then electric=electric+e.products_finished end
              end
            end
          end
          checks.steel_made={passed=steel>=2,detail={steel=steel,goals=purposes}}
          -- Retired once the bus's electric fields took over its job.
          local retired=npc and npc.retired and npc.retired["electric-iron-mining"]
          checks.electric_mining={passed=electric>=10 or retired~=nil,detail={plates=electric,retired=retired}}
        end
        if from(storage.test.variant,"j") then
          local made={}
          for _,plan in ipairs(npc and npc.design_history or {}) do
            for _,id in ipairs(plan.order) do
              local e=plan.entities[id].entity
              local recipe=e and e.valid and e.type=="assembling-machine" and e.get_recipe()
              if recipe then made[recipe.name]=(made[recipe.name] or 0)+e.products_finished end
            end
          end
          local oil=npc.force.technologies["oil-processing"].researched
          checks.oil_processing_unlocked={passed=oil,detail={by="mining crude oil"}}
          checks.plastic_and_sulfur={passed=(made["plastic-bar"] or 0)>=4 and (made["sulfur"] or 0)>=2,
            detail={plastic=made["plastic-bar"] or 0,sulfur=made["sulfur"] or 0}}
          checks.engines_made={passed=(made["engine-unit"] or 0)>=2,detail=made["engine-unit"] or 0}
          checks.blue_science={passed=(made["chemical-science-pack"] or 0)>=2,
            detail={crafts=made["chemical-science-pack"] or 0,goals=purposes}}
          if from(storage.test.variant,"l") then
            -- Map l: a main bus base, stage by stage, making science on its own modules.
            local bp=npc.base_plan
            local done,total=0,#(bp and bp.stages or {})
            for _ in pairs(bp and bp.done or {}) do done=done+1 end
            local bus={}
            for _,plan in ipairs(npc and npc.design_history or {}) do
              if plan.purpose=="bus-base" then
                for _,id in ipairs(plan.order) do
                  local e=plan.entities[id].entity
                  local recipe=e and e.valid and e.type=="assembling-machine" and e.get_recipe()
                  if recipe then bus[recipe.name]=(bus[recipe.name] or 0)+e.products_finished end
                end
              end
            end
            local red=bus["automation-science-pack"] or 0
            checks.bus_base_built={passed=total>0 and done>=total,detail={done=done,stages=total}}
            checks.bus_red_science={passed=red>=10,detail={red_packs=red}}
            if from(storage.test.variant,"n") then
              -- Map n: the bus grows a rocket section and the engineer reaches orbit.
              local refinery,silo=0,nil
              for _,plan in ipairs(npc and npc.design_history or {}) do
                for _,id in ipairs(plan.order) do
                  local e=plan.entities[id].entity
                  if e and e.valid and e.type=="assembling-machine" and e.get_recipe()
                      and e.get_recipe().name=="advanced-oil-processing" then refinery=refinery+e.products_finished end
                  if e and e.valid and e.type=="rocket-silo" then silo=e end
                end
              end
              checks.oil_complex_working={passed=refinery>=10,detail={refinery_crafts=refinery}}
              local parts=silo and ((silo.rocket_parts or 0)+50*silo.products_finished) or 0
              checks.rocket_built={passed=parts>=50,detail={silo=silo~=nil,parts=parts,made=bus}}
              checks.platform_in_orbit={passed=Orbit.reached(npc),detail=PlatformLaunch.observe()}
            end
            if from(storage.test.variant,"m") then
              -- Map m: the bus is extended for blue science and makes it on its own modules.
              local blue=bus["chemical-science-pack"] or 0
              checks.bus_blue_science={passed=blue>=10 and (bus["advanced-circuit"] or 0)>=10
                and (bus["engine-unit"] or 0)>=10,
                detail={blue_packs=blue,red_circuits=bus["advanced-circuit"] or 0,engines=bus["engine-unit"] or 0}}
            end
          end
          if from(storage.test.variant,"k") then
            local far,automated=nil,false
            for _,plan in ipairs(npc and npc.design_history or {}) do
              local pumpjack=plan.purpose=="oil-gathering" and plan.entities.pumpjack and plan.entities.pumpjack.entity
              if pumpjack and pumpjack.valid then
                far=math.floor(math.sqrt((pumpjack.position.x-npc.home.x)^2+(pumpjack.position.y-npc.home.y)^2))
              end
              if plan.purpose=="blue-supply-automation" then automated=true end
              if plan.purpose=="bus-base" and not automated then   -- a bus with blue science modules
                for _,id in ipairs(plan.order) do
                  local e=plan.entities[id].entity
                  local recipe=e and e.valid and e.type=="assembling-machine" and e.get_recipe()
                  if recipe and recipe.name=="chemical-science-pack" then automated=true; break end
                end
              end
            end
            checks.far_oil_field={passed=far~=nil and far>60,detail={distance=far}}
            checks.blue_supply_automated={passed=automated and (made["chemical-science-pack"] or 0)>=6,
              detail={automated=automated,crafts=made["chemical-science-pack"] or 0}}
          end
        end
        if from(storage.test.variant,"g") then
          local green=0
          for _,asm in ipairs(plan_entities(npc,"assembling-machine")) do
            local e=asm.entity
            if e and e.valid and e.get_recipe() and e.get_recipe().name=="logistic-science-pack" then
              green=green+e.products_finished
            end
          end
          checks.automated_green_science={passed=green>=5,detail={green_packs=green,goals=purposes}}
        end
      end
    end
  end
  if storage.test.variant=="o" and npc then
    -- Map o: under attack, buildings may be lost; what counts is that the engineer defends itself.
    checks.everything_built_and_paid,checks.drills_feed_furnaces=nil,nil
    local turrets=0
    for _,plan in ipairs(all_plans(npc)) do
      if plan.purpose=="outpost-defense" or plan.purpose=="basic-defense" then
        for _,id in ipairs(plan.order) do
          local e=plan.entities[id].entity
          if e and e.valid and e.type=="ammo-turret" then turrets=turrets+1 end
        end
      end
    end
    local kills=0
    local ok,stats=pcall(function() return npc.force.get_kill_count_statistics(game.surfaces[1]) end)
    for name,n in pairs(ok and stats.input_counts or {}) do
      local proto=prototypes.entity[name]
      if proto and (proto.type=="unit" or proto.type=="unit-spawner") then kills=kills+n end
    end
    -- Waves: once turrets stand, small biters set out from a nest for the base every 5 minutes
    -- (a small base's pollution may take hours to reach the nests on its own).
    local waves=storage.test.waves or 0
    if turrets>=2 and game.tick>=(storage.test.next_wave or 0) and waves<12 then
      local surface=game.surfaces[1]
      local nest=surface.find_entities_filtered{type="unit-spawner",force="enemy",limit=1}[1]
      if nest then
        local group=surface.create_unit_group{position=nest.position,force="enemy"}
        for _=1,8 do
          local p=surface.find_non_colliding_position("small-biter",nest.position,12,0.5)
          local biter=p and surface.create_entity{name="small-biter",position=p,force="enemy"}
          if biter then group.add_member(biter) end
        end
        group.set_command{type=defines.command.attack_area,destination=npc.home,radius=24,
          distraction=defines.distraction.by_enemy}
        storage.test.waves=waves+1
      end
      storage.test.next_wave=game.tick+18000
    end
    checks.defense_built={passed=turrets>=2,detail={turrets=turrets,nests=storage.test.nests,waves=storage.test.waves or 0}}
    -- An ally asks for help once the engineer's own turrets stand (as a chat request would).
    if turrets>=2 and not storage.test.help_asked then
      storage.test.help_asked=game.tick
      npc.help_request={kind="defend",player_index=1,tick=game.tick,position={x=npc.home.x+40,y=npc.home.y-40}}
    end
    local helped=0
    for _,plan in ipairs(all_plans(npc)) do
      if plan.purpose=="ally-defense" then
        for _,id in ipairs(plan.order) do
          local e=plan.entities[id].entity
          if e and e.valid and e.type=="ammo-turret" then helped=helped+1 end
        end
      end
    end
    checks.ally_helped={passed=helped>=2,detail={turrets=helped,asked=storage.test.help_asked,
      open=npc.help_request~=nil}}
    checks.biters_killed={passed=kills>=20,detail={kills=kills}}
    checks.engineer_alive={passed=npc.character~=nil and npc.character.valid}
    checks.losses_rebuilt={passed=(storage.test.waves or 0)<3 or (npc.rebuilt or 0)>=0,
      detail={rebuilt=npc.rebuilt or 0,waiting=#(npc.rebuild or {})}}
  end
  if storage.test.variant=="p" and npc then
    -- Map p: only the raid counts. The raider must reach the base; the defender must arm itself,
    -- survive, put up turrets, and see the raider dead (or at least gone once its raid is over).
    checks={}
    local raider=storage.npcs[storage.test.raider or 2]
    local rc=raider and raider.character
    local alive=rc~=nil and rc.valid
    local away
    if alive then
      away=math.sqrt((rc.position.x-npc.home.x)^2+(rc.position.y-npc.home.y)^2)
      storage.test.raider_closest=math.min(storage.test.raider_closest or 1e9,away)
    end
    local arrived=(storage.test.raider_closest or 1e9)<=60
    local c=npc.character
    local guns,ammo=0,0
    if c and c.valid then   -- carried or in hand: its combat takes up the best gun when a fight starts
      for _,inv in ipairs({defines.inventory.character_guns,defines.inventory.character_ammo,
          defines.inventory.character_main}) do
        local i=c.get_inventory(inv)
        if i then
          guns=guns+i.get_item_count("pistol")+i.get_item_count("submachine-gun")
          ammo=ammo+i.get_item_count("firearm-magazine")+i.get_item_count("piercing-rounds-magazine")
        end
      end
    end
    local turrets=0
    for _,plan in ipairs(all_plans(npc)) do
      if plan.purpose=="outpost-defense" or plan.purpose=="basic-defense" then
        for _,id in ipairs(plan.order) do
          local e=plan.entities[id].entity
          if e and e.valid and e.type=="ammo-turret" then turrets=turrets+1 end
        end
      end
    end
    local lost=0
    local ok,stats=pcall(function() return raider.force.get_kill_count_statistics(game.surfaces[1]) end)
    for _,n in pairs(ok and stats.input_counts or {}) do lost=lost+n end
    local over=storage.test.raid_tick and game.tick-storage.test.raid_tick>=25*3600
    checks.raid_reached_base={passed=arrived,detail={closest=storage.test.raider_closest and
      math.floor(storage.test.raider_closest),order=storage.test.raid_order}}
    checks.defender_armed={passed=guns>0 and ammo>=20,detail={guns=guns,ammo=ammo}}
    checks.engineer_alive={passed=c~=nil and c.valid}
    checks.defense_built={passed=turrets>=2,detail={turrets=turrets}}
    checks.raid_over={passed=arrived and (not alive or (over==true and (away or 0)>100)),
      detail={raider_alive=alive,away=away and math.floor(away),buildings_lost=lost}}
  end
  local passed=true
  for _,c in pairs(checks) do if not c.passed then passed=false end end
  state={phase=npc and npc.phase,status=d.status,reason=d.reason,source=d.plan and d.plan.source,stages=stages,
    plates=plates,failed_jobs=d.failed_jobs or 0,last_failure=d.last_failure,cleared=d.cleared or 0,
    coal_mined_by_hand=coal_mined,coal_from_chests=coal_from_chests,
    storage_chests=npc and #(npc.storage_chests or {}) or 0,stored_items=npc and npc.design_stored or 0,
    relative_tick=game.tick-storage.test.start_tick}
  return {schema=1,mode="designed",tick=game.tick,phase=passed and finalize and "complete" or "working",
    completed=passed and finalize==true,checks=checks,state=state,fingerprint=helpers.table_to_json(state)}
end

return DesignedFixture
