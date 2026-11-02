-- Early robots (W-26, the user's boost): once the engineer makes the setting's science pack by machine (green science
-- by default), it is given modular armor with a personal roboport, the power for it and construction robots. From then
-- on what its robots can do they do: a plan's pieces, its rebuilds and its clearing go up as ghosts and marks the
-- robots take from its pockets, while it keeps the items stocked (fetched and crafted as ever) and stands within their
-- reach. What they cannot do, or have not done within a while, is done by hand as before.
-- The mod setting npc-factions-early-robots: "green-science" (the default), "red-science", "blue-science", or "off".

local Robots = {}
local Logistics = require("scripts.logistics")
local Ghosts = require("scripts.ghosts")

Robots.TIERS = {["red-science"]="automation-science-pack", ["green-science"]="logistic-science-pack",
  ["blue-science"]="chemical-science-pack"}
Robots.COUNT = 10              -- construction robots given
Robots.CHECK_EVERY = 600       -- ticks between looks at whether its tier is reached
Robots.PATIENCE = 45 * 60      -- ticks within their reach with a piece not done: then by hand
Robots.REACH_FOR = 4 * Robots.PATIENCE   -- ticks from its first ask, walks and all: then by hand
Robots.AWAY = 600              -- ticks unasked after which its time in their reach counts afresh
Robots.WAITS_MAX = 64
Robots.WITHIN = 0.6           -- (share of its reach a piece must be within)
Robots.PANELS_MAX = 30

local function try(f) local ok, v = pcall(f); if ok then return v end end

function Robots.setting()
  local s = settings.global["npc-factions-early-robots"]
  return s and s.value or "green-science"
end

-- Its pack made by machine: an assembler of its own plans set to it that has made one since it was first seen on
-- that recipe (its count is its whole life's, whatever it made before: Codex, W-26).
function Robots.reached(npc, pack)
  local plans = {npc.design and npc.design.plan or false}
  for _, p in ipairs(npc.design_history or {}) do plans[#plans + 1] = p end
  npc.robot_tier_seen = npc.robot_tier_seen or {}
  local seen = npc.robot_tier_seen
  for _, plan in ipairs(plans) do
    for _, id in ipairs(plan and plan.order or {}) do
      local e = plan.entities[id]
      local x = e and e.entity
      local unit = x and x.valid and x.type == "assembling-machine" and x.unit_number
      if unit then
        local r = try(function() return x.get_recipe() end)
        local made = try(function() return x.products_finished end) or 0
        if r and r.name == pack then
          local s = seen[unit]
          -- (planned for it: its whole count is of that pack; Codex, W-26)
          if e.recipe == pack and not e.adopted and made > 0 then return true end   -- (adopted: made before, maybe)
          if not (s and s.recipe == pack) then seen[unit] = {recipe=pack, made=made}
          elseif made > s.made then return true end
        else
          seen[unit] = nil
        end
      end
    end
  end
  return false
end

-- The first of `names` that exists, else the smallest of `list` (by area, then name).
local function pick(names, list, area)
  for _, n in ipairs(names) do if list[n] then return n end end
  local best, ba
  for name, p in pairs(list) do
    local a = area(p)
    if a and (not ba or a < ba or (a == ba and name < best)) then best, ba = name, a end
  end
  return best
end

local function equipment(kind)
  return try(function() return prototypes.get_equipment_filtered{{filter="type", type=kind}} end) or {}
end

local function shape_area(p)
  local s = try(function() return p.shape end)
  return s and (s.width or 1) * (s.height or 1) or nil
end

-- What it is given, from this game's prototypes (the user's mod set: vanilla's where it has them, else the smallest of
-- each kind): {armor, roboport, battery, panel, generator, robot}, or nil with why.
function Robots.kit()
  local armors = {}
  for name, item in pairs(try(function() return prototypes.get_item_filtered{{filter="type", type="armor"}} end) or {}) do
    local g = try(function() return item.equipment_grid end)
    if g and g.width >= 2 and g.height >= 2 then armors[name] = g end
  end
  local armor = pick({"modular-armor"}, armors, function(g) return g.width * g.height end)
  local ports = {}
  for name, p in pairs(equipment("roboport-equipment")) do
    if (try(function() return p.logistic_parameters.construction_radius end) or 0) > 0 then ports[name] = p end
  end
  local roboport = pick({"personal-roboport-equipment"}, ports, shape_area)
  local robots = {}
  for _, p in pairs(try(function() return prototypes.get_entity_filtered{{filter="type", type="construction-robot"}} end)
      or {}) do
    local item = try(function() return p.items_to_place_this[1].name end)
    if item then robots[item] = p end
  end
  local robot = pick({"construction-robot"}, robots, function() return 1 end)
  if not (armor and roboport and robot) then
    return nil, "no " .. (not armor and "armor with a grid" or not roboport and "personal roboport" or "construction robot")
  end
  return {armor=armor, roboport=roboport, robot=robot,
    battery=pick({"battery-equipment"}, equipment("battery-equipment"), shape_area),
    panel=pick({"solar-panel-equipment"}, equipment("solar-panel-equipment"), shape_area),
    generators=Robots.by_size(Robots.modest(equipment("generator-equipment")))}
end

-- Generators no stronger than vanilla's portable fission reactor (750 kW): the boost is early robots, not a portable
-- fusion reactor this early (the coordinator, W-26).
Robots.POWER_MAX = 750e3
function Robots.modest(list)
  local out = {}
  for name, p in pairs(list) do
    local w = try(function() return p.energy_production end)   -- (per tick; unread: left out, batteries and solar)
    if type(w) == "number" and w > 0 and w * 60 <= Robots.POWER_MAX then out[name] = p end
  end
  return out
end

-- Names of `list` smallest first (then by name): a generator that fits beside the roboport is tried before a bigger.
function Robots.by_size(list)
  local out = {}
  for name, p in pairs(list) do out[#out + 1] = {name=name, area=shape_area(p) or 99} end
  table.sort(out, function(a, b) return a.area < b.area or (a.area == b.area and a.name < b.name) end)
  local names = {}
  for i, o in ipairs(out) do names[i] = o.name end
  return names
end

local function charge(eq)
  if eq then pcall(function() eq.energy = eq.max_energy end) end
  return eq
end

-- Given once: the armor on (what it wore goes into its pockets), the roboport in its grid, power as fits (two
-- batteries, a generator if one fits, else solar panels filling the rest), charged, and the robots in its pockets.
function Robots.grant(npc, tick)
  local c = npc.character
  if not (c and c.valid) then return nil, "no_body" end
  local kit, why = Robots.kit()
  if not kit then return nil, why end
  local slot = c.get_inventory(defines.inventory.character_armor)
  local main = c.get_main_inventory()
  if not (slot and main) then return nil, "no_inventory" end
  local worn = slot[1] and slot[1].valid_for_read and slot[1]
  if worn and worn.name ~= kit.armor then
    local def = {name=worn.name, count=1, quality=worn.quality and worn.quality.name or "normal"}
    if main.insert(def) > 0 then worn.clear() end
  end
  if not (slot[1] and slot[1].valid_for_read) then slot.insert{name=kit.armor, count=1} end
  local grid = slot[1] and slot[1].valid_for_read and slot[1].grid
  if not grid then return nil, "armor_without_grid" end
  if not charge(grid.put{name=kit.roboport}) then return nil, "roboport_does_not_fit" end
  for _ = 1, 2 do if kit.battery then charge(grid.put{name=kit.battery}) end end
  local powered   -- (a generator, the smallest that fits, works at night too; solar panels fill what is left)
  for _, name in ipairs(kit.generators or {}) do
    powered = charge(grid.put{name=name})
    if powered then powered = name; break end
  end
  local panels = 0
  if kit.panel then
    for _ = 1, Robots.PANELS_MAX do if not grid.put{name=kit.panel} then break end panels = panels + 1 end
  end
  local given = main.insert{name=kit.robot, count=Robots.COUNT}
  -- (its pockets full: what did not go in is owed, and given at its next look: Codex, W-26)
  npc.robots_granted = {tick=tick, armor=kit.armor, roboport=kit.roboport, robot=kit.robot, robots=given,
    owed=Robots.COUNT - given}
  log("[npc-factions] npc=" .. tostring(npc.id) .. " is given " .. kit.armor .. ", " .. kit.roboport .. ", "
    .. (powered and (powered .. ", ") or "") .. panels .. " " .. tostring(kit.panel) .. ", batteries "
    .. tostring(kit.battery) .. " and " .. given .. " " .. kit.robot)
  return true
end

-- Its tier looked at now and then; given once, when reached.
function Robots.tick(npc, tick)
  if (tick % Robots.CHECK_EVERY) ~= ((npc.id or 0) % Robots.CHECK_EVERY) then return end
  local g = npc.robots_granted
  if g then
    local c = npc.character
    if (g.owed or 0) > 0 and g.robot and c and c.valid then
      local got = c.get_main_inventory().insert{name=g.robot, count=g.owed}
      g.owed, g.robots = g.owed - got, (g.robots or 0) + got
    end
    return
  end
  local pack = Robots.TIERS[Robots.setting()]
  if pack and Robots.reached(npc, pack) then Robots.grant(npc, tick) end
end

-- Its robots at hand: a personal roboport cell with reach and construction robots in its network; the reach, or nil.
-- (LIVE-7c: at night its solar panels gave nothing, its batteries ran down, and every piece waited 45 s or more on
-- robots that did not fly; science-tier stood 10 minutes without a piece on three engineers. Its robots count only
-- while its grid holds charge, and not while they are switched off for a stall: Robots.stalled)
Robots.CHARGE_MIN = 0.1      -- share of its grid's battery capacity below which its robots are as none
Robots.OFF_FOR = 10 * 3600   -- ticks its robot mode is off after its robots built nothing for a whole patience
function Robots.charged(npc)
  local c = npc.character
  local slot = c and c.valid and try(function() return c.get_inventory(defines.inventory.character_armor) end)
  local grid = slot and try(function() return slot[1] and slot[1].valid_for_read and slot[1].grid end)
  if not grid then return true end   -- (no grid read: as before)
  local cap = try(function() return grid.battery_capacity end) or 0
  if cap <= 0 then return true end
  return (try(function() return grid.available_in_batteries end) or 0) >= Robots.CHARGE_MIN * cap
end

function Robots.reach(npc)
  local c = npc.character
  if not (c and c.valid) then return nil end
  if npc.robots_off_until and game and game.tick < npc.robots_off_until then return nil end
  if not Robots.charged(npc) then return nil end
  local cell = try(function() return c.logistic_cell end)
  local radius = cell and (try(function() return cell.construction_radius end) or 0) or 0
  local net = radius > 0 and try(function() return cell.logistic_network end)
  local bots = net and (try(function() return net.all_construction_robots end) or 0) or 0
  if bots < 1 then return nil end
  return radius
end

local function key(job)
  local p = job.position or (job.target and job.target.valid and job.target.position)
  if not p then return nil end
  return job.kind .. ":" .. tostring(job.entity_name or (job.target and job.target.name)) .. ":" .. p.x .. ":" .. p.y
end

local function waits(npc, tick)
  npc.robot_waits = npc.robot_waits or {}
  local n = 0
  for k, w in pairs(npc.robot_waits) do
    if tick - w.since > 2 * Robots.REACH_FOR then npc.robot_waits[k] = nil else n = n + 1 end
  end
  if n > Robots.WAITS_MAX then npc.robot_waits = {} end
  return npc.robot_waits
end

-- The ghost of a planned piece (`piece`: world, prototype, direction, recipe, belt_to_ground_type), placed if missing
-- and kept on the piece (scripts/holograms.lua clears it with its plan); nil when none can stand there.
function Robots.ghost(npc, piece)
  local c = npc.character
  if not (c and c.valid and piece and piece.world) then return nil end
  -- (one given to its hands: no ghost again, or a robot claims it while it walks over; Codex, W-26)
  local w = npc.robot_waits and npc.robot_waits["build:" .. tostring(piece.prototype) .. ":" .. piece.world.x .. ":"
    .. piece.world.y]
  if piece.by_hand or (w and w.hands) then return nil end
  if piece.ghost and piece.ghost.valid then   -- (its hologram's too: as planned, or by hand; Codex, W-26)
    return Robots.matches(piece.ghost, piece) and piece.ghost or nil
  end
  local surface = c.surface
  for _, g in ipairs(try(function() return surface.find_entities_filtered{name="entity-ghost", position=piece.world,
      radius=0.5, force=npc.force} end) or {}) do
    if g.valid and g.ghost_name == piece.prototype then
      if not Robots.matches(g, piece) then return nil end   -- (a hologram not as planned: by hand)
      piece.ghost = g
      return g
    end
  end
  local g = Ghosts.create(surface, {inner_name=piece.prototype,   -- (only where a ghost may go: W-35)
    position=piece.world, direction=piece.direction, force=npc.force, expires=false,
    type=piece.belt_to_ground_type, mirror=piece.mirror == true or nil})
  if not g then return nil end
  if piece.recipe then try(function() g.set_recipe(piece.recipe) end) end
  if not Robots.matches(g, piece) then
    g.destroy()   -- (what its robots would put up not as planned: by hand)
    log("[npc-factions] npc=" .. tostring(npc.id) .. " builds " .. piece.prototype .. " by hand: its ghost is not as "
      .. "planned (recipe " .. tostring((try(function() return g.get_recipe().name end))) .. ", end "
      .. tostring((try(function() return g.belt_to_ground_type end))) .. ")")
    return nil
  end
  piece.ghost = g
  return g
end

-- A ghost as the piece is planned: its recipe, its belt end, its direction (adopt takes nothing else; a ghost that
-- cannot be read so is no match: Codex, W-26).
function Robots.matches(g, piece)
  if piece.recipe then
    local r = try(function() return g.get_recipe() end)
    if not (r and r.name == piece.recipe) then return false end
  end
  if piece.belt_to_ground_type and try(function() return g.belt_to_ground_type end) ~= piece.belt_to_ground_type then
    return false
  end
  if (try(function() return g.mirroring end) == true) ~= (piece.mirror == true) then return false end   -- (DS-3)
  if piece.direction and try(function() return g.direction end) ~= piece.direction then
    local p = prototypes.entity[piece.prototype]
    local turns = p and try(function() return p.supports_direction end)
    if turns ~= false then return false end
  end
  return true
end

-- A hand job its robots could do instead (`job`: a build of `piece`, or a mine of a tree or rock), as
-- (job, nil) to do (a walk into their reach, or `job` itself by hand) or (nil, why) to wait while they work.
function Robots.instead(npc, job, tick, piece)
  local spot = job and (job.position or (job.target and job.target.valid and job.target.position))
  local net = spot and Robots.network(npc, spot, job.kind == "build" and job.item or nil)
  local radius = job and Robots.reach(npc)
  if not (radius or net) then return job end
  local k = key(job)
  local w = k and waits(npc, tick)[k]
  if not k or (w and w.hands) or (piece and piece.by_hand) then return job end
  local at
  if job.kind == "build" and piece then
    local held = try(function() return npc.character.get_main_inventory().get_item_count(job.item) end) or 0
    -- (its network's robots, from its chests: no walk, nothing in its pockets; else its own, from its pockets: W-26b)
    local stocked = net and (try(function() return net.get_item_count(job.item) end) or 0) >= 1
    if not stocked then net = nil end
    if not (stocked or (radius and held >= 1)) or not Robots.ghost(npc, piece) then
      -- (by hand, then: with none in its pockets it fetches one first, the network's stock no reason not to; Codex)
      if held < 1 then piece.by_hand = true end
      return job
    end
    at = piece.world
  elseif job.kind == "mine" and job.target and job.target.valid
      and (job.target.type == "tree" or job.target.type == "simple-entity") then
    local marked = try(function() return job.target.to_be_deconstructed() end)
      or try(function() return job.target.order_deconstruction(npc.force) end)
    if not marked then return job end
    at = job.target.position
  else
    return job
  end
  if not w then   -- (from its first ask: REACH_FOR bounds the walks; what its robots had built then, Robots.by_hand)
    w = {since=tick, built_then=(npc.robots_built or 0) + (npc.robots_built_network or 0) + (npc.robots_mined or 0)}
    npc.robot_waits[k] = w
  end
  if tick - w.since > Robots.REACH_FOR then return Robots.by_hand(npc, w, job, piece, at, Robots.REACH_FOR) end
  if net then   -- (its network's: it waits where it is, its time counted as in its own robots' reach)
    if not w.within or tick - (w.seen or tick) > Robots.AWAY then w.within = tick end
    w.seen = tick
    if tick - w.within > Robots.PATIENCE then return Robots.by_hand(npc, w, job, piece, at, Robots.PATIENCE) end
    return nil, "robots:network"
  end
  if not radius then return job end
  local c = npc.character.position
  local dx, dy = c.x - at.x, c.y - at.y
  local far = math.max(math.abs(dx), math.abs(dy))
  -- (well within: at the edge of its reach the robot-build run waited 45 s for two furnaces 13.6 tiles off)
  if far > radius * Robots.WITHIN then   -- (a walk into their reach, halfway in from its side, off every ghost)
    w.within = nil   -- (out of their reach: its clock starts again in it)
    local s = (radius / 2) / math.max(far, 0.001)
    return {kind="move", position=Robots.stand(npc, {x=at.x + dx * s, y=at.y + dy * s}, at, radius)}
  end
  -- (standing on a ghost: its robots cannot put that up under it and wait above it; it steps off. The robot-build
  -- run: it stood on a furnace's spot and two furnaces went to hand after 45 s)
  if Robots.on_ghost(npc, c) then
    local q = Robots.stand(npc, c, at, radius)
    if q.x ~= c.x or q.y ~= c.y then return {kind="move", position=q} end
  end
  -- (time in their reach only: away a while, the defense say, its clock starts again; Codex, W-26)
  if not w.within or tick - (w.seen or tick) > Robots.AWAY then w.within = tick end
  w.seen = tick
  if tick - w.within > Robots.PATIENCE then return Robots.by_hand(npc, w, job, piece, at, Robots.PATIENCE) end
  return nil, "robots:" .. job.kind
end

-- By hand from now on, that piece: its ghost goes (no robot claims it while it walks over), and it is said. Its
-- robots having built nothing at all meanwhile, its robot mode is off a while: every piece by hand, not one 45 s wait
-- after another (LIVE-7c).
function Robots.by_hand(npc, w, job, piece, at, after)
  w.hands = true
  local built = (npc.robots_built or 0) + (npc.robots_built_network or 0) + (npc.robots_mined or 0)
  if w.built_then and built <= w.built_then and game then
    npc.robots_off_until = game.tick + Robots.OFF_FOR
    log("[npc-factions] npc=" .. tostring(npc.id) .. " builds by hand for " .. math.floor(Robots.OFF_FOR / 3600)
      .. " min: its robots built nothing in " .. math.floor(after / 60) .. " s")
  end
  if piece then piece.by_hand = true end   -- (kept with its plan: the waits are pruned in a while; Codex, W-26)
  if job.kind == "build" and piece and piece.ghost and piece.ghost.valid then piece.ghost.destroy(); piece.ghost = nil end
  if job.kind == "mine" and job.target and job.target.valid then   -- (its mark taken off: its hands', not theirs)
    try(function() job.target.cancel_deconstruction(npc.force) end)
  end
  log("[npc-factions] npc=" .. tostring(npc.id) .. " " .. job.kind .. "s " .. tostring(job.entity_name
    or (job.target and job.target.valid and job.target.name)) .. " at " .. at.x .. "," .. at.y
    .. " by hand: its robots did not in " .. math.floor(after / 60) .. " s")
  return job
end

-- A ghost of its own under `p` (a character's box, with a little room).
function Robots.on_ghost(npc, p)
  local s = npc.character.surface
  return #(try(function() return s.find_entities_filtered{type="entity-ghost", force=npc.force,
    area={{p.x - 0.9, p.y - 0.9}, {p.x + 0.9, p.y + 0.9}}, limit=1} end) or {}) > 0
end

-- Somewhere a character can stand (no water, no wall: Codex, W-26).
local function standable(npc, p)
  local s = npc.character.surface
  local ok = try(function() return s.can_place_entity{name="character", position=p} end)
  return ok ~= false
end

-- Where to stand near `want`, within reach of `at`, off every ghost: `want` if free, else the nearest of rings round
-- it; `want` itself when none is.
function Robots.stand(npc, want, at, radius)
  if not Robots.on_ghost(npc, want) and standable(npc, want) then return want end
  for r = 1, 6 do
    for k = 0, 7 do
      local a = k * math.pi / 4
      local q = {x=want.x + r * math.cos(a), y=want.y + r * math.sin(a)}
      if math.max(math.abs(q.x - at.x), math.abs(q.y - at.y)) <= radius * Robots.WITHIN and not Robots.on_ghost(npc, q)
          and standable(npc, q) then
        return q
      end
    end
  end
  return want
end

-- The logistic network of its own that covers `pos` with construction robots free (scripts/logistics.lua: NPC
-- Ledger's L-3), or nil; never its own personal cell's (that is its robots' reach, Robots.reach).
-- Every fixed network of its own covering `pos` (its personal cell's left out): its own cell may cover it too, beside
-- one or more fixed ones (Codex, W-26b).
function Robots.networks(npc, pos)
  local c = npc.character
  if not (c and c.valid) then return {} end
  local cell = try(function() return c.logistic_cell end)
  local mine = cell and try(function() return cell.logistic_network.network_id end)
  local all = try(function() return c.surface.find_logistic_networks_by_construction_area(pos, npc.force) end)
  if not all then all = {try(function() return Logistics.network_at(npc, pos) end)} end
  local out = {}
  for _, net in ipairs(all) do
    if net and (not mine or try(function() return net.network_id end) ~= mine) then out[#out + 1] = net end
  end
  return out
end

-- The one with construction robots free (and `item` in stock, when asked), or nil.
function Robots.network(npc, pos, item)
  for _, net in ipairs(Robots.networks(npc, pos)) do
    if (try(function() return net.available_construction_robots end) or 0) > 0
        and (not item or (try(function() return net.get_item_count(item) end) or 0) >= 1) then
      return net
    end
  end
  return nil
end

-- Its network there holds `item` for its robots: then it is neither fetched nor carried (W-26b).
function Robots.network_has(npc, pos, item)
  return pos ~= nil and item ~= nil and Robots.network(npc, pos, item) ~= nil
end

-- What its own robots put up and took down, counted (for whoever reads how it builds: the proving ground).
local function owner(robot)
  local net = robot and robot.valid and try(function() return robot.logistic_network end)
  local id = net and try(function() return net.network_id end)
  if not id then return nil end
  for _, npc in pairs(storage.npcs or {}) do
    local c = npc.character
    local cell = c and c.valid and try(function() return c.logistic_cell end)
    local mine = cell and try(function() return cell.logistic_network.network_id end)
    if mine == id then return npc end
  end
end

function Robots.on_built(event)
  local npc = owner(event.robot)
  if npc then npc.robots_built = (npc.robots_built or 0) + 1; return end
  -- (a fixed network's robot: counted for the engineer whose network it is there, Ledger's stage-robots score)
  local id = try(function() return event.robot.logistic_network.network_id end)
  local at = try(function() return event.entity.position end)
  if not (id and at) then return end
  for _, n in pairs(storage.npcs or {}) do
    for _, net in ipairs(Robots.networks(n, at)) do   -- (any of its networks there: Codex, W-26b)
      if try(function() return net.network_id end) == id then
        n.robots_built_network = (n.robots_built_network or 0) + 1
        return
      end
    end
  end
end

function Robots.on_mined(event)
  local npc = owner(event.robot)
  if npc then npc.robots_mined = (npc.robots_mined or 0) + 1 end
end

return Robots
