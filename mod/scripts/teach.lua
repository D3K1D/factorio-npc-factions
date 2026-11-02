-- Players teach engineers layouts: hold a blueprint (or a whole book) and click the
-- slot in the Engineers window (shortcut bar), or type /npc teach [engineer]. The
-- blueprint waits here until the host adds it to the engineers' library; teaching
-- counts as helping.
local Chronicle = require("scripts.chronicle")
local Teach = {}

local MAX_ENTITIES, MAX_WAITING = 600, 64

local function state()
  storage.taught = storage.taught or {next_id=1, list={}}
  return storage.taught
end

-- Every set-up blueprint the player is holding: one, or all of a book's (books in books too).
local function held_blueprints(player)
  local out = {}
  local function collect(stack, depth)
    if not (stack and stack.valid_for_read) or depth > 3 then return end
    if stack.is_blueprint_book then
      local inventory = stack.get_inventory(defines.inventory.item_main)
      for i = 1, inventory and #inventory or 0 do collect(inventory[i], depth + 1) end
    elseif stack.is_blueprint and stack.is_blueprint_setup() then
      out[#out + 1] = stack
    end
  end
  collect(player.cursor_stack, 0)
  return out
end

local function npc_named(token)
  if not token then return nil end
  local id = tonumber(token)
  for i = 1, 10 do
    local npc = storage.npcs and storage.npcs[i]
    if npc and (id == i or npc.name:lower() == tostring(token):lower()) then return npc end
  end
end

local function entry_of(e)
  local proto = prototypes.entity[e.name]
  if not proto then return nil end
  local item = proto.items_to_place_this and proto.items_to_place_this[1]
  local recipe = e.recipe
  if type(recipe) == "table" then recipe = recipe.name end
  return {name=e.name, x=e.position.x, y=e.position.y, direction=e.direction or 0,
    recipe=recipe, belt_to_ground_type=e.type, w=proto.tile_width, h=proto.tile_height, kind=proto.type,
    item=item and (item.name or item[1]) or nil}
end

local function keep(player, label, entities, npc)
  local s = state()
  label = label or ("blueprint #" .. s.next_id)
  s.list[#s.list + 1] = {id=s.next_id, label=label, player=player.name, player_index=player.index,
    npc_id=npc and npc.id or nil, tick=game.tick, entities=entities}
  s.next_id = s.next_id + 1
  while #s.list > MAX_WAITING do table.remove(s.list, 1) end
  return label
end

-- Teaches one blueprint; returns its label, or nil and why not.
local function teach_one(player, stack, npc)
  local raw = stack.get_blueprint_entities() or {}
  if #raw == 0 then return nil, "it has no buildings" end
  if #raw > MAX_ENTITIES then return nil, "it is too big (over " .. MAX_ENTITIES .. " buildings)" end
  local entities = {}
  for _, e in ipairs(raw) do entities[#entities + 1] = entry_of(e) end
  return keep(player, stack.label ~= "" and stack.label or nil, entities, npc)
end

-- What a player showed by dragging over part of their base: the buildings as a layout every engineer
-- learns (positions relative to the area). Returns the reply, and a short list of what was in it.
function Teach.from_area(player, entities, area)
  local list = {}
  local counts = {}
  for _, e in ipairs(entities or {}) do
    if e.valid and e.type ~= "character" and #list < MAX_ENTITIES then
      local entry = entry_of({name=e.name, direction=e.direction,
        type=e.type == "underground-belt" and e.belt_to_ground_type or e.type == "loader" and e.loader_type or nil,
        recipe=(e.type == "assembling-machine" or e.type == "furnace") and e.get_recipe() and e.get_recipe().name or nil,
        position={x=e.position.x - area.left_top.x, y=e.position.y - area.left_top.y}})
      if entry then
        list[#list + 1] = entry
        counts[e.name] = (counts[e.name] or 0) + 1
      end
    end
  end
  if #list == 0 then return "Drag over some of your buildings to show the engineers.", nil end
  local names = {}
  for name, n in pairs(counts) do names[#names + 1] = {name=name, n=n} end
  table.sort(names, function(a, b) return a.n > b.n or (a.n == b.n and a.name < b.name) end)
  local what = {}
  for i = 1, math.min(4, #names) do what[#what + 1] = names[i].n .. " " .. names[i].name:gsub("-", " ") end
  local label = keep(player, "shown by " .. player.name .. ": " .. table.concat(what, ", "), list, nil)
  for id = 1, 10 do
    if storage.npcs and storage.npcs[id] then
      Chronicle.record{kind="helped", actor="player:" .. player.index, target="npc:" .. id, tick=game.tick,
        source="player", payload={value=4, taught=label}}
    end
  end
  return "The engineers will study what you showed them (" .. table.concat(what, ", ") .. ").", what
end

-- Teaches what the player holds (a blueprint, or every blueprint in a book) to one
-- engineer (`token`: id or name) or to all of them. Returns the reply text.
function Teach.from_cursor(player, token)
  local stacks = held_blueprints(player)
  if #stacks == 0 then return "Hold a blueprint or a blueprint book, then teach it." end
  local npc = npc_named(token)
  local taught, skipped = {}, {}
  for _, stack in ipairs(stacks) do
    local label, why = teach_one(player, stack, npc)
    if label then taught[#taught + 1] = label else skipped[#skipped + 1] = why end
  end
  if #taught == 0 then return "Nothing to learn there: " .. tostring(skipped[1]) .. "." end
  -- Teaching is help: the engineers it was for remember who taught them.
  for id = 1, 10 do
    local who = storage.npcs and storage.npcs[id]
    if who and (not npc or who == npc) then
      Chronicle.record{kind="helped", actor="player:" .. player.index, target="npc:" .. id, tick=game.tick,
        source="player", payload={value=math.min(10, 2 * #taught), taught=taught[1]}}
    end
  end
  local what = #taught == 1 and ("\"" .. taught[1] .. "\"") or (#taught .. " blueprints")
  if npc then return npc.name .. ": Thanks, I'll study " .. what .. "." end
  return "The engineers will study " .. what .. "."
end

function Teach.recent(n)
  local list, out = state().list, {}
  for i = #list, math.max(1, #list - (n or 6) + 1), -1 do out[#out + 1] = list[i] end
  return out
end

-- Blueprints the host has not added to the library yet.
function Teach.waiting(limit)
  local out = {}
  for _, entry in ipairs(state().list) do
    if not entry.learned then
      out[#out + 1] = entry
      if #out >= (limit or 4) then break end
    end
  end
  return out
end

function Teach.learned(id, purpose)
  for _, entry in ipairs(state().list) do
    if entry.id == id then
      entry.learned, entry.purpose, entry.entities = game.tick, purpose, nil
      return true
    end
  end
  return false
end

return Teach
