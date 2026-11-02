-- Private test setup may grant/clear explicitly recorded items and create blockers.
-- These fixtures do not claim a normal-economy bootstrap or production result.
local Worker = require("scripts.worker")
local Guards = {}
local CASES = {"natural_furnace_craft", "cancel_craft_refund_once", "full_inventory_mining",
  "foreign_inventory_denied", "partial_transfer_conserved", "blocked_build_item_preserved",
  "stale_path_callback_ignored"}

local function inventory(npc) return npc.character.get_main_inventory() end
local function items(npc, name) return inventory(npc).get_item_count{name = name, quality = "normal"} end
local function enqueue(npc, job)
  local ok, reason = Worker.enqueue(npc, job)
  if not ok then error("fixture enqueue failed: " .. tostring(reason)) end
end
local function record_grant(t, name, count, location)
  t.grants[#t.grants + 1] = {stage = t.stage, item = name, count = count, location = location or "character"}
end
local function grant(t, npc, name, n)
  local inserted = inventory(npc).insert{name = name, count = n, quality = "normal"}
  if inserted ~= n then error("guard fixture grant did not fit") end
  record_grant(t, name, n)
end
local function reset(t, npc)
  if npc.current_job then Worker.cancel(npc, "fixture_case_cleanup") end
  npc.jobs = {}
  -- A case boundary explicitly discards the preceding case's test supplies.
  local removed = {}
  local inv = inventory(npc)
  for i = 1, #inv do
    if inv[i].valid_for_read then removed[inv[i].name] = (removed[inv[i].name] or 0) + inv[i].count end
  end
  t.discards[#t.discards + 1] = {stage = t.stage, items = removed}
  inv.clear()
end
local function receipt(npc, id) return npc.receipts[id] end
local function check(t, passed, detail)
  t.checks[CASES[t.stage]] = {passed = not not passed, detail = detail}
  t.executed = t.executed + 1
  t.stage = t.stage + 1
  t.active, t.case_state = false, nil
end

local function snapshot_state(t, npc, tick)
  local status = {}
  for _, id in ipairs(npc.receipt_order) do
    local r = npc.receipts[id]
    status[#status + 1] = {job_id = r.job_id, kind = r.kind, status = r.status,
      reason = r.reason, count = r.count, completed = r.completed,
      consumed = r.consumed, produced = r.produced, refunded = r.refunded}
  end
  return {fixture_setup = true, ordinary_economy_claim = false, executed = t.executed,
    stage = t.stage, relative_tick = tick - t.start_tick, grants = t.grants, discards = t.discards,
    position = {x = npc.character.position.x, y = npc.character.position.y},
    inventory = {stone = items(npc, "stone"), coal = items(npc, "coal"),
      furnace = items(npc, "stone-furnace")}, receipts = status,
    current_job = npc.current_job and npc.current_job.id or nil,
    pending_paths = table_size(storage.path_requests)}
end

function Guards.snapshot()
  local t = storage.guards
  if not t then return {schema = 1, mode = "guards", tick = game.tick, phase = "not-started",
    completed = false, checks = {}, state = {}, fingerprint = ""} end
  if t.result then return t.result end
  local npc = storage.npcs[1]
  local state = snapshot_state(t, npc, game.tick)
  return {schema = 1, mode = "guards", tick = game.tick, phase = CASES[t.stage] or "finishing",
    completed = false, checks = t.checks, state = state, fingerprint = helpers.table_to_json(state)}
end

local function terminal(t, npc, tick, reason)
  if t.result then return end
  if npc.current_job then Worker.cancel(npc, "fixture_terminal_cleanup") end
  npc.jobs = {}
  local passed = t.executed == #CASES and not reason
  for _, name in ipairs(CASES) do if not t.checks[name].passed then passed = false end end
  local state = snapshot_state(t, npc, tick)
  state.terminal_reason = reason
  t.result = {schema = 1, mode = "guards", tick = tick,
    phase = passed and "complete" or "failed", completed = passed,
    checks = t.checks, state = state, fingerprint = helpers.table_to_json(state)}
  storage.test.result = t.result
end

function Guards.start()
  if storage.test or next(storage.npcs) then return false, "guards require a fresh world without NPCs" end
  local surface = game.surfaces[1]
  surface.request_to_generate_chunks({0, 0}, 2)
  surface.force_generate_chunk_requests()
  local area = {{-24, -12}, {48, 12}}
  for _, entity in ipairs(surface.find_entities_filtered{area = area}) do
    if entity.type ~= "character" then entity.destroy() end
  end
  local tiles = {}
  for x = -24, 48 do for y = -12, 12 do tiles[#tiles + 1] = {name = "grass-1", position = {x, y}} end end
  surface.set_tiles(tiles)
  local force = game.forces["npc-guard-fixture"] or game.create_force("npc-guard-fixture")
  local foreign = game.forces["npc-guard-foreign"] or game.create_force("npc-guard-foreign")
  local position = surface.find_non_colliding_position("character", {0, 0}, 8, 0.5)
  if not position then return false, "guard character has no spawn position" end
  local character = surface.create_entity{name = "character", position = position, force = force}
  if not character then return false, "guard character creation failed" end
  local npc = {id = 1, character = character, force = force, home = {x = 0, y = 0},
    jobs = {}, current_job = nil, receipts = {}, receipt_order = {}, completed_jobs = {},
    failures = {}, transfer_permissions = {}, suspended = true, phase = "guard-fixture",
    travelled = 0, last_position = {x = position.x, y = position.y}}
  storage.npcs[1] = npc
  storage.path_requests = storage.path_requests or {}
  storage.test = {mode = "guards", start_tick = game.tick, terrain_fixture = true}
  storage.guards = {stage = 1, active = false, executed = 0, checks = {},
    start_tick = game.tick, deadline = game.tick + 3000, grants = {}, discards = {}, foreign_force = foreign.name}
  for _, name in ipairs(CASES) do storage.guards.checks[name] = {passed = false, detail = "not run"} end
  return true
end

local function begin_case(t, npc, tick)
  reset(t, npc)
  t.active, t.case_deadline = true, tick + 600
  local s = t.stage
  if s == 1 then
    grant(t, npc, "stone", 5)
    enqueue(npc, {id = "guard-craft", kind = "craft", recipe = "stone-furnace", count = 1})
  elseif s == 2 then
    grant(t, npc, "stone", 5)
    enqueue(npc, {id = "guard-cancel-craft", kind = "craft", recipe = "stone-furnace", count = 1})
  elseif s == 3 then
    local inv = inventory(npc)
    -- One stone per occupied slot prevents any coal insertion with only 80 test items.
    for i = 1, #inv do
      if not inv[i].set_stack{name = "stone", count = 1, quality = "normal"} then error("fixture slot setup failed") end
    end
    record_grant(t, "stone", #inv)
    t.full_slots, t.full_coal_capacity = #inv, inv.get_insertable_count{name = "coal", quality = "normal"}
    t.ore = npc.character.surface.create_entity{name = "coal", position = {1.5, 0.5}, amount = 100}
    if not t.ore then error("fixture coal setup failed") end
    t.ore_initial = t.ore.amount
    enqueue(npc, {id = "guard-full-mine", kind = "mine", item = "coal", target = t.ore, count = 1})
  elseif s == 4 then
    grant(t, npc, "stone", 10)
    t.chest = npc.character.surface.create_entity{name = "wooden-chest", position = {3.5, 0.5}, force = t.foreign_force}
    if not t.chest then error("fixture foreign chest setup failed") end
    enqueue(npc, {id = "guard-foreign", kind = "transfer", target = t.chest,
      item = "stone", count = 5, mode = "deposit", inventory = defines.inventory.chest})
  elseif s == 5 then
    grant(t, npc, "stone", 10)
    local inv = t.chest.get_inventory(defines.inventory.chest)
    inv.set_bar(2) -- one usable slot
    local supplied = prototypes.item.stone.stack_size - 2
    if inv.insert{name = "stone", count = supplied, quality = "normal"} ~= supplied then error("fixture constrained chest setup failed") end
    record_grant(t, "stone", supplied, "foreign-chest")
    t.transfer_total = items(npc, "stone") + inv.get_item_count("stone")
    t.transfer_capacity = inv.get_insertable_count{name = "stone", quality = "normal"}
    npc.transfer_permissions[t.foreign_force] = {deposit = true, withdraw = false}
    enqueue(npc, {id = "guard-partial", kind = "transfer", target = t.chest,
      item = "stone", count = 5, mode = "deposit", inventory = defines.inventory.chest})
  elseif s == 6 then
    grant(t, npc, "stone-furnace", 1)
    t.block_position = {x = 6, y = 0}
    t.blocker = npc.character.surface.create_entity{name = "big-rock", position = t.block_position}
    if not t.blocker then error("fixture blocker setup failed") end
    enqueue(npc, {id = "guard-blocked-build", kind = "build", item = "stone-furnace",
      entity_name = "stone-furnace", position = t.block_position})
  elseif s == 7 then
    t.case_state = "waiting_old_request"
    enqueue(npc, {id = "guard-old-path", kind = "move", position = {40, 4}})
  end
end

local function advance(t, npc, tick)
  local s = t.stage
  if s == 1 then
    local r = receipt(npc, "guard-craft")
    if not r then return end
    local minimum_ticks = math.max(1, math.floor(npc.force.recipes["stone-furnace"].energy * 60) - 1)
    check(t, r.status == "complete" and r.completed == 1 and (r.consumed.stone or 0) == 5
      and (r.produced["stone-furnace"] or 0) == 1 and items(npc, "stone") == 0
      and items(npc, "stone-furnace") == 1 and r.tick - r.started_tick >= minimum_ticks,
      {status = r.status, reason = r.reason, consumed = r.consumed, produced = r.produced,
        elapsed_ticks = r.tick - r.started_tick, minimum_ticks = minimum_ticks,
        native_queue = npc.character.crafting_queue_size})
  elseif s == 2 then
    local job = npc.current_job
    if not t.cancel_tick and job and job.id == "guard-cancel-craft" and job.craft_started
        and tick - job.craft_start_tick >= 5 then
      t.canceled_progress = npc.character.crafting_queue_progress
      t.cancel_tick = tick
      local before = #npc.receipt_order
      Worker.cancel(npc, "guard_midcraft_cancel")
      Worker.cancel(npc, "guard_duplicate_cancel")
      t.cancel_receipts = #npc.receipt_order - before
      return -- verify refund remains stable over subsequent native simulation ticks
    end
    if not t.cancel_tick or tick - t.cancel_tick < 60 then return end
    local r = receipt(npc, "guard-cancel-craft")
    check(t, r and r.status == "canceled" and r.completed == 0 and (r.produced["stone-furnace"] or 0) == 0
      and (r.refunded.stone or 0) == 5 and items(npc, "stone") == 5 and items(npc, "stone-furnace") == 0
      and npc.character.crafting_queue_size == 0 and t.cancel_receipts == 1
      and t.canceled_progress > 0 and t.canceled_progress < 1,
      {status = r and r.status, refunded = r and r.refunded, stone = items(npc, "stone"),
        furnace = items(npc, "stone-furnace"), receipts_added = t.cancel_receipts,
        queue = npc.character.crafting_queue_size, progress_at_cancel = t.canceled_progress})
  elseif s == 3 then
    local r = receipt(npc, "guard-full-mine")
    if not r then return end
    check(t, t.full_slots == 80 and items(npc, "stone") == 80 and t.full_coal_capacity == 0
      and r.status == "failed" and r.reason == "inventory_full" and r.count == 0
      and t.ore.valid and t.ore.amount == t.ore_initial and items(npc, "coal") == 0,
      {status = r.status, reason = r.reason, count = r.count, coal = items(npc, "coal"),
        slots = t.full_slots, occupied_stone_slots = items(npc, "stone"), capacity_for_coal = t.full_coal_capacity,
        resource_before = t.ore_initial, resource_after = t.ore.valid and t.ore.amount or 0})
  elseif s == 4 then
    local r = receipt(npc, "guard-foreign")
    if not r then return end
    local remaining = t.chest.get_inventory(defines.inventory.chest).get_item_count("stone")
    check(t, r.status == "failed" and r.reason == "inventory_access_denied"
      and items(npc, "stone") == 10 and remaining == 0 and r.count == 0,
      {status = r.status, reason = r.reason, source = items(npc, "stone"), destination = remaining})
  elseif s == 5 then
    local r = receipt(npc, "guard-partial")
    if not r then return end
    local destination = t.chest.get_inventory(defines.inventory.chest).get_item_count("stone")
    local source = items(npc, "stone")
    check(t, t.transfer_capacity == 2 and r.status == "failed" and r.reason == "partial_transfer"
      and r.count == 2 and source == 8 and source + destination == t.transfer_total
      and (r.consumed.stone or 0) == 2 and (r.produced.stone or 0) == 2,
      {status = r.status, reason = r.reason, requested = 5, moved = r.count,
        source = source, destination = destination, before_total = t.transfer_total,
        after_total = source + destination, capacity = t.transfer_capacity})
  elseif s == 6 then
    local r = receipt(npc, "guard-blocked-build")
    if not r then return end
    local entities = npc.character.surface.count_entities_filtered{name = "stone-furnace",
      position = t.block_position, radius = 1}
    check(t, r.status == "failed" and (r.reason == "placement_blocked_or_out_of_reach" or r.reason == "placement_collision")
      and items(npc, "stone-furnace") == 1 and (r.consumed["stone-furnace"] or 0) == 0
      and not r.committed and t.blocker.valid and entities == 0,
      {status = r.status, reason = r.reason, furnace_item = items(npc, "stone-furnace"),
        placed_furnaces = entities, consumed = r.consumed})
  elseif s == 7 then
    local job = npc.current_job
    local nav = job and job.navigation
    if t.case_state == "waiting_old_request" and job and job.id == "guard-old-path" and nav and nav.request_id then
      t.old_request = nav.request_id
      t.old_request_registered = storage.path_requests[t.old_request] ~= nil
      Worker.cancel(npc, "guard_path_cancel")
      enqueue(npc, {id = "guard-successor-path", kind = "move", position = {-16, 4}})
      t.case_state = "waiting_new_request"
    elseif t.case_state == "waiting_new_request" and job and job.id == "guard-successor-path" and nav and nav.request_id then
      local new_id, old_receipt_count = nav.request_id, #npc.receipt_order
      -- Re-deliver the obsolete real request's ID with a valid-looking result.
      Worker.on_path_finished{id = t.old_request, tick = tick, path = {{position = {40, 4}}}}
      local unchanged = npc.current_job == job and job.navigation == nav and nav.request_id == new_id
      local no_obsolete_map = storage.path_requests[t.old_request] == nil
      local canceled = receipt(npc, "guard-old-path")
      local added = #npc.receipt_order - old_receipt_count
      Worker.cancel(npc, "guard_successor_cleanup")
      check(t, t.old_request_registered and new_id ~= t.old_request and unchanged and no_obsolete_map
        and canceled and canceled.status == "canceled" and added == 0,
        {old_request_registered = t.old_request_registered, distinct_successor_request = new_id ~= t.old_request,
          successor_unchanged = unchanged, obsolete_map_removed = no_obsolete_map,
          old_status = canceled and canceled.status, receipts_added_by_stale_callback = added})
    end
  end
end

function Guards.tick(tick)
  local t = storage.guards
  if not t or t.result then return end
  local npc = storage.npcs[1]
  if not npc or not npc.character or not npc.character.valid then
    local state = {fixture_setup = true, ordinary_economy_claim = false,
      executed = t.executed, stage = t.stage, terminal_reason = "fixture_body_missing"}
    t.result = {schema = 1, mode = "guards", tick = tick, phase = "failed", completed = false,
      checks = t.checks, state = state, fingerprint = helpers.table_to_json(state)}
    storage.test.result = t.result
    return
  end
  if tick >= t.deadline then return terminal(t, npc, tick, "fixture_deadline") end
  if t.stage > #CASES then return terminal(t, npc, tick) end
  if not t.active then
    local ok, message = pcall(begin_case, t, npc, tick)
    if not ok then check(t, false, "setup error: " .. tostring(message)) end
  elseif tick >= t.case_deadline then
    if npc.current_job then Worker.cancel(npc, "guard_case_timeout") end
    check(t, false, "case deadline exceeded")
  else
    local ok, message = pcall(advance, t, npc, tick)
    if not ok then check(t, false, "assertion error: " .. tostring(message)) end
  end
end

return Guards
