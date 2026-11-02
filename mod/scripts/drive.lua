-- Driving on autopilot (the user, 2026-10-13): with a car or a tank of its own, a long trip is driven,
-- not walked, at full speed, the biters left behind. No AI in it: a route planned for the vehicle (a car
-- keeps out of worms' and enemy turrets' reach but passes nests, whose biters cannot keep up; a tank goes
-- closer still), each leg pathfound by the game for the vehicle's size, steered at speed, slowing for
-- turns and at the end, backing out when stuck. It parks short of the goal and goes on foot from there.
-- While it drives it owns the body (combat.lua would stop the car and get out).
local Routes = require("scripts.combat_routes")
local Drive = {}

local MIN_TRIP = 150       -- shorter trips are walked
Drive.MIN_TRIP = MIN_TRIP
local FETCH = 100          -- a vehicle of its own parked this close is walked to and driven
local BOARD = 3            -- close enough to get in
local LEG = 96             -- a leg of the route at a time, pathfound for the vehicle
local LOOK = 8             -- it steers for the point of the path this far ahead
-- Room for the vehicle as it turns and sways at speed, as half a square box's side: a wide one (drive11:
-- a 5x5 one's path passed a big rock with a tile to spare, and four runs' cars hit it), the narrow one
-- where that finds no way (drive3: a 3x3 one's path threaded two big rocks 4 tiles apart, which the car
-- could not).
local WIDE, NARROW = 4, 2.5
-- and a box not much bigger than the car itself where it has not room even for the narrow one to start
-- in (the range: a car on a lake shore, water inside every narrow box about it)
local TIGHT = 1.5
local PATH_WAIT = 300      -- ticks for the pathfinder; then straight for the leg's end
-- Stuck and "no way" are counted for each trouble, afresh once it is past it (drive7: twelve "no way"
-- legs counted over a whole trip, each one got round, ended it 500 tiles out); a trip that comes no
-- nearer the goal for NO_PROGRESS ends all the same.
-- (the user: "the car can back up: it hits a rock or a tree, it backs up quickly and keeps moving")
-- (the user, in the drive lab: backs up half a second or so from a standstill, then on; a little longer
-- here, as it does not steer as it backs: the range stuck less)
local STUCK_TICKS, BACK_TICKS, MAX_STUCK, MAX_NO_PATH = 60, 50, 10, 12
local CLEAR_AFTER = 3      -- stuck this often at one spot, it gets out and cuts what is in the way
local WOODS_TREES, WOODS_BLOCK = 12, 20   -- trees within 10 tiles that make thick woods; kept clear of so far
local STUCK_MOVE = 2       -- tiles it must get from where it was within STUCK_TICKS
-- Going nowhere: no nearer the goal and not MOVED tiles from where it was, for NO_PROGRESS (drive11: the
-- long way round a lake is no nearer for minutes); a trip slower than walking it twice is given up.
local NO_PROGRESS, CLOSER, MOVED = 10800, 32, 64
local WALK_SPEED = 0.15
-- After it stuck, it drives the next while as among trees (drive9: nine times into the same big rock,
-- cutting the corner round it at speed).
local CAREFUL_FOR = 600
-- It looks about it as it drives (drive9: into a worm's reach it had not seen, planned once and never
-- looked again): a worm, a turret or a nest it did not know of, and the route is planned again.
local LOOK_EVERY = 60
-- The land along a new route generated first (drive9: legs into land not yet made, where the game finds
-- no way at all, each "blocked" in turn).
local LAND_WAIT = 1800
-- Hit by a gun that stands (a worm, a turret): it backs straight off when it is ahead, drives away when
-- not, the other way when that is blocked, until out of its reach, and plans a route round it (drive9:
-- the car wrecked and it got out beside the worm, and died; drive13: backing into trees for five
-- seconds, then stopped still in reach; a car outruns biters, so those it drives on from).
local FLEE_FOR, CLEAR_OF, FLEE_BLOCKED = 900, 56, 20
-- Backing off, it backs out the way it came, along where it drove (the range: backing straight into the
-- trees beside its way, a behemoth worm's acid on it): where it was every CRUMB tiles, the last CRUMBS.
-- (reversing, the wheel turns it the other way, as in a real car: the drive lab)
local CRUMB, CRUMBS = 2, 40
-- Stuck twice in thick woods: backed out of them the way it came, this long, and round them (the range:
-- backing out turned into the next trees, again and again, cutting them one by one till it gave up).
local WOODS_BACK = 240
local GUNS = {"turret", "ammo-turret", "electric-turret", "fluid-turret"}
-- The next leg asked for this far before the end of the one it drives, so it drives on without
-- stopping for it (drive10: a stop at the end of every leg, and the car no faster than on foot).
local PREFETCH = 48
-- The pathfinder busy this many times running: straight for the leg's end (drive10: ten minutes stood
-- still on its way home, asking again and again).
local BUSY_MAX = 20
-- Wedged on a rock or a tree (drive12: between two big rocks, eleven times over, backing out into the
-- other): out of the vehicle, the one touching it mined as a player would, at the engine's own mining
-- speed, and back in.
local CLEAR_NEAR, CLEAR_FOR, MAX_CLEARS = 3.2, 600, 12
-- What is close to its path ahead, looked at every few ticks as far as it needs to slow down in (the
-- user: "a rock coming my way, I turn a little or slow down"): nothing within NEAR_PATH of it, full speed,
-- steering for a point well ahead; something, slow, steering for a point just ahead so it keeps to the
-- path the game found between them (drive1: stuck in a forest after cutting its corners at speed). Only
-- the path counts, not how many trees stand about (the range: a car crawling through every wood its
-- path went clear through). A tank knocks trees down: for it only rocks, cliffs and water.
-- Trees are no reason to slow down: a car breaks through a few at speed with hardly a scratch, and at a
-- crawl one stops it (the user, and tried on the range: a car at 0.43 through trees seven tiles apart
-- lost 2 of its 450, stopped dead by one at 0.15; a rock stopped it and took 59). A tank goes through
-- trees and rocks alike, big and huge (no damage): only cliffs, water and buildings stop it.
local NEAR_PATH, AHEAD_EVERY, CAREFUL_LOOK = 2.6, 10, 3
local CAREFUL_SPEED = {car = 0.25, tank = 0.15}
local BUILDINGS = {"wall", "gate", "turret", "ammo-turret", "electric-turret", "fluid-turret", "unit-spawner",
  "container", "logistic-container", "furnace", "assembling-machine", "mining-drill", "inserter",
  "transport-belt", "underground-belt", "splitter", "loader", "pipe", "pipe-to-ground", "electric-pole", "boiler",
  "generator", "lab", "radar", "storage-tank", "offshore-pump", "pump", "rocket-silo", "solar-panel",
  "accumulator", "roboport", "beacon", "reactor", "land-mine"}
local function with_buildings(kinds)
  local out = {}
  for _, k in ipairs(kinds) do out[#out + 1] = k end
  for _, k in ipairs(BUILDINGS) do out[#out + 1] = k end
  return out
end
local IN_THE_WAY = {car = with_buildings{"simple-entity", "cliff"}, tank = with_buildings{"cliff"}}
-- A leg it can drive straight, the pathfinder not asked: nothing it would stop at near the line, and
-- for a car not many trees on it (one in eight tiles: through trees seven tiles apart at speed, nearly
-- stopped by ones four apart).
local TREES_PER_TILE = 1 / 8
-- A person keeps rocks a berth of four to six tiles (the drive lab: 46 of 47 passed farther than 2 tiles,
-- most 4 to 6).
local ROCK_BERTH = 4
local TREES_AHEAD = 12     -- trees on the way this far ahead counted (looked at, not slowed for)
-- Biters about: it never stops among them (the lab: nearly all 700 bites taken standing still; moving,
-- it hardly gets bitten), and it shoots them with the vehicle's gun as it goes (shooting 73% of the time
-- they were about), worms in reach too.
local THREAT_RADIUS, GUN_REACH, LOOK_THREAT = 25, 20, 30
local ARRIVE_AMONG_BITERS = 900   -- ticks at most it drives on about the goal before getting out among them
local STRAIGHT_AFTER_STUCK = 600   -- after a stuck, legs by the pathfinder's way round for a while
-- It brakes for the goal from as far as it takes to stop (tiles per tick it sheds a tick, about), not
-- from 40 tiles out at a crawl.
local BRAKE = 0.004
local WALK_FOR = 1800      -- ticks to walk to a parked vehicle
-- Its own base is no place to drive (drive2: a car put down among its buildings never got out): it walks
-- out before it puts one down, and coming home parks outside and walks in.
local HOME_CLEAR = 64
Drive.HOME_CLEAR = HOME_CLEAR
local FUEL_KEEP = 20       -- fuel kept in the burner
-- Below this much of its health, stopped and still being hit, the vehicle is left; moving, it drives on
-- (drive13: got out at a quarter beside the worm that hit it, and died; a vehicle's armor is better
-- than none, and the game puts its driver out unhurt when it is destroyed).
local BAIL_HEALTH = 0.25
-- Badly hurt, with repair packs (the user: "if it takes too much damage they can drive to a safe spot,
-- kill biters that are chasing if needed, then get out and repair really quick"): below half its health
-- it drives away from what hurt it (the trip's own way, when that leads away) till nothing is about,
-- sits a while in the seat with its gun on whatever followed, gets out and mends the vehicle as a
-- player does (at the pack's speed; the lab: the user mended at 1.2 to 1.6 a tick, pauses and all),
-- and goes on; anything coming at it meanwhile, back in and away.
local MEND_AT, MEND_TO = 0.5, 0.95
local SAFE_RADIUS = 40     -- no biter or enemy this near (nor a worm or a turret within CLEAR_OF)
local QUIET_FOR = 180      -- and not hit for this long: it stops
local WATCH_FOR = 90       -- stopped, ticks in the seat with nothing about before it gets out
local WAIT_MAX = 600       -- something about that long without coming on: farther away
local RETREAT = 96         -- how far off it heads: away from them, and back the way it came
local MAX_RETREATS = 5     -- driven off this often, it gives mending up for the trip
local REPAIR_REACH = 6
local PACK = "repair-pack"
local TWO_PI = 2 * math.pi

local function distance(a, b)
  local dx, dy = a.x - b.x, a.y - b.y
  return math.sqrt(dx * dx + dy * dy)
end

-- What it can drive, from the game's data (a modded game adds its own: armored cars, bigger tanks), sturdiest
-- first: every car-like vehicle an item puts down. A heavy one (a tank) goes through trees and rocks; each turns
-- at its own rate (the range: a tank orbited a point a car turns at, turning four times slower).
local HEAVY = 5000         -- weight at or over which it goes through trees and rocks (a car 700, a tank 20000)
local KNOWN = {tank = {name = "tank", item = "tank", heavy = true, turn = 0.0035 * TWO_PI, health = 2000},
  car = {name = "car", item = "car", heavy = false, turn = 0.015 * TWO_PI, health = 450}}   -- (no game data: tests)
local kinds   -- (worked out once a load from the prototypes, the same on every machine: not saved)
function Drive.vehicles()
  if kinds then return kinds end
  local out = {}
  local ok, list = pcall(function() return prototypes.get_entity_filtered{{filter = "type", type = "car"}} end)
  for name, p in pairs(ok and list or {}) do
    local items = p.items_to_place_this
    local item = items and items[1] and items[1].name
    if item then
      local hp_ok, hp = pcall(function() return p.get_max_health() end)
      out[#out + 1] = {name = name, item = item, heavy = (p.weight or 0) >= HEAVY,
        turn = (p.rotation_speed or 0.015) * TWO_PI, health = hp_ok and hp or 0}
    end
  end
  if #out == 0 then out = {KNOWN.tank, KNOWN.car} end
  table.sort(out, function(a, b)
    if a.health ~= b.health then return a.health > b.health end
    return a.name < b.name
  end)
  kinds = out
  return out
end
-- One vehicle's kind by its entity's (or item's) name.
local function kind_of(name)
  for _, k in ipairs(Drive.vehicles()) do
    if k.name == name or k.item == name then return k end
  end
  return KNOWN[name] or KNOWN.car
end
Drive.kind_of = kind_of
local function heavy(v) return kind_of(v.name).heavy end

-- What burns in its burner, from the game's data, best first: the fastest (the range: a car after ten seconds
-- at 0.43 on coal, 0.47 on solid fuel, 0.57 on rocket fuel, 0.67 on nuclear; a tank 0.23 on coal, 0.36 on
-- nuclear), then the longest burning; nothing that slows it (spoilage).
local FUELS = {"nuclear-fuel", "rocket-fuel", "solid-fuel", "coal", "wood"}   -- (no game data: tests)
local fuel_lists = {}   -- (by vehicle, worked out once a load)
local function fuels(name)
  local list = fuel_lists[name]
  if list then return list end
  local rows = {}
  local ok = pcall(function()
    local burner = prototypes.entity[name].burner_prototype
    local cats = burner and burner.fuel_categories or {}
    for item, p in pairs(prototypes.get_item_filtered{{filter = "fuel"}}) do
      local top = p.fuel_top_speed_multiplier or 1
      for _, c in ipairs(p.fuel_categories or {}) do
        if cats[c] and top >= 1 then
          rows[#rows + 1] = {name = item, top = top, acc = p.fuel_acceleration_multiplier or 1, value = p.fuel_value or 0}
          break
        end
      end
    end
  end)
  table.sort(rows, function(a, b)
    if a.top ~= b.top then return a.top > b.top end
    if a.acc ~= b.acc then return a.acc > b.acc end
    if a.value ~= b.value then return a.value > b.value end
    return a.name < b.name
  end)
  list = {}
  for _, r in ipairs(rows) do list[#list + 1] = r.name end
  if not ok then list = FUELS end   -- (no game data, as in tests; a vehicle no fuel suits gets none)
  fuel_lists[name] = list
  return list
end
Drive.fuels = fuels

-- What its drives came to, the last TRACE things (where it got in, legs with no way, spots blocked,
-- stuck, why it stopped): looked at after a run.
local TRACE = 30
local function note(npc, what, p)
  local t = npc.drive_trace or {}
  npc.drive_trace = t
  t[#t + 1] = string.format("%d %s @%.0f,%.0f", game.tick, what, p and p.x or 0, p and p.y or 0)
  while #t > TRACE do table.remove(t, 1) end
end

-- How to drive toward `aim` from `pos` with the vehicle facing `orientation` (0 north, 0.25 east) at
-- `speed`: the acceleration ("accelerating", "braking", "nothing") and the turn ("left", "right",
-- "straight"). Slower for a sharp turn, and when `slow` (the end of the trip near).
-- How fast it may go to make a turn toward `aim`: a vehicle turns at its own rate (radians a tick), so
-- the nearer and the more to the side the point it steers for, the slower; any faster and it circles
-- round the point for ever (the range: a tank, eleven thousand ticks round one at a crawl a car turns
-- at in no room at all; a tank turns four times slower).
local TURN_RATE = {car = 0.015 * TWO_PI, tank = 0.0035 * TWO_PI}
local COAST = 0.06         -- this much too fast, off the accelerator; more, the brake
Drive.TURN_RATE = TURN_RATE
function Drive.steer(orientation, speed, pos, aim, slow, turn_rate)
  local dx, dy = aim.x - pos.x, aim.y - pos.y
  local wanted = (math.atan2(dx, -dy) / TWO_PI) % 1
  local delta = (wanted - orientation + 0.5) % 1 - 0.5
  local turn = delta > 0.01 and "right" or delta < -0.01 and "left" or "straight"
  local sharp = math.abs(delta)
  -- `slow`: true (a crawl) or the speed to keep under
  local cap = type(slow) == "number" and slow or slow and 0.12 or 1
  local side = sharp > 0.25 and 1 or math.sin(sharp * TWO_PI)
  local turning = side < 0.02 and 1 or (turn_rate or TURN_RATE.car) * math.sqrt(dx * dx + dy * dy) / (2 * side) + 0.02
  local limit = math.min(cap, turning)
  -- (as the user drives, the drive lab: on the accelerator 62% of the time, off it 34%, the brake 2%:
  -- off it to shed a little, the brake only to shed a lot)
  local over = math.abs(speed) - limit
  local accel = over > COAST and "braking" or over > 0 and "nothing" or "accelerating"
  return accel, turn
end

-- Its own vehicle: the one it drives, one it parked within reach, or any idle one of its force near it.
local function vehicle_near(npc, reach)
  reach = reach or FETCH
  local c = npc.character
  local v = c.vehicle
  if v and v.valid and v.type == "car" and v.force == npc.force then return v end
  local p = npc.parked
  if p and p.valid and p.surface == c.surface and not p.get_driver() and distance(p.position, c.position) <= reach then
    return p
  end
  local best, best_d
  for _, e in ipairs(c.surface.find_entities_filtered{type = "car", force = npc.force, position = c.position,
      radius = reach}) do
    local d = distance(e.position, c.position)
    if e.valid and not e.get_driver() and not e.get_passenger() and (not best or d < best_d) then best, best_d = e, d end
  end
  return best
end
-- A vehicle in its pockets, sturdiest first: the item, and the vehicle it puts down.
local function vehicle_item(npc)
  local main = npc.character.get_main_inventory()
  for _, k in ipairs(Drive.vehicles()) do
    if main and main.get_item_count(k.item) > 0 then return k.item, k.name end
  end
end
-- The best fuel in its pockets for that vehicle (by entity name; a car's when it has none yet).
local function fuel_item(npc, name)
  local main = npc.character.get_main_inventory()
  for _, item in ipairs(fuels(name or "car")) do
    if main and main.get_item_count(item) > 0 then return item end
  end
end
local function fueled(v)
  local b = v.burner
  return b and ((b.remaining_burning_fuel or 0) > 0 or not b.inventory.is_empty())
end
-- Fuel from its pockets into the burner, up to FUEL_KEEP.
local function refuel(npc, v)
  local b = v.burner
  local main = npc.character.get_main_inventory()
  if not (b and main) then return end
  local name = fuel_item(npc, v.name)
  if not name then return end
  local have = b.inventory.get_item_count(name)
  local n = math.min(FUEL_KEEP - have, main.get_item_count(name))
  if n <= 0 then return end
  local put = b.inventory.insert{name = name, count = n}
  if put > 0 then main.remove{name = name, count = put} end
end

-- The vehicle's guns loaded from its pockets: magazines for the machine gun (the best it has, a stack
-- kept for itself on foot), and a tank's shells.
local GUN_AMMO = {{"uranium-rounds-magazine", "piercing-rounds-magazine", "firearm-magazine"},
  {"explosive-cannon-shell", "cannon-shell"}}
local AMMO_LOADED, AMMO_KEPT = 50, 30
local function load_guns(npc, v)
  local main = npc.character.get_main_inventory()
  local slot = defines.inventory and defines.inventory.car_ammo
  local ammo = slot and v.get_inventory and v.get_inventory(slot)
  if not (main and ammo) then return end
  for _, kinds in ipairs(GUN_AMMO) do
    for _, name in ipairs(kinds) do
      local have = main.get_item_count(name)
      local spare = name:find("magazine") and have - AMMO_KEPT or have
      local n = math.min(AMMO_LOADED - ammo.get_item_count(name), spare)
      if n > 0 then
        local put = ammo.insert{name = name, count = n}
        if put > 0 then main.remove{name = name, count = put} end
        if put > 0 then break end
      end
    end
  end
end

-- Its wall ring (the newest its defense plans keep, world tiles x0 y0 x1 y1, as TeamWar.ring_edges reads it):
-- inside it its factory, no place to drive. It comes and goes by the ring's doors and parks inside by one.
local function ring_box(npc)
  local best
  for _, plan in ipairs(npc.design_history or {}) do
    local k = plan.keep
    if k and k.ring and k.ring_gen and (not best or k.ring_gen > best.ring_gen) then best = k end
  end
  return best and best.ring
end
Drive.ring_box = ring_box
local function inside(box, p, margin)
  margin = margin or 0
  return box ~= nil and p.x > box[1] + margin and p.x < box[3] - margin and p.y > box[2] + margin
    and p.y < box[4] - margin
end
local function friendly(npc, f)
  return f == npc.force or (npc.force.get_friend and npc.force.get_friend(f)) or false
end

-- Doors (M-2, tried in the engine 2026-10-26): gates of its own and of its friends open for its vehicle at
-- full speed, square on or up to 30 degrees off, through any number of wall layers; at 45 degrees a car
-- wedges in a 4-wide door. The game's pathfinder has no way for a vehicle-sized box through one (only a 3x3
-- box gets through 4 gates, and it takes every gate for open, an enemy's too). So a leg across a wall of its
-- own or its friends goes to a point square in front of a door, straight through it, and on from the far side.
-- Gates touching (side by side along the wall, layer behind layer) are one door; a gate in a wall running
-- east-west faces east or west and is gone through north-south.
local DOOR_OUT = 12        -- tiles in front of a door where it lines up
local DOOR_LINE = 7        -- the point on the door's line it steers for before the door
local DOOR_AT = 7          -- this near the lining-up point, it goes through
local WALL_AHEAD = 16      -- a wall this far past a leg's end, into or out of its ring, is crossed on that leg
local RING_NEAR = 12       -- this near its ring's wall, either side, it drives carefully
local RING_ROOM = 6        -- out there, a leg passing this near its ring goes round it by a corner
local DOOR_RUN = 8         -- and it lines up this much farther out where the run in is clear: from the side at
                           -- 12 out, a car could not square up in time and hit the wall beside the door (the
                           -- engine check; the doors' lanes are kept clear 12 deep, companion/doorways.py)
local DOOR_CAREFUL = 300   -- ticks it goes slowly, going through
local MAX_CROSSINGS = 6    -- doors gone through on one trip, at most (back and forth: given up)
function Drive.doors(npc, s, area, own)
  local gates = {}
  for _, g in ipairs(s.find_entities_filtered{area = area, type = "gate"}) do
    if g.valid and (g.force == npc.force or not own and friendly(npc, g.force)) then gates[#gates + 1] = g end
  end
  local doors, seen = {}, {}
  for i, g in ipairs(gates) do
    if not seen[i] then
      seen[i] = true
      local group, k = {g}, 1
      while group[k] do
        local a = group[k].position
        for j, h in ipairs(gates) do
          if not seen[j] and math.abs(h.position.x - a.x) <= 1.5 and math.abs(h.position.y - a.y) <= 1.5 then
            seen[j], group[#group + 1] = true, h
          end
        end
        k = k + 1
      end
      local sx, sy, x0, x1, y0, y1 = 0, 0, math.huge, -math.huge, math.huge, -math.huge
      for _, h in ipairs(group) do
        local p = h.position
        sx, sy = sx + p.x, sy + p.y
        x0, x1, y0, y1 = math.min(x0, p.x), math.max(x1, p.x), math.min(y0, p.y), math.max(y1, p.y)
      end
      local ew = g.direction == defines.direction.east or g.direction == defines.direction.west
      doors[#doors + 1] = {x = sx / #group, y = sy / #group, across = ew and "y" or "x",
        width = ew and (x1 - x0 + 1) or (y1 - y0 + 1)}
    end
  end
  return doors
end
-- A door's two lining-up points: the one on `from`'s side first. A door in its ring: the side is inside
-- or outside the ring (`from_in`), not the side of the door's own line (from outside, south of the ring, a
-- north door's outer side is north of it).
local function door_ends(door, from, box, from_in)
  local ax, ay = 0, 0
  if box then
    local s = from_in and 1 or -1
    if door.across == "y" then ay = ((box[2] + box[4]) / 2 > door.y and 1 or -1) * s
    else ax = ((box[1] + box[3]) / 2 > door.x and 1 or -1) * s end
  elseif door.across == "y" then ay = from.y < door.y and -1 or 1 else ax = from.x < door.x and -1 or 1 end
  return {x = door.x + ax * DOOR_OUT, y = door.y + ay * DOOR_OUT}, {x = door.x - ax * DOOR_OUT, y = door.y - ay * DOOR_OUT},
    {x = door.x + ax * DOOR_LINE, y = door.y + ay * DOOR_LINE}
end
-- Going through a door: the point to steer for, on the door's axis ahead of where the vehicle is on it (toward
-- the far side, b at most): DOOR_AHEAD out there, nearer as the door comes close, so it is on the line before
-- the door (the steering slows it for the sharper turn). Steering for the lining-up points themselves, a car
-- that came at one from the side circled it for minutes, never near enough to count it reached, or cut the
-- corner into the wall beside the door (the engine check, a walled range).
local DOOR_AHEAD, DOOR_AHEAD_MIN = 8, 2
local function door_aim(door, here)
  local c, b = door.door, door.b
  local len = distance(c, b)
  local ux, uy = (b.x - c.x) / len, (b.y - c.y) / len
  local s = (here.x - c.x) * ux + (here.y - c.y) * uy
  local ahead = s < 0 and math.max(DOOR_AHEAD_MIN, math.min(DOOR_AHEAD, -s / 2)) or DOOR_AHEAD
  local t = math.min(s + ahead, len)
  return {x = c.x + ux * t, y = c.y + uy * t}
end
-- A door in the ring's wall itself (not one inside it, or of an older, smaller ring).
local function on_edge(box, door)
  return inside(box, door, -4) and not inside(box, door, 4)
end
-- Whether the way from a to b goes into the box (walls on its edges), or within `room` of it (2 tiles; where it
-- sets off, 2 at most: just out of a door, every way would go near it).
local function enters(box, a, b, room)
  local n = math.max(1, math.ceil(distance(a, b) / 4))
  for i = 0, n do
    local m = i == 0 and math.min(room or 2, 2) or room or 2
    if inside(box, {x = a.x + (b.x - a.x) * i / n, y = a.y + (b.y - a.y) * i / n}, -m) then return true end
  end
  return false
end
-- The way from a to b outside the box: straight, or round the corner that makes it shortest when straight
-- would go through it.
local AROUND = 24          -- room kept round its ring's corners going round it
local function corners(box)
  return {{x = box[1] - AROUND, y = box[2] - AROUND}, {x = box[3] + AROUND, y = box[2] - AROUND},
    {x = box[3] + AROUND, y = box[4] + AROUND}, {x = box[1] - AROUND, y = box[4] + AROUND}}
end
local function round_about(box, a, b)
  if not (box and enters(box, a, b)) then return distance(a, b) end
  local best
  local cs = corners(box)
  for i, q in ipairs(cs) do
    if not enters(box, a, q) then
      if not enters(box, q, b) then   -- round one corner
        local cost = distance(a, q) + distance(q, b)
        best = best and math.min(best, cost) or cost
      end
      for _, j in ipairs({i % 4 + 1, (i + 2) % 4 + 1}) do   -- or along a side to the next corner, then on
        local r = cs[j]
        if not enters(box, r, b) then
          local cost = distance(a, q) + distance(q, r) + distance(r, b)
          best = best and math.min(best, cost) or cost
        end
      end
    end
  end
  return best or distance(a, b) * 2
end
-- Whether the way straight from a to b runs into a wall of its own or its friends (a ring, a neighbour's).
local function walled(npc, s, a, b)
  local area = {{math.min(a.x, b.x) - 1, math.min(a.y, b.y) - 1}, {math.max(a.x, b.x) + 1, math.max(a.y, b.y) + 1}}
  local dx, dy = b.x - a.x, b.y - a.y
  local len2 = dx * dx + dy * dy
  for _, w in ipairs(s.find_entities_filtered{area = area, type = {"wall", "gate"}}) do
    if w.valid and friendly(npc, w.force) then
      local p = w.position
      local t = len2 > 0 and math.max(0, math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) or 0
      local qx, qy = a.x + t * dx - p.x, a.y + t * dy - p.y
      if qx * qx + qy * qy <= 2.25 then return true end
    end
  end
  return false
end
Drive.walled = walled
-- Out there, whether the way from a to b goes round its ring: into it, or along it nearer than RING_ROOM (one
-- just out of its door 2 tiles off the wall, and the car ran along the wall into it: the range); only into it
-- when b is where it stops and that near the wall (a goal by the wall: Codex, round one corner then the other).
local function goes_round(box, a, b, stops)
  return enters(box, a, b, stops and inside(box, b, -RING_ROOM) and 2 or RING_ROOM)
end
-- The door to cross by, on the way from `here` toward `to` (whose far side it leads to) and on to `bound`:
-- the one that makes the way shortest; nil when it knows none. Into or out of its ring (`here` and `bound`
-- on its two sides) any door in the ring's wall will do, wherever the wall is met, the way round the ring
-- counted (the engine check: out east with its one door west, no door lay across the way, and it took the
-- tightest box, back and forth); else a door on the line of the wall met (a neighbour's).
-- Whether the straight run from a to b is clear for the vehicle (nothing it would hit, no water), `half` either side.
local function run_clear(s, v, a, b, half)
  local mask = v and v.prototype and v.prototype.collision_mask
  if not (mask and mask.layers) then return false end
  local layers = {}
  for layer in pairs(mask.layers) do layers[#layers + 1] = layer end
  local area = {{math.min(a.x, b.x) - half, math.min(a.y, b.y) - half}, {math.max(a.x, b.x) + half, math.max(a.y, b.y) + half}}
  return s.count_entities_filtered{area = area, collision_mask = layers, limit = 1} == 0
    and s.count_tiles_filtered{area = area, collision_mask = layers, limit = 1} == 0
end
local function door_for(npc, s, here, to, bound, v)
  local box = ring_box(npc)
  local here_in = box and inside(box, here)
  local ring = box and here_in ~= inside(box, bound)
  local r = 96
  local area = ring and {{box[1] - 4, box[2] - 4}, {box[3] + 4, box[4] + 4}}
    or {{math.min(here.x, to.x) - r, math.min(here.y, to.y) - r}, {math.max(here.x, to.x) + r, math.max(here.y, to.y) + r}}
  local best, best_cost
  for _, door in ipairs(Drive.doors(npc, s, area)) do
    local across
    if ring then
      across = on_edge(box, door)
    else
      across = door.across == "y" and (to.y - door.y) * (here.y - door.y) < 0
        or door.across == "x" and (to.x - door.x) * (here.x - door.x) < 0
    end
    if across then
      local a, b, line = door_ends(door, here, ring and box, here_in)
      local cost = ring and (round_about(not here_in and box, here, a) + round_about(here_in and box, b, bound))
        or distance(here, a) + distance(b, bound)
      if not best or cost < best_cost then best, best_cost = {door = door, a = a, b = b, line = line}, cost end
    end
  end
  if best then
    local a, door = best.a, best.door
    local far = {x = door.x + (a.x - door.x) * (DOOR_OUT + DOOR_RUN) / DOOR_OUT,
      y = door.y + (a.y - door.y) * (DOOR_OUT + DOOR_RUN) / DOOR_OUT}
    if run_clear(s, v, far, a, (door.width or 4) / 2 + 1) then best.far = far end
  end
  return best
end

-- Where it parks at home (the user: vehicles kept inside the wall): inside its ring by the door of its own
-- nearest `toward` (the work it walks on to), off the door's way in and out and off belts (a vehicle on a belt
-- is carried away); nil without a ring with a door.
local GARAGE_IN, GARAGE_SIDE = 16, 5
local BELTS = {"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1", "linked-belt"}
function Drive.garage(npc, s, toward, name)
  local box = ring_box(npc)
  if not box then return nil end
  -- the doors in the ring's wall (not an inner gate, or an older ring's), nearest first: by the next one
  -- when the factory has filled the ground by the nearest
  local doors = {}
  for _, door in ipairs(Drive.doors(npc, s, {{box[1] - 4, box[2] - 4}, {box[3] + 4, box[4] + 4}}, true)) do
    if on_edge(box, door) then doors[#doors + 1] = door end
  end
  table.sort(doors, function(a, b) return distance(a, toward) < distance(b, toward) end)
  local cx, cy = (box[1] + box[3]) / 2, (box[2] + box[4]) / 2
  for _, best in ipairs(doors) do
    local ix, iy, sx, sy = 0, 0, 0, 0   -- inward, and along the wall
    if best.across == "y" then iy, sx = cy > best.y and 1 or -1, 1 else ix, sy = cx > best.x and 1 or -1, 1 end
    local side = (best.width or 4) / 2 + GARAGE_SIDE
    local p = {x = best.x + ix * GARAGE_IN + sx * side, y = best.y + iy * GARAGE_IN + sy * side}
    for r = 0, 12, 3 do
      for k = 0, (r == 0 and 0 or 7) do
        local a = k * math.pi / 4
        local q = {x = p.x + math.cos(a) * r, y = p.y + math.sin(a) * r}
        local spot = inside(box, q, 4) and s.find_non_colliding_position(name or "car", q, 2, 0.5)
        if spot and inside(box, spot, 4) and s.count_entities_filtered{area = {{spot.x - 3, spot.y - 3},
            {spot.x + 3, spot.y + 3}}, type = BELTS, limit = 1} == 0 then
          return {x = spot.x, y = spot.y}
        end
      end
    end
  end
  return nil
end

local function park_for(npc, goal)
  local c = npc.character
  if not (c and c.valid) or not inside(ring_box(npc), goal, 2) then return nil end
  local v = vehicle_near(npc)
  local _, name = vehicle_item(npc)
  return Drive.garage(npc, c.surface, goal, v and v.name or name)
end

-- Whether this trip is one to drive: long enough, a vehicle of its own (near, or in its pockets) and
-- fuel for it; not from one place in its ring to another (across its own factory), and one from its pockets
-- not put down among its buildings (out of its ring, or its base, first).
function Drive.can(npc, goal)
  local c = npc.character
  if not (c and c.valid) or distance(c.position, goal) < MIN_TRIP then return false end
  local box = ring_box(npc)
  if box and inside(box, c.position) and inside(box, goal) then return false end
  local v = vehicle_near(npc)
  -- into its ring only to park by a door: with no room left there, not driven in among its buildings
  if box and inside(box, goal, 2) and not park_for(npc, goal) then return false end
  if v then return fueled(v) or fuel_item(npc, v.name) ~= nil end
  if box and inside(box, c.position) then return false end
  if not box and npc.home and distance(c.position, npc.home) < HOME_CLEAR then return false end   -- out of the base first
  local item, name = vehicle_item(npc)
  return item ~= nil and fuel_item(npc, name) ~= nil
end

-- How fast its drives go, tiles a tick as the crow flies, boarding and doors and detours in (learned from its
-- own drives, this to start with: the range drove courses in about half the time of walking straight there).
local DRIVE_SPEED, SPEED_LEARN = 0.25, 0.3
Drive.DRIVE_SPEED = DRIVE_SPEED
local GAIN = 0.8           -- driven only when it takes at most this much of the walk's time
local DETOUR = 1.25        -- a way is this much longer than straight
local DRIVE_OVERHEAD = 300 -- ticks to get in, through a door, park and get out
local FETCH_FAR = 200      -- its parked vehicle this far off may be walked to, when that still saves time
local BRING_HOME = 40      -- going home past where it left its vehicle outside the ring: driven in from this short
-- Whether a walk of its work is better driven (M-1: the ring's sides 255 tiles long, upkeep rounds of 260
-- tiles, outposts): with a vehicle of its own and fuel for it, the time with walking to it and from where it
-- parks against the time on foot. "drive" (in it, beside it, or one in its pockets put down here), "fetch" and
-- the spot to walk to first (its parked vehicle, or the place by a door to put one down), or nil.
function Drive.worth(npc, goal)
  local c = npc.character
  if not (c and c.valid) or c.vehicle then return nil end
  local here, s = c.position, c.surface
  local box = ring_box(npc)
  local home_in = inside(box, goal, 2)
  if home_in and inside(box, here, 2) then return nil end   -- (not across its own factory)
  local walk = distance(here, goal)
  if walk < BRING_HOME then return nil end
  local v = vehicle_near(npc, FETCH_FAR)
  local item, name
  if v then name = v.name else item, name = vehicle_item(npc) end
  if not name or not (v and fueled(v)) and not fuel_item(npc, name) then return nil end
  local start = v and v.position or here
  if not v and box and inside(box, here) then
    start = Drive.garage(npc, s, goal, name)   -- put down by a door, not among its buildings
    if not start then return nil end
  elseif not v and not box and npc.home and distance(here, npc.home) < HOME_CLEAR then
    return nil
  end
  local park = home_in and Drive.garage(npc, s, goal, name) or goal
  if home_in and park == goal then return nil end   -- no room to park by a door: not driven in among its buildings
  local speed = npc.drive_speed or DRIVE_SPEED
  local on_foot = (distance(here, start) + distance(park, goal)) * DETOUR / WALK_SPEED
  local driving = on_foot + distance(start, park) / speed + DRIVE_OVERHEAD
  local bring = v and box and home_in and not inside(box, v.position) and distance(here, v.position) < 24
  if not bring and (walk < MIN_TRIP or driving > GAIN * walk * DETOUR / WALK_SPEED) then return nil end
  if distance(here, start) > BOARD + 1 then return "fetch", {x = start.x, y = start.y} end
  return "drive"
end
Drive.FETCH_FAR = FETCH_FAR

-- Where its parked vehicle stands, when it is to walk there (on foot, by the usual pathfinding) before
-- driving; nil when it is in it already or will put one down from its pockets.
function Drive.vehicle_at(npc)
  local c = npc.character
  if c.vehicle and c.vehicle.valid then return nil end
  local v = vehicle_near(npc)
  return v and {x = v.position.x, y = v.position.y} or nil
end

-- What kind of vehicle it drives (or will), as its routes know them: "tank" (a heavy one) or "car".
function Drive.kind(npc)
  local v = vehicle_near(npc)
  if v then return heavy(v) and "tank" or "car" end
  local _, name = vehicle_item(npc)
  return name and kind_of(name).heavy and "tank" or "car"
end

-- Where the drive takes it: the goal, or its parking place inside its ring when the goal is in there.
local function dest(d) return d.park or d.goal end

-- `opts`: spare (what is this near the goal it comes for: their defenses, not to be kept clear of on
-- the way), spare_nests, strict (a trip it need not make: not driven by a way not clear of them, as not
-- walked; drive13 drove a raid straight at a worm by the least bad way, and died), park (where it parks
-- instead, by a door inside its ring), job (the walk of its work this drive is for: navigation.lua).
-- A new goal for a drive under way: its route, path and the watch on its progress afresh (the leg
-- asked for ahead and the arriving at the old one too).
local function retarget(d, goal, near, opts)
  d.goal, d.near, d.route, d.path, d.request_id = {x = goal.x, y = goal.y}, near, nil, nil, nil
  d.closest, d.anchor, d.limit, d.pending = nil, nil, nil, nil
  d.arriving, d.there_since, d.next, d.next_id, d.next_none, d.tiny = nil, nil, nil, nil, nil, nil
  d.spare, d.spare_nests, d.strict, d.park, d.job, d.door = opts.spare, opts.spare_nests, opts.strict, opts.park,
    opts.job, nil
end

-- Its parking place for a goal inside its ring: by the door nearest the goal.

function Drive.start(npc, goal, near, tick, opts)
  opts = opts or {}
  opts.park = opts.park or park_for(npc, goal)
  local d = npc.drive
  if d and not d.done and not d.failed then   -- driving already: a new goal (a call home)
    if d.mend and d.mend.resume then   -- away to mend the vehicle: there after that
      d.mend.resume = {goal = {x = goal.x, y = goal.y}, near = near, spare = opts.spare,
        spare_nests = opts.spare_nests, strict = opts.strict, park = opts.park, job = opts.job}
      return
    end
    retarget(d, goal, near, opts)
    if d.phase ~= "board" and d.phase ~= "clear" and d.phase ~= "mend" then d.phase = "plan" end
    return
  end
  local c = npc.character
  npc.drive = {goal = {x = goal.x, y = goal.y}, near = near, since = tick, phase = "board", stuck = 0,
    spare = opts.spare, spare_nests = opts.spare_nests, strict = opts.strict, park = opts.park, job = opts.job,
    from = c and c.valid and {x = c.position.x, y = c.position.y} or nil}
  note(npc, string.format("start for %.0f,%.0f%s", goal.x, goal.y, opts.park and ", parking inside" or ""),
    npc.character.position)
end

-- Where it stops: short of the goal, at its parking place inside its ring, or outside its base when the goal is
-- in it and it has no ring with a door.
local function there(npc, d, here)
  if d.mend and d.mend.resume then return false end   -- away to mend it: not the trip's goal
  if d.door then return false end   -- (not in a doorway)
  if d.park then return d.arriving or distance(here, d.park) <= 8 end
  if d.arriving or distance(here, d.goal) <= math.max(8, d.near or 0) then return true end
  if ring_box(npc) then return false end
  local h = npc.home
  return h ~= nil and distance(d.goal, h) <= HOME_CLEAR and distance(here, h) <= HOME_CLEAR + 8
end

local function stop_walking(c)
  c.walking_state = {walking = false, direction = defines.direction.north}
end
local function ride(v, accel, turn)
  v.riding_state = {acceleration = defines.riding.acceleration[accel], direction = defines.riding.direction[turn]}
end
-- Given up for want of a way (or of a clear one: drive14 left it at home, and walked back from the raid),
-- the vehicle is picked up (fuel and all) to be put down again another time, not left behind; a wreck,
-- or one out of fuel, stays.
local ABORT_AHEAD, KEEP_OFF_FOES = 60, 40   -- (combat's judgment of the road ahead; no getting out this near biters)
local TAKE_BACK = {no_path = true, stuck = true, no_progress = true, too_slow = true, unsafe = true,
  biters_ahead = true, biters_at_goal = true}
local function finish(npc, failed, why)
  local d = npc.drive
  local c = npc.character
  local v = c and c.valid and c.vehicle
  -- how fast it got there, learned (a drive of its work only: a raid's waits are not the road's)
  if not failed and d.job and d.boarded_at and d.board_from and v and v.valid then
    local ticks = game.tick - d.boarded_at
    local made = distance(d.board_from, v.position)
    if ticks > 300 and made > 60 then
      local speed = math.max(0.05, math.min(0.6, made / ticks))
      npc.drive_speed = (npc.drive_speed or DRIVE_SPEED) * (1 - SPEED_LEARN) + speed * SPEED_LEARN
    end
  end
  if v and v.valid then
    pcall(function() ride(v, "braking", "straight") end)
    pcall(function() v.set_driver(nil) end)
    npc.parked = v
    if failed and TAKE_BACK[why] then
      local ok, mined = pcall(function() return c.mine_entity(v, true) end)
      if ok and mined then npc.parked = nil end
    end
  end
  if c and c.valid then stop_walking(c) end
  d.done, d.failed, d.why = not failed, failed or nil, why
  note(npc, why, c and c.valid and c.position)
  return false
end

-- The direction (8-way) toward a point, for walking to the vehicle.
local function walk_dir(from, to)
  local a = math.atan2(to.x - from.x, -(to.y - from.y))
  local k = math.floor(((a / TWO_PI) % 1) * 8 + 0.5) % 8
  local dirs = {"north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"}
  return defines.direction[dirs[k + 1]]
end

-- Into the vehicle: walk to its own one, or put one down from its pockets; fuel it; get in.
local function board(npc, d, tick)
  local c = npc.character
  if c.vehicle and c.vehicle.valid then
    stop_walking(c)
    d.phase = "plan"
    return true
  end
  local v = vehicle_near(npc)
  if not v then
    local item, name = vehicle_item(npc)
    if not item then return finish(npc, true, "no_vehicle") end
    -- put down where it has room to drive off (the range: put down in a clearing of a wood, no way out
    -- of it), walked to; else where it stands
    local room = Drive.open_spot and Drive.open_spot(c.surface, c.position, NARROW, 12)
    local spot = room and c.surface.find_non_colliding_position(name, room, 4, 0.5)
      or c.surface.find_non_colliding_position(name, c.position, 10, 0.5)
    if not spot then return finish(npc, true, "no_room_for_vehicle") end
    local main = c.get_main_inventory()
    if main.remove{name = item, count = 1} ~= 1 then return finish(npc, true, "no_vehicle") end
    local ok, e = pcall(function()
      return c.surface.create_entity{name = name, position = spot, force = npc.force, raise_built = false}
    end)
    if not (ok and e and e.valid) then
      main.insert{name = item, count = 1}
      return finish(npc, true, "vehicle_not_placed")
    end
    npc.parked = e
    v = e
  end
  if distance(v.position, c.position) > BOARD then
    d.walk_since = d.walk_since or tick
    if tick - d.walk_since > WALK_FOR then return finish(npc, true, "vehicle_out_of_reach") end
    c.walking_state = {walking = true, direction = walk_dir(c.position, v.position)}
    return true
  end
  stop_walking(c)
  refuel(npc, v)
  if not fueled(v) then return finish(npc, true, "no_fuel") end
  load_guns(npc, v)
  local ok = pcall(function() v.set_driver(c) end)
  if not ok or v.get_driver() ~= c then return finish(npc, true, "board_rejected") end
  npc.parked = v
  d.phase = "plan"
  d.boarded_at, d.board_from = d.boarded_at or tick, d.board_from or {x = v.position.x, y = v.position.y}
  return true
end

-- The nearest gun that stands and is not of its own side or its friends, within CLEAR_OF.
local function shooter(npc, v, here)
  local best, best_d
  for _, e in ipairs(v.surface.find_entities_filtered{type = GUNS, position = here, radius = CLEAR_OF}) do
    local f = e.valid and e.force
    if f and f ~= npc.force and not npc.force.get_friend(f) and not npc.force.get_cease_fire(f) then
      local dd = distance(e.position, here)
      if not best or dd < best_d then best, best_d = e, dd end
    end
  end
  return best
end

-- Where a leg's path starts: the pathfinder finds none from a spot its box does not fit on (drive8: the
-- car stopped with a tree and the shore under its box, and every leg from there was "no way", even one
-- 30 tiles over open ground), so from the nearest spot a few tiles off that it does fit on.
local function fits(surface, p, h)
  local area = {{p.x - h, p.y - h}, {p.x + h, p.y + h}}
  return surface.count_tiles_filtered{area = area, collision_mask = "water_tile", limit = 1} == 0
    and surface.count_entities_filtered{area = area, type = {"tree", "simple-entity", "cliff"}, limit = 1} == 0
end
-- An open spot near p the wide box fits on, or nil (drive12: a leg ended where only the car fitted,
-- between two big rocks, and the path into it wedged the car there).
local function open_spot(s, p, h, reach)
  if not s.count_tiles_filtered then return nil end
  if fits(s, p, h) then return p end
  for r = 3, reach, 3 do
    for k = 0, 7 do
      local a = k * math.pi / 4
      local q = {x = p.x + math.sin(a) * r, y = p.y - math.cos(a) * r}
      if fits(s, q, h) then return q end
    end
  end
end

-- (behind it first, where it came from: drive10, a path begun on the far side of the big rock it had
-- stuck on, and driven into it again)
local RING = {4, 3, 5, 2, 6, 1, 7, 0}
local function path_start(v, here, h)
  h = h or NARROW
  local s = v.surface
  if not s.count_tiles_filtered or fits(s, here, h) then return here, true end
  local o = v.orientation or 0
  for r = 3, 9, 3 do
    for _, k in ipairs(RING) do
      local a = (o + k / 8) * TWO_PI
      local p = {x = here.x + math.sin(a) * r, y = here.y - math.cos(a) * r}
      if fits(s, p, h) then return p, true end
    end
  end
  return here, false
end
Drive.path_start = path_start
Drive.open_spot = open_spot

-- The end of a leg from `from`: the route's next point, LEG along it at most, on open ground the
-- vehicle fits on (drive5: legs ending among trees found no path); and which point of the route it is.
-- (`far`: the same spot found no way twice, the route insisting on it: the game's own pathfinder, which
-- knows the land tile by tile, asked for a way to a point well on along the route instead, as a player
-- who finds no way on looks further ahead; the range, on a natural map, a route across a lake its
-- survey did not see)
local FAR_POINTS, FAR_LEG = 2, 3
local function leg_end(d, r, from, v, far)
  local k = r.k
  while r.points[k + 1] and distance(from, r.points[k]) <= 12 do k = k + 1 end
  if far then k = math.min(#r.points, k + FAR_POINTS) end
  local p = r.points[k] or dest(d)
  local dd = distance(from, p)
  local leg = LEG * (far and FAR_LEG or d.leg_scale or 1)
  if dd > leg then p = {x = from.x + (p.x - from.x) / dd * leg, y = from.y + (p.y - from.y) / dd * leg} end
  local q = open_spot(v.surface, p, WIDE, 12)
  -- (not one back by where it stands: the range went leg after leg of nothing, the open spot for a
  -- point among trees beside the car)
  if q and distance(from, p) > 16 and distance(from, q) < distance(from, p) / 2 then q = nil end
  p = q or v.surface.find_non_colliding_position(v.name, p, 12, 1) or p
  return {x = p.x, y = p.y}, k
end
-- Its last legs, where from and where to (looked at after a run), and whether it is going back and
-- forth between two places: each leg somewhere, none further on, nothing said till the trip ran out
-- (the range on a natural map, and drive16 near Dax).
local LEGS_KEPT = 12
local function begin_leg(npc, d, from, to, tick)
  d.leg_from = {x = from.x, y = from.y}
  local legs = npc.drive_legs or {}
  npc.drive_legs = legs
  legs[#legs + 1] = {t = tick, fx = math.floor(from.x), fy = math.floor(from.y), x = math.floor(to.x), y = math.floor(to.y)}
  while #legs > LEGS_KEPT do table.remove(legs, 1) end
  local n = #legs
  if n >= 3 and distance(legs[n - 2], legs[n]) < 12 and distance(legs[n - 1], legs[n]) > 20 then
    d.shuttles = (d.shuttles or 0) + 1
  else
    d.shuttles = 0
  end
  if d.shuttles < 2 then return false end
  d.shuttles = 0
  note(npc, "back and forth, blocked", to)
  Routes.block(npc, to, nil, 16)
  d.route, d.next, d.next_id = nil, nil, nil
  return true
end

-- A path for the vehicle asked of the game; its id.
local function ask(npc, v, from, to, h)
  local start, fitted = path_start(v, from, h)
  if npc.drive then npc.drive.asked_from = {x = start.x, y = start.y, fitted = fitted} end
  local ok, id = pcall(function()
    return v.surface.request_path{bounding_box = {{-h, -h}, {h, h}}, collision_mask = v.prototype.collision_mask,
      start = start, goal = to, radius = 4, force = npc.force, entity_to_ignore = v,
      can_open_gates = true,
      pathfind_flags = {cache = false, prefer_straight_paths = true, allow_destroy_friendly_entities = false}}
  end)
  if not (ok and id) then return nil end
  storage.drive_paths = storage.drive_paths or {}
  storage.drive_paths[id] = npc.id
  return id
end

-- A leg across walls: from outside its ring to outside it, round the ring by the corner that makes the way
-- shortest (never through its own factory); across a wall of its own or its friends, to the door that makes
-- the way shortest (d.door: lined up in front of it, then through); across a wall with no door it knows, by
-- the pathfinder's tightest box (which finds a gap if there is one). The leg's new end, or nil.
function Drive.cross(npc, d, v, here, p)
  local s = v.surface
  local goal = dest(d)
  local box = ring_box(npc)
  -- (out there both: a point of its route inside its ring, which the route knows nothing of, is passed over
  -- for the first beyond it; the range, round the ring by one corner then the other for a point on the wall)
  local over   -- (the leg's new end, when it is passed over)
  if box and not inside(box, here) and not inside(box, goal) and inside(box, p, -2) then
    local r = d.route
    while r and r.points[r.k + 1] and inside(box, r.points[r.k], -2) do r.k = r.k + 1 end
    p = r and r.points[r.k] and not inside(box, r.points[r.k], -2) and r.points[r.k] or goal
    local dd = distance(here, p)
    if dd > LEG and not enters(box, here, p) then
      p = {x = here.x + (p.x - here.x) / dd * LEG, y = here.y + (p.y - here.y) / dd * LEG}
    end
    over = {x = p.x, y = p.y}
  end
  -- (the last leg only as far as where it stops: a wall it comes to work on is no wall to cross)
  local to, near, left = p, d.park and 0 or (d.near or 0), distance(here, p)
  if near > 0 and distance(p, goal) <= near + 2 and left > near then
    to = {x = here.x + (p.x - here.x) * (left - near) / left, y = here.y + (p.y - here.y) * (left - near) / left}
  end
  local out_there = box and not inside(box, here) and not inside(box, goal)
  if out_there and goes_round(box, here, to, distance(p, goal) <= near + 2) then
    local best, best_cost
    for _, q in ipairs(corners(box)) do
      local bad = false
      for _, b in ipairs(d.bad_corners or {}) do if distance(b, q) < 1 then bad = true end end
      if not bad and distance(here, q) > 16 and not enters(box, here, q) then
        local cost = distance(here, q) + round_about(box, q, goal)
        if not best or cost < best_cost then best, best_cost = q, cost end
      end
    end
    if best then
      -- an open spot by the corner (on a real map the corner itself may be in a lake or a wood: the range,
      -- no way there, the same corner asked for till the trip gave up)
      local spot = open_spot(s, best, WIDE, 12) or best
      d.corner = {x = best.x, y = best.y, at = {x = spot.x, y = spot.y}}
      note(npc, string.format("round its ring (for %.0f,%.0f)", p.x, p.y), spot)
      return {x = spot.x, y = spot.y}
    end
    -- no corner left to go by: never through its factory by a door (Codex); the pathfinder's way round it
    return over
  end
  local crossing = walled(npc, s, here, to)
  -- Into or out of its ring, a leg ending just short of the wall crosses now: met at speed at the leg's end,
  -- the wall a few tiles on, a car ran into it while it waited for the next leg's path (the engine check).
  -- (not when it may stop short of the wall anyway: a goal just outside it, reached from inside)
  if not crossing and box and inside(box, here) ~= inside(box, goal) and left > 1 and to == p then
    local k = (left + WALL_AHEAD) / left
    crossing = walled(npc, s, here, {x = here.x + (p.x - here.x) * k, y = here.y + (p.y - here.y) * k})
  end
  if not crossing then return over end
  d.crossings = (d.crossings or 0) + 1
  if d.crossings > MAX_CROSSINGS then return over end
  local through = door_for(npc, s, here, to, goal, v)
  if through then
    d.door = through
    note(npc, "to the door", through.door)
    return through.far or through.a
  end
  d.tight = true
  note(npc, "a wall and no door it knows: the tightest way", to)
  return over
end

-- The next leg: the route (planned once, for the vehicle, and again after trouble), then a path for
-- the vehicle to the point up to LEG along it.
local function plan(npc, d, v, tick)
  local here = v.position
  if d.door and d.door.through then   -- in the doorway: through it first, whatever else
    d.path, d.pk, d.leg, d.phase = {d.door.b}, 1, d.door.b, "drive"
    return true
  end
  if not d.route then
    if Routes.prepare and not Routes.prepare(npc, dest(d), tick) then
      ride(v, "braking", "straight")
      return true   -- the land along the way is still being made
    end
    -- (the same route looked at again while its land is made, not planned anew each time: drive11
    -- planned it ten times over waiting; planned again once with what is there)
    -- (planned a slice a tick: a far trip through nest country took seconds at once, the game held still;
    -- meanwhile it waits, or among biters drives on)
    local route = d.pending or Routes.request(npc, dest(d), d.spare, d.spare_nests, 0, Drive.kind(npc))
    if not route then
      if d.threat and Drive.keep_going then Drive.keep_going(d, v, here) else ride(v, "braking", "straight") end
      return true
    end
    if Routes.route_ready and not Routes.route_ready(npc, here, route.points) then
      if not d.land_until then
        note(npc, "waiting for the land", here)
        d.land_until = tick + LAND_WAIT
      end
      if tick < d.land_until then
        d.pending, d.retry_at, d.phase = route, tick + 120, "plan"
        ride(v, "braking", "straight")
        return true
      end
    end
    if d.pending then
      route = Routes.request(npc, dest(d), d.spare, d.spare_nests, 0, Drive.kind(npc))
      if not route then
        if d.threat and Drive.keep_going then Drive.keep_going(d, v, here) else ride(v, "braking", "straight") end
        return true
      end
    end
    d.route, d.pending, d.land_until = route, nil, nil
    note(npc, string.format("route of %d points%s", #route.points, route.safe and "" or ", not clear"), here)
    if not route.safe and d.strict then return finish(npc, true, "unsafe") end
    d.look_at = tick + LOOK_EVERY
  end
  local r = d.route
  local door = d.door
  local lined = door and (door.far or door.a)
  if door and distance(here, lined) <= DOOR_AT then
    -- Lined up in front of the door: straight through it, slowly (the gate opening), no pathfinder; on from
    -- the far side by a route planned afresh (the old one may lead back through the wall).
    door.through = true
    d.path = {door.line, {x = door.door.x, y = door.door.y}, door.b}
    if door.far then table.insert(d.path, 1, door.a) end
    d.pk, d.leg, d.phase = 1, door.b, "drive"
    d.leg_from, d.careful_until = {x = here.x, y = here.y}, tick + DOOR_CAREFUL
    d.next, d.next_id, d.next_none, d.next_at = nil, nil, nil, nil
    note(npc, "through the door", door.door)
    return true
  end
  local p, k
  if door then
    p = lined
  else
    p, k = leg_end(d, r, here, v, d.far)
    r.k = k
    p = Drive.cross(npc, d, v, here, p) or p
    if (d.crossings or 0) > MAX_CROSSINGS then return finish(npc, true, "doors") end
  end
  d.leg = p
  if begin_leg(npc, d, here, p, tick) then
    d.phase, d.retry_at = "plan", tick + 1
    ride(v, "braking", "straight")
    return true
  end
  d.path, d.pk = nil, 1
  d.next, d.next_id, d.next_none, d.next_at = nil, nil, nil, nil   -- (asked from where it no longer is)
  if tick >= (d.no_straight_until or 0) and Drive.straight_ok(v, here, d.leg) then
    d.path, d.phase = {d.leg}, "drive"
    return true
  end
  d.request_id = ask(npc, v, here, d.leg, d.tight and TIGHT or d.narrow and NARROW or WIDE)
  d.asked = tick
  d.phase = "wait"
  return true
end

function Drive.on_path_finished(event)
  local id = storage.drive_paths and storage.drive_paths[event.id]
  if not id then return false end
  storage.drive_paths[event.id] = nil
  local npc = storage.npcs and storage.npcs[id]
  local d = npc and npc.drive
  if d and d.next_id == event.id then   -- the leg after this one
    d.next_id = nil
    if event.path and #event.path > 0 then
      local path = {}
      for _, w in ipairs(event.path) do path[#path + 1] = {x = w.position.x, y = w.position.y} end
      d.next = {path = path, leg = d.next_leg, k = d.next_k}
    elseif event.try_again_later then
      d.next_at = game.tick + 30
    else
      d.next_none = true   -- planned at the leg's end, as before
    end
    return true
  end
  if not (d and d.request_id == event.id) then return true end
  d.request_id = nil
  if event.try_again_later then
    -- the pathfinder was busy (drive6: counted as "no way" and given up on open ground): the same leg
    -- asked again in a moment, no failure counted; busy for long, straight for it
    d.busy = (d.busy or 0) + 1
    d.busy_ticks = (d.busy_ticks or 0) + 1
    if d.busy == 1 then note(npc, "pathfinder busy", d.leg) end
    if d.busy > BUSY_MAX then
      d.busy = nil
      d.path, d.pk, d.phase = {d.leg}, 1, "drive"
      return true
    end
    d.phase, d.retry_at = "plan", game.tick + 30
    return true
  end
  d.busy = nil
  if event.path and #event.path > 0 then
    local path = {}
    for _, w in ipairs(event.path) do path[#path + 1] = {x = w.position.x, y = w.position.y} end
    d.path = path
    d.leg_scale, d.no_path, d.narrow, d.far, d.tight = 1, nil, nil, nil, nil
  elseif not d.narrow then
    -- no way for the wide box: the same leg for the narrow one, no failure counted
    d.narrow = true
    d.phase = "plan"
    return true
  else
    -- no room to start a path where it stands: the trees or rocks round it cut down first
    local f = d.asked_from
    local v = npc.character and npc.character.valid and npc.character.vehicle
    if f and f.fitted == false and v and v.valid and Drive.clear_around(npc, d, v, game.tick) then return true end
    if f and f.fitted == false and not d.tight then
      d.tight, d.phase = true, "plan"   -- nothing to cut (water): the tight box, no failure counted
      return true
    end
    -- no way to the corner of its ring it was going round by: by another corner (not counted)
    if d.corner and d.leg and distance(d.corner.at, d.leg) < 0.5 then
      d.bad_corners = d.bad_corners or {}
      d.bad_corners[#d.bad_corners + 1] = {x = d.corner.x, y = d.corner.y}
      d.corner, d.phase = nil, "plan"
      return true
    end
    -- no path for the leg (a forest, a lake): a shorter one; only when even that fails, straight for it
    d.no_path = (d.no_path or 0) + 1
    d.leg_scale = (d.leg_scale or 1) * 0.5
    d.phase = "plan"
    if d.leg_scale < 0.2 then
      -- not even a short way on (drive4: driven straight for it, into a lake shore): that spot is
      -- blocked and the route planned again round it; after many, it gives up and walks
      if d.no_path > MAX_NO_PATH then finish(npc, true, "no_path") return true end
      local f = d.asked_from
      note(npc, f and string.format("no way from %.0f,%.0f%s, blocked", f.x, f.y,
        f.fitted == false and " (no room to start)" or "") or "no way, blocked", d.leg)
      -- (not over where it stands: the range, the car within the circle of the spot it blocked, and that
      -- spot chosen again and again)
      local c = npc.character
      local r = c and c.valid and math.max(8, math.min(24, distance(c.position, d.leg) - 8)) or 24
      Routes.block(npc, d.leg, nil, r)
      d.far = d.blocked_at ~= nil and distance(d.blocked_at, d.leg) < 12 and not d.far
      d.blocked_at = {x = d.leg.x, y = d.leg.y}
      d.route, d.leg_scale, d.narrow = nil, 1, d.far or nil
    end
    return true
  end
  d.pk = 1
  d.phase = "drive"
  return true
end

-- Whether something it would hit is within NEAR_PATH of its path, from where it is `reach` tiles on.
function Drive.straight_ok(v, from, to)
  local s = v.surface
  if not s.count_tiles_filtered then return false end
  local kinds = IN_THE_WAY[heavy(v) and "tank" or "car"]
  local len = distance(from, to)
  local n = math.max(1, math.floor(len / 3))
  local trees = 0
  for i = 1, n do
    local p = {x = from.x + (to.x - from.x) * i / n, y = from.y + (to.y - from.y) * i / n}
    if s.count_entities_filtered{type = kinds, position = p, radius = ROCK_BERTH, limit = 1} > 0 then return false end
    if s.count_tiles_filtered{position = p, radius = NEAR_PATH, collision_mask = "water_tile", limit = 1} > 0 then
      return false
    end
    if not heavy(v) then trees = trees + s.count_entities_filtered{type = "tree", position = p, radius = 1.6} end
  end
  return trees <= len * TREES_PER_TILE
end

local function close_ahead(v, path, pk, here, reach)
  local s = v.surface
  local kinds = IN_THE_WAY[heavy(v) and "tank" or "car"]
  local from, gone, last, trees = here, 0, here, 0
  for i = pk, #path do
    local p = path[i]
    gone = gone + distance(from, p)
    from = p
    if distance(p, last) >= 2.5 or i == #path then
      last = p
      if s.count_entities_filtered{type = kinds, position = p, radius = NEAR_PATH, limit = 1} > 0 then return true, trees end
      if s.count_tiles_filtered and s.count_tiles_filtered{position = p, radius = NEAR_PATH,
          collision_mask = "water_tile", limit = 1} > 0 then return true, trees end
      if not heavy(v) and gone <= TREES_AHEAD then
        trees = trees + s.count_entities_filtered{type = "tree", position = p, radius = 1.6}
      end
    end
    if gone >= math.max(reach, TREES_AHEAD) then break end
  end
  return false, trees
end

-- The way back out, along where it drove (CRUMB): how to turn the wheel backing toward the spot a few
-- tiles behind it on its way (reversing, the wheel turns it the other way); nil when it has none.
local function back_along(d, v, here)
  local cr = d.crumbs
  while cr and cr[1] and distance(here, cr[#cr]) < 2 * CRUMB do table.remove(cr) end
  local aim = cr and cr[#cr]
  if not aim then return nil end
  local want = (math.atan2(aim.x - here.x, -(aim.y - here.y)) / TWO_PI) % 1
  local off = (want - (v.orientation + 0.5) + 0.5) % 1 - 0.5
  return off > 0.02 and "left" or off < -0.02 and "right" or "straight"
end

-- Biters near (it does not stop among them) and the nearest thing to shoot within the gun's reach.
local SHOOT_AT = {"unit", "turret"}
local function look_for_threat(npc, d, v, tick)
  local s, p = v.surface, v.position
  d.threat = s.count_entities_filtered{position = p, radius = THREAT_RADIUS, force = "enemy", type = "unit", limit = 1} > 0
  local best, best_d
  for _, e in ipairs(s.find_entities_filtered{position = p, radius = GUN_REACH, force = "enemy", type = SHOOT_AT}) do
    local dd = distance(e.position, p)
    if not best or dd < best_d then best, best_d = e, dd end
  end
  d.target = best
end
local function shoot(npc, d)
  local c = npc.character
  local t = d.target
  if t and t.valid then
    c.selected = t
    c.shooting_state = {state = defines.shooting.shooting_selected, position = t.position}
  elseif d.was_shooting then
    c.shooting_state = {state = defines.shooting.not_shooting, position = c.position}
  end
  d.was_shooting = t and t.valid or nil
end
-- On among biters while it waits for a path: steering for where it was going, not standing still.
local function keep_going(d, v, here)
  local aim = d.leg or dest(d)
  local accel, turn = Drive.steer(v.orientation, v.speed, here, aim, 0.3, kind_of(v.name).turn)
  ride(v, accel, turn)
end
Drive.keep_going = keep_going

-- The rock or tree touching the vehicle, nearest first.
local function touching(v, d)
  local best, best_d
  for _, e in ipairs(v.surface.find_entities_filtered{position = v.position, radius = CLEAR_NEAR,
      type = {"tree", "simple-entity"}}) do
    if e.valid and e.minable and e.force and e.force.name == "neutral"
        and not (d and d.unclearable and d.unclearable[string.format("%.1f,%.1f", e.position.x, e.position.y)]) then
      local dd = distance(e.position, v.position)
      if not best or dd < best_d then best, best_d = e, dd end
    end
  end
  return best
end
local function clearing(npc, d, tick)
  local c, k = npc.character, d.clear
  if k and k.target.valid and tick >= k.until_tick then
    note(npc, string.format("could not clear %s, %.1f off, %s, mining %s", k.target.name,
      distance(c.position, k.target.position), c.vehicle and "in the vehicle" or "on foot",
      tostring(c.mining_state and c.mining_state.mining)), k.target.position)
    -- (not that one again: the range, eight times at a tree it could not walk up to for others)
    d.unclearable = d.unclearable or {}
    d.unclearable[string.format("%.1f,%.1f", k.target.position.x, k.target.position.y)] = true
  end
  if not (k and k.target.valid) or tick >= k.until_tick then
    c.mining_state = {mining = false}
    d.clear, d.phase, d.walk_since, d.progress_at = nil, "board", nil, tick
    return board(npc, d, tick)
  end
  -- (mined only from close by, as walking chops: the range stood 3.7 tiles off a big sand rock and the
  -- game would not start; asked to mine from out of reach every tick, it never walked nearer either)
  local box = k.target.bounding_box
  local size = box and math.max(box.right_bottom.x - box.left_top.x, box.right_bottom.y - box.left_top.y) or 1
  local dd = distance(c.position, k.target.position)
  if dd <= (k.target.type == "tree" and 1.3 or math.max(2.6, size / 2 + 1.2)) then
    stop_walking(c)
    c.selected = k.target
    c.mining_state = {mining = true, position = k.target.position}
  else
    c.mining_state = {mining = false}
    -- (and round to the side when it gets no nearer: the range, 4.5 tiles off a huge rock with the car
    -- in between)
    if not k.best or dd < k.best - 0.1 then k.best, k.best_at = dd, tick end
    -- no nearer for a second: another one touching the vehicle, the nearest to where it stands
    if tick - k.best_at > 60 and k.vehicle and k.vehicle.valid then
      local other, other_d
      for _, e in ipairs(c.surface.find_entities_filtered{position = k.vehicle.position, radius = CLEAR_NEAR,
          type = {"tree", "simple-entity"}}) do
        if e.valid and e ~= k.target and e.minable and e.force and e.force.name == "neutral" then
          local od = distance(e.position, c.position)
          if od < dd - 0.5 and (not other or od < other_d) then other, other_d = e, od end
        end
      end
      if other then k.target, k.best, k.best_at = other, nil, tick; return true end
    end
    local dir = walk_dir(c.position, k.target.position)
    if tick - k.best_at > 30 then
      dir = (dir + 4) % 16
      if tick - k.best_at > 60 then k.best_at = tick end
    end
    c.walking_state = {walking = true, direction = dir}
  end
  return true
end

local function start_clear(npc, d, v, e, tick, why)
  d.clears = (d.clears or 0) + 1
  note(npc, why .. e.name, e.position)
  ride(v, "braking", "straight")
  pcall(function() v.set_driver(nil) end)
  npc.parked = v
  d.clear, d.phase = {target = e, until_tick = tick + CLEAR_FOR, vehicle = v}, "clear"
end
-- No room to start a path where it stands (the range: a car in a clearing of a wood): the nearest tree
-- or rock about it cut down, and again, until there is.
function Drive.clear_around(npc, d, v, tick)
  if (d.clears or 0) >= MAX_CLEARS or d.threat or (d.mend and d.mend.step == "away") then return false end
  local best, best_d
  for _, e in ipairs(v.surface.find_entities_filtered{position = v.position, radius = NARROW + 2,
      type = {"tree", "simple-entity"}}) do
    if e.valid and e.minable and e.force and e.force.name == "neutral"
        and not (d.unclearable and d.unclearable[string.format("%.1f,%.1f", e.position.x, e.position.y)]) then
      local dd = distance(e.position, v.position)
      if not best or dd < best_d then best, best_d = e, dd end
    end
  end
  if not best then return false end
  start_clear(npc, d, v, best, tick, "no room to start, clearing ")
  return true
end

-- Who would hurt it: the biters, and any side its own is at war with.
local function hostile(npc, f)
  if not f or f == npc.force or f.name == "neutral" then return false end
  if f.name == "enemy" then return true end
  local mine = npc.force
  return not (mine.get_friend and mine.get_friend(f)) and not (mine.get_cease_fire and mine.get_cease_fire(f))
end
local FOES = {"unit", "character", "unit-spawner"}
local function foes_near(npc, s, p, r)
  local out = {}
  for _, e in ipairs(s.find_entities_filtered{position = p, radius = r, type = FOES}) do
    if e.valid and hostile(npc, e.force) then out[#out + 1] = e end
  end
  return out
end
-- Nothing about to hurt it: no biter or enemy near, no worm or turret in reach.
local function calm(npc, v, here)
  return #foes_near(npc, v.surface, here, SAFE_RADIUS) == 0 and not shooter(npc, v, here)
end
-- A repair pack: in its pockets, else in the vehicle's trunk.
local function pack(npc, v)
  local inv = npc.character.get_main_inventory()
  local stack = inv and inv.find_item_stack and inv.find_item_stack(PACK)
  if stack and stack.valid_for_read then return stack end
  local trunk = defines.inventory and v and v.valid and v.get_inventory and v.get_inventory(defines.inventory.car_trunk)
  stack = trunk and trunk.find_item_stack and trunk.find_item_stack(PACK)
  return stack and stack.valid_for_read and stack or nil
end
local function repair_rate(v)
  local speed, mod = 2, 1
  pcall(function() speed = prototypes.item[PACK].speed or speed end)
  pcall(function() mod = v.prototype.repair_speed_modifier or mod end)
  return speed * mod
end

-- Where it heads to mend it: away from them (most from the gun that hit it) and back the way it came;
-- nil when there is nothing to keep away from, or the trip's own way leads away already (it drives on
-- till it is clear).
local function retreat_point(npc, d, v, here)
  local sx, sy, n = 0, 0, 0
  for _, e in ipairs(foes_near(npc, v.surface, here, SAFE_RADIUS + 20)) do
    sx, sy, n = sx + e.position.x, sy + e.position.y, n + 1
  end
  local gun = shooter(npc, v, here)
  if gun then sx, sy, n = sx + 4 * gun.position.x, sy + 4 * gun.position.y, n + 4 end
  if n == 0 then return nil end
  local centre = {x = sx / n, y = sy / n}
  local len = distance(here, centre)
  local ax, ay
  if len < 0.5 then   -- in the midst of them: behind it
    local o = v.orientation * TWO_PI
    ax, ay = -math.sin(o), math.cos(o)
  else
    ax, ay = (here.x - centre.x) / len, (here.y - centre.y) / len
  end
  local g = dest(d)
  local gl = distance(here, g)
  if not d.mend.resume and gl > 1 and (ax * (g.x - here.x) + ay * (g.y - here.y)) / gl > 0.3 then return nil end
  local from = d.from
  if from and distance(here, from) > 16 then
    local fl = distance(here, from)
    ax, ay = ax + 0.5 * (from.x - here.x) / fl, ay + 0.5 * (from.y - here.y) / fl
  end
  local l = math.sqrt(ax * ax + ay * ay)
  return {x = here.x + ax / l * RETREAT, y = here.y + ay / l * RETREAT}
end
local function retreat(npc, d, v, here)
  local m = d.mend
  local p = retreat_point(npc, d, v, here)
  if not p then return end
  if not m.resume then
    m.resume = {goal = d.goal, near = d.near, spare = d.spare, spare_nests = d.spare_nests, strict = d.strict,
      park = d.park, job = d.job}
  end
  retarget(d, p, 0, {job = d.job})
  d.phase = "plan"
  note(npc, "away to mend it", p)
end
-- Mended, or given up: on with the trip.
local function resume(d)
  local m = d.mend
  d.mend, d.hp = nil, nil
  if m and m.resume then retarget(d, m.resume.goal, m.resume.near, m.resume) end
end
local function off_again(npc, d, v, here, why)
  local m = d.mend
  m.tries = m.tries + 1
  if m.tries > MAX_RETREATS then
    note(npc, "could not mend it", here)
    d.mend_off = true
    resume(d)
    d.phase = "plan"
    return
  end
  note(npc, why, here)
  m.step, m.stop_at, m.still_at, m.calm_since = "away", nil, nil, nil
  retreat(npc, d, v, here)
end

-- In the vehicle, every tick: whether to mend it, and how far it is with that (true: it has the tick,
-- stopping or stopped; false: it drives on).
local function mend(npc, d, v, here, tick)
  local m = d.mend
  if not m then
    if d.mend_off or not (v.health and v.max_health) or v.health >= v.max_health * MEND_AT then return false end
    if tick < (d.pack_at or 0) then return false end
    if not pack(npc, v) then d.pack_at = tick + 300; return false end
    m = {tries = 0, step = "away"}
    d.mend = m
    note(npc, string.format("hurt, %d%% left: to mend it", math.floor(100 * v.health / v.max_health)), here)
    retreat(npc, d, v, here)
  end
  if m.back_in then   -- back in from mending it, something coming: away
    m.back_in = nil
    off_again(npc, d, v, here, "back in, away")
    if not d.mend then return false end
  end
  if m.step == "away" then
    local arrived = m.resume and (d.arriving or distance(here, dest(d)) <= 12)
    if not arrived and ((d.hit_at and tick - d.hit_at < QUIET_FOR) or tick < (m.look_at or 0)) then return false end
    m.look_at = tick + 10
    if not calm(npc, v, here) then
      if arrived then off_again(npc, d, v, here, "still not clear of them, farther") end
      return false
    end
    m.step, m.stop_at = "stop", tick
    note(npc, "clear of them, stopping to mend it", here)
  end
  -- stopping, then a while in the seat, its gun on whatever followed (hit meanwhile: on, away)
  if d.hit_at and d.hit_at >= m.stop_at then
    off_again(npc, d, v, here, "hit as it stood, away")
    return false
  end
  ride(v, "braking", "straight")
  if math.abs(v.speed) > 0.01 then return true end
  m.still_at = m.still_at or tick
  if tick >= (m.look_at or 0) then
    m.look_at = tick + 10
    if calm(npc, v, here) then m.calm_since = m.calm_since or tick else m.calm_since = nil end
  end
  if not m.calm_since then
    if tick - m.still_at > WAIT_MAX then off_again(npc, d, v, here, "something about, farther") return false end
    return true
  end
  if tick - m.calm_since < WATCH_FOR then return true end
  local c = npc.character
  pcall(function() v.set_driver(nil) end)
  npc.parked = v
  m.step, m.vehicle, m.c_hp, m.out_at, m.rate = "out", v, c.health, tick, repair_rate(v)
  d.phase = "mend"
  note(npc, "out to mend it", here)
  return true
end

-- On foot beside it, mending it with packs: a pack's worth of health for its durability.
local function mending(npc, d, tick)
  local c, m = npc.character, d.mend
  local v = m and m.vehicle
  if not (v and v.valid) then
    resume(d)
    d.phase = "board"
    return board(npc, d, tick)
  end
  local hurt = m.c_hp and c.health < m.c_hp - 0.5
  m.c_hp = c.health
  if hurt or tick >= (m.look_at or 0) then
    m.look_at = tick + 10
    if hurt or #foes_near(npc, c.surface, c.position, THREAT_RADIUS) > 0 or shooter(npc, v, c.position) then
      note(npc, "mending broken off, back in", c.position)
      m.step, m.vehicle, m.back_in = "away", nil, true
      d.phase = "board"
      return board(npc, d, tick)
    end
  end
  local stack = pack(npc, v)
  if not stack or v.health >= v.max_health * MEND_TO or tick - m.out_at > WALK_FOR then
    note(npc, string.format("mended, %d%%", math.floor(100 * v.health / v.max_health)), v.position)
    d.mends = (d.mends or 0) + 1
    resume(d)
    d.phase = "board"
    return board(npc, d, tick)
  end
  if distance(c.position, v.position) > REPAIR_REACH then
    c.walking_state = {walking = true, direction = walk_dir(c.position, v.position)}
    return true
  end
  stop_walking(c)
  local heal = math.min(m.rate or 2, v.max_health - v.health)
  v.health = v.health + heal
  stack.drain_durability(heal)
  return true
end

-- Every tick while it drives: true while it owns the body.
function Drive.tick(npc, tick)
  local d = npc.drive
  if not d or d.done or d.failed then return false end
  local c = npc.character
  if not (c and c.valid) then npc.drive = nil; return false end
  -- where its time goes, by what it is doing (looked at on the driving range)
  local doing = d.flee and "flee" or d.backing and "backing" or d.mend and d.mend.step == "stop" and "mend" or d.phase
  d.spent = d.spent or {}
  d.spent[doing] = (d.spent[doing] or 0) + 1
  if d.phase == "board" then return board(npc, d, tick) end
  if d.phase == "clear" then return clearing(npc, d, tick) end
  if d.phase == "mend" then return mending(npc, d, tick) end
  local v = c.vehicle
  if not (v and v.valid) then d.phase = "board"; return board(npc, d, tick) end
  if v.health and v.max_health and v.health < v.max_health * BAIL_HEALTH and not d.flee and not d.mend
      and math.abs(v.speed) < 0.01 and d.hit_at and tick - d.hit_at < 60 then
    return finish(npc, true, "vehicle_wrecked")
  end
  if tick % 300 == 0 then refuel(npc, v) end
  if not fueled(v) then refuel(npc, v); if not fueled(v) then return finish(npc, true, "no_fuel") end end
  local here = v.position
  local to_goal = distance(here, dest(d))
  if not (d.flee and d.flee.back) and not (d.backing and d.backing.along) then
    local cr = d.crumbs or {}
    d.crumbs = cr
    if not cr[#cr] or distance(here, cr[#cr]) >= CRUMB then
      cr[#cr + 1] = {x = here.x, y = here.y}
      if #cr > CRUMBS then table.remove(cr, 1) end
    end
  end
  -- Biters it would lose to on the road ahead or where it is going (combat.lua judges, as on foot, O-8): it stops
  -- short and goes on on foot, where its fights keep it clear of them, instead of driving in among them. (Not while
  -- fleeing or backing off a gun: that is already getting it away.)
  -- (an attack on nests is a fight its offense chose: not stopped short of it; the goal judged only once within reach;
  -- with biters about the vehicle, it first drives away from them and gets out only once clear, the review)
  if d.away then
    if tick < d.away.until_tick then
      local away = {x = here.x + (here.x - d.away.from.x), y = here.y + (here.y - d.away.from.y)}
      local accel, turn = Drive.steer(v.orientation, v.speed, here, away, 0.3, kind_of(v.name).turn)
      ride(v, accel, turn)
      return true
    end
    if c.surface.count_entities_filtered{position = here, radius = KEEP_OFF_FOES, force = "enemy", type = "unit",
        limit = 1} == 0 then
      return finish(npc, true, d.away.why)
    end
    d.away.until_tick = tick + 120
    return true
  end
  if Drive.abort_check and not d.spare_nests and not d.flee and not d.backing and tick >= (d.abort_look or 0) then
    d.abort_look = tick + 30
    local o = v.orientation * TWO_PI
    local ahead = {x = here.x + math.sin(o) * ABORT_AHEAD, y = here.y - math.cos(o) * ABORT_AHEAD}
    local goal = to_goal <= ABORT_AHEAD + 56 and dest(d) or nil
    local ok, why = pcall(Drive.abort_check, npc, tick, goal, ahead)
    if ok and why then
      if c.surface.count_entities_filtered{position = here, radius = KEEP_OFF_FOES, force = "enemy", type = "unit",
          limit = 1} > 0 then
        d.away = {from = ahead, why = why, until_tick = tick + 180}
        return true
      end
      return finish(npc, true, why)
    end
  end
  -- There (short of the goal, where it means to stand, or outside its base): stop and get out.
  if there(npc, d, here) then
    -- not out among biters (the range: a car chased to its goal by them, its driver out and bitten to
    -- death): on about a while longer, shooting, till they are left behind or dead
    if d.threat and tick - (d.there_since or tick) < ARRIVE_AMONG_BITERS then
      d.there_since = d.there_since or tick
      local o = v.orientation * TWO_PI
      local on = {x = here.x + math.sin(o) * 30, y = here.y - math.cos(o) * 30}
      local accel, turn = Drive.steer(v.orientation, v.speed, here, on, 0.3, kind_of(v.name).turn)
      ride(v, accel, turn)
      return true
    end
    if math.abs(v.speed) > 0.02 then ride(v, "braking", "straight"); return true end
    -- hurt, and nothing about here: mended before it goes on foot
    local m = d.mend
    if m and (m.step == "stop" or not (d.hit_at and tick - d.hit_at < QUIET_FOR) and calm(npc, v, here)) then
      if m.step ~= "stop" then m.step, m.stop_at = "stop", tick end
      if mend(npc, d, v, here, tick) or d.mend then return true end
    end
    return finish(npc, false, "arrived")
  end
  if not d.closest or to_goal < d.closest - CLOSER then d.closest, d.closer_at = to_goal, tick end
  if not d.anchor or distance(here, d.anchor) > MOVED then d.anchor, d.closer_at = {x = here.x, y = here.y}, tick end
  if tick - d.closer_at > NO_PROGRESS then return finish(npc, true, "no_progress") end
  d.limit = d.limit or tick + math.floor(2 * to_goal / WALK_SPEED) + 3600
  if tick > d.limit then return finish(npc, true, "too_slow") end
  -- Under fire from a gun that stands: away from it first.
  local hp = v.health
  if d.hp and hp and hp < d.hp - 0.5 then
    d.hit_at = tick
    local gun = not d.flee and shooter(npc, v, here)
    if gun then
      -- (whether its route knew of it, or it had seen it at all: looked at after a run)
      local id = gun.unit_number
      local known = id and (d.route and d.route.known and d.route.known[id] and "on its route"
        or npc.seen_dangers and npc.seen_dangers.list[id] and "known, not on its route") or "not known"
      Routes.look(npc)   -- known now: the next route keeps out of its reach
      local toward = (math.atan2(gun.position.x - here.x, -(gun.position.y - here.y)) / TWO_PI) % 1
      d.flee = {from = {x = gun.position.x, y = gun.position.y}, until_tick = tick + FLEE_FOR, moved_at = tick,
        back = math.abs((toward - v.orientation + 0.5) % 1 - 0.5) < 0.25}
      d.route, d.path, d.request_id, d.backing = nil, nil, nil, nil
      note(npc, string.format("under fire from %s %.0f off, %s", gun.name, distance(gun.position, here), known), here)
    end
  end
  d.hp = hp
  if tick >= (d.threat_at or 0) then
    d.threat_at = tick + LOOK_THREAT
    look_for_threat(npc, d, v, tick)
  end
  shoot(npc, d)
  if mend(npc, d, v, here, tick) then return true end
  if d.flee then
    local f = d.flee
    if tick < f.until_tick and distance(here, f.from) < CLEAR_OF then
      local moved = f.last and distance(here, f.last) or 1
      f.last = {x = here.x, y = here.y}
      if moved > 0.03 then
        f.moved_at = tick
      elseif tick - f.moved_at > FLEE_BLOCKED then
        f.back, f.moved_at = not f.back, tick   -- blocked: the other way
      end
      if f.back then
        ride(v, "reversing", back_along(d, v, here) or "straight")
      else
        local away = {x = 2 * here.x - f.from.x, y = 2 * here.y - f.from.y}
        local _, turn = Drive.steer(v.orientation, v.speed, here, away, false)
        ride(v, "accelerating", turn)
      end
      return true
    end
    d.flee = nil
    d.phase = "plan"
    return plan(npc, d, v, tick)
  end
  if d.phase == "plan" then
    if tick < (d.retry_at or 0) then
      if d.threat then keep_going(d, v, here) else ride(v, "braking", "straight") end
      return true
    end
    return plan(npc, d, v, tick)
  end
  if d.phase == "wait" then
    if d.threat then keep_going(d, v, here)
    else ride(v, math.abs(v.speed) > 0.05 and "braking" or "nothing", "straight") end
    if tick - d.asked > PATH_WAIT then
      note(npc, "no path in time, straight", d.leg)
      d.path, d.pk, d.phase = {d.leg}, 1, "drive"
    end
    return true
  end
  -- Backing out of where it stuck, turned the other way.
  if d.backing then
    if tick < d.backing.until_tick then
      ride(v, "reversing", d.backing.along and back_along(d, v, here) or d.backing.turn or "straight")
      return true
    end
    d.backing = nil
    d.progress_at = tick
    -- backed out: a fresh path for the leg from where it is now, not the old one it left
    if d.stuck % 3 == 0 then d.route = nil end
    d.phase = "plan"
    return plan(npc, d, v, tick)
  end
  -- Something new about it to keep clear of: a route round it.
  if d.route and tick >= (d.look_at or 0) then
    d.look_at = tick + LOOK_EVERY
    if Routes.new_sighting(npc, d.route, true) then
      note(npc, "new danger seen", here)
      d.route = nil
      d.phase = "plan"
      return plan(npc, d, v, tick)
    end
  end
  -- Along the path: steer for the point LOOK ahead of the nearest; the leg done, the next.
  local path = d.path
  if tick >= (d.ahead_at or 0) then
    d.ahead_at = tick + AHEAD_EVERY
    d.close, d.trees_ahead = close_ahead(v, path, d.pk, here, 8 + math.abs(v.speed) ^ 2 / (2 * BRAKE))
  end
  -- near its ring's wall, careful: at full speed a car could not turn in the room there (the engine check,
  -- a leg along the wall then one for its door, and it ran into the wall turning)
  local box = ring_box(npc)
  local careful = d.close or tick < (d.careful_until or 0)
    or box and inside(box, here, -RING_NEAR) and not inside(box, here, RING_NEAR)
  local look = careful and (heavy(v) and 2 * CAREFUL_LOOK or CAREFUL_LOOK) or LOOK
  local was = d.pk
  while path[d.pk + 1] and distance(here, path[d.pk]) <= look do d.pk = d.pk + 1 end
  -- not looking past the corner of a wall: a fast car on a path round its ring's corner steered for a point
  -- past it, straight through the wall (the engine check, a walled range)
  if d.pk > was and not (d.door and d.door.through) then
    while d.pk > was and walled(npc, v.surface, here, path[d.pk]) do d.pk = d.pk - 1 end
  end
  local aim = path[d.pk]
  local through = d.door and d.door.through
  if through then aim, d.pk = door_aim(d.door, here), #path end
  if not path[d.pk + 1] and distance(here, through and d.door.b or aim) <= 6 then
    d.stuck = 0   -- the leg driven: past whatever stuck it
    if d.door and d.door.through then   -- through the door: on by a route from this side of the wall
      d.door, d.route, d.next, d.next_id = nil, nil, nil, nil
    end
    -- the last one: as near the goal as a path goes (the range: its path ended 4 tiles short, the car
    -- stopped 6 short of that, and it planned leg after leg where it stood till the trip ran out)
    if to_goal <= math.max(8, d.near or 0) + 12 then
      d.arriving = true
      ride(v, "braking", "straight")
      return true
    end
    -- a leg that took it nowhere, three times running: that spot blocked, a new route (the range: a car
    -- going round legs of a few tiles where it stood until the trip ran out)
    if distance(here, d.leg_from or here) < 4 then
      d.tiny = (d.tiny or 0) + 1
      if d.tiny >= 3 then
        note(npc, "going nowhere, blocked", d.leg)
        Routes.block(npc, d.leg, nil, 12)
        d.route, d.tiny, d.next, d.next_id = nil, 0, nil, nil
      end
    else
      d.tiny = 0
    end
    if d.next and begin_leg(npc, d, here, d.next.leg, tick) then
      d.phase = "plan"
      return plan(npc, d, v, tick)
    end
    if d.next then   -- on along the next, without stopping
      d.path, d.leg, d.pk = d.next.path, d.next.leg, 1
      d.route.k = d.next.k
      d.next, d.next_none, d.next_at = nil, nil, nil
      return true
    end
    d.phase = "plan"
    return plan(npc, d, v, tick)
  end
  local last = path[#path]
  if d.route and not d.door and not (d.next or d.next_id or d.next_none) and tick >= (d.next_at or 0)
      and distance(here, last) < PREFETCH and distance(last, dest(d)) > (d.near or 0) + 8 then
    local p, k = leg_end(d, d.route, last, v)
    -- (not a leg across a wall or round its ring, or one ending just short of the ring's wall it must cross:
    -- planned when it comes to it, Drive.cross)
    local left = distance(last, p)
    local on = left > 1 and box and inside(box, last) ~= inside(box, dest(d))
      and {x = last.x + (p.x - last.x) * (left + WALL_AHEAD) / left, y = last.y + (p.y - last.y) * (left + WALL_AHEAD) / left}
    local stops = distance(p, dest(d)) <= (d.near or 0) + 2
    if walled(npc, v.surface, last, p) or box and not inside(box, dest(d)) and goes_round(box, last, p, stops)
        or on and walled(npc, v.surface, last, on) then
      d.next_none = true
    elseif distance(p, last) > 8 then
      d.next_leg, d.next_k = p, k
      d.next_id = ask(npc, v, last, p, WIDE)
    end
  end
  local stop = math.sqrt(2 * BRAKE * math.max(0, to_goal - math.max(8, d.near or 0)))
  local careful_speed = CAREFUL_SPEED[heavy(v) and "tank" or "car"]
  -- (not slower for trees on the way, as the user eases off a little to steer round them by eye: the
  -- range, slower among trees it follows the path between, it stuck on more of them)
  local cap = stop + 0.05
  if careful then cap = math.min(careful_speed, cap) end
  local accel, turn = Drive.steer(v.orientation, v.speed, here, aim, cap, kind_of(v.name).turn)
  ride(v, accel, turn)
  -- Stuck (no way forward for a while): back out, turned the other way; again and again, a new route.
  -- (by how far it got in a while, not tick by tick: the range, a car pressed on a tree that rocked to
  -- and fro a little every tick, never "stuck", for ten thousand ticks)
  if not d.held or distance(here, d.held) > STUCK_MOVE then d.held, d.progress_at = {x = here.x, y = here.y}, tick end
  d.progress_at = d.progress_at or tick
  if tick - d.progress_at > STUCK_TICKS then
    d.stuck = d.stuck + 1
    note(npc, "stuck " .. d.stuck, here)
    if d.stuck > MAX_STUCK then return finish(npc, true, "stuck") end
    -- backed out and on, a way round by the pathfinder; stuck again and again at one spot, what is in
    -- the way cut down
    if d.stuck_spot and distance(here, d.stuck_spot) < 6 then d.stuck_here = (d.stuck_here or 1) + 1
    else d.stuck_spot, d.stuck_here = {x = here.x, y = here.y}, 1 end
    d.no_straight_until = tick + STRAIGHT_AFTER_STUCK
    -- stuck twice in thick woods: a car does not drive through a forest, it goes round (the range on
    -- natural maps: twenty stucks a trip in one, cutting tree after tree it could not walk up to)
    local woods = not heavy(v) and d.stuck_here == 2 and v.surface.count_entities_filtered{type = "tree",
      position = here, radius = 10, limit = WOODS_TREES} >= WOODS_TREES
    if woods then
      note(npc, "thick woods, out the way it came and round them", here)
      Routes.block(npc, here, nil, WOODS_BLOCK)
      d.route, d.next, d.next_id = nil, nil, nil
      d.backing = {until_tick = tick + WOODS_BACK, along = true}
      d.progress_at = tick
      d.careful_until = tick + WOODS_BACK + CAREFUL_FOR
      return true
    end
    -- (never out of it to cut anything with biters about, or hurt and getting away: back out the way it
    -- came; the range: out to mine the big rock its car was stuck on, biters at it, the car lost)
    local beset = d.threat or (d.mend and d.mend.step == "away")
    local e = not beset and d.stuck_here >= CLEAR_AFTER and (d.clears or 0) < MAX_CLEARS and touching(v, d)
    if e then
      start_clear(npc, d, v, e, tick, "clearing ")
      return true
    end
    d.backing = {until_tick = tick + (beset and 2 or 1) * BACK_TICKS, turn = turn == "left" and "right" or "left",
      along = beset or nil}
    d.progress_at = tick
    d.careful_until = tick + BACK_TICKS + CAREFUL_FOR
    -- stuck at water: that spot blocked, a route round it
    local o = v.orientation * TWO_PI
    local ahead = {x = here.x + math.sin(o) * 5, y = here.y - math.cos(o) * 5}
    if v.surface.count_tiles_filtered and v.surface.count_tiles_filtered{position = ahead, radius = 3,
        collision_mask = "water_tile", limit = 1} > 0 then
      Routes.block(npc, ahead, nil, 24)
      d.route = nil
    end
  end
  return true
end

-- Where it stands: driving (and how far along), arrived, or given up.
function Drive.state(npc)
  local d = npc.drive
  if not d then return nil end
  return d.failed and "failed" or d.done and "arrived" or "driving"
end

-- A drive of its work given up (the job cancelled under it): stopped, and the vehicle left where it stands.
function Drive.stop(npc, why)
  local d = npc.drive
  if d and not d.done and not d.failed then finish(npc, true, why or "cancelled") end
end

-- What the strategist and the vehicle judgement see (companion/vehicle_brain.py): its long walks of late (how
-- many an hour, the minutes an hour they take on foot and driven: kept by navigation.lua), its vehicle, and the
-- vehicles its force can make now, with what each takes that its hands cannot make and how much of that it has.
local TRAVEL_WINDOW = 216000   -- the last game hour of its trips
local function recipe_for(force, item)
  local ok, list = pcall(function()
    return prototypes.get_recipe_filtered{{filter = "has-product-item", elem_filters = {{filter = "name", name = item}}}}
  end)
  for name in pairs(ok and list or {}) do
    local r = force.recipes[name]
    if r and r.enabled then return r end
  end
end
local function held(npc, item)
  local n = npc.character.get_main_inventory().get_item_count(item)
  for _, chest in ipairs(npc.storage_chests or {}) do
    if chest.valid then n = n + chest.get_inventory(defines.inventory.chest).get_item_count(item) end
  end
  return n
end
function Drive.travel(npc, tick)
  local c = npc.character
  if not (c and c.valid) then return nil end
  local out = {trips = 0, tiles = 0, walk_minutes_per_hour = 0, drive_minutes_per_hour = 0, per_hour = 0, drove = 0}
  local first
  for _, t in ipairs(npc.travel and npc.travel.trips or {}) do
    if tick - t.t <= TRAVEL_WINDOW then
      out.trips, out.tiles = out.trips + 1, out.tiles + t.d
      if t.drove then
        out.drove, out.drive_minutes_per_hour = out.drove + 1, out.drive_minutes_per_hour + t.ticks
      elseif t.d >= MIN_TRIP then   -- (walks it would drive)
        out.walk_minutes_per_hour = out.walk_minutes_per_hour + t.ticks
      end
      first = math.min(first or t.t - t.ticks, t.t - t.ticks)
    end
  end
  local hours = math.max(1 / 6, ((tick - (first or tick)) / 216000))
  out.per_hour = math.floor(10 * out.trips / hours) / 10
  out.walk_minutes_per_hour = math.floor(10 * out.walk_minutes_per_hour / 3600 / hours) / 10
  out.drive_minutes_per_hour = math.floor(10 * out.drive_minutes_per_hour / 3600 / hours) / 10
  out.average_tiles = out.trips > 0 and math.floor(out.tiles / out.trips) or 0
  out.drive_speed = npc.drive_speed and math.floor(1000 * npc.drive_speed) / 1000 or nil
  local v = c.vehicle or npc.parked
  if v and v.valid and v.type == "car" then
    out.vehicle = {name = v.name, health = math.floor(100 * (v.health or 0) / math.max(1, v.max_health or 1)),
      fueled = fueled(v) or false, x = math.floor(v.position.x), y = math.floor(v.position.y),
      inside = inside(ring_box(npc), v.position) or nil}
  end
  local item = vehicle_item(npc)
  out.in_pockets = item
  out.fuel = fuel_item(npc, v and v.valid and v.name or nil)
  local hand = c.prototype.crafting_categories or {}
  local makes = {}
  for _, k in ipairs(Drive.vehicles()) do
    local r = recipe_for(npc.force, k.item)
    if r then
      local parts = {}
      for _, ing in ipairs(r.ingredients) do
        if ing.type == "item" then
          local by = "machine"
          local made = recipe_for(npc.force, ing.name)
          local cats = made and (made.categories or {made.category}) or {}   -- (2.1: categories)
          if not made then by = "raw" end
          for _, cat in ipairs(cats) do
            if hand[cat] then by = "hand" elseif cat == "smelting" and by == "machine" then by = "furnace" end
          end
          if by ~= "hand" and by ~= "raw" then
            parts[#parts + 1] = {name = ing.name, amount = ing.amount, have = held(npc, ing.name), by = by}
          end
        end
      end
      makes[#makes + 1] = {name = k.name, item = k.item, heavy = k.heavy or nil, health = k.health, parts = parts}
    end
  end
  out.can_make = makes
  return out
end

return Drive
