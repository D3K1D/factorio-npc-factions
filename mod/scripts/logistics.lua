-- The engineer's logistic networks (companion/network_ledger.py, companion/robo_grid.py): where its roboports stand
-- and how far they reach, how many robots each network has and how many are free, what the network holds, and its
-- chests by kind. Read-only. Robots build what is planned inside a network's construction range with the items the
-- network holds; what it holds and lacks is what the mall stocks for them.
local Logistics = {}

local MAX_CELLS = 200     -- roboports exported per network at most
local MAX_ITEMS = 96      -- items of a network's contents exported (the most held first)
local RADIUS = 400        -- networks with a roboport this near home are the engineer's

local function try(f) local ok, v = pcall(f); if ok then return v end end

-- The networks of the engineer's force with a cell near its home, each once.
local function networks(npc)
  local c = npc.character
  if not (c and c.valid) then return {} end
  local home = npc.home or c.position
  local surface = c.surface
  local found, seen = {}, {}
  for _, port in ipairs(try(function()
      return surface.find_entities_filtered{type="roboport", force=npc.force, position=home, radius=RADIUS}
    end) or {}) do
    local net = try(function() return port.logistic_network end)
    if net and net.valid and not seen[net.network_id] then
      seen[net.network_id] = true
      found[#found + 1] = net
    end
  end
  table.sort(found, function(a, b) return a.network_id < b.network_id end)
  return found
end

local function points(list)
  local n = 0
  for _ in pairs(list or {}) do n = n + 1 end
  return n
end

-- Repair packs: every repair tool the game knows (a mod's packs too), by name. Looked up each time (no module state:
-- the same on every machine).
function Logistics.repair_tools()
  local out = {}
  local found = try(function() return prototypes.get_item_filtered{{filter="type", type="repair-tool"}} end) or {}
  for name in pairs(found) do out[#out + 1] = name end
  table.sort(out)
  return out
end

-- Repair packs a network holds, all kinds together.
function Logistics.packs(net)
  local n = 0
  for _, name in ipairs(Logistics.repair_tools()) do
    n = n + (try(function() return net.get_item_count({name=name, quality="normal"}) end)
      or try(function() return net.get_item_count(name) end) or 0)
  end
  return n
end

-- One network: its cells (roboports, in world tiles), robots, contents and chests.
function Logistics.network(net)
  local cells = {}
  for _, cell in ipairs(try(function() return net.cells end) or {}) do
    if #cells >= MAX_CELLS then break end
    local owner = cell.owner
    if owner and owner.valid and owner.type == "roboport" then
      cells[#cells + 1] = {x=owner.position.x, y=owner.position.y, name=owner.name,
        logistic=cell.logistic_radius, construction=cell.construction_radius,
        robots=(cell.stationed_construction_robot_count or 0) + (cell.stationed_logistic_robot_count or 0),
        powered=(try(function() return owner.energy end) or 0) > 0 or nil}
    end
  end
  local held = {}
  for _, it in ipairs(try(function() return net.get_contents() end) or {}) do
    if (it.quality or "normal") == "normal" then held[#held + 1] = {it.name, it.count} end
  end
  table.sort(held, function(a, b) return a[2] > b[2] or (a[2] == b[2] and a[1] < b[1]) end)
  local items = {}
  for i = 1, math.min(#held, MAX_ITEMS) do items[held[i][1]] = held[i][2] end
  return {id=net.network_id, cells=cells,
    robots={construction=net.all_construction_robots or 0, construction_free=net.available_construction_robots or 0,
      logistic=net.all_logistic_robots or 0, logistic_free=net.available_logistic_robots or 0},
    items=next(items) and items or nil, repair_packs=Logistics.packs(net),
    chests={providers=points(try(function() return net.provider_points end)),
      requesters=points(try(function() return net.requester_points end)),
      storages=points(try(function() return net.storage_points end))}}
end

-- The site's `logistics`: every network near home (world tiles), or nil when it has none. Each with the buildings of
-- its force waiting as ghosts in its construction range (the jobs its robots have ahead).
function Logistics.export(npc)
  local out = {}
  local c = npc.character
  for _, net in ipairs(networks(npc)) do
    local row = Logistics.network(net)
    local ghosts = 0
    for _, cell in ipairs(row.cells) do
      local r = cell.construction or 0
      ghosts = ghosts + (try(function()
        return c.surface.count_entities_filtered{type="entity-ghost", force=npc.force,
          area={{cell.x - r, cell.y - r}, {cell.x + r, cell.y + r}}, limit=1000}
      end) or 0)
      if ghosts >= 1000 then break end
    end
    row.ghosts = ghosts   -- (cells overlap: a ghost in two cells counts twice; a rough count of the work ahead)
    -- (what its robots keep repaired: walls, gates and turrets in its construction areas; the repair packs it keeps
    -- are sized by them, companion/network_ledger.py. Overlapping cells count twice: a rough measure)
    local defenses = 0
    for _, cell in ipairs(row.cells) do
      local r = cell.construction or 0
      defenses = defenses + (try(function()
        return c.surface.count_entities_filtered{force=npc.force, area={{cell.x - r, cell.y - r}, {cell.x + r, cell.y + r}},
          type={"wall", "gate", "ammo-turret", "electric-turret", "fluid-turret", "artillery-turret"}, limit=5000}
      end) or 0)
      if defenses >= 5000 then break end
    end
    row.defenses = defenses
    -- (machines making repair packs in its logistic areas: the cells already feeding it, read live so an old cell that
    -- has left the base memory's latest plans is not built twice)
    local tools, makers, seen = {}, 0, {}
    for _, name in ipairs(Logistics.repair_tools()) do tools[name] = true end
    for _, cell in ipairs(row.cells) do
      local r = cell.logistic or 0
      for _, m in ipairs(try(function()
        return c.surface.find_entities_filtered{force=npc.force, type="assembling-machine",
          area={{cell.x - r, cell.y - r}, {cell.x + r, cell.y + r}}, limit=500}
      end) or {}) do
        local recipe = try(function() return m.get_recipe() end)
        local key = m.unit_number or (m.position.x .. "," .. m.position.y)
        if recipe and tools[recipe.name] and not seen[key] then seen[key] = true makers = makers + 1 end
      end
    end
    row.makers = makers
    out[#out + 1] = row
  end
  return #out > 0 and out or nil
end

-- The network whose construction range covers a position (its robots can build there), or nil.
function Logistics.network_at(npc, position)
  local c = npc.character
  if not (c and c.valid) then return nil end
  local net = try(function() return npc.force.find_logistic_network_by_position(position, c.surface) end)
  if net and net.valid then return net end
  for _, n in ipairs(networks(npc)) do
    for _, cell in ipairs(try(function() return n.cells end) or {}) do
      if try(function() return cell.is_in_construction_range(position) end) then return n end
    end
  end
  return nil
end

-- The networks whose construction areas cover a position (their robots can work there).
local function covering(npc, position)
  local c = npc.character
  if not (c and c.valid) then return {} end
  local found = try(function()
    return c.surface.find_logistic_networks_by_construction_area(position, npc.force)
  end)
  if found and #found > 0 then return found end
  local one = Logistics.network_at(npc, position)
  return one and {one} or {}
end

-- Whether robots will repair at a position (the user, 2026-10-30: late game, robots with repair packs repair walls
-- and buildings in the roboports' construction areas, so the engineer does not): a network of its own (not its
-- personal cell) covers it, holds a repair pack and has a construction robot. NPC Worker asks this before making a
-- repair job. Returns the network's packs (0 when no).
function Logistics.can_repair(npc, position)
  local best = 0
  for _, net in ipairs(covering(npc, position)) do
    local own = try(function() return npc.character.logistic_cell end)
    local mine = own and try(function() return own.logistic_network end)
    if net.valid and not (mine and mine.valid and mine.network_id == net.network_id)
        and (net.all_construction_robots or 0) > 0 then
      best = math.max(best, Logistics.packs(net))
    end
  end
  return best
end

return Logistics
