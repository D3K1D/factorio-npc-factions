-- Saved, deterministic individual variation. This is rule-based timing and
-- preference variation, not a learned policy or a change to world speed.
local Behavior = {}
local Personality = require("scripts.personality")

local ROLES = {
  {name="methodical", tempo=0.65, gait=-0.55, recovery=0.45, traits={0.75,-0.35,0.25,-0.65}},
  {name="scout", tempo=-0.80, gait=0.90, recovery=-0.45, traits={-0.45,0.75,-0.15,0.80}},
  {name="caretaker", tempo=0.20, gait=-0.15, recovery=0.25, traits={0.55,-0.20,0.85,-0.35}},
  {name="builder", tempo=-0.35, gait=0.40, recovery=-0.10, traits={-0.15,0.90,0.20,0.25}},
  {name="steward", tempo=0.45, gait=-0.35, recovery=0.65, traits={0.90,-0.40,0.55,-0.55}},
  {name="sprinter", tempo=-0.65, gait=0.80, recovery=-0.60, traits={-0.35,0.45,-0.25,0.65}},
  {name="mediator", tempo=0.05, gait=0.10, recovery=0.05, traits={0.25,0.05,0.95,-0.10}},
  {name="pioneer", tempo=-0.50, gait=0.60, recovery=-0.25, traits={-0.55,0.95,0.05,0.55}},
  {name="analyst", tempo=0.70, gait=-0.45, recovery=0.55, traits={0.60,-0.05,0.20,-0.45}},
  {name="forager", tempo=-0.20, gait=0.30, recovery=-0.15, traits={0.15,0.35,0.40,0.35}},
}
local TRAIT_KEYS = {"resource_conservatism", "expansion_drive", "cooperation", "risk_tolerance"}
local SETTING_KEYS = {"npc-factions-conservation", "npc-factions-expansion",
  "npc-factions-cooperation", "npc-factions-risk"}

local function clamp(value, low, high)
  if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
    return low
  end
  return math.max(low, math.min(high, value))
end

local function jitter(seed, channel)
  -- Integer arithmetic stays below 2^53, with no unsaved RNG consumption.
  return ((seed * (channel * 43 + 17)) % 1009) / 504 - 1
end

local function setting(name, fallback)
  local entry = settings.global[name]
  return entry and clamp(entry.value, 0, 1) or fallback
end

local function fixture()
  return storage and storage.test ~= nil
end

local function effective_variation()
  return fixture() and 0 or setting("npc-factions-variation", 0.35)
end

local function effective_adaptation()
  return fixture() and 0 or setting("npc-factions-adaptation", 0.5)
end

function Behavior.ensure(npc)
  Personality.ensure(npc)
  if npc.behavior and npc.behavior.version == 1 then
    local c,b=npc.character,npc.behavior
    if c and c.valid and type(b.walk_speed_modifier)=="number" then
      local desired=clamp((b.base_running_speed_modifier or 0)+b.walk_speed_modifier,-0.5,0.5)
      if math.abs(c.character_running_speed_modifier-desired)>0.000001 then
        c.character_running_speed_modifier=desired
      end
    end
    return npc.behavior
  end
  local id = math.floor(clamp(npc.id or 1, 1, #ROLES))
  local role = ROLES[id]
  local seed = id * 7919 + 104729
  local traits = {}
  for index = 1, #TRAIT_KEYS do
    traits[index] = clamp(role.traits[index] + 0.05 * jitter(seed, index + 3), -1, 1)
  end
  local character = npc.character
  local base_modifier = character and character.valid and character.character_running_speed_modifier or 0
  npc.behavior = {
    version = 1, seed = seed, role = role.name,
    tempo_axis = clamp(role.tempo + 0.05 * jitter(seed, 1), -1, 1),
    gait_axis = clamp(role.gait + 0.05 * jitter(seed, 2), -1, 1),
    recovery_axis = clamp(role.recovery + 0.05 * jitter(seed, 3), -1, 1),
    trait_axes = traits, base_running_speed_modifier = clamp(base_modifier, -0.5, 0.5),
    failure_streak = 0,
  }
  return Behavior.refresh(npc)
end

function Behavior.refresh(npc)
  local behavior = npc.behavior
  if not behavior or behavior.version ~= 1 then return Behavior.ensure(npc) end
  local variation = effective_variation()
  local adaptation = effective_adaptation()
  local axes = Personality.axes(npc)
  behavior.variation = variation
  behavior.adaptation = adaptation
  behavior.cadence_multiplier = clamp(1 + variation * (0.30 * behavior.tempo_axis
    + 0.90 * (0.5-axes.work_rate) + 0.50 * (0.5-axes.cognition)), 0.4, 1.8)
  behavior.walk_speed_modifier = clamp(variation * (0.09 * behavior.gait_axis
    + 0.25 * (axes.work_rate-0.5)), -0.22, 0.22)
  behavior.recovery_multiplier = clamp(1 + 0.50 * variation * behavior.recovery_axis, 0.5, 1.5)
  npc.profile = npc.profile or {}
  for index, key in ipairs(TRAIT_KEYS) do
    local personality_axis = ({axes.frugality,axes.ambition,axes.cooperation,axes.courage})[index]
    npc.profile[key] = clamp(setting(SETTING_KEYS[index], 0.5)
      + variation * (0.15 * behavior.trait_axes[index] + 0.60 * (personality_axis-0.5)), 0, 1)
  end
  if npc.character and npc.character.valid then
    npc.character.character_running_speed_modifier = clamp(
      behavior.base_running_speed_modifier + behavior.walk_speed_modifier, -0.5, 0.5)
  end
  return behavior
end

function Behavior.cadence(npc, base_pace, tick)
  local behavior = Behavior.ensure(npc)
  local streak = clamp(behavior.failure_streak or 0, 0, 4)
  local adaptation = behavior.adaptation * (0.5 + Personality.axes(npc).adaptability)
  local response = 1 + adaptation * streak * 0.08
  local watch = npc.progress_watch
  if watch and type(watch.last_progress_tick) == "number"
      and type(tick) == "number" and tick - watch.last_progress_tick >= 600 then
    -- Reconsider a stalled camp a little sooner; this does not repair it.
    response = response * (1 - 0.25 * adaptation)
  end
  return math.max(npc.design and 15 or 30, math.floor(base_pace * behavior.cadence_multiplier * response + 0.5))
end

function Behavior.recovery_delay(npc)
  local behavior = Behavior.ensure(npc)
  local repeated = math.max(0, clamp(behavior.failure_streak or 0, 0, 4) - 1)
  return math.floor(clamp(300 * behavior.recovery_multiplier
    * (1 + behavior.adaptation * repeated * 0.20), 120, 900) + 0.5)
end

function Behavior.on_receipt(npc, receipt)
  local behavior = Behavior.ensure(npc)
  if receipt.status == "complete" then
    behavior.failure_streak = 0
  elseif receipt.status == "failed" then
    behavior.failure_streak = math.min(8, (behavior.failure_streak or 0) + 1)
  end
end

return Behavior
