local Navigation = require("scripts.navigation")
local Recipes=require("scripts.recipes")
local Behavior = require("scripts.behavior")
local Credit = require("scripts.production_credit")
local PlatformLaunch=require("scripts.platform_launch")
local Rail=require("scripts.rail_support")
local PlayerTrade=require("scripts.player_trade")
local Cooperation=require("scripts.cooperation")
local TrainJobs=require("scripts.train_jobs")
local TeamWorld=require("scripts.team_world")
local BaseMap=require("scripts.base_map")
local TeamResearch=require("scripts.team_research")
local TeamSupply=require("scripts.team_supply")
local Worker = {}
local Rescue = require("scripts.rescue")
local Reach = require("scripts.reach")
local Hands = require("scripts.hands")
local Ghosts = require("scripts.ghosts")
local Mend = require("scripts.mend")
local Boosts = require("scripts.boosts")
-- A building planned mirrored, mirrored if create_entity left it not (2.1's LuaEntity.mirroring).
function Worker.mirrored(entity, mirror)
  if mirror ~= true then return end
  pcall(function() if not entity.mirroring then entity.mirroring = true end end)
end
local KINDS = {move = true, mine = true, craft = true, build = true, build_run = true, transfer = true,
  theft = true, sabotage = true,
  request_platform = true, launch_platform = true,
  configure_recipe = true,configure_direction=true,belt_pickup=true,player_trade=true,
  configure_train_stop=true,rail_move=true, select_research = true, landfill = true, sweep = true, mend = true, pickup = true}
local BUILD_TYPES = {furnace = true, ["mining-drill"] = true, container = true,
  ["assembling-machine"] = true, ["transport-belt"] = true, inserter = true,
  ["electric-pole"] = true, boiler = true, generator = true, lab = true, pipe = true,
  ["offshore-pump"] = true, ["ammo-turret"] = true,
  ["pipe-to-ground"] = true, ["storage-tank"] = true, ["pump"] = true,
  ["rocket-silo"] = true, ["cargo-landing-pad"] = true, ["roboport"] = true,
  ["logistic-container"] = true, ["rail-signal"] = true, ["rail-chain-signal"] = true,
  ["train-stop"] = true, ["straight-rail"] = true, splitter = true, ["underground-belt"] = true, loader = true,
  wall = true, gate = true, ["solar-panel"] = true, accumulator = true, radar = true,
  ["electric-turret"] = true, ["fluid-turret"] = true, ["artillery-turret"] = true, ["land-mine"] = true,
  reactor = true, ["heat-pipe"] = true}   -- (a nuclear plant: NPC Designer's book, DS-4)

local function normal(item) return {name = item, quality = "normal"} end
-- Another mod may turn a loader round once it is built (Vanilla Loaders snaps one to its neighbours): the
-- plan's end and facing stand. (Setting the end turns a loader round too, so the facing goes last.)
local function keep_loader_end(entity, wanted, direction)
  if not (entity.valid and entity.type == "loader" and wanted) then return end
  if entity.loader_type ~= wanted then entity.loader_type = wanted end
  if entity.direction ~= direction then entity.direction = direction end
end
local function main(npc) return npc.character.get_main_inventory() end
local function count(npc, item) return main(npc).get_item_count(normal(item)) end
local function craft_output_count(npc,item)
  local total=count(npc,item)
  local prototype=prototypes.item[item]
  local slot=prototype and ({ammo=defines.inventory.character_ammo,gun=defines.inventory.character_guns,
    armor=defines.inventory.character_armor})[prototype.type]
  local inventory=slot and npc.character.get_inventory(slot)
  if inventory and inventory.valid then total=total+inventory.get_item_count(normal(item)) end
  return total
end
local function inventory_counts(inventory)
  local result = {}
  for i = 1, #inventory do
    local stack = inventory[i]
    if stack.valid_for_read and stack.quality.name == "normal" then
      result[stack.name] = (result[stack.name] or 0) + stack.count
    end
  end
  return result
end
local function stop(npc, job)
  Navigation.clear(npc, job)
  if npc.character and npc.character.valid then
    npc.character.mining_state = {mining = false}
    npc.character.selected = nil
  end
end

local function queue_count(character, recipe)
  local n = 0
  for _, entry in ipairs(character.crafting_queue or {}) do
    if entry.recipe == recipe then n = n + entry.count end
  end
  return n
end

local function native_craft_remaining_ticks(character)
  local queue=character.crafting_queue or {}
  if #queue==0 then return nil end
  local speed=character.crafting_speed
  if type(speed)~="number" or speed<=0 then return nil end
  local seconds=0
  for index,entry in ipairs(queue) do
    local recipe=character.force.recipes[entry.recipe]
    if not recipe or type(recipe.energy)~="number" or recipe.energy<=0 then return nil end
    local count=entry.count or 0
    if count<1 then return nil end
    seconds=seconds+recipe.energy*count
    if index==1 then
      local progress=math.max(0,math.min(1,character.crafting_queue_progress or 0))
      seconds=seconds-recipe.energy*progress
    end
  end
  return math.ceil(seconds*60/speed)
end

local function extend_native_craft_deadline(character,job,tick)
  if job.craft_timeout_set or not job.craft_started then return end
  local queue=character.crafting_queue or {}
  if #queue==0 then return end
  local remaining=native_craft_remaining_ticks(character)
  -- An unreadable queue still gets a finite allowance rather than an early
  -- cancellation of legitimate native prerequisites.
  local allowance=remaining and math.max(3600,math.ceil(remaining*1.5)+3600) or 360000
  allowance=math.min(360000,allowance)
  job.craft_stall_deadline=tick+allowance
  if not job.requested_deadline then
    job.deadline=math.max(job.deadline or 0,job.craft_stall_deadline)
  end
  job.craft_timeout_set=true
  job.craft_estimated_remaining_ticks=remaining
end

local function cancel_native_craft(npc, job)
  -- The worker starts only with an empty native queue, and owns it exclusively.
  if job and job.kind == "craft" and job.craft_started and npc.character and npc.character.valid then
    local before = inventory_counts(main(npc))
    local initial = npc.character.crafting_queue or {}
    for _ = 1, #initial do
      -- Canceling a dependent recipe can remove its prerequisites and renumber
      -- the queue. Every index must come from a fresh native queue read.
      local q = npc.character.crafting_queue or {}
      if #q == 0 then break end
      local tail=q[#q]
      npc.character.cancel_crafting{index = tail.index, count = tail.count}
    end
    job.craft_cancel_incomplete=(npc.character.crafting_queue_size or 0)>0
    local after = inventory_counts(main(npc))
    job.refunded = job.refunded or {}
    for item, n in pairs(after) do
      if n > (before[item] or 0) then job.refunded[item] = (job.refunded[item] or 0) + n - (before[item] or 0) end
    end
  end
end

local function finish(npc, job, status, reason, tick, extra)
  if job.terminal then return end
  -- Cancellation/death/deadline can happen after engine work but before our next poll.
  if npc.character and npc.character.valid then
    if job.kind == "mine" and job.initial_count and job.item then
      job.actual_count = math.max(0, count(npc, job.item) - job.initial_count)
    elseif job.kind == "craft" and job.craft_started and queue_count(npc.character, job.recipe) == 0 then
      local recipe, verified = npc.character.force.recipes[job.recipe], true
      if not recipe then verified = false end
      for _, product in ipairs(recipe and recipe.products or {}) do
        if not product.amount or craft_output_count(npc, product.name) - (job.craft_baseline[product.name] or 0) < product.amount then verified = false end
      end
      if verified then
        job.completed, job.craft_started = (job.completed or 0) + 1, false
        job.produced = job.produced or {}
        for _, product in ipairs(recipe.products) do
          job.produced[product.name] = (job.produced[product.name] or 0) + product.amount
        end
      end
    end
  end
  if job.kind == "craft" and job.production_credit then
    Credit.complete(npc, job, npc.character and npc.character.valid and npc.character.force.recipes[job.recipe] or nil)
  end
  local retries = job.navigation and job.navigation.requests or 0
  stop(npc, job)
  if status ~= "complete" then cancel_native_craft(npc, job) end
  job.terminal, job.state, job.finished_tick = true, status, tick
  local receipt = {id = tostring(npc.id) .. ":" .. tostring(job.id), npc_id = npc.id,
    job_id = job.id, kind = job.kind, status = status, reason = reason,
    started_tick = job.started_tick, tick = tick, completed_tick = tick, item = job.item,
    count = job.kind == "craft" and (job.completed or 0) or (job.actual_count or 0), completed = job.completed or 0,
    recipe = job.recipe, social_intent_id = job.social_intent_id,deal_id=job.deal_id,mode=job.mode,
    resource_source=job.resource_source,resource_unavailable=job.resource_unavailable,
    target_unit_number=job.target and job.target.valid and job.target.unit_number or nil,
    target_force=job.target and job.target.valid and job.target.force.name or nil,
    target_type=job.target and job.target.valid and job.target.type or nil,
    target_surface_index=job.target and job.target.valid and job.target.surface.index or nil,
    products = job.produced or {}, produced = job.produced or {}, craft_wait = job.craft_wait,
    consumed = job.consumed or {}, refunded = job.refunded or {}}
  if job.kind == "craft" and job.craft_cancel_incomplete then receipt.craft_cancel_incomplete=true end
  if job.kind == "craft" then receipt.production_credit = Credit.summary(job) end
  if job.kind == "sweep" then receipt.moved = job.moved or {} end   -- (each stop's share, however it ended)
  receipt.products = receipt.produced
  if job.kind == "mine" and job.item then
    receipt.produced[job.item] = job.actual_count or 0
  elseif job.kind == "build" and status == "complete" then
    receipt.consumed[job.item] = job.placement_count or 1
  elseif (job.kind == "transfer" or job.kind=="theft" or job.kind=="sabotage" or job.kind=="belt_pickup"
      or job.kind == "sweep") and job.item then
    -- Both sides of a transfer are explicit; this is movement, not production credit.
    receipt.consumed[job.item], receipt.produced[job.item] = job.actual_count or 0, job.actual_count or 0
  end
  local journey=TrainJobs.finish(npc,job,status,tick)
  for k,v in pairs(journey or {}) do receipt[k]=v end
  for k, v in pairs(extra or {}) do receipt[k] = v end
  npc.receipts = npc.receipts or {}
  npc.receipts[job.id] = receipt
  npc.receipt_order = npc.receipt_order or {}
  npc.receipt_order[#npc.receipt_order + 1] = job.id
  npc.completed_jobs = npc.completed_jobs or {}
  npc.completed_jobs[job.id] = receipt.id
  if status == "canceled" and reason == "project_replan" then
    npc.migration_cancellations = npc.migration_cancellations or {}
    npc.migration_cancellations[#npc.migration_cancellations+1] = {job_id=job.id,kind=job.kind,tick=tick,reason=reason,completed=receipt.completed,count=receipt.count}
    while #npc.migration_cancellations>128 do table.remove(npc.migration_cancellations,1) end
  elseif status ~= "complete" then
    npc.failures = npc.failures or {}
    local target=job.target and job.target.valid and job.target
    npc.failures[#npc.failures + 1] = {job_id = job.id, kind = job.kind,
      reason = reason, tick = tick, retries = retries,item=job.item,recipe=job.recipe,
      target_name=target and target.name,target_position=target and {x=target.position.x,y=target.position.y},
      actor_position=npc.character and npc.character.valid and {x=npc.character.position.x,y=npc.character.position.y}}
    log("[npc-factions] npc=" .. tostring(npc.id) .. " job=" .. tostring(job.id)
      .. " " .. status .. ": " .. tostring(reason))
    Rescue.note(npc, reason, tick)
  end
  if npc.current_job == job then npc.current_job = nil end
  return receipt
end

function Worker.enqueue(npc, request)
  if type(request) ~= "table" or not KINDS[request.kind] then return false, "unknown_job_kind" end
  if request.id ~= nil and type(request.id) ~= "string" and type(request.id) ~= "number" then return false, "invalid_job_id" end
  if type(request.id) == "number" and (request.id ~= request.id or math.abs(request.id) > 9007199254740991) then return false, "invalid_job_id" end
  if request.deadline and (type(request.deadline) ~= "number" or request.deadline ~= math.floor(request.deadline)
      or math.abs(request.deadline) > 9007199254740991) then return false, "invalid_deadline" end
  if request.position then
    if type(request.position) ~= "table" then return false, "invalid_position" end
    local x, y = request.position.x or request.position[1], request.position.y or request.position[2]
    if type(x) ~= "number" or type(y) ~= "number" or x ~= x or y ~= y
        or math.abs(x) > 1000000 or math.abs(y) > 1000000 then return false, "invalid_position" end
  end
  if request.count and (type(request.count) ~= "number" or request.count ~= math.floor(request.count)
      or request.count < 1 or request.count > 10000) then return false, "invalid_count" end
  if request.line ~= nil and request.line ~= 1 and request.line ~= 2 then return false, "invalid_line" end
  if request.up_to ~= nil and type(request.up_to) ~= "boolean" then return false, "invalid_up_to" end
  local entries   -- (a sweep's stops: what goes into or comes out of each)
  if request.kind == "sweep" then
    if (request.mode ~= "deposit" and request.mode ~= "withdraw") or type(request.item) ~= "string"
        or type(request.entries) ~= "table" or #request.entries == 0 or #request.entries > 64 then
      return false, "invalid_sweep"
    end
    entries = {}
    for _, e in ipairs(request.entries) do
      if type(e) ~= "table" or type(e.count) ~= "number" or e.count < 1 or e.count ~= math.floor(e.count)
          or not (e.target and e.target.valid) then return false, "invalid_sweep" end
      entries[#entries + 1] = {target = e.target, count = e.count, inventory = e.inventory}
    end
  end
  local tiles   -- (a landfill job's water tiles)
  if request.kind == "landfill" then
    if type(request.item) ~= "string" or type(request.positions) ~= "table" or #request.positions == 0
        or #request.positions > 64 then return false, "invalid_positions" end
    tiles = {}
    for _, p in ipairs(request.positions) do
      local x, y = type(p) == "table" and p.x, type(p) == "table" and p.y
      if type(x) ~= "number" or type(y) ~= "number" or x ~= x or y ~= y or math.abs(x) > 1000000
          or math.abs(y) > 1000000 then return false, "invalid_positions" end
      tiles[#tiles + 1] = {x = x, y = y}
    end
  end
  local targets   -- (a mend job's hurt buildings, or a pickup job's buildings: its own)
  if request.kind == "mend" or request.kind == "pickup" then
    local most = request.kind == "mend" and Mend.MAX_TARGETS or Hands.MAX_PICKUP
    if type(request.targets) ~= "table" or #request.targets == 0 or #request.targets > most then
      return false, "invalid_targets"
    end
    targets = {}
    for _, e in ipairs(request.targets) do
      if not (type(e) == "table" or type(e) == "userdata") or not e.valid or e.force ~= npc.character.force then
        return false, "invalid_targets"
      end
      targets[#targets + 1] = e
    end
  end
  local placements
  if request.kind == "build_run" then
    if type(request.placements) ~= "table" or #request.placements == 0 or #request.placements > 128 then
      return false, "invalid_placements"
    end
    placements = {}
    for _, p in ipairs(request.placements) do
      local x, y = type(p) == "table" and type(p.position) == "table" and p.position.x, p.position and p.position.y
      if type(p.entity_name) ~= "string" or type(p.item) ~= "string" or type(x) ~= "number" or type(y) ~= "number"
          or x ~= x or y ~= y or math.abs(x) > 1000000 or math.abs(y) > 1000000 then
        return false, "invalid_placements"
      end
      placements[#placements + 1] = {id=p.id, entity_name=p.entity_name, item=p.item, position={x=x, y=y},
        direction=p.direction, belt_to_ground_type=p.belt_to_ground_type, mirror=p.mirror==true or nil}
    end
  end
  npc.jobs, npc.receipts, npc.failures = npc.jobs or {}, npc.receipts or {}, npc.failures or {}
  npc.completed_jobs = npc.completed_jobs or {}
  if #npc.jobs >= 64 then return false, "queue_full" end
  npc.job_sequence = (npc.job_sequence or 0) + 1
  local id = request.id or (tostring(npc.id) .. "-" .. npc.job_sequence)
  if npc.completed_jobs[id] or (npc.current_job and npc.current_job.id == id) then return false, "duplicate_job" end
  for _, queued in ipairs(npc.jobs) do if queued.id == id then return false, "duplicate_job" end end
  local job = {}
  -- Do not accept injected executor bookkeeping from a submitted plan.
  for _, key in ipairs({"kind", "target", "position", "item", "count", "recipe", "entity_name",
      "direction", "belt_to_ground_type", "mirror", "avoid_danger", "owner_force", "inventory", "mode", "deadline", "search_radius", "technology",
      "social_intent_id", "platform_name","deal_id","upkeep_source",
      "route_id","station_role","destination_role","journey_id","line","up_to","background","sweep","natural_only","avoid",
      "clears"}) do
    job[key] = request[key]
  end
  job.id, job.state, job.count = id, "queued", request.count or 1
  job.placements, job.positions, job.entries, job.targets = placements, tiles, entries, targets
  if job.position then job.position = {x = job.position.x or job.position[1], y = job.position.y or job.position[2]} end
  npc.jobs[#npc.jobs + 1] = job
  return true, id
end

function Worker.cancel(npc, reason)
  local job = npc.current_job
  if job then return finish(npc, job, "canceled", reason or "canceled", game.tick) end
end

-- Scoped cancellation never interrupts an unrelated active native task.
function Worker.cancel_job(npc,id,reason)
  if npc.current_job then
    if npc.current_job.id~=id then return nil,"different_job_active" end
    return finish(npc,npc.current_job,"canceled",reason or "canceled",game.tick)
  end
  for index,job in ipairs(npc.jobs or {}) do
    if job.id==id then
      table.remove(npc.jobs,index)
      return finish(npc,job,"canceled",reason or "canceled",game.tick)
    end
  end
  return nil,"job_not_found"
end

function Worker.on_path_finished(event) Navigation.on_path_finished(event) end

-- What a job was for is so already, whoever did it (LIVE-OP-a: the engineer's own robots took the trees and rocks in a
-- wall's way, each clearing failed "target_exhausted", and six such failures gave up a 170-piece wall plan; since W-26
-- its robots and its network's race its hands all the time): its building standing where it goes, its own; what it
-- was to clear or take down gone; a recipe or a direction already set. Then the job is done, never a failure counted
-- toward giving up a plan. Returns the receipt's extra fields, or nil.
function Worker.goal_met(npc, job)
  local c = npc.character
  if not (c and c.valid) then return nil end
  local ok, met = pcall(function()
    if job.kind == "build" and job.entity_name and job.position then
      for _, e in ipairs(c.surface.find_entities_filtered{name=job.entity_name, position=job.position, radius=0.5,
          force=c.force}) do
        -- (as the piece is planned, as adopt asks: its direction, its belt end, its mirroring; Codex)
        local same_end = not job.belt_to_ground_type or e.belt_to_ground_type == job.belt_to_ground_type
        local okm, mv = pcall(function() return e.mirroring end)
        local mirrored = (okm and mv == true) == (job.mirror == true)
        if e.valid and math.abs(e.position.x - job.position.x) < 0.01 and math.abs(e.position.y - job.position.y) < 0.01
            and (job.direction == nil or e.direction == job.direction or not e.supports_direction)
            and same_end and mirrored then
          return {entity=e, entity_name=job.entity_name, position=e.position, already=true}
        end
      end
    elseif job.kind == "mine" and (job.clears or job.mode == "deconstruct" or job.mode == "clear")
        and not (job.target and job.target.valid) then
      return {already=true}
    elseif job.kind == "configure_recipe" and job.target and job.target.valid and job.recipe then
      local r = job.target.get_recipe()
      if r and r.name == job.recipe and (job.direction == nil or job.target.direction == job.direction) then
        return {configured_recipe=r.name, target=job.target, already=true}
      end
    elseif job.kind == "configure_direction" and job.target and job.target.valid and job.direction ~= nil
        and job.target.direction == job.direction then
      return {target=job.target, configured_direction=job.direction, already=true}
    end
    return nil
  end)
  return ok and met or nil
end

local function fail(npc, job, reason, tick, extra)
  if reason ~= "in_danger" and reason ~= "combat_interrupt" then
    local met = Worker.goal_met(npc, job)
    if met then   -- (another hand or robot did it: done, nothing of its own spent)
      if job.kind == "build" then job.placement_count = 0 end
      return finish(npc, job, "complete", nil, tick, met)
    end
  end
  return finish(npc, job, "failed", reason, tick, extra)
end

-- Ticks a job spent waiting on a craft still queued (its receipt's craft_wait: designed_start's tally counts them as
-- "wait:craft", out of the job's own time). The player crafts 37% of the time, 42% of that walking or mining (the
-- user's recorded play, 2026-10-28).
local function craft_wait(job)
  job.state = "waiting_craft"
  job.craft_wait = (job.craft_wait or 0) + 1
end

-- How many of `item` its crafting queue will still make.
local function craft_coming(npc, item)
  local n = 0
  for _, q in ipairs(npc.character.crafting_queue or {}) do
    local r = not q.prerequisite and prototypes.recipe[q.recipe]   -- (an intermediate goes into a later craft)
    for _, p in ipairs(r and r.products or {}) do
      if p.name == item then n = n + (p.amount or 1) * (q.count or 1) end
    end
  end
  return n
end
Worker.craft_coming = craft_coming

-- Ground the game refused to build on for good (a cliff, another force's building), remembered as world
-- boxes so every plan after keeps off it (the map dump carries them: BaseMap). A coal belt for the boilers
-- was planned across a cliff and tried six times (run 20, 2026-10-25); a plan made after would have crossed
-- it again.
Worker.UNBUILDABLE_KEPT = 64
function Worker.unbuildable(npc, e)
  local ok, b = pcall(function() return e.bounding_box end)
  if not (ok and b) then return end
  local x0, y0, x1, y1 = BaseMap.tile_box(b)   -- (a diagonal cliff's box is turned)
  local box = {x0=x0, y0=y0, x1=x1 - 1, y1=y1 - 1, kind=e.type, tick=game.tick}
  npc.unbuildable = npc.unbuildable or {}
  for _, u in ipairs(npc.unbuildable) do
    if u.x0 == box.x0 and u.y0 == box.y0 and u.x1 == box.x1 and u.y1 == box.y1 then return end
  end
  table.insert(npc.unbuildable, box)
  while #npc.unbuildable > Worker.UNBUILDABLE_KEPT do table.remove(npc.unbuildable, 1) end
end
-- For good: a cliff (until blasted), or a building of another force that is not at war with it.
function Worker.for_good(character, e)
  if e.type == "cliff" then return true end
  local force = e.force
  if not force or force == character.force or force.name == "neutral" or force.name == "enemy" then return false end
  return e.type ~= "character" and e.type ~= "unit" and e.type ~= "car" and e.type ~= "spider-vehicle"
end
local function valid_target(npc, target)
  return target and target.valid and target.surface == npc.character.surface
end

local function navigate(npc, job, position, radius, tick)
  job.state = "navigating"
  local state, reason = Navigation.step(npc, job, position, radius, tick)
  if state == "failed" then fail(npc, job, reason, tick) end
  return state == "arrived"
end

local function same_force(a, b)
  local name_a = type(a) == "string" and a or (a and a.name)
  local name_b = type(b) == "string" and b or (b and b.name)
  return name_a == name_b
end

local function accepted_contract(npc,job,target,intent)
  if not intent.request_id then return true end
  local requests=storage.society and storage.society.requests
  for _,request in pairs(requests or {}) do
    if request.id==intent.request_id then
      local recipient=storage.npcs and storage.npcs[request.requester_id]
      return request.status=="accepted" and request.supplier_id==npc.id
        and type(request.accepted_count)=="number" and request.accepted_count>=job.count
        and type(request.expires_tick)=="number" and request.expires_tick>=game.tick
        and recipient and recipient.force and same_force(recipient.force,target.force)
        and Cooperation.project_valid(request,recipient)
        and Cooperation.project_allowed(storage.society,npc,request,recipient)
    end
  end
  return false
end

local function authorized(npc, job, target)
  if job.owner_force and not same_force(job.owner_force, target.force) then return false end
  if same_force(npc.character.force, target.force) then return true end
  -- the team world: a teammate's chest is open to it, a player's only while that player's yes stands
  if job.kind=="transfer" and job.mode=="withdraw" and not job.social_intent_id
      and TeamSupply.may_withdraw(npc,target,job.item) then return true end
  if job.social_intent_id then
    local intent=storage.social_intents and storage.social_intents[job.social_intent_id]
    if intent and intent.status=="pending" and intent.actor_id==npc.id
        and intent.target_unit_number==target.unit_number and intent.target_force==target.force.name
        and (not intent.target_entity or intent.target_entity==target)
        and intent.surface_index==target.surface.index and intent.expires_tick>=game.tick
        and intent.item==job.item and job.count<=intent.count
        and accepted_contract(npc,job,target,intent) then
      if (job.kind=="theft" or job.kind=="sabotage") and job.mode=="withdraw"
          and job.kind==intent.kind then
        local own,other=npc.character.force,target.force
        return not own.get_friend(other) and not other.get_friend(own)
          and not own.get_cease_fire(other) and not other.get_cease_fire(own)
      end
      if job.kind=="transfer" and intent.kind=="supply" and job.mode=="deposit" then return true end
    end
    return false
  end
  if job.kind=="theft" or job.kind=="sabotage" then return false end
  local permission = npc.transfer_permissions and npc.transfer_permissions[target.force.name]
  return type(permission) == "table" and permission[job.mode] == true
end

function Worker.observe(npc)
  local c=npc and npc.character
  if not (c and c.valid) then return {body_valid=false} end
  local job=npc.current_job
  local target=job and job.target and job.target.valid and job.target
  local selected=c.selected and c.selected.valid and c.selected
  local result={body_valid=true,tick=game.tick,position={x=c.position.x,y=c.position.y},
    job_id=job and job.id,kind=job and job.kind,state=job and job.state,
    upkeep_source=job and job.upkeep_source,resource_source=job and job.resource_source,
    resource_unavailable=job and job.resource_unavailable,
    item=job and job.item,item_count=job and job.item and count(npc,job.item),
    initial_count=job and job.initial_count,actual_count=job and job.actual_count,
    last_gain_tick=job and job.last_gain_tick,mining_progress=c.character_mining_progress,
    mining_state=c.mining_state,resource_reach=c.resource_reach_distance,
    crafting_queue_size=c.crafting_queue_size,inventory_full=main(npc).is_full(),
    selected_name=selected and selected.name,
    selected_position=selected and {x=selected.position.x,y=selected.position.y},
    target_name=target and target.name,target_minable=target and target.minable,
    target_position=target and {x=target.position.x,y=target.position.y},
    selected_matches_target=target and selected==target or false}
  if target then
    result.target_distance=Navigation.distance(c.position,target.position)
    if target.type=="resource" then result.target_amount=target.amount end
    result.covering_entities={}
    for _,entity in ipairs(c.surface.find_entities_filtered{position=target.position,radius=.3}) do
      if entity~=target and entity~=c then result.covering_entities[#result.covering_entities+1]=entity.name end
    end
  end
  return result
end

-- `own` allows picking up the NPC's own buildings (deconstruct jobs); the
-- character mines them natively and receives the building item and contents.
local function yield_item(target, item, own)
  local natural = target.type == "resource" or target.type == "tree" or target.type == "simple-entity"
  if not natural and not own then return nil end
  local properties = target.prototype.mineable_properties
  if not target.minable or not properties or not properties.minable or properties.required_fluid then return nil end
  for _, product in ipairs(properties.products or {}) do
    if product.type == "item" and (not item or product.name == item) then return product.name end
  end
end

local ResourceMemory=require('scripts.resource_memory')
local RUN_SPEED = 9   -- tiles a second an engineer covers on foot (about a character's running speed)

-- How much of `item` mining `entity` gives a second, walks included, counting no more than the job still
-- `needs`: what its own products say one mining gives (an ore tile gives that each time, as often as wanted; a rock
-- or a tree once), over its minings at the engineer's mining speed plus the walk there and back (it goes back to
-- its work). A huge rock's 24 to 50 stone (and its coal) for one mining beats an ore tile's one at a time, unless only
-- a stone or two are wanted: the user, 2026-10-24, "a lot of stone in the early game by just breaking the stone rocks
-- around the starting area". Counting the walk one way only, and an ore tile's walk against one item, it walked 130
-- tiles to a rock for ten coal with coal ore ten tiles away, the rocks round home used up (trace 2026-10-26).
local function yield_rate(character, entity, item, distance, needs)
  local properties = entity.prototype.mineable_properties
  local amount = 0
  for _, product in ipairs(properties and properties.products or {}) do
    if product.type == "item" and product.name == item then
      local n = product.amount or ((product.amount_min or 1) + (product.amount_max or product.amount_min or 1)) / 2
      amount = amount + n * (product.probability or product.independent_probability or 1)
    end
  end
  if amount <= 0 then return 0 end
  local speed = (character.prototype and character.prototype.mining_speed) or 0.5
  local want = math.max(1, needs or amount)
  local get = math.min(amount, want)
  if entity.type == "resource" then get = math.min(want, math.max(amount, (entity.amount or want) * amount)) end
  local minings = math.ceil(get / amount)
  return get / (minings * (properties.mining_time or 1) / math.max(0.01, speed) + 2 * distance / RUN_SPEED)
end
Worker.yield_rate = yield_rate

-- What yields `item` when mined by hand (types as discover looks for them): the prototypes' names, and the fewest
-- seconds of mining (at speed 1) one item takes from any of them. Built from prototypes: the same on every machine.
-- (A product that never comes, probability 0, is no source: the whole-radius search could pick one at a rate of 0.)
local SOURCES = {}
local function sources(item, natural_only)
  local key = tostring(item) .. (natural_only and ":natural" or "")
  local s = SOURCES[key]
  if s then return s end
  s = {names = {}, per_item = math.huge}
  local types = natural_only and {"tree", "simple-entity"} or {"resource", "tree", "simple-entity"}
  local ok, all = pcall(function() return prototypes.get_entity_filtered{{filter = "type", type = types}} end)
  for name, p in pairs(ok and all or {}) do
    local props = p.mineable_properties
    if props and props.minable and not props.required_fluid then
      local amount = 0
      for _, product in ipairs(props.products or {}) do
        if product.type == "item" and product.name == item then
          local n = product.amount or ((product.amount_min or 1) + (product.amount_max or product.amount_min or 1)) / 2
          amount = amount + n * (product.probability or product.independent_probability or 1)
        end
      end
      if amount > 0 then
        s.names[#s.names + 1] = name
        s.per_item = math.min(s.per_item, (props.mining_time or 1) / amount)
      end
    end
  end
  table.sort(s.names)
  SOURCES[key] = s
  return s
end

-- Rings out from the engineer, nearest first (a forest or a big ore field held thousands of candidates in one radius-96
-- query, again each time a tree ran out: the Workers Union mod searches 8, 24, 64, 128 tiles out). After each ring it
-- stops once nothing farther can do better: from `r` tiles out the best a source can give is W/(W*k + 2r/RUN_SPEED)
-- (W what the job still needs, k the fewest seconds of mining an item: yield_rate's own sum with every mining at its
-- best), so the target is the very one the whole radius would have given.
Worker.RINGS = {8, 24, 64, 128}
local function discover(npc, job)
  local character = npc.character
  local radius = math.min(128, math.max(8, job.search_radius or 96))
  -- (natural_only: trees and rocks, never a patch its drills mine: designed_start's acquire)
  local src = sources(job.item, job.natural_only)
  local speed = math.max(0.01, (character.prototype and character.prototype.mining_speed) or 0.5)
  local needs = (job.count or 1) - (job.actual_count or 0)
  local W = math.max(1, needs)
  -- the most of the item a second, walk included (ties: nearest, then by position, the same on every machine)
  local best, best_rate, best_distance
  local inner = -1
  -- (no source known by name: one query over the whole radius by kind, as before)
  local named = #src.names > 0
  local types = job.natural_only and {"tree", "simple-entity"} or {"resource", "tree", "simple-entity"}
  for _, ring in ipairs(named and Worker.RINGS or {radius}) do
    local r = math.min(ring, radius)
    for _, entity in ipairs(character.surface.find_entities_filtered{
        position = character.position, radius = r, name = named and src.names or nil, type = not named and types or nil}) do
      local d = entity.valid and Navigation.distance(character.position, entity.position)
      if d and d > inner and entity.force.name == "neutral" and yield_item(entity, job.item)
          and not (job.excluded and job.excluded[entity.unit_number or (entity.name .. ":" .. entity.position.x .. ":" .. entity.position.y)]) then
        local rate = yield_rate(character, entity, job.item, d, needs)
        if not best or rate > best_rate or (rate == best_rate and (d < best_distance or (d == best_distance and
            (entity.position.x < best.position.x or (entity.position.x == best.position.x and entity.position.y < best.position.y))))) then
          best, best_rate, best_distance = entity, rate, d
        end
      end
    end
    inner = r
    if r >= radius then break end
    if best and named and best_rate >= W / (W * src.per_item / speed + 2 * r / RUN_SPEED) then break end
  end
  if best then job.resource_source='local_observation';job.resource_unavailable=nil;return best end
  if job.natural_only then return nil end   -- (no remembered patch either)
  local remembered,reason=ResourceMemory.recall(npc,job.item,job.excluded)
  job.resource_source=remembered and 'observed_camp_patch' or nil
  job.resource_unavailable=reason
  return remembered
end
Worker.discover = discover

-- A tree or rock it cannot get to for others round it (a forest): it cuts the one in the way first, as a player clears
-- a way, then goes on to the target, in the same job (a detour: what the job is for and what it counts stay its own).
-- The one cut: near it, on the line toward the target and nearer it, on its own ground; nil if none.
-- Unreached, a belt tile under a tree in a forest was left out of a coal line, and its boilers were fed by hand for the
-- rest of the run (run 60, 2026-10-26).
Worker.CUT_THROUGH, Worker.CUT_NEAR, Worker.CUT_OFF_LINE, Worker.DETOUR_TICKS = 12, 6, 1.5, 900
function Worker.in_the_way(npc, target)
  local character = npc.character
  local c, t = character.position, target.position
  local here = Navigation.distance(c, t)
  if here <= 0 then return nil end
  local ux, uy = (t.x - c.x) / here, (t.y - c.y) / here
  local best, best_d
  for _, e in ipairs(character.surface.find_entities_filtered{position = c, radius = Worker.CUT_NEAR,
      type = {"tree", "simple-entity"}}) do
    if e.valid and e ~= target and e.minable and e.force.name == "neutral"
        and Navigation.distance(e.position, t) < here then
      local px, py = e.position.x - c.x, e.position.y - c.y
      local off = math.abs(px * uy - py * ux)   -- (how far off the line toward the target)
      local b = e.bounding_box
      local box = b and {{b.left_top.x, b.left_top.y}, {b.right_bottom.x, b.right_bottom.y}}
        or {{e.position.x - 0.5, e.position.y - 0.5}, {e.position.x + 0.5, e.position.y + 0.5}}
      -- (its own ground only: not another engineer's plot, not by a player's buildings: TeamWorld.may_build)
      if off <= Worker.CUT_OFF_LINE and (not TeamWorld.active() or TeamWorld.may_build(npc, box, character.surface)) then
        local d = Navigation.distance(c, e.position)
        if not best or d < best_d then best, best_d = e, d end
      end
    end
  end
  return best
end
-- `target`: what the job goes to, an entity or, for a building to put down, {position=...}. Recorded as a rung of the
-- ladder for whatever is in the way (scripts/obstacles.lua keeps the same log).
local function cut_through(npc, job, target, reason, tick)
  if not (reason == "path_ended_outside_reach" or reason == "no_path") then return false end
  -- (cuts that bring it no nearer are counted, not all: a wide forest takes many, each a step on)
  local here = Navigation.distance(npc.character.position, target.position)
  if not job.cut_best or here < job.cut_best - 1 then job.cut_best, job.cuts = here, 0 end
  if (job.cuts or 0) >= Worker.CUT_THROUGH then return false end
  local way = Worker.in_the_way(npc, target)
  if not way then return false end
  job.cuts = (job.cuts or 0) + 1
  stop(npc, job)
  job.detour, job.detour_until, job.detour_base = way, tick + Worker.DETOUR_TICKS, job.actual_count or 0
  npc.obstacle_log = npc.obstacle_log or {}
  table.insert(npc.obstacle_log, {tick = tick, rung = "cut_through", what = way.type .. ":" .. way.name,
    detail = job.kind .. " " .. reason})
  while #npc.obstacle_log > 64 do table.remove(npc.obstacle_log, 1) end
  log("[npc-factions] npc=" .. tostring(npc.id) .. " in the way of its " .. job.kind .. " (" .. reason .. "): cut_through "
    .. way.name)
  return true
end
-- The detour, while it is on (true): to the one in the way, and cut it; then back to the job's own target, what the
-- cut gave no part of the job's count.
local function detour_step(npc, job, tick)
  local way = job.detour
  if not way then return false end
  if not way.valid or tick > (job.detour_until or 0) then
    job.detour, job.detour_until = nil, nil
    stop(npc, job)
    if job.item and job.initial_count then
      local now = count(npc, job.item)
      job.initial_count, job.last_count, job.last_gain_tick = now - (job.detour_base or 0), now, tick
    end
    return false
  end
  local reach = math.min(npc.character.resource_reach_distance, npc.character.reach_distance)
  if Navigation.distance(npc.character.position, way.position) > math.max(0.3, reach - 0.45) then
    npc.character.mining_state = {mining = false}
    local state = Navigation.step(npc, job, way.position, math.max(0.3, reach - 0.6), tick)
    if state == "failed" then job.detour_until = 0 end   -- (no way to that one either: back to the target)
    return true
  end
  Navigation.stop(npc.character)
  npc.character.selected = way
  npc.character.mining_state = {mining = true, position = way.position}
  return true
end

-- Clearing ground (mode "clear", scripts/clearing.lua): one thing of the map's own (a tree, a rock, a ruin a
-- mod left) mined where a later build will go. Done once it is gone, whatever it gave: some things give
-- nothing, some several things at once (a huge rock: stone and coal), all kept.
local function clear(npc, job, tick)
  if detour_step(npc, job, tick) then return end
  local target = job.target
  if not valid_target(npc, target) then
    if job.started_mining then return finish(npc, job, "complete", nil, tick) end
    return fail(npc, job, "target_missing", tick)
  end
  local properties = target.prototype.mineable_properties
  if target.force.name ~= "neutral" or not target.minable or not (properties and properties.minable)
      or properties.required_fluid then
    return fail(npc, job, "forbidden_mining_target", tick)
  end
  for _, product in ipairs(properties.products or {}) do
    if product.type == "item" and not main(npc).can_insert{name = product.name, count = 1, quality = "normal"} then
      return fail(npc, job, "inventory_full", tick)
    end
  end
  local reach = math.min(npc.character.resource_reach_distance, npc.character.reach_distance)
  if Navigation.distance(npc.character.position, target.position) > math.max(0.3, reach - 0.45) then
    if not (job.navigation and job.navigation.chop) then npc.character.mining_state = {mining = false} end
    job.state = "navigating"
    local state, reason = Navigation.step(npc, job, target.position, math.max(0.3, reach - 0.6), tick)
    if state == "failed" then
      if cut_through(npc, job, target, reason, tick) then return end   -- (the one in the way first)
      return fail(npc, job, reason, tick)
    end
    if state ~= "arrived" then return end
  end
  if job.state ~= "interacting" then job.last_gain_tick = tick end
  job.state, job.started_mining = "interacting", true
  Navigation.stop(npc.character)
  npc.character.selected = target
  npc.character.mining_state = {mining = true, position = target.position}
  if tick - job.last_gain_tick > math.max(600, (properties.mining_time or 1) * 60 * 10) then
    fail(npc, job, "mining_no_yield", tick)
  end
end

local function mine(npc, job, tick)
  if job.mode == "clear" then return clear(npc, job, tick) end
  if not job.item then
    -- (a clearing whose obstacle went before it began: done all the same; Codex's LIVE-OP-a review)
    if job.clears and not (job.target and job.target.valid) then return finish(npc, job, "complete", nil, tick) end
    if not valid_target(npc, job.target) then return fail(npc, job, "mining_item_required", tick) end
    job.item = yield_item(job.target, nil, job.mode == "deconstruct" and job.target.force == npc.force)
    if not job.item then return fail(npc, job, "unsupported_mining_target", tick) end
  end
  if not job.initial_count then
    job.initial_count, job.last_count, job.last_gain_tick = count(npc, job.item), count(npc, job.item), tick
    job.discover_targets = not job.target
  end
  if detour_step(npc, job, tick) then return end   -- (a tree in the way of its target being cut first)
  local current = count(npc, job.item)
  job.actual_count = math.max(0, current - job.initial_count)
  if current > job.last_count then job.last_gain_tick = tick end
  job.last_count = current
  if job.actual_count >= job.count then return finish(npc, job, "complete", nil, tick) end
  if not valid_target(npc, job.target) then
    stop(npc, job)
    -- (a job that clears a spot: its obstacle gone, by whatever hand or robot, is the job done; LIVE-OP-a: the engineer's
    -- own construction robots took the trees in a wall's way, each clearing failed "target_exhausted", and the ring's
    -- 170-piece north wall was given up with 3 standing)
    if job.clears and not (job.target and job.target.valid) then return finish(npc, job, "complete", nil, tick) end
    if not job.discover_targets then return fail(npc, job, "target_exhausted", tick) end
    job.target = discover(npc, job)
    job.last_gain_tick = tick
    if not job.target then return fail(npc, job, "no_local_resource", tick) end
  end
  local target = job.target
  local own = job.mode == "deconstruct" and target.force == npc.force
  if (target.force.name ~= "neutral" and not own) or not yield_item(target, job.item, own) then
    return fail(npc, job, "forbidden_mining_target", tick)
  end
  if not main(npc).can_insert{name = job.item, count = 1, quality = "normal"} then
    return fail(npc, job, "inventory_full", tick)
  end
  -- 2.0.77 diagnostic: a selected tree at 4.7 tiles, general reach 10 and
  -- resource reach 2.7 kept mining=true but progress=0. Use the conservative
  -- mining reach for trees/rocks as well; the following engine gate qualifies it.
  local reach = math.min(npc.character.resource_reach_distance, npc.character.reach_distance)
  -- A conservative centre distance keeps the natural mining state inside engine reach.
  if Navigation.distance(npc.character.position, target.position) > math.max(0.3, reach - 0.45) then
    -- (chopping its way out of trees on the way mines, too)
    if not (job.navigation and job.navigation.chop) then npc.character.mining_state = {mining = false} end
    job.state = "navigating"
    local state, reason = Navigation.step(npc, job, target.position, math.max(0.3, reach - 0.6), tick)
    if state == "failed" then
      if job.discover_targets and (job.retargets or 0) < 3 then
        job.retargets = (job.retargets or 0) + 1
        job.excluded = job.excluded or {}
        job.excluded[target.unit_number or (target.name .. ":" .. target.position.x .. ":" .. target.position.y)] = true
        stop(npc, job)
        job.target = nil
        return
      end
      if job.resource_source=='observed_camp_patch' then ResourceMemory.route_failed(npc,job.item,tick,reason) end
      -- A building of its own it cannot walk close enough to mine (the engine's mining state works only at a tree's
      -- reach) but within the reach it builds in: picked up as a player picks one up, from where it stands. A belt
      -- boxed in by walls on both sides was never reached, and a whole wall plan was given up (run 60's checkpoint
      -- replayed, 2026-10-26). What it gives is counted next tick.
      if own and npc.character.can_reach_entity(target) then
        local ok = pcall(function() return npc.character.mine_entity(target) end)
        if ok and not target.valid then stop(npc, job) return end
      end
      -- (a given target out of reach: the tree or rock in the way first)
      if not job.discover_targets and cut_through(npc, job, target, reason, tick) then return end
      return fail(npc, job, reason, tick)
    end
    if state ~= "arrived" then return end
    job.last_gain_tick = tick
  end
  -- The no-yield timeout measures actual mining time. A distant discovered
  -- resource can consume most of 600 ticks in navigation before the character
  -- is close enough to begin native mining progress.
  if job.state ~= "interacting" then job.last_gain_tick = tick end
  job.state = "interacting"
  Navigation.stop(npc.character)
  npc.character.selected = target
  npc.character.mining_state = {mining = true, position = target.position}
  local mining_time = target.prototype.mineable_properties.mining_time or 1
  -- (a building of its own whose mining makes no headway: picked up as a player picks one up, before it gives up; the
  -- retired burner rows of run 60's checkpoint stood through six such tries each, replayed 2026-10-26)
  if own and tick - job.last_gain_tick > math.max(120, mining_time * 60 * 4) and npc.character.can_reach_entity(target) then
    local ok = pcall(function() return npc.character.mine_entity(target) end)
    if ok and not target.valid then stop(npc, job) return end
  end
  if tick - job.last_gain_tick > math.max(600, mining_time * 60 * 10) then
    fail(npc, job, "mining_no_yield", tick)
  end
end

-- A craft in the background: queued in the engineer's own crafting queue, which runs while it walks, builds and
-- mines, as a player's does; the job is done once queued, and the products come into its inventory as they are
-- made. An engineer that stood still for each craft spent 35 to 55% of its time crafting from game minute 18 to 96
-- (run 41, 2026-10-26). What the queue makes is credited to the force's production statistics as it comes out
-- (Worker.credit_background): the game does not count a script character's hand crafts, and research triggers read
-- them (the first lab crafted unlocks red science: uncredited, run 43's lab waited for packs it could never make).
local function background_craft(npc, job, tick, recipe)
  local character = npc.character
  for _, product in ipairs(recipe.products) do
    if main(npc).get_insertable_count(normal(product.name)) < product.amount * job.count then
      return fail(npc, job, "inventory_full", tick)
    end
  end
  local before = inventory_counts(main(npc))
  local started = character.begin_crafting{count = job.count, recipe = job.recipe, silent = true}
  if not started or started < 1 then return fail(npc, job, "missing_craft_ingredients", tick) end
  local after = inventory_counts(main(npc))
  job.consumed = {}
  for item, n in pairs(before) do
    if n > (after[item] or 0) then job.consumed[item] = n - (after[item] or 0) end
  end
  npc.background_crafts = true
  local made = {}
  for _, product in ipairs(recipe.products) do made[product.name] = (made[product.name] or 0) + product.amount end
  npc.background_queued = npc.background_queued or {}
  table.insert(npc.background_queued, {recipe = job.recipe, count = started, done = 0, made = made})
  job.actual_count, job.completed = started, started
  finish(npc, job, "complete", nil, tick, {queued = started})
end
Worker.background_craft = background_craft

Worker.QUEUE_WAIT = 36000   -- ticks an owned craft waits for the background crafts before it, at most
local function craft(npc, job, tick)
  local character, inventory = npc.character, main(npc)
  local recipe = character.force.recipes[job.recipe or ""]
  if not recipe or not recipe.enabled then return fail(npc, job, "recipe_locked_or_missing", tick) end
  if not Recipes.made_in(recipe,character.prototype.crafting_categories) then
    return fail(npc, job, "recipe_not_handcraftable", tick)
  end
  local products = recipe.products
  for _, product in ipairs(products) do
    if product.type ~= "item" or not product.amount or (product.probability and product.probability ~= 1) then
      return fail(npc, job, "unsupported_craft_products", tick)
    end
  end
  if job.background then return background_craft(npc, job, tick, recipe) end
  job.completed, job.produced = job.completed or 0, job.produced or {}
  Navigation.stop(character)
  job.state = "interacting"
  if job.craft_started then
    if queue_count(character, job.recipe) > 0 then
      extend_native_craft_deadline(character,job,tick)
      if tick > (job.craft_stall_deadline or tick+3600) then
        return fail(npc, job, "craft_queue_stalled", tick)
      end
      return
    end
    -- Queue disappearance is insufficient: verify outputs against the post-enqueue inventory.
    for _, product in ipairs(products) do
      if craft_output_count(npc,product.name) - job.craft_baseline[product.name] < product.amount then
        return fail(npc, job, "craft_canceled_or_output_missing", tick)
      end
    end
    job.completed = job.completed + 1
    for _, product in ipairs(products) do
      job.produced[product.name] = (job.produced[product.name] or 0) + product.amount
    end
    Credit.complete(npc, job, recipe)
    job.craft_started = false
    job.craft_timeout_set,job.craft_stall_deadline=nil,nil
    if job.completed >= job.count then
      job.actual_count = job.completed
      return finish(npc, job, "complete", nil, tick)
    end
  end
  if character.crafting_queue_size > 0 then
    -- (its own crafts queued in the background run first: this one waits its turn, a while)
    if not npc.background_crafts then return fail(npc, job, "native_queue_not_owned", tick) end
    job.queue_wait = job.queue_wait or tick
    if tick - job.queue_wait > Worker.QUEUE_WAIT then return fail(npc, job, "native_queue_busy", tick) end
    return
  end
  npc.background_crafts = nil
  for _, product in ipairs(products) do
    if inventory.get_insertable_count(normal(product.name)) < product.amount then
      return fail(npc, job, "inventory_full", tick)
    end
  end
  local before = inventory_counts(inventory)
  local credit_ok, credit_reason = Credit.begin(npc, job)
  if not credit_ok then return fail(npc, job, credit_reason, tick) end
  local started = character.begin_crafting{count = 1, recipe = job.recipe, silent = true}
  if started ~= 1 then return fail(npc, job, "missing_craft_ingredients", tick) end
  local after = inventory_counts(inventory)
  job.consumed = job.consumed or {}
  for item, n in pairs(before) do
    local removed = n - (after[item] or 0)
    if removed > 0 then job.consumed[item] = (job.consumed[item] or 0) + removed end
  end
  job.craft_started, job.craft_start_tick, job.craft_baseline = true, tick, {}
  for _, product in ipairs(products) do job.craft_baseline[product.name] = craft_output_count(npc,product.name) end
  job.craft_timeout_set=nil
  extend_native_craft_deadline(character,job,tick)
end

local function rotated_build_box(prototype, position, direction)
  local bounds=prototype and prototype.collision_box
  local turns=({[defines.direction.north]=0,[defines.direction.east]=1,
    [defines.direction.south]=2,[defines.direction.west]=3})[direction]
  if not bounds or turns==nil then return nil end
  local a,z=bounds.left_top or bounds[1],bounds.right_bottom or bounds[2]
  if not a or not z then return nil end
  local x1,y1,x2,y2=math.huge,math.huge,-math.huge,-math.huge
  for _,p in ipairs({{a.x,a.y},{a.x,z.y},{z.x,a.y},{z.x,z.y}}) do
    local x,y=p[1],p[2]
    for _=1,turns do x,y=-y,x end
    x1,y1,x2,y2=math.min(x1,x),math.min(y1,y),math.max(x2,x),math.max(y2,y)
  end
  return {x1=position.x+x1,y1=position.y+y1,
    x2=position.x+x2,y2=position.y+y2}
end

local function boxes_overlap(a,b)
  return a.x1<b.x2 and b.x1<a.x2 and a.y1<b.y2 and b.y1<a.y2
end

-- (touching counts: the game refuses a building whose box only meets the character's edge, and with a strict test it
-- stood on that edge, nudged there by a pole beside its stance, and a lab was refused at "nothing there" three times,
-- the proving ground 2026-10-28)
Worker.TOUCH=0.05
local function character_overlaps_build(character,box)
  local b,e=character.bounding_box,Worker.TOUCH
  return boxes_overlap({x1=b.left_top.x-e,y1=b.left_top.y-e,
    x2=b.right_bottom.x+e,y2=b.right_bottom.y+e},box)
end

-- Where to stand to build, chosen before it walks there (the Workers Union review, 2026-10-29: their
-- construct.lua:265-447, MIT, the idea taken, no code): within build reach of the spot, out of its box and out of the
-- boxes of the pieces still to go up near it (job.avoid), on open ground (Rescue.open_at), the nearest such point to
-- where it is; spots found shut in are remembered POCKET_KEPT ticks. A stance picked once on the spot after the walk
-- had it step aside again and again, into the next piece's place.
Worker.POCKET_KEPT = 3600
Worker.STAND_RINGS = {1.5, 3, 5}
Worker.STAND_TRIES = 8
local function inside(p, b) return p.x > b[1][1] and p.x < b[2][1] and p.y > b[1][2] and p.y < b[2][2] end
function Worker.stand_point(npc, job, box, tick)
  local c = npc.character
  local reach = c.build_distance - 1.25   -- (arrived within 0.5 of it: still nearer than build_distance - 0.5)
  local spot = job.position
  local avoid = {{{box.x1, box.y1}, {box.x2, box.y2}}}
  for _, b in ipairs(job.avoid or {}) do avoid[#avoid + 1] = b end
  npc.closed_pockets = npc.closed_pockets or {}
  for k, at in pairs(npc.closed_pockets) do
    if tick - at >= Worker.POCKET_KEPT then npc.closed_pockets[k] = nil end
  end
  local tries = 0
  local list = {}
  for _, r in ipairs(Worker.STAND_RINGS) do
    local around = math.max(box.x2 - box.x1, box.y2 - box.y1) / 2 + r
    for k = 0, 15 do
      local a = k * math.pi / 8
      list[#list + 1] = {x = spot.x + math.cos(a) * around, y = spot.y + math.sin(a) * around}
    end
  end
  table.sort(list, function(p, q)
    local dp, dq = Navigation.distance(c.position, p), Navigation.distance(c.position, q)
    if dp ~= dq then return dp < dq end
    if p.x ~= q.x then return p.x < q.x end
    return p.y < q.y
  end)
  for _, p in ipairs(list) do
    if tries >= Worker.STAND_TRIES then return nil end   -- (each a flood fill: as near as it gets after so many)
    local clear = Navigation.distance(p, spot) <= reach
    for _, b in ipairs(avoid) do if clear and inside(p, b) then clear = false end end
    local k = math.floor(p.x) .. "," .. math.floor(p.y)
    local shut = npc.closed_pockets[k]
    if clear and shut and tick - shut < Worker.POCKET_KEPT then clear = false end
    if clear then
      local found = c.surface.find_non_colliding_position("character", p, 0.5, 0.25)
      if found and Navigation.distance(found, spot) <= reach then
        local ok = true
        for _, b in ipairs(avoid) do if inside(found, b) then ok = false end end
        if ok then
          tries = tries + 1
          if not Rescue.open_at(c, found, avoid) then npc.closed_pockets[k] = tick; ok = false end
        end
        if ok then return {x = found.x, y = found.y} end
      end
    end
  end
end

local function choose_build_stance(character,position,box)
  local cb=character.prototype.collision_box
  local a,z=cb.left_top or cb[1],cb.right_bottom or cb[2]
  if not a or not z then return nil end
  local margin=.6
  local function clamp(value,lo,hi) return math.max(lo,math.min(hi,value)) end
  local x=clamp(character.position.x,box.x1,box.x2)
  local y=clamp(character.position.y,box.y1,box.y2)
  local left,right=box.x1-z.x-margin,box.x2-a.x+margin
  local top,bottom=box.y1-z.y-margin,box.y2-a.y+margin
  local candidates={{x=left,y=y},{x=right,y=y},{x=x,y=top},{x=x,y=bottom},
    {x=left,y=top},{x=left,y=bottom},{x=right,y=top},{x=right,y=bottom}}
  table.sort(candidates,function(one,two)
    local da=Navigation.distance(character.position,one)
    local db=Navigation.distance(character.position,two)
    return da<db or (da==db and (one.y<two.y or
      (one.y==two.y and one.x<two.x)))
  end)
  for _,candidate in ipairs(candidates) do
    local found=character.surface.find_non_colliding_position('character',candidate,1,.25)
    if found and Navigation.distance(found,position)<=character.build_distance-.75 then
      local e=Worker.TOUCH   -- (the same margin the build checks with: else a stance on the edge is chosen for ever)
      local stance={x1=found.x+a.x-e,y1=found.y+a.y-e,
        x2=found.x+z.x+e,y2=found.y+z.y+e}
      if not boxes_overlap(stance,box) then
        return {x=found.x,y=found.y}
      end
    end
  end
end

-- What defends goes down in a fight on purpose (a kit's turrets, walls): never refused for the danger it is against.
Worker.FIGHTS = {["ammo-turret"]=true, ["electric-turret"]=true, ["fluid-turret"]=true, ["artillery-turret"]=true,
  wall=true, gate=true,
  ["land-mine"]=true}
local function brave(name)
  local p = name and prototypes.entity[name]
  return p ~= nil and Worker.FIGHTS[p.type] == true
end

local function build(npc, job, tick)
  if not job.position or not job.entity_name or not job.item then return fail(npc, job, "invalid_build", tick) end
  -- (never into a fight, whatever asked for it: biters, a nest, a worm's reach at its spot; W-30)
  job.danger = job.danger or {}
  if not brave(job.entity_name) and Hands.danger_at(npc, job.danger, 1, job.position, tick) then
    return fail(npc, job, "in_danger", tick)
  end
  local item = prototypes.item[job.item]
  local entity_prototype = prototypes.entity[job.entity_name]
  local rail_item=Rail.supports(job.entity_name) and Rail.item_for_entity(npc,job.entity_name)
  local basic=item and item.type=="item" and item.place_result and item.place_result.name==job.entity_name
      and entity_prototype and BUILD_TYPES[entity_prototype.type]
  if not basic and not (rail_item and rail_item.name==job.item) then
    return fail(npc, job, "unsupported_placement_item", tick)
  end
  -- The team world: only on its own ground (its plot, its bands of the yard, or out past the wall) and
  -- clear of players' builds, whatever asked for the building.
  if TeamWorld.active() and entity_prototype then
    local b = rotated_build_box(entity_prototype, job.position, job.direction or defines.direction.north)
    local ok, why = TeamWorld.may_build(npc, b and {{b.x1, b.y1}, {b.x2, b.y2}}
      or {{job.position.x, job.position.y}, {job.position.x, job.position.y}})
    if not ok then return fail(npc, job, "not_its_ground:" .. tostring(why), tick) end
  end
  local character = npc.character
  local placement = {name = job.entity_name, position = job.position, direction = job.direction or defines.direction.north,
    type = job.belt_to_ground_type}
  -- Native build reach is the authority. In particular, a coastal pole can
  -- already be placeable even when the conservative path radius is 0.01 tile
  -- too short to reach before water stops the walking path.
  local natively_placeable=not rail_item and character.can_place_entity(placement)
  if detour_step(npc, job, tick) then return end   -- (a tree in the way of the spot being cut first)
  if not natively_placeable and
      Navigation.distance(character.position, job.position) > math.max(0.3, character.build_distance - 0.5) then
    job.state = "navigating"
    -- (to a stand point chosen now, not anywhere in reach: Worker.stand_point; else as near as it gets)
    if job.stand == nil and not rail_item and entity_prototype then
      -- (a choice only: whatever goes wrong in it, it walks as before)
      local ok, at = pcall(function()
        local b = rotated_build_box(entity_prototype, job.position, job.direction or defines.direction.north)
        return b and Worker.stand_point(npc, job, b, tick)
      end)
      job.stand = ok and at or false
    end
    local goal, radius = job.position, math.max(0.3, character.build_distance - 0.7)
    if job.stand then goal, radius = job.stand, 0.5 end
    local state, reason = Navigation.step(npc, job, goal, radius, tick)
    if state == "failed" and job.stand then job.stand = false; return end   -- (no way there: as near as it gets)
    if state == "failed" then
      if cut_through(npc, job, {position = job.position}, reason, tick) then return end
      return fail(npc, job, reason, tick)
    end
    if state ~= "arrived" then return end
  end
  -- A character can finish clearing a planned site while standing inside its
  -- future collision box. Walk to a standable edge before native placement;
  -- this remains an ordinary path request and spends no item until all gates.
  if not rail_item then
    local box=rotated_build_box(entity_prototype,job.position,
      job.direction or defines.direction.north)
    if box and character_overlaps_build(character,box) then
      if not job.build_stance then
        job.build_stance=choose_build_stance(character,job.position,box)
      end
      if not job.build_stance then return fail(npc,job,"no_reachable_build_stance",tick) end
      if not navigate(npc,job,job.build_stance,.2,tick) then return end
      if character_overlaps_build(character,box) then
        -- arrived, still on the spot (pushed off its stance): a stance chosen afresh from here, at most a few times
        job.stance_tries=(job.stance_tries or 0)+1
        job.build_stance=nil
        if job.stance_tries>3 then return fail(npc,job,"no_reachable_build_stance",tick) end
        return
      end
    end
  end
  job.state = "interacting"
  if rail_item then
    local allowed,reason=Rail.valid_owned_placement(npc,placement)
    if not allowed then return fail(npc,job,reason,tick) end
  end
  -- One of its own of the same group stands there (a stone furnace where a steel one goes, a belt under a faster one):
  -- replaced as a player does, in one go, contents and recipe kept, the old one back in its hands (the Workers Union
  -- review, 2026-10-29: their construct.lua:942-972, MIT, the idea taken, no code). Picked up and put down again, the
  -- old one's ore and plates were spilled or carried, and the line stood empty a while.
  if not rail_item and Worker.fast_replace(npc, job, placement, tick) then return end
  if not character.can_place_entity(placement) then
    -- Say what stands in the way (a cliff, a building of another force...), for whoever reads the log.
    local box=rotated_build_box(entity_prototype,job.position,job.direction or defines.direction.north)
    local blockers={}
    local lasting
    local area=box and {{box.x1,box.y1},{box.x2,box.y2}}   -- (the box helper's own x1/y1 form is no area)
    -- Its own vehicle parked in the way (drive.lua parks it near its work): picked up, as a player would, and
    -- the building tried again. (Only its own: a teammate's car is the teammate's.)
    local own=npc.parked
    if area and own and own.valid and not own.get_driver() and not own.get_passenger() then
      local q=own.position
      if q.x>=area[1][1]-2 and q.x<=area[2][1]+2 and q.y>=area[1][2]-2 and q.y<=area[2][2]+2
          and pcall(function() return character.mine_entity(own,true) end) and not own.valid then
        npc.parked=nil
        return
      end
    end
    if box and character_overlaps_build(character,box) then blockers[#blockers+1]="self:character" end
    for _,e in ipairs(area and character.surface.find_entities_filtered{area=area} or {}) do
      if e.valid and e~=character then
        blockers[#blockers+1]=e.type..":"..e.name
        if Worker.for_good(character,e) then lasting=lasting or e; Worker.unbuildable(npc,e) end
      end
    end
    -- (refused with nothing there: the game's own verdict, reach or ground or grid; that spot is not offered again
    -- for a while by Hands.free_spot, W-25)
    if #blockers==0 then Hands.refused(npc,job.entity_name,job.position,tick) end
    log("[npc-factions] npc="..tostring(npc.id).." cannot place "..tostring(job.entity_name).." at "
      ..job.position.x..","..job.position.y..": "..(#blockers>0 and table.concat(blockers,", ") or "nothing there"))
    return fail(npc, job, "placement_blocked_or_out_of_reach", tick, lasting and {blocked_by=lasting.type} or nil)
  end
  placement.force = character.force
  placement.build_check_type = defines.build_check_type.manual
  if not character.surface.can_place_entity(placement) then return fail(npc, job, "placement_collision", tick) end
  placement.build_check_type = nil
  -- Damaged items go down too, as a player puts down a turret picked up after a fight: the building comes with the
  -- item's health, the undamaged ones used first. Undamaged only, the engineer counted a damaged turret as one it had
  -- and tried to build it 16 times (rehearsal 3, 2026-10-27).
  local inventory, slots,usable = main(npc), {},0
  for i = 1, #inventory do
    local stack = inventory[i]
    if stack.valid_for_read and stack.name == job.item and stack.quality.name == "normal" and stack.health > 0
        and (item.type~="item-with-entity-data" or Rail.plain_item_stack(stack)) then
      slots[#slots+1]=stack
      usable=usable+stack.count
    end
  end
  table.sort(slots, function(a, b) return a.health > b.health end)
  local needed=rail_item and rail_item.count or 1
  if usable<needed and craft_coming(npc, job.item) > 0 then return craft_wait(job) end   -- (still being crafted: it comes)
  if usable<needed then return fail(npc, job, "missing_healthy_normal_placement_item", tick) end
  -- Native rails can require multiple planner items. Debit the prototype's
  -- complete normal item cost only after all physical placement gates.
  local remaining,health,taken=needed,1,{}
  for _,slot in ipairs(slots) do
    local moved=math.min(slot.count,remaining)
    if moved>0 then
      health=math.min(health,slot.health)
      taken[#taken+1]={count=moved,health=slot.health}
    end
    slot.count=slot.count-moved
    remaining=remaining-moved;if remaining==0 then break end
  end
  placement.quality, placement.raise_built, placement.move_stuck_players = "normal", false, false
  placement.preserve_ghosts_and_corpses = true
  placement.mirror = job.mirror == true or nil   -- (built mirrored as planned: a refinery's inlets swap; DS-3)
  local ok, entity = pcall(function() return character.surface.create_entity(placement) end)
  -- (no ghost of its own left under what now stands: the engine aborts the game on one; W-35)
  if ok and entity then Ghosts.clear(entity.surface, entity.name, entity.position, entity.direction, entity.force) end
  if ok and entity then Worker.mirrored(entity, job.mirror) end
  if not ok or not entity then
    for _, t in ipairs(taken) do   -- (each back as it was)
      local restored = inventory.insert{name = job.item, count = t.count, quality = "normal", health = t.health}
      if restored < t.count then
        character.surface.spill_item_stack{position = character.position,
          stack = {name = job.item, count = t.count-restored, quality = "normal", health = t.health},
          enable_looted = false, allow_belts = false}
      end
    end
    return fail(npc, job, "entity_creation_failed_item_restored", tick)
  end
  if health < 1 then pcall(function() entity.health = entity.max_health * health end) end
  job.actual_count,job.placement_count = 1,needed
  local receipt = finish(npc, job, "complete", nil, tick,
    {entity = entity, entity_name = job.entity_name, position = entity.position, committed = true})
  -- The receipt precedes callbacks. Destruction by another mod is world loss, never a refund.
  if entity.valid then script.raise_script_built{entity = entity} end
  keep_loader_end(entity, job.belt_to_ground_type, placement.direction)
  if receipt then receipt.world_loss = not entity.valid end
end

-- One placement of a build run, under the same rules as build(): from reach, never where the engineer
-- stands, the item taken from its inventory only once the game agrees. The entity, or nil and why.
local function place_one(npc, spec)
  local item = prototypes.item[spec.item]
  local proto = prototypes.entity[spec.entity_name]
  if not (item and item.type == "item" and item.place_result and item.place_result.name == spec.entity_name
      and proto and BUILD_TYPES[proto.type]) then return nil, "unsupported" end
  local character = npc.character
  local placement = {name = spec.entity_name, position = spec.position,
    direction = spec.direction or defines.direction.north, type = spec.belt_to_ground_type}
  local box = rotated_build_box(proto, spec.position, placement.direction)
  if box and TeamWorld.active() then   -- (a batch of a plan, or turrets put down in a fight: its own ground only)
    local ok, why = TeamWorld.may_build(npc, {{box.x1, box.y1}, {box.x2, box.y2}})
    if not ok then return nil, "not_its_ground:" .. tostring(why) end
  end
  if box and character_overlaps_build(character, box) then return nil, "standing_there" end
  if not character.can_place_entity(placement) then return nil, "blocked" end
  placement.force = character.force
  placement.build_check_type = defines.build_check_type.manual
  if not character.surface.can_place_entity(placement) then return nil, "collision" end
  placement.build_check_type = nil
  local inventory, slot = main(npc), nil
  for i = 1, #inventory do   -- (the healthiest; a damaged one too, its health carried over: as build())
    local stack = inventory[i]
    if stack.valid_for_read and stack.name == spec.item and stack.quality.name == "normal" and stack.health > 0
        and (not slot or stack.health > slot.health) then
      slot = stack
    end
  end
  if not slot then return nil, "no_item" end
  local health = slot.health
  slot.count = slot.count - 1
  placement.quality, placement.raise_built, placement.move_stuck_players = "normal", false, false
  placement.preserve_ghosts_and_corpses = true
  placement.mirror = spec.mirror == true or nil
  local ok, entity = pcall(function() return character.surface.create_entity(placement) end)
  if ok and entity then Ghosts.clear(entity.surface, entity.name, entity.position, entity.direction, entity.force) end
  if ok and entity then Worker.mirrored(entity, spec.mirror) end
  if not ok or not entity then
    if inventory.insert{name = spec.item, count = 1, quality = "normal", health = health} < 1 then
      character.surface.spill_item_stack{position = character.position,
        stack = {name = spec.item, count = 1, quality = "normal", health = health}, enable_looted = false,
        allow_belts = false}
    end
    return nil, "creation_failed"
  end
  if health < 1 then pcall(function() entity.health = entity.max_health * health end) end
  if entity.valid then script.raise_script_built{entity = entity} end
  keep_loader_end(entity, spec.belt_to_ground_type, placement.direction)
  return entity
end

-- Put down in a fight (turrets it carries), by the same rules as any building.
Worker.place_one = place_one

-- A fast replace of its own building at the spot, if the game allows one there: true once done (the job finished) or
-- failed (the job failed); false when there is none to do.
function Worker.fast_replace(npc, job, placement, tick)
  local character = npc.character
  local surface = character.surface
  local spec = {name = placement.name, position = placement.position, direction = placement.direction,
    force = character.force}
  local ok, can = pcall(function() return surface.can_fast_replace(spec) end)
  if not (ok and can) then return false end
  -- (only over its own: a teammate's or a player's is theirs)
  local box = rotated_build_box(prototypes.entity[placement.name], placement.position, placement.direction)
  local p = placement.position
  local area = box and {{box.x1 + 0.1, box.y1 + 0.1}, {box.x2 - 0.1, box.y2 - 0.1}}
    or {{p.x - 0.4, p.y - 0.4}, {p.x + 0.4, p.y + 0.4}}
  local old = area and surface.find_entities_filtered{area = area, force = character.force, limit = 1}[1]
  if not (old and old.valid and old.type ~= "character") then return false end
  -- (in build reach, as the ordinary build walks to: not access reach, which may be shorter)
  if Navigation.distance(character.position, old.position) > character.build_distance then return false end
  local inventory = main(npc)
  local slot
  for i = 1, #inventory do
    local stack = inventory[i]
    if stack.valid_for_read and stack.name == job.item and stack.quality.name == "normal" and stack.health > 0
        and (not slot or stack.health > slot.health) then slot = stack end
  end
  if not slot then fail(npc, job, "missing_healthy_normal_placement_item", tick); return true end
  -- (room for the old one, never spilled: a free slot, the slot the new one leaves, or a stack the old one joins)
  local back = old.prototype and old.prototype.items_to_place_this and old.prototype.items_to_place_this[1]
  local back_name = back and (back.name or back[1])
  local room = inventory.count_empty_stacks(false, false) >= 1 or slot.count == 1
    or (back_name and inventory.can_insert{name = back_name, count = 1, quality = old.quality})
  if not room then fail(npc, job, "inventory_full", tick); return true end
  local health = slot.health
  slot.count = slot.count - 1
  local args = {name = placement.name, position = placement.position, direction = placement.direction,
    force = character.force, fast_replace = true, character = character, spill = true, raise_built = false,
    type = placement.type, quality = "normal", create_build_effect_smoke = false,
    mirror = job.mirror == true or nil}   -- (as planned: DS-3)
  local made_ok, entity = pcall(function() return surface.create_entity(args) end)
  if made_ok and entity then Ghosts.clear(entity.surface, entity.name, entity.position, entity.direction, entity.force) end
  if made_ok and entity then Worker.mirrored(entity, job.mirror) end
  if not (made_ok and entity) then
    -- (back in the slot it came from, which has room for it: never lost to a full inventory)
    if slot.valid_for_read then slot.count = slot.count + 1
    else slot.set_stack{name = job.item, count = 1, quality = "normal", health = health} end
    fail(npc, job, "fast_replace_failed", tick)
    return true
  end
  if health < 1 then pcall(function() entity.health = entity.max_health * health end) end
  job.actual_count, job.placement_count, job.fast_replaced = 1, 1, true
  local receipt = finish(npc, job, "complete", nil, tick,
    {entity = entity, entity_name = job.entity_name, position = entity.position, committed = true, fast_replaced = true})
  if entity.valid then script.raise_script_built{entity = entity} end
  keep_loader_end(entity, job.belt_to_ground_type, placement.direction)
  if receipt then receipt.world_loss = not entity.valid end
  return true
end

-- Water filled where a planned piece goes, as a player throws landfill: from build reach, one item a tile, only on its
-- own ground, only tiles that are still water (scripts/obstacles.lua, the ladder's first rung).
local function is_water(surface, p)
  local ok, hit = pcall(function() return surface.get_tile(p.x, p.y).collides_with("water_tile") end)
  return ok and hit
end
local function landfill(npc, job, tick)
  local character = npc.character
  local surface = character.surface
  local item = prototypes.item[job.item]
  local tile = item and item.place_as_tile_result and item.place_as_tile_result.result
  if not tile then return fail(npc, job, "not_a_landfill_item", tick) end
  local todo = {}
  for _, p in ipairs(job.positions) do if is_water(surface, p) then todo[#todo + 1] = p end end
  if #todo == 0 then
    job.actual_count = job.filled or 0
    return finish(npc, job, "complete", nil, tick)
  end
  local reach = character.build_distance - 0.5
  local near = todo[1]
  if Navigation.distance(character.position, near) > reach then
    if not navigate(npc, job, near, math.max(0.3, reach - 0.7), tick) then return end
  end
  job.state = "interacting"
  local placed = 0
  for _, p in ipairs(todo) do
    if Navigation.distance(character.position, p) <= reach then
      if TeamWorld.active() then
        local ok, why = TeamWorld.may_build(npc, {{p.x - 0.5, p.y - 0.5}, {p.x + 0.5, p.y + 0.5}})
        if not ok then return fail(npc, job, "not_its_ground:" .. tostring(why), tick) end
      end
      if count(npc, job.item) < 1 then return fail(npc, job, "missing_landfill", tick) end
      main(npc).remove{name = job.item, count = 1, quality = "normal"}
      surface.set_tiles({{name = tile.name, position = {x = math.floor(p.x), y = math.floor(p.y)}}}, true, false, true, true)
      placed = placed + 1
    end
  end
  job.filled = (job.filled or 0) + placed
  -- (none in reach after the walk would never end: given up)
  if placed == 0 then return fail(npc, job, "landfill_out_of_reach", tick) end
end

-- A build run: buildings placed one after another as the engineer walks past them, the way a player
-- drags out a belt or a row of poles: several a second, no stop between them. What cannot go down
-- now (blocked, or where it stands) is left for the single builder, which clears and steps aside.
local RUN_PER_TICK = 2
local function build_run(npc, job, tick)
  local character = npc.character
  local reach = character.build_distance - 0.5
  job.done, job.placed_list = job.done or {}, job.placed_list or {}
  local placed_now, target = 0, nil
  -- (every placement judged for danger, not the first alone: one in danger is left, and the run stops short of it;
  -- W-30, the engineer died at minute 1 inside a nest cluster's reach)
  -- (it stops short: the first piece in danger and every one after it in the run's walk are left, never walked
  -- through on the way to a safe one beyond; Codex, W-30)
  job.danger = job.danger or {}
  local short = false
  for i, spec in ipairs(job.placements) do
    if not job.done[i] and (short or (not brave(spec.entity_name)
        and Hands.danger_at(npc, job.danger, i, spec.position, tick))) then
      short = true
      job.done[i] = true
      job.in_danger = (job.in_danger or 0) + 1
    end
  end
  for i, spec in ipairs(job.placements) do
    if not job.done[i] then
      if Navigation.distance(character.position, spec.position) <= reach then
        if placed_now < RUN_PER_TICK then
          local entity, why = place_one(npc, spec)
          if entity then
            job.done[i] = true
            job.placed_list[#job.placed_list + 1] = {id = spec.id, entity = entity}
            placed_now = placed_now + 1
          elseif why ~= "standing_there" then
            job.done[i] = true   -- left for the single builder
          end
        end
      elseif not target then
        target = spec
      end
    end
  end
  if not target then
    for i, spec in ipairs(job.placements) do
      if not job.done[i] then
        -- only buildings where it stands are left, all in reach: step on to the next or give them up
        job.done[i] = true
      end
    end
    if placed_now > 0 then return end
    job.actual_count = #job.placed_list
    return finish(npc, job, "complete", nil, tick, {placed = job.placed_list})
  end
  if placed_now > 0 then job.state = "interacting" end
  -- (to a stand point chosen before the walk, clear of the run's pieces still to go up: Worker.stand_point, as build())
  if job.stand_for ~= target then
    job.stand_for = target
    local ok, at = pcall(function()
      local north = defines.direction.north
      local b = rotated_build_box(prototypes.entity[target.entity_name], target.position, target.direction or north)
      if not b then return nil end
      local avoid = {}
      for i, spec in ipairs(job.placements) do
        if not job.done[i] and spec ~= target then
          local o = rotated_build_box(prototypes.entity[spec.entity_name], spec.position, spec.direction or north)
          if o then avoid[#avoid + 1] = {{o.x1, o.y1}, {o.x2, o.y2}} end
        end
      end
      return Worker.stand_point(npc, {position = target.position, avoid = avoid}, b, tick)
    end)
    job.stand = ok and at or false
  end
  if job.stand then
    job.state = "navigating"
    local state = Navigation.step(npc, job, job.stand, 0.5, tick)
    -- (no way there, or there and still out of reach: as near as it gets, as before)
    if state == "failed" or state == "arrived" then job.stand = false end
    return
  end
  navigate(npc, job, target.position, math.max(0.3, reach - 0.5), tick)
end

-- One transfer, its outcome to `done(status, reason, extra)`: the job's own finish, or a sweep's tally (`stand`: it is
-- not walked to; out of reach it is out of reach).
local function transfer_core(npc, job, tick, done, stand)
  local function fail(_, _, reason, _, extra) return done("failed", reason, extra) end
  local function finish(_, _, status, reason, _, extra) return done(status, reason, extra) end
  local target = job.target
  if not valid_target(npc, target) then return fail(npc, job, "transfer_target_missing", tick) end
  if job.mode ~= "deposit" and job.mode ~= "withdraw" then return fail(npc, job, "invalid_transfer_mode", tick) end
  if not job.item or not prototypes.item[job.item] then return fail(npc, job, "invalid_transfer_item", tick) end
  if not authorized(npc, job, target) then return fail(npc, job, "inventory_access_denied", tick) end
  if not npc.character.can_reach_entity(target) then
    if stand then return fail(npc, job, "inventory_out_of_reach", tick) end
    local radius = math.max(0.3, npc.character.reach_distance - 0.75 - (job.approach_retry or 0))
    if not navigate(npc, job, target.position, radius, tick) then return end
    if not npc.character.can_reach_entity(target) then
      job.approach_retry = (job.approach_retry or 0) + 1
      if job.approach_retry >= 3 then return fail(npc, job, "inventory_out_of_reach", tick) end
      return
    end
  end
  job.state = "interacting"
  local target_inventory = job.inventory and target.get_inventory(job.inventory) or nil
  if not target_inventory and not job.inventory then
    if target.type == "character" then target_inventory=target.get_main_inventory()
    elseif target.type == "container" or target.type == "logistic-container" then target_inventory = target.get_inventory(defines.inventory.chest)
    elseif target.type=="ammo-turret" then target_inventory=target.get_inventory(defines.inventory.turret_ammo)
    elseif job.mode == "deposit" then target_inventory = target.get_fuel_inventory()
    else target_inventory = target.get_output_inventory() end
  end
  if not target_inventory or not target_inventory.valid then return fail(npc, job, "inventory_missing", tick) end
  local source, destination = main(npc), target_inventory
  if job.mode == "withdraw" then source, destination = destination, source end
  if source == destination then return fail(npc, job, "same_inventory", tick) end
  local available = 0
  -- Reject metadata-bearing/damaged supplies instead of flattening them into basic items.
  local prototype = prototypes.item[job.item]
  local science = prototype.type == "tool" and job.item:match("science%-pack$")~=nil
  local ammo = prototype.type=="ammo"
  local starter=prototype.type=="space-platform-starter-pack" and job.item=="space-platform-starter-pack"
  if prototype.type ~= "item" and not science and not ammo and not starter then return fail(npc, job, "unsupported_transfer_item", tick) end
  local function plain_stack(stack)
    return stack.valid_for_read and stack.name == job.item and stack.quality.name == "normal"
      and stack.health == 1 and (not science or stack.durability == 1)
  end
  local function transferable(stack)
    if not plain_stack(stack) then return 0 end
    -- A stack's first magazine may be partially spent. The remaining magazines
    -- are fresh. Leave that leading magazine and its exact ammo state in place.
    return stack.count-(ammo and stack.ammo<prototype.magazine_size and 1 or 0)
  end
  for i = 1, #source do
    local stack = source[i]
    available=available+transferable(stack)
  end
  local amount = math.min(job.count, available, destination.get_insertable_count(normal(job.item)))
  if available == 0 and job.mode == "deposit" and not stand and craft_coming(npc, job.item) > 0 then return craft_wait(job) end
  if amount <= 0 then return fail(npc, job, available == 0 and "source_empty" or "destination_full", tick) end
  local remaining = amount
  for i = 1, #source do
    local stack = source[i]
    if remaining > 0 and transferable(stack)>0 then
      local removed = math.min(transferable(stack), remaining)
      local prior_ammo=ammo and stack.ammo or nil
      stack.count = stack.count - removed
      -- Native count writes reset the surviving leading magazine to full.
      -- Restore its exact previous rounds; this preserves metadata and prevents
      -- the count change from inventing bullets (e.g. 3+60 must remain 63).
      if prior_ammo and stack.valid_for_read then stack.ammo=prior_ammo end
      remaining = remaining - removed
    end
  end
  local bind_platform=job.mode=="deposit" and job.item=="space-platform-starter-pack"
      and target.type=="rocket-silo" and job.inventory==defines.inventory.rocket_silo_rocket
  if bind_platform then PlatformLaunch.bind_payload(npc,target,amount) end
  local inserted = destination.insert{name = job.item, count = amount, quality = "normal"}
  if bind_platform then PlatformLaunch.confirm_payload(npc,target,inserted,job.id) end
  if inserted < amount then
    local restored = source.insert{name = job.item, count = amount - inserted, quality = "normal"}
    if restored ~= amount - inserted then
      npc.character.surface.spill_item_stack{position = npc.character.position,
        stack = {name = job.item, count = amount - inserted - restored, quality = "normal"}, allow_belts = false}
    end
  end
  job.actual_count = inserted
  -- (`up_to`: what is there, as from a machine its inserters empty too: a few gears fewer when the engineer got
  -- there failed the job, and the failures counted against its plan, run 48, 2026-10-26)
  if inserted < job.count and not (job.up_to and inserted > 0) then return finish(npc, job, "failed",
    "partial_transfer", tick, {target = target, mode = job.mode, requested = job.count}) end
  finish(npc, job, "complete", nil, tick, {target = target, mode = job.mode, requested = job.count})
end
local function transfer(npc, job, tick)
  return transfer_core(npc, job, tick, function(status, reason, extra)
    if status == "complete" then return finish(npc, job, status, reason, tick, extra) end
    return fail(npc, job, reason, tick, extra)
  end)
end

-- A sweep, as a player's ctrl-drag over machines (the user's even-distribution mod, 2026-10-28): one item from or into
-- every entity listed, SWEEP_TICKS apiece, from where it stands; it walks only to one out of its reach. What each one
-- gave or took is in the receipt (`moved`). Twenty fuel trips, one walk and one job each, become one job of seconds.
Worker.SWEEP_TICKS = 3
local function sweep(npc, job, tick)
  job.i, job.moved, job.actual_count = job.i or 1, job.moved or {}, job.actual_count or 0
  if job.next_at and tick < job.next_at then return end
  local e = job.entries[job.i]
  if not e then
    if job.actual_count > 0 then return finish(npc, job, "complete", nil, tick, {moved = job.moved}) end
    return fail(npc, job, job.last_reason or "sweep_moved_nothing", tick, {moved = job.moved})
  end
  if not valid_target(npc, e.target) then job.i = job.i + 1; return end
  local c = npc.character
  if not c.can_reach_entity(e.target) then
    job.state = "navigating"
    local state = Navigation.step(npc, job, e.target.position, math.max(0.3, c.reach_distance - 0.75), tick)
    if state == "failed" then
      stop(npc, job)
      job.last_reason, job.i = "inventory_out_of_reach", job.i + 1   -- (that one is left; the rest go on)
    elseif state == "arrived" and not c.can_reach_entity(e.target) then
      job.last_reason, job.i = "inventory_out_of_reach", job.i + 1
    end
    return
  end
  Navigation.stop(c)
  job.state = "interacting"
  local one = {kind = "transfer", id = job.id, target = e.target, item = job.item, count = e.count, mode = job.mode,
    inventory = e.inventory, up_to = true, owner_force = job.owner_force}
  transfer_core(npc, one, tick, function(status, reason)
    if status ~= "complete" then job.last_reason = reason end
  end, true)
  local n = one.actual_count or 0
  if n > 0 then
    job.actual_count = job.actual_count + n
    job.moved[#job.moved + 1] = {unit_number = e.target.unit_number, type = e.target.type, count = n}
  end
  job.i, job.next_at = job.i + 1, tick + Worker.SWEEP_TICKS
end

local function configure_recipe(npc, job, tick)
  local target = job.target
  if not valid_target(npc, target) or target.type ~= "assembling-machine"
      or not same_force(npc.character.force, target.force) then
    return fail(npc, job, "recipe_configuration_requires_owned_assembler", tick)
  end
  local recipe = npc.character.force.recipes[job.recipe or ""]
  if not recipe or not recipe.enabled then return fail(npc, job, "recipe_locked_or_missing", tick) end
  if job.direction~=nil and job.direction~=defines.direction.north
      and job.direction~=defines.direction.east and job.direction~=defines.direction.south
      and job.direction~=defines.direction.west then
    return fail(npc,job,"invalid_assembler_direction",tick)
  end
  if not npc.character.can_reach_entity(target) then
    if not navigate(npc, job, target.position, math.max(0.3, npc.character.reach_distance - 0.75), tick) then return end
    if not npc.character.can_reach_entity(target) then return fail(npc, job, "configuration_out_of_reach", tick) end
  end
  local existing = target.get_recipe()
  if existing and existing.name == recipe.name
      and (job.direction==nil or target.direction==job.direction) then
    return finish(npc, job, "complete", nil, tick, {configured_recipe=recipe.name, target=target})
  end
  -- Initial configuration is supported without discarding a previous recipe's
  -- items, fluids or partial work. Changing a populated assembler needs a later
  -- explicit drain-and-reconfigure procedure.
  local input, output = target.get_inventory(defines.inventory.crafter_input), target.get_output_inventory()
  if (input and not input.is_empty()) or (output and not output.is_empty())
      or target.crafting_progress > 0 then return fail(npc, job, "recipe_change_requires_empty_machine", tick) end
  target.set_recipe(recipe.name)
  local applied = target.get_recipe()
  if not applied or applied.name ~= recipe.name then return fail(npc, job, "recipe_configuration_not_applied", tick) end
  -- Fluid recipes can normalize the assembler's orientation during set_recipe.
  -- Restore the authored port direction only after native recipe selection.
  if job.direction~=nil then target.direction=job.direction end
  if job.direction~=nil and target.direction~=job.direction then
    return fail(npc,job,"assembler_direction_not_applied",tick)
  end
  finish(npc, job, "complete", nil, tick, {configured_recipe=recipe.name,
    configured_direction=target.direction,target=target})
end

local function configure_direction(npc,job,tick)
  local target=job.target
  if not valid_target(npc,target) or not same_force(npc.character.force,target.force)
      or (target.type~="inserter" and target.type~="transport-belt") then
    return fail(npc,job,"direction_configuration_requires_owned_belt_or_inserter",tick)
  end
  if job.direction~=defines.direction.north and job.direction~=defines.direction.east
      and job.direction~=defines.direction.south and job.direction~=defines.direction.west then
    return fail(npc,job,"invalid_direction",tick)
  end
  if not npc.character.can_reach_entity(target) then
    if not navigate(npc,job,target.position,math.max(.3,npc.character.reach_distance-.75),tick) then return end
    if not npc.character.can_reach_entity(target) then return fail(npc,job,"configuration_out_of_reach",tick) end
  end
  local previous=target.direction
  target.direction=job.direction
  if target.direction~=job.direction then return fail(npc,job,"direction_not_applied",tick) end
  finish(npc,job,"complete",nil,tick,{target=target,previous_direction=previous,configured_direction=target.direction})
end

local BELT_PICKUP_ITEMS={['automation-science-pack']=true,['logistic-science-pack']=true,
  ['chemical-science-pack']=true,['engine-unit']=true,['iron-plate']=true,['copper-plate']=true,
  ['steel-plate']=true,['iron-ore']=true,['copper-ore']=true,coal=true,stone=true,
  ['iron-gear-wheel']=true,['electronic-circuit']=true,['advanced-circuit']=true,
  ['plastic-bar']=true,sulfur=true,pipe=true,['transport-belt']=true}
-- Items picked off one of the engineer's belts, as a player picks them up by hand. `line` (1 left, 2 right) takes
-- from that lane only; `up_to` takes what is there and fits (items move on while the engineer walks up: a
-- clean-up takes what it finds, and taking none is no failure); `sweep`, more of its belts round there, those within
-- its reach (gears grabbed off a line for hand crafting: a belt holds eight at most).
local function belt_pickup(npc,job,tick)
  local target=job.target
  local proto=job.item and prototypes.item[job.item]
  -- (any plain stackable item, as its callers ask: Hands.belt_pickable; a defense row's magazines were refused as not
  -- plain again and again, W-25)
  local plain=BELT_PICKUP_ITEMS[job.item] or (proto~=nil and Hands.belt_pickable(job.item))
  if not valid_target(npc,target) or target.type~="transport-belt"
      or not same_force(npc.character.force,target.force) or not plain
      or (job.line and job.line~=1 and job.line~=2) then
    return fail(npc,job,"belt_pickup_requires_owned_belt_and_plain_item",tick)
  end
  if not npc.character.can_reach_entity(target) then
    if not navigate(npc,job,target.position,math.max(.3,npc.character.reach_distance-.75),tick) then return end
    if not npc.character.can_reach_entity(target) then return fail(npc,job,"belt_pickup_out_of_reach",tick) end
  end
  local destination=main(npc)
  local room=destination.get_insertable_count(normal(job.item))
  if room<(job.up_to and 1 or job.count) then return fail(npc,job,"destination_full",tick) end
  -- (each line once: belts side by side share a line under different objects, counted twice it asked for more than
  -- stood there; Codex, W-25)
  local lines={}
  local function add(line)
    for _,l in ipairs(lines) do
      if l==line then return end
      local ok,same=pcall(function() return l.line_equals(line) end)
      if ok and same then return end
    end
    lines[#lines+1]=line
  end
  if job.line then add(target.get_transport_line(job.line))
  else add(target.get_transport_line(1)); add(target.get_transport_line(2)) end
  for _,b in ipairs(type(job.sweep)=="table" and job.sweep or {}) do
    if b~=target and b.valid and b.type=="transport-belt" and same_force(npc.character.force,b.force)
        and npc.character.can_reach_entity(b) then
      add(b.get_transport_line(1)); add(b.get_transport_line(2))
    end
  end
  local available=0
  for _,line in ipairs(lines) do available=available+line.get_item_count(normal(job.item)) end
  if job.up_to then job.count=math.min(job.count,room,available) end
  if job.count<=0 then
    job.actual_count=0
    return finish(npc,job,"complete",nil,tick,{target=target,removed_from_belt=0,mode="withdraw"})
  end
  if available<job.count then return fail(npc,job,"belt_item_shortfall",tick) end
  -- (stack by stack with each one's own state, and only what went into its pockets leaves the belt: a fresh stack
  -- put a part-used magazine back full, a damaged turret whole; Codex, W-25)
  local inserted=Hands.take_from_lines(lines,job.item,job.count,destination)
  local removed=inserted
  job.actual_count=inserted
  if inserted~=job.count then return fail(npc,job,"partial_belt_pickup",tick) end
  finish(npc,job,"complete",nil,tick,{target=target,removed_from_belt=removed,mode="withdraw"})
end

local function select_research(npc, job, tick)
  local force = npc.character.force
  local technology = force.technologies[job.technology or ""]
  if not technology or not technology.enabled then return fail(npc, job, "technology_locked_or_missing", tick) end
  if technology.researched then return finish(npc, job, "complete", nil, tick, {technology=technology.name, already_researched=true}) end
  for _, prerequisite in pairs(technology.prerequisites) do
    if not prerequisite.researched then return fail(npc, job, "technology_prerequisite_missing", tick) end
  end
  if TeamWorld.active() then
    -- One tech tree: the pick goes at the end of the team's queue, which the players run. Not taken
    -- (a player took it out lately, or enough picks are waiting) is no failure of the engineer's.
    local queued, why = TeamResearch.request(npc, technology.name, tick, job.need)
    return finish(npc, job, "complete", nil, tick, {technology=technology.name, research_queued=queued == true,
      not_queued=why})
  end
  if not (force.current_research and force.current_research.name == technology.name)
      and not force.add_research(technology.name) then return fail(npc, job, "research_not_available", tick) end
  -- (ahead of a current research its packs made by nothing, which holds its labs still: its own force's queue; R-9)
  if job.ahead and force.current_research and force.current_research.name ~= technology.name then
    local order = {technology.name}
    for _, t in ipairs(force.research_queue or {}) do
      if t.name ~= technology.name then order[#order + 1] = t.name end
    end
    force.research_queue = order
  end
  -- This selects the same research a player can select in the research UI.
  -- Progress still requires actual powered labs consuming physical science.
  finish(npc, job, "complete", nil, tick, {technology=technology.name, research_selected=true})
end

-- Its hurt buildings mended with its repair packs (W-33: scripts/mend.lua), walking to each in turn; it gives way to
-- anything urgent, to biters near, or to a stage of its plan going up.
local function mend(npc, job, tick)
  local walked = false
  local state, e = Mend.step(npc, job, tick, function(position, radius)
    walked = true
    return navigate(npc, job, position, radius, tick)
  end)
  if job.terminal then return end   -- (its walk failed)
  if state == "walk" then return end
  if not walked and job.state == "navigating" then
    Navigation.clear(npc, job); Navigation.stop(npc.character)
  end
  local extra = {mended=math.floor(job.mended or 0), mend_target=e and e.valid and e.unit_number or nil,
    mended_by_kind=job.by_kind, first_kind=job.first_kind}
  if state == "mend" then job.state = "mending"
  elseif state == "complete" then finish(npc, job, "complete", nil, tick, extra)
  elseif state == "yielded" then finish(npc, job, "canceled", "yielded", tick, extra)
  elseif state == "in_danger" then fail(npc, job, "in_danger", tick, extra)
  else
    -- (out of packs with some mended: done as far as they went)
    if (job.mended or 0) > 0 then finish(npc, job, "complete", nil, tick, extra)
    else fail(npc, job, state, tick, extra) end
  end
end

-- Its own buildings picked up where they stand, from its reach (lesson 17: Hands.pickup_step).
local function pickup(npc, job, tick)
  local walked = false
  local state = Hands.pickup_step(npc, job, tick, function(position, radius)
    walked = true
    return navigate(npc, job, position, radius, tick)
  end, brave)
  if job.terminal then return end   -- (its walk failed)
  if state == "walk" then return end
  if not walked and job.state == "navigating" then
    Navigation.clear(npc, job); Navigation.stop(npc.character)
  end
  local extra = {picked = job.picked or 0, passed = job.passed or 0, pickup_count = #(job.targets or {})}
  if state == "picked" then job.state = "picking"
  elseif state == "complete" then finish(npc, job, "complete", nil, tick, extra)
  else fail(npc, job, state, tick, extra) end
end

local function run(npc, job, tick)
  if job.kind == "move" then
    local position = job.position or (valid_target(npc, job.target) and job.target.position)
    if not position then return fail(npc, job, "move_target_missing", tick) end
    -- (a walk on the way to a build, never into its danger: a project's staging point beside its site; W-30. Not a
    -- combat or help move, which goes to a fight on purpose)
    job.danger = job.danger or {}
    if job.avoid_danger and Hands.danger_at(npc, job.danger, 1, position, tick) then
      return fail(npc, job, "in_danger", tick)
    end
    if navigate(npc, job, position, job.target and 1 or 0.35, tick) then
      finish(npc, job, "complete", nil, tick, {position = npc.character.position})
    end
  elseif job.kind == "mine" then mine(npc, job, tick)
  elseif job.kind == "craft" then craft(npc, job, tick)
  elseif job.kind == "build" then build(npc, job, tick)
  elseif job.kind == "build_run" then build_run(npc, job, tick)
  elseif job.kind == "landfill" then landfill(npc, job, tick)
  elseif job.kind == "sweep" then sweep(npc, job, tick)
  elseif job.kind == "transfer" or job.kind=="theft" or job.kind=="sabotage" then transfer(npc, job, tick)
  elseif job.kind == "configure_recipe" then configure_recipe(npc, job, tick)
  elseif job.kind == "configure_direction" then configure_direction(npc,job,tick)
  elseif job.kind == "belt_pickup" then belt_pickup(npc,job,tick)
  elseif job.kind == "mend" then mend(npc, job, tick)
  elseif job.kind == "pickup" then pickup(npc, job, tick)
  elseif job.kind == "player_trade" then
    if not valid_target(npc,job.target) then return fail(npc,job,"trade_target_missing",tick) end
    if not npc.character.can_reach_entity(job.target) then
      if not navigate(npc,job,job.target.position,math.max(.3,npc.character.reach_distance-.75),tick) then return end
    end
    local okay,result=PlayerTrade.execute(npc,job)
    if not okay then return fail(npc,job,result,tick) end
    finish(npc,job,"complete",nil,tick,result)
  elseif job.kind=="configure_train_stop" then
    if not valid_target(npc,job.target) then return fail(npc,job,"rail_stop_missing",tick) end
    if not npc.character.can_reach_entity(job.target) then
      if not navigate(npc,job,job.target.position,math.max(.3,npc.character.reach_distance-.75),tick) then return end
    end
    local okay,detail=TrainJobs.configure(npc,job)
    if not okay then return fail(npc,job,detail,tick) end
    finish(npc,job,"complete",nil,tick,detail)
  elseif job.kind=="rail_move" then
    if not valid_target(npc,job.target) then return fail(npc,job,"rail_wagon_missing",tick) end
    if not job.journey_started then
      local goal,reason=TrainJobs.dispatch_goal(npc,job)
      if not goal then return fail(npc,job,reason,tick) end
      if not navigate(npc,job,goal,.35,tick) then return end
      Navigation.clear(npc,job)
    end
    local state,detail=TrainJobs.step(npc,job,tick)
    if state=="failed" then return fail(npc,job,detail,tick) end
    if state=="complete" then finish(npc,job,"complete",nil,tick) end
  elseif job.kind == "select_research" then select_research(npc, job, tick)
  elseif job.kind=="request_platform" then
    local ok,result=PlatformLaunch.request(npc,job.platform_name)
    if not ok then return fail(npc,job,result,tick) end
    finish(npc,job,"complete",nil,tick,{platform_index=result.index,pending_shell=true})
  elseif job.kind=="launch_platform" then
    local target=job.target
    if not valid_target(npc,target) then return fail(npc,job,"launch_target_missing",tick) end
    if not npc.character.can_reach_entity(target) then
      if not navigate(npc,job,target.position,math.max(.3,npc.character.reach_distance-.75),tick) then return end
    end
    local ok,reason=PlatformLaunch.launch(npc,target)
    if not ok then return fail(npc,job,reason,tick) end
    finish(npc,job,"complete",nil,tick,{native_launch_ordered=true})
  else fail(npc, job, "unknown_job_kind", tick) end
end

-- The background crafts done since the last look (the queue holds less of their recipe than is still owed, the
-- oldest first), credited to the production statistics as an owned craft's are (scripts/production_credit.lua).
function Worker.credit_background(npc)
  local owed = npc.background_queued
  if not owed or #owed == 0 then return end
  local character = npc.character
  local outstanding = {}
  for _, r in ipairs(owed) do outstanding[r.recipe] = (outstanding[r.recipe] or 0) + r.count - r.done end
  local statistics
  for recipe, left in pairs(outstanding) do
    local finished = left - queue_count(character, recipe)
    for _, r in ipairs(owed) do
      if finished <= 0 then break end
      if r.recipe == recipe and r.done < r.count then
        local n = math.min(finished, r.count - r.done)
        r.done, finished = r.done + n, finished - n
        statistics = statistics or character.force.get_item_production_statistics(character.surface)
        for name, amount in pairs(r.made) do
          pcall(function() statistics.on_flow({name = name, quality = "normal"}, amount * n) end)
        end
      end
    end
  end
  for i = #owed, 1, -1 do if owed[i].done >= owed[i].count then table.remove(owed, i) end end
end

function Worker.tick(npc, tick)
  if not npc.character or not npc.character.valid then
    npc.background_queued = nil   -- (its queue went with it: nothing more of it will be made)
    if npc.current_job then fail(npc, npc.current_job, "body_missing", tick) end
    return
  end
  if tick % 30 == 0 then Worker.credit_background(npc) end
  if Reach.keep then Reach.keep(npc, tick) end   -- (far-reach's bonuses on its body, as on a player's: reach.lua)
  if Boosts.keep then Boosts.keep(npc, tick) end   -- (the host's boosts and the quick start: boosts.lua)
  -- Initializes identity and physical speed for saves created before variation.
  Behavior.ensure(npc)
  if not npc.current_job then
    npc.jobs = npc.jobs or {}
    if #npc.jobs == 0 then Navigation.stop(npc.character); return end
    npc.current_job = table.remove(npc.jobs, 1)
    npc.current_job.started_tick, npc.current_job.state = tick, "acquiring"
    if npc.current_job.kind=="craft" then
      npc.current_job.requested_deadline=npc.current_job.deadline
      npc.current_job.deadline=math.min(npc.current_job.deadline or tick+36000,tick+360000)
    else
      npc.current_job.deadline = math.min(npc.current_job.deadline or tick + 36000, tick + 36000)
    end
  end
  local job = npc.current_job
  if job.kind=="craft" and job.craft_started and not job.craft_timeout_set then
    extend_native_craft_deadline(npc.character,job,tick)
  end
  if tick >= job.deadline then return fail(npc, job, "job_deadline", tick) end
  local trace
  local ok, error_message = xpcall(run, function(message)
    trace = debug and debug.traceback and debug.traceback(tostring(message), 2) or tostring(message)
    return message
  end, npc, job, tick)
  if not ok and not job.terminal then
    -- Where it went wrong, once per kind of error (the job just fails; the log says why).
    storage.executor_traces = storage.executor_traces or {}
    local key = job.kind .. ":" .. tostring(error_message):sub(1, 80)
    if not storage.executor_traces[key] then
      storage.executor_traces[key] = true
      log("[npc-factions] npc=" .. tostring(npc.id) .. " " .. job.kind .. " job error: " .. tostring(trace))
    end
    fail(npc, job, "executor_error: " .. tostring(error_message), tick)
  end
end

return Worker
