local StarterIron=require('scripts.starter_iron')
-- The scripted starter camp (a burner drill feeding a furnace) and, in the scripted start mode, the
-- project chain that grows it; designed engineers fall back to the camp when no plan arrives.
local Worker = require("scripts.worker")
local Behavior = require("scripts.behavior")
local Projects = require("scripts.factory_projects")
local Society = require("scripts.society")
local Personality = require("scripts.personality")
local SocialJobs = require("scripts.social_jobs")
local ProjectChoices=require("scripts.project_choices")
local Strategy=require("scripts.strategy_policy")
local Repairs=require("scripts.repairs")
local PlayerTrade=require("scripts.player_trade")
local RailTrade=require("scripts.rail_trade_projects")
local Defense=require("scripts.defense_projects")
RailTrade.bind_social_adapter(SocialJobs)
local SharedProjects=require("scripts.shared_projects")
local ResourceMemory=require('scripts.resource_memory')
local DesignedStart=require('scripts.designed_start')
local Agent=require('scripts.agent')
local Kit=require('scripts.defense_kit')   -- (its own defense kit where biters are about)
local Rescue=require('scripts.rescue')
local Factory = {}
local KIT = {{"iron-plate",8},{"wood",1},{"pistol",1},{"firearm-magazine",10},
  {"burner-mining-drill",1},{"stone-furnace",1}}
local STEPS = {"wood","coal","iron","miner","furnace","miner-fuel","furnace-fuel","ore"}
local NAMES = {"Ada","Borin","Cora","Dax","Elin","Fenn","Gita","Harlan","Iona","Jory"}
local COLORS = {
  {r=0.9,g=0.4,b=0.2}, {r=0.2,g=0.7,b=0.9}, {r=0.6,g=0.3,b=0.9},
  {r=0.2,g=0.8,b=0.4}, {r=0.95,g=0.75,b=0.2}, {r=0.95,g=0.3,b=0.65},
  {r=0.4,g=0.8,b=0.75}, {r=0.65,g=0.55,b=0.3}, {r=0.45,g=0.55,b=0.95},
  {r=0.75,g=0.85,b=0.3},
}

local function distance(a,b) local x,y=a.x-b.x,a.y-b.y; return math.sqrt(x*x+y*y) end
local function item_count(npc,item) return npc.character.get_main_inventory().get_item_count(item) end

function Factory.spawn(id,surface,spawn,home,founder,survey)
  if not NAMES[id] then return nil,"invalid_npc_id" end
  local force=game.forces["npc-faction-"..id] or game.create_force("npc-faction-"..id)
  -- At peace with the players (a cease-fire: nobody shoots, nothing is shared), not on their team:
  -- an alliance is something it agrees to later, or not. In the team world everyone is allied from
  -- the start (TeamWorld.ally_all, once the engineer is in the roster).
  force.set_friend(founder,false); founder.set_friend(force,false)
  force.set_cease_fire(founder,true); founder.set_cease_fire(force,true)
  for _,other in pairs(storage.npcs) do
    force.set_cease_fire(other.force,true); other.force.set_cease_fire(force,true)
  end
  local pos=surface.find_non_colliding_position("character",spawn,24,0.5)
  if not pos then return nil,"no_spawn_space" end
  local c=surface.create_entity{name="character",position=pos,force=force}
  if not c then return nil,"character_creation_failed" end
  c.color=COLORS[id]
  local npc={id=id,name=NAMES[id],character=c,force=force,surface_index=surface.index,
    created_tick=game.tick,
    home={x=home.x or home[1],y=home.y or home[2]},spawn={x=pos.x,y=pos.y},jobs={},receipts={},receipt_order={},
    failures={},completed_jobs={},transfer_permissions={},grants={},phase="finding-iron",step=1,
    profile={resource_conservatism=settings.global["npc-factions-conservation"].value,
      expansion_drive=settings.global["npc-factions-expansion"].value,
      cooperation=settings.global["npc-factions-cooperation"].value,
      risk_tolerance=settings.global["npc-factions-risk"].value},
    plan_revision=0,decisions=0,travelled=0,last_position={x=pos.x,y=pos.y}}
  Behavior.ensure(npc)
  if survey then
    local attached,reason=ResourceMemory.attach(npc,survey)
    if not attached then npc.camp_survey_problem=reason end
  end
  for _,entry in ipairs(KIT) do
    if prototypes.item[entry[1]] then   -- (a mod set without it: none; the quick start finds its own gun, boosts.lua)
    local inserted=c.get_main_inventory().insert{name=entry[1],count=entry[2]}
    if inserted~=entry[2] then error("NPC starter inventory did not fit") end
    npc.grants[entry[1]]=inserted
    end
  end
  npc.kit_granted=true
  storage.npcs[id]=npc
  rendering.draw_text{text=npc.name,target=c,surface=surface,target_offset={0,-2.3},
    color=c.color,alignment="center",scale=1}
  return npc
end

local function planned_job(npc,kind,spec)
  npc.plan_revision=npc.plan_revision+1
  local job=spec or {}; job.kind=kind; job.id="plan-"..npc.plan_revision
  local ok,id=Worker.enqueue(npc,job)
  if ok then
    npc.pending_job=id
    if kind=="mine" and npc.step<=3 then npc.bootstrap_current_count=job.count end
  else
    Projects.reject_unbound(npc,job,id)
    npc.phase="enqueue-failed:"..tostring(id)
  end
end

-- Its hurt buildings mended while no plan of its own goes up (W-33): true when a job was given.
local function mend(npc,tick)
  local job,meta=DesignedStart.mend_now(npc,tick)
  if not job then return false end
  planned_job(npc,job.kind,job)
  if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,meta);npc.phase="designed:upkeep:"..meta.upkeep end
  return true
end

local function find_module(npc)
  if game.tick<(npc.starter_iron_retry_at or 0)then return false end
  local selected,reason,diagnostic=StarterIron.select(npc)
  npc.starter_iron_diagnostic=diagnostic
  if not selected then
    npc.phase="waiting-for-rich-iron:"..tostring(reason);npc.starter_iron_retry_at=game.tick+600;return false
  end
  npc.module=selected.position;npc.module_direction=selected.direction
  npc.furnace_position=selected.furnace_position;npc.starter_iron_selection=selected
  npc.phase="gathering"
  planned_job(npc,"move",{position=npc.home});npc.moving_home=true
  return true
end

local function recover(npc,receipt,tick)
  npc.recovery_count=(npc.recovery_count or 0)+1
  npc.last_problem=receipt.reason
  npc.retry_at=tick+Behavior.recovery_delay(npc)
  if npc.moving_home then
    if npc.recovery_count<=3 then
      planned_job(npc,"move",{position=npc.home})
    else npc.phase="needs-help:camp_unreachable";npc.suspended=true end
    return
  end
  if npc.step==1 and receipt.reason=="no_local_resource" and item_count(npc,"wood")>=1 then
    npc.step=2; npc.phase="using-starter-wood"; return
  end
  -- Preserve the drill output port by physically clearing a neutral mineable
  -- obstruction first. No scripted obstacle deletion or free replacement item.
  if npc.step==5 and string.find(receipt.reason or "","placement",1,true) then
    local surface=npc.character.surface
    local blockers=surface.find_entities_filtered{position=npc.furnace_position,radius=1.5,type={"tree","simple-entity"},force="neutral"}
    for _,e in ipairs(blockers) do
      if e.minable then
        local products=e.prototype.mineable_properties.products or {}
        for _,product in ipairs(products) do
          if product.type=="item" then
            npc.obstruction_furnace_held=item_count(npc,"stone-furnace")==1
            npc.clearing_obstacle=true
            planned_job(npc,"mine",{target=e,item=product.name,count=1})
            return
          end
        end
      end
    end
    npc.phase="needs-help:factory-port-blocked"; npc.suspended=true
  end
  if npc.recovery_count>8 then npc.phase="needs-help:"..tostring(receipt.reason); npc.suspended=true end
end

local function schedule_obstruction(npc,tick)
  local obstruction=Projects.obstruction_maintenance(npc,tick)
  if not obstruction then return false end
  planned_job(npc,obstruction.kind,obstruction)
  if npc.pending_job then
    if not Projects.bind_obstruction(npc,npc.pending_job) then
      local rejected_id=npc.pending_job
      Worker.cancel_job(npc,rejected_id,"obstruction_bind_rejected")
      Projects.reject_obstruction(npc,"obstruction_bind_rejected")
      if npc.receipts[rejected_id] then npc.pending_job=nil end
    else npc.phase="recovering-blocked-clearance" end
  else Projects.reject_obstruction(npc,"obstruction_enqueue_rejected") end
  return true
end

-- Back after it dies, as a player comes back: after a player's respawn time, at home (its spawn if home has
-- no room), with its plans, memory and base its own and nothing in hand (what it carried lies in its
-- corpse). Its body was lost for good to a biter attack in a one-engineer run, 2026-10-24.
-- Never back into the fight that killed it: only where no biters are near (home, else its spawn, else the
-- team's spawn), else it waits; and each death soon after the last doubles the wait (it came back at home
-- while biters held it, and died 48 times running, one-engineer run 2026-10-25).
-- OFF for now: a body brought back left state in other modules made for the old one (the game hung in a
-- loop, its memory past 25 GB, right after a respawn, 2026-10-25). Bringing an engineer back takes resetting
-- everything a body carries in every module (jobs, combat, routes, the agent's march), to be designed as a
-- whole; until then a lost body stays lost, and a test reloads its last checkpoint (the user's call).
Factory.RESPAWN=false
Factory.RESPAWN_TICKS=600
Factory.RESPAWN_SAFE=48        -- tiles round a respawn point free of biters
Factory.RESPAWN_RECENT=36000   -- deaths this close together back the wait off
function Factory.respawn_delay(npc,tick)
  local recent=0
  for _,t in ipairs(npc.respawn_ticks or {}) do if tick-t<Factory.RESPAWN_RECENT then recent=recent+1 end end
  return Factory.RESPAWN_TICKS*2^math.min(5,recent)
end
function Factory.respawn(npc,tick)
  local surface=npc.surface_index and game.surfaces[npc.surface_index] or game.surfaces[1]
  if not surface then return false end
  if tick<(npc.respawn_retry or 0) then return false end
  local team_spawn
  pcall(function() team_spawn=game.forces.player.get_spawn_position(surface) end)
  local pos
  for _,at in ipairs({npc.home,npc.spawn,team_spawn}) do
    local p=at and surface.find_non_colliding_position("character",at,24,0.5)
    if p and surface.count_entities_filtered{force="enemy",type="unit",position=p,radius=Factory.RESPAWN_SAFE,limit=1}==0 then
      pos=p
      break
    end
  end
  if not pos then npc.respawn_retry=tick+300; return false end   -- (biters everywhere it could come back: later)
  local c=surface.create_entity{name="character",position=pos,force=npc.force}
  if not c then return false end
  c.color=COLORS[npc.id] or c.color
  local fell=npc.last_position   -- (where it died: what it carried lies there, fetched once it is calm)
  if fell then npc.corpse_at={x=fell.x,y=fell.y,tick=tick} end
  npc.character=c
  npc.jobs={};npc.pending_job=nil;npc.current_job=nil
  npc.last_position={x=pos.x,y=pos.y}
  npc.loss_recorded=nil
  npc.respawns=(npc.respawns or 0)+1
  npc.respawn_ticks=npc.respawn_ticks or {}
  table.insert(npc.respawn_ticks,tick)
  while #npc.respawn_ticks>8 do table.remove(npc.respawn_ticks,1) end
  npc.phase="respawned"
  log("[npc-factions] npc="..npc.id.." respawned at "..math.floor(pos.x)..","..math.floor(pos.y)
    .." (respawn "..npc.respawns..", tick "..tick..")")
  return true
end

local Navigation=require("scripts.navigation")
-- Whether its factory is its plans (the designed start), not the scripted start's chain.
function Factory.designed_engineer(npc)
  return npc.design~=nil or (npc.design_history~=nil and #npc.design_history>0)
end

function Factory.tick(npc,tick)
  if not npc.character or not npc.character.valid then
    if not npc.loss_recorded then
      -- Worker has already committed a body_missing receipt if needed. Attribute
      -- actual physical failure once; unresolved offers receive no invented score.
      if npc.social_job and npc.pending_job and npc.receipts[npc.pending_job] then
        SocialJobs.complete(npc,npc.social_job,npc.receipts[npc.pending_job])
      end
      if npc.pending_job and npc.receipts[npc.pending_job] then
        RailTrade.on_receipt(npc,npc.receipts[npc.pending_job])
      end
      Strategy.actor_lost(npc)
      Society.ensure(npc).pending_intent=nil
      npc.pending_job=nil;npc.social_job=nil;npc.project_strategy_token=nil;npc.rail_strategy_token=nil
      npc.loss_recorded=tick
    end
    npc.phase="body-lost"
    if Factory.RESPAWN and tick>=npc.loss_recorded+Factory.respawn_delay(npc,tick) then Factory.respawn(npc,tick) end
    return
  end
  local p=npc.character.position
  npc.travelled=npc.travelled+distance(p,npc.last_position)
  npc.last_position={x=p.x,y=p.y}
  DesignedStart.crafting_pace(npc,tick)   -- (its hands' speed, by what is being crafted)
  DesignedStart.robots_tick(npc,tick)     -- (its robots given at its tier: W-26)
  Projects.resume_reachable_build_approach(npc,tick)
  if Navigation.idle_tick(npc,tick) then return end
  local suspended_obstruction=Projects.suspended_obstruction_mode(npc,tick)
  -- (an engineer of plans, set aside as "factory destroyed" by the scripted start's check below before it knew
  -- better: back to work)
  if npc.suspended and npc.phase=="needs-help:factory-destroyed" and Factory.designed_engineer(npc) then
    npc.suspended=nil
  end
  if npc.suspended and npc.phase~="needs-help:factory-destroyed" and not suspended_obstruction then return end
  if npc.pending_job then
    Projects.observe_obstruction_navigation(npc,tick)
    local receipt=npc.receipts[npc.pending_job]
    if not receipt then return end
    if Projects.obstruction_pending(npc) then
      -- Both slots stay bound through helper authentication and conservation.
      if not Projects.receipt_matches_binding(npc,receipt) then
        npc.phase="obstruction-receipt-mismatch";return
      end
      local handled,reason=Projects.on_obstruction_receipt(npc,receipt,tick)
      if not handled then npc.phase="obstruction-receipt-mismatch";return end
      npc.phase=reason and "obstruction-held:"..reason or "recovering-blocked-clearance"
      return
    end
    if npc.factory_project and npc.factory_project.pending_job==npc.pending_job
        and not Projects.receipt_matches_binding(npc,receipt) then
      npc.phase="growth-receipt-mismatch"
      return
    end
    npc.pending_job=nil
    Behavior.on_receipt(npc,receipt)
    if DesignedStart.owns(npc,receipt) then
      DesignedStart.on_receipt(npc,receipt,tick)
      if receipt.status~="complete" then npc.retry_at=tick+Behavior.recovery_delay(npc) end
      return
    end
    if npc.personal_ammo_job==receipt.job_id then
      npc.personal_ammo_job=nil
      Defense.personal_receipt(npc,receipt)
      if receipt.status~="complete" then
        npc.personal_ammo_retry_at=tick+Behavior.recovery_delay(npc)
        npc.phase="personal-ammo-retry:"..tostring(receipt.reason)
      end
      return
    end
    if RailTrade.on_receipt(npc,receipt) then
      local rail=npc.rail_project
      if rail and npc.rail_strategy_token and (rail.status=="failed" or rail.status=="complete") then
        Strategy.feedback(npc,npc.rail_strategy_token,rail.status=="complete" and 1 or -.7,
          rail.status=="complete" and "physical_rail_delivery_complete" or "physical_rail_failed:"..tostring(rail.reason))
        npc.rail_strategy_token=nil
      end
      if rail and rail.status=="failed" then
        npc.phase="rail-failed:"..tostring(rail.reason)
        npc.retry_at=tick+Behavior.recovery_delay(npc)
      end
      return
    end
    if npc.social_job and npc.social_job.job_id==receipt.job_id then
      SocialJobs.complete(npc,npc.social_job,receipt)
      npc.social_job=nil
      return
    end
    if Repairs.on_receipt(npc,receipt) then return end
    if PlayerTrade.on_receipt(npc,receipt) then return end
    if SharedProjects.on_receipt(npc,receipt) then return end
    if Projects.on_blocked_upkeep_receipt(npc,receipt) then
      if receipt.status~="complete" then
        npc.retry_at=tick+Behavior.recovery_delay(npc)
        npc.phase="blocked-upkeep-retry:"..tostring(receipt.reason)
      end
      return
    end
    if Projects.on_receipt(npc,receipt) then
      if npc.factory_project and npc.factory_project.status=="blocked" and npc.project_strategy_token then
        local blocked_reason=npc.factory_project.reason or receipt.reason
        Strategy.feedback(npc,npc.project_strategy_token,-.7,"physical_project_blocked:"..tostring(blocked_reason))
        npc.project_strategy_token=nil
      end
      if receipt.status~="complete" then
        npc.retry_at=tick+Behavior.recovery_delay(npc)
        npc.phase="growth-retry:"..tostring(receipt.reason)
      end
      return
    end
    if receipt.status=="canceled" and receipt.reason=="combat_interrupt" then
      npc.retry_at=tick+60
      if npc.moving_home then
        planned_job(npc,"move",{position=npc.home})
      elseif npc.step<=3 then
        npc.bootstrap_remainder=math.max(0,(npc.bootstrap_current_count or ({1,12,8})[npc.step])-(receipt.count or 0))
      end
      return
    end
    if receipt.status~="complete" then recover(npc,receipt,tick); return end
    if npc.clearing_obstacle then npc.clearing_obstacle=nil; npc.obstacle_cleared=true
    elseif npc.moving_home then npc.moving_home=nil
    elseif npc.step<=#STEPS then
      if npc.step==4 then npc.miner=receipt.entity end
      if npc.step==5 then npc.furnace=receipt.entity end
      npc.step=npc.step+1
      npc.bootstrap_current_count=nil
    end
  end
  local difficulty=settings.global["npc-factions-difficulty"].value
  local base_pace=({easy=1200,normal=300,hard=120})[difficulty]
  -- An engineer working through its own plans takes the next step right after the last, as a player
  -- does (placing one belt, then the next); the setting only sets how brisk it is. The long pace was
  -- for the scripted camp's decisions, and left designed engineers standing still most of the time.
  if npc.design then base_pace=({easy=90,normal=45,hard=20})[difficulty] end
  local pace=Behavior.cadence(npc,base_pace,tick)
  if tick<(npc.retry_at or 0) or tick<(npc.next_decision or 0) then return end
  npc.next_decision=tick+pace; npc.decisions=npc.decisions+1
  if suspended_obstruction then
    -- Only the authenticated clearance stream can run through this suspension.
    schedule_obstruction(npc,tick)
    return
  end
  if not storage.test and npc.step>#STEPS and tick>=(npc.personal_ammo_retry_at or 0) then
    local ammunition,ammo_reason=Defense.personal_maintenance(npc)
    if ammunition==false then
      npc.phase=ammo_reason or 'smelting-personal-ammo'
      return
    end
    if ammunition then
      ammunition.upkeep_source="personal_ammo"
      planned_job(npc,ammunition.kind,ammunition)
      if npc.pending_job then
        npc.personal_ammo_job=npc.pending_job
        npc.phase="rearming-from-owned-iron"
      end
      return
    end
  end
  -- Boxed in by its own buildings (paths keep failing): pick up a small piece at the edge of
  -- its pocket and walk out; the plan puts the piece back.
  local blocker=Rescue.blocker(npc,tick)
  if blocker then
    planned_job(npc,"mine",{target=blocker,count=1,mode="deconstruct"})
    if npc.pending_job then npc.phase="freeing-itself" end
    return
  end
  if not storage.test and npc.step>#STEPS then
    local repair=Repairs.next(npc,tick)
    if repair then
      if npc.suspended and npc.phase=="needs-help:factory-destroyed" then npc.suspended=nil end
      if repair.kind=="job" then
        planned_job(npc,repair.job.kind,repair.job)
        if npc.pending_job then Repairs.bind(npc,npc.pending_job,repair) end
        npc.phase="repairing:"..tostring(repair.stage)
      else npc.phase="repair-"..repair.kind..":"..tostring(repair.reason) end
      return
    elseif npc.suspended then return end
  end
  if not storage.test and npc.step>#STEPS then
    local rail_waiting=false
    local trade,deal_id=PlayerTrade.next(npc,tick)
    if trade then
      planned_job(npc,trade.kind,trade)
      if npc.pending_job then PlayerTrade.bind(npc,deal_id,npc.pending_job) end
      npc.phase="trading-with-player"
      return
    end
    if npc.rail_project and npc.rail_project.status=="active" then
      local rail=RailTrade.next(npc)
      if npc.rail_project.status=="failed" and npc.rail_strategy_token then
        Strategy.feedback(npc,npc.rail_strategy_token,-.7,"physical_rail_failed:"..tostring(npc.rail_project.reason))
        npc.rail_strategy_token=nil;npc.rail_retry_at=tick+72000
      end
      if rail then
        planned_job(npc,rail.kind,rail)
        if npc.pending_job then RailTrade.bind(npc,npc.pending_job) end
        npc.phase="rail:"..tostring(npc.rail_project.phase)
        return
      end
      if npc.rail_project.status=="active" then
        rail_waiting=true
        if npc.rail_project.phase~="fresh_contract" then
          npc.phase="rail-wait:"..tostring(npc.rail_project.reason or npc.rail_project.phase)
          return
        end
      end
    end
    local shared,task=SharedProjects.next(npc,tick)
    if shared then
      planned_job(npc,shared.kind,shared)
      if npc.pending_job then SharedProjects.bind(npc,npc.pending_job,task) end
      npc.phase="supporting-coalition:"..tostring(task.item)
      return
    end
    local social,pending
    if not (Agent.on_war_trip and Agent.on_war_trip(npc)) then social,pending=SocialJobs.next(npc) end
    if social then
      planned_job(npc,social.kind,social)
      if npc.pending_job then pending.job_id=npc.pending_job;npc.social_job=pending;npc.phase="social:"..pending.intent.kind end
      return
    end
    if rail_waiting then
      npc.phase="rail-wait:fresh-contract"
      return
    end
  end
  if DesignedStart.wanted(npc) then DesignedStart.begin(npc,tick) end
  -- The next goal is asked for as soon as one is due, ahead of upkeep: asking costs the engineer nothing (upkeep
  -- goes on while the plan is made), and asked only once upkeep was idle, the gaps between goals came to 11 of the
  -- first 40 game minutes (run 35, 2026-10-25). Building then gets its share (DesignedStart.build_starved).
  if DesignedStart.goal_due(npc,tick) then
    DesignedStart.begin(npc,tick,"Choose and build the most useful next production for this engineer "
      .."near its existing factory, paid for with its own materials.","choose")
  end
  -- Its base hit while it builds something else: what now (DesignedStart.reconsider).
  if DesignedStart.reconsider and DesignedStart.reconsider(npc,tick) then return end
  -- Turrets biters are at or coming to, loaded before its plan and its agenda: standing off from a wave (agent.lua) it
  -- still feeds the turrets it can get to unseen (DesignedStart.defense_supply).
  do
    local guard,guard_meta=DesignedStart.defense_supply(npc,tick,true)
    if guard then
      planned_job(npc,guard.kind,guard)
      if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,guard_meta);npc.phase="designed:upkeep:turret_ammo" end
      return
    end
  end
  -- What biters destroyed goes back once it is calm there, before its plans and its agenda (DesignedStart.rebuild_now),
  -- unless a plan is short of its building share (then upkeep rebuilds within it), till a loss is overdue (W-34:
  -- DesignedStart.rebuild_overdue). Urgent work at home (a defense ordered
  -- now, guarding, a recall home) has its turn first, and the losses go back while it waits (below). Urgent work away (a
  -- help, an attack, a march) lets go back only what it reaches from where it stands, with what it holds (rebuild_opts).
  -- Every "urgent" held them all, and a defense ordered now on its home held 50 rebuilds there (rehearsal 7f).
  local starved=DesignedStart.active(npc) and DesignedStart.build_starved and DesignedStart.build_starved(npc)
  local opts=DesignedStart.rebuild_opts and DesignedStart.rebuild_opts(npc)
  -- `free`: its urgent order waits (agenda_first's turn for its losses): held only by a starved stage then.
  local function rebuild(free)
    if not DesignedStart.rebuild_now then return false end
    -- (a stage short of its share, or its urgent order on its own ground, goes first, but not past a loss overdue: then
    -- the two take turns, a job each, the loss's turn a short walk (REBUILD_RUN_STARVED pieces) at most every
    -- OVERDUE_EVERY, so the stage or the order keeps its time; with nothing it can put back now it looks again in a
    -- while; W-34, W-36: under steady attack its build(now) orders held every loss, power and belts 630 s)
    local o=opts
    local held=starved or (opts and opts.agenda_first and not free)
    if held then
      if npc.overdue_turn or tick<(npc.overdue_idle_until or 0) or tick<(npc.overdue_next or 0)
          or not DesignedStart.rebuild_overdue(npc,tick) then
        npc.overdue_turn=nil
        return false
      end
      o={run=tonumber(DesignedStart.REBUILD_RUN_STARVED) or 8}
      for k,v in pairs(opts or {}) do o[k]=v end
    end
    -- (called on its own: inside an `and`, Lua keeps only a call's first result, and the meta was lost; 7e crashed)
    local again,again_meta=DesignedStart.rebuild_now(npc,tick,o)
    if not again then
      if held then npc.overdue_idle_until=tick+300 end
      return false
    end
    if held then npc.overdue_turn,npc.overdue_next=true,tick+(tonumber(DesignedStart.OVERDUE_EVERY) or 1800) end
    planned_job(npc,again.kind,again)
    if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,again_meta);npc.phase="designed:upkeep:"..again_meta.upkeep end
    return true
  end
  if rebuild() then return end
  -- A plan being built gets its share of the engineer's time (DesignedStart.build_starved) whatever else is
  -- asked of it; only what is urgent comes before it then.
  if DesignedStart.active(npc) and DesignedStart.build_starved and DesignedStart.build_starved(npc)
      and not (Agent.urgent and Agent.urgent(npc)) then
    local job,meta=DesignedStart.next(npc,tick)
    if job then
      planned_job(npc,job.kind,job)
      if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,meta) end
      return
    end
  end
  -- What the engineer decided to do (its strategist's agenda) comes before upkeep and building.
  local act,act_meta=Agent.next(npc,tick)
  -- (its urgent order at home waits, a defense planned or a guard stood: its losses go back meanwhile)
  if act==false and opts and opts.agenda_first and rebuild(true) then return end
  if act==false then npc.phase="agent:"..tostring(act_meta); return end   -- e.g. visiting someone: stays
  if act then
    planned_job(npc,act.kind,act)
    if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,act_meta);npc.phase="agent:"..act_meta.intention.tool end
    return
  end
  -- Its own defense kit where biters are about (defense_kit.lua): what it fights with, and turrets to put down at its
  -- feet; the turrets it put down in a fight picked up again after.
  local kit,kit_meta=Kit.next(npc,tick,Agent.designed and Agent.designed.acquire)
  if kit then
    planned_job(npc,kit.kind,kit)
    if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,kit_meta);npc.phase="designed:upkeep:"..kit_meta.upkeep end
    return
  end
  local keep,keep_meta=DesignedStart.upkeep(npc,tick)
  if keep then
    planned_job(npc,keep.kind,keep)
    if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,keep_meta);npc.phase="designed:upkeep:"..keep_meta.upkeep end
    return
  end
  if DesignedStart.goal_due(npc,tick) then
    DesignedStart.begin(npc,tick,"Choose and build the most useful next production for this engineer "
      .."near its existing factory, paid for with its own materials.","choose")
  end
  if DesignedStart.active(npc) then
    local job,meta=DesignedStart.next(npc,tick)
    if job then
      planned_job(npc,job.kind,job)
      if npc.pending_job then DesignedStart.bind(npc,npc.pending_job,meta) end
    elseif npc.design and npc.design.status=="waiting" then mend(npc,tick)   -- (its next plan not come yet)
    end
    return
  end
  -- An engineer of plans: its factory is its plans, and between them it waits for its next goal (asked for above),
  -- never falling back to the scripted start's chain, whose first drill and furnace a plan may take down on
  -- purpose (run 24 stopped dead as "factory destroyed" once its bus retired the burner iron, 2026-10-25).
  if Factory.designed_engineer(npc) then
    if not mend(npc,tick) then npc.phase="designed:between-goals" end
    return
  end
  if not npc.module then find_module(npc); return end
  if npc.step<=3 and npc.bootstrap_remainder==0 then npc.step=npc.step+1;npc.bootstrap_remainder=nil end
  if npc.step==1 then planned_job(npc,"mine",{item="wood",count=npc.bootstrap_remainder or 1});npc.bootstrap_remainder=nil
  elseif npc.step==2 then planned_job(npc,"mine",{item="coal",count=npc.bootstrap_remainder or 12});npc.bootstrap_remainder=nil
  elseif npc.step==3 then planned_job(npc,"mine",{item="iron-ore",count=npc.bootstrap_remainder or 8});npc.bootstrap_remainder=nil
  elseif npc.step==4 then planned_job(npc,"build",{entity_name="burner-mining-drill",item="burner-mining-drill",position=npc.module,direction=npc.module_direction or defines.direction.north})
  elseif npc.step==5 then planned_job(npc,"build",{entity_name="stone-furnace",item="stone-furnace",position=npc.furnace_position})
  elseif npc.step==6 then planned_job(npc,"transfer",{target=npc.miner,item="coal",count=2,mode="deposit"})
  elseif npc.step==7 then planned_job(npc,"transfer",{target=npc.furnace,item="coal",count=4,mode="deposit"})
  elseif npc.step==8 then planned_job(npc,"transfer",{target=npc.furnace,item="iron-ore",count=8,mode="deposit",inventory=defines.inventory.crafter_input})
  else
    npc.phase="smelting"
    if storage.test and not storage.test.ai_active then return end -- ordinary fixtures stop after initial macro
    if not (npc.miner and npc.miner.valid and npc.furnace and npc.furnace.valid) then
      npc.phase="needs-help:factory-destroyed"; npc.suspended=true; return
    end
    -- A designed engineer grows through the plans it asks for, never through the scripted chain.
    if settings.global["npc-factions-factory-growth"].value and not storage.test and not npc.design then
      if npc.requested_project and (not npc.factory_project or npc.factory_project.status~="active") then
        local requested=npc.requested_project;npc.requested_project=nil
        npc.completed_projects=npc.completed_projects or {}
        if npc.factory_project and npc.factory_project.status=="complete" then
          npc.completed_projects[npc.factory_project.id]={completed_tick=npc.factory_project.completed_tick}
        end
        local started,reason=Projects.start(npc,requested)
        if started then npc.growth_wait_reason=nil else npc.growth_wait_reason=reason end
      end
      if not npc.factory_project and tick>=(npc.starter_plan_retry_at or 0) then
        local started,reason=Projects.start(npc,"starter_power_science")
        if started then
          npc.growth_wait_reason=nil
          npc.starter_plan_failures=nil;npc.starter_plan_retry_at=nil
        else
          npc.growth_wait_reason=reason
          npc.starter_plan_failures=(npc.starter_plan_failures or 0)+1
          local delay=math.max(2400,Behavior.recovery_delay(npc)*20)
            *2^math.min(4,npc.starter_plan_failures-1)
          npc.starter_plan_retry_at=tick+math.min(72000,delay)
        end
      end
      if npc.factory_project and npc.factory_project.status=="blocked" then
        Projects.resume_reachable_build_approach(npc,tick)
        Projects.recover_raw_supply(npc,tick)
        Projects.recover_saved_copper(npc,tick)
        Projects.recover_missing_build_supply(npc,tick)
        Projects.recover_missing_craft_supply(npc,tick)
        if npc.factory_project.status=="blocked" then
          if schedule_obstruction(npc,tick) then return end
          local upkeep=Projects.blocked_maintenance(npc,tick)
          if upkeep then
            planned_job(npc,upkeep.kind,upkeep)
            if npc.pending_job then
              if not Projects.bind_blocked_upkeep(npc,npc.pending_job) then
                local rejected_id=npc.pending_job
                Worker.cancel_job(npc,rejected_id,"blocked_upkeep_bind_rejected")
                Projects.reject_blocked_upkeep(npc,"blocked_upkeep_bind_rejected")
                if npc.receipts[rejected_id] then npc.pending_job=nil end
              else npc.phase="maintaining-blocked-factory" end
            else Projects.reject_blocked_upkeep(npc,"blocked_upkeep_enqueue_rejected") end
            return
          end
        end
      end
      if npc.factory_project and npc.factory_project.status=="active" then
        SharedProjects.leader_supplied_step(npc)
        if SharedProjects.leader_waiting(npc,tick) then
          npc.phase="waiting-coalition-project";return
        end
        local action=Projects.next(npc,tick)
        if action.kind=="job" then
          planned_job(npc,action.job.kind,action.job)
          if npc.pending_job then
            Projects.bind(npc,npc.pending_job,action.maintenance)
            npc.phase="building:"..tostring(npc.factory_project.id)..":"..tostring(action.index)
          end
        elseif action.kind=="wait" then
          npc.phase="building-wait:"..tostring(action.reason)
        else
          if action.kind=="blocked" then
            npc.factory_project.status="blocked";npc.factory_project.reason=action.reason
            if npc.project_strategy_token then
              Strategy.feedback(npc,npc.project_strategy_token,-.7,"physical_project_blocked:"..tostring(action.reason))
              npc.project_strategy_token=nil
            end
          end
          npc.phase="building-"..tostring(action.kind)..":"..tostring(action.reason or "")
        end
        return
      end
      if npc.factory_project and npc.factory_project.status=="complete" then
        npc.completed_projects=npc.completed_projects or {}
        npc.completed_projects[npc.factory_project.id]={completed_tick=npc.factory_project.completed_tick}
        if npc.project_strategy_token then
          Strategy.feedback(npc,npc.project_strategy_token,1,"physical_project_complete:"..npc.factory_project.id)
          npc.project_strategy_token=nil
        end
        if settings.global["npc-factions-orbital-progression"].value
            and not (storage.dev and storage.dev.progression_external) then
          local next_id,token,rail_options=ProjectChoices.choose(npc,tick)
          if next_id then
            if next_id=="ground_rail_trade" then
              local started,reason=RailTrade.start(npc,rail_options)
              if started then npc.rail_strategy_token=token;npc.phase="rail:research";return end
              Strategy.feedback(npc,token,-.5,"rail_plan_unavailable:"..tostring(reason))
              npc.rail_retry_at=tick+72000;npc.growth_wait_reason=reason
              return
            end
            local started,reason=Projects.start(npc,next_id)
            if started then npc.growth_wait_reason=nil;npc.project_strategy_token=token;npc.phase="building:"..next_id;return end
            Strategy.feedback(npc,token,-.5,"project_plan_unavailable:"..tostring(reason))
            npc.project_retry=npc.project_retry or {};npc.project_retry[next_id]=tick+6000
            npc.growth_wait_reason=reason
          elseif #ProjectChoices.candidates(npc,tick)==1 and not npc.growth_wait_reason then
            npc.growth_wait_reason="next_template_required:rocket_materials"
          end
        end
        local upkeep=Projects.maintenance(npc)
        if upkeep then
          planned_job(npc,upkeep.kind,upkeep)
          if npc.pending_job then
            Projects.bind(npc,npc.pending_job,true)
            npc.phase="maintaining-expanded-factory"
          end
          return
        end
      end
    end
    local inventory=npc.character.get_main_inventory()
    if item_count(npc,"coal")<8 then planned_job(npc,"mine",{item="coal",count=12})
    elseif npc.miner.get_fuel_inventory().get_item_count("coal")<2 then
      planned_job(npc,"transfer",{target=npc.miner,item="coal",count=4,mode="deposit"})
    elseif npc.furnace.get_fuel_inventory().get_item_count("coal")<2 then
      planned_job(npc,"transfer",{target=npc.furnace,item="coal",count=4,mode="deposit"})
    elseif npc.furnace.get_output_inventory().get_item_count("iron-plate")>=8 then
      planned_job(npc,"transfer",{target=npc.furnace,item="iron-plate",count=8,mode="withdraw"})
    elseif npc.relocated and npc.furnace.get_inventory(defines.inventory.crafter_input).get_item_count("iron-ore")<2 then
      if inventory.get_item_count("iron-ore")<8 then planned_job(npc,"mine",{item="iron-ore",count=8})
      else planned_job(npc,"transfer",{target=npc.furnace,item="iron-ore",count=8,mode="deposit",inventory=defines.inventory.crafter_input}) end
    end
  end
end

return Factory
