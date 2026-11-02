-- A shortcut-bar button (bottom right, beside the quickbar) that opens the Engineers window:
-- relations, an engineer's question (Mark location), Show an area, and teaching blueprints.
data:extend({
  {
    type = "shortcut",
    name = "npc-teach-blueprint",
    action = "lua",
    order = "z[npc-factions]-a[teach]",
    icon = "__npc-factions__/graphics/teach-shortcut-x32.png",
    icon_size = 32,
    small_icon = "__npc-factions__/graphics/teach-shortcut-x24.png",
    small_icon_size = 24,
    toggleable = true,
  },
  -- Mark location: the engineers' window hands it out; click a spot (on the map too) to answer an
  -- engineer that asked where, or to leave a mark they can use.
  {
    type = "selection-tool",
    name = "npc-mark-location",
    icon = "__npc-factions__/graphics/teach-shortcut-x64.png",
    icon_size = 64,
    flags = {"only-in-cursor", "not-stackable", "spawnable"},
    subgroup = "tool",
    order = "z[npc-mark-location]",
    stack_size = 1,
    select = {border_color = {r = 0.2, g = 0.8, b = 1}, cursor_box_type = "entity", mode = {"nothing"}},
    alt_select = {border_color = {r = 0.2, g = 0.8, b = 1}, cursor_box_type = "entity", mode = {"nothing"}},
  },
  -- Show engineers: drag over part of your base; every engineer copies it into its layouts.
  {
    type = "selection-tool",
    name = "npc-show-area",
    icon = "__npc-factions__/graphics/teach-shortcut-x64.png",
    icon_size = 64,
    flags = {"only-in-cursor", "not-stackable", "spawnable"},
    subgroup = "tool",
    order = "z[npc-show-area]",
    stack_size = 1,
    select = {border_color = {r = 0.3, g = 1, b = 0.4}, cursor_box_type = "copy", mode = {"buildable-type", "same-force"}},
    alt_select = {border_color = {r = 0.3, g = 1, b = 0.4}, cursor_box_type = "copy", mode = {"buildable-type", "same-force"}},
  },
})
