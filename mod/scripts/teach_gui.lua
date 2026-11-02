-- The engineers' window, opened from the shortcut bar: where every engineer stands with you and with
-- each other, an engineer's open question (answer a "where?" with Mark location, which works on the
-- map too), and teaching: clicking the slot while holding a blueprint (or a book of them) teaches
-- every engineer.
local Teach = require("scripts.teach")
local Relations = require("scripts.relations")
local Diplomacy = require("scripts.diplomacy")
local TeachGui = {}

local FRAME, SLOT, CLOSE, LIST = "npc_teach_frame", "npc_teach_slot", "npc_teach_close", "npc_teach_list"
local MARK, QUESTION, SHOW = "npc_mark_location", "npc_question", "npc_show_area"
local RELATIONS = "npc_relations"
local SHORTCUT = "npc-teach-blueprint"
local STANCE_COLOR = {allied = {0.45, 1, 0.45}, peace = {0.85, 0.85, 0.85}, war = {1, 0.35, 0.3}}

-- How it feels about you, beyond the treaty: from its trust, liking, grudges and fear.
function TeachGui.feeling(stance)
  if stance >= 0.35 then return "friendly" end
  if stance <= -0.5 then return "hostile" end
  if stance <= -0.15 then return "wary" end
  return "neutral"
end

local function engineers()
  local out = {}
  for id = 1, 10 do
    local npc = storage.npcs and storage.npcs[id]
    if npc and npc.character and npc.character.valid then out[#out + 1] = npc end
  end
  return out
end

-- Every engineer: allied, at peace or at war with you, how it feels about you, and who among the
-- other engineers it is allied with or fighting.
local function fill_relations(box, player)
  box.clear()
  local all = engineers()
  if #all == 0 then
    box.add{type = "label", caption = "No engineers yet."}
    return
  end
  local grid = box.add{type = "table", column_count = 4, draw_horizontal_line_after_headers = true}
  grid.style.horizontal_spacing = 14
  for _, heading in ipairs({"Engineer", "With you", "Feels", "With the others"}) do
    grid.add{type = "label", caption = "[font=default-bold]" .. heading .. "[/font]"}
  end
  local key = "player:" .. player.index
  for _, npc in ipairs(all) do
    local name = grid.add{type = "label", caption = npc.name .. " (#" .. npc.id .. ")"}
    name.style.font_color = npc.character.color
    local state = Diplomacy.state(npc.force, player.force)
    local joined = npc.joined and npc.joined.player_index == player.index
    local stance = grid.add{type = "label", caption = state .. (joined and ", on your team" or ""),
      tooltip = state == "war" and "Its turrets and yours fire on sight." or state == "allied"
        and "Turrets never fire on each other; you may build in each other's bases." or
        "A truce: nobody shoots, nothing is shared."}
    stance.style.font_color = STANCE_COLOR[state]
    local ok, r = pcall(Relations.get, npc, key, game.tick)
    local okk, s = pcall(Relations.stance, npc.id, key, game.tick)
    grid.add{type = "label", caption = okk and TeachGui.feeling(s) or "?",
      tooltip = ok and ("trust " .. math.floor(r.trust) .. ", grudge " .. math.floor(r.grudge)) or nil}
    local parts = {}
    for _, other in ipairs(all) do
      if other ~= npc then
        local with = Diplomacy.state(npc.force, other.force)
        if with == "war" then parts[#parts + 1] = "[color=1,0.35,0.3]at war with " .. other.name .. "[/color]"
        elseif with == "allied" then parts[#parts + 1] = "[color=0.45,1,0.45]allied with " .. other.name .. "[/color]" end
      end
    end
    grid.add{type = "label", caption = #parts > 0 and table.concat(parts, ", ") or "at peace with all"}
  end
end

local function refresh(frame)
  local list = frame[LIST]
  if not list then return end
  list.clear()
  local recent = Teach.recent(6)
  if #recent == 0 then
    list.add{type="label", caption="Nothing taught yet."}
  end
  for _, entry in ipairs(recent) do
    local state = entry.learned and ("learned as " .. tostring(entry.purpose or "?")) or "waiting to be studied"
    list.add{type="label", caption="[img=item/blueprint] " .. entry.label .. "  (" .. state .. ", from " .. entry.player .. ")"}
  end
end

function TeachGui.open(player)
  local screen = player.gui.screen
  if screen[FRAME] then screen[FRAME].destroy() end
  local frame = screen.add{type="frame", name=FRAME, direction="vertical"}
  local title = frame.add{type="flow", direction="horizontal"}
  title.add{type="label", caption="Engineers", style="frame_title"}.drag_target = frame
  local filler = title.add{type="empty-widget", style="draggable_space_header"}
  filler.style.horizontally_stretchable = true
  filler.style.height = 24
  filler.drag_target = frame
  title.add{type="sprite-button", name=CLOSE, sprite="utility/close", style="close_button",
    tooltip="Close"}
  local relations = frame.add{type="frame", style="inside_shallow_frame_with_padding", direction="vertical"}
  relations.add{type="label", caption="Relations", style="caption_label"}
  fill_relations(relations.add{type="flow", name=RELATIONS, direction="vertical"}, player)
  local ask = frame.add{type="frame", style="inside_shallow_frame_with_padding", direction="vertical"}
  local q = storage.agent_questions and storage.agent_questions[player.index]
  local npc = q and storage.npcs and storage.npcs[q.npc_id]
  local question = ask.add{type="label", name=QUESTION,
    caption=npc and ("[font=default-bold]" .. npc.name .. " asks:[/font] " .. q.question)
      or "No engineer is waiting for an answer. You can still mark a spot for them."}
  question.style.single_line = false
  question.style.maximal_width = 360
  ask.add{type="button", name=MARK, caption="Mark location",
    tooltip="Then click a spot, in the world or on the map. It answers an engineer's \"where?\", "
      .. "or leaves a mark engineers can use."}
  local body = frame.add{type="frame", style="inside_shallow_frame_with_padding", direction="vertical"}
  local row = body.add{type="flow", direction="horizontal"}
  row.style.vertical_align = "center"
  row.add{type="sprite-button", name=SLOT, sprite="item/blueprint", style="slot_button",
    tooltip="Click here while holding a blueprint or a blueprint book. Every engineer learns it."}
  local hint = row.add{type="label", caption="Hold a blueprint or book and click the slot.\nEvery engineer will learn it."}
  hint.style.single_line = false
  hint.style.left_margin = 8
  body.add{type="button", name=SHOW, caption="Show an area of my base",
    tooltip="Then drag over part of your base. Every engineer copies it into the layouts it knows."}
  body.add{type="line"}
  body.add{type="flow", name=LIST, direction="vertical"}
  refresh(body)
  frame.force_auto_center()
  player.set_shortcut_toggled(SHORTCUT, true)
end

function TeachGui.close(player)
  local frame = player.gui.screen[FRAME]
  if frame then frame.destroy() end
  player.set_shortcut_toggled(SHORTCUT, false)
end

function TeachGui.on_shortcut(event)
  if event.prototype_name ~= SHORTCUT then return end
  local player = game.get_player(event.player_index)
  if not player then return end
  if player.gui.screen[FRAME] then TeachGui.close(player) else TeachGui.open(player) end
end

function TeachGui.on_click(event)
  local element = event.element
  if not (element and element.valid) then return false end
  local player = game.get_player(event.player_index)
  if element.name == CLOSE then TeachGui.close(player); return true end
  if element.name == MARK then
    player.clear_cursor()
    if player.cursor_stack then player.cursor_stack.set_stack{name="npc-mark-location", count=1} end
    player.create_local_flying_text{text="Click a spot (works on the map too)", create_at_cursor=true}
    TeachGui.close(player)
    return true
  end
  if element.name == SHOW then
    player.clear_cursor()
    if player.cursor_stack then player.cursor_stack.set_stack{name="npc-show-area", count=1} end
    player.create_local_flying_text{text="Drag over part of your base", create_at_cursor=true}
    TeachGui.close(player)
    return true
  end
  if element.name ~= SLOT then return false end
  player.create_local_flying_text{text=Teach.from_cursor(player, nil), create_at_cursor=true}
  local frame = player.gui.screen[FRAME]
  local body = frame and frame.children[4]
  if body then refresh(body) end
  return true
end

-- An open window keeps up as relations change.
function TeachGui.tick(tick)
  if tick % 300 ~= 41 then return end
  for _, player in pairs(game.connected_players) do
    local frame = player.gui.screen[FRAME]
    local box = frame and frame.children[2] and frame.children[2][RELATIONS]
    if box then fill_relations(box, player) end
  end
end

return TeachGui
