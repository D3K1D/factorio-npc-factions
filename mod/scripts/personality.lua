-- Composable, saved personality. Traits adjust interpretable policy parameters;
-- they are configuration, not evidence that a neural policy has learned a skill.
local Personality = {}
-- (optional: outside the game's mod, as in unit tests, there is no team world and no world traits)
local has_team_world, TeamWorld = pcall(require, "scripts.team_world")
if not has_team_world then TeamWorld = nil end
local KEYS = {"morality","aggression","courage","loyalty","honesty","cooperation",
  "trade","ambition","cognition","work_rate","defense","science","logistics",
  "power","frugality","territoriality","adaptability","sociability","humor",
  "curiosity","grudge","stealth","resourcefulness","patience"}
local TRAITS, ORDER, CONFLICTS = {}, {}, {}
local function trait(name,effects)
  ORDER[#ORDER+1]=name; TRAITS[name]={name=name,effects=effects}
end
local function opposite(a,b)
  CONFLICTS[a]=CONFLICTS[a] or {}; CONFLICTS[b]=CONFLICTS[b] or {}
  CONFLICTS[a][b]=true; CONFLICTS[b][a]=true
end
trait("good",{morality=0.40,honesty=0.15,cooperation=0.15})
trait("evil",{morality=-0.45,honesty=-0.20,aggression=0.25})
trait("hero",{morality=0.25,courage=0.30,defense=0.20,cooperation=0.10})
trait("mysterious",{sociability=-0.25,stealth=0.25,honesty=-0.05})
trait("clown",{humor=0.45,sociability=0.25,patience=-0.05})
trait("slow",{work_rate=-0.35,cognition=-0.20,patience=0.15})
trait("smart",{cognition=0.40,adaptability=0.20,science=0.20})
trait("cunning",{cognition=0.20,stealth=0.20,honesty=-0.20})
trait("brave",{courage=0.35,aggression=0.10})
trait("cowardly",{courage=-0.40,aggression=-0.15,defense=0.10})
trait("aggressive",{aggression=0.40,courage=0.10,cooperation=-0.10})
trait("pacifist",{aggression=-0.45,morality=0.10,trade=0.10})
trait("bold",{courage=0.25,ambition=0.20,patience=-0.10})
trait("cautious",{courage=-0.15,defense=0.20,patience=0.20})
trait("altruistic",{morality=0.20,cooperation=0.30,trade=0.10})
trait("selfish",{morality=-0.20,cooperation=-0.25,ambition=0.15})
trait("honest",{honesty=0.40,loyalty=0.10})
trait("deceptive",{honesty=-0.40,stealth=0.20})
trait("loyal",{loyalty=0.40,cooperation=0.15})
trait("treacherous",{loyalty=-0.45,honesty=-0.20,aggression=0.10})
trait("trusting",{cooperation=0.20,sociability=0.15,grudge=-0.15})
trait("suspicious",{cooperation=-0.20,stealth=0.15,grudge=0.15})
trait("diplomatic",{sociability=0.25,cooperation=0.25,trade=0.20})
trait("abrasive",{sociability=-0.30,cooperation=-0.15,grudge=0.10})
trait("cooperative",{cooperation=0.40,loyalty=0.10})
trait("independent",{cooperation=-0.30,resourcefulness=0.20})
trait("merchant",{trade=0.45,logistics=0.20,sociability=0.10})
trait("hoarder",{trade=-0.25,frugality=0.30})
trait("frugal",{frugality=0.40,resourcefulness=0.10})
trait("wasteful",{frugality=-0.40})
trait("ambitious",{ambition=0.40,work_rate=0.15,science=0.10})
trait("content",{ambition=-0.35,patience=0.20})
trait("curious",{curiosity=0.40,adaptability=0.15})
trait("traditionalist",{curiosity=-0.25,adaptability=-0.25,patience=0.15})
trait("innovative",{curiosity=0.25,adaptability=0.30,cognition=0.15})
trait("methodical",{patience=0.25,cognition=0.15,work_rate=-0.10})
trait("improviser",{adaptability=0.30,resourcefulness=0.20,patience=-0.10})
trait("perfectionist",{cognition=0.15,patience=0.30,work_rate=-0.10})
trait("pragmatic",{resourcefulness=0.30,work_rate=0.10})
trait("organized",{logistics=0.25,cognition=0.15,patience=0.15})
trait("chaotic",{logistics=-0.25,patience=-0.20,humor=0.10})
trait("patient",{patience=0.40,grudge=-0.10})
trait("impatient",{patience=-0.40,aggression=0.10})
trait("stoic",{patience=0.25,humor=-0.15,sociability=-0.10})
trait("dramatic",{humor=0.15,sociability=0.20,patience=-0.15})
trait("cheerful",{humor=0.25,sociability=0.20})
trait("grim",{humor=-0.35,sociability=-0.10})
trait("optimistic",{courage=0.15,ambition=0.15,grudge=-0.15})
trait("pessimistic",{courage=-0.20,defense=0.15,grudge=0.15})
trait("protective",{defense=0.35,loyalty=0.20,morality=0.10})
trait("vengeful",{grudge=0.45,aggression=0.20})
trait("forgiving",{grudge=-0.45,cooperation=0.15})
trait("opportunistic",{ambition=0.20,adaptability=0.25,loyalty=-0.10})
trait("territorial",{territoriality=0.45,defense=0.15})
trait("nomadic",{territoriality=-0.40,curiosity=0.20})
trait("industrialist",{ambition=0.20,power=0.25,work_rate=0.15,science=0.10})
trait("environmentalist",{frugality=0.25,power=0.10,territoriality=-0.10})
trait("scientist",{science=0.45,cognition=0.20})
trait("engineer",{cognition=0.20,logistics=0.20,power=0.15})
trait("miner",{resourcefulness=0.25,work_rate=0.25,science=-0.10})
trait("quartermaster",{logistics=0.35,frugality=0.20,cooperation=0.10})
trait("railway_enthusiast",{logistics=0.40,curiosity=0.15})
trait("logistician",{logistics=0.45,cognition=0.10})
trait("power_specialist",{power=0.45,resourcefulness=0.10})
trait("nuclear_enthusiast",{power=0.35,science=0.20,ambition=0.15})
trait("fortifier",{defense=0.45,territoriality=0.20})
trait("scout",{curiosity=0.35,courage=0.20,work_rate=0.15})
trait("explorer",{curiosity=0.40,resourcefulness=0.15})
trait("risk_taker",{courage=0.25,aggression=0.15,patience=-0.10})
trait("risk_averse",{courage=-0.25,defense=0.25,patience=0.10})
trait("diligent",{work_rate=0.35,patience=0.15})
trait("lazy",{work_rate=-0.35,ambition=-0.15})
trait("generous",{trade=0.25,cooperation=0.20,morality=0.15})
trait("stingy",{trade=-0.30,frugality=0.20})
trait("charismatic",{sociability=0.40,cooperation=0.10})
trait("shy",{sociability=-0.40,stealth=0.10})
trait("competitive",{ambition=0.30,aggression=0.15,cooperation=-0.10})
trait("collaborative",{cooperation=0.30,logistics=0.15,loyalty=0.15})
trait("reckless",{courage=0.30,patience=-0.30,defense=-0.15})
trait("planner",{cognition=0.25,patience=0.25,logistics=0.15})
trait("saboteur",{stealth=0.40,morality=-0.25,aggression=0.20})
trait("pirate",{aggression=0.30,morality=-0.30,trade=-0.15})
trait("resourceful",{resourcefulness=0.45,adaptability=0.15})
trait("adaptable",{adaptability=0.45})
trait("stubborn",{adaptability=-0.40,grudge=0.15})
-- Given by the world, never drawn (TeamWorld.world_traits; kept out of ORDER, so the draw is as before): a death
-- world's engineers put survival first.
TRAITS.survivor={name="survivor",effects={defense=0.40,courage=0.15,cooperation=0.15,aggression=0.10}}
for _,pair in ipairs({{"good","evil"},{"hero","evil"},{"slow","smart"},
  {"brave","cowardly"},{"aggressive","pacifist"},{"bold","cautious"},
  {"altruistic","selfish"},{"honest","deceptive"},{"loyal","treacherous"},
  {"trusting","suspicious"},{"diplomatic","abrasive"},{"cooperative","independent"},
  {"merchant","hoarder"},{"frugal","wasteful"},{"ambitious","content"},
  {"curious","traditionalist"},{"innovative","traditionalist"},{"organized","chaotic"},
  {"patient","impatient"},{"stoic","dramatic"},{"cheerful","grim"},
  {"optimistic","pessimistic"},{"vengeful","forgiving"},{"territorial","nomadic"},
  {"risk_taker","risk_averse"},{"diligent","lazy"},{"generous","stingy"},
  {"charismatic","shy"},{"competitive","collaborative"},{"reckless","planner"},
  {"adaptable","stubborn"},{"good","saboteur"},{"good","pirate"},
  {"slow","diligent"},{"survivor","reckless"},
  -- (a death world's engineers are good teammates: the traits that turn on others give way)
  {"survivor","evil"},{"survivor","treacherous"},{"survivor","saboteur"},{"survivor","pirate"}}) do opposite(pair[1],pair[2]) end
local function clamp(x) return math.max(0,math.min(1,x)) end
local function next_seed(seed) return (seed*48271)%2147483647 end
function Personality.compatible(names,candidate)
  if not TRAITS[candidate] then return false,"unknown_trait" end
  for _,name in ipairs(names or {}) do
    if name==candidate then return false,"duplicate_trait" end
    if CONFLICTS[name] and CONFLICTS[name][candidate] then return false,"opposite_trait:"..name end
  end
  return true
end
function Personality.assign(npc,names,seed)
  local accepted,selected={},{}
  for _,name in ipairs(names) do
    local ok,reason=Personality.compatible(accepted,name)
    if not ok then return false,reason end
    accepted[#accepted+1]=name; selected[name]=true
  end
  local axes={}
  local state=seed or 1
  for _,key in ipairs(KEYS) do
    state=next_seed(state); axes[key]=0.45+(state%1001)/10000
  end
  for _,name in ipairs(accepted) do
    for key,delta in pairs(TRAITS[name].effects) do axes[key]=axes[key]+delta end
  end
  for _,key in ipairs(KEYS) do axes[key]=clamp(axes[key]) end
  npc.personality={version=1,seed=seed or 1,traits=accepted,selected=selected,axes=axes}
  return true,npc.personality
end
-- The traits the world gives every engineer in it (a death world: survivor) join its own, and go again when the
-- world no longer gives them (world_given remembers which). A drawn trait that conflicts with one gives way, and
-- comes back with them gone (world_displaced). Its drift (axes moved from their baseline, and the log of it)
-- carries over onto the new baseline.
local function with_world_traits(npc,p)
  if not (TeamWorld and TeamWorld.world_traits) then return p end
  local now,given={},{}
  for _,name in ipairs(TeamWorld.world_traits()) do if TRAITS[name] then now[name]=true end end
  for _,name in ipairs(p.world_given or {}) do given[name]=true end
  local changed=false
  for name in pairs(now) do if not p.selected[name] then changed=true end end
  for name in pairs(given) do if not now[name] then changed=true end end
  if not changed then return p end
  local names,added,displaced={},{},{}
  local function conflicts(name)
    for m in pairs(now) do if CONFLICTS[m] and CONFLICTS[m][name] then return true end end
  end
  for _,name in ipairs(p.traits) do
    if conflicts(name) then displaced[#displaced+1]=name
    elseif not (given[name] and not now[name]) then names[#names+1]=name end
  end
  for _,name in ipairs(p.world_displaced or {}) do   -- (back once nothing the world gives conflicts with it)
    if conflicts(name) then displaced[#displaced+1]=name else table.insert(names,1,name) end
  end
  for _,name in ipairs(TeamWorld.world_traits()) do
    if now[name] and not p.selected[name] then names[#names+1]=name; added[#added+1]=name end
  end
  for _,name in ipairs(p.world_given or {}) do if now[name] then added[#added+1]=name end end
  local drift={}
  for _,key in ipairs(KEYS) do drift[key]=p.base_axes and (p.axes[key]-p.base_axes[key]) or 0 end
  local log=p.drift_log
  local ok,result=Personality.assign(npc,names,p.seed)
  if not ok then return p end
  if p.base_axes then
    result.base_axes={}
    for _,key in ipairs(KEYS) do
      result.base_axes[key]=result.axes[key]
      result.axes[key]=clamp(result.axes[key]+drift[key])
    end
  end
  result.drift_log=log
  result.world_given=#added>0 and added or nil
  result.world_displaced=#displaced>0 and displaced or nil
  return result
end
function Personality.ensure(npc)
  if npc.personality and npc.personality.version==1 then return with_world_traits(npc,npc.personality) end
  local surface=npc.character and npc.character.valid and npc.character.surface
  local map_seed=surface and surface.map_gen_settings.seed or 1
  local seed=(map_seed+(npc.id or 1)*7919+104729)%2147483646+1
  local state=seed
  local names={}
  -- One primary archetype gives the roster distinct starting motivations.
  local anchors={"smart","evil","hero","mysterious","clown","slow","merchant",
    "scientist","fortifier","power_specialist"}
  names[1]=anchors[((npc.id or 1)-1)%#anchors+1]
  for _=1,512 do
    state=next_seed(state)
    local candidate=ORDER[(state%#ORDER)+1]
    if Personality.compatible(names,candidate) then names[#names+1]=candidate end
    if #names>=6 then break end
  end
  local ok,result=Personality.assign(npc,names,seed)
  if not ok then error("invalid generated personality:"..tostring(result)) end
  return with_world_traits(npc,result)
end
function Personality.axes(npc)
  local personality=Personality.ensure(npc)
  if storage and storage.test then
    local neutral={}; for _,key in ipairs(KEYS) do neutral[key]=0.5 end
    return neutral
  end
  return personality.axes
end
function Personality.has(npc,name) return Personality.ensure(npc).selected[name]==true end
function Personality.observe(npc)
  local p=Personality.ensure(npc)
  local names,axes={},{}
  for i,name in ipairs(p.traits) do names[i]=name end
  for _,key in ipairs(KEYS) do axes[key]=Personality.axes(npc)[key] end
  return {version=p.version,seed=p.seed,traits=names,axes=axes,catalog_size=#ORDER}
end
function Personality.catalog()
  local result={}
  for _,name in ipairs(ORDER) do
    local conflicts={}
    for _,other in ipairs(ORDER) do if CONFLICTS[name] and CONFLICTS[name][other] then conflicts[#conflicts+1]=other end end
    result[#result+1]={name=name,effects=TRAITS[name].effects,conflicts=conflicts}
  end
  return result
end
-- Drift changes policy axes only: selected traits and assign/ensure behaviour stay intact.
-- Moral-core traits protect every axis they originally affect. Global stability starts
-- at 0.5, shifts once per trait group, and is bounded to [0,0.95].
local MORAL_CORE={"good","evil","hero","honest","deceptive","loyal",
  "treacherous","pacifist","saboteur","pirate"}
function Personality.drift(npc,axis,delta,reason,tick)
  if not npc then return nil,"missing_npc" end
  local p=Personality.ensure(npc)
  if type(p.axes[axis])~="number" then return nil,"unknown_axis" end
  if type(delta)~="number" or delta~=delta or math.abs(delta)==math.huge then return nil,"invalid_delta" end
  if reason~=nil and type(reason)~="string" then return nil,"invalid_reason" end
  if tick~=nil and (type(tick)~="number" or tick~=tick or math.abs(tick)==math.huge or tick<0 or tick%1~=0) then
    return nil,"invalid_tick"
  end
  local stability=0.5
  for _,name in ipairs(MORAL_CORE) do
    if p.selected[name] and TRAITS[name].effects[axis] then stability=0.9; break end
  end
  if p.selected.stubborn or p.selected.traditionalist then stability=stability+0.3 end
  if p.selected.adaptable or p.selected.improviser or p.selected.innovative then stability=stability-0.3 end
  stability=math.max(0,math.min(0.95,stability))
  if not p.base_axes then
    p.base_axes={}
    for _,key in ipairs(KEYS) do p.base_axes[key]=p.axes[key] end
  end
  local before=p.axes[axis]
  p.axes[axis]=clamp(math.max(p.base_axes[axis]-0.25,
    math.min(p.base_axes[axis]+0.25,before+delta*(1-stability))))
  p.drift_log=p.drift_log or {}
  if #p.drift_log>=16 then table.remove(p.drift_log,1) end
  -- delta is the actual applied change (including stability and caps).
  p.drift_log[#p.drift_log+1]={axis=axis,delta=p.axes[axis]-before,
    reason=reason and reason:sub(1,256) or nil,tick=tick}
  return p.axes[axis],p.axes[axis]-before
end
return Personality
