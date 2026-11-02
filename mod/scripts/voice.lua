-- Rate-limited NPC speech. Lines are short templates picked by personality,
-- relationship and mood; the chat setting decides how much reaches the chat.
-- Choices hash saved state, so every multiplayer peer prints the same line.
local Personality = require("scripts.personality")
local Radio = require("scripts.radio")
local Voice = {}

-- Priority: 1 ambient (research, finished builds, banter), 2 social (offers,
-- deliveries, greetings), 3 major (war, treaties, attacks, deaths).
local MIN_PRIORITY = {quiet=3, normal=2, chatty=1}
local GAP = {quiet={npc=18000, global=3600}, normal={npc=3600, global=600}, chatty={npc=900, global=180}}
local REPEAT = 18000       -- the same speaker/kind/listener is not repeated within 5 minutes
local LOG = 64

local T = {}
-- {warm, curt, playful, hostile}; a missing tone falls back to warm.
T.offer_made = {
  warm={"{target}, up for a {offer}? Could work for both of us.", "{target}, how about a {offer}?"},
  curt={"{target}: {offer}. Yes or no.", "{offer}, {target}?"},
  playful={"{target}, a {offer}? I'll bring snacks.", "{target}, let's make it a {offer}."},
  hostile={"{target}, {offer}. Last chance."}}
T.treaty = {
  warm={"{offer} with {target}. Good stuff.", "{offer} with {target} is set."},
  curt={"{offer} with {target}, done."},
  playful={"{offer} with {target}. No take-backs."},
  hostile={"Fine, {offer} with {target}. Don't blow it."}}
T.offer_rejected = {
  warm={"Not this time, {target}.", "Gonna pass, {target}."},
  curt={"No, {target}.", "Pass."},
  playful={"Tempting, {target}. Still no."},
  hostile={"Not a chance, {target}.", "Nope, {target}."}}
T.war_declared = {
  warm={"{target}, you left me no choice. It's war."},
  curt={"{target}, we're at war."},
  playful={"{target}, it's war now. Nothing personal."},
  hostile={"{target}, it's war.", "Coming for your factory, {target}."}}
T.war_received = {
  warm={"{target} wants war? Thought better of you."},
  curt={"War with {target}. Copy."},
  playful={"War, {target}? Bold, with belts like those."},
  hostile={"Want war, {target}? You got it."}}
T.delivery_completed = {
  warm={"Dropped {count} {item} off for {target}.", "{target}, your {item} is there."},
  curt={"{count} {item} delivered, {target}."},
  playful={"Delivery for {target}: {count} {item}, still warm."}}
T.delivery_failed = {
  warm={"Sorry {target}, couldn't get the {item} to you."},
  curt={"Delivery to {target} failed."},
  playful={"Lost the {item} for {target}. Blame the biters."}}
T.robbed = {
  warm={"{target} took my {item}. Didn't see that coming.", "{target}, really? I trusted you."},
  curt={"{target} stole from me. Noted."},
  playful={"{target} borrowed my {item}. Without asking."},
  hostile={"{target} stole from me. Won't forget it.", "Thief. {target}, you'll pay for that."}}
T.sabotaged = {
  warm={"{target} wrecked my machines. Why?"},
  curt={"{target} sabotaged me. Noted."},
  hostile={"{target} hit my factory. Not over."}}
T.attacked = {
  warm={"{target}, stop shooting at me!"},
  curt={"{target} is attacking me."},
  playful={"Ow. {target}, that's not how you say hi."},
  hostile={"{target} is on me! You'll regret it.", "Back off, {target}!"}}
T.research_completed = {
  warm={"{tech} is done.", "Got {tech}."},
  curt={"{tech} done."},
  playful={"Science! {tech} unlocked."}}
T.plan_completed = {
  warm={"{purpose} is up and running.", "{purpose} done."},
  curt={"{purpose}, done."},
  playful={"{purpose} is up. Barely any spaghetti."}}
T.plan_failed = {
  warm={"{purpose} didn't work out. Pulling it down."},
  curt={"{purpose} failed. Salvaging."},
  playful={"{purpose} was a learning experience."}}
T.trade_completed = {
  warm={"Thanks, {target}. Good trade.", "Pleasure, {target}."},
  curt={"Trade done, {target}."},
  playful={"{target}, hard bargain. Come back soon."},
  hostile={"Done. Don't get used to it, {target}."}}
T.trade_failed = {
  warm={"{target}, that trade fell through."},
  curt={"Trade with {target} failed."},
  hostile={"{target}, you didn't hold up your end."}}
T.greet = {
  warm={"Hey {target}!", "Hey {target}, how's it going?"},
  curt={"{target}.", "Busy, {target}."},
  playful={"{target}! Here to admire my spaghetti?", "Hey {target}, got any coal?"},
  hostile={"{target}. Keep your distance.", "What do you want, {target}?"}}
T.threat = {
  hostile={"{target}, I remember what you did. Stay away.", "Careful, {target}. I haven't forgotten."}}
T.need_help = {
  warm={"{target}, I'm stuck. Got any {item}?"},
  curt={"Need {item}."},
  playful={"{target}, out of {item} here. Spare some?"},
  hostile={"Short on {item}. Don't get ideas, {target}."}}
T.trade_offer = {
  warm={"{target}, {npc_count} {npc_item} for {player_count} {player_item}? Say '{name}: accept {deal}'."},
  curt={"Offer #{deal}: {npc_count} {npc_item} for {player_count} {player_item}. '{name}: accept {deal}'."},
  playful={"{target}, {npc_count} shiny {npc_item} for {player_count} {player_item}? '{name}: accept {deal}'."},
  hostile={"{npc_count} {npc_item} for {player_count} {player_item}, {target}. '{name}: accept {deal}'."}}
T.banter = {
  warm={"{target}, base is looking good.", "Nice work, {target}."},
  curt={"{target}. Still at it?"},
  playful={"{target}, is that a bus or modern art?", "{target}, race you to the rocket."},
  hostile={"{target}, stay off my ore.", "I see you, {target}."}}
T.scout_found = {
  warm={"Found {resource} out past the base.", "There's {resource} out there."},
  curt={"{resource} spotted."},
  playful={"Treasure. Well, {resource}. Close enough."}}
T.retire_old = {
  warm={"Bus does the work now. Taking down {count} old builds.", "Old furnaces coming down, the bus took over."},
  curt={"Pulling {count} old builds. Bus replaces them."},
  playful={"Bye, old spaghetti. Bus is in charge."},
  hostile={"Old builds coming down. Hands off the parts."}}
T.retire_keep = {
  warm={"Keeping my old lines for now. Still need them."},
  curt={"Old lines stay. Still needed."},
  playful={"Old factory lives on. Still has a job."}}
T.refuse_repeat = {
  warm={"Already gave you my answer. Give it time."},
  curt={"Asked and answered."},
  playful={"Asking again won't change it. Probably."},
  hostile={"I said no."}}
-- The team world's shared stores (team_supply.lua): a teammate's chest is fair game, a player's is asked about.
T.team_grab = {
  warm={"Grabbed {count} {item} from {target}'s chest.", "Took {count} {item} from {target}'s stash, thanks."},
  curt={"{count} {item} from {target}'s chest."},
  playful={"Borrowing {count} {item} from {target}. Teamwork."}}
T.ask_chest = {
  warm={"{target}, mind if I grab {count} {item} from your chest at {where}?",
        "{target}, ok if I take {count} {item} from your chest at {where}?"},
  curt={"{target}, can I take {count} {item} from your chest at {where}?"},
  playful={"{target}, could I borrow {count} {item} from your chest at {where}?"}}
T.chest_yes = {
  warm={"Thanks, {target}. Grabbing it.", "Cheers, {target}."},
  curt={"Thanks. On it."},
  playful={"You're the best, {target}."}}
T.chest_no = {
  warm={"No worries, I'll find some {item} elsewhere.", "All good, I'll make my own."},
  curt={"Copy. I'll get my own."},
  playful={"Fair enough. Back to the mines."}}
Voice.TEMPLATES = T

local function state()
  storage.voice = storage.voice or {last_npc={}, last_global=-1000000, recent={}, lines={}}
  return storage.voice
end

function Voice.level()
  local ok, value = pcall(function() return settings.global["npc-factions-chat"].value end)
  return ok and MIN_PRIORITY[value] and value or "normal"
end

local function hash(text)
  local h = 5381
  for i = 1, #text do h = (h * 33 + text:byte(i)) % 2147483647 end
  return h
end

-- Tone from personality, overridden by how the speaker feels about the listener.
function Voice.tone(npc, label, mood)
  local a = Personality.axes(npc) or {}
  if label == "enemy" or label == "rival" or mood and mood.anger and mood.anger >= 50 then return "hostile" end
  if (a.humor or 0.5) >= 0.7 then return "playful" end
  if (a.sociability or 0.5) < 0.35 or label == "wary" then return "curt" end
  if (a.aggression or 0.5) >= 0.8 and label ~= "friend" and label ~= "ally" then return "hostile" end
  return "warm"
end

function Voice.pretty(name)
  return (tostring(name or "?"):gsub("-", " "))
end

function Voice.render(kind, tone, vars, salt)
  local set = T[kind]
  if not set then return nil end
  local lines = set[tone] or set.warm or set.hostile
  if not lines then return nil end
  local line = lines[hash(kind .. ":" .. tostring(salt or 0)) % #lines + 1]
  return (line:gsub("{([%w_]+)}", function(k)
    local v = vars and vars[k]
    return v ~= nil and tostring(v) or ""
  end))
end

local function color(npc)
  local c = npc.color
  if type(c) == "table" and c.r then return c end
  local palette = {{r=0.55,g=0.85,b=1}, {r=1,g=0.75,b=0.45}, {r=0.7,g=1,b=0.6}, {r=1,g=0.6,b=0.8},
    {r=0.85,g=0.75,b=1}}
  return palette[(npc.id - 1) % #palette + 1]
end

local function remember(s, entry)
  s.lines[#s.lines + 1] = entry
  while #s.lines > LOG do table.remove(s.lines, 1) end
end

-- opts: priority (1-3), target (key used for repeat suppression), label/mood
-- (relationship toward the listener), player (LuaPlayer: say only to them),
-- direct (a reply the player asked for: no rate limit).
-- Returns the printed text, or nil and the reason it stayed silent.
function Voice.say(npc, kind, vars, opts)
  opts = opts or {}
  if not (npc and npc.character and npc.character.valid) then return nil, "speaker_missing" end
  local tick = game.tick
  local s = state()
  local level = Voice.level()
  local priority = opts.priority or 2
  if not opts.direct then
    if priority < MIN_PRIORITY[level] then return nil, "below_chat_level" end
    local key = npc.id .. ":" .. kind .. ":" .. tostring(opts.target or "")
    if tick - (s.recent[key] or -REPEAT) < REPEAT then return nil, "repeat" end
    if priority < 3 then
      local gap = GAP[level]
      if tick - (s.last_npc[npc.id] or -gap.npc) < gap.npc then return nil, "npc_gap" end
      if tick - s.last_global < gap.global then return nil, "global_gap" end
    end
    s.recent[key] = tick
  end
  vars = vars or {}
  vars.name = vars.name or npc.name
  local text = Radio.clean(Voice.render(kind, opts.tone or Voice.tone(npc, opts.label, opts.mood), vars, npc.id .. ":" .. tick))
  if not text then return nil, "no_template" end
  s.last_npc[npc.id] = tick
  s.last_global = tick
  local message = "[" .. npc.name .. "] " .. text
  if opts.player then
    if opts.player.valid then opts.player.print(message, {color=color(npc)}) end
  else
    game.print(message, {color=color(npc)})
  end
  remember(s, {tick=tick, npc_id=npc.id, kind=kind, text=text,
    player_index=opts.player and opts.player.valid and opts.player.index or nil})
  if Voice.recorder then Voice.recorder.say(npc, opts.player and opts.player.valid and opts.player.name or nil, text) end
  -- Keep the repeat table small: forget entries past their window.
  local stale = {}
  for key, at in pairs(s.recent) do if tick - at >= REPEAT then stale[#stale + 1] = key end end
  for _, key in ipairs(stale) do s.recent[key] = nil end
  return text
end

-- Narration for events no engineer can say themselves (a death).
function Voice.announce(text)
  game.print("[NPC factions] " .. text, {color={r=1, g=0.85, b=0.4}})
  remember(state(), {tick=game.tick, kind="announce", text=text})
end

function Voice.recent(n)
  local s = state()
  local out = {}
  for i = math.max(1, #s.lines - (n or 10) + 1), #s.lines do out[#out + 1] = s.lines[i] end
  return out
end

return Voice
