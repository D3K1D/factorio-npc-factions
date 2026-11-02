-- Narrow adapter for missing unassociated-character handcraft production on 2.0.77.
-- It does not create items, set research flags/progress, or credit machine output.
-- Caller owns the crafting queue and verifies actual products before complete().
-- Force statistics include concurrent machines: subtracting their positive flows
-- is conservative and can under-credit a handcraft. Never infer extra output.
local Credit = {}

local function item_id(name) return {name = name, quality = "normal"} end

local function names_for(recipe)
  local seen, names = {}, {}
  for _,product in ipairs(recipe.products or {}) do
    if product.type ~= "item" or not product.amount or product.amount <= 0 or
        (product.probability and product.probability ~= 1) then
      return nil, "unsupported_credit_products"
    end
    if not seen[product.name] then
      seen[product.name] = true
      names[#names + 1] = product.name
    end
  end
  table.sort(names)
  return names
end

-- Call once, BEFORE the first begin_crafting invocation for this owned job.
-- Repeated calls preserve the original baseline across batches/save/load.
function Credit.begin(npc, job)
  if type(job) ~= "table" or job.kind ~= "craft" then return false, "not_handcraft_job" end
  if job.production_credit then return true, "already_snapshotted" end
  local character = npc.character
  if not character or not character.valid then return false, "body_missing" end
  local recipe = character.force.recipes[job.recipe or ""]
  if not recipe then return false, "recipe_missing" end
  local names, reason = names_for(recipe)
  if not names then return false, reason end
  local statistics = character.force.get_item_production_statistics(character.surface)
  local baseline = {}
  for _,name in ipairs(names) do baseline[name] = statistics.get_input_count(item_id(name)) end
  job.production_credit = {schema = 1, receipt_id = tostring(npc.id) .. ":" .. tostring(job.id),
    force_name = character.force.name, surface_index = character.surface.index, recipe = job.recipe,
    quality = "normal", product_names = names, baseline = baseline, credited = {}, observed = {},
    native_delta = {}, errors = {}}
  return true
end

-- job.produced is the worker's cumulative VERIFIED item output ledger. A queued
-- request, requested count, gifted inventory item or cancellation is not output.
-- Call after each newly verified product and again on terminal/cancel completion.
-- A duplicate call is harmless: each item's cumulative applied total is stored.
function Credit.complete(npc, job, recipe)
  if type(job) ~= "table" or job.kind ~= "craft" then return false, "not_handcraft_job" end
  local state = job.production_credit
  if not state then return false, "credit_baseline_missing" end
  if state.recipe ~= job.recipe or (recipe and recipe.name ~= state.recipe) then
    return false, "credit_recipe_changed"
  end
  local force, surface = game.forces[state.force_name], game.surfaces[state.surface_index]
  if not force or not force.valid or not surface or not surface.valid then
    return false, "credit_force_or_surface_missing"
  end
  local statistics = force.get_item_production_statistics(surface)
  local total = 0
  for _,name in ipairs(state.product_names) do
    local actual = (job.produced or {})[name] or 0
    if type(actual) ~= "number" or actual ~= actual or actual < 0 or actual ~= math.floor(actual) or actual > 1000000 then
      return false, "invalid_verified_credit_output"
    end
    local applied = state.credited[name] or 0
    local current = statistics.get_input_count(item_id(name))
    -- Remove this adapter's own earlier flows when measuring native flows.
    local native = math.max(0, current - state.baseline[name] - applied)
    local missing = math.max(0, actual - applied - native)
    state.observed[name], state.native_delta[name] = actual, native
    if missing > 0 then
      -- Mark before applying so recursive/duplicate completion never re-credits.
      state.credited[name] = applied + missing
      local ok, error_message = pcall(function() statistics.on_flow(item_id(name), missing) end)
      if not ok then
        -- An unexpected API failure is surfaced; keep the applied marker because
        -- retrying an uncertain mutation could duplicate statistics/research.
        state.errors[#state.errors + 1] = {item = name, count = missing, error = tostring(error_message)}
        log("[npc-factions] production credit failed for " .. state.receipt_id .. ": " .. tostring(error_message))
        return false, "credit_api_failure"
      end
      total = total + missing
    end
  end
  return true, total
end

function Credit.summary(job)
  local state = job and job.production_credit
  if not state then return nil end
  return {schema = state.schema, receipt_id = state.receipt_id, force_name = state.force_name,
    surface_index = state.surface_index, quality = state.quality, baseline = state.baseline,
    observed = state.observed, native_delta = state.native_delta, credited = state.credited,
    errors = state.errors}
end

return Credit
